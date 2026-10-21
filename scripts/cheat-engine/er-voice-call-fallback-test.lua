-- Read only: runs er-voice-chat.CT's er_voice_call through its createRemoteThread fallback on
-- the bank query AK Query::GetPositioningInfo (0x142223760). Run with er-ce-run-lua.py.
local log_path = os.getenv('ER_CE_RUN_LOG')
if log_path == nil or log_path == '' then return end
local close_when_done = os.getenv('ER_CE_RUN_CLOSE') == '1'
local function note(line)
  local f = io.open(log_path, 'a')
  if f then f:write(os.date('%Y-%m-%d %H:%M:%S  ') .. line .. '\n'); f:close() end
end
function er_voice_call(timeout, fn, a1, a2, a3)
  -- Once executeCodeEx has returned nil it is not tried again: a nil after its timeout would
  -- otherwise run the same call twice. The first call made is a read-only query.
  if not er_voice_noexec then
    local r = executeCodeEx(0, timeout, fn, a1 or 0, a2 or 0, a3 or 0)
    if r ~= nil then return r end
    er_voice_noexec = true
  end
  local mem = allocateMemory(0x100)
  if mem == nil or mem == 0 then return nil, 'executeCodeEx returned nil and allocateMemory failed' end
  local res, done, started = mem + 0x80, mem + 0x88, mem + 0x90
  writeQword(res, 0)
  writeQword(done, 0)
  writeQword(started, 0)
  local code = {}
  local function b(...) for _, v in ipairs({...}) do code[#code + 1] = v end end
  local function q(v) for i = 0, 7 do code[#code + 1] = (v >> (i * 8)) & 0xFF end end
  b(0x48, 0xB8); q(started)                 -- mov rax,started
  b(0xC7, 0x00, 0x01, 0x00, 0x00, 0x00)     -- mov dword [rax],1
  b(0x48, 0x83, 0xEC, 0x28)                 -- sub rsp,28
  b(0x48, 0xB9); q(a1 or 0)                 -- mov rcx,a1
  b(0x48, 0xBA); q(a2 or 0)                 -- mov rdx,a2
  b(0x49, 0xB8); q(a3 or 0)                 -- mov r8,a3
  b(0x48, 0xB8); q(fn)                      -- mov rax,fn
  b(0xFF, 0xD0)                             -- call rax
  b(0x48, 0xA3); q(res)                     -- mov [res],rax
  b(0x48, 0xB9); q(done)                    -- mov rcx,done
  b(0xC7, 0x01, 0x01, 0x00, 0x00, 0x00)     -- mov dword [rcx],1
  b(0x48, 0x83, 0xC4, 0x28)                 -- add rsp,28
  b(0x31, 0xC0, 0xC3)                       -- xor eax,eax / ret
  writeBytes(mem, code)
  fullAccess(mem, 0x100)
  if createRemoteThread(mem, 0) == nil then
    return nil, 'executeCodeEx returned nil and createRemoteThread failed'
  end
  local waited = 0
  while readInteger(done) ~= 1 and waited < timeout do sleep(20); waited = waited + 20 end
  if readInteger(done) ~= 1 then
    -- Left allocated: the call may still finish and write here.
    if readInteger(started) ~= 1 then
      return nil, string.format('a thread created in the game to call %X never started within %d ms, and executeCodeEx also returned nil: something on this PC stops Cheat Engine from running code in the game (antivirus, or Cheat Engine not run as administrator)', fn, timeout)
    end
    return nil, string.format('the thread calling %X started but the call did not return within %d ms (executeCodeEx also returned nil)', fn, timeout)
  end
  local r = readQword(res)
  deAlloc(mem)
  return r
end
local t = createTimer(nil, false)
t.Interval = 2000
t.OnTimer = function()
  t.destroy()
  local ok, err = pcall(function()
    if getOpenedProcessID() == 0 then openProcess('eldenring.exe') end
    local buf = allocateMemory(4096)
    local a = executeCodeEx(0, 5000, 0x142223760, 0x1B180742, buf + 0xC00)
    note('executeCodeEx query -> ' .. tostring(a and (a & 0xFFFFFFFF)))
    er_voice_noexec = true
    local b, why = er_voice_call(5000, 0x142223760, 0x1B180742, buf + 0xC00)
    note('fallback query -> ' .. tostring(b and (b & 0xFFFFFFFF)) .. ' ' .. tostring(why))
    deAlloc(buf)
  end)
  if not ok then note('error ' .. tostring(err)) end
  note('done')
  if close_when_done then closeCE() end
end
t.Enabled = true
