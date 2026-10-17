-- Set the positional voice table's live "Voice volume (x)" in the running game, from outside the
-- Cheat Engine that holds the table. ER_VOICE_GAIN_ADDR is the table's er_vp_data+460 (find the
-- data block by its "Play_VoiceChat" string at +A00), ER_VOICE_GAIN the new factor. Run by
-- er-ce-run-lua.py; acts only when ER_CE_RUN_LOG is set.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local ok, err = pcall(function()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  local addr = tonumber(os.getenv('ER_VOICE_GAIN_ADDR'))
  local gain = tonumber(os.getenv('ER_VOICE_GAIN'))
  if addr == nil or gain == nil or gain <= 0 or gain > 32 then error('bad address or gain') end
  if readString(addr - 0x460 + 0xA00, 14) ~= 'Play_VoiceChat' then
    error(string.format('0x%X is not a positional voice table data block', addr - 0x460))
  end
  local before = readFloat(addr)
  writeFloat(addr, gain)
  note(string.format('voice volume %.2f -> %.2f', before or -1, readFloat(addr) or -1))
end)
if not ok then note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
