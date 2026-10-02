//! `er_gfx::ttf` against the real menu font. The font is commercial and game-derived, so it is
//! read from the local extraction and the converted TrueType bytes stay in memory; the test
//! skips when the extraction is absent.

use std::path::PathBuf;

use er_gfx::raster::RasterFont;
use er_gfx::{Movie, Tag};

/// Default local extraction of `font/eu_std/font.gfx`. Overridden by `ER_FONT_GFX_PATH`.
const DEFAULT_FONT_GFX: &str =
    "/home/banon/er-extract/LOOK_HERE_ALL_ASSETS_20260713/font/eu_std/font.gfx";

fn font_gfx_or_skip() -> Option<Vec<u8>> {
    let path = match std::env::var("ER_FONT_GFX_PATH") {
        Ok(v) if !v.trim().is_empty() => PathBuf::from(v),
        _ => PathBuf::from(DEFAULT_FONT_GFX),
    };
    if !path.exists() {
        eprintln!(
            "SKIP: {} not present; menu font test skipped",
            path.display()
        );
        return None;
    }
    Some(std::fs::read(&path).expect("read font.gfx"))
}

#[test]
fn menu_font_converts_to_a_loadable_ttf() {
    let Some(gfx) = font_gfx_or_skip() else {
        return;
    };
    let ttf = er_gfx::ttf::menu_font_ttf(&gfx).expect("convert menu font");
    let face = ttf_parser::Face::parse(&ttf, 0).expect("ttf-parser accepts the output");
    // 910 source glyphs plus the `.notdef` at TrueType glyph 0.
    assert_eq!(face.number_of_glyphs(), 911);
    assert_eq!(face.units_per_em(), er_gfx::ttf::UNITS_PER_EM);

    let movie = Movie::parse(&gfx).unwrap();
    let tag = movie
        .tags
        .iter()
        .find(|t| matches!(t, Tag::DefineFont3 { .. }))
        .unwrap();
    let raster = RasterFont::from_define_font3(tag).unwrap();

    // Every code the DefineFont3 maps resolves to the same glyph through the TrueType cmap.
    let Tag::DefineFont3 { codes, layout, .. } = tag else {
        unreachable!()
    };
    // TrueType glyph `i + 1` is source glyph `i`. The menu font's source glyph 0 is the space,
    // which is exactly the glyph that would vanish from an imgui atlas without the `.notdef`.
    assert_eq!(codes[0], u16::from(b' '));
    for &code in codes {
        let Some(ch) = char::from_u32(u32::from(code)) else {
            continue;
        };
        let expected = raster.glyph_index(ch).map(|g| g as u16 + 1);
        assert_eq!(
            face.glyph_index(ch).map(|g| g.0),
            expected,
            "cmap for U+{code:04X}"
        );
    }

    // Advances are the layout table divided by 20.
    let layout = layout.as_ref().expect("menu font has a layout block");
    let a = face.glyph_index('A').expect("'A' is mapped");
    let expected_adv = (f32::from(layout.advance[a.0 as usize - 1]) / 20.0).round() as u16;
    assert_eq!(face.glyph_hor_advance(a), Some(expected_adv));

    // The outline bbox of 'A' matches the rasterizer's ink box at 1 px per TrueType unit.
    struct Sink;
    impl ttf_parser::OutlineBuilder for Sink {
        fn move_to(&mut self, _: f32, _: f32) {}
        fn line_to(&mut self, _: f32, _: f32) {}
        fn quad_to(&mut self, _: f32, _: f32, _: f32, _: f32) {}
        fn curve_to(&mut self, _: f32, _: f32, _: f32, _: f32, _: f32, _: f32) {}
        fn close(&mut self) {}
    }
    let bbox = face
        .outline_glyph(a, &mut Sink)
        .expect("'A' has an outline");
    let bitmap = raster.rasterize('A', 1.0 / 20.0).expect("'A' rasterizes");
    let raster_box = (
        bitmap.left,
        -(bitmap.top + bitmap.height as i32),
        bitmap.left + bitmap.width as i32,
        -bitmap.top,
    );
    let ttf_box = (
        i32::from(bbox.x_min),
        i32::from(bbox.y_min),
        i32::from(bbox.x_max),
        i32::from(bbox.y_max),
    );
    let close = |a: i32, b: i32| (a - b).abs() <= 1;
    assert!(
        close(raster_box.0, ttf_box.0)
            && close(raster_box.1, ttf_box.1)
            && close(raster_box.2, ttf_box.2)
            && close(raster_box.3, ttf_box.3),
        "'A' bbox: raster {raster_box:?} vs ttf {ttf_box:?}"
    );
}
