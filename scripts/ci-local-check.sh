#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

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
