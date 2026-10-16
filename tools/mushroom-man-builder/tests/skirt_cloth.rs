//! Lonesome Gavlan's skirt (Dark Souls II c1350) cut out of his body and built onto
//! `BD_*_1380`'s cloth chains, against the games' own files: what a build does with a skirted
//! character, short of the archives, at the default rigidity.
//!
//! Holds the cut to the tail's promises: every triangle lands in exactly one half, and no vertex
//! the two halves share weighs on cloth. Then the skirt goes into each of the four cloth part
//! models (male and female, both detail levels), and each passes `verify-package`'s FLVER check
//! against its own TPF with the skirt's vertices on the part's cloth bones.
//!
//! Corpus-gated: reads Dark Souls II's archives, and the donor body, the `C[AMSN]` templates and
//! the four `BD_*_1380` models out of the unpacked Elden Ring corpus; skips without them.

mod common;

use common::{donor_or_skip, ds2_archives_or_skip, part_or_skip};
use er_tpf::Tpf;
use mushroom_man_builder::{
    character::{self, Game},
    edge::{OPAQUE_TEMPLATE_FLVER, TEMPLATE_FLVER, Templates, edge_template, opaque_template},
    flver::{Flver, verify::check_flver},
    package::{Cloth, Cutouts, cloth_model, hanging_cloth, hanging_split, read_ds2_character},
    skirt::{self, DEFAULT_RIGIDITY},
    tail::CLOTH_PREFIX,
};

fn tpf_names(tpf: &[u8]) -> Vec<String> {
    Tpf::parse(tpf)
        .unwrap()
        .textures
        .into_iter()
        .map(|texture| texture.name)
        .collect()
}

#[test]
fn gavlans_skirt_goes_onto_the_skirt_chains_and_every_part_checks() {
    let test = "gavlans_skirt_goes_onto_the_skirt_chains_and_every_part_checks";
    let (Some(archives), Some(donor)) = (ds2_archives_or_skip(test), donor_or_skip(test)) else {
        return;
    };
    let (Some(cutout), Some(opaque), Some(male)) = (
        part_or_skip(test, "bd_f_1451-partsbnd-dcx", TEMPLATE_FLVER),
        part_or_skip(test, "bd_m_1711-partsbnd-dcx", OPAQUE_TEMPLATE_FLVER),
        part_or_skip(test, "bd_m_1380-partsbnd-dcx", "BD_M_1380.flver"),
    ) else {
        return;
    };
    let templates = Templates {
        cutout: edge_template(&cutout).unwrap(),
        opaque: opaque_template(&opaque).unwrap(),
    };
    let gavlan = character::resolve_in(Game::Ds2, "c1350", true).unwrap();
    let files = read_ds2_character(&archives, gavlan).unwrap();
    assert_eq!(
        hanging_cloth(&files.flver, DEFAULT_RIGIDITY, skirt::CAPE_RIGIDITY).unwrap(),
        Some(Cloth::Skirt)
    );

    let full = mushroom_man_builder::fit::export(gavlan, &files.flver, &donor).unwrap();
    let (body, skirt) =
        hanging_split(gavlan, &files.flver, &donor, &male, DEFAULT_RIGIDITY).unwrap();
    assert_eq!(
        body.mesh.triangles.len() + skirt.mesh.triangles.len(),
        full.mesh.triangles.len(),
        "every triangle in one half"
    );
    for row in &body.mesh.weights {
        assert!(
            !row.target_bone.starts_with(CLOTH_PREFIX),
            "a body vertex weighs on {}",
            row.target_bone
        );
    }
    let on_cloth: f32 = skirt
        .mesh
        .weights
        .iter()
        .filter(|row| row.target_bone.starts_with(CLOTH_PREFIX))
        .map(|row| row.weight)
        .sum();
    let on_legs: f32 = skirt
        .mesh
        .weights
        .iter()
        .filter(|row| {
            ["Thigh", "Calf", "Knee"]
                .iter()
                .any(|leg| row.target_bone.contains(leg))
        })
        .map(|row| row.weight)
        .sum();
    eprintln!(
        "body {} vertices / {} triangles, skirt {} / {}, {on_cloth:.0} vertex-weights on cloth, \
         {on_legs:.0} on the legs",
        body.mesh.vertices.len(),
        body.mesh.triangles.len(),
        skirt.mesh.vertices.len(),
        skirt.mesh.triangles.len()
    );
    // c1350's twelve chains carry the skirt from the belt to above the knee.
    assert!(skirt.mesh.triangles.len() > 1000, "the skirt is cut out");
    assert!(on_cloth > 200.0, "the skirt weighs on the cloth");
    assert!(
        on_legs > 50.0,
        "the stiff share follows the legs toward the hem"
    );

    let male_cloth = skirt::cloth_bones(&Flver::parse(&male).unwrap().bones).unwrap();
    for (dir, flver_file, gender, lod) in [
        ("bd_m_1380-partsbnd-dcx", "BD_M_1380.flver", "M", ""),
        ("bd_m_1380_l-partsbnd-dcx", "BD_M_1380_L.flver", "M", "_l"),
        ("bd_f_1380-partsbnd-dcx", "BD_F_1380.flver", "F", ""),
        ("bd_f_1380_l-partsbnd-dcx", "BD_F_1380_L.flver", "F", "_l"),
    ] {
        let Some(part) = part_or_skip(test, dir, flver_file) else {
            return;
        };
        // Every cloth bone the weights name, in every gender and detail, under the same name.
        let names: Vec<String> = Flver::parse(&part)
            .unwrap()
            .bones
            .into_iter()
            .map(|bone| bone.name)
            .collect();
        for bone in &male_cloth {
            assert!(
                names.contains(&bone.name),
                "{flver_file} has no {}",
                bone.name
            );
        }
        let model = cloth_model(
            Cloth::Skirt,
            &part,
            &skirt,
            &files,
            gavlan,
            Cutouts::EdgeMaterial,
            &templates,
            gender,
            lod,
        )
        .unwrap_or_else(|error| panic!("{flver_file}: {error}"));
        let textures = tpf_names(&model.tpf);
        let check = check_flver(&model.flver, &textures, !lod.is_empty())
            .unwrap_or_else(|error| panic!("{flver_file}: {error}"));
        eprintln!(
            "{flver_file}: {} slots, {} vertices, textures {textures:?}",
            model.reports.len(),
            model
                .reports
                .iter()
                .map(|slot| slot.vertices)
                .sum::<usize>()
        );
        for slot in &model.reports {
            eprintln!("  {}", slot.describe());
        }
        assert!(check.named_textures > 0);
    }
}
