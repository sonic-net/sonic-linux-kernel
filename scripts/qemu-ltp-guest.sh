#!/bin/bash
# Runs inside the QEMU guest from the tar on /dev/vdb (see qemu-ltp.sh), which
# carries this script and the run config in and the results out.
set -u
IO=/run/qemu-ltp
mkdir -p $IO/out
. $IO/env
export LTP_TIMEOUT_MUL TMPDIR=/var/tmp

while read -r suite pattern; do
    [ -z "$suite" ] || grep -E "^($pattern)" /opt/ltp/runtest/"$suite" ||
        echo "no tests selected by: $suite $pattern" >> $IO/out/errors
done < $IO/tests > /opt/ltp/runtest/qemu-ltp

uname -a
/opt/ltp/kirk --no-colors --tmp-dir /var/tmp --skip-file $IO/skip \
    --exec-timeout 15m --suite-timeout 1h \
    --json-report $IO/out/results.json --run-suite qemu-ltp
echo "kirk exit code $?"
dmesg > $IO/out/dmesg.log
tar -cf /dev/vdb -C $IO/out .
sync
echo o > /proc/sysrq-trigger
