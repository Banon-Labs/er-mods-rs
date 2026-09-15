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
# 2. gamescope's window opens on whatever output the compositor gives it, and for this game that
#    has to be one specific monitor. The user's own Hyprland config pins Elden Ring to workspace
#    2 with the reason written beside it: the game positions itself from XWayland's flat
#    11520x2160 RandR row, which disagrees with this T-shaped layout, so from any monitor but
#    DP-1 it drifts off-canvas once loading passes the first map index. gamescope is the window
#    now, so the same pin has to be applied to gamescope -- following the focused monitor
#    instead, which this script did first, reproduces exactly the bug that rule exists to fix.
#
#    Targeted by WORKSPACE rather than by monitor name so it keeps following the user's own
#    workspace_rule binding if they ever move it, the same way their window rule does.
#
# Everything after `--` is passed to scripts/er-run-branch.py unchanged.
set -u

REPO_ROOT="${ER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# DP-1's logical size, and the same numbers the user's `elden-ring-float-center` window rule
# gives the game. They have to match: gamescope scales its nested output to its window, so a
# nested size that differs from the output means the cursor position imgui sees is not where the
# pointer actually is. Measured 2026-09-15 at 2560x1440 against a 3072x1728 output -- the
# settings panel drew 7920 frames and registered zero clicks while eleven were being suppressed
# from the game, which on screen is a panel that ignores you and a character that swings anyway.
WIDTH="${ER_GAMESCOPE_WIDTH:-3072}"
HEIGHT="${ER_GAMESCOPE_HEIGHT:-1728}"

# Workspace 2 is DP-1 here, matching the `elden-ring-float-center` rule in the user's
# hyprland.lua. Override only if that binding changes.
TARGET_WORKSPACE="${ER_GAMESCOPE_WORKSPACE:-2}"


gamescope --backend wayland -W "$WIDTH" -H "$HEIGHT" -f \
    -- bash "$REPO_ROOT/scripts/gamescope-er-run.sh" "$@" &
gamescope_pid=$!

if [ -n "$TARGET_WORKSPACE" ]; then
    # Blocks on Hyprland's own openwindow event rather than polling for the window to appear;
    # scripts/check-no-timeouts.py bans the poll, and the event is the thing being waited for.
    python3 "$REPO_ROOT/scripts/hypr-place-window.py" \
        --class gamescope --workspace "$TARGET_WORKSPACE" &
else
    echo "[er-run-gamescope] no target workspace set; leaving placement to the compositor" >&2
fi

wait "$gamescope_pid"
