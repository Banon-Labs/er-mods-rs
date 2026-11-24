#!/usr/bin/env bash
# Open a new kitty split tailing a dynamic-workflow run with color/formatting.
#
# Usage: scripts/watch-workflow.sh [RUNID|latest|/abs/run/dir] [--full] [--tab]
#   RUNID   a wf_... id (default: latest run under this project)
#   --full  include agent reasoning text, not just tool calls + results
#   --tab   open a new tab instead of a split in the agent's own tab
#
# The split is a new kitty window in the tab holding $KITTY_WINDOW_ID, the agent's window
# (user request 2026-11-07), placed the way scripts/kitty-user-command.py's `placement` does.
# Requires kitty remote control (KITTY_LISTEN_ON set; `allow_remote_control yes`).
# Falls back to a separate kitty window, then to running inline, if it is off.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RENDER="$REPO/scripts/watch-workflow.py"

# First non-flag positional is the run spec; `--tab` is this launcher's own; everything else
# (flags + their values, e.g. --full, --result-lines N) is forwarded to the renderer verbatim.
ARG="latest"; PASS=(); expect_val=0; got_arg=0; KIND="split"
for a in "$@"; do
  if [[ $expect_val == 1 ]]; then PASS+=("$a"); expect_val=0; continue; fi
  case "$a" in
    --tab) KIND=tab ;;
    --result-lines) PASS+=("$a"); expect_val=1 ;;
    --*) PASS+=("$a") ;;
    *) if [[ $got_arg == 0 ]]; then ARG="$a"; got_arg=1; else PASS+=("$a"); fi ;;
  esac
done

# Resolve the run dir up front so the window title can name it and a bad spec fails
# here, not in a detached window. The renderer prints the dir on header line 2.
RUNDIR="$(python3 "$RENDER" "$ARG" --no-follow 2>/dev/null | sed -n '2p' | sed -E 's/\x1b\[[0-9;]*m//g')"
TITLE="wf:$(basename "${RUNDIR:-run}")"

CMD=(python3 "$RENDER" "$ARG" --follow "${PASS[@]}")

# --match is a tab match for a launch, and window_id:<n> is the tab holding window n; kitty
# ignores --next-to unless that window is in the target tab, so a split gives both.
if [[ $KIND == tab ]]; then
  PLACE=(--type=tab --tab-title "$TITLE"); WHERE="a new tab"
elif [[ "${KITTY_WINDOW_ID:-}" =~ ^[0-9]+$ ]]; then
  PLACE=(--type=window --location=split --window-title "$TITLE"
    --match "window_id:$KITTY_WINDOW_ID" --next-to "id:$KITTY_WINDOW_ID")
  WHERE="the agent's tab, next to window $KITTY_WINDOW_ID"
else
  PLACE=(--type=window --location=split --window-title "$TITLE")
  WHERE="the focused tab: KITTY_WINDOW_ID is not set, so the agent's own tab is unknown"
fi

if [[ -n "${KITTY_LISTEN_ON:-}" ]] && kitty @ --to "$KITTY_LISTEN_ON" ls >/dev/null 2>&1; then
  kitty @ --to "$KITTY_LISTEN_ON" launch "${PLACE[@]}" --keep-focus \
    --cwd "$REPO" -- "${CMD[@]}"
  echo "opened kitty $KIND '$TITLE' in $WHERE, tailing ${RUNDIR:-latest run} (focus kept on Claude)"
elif command -v kitty >/dev/null 2>&1; then
  kitty --title "$TITLE" --directory "$REPO" "${CMD[@]}" >/dev/null 2>&1 &
  echo "kitty remote control off; opened a separate kitty window '$TITLE'"
else
  echo "no kitty; running inline (Ctrl-C to stop):"
  exec "${CMD[@]}"
fi
