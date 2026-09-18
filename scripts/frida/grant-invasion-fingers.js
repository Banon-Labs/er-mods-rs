// Hand the player the three vanilla invasion fingers, through the engine's own inventory call.
//
// # Why this is not a written record and a signature-scanned routine
//
// The community `ItemGib` builds a 16-byte record array and calls a routine found by scanning.
// The game already exports the operation: `CS::EquipGameData::AddInventoryEquipByItemId` is what
// the engine itself uses, it mints the gaitem handle through `CSGaitemImp`, and it takes the same
// category-tagged id the goods rows carry. `er-build-import-runtime/src/grant.rs` uses exactly
// this call for the same reason, and this agent is that path with no build step in front of it.
//
// # The addresses, and why they are safe to use on the installed build
//
// Every one is a 1.16.2 address carried onto 1.17 by `scripts/map-rvas-1162-to-1170.py`, which
// reported delta +0x0 for all three, and each sits below the `0xafefe9` boundary where 1.17.1
// slides everything by +0x70 -- so 1.16.2, 1.17.0 and the installed 1.17.1 share them. Each was
// then read in `eldenring-deobf-1.17.1.bin` before being called, because a mapping is a candidate
// and calling a wrong address is worse than hooking one:
//
//   0x246840  push rdi / sub rsp,0x40 / movq [rsp+38],-2      a real frame
//   0x247b30  lea rax,[rcx+0x158] / ret                       the accessor it should be
//   0x24c1b0  push rsi / push rdi / push r14 / sub rsp,0x40    a real frame
//
// `GameDataMan` is a data global and data globals do move between 1.16.2 and 1.17, so it is not
// carried across -- it is read out of the 1.17.1 image directly, from the `mov rax,[rip+d]` at
// `CanMainPlayerUseGoods+0xd` (`0x14068ed30`, a pair this repo has already verified), which loads
// it and then takes `+0x8`. That instruction resolves to `0x143d61f98`.
//
// # Why the work happens inside a `PeekMessageW` hook
//
// It mutates the player's inventory and touches the `CSGaitemImp` singleton, so it has to run on
// the thread that owns them. A `NativeFunction` called from Frida's own thread races the game's
// inventory code, and this repo has already measured the sharper version of that failure: calling
// ERSC's invade from an RPC thread parked forever on the session mutex (2026-09-08). The game's
// main thread pumps `PeekMessageW` every frame, so the hook is a free ride onto it -- the same
// trick `scripts/frida/breakin-gate.js` uses.
//
// No detach. Detaching from inside the callback is not safe, and the `granted` latch makes every
// later frame a compare-and-return.
'use strict';

const GAME = 'eldenring.exe';

/// `GLOBAL_GameDataMan`, read off `CanMainPlayerUseGoods+0xd` in the 1.17.1 image.
const GAME_DATA_MAN_GLOBAL_RVA = 0x3d61f98;
/// `GameDataMan::mainPlayerGameData`.
const PLAYER_GAME_DATA_OFFSET = 0x08;
/// `PlayerGameData::equipGameData`, held by value -- so this is an address, not a pointer to read.
const EQUIP_GAME_DATA_OFFSET = 0x2b0;

/// `CS::EquipGameData::AddInventoryEquipByItemId(egd, int *itemId, u32 amount,
/// bool updateTrophyStats, bool updateAutoEquip) -> int`.
const ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA = 0x246840;
/// `CS::EquipGameData::GetEquipInventoryData(egd) -> EquipInventoryData*`.
const GET_EQUIP_INVENTORY_DATA_RVA = 0x247b30;
/// `CS::EquipInventoryData::GetQuantityByItemId(inv, int *itemId) -> int`.
const GET_QUANTITY_BY_ITEM_ID_RVA = 0x24c1b0;

/// The category nibble goods carry. Confirmed live rather than assumed: run
/// `br-20260918-192951-e31c` logged `selectedGoodsItemId=0x40000070` while the player held goods
/// 112, and `0x70` is 112.
const GOODS_CATEGORY = 0x40000000;

/// The three vanilla invasion items, with the goods rows `vanilla_invasion_items.rs` names.
///
/// The Bloody Finger is the consumable and the reason this exists, so it is asked for in bulk; the
/// other two are the reusable fingers and one each is all they can be. Over-asking is safe in
/// either direction -- `InsertItem` and `UpdateQuantity` both clamp with a bare
/// `if (max < amount) amount = max;` -- and the read-back below reports what the inventory
/// actually holds rather than what was requested, so a clamp is visible instead of silent.
const WANTED = [
    { row: 102, amount: 99, label: 'Bloody Finger (consumable)' },
    { row: 111, amount: 1, label: 'Festering Bloody Finger' },
    { row: 112, amount: 1, label: 'Recusant Finger' },
];

function grantAll() {
    const game = Process.findModuleByName(GAME);
    if (game === null) {
        send({ kind: 'module_missing', module: GAME });
        return;
    }

    const gameDataMan = game.base.add(GAME_DATA_MAN_GLOBAL_RVA).readPointer();
    if (gameDataMan.isNull()) {
        send({ kind: 'no_game_data_man' });
        return;
    }
    const playerGameData = gameDataMan.add(PLAYER_GAME_DATA_OFFSET).readPointer();
    // Null before a character is loaded, which is a refusal rather than an error: there is nobody
    // to give anything to yet, and the next frame asks again.
    if (playerGameData.isNull()) {
        return;
    }
    const equipGameData = playerGameData.add(EQUIP_GAME_DATA_OFFSET);

    // The two trailing `bool` arguments are declared `int` and passed `0`.
    //
    // Frida's `'bool'` marshaller rejected `false` with `TypeError: expected an integer` on the
    // first live run, and the ABI does not care: both are byte-wide MSVC `bool`s in register
    // arguments, and the callee tests them with `test r8b,r8b` either way. `grant.rs` gets to keep
    // its `bool` because Rust's `extern "system"` lowers it the same way.
    const addInventory = new NativeFunction(
        game.base.add(ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA),
        'int',
        ['pointer', 'pointer', 'uint32', 'int', 'int'],
    );
    const getInventory = new NativeFunction(
        game.base.add(GET_EQUIP_INVENTORY_DATA_RVA),
        'pointer',
        ['pointer'],
    );
    const getQuantity = new NativeFunction(
        game.base.add(GET_QUANTITY_BY_ITEM_ID_RVA),
        'int',
        ['pointer', 'pointer'],
    );

    const inventory = getInventory(equipGameData);
    const results = [];
    for (const item of WANTED) {
        const itemId = GOODS_CATEGORY + item.row;
        // The call takes a POINTER to the id, not the id: `AddInventoryEquipByItemId` reads it
        // through `GetGaitemHandleByItemId` and the read-back below reads the same cell.
        const cell = Memory.alloc(4);
        cell.writeS32(itemId);

        const before = inventory.isNull() ? -1 : getQuantity(inventory, cell);
        let returned = null;
        let threw = null;
        try {
            returned = addInventory(equipGameData, cell, item.amount, 0, 0);
        } catch (e) {
            threw = e.message;
        }
        // The return value is an int this agent does not pretend to understand, so it is reported
        // and not believed. The quantity is the evidence: a call that "succeeded" is not proof the
        // item exists, which is the same rule `grant.rs` follows.
        const after = inventory.isNull() ? -1 : getQuantity(inventory, cell);

        results.push({
            label: item.label,
            item_id: '0x' + (itemId >>> 0).toString(16),
            requested: item.amount,
            held_before: before,
            held_after: after,
            returned: returned,
            threw: threw,
        });
    }

    send({
        kind: 'granted',
        game_data_man: gameDataMan.toString(),
        equip_game_data: equipGameData.toString(),
        inventory: inventory.toString(),
        results: results,
    });
    for (const r of results) {
        console.log(
            `grant-invasion-fingers: ${r.label} ${r.item_id} -- held ${r.held_before} -> ` +
                `${r.held_after} (asked ${r.requested}, call returned ${r.returned}` +
                `${r.threw === null ? '' : `, THREW ${r.threw}`})`,
        );
    }
}

// One shot, on the game's own main thread, then the trampoline comes back out.
//
// `Module.getExportByName(module, name)` is gone in Frida 17 -- it throws `TypeError: not a
// function`, which is what this agent did on its first run. The per-module method replaces it, and
// `scripts/frida/er-request-invade.js` already uses that form. The older spelling is still tried
// as a fallback so this file keeps working if it is ever run against an older Frida.
function exportAddress(moduleName, exportName) {
    const owner = Process.findModuleByName(moduleName);
    if (owner !== null && typeof owner.getExportByName === 'function') {
        return owner.getExportByName(exportName);
    }
    if (typeof Module.getGlobalExportByName === 'function') {
        return Module.getGlobalExportByName(exportName);
    }
    return Module.getExportByName(moduleName, exportName);
}

const peek = exportAddress('user32.dll', 'PeekMessageW');
let granted = false;
Interceptor.attach(peek, {
    onEnter() {
        if (granted) {
            return;
        }
        const game = Process.findModuleByName(GAME);
        if (game === null) {
            return;
        }
        // The latch is taken before the work, not after: a throw inside `grantAll` must not leave
        // this re-entering on every frame for the rest of the session.
        granted = true;
        try {
            grantAll();
        } catch (e) {
            console.log(`grant-invasion-fingers: failed: ${e.message}`);
            send({ kind: 'failed', message: e.message });
        }
    },
});

send({ kind: 'ready' });
console.log('grant-invasion-fingers: armed -- granting on the next frame the game pumps');
