// What the planned er-target-bars panel would show for whatever the local player is locked on to,
// read on the game thread once per few frames. Read-only: no write, no watchpoint, no
// MemoryAccessMonitor. The one call it makes into the game is the engine's own handle lookup.
//
// Every offset below was established statically against the installed 1.17.1
// (`eldenring-deobf-1.17.1.bin`) before this file was written; the point of running it is to see
// the values the static reading cannot give -- what an enemy's FP/stamina maxima really hold, that
// a status gauge falls from the resistance and snaps back on a proc, and what the stance maximum
// is on an ordinary enemy.
//
//   WorldChrManImp global      rva 0x3d69ff8   (0x140344e29: mov rdx,[rip+0x3a251c8])
//   ->mainPlayer               +0x1e508        (0x140344e30)
//   PlayerIns lockedOnEnemy    +0x6b0          (read 0x140344e3a; LockTgtMan writes -1 at
//                                              0x14071806a and the target's handle at 0x1407180d2)
//   LockTgtMan update          rva 0x7170b0    (prologue byte-identical to 1.16.2 0x140716260)
//   GetChrInsFromHandle        rva 0x508a50    (tail call of the point resolver 0x140714c5f)
//   ChrIns handle +0x8, npc_param_id +0x60, chr_type +0x68, specialEffect +0x178, modules +0x190
//   modules: data +0x0, resist +0x20, superArmor +0x40
//   CSChrDataModule hp/max +0x138/+0x13c (GetHpRate 0x1404377c0), fp +0x148/+0x14c,
//                   stamina +0x154/+0x158 (ctor stores 1 into all of them, 0x140436610)
//   CSChrSuperArmorModule cur +0x10, max +0x14 (rewritten every frame, 0x14047ecbf),
//                   speffect bonus +0x18, recover timer +0x1c
//   CSChrResistModule gauge[7] i32 +0x10, resistance[7] i32 +0x2c (0x14043de14, 0x14043e9c5)
//   SpecialEffect head +0x8; entry param row +0x0, next +0x30, timer +0x40, flags +0x60
//                   (entries with flags & 0x800c0003 are skipped by the engine, 0x1404fd490)
//   SpEffectParam stateInfo u16 +0x156 (0x14043e7e5)
//
// Run, against an approved session only:
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/target-bars-probe.js

'use strict';

const RVA_WORLD_CHR_MAN = 0x3d69ff8;
const RVA_LOCK_TGT_UPDATE = 0x7170b0;
const RVA_GET_CHR_INS_FROM_HANDLE = 0x508a50;

const MAIN_PLAYER = 0x1e508;
const LOCKED_ON_ENEMY = 0x6b0;
const CHR_HANDLE = 0x8;
const CHR_NPC_PARAM_ID = 0x60;
const CHR_TYPE = 0x68;
const CHR_SPECIAL_EFFECT = 0x178;
const CHR_MODULES = 0x190;
const MODULE_DATA = 0x0;
const MODULE_RESIST = 0x20;
const MODULE_SUPER_ARMOR = 0x40;

const STATUS_NAMES = ['poison', 'rot', 'bleed', 'blight', 'frost', 'sleep', 'madness'];
const STATE_INFO = [2, 5, 6, 0x74, 0x104, 0x1b4, 0x1b5];

// Every fifteenth update: four readings a second at 60 fps is plenty to watch a gauge fall.
const EVERY_CALLS = 15;
const WALK_LIMIT = 512;

const base = Process.getModuleByName('eldenring.exe').base;
const getChrInsFromHandle = new NativeFunction(
  base.add(RVA_GET_CHR_INS_FROM_HANDLE), 'pointer', ['pointer', 'pointer']);

let calls = 0;
let lastHandle = null;
let sent = 0;

function activeStatuses (chr) {
  const out = [];
  const container = chr.add(CHR_SPECIAL_EFFECT).readPointer();
  if (container.isNull()) return out;
  let entry = container.add(0x8).readPointer();
  for (let i = 0; i < WALK_LIMIT && !entry.isNull(); i++) {
    const flags = entry.add(0x60).readU32();
    const row = entry.readPointer();
    if ((flags & 0x800c0003) === 0 && !row.isNull()) {
      const stateInfo = row.add(0x156).readU16();
      const index = STATE_INFO.indexOf(stateInfo);
      if (index >= 0) {
        out.push({ status: STATUS_NAMES[index], id: entry.add(0x8).readS32(), timer: entry.add(0x40).readFloat() });
      }
    }
    entry = entry.add(0x30).readPointer();
  }
  return out;
}

function reading (world, handlePtr) {
  const chr = getChrInsFromHandle(world, handlePtr);
  if (chr.isNull()) return { resolved: false };
  const out = {
    resolved: true,
    chr: chr.toString(),
    handle_matches: chr.add(CHR_HANDLE).readU64().equals(handlePtr.readU64()),
    npc_param_id: chr.add(CHR_NPC_PARAM_ID).readS32(),
    chr_type: chr.add(CHR_TYPE).readS32(),
  };
  const modules = chr.add(CHR_MODULES).readPointer();
  if (modules.isNull()) return out;
  const data = modules.add(MODULE_DATA).readPointer();
  if (!data.isNull()) {
    out.hp = [data.add(0x138).readS32(), data.add(0x13c).readS32()];
    out.fp = [data.add(0x148).readS32(), data.add(0x14c).readS32()];
    out.stamina = [data.add(0x154).readS32(), data.add(0x158).readS32()];
  }
  const sa = modules.add(MODULE_SUPER_ARMOR).readPointer();
  if (!sa.isNull()) {
    out.stance = {
      cur: sa.add(0x10).readFloat(),
      max: sa.add(0x14).readFloat(),
      bonus: sa.add(0x18).readFloat(),
      recover_in: sa.add(0x1c).readFloat(),
    };
  }
  const resist = modules.add(MODULE_RESIST).readPointer();
  if (!resist.isNull()) {
    out.gauge = [];
    out.resistance = [];
    for (let i = 0; i < 7; i++) {
      out.gauge.push(resist.add(0x10 + i * 4).readS32());
      out.resistance.push(resist.add(0x2c + i * 4).readS32());
    }
  }
  out.active = activeStatuses(chr);
  return out;
}

Interceptor.attach(base.add(RVA_LOCK_TGT_UPDATE), {
  onLeave () {
    calls += 1;
    if (calls % EVERY_CALLS !== 0) return;
    try {
      const world = base.add(RVA_WORLD_CHR_MAN).readPointer();
      if (world.isNull()) return;
      const player = world.add(MAIN_PLAYER).readPointer();
      if (player.isNull()) return;
      const handlePtr = player.add(LOCKED_ON_ENEMY);
      const handle = handlePtr.readU64();
      const selector = handle.and(0xffffffff).toNumber();
      if (selector === 0xffffffff) {
        if (lastHandle !== null) send({ event: 'unlocked', handle: lastHandle });
        lastHandle = null;
        return;
      }
      const key = handle.toString(16);
      const changed = key !== lastHandle;
      lastHandle = key;
      const body = { event: changed ? 'target' : 'tick', handle: key, chr_selector_type: selector >>> 28 };
      Object.assign(body, reading(world, handlePtr));
      send(body);
      sent += 1;
    } catch (e) {
      send({ event: 'read-failed', error: String(e) });
    }
  },
});

rpc.exports = {
  // Nothing to undo: no watchpoint and no write. The Interceptor reverts on unload.
  dispose () {},
  stats () { return { calls, sent }; },
};

send({ event: 'ready', base: base.toString(), lock_tgt_update: base.add(RVA_LOCK_TGT_UPDATE).toString() });
