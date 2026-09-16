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

rpc.exports = {
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

  // Clear `disableOffline` so `CanUseGoods` stops refusing the vanilla fingers.
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
        at.writeU8(before & ~DISABLE_OFFLINE_BIT);
        done[id] = { before, after: at.readU8() };
      } catch (e) { done[id] = 'faulted: ' + e.message; }
    }
    return done;
  },

  // Pin an item and request its use. The caller presses the use action afterwards.
  pin (goodsId) {
    const itemId = (GOODS_TAG | goodsId) >>> 0;
    const idx = inventoryIndex(itemId);
    if (idx < 0) return { ok: false, why: `item ${itemId.toString(16)} is not in the inventory` };
    const st = useStateStruct();
    if (st === null) return { ok: false, why: 'menuGaitemUseState is unreachable' };
    pinnedItemId = itemId | 0;
    answered = 0;
    const onlineWas = holdOnlineMode(true);
    st.add(USE_ITEM_ID).writeU32(itemId);
    st.add(USE_ITEM_IDX).writeU32(idx);
    st.add(USE_ARG).writeU32(0);
    // A byte, not a dword: 0x9..0xb are adjacent fields and a wider store clobbers them.
    st.add(USE_STATE).writeU8(1);
    return { ok: true, itemId: '0x' + itemId.toString(16), idx, onlineModeWas: onlineWas };
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
