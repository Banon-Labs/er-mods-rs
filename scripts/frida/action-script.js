// er-frida-watch: no-reload (this agent drives the game; an edit must not restart a script mid-session)
//
// Runs a fixed list of steps on the player and logs every clip the game picks, for fallback rows
// the chainsaw driver cannot express (a buff, a hand change, then a press). The player's input is
// blocked from the first step to the last and handed back afterwards.
//
// Config: { steps: [...], sustain: true }. Steps run in order, one per game frame unless they wait:
//   { grant: item, gem? }      weapon into the inventory (mint 0x140672b30, add 0x140246480,
//                              release 0x1406832d0), skipped when already carried
//   { equip: item, hand: 'R'|'L' }  into the active slot of that hand, natively
//                              (EquipItemToChrAsmSlot 0x140788ab0 with a MenuGaitem, as the driver)
//   { press: ['R1','R2','L1','L2'], frames }  request bits held that many frames, then released
//   { move: m, frames, hold? }  walk forward at speed level m (0.5 walk, 1 run) for that many frames,
//                              holding the listed request bits meanwhile
//   { fillFp: true }          FP to its maximum
//   { speffects: true }       log the player's live SpEffect ids
//   { wait: frames }
// Request bits are CSChrActionRequestModule +0x10 on entry to UpdateFromManipulator 0x140408190
// (bit n = HKS ACTION_ARM n: R1 0, R2 1, L1 2, L2 3). `sustain` (default on) keeps the player at full
// HP with the data module's no-death bit, and Darkness (SpEffect 1653000, applied from the player
// with 0x1403fb010) on every other character within 12 m every 60 frames.
// Clips: every TimeAct the CustomManualSelectorGenerator writer 0x1419bb530 stores at node+0xec.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const cfg = Object.assign({ steps: [], sustain: true }, globalThis.__ER_FRIDA_CONFIG || {});

const WORLD_CHR_MAN = va('0x143d69ff8');
const GAME_DATA_MAN = va('0x143d61f98');
const GLOBAL_CSGAITEM = va('0x143d6d900');
const PRE_BEHAVIOR_SAFE = va('0x140401f30');
const UPDATE_FROM_MANIPULATOR = va('0x140408190');
const CMSG_SET_TAE = va('0x1419bb530');
const GET_EQUIP = new NativeFunction(va('0x1406577b0'), 'int', ['pointer', 'int']);
const GET_INV = new NativeFunction(va('0x140247b30'), 'pointer', ['pointer']);
const GET_IDX = new NativeFunction(va('0x14024c560'), 'int', ['pointer', 'pointer']);
const GET_PARAM_IN_SLOT = new NativeFunction(va('0x1402470e0'), 'int', ['pointer', 'int']);
const EQUIP = new NativeFunction(va('0x140788ab0'), 'void', ['int', 'pointer']);
const MINT = new NativeFunction(va('0x140672b30'), 'pointer', ['pointer', 'pointer', 'int', 'int']);
const ADD_BY_HANDLE = new NativeFunction(va('0x140246480'), 'int', ['pointer', 'pointer', 'uint32', 'uint8', 'uint8']);
const HANDLE_DTOR = new NativeFunction(va('0x1406832d0'), 'void', ['pointer']);
const APPLY_SPEFFECT_FROM = new NativeFunction(va('0x1403fb010'), 'uint8', ['pointer', 'uint32', 'pointer', 'pointer', 'pointer', 'uint8', 'uint8', 'uint8']);
const BIT = { R1: 1, R2: 2, L1: 4, L2: 8 };

const S = { frame: 0, step: 0, until: 0, bits: 0, blocking: false, done: false, last: null, lastFrame: -10, darkness: {}, noDead: {} };
const SP_POS = Memory.alloc(16);
const SP_CORR = Memory.alloc(0x60);

function mainPlayer () {
  try {
    const w = WORLD_CHR_MAN.readPointer();
    if (w.isNull()) return null;
    const p = w.add(0x1e508).readPointer();
    return p.isNull() ? null : p;
  } catch (e) { return null; }
}
function equipGameData () {
  const g = GAME_DATA_MAN.readPointer();
  return g.isNull() ? null : g.add(0x08).readPointer().add(0x2b0);
}
function invIndex (egd, item) {
  const id = Memory.alloc(4);
  id.writeS32(item);
  return GET_IDX(GET_INV(egd), id);
}
function dataModule (c) { return c.add(0x190).readPointer().readPointer(); }
function pos (c) { const q = c.add(0x190).readPointer().add(0x68).readPointer().add(0x70); return [q.readFloat(), q.add(4).readFloat(), q.add(8).readFloat()]; }
function keepAlive (c) {
  const dm = dataModule(c);
  const max = dm.add(0x13c).readS32();
  if (max > 0 && dm.add(0x138).readS32() < max) dm.add(0x138).writeS32(max);
  const f = dm.add(0x19b).readU8();
  if ((f & 1) === 0) { dm.add(0x19b).writeU8(f | 1); S.noDead[dm.toString()] = dm; }
}

function runStep (p, st) {
  const egd = equipGameData();
  const ev = { kind: 'step', i: S.step, frame: S.frame, step: st, ok: true };
  if (st.grant !== undefined) {
    if (invIndex(egd, st.grant) >= 0) { ev.why = 'already'; return send(ev); }
    const h = Memory.alloc(16);
    MINT(GLOBAL_CSGAITEM.readPointer(), h, st.grant, st.gem === undefined ? -1 : st.gem);
    ev.idx = ADD_BY_HANDLE(egd, h, 1, 1, 1);
    HANDLE_DTOR(h);
    ev.ok = invIndex(egd, st.grant) >= 0;
  } else if (st.equip !== undefined) {
    const asm = egd.add(0x6c);
    const slot = st.hand === 'L' ? asm.add(0x0c).readU32() * 2 : asm.add(0x10).readU32() * 2 + 1;
    const idx = invIndex(egd, st.equip);
    const mg = Memory.alloc(0x80);
    mg.add(0x48).writeS32(idx);
    mg.add(0x4c).writeS32(st.equip);
    if (idx >= 0) EQUIP(slot, mg);
    ev.slot = slot;
    ev.after = GET_PARAM_IN_SLOT(egd, slot);
    ev.ok = idx >= 0 && Math.floor(ev.after / 10000) === Math.floor(st.equip / 10000);
  } else if (st.press !== undefined) {
    S.bits = st.press.reduce(function (b, k) { return b | BIT[k]; }, 0);
    S.until = S.frame + (st.frames || 2);
    S.release = true;
  } else if (st.move !== undefined) {
    S.move = st.move;
    S.moveUntil = S.frame + (st.frames || 30);
    if (st.hold) S.bits = st.hold.reduce(function (b, k) { return b | BIT[k]; }, 0);
    ev.from = pos(p);
  } else if (st.fillFp) {
    const dm = dataModule(p);
    dm.add(0x148).writeS32(dm.add(0x14c).readS32());
    ev.fp = dm.add(0x148).readS32();
  } else if (st.speffects) {
    // The player's live SpEffect ids (ChrIns+0x178 -> +0x8 head; entry param id +0x8, next +0x30).
    ev.ids = [];
    for (let e = p.add(0x178).readPointer().add(0x8).readPointer(); !e.isNull() && ev.ids.length < 64; e = e.add(0x30).readPointer()) ev.ids.push(e.add(8).readS32());
  } else if (st.wait !== undefined) {
    S.until = S.frame + st.wait;
  }
  send(ev);
}

const hooks = [];
hooks.push(Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    const p = mainPlayer();
    if (p === null) return;
    const c = args[0];
    try {
      if (!c.equals(p)) {
        if (!cfg.sustain || !S.blocking) return;
        const a = pos(c); const b = pos(p);
        if (Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) > 12) return;
        keepAlive(c);
        const k = c.toString();
        if (S.frame - (S.darkness[k] || -1e9) >= 60) { S.darkness[k] = S.frame; APPLY_SPEFFECT_FROM(c, 1653000, p, SP_POS, SP_CORR, 0, 0, 0); }
        return;
      }
      S.frame += 1;
      if (cfg.sustain) keepAlive(p);
      if (S.done) return;
      if (!S.blocking) { S.blocking = true; send({ kind: 'start', frame: S.frame, steps: cfg.steps.length }); }
      if (S.frame < S.until) return;
      if (S.release) { S.release = false; S.bits = 0; }
      if (S.step >= cfg.steps.length) { S.done = true; S.blocking = false; S.bits = 0; send({ kind: 'done', frame: S.frame }); return; }
      runStep(p, cfg.steps[S.step]);
      S.step += 1;
    } catch (e) { send({ kind: 'error', frame: S.frame, step: S.step, error: e.message }); S.done = true; S.blocking = false; S.bits = 0; }
  },
}));
hooks.push(Interceptor.attach(UPDATE_FROM_MANIPULATOR, {
  onEnter (args) {
    if (!S.blocking) return;
    const p = mainPlayer();
    if (p === null || !args[0].add(8).readPointer().equals(p)) return;
    args[0].add(0x10).writeU64(uint64(S.bits));
    // Movement (docs/er-mechanics/movement-input-injection.md): the manipulator's character-relative
    // move vector at +0x10 and its copy +0x70, (0, 0, -m, 0) with forward = local -Z, plus the move
    // request bit 0 of module+0xfc, rewritten every frame of the hold; zeroed once afterwards.
    // Manipulator = ChrIns+0x58 (ChrCtrl) -> +0x3b0, or +0x18 when that is null.
    if (S.move !== undefined) {
      try {
        const ctrl = p.add(0x58).readPointer();
        let man = ctrl.add(0x3b0).readPointer();
        if (man.isNull()) man = ctrl.add(0x18).readPointer();
        const on = S.frame < S.moveUntil;
        const m = on ? S.move : 0;
        [0x10, 0x70].forEach(function (o) { man.add(o).writeFloat(0); man.add(o + 4).writeFloat(0); man.add(o + 8).writeFloat(-m); man.add(o + 12).writeFloat(0); });
        const f = args[0].add(0xfc).readU32();
        args[0].add(0xfc).writeU32(on ? (f | 1) : (f & ~1));
        if (!on) { S.move = undefined; send({ kind: 'moved', frame: S.frame, to: pos(p), speedLevel: ctrl.add(0x3a0).readFloat() }); }
      } catch (e) { send({ kind: 'error', frame: S.frame, error: 'move: ' + e.message }); S.move = undefined; }
    }
  },
}));
hooks.push(Interceptor.attach(CMSG_SET_TAE, {
  onEnter (args) { this.node = args[0]; },
  onLeave () {
    let tae;
    try { tae = this.node.add(0xec).readS32(); } catch (e) { return; }
    if (tae <= 0) return;
    const fold = tae === S.last && S.frame - S.lastFrame <= 1;
    S.last = tae; S.lastFrame = S.frame;
    if (fold) return;
    const p = mainPlayer();
    const cat = Math.floor(tae / 1000000);
    send({ kind: 'clip', frame: S.frame, clip: 'a' + String(cat).padStart(3, '0') + '_' + String(tae % 1000000).padStart(6, '0'), node: this.node.toString(), heldR: p === null ? null : GET_EQUIP(p, -1), heldL: p === null ? null : GET_EQUIP(p, -2) });
  },
}));

send({ kind: 'armed', steps: cfg.steps.length, sustain: cfg.sustain });

rpc.exports = {
  dispose () {
    S.blocking = false;
    hooks.forEach(function (h) { h.detach(); });
    Object.keys(S.noDead).forEach(function (k) { try { const b = S.noDead[k].add(0x19b); b.writeU8(b.readU8() & 0xfe); } catch (e) { /* character gone */ } });
  },
};
