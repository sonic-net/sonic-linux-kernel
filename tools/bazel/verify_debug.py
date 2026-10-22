#!/usr/bin/env python3
"""Verify installed kernel/module debug lookup with no compiler tree available."""

import argparse
import json
import lzma
from pathlib import Path
import re
import subprocess
import tempfile

from kernel_arch import ARCHITECTURES, kernel_abi, materialize_kernel_elf

ABI = kernel_abi()


def gdb_check(executable, expected_debug, debug_root, source_root, *, kernel):
    # GDB's Python API proves that lines resolve to shipped source files and that
    # symbols came from the packaged build-ID file, not a local build artifact.
    script = '''
import gdb, json, pathlib, re
expected = pathlib.Path(EXPECTED).resolve()
source_root = pathlib.Path(SOURCE_ROOT).resolve()
if not any(pathlib.Path(obj.filename).resolve() == expected for obj in gdb.objfiles()):
    raise RuntimeError("GDB did not load the expected packaged debug ELF")
name = "start_kernel" if KERNEL else None
if name is None:
    listing = gdb.execute("info functions", to_string=True)
    functions = re.findall(r"^\\d+:\\s+.*?([A-Za-z_][A-Za-z_0-9]*)\\s*\\(", listing, re.M)
    if not functions:
        raise RuntimeError("Packaged module has no source-level functions")
    name = functions[0]
locations = gdb.decode_line(name)[1]
if not locations or not locations[0].symtab or locations[0].line < 1:
    raise RuntimeError("GDB did not resolve a source line")
location = locations[0]
source = pathlib.Path(location.symtab.fullname()).resolve()
if not source.is_relative_to(source_root) or not source.is_file():
    raise RuntimeError("GDB source is absent from the debug Debian package")
if len(source.read_text().splitlines()) < location.line:
    raise RuntimeError("GDB source line is outside the shipped file")
gdb.execute("list " + name, to_string=True)
if KERNEL:
    definition = gdb.execute("ptype struct task_struct", to_string=True)
    if "struct task_struct" not in definition or "pid" not in definition:
        raise RuntimeError("Kernel task_struct type information is missing")
print("SONIC_GDB_VERIFIED " + json.dumps({"function": name, "source": str(source.relative_to(source_root)), "line": location.line, "type_lookup": bool(KERNEL)}, sort_keys=True))
'''
    variables = {"EXPECTED": str(expected_debug), "SOURCE_ROOT": str(source_root), "KERNEL": kernel}
    statements = [key + " = " + repr(value) for key, value in variables.items()]
    command = ["gdb", "--batch", "--nx", "-ex", "set pagination off",
               "-ex", "set auto-load safe-path /nonexistent",
               "-ex", "set debuginfod enabled off",
               "-ex", "set debug-file-directory " + str(debug_root / "usr/lib/debug"),
               "-ex", "set substitute-path /build/source/linux-6.12.41 " + json.dumps(str(source_root)),
               "-ex", "directory " + json.dumps(str(source_root / "debian/build/source_sonic")),
               "-ex", "file " + json.dumps(str(executable)),
               "-ex", "python exec(" + repr("\n".join(statements) + script) + ")"]
    result = subprocess.run(command, cwd=source_root, capture_output=True, text=True, check=True)
    matches = re.findall(r"^SONIC_GDB_VERIFIED (.+)$", result.stdout, re.M)
    if len(matches) != 1:
        raise ValueError("GDB could not verify installed symbols/source: " + result.stderr[-2000:])
    return json.loads(matches[0])


def verify(root, architecture="amd64"):
    abi = kernel_abi(architecture)
    root = root.resolve()
    source = root / "usr/src/debug" / ("linux-" + abi)
    debug = root / "usr/lib/debug"
    version = subprocess.check_output(["gdb", "--version"], text=True).splitlines()[0]
    modules = root / "usr/lib/modules" / abi
    candidates = sorted(modules.rglob("dummy.ko*")) or sorted(modules.rglob("veth.ko*")) or sorted(modules.rglob("*.ko.xz"))
    if not candidates:
        raise ValueError("Installed runtime contains no module for debugger validation")
    selected = candidates[0]
    with tempfile.TemporaryDirectory(prefix="kernel-gdb-") as temporary:
        temporary = Path(temporary)
        kernel = temporary / "vmlinux"
        materialize_kernel_elf(architecture, source / "debian/build/source_sonic",
                               root / ("boot/vmlinuz-" + abi), debug / ("boot/vmlinux-" + abi), kernel)
        kernel_result = gdb_check(kernel, debug / ("boot/vmlinux-" + abi), root, source, kernel=True)
        module = temporary / "module.ko"
        module.write_bytes(lzma.decompress(selected.read_bytes()) if selected.suffix == ".xz" else selected.read_bytes())
        relative = selected.relative_to(modules)
        if relative.suffix == ".xz":
            relative = relative.with_suffix("")
        module_result = gdb_check(module, debug / "lib/modules" / abi / relative, root, source, kernel=False)
    return {"schema": 1, "result": "passed", "architecture": architecture, "gdb": version,
            "kernel": kernel_result, "module": dict(module_result, path=str(relative))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="root with extracted matching runtime/debug Debian packages")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--architecture", choices=ARCHITECTURES, default="amd64")
    args = parser.parse_args()
    result = verify(args.root, args.architecture)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
