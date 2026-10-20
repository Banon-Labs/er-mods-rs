//! The mid-hook registry: instructions inside a game function, intercepted with `ilhook`.
//!
//! A *mid hook* intercepts one instruction inside a function, not its entry. MinHook (`er-hook`)
//! patches entries only, and `er_game_base::game_build::resolve_detour_address` refuses
//! mid-function addresses on purpose. The mechanism is [`ilhook`]'s jmp-ret hook: it writes a
//! 14-byte absolute jump over the hooked instructions, moves those instructions into a trampoline,
//! saves every general register into a [`Registers`](ilhook::x64::Registers) block, and calls a
//! routine that may change any of them and returns the address to continue at. Returning the
//! trampoline (`ori_func_ptr`) runs the moved instructions and falls back into the function, with
//! the edited registers restored; returning any other address continues there instead.
//!
//! The routine runs before the moved instructions, not after them. A routine that wants a value
//! the first moved instruction stores therefore edits the register that instruction reads.
//!
//! The rules, the same as `er-seamless-bugfixes/src/mid_hooks/mod.rs` (the worked example):
//!
//! 1. **Resume where the function's own path continues.** An alternative address is a branch
//!    target the function already has, or the instruction right after a call whose effect the
//!    routine reproduces; every register and local that point reads is left as the original path
//!    would leave it. Each hook's module names them.
//! 2. **Pin the whole moved window.** Every instruction the 14-byte jump touches is relocated, so
//!    the expected window is those instructions in full, generated from named instructions by
//!    `build.rs` and checked against the live image before anything is written. No branch in the
//!    function may land inside the window; the module records how that was checked.
//! 3. **Pin the resume target too**, so a target that moved fails closed rather than sending a
//!    thread into the middle of an instruction.
//! 4. **Resolve as call sites, never as detours.** Both addresses are an offset into a function,
//!    so they go through [`er_game_base::game_build::resolve_call_site_rva`], which maps the
//!    enclosing function and adds the offset afterwards.
//!
//! Comparisons honour the generated `_MASK`: a RIP-relative displacement re-encodes on every build
//! because both the instruction and the global it names move, and ilhook relocates whatever the
//! live instruction actually names.

#![cfg_attr(not(windows), allow(dead_code))]

use core::sync::atomic::{AtomicU64, AtomicUsize};

pub(crate) mod tag_list;

/// Bytes checked at one address: the generated pin and its mask (0xff compare, 0x00 ignore).
pub(crate) struct Window {
    pub(crate) bytes: &'static [u8],
    pub(crate) mask: &'static [u8],
}

/// One hooked instruction, and the address it may divert to.
pub(crate) struct MidHook {
    /// Name used in the install log and the telemetry line.
    pub(crate) name: &'static str,
    /// 1.16.2 RVA of the `.pdata` function entry that contains both addresses.
    pub(crate) function_rva: usize,
    /// Offset of the hooked instruction within [`Self::function_rva`].
    pub(crate) hook_offset: usize,
    /// The instructions the 14-byte jump overwrites, checked before hooking.
    pub(crate) expected_window: Window,
    /// Offset of the address the routine may divert to and the bytes there, or `None` for a
    /// routine that only edits registers and always runs the moved instructions.
    pub(crate) resume: Option<(usize, Window)>,
    /// The jmp-ret routine.
    pub(crate) routine: ilhook::x64::JmpToRetRoutine,
    /// Absolute resume address, published before the hook is enabled. Zero means unresolved or
    /// unused, and a routine that reads zero takes the original path.
    pub(crate) resume_address: &'static AtomicUsize,
    /// Times the routine changed what the function did. The proof a run reached the hook.
    pub(crate) acted: &'static AtomicU64,
    /// What the routine changes, printed at install time.
    pub(crate) rationale: &'static str,
}

/// Every mid hook this DLL installs.
pub(crate) static REGISTRY: &[MidHook] = &[
    tag_list::TAG_SLOT,
    tag_list::TAG_COLOUR,
    tag_list::ENEMY_BAR,
];

/// Whether `actual` equals `window` at every byte its mask compares.
pub(crate) fn window_matches(actual: &[u8], window: &Window) -> bool {
    actual.len() == window.bytes.len()
        && window.mask.len() == window.bytes.len()
        && actual
            .iter()
            .zip(window.bytes)
            .zip(window.mask)
            .all(|((have, want), mask)| have & mask == want & mask)
}

/// Whether a counter that went from `before` to `now` crossed a line worth logging: its first
/// hit, then every power of ten. A hook in a per-frame path would otherwise write a line a frame.
pub(crate) fn milestone(before: u64, now: u64) -> bool {
    if now <= before {
        return false;
    }
    if before == 0 {
        return true;
    }
    let mut step = 10_u64;
    while step <= now {
        if before < step {
            return true;
        }
        match step.checked_mul(10) {
            Some(next) => step = next,
            None => break,
        }
    }
    false
}

#[cfg(windows)]
mod install {
    use core::sync::atomic::Ordering;
    use std::sync::Mutex;

    use super::{MidHook, REGISTRY, Window, milestone, window_matches};
    use crate::log::summons_log;

    /// What happened to one hook.
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Outcome {
        Armed,
        /// No address on the running build; nothing read or written.
        Refused,
        /// Resolved, but the bytes there are not the verified ones, or ilhook declined.
        Disarmed,
    }

    /// The last count each hook's telemetry line reported.
    static REPORTED: Mutex<Vec<u64>> = Mutex::new(Vec::new());

    fn code_window_matches(name: &str, what: &str, address: usize, window: &Window) -> bool {
        let mut actual = [0_u8; 32];
        let Some(read) = actual.get_mut(..window.bytes.len()) else {
            summons_log(format_args!(
                "mid hooks: DISARMED {name} ({what} @0x{address:x}): a {}-byte window is longer \
                 than the checker reads",
                window.bytes.len()
            ));
            return false;
        };
        // SAFETY: a fault-tolerant read of code bytes.
        if !unsafe { er_game_base::mem::read_bytes(address, read) } {
            summons_log(format_args!(
                "mid hooks: DISARMED {name} ({what} @0x{address:x}): unreadable"
            ));
            return false;
        }
        if !window_matches(read, window) {
            summons_log(format_args!(
                "mid hooks: DISARMED {name} ({what} @0x{address:x}): byte mismatch (got \
                 {read:02x?}, want {:02x?} under mask {:02x?}). The address was resolved for the \
                 running build, so another mod rewrote these bytes or the mapping is wrong. Not \
                 installing.",
                window.bytes, window.mask
            ));
            return false;
        }
        true
    }

    fn install_one(hook: &MidHook, base: usize) -> Outcome {
        use ilhook::x64::{CallbackOption, HookFlags, HookType, Hooker};

        let resolve = |offset: usize| {
            er_game_base::game_build::resolve_call_site_rva(hook.function_rva, offset, hook.name)
                .map(|rva| base + rva)
        };
        let Some(site) = resolve(hook.hook_offset) else {
            summons_log(format_args!(
                "mid hooks: REFUSED {} (1.16.2 fn 0x{:x} + 0x{:x}): no mapping for the containing \
                 function on the running build -- {}. Nothing read, nothing written.",
                hook.name,
                hook.function_rva,
                hook.hook_offset,
                er_game_base::game_build::describe_build()
            ));
            return Outcome::Refused;
        };
        if !code_window_matches(hook.name, "window", site, &hook.expected_window) {
            return Outcome::Disarmed;
        }
        let mut resume = 0;
        if let Some((offset, window)) = &hook.resume {
            let Some(address) = resolve(*offset) else {
                summons_log(format_args!(
                    "mid hooks: REFUSED {}: its resume (+0x{offset:x}) has no address on the \
                     running build. Nothing written.",
                    hook.name
                ));
                return Outcome::Refused;
            };
            if !code_window_matches(hook.name, "resume", address, window) {
                return Outcome::Disarmed;
            }
            resume = address;
        }
        // Published before the jump is written: the routine reads it on its first call.
        hook.resume_address.store(resume, Ordering::Release);
        let hooker = Hooker::new(
            site,
            HookType::JmpToRet(hook.routine),
            CallbackOption::None,
            0,
            HookFlags::empty(),
        );
        // SAFETY: `site` is an instruction boundary whose window was just checked byte for byte,
        // and no branch in the function lands inside it (see the hook's module).
        match unsafe { hooker.hook() } {
            Ok(point) => {
                // The hook lives as long as the process; dropping the point would unhook it.
                core::mem::forget(point);
                summons_log(format_args!(
                    "mid hooks: ARMED {} @0x{site:x} (resume 0x{resume:x}; 1.16.2 fn 0x{:x} + \
                     0x{:x}): {}",
                    hook.name, hook.function_rva, hook.hook_offset, hook.rationale
                ));
                Outcome::Armed
            }
            Err(err) => {
                hook.resume_address.store(0, Ordering::Release);
                summons_log(format_args!(
                    "mid hooks: DISARMED {} @0x{site:x}: ilhook refused the hook: {err:?}",
                    hook.name
                ));
                Outcome::Disarmed
            }
        }
    }

    /// Install every mid hook in the registry. Called once, from the install thread.
    ///
    /// `voice_chat_guarded` says whether the voice-chat lookup was detoured. Without it a
    /// companion in the friendly tag list would reach that lookup with a `PlayerIns` it reads
    /// unchecked, so the tag-slot hook stays off; the other two do not depend on it.
    pub(crate) fn install(voice_chat_guarded: bool) {
        let base = match er_game_base::mem::game_module_base() {
            Ok(base) => base,
            Err(why) => {
                summons_log(format_args!("mid hooks: none installed -- {why}"));
                return;
            }
        };
        let (mut armed, mut refused, mut disarmed) = (0_usize, 0_usize, 0_usize);
        for hook in REGISTRY {
            if !voice_chat_guarded && hook.name == super::tag_list::TAG_SLOT.name {
                summons_log(format_args!(
                    "mid hooks: REFUSED {}: the voice-chat lookup is not guarded",
                    hook.name
                ));
                refused += 1;
                continue;
            }
            match install_one(hook, base) {
                Outcome::Armed => armed += 1,
                Outcome::Refused => refused += 1,
                Outcome::Disarmed => disarmed += 1,
            }
        }
        *REPORTED.lock().unwrap_or_else(|e| e.into_inner()) = vec![0; REGISTRY.len()];
        summons_log(format_args!(
            "mid hooks: {armed}/{} ARMED, {refused} REFUSED (no address on the running build), \
             {disarmed} DISARMED (bytes there are not the verified ones)",
            REGISTRY.len()
        ));
    }

    /// Log each hook's count at its first hit and every power of ten. Called from the game task.
    pub(crate) fn report() {
        let Ok(mut reported) = REPORTED.try_lock() else {
            return;
        };
        for (hook, last) in REGISTRY.iter().zip(reported.iter_mut()) {
            let now = hook.acted.load(Ordering::Relaxed);
            if milestone(*last, now) {
                summons_log(format_args!("mid hooks: {} acted {now} time(s)", hook.name));
                *last = now;
            }
        }
    }
}

#[cfg(windows)]
pub(crate) use install::{install, report};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_masked_byte_is_not_compared() {
        let window = Window {
            bytes: &[0x48, 0x8b, 0x0d, 0x11, 0x22, 0x33, 0x44],
            mask: &[0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00],
        };
        assert!(window_matches(
            &[0x48, 0x8b, 0x0d, 0xaa, 0xbb, 0xcc, 0xdd],
            &window
        ));
        assert!(!window_matches(
            &[0x48, 0x8b, 0x0c, 0x11, 0x22, 0x33, 0x44],
            &window
        ));
        assert!(
            !window_matches(&[0x48, 0x8b, 0x0d], &window),
            "a short read never matches"
        );
    }

    #[test]
    fn milestones_are_the_first_hit_and_powers_of_ten() {
        assert!(milestone(0, 1));
        assert!(!milestone(1, 9));
        assert!(milestone(9, 10));
        assert!(milestone(1, 250));
        assert!(!milestone(10, 99));
        assert!(milestone(99, 100));
        assert!(!milestone(5, 5));
    }

    /// ilhook overwrites 14 bytes, so every checked window must cover at least that much.
    #[test]
    fn every_window_covers_the_jump() {
        const ILHOOK_JUMP_BYTES: usize = 14;
        for hook in REGISTRY {
            assert!(
                hook.expected_window.bytes.len() >= ILHOOK_JUMP_BYTES,
                "{}: {} bytes",
                hook.name,
                hook.expected_window.bytes.len()
            );
            assert_eq!(
                hook.expected_window.bytes.len(),
                hook.expected_window.mask.len()
            );
            if let Some((_, resume)) = &hook.resume {
                assert_eq!(resume.bytes.len(), resume.mask.len());
            }
        }
    }
}
