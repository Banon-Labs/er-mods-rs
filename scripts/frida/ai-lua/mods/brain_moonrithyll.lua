-- Moonrithyll's own brain: a simulated player, spec docs/er-mechanics/simulated-player-ai.md.
--
-- Named to sort before every other mod, so its overrides are the outermost layer: for her think id
-- they decide and never call `orig`, which keeps the Moongrum follow / patrol / hearing layers and
-- the stock GeneralNPC act table out of her decisions. Every other NPC passes straight through.
--
-- Entry points taken over (from the decompiled scripts):
--   common10000_Logic(ai)                     her logic: battle goal in combat, follow otherwise
--   common10000_Interupt(ai, goal)            logic interrupts: none of the stock ones
--   ActivateTableGoal(ai, goal, goalId)       battle goal 29999's planning: MR_plan below
--   InterruptTableGoal(ai, goal, goalId, t)   battle goal 29999's reactions: none yet
-- ActivateTableGoal and InterruptTableGoal are table_ai_common's dispatchers into g_GoalTable, so
-- overriding them reaches the goal table without touching it.
--
-- World facts from spawn-npc.js (LAB_WORLD, quiet):
--   mr_unhittable  distances of hostiles that are dead or in a death throw
--   mr_near        hostiles within 6 m;  mr_heavy_reach  hostiles within 3.2 m

MR_THINK = 524320000
MR_BATTLE = GOAL_CommonNPCTest29999_Battle

-- The build: Bloodfiend's Arm is a strength weapon on a strength build, so it is two-handed.
MR_TWO_HAND = true
-- Stamina below this and she stops to recover instead of attacking (GetSp read 197 at rest).
MR_MIN_SP = 25
-- A target whose distance is within this of an unhittable one is taken to be that one.
MR_SAME_TARGET = 0.6

MR_STATE = MR_STATE or {}

local function r1(x)
  return math.floor(x * 10 + 0.5) / 10
end

-- false hands her back to the stock AI (every override below passes her through).
MR_BRAIN_ON = false

-- Which characters run this brain is set by lab_brain(think, "moonrithyll"), in moonrithyll.lua
-- for her; the same line on any other think id puts that character on it.
local function is_her(ai)
  if not MR_BRAIN_ON then return false end
  return lab_runs(ai, "moonrithyll")
end

local function unhittable(d)
  local list = LAB_WORLD.mr_unhittable
  if list == nil then return false end
  for i = 1, table.getn(list) do
    if math.abs(list[i] - d) <= MR_SAME_TARGET then return true end
  end
  return false
end

local function two_handed(ai)
  local ok, s = pcall(function() return ai:GetWeaponBothHandState(TARGET_SELF) end)
  return ok and s == 1
end

local function attack(goal, button, reach)
  goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, button, TARGET_ENE_0, reach, 0, 0)
end

-- One decision per call, then back here when it finishes: the least-commitment shape the spec
-- asks for, with a fresh look at the target, stamina and HP before every swing.
function MR_plan(ai, goal)
  local d = ai:GetDist(TARGET_ENE_0)
  local hp = ai:GetHp(TARGET_ENE_0)
  local sp = ai:GetSp(TARGET_SELF)
  local two = two_handed(ai)
  local reach = ai:GetStringIndexedNumber(two and "R_Dist_TwoHandR1_First" or "R_Dist_OneHandR1_First")
  local heavy = ai:GetStringIndexedNumber(two and "R_Dist_TwoHandR2_First" or "R_Dist_OneHandR2_First")
  local what

  if hp <= 0 or unhittable(d) then
    -- Dead, dying or mid-critical: no swing. Keep her eyes on it and re-look shortly.
    what = "hold-unhittable"
    goal:AddSubGoal(GOAL_COMMON_Wait, 0.4, TARGET_ENE_0)
  elseif sp < MR_MIN_SP then
    what = "recover-stamina"
    goal:AddSubGoal(GOAL_COMMON_SidewayMove, 1.5, TARGET_ENE_0, ai:GetRandam_Int(0, 1), 80, true, true, -1)
  elseif MR_TWO_HAND and not two then
    what = "two-hand"
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 1, NPC_ATK_ChangeStyleR, TARGET_ENE_0, 999, 0, 0)
  elseif d > heavy + 0.4 then
    what = "approach"
    goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_ENE_0, reach, TARGET_SELF, d < 5, -1)
  elseif (LAB_WORLD.mr_heavy_reach or 0) >= 2 and d <= heavy then
    -- A heavy commit is worth it only when it lands on more than one.
    what = "heavy"
    attack(goal, NPC_ATK_R2, heavy + 0.4)
  elseif d <= reach + 0.2 then
    what = "light"
    attack(goal, NPC_ATK_R1, reach + 0.4)
  else
    what = "close-in"
    goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 2, TARGET_ENE_0, reach, TARGET_SELF, true, -1)
  end
  hot_log("brain", "think", MR_THINK, "do", what, "d", r1(d), "hp", hp, "sp", sp, "two", tostring(two),
    "near", LAB_WORLD.mr_near or -1, "heavy", LAB_WORLD.mr_heavy_reach or -1)
end

-- Out of combat: keep near the player (the chokepoint hold comes later).
function MR_idle(ai)
  local p = ai:GetDist(TARGET_LOCALPLAYER)
  if p > 4 then
    ai:AddTopGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_LOCALPLAYER, 2.5, TARGET_SELF, p < 10, -1)
  else
    ai:AddTopGoal(GOAL_COMMON_Wait, 1, TARGET_LOCALPLAYER)
  end
end

lab_override("common10000_Logic", function(orig, ai)
  if not is_her(ai) then return orig(ai) end
  COMMON_Initialize(ai)
  if ai:IsBattleState() then
    COMMON_EasySetup3(ai)
  else
    MR_idle(ai)
  end
end)

lab_override("common10000_Interupt", function(orig, ai, goal)
  if not is_her(ai) then return orig(ai, goal) end
  return false
end)

lab_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if goalId ~= MR_BATTLE or not is_her(ai) then return orig(ai, goal, goalId) end
  MR_plan(ai, goal)
  return true
end)

lab_override("InterruptTableGoal", function(orig, ai, goal, goalId, kind)
  if goalId ~= MR_BATTLE or not is_her(ai) then return orig(ai, goal, goalId, kind) end
  return false
end)
