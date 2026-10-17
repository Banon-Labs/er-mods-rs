-- Load er-voice-chat-positional.CT into this Cheat Engine, tick its entry and the
-- "Hear my own voice" child, then log the table's counters every 3 s for ER_CE_RUN_SECONDS
-- (default 300) and stay open. Run by er-ce-run-lua.py with ER_CE_TABLE naming the .CT; acts only
-- when ER_CE_RUN_LOG is set. The work runs from timers: loadTable called straight from autorun
-- never returned (2026-10-17).
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local table_path = os.getenv('ER_CE_TABLE')
local seconds = tonumber(os.getenv('ER_CE_RUN_SECONDS') or '300')

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

-- Depth-first: the user-facing table keeps its entries under a "Debug" group.
local function child(record, prefix)
  for i = 0, record.Count - 1 do
    local c = record.Child[i]
    if c.Description:sub(1, #prefix) == prefix then return c end
    local found = child(c, prefix)
    if found then return found end
  end
  return nil
end

note('armed')
local root = nil
local tries = 0
local started = nil
local timer = createTimer(nil, false)
timer.Interval = 2000
timer.OnTimer = function()
  timer.Enabled = false
  local ok, err = pcall(function()
    if root == nil then
      tries = tries + 1
      if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
      if getOpenedProcessID() == 0 then error('eldenring.exe is not running') end
      -- A Cheat Engine that held this table and was closed leaves both calls pointing at its own
      -- code; point them back at Seamless's playback call (ersc+A5EC0) so the entry's byte
      -- checks accept them.
      for _, site in ipairs({0x9354F, 0x92F75}) do
        local a = getAddressSafe('ersc.dll') + site
        local b = readBytes(a, 5, true)
        local rel = 0xA5EC0 - (site + 5)
        if b and b[1] == 0xE8 then
          local want = {0xE8, rel & 0xFF, (rel >> 8) & 0xFF, (rel >> 16) & 0xFF, (rel >> 24) & 0xFF}
          if b[2] ~= want[2] or b[3] ~= want[3] or b[4] ~= want[4] or b[5] ~= want[5] then
            writeBytes(a, want)
            note(string.format('ersc+%X pointed back at ersc+A5EC0', site))
          end
        end
      end
      if getAddressList().Count == 0 then loadTable(table_path) end
      local r = getAddressList().getMemoryRecord(0)
      r.Active = true
      if not r.Active then error('entry did not activate') end
      local own = child(r, 'Hear my own voice')
      if own then own.Active = true end
      local mic = child(r, 'Mic capture counters')
      if mic then mic.Active = true end
      note('mic capture counters ' .. (mic and tostring(mic.Active) or 'missing'))
      root = r
      started = os.time()
      note(string.format('"%s" active; own-voice loopback %s', r.Description,
                         own and tostring(own.Active) or 'missing'))
      timer.Interval = 3000
    else
      -- Every value child of the entry (script children have no value and are skipped).
      local parts = {}
      local function collect(record)
        for i = 0, record.Count - 1 do
          local c = record.Child[i]
          if c.Type ~= vtAutoAssembler and not c.IsGroupHeader then
            parts[#parts + 1] = c.Description:gsub(' %(.*', '') .. '=' .. tostring(c.Value)
          end
          collect(c)
        end
      end
      collect(root)
      local mic = child(root, 'Mic capture counters')
      if mic then
        for i = 0, mic.Count - 1 do
          parts[#parts + 1] = mic.Child[i].Description .. '=' .. tostring(mic.Child[i].Value)
        end
      end
      note(table.concat(parts, '  '))
    end
  end)
  if not ok then
    note('error: ' .. tostring(err))
    if root == nil and tries >= 5 then timer.destroy(); note('done'); return end
  end
  if started ~= nil and os.time() - started >= seconds then
    timer.destroy(); note('stopped logging; entry stays ticked'); note('done'); return
  end
  timer.Enabled = true
end
timer.Enabled = true
