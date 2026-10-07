// Does TestNetStep::STEP_Update (the in-game network step that owns SosSignMan) run, and does it
// reach the sign update? 1.17.1: STEP_Update 0x140b082c0 (1.16.2 0x140b06bb0), CSRemoImp::IsInCutscene
// call at 0x140b0843a (0x140a9e000), sign update wrapper 0x1406fc590, sign update 0x1406fffe0.
// The DLL's duel sign was touched, its PhantomJoinData queued, and the join never ran (2026-10-06).
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-netstep.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

// The live step table at 0x143d74f60 holds {0x140b082c0 STEP_Update, name} then {0x140b082b0, name}.
const counts = { stepOther: 0, step: 0, cutsceneTrue: 0, cutsceneFalse: 0, wrapper: 0, update: 0, joinTick: 0 };
const hooks = [];
hooks.push(Interceptor.attach(va('0x140b082c0'), { onEnter() { counts.step++; } }));
hooks.push(Interceptor.attach(va('0x140b082b0'), { onEnter() { counts.stepOther++; } }));
send({ kind: 'netstep-names', update: va('0x142b642f8').readUtf16String(), other: va('0x142b64330').readUtf16String() });
hooks.push(Interceptor.attach(va('0x140a9e000'), {
    onLeave(r) { if ((r.toInt32() & 0xff) !== 0) counts.cutsceneTrue++; else counts.cutsceneFalse++; },
}));
hooks.push(Interceptor.attach(va('0x1406fc590'), { onEnter() { counts.wrapper++; } }));
hooks.push(Interceptor.attach(va('0x1406fffe0'), { onEnter() { counts.update++; } }));
hooks.push(Interceptor.attach(va('0x1406ff260'), { onEnter() { counts.joinTick++; } }));

// Reported from the frame tick, every 120 frames, so the report rides the game's own clock.
let frames = 0;
hooks.push(Interceptor.attach(va('0x140773900'), {
    onEnter() { if (++frames % 120 === 0) send(Object.assign({ kind: 'netstep', frames: frames }, counts)); },
}));
rpc.exports.dispose = () => hooks.forEach((h) => h.detach());
