-- er-npc-summons brain framework. Copied from scripts/frida/ai-lua/_lab.lua (the Frida lab's
-- framework) and cut down to what a companion brain needs: the override chain, the per-think
-- filter and a log the DLL drains. Lab-only requests (team, home, equipment pushed to spawn-npc.js)
-- are gone; the DLL owns those.
--
-- Loaded into the game's AI Lua state (stock Lua 5.0.2) by er_npc_summons.dll, from inside a
-- lua_pcall the game is making on its AI thread, in this order on every apply:
--   1. this file;
--   2. for each companion with `ai = { brain = "<name>" }`, a one-line prelude setting
--      BRAIN = { name = "<name>", think = <think id> }, then
--      <game dir>/er-npc-summons/brains/<name>.lua;
--   3. brain_wrap_all(), then brain_drain_log(), whose string the DLL copies into
--      er-npc-summons.log.
-- An apply runs whenever the state pointer changes (a world load builds a new state) and every
-- 120 frames after that, because battle scripts load lazily and overwrite the globals the wrappers
-- replace. Everything here is idempotent.
--
-- A brain is keyed by NpcThinkParam id, not by character. Every character running that think id
-- runs the brain, including world NPCs that share it. The Mimic Tear's own think (100000010) is
-- used by nothing else, which is why it is the default companion body.
--
-- What a brain file gets:
--   brain_override(fname, fn [, ai_index])  replace global function `fname` for this brain's think
--                                           only; fn(orig, ...) receives the next layer first.
--                                           ai_index is which argument is the `ai` object (1 for
--                                           common10000_Logic(ai) and GeneralNPC_ActNN(ai, ...),
--                                           2 for MakeNPCProbArr(self, ai, ...)); default 1.
--   brain_is_mine(ai)                       true when `ai` runs this brain's think.
--   brain_log(...)                          one line into er-npc-summons.log.
--   brain_void()                            opt this brain's think into void tech: from now on
--                                           every jump its characters make gets the one press
--                                           their gear can double (a jump cast, or a weapon jump
--                                           that throws or fires), timed by the DLL to spawn on
--                                           the landing frame. Gear that cannot double gets none.
--   brain_void_act(ai, goal [, range])      queue a void tech attempt when the gear can do it and
--                                           the target is within `range` (default
--                                           BRAIN_VOID_RANGE): a jump, or first a grip switch when
--                                           only the other grip doubles. False when it queued
--                                           nothing, so the caller falls through to its own plan.
--
-- Argument roles, read from the decompiled 029999_battle:
--   Goal.Activate(self, ai, goal)          ai has the Get*/Is* queries, goal has AddSubGoal
--   MakeNPCProbArr(self, ai, goal, mode)   returns {act index -> weight}; mode 1 is the main table
--   GeneralNPC_ActNN(ai, goal, paramTbl)   acts 1..9 are zero-padded: GeneralNPC_Act01

-- Lines beyond this many between drains are dropped and counted.
BRAIN_LOG_MAX = 200

function brain_log(...)
  BRAIN_LOG_N = (BRAIN_LOG_N or 0) + 1
  if BRAIN_LOG_N > BRAIN_LOG_MAX then
    BRAIN_LOG_DROPPED = (BRAIN_LOG_DROPPED or 0) + 1
    return
  end
  local line = ""
  for i = 1, arg.n do
    if i > 1 then line = line .. "\t" end
    line = line .. tostring(arg[i])
  end
  BRAIN_LOG_OUT = (BRAIN_LOG_OUT or "") .. line .. "\n"
end

-- Called by the DLL after every apply; returns and clears what was logged since the last call.
function brain_drain_log()
  local out = BRAIN_LOG_OUT or ""
  if (BRAIN_LOG_DROPPED or 0) > 0 then
    out = out .. "dropped\t" .. BRAIN_LOG_DROPPED .. "\n"
  end
  BRAIN_LOG_OUT = nil
  BRAIN_LOG_N = 0
  BRAIN_LOG_DROPPED = 0
  return out
end

-- Rebuilt on every apply, so deleting a line from a brain undoes it on the next apply.
BRAIN_OVERRIDES = {}
BRAIN_THINKS = {}
BRAIN_VOID_THINKS = {}

-- Our wrappers, so a repeat apply can tell its own function from the game's. Kept across applies.
BRAIN_WRAPPERS = BRAIN_WRAPPERS or {}
-- One log line per distinct error message, across applies.
BRAIN_ERRORS = BRAIN_ERRORS or {}

local function think_id(ai)
  local ok, id = pcall(function() return ai:GetNpcThinkParamID() end)
  if ok then return id end
  return -1
end

function brain_is_mine(ai)
  return BRAIN ~= nil and think_id(ai) == BRAIN.think
end

-- Void tech. The DLL sets BRAIN_VOID_OFFERS = { [think] = { one = bool, two = bool } } before the
-- brains load, from the gear it last read on a character of each opted-in think, and reads
-- brain_void_list() after them.
BRAIN_VOID_OFFERS = BRAIN_VOID_OFFERS or {}
-- Metres. Every doubling action throws, fires or casts at range; the shortest, Bestial Sling,
-- reaches about 4.
BRAIN_VOID_RANGE = 4
-- Seconds after a jump before the next plan, so attempts do not overlap.
BRAIN_VOID_GAP = 1.2

function brain_void()
  if BRAIN == nil then
    brain_log("brain-error", "brain_void outside a brain file")
    return
  end
  BRAIN_VOID_THINKS[BRAIN.think] = true
end

function brain_void_list()
  local out = ""
  for think, _ in pairs(BRAIN_VOID_THINKS) do
    if out ~= "" then out = out .. "," end
    out = out .. string.format("%d", think)
  end
  return out
end

function brain_void_act(ai, goal, range)
  local offer = BRAIN_VOID_OFFERS[think_id(ai)]
  if offer == nil or not (offer.one or offer.two) then return false end
  local ok, d = pcall(function() return ai:GetDist(TARGET_ENE_0) end)
  if not ok or d > (range or BRAIN_VOID_RANGE) then return false end
  -- -1 is one hand on each weapon.
  local okh, hands = pcall(function() return ai:GetWeaponBothHandState(TARGET_SELF) end)
  local one = okh and hands == -1
  if (one and offer.one) or (not one and offer.two) then
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 2, NPC_ATK_Jump, TARGET_ENE_0, 999, 0, 0)
    goal:AddSubGoal(GOAL_COMMON_Wait, BRAIN_VOID_GAP, TARGET_ENE_0)
    brain_log("void", "jump", "think", think_id(ai), "dist", math.floor(d * 100 + 0.5) / 100)
  else
    goal:AddSubGoal(GOAL_COMMON_AttackTunableSpin, 1, NPC_ATK_ChangeStyleR, TARGET_ENE_0, 999, 0, 0)
    brain_log("void", "grip", "think", think_id(ai), "to", one and "two" or "one")
  end
  return true
end

-- Several brains may override the same function; they chain in load order, each one's `orig`
-- being the next, the last one's the game's.
local function register(fname, fn)
  BRAIN_OVERRIDES[fname] = BRAIN_OVERRIDES[fname] or {}
  table.insert(BRAIN_OVERRIDES[fname], fn)
end

function brain_override(fname, fn, ai_index)
  if BRAIN == nil then
    brain_log("brain-error", "brain_override outside a brain file", fname)
    return
  end
  local think = BRAIN.think
  local index = ai_index or 1
  BRAIN_THINKS[think] = BRAIN.name
  register(fname, function(orig, a1, a2, a3, a4, a5, a6, a7, a8)
    local args = { a1, a2, a3, a4, a5, a6, a7, a8 }
    if think_id(args[index]) ~= think then
      return orig(a1, a2, a3, a4, a5, a6, a7, a8)
    end
    return fn(orig, a1, a2, a3, a4, a5, a6, a7, a8)
  end)
end

-- An override runs under pcall and falls back to the next layer when it errors. Without this one
-- bad save is permanent: measured 2026-10-05 in the lab, an override that indexed a nil argument
-- made common10000_Logic throw once and the engine never called that NPC's logic again.
local function layer(fname, list, i, orig)
  local o = list[i]
  if o == nil then return orig end
  local inner = layer(fname, list, i + 1, orig)
  return function(a1, a2, a3, a4, a5, a6, a7, a8)
    local r = { pcall(o, inner, a1, a2, a3, a4, a5, a6, a7, a8) }
    if r[1] then return r[2], r[3], r[4], r[5] end
    local msg = tostring(r[2])
    if BRAIN_ERRORS[msg] == nil then
      BRAIN_ERRORS[msg] = true
      brain_log("override-error", "function", fname, "layer", i, "error", msg)
    end
    return inner(a1, a2, a3, a4, a5, a6, a7, a8)
  end
end

-- Dispatch is looked up per call, so a changed brain applies without rewrapping.
local function wrap(fname, orig)
  return function(a1, a2, a3, a4, a5, a6, a7, a8)
    local list = BRAIN_OVERRIDES[fname]
    if list == nil then return orig(a1, a2, a3, a4, a5, a6, a7, a8) end
    return layer(fname, list, 1, orig)(a1, a2, a3, a4, a5, a6, a7, a8)
  end
end

-- Wrap every overridden global that is present and not already ours. A battle script that has
-- not loaded yet is wrapped on a later apply.
function brain_wrap_all()
  local newly = 0
  for fname, _ in pairs(BRAIN_OVERRIDES) do
    local current = _G[fname]
    if type(current) == "function" and not BRAIN_WRAPPERS[current] then
      local w = wrap(fname, current)
      BRAIN_WRAPPERS[w] = true
      _G[fname] = w
      newly = newly + 1
    end
  end
  if newly > 0 then
    local thinks = ""
    for think, name in pairs(BRAIN_THINKS) do
      thinks = thinks .. name .. "=" .. think .. " "
    end
    brain_log("wrapped", "count", newly, "brains", thinks)
  end
end
