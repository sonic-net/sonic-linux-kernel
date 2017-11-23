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
