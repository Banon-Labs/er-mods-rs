//! End-to-end: a real planner payload becomes a complete, correct grant list.
//!
//! The fixture is the exact 6871-byte body returned by
//! `GET https://er-inventory-api.nyasu.business/inventories/af97a9da874151`,
//! captured unauthenticated. Its catalogue rows come from the planner's public
//! item database.

mod fixture_catalog;

use er_build_import::catalog::Kind;
use er_build_import::plan::{NO_SKILL, plan};
use er_build_import::{model, share_id_from_url};

const BUILD: &str = include_str!("fixtures/build-af97a9da874151.json");

fn planned() -> (model::BuildDoc, er_build_import::Plan) {
    let doc = model::parse(BUILD).expect("fixture parses");
    let result = plan(&doc, &fixture_catalog::catalog());
    (doc, result)
}

#[test]
fn parses_the_real_payload() {
    let (doc, _) = planned();
    assert_eq!(doc.id, "af97a9da874151");
    assert_eq!(doc.name, "Occult Mage");
    assert_eq!(doc.character_class.as_deref(), Some("Bandit"));
    assert_eq!(doc.weapon_upgrade, 25);
    assert_eq!(doc.stats.get("rl"), Some(&150));
    assert_eq!(doc.inventory.slots.len(), 35);
    assert_eq!(doc.spells.slots.len(), 8);
    assert_eq!(doc.talismans.slots.len(), 4);
}

#[test]
fn every_referenced_item_resolves() {
    let (_, result) = planned();
    assert!(
        result.is_complete(),
        "unresolved items: {:?}",
        result
            .unresolved
            .iter()
            .map(|u| (u.kind.label(), &u.name))
            .collect::<Vec<_>>()
    );
}

#[test]
fn the_accented_weapon_resolves() {
    // The build spells it `Miséricorde`; the catalogue key is `Misericorde`.
    let (doc, result) = planned();
    assert!(
        doc.inventory
            .slots
            .iter()
            .any(|s| s.name.contains('\u{e9}')),
        "fixture should still exercise the accent path"
    );
    assert!(!result.grants.iter().any(|g| g.label.is_empty()));
    assert!(result.unresolved.is_empty());
}

#[test]
fn spells_are_memorised_in_slot_order() {
    let (doc, result) = planned();
    assert_eq!(result.equip_spells.len(), doc.spells.slots.len());
    // Great Oracular Bubble is slot 0 and has param id 5110.
    assert_eq!(result.equip_spells[0], 5110);
}

#[test]
fn grant_count_matches_the_build() {
    let (doc, result) = planned();
    let armour: usize = doc.protectors.values().map(|p| p.slots.len()).sum();
    let expected = doc.inventory.slots.len()
        + doc.spells.slots.len()
        + doc.talismans.slots.len()
        + armour
        + doc.items.tools.slots.len();
    assert_eq!(result.grants.len(), expected);
}

#[test]
fn every_grant_encodes_to_sixteen_bytes() {
    let (_, result) = planned();
    for grant in &result.grants {
        let record = grant.to_record();
        assert_eq!(&record[0..4], &grant.item_id.to_le_bytes());
        assert_eq!(
            &record[10..12],
            &[0, 0],
            "the reserved half-word stays zero"
        );
    }
}

#[test]
fn unenchanted_items_carry_no_skill_and_no_upgrade() {
    let (_, result) = planned();
    let talisman = result
        .grants
        .iter()
        .find(|g| g.label == "Radagon Icon")
        .expect("talisman");
    assert_eq!(talisman.item_id, 0x20000BFE);
    assert_eq!(talisman.reinforce_lv, 0);
    assert_eq!(talisman.weapon_skill, NO_SKILL);
}

#[test]
fn share_ids_are_extracted_and_the_offline_form_is_rejected() {
    assert_eq!(
        share_id_from_url("https://er-build-planner.nyasu.business/?b=af97a9da874151"),
        Some("af97a9da874151")
    );
    // `?i=` is the self-contained payload form and needs no fetch.
    assert_eq!(
        share_id_from_url("https://er-build-planner.nyasu.business/?i=uwuABC"),
        None
    );
    assert_eq!(
        share_id_from_url("https://er-build-planner.nyasu.business/"),
        None
    );
}

#[test]
fn name_collisions_across_categories_stay_distinct() {
    // `Golden Vow` is a spell, an ash of war and a consumable. A flat map would
    // grant whichever happened to be inserted last.
    let catalog = fixture_catalog::catalog();
    use er_build_import::Catalog;
    let spell = catalog.lookup(Kind::Spell, "Golden Vow");
    let ash = catalog.lookup(Kind::AshOfWar, "Golden Vow");
    if let (Some(spell), Some(ash)) = (spell, ash) {
        assert_ne!(spell.full_item_id, ash.full_item_id);
    }
}

// ---------------------------------------------------------------- equipping

use er_build_import::equip::{Capacity, PHYSICK_SLOTS, QUICKBAR_SLOTS, equip_plan};

fn equipped() -> er_build_import::EquipPlan {
    let doc = model::parse(BUILD).expect("fixture parses");
    equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default())
}

#[test]
fn armaments_land_in_the_slots_the_author_chose() {
    let plan = equipped();
    assert!(plan.is_complete(), "rejected: {:?}", plan.rejected);
    let names: Vec<_> = plan
        .armaments
        .iter()
        .map(|slot| slot.as_ref().map(|item| item.name.as_str()))
        .collect();
    assert_eq!(
        names,
        vec![
            Some("Deadly Poison Perfume Bottle"),
            Some("Albinauric Staff"),
            Some("Mis\u{e9}ricorde"),
            Some("Poisoned Hand"),
            Some("Azur's Glintstone Staff"),
            None,
        ]
    );
}

#[test]
fn all_four_armour_pieces_are_worn() {
    let plan = equipped();
    assert_eq!(
        plan.head.as_ref().map(|i| i.name.as_str()),
        Some("Mushroom Crown")
    );
    assert_eq!(
        plan.body.as_ref().map(|i| i.name.as_str()),
        Some("Armor of Solitude")
    );
    assert_eq!(
        plan.arms.as_ref().map(|i| i.name.as_str()),
        Some("Young Lion's Gauntlets")
    );
    assert_eq!(
        plan.legs.as_ref().map(|i| i.name.as_str()),
        Some("Greaves of Solitude")
    );
}

#[test]
fn talismans_fill_every_slot_in_order() {
    let plan = equipped();
    let names: Vec<_> = plan
        .talismans
        .iter()
        .map(|s| s.as_ref().map(|i| i.name.as_str()))
        .collect();
    assert_eq!(
        names,
        vec![
            Some("Radagon Icon"),
            Some("Erdtree's Favor +2"),
            Some("Graven-Mass Talisman"),
            Some("Ritual Shield Talisman"),
        ]
    );
}

#[test]
fn spells_are_memorised_in_order_and_fit() {
    let plan = equipped();
    assert_eq!(plan.spells.len(), 8);
    assert_eq!(plan.spells[0].name, "Great Oracular Bubble");
    assert_eq!(plan.spells[7].name, "Scholar's Armament");
}

#[test]
fn spells_beyond_the_memory_slots_are_refused_not_dropped() {
    let doc = model::parse(BUILD).expect("fixture parses");
    let cramped = Capacity {
        memory_slots: 3,
        ..Capacity::default()
    };
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), cramped);
    assert_eq!(plan.spells.len(), 3);
    assert_eq!(plan.rejected.len(), 5);
    assert!(
        plan.rejected
            .iter()
            .all(|r| r.reason.contains("memory slots"))
    );
    assert!(
        !plan.is_complete(),
        "an overrun must be visible, never silent"
    );
}

#[test]
fn talisman_slots_shrink_with_capacity() {
    let doc = model::parse(BUILD).expect("fixture parses");
    let cramped = Capacity {
        talismans: 2,
        ..Capacity::default()
    };
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), cramped);
    assert_eq!(plan.talismans.len(), 2);
    assert_eq!(
        plan.rejected.len(),
        2,
        "the two overflow talismans must be reported"
    );
}

#[test]
fn empty_quickbar_pouch_and_physick_stay_empty() {
    // This build equips no tools and no tears; the plan must not invent any.
    let plan = equipped();
    assert_eq!(plan.quickbar.len(), QUICKBAR_SLOTS);
    assert_eq!(plan.physick.len(), PHYSICK_SLOTS);
    assert!(plan.quickbar.iter().all(Option::is_none));
    assert!(plan.pouch.iter().all(Option::is_none));
    assert!(plan.physick.iter().all(Option::is_none));
    assert!(plan.great_rune.is_none());
}

#[test]
fn quickbar_and_pouch_split_at_ten() {
    // Synthetic: the fixture build equips no tools, so the boundary the planner
    // renders (`equipIndex < 10` is quickbar, `>= 10` is pouch) is pinned here.
    let doc = model::parse(
        r#"{"items":{"tools":{"slots":[
             {"name":"Fingerprint Nostrum","equipIndex":0},
             {"name":"Fingerprint Nostrum","equipIndex":9},
             {"name":"Fingerprint Nostrum","equipIndex":10},
             {"name":"Fingerprint Nostrum","equipIndex":15}]}}}"#,
    )
    .expect("synthetic doc parses");
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default());
    assert!(plan.is_complete(), "rejected: {:?}", plan.rejected);
    assert!(plan.quickbar[0].is_some() && plan.quickbar[9].is_some());
    assert!(plan.pouch[0].is_some() && plan.pouch[5].is_some());
    assert_eq!(plan.quickbar.iter().filter(|s| s.is_some()).count(), 2);
    assert_eq!(plan.pouch.iter().filter(|s| s.is_some()).count(), 2);
}

#[test]
fn an_out_of_range_position_is_reported_rather_than_silently_clamped() {
    let doc = model::parse(
        r#"{"items":{"tools":{"slots":[{"name":"Fingerprint Nostrum","equipIndex":99}]}}}"#,
    )
    .expect("synthetic doc parses");
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default());
    assert!(!plan.is_complete());
    assert!(plan.rejected[0].reason.contains("out of range"));
}
