// er-frida-watch: no-reload
//
// Read-only: stream the player's position for an arena-extent survey. Same reads as
// player-where.js (ChrIns +0x30 block id, physics module +0x70 position), at their 2.7.2.0
// addresses: WorldChrMan 0x143d69ff8 -> 0x143d6d018, the
// pre-behavior hook 0x140401f30 -> 0x140401fc0 (same prologue bytes in both images). Sends a
// point every 4 player frames while the player has moved more than 0.1 m since the last one;
// scripts/arena-extents.py turns the log into per-block hull diameters and bounds.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const WORLD_CHR_MAN = va('0x143d6d018');
const PRE_BEHAVIOR_SAFE = va('0x140401fc0');

let frame = 0;
let last = null;
const hook = Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    let p;
    try { p = WORLD_CHR_MAN.readPointer().add(0x1e508).readPointer(); } catch (e) { return; }
    if (p.isNull() || !args[0].equals(p) || ++frame % 4 !== 0) return;
    try {
      const q = p.add(0x190).readPointer().add(0x68).readPointer().add(0x70);
      const pos = [q.readFloat(), q.add(4).readFloat(), q.add(8).readFloat()];
      if (last !== null && Math.hypot(pos[0] - last[0], pos[1] - last[1], pos[2] - last[2]) < 0.1) return;
      last = pos;
      // PlayerIns+0x6c0 block_position: the same point in the block's own frame, which the
      // physics position only matches up to a floating origin (bd er-local-warp-no-reload-1171).
      const b = p.add(0x6c0);
      send({ kind: 'pt', block: '0x' + (p.add(0x30).readU32() >>> 0).toString(16), pos: pos, bpos: [b.readFloat(), b.add(4).readFloat(), b.add(8).readFloat()] });
    } catch (e) { send({ kind: 'error', error: e.message }); }
  },
});

rpc.exports = { dispose () { hook.detach(); } };
