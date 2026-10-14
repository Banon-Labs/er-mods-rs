//! Dark Souls Remastered's c2280 as a source mesh for the Elden Ring donor: its first mesh's
//! LOD0 triangles, fitted to Elden Ring's bind skeleton and weighted to Elden Ring bone names.
//!
//! # The fit
//!
//! The game skins this mesh with the player's skeleton, so every vertex turns about the joint of
//! the Elden Ring bone it is weighted to. A part that sits far from that joint swings on a long
//! lever in every pose but the bind pose -- the standing idle included -- which is what bent the
//! first version's arms. So the body keeps c2280's own shape under one scale, and each arm is
//! moved as one rigid piece:
//!
//! - turned so it points along Elden Ring's arm (shoulder to hand, 45 degrees down in the bind
//!   pose) instead of c2280's own 23 degrees, so it hangs where a human arm hangs;
//! - kept at its own short length, rooted at c2280's shoulder;
//! - set [`FitParams::arm_offset`] up and out from there, so the wide body does not swallow it
//!   when the arm hangs;
//! - weighted wholly to `L_UpperArm` / `R_UpperArm`: it swings from the shoulder and does not
//!   bend at the human elbow, which falls partway down the short arm.
//!
//! Blending the body's and the arm's placements by c2280's own weights keeps the shoulder seam
//! closed. Measured with `scripts/mushroom-arm-fit-probe.py`, which poses the result into frame 0
//! of the standing idle the way the game skins it: the hand-tuned reshape this replaces turned
//! 128 of 1708 triangles inside out before any animation and left 1100 edges stretched or
//! squashed past 1.5x in the idle; this fit turns none inside out and leaves 212, 143 of them in
//! the legs, which it does not touch.
//!
//! [`SourceMesh::to_obj`] / [`SourceMesh::to_weights_tsv`] still emit the text the old
//! `scripts/route_a_mushroom_export.rs` wrote, for comparison and for the Blender authoring path.

use std::fmt::Write as _;

use super::{
    Flver, Header, Vec2, Vec3, bind_positions, parse_face_set_indices, parse_layout_members,
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
    /// The whole mushroom's scale: c2280 is a child, about 1.26 m to the top of its cap.
    pub scale: f32,
    /// Extra height on top of `scale`, so the cap clears a human head (1.76 m in all).
    pub vertical_stretch: f32,
    /// Metres each arm is set up and out from its shoulder, across Elden Ring's arm axis. At 0
    /// the hanging arms sink into the body (410 of 571 arm vertices inside it in the idle); at
    /// 0.2 that falls to 31 with the fewest stretched edges, and past it the seam stretches.
    pub arm_offset: f32,
}

impl Default for FitParams {
    fn default() -> Self {
        Self {
            scale: 1.225,
            vertical_stretch: 1.14,
            arm_offset: 0.2,
        }
    }
}

impl FitParams {
    fn scaled(&self, v: Vec3) -> Vec3 {
        Vec3 {
            x: v.x * self.scale,
            y: v.y * self.scale * self.vertical_stretch,
            z: v.z * self.scale,
        }
    }
}

/// One arm's rigid move from c2280's bind pose onto Elden Ring's.
#[derive(Clone, Copy, Debug)]
struct ArmFit {
    /// c2280's shoulder joint, unscaled.
    joint: Vec3,
    /// Where that joint lands.
    root: Vec3,
    /// Turns the scaled arm onto Elden Ring's arm axis (a quaternion, x y z w).
    rotation: [f32; 4],
    target: &'static str,
}

impl ArmFit {
    /// `joint` and `tip` are c2280's shoulder and palm; `shoulder` and `hand` are Elden Ring's.
    fn new(
        joint: Vec3,
        tip: Vec3,
        shoulder: Vec3,
        hand: Vec3,
        target: &'static str,
        params: FitParams,
    ) -> Result<Self> {
        let axis = normalized(sub(hand, shoulder)).ok_or("Elden Ring's arm has no length")?;
        let along =
            normalized(params.scaled(sub(tip, joint))).ok_or("c2280's arm has no length")?;
        let out =
            normalized(sub(UP, mul(axis, dot(UP, axis)))).ok_or("Elden Ring's arm is vertical")?;
        Ok(Self {
            joint,
            root: add(params.scaled(joint), mul(out, params.arm_offset)),
            rotation: arc_rotation(along, axis),
            target,
        })
    }

    fn place(&self, v: Vec3, params: FitParams) -> Vec3 {
        add(
            self.root,
            rotate(self.rotation, params.scaled(sub(v, self.joint))),
        )
    }
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
    header: Header,
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
    let left = ArmFit::new(
        joint("LArm1")?,
        joint("LArmPalm")?,
        er_joint("L_UpperArm")?,
        er_joint("L_Hand")?,
        "L_UpperArm",
        params,
    )?;
    let right = ArmFit::new(
        joint("RArm1")?,
        joint("RArmPalm")?,
        er_joint("R_UpperArm")?,
        er_joint("R_Hand")?,
        "R_UpperArm",
        params,
    )?;
    let arm_of = |name: &str| {
        if name.starts_with("LArm") {
            Some(&left)
        } else if name.starts_with("RArm") {
            Some(&right)
        } else {
            None
        }
    };

    let mut vertices = Vec::with_capacity(mesh.vertices.len());
    let mut weights = Vec::new();
    for (vertex_index, vertex) in mesh.vertices.iter().enumerate() {
        let body = params.scaled(vertex.position);
        let mut placed = Vec3::default();
        let mut total = 0.0;
        for slot in 0..4 {
            let weight = vertex.bone_weights[slot];
            if weight <= WEIGHT_FLOOR {
                continue;
            }
            let name = source_bone_name(&mesh, vertex.bone_indices[slot]);
            let (at, target) = match name.and_then(arm_of) {
                Some(arm) => (arm.place(vertex.position, params), arm.target),
                None => (
                    body,
                    name.map_or("<missing>", |name| body_target(name, vertex, &mesh.header)),
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
    Ok(SourceMesh {
        vertices,
        triangles: mesh.triangles,
        weights,
    })
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
        header: flver.header,
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

fn normalized_vertex_y(vertex: &DsrVertex, header: &Header) -> f32 {
    let height = header.bbox_max.y - header.bbox_min.y;
    if height.abs() > f32::EPSILON {
        (vertex.position.y - header.bbox_min.y) / height
    } else {
        0.5
    }
}

/// The Elden Ring bone for a weight on one of c2280's body or leg bones (the arms are
/// [`ArmFit`]'s). c2280 has one bone per leg, so its weight goes to the foot, calf or thigh by
/// how far up the model the vertex sits.
fn body_target(source: &str, vertex: &DsrVertex, header: &Header) -> &'static str {
    let normalized_y = normalized_vertex_y(vertex, header);
    let leg = |side: &'static [&'static str; 3]| {
        if normalized_y < 0.10 {
            side[0]
        } else if normalized_y < 0.24 {
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
    fn the_leg_bone_splits_into_foot_calf_and_thigh_by_height() {
        let header = Header {
            version: 0,
            data_offset: 0,
            data_length: 0,
            dummy_count: 0,
            material_count: 0,
            bone_count: 0,
            mesh_count: 0,
            vertex_buffer_count: 0,
            bbox_min: v(0.0, 0.0, 0.0),
            bbox_max: v(0.0, 2.0, 0.0),
            vertex_index_size: 16,
            face_set_count: 0,
            buffer_layout_count: 0,
        };
        let at = |y: f32| DsrVertex {
            position: v(0.1, y, 0.0),
            ..DsrVertex::default()
        };
        assert_eq!(body_target("LLeg1", &at(0.1), &header), "L_Foot");
        assert_eq!(body_target("RLeg1", &at(0.3), &header), "R_Calf");
        assert_eq!(body_target("LLeg1", &at(1.0), &header), "L_Thigh");
        assert_eq!(body_target("Spine3", &at(1.0), &header), "Spine2");
    }

    #[test]
    fn the_shortest_arc_takes_one_direction_onto_the_other() {
        let from = normalized(v(1.0, -0.4, 0.1)).unwrap();
        let to = normalized(v(0.7, -0.7, 0.05)).unwrap();
        assert!(close(rotate(arc_rotation(from, to), from), to));
    }

    #[test]
    fn an_arm_moves_whole_onto_the_elden_ring_arm_at_its_own_length() {
        let params = FitParams::default();
        let (joint, tip) = (v(0.2, 0.73, 0.0), v(0.45, 0.62, 0.0));
        let (shoulder, hand) = (v(0.17, 1.39, 0.03), v(0.53, 1.03, 0.02));
        let arm = ArmFit::new(joint, tip, shoulder, hand, "L_UpperArm", params).unwrap();

        // The root is the scaled shoulder, moved `arm_offset` up and out across the ER arm.
        let root = arm.place(joint, params);
        let offset = sub(root, params.scaled(joint));
        assert!((dot(offset, offset).sqrt() - params.arm_offset).abs() < 1e-5);
        assert!(offset.x > 0.0 && offset.y > 0.0);

        // It points down Elden Ring's arm, and keeps its own scaled length.
        let placed = sub(arm.place(tip, params), root);
        let axis = normalized(sub(hand, shoulder)).unwrap();
        let length = dot(placed, placed).sqrt();
        assert!(close(mul(placed, 1.0 / length), axis));
        let scaled = params.scaled(sub(tip, joint));
        assert!((length - dot(scaled, scaled).sqrt()).abs() < 1e-5);

        // Any two points of the arm keep their scaled distance: the move is rigid.
        let (a, b) = (v(0.3, 0.7, 0.05), v(0.4, 0.6, -0.04));
        let before = params.scaled(sub(a, b));
        let after = sub(arm.place(a, params), arm.place(b, params));
        assert!((dot(before, before).sqrt() - dot(after, after).sqrt()).abs() < 1e-5);
    }
}
