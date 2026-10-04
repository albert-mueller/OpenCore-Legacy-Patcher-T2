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
import os
import re
import subprocess
import sys
from collections import Counter

HOLDER = os.environ.get("UPSTREAM_HOLDER", "Dortania")
REPORT = os.environ.get("REPORT_PATH", "license-report.md")
MARKER = "<!-- license-guard -->"
DIFF_RANGE = sys.argv[1]

# "copyright" as a whole word (not AuthorizationCopyRights / copyright_date), ©, or "(c) 2020"
COPYRIGHT_RE = re.compile(r"(?<![A-Za-z_])copyright(?![A-Za-z_])|©|\(c\)\s*\d", re.I)
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*(.+?)\s*(?:\*/|-->|$)", re.I)
LICENSE_FILE_RE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)(\.(txt|md|rst))?$", re.I)
# The guard's own files talk about copyrights all the time - don't scan them.
SELF_PATHS = {".github/scripts/license_guard.py", ".github/workflows/license-guard.yml"}
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True,
                          encoding="utf-8", errors="replace").stdout


def norm(line):
    """Normalise whitespace and years so a pure year bump isn't reported."""
    return YEAR_RE.sub("YYYY", " ".join(line.split())).lower()


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

    def add(sev, path, title, details=()):
        findings.append((sev, path, title, list(details)))

    for f in parse_diff(diff):
        path = f["new"] or f["old"]
        if not path or path in SELF_PATHS:
            continue
        is_license = bool(LICENSE_FILE_RE.search(path))
        rem_cr = [l for l in f["rem"] if COPYRIGHT_RE.search(l)]
        add_cr = [l for l in f["add"] if COPYRIGHT_RE.search(l)]

        if f["deleted"]:
            if is_license:
                add("violation", path, "License file deleted")
            elif rem_cr:
                add("warning", path, "File with an upstream copyright notice was deleted",
                    ["  Fine if the code is really gone. Not fine if it was moved",
                     "  or re-added elsewhere without the notice."])
            continue

        # 1) Upstream copyright notice removed or replaced (any holder; year bumps ignored)
        lost_n = Counter(norm(l) for l in rem_cr) - Counter(norm(l) for l in add_cr)
        new_n = Counter(norm(l) for l in add_cr) - Counter(norm(l) for l in rem_cr)
        lost = [l for l in rem_cr if lost_n[norm(l)]]
        new = [l for l in add_cr if new_n[norm(l)]]
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
    for sev, emoji, items in (("Violations", "❌", v), ("Warnings", "⚠️", w), ("Info", "ℹ️", i)):
        if not items:
            continue
        out += ["", f"### {emoji} {sev}"]
        for _, path, title, details in items:
            out.append(f"**{title}** — `{path}`")
            if details:
                out += ["```diff", *details, "```"]
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
