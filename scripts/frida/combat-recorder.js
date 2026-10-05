// Combat recorder: what every character near the player does, from the local game's own memory,
// as training data for the simulated-player AI (docs/er-mechanics/simulated-player-ai.md).
// Peers in a Seamless session are recorded from what their game already sends ours; nothing here
// reads a Steam id or a character name. Characters are numbered per session in the order first seen.
//
//   anim   every new entry in a character's TimeAct queue (a roll, a swing, a heal, a stagger),
//          with where it stands, which way it faces, its distance to the player and its HP / FP /
//          stamina at that moment
//   hit    every hit between two recorded characters: who, whom, damage, distance, and how far into
//          its animation the attacker was
//   char   every `charEveryMs`, the build of each player character (level, attributes, two-hand
//          state, weapons and armour) and the NpcParam of everything else
//
// Passive: no hook here changes what the game does.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const cfg = Object.assign({ radius: 40, charEveryMs: 5000 }, globalThis.__ER_FRIDA_CONFIG || {});

const FRAME_TICK = va('0x140773900');
const WORLD_CHR_MAN = va('0x143d69ff8');
const CALC_DAMAGE2 = va('0x140448910');
const MAIN_PLAYER = 0x1e508;
// fromsoftware-rs WorldChrMan: chr_sets[196] directly below null_chr_set, player_grid_area and
// main_player; ChrSet capacity +0x10, entries +0x18, 16-byte entries with the ChrIns first.
const CHR_SETS = MAIN_PLAYER - 0x10 - 196 * 8;

// ChrIns (fromsoftware-rs cs/chr_ins.rs): +0x60 npc_param_id, +0x68 chr_type, +0x6c team_type,
// +0x190 module container, +0x580 PlayerGameData on a PlayerIns only.
const NPC_PARAM = 0x60;
const CHR_TYPE = 0x68;
const TEAM_TYPE = 0x6c;
const MODULES = 0x190;
const PLAYER_GAME_DATA = 0x580;
// Module container: +0x0 data, +0x18 time_act, +0x68 physics.
// CSChrDataModule: hp +0x138, max hp +0x13c, fp +0x148, max fp +0x14c, stamina +0x154, max +0x158.
// CSChrTimeActModule: anim_queue +0x20 (ten {anim_id, play_time, play_time2, anim_length}),
// write_idx +0xc0. CSChrPhysicsModule: orientation quaternion +0x60, position +0x70.
const TA_QUEUE = 0x20;
const TA_WRITE = 0xc0;
// PlayerGameData: vigor..arcane +0x3c..+0x58, level +0x68, EquipGameData +0x2b0. In that, ChrAsm
// starts +0x6c: arm_style +0x8 (0 empty, 1 one hand, 2 left two hands, 3 right two hands),
// selected weapon slots +0xc, equipment param ids +0x7c (22: L1 R1 L2 R2 L3 R3, 6 ammo, head chest
// hands legs, unused, 4 talismans, covenant).
const PGD_ATTRS = 0x3c;
const PGD_LEVEL = 0x68;
const CHR_ASM = 0x2b0 + 0x6c;
// Chr types that are a PlayerIns (local, phantoms, invaders, hunters); 7 is not one
// (bd chrtype-7-is-not-a-player-wide-sweep-must-be-allow-list-2026-08-25).
const PLAYER_TYPES = new Set([0, 1, 2, 8, 13, 15, 16, 17, 18]);

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now() }, fields));
}

function allChrs() {
    const wcm = WORLD_CHR_MAN.readPointer();
    const out = [];
    if (wcm.isNull()) return out;
    for (let s = 0; s < 196; s++) {
        const set = wcm.add(CHR_SETS + s * 8).readPointer();
        if (set.isNull()) continue;
        const cap = set.add(0x10).readU32();
        const entries = set.add(0x18).readPointer();
        if (entries.isNull() || cap > 4096) continue;
        for (let i = 0; i < cap; i++) {
            const chr = entries.add(i * 16).readPointer();
            if (!chr.isNull()) out.push(chr);
        }
    }
    return out;
}

function mainPlayer() {
    const wcm = WORLD_CHR_MAN.readPointer();
    if (wcm.isNull()) return null;
    const p = wcm.add(MAIN_PLAYER).readPointer();
    return p.isNull() ? null : p;
}

const r2 = (x) => Math.round(x * 100) / 100;

function yawOf(q) {
    const [x, y, z, w] = q;
    return Math.atan2(2 * (w * y + x * z), 1 - 2 * (x * x + y * y));
}

function poseOf(chr) {
    const ph = chr.add(MODULES).readPointer().add(0x68).readPointer();
    const p = ph.add(0x70);
    const q = ph.add(0x60);
    return {
        pos: [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()],
        yaw: yawOf([q.readFloat(), q.add(4).readFloat(), q.add(8).readFloat(), q.add(12).readFloat()]),
    };
}

function vitals(chr) {
    const d = chr.add(MODULES).readPointer().readPointer();
    return {
        hp: [d.add(0x138).readS32(), d.add(0x13c).readS32()],
        fp: [d.add(0x148).readS32(), d.add(0x14c).readS32()],
        sp: [d.add(0x154).readS32(), d.add(0x158).readS32()],
    };
}

function timeAct(chr) {
    return chr.add(MODULES).readPointer().add(0x18).readPointer();
}

function animAt(ta, i) {
    const e = ta.add(TA_QUEUE + (i % 10) * 16);
    return { anim: e.readS32(), at: r2(e.add(4).readFloat()), len: r2(e.add(12).readFloat()) };
}

function isPlayer(chr) {
    return PLAYER_TYPES.has(chr.add(CHR_TYPE).readS32());
}

function build(chr) {
    const pgd = chr.add(PLAYER_GAME_DATA).readPointer();
    const attrs = Array.from({ length: 8 }, (_, i) => pgd.add(PGD_ATTRS + i * 4).readU32());
    const asm = pgd.add(CHR_ASM);
    const ids = Array.from({ length: 22 }, (_, i) => asm.add(0x7c + i * 4).readS32());
    return {
        level: pgd.add(PGD_LEVEL).readU32(),
        attrs: { vig: attrs[0], min: attrs[1], end: attrs[2], str: attrs[3], dex: attrs[4], int: attrs[5], fai: attrs[6], arc: attrs[7] },
        armStyle: asm.add(0x8).readU32(),
        slotL: asm.add(0xc).readU32(),
        slotR: asm.add(0x10).readU32(),
        weaponsL: [ids[0], ids[2], ids[4]],
        weaponsR: [ids[1], ids[3], ids[5]],
        armour: ids.slice(12, 16),
        talismans: ids.slice(17, 21),
    };
}

// Characters by address, numbered in the order first seen. A freed address reused by a new
// character keeps the old number only until its chr_type or NpcParam differs.
const seen = new Map();
let nextId = 1;
function idOf(chr) {
    const k = chr.toString();
    const sig = chr.add(CHR_TYPE).readS32() + ':' + chr.add(NPC_PARAM).readS32();
    let e = seen.get(k);
    if (e === undefined || e.sig !== sig) {
        e = { id: nextId++, sig: sig };
        seen.set(k, e);
    }
    return e;
}

function describe(chr, self) {
    const e = idOf(chr);
    return {
        id: e.id,
        who: chr.equals(self) ? 'self' : isPlayer(chr) ? 'player' : 'npc',
        chrType: chr.add(CHR_TYPE).readS32(),
        team: chr.add(TEAM_TYPE).readU8(),
        npcParam: chr.add(NPC_PARAM).readS32(),
    };
}

let frame = 0;
let charAt = 0;
let errors = 0;

function tick() {
    const self = mainPlayer();
    if (self === null) return;
    frame += 1;
    const me = poseOf(self).pos;
    const now = Date.now();
    const doChar = now - charAt >= cfg.charEveryMs;
    if (doChar) charAt = now;
    for (const chr of allChrs()) {
        try {
            const pose = poseOf(chr);
            const d = Math.hypot(pose.pos[0] - me[0], pose.pos[1] - me[1], pose.pos[2] - me[2]);
            if (d > cfg.radius) continue;
            const e = idOf(chr);
            const ta = timeAct(chr);
            // write_idx moves on most frames without a new animation, so a new one is a different
            // newest anim id, or the same id with its play time gone back (a second roll in a row).
            const cur = animAt(ta, (ta.add(TA_WRITE).readU32() + 9) % 10);
            const player = isPlayer(chr);
            const fresh = e.anim !== undefined && (cur.anim !== e.anim || cur.at < e.at - 0.1);
            e.anim = cur.anim;
            e.at = cur.at;
            if (fresh) {
                emit('anim', Object.assign(describe(chr, self), cur, {
                    f: frame, pos: pose.pos.map(r2), yaw: r2(pose.yaw), d: r2(d),
                    two: player ? chr.add(PLAYER_GAME_DATA).readPointer().add(CHR_ASM + 0x8).readU32() >= 2 : null,
                }, vitals(chr)));
            }
            if (doChar) {
                emit('char', Object.assign(describe(chr, self), { f: frame, d: r2(d) }, vitals(chr),
                    player ? build(chr) : {}));
            }
        } catch (err) {
            errors += 1;
            if (errors <= 5 || errors % 1000 === 0) emit('error', { where: 'tick', n: errors, error: err.message });
        }
    }
}

const hooks = [];
hooks.push(Interceptor.attach(FRAME_TICK, { onEnter() { tick(); } }));

// CalcDamage2 (chainsaw-driver.js, measured): attacker is argument 1, the victim [argument 0 + 0x8],
// and on return the damage info (argument 2) holds the HP the hit takes off at +0x228.
const DAMAGE_HP = 0x228;
const pending = {};
const damageAt = CALC_DAMAGE2.readU8() === 0xe9 ? CALC_DAMAGE2.add(5).add(CALC_DAMAGE2.add(1).readS32()) : CALC_DAMAGE2;
hooks.push(Interceptor.attach(damageAt, {
    onEnter(args) {
        pending[this.threadId] = { victim: args[0].add(8).readPointer(), attacker: args[1], info: args[2] };
    },
    onLeave() {
        const h = pending[this.threadId];
        delete pending[this.threadId];
        if (h === undefined) return;
        try {
            const self = mainPlayer();
            if (self === null) return;
            const me = poseOf(self).pos;
            const a = poseOf(h.attacker).pos;
            const v = poseOf(h.victim).pos;
            if (Math.hypot(v[0] - me[0], v[1] - me[1], v[2] - me[2]) > cfg.radius) return;
            const ta = timeAct(h.attacker);
            emit('hit', {
                f: frame,
                attacker: describe(h.attacker, self),
                victim: describe(h.victim, self),
                damage: h.info.add(DAMAGE_HP).readS32(),
                d: r2(Math.hypot(a[0] - v[0], a[1] - v[1], a[2] - v[2])),
                attackerAnim: animAt(ta, (ta.add(TA_WRITE).readU32() + 9) % 10),
                victimHp: vitals(h.victim).hp,
            });
        } catch (err) {
            errors += 1;
            if (errors <= 5 || errors % 1000 === 0) emit('error', { where: 'hit', n: errors, error: err.message });
        }
    },
}));

// One read of the local player through both paths, so a wrong offset shows at once: the data
// module's HP and the PlayerGameData copy should agree, and arm_style should be 0..3.
(function selfCheck() {
    try {
        const self = mainPlayer();
        if (self === null) { emit('self-check', { ok: false, why: 'no player' }); return; }
        const pgd = self.add(PLAYER_GAME_DATA).readPointer();
        const v = vitals(self);
        const b = build(self);
        emit('self-check', { ok: pgd.add(0x10).readU32() === v.hp[0] && b.armStyle <= 3,
            pgdHp: pgd.add(0x10).readU32(), dataHp: v.hp[0], chrType: self.add(CHR_TYPE).readS32(), ...b });
    } catch (err) {
        emit('self-check', { ok: false, error: err.message });
    }
})();

emit('armed', { cfg: cfg, hooks: hooks.length });

rpc.exports = {
    dispose() { hooks.forEach((h) => h.detach()); },
};
