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

function goodsInUse () {
  try {
    const world = GLOBAL_WORLD_CHR_MAN.readPointer();
    if (world.isNull()) return null;
    const player = world.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
    if (player.isNull()) return null;
    const raw = player.add(CHR_INS_CONFIRMED_USED_GOODS).readU32();
    if ((raw & ITEM_CATEGORY_MASK) !== ITEM_CATEGORY_GOODS) return null;
    return raw & MAX_GOODS_ID;
  } catch (e) {
    return null;
  }
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
function playerIsInWorld () {
  try {
    const world = GLOBAL_WORLD_CHR_MAN.readPointer();
    if (world.isNull()) return false;
    const player = world.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
    if (player.isNull()) return false;
    // A real `ChrIns` has a game vtable; a stale slot does not.
    return Process.findModuleByAddress(player.readPointer()) !== null;
  } catch (e) {
    return false;
  }
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

  Interceptor.attach(OPEN_CHOICES, {
    onEnter () {
      const goods = goodsInUse();
      if (goods === null || FINGERS[goods] === undefined) return;
      armed = goods;
      console.log(`finger: ${FINGERS[goods]} (row ${goods}) opened its popup -- armed`);
    },
  });

  // The deferred driver is `EquipParamGoods::GetEntry`, not a render-side update.
  //
  // `CS::FeSystemAnnounceView::Update` was the obvious per-frame choice and it is not hookable here:
  // an `Interceptor` on `0x1408c47c0` terminated the game with `0xc0000005 rip=0x1408c47c7`, seven
  // bytes in, on 2026-09-16. `er_invasion_warp.dll` reaches the same function through `er-hook`'s
  // bare detour, which relocates the prologue Frida could not.
  //
  // This one is already proven: the same address is CALLED by this agent to read param rows, the
  // game calls it constantly on its own thread while menus and items are in play, and it is far
  // from the popup's thread -- which is the whole point, since driving the invade from there hard
  // locked the game twice.
  // The deferred driver is `EquipParamGoods::GetEntry`, not a render-side update.
  //
  // `CS::FeSystemAnnounceView::Update` was the obvious per-frame choice and it is not hookable here:
  // an `Interceptor` on `0x1408c47c0` terminated the game with `0xc0000005 rip=0x1408c47c7`, seven
  // bytes in, on 2026-09-16. `er_invasion_warp.dll` reaches the same function through `er-hook`'s
  // bare detour, which relocates a prologue Frida could not.
  //
  // This one is already proven: the agent CALLS the same address to read param rows, the game calls
  // it on its own thread while menus and items are in play, and it is nowhere near the popup's
  // thread -- which matters, because driving the invade from there hard locked the game twice.
  let reEnabled = false;
  let inside = false;

  function tick () {
    if (!reEnabled) {
      const cleared = reEnableFingers();
      if (cleared >= 0) {
        reEnabled = true;
        console.log(`finger: ${cleared} row(s) re-enabled -- use a finger now`);
      }
    }
    if (armed === null) return;
    const goods = armed;
    armed = null;
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

  Interceptor.attach(EQUIP_PARAM_GOODS_GET_ENTRY, {
    onEnter () {
      // This agent calls the same function, so a re-entrant frame must not drive anything.
      if (inside) return;
      inside = true;
      try {
        tick();
      } finally {
        inside = false;
      }
    },
  });

  console.log('finger: armed -- re-enabling as soon as the world is up');
}
