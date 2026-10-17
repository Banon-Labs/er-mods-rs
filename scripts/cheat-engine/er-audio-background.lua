-- Keep Elden Ring's audio playing while its window is unfocused (1.17.1).
--
-- The game silences all output when it loses focus: its sound wrapper at 0x142216f40
-- (push rbx / sub rsp,20 / movzx ebx,cl / call the sound-engine getter 0x142223f90 / jmp to the
-- stub that tail-calls AK::SoundEngine::Suspend, an export of eldenring.exe at 0x142242850) is
-- the only path in the image to Suspend, and 0x142216f70 is its WakeupFromSuspend twin. This
-- makes the suspend wrapper return AK_Success without suspending, then wakes the engine once in
-- case it is already suspended.
--
-- er-ce-run-lua.py copies this into Cheat Engine's autorun folder; it does nothing unless
-- ER_CE_RUN_LOG names the log file. Disable = restore the two bytes at 0x142216f40 and the rest.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local SUSPEND = 0x142216F40
local WAKE = 0x142216F70
local SUSPEND_BYTES = {0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x0F, 0xB6, 0xD9}
local WAKE_BYTES = {0x48, 0x83, 0xEC, 0x28, 0xE8}

local function matches(address, want)
  local got = readBytes(address, #want, true)
  if got == nil then return false end
  for i = 1, #want do if got[i] ~= want[i] then return false end end
  return true
end

local function run()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  note('armed')
  if matches(SUSPEND, {0xB8, 0x01, 0x00, 0x00, 0x00, 0xC3}) then
    note('suspend wrapper already patched')
  elseif not matches(SUSPEND, SUSPEND_BYTES) or not matches(WAKE, WAKE_BYTES) then
    note('bytes at the suspend/wake wrappers are not the 1.17.1 ones; nothing changed')
    return
  else
    -- mov eax,1 / ret
    writeBytes(SUSPEND, 0xB8, 0x01, 0x00, 0x00, 0x00, 0xC3)
    note('suspend wrapper now returns AK_Success without suspending')
  end
  local r = executeCodeEx(0, 5000, WAKE)
  note(string.format('wakeup returned %s', tostring(r)))
end

local ok, err = pcall(run)
if not ok then note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
