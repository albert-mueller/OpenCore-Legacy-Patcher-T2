#!/usr/bin/env bash
# Syntax & sanity checks for the repo.
# Usage: syntax_check.sh [failures.jsonl]
# Syntax errors are also written as JSON lines to the given file, so
# ai_syntax_fix.py knows what to repair.
set -u
out="${1:-/dev/null}"
: > "$out"
fail=0
EXCL=(':!payloads/**' ':!NEW_EFI_*/**' ':!Releases/**')
RUFF_EXCLUDE="payloads,NEW_EFI_*,Releases"

record() {  # record <kind> <file> <error>
  python3 -c 'import json,sys; print(json.dumps({"kind":sys.argv[1],"file":sys.argv[2],"error":sys.argv[3][-4000:]}))' \
    "$1" "$2" "$3" >> "$out"
  fail=1
}

is_zsh()    { head -n1 "$1" | grep -q 'zsh'; }
is_python() { head -n1 "$1" | grep -q 'python'; }

echo "::group::Python syntax"
while IFS= read -r -d '' f; do
  # compile() instead of py_compile: no __pycache__ files end up in the PR
  if ! err=$(python3 -c 'import sys; compile(open(sys.argv[1],"rb").read(), sys.argv[1], "exec")' "$f" 2>&1); then
    echo "::error file=$f::$err"; record python "$f" "$err"
  fi
done < <(
  git ls-files -z -- '*.py' "${EXCL[@]}"
  # .command/.sh files with a python shebang are Python, not shell
  while IFS= read -r -d '' c; do is_python "$c" && printf '%s\0' "$c"; done \
    < <(git ls-files -z -- '*.sh' '*.command' "${EXCL[@]}")
)
# Report-only: undefined names, invalid comparisons, etc.
ruff check . --select E9,F63,F7,F82 --extend-exclude "$RUFF_EXCLUDE" || fail=1
echo "::endgroup::"

echo "::group::Shell syntax"
while IFS= read -r -d '' f; do
  is_python "$f" && continue   # checked in the Python section
  if is_zsh "$f"; then sh_bin=zsh; else sh_bin=bash; fi
  if ! err=$($sh_bin -n "$f" 2>&1); then
    echo "::error file=$f::$err"; record shell "$f" "$err"
  elif [ "$sh_bin" = bash ]; then
    shellcheck -S error "$f" || fail=1   # report-only
  fi
done < <(git ls-files -z -- '*.sh' '*.command' "${EXCL[@]}")
echo "::endgroup::"

echo "::group::YAML / JSON"
while IFS= read -r -d '' f; do
  if ! err=$(python3 -c 'import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))' "$f" 2>&1); then
    echo "::error file=$f::$err"; record yaml "$f" "$err"
  fi
done < <(git ls-files -z -- '*.yml' '*.yaml' "${EXCL[@]}")
while IFS= read -r -d '' f; do
  if ! err=$(python3 -m json.tool "$f" 2>&1 >/dev/null); then
    echo "::error file=$f::$err"; record json "$f" "$err"
  fi
done < <(git ls-files -z -- '*.json' "${EXCL[@]}")
echo "::endgroup::"

echo "::group::Tests"
# Only a dedicated tests/ folder; root test_*.py are macOS-only helper scripts
if [ -d tests ]; then
  pytest -q tests; rc=$?
  if [ $rc -ne 0 ] && [ $rc -ne 5 ]; then fail=1; fi
else
  echo "No tests/ directory - skipping."
fi
echo "::endgroup::"

exit $fail
