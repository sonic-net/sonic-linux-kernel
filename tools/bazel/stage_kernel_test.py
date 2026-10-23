#!/usr/bin/env python3
"""Focused regressions for signed kernel runtime/debug payloads."""
import hashlib
import lzma
import os
import struct
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
    def test_armhf_uses_the_existing_armmp_package_flavour(self):
        self.assertEqual(kernel_arch.kernel_abi("armhf"), "6.12.41+deb13-sonic-armmp")
        self.assertEqual(kernel_arch.build_directory("armhf"), "build_armhf_sonic_armmp")
        self.assertEqual(kernel_arch.kernel_architecture("armhf"), "arm")
        for host, target in (("amd64", "amd64"), ("amd64", "arm64"), ("amd64", "armhf"), ("arm64", "arm64")):
            self.assertEqual(kernel_arch.validate_build_pair(host, target), target)
        for host, target in (("arm64", "amd64"), ("arm64", "armhf"), ("armhf", "armhf")):
            with self.assertRaisesRegex(ValueError, "unsupported kernel build pair"):
                kernel_arch.validate_build_pair(host, target)

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
                    "objcopy", "-O", "binary", "-R", ".note", "-R", ".note.gnu.build-id",
                    "-R", ".comment", "-S", str(symbols), str(output) + ".Image",
                ])
                self.assertEqual(child.call_args_list[1].args[0], ["objcopy", "--strip-debug", str(symbols), str(output)])
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
                self.assertEqual(child.call_args.args[0], ["sh", str(root / "scripts/extract-vmlinux"), str(root / "vmlinuz")])
                self.assertEqual((root / "vmlinux").read_bytes(), b"embedded ELF")

    def test_armhf_zimage_payload_matches_debug_elf_using_target_objcopy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, symbols, output = root / "zImage", root / "debug", root / "vmlinux"
            payload = b"ARM raw boot code" * 128
            header = bytearray(48)
            struct.pack_into("<I", header, 36, 0x016F2818)
            image.write_bytes(header + lzma.compress(payload) + b"size and zImage trailer")
            symbols.write_bytes(b"debug ELF")
            def objcopy(command, **kwargs):
                Path(command[-1]).write_bytes(payload if "binary" in command else b"stripped ARM ELF")
            with patch.dict(os.environ, {"SONIC_KERNEL_CROSS_COMPILE": "arm-linux-gnueabihf-"}), \
                    patch.object(kernel_arch.subprocess, "run", side_effect=objcopy) as child:
                kernel_arch.materialize_kernel_elf("armhf", root, image, symbols, output)
                self.assertEqual(child.call_args_list[0].args[0], [
                    "arm-linux-gnueabihf-objcopy", "-O", "binary", "-R", ".comment", "-S",
                    str(symbols), str(output) + ".Image",
                ])
                self.assertEqual(output.read_bytes(), b"stripped ARM ELF")
                for incorrect in (lzma.compress(b"wrong kernel"), lzma.compress(payload)[:-15],
                                  lzma.compress(payload + b"unexpected byte")):
                    image.write_bytes(header + incorrect)
                    with self.assertRaisesRegex(ValueError, "boot zImage and debug vmlinux bytes differ"):
                        kernel_arch.materialize_kernel_elf("armhf", root, image, symbols, output)
                image.write_bytes(b"not a zImage")
                with self.assertRaisesRegex(ValueError, "zImage header"):
                    kernel_arch.materialize_kernel_elf("armhf", root, image, symbols, output)

    def test_elf_machine_must_match_selected_kernel_architecture(self):
        for architecture, other in (("amd64", "arm64"), ("arm64", "amd64"), ("armhf", "arm64")):
            with self.subTest(architecture=architecture), patch.object(stage.subprocess, "run") as child:
                child.return_value.stdout = ("Class: " + kernel_arch.elf_class(architecture) + "\nMachine: " + kernel_arch.elf_machine(architecture)
                                             + "\nBuild ID: " + "a" * 40 + "\n.debug_info\n")
                self.assertEqual(stage.elf_info(Path("fixture"), architecture), ("a" * 40, True))
                with self.assertRaisesRegex(ValueError, "kernel artifact"):
                    stage.elf_info(Path("fixture"), other)

    def test_interpreter_reader_ignores_signed_modules_and_reads_native_executables(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tool"
            interpreter = b"/declared/runtime/ld-linux.so\0"
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", header, 16, 2)
            struct.pack_into("<Q", header, 32, 64)
            struct.pack_into("<HH", header, 54, 56, 1)
            program = struct.pack("<IIQQQQQQ", 3, 4, 120, 0, 0, len(interpreter), len(interpreter), 1)
            path.write_bytes(header + program + interpreter)
            self.assertEqual(stage.elf_interpreter(path), interpreter[:-1].decode())
            struct.pack_into("<H", header, 16, 1)
            path.write_bytes(header + program + interpreter + b"~Module signature appended~")
            before = path.read_bytes()
            self.assertIsNone(stage.elf_interpreter(path))
            self.assertEqual(path.read_bytes(), before)

    def test_packaged_tools_restore_native_loader_and_preserve_nonprivate_rpath(self):
        for architecture, loader in [("amd64", "/lib64/ld-linux-x86-64.so.2"), ("arm64", "/lib/ld-linux-aarch64.so.1"),
                                     ("armhf", "/lib/ld-linux-armhf.so.3")]:
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime, package = root / "runtime", root / "package"
                tool = package / "usr/lib/linux-kbuild/scripts/tool"
                tool.parent.mkdir(parents=True)
                tool.write_bytes(b"native host tool")
                module = package / "signed.ko"
                module.write_bytes(b"~Module signature appended~")
                inspections = {tool: iter([str(runtime / "ld.so"), loader])}
                def inspect(path):
                    return next(inspections[path]) if path in inspections else None
                with patch.object(stage, "elf_interpreter", side_effect=inspect), \
                        patch.object(stage.subprocess, "check_output", return_value=str(runtime / "usr/lib") + ":$ORIGIN/../lib\n"), \
                        patch.object(stage.subprocess, "run") as child:
                    self.assertEqual(stage.normalize_tools(package, architecture, runtime, Path("/declared/patchelf")), 1)
                    self.assertEqual(child.call_args.args[0], ["/declared/patchelf", "--set-interpreter", loader,
                                                              "--set-rpath", "$ORIGIN/../lib", str(tool)])
                self.assertEqual(module.read_bytes(), b"~Module signature appended~")

    def test_armhf_interpreter_reader_handles_elf32(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tool"
            interpreter = b"/private/runtime/ld-linux-armhf.so.3\0"
            header = bytearray(52)
            header[:6] = b"\x7fELF\x01\x01"
            struct.pack_into("<HH", header, 16, 2, 40)
            struct.pack_into("<I", header, 28, 52)
            struct.pack_into("<HH", header, 42, 32, 1)
            program = struct.pack("<IIIIIIII", 3, 84, 0, 0, len(interpreter), len(interpreter), 4, 1)
            path.write_bytes(header + program + interpreter)
            self.assertEqual(stage.elf_interpreter(path), interpreter[:-1].decode())
            path.write_bytes(header + program + interpreter[:-2])
            with self.assertRaisesRegex(ValueError, "truncated ELF interpreter"):
                stage.elf_interpreter(path)

    def test_packaged_kbuild_tools_reject_build_machine_elf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            package.mkdir()
            tool = package / "modpost"
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<HH", header, 16, 2, 62)
            tool.write_bytes(header)
            for architecture in ("arm64", "armhf"):
                with self.assertRaisesRegex(ValueError, "wrong target architecture"), \
                        patch.object(stage.subprocess, "run") as child:
                    stage.normalize_tools(package, architecture, root / "runtime", Path("patchelf"))
                child.assert_not_called()

    def test_debug_compatibility_vdsos_preserve_their_foreign_elf(self):
        for architecture, name, machine in (("arm64", "vdso32.so", 40),
                                             ("amd64", "vdso32.so", 3),
                                             ("amd64", "vdsox32.so", 62)):
            with self.subTest(architecture=architecture, name=name), tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / "package"
                vdso = package / "usr/lib/debug/lib/modules" / kernel_arch.kernel_abi(architecture) / "vdso" / name
                vdso.parent.mkdir(parents=True)
                header = bytearray(52)
                header[:6] = b"\x7fELF\x01\x01"
                struct.pack_into("<HH", header, 16, 3, machine)
                vdso.write_bytes(header)
                with patch.object(stage.subprocess, "run") as child:
                    self.assertEqual(stage.normalize_tools(package, architecture, Path(temporary) / "runtime",
                                                           Path("patchelf")), 0)
                child.assert_not_called()
                self.assertEqual(vdso.read_bytes(), header)

    def test_compatibility_vdso_exception_rejects_tools_and_wrong_elf(self):
        abi = kernel_arch.kernel_abi("arm64")
        valid = "usr/lib/debug/lib/modules/" + abi + "/vdso/vdso32.so"
        cases = [
            (valid.replace(abi, "wrong-abi"), 3, 40, None),
            (valid.replace("vdso32.so", "other.so"), 3, 40, None),
            ("usr/lib/linux-kbuild/scripts/vdso32.so", 3, 40, None),
            ("lib/modules/" + abi + "/vdso/vdso32.so", 3, 40, None),
            (valid, 1, 40, None),
            (valid, 2, 40, None),
            (valid, 3, 3, None),
            (valid, 3, 183, None),
            (valid, 3, 40, b"/lib/ld-linux-armhf.so.3\0"),
        ]
        for relative, kind, machine, interpreter in cases:
            with self.subTest(relative=relative, kind=kind, machine=machine, interpreter=interpreter), \
                    tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / "package"
                vdso = package / relative
                vdso.parent.mkdir(parents=True)
                elf_class = 2 if machine == 183 else 1
                header = bytearray(64 if elf_class == 2 else 52)
                header[:6] = b"\x7fELF" + bytes([elf_class, 1])
                struct.pack_into("<HH", header, 16, kind, machine)
                if interpreter:
                    struct.pack_into("<I", header, 28, 52)
                    struct.pack_into("<HH", header, 42, 32, 1)
                    program = struct.pack("<IIIIIIII", 3, 84, 0, 0, len(interpreter), len(interpreter), 4, 1)
                    header += program + interpreter
                vdso.write_bytes(header)
                with self.assertRaisesRegex(ValueError, "wrong target architecture"), \
                        patch.object(stage.subprocess, "run") as child:
                    stage.normalize_tools(package, "arm64", Path(temporary) / "runtime", Path("patchelf"))
                child.assert_not_called()
                self.assertEqual(vdso.read_bytes(), header)

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
