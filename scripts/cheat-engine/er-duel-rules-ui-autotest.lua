-- Drives the duel rules table's ban editor and Rules window with no hands, inside Cheat Engine
-- under Wine, and writes what happened to a log. scripts/test-er-duel-rules-ce.lua proves the
-- logic behind the windows on any Lua; this proves the windows themselves build, show and answer
-- their buttons in Cheat Engine 7.5, which no plain Lua can.
--
-- er-duel-rules-ce.py --ui-test copies this into Cheat Engine's autorun folder. It does nothing
-- unless ER_DUEL_RULES_UI_TEST names the referee source, writes its rules file into
-- ER_DUEL_RULES_UI_TEST_DIR (never the game's), and closes Cheat Engine when done. It needs no
-- game: nothing here opens a process.
local source = os.getenv('ER_DUEL_RULES_UI_TEST')
if source == nil or source == '' then return end
local dir = os.getenv('ER_DUEL_RULES_UI_TEST_DIR') or '.'
local logPath = dir .. '\\ui-autotest.log'
local rulesPath = dir .. '\\er-duel-rules.json'
os.remove(logPath)
os.remove(rulesPath)

local failures, passes = 0, 0
local function note(line)
  local f = io.open(logPath, 'a')
  if f then f:write(line .. '\n'); f:close() end
end
local function check(cond, what)
  if cond then passes = passes + 1; note('ok    ' .. what)
  else failures = failures + 1; note('FAIL  ' .. what) end
end
local function items(list)
  local out = {}
  for i = 0, list.Items.Count - 1 do out[#out + 1] = list.Items[i] end
  return out
end
-- Fire a handler the way the control would, reading it back off the control.
local function fire(control, event)
  local handler = control[event]
  check(type(handler) == 'function', event .. ' reads back as the function it was given')
  if type(handler) == 'function' then handler(control) end
end

local function run()
  local M = dofile(source)
  note('loaded ' .. source)

  -- The ban editor, shown and driven the way a person drives it.
  local ed = M.banEditorForm(nil)
  ed.form.show()
  ed.name.Text = 'Bleed katanas'
  ed.search.Text = 'katana'
  check(ed.results.Items.Count > 0 and ed.results.Items.Count == #ed.hits,
    'typing in Find lists names: ' .. ed.results.Items.Count)
  ed.kind.ItemIndex = 2
  fire(ed.kind, 'OnChange')
  check(ed.results.Items[0] == 'Weapon class: Katana', 'the kind box narrows the list: ' .. tostring(ed.results.Items[0]))
  ed.results.ItemIndex = 0
  ed.addNew.doClick()
  check(ed.conditions.Items.Count == 1 and ed.conditions.Items[0] == 'Weapon: class Katana',
    'Add as a new condition: ' .. table.concat(items(ed.conditions), ' | '))
  ed.kind.ItemIndex = 3
  ed.search.Text = 'blood'
  check(ed.results.Items[0] == 'Affinity: Blood', 'affinities: ' .. tostring(ed.results.Items[0]))
  ed.results.ItemIndex = 0
  ed.select(1)
  ed.addOr.doClick()
  check(ed.conditions.Items[0] == 'Weapon: class Katana, affinity Blood', 'Add to the selected condition: ' .. ed.conditions.Items[0])
  ed.where.ItemIndex = 4
  fire(ed.where, 'OnChange')
  ed.buff.Checked = true
  check(ed.conditions.Items[0] == 'Weapon: class Katana, affinity Blood, two-handed, with a weapon buff',
    'grip and buff: ' .. ed.conditions.Items[0])
  ed.kind.ItemIndex = 5
  ed.search.Text = 'lord of blood'
  ed.results.ItemIndex = 0
  ed.addNew.doClick()
  ed.ask = function() return '!= 125' end
  ed.other.ItemIndex = 3
  ed.addOther.doClick()
  check(ed.conditions.Items.Count == 3 and ed.conditions.Items[2] == 'Rune level != 125',
    'Add other condition: ' .. table.concat(items(ed.conditions), ' | '))
  ed.select(3)
  ed.remove.doClick()
  check(ed.conditions.Items.Count == 2, 'Remove condition: ' .. table.concat(items(ed.conditions), ' | '))
  ed.ok.doClick()
  check(ed.result ~= nil and ed.result.name == 'Bleed katanas', 'OK accepts the ban')
  local ban = ed.result
  ed.form.hide()
  ed.form.destroy()

  -- The Rules window, saving into this test's own folder.
  local saved = 0
  local rw = M.rulesWindowForm({
    config = M.defaultConfig(),
    save = function(cfg)
      local f = assert(io.open(rulesPath, 'wb'))
      f:write(M.writeConfig(cfg))
      f:close()
      saved = saved + 1
      return nil
    end,
    edit = function() return ban end,
  })
  rw.form.show()
  check(rw.rules.Items.Count == #M.RULE_ORDER, 'every built-in rule is listed: ' .. rw.rules.Items.Count)
  check(rw.rules.Items[0].Checked, 'and ticked')
  for i, key in ipairs(M.RULE_ORDER) do
    if key == 'LightRoll' then rw.rules.Items[i - 1].Checked = false end
  end
  rw.numbers.runeLevel.Text = '150'
  rw.pages.TabIndex = 1
  rw.newButton.doClick()
  check(rw.bans.Items.Count == 1 and rw.bans.Items[0].Caption == 'Bleed katanas', 'New ban lists the ban')
  rw.saveButton.doClick()
  local f = io.open(rulesPath, 'rb')
  local text = f and f:read('a') or ''
  if f then f:close() end
  local cfg, problems = M.readConfig(text)
  check(saved == 1 and #problems == 0, 'Save and apply writes a file that reads back clean: ' .. table.concat(problems, '; '))
  check(#cfg.off == 1 and cfg.off[1] == 'LightRoll' and cfg.runeLevel == 150, 'with the rule off and the level changed')
  check(#cfg.bans == 1 and cfg.bans[1].all[1].where == 'twoHanded' and cfg.bans[1].all[1].buff == true,
    'and the ban as edited')
  check(rw.rules.Items[0].Caption == 'Rune Level: 150', 'the rule is reworded with its number: ' .. rw.rules.Items[0].Caption)
  rw.bans.Items[0].Checked = false
  rw.saveButton.doClick()
  cfg = M.readConfig(io.open(rulesPath, 'rb'):read('a'))
  check(cfg.bans[1].on == false, 'unticking a ban saves it switched off')
  rw.bans.ItemIndex = 0
  rw.deleteButton.doClick()
  rw.saveButton.doClick()
  cfg = M.readConfig(io.open(rulesPath, 'rb'):read('a'))
  check(#cfg.bans == 0 and cfg.off[1] == 'LightRoll', 'Delete removes the ban and keeps the rest')
  rw.numbers.runeLevel.Text = 'lots'
  check(not rw.apply() and rw.status.Caption:find('Rune level must be a number', 1, true) ~= nil,
    'a bad number is refused: ' .. rw.status.Caption)
  rw.form.hide()
  rw.form.destroy()
end

local timer = createTimer(nil, false)
timer.Interval = 1000
timer.OnTimer = function()
  timer.Enabled = false
  timer.destroy()
  local ok, err = pcall(run)
  if not ok then failures = failures + 1; note('FAIL  stopped: ' .. tostring(err)) end
  note(string.format('%s: %d passed, %d failed', failures == 0 and 'PASS' or 'FAIL', passes, failures))
  closeCE()
end
timer.Enabled = true
note('ui autotest armed for ' .. source)
