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

// ------------------------------------------------------------------ loadout sets

/// The 22634-byte body of `GET /inventories/82086df03c4b8e`, a build carrying six
/// armament sets, six talisman sets and three armour sets. Kept whole rather than
/// trimmed: the collisions this exercises live in rows that a "tidy" fixture would
/// have deleted as noise.
const SETS_BUILD: &str = include_str!("fixtures/build-82086df03c4b8e.json");

fn sets_build() -> model::BuildDoc {
    model::parse(SETS_BUILD).expect("sets fixture parses")
}

fn armament_names(plan: &er_build_import::EquipPlan) -> Vec<Option<&str>> {
    plan.armaments
        .iter()
        .map(|slot| slot.as_ref().map(|item| item.name.as_str()))
        .collect()
}

#[test]
fn the_payload_carries_named_sets_with_one_active_per_category() {
    let doc = sets_build();
    let names: Vec<_> = doc.sets.weapons.iter().map(|s| s.name.as_str()).collect();
    assert_eq!(
        names,
        vec!["Default", "PSGKAT", "2H", "PS Katana", "Urumi", "Bows"]
    );
    // Three categories, each with its own switch -- not one build-wide index.
    assert_eq!(doc.sets.active_weapons(), 0);
    assert_eq!(doc.sets.active_talismans(), 0);
    assert_eq!(doc.sets.active_protectors(), 0);
    assert_eq!(doc.sets.talismans.len(), 6);
    assert_eq!(doc.sets.protectors.len(), 3);
}

#[test]
fn equip_index_is_only_a_cache_of_the_active_sets_entry() {
    // The invariant `setSlotEquipIndex` maintains, checked against every row of a
    // real six-set build: `equipIndex == equipSet[active]`, always. That is why
    // reading `equipSet` selects the same rows -- and why it is safe to stop
    // trusting `equipIndex` and derive the selection instead.
    let doc = sets_build();
    let lists = [&doc.inventory, &doc.talismans];
    let mut checked = 0;
    for list in lists.into_iter().chain(doc.protectors.values()) {
        for slot in &list.slots {
            if slot.equip_set.is_none() && slot.equip_index.is_none() {
                continue;
            }
            assert_eq!(
                slot.equip_index,
                slot.equip_index_in_set(0),
                "{} disagrees with its own equipSet",
                slot.name
            );
            checked += 1;
        }
    }
    assert_eq!(
        checked, 43,
        "19 armament rows, 14 talismans and 10 armour pieces carry a set assignment"
    );
}

#[test]
fn only_the_active_armament_set_is_equipped() {
    let plan = equip_plan(
        &sets_build(),
        &fixture_catalog::catalog(),
        Capacity::default(),
    );
    assert_eq!(
        armament_names(&plan),
        vec![
            Some("Shamshir"),
            Some("Mis\u{e9}ricorde"),
            None,
            Some("Mis\u{e9}ricorde"),
            Some("Frenzied Flame Seal"),
            Some("Twinbird Kite Shield"),
        ]
    );
    // Rows that belong to PSGKAT / 2H / PS Katana / Urumi / Bows must not appear
    // anywhere -- not placed, and not rejected either, because they were never
    // candidates for this loadout.
    for name in [
        "Nagakiba",
        "Guardian's Swordspear",
        "Rakshasa's Great Katana",
    ] {
        assert!(
            !plan.armaments.iter().flatten().any(|i| i.name == name),
            "{name} belongs to another set"
        );
        assert!(
            !plan.rejected.iter().any(|r| r.name == name),
            "{name} should not even be considered"
        );
    }
}

#[test]
fn the_contested_armament_positions_go_the_way_the_planner_shows_them() {
    // Three rows claim armament position 0 and three claim position 4, all inside
    // the ACTIVE set. The planner's own `WeaponEquipSlots` folds them with a
    // `reduce` that overwrites, so the last row in the list is what its author
    // sees, and that is what gets equipped.
    let plan = equip_plan(
        &sets_build(),
        &fixture_catalog::catalog(),
        Capacity::default(),
    );
    let contested: Vec<_> = plan
        .contested
        .iter()
        .map(|c| {
            (
                c.position.as_str(),
                c.winner.as_str(),
                c.losers.iter().map(String::as_str).collect::<Vec<_>>(),
            )
        })
        .collect();
    assert_eq!(
        contested,
        vec![
            (
                "armament slot 0",
                "Shamshir",
                vec!["Mis\u{e9}ricorde", "Shamshir"]
            ),
            (
                "armament slot 1",
                "Mis\u{e9}ricorde",
                vec!["Mis\u{e9}ricorde"]
            ),
            (
                "armament slot 4",
                "Frenzied Flame Seal",
                vec!["Mis\u{e9}ricorde", "Frenzied Flame Seal"]
            ),
        ]
    );
}

#[test]
fn a_losing_claimant_is_rejected_so_no_caller_can_miss_the_collision() {
    // The whole point: a plan that quietly drops five armaments is
    // indistinguishable from a plan that placed them. `rejected` is what the
    // runtime already logs, so the losers go there too rather than only into the
    // structured list.
    let plan = equip_plan(
        &sets_build(),
        &fixture_catalog::catalog(),
        Capacity::default(),
    );
    assert!(
        !plan.is_complete(),
        "a contested build is not a clean import"
    );
    let losses: Vec<_> = plan
        .rejected
        .iter()
        .map(|r| (r.name.as_str(), r.reason.as_str()))
        .collect();
    assert_eq!(
        losses,
        vec![
            (
                "Mis\u{e9}ricorde",
                "armament slot 0 already claimed by \"Shamshir\""
            ),
            (
                "Shamshir",
                "armament slot 0 already claimed by \"Shamshir\""
            ),
            (
                "Mis\u{e9}ricorde",
                "armament slot 1 already claimed by \"Mis\u{e9}ricorde\""
            ),
            (
                "Mis\u{e9}ricorde",
                "armament slot 4 already claimed by \"Frenzied Flame Seal\""
            ),
            (
                "Frenzied Flame Seal",
                "armament slot 4 already claimed by \"Frenzied Flame Seal\""
            ),
        ]
    );
    assert_eq!(
        plan.contested.len(),
        3,
        "five losses across three positions"
    );
}

#[test]
fn the_contest_winner_keeps_its_own_affinity() {
    // Position 1 is claimed by two Miséricordes that differ ONLY by affinity:
    // a Keen one earlier in the list and a Lightning one later. Asserting on the
    // name alone would pass either way, so pin the id: last-wins must bring the
    // Lightning row's `+600`, not the Keen row's `+200`.
    let plan = equip_plan(
        &sets_build(),
        &fixture_catalog::catalog(),
        Capacity::default(),
    );
    const MISERICORDE: u32 = 0x000F_B770;
    let slot = plan.armaments[1].as_ref().expect("position 1 is filled");
    assert_eq!(slot.item_id, MISERICORDE + 600, "Lightning, not Keen");
    let slot = plan.armaments[0].as_ref().expect("position 0 is filled");
    assert_eq!(slot.item_id, 0x006B_44F0 + 200, "the Keen Shamshir");
}

#[test]
fn talismans_and_armour_resolve_against_their_own_sets() {
    let plan = equip_plan(
        &sets_build(),
        &fixture_catalog::catalog(),
        Capacity::default(),
    );
    let talismans: Vec<_> = plan
        .talismans
        .iter()
        .map(|s| s.as_ref().map(|i| i.name.as_str()))
        .collect();
    assert_eq!(
        talismans,
        vec![
            Some("Blue-Feathered Branchsword"),
            Some("Crimson Amber Medallion +3"),
            Some("Erdtree's Favor +2"),
            Some("Bull-Goat's Talisman"),
        ]
    );
    assert_eq!(
        plan.head.as_ref().map(|i| i.name.as_str()),
        Some("Divine Beast Helm")
    );
    assert_eq!(
        plan.body.as_ref().map(|i| i.name.as_str()),
        Some("Armor of Solitude")
    );
    assert_eq!(
        plan.arms.as_ref().map(|i| i.name.as_str()),
        Some("Gauntlets of Solitude")
    );
    assert_eq!(
        plan.legs.as_ref().map(|i| i.name.as_str()),
        Some("Greaves of Solitude")
    );
    // Rakshasa is the armour set 2 wears; set 0 must not pick any of it up.
    for worn in [&plan.head, &plan.body, &plan.arms, &plan.legs] {
        let name = worn.as_ref().map(|i| i.name.as_str()).unwrap_or_default();
        assert!(!name.starts_with("Rakshasa"), "{name} is set 2's armour");
    }
}

#[test]
fn switching_the_active_armament_set_switches_only_the_armaments() {
    // `sets` is per category, so activating the "PS Katana" armament set must not
    // disturb the talismans or armour -- which is exactly the mistake a single
    // build-wide active index would make.
    let mut doc = sets_build();
    doc.sets.weapons[0].active = false;
    doc.sets.weapons[3].active = true;
    assert_eq!(doc.sets.weapons[3].name, "PS Katana");
    assert_eq!(doc.sets.active_weapons(), 3);

    let plan = equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default());
    assert_eq!(
        armament_names(&plan),
        vec![
            Some("Nagakiba"),
            None,
            None,
            Some("Nagakiba"),
            Some("Frenzied Flame Seal"),
            None,
        ]
    );
    // Not one Miséricorde survives: every one of them is a set-0 row.
    assert!(
        !plan
            .armaments
            .iter()
            .flatten()
            .any(|i| i.name.contains('\u{e9}')),
        "set 3 has no daggers"
    );
    assert_eq!(
        plan.talismans[0].as_ref().map(|i| i.name.as_str()),
        Some("Blue-Feathered Branchsword"),
        "the talisman set did not move"
    );
    assert_eq!(
        plan.head.as_ref().map(|i| i.name.as_str()),
        Some("Divine Beast Helm"),
        "the armour set did not move"
    );
}

#[test]
fn a_row_carrying_only_equip_index_still_equips() {
    // Builds authored before the planner grew sets have no `sets` key and no
    // `equipSet`. The planner migrates such a row to `equipSet = [equipIndex]`,
    // i.e. set 0, and so must the importer -- otherwise every older share link
    // imports a naked character.
    let doc = model::parse(
        r#"{"inventory":{"slots":[{"name":"Shamshir","order":0,"equipIndex":2}]},
            "talismans":{"slots":[{"name":"Radagon Icon","order":0,"equipIndex":1}]}}"#,
    )
    .expect("legacy doc parses");
    assert!(doc.sets.weapons.is_empty());
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default());
    assert!(plan.is_complete(), "rejected: {:?}", plan.rejected);
    assert_eq!(armament_names(&plan)[2], Some("Shamshir"));
    assert_eq!(
        plan.talismans[1].as_ref().map(|i| i.name.as_str()),
        Some("Radagon Icon")
    );
}

#[test]
fn a_row_equipped_only_in_an_inactive_set_is_left_carried() {
    // The `null` holes are the whole encoding: this row is position 0 of set 1,
    // and nothing at all in the active set 0.
    let doc = model::parse(
        r#"{"sets":{"weapons":[{"name":"Default","active":true},{"name":"Bows"}]},
            "inventory":{"slots":[{"name":"Shamshir","order":0,"equipSet":[null,0]}]}}"#,
    )
    .expect("doc parses");
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default());
    assert!(plan.armaments.iter().all(Option::is_none));
    assert!(plan.rejected.is_empty(), "not equipped is not a failure");
    assert!(plan.contested.is_empty());
}

#[test]
fn armour_takes_the_first_claimant_the_way_the_planner_does() {
    // `ProtectorEquipSlots` resolves with `find`, not a fold, so armour breaks a
    // tie the opposite way to armaments. And the VALUE is meaningless: the planner
    // writes a constant `1` for every worn piece, so it is membership that counts.
    let doc = model::parse(
        r#"{"sets":{"protectors":[{"name":"Default","active":true}]},
            "protectors":{"head":{"slots":[
                {"name":"Divine Beast Helm","order":0,"equipSet":[1],"equipIndex":1},
                {"name":"Mushroom Crown","order":1,"equipSet":[1],"equipIndex":1}]}}}"#,
    )
    .expect("doc parses");
    let plan = equip_plan(&doc, &fixture_catalog::catalog(), Capacity::default());
    assert_eq!(
        plan.head.as_ref().map(|i| i.name.as_str()),
        Some("Divine Beast Helm")
    );
    assert_eq!(plan.contested.len(), 1);
    assert_eq!(plan.contested[0].position, "head armour");
    assert_eq!(plan.contested[0].losers, vec!["Mushroom Crown"]);
}

#[test]
fn the_rest_of_the_real_build_still_imports_whole() {
    // Everything the sets machinery does NOT touch: tools carry no `equipSet` at
    // all, and spells, tears and the great rune are set-free too.
    let plan = equip_plan(
        &sets_build(),
        &fixture_catalog::catalog(),
        Capacity::default(),
    );
    assert_eq!(
        plan.quickbar[5].as_ref().map(|i| i.name.as_str()),
        Some("Clarifying Boluses")
    );
    assert_eq!(
        plan.quickbar[7].as_ref().map(|i| i.name.as_str()),
        Some("Neutralizing Boluses")
    );
    assert_eq!(
        plan.pouch[0].as_ref().map(|i| i.name.as_str()),
        Some("Blessing of Marika")
    );
    assert_eq!(
        plan.pouch[2].as_ref().map(|i| i.name.as_str()),
        Some("Flask of Cerulean Tears")
    );
    assert_eq!(
        plan.physick[0].as_ref().map(|i| i.name.as_str()),
        Some("Crimsonwhorl Bubbletear")
    );
    assert_eq!(plan.spells.len(), 1);
    assert_eq!(plan.spells[0].name, "Bestial Vitality");
    assert_eq!(
        plan.great_rune.as_ref().map(|i| i.name.as_str()),
        Some("Mohg's Great Rune")
    );
    assert!(plan.two_handing);
}
