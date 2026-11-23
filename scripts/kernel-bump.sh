#!/bin/bash
# Move the kernel to a newer Debian release and re-fit patches-sonic to it.
# Usage: scripts/kernel-bump.sh [--latest | <debian-version>]
# Default version: the newest in $DIST. Prints a markdown summary on stdout.
# Exits 2 if any patch does not apply; those stay in the series unchanged.
set -euo pipefail
cd "$(dirname "$0")/.."

DIST=${DIST:-trixie-security}
MIRROR=${MIRROR:-https://deb.debian.org/debian-security}
POOL=$MIRROR/pool/updates/main/l/linux
STABLE=${STABLE:-https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git}
SERIES=patches-sonic/series

mk() { sed -n "s/^$1 ?= //p" kernel-version.mk; }
latest() {
    curl -fsS "$MIRROR/dists/$DIST/main/source/Sources.xz" | xz -d |
        awk '/^Package: /{p=$2} /^Version: / && p=="linux"{print $2}' | sort -V | tail -1
}
[ "${1:-}" = --latest ] && { latest; exit 0; }

cur=$(mk KERNEL_VERSION)-$(mk KERNEL_SUBVERSION)
new=${1:-$(latest)}
dpkg --compare-versions "$new" gt "$cur" || { echo "Up to date: $cur" >&2; exit 0; }
ver=${new%-*}

work=$(mktemp -d)
trap '[ ! -f "$work/series.keep" ] || cat "$work/series.keep" > $SERIES; rm -rf "$work"' EXIT
for f in "linux_$new.dsc" "linux_$ver.orig.tar.xz" "linux_$new.debian.tar.xz"; do
    curl -fsSo "$work/$f" "$POOL/$f"
done
dpkg-source -x "$work/linux_$new.dsc" "$work/src" >&2

# Upstream references (mailing-list ids, mainline commit ids) of every stable
# commit since the current kernel, as "<ref> <stable commit>".
REFS='{ l = $0 }
l ~ /^[ \t]*(Link|Closes|Message-Id):/ {
    while (match(l, /[^][\/ <>()"]+@[^][\/ <>()"]+/)) {
        m = substr(l, RSTART, RLENGTH); l = substr(l, RSTART + RLENGTH)
        if (m !~ /@lists\.|@vger\.kernel\.org$/) print m, c }
    next }
tolower(l) ~ /cherry picked from commit|upstream commit|commit [0-9a-f]+ upstream|upstream-status: *backport|^from [0-9a-f]{40} / {
    while (match(l, /[0-9a-f]{12,40}/)) { print substr(l, RSTART, 12), c; l = substr(l, RSTART + RLENGTH) } }'
: > "$work/stable.refs"
if [ "${cur%-*}" != "$ver" ]; then
    git init -q "$work/stable"
    git -C "$work/stable" fetch -q --shallow-exclude="v${cur%-*}" "$STABLE" tag "v$ver" >&2
    git -C "$work/stable" log --format='@@@ %h%n%B' "v$ver" |
        awk '/^@@@ /{c = $2; next} '"$REFS" | sort -u > "$work/stable.refs"
fi
# The stable commit that already carries patch $1, if any.
upstream_ref() {
    awk '/^---$|^diff /{exit} {print}' "patches-sonic/$1" | awk -v c=x "$REFS" |
        awk 'NR==FNR{r[$1] = $2; next} ($1 in r) && !o++ {print r[$1]}' "$work/stable.refs" -
}

sed -i -e "s/^KERNEL_VERSION ?= .*/KERNEL_VERSION ?= $ver/" \
       -e "s/^KERNEL_SUBVERSION ?= .*/KERNEL_SUBVERSION ?= ${new#*-}/" kernel-version.mk

# The new stable release carries the CVE backports.
sed -i '/^###-> security-start/,/^###-> security-end/{/^security\//d}' $SERIES
git rm -rq --ignore-unmatch patches-sonic/security

entries() { sed -e 's/#.*//' -e 's/[[:space:]]*$//' | grep -v '^$' | awk '!s[$0]++' || true; }
section() { sed -n "/^###-> $1\$/,/^###-> $2\$/p" $SERIES | entries; }
files() { sed -n -E 's#^(---|\+\+\+) ([ab]/)?([^[:space:]]+).*#\3#p' "patches-sonic/$1" | grep -vx /dev/null || true; }
drop() {
    awk -v p="$1" '{l=$0; sub(/[[:space:]]*#.*/, "", l)} l != p' $SERIES > "$work/s"
    cat "$work/s" > $SERIES
    git rm -qf "patches-sonic/$1"
}

# Push each patch the way the build does (quilt, fuzz 0). If that fails: drop
# it when it is already upstream, refresh it when it applies with fuzz, else
# report it as failed. Patches touching a file of an earlier failure are left
# as they are (blocked), since their base is incomplete.
triage() {
    rm -rf "$work/t"
    cp -a "$work/src" "$work/t"
    : > "$work/series"
    : > "$work/result"
    : > "$work/ffiles"
    export QUILT_PATCHES=$PWD/patches-sonic QUILT_SERIES=$work/series QUILT_PC=.pc-sonic
    entries < $SERIES | while read -r p; do
        echo "$p" >> "$QUILT_SERIES"
        (cd "$work/t" && quilt --quiltrc - push -q --fuzz=0 >/dev/null 2>&1) && continue
        ev=$(upstream_ref "$p")
        if [ -n "$ev" ]; then
            r=upstream
        elif grep -qxFf "$work/ffiles" <(files "$p"); then
            r=blocked
        elif (cd "$work/t" && patch -p1 -R -s -f -F0 --dry-run < "$QUILT_PATCHES/$p" >/dev/null 2>&1); then
            r=upstream
        elif (cd "$work/t" && quilt --quiltrc - push -q >/dev/null 2>&1 &&
              quilt --quiltrc - refresh -p ab --no-timestamps --no-index >/dev/null); then
            r=refreshed
        else
            r=failed
        fi
        case $r in failed|blocked) files "$p" >> "$work/ffiles" ;; esac
        [ $r = refreshed ] || sed -i '$d' "$QUILT_SERIES"
        echo "$r $p $ev" >> "$work/result"
    done
    for p in $(awk '$1=="upstream"{print $2}' "$work/result"); do drop "$p"; done
}

# Regenerate the Aspeed patches at the Aspeed tag they were made from. The
# generator aborts if any patch it applies fails, so hide the failed and
# blocked ones from it.
regen_aspeed() {
    local old tag rc
    old=$(section aspeed-upstream aspeed-upstream-end)
    tag=v$(grep -o -m1 '[0-9][0-9]\.[0-9][0-9]\.[0-9][0-9]' <<< "$old") ||
        { echo "No Aspeed tag in the aspeed-upstream patch names" >&2; exit 1; }
    local hidden
    hidden=$(grep -vxFf <(echo "$old") <<< "$bad" || true)
    cp $SERIES "$work/series.keep"
    awk 'NR==FNR{s[$0]=1; next} {l=$0; sub(/[[:space:]]*#.*/, "", l)} !(l in s)' \
        <(echo "$hidden") "$work/series.keep" > $SERIES
    rc=0
    SONIC_KERNEL_URL=$POOL/linux_$ver.orig.tar.xz ASPEED_TAG=$tag TMPDIR=$work \
        scripts/generate-aspeed-patch.sh --baseline >&2 || rc=$?
    cat "$work/series.keep" > $SERIES
    rm "$work/series.keep"
    [ $rc = 0 ] || { note="Aspeed patches fail, and regenerating them at $tag failed (see the log)."; return 0; }
    INPUT_PATCH=$work/aspeed-patch-gen/aspeed-ast2700-baseline.patch \
        OUTDIR=$work/aspeed/split ASPEED_TAG=$tag scripts/split-aspeed-patch.py >&2
    for p in $old; do drop "$p"; done
    cp "$work"/aspeed/split/*.patch patches-sonic/
    NEW=$(cd "$work/aspeed/split" && ls *.patch) \
        awk '/^###-> aspeed-upstream-end$/{print ENVIRON["NEW"]} {print}' $SERIES > "$work/s"
    cat "$work/s" > $SERIES
    note="Aspeed patches regenerated at $tag."
    regen=1
}

# If the Aspeed patches fail, regenerate them and triage again.
note= regen=
triage
bad=$(awk '$1=="failed" || $1=="blocked" {print $2}' "$work/result")
if grep -qxFf <(section aspeed-upstream aspeed-upstream-end) <<< "$bad"; then
    grep -E '^(upstream|refreshed) ' "$work/result" |
        grep -vFf <(section aspeed-upstream aspeed-upstream-end) > "$work/result1" || true
    regen_aspeed
    if [ -n "$regen" ]; then
        triage
        cat "$work/result" "$work/result1" | awk '!s[$2]++' > "$work/s"
        cat "$work/s" > "$work/result"
        for p in $(awk '$1=="failed" || $1=="blocked" {print $2}' "$work/result"); do files "$p"; done > "$work/bfiles"
        for p in $(section aspeed-upstream aspeed-upstream-end); do files "$p"; done > "$work/afiles"
        ! grep -qxFf "$work/bfiles" "$work/afiles" ||
            note="$note They share files with failed or blocked patches, so regenerate them again once those are fixed."
    fi
fi

echo "Linux $cur → $new"
echo
echo "- CVE backports in \`patches-sonic/security\` dropped; the new stable release carries them."
[ -z "$note" ] || echo "- $note"
bad=$(grep -cE '^(failed|blocked) ' "$work/result" || true)
[ "$bad" = 0 ] || echo "- **$bad patches do not apply. They are kept unchanged; fix them before merging.**"
echo
echo "| Result | Patches |"
echo "|---|---|"
for r in failed blocked refreshed upstream; do
    n=$(grep -c "^$r " "$work/result" || true)
    [ "$n" = 0 ] || echo "| $r | $n |"
done
echo
echo "blocked: touches a file of an earlier failed patch, so left unchanged."
for r in failed blocked refreshed upstream; do
    grep -q "^$r " "$work/result" || continue
    printf '\n<details><summary>%s</summary>\n\n' $r
    awk -v r=$r '$1==r{print "- `" $2 "`" ($3 ? " (stable " $3 ")" : "")}' "$work/result"
    printf '\n</details>\n'
done
[ "$bad" = 0 ] || exit 2
