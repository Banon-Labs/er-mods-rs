//! Convert a parsed Scaleform [`Tag::DefineFont3`] into a TrueType font in memory.
//!
//! The menu font ships as a `DefineFont3` inside `font/<region>/font.gfx`. imgui loads fonts
//! through stb_truetype (`FontSource::TtfData`), so this module re-encodes the same outlines as a
//! minimal `glyf`-flavoured sfnt: `cmap` (format 4), `glyf`, `head`, `hhea`, `hmtx`, `loca`,
//! `maxp` (version 1.0) and `post` (format 3). stb_truetype needs `cmap`, `head`, `hhea`, `hmtx`
//! and `loca`/`glyf`; `ttf-parser` additionally needs `maxp`. Neither reads `OS/2` or `name`, so
//! they are left out rather than filled with invented values.
//!
//! The output is built from game data at runtime and is never meant to be written into this
//! repository: the font is commercial.
//!
//! # Geometry
//!
//! A `DefineFont3` glyph is a pen walk on a 1024-unit em stored in twips (20480 units per em) with
//! y pointing down. Every coordinate is divided by [`TWIPS_PER_PIXEL`](crate::TWIPS_PER_PIXEL) and
//! negated in y, giving a 1024-unit em with the usual y-up baseline. Rounding is applied to the
//! absolute pen position, never to a delta, so error cannot accumulate along a contour.
//!
//! SWF curved edges are quadratic Béziers, the same primitive TrueType uses, so each one becomes
//! one off-curve control point followed by its on-curve anchor. Contours are split and closed
//! exactly as [`crate::raster`] does: a `MoveTo` starts a new contour, every contour closes back
//! to its start, and contours with fewer than two points are dropped. Fill style indices are
//! ignored there as well, which is the non-zero winding rule stb_truetype applies, so the two
//! renderers agree on which pixels are inside.
//!
//! # Glyph order
//!
//! TrueType glyph 0 is an empty `.notdef` that no code maps to, and glyph `i + 1` is glyph `i` of
//! the `DefineFont3`, so the converted font has one glyph more than the source. The extra glyph
//! is required, not cosmetic: stb_truetype's `stbtt_FindGlyphIndex` returns 0 for "not in the
//! font", and imgui 1.89.2 (`imgui_draw.cpp`, `ImFontAtlasBuildWithStbTruetype`) skips every
//! codepoint for which it returns 0. The menu font's glyph 0 is the space, so mapping it to
//! TrueType glyph 0 would silently drop the space from every atlas.
//!
//! The character map comes from the font's own code table, first occurrence winning, which is the
//! same lookup [`crate::raster::RasterFont::glyph_index`] performs.

use std::fmt;

use crate::{GfxError, GlyphShape, MoveTo, Movie, ShapeRecord, StraightEdge, Tag};

/// TrueType units per em for the converted font (the SWF em of 20480 twips divided by 20).
pub const UNITS_PER_EM: u16 = 1024;

/// The `SymbolClass` export name of the menu font in `font/<region>/font.gfx`.
pub const MENU_FONT_EXPORT: &str = "MenuFont_01";

/// Advance used when the font has no layout block: the inked extent plus this fraction of the
/// em, matching the fallback in [`crate::raster::RasterFont::advance_px`].
const FALLBACK_ADVANCE_GAP_EM: f32 = 0.15;

/// Why a conversion failed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Error {
    /// The movie bytes did not parse.
    Parse(GfxError),
    /// The tag handed to [`define_font3_to_ttf`] is not a `DefineFont3`.
    NotDefineFont3,
    /// No `SymbolClass` entry exports [`MENU_FONT_EXPORT`].
    MenuFontNotExported,
    /// The exported character id names no top-level `DefineFont3`.
    MenuFontMissing { font_id: u16 },
    /// The code table and glyph table have different lengths.
    CodeTableMismatch { glyphs: usize, codes: usize },
    /// The font has no glyphs, or more than a `u16` glyph id can address.
    GlyphCount(usize),
    /// A glyph has more points than `glyf` can index.
    TooManyPoints { glyph: usize },
    /// A coordinate does not fit in a TrueType `i16`.
    CoordinateOverflow { glyph: usize },
    /// The format 4 `cmap` subtable would exceed its `u16` length field.
    CmapTooLarge,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Parse(e) => write!(f, "gfx parse failed: {e}"),
            Error::NotDefineFont3 => write!(f, "tag is not a DefineFont3"),
            Error::MenuFontNotExported => {
                write!(f, "no SymbolClass entry exports {MENU_FONT_EXPORT}")
            }
            Error::MenuFontMissing { font_id } => {
                write!(
                    f,
                    "{MENU_FONT_EXPORT} names character {font_id}, which is not a DefineFont3"
                )
            }
            Error::CodeTableMismatch { glyphs, codes } => {
                write!(f, "{glyphs} glyphs but {codes} character codes")
            }
            Error::GlyphCount(n) => write!(f, "glyph count {n} is not representable"),
            Error::TooManyPoints { glyph } => write!(f, "glyph {glyph} has too many points"),
            Error::CoordinateOverflow { glyph } => {
                write!(f, "glyph {glyph} has a coordinate outside i16")
            }
            Error::CmapTooLarge => write!(f, "format 4 cmap subtable exceeds 65535 bytes"),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Error::Parse(e) => Some(e),
            _ => None,
        }
    }
}

impl From<GfxError> for Error {
    fn from(e: GfxError) -> Self {
        Error::Parse(e)
    }
}

/// Parse a `.gfx` movie, find the `DefineFont3` exported as [`MENU_FONT_EXPORT`], and convert it.
pub fn menu_font_ttf(gfx_bytes: &[u8]) -> Result<Vec<u8>, Error> {
    let movie = Movie::parse(gfx_bytes)?;
    let font_id = movie
        .tags
        .iter()
        .find_map(|tag| match tag {
            Tag::SymbolClass { symbols, .. } => symbols
                .iter()
                .find(|(_, name)| name == MENU_FONT_EXPORT)
                .map(|(id, _)| *id),
            _ => None,
        })
        .ok_or(Error::MenuFontNotExported)?;
    let font = movie
        .tags
        .iter()
        .find(|tag| matches!(tag, Tag::DefineFont3 { font_id: id, .. } if *id == font_id))
        .ok_or(Error::MenuFontMissing { font_id })?;
    define_font3_to_ttf(font)
}

/// Convert one `DefineFont3` tag into TrueType bytes loadable by stb_truetype.
pub fn define_font3_to_ttf(font: &Tag) -> Result<Vec<u8>, Error> {
    let Tag::DefineFont3 {
        glyphs,
        codes,
        layout,
        ..
    } = font
    else {
        return Err(Error::NotDefineFont3);
    };
    if codes.len() != glyphs.len() {
        return Err(Error::CodeTableMismatch {
            glyphs: glyphs.len(),
            codes: codes.len(),
        });
    }
    // Source glyphs plus the leading `.notdef`.
    let num_glyphs = glyphs.len() + 1;
    if glyphs.is_empty() || num_glyphs > u16::MAX as usize {
        return Err(Error::GlyphCount(glyphs.len()));
    }

    let mut outlines = Vec::with_capacity(num_glyphs);
    outlines.push(Outline::empty());
    for (i, g) in glyphs.iter().enumerate() {
        outlines.push(Outline::from_glyph(g, i)?);
    }

    let em = f32::from(UNITS_PER_EM);
    let advances: Vec<u16> = std::iter::once(UNITS_PER_EM / 2)
        .chain(outlines[1..].iter().enumerate().map(|(i, o)| {
            let units = match layout {
                Some(l) if i < l.advance.len() => twips_to_units(i32::from(l.advance[i])) as f32,
                _ => match o.bbox {
                    Some(b) => f32::from(b.x_max) + FALLBACK_ADVANCE_GAP_EM * em,
                    None => 0.25 * em,
                },
            };
            units.round().clamp(0.0, f32::from(u16::MAX)) as u16
        }))
        .collect();

    let font_bbox = outlines
        .iter()
        .filter_map(|o| o.bbox)
        .reduce(BBox::union)
        .unwrap_or_default();
    let (ascender, descender, line_gap) = match layout {
        Some(l) => (
            twips_to_units(i32::from(l.ascent)),
            -twips_to_units(i32::from(l.descent)),
            twips_to_units(i32::from(l.leading)),
        ),
        None => (i32::from(font_bbox.y_max), i32::from(font_bbox.y_min), 0),
    };
    let clamp16 = |v: i32| v.clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16;

    let (glyf, loca) = build_glyf_loca(&outlines);
    let max_points = outlines.iter().map(|o| o.points.len()).max().unwrap_or(0) as u16;
    let max_contours = outlines
        .iter()
        .map(|o| o.end_points.len())
        .max()
        .unwrap_or(0) as u16;

    let tables = [
        (*b"cmap", build_cmap(codes)?),
        (*b"glyf", glyf),
        (
            *b"head",
            build_head(font_bbox, /* index_to_loc_format = long */ 1),
        ),
        (
            *b"hhea",
            build_hhea(&HheaFields {
                ascender: clamp16(ascender),
                descender: clamp16(descender),
                line_gap: clamp16(line_gap),
                outlines: &outlines,
                advances: &advances,
            }),
        ),
        (*b"hmtx", build_hmtx(&outlines, &advances)),
        (*b"loca", loca),
        (
            *b"maxp",
            build_maxp(num_glyphs as u16, max_points, max_contours),
        ),
        (*b"post", build_post()),
    ];
    Ok(assemble_sfnt(&tables))
}

/// SWF twips to TrueType units, rounded.
fn twips_to_units(v: i32) -> i32 {
    (v as f32 / crate::TWIPS_PER_PIXEL_F32).round() as i32
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct BBox {
    x_min: i16,
    y_min: i16,
    x_max: i16,
    y_max: i16,
}

impl BBox {
    fn union(a: BBox, b: BBox) -> BBox {
        BBox {
            x_min: a.x_min.min(b.x_min),
            y_min: a.y_min.min(b.y_min),
            x_max: a.x_max.max(b.x_max),
            y_max: a.y_max.max(b.y_max),
        }
    }
}

/// One glyph as TrueType points: `(x, y, on_curve)` in y-up units, plus the index of each
/// contour's last point.
struct Outline {
    points: Vec<(i16, i16, bool)>,
    end_points: Vec<u16>,
    bbox: Option<BBox>,
}

impl Outline {
    fn empty() -> Outline {
        Outline {
            points: Vec::new(),
            end_points: Vec::new(),
            bbox: None,
        }
    }

    fn from_glyph(glyph: &GlyphShape, index: usize) -> Result<Outline, Error> {
        let mut contours: Vec<Vec<(i32, i32, bool)>> = Vec::new();
        let mut cur: Vec<(i32, i32, bool)> = Vec::new();
        let (mut pen_x, mut pen_y) = (0i32, 0i32);
        let to_pt = |x: i32, y: i32, on: bool| (twips_to_units(x), -twips_to_units(y), on);
        for rec in &glyph.records {
            match rec {
                ShapeRecord::StyleChange {
                    move_to: Some(MoveTo { dx, dy, .. }),
                    ..
                } => {
                    close_contour(&mut cur, &mut contours);
                    pen_x = *dx;
                    pen_y = *dy;
                    cur.push(to_pt(pen_x, pen_y, true));
                }
                ShapeRecord::StyleChange { .. } => {}
                ShapeRecord::StraightEdge { edge, .. } => {
                    let (dx, dy) = match edge {
                        StraightEdge::General { dx, dy } => (*dx, *dy),
                        StraightEdge::Horizontal { dx } => (*dx, 0),
                        StraightEdge::Vertical { dy } => (0, *dy),
                    };
                    pen_x += dx;
                    pen_y += dy;
                    push_on_curve(&mut cur, to_pt(pen_x, pen_y, true));
                }
                ShapeRecord::CurvedEdge {
                    control_dx,
                    control_dy,
                    anchor_dx,
                    anchor_dy,
                    ..
                } => {
                    let cx = pen_x + control_dx;
                    let cy = pen_y + control_dy;
                    pen_x = cx + anchor_dx;
                    pen_y = cy + anchor_dy;
                    cur.push(to_pt(cx, cy, false));
                    cur.push(to_pt(pen_x, pen_y, true));
                }
                ShapeRecord::End => break,
            }
        }
        close_contour(&mut cur, &mut contours);

        let mut points = Vec::new();
        let mut end_points = Vec::new();
        for contour in contours {
            for (x, y, on) in contour {
                let fit = |v: i32| {
                    i16::try_from(v).map_err(|_| Error::CoordinateOverflow { glyph: index })
                };
                points.push((fit(x)?, fit(y)?, on));
            }
            let end = u16::try_from(points.len() - 1)
                .map_err(|_| Error::TooManyPoints { glyph: index })?;
            end_points.push(end);
        }
        // Over every point, control points included, which is how `glyf` headers are
        // conventionally computed: always a superset of the drawn curve.
        let bbox = points.iter().fold(None, |acc: Option<BBox>, &(x, y, _)| {
            let p = BBox {
                x_min: x,
                y_min: y,
                x_max: x,
                y_max: y,
            };
            Some(acc.map_or(p, |b| BBox::union(b, p)))
        });
        Ok(Outline {
            points,
            end_points,
            bbox,
        })
    }
}

/// Append an on-curve point unless rounding collapsed it onto the previous one.
fn push_on_curve(cur: &mut Vec<(i32, i32, bool)>, p: (i32, i32, bool)) {
    if cur
        .last()
        .is_some_and(|&(x, y, on)| on && x == p.0 && y == p.1)
    {
        return;
    }
    cur.push(p);
}

/// Finish the current contour. TrueType closes contours implicitly, so a trailing on-curve point
/// equal to the start is dropped; contours with fewer than two points are discarded, as in
/// [`crate::raster`].
fn close_contour(cur: &mut Vec<(i32, i32, bool)>, contours: &mut Vec<Vec<(i32, i32, bool)>>) {
    if cur.len() >= 2 {
        let first = cur[0];
        if let Some(&last) = cur.last()
            && last.2
            && last.0 == first.0
            && last.1 == first.1
        {
            cur.pop();
        }
    }
    if cur.len() >= 2 {
        contours.push(std::mem::take(cur));
    } else {
        cur.clear();
    }
}

// --- table builders -------------------------------------------------------------------------

fn put_u16(out: &mut Vec<u8>, v: u16) {
    out.extend_from_slice(&v.to_be_bytes());
}
fn put_i16(out: &mut Vec<u8>, v: i16) {
    out.extend_from_slice(&v.to_be_bytes());
}
fn put_u32(out: &mut Vec<u8>, v: u32) {
    out.extend_from_slice(&v.to_be_bytes());
}

/// `glyf` plus a long-format `loca`. Each glyph record is padded to four bytes; a glyph with no
/// contours gets a zero-length entry.
fn build_glyf_loca(outlines: &[Outline]) -> (Vec<u8>, Vec<u8>) {
    let mut glyf = Vec::new();
    let mut loca = Vec::with_capacity((outlines.len() + 1) * 4);
    for o in outlines {
        put_u32(&mut loca, glyf.len() as u32);
        let Some(bbox) = o.bbox else { continue };
        if o.end_points.is_empty() {
            continue;
        }
        put_i16(&mut glyf, o.end_points.len() as i16);
        put_i16(&mut glyf, bbox.x_min);
        put_i16(&mut glyf, bbox.y_min);
        put_i16(&mut glyf, bbox.x_max);
        put_i16(&mut glyf, bbox.y_max);
        for &e in &o.end_points {
            put_u16(&mut glyf, e);
        }
        put_u16(&mut glyf, 0); // instructionLength

        let mut flags = Vec::with_capacity(o.points.len());
        let mut xs = Vec::new();
        let mut ys = Vec::new();
        let (mut px, mut py) = (0i32, 0i32);
        for &(x, y, on) in &o.points {
            let mut flag = u8::from(on);
            let dx = i32::from(x) - px;
            let dy = i32::from(y) - py;
            encode_delta(dx, 0x02, 0x10, &mut flag, &mut xs);
            encode_delta(dy, 0x04, 0x20, &mut flag, &mut ys);
            flags.push(flag);
            px = i32::from(x);
            py = i32::from(y);
        }
        glyf.extend_from_slice(&flags);
        glyf.extend_from_slice(&xs);
        glyf.extend_from_slice(&ys);
        while glyf.len() % 4 != 0 {
            glyf.push(0);
        }
    }
    put_u32(&mut loca, glyf.len() as u32);
    (glyf, loca)
}

/// Encode one coordinate delta with the `glyf` short/same flags.
fn encode_delta(d: i32, short_bit: u8, same_or_pos_bit: u8, flag: &mut u8, out: &mut Vec<u8>) {
    if d == 0 {
        *flag |= same_or_pos_bit;
    } else if (-255..=255).contains(&d) {
        *flag |= short_bit;
        if d > 0 {
            *flag |= same_or_pos_bit;
        }
        out.push(d.unsigned_abs() as u8);
    } else {
        // Points are i16, so any delta between two of them fits after wrapping to i16.
        out.extend_from_slice(&(d as i16).to_be_bytes());
    }
}

/// `cmap` with one format 4 subtable shared by the Unicode (0/3) and Windows BMP (3/1) records.
/// `codes[i]` maps to TrueType glyph `i + 1`, past the `.notdef`.
fn build_cmap(codes: &[u16]) -> Result<Vec<u8>, Error> {
    // First occurrence wins, matching `RasterFont::glyph_index`. 0xFFFF is reserved for the
    // terminating segment. The caller has already bounded `codes.len() + 1` to `u16`.
    let mut map: Vec<(u16, u16)> = Vec::with_capacity(codes.len());
    let mut seen = std::collections::HashSet::new();
    for (i, &code) in codes.iter().enumerate() {
        if code != 0xFFFF && seen.insert(code) {
            map.push((code, (i + 1) as u16));
        }
    }
    map.sort_unstable();

    // Runs of consecutive codes. A run whose glyph ids are also consecutive is encoded with
    // idDelta alone; any other run indexes glyphIdArray.
    struct Segment {
        start: u16,
        end: u16,
        delta: u16,
        glyphs: Option<Vec<u16>>,
    }
    let mut segments: Vec<Segment> = Vec::new();
    let mut i = 0;
    while i < map.len() {
        let mut j = i + 1;
        while j < map.len() && map[j].0 == map[j - 1].0 + 1 {
            j += 1;
        }
        let run = &map[i..j];
        let linear = run.windows(2).all(|w| w[1].1 == w[0].1.wrapping_add(1));
        segments.push(Segment {
            start: run[0].0,
            end: run[run.len() - 1].0,
            delta: if linear {
                run[0].1.wrapping_sub(run[0].0)
            } else {
                0
            },
            glyphs: (!linear).then(|| run.iter().map(|&(_, g)| g).collect()),
        });
        i = j;
    }
    segments.push(Segment {
        start: 0xFFFF,
        end: 0xFFFF,
        delta: 1,
        glyphs: None,
    });

    let seg_count = segments.len();
    let glyph_array_len: usize = segments
        .iter()
        .filter_map(|s| s.glyphs.as_ref().map(Vec::len))
        .sum();
    let sub_len = 16 + seg_count * 8 + glyph_array_len * 2;
    let sub_len = u16::try_from(sub_len).map_err(|_| Error::CmapTooLarge)?;
    let seg_count_x2 = (seg_count * 2) as u16;
    let pow2 = 1u16 << (u16::BITS - 1 - (seg_count as u16).leading_zeros());

    let mut sub = Vec::with_capacity(sub_len as usize);
    put_u16(&mut sub, 4);
    put_u16(&mut sub, sub_len);
    put_u16(&mut sub, 0); // language
    put_u16(&mut sub, seg_count_x2);
    put_u16(&mut sub, pow2 * 2); // searchRange
    put_u16(&mut sub, pow2.trailing_zeros() as u16); // entrySelector
    put_u16(&mut sub, seg_count_x2 - pow2 * 2); // rangeShift
    for s in &segments {
        put_u16(&mut sub, s.end);
    }
    put_u16(&mut sub, 0); // reservedPad
    for s in &segments {
        put_u16(&mut sub, s.start);
    }
    for s in &segments {
        put_u16(&mut sub, s.delta);
    }
    // idRangeOffset[k] is a byte offset from its own slot to the segment's first glyphIdArray
    // entry.
    let mut array_cursor = 0usize;
    for (k, s) in segments.iter().enumerate() {
        match &s.glyphs {
            Some(g) => {
                let offset = (seg_count - k + array_cursor) * 2;
                put_u16(&mut sub, offset as u16);
                array_cursor += g.len();
            }
            None => put_u16(&mut sub, 0),
        }
    }
    for g in segments.iter().filter_map(|s| s.glyphs.as_ref()).flatten() {
        put_u16(&mut sub, *g);
    }

    let mut cmap = Vec::with_capacity(4 + 16 + sub.len());
    put_u16(&mut cmap, 0); // version
    put_u16(&mut cmap, 2); // numTables
    let sub_offset = 4 + 2 * 8;
    for (platform, encoding) in [(0u16, 3u16), (3, 1)] {
        put_u16(&mut cmap, platform);
        put_u16(&mut cmap, encoding);
        put_u32(&mut cmap, sub_offset);
    }
    cmap.extend_from_slice(&sub);
    Ok(cmap)
}

/// `head` with a zero `checkSumAdjustment`; [`assemble_sfnt`] patches it.
fn build_head(bbox: BBox, index_to_loc_format: i16) -> Vec<u8> {
    let mut t = Vec::with_capacity(54);
    put_u32(&mut t, 0x0001_0000); // version
    put_u32(&mut t, 0x0001_0000); // fontRevision
    put_u32(&mut t, 0); // checkSumAdjustment
    put_u32(&mut t, 0x5F0F_3CF5); // magicNumber
    put_u16(&mut t, 0x0009); // flags: baseline at y=0, integer ppem
    put_u16(&mut t, UNITS_PER_EM);
    t.extend_from_slice(&[0; 16]); // created, modified
    put_i16(&mut t, bbox.x_min);
    put_i16(&mut t, bbox.y_min);
    put_i16(&mut t, bbox.x_max);
    put_i16(&mut t, bbox.y_max);
    put_u16(&mut t, 0); // macStyle
    put_u16(&mut t, 8); // lowestRecPPEM
    put_i16(&mut t, 2); // fontDirectionHint
    put_i16(&mut t, index_to_loc_format);
    put_i16(&mut t, 0); // glyphDataFormat
    t
}

struct HheaFields<'a> {
    ascender: i16,
    descender: i16,
    line_gap: i16,
    outlines: &'a [Outline],
    advances: &'a [u16],
}

fn build_hhea(f: &HheaFields<'_>) -> Vec<u8> {
    let mut min_lsb = i16::MAX;
    let mut min_rsb = i16::MAX;
    let mut max_extent = i16::MIN;
    for (o, &adv) in f.outlines.iter().zip(f.advances) {
        if let Some(b) = o.bbox {
            min_lsb = min_lsb.min(b.x_min);
            let rsb = i32::from(adv) - i32::from(b.x_max);
            min_rsb = min_rsb.min(rsb.clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16);
            max_extent = max_extent.max(b.x_max);
        }
    }
    if max_extent == i16::MIN {
        (min_lsb, min_rsb, max_extent) = (0, 0, 0);
    }
    let mut t = Vec::with_capacity(36);
    put_u32(&mut t, 0x0001_0000);
    put_i16(&mut t, f.ascender);
    put_i16(&mut t, f.descender);
    put_i16(&mut t, f.line_gap);
    put_u16(&mut t, f.advances.iter().copied().max().unwrap_or(0));
    put_i16(&mut t, min_lsb);
    put_i16(&mut t, min_rsb);
    put_i16(&mut t, max_extent);
    put_i16(&mut t, 1); // caretSlopeRise
    put_i16(&mut t, 0); // caretSlopeRun
    put_i16(&mut t, 0); // caretOffset
    t.extend_from_slice(&[0; 8]); // reserved
    put_i16(&mut t, 0); // metricDataFormat
    put_u16(&mut t, f.advances.len() as u16); // numberOfHMetrics
    t
}

fn build_hmtx(outlines: &[Outline], advances: &[u16]) -> Vec<u8> {
    let mut t = Vec::with_capacity(advances.len() * 4);
    for (o, &adv) in outlines.iter().zip(advances) {
        put_u16(&mut t, adv);
        put_i16(&mut t, o.bbox.map_or(0, |b| b.x_min));
    }
    t
}

fn build_maxp(num_glyphs: u16, max_points: u16, max_contours: u16) -> Vec<u8> {
    let mut t = Vec::with_capacity(32);
    put_u32(&mut t, 0x0001_0000);
    put_u16(&mut t, num_glyphs);
    put_u16(&mut t, max_points);
    put_u16(&mut t, max_contours);
    put_u16(&mut t, 0); // maxCompositePoints
    put_u16(&mut t, 0); // maxCompositeContours
    put_u16(&mut t, 2); // maxZones
    for _ in 0..8 {
        // maxTwilightPoints .. maxComponentDepth: no hinting, no composites.
        put_u16(&mut t, 0);
    }
    t
}

fn build_post() -> Vec<u8> {
    let mut t = Vec::with_capacity(32);
    put_u32(&mut t, 0x0003_0000); // format 3: no glyph names
    put_u32(&mut t, 0); // italicAngle
    put_i16(&mut t, -(UNITS_PER_EM as i16) / 10); // underlinePosition
    put_i16(&mut t, (UNITS_PER_EM / 20) as i16); // underlineThickness
    t.extend_from_slice(&[0; 20]); // isFixedPitch, memory hints
    t
}

fn table_checksum(data: &[u8]) -> u32 {
    data.chunks(4).fold(0u32, |sum, chunk| {
        let mut word = [0u8; 4];
        word[..chunk.len()].copy_from_slice(chunk);
        sum.wrapping_add(u32::from_be_bytes(word))
    })
}

/// Lay out the table directory and tables (already sorted by tag), four-byte aligned, and patch
/// `head.checkSumAdjustment`.
fn assemble_sfnt(tables: &[([u8; 4], Vec<u8>)]) -> Vec<u8> {
    let num_tables = tables.len() as u16;
    let pow2 = 1u16 << (u16::BITS - 1 - num_tables.leading_zeros());
    let mut out = Vec::new();
    put_u32(&mut out, 0x0001_0000);
    put_u16(&mut out, num_tables);
    put_u16(&mut out, pow2 * 16); // searchRange
    put_u16(&mut out, pow2.trailing_zeros() as u16); // entrySelector
    put_u16(&mut out, num_tables * 16 - pow2 * 16); // rangeShift

    let mut offset = 12 + 16 * tables.len();
    let mut head_offset = None;
    for (tag, data) in tables {
        out.extend_from_slice(tag);
        put_u32(&mut out, table_checksum(data));
        put_u32(&mut out, offset as u32);
        put_u32(&mut out, data.len() as u32);
        if tag == b"head" {
            head_offset = Some(offset);
        }
        offset += data.len().next_multiple_of(4);
    }
    for (_, data) in tables {
        out.extend_from_slice(data);
        out.resize(out.len().next_multiple_of(4), 0);
    }
    if let Some(h) = head_offset {
        let adjustment = 0xB1B0_AFBAu32.wrapping_sub(table_checksum(&out));
        out[h + 8..h + 12].copy_from_slice(&adjustment.to_be_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Font3Layout, Rect};

    fn zero_rect() -> Rect {
        Rect {
            nbits: 0,
            x_min: 0,
            x_max: 0,
            y_min: 0,
            y_max: 0,
        }
    }

    fn style_move(dx: i32, dy: i32) -> ShapeRecord {
        ShapeRecord::StyleChange {
            flags: 0,
            move_to: Some(MoveTo {
                num_bits: 0,
                dx,
                dy,
            }),
            fill_style0: None,
            fill_style1: Some(1),
            line_style: None,
            new_styles: None,
        }
    }

    fn line(dx: i32, dy: i32) -> ShapeRecord {
        ShapeRecord::StraightEdge {
            num_bits: 0,
            edge: StraightEdge::General { dx, dy },
        }
    }

    /// Two glyphs in twips: 'A' is a 400x700 box above the baseline with a box-shaped hole wound
    /// the other way, 'B' is a single quadratic bump. Code 'C' is left unmapped.
    fn synthetic_font() -> Tag {
        let a = GlyphShape {
            num_fill_bits: 1,
            num_line_bits: 0,
            records: vec![
                style_move(0, 0),
                line(0, -14000),
                line(8000, 0),
                line(0, 14000),
                line(-8000, 0),
                style_move(2000, -2000),
                line(4000, 0),
                line(0, -10000),
                line(-4000, 0),
                line(0, 10000),
                ShapeRecord::End,
            ],
        };
        let b = GlyphShape {
            num_fill_bits: 1,
            num_line_bits: 0,
            records: vec![
                style_move(0, 0),
                ShapeRecord::CurvedEdge {
                    num_bits: 0,
                    control_dx: 4000,
                    control_dy: -8000,
                    anchor_dx: 4000,
                    anchor_dy: 8000,
                },
                line(-8000, 0),
                ShapeRecord::End,
            ],
        };
        Tag::DefineFont3 {
            font_id: 1,
            flags: 0,
            language_code: 0,
            font_name: b"Synthetic\0".to_vec(),
            offsets: vec![0, 0, 0],
            glyphs: vec![a, b],
            codes: vec!['A' as u16, 'B' as u16],
            layout: Some(Font3Layout {
                ascent: 16000,
                descent: 4480,
                leading: 0,
                advance: vec![9000, 9400],
                bounds: vec![zero_rect(), zero_rect()],
                kernings: Vec::new(),
            }),
            force_long: false,
        }
    }

    #[test]
    fn rejects_non_font_tags() {
        assert_eq!(define_font3_to_ttf(&Tag::End), Err(Error::NotDefineFont3));
    }

    #[test]
    fn synthetic_font_parses_with_ttf_parser() {
        let bytes = define_font3_to_ttf(&synthetic_font()).unwrap();
        let face = ttf_parser::Face::parse(&bytes, 0).unwrap();
        assert_eq!(face.number_of_glyphs(), 3, "two glyphs plus .notdef");
        assert_eq!(face.units_per_em(), 1024);
        assert_eq!(face.ascender(), 800);
        assert_eq!(face.descender(), -224);

        let a = face.glyph_index('A').unwrap();
        let b = face.glyph_index('B').unwrap();
        assert_eq!((a.0, b.0), (1, 2));
        assert!(face.glyph_index('C').is_none());
        assert_eq!(face.glyph_hor_advance(a), Some(450));
        assert_eq!(face.glyph_hor_advance(b), Some(470));

        struct Count(usize, usize);
        impl ttf_parser::OutlineBuilder for Count {
            fn move_to(&mut self, _: f32, _: f32) {
                self.0 += 1;
            }
            fn line_to(&mut self, _: f32, _: f32) {}
            fn quad_to(&mut self, _: f32, _: f32, _: f32, _: f32) {
                self.1 += 1;
            }
            fn curve_to(&mut self, _: f32, _: f32, _: f32, _: f32, _: f32, _: f32) {}
            fn close(&mut self) {}
        }
        let mut count = Count(0, 0);
        let bbox = face.outline_glyph(a, &mut count).unwrap();
        assert_eq!((count.0, count.1), (2, 0), "two contours, no curves");
        assert_eq!(
            (bbox.x_min, bbox.y_min, bbox.x_max, bbox.y_max),
            (0, 0, 400, 700)
        );

        let mut count = Count(0, 0);
        let bbox = face.outline_glyph(b, &mut count).unwrap();
        assert_eq!((count.0, count.1), (1, 1), "one contour with one quadratic");
        assert_eq!(bbox.x_min, 0);
        assert_eq!(bbox.x_max, 400);
        assert_eq!(bbox.y_min, 0);
    }

    #[test]
    fn checksum_adjustment_balances_file() {
        let bytes = define_font3_to_ttf(&synthetic_font()).unwrap();
        assert_eq!(table_checksum(&bytes), 0xB1B0_AFBA);
    }

    #[test]
    fn cmap_handles_non_linear_runs_and_duplicates() {
        // Source glyphs 0..4 are TrueType glyphs 1..5. 'a','b','c' land on 3,1,2 (a non-linear
        // run), the duplicate 'a' at source glyph 3 loses, and 'x','y' form a linear run.
        let codes = ['b', 'c', 'a', 'a', 'x', 'y'].map(|c| c as u16);
        let cmap = build_cmap(&codes).unwrap();
        let sub = ttf_parser::cmap::Subtable4::parse(&cmap[20..]).unwrap();
        assert_eq!(sub.glyph_index('a' as u32).map(|g| g.0), Some(3));
        assert_eq!(sub.glyph_index('b' as u32).map(|g| g.0), Some(1));
        assert_eq!(sub.glyph_index('c' as u32).map(|g| g.0), Some(2));
        assert_eq!(sub.glyph_index('x' as u32).map(|g| g.0), Some(5));
        assert_eq!(sub.glyph_index('y' as u32).map(|g| g.0), Some(6));
        assert_eq!(sub.glyph_index('d' as u32), None);
    }
}
