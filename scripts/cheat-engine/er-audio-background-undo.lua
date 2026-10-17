-- Undo er-audio-background.lua: put the original first bytes back on Elden Ring's (1.17.1) sound
-- suspend wrapper at 0x142216f40. Run by er-ce-run-lua.py; acts only when ER_CE_RUN_LOG is set.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ok, err = pcall(function()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  local b = readBytes(0x142216F40, 6, true)
  if b and b[1] == 0xB8 and b[6] == 0xC3 then
    writeBytes(0x142216F40, 0x40, 0x53, 0x48, 0x83, 0xEC, 0x20)
    note('suspend wrapper restored')
  else
    note('suspend wrapper was not patched; nothing changed')
  end
end)
if not ok then note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
