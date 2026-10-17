-- Test where Elden Ring's (1.17.1) unfocused silence comes from: record Wwise's own final mix with
-- AK::SoundEngine::StartOutputCapture (export 0x142242340, takes a wide file name) for a few
-- seconds, then StopOutputCapture (0x142242710). Audio in that file while the game's PipeWire
-- stream carries zeros means the mute happens after Wwise's mix, in its output sink or below.
-- Run by er-ce-run-lua.py; acts only when ER_CE_RUN_LOG is set.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local SECONDS = tonumber(os.getenv('ER_CE_RUN_SECONDS') or '5')

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ok, err = pcall(function()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  local name = allocateMemory(1024)
  writeString(name, 'er_output_capture.wav', true)
  writeBytes(name + 42, 0, 0)
  local r = executeCodeEx(0, 5000, 0x142242340, name)
  note(string.format('StartOutputCapture returned %s (1 = AK_Success)', tostring(r)))
  sleep(SECONDS * 1000)
  r = executeCodeEx(0, 5000, 0x142242710)
  note(string.format('StopOutputCapture returned %s', tostring(r)))
end)
if not ok then note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
