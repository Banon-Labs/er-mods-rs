//! FLVER2 tables, read in place: the header, bones, meshes, face sets, vertex buffers and buffer
//! layouts that both Dark Souls Remastered's c2280 and Elden Ring's donor parts are made of.
//!
//! Nothing here rebuilds a FLVER. The donor is patched in place -- vertices and indices
//! overwritten inside the buffers it already has -- so the only structure needed is where each
//! table and buffer sits. Moved here from `scripts/route_a_mushroom_{export,patch_donor,
//! hide_flver_faces}.rs`, which each carried their own copy of these parsers.

pub mod export;
pub mod hide;
pub mod patch;

use crate::{
    bytes::{f32_le, i16_le, slice, u8_at, u32_le, utf16z},
    error::{Error, Result},
};

const HEADER_SIZE: usize = 0x80;
const DUMMY_SIZE: usize = 0x40;
const MATERIAL_SIZE: usize = 0x20;
const BONE_SIZE: usize = 0x80;
const MESH_SIZE: usize = 0x30;
const FACE_SET_SIZE: usize = 0x20;
const VERTEX_BUFFER_SIZE: usize = 0x20;
const BUFFER_LAYOUT_SIZE: usize = 0x10;
const LAYOUT_MEMBER_SIZE: usize = 0x14;
/// FLVER versions from this one on store UVs at 1/2048, older ones at 1/1024.
const UV_FACTOR_2048_VERSION: u32 = 0x2000F;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Vec2 {
    pub x: f32,
    pub y: f32,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Vec3 {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

#[derive(Clone, Copy, Debug)]
pub struct Header {
    pub version: u32,
    pub data_offset: usize,
    pub data_length: usize,
    pub dummy_count: usize,
    pub material_count: usize,
    pub bone_count: usize,
    pub mesh_count: usize,
    pub vertex_buffer_count: usize,
    pub bbox_min: Vec3,
    pub bbox_max: Vec3,
    pub vertex_index_size: u32,
    pub face_set_count: usize,
    pub buffer_layout_count: usize,
}

impl Header {
    pub fn uv_factor(&self) -> f32 {
        if self.version >= UV_FACTOR_2048_VERSION {
            2048.0
        } else {
            1024.0
        }
    }
}

#[derive(Clone, Debug)]
pub struct Bone {
    pub name: String,
    pub parent_index: i16,
    pub translation: Vec3,
    /// Euler angles in radians, applied X, then Z, then Y.
    pub rotation: Vec3,
    pub scale: Vec3,
}

/// A row-vector 4x4 transform, as System.Numerics and SoulsFormats compose them.
type Mat4 = [[f32; 4]; 4];

fn mat_mul(a: &Mat4, b: &Mat4) -> Mat4 {
    let mut out = [[0.0; 4]; 4];
    for (row, out_row) in out.iter_mut().enumerate() {
        for (col, cell) in out_row.iter_mut().enumerate() {
            *cell = (0..4).map(|k| a[row][k] * b[k][col]).sum();
        }
    }
    out
}

impl Bone {
    /// The bone relative to its parent: `scale * rotX * rotZ * rotY * translation`, the order
    /// SoulsFormats' `FLVER.Node.ComputeLocalTransform` uses.
    fn local_transform(&self) -> Mat4 {
        let s = self.scale;
        let (sx, cx) = self.rotation.x.sin_cos();
        let (sy, cy) = self.rotation.y.sin_cos();
        let (sz, cz) = self.rotation.z.sin_cos();
        let scale = [
            [s.x, 0.0, 0.0, 0.0],
            [0.0, s.y, 0.0, 0.0],
            [0.0, 0.0, s.z, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ];
        let rot_x = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, cx, sx, 0.0],
            [0.0, -sx, cx, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ];
        let rot_z = [
            [cz, sz, 0.0, 0.0],
            [-sz, cz, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ];
        let rot_y = [
            [cy, 0.0, -sy, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [sy, 0.0, cy, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ];
        let t = self.translation;
        let mut local = mat_mul(&mat_mul(&mat_mul(&scale, &rot_x), &rot_z), &rot_y);
        local[3] = [t.x, t.y, t.z, 1.0];
        local
    }
}

/// Where each bone's joint sits in the bind pose, in model space.
pub fn bind_positions(bones: &[Bone]) -> Result<Vec<Vec3>> {
    bones
        .iter()
        .map(|bone| {
            let mut world = bone.local_transform();
            let mut parent = bone.parent_index;
            for _ in 0..bones.len() {
                let Ok(index) = usize::try_from(parent) else {
                    let [x, y, z, _] = world[3];
                    return Ok(Vec3 { x, y, z });
                };
                let up = bones
                    .get(index)
                    .ok_or_else(|| Error::new(format!("bone parent {index} out of range")))?;
                world = mat_mul(&world, &up.local_transform());
                parent = up.parent_index;
            }
            Err(Error::new(format!("bone {} has a parent cycle", bone.name)))
        })
        .collect()
}

#[derive(Clone, Copy, Debug)]
pub struct Mesh {
    pub material_index: u32,
    pub default_bone_index: u32,
    pub bone_count: usize,
    pub bounding_box_offset: usize,
    pub bone_offset: usize,
    pub face_set_count: usize,
    pub face_set_offset: usize,
    pub vertex_buffer_count: usize,
    pub vertex_buffer_offset: usize,
}

#[derive(Clone, Copy, Debug)]
pub struct FaceSet {
    pub flags: u32,
    pub triangle_strip: bool,
    pub index_count: usize,
    pub index_offset: usize,
    pub index_size: u32,
}

#[derive(Clone, Copy, Debug)]
pub struct VertexBuffer {
    pub layout_index: usize,
    pub vertex_size: usize,
    pub vertex_count: usize,
    pub buffer_length: usize,
    pub buffer_offset: usize,
}

#[derive(Clone, Copy, Debug)]
pub struct Layout {
    pub member_count: usize,
    pub member_offset: usize,
}

#[derive(Clone, Copy, Debug)]
pub struct LayoutMember {
    pub struct_offset: usize,
    pub format_id: u32,
    pub semantic_id: u32,
    pub index: u32,
}

/// Where every table of one FLVER sits, plus the tables themselves.
pub struct Flver {
    pub header: Header,
    pub mesh_table: usize,
    pub bones: Vec<Bone>,
    pub meshes: Vec<Mesh>,
    pub face_sets: Vec<FaceSet>,
    pub vertex_buffers: Vec<VertexBuffer>,
    pub layouts: Vec<Layout>,
}

impl Flver {
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        let header = parse_header(bytes)?;
        let bone_table =
            HEADER_SIZE + DUMMY_SIZE * header.dummy_count + MATERIAL_SIZE * header.material_count;
        let mesh_table = bone_table + BONE_SIZE * header.bone_count;
        let face_set_table = mesh_table + MESH_SIZE * header.mesh_count;
        let vertex_buffer_table = face_set_table + FACE_SET_SIZE * header.face_set_count;
        let layout_table = vertex_buffer_table + VERTEX_BUFFER_SIZE * header.vertex_buffer_count;
        Ok(Self {
            bones: parse_bones(bytes, bone_table, header.bone_count)?,
            meshes: parse_meshes(bytes, mesh_table, header.mesh_count)?,
            face_sets: parse_face_sets(bytes, face_set_table, header.face_set_count)?,
            vertex_buffers: parse_vertex_buffers(
                bytes,
                vertex_buffer_table,
                header.vertex_buffer_count,
            )?,
            layouts: parse_layouts(bytes, layout_table, header.buffer_layout_count)?,
            mesh_table,
            header,
        })
    }

    pub fn mesh_table_offset(&self, mesh_index: usize) -> usize {
        self.mesh_table + mesh_index * MESH_SIZE
    }

    /// The index size a face set actually uses: its own, or the header's when it records none.
    pub fn index_size(&self, face_set: &FaceSet) -> u32 {
        if face_set.index_size == 0 {
            self.header.vertex_index_size
        } else {
            face_set.index_size
        }
    }
}

pub fn parse_header(bytes: &[u8]) -> Result<Header> {
    if bytes.len() < HEADER_SIZE || &bytes[0..6] != b"FLVER\0" || &bytes[6..8] != b"L\0" {
        return Err(Error::new("expected a little-endian FLVER header"));
    }
    Ok(Header {
        version: u32_le(bytes, 0x08)?,
        data_offset: u32_le(bytes, 0x0C)? as usize,
        data_length: u32_le(bytes, 0x10)? as usize,
        dummy_count: u32_le(bytes, 0x14)? as usize,
        material_count: u32_le(bytes, 0x18)? as usize,
        bone_count: u32_le(bytes, 0x1C)? as usize,
        mesh_count: u32_le(bytes, 0x20)? as usize,
        vertex_buffer_count: u32_le(bytes, 0x24)? as usize,
        bbox_min: read_vec3(bytes, 0x28)?,
        bbox_max: read_vec3(bytes, 0x34)?,
        vertex_index_size: u32::from(u8_at(bytes, 0x48)?),
        face_set_count: u32_le(bytes, 0x50)? as usize,
        buffer_layout_count: u32_le(bytes, 0x54)? as usize,
    })
}

pub fn read_vec3(bytes: &[u8], offset: usize) -> Result<Vec3> {
    Ok(Vec3 {
        x: f32_le(bytes, offset)?,
        y: f32_le(bytes, offset + 4)?,
        z: f32_le(bytes, offset + 8)?,
    })
}

fn parse_bones(bytes: &[u8], offset: usize, count: usize) -> Result<Vec<Bone>> {
    (0..count)
        .map(|i| {
            let off = offset + i * BONE_SIZE;
            let name_offset = u32_le(bytes, off + 0x0C)? as usize;
            Ok(Bone {
                // A name offset past the end is an unnamed bone, as the scripts read it.
                name: if name_offset >= bytes.len() {
                    String::new()
                } else {
                    utf16z(bytes, name_offset)?
                },
                parent_index: i16_le(bytes, off + 0x1C)?,
                translation: read_vec3(bytes, off)?,
                rotation: read_vec3(bytes, off + 0x10)?,
                scale: read_vec3(bytes, off + 0x20)?,
            })
        })
        .collect()
}

fn parse_meshes(bytes: &[u8], offset: usize, count: usize) -> Result<Vec<Mesh>> {
    (0..count)
        .map(|i| {
            let off = offset + i * MESH_SIZE;
            Ok(Mesh {
                material_index: u32_le(bytes, off + 0x04)?,
                default_bone_index: u32_le(bytes, off + 0x10)?,
                bone_count: u32_le(bytes, off + 0x14)? as usize,
                bounding_box_offset: u32_le(bytes, off + 0x18)? as usize,
                bone_offset: u32_le(bytes, off + 0x1C)? as usize,
                face_set_count: u32_le(bytes, off + 0x20)? as usize,
                face_set_offset: u32_le(bytes, off + 0x24)? as usize,
                vertex_buffer_count: u32_le(bytes, off + 0x28)? as usize,
                vertex_buffer_offset: u32_le(bytes, off + 0x2C)? as usize,
            })
        })
        .collect()
}

fn parse_face_sets(bytes: &[u8], offset: usize, count: usize) -> Result<Vec<FaceSet>> {
    (0..count)
        .map(|i| {
            let off = offset + i * FACE_SET_SIZE;
            Ok(FaceSet {
                flags: u32_le(bytes, off)?,
                triangle_strip: u8_at(bytes, off + 4)? != 0,
                index_count: u32_le(bytes, off + 8)? as usize,
                index_offset: u32_le(bytes, off + 0x0C)? as usize,
                index_size: u32_le(bytes, off + 0x18)?,
            })
        })
        .collect()
}

fn parse_vertex_buffers(bytes: &[u8], offset: usize, count: usize) -> Result<Vec<VertexBuffer>> {
    (0..count)
        .map(|i| {
            let off = offset + i * VERTEX_BUFFER_SIZE;
            Ok(VertexBuffer {
                layout_index: u32_le(bytes, off + 0x04)? as usize,
                vertex_size: u32_le(bytes, off + 0x08)? as usize,
                vertex_count: u32_le(bytes, off + 0x0C)? as usize,
                buffer_length: u32_le(bytes, off + 0x18)? as usize,
                buffer_offset: u32_le(bytes, off + 0x1C)? as usize,
            })
        })
        .collect()
}

fn parse_layouts(bytes: &[u8], offset: usize, count: usize) -> Result<Vec<Layout>> {
    (0..count)
        .map(|i| {
            let off = offset + i * BUFFER_LAYOUT_SIZE;
            Ok(Layout {
                member_count: u32_le(bytes, off)? as usize,
                member_offset: u32_le(bytes, off + 0x0C)? as usize,
            })
        })
        .collect()
}

pub fn parse_layout_members(bytes: &[u8], layout: Layout) -> Result<Vec<LayoutMember>> {
    (0..layout.member_count)
        .map(|i| {
            let off = layout.member_offset + i * LAYOUT_MEMBER_SIZE;
            Ok(LayoutMember {
                struct_offset: u32_le(bytes, off + 0x04)? as usize,
                format_id: u32_le(bytes, off + 0x08)?,
                semantic_id: u32_le(bytes, off + 0x0C)?,
                index: u32_le(bytes, off + 0x10)?,
            })
        })
        .collect()
}

pub fn parse_u32_list(bytes: &[u8], offset: usize, count: usize) -> Result<Vec<u32>> {
    slice(bytes, offset, count * 4)?;
    (0..count).map(|i| u32_le(bytes, offset + i * 4)).collect()
}

/// A face set's indices, widened to `u32`.
pub fn parse_face_set_indices(bytes: &[u8], flver: &Flver, face_set: &FaceSet) -> Result<Vec<u32>> {
    let range = index_range(bytes, flver, face_set)?;
    let data = &bytes[range];
    Ok(match flver.index_size(face_set) {
        16 => data
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&c| u32::from(u16::from_le_bytes(c)))
            .collect(),
        _ => data
            .as_chunks::<4>()
            .0
            .iter()
            .map(|&c| u32::from_le_bytes(c))
            .collect(),
    })
}

/// The byte range of a face set's indices, checked against the buffer.
pub fn index_range(
    bytes: &[u8],
    flver: &Flver,
    face_set: &FaceSet,
) -> Result<std::ops::Range<usize>> {
    let width = match flver.index_size(face_set) {
        16 => 2,
        32 => 4,
        other => return Err(Error::new(format!("unsupported FLVER index size {other}"))),
    };
    let start = flver.header.data_offset + face_set.index_offset;
    slice(bytes, start, face_set.index_count * width)?;
    Ok(start..start + face_set.index_count * width)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bone(parent_index: i16, translation: Vec3, rotation: Vec3) -> Bone {
        Bone {
            name: String::new(),
            parent_index,
            translation,
            rotation,
            scale: Vec3 {
                x: 1.0,
                y: 1.0,
                z: 1.0,
            },
        }
    }

    #[test]
    fn a_child_joint_is_carried_by_its_parents_turn() {
        // The parent sits at (0, 1, 0) turned a quarter about Z, so the child's one metre along
        // its local X points up: it lands at (0, 2, 0).
        let bones = [
            bone(
                -1,
                Vec3 {
                    x: 0.0,
                    y: 1.0,
                    z: 0.0,
                },
                Vec3 {
                    x: 0.0,
                    y: 0.0,
                    z: std::f32::consts::FRAC_PI_2,
                },
            ),
            bone(
                0,
                Vec3 {
                    x: 1.0,
                    y: 0.0,
                    z: 0.0,
                },
                Vec3::default(),
            ),
        ];
        let positions = bind_positions(&bones).unwrap();
        let child = positions[1];
        assert!(child.x.abs() < 1e-6 && (child.y - 2.0).abs() < 1e-6 && child.z.abs() < 1e-6);
    }

    #[test]
    fn a_parent_cycle_is_refused() {
        let bones = [
            bone(1, Vec3::default(), Vec3::default()),
            bone(0, Vec3::default(), Vec3::default()),
        ];
        assert!(bind_positions(&bones).is_err());
    }
}
