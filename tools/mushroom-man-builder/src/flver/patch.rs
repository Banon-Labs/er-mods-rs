//! Writes a [`SourceMesh`] into one mesh of an Elden Ring donor FLVER, in place.
//!
//! The donor keeps its own structure -- buffers, layouts, materials, skeleton -- and the chosen
//! mesh's vertex buffer and face sets are overwritten with the source's vertices and triangles.
//! Every other mesh in the donor has its indices zeroed, so it draws nothing. Moved from
//! `scripts/route_a_mushroom_patch_donor.rs` with its arithmetic unchanged.
//!
//! In-place is the reason for both limits this enforces and reports: the source cannot have
//! more vertices than the donor buffer holds (refused), and triangles past the donor's index
//! capacity do not fit (counted in [`PatchReport::triangles_dropped`]).

use std::collections::HashMap;

use super::{
    FaceSet, Flver, LayoutMember, Vec2, Vec3, export::SourceMesh, index_range,
    parse_layout_members, parse_u32_list,
};
use crate::error::{Error, Result};

/// The FLVER version of Elden Ring's player parts; the patcher refuses anything else.
const ELDEN_RING_FLVER_VERSION: u32 = 0x2001A;
/// Every vertex needs at least one bone; one with no usable weight goes to the chest.
const FALLBACK_BONE: &str = "Spine2";
const MAX_BONES_PER_VERTEX: usize = 4;
const WEIGHT_FLOOR: f32 = 0.0001;

#[derive(Clone, Copy, Debug, Default)]
struct SkinnedVertex {
    position: Vec3,
    normal: Vec3,
    uv: Vec2,
    bone_indices: [u8; 4],
    bone_weights: [f32; 4],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PatchReport {
    pub vertex_capacity: usize,
    pub lod0_index_capacity: usize,
    pub patched_face_sets: usize,
    pub hidden_face_sets: usize,
    pub triangles_dropped: usize,
}

/// Patch `donor` (an Elden Ring FLVER) so its mesh `donor_mesh_index` is `source`.
pub fn patch_donor(
    donor: &mut [u8],
    source: &SourceMesh,
    donor_mesh_index: usize,
) -> Result<PatchReport> {
    let flver = Flver::parse(donor)?;
    if flver.header.version != ELDEN_RING_FLVER_VERSION {
        return Err(Error::new(format!(
            "expected an Elden Ring donor FLVER 0x{ELDEN_RING_FLVER_VERSION:X}, got 0x{:X}",
            flver.header.version
        )));
    }
    let bones: HashMap<String, u16> = flver
        .bones
        .iter()
        .enumerate()
        .map(|(index, bone)| Ok((bone.name.clone(), u16::try_from(index)?)))
        .collect::<Result<_>>()?;
    let vertices = skin(source, &bones)?;
    let (bbox_min, bbox_max) = bbox(&vertices);

    let donor_mesh = *flver
        .meshes
        .get(donor_mesh_index)
        .ok_or_else(|| Error::new(format!("donor mesh index {donor_mesh_index} out of range")))?;
    // Material 0 is the donor's first slot. The Route A prototype chose it over the mesh's own
    // material for its shader family, and that choice is kept with the rest of the arithmetic.
    write_u32(donor, flver.mesh_table_offset(donor_mesh_index) + 0x04, 0)?;
    let vertex_buffer_index = *parse_u32_list(
        donor,
        donor_mesh.vertex_buffer_offset,
        donor_mesh.vertex_buffer_count,
    )?
    .first()
    .ok_or("donor mesh has no vertex buffers")? as usize;
    let vertex_buffer = *flver
        .vertex_buffers
        .get(vertex_buffer_index)
        .ok_or("donor vertex buffer index out of range")?;
    if vertex_buffer.vertex_count < vertices.len() {
        return Err(Error::new(format!(
            "donor vertex buffer holds {} vertices, the source has {}",
            vertex_buffer.vertex_count,
            vertices.len()
        )));
    }
    let layout = *flver
        .layouts
        .get(vertex_buffer.layout_index)
        .ok_or("donor vertex buffer layout index out of range")?;
    let members = parse_layout_members(donor, layout)?;
    let buffer_start = flver.header.data_offset + vertex_buffer.buffer_offset;
    crate::bytes::slice(donor, buffer_start, vertex_buffer.buffer_length)?;
    for index in 0..vertex_buffer.vertex_count {
        let vertex = vertices.get(index).copied().unwrap_or_default();
        let start = buffer_start + index * vertex_buffer.vertex_size;
        write_vertex(donor, start, &members, &vertex, flver.header.uv_factor())?;
    }
    write_vec3(donor, 0x28, bbox_min)?;
    write_vec3(donor, 0x34, bbox_max)?;
    if donor_mesh.bounding_box_offset != 0 {
        write_vec3(donor, donor_mesh.bounding_box_offset, bbox_min)?;
        write_vec3(donor, donor_mesh.bounding_box_offset + 0x0C, bbox_max)?;
    }

    let mut report = PatchReport {
        vertex_capacity: vertex_buffer.vertex_count,
        lod0_index_capacity: 0,
        patched_face_sets: 0,
        hidden_face_sets: 0,
        triangles_dropped: 0,
    };
    for (mesh_index, mesh) in flver.meshes.iter().enumerate() {
        for face_set_index in parse_u32_list(donor, mesh.face_set_offset, mesh.face_set_count)? {
            let face_set = *flver
                .face_sets
                .get(face_set_index as usize)
                .ok_or("mesh references a missing face set")?;
            if mesh_index == donor_mesh_index {
                if face_set.triangle_strip {
                    return Err(Error::new(
                        "the donor mesh's face set is a triangle strip; expected a list",
                    ));
                }
                if report.patched_face_sets == 0 {
                    report.lod0_index_capacity = face_set.index_count;
                    report.triangles_dropped = source
                        .triangles
                        .len()
                        .saturating_sub(face_set.index_count / 3);
                }
                write_indices(donor, &flver, &face_set, &source.triangles)?;
                report.patched_face_sets += 1;
            } else {
                let range = index_range(donor, &flver, &face_set)?;
                donor[range].fill(0);
                report.hidden_face_sets += 1;
            }
        }
    }
    Ok(report)
}

/// Per-vertex Elden Ring bone indices and weights: the source's rows summed per donor bone,
/// the heaviest four kept, normalised to one.
fn skin(source: &SourceMesh, bones: &HashMap<String, u16>) -> Result<Vec<SkinnedVertex>> {
    let resolve = |name: &str| -> Option<u16> {
        bones.get(name).copied().or_else(|| {
            (name == "Head")
                .then(|| bones.get("Neck").copied())
                .flatten()
        })
    };
    let byte_bone = |index: u16, name: &str| -> Result<u8> {
        u8::try_from(index).map_err(|_| {
            Error::new(format!(
                "donor bone {name} index {index} does not fit a byte weight"
            ))
        })
    };

    let mut accumulated = vec![[0.0_f32; 256]; source.vertices.len()];
    for row in &source.weights {
        if row.weight <= 0.0 || row.target_bone.starts_with('<') {
            continue;
        }
        let bone = resolve(&row.target_bone).ok_or_else(|| {
            Error::new(format!("the donor has no bone named {}", row.target_bone))
        })?;
        let bone = byte_bone(bone, &row.target_bone)?;
        accumulated
            .get_mut(row.vertex)
            .ok_or_else(|| Error::new(format!("weight for missing vertex {}", row.vertex)))?
            [usize::from(bone)] += row.weight;
    }

    source
        .vertices
        .iter()
        .zip(&accumulated)
        .map(|(vertex, weights)| {
            let mut pairs: Vec<(u8, f32)> = weights
                .iter()
                .enumerate()
                .filter(|&(_, &weight)| weight > WEIGHT_FLOOR)
                .map(|(bone, &weight)| (bone as u8, weight))
                .collect();
            if pairs.is_empty() {
                let fallback = resolve(FALLBACK_BONE).ok_or("the donor has no Spine2 bone")?;
                pairs.push((byte_bone(fallback, FALLBACK_BONE)?, 1.0));
            }
            pairs.sort_by(|a, b| b.1.total_cmp(&a.1));
            pairs.truncate(MAX_BONES_PER_VERTEX);
            let total: f32 = pairs.iter().map(|&(_, weight)| weight).sum();
            let mut skinned = SkinnedVertex {
                position: vertex.position,
                normal: vertex.normal,
                uv: vertex.uv,
                ..SkinnedVertex::default()
            };
            for (slot, (bone, weight)) in pairs.into_iter().enumerate() {
                skinned.bone_indices[slot] = bone;
                skinned.bone_weights[slot] = if total > 0.0 { weight / total } else { 0.0 };
            }
            Ok(skinned)
        })
        .collect()
}

fn bbox(vertices: &[SkinnedVertex]) -> (Vec3, Vec3) {
    let mut min = Vec3 {
        x: f32::INFINITY,
        y: f32::INFINITY,
        z: f32::INFINITY,
    };
    let mut max = Vec3 {
        x: f32::NEG_INFINITY,
        y: f32::NEG_INFINITY,
        z: f32::NEG_INFINITY,
    };
    for vertex in vertices {
        let p = vertex.position;
        min = Vec3 {
            x: min.x.min(p.x),
            y: min.y.min(p.y),
            z: min.z.min(p.z),
        };
        max = Vec3 {
            x: max.x.max(p.x),
            y: max.y.max(p.y),
            z: max.z.max(p.z),
        };
    }
    (min, max)
}

fn write_vertex(
    bytes: &mut [u8],
    start: usize,
    members: &[LayoutMember],
    vertex: &SkinnedVertex,
    uv_factor: f32,
) -> Result<()> {
    for member in members {
        let off = start + member.struct_offset;
        match (member.semantic_id, member.format_id) {
            (0, 0x02) => write_vec3(bytes, off, vertex.position)?,
            (3, 0x10 | 0x11 | 0x13 | 0x2F) => {
                let n = vertex.normal;
                write_snorm8x4(bytes, off, [n.x, n.y, n.z, 1.0])?;
            }
            (6, 0x10 | 0x11 | 0x13 | 0x2F) => write_snorm8x4(bytes, off, [1.0, 0.0, 0.0, 1.0])?,
            (7, 0x10 | 0x11 | 0x13 | 0x2F) => write_snorm8x4(bytes, off, [0.0, 1.0, 0.0, 1.0])?,
            (2, 0x11 | 0x24) => put(bytes, off, &vertex.bone_indices)?,
            (2, 0x18) => {
                for (i, &bone) in vertex.bone_indices.iter().enumerate() {
                    put(bytes, off + i * 2, &u16::from(bone).to_le_bytes())?;
                }
            }
            (1, 0x13) => write_unorm8x4(bytes, off, vertex.bone_weights)?,
            (1, 0x16 | 0x1A) => {
                for (i, &weight) in vertex.bone_weights.iter().enumerate() {
                    let value = (weight.clamp(0.0, 1.0) * 32767.0).round() as i16;
                    put(bytes, off + i * 2, &value.to_le_bytes())?;
                }
            }
            (10, 0x10 | 0x11 | 0x13 | 0x2F) => put(bytes, off, &[255; 4])?,
            (5, 0x10 | 0x11 | 0x12 | 0x13 | 0x15) => write_uv(bytes, off, vertex.uv, uv_factor)?,
            (5, 0x16 | 0x2E) => {
                write_uv(bytes, off, vertex.uv, uv_factor)?;
                write_uv(bytes, off + 4, vertex.uv, uv_factor)?;
            }
            _ => {}
        }
    }
    Ok(())
}

/// The source triangles, repeated into every slot of the face set; slots past the source's
/// last triangle are index 0, which draws nothing.
fn write_indices(
    bytes: &mut [u8],
    flver: &Flver,
    face_set: &FaceSet,
    triangles: &[[u32; 3]],
) -> Result<()> {
    let range = index_range(bytes, flver, face_set)?;
    let index_at = |slot: usize| triangles.get(slot / 3).map_or(0, |t| t[slot % 3]);
    let data = &mut bytes[range];
    if flver.index_size(face_set) == 16 {
        for (slot, out) in data.as_chunks_mut::<2>().0.iter_mut().enumerate() {
            *out = (index_at(slot) as u16).to_le_bytes();
        }
    } else {
        for (slot, out) in data.as_chunks_mut::<4>().0.iter_mut().enumerate() {
            *out = index_at(slot).to_le_bytes();
        }
    }
    Ok(())
}

fn put(bytes: &mut [u8], offset: usize, value: &[u8]) -> Result<()> {
    let end = offset + value.len();
    bytes
        .get_mut(offset..end)
        .ok_or_else(|| Error::new(format!("write at 0x{offset:X} runs past the donor")))?
        .copy_from_slice(value);
    Ok(())
}

fn write_u32(bytes: &mut [u8], offset: usize, value: u32) -> Result<()> {
    put(bytes, offset, &value.to_le_bytes())
}

fn write_vec3(bytes: &mut [u8], offset: usize, value: Vec3) -> Result<()> {
    put(bytes, offset, &value.x.to_le_bytes())?;
    put(bytes, offset + 4, &value.y.to_le_bytes())?;
    put(bytes, offset + 8, &value.z.to_le_bytes())
}

fn write_uv(bytes: &mut [u8], offset: usize, uv: Vec2, factor: f32) -> Result<()> {
    put(
        bytes,
        offset,
        &((uv.x * factor).round() as i16).to_le_bytes(),
    )?;
    put(
        bytes,
        offset + 2,
        &((uv.y * factor).round() as i16).to_le_bytes(),
    )
}

fn write_snorm8x4(bytes: &mut [u8], offset: usize, values: [f32; 4]) -> Result<()> {
    let encoded = values.map(|value| (value.clamp(-1.0, 1.0) * 127.0).round() as i8 as u8);
    put(bytes, offset, &encoded)
}

/// Byte weights that always sum to 255: rounding error is folded into the first weight.
fn write_unorm8x4(bytes: &mut [u8], offset: usize, values: [f32; 4]) -> Result<()> {
    let mut encoded = values.map(|value| (value.clamp(0.0, 1.0) * 255.0).round() as u8);
    let total: u16 = encoded.iter().map(|&byte| u16::from(byte)).sum();
    if total == 0 {
        encoded[0] = 255;
    } else if total != 255 {
        let first = i16::from(encoded[0]) + (255 - total as i16);
        encoded[0] = first.clamp(0, 255) as u8;
    }
    put(bytes, offset, &encoded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn byte_weights_always_sum_to_255() {
        let mut buf = [0_u8; 4];
        write_unorm8x4(&mut buf, 0, [0.334, 0.333, 0.333, 0.0]).unwrap();
        assert_eq!(buf.iter().map(|&b| u16::from(b)).sum::<u16>(), 255);
        write_unorm8x4(&mut buf, 0, [0.0; 4]).unwrap();
        assert_eq!(buf, [255, 0, 0, 0]);
    }

    #[test]
    fn heaviest_four_bones_are_kept_and_normalised() {
        let bones: HashMap<String, u16> = ["Spine2", "A", "B", "C", "D", "E"]
            .iter()
            .enumerate()
            .map(|(i, name)| ((*name).to_owned(), i as u16))
            .collect();
        let row = |bone: &str, weight: f32| super::super::export::WeightRow {
            vertex: 0,
            target_bone: bone.to_owned(),
            weight,
        };
        let source = SourceMesh {
            vertices: vec![Default::default()],
            triangles: vec![],
            weights: vec![
                row("A", 0.1),
                row("B", 0.4),
                row("C", 0.2),
                row("D", 0.2),
                row("E", 0.1),
            ],
        };
        let skinned = skin(&source, &bones).unwrap();
        assert_eq!(skinned[0].bone_indices[0], 2);
        let sum: f32 = skinned[0].bone_weights.iter().sum();
        assert!((sum - 1.0).abs() < 1e-6);
    }

    #[test]
    fn a_vertex_without_weights_goes_to_the_chest() {
        let bones: HashMap<String, u16> = [("Spine2".to_owned(), 7)].into_iter().collect();
        let source = SourceMesh {
            vertices: vec![Default::default()],
            ..SourceMesh::default()
        };
        let skinned = skin(&source, &bones).unwrap();
        assert_eq!(skinned[0].bone_indices[0], 7);
        assert_eq!(skinned[0].bone_weights[0], 1.0);
    }
}
