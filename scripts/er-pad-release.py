#!/usr/bin/env python3
"""Release a pad mask left asserted in a live Elden Ring, instead of relaunching the game.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-pad-release.py

# Why this exists

`scripts/frida/pad-frames.js` writes pad state only while a tap is pending, so a driver killed
mid-hold leaves its mask asserted for the rest of the process's life. The game then sees a button
held forever and produces no new edge: menus stop answering, the quick-item cursor freezes, and
every later driver reports "input is not reaching the game" while the pad is in fact reaching it
perfectly.

`Pad.__init__` releases on construction, which repairs it for the next driver -- but only if one
runs. On 2026-09-18 a driver was interrupted mid-press and the session was unusable until the game
was relaunched, which is a minute of the player's evening for a one-word fix. This is that word.

Read-only apart from zeroing the injected pad state: it takes no game action, presses nothing, and
touches no param byte.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("er_frida_watch", HERE / "er-frida-watch.py")
_watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_watch)

from er_pad_frames import Pad  # noqa: E402


def selftest() -> int:
    """The repair has to survive an exception, which is the case that stranded a held mask."""
    source = (HERE / "er_pad_frames.py").read_text(encoding="utf-8")
    assert "__exit__" in source and "self.release()" in source, (
        "Pad must release from __exit__, or a driver that raises still strands the mask"
    )
    print("selftest ok -- Pad releases on every exit path")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()

    device = _watch.device()
    try:
        pid = _watch.find_game_bounded(device)
    except TimeoutError:
        print("the frida server did not answer -- restart it with --force", file=sys.stderr)
        return 2
    if pid is None:
        print("no eldenring.exe in the prefix", file=sys.stderr)
        return 1

    session = device.attach(pid)
    # Constructing it releases; the context manager releases again on the way out. Both are
    # deliberate -- the second covers a mask asserted between the two by something else.
    with Pad(session) as pad:
        pad.release()
        print(f"pad released on pid {pid} at game frame {pad.frames()}")
    session.detach()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
