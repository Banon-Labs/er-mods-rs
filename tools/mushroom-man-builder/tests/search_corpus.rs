//! What `--search` makes of the whole Dark Souls Remastered install, and that every character it
//! offers gets through every stage of the build that needs no Elden Ring archive.
//!
//! Without the donor the search reads each model and its textures and runs the map report; with
//! it, it also makes the fit, the slots, the rebuilt FLVER and the body TPF. Every character the
//! fitted search offers is then built here the rest of the way the package builds it -- the Edge
//! and set materials retargeted when a slot needs them, both body TPFs with every set's maps, and
//! `verify-package`'s FLVER check against them -- on the donor out of the unpacked corpus. What is
//! left to the Windows build is reading and writing Elden Ring's Kraken-compressed archives. A
//! character the search offers that fails here is a search that promises too much.
//!
//! Corpus-gated: reads the Dark Souls Remastered install, and the donor and both `C[AMSN]`
//! templates out of the unpacked Elden Ring corpus (see `common`), and skips without them.

mod common;

use std::collections::BTreeMap;

use common::{donor_or_skip, dsr_with_or_skip, part_or_skip};
use er_tpf::Tpf;
use mushroom_man_builder::{
    character::{self, FitConfig},
    edge::{
        OPAQUE_TEMPLATE_FLVER, TEMPLATE_FLVER, Templates, edge_template, opaque_template,
        retarget_materials,
    },
    error::{Error, Result},
    flver::{
        rebuild::{HeaderAppend, rebuild},
        verify::check_flver,
    },
    package::{Cutouts, biped_body, read_character},
    search::{self, Hit, Verdict},
    textures::body_tpf,
};

const TEMPLATE_BINDER: &str = "bd_f_1451-partsbnd-dcx";
const OPAQUE_TEMPLATE_BINDER: &str = "bd_m_1711-partsbnd-dcx";
const BODY_STEM: &str = "FC_M_0000";

/// The verdicts over the install, by how many models get each, without the donor and with it:
/// measured 2026-10-15. The fit turns down c2940 (its thighs are roots) and c3110 (a lizard on a
/// Biped rig, its pelvis 0.13 m up), and the alpha-blended characters that are four-legged on a
/// Biped rig (the four rats, Sif and Young Sif) or have no left upper arm (Four Kings). The one
/// texture refusal is c4091, whose albedo is in no binder. Before characters with several texture
/// sets were built, 26 more stopped there; before alpha blending had a route, 13 more were refused.
///
/// FromSoft's own rig names and the Bloatheads' numbered ones let 11 more in as Biped: six fit
/// (c2260, c2270, c2830, c4130 Scarecrow, c4150 and c4160 the Bloatheads), the two crows (c3510,
/// c3511) are turned down for heels in front of their toes, c5220 Nito for 20 materials against 15
/// donor body meshes, c4500 Manus for a calf twist off its calf, and c5310 Gwynevere for a root
/// bone `ex` carrying her dress.
///
/// Legs shorter than the trunk are refused as too far from a human's proportions: c2270 Mushroom
/// Parent, which the user left out after seeing it in game, and c3450 Stone Dragon. Folding
/// materials that draw alike into one slot fits Nito.
const UNFITTED: [(&str, usize); 6] = [
    ("maps", 78),
    ("no model", 8),
    ("not biped", 89),
    ("own fit", 1),
    ("refused", 16),
    ("textures", 1),
];
const FITTED: [(&str, usize); 7] = [
    ("fit", 11),
    ("fits", 67),
    ("no model", 8),
    ("not biped", 89),
    ("own fit", 1),
    ("refused", 16),
    ("textures", 1),
];

fn kind(verdict: &Verdict) -> &'static str {
    match verdict {
        Verdict::Maps { fitted: true, .. } => "fits",
        Verdict::Maps { fitted: false, .. } => "maps",
        Verdict::Fit(_) => "fit",
        Verdict::OwnFit => "own fit",
        Verdict::NoModel => "no model",
        Verdict::NotBiped => "not biped",
        Verdict::Refused(_) => "refused",
        Verdict::Textures(_) => "textures",
        Verdict::Missing => "missing",
        Verdict::Unreadable(_) => "unreadable",
    }
}

fn tally(hits: &[Hit]) -> Vec<(&'static str, usize)> {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for hit in hits {
        *counts.entry(kind(&hit.verdict)).or_default() += 1;
    }
    counts.into_iter().collect()
}

fn verdict(hits: &[Hit], id: &str) -> Verdict {
    hits.iter()
        .find(|hit| hit.id == id)
        .unwrap()
        .verdict
        .clone()
}

#[test]
fn without_the_donor_the_search_sorts_every_model_short_of_the_fit() {
    let test = "without_the_donor_the_search_sorts_every_model_short_of_the_fit";
    let Some(dsr) = dsr_with_or_skip(test, "c2500") else {
        return;
    };
    let hits = search::search(&dsr, "c", None).unwrap();
    assert_eq!(hits.len(), 193, "one hit per chrbnd");
    assert_eq!(tally(&hits), UNFITTED);
    assert_eq!(
        verdict(&hits, "c2500"),
        Verdict::Maps {
            vertices: 1956,
            triangles: 2547,
            materials: 2,
            fitted: false
        },
        "the counts the c2500 build reported"
    );
    assert_eq!(verdict(&hits, "c2280"), Verdict::OwnFit);
    // Quelaag's lettered limb sets (`W_LArm11`, `LLeg_d_1`) are not a Biped's.
    assert_eq!(verdict(&hits, "c5280"), Verdict::NotBiped);
    for id in ["c2270", "c3450"] {
        assert!(
            matches!(verdict(&hits, id), Verdict::Refused(why) if why.contains("shorter than its trunk")),
            "{id}"
        );
    }
    // Split textures, its own (c2320) and borrowed (c2230 draws with c2239's).
    assert!(verdict(&hits, "c2320").maps());
    assert!(verdict(&hits, "c2230").maps());
    // c4091 draws with c4090's albedo and specular, and c4090's TPF holds neither.
    assert!(matches!(verdict(&hits, "c4091"), Verdict::Textures(why) if why.contains("albedo")));
    // Two texture sets (body and cloth), and five drawn out of c2799's binder.
    assert!(verdict(&hits, "c2060").maps());
    assert!(verdict(&hits, "c2792").maps());

    let hollows = search::search(&dsr, "hollow", None).unwrap();
    let found: Vec<(&str, Option<&str>, bool)> = hollows
        .iter()
        .map(|hit| (hit.id.as_str(), hit.name, hit.verdict.maps()))
        .collect();
    assert_eq!(
        found,
        [
            ("c2500", Some("Hollow"), true),
            ("c2540", Some("Armored Hollow"), true),
            ("c2820", Some("Prototype Claw Hollow"), true)
        ]
    );
}

/// Everything the package does to `id` but the archives, as one line for the log.
fn build_offline(
    dsr: &std::path::Path,
    donor: &[u8],
    templates: &Templates,
    id: &str,
) -> Result<String> {
    let character = character::resolve(id, true)?;
    if !matches!(character.fit, FitConfig::Biped(_)) {
        return Err(Error::new(format!("{id} is not fitted as a Biped")));
    }
    let files = read_character(dsr, character)?;
    let body = biped_body(
        donor,
        character,
        &files.flver,
        &files.textures,
        Cutouts::EdgeMaterial,
    )?;
    let append = if body.retargets.is_empty() {
        HeaderAppend::default()
    } else {
        retarget_materials(donor, templates, character.id, &body.retargets)?
    };
    let flver = rebuild(donor, &body.slots, true, &append)?;
    for lod in ["", "_l"] {
        let tpf = body_tpf(
            &files.texture_tpfs(),
            &files.textures,
            &body.extra_sets(),
            body.samples_metallic(),
            BODY_STEM,
            lod,
        )?;
        let names: Vec<String> = Tpf::parse(&tpf)
            .map_err(|error| Error::new(error.to_string()))?
            .textures
            .into_iter()
            .map(|texture| texture.name)
            .collect();
        check_flver(&flver, &names, !lod.is_empty())?;
    }
    let vertices: usize = body.reports.iter().map(|slot| slot.vertices).sum();
    Ok(format!(
        "{id}: {} slots, {vertices} vertices{}, {} texture set{}",
        body.reports.len(),
        if body.edge() { ", Edge material" } else { "" },
        files.textures.sets.len(),
        if files.textures.sets.len() == 1 {
            ""
        } else {
            "s"
        }
    ))
}

#[test]
fn every_character_the_fitted_search_offers_builds_short_of_the_archives() {
    let test = "every_character_the_fitted_search_offers_builds_short_of_the_archives";
    let (Some(dsr), Some(donor)) = (dsr_with_or_skip(test, "c2500"), donor_or_skip(test)) else {
        return;
    };
    let (Some(cutout), Some(opaque)) = (
        part_or_skip(test, TEMPLATE_BINDER, TEMPLATE_FLVER),
        part_or_skip(test, OPAQUE_TEMPLATE_BINDER, OPAQUE_TEMPLATE_FLVER),
    ) else {
        return;
    };
    let templates = Templates {
        cutout: edge_template(&cutout).unwrap(),
        opaque: opaque_template(&opaque).unwrap(),
    };
    let hits = search::search(&dsr, "c", Some(&donor)).unwrap();
    for hit in &hits {
        if let Verdict::Fit(why) = &hit.verdict {
            eprintln!("{}: the fit fails: {why}", hit.id);
        }
    }
    assert_eq!(tally(&hits), FITTED);
    assert!(matches!(verdict(&hits, "c2940"), Verdict::Fit(why) if why.contains("R Thigh01")));
    assert!(matches!(verdict(&hits, "c3110"), Verdict::Fit(why) if why.contains("knots")));
    assert!(matches!(verdict(&hits, "c5210"), Verdict::Fit(why) if why.contains("knots")));
    // Priscilla's two alpha-blended materials draw dithered.
    assert!(matches!(
        verdict(&hits, "c2730"),
        Verdict::Maps { fitted: true, .. }
    ));
    // FromSoft-named and Bloathead-named rigs, and Nito, whose 20 materials draw 8 ways.
    for id in ["c4130", "c4150", "c4160", "c5220"] {
        assert!(
            matches!(verdict(&hits, id), Verdict::Maps { fitted: true, .. }),
            "{id}"
        );
    }

    let offered: Vec<&str> = hits
        .iter()
        .filter(|hit| matches!(hit.verdict, Verdict::Maps { fitted: true, .. }))
        .map(|hit| hit.id.as_str())
        .collect();
    let mut failures = Vec::new();
    for id in &offered {
        match build_offline(&dsr, &donor, &templates, id) {
            Ok(line) => eprintln!("{line}"),
            Err(error) => failures.push(format!("{id}: {error}")),
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} offered characters failed:\n{}",
        failures.len(),
        offered.len(),
        failures.join("\n")
    );
}
