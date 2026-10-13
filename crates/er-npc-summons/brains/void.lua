-- Void tech brain: a character that doubles its jump projectiles whenever its gear allows.
--
-- Install as <game dir>/er-npc-summons/brains/void.lua and give a companion
--   ai = { brain = "void" }
-- in er-npc-summons.toml. Its think id then runs the stock AI with one change: on each battle plan,
-- when the target is in range and the gear can double (a jump cast such as Bestial Sling or a
-- glintstone shard, or a weapon jump that throws or fires, such as the Smithscript Dagger, a bow or
-- a crossbow; the DLL's table is crates/er-npc-summons-core/data/void-table.tsv), it jumps, after
-- switching grip if only the other grip doubles. The DLL presses the attack at the frame that puts
-- the spawn on the landing frame and learns the timing from every landing. Gear that cannot double
-- leaves the stock plan untouched, so the brain is safe on any character.

brain_void()

-- What the DLL read off this think's gear, logged when it changes.
do
  local o = BRAIN_VOID_OFFERS[BRAIN.think]
  local now = o == nil and "none" or ("one=" .. tostring(o.one) .. " two=" .. tostring(o.two))
  if VOID_OFFER_LOGGED ~= now then
    VOID_OFFER_LOGGED = now
    brain_log("void", "offers", "think", BRAIN.think, now)
  end
end

-- The battle goal every human NPC think in aicommon runs (NpcThinkParam battleGoalID 29999).
local BATTLE = GOAL_CommonNPCTest29999_Battle
-- Metres within which it tries; a thrown dagger or a bow reaches further than the default.
local RANGE = 6

-- Whether this character's gear doubles in either grip (the DLL's BRAIN_VOID_OFFERS).
local function can_void(ai)
  local ok, think = pcall(function() return ai:GetNpcThinkParamID() end)
  local offer = ok and BRAIN_VOID_OFFERS[think]
  return offer and (offer.one or offer.two)
end

-- Metres within which any enemy target is engaged, battle state or not.
local ENGAGE = 40

-- An ally only plans a fight once its battle state is set. With gear that doubles it does not
-- wait for that: any enemy target within ENGAGE starts the battle plan below.
brain_override("common10000_Logic", function(orig, ai)
  if not can_void(ai) then return orig(ai) end
  local battle = ai:IsBattleState()
  local ok, d = pcall(function() return ai:GetDist(TARGET_ENE_0) end)
  local seen = ok and d ~= nil and d > 0 and d < ENGAGE
  local now = tostring(battle) .. "/" .. tostring(seen)
  if VOID_STATE_LOGGED ~= now then
    VOID_STATE_LOGGED = now
    brain_log("void", "state", "battle", battle, "target", seen, "dist", ok and d or -1)
  end
  if battle or not seen then return orig(ai) end
  COMMON_Initialize(ai)
  COMMON_EasySetup3(ai)
end)

-- With gear that doubles it engages every target it has: out of range it runs in to RANGE - 1
-- and void techs from there, and the stock plan (strafing, backing off, melee) never runs.
brain_override("ActivateTableGoal", function(orig, ai, goal, goalId)
  if goalId ~= BATTLE or not can_void(ai) then
    return orig(ai, goal, goalId)
  end
  if brain_void_act(ai, goal, RANGE) then
    return true
  end
  goal:AddSubGoal(GOAL_COMMON_ApproachTarget, 3, TARGET_ENE_0, RANGE - 1, TARGET_SELF, false, -1)
  brain_log("void", "approach", "think", ai:GetNpcThinkParamID())
  return true
end)
