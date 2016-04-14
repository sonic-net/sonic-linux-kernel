.ONESHELL:
SHELL = /bin/bash

MAIN_TARGET = linux-image-3.16.0-4-amd64_3.16.36-1+deb8u2_amd64.deb

VERSION = 3.16.36
SUBVERSION = 1+deb8u2

DSC_FILE = linux_${VERSION}-${SUBVERSION}.dsc
ORIG_FILE = linux_${VERSION}.orig.tar.xz
DEBIAN_FILE = linux_${VERSION}-${SUBVERSION}.debian.tar.xz
URL = http://security.debian.org/debian-security/pool/updates/main/l/linux
BUILD_DIR=linux-${VERSION}

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
	# Obtaining the Debian kernel source
	rm -rf $(BUILD_DIR)
	wget -O $(DSC_FILE) $(URL)/$(DSC_FILE)
	wget -O $(ORIG_FILE) $(URL)/$(ORIG_FILE)
	wget -O $(DEBIAN_FILE) $(URL)/$(DEBIAN_FILE)

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
	mv $* $(DEST)/
endif
