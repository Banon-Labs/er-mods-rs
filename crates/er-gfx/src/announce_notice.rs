//! Centre the text on the game's auto-closing announcement banner, and let it draw two lines.
//!
//! Two edits to one movie, applied together by [`with_two_line_notice`]. The centring came first
//! and is described below; [`make_notice_two_line`] carries the second one's own derivation.
//!
//! # Which movie this is, and how that was established rather than guessed
//!
//! `CS::FeSystemAnnounceView` drives `menu:/01_080_EmergencyNotice.gfx`. The name alone is
//! suggestive and not proof, so the identification rests on a match the name cannot fake: the
//! view's display step (`0x1408c48c0`) drives its fade by name, calling
//! `FUN_140749b20(view+0xa50, "FadeIn")` in one state and `"FadeOut"` in another — and this movie
//! declares frame labels `FadeIn`, `Loop` and `fadeOut`, and is the only movie in the menu corpus
//! that mentions "Announce" at all (it also carries `MENU_Announce.tga`).
//!
//! # Why the text sat on the left
//!
//! Its single [`Tag::DefineEditText`] — character [`NOTICE_TEXT_CHARACTER_ID`] — declares
//! `align: 0`, i.e. Left, inside bounds 34,520 twips wide (~1,726 px). A short line like
//! "Rejected Limgrave (elsewhere)" therefore starts at the far left of a box spanning most of the
//! screen, which reads as "stuck to the edge" rather than as a banner.
//!
//! `AutoSize` is clear on this field (`flags2 = 0xa1` = `HasFontClass | HasLayout | UseOutlines`),
//! so the bounds are fixed and do not shrink to the text. That is what makes alignment the correct
//! and sufficient lever: with a fixed box, `align: 2` centres the line inside the same rectangle
//! the game already positioned, so nothing about the banner's placement, size or fade changes —
//! only where the glyphs sit within it. Moving the field instead would have to reproduce that
//! positioning by hand and would break the moment the game moved the banner.
//!
//! # Fail-closed
//!
//! The edit refuses unless it finds exactly the field it expects, still left-aligned. A movie that
//! has already been centred, or a different movie that happens to have an edit text, is left
//! untouched and the caller serves the original bytes.

use crate::{GfxError, Movie, Tag};

/// The movie that owns the announcement banner.
pub const NOTICE_MOVIE_FILE_NAME: &str = "01_080_emergencynotice.gfx";

/// The `DefineEditText` character the banner's line is drawn in.
pub const NOTICE_TEXT_CHARACTER_ID: u16 = 6;

/// `align` values of a `DefineEditText` layout block.
pub const ALIGN_LEFT: u8 = 0;
/// Centre — what the banner is changed to.
pub const ALIGN_CENTER: u8 = 2;

/// The notice field's width in pixels, as the engine computes it.
///
/// Bounds are `x_min = -40`, `x_max = 34_520` twips. The engine's own measurement
/// (`FUN_140d82660`) scales each edge by `0.05` (twips to pixels) and truncates to `int` before
/// subtracting: `(int)(34520 * 0.05) - (int)(-40 * 0.05)` = `1726 - -2` = `1728`.
///
/// Reproduced here rather than approximated because it is the baseline the DLL's blank-banner
/// oracle subtracts: that measurement returns `textWidth - fieldWidth`, so the text's own width is
/// only recoverable by adding this back. Getting it wrong shifts the threshold that decides whether
/// a banner drew anything.
pub const NOTICE_FIELD_WIDTH_PX: i32 = 1_728;

/// `flags2` bit meaning the layout block is present. Without it there is nothing to align.
pub const EDIT_TEXT_HAS_LAYOUT: u8 = 0x20;
/// `flags2` bit meaning the field shrinks to its text. Clear on this field, which is what makes
/// centring meaningful — an auto-sized box has no spare width to centre within.
pub const EDIT_TEXT_AUTO_SIZE: u8 = 0x40;

/// `flags1` bit that lets the field lay out more than one line, so a `\n` in the text breaks it.
///
/// Clear on the vanilla field, which is why a banner could only ever be one line no matter what
/// the DLL wrote into it.
pub const EDIT_TEXT_MULTILINE: u8 = 0x20;
/// `flags1` bit that breaks a long line at the field's own width.
///
/// [`make_notice_two_line`] leaves it clear, and that is the whole reason a two-line banner does
/// not change how a long one-line banner behaves. `FeSystemAnnounceView` scrolls text that
/// overflows the field -- it owns `systemAnnounceScrollBufferTimer` and the overflow measurement
/// the DLL's blank-banner oracle reads -- so a wrapping field would replace the game's own scroll
/// with a silent second line for every long vanilla notice. With wrap off, only an explicit `\n`
/// breaks a line and everything else behaves as it did.
pub const EDIT_TEXT_WORD_WRAP: u8 = 0x40;

/// The vanilla `flags1` of [`NOTICE_TEXT_CHARACTER_ID`]: `HasText | ReadOnly | HasTextColor`.
///
/// The edit refuses anything else, so a movie that already carries a multi-line field -- or a
/// different movie whose char 6 happens to be an edit text -- is left alone.
pub const NOTICE_TEXT_VANILLA_FLAGS1: u8 = 0x8c;

/// The sprite whose single placement draws the banner's background panel.
///
/// `DefineSprite 5` holds one `PlaceObject2` of `DefineSprite 4`, which in turn holds the
/// `MENU_FL_Dialog` image at half scale. Sprite 5's placement is where the panel's on-screen size
/// is decided, and it is the only thing that has to move when the text field grows.
pub const NOTICE_BAR_SPRITE_ID: u16 = 5;
/// The character [`NOTICE_BAR_SPRITE_ID`] places: the panel, pre-scaled to half size.
pub const NOTICE_BAR_CHARACTER_ID: u16 = 4;

/// Half the panel's height in [`NOTICE_BAR_SPRITE_ID`]'s own coordinates, before its scale.
///
/// `MENU_FL_Dialog` is 646x102 px (`DefineExternalImage2` character 3) and `DefineSprite 4` places
/// it at scale `0.5`, translated `(-161.5, -25.5)` px -- so inside sprite 4 it spans `-25.5..25.5`
/// and is centred on the origin. That is what makes the panel's height a pure function of sprite
/// 5's `scale_y`, and what lets this edit re-derive the scale from a target height.
const BAR_HALF_HEIGHT_PX: f64 = 25.5;

/// The vanilla vertical terms of sprite 5's placement, which the edit refuses to overwrite blind.
///
/// `scale_y` is 16.16 fixed point: `70669 / 65536 = 1.0783233642578125`, so the panel is
/// `2 * 25.5 * 1.07832 = 54.994` px tall, centred on `translate_y = 27` twips (1.35 px). Read out
/// of the vanilla movie with `scripts/gfx_display_list.py`.
const BAR_VANILLA_SCALE_Y: i32 = 70_669;
const BAR_VANILLA_TRANSLATE_Y_TWIPS: i32 = 27;

/// The vanilla vertical bounds of the text field, likewise refused if they have moved.
const NOTICE_TEXT_VANILLA_Y_MIN: i32 = -40;
const NOTICE_TEXT_VANILLA_Y_MAX: i32 = 680;

/// How far the field and the panel grow, in twips: one line of `MenuFont_01` at this field's size.
///
/// Measured from the font rather than guessed. `font/eu_std/font.gfx` declares `MenuFont_01` as
/// `DefineFont3` id 1 ("Agmena W1G For Bandai") with `ascent = 20800`, `descent = 8540` and
/// `leading = 8860` on a `1024 * 20` unit em square, and the field's `font_height` is 480 twips
/// (24 px). A line therefore advances
/// `(20800 + 8540 + 8860) / 20480 * 24 = 44.77` px, which rounds up to the 45 px here.
///
/// It is the advance and not the glyph box on purpose: the vanilla 36 px field already holds one
/// line's glyphs (`ascent + descent` is 34.38 px) with a little slack, so adding exactly one line
/// advance keeps that slack and cannot clip the descenders of the second line.
const SECOND_LINE_TWIPS: i32 = 900;

/// Why the notice text could not be centred. Every variant means the movie was left alone.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NoticeError {
    /// The movie did not parse.
    Parse(GfxError),
    /// The movie did not serialise back.
    Write(GfxError),
    /// No `DefineEditText` with [`NOTICE_TEXT_CHARACTER_ID`].
    TextFieldMissing,
    /// The field carries no layout block, so it has no alignment to change.
    NoLayoutBlock,
    /// The field is auto-sized, so it has no spare width and centring would do nothing visible.
    AutoSized,
    /// The field was not left-aligned, so this is not the movie this edit was measured against.
    NotLeftAligned { found: u8 },
    /// The field's `flags1` are not the ones the two-line edit was measured against.
    NotVanillaTextFlags { found: u8 },
    /// The field's vertical bounds have moved, so growing them by a measured amount is guesswork.
    NotVanillaTextBounds { y_min: i32, y_max: i32 },
    /// No `DefineSprite` [`NOTICE_BAR_SPRITE_ID`] placing [`NOTICE_BAR_CHARACTER_ID`] with a
    /// scale matrix, so the panel behind the text cannot be grown with it.
    BarPlacementMissing,
    /// The panel's placement is not the one this edit measured, so its height is unknown.
    NotVanillaBar { scale_y: i32, translate_y: i32 },
}

impl core::fmt::Display for NoticeError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Parse(error) => write!(f, "notice movie did not parse: {error:?}"),
            Self::Write(error) => write!(f, "edited notice movie did not serialise: {error:?}"),
            Self::TextFieldMissing => write!(
                f,
                "no DefineEditText {NOTICE_TEXT_CHARACTER_ID}; this is not the announcement movie \
                 the alignment was measured against"
            ),
            Self::NoLayoutBlock => write!(
                f,
                "DefineEditText {NOTICE_TEXT_CHARACTER_ID} has no layout block, so it has no \
                 alignment to change"
            ),
            Self::AutoSized => write!(
                f,
                "DefineEditText {NOTICE_TEXT_CHARACTER_ID} is auto-sized; it has no spare width to \
                 centre within, so alignment is not the right lever"
            ),
            Self::NotLeftAligned { found } => write!(
                f,
                "DefineEditText {NOTICE_TEXT_CHARACTER_ID} has align {found}, expected \
                 {ALIGN_LEFT} (left); refusing to overwrite an alignment this edit did not measure"
            ),
            Self::NotVanillaTextFlags { found } => write!(
                f,
                "DefineEditText {NOTICE_TEXT_CHARACTER_ID} has flags1 {found:#04x}, expected \
                 {NOTICE_TEXT_VANILLA_FLAGS1:#04x}; this field is not the one the two-line \
                 geometry was measured against"
            ),
            Self::NotVanillaTextBounds { y_min, y_max } => write!(
                f,
                "DefineEditText {NOTICE_TEXT_CHARACTER_ID} spans {y_min}..{y_max} twips, expected \
                 {NOTICE_TEXT_VANILLA_Y_MIN}..{NOTICE_TEXT_VANILLA_Y_MAX}; growing a box whose \
                 height is not the measured one would put the second line anywhere"
            ),
            Self::BarPlacementMissing => write!(
                f,
                "no scaled placement of character {NOTICE_BAR_CHARACTER_ID} inside DefineSprite \
                 {NOTICE_BAR_SPRITE_ID}; the panel behind the text cannot be grown to match it"
            ),
            Self::NotVanillaBar {
                scale_y,
                translate_y,
            } => write!(
                f,
                "the panel is placed with scale_y {scale_y} at translate_y {translate_y}, expected \
                 {BAR_VANILLA_SCALE_Y} at {BAR_VANILLA_TRANSLATE_Y_TWIPS}; refusing to resize a \
                 panel whose current height this edit did not measure"
            ),
        }
    }
}

impl std::error::Error for NoticeError {}

/// Centre the announcement banner's text, in place.
///
/// # Errors
///
/// Returns a [`NoticeError`] and leaves `movie` untouched when any expectation fails.
pub fn center_notice_text(movie: &mut Movie) -> Result<(), NoticeError> {
    let field = movie
        .tags
        .iter_mut()
        .find_map(|tag| match tag {
            Tag::DefineEditText {
                character_id,
                flags2,
                layout,
                ..
            } if *character_id == NOTICE_TEXT_CHARACTER_ID => Some((flags2, layout)),
            _ => None,
        })
        .ok_or(NoticeError::TextFieldMissing)?;
    let (flags2, layout) = field;
    if *flags2 & EDIT_TEXT_HAS_LAYOUT == 0 {
        return Err(NoticeError::NoLayoutBlock);
    }
    if *flags2 & EDIT_TEXT_AUTO_SIZE != 0 {
        return Err(NoticeError::AutoSized);
    }
    let layout = layout.as_mut().ok_or(NoticeError::NoLayoutBlock)?;
    if layout.align != ALIGN_LEFT {
        return Err(NoticeError::NotLeftAligned {
            found: layout.align,
        });
    }
    layout.align = ALIGN_CENTER;
    Ok(())
}

/// Parse `bytes`, centre the notice text, and serialise the result.
///
/// # Errors
///
/// See [`NoticeError`]. On any error the caller should serve the original bytes unchanged.
pub fn with_centered_notice_text(bytes: &[u8]) -> Result<Vec<u8>, NoticeError> {
    let mut movie = Movie::parse(bytes).map_err(NoticeError::Parse)?;
    center_notice_text(&mut movie)?;
    movie.write().map_err(NoticeError::Write)
}

/// Let the banner draw two lines, and grow the panel behind it so the second one lands on it.
///
/// # Why a one-line field could not just be written to with a `\n`
///
/// `CS::FeSystemAnnounceView` hands its text to the widget through a plain variable set
/// (`FUN_14074a000` -> `FUN_140d842a0` -> the movie view's `SetVariable` slot), so the string
/// reaches a Scaleform `TextField` unfiltered and a `\n` in it is already a line break as far as
/// the text engine is concerned. What the vanilla field refuses is the LAYOUT: [`EDIT_TEXT_MULTILINE`]
/// is clear, so there is only ever one line box to put glyphs in.
///
/// # And why the panel has to move with it
///
/// Two lines of `MenuFont_01` at this field's 24 px do not fit the vanilla banner, and the
/// shortfall is not marginal. The panel is 54.99 px tall and the field 36 px; a second line adds
/// 44.77 px of advance (see [`SECOND_LINE_TWIPS`]), so a field grown alone would hang its second
/// line most of the way off the bottom of the art. The panel is therefore grown by exactly the
/// same amount, downward, with its top edge held: every one-line notice -- this mod's and the
/// game's own -- keeps the pixel position it has today, and the change is only visible as empty
/// panel below a short message.
///
/// The panel is authored to be stretched, which is what makes this a resize rather than a rebuild:
/// `MENU_FL_Dialog` carries a [`Tag::DefineScalingGrid`] and is the same image every message box
/// in the game stretches to its own size.
///
/// # Errors
///
/// See [`NoticeError`]. Every variant means the movie was left exactly as it arrived, which leaves
/// a working one-line banner rather than a broken two-line one.
pub fn make_notice_two_line(movie: &mut Movie) -> Result<(), NoticeError> {
    let field = movie
        .tags
        .iter_mut()
        .find_map(|tag| match tag {
            Tag::DefineEditText {
                character_id,
                bounds,
                flags1,
                ..
            } if *character_id == NOTICE_TEXT_CHARACTER_ID => Some((bounds, flags1)),
            _ => None,
        })
        .ok_or(NoticeError::TextFieldMissing)?;
    let (bounds, flags1) = field;
    if *flags1 != NOTICE_TEXT_VANILLA_FLAGS1 {
        return Err(NoticeError::NotVanillaTextFlags { found: *flags1 });
    }
    if bounds.y_min != NOTICE_TEXT_VANILLA_Y_MIN || bounds.y_max != NOTICE_TEXT_VANILLA_Y_MAX {
        return Err(NoticeError::NotVanillaTextBounds {
            y_min: bounds.y_min,
            y_max: bounds.y_max,
        });
    }
    let grown_y_max = NOTICE_TEXT_VANILLA_Y_MAX + SECOND_LINE_TWIPS;
    // The panel first, so a refusal there leaves the field untouched too. Both halves of this edit
    // are one change, and half of it is worse than none: a grown field with the vanilla panel is
    // the overflowing banner the panel resize exists to prevent.
    let (bar_scale_y, bar_translate_y, bar_nbits) = grown_bar_placement()?;
    let bar = movie
        .tags
        .iter_mut()
        .find_map(|tag| match tag {
            Tag::DefineSprite { id, tags, .. } if *id == NOTICE_BAR_SPRITE_ID => Some(tags),
            _ => None,
        })
        .ok_or(NoticeError::BarPlacementMissing)?
        .iter_mut()
        .find_map(|tag| match tag {
            Tag::PlaceObject2 {
                character_id: Some(NOTICE_BAR_CHARACTER_ID),
                matrix: Some(matrix),
                ..
            } if matrix.has_scale => Some(matrix),
            _ => None,
        })
        .ok_or(NoticeError::BarPlacementMissing)?;
    if bar.scale_y != BAR_VANILLA_SCALE_Y || bar.translate_y != BAR_VANILLA_TRANSLATE_Y_TWIPS {
        return Err(NoticeError::NotVanillaBar {
            scale_y: bar.scale_y,
            translate_y: bar.translate_y,
        });
    }
    bar.scale_y = bar_scale_y;
    bar.translate_y = bar_translate_y;
    bar.scale_nbits = bar
        .scale_nbits
        .max(crate::min_signed_nbits(&[bar.scale_x, bar.scale_y]));
    bar.translate_nbits = bar.translate_nbits.max(bar_nbits);
    // The field, now that the panel has agreed to hold it.
    let field = movie
        .tags
        .iter_mut()
        .find_map(|tag| match tag {
            Tag::DefineEditText {
                character_id,
                bounds,
                flags1,
                ..
            } if *character_id == NOTICE_TEXT_CHARACTER_ID => Some((bounds, flags1)),
            _ => None,
        })
        .ok_or(NoticeError::TextFieldMissing)?;
    let (bounds, flags1) = field;
    bounds.y_max = grown_y_max;
    bounds.nbits = bounds.nbits.max(crate::min_signed_nbits(&[
        bounds.x_min,
        bounds.x_max,
        bounds.y_min,
        bounds.y_max,
    ]));
    *flags1 |= EDIT_TEXT_MULTILINE;
    Ok(())
}

/// The panel's `scale_y`, `translate_y` and translate bit width once it has grown by one line.
///
/// Held apart from the edit so the arithmetic can be read on its own, and asserted by
/// `the_grown_panel_keeps_its_top_edge`:
///
/// ```text
///   vanilla half-height  25.5 * 70669/65536            = 27.4972 px
///   vanilla top          1.35 - 27.4972                = -26.1472 px
///   vanilla bottom       1.35 + 27.4972                =  28.8472 px
///   grown bottom         28.8472 + 45                  =  73.8472 px
///   grown half-height    (73.8472 + 26.1472) / 2       =  49.9972 px
///   grown centre         -26.1472 + 49.9972            =  23.85 px  -> 477 twips
///   grown scale_y        49.9972 / 25.5 * 65536        =  128495
/// ```
fn grown_bar_placement() -> Result<(i32, i32, u32), NoticeError> {
    const FIXED_ONE: f64 = 65_536.0;

    let vanilla_half = BAR_HALF_HEIGHT_PX * f64::from(BAR_VANILLA_SCALE_Y) / FIXED_ONE;
    let vanilla_centre =
        f64::from(BAR_VANILLA_TRANSLATE_Y_TWIPS) / f64::from(crate::TWIPS_PER_PIXEL);
    let top = vanilla_centre - vanilla_half;
    let bottom = vanilla_centre
        + vanilla_half
        + f64::from(SECOND_LINE_TWIPS) / f64::from(crate::TWIPS_PER_PIXEL);
    let grown_half = (bottom - top) / 2.0;
    let centre = top + grown_half;
    let scale_y = (grown_half / BAR_HALF_HEIGHT_PX * FIXED_ONE).round() as i32;
    let translate_y = (centre * f64::from(crate::TWIPS_PER_PIXEL)).round() as i32;
    Ok((
        scale_y,
        translate_y,
        crate::min_signed_nbits(&[translate_y]),
    ))
}

/// Parse `bytes`, centre the notice text, let it draw two lines, and serialise the result.
///
/// The two edits are applied together because they are one surface: [`center_notice_text`] decides
/// where a line sits across the panel and [`make_notice_two_line`] decides how many there may be,
/// and a movie carrying one without the other is a banner nobody measured.
///
/// # Errors
///
/// See [`NoticeError`]. On any error the caller should serve the original bytes unchanged.
pub fn with_two_line_notice(bytes: &[u8]) -> Result<Vec<u8>, NoticeError> {
    let mut movie = Movie::parse(bytes).map_err(NoticeError::Parse)?;
    center_notice_text(&mut movie)?;
    make_notice_two_line(&mut movie)?;
    movie.write().map_err(NoticeError::Write)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::EditTextLayout;

    fn field(character_id: u16, flags2: u8, align: Option<u8>) -> Tag {
        Tag::DefineEditText {
            character_id,
            bounds: crate::Rect {
                nbits: 17,
                x_min: -40,
                x_max: 34_520,
                y_min: -40,
                y_max: 680,
            },
            flags1: 0x8c,
            flags2,
            font_id: None,
            font_class: Some("MenuFont_01".to_string()),
            font_height: Some(480),
            text_color: Some([166, 166, 166, 255]),
            max_length: None,
            layout: align.map(|align| EditTextLayout {
                align,
                left_margin: 0,
                right_margin: 0,
                indent: 0,
                leading: 0,
            }),
            variable_name: String::new(),
            initial_text: Some("notice line 1 notice line 2".to_string()),
            force_long: true,
        }
    }

    fn movie_with(tags: Vec<Tag>) -> Movie {
        Movie {
            header: crate::Header {
                version: 11,
                movie_rect_raw: vec![136, 0, 1, 44, 0, 0, 0, 42, 48, 0],
                frame_rate: 7680,
                frame_count: 19,
            },
            tags,
        }
    }

    #[test]
    fn the_notice_field_is_centred() {
        let mut movie = movie_with(vec![
            field(NOTICE_TEXT_CHARACTER_ID, 0xa1, Some(ALIGN_LEFT)),
            Tag::End,
        ]);
        center_notice_text(&mut movie).expect("centres");
        let Tag::DefineEditText {
            layout: Some(layout),
            ..
        } = &movie.tags[0]
        else {
            panic!("edit text");
        };
        assert_eq!(layout.align, ALIGN_CENTER);
    }

    #[test]
    fn nothing_but_the_alignment_changes() {
        // The banner's position, size, colour, font and text all belong to the game. This edit
        // moves glyphs inside a box it does not touch; changing anything else here would move a
        // surface the game positions itself.
        let before = movie_with(vec![
            field(NOTICE_TEXT_CHARACTER_ID, 0xa1, Some(ALIGN_LEFT)),
            Tag::End,
        ]);
        let mut after = before.clone();
        center_notice_text(&mut after).expect("centres");
        let (
            Tag::DefineEditText {
                bounds: before_bounds,
                flags1: before_flags1,
                flags2: before_flags2,
                font_height: before_height,
                text_color: before_color,
                initial_text: before_text,
                layout: Some(before_layout),
                ..
            },
            Tag::DefineEditText {
                bounds: after_bounds,
                flags1: after_flags1,
                flags2: after_flags2,
                font_height: after_height,
                text_color: after_color,
                initial_text: after_text,
                layout: Some(after_layout),
                ..
            },
        ) = (&before.tags[0], &after.tags[0])
        else {
            panic!("edit text");
        };
        assert_eq!(
            before_bounds, after_bounds,
            "the box must not move or resize"
        );
        assert_eq!(before_flags1, after_flags1);
        assert_eq!(before_flags2, after_flags2);
        assert_eq!(before_height, after_height);
        assert_eq!(before_color, after_color);
        assert_eq!(before_text, after_text);
        assert_eq!(before_layout.left_margin, after_layout.left_margin);
        assert_eq!(before_layout.right_margin, after_layout.right_margin);
        assert_eq!(before_layout.indent, after_layout.indent);
        assert_eq!(before_layout.leading, after_layout.leading);
        assert_ne!(before_layout.align, after_layout.align, "except the align");
    }

    #[test]
    fn an_already_centred_field_is_refused_rather_than_rewritten() {
        // Not a no-op on purpose: reaching here means the movie is not the one measured, and
        // quietly succeeding would report a centred banner for a movie nobody checked.
        let mut movie = movie_with(vec![
            field(NOTICE_TEXT_CHARACTER_ID, 0xa1, Some(ALIGN_CENTER)),
            Tag::End,
        ]);
        assert_eq!(
            center_notice_text(&mut movie),
            Err(NoticeError::NotLeftAligned {
                found: ALIGN_CENTER
            })
        );
    }

    #[test]
    fn an_auto_sized_field_is_refused_because_centring_would_do_nothing() {
        let mut movie = movie_with(vec![
            field(
                NOTICE_TEXT_CHARACTER_ID,
                0xa1 | EDIT_TEXT_AUTO_SIZE,
                Some(ALIGN_LEFT),
            ),
            Tag::End,
        ]);
        assert_eq!(center_notice_text(&mut movie), Err(NoticeError::AutoSized));
    }

    #[test]
    fn a_movie_without_the_field_is_refused() {
        let mut movie = movie_with(vec![field(999, 0xa1, Some(ALIGN_LEFT)), Tag::End]);
        assert_eq!(
            center_notice_text(&mut movie),
            Err(NoticeError::TextFieldMissing)
        );
    }

    #[test]
    fn a_field_with_no_layout_block_is_refused() {
        let mut movie = movie_with(vec![
            field(NOTICE_TEXT_CHARACTER_ID, 0xa1 & !EDIT_TEXT_HAS_LAYOUT, None),
            Tag::End,
        ]);
        assert_eq!(
            center_notice_text(&mut movie),
            Err(NoticeError::NoLayoutBlock)
        );
    }

    #[test]
    fn the_align_constants_are_the_swf_ones() {
        // 0 left, 1 right, 2 center, 3 justify. Writing 1 here would shove the banner to the far
        // right, which is a worse version of the bug being fixed.
        assert_eq!(ALIGN_LEFT, 0);
        assert_eq!(ALIGN_CENTER, 2);
    }
}
