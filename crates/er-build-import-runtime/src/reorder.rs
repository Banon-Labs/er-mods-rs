//! Making the character's inventory come out in the build's order, for items they already own.
//!
//! # The case this exists for
//!
//! A build imported twice is usually not a build being installed twice. It is the same items with
//! something moved, or with one thing swapped out -- the player edits the page and re-imports.
//! Everything the character already holds is left alone by the grant, which is right: reconciling
//! to a target rather than adding to a pile is what stopped the importer minting a second Flask of
//! Wondrous Physick. But it means nothing about those items changes, and the one thing the player
//! looks at to check the import worked -- the inventory list, next to the planner page -- keeps
//! whatever order it had before.
//!
//! `plan::plan` already emits its grants in the build's own order, and the comment there says
//! plainly that this is "the order the player sees in their inventory and the only order they can
//! check against the planner page". That is true only for items being acquired for the first time.
//! For an item already held, the acquisition order was decided whenever the player picked it up,
//! and no amount of granting in the right order changes it.
//!
//! # What changes it
//!
//! `EquipInventoryData.nextSortId` is a counter, and `CS::EquipInventoryData::InsertItem` stamps
//! `entry.sortId` from it and increments on every insert. So the order is the order things
//! arrived, and an item arrives when it enters the inventory -- including when it comes back out
//! of the storage box.
//!
//! The obvious way to earn a new one is to make the game acquire the item again: deposit the
//! stack into the storage box and take it straight back. That is what this pass did until
//! 2026-09-10, and it fails completely on a full box -- a deposit needs a free entry, and
//! `GetAddOrRemoveAmount` answers zero for a non-stackable when there is none. Measured on a box
//! at `1920 of 1920`: 6 items of 137 re-acquired, 131 declined with "the box would not take it",
//! and the inventory kept whatever order it had.
//!
//! So the counter is used directly. Walking the build's items in the build's order and stamping
//! each entry from `nextSortId` is exactly what `InsertItem` does, minus the two transfers -- and
//! it cannot strand an item in the box, because no item moves. The counter is left past the last
//! value written, so anything the player picks up afterwards still sorts above the build.
//!
//! # Why nothing else has to be moved out of the way
//!
//! The obvious alternative is to evict the character's other items so the build's items are not
//! interleaved with them. This does not need that, and deliberately does not do it: after the
//! walk, every item the build names sits above every item it does not, so there is nothing left to
//! interleave. A player's crafting materials, keys and souvenirs stay in their pockets, which is
//! where they want them -- exiling them to the box to tidy a sort order would be a much larger
//! change to the character than the import itself.
//!
//! # One entry per copy the build lists
//!
//! A build names copies and an inventory holds entries, and until 2026-09-22 this pass resolved
//! every listing of an item to the lowest index holding it. A build listing seventy-five Swords of
//! Night stamped one entry seventy-five times, left the other seventy-four where they were, and
//! read that one entry seventy-five times in the order check -- which could then never report the
//! build in order, so the pass never stopped. `er_build_import_core::claim` is where each listing
//! is given an entry of its own, and its header carries the measurement.
//!
//! # What it will not do
//!
//! An entry whose store fails is reported by name rather than counted, and no transfer can be left
//! half-completed, because no item moves.

use er_build_import_core::claim::Claims;
use er_build_import_core::plan::Grant;
use er_game_base::rva::GET_EQUIP_INVENTORY_DATA_RVA;

use crate::storage::Storage;

/// `CS::EquipGameData::GetEquipInventoryData(egd) -> EquipInventoryData*`.
type GetInventoryFn = unsafe extern "system" fn(usize) -> usize;

/// Every inventory entry a grant could be given, from one walk of the carried inventory.
///
/// The rules for handing them out are [`Claims`], in the core crate beside the sweep that shares
/// their vocabulary, where they can be held to their invariants without a game attached. This is
/// the half that needs one: the walk, which also replaces one native call per item.
///
/// # Safety
///
/// Game thread; the caller's contract covers the walk.
unsafe fn read_claims(storage: &Storage) -> Claims {
    // Safety: game thread, read only -- delegated to the caller's contract.
    let entries = unsafe { storage.carried_entries() };
    Claims::from_entries(
        entries
            .into_iter()
            .filter(|entry| entry.quantity > 0)
            .map(|entry| (entry.index, entry.item_id)),
    )
}

/// What one reorder pass did.
#[derive(Debug, Default)]
pub struct ReorderOutcome {
    /// Distinct items the build names that the character holds.
    pub attempted: usize,
    /// Items the game re-acquired, proved by a strictly larger `sortId`.
    pub restamped: usize,
    /// Items that were worn and had to be taken off first, so they could be moved at all.
    pub unequipped: usize,
    /// `(label, why)` for each item that was not moved.
    pub declined: Vec<(String, &'static str)>,
    /// Items the build names that no inventory entry could be found for, by label.
    ///
    /// Distinct from [`Self::declined`], which is "tried and failed". This is "never tried", and
    /// it used to be a bare `continue`: not attempted, not declined, and skipped again by the
    /// order check, so the pass scored itself over the items it happened to resolve and printed
    /// `160/160 ... in the build's order` while one of them sat at the front of the inventory.
    ///
    /// One entry per label however many times the build names it, because a build listing nine
    /// copies of something absent is one item to go and find, not nine.
    pub not_held: Vec<String>,
    /// Mentions of an item the character holds fewer copies of than the build lists.
    ///
    /// A build names copies and an inventory holds entries. The two agree for an armament, which
    /// the grant pass mints one of per listing, and they do not for a talisman or a piece of
    /// armour: `plan::plan` grants those a literal one each, so a build listing an item five times
    /// leaves the character holding it once. The first mention claims that entry and the rest land
    /// here.
    ///
    /// Not a failure of this pass -- there is no second entry to put anywhere -- but the only
    /// place the shortfall is counted at all. The grant ledger cannot see it: it asks whether the
    /// character holds at least the quantity each grant names, and one entry answers yes to every
    /// mention of it. Measured on build `b36964c2314bc5`, 2026-09-22: 493 of 493 grants confirmed,
    /// while that build lists 80 talismans over 72 distinct names and 80 pieces of armour over 39,
    /// and the eviction pass then walked 426 gear entries against the 475 the build lists. The 49
    /// absent are these.
    pub repeats: usize,
    /// `(label, deposited, retrieved)` for any item that did not come all the way back.
    ///
    /// Always empty in a healthy run, and the one failure here that costs the player something
    /// rather than merely leaving a list in the wrong order.
    pub stranded: Vec<(String, i32, i32)>,
    /// Whether the final read-back found the build's items in the build's order.
    pub in_order: bool,
    /// How many items that verdict covers.
    pub order_checked: usize,
    /// Why nothing was attempted, when that is the answer.
    pub unavailable: Option<&'static str>,
    /// The inventory was already in the build's order, so nothing was moved.
    ///
    /// The common case for a first import onto a character who owned none of it: the grant walks
    /// the build in order, so the items arrive in order and there is nothing to correct. Reported
    /// rather than folded into a zero, because "nothing needed doing" and "nothing could be done"
    /// are the same numbers and opposite facts.
    pub already_in_order: bool,
}

impl ReorderOutcome {
    /// One line for the import log.
    pub fn summary(&self) -> String {
        if let Some(why) = self.unavailable {
            return format!(
                "REORDER: the inventory keeps whatever order it had -- {why}. Items the character \
                 already owned will not sit where the build lists them"
            );
        }
        if self.already_in_order {
            return format!(
                "REORDER: nothing to do -- the {} item(s) the build names are already in the \
                 build's order",
                self.order_checked
            );
        }
        format!(
            "REORDER: {}/{} item(s) re-acquired into the build's order ({} had to be taken off \
             first, {} declined, {} stranded in the box, {} further mention(s) of an item the \
             character holds fewer copies of than the build lists, {} the character does not hold \
             under any id{}); the {} item(s) that could be read back are {}",
            self.restamped,
            self.attempted,
            self.unequipped,
            self.declined.len(),
            self.stranded.len(),
            self.repeats,
            self.not_held.len(),
            if self.not_held.is_empty() {
                String::new()
            } else {
                format!(
                    " -- those are NOT in the count above and keep whatever order they had: {}",
                    self.not_held.join(", ")
                )
            },
            self.order_checked,
            if self.in_order {
                "in the build's order"
            } else {
                "NOT in the build's order"
            }
        )
    }
}

/// Re-acquire everything the build names, in the build's order.
///
/// Runs between the grant and the equip, and both sides of that are load-bearing:
///
/// * after the grant, because an item that is not held yet cannot be reordered. Items the grant
///   has just minted are moved along with the rest and not skipped: the grant walks the build in
///   order, so those are in order among themselves, but every item the character already owned
///   carries an older `sortId` and would sort ahead of all of them regardless of where the build
///   lists it. Only moving everything puts the two groups on one scale;
/// * before the equip, because a worn entry cannot be deposited. The pass takes items off to move
///   them and does not put them back; the equip pass, which is about to write every position the
///   build names anyway, is what dresses the character afterwards.
///
/// # Safety
///
/// Game thread, character in the world, grants already applied, `egd` a live `EquipGameData*`.
/// The carried inventory is resolved from it here rather than passed in, so no caller can hand
/// this pass an `EquipInventoryData*` belonging to a different `EquipGameData`.
pub unsafe fn apply_build_order(
    module_base: usize,
    egd: usize,
    grants: &[Grant],
) -> ReorderOutcome {
    let mut outcome = ReorderOutcome::default();

    let Some(get_inventory) = crate::native::resolve(
        module_base,
        GET_EQUIP_INVENTORY_DATA_RVA,
        "CS::EquipGameData::GetEquipInventoryData",
    ) else {
        outcome.unavailable =
            Some("`GetEquipInventoryData` has no verified mapping for the running build");
        return outcome;
    };
    // Safety: resolved for the running build on the line above.
    let get_inventory: GetInventoryFn = unsafe { core::mem::transmute(get_inventory) };
    // Safety: game thread, `egd` live; the getter reads one field.
    let carried = unsafe { get_inventory(egd) };
    if carried == 0 {
        outcome.unavailable = Some("the carried inventory is null");
        return outcome;
    }

    // Safety: delegated -- `open` does its own singleton null checks and resolves every native it
    // needs before calling any of them.
    let Some(storage) = (unsafe { Storage::open(module_base, egd, carried) }) else {
        outcome.unavailable = Some("the storage box is unreachable this session");
        return outcome;
    };

    // Ask before moving anything. A first import onto a character who owned none of the build
    // leaves it already in order -- the grant walks the build in order and the items arrive in
    // that order -- and this pass would otherwise put every one of them through the storage box
    // to arrive at the arrangement they were already in. Two transfers per item, for nothing.
    //
    // Getting this answer wrong is not a wasted pass, it is a wrecked inventory. The stamps below
    // come from `nextSortId`, above every entry the grant just minted, so a build already in order
    // comes back out with one copy of each item pulled to the end of the list and the rest left
    // where they were. That is what build `b36964c2314bc5` hit on 2026-09-22: 315 armaments minted
    // in the build's order, then 165 of them restamped above the other 150. See `Claims` for the
    // two grants that shared one entry and made this answer wrong.
    // Safety: game thread, read only.
    let (already, checked) = unsafe { verify_order(&storage, grants) };
    if already {
        outcome.already_in_order = true;
        outcome.in_order = true;
        outcome.order_checked = checked;
        return outcome;
    }

    // The counter the game itself stamps from, so everything this pass writes sits above every
    // acquisition the character already had and below everything they pick up afterwards.
    // Safety: game thread, read only.
    let Some(mut next) = (unsafe { storage.next_sort_id() }) else {
        outcome.unavailable = Some("the inventory's acquisition counter could not be read");
        return outcome;
    };

    // One walk, and every claim below comes out of it. See `Claims` for why asking the inventory
    // which entry holds `grant.item_id` is the wrong question.
    // Safety: game thread, read only.
    let mut claims = unsafe { read_claims(&storage) };

    for grant in grants {
        let Some(index) = claims.claim(grant) else {
            // Two absences with one shape, and the pass has to tell them apart: an item the
            // character does not own under any id it could be filed as, and one they own fewer
            // copies of than the build lists. Counted rather than skipped -- a grant that leaves
            // the loop without an outcome is one the order check skips too, and the pass then
            // prints a perfect score over the items it happened to find.
            if claims.holds(grant) {
                outcome.repeats += 1;
            } else if !outcome.not_held.contains(&grant.label) {
                outcome.not_held.push(grant.label.clone());
            }
            continue;
        };
        outcome.attempted += 1;

        // Safety: game thread; a store of one int into a live entry, fault-checked.
        if unsafe { storage.restamp(index, next) } {
            outcome.restamped += 1;
            next = next.saturating_add(1);
        } else {
            outcome.declined.push((
                grant.label.clone(),
                "its inventory entry could not be stamped with a new acquisition order",
            ));
        }
    }

    // Safety: game thread; a store of one int into a live inventory, fault-checked.
    unsafe { storage.set_next_sort_id(next) };

    // Safety: game thread, read only.
    let (in_order, checked) = unsafe { verify_order(&storage, grants) };
    outcome.in_order = in_order;
    outcome.order_checked = checked;
    outcome
}

/// Whether the build's items now read back in the build's order, and over how many of them.
///
/// The measurement the pass is for. Counting successful round trips would prove that items moved;
/// this proves what moving them was supposed to achieve, which is a different claim and the only
/// one worth printing. An item that could not be read is left out of both numbers rather than
/// counted as agreeing.
///
/// # Safety
///
/// Game thread.
unsafe fn verify_order(storage: &Storage, grants: &[Grant]) -> (bool, usize) {
    // Safety: game thread, read only -- delegated to the caller's contract.
    let mut claims = unsafe { read_claims(storage) };
    let mut previous: Option<i32> = None;
    let mut checked = 0usize;
    let mut in_order = true;
    for grant in grants {
        // The same claiming walk the stamping loop uses, over its own fresh set. When the two
        // disagreed, the loop skipped an item and so did this, and the pass reported the order of
        // what was left as the order of the whole build.
        let Some(index) = claims.claim(grant) else {
            continue;
        };
        // Safety: game thread, read only.
        let Some(sort_id) = (unsafe { storage.sort_id_at(index) }) else {
            continue;
        };
        checked += 1;
        if let Some(previous) = previous
            && sort_id <= previous
        {
            in_order = false;
        }
        previous = Some(sort_id);
    }
    (in_order, checked)
}

#[cfg(test)]
mod tests {
    use super::*;
    use er_build_import_core::plan::NO_SKILL;

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

    /// The claiming rules themselves are tested in `er_build_import_core::claim`, which builds on
    /// the host. This crate is a Windows `cdylib` and its tests only ever type-check here, so what
    /// is worth keeping in it is the reporting -- the part that decides what the player is told.
    ///
    /// The summary names what it could not find, and says those are outside its own score.
    #[test]
    fn the_summary_names_what_it_never_tried() {
        let outcome = ReorderOutcome {
            attempted: 159,
            restamped: 159,
            order_checked: 159,
            in_order: true,
            not_held: vec!["Bone Bow".to_owned()],
            ..ReorderOutcome::default()
        };
        let summary = outcome.summary();
        assert!(summary.contains("Bone Bow"), "{summary}");
        assert!(summary.contains("NOT in the count above"), "{summary}");
    }

    /// A build asking for more copies than the character holds says so in its own clause.
    ///
    /// The shortfall has no other reader. The grant ledger asks whether the character holds at
    /// least the quantity each grant names and one entry answers yes to every mention of it, so
    /// build `b36964c2314bc5` scored 493 of 493 while 49 of the talisman and armour copies it
    /// lists were never on the character at all.
    #[test]
    fn the_summary_counts_copies_the_character_does_not_have() {
        let outcome = ReorderOutcome {
            attempted: 426,
            restamped: 426,
            order_checked: 426,
            in_order: true,
            repeats: 49,
            ..ReorderOutcome::default()
        };
        let summary = outcome.summary();
        assert!(
            summary.contains("49 further mention(s) of an item the character holds fewer copies"),
            "{summary}"
        );
    }

    /// A grant is still what the pass walks, so the helper has to build one.
    #[test]
    fn a_grant_carries_the_ids_the_claim_is_made_under() {
        let grant = grant_of(1_010_000, "Longsword", &[1_011_200]);
        assert_eq!(grant.item_id, 1_010_000);
        assert_eq!(grant.also_known_as, vec![1_011_200]);
    }
}
