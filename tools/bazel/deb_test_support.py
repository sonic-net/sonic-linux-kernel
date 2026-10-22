"""Small real Debian packages for control/cache contract regression tests."""
import os
from pathlib import Path
import subprocess
import tempfile


def write_deb(path, package=None, architecture=None, version="6.12.41-1"):
    name, _version, binary_arch = path.name[:-4].split("_")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "DEBIAN").mkdir()
        (root / "DEBIAN/control").write_text(
            f"Package: {package or name}\nVersion: {version}\nArchitecture: {architecture or binary_arch}\n"
            "Maintainer: Kernel contract tests <test@example.invalid>\nDescription: Kernel package contract fixture\n")
        payload = root / "usr/share/doc" / name / "copyright"
        payload.parent.mkdir(parents=True)
        payload.write_text("Public domain test fixture\n")
        subprocess.run(["/usr/bin/dpkg-deb", "--root-owner-group", "-Znone", "--build", str(root), str(path)],
                       check=True, stdout=subprocess.DEVNULL,
                       env={**os.environ, "SOURCE_DATE_EPOCH": "1754969284"})
