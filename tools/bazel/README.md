# Bazel kernel packages and shared action cache

`//:kernel_packages` compiles the maintained SONiC/Debian kernel source and exports
five real Debian packages. `//:kernel_artifacts` remains an aggregate alias.

| Target | Packages | Purpose |
| --- | --- | --- |
| `//:runtime_deb` | `linux-image-<ABI>-unsigned` | Kernel image, config, modules, native device trees and maintained postinst hooks |
| `//:debug_deb` | `linux-image-<ABI>-dbg` | Matched vmlinux/module symbols, GNU build-ID links, patched sources and generated headers |
| `//:development_debs` | `linux-headers-<ABI>`, `linux-headers-6.12.41+deb13-common-sonic`, `linux-kbuild-6.12.41+deb13` | Configured headers, Module.symvers, BTF and native module build tools |
| `//:manifest` | `kernel-packages.json` | Schema4 host/target identity, package size/hash, source/tool provenance, all created packages, and debug pairing |

The supported host/target pairs are:

| Linux build host | Kernel target | Build mode |
| --- | --- | --- |
| AMD64 | AMD64 | Native |
| ARM64 | ARM64 | Native |
| AMD64 | ARM64 | Cross compilation |
| AMD64 | ARMHF | Cross compilation |

All use the VS configuration, kernel version `6.12.41-1`, and an unsigned boot
image. The ABI suffix is `sonic-amd64`, `sonic-arm64`, or `sonic-armmp` for ARMHF.
Package filenames follow `<package>_6.12.41-1_<architecture>.deb`; common headers
use architecture `all`. Debian's ARMHF image package has no `-unsigned` suffix.
Native ARMHF builds and ARM64-host cross builds are not supported.

The kernel action uses checksum-pinned source archives, the repository's SONiC
patches/configuration, and the declared `sonic-build-infra` build-tools runtime. It runs
`dpkg-buildpackage`, using the same maintained Debian binary recipe
used by the normal Make build, as an ordinary user in Bazel's `linux-sandbox`.
The action neither launches Docker nor uses root, chroot or device creation.
The relocated compiler, Python, Perl, dpkg and debhelper tools use their declared
libraries and data. One AMD64 tool runtime serves native AMD64 and both cross
builds. It contains both target-prefixed compiler/binutils sets and target libraries
in their Debian multiarch directories; each compiler and pkg-config wrapper selects
its target's headers and libraries. Debian receives the target architecture. No target
program or QEMU process is needed to compile the kernel. Debian owns the
control metadata, dependencies, maintainer scripts, image, headers and kbuild
installation layouts. The existing Make contract is unchanged.

Bazel statically links its bootstrap executables, including package-metadata
`gawk`, with `--host_features=fully_static_link`, so they do not depend on the host
glibc. The kernel recipe and installed kbuild tools retain their normal link flags.

Bazel-only adaptations select the declared tools in Debian's build rules and
give DWARF source paths the stable `/usr/src/debug/linux-<ABI>` prefix. Programs
compiled with `HOSTCC` use the private tool runtime while building. Before Debian
strips and packages installed kbuild tools, their ordinary Debian ELF interpreter
is restored and private runtime search paths are removed. Kernel modules and their
signatures are untouched. Debug packages retain the kernel's 32-bit compatibility
vDSOs at their ABI-specific paths; shipped kbuild executables must match the
package target architecture.

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

## Build configuration

The public string setting `//tools/bazel:kernel_config` accepts a JSON object of
kernel Make variables. `tools/bazel/build.py --kernel-config JSON` applies the same
configuration to its build and output query, using the target's repository label.
For example, a buildimage consumer passes `--kernel-config
'{"CONFIGURED_ARCH":"arm64","CONFIGURED_PLATFORM":"vs","BLDENV":"trixie"}'`
on its native ARM64 runner. The launcher expands omitted values to the defaults
above; empty false flags become `n` and an empty signing mode becomes `no_sign`.
The target (`--architecture`) must match `CONFIGURED_ARCH`. The execution host is
selected independently: omitted `CROSS_BUILD_ENVIRON` becomes `y` for AMD64 builds
targeting ARM64/ARMHF and `n` for native builds. The producer derives the same
default from its execution platform when invoked directly with Bazel.

The producer validates the requested configuration during Bazel analysis.
Unsupported values, such as `SECURE_UPGRADE_MODE=prod`, an architecture that
differs from the selected target, contradictory `CROSS_BUILD_ENVIRON`, custom
profiles, or external patches, produce `Unsupported kernel configuration`
before kernel compilation. Consumers can select Bazel directly and forward their
configuration without maintaining a second capability allowlist or falling back
to Make. ARMHF uses the `armmp` kernel ABI suffix.

The normalized configuration is a declared action input and is recorded as
`kernel_config` in both `invocation.json` and `kernel-packages.json`. Cache proof
checks require these records to agree in both consumers. The source timestamp
remains pinned to Debian's changelog (`SOURCE_DATE_EPOCH=1754969284`); unknown Make
keys are rejected rather than silently ignored.

## Shared build tools

The root build and fresh cache consumer select
`sonic-build-infra@0.0.19-46f471185c80ac5fd758f995195a428a43e3831e`
from the maintained SONiC registry `main`. This landed runtime provides native
AMD64 tools and both ARM cross compilers. Both consumers use their committed
`.bazelrc` registry settings, so source and cache validation resolve the same
published recipe without a temporary registry override.

## Local build and cache reuse

Use a native Linux AMD64 or ARM64 host with unprivileged user namespaces enabled.
Run as your ordinary user, with Python 3.10 or newer, a POSIX shell and GNU `env` available.
The launcher selects the checksum-pinned Bazel8.5.1 executable for the host,
independently of the requested target platform.
The kernel action explicitly requires `linux-sandbox`; an unavailable sandbox is
an error rather than a fallback to unsandboxed compilation. Sources and tools
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

`--architecture` selects the kernel target; omitting it selects the native host.
On an AMD64 host, use `--architecture arm64` or `--architecture armhf` to cross
compile. For example:

```sh
python3 tools/bazel/build.py \
  --architecture armhf \
  --workspace tools/bazel/cache-consumer \
  --module-override "sonic-linux-kernel=$PWD" \
  --work-dir /tmp/kernel-armhf-cold \
  --repository-cache /tmp/kernel-downloads \
  --remote-cache http://127.0.0.1:8080
```

Direct Bazel callers can select `--config=cross_arm64` or
`--config=cross_armhf`; existing `--config=arm64` retains native ARM64 execution.
`--remote-cache` accepts the shared HTTP cache URL for the deployment. A trusted
cache can serve all supported pairs; their declared host runtime, target
compiler, source and configuration identities keep incompatible actions separate.
The AMD64 runtime is shared across all three targets, so a change to either cross
toolchain also changes that shared runtime's inputs and invalidates its consumers.
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

For cross-build proof, pass the same `--architecture arm64` or `armhf` to both
builds and add `--host-architecture amd64` to `verify_cache.py`. The verifier checks
both invocation and manifest host/target identities, so a native ARM64 build
cannot be reported as an AMD64 cross build.

`verify_cache.py` requires one successful `linux-sandbox` `SonicKernelBuild` action followed
by a `remote cache hit` with identical command/environment/platform/input/output
identities and byte-identical five DEBs plus manifest. Timing alone is insufficient.

Consumers should install the DEBs through apt/dpkg, retaining package records,
dependencies, initramfs hooks and future package upgrades. The debug DEB can be
installed only in a debug image; header and kbuild DEBs belong in module build
environments. Buildimage uses the four original Make packages for its ordinary
kernel/module flow and consumes the debug package when requested.

## CI and publication boundary

CI builds each supported host/target pair independently: native AMD64 and both
cross builds run on `ubuntu-22.04`, and native ARM64 runs on `ubuntu-22.04-arm`.
Each job starts an empty remote cache and proves a fresh read-only cache hit. The
existing required `Kernel input checks` status is a final aggregate: it passes
only when the unit, all source/cache, and all installation jobs succeed. Failed,
cancelled, or skipped prerequisites cannot make that status pass. The
source jobs avoid Ubuntu 24.04's default AppArmor namespace restriction and
check unprivileged namespace support before building; the declared compiler and
packaging runtime remain Debian Trixie on both architectures. Kernel
compilation and the checksum-pinned native loopback cache run without root or
Docker. Runner disk preparation is separate from the build.

Separate installation jobs install the exact source job's DEBs with apt in
disposable Trixie containers. AMD64 and ARM64 package checks run natively,
including native ARM64 checks for the AMD64-cross-built ARM64 packages. ARMHF
installation checks explicitly use QEMU ARM emulation; the AMD64 kernel source
build and cache proof do not use emulation. It verifies dpkg package
identities/configuration and ARM device trees, verifies the postinst-generated
ABI initramfs, compiles an external module using installed headers/kbuild, and checks its target ELF
architecture and vermagic. GDB then verifies installed build-ID symbol selection,
`start_kernel` and representative module source lines, and `struct task_struct`
without access to the compiler tree or debuginfod.

The source job publishes the five verified DEBs and a compact `validation.json`
with the checkout revision, host and target architectures, package and manifest hashes, and
cache check results. The installation job adds install/debug results to its
validation summary. The collector checks those receipts and the
copied bytes; package validation remains with the build, cache and installed-package
checks above. Failed jobs publish only a fixed incomplete-validation summary.

The full `kernel-packages.json` remains an unchanged Bazel output for consumers
such as buildimage. The public artifacts omit that full manifest, generated
module locks/graphs, profiles and cache diagnostics. Compilation output is
visible in Actions logs. Public artifacts expire after seven days.

The five source-owned regression-test targets cover input identity, actual Debian
control fixtures, cache provenance, debug pairing, launcher architecture and the
public artifact allowlist. Registry CI runs these contracts on both architectures;
the source repository owns full native and cross kernel builds and cache verification.
