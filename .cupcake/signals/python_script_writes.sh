#!/usr/bin/env bash
# Cupcake signal: python_script_writes
#
# Consumed by `.cupcake/policies/claude/bash_no_python_file_write.rego`, which refuses
# `python3 <path>.py` for any path outside this repo's committed `scripts/` tree. That guard
# cannot see inside the file, so it used to refuse a scratch script that only reads exactly as it
# refused one that edits a source file (measured 2026-09-29). This reads the files the pending
# command names and prints one line per python script operand:
#
#   `READONLY <operand>`        nothing in the file writes
#   `WRITES <operand> <why>`    something in it writes, or could
#   `UNKNOWN <operand> <why>`   the file could not be pinned down or read
#
# The judgement and its selftest live in `scripts/cupcake_python_script_writes.py`; read its
# docstring for what counts as a write and which command shapes fail closed.
#
# Fails closed: the policy lifts its refusal only on a `READONLY` line naming the operand, so
# printing nothing -- a missing reader, a crash, a timeout -- leaves the guard as it was.
#
# CUPCAKE_PYTHON_SCRIPT_WRITES_OVERRIDE pins the output for policy regression runs, the way
# CUPCAKE_HOST_PLATFORM_OVERRIDE pins host_platform. `opa test` supplies no signals at all, so
# the rego tests set `input.signals.python_script_writes` directly instead.
set -uo pipefail

if [ -n "${CUPCAKE_PYTHON_SCRIPT_WRITES_OVERRIDE:-}" ]; then
  printf '%s' "$CUPCAKE_PYTHON_SCRIPT_WRITES_OVERRIDE"
  exit 0
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_python_script_writes.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

# Nearly every Bash call names no `.py` file; skip the python start for those.
case "$event" in
*python*.py*) ;;
*) exit 0 ;;
esac

printf '%s' "$event" | timeout 5 python3 "$reader" 2>/dev/null || true
exit 0
