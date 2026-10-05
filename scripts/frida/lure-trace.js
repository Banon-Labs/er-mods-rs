// Lure nearby human-build enemies to the player with the Piquebone Arrow's white smoke, then record
// how far the player's own hits move them. The player keeps their controls: nothing here blocks or
// injects input.
//
//   lure    bullet 20003309 (the smoke a Piquebone arrow leaves where it lands: 4.0 s, radius 15 m,
//           AtkParam_Pc 0 "Impact: Oppose", rain-of-arrows-seppuku.md) spawned at the player's feet
//           every `every` frames for `lureFrames` frames, owned by the player. The spawn copies the
//           game's own debug spawner 1.17.1 0x1403a6d70 [1.16.2 0x1403a6d60]: BulletSpawnData ctor
//           0x14038c580 [0x14038c570], owner handle +0x0, bullet id +0x14, flags +0x44 |= 1, position
//           +0x80, CSBulletManager::SpawnBullet 0x1403a2cb0 [0x1403a2ca0] (manager, int *out, data,
//           int *err; *out -1 = refused), dtor 0x14038d020 [0x14038d010]. GLOBAL_CSBulletManager is
//           0x143d667a8 [0x143d62748], read from the store after its ctor call in _Common_Initialize.
//   sustain the player and every character within `radius` m are kept at full HP; the others also get
//           the data module's no-death bit (+0x19b bit 0, tested by 0x1404379d0), cleared on dispose.
//   trace   each character within `radius` m: its position every frame (`vtrace`, capped), and every
//           player hit on it from CalculateDamage2 0x140448910 [0x1404483b0] with the victim's
//           position at that moment. A smoke hit has atk 0, which shows the smoke spawned where the
//           enemies are.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const cfg = Object.assign({ bullet: 20003309, every: 240, lureFrames: 36000, radius: 20, traceMax: 400000 }, globalThis.__ER_FRIDA_CONFIG || {});

const WORLD_CHR_MAN = va('0x143d69ff8');
const BULLET_MAN = va('0x143d667a8');
const PRE_BEHAVIOR_SAFE = va('0x140401f30');
const CALC_DAMAGE2 = va('0x140448910');
const SPAWN_DATA_CTOR = new NativeFunction(va('0x14038c580'), 'pointer', ['pointer']);
const SPAWN_DATA_DTOR = new NativeFunction(va('0x14038d020'), 'void', ['pointer']);
const SPAWN_BULLET = new NativeFunction(va('0x1403a2cb0'), 'pointer', ['pointer', 'pointer', 'pointer', 'pointer']);

const S = { frame: 0, spawns: 0, refused: 0, lastSpawn: -1e9, traced: 0, hits: 0, noDead: {}, err: null };
const DATA = Memory.alloc(0x200);
const OUT = Memory.alloc(8);
const ERR = Memory.alloc(8);

function mainPlayer () {
  try {
    const w = WORLD_CHR_MAN.readPointer();
    if (w.isNull()) return null;
    const p = w.add(0x1e508).readPointer();
    return p.isNull() ? null : p;
  } catch (e) { return null; }
}
function dataModule (c) { return c.add(0x190).readPointer().readPointer(); }
function posPtr (c) { return c.add(0x190).readPointer().add(0x68).readPointer().add(0x70); }
function pos (c) { const q = posPtr(c); return [q.readFloat(), q.add(4).readFloat(), q.add(8).readFloat()]; }
const r3 = function (a) { return a.map(function (x) { return Math.round(x * 1000) / 1000; }); };

function refill (c, keepAlive) {
  const dm = dataModule(c);
  const max = dm.add(0x13c).readS32();
  if (max > 0 && dm.add(0x138).readS32() < max) dm.add(0x138).writeS32(max);
  if (keepAlive) {
    const f = dm.add(0x19b).readU8();
    if ((f & 1) === 0) { dm.add(0x19b).writeU8(f | 1); S.noDead[dm.toString()] = dm; }
  } else {
    const smax = dm.add(0x158).readS32();
    if (smax > 0 && dm.add(0x154).readS32() < smax) dm.add(0x154).writeS32(smax);
  }
}

function spawnLure (p) {
  const mgr = BULLET_MAN.readPointer();
  if (mgr.isNull()) { S.err = 'no bullet manager'; return; }
  SPAWN_DATA_CTOR(DATA);
  DATA.writeU64(p.add(8).readU64());
  DATA.add(0x14).writeS32(cfg.bullet);
  DATA.add(0x44).writeU32(DATA.add(0x44).readU32() | 1);
  const a = pos(p);
  DATA.add(0x80).writeFloat(a[0]); DATA.add(0x84).writeFloat(a[1]); DATA.add(0x88).writeFloat(a[2]);
  OUT.writeS32(0); ERR.writeS32(0);
  SPAWN_BULLET(mgr, OUT, DATA, ERR);
  const out = OUT.readS32();
  SPAWN_DATA_DTOR(DATA);
  if (out === -1) S.refused += 1; else S.spawns += 1;
  send({ kind: 'lure', frame: S.frame, out: out, err: ERR.readS32(), at: r3(a) });
}

const hooks = [];
hooks.push(Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    const p = mainPlayer();
    if (p === null) return;
    const c = args[0];
    try {
      if (c.equals(p)) {
        S.frame += 1;
        refill(p, false);
        if (S.frame <= cfg.lureFrames && S.frame - S.lastSpawn >= cfg.every) { S.lastSpawn = S.frame; spawnLure(p); }
        if (S.frame % 600 === 0) send({ kind: 'hb', frame: S.frame, spawns: S.spawns, refused: S.refused, hits: S.hits, traced: S.traced, player: r3(pos(p)), err: S.err });
        return;
      }
      const a = pos(c); const b = pos(p);
      const d = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
      if (d > cfg.radius) return;
      refill(c, true);
      if (S.traced < cfg.traceMax) { S.traced += 1; send({ kind: 'vtrace', frame: S.frame, chr: c.toString(), pos: r3(a), player: r3(b) }); }
    } catch (e) { S.err = e.message; }
  },
}));

hooks.push(Interceptor.attach(CALC_DAMAGE2, {
  onEnter (args) {
    const p = mainPlayer();
    if (p === null || !args[1].equals(p)) return;
    try {
      const v = args[0].add(8).readPointer();
      this.rec = { victim: v.toString(), npc: v.add(0x60).readS32(), vpos: r3(pos(v)), player: r3(pos(p)), adi: args[2] };
    } catch (e) { this.rec = null; }
  },
  onLeave () {
    if (!this.rec) return;
    const r = this.rec;
    try { r.atk = r.adi.add(0x40).readS32(); r.damage = r.adi.add(0x228).readS32(); } catch (e) { r.atk = null; }
    delete r.adi;
    S.hits += 1;
    send(Object.assign({ kind: 'hit', frame: S.frame }, r));
  },
}));

send({ kind: 'armed', cfg: cfg, hooks: hooks.length });

rpc.exports = {
  dispose () {
    hooks.forEach(function (h) { h.detach(); });
    Object.keys(S.noDead).forEach(function (k) { try { const b = S.noDead[k].add(0x19b); b.writeU8(b.readU8() & 0xfe); } catch (e) { /* character gone */ } });
  },
};
