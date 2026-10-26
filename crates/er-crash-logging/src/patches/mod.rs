//! The patch registry.
//!
//! A *patch* rewrites one byte of the game's own code. It exists for the crashes a detour cannot
//! reach: a fault raised by an instruction in the middle of a function, where intercepting the
//! function's entry would mean reimplementing everything that function does.
//!
//! This is a heavier tool than a detour guard (see `er-seamless-bugfixes/src/guards/`), and the bar
//! is correspondingly higher.
//!
//! # Rules a new patch must satisfy
//!
//! 1. **The patched-out instruction must be provably optional.** Not "probably harmless" -- the
//!    surrounding code has to already contain a path that skips it, and the window must contain
//!    the evidence. [`freelist_shutdown_assert`] patches an `INT3` whose own `JZ` jumps exactly one
//!    byte past it: both paths converge on the next instruction, so removing the break produces
//!    the byte-identical behaviour the game already has whenever the assertion holds.
//! 2. **Pin the address by a window, not by the byte.** A lone `0xcc` occurs everywhere. The window
//!    is generated from named `iced-x86` instructions by this crate's `build.rs` and ground-truthed
//!    against `eldenring-deobf-1.17.2.bin` there, then re-checked against the live image before
//!    anything is written.
//! 3. **Write one byte, not the window.** The verified window is live code other threads may be
//!    executing. Rewriting bytes that were already correct widens that race for no gain, so
//!    [`Patch::offset`] names the single byte that changes.
//! 4. **Read it back.** A successful `VirtualProtect` is not proof the byte landed; another mod can
//!    own the same address. The install path reads the byte after writing it and reports what it
//!    found there.
//!
//! # What a patch cannot tell you
//!
//! A guard carries a block counter, and a nonzero count is a fault the game would otherwise have
//! taken. A patch has no such counter: the instruction it removes never runs, so nothing can count
//! it. [`Patch::applied`] says the byte changed, and that is all it says. Evidence that a patch
//! fixed anything has to come from outside the process -- for [`freelist_shutdown_assert`], from a
//! quit that leaves no crash record where one was previously written every time.

use core::sync::atomic::AtomicBool;

pub(crate) mod freelist_shutdown_assert;

/// One rewritten instruction byte.
pub(crate) struct Patch {
    /// Name used in the install log.
    pub(crate) name: &'static str,
    /// 2.7.2.0 RVA of the `.pdata` function entry that contains the window.
    ///
    /// The window's own address is not stored, and that is the point. A patch site is by nature
    /// mid-function -- if the interesting instruction were the first one, this would be a guard --
    /// and a mid-function address must never look like a function entry to anything that keys on
    /// one. The function is checked against the running build; the offset rides along in Rust.
    /// See [`er_game_base::game_build::resolve_call_site_rva`], which exists for exactly this and
    /// spells out why the offset must never enter a table.
    pub(crate) function_rva: usize,
    /// Byte offset of the verified window's first byte within [`Self::function_rva`]. Not the byte
    /// written -- [`Self::offset`] indexes that within the window.
    pub(crate) offset_in_function: usize,
    /// Bytes the window must hold before anything is written. Generated and ground-truthed by
    /// `build.rs`; a mismatch abandons this patch and logs the bytes actually found.
    pub(crate) expected_window: &'static [u8],
    /// Index into the window of the single byte this patch rewrites. `expected_window[offset]` is
    /// therefore the value being replaced, which is why no separate "from" field exists.
    pub(crate) offset: usize,
    /// What that byte becomes.
    pub(crate) replacement: u8,
    /// Set once the byte has been written and read back as [`Self::replacement`]. See the module
    /// docs for why this is weaker evidence than a guard's counter.
    pub(crate) applied: &'static AtomicBool,
    /// Why removing the instruction is safe, printed at install time so a reader of the log does
    /// not have to open this source to judge it.
    pub(crate) rationale: &'static str,
}

impl Patch {
    /// 2.7.2.0 RVA of the verified window's first byte.
    ///
    /// Derived from the two stored halves rather than kept beside them, so no third field can
    /// drift out of agreement with the address the window was generated at. Used for the
    /// bookkeeping this file does -- overlap checks, the install log -- and never as the address
    /// to check: the install path checks [`Self::function_rva`] and adds
    /// [`Self::offset_in_function`] to the answer.
    pub(crate) const fn rva(&self) -> usize {
        self.function_rva + self.offset_in_function
    }

    /// Absolute address of the byte this patch rewrites, given the window's address on the
    /// running build.
    ///
    /// It takes the checked window rather than the module base on purpose: the window is the
    /// address the install path just verified, and deriving the byte from anything else would let
    /// the check and the write disagree about which byte they mean. One input, one answer.
    pub(crate) fn target(&self, window: usize) -> usize {
        window + self.offset
    }

    /// The byte being replaced, read out of the window so the two can never disagree.
    pub(crate) fn replaced(&self) -> Option<u8> {
        self.expected_window.get(self.offset).copied()
    }
}

/// Every patch this DLL applies.
pub(crate) static REGISTRY: &[Patch] = &[freelist_shutdown_assert::FREELIST_SHUTDOWN_ASSERT];

/// Where the install path writes its lines.
#[cfg(windows)]
pub(crate) type LogFn = fn(core::fmt::Arguments<'_>);

/// What happened to one patch.
///
/// Three facts, not two. `byte mismatch` describes a signature that went stale on an address that
/// is still right; a wrong build is a different fault with a different fix, and on 2026-08-30 four
/// rows in `er-seamless-bugfixes` reported the first when the cause was the second.
#[cfg(windows)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Outcome {
    /// Byte written and read back.
    Patched,
    /// No address on the running build. Nothing was read and nothing was written.
    Refused,
    /// The address resolved, and what is at it is not what this row was verified against -- or
    /// the write itself failed. Either way the game is exactly as it was.
    Disarmed,
}

/// Apply every patch in the registry against the game image at `base`.
///
/// A patch that half-applied would leave a rewritten instruction behind, so the read-back is
/// reported whether it succeeds or not.
#[cfg(windows)]
pub(crate) fn install(base: usize, log: LogFn) {
    log(format_args!(
        "patch attach: {} code patch(es); game base 0x{base:x}. Each patch removes one \
         instruction the game's own control flow already skips, and writes exactly one byte to \
         do it.",
        REGISTRY.len()
    ));
    let (mut applied, mut refused, mut disarmed) = (0_usize, 0_usize, 0_usize);
    for patch in REGISTRY {
        match apply(patch, base, log) {
            Outcome::Patched => applied += 1,
            Outcome::Refused => refused += 1,
            Outcome::Disarmed => disarmed += 1,
        }
    }
    log(format_args!(
        "patch complete: {applied}/{} patch(es) PATCHED, {refused} REFUSED (wrong build), \
         {disarmed} DISARMED (address checked, bytes there are not the verified ones)",
        REGISTRY.len()
    ));
}

/// Confirm the live bytes at `address` still match the window this patch was verified against.
///
/// `address` has already been checked against the running build, so a mismatch here is evidence
/// about the bytes and not about the build. The line names the bytes found so the reader does not
/// have to take the verdict on trust.
#[cfg(windows)]
fn window_matches(patch: &Patch, address: usize, log: LogFn) -> bool {
    let expected = patch.expected_window;
    let mut actual = [0_u8; 32];
    let Some(window) = actual.get_mut(..expected.len()) else {
        log(format_args!(
            "DISARMED {} @0x{address:x}: expected window is {} bytes, longer than the {} the \
             checker can read",
            patch.name,
            expected.len(),
            actual.len(),
        ));
        return false;
    };
    if !unsafe { er_game_base::mem::read_bytes(address, window) } {
        log(format_args!(
            "DISARMED {} @0x{address:x}: window unreadable",
            patch.name
        ));
        return false;
    }
    if window != expected {
        log(format_args!(
            "DISARMED {} @0x{address:x}: byte mismatch (got {window:02x?}, want {expected:02x?}) \
             -- the running build is 2.7.2.0, the build this window was measured on, so the \
             likeliest cause is that another mod rewrote these bytes; the other is that the \
             constant is wrong. Not installing.",
            patch.name,
        ));
        return false;
    }
    true
}

/// Resolve the window, verify it, write the one byte, and read it back.
#[cfg(windows)]
fn apply(patch: &Patch, base: usize, log: LogFn) -> Outcome {
    // `resolve_call_site_rva`, not `resolve_detour_address`. The detour resolver answers "may
    // MinHook overwrite five bytes at this function entry", and this address is not an entry and
    // is not being detoured: it is a single byte inside a function, read and then written. The
    // function is checked against the running build and the offset is added afterwards, so the
    // mid-function address never enters a table that could read it as a licence to detour.
    let Some(window) = er_game_base::game_build::resolve_call_site_rva(
        patch.function_rva,
        patch.offset_in_function,
        patch.name,
    )
    .map(|rva| base + rva) else {
        log(format_args!(
            "REFUSED {} (2.7.2.0 window rva 0x{:x} = fn 0x{:x} + 0x{:x}): the running build is \
             not the one this window was measured on -- {}. Nothing read, nothing written. This \
             is a build refusal, not a stale signature.",
            patch.name,
            patch.rva(),
            patch.function_rva,
            patch.offset_in_function,
            er_game_base::game_build::describe_build()
        ));
        return Outcome::Refused;
    };
    if !window_matches(patch, window, log) {
        return Outcome::Disarmed;
    }
    let target = patch.target(window);
    let Some(replaced) = patch.replaced() else {
        log(format_args!(
            "DISARMED {} @0x{target:x}: offset {} is outside its own {}-byte window",
            patch.name,
            patch.offset,
            patch.expected_window.len(),
        ));
        return Outcome::Disarmed;
    };
    // SAFETY: `target` is inside the game image -- `patch.target(window)` is an offset into the
    // window `window_matches` just verified byte-for-byte against this build, at the address the
    // resolver gave for this build, so it is mapped, executable, and holds the instruction this
    // patch was written for. Both a refusal and a mismatch have already returned above.
    if !unsafe { er_hook::write_code_byte(target, patch.replacement) } {
        log(format_args!(
            "DISARMED {} @0x{target:x}: VirtualProtect refused the write",
            patch.name
        ));
        return Outcome::Disarmed;
    }
    // A successful `VirtualProtect` says the write was permitted, not that the byte is still what
    // was written. Read it back: this is the only proof a patch can offer that it changed anything.
    let mut readback = [0_u8; 1];
    if !unsafe { er_game_base::mem::read_bytes(target, &mut readback) } {
        log(format_args!(
            "DISARMED {} @0x{target:x}: wrote 0x{:02x} but could not read the byte back, so \
             whether it landed is unknown",
            patch.name, patch.replacement,
        ));
        return Outcome::Disarmed;
    }
    if readback[0] != patch.replacement {
        log(format_args!(
            "DISARMED {} @0x{target:x}: wrote 0x{:02x} but found 0x{:02x} -- something else owns \
             this address",
            patch.name, patch.replacement, readback[0],
        ));
        return Outcome::Disarmed;
    }
    patch
        .applied
        .store(true, core::sync::atomic::Ordering::SeqCst);
    log(format_args!(
        "PATCHED {} @0x{target:x} (2.7.2.0 rva 0x{:x}): 0x{replaced:02x} -> 0x{:02x}, read back. {}",
        patch.name,
        patch.rva() + patch.offset,
        patch.replacement,
        patch.rationale
    ));
    Outcome::Patched
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::sync::atomic::Ordering;

    #[test]
    fn every_patch_is_documented_and_pinned() {
        for patch in REGISTRY {
            assert!(!patch.name.is_empty());
            assert!(
                !patch.rationale.is_empty(),
                "{}: a patch must say why removing the instruction is safe",
                patch.name
            );
            assert!(patch.rva() != 0);
            assert!(
                !patch.expected_window.is_empty(),
                "{}: a patch must pin its address by a window, not by the byte",
                patch.name
            );
            assert!(
                patch.offset < patch.expected_window.len(),
                "{}: offset {} is outside the {}-byte window it indexes",
                patch.name,
                patch.offset,
                patch.expected_window.len()
            );
        }
    }

    #[test]
    fn every_patch_actually_changes_its_byte() {
        for patch in REGISTRY {
            assert_ne!(
                patch.replaced(),
                Some(patch.replacement),
                "{}: the replacement equals the byte already there, so this patch is a no-op that \
                 would still report itself applied",
                patch.name
            );
        }
    }

    #[test]
    fn patches_start_unapplied() {
        for patch in REGISTRY {
            assert!(
                !patch.applied.load(Ordering::Relaxed),
                "{}: applied must start false so a true value means the byte really changed",
                patch.name
            );
        }
    }

    /// The address actually written is derived, not stored, so the derivation is what a wrong
    /// patch would get wrong. A window of zero would make this pass on the identity, so it uses
    /// the real preferred image base plus the RVA.
    #[test]
    fn target_is_the_window_start_plus_the_offset() {
        const PREFERRED_IMAGE_BASE: usize = 0x1_4000_0000;
        for patch in REGISTRY {
            let window = PREFERRED_IMAGE_BASE + patch.rva();
            assert_eq!(
                patch.target(window),
                window + patch.offset,
                "{}: target must name the byte inside the window, not the window",
                patch.name
            );
            assert!(
                patch.target(window) >= window,
                "{}: target fell before its own window",
                patch.name
            );
        }
    }

    /// `target` must follow the window it was handed, not recompute one from the preferred base.
    ///
    /// `apply_patch` verifies the bytes at the window it was given and then writes; if `target`
    /// derived the byte from anything else, the two could name different bytes -- verified in one
    /// place, written in another. A window away from the preferred base (the game loads wherever
    /// ASLR puts it) is modelled here so the assertion cannot pass by accident.
    #[test]
    fn target_follows_the_window_it_was_handed() {
        const PREFERRED_IMAGE_BASE: usize = 0x1_4000_0000;
        const RELOCATED_BY: usize = 0x7ff6_0000_0000;
        for patch in REGISTRY {
            let relocated = PREFERRED_IMAGE_BASE + patch.rva() + RELOCATED_BY;
            assert_eq!(
                patch.target(relocated),
                relocated + patch.offset,
                "{}: target must be relative to the window it was handed",
                patch.name
            );
            assert_ne!(
                patch.target(relocated),
                PREFERRED_IMAGE_BASE + patch.rva() + patch.offset,
                "{}: target ignored the window and answered with the preferred-base address",
                patch.name
            );
        }
    }

    /// `apply_patch` refuses a patch whose `replaced()` is `None`, and that refusal is the only
    /// thing standing between an out-of-range offset and a write past the verified window. The
    /// registry can never produce one (`every_patch_is_documented_and_pinned` forbids it), so the
    /// branch is proven on a constructed row instead of left unexercised.
    #[test]
    fn an_offset_past_the_window_has_no_byte_to_replace() {
        static NEVER_APPLIED: AtomicBool = AtomicBool::new(false);
        let window: &'static [u8] = &[0x90, 0x90];
        let out_of_range = Patch {
            name: "test row, never registered",
            function_rva: 0x1000,
            offset_in_function: 0,
            expected_window: window,
            offset: window.len(),
            replacement: 0xcc,
            applied: &NEVER_APPLIED,
            rationale: "constructed for the refusal path; not a real patch",
        };
        assert_eq!(
            out_of_range.replaced(),
            None,
            "an offset outside the window must report no byte, so the install path refuses"
        );
    }

    /// One byte changes and the rest of the verified window stays exactly as it was. This is rule
    /// 3 made executable: writing the whole window would rewrite bytes that were already correct,
    /// widening the race against threads executing this code right now.
    #[test]
    fn applying_a_patch_changes_exactly_one_byte_of_the_window() {
        for patch in REGISTRY {
            let mut patched = patch.expected_window.to_vec();
            patched[patch.offset] = patch.replacement;
            let differing = patch
                .expected_window
                .iter()
                .zip(&patched)
                .filter(|(before, after)| before != after)
                .count();
            assert_eq!(
                differing, 1,
                "{}: a patch must move exactly one byte of its window",
                patch.name
            );
            assert_eq!(
                patched.len(),
                patch.expected_window.len(),
                "{}: the window must not change length, or every instruction after the patch                  moves",
                patch.name
            );
        }
    }

    /// Two patches sharing a window would each verify bytes the other had already rewritten, so
    /// whichever installed second would disarm on a mismatch it caused itself.
    #[test]
    fn no_two_patches_share_a_window() {
        for (index, patch) in REGISTRY.iter().enumerate() {
            let end = patch.rva() + patch.expected_window.len();
            for other in &REGISTRY[index + 1..] {
                let other_end = other.rva() + other.expected_window.len();
                assert!(
                    end <= other.rva() || other_end <= patch.rva(),
                    "patch '{}' [0x{:x}..0x{end:x}) overlaps patch '{}' [0x{:x}..0x{other_end:x})",
                    patch.name,
                    patch.rva(),
                    other.name,
                    other.rva(),
                );
            }
        }
    }
}
