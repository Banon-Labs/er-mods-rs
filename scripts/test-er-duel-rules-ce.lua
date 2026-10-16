#!/usr/bin/env lua
-- The Cheat Engine port's rule logic (scripts/cheat-engine/er-duel-rules.lua) against the same
-- cases the Frida agent's tests prove (scripts/test-er-duel-rules-frida.cjs), so the three
-- referees cannot drift apart unnoticed. Needs only Lua 5.3 or later; no game, no Cheat Engine.
--
--   lua scripts/test-er-duel-rules-ce.lua

local here = arg and arg[0] and arg[0]:match('^(.*)/') or '.'
local A = dofile(here .. '/cheat-engine/er-duel-rules.lua')
local T = A.T

local passed = 0
local function test(name, fn)
    local ok, err = pcall(fn)
    if not ok then
        io.stderr:write(string.format('  FAIL  %s\n        %s\n', name, tostring(err)))
        os.exit(1)
    end
    passed = passed + 1
    print('  ok    ' .. name)
end

local function same(a, b)
    if type(a) ~= type(b) then return false end
    if type(a) ~= 'table' then return a == b end
    for k, v in pairs(a) do if not same(v, b[k]) then return false end end
    for k in pairs(b) do if a[k] == nil then return false end end
    return true
end
local function show(v)
    if type(v) ~= 'table' then return string.format('%q', tostring(v)) end
    local parts = {}
    for k, x in pairs(v) do parts[#parts + 1] = tostring(k) .. '=' .. show(x) end
    table.sort(parts)
    return '{' .. table.concat(parts, ', ') .. '}'
end
local function eq(got, want, why)
    if not same(got, want) then
        error(string.format('%s: got %s, want %s', why or 'mismatch', show(got), show(want)), 2)
    end
end
local function ok(cond, why) if not cond then error(why or 'expected true', 2) end end
local function has(list, x) for _, v in ipairs(list) do if v == x then return true end end return false end
local function any(list, pred) for _, v in ipairs(list) do if pred(v) then return true end end return false end
local function map(list, f) local out = {} for i, v in ipairs(list) do out[i] = f(v) end return out end
local function merge(base, over) local out = {} for k, v in pairs(base) do out[k] = v end for k, v in pairs(over or {}) do out[k] = v end return out end

local GRAPH_220 = {
    stageMaxVal = { 1, 8, 25, 60, 99 },
    stageMaxGrowVal = { 45, 45, 72, 120, 160 },
    adjPtMaxGrowVal = { 1, 1, A.f32(1.1), 1, 1 },
}
local HEAVY_ARMOR = 1

local function facts(weapons, effects, extra)
    weapons, effects, extra = weapons or {}, effects or {}, extra or {}
    local armor = merge({ [HEAVY_ARMOR] = 40 }, extra.armor)
    local talismans = extra.talismans or {}
    local resident = extra.resident or {}
    return {
        weapon = function(id) return weapons[id - (id % 100)] end,
        armorWeight = function(id) return armor[id] end,
        talismanWeight = function(id) return talismans[id] end,
        residentEffects = function(_, id) return resident[id] or {} end,
        effect = function(id) return effects[id] end,
        equipLoadBase = function(e) return A.correctGraphAt(GRAPH_220, e) end,
    }
end
local function w(wepType, weight, extra)
    return merge({ wepType = wepType, weight = weight or 3, physGuardCut = 50, inflictsStatus = false }, extra)
end
local function buff(category) return { category = category, equipWeightRate = 1, addEndurance = 0 } end

local function player(over)
    return merge({
        key = '1', name = 'p', isLocal = false, level = 125, endurance = 0, attributes = { 0, 0, 0, 0, 0, 0, 0, 0 },
        localSettings = nil, armStyle = 1, leftSlot = 0, rightSlot = 0,
        weaponsLeft = { -1, -1, -1 }, weaponsRight = { -1, -1, -1 }, armor = { HEAVY_ARMOR, -1, -1, -1 },
        talismans = { -1, -1, -1, -1 }, physick = { -1, -1, -1 }, speffects = {}, newAnims = {}, bullets = {}, hp = 1, riding = false,
    }, over)
end
local function rules(p, f) return map(A.check(p, f), function(v) return v.rule end) end
local function details(p, f) return map(A.check(p, f), function(v) return v.detail end) end
local LONGSWORD, ESTOC, PARRYING_DAGGER = 2000000, 3030000, 1020000
local function skillAnim(typeNew, clip) return (600 + typeNew) * 1000000 + clip end

test('graph 220 matches the game at its stage ends', function()
    eq(A.correctGraphAt(GRAPH_220, 8), 45)
    eq(A.correctGraphAt(GRAPH_220, 99), 160)
    eq(A.correctGraphAt(GRAPH_220, 150), 160)
    eq(A.correctGraphAt(GRAPH_220, 60), 120)
end)

test('a clean build breaks nothing', function()
    local f = facts({ [LONGSWORD] = w(3, 3.5) })
    eq(rules(player({ weaponsRight = { LONGSWORD + 25, -1, -1 } }), f), {})
end)

test('rune level is exactly 125 and waits for the data', function()
    eq(rules(player({ level = 150 }), facts()), { 'RuneLevel' })
    eq(rules(player({ level = 0 }), facts()), {})
end)

test('banned weapons fire from any slot with any affinity', function()
    local f = facts({ [T.weapon.MILADY + 300] = w(93, 4) })
    ok(has(rules(player({ weaponsLeft = { -1, -1, T.weapon.MILADY + 325 } }), f), 'Milady'))
end)

test('grip bans need the grip', function()
    local f = facts({ [T.weapon.REVERSE_BLADED_SWORD + 600] = w(92), [T.weapon.CLEANROT_KNIGHTS_SWORD] = w(15) })
    local p = player({ weaponsRight = { T.weapon.REVERSE_BLADED_SWORD + 625, -1, -1 } })
    ok(not has(rules(p, f), 'LightningReverseBlades'))
    p.armStyle = 3
    ok(has(rules(p, f), 'LightningReverseBlades'))
    p.weaponsRight[1] = T.weapon.CLEANROT_KNIGHTS_SWORD + 10
    ok(has(rules(p, f), 'TwoHandedCleanrot'))
end)

test('pata is legal only one-handed in the left hand without powerstance', function()
    local f = facts({ [T.weapon.PATA] = w(35, 2), [T.weapon.STAR_FIST] = w(35, 2.5), [LONGSWORD] = w(3, 3.5) })
    local p = player({ weaponsRight = { LONGSWORD, -1, -1 }, weaponsLeft = { T.weapon.PATA, -1, -1 } })
    ok(not has(rules(p, f), 'PataNotOffhand'))
    p.weaponsRight[1] = T.weapon.STAR_FIST
    ok(has(rules(p, f), 'PataNotOffhand'))
    p.weaponsRight[1] = LONGSWORD; p.armStyle = 2
    ok(has(rules(p, f), 'PataNotOffhand'))
    p.armStyle = 1; p.weaponsRight[1] = T.weapon.PATA
    ok(has(rules(p, f), 'PataNotOffhand'))
end)

test('shields block above ninety percent only', function()
    local KITE = 31000000
    local p = player({ weaponsLeft = { KITE, -1, -1 } })
    ok(has(rules(p, facts({ [KITE] = w(67, 4, { physGuardCut = 100 }) })), 'ShieldGuard'))
    ok(not has(rules(p, facts({ [KITE] = w(67, 4, { physGuardCut = 90 }) })), 'ShieldGuard'))
end)

test('a weapon buff is judged against the hand it sits on', function()
    local f = facts({ [ESTOC] = w(15), [PARRYING_DAGGER] = w(1, 1.5) }, { [9001] = buff(162), [9002] = buff(163) })
    local p = player({ weaponsRight = { ESTOC, -1, -1 }, weaponsLeft = { PARRYING_DAGGER, -1, -1 }, speffects = { 9001 } })
    ok(not has(rules(p, f), 'WeaponBuffRestricted'))
    p.armStyle = 3
    ok(has(details(p, f), 'weapon buff on 2H Thrusting Sword'))
    p.armStyle = 1; p.speffects = { 9002 }
    ok(has(details(p, f), 'weapon buff on Offhand Daggers'))
end)

test('grease rows know their status', function()
    local poison = T.poisonGrease[1]
    local f = facts({ [T.weapon.SHAMSHIR] = w(9) }, { [poison] = buff(162) })
    local p = player({ weaponsRight = { T.weapon.SHAMSHIR, -1, -1 }, speffects = { poison } })
    ok(not has(rules(p, f), 'PoisonGreaseShamshir'))
    p.armStyle = 3
    ok(has(rules(p, f), 'PoisonGreaseShamshir'))
    p.speffects = { T.rotGrease[1] }
    ok(has(rules(p, f), 'RotGrease'))
end)

test('skills fire on use, not on equip', function()
    local f = facts()
    local p = player()
    eq(rules(p, f), {})
    p.newAnims = { skillAnim(T.skill.BLOODHOUNDS_STEP, 40000) }
    eq(details(p, f), { "Bloodhound's Step" })
    p.newAnims = { skillAnim(T.skill.SWORD_DANCE, 40200) }
    eq(rules(p, f), {})
    p.newAnims = { skillAnim(T.skill.SWORD_DANCE, 40000) }
    eq(A.check(p, f)[1].detail, 'Fast Sword Dance')
    p.newAnims = { skillAnim(T.parrySkills[1][1], 40000) }
    ok(A.check(p, f)[1].detail:find('^All Parries') ~= nil)
end)

test('spinning slash is judged on the weapon that used it', function()
    local HEFTY_SCIMITAR, CURVED_GREATSWORD = 8530000, 11
    local f = facts({ [HEFTY_SCIMITAR] = w(CURVED_GREATSWORD, 15), [T.weapon.IDUS_SWORD] = w(T.wepType.LIGHT_GREATSWORD, 7) })
    local function spun(p, clip)
        return has(rules(merge(p, { newAnims = { skillAnim(T.skill.SPINNING_SLASH, clip) } }), f), 'SpinningSlashLightGreatsword')
    end
    local p = player({ armStyle = 3, weaponsRight = { HEFTY_SCIMITAR, -1, -1 } })
    ok(not spun(p, 40200))
    p.weaponsLeft = { T.weapon.IDUS_SWORD, -1, -1 }
    ok(not spun(p, 40200), 'an idle offhand Light Greatsword did not spin')
    p.armStyle = 1
    ok(not spun(p, 40200))
    p.weaponsRight = { T.weapon.IDUS_SWORD, -1, -1 }
    ok(spun(p, 40000))
    p.weaponsRight = { HEFTY_SCIMITAR, -1, -1 }; p.armStyle = 2
    ok(spun(p, 40000))
end)

local IDLE, SKILL = 12000000, 603040200
local function idleRing()
    local ring = {}
    for i = 0, 9 do ring[i + 1] = { IDLE, 1 + i * 0.03 } end
    return ring
end

test('one animation filling the ring starts once', function()
    local ring = idleRing()
    local starts, cursor = A.animStarts(nil, ring, 0)
    eq(starts, {}, 'first sight reports nothing')
    local seen = {}
    for frame = 0, 11 do
        local slot = frame % 10
        ring[slot + 1] = { SKILL, frame * 0.03 }
        starts, cursor = A.animStarts(cursor, ring, (slot + 1) % 10)
        for _, s in ipairs(starts) do seen[#seen + 1] = s end
    end
    eq(seen, { SKILL })
end)

test('several frames between reads still find the start', function()
    local ring = idleRing()
    local _, cursor = A.animStarts(nil, ring, 3)
    ring[4] = { IDLE, 1.5 }; ring[5] = { SKILL, 0 }; ring[6] = { SKILL, 0.03 }
    eq((A.animStarts(cursor, ring, 6)), { SKILL })
end)

test('the same animation restarting is a new start, hitstop is not', function()
    local ring = idleRing()
    ring[1] = { SKILL, 0.4 }
    local _, cursor = A.animStarts(nil, ring, 1)
    ring[2] = { SKILL, 0 }
    eq((A.animStarts(cursor, ring, 2)), { SKILL })
    _, cursor = A.animStarts(nil, ring, 2)
    ring[3] = { SKILL, 0 }
    eq((A.animStarts(cursor, ring, 3)), {})
end)

test('a full lap between reads is read as ten frames', function()
    local ring = idleRing()
    local _, cursor = A.animStarts(nil, ring, 4)
    eq((A.animStarts(cursor, ring, 4)), {}, 'nothing written')
    for k = 0, 9 do ring[((4 + k) % 10) + 1] = { SKILL, k * 0.03 } end
    eq((A.animStarts(cursor, ring, 4)), { SKILL })
    eq((A.animStarts(cursor, ring, 10)), {}, 'an index past the ring reads nothing')
end)

test('listed effects and tears fire', function()
    local got = rules(player({ speffects = { T.bodyBuffs[1][1], T.heals[1][1], T.spiritAshTriggers[1][1] },
        physick = { T.bannedTears[1][1], -1, -1 } }), facts())
    ok(has(got, 'PlayerBuff') and has(got, 'Healing') and has(got, 'SpiritAshes'))
end)

test('a grease on a weapon that takes none was carried there', function()
    local FIRE_LONGSWORD = LONGSWORD + 400
    local grease = T.greaseBuffs[1]
    local effects = { [grease] = buff(162) }
    local p = player({ weaponsRight = { FIRE_LONGSWORD, -1, -1 }, speffects = { grease } })
    ok(has(rules(p, facts({ [FIRE_LONGSWORD] = w(3, 3.5, { takesGrease = false }) }, effects)), 'BuffTransfer'))
    ok(not has(rules(p, facts({ [FIRE_LONGSWORD] = w(3, 3.5, { takesGrease = true }) }, effects)), 'BuffTransfer'))
end)

test('a status skill from a two-handed club breaks the status ban', function()
    local status, name = T.statusSkills[1][1], T.statusSkills[1][2]
    local f = facts({ [T.weapon.STONE_CLUB] = w(21, 9) })
    local p = player({ weaponsRight = { T.weapon.STONE_CLUB, -1, -1 }, newAnims = { skillAnim(status, 40000) } })
    ok(not has(rules(p, f), 'StoneClubStatus'), 'one-handed is legal')
    p.armStyle = 3
    ok(any(A.check(p, f), function(v) return v.rule == 'StoneClubStatus' and v.detail == name end))
end)

test('local settings break the rules they name', function()
    local s = { manualAttackAiming = true, betterMultiplayer = false, targetFps = 60, measuredFps = 41 }
    eq(rules(player({ localSettings = s }), facts()), {}, "the aiming rule is Better Multiplayer's")
    s.betterMultiplayer = true
    eq(rules(player({ localSettings = s }), facts()), { 'ManualAttackAiming' })
    s.manualAttackAiming = false; s.targetFps = 120
    eq(rules(player({ localSettings = s }), facts()), { 'UnlockedFps' })
end)

test('a build is fixed once an opponent shares the world', function()
    local r = A.Referee.new()
    local f = facts()
    local p = player({ key = '1', attributes = { 40, 20, 25, 20, 40, 9, 9, 7 }, weaponsRight = { LONGSWORD, -1, -1 } })
    local other = player({ key = '2' })
    local function ruleOf(alerts) return map(alerts, function(a) return a.rule end) end
    r:observe(0, { p }, f)
    p.weaponsRight[1] = ESTOC
    eq(r:observe(100, { p }, f), {}, 'alone: preparation')
    eq(r:observe(200, { p, other }, f), {})
    p.weaponsRight[2] = LONGSWORD
    local a = r:observe(300, { p, other }, f)
    eq(ruleOf(a), { 'SwitchingBuilds' })
    eq(a[1].detail, string.format('right weapon 2: -1 -> %d', LONGSWORD))
    p.weaponsRight[2] = -1; p.attributes[1] = 45
    a = r:observe(400, { p, other }, f)
    eq(ruleOf(a), { 'Respec' })
    eq(a[1].detail, 'Vigor 40 -> 45')
    r:observe(500, { p }, f)
    eq(r:observe(600, { p, other }, f), {}, 'a new opponent, a new match')
end)

test('armor taken off and put back is the poise reset', function()
    local r = A.Referee.new()
    local f = facts()
    local p = player({ key = '1', armor = { HEAVY_ARMOR, HEAVY_ARMOR, -1, -1 } })
    local other = player({ key = '2' })
    local function ruleOf(alerts) return map(alerts, function(a) return a.rule end) end
    r:observe(0, { p, other }, f)
    p.armor[1] = -1
    eq(ruleOf(r:observe(100, { p, other }, f)), { 'SwitchingBuilds' })
    p.armor[1] = HEAVY_ARMOR
    eq(ruleOf(r:observe(200, { p, other }, f)), { 'ArmorSwapPoiseReset' })
    eq(r:observe(300, { p, other }, f), {})
end)

test('in Better Multiplayer everyone lights a Rune Arc', function()
    local r = A.Referee.new()
    local f = facts()
    local GRACE = 20000
    local me = player({ key = '1', isLocal = true, runeArcFlag = true,
        localSettings = { manualAttackAiming = false, betterMultiplayer = true, targetFps = 60, measuredFps = 60 } })
    local other = player({ key = '2' })
    r:observe(0, { me, other }, f)
    eq(r:observe(GRACE - 1, { me, other }, f), {}, 'still time to light one')
    local a = r:observe(GRACE, { me, other }, f)
    eq(map(a, function(x) return { x.rule, x.key } end), { { 'RuneArc', '2' } })
    other.speffects = { T.runeArcEffects[1][1] }
    eq(r:observe(GRACE + 100, { me, other }, f), {}, 'a Great Rune effect row counts as lit')
    me.localSettings = nil; other.speffects = {}
    eq(r:observe(GRACE * 3, { me, other }, f), {}, 'outside Better Multiplayer nobody is asked to')
end)

test('a weapon buff kept across a weapon change is a buff transfer', function()
    local r = A.Referee.new()
    local b = 9001
    local f = facts({}, { [b] = buff(162) })
    local p = player({ isLocal = true, weaponsRight = { LONGSWORD, ESTOC, -1 }, speffects = { b } })
    r:observe(0, { p }, f)
    p.rightSlot = 1; p.speffects = {}
    r:observe(16, { p }, f)
    eq(r:observe(400, { p }, f), {}, 'the game stripped it')
    p.rightSlot = 0; p.speffects = { b }
    r:observe(500, { p }, f)
    p.rightSlot = 1
    eq(r:observe(516, { p }, f), {}, 'grace')
    eq(map(r:observe(800, { p }, f), function(a) return a.rule end), { 'BuffTransfer' })
end)

test('spell bullets name the spell', function()
    local spell, bullets = T.spellBullets[1][1], T.spellBullets[1][2]
    eq(A.check(player({ bullets = { bullets[1] } }), facts()),
        { { rule = 'BannedSpell', detail = spell, onEquipment = false, event = false } })
end)

test('light roll is a roll at or under thirty percent', function()
    local f = facts({ [LONGSWORD] = w(3, 3.5) })
    local p = player({ endurance = 40, weaponsRight = { LONGSWORD, -1, -1 }, armor = { -1, -1, -1, -1 } })
    ok(not has(rules(p, f), 'LightRoll'), 'light, but no roll')
    p.newAnims = { 27100 }
    ok(has(rules(p, f), 'LightRoll'))
    p.armor[1] = HEAVY_ARMOR
    ok(not has(rules(p, f), 'LightRoll'), 'a roll at medium load')
    eq(A.equipLoad(player({ weaponsRight = { LONGSWORD, -1, -1 } }), facts()), nil, 'unknown item -> no load')
end)

test('a lasting break pops once, and again only after it was gone', function()
    local r = A.Referee.new()
    local f = facts()
    local p = player({ level = 150 })
    local opponent = player({ key = '8', name = 'opponent' })
    local function both() return { p, opponent } end
    eq(#r:observe(0, both(), f), 1)
    for t = 16, 29999, 16 do eq(#r:observe(t, both(), f), 0) end
    p.level = 125; r:observe(30000, both(), f)
    p.level = 150; eq(#r:observe(30500, both(), f), 0, 'gone only 0.5 s')
    p.level = 125; r:observe(31000, both(), f)
    p.level = 150; eq(#r:observe(33000, both(), f), 1)
end)

test('equipment is judged once an opponent shares the world, and acts always', function()
    local r = A.Referee.new()
    local f = facts()
    local p = player({ level = 9, isLocal = true })
    eq(#r:observe(0, { p }, f), 0)
    p.speffects = { T.spiritAshTriggers[1][1] }
    eq(map(r:observe(100, { p }, f), function(a) return a.rule end), { 'SpiritAshes' })
    p.speffects = {}
    eq(map(r:observe(200, { p, player({ key = '8', name = 'opponent' }) }, f), function(a) return a.rule end), { 'RuneLevel' })
end)

test('every use of a banned skill is its own break', function()
    local r = A.Referee.new()
    local p = player({})
    local uses = 0
    for _, t in ipairs({ 0, 300, 600 }) do
        p.newAnims = { skillAnim(T.skill.BLOODHOUNDS_STEP, 0) }
        uses = uses + #r:observe(t, { p }, facts())
        p.newAnims = {}
        r:observe(t + 16, { p }, facts())
    end
    eq(uses, 3)
end)

test('a tear equipped waits for a match, and a tear drunk does not', function()
    local p = player({ physick = { 11007, -1, -1 } })
    eq(A.check(p, facts()), { { rule = 'Healing', detail = 'Crimson Bubbletear', onEquipment = true, event = false } })
    p.physick = { -1, -1, -1 }
    p.speffects = { 3512 }
    eq(A.check(p, facts()), { { rule = 'Healing', detail = 'Crimson Bubbletear', onEquipment = false, event = false } })
end)

test('the third blue flask of a round breaks the rule and a death refills', function()
    local r = A.Referee.new()
    local f = facts()
    local p, other = player({ key = '1' }), player({ key = '2' })
    local now = 0
    local function drink()
        p.speffects = { T.ceruleanFlask[1] }; now = now + 100
        local a = r:observe(now, { p, other }, f)
        p.speffects = {}; now = now + 100
        r:observe(now, { p, other }, f)
        return a
    end
    eq(#drink(), 0)
    eq(#drink(), 0)
    local third = drink()
    eq(#third, 1)
    eq(third[1].rule, 'CeruleanFlasks')
    r:observe(10000, { p, merge(other, { hp = 0 }) }, f)
    eq(r:flasksUsed('1'), 0)
end)

test('the headline reads the way the popup does', function()
    local a = { player = 'Vagabond', isLocal = true, rule = 'RuneLevel' }
    eq(A.headline(a, 'The Nut Wizard'), 'Attention: Rule break: Vagabond (The Nut Wizard). Rune Level: 125')
    eq(A.headline(a, nil), 'Attention: Rule break: Vagabond (you). Rune Level: 125')
end)

test('a break is filed as the DLL files it (feed::Entry::file_line)', function()
    local when = { year = 2026, month = 10, day = 16, hour = 16, min = 12, sec = 5 }
    eq(A.breakLine(when, { player = 'Lil Chud', isLocal = true, rule = 'SpiritAshes', detail = 'Noble Sorcerer Ashes' }, 'The Nut Wizard'),
        '2026-10-16 16:12:05  Lil Chud (The Nut Wizard)  Spirit Ashes  --  Noble Sorcerer Ashes')
    eq(A.breakLine(when, { player = 'Opponent', isLocal = false, rule = 'SpiritAshes', detail = '' }, 'The Nut Wizard'),
        '2026-10-16 16:12:05  Opponent  Spirit Ashes')
end)

test('the local player is named by Steam, and marked (you) only without it (feed::player_label)', function()
    eq(A.playerLabel('Lion Stronk', true, 'The Nut Wizard'), 'Lion Stronk (The Nut Wizard)')
    eq(A.playerLabel('Lion Stronk', true, ''), 'Lion Stronk (you)')
    eq(A.playerLabel('Opponent', false, 'The Nut Wizard'), 'Opponent')
end)

test('light roll detail matches the agent to the tenth of a percent', function()
    -- The agent's (0.2063 * 100).toFixed(1), with the ratio in single precision.
    local f = facts({ [LONGSWORD] = w(3, 3.5) })
    local p = player({ endurance = 40, weaponsRight = { LONGSWORD, -1, -1 }, armor = { -1, -1, -1, -1 }, newAnims = { 27100 } })
    local load = A.equipLoad(p, f)
    eq(A.check(p, f)[1].detail, string.format('rolled at %.1f%% equip load', load.ratio * 100))
end)

local function ringBlock(entries)
    local slots = {}
    for i = 0, A.RING_SLOTS - 1 do slots[i] = string.pack('<I8i4I4', 0, 0, 0) end
    for _, e in ipairs(entries) do slots[(e.seq - 1) % A.RING_SLOTS] = string.pack('<I8i4I4', e.owner, e.id, e.seq) end
    local parts = {}
    for i = 0, A.RING_SLOTS - 1 do parts[#parts + 1] = slots[i] end
    return table.concat(parts)
end

test('the apply ring hands over each entry once, and only whole ones', function()
    local block = ringBlock({ { seq = 5, owner = 0x2c219080, id = 3505 }, { seq = 6, owner = 0x2c219080, id = 3160 },
        { seq = 7, owner = 0x1000, id = 0 } })
    local reads = 0
    local function read() reads = reads + 1; return block end
    local got, seen = A.ringEntries(4, nil, read)
    eq(got, {}, 'the first look only takes the count'); eq(seen, 4); eq(reads, 0)
    got, seen = A.ringEntries(6, seen, read)
    eq(got, { [0x2c219080] = { 3505, 3160 } }); eq(seen, 6)
    got, seen = A.ringEntries(6, seen, read)
    eq(got, {}, 'nothing new'); eq(reads, 1, 'and nothing read')
    got = A.ringEntries(8, 6, read)
    eq(got, { [0x1000] = { 0 } }, 'seq 8 is still being written, so only 7 is taken')
    got = A.ringEntries(1000, 6, read)
    eq(got, {}, 'a lap overwritten: every surviving slot carries a newer count than asked for')
end)

test('a hook an earlier table left behind is recognised, and nothing else is', function()
    -- The cave as Cheat Engine assembled it live on 2026-10-16: jmp at the site, ring at cave+0x100.
    local site = 0x1404fb79e
    local cave = 0x13ffc0000
    local siteBytes = string.pack('<Bi4', 0xE9, cave - (site + 5)) .. '\x00\x00\x00\x85\xC0\x0F\x49\xF8\x8B\xC7\xEB\x04'
    local caveBytes = '\x85\xC0\x0F\x88\x2F\x00\x00\x00\xB9\x01\x00\x00\x00\xF0\x0F\xC1\x0D' .. string.pack('<i4', 0xeb)
    local function read(address, n) eq(address, cave); return caveBytes:sub(1, n) end
    eq(A.leftoverRing(siteBytes, site, read), 0x13ffc0100)
    eq(A.leftoverRing('\x48\x8B\x9C\x24\x80\x00\x00\x00\x85\xC0\x0F\x49\xF8\x8B\xC7\xEB\x04', site, read), nil, 'the clean site')
    local other = '\x90\x90\x90\x90' .. caveBytes:sub(5)
    eq(A.leftoverRing(siteBytes, site, function() return other end), nil, "someone else's detour")
end)

-- -- your own rules: the cases the Frida agent runs too ----------------------------------------
local function readFile(path)
    local f = assert(io.open(path, 'rb'))
    local s = f:read('a')
    f:close()
    return s
end
local BANS = A.json.decode(readFile(here .. '/test-er-duel-rules-bans.json'))
local fixtureFacts = (function()
    local weapons, effects = {}, {}
    for id, wf in pairs(BANS.facts.weapons) do weapons[math.tointeger(tonumber(id))] = w(wf.wepType) end
    for id, e in pairs(BANS.facts.effects) do effects[math.tointeger(tonumber(id))] = buff(e.category) end
    return facts(weapons, effects)
end)()
for _, c in ipairs(BANS.cases) do
    test('rules file: ' .. c.name, function()
        local config, problems = A.readConfig(c.configText or A.json.encode(c.config))
        eq(#problems, c.problems or 0, table.concat(problems, '; '))
        local referee = A.Referee.new(config)
        local got = {}
        for _, step in ipairs(c.steps) do
            if not c.oneReferee then referee = A.Referee.new(config) end
            local players = { player(merge({ key = '1' }, step.player)) }
            if step.match then players[2] = player({ key = '2' }) end
            for _, a in ipairs(referee:observe(step.t, players, fixtureFacts)) do
                if a.key == '1' then got[#got + 1] = { rule = a.rule, text = a.text, detail = a.detail } end
            end
        end
        eq(got, c.expect)
    end)
end

test('JSON reads and writes what the Frida agent and Python do', function()
    local v = A.json.decode('{"a": [1, 2.5, "x\\"y\\u00e9\\ud83d\\ude00", true, null, {}], "b": {"c": -3e2}}')
    eq(v.a[1], 1); eq(math.type(v.a[1]), 'integer'); eq(v.a[2], 2.5)
    eq(v.a[3], 'x"y\u{e9}\u{1F600}'); eq(v.a[4], true); eq(v.a[5], A.json.null); eq(#v.a, 6)
    eq(v.b.c, -300)
    eq(A.json.encode(v), '{"a":[1,2.5,"x\\"y\u{e9}\u{1F600}",true,null,{}],"b":{"c":-300}}')
    eq(A.json.encode(A.jsonArray()), '[]')
    for _, bad in ipairs({ '{', '[1,]', '{"a" 1}', '"open', '1 2', 'nope' }) do
        ok(not pcall(A.json.decode, bad), bad .. ' is not JSON')
    end
end)

test('every built-in rule is listed once, in the agent\'s order', function()
    local seen = {}
    for _, k in ipairs(A.RULE_ORDER) do
        ok(A.RULE_TEXT[k] ~= nil, k .. ' is not a rule'); ok(not seen[k], k .. ' twice'); seen[k] = true
    end
    for k in pairs(A.RULE_TEXT) do ok(seen[k], k .. ' is missing from RULE_ORDER') end
    eq(A.ruleText('RuneLevel'), 'Rune Level: 125')
    eq(A.ruleText('ShieldGuard'), A.RULE_TEXT.ShieldGuard)
end)

test('the ban editor builds the file the Frida agent builds', function()
    local d = A.newDraft()
    d.name = '  Bleed katanas with Lord of Blood  '
    local hits = A.searchCatalog(A.CATALOG, 'katana', nil, 'class')
    eq(map(hits, function(h) return h.name end), { 'Katana', 'Great Katana' })
    eq(d:addPick(hits[1]), nil)
    eq(d:addPick(A.searchCatalog(A.CATALOG, 'blood', nil, 'affinity')[1], 1), nil)
    local exult = A.searchCatalog(A.CATALOG, "lord of blood's", nil, 'talisman')[1]
    ok(d:addPick(exult, 1):find('cannot be one of the alternatives'), 'a talisman is not a weapon alternative')
    eq(d:addPick(exult), nil)
    d:setWeapon(1, 'twoHanded', true)
    eq(A.describeCondition(d.all[1]), 'Weapon: class Katana, affinity Blood, two-handed, with a weapon buff')
    eq(A.describeCondition(d.all[2]), "Talisman: Lord of Blood's Exultation")
    eq(d:addIds('effect', 'none'), 'no ids in "none"')
    eq(d:addIds('effect', '3310, 3314'), nil)
    ok(d:addCompare('level', 'about 150'):find('comparison'), 'level needs a comparison')
    eq(d:addCompare('level', '!= 125'), nil)
    eq(d:addCompare('stat', 'arcane >= 45'), nil)
    eq(A.describeCondition(d.all[5]), 'Arcane >= 45')
    d:remove(3)
    local ban = assert(d:ban())
    eq(ban.name, 'Bleed katanas with Lord of Blood')
    eq(map(ban.all, function(c) return c.kind end), { 'weapon', 'talisman', 'level', 'stat' })
    eq(A.banJudged(ban), 'act', 'a buffed weapon is something done')
    local cfg = A.withBan(A.defaultConfig(), nil, ban)
    cfg.off[#cfg.off + 1] = 'LightRoll'
    cfg.runeLevel = 150
    local back, problems = A.readConfig(A.writeConfig(cfg))
    eq(problems, {})
    eq(back, cfg)
    eq(A.describeConfig(back), '48 of 49 built-in rules on (off: LightRoll), runeLevel=150, 1 of 1 bans of your own on')
    -- Renaming a ban in place keeps its position; saving one under an existing name replaces it.
    local renamed = A.newDraft(ban)
    renamed.name = 'Renamed'
    cfg = A.withBan(A.withBan(cfg, nil, { name = 'Second', on = true, all = ban.all }), ban.name, assert(renamed:ban()))
    eq(map(cfg.bans, function(b) return b.name end), { 'Renamed', 'Second' })
    eq(A.newDraft():ban(), nil)
end)

test('the table carries this source, and its Lua compiles as Cheat Engine will run it', function()
    local f = assert(io.open(here .. '/cheat-engine/er-duel-rules.CT', 'rb'))
    local ct = f:read('a')
    f:close()
    local script = ct:match('<!%[CDATA%[(.-)%]%]>')
    ok(script ~= nil, 'no Auto Assembler script')
    local enable = script:match('%[ENABLE%]\n{%$lua}\n(.-)\n{%$asm}')
    local disable = script:match('%[DISABLE%]\n{%$lua}\n(.-)\n{%$asm}')
    ok(enable and disable, 'both halves carry a Lua block')
    local src = assert(io.open(here .. '/cheat-engine/er-duel-rules.lua', 'rb'))
    local lua = src:read('a')
    src:close()
    ok(enable:find(lua:gsub('%s+$', ''), 1, true) ~= nil, 'the table is stale; rerun scripts/er-duel-rules-gen.py --write')
    for _, block in ipairs({ enable, disable }) do
        local chunk, err = load(block, '=ct', 't', setmetatable({ syntaxcheck = true }, { __index = _G }))
        ok(chunk ~= nil, tostring(err))
        eq(chunk(), nil, 'a syntax check returns before doing anything')
    end
end)

print(string.format('test-er-duel-rules-ce: %d passed', passed))
