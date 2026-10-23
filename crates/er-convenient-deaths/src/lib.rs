//! Three death conveniences, each one rewritten byte of ELDEN RING's own code.
//!
//! Ported from `github chozandrias76/er-convenient-deaths`, which is where the idea and the
//! choice of three options come from. What is not carried over is its addresses: all three of its
//! offsets are dead on both builds this workspace has an image for, and because each patch checks
//! its own expected bytes and skips on mismatch, a stale offset is a silent no-op rather than a
//! crash -- so a port that trusted them would have installed nothing and said nothing. The sites
//! were re-measured from the functions themselves; `src/patches.rs` carries the derivation.
//!
//! # Shape
//!
//! A `[[natives]]` entry, enabled by presence, with no dependency on the product DLL. It installs
//! no detour. It waits for the game module, writes the all-off default config if there is none,
//! and registers one `FrameBegin` game task that re-reads `er-convenient-deaths.toml` in the game
//! directory about once a second (`er_hotkey_config::HotFile`, which compares the text and does no
//! I/O between reads). Each edit is diffed against the previous config (`src/reload.rs`): an option
//! turned on has its byte written, an option turned off has the original byte -- saved when the
//! patch went in -- written back. Both happen on the game's task thread at frame begin, the same
//! thread and the same point every other `FrameBegin` task in this workspace uses.
//!
//! # What the log is worth
//!
//! `PATCHED` means the byte was written and read back as the intended value, and `RESTORED` the
//! same for the original. That is a weaker claim than a hook's call counter: the instruction a
//! patch redirects around never runs, so nothing can count it. Evidence that a patch *did*
//! anything has to come from playing -- dying and still having your runes. What the log can prove
//! is the negative, and it does so loudly: `REFUSED` (the running game is not the 2.7.2.0 build
//! the sites were measured on) and `DISARMED` (the address resolved, but the bytes there are not
//! the measured ones) both mean the game is untouched.

// The install path is `cfg(windows)`: on the host there is no game to patch, so the registry
// rows and the logging they feed have no caller. That is a cfg artifact, not dead code -- and
// without this the crate's own host tests cannot build, let alone run.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

pub(crate) mod config;
pub(crate) mod patches;
pub(crate) mod reload;

use std::{
    fmt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

use er_game_base::log::{append_line, game_directory_path};

use crate::patches::{Patch, REGISTRY};

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;

const LOG_FILE_NAME: &str = "er-convenient-deaths.log";

static LOG_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// The game directory, or the working directory when it cannot be resolved -- the same fallback
/// the log has always used, so the config and the log cannot end up in different places.
fn game_directory() -> PathBuf {
    game_directory_path()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

fn log_message(args: fmt::Arguments<'_>) {
    let path = game_directory().join(LOG_FILE_NAME);
    let seq = LOG_SEQUENCE.fetch_add(1, Ordering::SeqCst) + 1;
    append_line(&path, format_args!("[{seq:06}] {args}"));
}

/// The config path in a given game directory.
fn config_path_in(game_directory: &Path) -> PathBuf {
    game_directory.join(config::CONFIG_FILE_NAME)
}

/// What happened to one patch that was asked for.
///
/// Three outcomes, not two. A refusal (no address on this build) and a disarm (an address, but not
/// the measured bytes) have different causes and different fixes, so they are logged apart.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Outcome {
    /// The byte was written and read back as intended.
    Patched,
    /// The running build is not the one the sites were measured on. Nothing read, nothing written.
    Refused,
    /// The address resolved but the bytes there are not the measured ones. Nothing written.
    Disarmed,
}

impl Outcome {
    /// The word the transition log line ends on.
    const fn word(self) -> &'static str {
        match self {
            Self::Patched => "patched",
            Self::Refused => "REFUSED, game untouched",
            Self::Disarmed => "DISARMED, game untouched",
        }
    }
}

/// A patch that is in force: where its byte is, and what was there before.
#[derive(Clone, Copy)]
struct Live {
    target: usize,
    original: u8,
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    _module: *mut core::ffi::c_void,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // One sink for this DLL's address lines: every cdylib links its own copy of
        // er-game-base, so without this a refused address is silent here. A panic in a cdylib
        // inside the game is otherwise anonymous -- what survives is a 0xe06d7363 record naming
        // the module and nothing else.
        er_game_base::panic_report::report_panics_to("er-convenient-deaths", log_message);
        er_hook::set_hook_logger(log_message);
        START.call_once(spawn_install_task);
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_convenient_deaths_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

#[cfg(windows)]
static START: std::sync::Once = std::sync::Once::new();

/// Every patch's key and effect, in registry order, for the file written on first run.
fn documented_keys() -> Vec<(&'static str, &'static str)> {
    REGISTRY
        .iter()
        .map(|patch| (patch.config_key, patch.effect))
        .collect()
}

/// Every patch's config key, in registry order, for [`reload::transitions`].
fn registry_keys() -> Vec<&'static str> {
    REGISTRY.iter().map(|patch| patch.config_key).collect()
}

/// Write the all-off default config if there is none. Nothing is parsed here: the game task's
/// first poll reads whatever is on disk, this file or the player's.
fn ensure_config_exists(path: &Path) {
    match std::fs::metadata(path) {
        Ok(_) => log_message(format_args!(
            "config: '{}' exists; it is re-read about once a second while the game runs",
            path.display()
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            match std::fs::write(path, config::boilerplate(&documented_keys())) {
                Ok(()) => log_message(format_args!(
                    "config: none found, wrote the default to '{}' with every option OFF -- edit \
                     it while the game runs to enable one; changes apply live",
                    path.display()
                )),
                Err(error) => log_message(format_args!(
                    "config: none found and '{}' could not be written ({error}); every option \
                     stays off until a file appears there",
                    path.display()
                )),
            }
        }
        Err(error) => log_message(format_args!(
            "config: '{}' could not be inspected ({error}); polling it anyway",
            path.display()
        )),
    }
}

/// What the game task carries between frames.
#[cfg(windows)]
struct TaskState {
    hot: er_hotkey_config::HotFile,
    keys: Vec<&'static str>,
    /// The config the last reload applied. Starts all off, so the first read applies whatever the
    /// file enables.
    config: config::Config,
    base: usize,
    /// One slot per registry row, `Some` while that patch's byte is ours.
    live: Vec<Option<Live>>,
}

/// Installation runs off the loader thread: it waits on the game module and the task scheduler,
/// neither of which belongs under the loader lock.
#[cfg(windows)]
fn spawn_install_task() {
    use eldenring::cs::{CSTaskGroupIndex, CSTaskImp};
    use eldenring::fd4::FD4TaskData;
    use fromsoftware_shared::{FromStatic, SharedTaskImpExt};

    let _ = std::thread::Builder::new()
        .name("er-convenient-deaths".to_owned())
        .spawn(move || {
            let config_path = config_path_in(&game_directory());
            ensure_config_exists(&config_path);
            let mut attempts = 0_u64;
            // Bounded on purpose: an unbounded `loop { yield_now() }` in two other shells starved
            // the wineserver and hung a whole boot. See er_game_base::wait.
            let found =
                er_game_base::wait::poll_until(|| match er_game_base::mem::game_module_base() {
                    Ok(base) => Some(base),
                    Err(error) => {
                        if attempts == 0 || attempts.is_multiple_of(4096) {
                            log_message(format_args!(
                                "install: waiting for game module base: {error}"
                            ));
                        }
                        attempts = attempts.saturating_add(1);
                        None
                    }
                });
            let Some(base) = found else {
                log_message(format_args!(
                    "install: no game module base; nothing patched"
                ));
                return;
            };
            // Bounded for the same reason as the wait above.
            let Some(task) =
                er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
            else {
                log_message(format_args!(
                    "install: CSTaskImp never appeared; nothing patched, the config is not watched"
                ));
                return;
            };
            let mut state = TaskState {
                hot: er_hotkey_config::HotFile::new(config_path),
                keys: registry_keys(),
                config: config::Config::default(),
                base,
                live: vec![None; REGISTRY.len()],
            };
            log_message(format_args!(
                "install: watching '{}' from a FrameBegin task. {}",
                state.hot.path().display(),
                er_game_base::game_build::describe_build()
            ));
            task.run_recurring(
                move |_data: &FD4TaskData| tick(&mut state),
                CSTaskGroupIndex::FrameBegin,
            );
        });
}

/// One frame. Costs one integer comparison except on the frame a read is due.
#[cfg(windows)]
fn tick(state: &mut TaskState) {
    let Some(change) = state.hot.poll() else {
        return;
    };
    match &change {
        er_hotkey_config::FileChange::Text(_) => log_message(format_args!(
            "config: read '{}'",
            state.hot.path().display()
        )),
        er_hotkey_config::FileChange::Missing => log_message(format_args!(
            "config: '{}' is gone or unreadable; every option is off until it is back",
            state.hot.path().display()
        )),
    }
    let new = reload::config_for(&change);
    for transition in reload::transitions(&state.keys, &state.config, &new) {
        let Some(index) = REGISTRY
            .iter()
            .position(|patch| patch.config_key == transition.key)
        else {
            continue;
        };
        let patch = &REGISTRY[index];
        match transition.action {
            reload::Action::Apply => {
                let (outcome, live) = apply_patch(patch, state.base);
                if live.is_some() {
                    state.live[index] = live;
                }
                log_transition(patch.config_key, false, outcome.word());
            }
            reload::Action::Restore => {
                let word = match state.live[index].take() {
                    Some(live) => restore_patch(patch, live),
                    None => "nothing to restore, it was never patched",
                };
                log_transition(patch.config_key, true, word);
            }
        }
    }
    state.config = new;
    log_in_force(&state.live);
}

/// `config: keep_runes_on_death OFF -> ON, patched`.
fn log_transition(key: &str, was_on: bool, result: &str) {
    log_message(format_args!(
        "config: {key} {} -> {}, {result}",
        reload::state_word(was_on),
        reload::state_word(!was_on)
    ));
}

/// One line naming every patch and whether its byte is ours right now.
fn log_in_force(live: &[Option<Live>]) {
    let summary = REGISTRY
        .iter()
        .zip(live)
        .map(|(patch, slot)| {
            format!(
                "{} {}",
                patch.config_key,
                if slot.is_some() {
                    "patched"
                } else {
                    "original"
                }
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    log_message(format_args!("config: in force -- {summary}"));
}

/// Resolve the window, verify it, write the one byte, and read it back.
///
/// Returns the byte's address and the original it replaced when the patch is in force, which is
/// what a later restore writes back.
#[cfg(windows)]
fn apply_patch(patch: &Patch, base: usize) -> (Outcome, Option<Live>) {
    // `resolve_call_site_rva`, not `resolve_detour_address`. The detour resolver answers "may
    // MinHook overwrite five bytes at this function entry", and this is not that question: this
    // address is a single byte inside a function, read and then written. Resolving the enclosing
    // function and adding the offset afterwards is what that helper exists for -- the offset
    // never enters an address table, so nothing can read it as a licence to detour a
    // mid-function address.
    let Some(window) = er_game_base::game_build::resolve_call_site_rva(
        patch.function_rva,
        patch.offset_in_function,
        patch.name,
    )
    .map(|rva| base + rva) else {
        log_message(format_args!(
            "REFUSED {} (2.7.2.0 window rva 0x{:x} = fn 0x{:x} + 0x{:x}): the running build is \
             not the one this site was measured on -- {}. Nothing read, nothing written.",
            patch.name,
            patch.rva(),
            patch.function_rva,
            patch.offset_in_function,
            er_game_base::game_build::describe_build()
        ));
        return (Outcome::Refused, None);
    };
    if !code_window_matches(patch.name, window, patch.expected_window) {
        return (Outcome::Disarmed, None);
    }
    let target = patch.target(window);
    let Some(replaced) = patch.replaced() else {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: offset {} is outside its own {}-byte window",
            patch.name,
            patch.offset,
            patch.expected_window.len(),
        ));
        return (Outcome::Disarmed, None);
    };
    // SAFETY: `target` is inside the game image. `patch.target(window)` is an offset into the
    // window `code_window_matches` just verified byte-for-byte, at the address the resolver gave
    // for this build, so it is mapped, executable, and holds the instruction this patch was
    // measured against. Both a refusal and a mismatch have already returned above.
    if !unsafe { er_hook::write_code_byte(target, patch.replacement) } {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: VirtualProtect refused the write",
            patch.name
        ));
        return (Outcome::Disarmed, None);
    }
    // A successful write is not proof the byte landed -- another mod can own the same address --
    // so the value is read back before anything claims to be applied.
    let Some(readback) = read_code_byte(target) else {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: the byte was written but could not be read back, so \
             nothing here can say what is at that address",
            patch.name
        ));
        return (Outcome::Disarmed, None);
    };
    if readback != patch.replacement {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: wrote 0x{:02x} over 0x{replaced:02x} but read back \
             0x{readback:02x} -- something else owns this byte",
            patch.name, patch.replacement,
        ));
        return (Outcome::Disarmed, None);
    }
    patch.applied.store(true, Ordering::SeqCst);
    log_message(format_args!(
        "PATCHED {name} @0x{target:x}: 0x{replaced:02x} -> 0x{replacement:02x}. Effect: {effect}. \
         Sound because {rationale}.",
        name = patch.name,
        replacement = patch.replacement,
        effect = patch.effect,
        rationale = patch.rationale,
    ));
    (
        Outcome::Patched,
        Some(Live {
            target,
            original: replaced,
        }),
    )
}

/// Put the original byte back, if the byte there is still the one this DLL wrote.
///
/// Returns the word the transition log line ends on. The patch is no longer ours afterwards
/// whatever happens: either the original is back, or something else has rewritten the byte and
/// owns it now.
#[cfg(windows)]
fn restore_patch(patch: &Patch, live: Live) -> &'static str {
    let Live { target, original } = live;
    patch.applied.store(false, Ordering::SeqCst);
    let Some(current) = read_code_byte(target) else {
        log_message(format_args!(
            "NOT RESTORED {} @0x{target:x}: the byte could not be read, so nothing was written",
            patch.name
        ));
        return "NOT RESTORED, byte unreadable";
    };
    match reload::check_restore(current, patch.replacement, original) {
        reload::RestoreCheck::AlreadyOriginal => {
            log_message(format_args!(
                "RESTORED {} @0x{target:x}: already 0x{original:02x}, nothing written",
                patch.name
            ));
            return "restored (it already was)";
        }
        reload::RestoreCheck::Foreign => {
            log_message(format_args!(
                "NOT RESTORED {} @0x{target:x}: reads 0x{current:02x}, which is neither the \
                 0x{:02x} this DLL wrote nor the original 0x{original:02x} -- something else \
                 rewrote it after this DLL did, so it is left alone",
                patch.name, patch.replacement,
            ));
            return "NOT RESTORED, another writer owns the byte";
        }
        reload::RestoreCheck::Restore => {}
    }
    // SAFETY: `target` is the byte this DLL patched earlier in the session, inside a window that
    // was verified byte-for-byte before that write, and it was just read back as this DLL's own
    // replacement -- so it is mapped game code, and writing `original` returns it to the exact
    // instruction it was measured as.
    if !unsafe { er_hook::write_code_byte(target, original) } {
        log_message(format_args!(
            "NOT RESTORED {} @0x{target:x}: VirtualProtect refused the write",
            patch.name
        ));
        return "NOT RESTORED, VirtualProtect refused";
    }
    match read_code_byte(target) {
        Some(readback) if readback == original => {
            log_message(format_args!(
                "RESTORED {} @0x{target:x}: 0x{:02x} -> 0x{original:02x}",
                patch.name, patch.replacement
            ));
            "restored"
        }
        readback => {
            log_message(format_args!(
                "NOT RESTORED {} @0x{target:x}: wrote 0x{original:02x} but read back {readback:02x?}",
                patch.name
            ));
            "NOT RESTORED, readback mismatch"
        }
    }
}

/// Read one byte of game code through the same primitive the window check uses.
#[cfg(windows)]
fn read_code_byte(address: usize) -> Option<u8> {
    let mut byte = [0_u8; 1];
    // SAFETY: `read_bytes` reports rather than faults on an unreadable address.
    unsafe { er_game_base::mem::read_bytes(address, &mut byte) }.then_some(byte[0])
}

/// Read the window and compare it against the bytes this patch was measured against.
#[cfg(windows)]
fn code_window_matches(label: &str, address: usize, expected: &[u8]) -> bool {
    let mut actual = [0_u8; 32];
    let Some(window) = actual.get_mut(..expected.len()) else {
        log_message(format_args!(
            "DISARMED {label} @0x{address:x}: expected window is {} bytes, longer than the {} the \
             checker can read",
            expected.len(),
            actual.len(),
        ));
        return false;
    };
    // SAFETY: `address` came from the address resolver for the running build, so it names bytes
    // inside the game image; `read_bytes` reports rather than faults if it does not.
    if !unsafe { er_game_base::mem::read_bytes(address, window) } {
        log_message(format_args!(
            "DISARMED {label} @0x{address:x}: window unreadable"
        ));
        return false;
    }
    if window != expected {
        log_message(format_args!(
            "DISARMED {label} @0x{address:x}: byte mismatch (got {window:02x?}, want \
             {expected:02x?}). This address was already resolved for the running build, so the \
             likeliest cause is that another mod rewrote these bytes; the other is that the \
             measured site is wrong. Not patching."
        ));
        return false;
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_config_sits_in_the_game_directory_under_the_log_s_name() {
        let path = config_path_in(Path::new("/game"));
        assert_eq!(path, Path::new("/game/er-convenient-deaths.toml"));
        assert_eq!(
            path.file_stem(),
            Path::new(LOG_FILE_NAME).file_stem(),
            "the config and the log should be findable as a pair"
        );
    }

    /// The file written on first run has to document every patch, or the only way to find an
    /// option is to read this source.
    #[test]
    fn every_registry_key_reaches_the_default_config() {
        let written = config::boilerplate(&documented_keys());
        for patch in REGISTRY {
            assert!(
                written.contains(patch.config_key),
                "{} is not named in the default config",
                patch.name
            );
        }
        assert_eq!(
            config::parse(&written).enabled_count(),
            0,
            "the default config must enable nothing"
        );
    }

    /// Every key has to be reachable by the parser, which is a different claim from appearing in
    /// the text: a key with a space or an `=` in it would be written and never matched.
    #[test]
    fn every_registry_key_round_trips_through_the_parser() {
        for patch in REGISTRY {
            let config = config::parse(&format!("{} = true\n", patch.config_key));
            assert!(
                config.is_enabled(patch.config_key),
                "{} cannot be switched on by its own key",
                patch.name
            );
        }
    }

    /// The keys the reload diff walks are the registry's, so every patch can be both applied and
    /// restored from the file.
    #[test]
    fn every_registry_key_can_be_applied_and_restored() {
        let keys = registry_keys();
        assert_eq!(keys.len(), REGISTRY.len());
        let off = config::Config::default();
        for patch in REGISTRY {
            let on = config::parse(&format!("{} = true\n", patch.config_key));
            assert_eq!(
                reload::transitions(&keys, &off, &on),
                vec![reload::Transition {
                    key: patch.config_key,
                    action: reload::Action::Apply
                }]
            );
            assert_eq!(
                reload::transitions(&keys, &on, &off),
                vec![reload::Transition {
                    key: patch.config_key,
                    action: reload::Action::Restore
                }]
            );
        }
    }
}
