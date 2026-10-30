#!/usr/bin/env python3
"""
License Guard
Scans a git diff (and, for known copyrighted artwork, the whole tree) for changes that would strip or replace ANY upstream copyright
notice (Dortania, Dhinak G / Mykola Grymalyuk, ASentientBot, dosdude1, Apple, ...)
or the license text itself, e.g.
    - Copyright (c) 2020-2026 Dortania
    + Copyright (c) 2020-2026 Albert
Year bumps are ignored. Adding your own copyright *next to* an existing one is fine
and only reported as info.

Usage: license_guard.py <git-diff-range>     e.g. "abc123...pr-head"
Env:   UPSTREAM_HOLDER (default "Dortania"), REPORT_PATH (default license-report.md)
"""
import fnmatch
import io
import os
import re
import subprocess
import sys
from collections import Counter

HOLDER = os.environ.get("UPSTREAM_HOLDER", "Dortania")
REPORT = os.environ.get("REPORT_PATH", "license-report.md")
MARKER = "<!-- license-guard -->"
DIFF_RANGE = sys.argv[1]

# A copyright *notice*: "©", "(c) 2020", or the word "Copyright" followed by a year or a name
# ("Copyright 2020 ...", "Copyright (c) Apple", "Copyright © Dortania"). Prose like "copyright label",
# comments like "Text: Copyright" and identifiers like AuthorizationCopyRights don't count.
COPYRIGHT_RE = re.compile(
    r"©|\([cC]\)\s*\d|(?<![A-Za-z_])(?i:copyright)(?![A-Za-z_])\s*(?:\([cC]\)|©)?\s*(?:\d{4}|[A-Z][A-Za-z])")
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*(.+?)\s*(?:\*/|-->|$)", re.I)
LICENSE_FILE_RE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)(\.(txt|md|rst))?$", re.I)
# The guards' own files (this script and the workflows, incl. attribution-guard.yml) quote
# copyright notices in their patterns and docs - don't scan them.
def is_self(path):
    return (path in (".github/scripts/license_guard.py", RELOCATED_FILE)
            or path.startswith(".github/workflows/"))


# Reviewed deletions: paths (glob patterns) listed here don't raise the "file with an upstream
# copyright notice was deleted" warning. Only that warning - violations are never suppressed.
# Read from the checked-out base branch, so a PR can't allowlist its own changes.
ALLOW_FILE = ".github/license-guard-allow.txt"


def load_allow():
    try:
        with open(ALLOW_FILE, encoding="utf-8") as fh:
            return [l.split("#", 1)[0].strip() for l in fh if l.split("#", 1)[0].strip()]
    except FileNotFoundError:
        return []
# Relocated notices: files deleted here because they (and their notices) now live elsewhere,
# e.g. the website that moved to its own repo. "<glob> -> <destination>" per line. Only full
# deletions of matching files are accepted; edits to notices are still checked as usual.
RELOCATED_FILE = ".github/relocated-notices.txt"


def load_relocated():
    entries = []
    try:
        with open(RELOCATED_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if "->" not in line:
                    continue
                pattern, dest = (part.strip() for part in line.split("->", 1))
                if pattern and dest:
                    entries.append((pattern, dest))
    except FileNotFoundError:
        pass
    return entries


# Known copyrighted artwork. Unlike the checks above, this scans EVERY file in the audited
# tree (not just the diff), so an old copy anywhere in the fork is found too.
# Only fingerprints are stored here, never the images themselves.
# Fingerprint = 256-bit dHash (16x16) of the image composited on grey, white and black,
# so resized, recompressed or flattened copies still match.
KNOWN_ART = {
    "dortania-oclp-icon": {
        "name": "Dortania's OpenCore Legacy Patcher icon",
        "holder": "Dortania",
        "hashes": [
            "2002461d90f6233b0e0f18a718f71967196718e71ae70f8f271b90f6469c2002",
            "2002469d90fe233b0e0f18a718f7196719671ce71be70d8f233b10f60c0c0000",
            "2002471d88ce33332e0b0a271cf7186719671ce71aa70fcf231b90f60e8c0000",
            "20284e8030f063384e0cd8a498f6996699669ce4dbe4cd8ce33870f1bc02417c",
            "20304e1c90f6233b0e0f18a718f61966196618e61ae54f8d271bd0f26e840030",
            "3002471c88ce33332e0b1a271cf7186799669ce6daa4cfcce31870f13e820024",
            "4004371068c9d334ee08da24dcf4d864d964dce4daa4cfccc31c70f13e800000",
            "40043e8070f1e338ce0cd8a4d8f4d964d964d8e4dae4cf8ce71870f13e804004",
            "4004be8270f1e338ce0cd8a4d8f4d964d964dce4dbe4cd8ce33870f1bc024004",
        ],
    },
    "apple-system-settings-icon": {
        "name": "Apple's System Settings icon",
        "holder": "Apple",
        "hashes": [
            "200243bd8cce30f3231b2b4b09af1c8f1c8f09af2b4b231b30f38cc6001c2002",
            "300243bc9cce30f3231b2b4b09af5c8d5c8d49adcb49e319f0f17cc038000040",
            "40043b607cc1e0f0e31ccb4cc9acdc8cdc8cc9accb4cc31cc0f87cc138004004",
        ],
    },
}
# Hamming distance (of 256 bits). Measured on this repo: copies 0-17, icons that embed
# the logo 39-51, unrelated icons 59+.
ART_COPY_MAX = 30      # -> violation
ART_DERIVED_MAX = 52   # -> warning (probably contains the artwork, check by hand)
ART_MAX_BYTES = 20_000_000
IMAGE_EXT = (".png", ".icns", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".ico")
TEXT_EXT = (".md", ".markdown", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".css",
            ".scss", ".py", ".plist", ".json", ".yml", ".yaml", ".txt", ".rst")
# Dortania's images used directly from their repo or website (e.g. the old README logo).
HOTLINK_RE = re.compile(
    r"(?:raw\.githubusercontent\.com/dortania/|github\.com/dortania/[^\s\"'()<>]*/(?:raw|blob)/"
    r"|dortania\.github\.io/)[^\s\"'()<>]*\.(?:png|icns|svg|jpe?g|gif|webp)", re.I)


def dhash256(data, bg):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        im = im.convert("RGBA")
    base = Image.new("RGBA", im.size, bg + (255,))
    base.alpha_composite(im)
    g = base.convert("L").resize((17, 16), Image.LANCZOS).tobytes()
    bits = 0
    for r in range(16):
        for c in range(16):
            bits = (bits << 1) | (g[r * 17 + c] > g[r * 17 + c + 1])
    return bits


def scan_artwork(head, add):
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 50_000_000
    except ImportError:
        add("warning", "-", "Artwork check skipped: Pillow is not installed")
        return
    known = {k: [int(h, 16) for h in v["hashes"]] for k, v in KNOWN_ART.items()}
    for row in git("ls-tree", "-r", "-l", head).splitlines():
        meta, path = row.split("\t", 1)
        _, kind, sha, size = meta.split()
        if kind != "blob" or is_self(path) or size == "-" or int(size) > ART_MAX_BYTES:
            continue
        low = path.lower()
        if low.endswith(IMAGE_EXT):
            data = subprocess.run(["git", "cat-file", "blob", sha], capture_output=True, check=True).stdout
            try:
                h = dhash256(data, (128, 128, 128))
            except Exception:
                continue  # not a decodable image
            for key, hashes in known.items():
                d = min(bin(h ^ x).count("1") for x in hashes)
                art = KNOWN_ART[key]
                if d <= ART_COPY_MAX:
                    add("violation", path, f"Copyrighted artwork: {art['name']}",
                        [f"  Matches {art['holder']}'s artwork (distance {d}/256).",
                         "  Replace it with your own artwork."])
                elif d <= ART_DERIVED_MAX:
                    add("warning", path, f"Probably contains {art['name']}",
                        [f"  Similar to {art['holder']}'s artwork (distance {d}/256).",
                         "  Check by hand; if it embeds the artwork, replace it."])
        elif low.endswith(TEXT_EXT):
            text = git("cat-file", "blob", sha)
            urls = sorted(set(m.group(0) for m in HOTLINK_RE.finditer(text)))
            if urls:
                add("violation", path, "Dortania artwork used from Dortania's servers",
                    [f"- {u}" for u in urls[:10]])


YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True,
                          encoding="utf-8", errors="replace").stdout


def norm(line):
    """Normalise whitespace and years so a pure year bump isn't reported."""
    return YEAR_RE.sub("YYYY", " ".join(line.split())).lower()


def core(line):
    """The notice itself, from where it starts up to the end of the string/tag it sits in,
    so `str = "Copyright © 2020 X"` and `<string>Copyright © X</string>` reduce to the notice."""
    m = COPYRIGHT_RE.search(line)
    return norm(re.split(r"[\"'<]", line[m.start():] if m else line)[0])


def show(line):
    line = line.strip().replace("`", "'")
    return line if len(line) <= 200 else line[:200] + "…"


def parse_diff(text):
    files, cur, in_hunk = [], None, False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            cur = {"old": None, "new": None, "add": [], "rem": [], "deleted": False}
            files.append(cur)
            in_hunk = False
        elif cur is None:
            continue
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk:
            if line.startswith("deleted file mode"):
                cur["deleted"] = True
            elif line.startswith("--- "):
                cur["old"] = None if line[4:] == "/dev/null" else line[6:]
            elif line.startswith("+++ "):
                cur["new"] = None if line[4:] == "/dev/null" else line[6:]
        elif line.startswith("+"):
            cur["add"].append(line[1:])
        elif line.startswith("-"):
            cur["rem"].append(line[1:])
    return files


def main():
    diff = git("diff", "-M", "-U0", "--no-color", "--no-ext-diff", DIFF_RANGE)
    findings = []  # (severity, path, title, [detail lines])
    allow = load_allow()
    relocated = load_relocated()

    def add(sev, path, title, details=()):
        findings.append((sev, path, title, list(details)))

    for f in parse_diff(diff):
        path = f["new"] or f["old"]
        if not path or is_self(path):
            continue
        is_license = bool(LICENSE_FILE_RE.search(path))
        rem_cr = [l for l in f["rem"] if COPYRIGHT_RE.search(l)]
        add_cr = [l for l in f["add"] if COPYRIGHT_RE.search(l)]

        if f["deleted"]:
            dest = next((d for p, d in relocated if fnmatch.fnmatch(path, p)), None)
            if dest:
                if is_license or rem_cr:
                    add("info", path, "Deleted here, notices relocated", [f"  Now at: {dest}"])
                continue
            if is_license:
                add("violation", path, "License file deleted")
            elif rem_cr and not any(fnmatch.fnmatch(path, a) for a in allow):
                add("warning", path, "File with an upstream copyright notice was deleted",
                    ["  Fine if the code is really gone. Not fine if it was moved",
                     "  or re-added elsewhere without the notice.",
                     f"  If the deletion is intentional, add the path to {ALLOW_FILE}."])
            continue

        # 1) Upstream copyright notice removed or replaced (any holder; year bumps ignored)
        # A notice counts as kept if its text still appears in some added line, so
        # "© 2020 Dortania" -> "© 2020 Dortania · fork © 2026 Albert" is fine.
        added_n = [norm(l) for l in f["add"]]
        removed_n = [norm(l) for l in f["rem"]]
        lost = [l for l in rem_cr if not any(core(l) in a for a in added_n)]
        new = [l for l in add_cr if not any(core(l) in r for r in removed_n)]
        if lost:
            add("violation", path,
                f"Upstream copyright notice {'replaced' if new else 'removed'}",
                [f"- {show(l)}" for l in lost] + [f"+ {show(l)}" for l in new])
        elif new:
            add("info", path, "Additional copyright added (upstream notice kept)",
                [f"+ {show(l)}" for l in new])

        # 2) SPDX identifier changed or removed
        rem_ids = Counter(m.group(1) for l in f["rem"] if (m := SPDX_RE.search(l)))
        add_ids = Counter(m.group(1) for l in f["add"] if (m := SPDX_RE.search(l)))
        if rem_ids - add_ids:
            add("violation", path, "SPDX license identifier changed or removed",
                [f"- {i}" for i in rem_ids - add_ids] + [f"+ {i}" for i in add_ids - rem_ids])

        # 3) License text itself changed (year bumps are ignored)
        if is_license:
            body = lambda ls: [l for l in ls if l.strip() and not COPYRIGHT_RE.search(l)]
            rem_n = Counter(norm(l) for l in body(f["rem"]))
            add_n = Counter(norm(l) for l in body(f["add"]))
            gone = [l for l in body(f["rem"]) if (rem_n - add_n)[norm(l)]]
            if gone:
                add("violation", path, "License text removed or altered",
                    [f"- {show(l)}" for l in gone[:15]] + (["  …"] if len(gone) > 15 else []))
            elif add_n - rem_n:
                add("info", path, "Lines added to license file",
                    [f"+ {show(l)}" for l in f["add"] if l.strip()][:15])


    # 4) Known copyrighted artwork anywhere in the audited tree
    scan_artwork(re.split(r"\.\.\.?", DIFF_RANGE)[-1], add)

    v = [x for x in findings if x[0] == "violation"]
    w = [x for x in findings if x[0] == "warning"]
    i = [x for x in findings if x[0] == "info"]

    out = [MARKER, "## 🛡️ License Guard", ""]
    if not v and not w:
        out.append(f"✅ No changes found that would affect upstream copyright notices or license text (`{DIFF_RANGE}`), and no known copyrighted artwork.")
    else:
        out.append(f"Checked `{DIFF_RANGE}`: **{len(v)} violation(s)**, {len(w)} warning(s), {len(i)} info.")
    def section(items):
        res = []
        for _, path, title, details in items:
            res.append(f"**{title}** — `{path}`")
            if details:
                res += ["```diff", *details, "```"]
        return res

    for sev, emoji, items in (("Violations", "❌", v), ("Warnings", "⚠️", w)):
        if items:
            out += ["", f"### {emoji} {sev}", *section(items)]
    if i:
        # Info is your own additions next to kept notices and relocated files - collapsed, nothing to fix.
        out += ["", f"<details><summary>ℹ️ Info ({len(i)}): copyright lines you added next to kept "
                "upstream notices, relocated files - nothing to fix</summary>", "", *section(i), "", "</details>"]
    out += ["", "<details><summary>Why this matters</summary>", "",
            f"{HOLDER}'s code is under licenses that require keeping every existing copyright notice and "
            "the license text in redistributed source (e.g. BSD 3-Clause, clause 1). Adding your own "
            "copyright line *alongside* the existing ones is fine; replacing or removing them is not. "
            "Year bumps like `2020-2024` → `2020-2026` are ignored.", "",
            "Artwork such as Dortania's OpenCore Legacy Patcher icon or Apple's System Settings icon is "
            "not covered by the code license and must not be shipped or hotlinked; use your own artwork.",
            "", "</details>"]

    report = "\n".join(out) + "\n"
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(report)
    for var, text in (("GITHUB_OUTPUT", f"violations={len(v)}\nwarnings={len(w)}\n"),
                      ("GITHUB_STEP_SUMMARY", report)):
        if os.environ.get(var):
            with open(os.environ[var], "a", encoding="utf-8") as fh:
                fh.write(text)


if __name__ == "__main__":
    main()
