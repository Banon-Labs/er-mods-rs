-- Read only: where this Cheat Engine resolves ersc.dll and Steam's client module, and whether it
-- can read Seamless there. Run with er-ce-run-lua.py; does nothing unless ER_CE_RUN_LOG is set.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end
local t = createTimer(nil, false)
t.Interval = 2000
t.OnTimer = function()
  t.destroy()
  local ok, err = pcall(function()
    if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
    note(string.format('pid %d', getOpenedProcessID()))
    for _, m in ipairs(enumModules() or {}) do
      local n = m.Name:lower()
      if n:find('ersc') or n:find('steamclient') or n == 'eldenring.exe' then
        note(string.format('module %s %X size %s path %s', m.Name, m.Address,
          tostring(getModuleSize(m.Name)), tostring(m.PathToFile)))
      end
    end
    local a = getAddressSafe('ersc.dll')
    note('getAddressSafe(ersc.dll) = ' .. (a and string.format('%X', a) or 'nil'))
    for _, x in ipairs({'ersc.dll+32C09', '180032C09', 'ersc.dll+222030'}) do
      local b = readBytes(getAddressSafe(x) or 0, 8, true)
      note(x .. ' -> ' .. (b and table.concat(b, ',') or 'nil'))
    end
  end)
  if not ok then note('error ' .. tostring(err)) end
  note('done')
  if close_when_done then closeCE() end
end
t.Enabled = true
