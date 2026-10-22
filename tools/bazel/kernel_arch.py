"""Native SONiC kernel architecture names shared by compilation and staging."""

import hashlib
from pathlib import Path
import subprocess

ARCHITECTURES = ("amd64", "arm64")
ABINAME = "6.12.41+deb13"


def validate_architecture(architecture):
    if architecture not in ARCHITECTURES:
        raise ValueError("unsupported kernel architecture: " + str(architecture))
    return architecture


def kernel_abi(architecture="amd64"):
    return ABINAME + "-sonic-" + validate_architecture(architecture)


def kernel_architecture(architecture="amd64"):
    return {"amd64": "x86", "arm64": "arm64"}[validate_architecture(architecture)]


def build_directory(architecture="amd64"):
    architecture = validate_architecture(architecture)
    return "build_" + architecture + "_sonic_" + architecture


def elf_machine(architecture="amd64"):
    return {"amd64": "Advanced Micro Devices X86-64", "arm64": "AArch64"}[validate_architecture(architecture)]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def materialize_kernel_elf(architecture, source, image, symbols, output):
    """Check the deployed boot image and provide its ELF for debug lookup.

    AMD64 embeds a compressed ELF. ARM64 installs a raw Image without its ELF
    header or build-ID note. Match that image byte-for-byte using Linux 6.12's
    arch/arm64/boot/Makefile objcopy recipe, then use a stripped ELF with the
    same loadable code and build ID to exercise external debug-file lookup.
    """
    validate_architecture(architecture)
    if architecture == "amd64":
        with output.open("wb") as stream:
            subprocess.run(["/bin/sh", str(source / "scripts/extract-vmlinux"), str(image)],
                           stdout=stream, check=True)
        return
    reconstructed = output.with_name(output.name + ".Image")
    subprocess.run(["/usr/bin/objcopy", "-O", "binary", "-R", ".note", "-R", ".note.gnu.build-id",
                    "-R", ".comment", "-S", str(symbols), str(reconstructed)], check=True)
    if _sha256(reconstructed) != _sha256(image):
        raise ValueError("ARM64 boot Image and debug vmlinux bytes differ")
    subprocess.run(["/usr/bin/objcopy", "--strip-debug", str(symbols), str(output)], check=True)
