[![Build Status](https://sonic-jenkins.westus2.cloudapp.azure.com/job/common/job/linux-kernel-build/badge/icon)](https://sonic-jenkins.westus2.cloudapp.azure.com/job/common/job/linux-kernel-build/)

# SONiC - Kernel

## Description
This repository contains the scripts and patches to build the kernel for SONiC. SONiC uses the same kernel for all platforms. We prefer to out-of-tree kernel platform modules. We accept kernel patches on following conditions:

- Existing kernel modules need to be enabled
- Existing kernel modules need to be patched and those patches are available in upstream
- New kernel modules which are common to all platforms
- Platform specific kernel modules which are impossible or very difficul to be built out of kernel tree

Platform specific kernel modules are expected to develop out-of-tree kernel modules, provide them in debian packages to be embeded into SONiC ONE image and installed on their platforms.

Usage:

    make DEST=<destination path>

If DEST is not set, package will stay in current directory

## Incremental Kernel Build

Normally, rebuilding the kernel involves removing the previously built kernel source tree, downloading the stock kernel, applying all the SONiC patches, and compiling the kernel and the kernel modules. In a development environment, you might want to skip downloading the kernel and applying all the patches, and just rebuild the kernel. This option is enabled by setting the value for DEFAULT_KERNEL_PROCURE_METHOD to "incremental" in the rules/config file.

Procedure to rebuild the kernel:

    make target/debs/stretch/linux-headers-4.9.0-9-2-common_4.9.168-1+deb9u5_all.deb-clean
    make target/debs/stretch/linux-headers-4.9.0-9-2-common_4.9.168-1+deb9u5_all.deb

You can then upload and install the kernel archive in the switch:
target/debs/stretch/linux-image-4.9.0-9-2-amd64_4.9.168-1+deb9u5_amd64.deb
