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
import struct
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


def elf_interpreter(path):
    """Read PT_INTERP without loading a build output or touching signed modules."""
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:6] != b"\x7fELF\x02\x01":
            return None
        if struct.unpack_from("<H", header, 16)[0] not in (2, 3):
            return None
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        for index in range(count):
            stream.seek(offset + index * size)
            program = stream.read(size)
            require(len(program) >= 56, "truncated ELF program header: " + str(path))
            if struct.unpack_from("<I", program)[0] == 3:
                stream.seek(struct.unpack_from("<Q", program, 8)[0])
                length = struct.unpack_from("<Q", program, 32)[0]
                return stream.read(length).rstrip(b"\0").decode()
    return None


def normalize_tools(package, architecture, runtime, patchelf):
    """Remove the private build runtime before Debian strips/packages tools.

    Kbuild's HOSTCC executables need the declared loader while building. The
    installed kbuild tools need the ordinary Debian loader and dependencies.
    Signed kernel modules have no PT_INTERP and are never modified here.
    """
    loader = {"amd64": "/lib64/ld-linux-x86-64.so.2", "arm64": "/lib/ld-linux-aarch64.so.1"}[architecture]
    prefixes = tuple(str(path) + "/" for path in {runtime.absolute(), runtime.resolve()})
    normalized = 0
    for path in sorted(package.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        interpreter = elf_interpreter(path)
        if interpreter is None or not interpreter.startswith(prefixes):
            continue
        old_rpath = subprocess.check_output([str(patchelf), "--print-rpath", str(path)], text=True).strip()
        kept = [entry for entry in old_rpath.split(":") if entry and not entry.startswith(prefixes)]
        rpath_args = ["--set-rpath", ":".join(kept)] if kept else ["--remove-rpath"]
        subprocess.run([str(patchelf), "--set-interpreter", loader, *rpath_args, str(path)], check=True)
        require(elf_interpreter(path) == loader, "installed tool retained its build-time interpreter")
        normalized += 1
    print("SONIC_KERNEL_TOOLS_NORMALIZED " + str(normalized), flush=True)
    return normalized


def elf_info(path, architecture="amd64"):
    result = subprocess.run(["readelf", "-h", "-n", "-SW", str(path)],
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
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("debug", type=Path, nargs="?")
    parser.add_argument("artifacts", type=Path, nargs="?")
    parser.add_argument("--normalize-tools", type=Path)
    parser.add_argument("--architecture", choices=ARCHITECTURES, default="amd64")
    args = parser.parse_args()
    if args.normalize_tools is not None:
        normalize_tools(args.normalize_tools, args.architecture,
                        Path(os.environ["SONIC_KERNEL_TOOLS"]).parent,
                        Path(os.environ["SONIC_KERNEL_PATCHELF"]))
    elif all(path is not None for path in (args.source, args.debug, args.artifacts)):
        stage(args.source, args.debug, args.artifacts, args.architecture)
    else:
        parser.error("source, debug and artifacts are required for debug staging")
