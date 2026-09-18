//! The region flag that makes a zone non-pvp, answered once for the whole engine.
//!
//! # Why this exists, and why the narrower shape failed
//!
//! `CS::IsBreakInLimitedByEventFlagId` (1.17.1 `0x140a61980`) is the engine's answer to "may anyone
//! break into this region". Vanilla flags whole zones off; Seamless makes them playable, so under
//! Seamless the flag is wrong rather than protective.
//!
//! [`crate::can_use_goods_gate`] first tried to route around it by asking `CanUseBreakInItem`'s
//! other terms itself and never asking this one. That opens the item and nothing else, and the
//! engine asks the same question again while the invasion runs:
//!
//! ```text
//! FUN_1405afaa0 (1.17.1, the function that raises the cancel message)
//!   line  67   cVar1 = CanStartBreakIn(GLOBAL_WorldChrMan)     <- asks this predicate
//!   line 106   if (cVar1 != 0) goto LAB_1405aff29              <- allowed, no cancel
//!   line 117   FUN_140763a70(local_60, 2200000)                <- refused, look up the message
//! ```
//!
//! Measured live on run `br-20260918-185924-8a9e`: the player raised the item in region `1400000`,
//! started an invasion, and the game cancelled it with network message id `2200200`, "Invasion
//! canceled". The capture came from `scripts/frida/who-shows-network-message.js`, and its stack --
//! `eldenring.exe+0xa05461`, `+0x5afe9a`, `+0x591780`, `+0x58f27e`, `+0x62599d` -- carries no
//! `ersc.dll` frame and none of ours, so the decision is the game's own. The same player, with this
//! predicate forced from Frida, invaded with no cancel at all.
//!
//! So the flag has to read "allowed" to the engine, not only to our gate.
//!
//! # Why owning this predicate is safe
//!
//! It has three callers on the installed build, found by scanning `eldenring-deobf-1.17.1.bin` for
//! calls that land on `0xa61980`, and every one of them is break-in:
//!
//! | call site | owner | what it decides |
//! |---|---|---|
//! | `0x140657dc0` | `CanUseBreakInItem` | may this player raise an invasion item |
//! | `0x14050a99b` | `CanStartBreakIn` | may a break-in run in this world right now |
//! | `0x140a04c25` | `FUN_140a04ba0` | which regions go into the invadable-region pool |
//!
//! The third was read rather than assumed: it walks a list of region ids, asks this predicate about
//! each, and appends the ones that pass to an output list, bounded by a count at `+0x7c`. Allowing
//! every region there widens that pool, which is the same intent as the other two and not a side
//! effect on unrelated code.
//!
//! # What it deliberately does not do
//!
//! It only turns a refusal into a permission. A region the engine already allows is left alone, so
//! this can never narrow anything, and every caller that was going to be told "yes" still is.

use core::sync::atomic::{AtomicUsize, Ordering};

/// `CS::IsBreakInLimitedByEventFlagId` on 1.17.1.
///
/// Read out of the image rather than mapped: `CanUseBreakInItem` computes the argument with
/// `lea rcx,[rbx+0x6e8]` and calls it at `0x140657dc0`, and `CanStartBreakIn` calls the same
/// address at `0x14050a99b` with a stack copy of `FieldArea->playRegionParamId`. Below the
/// `0xafefe9` boundary, so 1.17.0 and 1.17.1 agree.
///
/// Its sense is inverted against its name: both callers refuse when it returns false, so true means
/// "breaking in is permitted in this region".
const IS_BREAK_IN_LIMITED_BY_EVENT_FLAG_ID_RVA: u32 = 0xa6_1980;

/// Trampoline to the original predicate, or the next handler in the union chain.
static ORIG: AtomicUsize = AtomicUsize::new(0);

/// How many refusals became permissions, and how many answers were already permissions.
///
/// Counted apart so one run can tell "the hook never ran" from "the hook ran and had nothing to
/// do". A single number cannot, and that ambiguity is what made an earlier zero reading
/// uninterpretable.
static ALLOWED_A_REFUSED_REGION: AtomicUsize = AtomicUsize::new(0);
static LEFT_ALONE: AtomicUsize = AtomicUsize::new(0);

/// The region id the first lifted refusal was about, plus one, so zero still means "none yet".
///
/// A run that lifted a refusal in a region vanilla already permits proves nothing, and the region
/// id is the whole difference. `1400000` was measured refusing and `1400011` permitting.
static FIRST_LIFTED_REGION_PLUS_ONE: AtomicUsize = AtomicUsize::new(0);

/// `bool IsBreakInLimitedByEventFlagId(u32 *regionId)`.
///
/// # Safety
///
/// Game thread, called by the engine. The union dispatcher is four-argument; this target reads only
/// `rcx`, so the other three are forwarded untouched and never examined by the callee.
#[cfg(windows)]
unsafe extern "system" fn hook(region_id: usize, b: usize, c: usize, d: usize) -> usize {
    let orig = ORIG.load(Ordering::Acquire);
    if orig == 0 {
        return 0;
    }
    // SAFETY: the slot holds either the game trampoline or the next handler, both `UnionFn`.
    let original = unsafe { core::mem::transmute::<usize, er_hook::UnionFn>(orig) };
    // SAFETY: forwarding the engine's own arguments, unaltered.
    let verdict = unsafe { original(region_id, b, c, d) };

    // The engine returns `bool` in `al` and does not clear the upper bits of `eax`, so the full
    // register is not a truth value. Masking is not cosmetic here: unmasked, a refusal reads as a
    // large non-zero number and every comparison against zero silently never matches. That exact
    // mistake cost the first reading of the multiplayer-row predicate.
    if verdict & 0xff != 0 {
        LEFT_ALONE.fetch_add(1, Ordering::Relaxed);
        return verdict;
    }

    if ALLOWED_A_REFUSED_REGION.fetch_add(1, Ordering::Relaxed) == 0 {
        // SAFETY: fault-tolerant read of the `u32` the engine passed a pointer to.
        let region = unsafe { er_game_base::mem::safe_read_i32(region_id) };
        FIRST_LIFTED_REGION_PLUS_ONE.store(
            region.map_or(0, |value| value as u32 as usize + 1),
            Ordering::Relaxed,
        );
        crate::standalone_log(format_args!(
            "break-in region: allowed a region the game refuses ({region:?}). Vanilla flags whole \
             zones non-pvp and Seamless makes them playable, so the flag is answered for every \
             caller -- the item, the running invasion, and the invadable-region pool -- rather \
             than for the item alone, which left the game cancelling what the player started."
        ));
    }
    1
}

/// Install the region override, once.
///
/// Returns whether the hook is in place. A refusal leaves every region as vanilla flags it, which
/// greys the invasion items again in those zones -- the shipped behaviour, not a broken one, and
/// the honest failure mode: an item that cannot be raised is better than one that raises an
/// invasion the engine then cancels.
///
/// # Safety
///
/// Game task thread. Installs one native detour through the shared union.
#[cfg(windows)]
pub unsafe fn install() -> bool {
    static INSTALLED: AtomicUsize = AtomicUsize::new(0);
    if INSTALLED.load(Ordering::SeqCst) != 0 {
        return true;
    }
    let Ok(entry) = er_game_base::mem::game_rva_for_hook(IS_BREAK_IN_LIMITED_BY_EVENT_FLAG_ID_RVA)
    else {
        return false;
    };
    // SAFETY: a four-argument dispatcher on a one-argument target, which is the established shape
    // here: the callee reads `rcx` and ignores the rest. `ORIG` is the static the handler calls
    // through `UnionFn`.
    match unsafe {
        er_hook::register_union_hook_runtime_derived(entry, hook as er_hook::UnionFn, &ORIG)
    } {
        Ok(()) => {
            INSTALLED.store(1, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "break-in region: IsBreakInLimitedByEventFlagId @0x{entry:x} answered. A zone \
                 vanilla flags non-pvp no longer refuses the item, the running invasion, or the \
                 region pool."
            ));
            true
        }
        Err(status) => {
            crate::standalone_log(format_args!(
                "break-in region: IsBreakInLimitedByEventFlagId @0x{entry:x} refused: {status:?} \
                 -- zones vanilla flags non-pvp stay non-pvp and the items stay greyed there"
            ));
            false
        }
    }
}

/// `(refusals lifted, answers left alone, first lifted region)`, for telemetry and tests.
#[must_use]
pub fn counters() -> (usize, usize, Option<u32>) {
    let first = FIRST_LIFTED_REGION_PLUS_ONE.load(Ordering::Relaxed);
    (
        ALLOWED_A_REFUSED_REGION.load(Ordering::Relaxed),
        LEFT_ALONE.load(Ordering::Relaxed),
        (first != 0).then(|| (first - 1) as u32),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_address_is_the_one_both_break_in_callers_reach() {
        // `CanUseBreakInItem` calls this at `0x140657dc0` and `CanStartBreakIn` at `0x14050a99b`,
        // both read out of `eldenring-deobf-1.17.1.bin`.
        assert_eq!(IS_BREAK_IN_LIMITED_BY_EVENT_FLAG_ID_RVA, 0xa6_1980);
    }

    /// Only a refusal may be overridden, asserted against the source so it cannot regress quietly.
    ///
    /// Answering a permission would be indistinguishable in a log from answering a refusal, and it
    /// would mean this module decides a question it only meant to stop blocking.
    #[test]
    fn a_permission_is_never_touched() {
        let source = include_str!("break_in_region_gate.rs");
        let code: String = source
            .split("mod tests {")
            .next()
            .expect("a tests module")
            .lines()
            .filter(|line| !line.trim_start().starts_with("//"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(
            code.contains("if verdict & 0xff != 0 {"),
            "the early return for an allowed region is gone"
        );
        // The mask is the whole reason the comparison works; without it every answer reads as
        // allowed and the module silently does nothing.
        assert!(code.contains("verdict & 0xff"), "the `al` mask is gone");
    }

    #[test]
    fn the_first_lifted_region_is_reportable() {
        // Zero means "nothing lifted yet", which is why the region is stored plus one.
        assert_eq!(counters().2, None);
    }
}
