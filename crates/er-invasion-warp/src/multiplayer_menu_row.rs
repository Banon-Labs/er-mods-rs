//! The Multiplayer row on the Escape menu, un-greyed by answering the one predicate that greys it.
//!
//! # What greys it
//!
//! `CS::MainTopDialog::CreateExecJob` (1.16.2 `0x14090e9d0`) fills a static array of seven pause
//! menu rows -- 1.17.1 base `0x143d71260`, stride `0xc8` -- each row being
//! `{ u32 textId; std::function a; std::function b; std::function c; }`. `a` builds the submenu
//! job, `b` answers whether the row exists at all, and `c` is the enable predicate. The builder
//! copies `c` into a wrapper returning `!c()` and stores that as the row's grey slot, so the row
//! holds a callable and re-asks rather than caching a boolean. That is what makes a return
//! override work at all.
//!
//! The Multiplayer row is index 5, text id `0x18a90` in `GR_MenuText.fmg`. Its `b` is
//! `mov al,1; ret`, so the dynamic path is always taken, and its `c` is the function this module
//! hooks. `c` appears exactly once in the whole image -- at `0x142b00b68 + 0x10`, the `_Do_call`
//! slot of that row's own `_Func_impl` vftable -- so answering its return touches this row and
//! nothing else in the game.
//!
//! # The two terms, and which one is false here
//!
//! ```text
//! enabled = IsInOnlineMode() || CS::PartyMemberInfo::IsNotAlone(GetPartyMemberInfo())
//! ```
//!
//! `IsInOnlineMode` (1.17.1 `0x14067ae80`) is one load of `GameMan + 0xbc8`. This mod keeps that
//! byte clear for the whole process lifetime on purpose -- `apply_online_disable` stubs the getter
//! -- because a modded client answering "online" can reach official matchmaking and get the player
//! banned. `IsNotAlone` (1.17.1 `0x1409fa5f0`) is the vanilla session's own player count and sign
//! man, which Seamless never populates because it runs its own netcode.
//!
//! Read out of a live session on 2026-09-16 (pid 39654, read-only `/proc/<pid>/mem`): `GameMan`
//! `0x9003c080`, `+0xbc8` = 0, `PartyMemberInfo` `0xa73bb140`, `+0x1c` = 1. Both terms false, the
//! predicate returns 0, the wrapper returns `!0`, and the row draws greyed.
//!
//! So this module lifts the grey without touching either term. Nothing here turns the online flag
//! back on, joins a session, or writes a param byte: it answers one menu predicate.
//!
//! # The masking trap
//!
//! The predicate returns `bool` in `al`, and neither tail clears the upper 24 bits of `eax` -- the
//! fall-through `ret` at `0x14091311e` hands back whatever `IsNotAlone` left there. Reading the
//! full register live gave `0x10d200`, `0x10df00` and `0x10e200` across 45 calls, every one of
//! them `al = 0`, so a comparison against zero never matched and nothing was ever forced while the
//! row sat visibly greyed. Mask to the byte the caller tests.
//!
//! # Proven where
//!
//! The same override, applied from Frida by `scripts/frida/force-multiplayer-menu.js`, un-greyed
//! the row on run `br-20260916-193045-979a` with the user watching the screen: 139 calls, all
//! returning 0, all forced. This module is that measurement written as product code.

use core::sync::atomic::{AtomicUsize, Ordering};

/// The Multiplayer row's enable predicate on 1.17.
///
/// Carried from 1.16.2 `0x140911f60` and then read rather than trusted. Two independent methods
/// agree: `scripts/map-rvas-1162-to-1170.py` calls it unique on a 35-byte signature, and the
/// masked pattern
/// `4883ec28e8????????84c07516e8????????488bc8e8????????84c075054883c428c3b0014883c428c3` has
/// exactly one hit in `eldenring-deobf-1.17.1.bin`, at this address. Its rva is below the
/// `0xafefe9` boundary, so 1.17.0 and 1.17.1 put it in the same place.
///
/// The 42 bytes there disassemble to the two-term predicate this module's header describes, ending
/// in the `mov al,1` tail at `+0x23` and the bare `ret` at `+0x22`.
const MULTIPLAYER_ROW_ENABLED_RVA: u32 = 0x91_3100;

/// Trampoline to the original predicate, or the next handler in the union chain.
static ORIG_ROW_ENABLED: AtomicUsize = AtomicUsize::new(0);

/// How often the predicate answered, and how many of those answers were refusals this lifted.
///
/// Two numbers rather than one because they fail differently. `CALLS` at zero says the pause menu
/// was never built with the hook in place, which is not evidence about the row; `FORCED` at zero
/// with `CALLS` climbing says the engine was already allowing the row and this module had nothing
/// to do. Collapsing them into a single counter is what made the first Frida reading unreadable.
static CALLS: AtomicUsize = AtomicUsize::new(0);
static FORCED: AtomicUsize = AtomicUsize::new(0);

/// What to hand back for a given original return value.
///
/// Refusal-only, so nothing here can grey a row the engine was enabling: a non-zero low byte is
/// returned untouched, upper bits and all, because the caller tests `al` and the engine's own
/// verdict is the better answer whenever it allows.
fn answered_verdict(original: usize) -> usize {
    if original & 0xff == 0 { 1 } else { original }
}

/// The row's enable predicate, answered.
///
/// # Safety
///
/// Game thread, called by the engine while the pause menu is built. The four arguments are the
/// union dispatcher's, not the target's -- the predicate itself reads none of them, it is reached
/// through a `std::function` whose `_Do_call` takes only `this` -- and they are forwarded
/// unaltered to whatever `ORIG_ROW_ENABLED` holds, which may be the next handler in the chain
/// rather than the game trampoline.
#[cfg(windows)]
unsafe extern "system" fn row_enabled_hook(a: usize, b: usize, c: usize, d: usize) -> usize {
    let orig = ORIG_ROW_ENABLED.load(Ordering::Acquire);
    if orig == 0 {
        return 0;
    }
    // SAFETY: the slot holds either the game trampoline or the next handler, both `UnionFn`.
    let original = unsafe { core::mem::transmute::<usize, er_hook::UnionFn>(orig) };
    // SAFETY: forwarding the dispatcher's own arguments, unaltered.
    let verdict = unsafe { original(a, b, c, d) };
    CALLS.fetch_add(1, Ordering::Relaxed);
    let answered = answered_verdict(verdict);
    if answered != verdict {
        FORCED.fetch_add(1, Ordering::Relaxed);
    }
    answered
}

/// Install the override, once.
///
/// Returns whether the hook is in place. A refusal leaves the row greyed, which is the shipped
/// behaviour rather than a broken one, and is logged once: the caller re-arms every tick because
/// the module may not be mapped on the first, and a line per tick would bury the log it is in.
///
/// # Safety
///
/// Game task thread. Installs one native detour through the shared union.
#[cfg(windows)]
pub unsafe fn install() -> bool {
    static INSTALLED: AtomicUsize = AtomicUsize::new(0);
    static REFUSAL_LOGGED: AtomicUsize = AtomicUsize::new(0);
    if INSTALLED.load(Ordering::SeqCst) != 0 {
        return true;
    }

    // `game_rva_for_hook` rather than `base + rva`: the address above is already a 1.17 one, and
    // the single resolve belongs to the hook API, which refuses an address this build cannot map
    // as `HOOK REFUSED` instead of detouring whatever now occupies those bytes.
    let Ok(entry) = er_game_base::mem::game_rva_for_hook(MULTIPLAYER_ROW_ENABLED_RVA) else {
        return false;
    };
    // SAFETY: a four-argument handler on the union's four-argument dispatcher, over a target that
    // reads no argument at all; `ORIG_ROW_ENABLED` is the static the handler calls through.
    // The Arxan-following entry point because this one was measured unstubbed in one process and
    // Arxan heals on its own schedule -- a stub left here on a later launch would take the detour
    // silently, which is the failure `CanUseGoods` spent a day on.
    match unsafe {
        er_hook::register_union_hook_runtime_derived_following_arxan(
            entry,
            row_enabled_hook as er_hook::UnionFn,
            &ORIG_ROW_ENABLED,
        )
    } {
        Ok(()) => {
            INSTALLED.store(1, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "multiplayer-row: enable predicate @0x{entry:x} answered -- the Escape menu's \
                 Multiplayer row opens offline, and neither the online flag nor a param byte was \
                 touched"
            ));
            true
        }
        Err(status) => {
            if REFUSAL_LOGGED.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "multiplayer-row: enable predicate @0x{entry:x} refused: {status:?} -- the row \
                     stays greyed"
                ));
            }
            false
        }
    }
}

/// `(calls, forced)`, for telemetry and tests.
#[must_use]
pub fn counters() -> (usize, usize) {
    (
        CALLS.load(Ordering::Relaxed),
        FORCED.load(Ordering::Relaxed),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The trap that cost the first live reading: the predicate's own tails leave the upper 24
    /// bits of `eax` dirty, so only the low byte is the answer.
    #[test]
    fn a_dirty_upper_byte_is_still_a_refusal() {
        for dirty in [0x10d200usize, 0x10df00, 0x10e200] {
            assert_eq!(dirty & 0xff, 0, "{dirty:#x} is a refusal in `al`");
            assert_eq!(answered_verdict(dirty), 1, "{dirty:#x} must be lifted");
        }
        assert_eq!(answered_verdict(0), 1);
    }

    /// Refusal-only: an answer the engine already allows is handed back exactly as it came,
    /// because the caller tests `al` and the engine's verdict is the better one.
    #[test]
    fn an_allowing_verdict_is_never_rewritten() {
        for allowed in [1usize, 0x10d201, 0xff, 0x1234_5678_9abc_de01] {
            assert_eq!(answered_verdict(allowed), allowed, "{allowed:#x} allows");
        }
    }

    /// The address is the one two independent methods agreed on, and it stays below the
    /// `0xafefe9` boundary that separates 1.17.0 from 1.17.1.
    #[test]
    fn the_rva_is_the_measured_one_and_below_the_1171_boundary() {
        assert_eq!(MULTIPLAYER_ROW_ENABLED_RVA, 0x913100);
        // A `const` block, because both sides are constants and `-D clippy::all` makes a runtime
        // assertion over them an error. Checking it at compile time is what the claim wanted
        // anyway: the boundary cannot stop holding between building this test and running it.
        const { assert!(MULTIPLAYER_ROW_ENABLED_RVA < 0xafefe9) };
    }

    /// This module lifts a menu grey and nothing else. The online flag is what the game reports to
    /// official matchmaking, and turning it on can get the player banned, so a write to it here
    /// would be the one change this feature must never make.
    #[test]
    fn this_module_touches_no_online_flag_and_no_param_row() {
        let source = include_str!("multiplayer_menu_row.rs");
        let code: String = source
            .split("mod tests {")
            .next()
            .expect("a tests module")
            .lines()
            .filter(|line| !line.trim_start().starts_with("//"))
            .filter(|line| !line.trim_start().starts_with("///"))
            .filter(|line| !line.trim_start().starts_with("//!"))
            .collect::<Vec<_>>()
            .join("\n");
        for forbidden in [
            "write_volatile",
            "write_unaligned",
            "as *mut u8",
            "restore_online_mode",
            "disable_offline",
            "0xbc8",
        ] {
            assert!(
                !code.contains(forbidden),
                "`{forbidden}` in the row module -- it answers one menu predicate and writes \
                 nothing"
            );
        }
    }
}
