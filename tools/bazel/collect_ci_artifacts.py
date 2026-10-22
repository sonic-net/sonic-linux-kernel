#!/usr/bin/env python3
"""Publish verified kernel DEBs and a small summary; keep raw evidence private."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel_action import ARCHITECTURES, MANIFEST, kernel_abi, package_names, sha256

DIGEST = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def no_symlinks(path):
    require(not any(parent.is_symlink() for parent in (path, *path.parents)),
            "symlink in artifact path")


def file_at(root, relative):
    path = root / relative
    no_symlinks(path)
    require(path.is_file() and path.resolve().is_relative_to(root.resolve()),
            "missing regular evidence file")
    return path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def receipt(private, relative, architecture):
    path = file_at(private, relative)
    require(path.stat().st_size <= 1024 * 1024, "receipt exceeds size limit")
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    require(isinstance(value, dict) and type(value.get("schema")) is int and value["schema"] == 1
            and value.get("result") == "passed" and value.get("architecture") == architecture,
            "verification did not pass for the requested architecture")
    return value


def timing_seconds(metrics):
    if not isinstance(metrics, dict):
        return {}
    return {name: float(value[:-1]) for name in (
        "totalTime", "executionWallTime", "fetchTime", "setupTime",
    ) if isinstance(value := metrics.get(name), str)
        and re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,9})?s", value)}


def success_summary(private, source, architecture):
    proof = receipt(private, "verified/cache-proof.json", architecture)
    require(all(proof.get(key) is True for key in (
        "cold_compiled", "consumer_kernel_cache_hit", "kernel_compilation_skipped",
    )) and proof.get("consumer_kernel_runner") == "remote cache hit", "missing remote cache proof")
    hashes = proof.get("outputs_sha256")
    require(isinstance(hashes, dict) and set(hashes) == package_names(architecture) | {MANIFEST}
            and all(isinstance(value, str) and DIGEST.fullmatch(value) for value in hashes.values()),
            "unexpected verified output digests")
    installation = receipt(private, "installation.json", architecture)
    require(installation.get("initramfs") is True and installation.get("external_module") is True,
            "package installation checks did not pass")
    expected_packages = []
    for name in sorted(package_names(architecture)):
        package, version, binary_architecture = name[:-4].rsplit("_", 2)
        expected_packages.append({"package": package, "version": version,
                                  "architecture": binary_architecture, "status": "install ok installed"})
    require(installation.get("installed_packages") == sorted(expected_packages, key=lambda item: item["package"]),
            "installed packages differ from verified outputs")
    debugger = receipt(private, "debugger.json", architecture)
    for kind in ("kernel", "module"):
        record = debugger.get(kind)
        require(isinstance(record, dict) and type(record.get("line")) is int and record["line"] > 0
                and record.get("type_lookup") is (kind == "kernel"), "debugger checks did not pass")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True, timeout=60).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "invalid source revision")
    # Copy no nested receipt/manifest fields except fixed names and validated hashes.
    return {
        "checkout_revision": revision, "outputs_sha256": hashes,
        "cold_compiled": True, "consumer_kernel_cache_hit": True, "kernel_compilation_skipped": True,
        "installation_verified": True, "debugger_verified": True,
        "cold_timing_seconds": timing_seconds(proof.get("cold_metrics")),
        "hit_timing_seconds": timing_seconds(proof.get("hit_metrics")),
    }


def collect(private, source, output, job_status, architecture="amd64"):
    require(architecture in ARCHITECTURES, "unsupported architecture")
    require(job_status in ("success", "failure", "cancelled"), "invalid job status")
    private, output = Path(private), Path(output)
    no_symlinks(output)
    require(not output.exists() and output.parent.is_dir(), "artifact destination must be new")
    require(not output.resolve().is_relative_to(private.resolve())
            and not private.resolve().is_relative_to(output.resolve()), "invalid artifact destination")
    temporary = Path(tempfile.mkdtemp(prefix=".kernel-artifacts-", dir=output.parent))
    try:
        summary = {"schema": 2, "job_status": job_status, "architecture": architecture,
                   "kernel_abi": kernel_abi(architecture), "result": "incomplete", "artifacts_published": False}
        if job_status == "success":
            summary.update(success_summary(private, source, architecture))
            for name, expected_hash in sorted(summary["outputs_sha256"].items()):
                path = file_at(private, "verified/" + name)
                require(sha256(path) == expected_hash, "verified output digest differs")
                # The full manifest may contain extra metadata. Hash it for evidence,
                # but publish only the five already verified Debian packages.
                if name != MANIFEST:
                    destination = temporary / name
                    shutil.copyfile(path, destination)
                    destination.chmod(0o600)
                    require(sha256(destination) == expected_hash, "copied output digest differs")
            summary.update(result="passed", artifacts_published=True)
        report = temporary / "validation.json"
        report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        report.chmod(0o600)
        no_symlinks(output)
        require(not output.exists(), "artifact destination appeared during collection")
        temporary.rename(output)
        return {"result": summary["result"], "published_file_count": len(list(output.iterdir()))}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--job-status", required=True, choices=("success", "failure", "cancelled"))
    parser.add_argument("--architecture", choices=ARCHITECTURES, default="amd64")
    args = parser.parse_args()
    try:
        result = collect(args.private_root, args.source_root, args.output, args.job_status, args.architecture)
    except Exception:
        print("Kernel artifact collection failed; private evidence was not published.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
