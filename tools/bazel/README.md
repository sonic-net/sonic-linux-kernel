# Bazel kernel packages and shared action cache

`//:kernel_packages` compiles the maintained SONiC/Debian kernel source and exports
five real Debian packages. `//:kernel_artifacts` remains an aggregate alias.

| Target | Packages | Purpose |
| --- | --- | --- |
| `//:runtime_deb` | `linux-image-<ABI>-unsigned` | Kernel image, config, modules, native device trees and maintained postinst hooks |
| `//:debug_deb` | `linux-image-<ABI>-dbg` | Matched vmlinux/module symbols, GNU build-ID links, patched sources and generated headers |
| `//:development_debs` | `linux-headers-<ABI>`, `linux-headers-6.12.41+deb13-common-sonic`, `linux-kbuild-6.12.41+deb13` | Configured headers, Module.symvers, BTF and native module build tools |
| `//:manifest` | `kernel-packages.json` | Schema3 package identity/size/hash, source/tool provenance, all created packages, and debug pairing |

The supported configurations are native Linux AMD64 and ARM64, VS, kernel version
`6.12.41-1`, and an unsigned boot image. The ABI is
`6.12.41+deb13-sonic-amd64` or `6.12.41+deb13-sonic-arm64`. Package filenames follow
`<package>_6.12.41-1_<architecture>.deb`; common headers use architecture `all`.
Bazel execution and target constraints must match. This is not cross compilation.

The kernel action uses checksum-pinned source archives, the repository's SONiC
patches/configuration, and the declared `sonic-build-tools` tool tree. It runs
`dpkg-buildpackage -b -us -uc -Pnodoc -j4`, the same maintained Debian binary recipe
used by the normal Make build, offline in a disposable chroot. Debian owns the
control metadata, dependencies, maintainer scripts, image, headers and kbuild
installation layouts. The existing Make contract is unchanged.

A Bazel-only patch adds a staging hook to Debian's `binary_image-dbg` recipe before
its maintained debhelper packaging sequence. It includes the actual patched
sources/generated headers under `/usr/src/debug/linux-<ABI>` and adds GNU build-ID
links. It verifies the boot image against debug vmlinux and every signed, stripped
runtime module against its original unsigned debug module. ARM64 raw Image bytes
must match reconstruction from the same debug vmlinux. No signed module is
modified to add a debuglink. Debian's debug package retains the real System.map;
the image package keeps Debian's normal System.map notice.

The manifest's `packages` inventory contains the five exported DEBs, including
Debian Package/Version/Architecture fields, kinds, hashes and sizes.
`created_packages` records every DEB produced by the Debian recipe before cleanup,
including whether it was exported. Metadata is read from actual packages with
`dpkg-deb`. Additional generated packages remain private to the action.

## Local build and cache reuse

Use Docker on a native Linux AMD64 or ARM64 host. The launcher selects a pinned
Bazel8.5.1 executable, worker image and matching Bazel platform. Sources and tools
are fetched by Bazel; the kernel action itself has no network access. Registry
resolution uses the canonical SONiC registry `main` plus the Bazel Central Registry.

```sh
python3 tools/bazel/build.py \
  --architecture amd64 \
  --workspace tools/bazel/cache-consumer \
  --module-override "sonic-linux-kernel=$PWD" \
  --work-dir /tmp/kernel-cold \
  --repository-cache /tmp/kernel-downloads \
  --remote-cache http://127.0.0.1:8080
```

Use `--architecture arm64` on a native ARM64 host. `--remote-cache` accepts the
shared HTTP cache URL for the deployment. A trusted cache can serve both
architectures; their source/configuration/tool/action identities differ.
`--disk-cache /path/to/persistent/cache` optionally retains a local Bazel cache.
A matching cache hit returns the original DEB bytes without compiling the kernel.
Independent cold builds may differ because Debian generates module-signing keys.

To prove remote reuse, start an empty loopback Bazel remote cache, build from one
consumer, then use a fresh output directory and read-only cache consumer. Do not
configure a disk cache for this proof:

```sh
python3 tools/bazel/build.py \
  --architecture amd64 --work-dir /tmp/kernel-hit \
  --workspace tools/bazel/cache-consumer \
  --module-override "sonic-linux-kernel=$PWD" \
  --repository-cache /tmp/kernel-downloads \
  --remote-cache http://127.0.0.1:8080 --remote-cache-read-only
python3 tools/bazel/verify_cache.py \
  --architecture amd64 --cold /tmp/kernel-cold --hit /tmp/kernel-hit \
  --artifacts /tmp/kernel-verified
```

`verify_cache.py` requires one successful local `SonicKernelBuild` action followed
by a `remote cache hit` with identical command/environment/platform/input/output
identities and byte-identical five DEBs plus manifest. Timing alone is insufficient.

Consumers should install the DEBs through apt/dpkg, retaining package records,
dependencies, initramfs hooks and future package upgrades. The debug DEB can be
installed only in a debug image; header and kbuild DEBs belong in module build
environments. Buildimage uses the four original Make packages for its ordinary
kernel/module flow and consumes the debug package when requested.

## Native CI and publication boundary

CI builds from source independently on `ubuntu-24.04` AMD64 and
`ubuntu-24.04-arm` ARM64, then proves a fresh read-only remote-cache hit. A fresh
native Trixie container installs all five DEBs with apt, verifies dpkg package
identities/configuration and ARM64 device trees, verifies the postinst-generated
ABI initramfs, compiles an external module using installed headers/kbuild, and checks its native ELF
architecture and vermagic. GDB then verifies installed build-ID symbol selection,
`start_kernel` and representative module source lines, and `struct task_struct`
without access to the compiler tree or debuginfod.

Successful CI publishes the five verified DEBs and a compact `validation.json`
with the checkout revision, architecture, package and manifest hashes, and
cache/install/debug check results. The collector checks those receipts and the
copied bytes; package validation remains with the build, cache and installed-package
checks above. Failed jobs publish only a fixed incomplete-validation summary.

The full `kernel-packages.json` remains an unchanged Bazel output for consumers
such as buildimage. CI keeps it, the generated module locks/graphs, raw logs,
profiles and cache diagnostics private instead of copying or sanitizing their
contents into the public artifact. The previous public `resolution.json` is no
longer produced. Public artifacts expire after seven days.

The five source-owned regression-test targets cover input identity, actual Debian
control fixtures, cache provenance, debug pairing, launcher architecture and the
public artifact allowlist. Registry CI runs these contracts on both architectures;
the source repository owns the full native kernel builds.
