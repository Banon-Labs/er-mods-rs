-- Test: does Elden Ring (1.17.1) silence its audio because its window object says it is
-- inactive? The window singleton is [0x144861d28]; +0x88d is set to 0 on focus loss (written at
-- 0x141f2b12d and by the window procedure) and read by the frame update at 0x141f6d9b7. This
-- sets +0x88d to 1 once, while the window stays unfocused. Run by er-ce-run-lua.py.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ok, err = pcall(function()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  local window = readQword(0x144861D28)
  if window == nil or window == 0 then note('no window object'); return end
  note(string.format('window 0x%X: +0x88d=%d +0x88e=%d', window, readBytes(window + 0x88D, 1) or -1,
                     readBytes(window + 0x88E, 1) or -1))
  writeBytes(window + 0x88D, 1)
  note(string.format('set +0x88d=1, reads back %d', readBytes(window + 0x88D, 1) or -1))
end)
if not ok then note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
