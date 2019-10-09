.ONESHELL:
SHELL = /bin/bash
.SHELLFLAGS += -e

KERNEL_ABI_MINOR_VERSION = 2
KVERSION_SHORT ?= 4.9.0-9-$(KERNEL_ABI_MINOR_VERSION)
KVERSION ?= $(KVERSION_SHORT)-amd64
KERNEL_VERSION ?= 4.9.168
KERNEL_SUBVERSION ?= 1+deb9u5
kernel_procure_method ?= build
CONFIGURED_ARCH ?= amd64

LINUX_HEADER_COMMON = linux-headers-$(KVERSION_SHORT)-common_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_all.deb
LINUX_HEADER_AMD64 = linux-headers-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
LINUX_IMAGE = linux-image-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb

MAIN_TARGET = $(LINUX_HEADER_COMMON)
DERIVED_TARGETS = $(LINUX_HEADER_AMD64) $(LINUX_IMAGE)

ifneq ($(kernel_procure_method), build)
# Downloading kernel

# TBD, need upload the new kernel packages
LINUX_HEADER_COMMON_URL = "https://sonicstorage.blob.core.windows.net/packages/kernel-public/linux-headers-$(KVERSION_SHORT)-common_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_all.deb?sv=2015-04-05&sr=b&sig=JmF0asLzRh6btfK4xxfVqX%2F5ylqaY4wLkMb5JwBJOb8%3D&se=2128-12-23T19%3A05%3A28Z&sp=r"

LINUX_HEADER_AMD64_URL = "https://sonicstorage.blob.core.windows.net/packages/kernel-public/linux-headers-$(KVERSION_SHORT)-amd64_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_amd64.deb?sv=2015-04-05&sr=b&sig=%2FD9a178J4L%2FN3Fi2uX%2FWJaddpYOZqGmQL4WAC7A7rbA%3D&se=2128-12-23T19%3A06%3A13Z&sp=r"

LINUX_IMAGE_URL = "https://sonicstorage.blob.core.windows.net/packages/kernel-public/linux-image-$(KVERSION_SHORT)-amd64_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_amd64.deb?sv=2015-04-05&sr=b&sig=oRGGO9xJ6jmF31KGy%2BwoqEYMuTfCDcfILKIJbbaRFkU%3D&se=2128-12-23T19%3A06%3A47Z&sp=r"

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
	# Obtaining the Debian kernel packages
	rm -rf $(BUILD_DIR)
	wget --no-use-server-timestamps -O $(LINUX_HEADER_COMMON) $(LINUX_HEADER_COMMON_URL)
	wget --no-use-server-timestamps -O $(LINUX_HEADER_AMD64) $(LINUX_HEADER_AMD64_URL)
	wget --no-use-server-timestamps -O $(LINUX_IMAGE) $(LINUX_IMAGE_URL)

ifneq ($(DEST),)
	mv $(DERIVED_TARGETS) $* $(DEST)/
endif

$(addprefix $(DEST)/, $(DERIVED_TARGETS)): $(DEST)/% : $(DEST)/$(MAIN_TARGET)

else
# Building kernel

DSC_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).dsc
ORIG_FILE = linux_$(KERNEL_VERSION).orig.tar.xz
DEBIAN_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).debian.tar.xz
BUILD_DIR=linux-$(KERNEL_VERSION)

DSC_FILE_URL = "https://sonicstorage.blob.core.windows.net/packages/kernel-source/linux_4.9.168-1+deb9u5.dsc?sv=2015-04-05&sr=b&sig=ZcTpvRltWLWwnJn3vQ%2BgTP1dVF6QSinOCJ1FSuyiogU%3D&se=2033-04-28T06%3A14%3A30Z&sp=r"
DEBIAN_FILE_URL = "https://sonicstorage.blob.core.windows.net/packages/kernel-source/linux_4.9.168-1+deb9u5.debian.tar.xz?sv=2015-04-05&sr=b&sig=koCXHDvmY39smVGcI3cPJrBDZMmdpKiLkyJPfMdNPwU%3D&se=2033-04-28T06%3A14%3A51Z&sp=r"
ORIG_FILE_URL = "https://sonicstorage.blob.core.windows.net/packages/kernel-source/linux_4.9.168.orig.tar.xz?sv=2015-04-05&sr=b&sig=ArvSGD3N46WGh%2BTYF8J1JgdT9x0BrFu4JhSuyyr3nNw%3D&se=2033-04-09T01%3A00%3A47Z&sp=r"

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
	# Obtaining the Debian kernel source
	rm -rf $(BUILD_DIR)
	wget -O $(DSC_FILE) $(DSC_FILE_URL)
	wget -O $(ORIG_FILE) $(ORIG_FILE_URL)
	wget -O $(DEBIAN_FILE) $(DEBIAN_FILE_URL)

	dpkg-source -x $(DSC_FILE)

	pushd $(BUILD_DIR)
	git init
	git add -f *
	git commit -qm "check in all loose files and diffs"

	# patching anything that could affect following configuration generation.
	stg init
	stg import -s ../patch/preconfig/series

	# re-generate debian/rules.gen, requires kernel-wedge
	debian/bin/gencontrol.py

	# generate linux build file for amd64_none_amd64
ifneq (,$(filter $(CONFIGURED_ARCH), armhf arm64))
	fakeroot make -f debian/rules.gen setup_$(CONFIGURED_ARCH)_none
else
	fakeroot make -f debian/rules.gen setup_$(CONFIGURED_ARCH)_none_$(CONFIGURED_ARCH)
endif

	# Applying patches and configuration changes
ifeq ($(CONFIGURED_ARCH), armhf)
    	# ARM32 (ARMHF) target does kconfig for both 32bit and PAE mode
	git add debian/build/build_$(CONFIGURED_ARCH)_none_armmp/.config -f
	git add debian/build/build_$(CONFIGURED_ARCH)_none_armmp-lpae/.config -f
else
	git add debian/build/build_$(CONFIGURED_ARCH)_none_$(CONFIGURED_ARCH)/.config -f
endif
	git add debian/config.defines.dump -f
	git add debian/control -f
	git add debian/rules.gen -f
	git add debian/tests/control -f
	git commit -m "unmodified debian source"

	# Learning new git repo head (above commit) by calling stg repair.
	stg repair
ifneq (,$(filter $(CONFIGURED_ARCH), armhf arm64))
	stg import -s ../patch/series_$(CONFIGURED_ARCH)
else
	stg import -s ../patch/series
endif

	# Building a custom kernel from Debian kernel source
	DO_DOCS=False fakeroot make -f debian/rules -j $(shell nproc) binary-indep
	fakeroot make -f debian/rules.gen -j $(shell nproc) binary-arch_$(CONFIGURED_ARCH)_none
	popd

ifneq ($(DEST),)
	mv $(DERIVED_TARGETS) $* $(DEST)/
endif

$(addprefix $(DEST)/, $(DERIVED_TARGETS)): $(DEST)/% : $(DEST)/$(MAIN_TARGET)

endif # building kernel
