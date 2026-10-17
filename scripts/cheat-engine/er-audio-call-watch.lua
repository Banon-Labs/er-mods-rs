-- Log calls to Elden Ring's (1.17.1) Wwise control exports for a while, to find what silences
-- the game's audio when its window loses focus.
--
-- Every export below starts with a 5-byte `jmp rel32` (the protection stub layout), so a hook is
-- that jump redirected to a cave that records (function index, return address, rcx, xmm1) into a
-- 256-entry ring and then jumps to the original destination. Run by er-ce-run-lua.py; acts only
-- when ER_CE_RUN_LOG is set; restores every jump before it writes `done`.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local SECONDS = tonumber(os.getenv('ER_CE_RUN_SECONDS') or '25')

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local TARGETS = {
  {0x14223C3E0, 'MuteBackgroundMusic'},
  {0x142241010, 'SetGameObjectOutputBusVolume'},
  {0x142241690, 'SetOutputVolume'},
  {0x142241930, 'SetRTPCValue(id)'},
  {0x142241EA0, 'SetState(id,id)'},
  {0x1422421B0, 'SetVolumeThreshold'},
  {0x142242490, 'StopAll'},
  {0x142242850, 'Suspend'},
  {0x1422434D0, 'WakeupFromSuspend'},
  {0x14223F7C0, 'RenderAudio'},
  {0x14223C860, 'PostEvent(id)'},
  {0x14223A2E0, 'ExecuteActionOnEvent(id)'},
}

local function jmp_dest(address)
  local b = readBytes(address, 5, true)
  if b == nil or b[1] ~= 0xE9 then return nil end
  local rel = b[2] | (b[3] << 8) | (b[4] << 16) | (b[5] << 24)
  if rel >= 0x80000000 then rel = rel - 0x100000000 end
  return address + 5 + rel
end

local saved = {}

local function restore()
  for address, bytes in pairs(saved) do writeBytes(address, bytes) end
end

local function run()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  note('armed')
  local lines = {'alloc(eracw_ring,8192,142240000)', 'alloc(eracw_code,4096,142240000)',
                 'registersymbol(eracw_ring)', 'eracw_code:'}
  for i, t in ipairs(TARGETS) do
    local dest = jmp_dest(t[1])
    if dest == nil then note(string.format('%s at 0x%X does not start with jmp; skipped', t[2], t[1]))
    else
      t.dest = dest
      lines[#lines + 1] = string.format([[
eracw_h%d:
  push rax
  push rdx
  mov rax,eracw_ring
  mov edx,1
  lock xadd [rax],edx
  and edx,FF
  shl edx,5
  lea rdx,[rax+rdx+20]
  mov dword ptr [rdx],#%d
  movd [rdx+4],xmm1
  push rcx
  mov rcx,[rsp+18]
  mov [rdx+8],rcx
  mov rcx,[rsp+8]
  mov [rdx+18],rcx
  pop rcx
  mov [rdx+10],rcx
  pop rdx
  pop rax
  jmp %X]], i, i, dest)
    end
  end
  for i, t in ipairs(TARGETS) do
    if t.dest then
      saved[t[1]] = readBytes(t[1], 5, true)
      lines[#lines + 1] = string.format('%X:\n  jmp eracw_h%d', t[1], i)
    end
  end
  local labels = {}
  for i, t in ipairs(TARGETS) do if t.dest then labels[#labels + 1] = 'label(eracw_h' .. i .. ')' end end
  local script = table.concat(labels, '\n') .. '\n' .. table.concat(lines, '\n') .. '\n'
  local ok, err = autoAssemble(script)
  if not ok then saved = {}; note('assemble failed: ' .. tostring(err)); return end
  local ring = getAddressSafe('eracw_ring')
  note(string.format('hooked; ring 0x%X; watching %d s', ring, SECONDS))
  local start = os.time()
  local seen = 0
  local counts = {}
  rtpc_last = {}
  local second = os.time()
  local per_second = {}
  while os.time() - start < SECONDS do
    sleep(200)
    if os.time() ~= second then
      local parts = {}
      for name, n in pairs(per_second) do parts[#parts + 1] = name .. '=' .. n end
      table.sort(parts)
      note('per second: ' .. table.concat(parts, ' '))
      per_second = {}
      second = os.time()
    end
    local count = readInteger(ring) or 0
    if count - seen > 256 then seen = count - 256 end
    while seen < count do
      local e = ring + 0x20 + (seen % 256) * 32
      local idx = readInteger(e) or 0
      local name = TARGETS[idx] and TARGETS[idx][2] or ('#' .. idx)
      counts[name] = (counts[name] or 0) + 1
      per_second[name] = (per_second[name] or 0) + 1
      -- RTPCs arrive every frame; report one only when its value jumps.
      local report = counts[name] <= 20
      if name == 'SetRTPCValue(id)' then
        local id = readQword(e + 16) or 0
        local v = readFloat(e + 4) or 0
        local last = rtpc_last[id]
        report = last == nil and counts[name] <= 40
          or (last ~= nil and math.abs(v - last) > math.max(1.0, math.abs(last) * 0.3))
        rtpc_last[id] = v
        if report and last ~= nil then note(string.format('RTPC 0x%X jumped %.3f -> %.3f', id, last, v)) end
        report = false
      end
      if (name == 'PostEvent(id)' or name == 'ExecuteActionOnEvent(id)') and os.getenv('ER_CE_RUN_EVENTS') == '1' then
        -- One line per call: event id and game object, to line up against focus changes.
        note(string.format('%s event 0x%X object 0x%X from 0x%X', name, (readQword(e + 16) or 0) & 0xFFFFFFFF,
                           readQword(e + 24) or 0, readQword(e + 8) or 0))
        report = false
      end
      if report then
        note(string.format('%s  rcx=0x%X  xmm1=%.3f  from 0x%X', name, readQword(e + 16) or 0,
                           readFloat(e + 4) or -1, readQword(e + 8) or 0))
      end
      seen = seen + 1
    end
  end
  restore()
  local parts = {}
  for name, n in pairs(counts) do parts[#parts + 1] = name .. '=' .. n end
  note('restored; call counts: ' .. table.concat(parts, ' '))
end

local ok, err = pcall(run)
if not ok then restore(); note('error: ' .. tostring(err)) end
note('done')
if close_when_done then closeCE() end
