#!/usr/bin/env python3
# Splits the monolithic Aspeed incremental patch produced by
# generate-aspeed-patch.sh into the Rule-B 3-part set. Intended as the
# post-generation step of a baseline reset (SONiC kernel version bump), where a
# fresh full merge replaces 0001/0002/0003 rather than appending an increment.
#
# Rule B (per changed file):
#   0001 - anything under the Aspeed namespace (path matches aspeed/astNN00),
#          both new files and modifications to Aspeed drivers already upstream.
#   0002 - brand-new files outside the Aspeed namespace (self-contained
#          dependency subsystems, e.g. i3c/mctp, jtag, dwc trng).
#   0003 - modifications to existing shared/common kernel files (the
#          vendor-independent review surface, e.g. pwm/core.c, Kconfig merges).
#
# All configuration mirrors generate-aspeed-patch.sh and is overridable via the
# environment so the two scripts share one source of truth:
#   INPUT_PATCH, WORK_DIR, OUTDIR, KERNEL_VERSION, ASPEED_REF/ASPEED_TAG,
#   PATCH_AUTHOR, PATCH_DATE
import os
import re
import subprocess
import sys
from email.utils import formatdate

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KERNEL_DIR = os.path.dirname(SCRIPT_DIR)

ASPEED_RE = re.compile(r"aspeed|ast1[0-9]00|ast2[0-9]00", re.I)


def _git_config(key):
    try:
        out = subprocess.run(["git", "-C", KERNEL_DIR, "config", key],
                             capture_output=True, text=True, check=False)
        return out.stdout.strip()
    except OSError:
        return ""


def _kernel_version():
    v = os.environ.get("KERNEL_VERSION", "").strip()
    if v:
        return v
    mk = os.path.join(KERNEL_DIR, "Makefile")
    with open(mk, "r", errors="surrogateescape") as fh:
        for line in fh:
            m = re.match(r"^KERNEL_VERSION[ \t]*[?:+]?=[ \t]*(.+?)[ \t]*$", line)
            if m:
                return m.group(1).strip()
    return "unknown"


def _author():
    a = os.environ.get("PATCH_AUTHOR", "").strip()
    if a:
        return a
    name, email = _git_config("user.name"), _git_config("user.email")
    if name and email:
        return f"{name} <{email}>"
    sys.exit("ERROR: no PATCH_AUTHOR and git user.name/user.email unset")


def _config():
    work = os.environ.get("WORK_DIR") or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "aspeed-patch-gen")
    ref = (os.environ.get("ASPEED_REF") or os.environ.get("ASPEED_TAG") or "").strip()
    if not ref:
        ref = "unknown-ref"
        sys.stderr.write("WARNING: ASPEED_REF/ASPEED_TAG unset; using 'unknown-ref'\n")
    return {
        "src": os.environ.get("INPUT_PATCH") or os.path.join(
            work, "aspeed-ast2700-incremental.patch"),
        "outdir": os.environ.get("OUTDIR") or os.path.join(work, "aspeed-split"),
        "kver": _kernel_version(),
        "ref": ref,
        "sfx": ref.lstrip("v"),
        "author": _author(),
        "date": os.environ.get("PATCH_DATE") or formatdate(localtime=True),
    }


def _buckets(cfg):
    kver, sfx, ref = cfg["kver"], cfg["sfx"], cfg["ref"]
    return {
        "0001": {
            "file": f"{cfg['outdir']}/0001-aspeed-ast2700-support.patch",
            "subject": "Aspeed AST2700 support: Aspeed-namespace files (new and updated)",
            "body": ("All files under the Aspeed namespace (new files plus updates to\n"
                     "Aspeed drivers already present upstream), from a fresh Aspeed\n"
                     f"merge at {ref} on the {kver} SONiC kernel."),
            "lines": [],
        },
        "0002": {
            "file": f"{cfg['outdir']}/0002-aspeed-ast2700-non-aspeed-deps-update-to-{kver}-{sfx}.patch",
            "subject": "Aspeed AST2700 support: new non-Aspeed dependency subsystems",
            "body": ("New, self-contained non-Aspeed subsystems the Aspeed image depends\n"
                     "on (i3c/mctp, jtag, dwc TRNG, mctp-pcie-vdm, xdma trace), from a\n"
                     f"fresh Aspeed merge at {ref} on the {kver} SONiC kernel."),
            "lines": [],
        },
        "0003": {
            "file": f"{cfg['outdir']}/0003-aspeed-ast2700-shared-kernel-changes-update-to-{kver}-{sfx}.patch",
            "subject": "Aspeed AST2700 support: modifications to shared/common kernel files",
            "body": ("Modifications to existing vendor-independent (shared/common) kernel\n"
                     "files - the common-code review surface (Kconfig/Makefile merges,\n"
                     "ftgmac100, pwm/core, clk-provider, pwm.h, videodev2.h), from a\n"
                     f"fresh Aspeed merge at {ref} on the {kver} SONiC kernel."),
            "lines": [],
        },
    }


def _header(cfg, b):
    return (f"From: {cfg['author']}\n"
            f"Date: {cfg['date']}\n"
            f"Subject: [PATCH] {b['subject']}\n\n"
            f"{b['body']}\n\n"
            f"Signed-off-by: {cfg['author']}\n"
            "---\n")


def _classify(path, is_new):
    if ASPEED_RE.search(path):
        return "0001"
    return "0002" if is_new else "0003"


def main():
    cfg = _config()
    if not os.path.isfile(cfg["src"]):
        sys.exit(f"ERROR: input patch not found: {cfg['src']}")
    os.makedirs(cfg["outdir"], exist_ok=True)
    buckets = _buckets(cfg)
    with open(cfg["src"], "r", errors="surrogateescape") as fh:
        lines = fh.readlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("diff --git ")), None)
    if start is None:
        sys.exit(f"ERROR: no 'diff --git' blocks found in {cfg['src']}")
    blocks, cur = [], None
    for l in lines[start:]:
        if l.startswith("diff --git "):
            if cur is not None:
                blocks.append(cur)
            cur = [l]
        else:
            cur.append(l)
    if cur is not None:
        blocks.append(cur)
    counts = {"0001": 0, "0002": 0, "0003": 0}
    for blk in blocks:
        path = re.match(r"diff --git a/(.*?) b/", blk[0]).group(1)
        is_new = next((x for x in blk if x.startswith("@@ ")), "").startswith("@@ -0,0")
        key = _classify(path, is_new)
        buckets[key]["lines"].extend(blk)
        counts[key] += 1
    for key, b in buckets.items():
        with open(b["file"], "w", errors="surrogateescape") as out:
            out.write(_header(cfg, b))
            out.writelines(b["lines"])
        print(f"{key}: {counts[key]:3d} files -> {b['file']}")
    print(f"TOTAL: {sum(counts.values())} files")
    print(f"\nCopy the three files into {KERNEL_DIR}/patches-sonic/ and refresh the\n"
          "###-> aspeed-upstream block in patches-sonic/series.")


if __name__ == "__main__":
    main()
