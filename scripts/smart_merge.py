#!/usr/bin/env python3
"""
Smart merge for Kconfig/Makefile files.
Uses a common ancestor approach: extract Aspeed additions from diff,
add them to SONiC base at correct locations while preserving order.
"""
import sys
import re
import difflib

def smart_merge(sonic_file, aspeed_file, target_file):
    # Aspeed-related patterns
    aspeed_patterns = [
        re.compile(r'aspeed', re.IGNORECASE),
        re.compile(r'ast2[567]00', re.IGNORECASE),
        re.compile(r'ast1[78]00', re.IGNORECASE),
        re.compile(r'AST2700_IRQ'),
        re.compile(r'ARCH_ASPEED'),
    ]

    def is_aspeed_related(text):
        return any(pattern.search(text) for pattern in aspeed_patterns)

    # Read files
    with open(sonic_file, 'r') as f:
        sonic_lines = f.readlines()

    with open(aspeed_file, 'r') as f:
        aspeed_lines = f.readlines()

    # Use difflib to get a proper sequence matcher
    matcher = difflib.SequenceMatcher(None, sonic_lines, aspeed_lines)

    result = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            # Common lines - keep them
            result.extend(sonic_lines[i1:i2])
        elif tag == 'delete':
            # Lines deleted in aspeed - KEEP THEM (preserve SONiC content)
            result.extend(sonic_lines[i1:i2])
        elif tag == 'insert':
            # Lines added in aspeed - check if Aspeed-related
            new_lines = aspeed_lines[j1:j2]
            aspeed_related = any(is_aspeed_related(line) for line in new_lines)
            if aspeed_related:
                # Add these lines
                result.extend(new_lines)
            # Otherwise skip (non-Aspeed additions)
        elif tag == 'replace':
            # Lines changed - keep SONiC version, then add Aspeed additions if any
            result.extend(sonic_lines[i1:i2])
            new_lines = aspeed_lines[j1:j2]
            aspeed_related = any(is_aspeed_related(line) for line in new_lines)
            if aspeed_related:
                # Also add the Aspeed replacements
                result.extend(new_lines)

    # Write result
    with open(target_file, 'w') as f:
        f.writelines(result)

if __name__ == '__main__':
    smart_merge(sys.argv[1], sys.argv[2], sys.argv[3])
