#!/usr/bin/env bash
# Launch Elden Ring through approved direct/offline ME3 with only er_better_refills.dll loaded,
# then immediately leave the game open for manual QA.
# This script deliberately does not install an exit trap that kills the run: the live process is
# handed to the user, and the printed teardown command is the explicit cleanup trail. Hook-active
# waiting defaults to 0 because readiness proof must not block a user-inspection handoff.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

default_game_dir() {
	local candidate
	for candidate in \
		"$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game" \
		"/mnt/d/Steam/steamapps/common/ELDEN RING/Game" \
		"/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game" \
		"/mnt/c/Program Files (x86)/Steam/steamapps/common/ELDEN RING/Game"; do
		[[ -f "$candidate/eldenring.exe" ]] && {
			printf '%s\n' "$candidate"
			return 0
		}
	done
	printf '%s\n' "$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game"
}

GAME_DIR="${GAME_DIR:-$(default_game_dir)}"
# shellcheck source=scripts/me3-launch-lib.sh
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/me3-launch-lib.sh"

ARTIFACT_DIR="${ARTIFACT_DIR:-$REPO_ROOT/target/runtime-probe/better-refills-user-qa-$(date +%Y%m%d-%H%M%S)}"
BUILT_DLL="${BUILT_DLL:-$REPO_ROOT/target/x86_64-pc-windows-msvc/release/er_better_refills.dll}"
ME3_PROFILE="${ME3_PROFILE:-$ARTIFACT_DIR/better-refills-user-qa.me3}"
PID_FILE="${PID_FILE:-$ARTIFACT_DIR/me3-launch.pid}"
GAME_LOG="${GAME_LOG:-$GAME_DIR/er-better-refills.log}"
CRASH_LOG="${CRASH_LOG:-$GAME_DIR/er-better-refills-crash-log.txt}"
ARTIFACT_LOG="${ARTIFACT_LOG:-$ARTIFACT_DIR/er-better-refills.log}"
ARTIFACT_CRASH_LOG="${ARTIFACT_CRASH_LOG:-$ARTIFACT_DIR/er-better-refills-crash-log.txt}"
HOOK_READY_TIMEOUT_SECONDS="${HOOK_READY_TIMEOUT_SECONDS:-${MAX_WAIT_SECONDS:-0}}"

fatal() {
	echo "better-refills-user-qa: $*" >&2
	exit 1
}

runtime_pids() {
	local proc pid comm cmdline
	for proc in /proc/[0-9]*; do
		pid=${proc##*/}
		[[ -r "$proc/comm" ]] || continue
		comm=$(tr -d '\0' 2>/dev/null <"$proc/comm") || continue
		if [[ "$comm" == "eldenring.exe" ]]; then
			printf '%s\n' "$pid"
			continue
		fi
		[[ -r "$proc/cmdline" ]] || continue
		cmdline=$(tr '\0' ' ' 2>/dev/null <"$proc/cmdline") || continue
		if [[ "$cmdline" == *"$GAME_DIR/eldenring.exe"* ]]; then
			printf '%s\n' "$pid"
			continue
		fi
		if [[ "$cmdline" == *"ELDEN RING\\Game\\eldenring.exe"* ]]; then
			printf '%s\n' "$pid"
			continue
		fi
		if [[ "$cmdline" == *"windows-bin/me3-launcher.exe"* || "$cmdline" == *"windows-bin\\me3-launcher.exe"* ]]; then
			printf '%s\n' "$pid"
		fi
	done
}

win_pids_for() {
	command -v tasklist.exe >/dev/null 2>&1 || return 0
	tasklist.exe /FI "IMAGENAME eq $1" /FO CSV /NH 2>/dev/null |
		python3 -c "import csv,sys; print(' '.join(r[1] for r in csv.reader(sys.stdin) if len(r)>1 and r[1].isdigit()))"
}

wait_for_hook_active() {
	local deadline
	deadline=$((SECONDS + HOOK_READY_TIMEOUT_SECONDS))
	while ((SECONDS < deadline)); do
		if timeout 1 grep --line-buffered -m1 "SetItemReplenishState hook ACTIVE" < <(tail -n +1 -F -- "$GAME_LOG" 2>/dev/null) >"$ARTIFACT_DIR/hook-active.log"; then
			return 0
		fi
	done
	return 1
}

preflight() {
	[[ "$HOOK_READY_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || fatal "HOOK_READY_TIMEOUT_SECONDS must be an integer"
	((HOOK_READY_TIMEOUT_SECONDS >= 0 && HOOK_READY_TIMEOUT_SECONDS <= 300)) || fatal "HOOK_READY_TIMEOUT_SECONDS must be 0..300"
	[[ -f "$BUILT_DLL" ]] || fatal "built DLL not found: $BUILT_DLL"
	[[ -d "$GAME_DIR" ]] || fatal "GAME_DIR not found: $GAME_DIR"
	bash "$REPO_ROOT/scripts/steam-running.sh" >/dev/null || fatal "Steam is not running according to scripts/steam-running.sh"
	me3_preflight
	me3_require_no_lazyloader "$GAME_DIR"
	if [[ -n "$(runtime_pids)" ]]; then
		fatal "Elden Ring/me3 runtime process already exists; refusing to launch over it"
	fi
	local existing_windows_pids
	existing_windows_pids="$(win_pids_for eldenring.exe)$(win_pids_for me3.exe)$(win_pids_for me3-launcher.exe)"
	if [[ -n "$existing_windows_pids" ]]; then
		fatal "Windows Elden Ring/me3 process already exists; refusing to launch over it"
	fi
}

# Build the exact me3 argv for this run. It is split out from the launch itself so
# `--selftest` can assert on it without starting anything.
#
# No path conversion happens here, on purpose. `me3` on this machine is a native Linux
# `ELF` binary, so it takes the same Linux paths every other helper hands it -- see
# `me3_launch` in `scripts/me3-launch-lib.sh`, which passes `$ME3_STEAM_DIR` and the
# profile path through unchanged. An earlier revision ran both of these paths through a
# `me3_to_host_path` helper. That helper was written for the retired `WSL2` setup, where
# `me3.exe` was a Windows binary reached through `wslpath`, and its definition never landed
# on main: under `set -euo pipefail` the two command substitutions returned 127 and aborted
# the script before any launch, every run from 2026-07-27 to 2026-09-14. Do not add it back.
me3_launch_argv() {
	ME3_LAUNCH_ARGV=("$ME3_BIN" --steam-dir "$ME3_STEAM_DIR" launch -g eldenring -p "$ME3_PROFILE")
}

launch_me3_detached() {
	me3_launch_argv
	# me3 resolves `me3-launcher.exe` and `me3_mod_host.dll` from a cwd-relative
	# `target/x86_64-pc-windows-msvc/release` directory when one exists, so launching from
	# this checkout would make Proton exec the wrong payload and die inside the compat
	# tool. `GAME_DIR` is the directory `me3_launch` tells its callers to move to first.
	(
		cd -- "$GAME_DIR" || exit 1
		exec nohup "${ME3_LAUNCH_ARGV[@]}"
	) >"$ARTIFACT_DIR/me3-launch.stdout.log" 2>"$ARTIFACT_DIR/me3-launch.stderr.log" &
	echo $! >"$PID_FILE"
}

usage() {
	cat <<'USAGE'
Usage: launch-better-refills-user-qa.sh [--selftest | --help]

No arguments: preflight, write a one-native me3 profile for er_better_refills.dll,
launch Elden Ring detached, and leave it running for manual QA.

  --selftest  resolve every path, check that every me3 helper this script names is
              defined, and run the real launch path against a recording stub in place
              of me3. Starts no game and needs no Steam.
  --help      this text.

Environment overrides: GAME_DIR, BUILT_DLL, ARTIFACT_DIR, ME3_PROFILE, PID_FILE,
ME3_BIN, ME3_STEAM_DIR, HOOK_READY_TIMEOUT_SECONDS.
USAGE
}

SELFTEST_FAILURES=0

selftest_true() {
	local label="$1"
	shift
	if "$@"; then
		printf '  ok    %s\n' "$label"
	else
		printf '  fail  %s\n' "$label" >&2
		SELFTEST_FAILURES=$((SELFTEST_FAILURES + 1))
	fi
}

selftest_equal() {
	local label="$1" actual="$2" expected="$3"
	if [[ "$actual" == "$expected" ]]; then
		printf '  ok    %s\n' "$label"
	else
		printf '  fail  %s\n' "$label" >&2
		printf '        expected: %s\n' "$expected" >&2
		printf '        actual:   %s\n' "$actual" >&2
		SELFTEST_FAILURES=$((SELFTEST_FAILURES + 1))
	fi
}

selftest_is_defined() {
	declare -F "$1" >/dev/null
}

# Every lowercase `me3_` helper this file calls must come from the sourced library.
# Checking the call sites against `declare -F` is the exact guard the 2026-07-27
# regression needed: a helper that is called but defined nowhere is a 127 at launch time
# and nothing sooner.
selftest_me3_helpers_defined() {
	local name
	while IFS= read -r name; do
		[[ -n "$name" ]] || continue
		selftest_true "helper $name is defined" selftest_is_defined "$name"
	done < <(
		python3 - "${BASH_SOURCE[0]}" <<'PY'
import re
import sys

# Command position only: start of line, or after a separator or a command substitution.
# Prose in comments is skipped, so naming a retired helper in a comment does not report
# it as missing, and a trailing `.` or `-` rules out filenames such as the mod host dll.
call = re.compile(r"(?:^|[;&|(]|\$\()\s*(me3_[a-z0-9_]+)(?![.\-])\b")
names = set()
for line in open(sys.argv[1], encoding="utf-8"):
    if line.lstrip().startswith("#"):
        continue
    names.update(call.findall(line))
print("\n".join(sorted(names)))
PY
	)
}

selftest() {
	local tmp stub record pid expected actual
	echo "better-refills-user-qa: selftest (no game is launched)"

	echo "  REPO_ROOT=$REPO_ROOT"
	echo "  GAME_DIR=$GAME_DIR"
	echo "  ME3_BIN=$ME3_BIN"
	echo "  ME3_STEAM_DIR=$ME3_STEAM_DIR"
	echo "  BUILT_DLL=$BUILT_DLL"

	selftest_me3_helpers_defined
	selftest_true "GAME_DIR is absolute" test "${GAME_DIR:0:1}" = "/"
	selftest_true "ME3_STEAM_DIR is absolute" test "${ME3_STEAM_DIR:0:1}" = "/"
	selftest_true "GAME_DIR holds eldenring.exe" test -f "$GAME_DIR/eldenring.exe"
	selftest_true "ME3_BIN is executable" test -x "$ME3_BIN"
	selftest_true "ME3_STEAM_DIR is a directory" test -d "$ME3_STEAM_DIR"
	selftest_true "launch cwd carries no rust payload dir" \
		test ! -d "$GAME_DIR/target/x86_64-pc-windows-msvc/release"

	tmp="$(mktemp -d -t better-refills-user-qa-selftest-XXXXXX)"
	stub="$tmp/me3-stub.sh"
	record="$tmp/me3-argv.txt"
	cat >"$stub" <<'STUB'
#!/usr/bin/env bash
printf 'cwd=%s\n' "$PWD" >"$ME3_STUB_RECORD"
printf 'bin=%s\n' "$0" >>"$ME3_STUB_RECORD"
printf '%s\n' "$@" >>"$ME3_STUB_RECORD"
STUB
	chmod +x "$stub"

	# Point every mutable global at the temp tree, then run the real launch path. The stub
	# stands in for me3 and records what it was handed, so this exercises the former abort
	# point end to end without a game.
	ME3_BIN="$stub"
	ARTIFACT_DIR="$tmp/artifacts"
	PID_FILE="$ARTIFACT_DIR/me3-launch.pid"
	ME3_PROFILE="$ARTIFACT_DIR/better-refills-user-qa.me3"
	mkdir -p "$ARTIFACT_DIR"
	printf 'not a real dll\n' >"$ARTIFACT_DIR/er_better_refills.dll"
	me3_write_profile "$ME3_PROFILE" "$ARTIFACT_DIR/er_better_refills.dll"
	selftest_true "profile names the built DLL" \
		grep -q 'er_better_refills.dll' "$ME3_PROFILE"

	export ME3_STUB_RECORD="$record"
	launch_me3_detached
	pid=""
	IFS= read -r pid <"$PID_FILE" || true
	selftest_true "launch recorded a pid" test -n "$pid"
	if [[ -n "$pid" ]]; then
		timeout 10 tail --pid="$pid" -f /dev/null >/dev/null 2>&1 || true
	fi
	unset ME3_STUB_RECORD

	if [[ -f "$record" ]]; then
		expected="cwd=$GAME_DIR
bin=$stub
--steam-dir
$ME3_STEAM_DIR
launch
-g
eldenring
-p
$ME3_PROFILE"
		actual="$(cat "$record")"
		selftest_equal "launch handed me3 the expected cwd and argv" "$actual" "$expected"
	else
		printf '  fail  the launch path never reached the me3 binary\n' >&2
		printf '        stderr: %s\n' "$(cat "$ARTIFACT_DIR/me3-launch.stderr.log" 2>/dev/null)" >&2
		SELFTEST_FAILURES=$((SELFTEST_FAILURES + 1))
	fi

	rm -rf "$tmp"
	if ((SELFTEST_FAILURES > 0)); then
		echo "better-refills-user-qa: selftest FAILED ($SELFTEST_FAILURES check(s))" >&2
		return 1
	fi
	echo "better-refills-user-qa: selftest passed"
	return 0
}

case "${1:-}" in
-h | --help)
	usage
	exit 0
	;;
--selftest)
	selftest
	exit $?
	;;
"") ;;
*)
	usage >&2
	fatal "unknown argument: $1"
	;;
esac

preflight
mkdir -p "$ARTIFACT_DIR"
cp -f "$BUILT_DLL" "$ARTIFACT_DIR/er_better_refills.dll"
rm -f "$GAME_LOG" "$CRASH_LOG" "$ARTIFACT_LOG" "$ARTIFACT_CRASH_LOG"
me3_write_profile "$ME3_PROFILE" "$ARTIFACT_DIR/er_better_refills.dll"

launch_me3_detached

echo "better-refills-user-qa: ARTIFACT_DIR=$ARTIFACT_DIR"
echo "better-refills-user-qa: ME3_PROFILE=$ME3_PROFILE"
echo "better-refills-user-qa: DLL=$ARTIFACT_DIR/er_better_refills.dll"
echo "better-refills-user-qa: launch_pid=$(<"$PID_FILE")"

if ((HOOK_READY_TIMEOUT_SECONDS > 0)); then
	echo "better-refills-user-qa: optional hook proof requested; waiting up to ${HOOK_READY_TIMEOUT_SECONDS}s for hook ACTIVE log"
	if ! wait_for_hook_active; then
		cp -f "$GAME_LOG" "$ARTIFACT_LOG" 2>/dev/null || true
		cp -f "$CRASH_LOG" "$ARTIFACT_CRASH_LOG" 2>/dev/null || true
		echo "better-refills-user-qa: FAIL hook-active log not observed within ${HOOK_READY_TIMEOUT_SECONDS}s" >&2
		echo "better-refills-user-qa: artifacts=$ARTIFACT_DIR" >&2
		exit 1
	fi
	cp -f "$GAME_LOG" "$ARTIFACT_LOG"
	cp -f "$CRASH_LOG" "$ARTIFACT_CRASH_LOG" 2>/dev/null || true
	echo "better-refills-user-qa: PASS hook installed; game left running for user QA"
	echo "better-refills-user-qa: log=$ARTIFACT_LOG"
else
	echo "better-refills-user-qa: hook readiness wait skipped (HOOK_READY_TIMEOUT_SECONDS=0); game handed off immediately"
	echo "better-refills-user-qa: inspect later: $GAME_LOG"
fi
echo "better-refills-user-qa: live_wsl_pids=$(runtime_pids | tr '\n' ' ')"
echo "better-refills-user-qa: live_windows_eldenring_pids=$(win_pids_for eldenring.exe)"
echo "better-refills-user-qa: live_windows_me3_pids=$(win_pids_for me3.exe) $(win_pids_for me3-launcher.exe)"
echo "better-refills-user-qa: teardown: taskkill.exe /F /PID <listed spawned Windows PID(s)>"
