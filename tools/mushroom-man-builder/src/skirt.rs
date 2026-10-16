//! A Biped character's hanging cloth -- a skirt, a cape -- built into the naked body slot on a
//! vanilla part's cloth chains so Elden Ring's cloth simulation swings it.
//!
//! Dark Souls II hangs its cloth from bone chains that its physics moves: the Gyrm c1350's skirt
//! is `Xtra01` to `Xtra12` under the pelvis, four bones each, all round the hips from the belt to
//! above the knee, and Velstadt's (c3330) cape is `L_Cloth4` to `L_Cloth403` and its fellows off
//! his upper spine, down to 0.29 m behind his calves. The fit knows them only as followers of the
//! bone they hang from ([`crate::flver::skeleton`]'s `Ancestor` role), so they rode it rigidly:
//! Lonesome Gavlan's skirt hung straight down in front of his legs as he walked, and Velstadt's
//! cape stood off his back like a board (the user saw both, 2026-10-16). Elden Ring hangs its own
//! skirts and capes from `[cloth]` chains, which its cloth simulation drives.
//!
//! So the cloth goes there, the way [`crate::tail`] puts a tail on a cape:
//!
//! - **The part.** A skirt, every chain hanging from the pelvis, goes on [`CLOTH_PART_ID`],
//!   `bd_<gender>_1380`, which hangs 18 cloth chains all round the hips from 0.98 m down to
//!   0.23 m, no gap wider than 57 degrees, from the thighs (`L_ThighTwist`, `L_ThighTwist1`,
//!   `L_Thigh`), the calves and the pelvis. Cloth with a chain hanging from the spine is a cape
//!   and goes on the cape the tail hangs from, `bd_<gender>_2240` ([`crate::tail::CLOTH_PART_ID`]):
//!   three chains off `Spine` across the upper back from 1.20 m down, three off the pelvis behind
//!   down to 0.04 m, two panels on each thigh in front and a rope. Both genders' parts name their
//!   chains after the male one.
//! - **The weights.** Each hanging bone ([`hanging_bones`]) is shared out by where the fit puts
//!   its joint ([`hanging_targets`]): between the two cloth bones nearest it, by inverse
//!   distance, and the bones those hang from. How much stays put is the player's [`Rigidity`],
//!   stiffer at the root than the hem; of that, the trunk bone the chain hangs at keeps it all at
//!   the root, and the bones the cloth hangs from take it over toward the hem.
//! - **The cut.** A triangle with a corner on a cloth bone is the cloth's ([`crate::tail::split`]);
//!   the seam vertices weigh only on the body's own bones, so both halves move them alike.

use crate::{
    error::{Error, Result},
    flver::{
        Bone, Vec3, bind_frames,
        skeleton::{self, Role},
    },
    tail::{CLOTH_PREFIX, Rigidity},
};

/// The vanilla body part whose cloth a skirt hangs from: `bd_<gender>_1380`.
pub const CLOTH_PART_ID: &str = "1380";
const PELVIS: &str = "Pelvis";
/// How far apart, in metres, two cloth bones may be from a skirt joint and both count: a share
/// by inverse distance cannot divide by nothing.
const NEAREST_FLOOR: f32 = 0.01;

/// A source bone and the Elden Ring bones its weight is shared among, each with its share, as
/// [`crate::fit::export_with_targets`] takes them.
pub type BoneShares = (String, Vec<(String, f32)>);

/// Hanging cloth's stiffness by default: 80% at the root, 20% at the hem.
pub const DEFAULT_RIGIDITY: Rigidity = Rigidity {
    base: 0.8,
    tip: 0.2,
};

/// What a chain of hanging cloth hangs from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Root {
    Pelvis,
    /// A spine link; which of Elden Ring's trunk bones holds it depends on where the fit puts
    /// the chain's first joint ([`hanging_targets`]).
    Spine,
}

/// A bone of a chain of hanging cloth.
#[derive(Clone, Debug, PartialEq)]
pub struct HangingBone {
    pub name: String,
    /// How far down its chain it hangs, 0 at the root and 1 at the last.
    pub t: f32,
    /// The chain's first bone, whose joint says where on the trunk the chain hangs.
    pub chain_root: String,
    pub root: Root,
}

/// The source's hanging cloth: every bone of a chain hanging straight from the pelvis or a spine
/// link whose bones all follow it ([`Role::Ancestor`]), except a tail
/// ([`crate::tail::tail_bones`]). c1350's `Xtra13` hangs from its head, a hood, and is not one;
/// nor are Velstadt's `L_Cloth2` and `R_Cloth2`, tassels hanging from his pauldrons.
pub fn hanging_bones(bones: &[Bone]) -> Result<Vec<HangingBone>> {
    let roles = skeleton::classify(bones)?;
    let tail = crate::tail::tail_bones(bones);
    let parent = |index: usize| usize::try_from(bones[index].parent_index).ok();
    let mut out = Vec::new();
    for (root, role) in roles.iter().enumerate() {
        let from = match parent(root).map(|up| &roles[up]) {
            Some(Role::Direct(name)) if name == PELVIS => Root::Pelvis,
            Some(Role::SpineChain(_)) => Root::Spine,
            _ => continue,
        };
        if !matches!(role, Role::Ancestor(_)) || tail.contains(&bones[root].name) {
            continue;
        }
        // The chain down from the root, one child at a time.
        let mut chain = vec![root];
        while let Some(&last) = chain.last() {
            let next = (0..bones.len()).find(|&index| {
                parent(index) == Some(last) && matches!(roles[index], Role::Ancestor(_))
            });
            match next {
                Some(next) if !chain.contains(&next) => chain.push(next),
                _ => break,
            }
        }
        let last = (chain.len() - 1).max(1) as f32;
        out.extend(chain.iter().enumerate().map(|(depth, &index)| HangingBone {
            name: bones[index].name.clone(),
            t: depth as f32 / last,
            chain_root: bones[root].name.clone(),
            root: from,
        }));
    }
    Ok(out)
}

/// One `[cloth]` bone of the cloth part: its name, the first bone above it that is not cloth (a
/// thigh, a calf or the pelvis), and where its joint sits at bind.
#[derive(Clone, Debug, PartialEq)]
pub struct ClothBone {
    pub name: String,
    pub anchor: String,
    pub at: Vec3,
}

/// Every `[cloth]` bone of `bones`, the cloth part's skeleton.
pub fn cloth_bones(bones: &[Bone]) -> Result<Vec<ClothBone>> {
    let frames = bind_frames(bones)?;
    let mut out = Vec::new();
    for (index, bone) in bones.iter().enumerate() {
        if !bone.name.starts_with(CLOTH_PREFIX) {
            continue;
        }
        let mut up = bone.parent_index;
        let mut steps = 0;
        let anchor = loop {
            let Some(above) = usize::try_from(up).ok().and_then(|up| bones.get(up)) else {
                break None;
            };
            if !above.name.starts_with(CLOTH_PREFIX) {
                break Some(above.name.clone());
            }
            up = above.parent_index;
            steps += 1;
            if steps > bones.len() {
                break None;
            }
        };
        let anchor = anchor
            .ok_or_else(|| Error::new(format!("cloth bone {} hangs from nothing", bone.name)))?;
        out.push(ClothBone {
            name: bone.name.clone(),
            anchor,
            at: frames[index].origin,
        });
    }
    Ok(out)
}

/// Where each hanging bone weighs: `hanging` from [`hanging_bones`], `joints` where the fit puts
/// each source bone ([`crate::fit::biped::fitted_joints`]), `cloth` the part's [`cloth_bones`],
/// and `trunk` Elden Ring's pelvis and spine bones with their joint heights, rising.
///
/// A bone `t` down its chain keeps what `rigidity` says there: `1 - t` of that on the trunk bone
/// its chain hangs at and `t` on the bones its two nearest cloth bones hang from. The rest goes
/// to those two cloth bones. The two split everything by inverse distance to the joint. A chain
/// off the pelvis hangs at the pelvis; one off a spine link at the highest of `trunk` whose joint
/// is not above the chain's first joint.
pub fn hanging_targets(
    hanging: &[HangingBone],
    joints: &[(String, Vec3)],
    cloth: &[ClothBone],
    trunk: &[(String, f32)],
    rigidity: Rigidity,
) -> Result<Vec<BoneShares>> {
    if cloth.len() < 2 {
        return Err(Error::new(format!(
            "the cloth part has {} cloth bones; hanging cloth needs at least two",
            cloth.len()
        )));
    }
    let joint = |bone: &str| {
        joints
            .iter()
            .find(|(name, _)| name == bone)
            .map(|(_, at)| *at)
            .ok_or_else(|| Error::new(format!("the fit does not place hanging bone {bone}")))
    };
    let mut out = Vec::with_capacity(hanging.len());
    for HangingBone {
        name: bone,
        t,
        chain_root,
        root,
    } in hanging
    {
        let at = joint(bone)?;
        let root_bone = match root {
            Root::Pelvis => PELVIS.to_owned(),
            Root::Spine => {
                let height = joint(chain_root)?.y;
                trunk
                    .iter()
                    .rev()
                    .find(|(_, y)| *y <= height)
                    .map_or_else(|| PELVIS.to_owned(), |(name, _)| name.clone())
            }
        };
        let distance = |cloth: &ClothBone| {
            let (dx, dy, dz) = (cloth.at.x - at.x, cloth.at.y - at.y, cloth.at.z - at.z);
            (dx * dx + dy * dy + dz * dz).sqrt()
        };
        let mut nearest: Vec<(f32, &ClothBone)> =
            cloth.iter().map(|cloth| (distance(cloth), cloth)).collect();
        nearest.sort_by(|a, b| a.0.total_cmp(&b.0));
        let pair = [nearest[0], nearest[1]];
        let inverse = pair.map(|(distance, _)| 1.0 / distance.max(NEAREST_FLOOR));
        let total = inverse[0] + inverse[1];
        let stiff = 1.0 - rigidity.chain_share(*t);
        let mut shares: Vec<(String, f32)> = vec![(root_bone, stiff * (1.0 - t))];
        for ((_, cloth), inverse) in pair.iter().zip(inverse) {
            let part = inverse / total;
            shares.push((cloth.anchor.clone(), stiff * t * part));
            shares.push((cloth.name.clone(), (1.0 - stiff) * part));
        }
        // One row per bone: the trunk bone may also be a cloth bone's anchor.
        let mut merged: Vec<(String, f32)> = Vec::new();
        for (name, share) in shares {
            match merged.iter_mut().find(|(seen, _)| *seen == name) {
                Some((_, sum)) => *sum += share,
                None => merged.push((name, share)),
            }
        }
        merged.retain(|(_, share)| *share > 0.0);
        out.push((bone.clone(), merged));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bone(name: &str, parent: i16, y: f32) -> Bone {
        Bone {
            name: name.to_owned(),
            parent_index: parent,
            translation: Vec3 { x: 0.0, y, z: 0.0 },
            rotation: Vec3::default(),
            scale: Vec3 {
                x: 1.0,
                y: 1.0,
                z: 1.0,
            },
        }
    }

    fn v(x: f32, y: f32, z: f32) -> Vec3 {
        Vec3 { x, y, z }
    }

    #[test]
    fn hanging_cloth_is_the_chains_off_the_pelvis_and_spine_and_not_a_tail_hood_or_tassel() {
        // A minimal Biped: pelvis, spine, neck, head, both arms and legs, then a two-bone skirt
        // chain and a one-bone one off the pelvis, a tail, a hood off the head, a two-bone cape
        // off the spine and a tassel off a clavicle.
        let names: [(&str, i16); 22] = [
            ("Pelvis", -1),
            ("Spine", 0),
            ("Neck", 1),
            ("Head", 2),
            ("L Clavicle", 2),
            ("L UpperArm", 4),
            ("L Forearm", 5),
            ("L Hand", 6),
            ("R Clavicle", 2),
            ("R UpperArm", 8),
            ("R Forearm", 9),
            ("R Hand", 10),
            ("L Thigh", 0),
            ("L Calf", 12),
            ("L Foot", 13),
            ("R Thigh", 0),
            ("R Calf", 15),
            ("R Foot", 16),
            ("Xtra01", 0),
            ("Xtra0102", 18),
            ("tail_00", 0),
            ("Hood", 3),
        ];
        let mut bones: Vec<Bone> = names
            .iter()
            .map(|&(name, parent)| bone(name, parent, 0.0))
            .collect();
        bones.push(bone("Xtra02", 0, 0.0));
        bones.push(bone("L_Cloth4", 1, 0.0));
        bones.push(bone("L_Cloth402", 23, 0.0));
        bones.push(bone("L_Cloth2", 4, 0.0));
        let hanging = |name: &str, t: f32, chain_root: &str, root: Root| HangingBone {
            name: name.to_owned(),
            t,
            chain_root: chain_root.to_owned(),
            root,
        };
        assert_eq!(
            hanging_bones(&bones).unwrap(),
            [
                hanging("Xtra01", 0.0, "Xtra01", Root::Pelvis),
                hanging("Xtra0102", 1.0, "Xtra01", Root::Pelvis),
                hanging("Xtra02", 0.0, "Xtra02", Root::Pelvis),
                hanging("L_Cloth4", 0.0, "L_Cloth4", Root::Spine),
                hanging("L_Cloth402", 1.0, "L_Cloth4", Root::Spine),
            ]
        );
    }

    #[test]
    fn a_hanging_bone_shares_out_to_its_nearest_cloth_and_its_anchors_take_over_down_the_chain() {
        let cloth = [
            ClothBone {
                name: "[cloth]Front_L".to_owned(),
                anchor: "L_ThighTwist".to_owned(),
                at: v(0.09, 0.89, -0.13),
            },
            ClothBone {
                name: "[cloth]Front_R".to_owned(),
                anchor: "R_ThighTwist".to_owned(),
                at: v(-0.09, 0.89, -0.13),
            },
            ClothBone {
                name: "[cloth]Back".to_owned(),
                anchor: "Pelvis".to_owned(),
                at: v(0.0, 0.9, 0.2),
            },
        ];
        let joints = [
            ("Xtra01".to_owned(), v(0.09, 0.89, -0.13)),
            ("Xtra0104".to_owned(), v(0.0, 0.89, -0.13)),
            ("L_Cloth4".to_owned(), v(0.0, 1.15, 0.2)),
        ];
        let trunk = [
            ("Pelvis".to_owned(), 0.97),
            ("Spine".to_owned(), 1.06),
            ("Spine1".to_owned(), 1.15),
            ("Spine2".to_owned(), 1.24),
        ];
        let hanging = |name: &str, t: f32, root: Root| HangingBone {
            name: name.to_owned(),
            t,
            chain_root: name.to_owned(),
            root,
        };
        let rigidity = DEFAULT_RIGIDITY;
        let targets = hanging_targets(
            &[
                hanging("Xtra01", 0.0, Root::Pelvis),
                hanging("Xtra0104", 1.0, Root::Pelvis),
                hanging("L_Cloth4", 0.0, Root::Spine),
            ],
            &joints,
            &cloth,
            &trunk,
            rigidity,
        )
        .unwrap();
        let share = |row: usize, name: &str| {
            targets[row]
                .1
                .iter()
                .find(|(bone, _)| bone == name)
                .map_or(0.0, |(_, share)| *share)
        };
        let close = |a: f32, b: f32| (a - b).abs() < 1e-4;
        for (bone, shares) in &targets {
            let total: f32 = shares.iter().map(|(_, share)| share).sum();
            assert!(close(total, 1.0), "{bone} adds up to {total}");
        }
        // At the belt: the pelvis keeps the stiff share, the cloth on the joint takes the rest.
        assert!(close(share(0, "Pelvis"), rigidity.base));
        assert!(share(0, "[cloth]Front_L") > 0.18);
        assert!(share(0, "L_ThighTwist") < 1e-6);
        // At the hem, halfway between the two front cloth bones: they split evenly, and the stiff
        // share is the thighs', not the pelvis's.
        assert!(close(share(1, "Pelvis"), 0.0));
        assert!(close(share(1, "L_ThighTwist"), share(1, "R_ThighTwist")));
        assert!(close(share(1, "L_ThighTwist"), rigidity.tip / 2.0));
        assert!(close(
            share(1, "[cloth]Front_L"),
            (1.0 - rigidity.tip) / 2.0
        ));
        assert_eq!(share(1, "[cloth]Back"), 0.0);
        // A cape's root hangs at the trunk bone its first joint is at, not the pelvis.
        assert!(close(share(2, "Spine1"), rigidity.base));
        assert!(close(share(2, "Pelvis"), 0.0));
        assert!(share(2, "[cloth]Back") > 0.1);
        // Too few cloth bones, or a bone the fit does not place, is refused.
        let unplaced = [hanging("Xtra09", 0.5, Root::Pelvis)];
        assert!(hanging_targets(&[], &joints, &cloth[..1], &trunk, rigidity).is_err());
        assert!(hanging_targets(&unplaced, &joints, &cloth, &trunk, rigidity).is_err());
    }
}
