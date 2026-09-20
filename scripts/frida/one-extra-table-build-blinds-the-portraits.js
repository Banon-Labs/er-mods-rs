// Does one extra call of the profile-renderer table builder leave a title whose portraits cannot
// draw? That is the only thing this repo does to that table which the game does not.
//
// # Why this is the experiment
//
// The user's own A/B, 2026-09-19: a quit-to-title after visiting the in-game Save Game menu drew
// no portraits, re-entering the list did not fix it, and re-issuing the game's own refresh through
// Frida did not fix it either -- while a quit-to-title without that visit drew them normally. So
// the request is not what is missing, and the renderers the refresh feeds are not the ones the
// screen is drawing.
//
// `profile_table_guard_body` calls the native table builder whenever it finds the table fully
// empty at the refresh's entry, which is exactly what an in-world ProfileSelect finds. The builder
// (`0x1409b05f0` on this build -- the address the shell's own log prints when it calls it) hands
// the previous ten renderers to `CSDelayDeleteMan` and allocates ten fresh `CSMenuProfModelRend`,
// each of which constructs its own `CSEzOffscreenRend` from a per-slot name in
// `DAT_143b39840 + slot * 0x20`. Two generations close together therefore claim the same ten named
// offscreen targets while the older set is still queued for deletion.
//
// The cheap way to test that is not to make the user replay the save-menu cycle. It is to make the
// extra build happen here, once, on a title that is drawing portraits correctly right now.
//
// # What it does, and what each outcome means
//
// Reads the ten offscreen targets, calls the builder once, reads them again, then calls the
// refresh so the new generation is asked for its portraits exactly as the game would ask.
//
//   * portraits stay on screen  -- one extra build is harmless and the cause is elsewhere;
//   * portraits go blank        -- the builder call is the cause, and the fix is in this repo's
//                                 guard rather than anywhere in the game.
//
// The pointer-level read is recorded either way, because a generation whose targets are null or
// repeat the previous generation's names the mechanism outright rather than merely implicating it.
'use strict';

const BUILDER_RVA = 0x9b05f0;
const REFRESH_RVA = 0x9ab820;
const RENDERER_TABLE_RVA = 0x3d71940;
const OFFSCREEN_REND_OFFSET = 0xa8;
const SLOT_COUNT = 10;

function hex(value) {
    return '0x' + value.toString(16);
}

function generation(table) {
    const slots = [];
    for (let slot = 0; slot < SLOT_COUNT; slot++) {
        const rend = table.add(slot * 8).readPointer();
        slots.push({
            slot,
            renderer: hex(rend),
            offscreen: rend.isNull() ? null : hex(rend.add(OFFSCREEN_REND_OFFSET).readPointer()),
        });
    }
    return slots;
}

function distinct(slots) {
    return new Set(slots.map((s) => s.offscreen).filter((o) => o !== null && o !== '0x0')).size;
}

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
    send({ kind: 'error', message: 'eldenring.exe not loaded' });
} else {
    const table = game.base.add(RENDERER_TABLE_RVA);
    const before = generation(table);
    send({ kind: 'before', distinct_offscreen: distinct(before), slots: before });

    const build = new NativeFunction(game.base.add(BUILDER_RVA), 'void', []);
    send({ kind: 'building', address: hex(game.base.add(BUILDER_RVA)) });
    build();

    const after = generation(table);
    send({ kind: 'after-build', distinct_offscreen: distinct(after), slots: after });

    const refresh = new NativeFunction(game.base.add(REFRESH_RVA), 'void', []);
    refresh();
    send({ kind: 'refreshed', address: hex(game.base.add(REFRESH_RVA)) });

    // A target the new generation shares with the old one is the collision stated as a fact rather
    // than as a story: the older renderers are queued for deletion and still hold the name.
    const older = new Set(before.map((s) => s.offscreen));
    send({
        kind: 'verdict',
        reused_offscreen: after.filter((s) => older.has(s.offscreen)).map((s) => s.slot),
        null_offscreen: after.filter((s) => s.offscreen === null || s.offscreen === '0x0')
            .map((s) => s.slot),
    });
}
