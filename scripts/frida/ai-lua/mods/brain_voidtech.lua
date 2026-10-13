-- Void tech test brain: a hostile that only ever jump-casts Bestial Sling at its target, pressing
-- the cast a little later on each jump, so scripts/frida/void-trace.js can tell whether any press
-- timing makes one jump cast twice (two FP charges, two casts). Assigned with
-- lab_brain(<think>, "voidtech") in voidtech.lua, which also gives it the seal and the one spell.
--
-- One attempt per decision, within VT_RANGE of its target: a standing jump (NPC_ATK_Jump), a wait
-- of VT_DELAYS[n], then NPC_ATK_R1, which with a seal in the right hand casts the selected spell.
-- Every attempt logs a "voidtech" line with its number, delay and distance, so
-- the trace's FP drops and cast clips can be matched to it by time. Nothing else: no melee, no
-- roll, no heal, no walking: out of range or out of battle it stands still.

VT_BATTLE = GOAL_CommonNPCTest29999_Battle
-- Seconds between the jump input and the cast input, swept one per attempt and then repeated.
VT_DELAYS = { 0, 0.05, 0.1, 0.15, 0.2, 0.233, 0.267, 0.3, 0.333, 0.367, 0.4, 0.433, 0.467, 0.5, 0.55, 0.6 }
-- Bestial Sling is a short-range spread of stones; inside this it reaches.
VT_RANGE = 4
-- Seconds after the cast before the next attempt, so attempts never overlap in the trace.
VT_GAP = 1.5
VT_N = "VT_N"

local function r2(x)
  return math.floor(x * 100 + 0.5) / 100
end

-- Two hostiles share this brain: "voidtech" jump-casts (voidtech.lua), "voidlance" jump-R1s a
-- two-handed Sword Lance (voidlance.lua). The R1 input is the same; only the grip differs.
local function is_vt(ai)
  return lab_runs(ai, "voidtech") or lab_runs(ai, "voidlance")
end

-- -1 is one hand on each weapon (brain_turtles.lua one_handed); ARM_R is the right weapon in both.
local function one_handed(ai)
  local ok, s = pcall(function() return ai:GetWeaponBothHandState(TARGET_SELF) end)
  return ok and s == -1
end

function VT_plan(ai, goal)
  local d = ai:GetDist(TARGET_ENE_0)
  -- It never walks: out of range it stands and waits for the target to come to it.
  if d > VT_RANGE then
    goal:AddSubGoal(GOAL_COMMON_Wait, 0.5, TARGET_ENE_0)
    hot_log("voidtech", "do", "wait", "d", r2(d))
    return
  end
  if lab_runs(ai, "voidlance") and one_handed(ai) then
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 1, NPC_ATK_ChangeStyleR, TARGET_ENE_0, 999, 0, 0)
    hot_log("voidtech", "do", "two-hand")
    return
  end
  local n = ai:GetStringIndexedNumber(VT_N) + 1
  if n > table.getn(VT_DELAYS) then n = 1 end
  ai:SetStringIndexedNumber(VT_N, n)
  local delay = VT_DELAYS[n]
  goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_Jump, TARGET_ENE_0, 999, 0, 0)
  -- The lance's R1 is injected by scripts/frida/void-trace.js at an exact frame after takeoff (a
  -- Lua wait is too coarse: its presses came 5-7 frames into a 14-frame jump), so it only jumps.
  delay = -1
  if delay > 0 then goal:AddSubGoal(GOAL_COMMON_Wait, delay, TARGET_ENE_0) end
  if delay >= 0 then goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_R1, TARGET_ENE_0, 999, 0, 0) end
  goal:AddSubGoal(GOAL_COMMON_Wait, VT_GAP, TARGET_ENE_0)
  hot_log("voidtech", "do", lab_runs(ai, "voidlance") and "jump-r1" or "jump-cast", "n", n, "delay", delay, "d", r2(d))
end

lab_override("common10000_Logic", function(orig, ai)
  if not is_vt(ai) then return orig(ai) end
  COMMON_Initialize(ai)
  if ai:IsBattleState() then
    COMMON_EasySetup3(ai)
  else
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.5, TARGET_SELF)
  end
end)

lab_override("common10000_Interupt", function(orig, ai, goal)
  if not is_vt(ai) then return orig(ai, goal) end
  return false
end)

lab_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if goalId ~= VT_BATTLE or not is_vt(ai) then return orig(ai, goal, goalId) end
  VT_plan(ai, goal)
  return true
end)

lab_override("InterruptTableGoal", function(orig, ai, goal, goalId, kind)
  if goalId ~= VT_BATTLE or not is_vt(ai) then return orig(ai, goal, goalId, kind) end
  return false
end)
