// Does TestNetStep::STEP_Update (the in-game network step that owns SosSignMan) run, and does it
// reach the sign update? Measured on 1.17.1 and carried to 2.7.2.0; every body matches between the
// two de-Arxan'd images with its rip-relative and branch operands carried:
//   STEP_Update                0x140b082c0 -> 0x140b08780 (1.16.2 0x140b06bb0)
//   CSRemoImp::IsInCutscene    0x140a9e000 -> 0x140a9e4c0, called at 0x140b0843a -> 0x140b088fa
//   sign update wrapper        0x1406fc590 -> 0x1406fca40
//   sign update                0x1406fffe0 -> 0x140700490
// The DLL's duel sign was touched, its PhantomJoinData queued, and the join never ran (2026-10-06).
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-netstep.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

// The live step table at 0x143d74f60 -> 0x143d77f80 holds {STEP_Update, name} then
// {0x140b082b0 -> 0x140b08770, name}; the two names are TestNetStep::STEP_Update and
// TestNetStep::STEP_Finish (0x142b642f8 -> 0x142b663d8 and 0x142b64330 -> 0x142b66410, the
// same UTF-16 text in both builds).
const counts = { stepOther: 0, step: 0, cutsceneTrue: 0, cutsceneFalse: 0, wrapper: 0, update: 0, joinTick: 0 };
const hooks = [];
hooks.push(Interceptor.attach(va('0x140b08780'), { onEnter() { counts.step++; } }));
hooks.push(Interceptor.attach(va('0x140b08770'), { onEnter() { counts.stepOther++; } }));
send({ kind: 'netstep-names', update: va('0x142b663d8').readUtf16String(), other: va('0x142b66410').readUtf16String() });
hooks.push(Interceptor.attach(va('0x140a9e4c0'), {
    onLeave(r) { if ((r.toInt32() & 0xff) !== 0) counts.cutsceneTrue++; else counts.cutsceneFalse++; },
}));
hooks.push(Interceptor.attach(va('0x1406fca40'), { onEnter() { counts.wrapper++; } }));
hooks.push(Interceptor.attach(va('0x140700490'), { onEnter() { counts.update++; } }));
// Join tick 0x1406ff260 -> 0x1406ff710.
hooks.push(Interceptor.attach(va('0x1406ff710'), { onEnter() { counts.joinTick++; } }));

// Reported from the frame tick (0x140773900 -> 0x140773db0), every 120 frames, so the report rides
// the game's own clock.
let frames = 0;
hooks.push(Interceptor.attach(va('0x140773db0'), {
    onEnter() { if (++frames % 120 === 0) send(Object.assign({ kind: 'netstep', frames: frames }, counts)); },
}));
rpc.exports.dispose = () => hooks.forEach((h) => h.detach());
