#!/usr/bin/env python3
"""Build maintained Debian kernel packages with declared, unprivileged tools."""

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

from kernel_arch import ARCHITECTURES, kernel_abi, validate_architecture

SOURCE_DATE_EPOCH = 1754969284  # Debian linux 6.12.41-1 changelog timestamp.
MANIFEST = "kernel-packages.json"
PACKAGE_VERSION = "6.12.41-1"
EXTRA_SOURCES = ("tools/bazel/kernel_arch.py", "tools/bazel/stage_kernel.py")

# Debian's control-real target deliberately fails after regeneration, to reject
# stale metadata during packaging. Prepare both outputs before that validation
# using its maintained prerequisite list, without changing the validation rule.
CONTROL_PREPARATION = """.PHONY: bazel-prepare-control
bazel-prepare-control: debian/bin/gencontrol.py $(CONTROL_FILES)
\tPYTHONHASHSEED=0 python3 $<
\tmd5sum $(sort $^) > debian/control.md5sum
"""


def package_kinds(architecture="amd64"):
    abi = kernel_abi(architecture)
    suffix = "_" + PACKAGE_VERSION + "_" + architecture + ".deb"
    return {
        "linux-image-" + abi + "-unsigned" + suffix: "runtime",
        "linux-image-" + abi + "-dbg" + suffix: "debug",
        "linux-headers-" + abi + suffix: "headers",
        "linux-headers-6.12.41+deb13-common-sonic_" + PACKAGE_VERSION + "_all.deb": "headers_common",
        "linux-kbuild-6.12.41+deb13" + suffix: "kbuild",
    }


def package_names(architecture="amd64"):
    return set(package_kinds(architecture))


PACKAGE_NAMES = package_names()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_identity(runtime, architecture="amd64"):
    validate_architecture(architecture)
    marker = json.loads((runtime / "kernel-runtime.json").read_text())
    require(marker.get("schema_version") == 1 and marker.get("kind") == "debian-build-tools",
            "unsupported declared build-tools runtime")
    require(marker.get("architecture") == architecture, "kernel tools must execute on " + architecture.upper())
    require(marker.get("execution") == "relocatable", "kernel tools must support unprivileged direct execution")
    inputs = marker.get("input_sha256")
    require(isinstance(inputs, list) and inputs
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in inputs)
            and inputs == sorted(inputs)
            and isinstance(marker.get("packages"), dict) and marker["packages"]
            and all(isinstance(name, str) and isinstance(version, str) and name and version
                    for name, version in marker["packages"].items()),
            "missing build-tools provenance")
    expected = hashlib.sha256(json.dumps({"architecture": architecture, "inputs": inputs},
                                         sort_keys=True).encode()).hexdigest()
    require(marker.get("identity_sha256") == expected, "build-tools identity digest differs from its inputs")
    return marker


def packaging_database(runtime, destination):
    """Let dpkg resolve declared libraries without modifying the shared input.

    dpkg-shlibdeps looks up the resolved library filename in dpkg's file lists.
    The libraries live beneath the declared runtime, so its private database
    needs those same paths instead of paths on the build host.
    """
    runtime = runtime.resolve(strict=True)
    shutil.copytree(runtime / "var/lib/dpkg", destination, copy_function=shutil.copyfile)
    for listing in sorted((destination / "info").glob("*.list")):
        paths = listing.read_text().splitlines()
        listing.write_text("\n".join(
            str(runtime / path.lstrip("/")) if path.startswith("/") else path
            for path in paths
        ) + "\n")


def source_inventory(sources, destination=None):
    inventory = []
    seen = set()
    for source in sorted(sources, key=lambda entry: entry["name"]):
        name = source["name"]
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and ".." not in relative.parts and name not in seen,
                "unsafe or duplicate kernel input path: " + name)
        seen.add(name)
        path = Path(source["path"])
        require(path.is_file(), "missing kernel input: " + str(path))
        # Bazel materializes source inputs as symlinks in the sandbox. Copy their
        # contents and executable bit, never a link to the original checkout.
        mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
        inventory.append({"name": name, "mode": mode, "sha256": sha256(path)})
        if destination is not None:
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
            output.chmod(mode)
            os.utime(output, (SOURCE_DATE_EPOCH, SOURCE_DATE_EPOCH))
    return inventory


def source_tree_sha256(inventory):
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def checkout_source_identity(checkout):
    files = [checkout / name for name in ("Makefile", "manage-config", *EXTRA_SOURCES)]
    for directory in ("config.local", "patches-debian", "patches-sonic"):
        files.extend(path for path in (checkout / directory).rglob("*") if path.is_file())
    return source_tree_sha256(source_inventory([
        {"name": str(path.relative_to(checkout)), "path": str(path)} for path in files
    ]))


def validate_outputs(config):
    names = package_names(config.get("architecture", "amd64"))
    outputs = config.get("outputs")
    require(isinstance(outputs, list) and len(outputs) == len(names)
            and all(isinstance(name, str) and name for name in outputs)
            and {Path(name).name for name in outputs} == names,
            "kernel action must declare exactly five Debian kernel packages")
    require(isinstance(config.get("manifest"), str)
            and Path(config["manifest"]).name == MANIFEST,
            "kernel action must declare " + MANIFEST)


def compilation_commands(architecture, source, archives):
    """Use the same source/configuration and Debian binary recipe as Makefile."""
    validate_architecture(architecture)
    source, archives = Path(source), Path(archives)
    kernel = source / "linux-6.12.41"
    return [
        ["dpkg-source", "-x", str(archives / "linux_6.12.41-1.dsc"), str(kernel)],
        ["cp", "-a", str(source / "config.local"), str(source / "patches-sonic"), str(kernel / "debian")],
        ["patch", "--batch", "--fuzz=0", "-d", str(kernel), "-p1", "-i", str(source / "patches-debian/disable-secureboot-config-checks.patch")],
        ["patch", "--batch", "--fuzz=0", "-d", str(kernel), "-p1", "-i", str(source / "patches-debian/bazel-debug-sources.patch")],
        ["patch", "--batch", "--fuzz=0", "-d", str(kernel), "-p1", "-i", str(source / "patches-debian/bazel-relocatable-tools.patch")],
        ["bash", str(source / "manage-config"), architecture, "no_sign", ""],
        ["make", "-f", "debian/rules", "-f", "-", "bazel-prepare-control"],
        ["dpkg-buildpackage", "-b", "-us", "-uc", "-Pnodoc", "-j4", "--rules-file=make -f debian/rules"],
    ]


def package_record(path, architecture="amd64", environment=None):
    command = ["dpkg-deb", "--show", "--showformat=${Package}\t${Version}\t${Architecture}", str(path)]
    result = subprocess.run(command, check=True, text=True, capture_output=True, env=environment)
    package, version, binary_arch = result.stdout.split("\t")
    require(version == PACKAGE_VERSION and binary_arch in (architecture, "all"),
            "unexpected Debian kernel package version or architecture")
    require(path.name == f"{package}_{version}_{binary_arch}.deb", "Debian filename differs from control metadata")
    return {"name": path.name, "kind": package_kinds(architecture).get(path.name, "other"),
            "package": package, "version": version, "architecture": binary_arch,
            "sha256": sha256(path), "size": path.stat().st_size}


def created_package_records(directory, architecture="amd64", environment=None):
    records = [package_record(path, architecture, environment) for path in sorted(directory.glob("*.deb"))]
    names = {item["name"] for item in records}
    require(package_names(architecture) <= names, "Debian build omitted required kernel packages")
    return [{**item, "exported": item["name"] in package_names(architecture)} for item in records]


def build(config):
    require(config.get("schema") == 4, "unsupported kernel action schema")
    validate_outputs(config)
    architecture = config.get("architecture", "amd64")
    require(os.geteuid() != 0, "kernel compilation must run as an unprivileged user")
    runtime = Path(config["build_tools"]).resolve(strict=True)
    marker = runtime_identity(runtime, architecture)
    # Bazel's Linux sandbox isolates the action. The declared runtime stays
    # read-only; only source, packaging metadata and build outputs are copied.
    with tempfile.TemporaryDirectory(prefix="kernel-action-", dir=Path.cwd()) as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir(parents=True)
        archives = root / "archives"
        archives.mkdir()
        dest = root / "artifacts"
        dest.mkdir()
        (root / "home").mkdir()
        (root / "tmp").mkdir()
        packaging_database(runtime, root / "dpkg")
        inventory = source_inventory(config["sources"], source)
        archive_records = []
        for archive in config["archives"]:
            require(Path(archive["name"]).name == archive["name"], "unsafe archive filename")
            require(sha256(archive["path"]) == archive["sha256"], "kernel archive checksum mismatch")
            shutil.copyfile(archive["path"], archives / archive["name"])
            archive_records.append({"name": archive["name"], "sha256": archive["sha256"]})
        environment = {
            "PATH": str(runtime / "bin"),
            "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
            "SHELL": str(runtime / "bin/bash"), "CONFIG_SHELL": str(runtime / "bin/bash"),
            "DPKG_ADMINDIR": str(root / "dpkg"),
            "DPKG_DATADIR": str(runtime / "usr/share/dpkg"),
            "DPKG_ORIGINS_DIR": str(runtime / "etc/dpkg/origins"),
            "DEB_RULES_REQUIRES_ROOT": "no",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "SOURCE_DATE_EPOCH": str(SOURCE_DATE_EPOCH), "PYTHONHASHSEED": "0",
            "KBUILD_BUILD_USER": "sonic", "KBUILD_BUILD_HOST": "kernel-builder",
            "KBUILD_BUILD_TIMESTAMP": "@" + str(SOURCE_DATE_EPOCH),
            "KBUILD_BUILD_VERSION": "1", "DEB_BUILD_OPTIONS": "parallel=4",
            "DEB_BUILD_PROFILES": "nodoc",
            "INCLUDE_EXTERNAL_PATCHES": "n",
            "SONIC_KERNEL_DEBUG_STAGE": str(source / "tools/bazel/stage_kernel.py"),
            "SONIC_KERNEL_ARTIFACTS": str(dest),
            "SONIC_KERNEL_TOOLS": str(runtime / "bin"),
            "SONIC_KERNEL_ARCHITECTURE": architecture,
            "SONIC_KERNEL_PATCHELF": str(Path(config["patchelf"]).absolute()),
        }
        print("SONIC_KERNEL_COMPILATION_STARTED", flush=True)
        kernel = source / "linux-6.12.41"
        for index, command in enumerate(compilation_commands(architecture, source, archives)):
            subprocess.run(command, cwd=source if index < 5 else kernel, env=environment,
                           input=CONTROL_PREPARATION if index == 6 else None,
                           text=True, check=True)
            if index == 1:
                configuration = kernel / "debian/config.local" / architecture
                if (configuration / "config.sonic-vs").is_file():
                    shutil.copyfile(configuration / "config.sonic-vs", configuration / "config.sonic-platform-specific")
        # Record every package before removing the disposable build tree. Only
        # the five declared image/debug/headers/kbuild DEBs become Bazel outputs.
        created = created_package_records(source, architecture, environment)
        packages = []
        for name in config["outputs"]:
            output = Path(name)
            package = source / output.name
            packages.append(package_record(package, architecture, environment))
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package, output)
        manifest = {
            "schema": 3, "architecture": architecture, "platform": "vs",
            "kernel_version": "6.12.41", "kernel_abi": kernel_abi(architecture),
            "package_version": "6.12.41-1", "signing": "unsigned",
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "source_archives": sorted(archive_records, key=lambda entry: entry["name"]),
            "source_tree_sha256": source_tree_sha256(inventory),
            "source_files": inventory,
            "build_tools": marker,
            "packages": sorted(packages, key=lambda entry: entry["name"]),
            "created_packages": created,
            "debug": json.loads((dest / "debug-validation.json").read_text()),
        }
        Path(config["manifest"]).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print("SONIC_KERNEL_COMPILATION_COMPLETED", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--source-tree-sha256":
        print(checkout_source_identity(Path(sys.argv[2])))
    else:
        build(json.loads(Path(sys.argv[1]).read_text()))
