-- Log every call to Elden Ring's output-volume wrapper (1.17.1) for a while, to see whether the
-- game silences itself on focus loss by setting the output volume.
--
-- 0x142217410 is the game's wrapper around AK::SoundEngine::SetOutputVolume (export
-- 0x142241690): mov [rsp+8],rcx / sub rsp,38 / ... / call 0x142224c80 with the volume in xmm1.
-- A code cave records (return address, volume) into a 64-entry ring and runs the original nine
-- bytes. Run by er-ce-run-lua.py; acts only when ER_CE_RUN_LOG is set; restores the bytes and
-- logs every recorded call before it writes `done`.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local SECONDS = tonumber(os.getenv('ER_CE_RUN_SECONDS') or '25')

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local TARGET = 0x142217410
local ORIGINAL = {0x48, 0x89, 0x4C, 0x24, 0x08, 0x48, 0x83, 0xEC, 0x38}

local ENABLE = [[
alloc(eravw_ring,4096,142217410)
alloc(eravw_hook,256,142217410)
registersymbol(eravw_ring)
label(eravw_back)

eravw_hook:
  push rax
  push rdx
  mov rax,eravw_ring
  mov edx,[rax]
  and edx,3F
  shl edx,4
  add rdx,rax
  add rdx,10
  push rcx
  mov rcx,[rsp+18]
  mov [rdx],rcx
  pop rcx
  movd [rdx+8],xmm1
  mov [rdx+C],ecx
  inc dword ptr [rax]
  pop rdx
  pop rax
  mov [rsp+8],rcx
  sub rsp,38
  jmp eravw_back

142217410:
  jmp eravw_hook
  nop 4
eravw_back:
]]

local function run()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  note('armed')
  local got = readBytes(TARGET, #ORIGINAL, true)
  for i = 1, #ORIGINAL do
    if got == nil or got[i] ~= ORIGINAL[i] then
      note('bytes at 0x142217410 are not the 1.17.1 ones; nothing changed'); return
    end
  end
  local ok, err = autoAssemble(ENABLE)
  if not ok then note('assemble failed: ' .. tostring(err)); return end
  local ring = getAddressSafe('eravw_ring')
  note(string.format('hook installed, ring 0x%X; watching %d s', ring, SECONDS))
  local start = os.time()
  local seen = 0
  while os.time() - start < SECONDS do
    sleep(250)
    local count = readInteger(ring) or 0
    while seen < count do
      local e = ring + 0x10 + (seen % 64) * 16
      local ret = readQword(e)
      local vol = readFloat(e + 8)
      note(string.format('call %d: volume %.3f output 0x%X from 0x%X', seen + 1, vol or -1,
                         readInteger(e + 12) or 0, ret or 0))
      seen = seen + 1
    end
  end
  writeBytes(TARGET, ORIGINAL)
  note(string.format('restored original bytes; %d calls seen', seen))
end

local ok, err = pcall(run)
if not ok then
  writeBytes(TARGET, ORIGINAL)
  note('error: ' .. tostring(err))
end
note('done')
if close_when_done then closeCE() end
