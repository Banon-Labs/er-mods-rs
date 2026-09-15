#!/usr/bin/env python3
"""Block until `eldenring.exe` exits, without polling.

`scripts/gamescope-er-run.sh` needs to stay alive exactly as long as the game does, because
gamescope tears its whole session down the moment its primary child returns -- and
`er-run-branch.py` is a launcher, so it returns as soon as the game is up. Handing it to
gamescope directly killed the game a second after it started, and the corpse looked exactly like
a crash: no exception in the Proton log, `outcome=running` in er-run-outcome.txt, and gamescope's
"Primary child shut down!" the only clue. Measured 2026-09-15.

The wait is a `pidfd`, which the kernel makes readable when the process dies, so `select` blocks
on the real event. `scripts/check-no-timeouts.py` bans sleep loops for good reason: a poll
interval is a guess about how long something takes and it is wrong in both directions.

The game is already up by the time this runs -- `er-run-branch.py` proves that from the DLL's own
log line before it returns -- so a missing process here means it exited during the handover, which
is reported rather than waited out.
"""

from __future__ import annotations

import os
import select
import sys

GAME_COMM = "eldenring.exe"


def game_pid() -> int | None:
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", encoding="utf-8") as handle:
                if handle.read().strip() == GAME_COMM:
                    return int(entry)
        except OSError:
            continue
    return None


def main() -> int:
    pid = game_pid()
    if pid is None:
        print(f"wait-for-game-exit: no {GAME_COMM} is running", file=sys.stderr)
        return 1
    try:
        fd = os.pidfd_open(pid, 0)
    except (OSError, AttributeError) as exc:
        print(f"wait-for-game-exit: cannot watch pid {pid}: {exc}", file=sys.stderr)
        return 1
    print(f"wait-for-game-exit: watching {GAME_COMM} pid {pid}", flush=True)
    try:
        select.select([fd], [], [])
    finally:
        os.close(fd)
    print(f"wait-for-game-exit: {GAME_COMM} pid {pid} has exited", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
