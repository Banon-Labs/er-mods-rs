#!/usr/bin/env bash
# Is something compiling the workspace right now?
#
# Read by `.cupcake/policies/claude/block_measurement_during_build.rego`, which refuses to launch a
# game run or drive a probe while the box is busy compiling.
#
# The failure this exists to prevent, measured 2026-09-16: a backgrounded `git push` ran
# `scripts/check.sh` fanned across stages, `/proc/loadavg` reached 23.07 on 16 cores, and Elden Ring
# was launched and measured in that same window -- a 2500ms settle, a 500ms pad hold, and a latch
# window of a frame or two. Those runs proved nothing and were reported as if they had. The agent
# also spent turns polling the push instead of working.
#
# Prints `BUILDING` and the offending command lines when a build is up, and nothing otherwise.
#
# Never fails: a signal that errors would take the policy with it, and the fail-open direction here
# is "nothing is building", which leaves launching exactly as permissive as it was before.
set -uo pipefail

found=""
# This process and every ancestor, so the tool call running the signal never counts as a build.
self_tree=""
walk=$$
while [ -n "$walk" ] && [ "$walk" != "0" ] && [ "$walk" != "1" ]; do
	self_tree="$self_tree $walk"
	walk="$(awk '{print $4}' "/proc/$walk/stat" 2>/dev/null)" || break
done
for proc in /proc/[0-9]*; do
	[ -r "$proc/cmdline" ] || continue
	cmd="$(tr '\0' ' ' <"$proc/cmdline" 2>/dev/null)" || continue
	[ -n "$cmd" ] || continue
	pid="${proc#/proc/}"
	# Skip this script and whatever launched it. A tool call that merely contains the word cargo --
	# writing this file, for instance -- has that text in its own command line, and matching it
	# would make the signal fire on itself and refuse every launch.
	case " $self_tree " in *" $pid "*) continue ;; esac
	case "$cmd" in *workspace_build_running*) continue ;; esac
	case "$cmd" in
		# `check.sh` is the gate; a push runs it, so both spellings appear.
		*check.sh*|*"git push"*|*cargo-xwin*|*"cargo build"*|*"cargo check"*|*"cargo clippy"*|*"cargo test"*|*rustc\ *)
			found="${found}${cmd:0:100}"$'\n' ;;
	esac
done

if [ -n "$found" ]; then
	printf 'BUILDING\n%s' "$found"
fi
exit 0
