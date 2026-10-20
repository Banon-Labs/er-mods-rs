// Drive both paths of er-seamless-bugfixes' GiveItems mid hooks the way Seamless reaches them
// (2026-10-19): from inside CS::MapItemManImpl::PacketReceive, on the game's own thread, with the
// MapItemManImpl the game passes there.
//
//   1. With a local player present: one GiveItems of 150 Golden Rune [1] (goods 2900, held cap
//      `maxNum` 99) with item_pickup_type -1. The 51 that do not fit take the storage-overflow
//      branch, and the negative pickup type takes the pickup-log branch, so both mid hooks run
//      their normal path. The probes after each 16-byte window prove the trampoline ran the moved
//      call and came back.
//   2. Then it waits for WorldChrMan->mainPlayerIns to read null (a loading screen) and makes the
//      same call. That is the crash the tester hit; both hooks should divert, and the DLL logs
//      `DIVERTED` lines. If the pointer never reads null, that is measured too.
//
// Offsets are 1.17.1. Golden Runes are the only thing it gives, once per phase.
'use strict';

const game = Process.getModuleByName('eldenring.exe');
const at = (rva) => game.base.add(rva);
const WORLD_CHR_MAN = 0x3d69ff8;
const MAIN_PLAYER = 0x1e508;
const PACKET_RECEIVE = 0x560eb0;
const GIVE_ITEMS = 0x561400;
const GOODS_CATEGORY = 0x40000000;
// Run 2 (2026-10-19): one Golden Rune [2], which fits, so only the pickup-log branch is reached;
// run 1 gave 150 Golden Rune [1] at the 99 cap and reached only the storage branch.
const GOLDEN_RUNE_1 = 2901;
const QUANTITY = 1;

const out = new File(game.path.replace(/[^\\/]*$/, '') + 'giveitems-midhook-drive.log', 'a');
const log = (event) => {
    send(event);
    out.write(`[${new Date().toISOString()}] ${JSON.stringify(event)}\n`);
    out.flush();
};

// `traps: 'all'` so the probes below fire for this call: without it Frida does not run hook
// callbacks for code entered from inside another hook's callback, which is where this call is made.
const giveItems = new NativeFunction(at(GIVE_ITEMS), 'void', ['pointer', 'pointer', 'pointer', 'int'],
    { abi: 'win64', traps: 'all' });

// ItemLotSpawnDataList: u32 count, then 16-byte entries {itemId, quantity, upgrade, gemId}
// (GiveItems reads the first entry's upgrade at list+0xc). ItemLotSpawnDataStatus: +0 pickup
// type (negative -> the pickup-log branch), +4 giver's player id, +8 character id.
const list = Memory.alloc(0x100);
const status = Memory.alloc(0x40);
function fill() {
    list.writeByteArray(new Array(0x100).fill(0));
    list.writeU32(1);
    list.add(4).writeU32(GOODS_CATEGORY | GOLDEN_RUNE_1);
    list.add(8).writeU32(QUANTITY);
    list.add(0xc).writeS32(0);
    list.add(0x10).writeS32(-1);
    status.writeByteArray(new Array(0x40).fill(0));
    status.writeS32(-1);
}

function mainPlayer() {
    const wcm = at(WORLD_CHR_MAN).readPointer();
    return wcm.isNull() ? null : wcm.add(MAIN_PLAYER).readPointer();
}

const counts = { storage: 0, pickupLog: 0 };
for (const [name, off] of [['storage', 0x1d9 + 16], ['pickupLog', 0x34a + 16]]) {
    Interceptor.attach(at(GIVE_ITEMS + off), {
        onEnter() {
            counts[name] += 1;
            log({ kind: 'after_window', site: name, player_game_data: this.context.rax.toString(), counts });
        },
    });
}

// Where the first pass went: the item-id check, how much fit, and the leftover the storage branch
// tests. Only logged while one of this agent's calls is on the stack.
let driving = false;
for (const [name, off, read] of [
    ['item_id_exists', 0x0f2, (c) => c.rax.and(0xff).toInt32()],
    ['fits_in_inventory', 0x10d, (c) => c.rsp.add(0x50).readS32()],
    // +0x197 is `TEST EBX,EBX` after `SUB EBX,R14D`; +0x196 is inside that SUB, and an Interceptor
    // there corrupted the instruction (illegal instruction, 2026-10-19).
    ['leftover', 0x197, (c) => c.rbx.toInt32()],
]) {
    Interceptor.attach(at(GIVE_ITEMS + off), {
        onEnter() {
            if (driving) log({ kind: 'trace', at: name, value: read(this.context) });
        },
    });
}

// The with-player phase ran on 2026-10-19 and the user received the Golden Runes, so it is skipped:
// the only give left is the one made while the local player is null.
// The loading screen is started here rather than waited for (user, 2026-10-19: "trigger a warp to
// get a loading screen yourself so you can time it exactly"): the game's own
// TriggerAreaReload(false), 1.17.1 0x1405f36e0, called once from the frame tick as
// scripts/frida/area-reload.js does. The give then fires on the first PacketReceive or frame tick
// that sees mainPlayerIns null, with the MapItemManImpl PacketReceive was last handed.
const FRAME_TICK = 0x773900;
const TRIGGER_AREA_RELOAD = 0x5f36e0;
const triggerAreaReload = new NativeFunction(at(TRIGGER_AREA_RELOAD), 'void', ['uint8'], 'win64');

// Measured 2026-10-19: TriggerAreaReload(false) swaps mainPlayerIns (0x30340080 -> 0x3034f080)
// without it ever reading null in 30 s, so a reload does not produce the tester's state. The
// 'force_null' phase produces it directly: inside PacketReceive, on the game thread, it clears
// WorldChrMan+0x1e508 for the length of one GiveItems call and restores it straight after, so the
// mid hooks see exactly what they guard against.
let phase = 'force_null';
let mapItemMan = null;

function forcedNullGive(mim) {
    const wcm = at(WORLD_CHR_MAN).readPointer();
    if (wcm.isNull()) return;
    const slot = wcm.add(MAIN_PLAYER);
    const saved = slot.readPointer();
    if (saved.isNull()) return;
    phase = 'done';
    fill();
    log({ kind: 'give', phase: 'forced_null_player', saved: saved.toString() });
    slot.writePointer(ptr(0));
    try {
        giveItems(mim, list, status, 1);
    } finally {
        slot.writePointer(saved);
    }
    log({ kind: 'returned', phase: 'forced_null_player', restored: slot.readPointer().toString() });
}

function nullGive(where) {
    const player = mainPlayer();
    if (phase !== 'await_null' || mapItemMan === null || player === null || !player.isNull()) return;
    phase = 'done';
    fill();
    log({ kind: 'give', phase: 'null_player', from: where });
    driving = true;
    giveItems(mapItemMan, list, status, 1);
    driving = false;
    log({ kind: 'returned', phase: 'null_player', counts });
}

Interceptor.attach(at(PACKET_RECEIVE), {
    onEnter(args) {
        mapItemMan = args[0];
        if (phase === 'force_null') {
            forcedNullGive(args[0]);
            return;
        }
        nullGive('packet_receive');
    },
});

Interceptor.attach(at(FRAME_TICK), {
    onEnter() {
        if (phase === 'await_mim' && mapItemMan !== null) {
            phase = 'await_null';
            log({ kind: 'reload', map_item_man: mapItemMan.toString(), player: String(mainPlayer()) });
            triggerAreaReload(0);
            return;
        }
        nullGive('frame_tick');
    },
});

log({ kind: 'armed', packet_receive: at(PACKET_RECEIVE).toString(), give_items: at(GIVE_ITEMS).toString() });
