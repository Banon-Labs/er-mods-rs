//! What a guest's font request must look like before the host lets imgui near it.
//!
//! The host rasterizes a guest's TrueType bytes inside `Present`, through Dear ImGui's bundled
//! `stb_truetype`. That code trusts its input completely: it reads table offsets straight out of
//! the file without bounds checks, and when it cannot initialise a font the atlas build returns
//! `false`, `TexReady` stays clear and the next `NewFrame` trips `IM_ASSERT` -- which in this
//! build is a C `assert`, so an abort of the game. Everything here is the cheap, pure half of
//! refusing that: the shape checks `stbtt_InitFont` would otherwise make the hard way. The host
//! additionally trial-builds every accepted font in a scratch atlas before touching its own.
//!
//! Kept free of hudhook and of `cfg(windows)` so `cargo test` proves it on Linux.

/// Glyphs the host rasterizes for every guest font: Basic Latin plus Latin-1 Supplement
/// (`0x20..=0xFF`), Latin Extended-A (`0x100..=0x17F`) and General Punctuation
/// (`0x2000..=0x206F`). Imgui reads this as zero-terminated `[first, last]` pairs and keeps the
/// pointer for as long as the atlas lives, hence a `static`.
pub static GLYPH_RANGES: [u32; 5] = [0x0020, 0x017F, 0x2000, 0x206F, 0];

/// Largest TrueType blob a guest may hand over. The host copies it, and the atlas keeps a copy
/// per size, so this bounds memory rather than taste.
pub const MAX_FONT_BYTES: usize = 16 * 1024 * 1024;

/// Most sizes one request may ask for. Every size is a separate `ImFont` in the one atlas
/// texture, and the texture is shared by every overlay in the process.
pub const MAX_SIZES_PER_FONT: usize = 8;

/// Most font requests one host accepts over the life of the process.
pub const MAX_FONT_REQUESTS: usize = 16;

/// Smallest and largest pixel size accepted. Below the floor glyphs are unreadable; above the
/// ceiling a single size can push the shared atlas past the texture size the GPU accepts.
pub const MIN_SIZE_PX: f32 = 6.0;
pub const MAX_SIZE_PX: f32 = 200.0;

/// Vertical resolution the layout of every guest is authored against.
pub const REFERENCE_HEIGHT_PX: f32 = 1080.0;

/// Why a request was refused. The host answers handle `0` for every one of these.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FontRejection {
    /// No bytes, or a null pointer.
    Empty,
    /// Over [`MAX_FONT_BYTES`].
    TooLarge,
    /// No sizes requested.
    NoSizes,
    /// Over [`MAX_SIZES_PER_FONT`].
    TooManySizes,
    /// A size that is not finite or outside [`MIN_SIZE_PX`]..=[`MAX_SIZE_PX`].
    BadSize,
    /// The bytes are not a font `stb_truetype` can open; names what was wrong.
    NotAFont(&'static str),
}

/// Check a request's sizes and bytes.
pub fn validate(ttf: &[u8], sizes_px: &[f32]) -> Result<(), FontRejection> {
    if ttf.is_empty() {
        return Err(FontRejection::Empty);
    }
    if ttf.len() > MAX_FONT_BYTES {
        return Err(FontRejection::TooLarge);
    }
    if sizes_px.is_empty() {
        return Err(FontRejection::NoSizes);
    }
    if sizes_px.len() > MAX_SIZES_PER_FONT {
        return Err(FontRejection::TooManySizes);
    }
    if sizes_px
        .iter()
        .any(|size| !size.is_finite() || !(MIN_SIZE_PX..=MAX_SIZE_PX).contains(size))
    {
        return Err(FontRejection::BadSize);
    }
    check_sfnt(ttf).map_err(FontRejection::NotAFont)
}

/// `display_h / 1080`, the factor a guest multiplies its 1080p layout by. A degenerate height
/// answers `1.0` so a guest never divides or scales by zero.
pub fn ui_scale(display_h: f32) -> f32 {
    if display_h.is_finite() && display_h > 0.0 {
        display_h / REFERENCE_HEIGHT_PX
    } else {
        1.0
    }
}

fn be16(data: &[u8], at: usize) -> Option<u16> {
    let bytes = data.get(at..at.checked_add(2)?)?;
    Some(u16::from_be_bytes([bytes[0], bytes[1]]))
}

fn be32(data: &[u8], at: usize) -> Option<u32> {
    let bytes = data.get(at..at.checked_add(4)?)?;
    Some(u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

const TAG_TRUETYPE: u32 = 0x0001_0000;
const TAG_TRUE: u32 = u32::from_be_bytes(*b"true");
const TAG_OTTO: u32 = u32::from_be_bytes(*b"OTTO");
const TAG_TTCF: u32 = u32::from_be_bytes(*b"ttcf");

/// The checks `stbtt_GetFontOffsetForIndex(data, 0)` and `stbtt_InitFont` make, done with bounds
/// checks: a recognised header, a table directory inside the buffer, every table inside the
/// buffer, and the tables `stbtt_InitFont` refuses to open a font without.
fn check_sfnt(data: &[u8]) -> Result<(), &'static str> {
    let mut offset = 0usize;
    let tag = be32(data, 0).ok_or("shorter than an sfnt header")?;
    if tag == TAG_TTCF {
        // A collection: imgui opens font 0, whose offset is the first entry after the header.
        let version = be32(data, 4).ok_or("truncated collection header")?;
        if version != 0x0001_0000 && version != 0x0002_0000 {
            return Err("unknown collection version");
        }
        if be32(data, 8).ok_or("truncated collection header")? == 0 {
            return Err("empty collection");
        }
        offset = be32(data, 12).ok_or("truncated collection header")? as usize;
    }
    let tag = be32(data, offset).ok_or("font offset outside the data")?;
    if tag != TAG_TRUETYPE && tag != TAG_TRUE && tag != TAG_OTTO {
        return Err("not a TrueType or OpenType font");
    }
    let tables = usize::from(be16(data, offset + 4).ok_or("truncated sfnt header")?);
    let directory = offset + 12;
    if directory
        .checked_add(tables.checked_mul(16).ok_or("table count overflows")?)
        .is_none_or(|end| end > data.len())
    {
        return Err("table directory runs past the data");
    }

    let found = |wanted: &[u8; 4]| -> Result<Option<usize>, &'static str> {
        let wanted = u32::from_be_bytes(*wanted);
        for index in 0..tables {
            let entry = directory + index * 16;
            if be32(data, entry) != Some(wanted) {
                continue;
            }
            let start = be32(data, entry + 8).ok_or("truncated table entry")? as usize;
            let length = be32(data, entry + 12).ok_or("truncated table entry")? as usize;
            if start.checked_add(length).is_none_or(|end| end > data.len()) {
                return Err("a table runs past the data");
            }
            return Ok(Some(length));
        }
        Ok(None)
    };

    // `stbtt_InitFont` reads fixed fields at these offsets without checking the table length.
    let head = found(b"head")?.ok_or("no head table")?;
    if head < 54 {
        return Err("head table too short");
    }
    let hhea = found(b"hhea")?.ok_or("no hhea table")?;
    if hhea < 36 {
        return Err("hhea table too short");
    }
    found(b"cmap")?.ok_or("no cmap table")?;
    found(b"hmtx")?.ok_or("no hmtx table")?;
    if found(b"glyf")?.is_some() {
        found(b"loca")?.ok_or("glyf without loca")?;
    } else if found(b"CFF ")?.is_none() {
        return Err("neither glyf nor CFF outlines");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A minimal sfnt with the named tables, each `len` bytes long, laid out after the directory.
    fn sfnt(tag: u32, tables: &[(&[u8; 4], usize)]) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&tag.to_be_bytes());
        out.extend_from_slice(&(tables.len() as u16).to_be_bytes());
        out.extend_from_slice(&[0; 6]);
        let mut data_at = 12 + tables.len() * 16;
        for (name, len) in tables {
            out.extend_from_slice(*name);
            out.extend_from_slice(&0u32.to_be_bytes());
            out.extend_from_slice(&(data_at as u32).to_be_bytes());
            out.extend_from_slice(&(*len as u32).to_be_bytes());
            data_at += len;
        }
        for (_, len) in tables {
            out.extend(std::iter::repeat_n(0u8, *len));
        }
        out
    }

    fn good_tables() -> Vec<(&'static [u8; 4], usize)> {
        vec![
            (b"cmap", 8),
            (b"head", 54),
            (b"hhea", 36),
            (b"hmtx", 4),
            (b"loca", 4),
            (b"glyf", 4),
        ]
    }

    #[test]
    fn a_complete_truetype_font_is_accepted() {
        assert_eq!(
            validate(&sfnt(TAG_TRUETYPE, &good_tables()), &[16.0, 24.0]),
            Ok(())
        );
    }

    #[test]
    fn a_cff_font_without_glyf_is_accepted() {
        let tables = [
            (b"cmap", 8),
            (b"head", 54),
            (b"hhea", 36),
            (b"hmtx", 4),
            (b"CFF ", 4),
        ];
        assert_eq!(validate(&sfnt(TAG_OTTO, &tables), &[16.0]), Ok(()));
    }

    #[test]
    fn a_collection_opens_its_first_font() {
        let inner = sfnt(TAG_TRUETYPE, &good_tables());
        let mut ttc = Vec::new();
        ttc.extend_from_slice(b"ttcf");
        ttc.extend_from_slice(&0x0001_0000u32.to_be_bytes());
        ttc.extend_from_slice(&1u32.to_be_bytes());
        ttc.extend_from_slice(&16u32.to_be_bytes());
        // Table offsets inside `inner` are relative to the file start, so shift them by 16.
        let mut shifted = inner.clone();
        for index in 0..good_tables().len() {
            let at = 12 + index * 16 + 8;
            let old = u32::from_be_bytes(shifted[at..at + 4].try_into().unwrap());
            shifted[at..at + 4].copy_from_slice(&(old + 16).to_be_bytes());
        }
        ttc.extend_from_slice(&shifted);
        assert_eq!(validate(&ttc, &[16.0]), Ok(()));
    }

    #[test]
    fn missing_required_tables_are_refused() {
        for drop in [b"cmap", b"head", b"hhea", b"hmtx", b"loca"] {
            let tables: Vec<_> = good_tables()
                .into_iter()
                .filter(|(name, _)| *name != drop)
                .collect();
            assert!(
                matches!(
                    validate(&sfnt(TAG_TRUETYPE, &tables), &[16.0]),
                    Err(FontRejection::NotAFont(_))
                ),
                "dropping {drop:?} must refuse"
            );
        }
    }

    #[test]
    fn a_table_past_the_end_is_refused() {
        let mut font = sfnt(TAG_TRUETYPE, &good_tables());
        font.truncate(font.len() - 2);
        assert_eq!(
            validate(&font, &[16.0]),
            Err(FontRejection::NotAFont("a table runs past the data"))
        );
    }

    #[test]
    fn short_head_is_refused() {
        let mut tables = good_tables();
        tables[1].1 = 20;
        assert_eq!(
            validate(&sfnt(TAG_TRUETYPE, &tables), &[16.0]),
            Err(FontRejection::NotAFont("head table too short"))
        );
    }

    #[test]
    fn non_font_bytes_are_refused() {
        assert!(matches!(
            validate(b"PK\x03\x04 not a font at all", &[16.0]),
            Err(FontRejection::NotAFont(_))
        ));
        assert_eq!(validate(&[], &[16.0]), Err(FontRejection::Empty));
    }

    #[test]
    fn sizes_are_bounded() {
        let font = sfnt(TAG_TRUETYPE, &good_tables());
        assert_eq!(validate(&font, &[]), Err(FontRejection::NoSizes));
        assert_eq!(
            validate(&font, &[16.0; 9]),
            Err(FontRejection::TooManySizes)
        );
        assert_eq!(validate(&font, &[f32::NAN]), Err(FontRejection::BadSize));
        assert_eq!(validate(&font, &[2.0]), Err(FontRejection::BadSize));
        assert_eq!(validate(&font, &[400.0]), Err(FontRejection::BadSize));
    }

    #[test]
    fn glyph_ranges_are_zero_terminated_pairs() {
        assert_eq!(GLYPH_RANGES.len() % 2, 1);
        assert_eq!(GLYPH_RANGES[GLYPH_RANGES.len() - 1], 0);
        for pair in GLYPH_RANGES[..GLYPH_RANGES.len() - 1].chunks(2) {
            assert!(pair[0] <= pair[1]);
        }
    }

    #[test]
    fn ui_scale_is_height_over_1080() {
        assert_eq!(ui_scale(1080.0), 1.0);
        assert_eq!(ui_scale(2160.0), 2.0);
        assert_eq!(ui_scale(0.0), 1.0);
        assert_eq!(ui_scale(f32::NAN), 1.0);
    }
}
