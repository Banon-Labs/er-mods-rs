//! A hand-built Elden Ring `0x2001A` FLVER for the writer's and the checker's tests: no game
//! bytes, only the tables the rebuild touches, laid out the way the donor's are (every face set
//! and vertex buffer at the next 16-byte boundary of the data section, the section padded to 16
//! and ending the file).

use super::FACE_SET_MOTION_BLUR;

/// One mesh: its vertex count and, in order, its face sets' flags and index counts.
pub struct MeshSpec {
    pub vertex_count: usize,
    pub face_sets: Vec<(u32, usize)>,
}

impl MeshSpec {
    /// LOD0, LOD1, LOD2 with these index counts, each followed later by its motion-blur twin,
    /// the order the donor's body meshes list them in.
    pub fn with_lods(vertex_count: usize, counts: [usize; 3]) -> Self {
        let lods = [0, 0x0100_0000, 0x0200_0000];
        let mut face_sets: Vec<(u32, usize)> = lods.iter().copied().zip(counts).collect();
        face_sets.extend(
            lods.iter()
                .map(|flags| flags | FACE_SET_MOTION_BLUR)
                .zip(counts),
        );
        Self {
            vertex_count,
            face_sets,
        }
    }
}

/// Position, four byte bone indices, four byte weights.
pub const VERTEX_SIZE: usize = 20;
pub const BONE_COUNT: usize = 2;
const MESH_SIZE: usize = 0x30;
const FACE_SET_SIZE: usize = 0x20;
const VERTEX_BUFFER_SIZE: usize = 0x20;
const BOX_SIZE: usize = 0x24;

fn utf16z(text: &str) -> Vec<u8> {
    text.encode_utf16()
        .chain([0])
        .flat_map(u16::to_le_bytes)
        .collect()
}

fn put_u32(bytes: &mut [u8], offset: usize, value: usize) {
    bytes[offset..offset + 4].copy_from_slice(&u32::try_from(value).unwrap().to_le_bytes());
}

fn pad(bytes: &mut Vec<u8>) {
    bytes.resize(bytes.len().next_multiple_of(16), 0);
}

/// The vertex the synthetic FLVER stores at `index`: a position along a line, bone
/// `index % BONE_COUNT` at full weight.
pub fn vertex(index: usize) -> [u8; VERTEX_SIZE] {
    let mut out = [0; VERTEX_SIZE];
    let position = [index as f32 * 0.25, index as f32 * 0.5, -(index as f32)];
    for (i, value) in position.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&value.to_le_bytes());
    }
    out[12] = (index % BONE_COUNT) as u8;
    out[16] = 255;
    out
}

pub fn flver(meshes: &[MeshSpec]) -> Vec<u8> {
    flver_with_textures(meshes, &[])
}

/// [`flver`] with its one material naming these texture paths (an empty one leaves the file to
/// the matbin, as Elden Ring's player parts do).
pub fn flver_with_textures(meshes: &[MeshSpec], texture_paths: &[&str]) -> Vec<u8> {
    let face_set_count: usize = meshes.iter().map(|mesh| mesh.face_sets.len()).sum();
    let material_table = 0x80;
    let bone_table = material_table + 0x20;
    let mesh_table = bone_table + 0x80 * BONE_COUNT;
    let face_set_table = mesh_table + MESH_SIZE * meshes.len();
    let buffer_table = face_set_table + FACE_SET_SIZE * face_set_count;
    let layout_table = buffer_table + VERTEX_BUFFER_SIZE * meshes.len();
    let texture_table = layout_table + 0x10;
    let members = texture_table + 0x20 * texture_paths.len();
    let boxes = members + 3 * 0x14;
    let lists = boxes + BOX_SIZE * meshes.len();
    let mut bytes = vec![0; lists + 4 * (face_set_count + meshes.len())];

    bytes[..8].copy_from_slice(b"FLVER\0L\0");
    put_u32(&mut bytes, 0x08, 0x2001A);
    put_u32(&mut bytes, 0x18, 1);
    put_u32(&mut bytes, 0x1C, BONE_COUNT);
    put_u32(&mut bytes, 0x20, meshes.len());
    put_u32(&mut bytes, 0x24, meshes.len());
    bytes[0x49] = 1;
    put_u32(&mut bytes, 0x50, face_set_count);
    put_u32(&mut bytes, 0x54, 1);
    put_u32(&mut bytes, 0x58, texture_paths.len());
    put_u32(&mut bytes, material_table + 0x08, texture_paths.len());

    // One layout: position at 0, byte bone indices at 12, byte weights at 16.
    put_u32(&mut bytes, layout_table, 3);
    put_u32(&mut bytes, layout_table + 0x0C, members);
    for (i, (offset, format, semantic)) in [(0, 0x02, 0), (12, 0x11, 2), (16, 0x13, 1)]
        .into_iter()
        .enumerate()
    {
        let at = members + i * 0x14;
        put_u32(&mut bytes, at + 0x04, offset);
        put_u32(&mut bytes, at + 0x08, format);
        put_u32(&mut bytes, at + 0x0C, semantic);
    }

    let mut cursor = lists;
    let mut face_set = 0;
    for (index, mesh) in meshes.iter().enumerate() {
        let entry = mesh_table + index * MESH_SIZE;
        bytes[entry] = 1;
        put_u32(&mut bytes, entry + 0x18, boxes + index * BOX_SIZE);
        put_u32(&mut bytes, entry + 0x20, mesh.face_sets.len());
        put_u32(&mut bytes, entry + 0x24, cursor);
        for _ in &mesh.face_sets {
            put_u32(&mut bytes, cursor, face_set);
            cursor += 4;
            face_set += 1;
        }
        put_u32(&mut bytes, entry + 0x28, 1);
        put_u32(&mut bytes, entry + 0x2C, cursor);
        put_u32(&mut bytes, cursor, index);
        cursor += 4;
    }

    // Strings: the material's name and MTD, its textures' paths and samplers, the bones' names.
    let mut strings = Vec::new();
    let mut named = vec![
        (material_table, "#03#"),
        (material_table + 0x04, "C[AMSN].matxml"),
        (bone_table + 0x0C, "Root"),
        (bone_table + 0x80 + 0x0C, "Spine2"),
    ];
    for (i, path) in texture_paths.iter().enumerate() {
        named.push((texture_table + i * 0x20, *path));
        named.push((texture_table + i * 0x20 + 0x04, "AlbedoMap"));
    }
    let strings_start = bytes.len();
    for (at, text) in named {
        put_u32(&mut bytes, at, strings_start + strings.len());
        strings.extend(utf16z(text));
    }
    for bone in 0..BONE_COUNT {
        let at = bone_table + bone * 0x80;
        bytes[at + 0x1C..at + 0x1E].copy_from_slice(&(bone as i16 - 1).to_le_bytes());
        for axis in 0..3 {
            bytes[at + 0x20 + axis * 4..at + 0x24 + axis * 4]
                .copy_from_slice(&1.0_f32.to_le_bytes());
        }
    }
    bytes.extend(strings);
    pad(&mut bytes);
    let data_offset = bytes.len();
    put_u32(&mut bytes, 0x0C, data_offset);

    let mut data = Vec::new();
    let (mut faces, mut faces_all, mut face_set) = (0, 0, 0);
    for (index, mesh) in meshes.iter().enumerate() {
        for &(flags, count) in &mesh.face_sets {
            pad(&mut data);
            let entry = face_set_table + face_set * FACE_SET_SIZE;
            put_u32(&mut bytes, entry, flags as usize);
            bytes[entry + 0x05] = 1;
            put_u32(&mut bytes, entry + 0x08, count);
            put_u32(&mut bytes, entry + 0x0C, data.len());
            put_u32(&mut bytes, entry + 0x10, count * 2);
            put_u32(&mut bytes, entry + 0x18, 16);
            // Distinct per face set, so a copy from the wrong place shows.
            for i in 0..count {
                let value = (i + face_set) % mesh.vertex_count;
                data.extend_from_slice(&(value as u16).to_le_bytes());
            }
            faces_all += count / 3;
            if flags & FACE_SET_MOTION_BLUR == 0 {
                faces += count / 3;
            }
            face_set += 1;
        }
        pad(&mut data);
        let entry = buffer_table + index * VERTEX_BUFFER_SIZE;
        put_u32(&mut bytes, entry + 0x08, VERTEX_SIZE);
        put_u32(&mut bytes, entry + 0x0C, mesh.vertex_count);
        put_u32(&mut bytes, entry + 0x18, mesh.vertex_count * VERTEX_SIZE);
        put_u32(&mut bytes, entry + 0x1C, data.len());
        for vertex_index in 0..mesh.vertex_count {
            data.extend_from_slice(&vertex(vertex_index));
        }
    }
    pad(&mut data);
    put_u32(&mut bytes, 0x10, data.len());
    put_u32(&mut bytes, 0x40, faces);
    put_u32(&mut bytes, 0x44, faces_all);
    bytes.extend(data);
    bytes
}
