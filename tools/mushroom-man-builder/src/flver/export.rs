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
//!   cap above keep their own proportions, about 2.04 m to the top of the cap;
//! - c2280's shoulder, elbow and wrist land on `UpperArm`, `Forearm` and `Hand`, each segment
//!   stretched along its own length to the human one while its girth keeps the body's scale, and
//!   the arm leans [`FitParams::arm_lean`] out from the shoulder to the elbow and comes back in
//!   along the forearm, so the hanging forearm clears the wide body and the hand stays put;
//! - the palm spans the wrist to the human knuckles, so a weapon sits in it, and each of c2280's
//!   three digits is weighted to a human finger (thumb, middle, ring) as one piece from its
//!   knuckle: the game curls those fingers around the grip, and the digit closes with them;
//! - the legs go to the foot, calf or thigh by where the human ankle and knee fall.
//!
//! The triangles that joined each arm to c2280's shoulder are dropped: each vertex keeps only its
//! own side's weights (arm or body, whichever holds most of it), and each opening is capped from a
//! new vertex at the middle of its rim. Left joined, the seam folds as the arm lifts.
//!
//! Measured with `scripts/mushroom-arm-fit-probe.py`, which skins the result the way the game
//! does, as the worst frame's edges stretched or squashed past 1.5x against the fitted shape: 108
//! in the idle loop, 452 in an R1 and 448 in a running R1, against 150, 492 and 512 with the
//! body under one scale and 244, 636 and the same legs with the stubby arms before that. The
//! weapon bone stays within 0.03 m of the palm, the digit tips close from 0.10 m to 0.06 m of it
//! in the idle, and no triangle is inside out before animation or in the idle.
//!
//! [`SourceMesh::to_obj`] / [`SourceMesh::to_weights_tsv`] still emit the text the old
//! `scripts/route_a_mushroom_export.rs` wrote, for comparison and for the Blender authoring path.

use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write as _,
};

use super::{
    Flver, Vec2, Vec3, bind_positions, parse_face_set_indices, parse_layout_members,
    parse_u32_list, read_vec3,
};
use crate::{
    bytes::{i8_at, i16_le, u8_at, u16_le},
    error::{Error, Result},
};

const PRIMITIVE_RESTART_U16: u32 = 0xFFFF;
/// Weights at or below this are noise in the source skin and are dropped.
const WEIGHT_FLOOR: f32 = 0.0001;
const UP: Vec3 = Vec3 {
    x: 0.0,
    y: 1.0,
    z: 0.0,
};

/// How c2280 is fitted to Elden Ring's body.
#[derive(Clone, Copy, Debug)]
pub struct FitParams {
    /// The mushroom's girth, and the height of its neck and cap: c2280 is a child, about 1.26 m
    /// to the top of its cap. The legs and trunk take their heights from the human skeleton.
    pub scale: f32,
    /// Metres the upper arm and elbow lean up and out across Elden Ring's arm axis, fading to
    /// nothing at the hand. At 0 the idle sinks 16 of each forearm's 39 vertices into the body;
    /// 0.08 leaves 4, and the hand does not move either way.
    pub arm_lean: f32,
}

impl Default for FitParams {
    fn default() -> Self {
        Self {
            scale: 1.225,
            arm_lean: 0.08,
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
#[derive(Clone, Copy, Debug)]
struct BodyFit {
    scale: f32,
    /// c2280's pelvis and shoulder heights, unscaled.
    hip: f32,
    shoulder: f32,
    /// Elden Ring's.
    er_hip: f32,
    er_shoulder: f32,
}

impl BodyFit {
    fn new(
        hip: f32,
        shoulder: f32,
        er_hip: f32,
        er_shoulder: f32,
        params: FitParams,
    ) -> Result<Self> {
        if hip <= 0.0 || shoulder <= hip || er_shoulder <= er_hip {
            return Err(Error::new(format!(
                "pelvis and shoulder heights out of order: c2280 {hip}..{shoulder}, Elden Ring {er_hip}..{er_shoulder}"
            )));
        }
        Ok(Self {
            scale: params.scale,
            hip,
            shoulder,
            er_hip,
            er_shoulder,
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
        Vec3 {
            x: v.x * self.scale,
            y,
            z: v.z * self.scale,
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
    /// How far along `axis` the elbow and the hand are.
    elbow_at: f32,
    hand_at: f32,
}

impl ArmFit {
    /// `own` is c2280's shoulder, elbow, wrist and the knuckles of its thumb, middle and ring
    /// digits (`XArmDigit12`, `22`, `32`); `human` is Elden Ring's `UpperArm`, `Forearm` and `Hand`
    /// joints and the middle and ring knuckles (`Finger2`, `Finger3`). `targets` names the bone
    /// each piece is weighted to.
    fn new(
        own: [Vec3; 6],
        human: [Vec3; 5],
        targets: [&'static str; 6],
        params: FitParams,
    ) -> Result<Self> {
        let [shoulder, elbow, wrist, thumb, middle, ring] = own;
        let [er_shoulder, er_elbow, er_hand, er_middle, er_ring] = human;
        let upper = Segment::spanning(shoulder, elbow, er_shoulder, er_elbow, params)?;
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
        let lean = smoothstep((self.hand_at - along) / (self.hand_at - self.elbow_at));
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

/// 0 at or below 0, 1 at or above 1, and a smooth ramp between.
fn smoothstep(x: f32) -> f32 {
    let t = x.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SourceVertex {
    pub position: Vec3,
    pub normal: Vec3,
    pub uv: Vec2,
}

/// One weight of one vertex, against an Elden Ring bone name.
#[derive(Clone, Debug, PartialEq)]
pub struct WeightRow {
    pub vertex: usize,
    pub target_bone: String,
    pub weight: f32,
}

/// What the donor patcher consumes: final positions, triangles and Elden Ring bone weights.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SourceMesh {
    pub vertices: Vec<SourceVertex>,
    pub triangles: Vec<[u32; 3]>,
    pub weights: Vec<WeightRow>,
}

#[derive(Clone, Copy, Debug, Default)]
struct DsrVertex {
    position: Vec3,
    normal: Vec3,
    uv: Vec2,
    bone_indices: [u16; 4],
    bone_weights: [f32; 4],
}

struct DsrMesh {
    bone_names: Vec<String>,
    mesh_bone_indices: Vec<u32>,
    vertices: Vec<DsrVertex>,
    triangles: Vec<[u32; 3]>,
}

/// c2280's FLVER bytes, fitted to the bind skeleton of `donor` (Elden Ring's `FC_M_0000.flver`).
pub fn export_c2280(flver_bytes: &[u8], donor: &[u8], params: FitParams) -> Result<SourceMesh> {
    let mesh = parse_first_lod0_mesh(flver_bytes)?;
    let joint = joint_finder(flver_bytes, "c2280")?;
    let er_joint = joint_finder(donor, "the Elden Ring donor")?;
    let height = |name: &str, find: &dyn Fn(&str) -> Result<Vec3>| find(name).map(|p| p.y);
    let body_fit = BodyFit::new(
        height("Pelvis", &joint)?,
        height("LArm1", &joint)?,
        height("Pelvis", &er_joint)?,
        height("L_UpperArm", &er_joint)?,
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
        ArmFit::new(
            [a?, b?, c?, d?, e?, f?],
            [g?, h?, i?, j?, k?],
            targets,
            params,
        )
    };
    let (left, right) = (arm("L")?, arm("R")?);
    let piece_of = |slot: usize, vertex: &DsrVertex| {
        source_bone_name(&mesh, vertex.bone_indices[slot])
            .and_then(|name| arm_piece(name, &left, &right))
    };

    // A vertex belongs to the arm when the arm's bones hold most of its weight.
    let on_arm: Vec<bool> = mesh
        .vertices
        .iter()
        .map(|vertex| {
            let (mut arm_weight, mut total) = (0.0, 0.0);
            for slot in 0..4 {
                let weight = vertex.bone_weights[slot];
                if weight > WEIGHT_FLOOR {
                    total += weight;
                    if piece_of(slot, vertex).is_some() {
                        arm_weight += weight;
                    }
                }
            }
            total > 0.0 && arm_weight >= 0.5 * total
        })
        .collect();

    let mut vertices = Vec::with_capacity(mesh.vertices.len());
    let mut weights = Vec::new();
    for (vertex_index, vertex) in mesh.vertices.iter().enumerate() {
        let body = body_fit.place(vertex.position);
        let mut placed = Vec3::default();
        let mut total = 0.0;
        for slot in 0..4 {
            let weight = vertex.bone_weights[slot];
            let piece = piece_of(slot, vertex);
            // Only the weights on the vertex's own side of the cut shoulder seam count.
            if weight <= WEIGHT_FLOOR || piece.is_some() != on_arm[vertex_index] {
                continue;
            }
            let (at, target) = match piece {
                Some((arm, piece)) => (
                    arm.place(piece, vertex.position, params),
                    arm.targets[piece],
                ),
                None => (
                    body,
                    source_bone_name(&mesh, vertex.bone_indices[slot])
                        .map_or("<missing>", |name| body_target(name, body.y, knee, ankle)),
                ),
            };
            placed = add(placed, mul(at, weight));
            total += weight;
            weights.push(WeightRow {
                vertex: vertex_index,
                target_bone: target.to_owned(),
                weight,
            });
        }
        vertices.push(SourceVertex {
            position: if total > 0.0 {
                mul(placed, 1.0 / total)
            } else {
                body
            },
            normal: vertex.normal,
            uv: vertex.uv,
        });
    }

    let (mut triangles, seam): (Vec<[u32; 3]>, Vec<[u32; 3]>) =
        mesh.triangles.iter().partition(|t| {
            t.iter()
                .all(|&i| on_arm[i as usize] == on_arm[t[0] as usize])
        });
    // c2280 splits a vertex wherever its texture does, so the openings are traced over positions.
    let mut spots = BTreeMap::new();
    let spot: Vec<u32> = mesh
        .vertices
        .iter()
        .map(|vertex| {
            let p = vertex.position;
            let next = u32::try_from(spots.len()).unwrap_or(u32::MAX);
            *spots
                .entry([p.x.to_bits(), p.y.to_bits(), p.z.to_bits()])
                .or_insert(next)
        })
        .collect();
    let cut: BTreeSet<u32> = seam.iter().flatten().map(|&i| spot[i as usize]).collect();
    // Each opening is capped from a new vertex at the middle of its rim, which draws no diagonal
    // across the rim: a fan from one rim vertex doubled two edges at each arm root.
    for rim in openings(&triangles, &cut, &spot) {
        let middle = u32::try_from(vertices.len()).map_err(|_| "too many vertices")?;
        let mut seen = BTreeSet::new();
        let corners: Vec<usize> = rim
            .iter()
            .map(|&(a, _)| a as usize)
            .filter(|&a| seen.insert(spot[a]))
            .collect();
        let share = 1.0 / corners.len() as f32;
        let mut cap = SourceVertex::default();
        let mut bones: BTreeMap<&str, f32> = BTreeMap::new();
        for &corner in &corners {
            let vertex = vertices[corner];
            cap.position = add(cap.position, mul(vertex.position, share));
            cap.normal = add(cap.normal, vertex.normal);
            cap.uv = Vec2 {
                x: cap.uv.x + vertex.uv.x * share,
                y: cap.uv.y + vertex.uv.y * share,
            };
            for row in weights.iter().filter(|row| row.vertex == corner) {
                *bones.entry(&row.target_bone).or_default() += row.weight * share;
            }
        }
        cap.normal = normalized(cap.normal).unwrap_or(vertices[corners[0]].normal);
        let rows: Vec<WeightRow> = bones
            .into_iter()
            .map(|(bone, weight)| WeightRow {
                vertex: middle as usize,
                target_bone: bone.to_owned(),
                weight,
            })
            .collect();
        weights.extend(rows);
        vertices.push(cap);
        triangles.extend(rim.iter().map(|&(a, b)| [b, a, middle]));
    }
    Ok(SourceMesh {
        vertices,
        triangles,
        weights,
    })
}

/// The rim of every hole whose edges lie wholly on the positions in `rim`, one list per hole.
/// `spot[i]` names the position vertex `i` sits at, shared by the copies a texture seam makes of
/// one vertex. A rim edge runs a->b in the triangle beside it, with no b->a at the same positions;
/// a cap crosses it b->a, so its winding matches the triangles around it.
fn openings(triangles: &[[u32; 3]], rim: &BTreeSet<u32>, spot: &[u32]) -> Vec<Vec<(u32, u32)>> {
    let at = |i: u32| spot[i as usize];
    let sides = |t: &[u32; 3]| [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])];
    let edges: BTreeSet<(u32, u32)> = triangles
        .iter()
        .flat_map(sides)
        .map(|(a, b)| (at(a), at(b)))
        .collect();
    let open: Vec<(u32, u32)> = triangles
        .iter()
        .flat_map(sides)
        .filter(|&(a, b)| {
            !edges.contains(&(at(b), at(a))) && rim.contains(&at(a)) && rim.contains(&at(b))
        })
        .collect();

    // Rim positions joined by an edge ring the same hole.
    let mut parent: BTreeMap<u32, u32> = open
        .iter()
        .flat_map(|&(a, b)| [at(a), at(b)])
        .map(|s| (s, s))
        .collect();
    let root = |parent: &BTreeMap<u32, u32>, mut s: u32| {
        while parent[&s] != s {
            s = parent[&s];
        }
        s
    };
    for &(a, b) in &open {
        let (ra, rb) = (root(&parent, at(a)), root(&parent, at(b)));
        parent.insert(ra.max(rb), ra.min(rb));
    }
    let mut holes: BTreeMap<u32, Vec<(u32, u32)>> = BTreeMap::new();
    for &(a, b) in &open {
        holes.entry(root(&parent, at(a))).or_default().push((a, b));
    }
    holes.into_values().collect()
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

fn parse_first_lod0_mesh(bytes: &[u8]) -> Result<DsrMesh> {
    let flver = Flver::parse(bytes)?;
    let mesh = *flver.meshes.first().ok_or("FLVER has no meshes")?;
    let mesh_bone_indices = parse_u32_list(bytes, mesh.bone_offset, mesh.bone_count)?;
    let vertex_buffer_indices =
        parse_u32_list(bytes, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)?;
    let first_vb = *vertex_buffer_indices
        .first()
        .ok_or("mesh has no vertex buffers")? as usize;
    let vertex_buffer = *flver
        .vertex_buffers
        .get(first_vb)
        .ok_or("mesh vertex buffer index out of range")?;
    let layout = *flver
        .layouts
        .get(vertex_buffer.layout_index)
        .ok_or("vertex buffer layout index out of range")?;
    let members = parse_layout_members(bytes, layout)?;

    let buffer_start = flver.header.data_offset + vertex_buffer.buffer_offset;
    let buffer = crate::bytes::slice(bytes, buffer_start, vertex_buffer.buffer_length)?;
    let uv_factor = flver.header.uv_factor();
    let mut vertices = vec![DsrVertex::default(); vertex_buffer.vertex_count];
    for (index, vertex) in vertices.iter_mut().enumerate() {
        let start = index * vertex_buffer.vertex_size;
        for member in &members {
            let off = start + member.struct_offset;
            match (member.semantic_id, member.format_id, member.index) {
                (0, 0x02, _) => vertex.position = read_vec3(buffer, off)?,
                (1, 0x1A, _) | (1, 0x16, _) => {
                    for i in 0..4 {
                        vertex.bone_weights[i] = f32::from(i16_le(buffer, off + i * 2)?) / 32767.0;
                    }
                }
                (1, 0x13, _) => {
                    for i in 0..4 {
                        vertex.bone_weights[i] = f32::from(u8_at(buffer, off + i)?) / 255.0;
                    }
                }
                (2, 0x11, _) | (2, 0x24, _) => {
                    for i in 0..4 {
                        vertex.bone_indices[i] = u16::from(u8_at(buffer, off + i)?);
                    }
                }
                (2, 0x18, _) => {
                    for i in 0..4 {
                        vertex.bone_indices[i] = u16_le(buffer, off + i * 2)?;
                    }
                }
                (3, 0x10, _) | (3, 0x11, _) | (3, 0x13, _) | (3, 0x2F, _) => {
                    vertex.normal = Vec3 {
                        x: f32::from(i8_at(buffer, off)?) / 127.0,
                        y: f32::from(i8_at(buffer, off + 1)?) / 127.0,
                        z: f32::from(i8_at(buffer, off + 2)?) / 127.0,
                    };
                }
                (5, 0x15, 0)
                | (5, 0x12, 0)
                | (5, 0x10, 0)
                | (5, 0x11, 0)
                | (5, 0x13, 0)
                | (5, 0x16, 0)
                | (5, 0x2E, 0) => {
                    vertex.uv = Vec2 {
                        x: f32::from(i16_le(buffer, off)?) / uv_factor,
                        y: f32::from(i16_le(buffer, off + 2)?) / uv_factor,
                    };
                }
                // Tangents, colours and secondary UVs are not carried into the donor.
                _ => {}
            }
        }
    }

    let face_set_indices = parse_u32_list(bytes, mesh.face_set_offset, mesh.face_set_count)?;
    let lod0 = face_set_indices
        .iter()
        .filter_map(|&index| flver.face_sets.get(index as usize))
        .find(|face_set| face_set.flags == 0)
        .ok_or("no LOD0 face set (flags == 0) found")?;
    let indices = parse_face_set_indices(bytes, &flver, lod0)?;

    Ok(DsrMesh {
        bone_names: flver.bones.iter().map(|bone| bone.name.clone()).collect(),
        mesh_bone_indices,
        vertices,
        triangles: triangulate(lod0.triangle_strip, &indices),
    })
}

fn triangulate(triangle_strip: bool, indices: &[u32]) -> Vec<[u32; 3]> {
    if !triangle_strip {
        return indices
            .as_chunks::<3>()
            .0
            .iter()
            .copied()
            .filter(|t| t[0] != t[1] && t[1] != t[2] && t[2] != t[0])
            .collect();
    }
    let mut triangles = Vec::new();
    let mut flip = false;
    for window in indices.windows(3) {
        let [a, b, c] = [window[0], window[1], window[2]];
        if a == PRIMITIVE_RESTART_U16 || b == PRIMITIVE_RESTART_U16 || c == PRIMITIVE_RESTART_U16 {
            flip = false;
            continue;
        }
        if a != b && b != c && c != a {
            triangles.push(if flip { [c, b, a] } else { [a, b, c] });
        }
        flip = !flip;
    }
    triangles
}

fn add(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.x + b.x,
        y: a.y + b.y,
        z: a.z + b.z,
    }
}

fn sub(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.x - b.x,
        y: a.y - b.y,
        z: a.z - b.z,
    }
}

fn mul(a: Vec3, k: f32) -> Vec3 {
    Vec3 {
        x: a.x * k,
        y: a.y * k,
        z: a.z * k,
    }
}

fn dot(a: Vec3, b: Vec3) -> f32 {
    a.x * b.x + a.y * b.y + a.z * b.z
}

fn cross(a: Vec3, b: Vec3) -> Vec3 {
    Vec3 {
        x: a.y * b.z - a.z * b.y,
        y: a.z * b.x - a.x * b.z,
        z: a.x * b.y - a.y * b.x,
    }
}

fn normalized(a: Vec3) -> Option<Vec3> {
    let length = dot(a, a).sqrt();
    (length > f32::EPSILON).then(|| mul(a, 1.0 / length))
}

/// The shortest rotation taking unit vector `from` onto unit vector `to`, as a quaternion.
fn arc_rotation(from: Vec3, to: Vec3) -> [f32; 4] {
    let axis = cross(from, to);
    let w = 1.0 + dot(from, to);
    let length = (dot(axis, axis) + w * w).sqrt();
    [
        axis.x / length,
        axis.y / length,
        axis.z / length,
        w / length,
    ]
}

fn rotate(q: [f32; 4], v: Vec3) -> Vec3 {
    let [x, y, z, w] = q;
    let u = Vec3 { x, y, z };
    let t = mul(cross(u, v), 2.0);
    add(add(v, mul(t, w)), cross(u, t))
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

impl SourceMesh {
    /// The OBJ the old exporter wrote (geometry lines only; its comment header is dropped).
    pub fn to_obj(&self) -> String {
        let mut out = String::new();
        for vertex in &self.vertices {
            let p = vertex.position;
            let _ = writeln!(out, "v {:.9} {:.9} {:.9}", p.x, p.y, p.z);
        }
        for vertex in &self.vertices {
            let _ = writeln!(out, "vt {:.9} {:.9}", vertex.uv.x, vertex.uv.y);
        }
        for vertex in &self.vertices {
            let n = vertex.normal;
            let _ = writeln!(out, "vn {:.9} {:.9} {:.9}", n.x, n.y, n.z);
        }
        for tri in &self.triangles {
            let _ = writeln!(
                out,
                "f {0}/{0}/{0} {1}/{1}/{1} {2}/{2}/{2}",
                tri[0] + 1,
                tri[1] + 1,
                tri[2] + 1
            );
        }
        out
    }

    /// The weight TSV's two columns the patcher reads, under the old header.
    pub fn to_weights_tsv(&self) -> String {
        let mut out = String::from(
            "vertex\tslot\tsource_mesh_bone_slot\tsource_global_bone_index\tsource_bone\ter_target_bone\tweight\n",
        );
        for row in &self.weights {
            let _ = writeln!(
                out,
                "{}\t-\t-\t-\t-\t{}\t{:.9}",
                row.vertex, row.target_bone, row.weight
            );
        }
        out
    }

    /// An OBJ (with `v`, `vt`, `vn` counts equal, one per vertex) and a weight TSV, as the
    /// Blender authoring export and the old Route A exporter both write them.
    pub fn from_obj_and_weights(obj: &str, weights_tsv: &str) -> Result<Self> {
        let mut positions = Vec::new();
        let mut normals = Vec::new();
        let mut uvs = Vec::new();
        let mut triangles = Vec::new();
        for line in obj.lines() {
            let mut parts = line.split_whitespace();
            let tag = parts.next();
            let mut float = |label: &str| -> Result<f32> {
                parts
                    .next()
                    .ok_or_else(|| Error::new(format!("OBJ line missing {label}: {line}")))?
                    .parse()
                    .map_err(|_| Error::new(format!("OBJ {label} is not a number: {line}")))
            };
            match tag {
                Some("v") => {
                    positions.push(Vec3 {
                        x: float("x")?,
                        y: float("y")?,
                        z: float("z")?,
                    });
                }
                Some("vn") => {
                    normals.push(Vec3 {
                        x: float("x")?,
                        y: float("y")?,
                        z: float("z")?,
                    });
                }
                Some("vt") => {
                    uvs.push(Vec2 {
                        x: float("u")?,
                        y: float("v")?,
                    });
                }
                Some("f") => {
                    let corners: Vec<u32> = line
                        .split_whitespace()
                        .skip(1)
                        .map(|token| {
                            token
                                .split('/')
                                .next()
                                .and_then(|index| index.parse::<u32>().ok())
                                .and_then(|index| index.checked_sub(1))
                                .ok_or_else(|| Error::new(format!("bad OBJ face: {line}")))
                        })
                        .collect::<Result<_>>()?;
                    if corners.len() != 3 {
                        return Err(Error::new(format!("OBJ face is not a triangle: {line}")));
                    }
                    triangles.push([corners[0], corners[1], corners[2]]);
                }
                _ => {}
            }
        }
        if positions.is_empty() || positions.len() != normals.len() || positions.len() != uvs.len()
        {
            return Err(Error::new(format!(
                "OBJ needs matching v/vn/vt counts, got v={} vn={} vt={}",
                positions.len(),
                normals.len(),
                uvs.len()
            )));
        }
        let vertices = positions
            .into_iter()
            .zip(normals)
            .zip(uvs)
            .map(|((position, normal), uv)| SourceVertex {
                position,
                normal,
                uv,
            })
            .collect();

        let mut weights = Vec::new();
        for (line_index, line) in weights_tsv.lines().enumerate() {
            if line_index == 0 || line.trim().is_empty() {
                continue;
            }
            let cols: Vec<&str> = line.split('\t').collect();
            if cols.len() < 7 {
                return Err(Error::new(format!(
                    "weight TSV line {} has {} columns, needs 7: {line}",
                    line_index + 1,
                    cols.len()
                )));
            }
            weights.push(WeightRow {
                vertex: cols[0]
                    .parse()
                    .map_err(|_| Error::new(format!("bad vertex index: {line}")))?,
                target_bone: cols[5].to_owned(),
                weight: cols[6]
                    .parse()
                    .map_err(|_| Error::new(format!("bad weight: {line}")))?,
            });
        }
        Ok(Self {
            vertices,
            triangles,
            weights,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn triangle_lists_drop_degenerate_triangles() {
        assert_eq!(triangulate(false, &[0, 1, 2, 3, 3, 4]), vec![[0, 1, 2]]);
    }

    #[test]
    fn triangle_strips_alternate_winding_and_reset_at_restart() {
        assert_eq!(
            triangulate(true, &[0, 1, 2, 3, 0xFFFF, 4, 5, 6]),
            vec![[0, 1, 2], [3, 2, 1], [4, 5, 6]]
        );
    }

    #[test]
    fn obj_and_weights_round_trip_through_the_text_the_scripts_wrote() {
        let mesh = SourceMesh {
            vertices: vec![
                SourceVertex {
                    position: Vec3 {
                        x: 0.5,
                        y: 1.0,
                        z: -0.25
                    },
                    normal: Vec3 {
                        x: 0.0,
                        y: 1.0,
                        z: 0.0
                    },
                    uv: Vec2 { x: 0.125, y: 0.75 },
                };
                3
            ],
            triangles: vec![[0, 1, 2]],
            weights: vec![WeightRow {
                vertex: 2,
                target_bone: "L_Hand".to_owned(),
                weight: 0.5,
            }],
        };
        let back =
            SourceMesh::from_obj_and_weights(&mesh.to_obj(), &mesh.to_weights_tsv()).unwrap();
        assert_eq!(back, mesh);
    }

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
        let body = BodyFit::new(0.381, 0.731, 0.97, 1.391, params).unwrap();
        assert!(close(body.place(v(0.0, 0.0, 0.0)), v(0.0, 0.0, 0.0)));
        assert!((body.place(v(0.1, 0.381, 0.0)).y - 0.97).abs() < 1e-5);
        assert!((body.place(v(0.1, 0.731, 0.0)).y - 1.391).abs() < 1e-5);
        // Above the shoulders, and across everywhere, it keeps the global scale.
        let (a, b) = (v(0.2, 0.9, -0.1), v(-0.3, 1.2, 0.2));
        let placed = sub(body.place(a), body.place(b));
        assert!(close(placed, params.scaled(sub(a, b))));
        assert!(BodyFit::new(0.7, 0.3, 0.97, 1.391, params).is_err());
    }

    #[test]
    fn the_shortest_arc_takes_one_direction_onto_the_other() {
        let from = normalized(v(1.0, -0.4, 0.1)).unwrap();
        let to = normalized(v(0.7, -0.7, 0.05)).unwrap();
        assert!(close(rotate(arc_rotation(from, to), from), to));
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
        ArmFit::new(OWN.map(at), HUMAN.map(at), targets, params).unwrap()
    }

    #[test]
    fn each_arm_joint_lands_on_its_human_joint_and_the_lean_spares_the_hand() {
        let params = FitParams::default();
        let arm = left_arm(params);
        let [shoulder, elbow, wrist, _, middle, ring] = OWN.map(at);
        let [er_shoulder, er_elbow, er_hand, er_middle, er_ring] = HUMAN.map(at);
        let lean = mul(arm.out, params.arm_lean);

        // The palm reaches from the human wrist to the human knuckle line.
        let knuckles = mul(add(middle, ring), 0.5);
        let er_knuckles = mul(add(er_middle, er_ring), 0.5);
        assert!(close(arm.place(2, knuckles, params), er_knuckles));

        // Shoulder and elbow lean out by the full amount; the wrist sits on the human hand.
        assert!(close(
            arm.place(0, shoulder, params),
            add(er_shoulder, lean)
        ));
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

    /// A cube with its top face missing, whose top corners 4..7 are the rim.
    fn open_box(sixth_corner_in_one_triangle: u32) -> Vec<[u32; 3]> {
        vec![
            [0, 2, 1],
            [0, 3, 2],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, sixth_corner_in_one_triangle],
            [3, 0, 4],
            [3, 4, 7],
        ]
    }

    /// Every directed edge, over positions, has its reverse and none appears twice.
    fn closed(triangles: &[[u32; 3]], spot: &[u32]) -> bool {
        let edges: Vec<_> = triangles
            .iter()
            .flat_map(|t| [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])])
            .map(|(a, b)| (spot[a as usize], spot[b as usize]))
            .collect();
        let set: BTreeSet<_> = edges.iter().copied().collect();
        set.len() == edges.len() && edges.iter().all(|&(a, b)| set.contains(&(b, a)))
    }

    /// The box capped the way `export_c2280` caps an opening: from a new vertex per hole.
    fn capped(open: &[[u32; 3]], spot: &[u32]) -> (Vec<[u32; 3]>, Vec<u32>) {
        let holes = openings(open, &[4, 5, 6, 7].into(), spot);
        let mut spot = spot.to_vec();
        let mut all = open.to_vec();
        for rim in holes {
            let middle = u32::try_from(spot.len()).unwrap();
            spot.push(100 + middle);
            all.extend(rim.iter().map(|&(a, b)| [b, a, middle]));
        }
        (all, spot)
    }

    #[test]
    fn a_cut_opening_is_capped_with_the_winding_around_it() {
        let open = open_box(6);
        let spot: Vec<u32> = (0..8).collect();
        assert_eq!(openings(&open, &[4, 5, 6, 7].into(), &spot).len(), 1);
        let (all, spot) = capped(&open, &spot);
        assert_eq!(all.len(), open.len() + 4);
        assert!(closed(&all, &spot));
    }

    #[test]
    fn an_opening_is_capped_across_a_vertex_the_texture_split() {
        // Vertex 8 is a copy of corner 6, used by one of the two triangles that meet there.
        let open = open_box(8);
        let spot = [0, 1, 2, 3, 4, 5, 6, 7, 6];
        let (all, spot) = capped(&open, &spot);
        assert_eq!(all.len(), open.len() + 4);
        assert!(closed(&all, &spot));
    }
}
