//! The fits on the real characters: c2650 through the Biped fit, c2280 through the same
//! [`fit::export`] entry with the probe's sidecars, and Elden Ring's unparented weapon bone placed
//! on its hand.
//!
//! These read the player's own Dark Souls Remastered install (`$MMB_DSR_DIR`, by default
//! `$HOME/.local/share/Steam/steamapps/common/DARK SOULS REMASTERED`) and an unpacked Elden Ring
//! tree for the donor (`$MMB_ER_CORPUS_ROOT`, by default
//! `$HOME/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713`), and skip when either is absent. The
//! numbers they hold are measurements of the fit, not game bytes.

mod common;

use std::collections::{HashMap, HashSet};

use common::{donor_or_skip, dsr_with_or_skip};
use mushroom_man_builder::{
    character::{C2280, C2650},
    fit::{self, MapRole},
    flver::{Flver, Vec3, bind_frames, compose_unparented},
    package::read_character,
};

fn distance(a: Vec3, b: Vec3) -> f32 {
    ((a.x - b.x).powi(2) + (a.y - b.y).powi(2) + (a.z - b.z).powi(2)).sqrt()
}

/// Elden Ring's `R_Weapon`, placed on `R_Hand`.
fn er_right_weapon(donor: &[u8]) -> Vec3 {
    let bones = Flver::parse(donor).unwrap().bones;
    let frames = bind_frames(&bones).unwrap();
    let index = |name: &str| bones.iter().position(|bone| bone.name == name).unwrap();
    compose_unparented(&bones, &frames, index("R_Weapon"), index("R_Hand")).origin
}

#[test]
fn elden_rings_weapon_bones_compose_onto_the_hands_where_the_player_skeleton_has_them() {
    let test = "elden_rings_weapon_bones_compose_onto_the_hands_where_the_player_skeleton_has_them";
    let Some(donor) = donor_or_skip(test) else {
        return;
    };
    let bones = Flver::parse(&donor).unwrap().bones;
    let frames = bind_frames(&bones).unwrap();
    let index = |name: &str| bones.iter().position(|bone| bone.name == name).unwrap();
    // Stored with no parent, the weapon bone's own bind is only its offset from the hand.
    assert_eq!(bones[index("R_Weapon")].parent_index, -1);
    assert!(frames[index("R_Weapon")].origin.y.abs() < 0.01);
    for (weapon, hand, at) in [
        ("R_Weapon", "R_Hand", [-0.5889, 0.9563, 0.0168]),
        ("L_Weapon", "L_Hand", [0.5889, 0.9563, 0.0168]),
    ] {
        let placed = compose_unparented(&bones, &frames, index(weapon), index(hand)).origin;
        let want = Vec3 {
            x: at[0],
            y: at[1],
            z: at[2],
        };
        assert!(distance(placed, want) < 1e-4, "{weapon} at {placed:?}");
    }
}

#[test]
fn c2650_fits_with_its_lantern_cut_and_its_grip_on_elden_rings_weapon() {
    let test = "c2650_fits_with_its_lantern_cut_and_its_grip_on_elden_rings_weapon";
    let (Some(dsr), Some(donor)) = (dsr_with_or_skip(test, "c2650"), donor_or_skip(test)) else {
        return;
    };
    let files = read_character(&dsr, &C2650).unwrap();
    let export = fit::export(&C2650, &files.flver, &donor).unwrap();
    assert_eq!(export.mesh.vertices.len(), 1745);
    assert_eq!(export.mesh.triangles.len(), 2456);
    assert_eq!(export.source_map.len(), 1745);
    // The girth scale is Elden Ring's pelvis height over c2650's.
    assert!(
        (export.scale - 0.97 / 0.9447).abs() < 1e-4,
        "{}",
        export.scale
    );

    // The lantern's own bone, carried by the right hand, lands within 0.03 m of Elden Ring's
    // weapon bone (0.022 m measured).
    let grip = export
        .grip_anchor
        .expect("c2650 holds its lantern by R_weapon");
    let gap = distance(grip, er_right_weapon(&donor));
    assert!(gap <= 0.03, "grip anchor {gap} m from R_Weapon");

    // Every bone's stretch along its length, as the prototype fit measured it.
    let stretch: HashMap<&str, f32> = export
        .stretch
        .iter()
        .map(|(bone, stretch)| (bone.as_str(), *stretch))
        .collect();
    for (bone, want) in [
        ("L Clavicle", 1.221),
        ("L UpperArm", 1.084),
        ("L Forearm", 0.889),
        ("L Hand", 0.939),
        ("L Finger1", 1.128),
        ("L Finger11", 1.027),
        ("L Finger2", 1.098),
        ("L Finger21", 1.027),
        ("L Thigh", 0.953),
        ("L Calf", 1.076),
        ("L Foot", 1.132),
        ("L Toe0", 1.027),
    ] {
        assert!(
            (stretch[bone] - want).abs() < 0.001,
            "{bone} {}",
            stretch[bone]
        );
        let right = bone.replacen('L', "R", 1);
        assert!(
            (stretch[right.as_str()] - stretch[bone]).abs() < 1e-4,
            "{right}"
        );
    }
    // The thumb rides the hand, so it has no map of its own.
    assert!(!stretch.contains_key("L Finger0"));

    // Every weight names a bone the donor has, and each vertex's weights add up to one.
    let donor_bones: HashSet<String> = Flver::parse(&donor)
        .unwrap()
        .bones
        .into_iter()
        .map(|bone| bone.name)
        .collect();
    let mut totals = vec![0.0_f32; export.mesh.vertices.len()];
    for row in &export.mesh.weights {
        assert!(
            donor_bones.contains(&row.target_bone),
            "{}",
            row.target_bone
        );
        totals[row.vertex] += row.weight;
    }
    assert!(totals.iter().all(|total| (total - 1.0).abs() < 1e-4));

    // Nothing of the item survives, and the tips are the three last finger segments a side.
    let roles: Vec<MapRole> = export.source_map.iter().map(|row| row.role).collect();
    assert!(!roles.contains(&MapRole::Prop));
    let tips: HashSet<&str> = export
        .source_map
        .iter()
        .filter(|row| row.role == MapRole::FingerTip)
        .map(|row| row.bone.as_str())
        .collect();
    let mut tips: Vec<&str> = tips.into_iter().collect();
    tips.sort_unstable();
    assert_eq!(
        tips,
        [
            "L Finger01",
            "L Finger11",
            "L Finger21",
            "R Finger01",
            "R Finger11",
            "R Finger21"
        ]
    );
    let json = export.fit_json("c2650.obj", "c2650_weights.tsv");
    assert!(json.contains("\"thumb\": \"hand\"") && json.contains("\"grip_anchor\": ["));
}

#[test]
fn c2280_maps_every_exported_vertex_onto_its_own_first_buffer() {
    let test = "c2280_maps_every_exported_vertex_onto_its_own_first_buffer";
    let (Some(dsr), Some(donor)) = (dsr_with_or_skip(test, "c2280"), donor_or_skip(test)) else {
        return;
    };
    let files = read_character(&dsr, &C2280).unwrap();
    let export = fit::export(&C2280, &files.flver, &donor).unwrap();
    assert_eq!(export.mesh.vertices.len(), 1108);
    assert!(
        export
            .source_map
            .iter()
            .enumerate()
            .all(|(index, row)| row.mesh == 0 && row.vertex == index)
    );
    // The digit tips the probe has always measured c2280 by: 121 left, 119 right.
    let tips = |side: char| {
        export
            .source_map
            .iter()
            .filter(|row| row.role == MapRole::FingerTip && row.bone.starts_with(side))
            .count()
    };
    assert_eq!((tips('L'), tips('R')), (121, 119));
    assert_eq!(export.grip_anchor, None);
    assert_eq!(
        export.fit_json("c2280_route_a_scaled.obj", "c2280_route_a_weights.tsv"),
        "{\"format\": 1, \"character\": \"c2280\", \"scale\": 1.225, \"params\": {\"scale\": \
         1.225, \"arm_lean\": 0.08, \"foot_spread\": 0.08, \"shoulder_blend\": 0.2}, \
         \"stretch\": {}, \"grip_anchor\": null, \"obj\": \"c2280_route_a_scaled.obj\", \
         \"weights\": \"c2280_route_a_weights.tsv\"}\n"
    );
}
