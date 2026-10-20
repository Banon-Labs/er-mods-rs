//! What `verify-package` asks of every FLVER in a body binder before the game is shown it:
//!
//! - the data section tiles the way From lays it out ([`super::rebuild`] says how), so every
//!   offset and length in the face set and vertex buffer tables is the one a reader expects,
//!   apart from the patch writer's shared LOD face sets, which are counted (`check_tiling`);
//! - every index names a vertex its mesh's buffer holds;
//! - every vertex's bone weights sum to one, and every bone index names a bone the FLVER has;
//! - every texture a material names by path is in the binder's TPF, under its `_l` name in a
//!   low-detail binder (whose FLVER keeps the unsuffixed path while its TPF holds `_l` names).
//!
//! A problem is reported with the face set, buffer, vertex or material it was found in; up to
//! [`PROBLEMS_SHOWN`] of them are listed, then a count of the rest.

use super::{
    Flver, VertexBuffer, parse_face_set_indices, parse_layout_members, parse_materials,
    parse_textures, parse_u32_list,
};
use crate::{
    bytes::{i16_le, slice, u16_le},
    error::{Error, Result},
};

const DATA_ALIGNMENT: usize = 16;
const STRIP_RESTART: u32 = 0xFFFF;
const WEIGHTS_SEMANTIC: u32 = 1;
const BONE_INDICES_SEMANTIC: u32 = 2;
/// From's own byte weights sum to 252 to 255, not always 255 (`FC_M_0000.flver`: 448, 7999,
/// 15894 and 1814 vertices at 252, 253, 254 and 255), so a sum is "one" within 3/255 and a
/// little.
const WEIGHT_SUM_TOLERANCE: f32 = 3.5 / 255.0;
pub const PROBLEMS_SHOWN: usize = 8;

/// What a FLVER that passed holds, for the summary line.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FlverCheck {
    pub meshes: usize,
    pub vertices: usize,
    /// Triangles in the LOD0 face sets that are not motion-blur copies.
    pub lod0_triangles: usize,
    /// Material textures named by path and found in the TPF.
    pub named_textures: usize,
    /// Material textures with an empty path, which the material's matbin names instead.
    pub unnamed_textures: usize,
    /// Face sets drawing another face set's indices instead of their own (see [`check_tiling`]).
    pub shared_face_sets: usize,
}

/// Check one decompressed FLVER against the rules above. `tpf_textures` are the names in the
/// same binder's TPF; `low` says the binder is a low-detail (`_l`) one.
pub fn check_flver(bytes: &[u8], tpf_textures: &[String], low: bool) -> Result<FlverCheck> {
    let flver = Flver::parse(bytes)?;
    let mut problems = Vec::new();
    let mut report = FlverCheck {
        meshes: flver.meshes.len(),
        ..FlverCheck::default()
    };
    report.shared_face_sets = check_tiling(bytes, &flver, &mut problems)?;

    for (mesh_index, mesh) in flver.meshes.iter().enumerate() {
        if mesh.material_index as usize >= flver.header.material_count {
            problems.push(format!(
                "mesh {mesh_index}: material {} of {}",
                mesh.material_index, flver.header.material_count
            ));
        }
        let buffers: Vec<VertexBuffer> =
            parse_u32_list(bytes, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)?
                .into_iter()
                .filter_map(|index| flver.vertex_buffers.get(index as usize).copied())
                .collect();
        let Some(vertex_count) = buffers.first().map(|buffer| buffer.vertex_count) else {
            problems.push(format!("mesh {mesh_index}: no vertex buffer"));
            continue;
        };
        report.vertices += vertex_count;
        for face_set_index in parse_u32_list(bytes, mesh.face_set_offset, mesh.face_set_count)? {
            let Some(face_set) = flver.face_sets.get(face_set_index as usize) else {
                continue;
            };
            let indices = parse_face_set_indices(bytes, &flver, face_set)?;
            let bad = indices.iter().find(|&&index| {
                index as usize >= vertex_count
                    && !(face_set.triangle_strip && index == STRIP_RESTART)
            });
            if let Some(index) = bad {
                problems.push(format!(
                    "face set {face_set_index}: index {index} past mesh {mesh_index}'s {vertex_count} vertices"
                ));
            }
            if face_set.lod() == 0 && !face_set.is_motion_blur() {
                report.lod0_triangles += face_set.index_count / 3;
            }
        }
        for buffer in &buffers {
            check_skin(bytes, &flver, buffer, mesh_index, &mut problems)?;
        }
    }

    let textures = parse_textures(bytes, &flver)?;
    for (material_index, material) in parse_materials(bytes, &flver)?.iter().enumerate() {
        let entries = textures
            .get(material.texture_index..material.texture_index + material.texture_count)
            .ok_or_else(|| {
                Error::new(format!("material {material_index}: textures out of range"))
            })?;
        for texture in entries {
            let Some(stem) = path_stem(&texture.path) else {
                report.unnamed_textures += 1;
                continue;
            };
            let wanted = if low && !stem.to_lowercase().ends_with("_l") {
                format!("{stem}_l")
            } else {
                stem.to_owned()
            };
            if tpf_textures
                .iter()
                .any(|name| name.eq_ignore_ascii_case(&wanted))
            {
                report.named_textures += 1;
            } else {
                problems.push(format!(
                    "material {material_index} ({}): texture {wanted} ({}) is not in the binder's TPF",
                    material.name, texture.sampler
                ));
            }
        }
    }

    if problems.is_empty() {
        Ok(report)
    } else {
        let mut message = problems
            .iter()
            .take(PROBLEMS_SHOWN)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ");
        if problems.len() > PROBLEMS_SHOWN {
            message += &format!("; and {} more", problems.len() - PROBLEMS_SHOWN);
        }
        Err(Error::new(message))
    }
}

/// Walk the meshes in table order and lay their face sets and buffers out the way the game's
/// files are laid out; every entry must sit exactly where that walk puts it, and the count of
/// face sets that share another's indices instead is returned.
///
/// That sharing is the patch writer's: c2280 has shipped with its body's LOD1, LOD2 and two of
/// their motion-blur copies pointed at the motion-blur LOD0's indices (`patch.rs`, the LOD
/// widening), each keeping its own count of the shared list and its old byte length, while
/// its own range stays in place, zeroed. The game draws it. So a face set whose offset and
/// count are another's in the same mesh -- one that sits where the walk puts it -- is counted
/// rather than refused, and the walk steps over its abandoned range by that old byte length.
/// The rebuild writer gives every face set its own range, so its output shares none.
fn check_tiling(bytes: &[u8], flver: &Flver, problems: &mut Vec<String>) -> Result<usize> {
    let header = &flver.header;
    let mut cursor = 0_usize;
    let mut shared = 0;
    let mut face_sets_seen = vec![0_u32; flver.face_sets.len()];
    let mut buffers_seen = vec![0_u32; flver.vertex_buffers.len()];
    for mesh in &flver.meshes {
        let mut placed = Vec::new();
        let mut displaced = Vec::new();
        for index in parse_u32_list(bytes, mesh.face_set_offset, mesh.face_set_count)? {
            let Some(face_set) = flver.face_sets.get(index as usize) else {
                problems.push(format!("a mesh names missing face set {index}"));
                continue;
            };
            face_sets_seen[index as usize] += 1;
            cursor = cursor.next_multiple_of(DATA_ALIGNMENT);
            let length = face_set.index_count * flver.index_size(face_set) as usize / 8;
            if face_set.index_offset == cursor && face_set.byte_length == length {
                placed.push((face_set.index_offset, face_set.index_count));
                cursor += length;
            } else {
                displaced.push((index, *face_set, cursor, length));
                cursor += face_set.byte_length;
            }
        }
        for (index, face_set, at, length) in displaced {
            if placed.contains(&(face_set.index_offset, face_set.index_count)) {
                shared += 1;
            } else {
                problems.push(format!(
                    "face set {index}: at 0x{:X} for 0x{:X} bytes, the layout puts it at 0x{at:X} for 0x{length:X}",
                    face_set.index_offset, face_set.byte_length
                ));
            }
        }
        for index in parse_u32_list(bytes, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)? {
            let Some(buffer) = flver.vertex_buffers.get(index as usize) else {
                problems.push(format!("a mesh names missing vertex buffer {index}"));
                continue;
            };
            buffers_seen[index as usize] += 1;
            cursor = cursor.next_multiple_of(DATA_ALIGNMENT);
            let length = buffer.vertex_count * buffer.vertex_size;
            if buffer.buffer_offset != cursor || buffer.buffer_length != length {
                problems.push(format!(
                    "vertex buffer {index}: at 0x{:X} for 0x{:X} bytes, the layout puts it at 0x{cursor:X} for 0x{length:X}",
                    buffer.buffer_offset, buffer.buffer_length
                ));
            }
            cursor = buffer.buffer_offset + length;
        }
    }
    for (what, seen) in [
        ("face set", &face_sets_seen),
        ("vertex buffer", &buffers_seen),
    ] {
        if let Some(index) = seen.iter().position(|&count| count != 1) {
            problems.push(format!(
                "{what} {index} belongs to {} meshes, not one",
                seen[index]
            ));
        }
    }
    let end = cursor.next_multiple_of(DATA_ALIGNMENT);
    if header.data_length != end || header.data_offset + header.data_length != bytes.len() {
        problems.push(format!(
            "data section 0x{:X} + 0x{:X} in a 0x{:X}-byte file; the layout ends it at 0x{end:X}",
            header.data_offset,
            header.data_length,
            bytes.len()
        ));
    }
    Ok(shared)
}

/// Each vertex's weights sum to one and its bone indices name bones the FLVER has.
fn check_skin(
    bytes: &[u8],
    flver: &Flver,
    buffer: &VertexBuffer,
    mesh_index: usize,
    problems: &mut Vec<String>,
) -> Result<()> {
    let Some(layout) = flver.layouts.get(buffer.layout_index) else {
        problems.push(format!(
            "mesh {mesh_index}: missing layout {}",
            buffer.layout_index
        ));
        return Ok(());
    };
    let members = parse_layout_members(bytes, *layout)?;
    let data = slice(
        bytes,
        flver.header.data_offset + buffer.buffer_offset,
        buffer.buffer_length,
    )?;
    let bone_count = flver.bones.len();
    let skin_members = members.iter().filter(|member| {
        matches!(
            (member.semantic_id, member.format_id),
            (WEIGHTS_SEMANTIC, 0x13 | 0x16 | 0x1A) | (BONE_INDICES_SEMANTIC, 0x11 | 0x24 | 0x18)
        )
    });
    for member in skin_members {
        for vertex in 0..buffer.vertex_count {
            let at = vertex * buffer.vertex_size + member.struct_offset;
            match (member.semantic_id, member.format_id) {
                (WEIGHTS_SEMANTIC, 0x13) => {
                    let sum: f32 = slice(data, at, 4)?
                        .iter()
                        .map(|&w| f32::from(w) / 255.0)
                        .sum();
                    if (sum - 1.0).abs() > WEIGHT_SUM_TOLERANCE {
                        problems.push(format!(
                            "mesh {mesh_index} vertex {vertex}: weights sum to {sum:.3}"
                        ));
                        break;
                    }
                }
                (WEIGHTS_SEMANTIC, 0x16 | 0x1A) => {
                    let mut sum = 0.0;
                    for i in 0..4 {
                        sum += f32::from(i16_le(data, at + i * 2)?) / 32767.0;
                    }
                    if (sum - 1.0).abs() > WEIGHT_SUM_TOLERANCE {
                        problems.push(format!(
                            "mesh {mesh_index} vertex {vertex}: weights sum to {sum:.3}"
                        ));
                        break;
                    }
                }
                _ => {
                    let mut indices = [0_usize; 4];
                    for (i, index) in indices.iter_mut().enumerate() {
                        *index = if member.format_id == 0x18 {
                            usize::from(u16_le(data, at + i * 2)?)
                        } else {
                            usize::from(slice(data, at + i, 1)?[0])
                        };
                    }
                    if let Some(bad) = indices.iter().find(|&&index| index >= bone_count) {
                        problems.push(format!(
                            "mesh {mesh_index} vertex {vertex}: bone {bad} of {bone_count}"
                        ));
                        break;
                    }
                }
            }
        }
    }
    Ok(())
}

/// `N:\...\FC_M_0000_a.tif` -> `FC_M_0000_a`; `None` for an empty path.
fn path_stem(path: &str) -> Option<&str> {
    let name = path.rsplit(['\\', '/']).next().unwrap_or(path);
    let stem = name.rsplit_once('.').map_or(name, |(stem, _)| stem);
    (!stem.is_empty()).then_some(stem)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::flver::synthetic::{self, MeshSpec, VERTEX_SIZE};

    fn donor() -> Vec<u8> {
        synthetic::flver(&[
            MeshSpec::with_lods(6, [6, 3, 3]),
            MeshSpec::with_lods(5, [3, 3, 3]),
        ])
    }

    #[test]
    fn a_well_formed_flver_passes_and_is_counted() {
        let report = check_flver(&donor(), &[], false).unwrap();
        assert_eq!(
            report,
            FlverCheck {
                meshes: 2,
                vertices: 11,
                lod0_triangles: 3,
                named_textures: 0,
                unnamed_textures: 0,
                shared_face_sets: 0,
            }
        );
    }

    #[test]
    fn a_face_set_sharing_another_in_its_mesh_is_counted_and_its_old_range_stepped_over() {
        // As the patch writer widens a LOD: face set 1 drawing face set 0's indices, its own
        // byte length and range left behind.
        let mut shared = donor();
        let flver = Flver::parse(&shared).unwrap();
        let (home, entry) = (flver.face_sets[0], flver.face_set_table_offset(1));
        shared[entry + 0x08..entry + 0x0C]
            .copy_from_slice(&(home.index_count as u32).to_le_bytes());
        shared[entry + 0x0C..entry + 0x10]
            .copy_from_slice(&(home.index_offset as u32).to_le_bytes());
        assert_eq!(
            check_flver(&shared, &[], false).unwrap().shared_face_sets,
            1
        );

        // The same pointer into the other mesh's face set is not sharing; it is misplaced.
        let mut foreign = donor();
        let other = flver.face_sets[6];
        foreign[entry + 0x0C..entry + 0x10]
            .copy_from_slice(&(other.index_offset as u32).to_le_bytes());
        assert!(
            check_flver(&foreign, &[], false)
                .unwrap_err()
                .to_string()
                .contains("face set 1: at")
        );
    }

    #[test]
    fn a_moved_buffer_an_out_of_range_index_and_a_bad_skin_are_each_named() {
        let fail = |bytes: &[u8]| check_flver(bytes, &[], false).unwrap_err().to_string();

        // Face set 1 a byte later than the layout puts it.
        let mut moved = donor();
        let flver = Flver::parse(&moved).unwrap();
        let entry = flver.face_set_table_offset(1) + 0x0C;
        moved[entry] += 2;
        assert!(fail(&moved).contains("face set 1: at 0x12"));

        // An index of 6 in a 6-vertex mesh.
        let mut index = donor();
        let flver = Flver::parse(&index).unwrap();
        let at = flver.header.data_offset + flver.face_sets[0].index_offset;
        index[at..at + 2].copy_from_slice(&6_u16.to_le_bytes());
        assert!(fail(&index).contains("index 6 past mesh 0's 6 vertices"));

        // Vertex 0 of mesh 0 with half its weight, then with bone 2 of 2.
        let buffer_at = |bytes: &[u8]| {
            let flver = Flver::parse(bytes).unwrap();
            flver.header.data_offset + flver.vertex_buffers[0].buffer_offset
        };
        let mut weight = donor();
        let at = buffer_at(&weight);
        weight[at + 16] = 128;
        assert!(fail(&weight).contains("mesh 0 vertex 0: weights sum to 0.502"));
        let mut bone = donor();
        let at = buffer_at(&bone);
        bone[at + 12] = 2;
        assert!(fail(&bone).contains("mesh 0 vertex 0: bone 2 of 2"));

        // A data section that does not end the file.
        let mut long = donor();
        long.extend([0; VERTEX_SIZE]);
        assert!(fail(&long).contains("data section"));
    }

    #[test]
    fn a_texture_path_resolves_by_stem_and_takes_l_in_a_low_binder() {
        assert_eq!(
            path_stem(r"N:\GR\data\parts\FC_M_0000_a.tif"),
            Some("FC_M_0000_a")
        );
        assert_eq!(path_stem("FC_M_0000_n.dds"), Some("FC_M_0000_n"));
        assert_eq!(path_stem(""), None);

        let flver = synthetic::flver_with_textures(
            &[MeshSpec::with_lods(3, [3, 3, 3])],
            &[r"N:\GR\data\parts\FC_M_0000_a.tif", ""],
        );
        let names = |list: &[&str]| list.iter().map(|&name| name.to_owned()).collect::<Vec<_>>();
        let high = check_flver(&flver, &names(&["FC_M_0000_a", "FC_M_0000_n"]), false).unwrap();
        assert_eq!((high.named_textures, high.unnamed_textures), (1, 1));
        check_flver(&flver, &names(&["fc_m_0000_a_l"]), true).unwrap();
        let error = check_flver(&flver, &names(&["FC_M_0000_a"]), true)
            .unwrap_err()
            .to_string();
        assert!(error.contains("texture FC_M_0000_a_l"), "{error}");
    }
}
