#!/usr/bin/env python3
"""Reject cache evidence that could hide local reuse or changed outputs."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_cache as proof
from deb_test_support import write_deb


def record(hit=False, runner="linux-sandbox"):
    return {"mnemonic": "SonicKernelBuild", "exitCode": 0, "status": "", "cacheable": True,
            "remoteCacheable": True, "cacheHit": hit, "runner": runner,
            "inputs": [{"path": "config", "digest": {"hash": "same"}}]}


def outputs_fixture(root, architecture="amd64"):
    root.mkdir(parents=True, exist_ok=True)
    names = sorted(proof.package_kinds(architecture))
    records = []
    for name in names:
        path = root / name
        write_deb(path)
        records.append(proof.package_record(path, architecture))
    (root / proof.MANIFEST).write_text(json.dumps({
        "schema": 3, "architecture": architecture, "kernel_abi": proof.kernel_abi(architecture), "packages": records,
    }))
    (root / "output-paths.txt").write_text("\n".join(str(root / name) for name in names + [proof.MANIFEST]))
    (root / "invocation.json").write_text(json.dumps({
        "schema": 2, "disk_cache": None, "architecture": architecture,
        "execution_strategy": "linux-sandbox", "uid": 1001, "work_dir": str(root),
        "host_machine": {"amd64": "x86_64", "arm64": "aarch64"}[architecture],
        "remote_cache": "http://127.0.0.1:8080", "remote_cache_read_only": root.name == "hit",
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
        for architecture in ("amd64", "arm64"):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cold, hit = root / "cold", root / "hit"
                for path, action in ((cold, record()), (hit, record(True, "remote cache hit"))):
                    outputs_fixture(path, architecture)
                    (path / "execution.json").write_text(json.dumps(action))
                receipt = proof.verify(cold, hit, root / "evidence", architecture)
                self.assertEqual(receipt["architecture"], architecture)
                self.assertEqual(set(receipt["outputs_sha256"]), set(proof.package_kinds(architecture)) | {proof.MANIFEST})
                self.assertTrue(receipt["kernel_compilation_skipped"])
                with self.assertRaisesRegex(ValueError, "already exists"):
                    proof.verify(cold, hit, root / "evidence", architecture)

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
                             ("host_machine", "aarch64")):
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
        receipt = {"result": "passed", "architecture": "amd64", "cold_compiled": True, "consumer_kernel_cache_hit": True,
                   "kernel_compilation_skipped": True, "outputs_sha256": {sentinel: "digest"},
                   "cold_metrics": {"environment": sentinel}, "hit_metrics": {"diagnostic": sentinel}}
        output = io.StringIO()
        with patch.object(proof, "verify", return_value=receipt), \
                patch.object(sys, "argv", ["verify", "--cold", "/tmp/cold", "--hit", "/tmp/hit", "--artifacts", "/tmp/artifacts"]), \
                contextlib.redirect_stdout(output):
            proof.main()
        self.assertNotIn(sentinel, output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), {
            "result": "passed", "architecture": "amd64", "cold_compiled": True, "consumer_kernel_cache_hit": True,
            "kernel_compilation_skipped": True, "verified_output_count": 1,
        })


if __name__ == "__main__":
    unittest.main()
