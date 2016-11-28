.ONESHELL:
SHELL = /bin/bash

MAIN_TARGET = linux-image-3.16.0-4-amd64_3.16.7-ckt11-2+acs8u2_amd64.deb

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
	# Obtaining the Debian kernel source
	rm -rf linux-3.16.7-ckt11
	wget -O linux_3.16.7-ckt11-1+deb8u3.dsc https://launchpad.net/debian/+archive/primary/+files/linux_3.16.7-ckt11-1+deb8u3.dsc
	wget -O linux_3.16.7-ckt11.orig.tar.xz https://launchpad.net/debian/+archive/primary/+files/linux_3.16.7-ckt11.orig.tar.xz
	wget -O linux_3.16.7-ckt11-1+deb8u3.debian.tar.xz https://launchpad.net/debian/+archive/primary/+files/linux_3.16.7-ckt11-1+deb8u3.debian.tar.xz

	dpkg-source -x linux_3.16.7-ckt11-1+deb8u3.dsc

	pushd linux-3.16.7-ckt11
	# patch debian changelog and update kernel package version
	patch -p0 < ../patch/changelog.patch

	# re-generate debian/rules.gen
	debian/bin/gencontrol.py

	# generate linux build file for amd64_none_amd64
	fakeroot make -f debian/rules.gen setup_amd64_none_amd64

	# Applying patches and configuration changes
	git init
	git add .
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
