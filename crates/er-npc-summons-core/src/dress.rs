//! Companion dressing: what a companion's build URL makes it wear.
//!
//! # The native point
//!
//! A companion is a c0000 human because its summon request carries a `CharaInitParam` id `>= 0`.
//! `CreateSummonChr` builds it synchronously: `ChrSet::SpawnChr` -> `ChrInsFactory::CreateCharacter`
//! (1.16.2 `0x140403a60`, 1.17.1 `0x140403dd0`) takes a free "NPC Player" `PlayerGameData`, looks
//! the row up with `GetCharaInitParam` (solo param 23; 1.16.2 `0x140d347f0`, 1.17.1 `0x140d35f00`)
//! and applies it with `0x140258c00` (1.17.1 `0x140258bd0`). That applier mints a gaitem for every
//! equip field and writes it into the new character's `ChrAsm`, the same way it does for every map
//! NPC in the game:
//!
//! | `CHARACTER_INIT_PARAM` | `ChrAsm` slot | read by (1.16.2 / 1.17.1) |
//! |---|---|---|
//! | `+0x10 / +0x14 / +0x108` right 1..3, type bytes `+0xe8..+0xea` | 1, 3, 5 | `0x140d34750` / `0x140d35e60` |
//! | `+0x18 / +0x1c / +0x10c` left 1..3, type bytes `+0xeb..+0xed` | 0, 2, 4 | `0x140d346b0` / `0x140d35dc0` |
//! | `+0x20..+0x2c` head, body, arms, legs | 12..15 | `0x14025aac0` / `0x14025aa90` |
//! | `+0x30..+0x3c` arrow, bolt, arrow 2, bolt 2, counts `+0xb2..+0xb8` | 6..9 | `0x140259850` / `0x140259820` |
//! | `+0x40..+0x4c` talismans 1..4 | 17..20 | `0x140255e80` / `0x140255e50` |
//!
//! A weapon field is an `EquipParamWeapon` id with affinity and upgrade level already added (the
//! game's own rows do this: 4100 of the 14938 weapons in the 1.17.1 regulation's CharaInitParam
//! carry a level, e.g. row 5400 right 2 = `33000025`), passed straight to
//! `CSGaitemImp::GetGaItemHandleWeapon`. A type byte of 0 means `EquipParamWeapon`; 1 sends the id
//! to a different table, so every armament this module plans is written with type 0. An empty
//! weapon field (`-1`) becomes Unarmed (110000), an empty armour field the default bare piece.
//!
//! So dressing a companion is writing its build into that row for exactly the length of its own
//! `CreateSummonChr` call, and putting the row back afterwards. This module decides the values.
//!
//! # What a build URL cannot dress
//!
//! The row has no field for an Ash of War, a face, a quickbar or a great rune, and the
//! companion's stats come from `NpcParam` and the summon's doping (design doc section 3.4), so
//! those are reported as not applied rather than approximated.

use er_build_import_core::catalog::{Catalog, Kind};
use er_build_import_core::equip::{Capacity, EquipPlan, EquipRef, equip_plan};
use er_build_import_core::model::BuildDoc;
use er_build_import_core::{UrlRejection, validate_build_url};

use crate::config::Companion;

/// What a companion's `build_url` asks for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Dressing {
    /// No URL: the body keeps its own `CharaInitParam` gear.
    Own,
    /// A planner share id to fetch.
    Build(String),
    /// A URL that cannot be fetched, and why. The body keeps its own gear.
    Refused(UrlRejection),
}

/// Read a companion's URL with the importer's own gate, so a link the Load Build from URL row
/// refuses is refused here for the same reason.
#[must_use]
pub fn dressing(companion: &Companion) -> Dressing {
    match companion.build_url.as_deref() {
        None => Dressing::Own,
        Some(url) => match validate_build_url(url) {
            Ok(id) => Dressing::Build(id.to_owned()),
            Err(why) => Dressing::Refused(why),
        },
    }
}

/// One armament the build wears.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Armament {
    /// `EquipParamWeapon` row with the affinity added: the +0 row.
    pub param_id: u32,
    /// The level the build asks for, on the scale [`Self::level_is_character_default`] names.
    pub requested_level: u16,
    /// `true` when the level is the build's character-wide `weaponUpgrade` (regular smithing
    /// levels, mapped down for a somber armament), `false` for a per-slot `upgrade`.
    pub level_is_character_default: bool,
    pub name: String,
}

/// One armour piece, talisman or ammunition stack the build wears.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Piece {
    /// Bare param row id.
    pub param_id: u32,
    pub name: String,
}

/// Everything the build wears that `CHARACTER_INIT_PARAM` can carry.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CompanionGear {
    /// Right hand slots 1..3.
    pub right: [Option<Armament>; 3],
    /// Left hand slots 1..3.
    pub left: [Option<Armament>; 3],
    /// Head, body, arms, legs.
    pub protectors: [Option<Piece>; 4],
    /// Arrow 1, bolt 1, arrow 2, bolt 2, with the stack size.
    pub ammo: [Option<(Piece, u16)>; 4],
    pub talismans: [Option<Piece>; 4],
    /// What the build asks for that this companion will not get, one line each.
    pub not_applied: Vec<String>,
}

/// The `CHARACTER_INIT_PARAM` values for one companion; `-1` is an empty slot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CharaInitGear {
    /// Right hand slots 1..3 (`+0x10`, `+0x14`, `+0x108`), level included.
    pub right: [i32; 3],
    /// Left hand slots 1..3 (`+0x18`, `+0x1c`, `+0x10c`), level included.
    pub left: [i32; 3],
    /// Head, body, arms, legs (`+0x20..+0x2c`).
    pub protectors: [i32; 4],
    /// Arrow, bolt, arrow 2, bolt 2 (`+0x30..+0x3c`).
    pub ammo: [i32; 4],
    /// Stack sizes (`+0xb2..+0xb8`); 0 with an empty slot.
    pub ammo_count: [u16; 4],
    /// Talismans 1..4 (`+0x40..+0x4c`).
    pub talismans: [i32; 4],
}

/// The planner's six armament positions, as (hand, slot within the hand): positions 0..3 are the
/// right hand and 3..6 the left (`er_build_import_core::equip::ARMAMENT_CHR_ASM_SLOTS`).
const fn hand_of(position: usize) -> (bool, usize) {
    (position < 3, position % 3)
}

/// The armament slot that won `position` in `equip_plan`: the same filter and the same
/// last-in-`order` rule, so the level comes from the row the build actually wears there.
fn level_of(doc: &BuildDoc, position: usize, winner: &EquipRef) -> (u16, bool) {
    let active = doc.sets.active_weapons();
    let mut claims: Vec<_> = doc
        .inventory
        .slots
        .iter()
        .filter(|slot| {
            slot.name == winner.name
                && slot
                    .equip_index_in_active_set(active)
                    .is_some_and(|index| index as usize == position)
        })
        .collect();
    claims.sort_by_key(|slot| slot.order);
    match claims.last().and_then(|slot| slot.upgrade) {
        Some(level) => (level, false),
        None => (doc.weapon_upgrade, true),
    }
}

fn piece(item: &EquipRef) -> Piece {
    Piece {
        param_id: item.param_id,
        name: item.name.clone(),
    }
}

/// Turn a parsed build into the gear a companion wears, resolving names through `catalog`
/// (the runtime catalog the importer builds from the game's own tables).
#[must_use]
pub fn gear_from_build(doc: &BuildDoc, catalog: &dyn Catalog) -> CompanionGear {
    let plan: EquipPlan = equip_plan(doc, catalog, Capacity::default());
    let mut gear = CompanionGear::default();

    for (position, entry) in plan.armaments.iter().enumerate() {
        let Some(item) = entry else { continue };
        let (requested_level, level_is_character_default) = level_of(doc, position, item);
        let armament = Armament {
            param_id: item.param_id,
            requested_level,
            level_is_character_default,
            name: item.name.clone(),
        };
        let (right, slot) = hand_of(position);
        if right {
            gear.right[slot] = Some(armament);
        } else {
            gear.left[slot] = Some(armament);
        }
    }
    for (index, part) in [&plan.head, &plan.body, &plan.arms, &plan.legs]
        .into_iter()
        .enumerate()
    {
        gear.protectors[index] = part.as_ref().map(piece);
    }
    for (index, entry) in plan.ammo.iter().enumerate().take(4) {
        gear.ammo[index] = entry.as_ref().map(|item| {
            let held = catalog
                .lookup(Kind::Ammo, &item.name)
                .and_then(|found| found.max_stored)
                .unwrap_or(1);
            (piece(item), u16::try_from(held.max(1)).unwrap_or(u16::MAX))
        });
    }
    for (index, entry) in plan.talismans.iter().enumerate().take(4) {
        gear.talismans[index] = entry.as_ref().map(piece);
    }

    for rejected in &plan.rejected {
        gear.not_applied
            .push(format!("{}: {}", rejected.name, rejected.reason));
    }
    let active = doc.sets.active_weapons();
    for slot in &doc.inventory.slots {
        if let Some(art) = slot.weapon_art.as_deref()
            && slot.equip_index_in_active_set(active).is_some()
        {
            gear.not_applied.push(format!(
                "ash of war {art:?} on {}: the row has no gem field, the armament keeps its own",
                slot.name
            ));
        }
    }
    if !plan.spells.is_empty() {
        gear.not_applied.push(format!(
            "{} spell(s): not memorised on a companion",
            plan.spells.len()
        ));
    }
    if plan.great_rune.is_some() {
        gear.not_applied
            .push("great rune: not carried by a companion".to_owned());
    }
    if doc.sliders.is_some() {
        gear.not_applied
            .push("face: the body's own face is kept".to_owned());
    }
    gear
}

fn id(piece: Option<&Piece>) -> i32 {
    piece.map_or(-1, |piece| i32::try_from(piece.param_id).unwrap_or(-1))
}

impl CompanionGear {
    /// The row values. `level(param_id, requested, is_character_default)` answers the game level
    /// to store for an armament: the runtime asks `ReinforceParamWeapon`, which is the only place
    /// that knows whether an armament is somber and how far it goes.
    #[must_use]
    pub fn chara_init(&self, level: impl Fn(u32, u16, bool) -> u16) -> CharaInitGear {
        let armament = |entry: &Option<Armament>| {
            entry.as_ref().map_or(-1, |armament| {
                let game_level = level(
                    armament.param_id,
                    armament.requested_level,
                    armament.level_is_character_default,
                );
                i32::try_from(er_build_import_core::plan::armament_item_id(
                    armament.param_id,
                    game_level,
                ))
                .unwrap_or(-1)
            })
        };
        CharaInitGear {
            right: [
                armament(&self.right[0]),
                armament(&self.right[1]),
                armament(&self.right[2]),
            ],
            left: [
                armament(&self.left[0]),
                armament(&self.left[1]),
                armament(&self.left[2]),
            ],
            protectors: self.protectors.each_ref().map(|part| id(part.as_ref())),
            ammo: self
                .ammo
                .each_ref()
                .map(|slot| id(slot.as_ref().map(|s| &s.0))),
            ammo_count: self
                .ammo
                .each_ref()
                .map(|slot| slot.as_ref().map_or(0, |s| s.1)),
            talismans: self.talismans.each_ref().map(|slot| id(slot.as_ref())),
        }
    }

    /// Whether the build put anything at all in the row's slots.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.right.iter().chain(&self.left).all(Option::is_none)
            && self.protectors.iter().all(Option::is_none)
            && self.ammo.iter().all(Option::is_none)
            && self.talismans.iter().all(Option::is_none)
    }

    /// One line naming what goes where, for the log.
    #[must_use]
    pub fn describe(&self, values: &CharaInitGear) -> String {
        let named = |label: &str, name: Option<&str>, value: i32| {
            name.map(|name| format!("{label} {name} ({value})"))
        };
        let mut parts = Vec::new();
        for (hand, slots, ids) in [
            ("R", &self.right, &values.right),
            ("L", &self.left, &values.left),
        ] {
            for (index, slot) in slots.iter().enumerate() {
                parts.extend(named(
                    &format!("{hand}{}", index + 1),
                    slot.as_ref().map(|a| a.name.as_str()),
                    ids[index],
                ));
            }
        }
        for (index, label) in ["head", "body", "arms", "legs"].into_iter().enumerate() {
            parts.extend(named(
                label,
                self.protectors[index].as_ref().map(|p| p.name.as_str()),
                values.protectors[index],
            ));
        }
        for (index, label) in ["arrow1", "bolt1", "arrow2", "bolt2"]
            .into_iter()
            .enumerate()
        {
            parts.extend(named(
                label,
                self.ammo[index].as_ref().map(|(p, _)| p.name.as_str()),
                values.ammo[index],
            ));
        }
        for (index, slot) in self.talismans.iter().enumerate() {
            parts.extend(named(
                &format!("talisman{}", index + 1),
                slot.as_ref().map(|p| p.name.as_str()),
                values.talismans[index],
            ));
        }
        if parts.is_empty() {
            "nothing".to_owned()
        } else {
            parts.join(", ")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use er_build_import_core::catalog::{Entry, MapCatalog, entry};
    use er_build_import_core::model;

    fn catalog() -> MapCatalog {
        MapCatalog::new()
            .with(Kind::Weapon, "Uchigatana", entry(9_000_000))
            .with(
                Kind::Weapon,
                "Moonveil",
                Entry {
                    somber: true,
                    ..entry(9_060_000)
                },
            )
            .with(Kind::Weapon, "Buckler", entry(31_190_000))
            .with(
                Kind::Protector,
                "Raging Wolf Helm",
                entry(0x1000_0000 | 1_190_000),
            )
            .with(
                Kind::Protector,
                "Raging Wolf Armor",
                entry(0x1000_0000 | 1_190_100),
            )
            .with(
                Kind::Talisman,
                "Rotten Winged Sword Insignia",
                entry(0x2000_0000 | 1_190),
            )
            .with(
                Kind::Ammo,
                "Bone Arrow",
                Entry {
                    max_stored: Some(99),
                    ..entry(51_100_000)
                },
            )
            .with(Kind::AshOfWar, "Bloody Slash", entry(0x2000_0000 | 10_300))
    }

    const BUILD: &str = r#"{
        "name": "wolf", "weaponUpgrade": 25,
        "inventory": {"slots": [
            {"name": "Uchigatana", "infusion": "Keen", "order": 0, "equipIndex": 0,
             "weaponArt": "Bloody Slash"},
            {"name": "Moonveil", "order": 1, "equipIndex": 1},
            {"name": "Buckler", "order": 2, "equipIndex": 3, "upgrade": 7},
            {"name": "Uchigatana", "order": 3}
        ]},
        "protectors": {
            "head": {"slots": [{"name": "Raging Wolf Helm", "equipIndex": 1}]},
            "body": {"slots": [{"name": "Raging Wolf Armor", "equipIndex": 1}]}
        },
        "talismans": {"slots": [{"name": "Rotten Winged Sword Insignia", "equipIndex": 2}]},
        "items": {"ammo": {"arrow1": "Bone Arrow"}}
    }"#;

    /// The runtime's rule, minus the table: somber armaments map the character-wide level down.
    fn level(param_id: u32, requested: u16, character_default: bool) -> u16 {
        if param_id / 10_000 == 906 && character_default {
            er_build_import_core::plan::somber_level_for_regular(requested)
        } else {
            requested
        }
    }

    #[test]
    fn a_build_lands_in_the_chara_init_fields_the_game_reads() {
        let doc = model::parse(BUILD).expect("fixture parses");
        let gear = gear_from_build(&doc, &catalog());
        let values = gear.chara_init(level);

        // Keen is +200; the character-wide +25 lands on the id, as in the game's own rows, and the
        // somber Moonveil gets the mapped +10.
        assert_eq!(values.right, [9_000_225, 9_060_010, -1]);
        // Planner position 3 is the left hand's first slot; its own upgrade wins.
        assert_eq!(values.left, [31_190_007, -1, -1]);
        assert_eq!(values.protectors, [1_190_000, 1_190_100, -1, -1]);
        assert_eq!(values.talismans, [-1, -1, 1_190, -1]);
        assert_eq!(values.ammo, [51_100_000, -1, -1, -1]);
        assert_eq!(values.ammo_count, [99, 0, 0, 0]);
        assert!(!gear.is_empty());
    }

    #[test]
    fn what_the_row_cannot_carry_is_named() {
        let doc = model::parse(BUILD).expect("fixture parses");
        let gear = gear_from_build(&doc, &catalog());
        assert_eq!(gear.not_applied.len(), 1);
        assert!(gear.not_applied[0].contains("Bloody Slash"));
    }

    #[test]
    fn an_unknown_item_is_left_empty_and_reported() {
        let doc = model::parse(
            r#"{"inventory": {"slots": [{"name": "Nonexistent Blade", "equipIndex": 0}]}}"#,
        )
        .expect("parses");
        let gear = gear_from_build(&doc, &catalog());
        assert!(gear.is_empty());
        assert_eq!(gear.chara_init(level).right, [-1, -1, -1]);
        assert!(gear.not_applied[0].contains("Nonexistent Blade"));
    }

    #[test]
    fn the_description_names_each_slot_and_value() {
        let doc = model::parse(BUILD).expect("fixture parses");
        let gear = gear_from_build(&doc, &catalog());
        let line = gear.describe(&gear.chara_init(level));
        assert!(line.starts_with("R1 Uchigatana (9000225), R2 Moonveil (9060010), L1 Buckler"));
        assert!(line.contains("talisman3 Rotten Winged Sword Insignia (1190)"));
    }

    #[test]
    fn urls_are_judged_by_the_importers_gate() {
        let config = Config::parse(
            "[mimic.companion.1]\nbuild_url = \"https://er-build-planner.nyasu.business/?b=af97a9da874151\"\n\
             [mimic.companion.2]\nbuild_url = \"https://er-build-planner.nyasu.business/?i=eyJ2IjoxfQ\"\n\
             [mimic.companion.3]\n",
        );
        let verdicts: Vec<Dressing> = config.mimic.companions.iter().map(dressing).collect();
        assert_eq!(
            verdicts,
            vec![
                Dressing::Build("af97a9da874151".to_owned()),
                Dressing::Refused(UrlRejection::SelfContained),
                Dressing::Own,
            ]
        );
    }
}
