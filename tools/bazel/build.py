#!/usr/bin/env python3
"""Build kernel DEBs as the current user in Bazel's Linux sandbox."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shlex
import ssl
import subprocess
import sys
import urllib.request
import urllib.parse
import uuid

BAZEL_VERSION = "8.5.1"
EXECUTION_STRATEGY = "linux-sandbox"
# Buildimage consumers inspect these literal launcher pins.
BAZEL_URL = "https://releases.bazel.build/8.5.1/release/bazel-8.5.1-linux-x86_64"
BAZEL_SHA256 = "61d89402f0368e64b6c827be5de79d8e65382e8124c3cbb97325611a1851392e"
HOST_ARCHITECTURES = {
    "amd64": {
        "machine": "x86_64", "platform": "x86_64_trixie",
        "bazel_url": BAZEL_URL, "bazel_sha256": BAZEL_SHA256,
    },
    "arm64": {
        "machine": "aarch64", "platform": "aarch64_trixie",
        "bazel_url": "https://releases.bazel.build/8.5.1/release/bazel-8.5.1-linux-arm64",
        "bazel_sha256": "b7f2a85595e8a87d54843bc656c1e379fd9b8c1b5f783dc41717c1d7eb7cc49f",
    },
}


TARGET_PLATFORMS = {
    "amd64": "x86_64_trixie",
    "arm64": "aarch64_trixie",
    "armhf": "armv7_trixie",
}


def kernel_configuration(architecture, requested, host_architecture=None):
    """Expand Make defaults without deciding which configurations Bazel supports."""
    if isinstance(requested, str):
        requested = json.loads(requested)
    if not isinstance(requested, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                                for key, value in requested.items()):
        raise ValueError("--kernel-config requires a JSON object of Make variable strings")
    host_architecture = host_architecture or architecture
    values = {
        "CONFIGURED_ARCH": architecture, "CONFIGURED_PLATFORM": "vs", "BLDENV": "trixie",
        "CROSS_BUILD_ENVIRON": "y" if host_architecture != architecture else "n", "MULTIARCH_QEMU_ENVIRON": "n",
        "KERNEL_VERSION": "6.12.41", "KERNEL_ABISUFFIX": "+deb13", "KERNEL_SUBVERSION": "1",
        "KERNEL_FEATURESET": "sonic", "ADDITIONAL_BUILD_PROFILES": "",
        "SECURE_UPGRADE_MODE": "no_sign", "SECURE_UPGRADE_KERNEL_CAFILE": "",
        "INCLUDE_EXTERNAL_PATCHES": "n", "EXTERNAL_KERNEL_PATCH_URL": "", "EXTERNAL_KERNEL_PATCH_LOC": "",
    }
    values.update(requested)
    for key in ("CROSS_BUILD_ENVIRON", "MULTIARCH_QEMU_ENVIRON", "INCLUDE_EXTERNAL_PATCHES"):
        values[key] = values[key] or "n"
    values["SECURE_UPGRADE_MODE"] = values["SECURE_UPGRADE_MODE"].strip('"') or "no_sign"
    suffix = "armmp" if values["CONFIGURED_ARCH"] == "armhf" else values["CONFIGURED_ARCH"]
    values.setdefault("KVERSION", values["KERNEL_VERSION"] + values["KERNEL_ABISUFFIX"] + "-"
                      + values["KERNEL_FEATURESET"] + "-" + suffix)
    return values


def kernel_config_option(args):
    if not re.fullmatch(r"(?:@{1,2}[^/]+)?//[^:]*:[^:]+", args.target):
        raise ValueError("--target must be an absolute Bazel label")
    repository = args.target.split("//", 1)[0]
    values = kernel_configuration(args.architecture, args.kernel_config, args.host_architecture)
    return "--" + repository + "//tools/bazel:kernel_config=" + json.dumps(values, sort_keys=True, separators=(",", ":"))


def native_architecture():
    host = os.uname()
    native = next((name for name, config in HOST_ARCHITECTURES.items()
                   if host.sysname == "Linux" and host.machine == config["machine"]), None)
    if native is None:
        raise ValueError("kernel source builds require native Linux AMD64 or ARM64 execution")
    return native


def target_architecture(host, requested=None):
    target = requested or host
    if host not in HOST_ARCHITECTURES or target not in TARGET_PLATFORMS or (host != "amd64" and target != host):
        raise ValueError("supported kernel builds are native AMD64/ARM64 and AMD64 cross builds for ARM64/ARMHF")
    return target


def bazel_binary(directory, ca_bundle=None, architecture="amd64"):
    config = HOST_ARCHITECTURES[architecture]
    url = config["bazel_url"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("bazel-8.5.1-" + architecture)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == config["bazel_sha256"]:
        return path
    temporary = directory / ("bazel-download-" + uuid.uuid4().hex)
    try:
        context = ssl.create_default_context()
        if ca_bundle:
            context.load_verify_locations(cafile=str(ca_bundle))
        with urllib.request.urlopen(url, timeout=120, context=context) as response, temporary.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != config["bazel_sha256"]:
            raise ValueError("Bazel executable checksum mismatch")
        temporary.chmod(0o755)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def selected_bazelrc(args):
    """Replace registry options without discarding the workspace's other policy."""
    if not args.bazel_arg:
        return None
    for option in args.bazel_arg:
        if not option.startswith("--registry=") or not option.split("=", 1)[1] or any(c.isspace() for c in option):
            raise ValueError("--bazel-arg supports only --registry=URL")
    source = args.workspace / ".bazelrc"
    lines = []
    for line in source.read_text().splitlines() if source.is_file() else []:
        words = shlex.split(line, comments=True)
        if words and words[0] in ("import", "try-import"):
            raise ValueError("registry replacement requires a workspace .bazelrc without imports")
        if not any(word == "--registry" or word.startswith("--registry=") for word in words[1:]):
            lines.append(line)
            continue
        kept = words[:1]
        index = 1
        while index < len(words):
            if words[index] == "--registry":
                index += 2
            elif words[index].startswith("--registry="):
                index += 1
            else:
                kept.append(words[index])
                index += 1
        if len(kept) > 1:
            lines.append(shlex.join(kept))
    lines.extend("common " + shlex.quote(option) for option in args.bazel_arg)
    return "\n".join(lines) + "\n"


def build_commands(args, executable):
    """Use caller-owned paths and preserve declared platform and execution policy."""
    host = args.host_architecture
    target_architecture(host, args.architecture)
    host_platform = "@sonic_build_infra//platforms:" + HOST_ARCHITECTURES[host]["platform"]
    platform = "@sonic_build_infra//platforms:" + TARGET_PLATFORMS[args.architecture]
    work = args.work_dir.resolve()
    repository_cache = (args.repository_cache or work / "repository-cache").resolve()
    bazel = [str(executable), "--output_user_root=" + str(work / "output"),
             "--nosystem_rc", "--nohome_rc", "--max_idle_secs=60"]
    if selected_bazelrc(args) is not None:
        bazel += ["--noworkspace_rc", "--bazelrc=" + str(work / "selected.bazelrc")]
    if args.java_trust_store:
        bazel.append("--host_jvm_args=-Djavax.net.ssl.trustStore=" + str(args.java_trust_store.resolve()))
    common = [
        "--repository_cache=" + str(repository_cache), "--lockfile_mode=update",
        "--symlink_prefix=" + str(work / "bazel-"),
        "--disk_cache=" + (str(args.disk_cache.resolve()) if args.disk_cache else ""),
        # Materialize all declared tools before the sandbox starts.
        "--remote_download_outputs=all", "--jobs=1",
        "--sandbox_default_allow_network=false",
        "--strategy=SonicKernelBuild=" + EXECUTION_STRATEGY,
        "--host_platform=" + host_platform, "--platforms=" + platform,
        kernel_config_option(args),
    ]
    if args.remote_cache:
        common += ["--remote_cache=" + args.remote_cache,
                   "--remote_upload_local_results=" + str(not args.remote_cache_read_only).lower()]
    if args.ca_bundle:
        common += ["--repo_env=SSL_CERT_FILE=" + str(args.ca_bundle.resolve()),
                   "--repo_env=REQUESTS_CA_BUNDLE=" + str(args.ca_bundle.resolve())]
    if args.distdir:
        common.append("--distdir=" + str(args.distdir.resolve()))
    for module, directory in args.module_override:
        common.append("--override_module=" + module + "=" + str(directory.resolve()))
    build = bazel + ["build"] + common + [
        "--execution_log_json_file=" + str(work / "execution.json"),
        "--profile=" + str(work / "profile.json.gz"),
        "--build_event_json_file=" + str(work / "bep.json"), args.target,
    ]
    query = bazel + ["cquery"] + common + ["--output=files", args.target]
    graph_options = [option for option in common if option.startswith((
        "--repository_cache=", "--repo_env=", "--distdir=", "--override_module=", "--registry=",
    ))]
    graph = bazel + ["mod", "graph"] + graph_options + [
        "--output=json", "--extension_info=hidden", "--lockfile_mode=off",
        "--color=no", "--curses=no",
    ]
    return {"build": build, "query": query, "graph": graph}


def output_paths(work):
    """Resolve cquery outputs through this invocation's execution root only."""
    work = work.resolve()
    execution_root = (work / "bazel-out").resolve(strict=True).parent
    execution_root.relative_to(work)
    outputs = []
    for line in (work / "output-paths.relative.txt").read_text().splitlines():
        relative = Path(line)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("kernel output escaped the execution root")
        output = (execution_root / relative).resolve(strict=True)
        output.relative_to(work)
        if not output.is_file():
            raise ValueError("kernel output is not a regular file")
        outputs.append(str(output))
    (work / "output-paths.txt").write_text("\n".join(outputs) + "\n")


def run_build(args, commands):
    work = args.work_dir.resolve()
    environment = os.environ.copy()
    if args.ca_bundle:
        environment.update(SSL_CERT_FILE=str(args.ca_bundle.resolve()),
                           REQUESTS_CA_BUNDLE=str(args.ca_bundle.resolve()))
    process = None
    try:
        with (work / "build.log").open("w") as log:
            process = subprocess.Popen(commands["build"], cwd=args.workspace, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True)
            for line in process.stdout:
                log.write(line)
                log.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            status = process.wait()
            if status:
                return status
            with (work / "output-paths.relative.txt").open("w") as output:
                status = subprocess.run(commands["query"], cwd=args.workspace, env=environment,
                                        stdout=output, stderr=log, check=False).returncode
            if status:
                return status
            output_paths(work)
        # Resolution evidence is retained even when graph inspection fails.
        with (work / "module-graph.json").open("w") as output, (work / "module-graph.stderr").open("w") as error:
            status = subprocess.run(commands["graph"], cwd=args.workspace, env=environment,
                                    stdout=output, stderr=error, check=False).returncode
        (work / "module-graph.exit-code").write_text(str(status) + "\n")
        return 0
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--repository-cache", type=Path)
    parser.add_argument("--disk-cache", type=Path)
    parser.add_argument("--remote-cache")
    parser.add_argument("--remote-cache-read-only", action="store_true")
    parser.add_argument("--target", default="@sonic_linux_kernel//:kernel_packages")
    parser.add_argument("--architecture", choices=tuple(TARGET_PLATFORMS),
                        help="target architecture (defaults to the Linux host); AMD64 can also build ARM64/ARMHF")
    parser.add_argument("--kernel-config", default="{}", help="JSON object of kernel Make variables; producer validates support")
    parser.add_argument("--bazel-arg", action="append", default=[], help="additional --registry=URL only")
    parser.add_argument("--module-override", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--java-trust-store", type=Path)
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument("--distdir", type=Path)
    args = parser.parse_args()
    try:
        args.host_architecture = native_architecture()
        args.architecture = target_architecture(args.host_architecture, args.architecture)
        args.kernel_config = kernel_configuration(args.architecture, args.kernel_config, args.host_architecture)
    except ValueError as error:
        parser.error(str(error))
    if os.geteuid() == 0:
        parser.error("kernel builds must run as an unprivileged user")
    if not (args.workspace / "MODULE.bazel").is_file():
        parser.error("--workspace must contain MODULE.bazel")
    workspace, work = args.workspace.resolve(), args.work_dir.resolve()
    if work in (Path("/"), Path("/tmp"), Path("/home"), Path.home()) or work == workspace or work in workspace.parents:
        parser.error("--work-dir must be a dedicated directory that does not contain the source workspace")
    for cache in (args.repository_cache, args.disk_cache):
        if cache and cache.resolve() in (Path("/"), Path("/tmp"), Path("/home"), Path.home(), workspace):
            parser.error("cache paths must name dedicated cache directories")
    if args.remote_cache:
        endpoint = urllib.parse.urlsplit(args.remote_cache)
        if endpoint.scheme not in ("http", "https", "grpc", "grpcs") or not endpoint.hostname:
            parser.error("remote cache must be an HTTP(S) or gRPC(S) endpoint")
        if endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment:
            parser.error("remote cache URL must not contain credentials, query parameters, or fragments")
    for path in (args.java_trust_store, args.ca_bundle):
        if path and not path.is_file():
            parser.error("trust inputs must name existing public certificate files")
    if args.ca_bundle and "PRIVATE KEY" in args.ca_bundle.read_text():
        parser.error("CA bundle must not contain a private key")
    if args.distdir and not args.distdir.is_dir():
        parser.error("--distdir must name a source archive directory")
    overrides = []
    for override in args.module_override:
        name, separator, directory = override.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9._-]*", name):
            parser.error("--module-override requires NAME=PATH")
        directory = Path(directory).resolve()
        if not (directory / "MODULE.bazel").is_file():
            parser.error("module override must contain MODULE.bazel")
        if directory == work or work in directory.parents:
            parser.error("--work-dir must not contain a module source override")
        overrides.append((name, directory))
    args.module_override = overrides
    args.workspace, args.work_dir = workspace, work
    args.work_dir.mkdir(parents=True, exist_ok=True)
    output = work / "output"
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        parser.error("Bazel output directory must be an ordinary directory")
    for cache in (args.repository_cache or work / "repository-cache", args.disk_cache):
        if cache:
            cache.mkdir(parents=True, exist_ok=True)
    try:
        # Validate options before a bootstrap download or source build.
        build_commands(args, work / "bootstrap" / "bazel")
    except ValueError as error:
        parser.error(str(error))
    registry_configuration = selected_bazelrc(args)
    if registry_configuration is not None:
        (work / "selected.bazelrc").write_text(registry_configuration)
    executable = bazel_binary(work / "bootstrap", args.ca_bundle, args.host_architecture).resolve()
    commands = build_commands(args, executable)
    config = HOST_ARCHITECTURES[args.host_architecture]
    (work / "invocation.json").write_text(json.dumps({
        "schema": 3, "target": args.target, "execution_strategy": EXECUTION_STRATEGY,
        "architecture": args.architecture, "host_architecture": args.host_architecture,
        "host_machine": os.uname().machine, "kernel_config": args.kernel_config,
        "uid": os.geteuid(), "bazel_version": BAZEL_VERSION,
        "bazel_url": config["bazel_url"], "bazel_sha256": config["bazel_sha256"],
        "workspace": str(workspace), "work_dir": str(work),
        "repository_cache": str((args.repository_cache or work / "repository-cache").resolve()),
        "remote_cache": args.remote_cache, "remote_cache_read_only": args.remote_cache_read_only,
        "disk_cache": str(args.disk_cache.resolve()) if args.disk_cache else None,
        "registry_overrides": args.bazel_arg,
    }, indent=2) + "\n")
    return run_build(args, commands)


if __name__ == "__main__":
    sys.exit(main())
