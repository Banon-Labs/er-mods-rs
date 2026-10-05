-- Moonrithyll does not chain another swing onto one she predicts will kill.
--
-- Prediction: the enemy's HP, ai:GetHp(TARGET_ENE_0) (absolute: measured 2026-10-05, her own read
-- 8375 and the player's 522, the same as their ChrIns data), against LAB_WORLD.swing_damage, the
-- weakest of her last five hits. spawn-npc.js measures those in the game's CalculateDamage2, so the
-- number already carries her weapon, her stats and the defences of what she has been hitting. A
-- kill is predicted when hp <= swing_damage; a target she has not hit yet is judged on hits against
-- others, and with no hit recorded at all nothing is predicted.
--
-- How her attacks are queued (029999_battle, npc_comboattack_withmove, decompiled):
--   GeneralNPC_ActNN(ai, goal, paramTbl)  adds a whole chain at once: an opening attack goal
--       (AttackTunableSpin, ComboAttackTunableSpin) and then the follow-ups (ComboTunable_*,
--       ComboRepeat*, ComboFinal) with goal:AddSubGoal
--   NpcComboAttackWithMove_AddInnerGoal(ai, goal)  adds one swing each time the last one ends;
--       goal:GetNumber(0) counts the swings made so far, and returning 0 ends the combo
-- So during one of her acts goal:AddSubGoal is filtered: the first attack goal goes through and is
-- judged, and when it is judged a kill every later attack goal of that act is dropped. Non-attack
-- goals (approach, sidestep, guard, wait) always go through. In a WithMove combo the first swing is
-- judged the same way and a lethal one ends the combo before the second.
--
-- goal:AddSubGoal is a method in the goal objects' shared metatable (getmetatable(goal).__index), so
-- the filter is installed there once and does nothing outside her acts (KC_ACTIVE is nil).

KC_THINK = { [524320000] = true }

-- Every common goal that swings a weapon, except NpcComboAttack_WithMove, which only holds the
-- swings it adds itself.
KC_ATTACK = {}
for name, v in pairs(_G) do
  if type(name) == "string" and type(v) == "number" and string.find(name, "^GOAL_COMMON_")
      and (string.find(name, "Attack") or string.find(name, "Combo"))
      and name ~= "GOAL_COMMON_NpcComboAttack_WithMove" then
    KC_ATTACK[v] = string.sub(name, 13)
  end
end

KC_STATE = KC_STATE or {}

local function r0(x)
  return math.floor(x + 0.5)
end

local function predicts_kill(ai)
  local dmg = LAB_WORLD.swing_damage
  if dmg == nil then return false, -1, -1 end
  -- Never a kill on the player, should she ever have the player as her enemy again.
  if ai:GetDistAtoB(TARGET_ENE_0, TARGET_LOCALPLAYER) < 0.5 then return false, -2, dmg end
  local hp = ai:GetHp(TARGET_ENE_0)
  return hp > 0 and hp <= dmg, hp, dmg
end

-- Returns whether goal:AddSubGoal(id, ...) goes ahead.
function KC_filter(id)
  local a = KC_ACTIVE
  if a == nil or KC_ATTACK[id] == nil then return true end
  a.attacks = a.attacks + 1
  -- A target already at 0 HP is dying and takes no hits: no attack goal of the act goes through.
  if a.dead == nil then
    local ok, hp = pcall(function() return a.ai:GetHp(TARGET_ENE_0) end)
    a.dead = ok and hp ~= nil and hp <= 0
  end
  if a.dead then
    hot_log("kill-confirm", "think", a.id, "phase", "dead-target", "goal", KC_ATTACK[id])
    return false
  end
  if a.attacks == 1 then
    local kill, hp, dmg = predicts_kill(a.ai)
    a.lethal = kill
    if kill then
      hot_log("kill-confirm", "think", a.id, "phase", "lethal", "hp", r0(hp), "swing", r0(dmg),
        "goal", KC_ATTACK[id])
    end
    return true
  end
  if a.lethal then
    hot_log("kill-confirm", "think", a.id, "phase", "dropped", "goal", KC_ATTACK[id])
    return false
  end
  return true
end

-- Defined once and kept across applies; it calls the current KC_filter, so edits here apply.
KC_WRAPPER = KC_WRAPPER or function(g, id, ...)
  if KC_ACTIVE ~= nil and not KC_filter(id) then return end
  return KC_ORIG(g, id, unpack(arg))
end

local function install(goal)
  local mt = getmetatable(goal)
  local index = type(mt) == "table" and mt.__index
  if type(index) ~= "table" or type(index.AddSubGoal) ~= "function" then
    if not KC_NO_INSTALL then
      KC_NO_INSTALL = true
      hot_log("kill-confirm", "phase", "no-install", "metatable", type(mt), "index", type(index))
    end
    return false
  end
  if index.AddSubGoal ~= KC_WRAPPER then
    KC_ORIG = index.AddSubGoal
    index.AddSubGoal = KC_WRAPPER
    hot_log("kill-confirm", "phase", "installed")
  end
  return true
end

local function act_override(orig, ai, goal, paramTbl)
  local target, id = lab_is_target(ai)
  if not target or not KC_THINK[id] or not install(goal) then return orig(ai, goal, paramTbl) end
  KC_ACTIVE = { id = id, ai = ai, attacks = 0, lethal = false }
  local ok, r = pcall(orig, ai, goal, paramTbl)
  KC_ACTIVE = nil
  if not ok then error(r) end
  return r
end

for n = 1, 300 do
  local name = lab_act_name(n)
  if type(_G[name]) == "function" then lab_override(name, act_override) end
end

lab_override("NpcComboAttackWithMove_AddInnerGoal", function(orig, ai, goal)
  local target, id = lab_is_target(ai)
  if not target or not KC_THINK[id] then return orig(ai, goal) end
  KC_STATE[id] = KC_STATE[id] or {}
  local s = KC_STATE[id]
  local made = goal:GetNumber(0)
  if made == 0 then
    local kill, hp, dmg = predicts_kill(ai)
    s.lethal = kill
    if kill then
      hot_log("kill-confirm", "think", id, "phase", "lethal", "hp", r0(hp), "swing", r0(dmg),
        "goal", "NpcComboAttack_WithMove")
    end
  elseif s.lethal then
    hot_log("kill-confirm", "think", id, "phase", "dropped", "goal", "NpcComboAttack_WithMove", "swing", made + 1)
    return 0
  end
  return orig(ai, goal)
end)
