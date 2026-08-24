//! One sweep over every loaded enemy.
//!
//! # Why applying the SpEffect is the whole feature
//!
//! `CS::ChrIns::GetTeamType` (`0x1403f1a60`, ER 1.16.2, dump == deobf == runtime at shift 0) is:
//!
//! ```text
//! *out = chrIns->teamType;
//! if (chrIns->specialEffect != NULL &&
//!     SpecialEffect::HasSpecialEffectWithStateInfo(chrIns->specialEffect, 0x84))
//!     *out = Charmed;
//! ```
//!
//! `0x84` is 132, the `stateInfo` of `[Item] Charming Branch` (20503350) and of `Bewitching
//! Branch` (503350) -- the only two rows in all 11,325 of `SpEffectParam` that carry it. So a
//! character counts as charmed for exactly as long as it holds one of those effects, with no
//! second gate: the `enableCharm` field is not consulted anywhere on that path. Putting the row on
//! a `ChrIns` therefore does precisely what the thrown item does to whatever it lands on.
//!
//! Note the game's own null check on `specialEffect`. It is load-bearing -- an unguarded read of
//! that container is a live crash this repo has already captured (`er-seamless-bugfixes-dll`'s
//! `null_special_effect` guard) -- so this sweep makes the same check before touching a character.

use eldenring::cs::{ChrIns, ChrInsExt, ChrSet, ChrType, WorldChrMan};
use fromsoftware_shared::{FromStatic, Subclass};

/// What one sweep does to the enemies it finds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SweepMode {
    /// Put the effect on every enemy that is not already under it.
    Apply,
    /// Take it off every enemy that is under it.
    Remove,
    /// Touch nothing; just count. This is what the periodic status line uses, so the log answers
    /// "did it find the enemies?" without the toggle having to be on.
    Count,
}

/// What a sweep touched, for the log line.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct SweepCounts {
    /// Loaded enemies considered.
    pub(crate) enemies: usize,
    /// Enemies that were already under the effect.
    pub(crate) already_charmed: usize,
    /// Enemies the effect was applied to this sweep.
    pub(crate) applied: usize,
    /// Enemies the effect was stripped from this sweep.
    pub(crate) removed: usize,
    /// `ApplySpEffect` calls that returned false -- the game declining the effect. Without this a
    /// declined apply is indistinguishable from one that worked and was instantly cleared: both
    /// leave the next sweep finding the same character un-charmed.
    pub(crate) apply_refused: usize,
    /// SpEffect rows held across every enemy walked. Zero here while `enemies` is not zero means
    /// the READER is wrong, not the writer -- a different failure from an apply that is refused.
    pub(crate) existing_entries: usize,
}

/// Walk every loaded enemy once, doing what `mode` says.
pub(crate) fn sweep(effect_id: i32, mode: SweepMode) -> SweepCounts {
    let mut counts = SweepCounts::default();
    let Ok(world_chr_man) = (unsafe { WorldChrMan::instance_mut() }) else {
        return counts;
    };

    // The four ChrSets that hold no enemies. `chr_sets` is indexed by a FieldInsHandle's container
    // number and is expected to hold pointers to these same inline sets, so they are excluded by
    // address rather than by index -- an index would be one more reverse-engineered constant to be
    // wrong about. The members are then ALSO collected below, because "expected to" is not
    // "verified to": if one of these sets is not reachable through `chr_sets` after all, the
    // address exclusion silently does nothing and the summons would be swept as enemies.
    let excluded_sets = [
        (&raw const world_chr_man.player_chr_set) as usize,
        (&raw const world_chr_man.ghost_chr_set) as usize,
        (&raw const world_chr_man.summon_buddy_chr_set) as usize,
        (&raw const world_chr_man.debug_chr_set) as usize,
    ];
    let mut protected: Vec<usize> = Vec::with_capacity(32);
    if let Some(player) = world_chr_man.main_player.as_ref() {
        protected.push(player.as_ptr() as usize);
    }
    collect_addresses(&world_chr_man.player_chr_set, &mut protected);
    collect_addresses(&world_chr_man.ghost_chr_set, &mut protected);
    // Spirit ashes and Torrent. They are `Npc` like any enemy, so nothing else would exclude them.
    collect_addresses(&world_chr_man.summon_buddy_chr_set, &mut protected);
    collect_addresses(&world_chr_man.debug_chr_set, &mut protected);

    let mut chr_sets: Vec<usize> = Vec::with_capacity(16);
    chr_sets.push((&raw const world_chr_man.open_field_chr_set.base) as usize);
    for chr_set in world_chr_man.chr_sets.iter().flatten() {
        let address = chr_set.as_ptr() as usize;
        if excluded_sets.contains(&address) || chr_sets.contains(&address) {
            continue;
        }
        chr_sets.push(address);
    }

    for address in chr_sets {
        let chr_set = unsafe { &*(address as *const ChrSet<ChrIns>) };
        for chr_ins in chr_set.characters() {
            if !is_sweepable_enemy(chr_ins, &protected) {
                continue;
            }
            counts.enemies += 1;
            let mut charmed = false;
            for entry in chr_ins.special_effect.entries() {
                counts.existing_entries += 1;
                charmed |= entry.param_id == effect_id;
            }
            if charmed {
                counts.already_charmed += 1;
            }
            match mode {
                SweepMode::Apply if !charmed => {
                    // `dont_sync` MUST be false. It is not a preference: with it true,
                    // `CS::ChrIns::ApplySpEffect` (`0x1403e8be0` -> `0x1403e8c90`) refuses outright
                    // for any target that is not the main player and not in the debug ChrSet --
                    //
                    //     else {                                   // shouldNotSync != 0
                    //       if (chr->isChrEventIdLessThan9998()) return false;
                    //       if (!IsMainPlayerIns(chr) && !IsChrInDebugChrSet(chr)) return false;
                    //     }
                    //
                    // -- so every enemy is rejected and the sweep re-applies the same 262
                    // characters every frame forever. Measured before the fix: `262 refused` out of
                    // 262, for both charm rows. With it false the call takes the branch that
                    // returns true for an ordinary local NPC, and the effect sticks.
                    const DONT_SYNC: bool = false;
                    chr_ins.apply_speffect(effect_id, DONT_SYNC);
                    counts.applied += 1;
                    // The trait method drops the native `bool`, so re-read the container instead:
                    // if the row is not there immediately after the call, the game refused it.
                    if !chr_ins
                        .special_effect
                        .entries()
                        .any(|entry| entry.param_id == effect_id)
                    {
                        counts.apply_refused += 1;
                    }
                }
                SweepMode::Remove if charmed => {
                    chr_ins.remove_speffect(effect_id);
                    counts.removed += 1;
                }
                SweepMode::Apply | SweepMode::Remove | SweepMode::Count => {}
            }
        }
    }
    counts
}

/// Record every character in a ChrSet, by the address of its `ChrIns` base.
fn collect_addresses<T: Subclass<ChrIns> + 'static>(chr_set: &ChrSet<T>, out: &mut Vec<usize>) {
    for character in chr_set.characters() {
        out.push((&raw const *character.superclass()) as usize);
    }
}

/// Is this character an enemy this sweep may touch?
fn is_sweepable_enemy(chr_ins: &ChrIns, protected: &[usize]) -> bool {
    if protected.contains(&((&raw const *chr_ins) as usize)) {
        return false;
    }
    // Everything the game spawns from a map -- enemies, bosses, friendly NPCs -- is `Npc`. Players
    // and every phantom/ghost kind are their own `ChrType`, so this holds the sweep to characters
    // even after a co-op session puts other players into a set that is not `player_chr_set`.
    if chr_ins.chr_type != ChrType::Npc {
        return false;
    }
    // `special_effect` is typed non-null but the game leaves it null on a character that is still
    // being constructed, and the read below is the exact one that faulted in the captured crash.
    // Read the field as a raw pointer so the null case is a skip rather than a dereference.
    let special_effect = unsafe { *((&raw const chr_ins.special_effect).cast::<usize>()) };
    special_effect != 0
}
