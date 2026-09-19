// Drive a goods item by id with no DLL of ours loaded.
//
// This is the port the standing directive asks for: "absolutely not. You're obligated to do it
// with Frida. The entire point is that dll has too many side effects." Every invasion experiment
// wants `er_invasion_warp.dll` withheld, because that module is the thing under suspicion -- and
// it is also the only condition under which `ersc.dll` may be hooked at all, since it byte-checks
// those prologues before every call and a trampoline disarms it. So the item drive has to live
// here instead of behind an export.
//
// # What actually gets an item to the character
//
// Re-stamping `menuGaitemUseState` every frame does not work and never did: nothing orders a game
// task against the reader, so a store landing after the reader has run that frame is invisible to
// it. The reader is `CS::PlayerIns::GetSelectedQuickSlotItemId`, and answering IT removes ordering
// from the problem. Measured across two runs: the struct stamp alone moved nothing in
// `ChrIns+0x150..0x180` for either the Lynchpin or the Festering Bloody Finger; the detour put
// `0x4000006f` into `ChrIns+0x160` on the first drive.
//
// All timing lives in the caller. There are no timers in this file on purpose -- the repo's
// `scripts/check-no-timeouts.py` bans them in scripts, and a hook-driven agent has no business
// owning a clock anyway.
//
// Addresses are 1.17.1, taken from er-invasion-warp's own `ADDRESS TRANSLATED` log lines rather
// than from a 1.16.2 constant plus arithmetic.
const CS_MENU_MAN_GLOBAL = ptr('0x143d6f820');
const CS_MENU_MAN_MENU_DATA = 0x8;
const MENU_GAITEM_USE_STATE = 0x70;
const USE_STATE = 0x8;
const USE_ITEM_ID = 0xc;
const USE_ITEM_IDX = 0x10;
const USE_ARG = 0x14;

const GET_SELECTED_QUICK_SLOT_ITEM_ID = ptr('0x140657410');
const GET_ITEM_INVENTORY_IDX = ptr('0x14024c560');

// `GameMan+0xbc8` is `isInOnlineMode`. Seamless keeps it clear, and `CanUseGoods` refuses every
// vanilla multiplayer item while it is -- so the module under test raises it for the duration of a
// finger use and puts it back. Clearing `disableOffline` on the row is the other half of the same
// refusal; do both, because the working drive does both.
const GAME_MAN = ptr('0x143d6d988');
const GAME_MAN_IS_IN_ONLINE_MODE = 0xbc8;

const GAME_DATA_MAN = ptr('0x143d61f98');
const GAME_DATA_MAN_PLAYER_GAME_DATA = 0x8;

// `PlayerGameData::chr_type`, asserted at `0x98` by `crates/er-game-base/src/pgd.rs`.
const PLAYER_GAME_DATA_CHR_TYPE = 0x98;
// `ChrType::Local`: alone in your own world, or hosting one. Every other value is a role the
// engine handed you for somebody else's session.
const CHR_TYPE_LOCAL = 0;
const CHR_TYPE_NAMES = {
  0: 'Local', 1: 'WhitePhantom', 2: 'Duelist', 8: 'GrayPhantom', 13: 'Arena',
  15: 'BloodyFinger', 16: 'Recusant', 17: 'BluePhantom', 18: 'FesteringBloodyFinger',
};
// Both of these are EMBEDDED, so the chain is address arithmetic and not a dereference.
// `GetEquipInventoryData` is literally `lea rax,[rcx+0x158]; ret`. Dereferencing the first one
// hands back an image address and a quantity of zero for an item the character is holding.
const PLAYER_GAME_DATA_EQUIP_GAME_DATA = 0x2b0;
const EQUIP_INVENTORY_DATA = 0x158;

const WORLD_CHR_MAN = ptr('0x143d69ff8');
const MAIN_PLAYER = 0x1e508;
const CHR_INS_QUEUED_USE_ITEM = 0x160;

// `EquipParamGoods::GetEntry(out, goodsId)`, out = { int paramId; int pad; row* }. Bit 5 of the
// flags byte at row+0x48 is `disableOffline`, which is what greys the vanilla fingers out while
// Seamless keeps the game's own online flag clear.
const EQUIP_PARAM_GOODS_GET_ENTRY = ptr('0x140d3b5b0');
const GOODS_FLAGS = 0x48;
const DISABLE_OFFLINE_BIT = 1 << 5;
const GOODS_TAG = 0x40000000;

let pinnedItemId = 0;
let answered = 0;
// `goodsId -> the flags byte before this agent cleared `disableOffline`, so `restoreFingers` can
// put the param row back and leave the matchmaking pool where it found it.
const goodsFlagsWas = {};
let onlineModeWas = null;

function holdOnlineMode (raise) {
  try {
    const man = GAME_MAN.readPointer();
    if (man.isNull()) return null;
    const at = man.add(GAME_MAN_IS_IN_ONLINE_MODE);
    if (raise) {
      if (onlineModeWas === null) onlineModeWas = at.readU8();
      at.writeU8(1);
      return onlineModeWas;
    }
    if (onlineModeWas !== null) { at.writeU8(onlineModeWas); onlineModeWas = null; }
    return null;
  } catch (e) { return null; }
}

// Run the original first so its pouch-slot work still happens, then overwrite the answer. The
// function's own prologue writes `*out = -1` (`c7 02 ff ff ff ff`), so replacing it outright
// would drop whatever else it does.
Interceptor.attach(GET_SELECTED_QUICK_SLOT_ITEM_ID, {
  onEnter (args) { this.out = args[1]; },
  onLeave () {
    if (pinnedItemId === 0 || this.out === undefined || this.out.isNull()) return;
    try { this.out.writeS32(pinnedItemId); answered += 1; } catch (e) { /* fault-closed */ }
  },
});

function useStateStruct () {
  const man = CS_MENU_MAN_GLOBAL.readPointer();
  if (man.isNull()) return null;
  const data = man.add(CS_MENU_MAN_MENU_DATA).readPointer();
  if (data.isNull()) return null;
  return data.add(MENU_GAITEM_USE_STATE);
}

function inventoryIndex (itemId) {
  // `this` is the equip inventory data, not the player -- passing the player faults inside the
  // lookup. And the id argument is a pointer to the tagged id: passing it by value returns -1 for
  // every row, which reads exactly like an empty inventory and has cost two wrong measurements.
  const man = GAME_DATA_MAN.readPointer();
  if (man.isNull()) return -1;
  const playerGameData = man.add(GAME_DATA_MAN_PLAYER_GAME_DATA).readPointer();
  if (playerGameData.isNull()) return -1;
  const inventory = playerGameData.add(PLAYER_GAME_DATA_EQUIP_GAME_DATA).add(EQUIP_INVENTORY_DATA);
  const holder = Memory.alloc(4);
  holder.writeU32(itemId >>> 0);
  const fn = new NativeFunction(GET_ITEM_INVENTORY_IDX, 'int', ['pointer', 'pointer']);
  return fn(inventory, holder);
}

// `CS::EquipGameData::SetQuickSlotItem(EquipGameData*, slot, _, inventoryIndex)`. The fourth
// argument is an INVENTORY INDEX, not the 0x40000000-tagged id: `GetQuickSlotIndexByInventoryIndex`
// walks `quickSlotEntries[i].index` against an inventory index, so feeding it a param id EMPTIES
// the slot instead of filling it. That mistake once cleared all ten of the user's slots.
const SET_QUICK_SLOT_ITEM = ptr('0x140249a30');
// `CS::EquipGameData::GetItemIdByQuickSlotIndex(EquipGameData*, int *out, uint slot)` -- reports
// tagged ids while the setter takes inventory indices, so putting a slot back means converting.
const GET_ITEM_ID_BY_QUICK_SLOT_INDEX = ptr('0x140247ee0');
const QUICK_SLOT_COUNT = 10;

let savedSlots = null;

function equipGameData () {
  const man = GAME_DATA_MAN.readPointer();
  if (man.isNull()) return null;
  const pgd = man.add(GAME_DATA_MAN_PLAYER_GAME_DATA).readPointer();
  if (pgd.isNull()) return null;
  // Embedded, not pointed to -- this is address arithmetic and not a dereference.
  return pgd.add(PLAYER_GAME_DATA_EQUIP_GAME_DATA);
}

// Read ALL ten before writing any. This module's Rust twin once wrote ten slots having saved one
// value, and there was nothing to put back; the user's quick slots had to be restored from disk.
function readAllSlots () {
  const equip = equipGameData();
  if (equip === null) return null;
  const fn = new NativeFunction(GET_ITEM_ID_BY_QUICK_SLOT_INDEX, 'pointer', ['pointer', 'pointer', 'uint']);
  const out = Memory.alloc(4);
  const slots = [];
  for (let slot = 0; slot < QUICK_SLOT_COUNT; slot++) {
    out.writeS32(-1);
    fn(equip, out, slot);
    slots.push(out.readS32());
  }
  return slots;
}

rpc.exports = {
  // The ten slots as the game reports them, so a cursor drive can count presses instead of
  // guessing at them. Reads only.
  readQuickSlots () {
    return readAllSlots();
  },

  // Put the item in a quick slot through the game's own setter, saving every slot first.
  //
  // This is what a synthetic answer to `GetSelectedQuickSlotItemId` cannot do. The Rust twin
  // measured it on run br-20260916-130933-5b28: the item queued and `ChrIns+0x168` stayed at zero
  // every time until the SLOT itself held it, and then it consumed on the first press. The consume
  // follows the equip system, so the equip system is what has to be told.
  equipQuickSlot (goodsId, slot) {
    const itemId = (GOODS_TAG | goodsId) >>> 0;
    const idx = inventoryIndex(itemId);
    if (idx < 0) return { ok: false, why: `item ${itemId.toString(16)} is not in the inventory` };
    const equip = equipGameData();
    if (equip === null) return { ok: false, why: 'EquipGameData is unreachable' };
    if (savedSlots === null) savedSlots = readAllSlots();
    if (savedSlots === null) return { ok: false, why: 'could not read the slots to save them' };
    const fn = new NativeFunction(SET_QUICK_SLOT_ITEM, 'void', ['pointer', 'uint', 'uint', 'uint']);
    fn(equip, slot, 0, idx);
    return { ok: true, slot, inventoryIndex: idx, saved: savedSlots, now: readAllSlots() };
  },

  // Put every slot back, converting saved ids to inventory indices the setter accepts.
  restoreQuickSlots () {
    if (savedSlots === null) return { ok: true, why: 'nothing was changed' };
    const equip = equipGameData();
    if (equip === null) return { ok: false, why: 'EquipGameData is unreachable' };
    const fn = new NativeFunction(SET_QUICK_SLOT_ITEM, 'void', ['pointer', 'uint', 'uint', 'uint']);
    for (let slot = 0; slot < savedSlots.length; slot++) {
      const id = savedSlots[slot] >>> 0;
      // An empty slot reads back as -1; restoring it means an index the setter treats as empty,
      // not a conversion of 0xffffffff.
      const idx = savedSlots[slot] === -1 ? 0xffffffff : inventoryIndex(id);
      fn(equip, slot, 0, idx >>> 0);
    }
    const now = readAllSlots();
    const restored = savedSlots;
    savedSlots = null;
    return { ok: true, restored, now };
  },

  // Whether the world exists yet. The launcher returns as soon as the DLL says it loaded, which
  // is seconds after boot and long before a player exists -- driving then makes `GetEntry` fault
  // and the inventory lookup answer -1, which reads exactly like an item the character does not
  // carry. Poll this first.
  ready () {
    try {
      const world = WORLD_CHR_MAN.readPointer();
      if (world.isNull()) return { ready: false, why: 'WorldChrMan is null' };
      const player = world.add(MAIN_PLAYER).readPointer();
      if (player.isNull()) return { ready: false, why: 'no main player' };
      if (Process.findModuleByAddress(player.readPointer()) === null) {
        return { ready: false, why: 'main player has no game vtable' };
      }
      const man = CS_MENU_MAN_GLOBAL.readPointer();
      if (man.isNull()) return { ready: false, why: 'CSMenuMan is null' };
      return { ready: true };
    } catch (e) { return { ready: false, why: 'faulted: ' + e.message }; }
  },

  // What the local player currently IS in the session, so a caller can refuse to drive an
  // invasion item into a session that is already under way.
  //
  // `ready()` is not this check and never was: it answers "a world exists", which is just as true
  // inside somebody else's world. On 2026-09-16 that gap let the Lynchpin be driven while the user
  // was a guest mid-invasion -- their words, "you're using an item to invade, while I'm in an
  // invasion lol".
  //
  // `PlayerGameData::chr_type` is the field, at `0x98`, which this repo asserts rather than
  // assumes (`crates/er-game-base/src/pgd.rs`: `offset_of!(PlayerGameData, chr_type) == 0x98`).
  // `0` is Local -- alone at home, or a host. Everything else is a multiplayer role the engine
  // gave you: 15/16/18 are the invader kinds, 1/8/17 the phantoms. The names come from
  // `crates/er-invasion-path/src/census.rs`, which built the list from a live session.
  //
  // A read that faults answers `null`, and a caller must treat that as a refusal too: not knowing
  // what you are is not permission to act.
  role () {
    try {
      const man = GAME_DATA_MAN.readPointer();
      if (man.isNull()) return null;
      const pgd = man.add(GAME_DATA_MAN_PLAYER_GAME_DATA).readPointer();
      if (pgd.isNull()) return null;
      const chrType = pgd.add(PLAYER_GAME_DATA_CHR_TYPE).readS32();
      return { chrType, name: CHR_TYPE_NAMES[chrType] || 'unknown', local: chrType === CHR_TYPE_LOCAL };
    } catch (e) { return null; }
  },

  // Clear `disableOffline` so `CanUseGoods` stops refusing the vanilla fingers.
  // Clearing `disableOffline` here writes a byte into a live `EquipParamGoods` row, and Seamless
  // hashes the loaded param tables into `lobby_key`, which Steam compares with
  // `k_ELobbyComparisonEqual`. So these three bytes silently move this client into a matchmaking
  // pool of one: measured 2026-09-18, a drive through here sent
  // `f89c2a507f99a522...` while the same build, same save and same config sent
  // `34154670c4dbf536...` when the player drove the item by hand -- and the hand-driven run landed
  // an invasion the driven ones could not, against a host publishing that same `34154670...`.
  // An entire evening went into blaming `map_pins`, other shells and Seamless's own re-derive
  // timer for a divergence this function was causing.
  //
  // `er_invasion_warp.dll` solves it without writing anything -- it answers `CanUseGoods` from a
  // detour and logs `no param byte written ... lobby_key is untouched`. Until this agent does the
  // same, the write is at least undone by `restoreFingers`, which the driver calls before it
  // returns, so the pool is only wrong for the seconds the drive is in flight.
  enableFingers (ids) {
    const fn = new NativeFunction(EQUIP_PARAM_GOODS_GET_ENTRY, 'pointer', ['pointer', 'int']);
    const out = Memory.alloc(16);
    const done = {};
    for (const id of ids) {
      try {
        fn(out, id);
        const row = out.add(8).readPointer();
        if (row.isNull()) { done[id] = 'no row'; continue; }
        const at = row.add(GOODS_FLAGS);
        const before = at.readU8();
        if (!(id in goodsFlagsWas)) goodsFlagsWas[id] = before;
        at.writeU8(before & ~DISABLE_OFFLINE_BIT);
        done[id] = { before, after: at.readU8() };
      } catch (e) { done[id] = 'faulted: ' + e.message; }
    }
    return done;
  },

  // Put every byte `enableFingers` cleared back, so the param fingerprint -- and with it the
  // matchmaking pool -- is the one the rest of the world is in once the drive is over.
  restoreFingers () {
    const fn = new NativeFunction(EQUIP_PARAM_GOODS_GET_ENTRY, 'pointer', ['pointer', 'int']);
    const out = Memory.alloc(16);
    const done = {};
    for (const id of Object.keys(goodsFlagsWas)) {
      const was = goodsFlagsWas[id];
      try {
        fn(out, parseInt(id, 10));
        const row = out.add(8).readPointer();
        if (row.isNull()) { done[id] = 'no row'; continue; }
        const at = row.add(GOODS_FLAGS);
        at.writeU8(was);
        done[id] = { restored: at.readU8(), wanted: was };
      } catch (e) { done[id] = 'faulted: ' + e.message; }
      delete goodsFlagsWas[id];
    }
    return done;
  },

  // Pin an item and request its use. The caller presses the use action afterwards.
  // `raiseOnline` defaults to true because a VANILLA multiplayer item needs it: `CanUseGoods`
  // refuses every one of them while `GameMan+0xbc8` is clear, which is the state Seamless keeps.
  //
  // It must be FALSE for Seamless's own items. Raising a flag Seamless deliberately holds clear is
  // a write to the black box under measurement, and a drive that makes it cannot distinguish "the
  // item did not open the popup" from "the popup was suppressed by the thing we changed". Same
  // reasoning as `enableFingers`, which clears `disableOffline` on a row -- correct for a vanilla
  // finger, and an uninvited edit to a Seamless item's own row.
  pin (goodsId, raiseOnline = true) {
    const itemId = (GOODS_TAG | goodsId) >>> 0;
    const idx = inventoryIndex(itemId);
    if (idx < 0) return { ok: false, why: `item ${itemId.toString(16)} is not in the inventory` };
    const st = useStateStruct();
    if (st === null) return { ok: false, why: 'menuGaitemUseState is unreachable' };
    pinnedItemId = itemId | 0;
    answered = 0;
    const onlineWas = raiseOnline ? holdOnlineMode(true) : null;
    st.add(USE_ITEM_ID).writeU32(itemId);
    st.add(USE_ITEM_IDX).writeU32(idx);
    st.add(USE_ARG).writeU32(0);
    // A byte, not a dword: 0x9..0xb are adjacent fields and a wider store clobbers them.
    st.add(USE_STATE).writeU8(1);
    return {
      ok: true,
      itemId: '0x' + itemId.toString(16),
      idx,
      onlineModeWas: onlineWas,
      raisedOnline: raiseOnline,
    };
  },

  // What the engine did with the request. State 2 is its action update latching it; roughly half
  // of all drives never reach 2, and a run whose control never latched is void, not negative.
  state () {
    const st = useStateStruct();
    const out = { answered };
    if (st !== null) {
      out.state = st.add(USE_STATE).readU8();
      out.itemId = '0x' + st.add(USE_ITEM_ID).readU32().toString(16);
      out.itemIdx = st.add(USE_ITEM_IDX).readU32();
    }
    try {
      const player = WORLD_CHR_MAN.readPointer().add(MAIN_PLAYER).readPointer();
      out.queuedUseItem = '0x' + player.add(CHR_INS_QUEUED_USE_ITEM).readU32().toString(16);
    } catch (e) { out.queuedUseItem = 'unreadable'; }
    return out;
  },

  // Put `ChrIns+0x160` back to empty so the next drive is not refused.
  //
  // The field is the queued use and it does NOT self-clear: after one drive it holds the tagged id
  // forever and the game declines every following use, which is what made this look unreproducible
  // and what the recipe answers with "relaunch between attempts". A relaunch costs two minutes and
  // a fresh world; this costs a store. `0xffffffff` is the empty value the field is read against
  // elsewhere in this repo, not a zero.
  clearQueue () {
    try {
      const player = WORLD_CHR_MAN.readPointer().add(MAIN_PLAYER).readPointer();
      const at = player.add(CHR_INS_QUEUED_USE_ITEM);
      const before = at.readU32();
      at.writeU32(0xffffffff);
      return { before: '0x' + before.toString(16), after: '0x' + at.readU32().toString(16) };
    } catch (e) { return { error: e.message }; }
  },

  // Clearing the request byte matters: a request the engine never latched stays at 1 and the
  // next `pin` writes into a struct that is already mid-request. The module under test writes 0
  // here for the same reason.
  unpin () {
    pinnedItemId = 0;
    holdOnlineMode(false);
    try {
      const st = useStateStruct();
      if (st !== null) st.add(USE_STATE).writeU8(0);
    } catch (e) { /* fault-closed */ }
    return { answered };
  },
};

console.log('lynchpin-item-drive: ready (quick-slot reader answered, no DLL export used)');
