//! Binders: FromSoftware's archive-in-a-file. `BND3` is read (Dark Souls Remastered's
//! `c2280.chrbnd`), `BND4` is read and written (every Elden Ring part).
//!
//! The layouts follow SoulsFormats' `BND3`/`BND4`, and the writer is held to a stricter
//! standard than "the game loads it": [`Bnd4::write`] of an unmodified parse must reproduce the
//! game's own bytes exactly (`tests/game_files.rs`). Header fields this tool has no reason to
//! change -- the version string, the raw format byte, per-file flags -- are carried verbatim
//! rather than re-derived, so they cannot be re-derived wrongly.

use crate::{
    bytes::{Writer, byte_string_z, i32_le, i64_le, slice, u8_at, u32_le, u64_le, utf16z},
    error::{Context, Error, Result},
};

const BND3_MAGIC: &[u8; 4] = b"BND3";
const BND4_MAGIC: &[u8; 4] = b"BND4";
const BND4_HEADER_SIZE: u64 = 0x40;
const BND3_FILE_HEADERS: usize = 0x20;
const HASH_TABLE_EXTENDED: u8 = 4;
const HASH_TABLE_MAGIC: i32 = 0x0008_0810;
const HASH_GROUP_DIVISOR: usize = 7;
const PATH_HASH_MULTIPLIER: u32 = 37;
const FILE_DATA_ALIGN: usize = 0x10;
const HASH_TABLE_ALIGN: usize = 8;

/// The binder format flags, after the bit-order quirk in [`decode_format`] is undone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Format(u8);

impl Format {
    const BIG_ENDIAN: u8 = 0b0000_0001;
    const IDS: u8 = 0b0000_0010;
    const NAMES1: u8 = 0b0000_0100;
    const NAMES2: u8 = 0b0000_1000;
    const LONG_OFFSETS: u8 = 0b0001_0000;
    const COMPRESSION: u8 = 0b0010_0000;
    const FLAG7: u8 = 0b1000_0000;

    fn has(self, bit: u8) -> bool {
        self.0 & bit != 0
    }
    fn ids(self) -> bool {
        self.has(Self::IDS)
    }
    fn names(self) -> bool {
        self.has(Self::NAMES1) || self.has(Self::NAMES2)
    }
    fn long_offsets(self) -> bool {
        self.has(Self::LONG_OFFSETS)
    }
    fn compression(self) -> bool {
        self.has(Self::COMPRESSION)
    }
    fn names1_only(self) -> bool {
        self.0 == Self::NAMES1
    }
    fn big_endian(self) -> bool {
        self.has(Self::BIG_ENDIAN)
    }
}

/// SoulsFormats `Binder.ReadFormat`: the byte is stored bit-reversed unless the binder says its
/// bits are big-endian, or the byte itself reads as a big-endian format with the top flag clear.
fn decode_format(raw: u8, bit_big_endian: bool) -> Format {
    let as_stored = bit_big_endian || (raw & Format::BIG_ENDIAN != 0 && raw & Format::FLAG7 == 0);
    Format(if as_stored { raw } else { raw.reverse_bits() })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BinderFile {
    pub flags: u8,
    pub id: i32,
    pub name: String,
    pub data: Vec<u8>,
}

/// The `name` of a binder entry without its directory, as SoulsFormats' `Path.GetFileName`.
pub fn file_name(name: &str) -> &str {
    name.rsplit(['\\', '/']).next().unwrap_or(name)
}

/// The files of a `BND3`, which this tool only ever reads.
pub fn read_bnd3(bytes: &[u8]) -> Result<Vec<BinderFile>> {
    if slice(bytes, 0, 4)? != BND3_MAGIC {
        return Err(Error::new("not a BND3 binder"));
    }
    let bit_big_endian = u8_at(bytes, 0x0E)? != 0;
    let format = decode_format(u8_at(bytes, 0x0C)?, bit_big_endian);
    if u8_at(bytes, 0x0D)? != 0 || format.big_endian() {
        return Err(Error::new(
            "big-endian BND3 is a console binder, not DSR PC",
        ));
    }
    let file_count = usize::try_from(i32_le(bytes, 0x10)?).context("BND3 file count")?;
    let mut cursor = BND3_FILE_HEADERS;
    let mut files = Vec::with_capacity(file_count);
    for _ in 0..file_count {
        let flags = u8_at(bytes, cursor)?;
        let compressed_size = usize::try_from(i32_le(bytes, cursor + 4)?).context("size")?;
        cursor += 8;
        let data_offset = if format.long_offsets() {
            let offset = u64_le(bytes, cursor)? as usize;
            cursor += 8;
            offset
        } else {
            let offset = u32_le(bytes, cursor)? as usize;
            cursor += 4;
            offset
        };
        let mut id = -1;
        if format.ids() {
            id = i32_le(bytes, cursor)?;
            cursor += 4;
        }
        let mut name = String::new();
        if format.names() {
            name = byte_string_z(bytes, u32_le(bytes, cursor)? as usize)?;
            cursor += 4;
        }
        if format.compression() {
            cursor += 4;
        }
        files.push(BinderFile {
            flags,
            id,
            name,
            data: slice(bytes, data_offset, compressed_size)?.to_vec(),
        });
    }
    Ok(files)
}

/// An Elden Ring binder, with every header field kept so it can be written back unchanged.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bnd4 {
    pub unk04: u8,
    pub unk05: u8,
    pub bit_big_endian_byte: u8,
    pub version: [u8; 8],
    pub unicode: bool,
    pub raw_format: u8,
    pub extended: u8,
    pub files: Vec<BinderFile>,
}

impl Bnd4 {
    fn format(&self) -> Format {
        decode_format(self.raw_format, self.bit_big_endian_byte == 0)
    }

    fn file_header_size(&self) -> usize {
        let format = self.format();
        0x10 + if format.compression() { 8 } else { 0 }
            + if format.long_offsets() { 8 } else { 4 }
            + if format.ids() { 4 } else { 0 }
            + if format.names() { 4 } else { 0 }
            + if format.names1_only() { 8 } else { 0 }
    }

    pub fn read(bytes: &[u8]) -> Result<Self> {
        if slice(bytes, 0, 4)? != BND4_MAGIC {
            return Err(Error::new("not a BND4 binder"));
        }
        if u8_at(bytes, 0x09)? != 0 {
            return Err(Error::new(
                "big-endian BND4 is a console binder, not Elden Ring PC",
            ));
        }
        let mut binder = Self {
            unk04: u8_at(bytes, 0x04)?,
            unk05: u8_at(bytes, 0x05)?,
            bit_big_endian_byte: u8_at(bytes, 0x0A)?,
            version: slice(bytes, 0x18, 8)?.try_into().context("BND4 version")?,
            unicode: u8_at(bytes, 0x30)? != 0,
            raw_format: u8_at(bytes, 0x31)?,
            extended: u8_at(bytes, 0x32)?,
            files: Vec::new(),
        };
        let format = binder.format();
        if format.big_endian() {
            return Err(Error::new("big-endian BND4 format flag on a PC binder"));
        }
        let file_count = usize::try_from(i32_le(bytes, 0x0C)?).context("BND4 file count")?;
        let header_size = u64_le(bytes, 0x20)? as usize;
        if header_size != binder.file_header_size() {
            return Err(Error::new(format!(
                "BND4 file header size 0x{header_size:X} does not match its format flags \
                 (0x{:X} expected)",
                binder.file_header_size()
            )));
        }
        for index in 0..file_count {
            let mut cursor = BND4_HEADER_SIZE as usize + index * header_size;
            let flags = u8_at(bytes, cursor)?;
            let compressed_size = usize::try_from(i64_le(bytes, cursor + 8)?).context("size")?;
            cursor += 0x10;
            if format.compression() {
                cursor += 8;
            }
            let data_offset = if format.long_offsets() {
                let offset = u64_le(bytes, cursor)? as usize;
                cursor += 8;
                offset
            } else {
                let offset = u32_le(bytes, cursor)? as usize;
                cursor += 4;
                offset
            };
            let mut id = -1;
            if format.ids() {
                id = i32_le(bytes, cursor)?;
                cursor += 4;
            }
            let mut name = String::new();
            if format.names() {
                let name_offset = u32_le(bytes, cursor)? as usize;
                name = if binder.unicode {
                    utf16z(bytes, name_offset)?
                } else {
                    byte_string_z(bytes, name_offset)?
                };
                cursor += 4;
            }
            if format.names1_only() {
                id = i32_le(bytes, cursor)?;
            }
            binder.files.push(BinderFile {
                flags,
                id,
                name,
                data: slice(bytes, data_offset, compressed_size)?.to_vec(),
            });
        }
        Ok(binder)
    }

    /// SoulsFormats `BND4.Write`, for an uncompressed little-endian binder.
    pub fn write(&self) -> Result<Vec<u8>> {
        let format = self.format();
        let mut out = Writer::default();
        out.bytes(BND4_MAGIC);
        out.u8(self.unk04);
        out.u8(self.unk05);
        out.bytes(&[0, 0, 0]);
        out.u8(0);
        out.u8(self.bit_big_endian_byte);
        out.u8(0);
        out.i32_le(i32::try_from(self.files.len()).context("BND4 file count")?);
        out.u64_le(BND4_HEADER_SIZE);
        out.bytes(&self.version);
        out.u64_le(self.file_header_size() as u64);
        let headers_end_at = out.position();
        out.u64_le(0);
        out.u8(u8::from(self.unicode));
        out.u8(self.raw_format);
        out.u8(self.extended);
        out.u8(0);
        out.u32_le(0);
        let hash_table_at = out.position();
        out.u64_le(0);

        struct Slots {
            compressed_size: usize,
            data_offset: usize,
            name_offset: Option<usize>,
        }
        let mut slots = Vec::with_capacity(self.files.len());
        for file in &self.files {
            out.u8(file.flags);
            out.bytes(&[0, 0, 0]);
            out.i32_le(-1);
            let compressed_size = out.position();
            out.i64_le(0);
            if format.compression() {
                out.i64_le(i64::try_from(file.data.len()).context("file size")?);
            }
            let data_offset = out.position();
            if format.long_offsets() {
                out.u64_le(0);
            } else {
                out.u32_le(0);
            }
            if format.ids() {
                out.i32_le(file.id);
            }
            let name_offset = format.names().then(|| {
                let at = out.position();
                out.u32_le(0);
                at
            });
            if format.names1_only() {
                out.i32_le(file.id);
                out.i32_le(0);
            }
            slots.push(Slots {
                compressed_size,
                data_offset,
                name_offset,
            });
        }

        for (file, slot) in self.files.iter().zip(&slots) {
            if let Some(name_offset) = slot.name_offset {
                let at = u32::try_from(out.position()).context("name offset")?;
                out.patch_u32_le(name_offset, at);
                if self.unicode {
                    for unit in file.name.encode_utf16() {
                        out.bytes(&unit.to_le_bytes());
                    }
                    out.bytes(&[0, 0]);
                } else {
                    out.bytes(file.name.as_bytes());
                    out.u8(0);
                }
            }
        }

        if self.extended == HASH_TABLE_EXTENDED {
            out.pad(HASH_TABLE_ALIGN);
            let at = out.position() as u64;
            out.patch_u64_le(hash_table_at, at);
            write_hash_table(&mut out, &self.files)?;
        }
        let headers_end = out.position() as u64;
        out.patch_u64_le(headers_end_at, headers_end);

        for (file, slot) in self.files.iter().zip(&slots) {
            if !file.data.is_empty() {
                out.pad(FILE_DATA_ALIGN);
            }
            let at = out.position();
            if format.long_offsets() {
                out.patch_u64_le(slot.data_offset, at as u64);
            } else {
                out.patch_u32_le(slot.data_offset, u32::try_from(at).context("data offset")?);
            }
            out.bytes(&file.data);
            out.patch_u64_le(slot.compressed_size, file.data.len() as u64);
        }
        Ok(out.into_inner())
    }

    pub fn file_named(&self, file_name_wanted: &str) -> Option<&BinderFile> {
        self.files
            .iter()
            .find(|file| file_name(&file.name).eq_ignore_ascii_case(file_name_wanted))
    }

    pub fn file_named_mut(&mut self, file_name_wanted: &str) -> Option<&mut BinderFile> {
        self.files
            .iter_mut()
            .find(|file| file_name(&file.name).eq_ignore_ascii_case(file_name_wanted))
    }
}

/// SoulsFormats `SFUtil.FromPathHash`: 32-bit, lower-cased, forward slashes, rooted at `/`.
fn binder_path_hash(name: &str) -> u32 {
    let mut normalized = name.to_lowercase().replace('\\', "/");
    if !normalized.starts_with('/') {
        normalized.insert(0, '/');
    }
    normalized.chars().fold(0_u32, |hash, ch| {
        hash.wrapping_mul(PATH_HASH_MULTIPLIER)
            .wrapping_add(u32::from(ch))
    })
}

fn is_prime(value: usize) -> bool {
    if value < 2 {
        return false;
    }
    (2..)
        .take_while(|d| d * d <= value)
        .all(|d| !value.is_multiple_of(d))
}

/// SoulsFormats `BinderHashTable.Write`: a prime number of groups, each a run of
/// `(hash, file index)` pairs sorted by hash.
fn write_hash_table(out: &mut Writer, files: &[BinderFile]) -> Result<()> {
    let group_count = (files.len() / HASH_GROUP_DIVISOR..)
        .find(|&candidate| is_prime(candidate))
        .ok_or_else(|| Error::new("no prime group count for the binder hash table"))?;
    let mut groups: Vec<Vec<(u32, i32)>> = vec![Vec::new(); group_count];
    for (index, file) in files.iter().enumerate() {
        let hash = binder_path_hash(&file.name);
        let index = i32::try_from(index).context("file index")?;
        groups[hash as usize % group_count].push((hash, index));
    }
    for group in &mut groups {
        group.sort_by_key(|&(hash, _)| hash);
    }

    let pairs_offset_at = out.position();
    out.u64_le(0);
    out.u32_le(u32::try_from(group_count).context("group count")?);
    out.i32_le(HASH_TABLE_MAGIC);
    let mut first = 0_usize;
    for group in &groups {
        out.i32_le(i32::try_from(group.len()).context("group length")?);
        out.i32_le(i32::try_from(first).context("group index")?);
        first += group.len();
    }
    let pairs_at = out.position() as u64;
    out.patch_u64_le(pairs_offset_at, pairs_at);
    for &(hash, index) in groups.iter().flatten() {
        out.u32_le(hash);
        out.i32_le(index);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(extended: u8) -> Bnd4 {
        Bnd4 {
            unk04: 0,
            unk05: 0,
            bit_big_endian_byte: 1,
            version: *b"07D7R6\0\0",
            unicode: true,
            // The byte every Elden Ring part stores: IDs, both name flags and compression.
            raw_format: 0x74,
            extended,
            files: (0..9)
                .map(|i| BinderFile {
                    flags: 0x40,
                    id: i,
                    name: format!("N:\\GR\\data\\x\\file{i}.flver"),
                    data: vec![i as u8; 17 + i as usize],
                })
                .collect(),
        }
    }

    #[test]
    fn written_binders_read_back_identically() {
        for extended in [0, HASH_TABLE_EXTENDED] {
            let binder = sample(extended);
            let bytes = binder.write().unwrap();
            assert_eq!(Bnd4::read(&bytes).unwrap(), binder);
        }
    }

    #[test]
    fn format_bits_are_reversed_unless_marked_big_endian() {
        // Elden Ring parts store 0x74 with little-endian bits: IDs | Names1 | Names2 | Compression.
        let part = decode_format(0x74, false);
        assert_eq!(part, Format(0x2E));
        assert!(part.ids() && part.names() && part.compression() && !part.long_offsets());
        assert_eq!(decode_format(0x74, true), Format(0x74));
    }

    #[test]
    fn file_name_strips_either_separator() {
        assert_eq!(
            file_name("N:\\GR\\parts\\FC_M_0000.flver"),
            "FC_M_0000.flver"
        );
        assert_eq!(file_name("a/b.tpf"), "b.tpf");
        assert_eq!(file_name("bare"), "bare");
    }

    #[test]
    fn group_counts_are_the_first_prime_at_or_above_a_seventh() {
        assert!(is_prime(2) && is_prime(13) && !is_prime(1) && !is_prime(21));
    }
}
