// Read-only: every sign in SosSignMan's map against the player's physics position and block, on
// the next frame. Written to answer "the DLL placed a red sign the player cannot see"
// (er-npc-summons, 2026-10-06).
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-sign-probe.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const FRAME_TICK = va('0x140773900');
const WORLD_CHR_MAN = va('0x143d69ff8');
const MAIN_PLAYER = 0x1e508;
const CS_EVENT_MAN = va('0x143d6c768');
const GET_PHYSICS_POS = new NativeFunction(va('0x1403f0e20'), 'void', ['pointer', 'pointer']);
const SIGN = { POS: 0x14, BLOCK: 0x10, NPC: 0x234 };

function signMan() {
    const em = CS_EVENT_MAN.readPointer();
    if (em.isNull()) return null;
    const ctrl = em.add(0x60).readPointer();
    if (ctrl.isNull()) return null;
    const man = ctrl.add(0x48).readPointer();
    return man.isNull() ? null : man;
}

function signs(man) {
    const head = man.add(0x10).readPointer();
    const out = [];
    const walk = (n, depth) => {
        if (n.isNull() || n.add(0x19).readU8() !== 0 || depth > 64) return;
        walk(n.readPointer(), depth + 1);
        out.push(n.add(0x28).readPointer());
        walk(n.add(0x10).readPointer(), depth + 1);
    };
    walk(head.add(8).readPointer(), 0);
    return out;
}

const vec = (p) => [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()].map((v) => +v.toFixed(2));

let done = false;   // reloading the file runs it again
Interceptor.attach(FRAME_TICK, {
    onEnter() {
        if (done) return;
        done = true;
        const wcm = WORLD_CHR_MAN.readPointer();
        const p = wcm.isNull() ? ptr(0) : wcm.add(MAIN_PLAYER).readPointer();
        const at = Memory.alloc(16);
        if (!p.isNull()) GET_PHYSICS_POS(p, at);
        const man = signMan();
        // signsSfx std::map: head at SosSignMan+0x28, node +0x20 key, +0x28 FXHGSfxCtrl_Sign*.
        const sfx = [];
        if (man !== null) {
            const head = man.add(0x28).readPointer();
            const walk = (n, depth) => {
                if (n.isNull() || n.add(0x19).readU8() !== 0 || depth > 64) return;
                walk(n.readPointer(), depth + 1);
                const ctrl = n.add(0x28).readPointer();
                sfx.push({ key: '0x' + n.add(0x20).readU64().toString(16), ctrl: String(ctrl),
                    flags420: ctrl.isNull() ? null : ctrl.add(0x420).readU32() });
                walk(n.add(0x10).readPointer(), depth + 1);
            };
            if (!head.isNull()) walk(head.add(8).readPointer(), 0);
        }
        send({ kind: 'duel-sign-sfx', sfx: sfx });
        send({
            kind: 'duel-sign-probe',
            player: p.isNull() ? null : { pos: vec(at), block: '0x' + p.add(0x38).readU32().toString(16) },
            signs: man === null ? null : signs(man).filter((s) => !s.isNull()).map((s) => ({
                data: String(s),
                type: s.readU32(),
                npc: s.add(SIGN.NPC).readU32(),
                block: '0x' + s.add(SIGN.BLOCK).readU32().toString(16),
                pos: vec(s.add(SIGN.POS)),
                head: s.readByteArray(0x40) === null ? null : Array.from(new Uint8Array(s.readByteArray(0x40))).map((b) => b.toString(16).padStart(2, '0')).join(''),
            })),
        });
    },
});
