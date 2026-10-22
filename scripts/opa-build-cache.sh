#!/usr/bin/env bash
# A content-addressed cache in front of `opa build`, for `cupcake eval --opa-path`.
#
# Why this exists. Every `cupcake eval` copies the rulebook's policies into a fresh temp directory
# and compiles them to WASM with `opa build -t wasm -O 2`, twice (project and global policies).
# Measured 2026-10-20 with `--trace signals,eval` on one eval: 1.18s wall, of which the project
# compile alone was 0.61s, and the eval as a whole cost 2.36 CPU-seconds because `opa build` is a
# multi-threaded Go program. `scripts/test-cupcake-policies.py` runs ~176 evals over the same
# unchanged rulebook, so it compiled the same two bundles ~176 times each, and that repeated
# compile is what pushed single cases past their 30s cap on a contended machine.
#
# How it stays faithful. The key is every argument, with each directory argument replaced by a
# hash of its file tree (relative path and content of every file, minus the `-o` output itself)
# and the `-o` path replaced by a placeholder, plus the size and mtime of the real `opa` binary. A
# changed policy, entrypoint, flag or `opa` therefore misses and compiles for real. Anything that
# is not a `build` with an `-o` is passed straight through. A failed compile is never cached.
#
# Usage: ER_OPA_BUILD_CACHE=<dir> cupcake eval --opa-path scripts/opa-build-cache.sh ...
#   ER_OPA_REAL   the real binary (default: `opa` on `PATH`)
# Each lookup appends `hit` or `miss` to <dir>/events, so a caller can report what it saved.
set -euo pipefail

real="${ER_OPA_REAL:-$(command -v opa)}"
cache="${ER_OPA_BUILD_CACHE:-}"

out=""
prev=""
for arg in "$@"; do
	[[ "$prev" == "-o" ]] && out="$arg"
	prev="$arg"
done
if [[ -z "$cache" || "${1:-}" != "build" || -z "$out" ]]; then
	exec "$real" "$@"
fi
mkdir -p "$cache"

tree_hash() {
	local dir="$1" skip=""
	case "$out" in "$dir"/*) skip="./${out#"$dir"/}" ;; esac
	(cd "$dir" && find . -type f ! -path "$skip" -print0 | sort -z | xargs -0 -r sha256sum) |
		sha256sum | cut -d' ' -f1
}

key_material="opa $(stat -L -c '%s %Y' "$real")"
for arg in "$@"; do
	if [[ "$arg" == "$out" ]]; then
		key_material+=$'\n<out>'
	elif [[ -d "$arg" ]]; then
		key_material+=$'\n<dir:'"$(tree_hash "$arg")"'>'
	else
		key_material+=$'\n'"$arg"
	fi
done
key=$(printf '%s' "$key_material" | sha256sum | cut -d' ' -f1)
entry="$cache/$key.tar.gz"

# One compile per key: concurrent callers with the same key wait for the first instead of all
# compiling at once, which is the thundering herd a 12-wide pool would otherwise start with.
exec 9>"$cache/$key.lock"
flock 9
if [[ -s "$entry" ]]; then
	cp -f "$entry" "$out"
	echo hit >>"$cache/events"
	exit 0
fi
"$real" "$@"
cp -f "$out" "$entry.tmp.$$"
mv -f "$entry.tmp.$$" "$entry"
echo miss >>"$cache/events"
