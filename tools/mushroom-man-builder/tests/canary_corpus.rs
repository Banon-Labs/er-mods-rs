//! The capacity canary, built in memory: c2280 behind 1500 pad vertices and 2498 degenerate
//! triangles, so every triangle it draws names a vertex past the donor's 1500 and sits past the
//! donor's 2498 triangles. In game, a writer whose growth the engine truncated would lose the
//! whole body. Here, offline: the rebuild writer must carry every c2280 vertex through unchanged,
//! each one's bytes equal to the patch path's bytes for it, and with no NaN in any tangent.
//!
//! The package the runtime check loads is built from the same construction written out as OBJ
//! and weight TSV (`--source-obj`/`--source-weights` with `--writer rebuild`). Corpus-gated.

mod common;

use common::{c2280_or_skip, donor_or_skip, fingerprint};
use mushroom_man_builder::{
    fit::{
        SourceMesh, WeightRow,
        mushroom::{FitParams, export_c2280},
    },
    flver::{
        Flver, parse_face_set_indices,
        patch::{patch_donor, skin_to_donor},
        verify::check_flver,
    },
    package::{FC_DONOR_MESH_INDEX, rebuild_body},
};

/// The donor's mesh 13: 1500 vertices, 2498 LOD0 triangles.
const DONOR_VERTICES: usize = 1500;
const DONOR_TRIANGLES: usize = 2498;
/// The canary FLVER as this test builds it, in memory; later changes to the writer must leave
/// it as it is. The M2 package's own (the same 1995488 bytes, sha256 `ca2eb994...`) was built
/// from this construction written out as OBJ and weight TSV, whose decimal text rounds the
/// vertices; `rebuild-flver` on those two files reproduced that one until 2026-10-15, when vertex
/// colour 1 became the donor's (255, 0, 0, 255) and the third UV zero.
const CANARY_FLVER: (usize, &str) = (
    1_995_488,
    "ec3d586b401ef705f863d3add991854970f597f2d58aa7368b7e5c65930ec72c",
);

/// `mesh` behind `DONOR_VERTICES` copies of its first vertex (each weighted as that vertex is)
/// and `DONOR_TRIANGLES` triangles that all name pad vertex 0.
fn canary(mesh: &SourceMesh) -> SourceMesh {
    let first = mesh.vertices[0];
    let first_rows: Vec<&WeightRow> = mesh.weights.iter().filter(|row| row.vertex == 0).collect();
    let mut weights = Vec::new();
    for pad in 0..DONOR_VERTICES {
        weights.extend(first_rows.iter().map(|row| WeightRow {
            vertex: pad,
            ..(*row).clone()
        }));
    }
    weights.extend(mesh.weights.iter().map(|row| WeightRow {
        vertex: row.vertex + DONOR_VERTICES,
        ..row.clone()
    }));
    let shift = DONOR_VERTICES as u32;
    SourceMesh {
        vertices: std::iter::repeat_n(first, DONOR_VERTICES)
            .chain(mesh.vertices.iter().copied())
            .collect(),
        triangles: std::iter::repeat_n([0, 0, 0], DONOR_TRIANGLES)
            .chain(mesh.triangles.iter().map(|t| t.map(|index| index + shift)))
            .collect(),
        weights,
    }
}

/// The bytes of vertex buffer `buffer` of `bytes`, which must be one vertex per `vertex_size`.
fn buffer_bytes(bytes: &[u8], buffer: usize) -> (Vec<u8>, usize) {
    let flver = Flver::parse(bytes).unwrap();
    let buffer = flver.vertex_buffers[buffer];
    let start = flver.header.data_offset + buffer.buffer_offset;
    (
        bytes[start..start + buffer.buffer_length].to_vec(),
        buffer.vertex_size,
    )
}

#[test]
fn every_canary_body_vertex_is_the_patch_paths_vertex_and_draws_past_the_donor() {
    let test = "every_canary_body_vertex_is_the_patch_paths_vertex_and_draws_past_the_donor";
    let (Some((flver, _)), Some(donor)) = (c2280_or_skip(test), donor_or_skip(test)) else {
        return;
    };
    let mesh = export_c2280(&flver, &donor, FitParams::default()).unwrap();
    let canary = canary(&mesh);

    // Every canary vertex carries usable weight, so none falls to the chest fallback.
    let mut weighted = vec![false; canary.vertices.len()];
    for row in &canary.weights {
        if row.weight > 0.0001 && !row.target_bone.starts_with('<') {
            weighted[row.vertex] = true;
        }
    }
    assert!(weighted.iter().all(|&w| w));

    let (rebuilt, report) = rebuild_body(&donor, &canary).unwrap();
    let (len, sha) = fingerprint(&rebuilt);
    assert_eq!(
        (len, sha.as_str()),
        CANARY_FLVER,
        "the canary FLVER changed: now ({len}, \"{sha}\")"
    );
    assert_eq!(report.vertices, DONOR_VERTICES + mesh.vertices.len());
    assert!(report.vertices > DONOR_VERTICES);
    assert_eq!(report.triangles[0], DONOR_TRIANGLES + mesh.triangles.len());
    assert!(report.triangles[0] > DONOR_TRIANGLES);
    assert_eq!(
        check_flver(&rebuilt, &[], false).unwrap().shared_face_sets,
        0
    );

    // The tangents degenerate triangles feed are zero, so none is NaN.
    let skinned = skin_to_donor(&canary, &Flver::parse(&donor).unwrap()).unwrap();
    assert!(
        skinned
            .iter()
            .all(|v| v.tangent.iter().all(|t| t.is_finite()) && v.normal.x.is_finite())
    );

    // Vertex 1500 + k of the canary is byte for byte vertex k of the c2280 patch.
    let mut patched = donor.clone();
    patch_donor(&mut patched, &mesh, FC_DONOR_MESH_INDEX).unwrap();
    let (patch_bytes, size) = buffer_bytes(&patched, FC_DONOR_MESH_INDEX);
    let (canary_bytes, canary_size) = buffer_bytes(&rebuilt, FC_DONOR_MESH_INDEX);
    assert_eq!(size, canary_size);
    let body = mesh.vertices.len() * size;
    assert_eq!(
        canary_bytes[DONOR_VERTICES * size..DONOR_VERTICES * size + body],
        patch_bytes[..body]
    );

    // Every triangle that is not a filler names a vertex past the donor's and comes after the
    // donor's index count; the fillers name pad vertex 0 only.
    let out = Flver::parse(&rebuilt).unwrap();
    let mesh_13 = out.meshes[FC_DONOR_MESH_INDEX];
    let lod0 = mushroom_man_builder::flver::parse_u32_list(
        &rebuilt,
        mesh_13.face_set_offset,
        mesh_13.face_set_count,
    )
    .unwrap()
    .into_iter()
    .map(|index| out.face_sets[index as usize])
    .find(|face_set| face_set.lod() == 0 && !face_set.is_motion_blur())
    .unwrap();
    let indices = parse_face_set_indices(&rebuilt, &out, &lod0).unwrap();
    let (fillers, drawn) = indices.split_at(DONOR_TRIANGLES * 3);
    assert!(fillers.iter().all(|&index| index == 0));
    assert!(drawn.iter().all(|&index| index as usize >= DONOR_VERTICES));

    let model = er_flver::parse(&rebuilt).expect("er-flver reads the canary");
    assert_eq!(
        model.meshes[FC_DONOR_MESH_INDEX].positions.len(),
        report.vertices
    );
}
