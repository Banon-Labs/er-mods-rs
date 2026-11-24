// Offline bullet flood: can a player spawning many cheap bullets at once take down the GPU?
//
// Calls the game's own ChrIns::SpawnBullet from the player's update, PER_FRAME times a frame, and
// counts what CSBulletManager refuses. Static RE (1.16.2 :8765, carried by bytes to 2.7.2.0):
// - ChrIns::SpawnBullet(ChrIns **owner, u32 bulletId, float pos[3]) -> bool, 1.16.2 0x1403c32d0,
//   2.7.2.0 0x1403c32e0. It sets spawn flags |= 7, so the per-chr emitter slot is not held and
//   every call reaches CSBulletManager::SpawnBullet.
// - CreateBullet (one CSBulletIns per shot), 2.7.2.0 0x1403a7c20: 128 preallocated, then at most
//   0x80 more from the heap (count at mgr+0x18), then null.
// - Launch allocator (one entry per launch), 2.7.2.0 0x1403a7cc0: 64 preallocated, then at most
//   0xc0 more from the heap (count at mgr+0x38), then null and status 4.
// So the expected answer is that live bullets saturate at 256 and the flood only churns them; the
// run measures whether that churn alone hurts the renderer.
//
// BULLET_ID 101103: sfxId_Bullet 830600, life 2.1 s, 15 m/s, atkId 0, no HitBulletID, no interval
// child, numShoot 1 -- a visible bullet that does nothing else. Edit and save to reload in place.
'use strict';

// 101103 held 256 live bullets with nothing visible on screen (user, 2026-10-27). 10400000 is
// Glintstone Pebble: sfxId_Bullet 523002, sfxId_Hit 523003, life 1.12 s, 13 m/s, no children.
// Split by element type at the artifacting rate (5/frame), each with no HitBulletID, no interval
// child, numShoot 1 (scripts/fxr-actions.py over the sfxId_Bullet FXR):
//   200003041 fx 442022: 1 PointLight, 3-4 billboards, no distortion, no tracer
//   2060      fx 440082: 1 Distortion, 1 billboard, no light, no tracer
//   3470192   fx 634726: 1 DynamicTracer only of the three
// Result 2026-10-27: 200003041 artifacts once its effects fill the screen (walking into them).
// 2060 (no light): no artifacting even walking into it (user, 2026-10-27).
const BULLET_ID = 200003041;
// Fractional rates carry the remainder: 4.5 is 9 spawns every 2 frames.
// 200003041 at 5/frame: 175 live, visible lights mean ~100 / max ~200 per upload, tiles only while
// the player walks into the effects, gone when standing still at the same count (user, 2026-10-27).
// 2/frame: ~70 live, visible max 80-86, no tiles walking in (user).
// 3.5/frame: no tiles walking in (user).
// 4.25/frame: no tiles walking in (user). Paused for a hand-crafted effect.
const PER_FRAME = 0;
// Lights-per-tile override for the lighting object (+0x378, live 96). Lower only: the per-tile
// index buffer was sized from the value at creation (96, read live 2026-10-27). null leaves it
// alone. An in-place reload by er-frida-watch does NOT call dispose (measured: 24 survived a reload),
// so putting it back takes an explicit 96 here.
const LIGHTS_PER_TILE_AT_CREATION = 96;
const LIGHTS_PER_TILE = 96;
const REPORT_FRAMES = 60;

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const WORLD_CHR_MAN = va('0x143d6d018');
const PRE_BEHAVIOR_SAFE = va('0x140401fc0');
const spawnBullet = new NativeFunction(va('0x1403c32e0'), 'uint8', ['pointer', 'uint32', 'pointer']);

const owner = Memory.alloc(8);
const pos = Memory.alloc(16);
let manager = null;
let frame = 0;
let credit = 0;
let windowStart = Date.now();
const tally = { calls: 0, spawned: 0, refused: 0, bulletNull: 0, launchNull: 0 };
let total = { calls: 0, spawned: 0, refused: 0, bulletNull: 0, launchNull: 0 };

// The live list: mgr+0x8 head, CSBulletIns+0xe18 next (CreateBullet links both, 2.7.2.0 bytes).
function liveBullets () {
  let n = 0;
  for (let b = manager.add(0x8).readPointer(); !b.isNull() && n < 4096; b = b.add(0xe18).readPointer()) n++;
  return n;
}

// Tiled light culling (1.16.2 FUN_141b26a80 / 2.7.2.0 0x141b29fe0, dispatch over 32 px tiles):
// ctor FUN_141b24110 sets +0x36c tile px 0x20, +0x370 organize 8, +0x378 lights per tile 0x40,
// +0x364/+0x368 0x80. Captured once off the dispatch's first argument, then read each report.
let lighting = null;
const lightingHook = Interceptor.attach(va('0x141b29fe0'), {
  onEnter (args) {
    if (lighting !== null) return;
    lighting = args[0];
    lightsPerTileWas = lighting.add(0x378).readU32();
    if (LIGHTS_PER_TILE !== null && LIGHTS_PER_TILE <= LIGHTS_PER_TILE_AT_CREATION) lighting.add(0x378).writeU32(LIGHTS_PER_TILE);
  },
});
let lightsPerTileWas = null;
function lightingFields () {
  if (lighting === null) return null;
  return [0x364, 0x368, 0x36c, 0x370, 0x378].map(function (o) { return '+0x' + o.toString(16) + '=' + lighting.add(o).readU32(); }).join(' ');
}

// Visible lights handed to the tiled upload each frame: 1.16.2 FUN_141b296f0 / 2.7.2.0 0x141b2cc50,
// packet in rdx (+0x8 light pointers, +0x10 u32 count), clamped to 0x400 inside. Max and mean per
// report window.
const lightCount = { max: 0, sum: 0, n: 0 };
const lightUploadHook = Interceptor.attach(va('0x141b2cc50'), {
  onEnter (args) {
    const c = args[1].add(0x10).readU32();
    if (c > lightCount.max) lightCount.max = c;
    lightCount.sum += c;
    lightCount.n++;
  },
});

const hooks = [
  lightingHook,
  lightUploadHook,
  Interceptor.attach(va('0x1403a7c20'), {
    onEnter (args) { manager = args[0]; },
    onLeave (ret) { if (ret.isNull()) tally.bulletNull++; },
  }),
  Interceptor.attach(va('0x1403a7cc0'), {
    onLeave (ret) { if (ret.isNull()) tally.launchNull++; },
  }),
  Interceptor.attach(PRE_BEHAVIOR_SAFE, {
    onEnter (args) {
      let p;
      try { p = WORLD_CHR_MAN.readPointer().add(0x1e508).readPointer(); } catch (e) { return; }
      if (p.isNull() || !args[0].equals(p)) return;
      const q = p.add(0x190).readPointer().add(0x68).readPointer().add(0x70);
      owner.writePointer(p);
      pos.writeFloat(q.readFloat());
      pos.add(4).writeFloat(q.add(4).readFloat() + 1.5);
      pos.add(8).writeFloat(q.add(8).readFloat());
      credit += PER_FRAME;
      const due = Math.floor(credit);
      credit -= due;
      for (let i = 0; i < due; i++) {
        tally.calls++;
        if (spawnBullet(owner, BULLET_ID, pos)) tally.spawned++;
        else { tally.refused++; break; }
      }
      if (++frame % REPORT_FRAMES !== 0) return;
      for (const k in tally) { total[k] += tally[k]; }
      const now = Date.now();
      const seconds = (now - windowStart) / 1000;
      windowStart = now;
      send({
        spawnedPerSecond: Math.round(tally.spawned / seconds), windowSeconds: seconds,
        kind: 'flood', frame: frame, bullet: BULLET_ID, last: Object.assign({}, tally), total: Object.assign({}, total),
        heapBullets: manager === null ? null : manager.add(0x18).readU32(),
        heapLaunches: manager === null ? null : manager.add(0x38).readU32(),
        live: manager === null ? null : liveBullets(),
        lighting: lighting === null ? null : lighting.toString() + ' ' + lightingFields(),
        visibleLights: { max: lightCount.max, mean: lightCount.n ? Math.round(lightCount.sum / lightCount.n) : null, uploads: lightCount.n },
      });
      lightCount.max = 0; lightCount.sum = 0; lightCount.n = 0;
      for (const k in tally) { tally[k] = 0; }
    },
  }),
];

send({ kind: 'armed', bullet: BULLET_ID, perFrame: PER_FRAME });

rpc.exports = {
  dispose () {
    hooks.forEach(function (h) { h.detach(); });
    if (lighting !== null && lightsPerTileWas !== null) lighting.add(0x378).writeU32(lightsPerTileWas);
  },
};
