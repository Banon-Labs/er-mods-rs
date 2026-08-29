//! Crash guards for faults only Seamless Co-op's networking mode can reach.
//!
//! Seamless Co-op puts the session into states vanilla Elden Ring reaches rarely or never, and
//! vanilla code paths written against the narrower states fault there. This DLL intercepts those
//! specific functions and returns the value the function itself already returns for the case it
//! failed to handle. It patches nothing in `ersc.dll` and adds no behaviour of its own.
//!
//! Every guard lives in [`guards::REGISTRY`]; see [`guards`] for the rules one has to satisfy. The
//! first is [`guards::null_special_effect`], a null `SpecialEffect` container reached through
//! `CS::SummonBuddyManager::Update`'s networked spirit-summon loop.
//!
//! A second, heavier mechanism lives in [`patches::REGISTRY`]: one rewritten instruction byte, for
//! a fault raised in the middle of a function where detouring the entry would mean reimplementing
//! everything the function does. Only [`patches::freelist_shutdown_assert`] uses it, and only to
//! remove an `INT3` the game's own `JZ` already jumps over. See [`patches`] for the rules; they are
//! stricter than a guard's, because a patch cannot report whether it was ever needed.
//!
//! # Version pinning
//!
//! Guard addresses are 1.16.2 RVAs. Each is re-checked against the live image's bytes before its
//! hook is installed, so a different build disarms that guard and says so in the log rather than
//! detouring whatever now occupies the address.
//!
//! # Why a private MinHook instance, and why that is safe here
//!
//! The repo's usual rule is to route a companion DLL's detours through the product's
//! `er_effects_union_register` export, so a single MinHook instance owns every shared prologue.
//! That is not available to these guards: the union's handler type is
//! `fn(usize, usize, usize, usize) -> usize`, and `er-hook` states plainly that it is "not for
//! float-arg or >4-stack-arg targets". `CS::SpecialEffect::Apply` takes eight arguments, four of
//! them on the stack. Its union dispatcher would forward only the register four and drop the rest,
//! which corrupts the call rather than protecting it -- worse than the crash being guarded.
//!
//! So both guards use this DLL's own MinHook instance, which is safe because no er-quickload DLL
//! hooks either address (searched repo-wide), and because the install-time prologue check is itself
//! the collision detector: a second MinHook instance that got there first would have replaced the
//! entry with a jump, the byte comparison would fail, and the guard would disarm loudly instead of
//! trampling a trampoline. If the product ever does hook one of these addresses, that check turns
//! the collision into a log line rather than a silent corruption.

// Everything below the crate docs is the install path, and the install path is `cfg(windows)`: on
// the host there is no game to hook, so the registry rows, their stubs, and the logging they feed
// have no caller. That is a cfg artifact, not dead code -- the same reason `er-invasion-warp`
// carries this attribute. Without it `cargo test -p er-seamless-bugfixes` cannot even build,
// which is how the crate's own host tests came to be unrunnable.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

pub(crate) mod guards;
pub(crate) mod patches;

use std::{
    fmt,
    path::PathBuf,
    sync::atomic::{AtomicU64, AtomicUsize, Ordering},
};

use er_game_base::log::{append_line, game_directory_path};

use crate::{
    guards::{Guard, ORIGINAL_UNSET, REGISTRY},
    patches::Patch,
};

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;

const LOG_FILE_NAME: &str = "er-seamless-bugfixes.log";

static LOG_SEQUENCE: AtomicU64 = AtomicU64::new(0);
static GAME_BASE: AtomicUsize = AtomicUsize::new(0);

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn Sleep(ms: u32);
}

fn log_message(args: fmt::Arguments<'_>) {
    let path = game_directory_path()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
        .join(LOG_FILE_NAME);
    let seq = LOG_SEQUENCE.fetch_add(1, Ordering::SeqCst) + 1;
    append_line(&path, format_args!("[{seq:06}] {args}"));
}

#[cfg(windows)]
static START: std::sync::Once = std::sync::Once::new();

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
        // One sink for this DLL's hook + address lines. Without it a refused address is
        // silent HERE, because every cdylib links its own copy of er-hook/er-game-base.
        // A rust_panic in a cdylib loaded into the game is otherwise anonymous: the message goes to a
        // stderr nobody reads, and what survives is a 0xe06d7363 record naming the MODULE and nothing
        // else. Two boots were lost to one before this existed. See er_game_base::panic_report.
        er_game_base::panic_report::report_panics_to("er-seamless-bugfixes", log_message);
        er_hook::set_hook_logger(log_message);
        START.call_once(spawn_install_task);
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_seamless_bugfixes_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

/// Installation runs off the loader thread: it waits on the game module and calls MinHook, neither
/// of which belongs under the loader lock.
#[cfg(windows)]
fn spawn_install_task() {
    let _ = std::thread::Builder::new()
        .name("er-seamless-bugfixes".to_owned())
        .spawn(|| {
            let mut attempts = 0_u64;
            // BOUNDED (2026-08-29): an unbounded `loop { yield_now() }` in two other shells starved the
            // wineserver and hung a whole boot -- see er_game_base::wait. Same shape, same fix.
            let found =
                er_game_base::wait::poll_until(|| match er_game_base::mem::game_module_base() {
                    Ok(base) => Some(base),
                    Err(err) => {
                        if attempts == 0 || attempts.is_multiple_of(4096) {
                            log_message(format_args!(
                                "install: waiting for game module base: {err}"
                            ));
                        }
                        attempts = attempts.saturating_add(1);
                        None
                    }
                });
            let Some(base) = found else {
                log_message(format_args!(
                    "install: no game module base; nothing installed"
                ));
                return;
            };
            GAME_BASE.store(base, Ordering::SeqCst);
            install_guards(base);
            install_patches(base);
        });
}

/// Confirm the live bytes at `address` still match what an RVA was verified against.
///
/// Fail-closed, and doing double duty: a drifted window means either the running image is not the
/// 1.16.2 build these addresses came from, or something else got to the address first. Both are
/// reasons not to install, and both produce a log line naming the bytes actually found.
///
/// `drift_hint` is what that log line offers as the likely cause, which differs by what is being
/// checked -- a detour target can be occupied by another hook, a mid-function window cannot.
#[cfg(windows)]
fn code_window_matches(label: &str, address: usize, expected: &[u8], drift_hint: &str) -> bool {
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
    if !unsafe { er_game_base::mem::read_bytes(address, window) } {
        log_message(format_args!(
            "DISARMED {label} @0x{address:x}: window unreadable"
        ));
        return false;
    }
    if window != expected {
        log_message(format_args!(
            "DISARMED {label} @0x{address:x}: byte mismatch (got {window:02x?}, want \
             {expected:02x?}) -- {drift_hint}. Not installing.",
        ));
        return false;
    }
    true
}

/// [`code_window_matches`] for a guard's detour target, where the likeliest reason the bytes moved
/// is that another hook already replaced the entry with its own jump.
#[cfg(windows)]
fn prologue_matches(guard: &Guard, address: usize) -> bool {
    code_window_matches(
        guard.name,
        address,
        guard.expected_prologue,
        "either this is not the 1.16.2 build these RVAs were verified against, or the entry is \
         already detoured",
    )
}

#[cfg(windows)]
fn install_guards(base: usize) {
    use er_hook::{MH_Initialize, MH_STATUS};

    log_message(format_args!(
        "attach: {} guard(s); game base 0x{base:x}. Each guard returns the guarded function's own \
         'nothing here' value on a null container and is otherwise pass-through.",
        REGISTRY.len()
    ));

    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            log_message(format_args!("install: MH_Initialize failed: {status:?}"));
            return;
        }
    }

    let mut armed = 0_usize;
    for guard in REGISTRY {
        let address = base + guard.rva;
        if !prologue_matches(guard, address) {
            continue;
        }
        // Before the hook can arm, not after: a stub that resolves a game global reads that slot
        // on its very first call, and the first call can land the instant the hook goes live.
        if let Some(prepare) = guard.prepare {
            prepare(base);
        }
        if install_guard(guard, address) {
            armed += 1;
            log_message(format_args!(
                "ARMED {} @0x{address:x} (rva 0x{:x}): {}",
                guard.name, guard.rva, guard.rationale
            ));
        }
    }

    log_message(format_args!(
        "install complete: {armed}/{} guard(s) armed",
        REGISTRY.len()
    ));

    warn_on_partial_groups();

    if armed > 0 {
        spawn_telemetry_task();
    }
}

/// Apply every patch in the registry.
///
/// Separate from [`install_guards`] because the mechanism is different in a way that matters to a
/// reader of the log: a guard that fails to arm leaves the game exactly as it was, while a patch
/// that half-applies would leave a rewritten instruction behind. The read-back is what rules that
/// out, so it is reported whether it succeeds or not.
#[cfg(windows)]
fn install_patches(base: usize) {
    log_message(format_args!(
        "attach: {} code patch(es); game base 0x{base:x}. Each patch removes one instruction the \
         game's own control flow already skips, and writes exactly one byte to do it.",
        patches::REGISTRY.len()
    ));

    let mut applied = 0_usize;
    for patch in patches::REGISTRY {
        if apply_patch(patch, base) {
            applied += 1;
        }
    }

    log_message(format_args!(
        "patch complete: {applied}/{} patch(es) applied",
        patches::REGISTRY.len()
    ));
}

/// Verify the window, write the one byte, and read it back.
#[cfg(windows)]
fn apply_patch(patch: &Patch, base: usize) -> bool {
    let window = base + patch.rva;
    if !code_window_matches(
        patch.name,
        window,
        patch.expected_window,
        "either this is not the 1.16.2 build this window was verified against, or another mod \
         already rewrote it",
    ) {
        return false;
    }
    let target = patch.target(base);
    let Some(replaced) = patch.replaced() else {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: offset {} is outside its own {}-byte window",
            patch.name,
            patch.offset,
            patch.expected_window.len(),
        ));
        return false;
    };
    // SAFETY: `target` is inside the game image -- `patch.target(base)` is an offset into the
    // window `code_window_matches` just verified byte-for-byte against this build, so the address
    // is mapped, executable, and holds the instruction this patch was written for. The call is
    // `unsafe` because the primitive takes a bare `usize` and stores through it; that verified
    // window is what earns the deref, and a mismatch has already returned `false` above.
    if !unsafe { er_hook::write_code_byte(target, patch.replacement) } {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: VirtualProtect refused the write",
            patch.name
        ));
        return false;
    }
    // A successful `VirtualProtect` says the write was permitted, not that the byte is still what
    // we wrote. Read it back: this is the only proof this patch can offer that it changed anything.
    let mut readback = [0_u8; 1];
    if !unsafe { er_game_base::mem::read_bytes(target, &mut readback) } {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: wrote 0x{:02x} but could not read the byte back, so \
             whether it landed is unknown",
            patch.name, patch.replacement,
        ));
        return false;
    }
    if readback[0] != patch.replacement {
        log_message(format_args!(
            "DISARMED {} @0x{target:x}: wrote 0x{:02x} but found 0x{:02x} -- something else owns \
             this address",
            patch.name, patch.replacement, readback[0],
        ));
        return false;
    }
    patch.applied.store(true, Ordering::SeqCst);
    log_message(format_args!(
        "PATCHED {} @0x{target:x} (rva 0x{:x}): 0x{replaced:02x} -> 0x{:02x}, read back. {}",
        patch.name,
        patch.rva + patch.offset,
        patch.replacement,
        patch.rationale
    ));
    true
}

/// Report any guard GROUP that came up partly armed.
///
/// A half-armed group is not a safe state: for `null_special_effect`, the query guard alone sends
/// the crashing caller into the apply path that faults on the same field. Scoping this by group
/// rather than by the whole registry matters as soon as two unrelated guards exist -- otherwise one
/// unrelated guard failing to arm would declare an otherwise-complete run UNGUARDED, which is both
/// false and the kind of noise that gets warnings ignored.
#[cfg(windows)]
fn warn_on_partial_groups() {
    for guard in REGISTRY {
        // Report each group once, at its first member.
        if REGISTRY.iter().position(|other| other.group == guard.group)
            != REGISTRY
                .iter()
                .position(|other| core::ptr::eq(other, guard))
        {
            continue;
        }
        let members: Vec<&Guard> = REGISTRY
            .iter()
            .filter(|other| other.group == guard.group)
            .collect();
        let armed = members
            .iter()
            .filter(|member| member.original.load(Ordering::SeqCst) != ORIGINAL_UNSET)
            .count();
        if armed == 0 || armed == members.len() {
            continue;
        }
        log_message(format_args!(
            "WARNING: group '{}' is only {armed}/{} armed. Its guards are designed to act \
             together, so treat this run as UNGUARDED for that group.",
            guard.group,
            members.len()
        ));
    }
}

#[cfg(windows)]
fn install_guard(guard: &Guard, address: usize) -> bool {
    use er_hook::{MH_EnableHook, MH_STATUS, MhHook};

    let hook = match unsafe {
        MhHook::new(
            address as *mut core::ffi::c_void,
            guard.detour as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            log_message(format_args!(
                "DISARMED {} @0x{address:x}: MhHook::new failed: {status:?}",
                guard.name
            ));
            return false;
        }
    };
    // Publish the trampoline BEFORE enabling. The stub's pass-through path tail-jumps through this
    // slot, and the first call can land the instant the hook goes live.
    guard
        .original
        .store(hook.trampoline() as usize, Ordering::SeqCst);
    match unsafe { MH_EnableHook(address as *mut core::ffi::c_void) } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ENABLED => true,
        status => {
            guard.original.store(ORIGINAL_UNSET, Ordering::SeqCst);
            log_message(format_args!(
                "DISARMED {} @0x{address:x}: MH_EnableHook failed: {status:?}",
                guard.name
            ));
            false
        }
    }
}

/// How often the telemetry task reports. The guards themselves cannot log -- they are naked stubs
/// on a hot path -- so their counters are read from outside instead.
#[cfg(windows)]
const TELEMETRY_POLL_MS: u32 = 5_000;

/// Report each guard's block count whenever it changes.
///
/// This is the semaphore for the whole DLL. A session where every count is still zero never met the
/// bug and is not evidence that the guard works; a nonzero count is a fault the game would
/// otherwise have taken.
#[cfg(windows)]
fn spawn_telemetry_task() {
    let _ = std::thread::Builder::new()
        .name("er-seamless-bugfixes-telemetry".to_owned())
        .spawn(|| {
            let mut reported = vec![0_u64; REGISTRY.len()];
            loop {
                unsafe { Sleep(TELEMETRY_POLL_MS) };
                for (index, guard) in REGISTRY.iter().enumerate() {
                    let blocked = guard.blocked.load(Ordering::Relaxed);
                    if blocked != reported[index] {
                        log_message(format_args!(
                            "BLOCKED {} guard_blocked_total={blocked} (+{}) -- null container \
                             intercepted; the game would have faulted here",
                            guard.name,
                            blocked.saturating_sub(reported[index]),
                        ));
                        reported[index] = blocked;
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_guard_is_documented_and_distinct() {
        for guard in REGISTRY {
            assert!(!guard.name.is_empty());
            assert!(
                !guard.rationale.is_empty(),
                "{}: a guard must say why its return value is correct",
                guard.name
            );
            assert!(guard.rva != 0);
            assert!(
                !guard.expected_prologue.is_empty(),
                "{}: a guard must pin its address by bytes",
                guard.name
            );
        }
        for (index, guard) in REGISTRY.iter().enumerate() {
            for other in &REGISTRY[index + 1..] {
                assert_ne!(
                    guard.rva, other.rva,
                    "two guards on one address would fight over the same prologue"
                );
            }
        }
    }

    #[test]
    fn guards_start_unarmed() {
        for guard in REGISTRY {
            assert_eq!(
                guard.blocked.load(Ordering::Relaxed),
                0,
                "{}: block counter must start at zero so a nonzero value means a real fault",
                guard.name
            );
            assert_eq!(
                guard.original.load(Ordering::Relaxed),
                ORIGINAL_UNSET,
                "{}: the stub tail-jumps through this slot, so it must be unset until a real \
                 trampoline is published",
                guard.name
            );
        }
    }
}
