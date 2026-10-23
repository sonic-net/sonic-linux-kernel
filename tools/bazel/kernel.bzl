"""Cacheable kernel compilation with declared tools in Bazel's Linux sandbox."""

load("@sonic_build_infra//tools/build_tools:defs.bzl", "PythonRuntimeInfo")
load(":sources.bzl", "KERNEL_SOURCES")

KernelConfigInfo = provider(fields = ["values"])
KernelExecutionInfo = provider(fields = ["architecture"])

def _kernel_execution_impl(ctx):
    if not ctx.target_platform_has_constraint(ctx.attr._linux[platform_common.ConstraintValueInfo]):
        fail("Kernel tools require a Linux execution platform")
    for architecture, constraint in [("amd64", ctx.attr._amd64), ("arm64", ctx.attr._arm64)]:
        if ctx.target_platform_has_constraint(constraint[platform_common.ConstraintValueInfo]):
            return [KernelExecutionInfo(architecture = architecture)]
    fail("Kernel tools require an AMD64 or ARM64 execution platform")

# This dependency is configured with cfg = "exec" by the package rule, so its
# target platform is the platform that actually runs the declared build tools.
kernel_execution = rule(
    implementation = _kernel_execution_impl,
    attrs = {
        "_linux": attr.label(default = Label("@platforms//os:linux")),
        "_amd64": attr.label(default = Label("@platforms//cpu:x86_64")),
        "_arm64": attr.label(default = Label("@platforms//cpu:aarch64")),
    },
)

def _kernel_config_impl(ctx):
    values = json.decode(ctx.build_setting_value)
    if type(values) != "dict" or any([type(key) != "string" or type(value) != "string" for key, value in values.items()]):
        fail("Unsupported kernel configuration: expected a JSON object of Make variable strings")
    return [KernelConfigInfo(values = values)]

kernel_config = rule(
    implementation = _kernel_config_impl,
    build_setting = config.string(flag = True),
)

def kernel_configuration(architecture, requested, host_architecture = None):
    """Own capability checks here, rather than in each image consumer."""
    host_architecture = host_architecture or architecture
    if (host_architecture, architecture) not in [
        ("amd64", "amd64"), ("arm64", "arm64"),
        ("amd64", "arm64"), ("amd64", "armhf"),
    ]:
        fail("Unsupported kernel build pair: %s -> %s" % (host_architecture, architecture))
    values = {
        "CONFIGURED_ARCH": architecture,
        "CONFIGURED_PLATFORM": "vs",
        "BLDENV": "trixie",
        "CROSS_BUILD_ENVIRON": "y" if host_architecture != architecture else "n",
        "MULTIARCH_QEMU_ENVIRON": "n",
        "KERNEL_VERSION": "6.12.41",
        "KERNEL_ABISUFFIX": "+deb13",
        "KERNEL_SUBVERSION": "1",
        "KERNEL_FEATURESET": "sonic",
        "KVERSION": "",
        "ADDITIONAL_BUILD_PROFILES": "",
        "SECURE_UPGRADE_MODE": "no_sign",
        "SECURE_UPGRADE_KERNEL_CAFILE": "",
        "INCLUDE_EXTERNAL_PATCHES": "n",
        "EXTERNAL_KERNEL_PATCH_URL": "",
        "EXTERNAL_KERNEL_PATCH_LOC": "",
    }
    defaults = dict(values)
    for key, value in requested.items():
        if key not in values:
            fail("Unsupported kernel configuration: unknown Make variable " + key)
        values[key] = value
    for key in ["CROSS_BUILD_ENVIRON", "MULTIARCH_QEMU_ENVIRON", "INCLUDE_EXTERNAL_PATCHES"]:
        values[key] = values[key] or "n"
    values["SECURE_UPGRADE_MODE"] = values["SECURE_UPGRADE_MODE"].strip('"') or "no_sign"
    if "KVERSION" not in requested:
        suffix = "armmp" if values["CONFIGURED_ARCH"] == "armhf" else values["CONFIGURED_ARCH"]
        values["KVERSION"] = values["KERNEL_VERSION"] + values["KERNEL_ABISUFFIX"] + "-" + values["KERNEL_FEATURESET"] + "-" + suffix
    defaults["KVERSION"] = "6.12.41+deb13-sonic-" + ("armmp" if architecture == "armhf" else architecture)
    for key in sorted(values):
        # Make does not read these inputs while their features are disabled.
        if key == "SECURE_UPGRADE_KERNEL_CAFILE" and values["SECURE_UPGRADE_MODE"] == "no_sign":
            continue
        if key in ["EXTERNAL_KERNEL_PATCH_URL", "EXTERNAL_KERNEL_PATCH_LOC"] and values["INCLUDE_EXTERNAL_PATCHES"] == "n":
            continue
        if values[key] != defaults[key]:
            fail("Unsupported kernel configuration: %s=%r; this producer supports %r" % (key, values[key], defaults[key]))
    return values

def _packages(architecture):
    abi = "6.12.41+deb13-sonic-" + ("armmp" if architecture == "armhf" else architecture)
    unsigned = "" if architecture == "armhf" else "-unsigned"
    suffix = "_6.12.41-1_" + architecture + ".deb"
    return [
        "linux-image-" + abi + unsigned + suffix,
        "linux-image-" + abi + "-dbg" + suffix,
        "linux-headers-" + abi + suffix,
        "linux-headers-6.12.41+deb13-common-sonic_6.12.41-1_all.deb",
        "linux-kbuild-6.12.41+deb13" + suffix,
    ]

def _kernel_packages_impl(ctx):
    architecture = ctx.attr.architecture
    host_architecture = ctx.attr._execution[KernelExecutionInfo].architecture
    kernel_config = kernel_configuration(architecture, ctx.attr._kernel_config[KernelConfigInfo].values, host_architecture)
    python = ctx.attr._python[PythonRuntimeInfo]
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
        "schema": 5,
        "architecture": architecture,
        "host_architecture": host_architecture,
        "kernel_config": kernel_config,
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
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64", "armhf"]),
        "srcs": attr.label_list(allow_files = True, mandatory = True),
        "makefile": attr.label(allow_single_file = True, mandatory = True),
        "dsc": attr.label(allow_single_file = True, mandatory = True),
        "orig": attr.label(allow_single_file = True, mandatory = True),
        "debian": attr.label(allow_single_file = True, mandatory = True),
        "build_tools": attr.label(allow_single_file = True, cfg = "exec", mandatory = True),
        "_kernel_config": attr.label(default = Label("//tools/bazel:kernel_config"), providers = [KernelConfigInfo]),
        "_execution": attr.label(default = Label("//tools/bazel:kernel_execution"), providers = [KernelExecutionInfo], cfg = "exec"),
        "_architecture": attr.label(default = Label("//tools/bazel:kernel_arch.py"), allow_single_file = True, cfg = "exec"),
        "_runner": attr.label(default = Label("//tools/bazel:kernel_action.py"), allow_single_file = True, cfg = "exec"),
        "_python": attr.label(default = Label("//tools/bazel:python_runtime"), providers = [PythonRuntimeInfo], cfg = "exec"),
        "_patchelf": attr.label(default = Label("@patchelf//:patchelf"), executable = True, cfg = "exec"),
    },
)
