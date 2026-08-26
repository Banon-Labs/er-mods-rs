//! The patch registry.
//!
//! A *patch* rewrites one byte of the game's own code. It exists for the crashes a detour cannot
//! reach: a fault raised by an instruction in the middle of a function, where intercepting the
//! function's entry would mean reimplementing everything that function does.
//!
//! This is a heavier tool than a [`guard`](crate::guards), and the bar is correspondingly higher.
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
//!    against `eldenring-deobf.bin` there, then re-checked against the live image before anything
//!    is written.
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
    /// 1.16.2 RVA of the verified window's FIRST byte, which is not necessarily the byte written.
    pub(crate) rva: usize,
    /// Bytes the window must hold before anything is written. Generated and ground-truthed by
    /// `build.rs`; a mismatch abandons this patch and logs the bytes actually found.
    pub(crate) expected_window: &'static [u8],
    /// Index into the window of the single byte this patch rewrites. `expected_window[offset]` is
    /// therefore the value being replaced, which is why no separate "from" field exists.
    pub(crate) offset: usize,
    /// What that byte becomes.
    pub(crate) replacement: u8,
    /// Set once the byte has been written AND read back as [`Self::replacement`]. See the module
    /// docs for why this is weaker evidence than a guard's counter.
    pub(crate) applied: &'static AtomicBool,
    /// Why removing the instruction is safe, printed at install time so a reader of the log does
    /// not have to open this source to judge it.
    pub(crate) rationale: &'static str,
}

impl Patch {
    /// Absolute address of the byte this patch rewrites.
    pub(crate) fn target(&self, base: usize) -> usize {
        base + self.rva + self.offset
    }

    /// The byte being replaced, read out of the window so the two can never disagree.
    pub(crate) fn replaced(&self) -> Option<u8> {
        self.expected_window.get(self.offset).copied()
    }
}

/// Every patch this DLL applies.
pub(crate) static REGISTRY: &[Patch] = &[freelist_shutdown_assert::FREELIST_SHUTDOWN_ASSERT];

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
            assert!(patch.rva != 0);
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
    /// patch would get wrong. A base of zero would make this pass on the identity, so it uses the
    /// real preferred image base.
    #[test]
    fn target_is_the_window_start_plus_the_offset() {
        const PREFERRED_IMAGE_BASE: usize = 0x1_4000_0000;
        for patch in REGISTRY {
            assert_eq!(
                patch.target(PREFERRED_IMAGE_BASE),
                PREFERRED_IMAGE_BASE + patch.rva + patch.offset,
                "{}: target must name the byte inside the window, not the window",
                patch.name
            );
            assert!(
                patch.target(PREFERRED_IMAGE_BASE) >= PREFERRED_IMAGE_BASE + patch.rva,
                "{}: target fell before its own window",
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
            rva: 0x1000,
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
            let end = patch.rva + patch.expected_window.len();
            for other in &REGISTRY[index + 1..] {
                let other_end = other.rva + other.expected_window.len();
                assert!(
                    end <= other.rva || other_end <= patch.rva,
                    "patch '{}' [0x{:x}..0x{end:x}) overlaps patch '{}' [0x{:x}..0x{other_end:x})",
                    patch.name,
                    patch.rva,
                    other.name,
                    other.rva,
                );
            }
        }
    }

    /// A patch inside a guarded function's prologue would fight MinHook's jump for the same bytes.
    #[test]
    fn no_patch_window_overlaps_a_guard_prologue() {
        for patch in REGISTRY {
            let patch_end = patch.rva + patch.expected_window.len();
            for guard in crate::guards::REGISTRY {
                let guard_end = guard.rva + guard.expected_prologue.len();
                assert!(
                    patch_end <= guard.rva || guard_end <= patch.rva,
                    "patch '{}' [0x{:x}..0x{patch_end:x}) overlaps guard '{}' \
                     [0x{:x}..0x{guard_end:x}); MinHook's jump and the patch would fight",
                    patch.name,
                    patch.rva,
                    guard.name,
                    guard.rva,
                );
            }
        }
    }
}
