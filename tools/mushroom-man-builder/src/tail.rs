//! A Biped character's tail, built into the naked body slot on a vanilla part's cloth chain so
//! Elden Ring's cloth simulation swings it.
//!
//! Welded to the pelvis, a tail is a stiff plank: Priscilla's (c2730) stands out level behind her
//! at hip height, 1.75 m of it. Dark Souls moves it with her animations (every one of her 27
//! clips keyframes all ten tail bones, `scripts/dsr-chr-physics.py`), and Elden Ring's player
//! animations know nothing of those bones. What Elden Ring does move on its own is a part's
//! `[cloth]` bone chain, driven by the Havok cloth data in the part's binder, and the naked body
//! slot `mushroom_man.dll` sends the game to runs that cloth: `bd_*_0000` written as vanilla
//! `bd_*_2240` swung its back cape in game (2026-10-15, `--cloth-test-body 2240`).
//!
//! So the tail goes there:
//!
//! - **The chain.** `BD_M_2240`'s back cape hangs from three cloth bones in a line off the pelvis,
//!   [`CHAIN`], from 0.78 m down to 0.19 m behind. The female part names its chain after the male
//!   one, so one table serves both.
//! - **The weights.** Each tail bone, root first ([`tail_bones`]), is shared between the pelvis
//!   and the chain ([`chain_targets`]) as the player's [`Rigidity`] says, stiffer at the base than
//!   the tip, the chain's part split between the two cloth bones nearest the bone's place along
//!   the tail so it bends smoothly. The fit carries them as before and only their weight moves
//!   ([`crate::fit::export_with_targets`]).
//! - **The cut.** A triangle with a corner weighing on the chain is the tail's ([`split`]). A
//!   vertex the tail and the body share therefore weighs on no chain bone, so both halves move it
//!   alike and the seam cannot open; the tail bends from the first ring of vertices past it.
//! - **The part.** The tail's triangles go into the cape's own model, every one of its meshes
//!   hidden and its cloth files kept, drawn with retargeted `C[AMSN]` materials whose textures
//!   ride in its own TPF (`crate::package`).

use crate::{
    error::{Error, Result},
    fit::Export,
    flver::Bone,
};

/// The vanilla body part whose cloth the tail hangs from: `bd_<gender>_2240`.
pub const CLOTH_PART_ID: &str = "2240";
/// That part's back-cape chain, root first: each the child of the one before, the first a child
/// of `Pelvis`.
pub const CHAIN: [&str; 3] = [
    "[cloth]BD_M_2240_Mant_002",
    "[cloth]BD_M_2240_Mant_003",
    "[cloth]BD_M_2240_Mant_017",
];
const TAIL_PREFIX: &str = "tail";
const PELVIS: &str = "Pelvis";

/// How stiff the tail is, at its base and at its tip, each from 0 (it swings wholly with the
/// cloth) to 1 (welded to the pelvis): the part of a tail bone's weight the pelvis keeps, the
/// rest going to the cloth chain. Between base and tip it eases with the square of the bone's
/// place along the tail, so the base stays stiff and the swing gathers toward the tip. Players set
/// it with `--rigidity tail=<base>:<tip>` in percent.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rigidity {
    pub base: f32,
    pub tip: f32,
}

impl Rigidity {
    /// 95% at the base, 55% at the tip. Seen in game 2026-10-15 (Priscilla): the whole tail on
    /// the chain swung like the light cape it came from and kinked where it changed cloth bone;
    /// 75% rising evenly to 40% was "quite a bit better" but a quarter too light and wanted much
    /// heavier at the base than the tip; this was accepted.
    pub const DEFAULT: Self = Self {
        base: 0.95,
        tip: 0.55,
    };

    /// `95:55`, base and tip in percent, each 0 to 100.
    pub fn parse(text: &str) -> Result<Self> {
        let percent = |part: &str| -> Result<f32> {
            let value: f32 = part.trim().parse().map_err(|_| {
                Error::new(format!("{part:?} is not a number of percent (0 to 100)"))
            })?;
            if !(0.0..=100.0).contains(&value) {
                return Err(Error::new(format!("{value} is not between 0 and 100")));
            }
            Ok(value / 100.0)
        };
        let (base, tip) = text.split_once(':').ok_or_else(|| {
            Error::new(format!(
                "{text:?} is not <base>:<tip>, percent stiff at each end (95:55)"
            ))
        })?;
        Ok(Self {
            base: percent(base)?,
            tip: percent(tip)?,
        })
    }

    /// Welded through and through: nothing for the cloth to swing, so the tail stays in the body.
    pub fn welded(&self) -> bool {
        self.base >= 1.0 && self.tip >= 1.0
    }

    /// The part of the weight the cloth chain takes at `t` along the tail, 0 at the base to 1 at
    /// the tip.
    fn chain_share(&self, t: f32) -> f32 {
        let (base, tip) = (1.0 - self.base, 1.0 - self.tip);
        base + (tip - base) * t * t
    }
}

impl Default for Rigidity {
    fn default() -> Self {
        Self::DEFAULT
    }
}

/// The source's tail bones, root first: every bone with a parent whose name starts with `tail`,
/// ordered by how many tail bones it hangs below. Priscilla's are `tail_00` to `tail_08`; her
/// `tailNub` has no parent in her FLVER (in her Havok skeleton it is `tail_08`'s child) and no
/// weight, so it is not one.
pub fn tail_bones(bones: &[Bone]) -> Vec<String> {
    let is_tail = |index: usize| bones[index].name.to_lowercase().starts_with(TAIL_PREFIX);
    let depth = |index: usize| {
        let mut depth = 0;
        let mut parent = bones[index].parent_index;
        let mut steps = 0;
        while let Ok(up) = usize::try_from(parent) {
            if up >= bones.len() || steps > bones.len() {
                break;
            }
            depth += usize::from(is_tail(up));
            parent = bones[up].parent_index;
            steps += 1;
        }
        depth
    };
    let mut tail: Vec<(usize, usize)> = (0..bones.len())
        .filter(|&index| is_tail(index) && bones[index].parent_index >= 0)
        .map(|index| (depth(index), index))
        .collect();
    tail.sort_unstable();
    tail.into_iter()
        .map(|(_, index)| bones[index].name.clone())
        .collect()
}

/// Where each of `tail` (root first) weighs. The `i`th of `n` sits at `t = i / (n - 1)` along the
/// tail: the chain takes what `rigidity` leaves it there, split between the two [`CHAIN`] bones
/// either side of `t` laid along the chain, and `Pelvis` takes the rest.
pub fn chain_targets(tail: &[String], rigidity: Rigidity) -> Vec<(String, Vec<(String, f32)>)> {
    let last = tail.len().saturating_sub(1).max(1) as f32;
    let links = (CHAIN.len() - 1) as f32;
    tail.iter()
        .enumerate()
        .map(|(i, bone)| {
            let t = i as f32 / last;
            let share = rigidity.chain_share(t);
            let along = t * links;
            let low = (along.floor() as usize).min(CHAIN.len() - 1);
            let frac = along - low as f32;
            let mut shares = vec![
                (PELVIS.to_owned(), 1.0 - share),
                (CHAIN[low].to_owned(), share * (1.0 - frac)),
            ];
            if frac > 0.0 && low + 1 < CHAIN.len() {
                shares.push((CHAIN[low + 1].to_owned(), share * frac));
            }
            (bone.clone(), shares)
        })
        .collect()
}

/// `export` cut into the body and the tail: a triangle with a corner weighing on a [`CHAIN`] bone
/// is the tail's, every other triangle the body's, and each keeps only the vertices it uses.
/// Refused when nothing weighs on the chain.
pub fn split(export: &Export) -> Result<(Export, Export)> {
    let mut on_chain = vec![false; export.mesh.vertices.len()];
    for row in &export.mesh.weights {
        if row.weight > 0.0 && CHAIN.contains(&row.target_bone.as_str()) {
            *on_chain.get_mut(row.vertex).ok_or_else(|| {
                Error::new(format!("a weight names missing vertex {}", row.vertex))
            })? = true;
        }
    }
    let tail_triangle = |triangle: &[u32; 3]| {
        triangle
            .iter()
            .any(|&corner| on_chain.get(corner as usize).copied().unwrap_or(false))
    };
    let tail = export.subset(tail_triangle)?;
    if tail.mesh.triangles.is_empty() {
        return Err(Error::new(format!(
            "no triangle of {} weighs on the cloth chain",
            export.character
        )));
    }
    let body = export.subset(|triangle| !tail_triangle(triangle))?;
    Ok((body, tail))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        fit::{MapRole, SourceMesh, SourceRow, SourceVertex, WeightRow},
        flver::Vec3,
    };

    fn bone(name: &str, parent: i16) -> Bone {
        Bone {
            name: name.to_owned(),
            parent_index: parent,
            translation: Vec3::default(),
            rotation: Vec3::default(),
            scale: Vec3 {
                x: 1.0,
                y: 1.0,
                z: 1.0,
            },
        }
    }

    #[test]
    fn the_tail_runs_root_first_and_shares_the_chain_out_in_order() {
        // Listed out of order, as a FLVER may: the nub, then the root, then the middle.
        let bones = [
            bone("Pelvis", -1),
            bone("tail_02", 3),
            bone("tail_03", 1),
            bone("tail_00", 0),
            bone("Spine", 0),
            // Unparented, as Priscilla's is: not part of the chain.
            bone("tailNub", -1),
        ];
        // tail_02 is the child of tail_00 here only through index 3.
        assert_eq!(tail_bones(&bones), ["tail_00", "tail_02", "tail_03"]);
        let nine: Vec<String> = (0..9).map(|i| format!("tail_0{i}")).collect();
        let rigidity = Rigidity::DEFAULT;
        let (root_share, tip_share) = (1.0 - rigidity.base, 1.0 - rigidity.tip);
        let targets = chain_targets(&nine, rigidity);
        let share_of = |shares: &[(String, f32)], bone: &str| {
            shares
                .iter()
                .find(|(name, _)| name == bone)
                .map_or(0.0, |(_, share)| *share)
        };
        let close = |a: f32, b: f32| (a - b).abs() < 1e-5;
        for (bone, shares) in &targets {
            let total: f32 = shares.iter().map(|(_, share)| share).sum();
            assert!(close(total, 1.0), "{bone} adds up to {total}");
        }
        // The root: mostly pelvis, the rest on the first cloth bone.
        assert!(close(share_of(&targets[0].1, "Pelvis"), rigidity.base));
        assert!(close(share_of(&targets[0].1, CHAIN[0]), root_share));
        // Half way along: the middle cloth bone alone, a quarter of the way from the root's share
        // to the tip's, since the share grows with the square.
        let middle = root_share + (tip_share - root_share) / 4.0;
        assert!(close(share_of(&targets[4].1, CHAIN[1]), middle));
        assert_eq!(targets[4].1.len(), 2);
        // A quarter along: split evenly between the first two.
        let quarter = root_share + (tip_share - root_share) / 16.0;
        assert!(close(share_of(&targets[2].1, CHAIN[0]), quarter / 2.0));
        assert!(close(share_of(&targets[2].1, CHAIN[1]), quarter / 2.0));
        // The tip: the last cloth bone takes the tip share.
        assert!(close(share_of(&targets[8].1, CHAIN[2]), tip_share));
        assert!(chain_targets(&[], rigidity).is_empty());
    }

    #[test]
    fn rigidity_reads_as_percent_stiff_at_base_and_tip() {
        assert_eq!(
            Rigidity::parse("95:55").unwrap(),
            Rigidity {
                base: 0.95,
                tip: 0.55
            }
        );
        assert_eq!(Rigidity::parse(" 100 : 0 ").unwrap().tip, 0.0);
        assert!(Rigidity::parse("100:100").unwrap().welded());
        assert!(!Rigidity::DEFAULT.welded());
        for bad in ["95", "95:", "x:55", "101:50", "50:-1"] {
            assert!(Rigidity::parse(bad).is_err(), "{bad}");
        }
        // Fully loose at the tip: the tip bone is wholly the cloth's.
        let loose = Rigidity {
            base: 1.0,
            tip: 0.0,
        };
        let targets = chain_targets(&["a".to_owned(), "b".to_owned()], loose);
        assert_eq!(targets[0].1[0], ("Pelvis".to_owned(), 1.0));
        assert_eq!(targets[1].1[1], (CHAIN[2].to_owned(), 1.0));
    }

    fn export(weights: &[(usize, &str)], triangles: Vec<[u32; 3]>) -> Export {
        let count = 1 + weights.iter().map(|(v, _)| *v).max().unwrap_or(0);
        Export {
            character: "c2730",
            mesh: SourceMesh {
                vertices: vec![SourceVertex::default(); count],
                triangles,
                weights: weights
                    .iter()
                    .map(|&(vertex, bone)| WeightRow {
                        vertex,
                        target_bone: bone.to_owned(),
                        weight: 1.0,
                    })
                    .collect(),
            },
            scale: 1.0,
            params: Vec::new(),
            stretch: Vec::new(),
            grip_anchor: None,
            source_map: (0..count)
                .map(|vertex| SourceRow {
                    mesh: 0,
                    vertex,
                    bone: String::new(),
                    role: MapRole::Other,
                })
                .collect(),
            strip: None,
        }
    }

    #[test]
    fn a_triangle_touching_the_chain_is_the_tail_and_the_seam_vertex_is_in_both() {
        // 0..3 body, 3 the seam (pelvis), 4 and 5 on the chain.
        let whole = export(
            &[
                (0, "Pelvis"),
                (1, "Pelvis"),
                (2, "Spine"),
                (3, "Pelvis"),
                (4, CHAIN[0]),
                (5, CHAIN[1]),
            ],
            vec![[0, 1, 3], [1, 2, 3], [3, 4, 5]],
        );
        let (body, tail) = split(&whole).unwrap();
        assert_eq!(body.mesh.triangles, [[0, 1, 3], [1, 2, 3]]);
        assert_eq!(body.mesh.vertices.len(), 4);
        assert_eq!(tail.mesh.triangles, [[0, 1, 2]]);
        let tail_bones: Vec<&str> = tail
            .mesh
            .weights
            .iter()
            .map(|row| row.target_bone.as_str())
            .collect();
        assert_eq!(tail_bones, ["Pelvis", CHAIN[0], CHAIN[1]]);
        let rows: Vec<usize> = tail.source_map.iter().map(|row| row.vertex).collect();
        assert_eq!(rows, [3, 4, 5], "source rows follow their vertices");
        // Nothing on the chain is no tail at all.
        let none = export(
            &[(0, "Pelvis"), (1, "Pelvis"), (2, "Pelvis")],
            vec![[0, 1, 2]],
        );
        assert!(split(&none).is_err());
    }
}
