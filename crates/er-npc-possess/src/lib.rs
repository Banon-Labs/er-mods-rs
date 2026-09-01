//! `er_npc_possess.dll` -- the "become an NPC" mod.
//!
//! # What it does
//!
//! Press the hotkey and you are the character you are looking at. `possess` is the engine; this
//! file is the shell around it -- a hot-reloadable config in the game directory, a keyboard hotkey
//! and a controller hotkey bound from it, a `FrameBegin` task that drives both, and a structured
//! line in `er-npc-possess.log` for every press saying what was requested and what happened to it.
//!
//! Layer 1 shipped everything except the possession and left [`engine`] as the seam; layer 2
//! filled that seam in and nothing above it had to change, which is the only real evidence that
//! the seam was cut in the right place.
//!
//! # What it touches in the game
//!
//! One recurring `FrameBegin` task and two Win32 input reads inside it, plus -- only while
//! something is possessed -- a handful of struct-field writes on two `ChrIns`. It installs **NO
//! detour**, resolves **no game function address**, patches no param and claims no prologue, so it
//! stays co-loadable with every other shell in this profile and cannot be broken by the
//! 1.16.2 -> 1.17 address migration. See `input` for why polling rather than hooking is the
//! smaller claim rather than the lazier one, and [`possess::game`] for why the engine needed no
//! addresses at all.
//!
//! The one thing it does that IS dangerous is write `ChrCtrl+0x3b0`, because `ChrCtrl::Unref`
//! DLPanics on a non-null value there. Every path that can end a possession -- the hotkey, the
//! creature dying, the creature despawning, and `DLL_PROCESS_DETACH` below -- goes through
//! `possess::teardown`, which clears it whatever else failed.

// The config, settings, engine and edge-state modules are ungated on purpose: they are pure logic,
// so their tests run on the host where the windows-only game bindings do not exist.
mod config;
mod engine;
mod input;
mod log;
mod possess;
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
use windows::Win32::{
    Foundation::HINSTANCE,
    System::SystemServices::{DLL_PROCESS_ATTACH, DLL_PROCESS_DETACH},
};

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

    // THE POSSESSION ITSELF, one frame of it. After the hotkey edge, so a press and its first
    // frame land in that order; unconditional, because the engine has to notice a possessed
    // character dying or despawning on a frame nobody pressed anything.
    engine::tick_engine();

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
    // STACK LAYER 2. The seam is filled: pressing the key now writes a forwarding thunk into the
    // target's `ChrCtrl+0x3b0`, points `WorldChrManDbg+0xb8` at it, and co-locates the player's
    // own (invisible, silent, invincible, non-attacking) body with it every frame.
    let installed = engine::install_engine(Box::new(possess::NpcPossessionEngine::new()));
    possess_log(format_args!(
        "possession engine: {} -- build={} | NOT in this layer: attacks (the W_Event/W_Attack \
         animation-prefix question is unresolved and a wrong prefix silently no-ops) and \
         untargetable (IsLockOnDisabled reads the SpEffect-accumulated modifier block, so it needs \
         a SpEffect row rather than a field write)",
        if installed {
            "INSTALLED"
        } else {
            "REFUSED -- one was already installed, and two engines writing one ChrIns is not \
             something to resolve by install order"
        },
        er_game_base::game_build::describe_build()
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
    // THE ONE TEARDOWN THAT IS NOT OPTIONAL. A possession leaves a pointer to OUR memory in the
    // possessed character's `ChrCtrl+0x3b0`, and `ChrCtrl::Unref` DLPanics on a non-null slot -- so
    // a DLL that unloads without clearing it arms a crash for whenever that character is next torn
    // down, in a process where our code is no longer present to explain it.
    if reason == DLL_PROCESS_DETACH {
        engine::shutdown_engine();
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_npc_possess_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}
