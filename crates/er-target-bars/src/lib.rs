//! Extra bars under the game's own HP bar for whatever you are locked on to.
//!
//! The game already draws the target's HP: a floating tag over an ordinary enemy, a bar along the
//! bottom for a boss. This adds thin bars directly under that bar, at its left edge and width:
//! stance (the poise that breaks into a stagger), stamina and FP when the target has those pools
//! at all, and one bar per status that is building up (against the target's own resistance) or
//! active. Statuses the target is immune to never appear. The bars follow the game's bar as it
//! moves and vanish whenever the game hides it; there is no panel and no second HP bar.
//!
//! # What this DLL does to the game
//!
//! Nothing. No detours, no memory writes, no param edits, no input. A `FrameBegin` game task reads
//! the lock-on slot, the target's modules and the HUD's own tag data ([`game`] has every offset
//! and where it was proven, [`layout`] the HUD geometry), and the overlay draws what that task
//! published.
//!
//! # The log is the oracle
//!
//! `er-target-bars.log` beside the game records each new target with every raw value the bars
//! were built from and which game bar they hang under, each status the first time it builds up on
//! that target, every proc as it starts, and a status line every ten seconds counting locked
//! frames, frames with a game bar shown, stale handles and draws.

// Ungated on purpose: the bar maths, the row selection, the status filter and the stale-handle
// rejection are pure and are exercised by `cargo test` on the host, where the game-facing modules
// compile out.
mod layout;
mod log;
mod model;

#[cfg(windows)]
mod game;
#[cfg(windows)]
mod render;

#[cfg(windows)]
use std::sync::{
    Once,
    atomic::{AtomicUsize, Ordering},
};

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};
#[cfg(windows)]
use windows::Win32::{Foundation::HINSTANCE, System::SystemServices::DLL_PROCESS_ATTACH};

#[cfg(windows)]
use crate::log::{bars_log, reset_log_file};
#[cfg(windows)]
use crate::model::{ActiveHold, STATUS_COUNT, Status};

const DLL_MAIN_SUCCESS: i32 = 1;

/// Frames between status lines. At 60fps, roughly every ten seconds.
#[cfg(windows)]
const STATUS_LOG_TICKS: usize = 600;

#[cfg(windows)]
static START: Once = Once::new();
#[cfg(windows)]
static TICKS: AtomicUsize = AtomicUsize::new(0);
/// Frames the lock-on slot named a live character.
#[cfg(windows)]
static FOUND: AtomicUsize = AtomicUsize::new(0);
/// Frames it named a character no ChrSet holds any more.
#[cfg(windows)]
static STALE: AtomicUsize = AtomicUsize::new(0);
/// Frames the game showed an HP bar for the live target, so the bars had somewhere to go.
#[cfg(windows)]
static ANCHORED: AtomicUsize = AtomicUsize::new(0);

/// What the game task carries between frames.
#[cfg(windows)]
#[derive(Default)]
struct TaskState {
    /// The handle the bars are about, so a new target resets everything per-target.
    target: Option<u64>,
    hold: ActiveHold,
    /// Statuses already logged as building up on this target.
    logged_buildup: [bool; STATUS_COUNT],
    /// Statuses live last frame, to log a proc once as it starts.
    was_live: [bool; STATUS_COUNT],
    /// The last stale handle logged, so a lingering one is reported once.
    logged_stale: Option<u64>,
    /// The last game bar logged, so the log records each change rather than every frame.
    logged_anchor: Option<Option<crate::layout::Anchor>>,
}

#[cfg(windows)]
fn wait_for_task_instance() -> Option<&'static CSTaskImp> {
    // Bounded: an unbounded spin on this singleton once starved the wineserver (er_game_base::wait).
    er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
}

/// One game frame.
#[cfg(windows)]
fn tick(state: &mut TaskState) {
    let ticks = TICKS.fetch_add(1, Ordering::Relaxed);
    if ticks.is_multiple_of(STATUS_LOG_TICKS) && ticks > 0 {
        bars_log(format_args!(
            "status: ticks={ticks} found={} anchored={} stale={} overlay_installed={} frames={} \
             draws={}",
            FOUND.load(Ordering::Relaxed),
            ANCHORED.load(Ordering::Relaxed),
            STALE.load(Ordering::Relaxed),
            render::installed(),
            render::frames(),
            render::draws()
        ));
    }
    // SAFETY: the task runs on the game thread.
    match unsafe { game::lookup() } {
        game::Lookup::NoWorld | game::Lookup::Unlocked => {
            if state.target.take().is_some() {
                bars_log(format_args!("lock released"));
            }
            render::clear();
        }
        game::Lookup::Stale(handle) => {
            STALE.fetch_add(1, Ordering::Relaxed);
            if state.logged_stale != Some(handle) {
                state.logged_stale = Some(handle);
                bars_log(format_args!(
                    "stale: lock-on handle {handle:#018x} names no live character; bars hidden"
                ));
            }
            render::clear();
        }
        game::Lookup::Found {
            handle,
            address,
            reading,
            anchor,
        } => {
            FOUND.fetch_add(1, Ordering::Relaxed);
            if state.target != Some(handle) {
                state.target = Some(handle);
                state.hold.reset();
                state.logged_buildup = [false; STATUS_COUNT];
                state.was_live = [false; STATUS_COUNT];
                bars_log(format_args!(
                    "target: handle={handle:#018x} chr={address:#x} player={} npc_param={} \
                     hp={}/{} fp={}/{} stamina={}/{} stance={:?} gauges={:?}",
                    reading.is_player,
                    reading.npc_param_id,
                    reading.hp,
                    reading.hp_max,
                    reading.fp,
                    reading.fp_max,
                    reading.stamina,
                    reading.stamina_max,
                    reading.stance,
                    reading.statuses
                ));
            }
            let live = reading.live_flags();
            for status in Status::ALL {
                let index = status.index();
                if let Some(gauges) = reading.statuses
                    && !state.logged_buildup[index]
                    && gauges[index].buildup() > 0
                {
                    state.logged_buildup[index] = true;
                    bars_log(format_args!(
                        "buildup: {} gauge {} of resistance {}",
                        status.label(),
                        gauges[index].gauge,
                        gauges[index].resistance
                    ));
                }
                if live[index] && !state.was_live[index] {
                    bars_log(format_args!(
                        "proc: {} active, timer {:?}",
                        status.label(),
                        reading.active[index].flatten()
                    ));
                }
            }
            state.was_live = live;
            let now = render::now_ms() as f64 / 1000.0;
            let held = state.hold.observe(now, &live);
            // Logged on change only: which game bar the extras hang under, or that the game shows
            // none (and so neither do we).
            if state.logged_anchor != Some(anchor) {
                state.logged_anchor = Some(anchor);
                bars_log(format_args!("anchor: {anchor:?}"));
            }
            match anchor {
                Some(anchor) => {
                    ANCHORED.fetch_add(1, Ordering::Relaxed);
                    render::publish(anchor, model::panel_rows(&reading, &held));
                }
                None => render::clear(),
            }
        }
    }
}

#[cfg(windows)]
fn spawn_game_task() {
    let _ = std::thread::Builder::new()
        .name("er-target-bars-task".to_owned())
        .spawn(move || {
            bars_log(format_args!("game task thread waiting for CSTaskImp"));
            let Some(task) = wait_for_task_instance() else {
                bars_log(format_args!(
                    "CSTaskImp never appeared; this shell stays inert rather than spinning"
                ));
                return;
            };
            bars_log(format_args!("game task registering FrameBegin tick"));
            let mut state = TaskState::default();
            task.run_recurring(
                move |_data: &FD4TaskData| tick(&mut state),
                CSTaskGroupIndex::FrameBegin,
            );
        });
}

#[cfg(windows)]
fn install(module_base: usize) {
    reset_log_file();
    bars_log(format_args!(
        "attach: module_base={module_base:#x}; read-only bars under the lock-on target's HP bar (no detours, no \
         game writes), overlay ABI {:#06x}",
        er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
    ));
    spawn_game_task();
    render::install(module_base);
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. On attach it only starts an installer thread -- hudhook's
/// install takes locks and enumerates modules, neither of which belongs under the loader lock.
pub unsafe extern "system" fn DllMain(
    module: HINSTANCE,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // First, before anything that can panic: a panic crossing `extern "system"` is an abort
        // that leaves no record, and this hook turns it into a file:line in the log.
        er_game_base::panic_report::report_panics_to("er-target-bars", crate::log::bars_log);

        let module_base = module.0 as usize;
        START.call_once(|| {
            let _ = std::thread::Builder::new()
                .name("er-target-bars-install".to_owned())
                .spawn(move || install(module_base));
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_target_bars_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

// If this module wins the imgui context, every other overlay in the process has to be able to
// find it by name.
#[cfg(windows)]
er_build_watermark_core::export_overlay_host!();
