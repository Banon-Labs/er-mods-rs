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
print("ok")
