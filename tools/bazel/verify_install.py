#!/usr/bin/env python3
"""Validate actual dpkg installation, initramfs hooks and native module headers."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from kernel_action import ARCHITECTURES, PACKAGE_VERSION, package_names, kernel_abi
from kernel_arch import elf_machine


def verify_device_trees(root, architecture):
    if architecture == "arm64":
        directory = root / "usr/lib" / ("linux-image-" + kernel_abi(architecture))
        if not any(path.is_file() for path in directory.rglob("*.dtb")):
            raise ValueError("ARM64 kernel package did not install device trees for its ABI")


def verify(architecture):
    abi = kernel_abi(architecture)
    packages = []
    for name in sorted(package_names(architecture)):
        package, version, binary_arch = name[:-4].split("_")
        result = subprocess.check_output([
            "dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\t${Status}", package], text=True)
        actual_package, actual_version, actual_architecture, status = result.split("\t")
        if (actual_package, actual_version, actual_architecture, status) != (
                package, PACKAGE_VERSION, binary_arch, "install ok installed"):
            raise ValueError("Installed Debian package identity or status differs: " + package)
        packages.append({"package": package, "version": version, "architecture": binary_arch, "status": status})
    packages.sort(key=lambda record: record["package"])
    initramfs = Path("/boot/initrd.img-" + abi)
    if not initramfs.is_file() or initramfs.stat().st_size == 0:
        raise ValueError("Debian postinst did not produce the ABI initramfs")
    verify_device_trees(Path("/"), architecture)
    with tempfile.TemporaryDirectory(prefix="kernel-header-check-") as temporary:
        directory = Path(temporary)
        (directory / "Makefile").write_text("obj-m += sonic_header_check.o\n")
        (directory / "sonic_header_check.c").write_text(
            '#include <linux/module.h>\n#include <linux/init.h>\n'
            'static int __init probe_init(void) { return 0; }\n'
            'static void __exit probe_exit(void) {}\n'
            'module_init(probe_init);\nmodule_exit(probe_exit);\nMODULE_LICENSE("GPL");\n')
        subprocess.run(["make", "-C", "/lib/modules/" + abi + "/build", "M=" + temporary, "modules"], check=True)
        module = directory / "sonic_header_check.ko"
        vermagic = subprocess.check_output(["modinfo", "-F", "vermagic", str(module)], text=True).strip()
        header = subprocess.check_output(["readelf", "-h", str(module)], text=True)
        if vermagic.split()[0] != abi or elf_machine(architecture) not in header:
            raise ValueError("External module architecture or ABI differs from installed kernel")
    return {"schema": 1, "result": "passed", "architecture": architecture,
            "installed_packages": packages, "initramfs": True,
            "external_module": True, "module_vermagic": vermagic}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=ARCHITECTURES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.architecture)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("Installed package, initramfs and native header checks passed.")


if __name__ == "__main__":
    main()
