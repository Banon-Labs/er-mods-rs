#!/usr/bin/env bash
# Run an Elden Ring launch inside gamescope's own nested X server, and keep gamescope alive.
#
# Why the nested server: wine opens one X11 connection per thread, and the host Xwayland here
# has been sitting at its 256-client ceiling with a long-lived Steam client holding ~198 of the
# slots. The game's later threads then get NULL from XOpenDisplay and it dies inside
# vkCreateSwapchainKHR with a black window and no crash dump. gamescope brings up its own X
# server with a fresh pool, which sidesteps that without touching anyone's Steam session.
# See bd `xwayland-maxclients-starvation-kills-er-boot-2026-09-14`.
#
# Why the wait loop: gamescope shuts its whole session down the moment its primary child exits,
# and `er-run-branch.py` is a launcher -- it returns as soon as the game is up. Handing it to
# gamescope directly therefore kills the game a second after it starts, and the corpse looks
# exactly like a crash: no exception in the Proton log, `outcome=running` in er-run-outcome.txt,
# and gamescope's own "Primary child shut down!" is the only clue. Measured 2026-09-15; two runs
# were misread as game deaths before the harness was found to be the killer.
set -u

REPO_ROOT="${ER_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

python3 "$REPO_ROOT/scripts/er-run-branch.py" "$@"
status=$?
if [ "$status" -ne 0 ]; then
    echo "[gamescope-er-run] launch failed with $status; not holding the session open" >&2
    exit "$status"
fi

# er-run-branch.py returns only after the DLL's own log line proves the game is up, so there is
# nothing to wait for here -- the watcher blocks on a pidfd, which the kernel makes readable when
# the process actually dies. A sleep loop would be a guess about a time the kernel already knows.
echo "[gamescope-er-run] holding the gamescope session until the game exits"
python3 "$REPO_ROOT/scripts/wait-for-game-exit.py"
echo "[gamescope-er-run] releasing the gamescope session"
