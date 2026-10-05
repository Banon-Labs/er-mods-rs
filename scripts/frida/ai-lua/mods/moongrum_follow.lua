-- Moongrum is on your side and keeps to you, on a leash.
--
--   you are standing on a lift             above everything else: he runs onto it beside you and
--                                          stays put until you step off (LAB_WORLD.player_on_lift)
--
--   enemy near you and he is fighting it   he fights as he normally would
--   further than FOLLOW_LEASH from you     he drops whatever he is doing, fight included, and runs
--   further than FOLLOW_NEAR, no fight     he walks back to you, or runs beyond FOLLOW_RUN
--   within FOLLOW_NEAR, no fight           the game and the other mods decide (hearing, patrol)
--
-- Team: lab_team(47) puts him on TEAM_TYPE 47, Spirit Summon, the team a Spirit Ash fights on.
-- The lab writes it into his character (spawn-npc.js), since AI Lua has no call that sets a team.
--
-- Why fights call _COMMON_AddBattleGoal instead of the game's logic: his normal battle logic
-- (_COMMON_SetBattleActLogic, common_logic_func) measures how far he has strayed from where he
-- spawned (GetMovePointEffectRange against the think param's maxBackhomeDist) and walks or
-- fade-warps him home past it. Following you is exactly that kind of straying. _COMMON_AddBattleGoal
-- is the call that logic ends in when he fights, without the check in front of it.
--
-- Why the leash also sits in MakeNPCProbArr: the battle goal is added with life -1, so his logic is
-- not asked again until the fight ends. Every attack he picks goes through MakeNPCProbArr first, and
-- ai:Replaning() there ends the battle goal so the logic below runs and sends him back.

lab_team(47)

-- No home: his home is wherever he stands. Measured 2026-10-05: past maxBackhomeDist (45 m) from
-- his spawn point the engine stops running his Lua and walks him back itself, so following you out
-- of his room ended with him frozen against a wall and none of the logic below being asked.
lab_home("self")

FOLLOW_NEAR = 5         -- out of a fight, further than this from you and he comes back, m
FOLLOW_ARRIVE = 3       -- he stops this close to you, m
FOLLOW_RUN = 10         -- further than this he runs instead of walking, m
FOLLOW_LEASH = 15       -- further than this he breaks off a fight and runs to you, m
FOLLOW_ENEMY_NEAR = 15  -- an enemy at most this far from you counts as nearby, m

FOLLOW_STUCK_GAIN = 2   -- a run toward you that closed less than this is stuck, m
FOLLOW_WARP_BEHIND = 2  -- a stuck run ends with him warped this far behind you, m

FOLLOW_LIFT_ON = 1.2    -- with you on a lift, this close to you counts as on it with you, m
FOLLOW_LIFT_LIFE = 4    -- each boarding run is re-decided after this long, s

FOLLOW_STATE = FOLLOW_STATE or {}

local function r1(x)
  return math.floor(x * 10 + 0.5) / 10
end

-- His current enemy, if it is near you. A target that is you does not count: before the team
-- change he had you as his enemy, and that memory may outlive it.
local function enemy_near(ai)
  local e = ai:GetDist(TARGET_ENE_0)
  if e < 0 then return false, e end
  if ai:GetDistAtoB(TARGET_ENE_0, TARGET_LOCALPLAYER) < 0.5 then return false, -2 end
  return ai:GetDistAtoB(TARGET_LOCALPLAYER, TARGET_ENE_0) <= FOLLOW_ENEMY_NEAR, e
end

local function mode_of(ai)
  local d = ai:GetDist(TARGET_LOCALPLAYER)
  if d < 0 then return nil, d, -1 end
  local near, e = enemy_near(ai)
  -- You are on a lift: nothing else counts, a fight included. He boards by closing to
  -- FOLLOW_LIFT_ON of you, since you are standing on it, then holds still until you step off.
  if LAB_WORLD.player_on_lift then
    if d > FOLLOW_LIFT_ON then return "lift-board", d, e end
    return "lift-ride", d, e
  end
  if d > FOLLOW_LEASH then return "leash", d, e end
  if near and ai:IsBattleState() then return "fight", d, e end
  if d > FOLLOW_RUN then return "run", d, e end
  if d > FOLLOW_NEAR then return "walk", d, e end
  return "near", d, e
end

local function note(id, mode, d, e)
  local s = FOLLOW_STATE[id]
  if s == nil then
    s = {}
    FOLLOW_STATE[id] = s
  end
  if s.mode ~= mode then
    s.mode = mode
    hot_log("follow", "think", id, "mode", mode, "player", r1(d), "enemy", r1(e))
  end
end

lab_override("common10000_Logic", function(orig, ai)
  local target, id = lab_is_target(ai)
  if not target then return orig(ai) end
  local mode, d, e = mode_of(ai)
  if mode == nil then return orig(ai) end
  note(id, mode, d, e)
  local s = FOLLOW_STATE[id]
  -- Something he cannot walk through stands between you: measured 2026-10-05, sent to run to you
  -- he paced 18 m short, x -13.7..-15.8, for over a minute while you saw him walking into a wall.
  -- When the last run toward you gained less than FOLLOW_STUCK_GAIN, warp him to you with the
  -- game's own GOAL_COMMON_ToTargetWarp (common_sound_behavior: life, target, direction,
  -- distance, face target, -1, -1, 0).
  local running = mode == "leash" or mode == "run" or mode == "lift-board"
  if running and s.ran_from ~= nil and s.ran_from - d < FOLLOW_STUCK_GAIN then
    hot_log("follow", "think", id, "phase", "warp", "player", r1(d), "was", r1(s.ran_from))
    s.ran_from = nil
    -- Onto the lift means right beside you; otherwise a little behind.
    local behind = mode == "lift-board" and FOLLOW_LIFT_ON - 0.2 or FOLLOW_WARP_BEHIND
    ai:AddTopGoal(GOAL_COMMON_ToTargetWarp, 10, TARGET_LOCALPLAYER, AI_DIR_TYPE_B, behind,
      TARGET_LOCALPLAYER, -1, -1, 0)
    return
  end
  s.ran_from = running and d or nil
  if mode == "lift-board" then
    ai:AddTopGoal(GOAL_COMMON_ApproachTarget, FOLLOW_LIFT_LIFE, TARGET_LOCALPLAYER, FOLLOW_LIFT_ON - 0.2,
      TARGET_SELF, false, -1)
    return
  end
  if mode == "lift-ride" then
    ai:AddTopGoal(GOAL_COMMON_Wait, 0.5, TARGET_LOCALPLAYER)
    return
  end
  if mode == "leash" or mode == "run" or mode == "walk" then
    ai:AddTopGoal(GOAL_COMMON_ApproachTarget, 10, TARGET_LOCALPLAYER, FOLLOW_ARRIVE, TARGET_SELF,
      mode == "walk", -1)
    return
  end
  if mode == "fight" then
    _COMMON_AddBattleGoal(ai)
    return
  end
  return orig(ai)
end)

lab_override("MakeNPCProbArr", function(orig, self, ai, goal, mode)
  local probs = orig(self, ai, goal, mode)
  local target, id = lab_is_target(ai)
  if target and (ai:GetDist(TARGET_LOCALPLAYER) > FOLLOW_LEASH or LAB_WORLD.player_on_lift) then
    hot_log("follow", "think", id, "phase", "leash-break", "player", r1(ai:GetDist(TARGET_LOCALPLAYER)),
      "lift", tostring(LAB_WORLD.player_on_lift == true))
    ai:Replaning()
  end
  return probs
end)
