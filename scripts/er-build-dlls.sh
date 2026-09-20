#!/usr/bin/env bash
# Build named ME3 shells and record what they were built from.
#
# Why this exists rather than a bare cargo call
# ---------------------------------------------
# `cargo xwin build --release --target x86_64-pc-windows-msvc` honours
#     default-members = ["crates/er-quickload"]
# so it builds only the product and exits 0 in a fraction of a second having compiled none of
# the other fifteen shells. That is indistinguishable from a successful incremental build,
# and the stale DLL from last week stays exactly where it was. So every package is named with
# an explicit `-p`, taken from scripts/me3-dll-list.py (the single source of truth for which
# cdylibs this workspace ships, including the four whose artifact name is not the package name
# with dashes swapped for underscores).
#
# Provenance is written here because it cannot be reconstructed afterwards: proving a DLL came
# from a given source tree needs a content hash taken while that tree was the one being
# compiled. scripts/er-run-branch.py refuses to launch an artifact without it.
#
# Usage:
#   scripts/er-build-dlls.sh er-quickload er-armament-icons     # named packages
#   scripts/er-build-dlls.sh --all                               # every shipped shell
#   scripts/er-build-dlls.sh --closure closure.json              # packages from a closure
#
# A cold cross-compile takes minutes -- run this detached, not in a foreground shell.
set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

# A cold cross-compile of 26 shells is the single heaviest thing this repo does. Yield first.
# shellcheck source=lib/cpu-courtesy.sh
# shellcheck disable=SC1091  # sourced at run time; shellcheck -x is not how this suite is linted.
. "$REPO_ROOT/scripts/lib/cpu-courtesy.sh"
cpu_courtesy er-build-dlls

# This script hashes the working tree, so it must not run while something is editing it.
#
# The provenance record written below is a content hash over each package's dependency closure,
# taken while that tree is the one being compiled. `scripts/prove-gate-positive-controls.py` edits
# tracked files and puts them back -- that is how it proves a gate can fail -- so a run of it
# overlapping this one makes every record here a hash of a mutant.
#
# Measured 2026-09-20: it did, and the records outlived the run. `tools/er-installer/build.rs` then
# refused to embed any of the 29 DLLs -- `recorded 8c3607d9e7ee, tree is now 3cc41b24c98d;
# 87 -> 87 files`, the same count and a different hash, on crates the session never touched --
# which killed the `cargo-build` stage of a push after every other stage had gone green.
#
# Shared, not exclusive: this has to coexist with a `check.sh --stage` run, which also takes it
# shared, and with a second build. The prover takes it exclusively, so it is the only thing
# excluded, which is exactly the pairing that was missing. Same machine-wide lock file check.sh
# uses -- every agent worktree is a separate checkout, and a per-checkout lock would guard nothing.
_build_lock="${XDG_RUNTIME_DIR:-/tmp}/er-mods-rs-check-sh.lock"
if command -v flock >/dev/null 2>&1; then
	exec 8<>"$_build_lock" || true
	if ! flock -n -s 8; then
		printf 'er-build-dlls: REFUSED -- something holds %s exclusively.\n' "$_build_lock" >&2
		printf '  The provenance this records is a hash of the working tree, and an exclusive\n' >&2
		printf '  holder (scripts/prove-gate-positive-controls.py, or a whole-suite check.sh)\n' >&2
		printf '  is either editing that tree or reading it. Wait for it, then build.\n' >&2
		exit 1
	fi
fi

TARGET="${ER_BUILD_TARGET:-x86_64-pc-windows-msvc}"
PROFILE_DIR="$REPO_ROOT/target/$TARGET/release"

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; }

packages=()
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --all)
    mapfile -t packages < <(python3 "$REPO_ROOT/scripts/me3-dll-list.py" --pairs | cut -d: -f1)
    ;;
  --closure)
    [[ -f "${2:-}" ]] || { echo "er-build-dlls: no such closure file: ${2:-}" >&2; exit 1; }
    mapfile -t packages < <(python3 -c "
import json,sys
print('\n'.join(json.load(open(sys.argv[1]))['packages']))" "$2")
    ;;
  "")
    echo "er-build-dlls: name at least one package, or pass --all / --closure FILE" >&2
    exit 1
    ;;
  *) packages=("$@") ;;
esac

if [[ ${#packages[@]} -eq 0 ]]; then
  echo "er-build-dlls: nothing to build" >&2
  exit 1
fi

# package -> artifact stem, from the same authoritative array. Deriving the filename by
# swapping dashes for underscores silently skips the four crates that override [lib] name.
declare -A artifact_of
while IFS=: read -r pkg artifact; do
  artifact_of["$pkg"]="$artifact"
done < <(python3 "$REPO_ROOT/scripts/me3-dll-list.py" --pairs)

cargo_args=()
for pkg in "${packages[@]}"; do
  [[ -n "${artifact_of[$pkg]:-}" ]] || {
    echo "er-build-dlls: '$pkg' is not an ME3-loadable shell (see scripts/me3-dll-list.py --pairs)" >&2
    exit 1
  }
  cargo_args+=(-p "$pkg")
done

echo "er-build-dlls: building ${#packages[@]} package(s): ${packages[*]}"
( cd "$REPO_ROOT" && cargo xwin build --release --target "$TARGET" "${cargo_args[@]}" )

status=0
for pkg in "${packages[@]}"; do
  dll="$PROFILE_DIR/${artifact_of[$pkg]}.dll"
  if [[ ! -f "$dll" ]]; then
    echo "er-build-dlls: BUILD REPORTED SUCCESS BUT $dll DOES NOT EXIST ($pkg)" >&2
    status=1
    continue
  fi
  python3 "$REPO_ROOT/scripts/er-dll-provenance.py" write --package "$pkg" --artifact "$dll"
done

exit "$status"
