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

from kernel_arch import (ARCHITECTURES, HOST_ARCHITECTURES, kernel_abi,
                         validate_architecture, validate_build_pair)

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
        "linux-image-" + abi + ("" if architecture == "armhf" else "-unsigned") + suffix: "runtime",
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


def runtime_identity(runtime, architecture=None, target_architecture=None):
    marker = json.loads((runtime / "kernel-runtime.json").read_text())
    return validate_runtime_identity(marker, architecture, target_architecture)


def validate_runtime_identity(marker, architecture=None, target_architecture=None):
    require(isinstance(marker, dict), "missing declared build-tools provenance")
    require(marker.get("schema_version") == 1 and marker.get("kind") == "debian-build-tools",
            "unsupported declared build-tools runtime")
    if architecture is None:
        architecture = marker.get("architecture")
    require(architecture in HOST_ARCHITECTURES, "unsupported kernel execution architecture")
    require(marker.get("architecture") == architecture, "kernel tools must execute on " + architecture.upper())
    cross_architectures = marker.get("cross_architectures", [])
    require(isinstance(cross_architectures, list)
            and all(isinstance(target, str) for target in cross_architectures)
            and cross_architectures == sorted(set(cross_architectures)),
            "invalid build-tools cross architectures")
    for target in cross_architectures:
        validate_build_pair(architecture, target)
        require(target != architecture, "cross architectures must exclude the execution architecture")
    if target_architecture is not None:
        validate_build_pair(architecture, target_architecture)
        require(target_architecture == architecture or target_architecture in cross_architectures,
                "kernel tools do not support the target architecture")
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


def validate_configuration(kernel_config, architecture, host_architecture):
    """Check the normalized producer contract before invoking build tools."""
    validate_build_pair(host_architecture, architecture)
    expected = {
        "CONFIGURED_ARCH": architecture, "CONFIGURED_PLATFORM": "vs", "BLDENV": "trixie",
        "CROSS_BUILD_ENVIRON": "y" if host_architecture != architecture else "n",
        "MULTIARCH_QEMU_ENVIRON": "n", "KERNEL_VERSION": "6.12.41", "KERNEL_ABISUFFIX": "+deb13",
        "KERNEL_SUBVERSION": "1", "KERNEL_FEATURESET": "sonic", "KVERSION": kernel_abi(architecture),
        "ADDITIONAL_BUILD_PROFILES": "", "SECURE_UPGRADE_MODE": "no_sign", "INCLUDE_EXTERNAL_PATCHES": "n",
    }
    inactive = {"SECURE_UPGRADE_KERNEL_CAFILE", "EXTERNAL_KERNEL_PATCH_URL", "EXTERNAL_KERNEL_PATCH_LOC"}
    require(isinstance(kernel_config, dict) and set(kernel_config) == set(expected) | inactive
            and all(isinstance(value, str) for value in kernel_config.values()),
            "kernel configuration must contain all normalized Make variables")
    for name, value in expected.items():
        require(kernel_config[name] == value, "unsupported kernel configuration: " + name)


def build_profiles(kernel_config, cross):
    profiles = ["cross", "nocheck", "nodoc"] if cross else ["nodoc"]
    return profiles + list(filter(None, kernel_config["ADDITIONAL_BUILD_PROFILES"].split(",")))


def compilation_commands(architecture, source, archives, kernel_config, host_architecture=None):
    """Use the same source/configuration and Debian binary recipe as Makefile."""
    host_architecture = host_architecture or architecture
    validate_build_pair(host_architecture, architecture)
    validate_configuration(kernel_config, architecture, host_architecture)
    cross = architecture != host_architecture
    source, archives = Path(source), Path(archives)
    kernel = source / ("linux-" + kernel_config["KERNEL_VERSION"])
    return [
        ["dpkg-source", "-x", str(archives / ("linux_" + kernel_config["KERNEL_VERSION"] + "-" + kernel_config["KERNEL_SUBVERSION"] + ".dsc")), str(kernel)],
        ["cp", "-a", str(source / "config.local"), str(source / "patches-sonic"), str(kernel / "debian")],
        ["patch", "--batch", "--fuzz=0", "-d", str(kernel), "-p1", "-i", str(source / "patches-debian/disable-secureboot-config-checks.patch")],
        ["patch", "--batch", "--fuzz=0", "-d", str(kernel), "-p1", "-i", str(source / "patches-debian/bazel-debug-sources.patch")],
        ["patch", "--batch", "--fuzz=0", "-d", str(kernel), "-p1", "-i", str(source / "patches-debian/bazel-relocatable-tools.patch")],
        ["bash", str(source / "manage-config"), architecture, kernel_config["SECURE_UPGRADE_MODE"], kernel_config["SECURE_UPGRADE_KERNEL_CAFILE"]],
        ["make", "-f", "debian/rules", "-f", "-", "bazel-prepare-control"],
        ["dpkg-buildpackage", "-b", "-us", "-uc"]
        + (["-a" + architecture] if cross else [])
        + ["-P" + ",".join(build_profiles(kernel_config, cross)), "-j4", "--rules-file=make -f debian/rules"],
    ]


def debian_architecture(runtime, architecture, host_architecture, environment):
    """Resolve the complete Debian build/host tuple before generating controls."""
    validate_build_pair(host_architecture, architecture)
    result = subprocess.run([str(runtime / "bin/dpkg-architecture"), "-a" + architecture, "-l"],
                            env=environment, text=True, capture_output=True, check=True)
    values = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"DEB_(BUILD|HOST|TARGET)_[A-Z_]+", name)
                and value and not any(c.isspace() for c in value),
                "invalid declared dpkg architecture output")
        require(name not in values, "duplicate declared dpkg architecture output")
        values[name] = value
    require(values.get("DEB_BUILD_ARCH") == host_architecture
            and values.get("DEB_HOST_ARCH") == architecture
            and values.get("DEB_HOST_GNU_TYPE"), "declared Debian build/host architecture differs")
    return values


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
    require(config.get("schema") == 5, "unsupported kernel action schema")
    validate_outputs(config)
    architecture = config.get("architecture", "amd64")
    require(os.geteuid() != 0, "kernel compilation must run as an unprivileged user")
    kernel_config = config["kernel_config"]
    runtime = Path(config["build_tools"]).resolve(strict=True)
    marker = runtime_identity(runtime, target_architecture=architecture)
    host_architecture = config["host_architecture"]
    require(host_architecture == marker["architecture"], "declared kernel execution host differs from runtime")
    validate_configuration(kernel_config, architecture, host_architecture)
    cross = host_architecture != architecture
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
            "DEB_BUILD_PROFILES": " ".join(build_profiles(kernel_config, cross)),
            **kernel_config,
            "SONIC_KERNEL_DEBUG_STAGE": str(source / "tools/bazel/stage_kernel.py"),
            "SONIC_KERNEL_ARTIFACTS": str(dest),
            "SONIC_KERNEL_TOOLS": str(runtime / "bin"),
            "SONIC_KERNEL_ARCHITECTURE": architecture,
            "SONIC_KERNEL_PATCHELF": str(Path(config["patchelf"]).absolute()),
        }
        environment.update(debian_architecture(runtime, architecture, host_architecture, environment))
        if cross:
            prefix = environment["DEB_HOST_GNU_TYPE"] + "-"
            for tool in ("gcc", "ld", "objcopy", "readelf", "strip", "pkg-config"):
                require((runtime / "bin" / (prefix + tool)).is_file(),
                        "declared runtime lacks cross tool: " + prefix + tool)
            environment["SONIC_KERNEL_CROSS_COMPILE"] = prefix
        print("SONIC_KERNEL_COMPILATION_STARTED", flush=True)
        kernel = source / ("linux-" + kernel_config["KERNEL_VERSION"])
        for index, command in enumerate(compilation_commands(architecture, source, archives, kernel_config, host_architecture)):
            subprocess.run(command, cwd=source if index < 5 else kernel, env=environment,
                           input=CONTROL_PREPARATION if index == 6 else None,
                           text=True, check=True)
            if index == 1:
                configuration = kernel / "debian/config.local" / architecture
                platform_config = configuration / ("config.sonic-" + kernel_config["CONFIGURED_PLATFORM"])
                if platform_config.is_file():
                    shutil.copyfile(platform_config, configuration / "config.sonic-platform-specific")
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
            "schema": 4, "architecture": architecture, "host_architecture": host_architecture,
            "platform": kernel_config["CONFIGURED_PLATFORM"], "kernel_config": kernel_config,
            "kernel_version": kernel_config["KERNEL_VERSION"], "kernel_abi": kernel_config["KVERSION"],
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
