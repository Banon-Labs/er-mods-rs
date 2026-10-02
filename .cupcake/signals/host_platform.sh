#!/usr/bin/env bash
# Is this host WSL, native Linux, or something this cannot tell?
#
# Read by `.cupcake/policies/claude/block_manual_pgrep.rego`. That guard exists for a WSL2 box
# running a native Windows Steam install: there Steam and the game/EAC processes are Windows
# processes that only tasklist.exe can see, so `pgrep -x steam` reports "down" while Steam is up.
# On a native Linux host that premise is false -- pgrep sees Steam, Proton and every other process
# directly -- and the guard did nothing there but block read-only process listings (measured
# 2026-09-26: a pre-cleanup check for running cargo/rustc builds and agent sessions was denied).
# This repo's dev box has run a native Linux Steam install since the WSL2 setup was retired (see
# AGENTS.md, the Seamless Co-op install-path note), so the guard now asks this signal first.
#
# Prints one word on stdout and always exits 0:
#   wsl      a WSL kernel or a WSL runtime marker is present
#   native   a Linux /proc is readable and carries no WSL marker
#   unknown  neither could be established (no /proc: macOS, a sandbox, ...)
#
# The policy lifts its block on `native` and on nothing else. `wsl`, `unknown`, a mistyped
# override, a crash (cupcake replaces a non-zero exit with a failure record) and a timeout (the key
# is then absent from input.signals) all leave the guard as it was, so this file breaking cannot
# remove the WSL protection; the worst it can do is bring back the over-blocking on native Linux.
#
# CUPCAKE_HOST_PLATFORM_OVERRIDE pins the answer for the policy regression suite, the way
# CUPCAKE_CURRENT_BRANCH_OVERRIDE pins the branch signal. scripts/test-cupcake-policies.py runs
# every case as `wsl` unless the case says otherwise, so the deny cases keep exercising the guard on
# any host (CI runs on native Linux) and the native-host cases pin `native`. Cupcake reads it from
# the hook process's environment, which an agent's Bash command does not reach.
set -u

if [ -n "${CUPCAKE_HOST_PLATFORM_OVERRIDE:-}" ]; then
	printf '%s\n' "$CUPCAKE_HOST_PLATFORM_OVERRIDE"
	exit 0
fi

# Markers WSL itself provides: the variables its init exports to the processes it starts, the
# interop binfmt handler it registers, and its runtime directory.
if [ -n "${WSL_DISTRO_NAME:-}" ] || [ -n "${WSL_INTEROP:-}" ] ||
	[ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || [ -d /run/WSL ]; then
	printf 'wsl\n'
	exit 0
fi

# The kernel release names it outright -- `5.15.167.4-microsoft-standard-WSL2` on WSL2,
# `4.4.0-19041-Microsoft` on WSL1 -- and unlike the variables above it survives a scrubbed
# environment.
osrelease=""
if [ -r /proc/sys/kernel/osrelease ]; then
	read -r osrelease </proc/sys/kernel/osrelease || true
fi
lowered="${osrelease,,}"
case "$lowered" in
"") printf 'unknown\n' ;;
*microsoft* | *wsl*) printf 'wsl\n' ;;
*) printf 'native\n' ;;
esac
exit 0
