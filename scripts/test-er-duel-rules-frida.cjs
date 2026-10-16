#!/usr/bin/env node
// The Frida agent's rule logic (scripts/frida/er-duel-rules.js) against the same cases the Rust
// core proves (crates/er-duel-rules-core), so the shared agent and the DLL cannot drift apart
// unnoticed. The agent exports its pure half when it is not running inside Frida.
//
//   node scripts/test-er-duel-rules-frida.cjs
'use strict';

const path = require('path');
const assert = require('assert');
const A = require(path.join(__dirname, 'frida', 'er-duel-rules.js'));
const { T } = A;

const GRAPH_220 = {
    stageMaxVal: [1, 8, 25, 60, 99],
    stageMaxGrowVal: [45, 45, 72, 120, 160],
    adjPtMaxGrowVal: [1, 1, Math.fround(1.1), 1, 1],
};
const HEAVY_ARMOR = 1;

function facts(weapons = {}, effects = {}, extra = {}) {
    const armor = Object.assign({ [HEAVY_ARMOR]: 40 }, extra.armor || {});
    const talismans = extra.talismans || {};
    const resident = extra.resident || {};
    return {
        weapon: (id) => weapons[id - (id % 100)] || null,
        armorWeight: (id) => (id in armor ? armor[id] : null),
        talismanWeight: (id) => (id in talismans ? talismans[id] : null),
        residentEffects: (_kind, id) => resident[id] || [],
        effect: (id) => effects[id] || null,
        equipLoadBase: (end) => A.correctGraphAt(GRAPH_220, end),
    };
}
const w = (wepType, weight = 3, extra = {}) => Object.assign({ wepType, weight, physGuardCut: 50, inflictsStatus: false }, extra);

function player(over = {}) {
    return Object.assign({
        key: '1', name: 'p', isLocal: false, level: 125, endurance: 0, armStyle: 1, leftSlot: 0, rightSlot: 0,
        weaponsLeft: [-1, -1, -1], weaponsRight: [-1, -1, -1], armor: [HEAVY_ARMOR, -1, -1, -1],
        talismans: [-1, -1, -1, -1], physick: [-1, -1, -1], speffects: [], newAnims: [], bullets: [], hp: 1, riding: false,
    }, over);
}
const rules = (p, f) => A.check(p, f).map((v) => v.rule);
const LONGSWORD = 2000000, ESTOC = 3030000, PARRYING_DAGGER = 1020000;
let passed = 0;
function test(name, fn) { fn(); passed += 1; console.log(`  ok    ${name}`); }

test('graph 220 matches the game at its stage ends', () => {
    assert.strictEqual(A.correctGraphAt(GRAPH_220, 8), 45);
    assert.strictEqual(A.correctGraphAt(GRAPH_220, 99), 160);
    assert.strictEqual(A.correctGraphAt(GRAPH_220, 150), 160);
    assert.strictEqual(A.correctGraphAt(GRAPH_220, 60), 120);
});

test('a clean build breaks nothing', () => {
    const f = facts({ [LONGSWORD]: w(3, 3.5) });
    assert.deepStrictEqual(rules(player({ weaponsRight: [LONGSWORD + 25, -1, -1] }), f), []);
});

test('rune level is exactly 125 and waits for the data', () => {
    assert.deepStrictEqual(rules(player({ level: 150 }), facts()), ['RuneLevel']);
    assert.deepStrictEqual(rules(player({ level: 0 }), facts()), []);
});

test('banned weapons fire from any slot with any affinity', () => {
    const f = facts({ [T.weapon.MILADY + 300]: w(93, 4) });
    assert.ok(rules(player({ weaponsLeft: [-1, -1, T.weapon.MILADY + 325] }), f).includes('Milady'));
});

test('grip bans need the grip', () => {
    const f = facts({ [T.weapon.REVERSE_BLADED_SWORD + 600]: w(92), [T.weapon.CLEANROT_KNIGHTS_SWORD]: w(15) });
    const p = player({ weaponsRight: [T.weapon.REVERSE_BLADED_SWORD + 625, -1, -1] });
    assert.ok(!rules(p, f).includes('LightningReverseBlades'));
    p.armStyle = 3;
    assert.ok(rules(p, f).includes('LightningReverseBlades'));
    p.weaponsRight[0] = T.weapon.CLEANROT_KNIGHTS_SWORD + 10;
    assert.ok(rules(p, f).includes('TwoHandedCleanrot'));
});

test('pata is legal only one-handed in the left hand without powerstance', () => {
    const f = facts({ [T.weapon.PATA]: w(35, 2), [T.weapon.STAR_FIST]: w(35, 2.5), [LONGSWORD]: w(3, 3.5) });
    const p = player({ weaponsRight: [LONGSWORD, -1, -1], weaponsLeft: [T.weapon.PATA, -1, -1] });
    assert.ok(!rules(p, f).includes('PataNotOffhand'));
    p.weaponsRight[0] = T.weapon.STAR_FIST;
    assert.ok(rules(p, f).includes('PataNotOffhand'));
    p.weaponsRight[0] = LONGSWORD; p.armStyle = 2;
    assert.ok(rules(p, f).includes('PataNotOffhand'));
    p.armStyle = 1; p.weaponsRight[0] = T.weapon.PATA;
    assert.ok(rules(p, f).includes('PataNotOffhand'));
});

test('shields block above ninety percent only', () => {
    const KITE = 31000000;
    const p = player({ weaponsLeft: [KITE, -1, -1] });
    assert.ok(rules(p, facts({ [KITE]: w(67, 4, { physGuardCut: 100 }) })).includes('ShieldGuard'));
    assert.ok(!rules(p, facts({ [KITE]: w(67, 4, { physGuardCut: 90 }) })).includes('ShieldGuard'));
});

test('a weapon buff is judged against the hand it sits on', () => {
    const f = facts({ [ESTOC]: w(15), [PARRYING_DAGGER]: w(1, 1.5) }, { 9001: { category: 162, equipWeightRate: 1, addEndurance: 0 },
        9002: { category: 163, equipWeightRate: 1, addEndurance: 0 } });
    const p = player({ weaponsRight: [ESTOC, -1, -1], weaponsLeft: [PARRYING_DAGGER, -1, -1], speffects: [9001] });
    assert.ok(!rules(p, f).includes('WeaponBuffRestricted'));
    p.armStyle = 3;
    assert.ok(A.check(p, f).some((v) => v.detail === 'weapon buff on 2H Thrusting Sword'));
    p.armStyle = 1; p.speffects = [9002];
    assert.ok(A.check(p, f).some((v) => v.detail === 'weapon buff on Offhand Daggers'));
});

test('grease rows know their status', () => {
    const poison = T.poisonGrease[0];
    const f = facts({ [T.weapon.SHAMSHIR]: w(9) }, { [poison]: { category: 162, equipWeightRate: 1, addEndurance: 0 } });
    const p = player({ weaponsRight: [T.weapon.SHAMSHIR, -1, -1], speffects: [poison] });
    assert.ok(!rules(p, f).includes('PoisonGreaseShamshir'));
    p.armStyle = 3;
    assert.ok(rules(p, f).includes('PoisonGreaseShamshir'));
    p.speffects = [T.rotGrease[0]];
    assert.ok(rules(p, f).includes('RotGrease'));
});

test('skills fire on use, not on equip', () => {
    const f = facts();
    const p = player();
    assert.deepStrictEqual(rules(p, f), []);
    p.newAnims = [(600 + T.skill.BLOODHOUNDS_STEP) * 1000000 + 40000];
    assert.deepStrictEqual(A.check(p, f).map((v) => v.detail), ["Bloodhound's Step"]);
    p.newAnims = [(600 + T.skill.SWORD_DANCE) * 1000000 + 40200];
    assert.deepStrictEqual(rules(p, f), []);
    p.newAnims = [(600 + T.skill.SWORD_DANCE) * 1000000 + 40000];
    assert.strictEqual(A.check(p, f)[0].detail, 'Fast Sword Dance');
    p.newAnims = [(600 + T.parrySkills[0][0]) * 1000000 + 40000];
    assert.ok(A.check(p, f)[0].detail.startsWith('All Parries'));
});

const IDLE = 12000000, SKILL = 603040200;
const idleRing = () => Array.from({ length: 10 }, (_, i) => [IDLE, 1 + i * 0.03]);

test('one animation filling the ring starts once', () => {
    const ring = idleRing();
    let { starts, cursor } = A.animStarts(undefined, ring, 0);
    assert.deepStrictEqual(starts, [], 'first sight reports nothing');
    const seen = [];
    for (let frame = 0; frame < 12; frame++) {
        const slot = frame % 10;
        ring[slot] = [SKILL, frame * 0.03];
        ({ starts, cursor } = A.animStarts(cursor, ring, (slot + 1) % 10));
        seen.push(...starts);
    }
    assert.deepStrictEqual(seen, [SKILL]);
});

test('several frames between reads still find the start', () => {
    const ring = idleRing();
    const { cursor } = A.animStarts(undefined, ring, 3);
    ring[3] = [IDLE, 1.5]; ring[4] = [SKILL, 0]; ring[5] = [SKILL, 0.03];
    assert.deepStrictEqual(A.animStarts(cursor, ring, 6).starts, [SKILL]);
});

test('the same animation restarting is a new start, hitstop is not', () => {
    const ring = idleRing();
    ring[0] = [SKILL, 0.4];
    let { cursor } = A.animStarts(undefined, ring, 1);
    ring[1] = [SKILL, 0];
    assert.deepStrictEqual(A.animStarts(cursor, ring, 2).starts, [SKILL]);
    ({ cursor } = A.animStarts(undefined, ring, 2));
    ring[2] = [SKILL, 0];
    assert.deepStrictEqual(A.animStarts(cursor, ring, 3).starts, []);
});

test('a full lap between reads is read as ten frames', () => {
    const ring = idleRing();
    const { cursor } = A.animStarts(undefined, ring, 4);
    assert.deepStrictEqual(A.animStarts(cursor, ring, 4).starts, [], 'nothing written');
    for (let k = 0; k < 10; k++) ring[(4 + k) % 10] = [SKILL, k * 0.03];
    assert.deepStrictEqual(A.animStarts(cursor, ring, 4).starts, [SKILL]);
    assert.deepStrictEqual(A.animStarts(cursor, ring, 10).starts, [], 'an index past the ring reads nothing');
});

test('listed effects and tears fire', () => {
    const got = rules(player({ speffects: [T.bodyBuffs[0][0], T.heals[0][0]], physick: [T.bannedTears[0][0], -1, -1] }), facts());
    assert.ok(got.includes('PlayerBuff') && got.includes('Healing'));
});

test('spell bullets name the spell', () => {
    const [spell, bullets] = T.spellBullets[0];
    assert.deepStrictEqual(A.check(player({ bullets: [bullets[0]] }), facts()), [{ rule: 'BannedSpell', detail: spell }]);
});

test('light roll needs a known load at or under thirty percent', () => {
    const f = facts({ [LONGSWORD]: w(3, 3.5) });
    const p = player({ endurance: 40, weaponsRight: [LONGSWORD, -1, -1], armor: [-1, -1, -1, -1] });
    assert.ok(rules(p, f).includes('LightRoll'));
    p.armor[0] = HEAVY_ARMOR;
    assert.ok(!rules(p, f).includes('LightRoll'));
    assert.strictEqual(A.equipLoad(player({ weaponsRight: [LONGSWORD, -1, -1] }), facts()), null, 'unknown item -> no load');
});

test('a lasting break pops once, and again only after it was gone', () => {
    const r = new A.Referee();
    const f = facts();
    const p = player({ level: 150 });
    assert.strictEqual(r.observe(0, [p], f).length, 1);
    for (let t = 16; t < 30000; t += 16) assert.strictEqual(r.observe(t, [p], f).length, 0);
    p.level = 125; r.observe(30000, [p], f);
    p.level = 150; assert.strictEqual(r.observe(35000, [p], f).length, 0);
    p.level = 125; r.observe(36000, [p], f);
    p.level = 150; assert.strictEqual(r.observe(50000, [p], f).length, 1);
});

test('the third blue flask of a round breaks the rule and a death refills', () => {
    const r = new A.Referee();
    const f = facts();
    const p = player({ key: '1' }), other = player({ key: '2' });
    let now = 0;
    const drink = () => { p.speffects = [T.ceruleanFlask[0]]; now += 100; const a = r.observe(now, [p, other], f); p.speffects = []; now += 100; r.observe(now, [p, other], f); return a; };
    assert.strictEqual(drink().length, 0);
    assert.strictEqual(drink().length, 0);
    const third = drink();
    assert.strictEqual(third.length, 1);
    assert.strictEqual(third[0].rule, 'CeruleanFlasks');
    r.observe(10000, [p, Object.assign({}, other, { hp: 0 })], f);
    assert.strictEqual(r.flasksUsed('1'), 0);
});

test('the headline reads the way the popup does', () => {
    assert.strictEqual(A.headline({ player: 'Vagabond', isLocal: true, rule: 'RuneLevel' }), 'Attention: Rule break: Vagabond (you). Rune Level: 125');
});

console.log(`test-er-duel-rules-frida: ${passed} passed`);
