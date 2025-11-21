.ONESHELL:
SHELL = /bin/bash
.SHELLFLAGS += -ex

KERNEL_VERSION ?= 6.12.41
KERNEL_ABISUFFIX ?= +deb13
KERNEL_SUBVERSION ?= 1
KERNEL_FEATURESET ?= sonic
CONFIGURED_ARCH ?= amd64
CONFIGURED_PLATFORM ?= vs
CROSS_BUILD_ENVIRON ?= n
SONIC_CONFIG_MAKE_JOBS ?= $(shell nproc)
KVERSION_SHORT := $(KERNEL_VERSION)$(KERNEL_ABISUFFIX)-$(KERNEL_FEATURESET)
ifeq ($(CONFIGURED_ARCH), armhf)
# Override kernel version for ARMHF as it uses arm MP (multi-platform) for short version
KVERSION ?= $(KVERSION_SHORT)-armmp
else
KVERSION ?= $(KVERSION_SHORT)-$(CONFIGURED_ARCH)
endif
SECURE_UPGRADE_MODE ?=
SECURE_UPGRADE_SIGNING_CERT ?=
SECURE_UPGRADE_KERNEL_CAFILE ?= $(SECURE_UPGRADE_SIGNING_CERT)

LINUX_HEADER_COMMON = linux-headers-$(KERNEL_VERSION)$(KERNEL_ABISUFFIX)-common-$(KERNEL_FEATURESET)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_all.deb
LINUX_HEADER_ARCH = linux-headers-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
LINUX_KBUILD = linux-kbuild-$(KERNEL_VERSION)$(KERNEL_ABISUFFIX)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
ifeq ($(CONFIGURED_ARCH), armhf)
	LINUX_IMAGE = linux-image-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
	KERNEL_FLAVOR_ARCH=armmp
else
	LINUX_IMAGE = linux-image-$(KVERSION)-unsigned_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
	KERNEL_FLAVOR_ARCH=$(CONFIGURED_ARCH)
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

	cp -vr ../config.local ../patches-sonic debian/
	if [[ -f debian/config.local/$(CONFIGURED_ARCH)/config.sonic-$(CONFIGURED_PLATFORM) ]]; then
		cp debian/config.local/$(CONFIGURED_ARCH)/config.sonic-$(CONFIGURED_PLATFORM) debian/config.local/$(CONFIGURED_ARCH)/config.sonic-platform-specific
	fi

	patch -p1 -i ../patches-debian/disable-secureboot-config-checks.patch

	# Enable secure boot configs if needed
	../manage-config $(CONFIGURED_ARCH) $(SECURE_UPGRADE_MODE) $(SECURE_UPGRADE_KERNEL_CAFILE)

	# re-generate debian packages and rules with SONiC customizations
	debian/bin/gencontrol.py

	# TODO(trixie): Make a way to verify that our configs are being set

ifeq ($(CROSS_BUILD_ENVIRON), y)
	dpkg-buildpackage -b -us -uc -a$(CONFIGURED_ARCH) -Pcross,nocheck,nodoc -j$(SONIC_CONFIG_MAKE_JOBS)
else
	dpkg-buildpackage -b -us -uc -Pnodoc -j$(SONIC_CONFIG_MAKE_JOBS)
endif
	popd

ifneq ($(DEST),)
	mv $(DERIVED_TARGETS) $* $(DEST)/
endif

$(addprefix $(DEST)/, $(DERIVED_TARGETS)): $(DEST)/% : $(DEST)/$(MAIN_TARGET)
