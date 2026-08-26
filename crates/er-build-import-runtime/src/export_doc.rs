//! Turning a [`CharacterRead`] into the planner document the share link carries.
//!
//! Two separate crates meet here and neither should know about the other: [`crate::read_character`]
//! speaks in `ChrAsmSlot`s and param ids, and `er-build-export` speaks in planner JSON. This module
//! is the only place that knows both, so a change to either side breaks exactly one file.
//!
//! # Everything read is EQUIPPED, so everything emitted is equipped
//!
//! The read is scoped to the loadout, so every armament, armour piece and talisman in a
//! [`CharacterRead`] came out of an equipment slot. Each therefore gets both `equipIndex` and
//! `equipSet` -- the planner writes them together, and an `equipIndex` without a matching
//! `equipSet` describes an item equipped in no set at all.
//!
//! Spells are the exception, and not by omission: the planner gives memorised spells no
//! `equipIndex` at all, only `order`. Their list position IS the memorisation order, which is
//! exactly how the importer reads them back.

use er_build_export::BuildExportDoc;
use er_build_export::model::{Slot, SlotList, Stats};
use er_build_import_core::equip::PROTECTOR_PARTS;
use er_build_import_core::plan::{MAX_SOMBER_LEVEL, regular_level_for_somber};

use crate::read_character::{CharacterRead, ReadSlot};

/// The equip index the planner writes for ARMOUR.
///
/// One, not zero, and not the body part: the planner's own writer is
/// `setSlotEquipIndex('protectors', slot, 1)` -- membership only, since which part a piece is worn
/// on comes from which of the four lists it sits in. Most of the site reads armour back with
/// `equipIndex != null`, which zero satisfies, but its build-code exporter looks for
/// `equipIndex === 1` exactly and finds NOTHING when the value is zero.
const PROTECTOR_EQUIP_INDEX: u32 = 1;

/// One item, as a planner slot: name, position in its list, and everything the read knew about it.
///
/// The equip index is what separates a WORN item from a carried one, and it is the read's answer
/// rather than the list position -- the two are different numbers, and conflating them is what
/// would put a backup weapon in the main hand.
fn planner_slot(item: &ReadSlot, order: usize) -> Slot {
    let mut slot = Slot::carried(&item.name, order as i64);
    if let Some(index) = item.equip_index {
        slot = slot.equipped_at(index);
    }
    if let Some(infusion) = item.infusion.as_deref() {
        slot = slot.with_infusion(infusion);
    }
    if let Some(art) = item.weapon_art.as_deref() {
        slot = slot.with_weapon_art(art);
    }
    // EVERY armament states its own level. `weaponUpgrade` is one number for the whole character
    // (the game's own `matching_weapon_level`), so leaving the per-slot key off showed a backup
    // weapon at the main weapon's level -- and the level is not an inference here: it is read
    // straight off the id of the instance in the slot.
    if let Some(upgrade) = item.upgrade {
        slot = slot.with_upgrade(upgrade);
    }
    slot
}

/// The planner's stat keys, in the order [`CharacterRead::stats`] produces them. Named here so a
/// key that stops matching fails to find its field rather than silently exporting a zero.
const STAT_LEVEL: &str = "rl";
const STAT_VIGOR: &str = "vig";
const STAT_MIND: &str = "mnd";
/// The planner calls ENDURANCE `vit`. Verified, not inferred -- and the single most dangerous key
/// here, because reading it as Vitality produces a build that is wrong in a way that looks right.
const STAT_ENDURANCE: &str = "vit";
const STAT_STRENGTH: &str = "str";
const STAT_DEXTERITY: &str = "dex";
const STAT_INTELLIGENCE: &str = "int";
const STAT_FAITH: &str = "fth";
const STAT_ARCANE: &str = "arc";

/// Build the document.
pub fn document_from(read: &CharacterRead) -> BuildExportDoc {
    let stat = |key: &str| {
        read.stats
            .iter()
            .find(|(name, _)| *name == key)
            .map(|(_, value)| *value)
            .unwrap_or_default()
    };

    let mut doc = BuildExportDoc {
        name: read.name.clone(),
        character_class: read.character_class.clone(),
        two_handing: read.two_handing,
        stats: Stats {
            rune_level: stat(STAT_LEVEL),
            vigor: stat(STAT_VIGOR),
            mind: stat(STAT_MIND),
            endurance: stat(STAT_ENDURANCE),
            strength: stat(STAT_STRENGTH),
            dexterity: stat(STAT_DEXTERITY),
            intelligence: stat(STAT_INTELLIGENCE),
            faith: stat(STAT_FAITH),
            arcane: stat(STAT_ARCANE),
        },
        ..BuildExportDoc::default()
    };

    // `weaponUpgrade` is MEASURED off the armaments, not taken from the character.
    //
    // `PlayerGameData::matching_weapon_level` looks like the right field and is not: it read 25 on
    // a character whose every armament is +17 or +7 (nothing it owned was +25), which put a "+25"
    // on the shared sheet that described nothing. So the number is derived from the armaments this
    // export actually carries, on the planner's regular-stone scale -- a somber armament's level is
    // mapped back up, because that is the scale this field is in and it also acts as a CAP:
    // the planner renders a slot at `min(slot.upgrade, lr[weaponUpgrade])`, so a number below any
    // slot's own level would silently clamp it.
    //
    // The character's field remains the fallback for a build with no armaments at all, where there
    // is nothing to measure.
    let measured = read
        .armaments
        .iter()
        .filter_map(|item| {
            let level = item.upgrade?;
            Some(match item.max_upgrade {
                Some(MAX_SOMBER_LEVEL) => regular_level_for_somber(level),
                _ => level,
            })
        })
        .max();
    if let Some(upgrade) = measured.or(read.weapon_upgrade) {
        doc.weapon_upgrade = upgrade;
    }

    doc.inventory.slots = read
        .armaments
        .iter()
        .enumerate()
        .map(|(order, item)| planner_slot(item, order))
        .collect();

    doc.talismans.slots = read
        .talismans
        .iter()
        .enumerate()
        .map(|(order, item)| planner_slot(item, order))
        .collect();

    // Armour, one list per body part, carrying everything the character HOLDS for that part with
    // the worn piece marked. Ordered by part rather than by the order the inventory listed them,
    // because the planner keeps four separate lists and an item's list is what says which part it
    // is for.
    for part in PROTECTOR_PARTS {
        let slots: Vec<Slot> = read
            .protectors
            .iter()
            .filter(|(held_part, _)| *held_part == part)
            .enumerate()
            .map(|(order, (_, item))| {
                let mut slot = planner_slot(item, order);
                if item.equip_index.is_some() {
                    slot = slot.equipped_at(PROTECTOR_EQUIP_INDEX);
                }
                slot
            })
            .collect();
        let list = SlotList {
            slots,
            ..SlotList::default()
        };
        match part {
            "head" => doc.protectors.head = list,
            "body" => doc.protectors.body = list,
            "arms" => doc.protectors.arms = list,
            "legs" => doc.protectors.legs = list,
            // `PROTECTOR_PARTS` is a fixed four-element table, so this is unreachable; dropping
            // rather than panicking keeps a future fifth part from taking the game down.
            _ => {}
        }
    }

    // Spells carry ORDER only: the planner gives a memorised spell no equip index, and its position
    // in this list is the memorisation slot.
    doc.spells.slots = read
        .spells
        .iter()
        .enumerate()
        .map(|(order, item)| Slot::carried(&item.name, order as i64))
        .collect();

    // Physick: always two entries, `null` for an empty half, which is the shape `makeDefault` has.
    doc.items.crystal_tears = read.crystal_tears.clone();
    doc.items.flasks.crimson = read.flask_crimson;
    doc.items.flasks.cerulean = read.flask_cerulean;
    doc.items.flasks.total = read.flask_crimson + read.flask_cerulean;

    doc.great_rune = read.great_rune.clone();
    // The appearance, as an uppercase hex AOB. Hex rather than base64 because it is what a player
    // pastes into a save editor or a Cheat Engine table, which is the only tool that can do
    // anything with it today -- the planner has no appearance at all.
    doc.face_data = read.face_data.as_deref().map(hex_upper);
    doc
}

/// Bytes as one uppercase hex string, no separators -- an AOB the way every tool that eats one
/// spells it.
fn hex_upper(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(HEX_DIGITS[usize::from(byte >> 4)]);
        out.push_str(HEX_DIGITS[usize::from(byte & 0x0F)]);
    }
    out
}

/// Uppercase hex digits, indexed by nibble.
const HEX_DIGITS: [&str; 16] = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F",
];
