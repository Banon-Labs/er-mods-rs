// Which bullet hits reach the weapon-buff reader, and what it returns, per hit.
//
// Written 2026-10-04 to settle the Rain of Arrows lock-on question
// (docs/er-mechanics/rain-of-arrows-seppuku.md, "Lock-on"): the static data says the Piquebone
// smoke 20003309 is the same bullet, context 1, AtkParam_Pc 0 (status scale 1.0) after a plain
// shot, an unlocked Rain of Arrows and a locked one, yet only the locked Rain of Arrows built status
// from the grease in game. This agent names, per enemy hit, the bullet and the attack, whether the
// buff block ran, and the on-hit SpEffect the reader returned.
//
// Run with the watcher, never a plain frida.attach (AGENTS.md):
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/weapon-buff-bullet-hits.js
//
// Addresses are 1.17.1 and below the 0xafefe9 shift boundary, so 1.17.0 == 1.17.1. Mapped from
// 1.16.2 with scripts/map-rvas-1162-to-1170.py and read on :8767:
//   CalculateDamage2      1.16.2 0x1404483b0 -> 0x140448910
//   buff reader call site 1.16.2 0x140448dd5 -> 0x140449335 (CALL 0x1404f7fb0)
//   FUN_1404f71e0         1.16.2 0x1404f71e0 -> 0x1404f7fb0 (returns atkOccurrenceSpEffectId or -1)
//   FUN_1403e8c90         1.16.2 0x1403e8c90 -> 0x1403e8e70 (apply SpEffect id to victim)
// AttackDamageInfo fields (1.16.2 struct, the 1.17 disassembly reads the same +0x13c/+0x140/+0x228):
//   +0x40 attackParamId, +0x10c bulletId, +0xda hit context byte, +0x13c / +0x140 status scale,
//   +0x228 damage, +0x267 bit 3 skips the whole SpEffect block.
//
// Uses only Interceptor (no watchpoints, nothing to unset), so a detach leaves nothing behind.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const CALC_DAMAGE2 = game.base.add(ptr('0x140448910').sub(BASE));
const BUFF_READER = game.base.add(ptr('0x1404f7fb0').sub(BASE));
const APPLY_SP = game.base.add(ptr('0x1403e8e70').sub(BASE));

// Hits on the bullets in question. Everything else is counted but not logged line by line.
const WATCH_ATK = new Set([0, 5036300, 5036310, 5036400, 5036410, 5036850, 5036855]);

const perThread = {};
// Hits on other attacks are only counted, flushed every FLUSH_EVERY hits and on dispose.
const FLUSH_EVERY = 200;
let counts = {};
let total = 0;

function rd(p, off, kind) {
    try {
        const a = p.add(off);
        if (kind === 'u8') return a.readU8();
        if (kind === 'f32') return a.readFloat();
        return a.readS32();
    } catch (e) {
        return null;
    }
}

const hooks = [];

hooks.push(Interceptor.attach(CALC_DAMAGE2, {
    onEnter(args) {
        const adi = args[2];
        const info = {
            dealer: args[1].isNull() ? null : args[1].toString(),
            atk: rd(adi, 0x40, 's32'),
            bullet: rd(adi, 0x10c, 's32'),
            ctx: rd(adi, 0xda, 'u8'),
            scale_rate: rd(adi, 0x13c, 'f32'),
            scale_point: rd(adi, 0x140, 'f32'),
            damage: rd(adi, 0x228, 's32'),
            skip_block: (rd(adi, 0x267, 'u8') & 8) !== 0,
            reader_ran: false,
            on_hit: null,
            applied: [],
        };
        perThread[this.threadId] = info;
    },
    onLeave() {
        const info = perThread[this.threadId];
        delete perThread[this.threadId];
        if (!info) return;
        const key = info.atk + '/' + info.bullet;
        counts[key] = (counts[key] || 0) + 1;
        total += 1;
        if (total % FLUSH_EVERY === 0) {
            send({ kind: 'counts', counts: counts });
            counts = {};
        }
        if (WATCH_ATK.has(info.atk)) send(Object.assign({ kind: 'hit', t: Date.now() }, info));
    },
}));

hooks.push(Interceptor.attach(BUFF_READER, {
    onLeave(ret) {
        const info = perThread[this.threadId];
        if (!info) return;
        info.reader_ran = true;
        info.on_hit = ret.toInt32();
    },
}));

hooks.push(Interceptor.attach(APPLY_SP, {
    onEnter(args) {
        const info = perThread[this.threadId];
        if (!info) return;
        info.applied.push(args[1].toInt32());
    },
}));

send({ kind: 'armed', calc_damage2: CALC_DAMAGE2.toString(), reader: BUFF_READER.toString() });

rpc.exports = {
    dispose() {
        send({ kind: 'counts', counts: counts });
        hooks.forEach(function (h) { h.detach(); });
    },
};
