// Why the title's Load Game portraits are blank after a visit to the Save Game menu.
//
// # What the game does, read out of the 1.16.2 dump (`FUN_1409aa680`, 1.17 `+0x9ab820`)
//
//     ps = CS::GameDataMan::GetProfileSummary();
//     for (slot = 0; slot < 10; slot++) {
//       rend = PROFILE_RENDERER_TABLE[slot];                  // +0x3d71940 on 1.17
//       rec  = ps + 0x18 + slot * 0x2a0;                      // always non-null for slot < 10
//       if (ps != 0 && ps->saveSlotsStates[slot]              // FUN_140875750 -> FUN_140261cd0
//           && rend[0x754] == 0 && rend[0x755] == 0) {
//         ... push chr params, FaceData buffer, slot index ...
//         rend[0x754] = 1;                                    // FUN_140bb9810
//         rend[0x755] = 1;                                    // FUN_140bb9830
//       }
//     }
//
// So the portrait build is one-shot per open, armed by two bytes on each renderer, and gated on
// the container reporting the slot occupied. `CS::CSMenuAsmModelRend::STEP_Wait_Request` consumes
// `0x754` (sets it back to 0 and issues the request through vtable+0x30).
//
// # What the two bytes cannot tell you, measured by this agent on 2026-09-19
//
// An earlier draft of this file argued that a live `0x755 == 0` proved the build had never run,
// because no instruction in `eldenring-deobf.bin` writes zero to that offset -- searched as
// `c6 ?? 55 07 00 00 00`, `88 ?? 55 07 00 00`, `66 c7 ?? 54 07 ...` and the SIB forms, all zero
// hits, with the only dword clear at `0x140bb8232` sitting inside the `CSMenuAsmModelRend`
// constructor. That argument is false, and this run is what falsified it: the call below set both
// bytes to 1 on all ten renderers, and a read 30 seconds later had both back to 0 on objects that
// were never reconstructed (`+0x760` and `+0x768` still held the same two heap pointers). The
// clearer is reached through a base register the offset is folded into, so an absolute-offset byte
// search cannot see it.
//
// The consequence is the useful part: an idle renderer that has already drawn its portrait is byte
// identical here to one that was never asked. These two bytes discriminate nothing after the fact,
// in either direction, and the question they were reached for needs an instrument at the frame --
// a counter on `FUN_140bb9830` across the refresh, or the rendered pixels.
//
// # What was already measured on the broken session (pid 1944466, before this agent)
//
// `er-save-game-row.log` line 157, taken at the refresh's own entry by our guard detour:
//   `profileselect-table-refresh #5: caller_rva=0x81f97e valid_mask=0x3ff null_mask=0x0
//    slot0=0x2d79e480(+0x754=0x0) slot1=0x23139c80(+0x754=0x0)`
// and `/proc/<pid>/mem` afterwards: the renderer table still holds those same ten pointers, slot0
// and slot1 still read `0x754 == 0` and `0x755 == 0`, while the container at `0x90b41c80` reads
// `saveSlotsStates = 01 x10` with record 0 named `rl60 invader` and its `FACE` block intact.
//
// This agent reads the container and all ten renderers in one pass and then calls the game's own
// refresh, which is the one thing that re-issues a one-shot nothing else re-issues. Whether the
// portraits appear when it does is the discriminator the flags cannot supply: they appear if the
// request was simply missed, and they stay blank if the request is being issued and the model or
// the draw is what fails.
//
// # Why calling it is safe to do here
//
// It is the function the game calls when `TitleTopDialog` constructs, on a table this process just
// validated. Its writes publish in the right order: every field is written before `0x754`, and
// `0x754` is the byte the menu thread's step machine polls to pick the work up -- so arming last
// is the same publication order the game itself uses. No watchpoint, no `MemoryAccessMonitor`, no
// page protection change.
'use strict';

const REFRESH_RVA = 0x9ab820;
const GAME_DATA_MAN_GLOBAL_RVA = 0x3d61f98;
const RENDERER_TABLE_RVA = 0x3d71940;
const PROFILE_SUMMARY_OFFSET = 0x78;
const SLOT_STATES_OFFSET = 0x8;
const RECORD_BASE_OFFSET = 0x18;
const RECORD_STRIDE = 0x2a0;
const SLOT_COUNT = 10;
const REQUEST_FLAG = 0x754;
const BUILT_FLAG = 0x755;

function hex(ptr) {
    return '0x' + ptr.toString(16);
}

function readName(record) {
    try {
        const name = record.readUtf16String(16);
        return name === null ? '' : name;
    } catch (e) {
        return '<unreadable>';
    }
}

function snapshot(table, summary) {
    const slots = [];
    for (let slot = 0; slot < SLOT_COUNT; slot++) {
        const rend = table.add(slot * 8).readPointer();
        const entry = {
            slot,
            renderer: hex(rend),
            request: null,
            built: null,
            occupied: null,
            name: null,
        };
        if (!rend.isNull()) {
            entry.request = rend.add(REQUEST_FLAG).readU8();
            entry.built = rend.add(BUILT_FLAG).readU8();
        }
        if (!summary.isNull()) {
            entry.occupied = summary.add(SLOT_STATES_OFFSET + slot).readU8();
            entry.name = readName(summary.add(RECORD_BASE_OFFSET + slot * RECORD_STRIDE));
        }
        slots.push(entry);
    }
    return slots;
}

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
    send({ kind: 'error', message: 'eldenring.exe not loaded' });
} else {
    const table = game.base.add(RENDERER_TABLE_RVA);
    const gameDataMan = game.base.add(GAME_DATA_MAN_GLOBAL_RVA).readPointer();
    const summary = gameDataMan.isNull()
        ? ptr(0)
        : gameDataMan.add(PROFILE_SUMMARY_OFFSET).readPointer();

    send({
        kind: 'hello',
        base: hex(game.base),
        table: hex(table),
        game_data_man: hex(gameDataMan),
        profile_summary: hex(summary),
    });

    const before = snapshot(table, summary);
    send({ kind: 'before', slots: before });

    // The verdict this agent exists for, stated before anything is called: a renderer whose `built`
    // byte is zero while its slot reads occupied is a portrait the refresh was asked for and never
    // requested.
    const missed = before.filter((s) => s.occupied === 1 && s.built === 0);
    send({
        kind: 'verdict',
        occupied_slots: before.filter((s) => s.occupied === 1).length,
        never_requested: missed.length,
        slots_never_requested: missed.map((s) => s.slot),
    });

    if (missed.length > 0) {
        const refresh = new NativeFunction(game.base.add(REFRESH_RVA), 'void', []);
        send({ kind: 'calling-refresh', address: hex(game.base.add(REFRESH_RVA)) });
        refresh();
        const after = snapshot(table, summary);
        send({ kind: 'after', slots: after });
        send({
            kind: 'result',
            armed: after.filter((s) => s.built === 1).length,
            still_unarmed: after.filter((s) => s.occupied === 1 && s.built === 0).length,
        });
    }
}
