// Warp the player to an arbitrary block-local coordinate, on demand, from outside the game.
//
// This is the sequence `er_invasion_warp.dll` performs in
// `crates/er-invasion-warp-core/src/warp.rs` -- the engine's own `TriggerAreaReload` steps with
// our destination substituted -- replayed here so a target can be chosen while the game is
// already running, with no rebuild and no relaunch.
//
// Every address is 1.17.1, carried from the 1.16.2 constants in `warp.rs` through
// `docs/recon/rva-map-1162-to-1170.verified.tsv` and `scripts/map-rvas-1170-to-1171.py`, then
// byte-checked against `eldenring-deobf-1.17.1.bin`. The prologues confirm the semantics rather
// than merely the location: `SET_EXPLICIT_SPAWN` opens `movaps xmm0,[rcx]` then
// `movups [rax+0xc90],xmm0`, and `GET_EXPLICIT_SPAWN_FLAG` is `movzx eax,byte [rax+0xcb0]` --
// the two offsets the reverse-engineering notes name.
//
// The whole sequence runs inside a one-shot `Interceptor` on `CS::CSFeManImp::UpdatePlayerComponents`,
// the per-frame heads-up-display pass, so it executes on the game's own main thread rather than on
// a Frida thread. Calling `WarpNextStageKick_` off-thread would race the stage machine.

const RVA = {
  updatePlayerComponents: 0x773900,
  setupMapReentry: 0xcb1370,
  sessionManagerGlobal: 0x3d7e540,
  setDisableMapEnterAnim: 0x67b6a0,
  setMoveMapStepBlockId: 0x67ba20,
  setExplicitSpawn: 0x67b970,
  setInitialAreaEntityId: 0x67ba00,
  getExplicitSpawnFlag: 0x67b010,
  getExplicitSpawn: 0x67a0f0,
  warpNextStageKick: 0x5f89c0,
  getCurrentMapId: 0x5efe00,
  chrInsGetPhysicsPosition: 0x3f0e20,
  convertBlockCoordsToPhysics: 0x61ef70,
  worldChrManGlobal: 0x3d69ff8,
};

// `WorldChrMan.main_player`. The global and this offset are one instruction pair in the
// de-Arxan'd image (`mov rax,[rip+0x3baf365]; mov r13,[rax+0x1e508]`), which is why they are
// quoted together rather than looked up separately.
const MAIN_PLAYER_OFFSET = 0x1e508;

// The walk to the player's vitals, all cross-checked in
// `crates/er-npc-possess/src/possess/layout.rs`:
//   ChrIns + 0x190            -> ChrInsModuleContainer
//   container + 0x00          -> CSChrDataModule,    + 0x138 hp (i32)
//   container + 0x68          -> CSChrPhysicsModule, + 0x70 position, + 0x92 standingOnSolidGround
// `hp` is proven by `CSChrDataModule::GetHpRate` reading `[+0x138] / [+0x13c]`, and the solid-ground
// byte is the field `ChrIns::IsStandingOnSolidGround` itself reads, so neither is a heuristic.
const MODULES_OFFSET = 0x190;
const MODULE_DATA = 0x00;
const MODULE_PHYSICS = 0x68;
const DATA_HP = 0x138;
const DATA_HP_MAX = 0x13c;
const PHYSICS_POSITION = 0x70;
const PHYSICS_ON_SOLID_GROUND = 0x92;

// `SetupMapReentry` runs only from `protocolState == InGame`, and its own first statement writes
// `WaitReentryToMap`, so the gate is self-latching: a second warp issued before the engine has
// driven the session back sees 7 and skips it. That is expected, not a failure.
const PROTOCOL_STATE_OFFSET = 0x10;
const PROTOCOL_STATE_IN_GAME = 6;
const SPAWN_FLAG_ARMED = 1;
// `TriggerAreaReload` stores this beside the spawn position.
const SPAWN_POSITION_W = 1.0;

let base = null;
let fn = {};
let pending = null;
let listener = null;

// Frame cadence, taken from the same per-frame hook the warp runs in. `frame` counts every pass
// through it, so the host can tell a sample taken before a warp executed from one taken after:
// the warp report carries the frame it ran on, and every sample carries its own.
let frame = 0;
// `null` means the stream is off. Otherwise a sample is sent on the first frame at least this
// many milliseconds after the previous one, so the host's `--rate` is honoured while every
// sample is still read on the game's own thread at a frame boundary.
let streamPeriodMs = null;
let lastStreamAt = 0;
// Set when a warp kicked the stage machine; the next frame's pass reports that it ran, which is
// the proof the main thread has left the hook invocation that issued the kick and driven the
// stage machine one more frame.
let kickedOnFrame = null;

function resolve() {
  const mod = Process.findModuleByName('eldenring.exe');
  if (mod === null) throw new Error('eldenring.exe not found in this process');
  base = mod.base;
  const at = (rva) => base.add(rva);
  fn = {
    setDisableMapEnterAnim: new NativeFunction(at(RVA.setDisableMapEnterAnim), 'void', ['bool'], 'win64'),
    setMoveMapStepBlockId: new NativeFunction(at(RVA.setMoveMapStepBlockId), 'pointer', ['pointer', 'pointer'], 'win64'),
    setExplicitSpawn: new NativeFunction(at(RVA.setExplicitSpawn), 'void', ['pointer', 'pointer'], 'win64'),
    setInitialAreaEntityId: new NativeFunction(at(RVA.setInitialAreaEntityId), 'void', ['pointer'], 'win64'),
    getExplicitSpawnFlag: new NativeFunction(at(RVA.getExplicitSpawnFlag), 'uint8', [], 'win64'),
    getExplicitSpawn: new NativeFunction(at(RVA.getExplicitSpawn), 'void', ['pointer', 'pointer'], 'win64'),
    warpNextStageKick: new NativeFunction(at(RVA.warpNextStageKick), 'void', [], 'win64'),
    getCurrentMapId: new NativeFunction(at(RVA.getCurrentMapId), 'pointer', ['pointer'], 'win64'),
    setupMapReentry: new NativeFunction(at(RVA.setupMapReentry), 'void', ['pointer', 'bool'], 'win64'),
    chrInsGetPhysicsPosition: new NativeFunction(at(RVA.chrInsGetPhysicsPosition), 'pointer', ['pointer', 'pointer'], 'win64'),
    convertBlockCoordsToPhysics: new NativeFunction(at(RVA.convertBlockCoordsToPhysics), 'bool', ['pointer', 'pointer', 'pointer'], 'win64'),
  };
  return base;
}

// Where the engine actually put the player, in physics space, plus where the requested
// block-local coordinate lands in that same space. Comparing the two is the only way to tell a
// point the player settled on from one they slid or fell off, and it needs no screenshot.
function probeWhere(req) {
  const out = { block: '0x' + currentBlock().toString(16) };
  const worldChrMan = base.add(RVA.worldChrManGlobal).readPointer();
  if (worldChrMan.isNull()) {
    out.player = null;
    out.reason = 'WorldChrMan is null';
  } else {
    const player = worldChrMan.add(MAIN_PLAYER_OFFSET).readPointer();
    if (player.isNull()) {
      out.player = null;
      out.reason = 'no main player';
    } else {
      const pos = Memory.alloc(16);
      fn.chrInsGetPhysicsPosition(player, pos);
      out.player = { x: pos.readFloat(), y: pos.add(4).readFloat(), z: pos.add(8).readFloat() };
    }
  }
  if (req && req.block !== undefined) {
    const local = Memory.alloc(16);
    local.writeFloat(req.x);
    local.add(4).writeFloat(req.y);
    local.add(8).writeFloat(req.z);
    local.add(12).writeFloat(0.0);
    const blockId = Memory.alloc(4);
    blockId.writeU32(req.block);
    const world = Memory.alloc(16);
    const ok = fn.convertBlockCoordsToPhysics(world, local, blockId);
    out.targetInPhysics = ok
      ? { x: world.readFloat(), y: world.add(4).readFloat(), z: world.add(8).readFloat() }
      : null;
    if (!ok) out.convertRefused = true;
  }
  return out;
}

function currentBlock() {
  const out = Memory.alloc(4);
  fn.getCurrentMapId(out);
  return out.readU32();
}

function sessionGate() {
  const manager = base.add(RVA.sessionManagerGlobal).readPointer();
  if (manager.isNull()) return 'null-manager';
  const state = manager.add(PROTOCOL_STATE_OFFSET).readS32();
  if (state !== PROTOCOL_STATE_IN_GAME) return 'not-in-game(' + state + ')';
  fn.setupMapReentry(manager, 1);
  return 'entered';
}

function performWarp(req) {
  const report = { target: req, origin: null, gate: null, effectiveBlock: null, flag: null, readback: null };
  report.origin = '0x' + currentBlock().toString(16);
  report.gate = sessionGate();

  fn.setDisableMapEnterAnim(1);

  const wanted = Memory.alloc(4);
  wanted.writeU32(req.block);
  const effective = Memory.alloc(4);
  effective.writeU32(req.block);
  fn.setMoveMapStepBlockId(effective, wanted);
  report.effectiveBlock = '0x' + effective.readU32().toString(16);

  // Block-local coordinates go in untouched: `MoveMapStep` runs
  // `ConvertBlockCoordsToPhysicsCoords` on them itself, so converting here would add the block
  // origin twice. The orientation slot is euler radians, yaw in `.y`.
  const position = Memory.alloc(16);
  position.writeFloat(req.x);
  position.add(4).writeFloat(req.y);
  position.add(8).writeFloat(req.z);
  position.add(12).writeFloat(SPAWN_POSITION_W);
  const orientation = Memory.alloc(16);
  orientation.writeFloat(0.0);
  orientation.add(4).writeFloat(req.yaw);
  orientation.add(8).writeFloat(0.0);
  orientation.add(12).writeFloat(0.0);
  fn.setExplicitSpawn(position, orientation);

  // Read the slot back before kicking. A slot that did not latch means `MoveMapStep` ignores the
  // coordinates and drops the player at the block default -- a silently wrong warp, which for
  // this experiment would look exactly like a point that turned out to be fine.
  const flag = fn.getExplicitSpawnFlag();
  report.flag = flag;
  if (flag !== SPAWN_FLAG_ARMED) {
    report.refused = 'spawn slot did not latch';
    return report;
  }
  const posBack = Memory.alloc(16);
  const oriBack = Memory.alloc(16);
  fn.getExplicitSpawn(posBack, oriBack);
  report.readback = {
    x: posBack.readFloat(),
    y: posBack.add(4).readFloat(),
    z: posBack.add(8).readFloat(),
    yaw: oriBack.add(4).readFloat(),
  };

  fn.warpNextStageKick();
  report.kicked = true;
  return report;
}

// Plain reads. Every step is guarded: during a load the player pointer is null and the honest
// answer is `null`, never a fabricated zero, because "hp is 0" and "there is no player" are
// opposite facts.
function readVitals() {
  const out = {
    block: null,
    player: null,
    hp: null,
    hpMax: null,
    onSolidGround: null,
    protocolState: null,
    spawnFlag: null,
  };
  try {
    out.block = '0x' + currentBlock().toString(16);
    out.spawnFlag = fn.getExplicitSpawnFlag();
    const mgr = base.add(RVA.sessionManagerGlobal).readPointer();
    if (!mgr.isNull()) out.protocolState = mgr.add(PROTOCOL_STATE_OFFSET).readS32();
    const worldChrMan = base.add(RVA.worldChrManGlobal).readPointer();
    if (worldChrMan.isNull()) return out;
    const chr = worldChrMan.add(MAIN_PLAYER_OFFSET).readPointer();
    if (chr.isNull()) return out;
    const modules = chr.add(MODULES_OFFSET).readPointer();
    if (modules.isNull()) return out;
    const data = modules.add(MODULE_DATA).readPointer();
    if (!data.isNull()) {
      out.hp = data.add(DATA_HP).readS32();
      out.hpMax = data.add(DATA_HP_MAX).readS32();
    }
    const physics = modules.add(MODULE_PHYSICS).readPointer();
    if (!physics.isNull()) {
      const p = physics.add(PHYSICS_POSITION);
      out.player = { x: p.readFloat(), y: p.add(4).readFloat(), z: p.add(8).readFloat() };
      out.onSolidGround = physics.add(PHYSICS_ON_SOLID_GROUND).readU8() !== 0;
    }
  } catch (e) {
    out.error = e.message;
  }
  return out;
}

function armListener() {
  if (listener !== null) return;
  listener = Interceptor.attach(base.add(RVA.updatePlayerComponents), {
    onEnter() {
      frame += 1;
      if (kickedOnFrame !== null) {
        send({ type: 'frame-after-kick', kickedOnFrame: kickedOnFrame, frame: frame });
        kickedOnFrame = null;
      }
      if (pending !== null) {
        const req = pending;
        pending = null;
        let report;
        try {
          report = req.probeOnly ? probeWhere(req) : performWarp(req);
        } catch (e) {
          report = { error: e.message, target: req };
        }
        report.thread = Process.getCurrentThreadId();
        report.frame = frame;
        if (report.kicked) kickedOnFrame = frame;
        send({ type: 'warp', report: report });
      }
      if (streamPeriodMs !== null) {
        const now = Date.now();
        if (now - lastStreamAt >= streamPeriodMs) {
          lastStreamAt = now;
          const v = readVitals();
          v.frame = frame;
          v.t = now;
          v.source = 'frame';
          send({ type: 'vitals', vitals: v });
        }
      }
    },
  });
}

rpc.exports = {
  ready() {
    resolve();
    armListener();
    return {
      base: base.toString(),
      block: '0x' + currentBlock().toString(16),
      spawnFlag: fn.getExplicitSpawnFlag(),
    };
  },
  warp(block, x, y, z, yaw) {
    if (base === null) resolve();
    armListener();
    pending = { block: block, x: x, y: y, z: z, yaw: yaw };
    return 'queued';
  },
  where() {
    if (base === null) resolve();
    return { block: '0x' + currentBlock().toString(16), spawnFlag: fn.getExplicitSpawnFlag() };
  },
  // One read on the calling Frida thread, for the moments the per-frame pass is not running --
  // a load, or a softlocked session -- where the stream below delivers nothing.
  vitals() {
    if (base === null) resolve();
    const v = readVitals();
    v.frame = frame;
    v.t = Date.now();
    v.source = 'rpc';
    return v;
  },
  // Start sending a `vitals` sample from the per-frame pass at most once per `periodMs`
  // (0 means every frame). Returns the current frame count.
  stream(periodMs) {
    if (base === null) resolve();
    armListener();
    lastStreamAt = 0;
    streamPeriodMs = periodMs;
    return frame;
  },
  unstream() {
    streamPeriodMs = null;
    return frame;
  },
  // Block-local -> physics, through the engine's own converter. Needed because a target is
  // authored block-local while the player reads back in physics space, and the two differ by
  // the block origin -- so comparing them directly says a landing missed by tens of metres
  // when it did not. Returns null when the block's world info is not resident, which is the
  // converter's own way of saying "not loaded".
  convert(block, x, y, z) {
    if (base === null) resolve();
    const local = Memory.alloc(16);
    local.writeFloat(x);
    local.add(4).writeFloat(y);
    local.add(8).writeFloat(z);
    local.add(12).writeFloat(0.0);
    const id = Memory.alloc(4);
    id.writeU32(block);
    const world = Memory.alloc(16);
    if (!fn.convertBlockCoordsToPhysics(world, local, id)) return null;
    return { x: world.readFloat(), y: world.add(4).readFloat(), z: world.add(8).readFloat() };
  },
  probe(block, x, y, z) {
    if (base === null) resolve();
    armListener();
    pending = { probeOnly: true, block: block, x: x, y: y, z: z };
    return 'queued';
  },
};
