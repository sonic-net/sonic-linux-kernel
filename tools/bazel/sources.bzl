"""Original Debian kernel sources, shared by standalone and image consumers."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")

KERNEL_SOURCES = {
    "kernel_dsc": struct(
        name = "linux_6.12.94-1.dsc",
        sha256 = "985a33d437c11f73f672b808b62589a0db3e97c6c286f315dbe14d1fc43f1191",
    ),
    "kernel_orig": struct(
        name = "linux_6.12.94.orig.tar.xz",
        sha256 = "3eea3dd18f67a103f6f8312e314075f695a27eaddd839e7b6c4641e6616dabba",
    ),
    "kernel_debian": struct(
        name = "linux_6.12.94-1.debian.tar.xz",
        sha256 = "8c5130fd9dee8d6efcc594dc3357809ad8e0d0234357139f85f5ed39b37922b4",
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
