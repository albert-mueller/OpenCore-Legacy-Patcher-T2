#!/usr/bin/env python3
"""
check_plist_format.py: Fail if a plist is not in the repo's canonical format.

Canonical format (the one payloads/Config/config.plist has used since before
4.0.0.190007):
  - valid XML plist (parses with plistlib)
  - indentation with 4 spaces per nesting level, no tabs
  - LF line endings, no trailing whitespace, file ends with a newline

A full reformat (e.g. re-saving the file in an editor that indents with tabs)
turns every line into a diff and hides the real changes in a PR, so it is
rejected here. To fix a failing file, re-indent it with 4 spaces without
changing its content.

Usage: check_plist_format.py <file.plist> [<file.plist> ...]
"""

import plistlib
import re
import sys

INDENT = 4
CONTAINERS = ("plist", "dict", "array")
OPEN_RE = re.compile(r"^<(plist|dict|array)(\s[^>]*)?>$")
CLOSE_RE = re.compile(r"^</(plist|dict|array)>$")
MAX_REPORTED = 20


def check(path: str) -> list:
    errors = []

    with open(path, "rb") as f:
        raw = f.read()

    try:
        plistlib.loads(raw)
    except Exception as e:
        return [f"not a valid plist: {e}"]

    if b"\r" in raw:
        errors.append("contains CR characters (use LF line endings)")
    if not raw.endswith(b"\n"):
        errors.append("does not end with a newline")

    text = raw.decode("utf-8")
    depth = 0
    for number, line in enumerate(text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"), start=1):
        if "\t" in line:
            errors.append(f"line {number}: contains a tab")
            continue
        if line != line.rstrip():
            errors.append(f"line {number}: trailing whitespace")

        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<?xml") or stripped.startswith("<!DOCTYPE"):
            continue

        if CLOSE_RE.match(stripped):
            depth -= 1

        expected = depth * INDENT
        actual = len(line) - len(line.lstrip(" "))
        if actual != expected:
            errors.append(f"line {number}: indented {actual} spaces, expected {expected}")

        if OPEN_RE.match(stripped):
            depth += 1

    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2

    failed = False
    for path in sys.argv[1:]:
        errors = check(path)
        if not errors:
            print(f"OK: {path}")
            continue
        failed = True
        print(f"::error file={path}::{path} is not in the canonical plist format ({len(errors)} problem(s))")
        for error in errors[:MAX_REPORTED]:
            print(f"  {error}")
        if len(errors) > MAX_REPORTED:
            print(f"  ... and {len(errors) - MAX_REPORTED} more")

    if failed:
        print("\nRe-indent the file with 4 spaces per level (no tabs) without changing its content.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
