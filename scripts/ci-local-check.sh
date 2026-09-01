#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# THE GATE MUST NOT DAMAGE THE THING IT GATES, AND ON 2026-08-31 IT DID -- TWICE.
#
# Route, reproduced end to end (bd hooks-selftest-under-git-hook-blanks-the-live-config-2026-08-31):
# a push FROM A LINKED WORKTREE runs this script from its pre-push hook, and git exports GIT_DIR to
# a linked worktree's hooks -- measured on git 2.55 by scripts/measure-git-hook-env.sh, which also
# measures that a MAIN checkout's hooks get no GIT_DIR at all, which is why this looked unreachable
# for a day. `git -C <fixture>` does NOT override GIT_DIR, so every fixture command in a downstream
# script lands on the SHARED config instead: `git init` saw a git dir not named `.git`, wrote
# core.bare = true, and every later `git status` in the main checkout died with "fatal: this
# operation must be run in a work tree"; `git config --unset core.hooksPath` disarmed the hooks for
# ninety minutes and a push reached origin ungated.
#
# scripts/check-git-hooks-installed.sh now scrubs its own environment, which closes the one script
# that was caught. This closes the CLASS: any gate below, today's or tomorrow's, that builds a git
# fixture without scrubbing gets caught here instead of in the next person's checkout.
#
# Scoped to the two keys the damage lands on, deliberately. Comparing the whole file would go red
# on an unrelated `[branch]` write from one of the other agents working in this tree, and a gate
# that cries wolf gets its check deleted. Read through an explicit --git-dir, which outranks an
# inherited GIT_DIR, and which keeps working after core.bare has already gone true -- the state in
# which most other git commands stop working.
gate_common_dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
gate_key() { [[ -z "$gate_common_dir" ]] || git --git-dir="$gate_common_dir" config --get "$1" || true; }
gate_bare_before=$(gate_key core.bare)
gate_hookspath_before=$(gate_key core.hooksPath)
gate_config_unchanged() {
	local rc=$? bare_after hookspath_after
	[[ -n "$gate_common_dir" ]] || exit "$rc"
	bare_after=$(gate_key core.bare)
	hookspath_after=$(gate_key core.hooksPath)
	if [[ "$gate_bare_before" != "$bare_after" || "$gate_hookspath_before" != "$hookspath_after" ]]; then
		echo "[ci-local-check] FAIL: this gate CHANGED the repository configuration it was checking." >&2
		echo "  $gate_common_dir/config" >&2
		echo "    core.bare      ${gate_bare_before:-<unset>} -> ${bare_after:-<unset>}" >&2
		echo "    core.hooksPath ${gate_hookspath_before:-<unset>} -> ${hookspath_after:-<unset>}" >&2
		echo "  A gate below built a git fixture without scrubbing its environment first. If this ran" >&2
		echo "  from a LINKED WORKTREE, its hooks inherit GIT_DIR and 'git -C <fixture>' does not" >&2
		echo "  override it, so fixture-only work lands on the shared config. Confirm with:" >&2
		echo "      bash scripts/measure-git-hook-env.sh" >&2
		echo "  Fix the offending script with: unset \$(git rev-parse --local-env-vars)" >&2
		echo "  Repair this checkout with: git config core.bare false && bash scripts/install-git-hooks.sh" >&2
		exit 1
	fi
	exit "$rc"
}
trap gate_config_unchanged EXIT

# SELF-HEAL THE WORKTREE CASE INSTEAD OF MAKING EVERY AGENT DO IT BY HAND. `vendor/` is
# gitignored, so a `git worktree` checkout (an agent sandbox under `.claude/worktrees/`, a
# `.worktrees/` lab) starts without MinHook while the MAIN checkout it was created from already has
# it -- and this script runs from the pre-push hook, so the first thing a worktree ever learns is
# that its push is refused. The shared git dir names that main checkout exactly, so link its vendor
# tree in rather than re-cloning ~1MB of C per worktree. Fail closed and unchanged when the link
# cannot be established, because a missing MinHook still breaks the cross-compile below.
if [[ ! -f vendor/minhook/src/buffer.c ]]; then
  main_checkout=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
  main_checkout=${main_checkout%/.git}
  if [[ -n "$main_checkout" ]] && [[ "$main_checkout" != "$repo_root" ]] &&
    [[ -f "$main_checkout/vendor/minhook/src/buffer.c" ]]; then
    mkdir -p vendor
    ln -sfn "$main_checkout/vendor/minhook" vendor/minhook
    echo "[ci-local-check] git worktree: linked vendor/minhook -> $main_checkout/vendor/minhook" >&2
  fi
fi

if [[ ! -f vendor/minhook/src/buffer.c ]]; then
  cat >&2 <<'EOF'
missing vendor/minhook/src/buffer.c

CI checks out MinHook with:
  git clone --depth 1 --branch v1.3.4 https://github.com/TsudaKageyu/minhook.git vendor/minhook

A git worktree normally self-heals here by linking the main checkout's vendor directory; reaching
this message from one means that checkout has no MinHook either, so clone it there first.
EOF
  exit 2
fi

# IS THIS GATE EVEN INSTALLED? First, because everything below it is reached only through the
# pre-push hook, and on 2026-08-31 that hook had not run for over a day: `core.hooksPath` still
# named the pre-rename directory /home/banon/projects/er-effects-rs/.githooks, git resolved its
# hooks directory to nothing, and no hook ran at all -- silently, because a hook that cannot be
# found looks exactly like a hook that passed. Selftest first, so the gate is never trusted on its
# own say-so.
bash scripts/check-git-hooks-installed.sh --selftest
bash scripts/check-git-hooks-installed.sh
# ...and that the trap at the top of THIS file still catches a gate that rewrites the shared
# config, which is how the checkout was damaged twice on 2026-08-31. Both directions, because a
# guard wedged green protects nothing and a guard wedged red gets deleted.
bash scripts/test-ci-local-check-config-guard.sh
python3 scripts/check-no-lossy-utf8.py
python3 scripts/check-no-timeouts.py
python3 scripts/test-no-timeouts.py
bash scripts/test-git-pre-push-block-main.sh
# One game address must have exactly ONE literal declaration. Divergent names for one address
# are divergent CLAIMS about what it is, and three of them turned out to be wrong RE facts
# shipping in the DLL (bd rva-67b750-is-save-write-not-continue-load-2026-08-01,
# rva-4852f88-is-saveload2-slsystemimpl-not-fd4-io-worker-2026-08-01). Selftest first, so the
# gate is never trusted on its own say-so.
python3 scripts/check-rva-alias-drift.py --selftest
python3 scripts/check-rva-alias-drift.py
cupcake validate --log-level error
python3 scripts/test-cupcake-policies.py
# The delivered-shape gate, which test-cupcake-policies.py used to shell out to and no longer
# does (see the comment at the top of that file). This script does not run scripts/check.sh, so
# without these two lines it would have no delivered-shape coverage at all. --selftest and the
# live run are different runs: the first proves the gate rejects a fictional fixture, the second
# checks the real contract.
python3 scripts/test-cupcake-delivered-shape.py --selftest
python3 scripts/test-cupcake-delivered-shape.py
cargo fmt --all -- --check
cargo test -p er-soulsformats -p er-param-inspect

# THIS LINE CHECKS ONE PACKAGE, NOT THE WORKSPACE, AND THAT IS DELIBERATE HERE -- but do not
# mistake it for a workspace gate. `default-members = ["crates/er-quickload"]` in the root
# Cargo.toml means the bare form below selects the product crate and its dependency closure and
# nothing else; er-save-suppress, er-quit-menu, er-invasion-warp and ~30 other members are
# outside it. Two commits reached origin on 2026-08-31 that this line had nothing to say about.
#
# The workspace-wide question is answered by scripts/check-committed-compiles.sh, which the
# pre-push hook runs just before this script and which asks it of the COMMITTED state rather than
# the working tree -- the distinction that made both of those commits invisible to every other
# gate. It keeps its own build cache, so duplicating a `--workspace` check against the working
# tree here would buy nothing and would grow the main target directory by several GB.
if command -v cargo-xwin >/dev/null 2>&1; then
  cargo xwin check --target x86_64-pc-windows-msvc
else
  cargo check --target x86_64-pc-windows-msvc
fi
