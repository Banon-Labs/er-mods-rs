//! Dark Souls Remastered's c2280 as a source mesh for the Elden Ring donor: its first mesh's
//! LOD0 triangles, fitted to Elden Ring's bind skeleton and weighted to Elden Ring bone names.
//!
//! # The fit
//!
//! The game skins this mesh with the player's skeleton, so every vertex turns about the joint of
//! the Elden Ring bone it is weighted to, and it draws a held weapon at the human hand
//! (`R_Weapon`, `L_Weapon`), which nothing in this mesh can move. So each part of c2280 is laid
//! onto the human joints that turn it:
//!
//! - the body is stretched upright in three bands ([`BodyFit`]): the legs so c2280's pelvis lands
//!   on the human pelvis, the trunk so its shoulders land on the human shoulders, and the neck and
//!   cap above keep their own proportions, about 2.04 m to the top of the cap; the two leg trunks
//!   part by [`FitParams::foot_spread`] toward the ground;
//! - each arm roots in c2280's own shoulder socket, where the body fit put it, and reaches the
//!   human elbow and hand (`UpperArm`, `Forearm`, `Hand`), each segment stretched along its own
//!   length to the human one while its girth keeps the body's scale; the elbow leans
//!   [`FitParams::arm_lean`] out so the hanging forearm clears the wide body, and the body around
//!   the socket takes a fading share of the upper arm ([`FitParams::shoulder_blend`]);
//! - the palm spans the wrist to the human knuckles, so a weapon sits in it, and each of c2280's
//!   three digits is weighted to a human finger (thumb, middle, ring) as one piece from its
//!   knuckle: the game curls those fingers around the grip, and the digit closes with them;
//! - the legs go to the foot, calf or thigh by where the human ankle and knee fall.
//!
//! The arm stays joined to the body through c2280's own shoulder seam. Cutting it off and capping
//! both openings stretched fewer edges in a swing (464 in an R1 against about 560), and left a
//! visible line where the arm met the body; the user asked for the shoulder to blend in.
//!
//! Measured with `scripts/dsr-er-fit-probe.py`, which skins the result the way the game
//! does. The weapon bone stays within 0.03 m of the palm, the digit tips close from 0.10 m to
//! 0.06 m of it in the idle, the leg trunks come no closer than 0.079 m in the idle (0.031 m
//! unspread), and no triangle is inside out before animation or in the idle.
//!
//! [`SourceMesh::to_obj`] / [`SourceMesh::to_weights_tsv`] still emit the text the old
//! `scripts/route_a_mushroom_export.rs` wrote, for comparison and for the Blender authoring path.

use super::{
    Carried, Export, MapRole, Placement, SourceMesh, SourceRow, SourceVertex, UP, WEIGHT_FLOOR,
    WeightRow, add, arc_rotation, dominant_bone, dot, mul, normalized, place_all, rotate,
    smoothstep, sub,
};
use crate::{
    error::{Error, Result},
    flver::{
        Flver, Vec3, bind_positions,
        source::{self, DsrVertex},
    },
};

/// How c2280 is fitted to Elden Ring's body.
#[derive(Clone, Copy, Debug)]
pub struct FitParams {
    /// The mushroom's girth, and the height of its neck and cap: c2280 is a child, about 1.26 m
    /// to the top of its cap. The legs and trunk take their heights from the human skeleton.
    pub scale: f32,
    /// Metres the elbow leans up and out across Elden Ring's arm axis, fading to nothing at the
    /// shoulder socket and at the hand, so the hanging forearm clears the wide body while the arm
    /// stays in its socket and the hand on the weapon. At 0 the idle sinks 119 arm vertices into
    /// the body; 0.08 leaves 82.
    pub arm_lean: f32,
    /// Metres each leg trunk moves out at the ground, fading to nothing where c2280's leg bone
    /// starts. c2280's two feet nearly touch; stretched to human leg length they stood 0.031 m
    /// apart in the idle, and 0.08 opens that to 0.079 m.
    pub foot_spread: f32,
    /// Metres around each shoulder socket over which the body takes a share of the upper arm's
    /// turn, half at the socket and none at this distance, so lifting the arm stretches the whole
    /// shoulder a little instead of the seam a lot.
    pub shoulder_blend: f32,
}

impl Default for FitParams {
    fn default() -> Self {
        Self {
            scale: 1.225,
            arm_lean: 0.08,
            foot_spread: 0.08,
            shoulder_blend: 0.20,
        }
    }
}

impl FitParams {
    fn scaled(&self, v: Vec3) -> Vec3 {
        mul(v, self.scale)
    }
}

/// c2280's body stretched upright in three bands, so its pelvis and shoulders land at the human
/// pelvis and shoulder heights; across, and above the shoulders, it keeps [`FitParams::scale`].
/// Below the top of the legs each trunk also leans out by up to [`FitParams::foot_spread`].
#[derive(Clone, Copy, Debug)]
struct BodyFit {
    scale: f32,
    /// c2280's pelvis and shoulder heights, unscaled.
    hip: f32,
    shoulder: f32,
    /// Elden Ring's.
    er_hip: f32,
    er_shoulder: f32,
    /// Where c2280's leg bone starts, once fitted, and how far out the feet go below it.
    leg_top: f32,
    spread: f32,
}

/// Within this many metres of the middle, a vertex takes only part of the foot spread, so the
/// two trunks part smoothly instead of tearing along the middle.
const SPREAD_MIDDLE: f32 = 0.03;

impl BodyFit {
    /// c2280's `hip`, `shoulder` and `leg` (where its leg bone starts) heights, and Elden Ring's
    /// hip and shoulder heights.
    fn new(
        [hip, shoulder, leg]: [f32; 3],
        [er_hip, er_shoulder]: [f32; 2],
        params: FitParams,
    ) -> Result<Self> {
        if hip <= 0.0 || shoulder <= hip || er_shoulder <= er_hip || leg <= 0.0 || leg > hip {
            return Err(Error::new(format!(
                "leg, pelvis and shoulder heights out of order: c2280 {leg}, {hip}..{shoulder}, Elden Ring {er_hip}..{er_shoulder}"
            )));
        }
        Ok(Self {
            scale: params.scale,
            hip,
            shoulder,
            er_hip,
            er_shoulder,
            leg_top: leg * er_hip / hip,
            spread: params.foot_spread,
        })
    }

    fn place(&self, v: Vec3) -> Vec3 {
        let y = if v.y <= self.hip {
            v.y * self.er_hip / self.hip
        } else if v.y <= self.shoulder {
            self.er_hip
                + (v.y - self.hip) * (self.er_shoulder - self.er_hip) / (self.shoulder - self.hip)
        } else {
            self.er_shoulder + (v.y - self.shoulder) * self.scale
        };
        let mut x = v.x * self.scale;
        if y < self.leg_top {
            let side = (x / SPREAD_MIDDLE).clamp(-1.0, 1.0);
            x += side * self.spread * smoothstep((self.leg_top - y) / self.leg_top);
        }
        Vec3 {
            x,
            y,
            z: v.z * self.scale,
        }
    }

    /// The normal at `v` once [`Self::place`] has stretched it: scaling a surface by `s`
    /// across and `k` upright turns its normal by the inverse, `(x / s, y / k, z / s)`. The
    /// small sideways lean of the feet is left out.
    fn normal(&self, v: Vec3, n: Vec3) -> Vec3 {
        let upright = if v.y <= self.hip {
            self.er_hip / self.hip
        } else if v.y <= self.shoulder {
            (self.er_shoulder - self.er_hip) / (self.shoulder - self.hip)
        } else {
            self.scale
        };
        Vec3 {
            x: n.x / self.scale,
            y: n.y / upright,
            z: n.z / self.scale,
        }
    }
}

/// One piece of c2280's arm, from joint `from`, laid onto Elden Ring's arm from joint `onto`.
#[derive(Clone, Copy, Debug)]
struct Segment {
    /// c2280's joint, unscaled.
    from: Vec3,
    /// The Elden Ring joint it lands on.
    onto: Vec3,
    /// The piece's own direction after the global scale, along which it is stretched.
    axis: Vec3,
    stretch: f32,
    /// Turns `axis` onto Elden Ring's direction (a quaternion, x y z w).
    rotation: [f32; 4],
}

impl Segment {
    /// c2280's `from`..`to` laid onto Elden Ring's `onto`..`onto_to`, stretched to its length.
    fn spanning(
        from: Vec3,
        to: Vec3,
        onto: Vec3,
        onto_to: Vec3,
        params: FitParams,
    ) -> Result<Self> {
        let own = params.scaled(sub(to, from));
        let own_length = dot(own, own).sqrt();
        let human = sub(onto_to, onto);
        let axis = normalized(own).ok_or("c2280's arm segment has no length")?;
        let target = normalized(human).ok_or("Elden Ring's arm segment has no length")?;
        Ok(Self {
            from,
            onto,
            axis,
            stretch: dot(human, human).sqrt() / own_length,
            rotation: arc_rotation(axis, target),
        })
    }

    fn place(&self, v: Vec3, params: FitParams) -> Vec3 {
        let w = params.scaled(sub(v, self.from));
        let w = add(w, mul(self.axis, (self.stretch - 1.0) * dot(w, self.axis)));
        add(self.onto, rotate(self.rotation, w))
    }

    /// A normal carried by this piece: stretched by the inverse of the piece's stretch along
    /// its axis, then turned with it. Unnormalised; the uniform scale drops out on normalising.
    fn normal(&self, n: Vec3) -> Vec3 {
        let n = add(
            n,
            mul(self.axis, (1.0 / self.stretch - 1.0) * dot(n, self.axis)),
        );
        rotate(self.rotation, n)
    }
}

/// One arm laid joint on joint onto Elden Ring's.
#[derive(Clone, Copy, Debug)]
struct ArmFit {
    /// Upper arm, forearm, hand, then the three digits (thumb, middle, ring): each lies on, and is
    /// weighted to, the bone in `targets`.
    pieces: [Segment; 6],
    targets: [&'static str; 6],
    /// Elden Ring's shoulder joint and its unit axis toward the hand.
    shoulder: Vec3,
    axis: Vec3,
    /// Across the arm axis, up and out: the way [`FitParams::arm_lean`] pushes.
    out: Vec3,
    /// Where the upper arm roots: c2280's own shoulder, as the body fit placed it.
    socket: Vec3,
    /// How far along `axis` the socket, the elbow and the hand are.
    socket_at: f32,
    elbow_at: f32,
    hand_at: f32,
}

impl ArmFit {
    /// `own` is c2280's shoulder, elbow, wrist and the knuckles of its thumb, middle and ring
    /// digits (`XArmDigit12`, `22`, `32`); `human` is Elden Ring's `UpperArm`, `Forearm` and `Hand`
    /// joints and the middle and ring knuckles (`Finger2`, `Finger3`). `socket` is where the body
    /// fit put c2280's own shoulder: the upper arm roots there, so the arm stays joined to the
    /// body, and reaches the human elbow. `targets` names the bone each piece is weighted to.
    fn new(
        own: [Vec3; 6],
        human: [Vec3; 5],
        socket: Vec3,
        targets: [&'static str; 6],
        params: FitParams,
    ) -> Result<Self> {
        let [shoulder, elbow, wrist, thumb, middle, ring] = own;
        let [er_shoulder, er_elbow, er_hand, er_middle, er_ring] = human;
        let upper = Segment::spanning(shoulder, elbow, socket, er_elbow, params)?;
        let fore = Segment::spanning(elbow, wrist, er_elbow, er_hand, params)?;
        // The palm spans the wrist to the line between the middle and ring knuckles.
        let hand = Segment::spanning(
            wrist,
            mul(add(middle, ring), 0.5),
            er_hand,
            mul(add(er_middle, er_ring), 0.5),
            params,
        )?;
        // Each digit keeps its own size, hangs from where the palm put its knuckle, and turns
        // with the palm until the game curls its finger.
        let digit = |knuckle: Vec3| Segment {
            from: knuckle,
            onto: hand.place(knuckle, params),
            stretch: 1.0,
            ..hand
        };
        let axis = normalized(sub(er_hand, er_shoulder)).ok_or("Elden Ring's arm has no length")?;
        let out =
            normalized(sub(UP, mul(axis, dot(UP, axis)))).ok_or("Elden Ring's arm is vertical")?;
        Ok(Self {
            pieces: [upper, fore, hand, digit(thumb), digit(middle), digit(ring)],
            targets,
            shoulder: er_shoulder,
            axis,
            out,
            socket,
            socket_at: dot(sub(socket, er_shoulder), axis),
            elbow_at: dot(sub(er_elbow, er_shoulder), axis),
            hand_at: dot(sub(er_hand, er_shoulder), axis),
        })
    }

    /// Where `v`, carried by piece `piece` of c2280's arm, lands.
    fn place(&self, piece: usize, v: Vec3, params: FitParams) -> Vec3 {
        let at = self.pieces[piece].place(v, params);
        if piece >= 3 {
            return at;
        }
        let along = dot(sub(at, self.shoulder), self.axis);
        let lean = smoothstep((self.hand_at - along) / (self.hand_at - self.elbow_at))
            * smoothstep((along - self.socket_at) / (self.elbow_at - self.socket_at));
        add(at, mul(self.out, params.arm_lean * lean))
    }
}

/// Which piece of which arm a c2280 bone moves: `XArm1` the upper arm, `XArm2` the forearm,
/// `XArmPalm` the hand, `XArmDigit12`, `22` and `32` the thumb, middle and ring digits. The
/// digits' first bones (`11`, `21`, `31`) carry palm, so they go with the hand.
fn arm_piece<'a>(name: &str, left: &'a ArmFit, right: &'a ArmFit) -> Option<(&'a ArmFit, usize)> {
    let (arm, rest) = if let Some(rest) = name.strip_prefix("LArm") {
        (left, rest)
    } else {
        (right, name.strip_prefix("RArm")?)
    };
    let piece = match rest {
        "1" => 0,
        "2" => 1,
        "Digit12" => 3,
        "Digit22" => 4,
        "Digit32" => 5,
        _ if rest.starts_with("Palm") || rest.starts_with("Digit") => 2,
        _ => return None,
    };
    Some((arm, piece))
}

struct DsrMesh {
    bone_names: Vec<String>,
    mesh_bone_indices: Vec<u32>,
    vertices: Vec<DsrVertex>,
    triangles: Vec<[u32; 3]>,
}

/// c2280's body and arm fits, each bone of its mesh carrying a vertex by one of them.
struct MushroomPlacement<'a> {
    mesh: &'a DsrMesh,
    body_fit: BodyFit,
    left: ArmFit,
    right: ArmFit,
    knee: f32,
    ankle: f32,
    params: FitParams,
}

impl MushroomPlacement<'_> {
    fn piece_of(&self, slot: usize, vertex: &DsrVertex) -> Option<(&ArmFit, usize)> {
        source_bone_name(self.mesh, vertex.bone_indices[slot])
            .and_then(|name| arm_piece(name, &self.left, &self.right))
    }
}

impl Placement for MushroomPlacement<'_> {
    fn rest(&self, vertex: &DsrVertex) -> (Vec3, Vec3) {
        (
            self.body_fit.place(vertex.position),
            self.body_fit.normal(vertex.position, vertex.normal),
        )
    }

    fn carry(
        &self,
        vertex: &DsrVertex,
        slot: usize,
        (body, body_normal): (Vec3, Vec3),
    ) -> Result<Carried> {
        let (at, normal, target) = match self.piece_of(slot, vertex) {
            Some((arm, piece)) => (
                arm.place(piece, vertex.position, self.params),
                arm.pieces[piece].normal(vertex.normal),
                arm.targets[piece],
            ),
            None => (
                body,
                body_normal,
                source_bone_name(self.mesh, vertex.bone_indices[slot])
                    .map_or("<missing>", |name| {
                        body_target(name, body.y, self.knee, self.ankle)
                    }),
            ),
        };
        Ok(Carried {
            at,
            normal,
            targets: vec![(target.to_owned(), 1.0)],
        })
    }
}

/// c2280's FLVER bytes, fitted to the bind skeleton of `donor` (Elden Ring's `FC_M_0000.flver`).
pub fn export_c2280(flver_bytes: &[u8], donor: &[u8], params: FitParams) -> Result<SourceMesh> {
    let mesh = parse_first_lod0_mesh(flver_bytes)?;
    let joint = joint_finder(flver_bytes, "c2280")?;
    let er_joint = joint_finder(donor, "the Elden Ring donor")?;
    let height = |name: &str, find: &dyn Fn(&str) -> Result<Vec3>| find(name).map(|p| p.y);
    let body_fit = BodyFit::new(
        [
            height("Pelvis", &joint)?,
            height("LArm1", &joint)?,
            height("LLeg1", &joint)?,
        ],
        [
            height("Pelvis", &er_joint)?,
            height("L_UpperArm", &er_joint)?,
        ],
        params,
    )?;
    let (knee, ankle) = (height("L_Calf", &er_joint)?, height("L_Foot", &er_joint)?);
    let arm = |side: &str| -> Result<ArmFit> {
        let own = [
            "Arm1",
            "Arm2",
            "ArmPalm",
            "ArmDigit12",
            "ArmDigit22",
            "ArmDigit32",
        ]
        .map(|bone| joint(&format!("{side}{bone}")));
        let human = ["UpperArm", "Forearm", "Hand", "Finger2", "Finger3"]
            .map(|bone| er_joint(&format!("{side}_{bone}")));
        let targets = if side == "L" {
            [
                "L_UpperArm",
                "L_Forearm",
                "L_Hand",
                "L_Finger01",
                "L_Finger2",
                "L_Finger3",
            ]
        } else {
            [
                "R_UpperArm",
                "R_Forearm",
                "R_Hand",
                "R_Finger01",
                "R_Finger2",
                "R_Finger3",
            ]
        };
        let [a, b, c, d, e, f] = own;
        let [g, h, i, j, k] = human;
        let a = a?;
        ArmFit::new(
            [a, b?, c?, d?, e?, f?],
            [g?, h?, i?, j?, k?],
            body_fit.place(a),
            targets,
            params,
        )
    };
    let fit = MushroomPlacement {
        mesh: &mesh,
        body_fit,
        left: arm("L")?,
        right: arm("R")?,
        knee,
        ankle,
        params,
    };

    // A vertex belongs to the body, and blends with the shoulder, when the arm's bones hold less
    // than half its weight.
    let on_arm: Vec<bool> = mesh
        .vertices
        .iter()
        .map(|vertex| {
            let (mut arm_weight, mut total) = (0.0, 0.0);
            for slot in 0..4 {
                let weight = vertex.bone_weights[slot];
                if weight > WEIGHT_FLOOR {
                    total += weight;
                    if fit.piece_of(slot, vertex).is_some() {
                        arm_weight += weight;
                    }
                }
            }
            total > 0.0 && arm_weight >= 0.5 * total
        })
        .collect();

    let (vertices, mut weights) = place_all(&fit, &mesh.vertices)?;
    blend_shoulders(
        &mut weights,
        &vertices,
        &on_arm,
        [
            (fit.left.socket, "L_UpperArm"),
            (fit.right.socket, "R_UpperArm"),
        ],
        params.shoulder_blend,
    );
    Ok(SourceMesh {
        vertices,
        triangles: mesh.triangles,
        weights,
    })
}

/// [`export_c2280`] with what the fit probe reads beside it. The export is the source's whole
/// first buffer in order, so export vertex `i` is mesh 0's vertex `i`.
pub fn export(id: &'static str, flver: &[u8], donor: &[u8], params: FitParams) -> Result<Export> {
    let mesh = export_c2280(flver, donor, params)?;
    let source = source::parse(flver)?;
    let first = source.meshes.first().ok_or("FLVER has no meshes")?;
    if first.vertices.len() != mesh.vertices.len() {
        return Err(Error::new(format!(
            "the export has {} vertices, {id}'s first mesh {}",
            mesh.vertices.len(),
            first.vertices.len()
        )));
    }
    let source_map = first
        .vertices
        .iter()
        .enumerate()
        .map(|(index, vertex)| {
            let bone = dominant_bone(
                vertex.bone_indices.map(|slot| first.bone(slot)),
                vertex.bone_weights,
            );
            let name = source.bones.get(bone).map_or("", |bone| bone.name.as_str());
            SourceRow {
                mesh: 0,
                vertex: index,
                bone: name.to_owned(),
                role: source_role(name),
            }
        })
        .collect();
    let number = |value: f32| value.to_string();
    Ok(Export {
        character: id,
        mesh,
        scale: params.scale,
        params: vec![
            ("scale", number(params.scale)),
            ("arm_lean", number(params.arm_lean)),
            ("foot_spread", number(params.foot_spread)),
            ("shoulder_blend", number(params.shoulder_blend)),
        ],
        stretch: Vec::new(),
        grip_anchor: None,
        source_map,
        strip: None,
    })
}

/// What a c2280 bone is, for the source map: `XArmDigitN2` is a digit's last segment.
fn source_role(name: &str) -> MapRole {
    if let Some(rest) = name
        .strip_prefix("LArm")
        .or_else(|| name.strip_prefix("RArm"))
    {
        return match rest {
            "1" | "2" => MapRole::Arm,
            "Palm" => MapRole::Hand,
            _ if rest.starts_with("Digit") && rest.ends_with('2') => MapRole::FingerTip,
            _ if rest.starts_with("Digit") => MapRole::Finger,
            _ => MapRole::Other,
        };
    }
    match name {
        "LLeg1" | "RLeg1" => MapRole::Leg,
        "Pelvis" | "Neck" | "Head" => MapRole::Trunk,
        _ if name.starts_with("Spine") => MapRole::Trunk,
        _ => MapRole::Other,
    }
}

/// Give every body vertex near a shoulder socket a share of that side's upper arm: half at the
/// socket, fading to none `reach` metres out, its own weights scaled to make room. The arm roots
/// in the socket and turns about the human shoulder 0.07 m further in, so without this the few
/// triangles of the seam take the whole stretch when the arm lifts.
fn blend_shoulders(
    weights: &mut Vec<WeightRow>,
    vertices: &[SourceVertex],
    on_arm: &[bool],
    sockets: [(Vec3, &'static str); 2],
    reach: f32,
) {
    if reach <= 0.0 {
        return;
    }
    let mut totals = vec![0.0; vertices.len()];
    for row in weights.iter() {
        totals[row.vertex] += row.weight;
    }
    let shares: Vec<f32> = vertices
        .iter()
        .enumerate()
        .map(|(index, vertex)| {
            if on_arm[index] {
                return 0.0;
            }
            let (socket, _) = sockets[usize::from(vertex.position.x <= 0.0)];
            let d = sub(vertex.position, socket);
            0.5 * smoothstep(1.0 - dot(d, d).sqrt() / reach)
        })
        .collect();
    for row in weights.iter_mut() {
        let share = shares[row.vertex];
        if share > WEIGHT_FLOOR && totals[row.vertex] > 0.0 {
            row.weight *= (1.0 - share) / totals[row.vertex];
        }
    }
    for (index, &share) in shares.iter().enumerate() {
        if share > WEIGHT_FLOOR {
            let (_, bone) = sockets[usize::from(vertices[index].position.x <= 0.0)];
            weights.push(WeightRow {
                vertex: index,
                target_bone: bone.to_owned(),
                weight: share,
            });
        }
    }
}

/// A lookup of one FLVER's bind-pose joints by bone name.
fn joint_finder(bytes: &[u8], what: &str) -> Result<impl Fn(&str) -> Result<Vec3>> {
    let bones = Flver::parse(bytes)?.bones;
    let positions = bind_positions(&bones)?;
    let what = what.to_owned();
    Ok(move |name: &str| {
        bones
            .iter()
            .position(|bone| bone.name == name)
            .map(|index| positions[index])
            .ok_or_else(|| Error::new(format!("{what} has no bone named {name}")))
    })
}

fn source_bone_name(mesh: &DsrMesh, mesh_bone_slot: u16) -> Option<&str> {
    let slot = usize::from(mesh_bone_slot);
    let global = mesh
        .mesh_bone_indices
        .get(slot)
        .map_or(slot, |&index| index as usize);
    mesh.bone_names.get(global).map(String::as_str)
}

/// The first mesh with its whole first buffer -- LOD1/LOD2-only vertices included, as c2280's
/// export has always carried them -- and its LOD0 triangles.
fn parse_first_lod0_mesh(bytes: &[u8]) -> Result<DsrMesh> {
    let source = source::parse(bytes)?;
    let bone_names = source.bones.iter().map(|bone| bone.name.clone()).collect();
    let mesh = source
        .meshes
        .into_iter()
        .next()
        .ok_or("FLVER has no meshes")?;
    let [triangles, ..] = mesh.lods;
    if triangles.is_empty() {
        return Err("no LOD0 face set (flags == 0) found".into());
    }
    Ok(DsrMesh {
        bone_names,
        mesh_bone_indices: mesh.palette,
        vertices: mesh.vertices,
        triangles,
    })
}

/// The Elden Ring bone for a weight on one of c2280's body or leg bones (the arms are
/// [`ArmFit`]'s). c2280 has one bone per leg, so its weight goes to the foot below the human
/// `ankle`, the calf below the `knee`, and the thigh above, by the fitted height `y`.
fn body_target(source: &str, y: f32, knee: f32, ankle: f32) -> &'static str {
    let leg = |side: &'static [&'static str; 3]| {
        if y < ankle {
            side[0]
        } else if y < knee {
            side[1]
        } else {
            side[2]
        }
    };
    match source {
        "Pelvis" => "Pelvis",
        "Spine1" => "Spine",
        "Spine2" => "Spine1",
        "Spine3" => "Spine2",
        "Neck" => "Neck",
        "Head" => "Head",
        "LLeg1" => leg(&["L_Foot", "L_Calf", "L_Thigh"]),
        "RLeg1" => leg(&["R_Foot", "R_Calf", "R_Thigh"]),
        "c2280" | "Model_Dmy" | "sfx_dummy" | "固定dmy" | "master" => "<unused>",
        _ => "Spine2",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(x: f32, y: f32, z: f32) -> Vec3 {
        Vec3 { x, y, z }
    }

    fn close(a: Vec3, b: Vec3) -> bool {
        let d = sub(a, b);
        dot(d, d).sqrt() < 1e-5
    }

    #[test]
    fn the_leg_bone_splits_into_foot_calf_and_thigh_at_the_human_ankle_and_knee() {
        let (knee, ankle) = (0.527, 0.11);
        assert_eq!(body_target("LLeg1", 0.05, knee, ankle), "L_Foot");
        assert_eq!(body_target("RLeg1", 0.3, knee, ankle), "R_Calf");
        assert_eq!(body_target("LLeg1", 0.6, knee, ankle), "L_Thigh");
        assert_eq!(body_target("Spine3", 1.0, knee, ankle), "Spine2");
    }

    #[test]
    fn the_body_lands_its_pelvis_and_shoulders_on_the_human_ones() {
        let params = FitParams::default();
        let body = BodyFit::new([0.381, 0.731, 0.308], [0.97, 1.391], params).unwrap();
        assert!(close(body.place(v(0.0, 0.0, 0.0)), v(0.0, 0.0, 0.0)));
        assert!((body.place(v(0.1, 0.381, 0.0)).y - 0.97).abs() < 1e-5);
        assert!((body.place(v(0.1, 0.731, 0.0)).y - 1.391).abs() < 1e-5);
        // Above the legs it keeps the global scale across, and above the shoulders upright too.
        let (a, b) = (v(0.2, 0.9, -0.1), v(-0.3, 1.2, 0.2));
        let placed = sub(body.place(a), body.place(b));
        assert!(close(placed, params.scaled(sub(a, b))));
        // Each foot stands `foot_spread` further out at the ground; the middle does not tear.
        let foot = body.place(v(0.1, 0.0, 0.0));
        assert!((foot.x - (0.1 * params.scale + params.foot_spread)).abs() < 1e-5);
        assert!(body.place(v(-0.1, 0.0, 0.0)).x < -0.1 * params.scale - 0.07);
        assert!(body.place(v(0.0, 0.0, 0.0)).x.abs() < 1e-6);
        assert!(BodyFit::new([0.7, 0.3, 0.2], [0.97, 1.391], params).is_err());
    }

    fn distance(a: Vec3, b: Vec3) -> f32 {
        let d = sub(a, b);
        dot(d, d).sqrt()
    }

    // c2280's left arm (shoulder, elbow, wrist, thumb, middle and ring knuckles) and Elden Ring's
    // (shoulder, elbow, hand, middle and ring knuckles), as their bind skeletons place them.
    const OWN: [[f32; 3]; 6] = [
        [0.201, 0.731, 0.0],
        [0.344, 0.667, 0.007],
        [0.453, 0.624, -0.002],
        [0.519, 0.569, -0.059],
        [0.562, 0.579, -0.008],
        [0.547, 0.573, 0.034],
    ];
    const HUMAN: [[f32; 3]; 5] = [
        [0.172, 1.391, 0.034],
        [0.371, 1.192, 0.025],
        [0.534, 1.029, 0.016],
        [0.609, 0.961, 0.005],
        [0.605, 0.962, 0.029],
    ];

    fn at(p: [f32; 3]) -> Vec3 {
        v(p[0], p[1], p[2])
    }

    fn left_arm(params: FitParams) -> ArmFit {
        let targets = [
            "L_UpperArm",
            "L_Forearm",
            "L_Hand",
            "L_Finger01",
            "L_Finger2",
            "L_Finger3",
        ];
        ArmFit::new(OWN.map(at), HUMAN.map(at), SOCKET, targets, params).unwrap()
    }

    /// Where the body fit puts c2280's left shoulder.
    const SOCKET: Vec3 = Vec3 {
        x: 0.246,
        y: 1.391,
        z: 0.0,
    };

    #[test]
    fn each_arm_joint_lands_on_its_human_joint_and_the_lean_spares_the_hand() {
        let params = FitParams::default();
        let arm = left_arm(params);
        let [shoulder, elbow, wrist, _, middle, ring] = OWN.map(at);
        let [_, er_elbow, er_hand, er_middle, er_ring] = HUMAN.map(at);
        let lean = mul(arm.out, params.arm_lean);

        // The palm reaches from the human wrist to the human knuckle line.
        let knuckles = mul(add(middle, ring), 0.5);
        let er_knuckles = mul(add(er_middle, er_ring), 0.5);
        assert!(close(arm.place(2, knuckles, params), er_knuckles));

        // The shoulder stays in its socket, the elbow leans out by the full amount, and the
        // wrist sits on the human hand.
        assert!(close(arm.place(0, shoulder, params), SOCKET));
        assert!(close(arm.place(0, elbow, params), add(er_elbow, lean)));
        assert!(close(arm.place(1, elbow, params), add(er_elbow, lean)));
        assert!(close(arm.place(1, wrist, params), er_hand));
        assert!(close(arm.place(2, wrist, params), er_hand));
        assert!(arm.out.x > 0.0 && arm.out.y > 0.0);
    }

    #[test]
    fn a_digit_hangs_from_where_the_palm_put_its_knuckle_at_its_own_size() {
        let params = FitParams::default();
        let arm = left_arm(params);
        let [_, _, _, thumb, middle, ring] = OWN.map(at);
        for (piece, knuckle) in [(3, thumb), (4, middle), (5, ring)] {
            assert!(close(
                arm.place(piece, knuckle, params),
                arm.place(2, knuckle, params)
            ));
            let tip = add(knuckle, v(0.04, -0.02, 0.01));
            let own = distance(params.scaled(knuckle), params.scaled(tip));
            let placed = distance(
                arm.place(piece, knuckle, params),
                arm.place(piece, tip, params),
            );
            assert!((own - placed).abs() < 1e-5);
        }
    }

    #[test]
    fn bone_names_pick_the_arm_piece_they_move() {
        let arm = left_arm(FitParams::default());
        let piece = |name| arm_piece(name, &arm, &arm).map(|(_, piece)| piece);
        assert_eq!(piece("LArm1"), Some(0));
        assert_eq!(piece("RArm2"), Some(1));
        assert_eq!(piece("LArmPalm"), Some(2));
        assert_eq!(piece("RArmDigit21"), Some(2));
        assert_eq!(piece("LArmDigit12"), Some(3));
        assert_eq!(piece("RArmDigit22"), Some(4));
        assert_eq!(piece("RArmDigit32"), Some(5));
        assert_eq!(piece("LLeg1"), None);
        assert_eq!(piece("Spine3"), None);
    }

    #[test]
    fn c2280_bones_take_the_probe_roles_its_tips_were_measured_by() {
        assert_eq!(source_role("LArmDigit12"), MapRole::FingerTip);
        assert_eq!(source_role("RArmDigit32"), MapRole::FingerTip);
        assert_eq!(source_role("LArmDigit21"), MapRole::Finger);
        assert_eq!(source_role("RArmPalm"), MapRole::Hand);
        assert_eq!(source_role("LArm2"), MapRole::Arm);
        assert_eq!(source_role("RLeg1"), MapRole::Leg);
        assert_eq!(source_role("Spine3"), MapRole::Trunk);
        assert_eq!(source_role("Head"), MapRole::Trunk);
        assert_eq!(source_role("master"), MapRole::Other);
    }

    #[test]
    fn the_body_near_a_socket_takes_a_fading_share_of_that_upper_arm() {
        let socket = v(0.25, 1.39, 0.0);
        let at = |position: Vec3| SourceVertex {
            position,
            ..SourceVertex::default()
        };
        // At the left socket, halfway out, past the reach, an arm vertex, and the right side.
        let vertices = [
            at(socket),
            at(add(socket, v(0.0, -0.1, 0.0))),
            at(add(socket, v(0.0, -0.3, 0.0))),
            at(add(socket, v(0.05, 0.0, 0.0))),
            at(v(-0.25, 1.39, 0.0)),
        ];
        let on_arm = [false, false, false, true, false];
        let mut weights: Vec<WeightRow> = (0..vertices.len())
            .map(|vertex| WeightRow {
                vertex,
                target_bone: "Spine2".to_owned(),
                weight: 2.0,
            })
            .collect();
        let sockets = [(socket, "L_UpperArm"), (v(-0.25, 1.39, 0.0), "R_UpperArm")];
        blend_shoulders(&mut weights, &vertices, &on_arm, sockets, 0.2);
        let share = |vertex: usize, bone: &str| -> f32 {
            let total: f32 = weights
                .iter()
                .filter(|r| r.vertex == vertex)
                .map(|r| r.weight)
                .sum();
            weights
                .iter()
                .filter(|r| r.vertex == vertex && r.target_bone == bone)
                .map(|r| r.weight)
                .sum::<f32>()
                / total
        };
        assert!((share(0, "L_UpperArm") - 0.5).abs() < 1e-5);
        assert!((share(1, "L_UpperArm") - 0.25).abs() < 1e-5);
        assert_eq!(share(2, "L_UpperArm"), 0.0);
        assert_eq!(share(3, "L_UpperArm"), 0.0);
        assert!((share(4, "R_UpperArm") - 0.5).abs() < 1e-5);
    }
}
