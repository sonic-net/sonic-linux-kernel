#!/bin/bash

# Note: this script was created because there is a problem when changing the kernel config
# values that requires in the Secure Boot feature when using patch/kconfig-inclusions (sonic flow to modify kernel flags).
# So, when this problem will be resolved, this script should be removed and used the kconfig-inclusions.

usage() {
    cat <<EOF
$0: # Display Help
$0 <PEM_CERT>
Script is modifying kernel config file to support system trusted key with custom certificate.
Note: The signature algorithem used will be SHA512.

Parameters description:
PEM_CERT                             public key (pem format). Key to be store in kernel.

Run example:
bash secure_boot_kernel_config.sh cert.pem
EOF
}

if [ "$1" = "-h" -o "$1" = "--help" ]; then 
    usage
fi

echo "$0: Adding Secure Boot support in Kernel config file."

CERT_PEM=$1

[ -f "$CERT_PEM" ] || {
    echo "Error: CERT_PEM file does not exist: $CERT_PEM"
    usage
    exit 1
}

local_cert_pem="debian/certs/$(basename $CERT_PEM)"
linux_cfg_file="debian/build/build_amd64_none_amd64/.config"
sed -i "s|^CONFIG_SYSTEM_TRUSTED_KEYS=.*|CONFIG_SYSTEM_TRUSTED_KEYS=\"$local_cert_pem\"|g" $linux_cfg_file
sed -i 's/^CONFIG_MODULE_SIG_HASH=.*/CONFIG_MODULE_SIG_HASH="sha512"/g' $linux_cfg_file
sed -i 's/^CONFIG_MODULE_SIG_SHA256=.*/# CONFIG_MODULE_SIG_SHA256 is not set/g' $linux_cfg_file
sed -i 's/# CONFIG_MODULE_SIG_SHA512 is not set/CONFIG_MODULE_SIG_SHA512=y/g' $linux_cfg_file

#lockdown feature disable
sed -i 's/^CONFIG_SECURITY_LOCKDOWN_LSM=.*/# CONFIG_SECURITY_LOCKDOWN_LSM is not set/g' $linux_cfg_file
sed -i 's/^CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=.*/# CONFIG_SECURITY_LOCKDOWN_LSM_EARLY is not set/g' $linux_cfg_file
sed -i 's/^CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=.*/# CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE is not set/g' $linux_cfg_file
sed -i 's/^CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=.*/# CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT is not set/g' $linux_cfg_file

# warm boot secure
sed -i 's/# CONFIG_KEXEC_SIG_FORCE is not set/CONFIG_KEXEC_SIG_FORCE=y/g' $linux_cfg_file

echo "$0: Secure Boot support in Kernel config file DONE."


