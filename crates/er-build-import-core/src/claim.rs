//! Giving each copy a build lists an inventory entry of its own.
//!
//! # A build names copies, an inventory holds entries
//!
//! The two are not the same thing and the difference is invisible until a build names one item
//! more than once. Build `b36964c2314bc5` lists 315 armaments over 36 ids -- seventy-five Swords
//! of Night, forty-five Star Fists, twenty-seven Raptor Talons -- and the grant pass mints one
//! inventory entry per listing, in the build's order.
//!
//! Until 2026-09-22 the reorder pass resolved every listing to the lowest index holding that
//! [`identity`], so a lookup answered with the same entry seventy-five times. That entry was
//! stamped seventy-five times, the other seventy-four copies were never touched, and the order
//! check read one entry seventy-five times and saw a value that had not increased. The pass
//! therefore could not report the build in order, which is the one condition that stops it
//! writing, and what it wrote then scattered an inventory the grant had just put in order.
//!
//! Claiming is the fix and it is one bit of state: an entry a grant has been given is taken, and
//! the next listing of the same item gets the next entry.
//!
//! # Two keys, deliberately
//!
//! [`identity`] folds an armament's affinity and upgrade level away, which is the right answer to
//! "would a player call these the same item" and the wrong one to "which of this character's
//! entries should this grant be given". Build `b36964c2314bc5` carries the counterexample:
//! `0x01502791` and `0x01502921` are Raptor Talons Keen +25 and Lightning +25, the only two of its
//! 36 armament ids that share an identity, and resolving both to one entry is what made the pass
//! report
//! `the 165 item(s) that could be read back are NOT in the build's order`
//! over an inventory that was in the build's order.
//!
//! So the identity groups the candidates and [`affinity_key`] chooses among them.

use std::collections::BTreeMap;

use crate::plan::{Grant, split_armament_id};
use crate::sweep::{Category, identity};

/// An armament id with its upgrade level taken off; anything else whole.
///
/// The key that tells a Keen Raptor Talons from a Lightning one while still matching the grant
/// that names it, because a grant carries no level -- the grant pass computes the level separately
/// and mints at `armament_item_id(grant.item_id, level)`. So a character holding a Bone Bow at +25
/// holds item `40500025` while the grant that names it says `40500000`, and the two agree only
/// once the level is off.
///
/// ```
/// use er_build_import_core::claim::affinity_key;
/// // A Bone Bow +25 and the grant that names it.
/// assert_eq!(affinity_key(40_500_025), 40_500_000);
/// // Keen and Lightning Raptor Talons keep their own keys, unlike under `identity`.
/// assert_ne!(affinity_key(0x0150_2791), affinity_key(0x0150_2921));
/// ```
#[must_use]
pub fn affinity_key(item_id: u32) -> u32 {
    if Category::of(item_id) == Category::Armament {
        split_armament_id(item_id).row_with_affinity
    } else {
        item_id
    }
}

/// One inventory entry, and whether a grant has already been given it.
#[derive(Debug, Clone, Copy)]
struct Claim {
    /// Index at the moment of the walk.
    index: i32,
    /// The entry's id without its upgrade level. See [`affinity_key`].
    affinity: u32,
    /// Whether a grant has claimed this entry.
    taken: bool,
}

/// Every inventory entry a grant could be given, grouped by [`identity`], ascending by index.
///
/// See the module header for what claiming replaced and why. Built from `(index, item_id)` pairs
/// so the rules hold without a game attached; the runtime's reorder pass fills it from one walk of
/// the carried inventory.
#[derive(Debug, Default)]
pub struct Claims {
    /// Candidate entries for each identity, ascending by index.
    by_identity: BTreeMap<u32, Vec<Claim>>,
}

impl Claims {
    /// Take the entries in any order; they are sorted by index here.
    #[must_use]
    pub fn from_entries(entries: impl IntoIterator<Item = (i32, u32)>) -> Self {
        let mut by_identity: BTreeMap<u32, Vec<Claim>> = BTreeMap::new();
        for (index, item_id) in entries {
            by_identity
                .entry(identity(item_id))
                .or_default()
                .push(Claim {
                    index,
                    affinity: affinity_key(item_id),
                    taken: false,
                });
        }
        for claims in by_identity.values_mut() {
            claims.sort_by_key(|claim| claim.index);
        }
        Self { by_identity }
    }

    /// Give `grant` an entry of its own, under any id the game files the item as.
    ///
    /// The grant's own id first, then its alternates, because a name resolving to several rows is
    /// the other way the character can hold the item under a number the plan does not mention.
    ///
    /// Within one identity an exact affinity wins, so a build listing six Keen copies ahead of
    /// twenty-one Lightning ones hands the Keen entries to the Keen grants and leaves the
    /// Lightning ones unclaimed until their own grants arrive. Any remaining entry of the identity
    /// will do after that, which is what lets a grant naming a plain Longsword claim the Occult
    /// +25 the character actually holds.
    pub fn claim(&mut self, grant: &Grant) -> Option<i32> {
        for id in std::iter::once(grant.item_id).chain(grant.also_known_as.iter().copied()) {
            let Some(claims) = self.by_identity.get_mut(&identity(id)) else {
                continue;
            };
            let wanted = affinity_key(id);
            let chosen = claims
                .iter()
                .position(|claim| !claim.taken && claim.affinity == wanted)
                .or_else(|| claims.iter().position(|claim| !claim.taken));
            if let Some(at) = chosen {
                claims[at].taken = true;
                return Some(claims[at].index);
            }
        }
        None
    }

    /// Whether the character holds the item at all, whoever has claimed its entries.
    ///
    /// [`Self::claim`] answers `None` to two opposite facts -- a build naming more copies than the
    /// character has, and a build naming something they do not own -- and a report that folds them
    /// together says nothing about either.
    #[must_use]
    pub fn holds(&self, grant: &Grant) -> bool {
        std::iter::once(grant.item_id)
            .chain(grant.also_known_as.iter().copied())
            .any(|id| self.by_identity.contains_key(&identity(id)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::plan::NO_SKILL;

    fn grant_of(item_id: u32, label: &str, also: &[u32]) -> Grant {
        Grant {
            item_id,
            also_known_as: also.to_vec(),
            quantity: 1,
            reinforce_lv: 0,
            upgrade_is_character_default: true,
            weapon_skill: NO_SKILL,
            label: label.to_owned(),
            pot_group: None,
            armament: true,
        }
    }

    /// The Bone Bow, measured 2026-09-11.
    ///
    /// The grant names `40500000` -- base row, no affinity, no level, because the grant pass
    /// computes the level separately. The character held `40500025`. An inventory lookup for the
    /// grant's own id finds nothing, and the reorder pass skipped it in silence while reporting
    /// every other armament in the build as correctly ordered.
    #[test]
    fn an_armament_is_found_at_the_level_the_character_holds_it() {
        let bone_bow_base = 40_500_000;
        let mut claims = Claims::from_entries([(2256, 40_500_025)]);
        assert_eq!(
            claims.claim(&grant_of(bone_bow_base, "Bone Bow", &[])),
            Some(2256)
        );
        assert_ne!(40_500_025, bone_bow_base);
    }

    /// An affinity is folded away too, once no entry carries the one the grant names.
    #[test]
    fn an_infused_armament_satisfies_a_grant_naming_the_plain_one() {
        let longsword = 1_010_000;
        let occult_25 = longsword + 1_200 + 25;
        let mut claims = Claims::from_entries([(7, occult_25)]);
        assert_eq!(
            claims.claim(&grant_of(longsword, "Longsword", &[])),
            Some(7)
        );
    }

    /// A name that resolves to several rows is the other way the character holds an item under a
    /// number the plan does not name.
    #[test]
    fn an_alternate_row_satisfies_the_grant_that_names_it() {
        let named = 0x2000_0BB8;
        let alternate = 0x2000_0BB9;
        let entries = [(11, alternate)];
        assert_eq!(
            Claims::from_entries(entries).claim(&grant_of(named, "Talisman", &[alternate])),
            Some(11)
        );
        assert_eq!(
            Claims::from_entries(entries).claim(&grant_of(named, "Talisman", &[])),
            None
        );
    }

    /// An item the character genuinely does not hold is neither claimed nor held.
    #[test]
    fn an_item_the_character_does_not_hold_is_not_held() {
        let mut claims = Claims::from_entries([]);
        let grant = grant_of(1_010_000, "Longsword", &[]);
        assert_eq!(claims.claim(&grant), None);
        assert!(!claims.holds(&grant));
    }

    /// Every copy the build lists gets an entry of its own.
    ///
    /// The regression this closes, measured on build `b36964c2314bc5` 2026-09-22: the build lists
    /// 315 armaments and the grant pass minted 315 entries for them, and the reorder pass resolved
    /// every listing of one item to the lowest index holding it. So 165 entries were stamped, the
    /// other 150 were left where they were, and the inventory the grant had just put in order came
    /// out split in two.
    #[test]
    fn each_copy_the_build_lists_claims_its_own_entry() {
        let sword_of_night = 2_150_000;
        let mut claims = Claims::from_entries([
            (40, sword_of_night + 25),
            (41, sword_of_night + 25),
            (42, sword_of_night + 25),
        ]);
        let grant = grant_of(sword_of_night, "Sword of Night", &[]);
        assert_eq!(claims.claim(&grant), Some(40));
        assert_eq!(claims.claim(&grant), Some(41));
        assert_eq!(claims.claim(&grant), Some(42));
        // A fourth listing has no entry left, and the character still holds the item -- which is
        // the difference between a build asking for more copies than exist and one asking for
        // something absent.
        assert_eq!(claims.claim(&grant), None);
        assert!(claims.holds(&grant));
    }

    /// Two affinities of one armament are two entries, not one entry read twice.
    ///
    /// `0x01502791` and `0x01502921` are Raptor Talons Keen +25 and Lightning +25, the only two of
    /// build `b36964c2314bc5`'s 36 armament ids that share an [`identity`]. Resolving both to the
    /// lowest index of that identity stamped one entry twice and made the order check read the
    /// same `sortId` for both, which is what convinced the pass to rewrite an inventory that was
    /// already in the build's order.
    #[test]
    fn two_affinities_of_one_armament_claim_two_entries() {
        let keen = 0x0150_2791;
        let lightning = 0x0150_2921;
        assert_eq!(identity(keen), identity(lightning));
        let mut claims = Claims::from_entries([(908, keen), (2070, lightning)]);
        assert_eq!(
            claims.claim(&grant_of(keen, "Raptor Talons", &[])),
            Some(908)
        );
        assert_eq!(
            claims.claim(&grant_of(lightning, "Raptor Talons", &[])),
            Some(2070)
        );
    }

    /// The build lists the affinities in one order and the inventory holds them in another.
    ///
    /// The exact-affinity preference is what keeps this from degenerating into first-come: a
    /// Lightning listing must not be handed the Keen entry merely because it sorts lower.
    #[test]
    fn an_affinity_claims_its_own_copy_whatever_the_inventory_order() {
        let keen = 0x0150_2791;
        let lightning = 0x0150_2921;
        let mut claims = Claims::from_entries([(908, lightning), (2070, keen)]);
        assert_eq!(
            claims.claim(&grant_of(keen, "Raptor Talons", &[])),
            Some(2070)
        );
        assert_eq!(
            claims.claim(&grant_of(lightning, "Raptor Talons", &[])),
            Some(908)
        );
    }

    /// Claiming a whole build hands out distinct entries, which is the property the order check
    /// rests on: two grants reading one entry read one `sortId` twice and can never increase.
    #[test]
    fn no_two_grants_are_given_the_same_entry() {
        let sword_of_night = 2_150_000;
        let keen = 0x0150_2791;
        let lightning = 0x0150_2921;
        let mut claims = Claims::from_entries([
            (1, sword_of_night + 25),
            (2, sword_of_night + 25),
            (3, keen),
            (4, lightning),
            (5, lightning),
        ]);
        let grants = [
            grant_of(sword_of_night, "Sword of Night", &[]),
            grant_of(keen, "Raptor Talons", &[]),
            grant_of(lightning, "Raptor Talons", &[]),
            grant_of(sword_of_night, "Sword of Night", &[]),
            grant_of(lightning, "Raptor Talons", &[]),
        ];
        let mut given: Vec<i32> = grants.iter().filter_map(|g| claims.claim(g)).collect();
        assert_eq!(given.len(), grants.len());
        given.sort_unstable();
        given.dedup();
        assert_eq!(given, vec![1, 2, 3, 4, 5]);
    }
}
