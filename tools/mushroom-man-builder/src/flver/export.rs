//! Dark Souls Remastered's c2280 as a source mesh for the Elden Ring donor: its first mesh's
//! LOD0 triangles, reshaped by the Route A knobs and weighted to Elden Ring bone names.
//!
//! Moved from `scripts/route_a_mushroom_export.rs` with its arithmetic unchanged. The script
//! wrote an OBJ and a weight TSV for the next tool to read back; here the same values go
//! straight into a [`SourceMesh`], and [`SourceMesh::to_obj`] / [`SourceMesh::to_weights_tsv`]
//! still emit the script's exact text for comparison and for the Blender authoring path.

use std::fmt::Write as _;

use super::{
    Flver, Header, Vec2, Vec3, parse_face_set_indices, parse_layout_members, parse_u32_list,
    read_vec3,
};
use crate::{
    bytes::{i8_at, i16_le, u8_at, u16_le},
    error::{Error, Result},
};

const DEFAULT_SCALE: f32 = 1.225;
const PRIMITIVE_RESTART_U16: u32 = 0xFFFF;
/// Weights at or below this are noise in the source skin and are dropped.
const WEIGHT_FLOOR: f32 = 0.0001;

/// The Route A reshaping knobs, at the values the prototype shipped with.
#[derive(Clone, Copy, Debug)]
pub struct RouteAParams {
    pub scale: f32,
    pub vertical_stretch: f32,
    pub arm_x_swell: f32,
    pub arm_y_swell: f32,
    pub arm_z_swell: f32,
    pub arm_shoulder_outward_offset: f32,
    pub arm_upper_outward_offset: f32,
    pub arm_forearm_outward_offset: f32,
    pub arm_hand_outward_offset: f32,
    pub arm_upper_to_shoulder_abs_x: f32,
    pub arm_forearm_to_upper_abs_x: f32,
    pub arm_forearm_to_hand_abs_x: f32,
    pub torso_x_scale: f32,
    pub torso_z_scale: f32,
    pub cap_x_scale: f32,
    pub cap_z_scale: f32,
    pub torso_start_norm_y: f32,
    pub cap_start_norm_y: f32,
}

impl Default for RouteAParams {
    fn default() -> Self {
        Self {
            scale: DEFAULT_SCALE,
            vertical_stretch: 1.14,
            arm_x_swell: 1.22,
            arm_y_swell: 1.08,
            arm_z_swell: 1.65,
            arm_shoulder_outward_offset: 0.33,
            arm_upper_outward_offset: 0.27,
            arm_forearm_outward_offset: 0.10,
            arm_hand_outward_offset: 0.02,
            arm_upper_to_shoulder_abs_x: 0.42,
            arm_forearm_to_upper_abs_x: 0.52,
            arm_forearm_to_hand_abs_x: 0.99,
            torso_x_scale: 1.0,
            torso_z_scale: 1.0,
            cap_x_scale: 1.0,
            cap_z_scale: 1.0,
            torso_start_norm_y: 0.18,
            cap_start_norm_y: 0.68,
        }
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

/// c2280's FLVER bytes, as a source mesh in Elden Ring's frame.
pub fn export_c2280(flver_bytes: &[u8], params: RouteAParams) -> Result<SourceMesh> {
    let mesh = parse_first_lod0_mesh(flver_bytes)?;
    let vertices = mesh
        .vertices
        .iter()
        .map(|vertex| SourceVertex {
            position: route_a_output_position(&mesh, vertex, params),
            normal: vertex.normal,
            uv: vertex.uv,
        })
        .collect();
    let mut weights = Vec::new();
    for (vertex_index, vertex) in mesh.vertices.iter().enumerate() {
        for slot in 0..4 {
            let weight = vertex.bone_weights[slot];
            if weight <= WEIGHT_FLOOR {
                continue;
            }
            let target = source_bone_name(&mesh, vertex.bone_indices[slot])
                .map(|name| {
                    er_target_for_vertex(
                        er_target_for_source_bone(name),
                        vertex,
                        &mesh.header,
                        params,
                    )
                })
                .unwrap_or("<missing>");
            weights.push(WeightRow {
                vertex: vertex_index,
                target_bone: target.to_owned(),
                weight,
            });
        }
    }
    Ok(SourceMesh {
        vertices,
        triangles: mesh.triangles,
        weights,
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

fn route_a_output_position(mesh: &DsrMesh, vertex: &DsrVertex, params: RouteAParams) -> Vec3 {
    let mut position = vertex.position;
    position.y *= params.vertical_stretch;

    let target = dominant_er_target_for_vertex(mesh, vertex, params);
    if is_arm_target(target) {
        let side =
            arm_side_for_target(target).unwrap_or(if position.x >= 0.0 { 1.0 } else { -1.0 });
        let center = Vec3 {
            x: side * 0.52,
            y: 0.78,
            z: 0.0,
        };
        position.x = center.x + (position.x - center.x) * params.arm_x_swell;
        position.y = center.y + (position.y - center.y) * params.arm_y_swell;
        position.z = center.z + (position.z - center.z) * params.arm_z_swell;
        position.x += side * arm_outward_offset_for_target(target, params);
    } else {
        let normalized_y = normalized_vertex_y(vertex, &mesh.header);
        if normalized_y >= params.torso_start_norm_y {
            let (x_scale, z_scale) = if normalized_y >= params.cap_start_norm_y {
                (params.cap_x_scale, params.cap_z_scale)
            } else {
                (params.torso_x_scale, params.torso_z_scale)
            };
            position.x *= x_scale;
            position.z *= z_scale;
        }
    }
    Vec3 {
        x: position.x * params.scale,
        y: position.y * params.scale,
        z: position.z * params.scale,
    }
}

fn normalized_vertex_y(vertex: &DsrVertex, header: &Header) -> f32 {
    let height = header.bbox_max.y - header.bbox_min.y;
    if height.abs() > f32::EPSILON {
        (vertex.position.y - header.bbox_min.y) / height
    } else {
        0.5
    }
}

fn dominant_er_target_for_vertex(
    mesh: &DsrMesh,
    vertex: &DsrVertex,
    params: RouteAParams,
) -> &'static str {
    let mut best_target = "Spine2";
    let mut best_weight = 0.0;
    for slot in 0..4 {
        let weight = vertex.bone_weights[slot];
        if weight <= best_weight {
            continue;
        }
        let Some(name) = source_bone_name(mesh, vertex.bone_indices[slot]) else {
            continue;
        };
        let base_target = er_target_for_source_bone(name);
        if base_target.starts_with('<') {
            continue;
        }
        best_target = er_target_for_vertex(base_target, vertex, &mesh.header, params);
        best_weight = weight;
    }
    best_target
}

fn arm_side_for_target(target: &str) -> Option<f32> {
    if target.starts_with("L_") {
        Some(1.0)
    } else if target.starts_with("R_") {
        Some(-1.0)
    } else {
        None
    }
}

fn arm_outward_offset_for_target(target: &str, params: RouteAParams) -> f32 {
    match target {
        "L_Shoulder" | "R_Shoulder" => params.arm_shoulder_outward_offset,
        "L_UpperArm" | "R_UpperArm" => params.arm_upper_outward_offset,
        "L_Forearm" | "R_Forearm" => params.arm_forearm_outward_offset,
        "L_Hand" | "R_Hand" => params.arm_hand_outward_offset,
        _ => 0.0,
    }
}

fn is_arm_target(target: &str) -> bool {
    matches!(
        target,
        "L_Shoulder"
            | "L_UpperArm"
            | "L_Forearm"
            | "L_Hand"
            | "R_Shoulder"
            | "R_UpperArm"
            | "R_Forearm"
            | "R_Hand"
    )
}

/// c2280's skeleton mapped onto Elden Ring's player bones.
fn er_target_for_source_bone(source: &str) -> &'static str {
    match source {
        "Pelvis" => "Pelvis",
        "Spine1" => "Spine",
        "Spine2" => "Spine1",
        "Spine3" => "Spine2",
        "Neck" => "Neck",
        "Head" => "Head",
        "LArm1" => "L_UpperArm",
        "LArm2" => "L_Forearm",
        "LArmPalm" | "LArmDigit11" | "LArmDigit12" | "LArmDigit21" | "LArmDigit22"
        | "LArmDigit31" | "LArmDigit32" => "L_Forearm",
        "RArm1" => "R_UpperArm",
        "RArm2" => "R_Forearm",
        "RArmPalm" | "RArmDigit11" | "RArmDigit12" | "RArmDigit21" | "RArmDigit22"
        | "RArmDigit31" | "RArmDigit32" => "R_Forearm",
        "LLeg1" => "L_Thigh",
        "RLeg1" => "R_Thigh",
        "c2280" | "Model_Dmy" | "sfx_dummy" | "固定dmy" | "master" => "<unused>",
        _ => "Spine2",
    }
}

/// Splits the coarse source mapping by where the vertex sits: shoulders and hands along the
/// arms, calves and feet down the single source leg bone.
fn er_target_for_vertex(
    base_target: &'static str,
    vertex: &DsrVertex,
    header: &Header,
    params: RouteAParams,
) -> &'static str {
    let normalized_y = normalized_vertex_y(vertex, header);
    let x = vertex.position.x;
    match base_target {
        "L_UpperArm" if x < params.arm_upper_to_shoulder_abs_x => "L_Shoulder",
        "L_Forearm" if x >= params.arm_forearm_to_hand_abs_x => "L_Hand",
        "L_Forearm" if x < params.arm_forearm_to_upper_abs_x => "L_UpperArm",
        "R_UpperArm" if x > -params.arm_upper_to_shoulder_abs_x => "R_Shoulder",
        "R_Forearm" if x <= -params.arm_forearm_to_hand_abs_x => "R_Hand",
        "R_Forearm" if x > -params.arm_forearm_to_upper_abs_x => "R_UpperArm",
        "L_Thigh" if normalized_y < 0.10 => "L_Foot",
        "L_Thigh" if normalized_y < 0.24 => "L_Calf",
        "R_Thigh" if normalized_y < 0.10 => "R_Foot",
        "R_Thigh" if normalized_y < 0.24 => "R_Calf",
        _ => base_target,
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

    #[test]
    fn source_bones_split_by_position_along_the_arm() {
        let params = RouteAParams::default();
        let header = Header {
            version: 0,
            data_offset: 0,
            data_length: 0,
            dummy_count: 0,
            material_count: 0,
            bone_count: 0,
            mesh_count: 0,
            vertex_buffer_count: 0,
            bbox_min: Vec3 {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            bbox_max: Vec3 {
                x: 0.0,
                y: 2.0,
                z: 0.0,
            },
            vertex_index_size: 16,
            face_set_count: 0,
            buffer_layout_count: 0,
        };
        let at = |x: f32| DsrVertex {
            position: Vec3 { x, y: 1.0, z: 0.0 },
            ..DsrVertex::default()
        };
        assert_eq!(
            er_target_for_vertex("L_Forearm", &at(1.2), &header, params),
            "L_Hand"
        );
        assert_eq!(
            er_target_for_vertex("L_Forearm", &at(0.3), &header, params),
            "L_UpperArm"
        );
        assert_eq!(
            er_target_for_vertex("R_UpperArm", &at(-0.1), &header, params),
            "R_Shoulder"
        );
    }
}
