-- One bounded test of ~/Downloads/er-voice-chat-positional.CT against the running game, with the
-- ring-drain oracle instead of an audio capture (a Cheat Engine starting silences the game's
-- output, so a capture taken now proves nothing).
--
--   ER_CE_TABLE=<.CT> ER_VOICEPOS_PCM=<s16le 48 kHz mono> \
--     python3 scripts/cheat-engine/er-ce-run-lua.py scripts/cheat-engine/er-voice-positional-test.lua
--
-- Acts only when ER_CE_RUN_LOG is set. In order:
--   1. records the bytes at the sites this table shares with er-voice-chat-nullfix.CT, which a
--      user-visible Cheat Engine may already hold ticked;
--   2. loads the table and ticks its entry; logs the patched call sites and the counters after
--      1.5 s of whatever voice the session carries (peers at ersc+9354F, own mic at +92F75);
--   3. test hook: feeds the PCM through the same core as a fake SteamID placed at the local
--      character (100 x 20 ms chunks, 20 ms apart), then reads that slot's audio input ring
--      (entity +0x124 read, +0x128 write, +0x12C queued) and whether Wwise still knows its
--      playing id (Query::GetEventIDFromPlayingID). A ring that drains at the push rate is the
--      proof Wwise is pulling the voice;
--   4. unticks the entry, checks both call sites read their original bytes again, and writes
--      back the shared bytes recorded in step 1, so the other Cheat Engine's entry is left as
--      it was; then closes this Cheat Engine.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local table_path = os.getenv('ER_CE_TABLE')
local pcm_path = os.getenv('ER_VOICEPOS_PCM')
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'

local TEST_KEY = 0x0110000100FEED01
local CHUNK, CHUNKS = 1920, 100

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local function hex(at, n)
  local t = readBytes(at, n, true)
  if t == nil then return 'unreadable' end
  local out = {}
  for i = 1, #t do out[i] = string.format('%02X', t[i]) end
  return table.concat(out, ' ')
end

local SHARED = {
  {'eldenring.exe+CBBF25', 3}, {'eldenring.exe+9F9441', 2}, {'ersc.dll+92D81', 6},
  {'eldenring.exe+3D7E89C', 2},
}

local function counters(d)
  return string.format('peer calls=%d loopback calls=%d test calls=%d created=%d failed=%d dropped=%d plays=%d tid=0x%X',
    readQword(d + 0x400), readQword(d + 0x408), readQword(d + 0x410), readQword(d + 0x418),
    readQword(d + 0x420), readQword(d + 0x428), readQword(d + 0x450), readQword(d + 0x430))
end

local function slots(d)
  local used = 0
  for i = 0, 15 do
    local s = d + i * 0x40
    local key = readQword(s)
    if key ~= 0 then
      used = used + 1
      note(string.format('slot %d: key=0x%X obj=0x%X entity=0x%X state=%d playing=0x%X play=%d pushes ok=%d setpos ok=%d unplaced=%d',
        i, key, readQword(s + 8), readQword(s + 0x10), readInteger(s + 0x18), readInteger(s + 0x20) & 0xFFFFFFFF,
        readInteger(s + 0x24), readQword(s + 0x28), readQword(s + 0x30), readQword(s + 0x38)))
    end
  end
  return used
end

local function find_slot(d, key)
  for i = 0, 15 do
    if readQword(d + i * 0x40) == key then return d + i * 0x40 end
  end
end

local function ring(entity, tag)
  local pid = readInteger(entity + 0x130) & 0xFFFFFFFF
  local ev = executeCodeEx(0, 5000, 0x142222F10, pid)
  ev = ev and (ev & 0xFFFFFFFF) or -1
  note(string.format('ring %s: read=%d write=%d queued=%d playing=0x%X event of it=0x%08X (0xFDF5AFAA = alive)', tag,
    readInteger(entity + 0x124), readInteger(entity + 0x128), readInteger(entity + 0x12C), pid, ev))
  return readInteger(entity + 0x12C), ev
end

local saved, record = {}, nil

-- Whatever happened, the entry ends unticked and the shared bytes end as they were found.
local function cleanup()
  if record ~= nil and record.Active then record.Active = false end
  for _, s in ipairs(saved) do writeBytes(s[1], s[2]) end
  for _, s in ipairs(SHARED) do note(string.format('restored: %s = %s', s[1], hex(getAddress(s[1]), s[2]))) end
end

local function run()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  if getOpenedProcessID() == 0 then error('eldenring.exe is not running') end
  for _, s in ipairs(SHARED) do
    saved[#saved + 1] = {s[1], readBytes(s[1], s[2], true)}
    note(string.format('before: %s = %s', s[1], hex(getAddress(s[1]), s[2])))
  end
  note('before: ersc+92F75 = ' .. hex(getAddress('ersc.dll+92F75'), 5) .. '  ersc+9354F = ' .. hex(getAddress('ersc.dll+9354F'), 5) ..
       '  ersc+A5921 = ' .. hex(getAddress('ersc.dll+A5921'), 6) .. '  ersc+A5F2A = ' .. hex(getAddress('ersc.dll+A5F2A'), 5))

  loadTable(table_path)
  record = getAddressList().getMemoryRecord(0)
  record.Active = true
  note(string.format('"%s" active=%s; bank %s', record.Description, tostring(record.Active),
    er_voicepos_bank and string.format('load=%s (revision loaded before: %s)', tostring(er_voicepos_bank.result),
      tostring(er_voicepos_bank.loaded_before)) or 'not loaded'))
  if not record.Active then
    -- A syntax check only: autoAssemble would apply the script outside the entry if it passed.
    local ok, okc, err = pcall(autoAssembleCheck, record.Script, true, false)
    note('assembler check says: ' .. tostring(ok) .. ' ' .. tostring(okc) .. ' ' .. tostring(err))
    return
  end
  local d = getAddressSafe('er_vp_data')
  note(string.format('data 0x%X code 0x%X; ersc+92F75 = %s; ersc+9354F = %s; next bytes %s', d, getAddressSafe('er_vp_code'),
    hex(getAddress('ersc.dll+92F75'), 5), hex(getAddress('ersc.dll+9354F'), 5), hex(getAddress('ersc.dll+93554'), 4)))
  sleep(1500)
  note('after 1.5 s: ' .. counters(d))
  slots(d)

  -- Test hook through the same core.
  local f = io.open(pcm_path or '', 'rb')
  local bytes = f and f:read(CHUNK * CHUNKS)
  if f then f:close() end
  if bytes == nil or #bytes < CHUNK * CHUNKS then error('pcm file missing or short: ' .. tostring(pcm_path)) end
  local pcm = allocateMemory(CHUNK * CHUNKS + 4096)
  for off = 0, #bytes - 1, 4096 do
    writeBytes(pcm + off, {string.byte(bytes, off + 1, math.min(off + 4096, #bytes))})
  end
  writeQword(d + 0xB00, pcm); writeInteger(d + 0xB08, CHUNKS); writeInteger(d + 0xB10, CHUNK)
  writeQword(d + 0xB18, TEST_KEY); writeInteger(d + 0xB28, 20)
  -- 10 chunks first, then the ring is read while the voice should be running, then the rest.
  writeInteger(d + 0xB08, 10)
  executeCodeEx(0, 10000, getAddressSafe('er_vp_test'), d)
  local slot = find_slot(d, TEST_KEY)
  if slot == nil then error('test SteamID got no slot: ' .. counters(d)) end
  local entity = readQword(slot + 0x10)
  if entity == 0 then error('test slot has no entity: ' .. counters(d)) end
  ring(entity, 'after 10 chunks')
  writeQword(d + 0xB00, pcm + 10 * CHUNK); writeInteger(d + 0xB08, CHUNKS - 10)
  executeCodeEx(0, 15000, getAddressSafe('er_vp_test'), d)
  local q1 = ring(entity, 'right after 100 chunks')
  sleep(300)
  local q2, ev = ring(entity, '300 ms later')
  -- The offsets wrap at the ring size (0x20000 measured: 192000 bytes pushed leave both at 60928),
  -- so queued, not the offset delta, is the drain measure.
  note('after test: ' .. counters(d))
  local used = slots(d)
  note(string.format('objects in use: %d', used))
  -- Which character does the game return for SteamID 0? (Measured: the main player, whose own
  -- session identity reads 0 under Seamless; why the loopback uses the explicit fallback.)
  local own = 0
  local scratch = allocateMemory(4096)
  writeQword(scratch, own)
  local wcm = readQword(0x143D69FF8)
  local by_own = executeCodeEx(0, 5000, 0x140509170, wcm, scratch)
  note(string.format('GetPlayerInsBySteamId(own 0x%X) = 0x%X; main player = 0x%X', own, by_own or -1, readQword(wcm + 0x1E508)))
  deAlloc(scratch)
  note(string.format('VERDICT: test voice %s; ring %s', ev == 0xFDF5AFAA and 'alive' or 'NOT alive',
    (q1 <= 4 * CHUNK and q2 <= 4 * CHUNK) and 'drains at the push rate' or 'does NOT drain'))

  record.Active = false
  note(string.format('unticked: active=%s; ersc+92F75 = %s; ersc+9354F = %s', tostring(record.Active),
    hex(getAddress('ersc.dll+92F75'), 5), hex(getAddress('ersc.dll+9354F'), 5)))
  sleep(200)
  deAlloc(pcm)
end

pcall(hideAllCEWindows)
local timer = createTimer(nil, false)
timer.Interval = 2000
timer.OnTimer = function()
  timer.Enabled = false
  timer.destroy()
  pcall(hideAllCEWindows)
  local ok, err = pcall(run)
  if not ok then note('error: ' .. tostring(err)) end
  local cok, cerr = pcall(cleanup)
  if not cok then note('cleanup error: ' .. tostring(cerr)) end
  note('done')
  if close_when_done then closeCE() end
end
timer.Enabled = true
note('armed; table ' .. tostring(table_path) .. '; pcm ' .. tostring(pcm_path))
