#!/usr/bin/env python3
"""
License Guard
Scans a git diff for changes that would strip or replace ANY upstream copyright
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
    return path == ".github/scripts/license_guard.py" or path.startswith(".github/workflows/")


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

    v = [x for x in findings if x[0] == "violation"]
    w = [x for x in findings if x[0] == "warning"]
    i = [x for x in findings if x[0] == "info"]

    out = [MARKER, "## 🛡️ License Guard", ""]
    if not v and not w:
        out.append(f"✅ No changes found that would affect upstream copyright notices or license text (`{DIFF_RANGE}`).")
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
        # Info is just your own additions next to kept notices - collapsed, nothing to fix.
        out += ["", f"<details><summary>ℹ️ Info ({len(i)}): copyright lines you added next to kept "
                "upstream notices - nothing to fix</summary>", "", *section(i), "", "</details>"]
    out += ["", "<details><summary>Why this matters</summary>", "",
            f"{HOLDER}'s code is under licenses that require keeping every existing copyright notice and "
            "the license text in redistributed source (e.g. BSD 3-Clause, clause 1). Adding your own "
            "copyright line *alongside* the existing ones is fine; replacing or removing them is not. "
            "Year bumps like `2020-2024` → `2020-2026` are ignored.",
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
