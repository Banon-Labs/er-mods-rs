#!/usr/bin/env python3
"""Watch the Steam lobby-list filters a search in progress is querying with. Drives no input.

Seamless re-issues `RequestLobbyList` every ~16s while searching, preceded by four
`AddRequestLobbyListStringFilter` calls. One of them is `lobby_key`, which the decrypted image
shows is `hex(SHA256(password + modeDigit + salt))`. If a live edit to the loaded param table moved
that key, this is where it shows: the key here would differ from the one captured before the edit.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from er_pad_frames import Pad  # noqa: E402

SESSION_MANAGER = 0x143D7E540
HOOK = """
const steam = Process.findModuleByName('lsteamclient.dll');
const SESSION = ptr('0x143d7e540');
function str (p) { try { return p.isNull() ? null : p.readUtf8String(); } catch (e) { return '?'; } }
if (steam !== null) {
  Interceptor.attach(steam.base.add(0x8ac80), {
    onEnter (args) { send({ kind: 'filter', key: str(args[1]), value: str(args[2]) }); },
  });
  Interceptor.attach(steam.base.add(0x8ba60), { onEnter () { send({ kind: 'request' }); } });
  Interceptor.attach(steam.base.add(0x8b7e0), { onEnter () { send({ kind: 'join' }); } });
}
rpc.exports = {
  ok () { return steam !== null; },
  state () {
    const s = SESSION.readPointer();
    return s.isNull() ? -1 : s.add(0xc).readU32();
  },
};
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=8, help="settle rounds of ~600 frames each")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        assert "0x8ac80" in HOOK and "0x8ba60" in HOOK
        print("selftest: ok")
        return 0

    import frida

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    events: queue.Queue = queue.Queue()
    seen: list[dict] = []

    script = sess.create_script(HOOK)
    script.on("message", lambda m, _d: events.put(m["payload"]) if m.get("type") == "send" else None)
    script.load()
    print(f"lsteamclient hooked: {script.exports_sync.ok()}  session state={script.exports_sync.state():#x}",
          flush=True)
    pad = Pad(sess)

    states = []
    for _ in range(args.rounds):
        pad.tap(0, hold_frames=0, gap_frames=600)
        while not events.empty():
            seen.append(events.get_nowait())
        states.append(script.exports_sync.state())
        if len([e for e in seen if e.get("kind") == "filter"]) >= 4:
            break
    while not events.empty():
        seen.append(events.get_nowait())

    print(f"session state samples: {[hex(s) for s in states]}", flush=True)
    print(f"RequestLobbyList calls: {len([e for e in seen if e.get('kind') == 'request'])}", flush=True)
    print(f"JoinLobby calls:        {len([e for e in seen if e.get('kind') == 'join'])}", flush=True)
    found = [e for e in seen if e.get("kind") == "filter"]
    print(f"FILTERS ({len(found)}):", flush=True)
    for entry in found[:8]:
        print("   %-34s = %s" % (entry["key"], entry["value"]), flush=True)
    script.unload()
    sess.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
