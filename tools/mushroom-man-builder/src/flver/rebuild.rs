//! An Elden Ring FLVER whose slot meshes can be any size: the donor's header region kept byte
//! for byte, and its data section -- every face set's indices and every vertex buffer -- laid
//! out again behind it.
//!
//! # Why the data section alone
//!
//! In a `0x2001A` FLVER only the face set and vertex buffer entries point into the data
//! section, as offsets from `dataOffset`; every other table, string and box sits before it. So a
//! mesh can grow, and every buffer after it move, by rewriting those entries' counts, lengths
//! and offsets and nothing else. Andre.SoulsFormats' `FLVER2` writer, which reproduces From's
//! file byte for byte apart from header 0x40, changes exactly those fields when it grows mesh 13
//! (`docs/recon/dsr-character-builder/andre-oracle-results.txt`), and this writer reproduces its
//! sizes and offsets (`tests/rebuild_corpus.rs`). Set beside Andre's grown file itself, the
//! output differs only where this writer sets a field on purpose: the header's box and face
//! count, and the grown mesh's material and box.
//!
//! # The layout
//!
//! Measured on `FC_M_0000.flver` and on Andre's grown copy, with no mismatch on either: mesh by
//! mesh in table order, each face set of the mesh (in the order its list names them) starts at
//! the next 16-byte boundary, then the mesh's vertex buffer does, and the section ends padded to
//! 16, the pad counted in `dataLength`. Every face set has its own range: the donor has 242
//! distinct index offsets, and 8 of its 121 motion-blur sets differ from their LOD twin, so a
//! motion-blur set gets its own copy of its twin's list rather than a shared one.
//!
//! # What is written
//!
//! A [`Slot`] replaces one donor mesh: its vertices, its triangles at each distance, its
//! material and back-face culling, and a real oriented box. A mesh with no slot keeps its own
//! bytes, or with `hide_unslotted` keeps their length but zeroes them, as `hide.rs` does, so the
//! engine never sees a face set with no indices. The header's data length, bounding box and two
//! face counts are recomputed; header 0x40 follows From (faces outside motion-blur sets, 45821 on
//! the donor), not Andre, which writes the total there too.
//!
//! # What the header region gains
//!
//! A [`HeaderAppend`] adds strings and GX lists to the header region and points donor material
//! entries at them ([`HeaderAppend::retarget_material`]), so a slot can draw with a material
//! the donor does not have without any table growing. The header region ends with its strings
//! (measured on `FC_M_0000.flver` and `BD_F_1451.flver`: every table, list and box sits before
//! the first string, and only the zeros up to the next 16-byte boundary follow the last), so new
//! bytes go after the last string and `dataOffset` moves to the 16-byte boundary after them.
//! Every face set and vertex buffer offset counts from `dataOffset`, so all of them stay valid
//! as written; every other header offset points before the strings and does not move.

use super::{
    BONE_SIZE, Flver, LayoutMember, MATERIAL_SIZE, TEXTURE_SIZE, Texture, Vec3, VertexBuffer,
    index_range, parse_layout_members, parse_materials, parse_textures, parse_u32_list,
    patch::{ELDEN_RING_FLVER_VERSION, put, write_u32, write_vec3},
};
use crate::{
    bytes::{i32_le, slice, u8_at, u16_le, u32_le},
    error::{Context, Error, Result},
};

const DATA_ALIGNMENT: usize = 16;
const HEADER_DATA_OFFSET: usize = 0x0C;
const HEADER_DATA_LENGTH: usize = 0x10;
const HEADER_BBOX_MIN: usize = 0x28;
const HEADER_BBOX_MAX: usize = 0x34;
/// Faces in face sets that are not motion-blur copies.
const HEADER_FACE_COUNT: usize = 0x40;
/// Faces in every face set.
const HEADER_FACE_COUNT_ALL: usize = 0x44;
const HEADER_VERTEX_INDEX_SIZE: usize = 0x48;
/// Nonzero when the header's strings are UTF-16.
const HEADER_UNICODE: usize = 0x49;
const MATERIAL_NAME: usize = 0x00;
const MATERIAL_MTD: usize = 0x04;
const MATERIAL_FLAGS: usize = 0x10;
const MATERIAL_GX_LIST: usize = 0x14;
/// The index of the first material drawn with the same MTD: 0 on each of `FC_M_0000`'s 15 Body
/// materials, 15 and 29 on its two hair groups, 2 on `BD_F_1451`'s three Leather materials.
/// Andre.SoulsFormats calls it `Index`.
const MATERIAL_FIRST_OF_MTD: usize = 0x18;
const TEXTURE_PATH: usize = 0x00;
const TEXTURE_SAMPLER: usize = 0x04;
/// A texture's U and V tiling modes, one byte each and then two zero bytes, written as one word.
/// Every texture entry with a path repeats in both (1 and 1, Andre.SoulsFormats' `Repeat`) and
/// every one without has none (0 and 0): 976 and 218704 entries in the extracted corpus's 3070
/// parts FLVERs, with no exception either way.
const TEXTURE_TILING: usize = 0x10;
const TILING_REPEAT: u32 = 0x0101;
const BONE_NAME: usize = 0x0C;
/// A GX item: its id, a version, its length including these 12 bytes, then its data.
const GX_ITEM_HEADER: usize = 12;
const GX_ITEM_LENGTH: usize = 0x08;
/// The ids that end a GX list.
const GX_TERMINATORS: [i32; 2] = [i32::MAX, -1];
/// A GX list is 32-bit words; the donor's second one starts on a 4-byte boundary (0x150F4).
const GX_ALIGNMENT: usize = 4;
const UTF16_ALIGNMENT: usize = 2;
const MESH_MATERIAL: usize = 0x04;
const FACE_SET_CULL: usize = 0x05;
const FACE_SET_INDEX_COUNT: usize = 0x08;
const FACE_SET_INDEX_OFFSET: usize = 0x0C;
const FACE_SET_BYTE_LENGTH: usize = 0x10;
const VERTEX_BUFFER_LAYOUT: usize = 0x04;
const VERTEX_BUFFER_VERTEX_SIZE: usize = 0x08;
const VERTEX_BUFFER_COUNT: usize = 0x0C;
const VERTEX_BUFFER_LENGTH: usize = 0x18;
const VERTEX_BUFFER_OFFSET: usize = 0x1C;
const BOX_ROTATION: usize = 0x0C;
const BOX_CENTRE: usize = 0x18;
/// A slot's indices are 16-bit.
const SLOT_INDEX_SIZE: u32 = 16;
const MAX_SLOT_VERTICES: usize = u16::MAX as usize;
const BONE_INDICES_SEMANTIC: u32 = 2;

/// A box with no rotation, written into a mesh's oriented-box slot (half-extents, rotation,
/// centre: Elden Ring's 0x24 bytes at the mesh's `boundingBoxOffset`) and into the header's
/// axis-aligned one.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Obb {
    pub min: Vec3,
    pub max: Vec3,
}

impl Obb {
    /// The box around `positions`, or `None` when there are none.
    pub fn around(positions: impl IntoIterator<Item = Vec3>) -> Option<Self> {
        positions.into_iter().fold(None, |bounds, p| {
            let Self { min, max } = bounds.unwrap_or(Self { min: p, max: p });
            Some(Self {
                min: Vec3 {
                    x: min.x.min(p.x),
                    y: min.y.min(p.y),
                    z: min.z.min(p.z),
                },
                max: Vec3 {
                    x: max.x.max(p.x),
                    y: max.y.max(p.y),
                    z: max.z.max(p.z),
                },
            })
        })
    }

    pub fn half_extents(&self) -> Vec3 {
        Vec3 {
            x: (self.max.x - self.min.x) * 0.5,
            y: (self.max.y - self.min.y) * 0.5,
            z: (self.max.z - self.min.z) * 0.5,
        }
    }

    pub fn centre(&self) -> Vec3 {
        Vec3 {
            x: (self.max.x + self.min.x) * 0.5,
            y: (self.max.y + self.min.y) * 0.5,
            z: (self.max.z + self.min.z) * 0.5,
        }
    }

    fn union(self, other: Self) -> Self {
        Self::around([self.min, self.max, other.min, other.max]).unwrap_or(self)
    }
}

/// One donor mesh replaced: what it draws, and how.
#[derive(Clone, Debug)]
pub struct Slot {
    /// Which mesh of the donor this replaces.
    pub donor_mesh: usize,
    /// The donor material index the mesh draws with.
    pub material: u32,
    pub cull_backfaces: bool,
    pub vertex_count: usize,
    /// `vertex_count` vertices, already encoded in [`Slot::layout`], or else in the layout of
    /// the donor mesh's buffer.
    pub vertex_bytes: Vec<u8>,
    /// Another of the donor's vertex layouts for this mesh's buffer, written into its entry.
    pub layout: Option<SlotLayout>,
    /// The triangles each distance draws, LOD0 to LOD2. A face set draws the list of its own
    /// distance; its motion-blur copy gets a copy of the same list.
    pub lods: [Vec<[u32; 3]>; 3],
    pub obb: Obb,
}

/// One of the donor's buffer layouts and the vertex size buffers in it have.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SlotLayout {
    pub layout_index: usize,
    pub vertex_size: usize,
}

impl Slot {
    /// The layout and vertex size this slot's vertices are in, given its donor mesh's buffer.
    fn layout_or(&self, buffer: &VertexBuffer) -> SlotLayout {
        self.layout.unwrap_or(SlotLayout {
            layout_index: buffer.layout_index,
            vertex_size: buffer.vertex_size,
        })
    }
}

/// What the header region gains (see the module docs): bytes after the donor's last string, and
/// the donor's material and texture entries they are pointed at. An empty append, which is what
/// [`HeaderAppend::default`] is, leaves the header region as the donor has it.
#[derive(Clone, Debug, Default)]
pub struct HeaderAppend {
    /// Where the appended bytes start: the end of the donor's last string. Zero for an append
    /// made with `default()`, which has no donor and so can hold nothing.
    start: usize,
    bytes: Vec<u8>,
    /// Words of the donor's header region to overwrite, in order.
    fields: Vec<(usize, u32)>,
    material_table: usize,
    texture_table: usize,
    materials: Vec<MaterialEntry>,
}

/// What retargeting needs to know of one donor material, as the append has left it.
#[derive(Clone, Debug)]
struct MaterialEntry {
    mtd: String,
    texture_index: usize,
    texture_count: usize,
    retargeted: bool,
}

impl HeaderAppend {
    /// An empty append onto `donor`'s header region, which must end with its strings.
    pub fn for_donor(donor: &[u8]) -> Result<Self> {
        let flver = Flver::parse(donor)?;
        if u8_at(donor, HEADER_UNICODE)? == 0 {
            return Err(
                "the donor's header strings are single-byte; only UTF-16 is appended".into(),
            );
        }
        let start = strings_end(donor, &flver)?;
        let data_offset = flver.header.data_offset;
        let ends_with_strings = start > 0
            && start <= data_offset
            && slice(donor, start, data_offset - start)
                .is_ok_and(|tail| tail.iter().all(|&byte| byte == 0));
        if !ends_with_strings {
            return Err(Error::new(format!(
                "the donor's header region does not end with its strings (the last ends at \
                 0x{start:X}, the data section starts at 0x{data_offset:X})"
            )));
        }
        let materials = parse_materials(donor, &flver)?
            .into_iter()
            .map(|material| MaterialEntry {
                mtd: material.mtd,
                texture_index: material.texture_index,
                texture_count: material.texture_count,
                retargeted: false,
            })
            .collect();
        Ok(Self {
            start,
            bytes: Vec::new(),
            fields: Vec::new(),
            material_table: flver.material_table,
            texture_table: flver.texture_table,
            materials,
        })
    }

    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty() && self.fields.is_empty()
    }

    /// Append `text` as a NUL-terminated UTF-16 string, returning the file offset it sits at.
    pub fn append_utf16(&mut self, text: &str) -> Result<u32> {
        let bytes: Vec<u8> = text
            .encode_utf16()
            .chain([0])
            .flat_map(u16::to_le_bytes)
            .collect();
        self.append_bytes(&bytes, UTF16_ALIGNMENT)
    }

    /// Append `bytes` at the next multiple of `align` (a power of two), returning the file offset
    /// they sit at.
    pub fn append_bytes(&mut self, bytes: &[u8], align: usize) -> Result<u32> {
        if self.start == 0 {
            return Err(
                "this header append has no donor; make it with HeaderAppend::for_donor".into(),
            );
        }
        if !align.is_power_of_two() {
            return Err(Error::new(format!(
                "alignment {align} is not a power of two"
            )));
        }
        let at = (self.start + self.bytes.len()).next_multiple_of(align);
        self.bytes.resize(at - self.start, 0);
        self.bytes.extend_from_slice(bytes);
        Ok(u32::try_from(at)?)
    }

    /// Point donor material `index` at another matxml: `name`, `mtd`, `flags` and its own copy
    /// of `gx` (one GX list, its terminator included), with its first texture entries naming
    /// `textures`, each tiled the way vanilla tiles a texture with a path or one without. The
    /// material keeps its place in the table and its run of texture entries, and is grouped
    /// with the first material that shares its new MTD (itself, unless an earlier one does).
    ///
    /// Its texture count stays the donor's. SoulsFormats refuses a FLVER with a texture entry no
    /// material owns ("Orphaned textures found", measured with Andre on `FC_M_0000.flver` with
    /// material 14's count cut to 3), so the entries past `textures` stay this material's as the
    /// donor has them: their paths are empty, which leaves a sampler to the matbin, and they name
    /// samplers the new matbin does not have, so there is nothing for them to set.
    pub fn retarget_material(
        &mut self,
        index: usize,
        name: &str,
        mtd: &str,
        textures: &[Texture],
        gx: &[u8],
        flags: u32,
    ) -> Result<()> {
        if self.start == 0 {
            return Err(
                "this header append has no donor; make it with HeaderAppend::for_donor".into(),
            );
        }
        let count = self.materials.len();
        let material = self.materials.get(index).ok_or_else(|| {
            Error::new(format!("material {index}: the donor has {count} materials"))
        })?;
        if material.retargeted {
            return Err(Error::new(format!("material {index} is retargeted twice")));
        }
        if textures.len() > material.texture_count {
            return Err(Error::new(format!(
                "material {index} has {} texture entries; {} were asked for",
                material.texture_count,
                textures.len()
            )));
        }
        let gx_length = gx_list_len(gx, 0).context("the GX list")?;
        if gx_length != gx.len() {
            return Err(Error::new(format!(
                "the GX list ends at byte {gx_length} of {}",
                gx.len()
            )));
        }
        let texture_index = material.texture_index;

        let entry = self.material_table + index * MATERIAL_SIZE;
        let name_at = self.append_utf16(name)?;
        let mtd_at = self.append_utf16(mtd)?;
        let gx_at = self.append_bytes(gx, GX_ALIGNMENT)?;
        self.fields.extend([
            (entry + MATERIAL_NAME, name_at),
            (entry + MATERIAL_MTD, mtd_at),
            (entry + MATERIAL_FLAGS, flags),
            (entry + MATERIAL_GX_LIST, gx_at),
        ]);
        for (offset, texture) in textures.iter().enumerate() {
            let entry = self.texture_table + (texture_index + offset) * TEXTURE_SIZE;
            let path_at = self.append_utf16(&texture.path)?;
            let sampler_at = self.append_utf16(&texture.sampler)?;
            let tiling = if texture.path.is_empty() {
                0
            } else {
                TILING_REPEAT
            };
            self.fields.extend([
                (entry + TEXTURE_PATH, path_at),
                (entry + TEXTURE_SAMPLER, sampler_at),
                (entry + TEXTURE_TILING, tiling),
            ]);
        }
        let material = &mut self.materials[index];
        material.mtd = mtd.to_owned();
        material.retargeted = true;
        Ok(())
    }

    /// The donor's header region with this append in it, up to the 16-byte boundary the data
    /// section now starts on.
    fn header_region(&self, donor: &[u8], flver: &Flver) -> Result<Vec<u8>> {
        if self.is_empty() {
            return Ok(donor[..flver.header.data_offset].to_vec());
        }
        if strings_end(donor, flver)? != self.start
            || self.materials.len() != flver.header.material_count
        {
            return Err("the header append was made for a different donor".into());
        }
        let mut out = donor[..self.start].to_vec();
        out.extend_from_slice(&self.bytes);
        pad(&mut out);
        let data_offset = u32::try_from(out.len())?;
        write_u32(&mut out, HEADER_DATA_OFFSET, data_offset)?;
        for &(at, value) in &self.fields {
            write_u32(&mut out, at, value)?;
        }
        // Grouped once every retarget is known, so a later one onto the same MTD at a lower
        // index regroups an earlier one.
        for (index, material) in self.materials.iter().enumerate() {
            if material.retargeted {
                let first = self
                    .materials
                    .iter()
                    .position(|other| other.mtd.eq_ignore_ascii_case(&material.mtd))
                    .unwrap_or(index);
                write_u32(
                    &mut out,
                    self.material_table + index * MATERIAL_SIZE + MATERIAL_FIRST_OF_MTD,
                    u32::try_from(first)?,
                )?;
            }
        }
        Ok(out)
    }
}

/// A vanilla material to retarget a donor material after: what its entry carries besides its
/// name and texture paths.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MaterialTemplate {
    pub mtd: String,
    pub flags: u32,
    /// Its GX list as the file stores it, terminator included.
    pub gx: Vec<u8>,
    /// The sampler each of its texture entries names, in order.
    pub samplers: Vec<String>,
}

/// Material `index` of an Elden Ring FLVER, as a [`MaterialTemplate`].
pub fn material_template(bytes: &[u8], index: usize) -> Result<MaterialTemplate> {
    let flver = Flver::parse(bytes)?;
    if flver.header.version != ELDEN_RING_FLVER_VERSION {
        return Err(Error::new(format!(
            "expected an Elden Ring FLVER 0x{ELDEN_RING_FLVER_VERSION:X}, got 0x{:X}",
            flver.header.version
        )));
    }
    let materials = parse_materials(bytes, &flver)?;
    let material = materials.get(index).ok_or_else(|| {
        Error::new(format!(
            "material {index}: the FLVER has {} materials",
            materials.len()
        ))
    })?;
    let entry = flver.material_table + index * MATERIAL_SIZE;
    let gx_at = u32_le(bytes, entry + MATERIAL_GX_LIST)? as usize;
    if gx_at == 0 {
        return Err(Error::new(format!("material {index} has no GX list")));
    }
    let textures = parse_textures(bytes, &flver)?;
    let samplers = textures
        .get(material.texture_index..material.texture_index + material.texture_count)
        .ok_or_else(|| Error::new(format!("material {index}: textures out of range")))?
        .iter()
        .map(|texture| texture.sampler.clone())
        .collect();
    Ok(MaterialTemplate {
        mtd: material.mtd.clone(),
        flags: u32_le(bytes, entry + MATERIAL_FLAGS)?,
        gx: slice(bytes, gx_at, gx_list_len(bytes, gx_at)?)?.to_vec(),
        samplers,
    })
}

/// The length of the GX list at `offset`: its items up to and including the terminator.
fn gx_list_len(bytes: &[u8], offset: usize) -> Result<usize> {
    let mut at = offset;
    loop {
        let id = i32_le(bytes, at)?;
        let length = u32_le(bytes, at + GX_ITEM_LENGTH)? as usize;
        if length < GX_ITEM_HEADER {
            return Err(Error::new(format!(
                "GX item at 0x{at:X} is {length} bytes long, shorter than its header"
            )));
        }
        slice(bytes, at, length)?;
        at += length;
        if GX_TERMINATORS.contains(&id) {
            return Ok(at - offset);
        }
    }
}

/// Where the header's strings end: past the terminator of whichever one ends last. Material
/// names and MTDs, texture paths and samplers, and bone names are every string a `0x2001A`
/// FLVER has.
fn strings_end(bytes: &[u8], flver: &Flver) -> Result<usize> {
    let header = &flver.header;
    let bone_table = flver.material_table + MATERIAL_SIZE * header.material_count;
    let materials = (0..header.material_count).flat_map(|index| {
        let entry = flver.material_table + index * MATERIAL_SIZE;
        [entry + MATERIAL_NAME, entry + MATERIAL_MTD]
    });
    let textures = (0..header.texture_count).flat_map(|index| {
        let entry = flver.texture_table + index * TEXTURE_SIZE;
        [entry + TEXTURE_PATH, entry + TEXTURE_SAMPLER]
    });
    let bones = (0..header.bone_count).map(|index| bone_table + index * BONE_SIZE + BONE_NAME);
    let mut end = 0;
    for field in materials.chain(textures).chain(bones) {
        let at = u32_le(bytes, field)? as usize;
        // A name offset past the end is an unnamed bone, as `parse_bones` reads it.
        if at == 0 || at >= bytes.len() {
            continue;
        }
        let mut cursor = at;
        while u16_le(bytes, cursor)? != 0 {
            cursor += 2;
        }
        end = end.max(cursor + 2);
    }
    Ok(end)
}

/// `donor` with every slot's mesh replaced and its data section laid out again, behind its
/// header region with `append` in it (see the module docs). With no slots, `hide_unslotted` off
/// and an empty append, the result is the donor, byte for byte.
pub fn rebuild(
    donor: &[u8],
    slots: &[Slot],
    hide_unslotted: bool,
    append: &HeaderAppend,
) -> Result<Vec<u8>> {
    let flver = Flver::parse(donor)?;
    check_donor(donor, &flver)?;
    for slot in slots {
        check_slot(donor, &flver, slot, slots)?;
    }

    let data_offset = flver.header.data_offset;
    let mut out = append.header_region(donor, &flver)?;
    let mut data = Vec::with_capacity(donor.len() - data_offset);
    let mut face_sets_seen = vec![false; flver.face_sets.len()];
    let mut buffers_seen = vec![false; flver.vertex_buffers.len()];
    let (mut faces, mut faces_all) = (0_usize, 0_usize);

    for (mesh_index, mesh) in flver.meshes.iter().enumerate() {
        let slot = slots.iter().find(|slot| slot.donor_mesh == mesh_index);
        for face_set_index in parse_u32_list(donor, mesh.face_set_offset, mesh.face_set_count)? {
            let face_set_index = face_set_index as usize;
            let face_set = *flver.face_sets.get(face_set_index).ok_or_else(|| {
                Error::new(format!(
                    "mesh {mesh_index} names missing face set {face_set_index}"
                ))
            })?;
            claim(&mut face_sets_seen, face_set_index, "face set")?;
            pad(&mut data);
            let offset = data.len();
            let index_count = match slot {
                Some(slot) => {
                    let triangles = &slot.lods[face_set.lod()];
                    for index in triangles.iter().flatten() {
                        data.extend_from_slice(&u16::try_from(*index)?.to_le_bytes());
                    }
                    triangles.len() * 3
                }
                None => {
                    let range = index_range(donor, &flver, &face_set)?;
                    if hide_unslotted {
                        data.resize(data.len() + range.len(), 0);
                    } else {
                        data.extend_from_slice(&donor[range]);
                    }
                    face_set.index_count
                }
            };
            let entry = flver.face_set_table_offset(face_set_index);
            write_u32(
                &mut out,
                entry + FACE_SET_INDEX_COUNT,
                u32::try_from(index_count)?,
            )?;
            write_u32(
                &mut out,
                entry + FACE_SET_INDEX_OFFSET,
                u32::try_from(offset)?,
            )?;
            write_u32(
                &mut out,
                entry + FACE_SET_BYTE_LENGTH,
                u32::try_from(data.len() - offset)?,
            )?;
            if let Some(slot) = slot {
                put(
                    &mut out,
                    entry + FACE_SET_CULL,
                    &[u8::from(slot.cull_backfaces)],
                )?;
            }
            faces_all += index_count / 3;
            if !face_set.is_motion_blur() {
                faces += index_count / 3;
            }
        }

        for buffer_index in
            parse_u32_list(donor, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)?
        {
            let buffer_index = buffer_index as usize;
            let buffer = *flver.vertex_buffers.get(buffer_index).ok_or_else(|| {
                Error::new(format!(
                    "mesh {mesh_index} names missing vertex buffer {buffer_index}"
                ))
            })?;
            claim(&mut buffers_seen, buffer_index, "vertex buffer")?;
            pad(&mut data);
            let offset = data.len();
            let vertex_count = match slot {
                Some(slot) => {
                    data.extend_from_slice(&slot.vertex_bytes);
                    slot.vertex_count
                }
                None => {
                    let start = data_offset + buffer.buffer_offset;
                    data.extend_from_slice(crate::bytes::slice(
                        donor,
                        start,
                        buffer.buffer_length,
                    )?);
                    buffer.vertex_count
                }
            };
            let entry = flver.vertex_buffer_table_offset(buffer_index);
            if let Some(layout) = slot.and_then(|slot| slot.layout) {
                write_u32(
                    &mut out,
                    entry + VERTEX_BUFFER_LAYOUT,
                    u32::try_from(layout.layout_index)?,
                )?;
                write_u32(
                    &mut out,
                    entry + VERTEX_BUFFER_VERTEX_SIZE,
                    u32::try_from(layout.vertex_size)?,
                )?;
            }
            write_u32(
                &mut out,
                entry + VERTEX_BUFFER_COUNT,
                u32::try_from(vertex_count)?,
            )?;
            write_u32(
                &mut out,
                entry + VERTEX_BUFFER_LENGTH,
                u32::try_from(data.len() - offset)?,
            )?;
            write_u32(
                &mut out,
                entry + VERTEX_BUFFER_OFFSET,
                u32::try_from(offset)?,
            )?;
        }

        if let Some(slot) = slot {
            write_u32(
                &mut out,
                flver.mesh_table_offset(mesh_index) + MESH_MATERIAL,
                slot.material,
            )?;
            if mesh.bounding_box_offset != 0 {
                let at = mesh.bounding_box_offset;
                write_vec3(&mut out, at, slot.obb.half_extents())?;
                write_vec3(&mut out, at + BOX_ROTATION, Vec3::default())?;
                write_vec3(&mut out, at + BOX_CENTRE, slot.obb.centre())?;
            }
        }
    }
    if let Some(index) = face_sets_seen.iter().position(|seen| !seen) {
        return Err(Error::new(format!("face set {index} belongs to no mesh")));
    }
    if let Some(index) = buffers_seen.iter().position(|seen| !seen) {
        return Err(Error::new(format!(
            "vertex buffer {index} belongs to no mesh"
        )));
    }
    pad(&mut data);

    write_u32(&mut out, HEADER_DATA_LENGTH, u32::try_from(data.len())?)?;
    if let Some(bounds) = slots.iter().map(|slot| slot.obb).reduce(Obb::union) {
        write_vec3(&mut out, HEADER_BBOX_MIN, bounds.min)?;
        write_vec3(&mut out, HEADER_BBOX_MAX, bounds.max)?;
    }
    write_u32(&mut out, HEADER_FACE_COUNT, u32::try_from(faces)?)?;
    write_u32(&mut out, HEADER_FACE_COUNT_ALL, u32::try_from(faces_all)?)?;
    out.extend_from_slice(&data);
    Ok(out)
}

/// The donor shapes this writer knows: an Elden Ring player-part FLVER whose data section starts
/// on 16 and ends the file, with index sizes recorded per face set.
fn check_donor(donor: &[u8], flver: &Flver) -> Result<()> {
    let header = &flver.header;
    if header.version != ELDEN_RING_FLVER_VERSION {
        return Err(Error::new(format!(
            "expected an Elden Ring FLVER 0x{ELDEN_RING_FLVER_VERSION:X}, got 0x{:X}",
            header.version
        )));
    }
    if u8_at(donor, HEADER_VERTEX_INDEX_SIZE)? != 0 {
        return Err(
            "the donor records one index size for the whole file; expected one per face set".into(),
        );
    }
    if !header.data_offset.is_multiple_of(DATA_ALIGNMENT)
        || header.data_offset.checked_add(header.data_length) != Some(donor.len())
    {
        return Err(Error::new(format!(
            "the donor's data section (0x{:X} + 0x{:X}) does not start on 16 and end the file (0x{:X})",
            header.data_offset,
            header.data_length,
            donor.len()
        )));
    }
    Ok(())
}

fn check_slot(donor: &[u8], flver: &Flver, slot: &Slot, slots: &[Slot]) -> Result<()> {
    let at = slot.donor_mesh;
    let mesh = flver
        .meshes
        .get(at)
        .ok_or_else(|| Error::new(format!("slot for mesh {at}: the donor has no such mesh")))?;
    if slots.iter().filter(|other| other.donor_mesh == at).count() > 1 {
        return Err(Error::new(format!("two slots replace mesh {at}")));
    }
    if slot.vertex_count > MAX_SLOT_VERTICES {
        return Err(Error::new(format!(
            "slot for mesh {at}: {} vertices do not fit 16-bit indices",
            slot.vertex_count
        )));
    }
    let buffers = parse_u32_list(donor, mesh.vertex_buffer_offset, mesh.vertex_buffer_count)?;
    let [buffer_index] = buffers.as_slice() else {
        return Err(Error::new(format!(
            "slot for mesh {at}: the mesh has {} vertex buffers, expected one",
            buffers.len()
        )));
    };
    let buffer = flver
        .vertex_buffers
        .get(*buffer_index as usize)
        .ok_or("slot mesh names a missing vertex buffer")?;
    let SlotLayout {
        layout_index,
        vertex_size,
    } = slot.layout_or(buffer);
    if slot.vertex_bytes.len() != slot.vertex_count * vertex_size {
        return Err(Error::new(format!(
            "slot for mesh {at}: {} vertex bytes for {} vertices of {} bytes",
            slot.vertex_bytes.len(),
            slot.vertex_count,
            vertex_size
        )));
    }
    if let Some(layout) = slot.layout
        && !flver.vertex_buffers.iter().any(|other| {
            (other.layout_index, other.vertex_size) == (layout.layout_index, layout.vertex_size)
        })
    {
        return Err(Error::new(format!(
            "slot for mesh {at}: no donor buffer is laid out as layout {} of {} bytes",
            layout.layout_index, layout.vertex_size
        )));
    }
    for face_set_index in parse_u32_list(donor, mesh.face_set_offset, mesh.face_set_count)? {
        let face_set = flver
            .face_sets
            .get(face_set_index as usize)
            .ok_or("slot mesh names a missing face set")?;
        if face_set.triangle_strip || flver.index_size(face_set) != SLOT_INDEX_SIZE {
            return Err(Error::new(format!(
                "slot for mesh {at}: face set {face_set_index} is not a 16-bit triangle list"
            )));
        }
        let lod = face_set.lod();
        let triangles = slot.lods.get(lod).ok_or_else(|| {
            Error::new(format!(
                "slot for mesh {at}: face set {face_set_index} draws at LOD {lod}"
            ))
        })?;
        if triangles.is_empty() {
            return Err(Error::new(format!(
                "slot for mesh {at}: no triangles for LOD {lod}"
            )));
        }
    }
    if let Some(index) = slot
        .lods
        .iter()
        .flatten()
        .flatten()
        .find(|&&index| index as usize >= slot.vertex_count)
    {
        return Err(Error::new(format!(
            "slot for mesh {at}: index {index} past its {} vertices",
            slot.vertex_count
        )));
    }
    let layout = flver
        .layouts
        .get(layout_index)
        .ok_or("slot mesh's vertex buffer names a missing layout")?;
    check_bone_indices(
        slot,
        &parse_layout_members(donor, *layout)?,
        vertex_size,
        flver.bones.len(),
    )
}

/// Every bone index a slot's vertices carry names a donor bone. Elden Ring's body layouts store
/// them as bytes, so this also holds them to 255.
fn check_bone_indices(
    slot: &Slot,
    members: &[LayoutMember],
    vertex_size: usize,
    bone_count: usize,
) -> Result<()> {
    for member in members
        .iter()
        .filter(|member| member.semantic_id == BONE_INDICES_SEMANTIC)
    {
        for vertex in 0..slot.vertex_count {
            let at = vertex * vertex_size + member.struct_offset;
            let indices: [usize; 4] = match member.format_id {
                0x11 | 0x24 => {
                    let bytes = crate::bytes::slice(&slot.vertex_bytes, at, 4)?;
                    [0, 1, 2, 3].map(|i| usize::from(bytes[i]))
                }
                0x18 => {
                    let mut out = [0; 4];
                    for (i, index) in out.iter_mut().enumerate() {
                        *index = usize::from(u16_le(&slot.vertex_bytes, at + i * 2)?);
                    }
                    out
                }
                other => {
                    return Err(Error::new(format!(
                        "slot for mesh {}: bone indices in unknown format 0x{other:X}",
                        slot.donor_mesh
                    )));
                }
            };
            if let Some(bad) = indices.iter().find(|&&index| index >= bone_count) {
                return Err(Error::new(format!(
                    "slot for mesh {}: vertex {vertex} names bone {bad} of {bone_count}",
                    slot.donor_mesh
                )));
            }
        }
    }
    Ok(())
}

fn claim(seen: &mut [bool], index: usize, what: &str) -> Result<()> {
    if std::mem::replace(&mut seen[index], true) {
        return Err(Error::new(format!("{what} {index} belongs to two meshes")));
    }
    Ok(())
}

fn pad(data: &mut Vec<u8>) {
    data.resize(data.len().next_multiple_of(DATA_ALIGNMENT), 0);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::flver::{
        FaceSet, parse_face_set_indices,
        synthetic::{self, BONE_COUNT, MeshSpec, VERTEX_SIZE},
        verify,
    };

    /// Two body meshes, the first the one a slot replaces.
    fn donor() -> Vec<u8> {
        synthetic::flver(&[
            MeshSpec::with_lods(10, [12, 6, 3]),
            MeshSpec::with_lods(7, [9, 3, 3]),
        ])
    }

    fn header_u32(bytes: &[u8], offset: usize) -> u32 {
        crate::bytes::u32_le(bytes, offset).unwrap()
    }

    /// A slot for mesh 0 with `vertex_count` synthetic vertices and these triangle counts.
    fn slot(vertex_count: usize, counts: [usize; 3]) -> Slot {
        let lod = |count: usize| -> Vec<[u32; 3]> {
            (0..count)
                .map(|i| [0, 1, 2].map(|k| ((i + k) % vertex_count) as u32))
                .collect()
        };
        Slot {
            donor_mesh: 0,
            material: 0,
            cull_backfaces: false,
            vertex_count,
            vertex_bytes: (0..vertex_count).flat_map(synthetic::vertex).collect(),
            layout: None,
            lods: counts.map(lod),
            obb: Obb {
                min: Vec3 {
                    x: -1.0,
                    y: 0.0,
                    z: -2.0,
                },
                max: Vec3 {
                    x: 1.0,
                    y: 2.0,
                    z: 0.0,
                },
            },
        }
    }

    fn mesh_face_sets(bytes: &[u8], flver: &Flver, mesh: usize) -> Vec<FaceSet> {
        let mesh = flver.meshes[mesh];
        parse_u32_list(bytes, mesh.face_set_offset, mesh.face_set_count)
            .unwrap()
            .into_iter()
            .map(|index| flver.face_sets[index as usize])
            .collect()
    }

    #[test]
    fn with_no_slots_the_donor_comes_back_byte_for_byte() {
        let donor = donor();
        let out = rebuild(&donor, &[], false, &HeaderAppend::default()).unwrap();
        assert_eq!(out, donor);
        verify::check_flver(&out, &[], false).unwrap();
    }

    #[test]
    fn hiding_zeroes_every_index_and_keeps_every_count() {
        let donor = donor();
        let out = rebuild(&donor, &[], true, &HeaderAppend::default()).unwrap();
        assert_eq!(out.len(), donor.len());
        let (before, after) = (Flver::parse(&donor).unwrap(), Flver::parse(&out).unwrap());
        for (old, new) in before.face_sets.iter().zip(&after.face_sets) {
            assert_eq!(old.index_count, new.index_count);
            assert!(
                parse_face_set_indices(&out, &after, new)
                    .unwrap()
                    .iter()
                    .all(|&index| index == 0)
            );
        }
        assert_eq!(
            out[..after.header.data_offset],
            donor[..before.header.data_offset]
        );
    }

    #[test]
    fn a_grown_slot_moves_every_later_buffer_and_keeps_the_layout() {
        let donor = donor();
        let grown = slot(40, [50, 20, 7]);
        let out = rebuild(
            &donor,
            std::slice::from_ref(&grown),
            false,
            &HeaderAppend::default(),
        )
        .unwrap();
        let before = Flver::parse(&donor).unwrap();
        let after = Flver::parse(&out).unwrap();

        // The data section tiles as the donor's did, and ends the file on 16.
        let report = verify::check_flver(&out, &[], false).unwrap();
        assert_eq!(report.vertices, 40 + 7);
        assert_eq!(report.shared_face_sets, 0);
        assert!(after.header.data_length.is_multiple_of(16));
        assert_eq!(
            after.header.data_offset + after.header.data_length,
            out.len()
        );
        assert_eq!(after.header.data_offset, before.header.data_offset);

        // The slot's face sets draw its own lists, LOD by LOD, motion-blur copies included,
        // each from its own range.
        let face_sets = mesh_face_sets(&out, &after, 0);
        let mut offsets = Vec::new();
        for face_set in &face_sets {
            let triangles = &grown.lods[face_set.lod()];
            assert_eq!(face_set.index_count, triangles.len() * 3);
            assert_eq!(face_set.byte_length, triangles.len() * 6);
            assert!(!face_set.cull_backfaces);
            let indices = parse_face_set_indices(&out, &after, face_set).unwrap();
            assert_eq!(
                indices,
                triangles.iter().flatten().copied().collect::<Vec<_>>()
            );
            offsets.push(face_set.index_offset);
        }
        offsets.dedup();
        assert_eq!(offsets.len(), face_sets.len());

        // Mesh 1 starts right after the slot's buffer and moved as one piece, by a multiple of
        // 16, its bytes unchanged.
        let slot_end = after.vertex_buffers[0].buffer_offset + 40 * VERTEX_SIZE;
        let (old_sets, new_sets) = (
            mesh_face_sets(&donor, &before, 1),
            mesh_face_sets(&out, &after, 1),
        );
        assert_eq!(new_sets[0].index_offset, slot_end.next_multiple_of(16));
        let shift = new_sets[0].index_offset - old_sets[0].index_offset;
        assert!(shift > 0 && shift.is_multiple_of(16));
        for (old, new) in old_sets.iter().zip(&new_sets) {
            assert_eq!(new.index_offset, old.index_offset + shift);
            assert_eq!(
                parse_face_set_indices(&out, &after, new).unwrap(),
                parse_face_set_indices(&donor, &before, old).unwrap()
            );
        }
        let (old, new) = (before.vertex_buffers[1], after.vertex_buffers[1]);
        assert_eq!(new.buffer_offset, old.buffer_offset + shift);
        let at = |flver: &Flver, buffer: &crate::flver::VertexBuffer| {
            flver.header.data_offset + buffer.buffer_offset
        };
        assert_eq!(
            out[at(&after, &new)..at(&after, &new) + new.buffer_length],
            donor[at(&before, &old)..at(&before, &old) + old.buffer_length]
        );

        // The slot's vertices, material, box and the header's sums.
        let slot_buffer = after.vertex_buffers[0];
        assert_eq!(slot_buffer.vertex_count, 40);
        assert_eq!(
            out[at(&after, &slot_buffer)..at(&after, &slot_buffer) + slot_buffer.buffer_length],
            grown.vertex_bytes[..]
        );
        // Faces outside motion-blur sets: the slot's 50 + 20 + 7 and mesh 1's 3 + 1 + 1. Every
        // face set: twice that.
        assert_eq!(header_u32(&out, 0x40), 82);
        assert_eq!(header_u32(&out, 0x44), 164);
        let box_at = after.meshes[0].bounding_box_offset;
        let read = |offset| crate::flver::read_vec3(&out, offset).unwrap();
        assert_eq!(
            read(box_at),
            Vec3 {
                x: 1.0,
                y: 1.0,
                z: 1.0
            }
        );
        assert_eq!(read(box_at + 0x0C), Vec3::default());
        assert_eq!(
            read(box_at + 0x18),
            Vec3 {
                x: 0.0,
                y: 1.0,
                z: -1.0
            }
        );
        assert_eq!(after.header.bbox_min, grown.obb.min);
        assert_eq!(after.header.bbox_max, grown.obb.max);
    }

    #[test]
    fn the_writer_refuses_what_it_cannot_lay_out() {
        let donor = donor();
        let refuse = |slot: Slot| {
            rebuild(&donor, &[slot], false, &HeaderAppend::default())
                .expect_err("expected a refusal")
                .to_string()
        };
        let mut bad = slot(4, [1, 1, 1]);
        bad.lods[0] = vec![[0, 1, 4]];
        assert!(refuse(bad).contains("index 4 past its 4 vertices"));

        let mut bad = slot(4, [1, 1, 1]);
        bad.lods[2].clear();
        assert!(refuse(bad).contains("no triangles for LOD 2"));

        let mut bad = slot(4, [1, 1, 1]);
        bad.vertex_bytes.pop();
        assert!(refuse(bad).contains("vertex bytes"));

        let mut bad = slot(4, [1, 1, 1]);
        bad.vertex_bytes[12] = BONE_COUNT as u8;
        assert!(refuse(bad).contains("names bone 2 of 2"));

        let mut bad = slot(4, [1, 1, 1]);
        bad.donor_mesh = 9;
        assert!(refuse(bad).contains("no such mesh"));

        let mut bad = slot(MAX_SLOT_VERTICES + 1, [1, 1, 1]);
        bad.vertex_bytes.clear();
        assert!(refuse(bad).contains("16-bit"));

        let twice = [slot(4, [1, 1, 1]), slot(4, [1, 1, 1])];
        assert!(
            rebuild(&donor, &twice, false, &HeaderAppend::default())
                .unwrap_err()
                .to_string()
                .contains("two slots")
        );

        let mut strip = donor.clone();
        let flver = Flver::parse(&strip).unwrap();
        strip[flver.face_set_table_offset(0) + 4] = 1;
        assert!(
            rebuild(
                &strip,
                &[slot(4, [1, 1, 1])],
                false,
                &HeaderAppend::default()
            )
            .unwrap_err()
            .to_string()
            .contains("not a 16-bit triangle list")
        );

        let mut trailing = donor.clone();
        trailing.push(0);
        assert!(rebuild(&trailing, &[], false, &HeaderAppend::default()).is_err());
    }

    /// One 16-byte `GX00` item and the terminator, laid out as Elden Ring's GX lists are.
    fn gx_list() -> Vec<u8> {
        let mut out = Vec::new();
        for (id, length) in [(i32::from_le_bytes(*b"GX00"), 16_usize), (i32::MAX, 12)] {
            out.extend(id.to_le_bytes());
            out.extend(100_u32.to_le_bytes());
            out.extend((length as u32).to_le_bytes());
            out.resize(out.len() + length - GX_ITEM_HEADER, 0x5A);
        }
        out
    }

    fn texture(sampler: &str, path: &str) -> Texture {
        Texture {
            path: path.to_owned(),
            sampler: sampler.to_owned(),
        }
    }

    #[test]
    fn an_append_moves_the_data_section_and_every_offset_into_it_still_holds() {
        let donor = synthetic::flver_with_textures(
            &[
                MeshSpec::with_lods(10, [12, 6, 3]),
                MeshSpec::with_lods(7, [9, 3, 3]),
            ],
            &["", "", ""],
        );
        let grown = vec![slot(40, [50, 20, 7])];
        let plain = rebuild(&donor, &grown, false, &HeaderAppend::default()).unwrap();
        // An append made for the donor with nothing in it changes nothing.
        let unused = HeaderAppend::for_donor(&donor).unwrap();
        assert!(unused.is_empty());
        assert_eq!(rebuild(&donor, &grown, false, &unused).unwrap(), plain);

        let mut append = HeaderAppend::for_donor(&donor).unwrap();
        let start = append.start;
        let text_at = append.append_utf16("C[AMSN]_e").unwrap() as usize;
        let bytes_at = append.append_bytes(&[1, 2, 3], 16).unwrap() as usize;
        assert_eq!(text_at, start);
        assert_eq!(bytes_at, (start + 20).next_multiple_of(16));
        let out = rebuild(&donor, &grown, false, &append).unwrap();

        let (before, after) = (Flver::parse(&plain).unwrap(), Flver::parse(&out).unwrap());
        assert_eq!(
            after.header.data_offset,
            (bytes_at + 3).next_multiple_of(16)
        );
        assert!(after.header.data_offset > before.header.data_offset);
        // The data section is the same bytes, and every entry points into it as it did.
        assert_eq!(
            out[after.header.data_offset..],
            plain[before.header.data_offset..]
        );
        assert_eq!(after.header.data_length, before.header.data_length);
        for (old, new) in before.face_sets.iter().zip(&after.face_sets) {
            assert_eq!(
                (old.index_offset, old.byte_length, old.index_count),
                (new.index_offset, new.byte_length, new.index_count)
            );
        }
        for (old, new) in before.vertex_buffers.iter().zip(&after.vertex_buffers) {
            assert_eq!(
                (old.buffer_offset, old.buffer_length),
                (new.buffer_offset, new.buffer_length)
            );
        }
        // Before the appended bytes, only dataOffset changed.
        assert_eq!(out[..HEADER_DATA_OFFSET], plain[..HEADER_DATA_OFFSET]);
        assert_eq!(
            out[HEADER_DATA_OFFSET + 4..start],
            plain[HEADER_DATA_OFFSET + 4..start]
        );
        assert_eq!(crate::bytes::utf16z(&out, text_at).unwrap(), "C[AMSN]_e");
        assert_eq!(out[bytes_at..bytes_at + 3], [1, 2, 3]);
        verify::check_flver(&out, &[], false).unwrap();
    }

    #[test]
    fn retargeting_a_material_rewrites_its_entry_and_its_first_textures_only() {
        let donor =
            synthetic::flver_with_textures(&[MeshSpec::with_lods(6, [6, 3, 3])], &["", "", ""]);
        let mut append = HeaderAppend::for_donor(&donor).unwrap();
        let mtd = r"N:\GR\data\Material\mtd\Chr_Base\matxml\C[AMSN]_e.matxml";
        let textures = [
            texture("AlbedoMap_0", r"N:\GR\tex\FC_M_0000_a.tif"),
            texture("NormalMap_4", r"N:\GR\tex\FC_M_0000_n.tif"),
        ];
        append
            .retarget_material(0, "c2650_edge", mtd, &textures, &gx_list(), 786)
            .unwrap();
        let out = rebuild(&donor, &[], false, &append).unwrap();
        let flver = Flver::parse(&out).unwrap();

        let material = &parse_materials(&out, &flver).unwrap()[0];
        assert_eq!(
            (
                material.name.as_str(),
                material.mtd.as_str(),
                material.texture_count
            ),
            ("c2650_edge", mtd, 3),
            "the donor's texture count is kept"
        );
        assert_eq!(
            material_template(&out, 0).unwrap(),
            MaterialTemplate {
                mtd: mtd.to_owned(),
                flags: 786,
                gx: gx_list(),
                samplers: ["AlbedoMap_0", "NormalMap_4", "AlbedoMap"]
                    .map(String::from)
                    .to_vec(),
            }
        );
        let paths: Vec<String> = parse_textures(&out, &flver)
            .unwrap()
            .into_iter()
            .map(|texture| texture.path)
            .collect();
        assert_eq!(
            paths,
            [textures[0].path.as_str(), textures[1].path.as_str(), ""]
        );
        let tiling = |index: usize| {
            let at = flver.texture_table + index * TEXTURE_SIZE + TEXTURE_TILING;
            out[at..at + 4].to_vec()
        };
        assert_eq!(tiling(0), [1, 1, 0, 0]);
        assert_eq!(tiling(2), [0, 0, 0, 0]);
        let first_of_mtd = flver.material_table + MATERIAL_FIRST_OF_MTD;
        assert_eq!(crate::bytes::u32_le(&out, first_of_mtd).unwrap(), 0);

        // Outside dataOffset, the material's entry and its first two texture entries, the
        // header region is the donor's.
        let written = [
            HEADER_DATA_OFFSET..HEADER_DATA_OFFSET + 4,
            flver.material_table..flver.material_table + MATERIAL_SIZE,
            flver.texture_table..flver.texture_table + 2 * TEXTURE_SIZE,
        ];
        for at in 0..append.start {
            assert!(
                out[at] == donor[at] || written.iter().any(|range| range.contains(&at)),
                "byte 0x{at:X} changed"
            );
        }
        let tpf = ["FC_M_0000_a", "FC_M_0000_n"].map(String::from);
        verify::check_flver(&out, &tpf, false).unwrap();
        assert!(verify::check_flver(&out, &tpf[..1], false).is_err());
    }

    #[test]
    fn a_header_append_refuses_what_it_cannot_place() {
        let donor =
            synthetic::flver_with_textures(&[MeshSpec::with_lods(6, [6, 3, 3])], &["", "", ""]);
        let error = |result: Result<()>| result.unwrap_err().to_string();
        let retarget = |append: &mut HeaderAppend, index: usize, count: usize, gx: &[u8]| {
            let textures = vec![texture("AlbedoMap_0", "a.tif"); count];
            append.retarget_material(index, "edge", "C[AMSN]_e", &textures, gx, 0)
        };

        let mut empty = HeaderAppend::default();
        assert!(error(empty.append_utf16("x").map(drop)).contains("no donor"));
        assert!(error(retarget(&mut empty, 0, 1, &gx_list())).contains("no donor"));

        let mut append = HeaderAppend::for_donor(&donor).unwrap();
        assert!(error(append.append_bytes(&[0], 3).map(drop)).contains("power of two"));
        assert!(error(retarget(&mut append, 1, 1, &gx_list())).contains("has 1 materials"));
        assert!(error(retarget(&mut append, 0, 4, &gx_list())).contains("4 were asked for"));
        let mut long = gx_list();
        long.push(0);
        assert!(error(retarget(&mut append, 0, 1, &long)).contains("ends at byte 28 of 29"));
        let mut short = gx_list();
        short[GX_ITEM_LENGTH] = 4;
        assert!(error(retarget(&mut append, 0, 1, &short)).contains("shorter than its header"));
        assert!(error(retarget(&mut append, 0, 1, &gx_list()[..16])).contains("runs past"));
        retarget(&mut append, 0, 1, &gx_list()).unwrap();
        assert!(error(retarget(&mut append, 0, 1, &gx_list())).contains("twice"));

        // An append is laid onto the donor it was made for and no other.
        let other = synthetic::flver_with_textures(&[MeshSpec::with_lods(6, [6, 3, 3])], &[""]);
        assert!(
            rebuild(&other, &[], false, &append)
                .unwrap_err()
                .to_string()
                .contains("different donor")
        );
        let mut single_byte = donor.clone();
        single_byte[HEADER_UNICODE] = 0;
        assert!(
            HeaderAppend::for_donor(&single_byte)
                .unwrap_err()
                .to_string()
                .contains("single-byte")
        );
    }
}
