.ONESHELL:
SHELL = /bin/bash
.SHELLFLAGS += -e

KERNEL_ABI_MINOR_VERSION = 2
KVERSION_SHORT ?= 6.1.0-11-$(KERNEL_ABI_MINOR_VERSION)
KVERSION ?= $(KVERSION_SHORT)-amd64
KERNEL_VERSION ?= 6.1.38
KERNEL_SUBVERSION ?= 4
CONFIGURED_ARCH ?= amd64
CONFIGURED_PLATFORM ?= vs
SECURE_UPGRADE_MODE ?=
SECURE_UPGRADE_SIGNING_CERT ?=

LINUX_HEADER_COMMON = linux-headers-$(KVERSION_SHORT)-common_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_all.deb
LINUX_HEADER_AMD64 = linux-headers-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
ifeq ($(CONFIGURED_ARCH), armhf)
	LINUX_IMAGE = linux-image-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
else
	LINUX_IMAGE = linux-image-$(KVERSION)-unsigned_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
endif

MAIN_TARGET = $(LINUX_HEADER_COMMON)
DERIVED_TARGETS = $(LINUX_HEADER_AMD64) $(LINUX_IMAGE)

DSC_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).dsc
DEBIAN_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).debian.tar.xz
ORIG_FILE = linux_$(KERNEL_VERSION).orig.tar.xz
BUILD_DIR=linux-$(KERNEL_VERSION)
LINUX_SOURCE_BASE_URL="https://sonicstorage.blob.core.windows.net/debian-security/pool/updates/main/l/linux"

DSC_FILE_URL = "$(LINUX_SOURCE_BASE_URL)/$(DSC_FILE)"
DEBIAN_FILE_URL = "$(LINUX_SOURCE_BASE_URL)/$(DEBIAN_FILE)"
ORIG_FILE_URL = "$(LINUX_SOURCE_BASE_URL)/$(ORIG_FILE)"
NON_UP_DIR = /tmp/non_upstream_patches

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
	# Include any non upstream patches
	rm -rf $(NON_UP_DIR)
	mkdir -p $(NON_UP_DIR)

	if [ x${INCLUDE_EXTERNAL_PATCHES} == xy ]; then
		if [ ! -z ${EXTERNAL_KERNEL_PATCH_URL} ]; then
			wget $(EXTERNAL_KERNEL_PATCH_URL) -O patches.tar
			tar -xf patches.tar -C $(NON_UP_DIR)
		else
			if [ -d "$(EXTERNAL_KERNEL_PATCH_LOC)" ]; then
				cp -r $(EXTERNAL_KERNEL_PATCH_LOC)/* $(NON_UP_DIR)/
			fi
		fi
	fi

	if [ -f "$(NON_UP_DIR)/external-changes.patch" ]; then
		cat $(NON_UP_DIR)/external-changes.patch
		git stash -- patch/
		git apply $(NON_UP_DIR)/external-changes.patch
	fi

	if [ -d "$(NON_UP_DIR)/patches" ]; then
		echo "Copy the non upstream patches"
		cp $(NON_UP_DIR)/patches/*.patch patch/
	fi

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
	DEB_HOST_ARCH=armhf fakeroot make -f debian/rules.gen setup_armhf_none_armmp
	DEB_HOST_ARCH=arm64 fakeroot make -f debian/rules.gen setup_arm64_none_arm64
	DEB_HOST_ARCH=amd64 fakeroot make -f debian/rules.gen setup_amd64_none_amd64

	# Applying patches and configuration changes
	git add debian/build/build_armhf_none_armmp/.config -f
	git add debian/build/build_arm64_none_arm64/.config -f
	git add debian/build/build_amd64_none_amd64/.config -f
	git add debian/config.defines.dump -f
	git add debian/control -f
	git add debian/rules.gen -f
	git add debian/tests/control -f
	git add debian/*.maintscript -f
	git add debian/*.bug-presubj -f
	git commit -m "unmodified debian source"

	# Learning new git repo head (above commit) by calling stg repair.
	stg repair
	stg import -s ../patch/series

	# Optionally add/remove kernel options
	if [ -f ../manage-config ]; then
		../manage-config $(CONFIGURED_ARCH) $(CONFIGURED_PLATFORM) $(SECURE_UPGRADE_MODE) $(SECURE_UPGRADE_SIGNING_CERT)
	fi

	# Building a custom kernel from Debian kernel source
	ARCH=$(CONFIGURED_ARCH) DEB_HOST_ARCH=$(CONFIGURED_ARCH) DEB_BUILD_PROFILES=nodoc fakeroot make -f debian/rules -j $(shell nproc) binary-indep
ifeq ($(CONFIGURED_ARCH), armhf)
	ARCH=$(CONFIGURED_ARCH) DEB_HOST_ARCH=$(CONFIGURED_ARCH) fakeroot make -f debian/rules.gen -j $(shell nproc) binary-arch_$(CONFIGURED_ARCH)_none_armmp
else
	ARCH=$(CONFIGURED_ARCH) DEB_HOST_ARCH=$(CONFIGURED_ARCH) fakeroot make -f debian/rules.gen -j $(shell nproc) binary-arch_$(CONFIGURED_ARCH)_none_$(CONFIGURED_ARCH)
endif
	popd

ifneq ($(DEST),)
	mv $(DERIVED_TARGETS) $* $(DEST)/
endif

$(addprefix $(DEST)/, $(DERIVED_TARGETS)): $(DEST)/% : $(DEST)/$(MAIN_TARGET)
