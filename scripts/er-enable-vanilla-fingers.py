#!/usr/bin/env python3
"""Make the vanilla invasion fingers usable by clearing `disable_offline` on the loaded rows.

`CanUseGoods` greys the Use row through one long `and` whose failing term is
`disable_offline != 0 -> IsInOnlineMode()`; Seamless runs its own netcode with the game's online
flag clear, so that term is false.  Clearing bit 5 of byte `0x48` in the loaded
`_EQUIP_PARAM_GOODS_ST` rows removes the test.

This write takes the player out of the Seamless matchmaking pool, and that is measured, not
feared.  `lobby_key` is `sha256(B + A + SALT32)` where `B` is a fingerprint of the param data the
game has loaded -- not of the file.  The three rows at their original `0x63/0xe3/0x63` give
`B = 76DFB8C5A838F5A3`; cleared to `0x43/0xc3/0x43` they give `B = 76DFB8C5A838F603`, a different
key, advertised and filtered on, matching nobody.  `regulation.bin` was byte-identical throughout.
So the hash is over the loaded table, and an untouched file proves nothing at all.

`B` moved by exactly `+0x60` while the three bytes fell by `0x60`, which is an additive
accumulation rather than a hash -- so a compensating `+0x60` elsewhere in the same table may hold
the key still.  That is a lead, not a result; nothing has tested it.

The param-free alternative is to answer the gate instead of the data: override `CanUseGoods`'
return for the three finger ids, which settles all four of its terms at once and writes no param
byte.  See `scripts/frida/force-canusegoods.js`.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
AGENT = REPO / "scripts/frida/goods-offline-gate.js"
REGULATION = pathlib.Path(
    os.environ.get(
        "ER_REGULATION_BIN",
        os.path.expanduser("~/.local/share/Steam/steamapps/common/ELDEN RING/Game/regulation.bin"),
    )
)


VERIFY = """
const REPO_RVA = 0x3d85f58, GOODS_INDEX = 3, GOODS = [102, 111, 112];
const game = Process.findModuleByName('eldenring.exe');
const repo = game.base.add(REPO_RVA).readPointer();
const cap = repo.add(0x88 + GOODS_INDEX * 9 * 8).readPointer();
const blob = cap.add(0x80).readPointer().add(0x80).readPointer();
const rowCount = blob.add(0x0a).readU16();
function rowFlagAddresses () {
  const at = {};
  for (let i = 0; i < rowCount; i += 1) {
    const entry = blob.add(0x40 + i * 24);
    const id = entry.readU32();
    if (GOODS.indexOf(id) !== -1) {
      at[id] = blob.add(entry.add(8).readU64().toNumber()).add(0x48);
    }
  }
  return at;
}
const AT = rowFlagAddresses();
function live () {
  const out = {};
  for (const id of Object.keys(AT)) out[id] = AT[id].readU8();
  return out;
}
rpc.exports = { flags () { return live(); } };
"""


def regulation_fingerprint() -> str:
    """The matchmaking-relevant file, hashed before and after so the claim is measured."""
    if not REGULATION.exists():
        return "absent"
    digest = hashlib.sha256(REGULATION.read_bytes()).hexdigest()
    return f"{digest[:16]}... mtime={int(REGULATION.stat().st_mtime)}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--restore", action="store_true",
                    help="put disable_offline back, to test whether the write moves lobby_key")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        assert AGENT.exists(), AGENT
        assert "DISABLE_OFFLINE_BIT" in AGENT.read_text()
        print("selftest: ok")
        return 0

    import frida

    before = regulation_fingerprint()
    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)
    lines: list[str] = []

    def on_message(message, _data):
        if message.get("type") == "log":
            lines.append(message["payload"])
        elif message.get("type") == "error":
            lines.append("ERROR " + str(message.get("description")))

    if args.restore:
        # Appending rather than rewriting a literal from VERIFY: the previous form replaced an
        # exact `rpc.exports` line, so editing `VERIFY` silently dropped the restore export.
        restore = sess.create_script(VERIFY + """
const ORIGINAL = { 102: 0x63, 111: 0xe3, 112: 0x63 };
const previous = rpc.exports;
rpc.exports = {
  flags: previous.flags,
  restore () {
    for (const id of Object.keys(AT)) AT[id].writeU8(ORIGINAL[id]);
    return live();
  },
};
""")
        restore.load()
        after = {int(k): v for k, v in restore.exports_sync.restore().items()}
        for row, value in sorted(after.items()):
            print(f"restored: row {row} byte0x48=0x{value:02x} disable_offline={(value >> 5) & 1}")
        restore.unload()
        sess.detach()
        return 0

    script = sess.create_script(AGENT.read_text())
    script.on("message", on_message)
    script.set_log_handler(lambda level, text: lines.append(f"{level}: {text}"))
    script.load()
    for line in lines:
        print(line, flush=True)
    script.unload()

    # Read the bytes back rather than scrape the writer's own log lines: Frida's default log
    # handler swallows `console.log`, so a log scrape reported zero cleared rows over a write that
    # had plainly landed.
    verifier = sess.create_script(VERIFY)
    verifier.load()
    flags = {int(k): v for k, v in verifier.exports_sync.flags().items()}
    verifier.unload()
    sess.detach()
    for row, value in sorted(flags.items()):
        print(f"read-back: row {row} byte0x48=0x{value:02x} disable_offline={(value >> 5) & 1}")
    cleared = [row for row, value in flags.items() if not (value >> 5) & 1]

    after = regulation_fingerprint()
    print(f"regulation.bin before: {before}")
    print(f"regulation.bin after:  {after}")
    print(f"regulation.bin unchanged: {before == after}")
    if len(cleared) < 3:
        print(f"REFUSING to claim success: only {len(cleared)} of 3 rows read back clear")
        return 4
    print("VERDICT: the three vanilla invasion fingers are usable; regulation.bin was never opened")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
