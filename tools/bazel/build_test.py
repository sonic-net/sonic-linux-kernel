#!/usr/bin/env python3
"""Check the kernel launcher's bounded worker and resolution command."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build


class KernelLauncherTest(unittest.TestCase):
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

    def test_worker_bounds_and_module_inspection_use_supported_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, work, source = root / "consumer", root / "work", root / "source"
            for path in (workspace, work, source):
                path.mkdir()
            args = argparse.Namespace(
                workspace=workspace, work_dir=work, repository_cache=None, disk_cache=None,
                remote_cache="http://127.0.0.1:8080", remote_cache_read_only=True,
                java_trust_store=None, ca_bundle=None, distdir=None,
                module_override=[("sonic-linux-kernel", source)],
                bazel_arg=["--strategy=SonicKernelBuild=local"],
                target="@sonic_linux_kernel//:kernel_artifacts", worker_image=build.WORKER_IMAGE,
                architecture="amd64",
            )
            command = build.worker_command(args, root / "bazel", "kernel-test")
            self.assertIn("--cpus=4", command)
            self.assertIn("--memory=12g", command)
            self.assertIn("--memory-swap=12g", command)
            script = command[-1]
            subprocess.run(["bash", "-n", "-c", script], check=True)
            graph = next(line for line in script.splitlines() if " mod graph " in line)
            options = shlex.split(graph)
            self.assertIn("--override_module=sonic-linux-kernel=/module-overrides/sonic-linux-kernel", options)
            self.assertIn("--repository_cache=/repository-cache", options)
            self.assertIn("--lockfile_mode=off", options)
            self.assertFalse(any(option.startswith(("--strategy=", "--jobs=", "--platforms=", "--host_platform="))
                                 for option in options))
            self.assertIn("/work/module-graph.json", graph)
            self.assertIn("/work/module-graph.exit-code", script)
            for architecture, platform in (("amd64", "x86_64_trixie"), ("arm64", "aarch64_trixie")):
                args.architecture = architecture
                command = build.worker_command(args, root / "bazel", "kernel-test")
                self.assertIn("--platform=linux/" + architecture, command)
                for line in command[-1].splitlines():
                    if " build " in line or " cquery " in line:
                        self.assertIn("--host_platform=@sonic_build_infra//platforms:" + platform, shlex.split(line))
                        self.assertIn("--platforms=@sonic_build_infra//platforms:" + platform, shlex.split(line))
                self.assertIn(build.ARCHITECTURES[architecture]["machine"], command[-1])


if __name__ == "__main__":
    unittest.main()
