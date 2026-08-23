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

use crate::read_character::CharacterRead;

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

    // The character's HIGHEST weapon upgrade is what the planner's `weaponUpgrade` means -- it is
    // the level every armament defaults to. Per-slot `upgrade` overrides exist in the format but
    // are NOT emitted: the level of an individual weapon lives in its gaitem, which this read does
    // not reach, and inventing one per slot would state something unmeasured as fact. A character
    // whose weapons are all at the same level -- which is the overwhelming case for a finished
    // build -- exports exactly right; a mixed one exports its best level for all of them.
    if let Some(upgrade) = read.weapon_upgrade {
        doc.weapon_upgrade = upgrade;
    }

    doc.inventory.slots = read
        .armaments
        .iter()
        .enumerate()
        .map(|(order, item)| {
            let mut slot = Slot::carried(&item.name, order as i64).equipped_at(item.equip_index);
            if let Some(infusion) = item.infusion.as_deref() {
                slot = slot.with_infusion(infusion);
            }
            if let Some(art) = item.weapon_art.as_deref() {
                slot = slot.with_weapon_art(art);
            }
            slot
        })
        .collect();

    doc.talismans.slots = read
        .talismans
        .iter()
        .enumerate()
        .map(|(order, item)| Slot::carried(&item.name, order as i64).equipped_at(item.equip_index))
        .collect();

    for (part, item) in &read.protectors {
        // Each body part holds exactly one worn piece, so it is index 0 of its own list.
        let list = SlotList {
            slots: vec![Slot::carried(&item.name, 0).equipped_at(0)],
            ..SlotList::default()
        };
        match *part {
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
    doc
}
