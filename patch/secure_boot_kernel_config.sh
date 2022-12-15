#!/bin/bash

# This script is doing modification in kconfig-inclusions and kconfig-exclusions files in order to support Secure Boot feature.

usage() {
    cat <<EOF
$0: # Display Help
$0 -c <PEM_CERT> -a <CONF_ARCH>
Script is modifying kernel config file to support system trusted key with custom certificate.
Note: The signature algorithm used will be RSA over SHA512 x509 format.

Parameters description:
PEM_CERT                             public key (pem format). Key to be store in kernel.
CONF_ARCH                           is the kernel arch amd/arm/etc
Usage example: bash secure_boot_kernel_config.sh cert.pem
EOF
}

# the function is appending a line after the string from variable $1
# var pos $2: new config to be set
# var pos $3: filename to be modify 
append_line_after_str() {
sed -i "/$1/a $2" $3
}

while getopts 'c:a:hv' flag; do
  case "${flag}" in
    c) CERT_PEM="${OPTARG}" ;;
    a) CONF_ARCH="${OPTARG}" ;;
    v) VERBOSE='true' ;;
    h) print_usage
       exit 1 ;;
  esac
done

if [ "$1" = "-h" -o "$1" = "--help" ]; then 
    usage
fi

[ -f "$CERT_PEM" ] || {
    echo "Error: CERT_PEM file does not exist: $CERT_PEM"
    usage
    exit 1
}

[ ! -z "$CONF_ARCH" ] || {
    echo "Error: CONF_ARCH file does not exist: $CONF_ARCH"
    usage
    exit 1
}

LOCAL_CERT_PEM="debian/certs/$(basename $CERT_PEM)"
KCONFIG_INCLUSIONS_FILE="../patch/kconfig-inclusions"
KCONFIG_EXCLUSIONS_FILE="../patch/kconfig-exclusions"
CONF_ARCH_BLOCK_REGEX="^\[$CONF_ARCH\]"

echo "$0: Appending kernel configuration in files: $KCONFIG_INCLUSIONS_FILE, $KCONFIG_EXCLUSIONS_FILE"

# add support to secure boot and secure warm boot
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_SYSTEM_TRUSTED_KEYS=\"$LOCAL_CERT_PEM\"" $KCONFIG_INCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_MODULE_SIG_HASH=\"sha512\"" $KCONFIG_INCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_MODULE_SIG_SHA512=y" $KCONFIG_INCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_KEXEC_SIG_FORCE=y" $KCONFIG_INCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "#Secure Boot" $KCONFIG_INCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_SECURITY_LOCKDOWN_LSM" $KCONFIG_EXCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_SECURITY_LOCKDOWN_LSM_EARLY" $KCONFIG_EXCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE" $KCONFIG_EXCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT" $KCONFIG_EXCLUSIONS_FILE
append_line_after_str $CONF_ARCH_BLOCK_REGEX "CONFIG_MODULE_SIG_SHA256" $KCONFIG_EXCLUSIONS_FILE
