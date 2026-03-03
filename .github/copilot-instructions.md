# Copilot Instructions for sonic-linux-kernel

## Project Overview

sonic-linux-kernel contains the build scripts, patches, and kernel configuration for the SONiC Linux kernel. SONiC uses a single shared kernel across all platforms, preferring out-of-tree kernel modules for platform-specific hardware support. This repo manages kernel patches, configuration options, and the Debian kernel package build.

## Architecture

```
sonic-linux-kernel/
├── Makefile              # Top-level build — produces Debian kernel packages
├── patch/                # Kernel configuration and patch management
│   ├── kconfig-exclusions  # Kernel options to remove (reduce build time)
│   ├── kconfig-inclusions  # Kernel options to add
│   └── series            # Patch application order
├── patches-sonic/        # SONiC-specific kernel patches
├── patches-debian/       # Debian-specific kernel patches
├── config.local          # Local kernel config overrides
├── manage-config         # Script to manage kernel configuration
├── .azure-pipelines/     # Azure DevOps CI
└── azure-pipelines.yml   # CI pipeline
```

### Key Concepts
- **Single kernel for all platforms**: All SONiC platforms share the same kernel image
- **Out-of-tree modules**: Platform-specific drivers should be built as external modules
- **kconfig management**: Options excluded via `kconfig-exclusions`, included via `kconfig-inclusions`
- **Patch layers**: `patches-sonic/` for SONiC-specific, `patches-debian/` for Debian-derived patches

## Language & Style

- **Primary languages**: Shell scripts, Makefiles, C (kernel patches)
- **Kernel patches**: Must pass `checkpatch.pl` validation
- **Patch format**: Standard kernel patch format with upstream commit reference when applicable
- **Commit messages**: Include original upstream commit ID and message for backported patches

## Build Instructions

```bash
# Build kernel Debian packages
make DEST=<destination_path>

# If DEST is not set, packages stay in current directory
make
```

## Kernel Configuration Changes

### Excluding Options (speed up builds)
Add to `patch/kconfig-exclusions`:
```
CONFIG_REISERFS_FS
CONFIG_JFS_FS
CONFIG_XFS_FS
```

### Including Options
Add to `patch/kconfig-inclusions` with the desired value:
```
CONFIG_MY_DRIVER=m
```

## Patch Acceptance Criteria

Kernel patches are accepted under these conditions:
1. **Enable existing modules**: Enabling built-in kernel modules for SONiC use
2. **Upstream patches**: Backported patches that are already accepted upstream (include upstream commit ID)
3. **Common platform modules**: New modules needed by all/most platforms
4. **Platform-specific (exception)**: Only when impossible or very difficult to build out-of-tree

## PR Guidelines

- **Signed-off-by**: Required on all commits
- **CLA**: Sign Linux Foundation EasyCLA
- **checkpatch.pl**: All kernel patches MUST pass `checkpatch.pl`
- **Upstream first**: Prefer upstream patches — include the original commit hash
- **Justification**: Explain why a patch is needed and why it can't be done out-of-tree
- **CI**: Azure pipeline checks must pass

## Gotchas

- **Build time**: Full kernel build is slow — use kconfig-exclusions to remove unnecessary drivers
- **All platforms affected**: Kernel changes impact every SONiC platform
- **ABI stability**: Kernel ABI changes can break out-of-tree platform modules
- **Patch rebasing**: During kernel version upgrades, all patches must be ported — minimize the patch set
- **Config conflicts**: `kconfig-exclusions` and `kconfig-inclusions` can conflict — exclusions take priority
- **Out-of-tree preference**: Reviewers will push back on patches that could be out-of-tree modules
