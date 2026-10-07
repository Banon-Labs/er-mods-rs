-- AI lab framework. Not for editing during play: AI changes go in mods/*.lua, which run after this
-- file, in name order, every time any of them is saved and every couple of seconds after that.
--
-- Loaded into the game's AI Lua state (stock Lua 5.0.2) by scripts/frida/ai-lua-hot-reload.js.
-- Battle scripts load lazily and overwrite the globals they define (029999_battle.lua is executed
-- when the first NPC using it is created), so every wrap here is idempotent and re-applied.
--
-- What mods get:
--   lab_override(name, fn)    replace a global function; fn(orig, ...) receives the original first
--   lab_weight(act, w)        set an act's plan weight for the target NPCs (0 disables it)
--   lab_scale(act, k)         multiply an act's plan weight for the target NPCs
--   lab_team(team)            put the lab's spawned character on TEAM_TYPE `team` (47 Spirit Summon,
--                             8 Ally, 6 Enemy); no call puts back the team it spawned with
--   lab_home(where)           the spawned character's home (POINT_INITIAL): "spawn" (the game's),
--                             "self" (wherever he stands: no home leash) or "player"; a home far
--                             from him stops his Lua while the engine walks him back to it
--   lab_equip(think, slot, id) when the lab's spawned character runs NpcThinkParam `think`, put
--                             protector `id` in `slot` (head, chest, hands, legs) over what its
--                             CharaInitParam row gave it; the row itself is not touched
--   LAB_TARGET_THINK          NpcThinkParam ids the weights and logging apply to (nil: every NPC)
--   hot_log(what, k, v, ...)  one line into the lab's event log
--   LAB_WORLD                 facts about the world the AI cannot query itself, pushed in by the
--                             lab from spawn-npc.js (player_on_lift, lift_pos {x, y, z})
--
-- Argument roles, read from the decompiled 029999_battle:
--   Goal.Activate(self, ai, goal)          ai has the Get*/Is* queries, goal has AddSubGoal
--   MakeNPCProbArr(self, ai, goal, mode)   returns {act index -> weight}; mode 1 is the main table
--   GeneralNPC_ActNN(ai, goal, paramTbl)   acts 1..9 are zero-padded: GeneralNPC_Act01
--
-- Logging is drained by the agent after each apply (`return HOT_LOG_OUT`), not printed: `print` in
-- this state is not luaB_print (measured 2026-10-05).

-- Moongrum's think ids (map part c0000_9014 uses 523590100), and Moonrithyll's (m61_47_44_00 part
-- c0000_9003 uses 524320000). Both run logic 10000 and battle goal 29999, so every mod written for
-- one drives the other.
LAB_TARGET_THINK = { [523590000] = true, [523590100] = true, [524320000] = true }

-- Lines beyond this many between drains are dropped and counted.
HOT_LOG_MAX = 400
-- How many of the heaviest acts to report per plan.
HOT_LOG_TOP = 6

function hot_log(...)
  HOT_LOG_N = (HOT_LOG_N or 0) + 1
  if HOT_LOG_N > HOT_LOG_MAX then
    HOT_LOG_DROPPED = (HOT_LOG_DROPPED or 0) + 1
    return
  end
  local line = ""
  for i = 1, arg.n do
    if i > 1 then line = line .. "\t" end
    line = line .. tostring(arg[i])
  end
  HOT_LOG_OUT = (HOT_LOG_OUT or "") .. line .. "\n"
end

-- Rebuilt on every apply, so deleting a line from a mod undoes it on the next save.
LAB_OVERRIDES = {}
LAB_WEIGHTS = {}
LAB_TEAM = nil
LAB_HOME = "spawn"
LAB_EQUIP = {}

-- Kept across applies: the lab only pushes a fact when it changes.
LAB_WORLD = LAB_WORLD or {}

-- Called by the lab (through the REPL path, on the AI thread) when a world fact changes. Every
-- target that acted since the last apply replans at once, so a fact like "the player stepped onto a
-- lift" is acted on now rather than when his current goal runs out.
-- A quiet fact (one that changes constantly, like swing_damage) is stored without the replan.
function lab_world_set(key, value, quiet)
  LAB_WORLD[key] = value
  if quiet then return end
  hot_log("world", "key", key, "value", tostring(value))
  for _, ai in pairs(LAB_AI) do
    ai:Replaning()
  end
end

-- Several mods may override the same function; they chain in load order (mods run by file name),
-- the first registered outermost, each one's `orig` being the next, the last one's the game's.
function lab_override(name, fn)
  LAB_OVERRIDES[name] = LAB_OVERRIDES[name] or {}
  table.insert(LAB_OVERRIDES[name], fn)
end

-- No ai: method sets a team or a home, so these requests leave Lua: lab_wrap_all() logs them every
-- apply, and the lab hands them to spawn-npc.js, which writes the spawned character's team byte
-- (ChrIns+0x6c) and home position (ChrIns+0x90).
function lab_team(team)
  LAB_TEAM = team
end

function lab_home(where)
  LAB_HOME = where
end

-- Written by spawn-npc.js into the spawn's ChrAsm (both copies), at creation and every heartbeat.
-- A weapon slot takes an optional gem (EquipParamGem id): the Ash of War mounted on the gaitem
-- spawn-npc.js mints for it.
-- The spawn's face, from a build planner `faceData` hex blob (the game's 288-byte face buffer),
-- applied by spawn-npc.js at creation. It travels in the equip request as "think:face=<hex>".
function lab_face(think, hex)
  table.insert(LAB_EQUIP, think .. ":face=" .. hex)
end

-- The spawn's character name (PlayerGameData character_name, 16 UTF-16 units at most), written by
-- spawn-npc.js at creation. Letters, digits and spaces only; it travels as "think:name=<text>".
function lab_name(think, name)
  table.insert(LAB_EQUIP, think .. ":name=" .. name)
end

-- The Ashes of War (EquipParamGem ids, -1 for the weapon's own skill) the summon's right-hand
-- weapon may switch between; spawn-npc.js artPlanner picks one for the moment.
function lab_arts(think, gems)
  table.insert(LAB_EQUIP, think .. ":arts=" .. gems)
end

function lab_equip(think, slot, id, gem)
  local piece = think .. ":" .. slot .. "=" .. id
  if gem ~= nil then piece = piece .. "/" .. gem end
  table.insert(LAB_EQUIP, piece)
end

function lab_weight(act, w)
  LAB_WEIGHTS[act] = { set = w }
end

function lab_scale(act, k)
  LAB_WEIGHTS[act] = { scale = k }
end

-- Our wrappers, so a repeat apply can tell its own function from the game's.
LAB_WRAPPERS = LAB_WRAPPERS or {}

-- The single-file hot.lua this replaced left wrappers in any state it ran in. Their originals are
-- sealed in closures, so they stay in the chain; an empty filter silences their logging.
HOT_LOG_THINK = {}

local function think_id(ai)
  local ok, id = pcall(function() return ai:GetNpcThinkParamID() end)
  if ok then return id end
  return -1
end

-- The last `ai` object seen for each target think id, for the REPL: LAB_AI[523590100]:GetDist(0).
-- REPL code runs on the AI thread, the same one that runs these scripts.
--
-- Emptied on every apply (every ~2 s), so it only ever holds an object one of the target's own
-- calls passed in moments ago. Kept across applies it outlived its character: measured 2026-10-05,
-- a REPL call on the handle of a respawn-removed Moongrum faulted at 0xc960 and every AI Lua call
-- in the game stopped (lua_pcall hits frozen, game CPU near idle). Nil here means "call again once
-- he has acted", never "use the old one".
LAB_AI = {}

function lab_is_target(ai)
  if LAB_TARGET_THINK == nil then return true, think_id(ai) end
  local id = think_id(ai)
  local target = LAB_TARGET_THINK[id] == true
  if target then LAB_AI[id] = ai end
  return target, id
end

local function dist(ai)
  local ok, d = pcall(function() return ai:GetDist(TARGET_ENE_0) end)
  if ok then return math.floor(d * 100 + 0.5) / 100 end
  return -1
end

-- Override dispatch is looked up per call, so a mod change applies without rewrapping.
--
-- An override runs under pcall and falls back to the game's function when it errors. Without this
-- one bad save is permanent: measured 2026-10-05, a patrol override that indexed a nil argument
-- made Moongrum's common10000_Logic throw once, and the engine never called his logic again (other
-- NPCs' kept running) until he was respawned.
LAB_ERRORS = LAB_ERRORS or {}

local function layer(name, list, i, orig)
  local o = list[i]
  if o == nil then return orig end
  local inner = layer(name, list, i + 1, orig)
  return function(a1, a2, a3, a4, a5, a6, a7, a8)
    local r = { pcall(o, inner, a1, a2, a3, a4, a5, a6, a7, a8) }
    if r[1] then return r[2], r[3], r[4], r[5] end
    local msg = tostring(r[2])
    -- Once per distinct message, so a per-frame error cannot flood the log.
    if LAB_ERRORS[msg] == nil then
      LAB_ERRORS[msg] = true
      hot_log("override-error", "function", name, "layer", i, "error", msg)
    end
    return inner(a1, a2, a3, a4, a5, a6, a7, a8)
  end
end

local function call(name, orig, a1, a2, a3, a4, a5, a6, a7, a8)
  local list = LAB_OVERRIDES[name]
  if list == nil then return orig(a1, a2, a3, a4, a5, a6, a7, a8) end
  return layer(name, list, 1, orig)(a1, a2, a3, a4, a5, a6, a7, a8)
end

local function apply_weights(probs)
  for act, rule in pairs(LAB_WEIGHTS) do
    if rule.set ~= nil then
      probs[act] = rule.set
    elseif probs[act] ~= nil then
      probs[act] = probs[act] * rule.scale
    end
  end
end

local function wrap_prob_arr(orig)
  return function(self, ai, goal, mode)
    local probs = call("MakeNPCProbArr", orig, self, ai, goal, mode)
    local target, id = lab_is_target(ai)
    -- Each think id once, target or not, so an empty log can be told apart from a filter miss.
    HOT_SEEN = HOT_SEEN or {}
    if not HOT_SEEN[id] then
      HOT_SEEN[id] = true
      hot_log("seen", "think", id, "target", tostring(target))
    end
    if target and mode == 1 then
      apply_weights(probs)
      local list = {}
      local total = 0
      for act, w in pairs(probs) do
        if w > 0 then
          table.insert(list, { act = act, w = w })
          total = total + w
        end
      end
      table.sort(list, function(a, b) return a.w > b.w end)
      local top = ""
      for i = 1, math.min(HOT_LOG_TOP, table.getn(list)) do
        -- As a percentage, so overridden weights read on the same scale as the game's.
        top = top .. list[i].act .. ":" .. (math.floor(list[i].w * 1000 / total + 0.5) / 10) .. " "
      end
      hot_log("plan", "think", id, "dist", dist(ai), "acts", table.getn(list), "top", top)
    end
    return probs
  end
end

local function wrap_act(name, act, orig)
  return function(ai, goal, paramTbl)
    local target, id = lab_is_target(ai)
    if target then
      hot_log("act", "think", id, "act", act, "dist", dist(ai),
        "override", tostring(LAB_OVERRIDES[name] ~= nil))
    end
    return call(name, orig, ai, goal, paramTbl)
  end
end

local function wrap_plain(name, orig)
  return function(a1, a2, a3, a4, a5, a6, a7, a8)
    return call(name, orig, a1, a2, a3, a4, a5, a6, a7, a8)
  end
end

-- Wrap `name` unless it is missing or already ours.
local function ensure(name, make)
  local current = _G[name]
  if type(current) ~= "function" or LAB_WRAPPERS[current] then return 0 end
  local w = make(current)
  LAB_WRAPPERS[w] = true
  _G[name] = w
  return 1
end

function lab_act_name(n)
  if n < 10 then return "GeneralNPC_Act0" .. n end
  return "GeneralNPC_Act" .. n
end

-- Wraps everything the framework logs, plus anything a mod has overridden. Mods run after this
-- file, so the agent calls lab_wrap_all() again once they have run.
function lab_wrap_all()
  local newly = ensure("MakeNPCProbArr", wrap_prob_arr)
  for n = 1, 300 do
    local act, name = n, lab_act_name(n)
    newly = newly + ensure(name, function(orig) return wrap_act(name, act, orig) end)
  end
  for name, _ in pairs(LAB_OVERRIDES) do
    newly = newly + ensure(name, function(orig) return wrap_plain(name, orig) end)
  end
  if newly > 0 then
    hot_log("wrapped", "count", newly)
  end
  hot_log("spawn-request", "team", LAB_TEAM or -1, "home", LAB_HOME, "equip", table.concat(LAB_EQUIP, ";"))
end
