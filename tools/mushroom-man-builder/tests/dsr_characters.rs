//! The builder's reading of real Dark Souls Remastered characters: c2650's binder, meshes,
//! skeleton and held-item strip, c2280 read through the same multi-mesh parser, and c2900, whose
//! four held items are meshes of their own and whose body is drawn with textures out of c2909's
//! binder.
//!
//! These read the player's own DSR install and skip when it is absent: the install is
//! `$MMB_DSR_DIR`, by default `$HOME/.local/share/Steam/steamapps/common/DARK SOULS REMASTERED`.
//! Nothing here holds game bytes, only the names and counts they are checked against.

mod common;

use std::collections::BTreeMap;

use common::dsr_with_or_skip;
use mushroom_man_builder::{
    character::{C2280, C2650, C2900},
    flver::{
        skeleton::{self, Role},
        source,
    },
    package::{kept_materials, read_character},
    textures::{TextureSet, character_tpf_from},
};

#[test]
fn c2650_reads_its_own_model_and_texture_set() {
    let Some(dsr) = dsr_with_or_skip("c2650_reads_its_own_model_and_texture_set", "c2650") else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    assert_eq!(&files.flver[..6], b"FLVER\0");
    assert_eq!(files.textures.sets, [TextureSet::named_after("c2650")]);
    let tpf = er_tpf::Tpf::parse(&files.tpf).unwrap();
    let mut names: Vec<&str> = tpf.textures.iter().map(|t| t.name.as_str()).collect();
    names.sort_unstable();
    assert_eq!(names, ["c2650", "c2650_n", "c2650_s"]);
}

#[test]
fn c2280_resolves_the_set_it_has_always_shipped() {
    let Some(dsr) = dsr_with_or_skip("c2280_resolves_the_set_it_has_always_shipped", "c2280")
    else {
        return;
    };
    let files = read_character(&dsr, &C2280).unwrap();
    assert_eq!(files.textures.sets, [TextureSet::named_after("c2280")]);
}

#[test]
fn c2650_parses_into_five_meshes_with_their_lod0_counts() {
    let test = "c2650_parses_into_five_meshes_with_their_lod0_counts";
    let Some(dsr) = dsr_with_or_skip(test, "c2650") else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    let source = source::parse(&files.flver).unwrap();
    let per_mesh =
        |f: fn(&source::DsrMesh) -> usize| -> Vec<usize> { source.meshes.iter().map(f).collect() };
    assert_eq!(per_mesh(|m| m.lods[0].len()), [1592, 736, 480, 243, 216]);
    assert_eq!(source.lod0_triangle_count(), 3267);
    assert_eq!(per_mesh(|m| m.vertices.len()), [1133, 646, 368, 320, 140]);
    assert_eq!(source.vertex_count(), 2607);
    assert_eq!(source.lod0_vertex_count(), 2478);
    // Meshes 2 and 3 are the cowl, cuffs and hem on the `_Edge` material; 4 is the skirt. All
    // three are drawn double-sided.
    assert_eq!(per_mesh(|m| m.material_index), [0, 0, 1, 1, 2]);
    let cull: Vec<bool> = source.meshes.iter().map(|m| m.cull_backfaces).collect();
    assert_eq!(cull, [true, true, false, false, false]);
    let mtds: Vec<&str> = source
        .materials
        .iter()
        .map(|m| m.mtd.rsplit('\\').next().unwrap())
        .collect();
    assert_eq!(
        mtds,
        [
            "C_DullLeather[DSB].mtd",
            "C_DullLeather[DSB]_Edge.mtd",
            "C_DullLeather[DSB].mtd"
        ]
    );
}

/// What each of c2650's 93 bones should become, in bone order. Read from
/// `c2650-to-er-bone-map.tsv` of the investigation, with four deliberate departures from it:
///
/// - a twist link rides its core bone (`L ForeTwist` with `L Forearm`), where the table sent it
///   to Elden Ring's own twist bone; which of the two it weights is the fit's choice;
/// - `R_weapon` and the `R_W_Cloth` chain under it are a held item to strip, where the table
///   offered `R_Hand` as an alternative;
/// - `Spine` and `Spine1` are spine-chain ranks 0 and 1, where the table named Elden Ring's `Spine`
///   and `Spine2`, because which spine bone a vertex feeds depends on the fit;
/// - the root-level Nubs are unused, as the table has them, rather than resolved to a parent they
///   do not have.
enum Expect {
    Direct(&'static str),
    Spine(usize),
    Twist(&'static str),
    Ancestor(&'static str),
    Prop,
    Unused,
}

const C2650_ROLES: [(&str, Expect); 93] = {
    use Expect::*;
    [
        ("Pelvis", Direct("Pelvis")),
        ("Spine", Spine(0)),
        ("Spine1", Spine(1)),
        ("Neck", Direct("Neck")),
        ("Head", Direct("Head")),
        ("Jaw", Direct("Head")),
        ("L Clavicle", Direct("L_Clavicle")),
        ("L UpperArm", Direct("L_UpperArm")),
        ("L Forearm", Direct("L_Forearm")),
        ("L Hand", Direct("L_Hand")),
        ("L Finger0", Direct("L_Finger0")),
        ("L Finger01", Direct("L_Finger01")),
        ("L Finger1", Direct("L_Finger1")),
        ("L Finger11", Direct("L_Finger11")),
        ("L Finger2", Direct("L_Finger2")),
        ("L Finger21", Direct("L_Finger21")),
        ("L_FA_Cloth_a1", Ancestor("L Forearm")),
        ("L_FA_Cloth_a2", Ancestor("L Forearm")),
        ("L_FA_Cloth_b1", Ancestor("L Forearm")),
        ("L_FA_Cloth_c1", Ancestor("L Forearm")),
        ("L_FA_Cloth_d1", Ancestor("L Forearm")),
        ("L ForeTwist", Twist("L Forearm")),
        ("R Clavicle", Direct("R_Clavicle")),
        ("R UpperArm", Direct("R_UpperArm")),
        ("R Forearm", Direct("R_Forearm")),
        ("R Hand", Direct("R_Hand")),
        ("R Finger0", Direct("R_Finger0")),
        ("R Finger01", Direct("R_Finger01")),
        ("R Finger1", Direct("R_Finger1")),
        ("R Finger11", Direct("R_Finger11")),
        ("R Finger2", Direct("R_Finger2")),
        ("R Finger21", Direct("R_Finger21")),
        ("R_weapon", Prop),
        ("R_W_Cloth_a1", Prop),
        ("R_W_Cloth_a2", Prop),
        ("R_W_Cloth_a3", Prop),
        ("R_FA_Cloth_a1", Ancestor("R Forearm")),
        ("R_FA_Cloth_a2", Ancestor("R Forearm")),
        ("R_FA_Cloth_b1", Ancestor("R Forearm")),
        ("R_FA_Cloth_c1", Ancestor("R Forearm")),
        ("R_FA_Cloth_d1", Ancestor("R Forearm")),
        ("R ForeTwist", Twist("R Forearm")),
        ("S_Cloth_b1", Ancestor("Spine1")),
        ("S_Cloth_b2", Ancestor("Spine1")),
        ("S_Cloth_a1", Ancestor("Spine1")),
        ("S_Cloth_a2", Ancestor("Spine1")),
        ("S_Cloth_c1", Ancestor("Spine1")),
        ("S_Cloth_c2", Ancestor("Spine1")),
        ("S_Cloth_d1", Ancestor("Spine1")),
        ("S_Cloth_d2", Ancestor("Spine1")),
        ("S_Cloth_e1", Ancestor("Spine1")),
        ("S_Cloth_e2", Ancestor("Spine1")),
        ("S_Cloth_f1", Ancestor("Spine1")),
        ("S_Cloth_f2", Ancestor("Spine1")),
        ("S_Cloth_food", Ancestor("Spine1")),
        ("L Thigh", Direct("L_Thigh")),
        ("L Calf", Direct("L_Calf")),
        ("L Foot", Direct("L_Foot")),
        ("L Toe0", Direct("L_Toe0")),
        ("Cloth_f00", Ancestor("L Calf")),
        ("Cloth_f01", Ancestor("L Calf")),
        ("Cloth_e00", Ancestor("L Calf")),
        ("Cloth_e01", Ancestor("L Calf")),
        ("Cloth_d00", Ancestor("L Calf")),
        ("Cloth_d01", Ancestor("L Calf")),
        ("LCalfTwist", Twist("L Calf")),
        ("R Thigh", Direct("R_Thigh")),
        ("R Calf", Direct("R_Calf")),
        ("R Foot", Direct("R_Foot")),
        ("R Toe0", Direct("R_Toe0")),
        ("Cloth_b00", Ancestor("R Calf")),
        ("Cloth_b01", Ancestor("R Calf")),
        ("Cloth_a00", Ancestor("R Calf")),
        ("Cloth_a01", Ancestor("R Calf")),
        ("Cloth_c00", Ancestor("R Calf")),
        ("Cloth_c01", Ancestor("R Calf")),
        ("RCalfTwist", Twist("R Calf")),
        ("c2650", Unused),
        ("R_Light", Unused),
        ("Model_Dmy", Unused),
        ("sfx_dummy", Unused),
        ("固定dmy", Unused),
        ("Master", Unused),
        ("HeadNub", Unused),
        ("JawNub", Unused),
        ("L Finger0Nub", Unused),
        ("L Finger1Nub", Unused),
        ("L Finger2Nub", Unused),
        ("R Finger0Nub", Unused),
        ("R Finger1Nub", Unused),
        ("R Finger2Nub", Unused),
        ("L Toe0Nub", Unused),
        ("R Toe0Nub", Unused),
    ]
};

#[test]
fn c2650_bones_get_the_written_role_table() {
    let Some(dsr) = dsr_with_or_skip("c2650_bones_get_the_written_role_table", "c2650") else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    let source = source::parse(&files.flver).unwrap();
    assert!(skeleton::is_biped(&source.bones));
    let roles = skeleton::classify(&source.bones).unwrap();
    assert_eq!(roles.len(), C2650_ROLES.len());
    let name = |index: usize| source.bones[index].name.as_str();
    for (index, (role, (expected_name, expected))) in roles.iter().zip(&C2650_ROLES).enumerate() {
        assert_eq!(name(index), *expected_name, "bone {index}");
        let ok = match (role, expected) {
            (Role::Direct(target), Expect::Direct(want)) => target == want,
            (Role::SpineChain(rank), Expect::Spine(want)) => rank == want,
            (Role::Twist(core), Expect::Twist(want)) => name(*core) == *want,
            (Role::Ancestor(up), Expect::Ancestor(want)) => name(*up) == *want,
            (Role::Prop, Expect::Prop) | (Role::Unused, Expect::Unused) => true,
            _ => false,
        };
        assert!(ok, "bone {index} {expected_name:?} came out {role:?}");
    }
    // Every weighted bone maps somewhere: nothing carrying weight is unused.
    for (index, weight) in source.bone_weights().into_iter().enumerate() {
        assert!(
            weight == 0.0 || roles[index] != Role::Unused,
            "{} carries {weight}",
            name(index)
        );
    }
}

#[test]
fn c2650_loses_its_lantern_vertex_by_vertex() {
    let Some(dsr) = dsr_with_or_skip("c2650_loses_its_lantern_vertex_by_vertex", "c2650") else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    let source = source::parse(&files.flver).unwrap();
    let roles = skeleton::classify(&source.bones).unwrap();
    let (kept, report) = source::strip_props(&source, &roles);
    assert_eq!(report.stripped_vertices, 817);
    assert_eq!(report.kept_vertices, 1745);
    assert_eq!(report.kept_triangles, 2456);
    assert_eq!(report.dropped_triangles, 3267 - 2456);
    // All five meshes keep something: the lantern shares meshes 1 and 3 with the left shin and
    // the hem, so no mesh goes whole.
    assert_eq!(kept.len(), 5);
    for mesh in &kept {
        let count = mesh.vertices.len() as u32;
        assert!(
            mesh.triangles
                .iter()
                .flatten()
                .all(|&corner| corner < count)
        );
    }
}

/// Per DSR mesh, the vertices the held-item strip cuts, counted by the bone carrying most of each
/// one's held-item weight, in bone-name order.
fn stripped_by_bone(source: &source::Source, roles: &[Role]) -> Vec<(usize, Vec<(String, usize)>)> {
    source
        .meshes
        .iter()
        .enumerate()
        .map(|(index, mesh)| {
            let mut counts: BTreeMap<String, usize> = BTreeMap::new();
            for vertex in &mesh.vertices {
                let (mut prop, mut total, mut heaviest) = (0.0, 0.0, (0.0, usize::MAX));
                for (slot, weight) in vertex.bone_indices.iter().zip(vertex.bone_weights) {
                    if weight <= 0.0 {
                        continue;
                    }
                    total += weight;
                    let bone = mesh.bone(*slot);
                    if roles.get(bone) == Some(&Role::Prop) {
                        prop += weight;
                        if weight > heaviest.0 {
                            heaviest = (weight, bone);
                        }
                    }
                }
                if total > 0.0 && prop >= 0.5 * total {
                    *counts
                        .entry(source.bones[heaviest.1].name.clone())
                        .or_default() += 1;
                }
            }
            (index, counts.into_iter().collect())
        })
        .collect()
}

#[test]
fn c2900_cuts_its_four_held_items_whole_and_nothing_of_its_body() {
    let test = "c2900_cuts_its_four_held_items_whole_and_nothing_of_its_body";
    let Some(dsr) = dsr_with_or_skip(test, "c2900") else {
        return;
    };
    let files = read_character(&dsr, &C2900).unwrap();
    let source = source::parse(&files.flver).unwrap();
    let per_mesh =
        |f: fn(&source::DsrMesh) -> usize| -> Vec<usize> { source.meshes.iter().map(f).collect() };
    // Meshes 0-2 are the body; 3 the sword, 4 the shield, 5 the bow with its arrow, 6 the
    // greatsword, each on a material of its own.
    assert_eq!(
        per_mesh(|m| m.vertices.len()),
        [1869, 105, 750, 359, 338, 919, 195]
    );
    assert_eq!(per_mesh(|m| m.material_index), [0, 0, 1, 2, 3, 4, 5]);
    assert_eq!(
        per_mesh(|m| m.lods[0].len()),
        [1754, 92, 583, 214, 268, 374, 150]
    );
    let roles = skeleton::classify(&source.bones).unwrap();
    let props: Vec<&str> = roles
        .iter()
        .zip(&source.bones)
        .filter(|(role, _)| **role == Role::Prop)
        .map(|(_, bone)| bone.name.as_str())
        .collect();
    assert_eq!(
        props,
        [
            "L_shield",
            "L_bow",
            "L_bow01",
            "L_bowstring",
            "R Weapon",
            "WP_A_0400",
            "WP_A_1500",
            "WP_A_1500_dymmy",
            "WP_A_1500_SFX用",
            "WP_A_0401"
        ]
    );
    let (kept, report) = source::strip_props(&source, &roles);
    assert_eq!(
        (
            report.stripped_vertices,
            report.dropped_triangles,
            report.unreferenced_vertices,
            report.kept_vertices,
            report.kept_triangles
        ),
        (1811, 1006, 123, 2601, 2429)
    );
    // Each body mesh keeps every LOD0 triangle it had; the four items go whole.
    let kept_shape: Vec<(usize, usize, usize)> = kept
        .iter()
        .map(|mesh| (mesh.source_mesh, mesh.vertices.len(), mesh.triangles.len()))
        .collect();
    assert_eq!(kept_shape, [(0, 1786, 1754), (1, 100, 92), (2, 715, 583)]);
    // What was cut, by the bone carrying it: every vertex of meshes 3-6, the bow's bowstring and
    // the arrow in the right hand among them, and none of meshes 0-2.
    let by_bone: Vec<(usize, Vec<(String, usize)>)> = stripped_by_bone(&source, &roles);
    let expected: Vec<(usize, Vec<(String, usize)>)> = [
        (0, vec![]),
        (1, vec![]),
        (2, vec![]),
        (3, vec![("R Weapon", 359)]),
        (4, vec![("L_shield", 338)]),
        (
            5,
            vec![
                ("L_bow", 691),
                ("L_bow01", 201),
                ("L_bowstring", 10),
                ("R Weapon", 17),
            ],
        ),
        (6, vec![("R Weapon", 195)]),
    ]
    .into_iter()
    .map(|(mesh, counts)| {
        let counts = counts
            .into_iter()
            .map(|(bone, count)| (bone.to_owned(), count))
            .collect();
        (mesh, counts)
    })
    .collect();
    assert_eq!(by_bone, expected);
    for (mesh, counts) in &by_bone {
        let cut: usize = counts.iter().map(|(_, count)| count).sum();
        assert_eq!(
            cut == source.meshes[*mesh].vertices.len(),
            *mesh >= 3,
            "mesh {mesh}"
        );
    }
}

/// Name, TPF format byte, DDS four-character code and DXGI format of every texture in `tpf`.
fn formats(tpf: &[u8]) -> Vec<(String, u8, String, Option<u32>)> {
    er_tpf::Tpf::parse(tpf)
        .unwrap()
        .textures
        .into_iter()
        .map(|texture| {
            let fourcc = texture.dds[0x54..0x58].escape_ascii().to_string();
            let dxgi = (fourcc == "DX10")
                .then(|| u32::from_le_bytes(texture.dds[0x80..0x84].try_into().unwrap()));
            (texture.name, texture.format, fourcc, dxgi)
        })
        .collect()
}

#[test]
fn c2900_draws_with_c2909s_textures_out_of_c2909s_binder() {
    let test = "c2900_draws_with_c2909s_textures_out_of_c2909s_binder";
    let (Some(dsr), Some(_)) = (
        dsr_with_or_skip(test, "c2900"),
        dsr_with_or_skip(test, "c2909"),
    ) else {
        return;
    };
    let files = read_character(&dsr, &C2900).unwrap();
    let source = source::parse(&files.flver).unwrap();
    // Only the two body materials are kept, and every map they name is in c2909's folder.
    assert_eq!(kept_materials(&C2900, &source).unwrap(), [0, 1]);
    for material in &source.materials[..2] {
        let paths: Vec<&str> = material
            .textures
            .iter()
            .map(|(_, path)| path.as_str())
            .filter(|path| !path.is_empty())
            .collect();
        assert_eq!(
            paths,
            ["c2909", "c2909_s", "c2909_n"]
                .map(|stem| format!(r"N:\FRPG\data\Model\chr\c2909\tex\{stem}.tga"))
        );
    }
    assert_eq!(files.textures.sets, [TextureSet::named_after("c2909")]);
    assert_eq!(files.textures.owners(), ["c2909"]);
    // Read from every material, the body and the weapons are five sets, not one.
    assert!(TextureSet::of_materials(&source.materials, C2900.id).is_err());

    // c2900's own TPF holds only the weapons' textures; c2909's binder holds the body's.
    let own: Vec<String> = formats(&files.tpf).into_iter().map(|t| t.0).collect();
    assert_eq!(own.len(), 12);
    assert!(
        own.iter().all(|name| name.starts_with("c2900_WP_A_")),
        "{own:?}"
    );
    let [(owner, lent)] = files.borrowed_tpfs.as_slice() else {
        panic!(
            "c2900 borrows from one binder: {:?}",
            files.borrowed_tpfs.len()
        );
    };
    assert_eq!(owner, "c2909");
    let lent_textures = formats(lent);
    // The shape of c2280's set: DXT1 albedo and specular, a BC5 normal map.
    assert_eq!(
        lent_textures,
        [
            ("c2909_s".to_owned(), 0, "DXT1".to_owned(), None),
            ("c2909".to_owned(), 0, "DXT1".to_owned(), None),
            ("c2909_n".to_owned(), 36, "DX10".to_owned(), Some(83)),
        ]
    );

    // The body TPF the package carries: c2909's specular and normal map byte for byte, its
    // albedo under the sRGB header (BC1 sRGB, 72) with the same blocks.
    let built = character_tpf_from(
        &files.texture_tpfs(),
        files.textures.primary(),
        "FC_M_0000",
        "",
    )
    .unwrap();
    assert_eq!(
        formats(&built),
        [
            ("FC_M_0000_3m".to_owned(), 0, "DXT1".to_owned(), None),
            ("FC_M_0000_a".to_owned(), 0, "DX10".to_owned(), Some(72)),
            ("FC_M_0000_n".to_owned(), 107, "DX10".to_owned(), Some(83)),
        ]
    );
    let dds = |tpf: &[u8], name: &str| {
        er_tpf::Tpf::parse(tpf)
            .unwrap()
            .textures
            .into_iter()
            .find(|texture| texture.name == name)
            .unwrap()
            .dds
    };
    assert_eq!(dds(&built, "FC_M_0000_3m"), dds(lent, "c2909_s"));
    assert_eq!(dds(&built, "FC_M_0000_n"), dds(lent, "c2909_n"));
    assert_eq!(
        dds(&built, "FC_M_0000_a")[0x80 + 20..],
        dds(lent, "c2909")[0x80..]
    );
}

#[test]
fn c2280_and_c2650_keep_every_material_they_have() {
    for character in [&C2280, &C2650] {
        let Some(dsr) = dsr_with_or_skip(
            "c2280_and_c2650_keep_every_material_they_have",
            character.id,
        ) else {
            continue;
        };
        let files = read_character(&dsr, character).unwrap();
        let source = source::parse(&files.flver).unwrap();
        let every: Vec<usize> = (0..source.materials.len()).collect();
        assert_eq!(
            kept_materials(character, &source).unwrap(),
            every,
            "{}",
            character.id
        );
        assert!(files.borrowed_tpfs.is_empty(), "{}", character.id);
        assert_eq!(files.textures.owners(), [character.id]);
    }
}

#[test]
fn c2280_through_the_multi_mesh_parser_keeps_its_whole_buffer() {
    let test = "c2280_through_the_multi_mesh_parser_keeps_its_whole_buffer";
    let Some(dsr) = dsr_with_or_skip(test, "c2280") else {
        return;
    };
    let files = read_character(&dsr, &C2280).unwrap();
    let source = source::parse(&files.flver).unwrap();
    assert_eq!(source.meshes.len(), 1);
    assert_eq!(source.meshes[0].vertices.len(), 1108);
    assert_eq!(source.meshes[0].lods[0].len(), 1708);
    // FromSoft's own names (`LArm1`, `LArmPalm`, `LLeg1`) now read as a rig the Biped fit takes;
    // c2280 keeps the fit of its own the user approved, through its `FitConfig::Mushroom`.
    assert!(skeleton::is_biped(&source.bones));
}
