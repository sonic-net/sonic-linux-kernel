#!/bin/bash
# Boot a linux-image .deb in QEMU on a Debian rootfs and run an LTP subset.
# Usage: sudo scripts/qemu-ltp.sh <linux-image-*.deb> [outdir]
# Needs root (mmdebstrap), mmdebstrap, qemu-system-x86, e2fsprogs >= 1.47.1.
set -euo pipefail

DEB=$(realpath "$1")
OUT=$(realpath -m "${2:-ltp-results}")
SCRIPTS=$(dirname "$(realpath "$0")")
WORK=${WORK:-$OUT/work}
LTP_VERSION=20260930
LTP_SHA256=b9837481134fa6eabce91c2fba57d96695f81360468f83e11e6982584ad4d29d
ROOTFS=$WORK/rootfs.tar

rm -rf "$WORK" && mkdir -p "$OUT" "$WORK"
LTP_TAR=$WORK/ltp-full-$LTP_VERSION.tar.xz
curl -fsSL -o "$LTP_TAR" "https://github.com/linux-test-project/ltp/releases/download/$LTP_VERSION/ltp-full-$LTP_VERSION.tar.xz"
echo "$LTP_SHA256  $LTP_TAR" | sha256sum -c -
tar -xf "$LTP_TAR" -C "$WORK"
cat > "$WORK/qemu-ltp.service" <<'EOF'
[Unit]
After=multi-user.target
[Service]
Type=oneshot
ExecStart=sh -c 'mkdir -p /run/qemu-ltp && tar -xf /dev/vdb -C /run/qemu-ltp && exec bash /run/qemu-ltp/guest.sh'
StandardOutput=tty
StandardError=tty
TTYPath=/dev/ttyS0
[Install]
WantedBy=multi-user.target
EOF

mmdebstrap --variant=minbase --format=tar \
    --include="initramfs-tools,systemd-sysv,udev,python3,e2fsprogs,dosfstools,iproute2,iputils-ping,bsdextrautils,procps,psmisc,acl,attr,kmod,bc,build-essential,pkgconf,libaio-dev,libacl1-dev,libcap-dev,libkeyutils-dev,libnuma-dev,libmnl-dev" \
    --customize-hook="copy-in $DEB /tmp" \
    --customize-hook="chroot \"\$1\" dpkg -i /tmp/$(basename "$DEB") && rm \"\$1\"/tmp/$(basename "$DEB")" \
    --customize-hook="copy-in $WORK/ltp-full-$LTP_VERSION /usr/src" \
    --customize-hook="chroot \"\$1\" sh -ec 'cd /usr/src/ltp-full-$LTP_VERSION && ./configure --prefix=/opt/ltp && make -j$(nproc) && make install' >\"$OUT/ltp-build.log\" 2>&1" \
    --customize-hook="rm -rf \"\$1\"/usr/src/ltp-full-$LTP_VERSION" \
    --customize-hook="copy-in $WORK/qemu-ltp.service /etc/systemd/system" \
    --customize-hook="chroot \"\$1\" systemctl enable qemu-ltp.service" \
    --customize-hook="chroot \"\$1\" systemctl mask serial-getty@ttyS0.service getty.target" \
    --customize-hook="echo '/dev/vda / ext4 rw 0 1' > \"\$1\"/etc/fstab; echo ltp > \"\$1\"/etc/hostname" \
    trixie "$ROOTFS" http://deb.debian.org/debian

mkdir "$WORK/boot"
tar -xf "$ROOTFS" -C "$WORK/boot" --wildcards --strip-components=2 './boot/vmlinuz-*' './boot/initrd.img-*'
truncate -s 8G "$WORK/disk.img"
mkfs.ext4 -q -F -d "$ROOTFS" "$WORK/disk.img"

if [ -w /dev/kvm ]; then
    ACCEL=(-accel kvm -cpu host) TIMEOUT_MUL=${LTP_TIMEOUT_MUL:-1}
else
    ACCEL=(-accel tcg,thread=multi -cpu max) TIMEOUT_MUL=${LTP_TIMEOUT_MUL:-4}
fi
echo "Accelerator: ${ACCEL[*]}"

mkdir "$WORK/io"
echo "LTP_TIMEOUT_MUL=$TIMEOUT_MUL" > "$WORK/io/env"
grep -v '^\s*#' "$SCRIPTS/qemu-ltp.tests" > "$WORK/io/tests"
install -m 755 "$SCRIPTS/qemu-ltp-guest.sh" "$WORK/io/guest.sh"
grep -v '^\s*#' "$SCRIPTS/qemu-ltp.skip" | grep . > "$WORK/io/skip" || true
tar -cf "$WORK/io.img" -C "$WORK/io" . && truncate -s 256M "$WORK/io.img"

timeout 2400 qemu-system-x86_64 "${ACCEL[@]}" -m 4G -smp 4 \
    -nographic -no-reboot -nic none \
    -kernel "$WORK"/boot/vmlinuz-* -initrd "$WORK"/boot/initrd.img-* \
    -append "root=/dev/vda rw console=ttyS0 panic=-1 oops=panic systemd.show_status=0" \
    -drive file="$WORK/disk.img",if=virtio,format=raw \
    -drive file="$WORK/io.img",if=virtio,format=raw \
    < /dev/null | tee "$OUT/console.log" || echo "qemu exited with $?"

tar -xf "$WORK/io.img" -C "$OUT" 2>/dev/null || true
if [ ! -f "$OUT/results.json" ]; then
    echo "No LTP results produced; last test started:"
    grep -o 'kirk\[[0-9]*\]: [^ ]*: start' "$OUT/console.log" | tail -1
    exit 1
fi
[ ! -s "$OUT/errors" ] || { cat "$OUT/errors"; exit 1; }
python3 -I - "$OUT/results.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
print("stats:", r["stats"])
bad = [t["test_fqn"] for t in r["results"] if t["status"] in ("fail", "brok")]
for t in bad:
    print("FAILED:", t)
sys.exit(1 if bad or not r["results"] else 0)
EOF
