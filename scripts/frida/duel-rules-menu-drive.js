// Drive er-duel-rules' two keys without taking the screen from the user: F2 opens the rules menu,
// ctrl+F2 hides the rule-break list, ctrl+F2 shows it again, and F2 closes the menu.
//
// The same approach as f3-toggle-drive.js, for the same measured reasons: a press injected from
// the compositor never reaches the game's own `GetAsyncKeyState`, and the DLL ignores its keys
// while the game is not the foreground window. So both reads are answered inside the process,
// scoped to calls whose return address lies in er_duel_rules.dll:
//
//   * `GetWindowThreadProcessId` reports this process, so the DLL's focus check passes for its own
//     poll and for nothing else in the game.
//   * `GetAsyncKeyState` reports F2, and ctrl for the ctrl+F2 presses, down for a few polls at
//     each step.
//
// Everything downstream is the real code: the exact-modifier match, the edge latch, the input
// capture the menu takes, the settings write. Steps are scheduled on the DLL's own poll count
// (one Escape poll per drawn frame), never on a timer. The log reads `menu opened`, the list
// hidden and shown, and `menu closed`; the status line carries the input counters.
const VK_CONTROL = 0x11;
const VK_ESCAPE = 0x1b;
const VK_F2 = 0x71;
const DOWN = 0x8000;

// [first frame, frames held, keys down]. Gaps between steps let the menu draw and the input
// capture count reads while it is open.
const STEPS = [
    [120, 3, [VK_F2]],
    [420, 3, [VK_CONTROL, VK_F2]],
    [720, 3, [VK_CONTROL, VK_F2]],
    [1020, 3, [VK_F2]],
];
const REPORT_EVERY_FRAMES = 300;

const user32 = Process.findModuleByName('user32.dll');
const duel = Process.findModuleByName('er_duel_rules.dll');

if (duel === null) {
    send({ tag: 'fatal', why: 'er_duel_rules.dll is not loaded in this process' });
} else {
    const lo = duel.base;
    const hi = duel.base.add(duel.size);
    const fromDuel = (ret) => ret.compare(lo) >= 0 && ret.compare(hi) < 0;
    const ownPid = Process.id;
    let frames = 0;
    let focusForced = 0;
    let reported = -1;

    const downNow = (vk) => STEPS.some(([at, held, keys]) => frames >= at && frames < at + held && keys.includes(vk));

    Interceptor.attach(user32.getExportByName('GetWindowThreadProcessId'), {
        onEnter(args) {
            this.out = args[1];
            this.mine = fromDuel(this.returnAddress);
        },
        onLeave() {
            if (!this.mine || this.out.isNull() || this.out.readU32() === ownPid) return;
            this.out.writeU32(ownPid);
            focusForced += 1;
        },
    });

    Interceptor.attach(user32.getExportByName('GetAsyncKeyState'), {
        onEnter(args) {
            this.vk = args[0].toInt32() & 0xffff;
            this.mine = fromDuel(this.returnAddress);
        },
        onLeave(retval) {
            if (!this.mine) return;
            // Every key the DLL asks about is answered here, so a key the user holds during the
            // run cannot reach its poll: only the steps above are ever down.
            retval.replace(ptr(downNow(this.vk) ? DOWN : 0));
            if (this.vk === VK_ESCAPE) {
                frames += 1;
                const step = STEPS.findIndex(([at]) => at === frames);
                if (step >= 0) send({ tag: 'step', step, frame: frames, keys: STEPS[step][2] });
                if (frames % REPORT_EVERY_FRAMES === 0 && frames !== reported) {
                    reported = frames;
                    send({ tag: 'frames', frames, focus_reports_forced: focusForced });
                }
                if (frames === STEPS[STEPS.length - 1][0] + 60) send({ tag: 'done', frames });
            }
        },
    });

    send({ tag: 'armed', duel: duel.base.toString(), size: duel.size, pid: ownPid });
}
