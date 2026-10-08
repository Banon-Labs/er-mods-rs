//! Lua brains for Mimic Tear companions: `ai = { brain = "<name>" }`.
//!
//! NPC AI runs in a stock Lua 5.0.2 state (bd `ai-lua-state-and-loader-1171-static-2026-10-05`;
//! the state chain was confirmed live on 2026-10-06 by `scripts/frida/npc-summons-gaps-probe.js`).
//! Lua is not thread-safe, so nothing here runs on a thread of its own: `lua_pcall` is detoured,
//! and when the game itself calls it on the AI state, the brains are loaded first, on that thread,
//! inside that call:
//!
//! 1. `lua_gettop` is saved;
//! 2. each chunk is compiled with `luaL_loadbuffer` and run through the trampoline with
//!    `(L, 0, 0, 0)`, so a nested `pcall` inside the chunk is the game's own;
//! 3. `lua_settop` puts the stack back to the saved index. Never the raw top pointer: a chunk can
//!    grow the stack, which reallocates it, and writing an old pointer back crashed the game twice
//!    in `luaV_execute` (2026-10-05);
//! 4. the game's own call runs.
//!
//! The chunks are the framework (`lua/brain-framework.lua`, compiled into the DLL), then per brain
//! a one-line prelude naming it and its think id followed by
//! `<game dir>/er-npc-summons/brains/<name>.lua`, then `brain_wrap_all()`, then a drain of the
//! framework's log into `er-npc-summons.log`. An apply happens when the state pointer changes (a
//! world load builds a new state) and every [`REAPPLY_FRAMES`] frames, because battle scripts load
//! lazily and overwrite the globals the framework wraps.
//!
//! A brain is keyed by think id. Every character running that think, world NPCs included, runs
//! it; the Mimic Tear's own think (100000010) is used by nothing else.

#![cfg(windows)]

use std::ffi::CString;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use er_game_base::mem::{
    game_module_base, game_rva_named, read_global_ptr, safe_read_cstr, safe_read_usize,
};
use windows::Win32::System::Threading::GetCurrentThreadId;

use crate::addr::{self, ai_lua, lua};
use crate::log::summons_log;

/// Frames between applies.
const REAPPLY_FRAMES: u32 = 120;
/// Log lines one drain may copy into the DLL's log.
const DRAIN_LINES_MAX: usize = 40;
/// The longest Lua string read back: a full drain is 200 lines of at most a few hundred bytes.
const LUA_STRING_MAX: usize = 64 * 1024;
/// The framework every apply loads first.
const FRAMEWORK: &str = include_str!("../lua/brain-framework.lua");

/// `(name, think)` per configured brain.
static BRAINS: Mutex<Vec<(String, i32)>> = Mutex::new(Vec::new());
/// How many brains are configured; 0 keeps the detour to one atomic load.
static BRAIN_COUNT: AtomicUsize = AtomicUsize::new(0);
/// The game task's frame count, and the frame of the last apply.
static FRAME: AtomicU32 = AtomicU32::new(0);
static LAST_APPLY_FRAME: AtomicU32 = AtomicU32::new(0);
/// The state the last apply went into.
static LAST_STATE: AtomicUsize = AtomicUsize::new(0);
/// The thread applying right now, so a `pcall` inside a brain goes straight to the game.
static APPLYING_TID: AtomicU32 = AtomicU32::new(0);
static APPLIES: AtomicUsize = AtomicUsize::new(0);

static ORIG_PCALL: AtomicUsize = AtomicUsize::new(0);

type PcallFn = unsafe extern "system" fn(usize, i32, i32, i32) -> i32;
type GettopFn = unsafe extern "system" fn(usize) -> i32;
type SettopFn = unsafe extern "system" fn(usize, i32);
type LoadbufferFn = unsafe extern "system" fn(usize, *const u8, usize, *const i8) -> i32;
type TostringFn = unsafe extern "system" fn(usize, i32) -> *const i8;

/// The brains to load, from a config read. An empty list stops loading; brains already in the
/// state stay until the next world load builds a new one.
pub(crate) fn configure(brains: Vec<(String, i32)>) {
    let count = brains.len();
    for (name, think) in &brains {
        summons_log(format_args!(
            "brains: \"{name}\" drives think {think}, and so every character running that think"
        ));
    }
    *BRAINS.lock().unwrap_or_else(|e| e.into_inner()) = brains;
    BRAIN_COUNT.store(count, Ordering::Release);
    // A changed list applies on the next AI call.
    LAST_STATE.store(0, Ordering::Release);
}

/// One game frame passed.
pub(crate) fn tick() {
    FRAME.fetch_add(1, Ordering::AcqRel);
}

fn this_thread() -> u32 {
    // SAFETY: no preconditions.
    unsafe { GetCurrentThreadId() }
}

/// The AI `lua_State*`: `CSWorldAiManager` -> `+0x6938` -> `+0xb8` -> `+0x28`.
fn ai_state() -> Option<usize> {
    let base = game_module_base().ok()?;
    let man = read_global_ptr(
        base,
        addr::CS_WORLD_AI_MAN_GLOBAL_RVA,
        "CS_WORLD_AI_MAN_GLOBAL_RVA",
    );
    if man == 0 {
        return None;
    }
    // SAFETY: fault-tolerant reads.
    let cs_ai_lua = unsafe { safe_read_usize(man + ai_lua::MAN_CS_AI_LUA) }.filter(|&p| p != 0)?;
    let detail =
        unsafe { safe_read_usize(cs_ai_lua + ai_lua::CS_AI_LUA_DETAIL) }.filter(|&p| p != 0)?;
    unsafe { safe_read_usize(detail + ai_lua::DETAIL_STATE) }.filter(|&p| p != 0)
}

/// The Lua calls an apply needs, resolved for the running build.
struct Api {
    pcall: PcallFn,
    gettop: GettopFn,
    settop: SettopFn,
    loadbuffer: LoadbufferFn,
    tostring: TostringFn,
}

fn api(pcall: PcallFn) -> Result<Api, String> {
    // SAFETY: each is the Lua 5.0.2 function with the declared signature; resolution refuses an
    // address with no verified mapping.
    unsafe {
        Ok(Api {
            pcall,
            gettop: core::mem::transmute::<usize, GettopFn>(game_rva_named(
                lua::GETTOP,
                "LUA_GETTOP",
            )?),
            settop: core::mem::transmute::<usize, SettopFn>(game_rva_named(
                lua::SETTOP,
                "LUA_SETTOP",
            )?),
            loadbuffer: core::mem::transmute::<usize, LoadbufferFn>(game_rva_named(
                lua::LOADBUFFER,
                "LUA_LOADBUFFER",
            )?),
            tostring: core::mem::transmute::<usize, TostringFn>(game_rva_named(
                lua::TOSTRING,
                "LUA_TOSTRING",
            )?),
        })
    }
}

/// The string at stack index `index`, if it is one.
fn string_at(api: &Api, state: usize, index: i32) -> Option<String> {
    // SAFETY: `lua_tostring` returns null for a non-string; the bytes are Lua's own.
    let text = unsafe { (api.tostring)(state, index) };
    if text.is_null() {
        return None;
    }
    // SAFETY: a fault-tolerant, length-capped read of the string Lua returned.
    let bytes = unsafe { safe_read_cstr(text as usize, LUA_STRING_MAX) }?;
    // UTF-8 Lossy: Lua strings are bytes; an error message may carry a non-UTF-8 name.
    Some(String::from_utf8_lossy(&bytes).into_owned())
}

/// Compile and run one chunk with `nresults` results; the caller restores the stack.
fn run(api: &Api, state: usize, name: &str, source: &str, nresults: i32) -> Result<(), String> {
    let chunk_name = CString::new(format!("={name}")).map_err(|e| e.to_string())?;
    // SAFETY: `source` outlives the call; the name is NUL-terminated.
    let loaded =
        unsafe { (api.loadbuffer)(state, source.as_ptr(), source.len(), chunk_name.as_ptr()) };
    if loaded != 0 {
        let why = string_at(api, state, -1).unwrap_or_default();
        return Err(format!("{name}: does not compile ({loaded}): {why}"));
    }
    // SAFETY: the trampoline, with the compiled chunk on top and no arguments.
    let ran = unsafe { (api.pcall)(state, 0, nresults, 0) };
    if ran != 0 {
        let why = string_at(api, state, -1).unwrap_or_default();
        return Err(format!("{name}: failed ({ran}): {why}"));
    }
    Ok(())
}

fn brain_path(name: &str) -> Option<std::path::PathBuf> {
    let ok = !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
    ok.then(|| {
        er_game_base::log::game_directory_path()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join("er-npc-summons")
            .join("brains")
            .join(format!("{name}.lua"))
    })
}

/// Load the framework and every brain into `state`, then drain the framework's log.
fn apply(api: &Api, state: usize) {
    let brains = BRAINS.lock().map(|b| b.clone()).unwrap_or_default();
    // SAFETY: `lua_gettop` / `lua_settop` on the state the game is about to call into.
    let top = unsafe { (api.gettop)(state) };
    let mut problems = Vec::new();
    if let Err(why) = run(api, state, "brain-framework", FRAMEWORK, 0) {
        problems.push(why);
    } else {
        // What each void-tech think's gear offers, before the brains that read it.
        if let Err(why) = run(
            api,
            state,
            "brain-void-offers",
            &crate::void_tech::offers_chunk(),
            0,
        ) {
            problems.push(why);
        }
        for (name, think) in &brains {
            let Some(path) = brain_path(name) else {
                problems.push(format!("{name}: not a usable brain name"));
                continue;
            };
            let source = match std::fs::read_to_string(&path) {
                Ok(source) => source,
                Err(why) => {
                    problems.push(format!("{name}: {} did not read: {why}", path.display()));
                    continue;
                }
            };
            let prelude = format!("BRAIN = {{ name = \"{name}\", think = {think} }}");
            if let Err(why) = run(api, state, "brain-prelude", &prelude, 0)
                .and_then(|()| run(api, state, name, &source, 0))
            {
                problems.push(why);
            }
        }
        if let Err(why) = run(api, state, "brain-wrap", "BRAIN = nil\nbrain_wrap_all()", 0) {
            problems.push(why);
        }
    }
    // SAFETY: restore by index; see the module docs.
    unsafe { (api.settop)(state, top) };
    // The thinks whose brains called brain_void(): their jumps get the void press.
    let thinks = run(api, state, "brain-void-list", "return brain_void_list()", 1)
        .ok()
        .and_then(|()| string_at(api, state, -1))
        .unwrap_or_default();
    // SAFETY: as above.
    unsafe { (api.settop)(state, top) };
    crate::void_tech::set_thinks(
        thinks
            .split(',')
            .filter_map(|t| t.trim().parse().ok())
            .collect(),
    );
    let drained = run(api, state, "brain-drain", "return brain_drain_log()", 1)
        .ok()
        .and_then(|()| string_at(api, state, -1))
        .unwrap_or_default();
    // SAFETY: as above.
    unsafe { (api.settop)(state, top) };
    let count = APPLIES.fetch_add(1, Ordering::AcqRel) + 1;
    let state_changed = LAST_STATE.swap(state, Ordering::AcqRel) != state;
    if state_changed || !problems.is_empty() || !drained.is_empty() {
        summons_log(format_args!(
            "brains: apply {count} into state 0x{state:x}, {} brain(s){}",
            brains.len(),
            if problems.is_empty() {
                ""
            } else {
                ", with problems:"
            }
        ));
    }
    for why in problems {
        summons_log(format_args!("brains:   {why}"));
    }
    for line in drained.lines().take(DRAIN_LINES_MAX) {
        summons_log(format_args!("brains: lua: {line}"));
    }
}

unsafe extern "system" fn pcall_detour(
    state: usize,
    nargs: i32,
    nresults: i32,
    errfunc: i32,
) -> i32 {
    let original = ORIG_PCALL.load(Ordering::Acquire);
    if original == 0 {
        return 2; // LUA_ERRRUN: there is nothing to call.
    }
    // SAFETY: the trampoline of the function this replaces.
    let original: PcallFn = unsafe { core::mem::transmute(original) };
    if BRAIN_COUNT.load(Ordering::Acquire) != 0 {
        let me = this_thread();
        let due = LAST_STATE.load(Ordering::Acquire) != state
            || FRAME
                .load(Ordering::Acquire)
                .wrapping_sub(LAST_APPLY_FRAME.load(Ordering::Acquire))
                >= REAPPLY_FRAMES;
        if due
            && APPLYING_TID.load(Ordering::Acquire) != me
            && ai_state() == Some(state)
            && APPLYING_TID
                .compare_exchange(0, me, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
        {
            LAST_APPLY_FRAME.store(FRAME.load(Ordering::Acquire), Ordering::Release);
            match api(original) {
                Ok(api) => apply(&api, state),
                Err(why) => {
                    BRAIN_COUNT.store(0, Ordering::Release);
                    summons_log(format_args!("brains: off -- {why}"));
                }
            }
            APPLYING_TID.store(0, Ordering::Release);
        }
    }
    unsafe { original(state, nargs, nresults, errfunc) }
}

/// Install the `lua_pcall` detour. Called once, from the install thread.
pub(crate) fn install() {
    match crate::mimic_hooks::hook(
        lua::PCALL,
        pcall_detour as *const () as usize,
        &ORIG_PCALL,
        "lua_pcall",
    ) {
        // SAFETY: applies the queued enable.
        Ok(_hook) => match unsafe { er_hook::MH_ApplyQueued() } {
            er_hook::MH_STATUS::MH_OK => summons_log(format_args!(
                "brains: lua_pcall detoured; brains load from er-npc-summons/brains/ when the AI \
                 state calls it"
            )),
            status => summons_log(format_args!("brains: MH_ApplyQueued failed: {status:?}")),
        },
        Err(why) => summons_log(format_args!("brains: off -- {why}")),
    }
}
