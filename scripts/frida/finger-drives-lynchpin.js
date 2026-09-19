// Make a vanilla invasion finger run Seamless's Challenger's Lynchpin action.
//
// The finger and the Lynchpin end in the same place -- `ersc+0x25850`, which reads `[rcx+0x58]` as
// the session and touches `rcx` nowhere else -- so the only thing the vanilla item is missing is
// somebody to make that call on its behalf. This is that somebody, in Frida, with no DLL loaded.
//
// # The two mistakes this is shaped around, both paid for today
//
// 1. Driving the invade from the bounds popup's own thread hard-locked the game twice. The popup is
//    the game's menu and its thread can be holding the first acquire of Seamless's session mutex.
//    So the popup only ARMS, and `CS::FeSystemAnnounceView::Update` -- an ordinary game-thread
//    update -- is where the call is made, one frame later.
// 2. The session was picked six times by numeric resemblance and six times it was the wrong object,
//    the last being an axis-aligned bounding box whose `CRITICAL_SECTION` read
//    `OwningThread=0xff7fffeeff7fffee`. So the lock itself is tested here, not the object's shape.
//
// Nothing is hooked inside `ersc.dll`: its action is CALLED, never patched, because a trampoline on
// that entry breaks the prologue check `er_invasion_warp.dll` makes before every call to it.
const INVADE_RVA = 0x25850;
const SESSION_MUTEX_OFFSET = 0x100;
const SESSION_STATE_OFFSET = 0x150;
const OWNER_SESSION_OFFSET = 0x58;
const STATE_IDLE = 0x01;
const STATE_SEARCHING = 0x0e;

// The popup the fingers open, from `er_invasion_warp.dll`'s own log on this build.
const OPEN_CHOICES = ptr('0x140e9e4f0');

// `WorldChrMan -> main player -> ChrIns+0x164`, the id of the goods use in flight. Category
// `0x40000000` over the param row; the field is residue when no use is running, so it is masked
// and range-checked rather than trusted.
const GLOBAL_WORLD_CHR_MAN = ptr('0x143d69ff8');
const WORLD_CHR_MAN_MAIN_PLAYER = 0x1e508;
const CHR_INS_CONFIRMED_USED_GOODS = 0x164;
const ITEM_CATEGORY_MASK = 0xf0000000;
const ITEM_CATEGORY_GOODS = 0x40000000;
const MAX_GOODS_ID = 0x0fffffff;

// The three rows Seamless greys out, from `crates/er-invasion-warp/src/vanilla_invasion_items.rs`.
const FINGERS = { 102: 'Bloody Finger', 111: 'Festering Bloody Finger', 112: 'Recusant Finger' };

// `EquipParamGoods::GetEntry(out, goodsId)` on 1.17.1, where `out` is
// `{ int paramId; int pad; _EQUIP_PARAM_GOODS_ST *row; }`. Mapped from 1.16.2 `0x140d39df0` and
// recorded in `docs/recon/rva-map-1162-to-1170.verified.tsv` as `IDENTICAL-WHOLE`; that row also
// notes this exact address was already called live through Frida and returned correct rows for
// five ids, including the two `ersc.dll` synthesises at runtime.
const EQUIP_PARAM_GOODS_GET_ENTRY = ptr('0x140d3b5b0');
// `disableOffline` in `EquipParamGoods`. `CanUseGoods` refuses the use action when this bit is set
// and the game's own online flag is clear, which is exactly the state Seamless leaves the player in.
const GOODS_FLAGS_OFFSET = 0x48;
const DISABLE_OFFLINE_BIT = 1 << 5;

const ersc = Process.findModuleByName('ersc.dll');

// Says WHY it answered nothing, once, so a silent detector can be told apart from a wrong read.
//
// The use-edge never fired through two bounds popups on 2026-09-16 while the tick around it was
// demonstrably live. That leaves two possibilities with opposite fixes -- the read is wrong (bad
// global or bad offset, so it can never see anything) or the read is right and no tick lands inside
// the use window -- and a detector that returns `null` for both cannot distinguish them.
let goodsWhySaid = false;
function goodsInUse () {
  let why = null;
  try {
    const world = GLOBAL_WORLD_CHR_MAN.readPointer();
    if (world.isNull()) {
      why = 'WorldChrMan is null';
    } else {
      const player = world.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
      if (player.isNull()) {
        why = `main player is null (WorldChrMan ${world} + 0x${WORLD_CHR_MAN_MAIN_PLAYER.toString(16)})`;
      } else if (Process.findModuleByAddress(player.readPointer()) === null) {
        why = `main player ${player} has no game vtable -- the offset is wrong`;
      } else {
        const raw = player.add(CHR_INS_CONFIRMED_USED_GOODS).readU32();
        if ((raw & ITEM_CATEGORY_MASK) === ITEM_CATEGORY_GOODS) {
          return raw & MAX_GOODS_ID;
        }
        why = `ChrIns+0x164 raw 0x${raw.toString(16)} is not the goods category`;
      }
    }
  } catch (e) {
    why = `read faulted: ${e.message}`;
  }
  if (!goodsWhySaid) {
    goodsWhySaid = true;
    console.log(`finger: no goods in use -- ${why} (said once; the field is residue when idle)`);
  }
  return null;
}

// A lock `InitializeCriticalSection` could have produced: free reads LockCount -1, RecursionCount 0,
// OwningThread 0; held reads LockCount >= 0 with a thread id. Uninitialised heap fails all three
// together, which is what the bounding box did.
function lockIsReal (session) {
  try {
    const cs = session.add(SESSION_MUTEX_OFFSET).add(8);
    const lockCount = cs.add(8).readS32();
    const recursion = cs.add(0xc).readS32();
    const owner = cs.add(0x10).readU64().toNumber();
    if (lockCount < -1 || recursion < 0 || owner > 0x100000) return false;
    return (recursion === 0) === (owner === 0);
  } catch (e) {
    return false;
  }
}

// Seamless's session, found through a pointer `ersc.dll` itself stores, then put to the lock test.
function findSession () {
  if (ersc === null) return null;
  for (const range of Process.enumerateRanges({ protection: 'rw-', coalesce: false })) {
    if (range.base.compare(ersc.base) < 0 || range.base.compare(ersc.base.add(ersc.size)) >= 0) {
      continue;
    }
    const words = range.size / Process.pointerSize;
    for (let i = 0; i < words; i++) {
      let candidate;
      try {
        candidate = range.base.add(i * Process.pointerSize).readPointer();
      } catch (e) {
        break;
      }
      if (candidate.isNull() || candidate.compare(ptr(0x10000)) < 0) continue;
      let state;
      try {
        state = candidate.add(SESSION_STATE_OFFSET).readU32();
      } catch (e) {
        continue;
      }
      if (state !== STATE_IDLE && state !== STATE_SEARCHING) continue;
      if (!lockIsReal(candidate)) continue;
      return candidate;
    }
  }
  return null;
}

// Clear `disableOffline` on the three fingers, which is the whole reason their `Use` is greyed out.
//
// Done here rather than in a DLL by user directive, 2026-09-16: "You're obligated to do it with
// Frida. The entire point is that dll has too many side effects."
// The chain a boot placeholder cannot fake, offsets read out of the sibling `fromsoftware-rs`
// `ChrIns` / `ChrInsModuleContainer` / `CSChrPhysicsModule` definitions and cross-checked against
// their own `unkNNN` field names:
//
//   WorldChrMan +0x1e508 -> PlayerIns (ChrIns at offset 0)
//   ChrIns      +0x190   -> ChrInsModuleContainer
//   container   +0x68    -> CSChrPhysicsModule   (`physics` is entry 13 of a pointer array)
//   physics     +0x70    -> HavokPosition        (`+0x90 unk90` pins the layout)
//   physics     +0x92    -> standing_on_solid_ground
//
// The previous gate stopped at "the player pointer has a game vtable", which a slot left over from
// boot also has -- so it passed during boot on run br-20260916-054913-df78, three param rows were
// rewritten seconds after launch, and the process died. A havok position is the difference: an
// unloaded character has no physics module at all, and a loaded-but-not-placed one sits at the
// origin. Requiring a finite, non-origin position is a fact about the world, not about a shape.
const CHR_INS_MODULES = 0x190;
const MODULES_PHYSICS = 0x68;
const PHYSICS_POSITION = 0x70;
const PHYSICS_ON_GROUND = 0x92;

let worldWhySaid = null;
function worldRefusal () {
  try {
    const world = GLOBAL_WORLD_CHR_MAN.readPointer();
    if (world.isNull()) return 'WorldChrMan is null';
    const player = world.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
    if (player.isNull()) return 'no main player yet';
    if (Process.findModuleByAddress(player.readPointer()) === null) {
      return `main player ${player} has no game vtable`;
    }
    const modules = player.add(CHR_INS_MODULES).readPointer();
    if (modules.isNull()) return 'the player has no module container -- not loaded';
    const physics = modules.add(MODULES_PHYSICS).readPointer();
    if (physics.isNull()) return 'the player has no physics module -- not placed';
    if (Process.findModuleByAddress(physics.readPointer()) === null) {
      return `physics module ${physics} has no game vtable`;
    }
    const pos = physics.add(PHYSICS_POSITION);
    const x = pos.readFloat();
    const y = pos.add(4).readFloat();
    const z = pos.add(8).readFloat();
    if (!isFinite(x) || !isFinite(y) || !isFinite(z)) {
      return `havok position is not finite (${x}, ${y}, ${z})`;
    }
    if (x === 0 && y === 0 && z === 0) return 'havok position is the origin -- loaded but not placed';
    return null;
  } catch (e) {
    return `world read faulted: ${e.message}`;
  }
}

// Says WHY it refused, and says it again whenever the reason CHANGES, so a boot that stalls at one
// step is distinguishable from one that is walking up the chain.
function playerIsInWorld () {
  const why = worldRefusal();
  if (why === null) return true;
  if (why !== worldWhySaid) {
    worldWhySaid = why;
    console.log(`finger: not in world yet -- ${why}`);
  }
  return false;
}

function reEnableFingers () {
  // `EquipParamGoods::GetEntry` opens by loading `GLOBAL_SoloParamRepository` and, when it is null,
  // takes an assert path that itself faults on a null `rcx` -- so calling it early is a `0xc0000005`
  // rather than a returned error. Measured here on 2026-09-16: calling it at attach time, before the
  // world was up, terminated the game mid-boot. The player existing is the cheap proxy for the param
  // tables existing, and it needs no address this agent does not already have.
  if (!playerIsInWorld()) {
    return -1;
  }
  const world = GLOBAL_WORLD_CHR_MAN.readPointer();
  const player = world.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
  const physics = player.add(CHR_INS_MODULES).readPointer().add(MODULES_PHYSICS).readPointer();
  const pos = physics.add(PHYSICS_POSITION);
  console.log(
    `finger: in world -- player ${player} at havok ` +
      `(${pos.readFloat().toFixed(1)}, ${pos.add(4).readFloat().toFixed(1)}, ${pos.add(8).readFloat().toFixed(1)})` +
      `, grounded ${physics.add(PHYSICS_ON_GROUND).readU8() !== 0}`
  );
  const getEntry = new NativeFunction(EQUIP_PARAM_GOODS_GET_ENTRY, 'pointer', ['pointer', 'uint32']);
  const out = Memory.alloc(16);
  let cleared = 0;
  for (const id of Object.keys(FINGERS)) {
    const goods = parseInt(id, 10);
    let row;
    try {
      out.writeU32(0xffffffff);
      out.add(8).writePointer(ptr(0));
      getEntry(out, goods);
      row = out.add(8).readPointer();
    } catch (e) {
      console.log(`finger: ${FINGERS[goods]} lookup faulted: ${e.message}`);
      continue;
    }
    if (row.isNull()) {
      console.log(`finger: ${FINGERS[goods]} (row ${goods}) not resolvable -- param tables not up`);
      continue;
    }
    try {
      const field = row.add(GOODS_FLAGS_OFFSET);
      const before = field.readU8();
      if ((before & DISABLE_OFFLINE_BIT) === 0) {
        console.log(`finger: ${FINGERS[goods]} already usable (+0x48 = 0x${before.toString(16)})`);
        continue;
      }
      field.writeU8(before & ~DISABLE_OFFLINE_BIT);
      cleared += 1;
      console.log(
        `finger: ${FINGERS[goods]} re-enabled -- row ${row}+0x48 ` +
          `0x${before.toString(16)} -> 0x${field.readU8().toString(16)}`
      );
    } catch (e) {
      console.log(`finger: ${FINGERS[goods]} flag write faulted: ${e.message}`);
    }
  }
  return cleared;
}

let armed = null;
let drives = 0;

if (ersc === null) {
  console.log('finger: ersc.dll is not loaded -- nothing to drive');
} else {
  const invade = new NativeFunction(ersc.base.add(INVADE_RVA), 'void', ['pointer']);
  // Our own `this`, holding the session where the action reads it. The function touches `rcx` once,
  // at +0x58, so whose box it is has no bearing on what it does -- and a box we allocated cannot be
  // a misidentification or be freed under the call.
  const owner = Memory.alloc(0x60);

  // The invade runs on a thread of our own, and every other caller shape has hard locked.
  //
  // `ersc+0x25850`'s first act is to take the session's `CRITICAL_SECTION`. Doing that from inside
  // a frame the game already owns deadlocks it, and that is a fact about the FRAME, not the thread:
  //
  //   the bounds popup's menu thread          two hard locks, 2026-09-16
  //   inside a hook on EquipParamGoods::GetEntry   hard lock, 2026-09-16, Seamless-only
  //   er_invasion_warp's own CSTaskImp game task   returned, 0x01 -> 0x0e
  //
  // The task tick worked because it owns its frame and holds nothing. This is the DLL-free
  // equivalent: a real Windows thread, created once, asleep in `WaitForSingleObject` until the hook
  // signals it. The hook does no work beyond setting a pointer and an event, so the game's frame is
  // never the one holding Seamless's lock.
  //
  // The wait is an event, not a timer: `scripts/check-no-timeouts.py` bans polling here, and an
  // auto-reset event is the readiness primitive it asks for.
  const k32 = Process.getModuleByName('KERNEL32.dll');
  const CreateEventA = new NativeFunction(k32.getExportByName('CreateEventA'), 'pointer', ['pointer', 'int', 'int', 'pointer']);
  const SetEvent = new NativeFunction(k32.getExportByName('SetEvent'), 'int', ['pointer']);
  const WaitForSingleObject = new NativeFunction(k32.getExportByName('WaitForSingleObject'), 'uint32', ['pointer', 'uint32']);
  const CreateThread = new NativeFunction(k32.getExportByName('CreateThread'), 'pointer', ['pointer', 'uint32', 'pointer', 'pointer', 'uint32', 'pointer']);
  const INFINITE = 0xffffffff;

  const wakeup = CreateEventA(NULL, 0, 0, NULL);
  // What the hook hands over: the goods row that was used. The session is resolved on the worker,
  // so nothing about it is read inside the game's frame either.
  const request = Memory.alloc(8);
  request.writeU32(0);

  let drives = 0;

  function driveOnce (goods) {
    const session = findSession();
    if (session === null) {
      console.log('finger: no session with a real lock -- refusing to drive');
      return;
    }
    let before;
    try {
      before = session.add(SESSION_STATE_OFFSET).readU32();
    } catch (e) {
      console.log('finger: session state unreadable -- refusing');
      return;
    }
    if (before !== STATE_IDLE) {
      console.log(`finger: session ${session} is at 0x${before.toString(16)}, not idle -- refusing`);
      return;
    }
    owner.add(OWNER_SESSION_OFFSET).writePointer(session);
    invade(owner);
    const after = session.add(SESSION_STATE_OFFSET).readU32();
    drives += 1;
    console.log(
      `finger: ${FINGERS[goods]} drove the Lynchpin action (#${drives}) -- session ${session} ` +
        `0x${before.toString(16)} -> 0x${after.toString(16)}` +
        (after === STATE_SEARCHING ? '  SEARCHING' : '  (did not reach searching)')
    );
  }

  // Held in a variable so the runtime cannot collect the trampoline the new thread is executing.
  // The worker is native code, because a JS worker deadlocks the agent's own load.
  //
  // Two attempts ran the loop as a `NativeCallback`. Both stopped the script mid-load: the new
  // thread's first call back into JS contends the runtime lock the loader is still holding, so
  // whatever statements came after `CreateThread` never ran. Moving the call to the last line only
  // moved where it stopped -- measured 2026-09-16, the monitor saw `watching 4 lobby slot(s)` and
  // never the line after it, while a direct attach printed both.
  //
  // So the thread executes C and touches no JS at all. It reads the goods field, spots the use
  // edge, and calls `ersc+0x25850` itself. JS keeps two jobs, both on threads it already owns:
  // handing the worker a session pointer, and reading back what happened.
  // All shared state lives in one block JS allocates, not in CModule globals.
  //
  // Writing `cm.g_*` faulted with an access violation on this build -- the module's data is not
  // mapped writable from the JS side -- so the block is `Memory.alloc`ed here and its address is
  // handed to the C as a symbol. C reads and writes it through fixed offsets; JS does the same.
  //
  //   +0x00 &WorldChrMan   +0x08 session   +0x10 owner shim
  //   +0x18 fingers[3]     +0x24 last goods seen
  //   +0x28 drives         +0x2c state before   +0x30 state after   +0x34 refusals
  //   +0x38 stop request   +0x3c magic          +0x40 worker left
  // The block lives at a FIXED address for the life of the process, not in the script's heap.
  //
  // `Memory.alloc` is freed when the script unloads, but the worker is a real Windows thread and
  // keeps running -- so every reattach left another thread looping over freed memory, and the
  // wreckage eventually wedged frida-server itself (`TransportError: timeout was reached` on plain
  // attach). A fixed `VirtualAlloc` fixes both halves: the memory outlives the script, and a magic
  // word in it tells a later attach that a worker already exists, so only one is ever created.
  const VirtualAlloc = new NativeFunction(k32.getExportByName('VirtualAlloc'), 'pointer', ['pointer', 'uint64', 'uint32', 'uint32']);
  const MEM_COMMIT_RESERVE = 0x1000 | 0x2000;
  const PAGE_READWRITE = 0x04;
  const CFG_AT = ptr('0x300000000');
  const CFG_MAGIC = 0x46494e47;
  const cfg = VirtualAlloc(CFG_AT, uint64(0x1000), MEM_COMMIT_RESERVE, PAGE_READWRITE);
  if (cfg.isNull()) {
    throw new Error('finger: could not reserve the shared block');
  }
  const workerAlreadyRunning = cfg.add(0x3c).readU32() === CFG_MAGIC;
  cfg.add(0x38).writeU32(0);
  cfg.add(0x40).writeU32(0);
  cfg.add(0x3c).writeU32(CFG_MAGIC);
  cfg.writePointer(GLOBAL_WORLD_CHR_MAN);
  cfg.add(0x08).writePointer(NULL);
  // No seed. A hard-coded owner from a previous process was tried and it is exactly the hazard
  // this file exists to avoid: on run br-20260916-054755-4d2a the dead address 0x469ad518 still
  // read as a live session and armed the worker, because `lockIsReal` is a shape test and shape
  // tests keep losing. The only object ever driven is one captured from `ersc.dll` in THIS process.
  cfg.add(0x18).writeU32(102);
  cfg.add(0x1c).writeU32(111);
  cfg.add(0x20).writeU32(112);

  const cm = new CModule(`
#include <stdint.h>

extern void Sleep (unsigned int ms);
extern void invade (void *owner);
extern uint8_t cfg[];
/* No callback into JS from here, and no backticks in this comment either: the whole module is a
   JS template literal, so one would end it.
   A callback was tried and the worker stopped on its first event. ChrIns+0x164 read 0x40000070 --
   row 112, a Recusant Finger, verified out of /proc -- while the agent reported nothing at all,
   which is what a NativeCallback invoked from a thread Frida did not create looks like when it
   blocks. The counters below are the only channel. */

static uint32_t goods_in_use (void) {
  uint64_t slot = *(uint64_t *) (cfg + 0x00);
  if (slot == 0) return 0;
  uint64_t world = *(uint64_t *) slot;
  if (world == 0) return 0;
  uint64_t player = *(uint64_t *) (world + 0x1e508);
  if (player == 0) return 0;
  uint32_t raw = *(uint32_t *) (player + 0x164);
  if ((raw & 0xf0000000u) != 0x40000000u) return 0;
  return raw & 0x0fffffffu;
}

/* The worker must be GONE before this module's code pages are, or its next instruction faults
   with an execute violation and takes the game with it. That is what killed run
   br-20260916-055037-8070: crash-log recorded
     access-violation addr=0x613c0123 (RIP outside .text) access=8   [8 = execute]
     modbt=[kernel32.dll+0x11649, ...]                               [BaseThreadInitThunk]
   which is a thread whose entry point had been freed. A fixed VirtualAlloc gave the shared BLOCK a
   lifetime longer than the script; the CODE still belonged to the script.
   So: JS never frees this module without Frida calling finalize first, and finalize does not return
   until the worker has acknowledged that it has left. */
void finalize (void) {
  *(uint32_t *) (cfg + 0x38) = 1;
  for (int i = 0; i < 200; i++) {
    if (*(uint32_t *) (cfg + 0x40) != 0) break;
    Sleep (5);
  }
  /* The worker is gone, so the next attach must build a new one. */
  *(uint32_t *) (cfg + 0x3c) = 0;
}

void watch (void) {
  uint32_t seen_last = 0;
  *(uint32_t *) (cfg + 0x40) = 0;
  for (;;) {
    if (*(uint32_t *) (cfg + 0x38) != 0) {
      *(uint32_t *) (cfg + 0x40) = 1;
      return;
    }
    Sleep (50);
    uint32_t goods = goods_in_use ();
    if (goods == seen_last) continue;
    seen_last = goods;
    *(uint32_t *) (cfg + 0x24) = goods;
    if (goods == 0) continue;
    if (goods != *(uint32_t *) (cfg + 0x18) &&
        goods != *(uint32_t *) (cfg + 0x1c) &&
        goods != *(uint32_t *) (cfg + 0x20)) continue;
    uint64_t session = *(uint64_t *) (cfg + 0x08);
    uint64_t owner_shim = *(uint64_t *) (cfg + 0x10);
    if (session == 0 || owner_shim == 0) { (*(uint32_t *) (cfg + 0x34))++; continue; }
    uint32_t before = *(uint32_t *) (session + 0x150);
    if (before != 0x01) { (*(uint32_t *) (cfg + 0x34))++; continue; }
    *(uint32_t *) (cfg + 0x2c) = before;
    invade ((void *) owner_shim);
    uint32_t after = *(uint32_t *) (session + 0x150);
    *(uint32_t *) (cfg + 0x30) = after;
    (*(uint32_t *) (cfg + 0x28))++;
  }
}
`, {
    Sleep: k32.getExportByName('Sleep'),
    invade: ersc.base.add(INVADE_RVA),
    cfg: cfg,
  });

  let reportedDrives = 0;
  let reportedGoods = -1;
  let reportedRefusals = 0;
  function reportWorker () {
    // What the worker SEES, not just what it did. Four uses in a row produced no drive and no
    // refusal line, which leaves three indistinguishable possibilities -- the C read never returns
    // a finger, it returns one but the session is missing, or it returns one and the state is not
    // idle. The worker records all three; this prints them.
    const seen = cfg.add(0x24).readU32();
    if (seen !== reportedGoods) {
      reportedGoods = seen;
      console.log(
        `finger: worker saw goods ${seen}` +
          (FINGERS[seen] !== undefined ? ` (${FINGERS[seen]})` : seen === 0 ? ' (nothing in use)' : ' (not a finger)')
      );
    }
    const refusals = cfg.add(0x34).readU32();
    if (refusals !== reportedRefusals) {
      reportedRefusals = refusals;
      const session = cfg.add(0x08).readPointer();
      const state = session.isNull() ? 'no session' : `state 0x${session.add(0x150).readU32().toString(16)}`;
      console.log(`finger: worker refusing (#${refusals}) -- session ${session}, ${state}`);
    }
    const drives = cfg.add(0x28).readU32();
    if (drives === reportedDrives) {
      return;
    }
    reportedDrives = drives;
    const before = cfg.add(0x2c).readU32();
    const after = cfg.add(0x30).readU32();
    console.log(
      `finger: drove the Lynchpin action (#${drives}) -- session state ` +
        `0x${before.toString(16)} -> 0x${after.toString(16)}` +
        (after === STATE_SEARCHING ? '  SEARCHING' : '  (did not reach searching)')
    );
  }

  // No scanned session is ever handed to the worker. Six shape signatures have been beaten in a
  // row, and the last one -- 0x4227aa24, which this scan produced -- parked the worker inside
  // `ersc+0x25850` after passing every check. A guess that parks is worse than no guess at all,
  // because the parked thread holds Seamless's lock for the rest of the session.
  //
  // The only object this drives is one `ersc.dll` itself passed to its invade action, captured by
  // the hook above. Until the Challenger's Lynchpin has been used once, there is nothing to drive
  // and the worker refuses, which it records in its refusal counter.
  let waitSaid = false;
  let armSaid = false;
  function giveWorkerASession () {
    const captured = cfg.add(0x08).readPointer();
    if (captured.isNull()) {
      // Said once: this runs from the param-lookup tick, which repeats.
      if (waitSaid) {
        return;
      }
      waitSaid = true;
      console.log(
        'finger: no session yet -- use the Challenger\'s Lynchpin ONCE so Seamless hands its own ' +
          'object through ersc+0x25850, then the fingers will drive it'
      );
      return;
    }
    if (armSaid) {
      return;
    }
    armSaid = true;
    console.log(`finger: worker armed with the captured session ${captured}`);
  }

  let reEnabled = false;
  let inside = false;
  let lookups = 0;
  let lastGoods = null;

  function tick () {
    if (!reEnabled) {
      const cleared = reEnableFingers();
      if (cleared >= 0) {
        reEnabled = true;
        console.log(`finger: ${cleared} row(s) re-enabled -- use a finger now`);
      }
    }
    // Detection lives in the worker; this tick does the two things that need a thread the game
    // already owns -- re-enabling the rows through the engine's own param lookup, handing the
    // worker a session, and reading back what it did.
    giveWorkerASession();
    reportWorker();
  }

  Interceptor.attach(EQUIP_PARAM_GOODS_GET_ENTRY, {
    onEnter () {
      // This agent calls the same function, so a re-entrant frame must not drive anything.
      if (inside) return;
      lookups += 1;
      if (lookups === 1) {
        console.log('finger: EquipParamGoods::GetEntry reached this agent -- the tick is live');
      }
      inside = true;
      try {
        tick();
      } finally {
        inside = false;
      }
    },
  });

  // Count Seamless's lobby calls in the same attach, so one finger use answers both questions.
  //
  // The open question after a successful drive is whether Seamless then asks Steam for anything.
  // Watching that in a separate attach is not an option -- two concurrent attaches silently break
  // the newer one's hooks -- and a separate run cannot be correlated with this use. The slots are
  // read from the interface `ersc.dll` itself holds at `ersc+0x21b610`, not from one resolved here,
  // because a wrapper of our own would be a different object and the count would mean nothing.
  const SLOTS = { RequestLobbyList: 4, AddRequestLobbyListStringFilter: 5, CreateLobby: 13, JoinLobby: 14 };
  const ERSC_MATCHMAKING_SLOT = 0x21b610;
  try {
    const iface = ersc.base.add(ERSC_MATCHMAKING_SLOT).readPointer();
    const vtable = iface.readPointer();
    for (const [name, slot] of Object.entries(SLOTS)) {
      let n = 0;
      const fn = vtable.add(slot * Process.pointerSize).readPointer();
      Interceptor.attach(fn, {
        onEnter () {
          n += 1;
          if (n <= 2) {
            console.log(`finger: Seamless called ${name} #${n}`);
          }
        },
      });
    }
    console.log(`finger: watching 4 lobby slot(s) on ersc's own interface ${iface}`);
  } catch (e) {
    console.log(`finger: could not watch the lobby slots: ${e.message}`);
  }

  // The worker is started LAST, on purpose.
  //
  // Created mid-script it hard-stopped the load: its first iteration calls back into JS, the loader
  // still held the runtime lock, and the script never reached its remaining statements -- measured
  // 2026-09-16, where `watching 4 lobby slot(s)` and this line never printed while a direct attach
  // printed both. Starting it after the last statement leaves no JS work outstanding for it to
  // contend with.
  // Both of these used to run only from the `GetEntry` hook, which is sparse: measured
  // 2026-09-16, it did not fire once in 27 seconds, so the worker ran the whole time with a null
  // session and could never have driven anything, and nothing could report that it hadn't.
  // Neither needs a game thread -- the scan is a read and the counters are our own memory.
  giveWorkerASession();
  reportWorker();
  // One worker per process, ever.
  if (workerAlreadyRunning) {
    console.log('finger: a worker from an earlier attach is still running -- reusing it');
  } else {
    const worker = CreateThread(NULL, 0, cm.watch, NULL, 0, NULL);
    console.log(`finger: worker thread ${worker} created`);
  }
  // Let Seamless hand over its own object, instead of recognising one.
  //
  // Every session this agent has scanned for has been a lookalike, and the last one parked the
  // worker inside `ersc+0x25850` after passing every check: the counters read
  // `saw goods 112, drives 0, refusals 0, state 0x1 -> 0x0`, which is the call entered and never
  // returned. Six shape signatures have now lost in a row.
  //
  // `ersc+0x25850` takes the owner as `rcx` and reads `[rcx+0x58]` as the session. When the player
  // uses the Challenger's Lynchpin, Seamless calls it with the real one. Hooking that entry is a
  // trampoline over the prologue -- which is exactly what must never be done while
  // `er_invasion_warp.dll` is loaded, because it byte-checks those bytes before every call. No DLL
  // of ours is in this process, so the objection does not apply here and the capture is free.
  // The ordinary capture: Seamless's option-menu builder, which runs for ANY of its dialogs.
  //
  // Capturing from the invade action alone would mean using the Challenger's Lynchpin before every
  // finger, which is scaffolding for a diagnostic and not a product. `ersc+0x241a0` is Seamless's
  // `show`, and its `rcx` IS the option-menu object -- `er_invasion_warp.dll` reaches the same
  // value through `lea r14,[rcx+0x120]` at ersc+0x241cb and logged `osm=0x466ad518` beside
  // `r14=0x466ad638` on a live run. That object's `+0x58` is the session the invade action reads.
  //
  // So any Seamless menu the player opens arms this, and the invade hook below is only a second
  // chance.
  const SHOW_RVA = 0x241a0;
  Interceptor.attach(ersc.base.add(SHOW_RVA), {
    onEnter (args) {
      if (!cfg.add(0x08).readPointer().isNull()) {
        return;
      }
      const osm = args[0];
      let session = NULL;
      try {
        session = osm.add(OWNER_SESSION_OFFSET).readPointer();
      } catch (e) {
        return;
      }
      if (session.isNull() || !lockIsReal(session)) {
        return;
      }
      cfg.add(0x08).writePointer(session);
      cfg.add(0x10).writePointer(osm);
      console.log(
        `finger: CAPTURED from Seamless's menu -- osm ${osm}, session ${session}, ` +
          `state 0x${session.add(SESSION_STATE_OFFSET).readU32().toString(16)}`
      );
    },
  });

  Interceptor.attach(ersc.base.add(INVADE_RVA), {
    onEnter (args) {
      const ownerFromErsc = args[0];
      let sessionFromErsc = NULL;
      try {
        sessionFromErsc = ownerFromErsc.add(OWNER_SESSION_OFFSET).readPointer();
      } catch (e) {
        return;
      }
      if (sessionFromErsc.isNull()) {
        return;
      }
      const known = cfg.add(0x08).readPointer();
      if (known.equals(sessionFromErsc)) {
        return;
      }
      cfg.add(0x08).writePointer(sessionFromErsc);
      cfg.add(0x10).writePointer(ownerFromErsc);
      console.log(
        `finger: CAPTURED from Seamless -- owner ${ownerFromErsc}, session ${sessionFromErsc}, ` +
          `state 0x${sessionFromErsc.add(SESSION_STATE_OFFSET).readU32().toString(16)}. ` +
          'Every finger from here drives that object, not a scanned one.'
      );
    },
  });

  // No settle here any more. A blocking `Sleep` on the loader's thread was enough to push the
  // script load past frida's transport timeout once the CModule and two capture hooks joined it --
  // the watcher died with `TransportError: timeout was reached` and the player's finger use met no
  // hooks at all. Counters are read on the next param-lookup tick instead.
  console.log('finger: armed -- use a Bloody, Festering Bloody or Recusant Finger');
}
