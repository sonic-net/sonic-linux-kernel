#!/bin/bash
# Generates an incremental Aspeed AST2700 patch for the SONiC kernel.
# Diffs (kernel with all currently-committed series patches applied) against
# (kernel with a fresh Aspeed upstream merge). The output captures whatever
# is new in upstream relative to the existing patches-sonic/ series. See the
# design comment further below for details.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$(dirname "$(dirname "$KERNEL_DIR")")"

# Configuration
# Example, run with ASPEED_TAG=v00.07.02 ./scripts/generate-aspeed-patch.sh
ASPEED_REF="${ASPEED_TAG:-aspeed-master-v6.12}"
ASPEED_REPO="https://github.com/AspeedTech-BMC/linux.git"
WORK_DIR="${TMPDIR:-/tmp}/aspeed-patch-gen"
ASPEED_SRC="$WORK_DIR/aspeed-src"
SONIC_SRC="$WORK_DIR/sonic-src"
BASELINE_TREE="$WORK_DIR/baseline"
SONIC_ASPEED="$WORK_DIR/sonic-src-aspeed"
OUTPUT_PATCH="$WORK_DIR/aspeed-ast2700-incremental.patch"

# Read kernel version from Makefile
KERNEL_VERSION=$(sed -nE 's/^KERNEL_VERSION[[:space:]]*[?:+]?=[[:space:]]*//p' "$KERNEL_DIR/Makefile")
SONIC_KERNEL_URL="https://packages.trafficmanager.net/public/debian-security/pool/updates/main/l/linux/linux_${KERNEL_VERSION}.orig.tar.xz"

# Patch author for the generated From:/Signed-off-by: lines. Resolution order:
#   1. $PATCH_AUTHOR if provided ("Name <email>")
#   2. the invoking user's git identity (git config user.name/user.email)
#   3. a generic fallback if git config is unset
if [ -z "$PATCH_AUTHOR" ]; then
    git_name=$(git -C "$KERNEL_DIR" config user.name 2>/dev/null)
    git_email=$(git -C "$KERNEL_DIR" config user.email 2>/dev/null)
    if [ -n "$git_name" ] && [ -n "$git_email" ]; then
        PATCH_AUTHOR="$git_name <$git_email>"
    else
        echo "WARNING: no PATCH_AUTHOR arg and git user.name/user.email unset;"
        exit 1
    fi
fi

# Accumulated across all apply_patches invocations.
TOTAL_APPLY_FAILURES=0

echo "========================================="
echo "Aspeed Patch Generation Script (incremental)"
echo "========================================="
echo "Kernel Version:         $KERNEL_VERSION"
echo "Aspeed Upstream Ref:    $ASPEED_REF"
echo "Patch Author:           $PATCH_AUTHOR"
echo "Work Directory:         $WORK_DIR"
echo "Output Patch:           $OUTPUT_PATCH"
echo "========================================="

# Clean up previous run (optional - comment out to preserve for analysis)
# if [ -d "$WORK_DIR" ]; then
#     echo "Cleaning up previous work directory..."
#     rm -rf "$WORK_DIR"
# fi

# Create work directory if it doesn't exist
if [ ! -d "$WORK_DIR" ]; then
    echo "Creating work directory..."
    mkdir -p "$WORK_DIR"
else
    echo "Work directory already exists, will reuse/overwrite files..."
fi

# Step 1: Download Aspeed kernel source
echo ""
echo "Step 1: Downloading Aspeed kernel source..."
if [ -d "$ASPEED_SRC/.git" ]; then
    cached_branch=$(git -C "$ASPEED_SRC" rev-parse --abbrev-ref HEAD 2>/dev/null)
    head_commit=$(git -C "$ASPEED_SRC" rev-parse -q --verify HEAD 2>/dev/null)
    ref_commit=$(git -C "$ASPEED_SRC" rev-parse -q --verify "${ASPEED_REF}^{commit}" 2>/dev/null)
    if [ "$cached_branch" = "$ASPEED_REF" ] || { [ -n "$head_commit" ] && [ "$head_commit" = "$ref_commit" ]; }; then
        echo "Aspeed source already exists at $ASPEED_SRC and is at $ASPEED_REF, reusing"
    else
        echo "  WARNING: cached Aspeed source at $ASPEED_SRC is NOT at $ASPEED_REF"
        echo "  WARNING:   checked out: ${cached_branch:-<unknown>} ($head_commit)"
        echo "  WARNING:   expected:    $ASPEED_REF"
        echo "  WARNING: reusing it as-is — the generated diff may not reflect $ASPEED_REF."
        echo "  WARNING: remove $ASPEED_SRC and re-run to force a fresh clone."
    fi
else
    rm -rf "$ASPEED_SRC"
    echo "Cloning $ASPEED_REPO @ $ASPEED_REF -> $ASPEED_SRC"
    git clone --depth 1 --branch "$ASPEED_REF" "$ASPEED_REPO" "$ASPEED_SRC"
fi

echo "Aspeed source ready (size on disk: $(du -sh "$ASPEED_SRC" | cut -f1))"

# Step 2: Download SONiC kernel source
echo ""
echo "Step 2: Downloading SONiC kernel source..."
SONIC_TARBALL="$WORK_DIR/linux_${KERNEL_VERSION}.orig.tar.xz"
if [ -d "$SONIC_SRC" ]; then
    echo "SONiC source already exists, removing and re-extracting..."
    rm -rf "$SONIC_SRC"
fi
if [ -f "$SONIC_TARBALL" ]; then
    echo "SONiC kernel tarball already cached at $SONIC_TARBALL, skipping download..."
else
    echo "Downloading SONiC kernel tarball to $SONIC_TARBALL..."
    wget -q -O "$SONIC_TARBALL" "$SONIC_KERNEL_URL"
fi
mkdir -p "$SONIC_SRC"
cd "$SONIC_SRC"
tar -xf "$SONIC_TARBALL" --strip-components=1
echo "SONiC kernel source ready (size on disk: $(du -sh "$SONIC_SRC" | cut -f1))"

# Series sections (delimited by "###-> NAME-start" / "###-> NAME-end") whose
# patches this script must NOT apply to either tree. Such blocks are managed
# elsewhere (e.g. the NVIDIA hw-mgmt integration rewrites nvidia_aspeed_bmc
# wholesale between its markers in sonic-buildimage) and/or depend on Aspeed
# content this script never builds; they also cancel in the diff since they'd
# land on both sides. To exclude a new section, add its base-name here — no
# other code change needed. (The ###-> aspeed section is handled separately.)
SKIP_SECTIONS=("nvidia_aspeed_bmc")

# _is_skip_section NAME -> returns 0 if NAME is listed in SKIP_SECTIONS, else 1.
_is_skip_section() {
    local needle="$1" s
    for s in "${SKIP_SECTIONS[@]}"; do
        [ "$s" = "$needle" ] && return 0
    done
    return 1
}

# validate_series_ordering: assert that every hand-written aspeed-section patch
# (one inside ###-> aspeed but outside any ###-> aspeed-upstream sub-section)
# comes AFTER all ###-> aspeed-upstream sub-sections. See above for motivation
validate_series_ordering() {
    local series_file="$KERNEL_DIR/patches-sonic/series"
    local in_aspeed=0 in_upstream=0 in_skip=0 seen_handwritten=0
    local handwritten_example="" sect=""

    # Reads via redirection (not a pipe), so this loop runs in the current
    # shell — exiting on the first violation aborts the whole script directly.
    while IFS= read -r line; do
        case "$line" in
            "###-> aspeed")              in_aspeed=1; continue ;;
            "###-> aspeed-end")          in_aspeed=0; continue ;;
            "###-> aspeed-upstream-end") in_upstream=0; continue ;;
            "###-> aspeed-upstream")
                if [ $seen_handwritten -eq 1 ]; then
                    echo "  ERROR: ###-> aspeed-upstream sub-section opens after hand-written" >&2
                    echo "         patch '$handwritten_example' — all upstream sub-sections must" >&2
                    echo "         precede every hand-written aspeed-section patch." >&2
                    echo "ABORT: patches-sonic/series ordering invariant violated." >&2
                    exit 1
                fi
                in_upstream=1
                continue
                ;;
            "###-> "*"-start")
                sect="${line#"###-> "}"; sect="${sect%-start}"
                if _is_skip_section "$sect"; then in_skip=1; fi
                continue
                ;;
            "###-> "*"-end")
                sect="${line#"###-> "}"; sect="${sect%-end}"
                if _is_skip_section "$sect"; then in_skip=0; fi
                continue
                ;;
        esac

        # Comments and blanks don't affect ordering.
        [[ "$line" =~ ^#.*$ ]] && continue
        [[ -z "$line" ]] && continue

        # A real patch entry inside the aspeed section, outside any upstream
        # sub-section (and outside any skip section), is hand-written.
        if [ $in_aspeed -eq 1 ] && [ $in_upstream -eq 0 ] && [ $in_skip -eq 0 ]; then
            seen_handwritten=1
            local stripped="${line%%#*}"
            stripped="${stripped%"${stripped##*[![:space:]]}"}"
            [ -n "$stripped" ] && handwritten_example="$stripped"
        fi
    done < "$series_file"
}

# apply_patches: iterate patches-sonic/series in order and apply patches into
# $target_dir, filtered by $mode.
#
# Modes:
#   non-aspeed      - apply entries OUTSIDE the ###-> aspeed section AND outside
#                     any SKIP_SECTIONS block (see that list above). Skip-section
#                     patches are vendor-managed and/or Aspeed-dependent and cancel
#                     in the diff regardless, so they are never applied.
#   aspeed-upstream - apply entries INSIDE the ###-> aspeed section AND inside an
#                     ###-> aspeed-upstream sub-section. Patches  outside any
#                     ###-> aspeed-upstream sub-section (i.e. handwritten patc)
#                     are deliberately NOT applied — see the design comment at
#                     the top of this file.
apply_patches() {
    local target_dir="$1"
    local mode="$2"
    local label="$3"

    echo ""
    echo "Applying patches into $target_dir [$label, mode=$mode]"

    local applied=0
    local skipped=0
    local failed=0
    local in_aspeed=0
    local in_upstream=0
    local in_skip=0
    local sect=""
    local series_file="$KERNEL_DIR/patches-sonic/series"

    pushd "$target_dir" > /dev/null
    set +e

    while IFS= read -r line; do
        if [[ "$line" == "###-> aspeed" ]]; then
            in_aspeed=1
            continue
        elif [[ "$line" == "###-> aspeed-end" ]]; then
            in_aspeed=0
            continue
        elif [[ "$line" == "###-> aspeed-upstream" ]]; then
            in_upstream=1
            continue
        elif [[ "$line" == "###-> aspeed-upstream-end" ]]; then
            in_upstream=0
            continue
        elif [[ "$line" == "###-> "*"-start" ]]; then
            sect="${line#"###-> "}"; sect="${sect%-start}"
            if _is_skip_section "$sect"; then in_skip=1; fi
            continue
        elif [[ "$line" == "###-> "*"-end" ]]; then
            sect="${line#"###-> "}"; sect="${sect%-end}"
            if _is_skip_section "$sect"; then in_skip=0; fi
            continue
        fi

        if [[ "$line" =~ ^#.*$ ]] || [[ -z "$line" ]]; then
            continue
        fi

        # Quilt-style trailing comments: "<filename>.patch # some note".
        # Strip the comment and surrounding whitespace before we look up
        # the patch file. (Marker lines are matched above by full equality,
        # so this stripping doesn't affect them.)
        line="${line%%#*}"
        line="${line%"${line##*[![:space:]]}"}"
        line="${line#"${line%%[![:space:]]*}"}"
        if [[ -z "$line" ]]; then
            continue
        fi

        local should_apply=0
        case "$mode" in
            non-aspeed)
                [ $in_aspeed -eq 0 ] && [ $in_skip -eq 0 ] && should_apply=1
                ;;
            aspeed-upstream)
                if [ $in_aspeed -eq 1 ] && [ $in_upstream -eq 1 ]; then
                    should_apply=1
                fi
                ;;
            *)
                echo "apply_patches: unknown mode '$mode'" >&2
                set -e
                popd > /dev/null
                return 2
                ;;
        esac

        if [ $should_apply -eq 0 ]; then
            skipped=$((skipped + 1))
            continue
        fi

        local patch_file="$KERNEL_DIR/patches-sonic/$line"
        if [ -f "$patch_file" ]; then
            echo "  Applying: $line"
            local patch_output patch_rc
            patch_output=$(patch -p1 < "$patch_file" 2>&1)
            patch_rc=$?
            echo "$patch_output" | sed 's/^/    /'

            if [ $patch_rc -ne 0 ]; then
                echo "  ERROR: $line FAILED to apply (exit $patch_rc)"
                failed=$((failed + 1))
            else
                applied=$((applied + 1))
            fi
        else
            echo "  ERROR: patch file not found: $line"
            failed=$((failed + 1))
        fi
    done < "$series_file"

    set -e
    popd > /dev/null

    echo "  Result: applied=$applied skipped=$skipped failed=$failed"

    TOTAL_APPLY_FAILURES=$((TOTAL_APPLY_FAILURES + failed))
}

# Validate series ordering before building any trees (cheap, fail-fast).
echo ""
echo "Validating patches-sonic/series ordering..."
validate_series_ordering
echo "Series ordering OK"

# Step 3: Apply non-aspeed patches into sonic-src (shared by both trees).
echo ""
echo "Step 3: Applying non-Aspeed SONiC patches into sonic-src..."
apply_patches "$SONIC_SRC" "non-aspeed" "sonic-src (shared base)"

# Step 3b: Build baseline tree = sonic-src + the ###-> aspeed-upstream patches.
# Represents the committed upstream-merge state the incremental will diff
# against. Hand-written aspeed-section patches are intentionally skipped (they
# cancel by absence — they're applied to neither tree).
echo ""
echo "Step 3b: Building baseline tree..."
if [ -d "$BASELINE_TREE" ]; then
    echo "Baseline tree already exists, removing and recreating..."
    rm -rf "$BASELINE_TREE"
fi
cp -a "$SONIC_SRC" "$BASELINE_TREE"
apply_patches "$BASELINE_TREE" "aspeed-upstream" "baseline"
echo "Baseline tree ready: $(du -sh $BASELINE_TREE | cut -f1)"

# Steps 4 + 5: build the target tree by merging fresh Aspeed upstream content
# into a copy of sonic-src. Wrapped in a function purely to keep the merge
# logic isolated from the control flow above.
merge_aspeed_files() {
    local merge_label="$1"
    echo ""
    echo "========================================="
    echo "Building target tree: $merge_label"
    echo "  ASPEED_SRC:   $ASPEED_SRC"
    echo "  SONIC_ASPEED: $SONIC_ASPEED"
    echo "========================================="

# Step 4: Create sonic-src-aspeed by copying sonic-src
echo ""
echo "Step 4: Creating sonic-src-aspeed..."
if [ -d "$SONIC_ASPEED" ]; then
    echo "sonic-src-aspeed already exists, removing and recreating..."
    rm -rf "$SONIC_ASPEED"
fi
cp -a "$SONIC_SRC" "$SONIC_ASPEED"
echo "Copy complete: $(du -sh $SONIC_ASPEED | cut -f1)"

# Step 5: Merge Aspeed files
echo ""
echo "Step 5: Merging Aspeed AST2700 files..."

# Step 5a: Copy all DTS files
echo "Step 5a: Copying ARM64 DTS files..."
mkdir -p "$SONIC_ASPEED/arch/arm64/boot/dts/aspeed"
cp -rv "$ASPEED_SRC/arch/arm64/boot/dts/aspeed/"* "$SONIC_ASPEED/arch/arm64/boot/dts/aspeed/" | wc -l

# Step 5a2: Preserve EXISTING ARM32 DTS files unchanged (don't add new ones)
echo "Step 5a2: Preserving existing ARM32 DTS files unchanged..."
# We need to preserve existing ARM32 DTS files exactly as they are in SONiC kernel
# We don't want to modify them OR add new ARM32 DTS files from Aspeed kernel because
# they have DTC syntax (/bits/) that's incompatible with SONiC kernel's DTC version
if [ -d "$SONIC_SRC/arch/arm/boot/dts/aspeed" ]; then
    mkdir -p "$SONIC_ASPEED/arch/arm/boot/dts/aspeed"

    # Copy ALL existing files from SONiC kernel unchanged
    ARM32_PRESERVED=0
    for sonic_file in "$SONIC_SRC/arch/arm/boot/dts/aspeed/"*; do
        if [ -f "$sonic_file" ]; then
            filename=$(basename "$sonic_file")
            # Always copy from SONiC source to preserve existing files unchanged
            cp -v "$sonic_file" "$SONIC_ASPEED/arch/arm/boot/dts/aspeed/$filename"
            ARM32_PRESERVED=$((ARM32_PRESERVED + 1))
        fi
    done
    echo "  Preserved $ARM32_PRESERVED existing ARM32 DTS files unchanged"
else
    echo "  No ARM32 DTS directory in SONiC source"
fi

# Step 5b: Find and intelligently merge Kconfig files that have Aspeed-related changes
echo "Step 5b: Intelligently merging modified Kconfig files..."
KCONFIG_COUNT=0

SMART_MERGE="$SCRIPT_DIR/smart_merge.py"

find "$ASPEED_SRC" -name "Kconfig" -type f > "$WORK_DIR/kconfig-files.txt"
while IFS= read -r aspeed_kconfig; do
    rel_path="${aspeed_kconfig#$ASPEED_SRC/}"
    sonic_kconfig="$SONIC_SRC/$rel_path"
    target_kconfig="$SONIC_ASPEED/$rel_path"

    # Only process if the file exists in SONiC source
    if [ -f "$sonic_kconfig" ]; then
        # Check if there are differences
        if ! diff -q "$sonic_kconfig" "$aspeed_kconfig" > /dev/null 2>&1; then
            set +e  # Temporarily disable exit on error for grep

            # Check if file is in Aspeed-related directory
            echo "$rel_path" | grep -qi "aspeed\|ast2[567]00\|ast1[78]00"
            in_aspeed_dir=$?

            # Check if diff has added lines with Aspeed content
            diff -u "$sonic_kconfig" "$aspeed_kconfig" | grep "^+" | grep -v "^+++" | grep -qi "aspeed\|ast2[567]00\|ast1[78]00"
            has_aspeed_additions=$?

            if [ $in_aspeed_dir -eq 0 ]; then
                # File is in Aspeed-specific directory - copy entirely
                echo "  Aspeed-specific file: $rel_path"
                mkdir -p "$SONIC_ASPEED/$(dirname $rel_path)"
                cp "$aspeed_kconfig" "$target_kconfig"
                KCONFIG_COUNT=$((KCONFIG_COUNT + 1))
            elif [ $has_aspeed_additions -eq 0 ]; then
                # Shared file with Aspeed additions - smart merge
                echo "  Shared file with Aspeed changes: $rel_path (smart merge)"
                mkdir -p "$SONIC_ASPEED/$(dirname $rel_path)"

                # Try smart merge, fall back to full copy if it fails
                if ! python3 "$SMART_MERGE" "$sonic_kconfig" "$aspeed_kconfig" "$target_kconfig" 2>/dev/null; then
                    echo "    Smart merge failed, using full file"
                    cp "$aspeed_kconfig" "$target_kconfig"
                fi

                KCONFIG_COUNT=$((KCONFIG_COUNT + 1))
            fi
            set -e  # Re-enable exit on error
        fi
    fi
done < "$WORK_DIR/kconfig-files.txt"
echo "  Processed $KCONFIG_COUNT Kconfig files with Aspeed changes"

# Step 5c: Find and intelligently merge Makefile files that have Aspeed-related changes
echo "Step 5c: Intelligently merging modified Makefile files..."
MAKEFILE_COUNT=0
find "$ASPEED_SRC" -name "Makefile" -type f > "$WORK_DIR/makefile-files.txt"
while IFS= read -r aspeed_makefile; do
    rel_path="${aspeed_makefile#$ASPEED_SRC/}"
    sonic_makefile="$SONIC_SRC/$rel_path"
    target_makefile="$SONIC_ASPEED/$rel_path"

    # Only process if the file exists in SONiC source
    if [ -f "$sonic_makefile" ]; then
        # Check if there are differences
        if ! diff -q "$sonic_makefile" "$aspeed_makefile" > /dev/null 2>&1; then
            set +e  # Temporarily disable exit on error for grep

            # Check if file is in Aspeed-related directory
            echo "$rel_path" | grep -qi "aspeed\|ast2[567]00\|ast1[78]00"
            in_aspeed_dir=$?

            # Check if diff has added lines with Aspeed content
            diff -u "$sonic_makefile" "$aspeed_makefile" | grep "^+" | grep -v "^+++" | grep -qi "aspeed\|ast2[567]00\|ast1[78]00"
            has_aspeed_additions=$?

            # Skip ALL ARM32 Makefiles (we only need ARM64 for AST2700)
            if echo "$rel_path" | grep -q "^arch/arm/"; then
                echo "  Skipping ARM32 Makefile: $rel_path (not needed for ARM64)"
            elif [ $in_aspeed_dir -eq 0 ]; then
                # File is in Aspeed-specific directory - copy entirely
                echo "  Aspeed-specific file: $rel_path"
                mkdir -p "$SONIC_ASPEED/$(dirname $rel_path)"
                cp "$aspeed_makefile" "$target_makefile"
                MAKEFILE_COUNT=$((MAKEFILE_COUNT + 1))
            elif [ $has_aspeed_additions -eq 0 ]; then
                # Shared file with Aspeed additions - smart merge
                echo "  Shared file with Aspeed changes: $rel_path (smart merge)"
                mkdir -p "$SONIC_ASPEED/$(dirname $rel_path)"

                # Use smart merge, fall back to full copy if it fails
                if ! python3 "$SMART_MERGE" "$sonic_makefile" "$aspeed_makefile" "$target_makefile" 2>/dev/null; then
                    echo "    Smart merge failed, using full file"
                    cp "$aspeed_makefile" "$target_makefile"
                fi

                MAKEFILE_COUNT=$((MAKEFILE_COUNT + 1))
            fi
            set -e  # Re-enable exit on error
        fi
    fi
done < "$WORK_DIR/makefile-files.txt"
echo "  Processed $MAKEFILE_COUNT Makefile files with Aspeed changes"

# Step 5c2: Find and copy directories referenced by new Kconfig source statements
echo "Step 5c2: Copying directories referenced by new Kconfig source statements..."
KCONFIG_DIR_COUNT=0
# Find all Kconfig files that were modified
find "$ASPEED_SRC" -name "Kconfig" -type f > "$WORK_DIR/kconfig-check-source.txt"
while IFS= read -r aspeed_kconfig; do
    rel_path="${aspeed_kconfig#$ASPEED_SRC/}"
    sonic_kconfig="$SONIC_SRC/$rel_path"

    # Only process if the file exists in SONiC source
    if [ -f "$sonic_kconfig" ]; then
        # Check if there are new source statements added
        set +e
        # Find lines that start with +source (added lines)
        new_sources=$(diff -u "$sonic_kconfig" "$aspeed_kconfig" | grep "^+source" | sed 's/^+source "\(.*\)"/\1/')
        set -e

        # For each new source statement, copy the referenced directory
        if [ -n "$new_sources" ]; then
            while IFS= read -r source_path; do
                if [ -n "$source_path" ]; then
                    # The source path is relative to kernel root (e.g., "drivers/char/hw_random/dwc/Kconfig")
                    source_file="$ASPEED_SRC/$source_path"
                    source_dir=$(dirname "$source_file")

                    # Check if the source directory exists and contains Aspeed-related content
                    if [ -d "$source_dir" ]; then
                        set +e
                        # Check if directory or its Kconfig has Aspeed references
                        grep -rqi "aspeed\|ast2[567]00\|ast1[78]00" "$source_dir" 2>/dev/null
                        if [ $? -eq 0 ]; then
                            rel_source_dir="${source_dir#$ASPEED_SRC/}"
                            echo "  Found new source directory: $rel_source_dir"
                            mkdir -p "$SONIC_ASPEED/$rel_source_dir"
                            cp -rv "$source_dir/"* "$SONIC_ASPEED/$rel_source_dir/" | wc -l
                            KCONFIG_DIR_COUNT=$((KCONFIG_DIR_COUNT + 1))
                        fi
                        set -e
                    fi
                fi
            done <<< "$new_sources"
        fi
    fi
done < "$WORK_DIR/kconfig-check-source.txt"
echo "  Copied $KCONFIG_DIR_COUNT directories referenced by Kconfig source statements"

# Step 5d: Find and copy all Aspeed-related .c and .h files
echo "Step 5d: Copying Aspeed-related source files..."
SOURCE_COUNT=0
find "$ASPEED_SRC/drivers" "$ASPEED_SRC/crypto" "$ASPEED_SRC/arch/arm64" -type f \( -name "*aspeed*.c" -o -name "*aspeed*.h" -o -name "*ast2[567]00*.c" -o -name "*ast2[567]00*.h" -o -name "*ast1[78]00*.c" -o -name "*ast1[78]00*.h" \) 2>/dev/null > "$WORK_DIR/aspeed-source-files.txt"
while IFS= read -r aspeed_file; do
    rel_path="${aspeed_file#$ASPEED_SRC/}"
    sonic_file="$SONIC_SRC/$rel_path"

    # Copy if file doesn't exist in SONiC or is different
    if [ ! -f "$sonic_file" ] || ! diff -q "$sonic_file" "$aspeed_file" > /dev/null 2>&1; then
        mkdir -p "$SONIC_ASPEED/$(dirname $rel_path)"
        cp -v "$aspeed_file" "$SONIC_ASPEED/$rel_path"
        SOURCE_COUNT=$((SOURCE_COUNT + 1))
    fi
done < "$WORK_DIR/aspeed-source-files.txt"
echo "  Copied $SOURCE_COUNT Aspeed source files"

# Step 5e: Copy specific driver directories that contain Aspeed support
# NOTE: Do NOT include drivers/edac here - it's a shared directory with non-Aspeed content
# drivers/edac/Kconfig and Makefile are handled by Steps 5b/5c (smart merge)
# Aspeed-specific .c files are handled by Step 5d
echo "Step 5e: Copying Aspeed-specific driver directories..."
ASPEED_DIRS=(
    "drivers/soc/aspeed"
    "drivers/media/platform/aspeed"
    "drivers/pinctrl/aspeed"
    "drivers/crypto/aspeed"
    "drivers/net/ethernet/faraday"
    # "drivers/edac" - REMOVED: This is a shared directory, handled by Steps 5b/5c/5d
    "drivers/usb/gadget/udc/aspeed-vhub"
)

for dir in "${ASPEED_DIRS[@]}"; do
    if [ -d "$ASPEED_SRC/$dir" ]; then
        echo "  Copying directory: $dir"
        mkdir -p "$SONIC_ASPEED/$dir"
        cp -rv "$ASPEED_SRC/$dir/"* "$SONIC_ASPEED/$dir/" 2>/dev/null | wc -l
    fi
done

# Step 5e1: Copy only Aspeed-specific irqchip drivers (not entire directory)
echo "Step 5e1: Copying Aspeed-specific irqchip drivers..."
IRQCHIP_COUNT=0
if [ -d "$ASPEED_SRC/drivers/irqchip" ]; then
    mkdir -p "$SONIC_ASPEED/drivers/irqchip"
    # Only copy files with "aspeed" in the name
    for irqchip_file in "$ASPEED_SRC/drivers/irqchip/"*aspeed*; do
        if [ -f "$irqchip_file" ]; then
            filename=$(basename "$irqchip_file")
            echo "  Copying Aspeed irqchip: $filename"
            cp -v "$irqchip_file" "$SONIC_ASPEED/drivers/irqchip/$filename"
            IRQCHIP_COUNT=$((IRQCHIP_COUNT + 1))
        fi
    done
    echo "  Copied $IRQCHIP_COUNT Aspeed irqchip drivers"
fi

# Step 5e2: Remove ARM32-only drivers that use ARM32-specific symbols
echo "Step 5e2: Removing ARM32-only drivers..."
# aspeed-sbc.c is AST2600-only and uses arch_debugfs_dir (ARM32-specific symbol)
if [ -f "$SONIC_ASPEED/drivers/soc/aspeed/aspeed-sbc.c" ]; then
    echo "  Removing aspeed-sbc.c (AST2600-only, uses ARM32-specific arch_debugfs_dir)"
    rm -f "$SONIC_ASPEED/drivers/soc/aspeed/aspeed-sbc.c"

    # Also remove the Makefile entry for aspeed-sbc
    if [ -f "$SONIC_ASPEED/drivers/soc/aspeed/Makefile" ]; then
        echo "  Removing aspeed-sbc.o from Makefile"
        sed -i '/obj-\$(CONFIG_ASPEED_SBC)/d' "$SONIC_ASPEED/drivers/soc/aspeed/Makefile"
    fi
fi

# Step 5f: Copy include files
echo "Step 5f: Copying include files..."
find "$ASPEED_SRC/include" -type f \( -name "*aspeed*" -o -name "*ast2700*" -o -name "*ast1700*" -o -name "*ast1800*" -o -name "*ast2600*" -o -name "*ast2500*" \) > "$WORK_DIR/include-files.txt"
while IFS= read -r f; do
    rel_path="${f#$ASPEED_SRC/}"
    mkdir -p "$SONIC_ASPEED/$(dirname $rel_path)"
    cp -v "$f" "$SONIC_ASPEED/$rel_path"
done < "$WORK_DIR/include-files.txt"

# Step 5g: Skip scripts/dtc/include-prefixes (they are symlinks to arch/*/boot/dts)
echo "Step 5g: Skipping DTC include prefixes (symlinks to arch/*/boot/dts)..."
# scripts/dtc/include-prefixes/arm64 -> ../../../arch/arm64/boot/dts
# scripts/dtc/include-prefixes/arm -> ../../../arch/arm/boot/dts
# These are symlinks, so files copied to arch/*/boot/dts will automatically appear here
# Copying them separately would create duplicate entries in the patch

# Step 5h: Smart merge arch/arm64 Kconfig files with Aspeed changes
echo "Step 5h: Smart merging arch/arm64 Kconfig files..."
if [ -f "$ASPEED_SRC/arch/arm64/Kconfig.platforms" ]; then
    if ! diff -q "$SONIC_SRC/arch/arm64/Kconfig.platforms" "$ASPEED_SRC/arch/arm64/Kconfig.platforms" > /dev/null 2>&1; then
        if diff -u "$SONIC_SRC/arch/arm64/Kconfig.platforms" "$ASPEED_SRC/arch/arm64/Kconfig.platforms" | grep -qi "aspeed"; then
            echo "  Smart merging arch/arm64/Kconfig.platforms with Aspeed changes"
            if ! python3 "$SMART_MERGE" "$SONIC_SRC/arch/arm64/Kconfig.platforms" \
                         "$ASPEED_SRC/arch/arm64/Kconfig.platforms" \
                         "$SONIC_ASPEED/arch/arm64/Kconfig.platforms" 2>/dev/null; then
                echo "    Smart merge failed, using full file"
                cp "$ASPEED_SRC/arch/arm64/Kconfig.platforms" "$SONIC_ASPEED/arch/arm64/Kconfig.platforms"
            fi
        fi
    fi
fi

# Step 5i: Smart merge arch/arm64/boot/dts Makefile
echo "Step 5i: Smart merging DTS Makefile..."
if [ -f "$ASPEED_SRC/arch/arm64/boot/dts/Makefile" ]; then
    if ! diff -q "$SONIC_SRC/arch/arm64/boot/dts/Makefile" "$ASPEED_SRC/arch/arm64/boot/dts/Makefile" > /dev/null 2>&1; then
        if diff -u "$SONIC_SRC/arch/arm64/boot/dts/Makefile" "$ASPEED_SRC/arch/arm64/boot/dts/Makefile" | grep -qi "aspeed"; then
            echo "  Smart merging arch/arm64/boot/dts/Makefile with Aspeed changes"
            if ! python3 "$SMART_MERGE" "$SONIC_SRC/arch/arm64/boot/dts/Makefile" \
                         "$ASPEED_SRC/arch/arm64/boot/dts/Makefile" \
                         "$SONIC_ASPEED/arch/arm64/boot/dts/Makefile" 2>/dev/null; then
                echo "    Smart merge failed, using full file"
                cp "$ASPEED_SRC/arch/arm64/boot/dts/Makefile" "$SONIC_ASPEED/arch/arm64/boot/dts/Makefile"
            fi
        fi
    fi
fi

# Step 5j: Detect and copy dependencies of Aspeed files
echo "Step 5j: Detecting and copying dependencies..."
DEPENDENCY_COUNT=0

# Create a list of all Aspeed .c files that were copied
find "$SONIC_ASPEED" -type f -name "*.c" \( -path "*/aspeed/*" -o -name "*aspeed*.c" -o -name "*ast2[567]00*.c" -o -name "*ast1[78]00*.c" \) > "$WORK_DIR/aspeed-c-files.txt"

# For each Aspeed .c file, find #include statements and check if those files are different in Aspeed source
while IFS= read -r aspeed_c_file; do
    # Extract #include statements (both "file.h" and <file.h>)
    grep -h '^#include' "$aspeed_c_file" 2>/dev/null | sed -n 's/^#include [<"]\(.*\)[>"]/\1/p' > "$WORK_DIR/includes-temp.txt" || true

    while IFS= read -r include_file; do
        if [ -n "$include_file" ]; then
            # Try to find the include file in common locations
            for base_dir in "include/linux" "include/trace/events" "include/dt-bindings" "drivers/net/mctp" "include/uapi/linux"; do
                aspeed_include="$ASPEED_SRC/$base_dir/$include_file"
                sonic_include="$SONIC_SRC/$base_dir/$include_file"

                # If the file exists in Aspeed source and is different from SONiC source
                if [ -f "$aspeed_include" ]; then
                    # Check if file doesn't exist in SONiC or is different
                    if [ ! -f "$sonic_include" ] || ! diff -q "$sonic_include" "$aspeed_include" > /dev/null 2>&1; then
                        # Check if the file has Aspeed-related changes or is a dependency
                        set +e
                        # Check if it's a new file or has Aspeed-related content
                        if [ ! -f "$sonic_include" ]; then
                            # New file - check if it has Aspeed references or is in our dependency list
                            if grep -qi "aspeed\|ast2[567]00\|ast1[78]00\|mctp.*pcie.*vdm\|xdma" "$aspeed_include" 2>/dev/null; then
                                echo "  Found dependency (new file): $base_dir/$include_file"
                                mkdir -p "$SONIC_ASPEED/$base_dir"
                                cp -v "$aspeed_include" "$SONIC_ASPEED/$base_dir/$include_file"
                                DEPENDENCY_COUNT=$((DEPENDENCY_COUNT + 1))
                            fi
                        else
                            # Existing file - check if diff has Aspeed-related additions or new flags/definitions
                            # For UAPI headers, also check for new flags, enums, or definitions that might be needed
                            if echo "$base_dir" | grep -q "uapi"; then
                                # For UAPI headers, copy if there are ANY additions (they're API definitions)
                                diff -u "$sonic_include" "$aspeed_include" | grep "^+" | grep -v "^+++" | grep -q "." 2>/dev/null
                                if [ $? -eq 0 ]; then
                                    echo "  Found dependency (modified UAPI): $base_dir/$include_file"
                                    mkdir -p "$SONIC_ASPEED/$base_dir"
                                    cp -v "$aspeed_include" "$SONIC_ASPEED/$base_dir/$include_file"
                                    DEPENDENCY_COUNT=$((DEPENDENCY_COUNT + 1))
                                fi
                            else
                                # For non-UAPI headers, check if diff has Aspeed-related additions
                                diff -u "$sonic_include" "$aspeed_include" | grep "^+" | grep -v "^+++" | grep -qi "aspeed\|ast2[567]00\|ast1[78]00\|devm_clk_hw_register_gate_parent_hw\|mctp.*pcie.*vdm\|xdma" 2>/dev/null
                                if [ $? -eq 0 ]; then
                                    echo "  Found dependency (modified file): $base_dir/$include_file"
                                    mkdir -p "$SONIC_ASPEED/$base_dir"
                                    cp -v "$aspeed_include" "$SONIC_ASPEED/$base_dir/$include_file"
                                    DEPENDENCY_COUNT=$((DEPENDENCY_COUNT + 1))
                                fi
                            fi
                        fi
                        set -e
                    fi
                fi
            done
        fi
    done < "$WORK_DIR/includes-temp.txt"
done < "$WORK_DIR/aspeed-c-files.txt"

# Also check for specific known dependencies
echo "  Checking for known dependencies..."
KNOWN_DEPS=(
    "include/linux/mctp-pcie-vdm.h"
    "drivers/net/mctp/mctp-pcie-vdm.c"
    "include/trace/events/xdma.h"
    "include/linux/clk-provider.h"
    "include/uapi/linux/videodev2.h"
    "drivers/pwm/core.c"
    "include/linux/pwm.h"
)

for dep in "${KNOWN_DEPS[@]}"; do
    aspeed_dep="$ASPEED_SRC/$dep"
    sonic_dep="$SONIC_SRC/$dep"

    if [ -f "$aspeed_dep" ]; then
        # Check if file doesn't exist in SONiC or is different
        if [ ! -f "$sonic_dep" ] || ! diff -q "$sonic_dep" "$aspeed_dep" > /dev/null 2>&1; then
            set +e
            # Check if this is a UAPI file
            is_uapi=0
            echo "$dep" | grep -q "uapi" && is_uapi=1

            # For new files, check if they have relevant content
            if [ ! -f "$sonic_dep" ]; then
                if grep -qi "aspeed\|ast2[567]00\|ast1[78]00\|mctp.*pcie.*vdm\|xdma\|devm_clk_hw_register_gate_parent_hw" "$aspeed_dep" 2>/dev/null; then
                    echo "  Found known dependency (new): $dep"
                    mkdir -p "$SONIC_ASPEED/$(dirname $dep)"
                    cp -v "$aspeed_dep" "$SONIC_ASPEED/$dep"
                    DEPENDENCY_COUNT=$((DEPENDENCY_COUNT + 1))
                fi
            else
                # For existing files, check if diff has relevant additions
                if [ $is_uapi -eq 1 ]; then
                    # For UAPI files, copy if there are ANY additions (they're API definitions)
                    diff -u "$sonic_dep" "$aspeed_dep" | grep "^+" | grep -v "^+++" | grep -q "." 2>/dev/null
                    if [ $? -eq 0 ]; then
                        echo "  Found known dependency (modified UAPI): $dep"
                        mkdir -p "$SONIC_ASPEED/$(dirname $dep)"
                        cp -v "$aspeed_dep" "$SONIC_ASPEED/$dep"
                        DEPENDENCY_COUNT=$((DEPENDENCY_COUNT + 1))
                    fi
                else
                    # For non-UAPI files, check for Aspeed-related additions or PWM API exports
                    diff -u "$sonic_dep" "$aspeed_dep" | grep "^+" | grep -v "^+++" | grep -qi "aspeed\|ast2[567]00\|ast1[78]00\|devm_clk_hw_register_gate_parent_hw\|mctp.*pcie.*vdm\|xdma\|pwm_request_from_chip" 2>/dev/null
                    if [ $? -eq 0 ]; then
                        echo "  Found known dependency (modified): $dep"
                        mkdir -p "$SONIC_ASPEED/$(dirname $dep)"
                        cp -v "$aspeed_dep" "$SONIC_ASPEED/$dep"
                        DEPENDENCY_COUNT=$((DEPENDENCY_COUNT + 1))
                    fi
                fi
            fi
            set -e
        fi
    fi
done

echo "  Copied $DEPENDENCY_COUNT dependency files"

# Step 5k: Summary of what was copied
echo ""
echo "========================================="
echo "Merge Summary:"
echo "========================================="
echo "DTS files: Copied all from arch/arm64/boot/dts/aspeed/ and arch/arm/boot/dts/aspeed/"
echo "Kconfig files: Scanned and copied files with Aspeed changes"
echo "Makefile files: Scanned and copied files with Aspeed changes"
echo "Kconfig source dirs: Copied $KCONFIG_DIR_COUNT directories referenced by new source statements"
echo "Source files: Copied all Aspeed-related .c and .h files"
echo "Driver directories: Copied soc, media, pinctrl, crypto, faraday, edac, aspeed-vhub"
echo "Irqchip drivers: Copied $IRQCHIP_COUNT Aspeed-specific irqchip drivers (not entire directory)"
echo "Include files: Copied all Aspeed/AST* header files"
echo "Dependencies: Copied $DEPENDENCY_COUNT dependency files (headers, drivers, trace events)"
echo "DTC includes: Skipped (symlinks excluded from diff to avoid duplicates)"
echo "========================================="
}  # end merge_aspeed_files

# Build the target tree:
#   - copy sonic-src (already has non-aspeed patches applied)
#   - merge in upstream Aspeed source (replaces what the ###-> aspeed-upstream
#     patches do in baseline)
# Hand-written aspeed-section patches are NOT applied here — they're skipped in
# baseline too, so they cancel by absence.
merge_aspeed_files "target"

# Bail out if any patch failed while building the baseline (or the shared
# non-aspeed base). Those patches feed one side of the diff, so a half-applied
# tree would encode their missing changes as a giant phantom delta.
if [ "$TOTAL_APPLY_FAILURES" -gt 0 ]; then
    echo ""
    echo "========================================="
    echo "ABORT: $TOTAL_APPLY_FAILURES patch(es) failed to apply"
    echo "========================================="
    echo "Tree state is invalid; refusing to produce $OUTPUT_PATCH"
    echo "Look above for 'ERROR:' lines, fix the underlying issue, then re-run."
    rm -f "$OUTPUT_PATCH"
    exit 1
fi

# Step 6: Generate diff (excluding symlinked directories)
DIFF_FROM_DIR="$(basename "$BASELINE_TREE")"
DIFF_TO_DIR="$(basename "$SONIC_ASPEED")"

echo ""
echo "Step 6: Generating diff: $DIFF_FROM_DIR -> $DIFF_TO_DIR"
echo "Note: Excluding scripts/dtc/include-prefixes (symlinks to arch/*/boot/dts)"
cd "$WORK_DIR"
diff -Naur --exclude='.git' --exclude='include-prefixes' "$DIFF_FROM_DIR" "$DIFF_TO_DIR" > "$WORK_DIR/aspeed-changes.diff" || true
echo "Diff created: $(du -sh $WORK_DIR/aspeed-changes.diff | cut -f1)"

# Bail out early on empty diff — nothing to ship.
if [ ! -s "$WORK_DIR/aspeed-changes.diff" ]; then
    rm -f "$OUTPUT_PATCH"
    echo ""
    echo "========================================="
    echo "Diff is empty — baseline already matches the fresh upstream merge."
    echo "Nothing new in $ASPEED_REF relative to the current series state."
    echo "No patch file written."
    echo "========================================="
    exit 0
fi

# Step 7: Convert diff to git format (a/ and b/ prefixes)
echo ""
echo "Step 7: Converting to git patch format..."
sed -e "s|^--- ${DIFF_FROM_DIR}/|--- a/|" \
    -e "s|^+++ ${DIFF_TO_DIR}/|+++ b/|" \
    -e "s|^diff -Naur .* ${DIFF_FROM_DIR}/\(.*\) ${DIFF_TO_DIR}/\1|diff --git a/\1 b/\1|" \
    "$WORK_DIR/aspeed-changes.diff" > "$WORK_DIR/aspeed-changes-git.diff"
echo "Converted to git format: $(du -sh $WORK_DIR/aspeed-changes-git.diff | cut -f1)"

# Step 8: Create git patch with header
echo ""
echo "Step 8: Creating git patch with header..."
cat > "$OUTPUT_PATCH" << PATCH_HEADER
From: ${PATCH_AUTHOR}
Date: $(date -R)
Subject: [PATCH] Aspeed AST2700 incremental update

Incremental patch capturing the delta between the currently-committed
patches-sonic/ series state and a fresh Aspeed upstream merge at
${ASPEED_REF}. Stage by appending inside the existing ###-> aspeed-upstream
sub-section in patches-sonic/series (after the prior upstream patches, before
###-> aspeed-upstream-end), so it stays ahead of the hand-written patches.

Signed-off-by: ${PATCH_AUTHOR}
---
PATCH_HEADER

cat "$WORK_DIR/aspeed-changes-git.diff" >> "$OUTPUT_PATCH"
echo "Git patch created: $(du -sh $OUTPUT_PATCH | cut -f1)"

# Step 9: Generate statistics
echo ""
echo "========================================="
echo "Patch Generation Complete!"
echo "========================================="
echo "Output patch: $OUTPUT_PATCH"
echo "Patch size: $(du -sh $OUTPUT_PATCH | cut -f1)"
echo "Total lines: $(wc -l < $OUTPUT_PATCH)"
echo "Files changed: $(grep -c '^diff --git' $OUTPUT_PATCH)"
echo ""
echo "Sample of changed files:"
grep '^diff --git' "$OUTPUT_PATCH" | head -20
echo ""

echo "To stage the incremental patch:"
echo "  # 1. Rename to reflect what the bump represents, e.g."
echo "  #      aspeed-ast2700-v00.07.02-to-v00.07.03.patch"
echo "  cp $OUTPUT_PATCH $KERNEL_DIR/patches-sonic/<new-name>.patch"
echo ""
echo "  # 2. Append the new patch INSIDE the existing ###-> aspeed-upstream"
echo "  #    sub-section in patches-sonic/series (after the prior upstream"
echo "  #    patches, before ###-> aspeed-upstream-end). It must stay ahead of"
echo "  #    the hand-written patches, and it applies on top of the upstream"
echo "  #    patches the diff was generated against. Example:"
echo "  #"
echo "  #      ###-> aspeed"
echo "  #      ###-> aspeed-upstream"
echo "  #      aspeed-ast2700-support.patch"
echo "  #      <new-name>.patch              <-- new"
echo "  #      ###-> aspeed-upstream-end"
echo "  #      <vendor>-board-dts.patch"
echo "  #      ###-> aspeed-end"
echo ""

