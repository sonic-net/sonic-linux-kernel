#!/usr/bin/env python3
"""Focused regressions for signed kernel runtime/debug payloads."""
import hashlib
import lzma
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage_kernel as stage
import kernel_arch
import verify_debug


class StageKernelTest(unittest.TestCase):
    def test_signed_runtime_keeps_bytes_and_pairs_original_debug_by_build_id(self):
        for architecture in kernel_arch.ARCHITECTURES:
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                abi = kernel_arch.kernel_abi(architecture)
                runtime, debug = root / "runtime", root / "debug"
                kernel = debug / ("usr/lib/debug/boot/vmlinux-" + abi)
                kernel.parent.mkdir(parents=True)
                kernel.write_bytes(b"kernel debug")
                runtime_file = runtime / "usr/lib/modules" / abi / "kernel/test.ko.xz"
                runtime_file.parent.mkdir(parents=True)
                signed = b"runtime ELF~Module signature appended~\n"
                runtime_file.write_bytes(lzma.compress(signed))
                original_hash = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
                debug_file = debug / "usr/lib/debug/lib/modules" / abi / "kernel/test.ko"
                debug_file.parent.mkdir(parents=True)
                debug_file.write_bytes(b"debug ELF")
                def inspect(path, selected):
                    self.assertEqual(selected, architecture)
                    if path.name.startswith("vmlinux"):
                        return "a" * 40, path == kernel
                    return "b" * 40, path == debug_file
                with patch.object(stage, "elf_info", side_effect=inspect), patch.object(stage, "materialize_kernel_elf") as boot:
                    result = stage.validate_debug_pairs(root / "source", runtime, debug, architecture)
                    self.assertEqual(boot.call_args.args[0], architecture)
                    self.assertEqual(result["module_count"], 1)
                    self.assertEqual(hashlib.sha256(runtime_file.read_bytes()).hexdigest(), original_hash)
                    link = debug / "usr/lib/debug/.build-id/bb" / ("b" * 38 + ".debug")
                    self.assertEqual(link.resolve(), debug_file)
                def mismatched(path, selected):
                    identity, has_debug = inspect(path, selected)
                    return ("c" * 40 if path == debug_file else identity), has_debug
                with patch.object(stage, "elf_info", side_effect=mismatched), patch.object(stage, "materialize_kernel_elf"):
                    with self.assertRaisesRegex(ValueError, "build ID or DWARF mismatch"):
                        stage.validate_debug_pairs(root / "source", runtime, debug, architecture)

    def test_arm64_boot_image_must_match_reconstructed_debug_vmlinux(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, symbols, output = root / "Image", root / "vmlinux.debug", root / "vmlinux"
            image.write_bytes(b"actual deployed raw ARM64 image")
            symbols.write_bytes(b"debug ELF")
            def objcopy(command, **kwargs):
                Path(command[-1]).write_bytes(b"actual deployed raw ARM64 image" if "binary" in command else b"stripped ELF probe")
            with patch.object(kernel_arch.subprocess, "run", side_effect=objcopy) as child:
                kernel_arch.materialize_kernel_elf("arm64", root, image, symbols, output)
                self.assertEqual(child.call_args_list[0].args[0], [
                    "/usr/bin/objcopy", "-O", "binary", "-R", ".note", "-R", ".note.gnu.build-id",
                    "-R", ".comment", "-S", str(symbols), str(output) + ".Image",
                ])
                self.assertEqual(child.call_args_list[1].args[0], ["/usr/bin/objcopy", "--strip-debug", str(symbols), str(output)])
                self.assertEqual(output.read_bytes(), b"stripped ELF probe")
                image.write_bytes(b"different kernel image")
                child.reset_mock()
                with self.assertRaisesRegex(ValueError, "boot Image and debug vmlinux bytes differ"):
                    kernel_arch.materialize_kernel_elf("arm64", root, image, symbols, output)
                self.assertEqual(child.call_count, 1)

    def test_amd64_keeps_embedded_elf_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def extract(command, **kwargs):
                kwargs["stdout"].write(b"embedded ELF")
            with patch.object(kernel_arch.subprocess, "run", side_effect=extract) as child:
                kernel_arch.materialize_kernel_elf("amd64", root, root / "vmlinuz", root / "debug", root / "vmlinux")
                self.assertEqual(child.call_args.args[0], ["/bin/sh", str(root / "scripts/extract-vmlinux"), str(root / "vmlinuz")])
                self.assertEqual((root / "vmlinux").read_bytes(), b"embedded ELF")

    def test_elf_machine_must_match_selected_kernel_architecture(self):
        for architecture, other in (("amd64", "arm64"), ("arm64", "amd64")):
            with self.subTest(architecture=architecture), patch.object(stage.subprocess, "run") as child:
                child.return_value.stdout = ("Class: ELF64\nMachine: " + kernel_arch.elf_machine(architecture)
                                             + "\nBuild ID: " + "a" * 40 + "\n.debug_info\n")
                self.assertEqual(stage.elf_info(Path("fixture"), architecture), ("a" * 40, True))
                with self.assertRaisesRegex(ValueError, "kernel artifact"):
                    stage.elf_info(Path("fixture"), other)

    def test_debug_package_adds_patched_sources_and_generated_native_headers(self):
        for architecture in kernel_arch.ARCHITECTURES:
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, debug = root / "source", root / "debug-package"
                abi = kernel_arch.kernel_abi(architecture)
                build = source / "debian/build" / kernel_arch.build_directory(architecture)
                kernel_architecture = kernel_arch.kernel_architecture(architecture)
                for relative in ("include/generated/test.h", "arch/" + kernel_architecture + "/include/generated/test.h", ".config"):
                    path = build / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("generated for " + architecture)
                patched = source / "debian/build/source_sonic/init/main.c"
                patched.parent.mkdir(parents=True)
                patched.write_text("patched kernel source")
                symbols = debug / ("usr/lib/debug/boot/vmlinux-" + abi)
                symbols.parent.mkdir(parents=True)
                symbols.write_bytes(b"original Debian debug ELF")
                receipt = {"kernel_build_id": "ab" * 20, "module_count": 1, "module_build_ids_sha256": "cd" * 32}
                with patch.object(stage, "validate_debug_pairs", return_value=receipt) as validate:
                    stage.stage(source, debug, root / "artifacts", architecture)
                    self.assertEqual(validate.call_args.args, (source, build / "output/image", debug, architecture))
                installed = debug / "usr/src/debug" / ("linux-" + abi)
                self.assertEqual((installed / "debian/build/source_sonic/init/main.c").read_text(), patched.read_text())
                self.assertTrue((installed / "debian/build" / build.name / "arch" / kernel_architecture / "include/generated/test.h").is_file())
                self.assertEqual(symbols.read_bytes(), b"original Debian debug ELF")
                symbols.unlink()
                with self.assertRaisesRegex(ValueError, "has not staged"):
                    stage.stage(source, debug, root / "other-artifacts", architecture)

    def test_arm64_debugger_verifies_matching_kernel_and_module_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            abi = kernel_arch.kernel_abi("arm64")
            module = root / "usr/lib/modules" / abi / "kernel/dummy.ko.xz"
            module.parent.mkdir(parents=True)
            module.write_bytes(lzma.compress(b"signed runtime module"))
            result = {"function": "symbol", "source": "source.c", "line": 1, "type_lookup": True}
            with patch.object(verify_debug.subprocess, "check_output", return_value="GNU gdb fixture\n"), \
                    patch.object(verify_debug, "materialize_kernel_elf") as boot, \
                    patch.object(verify_debug, "gdb_check", return_value=result) as debugger:
                receipt = verify_debug.verify(root, "arm64")
                self.assertEqual(receipt["architecture"], "arm64")
                self.assertEqual(boot.call_args.args[:4], (
                    "arm64", root / "usr/src/debug" / ("linux-" + abi) / "debian/build/source_sonic",
                    root / ("boot/vmlinuz-" + abi), root / ("usr/lib/debug/boot/vmlinux-" + abi),
                ))
                self.assertEqual(debugger.call_args_list[1].args[1], root / "usr/lib/debug/lib/modules" / abi / "kernel/dummy.ko")


if __name__ == "__main__":
    unittest.main()
