-- The perfect-block sparring partner. A character given it does one thing: it faces its enemy with
-- its right weapon in both hands, so L1 guards (the build powerstances two Iron Balls, and with a
-- weapon in each hand L1 is a left-hand attack, not a guard). It never attacks, moves, heals or uses
-- an item, and no goal here ever presses L1.
--
-- The guard press itself comes from scripts/frida/block-trainer.js, on the frame it predicts from
-- the player's swing: a perfect block is a one-frame press, and Lua decides on its own schedule.
--
-- Which characters run it is set by lab_brain(think, "block"); duelists.lua does that for
-- duelists.json's `brainThink`, the row only the defender is spawned on, so the attacker keeps its
-- stock AI. The defender faces whatever enemy it has: the other duelist, or the player when its spot
-- is the player's sparring partner (`vs: "player"`). With no enemy it waits like brain_shell.lua.
--
-- Entry points taken over, the same four brain_shell.lua takes:
--   common10000_Logic(ai)                     face the enemy, two-handed; otherwise wait
--   common10000_Interupt(ai, goal)            no interrupts
--   ActivateTableGoal(ai, goal, goalId)       no plan: a short wait
--   InterruptTableGoal(ai, goal, goalId, t)   no reactions

-- Degrees either side of straight ahead that count as facing the player.
BLOCK_FACE_DEG = 10

local function is_block(ai)
  return lab_runs(ai, "block")
end

-- An enemy to face: GetDist reads below 0 with no enemy (moongrum_patrol.lua).
local function has_enemy(ai)
  local ok, d = pcall(function() return ai:GetDist(TARGET_ENE_0) end)
  return ok and d >= 0
end

-- -1 is one hand on each weapon (brain_turtles.lua one_handed).
local function one_handed(ai)
  local ok, s = pcall(function() return ai:GetWeaponBothHandState(TARGET_SELF) end)
  return ok and s == -1
end

local function facing(ai)
  local ok, inside = pcall(function() return ai:IsInsideTarget(TARGET_ENE_0, AI_DIR_TYPE_F, BLOCK_FACE_DEG * 2) end)
  return ok and inside
end

lab_override("common10000_Logic", function(orig, ai)
  if not is_block(ai) then return orig(ai) end
  if not has_enemy(ai) then
    ai:AddTopGoal(GOAL_COMMON_Wait, 1, TARGET_SELF)
    return
  end
  if one_handed(ai) then
    ai:AddTopGoal(GOAL_COMMON_AttackTunableSpin, 1, NPC_ATK_ChangeStyleR, TARGET_ENE_0, 999, 0, 0)
    hot_log("block", "two-hand")
    return
  end
  if facing(ai) then
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.3, TARGET_ENE_0)
  else
    ai:AddTopGoal(GOAL_COMMON_Turn, 1, TARGET_ENE_0, BLOCK_FACE_DEG, 0, 0)
    hot_log("block", "turn")
  end
end)

lab_override("common10000_Interupt", function(orig, ai, goal)
  if not is_block(ai) then return orig(ai, goal) end
  return false
end)

lab_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if not is_block(ai) then return orig(ai, goal, goalId) end
  goal:AddSubGoal(GOAL_COMMON_Wait, 0.3, TARGET_ENE_0)
  return true
end)

lab_override("InterruptTableGoal", function(orig, ai, goal, goalId, kind)
  if not is_block(ai) then return orig(ai, goal, goalId, kind) end
  return false
end)
