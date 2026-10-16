//! Priscilla's tail cut out of her body and built onto `BD_*_2240`'s cloth chain, against the
//! games' own files: what a build does with a tailed character, short of the archives, at the
//! default rigidity.
//!
//! Holds the cut to two promises: every triangle lands in exactly one half, and no vertex the two
//! halves share weighs on the chain, so the seam cannot open. Then the body is rebuilt as before
//! and the tail into each of the four cloth part models (male and female, both detail levels),
//! and each passes `verify-package`'s FLVER check against its own TPF.
//!
//! Corpus-gated: reads the DSR install, and the donor body, the `C[AMSN]` templates and the four
//! `BD_*_2240` models out of the unpacked Elden Ring corpus; skips without them.

mod common;

use common::{donor_or_skip, dsr_with_or_skip, part_or_skip};
use er_tpf::Tpf;
use mushroom_man_builder::{
    character,
    edge::{OPAQUE_TEMPLATE_FLVER, TEMPLATE_FLVER, Templates, edge_template, opaque_template},
    flver::{Flver, parse_layout_members, parse_u32_list, verify::check_flver},
    package::{Cutouts, biped_body_from, read_character, tail_model, tail_split},
    tail::{CHAIN, Rigidity},
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
fn priscillas_tail_goes_onto_the_cape_chain_and_every_part_checks() {
    let test = "priscillas_tail_goes_onto_the_cape_chain_and_every_part_checks";
    let (Some(dsr), Some(donor)) = (dsr_with_or_skip(test, "c2730"), donor_or_skip(test)) else {
        return;
    };
    let (Some(cutout), Some(opaque)) = (
        part_or_skip(test, "bd_f_1451-partsbnd-dcx", TEMPLATE_FLVER),
        part_or_skip(test, "bd_m_1711-partsbnd-dcx", OPAQUE_TEMPLATE_FLVER),
    ) else {
        return;
    };
    let templates = Templates {
        cutout: edge_template(&cutout).unwrap(),
        opaque: opaque_template(&opaque).unwrap(),
    };
    let priscilla = character::resolve("c2730", true).unwrap();
    let files = read_character(&dsr, priscilla).unwrap();
    let full = mushroom_man_builder::fit::export(priscilla, &files.flver, &donor).unwrap();
    let (body, tail) = tail_split(priscilla, &files.flver, &donor, Rigidity::DEFAULT).unwrap();
    assert_eq!(
        body.mesh.triangles.len() + tail.mesh.triangles.len(),
        full.mesh.triangles.len(),
        "every triangle in one half"
    );
    for row in &body.mesh.weights {
        assert!(
            !CHAIN.contains(&row.target_bone.as_str()),
            "a body vertex weighs on {}",
            row.target_bone
        );
    }
    let on_chain: f32 = tail
        .mesh
        .weights
        .iter()
        .filter(|row| CHAIN.contains(&row.target_bone.as_str()))
        .map(|row| row.weight)
        .sum();
    eprintln!(
        "body {} vertices / {} triangles, tail {} / {}, {on_chain:.0} vertex-weights on the chain",
        body.mesh.vertices.len(),
        body.mesh.triangles.len(),
        tail.mesh.vertices.len(),
        tail.mesh.triangles.len()
    );
    // Shared with the pelvis, 5% at the root to 45% at the tip, so well under one per vertex.
    assert!(on_chain > 100.0, "the tail weighs on the chain");
    assert!(
        on_chain < tail.mesh.vertices.len() as f32 * 0.45,
        "the pelvis keeps most of the tail"
    );

    let built = biped_body_from(
        &donor,
        priscilla,
        &files.flver,
        &body,
        &files.textures,
        Cutouts::EdgeMaterial,
    )
    .unwrap();
    assert!(!built.slots.is_empty());

    for (dir, flver_file, gender, lod) in [
        ("bd_m_2240-partsbnd-dcx", "BD_M_2240.flver", "M", ""),
        ("bd_m_2240_l-partsbnd-dcx", "BD_M_2240_L.flver", "M", "_l"),
        ("bd_f_2240-partsbnd-dcx", "BD_F_2240.flver", "F", ""),
        ("bd_f_2240_l-partsbnd-dcx", "BD_F_2240_L.flver", "F", "_l"),
    ] {
        let Some(part) = part_or_skip(test, dir, flver_file) else {
            return;
        };
        let model = tail_model(
            &part,
            &tail,
            &files,
            priscilla,
            Cutouts::EdgeMaterial,
            &templates,
            gender,
            lod,
        )
        .unwrap_or_else(|error| panic!("{flver_file}: {error}"));
        let names = tpf_names(&model.tpf);
        let check = check_flver(&model.flver, &names, !lod.is_empty())
            .unwrap_or_else(|error| panic!("{flver_file}: {error}"));
        eprintln!(
            "{flver_file}: {} slots, {} vertices, textures {names:?}",
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

        // The tail's vertices weigh on the chain bones of the model they are in.
        let flver = Flver::parse(&model.flver).unwrap();
        let chain: Vec<usize> = CHAIN
            .iter()
            .map(|name| flver.bones.iter().position(|b| b.name == *name).unwrap())
            .collect();
        let mut chain_vertices = 0;
        for slot in &model.reports {
            let mesh = &flver.meshes[slot.donor_mesh];
            let buffer = parse_u32_list(&model.flver, mesh.vertex_buffer_offset, 1).unwrap()[0];
            let buffer = flver.vertex_buffers[buffer as usize];
            let members =
                parse_layout_members(&model.flver, flver.layouts[buffer.layout_index]).unwrap();
            let bones_at = members
                .iter()
                .find(|member| member.semantic_id == 2)
                .unwrap()
                .struct_offset;
            // A buffer's offset counts from the start of the data section.
            let start = flver.header.data_offset + buffer.buffer_offset;
            for vertex in 0..buffer.vertex_count {
                let at = start + vertex * buffer.vertex_size + bones_at;
                let bones = &model.flver[at..at + 4];
                if bones.iter().any(|&b| chain.contains(&usize::from(b))) {
                    chain_vertices += 1;
                }
            }
        }
        assert!(
            chain_vertices > 1000,
            "{flver_file}: only {chain_vertices} tail vertices weigh on the chain"
        );
    }
}
