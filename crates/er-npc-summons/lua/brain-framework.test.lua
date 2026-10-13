-- Host check for brain-framework.lua: lua5.1 brain-framework.test.lua brain-framework.lua
-- Lua 5.1 keeps 5.0's implicit `arg` table in vararg functions, so the framework runs unchanged.
local framework = arg[1]
dofile(framework)

local AI = {}
function AI:GetNpcThinkParamID() return self.think end
local function ai(think) return setmetatable({ think = think }, { __index = AI }) end

function common10000_Logic(a) return "game:" .. a.think end

BRAIN = { name = "t", think = 7 }
brain_override("common10000_Logic", function(orig, a) return "brain:" .. a.think end)
BRAIN = nil
brain_wrap_all()
assert(common10000_Logic(ai(7)) == "brain:7", "the brain runs for its own think")
assert(common10000_Logic(ai(8)) == "game:8", "any other think gets the game's function")

-- A second apply rebuilds the overrides and does not wrap twice; a throwing brain falls back.
dofile(framework)
BRAIN = { name = "t", think = 7 }
brain_override("common10000_Logic", function() error("boom") end)
BRAIN = nil
brain_wrap_all()
assert(common10000_Logic(ai(7)) == "game:7", "an erroring override falls back to the game")
local log = brain_drain_log()
assert(string.find(log, "override-error", 1, true), log)
assert(brain_drain_log() == "", "a drain clears the log")
-- Void tech: brain_void opts the think in, and brain_void_act jumps, switches grip, or declines.
dofile(framework)
BRAIN = { name = "v", think = 100000010 }
brain_void()
BRAIN = nil
assert(brain_void_list() == "100000010", brain_void_list())
TARGET_ENE_0, TARGET_SELF, GOAL_COMMON_AttackTunableSpin, GOAL_COMMON_Wait = 0, -1, 1, 2
NPC_ATK_Jump, NPC_ATK_ChangeStyleR = 30, 31
function AI:GetDist() return self.dist end
function AI:GetWeaponBothHandState() return self.hands end
local function goal()
  return { subs = {}, AddSubGoal = function(g, id, t, act) table.insert(g.subs, act or id) end }
end
local npc = setmetatable({ think = 100000010, dist = 3, hands = -1 }, { __index = AI })
local g = goal()
assert(brain_void_act(npc, g) == false, "no offer yet")
BRAIN_VOID_OFFERS = { [100000010] = { one = false, two = true } }
assert(brain_void_act(npc, g) == true and g.subs[1] == NPC_ATK_ChangeStyleR, "switches to two hands")
npc.hands = 1
g = goal()
assert(brain_void_act(npc, g) == true and g.subs[1] == NPC_ATK_Jump, "jumps two-handed")
npc.dist = 9
assert(brain_void_act(npc, goal()) == false, "out of range")
assert(brain_void_act(npc, goal(), 10) == true, "a brain's own range")
print("ok")
