-- Host check of lab_brain / lab_runs, outside the game:
--   lua5.1 scripts/frida/ai-lua/tests/brain_assign.lua
-- _lab.lua is loaded as the game loads it; the `ai` objects are stand-ins that answer only
-- GetNpcThinkParamID, the one query the assignment reads.
local here = string.gsub(arg and arg[0] or "", "tests/brain_assign.lua$", "")
dofile(here .. "_lab.lua")

local function fake(id)
  return { GetNpcThinkParamID = function() return id end }
end

lab_brain(523590100, "turtles")
assert(lab_runs(fake(523590100), "turtles"), "assigned brain runs")
assert(not lab_runs(fake(523590100), "moonrithyll"), "other brain does not")
assert(not lab_runs(fake(1), "turtles"), "unassigned think id stays stock")
assert(LAB_TARGET_THINK[523590100], "assigned think id is logged")
assert(LAB_AI[523590100] ~= nil, "REPL handle recorded")

-- Reassigning moves the brain; the last call wins.
lab_brain(523590100, "moonrithyll")
assert(lab_runs(fake(523590100), "moonrithyll"))
assert(not lab_runs(fake(523590100), "turtles"))

print("brain assignment ok")
