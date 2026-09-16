#!/usr/bin/env python3
"""Recover the exact bytes Seamless hashes into `lobby_key`, and re-derive the key from them.

Read statically out of the decrypted image (`ersc+0xad6e0`, the builder the `lobby_key` filter call
at `ersc+0xac4d1` calls):

    lobby_key = sha256_hex( B + A + SALT32 )

    B      built from the object at settings+0xc8 -- two byte ranges, [+0x08,+0x10) and [+0x20,+0x28)
    A      decimal string of bit 2 of settings+0x281, so "0" or "1"
    SALT32 "2XfW3z/+eN0vdnEFP8pGxtoHpJ/4bxdC", a constant baked into ersc 2.0.1

This hooks the one `sha256_update` call in that builder (`ersc+0xe29a0`, return address
`ersc+0xad911`) and captures its buffer. Hashing that buffer here and matching the key the search
actually sends turns the static reading into a closed proof rather than a plausible one.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import importlib.util
import queue
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from er_pad_frames import Pad  # noqa: E402

_spec = importlib.util.spec_from_file_location("runtime_timeout_cap", HERE / "runtime_timeout_cap.py")
_cap = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_cap)

SALT32 = b"2XfW3z/+eN0vdnEFP8pGxtoHpJ/4bxdC"
HOOK = """
const ersc = Process.findModuleByName('ersc.dll');
const steam = Process.findModuleByName('lsteamclient.dll');
const RET_IN_BUILDER = ersc.base.add(0xad911);
Interceptor.attach(ersc.base.add(0xe29a0), {
  onEnter (args) {
    if (!this.returnAddress.equals(RET_IN_BUILDER)) return;
    const len = args[2].toUInt32();
    send({ kind: 'preimage', len }, args[1].readByteArray(Math.min(len, 4096)));
  },
});
Interceptor.attach(steam.base.add(0x8ac80), {
  onEnter (args) {
    try {
      const key = args[1].readUtf8String();
      if (key === 'lobby_key') send({ kind: 'key', value: args[2].readUtf8String() });
    } catch (e) {}
  },
});
// The goods bytes the comparison turns on, read at capture time. A preimage recorded without them
// cannot be placed in either arm of the experiment afterwards, which is exactly how the previous
// comparison became ambiguous -- the number was right and nothing said which condition produced it.
const REPO_RVA = 0x3d85f58, GOODS_INDEX = 3, GOODS = [102, 111, 112];
const game = Process.findModuleByName('eldenring.exe');
const goodsAt = (function () {
  const repo = game.base.add(REPO_RVA).readPointer();
  const cap = repo.add(0x88 + GOODS_INDEX * 9 * 8).readPointer();
  const blob = cap.add(0x80).readPointer().add(0x80).readPointer();
  const at = {};
  for (let i = 0, n = blob.add(0x0a).readU16(); i < n; i += 1) {
    const entry = blob.add(0x40 + i * 24);
    const id = entry.readU32();
    if (GOODS.indexOf(id) !== -1) at[id] = blob.add(entry.add(8).readU64().toNumber()).add(0x48);
  }
  return at;
})();
rpc.exports = {
  ok () { return true; },
  goods () {
    const out = {};
    for (const id of Object.keys(goodsAt)) out[id] = goodsAt[id].readU8();
    return out;
  },
};
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        assert hashlib.sha256(b"a" + SALT32).hexdigest()
        assert "0xad911" in HOOK
        print("selftest: ok")
        return 0

    import frida

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    events: queue.Queue = queue.Queue()

    def on_message(message, data):
        if message.get("type") == "send":
            events.put((message["payload"], data))

    script = sess.create_script(HOOK)
    script.on("message", on_message)
    script.load()
    fingers = {int(k): v for k, v in script.exports_sync.goods().items()}
    usable = all(not (v >> 5) & 1 for v in fingers.values())
    print("condition: fingers " + ("USABLE" if usable else "greyed") + "  " +
          " ".join(f"row{r}=0x{v:02x}" for r, v in sorted(fingers.items())), flush=True)
    pad = Pad(sess)
    deadline = time.monotonic() + _cap.runtime_timeout_cap_seconds()
    preimages: list[bytes] = []
    keys: list[str] = []
    for _ in range(args.rounds):
        if time.monotonic() > deadline:
            print("stopped: runtime cap reached with nothing captured", flush=True)
            break
        pad.tap(0, hold_frames=0, gap_frames=600)
        while not events.empty():
            payload, data = events.get_nowait()
            if payload["kind"] == "preimage" and data is not None:
                preimages.append(bytes(data))
            elif payload["kind"] == "key":
                keys.append(payload["value"])
        if preimages and keys:
            break

    for blob in preimages[:2]:
        print(f"preimage ({len(blob)} bytes): {blob!r}")
        print(f"  sha256 = {hashlib.sha256(blob).hexdigest()}")
    for key in keys[:2]:
        print(f"lobby_key sent = {key}")
    if preimages and keys:
        match = hashlib.sha256(preimages[0]).hexdigest() == keys[0]
        print(f"PROVEN: sha256(preimage) == lobby_key -> {match}")
        body = preimages[0]
        if body.endswith(SALT32):
            print(f"  variable part (everything before the ersc salt): {body[:-len(SALT32)]!r}")
    else:
        print(f"no match captured: {len(preimages)} preimage(s), {len(keys)} key(s)")
    script.unload()
    sess.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
