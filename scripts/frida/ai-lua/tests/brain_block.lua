-- Host check of mods/brain_block.lua's decisions, outside the game:
--   lua5.1 scripts/frida/ai-lua/tests/brain_block.lua
-- _lab.lua and the brain are loaded as the game loads them, over a stock common10000_Logic that
-- only records that it ran. The `ai` objects are stand-ins answering the queries the brain reads,
-- and the game's constants are stand-in numbers.
local here = string.gsub(arg and arg[0] or "", "tests/brain_block.lua$", "")

GOAL_COMMON_Wait = 1
GOAL_COMMON_Turn = 2
GOAL_COMMON_AttackTunableSpin = 3
NPC_ATK_ChangeStyleR = 4
TARGET_SELF = 0
TARGET_ENE_0 = 10
TARGET_LOCALPLAYER = 20
AI_DIR_TYPE_F = 1

local stock_ran = false
function common10000_Logic(ai)
  stock_ran = true
end

dofile(here .. "_lab.lua")
dofile(here .. "mods/brain_block.lua")
lab_wrap_all()

-- The defender's own row (duelists.json brainThink); the attacker's row, 524320000, keeps its stock AI.
local THINK = 524030000
lab_brain(THINK, "block")

-- enemy: distance to its enemy (-1 none); enemy_to_player: that enemy's distance to the player;
-- hands: GetWeaponBothHandState; facing: IsInsideTarget's answer.
local function fake(think, enemy, enemy_to_player, hands, facing)
  local ai = { goals = {} }
  function ai:GetNpcThinkParamID() return think end
  function ai:GetDist(t) return enemy end
  function ai:GetDistAtoB(a, b) return enemy_to_player end
  function ai:GetWeaponBothHandState(t) return hands end
  function ai:IsInsideTarget(t, dir, ang) return facing end
  function ai:AddTopGoal(id, life, a1, a2) table.insert(self.goals, { id = id, life = life, a1 = a1, a2 = a2 }) end
  return ai
end

local function only_goal(ai)
  assert(table.getn(ai.goals) == 1, "one top goal, got " .. table.getn(ai.goals))
  return ai.goals[1]
end

-- No enemy: stand still, like brain_shell.lua.
local ai = fake(THINK, -1, -1, -1, false)
common10000_Logic(ai)
local g = only_goal(ai)
assert(g.id == GOAL_COMMON_Wait and g.a1 == TARGET_SELF, "no enemy waits on itself")

-- An enemy that is not the player (the attacking duelist), two-handed, not facing: turn to it.
ai = fake(THINK, 15, 15, 1, false)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_Turn and g.a1 == TARGET_ENE_0, "the other duelist is faced too")

-- The player, one hand on each weapon: two-hand the right one first, so L1 guards. The enemy's
-- surface distance to the player is -0.8 when the enemy is the player (measured in game).
ai = fake(THINK, 4, -0.8, -1, true)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_AttackTunableSpin and g.a1 == NPC_ATK_ChangeStyleR, "one-handed: two-hand")

-- The player, two-handed, not facing: turn to the player.
ai = fake(THINK, 4, -0.8, 1, false)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_Turn and g.a1 == TARGET_ENE_0, "not facing: turn")

-- The player, two-handed, facing: wait facing it. Nothing else, and never an attack.
ai = fake(THINK, 4, -0.8, 1, true)
common10000_Logic(ai)
g = only_goal(ai)
assert(g.id == GOAL_COMMON_Wait and g.a1 == TARGET_ENE_0, "facing: wait on the player")

-- Another think id keeps the stock logic.
stock_ran = false
ai = fake(1, 4, -0.8, 1, true)
common10000_Logic(ai)
assert(stock_ran and table.getn(ai.goals) == 0, "other think ids run the stock logic")

print("brain_block ok")
