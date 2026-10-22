"""Original Debian kernel sources, shared by standalone and image consumers."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")

KERNEL_SOURCES = {
    "kernel_dsc": struct(
        name = "linux_6.12.41-1.dsc",
        sha256 = "65bbf4e35635465326c9470605da1886ce3d3cf7e2a6d93392c4c6245d895b34",
    ),
    "kernel_orig": struct(
        name = "linux_6.12.41.orig.tar.xz",
        sha256 = "78afa637ca891174c22b332e4c1f87cf6aaa81861a6cca3b529e861843fa2fd3",
    ),
    "kernel_debian": struct(
        name = "linux_6.12.41-1.debian.tar.xz",
        sha256 = "b62680107fc155ad3d2c421ba0af1d5848812674fe767fcd40ba81a414e4ce2a",
    ),
}

def _kernel_sources_impl(_module_ctx):
    for name, source in KERNEL_SOURCES.items():
        http_file(
            name = name,
            urls = ["https://packages.trafficmanager.net/public/debian-security/pool/updates/main/l/linux/" + source.name],
            downloaded_file_path = source.name,
            sha256 = source.sha256,
        )

kernel_sources = module_extension(implementation = _kernel_sources_impl)
