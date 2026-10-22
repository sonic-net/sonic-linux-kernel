#!/usr/bin/env python3
"""Add matched debug sources and build-ID links to Debian's debug package.

Debian build_image already separates signed stripped modules from their original
unsigned debug ELFs. Preserve those bytes: changing a signed module to add a GNU
debuglink would invalidate its signature. GDB can instead resolve detached
symbols through the matching GNU build IDs and conventional debug paths.
"""

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from kernel_arch import (ARCHITECTURES, build_directory, elf_machine,
                         kernel_abi, kernel_architecture, materialize_kernel_elf)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


def link(target, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(target)


def elf_info(path, architecture="amd64"):
    result = subprocess.run(["/usr/bin/readelf", "-h", "-n", "-SW", str(path)],
                            capture_output=True, text=True, check=True).stdout
    match = re.search(r"Build ID: ([0-9a-f]+)", result)
    require(match is not None, "ELF lacks a GNU build ID: " + str(path))
    require("ELF64" in result and elf_machine(architecture) in result,
            "ELF is not an " + architecture.upper() + " kernel artifact: " + str(path))
    return match[1], ".debug_info" in result


def build_id_link(debug_root, debug_file, build_id):
    destination = debug_root / "usr/lib/debug/.build-id" / build_id[:2] / (build_id[2:] + ".debug")
    if destination.is_symlink():
        require(destination.resolve().read_bytes() == debug_file.read_bytes(),
                "different debug files share a build ID")
    else:
        link(os.path.relpath(debug_file, destination.parent), destination)


def validate_debug_pairs(source, runtime, debug, architecture="amd64"):
    abi = kernel_abi(architecture)
    kernel = debug / ("usr/lib/debug/boot/vmlinux-" + abi)
    kernel_id, has_debug = elf_info(kernel, architecture)
    require(has_debug, "kernel vmlinux is missing DWARF debug information")
    with tempfile.TemporaryDirectory(prefix="kernel-debug-check-") as temporary:
        temporary = Path(temporary)
        embedded = temporary / "vmlinux"
        materialize_kernel_elf(architecture, source, runtime / ("boot/vmlinuz-" + abi), kernel, embedded)
        require(elf_info(embedded, architecture)[0] == kernel_id, "boot image and debug vmlinux build IDs differ")
        build_id_link(debug, kernel, kernel_id)
        modules = runtime / "lib/modules" / abi
        if not modules.exists():
            modules = runtime / "usr/lib/modules" / abi
        debug_modules = debug / "usr/lib/debug/lib/modules" / abi
        runtime_modules = sorted(list(modules.rglob("*.ko")) + list(modules.rglob("*.ko.xz")))
        require(runtime_modules, "runtime contains no kernel modules")
        pairs = []
        for deployed in runtime_modules:
            relative = deployed.relative_to(modules)
            if deployed.suffix == ".xz":
                module = temporary / "module.ko"
                with lzma.open(deployed, "rb") as src, module.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                relative = relative.with_suffix("")
            else:
                module = deployed
            detached = debug_modules / relative
            require(detached.is_file(), "runtime module has no matching debug module: " + str(relative))
            runtime_id, runtime_debug = elf_info(module, architecture)
            debug_id, has_debug = elf_info(detached, architecture)
            require(runtime_id == debug_id and has_debug and not runtime_debug,
                    "runtime/debug module build ID or DWARF mismatch: " + str(relative))
            require(b"~Module signature appended~" in module.read_bytes(),
                    "runtime kernel module lost its Debian-generated signature: " + str(relative))
            require(b"~Module signature appended~" not in detached.read_bytes(),
                    "debug module unexpectedly contains a signature")
            build_id_link(debug, detached, debug_id)
            pairs.append({"module": str(relative), "build_id": debug_id})
        require(len(list(debug_modules.rglob("*.ko"))) == len(pairs),
                "runtime and debug module inventories differ")
    return {"kernel_build_id": kernel_id, "module_count": len(pairs),
            "module_build_ids_sha256": hashlib.sha256(json.dumps(pairs, sort_keys=True).encode()).hexdigest()}


def stage(source, debug, artifacts, architecture="amd64"):
    """Run inside Debian binary_image-dbg before its maintained dh_* sequence.

    Debian owns the control metadata, dependencies, hooks, runtime, headers and
    kbuild packaging. This hook only adds matching source and build-ID links to
    the already staged debug package; it never repacks or modifies signed ELFs.
    """
    abi = kernel_abi(architecture)
    build = source / "debian/build" / build_directory(architecture)
    patched = source / "debian/build/source_sonic"
    require((debug / ("usr/lib/debug/boot/vmlinux-" + abi)).is_file(),
            "Debian has not staged the debug kernel")
    debug_source = debug / "usr/src/debug" / ("linux-" + abi)
    shutil.copytree(patched, debug_source / "debian/build/source_sonic",
                    symlinks=True, ignore=shutil.ignore_patterns(".pc"))
    for name in ("include", "arch/" + kernel_architecture(architecture) + "/include", ".config"):
        copy(build / name, debug_source / "debian/build" / build_directory(architecture) / name)
    receipt = validate_debug_pairs(source, build / "output/image", debug, architecture)
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "debug-validation.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print("SONIC_KERNEL_DEBUG_VERIFIED " + json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("debug", type=Path)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--architecture", choices=ARCHITECTURES, default="amd64")
    args = parser.parse_args()
    stage(args.source, args.debug, args.artifacts, args.architecture)
