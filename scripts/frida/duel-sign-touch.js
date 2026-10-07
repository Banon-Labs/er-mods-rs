// What happens when the player touches the duel sign: does the action button find it, does the
// summon get queued, does the join tick run? 1.17.1 addresses, bd
// placenpcsummonsign-position-1171-2026-10-06 and npc-sign-join-visibility-1171-2026-10-06.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-sign-touch.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const GET_SIGN_BY_ACTION_BUTTON = va('0x1406fadb0');
const QUEUE_SUMMON = va('0x1406fe520');     // copies the sign's pos/yaw into PhantomJoinData
const JOIN_TICK = va('0x1406ff260');
const CONVERT_TO_NPC_PHANTOM = va('0x14050c6d0');
const CHECK_FRAME = va('0x1406f3410');
const IS_SIGNS_PRESENT = va('0x1401d3000');
const SIGN_TERM3 = va('0x1406fc850');

const hooks = [];
const last = {};
function report(kind, fields, everyMs) {
    const now = Date.now();
    if (everyMs && last[kind] && now - last[kind] < everyMs) return;
    last[kind] = now;
    send(Object.assign({ kind: kind }, fields));
}

hooks.push(Interceptor.attach(GET_SIGN_BY_ACTION_BUTTON, {
    onLeave(r) { report('touch-lookup', { result: String(r) }, 2000); },
}));
hooks.push(Interceptor.attach(CHECK_FRAME, {
    // SummoningFrame (bd sign-touch-verdict-offline-1171-2026-10-06): +0x8 mixedCovenantSlot,
    // +0xc redInvaderSlot1, +0x10 redInvaderSlot2 (0 Available, 1 Filled, 2 Disabled),
    // +0x26 blueHunterPresent, +0x29 canSummonRedSign, +0x2e totalPhantoms, +0x33 forNpc.
    onEnter(args) {
        this.type = args[1].readU8();
        const f = args[0];
        this.frame = { mixed: f.add(0x8).readU32(), red1: f.add(0xc).readU32(), red2: f.add(0x10).readU32(),
            blueHunter: f.add(0x26).readU8(), canRed: f.add(0x29).readU8(), phantoms: f.add(0x2e).readU8(),
            forNpc: f.add(0x33).readU8(), raw: Array.from(new Uint8Array(f.readByteArray(0x40))).map((b) => b.toString(16).padStart(2, '0')).join('') };
    },
    onLeave(r) { report('touch-check-frame', { type: this.type, ok: r.toInt32() & 0xff, frame: this.frame }, 2000); },
}));
hooks.push(Interceptor.attach(IS_SIGNS_PRESENT, {
    onLeave(r) { report('touch-signs-present', { present: r.toInt32() & 0xff }, 2000); },
}));
hooks.push(Interceptor.attach(SIGN_TERM3, {
    onLeave(r) { report('touch-term3', { ok: r.toInt32() & 0xff }, 2000); },
}));
hooks.push(Interceptor.attach(QUEUE_SUMMON, {
    onEnter() { report('touch-queued', {}); },
}));
hooks.push(Interceptor.attach(JOIN_TICK, {
    onEnter() { report('touch-join-tick', {}, 2000); },
}));
hooks.push(Interceptor.attach(CONVERT_TO_NPC_PHANTOM, {
    onEnter() { report('touch-converted', {}); },
}));

rpc.exports.dispose = () => hooks.forEach((h) => h.detach());
