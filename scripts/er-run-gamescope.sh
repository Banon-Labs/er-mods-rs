#!/usr/bin/env bash
# Launch Elden Ring inside gamescope, on the monitor you are actually looking at.
#
# Two problems this solves, both measured 2026-09-15:
#
# 1. The host Xwayland sits at its 256-client ceiling here, with a long-lived Steam client
#    holding ~198 slots. Wine opens one X11 connection per thread, so the game's later threads
#    get NULL from XOpenDisplay and it dies inside vkCreateSwapchainKHR -- a black window, no
#    crash dump, and a failure that reads as a broken mod build. gamescope runs its own nested X
#    server with a fresh pool. See bd `xwayland-maxclients-starvation-kills-er-boot-2026-09-14`.
#
# 2. gamescope's window opens on whatever output the compositor gives it, which was not the one
#    the user was working on. AGENTS.md wants the game where the launcher is, not pinned to a
#    hard-coded monitor, so the focused workspace is read before gamescope starts -- afterwards
#    the answer is polluted by gamescope taking focus itself -- and the window is moved there.
#
# Everything after `--` is passed to scripts/er-run-branch.py unchanged.
set -u

REPO_ROOT="${ER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WIDTH="${ER_GAMESCOPE_WIDTH:-2560}"
HEIGHT="${ER_GAMESCOPE_HEIGHT:-1440}"

focused_workspace() {
    hyprctl monitors -j 2>/dev/null | python3 -c "
import json, sys
try:
    monitors = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for monitor in monitors:
    if monitor.get('focused'):
        print(monitor['activeWorkspace']['id'])
        break
" 2>/dev/null
}

gamescope_window() {
    hyprctl clients -j 2>/dev/null | python3 -c "
import json, sys
try:
    clients = json.load(sys.stdin)
except Exception:
    sys.exit(1)
# Only this one class is ever read or printed; AGENTS.md forbids dumping the window list,
# because it exposes every unrelated application the user happens to be running.
for client in clients:
    if (client.get('class') or '').lower() == 'gamescope':
        print(client['address'])
        break
" 2>/dev/null
}

target_workspace="$(focused_workspace)"

gamescope --backend wayland -W "$WIDTH" -H "$HEIGHT" -f \
    -- bash "$REPO_ROOT/scripts/gamescope-er-run.sh" "$@" &
gamescope_pid=$!

if [ -n "$target_workspace" ]; then
    # Blocks on Hyprland's own openwindow event rather than polling for the window to appear;
    # scripts/check-no-timeouts.py bans the poll, and the event is the thing being waited for.
    python3 "$REPO_ROOT/scripts/hypr-place-window.py" \
        --class gamescope --workspace "$target_workspace" &
else
    echo "[er-run-gamescope] could not read the focused monitor; leaving placement to the compositor" >&2
fi

wait "$gamescope_pid"
