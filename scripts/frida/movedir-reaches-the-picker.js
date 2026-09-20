// Does the engine resolve a LEFT/RIGHT the player is pressing, while our drive strip does nothing?
//
// # The question
//
// The save picker's drive strip switches drives on a mouse click and never on a direction. Measured
// in the live run of 2026-09-19 (pid 1828121, er-save-game-row.log): `drive-strip pump mouse` x14
// against `drive-strip pump key` x0, and not one `save-picker-nav: native move_dir` line in a run
// where the list cursor moved twelve times. Two readings fit that, and they want opposite fixes:
//
//   1. the engine never resolved a horizontal direction at all -- a binding problem, the shape of
//      bd `dpad-right-was-unbound-in-the-players-own-key-config-2026-09-19`;
//   2. the engine resolved it and our own code never asked -- `save_picker_native_nav::sample` is
//      called only while `dinput_nav_reader_live()` is false, and that reader goes live the first
//      frame the game polls the DirectInput keyboard, which it does whether or not a pad is in use.
//
// This hook answers which, from outside the DLL, where our own logging cannot flatter the answer.
//
// # What it hooks, and why that address is safe to attach to
//
// `FUN_140757c40` on 1.16.2, `+0x758a90` on 1.17 -- `CS::MoveDir GetMoveDir(mode 2)`, the resolver
// `CS::GridControl` steps its cursor with. Its two components are a column delta and a row delta,
// so `x == -1` is left and `x == +1` is right (derived in `save_picker_native_nav`'s module doc
// from the grid's own `dir[1] * columns + dir[0]`). `0x758a90` is below the 1.17.0 -> 1.17.1
// boundary rva `0xafefe9`, so it needs no `+0x70` carry.
//
// Nothing in this repo detours that address -- `er-quit-menu-core` CALLS it -- so `Interceptor` is
// attaching to a real prologue rather than to somebody's trampoline.
//
// Read-only: no write, no watchpoint, no `MemoryAccessMonitor`. The callee writes both components
// of the out pointer on every branch including its refusal path, so reading them on leave is
// reading what the engine just decided.
'use strict';

const MOVE_DIR_MODE2_RVA = 0x758a90;

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
    send({ kind: 'error', message: 'eldenring.exe not loaded' });
} else {
    const target = game.base.add(MOVE_DIR_MODE2_RVA);
    send({
        kind: 'hello',
        base: '0x' + game.base.toString(16),
        move_dir: '0x' + target.toString(16),
    });

    // Held levels, so a report is a press rather than a frame. The engine pulses this with its own
    // auto-repeat, so a hold reads as a run of edges -- which is the point: any of them would have
    // been enough for the drive strip to act on.
    let lastX = 0;
    let lastY = 0;
    let reported = 0;
    let calls = 0;
    const seen = { left: 0, right: 0, up: 0, down: 0, zero: 0 };

    // How many resolver calls between tallies. The resolver runs once per frame while a grid menu
    // is up, so this is roughly fifteen seconds of menu time -- counted in the menu's own frames
    // rather than on a wall clock, which is both the honest unit for the question and the one that
    // needs no timer.
    const TALLY_EVERY = 900;

    Interceptor.attach(target, {
        onLeave(retval) {
            let x;
            let y;
            try {
                x = retval.readS32();
                y = retval.add(4).readS32();
            } catch (e) {
                return;
            }
            calls += 1;
            if (calls % TALLY_EVERY === 0) {
                send({ kind: 'tally', calls: calls, seen: seen });
            }
            if (x === 0 && y === 0) {
                seen.zero += 1;
                lastX = 0;
                lastY = 0;
                return;
            }
            if (x < 0) seen.left += 1;
            if (x > 0) seen.right += 1;
            if (y < 0) seen.up += 1;
            if (y > 0) seen.down += 1;
            const rising = x !== lastX || y !== lastY;
            lastX = x;
            lastY = y;
            if (!rising) {
                return;
            }
            reported += 1;
            if (reported <= 40 || reported % 25 === 0) {
                send({ kind: 'move_dir', n: reported, x: x, y: y, seen: seen });
            }
        },
    });

    // The tally inside the hook is what makes a quiet session mean something: a run that saw only
    // vertical directions says so out loud instead of reading like a player who pressed nothing.
    // It counts the resolver's own calls, zeros included, so the absence is reported by the same
    // instrument that would have reported the presence -- and no timer is involved, which is what
    // `scripts/check-no-timeouts.py` is about.
    //
    // Once more on the way out, so a session shorter than one tally interval still says what it
    // saw. Frida calls this on a reload in place, on a clean detach, and on the watcher's own
    // `SIGTERM`.
    rpc.exports = {
        dispose() {
            send({ kind: 'tally', calls: calls, seen: seen, final: true });
        },
    };
}
