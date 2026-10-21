#!/usr/bin/env python3
"""Scan a file or directory for keyword matches, ignoring comments.

Used by generate-aspeed-patch.sh to decide whether a new file/directory is
genuinely Aspeed-related. A plain `grep -ri aspeed` also matches attribution
comments such as "Based on aspeed-socinfo.c", which pulls in unrelated files
(e.g. drivers/soc/vt8500). This helper strips comments before matching so that
only real code/config references count.

Usage:
    aspeed_content_scan.py <path> [regex]

Exit status:
    0 - at least one non-comment match found
    1 - no non-comment match found (or path missing)
"""

import os
import re
import sys

DEFAULT_REGEX = r"aspeed|ast2[567]00|ast1[78]00"

# Extensions / filenames that use C-style comments (/* */ and //).
C_STYLE_EXT = {".c", ".h", ".dts", ".dtsi", ".dtso", ".S", ".s"}
# Extensions / filenames that use hash (#) comments.
HASH_STYLE_EXT = {".yaml", ".yml", ".txt", ".rst", ".mk", ".defconfig",
                  ".config", ".sh", ".py"}
HASH_STYLE_NAMES = {"Kconfig", "Makefile", "Kbuild"}

_C_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_C_LINE = re.compile(r"//[^\n]*")
_HASH_LINE = re.compile(r"#[^\n]*")


def _strip_c_comments(text):
    text = _C_BLOCK.sub(" ", text)
    text = _C_LINE.sub(" ", text)
    return text


def _strip_hash_comments(text):
    return _HASH_LINE.sub(" ", text)


def _style_for(path):
    base = os.path.basename(path)
    if base in HASH_STYLE_NAMES or base.startswith("Kconfig") or \
            base.startswith("Makefile"):
        return "hash"
    ext = os.path.splitext(base)[1]
    if ext in C_STYLE_EXT:
        return "c"
    if ext in HASH_STYLE_EXT:
        return "hash"
    # Unknown: strip C-style only. This is the safe default because stripping
    # '#' from an unknown C-like file would remove #include/#define lines that
    # may legitimately reference an aspeed header.
    return "c"


def _strip_comments(path, text):
    style = _style_for(path)
    if style == "hash":
        return _strip_hash_comments(text)
    return _strip_c_comments(text)


def _file_matches(path, pattern):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except (OSError, IOError):
        return False
    stripped = _strip_comments(path, text)
    return pattern.search(stripped) is not None


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: aspeed_content_scan.py <path> [regex]\n")
        return 2
    target = argv[1]
    regex = argv[2] if len(argv) > 2 else DEFAULT_REGEX
    pattern = re.compile(regex, re.IGNORECASE)

    if os.path.isfile(target):
        return 0 if _file_matches(target, pattern) else 1

    if os.path.isdir(target):
        for root, _dirs, files in os.walk(target):
            for name in files:
                if _file_matches(os.path.join(root, name), pattern):
                    return 0
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
