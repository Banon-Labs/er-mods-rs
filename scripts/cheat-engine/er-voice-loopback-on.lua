-- Turn on Seamless Co-op 2.0.1's own-mic loopback in the running game: the loopback call at
-- ersc.dll+92F75 is skipped unless bit 0x10 of the voice flags byte is set (ersc.dll+92EFD
-- test [rsi+281],10 / +92F0C je); this makes that jump a 2-byte nop, the same patch as the
-- "Hear my own voice" entry of er-voice-chat-positional.CT. Run by er-ce-run-lua.py; acts only
-- when ER_CE_RUN_LOG is set.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ok, err = pcall(function()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  local j = getAddressSafe('ersc.dll+92F0C')
  local b = readBytes(j, 2, true)
  if b and b[1] == 0x74 and b[2] == 0x70 then
    writeBytes(j, 0x66, 0x90)
    note('loopback jump at ersc.dll+92F0C is now a nop')
  elseif b and b[1] == 0x66 and b[2] == 0x90 then
    note('loopback jump was already a nop')
  else
    note('ersc.dll+92F0C is not the Seamless 2.0.1 loopback jump; nothing changed')
  end
end)
if not ok then note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
