#!/usr/bin/env python3
"""Capture the Steam lobby-list filters a Seamless invasion search actually queries with.

Seamless's search is four equality filters on `AddRequestLobbyListStringFilter`, repeated on every
`RequestLobbyList`.  Three are constants -- a boolean, the `yknx3_seamless_master_lobby` marker, and
`lobby_key`, which the decrypted image shows is `hex(SHA256(password + modeDigit + salt))`.  The
fourth carries a short value such as `0_0`, and that is the one a *Nearby only* answer and a *Both
near and far* answer should differ on.

So this is the instrument for the question the redirect work runs into: driving `ersc+0x25850`
starts a genuinely live search, but if the search is querying the wrong pool it can never match.
Run it once driving the Challenger's Lynchpin and once driving the finger's redirect, and diff.

It also prints the session owner the Lynchpin's own accept passes to `ersc+0x25850`, which is the
pointer a redirect has to be armed with and which changes every launch.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from er_pad_frames import PAD_A, PAD_DOWN, PAD_RIGHT, PAD_X, Pad  # noqa: E402

ITEMS = {"lynchpin": 0x407FDE63, "finger": 0x4000006F}
FILTER_HOOK = """
const steam = Process.findModuleByName('lsteamclient.dll');
function str (p) { try { return p.isNull() ? null : p.readUtf8String(); } catch (e) { return '?'; } }
if (steam !== null) {
  Interceptor.attach(steam.base.add(0x8ac80), {
    onEnter (args) { send({ kind: 'filter', key: str(args[1]), value: str(args[2]) }); },
  });
  Interceptor.attach(steam.base.add(0x8ba60), {
    onEnter () { send({ kind: 'request' }); },
  });
}
rpc.exports = { ok () { return steam !== null; } };
"""


def focus_game() -> None:
    """Elden Ring throws injected input away while unfocused. Addressed by class alone, so no
    other window is ever named or listed."""
    subprocess.run(
        ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "class:steam_app_1245620" })'],
        capture_output=True, timeout=10)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--item", default="lynchpin", choices=sorted(ITEMS))
    ap.add_argument("--rounds", type=int, default=30,
                    help="how many settle rounds to wait for four filters")
    ap.add_argument("--redirect", metavar="OWNER",
                    help="drive the finger's confirm into ersc+0x25850 with this owner, so the "
                         "search it starts can be compared against a Lynchpin search's filters")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        assert "0x8ac80" in FILTER_HOOK, "the filter hook lost its address"
        assert ITEMS["lynchpin"] == 0x407FDE63
        print("selftest: ok")
        return 0

    import frida

    focus_game()
    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    events: queue.Queue = queue.Queue()
    seen: list[dict] = []

    def on_message(message, _data):
        if message.get("type") == "send":
            events.put(message["payload"])

    def agent(name: str):
        script = sess.create_script((REPO / f"scripts/frida/{name}.js").read_text())
        script.on("message", on_message)
        script.load()
        return script

    menu = agent("ersc-menu-accept")
    actions = agent("ersc-action-trace")
    cycle = agent("native-quickslot-cycle")
    redirect = agent("finger-redirect-to-seamless")
    filters = sess.create_script(FILTER_HOOK)
    filters.on("message", on_message)
    filters.load()
    pad = Pad(sess)

    def settle(frames: int = 120) -> None:
        pad.tap(0, hold_frames=0, gap_frames=frames)

    def collect() -> None:
        while not events.empty():
            seen.append(events.get_nowait())

    target = ITEMS[args.item]
    for _ in range(20):
        collect()
        if cycle.exports_sync.selected()["id"] == target:
            break
        pad.tap(PAD_DOWN)
    if cycle.exports_sync.selected()["id"] != target:
        print(f"refusing: the cursor never reached {args.item} -- a dialog may be holding the ring")
        return 5
    print(f"cursor on {args.item}", flush=True)

    pad.tap(PAD_X)
    settle(180)
    if args.item == "finger":
        # The two rows sit left/right with the cursor on the left, so Both near and far needs one
        # move. The answer index reported back is the proof of which row the game actually took.
        pad.tap(PAD_RIGHT)
    if args.redirect:
        armed = redirect.exports_sync.arm(args.redirect, 2)
        print(f"redirect armed: {armed}", flush=True)
    pad.tap(PAD_A)
    for _ in range(args.rounds):
        settle()
        collect()
        if len([e for e in seen if e.get("kind") == "filter"]) >= 4:
            break
    collect()

    accepts = [e for e in seen if e.get("kind") == "menu-accept"]
    for accept in accepts:
        record = accept["record"]
        print(f"menu accept: session={record.get('session')} state={record.get('state')} "
              f"rows={record.get('rowCount')} caller={record.get('caller')}", flush=True)
    lines = [e["line"].splitlines()[0] for e in seen if e.get("kind") == "action"]
    print(f"ersc actions: {lines or 'none'}", flush=True)
    print(f"RequestLobbyList calls: {len([e for e in seen if e.get('kind') == 'request'])}", flush=True)
    found = [e for e in seen if e.get("kind") == "filter"]
    print(f"FILTERS ({len(found)}):", flush=True)
    for entry in found[:8]:
        print("   %-66s = %s" % (entry["key"], entry["value"]), flush=True)
    print(f"redirect: {redirect.exports_sync.report()['log']}", flush=True)
    print(f"action counts: {actions.exports_sync.report()['counts']}", flush=True)
    print(f"menu report rows: {len(menu.exports_sync.report()['calls'])}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
