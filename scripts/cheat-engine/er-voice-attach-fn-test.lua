-- Read only: runs er-voice-chat.CT's er_voice_attach (copied from its table script) in a
-- Cheat Engine that is not attached, and records the result. Run with er-ce-run-lua.py.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end
function er_voice_attach()
  local pid = getProcessIDFromProcessName('eldenring.exe')
  if pid == nil or pid == 0 then return false, 'eldenring.exe is not running' end
  if getOpenedProcessID() == pid and readBytes(0x141EBBD40, 1) ~= nil then return true end
  openProcess(pid)
  reinitializeSymbolhandler(true)
  if getOpenedProcessID() ~= pid or readBytes(0x141EBBD40, 1) == nil then
    return false, string.format('opened eldenring.exe (pid %d) but cannot read it', pid)
  end
  return true
end
local t = createTimer(nil, false)
t.Interval = 2000
t.OnTimer = function()
  t.destroy()
  local ok, a, why = pcall(er_voice_attach)
  local b = readBytes(0x141EBBD40, 8, true)
  note(string.format('er_voice_attach -> %s %s %s; opened pid %d; 141EBBD40 %s; ersc.dll %s', tostring(ok),
    tostring(a), tostring(why), getOpenedProcessID(), b and table.concat(b, ',') or 'unreadable',
    tostring(getAddressSafe('ersc.dll'))))
  note('done')
  if close_when_done then closeCE() end
end
t.Enabled = true
