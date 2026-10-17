-- Ticks er-voice-chat.CT without hands and records what it changed, for a live test under Wine.
-- er-voice-chat-ce.py --autotest copies this into Cheat Engine's autorun folder; it does nothing
-- unless ER_VOICE_CHAT_AUTOTEST names the table, so a hand-started Cheat Engine never sees it act.
local table_path = os.getenv('ER_VOICE_CHAT_AUTOTEST')
if table_path == nil or table_path == '' then return end

local note_path = os.getenv('ER_VOICE_CHAT_AUTOTEST_LOG') or 'er-voice-chat-autotest.log'
local function note(line)
  local f = io.open(note_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ENTRY = 'Voice chat with every player (Elden Ring 1.17.1)'
local function hex(addr, n)
  local t = readBytes(addr, n, true)
  if t == nil then return 'unreadable' end
  local out = {}
  for i = 1, #t do out[i] = string.format('%02X', t[i]) end
  return table.concat(out, ' ')
end
local function snapshot(label, record)
  local parts = {label, 'mute=' .. hex('eldenring.exe+CBBF25', 3), 'party=' .. hex('eldenring.exe+9F9441', 2),
    'seamless-capture=' .. hex('ersc.dll+92D81', 6), 'seamless-playback=' .. hex('ersc.dll+A5921', 6)}
  for i = 0, record.Count - 1 do
    local child = record.Child[i]
    parts[#parts + 1] = string.format('[%s]=%s', child.Description, tostring(child.Value))
  end
  note(table.concat(parts, '  '))
end

local tries = 0
local timer = createTimer(nil, false)
timer.Interval = 2000
timer.OnTimer = function()
  timer.Enabled = false
  tries = tries + 1
  local ok, err = pcall(function()
    if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
    if getOpenedProcessID() == 0 then error('eldenring.exe is not running') end
    if getAddressList().getMemoryRecordByDescription(ENTRY) == nil then loadTable(table_path) end
    local record = getAddressList().getMemoryRecordByDescription(ENTRY)
    if record == nil then error('the table has no entry named ' .. ENTRY) end
    snapshot('before', record)
    record.Active = true
    if not record.Active then error('the entry did not enable') end
    snapshot('ticked', record)
    record.Active = false
    snapshot('unticked', record)
    record.Active = true
    snapshot('ticked again', record)
    note('done; the entry is left ticked')
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
