#!/usr/bin/env python3
"""Build maintained Debian kernel packages offline in a verified shared worker."""

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import stat
import tempfile

from kernel_arch import ARCHITECTURES, kernel_abi, validate_architecture

SOURCE_DATE_EPOCH = 1754969284  # Debian linux 6.12.41-1 changelog timestamp.
MANIFEST = "kernel-packages.json"
PACKAGE_VERSION = "6.12.41-1"
EXTRA_SOURCES = ("tools/bazel/kernel_arch.py", "tools/bazel/stage_kernel.py")


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


def in_chroot(root, command, **kwargs):
    def enter():
        os.chroot(root)
        os.chdir("/build/source")
    return subprocess.run(command, preexec_fn=enter, **kwargs)


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


def compilation_commands(architecture="amd64"):
    """Use the same source/configuration and Debian binary recipe as Makefile."""
    validate_architecture(architecture)
    kernel = "/build/source/linux-6.12.41"
    return [
        ["/usr/bin/dpkg-source", "-x", "/build/archives/linux_6.12.41-1.dsc", kernel],
        ["/bin/cp", "-a", "/build/source/config.local", "/build/source/patches-sonic", kernel + "/debian/"],
        ["/usr/bin/patch", "-d", kernel, "-p1", "-i", "/build/source/patches-debian/disable-secureboot-config-checks.patch"],
        ["/usr/bin/patch", "-d", kernel, "-p1", "-i", "/build/source/patches-debian/bazel-debug-sources.patch"],
        ["/bin/bash", "-ec", "cd " + kernel +
         f" && if test -f debian/config.local/{architecture}/config.sonic-vs; then "
         f"cp debian/config.local/{architecture}/config.sonic-vs debian/config.local/{architecture}/config.sonic-platform-specific; fi"
         f" && /build/source/manage-config {architecture} no_sign '' && debian/bin/gencontrol.py"],
        ["/bin/bash", "-ec", "cd " + kernel + " && /usr/bin/dpkg-buildpackage -b -us -uc -Pnodoc -j4"],
    ]


def package_record(path, architecture="amd64", root=None):
    command = ["/usr/bin/dpkg-deb", "--show", "--showformat=${Package}\t${Version}\t${Architecture}",
               "/" + str(path.relative_to(root)) if root is not None else str(path)]
    result = (in_chroot(root, command, check=True, text=True, capture_output=True) if root is not None
              else subprocess.run(command, check=True, text=True, capture_output=True))
    package, version, binary_arch = result.stdout.split("\t")
    require(version == PACKAGE_VERSION and binary_arch in (architecture, "all"),
            "unexpected Debian kernel package version or architecture")
    require(path.name == f"{package}_{version}_{binary_arch}.deb", "Debian filename differs from control metadata")
    return {"name": path.name, "kind": package_kinds(architecture).get(path.name, "other"),
            "package": package, "version": version, "architecture": binary_arch,
            "sha256": sha256(path), "size": path.stat().st_size}


def created_package_records(directory, architecture="amd64", root=None):
    records = [package_record(path, architecture, root) for path in sorted(directory.glob("*.deb"))]
    names = {item["name"] for item in records}
    require(package_names(architecture) <= names, "Debian build omitted required kernel packages")
    return [{**item, "exported": item["name"] in package_names(architecture)} for item in records]


def build(config):
    require(config.get("schema") == 3, "unsupported kernel action schema")
    validate_outputs(config)
    architecture = config.get("architecture", "amd64")
    require(os.geteuid() == 0, "kernel action requires root in its disposable worker for chroot")
    runtime = Path(config["build_tools"])
    marker = runtime_identity(runtime, architecture)
    # The tool tree is a declared input. Copy it before building so no action
    # mutates the shared runtime. Chroot makes Kbuild's /build path independent
    # of the Bazel output base, checkout path, and host tool installation.
    with tempfile.TemporaryDirectory(prefix="kernel-action-", dir=Path.cwd()) as temporary:
        root = Path(temporary) / "root"
        shutil.copytree(runtime, root, symlinks=True)
        source = root / "build/source"
        source.mkdir(parents=True)
        archives = root / "build/archives"
        archives.mkdir()
        dest = root / "build/artifacts"
        dest.mkdir()
        (root / "build/home").mkdir()
        (root / "build/tmp").mkdir()
        (root / "dev").mkdir(exist_ok=True)
        for name, minor in (("null", 3), ("zero", 5), ("random", 8), ("urandom", 9)):
            os.mknod(root / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
        inventory = source_inventory(config["sources"], source)
        archive_records = []
        for archive in config["archives"]:
            require(Path(archive["name"]).name == archive["name"], "unsafe archive filename")
            require(sha256(archive["path"]) == archive["sha256"], "kernel archive checksum mismatch")
            shutil.copyfile(archive["path"], archives / archive["name"])
            archive_records.append({"name": archive["name"], "sha256": archive["sha256"]})
        environment = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/build/home", "TMPDIR": "/build/tmp",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "SOURCE_DATE_EPOCH": str(SOURCE_DATE_EPOCH), "PYTHONHASHSEED": "0",
            "KBUILD_BUILD_USER": "sonic", "KBUILD_BUILD_HOST": "kernel-builder",
            "KBUILD_BUILD_TIMESTAMP": "@" + str(SOURCE_DATE_EPOCH),
            "KBUILD_BUILD_VERSION": "1", "DEB_BUILD_OPTIONS": "parallel=4",
            "DEB_BUILD_PROFILES": "nodoc",
            "INCLUDE_EXTERNAL_PATCHES": "n",
            "SONIC_KERNEL_DEBUG_STAGE": "/build/source/tools/bazel/stage_kernel.py",
        }
        print("SONIC_KERNEL_COMPILATION_STARTED", flush=True)
        for command in compilation_commands(architecture):
            in_chroot(root, command, env=environment, check=True)
        # Record every package before removing the disposable build tree. Only
        # the five declared image/debug/headers/kbuild DEBs become Bazel outputs.
        created = created_package_records(source, architecture, root)
        packages = []
        for name in config["outputs"]:
            output = Path(name)
            package = source / output.name
            packages.append(package_record(package, architecture, root))
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
