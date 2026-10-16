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
        key: '1', name: 'p', isLocal: false, level: 125, endurance: 0, attributes: [0, 0, 0, 0, 0, 0, 0, 0], localSettings: null,
        armStyle: 1, leftSlot: 0, rightSlot: 0,
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

test('spinning slash is judged on the weapon that used it', () => {
    const HEFTY_SCIMITAR = 8530000, CURVED_GREATSWORD = 11;
    const f = facts({ [HEFTY_SCIMITAR]: w(CURVED_GREATSWORD, 15), [T.weapon.IDUS_SWORD]: w(T.wepType.LIGHT_GREATSWORD, 7) });
    const spun = (p, clip) => rules(Object.assign({}, p, { newAnims: [(600 + T.skill.SPINNING_SLASH) * 1000000 + clip] }), f)
        .includes('SpinningSlashLightGreatsword');
    // Measured live 2026-10-16: two-handing the Hefty Scimitar, nothing in the left hand.
    const p = player({ armStyle: 3, weaponsRight: [HEFTY_SCIMITAR, -1, -1] });
    assert.ok(!spun(p, 40200));
    p.weaponsLeft = [T.weapon.IDUS_SWORD, -1, -1];
    assert.ok(!spun(p, 40200), 'an idle offhand Light Greatsword did not spin');
    p.armStyle = 1;
    assert.ok(!spun(p, 40200));
    p.weaponsRight = [T.weapon.IDUS_SWORD, -1, -1];
    assert.ok(spun(p, 40000));
    p.weaponsRight = [HEFTY_SCIMITAR, -1, -1]; p.armStyle = 2;
    assert.ok(spun(p, 40000));
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
    const got = rules(player({ speffects: [T.bodyBuffs[0][0], T.heals[0][0], T.spiritAshTriggers[0][0]], physick: [T.bannedTears[0][0], -1, -1] }), facts());
    assert.ok(got.includes('PlayerBuff') && got.includes('Healing') && got.includes('SpiritAshes'));
});

test('a grease on a weapon that takes none was carried there', () => {
    const FIRE_LONGSWORD = LONGSWORD + 400;
    const grease = T.greaseBuffs[0];
    const effects = { [grease]: { category: 162, equipWeightRate: 1, addEndurance: 0 } };
    const p = player({ weaponsRight: [FIRE_LONGSWORD, -1, -1], speffects: [grease] });
    assert.ok(rules(p, facts({ [FIRE_LONGSWORD]: w(3, 3.5, { takesGrease: false }) }, effects)).includes('BuffTransfer'));
    assert.ok(!rules(p, facts({ [FIRE_LONGSWORD]: w(3, 3.5, { takesGrease: true }) }, effects)).includes('BuffTransfer'));
});

test('a status skill from a two-handed club breaks the status ban', () => {
    const [status, name] = T.statusSkills[0];
    const f = facts({ [T.weapon.STONE_CLUB]: w(21, 9) });
    const p = player({ weaponsRight: [T.weapon.STONE_CLUB, -1, -1], newAnims: [(600 + status) * 1000000 + 40000] });
    assert.ok(!rules(p, f).includes('StoneClubStatus'), 'one-handed is legal');
    p.armStyle = 3;
    assert.ok(A.check(p, f).some((v) => v.rule === 'StoneClubStatus' && v.detail === name));
});

test('local settings break the rules they name', () => {
    const s = { manualAttackAiming: true, betterMultiplayer: false, targetFps: 60, measuredFps: 41 };
    assert.deepStrictEqual(rules(player({ localSettings: s }), facts()), [], "the aiming rule is Better Multiplayer's");
    s.betterMultiplayer = true;
    assert.deepStrictEqual(rules(player({ localSettings: s }), facts()), ['ManualAttackAiming']);
    s.manualAttackAiming = false; s.targetFps = 120;
    assert.deepStrictEqual(rules(player({ localSettings: s }), facts()), ['UnlockedFps']);
});

test('a build is fixed once an opponent shares the world', () => {
    const r = new A.Referee();
    const f = facts();
    const p = player({ key: '1', attributes: [40, 20, 25, 20, 40, 9, 9, 7], weaponsRight: [LONGSWORD, -1, -1] });
    const other = player({ key: '2' });
    const ruleOf = (alerts) => alerts.map((a) => a.rule);
    r.observe(0, [p], f);
    p.weaponsRight[0] = ESTOC;
    assert.deepStrictEqual(r.observe(100, [p], f), [], 'alone: preparation');
    assert.deepStrictEqual(r.observe(200, [p, other], f), []);
    p.weaponsRight[1] = LONGSWORD;
    let a = r.observe(300, [p, other], f);
    assert.deepStrictEqual(ruleOf(a), ['SwitchingBuilds']);
    assert.strictEqual(a[0].detail, `right weapon 2: -1 -> ${LONGSWORD}`);
    p.weaponsRight[1] = -1; p.attributes[0] = 45;
    a = r.observe(400, [p, other], f);
    assert.deepStrictEqual(ruleOf(a), ['Respec']);
    assert.strictEqual(a[0].detail, 'Vigor 40 -> 45');
    r.observe(500, [p], f);
    assert.deepStrictEqual(r.observe(600, [p, other], f), [], 'a new opponent, a new match');
});

test('armor taken off and put back is the poise reset', () => {
    const r = new A.Referee();
    const f = facts();
    const p = player({ key: '1', armor: [HEAVY_ARMOR, HEAVY_ARMOR, -1, -1] });
    const other = player({ key: '2' });
    r.observe(0, [p, other], f);
    p.armor[0] = -1;
    assert.deepStrictEqual(r.observe(100, [p, other], f).map((a) => a.rule), ['SwitchingBuilds']);
    p.armor[0] = HEAVY_ARMOR;
    assert.deepStrictEqual(r.observe(200, [p, other], f).map((a) => a.rule), ['ArmorSwapPoiseReset']);
    assert.deepStrictEqual(r.observe(300, [p, other], f), []);
});

test('in Better Multiplayer everyone lights a Rune Arc', () => {
    const r = new A.Referee();
    const f = facts();
    const GRACE = 20000;
    const me = player({ key: '1', isLocal: true, runeArcFlag: true,
        localSettings: { manualAttackAiming: false, betterMultiplayer: true, targetFps: 60, measuredFps: 60 } });
    const other = player({ key: '2' });
    r.observe(0, [me, other], f);
    assert.deepStrictEqual(r.observe(GRACE - 1, [me, other], f), [], 'still time to light one');
    const a = r.observe(GRACE, [me, other], f);
    assert.deepStrictEqual(a.map((x) => [x.rule, x.key]), [['RuneArc', '2']]);
    other.speffects = [T.runeArcEffects[0][0]];
    assert.deepStrictEqual(r.observe(GRACE + 100, [me, other], f), [], 'a Great Rune effect row counts as lit');
    me.localSettings = null; other.speffects = [];
    assert.deepStrictEqual(r.observe(GRACE * 3, [me, other], f), [], 'outside Better Multiplayer nobody is asked to');
});

test('a weapon buff kept across a weapon change is a buff transfer', () => {
    const r = new A.Referee();
    const buff = 9001;
    const f = facts({}, { [buff]: { category: 162, equipWeightRate: 1, addEndurance: 0 } });
    const p = player({ isLocal: true, weaponsRight: [LONGSWORD, ESTOC, -1], speffects: [buff] });
    r.observe(0, [p], f);
    p.rightSlot = 1; p.speffects = [];
    r.observe(16, [p], f);
    assert.deepStrictEqual(r.observe(400, [p], f), [], 'the game stripped it');
    p.rightSlot = 0; p.speffects = [buff];
    r.observe(500, [p], f);
    p.rightSlot = 1;
    assert.deepStrictEqual(r.observe(516, [p], f), [], 'grace');
    assert.deepStrictEqual(r.observe(800, [p], f).map((a) => a.rule), ['BuffTransfer']);
});

test('spell bullets name the spell', () => {
    const [spell, bullets] = T.spellBullets[0];
    assert.deepStrictEqual(A.check(player({ bullets: [bullets[0]] }), facts()),
        [{ rule: 'BannedSpell', detail: spell, onEquipment: false, event: false }]);
});

test('light roll is a roll at or under thirty percent', () => {
    const f = facts({ [LONGSWORD]: w(3, 3.5) });
    const p = player({ endurance: 40, weaponsRight: [LONGSWORD, -1, -1], armor: [-1, -1, -1, -1] });
    assert.ok(!rules(p, f).includes('LightRoll'), 'light, but no roll');
    p.newAnims = [27100];
    assert.ok(rules(p, f).includes('LightRoll'));
    p.armor[0] = HEAVY_ARMOR;
    assert.ok(!rules(p, f).includes('LightRoll'), 'a roll at medium load');
    assert.strictEqual(A.equipLoad(player({ weaponsRight: [LONGSWORD, -1, -1] }), facts()), null, 'unknown item -> no load');
});

test('a lasting break pops once, and again only after it was gone', () => {
    const r = new A.Referee();
    const f = facts();
    const p = player({ level: 150 });
    const opponent = player({ key: '8', name: 'opponent' });
    const both = () => [p, opponent];
    assert.strictEqual(r.observe(0, both(), f).length, 1);
    for (let t = 16; t < 30000; t += 16) assert.strictEqual(r.observe(t, both(), f).length, 0);
    p.level = 125; r.observe(30000, both(), f);
    p.level = 150; assert.strictEqual(r.observe(30500, both(), f).length, 0, 'gone only 0.5 s');
    p.level = 125; r.observe(31000, both(), f);
    p.level = 150; assert.strictEqual(r.observe(33000, both(), f).length, 1);
});

test('equipment is judged once an opponent shares the world, and acts always', () => {
    const r = new A.Referee();
    const f = facts();
    const p = player({ level: 9, isLocal: true });
    assert.strictEqual(r.observe(0, [p], f).length, 0);
    p.speffects = [T.spiritAshTriggers[0][0]];
    assert.deepStrictEqual(r.observe(100, [p], f).map((a) => a.rule), ['SpiritAshes']);
    p.speffects = [];
    assert.deepStrictEqual(r.observe(200, [p, player({ key: '8', name: 'opponent' })], f).map((a) => a.rule), ['RuneLevel']);
});

test('every use of a banned skill is its own break', () => {
    const r = new A.Referee();
    const p = player({});
    let uses = 0;
    for (const t of [0, 300, 600]) {
        p.newAnims = [(600 + T.skill.BLOODHOUNDS_STEP) * 1000000];
        uses += r.observe(t, [p], facts()).length;
        p.newAnims = [];
        r.observe(t + 16, [p], facts());
    }
    assert.strictEqual(uses, 3);
});

test('a tear equipped waits for a match, and a tear drunk does not', () => {
    const p = player({ physick: [11007, -1, -1] });
    assert.deepStrictEqual(A.check(p, facts()), [{ rule: 'Healing', detail: 'Crimson Bubbletear', onEquipment: true, event: false }]);
    p.physick = [-1, -1, -1];
    p.speffects = [3512];
    assert.deepStrictEqual(A.check(p, facts()), [{ rule: 'Healing', detail: 'Crimson Bubbletear', onEquipment: false, event: false }]);
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
    const a = { player: 'Vagabond', isLocal: true, rule: 'RuneLevel' };
    assert.strictEqual(A.headline(a, 'The Nut Wizard'), 'Attention: Rule break: Vagabond (The Nut Wizard). Rune Level: 125');
    assert.strictEqual(A.headline(a, null), 'Attention: Rule break: Vagabond (you). Rune Level: 125');
});

test('a break is filed as the DLL files it (feed::Entry::file_line)', () => {
    const when = { year: 2026, month: 10, day: 16, hour: 16, minute: 12, second: 5 };
    assert.strictEqual(A.breakLine(when, { player: 'Lil Chud', isLocal: true, rule: 'SpiritAshes', detail: 'Noble Sorcerer Ashes' }, 'The Nut Wizard'),
        '2026-10-16 16:12:05  Lil Chud (The Nut Wizard)  Spirit Ashes  --  Noble Sorcerer Ashes');
    assert.strictEqual(A.breakLine(when, { player: 'Opponent', isLocal: false, rule: 'SpiritAshes', detail: '' }, 'The Nut Wizard'),
        '2026-10-16 16:12:05  Opponent  Spirit Ashes');
});

test('the local player is named by Steam, and marked (you) only without it (feed::player_label)', () => {
    assert.strictEqual(A.playerLabel('Lion Stronk', true, 'The Nut Wizard'), 'Lion Stronk (The Nut Wizard)');
    assert.strictEqual(A.playerLabel('Lion Stronk', true, ''), 'Lion Stronk (you)');
    assert.strictEqual(A.playerLabel('Opponent', false, 'The Nut Wizard'), 'Opponent');
});

// -- your own rules: the cases the Cheat Engine port runs too ----------------------------------
const BANS = JSON.parse(require('fs').readFileSync(path.join(__dirname, 'test-er-duel-rules-bans.json'), 'utf8'));
const fixtureFacts = (() => {
    const weapons = {};
    for (const [id, wf] of Object.entries(BANS.facts.weapons)) weapons[id] = w(wf.wepType);
    return facts(weapons, BANS.facts.effects);
})();
for (const c of BANS.cases) {
    test(`rules file: ${c.name}`, () => {
        const { config, problems } = A.readConfig(c.configText !== undefined ? c.configText : JSON.stringify(c.config));
        assert.strictEqual(problems.length, c.problems || 0, problems.join('; '));
        let referee = new A.Referee(config);
        const got = [];
        for (const step of c.steps) {
            if (!c.oneReferee) referee = new A.Referee(config);
            const players = [player(Object.assign({ key: '1' }, step.player))];
            if (step.match) players.push(player({ key: '2' }));
            for (const a of referee.observe(step.t, players, fixtureFacts)) {
                if (a.key === '1') got.push({ rule: a.rule, text: a.text, detail: a.detail });
            }
        }
        assert.deepStrictEqual(got, c.expect);
    });
}

test('a rules file reads back the way it was written', () => {
    const C = A.conditionBuilders(A.CATALOG);
    let cfg = A.configWith.ban(A.DEFAULT_CONFIG, 'No 2H Rivers', [C.weapon('rivers of blood', { where: 'twoHanded' })]);
    cfg = A.configWith.rule(cfg, A.ruleKey('Light Roll (<30% Equip Load)'), false);
    cfg = A.configWith.setting(cfg, 'runeLevel', 150);
    const { config, problems } = A.readConfig(A.writeConfig(cfg));
    assert.deepStrictEqual(problems, []);
    assert.deepStrictEqual(config, cfg);
    assert.deepStrictEqual(config.off, ['LightRoll']);
    assert.strictEqual(config.bans[0].all[0].items[0].name, 'Rivers of Blood');
    assert.strictEqual(A.describeConfig(config),
        '48 of 49 built-in rules on (off: LightRoll), runeLevel=150, 1 of 1 bans of your own on');
    // Re-banning a name replaces the ban; turning a rule back on removes it from `off`.
    cfg = A.configWith.ban(cfg, 'No 2H Rivers', [C.talisman("Lord of Blood's Exultation")]);
    assert.strictEqual(cfg.bans.length, 1);
    assert.strictEqual(cfg.bans[0].all[0].kind, 'talisman');
    assert.deepStrictEqual(A.configWith.rule(cfg, 'LightRoll', true).off, []);
});

// The DLL's writer (crates/er-duel-rules-core/src/config.rs) is held to the same bytes, so a file
// saved from its menu and one saved here are the same file.
test('a rules file written here reads back to the very bytes the DLL writes', () => {
    const golden = require('fs').readFileSync(path.join(__dirname, 'test-er-duel-rules-written.json'), 'utf8');
    const { config, problems } = A.readConfig(golden);
    assert.deepStrictEqual(problems, []);
    assert.strictEqual(A.writeConfig(config), golden);
});

test('the catalog finds names and refuses ones it does not have', () => {
    const hits = A.searchCatalog(A.CATALOG, 'katana').map((h) => `${h.pick}: ${h.name}`);
    assert.ok(hits.includes('class: Katana') && hits.includes('class: Great Katana'), hits.join(', '));
    const C = A.conditionBuilders(A.CATALOG);
    assert.throws(() => C.armor('Not An Armour'), /findItem/);
    assert.throws(() => C.weapon(null, { where: 'nowhere' }), /where is one of/);
    const blood = C.weapon(null, { class: 'Katana', affinity: 'Blood' });
    assert.deepStrictEqual([blood.classes[0].ids, blood.affinities[0].ids], [[13], [1100]]);
    assert.ok(C.spell('Comet Azur').items[0].bullets.length > 0);
    assert.deepStrictEqual(C.stat('arcane', '>=', 45), { kind: 'stat', stat: 7, op: '>=', value: 45 });
});

console.log(`test-er-duel-rules-frida: ${passed} passed`);
