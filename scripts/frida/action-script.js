// er-frida-watch: no-reload (this agent drives the game; an edit must not restart a script mid-session)
//
// Runs a fixed list of steps on the player and logs every clip the game picks, for fallback rows
// the chainsaw driver cannot express (a buff, a hand change, then a press). The player's input is
// blocked from the first step to the last and handed back afterwards.
//
// Config: { steps: [...], sustain: true }. Steps run in order, one per game frame unless they wait:
//   { grant: item, gem? }      weapon into the inventory (mint, add, release; addresses below),
//                              skipped when already carried
//   { equip: item, hand: 'R'|'L' }  into the active slot of that hand, natively
//                              (EquipItemToChrAsmSlot with a MenuGaitem, as the driver)
//   { press: ['R1','R2','L1','L2','JUMP'], frames }  request bits held that many frames, then released
//   { move: m, frames, hold?, dir?, sprint?, untilClip? }  walk forward at speed level m (0.5 walk,
//                              1 run) for that many frames, holding the listed request bits meanwhile;
//                              `untilClip` ends the hold early at that clip
//   { fillFp: true }          FP to its maximum
//   { speffects: true }       log the player's live SpEffect ids
//   { wait: frames }
//   { untilClip: 'a000_0042', frames }  wait for a clip by name prefix (here any landing), at most
//                              `frames`; the landing clips are a000_004200/4210/4220, 4290 the low one
//   { savePos: true } / { restorePos: true }  remember the player's position, write it back
// Request bits are CSChrActionRequestModule +0x10 on entry to UpdateFromManipulator (bit n = HKS
// ACTION_ARM n: R1 0, R2 1, L1 2, L2 3). `sustain` (default on) keeps the player at full HP with
// the data module's no-death bit, and Darkness (SpEffect 1653000, applied from the player with
// APPLY_SPEFFECT_FROM) on every other character within 12 m every 60 frames.
// Clips: every TimeAct the CustomManualSelectorGenerator writer CMSG_SET_TAE stores at node+0xec.
//
// Addresses are 2.7.2.0, measured on 1.17.1 and carried with scripts/map-rvas-1171-to-1172.py; each
// is written `1.17.1 -> 2.7.2.0`. Every function's body matches eldenring-deobf-1.17.2.bin apart
// from rip-relative and branch operands, and each global was checked through its readers (the same
// instruction at the carried place reads the carried address).
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const cfg = Object.assign({ steps: [], sustain: true }, globalThis.__ER_FRIDA_CONFIG || {});

// WorldChrManImp grew 0x10 bytes past +0x1e5e8 in 2.7.2.0; main_player +0x1e508 is below that,
// and 456 of its 469 1.17.1 readers are unchanged at their carried place (the rest are refused by
// the carry or decode differently, none with another displacement).
const WORLD_CHR_MAN = va('0x143d6d018');           // 0x143d69ff8 -> 0x143d6d018
const GAME_DATA_MAN = va('0x143d64fb8');           // 0x143d61f98 -> 0x143d64fb8
const GLOBAL_CSGAITEM = va('0x143d70920');         // 0x143d6d900 -> 0x143d70920
const PRE_BEHAVIOR_SAFE = va('0x140401fc0');       // 0x140401f30 -> 0x140401fc0
const UPDATE_FROM_MANIPULATOR = va('0x140408220'); // 0x140408190 -> 0x140408220
const CMSG_SET_TAE = va('0x1419bcc20');            // 0x1419bb530 -> 0x1419bcc20
// 0x1406577b0 -> 0x140657c60
const GET_EQUIP = new NativeFunction(va('0x140657c60'), 'int', ['pointer', 'int']);
// 0x140247b30, unmoved
const GET_INV = new NativeFunction(va('0x140247b30'), 'pointer', ['pointer']);
// 0x14024c560, unmoved
const GET_IDX = new NativeFunction(va('0x14024c560'), 'int', ['pointer', 'pointer']);
// 0x1402470e0, unmoved
const GET_PARAM_IN_SLOT = new NativeFunction(va('0x1402470e0'), 'int', ['pointer', 'int']);
// EquipItemToChrAsmSlot 0x140788ab0 -> 0x140788f60
const EQUIP = new NativeFunction(va('0x140788f60'), 'void', ['int', 'pointer']);
// mint 0x140672b30 -> 0x140672fe0
const MINT = new NativeFunction(va('0x140672fe0'), 'pointer', ['pointer', 'pointer', 'int', 'int']);
// add 0x140246480, unmoved
const ADD_BY_HANDLE = new NativeFunction(va('0x140246480'), 'int', ['pointer', 'pointer', 'uint32', 'uint8', 'uint8']);
// release 0x1406832d0 -> 0x140683780
const HANDLE_DTOR = new NativeFunction(va('0x140683780'), 'void', ['pointer']);
// CSChrActionRequestModule::IsMoveCancelPossible (1.16.2 0x140407720, byte-matched at 1.17.1
// 0x140407c50, carried to 2.7.2.0 0x140407ce0 with the same body): the input queue's answer when
// queue mode is on, else `taeCancels` (+0x100) bits 1 and 2 set with bit 11 clear. `HksEnv` serves
// env 2000 (IsMoveCancelPossible) with it, argument 1.
const IS_MOVE_CANCEL_POSSIBLE = new NativeFunction(va('0x140407ce0'), 'uint8', ['pointer', 'int']);
// 0x1403fb010 -> 0x1403fb0a0
const APPLY_SPEFFECT_FROM =new NativeFunction(va('0x1403fb0a0'), 'uint8', ['pointer', 'uint32', 'pointer', 'pointer', 'pointer', 'uint8', 'uint8', 'uint8']);
// JUMP is HKS ACTION_ARM_CHANGE_STYLE (6): ExecJump starts a jump on env(ActionRequest, 6), the
// player's jump button, or env 700 (IsAIJumpRequested) for an AI. ACTION is ACTION_ARM_ACTION (4):
// held with R1 it is the two-hand input `ExecWeaponChange` reads (`env(ActionDuration, 4) > 0`);
// that the duration is counted from this request bit is inferred, not traced. ROLL and BACKSTEP are
// ACTION_ARM_ROLLING (17) and ACTION_ARM_BACKSTEP (16), the two `GetEvasionRequest` turns into a
// roll and a backstep; that the request word carries them at bit n like the others is inferred.
const BIT = { R1: 1, R2: 2, L1: 4, L2: 8, ACTION: 0x10, JUMP: 0x40, BACKSTEP: 0x10000, ROLL: 0x20000 };

const S = { frame: 0, step: 0, until: 0, bits: 0, blocking: false, done: false, last: null, lastFrame: -10, darkness: {}, noDead: {}, killed: {} };
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
// The player's newest TimeAct queue entry (`combat-recorder.js`: module container +0x18, queue +0x20
// of ten {anim_id, play_time, play_time2, anim_length}, write index +0xc0) and the ChrAsm arm style
// (PlayerGameData +0x580 -> EquipGameData +0x2b0, ChrAsm +0x6c, style +0x8: 3 right two-handed).
function animName (id) { return 'a' + String(Math.floor(id / 1000000)).padStart(3, '0') + '_' + String(id % 1000000).padStart(6, '0'); }
function playerAnim (c) {
  const ta = c.add(0x190).readPointer().add(0x18).readPointer();
  const e = ta.add(0x20 + ((ta.add(0xc0).readU32() + 9) % 10) * 16);
  return { anim: e.readS32(), at: e.add(4).readFloat(), len: e.add(12).readFloat() };
}
function armStyle () { const egd = equipGameData(); return egd === null ? null : egd.add(0x6c + 0x8).readU32(); }
function watchPlayerAnim (p) {
  const a = playerAnim(p);
  const fresh = a.anim !== S.pAnim || a.at < S.pAt - 0.1;
  S.pAnim = a.anim; S.pAt = a.at;
  const style = armStyle();
  if (fresh || style !== S.pStyle) {
    const name = animName(a.anim);
    send({ kind: 'pa', frame: S.frame, anim: name, at: Math.round(a.at * 1000) / 1000, len: Math.round(a.len * 1000) / 1000, style: style });
    // A prefix starting with '_' matches the animation number in any TimeAct category: a bow in hand
    // plays the fall and landing as a003_004xxx instead of a000_004xxx (measured 2026-10-12).
    const hit = S.waitClip !== undefined && (S.waitClip[0] === '_' ? name.slice(4).startsWith(S.waitClip) : name.startsWith(S.waitClip));
    if (fresh && hit) { S.waitClip = undefined; S.until = S.frame; if (S.moveStopOnClip) { S.moveStopOnClip = false; S.moveUntil = S.frame; } }
  }
  S.pStyle = style;
}
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
    // Equipping what the slot already holds takes it off (measured 2026-10-12), so that is a no-op here.
    if (Math.floor(GET_PARAM_IN_SLOT(egd, slot) / 10000) === Math.floor(st.equip / 10000)) { ev.why = 'already'; ev.after = GET_PARAM_IN_SLOT(egd, slot); return send(ev); }
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
    S.moveDir = st.dir;
    S.sprint = Boolean(st.sprint);
    S.moveUntil = S.frame + (st.frames || 30);
    if (st.hold) S.bits = st.hold.reduce(function (b, k) { return b | BIT[k]; }, 0);
    // With `untilClip` the hold ends at that clip (or after `frames`), and the step list waits for it.
    if (st.untilClip !== undefined) { S.waitClip = st.untilClip; S.moveStopOnClip = true; S.until = S.moveUntil; }
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
  } else if (st.untilClip !== undefined) {
    // Holds the step list until a clip whose name starts with `untilClip` is set, at most `frames`.
    S.waitClip = st.untilClip;
    S.until = S.frame + (st.frames || 300);
  } else if (st.savePos) {
    // `cfg.home` ({pos, rot}, from an earlier run's savePos event) pins the start, so a run that
    // begins somewhere else still starts every case from the same spot and facing.
    const ph = p.add(0x190).readPointer().add(0x68).readPointer();
    S.saved = cfg.home ? cfg.home.pos : pos(p);
    // Facing too: the physics module's orientation quaternion +0x60 (`combat-recorder.js`).
    const rot = ph.add(0x60);
    if (cfg.home && cfg.home.rot) { S.savedRot = Memory.alloc(16); cfg.home.rot.forEach(function (v, i) { S.savedRot.add(i * 4).writeFloat(v); }); S.savedRot = S.savedRot.readByteArray(16); }
    else S.savedRot = rot.readByteArray(16);
    ev.pos = S.saved;
    ev.rot = [0, 1, 2, 3].map(function (i) { return rot.add(i * 4).readFloat(); });
  } else if (st.restorePos) {
    // The physics module's position (the field `pos` reads) and orientation, written back; a short
    // hop only, since nothing reloads the map. Measured 2026-10-12: the position holds.
    const ph = p.add(0x190).readPointer().add(0x68).readPointer();
    const q = ph.add(0x70);
    if (S.saved) { q.writeFloat(S.saved[0]); q.add(4).writeFloat(S.saved[1]); q.add(8).writeFloat(S.saved[2]); }
    if (S.savedRot) ph.add(0x60).writeByteArray(S.savedRot);
    ev.ok = Boolean(S.saved);
    ev.pos = pos(p);
    // `hold`: write the position again every frame for that many frames. A single write from the
    // bottom of the drop fell 13 m straight through the floor at the start (measured 2026-10-12, run
    // p), as if the start's collision was not loaded yet when the player arrived.
    if (st.hold) { S.pinUntil = S.frame + st.hold; S.until = S.pinUntil; }
  } else if (st.checkPos !== undefined) {
    // Ends the run when the player is more than `checkPos` metres from the saved spot, so no case
    // starts anywhere but there.
    const a = pos(p);
    ev.pos = a;
    ev.dist = Math.hypot(a[0] - S.saved[0], a[1] - S.saved[1], a[2] - S.saved[2]);
    if (ev.dist > st.checkPos) { ev.ok = false; S.done = true; S.blocking = false; S.bits = 0; S.move = undefined; send(ev); send({ kind: 'error', frame: S.frame, error: 'not at the saved spot' }); return; }
  } else if (st.face !== undefined) {
    // Turn to face world direction [dx, dz]: a yaw-only quaternion (x, y, z, w) = (0, sin h, 0, cos h)
    // with forward = local -Z, so yaw = atan2(-dx, -dz). Layout and sign are inferred; the caller
    // checks the result by walking a few frames.
    const yaw = Math.atan2(-st.face[0], -st.face[1]);
    const r = p.add(0x190).readPointer().add(0x68).readPointer().add(0x60);
    ev.before = [0, 1, 2, 3].map(function (i) { return r.add(i * 4).readFloat(); });
    r.writeFloat(0); r.add(4).writeFloat(Math.sin(yaw / 2)); r.add(8).writeFloat(0); r.add(12).writeFloat(Math.cos(yaw / 2));
    ev.after = [0, 1, 2, 3].map(function (i) { return r.add(i * 4).readFloat(); });
  } else if (st.oneHand) {
    // Back to one-handed (ChrAsm arm style 1) with a two-hand tap when the last case left it at 2/3.
    ev.style = armStyle();
    if (ev.style >= 2) { S.bits = BIT.ACTION | BIT.R1; S.until = S.frame + 4; S.release = true; }
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
        const a = pos(c); const b = pos(p);
        const dist = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
        // `killNearby` (user, 2026-10-12: "every time we reload this world to kill all NPCs in the
        // area"): HP to 0 on every other character within that many metres, once each.
        if (cfg.killNearby && dist <= cfg.killNearby) {
          const k = c.toString();
          if (!S.killed[k]) {
            const dm = dataModule(c);
            if (dm.add(0x138).readS32() > 0) { dm.add(0x138).writeS32(0); S.killed[k] = true; send({ kind: 'killed', frame: S.frame, d: Math.round(dist * 10) / 10 }); }
          }
          return;
        }
        if (!cfg.sustain || !S.blocking) return;
        if (dist > 12) return;
        keepAlive(c);
        const k = c.toString();
        if (S.frame - (S.darkness[k] || -1e9) >= 60) { S.darkness[k] = S.frame; APPLY_SPEFFECT_FROM(c, 1653000, p, SP_POS, SP_CORR, 0, 0, 0); }
        return;
      }
      S.frame += 1;
      if (cfg.sustain) keepAlive(p);
      if (S.pinUntil !== undefined && S.frame < S.pinUntil && S.saved) {
        const q = p.add(0x190).readPointer().add(0x68).readPointer().add(0x70);
        q.writeFloat(S.saved[0]); q.add(4).writeFloat(S.saved[1]); q.add(8).writeFloat(S.saved[2]);
      }
      watchPlayerAnim(p);
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
    // The game's own answer to "may a move input start moving now" (HKS env 2000, the check
    // `MoveStartonCancelTiming` makes before `MoveStart`), logged on every change so the earliest
    // movement frame is read without any movement input.
    const mc = IS_MOVE_CANCEL_POSSIBLE(args[0], 1) !== 0;
    if (mc !== S.moveCancel) { S.moveCancel = mc; send({ kind: 'mc', frame: S.frame, possible: mc }); }
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
        // `dir` [dx, dz] walks along a world direction: the character-relative vector is that
        // direction turned by minus the current yaw (physics quaternion y, w; measured 2026-10-12:
        // yaw t faces world (-sin t, -cos t), the direction a plain forward walk took).
        let lx = 0; let lz = -m;
        if (on && S.moveDir) {
          const r = p.add(0x190).readPointer().add(0x68).readPointer().add(0x60);
          const t = 2 * Math.atan2(r.add(4).readFloat(), r.add(12).readFloat());
          const n = Math.hypot(S.moveDir[0], S.moveDir[1]); const wx = S.moveDir[0] / n; const wz = S.moveDir[1] / n;
          lx = m * (wx * Math.cos(t) - wz * Math.sin(t));
          lz = m * (wx * Math.sin(t) + wz * Math.cos(t));
        }
        [0x10, 0x70].forEach(function (o) { man.add(o).writeFloat(lx); man.add(o + 4).writeFloat(0); man.add(o + 8).writeFloat(lz); man.add(o + 12).writeFloat(0); });
        // Bit 2 is dash: with speed level 2.0 the player sprints (movement-input-injection.md).
        const f = args[0].add(0xfc).readU32();
        const bits = 1 | (S.sprint ? 4 : 0);
        args[0].add(0xfc).writeU32(on ? (f | bits) : (f & ~5));
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
    const clip = 'a' + String(cat).padStart(3, '0') + '_' + String(tae % 1000000).padStart(6, '0');
    // Every character's clips pass through here; `pa` events are the player's alone.
    if (!cfg.allClips) return;
    send({ kind: 'clip', frame: S.frame, clip: clip, node: this.node.toString(), heldR: p === null ? null : GET_EQUIP(p, -1), heldL: p === null ? null : GET_EQUIP(p, -2) });
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
