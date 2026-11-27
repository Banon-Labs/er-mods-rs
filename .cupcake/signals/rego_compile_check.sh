#!/usr/bin/env bash
# Cupcake signal: rego_compile_check
#
# Consumed by `.cupcake/policies/claude/rego_must_compile.rego`. For a Write, Edit, MultiEdit or a
# resolvable Bash write whose target is a `.rego` the engine loads (`.cupcake/policies/` or
# `.cupcake/system/`), prints `REGOCHECK<TAB>error<TAB><path>` and opa's errors under it when the
# engine's policy set would no longer compile with the write in place, `REGOCHECK<TAB>unchecked...`
# when the check could not run, and nothing otherwise. The judgement and the 2026-11-11 incident
# behind it (an Edit calling an undefined `value_of` locked every session out) live in
# `scripts/cupcake_rego_compile_check.py`.
#
# Fails open: a missing reader, a crash or a timeout prints nothing, and the policy allows.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_rego_compile_check.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

# Only an event that names a .rego file can write one; skip the python start for everything else.
case "$event" in
*.rego*) ;;
*) exit 0 ;;
esac

# Cupcake drops a discovered signal after 5 s, and a dropped signal is an allow. The reader keeps
# its own work under 4.2 s.
printf '%s' "$event" | timeout 4.8 python3 "$reader" --signal || true
exit 0
