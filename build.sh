#!/bin/bash

# Obtaining the Debian kernel source
wget https://launchpad.net/debian/+archive/primary/+files/linux_3.16.7-ckt11-1+deb8u3.dsc
wget https://launchpad.net/debian/+archive/primary/+files/linux_3.16.7-ckt11.orig.tar.xz
wget https://launchpad.net/debian/+archive/primary/+files/linux_3.16.7-ckt11-1+deb8u3.debian.tar.xz

dpkg-source -x linux_3.16.7-ckt11-1+deb8u3.dsc

cd linux-*
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
fakeroot make -f debian/rules.gen -j $(nproc) binary-arch_amd64_none
