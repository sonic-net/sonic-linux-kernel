#!/usr/bin/env python3
"""Check input identity and Debian package handoff without compiling a sample kernel."""

import hashlib
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kernel_action as action
import build
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
            for architecture in action.HOST_ARCHITECTURES:
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

    def test_shared_amd64_runtime_supports_native_and_both_cross_targets(self):
        inputs = ["a" * 64]
        marker = {"schema_version": 1, "kind": "debian-build-tools", "architecture": "amd64",
                  "execution": "relocatable", "cross_architectures": ["arm64", "armhf"],
                  "input_sha256": inputs, "packages": {"make": "4.4.1"},
                  "identity_sha256": hashlib.sha256(json.dumps(
                      {"architecture": "amd64", "inputs": inputs}, sort_keys=True).encode()).hexdigest()}
        for target in action.ARCHITECTURES:
            self.assertEqual(action.validate_runtime_identity(marker, "amd64", target), marker)
        for targets in ([], ["arm64"], ["armhf", "armhf"], ["amd64", "armhf"], "armhf", [None]):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                action.validate_runtime_identity(dict(marker, cross_architectures=targets), "amd64", "armhf")

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
            action.validate_outputs(dict(config, architecture="riscv64"))

    def test_compile_recipe_keeps_maintained_debian_packaging_for_each_architecture(self):
        for architecture in action.ARCHITECTURES:
            with self.subTest(architecture=architecture):
                host = "amd64" if architecture == "armhf" else architecture
                config = build.kernel_configuration(architecture, {}, host)
                commands = action.compilation_commands(architecture, Path("/declared/source"), Path("/declared/archives"), config, host)
                self.assertEqual(commands[0][0:2], ["dpkg-source", "-x"])
                self.assertEqual(commands[5], ["bash", "/declared/source/manage-config", architecture, "no_sign", ""])
                self.assertIn("bazel-debug-sources.patch", commands[3][-1])
                self.assertIn("bazel-relocatable-tools.patch", commands[4][-1])
                if architecture == "armhf":
                    self.assertIn("-aarmhf", commands[-1])
                    self.assertIn("-Pcross,nocheck,nodoc", commands[-1])
                else:
                    self.assertEqual(commands[-1][:6], ["dpkg-buildpackage", "-b", "-us", "-uc", "-Pnodoc", "-j4"])
                self.assertNotIn("nokerneldbg", repr(commands))
        with self.assertRaisesRegex(ValueError, "unsupported kernel architecture"):
            action.compilation_commands("riscv64", Path("source"), Path("archives"), {}, "amd64")

    def test_cross_recipe_keeps_host_tools_and_targets_debian_architecture(self):
        for architecture in ("arm64", "armhf"):
            commands = action.compilation_commands(architecture, Path("source"), Path("archives"), build.kernel_configuration(architecture, {}, "amd64"), "amd64")
            self.assertIn("-a" + architecture, commands[-1])
            self.assertIn("-Pcross,nocheck,nodoc", commands[-1])
            self.assertNotIn("-d", commands[-1])  # Dependency validation remains enabled.
        with self.assertRaisesRegex(ValueError, "unsupported"):
            action.compilation_commands("amd64", Path("source"), Path("archives"), {}, "arm64")

    def test_debian_architecture_rejects_wrong_host_or_target(self):
        output = "DEB_BUILD_ARCH=amd64\nDEB_HOST_ARCH=armhf\nDEB_HOST_GNU_TYPE=arm-linux-gnueabihf\n"
        with patch.object(action.subprocess, "run", return_value=CompletedProcess([], 0, output, "")):
            values = action.debian_architecture(Path("runtime"), "armhf", "amd64", {})
            self.assertEqual(values["DEB_HOST_GNU_TYPE"], "arm-linux-gnueabihf")
            with self.assertRaisesRegex(ValueError, "architecture differs"):
                action.debian_architecture(Path("runtime"), "arm64", "amd64", {})
        for invalid in (output + "PATH=/host/bin\n", output + "DEB_BUILD_ARCH=amd64\n"):
            with patch.object(action.subprocess, "run", return_value=CompletedProcess([], 0, invalid, "")):
                with self.assertRaises(ValueError):
                    action.debian_architecture(Path("runtime"), "armhf", "amd64", {})

    def test_armhf_package_names_match_maintained_armmp_contract(self):
        names = action.package_names("armhf")
        self.assertIn("linux-image-6.12.41+deb13-sonic-armmp_6.12.41-1_armhf.deb", names)
        self.assertIn("linux-headers-6.12.41+deb13-sonic-armmp_6.12.41-1_armhf.deb", names)
        self.assertFalse(any("unsigned" in name for name in names))

    def test_recipe_uses_the_selected_configuration(self):
        config = build.kernel_configuration("arm64", {"SECURE_UPGRADE_MODE": ""})
        commands = action.compilation_commands("arm64", Path("/source"), Path("/archives"), config)
        self.assertEqual(commands[5], ["bash", "/source/manage-config", "arm64", "no_sign", ""])
        self.assertEqual(action.SOURCE_DATE_EPOCH, 1754969284)

    def test_configuration_binds_execution_host_target_and_armmp_abi(self):
        for host, target in (("amd64", "amd64"), ("arm64", "arm64"),
                             ("amd64", "arm64"), ("amd64", "armhf")):
            with self.subTest(host=host, target=target):
                config = build.kernel_configuration(target, {}, host)
                action.validate_configuration(config, target, host)
                for key, value in (("CONFIGURED_ARCH", "other"), ("KVERSION", "wrong-abi"),
                                   ("CROSS_BUILD_ENVIRON", "n" if host != target else "y"),
                                   ("ADDITIONAL_BUILD_PROFILES", "nokerneldbg")):
                    with self.assertRaisesRegex(ValueError, key):
                        action.validate_configuration(dict(config, **{key: value}), target, host)
                with self.assertRaisesRegex(ValueError, "normalized Make variables"):
                    action.validate_configuration({"CONFIGURED_ARCH": target}, target, host)

    def test_action_rejects_runtime_for_another_declared_execution_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {
                "schema": 5, "architecture": "arm64", "host_architecture": "arm64",
                "build_tools": temporary,
                "kernel_config": build.kernel_configuration("arm64", {}),
                "outputs": [str(Path(temporary) / name) for name in action.package_names("arm64")],
                "manifest": str(Path(temporary) / action.MANIFEST),
            }
            with patch.object(action.os, "geteuid", return_value=1000), \
                    patch.object(action, "runtime_identity", return_value={"architecture": "amd64"}), \
                    patch.object(action.subprocess, "run") as child, \
                    self.assertRaisesRegex(ValueError, "execution host differs"):
                action.build(config)
            child.assert_not_called()

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
        config = {"schema": 5, "outputs": sorted(action.package_names()), "manifest": action.MANIFEST}
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
