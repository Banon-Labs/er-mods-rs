#!/usr/bin/env bash
# Has a Frida session actually observed something since the last commit?
#
# Read by `.cupcake/policies/claude/no_rust_edit_without_frida_proof.rego`, which refuses a Rust
# edit under `crates/` until this prints `PROVEN`. The judgement lives in
# `scripts/er-frida-evidence.py --check`, not here, so the rule and its selftest are one thing.
#
# Fails closed, unlike the other signals in this directory. Printing nothing means the policy sees
# no proof and denies, which is the whole point: a broken evidence reader must not quietly hand out
# permission to edit. `live_er_run.sh` fails open because its absence means "no run to protect";
# absence here means "no measurement was taken".
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 0
reader="$repo_root/scripts/er-frida-evidence.py"
[ -f "$reader" ] || exit 0

# Cupcake pipes the pending event to every signal on stdin. The reader takes it so that only a
# commit to the crate being edited spends the measurement, not every Rust commit in the tree.
# Guarded on a pipe: run by hand from a terminal, stdin is a keyboard and reading it would hang.
if [ -t 0 ]; then
  timeout 15 python3 "$reader" --check 2>/dev/null || true
else
  timeout 15 python3 "$reader" --check --event - 2>/dev/null || true
fi
exit 0
