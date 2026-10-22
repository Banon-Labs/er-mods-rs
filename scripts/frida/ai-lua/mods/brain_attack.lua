-- The attacking duelist: a shell that runs at its enemy and swings, and nothing else. No jump, no
-- Ash of War, no weapon swap, no roll, no guard, no item (user, 2026-10-21: "a shell who runs and
-- attacks"). In the arena it is the attacker block-trainer.js defends against, so its swings are the
-- only thing the defender has to answer.
--
-- Which characters run it is set by lab_brain(think, "attack"); duelists.lua does that from
-- duelists.json's `brains`, for the row only the attacker is spawned on.
--
-- Entry points taken over, the same four brain_shell.lua takes:
--   common10000_Logic(ai)                     run at the enemy, then R1 or R2 once in reach; no
--                                             enemy, wait
--   common10000_Interupt(ai, goal)            no interrupts, so no reactive roll or guard
--   ActivateTableGoal(ai, goal, goalId)       no plan: a short wait
--   InterruptTableGoal(ai, goal, goalId, t)   no reactions

-- Metres (between body surfaces, as GetDist reads) at which it stops running and swings.
ATTACK_REACH = 2.0
-- Percent of swings that are the heavy attack.
ATTACK_HEAVY_PERCENT = 30

local function is_attack(ai)
  return lab_runs(ai, "attack")
end

-- Distance to the enemy, or nil with no enemy: GetDist reads below 0 then (moongrum_patrol.lua).
local function enemy_dist(ai)
  local ok, d = pcall(function() return ai:GetDist(TARGET_ENE_0) end)
  if not ok or d < 0 then return nil end
  return d
end

lab_override("common10000_Logic", function(orig, ai)
  if not is_attack(ai) then return orig(ai) end
  local d = enemy_dist(ai)
  if d == nil then
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.5, TARGET_SELF)
    return
  end
  if d > ATTACK_REACH then
    -- walk false: run.
    ai:AddTopGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_ENE_0, ATTACK_REACH, TARGET_SELF, false, -1)
    return
  end
  local heavy = ai:GetRandam_Int(1, 100) <= ATTACK_HEAVY_PERCENT
  ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, 2, heavy and NPC_ATK_R2 or NPC_ATK_R1, TARGET_ENE_0,
    ATTACK_REACH + 1, 0, 0)
end)

lab_override("common10000_Interupt", function(orig, ai, goal)
  if not is_attack(ai) then return orig(ai, goal) end
  return false
end)

lab_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if not is_attack(ai) then return orig(ai, goal, goalId) end
  goal:AddSubGoal(GOAL_COMMON_Wait, 0.3, TARGET_ENE_0)
  return true
end)

lab_override("InterruptTableGoal", function(orig, ai, goal, goalId, kind)
  if not is_attack(ai) then return orig(ai, goal, goalId, kind) end
  return false
end)
