//! `er_npc_possess.dll` -- stack layer 1 of the "become an NPC" mod.
//!
//! # What this layer is, and what it deliberately is not
//!
//! It is the whole player-facing surface of the mod with the possession itself left out: a
//! hot-reloadable config file in the game directory, a keyboard hotkey and a controller hotkey
//! bound from it, and a seam where the possession engine plugs in. Press the key in-game and a
//! structured line lands in `er-npc-possess.log` saying the press was seen, which device it came
//! from, and what the engine did with it -- which today is nothing, because there is no engine.
//!
//! That split is on purpose. Everything above the seam (config schema, hot reload, edge detection,
//! rejection handling, the not-live `[target]` rule) is decidable now and provable on the host with
//! `cargo test`. Everything below it needs reverse engineering that has not been done: which
//! `ChrIns` field the AI reads its input out of, how to drive a `TimeAct` from outside the
//! behaviour graph, where the camera's follow target lives. Inventing a mechanism for those now
//! would mean unpicking it later, so this crate ships the shape and refuses to guess at the
//! contents. See [`engine`] for the seam and who calls it.
//!
//! # What it touches in the game
//!
//! One recurring `FrameBegin` task, and two Win32 input reads inside it. It installs NO detour,
//! patches no param, writes no game memory and claims no prologue -- so it is co-loadable with
//! every other shell in this profile. See `input` for why polling rather than hooking is the
//! smaller claim rather than the lazier one.

// The config, settings, engine and edge-state modules are ungated on purpose: they are pure logic,
// so their tests run on the host where the windows-only game bindings do not exist.
mod config;
mod engine;
mod input;
mod log;
mod settings;
mod toml;

#[cfg(windows)]
use std::sync::{
    Mutex, Once,
    atomic::{AtomicUsize, Ordering},
};

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use er_hotkey_config::{chord_name, pad::pad_chord_name};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};
#[cfg(windows)]
use windows::Win32::{Foundation::HINSTANCE, System::SystemServices::DLL_PROCESS_ATTACH};

#[cfg(windows)]
use crate::{input::Edges, log::possess_log};

const DLL_MAIN_SUCCESS: i32 = 1;

/// Frames between status lines. At 60fps this is roughly every ten seconds.
#[cfg(windows)]
const STATUS_LOG_TICKS: usize = 600;

#[cfg(windows)]
static START: Once = Once::new();
#[cfg(windows)]
static TICKS: AtomicUsize = AtomicUsize::new(0);
/// Presses seen, whatever the engine did with them. The status line carries this so "nothing
/// happened" separates into "the key never fired" and "the key fired and there was nobody to tell".
#[cfg(windows)]
static PRESSES_SEEN: AtomicUsize = AtomicUsize::new(0);
/// The edge latches. A `Mutex` rather than a `static mut`: the FrameBegin task is the only writer,
/// so it is uncontended, and it costs one uncontended lock per frame to avoid the unsafe.
#[cfg(windows)]
static EDGES: Mutex<Option<Edges>> = Mutex::new(None);

#[cfg(windows)]
fn wait_for_task_instance() -> Option<&'static CSTaskImp> {
    // BOUNDED. An unbounded `loop { yield_now() }` here starved the wineserver on 1.17 when the
    // singleton did not turn up promptly; see er_game_base::wait for the measurement.
    er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
}

/// One `FrameBegin` tick: sample the devices, take a reload if the engine will allow one, and turn
/// a press into a possession request.
#[cfg(windows)]
fn tick() {
    // Sample the pad BEFORE the reload, so a rebind can seed its latch from the buttons that are
    // down at that instant instead of clearing it and manufacturing a press.
    let buttons = input::read_pad_buttons();

    // THE RELOAD GATE. A config reload must not land mid-animation -- the mapping tables are read
    // while an attack is playing, and swapping them under it would finish one character's swing
    // with another's. Only the engine knows whether the body is neutral, so it decides when the
    // file may be consumed. With no engine installed this is always open; see `engine`.
    if engine::accepts_reload()
        && let Some(update) = config::poll_reload()
    {
        config::log_update(&update);
        if update.bindings_moved() {
            // The latches are re-seated below, from the pad sample taken THIS frame. This is only
            // the reason the "now listening" line is about to appear.
            possess_log(format_args!(
                "config: a hotkey binding moved; re-seating the latches"
            ));
        }
    }

    let bindings = config::bindings();
    let ticks = TICKS.fetch_add(1, Ordering::Relaxed);

    let mut guard = match EDGES.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let edges = guard.get_or_insert_with(|| Edges::new(&bindings));
    if edges.rebind(&bindings, buttons) {
        possess_log(format_args!(
            "hotkey: now listening for keyboard={} gamepad={} radial={}",
            bindings
                .keyboard
                .map_or_else(|| "(none)".to_owned(), chord_name),
            pad_chord_name(bindings.gamepad),
            pad_chord_name(bindings.radial)
        ));
    }
    let keyboard_pressed = match bindings.keyboard {
        Some(chord) => input::keyboard_edge(chord, edges.keyboard_latch()),
        None => false,
    };
    let sample = edges.feed(buttons, keyboard_pressed);
    // The latches are advanced above whatever `enabled` says, so turning the mod back on mid-hold
    // does not read the button that was already down as a fresh press.
    drop(guard);

    if !bindings.enabled {
        return;
    }

    if sample.radial_changed {
        possess_log(format_args!(
            "radial: {} ({} held) -- RESERVED, there is no wheel to open until the mapping layer lands",
            if sample.radial_held { "DOWN" } else { "UP" },
            pad_chord_name(bindings.radial)
        ));
    }

    if let Some(source) = sample.possess_source() {
        PRESSES_SEEN.fetch_add(1, Ordering::Relaxed);
        // Taking the request and promoting a staged `[target]` happen under one lock, so the
        // request the engine is handed is the one the log line describes.
        let (request, adopted) = config::take_request();
        if let Some((from, to)) = adopted {
            possess_log(format_args!("config: [target] ADOPTED {from} -> {to}"));
        }
        let report = engine::on_hotkey_edge(source, request);
        let binding = if source == "keyboard" {
            bindings
                .keyboard
                .map_or_else(|| "(none)".to_owned(), chord_name)
        } else {
            pad_chord_name(bindings.gamepad)
        };
        possess_log(format_args!("{}", report.line(&binding)));
    }

    if ticks.is_multiple_of(STATUS_LOG_TICKS) {
        let (state, presses) = engine::snapshot();
        possess_log(format_args!(
            "status: enabled={} keyboard={} gamepad={} radial={} engine_installed={} state={} presses_seen={} presses_handled={} pad_buttons=0x{buttons:04x}",
            bindings.enabled,
            bindings
                .keyboard
                .map_or_else(|| "(none)".to_owned(), chord_name),
            pad_chord_name(bindings.gamepad),
            pad_chord_name(bindings.radial),
            engine::engine_installed(),
            state.name(),
            PRESSES_SEEN.load(Ordering::Relaxed),
            presses
        ));
    }
}

#[cfg(windows)]
fn spawn_game_task() {
    let _ = std::thread::Builder::new()
        .name("er-npc-possess-task".to_owned())
        .spawn(move || {
            possess_log(format_args!("game task thread waiting for CSTaskImp"));
            let Some(task) = wait_for_task_instance() else {
                possess_log(format_args!(
                    "CSTaskImp never appeared; this shell stays inert rather than spinning"
                ));
                return;
            };
            possess_log(format_args!("game task registering FrameBegin tick"));
            task.run_recurring(
                move |_data: &FD4TaskData| tick(),
                CSTaskGroupIndex::FrameBegin,
            );
        });
}

#[cfg(windows)]
fn install() {
    log::reset_log_file();
    let config = config::init_config();
    let bindings = config.bindings();
    possess_log(format_args!(
        "er-npc-possess attach: enabled={} hotkey={:?} ({}) gamepad_hotkey={:?} ({}) radial={:?} ({}) config={} -- edits to that file take effect while the game runs, no restart",
        bindings.enabled,
        config.keyboard_text,
        bindings
            .keyboard
            .map_or_else(|| "(none)".to_owned(), chord_name),
        config.gamepad_text,
        pad_chord_name(bindings.gamepad),
        config.radial_text,
        pad_chord_name(bindings.radial),
        config.config_path.display()
    ));
    possess_log(format_args!(
        "settings in force: [target] {} | {}",
        config.target().summary(),
        config.tables.summary()
    ));
    possess_log(format_args!(
        "derived moveset table: {} is NOT written by this layer -- the auto-classifier that \
         produces it lands later, and your edits to it will win over its verdicts when it does",
        config::DERIVED_CONFIG_FILE_NAME
    ));
    // SAY IT ONCE, PLAINLY. A mod whose hotkey works and whose feature does not is the exact shape
    // of a bug report nobody can act on; the log has to make the distinction before anyone presses
    // anything.
    possess_log(format_args!(
        "possession engine: NOT INSTALLED (stack layer 1). The hotkeys are live and every press is \
         logged, but nothing is possessed yet -- a later layer calls engine::install_engine"
    ));
    spawn_game_task();
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
/// Standard Windows `DllMain`; on attach it only starts an installer thread.
pub unsafe extern "system" fn DllMain(
    _module: HINSTANCE,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // A `rust_panic` in a cdylib loaded into the game is otherwise anonymous: the message goes
        // to a stderr nobody reads, and what survives is a 0xe06d7363 record naming the MODULE and
        // nothing else. Every cdylib links its own copy of er-game-base, so this is per-DLL.
        //
        // There is deliberately no `er_hook::set_hook_logger` call beside it: this DLL installs no
        // detour and resolves no game address, so it has no refusal to be silent about. If a later
        // layer adds one, that call goes here.
        er_game_base::panic_report::report_panics_to("er-npc-possess", crate::possess_log);
        START.call_once(|| {
            let _ = std::thread::Builder::new()
                .name("er-npc-possess-install".to_owned())
                .spawn(install);
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_npc_possess_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}
