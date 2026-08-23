//! FEASIBILITY PROBE for a sixth Quit-tab row (`Item_2_1`).
//!
//! Applies the shipped five-cell edit table plus one authored `PlaceObject2` and checks that the
//! result is a complete 2x3 grid. The new tag's bytes were produced by re-deriving the SWF `MATRIX`
//! bit packing and confirming the encoder reproduces `Item_1_0`, `Item_1_1` and `Item_2_0`
//! byte-for-byte first, exactly as `options_02_040_quit5_edits.rs` documents for the fifth cell.

mod common;

use er_game_base::fnv1a::fnv1a64;
use er_gfx::edit::{EditOp, TagEdit, apply_edits};
use er_gfx::options_02_040::{
    OPTIONS_02_040_QUIT5_EDITS, VANILLA_WIN_FNV1A64, VANILLA_WIN_LEN, is_known_vanilla_win,
    measure_grid,
};
use er_gfx::{Movie, Tag};

const QUIT_GAME_SPRITE_ID: u16 = 138;

/// `Item_2_1`: depth 19, char 129, translate-only matrix tx = +4780 (same column as `Item_1_1`),
/// ty = 6700 (same row as `Item_2_0`).
const ITEM_2_1: &[u8] = &[
    0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x13, 0x00, 0x81, 0x00, 0x1c, 0x95, 0x63, 0x45, 0x80,
    0x49, 0x74, 0x65, 0x6d, 0x5f, 0x32, 0x5f, 0x31, 0x00,
];

/// The anchor every added cell is inserted after, copied from the shipped table.
const PLAYER_INFO_ANCHOR: &[u8] = &[
    0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50,
    0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00,
];

fn placed_names(movie: &Movie, sprite_id: u16) -> Vec<String> {
    movie
        .tags
        .iter()
        .filter_map(|tag| match tag {
            Tag::DefineSprite { id, tags, .. } if *id == sprite_id => Some(tags),
            _ => None,
        })
        .flatten()
        .filter_map(|child| match child {
            Tag::PlaceObject2 { name, .. } => name.clone(),
            _ => None,
        })
        .collect()
}

#[test]
fn sixth_cell_completes_the_grid() {
    let Some(vanilla) = common::read_vanilla_or_skip(
        "win/02_040_optionsetting.gfx",
        VANILLA_WIN_LEN,
        VANILLA_WIN_FNV1A64,
        fnv1a64,
        is_known_vanilla_win,
    ) else {
        return;
    };

    let mut movie = Movie::parse(&vanilla).expect("vanilla parses");
    // Exactly as a fourth entry appended to the shipped table would be applied.
    let mut edits: Vec<TagEdit> = OPTIONS_02_040_QUIT5_EDITS.to_vec();
    edits.push(TagEdit {
        sprite_id: Some(QUIT_GAME_SPRITE_ID),
        code: 26,
        old_tag: PLAYER_INFO_ANCHOR,
        new_tag: Some(ITEM_2_1),
        op: EditOp::InsertAfter,
    });
    apply_edits(&mut movie, &edits).expect("six-cell edits apply");
    let out = movie.write().expect("derived movie writes");

    let derived = Movie::parse(&out).expect("derived movie re-parses");
    let names = placed_names(&derived, QUIT_GAME_SPRITE_ID);
    for cell in [
        "Item_0_0", "Item_0_1", "Item_1_0", "Item_1_1", "Item_2_0", "Item_2_1",
    ] {
        assert!(
            names.iter().any(|n| n == cell),
            "missing {cell} in {names:?}"
        );
    }
    let has_cell = |row: u32, col: u32| names.iter().any(|n| *n == format!("Item_{row}_{col}"));
    assert_eq!(measure_grid(has_cell), (2, 3));
    eprintln!(
        "QUIT6 derived: len={} fnv1a64=0x{:016x} cells={names:?}",
        out.len(),
        fnv1a64(&out)
    );
}
