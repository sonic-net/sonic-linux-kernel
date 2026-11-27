#!/usr/bin/env bash
# Compare the actual host generators before and after applying the SONiC patch.
set -euo pipefail

archive=$(realpath "${1:?pass the linux orig source archive}")
repo=$(cd "$(dirname "$0")/../.." && pwd)
patch_file=speakup-allow-long-input-paths-in-map-generators.patch
grep -Fxq "$patch_file" "$repo/patches-sonic/series"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/src" "$work/reference" "$work/long"
cd "$work/src"
tar -xf "$archive" --strip-components=1 --wildcards \
    'linux-*/drivers/accessibility/speakup/*' \
    'linux-*/include/linux/input.h' 'linux-*/include/uapi/linux/input-event-codes.h'

speakup=drivers/accessibility/speakup
generate() {
    local src=$1 out=$2
    cc -O2 "$speakup/makemapdata.c" -o "$out/makemapdata"
    TOPDIR="$src" SPKDIR="$src/$speakup" "$out/makemapdata" > "$out/mapdata.h"
    cc -O2 -I"$out" "$speakup/genmap.c" -o "$out/genmap"
    "$out/genmap" "$src/$speakup/speakupmap.map" > "$out/speakupmap.h"
}

generate . "$work/reference"
patch --batch --fuzz=0 -p1 -i "$repo/patches-sonic/$patch_file"
# Each component fits NAME_MAX while the absolute path exceeds 255 bytes.
segment=$(printf '%090d' 0)
long="$work/$segment/$segment/$segment"
mkdir -p "$long"
ln -s "$work/src" "$long/source"
generate "$long/source" "$work/long"
cmp "$work/reference/mapdata.h" "$work/long/mapdata.h"
cmp "$work/reference/speakupmap.h" "$work/long/speakupmap.h"
