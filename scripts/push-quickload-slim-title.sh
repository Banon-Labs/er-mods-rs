#!/usr/bin/env bash
# Push the slim-quickload title branch and, in the same invocation, fast-forward the stack's base
# branch onto the three gate fixes that sit above it.
#
# Both refs go in a single `git push` on purpose: the pre-push hook runs the whole gate suite, so
# two invocations would run it twice for no extra coverage.
#
# Detached on purpose too. That suite takes ~25 minutes, far past an agent shell's 30s budget, and
# killing a push mid-suite orphans check.sh's flock holders
# (bd killing-a-push-orphans-check-sh-flock-holders-2026-09-11). Launch it with
# `setsid nohup ... &` and read the log.
#
# `main` is never named here. The two refs are literal.
set -euo pipefail
cd "$(dirname "$0")/.."
# The base ref is a fast-forward of someone else's branch onto commits that fix its checks, so it
# is pushed only while it is actually behind. Once the remote already has it, naming a bare commit
# object again fails with "you must fully qualify the ref" -- git cannot infer a branch from a sha
# for a ref that now exists.
base_fix="${1:-b8ca9b8c}"
remote_base="$(git rev-parse --verify --quiet "origin/feat/quit-menu-character-rows" || true)"
refspecs=(fix/quickload-vanilla-title-after-boot)
if [[ "$remote_base" != "$(git rev-parse --verify "$base_fix")" ]]; then
	refspecs=("${base_fix}:refs/heads/feat/quit-menu-character-rows" "${refspecs[@]}")
fi
git push -u origin "${refspecs[@]}"
echo "PUSH-SEQUENCE-DONE"
