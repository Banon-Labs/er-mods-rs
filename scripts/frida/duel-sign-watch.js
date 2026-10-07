// Who writes SosSignData+0x30, the byte CreateSignSfx reads to decide whether a sign's effect is
// shown (1.17.1 `cmp byte ptr [rsi+0x30],0` at 0x1406fed53)? The only static writer found on
// 1.16.2 is TestNetStep::STEP_Update's sign update, which does not run in a normal session.
//
// Arms one hardware write watchpoint, on the thread that runs the frame tick, over +0x30 of the
// sign keyed to SIGN_NPC. Reports each distinct writing instruction once. `dispose` unsets it.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-sign-watch.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const FRAME_TICK = va('0x140773900');
const CS_EVENT_MAN = va('0x143d6c768');
const SIGN_NPC = 35000;
const SLOT = 0;

function signFor(npc) {
    const em = CS_EVENT_MAN.readPointer();
    if (em.isNull()) return null;
    const ctrl = em.add(0x60).readPointer();
    if (ctrl.isNull()) return null;
    const man = ctrl.add(0x48).readPointer();
    if (man.isNull()) return null;
    const head = man.add(0x10).readPointer();
    let found = null;
    const walk = (n, depth) => {
        if (found !== null || n.isNull() || n.add(0x19).readU8() !== 0 || depth > 64) return;
        walk(n.readPointer(), depth + 1);
        const s = n.add(0x28).readPointer();
        if (!s.isNull() && s.add(0x234).readU32() === npc) found = s;
        walk(n.add(0x10).readPointer(), depth + 1);
    };
    walk(head.add(8).readPointer(), 0);
    return found;
}

let armed = null;
const seen = {};
Process.setExceptionHandler((d) => {
    if (armed === null || d.type !== 'single-step') return false;
    const pc = d.address;
    const key = String(pc);
    if (!(key in seen)) {
        seen[key] = true;
        send({ kind: 'sign-30-write', pc: String(pc.sub(game.base).add(BASE)), value: armed.target.readU8(),
            type: d.type, caller: String(ptr(d.context.rsp).readPointer()) });
    }
    return true;
});

const tick = Interceptor.attach(FRAME_TICK, {
    onEnter() {
        if (armed !== null) return;
        const s = signFor(SIGN_NPC);
        if (s === null) return;
        const tid = Process.getCurrentThreadId();
        const thread = Process.enumerateThreads().find((t) => t.id === tid);
        if (thread === undefined) return send({ kind: 'sign-30-watch', ok: false, why: 'no thread object' });
        thread.setHardwareWatchpoint(SLOT, s.add(0x30), 1, 'w');
        armed = { thread: thread, target: s.add(0x30) };
        send({ kind: 'sign-30-watch', ok: true, sign: String(s), tid: tid, value: s.add(0x30).readU8() });
    },
});

rpc.exports.dispose = () => {
    tick.detach();
    if (armed !== null) armed.thread.unsetHardwareWatchpoint(SLOT);
};
