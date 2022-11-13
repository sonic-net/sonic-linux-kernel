.ONESHELL:
SHELL = /bin/bash
.SHELLFLAGS += -e

KERNEL_ABI_MINOR_VERSION = 2
KVERSION_SHORT ?= 5.10.0-18-$(KERNEL_ABI_MINOR_VERSION)
KVERSION ?= $(KVERSION_SHORT)-amd64
KERNEL_VERSION ?= 5.10.140
KERNEL_SUBVERSION ?= 1
kernel_procure_method ?= build
CONFIGURED_ARCH ?= amd64

LINUX_HEADER_COMMON = linux-headers-$(KVERSION_SHORT)-common_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_all.deb
LINUX_HEADER_AMD64 = linux-headers-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
ifeq ($(CONFIGURED_ARCH), armhf)
	LINUX_IMAGE = linux-image-$(KVERSION)_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
else
	LINUX_IMAGE = linux-image-$(KVERSION)-unsigned_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION)_$(CONFIGURED_ARCH).deb
endif

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
DEBIAN_FILE = linux_$(KERNEL_VERSION)-$(KERNEL_SUBVERSION).debian.tar.xz
ORIG_FILE = linux_$(KERNEL_VERSION).orig.tar.xz
BUILD_DIR=linux-$(KERNEL_VERSION)
SOURCE_FILE_BASE_URL="https://sonicstorage.blob.core.windows.net/debian-security/pool/updates/main/l/linux"

DSC_FILE_URL = "$(SOURCE_FILE_BASE_URL)/$(DSC_FILE)"
DEBIAN_FILE_URL = "$(SOURCE_FILE_BASE_URL)/$(DEBIAN_FILE)"
ORIG_FILE_URL = "$(SOURCE_FILE_BASE_URL)/$(ORIG_FILE)"
NON_UP_DIR = /tmp/non_upstream_patches

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
	fakeroot make -f debian/rules.gen DEB_HOST_ARCH=armhf setup_armhf_none_armmp
	fakeroot make -f debian/rules.gen DEB_HOST_ARCH=arm64 setup_arm64_none_arm64
	fakeroot make -f debian/rules.gen DEB_HOST_ARCH=amd64 setup_amd64_none_amd64

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

	rm -rf $(NON_UP_DIR)
	mkdir -p $(NON_UP_DIR)

	if [ ! -z ${EXTERNAL_KERNEL_PATCH_URL} ];  then
		wget $(EXTERNAL_KERNEL_PATCH_URL) -O patches.tar
		tar -xf patches.tar -C $(NON_UP_DIR)
	fi

	# Precedence is given for external URL
	if [ -z ${EXTERNAL_KERNEL_PATCH_URL} ] && [ x${INCLUDE_EXTERNAL_PATCH_TAR} == xy ]; then
		if [ -f "$(EXTERNAL_KERNEL_PATCH_TAR)" ]; then
			tar -xf $(EXTERNAL_KERNEL_PATCH_TAR) -C $(NON_UP_DIR)
		fi
	fi

	if [ -f "$(NON_UP_DIR)/series" ]; then
		echo "External Patches applied:"
		cat $(NON_UP_DIR)/series
		stg import -s $(NON_UP_DIR)/series
	fi

	# Optionally add/remove kernel options
	if [ -f ../manage-config ]; then
		../manage-config $(CONFIGURED_ARCH) $(CONFIGURED_PLATFORM)
	fi

ifeq ($(SECURE_UPGRADE_MOD),$(filter $(SECURE_UPGRADE_MOD),dev prod))
	if [ -f $(SECURE_UPGRADE_DEV_SIGNING_CERT) ]; then
		echo "Add secure boot support in kernel config file"
		cp ../patch/secure_boot_kernel_config.sh .
		cp $(SECURE_UPGRADE_DEV_SIGNING_CERT) debian/certs
		bash secure_boot_kernel_config.sh $(SECURE_UPGRADE_DEV_SIGNING_CERT)
	else
		echo "no certificate file exist, SECURE_UPGRADE_DEV_SIGNING_CERT=$(SECURE_UPGRADE_DEV_SIGNING_CERT)"
		exit 1
	fi

endif # ifeq ($(SECURE_UPGRADE_MOD),$(filter $(SECURE_UPGRADE_MOD),dev prod))

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

endif # building kernel
