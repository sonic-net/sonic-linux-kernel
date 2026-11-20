
# SONiC - Kernel

## Build Status

[![Master Branch](https://dev.azure.com/mssonic/build/_apis/build/status%2FAzure.sonic-linux-kernel?branchName=master&label=Master)](https://dev.azure.com/mssonic/build/_build/latest?definitionId=13&branchName=master)
[![202211 Branch](https://dev.azure.com/mssonic/build/_apis/build/status%2FAzure.sonic-linux-kernel?branchName=202211&label=202211)](https://dev.azure.com/mssonic/build/_build/latest?definitionId=13&branchName=202211)
[![202205 Branch](https://dev.azure.com/mssonic/build/_apis/build/status%2FAzure.sonic-linux-kernel?branchName=202205&label=202205)](https://dev.azure.com/mssonic/build/_build/latest?definitionId=13&branchName=202205)

## Description
This repository contains the scripts and patches to build the kernel for SONiC. SONiC uses the same kernel for all amd64 platforms (armhf and arm64 platforms currently have platform-specific kernels). We accept kernel patches on following conditions:

- Existing kernel modules need to be enabled.
- Existing kernel modules/code need to be patched, or new kernel modules which are common to all platforms. In addition:
  - Those patches are available in upstream.
    - Please include the original upstream commit ID and message in the patch or the PR description. This allows the maintainer to remove upstream patches during the kernel upgrade.
  - Those patches will be upstreamed or are in the process of being upstreamed.
    - If being upstreamed, please include a link to the mail thread in the patch or PR description.
  - Those patches don't make sense to be upstreamed, either because they're applicable only within SONiC or they would break some general use case.
    - DTS (device tree source) files are acceptable here, as the board description in the files may be applicable to SONiC software only.
    - For patches added into this folder, please explain why the patch cannot be upstreamed.
- Platform specific kernel modules which are impossible or very difficult to be built out of kernel tree.

Platform specific kernel modules are expected to develop out-of-tree kernel modules, provide them in debian packages to be embedded into SONiC image and installed on their platforms.

Patches must be placed into one of three folders:

- `backports`: For patches that have already been upstreamed into the mainline Linux kernel
- `toBeUpstreamed`: For patches that will be upstreamed or are in the process of being upstreamed
- `sonicOnly`: For patches that will not be upstreamed
  - For organizational purposes, this folder will contain multiple folders, one per platform/vendor. 

For all patches, please ensure you have run the patch with `scripts/checkpatch.pl` (available within the kernel source code).

Usage:

    make DEST=<destination path>

If DEST is not set, package will stay in current directory.

## Kernel Configuration Changes

The Debian kernel used with SONiC includes almost all available hardware that can be found on a system using Linux. This increases considerably the time needed to build the kernel Debian image. Since there are many drivers, protocols or filesystems which will never be used on a switch, there is a simple mechanism to remove kernel options which does not require creating a kernel configuration patch. The options which need to be excluded from the kernel are simply listed in a flat text file, `patch/kconfig-exclusions`.

Example:

    CONFIG_REISERFS_FS
    CONFIG_JFS_FS
    CONFIG_XFS_FS

Similarly, there is a mechanism to include some kernel options by listing these options into the flat text file `patch/kconfig-inclusions`.

Example:

    CONFIG_INT340X_THERMAL=m
    CONFIG_LOG_BUF_SHIFT=18

If the files `patch/kconfig-exclusions` and `patch/kconfig-inclusions` exist, they will be processed after all the kernel patches described in the patch directory have been applied, exclusions being done before inclusions.

Also, the final kernel configuration will be checked to verify that:
- all options asked to be excluded are effectively not present in the kernel,
- and all options asked to be included are effectively present in the kernel, using the exact type (module or built-in) or string or number.

## Kernel Configuration Difference during Upgrades

During Kernel Upgrades, the maintainer must update the configuration diff in the wiki of this repo. This acts as a guide for keeping track of configuration changes between Kernel upgrades. Applies for minor version upgrades as well

The diff is saved as kconfig-diff-{platform}-{arch}.rst under the artifacts of Azure.sonic-linux-kernel job runs.
