//! End-to-end: a real planner payload becomes a complete, correct grant list.
//!
//! The fixture is the exact 6871-byte body returned by
//! `GET https://er-inventory-api.nyasu.business/inventories/af97a9da874151`,
//! captured unauthenticated. Its catalog rows come from the planner's public
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
    // The build spells it `Miséricorde`; the catalog key is `Misericorde`.
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

// ------------------------------------------------------------------ er-effects.toml `build_url`

/// The exact block `er-effects-rs`'s `boilerplate_config` writes into a fresh `er-effects.toml`,
/// minus the picker block. This is the file a player actually edits, so the scanner is held to it
/// rather than to a convenient shape: the commented example must NOT be read as a value, and the
/// key must survive being surrounded by the other keys' comments.
const PRODUCT_BOILERPLATE: &str = "\
# er-effects-rs runtime config (auto-created next to the game executable).
# All keys are optional; uncomment and edit as needed.
#
# save_file = 'C:\\path\\to\\ER0000.sl2'  # explicit read-only source
# slot = 0                               # character slot the autoload selects
# os_native_save_picker = false          # false=in-game browser, true=OS file dialog
# save_suppression_enabled = false
# build_url = 'https://er-build-planner.example/?b=af97a9da874151'
# The er-build-planner share link the System>Quit \"Load Build from URL\" row imports onto the
# character you are playing.
";

#[test]
fn the_untouched_product_config_configures_no_build() {
    // Every `build_url` in the shipped file is commented out, so a player who never edited it must
    // get "nothing to import" -- not the example link, which is not their build.
    assert_eq!(
        er_build_import::build_url_from_config(PRODUCT_BOILERPLATE),
        None
    );
}

#[test]
fn a_configured_build_url_is_read_in_every_spelling_the_file_allows() {
    for (line, expected) in [
        ("build_url = 'https://p/?b=abc123'", "https://p/?b=abc123"),
        ("build_url = \"https://p/?b=abc123\"", "https://p/?b=abc123"),
        ("build_url=https://p/?b=abc123", "https://p/?b=abc123"),
        (
            "   build_url   =   'https://p/?b=abc123'   ",
            "https://p/?b=abc123",
        ),
    ] {
        let contents = format!("{PRODUCT_BOILERPLATE}{line}\n");
        assert_eq!(
            er_build_import::build_url_from_config(&contents),
            Some(expected),
            "{line:?}"
        );
    }
}

#[test]
fn an_empty_or_absent_value_imports_nothing_rather_than_an_empty_build() {
    for contents in [
        "build_url =\n",
        "build_url = ''\n",
        "build_url = \"\"\n",
        "",
    ] {
        assert_eq!(
            er_build_import::build_url_from_config(contents),
            None,
            "{contents:?}"
        );
    }
}

/// The two halves have to agree: a value the scanner accepts still has to carry a share id, and the
/// self-contained `?i=` form -- which needs no network at all -- must be refused rather than fetched.
#[test]
fn the_configured_url_feeds_the_share_id_extractor() {
    let contents = "build_url = 'https://er-build-planner.example/?b=af97a9da874151'\n";
    let url = er_build_import::build_url_from_config(contents).expect("configured");
    assert_eq!(share_id_from_url(url), Some("af97a9da874151"));

    let self_contained = "build_url = 'https://er-build-planner.example/?i=eyJ2IjoxfQ'\n";
    let url = er_build_import::build_url_from_config(self_contained).expect("configured");
    assert_eq!(share_id_from_url(url), None);
}

// ------------------------------------------------------------------ in-game URL entry

use er_build_import::{BUILD_URL_PREFIX, UrlRejection, validate_build_url};

/// The editor opens pre-filled with the prefix, so the untouched field must REFUSE. If it did not,
/// pressing Accept without typing would start a fetch for a build id that is the empty string.
#[test]
fn the_untouched_prefill_is_refused() {
    assert_eq!(
        validate_build_url(BUILD_URL_PREFIX),
        Err(UrlRejection::Empty)
    );
    assert_eq!(validate_build_url(""), Err(UrlRejection::Empty));
    assert_eq!(validate_build_url("   "), Err(UrlRejection::Empty));
}

/// Completing the prefill -- the exact thing a player does -- must be accepted.
#[test]
fn typing_an_id_onto_the_prefix_is_accepted() {
    let typed = format!("{BUILD_URL_PREFIX}af97a9da874151");
    assert_eq!(validate_build_url(&typed), Ok("af97a9da874151"));
    // ...and a pasted-in link from any host, since the planner has moved domain before.
    assert_eq!(
        validate_build_url("https://somewhere.else/x?b=bc2a932db14675"),
        Ok("bc2a932db14675")
    );
}

/// A controller-typed field picks up stray whitespace; a trailing space is a slip, not a link.
#[test]
fn surrounding_whitespace_does_not_change_the_link() {
    let padded = format!("  {BUILD_URL_PREFIX}af97a9da874151\t");
    assert_eq!(validate_build_url(&padded), Ok("af97a9da874151"));
}

/// Every refusal names itself, and no two share a code -- the codes are the telemetry wire format.
#[test]
fn every_rejection_has_a_distinct_code_and_a_sentence() {
    let cases = [
        ("", UrlRejection::Empty),
        ("https://p/?i=eyJ2IjoxfQ", UrlRejection::SelfContained),
        ("https://p/?other=1", UrlRejection::NoShareId),
        ("https://p/no-query-at-all", UrlRejection::NoShareId),
        ("https://p/?b=", UrlRejection::MalformedShareId),
        ("https://p/?b=not an id", UrlRejection::MalformedShareId),
        ("https://p/?b=../../etc", UrlRejection::MalformedShareId),
    ];
    let mut codes = Vec::new();
    for (url, want) in cases {
        assert_eq!(validate_build_url(url), Err(want), "{url:?}");
        assert!(!want.indicator().is_empty(), "{want:?} has no sentence");
        assert_ne!(want.code(), 0, "0 is reserved for accepted");
        codes.push(want.code());
    }
    codes.sort_unstable();
    codes.dedup();
    assert_eq!(codes.len(), 4, "rejection codes must be distinct");
}

/// The gate and the fetcher must never disagree: anything this accepts has to yield the SAME id to
/// `share_id_from_url`, which is what actually builds the request path. A link that validates but
/// does not resolve would be refused by nothing and then fetch nothing.
#[test]
fn acceptance_agrees_with_the_share_id_the_fetch_will_use() {
    for url in [
        "https://er-build-planner.nyasu.business/?b=af97a9da874151",
        "https://p/?x=1&b=abc123&y=2",
        "https://p/?b=abc123#fragment",
    ] {
        let accepted = validate_build_url(url).expect("valid");
        assert_eq!(Some(accepted), share_id_from_url(url), "{url:?}");
    }
    // ...and the converse: nothing this refuses may still resolve.
    for url in [
        "https://p/?b=",
        "https://p/?i=payload",
        "https://p/?b=bad id",
    ] {
        assert!(validate_build_url(url).is_err(), "{url:?}");
        assert_eq!(share_id_from_url(url), None, "{url:?}");
    }
}

// ---------------------------------------------------------------------------------------------
// THE EXPORT DIRECTION. Every helper below is used by the Generate Build Link row to turn the LIVE
// character back into a planner document, and every one of them is the inverse of something the
// importer does. An inverse that is subtly wrong does not fail loudly -- it produces a build that
// looks right and comes back a different weapon, or hand-swapped. So the pairs are tested as pairs.

#[test]
fn every_affinity_the_importer_adds_the_exporter_can_subtract() {
    use er_build_import::plan::{
        INFUSION_STEP, infusion_names, infusion_offset, split_armament_id,
    };

    // Misericorde, the worked example in the crate docs.
    const BASE: u32 = 1_070_000;
    for (index, name) in infusion_names().enumerate() {
        let offset = infusion_offset(Some(name))
            .unwrap_or_else(|| panic!("the importer must know the affinity {name:?}"));
        assert_eq!(offset, index as u32 * INFUSION_STEP, "{name} offset");

        let (base, read_back) = split_armament_id(BASE + offset);
        assert_eq!(base, BASE, "{name} base row");
        // Standard is index 0 and is spelled as an ABSENT field, never as the word.
        let expected = (index != 0).then_some(name);
        assert_eq!(read_back, expected, "{name} round trip");
    }
}

#[test]
fn an_id_carrying_no_recognisable_affinity_is_taken_whole() {
    use er_build_import::plan::split_armament_id;

    // Offset 1300 is past the last affinity (Occult, 1200). Subtracting an invented amount would
    // silently rename the weapon, so the id is left alone and reported as having no affinity.
    assert_eq!(split_armament_id(1_071_300), (1_071_300, None));
}

#[test]
fn the_armament_hand_map_is_a_bijection_in_both_directions() {
    use er_build_import::equip::{ARMAMENT_CHR_ASM_SLOTS, armament_planner_index, armament_slot};

    // Six planner indices onto six distinct ChrAsm slots, and back again.
    let mut seen = ARMAMENT_CHR_ASM_SLOTS;
    seen.sort_unstable();
    assert_eq!(
        seen,
        [0, 1, 2, 3, 4, 5],
        "the six hand slots, each used once"
    );

    for planner_index in 0..6u32 {
        let slot = armament_slot(planner_index).expect("every planner index maps to a slot");
        assert_eq!(
            armament_planner_index(slot),
            Some(planner_index),
            "slot {slot} must map back to planner index {planner_index}"
        );
    }
    // Out of range in both directions is None, not a wrapped index.
    assert_eq!(armament_slot(6), None);
    assert_eq!(armament_planner_index(6), None);
    assert_eq!(armament_planner_index(-1), None);

    // The hand convention itself: planner 0..2 is the RIGHT hand (odd slots), 3..5 the LEFT.
    // If imported builds ever come out hand-swapped, this assertion is the one to invert, together
    // with `ARMAMENT_CHR_ASM_SLOTS` -- and inverting the table alone will fail here first.
    assert!(
        ARMAMENT_CHR_ASM_SLOTS[0..3]
            .iter()
            .all(|slot| slot % 2 == 1)
    );
    assert!(
        ARMAMENT_CHR_ASM_SLOTS[3..6]
            .iter()
            .all(|slot| slot % 2 == 0)
    );
}

#[test]
fn the_armour_slots_are_consecutive_from_protector_head() {
    use er_build_import::equip::{CHR_ASM_SLOT_PROTECTOR_HEAD, PROTECTOR_PARTS};

    // `ProtectorIndexToChrAsmSlot` is literally `index + ProtectorHead`, so the four planner keys
    // must be in that order -- the exporter walks them by offset and would mislabel armour if the
    // order drifted.
    assert_eq!(PROTECTOR_PARTS, ["head", "body", "arms", "legs"]);
    assert_eq!(CHR_ASM_SLOT_PROTECTOR_HEAD, 12);
}
