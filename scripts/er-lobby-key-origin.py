#!/usr/bin/env python3
"""Find, in ersc's own code, where the `lobby_key` filter value comes from.

Themida defeats every purely static entry point into this: the filter strings are not in the
decrypted image, the import table carries one stub slot per DLL with no names, and the resolved
Steamworks pointers are not stored in plain form, so an IAT scan finds nothing to xref. What is
left is one measurement -- the return address of the call, and the address the value string lives
at -- which turns the static read from a search into a read of a known function.
"""
from __future__ import annotations

import argparse
import pathlib
import queue
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from er_pad_frames import Pad  # noqa: E402

HOOK = """
const steam = Process.findModuleByName('lsteamclient.dll');
const ersc = Process.findModuleByName('ersc.dll');
const lo = ersc.base, hi = ersc.base.add(ersc.size);
function str (p) { try { return p.isNull() ? null : p.readUtf8String(); } catch (e) { return '?'; } }
function inErsc (p) { return p.compare(lo) >= 0 && p.compare(hi) < 0; }
function erscFrames (ctx) {
  const out = [];
  // Backtrace first; where the frame pointer is absent, sweep the stack for return addresses.
  try {
    for (const f of Thread.backtrace(ctx, Backtracer.FUZZY)) {
      if (inErsc(f)) out.push('rva ' + f.sub(lo).toString(16));
      if (out.length >= 8) break;
    }
  } catch (e) {}
  if (out.length === 0) {
    for (let i = 0; i < 96; i++) {
      const slot = ctx.rsp.add(i * 8);
      try {
        const v = slot.readPointer();
        if (inErsc(v)) { out.push('stack+' + (i * 8).toString(16) + ' rva ' + v.sub(lo).toString(16)); }
      } catch (e) {}
      if (out.length >= 8) break;
    }
  }
  return out;
}
function where (p) {
  if (p.isNull()) return 'null';
  if (inErsc(p)) return 'ersc+0x' + p.sub(lo).toString(16);
  const m = Process.findModuleByAddress(p);
  return m ? m.name + '+0x' + p.sub(m.base).toString(16) : 'heap ' + p.toString();
}
Interceptor.attach(steam.base.add(0x8ac80), {
  onEnter (args) {
    send({
      kind: 'filter',
      key: str(args[1]),
      value: str(args[2]),
      valueAt: where(args[2]),
      keyAt: where(args[1]),
      callers: erscFrames(this.context),
    });
  },
});
rpc.exports = { base () { return ersc.base.toString(); } };
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        assert "0x8ac80" in HOOK and "Backtracer.FUZZY" in HOOK
        print("selftest: ok")
        return 0

    import frida

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    events: queue.Queue = queue.Queue()
    script = sess.create_script(HOOK)
    script.on("message", lambda m, _d: events.put(m["payload"]) if m.get("type") == "send" else None)
    script.load()
    print(f"ersc base {script.exports_sync.base()}", flush=True)
    pad = Pad(sess)
    seen: list[dict] = []
    for _ in range(args.rounds):
        pad.tap(0, hold_frames=0, gap_frames=600)
        while not events.empty():
            seen.append(events.get_nowait())
        if len(seen) >= 4:
            break
    for entry in seen[:8]:
        print(f"\nkey   {entry['key']}   [{entry['keyAt']}]")
        print(f"value {entry['value']}   [{entry['valueAt']}]")
        for frame in entry["callers"]:
            print(f"    caller {frame}")
    script.unload()
    sess.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
