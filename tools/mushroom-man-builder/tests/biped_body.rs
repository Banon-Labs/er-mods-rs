//! c2650's body as the package writes it: the fitted character in three slots of the rebuilt
//! donor, one per material group of its kept meshes, its cutout group on the Edge material or,
//! with the fallback, every group on the Body material.
//!
//! What it holds the builder to, as numbers rather than bytes: the donor mesh, material and
//! culling each slot gets and how many vertices and triangles it carries (1745 and 2456 in all,
//! the fit's); that the result passes `verify-package`'s FLVER check against the body TPF's names,
//! high and low, and reads back through `er-flver`; each body TPF's texture names, format bytes
//! and DDS formats; and the fingerprint of every FLVER and TPF. The Windows build writes the same
//! bytes into `FC_M_0000.flver`, `FC_M_0000_L.flver` and their TPFs in its package, which
//! `hash-package` shows.
//!
//! Developer check, opt-in: with `MMB_BIPED_FLVER_DIR` naming a folder, both FLVERs are written
//! there as `c2650-edge.flver` and `c2650-body.flver` for `tools/flver-andre-oracle` to `read`.
//! They are game-derived; point it at a scratch folder.
//!
//! Corpus-gated: reads c2650 out of the Dark Souls Remastered install and the donor and the Edge
//! template out of the unpacked Elden Ring corpus (see `common`), and skips without them.

mod common;

use common::{donor_or_skip, dsr_with_or_skip, fingerprint, part_or_skip};
use er_tpf::Tpf;
use mushroom_man_builder::{
    character::C2650,
    edge::{EDGE_MATERIAL, TEMPLATE_FLVER, edge_material, edge_template},
    flver::{
        rebuild::{HeaderAppend, rebuild},
        verify::check_flver,
    },
    package::{BODY_MATERIAL, BipedBody, Cutouts, biped_body, read_character},
    textures::{character_tpf, with_zero_metallic},
};

const TEMPLATE_BINDER: &str = "bd_f_1451-partsbnd-dcx";

/// Per slot: donor mesh, material, back faces culled, vertices, triangles at each distance.
type SlotShape = (usize, u32, bool, usize, usize);

/// The opaque body (DSR meshes 0 and 1), the cowl, cuffs and hem (2 and 3, `_Edge`, double-sided)
/// and the skirt (4, opaque but double-sided, on a material of its own).
const CUTOUT_SLOTS: [SlotShape; 3] = [
    (13, BODY_MATERIAL, true, 1189, 1700),
    (14, EDGE_MATERIAL, false, 416, 540),
    (10, BODY_MATERIAL, false, 140, 216),
];

/// The rebuilt FLVER with the Edge material, and with the fallback. The Windows build's package
/// carries the first byte for byte as both `FC_M_0000.flver` and `FC_M_0000_L.flver`.
const CUTOUT_FLVER: (usize, &str) = (
    1_708_848,
    "d604b82cb05abd9469e5ab923fa56404b21a947e33e8c99300781e9d0ab43a57",
);
const BODY_FLVER: (usize, &str) = (
    1_708_080,
    "9a8c20fe61f41d33bf84e1927d1d9be8b5b37250a19afe0e5f7ff9b25f77545e",
);

/// The body TPFs, high and low detail, with the Edge material's metallic map and without it: the
/// Windows build's `FC_M_0000.tpf` and `FC_M_0000_L.tpf`, byte for byte.
const TPFS: [(&str, bool, (usize, &str)); 4] = [
    (
        "",
        true,
        (
            3_498_810,
            "765fc088b8f259662f99e70468ac7d619e6c8b1bd6680b5131e60ea7347310eb",
        ),
    ),
    (
        "_l",
        true,
        (
            3_498_826,
            "2e9bdb8abe7a7c0a4a61f2a031bed1bae7b06057677625d699db286ae89c0b09",
        ),
    ),
    (
        "",
        false,
        (
            3_495_894,
            "0535187ee71f6e04aa35172ec6010e382dc9192e4f8cebb0554eb3c3ff8148ba",
        ),
    ),
    (
        "_l",
        false,
        (
            3_495_906,
            "e6ca7cb99ab445b61260c0d3571a9e8023de4accae54f9925477b588c4b37038",
        ),
    ),
];
/// Where a DDS header stores its four-character code, and a DX10 header its `DXGI_FORMAT`.
const DDS_FOURCC: usize = 0x54;
const DDS_DXGI_FORMAT: usize = 0x80;

/// The body TPF's texture names, with the Edge material's metallic map when it has one.
fn names(lod: &str, metallic: bool) -> Vec<String> {
    ["_3m", "_a", "_m", "_n"]
        .iter()
        .filter(|slot| metallic || **slot != "_m")
        .map(|slot| format!("FC_M_0000{slot}{lod}"))
        .collect()
}

fn c2650_body(test: &str, cutouts: Cutouts) -> Option<(Vec<u8>, BipedBody)> {
    let (Some(dsr), Some(donor)) = (dsr_with_or_skip(test, "c2650"), donor_or_skip(test)) else {
        return None;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    let body = biped_body(&donor, &C2650, &files.flver, cutouts).unwrap();
    Some((donor, body))
}

fn shape(body: &BipedBody) -> Vec<SlotShape> {
    body.reports
        .iter()
        .map(|slot| {
            assert!(
                slot.triangles
                    .iter()
                    .all(|&count| count == slot.triangles[0]),
                "LOD0 at every distance"
            );
            (
                slot.donor_mesh,
                slot.material,
                slot.cull_backfaces,
                slot.vertices,
                slot.triangles[0],
            )
        })
        .collect()
}

/// Write `bytes` as `name` into `MMB_BIPED_FLVER_DIR`, when it is set.
fn write_for_the_oracle(name: &str, bytes: &[u8]) {
    if let Some(dir) = std::env::var_os("MMB_BIPED_FLVER_DIR") {
        let dir = std::path::PathBuf::from(dir);
        std::fs::create_dir_all(&dir).expect("create MMB_BIPED_FLVER_DIR");
        std::fs::write(dir.join(name), bytes).expect("write into MMB_BIPED_FLVER_DIR");
    }
}

fn assert_fingerprint(what: &str, bytes: &[u8], expected: (usize, &str)) {
    let (len, sha) = fingerprint(bytes);
    assert_eq!(
        (len, sha.as_str()),
        expected,
        "{what} changed: now ({len}, \"{sha}\")"
    );
}

#[test]
fn c2650_rebuilds_into_three_material_slots_with_its_cutouts_on_the_edge_material() {
    let test = "c2650_rebuilds_into_three_material_slots_with_its_cutouts_on_the_edge_material";
    let Some((donor, body)) = c2650_body(test, Cutouts::EdgeMaterial) else {
        return;
    };
    let Some(template) = part_or_skip(test, TEMPLATE_BINDER, TEMPLATE_FLVER) else {
        return;
    };
    assert_eq!(shape(&body), CUTOUT_SLOTS);
    assert!(body.edge);
    let vertices: usize = body.reports.iter().map(|slot| slot.vertices).sum();
    let triangles: usize = body.reports.iter().map(|slot| slot.triangles[0]).sum();
    assert_eq!((vertices, triangles), (1745, 2456));
    assert_eq!((body.source_vertices, body.source_triangles), (1745, 2456));
    let strip = body.strip;
    assert_eq!(
        (
            strip.stripped_vertices,
            strip.dropped_triangles,
            strip.unreferenced_vertices
        ),
        (817, 811, 45)
    );
    let sources: Vec<(usize, Vec<usize>)> = body
        .reports
        .iter()
        .map(|slot| {
            let source = slot.source.as_ref().unwrap();
            (source.material, source.meshes.clone())
        })
        .collect();
    assert_eq!(sources, [(0, vec![0, 1]), (1, vec![2, 3]), (2, vec![4])]);

    let append = edge_material(&donor, &edge_template(&template).unwrap(), C2650.id).unwrap();
    let out = rebuild(&donor, &body.slots, true, &append).unwrap();
    write_for_the_oracle("c2650-edge.flver", &out);
    assert_fingerprint("the Edge-material c2650 FLVER", &out, CUTOUT_FLVER);
    let check = check_flver(&out, &names("", true), false).unwrap();
    assert_eq!(check.named_textures, 3, "the Edge material's three paths");
    check_flver(&out, &names("_l", true), true).unwrap();
    // Without the metallic map the Edge material names a texture the TPF does not hold.
    assert!(check_flver(&out, &names("", false), false).is_err());

    let model = er_flver::parse(&out).expect("er-flver reads the c2650 rebuild");
    for (mesh, material, _, vertices, triangles) in CUTOUT_SLOTS {
        let read = &model.meshes[mesh];
        assert_eq!(read.material_index, material as usize, "mesh {mesh}");
        assert_eq!(read.positions.len(), vertices, "mesh {mesh}");
        assert_eq!(read.triangle_count(), triangles, "mesh {mesh}");
    }
    assert_eq!(model.materials[EDGE_MATERIAL as usize].name, "c2650_edge");
    assert_eq!(model.materials[BODY_MATERIAL as usize].name, "#03#");
}

#[test]
fn c2650s_albedo_goes_in_as_srgb_bc7_and_the_metallic_map_only_with_the_edge_material() {
    let test = "c2650s_albedo_goes_in_as_srgb_bc7_and_the_metallic_map_only_with_the_edge_material";
    let Some(dsr) = dsr_with_or_skip(test, "c2650") else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    for (lod, metallic, expected) in TPFS {
        let mut tpf = character_tpf(&files.tpf, &files.textures, "FC_M_0000", lod).unwrap();
        if metallic {
            tpf = with_zero_metallic(&tpf, "FC_M_0000", lod).unwrap();
        }
        assert_fingerprint(&format!("FC_M_0000{lod}.tpf ({metallic})"), &tpf, expected);
        // Name, TPF format byte, DDS four-character code and DXGI format of each texture: the
        // specular as the legacy DXT1 it is, the albedo moved from BC7 (98) to BC7 sRGB (99)
        // under vanilla's albedo byte, the BC5 normal map under 107, the metallic map as BC4.
        let shown: Vec<(String, u8, String, Option<u32>)> = Tpf::parse(&tpf)
            .unwrap()
            .textures
            .into_iter()
            .map(|texture| {
                let fourcc = texture.dds[DDS_FOURCC..DDS_FOURCC + 4]
                    .escape_ascii()
                    .to_string();
                let dxgi = (fourcc == "DX10").then(|| {
                    u32::from_le_bytes(
                        texture.dds[DDS_DXGI_FORMAT..DDS_DXGI_FORMAT + 4]
                            .try_into()
                            .unwrap(),
                    )
                });
                (texture.name, texture.format, fourcc, dxgi)
            })
            .collect();
        let mut wanted = vec![
            (format!("FC_M_0000_3m{lod}"), 0, "DXT1".to_owned(), None),
            (
                format!("FC_M_0000_a{lod}"),
                102,
                "DX10".to_owned(),
                Some(99),
            ),
            (
                format!("FC_M_0000_n{lod}"),
                107,
                "DX10".to_owned(),
                Some(83),
            ),
        ];
        if metallic {
            wanted.insert(
                2,
                (format!("FC_M_0000_m{lod}"), 103, "ATI1".to_owned(), None),
            );
        }
        assert_eq!(shown, wanted);
    }
}

#[test]
fn the_fallback_draws_every_slot_with_the_body_material() {
    let test = "the_fallback_draws_every_slot_with_the_body_material";
    let Some((donor, body)) = c2650_body(test, Cutouts::BodyMaterial) else {
        return;
    };
    let expected: Vec<SlotShape> = CUTOUT_SLOTS
        .iter()
        .map(|&(mesh, _, cull, vertices, triangles)| {
            (mesh, BODY_MATERIAL, cull, vertices, triangles)
        })
        .collect();
    assert_eq!(shape(&body), expected);
    assert!(!body.edge);

    let out = rebuild(&donor, &body.slots, true, &HeaderAppend::default()).unwrap();
    write_for_the_oracle("c2650-body.flver", &out);
    assert_fingerprint("the Body-material c2650 FLVER", &out, BODY_FLVER);
    let check = check_flver(&out, &names("", false), false).unwrap();
    assert_eq!(check.named_textures, 0, "nothing names a texture by path");
    check_flver(&out, &names("_l", false), true).unwrap();
    let model = er_flver::parse(&out).expect("er-flver reads the c2650 fallback");
    for (mesh, _, _, vertices, _) in CUTOUT_SLOTS {
        assert_eq!(model.meshes[mesh].material_index, BODY_MATERIAL as usize);
        assert_eq!(model.meshes[mesh].positions.len(), vertices);
    }
}
