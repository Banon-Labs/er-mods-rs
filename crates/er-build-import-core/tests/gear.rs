//! `gear::worn_gear` over a synthetic build: one id per `ChrAsm` slot, from the same equip plan and
//! armament arithmetic the importer uses.
//!
//! Each row of the document is there to pin one rule: an armament listed once per ash, a somber
//! armament at the character-wide level, a per-slot level, a contested position, an ash the catalog
//! does not have, armour with a part missing, and talismans where the equipped ones are not the
//! first four in the list.

use er_build_import_core::gear::{CatalogRow, WornGear, catalog_from_rows, worn_gear};
use er_build_import_core::model;

const ROWS: &str = r#"[
    {"kind": "weapon", "row": 21070000, "name": "Iron Ball"},
    {"kind": "weapon", "row": 9010000, "name": "Moonveil", "somber": true},
    {"kind": "weapon", "row": 1000000, "name": "Dagger"},
    {"kind": "weapon", "row": 11000000, "name": "Club"},
    {"kind": "ash_of_war", "row": 65200, "name": "Beast's Roar"},
    {"kind": "ash_of_war", "row": 413000, "name": "Blinkbolt"},
    {"kind": "ash_of_war", "row": 50300, "name": "Kick"},
    {"kind": "protector", "row": 5330000, "name": "Imp Head (Lion)"},
    {"kind": "protector", "row": 140000, "name": "Bull-Goat Helm"},
    {"kind": "protector", "row": 140100, "name": "Bull-Goat Armor"},
    {"kind": "protector", "row": 140200, "name": "Bull-Goat Gauntlets"},
    {"kind": "talisman", "row": 7060, "name": "Clarifying Horn Charm +2"},
    {"kind": "talisman", "row": 4090, "name": "Ritual Shield Talisman"},
    {"kind": "talisman", "row": 7000, "name": "Crimson Amber Medallion +3"}
]"#;

const BUILD: &str = r#"{
    "id": "synthetic", "name": "Gear test", "weaponUpgrade": 25,
    "sets": {"weapons": [{"name": "Default", "active": true}],
             "talismans": [{"name": "Default", "active": true}],
             "protectors": [{"name": "Default", "active": true}]},
    "inventory": {"slots": [
        {"name": "Iron Ball", "order": 0, "infusion": "Heavy", "weaponArt": "Beast's Roar",
         "equipSet": [0], "equipIndex": 0},
        {"name": "Iron Ball", "order": 1, "infusion": "Heavy", "weaponArt": "Blinkbolt"},
        {"name": "Dagger", "order": 2, "upgrade": 7, "equipSet": [1], "equipIndex": 1},
        {"name": "Club", "order": 3, "weaponArt": "Blinkbolt", "equipSet": [2], "equipIndex": 2},
        {"name": "Club", "order": 4, "weaponArt": "Kick", "equipSet": [2], "equipIndex": 2},
        {"name": "Moonveil", "order": 5, "equipSet": [3], "equipIndex": 3},
        {"name": "Club", "order": 6, "weaponArt": "No Such Ash", "equipSet": [4], "equipIndex": 4}
    ]},
    "protectors": {
        "head": {"slots": [{"name": "Bull-Goat Helm", "order": 0},
                           {"name": "Imp Head (Lion)", "order": 1, "equipSet": [1], "equipIndex": 1}]},
        "body": {"slots": [{"name": "Bull-Goat Armor", "order": 0, "equipSet": [1], "equipIndex": 1}]},
        "arms": {"slots": [{"name": "Bull-Goat Gauntlets", "order": 0, "equipSet": [1], "equipIndex": 1}]}
    },
    "talismans": {"slots": [
        {"name": "Clarifying Horn Charm +2", "order": 0},
        {"name": "Ritual Shield Talisman", "order": 1, "equipSet": [null], "equipIndex": 0},
        {"name": "Crimson Amber Medallion +3", "order": 2, "equipSet": [2], "equipIndex": 2},
        {"name": "Unknown Charm", "order": 3, "equipSet": [3], "equipIndex": 3}
    ]},
    "spells": {"slots": [{"name": "Glintstone Pebble", "order": 0}]},
    "items": {"tools": {"slots": [{"name": "Neutralizing Boluses", "order": 0, "equipIndex": 0}]}},
    "greatRune": "Morgott's Great Rune"
}"#;

fn gear() -> WornGear {
    let rows: Vec<CatalogRow> = serde_json::from_str(ROWS).expect("catalog rows parse");
    let doc = model::parse(BUILD).expect("build parses");
    worn_gear(&doc, &catalog_from_rows(&rows))
}

fn id(gear: &WornGear, slot: &str) -> i64 {
    gear.slot(slot).unwrap_or_else(|| panic!("no slot {slot}")).id
}

fn gem(gear: &WornGear, slot: &str) -> Option<u32> {
    gear.slot(slot).unwrap_or_else(|| panic!("no slot {slot}")).gem
}

#[test]
fn every_slot_is_present_in_a_fixed_order() {
    let names: Vec<&str> = gear().slots.iter().map(|piece| piece.slot).collect();
    assert_eq!(
        names,
        [
            "right1",
            "right2",
            "right3",
            "left1",
            "left2",
            "left3",
            "head",
            "chest",
            "hands",
            "legs",
            "talisman1",
            "talisman2",
            "talisman3",
            "talisman4"
        ]
    );
    let slots: Vec<i32> = gear().slots.iter().map(|piece| piece.chr_asm_slot).collect();
    assert_eq!(slots, [1, 3, 5, 0, 2, 4, 12, 13, 14, 15, 17, 18, 19, 20]);
}

#[test]
fn the_worn_copy_of_an_armament_listed_once_per_ash_carries_its_own_ash() {
    let gear = gear();
    // Heavy is +100 on the row, and `weaponUpgrade: 25` on a regular armament is +25.
    assert_eq!(id(&gear, "right1"), 21_070_125);
    assert_eq!(gem(&gear, "right1"), Some(65_200));
    let right1 = gear.slot("right1").expect("right1");
    assert_eq!(right1.name.as_deref(), Some("Iron Ball"));
    assert_eq!(right1.ash.as_deref(), Some("Beast's Roar"));
}

#[test]
fn levels_follow_the_importer_on_both_scales() {
    let gear = gear();
    // A per-slot `upgrade` is the game's own number.
    assert_eq!(id(&gear, "right2"), 1_000_007);
    assert_eq!(gem(&gear, "right2"), None);
    // `weaponUpgrade: 25` on a somber armament is +10.
    assert_eq!(id(&gear, "left1"), 9_010_010);
}

#[test]
fn a_contested_position_takes_the_last_row_and_its_ash() {
    let gear = gear();
    assert_eq!(id(&gear, "right3"), 11_000_025);
    assert_eq!(gem(&gear, "right3"), Some(50_300));
    assert!(
        gear.unresolved
            .iter()
            .any(|problem| problem.name == "Club" && problem.reason.contains("already claimed")),
        "{:?}",
        gear.unresolved
    );
}

#[test]
fn an_unresolved_ash_leaves_the_weapon_and_is_reported() {
    let gear = gear();
    assert_eq!(id(&gear, "left2"), 11_000_025);
    assert_eq!(gem(&gear, "left2"), None);
    assert!(
        gear.unresolved
            .iter()
            .any(|problem| problem.name == "No Such Ash"),
        "{:?}",
        gear.unresolved
    );
}

#[test]
fn empty_hands_hold_the_unarmed_fist() {
    assert_eq!(id(&gear(), "left3"), 110_000);
    assert_eq!(gear().slot("left3").expect("left3").name, None);
}

#[test]
fn armour_is_the_worn_piece_and_a_missing_part_is_the_bare_piece() {
    let gear = gear();
    assert_eq!(id(&gear, "head"), 5_330_000);
    assert_eq!(id(&gear, "chest"), 140_100);
    assert_eq!(id(&gear, "hands"), 140_200);
    assert_eq!(id(&gear, "legs"), 10_300);
}

#[test]
fn talismans_are_the_equipped_ones_not_the_first_in_the_list() {
    let gear = gear();
    // A stale `equipSet` hole with `equipIndex` 0 is still worn (model::Slot docs).
    assert_eq!(id(&gear, "talisman1"), 4_090);
    assert_eq!(id(&gear, "talisman2"), -1);
    assert_eq!(id(&gear, "talisman3"), 7_000);
    // Equipped but not in the catalog: the slot is empty and the name is reported.
    assert_eq!(id(&gear, "talisman4"), -1);
    assert!(
        gear.unresolved
            .iter()
            .any(|problem| problem.name == "Unknown Charm"),
        "{:?}",
        gear.unresolved
    );
    // Listed first and never equipped.
    assert!(gear.slots.iter().all(|piece| piece.id != 7_060));
}

#[test]
fn spells_tools_and_the_great_rune_are_not_reported_against_a_gear_catalog() {
    let gear = gear();
    for absent in [
        "Glintstone Pebble",
        "Neutralizing Boluses",
        "Morgott's Great Rune",
    ] {
        assert!(
            gear.unresolved.iter().all(|problem| problem.name != absent),
            "{absent} should not be reported: {:?}",
            gear.unresolved
        );
    }
}
