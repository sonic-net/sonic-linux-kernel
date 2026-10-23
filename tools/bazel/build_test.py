#!/usr/bin/env python3
"""Check unprivileged launcher policy and output path containment."""

import argparse
import ast
import json
from types import SimpleNamespace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build


def arguments(root, architecture="amd64"):
    return argparse.Namespace(
        workspace=root / "consumer", work_dir=root / "work", repository_cache=root / "downloads",
        disk_cache=None, remote_cache="http://127.0.0.1:8080", remote_cache_read_only=True,
        java_trust_store=None, ca_bundle=None, distdir=None,
        module_override=[("sonic-linux-kernel", root / "source")], bazel_arg=[],
        target="@sonic_linux_kernel//:kernel_packages", architecture=architecture, kernel_config="{}",
    )


class KernelLauncherTest(unittest.TestCase):
    def test_cache_consumer_matches_source_toolchain_pin(self):
        root = Path(__file__).absolute().parents[2]

        def toolchain_version(path):
            versions = []
            for statement in ast.parse(path.read_text()).body:
                if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                    continue
                call = statement.value
                if not isinstance(call.func, ast.Name) or call.func.id != "bazel_dep":
                    continue
                keywords = {item.arg: item.value for item in call.keywords}
                name = keywords.get("name")
                if isinstance(name, ast.Constant) and name.value == "sonic-build-infra":
                    version = keywords.get("version")
                    self.assertIsInstance(version, ast.Constant, str(path))
                    self.assertIsInstance(version.value, str, str(path))
                    versions.append(version.value)
            self.assertEqual(len(versions), 1, str(path))
            return versions[0]

        self.assertEqual(toolchain_version(root / "MODULE.bazel"),
                         toolchain_version(root / "tools/bazel/cache-consumer/MODULE.bazel"),
                         "The cache consumer must resolve the same declared kernel toolchain as the source module")

    def test_configuration_forwarding_uses_the_target_repository_for_build_and_query(self):
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(Path(directory))
            for target, prefix in (("//:kernel_packages", "--//"),
                                   ("@sonic_linux_kernel//:kernel_packages", "--@sonic_linux_kernel//")):
                args.target = target
                # The launcher passes unsupported settings to producer analysis.
                args.kernel_config = '{"SECURE_UPGRADE_MODE":"unsupported","CONFIGURED_ARCH":"armhf"}'
                commands = build.build_commands(args, Path(directory) / "bazel")
                flag = prefix + "tools/bazel:kernel_config="
                for name in ("build", "query"):
                    value, = [option[len(flag):] for option in commands[name] if option.startswith(flag)]
                    config = json.loads(value)
                    self.assertEqual(config["SECURE_UPGRADE_MODE"], "unsupported")
                    self.assertEqual(config["CONFIGURED_ARCH"], "armhf")
                    self.assertEqual(config["KVERSION"], "6.12.41+deb13-sonic-armmp")
                self.assertFalse(any(":kernel_config=" in option for option in commands["graph"]))

    def test_configuration_rejects_non_string_values_and_normalizes_make_false_flags(self):
        for invalid in ('[]', '{"KERNEL_VERSION":6}', '{invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build.kernel_configuration("amd64", invalid)
        self.assertEqual(build.kernel_configuration("arm64", {}), build.kernel_configuration("arm64", {
            "CROSS_BUILD_ENVIRON": "", "MULTIARCH_QEMU_ENVIRON": "", "INCLUDE_EXTERNAL_PATCHES": "",
            "SECURE_UPGRADE_MODE": '"no_sign"',
        }))
        self.assertEqual(build.kernel_configuration("amd64", {}),
                         build.kernel_configuration("amd64", {"SECURE_UPGRADE_MODE": ""}))

    def test_producer_configuration_rejects_unsupported_requests_before_actions(self):
        # Execute the maintained Starlark validation body, which uses the same
        # language subset as Python. No duplicate capability list lives in tests.
        source = Path(__file__).with_name("kernel.bzl")
        tree = ast.parse(source.read_text())
        selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                    and node.name in ("kernel_configuration", "_kernel_packages_impl")], type_ignores=[])
        def fail(message):
            raise ValueError(message)
        provider = object()
        namespace = {"fail": fail, "KernelConfigInfo": provider}
        exec(compile(selected, str(source), "exec"), namespace)
        for architecture in ("amd64", "arm64"):
            default = build.kernel_configuration(architecture, {})
            self.assertEqual(namespace["kernel_configuration"](architecture, {}), default)
            self.assertEqual(namespace["kernel_configuration"](architecture, default), default)
            inactive = {
                "SECURE_UPGRADE_KERNEL_CAFILE": "/nonexistent/signing/certificate.pem",
                "EXTERNAL_KERNEL_PATCH_URL": "https://example.invalid/unused.patch",
                "EXTERNAL_KERNEL_PATCH_LOC": "/nonexistent/kernel/patches",
            }
            # Disabled features retain their Make values without trying to read
            # nonexistent certificates/patches or fetching an inactive URL.
            accepted = namespace["kernel_configuration"](architecture, inactive)
            self.assertEqual(accepted, dict(default, **inactive))
            for key, value in {
                "CONFIGURED_ARCH": "armhf", "CONFIGURED_PLATFORM": "asic", "BLDENV": "bookworm",
                "CROSS_BUILD_ENVIRON": "y", "MULTIARCH_QEMU_ENVIRON": "y", "KERNEL_VERSION": "6.12.42",
                "KERNEL_ABISUFFIX": "+custom", "KERNEL_SUBVERSION": "2", "KERNEL_FEATURESET": "other",
                "KVERSION": "custom", "ADDITIONAL_BUILD_PROFILES": "nokerneldbg",
                "SECURE_UPGRADE_MODE": "unsupported", "INCLUDE_EXTERNAL_PATCHES": "y",
                "SOURCE_DATE_EPOCH": "0", "TYPO": "n",
            }.items():
                with self.subTest(architecture=architecture, key=key):
                    # No action or tool attributes exist: rejection must happen
                    # before the implementation attempts to declare an output.
                    ctx = SimpleNamespace(attr=SimpleNamespace(architecture=architecture,
                                          _kernel_config={provider: SimpleNamespace(values={key: value})}))
                    with self.assertRaisesRegex(ValueError, "Unsupported kernel configuration.*" + key):
                        namespace["_kernel_packages_impl"](ctx)

    def test_native_architecture_rejects_emulated_or_cross_configuration(self):
        for architecture, machine in (("amd64", "x86_64"), ("arm64", "aarch64")):
            with self.subTest(architecture=architecture), patch.object(
                    build.os, "uname", return_value=os.uname_result(("Linux", "runner", "", "", machine))):
                self.assertEqual(build.native_architecture(), architecture)
                self.assertEqual(build.native_architecture(architecture), architecture)
                other = "arm64" if architecture == "amd64" else "amd64"
                with self.assertRaisesRegex(ValueError, "must match"):
                    build.native_architecture(other)
        with patch.object(build.os, "uname", return_value=os.uname_result(("Linux", "runner", "", "", "riscv64"))):
            with self.assertRaisesRegex(ValueError, "native Linux"):
                build.native_architecture()

    def test_direct_commands_use_native_platform_and_linux_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for architecture, platform in (("amd64", "x86_64_trixie"), ("arm64", "aarch64_trixie")):
                with self.subTest(architecture=architecture):
                    args = arguments(root, architecture)
                    commands = build.build_commands(args, root / "bazel")
                    for kind in ("build", "query"):
                        command = commands[kind]
                        self.assertEqual(command[0], str(root / "bazel"))
                        self.assertIn("--strategy=SonicKernelBuild=linux-sandbox", command)
                        self.assertIn("--host_platform=@sonic_build_infra//platforms:" + platform, command)
                        self.assertIn("--platforms=@sonic_build_infra//platforms:" + platform, command)
                        self.assertIn("--disk_cache=", command)
                        self.assertIn("--remote_upload_local_results=false", command)
                    graph = commands["graph"]
                    self.assertIn("--override_module=sonic-linux-kernel=" + str(root / "source"), graph)
                    self.assertIn("--repository_cache=" + str(root / "downloads"), graph)
                    self.assertIn("--lockfile_mode=off", graph)
                    self.assertFalse(any(option.startswith(("--strategy=", "--jobs=", "--platforms=", "--host_platform="))
                                         for option in graph))

    def test_extra_options_cannot_change_execution_or_cache_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(Path(directory))
            for option in ("--strategy=SonicKernelBuild=local", "--spawn_strategy=local",
                           "--strategy_regexp=.*=local", "--disk_cache=/old-output", "--config=local"):
                with self.subTest(option=option):
                    args.bazel_arg = [option]
                    with self.assertRaisesRegex(ValueError, "only --registry"):
                        build.build_commands(args, Path(directory) / "bazel")
            args.bazel_arg = ["--registry=https://example.test/registry"]
            for command in build.build_commands(args, Path(directory) / "bazel").values():
                self.assertIn("--noworkspace_rc", command)
                self.assertIn("--bazelrc=" + str(args.work_dir / "selected.bazelrc"), command)

    def test_registry_selection_replaces_endpoints_and_preserves_workspace_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(Path(directory))
            args.workspace.mkdir()
            original = ("# workspace policy\ncommon --registry=https://registry.test/main --enable_bzlmod\n"
                        "common --registry=https://bcr.bazel.build\n"
                        "build --sandbox_default_allow_network=false\n")
            (args.workspace / ".bazelrc").write_text(original)
            self.assertIsNone(build.selected_bazelrc(args))
            default = build.build_commands(args, Path(directory) / "bazel")
            self.assertNotIn("--noworkspace_rc", default["build"])
            args.bazel_arg = ["--registry=https://registry.test/reviewed-branch", "--registry=https://bcr.bazel.build"]
            selected = build.selected_bazelrc(args)
            self.assertEqual([line for line in selected.splitlines() if "--registry=" in line],
                             ["common " + value for value in args.bazel_arg])
            self.assertIn("common --enable_bzlmod", selected)
            self.assertIn("build --sandbox_default_allow_network=false", selected)
            self.assertEqual((args.workspace / ".bazelrc").read_text(), original)
            (args.workspace / ".bazelrc").write_text("import /tmp/extra.bazelrc\n" + original)
            with self.assertRaisesRegex(ValueError, "without imports"):
                build.selected_bazelrc(args)

    def test_output_paths_resolve_to_owned_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            execution = work / "output" / "execroot" / "consumer"
            output = execution / "bazel-out" / "bin" / "kernel.deb"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"package")
            (work / "bazel-out").symlink_to(execution / "bazel-out")
            query = work / "output-paths.relative.txt"
            query.write_text("bazel-out/bin/kernel.deb\n")
            build.output_paths(work)
            self.assertEqual((work / "output-paths.txt").read_text(), str(output) + "\n")
            escaped = Path(directory) / "outside.deb"
            escaped.write_bytes(b"outside")
            output.unlink()
            output.symlink_to(escaped)
            for value in ("bazel-out/bin/kernel.deb", str(escaped), "../outside.deb"):
                query.write_text(value + "\n")
                with self.assertRaises(ValueError):
                    build.output_paths(work)

    def test_privileged_launcher_fails_before_download(self):
        argv = ["build", "--workspace", "/tmp/workspace", "--work-dir", "/tmp/kernel-build"]
        with patch.object(sys, "argv", argv), patch.object(build, "native_architecture", return_value="amd64"), \
                patch.object(build.os, "geteuid", return_value=0), patch.object(build, "bazel_binary") as download, \
                patch.object(argparse.ArgumentParser, "error", side_effect=ValueError) as error:
            with self.assertRaises(ValueError):
                build.main()
            self.assertIn("unprivileged", error.call_args.args[0])
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
