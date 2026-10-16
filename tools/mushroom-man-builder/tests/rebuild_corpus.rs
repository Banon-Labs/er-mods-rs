//! The rebuild writer against Elden Ring's own donor body, `FC_M_0000.flver`.
//!
//! Two kinds of evidence, both recorded as numbers rather than bytes:
//!
//! - With no slots the writer gives the donor back byte for byte, so its layout model is the
//!   one From's file is laid out by, down to every pad.
//! - Growing mesh 13 to 2607 vertices and 3267/1977/652 triangles gives the sizes and offsets
//!   Andre.SoulsFormats' `FLVER2.Write` gave for the same growth
//!   (`docs/recon/dsr-character-builder/andre-oracle-results.txt`, `tools/flver-andre-oracle`).
//!
//! Each output is also read back by this crate's parser and checker, and by `er-flver`, a second
//! parser written against a different library. Corpus-gated: skips without the donor.

mod common;

use common::{donor_or_skip, fingerprint};
use mushroom_man_builder::flver::{
    Flver, parse_face_set_indices, parse_u32_list, read_vec3,
    rebuild::{HeaderAppend, Obb, Slot, rebuild},
    verify::check_flver,
};

const DONOR_MESH: usize = 13;
/// Header 0x40 and 0x44 on the donor: faces outside motion-blur sets, and in all of them.
const DONOR_FACES: u32 = 45_821;
const DONOR_FACES_ALL: u32 = 91_670;

/// What Andre's writer made of the same growth.
const GROWN_VERTICES: usize = 2607;
const GROWN_TRIANGLES: [usize; 3] = [3267, 1977, 652];
const GROWN_FILE_LEN: usize = 1_914_768;
const GROWN_DATA_OFFSET: usize = 0x1FDB0;
const GROWN_DATA_LENGTH: usize = 0x1B_39E0;
/// The first face set after mesh 13's vertex buffer.
const GROWN_FACE_SET_84_OFFSET: usize = 0xA_6FD0;
/// Mesh 13's vertex buffer: count, length, offset.
const GROWN_BUFFER_13: (usize, usize, usize) = (2607, 135_564, 548_416);

fn u32_at(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

#[test]
fn with_no_slots_the_donor_comes_back_byte_for_byte() {
    let Some(donor) = donor_or_skip("with_no_slots_the_donor_comes_back_byte_for_byte") else {
        return;
    };
    let out = rebuild(&donor, &[], false, &HeaderAppend::default()).unwrap();
    assert_eq!(fingerprint(&out), fingerprint(&donor));
    assert_eq!(u32_at(&out, 0x40), DONOR_FACES);
    assert_eq!(u32_at(&out, 0x44), DONOR_FACES_ALL);
    let report = check_flver(&out, &[], false).unwrap();
    assert_eq!(report.meshes, 42);
    er_flver::parse(&out).expect("er-flver reads the identity rebuild");
}

/// Mesh 13 grown the way the oracle grew it: its own vertices and triangles repeated until the
/// counts are reached, so only the sizes change.
fn grown_slot(donor: &[u8]) -> Slot {
    let flver = Flver::parse(donor).unwrap();
    let mesh = flver.meshes[DONOR_MESH];
    let buffer_index = parse_u32_list(donor, mesh.vertex_buffer_offset, 1).unwrap()[0] as usize;
    let buffer = flver.vertex_buffers[buffer_index];
    let start = flver.header.data_offset + buffer.buffer_offset;
    let own = &donor[start..start + buffer.buffer_length];
    let vertex_bytes: Vec<u8> = own
        .iter()
        .copied()
        .cycle()
        .take(GROWN_VERTICES * buffer.vertex_size)
        .collect();
    let face_sets = parse_u32_list(donor, mesh.face_set_offset, mesh.face_set_count).unwrap();
    let lods = [0, 1, 2].map(|lod| {
        let face_set = face_sets
            .iter()
            .map(|&index| flver.face_sets[index as usize])
            .find(|face_set| face_set.lod() == lod && !face_set.is_motion_blur())
            .unwrap();
        let indices = parse_face_set_indices(donor, &flver, &face_set).unwrap();
        let triangles: Vec<[u32; 3]> = indices
            .as_chunks::<3>()
            .0
            .iter()
            .copied()
            .cycle()
            .take(GROWN_TRIANGLES[lod])
            .collect();
        triangles
    });
    let positions = (0..GROWN_VERTICES)
        .map(|vertex| read_vec3(&vertex_bytes, vertex * buffer.vertex_size).unwrap());
    Slot {
        donor_mesh: DONOR_MESH,
        material: 0,
        cull_backfaces: true,
        vertex_count: GROWN_VERTICES,
        lods,
        obb: Obb::around(positions).unwrap(),
        vertex_bytes,
        layout: None,
    }
}

#[test]
fn growing_mesh_13_lands_every_size_and_offset_where_andre_put_them() {
    let Some(donor) =
        donor_or_skip("growing_mesh_13_lands_every_size_and_offset_where_andre_put_them")
    else {
        return;
    };
    let slot = grown_slot(&donor);
    let out = rebuild(&donor, &[slot], false, &HeaderAppend::default()).unwrap();
    let flver = Flver::parse(&out).unwrap();
    assert_eq!(out.len(), GROWN_FILE_LEN);
    assert_eq!(flver.header.data_offset, GROWN_DATA_OFFSET);
    assert_eq!(flver.header.data_length, GROWN_DATA_LENGTH);
    assert_eq!(flver.face_sets[84].index_offset, GROWN_FACE_SET_84_OFFSET);
    let buffer = flver.vertex_buffers[DONOR_MESH];
    assert_eq!(
        (
            buffer.vertex_count,
            buffer.buffer_length,
            buffer.buffer_offset
        ),
        GROWN_BUFFER_13
    );
    let slot_faces: usize = GROWN_TRIANGLES.iter().sum();
    let donor_mesh_13 = 7494 / 3 + 2034 / 3 + 576 / 3;
    assert_eq!(
        u32_at(&out, 0x40) as usize,
        DONOR_FACES as usize - donor_mesh_13 + slot_faces
    );
    assert_eq!(
        u32_at(&out, 0x44) as usize,
        DONOR_FACES_ALL as usize - 2 * donor_mesh_13 + 2 * slot_faces
    );
    compare_with_andre(&out);

    let donor_vertices: usize = Flver::parse(&donor)
        .unwrap()
        .vertex_buffers
        .iter()
        .map(|buffer| buffer.vertex_count)
        .sum();
    let report = check_flver(&out, &[], false).unwrap();
    assert_eq!(report.vertices, donor_vertices - 1500 + GROWN_VERTICES);
    assert_eq!(report.shared_face_sets, 0);
    let model = er_flver::parse(&out).expect("er-flver reads the grown rebuild");
    assert_eq!(model.meshes[DONOR_MESH].positions.len(), GROWN_VERTICES);
    assert_eq!(
        model.meshes[DONOR_MESH].triangle_count(),
        GROWN_TRIANGLES[0]
    );
    assert_eq!(model.meshes.len(), 42);
}

/// Developer check, opt-in: with `MMB_ANDRE_GROWN13` naming the `grown13.flver` the oracle's
/// `probe` wrote, the grown rebuild must be that file byte for byte outside the fields this
/// writer sets on purpose and Andre leaves as they were: the header's box (0x28..0x40) and
/// face count (0x40, which Andre fills with the total), mesh 13's material, and mesh 13's box.
fn compare_with_andre(out: &[u8]) {
    let Some(path) = std::env::var_os("MMB_ANDRE_GROWN13") else {
        return;
    };
    let andre = std::fs::read(&path).expect("read MMB_ANDRE_GROWN13");
    assert_eq!(out.len(), andre.len());
    let flver = Flver::parse(out).unwrap();
    let mesh = flver.meshes[DONOR_MESH];
    let material = flver.mesh_table_offset(DONOR_MESH) + 0x04;
    let mesh_box = mesh.bounding_box_offset..mesh.bounding_box_offset + 0x24;
    let expected = |at: usize| {
        (0x28..0x44).contains(&at)
            || (material..material + 4).contains(&at)
            || mesh_box.contains(&at)
    };
    let unexpected: Vec<usize> = (0..out.len())
        .filter(|&at| out[at] != andre[at] && !expected(at))
        .collect();
    assert!(
        unexpected.is_empty(),
        "{} bytes differ from Andre's grown file outside the expected fields, first at 0x{:X}",
        unexpected.len(),
        unexpected[0]
    );
}

#[test]
fn a_hidden_rebuild_keeps_every_count_and_still_reads() {
    let Some(donor) = donor_or_skip("a_hidden_rebuild_keeps_every_count_and_still_reads") else {
        return;
    };
    let slot = grown_slot(&donor);
    let out = rebuild(&donor, &[slot], true, &HeaderAppend::default()).unwrap();
    let (before, after) = (Flver::parse(&donor).unwrap(), Flver::parse(&out).unwrap());
    for (index, (old, new)) in before.face_sets.iter().zip(&after.face_sets).enumerate() {
        if !(78..84).contains(&index) {
            assert_eq!(old.index_count, new.index_count, "face set {index}");
            assert!(
                parse_face_set_indices(&out, &after, new)
                    .unwrap()
                    .iter()
                    .all(|&i| i == 0)
            );
        }
    }
    check_flver(&out, &[], false).unwrap();
    er_flver::parse(&out).expect("er-flver reads the hidden rebuild");
}
