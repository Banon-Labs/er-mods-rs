// Experiment: does setting SosSignData+0x30 to 1 and re-running CreateSignSfx make the duel sign
// visible? +0x30 is the byte CreateSignSfx tests (1.17.1 0x1406fed53) before it builds the effect;
// the only static writer is TestNetStep's sign update, which does not run in a normal session, and
// the DLL's sign keyed to entity 35000 reads 0 with no effect drawn (2026-10-06).
//
// Runs once, on the next frame tick, on the game thread.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-sign-show.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const FRAME_TICK = va('0x140773900');
const CS_EVENT_MAN = va('0x143d6c768');
const CREATE_SIGN_SFX = new NativeFunction(va('0x1406febe0'), 'void', ['pointer', 'pointer']);
const SIGN_NPC = 35000;

function signMan() {
    const em = CS_EVENT_MAN.readPointer();
    if (em.isNull()) return null;
    const ctrl = em.add(0x60).readPointer();
    if (ctrl.isNull()) return null;
    const man = ctrl.add(0x48).readPointer();
    return man.isNull() ? null : man;
}

function walk(head, visit) {
    const go = (n, depth) => {
        if (n.isNull() || n.add(0x19).readU8() !== 0 || depth > 64) return;
        go(n.readPointer(), depth + 1);
        visit(n);
        go(n.add(0x10).readPointer(), depth + 1);
    };
    go(head.add(8).readPointer(), 0);
}

function sfxFlags(man, signId) {
    let out = null;
    walk(man.add(0x28).readPointer(), (n) => {
        if (n.add(0x20).readU32() === signId) {
            const ctrl = n.add(0x28).readPointer();
            out = ctrl.isNull() ? 'no ctrl' : ctrl.add(0x420).readU32();
        }
    });
    return out;
}

let done = false;
const tick = Interceptor.attach(FRAME_TICK, {
    onEnter() {
        if (done) return;
        done = true;
        const man = signMan();
        if (man === null) return send({ kind: 'sign-show', ok: false, why: 'no SosSignMan' });
        let sign = null;
        walk(man.add(0x10).readPointer(), (n) => {
            const s = n.add(0x28).readPointer();
            if (!s.isNull() && s.add(0x234).readU32() === SIGN_NPC) sign = s;
        });
        if (sign === null) return send({ kind: 'sign-show', ok: false, why: 'no sign for ' + SIGN_NPC });
        const id = sign.readU32();
        const before = { show: sign.add(0x30).readU8(), sfx: sfxFlags(man, id) };
        sign.add(0x30).writeU8(1);
        CREATE_SIGN_SFX(man, sign);
        send({ kind: 'sign-show', ok: true, sign: String(sign), before: before,
            after: { show: sign.add(0x30).readU8(), sfx: sfxFlags(man, id) } });
    },
});

rpc.exports.dispose = () => tick.detach();
