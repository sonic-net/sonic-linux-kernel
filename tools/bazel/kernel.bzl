"""Cacheable kernel compilation with declared tools in Bazel's Linux sandbox."""

load("@sonic_build_infra//tools/build_tools:defs.bzl", "PythonRuntimeInfo")
load(":sources.bzl", "KERNEL_SOURCES")

def _packages(architecture):
    abi = "6.12.41+deb13-sonic-" + architecture
    suffix = "_6.12.41-1_" + architecture + ".deb"
    return [
        "linux-image-" + abi + "-unsigned" + suffix,
        "linux-image-" + abi + "-dbg" + suffix,
        "linux-headers-" + abi + suffix,
        "linux-headers-6.12.41+deb13-common-sonic_6.12.41-1_all.deb",
        "linux-kbuild-6.12.41+deb13" + suffix,
    ]

def _kernel_packages_impl(ctx):
    python = ctx.attr._python[PythonRuntimeInfo]
    architecture = ctx.attr.architecture
    outputs = [ctx.actions.declare_file(architecture + "/" + name) for name in _packages(architecture)]
    manifest = ctx.actions.declare_file(architecture + "/kernel-packages.json")
    config = ctx.actions.declare_file(ctx.label.name + ".action.json")
    source_dir = ctx.file.makefile.dirname
    source_root = source_dir + "/" if source_dir not in ["", "."] else ""
    sources = []
    for src in ctx.files.srcs:
        if not src.path.startswith(source_root):
            fail("kernel inputs must belong to the kernel source directory")
        sources.append({"path": src.path, "name": src.path[len(source_root):]})
    archives = []
    for name, src in [("kernel_dsc", ctx.file.dsc), ("kernel_orig", ctx.file.orig), ("kernel_debian", ctx.file.debian)]:
        source = KERNEL_SOURCES[name]
        archives.append({"path": src.path, "name": source.name, "sha256": source.sha256})
    ctx.actions.write(config, json.encode({
        "schema": 4,
        "architecture": architecture,
        "sources": sources,
        "archives": archives,
        "build_tools": ctx.file.build_tools.path,
        "patchelf": ctx.executable._patchelf.path,
        "outputs": [f.path for f in outputs],
        "manifest": manifest.path,
    }))
    ctx.actions.run(
        executable = python.interpreter,
        arguments = [ctx.file._runner.path, config.path],
        inputs = depset(ctx.files.srcs + [config, ctx.file.build_tools, ctx.file.dsc, ctx.file.orig, ctx.file.debian]),
        tools = [ctx.attr._patchelf[DefaultInfo].files_to_run, python.interpreter, ctx.file._runner, ctx.file._architecture] + python.files.to_list(),
        outputs = outputs + [manifest],
        env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "SonicKernelBuild",
        progress_message = "Compiling SONiC 6.12.41 %s VS kernel Debian image/debug/headers/kbuild packages" % architecture.upper(),
        execution_requirements = {"block-network": "", "no-remote-exec": "1"},
    )
    return [
        DefaultInfo(files = depset(outputs + [manifest])),
        OutputGroupInfo(
            runtime = depset([outputs[0]]),
            debug = depset([outputs[1]]),
            development = depset(outputs[2:]),
            manifest = depset([manifest]),
        ),
    ]

kernel_packages = rule(
    implementation = _kernel_packages_impl,
    attrs = {
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64"]),
        "srcs": attr.label_list(allow_files = True, mandatory = True),
        "makefile": attr.label(allow_single_file = True, mandatory = True),
        "dsc": attr.label(allow_single_file = True, mandatory = True),
        "orig": attr.label(allow_single_file = True, mandatory = True),
        "debian": attr.label(allow_single_file = True, mandatory = True),
        "build_tools": attr.label(allow_single_file = True, cfg = "exec", mandatory = True),
        "_architecture": attr.label(default = Label("//tools/bazel:kernel_arch.py"), allow_single_file = True, cfg = "exec"),
        "_runner": attr.label(default = Label("//tools/bazel:kernel_action.py"), allow_single_file = True, cfg = "exec"),
        "_python": attr.label(default = Label("//tools/bazel:python_runtime"), providers = [PythonRuntimeInfo], cfg = "exec"),
        "_patchelf": attr.label(default = Label("@patchelf//:patchelf"), executable = True, cfg = "exec"),
    },
)
