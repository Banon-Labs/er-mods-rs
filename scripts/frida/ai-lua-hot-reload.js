// Hot-reload NPC AI Lua: run scripts/frida/ai-lua/_lab.lua and then each mods/*.lua inside the
// game's AI Lua state whenever any of them changes, so redefined goal functions (GeneralNPC_ActNN,
// Common_NPC_AI, Goal.Activate of any battle goal) take effect on the next AI plan without
// restarting the game.
//
// Normally loaded by the lab, which also serves the editor (never a plain frida.attach, AGENTS.md):
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-ai-lab.py
//
// Static chain, 1.17.1 (all read from eldenring-deobf-1.17.1.bin; names from the 1.16.2 dump):
//   CSWorldAiManager global   0x143d66548  (1.16.2 0x143d624e8; 11 callers of the getter agree)
//   ->+0x6938 CSAiLua          getter `mov rax,[rcx+0x6938]; ret` at 0x14037c140, unique in the image
//   ->+0xb8   DLLuaDetail<50>  CSAiLua ctor 0x1403713d0 reads it; registers it with the retail-stubbed
//                              CSLuaConsoleServer under the name "AI"
//   ->+0x28   lua_State*       DLLuaDetail load wrapper (1.17.0 0x142020890) passes [this+0x28]
//   luaL_loadbuffer 0x142027f30  called by luaB_loadstring 0x14202c5e0 (base lib table 0x1430eb1a0);
//                                1.16.2 dump names it luaL_loadbuffer
//   lua_pcall       0x142026970  called by luaB_pcall 0x14202c820 with (L, n-1, -1, 0)
//   lua_State: +0x10 top, +0x18 base, 16-byte TValue {int tt; pad; void *value}
//   luaB_print      0x14202bd60  base lib entry "print"; lua_gettop 0x1420265d0, lua_tostring
//                                0x142027200 (both called by it); luaopen_base 0x14202cb90 pushes
//                                _VERSION "Lua 5.0.2"
//
// Behaviour logging rides on the game's own print: a script line `print("HOTLOG", kind, ...)`
// reaches the luaB_print hook below, which reads the arguments and sends them as one event. Other
// print calls are forwarded as kind "print". Nothing is registered inside Lua, so unloading this
// agent leaves no callback behind for the game to call into.
//
// Lua 5.0 is not thread-safe, so nothing is compiled from Frida's own thread: the reload runs
// inside a lua_pcall hook, on whichever thread is already running the AI state at that moment.
// Interceptor does not re-enter a hook from inside its own callback, so the nested pcall is safe.
//
// Uses only Interceptor (no watchpoints), so a detach or reload leaves nothing behind.
'use strict';

const cfg = Object.assign({
    // _lab.lua (the framework) and mods/*.lua. manifest.txt in this directory lists them in run
    // order, one relative path per line; scripts/er-ai-lab.py rewrites it whenever the mods
    // directory changes. Without one, only _lab.lua runs.
    dir: 'Z:\\home\\banon\\projects\\er-mods-rs\\scripts\\frida\\ai-lua\\',
    pollMs: 500,
    // Re-run the file this often even when it has not changed. Battle scripts load lazily and
    // overwrite the globals the file wraps, so the file has to be idempotent and re-applied.
    reapplyMs: 2000,
}, globalThis.__ER_FRIDA_CONFIG || {});

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const WORLD_AI_MAN = va('0x143d66548');
const LUAL_LOADBUFFER = new NativeFunction(va('0x142027f30'), 'int', ['pointer', 'pointer', 'size_t', 'pointer']);
const LUA_PCALL_ADDR = va('0x142026970');
const LUA_PCALL = new NativeFunction(LUA_PCALL_ADDR, 'int', ['pointer', 'int', 'int', 'int']);

const LUAB_PRINT = va('0x14202bd60');
const LUA_GETTOP = new NativeFunction(va('0x1420265d0'), 'int', ['pointer']);
// lua_settop: luaB_print calls it as lua_pop(L, 1), i.e. (L, -2), after each argument.
const LUA_SETTOP = new NativeFunction(va('0x142026fd0'), 'void', ['pointer', 'int']);
const LUA_TOSTRING = new NativeFunction(va('0x142027200'), 'pointer', ['pointer', 'int']);

const LUA_TSTRING = 4;
const TSTRING_DATA = 0x18;

// A Lua string value (TString: len at +0x10, bytes at +0x18). Lua strings are bytes; the AI state
// holds some that are not UTF-8 (measured: a global name with byte 0xa4), so those come back with
// each non-ASCII byte escaped as \xNN instead of failing the whole read.
const TSTRING_LEN = 0x10;
function readLuaString(ts) {
    const len = ts.add(TSTRING_LEN).readU64().toNumber();
    const data = ts.add(TSTRING_DATA);
    try {
        return data.readUtf8String(len);
    } catch (e) {
        const bytes = new Uint8Array(data.readByteArray(len));
        let out = '';
        for (const b of bytes) out += b < 0x80 ? String.fromCharCode(b) : '\\x' + b.toString(16).padStart(2, '0');
        return out;
    }
}

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now() }, fields));
}

function aiLuaState() {
    try {
        const man = WORLD_AI_MAN.readPointer();
        if (man.isNull()) return null;
        const aiLua = man.add(0x6938).readPointer();
        if (aiLua.isNull()) return null;
        const detail = aiLua.add(0xb8).readPointer();
        if (detail.isNull()) return null;
        const L = detail.add(0x28).readPointer();
        return L.isNull() ? null : L;
    } catch (e) {
        return null;
    }
}

// The error message a failed load or pcall leaves on top of the stack, if it is a string.
function topError(L) {
    try {
        const top = L.add(0x10).readPointer();
        const tv = top.sub(16);
        if (tv.readS32() !== LUA_TSTRING) return '(non-string error, tt=' + tv.readS32() + ')';
        return readLuaString(tv.add(8).readPointer());
    } catch (e) {
        return '(unreadable error: ' + e.message + ')';
    }
}

function readFile(path) {
    try {
        return { text: File.readAllText(path) };
    } catch (e) {
        return { error: e.message };
    }
}

// [{name, text}] in run order, or {error} when the framework itself cannot be read.
function readSources() {
    let names = ['_lab.lua'];
    const manifest = readFile(cfg.dir + 'manifest.txt');
    if (manifest.text !== undefined) {
        names = manifest.text.split(/\r?\n/).map((s) => s.trim()).filter((s) => s !== '' && !s.startsWith('#'));
    }
    const out = [];
    for (const name of names) {
        const r = readFile(cfg.dir + name.replace(/\//g, '\\'));
        if (r.error !== undefined) {
            if (name === '_lab.lua') return { error: r.error };
            out.push({ name: name, error: r.error });
        } else {
            out.push({ name: name, text: r.text });
        }
    }
    return { files: out };
}

let lastText = null;
let lastPoll = 0;
let lastReadError = null;
let pcallHits = 0;
let aiHits = 0;
let reloads = 0;
let reapplies = 0;
let lastApply = 0;

function utf8Length(text) {
    let n = 0;
    for (const ch of text) {
        const c = ch.codePointAt(0);
        n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
    }
    return n;
}

// Compile and run `text` in L, then put L's stack back exactly where it was.
//
// The stack is saved as an index (lua_gettop) and restored with lua_settop, never as the raw
// L->top pointer. Running a chunk can grow the stack, and luaD_growstack reallocates it, so a
// saved absolute pointer then names freed memory; writing it back corrupted the state and crashed
// the game twice in luaV_execute (game+0x203646f, measured 2026-10-05).
function runChunk(L, text, chunkName) {
    const saved = LUA_GETTOP(L);
    const buf = Memory.allocUtf8String(text);
    const name = Memory.allocUtf8String('=' + chunkName);
    let status = LUAL_LOADBUFFER(L, buf, utf8Length(text), name);
    let phase = 'load';
    if (status === 0) {
        status = LUA_PCALL(L, 0, 0, 0);
        phase = 'run';
    }
    const error = status === 0 ? null : topError(L);
    LUA_SETTOP(L, saved);
    return { status: status, phase: phase, error: error };
}

// The game builds a new AI state when the world loads (measured: 0xaedbd80 at the title, 0x3e30fb00
// once in the world), and a script applied to the old one is gone with it. So a new state gets the
// file again even if the file did not change.
let lastState = null;

function maybeReload(L) {
    if (lastState === null || !L.equals(lastState)) {
        if (lastState !== null) emit('state-changed', { from: lastState.toString(), to: L.toString() });
        lastState = L;
        lastText = null;
        lastPoll = 0;
    }
    const now = Date.now();
    if (now - lastPoll < cfg.pollMs) return;
    lastPoll = now;
    const src = readSources();
    if (src.error !== undefined) {
        if (src.error !== lastReadError) emit('read-error', { path: cfg.dir + '_lab.lua', error: src.error });
        lastReadError = src.error;
        return;
    }
    lastReadError = null;
    const joined = JSON.stringify(src.files);
    const changed = joined !== lastText;
    if (!changed && now - lastApply < cfg.reapplyMs) return;
    lastText = joined;
    lastApply = now;
    // Framework first, then each mod, then the wrap pass that picks up the mods' overrides. Each
    // file is its own chunk, so an error names the file and line and the other files still run.
    const results = [];
    for (const f of src.files) {
        if (f.error !== undefined) {
            results.push({ file: f.name, status: -1, phase: 'read', error: f.error });
            continue;
        }
        const r = runChunk(L, f.text, f.name);
        results.push({ file: f.name, status: r.status, phase: r.phase, error: r.error });
    }
    const w = runChunk(L, 'if lab_wrap_all then lab_wrap_all() end', 'wrap');
    results.push({ file: 'wrap', status: w.status, phase: w.phase, error: w.error });
    const failed = results.filter((r) => r.status !== 0);
    if (changed || failed.length > 0) {
        reloads += 1;
        emit(failed.length === 0 ? 'reloaded' : 'reload-failed', {
            files: src.files.map((f) => f.name), n: reloads,
            error: failed.map((r) => r.file + ': ' + r.error).join('\n') || null, results: results,
        });
    } else {
        reapplies += 1;
    }
    drainLog(L);
}

// Lines hot.lua's hot_log() queued since the last drain. A chunk returns HOT_LOG_OUT and clears
// it, the string is read off the top of the stack, and the stack is put back.
const DRAIN = 'local s = HOT_LOG_OUT; HOT_LOG_OUT = nil; local d = HOT_LOG_DROPPED; '
    + 'HOT_LOG_DROPPED = nil; HOT_LOG_N = 0; if d then s = (s or "") .. "dropped\\tcount\\t" .. d .. "\\n" end; return s';
const drainBuf = Memory.allocUtf8String(DRAIN);
const drainName = Memory.allocUtf8String('=drain');

function drainLog(L) {
    const saved = LUA_GETTOP(L);
    try {
        if (LUAL_LOADBUFFER(L, drainBuf, utf8Length(DRAIN), drainName) !== 0) return;
        if (LUA_PCALL(L, 0, 1, 0) !== 0) return;
        const tv = L.add(0x10).readPointer().sub(16);
        if (tv.readS32() !== LUA_TSTRING) return;
        const text = readLuaString(tv.add(8).readPointer());
        for (const line of text.split('\n')) {
            if (line === '') continue;
            const parts = line.split('\t');
            const fields = { what: parts[0] };
            for (let i = 1; i + 1 < parts.length; i += 2) fields[parts[i]] = asNumber(parts[i + 1]);
            logLines += 1;
            emit('ai', fields);
        }
    } finally {
        LUA_SETTOP(L, saved);
    }
}

// REPL: code queued by the eval RPC runs at the next AI pcall, on the thread running the AI state,
// for the same reason the reload does. The code is tried as an expression first (`return <code>`)
// and then as a statement block; every return value comes back as text, and a table is listed one
// level deep.
const evalQueue = [];

// `code` as a Lua 5.0 string literal. Not a long bracket: 5.0 has only `[[ ]]`, without the `[=[`
// levels, so code containing or ending in `]]`/`]` cannot be wrapped in one. Every byte outside
// printable ASCII, and `"` and `\`, is written as a decimal `\ddd` escape.
function luaQuote(text) {
    const bytes = new Uint8Array(Memory.allocUtf8String(text).readByteArray(utf8Length(text)));
    let out = '"';
    for (const b of bytes) {
        out += b >= 0x20 && b < 0x7f && b !== 0x22 && b !== 0x5c ? String.fromCharCode(b) : '\\' + String(b).padStart(3, '0');
    }
    return out + '"';
}

function evalChunk(code) {
    const src = luaQuote(code);
    return 'local src = ' + src + '\n'
        + 'local f, e = loadstring("return " .. src, "=repl")\n'
        + 'if not f then f, e = loadstring(src, "=repl") end\n'
        + 'if not f then return "ERR\\t" .. tostring(e) end\n'
        + 'local r = {pcall(f)}\n'
        + 'if not r[1] then return "ERR\\t" .. tostring(r[2]) end\n'
        + 'local function show(v)\n'
        + '  if type(v) ~= "table" then return tostring(v) end\n'
        + '  local s, n = tostring(v) .. " {", 0\n'
        + '  for k, x in pairs(v) do\n'
        + '    n = n + 1\n'
        + '    if n > 200 then s = s .. "\\n  ..."; break end\n'
        + '    s = s .. "\\n  " .. tostring(k) .. " = " .. tostring(x)\n'
        + '  end\n'
        + '  return s .. "\\n}"\n'
        + 'end\n'
        + 'local out = ""\n'
        + 'for i = 2, table.getn(r) do\n'
        + '  if i > 2 then out = out .. "\\t" end\n'
        + '  out = out .. show(r[i])\n'
        + 'end\n'
        + 'return "OK\\t" .. out\n';
}

function runEval(L, code) {
    const saved = LUA_GETTOP(L);
    try {
        const text = evalChunk(code);
        const buf = Memory.allocUtf8String(text);
        const name = Memory.allocUtf8String('=repl-wrapper');
        let status = LUAL_LOADBUFFER(L, buf, utf8Length(text), name);
        if (status !== 0) return { ok: false, out: 'wrapper load failed: ' + topError(L) };
        status = LUA_PCALL(L, 0, 1, 0);
        if (status !== 0) return { ok: false, out: 'wrapper failed: ' + topError(L) };
        const tv = L.add(0x10).readPointer().sub(16);
        if (tv.readS32() !== LUA_TSTRING) return { ok: false, out: '(wrapper returned tt=' + tv.readS32() + ')' };
        const s = readLuaString(tv.add(8).readPointer());
        const tab = s.indexOf('\t');
        return { ok: s.slice(0, tab) === 'OK', out: s.slice(tab + 1) };
    } finally {
        LUA_SETTOP(L, saved);
    }
}

Interceptor.attach(LUA_PCALL_ADDR, {
    onEnter(args) {
        pcallHits += 1;
        const L = aiLuaState();
        if (L === null || !args[0].equals(L)) return;
        aiHits += 1;
        while (evalQueue.length > 0) {
            const job = evalQueue.shift();
            let r;
            try {
                r = runEval(L, job.code);
                drainLog(L);
            } catch (e) {
                r = { ok: false, out: 'hook fault: ' + e.message };
            }
            job.resolve(r);
        }
        try {
            maybeReload(L);
        } catch (e) {
            emit('hook-error', { error: e.message });
        }
    },
});

let printHits = 0;
let logLines = 0;

// Numbers come back as their decimal text; tables, functions and nil come back as null.
function printArgs(L) {
    const out = [];
    const n = LUA_GETTOP(L);
    for (let i = 1; i <= n; i++) {
        const s = LUA_TOSTRING(L, i);
        out.push(s.isNull() ? null : s.readUtf8String());
    }
    return out;
}

function asNumber(s) {
    if (s === null) return null;
    const n = Number(s);
    return Number.isFinite(n) && s.trim() !== '' ? n : s;
}

Interceptor.attach(LUAB_PRINT, {
    onEnter(args) {
        printHits += 1;
        try {
            const parts = printArgs(args[0]);
            if (parts[0] === 'HOTLOG') {
                logLines += 1;
                // HOTLOG, kind, then key/value pairs.
                const fields = { what: parts[1] };
                for (let i = 2; i + 1 < parts.length; i += 2) fields[parts[i]] = asNumber(parts[i + 1]);
                emit('ai', fields);
            } else {
                emit('print', { args: parts });
            }
        } catch (e) {
            emit('hook-error', { where: 'print', error: e.message });
        }
    },
});

// A hook that never fires has told us nothing, so say so instead of staying quiet. Driven by the
// game's frame tick (1.17.1 0x140773900, the one spawn-npc.js uses), which runs whether or not any
// AI does: every HEARTBEAT_MS of frames it reports the counters and fails evals no AI pcall took.
const FRAME_TICK = va('0x140773900');
const HEARTBEAT_MS = 5000;
const EVAL_EXPIRE_MS = 5000;
let heartbeatAt = 0;
const frameHook = Interceptor.attach(FRAME_TICK, {
    onEnter() {
        const now = Date.now();
        for (let i = evalQueue.length - 1; i >= 0; i--) {
            const job = evalQueue[i];
            if (now - job.at < EVAL_EXPIRE_MS) continue;
            evalQueue.splice(i, 1);
            job.resolve({ ok: false, out: 'timed out: no AI pcall in 5 s (is any AI character loaded?)' });
        }
        if (now - heartbeatAt < HEARTBEAT_MS) return;
        heartbeatAt = now;
        const L = aiLuaState();
        emit('heartbeat', {
            aiState: L === null ? null : L.toString(),
            pcallHits: pcallHits, aiHits: aiHits, reloads: reloads, reapplies: reapplies,
            printHits: printHits, logLines: logLines,
        });
    },
});

emit('armed', { dir: cfg.dir, aiState: (aiLuaState() || ptr(0)).toString() });

rpc.exports = {
    // Run Lua in the AI state at its next pcall. Times out when no AI runs (no AI character loaded).
    eval(code) {
        return new Promise((resolve) => {
            evalQueue.push({ code: String(code), resolve: resolve, at: Date.now() });
        });
    },
    dispose() {
        frameHook.detach();
    },
};
