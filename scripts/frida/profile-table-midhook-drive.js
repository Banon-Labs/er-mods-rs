// Drive er-quit-menu-core's profile-renderer refresh mid hook (er-effects-rs-7xbs.2) from inside
// the refresh itself, on the game's own thread.
//
// The refresh (1.17.1 0x1409ab820 -> 2.7.2.0 0x1409abcd0) walks the ten-slot renderer table at
// 1.17.1 0x143d71940 -> 2.7.2.0 0x143d74960. The mid hook
// at +0x54 sends a null or non-renderer slot to the loop's own next-slot join at +0xce; the entry
// detour has already run by then and only diagnoses once the hook is armed. Two forced states:
//
//   1. one_null: on the first refresh that starts with all ten slots valid, null exactly one slot
//      (ONE_NULL_SLOT) after the entry detour has scanned the table, for the length of that one
//      refresh, and restore it at the epilogue. Every slot that reaches the per-slot draw path
//      (+0x66, the first instruction after the hooked window) is counted, and each slot's
//      +0x754 / +0x755 request latches are read before and after. Expected: the nulled slot never
//      reaches +0x66, the other slots reach it exactly as their profile records allow, their
//      latches are set afterwards, and the game does not fault.
//   2. all_null: SKIP_REFRESHES refreshes later, null all ten for one refresh. Expected: no slot
//      reaches +0x66 and the game does not fault.
//
// A slot whose latch is already set, or whose save slot has no profile record, is skipped by the
// refresh's own tests before +0x54; the before/after latches and the +0x66 counts let the reader
// tell that apart from a diversion. The DLL's own log line `PROFILESELECT-TABLE-GUARD PASS` and the
// mid hook's diversion count are the other half of the evidence.
//
// Safety: nulling a slot with the hook absent faults at +0x54, so the agent refuses to arm unless
// the bytes at +0x54 are a jump (ilhook's E9 or FF 25) instead of the original `80 BF 54 07`.
// Every nulled slot is restored at the epilogue and again in dispose(). No timers: phases advance
// on refreshes only.
//
// Addresses are 2.7.2.0, carried from 1.17.1 with scripts/map-rvas-1171-to-1172.py. The offsets
// into the refresh are unchanged: its whole body (0xf8 bytes) is the same in both images apart
// from rel32/rip displacements, and the slot reads +0x754/+0x755 are in that body.
'use strict';

const game = Process.getModuleByName('eldenring.exe');
const at = (rva) => game.base.add(rva);
// 1.17.1 0x9ab820 -> 2.7.2.0 0x9abcd0.
const REFRESH = 0x9abcd0;
// 1.17.1 0x3d71940 -> 2.7.2.0 0x3d74960; the refresh's own `LEA RSI,[table]` at +0x22 reads it in
// both images.
const TABLE = 0x3d74960;
const SLOT_COUNT = 10;
const LATCH = 0x754;
const PENDING = 0x755;
// +0x22 `LEA RSI,[table]`: the entry detour has chained into the original by now, and the loop has
// not read a slot yet.
const AFTER_ENTRY = 0x22;
// +0x54 is the mid hook's site; +0x66 is the first instruction after its 16-byte window, reached
// only by a slot that passed every test and is about to be drawn.
const MID_HOOK_SITE = 0x54;
const DRAW_PATH = 0x66;
// +0xdd `MOV RBX,[RSP+0x30]`: the epilogue, reached once the loop has walked all ten slots.
const EPILOGUE = 0xdd;
const ONE_NULL_SLOT = 3;
// One opening of the character list builds the window once, so the all-null case runs on the next
// refresh after one_null rather than waiting for openings nobody makes (2026-10-19).
const SKIP_REFRESHES = 0;

const out = new File(game.path.replace(/[^\\/]*$/, '') + 'profile-table-midhook-drive.log', 'a');
const log = (event) => {
    send(event);
    out.write(`[${new Date().toISOString()}] ${JSON.stringify(event)}\n`);
    out.flush();
};

const slotAddr = (k) => at(TABLE).add(8 * k);
const vtable = () => {
    // Every valid slot shares one vtable; take it from the first non-null slot of a full table.
    for (let k = 0; k < SLOT_COUNT; k++) {
        const r = slotAddr(k).readPointer();
        if (!r.isNull()) return r.readPointer();
    }
    return null;
};

function tableState() {
    const want = vtable();
    const slots = [];
    let allValid = want !== null;
    for (let k = 0; k < SLOT_COUNT; k++) {
        const r = slotAddr(k).readPointer();
        const valid = !r.isNull() && want !== null && r.readPointer().equals(want);
        allValid = allValid && valid;
        slots.push({
            slot: k,
            renderer: r.toString(),
            valid,
            latch: valid ? r.add(LATCH).readU8() : null,
            pending: valid ? r.add(PENDING).readU8() : null,
        });
    }
    return { allValid, slots };
}

const site = at(REFRESH + MID_HOOK_SITE);
const siteBytes = Array.from(new Uint8Array(site.readByteArray(4)));
const hooked = siteBytes[0] === 0xe9 || (siteBytes[0] === 0xff && siteBytes[1] === 0x25);
if (!hooked) {
    log({
        kind: 'refused',
        reason: 'the mid hook is not armed at +0x54; nulling a slot would fault there',
        site: site.toString(),
        bytes: siteBytes.map((b) => b.toString(16)),
    });
    throw new Error('profile-table mid hook not armed');
}

let phase = 'one_null';
let refreshesSinceOneNull = 0;
// Slots this agent nulled for the refresh in flight, with the pointers to restore.
let saved = [];
let before = null;
let drawn = null;
let currentCase = null;

function restore() {
    for (const { slot, renderer } of saved) slotAddr(slot).writePointer(renderer);
    const restored = saved.map(({ slot }) => slot);
    saved = [];
    return restored;
}

Interceptor.attach(at(REFRESH + AFTER_ENTRY), {
    onEnter() {
        if (phase === 'done' || saved.length !== 0) return;
        if (phase === 'wait_all_null') {
            refreshesSinceOneNull += 1;
            if (refreshesSinceOneNull < SKIP_REFRESHES) return;
            phase = 'all_null';
        }
        const state = tableState();
        if (!state.allValid) {
            log({ kind: 'waiting', phase, reason: 'table not fully valid at refresh start' });
            return;
        }
        before = state.slots;
        drawn = new Array(SLOT_COUNT).fill(0);
        const victims = phase === 'one_null' ? [ONE_NULL_SLOT] : [...Array(SLOT_COUNT).keys()];
        for (const slot of victims) {
            saved.push({ slot, renderer: slotAddr(slot).readPointer() });
            slotAddr(slot).writePointer(ptr(0));
        }
        currentCase = phase;
        log({ kind: 'nulled', case: currentCase, slots: victims, thread: Process.getCurrentThreadId() });
    },
});

Interceptor.attach(at(REFRESH + DRAW_PATH), {
    onEnter() {
        if (currentCase === null) return;
        const slot = this.context.rbx.toInt32();
        if (slot >= 0 && slot < SLOT_COUNT) drawn[slot] += 1;
    },
});

Interceptor.attach(at(REFRESH + EPILOGUE), {
    onEnter() {
        if (currentCase === null) return;
        const restored = restore();
        const after = tableState().slots;
        log({
            kind: 'result',
            case: currentCase,
            restored,
            drawn,
            slots: after.map((s, k) => ({
                slot: k,
                nulled: restored.includes(k),
                reached_draw_path: drawn[k],
                latch_before: before[k].latch,
                latch_after: s.latch,
                pending_before: before[k].pending,
                pending_after: s.pending,
            })),
        });
        phase = currentCase === 'one_null' ? 'wait_all_null' : 'done';
        currentCase = null;
    },
});

// A fault inside the refresh while a slot is nulled means the mid hook did not divert it: put the
// table back and record where it faulted, then let the game's own handling run.
Process.setExceptionHandler((details) => {
    if (saved.length === 0) return false;
    const restored = restore();
    log({
        kind: 'fault',
        case: currentCase,
        type: details.type,
        address: details.address.toString(),
        rva: details.address.sub(game.base).toString(),
        rdi: details.context.rdi.toString(),
        restored,
    });
    return false;
});

rpc.exports.dispose = () => {
    const restored = restore();
    log({ kind: 'dispose', restored });
};

log({
    kind: 'armed',
    refresh: at(REFRESH).toString(),
    table: at(TABLE).toString(),
    mid_hook_site_bytes: siteBytes.map((b) => b.toString(16)),
    one_null_slot: ONE_NULL_SLOT,
});
