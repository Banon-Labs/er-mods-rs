// Drive both branches of er-scaleform-hooks' descriptor-ring mid hook (er-effects-rs-7xbs.1) on
// the game's own render thread, from inside the descriptor-ring advance `advance(ring, count)`.
//
// The hook replaces an entry guard that skipped the whole function while the ring's current page
// `ring+0x38` was null. It now diverts only the page-roll branch (advance+0x8c -> +0x157). This
// agent forces the null page for exactly one call on each branch and restores it straight after:
//
//   (a) fast path -- a call with cursor+count < capacity. The function must advance as if nothing
//       were hooked: cursor `+0x28` by `count`, the CPU/GPU handles `+0x50`/`+0x58` by
//       `count * stride` (`+0x30`), and the page slot must still read null on return (the fast
//       path never touches it). The old entry guard moved none of these.
//   (b) page-roll path -- a call with cursor+count >= capacity. Without the mid hook this faults at
//       advance+0xa1, so it is forced only when the 14-byte ilhook jump is present at +0x8c. On
//       return the page slot must still read null (no page was selected or allocated), the cursor
//       must have moved by `count` (the add at +0x0b ran), the handles must be unchanged (the
//       diversion returned through +0x157, not through the page allocator at 1.17.1 0x140ecb610,
//       which zeroes them before it looks at the device) and the
//       return value must be 0. The DLL counts the diversion in
//       `oracle_scaleform_desc_provider_null_hits`, which should rise by exactly one.
//
// Each forced call is logged with its before/after state and a verdict, and `counts` keeps one
// counter per branch. Calls that arrive with a null page on their own are counted, not forced.
//
// Offsets are 1.17.1. Nothing is written except `ring+0x38`, and only for the length of the one
// call; the forced page-roll call leaves the real page off the free list until the next roll,
// and its caller reuses the previous descriptor slot for one draw.
'use strict';

const game = Process.getModuleByName('eldenring.exe');
const at = (rva) => game.base.add(rva);

// 1.17.1 rva of the advance (1.16.2 0xec9530, IDENTICAL-WHOLE).
const ADVANCE = 0xecb3a0;
const PAGE_ROLL_LOAD = 0x8c;
const RING_CAPACITY = 0x20;
const RING_CURSOR = 0x28;
const RING_STRIDE = 0x30;
const RING_PAGE = 0x38;
const RING_CPU_HANDLE = 0x50;
const RING_GPU_HANDLE = 0x58;
// ilhook's jump: `JMP [RIP+0]` followed by the absolute address.
const ILHOOK_JUMP = [0xff, 0x25, 0x00, 0x00, 0x00, 0x00];

const out = new File(game.path.replace(/[^\\/]*$/, '') + 'scaleform-descriptor-midhook-drive.log', 'a');
const log = (event) => {
    send(event);
    out.write(`[${new Date().toISOString()}] ${JSON.stringify(event)}\n`);
    out.flush();
};

const hex = (bytes) => Array.from(new Uint8Array(bytes)).map((b) => b.toString(16).padStart(2, '0')).join(' ');
const siteBytes = at(ADVANCE + PAGE_ROLL_LOAD).readByteArray(14);
const midHookArmed = ILHOOK_JUMP.every((b, i) => new Uint8Array(siteBytes)[i] === b);

function ringState(ring) {
    return {
        capacity: ring.add(RING_CAPACITY).readU64(),
        cursor: ring.add(RING_CURSOR).readU64(),
        stride: ring.add(RING_STRIDE).readU32(),
        page: ring.add(RING_PAGE).readPointer(),
        cpu: ring.add(RING_CPU_HANDLE).readU64(),
        gpu: ring.add(RING_GPU_HANDLE).readU64(),
    };
}

const show = (s) => ({
    capacity: s.capacity.toString(),
    cursor: s.cursor.toString(),
    stride: s.stride,
    page: s.page.toString(),
    cpu: '0x' + s.cpu.toString(16),
    gpu: '0x' + s.gpu.toString(16),
});

const counts = {
    fast_forced: 0,
    fast_ok: 0,
    page_roll_forced: 0,
    page_roll_ok: 0,
    natural_null_page: 0,
};
const pending = { fast: true, pageRoll: midHookArmed };
let busy = false;

function judgeFast(before, after, count, retval) {
    const step = count * before.stride;
    const checks = {
        cursor_advanced: after.cursor.equals(before.cursor.add(count)),
        cpu_advanced: after.cpu.equals(before.cpu.add(step)),
        gpu_advanced: after.gpu.equals(before.gpu.add(step)),
        page_still_null: after.page.isNull(),
    };
    return { checks, ok: Object.values(checks).every(Boolean), retval: retval.toString() };
}

function judgePageRoll(before, after, count, retval) {
    const checks = {
        page_still_null: after.page.isNull(),
        cursor_advanced: after.cursor.equals(before.cursor.add(count)),
        cpu_unchanged: after.cpu.equals(before.cpu),
        gpu_unchanged: after.gpu.equals(before.gpu),
        returned_zero: retval.isNull(),
    };
    return { checks, ok: Object.values(checks).every(Boolean), retval: retval.toString() };
}

Interceptor.attach(at(ADVANCE), {
    onEnter(args) {
        this.forced = null;
        if (busy) return;
        const ring = args[0];
        const count = args[1].toUInt32();
        const before = ringState(ring);
        if (before.page.isNull()) {
            counts.natural_null_page += 1;
            return;
        }
        const fast = before.cursor.add(count).compare(before.capacity) < 0;
        const branch = fast ? 'fast' : 'pageRoll';
        if (!pending[branch]) return;
        pending[branch] = false;
        busy = true;
        counts[fast ? 'fast_forced' : 'page_roll_forced'] += 1;
        this.forced = { ring, count, branch, before, savedPage: before.page };
        ring.add(RING_PAGE).writePointer(ptr(0));
        log({ kind: 'forced', branch, ring: ring.toString(), count, before: show(before), thread: Process.getCurrentThreadId() });
    },
    onLeave(retval) {
        const forced = this.forced;
        if (forced === null) return;
        const after = ringState(forced.ring);
        forced.ring.add(RING_PAGE).writePointer(forced.savedPage);
        busy = false;
        const verdict = forced.branch === 'fast'
            ? judgeFast(forced.before, after, forced.count, retval)
            : judgePageRoll(forced.before, after, forced.count, retval);
        if (verdict.ok) counts[forced.branch === 'fast' ? 'fast_ok' : 'page_roll_ok'] += 1;
        log({
            kind: 'returned',
            branch: forced.branch,
            after: show(after),
            restored_page: forced.ring.add(RING_PAGE).readPointer().toString(),
            ...verdict,
            counts,
        });
        if (!pending.fast && !pending.pageRoll) log({ kind: 'done', counts });
    },
});

log({
    kind: 'armed',
    advance: at(ADVANCE).toString(),
    site: at(ADVANCE + PAGE_ROLL_LOAD).toString(),
    site_bytes: hex(siteBytes),
    mid_hook_armed: midHookArmed,
    page_roll_drive: midHookArmed ? 'pending' : 'refused: no ilhook jump at +0x8c, the forced call would fault at +0xa1',
});
