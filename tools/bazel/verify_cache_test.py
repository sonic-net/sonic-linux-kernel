#!/usr/bin/env python3
"""Reject cache evidence that could hide local reuse or changed outputs."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_cache as proof
import build
from deb_test_support import write_deb


def record(hit=False, runner="linux-sandbox"):
    return {"mnemonic": "SonicKernelBuild", "exitCode": 0, "status": "", "cacheable": True,
            "remoteCacheable": True, "cacheHit": hit, "runner": runner,
            "inputs": [{"path": "config", "digest": {"hash": "same"}}]}


def outputs_fixture(root, architecture="amd64", host_architecture=None):
    host_architecture = host_architecture or architecture
    root.mkdir(parents=True, exist_ok=True)
    names = sorted(proof.package_kinds(architecture))
    records = []
    for name in names:
        path = root / name
        write_deb(path)
        records.append(proof.package_record(path, architecture))
    inputs = ["a" * 64]
    identity = hashlib.sha256(json.dumps({"architecture": host_architecture, "inputs": inputs}, sort_keys=True).encode()).hexdigest()
    marker = {"schema_version": 1, "kind": "debian-build-tools", "architecture": host_architecture,
              "cross_architectures": ["arm64", "armhf"] if host_architecture == "amd64" else [],
              "execution": "relocatable", "input_sha256": inputs,
              "identity_sha256": identity, "packages": {"make": "4.4.1"}}
    (root / proof.MANIFEST).write_text(json.dumps({
        "schema": 4, "kernel_config": build.kernel_configuration(architecture, {}, host_architecture), "architecture": architecture, "host_architecture": host_architecture, "kernel_abi": proof.kernel_abi(architecture), "packages": records, "build_tools": marker,
    }))
    (root / "output-paths.txt").write_text("\n".join(str(root / name) for name in names + [proof.MANIFEST]))
    (root / "invocation.json").write_text(json.dumps({
        "schema": 3, "kernel_config": build.kernel_configuration(architecture, {}, host_architecture), "disk_cache": None, "architecture": architecture, "host_architecture": host_architecture,
        "execution_strategy": "linux-sandbox", "uid": 1001, "work_dir": str(root),
        "host_machine": {"amd64": "x86_64", "arm64": "aarch64"}[host_architecture],
        "remote_cache": "http://127.0.0.1:8080", "remote_cache_read_only": root.name == "hit",
        "registry_overrides": [],
    }))


class CacheProofTest(unittest.TestCase):
    def test_reads_concatenated_bazel_json_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution.json"
            path.write_text(json.dumps({"mnemonic": "Other"}) + "\n" + json.dumps(record()))
            self.assertEqual(proof.kernel_record(path.parent), record())

    def test_rejects_ambiguous_or_failed_kernel_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution.json"
            for entries in ([], [record(), record()], [dict(record(), exitCode=1)]):
                path.write_text("\n".join(json.dumps(entry) for entry in entries))
                with self.assertRaises(ValueError):
                    proof.kernel_record(path.parent)

    def test_rejects_unsandboxed_kernel_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for runner in ("local", "processwrapper-sandbox"):
                with self.subTest(runner=runner), patch.object(proof, "kernel_record", side_effect=[
                        record(False, runner), record(True, "remote cache hit")]):
                    with self.assertRaisesRegex(ValueError, "Linux sandbox"):
                        proof.verify(root / "cold", root / "hit", root / "evidence")

    def test_rejects_local_hits_and_changed_action_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cold, hit = root / "cold", root / "hit"
            for second in (record(True, "disk cache hit"), record(False),
                           dict(record(True, "remote cache hit"), inputs=[{"path": "changed"}])):
                with patch.object(proof, "kernel_record", side_effect=[record(), second]):
                    with self.assertRaises(ValueError):
                        proof.verify(cold, hit, root / "evidence")

    def test_rejects_changed_configurations_or_manifest_disagreement(self):
        for filename in ("invocation.json", proof.MANIFEST):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cold, hit = root / "cold", root / "hit"
                for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                    outputs_fixture(path)
                    (path / "execution.json").write_text(json.dumps(action))
                changed = hit / filename
                data = json.loads(changed.read_text())
                data["kernel_config"]["SECURE_UPGRADE_MODE"] = "unsupported"
                changed.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "kernel configuration|manifest configuration differs"):
                    proof.verify(cold, hit, root / "proof")

    def test_rejects_matching_but_invalid_cross_configuration(self):
        # Matching producer/consumer JSON cannot turn a contradictory native
        # build or the wrong ARMHF ABI into valid cross-build evidence.
        for key, value in (("CROSS_BUILD_ENVIRON", "n"),
                           ("KVERSION", "6.12.41+deb13-sonic-armhf"),
                           ("CONFIGURED_ARCH", "amd64")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cold, hit = root / "cold", root / "hit"
                for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                    outputs_fixture(path, "armhf", "amd64")
                    (path / "execution.json").write_text(json.dumps(action))
                    for name in ("invocation.json", proof.MANIFEST):
                        file = path / name
                        data = json.loads(file.read_text())
                        data["kernel_config"][key] = value
                        file.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "kernel configuration"):
                    proof.verify(cold, hit, root / "proof", "armhf", "amd64")

    def test_rejects_missing_configuration_in_cross_evidence(self):
        for name in ("invocation.json", proof.MANIFEST):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cold, hit = root / "cold", root / "hit"
                for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                    outputs_fixture(path, "arm64", "amd64")
                    (path / "execution.json").write_text(json.dumps(action))
                file = hit / name
                data = json.loads(file.read_text())
                del data["kernel_config"]
                file.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "kernel configuration"):
                    proof.verify(cold, hit, root / "proof", "arm64", "amd64")

    def test_rejects_reusing_one_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "distinct"):
                proof.verify(root, root, root / "evidence")

    def test_rejects_output_paths_outside_owned_work_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output in ("/outside/kernel-runtime.tar", "/work/../escape.tar"):
                (root / "output-paths.txt").write_text(output + "\n")
                with self.assertRaises(ValueError):
                    proof.outputs(root)


    def test_accepts_identical_debian_outputs_from_remote_cache(self):
        for host, architecture in (("amd64", "amd64"), ("arm64", "arm64"), ("amd64", "arm64"), ("amd64", "armhf")):
            with self.subTest(host=host, architecture=architecture), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cold, hit = root / "cold", root / "hit"
                for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                    outputs_fixture(path, architecture, host)
                    (path / "execution.json").write_text(json.dumps(action))
                receipt = proof.verify(cold, hit, root / "evidence", architecture, host)
                self.assertEqual(receipt["architecture"], architecture)
                self.assertEqual(receipt["host_architecture"], host)
                self.assertEqual(receipt["kernel_config"], build.kernel_configuration(architecture, {}, host))
                self.assertEqual(set(receipt["outputs_sha256"]), set(proof.package_kinds(architecture)) | {proof.MANIFEST})
                self.assertTrue(receipt["kernel_compilation_skipped"])
                with self.assertRaisesRegex(ValueError, "already exists"):
                    proof.verify(cold, hit, root / "evidence", architecture, host)

    def test_cross_proof_rejects_wrong_host_or_target_tools(self):
        for field, value in (("architecture", "arm64"), ("cross_architectures", ["arm64"]),
                             ("identity_sha256", "0" * 64)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                outputs_fixture(root, "armhf", "amd64")
                path = root / proof.MANIFEST
                manifest = json.loads(path.read_text())
                manifest["build_tools"][field] = value
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    proof.outputs(root, "armhf", "amd64")

    def test_rejects_native_outputs_labeled_as_cross_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs_fixture(root, "arm64", "arm64")
            with self.assertRaisesRegex(ValueError, "manifest host architecture"):
                proof.outputs(root, "arm64", "amd64")

    def test_rejects_wrong_manifest_architecture_or_abi(self):
        for field in ("architecture", "kernel_abi"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                outputs_fixture(root, "arm64")
                path = root / proof.MANIFEST
                manifest = json.loads(path.read_text())
                manifest[field] = "amd64" if field == "architecture" else proof.kernel_abi("amd64")
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "architecture or ABI"):
                    proof.outputs(root, "arm64")

    def test_rejects_mixed_invocation_architectures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cold, hit = root / "cold", root / "hit"
            for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                outputs_fixture(path, "arm64")
                (path / "execution.json").write_text(json.dumps(action))
            (hit / "invocation.json").write_text(json.dumps({"disk_cache": None, "architecture": "amd64"}))
            with self.assertRaisesRegex(ValueError, "invocation architecture"):
                proof.verify(cold, hit, root / "evidence", "arm64")

    def test_rejects_root_execution_or_writable_cache_consumer(self):
        for field, value in (("uid", 0), ("uid", True), ("execution_strategy", "local"),
                             ("remote_cache_read_only", False), ("work_dir", "/different"),
                             ("host_machine", "aarch64"), ("host_architecture", "arm64"),
                             ("registry_overrides", ["--registry=https://different.invalid/main"])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cold, hit = root / "cold", root / "hit"
                for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                    outputs_fixture(path)
                    (path / "execution.json").write_text(json.dumps(action))
                path = hit / "invocation.json"
                invocation = json.loads(path.read_text())
                invocation[field] = value
                path.write_text(json.dumps(invocation))
                with self.assertRaises(ValueError):
                    proof.verify(cold, hit, root / "evidence")

    def test_rejects_changed_debian_manifest_and_symlink_outputs(self):
        for change in ("bytes", "duplicate", "kind", "symlink", "schema"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                outputs_fixture(root)
                path = root / proof.MANIFEST
                manifest = json.loads(path.read_text())
                if change == "bytes":
                    (root / next(name for name, kind in proof.package_kinds().items() if kind == "runtime")).write_bytes(b"changed")
                elif change == "duplicate":
                    manifest["packages"][1] = manifest["packages"][0]
                elif change == "kind":
                    manifest["packages"][0]["kind"] = "invalid"
                elif change == "schema":
                    manifest["schema"] = 1
                else:
                    data = root / next(name for name, kind in proof.package_kinds().items() if kind == "runtime")
                    data.rename(root / "real-output")
                    data.symlink_to("real-output")
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    proof.outputs(root)

    def test_rejects_non_debian_payload_even_when_hashes_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs_fixture(root)
            path = root / next(name for name, kind in proof.package_kinds().items() if kind == "runtime")
            path.write_bytes(b"!<arch>\nnot-a-tar")
            manifest_path = root / proof.MANIFEST
            manifest = json.loads(manifest_path.read_text())
            record = next(item for item in manifest["packages"] if item["name"] == path.name)
            record.update(size=path.stat().st_size, sha256=proof.sha256(path))
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "not a valid Debian"):
                proof.outputs(root)

    def test_cli_omits_arbitrary_execution_fields(self):
        sentinel = "SENSITIVE_FIXTURE_DO_NOT_PUBLISH"
        receipt = {"result": "passed", "architecture": "amd64", "host_architecture": "amd64", "cold_compiled": True, "consumer_kernel_cache_hit": True,
                   "kernel_compilation_skipped": True, "outputs_sha256": {sentinel: "digest"},
                   "cold_metrics": {"environment": sentinel}, "hit_metrics": {"diagnostic": sentinel}}
        output = io.StringIO()
        with patch.object(proof, "verify", return_value=receipt), \
                patch.object(sys, "argv", ["verify", "--cold", "/tmp/cold", "--hit", "/tmp/hit", "--artifacts", "/tmp/artifacts"]), \
                contextlib.redirect_stdout(output):
            proof.main()
        self.assertNotIn(sentinel, output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), {
            "result": "passed", "architecture": "amd64", "host_architecture": "amd64", "cold_compiled": True, "consumer_kernel_cache_hit": True,
            "kernel_compilation_skipped": True, "verified_output_count": 1,
        })


if __name__ == "__main__":
    unittest.main()
