-- Host check of mods/brain_attack.lua's decisions, outside the game:
--   lua5.1 scripts/frida/ai-lua/tests/brain_attack.lua
-- _lab.lua and the brain are loaded as the game loads them, over a stock common10000_Logic that
-- only records that it ran. The `ai` objects are stand-ins answering the queries the brain reads,
-- and the game's constants are stand-in numbers.
local here = string.gsub(arg and arg[0] or "", "tests/brain_attack.lua$", "")

GOAL_COMMON_Wait = 1
GOAL_COMMON_ApproachTarget = 2
GOAL_COMMON_AttackTunableSpin = 3
NPC_ATK_R1 = 4
NPC_ATK_R2 = 5
TARGET_SELF = 0
TARGET_ENE_0 = 10

local stock_ran = false
function common10000_Logic(ai)
  stock_ran = true
end

dofile(here .. "_lab.lua")
dofile(here .. "mods/brain_attack.lua")
lab_wrap_all()

-- The attacker's row (duelists.json brains).
local THINK = 524320000
lab_brain(THINK, "attack")

-- enemy: distance to its enemy (-1 none); roll: what GetRandam_Int answers.
local function fake(think, enemy, roll)
  local ai = { goals = {} }
  function ai:GetNpcThinkParamID() return think end
  function ai:GetDist(t) return enemy end
  function ai:GetRandam_Int(lo, hi) return roll end
  function ai:AddTopGoal(id, life, a1, a2) table.insert(self.goals, { id = id, life = life, a1 = a1, a2 = a2 }) end
  return ai
end

local function only_goal(ai)
  assert(table.getn(ai.goals) == 1, "one top goal, got " .. table.getn(ai.goals))
  return ai.goals[1]
end

-- No enemy: wait.
local ai = fake(THINK, -1, 50)
common10000_Logic(ai)
local g = only_goal(ai)
assert(g.id == GOAL_COMMON_Wait and g.a1 == TARGET_SELF, "no enemy waits")

-- Far: run at it.
ai = fake(THINK, 8, 50)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_ApproachTarget and g.a1 == TARGET_ENE_0, "far: approach")

-- In reach: light most of the time, heavy on a low roll. Never anything else.
ai = fake(THINK, 1, 90)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_AttackTunableSpin and g.a1 == NPC_ATK_R1, "in reach, high roll: R1")
ai = fake(THINK, 1, 10)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_AttackTunableSpin and g.a1 == NPC_ATK_R2, "in reach, low roll: R2")

-- Another think id keeps the stock logic.
stock_ran = false
ai = fake(1, 1, 10)
common10000_Logic(ai)
assert(stock_ran and table.getn(ai.goals) == 0, "other think ids run the stock logic")

print("brain_attack ok")
