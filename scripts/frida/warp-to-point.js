// Warp the player to an arbitrary block-local coordinate, on demand, from outside the game.
//
// This is the sequence `er_invasion_warp.dll` performs in
// `crates/er-invasion-warp-core/src/warp.rs` -- the engine's own `TriggerAreaReload` steps with
// our destination substituted -- replayed here so a target can be chosen while the game is
// already running, with no rebuild and no relaunch.
//
// The addresses were 1.17.1, carried from the 1.16.2 constants in `warp.rs` through
// `docs/recon/rva-map-1162-to-1170.verified.tsv` and `scripts/map-rvas-1170-to-1171.py`, then
// byte-checked against `eldenring-deobf-1.17.1.bin`. The prologues confirm the semantics rather
// than merely the location: `SET_EXPLICIT_SPAWN` opens `movaps xmm0,[rcx]` then
// `movups [rax+0xc90],xmm0`, and `GET_EXPLICIT_SPAWN_FLAG` is `movzx eax,byte [rax+0xcb0]` --
// the two offsets the reverse-engineering notes name.
//
// They are now 2.7.2.0 (1.17.1 -> 2.7.2.0 beside each), carried with
// `scripts/map-rvas-1171-to-1172.py`. Every function is byte-identical in
// `eldenring-deobf-1.17.2.bin` apart from relocated call and data operands, and both globals have
// the same readers in both builds (session manager 3143 of 3147, the rest Arxan stubs).
//
// The whole sequence runs inside a one-shot `Interceptor` on `CS::CSFeManImp::UpdatePlayerComponents`,
// the per-frame heads-up-display pass, so it executes on the game's own main thread rather than on
// a Frida thread. Calling `WarpNextStageKick_` off-thread would race the stage machine.

const RVA = {
  updatePlayerComponents: 0x773db0, // 1.17.1 0x773900
  setupMapReentry: 0xcb2a60, // 1.17.1 0xcb1370
  sessionManagerGlobal: 0x3d81560, // 1.17.1 0x3d7e540
  setDisableMapEnterAnim: 0x67bb50, // 1.17.1 0x67b6a0
  setMoveMapStepBlockId: 0x67bed0, // 1.17.1 0x67ba20
  setExplicitSpawn: 0x67be20, // 1.17.1 0x67b970
  setInitialAreaEntityId: 0x67beb0, // 1.17.1 0x67ba00
  getExplicitSpawnFlag: 0x67b4c0, // 1.17.1 0x67b010
  getExplicitSpawn: 0x67a5a0, // 1.17.1 0x67a0f0
  warpNextStageKick: 0x5f8e70, // 1.17.1 0x5f89c0
  getCurrentMapId: 0x5f02b0, // 1.17.1 0x5efe00
  chrInsGetPhysicsPosition: 0x3f0e20, // 1.17.1 0x3f0e20, unmoved
  convertBlockCoordsToPhysics: 0x61f420, // 1.17.1 0x61ef70
  convertPhysicsToBlockCoords: 0x61f570, // 1.17.1 0x61f0c0
  chrInsGetOrientation: 0x3eeb80, // 1.17.1 0x3eeb80, unmoved
  chrInsRequestWarp: 0x3fa660, // 1.17.1 0x3fa5d0
  worldChrManGlobal: 0x3d6d018, // 1.17.1 0x3d69ff8
};

// The local warp: the engine's own in-map teleport, with no area reload.
//
// `0x1403fa5d0` (2.7.2.0 `0x1403fa660`; 1.16.2 `0x1403fa3a0`, unnamed there) is what every in-place
// move of a live character goes through -- 23 callers in 1.16.2, among them the EMEVD
// WarpCharacterAndCopyFloor bodies `FUN_1405f7790` / `FUN_1405f7890` (1.17.1 `0x1405f85e0` /
// `0x1405f86e0`, 2.7.2.0 `0x1405f8a90` / `0x1405f8b90`) behind
// `CSEventFadeWarpCtrl::STEP_Warp`. Its signature is (ChrIns*, FloatVector4* physicsPos,
// FloatVector4* euler). It does not move anything itself: it calls `0x1403c9250` (unmoved in
// 2.7.2.0), which is
//
//   or     dword [rcx+0xfc], 3          ; ChrCtrl.chrProxyFlags |= pos | rot
//   movaps xmm0, [rdx]                  ; position, 16-byte aligned or #GP
//   movups [rcx+0x100], xmm0            ; ChrCtrl.ragdollPosition
//   movaps xmm0, [r8]                   ; euler, 16-byte aligned or #GP
//   movups [rcx+0x110], xmm0            ; ChrCtrl.ragdollRotation
//
// and then marks the ChrIns dirty (+0x2e7 |= 2, vtable +0x480, +0x1c6 |= 0x80, +0xc8 = 1,
// +0x350 = 1.0). The engine drains the request on its next character update through
// `ChrCtrl::UpdatePositions`: `CSChrPhysicsModule::ForceSetPosition` + `SetOrientation` + the
// Havok proxy move, then it clears the flags. The EMEVD caller follows the call with
// `or byte [rbx+0x1c4], 2` (force this character's update this frame), and so does this agent.
// Read out of eldenring-deobf-1.17.1.bin at `0x1405f8650..0x1405f8662`, so the 1.17.1 call
// sequence is byte-proven, not carried. In 2.7.2.0 the same bytes sit at `0x1405f8b00..0x1405f8b12`
// inside a byte-identical body.
//
// The position is in physics space, the space `ChrIns::GetPhysicsPosition` reads. A block-local
// target is put through `ConvertBlockCoordsToPhysicsCoords` (`0x14061ef70`, 2.7.2.0 `0x14061f420`,
// 1.16.2 `0x14061e120`), which is a pure translation by the block's origin (overworld: the grid
// cell's `WorldGridAreaInfo` coordinates; legacy: `WorldBlockInfo::GetBlockCenterInPhysicsSpace`).
// `ConvertPhysicsCoordinatesToBlockCoordinates` (`0x14061f0c0`, 2.7.2.0 `0x14061f570`) is its exact
// inverse.
//
// `ForceSetPosition` does not touch `CSChrPhysicsModule+0x150` lastGroundedPosition, and the fall
// module charges `lastGroundedPosition.y - position.y` on landing (bd
// invincibility-does-not-cover-fall-death-pin-lastgrounded-2026-09-02). So on the frame after the
// drain the agent pins +0x150 to the new position: a downward warp then lands as a fall from the
// target, not from where the player stood before.
//
// These offsets read unchanged in 2.7.2.0, each in a function that is byte-identical across the
// builds (1.17.1 -> 2.7.2.0): ChrIns +0x58 in the warp request 0x1403fa5d0 -> 0x1403fa660, +0x1c4 in
// the EMEVD body above, +0x30 in ChrIns::GetBlockId 0x1403f0470 (unmoved), ChrCtrl +0xfc in
// 0x1403c9250, physics +0x150 in the fall check 0x14044e27f -> 0x14044e35f, and PlayerIns +0x6c0
// and +0x6d0 in the block-position writer 0x14065ad00 -> 0x14065b1b0.
const CHR_INS_CHR_CTRL = 0x58;
const CHR_INS_FLAGS_1C4 = 0x1c4;
const CHR_INS_FLAGS_1C4_FORCE_UPDATE = 0x2;
const CHR_INS_BLOCK_ID = 0x30;
const CHR_CTRL_PROXY_FLAGS = 0xfc;
const CHR_CTRL_PROXY_FLAGS_POS_ROT = 0x3;
const PHYSICS_LAST_GROUNDED = 0x150;
// PlayerIns block_position (fromsoftware-rs `PlayerIns::block_position`, "position within the
// current block"), followed by current_block_id at +0x10 and safe_block_pos at +0x14. Found live
// 2026-10-17 by scanning the PlayerIns for the converter's output: +0x6c0 matched it to the last
// bit, with 0x3c2a2400 (m60_42_36_00) at +0x6d0. The engine maintains it itself, so it is an
// independent check that `blockLocal` (our conversion) is the frame the MSB and the warp use.
const PLAYER_INS_BLOCK_POSITION = 0x6c0;
// Frames to watch after the request before reporting where the player settled. 45 is ~0.75 s at
// 60 fps: long enough for a short drop onto the target surface to finish.
const LOCAL_SETTLE_FRAMES = 45;

// `WorldChrMan.main_player`. The global and this offset are one instruction pair in the
// de-Arxan'd image (`mov rax,[rip+disp]; mov r13,[rax+0x1e508]`), which is why they are quoted
// together rather than looked up separately. 2.7.2.0 keeps it: `UpdatePlayerComponents` reads
// `[r12+0x1e508]` in both builds.
const MAIN_PLAYER_OFFSET = 0x1e508;

// The walk to the player's vitals, all cross-checked in
// `crates/er-npc-possess/src/possess/layout.rs`:
//   ChrIns + 0x190            -> ChrInsModuleContainer
//   container + 0x00          -> CSChrDataModule,    + 0x138 hp (i32)
//   container + 0x68          -> CSChrPhysicsModule, + 0x70 position, + 0x92 standingOnSolidGround
// `hp` is proven by `CSChrDataModule::GetHpRate` reading `[+0x138] / [+0x13c]`, and the solid-ground
// byte is the field `ChrIns::IsStandingOnSolidGround` itself reads, so neither is a heuristic.
// Same in 2.7.2.0: GetHpRate 0x1404377c0 -> 0x140437850 and IsStandingOnSolidGround 0x1403f4040
// (unmoved, +0x190 -> +0x68 -> +0x92) are byte-identical, `ChrCtrl::SetScaleSize` still takes the
// data module as `modules[0]`, and the position accessor still reads `+0x70`.
const MODULES_OFFSET = 0x190;
const MODULE_DATA = 0x00;
const MODULE_PHYSICS = 0x68;
const DATA_HP = 0x138;
const DATA_HP_MAX = 0x13c;
const PHYSICS_POSITION = 0x70;
const PHYSICS_ON_SOLID_GROUND = 0x92;

// `SetupMapReentry` runs only from `protocolState == InGame`, and its own first statement writes
// `WaitReentryToMap`, so the gate is self-latching: a second warp issued before the engine has
// driven the session back sees 7 and skips it. That is expected, not a failure. 2.7.2.0 keeps both
// numbers: `SetupMapReentry` still opens `mov dword [rcx+0x10], 7`, and `TriggerAreaReload` still
// gates its call on `cmp dword [rcx+0x10], 6`; both functions are byte-identical.
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
// Set while a local warp is being followed frame by frame (see `followLocalWarp`).
let localWatch = null;

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
    convertPhysicsToBlockCoords: new NativeFunction(at(RVA.convertPhysicsToBlockCoords), 'bool', ['pointer', 'pointer', 'pointer'], 'win64'),
    chrInsGetOrientation: new NativeFunction(at(RVA.chrInsGetOrientation), 'pointer', ['pointer', 'pointer'], 'win64'),
    chrInsRequestWarp: new NativeFunction(at(RVA.chrInsRequestWarp), 'void', ['pointer', 'pointer', 'pointer'], 'win64'),
  };
  return base;
}

// A FloatVector4 the engine may load with MOVAPS. `Memory.alloc` promises no 16-byte alignment.
function vec4Aligned(x, y, z, w) {
  const raw = Memory.alloc(32);
  const p = raw.add((16 - (raw.toUInt32() & 15)) & 15);
  p.writeFloat(x);
  p.add(4).writeFloat(y);
  p.add(8).writeFloat(z);
  p.add(12).writeFloat(w);
  return { raw: raw, p: p };
}

function readVec3(p) {
  return { x: p.readFloat(), y: p.add(4).readFloat(), z: p.add(8).readFloat() };
}

function mainPlayer() {
  const worldChrMan = base.add(RVA.worldChrManGlobal).readPointer();
  if (worldChrMan.isNull()) return null;
  const chr = worldChrMan.add(MAIN_PLAYER_OFFSET).readPointer();
  return chr.isNull() ? null : chr;
}

function playerPhysics(chr) {
  return chr.add(MODULES_OFFSET).readPointer().add(MODULE_PHYSICS).readPointer();
}

function toBlock(physicsPos, block) {
  const id = Memory.alloc(4);
  id.writeU32(block);
  const out = vec4Aligned(0, 0, 0, 0);
  if (!fn.convertPhysicsToBlockCoords(out.p, physicsPos, id)) return null;
  return readVec3(out.p);
}

// Where the player is, in both spaces, plus the block they are in.
function localState() {
  const chr = mainPlayer();
  if (chr === null) return null;
  const ph = playerPhysics(chr);
  const block = chr.add(CHR_INS_BLOCK_ID).readU32() >>> 0;
  const phys = readVec3(ph.add(PHYSICS_POSITION));
  const posBuf = vec4Aligned(phys.x, phys.y, phys.z, 1.0);
  const euler = vec4Aligned(0, 0, 0, 0);
  fn.chrInsGetOrientation(chr, euler.p);
  return {
    playerBlock: '0x' + block.toString(16),
    mapBlock: '0x' + currentBlock().toString(16),
    physics: phys,
    blockLocal: toBlock(posBuf.p, block),
    engineBlockLocal: readVec3(chr.add(PLAYER_INS_BLOCK_POSITION)),
    engineBlock: '0x' + (chr.add(PLAYER_INS_BLOCK_POSITION + 0x10).readU32() >>> 0).toString(16),
    yaw: euler.p.add(4).readFloat(),
    standing: ph.add(PHYSICS_ON_SOLID_GROUND).readU8() !== 0,
    lastGrounded: readVec3(ph.add(PHYSICS_LAST_GROUNDED)),
    proxyFlags: chr.add(CHR_INS_CHR_CTRL).readPointer().add(CHR_CTRL_PROXY_FLAGS).readU32(),
  };
}

// Request the engine's in-map teleport for the main player. Runs on the game thread.
function performLocalWarp(req) {
  const report = { mode: 'local', target: req, before: localState() };
  const chr = mainPlayer();
  if (chr === null) {
    report.refused = 'no main player';
    return report;
  }
  const playerBlock = chr.add(CHR_INS_BLOCK_ID).readU32() >>> 0;
  if (playerBlock !== (req.block >>> 0) && !req.anyLoadedBlock) {
    report.refused = 'target block is not the player block (' + report.before.playerBlock + ')';
    return report;
  }
  const local = vec4Aligned(req.x, req.y, req.z, 0.0);
  const id = Memory.alloc(4);
  id.writeU32(req.block);
  const pos = vec4Aligned(0, 0, 0, 1.0);
  if (!fn.convertBlockCoordsToPhysics(pos.p, local.p, id)) {
    report.refused = 'ConvertBlockCoordsToPhysicsCoords refused the block (not resident)';
    return report;
  }
  // The converter writes a FloatVector3; keep w = 1 as GetDmyPolyPosition hands the EMEVD path.
  pos.p.add(12).writeFloat(1.0);
  const euler = vec4Aligned(0, 0, 0, 0);
  if (req.yaw === null || req.yaw === undefined) {
    fn.chrInsGetOrientation(chr, euler.p);
  } else {
    euler.p.add(4).writeFloat(req.yaw);
  }
  report.physicsTarget = readVec3(pos.p);
  report.euler = readVec3(euler.p);
  fn.chrInsRequestWarp(chr, pos.p, euler.p);
  chr.add(CHR_INS_FLAGS_1C4).writeU8(chr.add(CHR_INS_FLAGS_1C4).readU8() | CHR_INS_FLAGS_1C4_FORCE_UPDATE);
  report.requestedFlags = chr.add(CHR_INS_CHR_CTRL).readPointer().add(CHR_CTRL_PROXY_FLAGS).readU32();
  report.requested = true;
  localWatch = {
    startFrame: frame,
    target: report.physicsTarget,
    pin: req.pinGround !== false,
    pinned: false,
    drainedOnFrame: null,
    settle: req.settleFrames || LOCAL_SETTLE_FRAMES,
  };
  return report;
}

// Per-frame follow-up of a local warp: notice the drain, pin lastGroundedPosition, then report.
function followLocalWarp() {
  const w = localWatch;
  const s = localState();
  if (s === null) return;
  const elapsed = frame - w.startFrame;
  const d = Math.hypot(s.physics.x - w.target.x, s.physics.y - w.target.y, s.physics.z - w.target.z);
  if (w.drainedOnFrame === null && (s.proxyFlags & CHR_CTRL_PROXY_FLAGS_POS_ROT) === 0) {
    w.drainedOnFrame = frame;
    w.distAtDrain = d;
    if (w.pin) {
      const ph = playerPhysics(mainPlayer());
      ph.add(PHYSICS_LAST_GROUNDED).writeFloat(s.physics.x);
      ph.add(PHYSICS_LAST_GROUNDED + 4).writeFloat(s.physics.y);
      ph.add(PHYSICS_LAST_GROUNDED + 8).writeFloat(s.physics.z);
      w.pinned = true;
    }
    send({ type: 'local-drained', frame: frame, elapsed: elapsed, distFromTarget: d, state: s, pinned: w.pinned });
  }
  if (elapsed >= w.settle) {
    localWatch = null;
    send({ type: 'local-settled', frame: frame, elapsed: elapsed, drainedOnFrame: w.drainedOnFrame, distAtDrain: w.distAtDrain === undefined ? null : w.distAtDrain, distFromTarget: d, state: s });
  }
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
          report = req.probeOnly ? probeWhere(req) : req.local ? performLocalWarp(req) : performWarp(req);
        } catch (e) {
          report = { error: e.message, target: req };
        }
        report.thread = Process.getCurrentThreadId();
        report.frame = frame;
        if (report.kicked) kickedOnFrame = frame;
        send({ type: 'warp', report: report });
      }
      if (localWatch !== null && pending === null) {
        try {
          followLocalWarp();
        } catch (e) {
          localWatch = null;
          send({ type: 'local-settled', error: e.message });
        }
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
    return { block: '0x' + currentBlock().toString(16), spawnFlag: fn.getExplicitSpawnFlag(), player: localState() };
  },
  // The in-map teleport, no area reload. `yaw` null keeps the current facing. `anyLoadedBlock`
  // lets the target name a block other than the player's own, provided the converter accepts it.
  localWarp(block, x, y, z, yaw, pinGround, anyLoadedBlock) {
    if (base === null) resolve();
    armListener();
    pending = { local: true, block: block, x: x, y: y, z: z, yaw: yaw, pinGround: pinGround, anyLoadedBlock: anyLoadedBlock };
    return 'queued';
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
