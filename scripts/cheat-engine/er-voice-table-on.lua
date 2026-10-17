-- Load a voice chat cheat table into this Cheat Engine, attach to eldenring.exe and tick its
-- first entry, then stay open (the table's Disable restores every byte when unticked).
-- The work runs from a timer, after Cheat Engine has finished starting: loadTable called directly
-- from autorun never returned (2026-10-17). Run by er-ce-run-lua.py with ER_CE_TABLE naming the
-- .CT; acts only when ER_CE_RUN_LOG is set.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local table_path = os.getenv('ER_CE_TABLE')

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

note('armed')
local tries = 0
local timer = createTimer(nil, false)
timer.Interval = 2000
timer.OnTimer = function()
  timer.Enabled = false
  tries = tries + 1
  local ok, err = pcall(function()
    if table_path == nil or table_path == '' then error('ER_CE_TABLE is not set') end
    if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
    if getOpenedProcessID() == 0 then error('eldenring.exe is not running') end
    if getAddressList().Count == 0 then loadTable(table_path) end
    local record = getAddressList().getMemoryRecord(0)
    if record == nil then error('table has no entries') end
    record.Active = true
    note(string.format('"%s" active=%s', record.Description, tostring(record.Active)))
    if not record.Active then error('entry did not activate') end
  end)
  if ok then
    timer.destroy(); note('done')
  elseif tries >= 5 then
    timer.destroy(); note('error: ' .. tostring(err)); note('done')
  else
    note(string.format('try %d: %s', tries, tostring(err))); timer.Enabled = true
  end
end
timer.Enabled = true
