// How often the game calls XInputGetState / XInputGetStateEx, per user index, and what each call
// returned. Written 2026-10-05 after a chainsaw-driver run ended `precheck_no_pad_polls` with no
// controller attached: the question is whether the game polls XInput rarely (a reconnect scan) or
// not at all while no pad is connected. Reports every 60 game action updates, 20 times, then detaches its
// hooks. Read-only: nothing returned to the game is changed.
'use strict';

const mods = ['xinput1_4.dll', 'xinput1_3.dll', 'xinput9_1_0.dll']
    .map(function (n) { return Process.findModuleByName(n); })
    .filter(function (m) { return m !== null; });

const counts = {};
const hooks = [];

function bump(key) { counts[key] = (counts[key] || 0) + 1; }

function hook(mod, name, addr) {
    hooks.push(Interceptor.attach(addr, {
        onEnter(args) { this.user = args[0].toUInt32(); },
        onLeave(ret) { bump(mod.name + ':' + name + ':u' + this.user + ':ret' + ret.toUInt32().toString(16)); },
    }));
}

mods.forEach(function (m) {
    try { hook(m, 'XInputGetState', m.getExportByName('XInputGetState')); } catch (e) { bump('err:' + e); }
    try {
        const gpa = new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('GetProcAddress'),
            'pointer', ['pointer', 'pointer']);
        const ex = gpa(m.base, ptr(100));
        if (!ex.isNull()) hook(m, 'XInputGetStateEx', ex);
    } catch (e) { bump('err:' + e); }
});

send({ kind: 'armed', modules: mods.map(function (m) { return m.name; }), hooks: hooks.length });

// The clock is the game itself: CSChrActionRequestModule::UpdateFromManipulator (1.17.1 0x140408190),
// which runs every frame for each character with a manipulator, so it ticks whether or not XInput
// is ever polled. One report per 60 calls, 20 reports, then the hooks come off.
const game = Process.findModuleByName('eldenring.exe');
let calls = 0;
let ticks = 0;
const clock = Interceptor.attach(game.base.add(0x408190), {
    onEnter() {
        calls += 1;
        if (calls % 60 !== 0 || ticks >= 20) return;
        ticks += 1;
        send({ kind: 'tick', n: ticks, counts: Object.assign({}, counts) });
        if (ticks >= 20) {
            hooks.forEach(function (h) { h.detach(); });
            send({ kind: 'done' });
        }
    },
});

rpc.exports = {
    dispose() {
        clock.detach();
        hooks.forEach(function (h) { h.detach(); });
    },
};
