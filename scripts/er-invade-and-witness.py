#!/usr/bin/env python3
"""Drive an invasion item, capture what the search advertises with, and witness world entry.

Three things at once, because they are three views of one event and a separate run for each would
compare different searches:

  the key      `lobby_key` is `sha256_hex(B + A + SALT32)`, read out of `ersc+0xad6e0`. The
               `sha256_update` call at `ersc+0xad90c` carries the whole preimage, so hashing it
               here and matching the string the search sends closes the reading.
  the filters  every `AddRequestLobbyListStringFilter` the search queries with.
  the landing  a foreign world shows as a NEW main-player pointer with the session past `0x16`;
               the SpEffect list says which side of the invasion the player is on.

# Why this cannot run forever

Every wait is frame-counted and the whole run carries a wall-clock deadline taken from
`.auto/runtime_timeout_cap_seconds` through the canonical reader, checked before each round and
before each blocking tap. It needs no shell `timeout` wrapper, and wrapping it in one would be a
second, drifting copy of a cap this repo keeps in exactly one place.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import pathlib
import queue
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from er_pad_frames import PAD_A, PAD_DOWN, PAD_RIGHT, PAD_X, Pad  # noqa: E402

_spec = importlib.util.spec_from_file_location("runtime_timeout_cap", HERE / "runtime_timeout_cap.py")
_cap = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_cap)

ITEMS = {"lynchpin": 0x407FDE63, "finger": 0x4000006F}
SALT32 = b"2XfW3z/+eN0vdnEFP8pGxtoHpJ/4bxdC"
FINGER_ACTIVE_SPEFFECT = 541
ROUND_FRAMES = 600
HOOK = """
const ersc = Process.findModuleByName('ersc.dll');
const steam = Process.findModuleByName('lsteamclient.dll');
const WORLD_CHR_MAN = ptr('0x143d69ff8');
const SESSION = ptr('0x143d7e540');
const RET_IN_BUILDER = ersc.base.add(0xad911);
Interceptor.attach(ersc.base.add(0xe29a0), {
  onEnter (args) {
    if (!this.returnAddress.equals(RET_IN_BUILDER)) return;
    send({ kind: 'preimage' }, args[1].readByteArray(Math.min(args[2].toUInt32(), 4096)));
  },
});
function str (p) { try { return p.isNull() ? null : p.readUtf8String(); } catch (e) { return '?'; } }
Interceptor.attach(steam.base.add(0x8ac80), {
  onEnter (args) { send({ kind: 'filter', key: str(args[1]), value: str(args[2]) }); },
});
Interceptor.attach(steam.base.add(0x8ba60), { onEnter () { send({ kind: 'request' }); } });
Interceptor.attach(steam.base.add(0x8b7e0), { onEnter () { send({ kind: 'join' }); } });
function player () {
  const w = WORLD_CHR_MAN.readPointer();
  if (w.isNull()) return null;
  const p = w.add(0x1e508).readPointer();
  return p.isNull() ? null : p;
}
const MENU_MAN = ptr('0x143d6f820');
rpc.exports = {
  // The popup's own answer slot. `-1` is a dialog that is up and waiting; anything else is the row
  // the game took. It is the only readable statement of when a press can land, and a frame count
  // guessed in its place presses into an animation that has not finished.
  popup () {
    const m = MENU_MAN.readPointer();
    if (m.isNull()) return null;
    const p = m.add(0x80).readPointer();
    if (p.isNull()) return null;
    try { return { at: p.toString(), answer: p.add(0x1a0).readS32(), row: p.add(0x1a4).readS32() }; }
    catch (e) { return null; }
  },
  sample () {
    const s = SESSION.readPointer();
    const p = player();
    const out = { state: s.isNull() ? -1 : s.add(0xc).readU32(),
                  player: p === null ? null : p.toString(), speffects: [] };
    if (p !== null) {
      try {
        let e = p.add(0x178).readPointer().add(0x08).readPointer();
        for (let i = 0; i < 512 && !e.isNull(); i++) {
          out.speffects.push(e.add(8).readS32());
          e = e.add(0x30).readPointer();
        }
      } catch (err) {}
    }
    return out;
  },
};
"""


def focus_game() -> None:
    """Addressed by window class alone, so no other window is ever named or listed."""
    subprocess.run(["hyprctl", "dispatch", 'hl.dsp.focus({ window = "class:steam_app_1245620" })'],
                   capture_output=True, timeout=10)


def wait_for(condition, pad: Pad, deadline: float, frames: int = 30, tries: int = 60) -> bool:
    """Block on a game-state condition, advancing in game frames and never in wall-clock sleeps."""
    for _ in range(tries):
        if condition():
            return True
        if time.monotonic() > deadline:
            return False
        pad.tap(0, hold_frames=0, gap_frames=frames)
    return condition()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--item", default="lynchpin", choices=sorted(ITEMS))
    ap.add_argument("--rounds", type=int, default=24, help=f"rounds of {ROUND_FRAMES} game frames")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        assert "0xad911" in HOOK and "0x8ac80" in HOOK and "0x1e508" in HOOK
        assert _cap.runtime_timeout_cap_seconds() > 0
        print(f"selftest: ok (deadline {_cap.runtime_timeout_cap_seconds()}s)")
        return 0

    import frida

    deadline = time.monotonic() + _cap.runtime_timeout_cap_seconds()
    focus_game()
    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    events: queue.Queue = queue.Queue()
    script = sess.create_script(HOOK)
    script.on("message",
              lambda m, d: events.put((m["payload"], d)) if m.get("type") == "send" else None)
    script.load()
    cycle = sess.create_script((REPO / "scripts/frida/native-quickslot-cycle.js").read_text())
    cycle.load()
    pad = Pad(sess)

    start = script.exports_sync.sample()
    print(f"before: state={start['state']:#x} player={start['player']} "
          f"finger_active={FINGER_ACTIVE_SPEFFECT in start['speffects']}", flush=True)

    target = ITEMS[args.item]
    for _ in range(20):
        if cycle.exports_sync.selected()["id"] == target or time.monotonic() > deadline:
            break
        pad.tap(PAD_DOWN)
    got = cycle.exports_sync.selected()["id"]
    if got != target:
        print(f"refusing: cursor is on {got:#x}, never reached {args.item} -- a dialog may hold it")
        return 5
    print(f"cursor on {args.item}", flush=True)
    # Both items raise a confirmation dialog, and an unanswered dialog means no search and so no
    # world entry ever. The use animation has to finish before the dialog exists, so the accept
    # waits on the popup's own answer slot reading `-1` rather than on a frame count.
    pad.tap(PAD_X)
    if not wait_for(lambda: (script.exports_sync.popup() or {}).get("answer") == -1,
                    pad, deadline):
        print("refusing: no dialog came up after the use -- nothing to accept, so no search")
        return 6
    if args.item == "finger":
        pad.tap(PAD_RIGHT)
    pad.tap(PAD_A)
    if not wait_for(lambda: (script.exports_sync.popup() or {"answer": 0})["answer"] != -1,
                    pad, deadline):
        print("refusing: the dialog never took an answer")
        return 7
    answered = script.exports_sync.popup()
    print(f"dialog answered: {answered}", flush=True)

    preimages: list[bytes] = []
    filters: list[dict] = []
    counts = {"request": 0, "join": 0}
    landed = None
    stopped = "rounds exhausted"
    for round_no in range(args.rounds):
        if time.monotonic() > deadline:
            stopped = "runtime cap reached"
            break
        pad.tap(0, hold_frames=0, gap_frames=ROUND_FRAMES)
        while not events.empty():
            payload, data = events.get_nowait()
            kind = payload["kind"]
            if kind == "preimage" and data is not None:
                preimages.append(bytes(data))
            elif kind == "filter":
                filters.append(payload)
            elif kind in counts:
                counts[kind] += 1
        now = script.exports_sync.sample()
        if now["player"] != start["player"] and now["state"] >= 0x16:
            landed = (round_no, now)
            stopped = "landed"
            break
        if round_no % 3 == 0:
            print(f"  round {round_no:2d} state={now['state']:#x} requests={counts['request']} "
                  f"joins={counts['join']} player={now['player']}", flush=True)

    if preimages:
        blob = preimages[0]
        print(f"\npreimage ({len(blob)} bytes): {blob!r}")
        print(f"  sha256 = {hashlib.sha256(blob).hexdigest()}")
        if blob.endswith(SALT32):
            print(f"  before the ersc salt: {blob[:-len(SALT32)]!r}")
    for entry in filters[:4]:
        print(f"  filter {entry['key'][:20]:20s} = {entry['value']}")
    print(f"RequestLobbyList={counts['request']}  JoinLobby={counts['join']}  stopped: {stopped}")
    if landed:
        round_no, now = landed
        print(f"\nWORLD ENTERED on round {round_no}: state={now['state']:#x} "
              f"player {start['player']} -> {now['player']}")
        print(f"  speffects now: {sorted(set(now['speffects']))[:24]}")
    else:
        final = script.exports_sync.sample()
        print(f"\nNO LANDING: state={final['state']:#x} player still {final['player']}")
    script.unload()
    sess.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
