#!/usr/bin/env python3
"""Check artifact publication without repeating kernel package validation."""

import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_ci_artifacts as artifacts

SECRET = "SENSITIVE_FIXTURE_DO_NOT_PUBLISH"
REVISION = "1" * 40


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def fixture(root, architecture="amd64", host_architecture=None):
    host_architecture = host_architecture or architecture
    private = root / "private"
    verified = private / "verified"
    verified.mkdir(parents=True)
    packages = []
    # The cache verifier owns DEB validation. Here payload bytes are opaque.
    for name in sorted(artifacts.package_names(architecture)):
        (verified / name).write_bytes(("verified package " + name).encode())
        package, version, binary_architecture = name[:-4].rsplit("_", 2)
        packages.append({"package": package, "version": version,
                         "architecture": binary_architecture, "status": "install ok installed"})
    write_json(verified / artifacts.MANIFEST, {"private_metadata": SECRET})
    hashes = {path.name: artifacts.sha256(path) for path in verified.iterdir()}
    common = {"schema": 1, "result": "passed", "architecture": architecture, "private_metadata": SECRET}
    write_json(verified / "cache-proof.json", {
        **common, "host_architecture": host_architecture, "cold_compiled": True, "consumer_kernel_cache_hit": True,
        "kernel_compilation_skipped": True, "consumer_kernel_runner": "remote cache hit",
        "producer_kernel_runner": "linux-sandbox", "unprivileged_build": True,
        "outputs_sha256": hashes, "cold_metrics": {"totalTime": "31.25s", "private": SECRET},
        "hit_metrics": {"fetchTime": "1.5s", "setupTime": SECRET},
    })
    write_json(private / "installation.json", {**common, "initramfs": True, "external_module": True,
               "installed_packages": sorted(packages, key=lambda item: item["package"]), "module_vermagic": SECRET})
    write_json(private / "debugger.json", {**common, "gdb": SECRET,
               "kernel": {"line": 1, "type_lookup": True, "source": SECRET},
               "module": {"line": 2, "type_lookup": False, "source": SECRET}})
    for name in ("cold/execution.json", "cold/invocation.json", "cold/module-graph.json",
                 "cold-consumer/MODULE.bazel.lock", "cache.log", "verified/unexpected.deb"):
        path = private / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SECRET)
    return private, hashes


class CollectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "public"
        git = patch.object(artifacts.subprocess, "check_output", return_value=REVISION + "\n")
        self.git = git.start()
        self.addCleanup(git.stop)

    def collect(self, private, architecture="amd64", status="success", host_architecture=None):
        return artifacts.collect(private, self.root, self.output, status, architecture, host_architecture=host_architecture)

    def test_only_five_unchanged_packages_and_safe_summary_are_published(self):
        for host, architecture in (("amd64", "amd64"), ("arm64", "arm64"), ("amd64", "arm64"), ("amd64", "armhf")):
            with self.subTest(host=host, architecture=architecture):
                private, hashes = fixture(self.root / (host + "-" + architecture), architecture, host)
                self.assertEqual(self.collect(private, architecture, host_architecture=host), {"result": "passed", "published_file_count": 6})
                self.assertEqual({p.name for p in self.output.iterdir()}, artifacts.package_names(architecture) | {"validation.json"})
                summary = json.loads((self.output / "validation.json").read_text())
                self.assertEqual(summary["schema"], 2)
                self.assertEqual(summary["architecture"], architecture)
                self.assertEqual(summary["host_architecture"], host)
                self.assertEqual(summary["checkout_revision"], REVISION)
                self.assertEqual(summary["outputs_sha256"], hashes)
                self.assertEqual(summary["cold_timing_seconds"], {"totalTime": 31.25})
                self.assertEqual(summary["hit_timing_seconds"], {"fetchTime": 1.5})
                for path in self.output.iterdir():
                    self.assertNotIn(SECRET.encode(), path.read_bytes())
                    if path.suffix == ".deb":
                        self.assertEqual(artifacts.sha256(path), hashes[path.name])
                shutil.rmtree(self.output)

    def test_source_job_publishes_packages_without_claiming_installation(self):
        private, hashes = fixture(self.root)
        (private / "installation.json").unlink()
        (private / "debugger.json").unlink()
        result = artifacts.collect(private, self.root, self.output, "success", phase="source")
        self.assertEqual(result, {"result": "passed", "published_file_count": 6})
        summary = json.loads((self.output / "validation.json").read_text())
        self.assertEqual(summary["phase"], "source")
        self.assertEqual(summary["outputs_sha256"], hashes)
        self.assertTrue(summary["linux_sandbox_verified"])
        self.assertTrue(summary["unprivileged_build"])
        self.assertFalse(summary["installation_verified"])
        self.assertFalse(summary["debugger_verified"])

    def test_installation_job_binds_downloaded_packages_to_source_summary(self):
        private, _ = fixture(self.root)
        artifacts.collect(private, self.root, self.output, "success", phase="source")
        shutil.copyfile(self.output / "validation.json", private / "verified/validation.json")
        (private / "verified" / artifacts.MANIFEST).unlink()
        (private / "verified/cache-proof.json").unlink()
        shutil.rmtree(self.output)
        result = artifacts.collect(private, self.root, self.output, "success", phase="installation")
        self.assertEqual(result, {"result": "passed", "published_file_count": 1})
        summary = json.loads((self.output / "validation.json").read_text())
        self.assertEqual(summary["phase"], "installation")
        self.assertTrue(summary["installation_verified"])
        self.assertTrue(summary["debugger_verified"])
        self.assertEqual(summary["source_validation_sha256"], artifacts.sha256(private / "verified/validation.json"))
        self.assertFalse(summary["artifacts_published"])
        shutil.rmtree(self.output)
        (private / "verified" / sorted(artifacts.package_names())[0]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "digest differs"):
            artifacts.collect(private, self.root, self.output, "success", phase="installation")

    def test_installation_rejects_missing_sandbox_proof_or_different_source_revision(self):
        private, _ = fixture(self.root)
        artifacts.collect(private, self.root, self.output, "success", phase="source")
        original = json.loads((self.output / "validation.json").read_text())
        shutil.rmtree(self.output)
        for field, value in (("checkout_revision", "2" * 40), ("linux_sandbox_verified", False),
                             ("unprivileged_build", False), ("phase", "all"), ("host_architecture", "arm64")):
            with self.subTest(field=field):
                write_json(private / "verified/validation.json", {**original, field: value})
                with self.assertRaises(ValueError):
                    artifacts.collect(private, self.root, self.output, "success", phase="installation")
                self.assertFalse(self.output.exists())

    def test_cross_source_summary_cannot_be_relabelled_as_native(self):
        private, _ = fixture(self.root, "arm64", "amd64")
        with self.assertRaisesRegex(ValueError, "source host architecture differs"):
            artifacts.collect(private, self.root, self.output, "success", "arm64", "source")
        self.assertFalse(self.output.exists())
        artifacts.collect(private, self.root, self.output, "success", "arm64", "source", "amd64")
        shutil.copyfile(self.output / "validation.json", private / "verified/validation.json")
        shutil.rmtree(self.output)
        with self.assertRaisesRegex(ValueError, "source package verification"):
            artifacts.collect(private, self.root, self.output, "success", "arm64", "installation", "arm64")
        artifacts.collect(private, self.root, self.output, "success", "arm64", "installation", "amd64")
        self.assertEqual(json.loads((self.output / "validation.json").read_text())["host_architecture"], "amd64")

    def test_missing_or_changed_package_and_manifest_do_not_publish_partial_output(self):
        for name in (sorted(artifacts.package_names())[0], artifacts.MANIFEST):
            for change in ("missing", "changed"):
                with self.subTest(name=name, change=change):
                    private, _ = fixture(self.root / (name + change))
                    path = private / "verified" / name
                    if change == "missing":
                        path.unlink()
                    else:
                        path.write_text(SECRET)
                    with self.assertRaises(ValueError):
                        self.collect(private)
                    self.assertFalse(self.output.exists())
                    self.assertFalse(list(self.root.glob(".kernel-artifacts-*")))

    def test_wrong_missing_or_failed_receipts_are_rejected(self):
        for index, relative in enumerate(("verified/cache-proof.json", "installation.json", "debugger.json")):
            for change in ("missing", "architecture", "result", "schema"):
                with self.subTest(relative=relative, change=change):
                    private, _ = fixture(self.root / (str(index) + change))
                    path = private / relative
                    value = json.loads(path.read_text())
                    if change == "missing":
                        path.unlink()
                    else:
                        value[change] = {"architecture": "arm64", "result": "failed", "schema": True}[change]
                        write_json(path, value)
                    with self.assertRaises(ValueError):
                        self.collect(private)
                    self.assertFalse(self.output.exists())

    def test_incomplete_cache_installation_and_debugger_proofs_are_rejected(self):
        cases = [
            ("verified/cache-proof.json", "consumer_kernel_runner", "local"),
            ("verified/cache-proof.json", "producer_kernel_runner", "local"),
            ("verified/cache-proof.json", "unprivileged_build", False),
            ("verified/cache-proof.json", "kernel_compilation_skipped", False),
            ("verified/cache-proof.json", "outputs_sha256", {}),
            ("installation.json", "initramfs", False),
            ("installation.json", "external_module", False),
            ("installation.json", "installed_packages", []),
            ("debugger.json", "kernel", {"line": 1, "type_lookup": False}),
            ("debugger.json", "module", {"line": 0, "type_lookup": False}),
        ]
        for index, (relative, key, value) in enumerate(cases):
            with self.subTest(relative=relative, key=key):
                private, _ = fixture(self.root / str(index))
                path = private / relative
                receipt = json.loads(path.read_text())
                receipt[key] = value
                write_json(path, receipt)
                with self.assertRaises(ValueError):
                    self.collect(private)

    def test_invalid_digest_map_and_copy_corruption_do_not_publish(self):
        private, hashes = fixture(self.root)
        proof_path = private / "verified/cache-proof.json"
        proof = json.loads(proof_path.read_text())
        for invalid in ({**hashes, "../unexpected.deb": "a" * 64},
                        {**hashes, artifacts.MANIFEST: SECRET}):
            write_json(proof_path, {**proof, "outputs_sha256": invalid})
            with self.assertRaises(ValueError):
                self.collect(private)
        write_json(proof_path, proof)
        with patch.object(artifacts.shutil, "copyfile", side_effect=lambda _source, dest: dest.write_text("corrupt")):
            with self.assertRaises(ValueError):
                self.collect(private)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".kernel-artifacts-*")))

    def test_failed_jobs_publish_only_fixed_incomplete_report_without_reading_evidence(self):
        for status in ("failure", "cancelled"):
            result = self.collect(self.root / SECRET, status=status)
            self.assertEqual(result, {"result": "incomplete", "published_file_count": 1})
            self.assertEqual({p.name for p in self.output.iterdir()}, {"validation.json"})
            self.assertFalse(json.loads((self.output / "validation.json").read_text())["artifacts_published"])
            self.assertNotIn(SECRET, (self.output / "validation.json").read_text())
            self.git.assert_not_called()
            shutil.rmtree(self.output)

    def test_symlink_evidence_and_existing_or_redirected_destinations_are_rejected(self):
        private, _ = fixture(self.root)
        for relative in ("installation.json", "verified", "verified/" + artifacts.MANIFEST,
                         "verified/" + sorted(artifacts.package_names())[0]):
            path = private / relative
            saved = path.with_name(path.name + ".saved")
            path.rename(saved)
            path.symlink_to(saved)
            with self.assertRaises(ValueError):
                self.collect(private)
            path.unlink()
            saved.rename(path)
        self.output.mkdir()
        (self.root / "redirect").symlink_to(self.root)
        for destination in (self.output, private / "public", self.root / "redirect" / "public"):
            with self.assertRaises(ValueError):
                artifacts.collect(private, self.root, destination, "success")

    def test_cli_does_not_print_private_exception_details(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        argv = ["collect", "--private-root", SECRET, "--source-root", ".", "--output", "public", "--job-status", "success"]
        with patch.object(sys, "argv", argv), patch.object(artifacts, "collect", side_effect=ValueError(SECRET)), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            self.assertEqual(artifacts.main(), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Kernel artifact collection failed; private evidence was not published.\n")


if __name__ == "__main__":
    unittest.main()
