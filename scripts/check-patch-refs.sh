#!/bin/bash
# Fail if a patch added since <base> has no upstream reference, which
# kernel-bump.sh needs to tell when upstream has picked the patch up.
# Usage: scripts/check-patch-refs.sh <base>
set -euo pipefail
cd "$(dirname "$0")/.."
rc=0
added=$(git diff --name-only --diff-filter=A "$1"...HEAD -- 'patches-sonic/*.patch')
for p in $added; do
    grep -qiE '^Link:.*@|^Upstream-Status:|cherry picked from commit [0-9a-f]|^\[ Upstream commit [0-9a-f]|^commit [0-9a-f]{40} upstream' \
        <(awk '/^---$|^diff /{exit} {print}' "$p") && continue
    echo "$p: add a lore/patch.msgid.link Link:, a (cherry picked from commit <id>) line, or an Upstream-Status: line" >&2
    rc=1
done
exit $rc
