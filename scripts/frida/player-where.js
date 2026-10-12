// er-frida-watch: no-reload
//
// Read-only: where the player is. Block ids from ChrIns (+0x30 block_id, +0x3c block_origin,
// fromsoftware-rs `chr_ins.rs`), the physics module position and orientation quaternion the action
// script's savePos/restorePos use (+0x70, +0x60), and the facing that quaternion gives (yaw
// t = 2 atan2(y, w) faces world (-sin t, -cos t), measured 2026-10-12). Sampled every 20 player
// frames from the player's own behavior update: sent while the player moves, and once when they
// have stood still for 80 frames.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const WORLD_CHR_MAN = va('0x143d69ff8');
const PRE_BEHAVIOR_SAFE = va('0x140401f30');

let frame = 0;
let last = null;
let still = 0;
const hook = Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    let p;
    try { p = WORLD_CHR_MAN.readPointer().add(0x1e508).readPointer(); } catch (e) { return; }
    if (p.isNull() || !args[0].equals(p) || ++frame % 20 !== 0) return;
    try {
      const ph = p.add(0x190).readPointer().add(0x68).readPointer();
      const q = ph.add(0x70);
      const pos = [q.readFloat(), q.add(4).readFloat(), q.add(8).readFloat()];
      const rot = [0, 1, 2, 3].map(function (i) { return ph.add(0x60 + i * 4).readFloat(); });
      const t = 2 * Math.atan2(rot[1], rot[3]);
      const moved = last === null || Math.hypot(pos[0] - last[0], pos[1] - last[1], pos[2] - last[2]) > 0.05;
      last = pos;
      still = moved ? 0 : still + 1;
      if (moved || still === 4) {
        send({ kind: moved ? 'where' : 'settled', block: '0x' + (p.add(0x30).readU32() >>> 0).toString(16), origin: '0x' + (p.add(0x3c).readU32() >>> 0).toString(16), pos: pos, rot: rot, facing: [-Math.sin(t), -Math.cos(t)] });
      }
    } catch (e) { send({ kind: 'error', error: e.message }); }
  },
});

rpc.exports = { dispose () { hook.detach(); } };
