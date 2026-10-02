#!/usr/bin/env python3
"""
check_python.py: Find Python code that cannot run.

Only reports problems that make Python fail when the code runs, not style:
  - syntax errors (SyntaxError, IndentationError, TabError), via compile()
  - names that are used but never defined (NameError), via pyflakes, plus the
    other pyflakes messages that are errors rather than warnings
  - relative imports inside the repo that point at a module, or a name in a
    module, that does not exist (ImportError at import time)

Nothing is imported or executed: every file is only parsed. That makes it safe
to run on untrusted PR code.

Usage:
  check_python.py --root <repo dir> [--baseline <repo dir>] [--json <out>]
                  [--known-json <out>] [--warnings]

With --baseline, problems that already exist in the baseline tree (same file,
same message) are not counted as new, so a PR is only blamed for what it
introduces. They are still listed separately with --known-json, so the
problems that are already broken on main stay visible.

With --warnings, pyflakes warnings (unused imports/variables, repeated dict
keys, ...) are reported as well, with severity "warning". Only errors make
the exit code non-zero.

Things pyflakes reports as unused but that are used on purpose are skipped:
  - re-exports in __init__.py: 'from .module import Name' there makes Name
    part of the package's API (sys_patch.mount.APFSSnapshot, from .patchsets
    import get_disabled_patchsets), even when no file in the repo uses it yet
  - imports inside try/except ImportError that only check availability
    (try: import ssl / except ImportError: ...)
  - the name of an except clause (except Exception as e) that the block
    doesn't use, e.g. because it logs via logging.exception() instead
"""

import argparse
import ast
import json
import os
import re
import sys
import warnings
from pathlib import Path

from pyflakes import checker as pyflakes_checker
from pyflakes import messages as m

# pyflakes messages that mean the code will fail at runtime (the rest are
# style warnings such as unused imports or variables).
ERROR_MESSAGES = tuple(getattr(m, n) for n in [
    "UndefinedName",
    "UndefinedLocal",
    "UndefinedExport",
    "DuplicateArgument",
    "ReturnOutsideFunction",
    "YieldOutsideFunction",
    "ContinueOutsideLoop",
    "BreakOutsideLoop",
    "DefaultExceptNotLast",
    "TwoStarredExpressions",
    "TooManyExpressionsInStarredAssignment",
    "StringDotFormatExtraPositionalArguments",
    "StringDotFormatExtraNamedArguments",
    "StringDotFormatMissingArgument",
    "StringDotFormatMixingAutomatic",
    "StringDotFormatInvalidFormat",
    "PercentFormatInvalidFormat",
    "PercentFormatMixedPositionalAndNamed",
    "PercentFormatUnsupportedFormatCharacter",
    "PercentFormatPositionalCountMismatch",
    "PercentFormatExtraNamedArguments",
    "PercentFormatMissingArgument",
    "PercentFormatExpectedMapping",
    "PercentFormatExpectedSequence",
    "PercentFormatStarRequiresSequence",
    "ForwardAnnotationSyntaxError",
    "RaiseNotImplemented",
    "InvalidPrintSyntax",
] if hasattr(m, n))

SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", "venv", ".venv"}
PY_SHEBANG = re.compile(rb"^#!.*\bpython[0-9.]*\b")


def python_files(root: Path) -> list:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if name.endswith(".py"):
                files.append(path)
            elif name.endswith(".command"):
                try:
                    with open(path, "rb") as f:
                        if PY_SHEBANG.match(f.readline()):
                            files.append(path)
                except OSError:
                    pass
    return sorted(files)


class ModuleIndex:
    """Top-level names per module file, parsed lazily (for import checks)."""

    def __init__(self, root: Path):
        self.root = root
        self.cache = {}

    def names(self, path: Path):
        if path not in self.cache:
            try:
                tree = ast.parse(path.read_bytes(), filename=str(path))
            except Exception:
                self.cache[path] = None          # broken module: reported on its own
                return None
            names, star = set(), False
            for node in tree.body:
                names |= _bound_names(node)
                if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                    star = True
                if isinstance(node, (ast.If, ast.Try)):
                    for sub in ast.walk(node):
                        names |= _bound_names(sub)
            self.cache[path] = (names, star)
        return self.cache[path]


def _bound_names(node) -> set:
    out = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out.add(node.name)
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    out.add(n.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            if a.name != "*":
                out.add((a.asname or a.name).split(".")[0])
    return out


def resolve(base_dir: Path, dotted: str):
    """Return (module_file, package_dir) for a dotted path under base_dir."""
    target = base_dir
    for part in [p for p in dotted.split(".") if p]:
        target = target / part
    if (target.with_suffix(".py")).is_file():
        return target.with_suffix(".py"), None
    if (target / "__init__.py").is_file():
        return target / "__init__.py", target
    if target.is_dir():
        return None, target                     # namespace package
    return None, None


def check_imports(path: Path, tree, index: ModuleIndex) -> list:
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = path.parent
        for _ in range(node.level - 1):
            base = base.parent
        module = node.module or ""
        mod_file, pkg_dir = resolve(base, module)
        shown = "." * node.level + module
        if module and mod_file is None and pkg_dir is None:
            problems.append((node.lineno, f"ImportError: no module named '{shown}'"))
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            # 'from pkg import sub' may name a submodule
            if pkg_dir is not None and (
                (pkg_dir / f"{alias.name}.py").is_file() or (pkg_dir / alias.name / "__init__.py").is_file()
                or (pkg_dir / alias.name).is_dir()
            ):
                continue
            if mod_file is None:
                problems.append((node.lineno, f"ImportError: cannot import name '{alias.name}' from '{shown}'"))
                continue
            info = index.names(mod_file)
            if info is None:
                continue
            names, star = info
            if alias.name not in names and not star:
                problems.append((node.lineno, f"ImportError: cannot import name '{alias.name}' from '{shown}'"))
    return problems


IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _handler_catches_import_error(handler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id in IMPORT_ERRORS
               or isinstance(t, ast.Attribute) and t.attr in IMPORT_ERRORS for t in types)


def intentional_unused(tree) -> tuple:
    """Lines of availability-check imports, (line, name) of except-clause names,
    and lines of relative imports (re-exports when the file is an __init__.py)."""
    probe_lines, except_names, reexport_lines = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            reexport_lines.add(node.lineno)
        if isinstance(node, ast.Try) and any(_handler_catches_import_error(h) for h in node.handlers):
            for stmt in node.body:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    probe_lines.add(stmt.lineno)
        if isinstance(node, ast.ExceptHandler) and node.name:
            except_names.add((node.lineno, node.name))
    return probe_lines, except_names, reexport_lines


def is_intentional(msg, path: Path, tree, cache: dict) -> bool:
    if "data" not in cache:
        cache["data"] = intentional_unused(tree)
    probe_lines, except_names, reexport_lines = cache["data"]
    if isinstance(msg, m.UnusedVariable):
        return (msg.lineno, msg.message_args[0]) in except_names
    if isinstance(msg, m.UnusedImport):
        return msg.lineno in probe_lines or (path.name == "__init__.py" and msg.lineno in reexport_lines)
    return False


def check_file(path: Path, index: ModuleIndex, with_warnings: bool = False) -> list:
    source = path.read_bytes()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(source, str(path), "exec", dont_inherit=True)
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        kind = type(e).__name__
        return [(e.lineno or 0, f"{kind}: {e.msg}", "error")]
    except ValueError as e:                     # e.g. null bytes
        return [(0, f"SyntaxError: {e}", "error")]

    problems = []
    cache = {}
    w = pyflakes_checker.Checker(tree, filename=str(path))
    for msg in w.messages:
        text = msg.message % msg.message_args
        if isinstance(msg, ERROR_MESSAGES):
            prefix = "NameError" if isinstance(msg, (m.UndefinedName, m.UndefinedLocal, m.UndefinedExport)) else "Error"
            problems.append((msg.lineno, f"{prefix}: {text}", "error"))
        elif with_warnings and not is_intentional(msg, path, tree, cache):
            problems.append((msg.lineno, f"Warning: {text}", "warning"))
    problems += [(line, text, "error") for line, text in check_imports(path, tree, index)]
    return problems


def scan(root: Path, with_warnings: bool = False) -> list:
    index = ModuleIndex(root)
    results = []
    for path in python_files(root):
        rel = path.relative_to(root).as_posix()
        for line, text, severity in check_file(path, index, with_warnings):
            results.append({"file": rel, "line": line, "message": text, "severity": severity})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--json", help="write the reported (new) problems here")
    ap.add_argument("--known-json", help="with --baseline: write problems that already exist in the baseline here")
    ap.add_argument("--warnings", action="store_true", help="also report pyflakes warnings")
    args = ap.parse_args()

    problems = scan(Path(args.root).resolve(), args.warnings)
    known = []
    if args.baseline:
        in_base = {(p["file"], p["message"]) for p in scan(Path(args.baseline).resolve(), args.warnings)}
        known = [p for p in problems if (p["file"], p["message"]) in in_base]
        problems = [p for p in problems if (p["file"], p["message"]) not in in_base]

    if args.json:
        with open(args.json, "w") as f:
            json.dump(problems, f, indent=2)
    if args.known_json:
        with open(args.known_json, "w") as f:
            json.dump(known, f, indent=2)

    errors = [p for p in problems if p["severity"] == "error"]
    warns = [p for p in problems if p["severity"] == "warning"]
    for p in errors:
        print(f"::error file={p['file']},line={p['line']}::{p['message']}")
        print(f"  {p['file']}:{p['line']}: {p['message']}")
    for p in warns:
        print(f"  {p['file']}:{p['line']}: {p['message']}")
    known_errors = [p for p in known if p["severity"] == "error"]
    if known_errors:
        print(f"\nAlready broken in the baseline (not counted): {len(known_errors)} error(s)")
        for p in known_errors:
            print(f"  {p['file']}:{p['line']}: {p['message']}")

    if not errors:
        print("\nOK: no syntax errors or code that would fail at runtime found" + (f" ({len(warns)} warning(s))" if warns else ""))
        return 0
    print(f"\n{len(errors)} error(s) found" + (f", {len(warns)} warning(s)" if warns else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
