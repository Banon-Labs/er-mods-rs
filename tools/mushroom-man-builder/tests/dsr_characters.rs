//! The builder's reading of real Dark Souls Remastered characters: c2650's binder, meshes,
//! skeleton and held-item strip, and c2280 read through the same multi-mesh parser.
//!
//! These read the player's own DSR install and skip when it is absent: the install is
//! `$MMB_DSR_DIR`, by default `$HOME/.local/share/Steam/steamapps/common/DARK SOULS REMASTERED`.
//! Nothing here holds game bytes, only the names and counts they are checked against.

mod common;

use common::dsr_with_or_skip;
use mushroom_man_builder::{
    character::{C2280, C2650},
    flver::{
        skeleton::{self, Role},
        source,
    },
    package::read_character,
    textures::TextureSet,
};

#[test]
fn c2650_reads_its_own_model_and_texture_set() {
    let Some(dsr) = dsr_with_or_skip("c2650_reads_its_own_model_and_texture_set", "c2650") else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    assert_eq!(&files.flver[..6], b"FLVER\0");
    assert_eq!(files.textures, TextureSet::named_after("c2650"));
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
    assert_eq!(files.textures, TextureSet::named_after("c2280"));
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
    assert!(!skeleton::is_biped(&source.bones));
}
