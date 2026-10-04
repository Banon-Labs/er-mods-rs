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
        // 1.17.1: RCX is the victim's CSChrDamageModule (victim ChrIns at +0x8), RDX the dealer,
        // R8 the ADI, whose +0x1d8 is the attacking bullet instance.
        let victim = null;
        let bulletIns = null;
        try { victim = args[0].add(8).readPointer().toString(); } catch (e) { victim = null; }
        try { bulletIns = args[2].add(0x1d8).readPointer().toString(); } catch (e) { bulletIns = null; }
        const info = {
            victim: victim,
            bullet_ins: bulletIns,
            dealer: args[1].isNull() ? null : args[1].toString(),
            atk: rd(adi, 0x40, 's32'),
            bullet: rd(adi, 0x10c, 's32'),
            // Launch-time source: equip slot (+0x48) and the weapon in the launching hand (+0x144).
            // The proposed fix compares +0x144 with the weapon now in the buff's hand.
            slot: rd(adi, 0x48, 's32'),
            hand_weapon: rd(adi, 0x144, 's32'),
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
        // Any hit the reader answered with a buff row is a carrier, whatever its attack id, so
        // Familial Rancor's spirits and carriers nobody has named yet are logged too.
        if (WATCH_ATK.has(info.atk) || (info.on_hit !== null && info.on_hit > 0)) {
            send(Object.assign({ kind: 'hit', t: Date.now() }, info));
        }
    },
}));

// The proposed fix, as a prototype. When FIX is true a hand-tagged buff's on-hit row is refused
// unless ADI+0x144 (the weapon that launched the hit) is the weapon now in that buff's hand.
// Prototype limits: it refuses the first passing entry instead of walking on to the next, and it
// skips the 10 on-hit ids that buffs of both hands share (880, 882, 1724, ...).
const FIX = false;
// atkOccurrenceSpEffectId -> hand of every stateInfo 152/153 row with wepParamChange 1/5 (R) or
// 2/6 (L), from the 1.17.1 regulation; ids shared by both hands are left out.
const ON_HIT_HAND = {"1511": "R", "1521": "R", "1756": "R", "1759": "L", "3141": "R", "3143": "R", "3145": "L", "3147": "L", "3151": "R", "3153": "R", "3155": "L", "3157": "L", "3176": "R", "3178": "R", "3180": "L", "3182": "L", "3191": "R", "3193": "R", "3195": "L", "3197": "L", "3311": "R", "3313": "R", "3315": "L", "3317": "L", "102311": "R", "102313": "R", "102315": "L", "102317": "L", "1449001": "R", "1626001": "R", "1632001": "R", "1723001": "R"};
// PlayerIns::GetEquipmentEntryParamId, 1.16.2 0x140656960 -> 1.17.1 0x1406577b0 (read: slot
// -6..11, else -1; reads PlayerIns+0x638), and the main player at WorldChrMan +0x1e508.
const GET_EQUIP = new NativeFunction(game.base.add(ptr('0x1406577b0').sub(BASE)), 'int', ['pointer', 'int']);
const WORLD_CHR_MAN = game.base.add(0x3d69ff8);

function mainPlayer() {
    try {
        const wcm = WORLD_CHR_MAN.readPointer();
        return wcm.isNull() ? null : wcm.add(0x1e508).readPointer();
    } catch (e) {
        return null;
    }
}

hooks.push(Interceptor.attach(BUFF_READER, {
    onEnter() {
        // At the call in CalculateDamage2, RSI is the attacker and R14 the ADI; both are
        // callee-saved, so they are still the caller's at entry.
        this.attacker = this.context.rsi;
        this.adi = this.context.r14;
    },
    onLeave(ret) {
        const info = perThread[this.threadId];
        if (!info) return;
        info.reader_ran = true;
        info.on_hit = ret.toInt32();
        const hand = ON_HIT_HAND[String(info.on_hit)];
        if (!hand) return;
        const player = mainPlayer();
        if (player === null || !this.attacker.equals(player)) return;
        const launch = rd(this.adi, 0x144, 's32');
        const held = GET_EQUIP(player, hand === 'R' ? -1 : -2);
        info.buff_hand = hand;
        info.launch_weapon = launch;
        info.held_weapon = held;
        info.fix_refuses = launch !== held;
        if (FIX && info.fix_refuses) {
            ret.replace(ptr(-1));
            info.refused = true;
        }
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
