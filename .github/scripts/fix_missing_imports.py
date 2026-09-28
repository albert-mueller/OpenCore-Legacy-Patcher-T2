"""
Auto-fix undefined names (ruff F821) that are unambiguously a missing
standard-library import, e.g. `sys.exit(1)` in a file without `import sys`.

Only adds `import <module>` when:
  - the undefined name is a top-level stdlib module, and
  - every flagged use in the file is attribute access (`name.something`).
Anything else is left for a human (the syntax check step will still fail).
"""
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

EXCLUDE = sys.argv[1] if len(sys.argv) > 1 else ""
STDLIB = set(sys.stdlib_module_names)

cmd = ["ruff", "check", ".", "--select", "F821", "--output-format", "json", "--exit-zero"]
if EXCLUDE:
    cmd += ["--extend-exclude", EXCLUDE]
diagnostics = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout or "[]")

candidates = defaultdict(lambda: defaultdict(list))  # file -> name -> [(row, col)]
for d in diagnostics:
    name = d["message"].split("`")[1] if "`" in d["message"] else None
    if name in STDLIB:
        candidates[d["filename"]][name].append((d["location"]["row"], d["location"]["column"]))

def import_insert_line(source: str) -> int:
    """0-based line index after the leading docstring/import block of a module."""
    last = 0
    for i, node in enumerate(ast.parse(source).body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = node.end_lineno
        elif i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            last = node.end_lineno
        else:
            break
    if last == 0 and source.startswith("#!"):
        last = 1
    return last


fixed = 0
for filename, names in candidates.items():
    path = Path(filename)
    source = path.read_text()
    lines = source.splitlines(keepends=True)
    to_add = []
    for name, locs in sorted(names.items()):
        # every use must look like `name.attr` – otherwise it may be a typo'd variable
        if all(lines[r - 1][c - 1 + len(name):].startswith(".") for r, c in locs):
            to_add.append(name)
    if not to_add:
        continue
    try:
        at = import_insert_line(source)
    except SyntaxError:
        continue
    lines[at:at] = [f"import {n}\n" for n in to_add]
    path.write_text("".join(lines))
    fixed += len(to_add)
    print(f"{filename}: added import {', '.join(to_add)}")

print(f"fix_missing_imports: {fixed} import(s) added")
