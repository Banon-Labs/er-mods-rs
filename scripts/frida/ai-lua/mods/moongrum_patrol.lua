-- Moongrum patrols the navmesh boundary after 10 s idle.
--
-- Idle means his logic (logic id 10000, common10000_Logic) is deciding his next top goal while he
-- is out of battle, not searching, and has no event request. Once that has held for PATROL_IDLE_S,
-- each logic call queues one walk step instead of the game's non-battle act:
--   1. Probe the navmesh along 12 rays around him (how far the mesh extends each way).
--   2. Not near an edge: walk toward the nearest one.
--   3. At an edge: walk along it, keeping it on the same side, as far as the mesh allows.
-- Anything that ends idle (battle, a search, an event) hands control straight back to the game.
--
-- Engine calls used, all as the game's own scripts call them:
--   ai:GetExistMeshOnLineDistSpecifyAngleEx(TARGET_SELF, angle, dist, AI_SPA_DIR_TYPE_TargetF,
--     radius, 0) -> mesh length along the ray (SpaceCheck, common_func_plan)
--   ai:SetAIFixedMoveTargetSpecifyAngle(TARGET_SELF, angle, dist, AI_SPA_DIR_TYPE_TargetF)
--     -> POINT_AI_FIXED_POS (014020_logic)
--   ai:AddTopGoal(GOAL_COMMON_MoveToSomewhere, life, point, AI_DIR_TYPE_CENTER, arriveDist,
--     turnTarget, walk) (014020_logic, common_logic_func)
-- Angles are degrees relative to his facing. os.clock() is wall time in this state (measured).

PATROL_IDLE_S = 10      -- idle this long before patrolling
PATROL_SCAN = 30        -- ray length, m
PATROL_RAYS = 12        -- rays per scan
PATROL_NEAR_EDGE = 3    -- an edge closer than this means he is on the boundary, m
PATROL_STEP = 8         -- longest walk per step along the edge, m
PATROL_MARGIN = 1.5     -- stop this far short of the edge, m
PATROL_LIFE = 8         -- seconds one step may take before logic decides again
PATROL_HAND = 1         -- 1: edge kept on one side, -1: the other

-- Per-NPC state, keyed by think id; kept across re-applies.
PATROL_STATE = PATROL_STATE or {}

local function norm(a)
  return a - 360 * math.floor(a / 360)
end

-- Set by moongrum_hearing.lua for the duration of a logic call when it has decided a sound was not
-- heard well enough to react to.
LAB_SOUND_IGNORED = LAB_SOUND_IGNORED or {}

local function idle_now(ai, id)
  if ai:IsBattleState() then return false, "battle" end
  local searching = ai:IsSearchHighState() or ai:IsSearchLowState()
  -- A search the hearing mod is ignoring is not a reason to stop, unless he has an enemy too.
  if searching and not (LAB_SOUND_IGNORED[id] and ai:GetDist(TARGET_ENE_0) < 0) then
    return false, "search"
  end
  -- Only the requests common10000_Logic acts on (100/110 walk home, 80 gesture); any other value,
  -- including the one a dynamic spawn carries, falls through to its normal path.
  local ev = ai:GetEventRequest()
  if ev == 100 or ev == 110 or ev == 80 then return false, "event " .. ev end
  return true, "idle"
end

local function scan(ai)
  local r = ai:GetMapHitRadius(TARGET_SELF)
  local d = {}
  local best = 0
  for i = 0, PATROL_RAYS - 1 do
    local a = i * 360 / PATROL_RAYS
    d[i] = ai:GetExistMeshOnLineDistSpecifyAngleEx(TARGET_SELF, a, PATROL_SCAN, AI_SPA_DIR_TYPE_TargetF, r, 0)
    if d[i] < d[best] then best = i end
  end
  return d, best
end

-- A step he made no progress on (measured 2026-10-05: "along-edge, angle 0, 6.5 m" re-issued for
-- 30 s with his position frozen; the mesh ray said there was room, the walk went nowhere). Since he
-- neither moved nor turned, the next scan picks the same ray again, so a stalled step bumps
-- s.skip, which rotates the choice further round; progress resets it.
PATROL_STUCK_GAIN = 1   -- he must have closed at least this much of the last step, m

local function stalled(ai, s)
  if s.asked == nil then return false end
  local left = ai:GetDist(POINT_AI_FIXED_POS)
  return left > s.asked - PATROL_STUCK_GAIN, left
end

local function step(ai, id, s)
  local stuck, left = stalled(ai, s)
  if stuck then
    s.skip = (s.skip or 0) + 1
    hot_log("patrol", "think", id, "phase", "stuck", "asked", math.floor(s.asked * 10) / 10,
      "left", math.floor(left * 10) / 10, "skip", s.skip)
  else
    s.skip = 0
  end
  local d, e = scan(ai)
  local step_deg = 360 / PATROL_RAYS
  local angle, dist, phase
  if d[e] > PATROL_NEAR_EDGE and s.skip == 0 then
    phase = "to-edge"
    angle = e * step_deg
    dist = d[e] - PATROL_MARGIN
  else
    -- Along the edge: the first ray turning away from it with room to walk, starting square to it,
    -- passing over s.skip candidates that already stalled.
    phase = "along-edge"
    local passed = 0
    for k = 3, PATROL_RAYS - 1 do
      local i = math.mod(e + PATROL_HAND * k + PATROL_RAYS * 2, PATROL_RAYS)
      if d[i] >= PATROL_NEAR_EDGE then
        if passed >= math.mod(s.skip, PATROL_RAYS) then
          angle = i * step_deg
          dist = math.min(d[i] - PATROL_MARGIN, PATROL_STEP)
          break
        end
        passed = passed + 1
      end
    end
    if angle == nil then
      phase = "boxed-in"
      angle = norm(e * step_deg + 180)
      dist = 1
    end
  end
  s.asked = dist
  ai:SetAIFixedMoveTargetSpecifyAngle(TARGET_SELF, angle, dist, AI_SPA_DIR_TYPE_TargetF)
  ai:AddTopGoal(GOAL_COMMON_MoveToSomewhere, PATROL_LIFE, POINT_AI_FIXED_POS, AI_DIR_TYPE_CENTER,
    0.5, TARGET_SELF, true)
  hot_log("patrol", "think", id, "phase", phase, "angle", angle, "dist", math.floor(dist * 10) / 10,
    "edge", math.floor(d[e] * 10) / 10)
end

lab_override("common10000_Logic", function(orig, ai)
  PATROL_CALLS = (PATROL_CALLS or 0) + 1
  local target, id = lab_is_target(ai)
  PATROL_BY_ID = PATROL_BY_ID or {}
  PATROL_BY_ID[id] = (PATROL_BY_ID[id] or 0) + 1
  if not target then return orig(ai) end
  local s = PATROL_STATE[id]
  if s == nil then
    s = { since = nil, patrolling = false }
    PATROL_STATE[id] = s
  end
  local now = os.clock()
  -- State is keyed by think id, so a respawned Moongrum would inherit the old one's idle clock
  -- (measured: he patrolled immediately after a respawn). Logic runs every few seconds while he
  -- exists, so a long gap means a new character.
  if s.last ~= nil and now - s.last > 15 then
    s.since, s.patrolling, s.asked, s.skip = nil, false, nil, 0
  end
  s.last = now
  local ok, why = idle_now(ai, id)
  s.why = why
  if not ok then
    if s.patrolling then hot_log("patrol", "think", id, "phase", "stop", "why", why) end
    s.since = nil
    s.patrolling = false
    s.asked = nil
    return orig(ai)
  end
  if s.since == nil then s.since = now end
  if now - s.since < PATROL_IDLE_S then
    -- The game's own logic would react to the ignored sound, so stand instead.
    if LAB_SOUND_IGNORED[id] then
      ai:AddTopGoal(GOAL_COMMON_Wait, 1, TARGET_NONE)
      return
    end
    return orig(ai)
  end
  if not s.patrolling then
    s.patrolling = true
    hot_log("patrol", "think", id, "phase", "start", "idle_s", math.floor(now - s.since))
  end
  -- An error in here is swallowed by the engine's pcall and he just stands, so report it and fall
  -- back to the game's own logic.
  local ok, err = pcall(step, ai, id, s)
  if not ok then
    hot_log("patrol", "think", id, "phase", "error", "error", tostring(err))
    return orig(ai)
  end
end)
