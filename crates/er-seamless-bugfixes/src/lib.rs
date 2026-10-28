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
//! The one-byte code patch that used to live here, `freelist_shutdown_assert` (the `INT3` in
//! `CS::CSFreeListMemorySystem`'s shutdown), moved to `er-crash-logging` on 2026-10-26. It fires
//! on vanilla 2.7.2.0 with no Seamless loaded (bd `er-effects-rs-843o`), so it is not a Seamless
//! bug, and players who never loaded this DLL crashed on every quit. It must not come back here:
//! two DLLs writing the same byte means the second finds it already changed.
//!
//! # Version pinning, and the order it has to happen in
//!
//! Guard and mid-hook addresses are 2.7.2.0 RVAs. Every address is first checked against
//! the running build -- through [`er_game_base::game_build`], which refuses every game address on
//! any other build -- and only then re-checked against the live image's bytes.
//!
//! That order is the whole of this section, because it was the other way round and the cost is
//! measured. When these were 1.16.2 RVAs and the game was 1.17, the byte check ran at a raw
//! `base + rva`, which was unrelated code: all three guards and the one patch failed the
//! comparison and disarmed, reporting `byte mismatch` -- wording that reads as a stale signature
//! when the fault was an untranslated address. The
//! `LoadBalancerParam` guard was among them, and on 2026-08-30 the exact crash it exists to stop
//! killed a session that had it "installed": `install complete: 0/3 guard(s) armed` at attach,
//! `DLPanic` at `FD4Singleton.h:180` twenty-five hours later.
//!
//! With the build check first, three outcomes are distinguishable in the log and they send a
//! reader to three different places:
//!
//! * `REFUSED` -- the running build is not 2.7.2.0, or a guard's entry is not on the 2.7.2.0
//!   detour allowlist. Nothing was read, nothing was written, and the fix is a re-measurement,
//!   not a new signature.
//! * `DISARMED` -- the address resolved and the bytes there are not the ones it was verified
//!   against. Something else owns the address, or the constant is wrong.
//! * `ARMED` / `PATCHED` -- installed.
//!
//! A refusal is per row. One refused guard costs that guard and nothing else; see bd
//! `one-refused-hook-must-not-abort-the-installer-2026-08-30`.
//!
//! # Why a private MinHook instance, and why that is safe here
//!
//! The repo's usual rule is to route a companion DLL's detours through the product's
//! `er_effects_union_register_2720` export, so a single MinHook instance owns every shared prologue.
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
pub(crate) mod mid_hooks;

use std::{
    fmt,
    path::PathBuf,
    sync::atomic::{AtomicU64, AtomicUsize, Ordering},
};

use er_game_base::log::{append_line, game_directory_path};

use crate::guards::{Guard, ORIGINAL_UNSET, REGISTRY};

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
        // silent here, because every cdylib links its own copy of er-hook/er-game-base.
        // A rust_panic in a cdylib loaded into the game is otherwise anonymous: the message goes to a
        // stderr nobody reads, and what survives is a 0xe06d7363 record naming the module and nothing
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
            // Bounded (2026-08-29): an unbounded `loop { yield_now() }` in two other shells starved the
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
            install_mid_hooks(base);
        });
}

/// What happened to one guard or one mid hook.
///
/// Three facts, not two, and a log that collapses them sends a reader to the wrong place. The
/// four dead rows on 2026-08-30 all reported `byte mismatch`, which describes a signature that
/// went stale on an address that is still right -- the opposite of what had happened. Naming the
/// outcomes in the type is what stops the install path from having to remember the distinction.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Outcome {
    /// Installed: detour or mid hook enabled.
    Armed,
    /// No address on the running build. Nothing was read and nothing was written.
    Refused,
    /// The address resolved, and what is at it is not what this row was verified against -- or
    /// the install itself failed. Either way the game is exactly as it was.
    Disarmed,
}

/// The name of a guard in `guard`'s own group that has no address on the running build.
///
/// A group exists because its members are only safe armed together. `null_special_effect` guards
/// the query and the apply for one reason: guarding the query alone makes the caller's
/// `if !has { apply }` pass, and the apply path faults on the same null field one RVA later. So a
/// group that cannot be found whole is not a group to half-arm; it is a group to skip.
///
/// [`warn_on_partial_groups`] reports a half-armed group after the fact. This prevents the half
/// that is predictable in advance: a member whose entry is missing from the 2.7.2.0 detour
/// allowlist is refused on its own while its partner is not. (On 1.17 that was not hypothetical:
/// `SpecialEffect::HasSpecialEffectId` carried no detour row while `SpecialEffect::Apply` did.)
/// Checking addresses before the byte check is what makes that asymmetry reachable -- before it,
/// both simply failed the comparison.
///
/// `resolved` is indexed against [`REGISTRY`], so the two must be the same length and order; the
/// only producer is [`install_guards`], and `a_group_is_skipped_whole_when_one_member_has_no_address`
/// exercises the rule.
fn unmapped_group_member(resolved: &[Option<usize>], guard: &Guard) -> Option<&'static str> {
    REGISTRY
        .iter()
        .zip(resolved)
        .find(|(other, address)| other.group == guard.group && address.is_none())
        .map(|(other, _)| other.name)
}

/// Confirm the live bytes at `address` still match what an RVA was verified against.
///
/// `address` is expected to have been checked against the running build already. That is what makes
/// this check mean what it says: a mismatch here is now evidence about the bytes, because the
/// address question was answered first and separately.
///
/// Fail-closed either way, and the log line names the bytes actually found so the reader does not
/// have to take the verdict on trust.
///
/// `drift_hint` is what that line offers as the likely cause, which differs by what is being
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
///
/// `address` has already been through [`er_game_base::game_build::resolve_detour_address`], so
/// "this is not the build these RVAs came from" is no longer one of the explanations: another
/// build is a `REFUSED` and is reported as one, above.
#[cfg(windows)]
fn prologue_matches(guard: &Guard, address: usize) -> bool {
    code_window_matches(
        guard.name,
        address,
        guard.expected_prologue,
        "the running build is 2.7.2.0, the build this address was measured on, so the likeliest \
         cause is that another hook replaced the entry first; the other is that the constant is \
         wrong",
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

    // Pass one: may each guarded function be hooked on the running build?
    //
    // Every guard is checked before any of them is installed, for two reasons. The byte check
    // needs an address the running build vouches for (that is the bug this ordering fixes), and
    // one guard's answer decides whether another may arm at all -- `unmapped_group_member` cannot
    // be asked until all the answers are in.
    //
    // `resolve_detour_address`, not `resolve_game_address`: MinHook is about to write five bytes
    // at each of these, and only the 2.7.2.0 detour allowlist says an address is a function entry
    // with a relocatable prologue. Its answer is `base + rva` itself or a refusal, so
    // `MhHook::new`'s own check of the same address in `install_guard` answers the same way.
    let resolved: Vec<Option<usize>> = REGISTRY
        .iter()
        .map(|guard| er_game_base::game_build::resolve_detour_address(base + guard.rva, guard.name))
        .collect();

    let (mut armed, mut refused, mut disarmed) = (0_usize, 0_usize, 0_usize);
    for (guard, address) in REGISTRY.iter().zip(&resolved) {
        match install_one_guard(base, guard, *address, &resolved) {
            Outcome::Armed => armed += 1,
            Outcome::Refused => refused += 1,
            Outcome::Disarmed => disarmed += 1,
        }
    }

    log_message(format_args!(
        "install complete: {armed}/{} guard(s) ARMED, {refused} REFUSED (not hookable on the \
         running build), {disarmed} DISARMED (address checked, bytes there are not the verified \
         ones)",
        REGISTRY.len()
    ));

    warn_on_partial_groups();

    if armed > 0 {
        spawn_telemetry_task();
    }
}

/// Install one guard, and report which of the three things happened to it.
///
/// Per guard on purpose. Every exit from here is one row's outcome and the caller's loop carries
/// on, because a refused address is the expected case during a version migration and losing the
/// unrelated guards with it is a choice -- a bad one when one of them is the guard that stops a
/// `DLPanic`. See bd `one-refused-hook-must-not-abort-the-installer-2026-08-30`.
#[cfg(windows)]
fn install_one_guard(
    base: usize,
    guard: &Guard,
    address: Option<usize>,
    resolved: &[Option<usize>],
) -> Outcome {
    let Some(address) = address else {
        log_message(format_args!(
            "REFUSED {} (2.7.2.0 rva 0x{:x}): not hookable on the running build -- {}. There is \
             no address to check bytes at, so nothing was read and nothing installed. This is a \
             build or detour-allowlist refusal, not a stale signature.",
            guard.name,
            guard.rva,
            er_game_base::game_build::describe_build()
        ));
        return Outcome::Refused;
    };
    if let Some(missing) = unmapped_group_member(resolved, guard) {
        log_message(format_args!(
            "REFUSED {} @0x{address:x}: this address is hookable, but '{missing}' in its group \
             '{}' is not, and these guards are only correct armed together -- arming this one alone \
             would send the crashing caller into the unguarded half. Skipping the whole group.",
            guard.name, guard.group
        ));
        return Outcome::Refused;
    }
    if !prologue_matches(guard, address) {
        return Outcome::Disarmed;
    }
    // Before the hook can arm, not after: a stub that resolves a game global reads that slot
    // on its very first call, and the first call can land the instant the hook goes live.
    if let Some(prepare) = guard.prepare {
        prepare(base);
    }
    if !install_guard(guard, address) {
        return Outcome::Disarmed;
    }
    log_message(format_args!(
        "ARMED {} @0x{address:x} (2.7.2.0 rva 0x{:x}): {}",
        guard.name, guard.rva, guard.rationale
    ));
    Outcome::Armed
}

/// Install every mid hook in the registry.
///
/// Both addresses of a hook are offsets into a function, so both go through
/// `resolve_call_site_rva`, and both windows are checked before anything is written. The resume
/// address is published before the hook goes live: the routine reads it on its first call.
#[cfg(windows)]
fn install_mid_hooks(base: usize) {
    log_message(format_args!(
        "attach: {} mid hook(s); game base 0x{base:x}. Each diverts one instruction to a branch \
         its own function already has, and otherwise runs the moved instructions unchanged.",
        mid_hooks::REGISTRY.len()
    ));
    let (mut armed, mut refused, mut disarmed) = (0_usize, 0_usize, 0_usize);
    for hook in mid_hooks::REGISTRY {
        match install_mid_hook(hook, base) {
            Outcome::Armed => armed += 1,
            Outcome::Refused => refused += 1,
            Outcome::Disarmed => disarmed += 1,
        }
    }
    log_message(format_args!(
        "mid hook complete: {armed}/{} mid hook(s) ARMED, {refused} REFUSED (wrong build), \
         {disarmed} DISARMED (address checked, bytes there are not the verified ones)",
        mid_hooks::REGISTRY.len()
    ));
    if armed > 0 {
        spawn_mid_hook_telemetry_task();
    }
}

#[cfg(windows)]
fn install_mid_hook(hook: &mid_hooks::MidHook, base: usize) -> Outcome {
    use ilhook::x64::{CallbackOption, HookFlags, HookType, Hooker};

    let resolve = |offset: usize| {
        er_game_base::game_build::resolve_call_site_rva(hook.function_rva, offset, hook.name)
            .map(|rva| base + rva)
    };
    let (Some(site), Some(resume)) = (resolve(hook.hook_offset), resolve(hook.resume_offset))
    else {
        log_message(format_args!(
            "REFUSED {} (2.7.2.0 fn 0x{:x} + 0x{:x}, resume + 0x{:x}): the running build is not \
             the one this function was measured on -- {}. Nothing read, nothing written.",
            hook.name,
            hook.function_rva,
            hook.hook_offset,
            hook.resume_offset,
            er_game_base::game_build::describe_build()
        ));
        return Outcome::Refused;
    };
    let drift = "the running build is 2.7.2.0, the build this window was measured on, so the \
                 likeliest cause is that another mod rewrote these bytes; the other is that the \
                 constant is wrong";
    if !code_window_matches(hook.name, site, hook.expected_window, drift)
        || !code_window_matches(hook.name, resume, hook.expected_resume, drift)
    {
        return Outcome::Disarmed;
    }
    hook.resume.store(resume, Ordering::Release);
    let hooker = Hooker::new(
        site,
        HookType::JmpToRet(hook.routine),
        CallbackOption::None,
        0,
        HookFlags::empty(),
    );
    match unsafe { hooker.hook() } {
        Ok(point) => {
            // The hook lives as long as the process; dropping the point would unhook it.
            core::mem::forget(point);
            log_message(format_args!(
                "ARMED {} @0x{site:x} (resume 0x{resume:x}; 2.7.2.0 fn 0x{:x} + 0x{:x}): {}",
                hook.name, hook.function_rva, hook.hook_offset, hook.rationale
            ));
            Outcome::Armed
        }
        Err(err) => {
            hook.resume.store(0, Ordering::Release);
            log_message(format_args!(
                "DISARMED {} @0x{site:x}: ilhook refused the hook: {err}",
                hook.name
            ));
            Outcome::Disarmed
        }
    }
}

/// Report each mid hook's diversion count whenever it changes. A count that stays zero means the
/// run never met the bug, not that the hook works.
#[cfg(windows)]
fn spawn_mid_hook_telemetry_task() {
    let _ = std::thread::Builder::new()
        .name("er-seamless-bugfixes-mid-hooks".to_owned())
        .spawn(|| {
            let mut reported = vec![0_u64; mid_hooks::REGISTRY.len()];
            loop {
                unsafe { Sleep(TELEMETRY_POLL_MS) };
                for (index, hook) in mid_hooks::REGISTRY.iter().enumerate() {
                    let diverted = hook.diverted.load(Ordering::Relaxed);
                    if diverted != reported[index] {
                        log_message(format_args!(
                            "DIVERTED {} mid_hook_diverted_total={diverted} (+{}) -- no local \
                             player; the game would have faulted here",
                            hook.name,
                            diverted.saturating_sub(reported[index]),
                        ));
                        reported[index] = diverted;
                    }
                }
            }
        });
}

/// Report any guard group that came up partly armed.
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
    // Publish the trampoline before enabling. The stub's pass-through path tail-jumps through this
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

    /// The address check has to be able to answer no per guard, and the answer has to be a group
    /// property. On 1.17 exactly one member of `null_special_effect` had a detour row, and arming
    /// that half alone is the failure that module's header exists to describe.
    #[test]
    fn a_group_is_skipped_whole_when_one_member_has_no_address() {
        const FAKE_BASE: usize = 0x1_4000_0000;
        for missing in REGISTRY {
            let resolved: Vec<Option<usize>> = REGISTRY
                .iter()
                .map(|guard| {
                    if core::ptr::eq(guard, missing) {
                        None
                    } else {
                        Some(FAKE_BASE + guard.rva)
                    }
                })
                .collect();
            for guard in REGISTRY {
                let skipped = unmapped_group_member(&resolved, guard);
                if guard.group == missing.group {
                    assert_eq!(
                        skipped,
                        Some(missing.name),
                        "'{}' shares a group with the unmapped '{}' and must be skipped with it",
                        guard.name,
                        missing.name
                    );
                } else {
                    assert_eq!(
                        skipped, None,
                        "'{}' is in group '{}' and must not be skipped for '{}' in group '{}'",
                        guard.name, guard.group, missing.name, missing.group
                    );
                }
            }
        }
    }

    /// The converse, so the rule cannot be satisfied by refusing everything: when every address
    /// resolves, no guard is held back.
    #[test]
    fn a_fully_mapped_registry_holds_nothing_back() {
        const FAKE_BASE: usize = 0x1_4000_0000;
        let resolved: Vec<Option<usize>> = REGISTRY
            .iter()
            .map(|guard| Some(FAKE_BASE + guard.rva))
            .collect();
        for guard in REGISTRY {
            assert_eq!(
                unmapped_group_member(&resolved, guard),
                None,
                "'{}' was held back although every address resolved",
                guard.name
            );
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
