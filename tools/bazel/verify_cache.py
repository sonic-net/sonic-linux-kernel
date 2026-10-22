#!/usr/bin/env python3
"""Prove that a fresh consumer downloaded the real kernel action's outputs."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import re
import subprocess

from kernel_action import ARCHITECTURES, MANIFEST, kernel_abi, package_kinds, package_record


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def records(path):
    decoder = json.JSONDecoder()
    content = path.read_text()
    offset = 0
    while offset < len(content):
        while offset < len(content) and content[offset].isspace():
            offset += 1
        if offset == len(content):
            return
        entry, offset = decoder.raw_decode(content, offset)
        yield entry


def kernel_record(directory):
    entries = [entry for entry in records(directory / "execution.json")
               if entry.get("mnemonic") == "SonicKernelBuild"]
    require(len(entries) == 1, "expected exactly one kernel action in " + str(directory))
    entry = entries[0]
    require(entry.get("exitCode") == 0 and entry.get("status") in ("", "SUCCESS"),
            "kernel action did not succeed")
    require(entry.get("cacheable") is True and entry.get("remoteCacheable") is True,
            "kernel action does not permit remote caching")
    return entry



def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def outputs(directory, architecture="amd64"):
    found = {}
    for line in (directory / "output-paths.txt").read_text().splitlines():
        relative = PurePosixPath(line).relative_to("/work")
        require(".." not in relative.parts, "output escaped the build directory")
        path = directory
        for part in relative.parts:
            path = path / part
            require(not path.is_symlink(), "kernel output must not be a symlink")
        require(path.is_file() and path.name not in found, "missing or duplicate kernel output")
        path.resolve().relative_to(directory.resolve())
        found[path.name] = path
    require(set(found) == set(package_kinds(architecture)) | {MANIFEST}, "expected five Debian packages and their manifest")
    manifest = json.loads(found[MANIFEST].read_text(), object_pairs_hook=unique_object)
    require(type(manifest.get("schema")) is int and manifest["schema"] == 3,
            "expected Debian package manifest schema 3")
    require(manifest.get("architecture") == architecture and manifest.get("kernel_abi") == kernel_abi(architecture),
            "kernel manifest architecture or ABI differs from cache proof")
    records = manifest.get("packages")
    require(isinstance(records, list) and len(records) == 5, "manifest must describe five Debian packages")
    names = []
    for artifact in records:
        require(isinstance(artifact, dict) and set(artifact) == {"name", "kind", "sha256", "size", "package", "version", "architecture"},
                "unexpected artifact record fields")
        name = artifact["name"]
        require(name in package_kinds(architecture) and artifact["kind"] == package_kinds(architecture)[name],
                "unexpected artifact name or kind")
        names.append(name)
        path = found[name]
        require(type(artifact["size"]) is int and artifact["size"] > 0
                and isinstance(artifact["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]),
                "invalid artifact size or digest")
        require(sha256(path) == artifact["sha256"] and path.stat().st_size == artifact["size"],
                "kernel package bytes differ from manifest")
        try:
            metadata = package_record(path, architecture)
        except (subprocess.CalledProcessError, ValueError) as error:
            raise ValueError("kernel output is not a valid Debian package") from error
        require(metadata == artifact, "Debian control metadata differs from manifest")
    require(names == sorted(package_kinds(architecture)), "duplicate or unordered artifact inventory")
    return found


def verify(cold, hit, destination, architecture="amd64"):
    kernel_abi(architecture)
    require(cold.resolve() != hit.resolve(), "cache proof requires distinct Bazel output directories")
    first, second = kernel_record(cold), kernel_record(hit)
    require(first.get("cacheHit") is False, "producer did not execute kernel compilation")
    require(second.get("cacheHit") is True and second.get("runner") == "remote cache hit",
            "consumer did not get the kernel action from the remote cache")
    for field in ("commandArgs", "environmentVariables", "platform", "inputs", "listedOutputs"):
        require(first.get(field) == second.get(field), "producer and consumer kernel actions differ: " + field)
    for directory in (cold, hit):
        invocation = json.loads((directory / "invocation.json").read_text())
        require(invocation.get("disk_cache") is None, "cache proof must disable the disk cache")
        require(invocation.get("architecture", "amd64") == architecture,
                "cache proof invocation architecture differs from " + architecture.upper())
    first_outputs, second_outputs = outputs(cold, architecture), outputs(hit, architecture)
    hashes = {name: sha256(path) for name, path in first_outputs.items()}
    require(hashes == {name: sha256(path) for name, path in second_outputs.items()},
            "cached Debian packages differ from freshly compiled packages")
    require(not destination.exists() and not destination.is_symlink(), "artifact destination already exists")
    destination.mkdir(parents=True)
    for name, path in second_outputs.items():
        shutil.copyfile(path, destination / name)
    receipt = {"schema": 1, "result": "passed", "architecture": architecture, "cold_compiled": True,
               "consumer_kernel_runner": second["runner"], "consumer_kernel_cache_hit": True,
               "kernel_compilation_skipped": True, "outputs_sha256": hashes,
               "cold_metrics": first.get("metrics"), "hit_metrics": second.get("metrics")}
    (destination / "cache-proof.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cold", required=True, type=Path)
    parser.add_argument("--hit", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--architecture", choices=ARCHITECTURES, default="amd64")
    args = parser.parse_args()
    receipt = verify(args.cold, args.hit, args.artifacts, args.architecture)
    # The full receipt stays in the private destination. CLI output contains no
    # arbitrary execution metrics, environment values, or output paths.
    print(json.dumps({
        "result": receipt["result"], "architecture": receipt["architecture"], "cold_compiled": receipt["cold_compiled"],
        "consumer_kernel_cache_hit": receipt["consumer_kernel_cache_hit"],
        "kernel_compilation_skipped": receipt["kernel_compilation_skipped"],
        "verified_output_count": len(receipt["outputs_sha256"]),
    }, indent=2))


if __name__ == "__main__":
    main()
