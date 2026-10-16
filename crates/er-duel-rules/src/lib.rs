//! A popup whenever anyone in the session breaks the competitive duel ruleset:
//! "Attention: Rule break: <player>. <rule>", with what broke it underneath.
//!
//! Every check and every decision about when a popup fires lives in `er-duel-rules-core`, where
//! `cargo test` proves it. This DLL only reads the game into that crate's `PlayerSnapshot`s,
//! answers its param questions from the live tables, and draws the notices.
//!
//! # What this DLL does to the game
//!
//! Nothing. No detours, no memory writes, no param edits, no input. A `FrameBegin` game task
//! reads the players, the param tables and the bullet manager; the overlay draws what that task
//! queued.
//!
//! # The log is the oracle
//!
//! `er-duel-rules.log` beside the game records every rule break with its detail, each player the
//! first time they are judged (level, weapons, armour, talismans, tears), every skill clip a
//! player starts, every blue flask counted, and a status line every ten seconds.

mod log;

#[cfg(windows)]
mod game;
#[cfg(windows)]
mod params;
#[cfg(windows)]
mod render;

#[cfg(windows)]
use std::collections::HashMap;
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
use er_duel_rules_core::player::{PlayerSnapshot, skill_clip};
#[cfg(windows)]
use er_duel_rules_core::referee::Referee;
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};
#[cfg(windows)]
use windows::Win32::{Foundation::HINSTANCE, System::SystemServices::DLL_PROCESS_ATTACH};

#[cfg(windows)]
use crate::log::{reset_log_file, rules_log};

const DLL_MAIN_SUCCESS: i32 = 1;

/// Frames between status lines. At 60fps, roughly every ten seconds.
#[cfg(windows)]
const STATUS_LOG_TICKS: usize = 600;

#[cfg(windows)]
static START: Once = Once::new();
#[cfg(windows)]
static TICKS: AtomicUsize = AtomicUsize::new(0);
#[cfg(windows)]
static ALERTS: AtomicUsize = AtomicUsize::new(0);

/// What the game task carries between frames.
#[cfg(windows)]
#[derive(Default)]
struct TaskState {
    reader: game::Reader,
    referee: Referee,
    /// The build each player was last logged with, to log it again only when it changes.
    logged_builds: HashMap<u64, String>,
    /// Ticks the param tables were not ready yet.
    facts_missing: usize,
}

#[cfg(windows)]
fn wait_for_task_instance() -> Option<&'static CSTaskImp> {
    // Bounded: an unbounded spin on this singleton once starved the wineserver (er_game_base::wait).
    er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
}

#[cfg(windows)]
fn build_line(p: &PlayerSnapshot) -> String {
    format!(
        "level={} end={} arm_style={:?} slots=L{}/R{} right={:?} left={:?} armor={:?} \
         talismans={:?} physick={:?}",
        p.level,
        p.endurance,
        p.arm_style,
        p.left_slot,
        p.right_slot,
        p.weapons_right,
        p.weapons_left,
        p.armor,
        p.talismans,
        p.physick
    )
}

/// One game frame.
#[cfg(windows)]
fn tick(state: &mut TaskState) {
    let ticks = TICKS.fetch_add(1, Ordering::Relaxed);
    let Some(facts) = params::LiveFacts::get() else {
        state.facts_missing += 1;
        return;
    };
    // SAFETY: the task runs on the game thread.
    let (players, stats) = unsafe { game::read_players(&mut state.reader) };
    for p in &players {
        let build = build_line(p);
        if state.logged_builds.get(&p.key) != Some(&build) {
            rules_log(format_args!(
                "player: {:?} local={} key={:#018x} {build}",
                p.name, p.is_local, p.key
            ));
            state.logged_builds.insert(p.key, build);
        }
        for &anim in &p.new_anims {
            if let Some((type_new, clip)) = skill_clip(anim) {
                rules_log(format_args!(
                    "skill: {:?} anim={anim} swordArtsTypeNew={type_new} clip={clip:06}",
                    p.name
                ));
            }
        }
    }
    state
        .logged_builds
        .retain(|key, _| players.iter().any(|p| p.key == *key));
    let flasks_before: Vec<(u64, u32)> = players
        .iter()
        .map(|p| (p.key, state.referee.flasks_used(p.key)))
        .collect();
    let alerts = state.referee.observe(render::now_ms(), &players, &facts);
    for (key, before) in flasks_before {
        let after = state.referee.flasks_used(key);
        if after > before {
            let name = players
                .iter()
                .find(|p| p.key == key)
                .map(|p| p.name.as_str());
            rules_log(format_args!(
                "flask: {name:?} blue flask {after} this round"
            ));
        }
    }
    for alert in alerts {
        ALERTS.fetch_add(1, Ordering::Relaxed);
        rules_log(format_args!(
            "{} -- {} (key={:#018x})",
            render::headline(&alert),
            alert.detail,
            alert.key
        ));
        render::push(alert);
    }
    if ticks.is_multiple_of(STATUS_LOG_TICKS) && ticks > 0 {
        rules_log(format_args!(
            "status: ticks={ticks} players={} remote_pending={} bullets_owned={} alerts={} \
             facts_missing_ticks={} overlay_installed={} notice_frames={}",
            stats.players,
            stats.remote_pending,
            stats.bullets_owned,
            ALERTS.load(Ordering::Relaxed),
            state.facts_missing,
            render::installed(),
            render::draws()
        ));
    }
}

#[cfg(windows)]
fn spawn_game_task() {
    let _ = std::thread::Builder::new()
        .name("er-duel-rules-task".to_owned())
        .spawn(move || {
            rules_log(format_args!("game task thread waiting for CSTaskImp"));
            let Some(task) = wait_for_task_instance() else {
                rules_log(format_args!(
                    "CSTaskImp never appeared; this shell stays inert rather than spinning"
                ));
                return;
            };
            rules_log(format_args!("game task registering FrameBegin tick"));
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
    rules_log(format_args!(
        "attach: module_base={module_base:#x}; duel ruleset referee, read-only (no detours, no \
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
        er_game_base::panic_report::report_panics_to("er-duel-rules", crate::log::rules_log);

        let module_base = module.0 as usize;
        START.call_once(|| {
            let _ = std::thread::Builder::new()
                .name("er-duel-rules-install".to_owned())
                .spawn(move || install(module_base));
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_duel_rules_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

// If this module wins the imgui context, every other overlay in the process has to be able to
// find it by name.
#[cfg(windows)]
er_build_watermark_core::export_overlay_host!();
