.ONESHELL:
SHELL = /bin/bash

KVERSION_SHORT ?= 3.16.0-4
KVERSION ?= $(KVERSION_SHORT)-amd64
KERNEL_VERSION ?= 3.16.36
KERNEL_SUBVERSION ?= 1+deb8u2

MAIN_TARGET = linux-headers-$(KVERSION_SHORT)-common_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_amd64.deb
DERIVED_TARGETS = linux-headers-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_amd64.deb \
                 linux-image-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_amd64.deb

DSC_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).dsc
ORIG_FILE = linux_$(KERNEL_VERSION).orig.tar.xz
DEBIAN_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).debian.tar.xz
URL = http://security.debian.org/debian-security/pool/updates/main/l/linux
BUILD_DIR=linux-$(KERNEL_VERSION)

DSC_FILE_URL = "https://sonicstorage.blob.core.windows.net/packages/linux_3.16.36-1+deb8u2.dsc?sv=2015-04-05&sr=b&sig=9wBtvznS4Ih4JTT2%2FWZzlnaW0XY4iCQlzIhdaHlA3dw%3D&se=2030-11-07T19%3A41%3A19Z&sp=r"
DEBIAN_FILE_URL = "https://sonicstorage.blob.core.windows.net/packages/linux_3.16.36-1+deb8u2.debian.tar.xz?sv=2015-04-05&sr=b&sig=wlcTymPDhkX8HOsM5im7789b76%2BewwCILOFUpFZYBvk%3D&se=2030-11-07T19%3A40%3A30Z&sp=r"
ORIG_FILE_URL = "https://sonicstorage.blob.core.windows.net/packages/linux_3.16.36.orig.tar.xz?sv=2015-04-05&sr=b&sig=ez%2Fv4ci4ADs7gnoflCr%2BrCMAK0e1WPN8%2F68sw33f9%2Bc%3D&se=2030-11-07T19%3A40%3A52Z&sp=r"

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
	# Obtaining the Debian kernel source
	rm -rf $(BUILD_DIR)
	wget -O $(DSC_FILE) $(DSC_FILE_URL)
	wget -O $(ORIG_FILE) $(ORIG_FILE_URL)
	wget -O $(DEBIAN_FILE) $(DEBIAN_FILE_URL)

	dpkg-source -x $(DSC_FILE)

	pushd $(BUILD_DIR)
	# patch debian changelog and update kernel package version
	patch -p0 < ../patch/changelog.patch

	# re-generate debian/rules.gen, requires kernel-wedge
	debian/bin/gencontrol.py

	# generate linux build file for amd64_none_amd64
	fakeroot make -f debian/rules.gen setup_amd64_none_amd64

	# Applying patches and configuration changes
	git init
	git add -f *
	git add debian/build/build_amd64_none_amd64/.config -f
	git commit -m "unmodified debian source"
	stg init
	stg import -s ../patch/series

	# Building a custom kernel from Debian kernel source
	fakeroot make -f debian/rules.gen -j $(shell nproc) binary-arch_amd64_none
	popd

ifneq ($(DEST),)
	mv $(DERIVED_TARGETS) $* $(DEST)/
endif

$(addprefix $(DEST)/, $(DERIVED_TARGETS)): $(DEST)/% : $(DEST)/$(MAIN_TARGET)
