//! The Edge material against Elden Ring's own files: the donor body `FC_M_0000.flver`, and the
//! template material, `BD_F_1451.flver`'s material 0, both out of the extracted corpus.
//!
//! What it holds the writer to, recorded as numbers and names rather than bytes:
//!
//! - The template is the `C[AMSN]_e` material this was built on: its flags and GX list carry the
//!   fingerprint recorded here. The builder reads them out of the installed game instead; the
//!   installed 1.17.1 `BD_F_1451.flver` was the corpus's file byte for byte when this was
//!   recorded (604384 bytes, sha256 `be663946...`).
//! - The donor material the Edge slot takes, 14, is drawn by mesh 14 alone and has 13 texture
//!   entries; every donor mesh draws with a material of its own, so retargeting 14 changes no
//!   other mesh's material.
//! - A rebuild with one Edge slot on material 14 moves `dataOffset` and nothing that counts from
//!   it, leaves every other material and texture entry as the donor has them -- material 0 stays
//!   the `#03#` Body material -- and reads back through this crate's parser and checker and
//!   through `er-flver`. With an empty append it is the rebuild without one.
//!
//! Developer check, opt-in: with `MMB_EDGE_FLVER_OUT` naming a file, the Edge rebuild is written
//! there for `tools/flver-andre-oracle` to `read`, and to print material 14 of with `materials`.
//! Corpus-gated: skips without the corpus.

mod common;

use common::{donor_or_skip, fingerprint, part_or_skip};
use mushroom_man_builder::{
    edge::{EDGE_MATERIAL, EDGE_MTD, TEMPLATE_FLVER, edge_material, edge_template, edge_textures},
    flver::{
        Flver, parse_face_set_indices, parse_materials, parse_textures, parse_u32_list, read_vec3,
        rebuild::{HeaderAppend, MaterialTemplate, Obb, Slot, material_template, rebuild},
        verify::check_flver,
    },
};

const TEMPLATE_BINDER: &str = "bd_f_1451-partsbnd-dcx";
/// `BD_F_1451.flver`'s material 0: its flags as a little-endian word, then its GX list.
const TEMPLATE_FINGERPRINT: (usize, &str) = (
    68,
    "3be93561bc0efac20e3d2f80a48c2065f030b2406653397b1754e5379c61ccff",
);
const EDGE_MESH: usize = 14;
const EDGE_MATERIAL_NAME: &str = "#73#_Special";
const EDGE_TEXTURE_ENTRIES: usize = 13;
const BODY_MATERIAL_NAME: &str = "#03#";
const BODY_MTD_FILE: &str = "P[FC_M_0000]_Body.matxml";
const MATERIAL_SIZE: usize = 0x20;
const TEXTURE_SIZE: usize = 0x20;
/// Where a material entry records the first material drawn with its MTD.
const MATERIAL_FIRST_OF_MTD: usize = 0x18;
/// Where a texture entry records its U and V tiling.
const TEXTURE_TILING: usize = 0x10;

fn template_or_skip(test: &str) -> Option<MaterialTemplate> {
    part_or_skip(test, TEMPLATE_BINDER, TEMPLATE_FLVER)
        .map(|bytes| edge_template(&bytes).expect("BD_F_1451.flver material 0 is C[AMSN]_e"))
}

#[test]
fn the_template_is_the_c_amsn_e_material_this_was_built_on() {
    let Some(template) =
        template_or_skip("the_template_is_the_c_amsn_e_material_this_was_built_on")
    else {
        return;
    };
    let mut recorded = template.flags.to_le_bytes().to_vec();
    recorded.extend_from_slice(&template.gx);
    let (len, sha) = fingerprint(&recorded);
    assert_eq!(
        (len, sha.as_str()),
        TEMPLATE_FINGERPRINT,
        "BD_F_1451.flver material 0 changed: now ({len}, \"{sha}\")"
    );
    assert!(template.mtd.eq_ignore_ascii_case(EDGE_MTD));
    let samplers: Vec<String> = edge_textures().into_iter().map(|t| t.sampler).collect();
    assert_eq!(template.samplers, samplers);
}

#[test]
fn material_14_is_mesh_14s_alone_and_material_0_is_body() {
    let Some(donor) = donor_or_skip("material_14_is_mesh_14s_alone_and_material_0_is_body") else {
        return;
    };
    let flver = Flver::parse(&donor).unwrap();
    let materials = parse_materials(&donor, &flver).unwrap();
    assert_eq!(materials.len(), flver.meshes.len());
    for (index, mesh) in flver.meshes.iter().enumerate() {
        assert_eq!(mesh.material_index as usize, index, "mesh {index}");
    }
    let edge = &materials[EDGE_MATERIAL as usize];
    assert_eq!(EDGE_MATERIAL as usize, EDGE_MESH);
    assert_eq!(
        (edge.name.as_str(), edge.texture_count),
        (EDGE_MATERIAL_NAME, EDGE_TEXTURE_ENTRIES)
    );
    let body = &materials[0];
    assert_eq!(body.name, BODY_MATERIAL_NAME);
    assert!(body.mtd.ends_with(BODY_MTD_FILE));
}

/// Mesh `mesh` as a slot of its own vertices and triangles, drawing with `material`.
fn own_slot(donor: &[u8], mesh: usize, material: u32) -> Slot {
    let flver = Flver::parse(donor).unwrap();
    let entry = flver.meshes[mesh];
    let buffer_index = parse_u32_list(donor, entry.vertex_buffer_offset, 1).unwrap()[0];
    let buffer = flver.vertex_buffers[buffer_index as usize];
    let start = flver.header.data_offset + buffer.buffer_offset;
    let vertex_bytes = donor[start..start + buffer.buffer_length].to_vec();
    let face_sets = parse_u32_list(donor, entry.face_set_offset, entry.face_set_count).unwrap();
    let lods = [0, 1, 2].map(|lod| {
        let face_set = face_sets
            .iter()
            .map(|&index| flver.face_sets[index as usize])
            .find(|face_set| face_set.lod() == lod && !face_set.is_motion_blur())
            .unwrap();
        let indices = parse_face_set_indices(donor, &flver, &face_set).unwrap();
        indices.as_chunks::<3>().0.to_vec()
    });
    let positions = (0..buffer.vertex_count)
        .map(|vertex| read_vec3(&vertex_bytes, vertex * buffer.vertex_size).unwrap());
    Slot {
        donor_mesh: mesh,
        material,
        cull_backfaces: false,
        vertex_count: buffer.vertex_count,
        lods,
        obb: Obb::around(positions).unwrap(),
        vertex_bytes,
        layout: None,
    }
}

fn names(lod: &str) -> Vec<String> {
    ["_3m", "_a", "_m", "_n"]
        .iter()
        .map(|slot| format!("FC_M_0000{slot}{lod}"))
        .collect()
}

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

#[test]
fn an_edge_slot_on_material_14_moves_only_the_data_offset_and_reads_back() {
    let test = "an_edge_slot_on_material_14_moves_only_the_data_offset_and_reads_back";
    let (Some(donor), Some(template)) = (donor_or_skip(test), template_or_skip(test)) else {
        return;
    };
    let slot = own_slot(&donor, EDGE_MESH, EDGE_MATERIAL);
    let slots = std::slice::from_ref(&slot);
    let plain = rebuild(&donor, slots, true, &HeaderAppend::default()).unwrap();
    // An append with nothing in it is the rebuild without one.
    let unused = HeaderAppend::for_donor(&donor).unwrap();
    assert_eq!(rebuild(&donor, slots, true, &unused).unwrap(), plain);

    let append = edge_material(&donor, &template, "c2650").unwrap();
    let out = rebuild(&donor, slots, true, &append).unwrap();
    if let Some(path) = std::env::var_os("MMB_EDGE_FLVER_OUT") {
        std::fs::write(&path, &out).expect("write MMB_EDGE_FLVER_OUT");
    }

    // dataOffset moved to a later 16-byte boundary; the data section, and every face set and
    // vertex buffer entry that points into it, are the plain rebuild's.
    let (before, after) = (Flver::parse(&plain).unwrap(), Flver::parse(&out).unwrap());
    assert!(after.header.data_offset > before.header.data_offset);
    assert!(after.header.data_offset.is_multiple_of(16));
    assert_eq!(
        out.len() - after.header.data_offset,
        plain.len() - before.header.data_offset
    );
    assert!(out[after.header.data_offset..] == plain[before.header.data_offset..]);
    for (index, (old, new)) in before.face_sets.iter().zip(&after.face_sets).enumerate() {
        let entry = |flver: &Flver, bytes: &[u8]| {
            let at = flver.face_set_table_offset(index);
            bytes[at..at + 0x20].to_vec()
        };
        assert_eq!(
            entry(&before, &plain),
            entry(&after, &out),
            "face set {index}"
        );
        assert_eq!(old.index_offset, new.index_offset);
    }
    for index in 0..before.vertex_buffers.len() {
        let at = before.vertex_buffer_table_offset(index);
        assert_eq!(
            plain[at..at + 0x20],
            out[at..at + 0x20],
            "vertex buffer {index}"
        );
    }

    // Every material entry but 14's, and every texture entry but its first three, are the
    // donor's; so is every other byte of the header region up to where its strings ended,
    // apart from dataOffset. The first string appended, the material's name, starts there,
    // in what was the donor's zero padding.
    let edge = EDGE_MATERIAL as usize;
    let edge_entry = after.material_table + edge * MATERIAL_SIZE;
    let strings_end = u32_at(&out, edge_entry) as usize;
    assert!(strings_end <= before.header.data_offset);
    assert!(
        plain[strings_end..before.header.data_offset]
            .iter()
            .all(|&b| b == 0)
    );
    let edge_textures_at = parse_materials(&donor, &before).unwrap()[edge].texture_index;
    let first_texture = after.texture_table + edge_textures_at * TEXTURE_SIZE;
    let written = [
        0x0C..0x10,
        edge_entry..edge_entry + MATERIAL_SIZE,
        first_texture..first_texture + 3 * TEXTURE_SIZE,
    ];
    let changed: Vec<usize> = (0..strings_end)
        .filter(|&at| out[at] != plain[at] && !written.iter().any(|range| range.contains(&at)))
        .collect();
    assert!(changed.is_empty(), "header bytes changed: {changed:X?}");

    // The Edge material reads back as written, and material 0 is still Body.
    let materials = parse_materials(&out, &after).unwrap();
    assert_eq!(materials.len(), before.header.material_count);
    assert_eq!(
        (
            materials[edge].name.as_str(),
            materials[edge].mtd.as_str(),
            materials[edge].texture_count
        ),
        ("c2650_edge", EDGE_MTD, EDGE_TEXTURE_ENTRIES)
    );
    assert_eq!(materials[0].name, BODY_MATERIAL_NAME);
    assert!(materials[0].mtd.ends_with(BODY_MTD_FILE));
    let read_back = material_template(&out, edge).unwrap();
    assert_eq!(
        (read_back.flags, &read_back.gx),
        (template.flags, &template.gx)
    );
    assert_eq!(read_back.samplers[..3], template.samplers[..]);
    assert_eq!(
        u32_at(&out, edge_entry + MATERIAL_FIRST_OF_MTD),
        EDGE_MATERIAL
    );
    let textures = parse_textures(&out, &after).unwrap();
    let paths: Vec<&str> = textures[edge_textures_at..edge_textures_at + 3]
        .iter()
        .map(|t| t.path.as_str())
        .collect();
    let wanted: Vec<String> = edge_textures().into_iter().map(|t| t.path).collect();
    assert_eq!(paths, wanted);
    // Each repeats in U and V, as every vanilla texture entry with a path does.
    for entry in 0..3 {
        let at = first_texture + entry * TEXTURE_SIZE + TEXTURE_TILING;
        assert_eq!(out[at..at + 4], [1, 1, 0, 0], "texture entry {entry}");
    }

    // verify-package's checks pass with the body TPF's names plus `_m`, and the material's
    // paths are what they resolve against: without `_m` the check fails.
    let check = check_flver(&out, &names(""), false).unwrap();
    assert_eq!(check.named_textures, 3);
    check_flver(&out, &names("_l"), true).unwrap();
    let without_m: Vec<String> = names("")
        .into_iter()
        .filter(|name| !name.ends_with("_m"))
        .collect();
    assert!(check_flver(&out, &without_m, false).is_err());

    let model = er_flver::parse(&out).expect("er-flver reads the Edge rebuild");
    assert_eq!(model.materials[edge].name, "c2650_edge");
    assert_eq!(model.materials[edge].mtd, EDGE_MTD);
    assert_eq!(model.materials[0].name, BODY_MATERIAL_NAME);
    assert_eq!(model.meshes[EDGE_MESH].material_index, edge);
    assert_eq!(model.meshes[EDGE_MESH].positions.len(), slot.vertex_count);
}

#[test]
fn the_edge_material_refuses_a_donor_whose_material_14_is_shared() {
    let test = "the_edge_material_refuses_a_donor_whose_material_14_is_shared";
    let (Some(donor), Some(template)) = (donor_or_skip(test), template_or_skip(test)) else {
        return;
    };
    let mut shared = donor.clone();
    let flver = Flver::parse(&shared).unwrap();
    let at = flver.mesh_table_offset(13) + 0x04;
    shared[at..at + 4].copy_from_slice(&EDGE_MATERIAL.to_le_bytes());
    let error = edge_material(&shared, &template, "c2650")
        .unwrap_err()
        .to_string();
    assert!(error.contains("meshes [13, 14]"), "{error}");
}
