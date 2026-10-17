-- Positional voice probe, step 1: can the game's Wwise play "Play_VoiceChat" through a
-- DLMOW::MOAudioInputObject of our own? Cheat Engine only: Auto Assembler for the calls, Lua for
-- the driving and the log.
--
-- er-voice-probe-ce.py --voice-probe copies this into Cheat Engine's autorun folder. It does
-- nothing unless ER_VOICE_PROBE_LOG names the log file, so a Cheat Engine started by hand never
-- sees it act.
--
-- What it does, in the order Seamless's own playback setup does it (ersc+0xa58e0, 2.0.x), but
-- with the game's functions called directly. Every address is Elden Ring 1.17.1 and was read off
-- eldenring-deobf-1.17.1.bin; the first eight bytes of each are checked against that image before
-- anything is called, and a mismatch stops the probe.
--
--   0x141ebbd40  DL heap alloc(size, align, allocator)   allocator = [0x144850f50] or [0x144850f30],
--                the same pair the audio manager allocates its 0x150-byte entities from
--   0x142218ab0  MOAudioInputObject ctor(obj)            object is 0x118 bytes, zeroed first
--   0x1422178b0  MOGameObject set name(obj, char*)       add rcx,0x20 / jmp to the hashed-name
--                assign: the Wwise game object id is GetIDFromString(name), so the name must not
--                be Seamless's "YuiNexus3VoiceChatAudioInputObj_%d" or the ids would collide
--   0x142218820  format(fmt, 48000, 0x4101, 16)          0x14-byte format block
--   0x142211280  hashed event name ctor(str, char*)      +0 = GetIDFromString, +8 DL string
--   0x142218df0  Init(obj, 0x10000, 0x80, play, stop, &fmt, int chanmap[8])
--                play (4th, r9) is copied to obj+0x70 and stop (5th) to obj+0xa8, then both are
--                destroyed; Seamless passes the "Play_VoiceChat" object in r9
--   0x14222da90  audio input manager getter (0x1448513c0)
--   0x14222dd00  register(mgr, obj) -> 1 on success; writes the entity handle to obj+0x114
--   0x14222ca30  entity lookup(mgr+0x38, handle)
--   0x1422178e0  MOGameObject::SetPosition(obj, float[12])  pos, front, top as padded vec4s
--   0x142218ec0  Play(obj) -> 1, or 2 when the entity is missing or PostEvent returned 0; on
--                success the entity keeps the playing id at entity+0x130 (0x14222c040)
--   0x142218f40  push(obj, pcm, bytes), 0x142218f00 Stop(obj)
--
-- Thread: the calls run on a Cheat Engine remote thread (executeCodeEx), not a hooked game
-- thread. Read off the code: register takes the manager's own lock at mgr+8, push takes the
-- entity ring's lock, SetPosition takes the object's own lock (vtable +0x38/+0x48), and Play is
-- an unlocked handle lookup plus AK::SoundEngine::PostEvent, which Wwise queues for its audio
-- thread. Nothing on that path assumes the caller is the main or render thread, which is also
-- how Seamless drives it from its own code.
--
-- Bank mode (ER_VOICE_PROBE_BANK names a .bnk, written by scripts/er-voice-bank.py): before the
-- sequence above, the bank is copied into the game and loaded with
--   0x14223be30  AK::SoundEngine::LoadBankMemoryCopy(const void*, AkUInt32, AkBankID&) -> AKRESULT
-- (an export of eldenring.exe, so the address is the 1.17.1 one by construction; AK_Success = 1).
-- The synchronous overload blocks until Wwise's bank thread has parsed it. Copy rather than
-- View, so the bank does not depend on this Cheat Engine's allocation outliving it; the buffer
-- is still 16-byte aligned, as a memory bank must be. Without the variable the probe is the old one.
local log_path = os.getenv('ER_VOICE_PROBE_LOG')
if log_path == nil or log_path == '' then return end
local pcm_path = os.getenv('ER_VOICE_PROBE_PCM')
local bank_path = os.getenv('ER_VOICE_PROBE_BANK')
if bank_path == '' then bank_path = nil end
-- Chunks pushed before Play (ER_VOICE_PROBE_PREFILL), so the voice's first execute finds data.
local prefill = tonumber(os.getenv('ER_VOICE_PROBE_PREFILL') or '') or 0
-- Event names posted by Play/Stop (ER_VOICE_PROBE_EVENT=X posts Play_X / Stop_X); at most 31 bytes.
-- ER_VOICE_PROBE_EVENT_ID=0x...: a game event id written over entity+0x38 before Play, as a control
-- (cs_smain.bnk 0x3FE983A8 plays a looping Wwise Silence); its playing id is then stopped with
-- AK::SoundEngine::StopPlayingID after the queries.
local event_id_override = tonumber(os.getenv('ER_VOICE_PROBE_EVENT_ID') or '')
local inspect_only = os.getenv('ER_VOICE_PROBE_INSPECT') == '1'
local reload_bank = os.getenv('ER_VOICE_PROBE_RELOAD') == '1'
local event_suffix = os.getenv('ER_VOICE_PROBE_EVENT') or ''
if event_suffix == '' then event_suffix = 'VoiceChat' end
local play_event = ('Play_' .. event_suffix):sub(1, 31)
local stop_event = ('Stop_' .. event_suffix):sub(1, 31)
local close_when_done = os.getenv('ER_VOICE_PROBE_CLOSE') == '1'

local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end

local EXPECT = {
  {0x141EBBD40, '49 8B 00 4D 8B C8 4C 8B', 'heap alloc'},
  {0x142218AB0, '48 89 4C 24 08 57 48 83', 'MOAudioInputObject ctor'},
  {0x1422178B0, '48 83 C1 20 E9 37 93 FF', 'MOGameObject set name'},
  {0x142218820, '48 89 5C 24 08 48 89 74', 'format'},
  {0x142211280, '48 89 5C 24 10 48 89 74', 'hashed name ctor'},
  {0x142218DF0, '40 57 48 83 EC 40 48 C7', 'Init'},
  {0x14222DA90, '48 8D 0D 29 39 62 02 48', 'manager getter'},
  {0x14222DD00, '40 57 48 83 EC 40 48 C7', 'register'},
  {0x14222CA30, '85 D2 74 3A 81 FA FF FF', 'entity lookup'},
  {0x1422178E0, '48 89 5C 24 08 57 48 83', 'SetPosition'},
  {0x142218EC0, '40 53 48 83 EC 20 48 8B', 'Play'},
  {0x142218F40, '48 89 5C 24 08 48 89 74', 'push'},
  {0x142218F00, '40 53 48 83 EC 20 48 8B', 'Stop'},
  {0x14223BE30, 'E9 60 F5 B9 FF F3 44 0F', 'AK::SoundEngine::LoadBankMemoryCopy'},
  {0x142223610, 'E9 86 CF AD FE CC 48 83', 'AK Query::GetPlayingIDsFromGameObject'},
  {0x1422233B0, 'E9 6C 15 5E FF 4C 89 54', 'AK Query::GetListeners'},
  {0x142223640, 'E9 CA CC 31 FE 48 8B 4B', 'AK Query::GetPosition'},
  {0x142223250, 'E9 93 1C 19 FE CC 81 4A', 'AK Query::GetIsGameObjectActive'},
  {0x142222F10, 'E9 80 43 23 FE 90 2C E9', 'AK Query::GetEventIDFromPlayingID'},
  {0x142242760, 'EB 73 EB 76 48 89 5C 24', 'AK::SoundEngine::StopPlayingID'},
  {0x142223290, 'E9 B8 8E 2A FF 48 89 45', 'AK Query::GetListenerPosition'},
  {0x142223760, 'E9 85 B5 A1 FE 48 83 C4', 'AK Query::GetPositioningInfo'},
  {0x142222C80, 'E9 6E 32 B9 FE CC 50 15', 'AK Query::GetActiveGameObjects'},
  {0x142242CE0, 'E9 0A CC 9F FE 4C 8B 4C', 'AK::SoundEngine::UnloadBank'},
}

local function hex(address, count)
  local t = readBytes(address, count, true)
  if t == nil then return nil end
  local out = {}
  for i = 1, #t do out[i] = string.format('%02X', t[i]) end
  return table.concat(out, ' ')
end

local function main_player_physics()
  local wcm = readQword(0x143D69FF8)
  if wcm == nil or wcm == 0 then return nil end
  local player = readQword(wcm + 0x1E508)
  if player == nil or player == 0 then return nil end
  local modules = readQword(player + 0x190)
  if modules == nil or modules == 0 then return nil end
  local physics = readQword(modules + 0x68)
  if physics == nil or physics == 0 then return nil end
  return physics
end

-- Results block offsets, written by the assembly below.
local R = {obj = 0x0, allocator = 0x8, mgr = 0x10, register = 0x18, handle = 0x20, entity = 0x28,
           play = 0x30, playing_id = 0x38, setpos = 0x40, stage = 0x48, pushed = 0x50,
           pushed_ok = 0x58, setpos_ok = 0x60, stop = 0x68, physics = 0x70}
local CHUNK = 1920      -- 20 ms of 48 kHz mono s16
local CHUNKS = 100      -- 2 s

-- Cheat Engine reads alloc sizes as decimal bytes: 400 here once gave the run routine 0x190
-- bytes, the next allocation was assembled over its tail, and the game jumped to 0.
local SCRIPT = [[
alloc(ervp_data,4096,eldenring.exe)
alloc(ervp_run,4096,eldenring.exe)
alloc(ervp_pos,1024,eldenring.exe)
alloc(ervp_push,1024,eldenring.exe)
alloc(ervp_pcm,200000)
alloc(ervp_bank,256,eldenring.exe)
alloc(ervp_query,1024,eldenring.exe)
registersymbol(ervp_query)
label(ervp_stoppid)
registersymbol(ervp_stoppid)
label(ervp_call)
registersymbol(ervp_call)
label(ervp_post_raw)
label(ervp_raw_check)
label(raw_check_out)
registersymbol(ervp_post_raw)
registersymbol(ervp_raw_check)
alloc(ervp_bankbuf,65536)
registersymbol(ervp_bank)
registersymbol(ervp_bankbuf)
registersymbol(ervp_data)
registersymbol(ervp_run)
registersymbol(ervp_pos)
registersymbol(ervp_push)
registersymbol(ervp_pcm)
label(run_out)
label(pos_none)
label(pos_done)
label(push_loop)
label(push_skip_pos)
label(push_not_ok)
label(push_no_stop)
label(ervp_play)
label(play_out)
registersymbol(ervp_play)

ervp_run:
  push rbx
  push rsi
  push rdi
  push rbp
  sub rsp,48
  mov rbx,rcx
  mov qword ptr [rbx+48],1
  mov rax,144850F30
  mov r8,[rax]
  mov rax,144850F50
  mov rax,[rax]
  test rax,rax
  cmovne r8,rax
  mov [rbx+8],r8
  test r8,r8
  je run_out
  mov edx,8
  mov ecx,118
  mov rax,141EBBD40
  call rax
  mov [rbx],rax
  test rax,rax
  je run_out
  mov rdi,rax
  mov ecx,118
  xor eax,eax
  rep stosb
  mov qword ptr [rbx+48],2
  mov rcx,[rbx]
  mov rax,142218AB0
  call rax
  mov qword ptr [rbx+48],3
  mov rcx,[rbx]
  lea rdx,[rbx+100]
  mov rax,1422178B0
  call rax
  mov qword ptr [rbx+48],4
  xorps xmm0,xmm0
  movups [rbx+180],xmm0
  movups [rbx+190],xmm0
  lea rcx,[rbx+180]
  mov edx,BB80
  mov r8d,4101
  mov r9d,10
  mov rax,142218820
  call rax
  mov qword ptr [rbx+48],5
  lea rdi,[rbx+200]
  mov ecx,100
  xor eax,eax
  rep stosb
  lea rcx,[rbx+200]
  lea rdx,[rbx+140]
  mov rax,142211280
  call rax
  lea rcx,[rbx+280]
  lea rdx,[rbx+160]
  mov rax,142211280
  call rax
  mov qword ptr [rbx+48],6
  mov dword ptr [rbx+1A0],FFFFFFFF
  mov dword ptr [rbx+1A4],FFFFFFFF
  mov dword ptr [rbx+1A8],0
  mov dword ptr [rbx+1AC],FFFFFFFF
  mov dword ptr [rbx+1B0],FFFFFFFF
  mov dword ptr [rbx+1B4],FFFFFFFF
  mov dword ptr [rbx+1B8],FFFFFFFF
  mov dword ptr [rbx+1BC],FFFFFFFF
  mov rcx,[rbx]
  mov edx,10000
  mov r8d,80
  lea r9,[rbx+200]
  lea rax,[rbx+280]
  mov [rsp+20],rax
  lea rax,[rbx+180]
  mov [rsp+28],rax
  lea rax,[rbx+1A0]
  mov [rsp+30],rax
  mov rax,142218DF0
  call rax
  mov qword ptr [rbx+48],7
  mov rax,14222DA90
  call rax
  mov [rbx+10],rax
  mov rcx,rax
  mov rdx,[rbx]
  mov rax,14222DD00
  call rax
  mov eax,eax
  mov [rbx+18],rax
  cmp eax,1
  jne run_out
  mov qword ptr [rbx+48],8
  mov rax,[rbx]
  mov edx,[rax+114]
  mov [rbx+20],rdx
  mov rcx,[rbx+10]
  add rcx,38
  mov rax,14222CA30
  call rax
  mov [rbx+28],rax
  mov qword ptr [rbx+48],9
  mov rcx,rbx
  mov rax,ervp_pos
  call rax
  mov [rbx+40],rax
  cmp qword ptr [rbx+420],0
  jne run_out
  mov rcx,rbx
  mov rax,ervp_play
  call rax
run_out:
  mov rax,[rbx+48]
  add rsp,48
  pop rbp
  pop rdi
  pop rsi
  pop rbx
  ret

// rcx = ervp_data: Play(obj), then the playing id the entity kept.
ervp_play:
  push rbx
  sub rsp,20
  mov rbx,rcx
  mov qword ptr [rbx+48],A
  mov rcx,[rbx]
  mov rax,142218EC0
  call rax
  mov eax,eax
  mov [rbx+30],rax
  mov rax,[rbx+28]
  test rax,rax
  je play_out
  mov eax,[rax+130]
  mov [rbx+38],rax
  mov qword ptr [rbx+48],B
play_out:
  mov rax,[rbx+48]
  add rsp,20
  pop rbx
  ret

ervp_pos:
  push rbx
  sub rsp,20
  mov rbx,rcx
  mov rax,143D69FF8
  mov rax,[rax]
  test rax,rax
  je pos_none
  mov rax,[rax+1E508]
  test rax,rax
  je pos_none
  mov rax,[rax+190]
  test rax,rax
  je pos_none
  mov rax,[rax+68]
  test rax,rax
  je pos_none
  mov [rbx+70],rax
  movups xmm0,[rax+70]
  movups [rbx+300],xmm0
  mov dword ptr [rbx+30C],0
  mov dword ptr [rbx+310],0
  mov dword ptr [rbx+314],0
  mov dword ptr [rbx+318],3F800000
  mov dword ptr [rbx+31C],0
  mov dword ptr [rbx+320],0
  mov dword ptr [rbx+324],3F800000
  mov dword ptr [rbx+328],0
  mov dword ptr [rbx+32C],0
  mov rcx,[rbx]
  lea rdx,[rbx+300]
  mov rax,1422178E0
  call rax
  mov eax,eax
  jmp pos_done
pos_none:
  mov eax,FFFF
pos_done:
  add rsp,20
  pop rbx
  ret

ervp_push:
  push rbx
  push rsi
  push rdi
  sub rsp,20
  mov rbx,rcx
  mov esi,[rbx+428]
  imul eax,esi,#1920
  mov rdi,ervp_pcm
  add rdi,rax
push_loop:
  mov eax,esi
  xor edx,edx
  mov ecx,#10
  div ecx
  test edx,edx
  jne push_skip_pos
  mov rcx,rbx
  mov rax,ervp_pos
  call rax
  cmp eax,1
  jne push_skip_pos
  inc qword ptr [rbx+60]
push_skip_pos:
  mov rcx,[rbx]
  mov rdx,rdi
  mov r8d,#1920
  mov rax,142218F40
  call rax
  inc qword ptr [rbx+50]
  cmp eax,1
  jne push_not_ok
  inc qword ptr [rbx+58]
push_not_ok:
  add rdi,#1920
  mov ecx,#20
  mov rax,kernel32.Sleep
  call rax
  inc esi
  cmp esi,[rbx+430]
  jb push_loop
  cmp qword ptr [rbx+438],0
  je push_no_stop
  mov rcx,[rbx]
  mov rax,142218F00
  call rax
  mov eax,eax
  mov [rbx+68],rax
push_no_stop:
  mov rax,[rbx+58]
  add rsp,20
  pop rdi
  pop rsi
  pop rbx
  ret

// rcx = ervp_data: what Wwise itself reports for the object's game object (id at obj+0x20) and
// the playing id kept at [+38]. Results at +0x500..+0x5f0, read by query_report in Lua.
ervp_query:
  push rbx
  sub rsp,20
  mov rbx,rcx
  mov dword ptr [rbx+500],#16
  mov rax,[rbx]
  mov rcx,[rax+20]
  lea rdx,[rbx+500]
  lea r8,[rbx+508]
  mov rax,142223610
  call rax
  mov eax,eax
  mov [rbx+550],rax
  mov dword ptr [rbx+558],8
  mov rax,[rbx]
  mov rcx,[rax+20]
  lea rdx,[rbx+560]
  lea r8,[rbx+558]
  mov rax,1422233B0
  call rax
  mov eax,eax
  mov [rbx+5A0],rax
  mov rax,[rbx]
  mov rcx,[rax+20]
  lea rdx,[rbx+5B0]
  mov rax,142223640
  call rax
  mov eax,eax
  mov [rbx+5A8],rax
  mov rax,[rbx]
  mov rcx,[rax+20]
  mov rax,142223250
  call rax
  movzx eax,al
  mov [rbx+5E0],rax
  mov ecx,[rbx+38]
  mov rax,142222F10
  call rax
  mov eax,eax
  mov [rbx+5E8],rax
  mov rcx,[rbx+560]
  lea rdx,[rbx+5F8]
  mov rax,142223290
  call rax
  mov eax,eax
  mov [rbx+5F0],rax
  add rsp,20
  pop rbx
  ret

// rcx = ervp_data: AK::SoundEngine::PostEvent([+640] event, [+648] game object), playing id to
// [+650]; then GetEventIDFromPlayingID can tell whether it is still alive.
ervp_post_raw:
  push rbx
  sub rsp,40
  mov rbx,rcx
  xor eax,eax
  mov [rsp+20],rax
  mov [rsp+28],eax
  mov [rsp+30],rax
  mov [rsp+38],eax
  xor r9d,r9d
  xor r8d,r8d
  mov rdx,[rbx+648]
  mov ecx,[rbx+640]
  mov rax,14223C860
  call rax
  mov eax,eax
  mov [rbx+650],rax
  add rsp,40
  pop rbx
  ret

// rcx = ervp_data: GetEventIDFromPlayingID([+650]) to [+658], and StopPlayingID([+650]) if [+660].
ervp_raw_check:
  push rbx
  sub rsp,20
  mov rbx,rcx
  mov ecx,[rbx+650]
  mov rax,142222F10
  call rax
  mov eax,eax
  mov [rbx+658],rax
  cmp qword ptr [rbx+660],0
  je raw_check_out
  mov ecx,[rbx+650]
  xor edx,edx
  mov r8d,4
  mov rax,142242760
  call rax
raw_check_out:
  add rsp,20
  pop rbx
  ret

// rcx = ervp_data: generic call, [+800](rcx=[+808], rdx=[+810], r8=[+818], r9=[+820]) -> [+828].
ervp_call:
  push rbx
  sub rsp,40
  mov rbx,rcx
  xor eax,eax
  mov [rsp+20],rax
  mov [rsp+28],rax
  mov [rsp+30],rax
  mov [rsp+38],rax
  mov rax,[rbx+800]
  mov rcx,[rbx+808]
  mov rdx,[rbx+810]
  mov r8,[rbx+818]
  mov r9,[rbx+820]
  call rax
  mov [rbx+828],rax
  add rsp,40
  pop rbx
  ret

// rcx = ervp_data: StopPlayingID([+38], 0 ms, linear).
ervp_stoppid:
  sub rsp,28
  mov ecx,[rcx+38]
  xor edx,edx
  mov r8d,4
  mov rax,142242760
  call rax
  add rsp,28
  ret

// rcx = ervp_data: [+400] bank pointer, [+408] size; out [+410] bank id, [+418] AKRESULT.
ervp_bank:
  push rbx
  sub rsp,30
  mov rbx,rcx
  mov dword ptr [rbx+410],0
  mov rcx,[rbx+400]
  mov edx,[rbx+408]
  lea r8,[rbx+410]
  mov rax,14223BE30
  call rax
  mov eax,eax
  mov [rbx+418],rax
  add rsp,30
  pop rbx
  ret
]]

local function finish()
  note('done')
  if close_when_done then closeCE() end
end

local function results(data)
  local parts = {}
  for _, k in ipairs({'stage', 'allocator', 'obj', 'mgr', 'register', 'handle', 'entity', 'setpos',
                      'play', 'playing_id', 'physics'}) do
    parts[#parts + 1] = string.format('%s=0x%X', k, readQword(data + R[k]) or -1)
  end
  return table.concat(parts, '  ')
end

local function load_pcm(dest)
  local f = io.open(pcm_path or '', 'rb')
  if f == nil then error('cannot open the pcm file ' .. tostring(pcm_path)) end
  local bytes = f:read(CHUNK * CHUNKS)
  f:close()
  if bytes == nil or #bytes < CHUNK * CHUNKS then
    error(string.format('pcm file holds %d bytes, need %d', bytes and #bytes or 0, CHUNK * CHUNKS))
  end
  for offset = 0, #bytes - 1, 4096 do
    local last = math.min(offset + 4096, #bytes)
    writeBytes(dest + offset, {string.byte(bytes, offset + 1, last)})
  end
  local check = readBytes(dest + CHUNK * CHUNKS - 4, 4, true)
  local want = {string.byte(bytes, CHUNK * CHUNKS - 3, CHUNK * CHUNKS)}
  for i = 1, 4 do
    if check[i] ~= want[i] then error('pcm copy did not land in the game') end
  end
  return #bytes
end

-- Copies the bank file into ervp_bankbuf and calls LoadBankMemoryCopy through ervp_bank.
local function load_bank(data)
  local f = io.open(bank_path, 'rb')
  if f == nil then error('cannot open the bank file ' .. tostring(bank_path)) end
  local bytes = f:read('*a')
  f:close()
  if bytes == nil or #bytes < 16 or #bytes > 65536 or bytes:sub(1, 4) ~= 'BKHD' then
    error(string.format('%s is not a bank this probe can load (%d bytes)', bank_path, bytes and #bytes or 0))
  end
  local buf = getAddressSafe('ervp_bankbuf')
  if buf == nil or buf % 16 ~= 0 then error(string.format('bank buffer %s is not 16-byte aligned', tostring(buf))) end
  for offset = 0, #bytes - 1, 4096 do
    local last = math.min(offset + 4096, #bytes)
    writeBytes(buf + offset, {string.byte(bytes, offset + 1, last)})
  end
  local check = readBytes(buf, #bytes, true)
  for i = 1, #bytes do
    if check[i] ~= string.byte(bytes, i) then error('bank copy did not land in the game at byte ' .. (i - 1)) end
  end
  writeQword(data + 0x400, buf)
  writeQword(data + 0x408, #bytes)
  local header_id = string.unpack('<I4', bytes, 13)
  note(string.format('bank: %d bytes from %s at 0x%X (header bank id 0x%08X); calling LoadBankMemoryCopy',
                     #bytes, bank_path, buf, header_id))
  local ret = executeCodeEx(0, 20000, getAddressSafe('ervp_bank'), data)
  local result = readQword(data + 0x418)
  local bank_id = readInteger(data + 0x410) & 0xFFFFFFFF
  note(string.format('bank: LoadBankMemoryCopy returned %s, AKRESULT=%d (1 = AK_Success), out bank id=0x%08X',
                     tostring(ret), result or -1, bank_id))
  return result, bank_id
end

-- Calls fn(a, b, c, d) in the game through ervp_call; returns rax.
local function call(data, fn, a, b, c, d)
  writeQword(data + 0x800, fn)
  writeQword(data + 0x808, a or 0); writeQword(data + 0x810, b or 0)
  writeQword(data + 0x818, c or 0); writeQword(data + 0x820, d or 0)
  executeCodeEx(0, 10000, getAddressSafe('ervp_call'), data)
  return readQword(data + 0x828)
end

-- ER_VOICE_PROBE_INSPECT=1: read-only Wwise queries, no object built. Whether our sound nodes are
-- loaded (Query::GetPositioningInfo answers only for a loaded node), and which events the game
-- itself has playing right now on which game objects (GetActiveGameObjects, then
-- GetPlayingIDsFromGameObject and GetEventIDFromPlayingID).
function inspect(data)
  for _, id in ipairs({0x63B4FE33, 0x69D7A733, 0x169DBE6F}) do
    local res = call(data, 0x142223760, id, data + 0x700) & 0xFFFFFFFF
    local raw = readBytes(data + 0x700, 0x48, true) or {}
    local hexs = {}
    for i = 1, #raw do hexs[i] = string.format('%02X', raw[i]) end
    note(string.format('inspect: GetPositioningInfo(0x%08X) = %d  [%s]', id, res, table.concat(hexs, ' ')))
  end
  for i = 0, 15 do writeBytes(data + 0x880 + i, 0) end
  local res = call(data, 0x142222C80, data + 0x880) & 0xFFFFFFFF
  local items, n = readQword(data + 0x880), readInteger(data + 0x888)
  note(string.format('inspect: GetActiveGameObjects = %d, %d objects', res, n or -1))
  for i = 0, math.min(n or 0, 24) - 1 do
    local go = readQword(items + 8 * i)
    writeInteger(data + 0x8A0, 16)
    call(data, 0x142223610, go, data + 0x8A0, data + 0x8A8)
    local count = readInteger(data + 0x8A0) or 0
    local parts = {}
    for k = 0, math.min(count, 16) - 1 do
      local pid = readInteger(data + 0x8A8 + 4 * k) & 0xFFFFFFFF
      local ev = call(data, 0x142222F10, pid) & 0xFFFFFFFF
      parts[#parts + 1] = string.format('0x%X:0x%08X', pid, ev)
    end
    note(string.format('inspect: game object 0x%X playing %d  %s', go, count, table.concat(parts, ' ')))
  end
end

local function probe()
  if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
  if getOpenedProcessID() == 0 then error('eldenring.exe is not running') end
  local base = getAddressSafe('eldenring.exe')
  if base ~= 0x140000000 then error(string.format('eldenring.exe base is %s', tostring(base))) end
  for _, e in ipairs(EXPECT) do
    local got = hex(e[1], 8)
    if got ~= e[2] then
      error(string.format('%s at 0x%X reads [%s], expected [%s]; not 1.17.1, nothing called',
                          e[3], e[1], tostring(got), e[2]))
    end
  end
  note(string.format('all %d call targets match eldenring-deobf-1.17.1.bin', #EXPECT))
  local physics = main_player_physics()
  if physics == nil then return 'retry', 'no main player in the world yet' end
  note(string.format('main player physics 0x%X pos %.3f %.3f %.3f', physics,
                     readFloat(physics + 0x70), readFloat(physics + 0x74), readFloat(physics + 0x78)))

  local ok, info = autoAssemble(SCRIPT)
  if not ok then error('auto assembler refused the script: ' .. tostring(info)) end
  local data = getAddressSafe('ervp_data')
  note(string.format('assembled: data 0x%X run 0x%X pos 0x%X push 0x%X pcm 0x%X', data,
                     getAddressSafe('ervp_run'), getAddressSafe('ervp_pos'),
                     getAddressSafe('ervp_push'), getAddressSafe('ervp_pcm')))
  -- A fresh name each run, so the Wwise game object id (GetIDFromString of the name) is never
  -- shared with an object an earlier run left registered.
  local obj_name = string.format('ErVoiceProbeAudioInputObj_%d', os.time())
  writeString(data + 0x100, obj_name); writeBytes(data + 0x100 + #obj_name, 0)
  writeString(data + 0x140, play_event); writeBytes(data + 0x140 + #play_event, 0)
  writeString(data + 0x160, stop_event); writeBytes(data + 0x160 + #stop_event, 0)
  if bank_path ~= nil and reload_bank then
    -- LoadBank of an id that is already loaded only adds a reference (AK_BankAlreadyLoaded, 69),
    -- so a rebuilt bank under the same id needs the old one unloaded first:
    -- AK::SoundEngine::UnloadBank(AkBankID, const void* = NULL) at 0x142242ce0.
    local f = io.open(bank_path, 'rb')
    local head = f and f:read(16)
    if f then f:close() end
    if head and #head == 16 then
      local id = string.unpack('<I4', head, 13)
      local res = call(data, 0x142242CE0, id, 0) & 0xFFFFFFFF
      note(string.format('bank: UnloadBank(0x%08X) = %d (1 = AK_Success)', id, res))
    end
  end
  if bank_path ~= nil then load_bank(data) end
  if inspect_only then
    inspect(data)
    return 'done'
  end
  writeQword(data + 0x420, prefill > 0 and 1 or 0)   -- run skips Play when prefilling

  local stage = executeCodeEx(0, 10000, getAddressSafe('ervp_run'), data)
  note(string.format('run returned stage %s', tostring(stage)))
  note('results  ' .. results(data))
  local obj = readQword(data + R.obj)
  if obj ~= nil and obj ~= 0 then
    note(string.format('object: +0x114 audio input id=0x%X  +0x70 play id=0x%08X  +0xa8 stop id=0x%08X  +0x20 game object id=0x%X',
                       readInteger(obj + 0x114) & 0xFFFFFFFF, readInteger(obj + 0x70) & 0xFFFFFFFF, readInteger(obj + 0xA8) & 0xFFFFFFFF,
                       readQword(obj + 0x20)))
  end
  local entity = readQword(data + R.entity)
  if entity ~= nil and entity ~= 0 then
    note(string.format('entity: +0x38 event id=0x%08X  +0x130 playing id=0x%X',
                       readInteger(entity + 0x38) & 0xFFFFFFFF, readInteger(entity + 0x130) & 0xFFFFFFFF))
  end
  if readQword(data + R.register) ~= 1 then
    note('register did not return 1; nothing pushed')
    return 'done'
  end
  local function push(first, last, stop)
    writeQword(data + 0x428, first); writeQword(data + 0x430, last); writeQword(data + 0x438, stop and 1 or 0)
    return executeCodeEx(0, 15000, getAddressSafe('ervp_push'), data)
  end
  -- entity+0xe0 is the ring the game's Audio Input execute callback (0x14222c180) reads:
  -- +0x124 read offset, +0x128 write offset, +0x12c bytes queued. A read offset that never moves
  -- means no Wwise voice ever pulled from this entity.
  local function ring(tag)
    if entity == nil or entity == 0 then return end
    note(string.format('ring %s: read=%d write=%d queued=%d playing=0x%X', tag,
                       readInteger(entity + 0x124), readInteger(entity + 0x128), readInteger(entity + 0x12C),
                       readInteger(entity + 0x130) & 0xFFFFFFFF))
  end
  local loaded = load_pcm(getAddressSafe('ervp_pcm'))
  note(string.format('pcm: %d bytes copied from %s', loaded, pcm_path))
  if event_id_override ~= nil and entity ~= nil and entity ~= 0 then
    writeInteger(entity + 0x38, event_id_override)
    note(string.format('control: entity+0x38 event id overridden to 0x%08X', event_id_override))
  end
  if prefill > 0 then
    push(0, prefill, false)
    ring(string.format('after prefill of %d chunks', prefill))
    executeCodeEx(0, 10000, getAddressSafe('ervp_play'), data)
    note('play after prefill  ' .. results(data))
  end
  local playing = readQword(data + R.playing_id)
  if playing == nil or playing == 0 then
    note('VERDICT: PostEvent gave playing id 0 -- Play_VoiceChat is not loaded (pushing anyway, to see the ring path)')
  else
    note(string.format('VERDICT: PostEvent gave playing id 0x%X -- Play_VoiceChat is loaded', playing))
  end

  local function query(tag)
    executeCodeEx(0, 10000, getAddressSafe('ervp_query'), data)
    local n = readInteger(data + 0x500) or 0
    local ids = {}
    for i = 0, math.min(n, 16) - 1 do ids[#ids + 1] = string.format('0x%X', readInteger(data + 0x508 + 4 * i) & 0xFFFFFFFF) end
    local ln = readInteger(data + 0x558) or 0
    local ls = {}
    for i = 0, math.min(ln, 8) - 1 do ls[#ls + 1] = string.format('0x%X', readQword(data + 0x560 + 8 * i)) end
    note(string.format('wwise %s: game object 0x%X active=%d; playing ids (%d, res %d): %s; listeners (%d, res %d): %s; position res %d; event of playing id 0x%X = 0x%08X',
                       tag, readQword(obj + 0x20), readQword(data + 0x5E0), n, readQword(data + 0x550), table.concat(ids, ' '),
                       ln, readQword(data + 0x5A0), table.concat(ls, ' '), readQword(data + 0x5A8),
                       readQword(data + R.playing_id), readQword(data + 0x5E8)))
    local function t9(at)
      local v = {}
      for i = 0, 8 do v[#v + 1] = string.format('%.2f', readFloat(at + 4 * i)) end
      return table.concat(v, ' ')
    end
    note(string.format('wwise %s: emitter transform [%s]; listener res %d transform [%s]', tag,
                       t9(data + 0x5B0), readQword(data + 0x5F0), t9(data + 0x5F8)))
  end
  query('right after Play')
  sleep(300)
  query('300 ms after Play')
  if event_id_override ~= nil then
    executeCodeEx(0, 10000, getAddressSafe('ervp_stoppid'), data)
    sleep(100)
    query('after StopPlayingID')
    -- The same event posted straight through AK::SoundEngine::PostEvent on the game's own
    -- listener object, then on ours: separates "our object" from "posting from this thread".
    for _, target in ipairs({{'listener', readQword(data + 0x560)}, {'our object', readQword(obj + 0x20)}}) do
      writeQword(data + 0x640, event_id_override); writeQword(data + 0x648, target[2]); writeQword(data + 0x660, 0)
      executeCodeEx(0, 10000, getAddressSafe('ervp_post_raw'), data)
      local pid = readQword(data + 0x650)
      executeCodeEx(0, 10000, getAddressSafe('ervp_raw_check'), data)
      local at0 = readQword(data + 0x658)
      sleep(300)
      writeQword(data + 0x660, 1)
      executeCodeEx(0, 10000, getAddressSafe('ervp_raw_check'), data)
      note(string.format('raw PostEvent 0x%08X on %s 0x%X: playing id 0x%X, event of it now 0x%08X, after 300 ms 0x%08X (then stopped)',
                         event_id_override, target[1], target[2], pid, at0, readQword(data + 0x658)))
    end
  end
  ring('before push')
  local pushed_ok = push(prefill, CHUNKS, true)
  ring('after push and Stop')
  note(string.format('push: returned %s; chunks pushed=%d ok=%d (of %d x %d bytes); SetPosition ok=%d; Stop=0x%X',
                     tostring(pushed_ok), readQword(data + R.pushed), readQword(data + R.pushed_ok),
                     CHUNKS, CHUNK, readQword(data + R.setpos_ok), readQword(data + R.stop)))
  note('results  ' .. results(data))
  return 'done'
end

-- This Cheat Engine's window becomes Wine's foreground window and the game then renders silence,
-- which voids any capture of the game's output. Hide it; nothing here needs the window.
pcall(hideAllCEWindows)

local tries = 0
local timer = createTimer(nil, false)
timer.Interval = 2000
timer.OnTimer = function()
  timer.Enabled = false
  pcall(hideAllCEWindows)
  tries = tries + 1
  local ok, state, why = pcall(probe)
  if ok and state == 'done' then
    timer.destroy(); finish(); return
  end
  note('try ' .. tries .. ': ' .. tostring(ok and why or state))
  if (ok and tries < 60) or (not ok and tries < 3 and tostring(state):find('not running')) then
    timer.Enabled = true
  else
    timer.destroy(); finish()
  end
end
timer.Enabled = true
note('voice probe armed; pcm ' .. tostring(pcm_path) .. '; bank ' .. tostring(bank_path))
