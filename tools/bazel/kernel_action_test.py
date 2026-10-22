#!/usr/bin/env python3
"""Check input identity and Debian package handoff without compiling a sample kernel."""

import hashlib
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kernel_action as action
import verify_install
from deb_test_support import write_deb


class KernelActionTest(unittest.TestCase):
    def test_installed_arm64_package_requires_device_trees_for_its_abi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verify_install.verify_device_trees(root, "amd64")
            wrong = root / "usr/lib/linux-image-wrong-abi/vendor/board.dtb"
            wrong.parent.mkdir(parents=True)
            wrong.write_bytes(b"wrong ABI")
            with self.assertRaisesRegex(ValueError, "device trees for its ABI"):
                verify_install.verify_device_trees(root, "arm64")
            native = root / "usr/lib" / ("linux-image-" + action.kernel_abi("arm64")) / "vendor/board.dtb"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"native device tree")
            verify_install.verify_device_trees(root, "arm64")

    def test_source_identity_ignores_path_but_tracks_content_and_executable_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.write_text("same content")
            second.write_text("same content")
            inventory = lambda path: action.source_inventory([{"name": "manage-config", "path": str(path)}])
            self.assertEqual(action.source_tree_sha256(inventory(first)), action.source_tree_sha256(inventory(second)))
            second.chmod(0o755)
            self.assertNotEqual(action.source_tree_sha256(inventory(first)), action.source_tree_sha256(inventory(second)))
            second.chmod(0o644)
            second.write_text("changed configuration")
            self.assertNotEqual(action.source_tree_sha256(inventory(first)), action.source_tree_sha256(inventory(second)))

    def test_source_paths_cannot_escape_or_collide(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_text("source")
            for name in ("../escape", "/absolute"):
                with self.assertRaisesRegex(ValueError, "unsafe or duplicate"):
                    action.source_inventory([{"name": name, "path": str(path)}])
            with self.assertRaisesRegex(ValueError, "unsafe or duplicate"):
                action.source_inventory([{"name": "same", "path": str(path)}] * 2)

    def test_sandbox_links_are_copied_as_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").write_text("source")
            (root / "link").symlink_to(root / "input")
            action.source_inventory([{"name": "config.local/test", "path": str(root / "link")}], root / "out")
            output = root / "out/config.local/test"
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_text(), "source")
            self.assertEqual(output.stat().st_mtime, action.SOURCE_DATE_EPOCH)

    def test_runtime_requires_matching_architecture_and_input_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for architecture in action.ARCHITECTURES:
                with self.subTest(architecture=architecture):
                    inputs = ["a" * 64, "b" * 64]
                    identity = hashlib.sha256(json.dumps({"architecture": architecture, "inputs": inputs},
                                                         sort_keys=True).encode()).hexdigest()
                    marker = {"schema_version": 1, "kind": "debian-build-tools", "architecture": architecture,
                              "execution": "relocatable",
                              "identity_sha256": identity, "input_sha256": inputs, "packages": {"make": "4.4.1"}}
                    (root / "kernel-runtime.json").write_text(json.dumps(marker))
                    self.assertEqual(action.runtime_identity(root, architecture), marker)
                    other = "arm64" if architecture == "amd64" else "amd64"
                    with self.assertRaisesRegex(ValueError, "must execute on " + other.upper()):
                        action.runtime_identity(root, other)
                    marker["input_sha256"][0] = "c" * 64
                    marker["input_sha256"].sort()
                    (root / "kernel-runtime.json").write_text(json.dumps(marker))
                    with self.assertRaisesRegex(ValueError, "identity digest"):
                        action.runtime_identity(root, architecture)

    def test_action_requires_all_five_packages_for_the_selected_architecture(self):
        for architecture in action.ARCHITECTURES:
            config = {"architecture": architecture, "outputs": sorted(action.package_names(architecture)),
                      "manifest": "out/" + action.MANIFEST}
            action.validate_outputs(config)
            for outputs in (config["outputs"][:-1], config["outputs"] + ["other.deb"],
                            [config["outputs"][0]] * 5, sorted(action.package_names(
                                "arm64" if architecture == "amd64" else "amd64"))):
                with self.subTest(outputs=outputs), self.assertRaisesRegex(ValueError, "exactly five"):
                    action.validate_outputs(dict(config, outputs=outputs))
            with self.assertRaisesRegex(ValueError, "kernel-packages.json"):
                action.validate_outputs(dict(config, manifest="out/other.json"))
        with self.assertRaisesRegex(ValueError, "unsupported kernel architecture"):
            action.validate_outputs(dict(config, architecture="armhf"))

    def test_compile_recipe_keeps_maintained_debian_packaging_for_each_architecture(self):
        for architecture in action.ARCHITECTURES:
            with self.subTest(architecture=architecture):
                commands = action.compilation_commands(architecture, Path("/declared/source"), Path("/declared/archives"))
                self.assertEqual(commands[0][0:2], ["dpkg-source", "-x"])
                self.assertEqual(commands[5], ["bash", "/declared/source/manage-config", architecture, "no_sign", ""])
                self.assertIn("bazel-debug-sources.patch", commands[3][-1])
                self.assertIn("bazel-relocatable-tools.patch", commands[4][-1])
                self.assertEqual(commands[-1][:6], ["dpkg-buildpackage", "-b", "-us", "-uc", "-Pnodoc", "-j4"])
                self.assertNotIn("nokerneldbg", repr(commands))
        with self.assertRaisesRegex(ValueError, "unsupported kernel architecture"):
            action.compilation_commands("armhf", Path("source"), Path("archives"))

    def test_packaging_database_uses_declared_library_paths_without_modifying_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, private = root / "runtime", root / "private-dpkg"
            source = runtime / "var/lib/dpkg/info/libc6:amd64.list"
            source.parent.mkdir(parents=True)
            source.write_text("/.\n/usr/lib/x86_64-linux-gnu/libc.so.6\n")
            source.chmod(0o444)
            before = source.read_bytes()
            alias = root / "sandbox-runtime"
            alias.symlink_to(runtime, target_is_directory=True)
            action.packaging_database(alias, private)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual((private / "info/libc6:amd64.list").read_text().splitlines(),
                             [str(runtime), str(runtime / "usr/lib/x86_64-linux-gnu/libc.so.6")])

    def test_kernel_action_rejects_privileged_execution(self):
        config = {"schema": 4, "outputs": sorted(action.package_names()), "manifest": action.MANIFEST}
        with patch.object(action.os, "geteuid", return_value=0), self.assertRaisesRegex(ValueError, "unprivileged"):
            action.build(config)

    def test_package_inventory_checks_real_debian_control_and_records_unexported_packages(self):
        for architecture in action.ARCHITECTURES:
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in action.package_names(architecture):
                    write_deb(root / name)
                extra = root / f"linux-libc-dev_6.12.41-1_{architecture}.deb"
                write_deb(extra)
                records = action.created_package_records(root, architecture)
                self.assertEqual(len(records), 6)
                self.assertEqual({r["name"] for r in records if r["exported"]}, action.package_names(architecture))
                self.assertEqual(next(r["kind"] for r in records if r["name"] == extra.name), "other")
                write_deb(extra, architecture="all")
                with self.assertRaisesRegex(ValueError, "filename differs from control"):
                    action.created_package_records(root, architecture)
                write_deb(extra, version="6.12.42-1")
                with self.assertRaisesRegex(ValueError, "version or architecture"):
                    action.created_package_records(root, architecture)
                extra.unlink()
                (root / sorted(action.package_names(architecture))[0]).unlink()
                with self.assertRaisesRegex(ValueError, "omitted required"):
                    action.created_package_records(root, architecture)


if __name__ == "__main__":
    unittest.main()
