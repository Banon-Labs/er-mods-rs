-- The empty brain: a character given it stands where it is and decides nothing. It is the base the
-- arena duelists are trained up from, so nothing here may act on its own: no stock GeneralNPC act
-- table, no logic interrupts, no follow, patrol or hearing layer, no battle-goal planning.
--
-- Which characters run it is set by lab_brain(think, "shell"); duelists.lua does that for the
-- duelists' think row, from duelists.json's `brain`.
--
-- Entry points taken over, the same four brain_moonrithyll.lua takes:
--   common10000_Logic(ai)                     a one-second wait, every time it is asked
--   common10000_Interupt(ai, goal)            no interrupts
--   ActivateTableGoal(ai, goal, goalId)       no plan: a one-second wait
--   InterruptTableGoal(ai, goal, goalId, t)   no reactions

local function is_shell(ai)
  return lab_runs(ai, "shell")
end

lab_override("common10000_Logic", function(orig, ai)
  if not is_shell(ai) then return orig(ai) end
  ai:AddTopGoal(GOAL_COMMON_Wait, 1, TARGET_SELF)
end)

lab_override("common10000_Interupt", function(orig, ai, goal)
  if not is_shell(ai) then return orig(ai, goal) end
  return false
end)

lab_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if not is_shell(ai) then return orig(ai, goal, goalId) end
  goal:AddSubGoal(GOAL_COMMON_Wait, 1, TARGET_SELF)
  return true
end)

lab_override("InterruptTableGoal", function(orig, ai, goal, goalId, kind)
  if not is_shell(ai) then return orig(ai, goal, goalId, kind) end
  return false
end)
