#!/usr/bin/env python3
"""
License Guard
Scans a git diff for changes that would strip or replace the upstream
(Dortania) copyright notice / license text, e.g.
    - Copyright (c) 2020-2026 Dortania
    + Copyright (c) 2020-2026 Albert
Adding your own copyright *next to* Dortania's is fine and only reported as info.

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

COPYRIGHT_RE = re.compile(r"copyright|\(c\)|©", re.I)
HOLDER_RE = re.compile(re.escape(HOLDER), re.I)
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*(.+?)\s*(?:\*/|-->|$)", re.I)
LICENSE_FILE_RE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)([._-][\w.-]*)?$", re.I)
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
        if not path:
            continue
        is_license = bool(LICENSE_FILE_RE.search(path))

        def protected(line):
            return HOLDER_RE.search(line) and (COPYRIGHT_RE.search(line) or is_license)

        if f["deleted"]:
            if is_license:
                add("violation", path, "License file deleted")
            elif any(protected(l) for l in f["rem"]):
                add("warning", path, f"File with a {HOLDER} copyright notice was deleted",
                    ["  Fine if the code is really gone. Not fine if it was moved",
                     "  or re-added elsewhere without the notice."])
            continue

        # 1) Dortania notice removed or replaced
        rem_prot = [l for l in f["rem"] if protected(l)]
        add_prot = [l for l in f["add"] if protected(l)]
        other_cr = [l for l in f["add"] if COPYRIGHT_RE.search(l) and not HOLDER_RE.search(l)]
        if len(rem_prot) > len(add_prot):
            add("violation", path,
                f"{HOLDER} copyright notice {'replaced' if other_cr else 'removed'}",
                [f"- {show(l)}" for l in rem_prot] + [f"+ {show(l)}" for l in other_cr])
        elif other_cr:
            add("info", path, f"Additional copyright added ({HOLDER}'s notice kept)",
                [f"+ {show(l)}" for l in other_cr])

        # 2) SPDX identifier changed or removed
        rem_ids = Counter(m.group(1) for l in f["rem"] if (m := SPDX_RE.search(l)))
        add_ids = Counter(m.group(1) for l in f["add"] if (m := SPDX_RE.search(l)))
        if rem_ids - add_ids:
            add("violation", path, "SPDX license identifier changed or removed",
                [f"- {i}" for i in rem_ids - add_ids] + [f"+ {i}" for i in add_ids - rem_ids])

        # 3) License text itself changed (year bumps are ignored)
        if is_license:
            rem_n = Counter(norm(l) for l in f["rem"] if l.strip() and not HOLDER_RE.search(l))
            add_n = Counter(norm(l) for l in f["add"] if l.strip() and not HOLDER_RE.search(l))
            lost = [l for l in f["rem"] if l.strip() and not HOLDER_RE.search(l)
                    and (rem_n - add_n)[norm(l)]]
            if lost:
                add("violation", path, "License text removed or altered",
                    [f"- {show(l)}" for l in lost[:15]] + (["  …"] if len(lost) > 15 else []))
            elif add_n - rem_n and not other_cr:
                add("info", path, "Lines added to license file",
                    [f"+ {show(l)}" for l in f["add"] if l.strip()][:15])

    v = [x for x in findings if x[0] == "violation"]
    w = [x for x in findings if x[0] == "warning"]
    i = [x for x in findings if x[0] == "info"]

    out = [MARKER, "## 🛡️ License Guard", ""]
    if not v and not w:
        out.append(f"✅ No changes found that would affect {HOLDER}'s copyright or license (`{DIFF_RANGE}`).")
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
            f"{HOLDER}'s code is under a license that requires keeping its copyright notice and "
            "license text in redistributed source (e.g. BSD-style licenses). Adding your own "
            f"copyright line *alongside* {HOLDER}'s is fine; replacing or removing theirs is not. "
            f"Year bumps like `2020-2024 {HOLDER}` → `2020-2026 {HOLDER}` are ignored.",
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
