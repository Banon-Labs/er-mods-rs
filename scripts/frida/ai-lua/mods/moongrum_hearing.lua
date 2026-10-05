-- Moongrum hears like a person: how well he hears a sound depends on where it is relative to his
-- facing, what lies between him and it, and how far away it is. A sound he hears well he goes to
-- investigate, a faint one makes him turn and listen, and one he barely hears is ignored.
--
-- The engine decides that a sound reached him at all (its own range, from the sound's param); this
-- mod decides what he does about it. It reads the engine's latest sound target, TARGET_SOUND (81):
--   ai:GetDist(TARGET_SOUND)          m, or -1 with no sound (measured)
--   ai:GetToTargetAngle(TARGET_SOUND) degrees from his facing, (-180, 180]; the same convention the
--                                     ray calls below take (measured: a fixed point placed at 270
--                                     reads back as -90)
-- Obstruction, along the line to the sound:
--   ai:GetExistMeshOnLineDistSpecifyAngle(TARGET_SELF, angle, dist, AI_SPA_DIR_TYPE_TargetF)
--     navmesh length along the line; short of the sound means a wall, drop or gap between
--   ai:IsExistChrOnLineSpecifyAngle(TARGET_SELF, angle, dist, AI_SPA_DIR_TYPE_TargetF)
--     another character in the way
-- Reactions use the game's own sound-behaviour goals (common_sound_behavior.lua):
--   GOAL_COMMON_Turn(life, TARGET_SOUND, 0, 0, 0)
--   GOAL_COMMON_ApproachTarget(life, TARGET_SOUND, arriveDist, TARGET_SELF, walk, -1)
-- A new sound interrupts whatever he is doing (INTERUPT_ChangeSoundTarget, then ClearSubGoal and
-- Replaning, as 020010_logic does for its own interrupt).

HEAR_MAX = 30              -- beyond this a sound counts for nothing, m
HEAR_NEAR = 3              -- within this distance counts fully, m
HEAR_BACK = 0.35           -- how well he hears directly behind (front is 1)
HEAR_WALL = 0.45           -- through a wall or across a gap in the navmesh
HEAR_BODY = 0.85           -- past another character
HEAR_IGNORE_BELOW = 0.25   -- score under this: ignored
HEAR_INVESTIGATE_AT = 0.55 -- score from this: walks over to look
HEAR_RUN_AT = 0.8          -- score from this: runs
HEAR_FORGET_S = 15         -- after this he stops acting on one sound
HEAR_SOURCE_GAP = 3        -- the in-the-way check stops this far short of the sound, m

HEAR_STATE = HEAR_STATE or {}

local function r1(x)
  return math.floor(x * 10 + 0.5) / 10
end

-- How well he heard the current sound, 0..1, and why.
function hear_score(ai)
  local d = ai:GetDist(TARGET_SOUND)
  if d < 0 then return nil end
  local a = ai:GetToTargetAngle(TARGET_SOUND)
  local facing = HEAR_BACK + (1 - HEAR_BACK) * (1 + math.cos(a * math.pi / 180)) / 2
  local distance = 1
  if d > HEAR_NEAR then distance = math.max(0, 1 - (d - HEAR_NEAR) / (HEAR_MAX - HEAR_NEAR)) end
  local mesh = ai:GetExistMeshOnLineDistSpecifyAngle(TARGET_SELF, a, d, AI_SPA_DIR_TYPE_TargetF)
  local wall = mesh < d * 0.9 - 0.5
  -- Stop short of the sound itself: its source is often a character standing on it. 1.5 m was not
  -- enough (measured 2026-10-05: a sound at 5.4 m with the player, its source, at 5.5 m read as
  -- "body in the way"), so the line ends HEAR_SOURCE_GAP before the sound.
  local body = d > HEAR_SOURCE_GAP + 1
    and ai:IsExistChrOnLineSpecifyAngle(TARGET_SELF, a, d - HEAR_SOURCE_GAP, AI_SPA_DIR_TYPE_TargetF)
  local score = facing * distance
  if wall then score = score * HEAR_WALL end
  if body then score = score * HEAR_BODY end
  return score, { dist = r1(d), angle = math.floor(a + 0.5), facing = r1(facing),
    distance = r1(distance), mesh = r1(mesh), wall = wall, body = body and true or false }
end

local function decide(score)
  if score < HEAR_IGNORE_BELOW then return "ignore" end
  if score < HEAR_INVESTIGATE_AT then return "turn" end
  if score < HEAR_RUN_AT then return "walk" end
  return "run"
end

local function state(id)
  local s = HEAR_STATE[id]
  if s == nil then
    s = {}
    HEAR_STATE[id] = s
  end
  return s
end

-- Perception probe: measured 2026-10-05, the player's noise put Moongrum straight into battle and
-- never set TARGET_SOUND, so which state carries "he only heard it" is still being found out.
-- Logs his perception whenever it changes, and every interrupt id that fires.
local PROBE_INTERRUPTS = { "INTERUPT_FindEnemy", "INTERUPT_FindAttack", "INTERUPT_ChangeSoundTarget",
  "INTERUPT_LoseSightTarget", "INTERUPT_Damaged", "INTERUPT_FindMissile" }

function hear_probe(ai, id, where)
  local p = {
    type = ai:GetCurrTargetType(), prev = ai:GetPrevTargetState(), find = ai:IsFindState(),
    memory = ai:IsMemoryState(), battle = ai:IsBattleState(), low = ai:IsSearchLowState(),
    high = ai:IsSearchHighState(), ene = r1(ai:GetDist(TARGET_ENE_0)), sound = r1(ai:GetDist(TARGET_SOUND)),
    search = r1(ai:GetDist(TARGET_SEARCH)),
  }
  local key = p.type .. p.prev .. tostring(p.find) .. tostring(p.memory) .. tostring(p.battle)
    .. tostring(p.low) .. tostring(p.high) .. (p.ene >= 0 and "e" or "") .. (p.sound >= 0 and "s" or "")
    .. (p.search >= 0 and "r" or "")
  HEAR_PROBE = HEAR_PROBE or {}
  if HEAR_PROBE[id] == key then return end
  HEAR_PROBE[id] = key
  hot_log("perceive", "think", id, "where", where, "type", p.type, "prev", p.prev, "find", tostring(p.find),
    "memory", tostring(p.memory), "battle", tostring(p.battle), "low", tostring(p.low),
    "high", tostring(p.high), "ene", p.ene, "sound", p.sound, "search", p.search,
    "angle", math.floor(ai:GetToTargetAngle(TARGET_LOCALPLAYER) + 0.5),
    "player", r1(ai:GetDist(TARGET_LOCALPLAYER)))
end

-- Every logic and interrupt call of the target, before anything else decides.
lab_override("common10000_Interupt", function(orig, ai, goal)
  local target, id = lab_is_target(ai)
  if target then
    local fired = ""
    for _, name in ipairs(PROBE_INTERRUPTS) do
      if _G[name] ~= nil and ai:IsInterupt(_G[name]) then fired = fired .. name .. " " end
    end
    if fired ~= "" then hot_log("interrupt", "think", id, "fired", fired) end
    hear_probe(ai, id, "interrupt")
    -- Measured 2026-10-05: after a fight his logic stopped being called for good, with only
    -- GOAL_COMMON_TopGoal and GOAL_COMMON_ActivateNoSubGoal (2033) active, while this interrupt
    -- kept firing. Say so when it happens, with what he was doing.
    local last = HEAR_LOGIC_AT and HEAR_LOGIC_AT[id]
    if last ~= nil and os.clock() - last > 12 and not (HEAR_STALL_SAID and HEAR_STALL_SAID[id]) then
      HEAR_STALL_SAID = HEAR_STALL_SAID or {}
      HEAR_STALL_SAID[id] = true
      hot_log("logic-stalled", "think", id, "since_s", math.floor(os.clock() - last),
        "battle", tostring(ai:IsBattleState()), "type", ai:GetCurrTargetType(),
        "nosub", tostring(ai:IsActiveGoal(GOAL_COMMON_ActivateNoSubGoal)))
    end
  end
  return orig(ai, goal)
end)

lab_override("common10000_Logic", function(orig, ai)
  local target, id = lab_is_target(ai)
  if target then
    HEAR_LOGIC_AT = HEAR_LOGIC_AT or {}
    HEAR_LOGIC_AT[id] = os.clock()
    if HEAR_STALL_SAID then HEAR_STALL_SAID[id] = nil end
    hear_probe(ai, id, "logic")
  end
  return orig(ai)
end)

-- A new sound: forget the old decision and decide again now, not when the current goal ends.
lab_override("common10000_Interupt", function(orig, ai, goal)
  local target, id = lab_is_target(ai)
  if target and ai:IsInterupt(INTERUPT_ChangeSoundTarget) and not ai:IsBattleState() then
    local s = state(id)
    s.decision, s.step = nil, nil
    hot_log("hear", "think", id, "phase", "new-sound")
    goal:ClearSubGoal()
    ai:Replaning()
    return true
  end
  return orig(ai, goal)
end)

lab_override("common10000_Logic", function(orig, ai)
  local target, id = lab_is_target(ai)
  if not target or ai:IsBattleState() then return orig(ai) end
  local score, why = hear_score(ai)
  local s = state(id)
  if score == nil then
    s.decision, s.step = nil, nil
    return orig(ai)
  end
  local now = os.clock()
  if s.decision == nil then
    s.decision, s.step, s.at = decide(score), 0, now
    hot_log("hear", "think", id, "decision", s.decision, "score", r1(score), "dist", why.dist,
      "angle", why.angle, "facing", why.facing, "distance", why.distance, "mesh", why.mesh,
      "wall", tostring(why.wall), "body", tostring(why.body))
  end
  if s.decision ~= "ignore" and now - s.at > HEAR_FORGET_S then
    hot_log("hear", "think", id, "phase", "gave-up", "was", s.decision)
    s.decision = "ignore"
  end

  if s.decision == "ignore" then
    LAB_SOUND_IGNORED[id] = true
    local ok, err = pcall(orig, ai)
    LAB_SOUND_IGNORED[id] = nil
    if not ok then error(err) end
    return
  end

  s.step = s.step + 1
  if s.decision == "turn" then
    if s.step == 1 then
      ai:AddTopGoal(GOAL_COMMON_Turn, 3, TARGET_SOUND, 0, 0, 0)
      ai:AddTopGoal(GOAL_COMMON_Wait, 2, TARGET_NONE)
    else
      -- He looked and heard nothing more.
      s.decision = "ignore"
      return orig(ai)
    end
  else
    if s.step == 1 or ai:GetDist(TARGET_SOUND) > 2.5 then
      ai:AddTopGoal(GOAL_COMMON_ApproachTarget, 10, TARGET_SOUND, 2, TARGET_SELF, s.decision == "walk", -1)
    else
      -- Arrived: look round once, then let it go.
      hot_log("hear", "think", id, "phase", "arrived")
      ai:AddTopGoal(GOAL_COMMON_Turn, 2, TARGET_SOUND, 0, 0, 0)
      ai:AddTopGoal(GOAL_COMMON_Wait, 2, TARGET_NONE)
      s.decision = "ignore"
    end
  end
end)
