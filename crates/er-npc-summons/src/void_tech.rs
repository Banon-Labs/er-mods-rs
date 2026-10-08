//! Void tech for Lua brains, the game half: the press, and the measurements that time it.
//!
//! A brain opts its think in with `brain_void()` and jumps (`brain_void_act`, in the framework);
//! everything after the jump is here, because it has to be frame-exact and a Lua wait is not (its
//! presses landed 5 to 7 frames into a 14-frame window, measured 2026-10-07). For every character
//! running an opted-in think:
//!
//! * takeoff is the first `create-or-reuse` call with behavior 550 while the character is not
//!   airborne, and the landing is the last frame that call refreshes it;
//! * on each `UpdateFromManipulator` while airborne the game clock (`FD4Time`) is summed, and the
//!   first frame [`Timing::press_now`] says so, the press [`choose`] picks from the character's gear
//!   is held for [`HOLD_FRAMES`] frames by setting its bits in the request bits;
//! * `SpawnBullet` with the character as owner records the spawn, and a spawn of the same behavior
//!   on the next frame is the double;
//! * at the landing the jump is learned from, per action, and logged.
//!
//! What each think's gear offers in each grip goes back to Lua as `BRAIN_VOID_OFFERS` on every
//! brain apply, so a brain can switch grip before jumping when only the other grip doubles.

#![cfg(windows)]

use std::collections::HashMap;
use std::fmt::Write as _;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard};

use er_game_base::mem::{safe_read_i32, safe_read_usize};
use er_npc_summons_core::void_tech::{self as vt, Gear, Jump, Press, Table, Timing};

use crate::addr::void_tech as a;
use crate::log::summons_log;

/// Frames a press is held.
const HOLD_FRAMES: u8 = 2;
/// Frames between re-reading a character's think id and gear.
const REFRESH_FRAMES: u32 = 60;
/// A character not seen for this many frames is forgotten.
const FORGET_FRAMES: u32 = 600;
/// An airborne record with no behavior 550 refresh for this many frames has landed.
const LANDED_AFTER: u32 = 2;
/// The longest jump believed; anything longer is dropped unlearned.
const AIR_FRAMES_MAX: u32 = 120;
/// Seconds past the landing a jump's record waits for a late spawn.
const POST_LAND_S: f32 = 0.15;
/// Frames after a press with no jump attack animation before pressing again. The anim queue
/// shows a new animation 2 frames after its clip starts (measured in `void-trace.js`), so 4.
const REPRESS_AFTER_FRAMES: u32 = 4;
/// Presses per jump at most. Measured 2026-10-07: 11 of 36 Bestial Sling presses produced no
/// cast at the same press times as the ones that doubled, so a dropped press is common and a jump
/// with no attack is the outcome to avoid, even at the cost of a late one.
const MAX_PRESSES: u8 = 6;

static ORIG_UFM: AtomicUsize = AtomicUsize::new(0);
static ORIG_ATTACK: AtomicUsize = AtomicUsize::new(0);
static ORIG_SPAWN: AtomicUsize = AtomicUsize::new(0);
/// Any think opted in; false keeps all three detours to one atomic load.
static ENABLED: AtomicBool = AtomicBool::new(false);
static FRAME: AtomicU32 = AtomicU32::new(0);

type UfmFn = unsafe extern "system" fn(usize, usize) -> usize;
type AttackFn = unsafe extern "system" fn(
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
) -> usize;
type SpawnFn = unsafe extern "system" fn(usize, usize, usize, usize) -> usize;

#[derive(Default)]
struct Air {
    /// Game seconds since takeoff, summed per `UpdateFromManipulator`.
    elapsed: f32,
    /// The character's own frames since takeoff.
    frames: u32,
    press: Option<Press>,
    /// The latest press: (elapsed, frame). A re-press moves it, so press-to-spawn is measured
    /// from the press that took.
    pressed: Option<(f32, u32)>,
    hold: u8,
    /// The jump attack or jump cast animation is playing: the press took.
    started: bool,
    /// Presses made this jump.
    presses: u8,
    /// FP at the first press.
    fp: Option<i32>,
    /// The animation playing at the last press, for a jump whose attack never started.
    anim: Option<i32>,
    last_550: (f32, u32),
    /// The landing (elapsed, frame), once behavior 550 stopped being refreshed.
    landed: Option<(f32, u32)>,
    /// First spawn after the press: (elapsed, frame, behavior).
    spawn: Option<(f32, u32, i32)>,
    doubled: bool,
}

struct Chr {
    think: i32,
    handle: u64,
    seen: u32,
    refreshed: Option<u32>,
    gear: Option<Gear>,
    air: Option<Air>,
}

struct State {
    table: Table,
    thinks: Vec<i32>,
    chrs: HashMap<usize, Chr>,
    timing: HashMap<i64, Timing>,
    /// The landing-to-spawn frame offset a double was seen at; the target the bias aims for.
    double_offset: i32,
    attempts: u32,
    doubles: u32,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

fn state() -> MutexGuard<'static, Option<State>> {
    STATE.lock().unwrap_or_else(|e| e.into_inner())
}

/// One game frame passed.
pub(crate) fn tick() {
    FRAME.fetch_add(1, Ordering::AcqRel);
}

/// The thinks whose characters void tech, from `brain_void_list()` after an apply.
pub(crate) fn set_thinks(thinks: Vec<i32>) {
    let mut guard = state();
    let Some(s) = guard.as_mut() else { return };
    if s.thinks != thinks {
        summons_log(format_args!("void: brains opted in thinks {thinks:?}"));
        s.thinks = thinks;
    }
    ENABLED.store(!s.thinks.is_empty(), Ordering::Release);
}

/// `BRAIN_VOID_OFFERS = { [think] = { one = bool, two = bool }, ... }`, from the gear last read
/// for a character of each opted-in think.
pub(crate) fn offers_chunk() -> String {
    let mut out = String::from("BRAIN_VOID_OFFERS = {");
    if let Some(s) = state().as_ref() {
        let mut seen = Vec::new();
        for chr in s.chrs.values() {
            let Some(gear) = chr.gear else { continue };
            if seen.contains(&chr.think) || !s.thinks.contains(&chr.think) {
                continue;
            }
            seen.push(chr.think);
            let (one, two) = vt::offers(&s.table, gear.right_weapon, gear.spell);
            let _ = write!(out, " [{}] = {{ one = {one}, two = {two} }},", chr.think);
        }
    }
    out.push_str(" }");
    out
}

/// The think id a character runs, through the game's own `GetComManipulator`.
fn think_of(chr: usize) -> Option<i32> {
    // SAFETY: fault-tolerant reads; the vtable slot is called only when it lies in the image.
    unsafe {
        let vtable = safe_read_usize(chr)?;
        let slot = safe_read_usize(vtable + a::VT_GET_COM_MANIPULATOR)?;
        let base = er_game_base::mem::game_module_base().ok()?;
        if !er_game_base::mem::vtable_in_game_image(slot, base) {
            return None;
        }
        let get: unsafe extern "system" fn(usize) -> usize = core::mem::transmute(slot);
        let com = get(chr);
        if com == 0 {
            return None;
        }
        let ai = safe_read_usize(com + a::COM_AI_INS).filter(|&p| p != 0)?;
        safe_read_i32(ai + a::AI_INS_THINK)
    }
}

/// The right weapon, its grip and the selected spell of a `PlayerIns`-shaped character.
fn gear_of(chr: usize) -> Option<Gear> {
    // SAFETY: fault-tolerant reads; a wrong shape reads ids the table does not hold.
    unsafe {
        let asm = safe_read_usize(chr + a::PLAYER_CHR_ASM).filter(|&p| p != 0)?;
        let style = safe_read_i32(asm + a::ASM_ARM_STYLE)? as u32;
        let slot = safe_read_i32(asm + a::ASM_RIGHT_SLOT)?;
        if !(0..3).contains(&slot) {
            return None;
        }
        let right_weapon = safe_read_i32(asm + a::ASM_PARAM_IDS + 4 * (1 + 2 * slot as usize))?;
        let spell = (|| {
            let pgd = safe_read_usize(chr + a::PLAYER_GAME_DATA).filter(|&p| p != 0)?;
            let egd = pgd + a::PGD_EQUIP_GAME_DATA;
            let magic = safe_read_usize(egd + a::EGD_MAGIC).filter(|&p| p != 0)?;
            if safe_read_usize(magic + a::MAGIC_BACK)? != egd {
                return None;
            }
            let sel = safe_read_i32(magic + a::MAGIC_SELECTED)?;
            if !(0..a::MAGIC_SLOTS).contains(&sel) {
                return None;
            }
            safe_read_i32(magic + a::MAGIC_ENTRIES + 8 * sel as usize)
        })()
        .unwrap_or(-1);
        Some(Gear {
            right_weapon,
            two_handed: style == a::ARM_STYLE_RIGHT_BOTH,
            spell,
        })
    }
}

/// Whether the animation playing is a jump attack or a jump cast: anim id (category * 1000000 +
/// anim) with anim in 031000..035000 (one-handed, two-handed, powerstance) or 045000..046000.
fn anim_of(chr: usize) -> Option<i32> {
    // SAFETY: fault-tolerant reads of the live character's own modules.
    unsafe {
        let modules = safe_read_usize(chr + a::CHR_MODULES).filter(|&p| p != 0)?;
        let tam = safe_read_usize(modules + a::MODULE_TIME_ACT).filter(|&p| p != 0)?;
        let read = safe_read_i32(tam + a::TAE_READ)? as u32 % a::TAE_QUEUE_LEN;
        safe_read_i32(tam + a::TAE_QUEUE + a::TAE_ENTRY * read as usize)
    }
}

fn jump_attack_playing(anim: Option<i32>) -> bool {
    anim.is_some_and(|id| {
        let anim = id.rem_euclid(1_000_000);
        (31_000..35_000).contains(&anim) || (45_000..46_000).contains(&anim)
    })
}

fn fp_of(chr: usize) -> Option<i32> {
    // SAFETY: fault-tolerant reads of the live character's data module.
    unsafe {
        let modules = safe_read_usize(chr + a::CHR_MODULES).filter(|&p| p != 0)?;
        let data = safe_read_usize(modules + a::MODULE_DATA).filter(|&p| p != 0)?;
        safe_read_i32(data + a::DATA_FP)
    }
}

fn finish(s: &mut State, think: i32, air: Air) {
    let (land_s, land_frame) = air.last_550;
    let Some(press) = air.press else { return };
    let Some((press_s, _)) = air.pressed else {
        return;
    };
    let mut frame_error = air.spawn.map(|(_, f, _)| f as i32 - land_frame as i32);
    if air.doubled {
        if let Some(e) = frame_error {
            s.double_offset = e;
        }
        s.doubles += 1;
    }
    frame_error = frame_error.map(|e| e - s.double_offset);
    s.attempts += 1;
    let frame_s = if air.frames > 0 {
        air.elapsed / air.frames as f32
    } else {
        1.0 / 30.0
    };
    let jump = Jump {
        press_s,
        spawn_s: air.spawn.map(|(t, _, _)| t),
        land_s,
        frame_error,
        frame_s,
    };
    let timing = s
        .timing
        .entry(press.key)
        .or_insert_with(|| Timing::seed(press.spawn_s));
    // Only a jump whose first press took says anything about when to press.
    if air.presses == 1 {
        timing.learn(&jump);
    }
    summons_log(format_args!(
        "void: think {think} bits {:#x} press {press_s:.3}s (press {} of {}, attack {}, anim {:?}, fp {:?}) spawn {:?} \
         land {land_s:.3}s (frame {land_frame}) error {frame_error:?} {} -- air {:.3}s, press-to-spawn {:.3}s, \
         bias {:+.3}s; {}/{} doubled",
        press.bits,
        air.presses,
        MAX_PRESSES,
        if air.started {
            "started"
        } else {
            "never started"
        },
        air.anim,
        air.fp,
        air.spawn.map(|(t, f, b)| (format!("{t:.3}s"), f, b)),
        if air.doubled { "DOUBLED" } else { "single" },
        timing.air_s,
        timing.press_to_spawn_s,
        timing.bias_s,
        s.doubles,
        s.attempts,
    ));
}

unsafe extern "system" fn ufm_detour(module: usize, time: usize) -> usize {
    // SAFETY: the trampoline of the function this replaces.
    let original: UfmFn = unsafe { core::mem::transmute(ORIG_UFM.load(Ordering::Acquire)) };
    if ENABLED.load(Ordering::Acquire) {
        // SAFETY: the game passes its live module and time step.
        unsafe { on_update(module, time) };
    }
    unsafe { original(module, time) }
}

unsafe fn on_update(module: usize, time: usize) {
    // SAFETY: the module and its owner are live for this call.
    let chr = unsafe { *((module + a::MODULE_OWNER) as *const usize) };
    let dt = unsafe { *((time + a::FD4_TIME) as *const f32) };
    if chr == 0 || !(0.0..0.2).contains(&dt) {
        return;
    }
    let now = FRAME.load(Ordering::Acquire);
    let mut guard = state();
    let Some(s) = guard.as_mut() else { return };
    let entry = s.chrs.entry(chr).or_insert(Chr {
        think: -1,
        handle: 0,
        seen: now,
        refreshed: None,
        gear: None,
        air: None,
    });
    entry.seen = now;
    if entry
        .refreshed
        .is_none_or(|r| now.wrapping_sub(r) >= REFRESH_FRAMES)
    {
        entry.refreshed = Some(now);
        entry.think = think_of(chr).unwrap_or(-1);
        // SAFETY: a fault-tolerant read of the character's own handle.
        entry.handle = unsafe { safe_read_usize(chr + a::CHR_HANDLE) }.unwrap_or(0) as u64;
        entry.gear = if s.thinks.contains(&entry.think) {
            gear_of(chr)
        } else {
            None
        };
    }
    if now.is_multiple_of(FORGET_FRAMES) {
        s.chrs
            .retain(|_, c| now.wrapping_sub(c.seen) < FORGET_FRAMES);
    }
    let Some(chr_state) = s.chrs.get_mut(&chr) else {
        return;
    };
    if !s.thinks.contains(&chr_state.think) {
        chr_state.air = None;
        return;
    }
    let think = chr_state.think;
    let Some(air) = chr_state.air.as_mut() else {
        return;
    };
    air.elapsed += dt;
    air.frames += 1;
    if air.frames > AIR_FRAMES_MAX {
        chr_state.air = None;
        return;
    }
    if air.landed.is_none() && air.frames > air.last_550.1 + LANDED_AFTER {
        air.landed = Some(air.last_550);
    }
    // A late press spawns after the landing, so the record stays open a little past it: without
    // that a late press is never seen as late and nothing pulls the next one earlier (measured
    // 2026-10-07: the press sat at 0.15 s for 9 jumps in a row, every spawn after the landing).
    if let Some((land_s, _)) = air.landed {
        if air.spawn.is_some() || air.elapsed > land_s + POST_LAND_S {
            let air = chr_state.air.take().unwrap_or_default();
            finish(s, think, air);
        }
        return;
    }
    if air.press.is_none() && air.pressed.is_none() {
        // The gear is read once per jump, at its first frame, so a weapon swap mid-air changes
        // nothing.
        air.press = gear_of(chr).and_then(|g| vt::choose(&s.table, &g));
        if air.press.is_none() {
            air.pressed = Some((-1.0, 0));
        }
    }
    if let (Some(press), None) = (air.press, air.pressed) {
        let timing = s
            .timing
            .entry(press.key)
            .or_insert_with(|| Timing::seed(press.spawn_s));
        if timing.press_now(air.elapsed, dt) {
            air.pressed = Some((air.elapsed, air.frames));
            air.hold = HOLD_FRAMES;
            air.presses = 1;
            air.fp = fp_of(chr);
        }
    }
    // Every jump attacks: a press the game dropped is made again, released for a frame first so
    // it reads as a new press, until the jump attack is playing or the presses run out.
    if air.press.is_some()
        && let Some((_, at)) = air.pressed.filter(|&(t, _)| t >= 0.0)
        && !air.started
    {
        air.anim = anim_of(chr);
        if jump_attack_playing(air.anim) {
            air.started = true;
        } else if air.hold == 0
            && air.frames >= at + REPRESS_AFTER_FRAMES
            && air.presses < MAX_PRESSES
        {
            air.pressed = Some((air.elapsed, air.frames));
            air.hold = HOLD_FRAMES;
            air.presses += 1;
        }
    }
    if air.hold > 0 {
        air.hold -= 1;
        if let Some(press) = air.press {
            // SAFETY: the live module's request bits, written before the game reads them.
            unsafe {
                let bits = (module + a::ACTION_REQUESTS) as *mut u64;
                *bits |= press.bits;
            }
        }
    }
}

unsafe extern "system" fn attack_detour(
    module: usize,
    out: usize,
    behavior: usize,
    a4: usize,
    a5: usize,
    a6: usize,
    a7: usize,
    a8: usize,
    a9: usize,
    a10: usize,
) -> usize {
    // SAFETY: the trampoline of the function this replaces.
    let original: AttackFn = unsafe { core::mem::transmute(ORIG_ATTACK.load(Ordering::Acquire)) };
    if ENABLED.load(Ordering::Acquire) && behavior as u32 as i32 == a::JUMP_BEHAVIOR {
        // SAFETY: the live damage module's owner.
        let chr = unsafe { *((module + a::MODULE_OWNER) as *const usize) };
        let mut guard = state();
        if let Some(c) = guard.as_mut().and_then(|s| {
            let thinks = &s.thinks;
            s.chrs.get_mut(&chr).filter(|c| thinks.contains(&c.think))
        }) {
            match c.air.as_mut() {
                // Past the landing the record only waits for a late spawn.
                Some(air) if air.landed.is_some() => {}
                Some(air) => air.last_550 = (air.elapsed, air.frames),
                None => c.air = Some(Air::default()),
            }
        }
    }
    unsafe { original(module, out, behavior, a4, a5, a6, a7, a8, a9, a10) }
}

unsafe extern "system" fn spawn_detour(
    manager: usize,
    out: usize,
    data: usize,
    a4: usize,
) -> usize {
    // SAFETY: the trampoline of the function this replaces.
    let original: SpawnFn = unsafe { core::mem::transmute(ORIG_SPAWN.load(Ordering::Acquire)) };
    if ENABLED.load(Ordering::Acquire) && data != 0 {
        // SAFETY: the game's spawn data for this call.
        let (owner, behavior) = unsafe { (*(data as *const u64), *((data + 8) as *const i32)) };
        let mut guard = state();
        if let Some(s) = guard.as_mut() {
            for c in s.chrs.values_mut() {
                if c.handle != owner || c.handle == 0 {
                    continue;
                }
                if let Some(air) = c
                    .air
                    .as_mut()
                    .filter(|air| air.pressed.is_some_and(|(t, _)| t >= 0.0))
                {
                    match air.spawn {
                        None => air.spawn = Some((air.elapsed, air.frames, behavior)),
                        Some((_, f, b)) if b == behavior && air.frames == f + 1 => {
                            air.doubled = true
                        }
                        _ => {}
                    }
                }
            }
        }
    }
    unsafe { original(manager, out, data, a4) }
}

/// Install the three detours. Called once, from the install thread, after MinHook is up.
pub(crate) fn install() {
    let (table, bad) = Table::parse(vt::BUILTIN_TABLE);
    let (w, sp, c) = table.counts();
    *state() = Some(State {
        table,
        thinks: Vec::new(),
        chrs: HashMap::new(),
        timing: HashMap::new(),
        double_offset: 0,
        attempts: 0,
        doubles: 0,
    });
    // SAFETY: idempotent across the DLLs that share MinHook; the mimic install may have returned
    // before it ran.
    match unsafe { er_hook::MH_Initialize() } {
        er_hook::MH_STATUS::MH_OK | er_hook::MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            summons_log(format_args!(
                "void: off -- MH_Initialize failed: {status:?}"
            ));
            return;
        }
    }
    let hooks = [
        crate::mimic_hooks::hook(
            a::UPDATE_FROM_MANIPULATOR,
            ufm_detour as *const () as usize,
            &ORIG_UFM,
            "UpdateFromManipulator",
        ),
        crate::mimic_hooks::hook(
            a::ATTACK_CREATE_OR_REUSE,
            attack_detour as *const () as usize,
            &ORIG_ATTACK,
            "attack create-or-reuse",
        ),
        crate::mimic_hooks::hook(
            a::SPAWN_BULLET,
            spawn_detour as *const () as usize,
            &ORIG_SPAWN,
            "SpawnBullet",
        ),
    ];
    let failed: Vec<String> = hooks
        .iter()
        .filter_map(|h| h.as_ref().err().cloned())
        .collect();
    if !failed.is_empty() {
        summons_log(format_args!("void: off -- {}", failed.join("; ")));
        return;
    }
    // SAFETY: applies the queued enables.
    match unsafe { er_hook::MH_ApplyQueued() } {
        er_hook::MH_STATUS::MH_OK => summons_log(format_args!(
            "void: armed, table {w} weapon jumps, {sp} spells, {c} catalysts ({bad} bad lines); idle until a brain calls brain_void()"
        )),
        status => summons_log(format_args!("void: MH_ApplyQueued failed: {status:?}")),
    }
    // `MhHook` has no `Drop`: each detour stays installed once applied.
}
