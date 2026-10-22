//! A Dark Souls Remastered character FLVER read whole: every mesh with its own bone palette,
//! material, back-face culling and LOD triangle lists, plus the materials' MTD names and texture
//! paths.
//!
//! c2280 is one mesh on one bone table, so the exporter used to read only `meshes[0]`. A Biped
//! character is several: c2650 has five, two of them double-sided cloth on an alpha-tested
//! material, and a vertex's bone index means something only through its own mesh's palette.
//!
//! [`strip_props`] then removes a held item vertex by vertex. c2650's lantern shares meshes 1
//! and 3 with the left shin and the coat hem, so no whole-mesh rule can take it out.

use super::{
    BUFFER_LAYOUT_SIZE, DUMMY_SIZE, FACE_SET_SIZE, Flver, HEADER_SIZE, MATERIAL_SIZE,
    VERTEX_BUFFER_SIZE, Vec2, Vec3, parse_face_set_indices, parse_layout_members, parse_u32_list,
    read_vec3,
};
use crate::{
    bytes::{i16_le, u8_at, u16_le, u32_le, utf16z},
    error::{Error, Result},
    flver::{Bone, skeleton::Role},
};

const PRIMITIVE_RESTART_U16: u32 = 0xFFFF;
/// Face set flags: bits 24-25 are the LOD level, the top bit marks the motion-blur copy.
const FACE_SET_MOTION_BLUR: u32 = 0x8000_0000;
const FACE_SET_LOD_SHIFT: u32 = 24;
const FACE_SET_LOD_MASK: u32 = 0x0300_0000;
const FACE_SET_CULL_OFFSET: usize = 0x05;
const LOD_COUNT: usize = 3;
/// `textureCount` in the FLVER2 header; the texture table follows the buffer layouts.
const HEADER_TEXTURE_COUNT: usize = 0x58;
const TEXTURE_SIZE: usize = 0x20;
/// A vertex at least this much on held-item bones is part of the item.
const PROP_SHARE: f32 = 0.5;

#[derive(Clone, Copy, Debug, Default)]
pub struct DsrVertex {
    pub position: Vec3,
    pub normal: Vec3,
    pub uv: Vec2,
    /// Slots into the owning mesh's palette, not global bone indices: see [`DsrMesh::bone`].
    pub bone_indices: [u16; 4],
    pub bone_weights: [f32; 4],
}

#[derive(Clone, Debug)]
pub struct DsrMesh {
    pub material_index: usize,
    /// Whether the LOD0 face set culls back faces (or the nearest LOD's, for a mesh with no
    /// LOD0). c2650's cloth does not.
    pub cull_backfaces: bool,
    /// The global bone each vertex bone slot names. Empty when the slots are global already.
    pub palette: Vec<u32>,
    /// Every vertex of the mesh, read across all its buffers, including the ones only LOD1 and
    /// LOD2 use.
    pub vertices: Vec<DsrVertex>,
    /// LOD0, LOD1 and LOD2, strip-decoded; motion-blur copies are skipped. A LOD the mesh has no
    /// face set for is empty.
    pub lods: [Vec<[u32; 3]>; LOD_COUNT],
}

impl DsrMesh {
    /// The global bone index a vertex's bone slot stands for, through this mesh's palette.
    pub fn bone(&self, slot: u16) -> usize {
        let slot = usize::from(slot);
        self.palette.get(slot).map_or(slot, |&index| index as usize)
    }
}

#[derive(Clone, Debug, Default)]
pub struct SourceMaterial {
    pub name: String,
    /// The full MTD path, `N:\FRPG\data\Material\mtd\character\C_DullLeather[DSB].mtd`.
    pub mtd: String,
    /// `(type, path)`, as `("g_Diffuse", "N:\...\c2650.tga")`. An unused slot has an empty path.
    pub textures: Vec<(String, String)>,
}

pub struct Source {
    pub bones: Vec<Bone>,
    pub materials: Vec<SourceMaterial>,
    pub meshes: Vec<DsrMesh>,
}

impl Source {
    pub fn vertex_count(&self) -> usize {
        self.meshes.iter().map(|mesh| mesh.vertices.len()).sum()
    }

    /// Vertices some LOD0 triangle uses.
    pub fn lod0_vertex_count(&self) -> usize {
        self.meshes
            .iter()
            .map(|mesh| referenced(mesh.vertices.len(), &mesh.lods[0]).count())
            .sum()
    }

    pub fn lod0_triangle_count(&self) -> usize {
        self.meshes.iter().map(|mesh| mesh.lods[0].len()).sum()
    }

    /// Each bone's total weight over every vertex of every buffer, in vertex-equivalents.
    pub fn bone_weights(&self) -> Vec<f32> {
        let mut totals = vec![0.0; self.bones.len()];
        for mesh in &self.meshes {
            for vertex in &mesh.vertices {
                for (slot, weight) in vertex.bone_indices.iter().zip(vertex.bone_weights) {
                    if weight > 0.0
                        && let Some(total) = totals.get_mut(mesh.bone(*slot))
                    {
                        *total += weight;
                    }
                }
            }
        }
        totals
    }
}

/// Every mesh, material and bone of a DSR FLVER.
pub fn parse(bytes: &[u8]) -> Result<Source> {
    let flver = Flver::parse(bytes)?;
    let meshes = (0..flver.meshes.len())
        .map(|index| {
            parse_mesh(bytes, &flver, index).map_err(|e| e.context(format!("mesh {index}")))
        })
        .collect::<Result<_>>()?;
    Ok(Source {
        materials: parse_materials(bytes, &flver)?,
        bones: flver.bones,
        meshes,
    })
}

/// Only the materials, for a caller that needs the texture names and not the geometry.
pub fn materials(bytes: &[u8]) -> Result<Vec<SourceMaterial>> {
    parse_materials(bytes, &Flver::parse(bytes)?)
}

fn parse_mesh(bytes: &[u8], flver: &Flver, index: usize) -> Result<DsrMesh> {
    let mesh = flver.meshes[index];
    let palette = parse_u32_list(bytes, mesh.bone_offset, mesh.bone_count)?;
    // A vertex can be spread over several buffers, each with its own layout and stride: Dark
    // Souls II keeps the positions and normals of some meshes in one and their bones, weights
    // and UVs in another (c1020 mesh 6). Every member is read from whichever buffer has it, the
    // first buffer winning when two carry the same semantic.
    let vertex_buffer_indices =
        parse_u32_list(bytes, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)?;
    if vertex_buffer_indices.is_empty() {
        return Err("mesh has no vertex buffers".into());
    }
    let mut vertex_count = None;
    let mut reads = Vec::new();
    let mut taken = Vec::new();
    for &buffer_index in &vertex_buffer_indices {
        let vertex_buffer = *flver
            .vertex_buffers
            .get(buffer_index as usize)
            .ok_or("mesh vertex buffer index out of range")?;
        match vertex_count {
            Some(count) if count != vertex_buffer.vertex_count => {
                return Err(Error::new(format!(
                    "the mesh's vertex buffers disagree on its vertex count ({count} and {})",
                    vertex_buffer.vertex_count
                )));
            }
            _ => vertex_count = Some(vertex_buffer.vertex_count),
        }
        let layout = *flver
            .layouts
            .get(vertex_buffer.layout_index)
            .ok_or("vertex buffer layout index out of range")?;
        let buffer_start = flver.header.data_offset + vertex_buffer.buffer_offset;
        let buffer = crate::bytes::slice(bytes, buffer_start, vertex_buffer.buffer_length)?;
        for member in parse_layout_members(bytes, layout)? {
            let key = (member.semantic_id, member.index);
            if !taken.contains(&key) {
                taken.push(key);
                reads.push((buffer, vertex_buffer.vertex_size, member));
            }
        }
    }

    let uv_factor = flver.header.uv_factor();
    let mut vertices = vec![DsrVertex::default(); vertex_count.unwrap_or(0)];
    for (index, vertex) in vertices.iter_mut().enumerate() {
        for &(buffer, stride, ref member) in &reads {
            let off = index * stride + member.struct_offset;
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
                // Byte normals are stored biased, (byte - 127) / 127: read as signed bytes,
                // c2280's normals come out at unit length for 441 of 1108 vertices, biased for
                // all of them.
                (3, 0x10, _) | (3, 0x11, _) | (3, 0x13, _) | (3, 0x2F, _) => {
                    let unbias = |at: usize| -> Result<f32> {
                        Ok((f32::from(u8_at(buffer, at)?) - 127.0) / 127.0)
                    };
                    vertex.normal = Vec3 {
                        x: unbias(off)?,
                        y: unbias(off + 1)?,
                        z: unbias(off + 2)?,
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

    // The first plain face set of each LOD. A motion-blur set is a copy of its LOD's twin. Some
    // meshes have no LOD0 set at all and are drawn only at a distance (c2230 mesh 4, c2860 mesh
    // 2); they get an empty LOD0 and take their culling from the nearest LOD they do have.
    let mut lods: [Option<Vec<[u32; 3]>>; LOD_COUNT] = Default::default();
    let mut cull: [Option<bool>; LOD_COUNT] = [None; LOD_COUNT];
    for face_set_index in parse_u32_list(bytes, mesh.face_set_offset, mesh.face_set_count)? {
        let face_set_index = face_set_index as usize;
        let face_set = flver
            .face_sets
            .get(face_set_index)
            .ok_or_else(|| Error::new(format!("face set {face_set_index} out of range")))?;
        if face_set.flags & FACE_SET_MOTION_BLUR != 0 {
            continue;
        }
        if face_set.flags & !FACE_SET_LOD_MASK != 0 {
            return Err(Error::new(format!(
                "face set {face_set_index} has flags 0x{:08X}, which this reader does not decode",
                face_set.flags
            )));
        }
        let lod = ((face_set.flags & FACE_SET_LOD_MASK) >> FACE_SET_LOD_SHIFT) as usize;
        if lod >= LOD_COUNT || lods[lod].is_some() {
            continue;
        }
        let at = flver.face_set_table_offset(face_set_index) + FACE_SET_CULL_OFFSET;
        cull[lod] = Some(u8_at(bytes, at)? != 0);
        let indices = parse_face_set_indices(bytes, flver, face_set)?;
        lods[lod] = Some(triangulate(face_set.triangle_strip, &indices));
    }
    Ok(DsrMesh {
        material_index: mesh.material_index as usize,
        cull_backfaces: cull.into_iter().flatten().next().unwrap_or(true),
        palette,
        vertices,
        lods: lods.map(Option::unwrap_or_default),
    })
}

fn parse_materials(bytes: &[u8], flver: &Flver) -> Result<Vec<SourceMaterial>> {
    let header = flver.header;
    let material_table = HEADER_SIZE + DUMMY_SIZE * header.dummy_count;
    let layout_table = flver.face_set_table
        + FACE_SET_SIZE * header.face_set_count
        + VERTEX_BUFFER_SIZE * header.vertex_buffer_count;
    let texture_table = layout_table + BUFFER_LAYOUT_SIZE * header.buffer_layout_count;
    let texture_count = u32_le(bytes, HEADER_TEXTURE_COUNT)? as usize;
    let string = |offset: u32| -> Result<String> {
        let offset = offset as usize;
        if offset == 0 || offset >= bytes.len() {
            Ok(String::new())
        } else {
            utf16z(bytes, offset)
        }
    };
    (0..header.material_count)
        .map(|index| {
            let at = material_table + index * MATERIAL_SIZE;
            let first = u32_le(bytes, at + 0x0C)? as usize;
            let count = u32_le(bytes, at + 0x08)? as usize;
            if first + count > texture_count {
                return Err(Error::new(format!(
                    "material {index} names textures {first}..{} of {texture_count}",
                    first + count
                )));
            }
            let textures = (first..first + count)
                .map(|texture| {
                    let entry = texture_table + texture * TEXTURE_SIZE;
                    Ok((
                        string(u32_le(bytes, entry + 4)?)?,
                        string(u32_le(bytes, entry)?)?,
                    ))
                })
                .collect::<Result<_>>()?;
            Ok(SourceMaterial {
                name: string(u32_le(bytes, at)?)?,
                mtd: string(u32_le(bytes, at + 4)?)?,
                textures,
            })
        })
        .collect()
}

/// Strip or list-decode a face set's indices into triangles, dropping degenerate ones.
pub fn triangulate(triangle_strip: bool, indices: &[u32]) -> Vec<[u32; 3]> {
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

/// Indices below `count` that some triangle uses, in order.
fn referenced(count: usize, triangles: &[[u32; 3]]) -> impl Iterator<Item = usize> {
    let mut used = vec![false; count];
    for corner in triangles.iter().flatten() {
        if let Some(flag) = used.get_mut(*corner as usize) {
            *flag = true;
        }
    }
    used.into_iter()
        .enumerate()
        .filter_map(|(index, used)| used.then_some(index))
}

/// One source mesh after the held items are gone: only what LOD0 still draws.
#[derive(Clone, Debug)]
pub struct KeptMesh {
    /// Which mesh of the source it came from.
    pub source_mesh: usize,
    pub material_index: usize,
    pub cull_backfaces: bool,
    pub vertices: Vec<DsrVertex>,
    /// For each kept vertex, its index in the source mesh's buffer.
    pub source_vertex: Vec<usize>,
    /// LOD0 triangles, indexing `vertices`.
    pub triangles: Vec<[u32; 3]>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct StripReport {
    /// Vertices at least half on held-item bones, across every buffer.
    pub stripped_vertices: usize,
    /// LOD0 triangles that touched one of them.
    pub dropped_triangles: usize,
    /// Vertices neither stripped nor used by a surviving LOD0 triangle (LOD1/LOD2-only ones).
    pub unreferenced_vertices: usize,
    pub kept_vertices: usize,
    pub kept_triangles: usize,
}

/// Remove every vertex with at least half its weight on [`Role::Prop`] bones and every LOD0
/// triangle touching one, then keep only the vertices the remaining LOD0 triangles use, with the
/// indices remapped. A mesh with nothing left is dropped.
pub fn strip_props(source: &Source, roles: &[Role]) -> (Vec<KeptMesh>, StripReport) {
    let mut report = StripReport::default();
    let mut kept = Vec::new();
    for (mesh_index, mesh) in source.meshes.iter().enumerate() {
        let is_prop: Vec<bool> = mesh
            .vertices
            .iter()
            .map(|vertex| {
                let (mut prop, mut total) = (0.0, 0.0);
                for (slot, weight) in vertex.bone_indices.iter().zip(vertex.bone_weights) {
                    if weight <= 0.0 {
                        continue;
                    }
                    total += weight;
                    if matches!(roles.get(mesh.bone(*slot)), Some(Role::Prop)) {
                        prop += weight;
                    }
                }
                total > 0.0 && prop >= PROP_SHARE * total
            })
            .collect();
        report.stripped_vertices += is_prop.iter().filter(|&&prop| prop).count();
        let triangles: Vec<[u32; 3]> = mesh.lods[0]
            .iter()
            .copied()
            .filter(|triangle| !triangle.iter().any(|&corner| is_prop[corner as usize]))
            .collect();
        report.dropped_triangles += mesh.lods[0].len() - triangles.len();
        let source_vertex: Vec<usize> = referenced(mesh.vertices.len(), &triangles).collect();
        report.unreferenced_vertices +=
            is_prop.iter().filter(|&&prop| !prop).count() - source_vertex.len();
        if triangles.is_empty() {
            continue;
        }
        let mut remap = vec![u32::MAX; mesh.vertices.len()];
        for (new, &old) in source_vertex.iter().enumerate() {
            remap[old] = new as u32;
        }
        report.kept_vertices += source_vertex.len();
        report.kept_triangles += triangles.len();
        kept.push(KeptMesh {
            source_mesh: mesh_index,
            material_index: mesh.material_index,
            cull_backfaces: mesh.cull_backfaces,
            vertices: source_vertex
                .iter()
                .map(|&old| mesh.vertices[old])
                .collect(),
            triangles: triangles
                .iter()
                .map(|triangle| triangle.map(|corner| remap[corner as usize]))
                .collect(),
            source_vertex,
        });
    }
    (kept, report)
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

    fn vertex(slots: [u16; 4], weights: [f32; 4]) -> DsrVertex {
        DsrVertex {
            bone_indices: slots,
            bone_weights: weights,
            ..DsrVertex::default()
        }
    }

    #[test]
    fn a_held_item_is_cut_vertex_by_vertex_and_the_rest_compacted() {
        // Global bones: 0 a hand, 1 a weapon. The mesh's palette swaps them, so slot 0 is the
        // weapon: the palette has to be read for the strip to find it.
        let roles = [Role::Direct("R_Hand".to_owned()), Role::Prop];
        let body = vertex([1, 0, 0, 0], [1.0, 0.0, 0.0, 0.0]);
        let item = vertex([0, 1, 0, 0], [0.6, 0.4, 0.0, 0.0]);
        let half = vertex([0, 1, 0, 0], [0.4, 0.6, 0.0, 0.0]);
        let mesh = DsrMesh {
            material_index: 2,
            cull_backfaces: false,
            palette: vec![1, 0],
            // 0..3 body, 3 item, 4 body used only by LOD1, 5 a vertex barely on the hand.
            vertices: vec![body, body, body, item, body, half],
            lods: [
                vec![[0, 1, 2], [1, 2, 3], [2, 0, 5]],
                vec![[0, 1, 4]],
                Vec::new(),
            ],
        };
        let source = Source {
            bones: Vec::new(),
            materials: Vec::new(),
            meshes: vec![
                mesh.clone(),
                DsrMesh {
                    vertices: vec![item; 3],
                    lods: [vec![[0, 1, 2]], Vec::new(), Vec::new()],
                    ..mesh
                },
            ],
        };
        let (kept, report) = strip_props(&source, &roles);
        assert_eq!(
            report,
            StripReport {
                stripped_vertices: 1 + 3,
                dropped_triangles: 1 + 1,
                unreferenced_vertices: 1,
                kept_vertices: 4,
                kept_triangles: 2,
            }
        );
        assert_eq!(kept.len(), 1, "a mesh of nothing but the item is dropped");
        assert_eq!(kept[0].source_vertex, [0, 1, 2, 5]);
        assert_eq!(kept[0].triangles, [[0, 1, 2], [2, 0, 3]]);
        assert_eq!(kept[0].material_index, 2);
        assert!(!kept[0].cull_backfaces);
    }

    #[test]
    fn a_vertex_slot_is_read_through_its_own_mesh_palette() {
        let mesh = DsrMesh {
            material_index: 0,
            cull_backfaces: true,
            palette: vec![7, 3],
            vertices: Vec::new(),
            lods: Default::default(),
        };
        assert_eq!(mesh.bone(0), 7);
        assert_eq!(mesh.bone(1), 3);
        // Past the palette the slot is taken as global, as the c2280 exporter always read it.
        assert_eq!(mesh.bone(5), 5);
    }
}
