//! A layer's stage-local mask (design 7.4 and 8): which pixels of its stage it applies to,
//! evaluated by `er_mask` in `shaders/stage_ctx.hlsli` from what the stage's pass already binds.
//!
//! | Mask | Stages | Text |
//! |---|---|---|
//! | Everything | all | `everything` |
//! | Geometry, Sky | Lighting | `geometry`, `sky` |
//! | Depth range in metres | Lighting | `depth:0..30m` |
//! | Normal facing, 0 edge-on to 1 facing the camera | Lighting | `facing:0..0.3` |
//! | Luminance range | all | `luma:0.5..2` |
//! | Screen rectangle, 0 to 1 across the screen | all | `rect:0,0..0.5,1` |
//!
//! A `!` before the mask inverts it, and `~2px` anywhere in the selector feathers its edge over
//! that many pixels; Geometry and Sky are exact per pixel and take no feather. This is the
//! screen-mask term of the selector text a layer's `select` holds; the family and instance terms
//! (`family:`, `pass:`, the capsules) arrive with P3 and P4, and until then a term the editor
//! does not know yet is reported and left out, so the selector applies more widely than written
//! rather than not at all.
//!
//! # In the parameter block
//!
//! The slot header's last component is the mask word: the kind in the low four bits, the invert
//! flag at bit 4, and the feather in eighths of a pixel from bit 5, a whole number small enough
//! to be exact in a float. The slot's register at `er_p[ER_P_LAYER_MASKS + slot]` holds the
//! bounds. Changing a mask is therefore a parameter write, never a recompile.

use super::LayerStage;

/// What a mask selects by.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum MaskKind {
    Everything = 0,
    Geometry = 1,
    Sky = 2,
    Depth = 3,
    Facing = 4,
    Luma = 5,
    Rect = 6,
}

impl MaskKind {
    pub const ALL: [Self; 7] = [
        Self::Everything,
        Self::Geometry,
        Self::Sky,
        Self::Depth,
        Self::Facing,
        Self::Luma,
        Self::Rect,
    ];

    /// The word in the selector text, before any `:`.
    #[must_use]
    pub const fn key(self) -> &'static str {
        match self {
            Self::Everything => "everything",
            Self::Geometry => "geometry",
            Self::Sky => "sky",
            Self::Depth => "depth",
            Self::Facing => "facing",
            Self::Luma => "luma",
            Self::Rect => "rect",
        }
    }

    /// The name the panel's mask combo shows.
    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Everything => "Everything",
            Self::Geometry => "Geometry",
            Self::Sky => "Sky",
            Self::Depth => "Depth range",
            Self::Facing => "Normal facing",
            Self::Luma => "Luminance range",
            Self::Rect => "Screen rectangle",
        }
    }

    /// Can a layer at `stage` use it? Geometry, Sky, Depth and Facing need the depth buffer and
    /// the G-buffer normal, which only the Lighting pass binds until the resolved plane of P2.
    #[must_use]
    pub const fn available_at(self, stage: LayerStage) -> bool {
        match self {
            Self::Everything | Self::Luma | Self::Rect => true,
            Self::Geometry | Self::Sky | Self::Depth | Self::Facing => {
                matches!(stage, LayerStage::Lighting)
            }
        }
    }

    /// Does the feather apply? Geometry and Sky are exact per pixel.
    #[must_use]
    pub const fn feathers(self) -> bool {
        matches!(self, Self::Depth | Self::Facing | Self::Luma | Self::Rect)
    }
}

/// Bits of the mask word, as `stage_ctx.hlsli` reads them.
pub const KIND_BITS: u32 = 15;
pub const INVERT_BIT: u32 = 16;
pub const FEATHER_SHIFT: u32 = 5;
/// The feather is stored in steps of one eighth of a pixel.
pub const FEATHER_STEPS: f32 = 8.0;
/// The widest feather, in pixels.
pub const MAX_FEATHER: f32 = 64.0;

// The three fields of the word do not overlap, and the widest word is a whole number a float
// holds exactly.
const _: () = assert!(INVERT_BIT > KIND_BITS && INVERT_BIT < 1 << FEATHER_SHIFT);
const _: () = assert!((((MAX_FEATHER * FEATHER_STEPS) as u32) << FEATHER_SHIFT) < 1 << 24);

/// One layer's mask.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Mask {
    pub kind: MaskKind,
    pub invert: bool,
    /// Pixels over which the edge fades, 0 to [`MAX_FEATHER`].
    pub feather: f32,
    /// A range's low and high ends in `[0]` and `[1]`; a rectangle's `x0, y0, x1, y1`.
    pub bounds: [f32; 4],
}

impl Mask {
    pub const EVERYTHING: Self = Self {
        kind: MaskKind::Everything,
        invert: false,
        feather: 0.0,
        bounds: [0.0; 4],
    };

    /// The slot header's last component.
    #[must_use]
    pub fn word(&self) -> f32 {
        let feather = (self.feather.clamp(0.0, MAX_FEATHER) * FEATHER_STEPS).round() as u32;
        let invert = if self.invert { INVERT_BIT } else { 0 };
        let word = (self.kind as u32) | invert | (feather << FEATHER_SHIFT);
        word as f32
    }

    /// The slot's mask register, as words.
    #[must_use]
    pub fn register(&self) -> [u32; 4] {
        self.bounds.map(f32::to_bits)
    }

    /// This mask, or Everything when `stage` cannot evaluate it.
    #[must_use]
    pub fn at_stage(self, stage: LayerStage) -> Self {
        if self.kind.available_at(stage) {
            self
        } else {
            Self::EVERYTHING
        }
    }

    /// The selector text for this mask: what [`parse`] reads back to it.
    #[must_use]
    pub fn render(&self) -> String {
        let mut out = String::new();
        if self.invert {
            out.push('!');
        }
        out.push_str(self.kind.key());
        let [a, b, c, d] = self.bounds;
        match self.kind {
            MaskKind::Depth => out.push_str(&format!(":{a}..{b}m")),
            MaskKind::Facing | MaskKind::Luma => out.push_str(&format!(":{a}..{b}")),
            MaskKind::Rect => out.push_str(&format!(":{a},{b}..{c},{d}")),
            MaskKind::Everything | MaskKind::Geometry | MaskKind::Sky => {}
        }
        if self.feather > 0.0 && self.kind.feathers() {
            out.push_str(&format!(" ~{}px", self.feather));
        }
        out
    }
}

/// `lo..hi`, both numbers.
fn range(text: &str) -> Result<[f32; 2], String> {
    let (lo, hi) = text
        .split_once("..")
        .ok_or_else(|| format!("`{text}` is not a range like 0..10"))?;
    let number = |part: &str| {
        part.trim()
            .parse::<f32>()
            .ok()
            .filter(|v| v.is_finite())
            .ok_or_else(|| format!("`{part}` is not a number"))
    };
    let (lo, hi) = (number(lo)?, number(hi)?);
    Ok(if lo <= hi { [lo, hi] } else { [hi, lo] })
}

/// `x,y`, both numbers.
fn point(text: &str) -> Result<[f32; 2], String> {
    let (x, y) = text
        .split_once(',')
        .ok_or_else(|| format!("`{text}` is not a corner like 0.5,1"))?;
    let number = |part: &str| {
        part.trim()
            .parse::<f32>()
            .ok()
            .filter(|v| v.is_finite())
            .ok_or_else(|| format!("`{part}` is not a number"))
    };
    Ok([number(x)?, number(y)?])
}

/// One screen-mask term without its `!`.
fn term(text: &str) -> Result<Option<(MaskKind, [f32; 4])>, String> {
    let (key, value) = text.split_once(':').unwrap_or((text, ""));
    let Some(kind) = MaskKind::ALL.into_iter().find(|kind| kind.key() == key) else {
        return Ok(None);
    };
    let bounds = match kind {
        MaskKind::Everything | MaskKind::Geometry | MaskKind::Sky => {
            if !value.is_empty() {
                return Err(format!("`{key}` takes no value"));
            }
            [0.0; 4]
        }
        MaskKind::Depth => {
            let [lo, hi] = range(value.trim_end_matches('m'))?;
            [lo.max(0.0), hi.max(0.0), 0.0, 0.0]
        }
        MaskKind::Facing | MaskKind::Luma => {
            let [lo, hi] = range(value)?;
            [lo, hi, 0.0, 0.0]
        }
        MaskKind::Rect => {
            let (from, to) = value
                .split_once("..")
                .ok_or_else(|| format!("`{value}` is not a rectangle like 0,0..0.5,1"))?;
            let ([x0, y0], [x1, y1]) = (point(from)?, point(to)?);
            [x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)]
        }
    };
    Ok(Some((kind, bounds)))
}

/// The mask a layer's selector text names, and a warning for each part of the text that could
/// not be used. An empty selector is Everything. Never fails: a term it cannot read costs that
/// term, and the rest of the selector still applies.
#[must_use]
pub fn parse(text: &str) -> (Mask, Vec<String>) {
    let mut mask = Mask::EVERYTHING;
    let mut warnings = Vec::new();
    let mut seen_mask = false;
    let spaced = text.replace('&', " & ");
    for word in spaced.split_whitespace() {
        if word == "&" {
            continue;
        }
        if let Some(feather) = word.strip_prefix('~') {
            match feather.trim_end_matches("px").parse::<f32>() {
                Ok(px) if px.is_finite() && px >= 0.0 => mask.feather = px.min(MAX_FEATHER),
                _ => warnings.push(format!("`{word}` is not a feather like ~2px")),
            }
            continue;
        }
        let (invert, body) = match word.strip_prefix('!') {
            Some(body) => (true, body),
            None => (false, word),
        };
        match term(body) {
            Ok(Some((kind, bounds))) if !seen_mask => {
                seen_mask = true;
                mask.kind = kind;
                mask.invert = invert;
                mask.bounds = bounds;
            }
            Ok(Some(_)) => warnings.push(format!(
                "`{word}`: a selector has one screen mask, and `{}` came first",
                mask.render()
            )),
            Ok(None) => warnings.push(format!(
                "`{word}` is not a screen mask (everything, geometry, sky, depth:, facing:, \
                 luma:, rect:); family and instance terms come later, so it is left out"
            )),
            Err(why) => warnings.push(format!("`{word}`: {why}")),
        }
    }
    (mask, warnings)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_word_layout_matches_stage_ctx() {
        let stage_ctx = include_str!("../../shaders/stage_ctx.hlsli");
        let defines: Vec<(String, u32)> = stage_ctx
            .lines()
            .filter_map(|line| line.trim().strip_prefix("#define ER_MASK_"))
            .filter_map(|rest| {
                let (name, value) = rest.split_once(' ')?;
                Some((name.to_owned(), value.trim().parse().ok()?))
            })
            .collect();
        let mut expected: Vec<(String, u32)> = MaskKind::ALL
            .iter()
            .map(|kind| (kind.key().to_ascii_uppercase(), *kind as u32))
            .collect();
        expected.extend([
            ("KIND_BITS".to_owned(), KIND_BITS),
            ("INVERT".to_owned(), INVERT_BIT),
            ("FEATHER_SHIFT".to_owned(), FEATHER_SHIFT),
            ("FEATHER_STEPS".to_owned(), FEATHER_STEPS as u32),
        ]);
        assert_eq!(defines, expected);
        assert!(MaskKind::ALL.iter().all(|kind| (*kind as u32) <= KIND_BITS));
    }

    #[test]
    fn the_word_is_exact_in_a_float() {
        let widest = Mask {
            kind: MaskKind::Rect,
            invert: true,
            feather: MAX_FEATHER,
            bounds: [0.0; 4],
        };
        let word = widest.word();
        assert!(word < 16_777_216.0);
        let bits = word as u32;
        assert_eq!(bits & KIND_BITS, MaskKind::Rect as u32);
        assert_ne!(bits & INVERT_BIT, 0);
        assert_eq!((bits >> FEATHER_SHIFT) as f32 / FEATHER_STEPS, MAX_FEATHER);
        assert_eq!(Mask::EVERYTHING.word(), 0.0);
    }

    #[test]
    fn the_design_examples_parse() {
        let (mask, warnings) = parse("depth:0..10m");
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(
            (mask.kind, mask.bounds[..2].to_vec()),
            (MaskKind::Depth, vec![0.0, 10.0])
        );
        let (mask, warnings) = parse("!luma:0.5..2 ~2px");
        assert!(warnings.is_empty(), "{warnings:?}");
        assert!(mask.invert);
        assert_eq!(mask.feather, 2.0);
        assert_eq!(mask.render(), "!luma:0.5..2 ~2px");
        let (mask, _) = parse("rect:0.5,1..0,0");
        assert_eq!(mask.bounds, [0.0, 0.0, 0.5, 1.0]);
        assert_eq!(parse("").0, Mask::EVERYTHING);
        assert_eq!(parse("everything").0, Mask::EVERYTHING);
    }

    #[test]
    fn every_mask_renders_back_to_itself() {
        for text in [
            "everything",
            "geometry",
            "!sky",
            "depth:0..30m ~1.5px",
            "facing:0..0.3",
            "luma:0.25..4",
            "!rect:0.1,0.2..0.9,0.8 ~3px",
        ] {
            let (mask, warnings) = parse(text);
            assert!(warnings.is_empty(), "{text}: {warnings:?}");
            assert_eq!(mask.render(), text);
            assert_eq!(parse(&mask.render()).0, mask);
        }
    }

    #[test]
    fn later_terms_are_reported_and_left_out() {
        let (mask, warnings) = parse("depth:0..30m & family:C[*] !M[Water]* pass:gbuf,fwd ~2px");
        assert_eq!(mask.kind, MaskKind::Depth);
        assert_eq!(mask.feather, 2.0);
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        let (mask, warnings) = parse("sky depth:0..1 luma:x..1");
        assert_eq!(mask.kind, MaskKind::Sky);
        assert_eq!(warnings.len(), 2, "{warnings:?}");
    }

    #[test]
    fn lighting_masks_fall_back_to_everything_elsewhere() {
        let (depth, _) = parse("!depth:0..10m");
        assert_eq!(depth.at_stage(LayerStage::Lighting), depth);
        for stage in [
            LayerStage::Albedo,
            LayerStage::TonemapPre,
            LayerStage::TonemapPost,
        ] {
            assert_eq!(depth.at_stage(stage), Mask::EVERYTHING);
            let (rect, _) = parse("rect:0,0..1,0.5");
            assert_eq!(rect.at_stage(stage), rect);
        }
    }
}
