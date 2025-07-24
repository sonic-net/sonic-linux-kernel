.ONESHELL:
SHELL = /bin/bash
.SHELLFLAGS += -ex

KERNEL_VERSION ?= 6.12.30
KERNEL_SUBVERSION ?= 1
KERNEL_FEATURESET ?= sonic
CONFIGURED_ARCH ?= amd64
CONFIGURED_PLATFORM ?= vs
KVERSION ?= $(KERNEL_VERSION)-$(KERNEL_FEATURESET)-$(CONFIGURED_ARCH)
SECURE_UPGRADE_MODE ?=
SECURE_UPGRADE_SIGNING_CERT ?=

LINUX_HEADER_COMMON = linux-headers-$(KERNEL_VERSION)-common-$(KERNEL_FEATURESET)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_all.deb
LINUX_HEADER_ARCH = linux-headers-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
LINUX_KBUILD = linux-kbuild-$(KERNEL_VERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
ifeq ($(CONFIGURED_ARCH), armhf)
	LINUX_IMAGE = linux-image-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
else
	LINUX_IMAGE = linux-image-$(KVERSION)-unsigned_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
endif

MAIN_TARGET = $(LINUX_HEADER_COMMON)
DERIVED_TARGETS = $(LINUX_HEADER_ARCH) $(LINUX_IMAGE) $(LINUX_KBUILD)

DSC_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).dsc
DEBIAN_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).debian.tar.xz
ORIG_FILE = linux_$(KERNEL_VERSION).orig.tar.xz
BUILD_DIR=linux-$(KERNEL_VERSION)
LINUX_SOURCE_BASE_URL=https://packages.trafficmanager.net/public/debian-security/pool/updates/main/l/linux

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
	#git init
	#git add -f *
	#git commit -qm "check in all loose files"

	cp -vr ../config.local ../patches-sonic debian/
	#git add -f debian/config.local debian/patches-sonic
	#git commit -qm "Add SONiC configuration"

	# patching anything that could affect following configuration generation.
	#stg init
	#stg import -s ../patch/preconfig/series

	# re-generate debian/rules.gen, requires kernel-wedge
	debian/bin/gencontrol.py

	# Learning new git repo head (above commit) by calling stg repair.
	#stg repair
	#stg import -s ../patch/series

	# Optionally add/remove kernel options
	# TODO(trixie): Make a way to verify that our configs are being set
	# if [ -f ../manage-config ]; then
	# 	../manage-config $(CONFIGURED_ARCH) $(CONFIGURED_PLATFORM) $(SECURE_UPGRADE_MODE) $(SECURE_UPGRADE_SIGNING_CERT)
	# fi

	# Building a custom kernel from Debian kernel source
	ARCH=$(CONFIGURED_ARCH) DEB_HOST_ARCH=$(CONFIGURED_ARCH) DEB_BUILD_PROFILES=nodoc fakeroot make -f debian/rules -j $(shell nproc) binary-indep
	ARCH=$(CONFIGURED_ARCH) DEB_HOST_ARCH=$(CONFIGURED_ARCH) fakeroot make -f debian/rules.gen -j $(shell nproc) binary-arch_$(CONFIGURED_ARCH)_sonic
	ARCH=$(CONFIGURED_ARCH) DEB_HOST_ARCH=$(CONFIGURED_ARCH) fakeroot make -f debian/rules.gen -j $(shell nproc) binary-arch_$(CONFIGURED_ARCH)_kbuild
	popd

ifneq ($(DEST),)
	mv $(DERIVED_TARGETS) $* $(DEST)/
endif

$(addprefix $(DEST)/, $(DERIVED_TARGETS)): $(DEST)/% : $(DEST)/$(MAIN_TARGET)
