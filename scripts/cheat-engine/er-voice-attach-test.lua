-- Read only: loads er-voice-chat.CT without attaching first and records whether its table script
-- attached this Cheat Engine to eldenring.exe. Run with er-ce-run-lua.py.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end
local step = 0
local t = createTimer(nil, false)
t.Interval = 2000
t.OnTimer = function()
  step = step + 1
  local ok, err = pcall(function()
    if step == 1 then
      note(string.format('before load: opened pid %d', getOpenedProcessID()))
      loadTable('Z:\\home\\banon\\Downloads\\er-voice-chat.CT', false)
      note('table loaded; er_voice_attach defined: ' .. tostring(er_voice_attach ~= nil))
      return
    end
    local b = readBytes(0x141EBBD40, 8, true)
    note(string.format('after %d s: opened pid %d, eldenring.exe pid %s, 141EBBD40 %s', (step - 1) * 2,
      getOpenedProcessID(), tostring(getProcessIDFromProcessName('eldenring.exe')),
      b and table.concat(b, ',') or 'unreadable'))
  end)
  if not ok then note('error ' .. tostring(err)) end
  if step >= 3 or not ok then
    t.destroy()
    note('done')
    if close_when_done then closeCE() end
  end
end
t.Enabled = true
