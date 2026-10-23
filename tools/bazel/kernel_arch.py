"""SONiC kernel target names and supported execution/target pairs."""

import hashlib
import lzma
import os
from pathlib import Path
import struct
import subprocess

ARCHITECTURES = ("amd64", "arm64", "armhf")
HOST_ARCHITECTURES = ("amd64", "arm64")
ABINAME = "6.12.41+deb13"


def validate_architecture(architecture):
    if architecture not in ARCHITECTURES:
        raise ValueError("unsupported kernel architecture: " + str(architecture))
    return architecture


def validate_build_pair(host_architecture, architecture):
    validate_architecture(architecture)
    if (host_architecture, architecture) not in (
            ("amd64", "amd64"), ("arm64", "arm64"),
            ("amd64", "arm64"), ("amd64", "armhf")):
        raise ValueError("unsupported kernel build pair: " + str(host_architecture) + " -> " + str(architecture))
    return architecture


def kernel_flavour(architecture="amd64"):
    architecture = validate_architecture(architecture)
    return "armmp" if architecture == "armhf" else architecture


def kernel_abi(architecture="amd64"):
    return ABINAME + "-sonic-" + kernel_flavour(architecture)


def kernel_architecture(architecture="amd64"):
    return {"amd64": "x86", "arm64": "arm64", "armhf": "arm"}[validate_architecture(architecture)]


def build_directory(architecture="amd64"):
    architecture = validate_architecture(architecture)
    return "build_" + architecture + "_sonic_" + kernel_flavour(architecture)


def elf_machine(architecture="amd64"):
    return {"amd64": "Advanced Micro Devices X86-64", "arm64": "AArch64", "armhf": "ARM"}[validate_architecture(architecture)]


def elf_class(architecture="amd64"):
    return "ELF32" if validate_architecture(architecture) == "armhf" else "ELF64"


def binutils_tool(name):
    """Use the declared target tool on a cross build, native tools after install."""
    return os.environ.get("SONIC_KERNEL_CROSS_COMPILE", "") + name


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _verify_armhf_image(image, reconstructed):
    """Match Debian armmp's XZ-compressed zImage payload to its debug ELF.

    Linux 6.12 arch/arm/boot/Makefile compresses the raw objcopy Image rather
    than an ELF. Its zImage wrapper and size trailer surround that XZ stream.
    The pinned Debian armhf configuration selects CONFIG_KERNEL_XZ=y.
    """
    deployed = image.read_bytes()
    if len(deployed) < 48 or struct.unpack_from("<I", deployed, 36)[0] != 0x016F2818:
        raise ValueError("ARMHF boot image lacks the zImage header")
    expected = reconstructed.read_bytes()
    magic = b"\xfd7zXZ\x00"
    offset = deployed.find(magic)
    while offset >= 0:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        try:
            payload = decoder.decompress(deployed[offset:], max_length=len(expected) + 1)
        except lzma.LZMAError:
            payload = None
        if decoder.eof and payload == expected:
            return
        offset = deployed.find(magic, offset + 1)
    raise ValueError("ARMHF boot zImage and debug vmlinux bytes differ")


def materialize_kernel_elf(architecture, source, image, symbols, output):
    """Check deployed boot code and provide its matched ELF for debug lookup.

    AMD64 embeds a compressed ELF. ARM64 has a raw Image and ARMHF compresses
    a raw Image inside zImage. Reproduce those raw bytes using Linux 6.12's
    arch-specific objcopy recipe, then retain the matched ELF's build ID.
    """
    validate_architecture(architecture)
    if architecture == "amd64":
        with output.open("wb") as stream:
            subprocess.run(["sh", str(source / "scripts/extract-vmlinux"), str(image)],
                           stdout=stream, check=True)
        return
    reconstructed = output.with_name(output.name + ".Image")
    objcopy = binutils_tool("objcopy")
    flags = ["-O", "binary", "-R", ".comment", "-S"]
    if architecture == "arm64":
        flags = ["-O", "binary", "-R", ".note", "-R", ".note.gnu.build-id", "-R", ".comment", "-S"]
    subprocess.run([objcopy, *flags, str(symbols), str(reconstructed)], check=True)
    if architecture == "armhf":
        _verify_armhf_image(image, reconstructed)
    elif _sha256(reconstructed) != _sha256(image):
        raise ValueError("ARM64 boot Image and debug vmlinux bytes differ")
    subprocess.run([objcopy, "--strip-debug", str(symbols), str(output)], check=True)
