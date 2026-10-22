// Hand the player what the duel-rules referee's Rune Arc and Spirit Ash checks need to be seen
// live: Rune Arcs, Godrick's Great Rune equipped in the rune slot, the Spirit Calling Bell if it is
// missing, and Noble Sorcerer Ashes (11 FP, the cheapest summon in the regulation; Mimic Tear's
// 660 HP is more than a low-Vigor character has).
//
// Game addresses carried to 2.7.2.0 by scripts/frida-carry-1172.py; each moved literal keeps its
// old value in its comment.
//
// Granting is `grant-invasion-fingers.js`'s path: `CS::EquipGameData::AddInventoryEquipByItemId`,
// on the game thread inside its per-frame tick, each grant proven by the quantity read
// back rather than by the call's return value.
//
// Equipping the rune is `er-build-import-runtime/src/equip_native.rs`'s path, which equipped a
// Great Rune on a live game and read it back (bd build-import-COMPLETE-all-components-verified):
// find the rune's inventory index, take its gaitem handle, hand both to the quick/pouch/rune
// dispatcher at index 16 (`SetGreatRune`), run the post-write refresh, and ask
// `GetEquippedGreatrune(egd, &out, 0)` -- three arguments, the third is the slot.
//
// Every address is 1.16.2's, mapped onto 1.17 by `scripts/map-rvas-1162-to-1170.py` and below the
// `0xafefe9` boundary, so 1.17.1 has it at the same place.
'use strict';

const GAME = 'eldenring.exe';

const GAME_DATA_MAN_GLOBAL_RVA = 0x3d64fb8; // 1.17.1 0x3d61f98
const PLAYER_GAME_DATA_OFFSET = 0x08;
const EQUIP_GAME_DATA_OFFSET = 0x2b0;

/// `CS::EquipGameData::AddInventoryEquipByItemId(egd, int *itemId, u32 amount, bool, bool) -> int`.
const ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA = 0x246840;
/// `CS::EquipGameData::GetEquipInventoryData(egd) -> EquipInventoryData*`.
const GET_EQUIP_INVENTORY_DATA_RVA = 0x247b30;
/// `CS::EquipInventoryData::GetQuantityByItemId(inv, int *itemId) -> int`.
const GET_QUANTITY_BY_ITEM_ID_RVA = 0x24c1b0;
/// `EquipInventoryData::GetItemInventoryIdx(inv, int *itemId) -> int`, negative when not held.
const GET_ITEM_INVENTORY_IDX_RVA = 0x24c560;
/// `CS::EquipInventoryData::GetGaItemHandleByIndex(inv, uint *out, uint itemIdx) -> uint*`.
const GET_GAITEM_HANDLE_BY_INDEX_RVA = 0x24c7b0;
/// `FUN_140249a50(egd, index, uint *gaitemHandle, uint itemIdx)`: 0..9 quickbar, 10..15 pouch,
/// 16 great rune.
const SET_QUICK_OR_POUCH_OR_RUNE_RVA = 0x249a50;
const GREAT_RUNE_DISPATCH_INDEX = 16;
/// `FUN_140249a90(egd)`, the refresh the menu path runs after every equip write.
const EQUIP_REFRESH_RVA = 0x249a90;
/// `CS::EquipGameData::GetEquippedGreatrune(egd, int *out, int slot) -> int*`.
const GET_EQUIPPED_GREATRUNE_RVA = 0x247900;

const GOODS_CATEGORY = 0x40000000;
const RUNE_ARC = 190;
/// 191..196 are the restored runes `GetGreatruneEnumByGoodsId` accepts; 191 is Godrick's.
const GODRICKS_GREAT_RUNE = 191;
const SPIRIT_CALLING_BELL = 8158;
const NOBLE_SORCERER_ASHES = 241000;

const WANTED = [
    { row: RUNE_ARC, amount: 5, label: 'Rune Arc' },
    { row: GODRICKS_GREAT_RUNE, amount: 1, label: "Godrick's Great Rune" },
    { row: SPIRIT_CALLING_BELL, amount: 1, label: 'Spirit Calling Bell', onlyIfMissing: true },
    { row: NOBLE_SORCERER_ASHES, amount: 1, label: 'Noble Sorcerer Ashes' },
];

function cellOf(itemId) {
    const cell = Memory.alloc(4);
    cell.writeS32(itemId);
    return cell;
}

function run() {
    const game = Process.findModuleByName(GAME);
    const gameDataMan = game.base.add(GAME_DATA_MAN_GLOBAL_RVA).readPointer();
    if (gameDataMan.isNull()) return false;
    const playerGameData = gameDataMan.add(PLAYER_GAME_DATA_OFFSET).readPointer();
    if (playerGameData.isNull()) return false;
    const egd = playerGameData.add(EQUIP_GAME_DATA_OFFSET);
    const fn = (rva, ret, args) => new NativeFunction(game.base.add(rva), ret, args);
    // The trailing bools go as `int` 0: Frida's 'bool' marshaller rejects `false`
    // (grant-invasion-fingers.js), and the callee tests the low byte either way.
    const addInventory = fn(ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA, 'int', ['pointer', 'pointer', 'uint32', 'int', 'int']);
    const getInventory = fn(GET_EQUIP_INVENTORY_DATA_RVA, 'pointer', ['pointer']);
    const getQuantity = fn(GET_QUANTITY_BY_ITEM_ID_RVA, 'int', ['pointer', 'pointer']);
    const getIndex = fn(GET_ITEM_INVENTORY_IDX_RVA, 'int', ['pointer', 'pointer']);
    const getHandle = fn(GET_GAITEM_HANDLE_BY_INDEX_RVA, 'pointer', ['pointer', 'pointer', 'uint32']);
    const setRune = fn(SET_QUICK_OR_POUCH_OR_RUNE_RVA, 'void', ['pointer', 'uint32', 'pointer', 'uint32']);
    const refresh = fn(EQUIP_REFRESH_RVA, 'void', ['pointer']);
    const equippedRune = fn(GET_EQUIPPED_GREATRUNE_RVA, 'pointer', ['pointer', 'pointer', 'int']);

    const inventory = getInventory(egd);
    if (inventory.isNull()) return false;
    for (const item of WANTED) {
        const cell = cellOf(GOODS_CATEGORY + item.row);
        const before = getQuantity(inventory, cell);
        let returned = null;
        if (!(item.onlyIfMissing && before > 0)) returned = addInventory(egd, cell, item.amount, 0, 0);
        const after = getQuantity(inventory, cell);
        console.log(`grant-duel-test-kit: ${item.label} goods ${item.row} -- held ${before} -> ${after}` +
            `${returned === null ? ' (already held, not granted)' : ` (asked ${item.amount}, call returned ${returned})`}`);
    }

    const runeCell = cellOf(GOODS_CATEGORY + GODRICKS_GREAT_RUNE);
    const idx = getIndex(inventory, runeCell);
    if (idx < 0) {
        console.log(`grant-duel-test-kit: Godrick's Great Rune is not in the inventory (index ${idx}); not equipped`);
        return true;
    }
    const handle = Memory.alloc(8);
    getHandle(inventory, handle, idx);
    setRune(egd, GREAT_RUNE_DISPATCH_INDEX, handle, idx);
    refresh(egd);
    const out = Memory.alloc(4);
    equippedRune(egd, out, 0);
    const equipped = out.readS32() >>> 0;
    console.log(`grant-duel-test-kit: great rune slot now 0x${equipped.toString(16)} ` +
        `(wanted 0x${(GOODS_CATEGORY + GODRICKS_GREAT_RUNE).toString(16)}; inventory index ${idx}, ` +
        `handle 0x${(handle.readU32() >>> 0).toString(16)})`);
    return true;
}

// The game's per-frame tick, the hook er-duel-rules.js runs its checks in: it fires once per
// rendered frame on the game thread (measured 2026-10-16: 41 fires a second against 123
// `CSFlipper::current_frame_qpc` changes in 3 s). `PeekMessageW`, the hook
// grant-invasion-fingers.js uses, did not fire once in 25 s on this game.
const FRAME_TICK_RVA = 0x773db0; // 1.17.1 0x773900

let done = false;
Interceptor.attach(Process.findModuleByName(GAME).base.add(FRAME_TICK_RVA), {
    onEnter() {
        if (done) return;
        // Taken before the work: a throw must not re-run the grant every frame.
        done = true;
        try {
            if (!run()) {
                done = false;
            }
        } catch (e) {
            console.log(`grant-duel-test-kit: THREW ${e.message}`);
        }
    },
});
console.log('grant-duel-test-kit: armed; granting on the next frame tick');
