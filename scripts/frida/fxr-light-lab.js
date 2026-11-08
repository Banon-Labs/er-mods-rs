// Hand-crafted light stimulus for the bullet-flood GPU artifacting (bd
// bullet-flood-artifacting-needs-point-lights-2720-2026-10-27): load an FXR written by
// scripts/fxr-craft-lights.mjs into the running game, repoint an already-loaded definition at it,
// and spawn it PER_FRAME times a frame at one fixed point in front of the player, so the player can
// stand still. Reports the visible-light count the tiled-light upload receives.
//
// Config (er-frida-watch --config-json): {"fxrHex": "<bytes>", "id": 442022}.
//
// 2.7.2.0 addresses, carried from 1.16.2 by bytes on eldenring-deobf-1.17.2.bin (2026-10-27):
// - fxr_load_from_bytes(bytes, len) -> FxrWrapper*: 1.16.2 0x1420fbd90 -> 0x1420ff330 (1 hit).
// - spawn-at-transform(u32 *fxrId, mat4 *world, 16-byte aligned): 1.16.2 0x140d929f0 -> 0x140d95e90
//   (third of three byte-identical twins in both builds, same call displacement 0x27d9).
// - CSSfx singleton: rip load inside that function -> 0x143d8aa48.
// - Definition list (fromsoftware-rs gxffx.rs): CSSfx+0x60 scene ctrl, +0x28 graphics resource
//   manager, +0x160 FxrResourceContainer, MSVC std::list head at +0x20; node +0x10 id, +0x18
//   FxrWrapper*, wrapper +0x0 the FXR. Repointing the wrapper changes new spawns only.
// - Visible lights: 0x141b2cc50 packet +0x10 (see bullet-flood.js).
'use strict';

// Fractional rates carry the remainder. Steady instances ~= PER_FRAME x fps x the FXR's duration.
// 8 lights x 1/frame x 2 s: ~880 lights submitted, drawn for under a second then gone (user, 2026-10-27).
// 1 light x 0.5/frame x 2 s at 60 fps: ~60 ours, max 69 submitted, visible, bright, clean (user).
// 1/frame at 46 fps: ~92 ours, max 100 submitted, visible, bright, ARTIFACTING (user).
// 0.75/frame: ~73 ours, clean (user). Next: ~1000 stacked (user request).
// 20/frame: packet 1,014-1,114 mean / 2,262 max (upload clamps 1024), fps ~50, lights NOT drawn,
// no device removal (user). Finding where lights vanish:
// 2/frame: packet max 248-250, half drawn half corrupted (user).
const PER_FRAME = 4;
const AHEAD_M = 6;
const REPORT_FRAMES = 60;

const cfg = globalThis.__ER_FRIDA_CONFIG || {};
const FXR_ID = cfg.id === undefined ? 442022 : cfg.id;
const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const WORLD_CHR_MAN = va('0x143d6d018');
const PRE_BEHAVIOR_SAFE = va('0x140401fc0');
const CS_SFX = va('0x143d8aa48');
const loadFxr = new NativeFunction(va('0x1420ff330'), 'pointer', ['pointer', 'int']);
const spawnFxr = new NativeFunction(va('0x140d95e90'), 'pointer', ['pointer', 'pointer']);

function findDefinition (id) {
  const head = CS_SFX.readPointer().add(0x60).readPointer().add(0x28).readPointer().add(0x160).readPointer().add(0x20).readPointer();
  let n = 0;
  for (let node = head.readPointer(); !node.equals(head) && n < 100000; node = node.readPointer(), n++) {
    if (node.add(0x10).readU32() === id) return { node: node, scanned: n };
  }
  return { node: null, scanned: n };
}

let loaded = null;
if (cfg.fxrHex) {
  const bytes = new Uint8Array(cfg.fxrHex.match(/../g).map(function (h) { return parseInt(h, 16); }));
  const buf = Memory.alloc(bytes.length);
  buf.writeByteArray(bytes);
  const def = findDefinition(FXR_ID);
  if (def.node === null) {
    send({ kind: 'error', error: 'definition ' + FXR_ID + ' not loaded', scanned: def.scanned });
  } else {
    const wrapper = loadFxr(buf, bytes.length);
    if (wrapper.isNull()) {
      send({ kind: 'error', error: 'game FXR loader refused the bytes', len: bytes.length });
    } else {
      const old = def.node.add(0x18).readPointer().readPointer();
      def.node.add(0x18).readPointer().writePointer(wrapper.readPointer());
      loaded = { old: old, fxr: wrapper.readPointer() };
      send({ kind: 'repointed', id: FXR_ID, len: bytes.length, scanned: def.scanned, old: old.toString(), fxr: loaded.fxr.toString() });
    }
  }
} else {
  send({ kind: 'error', error: 'no fxrHex in config' });
}

const idBuf = Memory.alloc(4);
idBuf.writeU32(FXR_ID);
// Memory.alloc is 16-byte aligned on this target only by accident; force it.
const matBacking = Memory.alloc(64 + 16);
const mat = matBacking.add((16 - (matBacking.toUInt32() & 15)) & 15);
let anchor = null;

const lightCount = { max: 0, sum: 0, n: 0 };
const hooks = [
  Interceptor.attach(va('0x141b2cc50'), {
    onEnter (args) {
      const c = args[1].add(0x10).readU32();
      if (c > lightCount.max) lightCount.max = c;
      lightCount.sum += c; lightCount.n++;
    },
  }),
];

let frame = 0;
let credit = 0;
let spawned = 0;
let windowStart = Date.now();
if (loaded !== null) {
  hooks.push(Interceptor.attach(PRE_BEHAVIOR_SAFE, {
    onEnter (args) {
      let p;
      try { p = WORLD_CHR_MAN.readPointer().add(0x1e508).readPointer(); } catch (e) { return; }
      if (p.isNull() || !args[0].equals(p)) return;
      if (anchor === null) {
        const ph = p.add(0x190).readPointer().add(0x68).readPointer();
        const q = ph.add(0x70);
        const t = 2 * Math.atan2(ph.add(0x64).readFloat(), ph.add(0x6c).readFloat());
        anchor = [q.readFloat() - Math.sin(t) * AHEAD_M, q.add(4).readFloat(), q.add(8).readFloat() - Math.cos(t) * AHEAD_M];
        const m = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, anchor[0], anchor[1], anchor[2], 1];
        m.forEach(function (v, i) { mat.add(i * 4).writeFloat(v); });
        send({ kind: 'anchor', pos: anchor });
      }
      credit += PER_FRAME;
      const due = Math.floor(credit);
      credit -= due;
      for (let i = 0; i < due; i++) { spawnFxr(idBuf, mat); spawned++; }
      if (++frame % REPORT_FRAMES !== 0) return;
      const now = Date.now();
      send({ kind: 'lab', frame: frame, perFrame: PER_FRAME, spawned: spawned, fps: Math.round(REPORT_FRAMES / ((now - windowStart) / 1000)), visibleLights: { max: lightCount.max, mean: lightCount.n ? Math.round(lightCount.sum / lightCount.n) : null } });
      windowStart = now; spawned = 0;
      lightCount.max = 0; lightCount.sum = 0; lightCount.n = 0;
    },
  }));
}

send({ kind: 'armed', id: FXR_ID, perFrame: PER_FRAME, aheadM: AHEAD_M });

rpc.exports = {
  dispose () { hooks.forEach(function (h) { h.detach(); }); },
};
