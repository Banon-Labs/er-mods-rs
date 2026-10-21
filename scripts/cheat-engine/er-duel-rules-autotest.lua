-- Ticks the duel rules table without hands, for a live test of er-duel-rules.CT under Wine.
-- er-duel-rules-ce.py --autotest copies this into Cheat Engine's autorun folder; it does
-- nothing unless ER_DUEL_RULES_AUTOTEST names the table to load, so a person starting the same
-- Cheat Engine install by hand never sees it act.
local table_path = os.getenv('ER_DUEL_RULES_AUTOTEST')
if table_path == nil or table_path == '' then return end

local note_path = os.getenv('ER_DUEL_RULES_AUTOTEST_LOG') or 'er-duel-rules-autotest.log'
local function note(line)
  local f = io.open(note_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ENTRY = 'Duel rules referee (Elden Ring 1.17.2)'
local tries = 0
local timer = createTimer(nil, false)
timer.Interval = 2000
timer.OnTimer = function()
  timer.Enabled = false
  tries = tries + 1
  local ok, err = pcall(function()
    if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
    if getOpenedProcessID() == 0 then error('eldenring.exe is not running') end
    note(string.format('opened eldenring.exe pid %d', getOpenedProcessID()))
    if getAddressList().getMemoryRecordByDescription(ENTRY) == nil then loadTable(table_path) end
    local record = getAddressList().getMemoryRecordByDescription(ENTRY)
    if record == nil then error('the table has no entry named ' .. ENTRY) end
    record.Active = true
    note('entry active=' .. tostring(record.Active))
    if not record.Active then error('the entry did not enable') end
  end)
  if ok then
    timer.destroy()
  else
    note('try ' .. tries .. ': ' .. tostring(err))
    if tries < 10 then timer.Enabled = true else timer.destroy() end
  end
end
timer.Enabled = true
note('autotest armed for ' .. table_path)
