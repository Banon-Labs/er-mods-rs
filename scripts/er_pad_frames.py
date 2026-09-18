"""Frame-counted controller taps, with no sleeps anywhere in the drive path.

A button hold is a number of game frames, asserted from inside the game's own per-frame pad
builder, and the caller blocks on the completion message that builder sends.  That removes the two
things `scripts/check-no-timeouts.py` bans -- sleep as synchronization and unbounded waits -- and it
removes a real bug with them: a hold expressed in seconds is a different number of frames every run.
"""
from __future__ import annotations

import pathlib
import queue

REPO = pathlib.Path(__file__).resolve().parent.parent

PAD_A = 0x1000
PAD_B = 0x2000
PAD_X = 0x4000
PAD_UP = 0x0001
PAD_DOWN = 0x0002
PAD_LEFT = 0x0004
PAD_RIGHT = 0x0008

# A press a menu reads as one clean edge: asserted long enough to be sampled, released long enough
# that the next assert is a new edge rather than auto-repeat.
HOLD_FRAMES = 22
GAP_FRAMES = 34
# Hard cap on any single wait. Every wait in this module is bounded by it.
WAIT_SECONDS = 30.0


class Pad:
    """A frame-counted pad. One tap in flight at a time, each acknowledged by the game thread."""

    def __init__(self, session):
        self._done: queue.Queue = queue.Queue()
        self._script = session.create_script(
            (REPO / "scripts/frida/pad-frames.js").read_text())
        self._script.on("message", self._on_message)
        self._script.load()
        self._next_id = 0
        # Start from neutral, because the previous driver may not have.
        #
        # `pad-frames.js` only writes pad state while a tap is pending, so a run killed mid-hold
        # leaves its mask asserted for the rest of the process's life. The game then sees a button
        # held forever, which produces no new edge: menus stop responding, the quick-item cursor
        # freezes, and every later driver reports "input is not reaching the game" while the pad is
        # in fact reaching it perfectly. Measured 2026-09-16 -- six D-pad presses moved the cursor
        # zero times across 342 game frames, and one release fixed it.
        self.release()

    def _on_message(self, message, _data):
        if message.get("type") == "send" and message["payload"].get("kind") == "tap-done":
            self._done.put(message["payload"])

    def tap(self, mask: int, hold_frames: int = HOLD_FRAMES, gap_frames: int = GAP_FRAMES,
            lx: int = 0, ly: int = 0) -> dict:
        self._next_id += 1
        started = self._script.exports_sync.tap(
            self._next_id, mask, hold_frames, gap_frames, lx, ly)
        if not started.get("ok"):
            raise RuntimeError(f"pad tap refused: {started.get('why')}")
        return self._done.get(timeout=WAIT_SECONDS)

    def frames(self) -> int:
        return self._script.exports_sync.frames()

    def release(self) -> None:
        self._script.exports_sync.release()

    # Releasing on construction only helps the NEXT driver. A driver killed or raising mid-hold
    # leaves the mask asserted until something else builds a `Pad`, and in the meantime the player
    # has a game that answers no input -- on 2026-09-18 that cost a relaunch mid-session. Used as
    # a context manager the release happens on every exit path, including an exception.
    def __enter__(self) -> "Pad":
        return self

    def __exit__(self, *_exc) -> None:
        try:
            self.release()
        except Exception:  # noqa: BLE001 -- a dead session cannot be released, and must not mask the real error
            pass


def wait_for(events: queue.Queue, match, seconds: float = WAIT_SECONDS):
    """Block until an event satisfies `match`, or the hard cap elapses. Never polls."""
    deadline_left = seconds
    while deadline_left > 0:
        try:
            event = events.get(timeout=deadline_left)
        except queue.Empty:
            return None
        if match(event):
            return event
        deadline_left = seconds
    return None
