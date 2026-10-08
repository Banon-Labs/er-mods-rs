-- The turtles' brain, one set of rules for the whole squad (gear in turtles.lua):
--
--   1. Defensive, and never blocking: face the threat, keep a few metres off it, back away when it
--      closes, and roll out of an attack aimed at it. Two-handed, L1 would block with the weapon
--      itself (the shell only blocks one-handed), so no goal here ever holds L1.
--   2. Never the first to swing. A turtle is provoked only by an attack on the squad: a hit it
--      takes, an enemy starting an attack on it within TT_THREAT_RANGE, or a hostile hitting the
--      player or a brother (spawn-npc.js squadHit). Provoked, it fights its enemy until that enemy
--      is dead, one light attack (the least-committing swing it has) at a time, still rolling,
--      sidestepping and drinking as it needs to. Nothing else it does is an attack: no heavy, no
--      skill.
--   3. Survival comes before the fight. Below TT_LOW_HP with its enemy close or a hit just taken,
--      it rolls clear (or runs, short of stamina) and drinks once clear; below TT_TOPUP_HP it drinks
--      whenever the risk is low (enemy dead or past TT_SAFE_HEAL_DIST, no hit just taken). A brother
--      below TT_LOW_HP (TARGET_FRI_0, the nearest ally) gets run to. Hit while out of reach to
--      answer, it runs out sideways, square to the line from its enemy.
--   4. Never walks: every move is a run (the walk flag is always false), and otherwise it stands
--      still. Not GOAL_COMMON_DashTarget: measured 2026-10-06, aimed at the player it ended the
--      moment it was added, so the turtles replanned every frame and stood stuck.
--   5. Never swaps weapons. turtles.lua leaves nothing in the other slots, and this brain has no
--      path that presses a weapon-change input.
--   6. Two-handed from the moment it is summoned and never one-handed again. The only grip input
--      it ever sends is NPC_ATK_ChangeStyleR, and only while GetWeaponBothHandState reads -1 (both
--      hands holding their own weapon), so the press can only ever take it into two hands.
--
-- Same shape as brain_moonrithyll.lua: it takes over the logic, the battle goal's planning and the
-- logic interrupts for TURTLE_THINK and never calls `orig` for it, so the stock GeneralNPC acts and
-- the Moongrum follow / hearing / patrol layers never decide anything for a turtle. Every other NPC
-- passes straight through.
--
-- Reactions run from InterruptTableGoal_Common, the engine's entry into the battle goal's
-- interrupts. Measured on Leo 2026-10-06: an override of InterruptTableGoal (the per-type
-- dispatcher under it) never once ran, and the logic-level common10000_Interupt saw one Damaged in
-- ten hits and no FindAttack at all.
--
-- Per-turtle memory lives in ai:SetStringIndexedNumber, which is per AI (four turtles share one
-- think id and nothing in Lua tells them apart). Not ai:SetTimer: measured the same day, slot 20
-- read back as expired on the very next decision after being set to 3 s.

TT_BATTLE = GOAL_CommonNPCTest29999_Battle

-- false hands the turtles back to the stock AI.
TT_BRAIN_ON = true

-- An enemy starting an attack this close counts as attacking the turtle.
TT_THREAT_RANGE = 5
-- Where a turtle stands off an unprovoked enemy: it backs away inside TT_TOO_CLOSE, closes in
-- beyond TT_TOO_FAR (so it stays near the fight), and strafes in between.
TT_TOO_CLOSE = 3
TT_TOO_FAR = 7
TT_KEEP = 4.5
-- Stamina below this: no roll and no swing, just get out and recover (GetSp read 197 at rest on
-- Moonrithyll).
TT_MIN_SP = 25
-- Used when the weapon reach numbers have not been filled in yet.
TT_FALLBACK_REACH = 2.5

-- Below this share of its own HP a turtle gets clear and drinks; an ally below it gets rushed to.
TT_LOW_HP = 0.5
-- Below this share, it also drinks whenever the risk of being hit is low.
TT_TOPUP_HP = 0.85
-- Low risk: its enemy is dead or at least this far away, and it was not just hit.
TT_SAFE_HEAL_DIST = 6
-- The heal goods every summon carries (CharaInitParam item; SpEffect 19391 heals 30% of max HP).
TT_HEAL_ITEM = 50201
-- Scorpion Stew, eaten when idle with HP in [TT_STEW_HP, 1): it regenerates, so at full HP it is wasted,
-- and below TT_TOPUP_HP the heal goes first. User rule 2026-10-06 said 90%; 0.85 closes the gap
-- between the two where a turtle at 89.7% ate nothing.
TT_STEW_ITEM = 2001202
TT_STEW_HP = 0.85
-- The SpEffects a stew puts on its eater: found on Raph alone, minutes after he ate one.
TT_STEW_EFFECTS = { 20501201, 20501202 }
-- Boiled Crab, eaten when idle with neither buff running. Its SpEffect, 500820, was on Mikey alone
-- after he ate one (2026-10-06); spawn-npc.js missed it in 'food' newEffects, which it reads the
-- moment the item leaves the inventory, before the effect lands.
TT_CRAB_ITEM = 820
TT_CRAB_EFFECTS = { 500820 }
-- Weapon grease (user rule 2026-10-06: always greased while the weapon takes grease): Dragonbolt
-- Grease out of combat, Drawstring Dragonbolt Grease in battle. spawn-npc.js stocks both only for a
-- weapon whose isEnhance is set, so for Donnie's staff ChangeEquipItem_ById finds nothing and both
-- are skipped. Any of the four rows, right or left, counts as greased.
TT_GREASE_ITEM = 2001410
TT_DRAWSTRING_ITEM = 2001510
TT_GREASE_EFFECTS = { 20501410, 20501411, 20501412, 20501413 }
-- Uses that put no grease on before a turtle stops trying. Measured 2026-10-06: an agent reload
-- stocked the drawstring on all three before the isEnhance gate ran, so Donnie holds one his staff
-- can never take, and pressing use on it would cost him every battle decision.
TT_GREASE_TRIES = "TT_GreaseTries"
TT_GREASE_MAX_TRIES = 3
-- Throwables (user rule 2026-10-06): once the player has held lock-on on an enemy for 2 s
-- (spawn-npc.js volleyCheck sets LAB_WORLD.tt_volley), a turtle whose own target is that enemy may
-- throw, but only a tool whose range covers the distance. Bands come from BulletParam (1.17.1
-- regulation): Kukri flies straight at 25 m/s for 20 m; the Harpoon straight at 45 m/s for 30 m;
-- the three pots leave at 25 (Hefty: 15, accelerating) m/s and fall after 1.5 m (Hefty: 13 m), so
-- they are kept to the lob range a player uses them at; the Spark Aromatic's spray moves at 6 m/s
-- for 1.3 s. The lower bounds keep a slow throw out of reach of the target's swing.
-- A throw that would status its target is skipped while that status is already running on it
-- (user rule 2026-10-06), since the game refuses a second row of a running status's category:
-- `cat` is the status category of its hit SpEffect (Kukri 3115 bleed 10003, Fetid Pot 3120 poison
-- 10004), checked against spawn-npc.js's tt_volley_cats; `effect` is a lasting effect of its own
-- (Albinauric Pot 500610, 25 s), checked on the target directly.
TT_THROWS = {
  { item = 1730, name = "kukri", min = 4, max = 18, cat = 10003 },
  { item = 2001710, name = "harpoon", min = 8, max = 28 },
  { item = 2000690, name = "hefty-pot", min = 6, max = 13 },
  { item = 330, name = "fetid-pot", min = 5, max = 12, cat = 10004 },
  -- Only at a real player (user rule 2026-10-06; spawn-npc.js isHuman -> tt_volley_human).
  { item = 610, name = "albinauric-pot", min = 5, max = 12, effect = 500610, human = true },
  { item = 3510, name = "spark-aromatic", min = 1.5, max = 3.5 },
}
-- How closely a tt_cure entry's distance to the player (m) and HP share must match this turtle's
-- own for the entry to be its own (spawn-npc.js statusCheck; re-sent every 0.4 s).
TT_CURE_DIST = 1.5
TT_CURE_HP = 0.05
-- Uplifting Aromatic (user rule 2026-10-06): its buff (503500 / 503501, 40 s) reaches everyone
-- around the user, so once all of them have been idle TT_AROMA_IDLE seconds with nobody buffed,
-- one turtle uses it and the others leave it. The turtles share this Lua state, so a global claim
-- keyed by tostring(ai) is how they agree; TT_AROMA_CLAIM_S covers the use before the buff lands.
TT_AROMA_ITEM = 3500
TT_AROMA_EFFECTS = { 503500, 503501 }
TT_AROMA_IDLE = 5
TT_AROMA_CLAIM_S = 6
-- Kept across loads: the lab re-runs this file periodically (its "reapplies"), and a fresh table
-- there restarted every idle clock before it reached TT_AROMA_IDLE (measured 2026-10-06).
-- Entries nobody updates expire in squad_note.
TT_SQUAD = TT_SQUAD or {}
TT_AROMA_CLAIMED = TT_AROMA_CLAIMED or -100
-- Mimic's Veil (user rule 2026-10-06): while the player's is up (spawn-npc.js veilCheck ->
-- tt_veil), each turtle puts on its own (SpEffect 503040) and then does nothing at all.
TT_VEIL_ITEM = 3040
TT_VEIL_EFFECT = 503040
-- Ashes of War (user rules 2026-10-06). spawn-npc.js artPlanner puts the one for the moment on
-- the weapon; this presses it (two-handed, the weapon's skill is L2). SwordArtsParam ids, as
-- GetArtsID(TARGET_SELF) reads them:
TT_ART_BLOODHOUND = 801  -- get away to heal: replaces the escape roll
TT_ART_BLINKBOLT = 4130  -- get in on its enemy: provoked, past reach, up to TT_BLINK_MAX m
TT_ART_PARRY = 302       -- a read swing within TT_PARRY_MAX m that has never beaten a parry
TT_ART_THUNDERBOLT = 216 -- finish a fleeing enemy below TT_FINISH_HP, TT_BOLT_MIN..TT_BOLT_MAX m
TT_ART_STORM = 210       -- the same, TT_STORM_MIN..TT_STORM_MAX m
TT_ART_CHILLING = 227    -- one use to apply its status, TT_MIST_MIN..TT_MIST_MAX m
TT_ART_POISON = 228
TT_BLINK_MAX = 15
TT_PARRY_MAX = 3
TT_FINISH_HP = 0.25
TT_BOLT_MIN = 5
TT_BOLT_MAX = 14
TT_STORM_MIN = 3
TT_STORM_MAX = 9
TT_MIST_MIN = 2
TT_MIST_MAX = 6
-- Unprovoked and this far from the player, a turtle leaves the enemy it is watching and follows.
TT_LEASH = 12
-- Provoked by an attack on the squad, a turtle goes after its enemy from this far.
TT_AID_RANGE = 12

-- Per-AI numbers.
TT_LAST_HP = "TT_LastHpRate"
TT_PROVOKED = "TT_Provoked"
TT_ALLY_SEEN = "TT_AllyHitSeen"
TT_PLAYER_SEEN = "TT_PlayerHitSeen"

local function r1(x)
  return math.floor(x * 10 + 0.5) / 10
end

-- Which characters run this brain is set by lab_brain(think, "turtles"), in turtles.lua for the
-- squad; the same line on any other think id puts that character on it.
function tt_is_turtle(ai)
  if not TT_BRAIN_ON then return false end
  return lab_runs(ai, "turtles")
end

-- Press use on the selected item, outside battle, and note when: a turtle standing still to use
-- something is not stuck (measured 2026-10-06, user: buffing up read as stuck and made them jump).
TT_ITEM_AT = "TT_ItemAt"
TT_ITEM_STILL_S = 3
-- Battle decisions that stand still on purpose, by prefix; they hold off the stuck jumps too.
TT_STILL_ACTS = { "art:", "throw:", "bolus:", "drawstring", "heal", "fight", "escape-bloodhound", "two-hand" }

function TT_use_item_top(ai)
  ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, 3, NPC_ATK_ButtonSquare, TARGET_LOCALPLAYER, 999, 0, 0)
  ai:SetStringIndexedNumber(TT_ITEM_AT, os.clock())
end

local function has_any(ai, ids)
  for _, id in ipairs(ids) do
    if ai:HasSpecialEffectId(TARGET_SELF, id) then return true end
  end
  return false
end

-- Whether to put grease `item` on now: none running, fewer than TT_GREASE_MAX_TRIES uses since
-- grease was last seen on, and the item selected. Counts the use it allows.
local function wants_grease(ai, goal, item)
  if has_any(ai, TT_GREASE_EFFECTS) then
    ai:SetStringIndexedNumber(TT_GREASE_TRIES, 0)
    return false
  end
  local tries = ai:GetStringIndexedNumber(TT_GREASE_TRIES)
  if tries >= TT_GREASE_MAX_TRIES or not ChangeEquipItem_ById(nil, ai, goal, item) then return false end
  ai:SetStringIndexedNumber(TT_GREASE_TRIES, tries + 1)
  return true
end

-- -1 is one hand on each weapon (read on Leo with ChrAsm armStyle 1, and 029999_battle tests -1 the
-- same way); ARM_R or ARM_L is a two-handed grip, which the turtles keep.
local function one_handed(ai)
  local ok, s = pcall(function() return ai:GetWeaponBothHandState(TARGET_SELF) end)
  return ok and s == -1
end

local function provoke(ai, why)
  ai:SetStringIndexedNumber(TT_PROVOKED, 1)
  hot_log("turtle", "provoked", why, "d", r1(ai:GetDist(TARGET_ENE_0)))
end

-- A drop in its own HP since the last look is a hit, whether or not the Damaged interrupt fired.
local function took_hit(ai)
  local now = ai:GetHpRate(TARGET_SELF)
  local before = ai:GetStringIndexedNumber(TT_LAST_HP)
  ai:SetStringIndexedNumber(TT_LAST_HP, now)
  return before > 0 and now < before
end

-- A throwable in range of `d`, picked at random among those that fit and are held, selected; or nil.
-- Only with the player's permission and against the player's enemy (same HP as tt_volley_hp).
local function pick_throw(ai, goal, d)
  if (LAB_WORLD.tt_volley or 0) ~= 1 or ai:GetHp(TARGET_ENE_0) ~= LAB_WORLD.tt_volley_hp then return nil end
  -- Counted by hand: the game's Lua has no `#` operator (measured 2026-10-06: `#fits` failed to
  -- load this file 503 times, leaving the turtles on the stock AI, shields up).
  local running = {}
  for _, c in ipairs(LAB_WORLD.tt_volley_cats or {}) do running[c] = true end
  local fits, n = {}, 0
  for _, t in ipairs(TT_THROWS) do
    if d >= t.min and d <= t.max and not (t.cat and running[t.cat])
        and not (t.human and LAB_WORLD.tt_volley_human ~= 1)
        and not (t.effect and ai:HasSpecialEffectId(TARGET_ENE_0, t.effect)) then
      n = n + 1
      fits[n] = t
    end
  end
  while n > 0 do
    local i = ai:GetRandam_Int(1, n)
    if ChangeEquipItem_ById(nil, ai, goal, fits[i].item) then return fits[i] end
    fits[i] = fits[n]
    fits[n] = nil
    n = n - 1
  end
  return nil
end

-- The bolus spawn-npc.js asks this turtle to take, selected; or nil. tt_cure is flat triples
-- (distance to player, HP share, goods id), one per turtle with a status to counter.
local function pick_cure(ai, goal)
  local cure = LAB_WORLD.tt_cure
  if cure == nil then return nil end
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  local hp = ai:GetHpRate(TARGET_SELF)
  local i = 1
  while cure[i + 2] ~= nil do
    if math.abs(cure[i] - p) <= TT_CURE_DIST and math.abs(cure[i + 1] - hp) <= TT_CURE_HP
        and ChangeEquipItem_ById(nil, ai, goal, cure[i + 2]) then
      return cure[i + 2]
    end
    i = i + 3
  end
  return nil
end

function TT_art(ai)
  local ok, id = pcall(function() return ai:GetArtsID(TARGET_SELF) end)
  if ok then return id end
  return -1
end

-- True once per change of the Ash of War on the weapon (the first look only records it).
TT_LAST_ART = "TT_LastArt"
function TT_art_changed(ai)
  local art = TT_art(ai)
  local last = ai:GetStringIndexedNumber(TT_LAST_ART)
  if art == last then return false end
  ai:SetStringIndexedNumber(TT_LAST_ART, art)
  return last ~= 0
end

-- Whether the Ash of War on the weapon is the one to press now, against an enemy at `d`.
local function art_attack(ai, d, r)
  local art = TT_art(ai)
  if art == TT_ART_BLINKBOLT then return d > r + 1 and d <= TT_BLINK_MAX end
  if art == TT_ART_THUNDERBOLT or art == TT_ART_STORM then
    if ai:GetHpRate(TARGET_ENE_0) >= TT_FINISH_HP then return false end
    if art == TT_ART_THUNDERBOLT then return d >= TT_BOLT_MIN and d <= TT_BOLT_MAX end
    return d >= TT_STORM_MIN and d <= TT_STORM_MAX
  end
  if art == TT_ART_CHILLING or art == TT_ART_POISON then return d >= TT_MIST_MIN and d <= TT_MIST_MAX end
  return false
end

-- Two-handed R1 reach, filled in by the stock weapon reader the same way Common_NPC_AI does.
local function reach(ai, goal)
  pcall(Common_NPC_AI_GetWeponParam, nil, ai, goal, ARM_R)
  local r = ai:GetStringIndexedNumber("R_Dist_TwoHandR1_First")
  if r == nil or r <= 0 then return TT_FALLBACK_REACH end
  return r
end

-- One decision per call; the battle goal comes back here when it finishes.
function TT_plan(ai, goal)
  if TT_on_lift(ai, goal) then return end
  if TT_veiled(ai, goal) then return end
  -- In a fight nobody stays crouched.
  if TT_crouched(ai) and TT_match_crouch(ai, goal) then return end
  local d = ai:GetDist(TARGET_ENE_0)
  local hp = ai:GetHp(TARGET_ENE_0)
  local sp = ai:GetSp(TARGET_SELF)
  local what

  local hit_now = took_hit(ai)
  if hit_now then provoke(ai, "hp-drop") end
  -- A hit on the player or a brother since this turtle last looked (spawn-npc.js squadHit).
  local squad = LAB_WORLD.tt_ally_hit or 0
  if squad > ai:GetStringIndexedNumber(TT_ALLY_SEEN) then
    ai:SetStringIndexedNumber(TT_ALLY_SEEN, squad)
    provoke(ai, "ally-hit:" .. tostring(LAB_WORLD.tt_ally_victim))
  end
  -- The player hitting something is the squad's fight as well (spawn-npc.js tt_player_hit).
  local mine = LAB_WORLD.tt_player_hit or 0
  if mine > ai:GetStringIndexedNumber(TT_PLAYER_SEEN) then
    ai:SetStringIndexedNumber(TT_PLAYER_SEEN, mine)
    if hp > 0 and d <= TT_AID_RANGE then provoke(ai, "player-fighting") end
  end
  -- A brother fighting is the squad fighting (user, 2026-10-06: two watched the third attack),
  -- so an enemy of its own alive within TT_AID_RANGE is reason enough to join.
  if ai:GetStringIndexedNumber(TT_PROVOKED) <= 0 and hp > 0 and d <= TT_AID_RANGE and TT_brother_fighting(ai) then
    provoke(ai, "brother-fighting")
  end
  local self_hp = ai:GetHpRate(TARGET_SELF)
  local ally_hp = ai:GetHpRate(TARGET_FRI_0)
  local ally_d = ai:GetDist(TARGET_FRI_0)
  -- Provoked stays provoked until its enemy is dead (user rule 2026-10-06: answering with one swing
  -- left every fight unfinished).
  if hp <= 0 then ai:SetStringIndexedNumber(TT_PROVOKED, 0) end
  local angry = ai:GetStringIndexedNumber(TT_PROVOKED) > 0
  -- Risk of being hit: its enemy is alive and close, or it was hit since the last look.
  local risky = hit_now or (hp > 0 and d < TT_SAFE_HEAL_DIST)
  local r = reach(ai, goal)
  local throw
  -- A bolus only when convenient (user rule 2026-10-06): not hit just now, no live enemy close.
  -- Every branch that uses an item selects its own, so selecting the bolus here is harmless.
  local cure
  if not risky then cure = pick_cure(ai, goal) end

  if TT_art_changed(ai) and not one_handed(ai) then
    -- A new Ash of War went on (spawn-npc.js artPlanner): like the player's swap, it costs the
    -- grip (user rule 2026-10-06). Drop to one hand; the next decision two-hands again first.
    what = "swap-ungrip"
    -- One press (a held toggle flips back, as the crouch did).
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 0.05, NPC_ATK_ChangeStyleR, TARGET_ENE_0, 999, 0, 0)
  elseif one_handed(ai) then
    what = "two-hand"
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 1, NPC_ATK_ChangeStyleR, TARGET_ENE_0, 999, 0, 0)
  elseif self_hp < TT_LOW_HP and risky and TT_art(ai) == TT_ART_BLOODHOUND then
    -- Bloodhound's Step is the way out to heal: the step, then keep going away.
    what = "escape-bloodhound"
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_L2, TARGET_ENE_0, 999, 0, 0)
    goal:AddSubGoal(GOAL_COMMON_LeaveTarget, 2, TARGET_ENE_0, TT_SAFE_HEAL_DIST + 2, TARGET_ENE_0, false, -1)
  elseif self_hp < TT_LOW_HP and risky and d < TT_TOO_CLOSE and sp >= TT_MIN_SP then
    -- Hurt with the enemy on top of it: roll clear first. Measured 2026-10-06: backing off on foot
    -- (LeaveTarget) went 91 decisions without gaining a metre on a chasing enemy, and they died.
    what = "escape-roll"
    goal:AddSubGoal(GOAL_COMMON_Attack, 10, NPC_ATK_StepB, TARGET_ENE_0, DIST_None, 0)
  elseif self_hp < TT_LOW_HP and risky then
    what = "escape-run"
    goal:AddSubGoal(GOAL_COMMON_LeaveTarget, 2.5, TARGET_ENE_0, TT_SAFE_HEAL_DIST + 2, TARGET_ENE_0, false, -1)
  elseif self_hp < TT_LOW_HP or (self_hp < TT_TOPUP_HP and not risky) then
    -- The same calls as 029999_battle's type-20 item act: select the heal in the shortcut, then
    -- press use. Their heal is goods TT_HEAL_ITEM (spawn-npc.js HEAL_ITEM, refilled to cfg.heals),
    -- which GetItemType reads as type 20, not the flask type 10 (measured 2026-10-06).
    what = "heal"
    if ChangeEquipItem_ById(nil, ai, goal, TT_HEAL_ITEM) then
      goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 3, NPC_ATK_ButtonSquare, TARGET_ENE_0, 999, 0, 0)
    else
      what = "heal-none-left"
      goal:AddSubGoal(GOAL_COMMON_LeaveTarget, 1.5, TARGET_ENE_0, TT_KEEP + 3, TARGET_ENE_0, false, -1)
    end
  elseif cure ~= nil then
    what = "bolus:" .. tostring(cure)
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 3, NPC_ATK_ButtonSquare, TARGET_ENE_0, 999, 0, 0)
  elseif not hit_now and wants_grease(ai, goal, TT_DRAWSTRING_ITEM) then
    -- In battle the drawstring, which is used on the move; after survival, before anything else.
    what = "drawstring"
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_ButtonSquare, TARGET_ENE_0, 999, 0, 0)
  elseif angry and not hit_now and sp >= TT_MIN_SP and art_attack(ai, d, r) then
    what = "art:" .. tostring(TT_art(ai))
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 3, NPC_ATK_L2, TARGET_ENE_0, 999, 0, 0)
  elseif not hit_now and sp >= TT_MIN_SP and not (angry and d <= r + 0.2) then
    throw = pick_throw(ai, goal, d)
  end
  if what ~= nil then
    -- decided above
  elseif throw ~= nil then
    -- The player's go-ahead to unload at range; a melee answer in reach still comes first.
    what = "throw:" .. throw.name
    -- Throwing at it makes it this turtle's enemy (user rule 2026-10-06): fight it until it is dead.
    if not angry then provoke(ai, "threw:" .. throw.name) end
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 3, NPC_ATK_ButtonSquare, TARGET_ENE_0, 999, 0, 0)
  elseif ally_d > 0 and ally_hp > 0 and ally_hp < TT_LOW_HP and ally_d > TT_TOO_CLOSE then
    -- A brother is in trouble: get to him; his attacker is likely this turtle's own target there.
    what = "aid-ally"
    goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_FRI_0, 2, TARGET_SELF, false, -1)
  elseif hp <= 0 then
    -- Its enemy is dead. Measured 2026-10-06: waiting on the body kept all three beside a corpse
    -- while a live enemy hit the player, so go back to the player, where the attacker is.
    what = "regroup"
    if ai:GetDist(TARGET_LOCALPLAYER) > TT_UNSTICK_MIN and TT_is_stuck(ai) then
      what = "regroup-unstick"
      TT_unstick(ai, goal)
    else
      goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_LOCALPLAYER, 2.5, TARGET_SELF, false, -1)
    end
  elseif sp < TT_MIN_SP then
    what = "recover-stamina"
    goal:AddSubGoal(GOAL_COMMON_LeaveTarget, 1.5, TARGET_ENE_0, TT_KEEP + 1, TARGET_ENE_0, false, -1)
  elseif angry and d <= r + 0.2 then
    -- Fighting back: a light swing at a time, each followed by a fresh look (stamina, a swing
    -- coming, HP), until the enemy is dead.
    what = "fight"
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_R1, TARGET_ENE_0, r + 0.4, 0, 0)
  elseif hit_now then
    -- Hit and out of reach to answer. Measured 2026-10-06: a straight, constant stream of fire
    -- killed all three, because backing off and closing in both move along the line to its
    -- source. Run out sideways, square to that line, before anything else.
    what = "sidestep"
    goal:AddSubGoal(GOAL_COMMON_SidewayMove, 1.5, TARGET_ENE_0, ai:GetRandam_Int(0, 1), 90, true, false, -1)
  elseif angry and d <= TT_AID_RANGE then
    what = "close-to-answer"
    if TT_is_stuck_enemy(ai) then
      -- No walking route to it (user rule 2026-10-06): look for one by jumping.
      what = "close-unstick"
      TT_unstick(ai, goal, TARGET_ENE_0)
    else
      goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 1.5, TARGET_ENE_0, r, TARGET_SELF, false, -1)
    end
  elseif not angry and ai:GetDist(TARGET_LOCALPLAYER) > TT_LEASH then
    -- Unprovoked, the player comes first: measured 2026-10-06, all three stayed 55-68 m behind,
    -- circling and backing off from an enemy that never touched them, while the player left.
    what = "leash"
    if ai:GetDist(TARGET_LOCALPLAYER) > TT_UNSTICK_MIN and TT_is_stuck(ai) then
      what = "leash-unstick"
      TT_unstick(ai, goal)
    else
      goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_LOCALPLAYER, 2.5, TARGET_SELF, false, -1)
    end
  elseif d < TT_TOO_CLOSE then
    what = "back-off"
    goal:AddSubGoal(GOAL_COMMON_LeaveTarget, 1.5, TARGET_ENE_0, TT_KEEP, TARGET_ENE_0, false, -1)
  elseif d > TT_TOO_FAR then
    what = "hold-near"
    if TT_is_stuck_enemy(ai) then
      what = "near-unstick"
      TT_unstick(ai, goal, TARGET_ENE_0)
    else
      goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 2, TARGET_ENE_0, TT_KEEP, TARGET_SELF, false, -1)
    end
  else
    what = "circle"
    goal:AddSubGoal(GOAL_COMMON_SidewayMove, 1.5, TARGET_ENE_0, ai:GetRandam_Int(0, 1),
      ai:GetRandam_Int(75, 90), true, false, -1)
  end
  -- Standing still on purpose (a skill, a throw, a heal, a swing) is not being stuck: measured
  -- 2026-10-06 (user), an Ash of War that roots the user set off the stuck jumps.
  for _, still in ipairs(TT_STILL_ACTS) do
    if string.find(what, still, 1, true) == 1 then
      ai:SetStringIndexedNumber(TT_ITEM_AT, os.clock())
      break
    end
  end
  hot_log("turtle", "do", what, "d", r1(d), "reach", r1(r), "sp", sp, "angry", tostring(angry),
    "hp", r1(self_hp), "ally_hp", r1(ally_hp), "ally_d", r1(ally_d), "art", TT_art(ai))
end

-- How closely a tt_roll_p distance must match an idle turtle's own distance to the player (m).
TT_IDLE_ROLL_MATCH = 0.75

-- Getting unstuck on the way back to the player (user rule 2026-10-06: jump, never teleport).
-- spawn-npc.js stuckCheck names a stuck turtle in tt_stuck_p by its distance to the player. Each
-- try is one move, escalating while it stays stuck, then starting over:
--   1 a running jump at the player       3 a step back, then a running jump
--   2 a run sideways, then a running jump   4 a jump up-left, then up-right
-- The match widens with the gap: measured 2026-10-06, a turtle flagged at 871.3 s first jumped at
-- 879.6 s, because the player ran from 24 to 60 m away and each turtle's distance had moved past a
-- fixed 1.5 m by the time it compared. The tolerance is TT_STUCK_MATCH or TT_STUCK_SHARE of the
-- gap, whichever is larger.
TT_STUCK_MATCH = 1.5
TT_STUCK_SHARE = 0.2
TT_UNSTICK_MIN = 6
TT_UNSTICK_N = "TT_UnstickTry"

-- The turtle's own measure of being stuck, for when nothing fills LAB_WORLD (er_npc_summons.dll has
-- no spawn-npc.js beside it). Measured 2026-10-07 (user): the player dropped down a safe fall and
-- the turtles stood at the edge, because the walk route ends there and no tt_stuck_p ever named
-- them. Stuck here is TT_SELF_STUCK_S of chasing that neither closed TT_SELF_STUCK_GAIN m nor lost
-- TT_SELF_STUCK_LOSE m (a target running off is not a ledge); the unstick's running jump then
-- takes the fall. An anchor older than TT_SELF_STUCK_STALE means the chase lapsed, so it restarts:
-- measured 2026-10-07, a turtle held at 12.4 m re-planned only every ~6 s, and a 4 s limit threw
-- the anchor away on every look, so it was never called stuck.
TT_SELF_STUCK_S = 2
TT_SELF_STUCK_GAIN = 1
TT_SELF_STUCK_LOSE = 3
TT_SELF_STUCK_STALE = 20

local function self_stuck(ai, key, d)
  local now = os.clock()
  local at = ai:GetStringIndexedNumber(key .. "At")
  local seen = ai:GetStringIndexedNumber(key .. "Seen")
  local from = ai:GetStringIndexedNumber(key .. "D")
  ai:SetStringIndexedNumber(key .. "Seen", now)
  if at == 0 or now - seen > TT_SELF_STUCK_STALE
      or d < from - TT_SELF_STUCK_GAIN or d > from + TT_SELF_STUCK_LOSE then
    ai:SetStringIndexedNumber(key .. "At", now)
    ai:SetStringIndexedNumber(key .. "D", d)
    return false
  end
  return now - at >= TT_SELF_STUCK_S
end

function TT_is_stuck(ai)
  if os.clock() - ai:GetStringIndexedNumber(TT_ITEM_AT) < TT_ITEM_STILL_S then return false end
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  if self_stuck(ai, "TT_SelfStuckP", p) then return true end
  for _, dp in ipairs(LAB_WORLD.tt_stuck_p or {}) do
    local tol = TT_STUCK_SHARE * p
    if tol < TT_STUCK_MATCH then tol = TT_STUCK_MATCH end
    if math.abs(dp - p) <= tol then return true end
  end
  ai:SetStringIndexedNumber(TT_UNSTICK_N, 0)
  return false
end

-- Picked as an enemy's target (spawn-npc.js tt_targeted_p, named by its distance to the player).
function TT_targeted(ai)
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  for _, dp in ipairs(LAB_WORLD.tt_targeted_p or {}) do
    local tol = TT_STUCK_SHARE * p
    if tol < TT_STUCK_MATCH then tol = TT_STUCK_MATCH end
    if math.abs(dp - p) <= tol then return true end
  end
  return false
end

-- Stuck chasing its own enemy (spawn-npc.js tt_stuck_e, named by its distance to that enemy).
function TT_is_stuck_enemy(ai)
  if os.clock() - ai:GetStringIndexedNumber(TT_ITEM_AT) < TT_ITEM_STILL_S then return false end
  local d = ai:GetDist(TARGET_ENE_0)
  if self_stuck(ai, "TT_SelfStuckE", d) then return true end
  for _, de in ipairs(LAB_WORLD.tt_stuck_e or {}) do
    local tol = TT_STUCK_SHARE * d
    if tol < TT_STUCK_MATCH then tol = TT_STUCK_MATCH end
    if math.abs(de - d) <= tol then return true end
  end
  return false
end

-- One move of a search for a route by jumping, toward `target` (the player by default), onto
-- `goal` or as a top goal when `goal` is nil. Each try is a different way up, so a ledge the
-- straight line cannot take gets tried from the sides and from a run-up:
--   1 a running jump straight at it         4 a step back for a run-up, then a running jump
--   2 run left, then a running jump          5 a jump up-left, then up-right
--   3 run right, then a running jump
-- No `%` and no `...` here: the game's Lua rejects `%` (measured 2026-10-06, "unexpected symbol
-- near `%'"), so the count wraps by hand.
TT_UNSTICK_STEPS = 5

function TT_unstick(ai, goal, target)
  local tgt = target or TARGET_LOCALPLAYER
  local n = ai:GetStringIndexedNumber(TT_UNSTICK_N) + 1
  if n > TT_UNSTICK_STEPS then n = 1 end
  ai:SetStringIndexedNumber(TT_UNSTICK_N, n)
  local function jump(action)
    if goal then
      goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, action, tgt, 999, 0, 0)
    else
      ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, 2, action, tgt, 999, 0, 0)
    end
  end
  local function side(dir)
    if goal then
      goal:AddSubGoal(GOAL_COMMON_SidewayMove, 1.5, tgt, dir, 90, true, false, -1)
    else
      ai:AddTopGoal(GOAL_COMMON_SidewayMove, 1.5, tgt, dir, 90, true, false, -1)
    end
  end
  if n == 2 then
    side(0)
  elseif n == 3 then
    side(1)
  elseif n == 4 then
    if goal then
      goal:AddSubGoal(GOAL_COMMON_Attack, 10, NPC_ATK_StepB, tgt, DIST_None, 0)
    else
      ai:AddTopGoal(GOAL_COMMON_Attack, 10, NPC_ATK_StepB, tgt, DIST_None, 0)
    end
  end
  if n == 5 then
    jump(NPC_ATK_UpLeft_Jump)
    jump(NPC_ATK_UpRight_Jump)
  else
    jump(NPC_ATK_Dash_Jump)
  end
  hot_log("turtle", "unstick", n, "d", r1(ai:GetDist(tgt)), "enemy", tostring(target ~= nil))
end

-- Out of combat: two hands on the weapon, and stay with the player.
function TT_idle(ai)
  -- Under attack without being in battle: the attacker never became its target (measured
  -- 2026-10-06, NpcParam 21500064 hit all three for seconds while they ate stew). It cannot
  -- answer without a target, but it can get out of the way: roll on a read swing meant for it
  -- (matched by its distance to the player), and run sideways off the line when hit.
  local hit_now = took_hit(ai)
  local n = LAB_WORLD.tt_roll or 0
  local seen = ai:GetStringIndexedNumber(TT_ROLL_SEEN)
  local warned = false
  if n > seen then
    ai:SetStringIndexedNumber(TT_ROLL_SEEN, n)
    local p = ai:GetDist(TARGET_LOCALPLAYER)
    for _, dp in ipairs(LAB_WORLD.tt_roll_p or {}) do
      if seen > 0 and math.abs(dp - p) <= TT_IDLE_ROLL_MATCH then warned = true end
    end
  end
  if (warned or hit_now) and ai:GetSp(TARGET_SELF) >= TT_MIN_SP then
    local step = TT_STEPS[ai:GetRandam_Int(1, 3)]
    ai:AddTopGoal(GOAL_COMMON_Attack, 10, step, TARGET_LOCALPLAYER, DIST_None, 0)
    hot_log("turtle", "idle", warned and "read-roll" or "hit-roll", "step", step)
    return
  elseif hit_now then
    ai:AddTopGoal(GOAL_COMMON_SidewayMove, 1.5, TARGET_LOCALPLAYER, ai:GetRandam_Int(0, 1), 90, true, false, -1)
    hot_log("turtle", "idle", "hit-sidestep")
    return
  end
  if one_handed(ai) then
    ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, 1, NPC_ATK_ChangeStyleR, TARGET_LOCALPLAYER, 999, 0, 0)
    return
  end
  -- Out of combat is the lowest risk there is: top up. Measured 2026-10-06: two turtles stood at
  -- 45% and 60% HP for minutes after a fight, because only the battle plan ever drank.
  if ai:GetHpRate(TARGET_SELF) < TT_TOPUP_HP and ChangeEquipItem_ById(nil, ai, nil, TT_HEAL_ITEM) then
    TT_use_item_top(ai)
    hot_log("turtle", "idle", "heal", "hp", r1(ai:GetHpRate(TARGET_SELF)))
    return
  end
  local cure = pick_cure(ai, nil)
  if cure ~= nil then
    TT_use_item_top(ai)
    hot_log("turtle", "idle", "bolus", "item", cure)
    return
  end
  -- Sneaking with the player (tt_crouch), the buffing round stops: no Aromatic, grease, stew or
  -- crab (user rule 2026-10-06). Heals and boluses above still go ahead.
  local sneaking = (LAB_WORLD.tt_crouch or 0) == 1
  if not sneaking and TT_wants_aroma(ai) then
    TT_use_item_top(ai)
    hot_log("turtle", "idle", "uplifting-aromatic")
    return
  end
  if not sneaking and wants_grease(ai, nil, TT_GREASE_ITEM) then
    TT_use_item_top(ai)
    hot_log("turtle", "idle", "grease")
    return
  end
  -- Idle food (user rules 2026-10-06), from the quick slots spawn-npc.js stockFood keeps filled:
  -- a Scorpion Stew while hurt but above the heal line, and otherwise, with no stew running, a Boiled Crab
  -- unless one is already running.
  local stew_on = has_any(ai, TT_STEW_EFFECTS)
  if not sneaking and not stew_on and ai:GetHpRate(TARGET_SELF) >= TT_STEW_HP and ai:GetHpRate(TARGET_SELF) < 1 and ChangeEquipItem_ById(nil, ai, nil, TT_STEW_ITEM) then
    TT_use_item_top(ai)
    hot_log("turtle", "idle", "stew", "hp", r1(ai:GetHpRate(TARGET_SELF)))
    return
  end
  if not sneaking and not stew_on and not has_any(ai, LAB_WORLD.tt_crab_effects or TT_CRAB_EFFECTS)
      and ChangeEquipItem_ById(nil, ai, nil, TT_CRAB_ITEM) then
    TT_use_item_top(ai)
    hot_log("turtle", "idle", "crab", "hp", r1(ai:GetHpRate(TARGET_SELF)))
    return
  end
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  -- Jumps only past TT_UNSTICK_MIN: measured 2026-10-06, a recovery that brought a turtle from
  -- 39.6 m to 4 m kept jumping at 4-7 m, where a run finishes the job.
  if p > TT_UNSTICK_MIN and TT_is_stuck(ai) then
    TT_unstick(ai, nil)
  elseif p > 4 then
    -- Never a walk (user rule): run, and stand still once there.
    -- Short goals, so the logic comes back here often enough to dodge a read swing (its lead is
    -- 0.3 s) or get off a stream that is hitting it.
    -- Crouched, it creeps (walks): a run would stand it up, and the crouch is the player's call.
    ai:AddTopGoal(GOAL_COMMON_ApproachTarget, 1, TARGET_LOCALPLAYER, 2.5, TARGET_SELF, TT_crouched(ai), -1)
  else
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.3, TARGET_LOCALPLAYER)
  end
end

-- The dodge, the way the stock FindAttack_Step_NPCPlayer does it: a step (a roll, for a human
-- model) back or to either side, picked at random so it is not predictable.
-- Global, since TT_idle above reads it too.
TT_STEPS = { NPC_ATK_StepB, NPC_ATK_StepL, NPC_ATK_StepR }

-- Being attacked, from the battle goal's own interrupt: open the provocation, and for a swing
-- still coming, dodge it now. A hit taken replans at once so the answer comes as soon as it can.
function TT_interrupt(ai, goal)
  if (LAB_WORLD.tt_veil or 0) == 1 or LAB_WORLD.player_on_lift then return false end
  if ai:IsInterupt(INTERUPT_FindAttack) and ai:GetDist(TARGET_ENE_0) <= TT_THREAT_RANGE then
    provoke(ai, "attack-started")
    goal:ClearSubGoal()
    local sp = ai:GetSp(TARGET_SELF)
    if sp >= TT_MIN_SP then
      local step = TT_STEPS[ai:GetRandam_Int(1, 3)]
      goal:AddSubGoal(GOAL_COMMON_Attack, 10, step, TARGET_ENE_0, DIST_None, 0)
      hot_log("turtle", "do", "roll", "step", step, "d", r1(ai:GetDist(TARGET_ENE_0)), "sp", sp)
    else
      goal:AddSubGoal(GOAL_COMMON_LeaveTarget, 1, TARGET_ENE_0, TT_KEEP + 1, TARGET_ENE_0, false, -1)
      hot_log("turtle", "do", "back-away", "sp", sp)
    end
    return true
  end
  if ai:IsInterupt(INTERUPT_Damaged) then
    provoke(ai, "damaged")
    goal:ClearSubGoal()
    TT_plan(ai, goal)
    return true
  end
  return false
end

-- Lifts (user rule 2026-10-06), as moongrum_follow.lua rides them (measured there: boarded in
-- ~2 s, rode a 24 m descent beside the player). While the player stands on one (spawn-npc.js
-- liftCheck -> LAB_WORLD.player_on_lift, from the player's ground handle), nothing else counts, a
-- fight included: a turtle not standing on it (off_lift) runs to within TT_LIFT_ON m of the
-- player, and one aboard holds still until the player steps off. No jumps (a jump could take it off the
-- edge), no items, no fighting.
TT_LIFT_ON = 1.6
TT_LIFT_LIFE = 2
TT_LIFT_CLOSE = 0.6
TT_LIFT_WALKS = 2
TT_LIFT_TRY = "TT_LiftTry"

-- Off the lift: spawn-npc.js lists every turtle not standing on the player's lift by its
-- distance to the player (tt_lift_off_p); one matching this turtle's own distance is still off.
local function off_lift(ai, p)
  for _, dp in ipairs(LAB_WORLD.tt_lift_off_p or {}) do
    if math.abs(dp - p) <= TT_STUCK_MATCH then return true end
  end
  return false
end

function TT_on_lift(ai, goal)
  if not LAB_WORLD.player_on_lift then
    ai:SetStringIndexedNumber(TT_LIFT_TRY, 0)
    return false
  end
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  if off_lift(ai, p) then
    -- Measured 2026-10-06 on lift 12021520: walking in to 1.2 m left all three on the ground
    -- around it, 2.4-3.6 m away. So walk right up to TT_LIFT_CLOSE, and after TT_LIFT_WALKS
    -- walks that did not get it aboard, jump on toward the player.
    local tries = ai:GetStringIndexedNumber(TT_LIFT_TRY) + 1
    ai:SetStringIndexedNumber(TT_LIFT_TRY, tries)
    local action = nil
    if tries > TT_LIFT_WALKS then action = NPC_ATK_Dash_Jump end
    if goal then
      if action then
        goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, action, TARGET_LOCALPLAYER, 999, 0, 0)
      else
        goal:AddSubGoal(GOAL_COMMON_ApproachTarget, TT_LIFT_LIFE, TARGET_LOCALPLAYER, TT_LIFT_CLOSE, TARGET_SELF, false, -1)
      end
    else
      if action then
        ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, 2, action, TARGET_LOCALPLAYER, 999, 0, 0)
      else
        ai:AddTopGoal(GOAL_COMMON_ApproachTarget, TT_LIFT_LIFE, TARGET_LOCALPLAYER, TT_LIFT_CLOSE, TARGET_SELF, false, -1)
      end
    end
    hot_log("turtle", "lift", action and "board-jump" or "board", "p", r1(p), "try", tries)
    return true
  end
  ai:SetStringIndexedNumber(TT_LIFT_TRY, 0)
  if goal then
    goal:AddSubGoal(GOAL_COMMON_Wait, 0.5, TARGET_LOCALPLAYER)
  else
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.5, TARGET_LOCALPLAYER)
  end
  return true
end

-- Crouching with the player (user rule 2026-10-06): while the player crouches (spawn-npc.js
-- crouchCheck -> tt_crouch, SpEffect 8001 "[HKS] Is Stealth") and no turtle is busy with an enemy,
-- every turtle crouches too; otherwise each stands back up. NPC_ATK_Squat toggles, and the same
-- 8001 says whether it took, so a toggle waits TT_SQUAT_S for the last one to land.
TT_STEALTH = 8001
TT_SQUAT_S = 1.5
TT_SQUAT_PRESS = 0.05
TT_SQUAT_AT = "TT_SquatAt"

local function squad_calm()
  local now = os.clock()
  for _, m in pairs(TT_SQUAD) do
    if now - m.seen < 2 and m.idle_since == nil then return false end
  end
  return true
end

function TT_crouched(ai)
  return ai:HasSpecialEffectId(TARGET_SELF, TT_STEALTH)
end

-- Toggle the crouch when it is not what it should be. True when it pressed.
function TT_match_crouch(ai, goal)
  local want = (LAB_WORLD.tt_crouch or 0) == 1 and squad_calm()
  if want == TT_crouched(ai) then return false end
  if os.clock() - ai:GetStringIndexedNumber(TT_SQUAT_AT) < TT_SQUAT_S then return false end
  ai:SetStringIndexedNumber(TT_SQUAT_AT, os.clock())
  -- One press only. Both attack goals keep pressing for as long as they live, since a crouch is
  -- not an attack that finishes (measured 2026-10-06, user: a 1 s AttackTunableSpin toggled three
  -- times, a 10 s GOAL_COMMON_Attack kept flipping for its whole 10 s), so the goal lives one
  -- frame's worth, TT_SQUAT_PRESS.
  if goal then
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, TT_SQUAT_PRESS, NPC_ATK_Squat, TARGET_LOCALPLAYER, 999, 0, 0)
  else
    ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, TT_SQUAT_PRESS, NPC_ATK_Squat, TARGET_LOCALPLAYER, 999, 0, 0)
  end
  hot_log("turtle", "crouch", tostring(want))
  return true
end

-- While the player hides under Mimic's Veil: put on its own, then hold still. True when it did.
function TT_veiled(ai, goal)
  if (LAB_WORLD.tt_veil or 0) ~= 1 then return false end
  if not ai:HasSpecialEffectId(TARGET_SELF, TT_VEIL_EFFECT) and ChangeEquipItem_ById(nil, ai, goal, TT_VEIL_ITEM) then
    if goal then
      goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 3, NPC_ATK_ButtonSquare, TARGET_LOCALPLAYER, 999, 0, 0)
    else
      TT_use_item_top(ai)
    end
    hot_log("turtle", "veil", "on")
  elseif goal then
    goal:AddSubGoal(GOAL_COMMON_Wait, 0.5, TARGET_LOCALPLAYER)
  else
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.5, TARGET_LOCALPLAYER)
  end
  return true
end

-- What every turtle shares through this Lua state: when it went idle (nil in battle), and
-- whether it carries the Aromatic buff, as of its last look. Keyed by an id kept in the AI's own
-- numbers: tostring(ai) is not stable (measured 2026-10-06: the engine hands the Lua a new ai
-- wrapper per call, the table filled with dozens of one-look entries, every idle clock restarted
-- each look, and the Aromatic was never used again after the first time).
TT_ID = "TT_SquadId"
TT_NEXT_ID = TT_NEXT_ID or 0

local function squad_id(ai)
  local id = ai:GetStringIndexedNumber(TT_ID)
  if id == nil or id <= 0 then
    TT_NEXT_ID = TT_NEXT_ID + 1
    id = TT_NEXT_ID
    ai:SetStringIndexedNumber(TT_ID, id)
  end
  return id
end

local function squad_note(ai, idle)
  local key = squad_id(ai)
  local me = TT_SQUAD[key] or {}
  local now = os.clock()
  if not idle then me.idle_since = nil
  elseif me.idle_since == nil then me.idle_since = now end
  me.aroma = has_any(ai, TT_AROMA_EFFECTS)
  me.angry = ai:GetStringIndexedNumber(TT_PROVOKED) > 0
  me.seen = now
  TT_SQUAD[key] = me
  -- Drop entries nobody has updated for a minute (dead or despawned turtles).
  for k, m in pairs(TT_SQUAD) do
    if now - m.seen > 60 then TT_SQUAD[k] = nil end
  end
  return me
end

-- Another turtle, seen in the last 2 s, is provoked.
function TT_brother_fighting(ai)
  local mine = squad_id(ai)
  local now = os.clock()
  for k, m in pairs(TT_SQUAD) do
    if k ~= mine and now - m.seen < 2 and m.angry then return true end
  end
  return false
end

-- All turtles seen in the last 2 s idle for TT_AROMA_IDLE s, none buffed, and no claim running.
function TT_wants_aroma(ai)
  local now = os.clock()
  if now - TT_AROMA_CLAIMED < TT_AROMA_CLAIM_S then return false end
  for _, m in pairs(TT_SQUAD) do
    if now - m.seen < 2 and (m.aroma or m.idle_since == nil or now - m.idle_since < TT_AROMA_IDLE) then
      return false
    end
  end
  if not ChangeEquipItem_ById(nil, ai, nil, TT_AROMA_ITEM) then return false end
  TT_AROMA_CLAIMED = now
  return true
end

lab_override("common10000_Logic", function(orig, ai)
  if not tt_is_turtle(ai) then return orig(ai) end
  COMMON_Initialize(ai)
  -- An enemy that has picked this turtle makes it free to engage (user rule 2026-10-06;
  -- spawn-npc.js targetedCheck names it in tt_targeted_p by its distance to the player).
  if ai:GetStringIndexedNumber(TT_PROVOKED) <= 0 and ai:GetHp(TARGET_ENE_0) > 0 and TT_targeted(ai) then
    provoke(ai, "targeted")
  end
  -- Busy is a real fight: provoked, or a living enemy within TT_AID_RANGE. The battle state alone
  -- flickers on with nothing near (measured 2026-10-06: idle clocks kept restarting at 1-2 s).
  local busy = ai:IsBattleState() and (ai:GetStringIndexedNumber(TT_PROVOKED) > 0
    or (ai:GetHp(TARGET_ENE_0) > 0 and ai:GetDist(TARGET_ENE_0) <= TT_AID_RANGE))
  squad_note(ai, not busy)
  if TT_on_lift(ai, nil) then return end
  if TT_veiled(ai, nil) then return end
  if TT_match_crouch(ai, nil) then return end
  -- In battle state but not busy is idle: measured 2026-10-06, after a kill they stayed in battle
  -- on the corpse, regrouping every frame, so no idle chore (the Aromatic included) ever ran.
  if busy then
    COMMON_EasySetup3(ai)
  else
    TT_idle(ai)
  end
end)

lab_override("common10000_Interupt", function(orig, ai, goal)
  if not tt_is_turtle(ai) then return orig(ai, goal) end
  return false
end)

-- The input read (spawn-npc.js readSwings): a swing is about to land on the turtle at each distance
-- in tt_roll_d. A turtle whose own enemy is at one of them rolls now, on the battle goal's update,
-- without waiting for its current goal to end.
TT_ROLL_SEEN = "TT_RollSeen"
TT_ROLL_MATCH = 0.5
TT_ROLL_REST = 1.5
TT_ROLL_AT = "TT_RollAt"
TT_UPDATES = TT_UPDATES or 0

function TT_read_roll(ai, goal)
  TT_UPDATES = TT_UPDATES + 1
  if (LAB_WORLD.tt_veil or 0) == 1 or LAB_WORLD.player_on_lift then return end
  local n = LAB_WORLD.tt_roll or 0
  local seen = ai:GetStringIndexedNumber(TT_ROLL_SEEN)
  if n <= seen then return end
  ai:SetStringIndexedNumber(TT_ROLL_SEEN, n)
  -- A turtle summoned after earlier signals starts level with them instead of rolling at once.
  if seen == 0 then return end
  local d = ai:GetDist(TARGET_ENE_0)
  local mine = false
  local from_target = false
  for _, di in ipairs(LAB_WORLD.tt_roll_d or {}) do
    if math.abs(di - d) <= TT_ROLL_MATCH then
      mine = true
      from_target = true
    end
  end
  -- A swing from an enemy that is not its own target is matched by its distance to the player
  -- instead. Measured 2026-10-06 (user): of two turtles under one attack, the one focused on
  -- another enemy did not roll.
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  for _, dp in ipairs(LAB_WORLD.tt_roll_p or {}) do
    if math.abs(dp - p) <= TT_IDLE_ROLL_MATCH then mine = true end
  end
  if not mine then return end
  -- One roll per TT_ROLL_REST: measured 2026-10-06, a roll on every warning (one each ~30 ms in
  -- a long attack) cleared every attack they queued, and nobody fought.
  if os.clock() - ai:GetStringIndexedNumber(TT_ROLL_AT) < TT_ROLL_REST then return end
  ai:SetStringIndexedNumber(TT_ROLL_AT, os.clock())
  provoke(ai, "swing-read")
  local sp = ai:GetSp(TARGET_SELF)
  goal:ClearSubGoal()
  if from_target and TT_art(ai) == TT_ART_PARRY and (LAB_WORLD.tt_roll_parry or 0) == 1 and d <= TT_PARRY_MAX
      and ai:GetHpRate(TARGET_SELF) >= TT_LOW_HP then
    -- Low risk: close, healthy, and a swing that has never got through a parry.
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_L2, TARGET_ENE_0, 999, 0, 0)
    ai:SetStringIndexedNumber(TT_ITEM_AT, os.clock())
    hot_log("turtle", "do", "read-parry", "d", r1(d), "sp", sp)
  elseif sp >= TT_MIN_SP then
    local step = TT_STEPS[ai:GetRandam_Int(1, 3)]
    goal:AddSubGoal(GOAL_COMMON_Attack, 10, step, TARGET_ENE_0, DIST_None, 0)
    hot_log("turtle", "do", "read-roll", "step", step, "d", r1(d), "sp", sp, "updates", TT_UPDATES)
  else
    goal:AddSubGoal(GOAL_COMMON_SidewayMove, 1.5, TARGET_ENE_0, ai:GetRandam_Int(0, 1), 90, true, false, -1)
    hot_log("turtle", "do", "read-sidestep", "d", r1(d), "sp", sp, "updates", TT_UPDATES)
  end
end

lab_override("UpdateTableGoal", function(orig, ai, goal, goalId)
  local result = orig(ai, goal, goalId)
  if goalId == TT_BATTLE and tt_is_turtle(ai) then TT_read_roll(ai, goal) end
  return result
end)

-- The engine's entry into a table goal's interrupts (it calls this, not InterruptTableGoal).
lab_override("InterruptTableGoal_Common", function(orig, ai, goal, goalId)
  if goalId ~= TT_BATTLE or not tt_is_turtle(ai) then return orig(ai, goal, goalId) end
  return TT_interrupt(ai, goal)
end)

lab_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if goalId ~= TT_BATTLE or not tt_is_turtle(ai) then return orig(ai, goal, goalId) end
  TT_plan(ai, goal)
  return true
end)

lab_override("InterruptTableGoal", function(orig, ai, goal, goalId, kind)
  if goalId ~= TT_BATTLE or not tt_is_turtle(ai) then return orig(ai, goal, goalId, kind) end
  return false
end)
