// Which of the three per-frame "show this sign" terms fails for an NPC red sign.
//
// SosSignMan's sign update (1.16.2 FUN_1406ff190, 1.17.1 0x1406fffe0) sets SosSignData+0x30 every
// frame to !IsSignsPresent() && SummoningFrame::Check(&f, &sign.multiplayType) &&
// FUN_1406fba00(&sign), then calls CreateSignSfx, which destroys the sign's effect when +0x30 is 0.
// The DLL's duel sign for entity 35000 had +0x30 == 0 and no visible effect (2026-10-06). This
// reports each term's return value, once per second, while inside that update.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/duel-sign-verdict.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const SIGN_UPDATE = va('0x1406fffe0');      // 1.16.2 0x1406ff190, +0xe50, prologue identical
const IS_SIGNS_PRESENT = va('0x1401d3000'); // 1.16.2 0x1401d3000, delta 0
const CHECK_FRAME = va('0x1406f3410');      // CheckSummoningFrame body, 1.16.2 0x1406f25c0
const SIGN_TERM3 = va('0x1406fc850');       // 1.16.2 FUN_1406fba00(SosSignData**)

let inUpdate = 0;
let last = 0;
let row = null;
const hooks = [];

hooks.push(Interceptor.attach(SIGN_UPDATE, {
    onEnter() { inUpdate++; row = { present: [], check: [], term3: [] }; },
    onLeave() {
        inUpdate--;
        const now = Date.now();
        if (now - last >= 1000) {
            last = now;
            send({ kind: 'duel-sign-verdict', ...row });
        }
    },
}));
hooks.push(Interceptor.attach(IS_SIGNS_PRESENT, {
    onLeave(r) { if (inUpdate && row) row.present.push(r.toInt32() & 0xff); },
}));
hooks.push(Interceptor.attach(CHECK_FRAME, {
    onEnter(args) { this.type = inUpdate ? args[1].readU8() : null; },
    onLeave(r) { if (this.type !== null && row) row.check.push({ type: this.type, ok: r.toInt32() & 0xff }); },
}));
hooks.push(Interceptor.attach(SIGN_TERM3, {
    onEnter(args) { this.npc = inUpdate ? args[0].readPointer().add(0x234).readU32() : null; },
    onLeave(r) { if (this.npc !== null && row) row.term3.push({ npc: this.npc, ok: r.toInt32() & 0xff }); },
}));

rpc.exports.dispose = () => hooks.forEach((h) => h.detach());
