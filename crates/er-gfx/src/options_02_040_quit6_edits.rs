// Provenance. Edits 1-2 were emitted by `scripts/gfx_tag_diff.py --emit-rust` from a vanilla /
// edited pair of local extracted game files under target/gfx-work (that pair carried the original
// four-cell Quit tab):
//   A (vanilla): len=44007 sha256=170996c2376bb14675fe1bb308c3ce82c28bec0deafbfc8e40bd8fef0e99e4b4
//   B (4-cell) : len=44057 sha256=15bdbb85d90192b1440f0b0b1aa2893da52510a4692e8ccddd2b6dde4ed0bd6e
//
// Edit 3 -- the `Item_2_0` cell that makes the Quit tab five rows -- is authored here rather than
// diffed out of a hand-edited movie, because it is one `PlaceObject2` whose every field is copied
// from the cell directly above it. Its bytes were produced by re-deriving the SWF `MATRIX`
// bit-packing from the two cells already in this table and confirming the encoder reproduces both
// of them byte-for-byte before it was used to emit a third:
//
//   flags 0x26  = HasCharacter | HasMatrix | HasName, exactly as edits 1-2
//   depth 0x12  = 18, the next depth after Item_1_0 (16) and Item_1_1 (17)
//   char  0x81  = 129, the same Quit-tab cell component the other four cells place
//   matrix      = translate-only, 14 translate bits: tx = -3979 twips (identical to Item_1_0, so
//                 the new row is left-aligned under it), ty = 6700 twips -- one more 1100-twip
//                 (55 px) step down the column, the same step that separates row 0 (ty = 4500)
//                 from row 1 (ty = 5600).
//
// Edit 4 -- the `Item_2_1` cell that makes the Quit tab six rows -- was authored the same way and
// held to the same gate: the matrix encoder was re-derived and made to reproduce edits 2, 3 and the
// `Item_2_0` of edit 4-as-it-then-was byte-for-byte before it was allowed to emit a fourth cell.
//
//   flags 0x26  = HasCharacter | HasMatrix | HasName, exactly as the others
//   depth 0x13  = 19, the next depth after Item_2_0 (18)
//   char  0x81  = 129, the same Quit-tab cell component every other cell places
//   matrix      = translate-only, 14 translate bits: tx = +4780 twips (identical to Item_1_1, so
//                 the new cell is right-aligned under it), ty = 6700 twips (identical to Item_2_0,
//                 so it shares that row) -- the corner the 2x3 grid was missing.
//
// Six is the easy case, and it replaced the hard one. Five items in a 2x3 grid left a ragged last
// row: the native measure loop probed an `Item_2_1` that was not there, the mouse hit test walked a
// sixth cell that could never be hovered, and reaching the bottom row by pad relied on
// `FUN_14073b0c0` walking back along the row after index 5 was refused. All of that reasoning was
// correct and is now moot -- the grid is full, `cols * rows == SetItemCount == 6`, and no cell the
// engine probes for is absent.
//
// The correctness gate is not this comment: `apply_edits` refuses any `new_tag` that does not parse
// as exactly one tag and re-serialize to these exact bytes, and `crates/er-gfx/tests/options_02_040.rs`
// asserts the derived movie's fingerprint, its six grid-cell names, and the geometry the native
// measure loop reads off them.
pub const OPTIONS_02_040_QUIT6_EDITS: &[TagEdit] = &[
    TagEdit {
        sprite_id: None,
        code: 22,
        old_tag: &[0xbf, 0x05, 0x39, 0x00, 0x00, 0x00, 0x90, 0x00, 0x74, 0x38, 0x4f, 0x1e, 0x69, 0xe4, 0x96, 0xc0, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x0d, 0xd0, 0xe1, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x71, 0x19, 0xe3, 0x10, 0xd6, 0xe5, 0xd1, 0xf0, 0x80, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x15, 0xcf, 0x1e, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x72, 0xe9, 0xe3, 0x10, 0xd6, 0xe2, 0x30, 0x00],
        new_tag: Some(&[0xbf, 0x05, 0x39, 0x00, 0x00, 0x00, 0x90, 0x00, 0x74, 0x38, 0x4f, 0x1e, 0x69, 0xe4, 0x96, 0xc0, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x0d, 0xd0, 0xe1, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x71, 0x19, 0xe3, 0x10, 0xd6, 0xe5, 0xd1, 0xf0, 0x00, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x15, 0xcf, 0x1e, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x72, 0xe9, 0xe3, 0x10, 0xd6, 0xe2, 0x30, 0x00]),
        op: EditOp::Replace,
    },
    TagEdit {
        sprite_id: Some(138),
        code: 26,
        old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
        new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x10, 0x00, 0x81, 0x00, 0x1d, 0x83, 0xaa, 0xbc, 0x00, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x31, 0x5f, 0x30, 0x00]),
        op: EditOp::InsertAfter,
    },
    TagEdit {
        sprite_id: Some(138),
        code: 26,
        old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
        new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x11, 0x00, 0x81, 0x00, 0x1c, 0x95, 0x62, 0xbc, 0x00, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x31, 0x5f, 0x31, 0x00]),
        op: EditOp::InsertAfter,
    },
    TagEdit {
        sprite_id: Some(138),
        code: 26,
        old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
        new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x12, 0x00, 0x81, 0x00, 0x1d, 0x83, 0xab, 0x45, 0x80, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x32, 0x5f, 0x30, 0x00]),
        op: EditOp::InsertAfter,
    },
    TagEdit {
        sprite_id: Some(138),
        code: 26,
        old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
        new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x13, 0x00, 0x81, 0x00, 0x1c, 0x95, 0x63, 0x45, 0x80, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x32, 0x5f, 0x31, 0x00]),
        op: EditOp::InsertAfter,
    },
];
// 5 edits: 0 removals, 1 replacement, 4 insertions.

// The seventh cell, applied on top of the six above when the Save Game row is armed as a row of
// its own rather than as a relabel of the native first one. Measured 2026-09-20 in a live run
// with `save-game` listed in `er-quit-menu.toml`: the shell cloned the row and reported
// `cols=2 rows=3 navigable_cells=6 item_count=7`, so the row existed with no cell to occupy and
// never reached the screen.
//
// Authored and gated exactly as edits 3 and 4 were: the matrix encoder was re-derived from the
// four cells already in this table and made to reproduce every one of them byte-for-byte before
// it was allowed to emit this one.
//
//   flags 0x26  = HasCharacter | HasMatrix | HasName, as every other cell
//   depth 0x14  = 20, the next depth after Item_2_1 (19)
//   char  0x81  = 129, the same Quit-tab cell component the other six place
//   matrix      = translate-only, 14 translate bits: tx = -3979 twips (identical to Item_1_0 and
//                 Item_2_0, so the new row is left-aligned under the same column), ty = 7800
//                 twips -- one more 1100-twip (55 px) step down, the step that separates every
//                 pair of rows above it.
//
// There is deliberately no `Item_3_1`. Seven items cannot fill a two-column grid, and of the two
// ragged shapes available this is the one the engine already handles: the measure loop probes
// `Item_3_1`, finds nothing, and destructs the invalid value it gets back -- the same exit it
// takes on vanilla. The alternative, an eighth cell whose item index is past `SetItemCount`, puts
// a component on screen that the hit test discards, and a cell that can be hovered but never
// chosen is worse than one that is not there. The module comment in `options_02_040.rs` records
// the three pieces of native behaviour this leans on, measured when the tab was five rows and its
// bottom row was ragged the same way.
pub const OPTIONS_02_040_QUIT7_EXTRA_EDITS: &[TagEdit] = &[TagEdit {
    sprite_id: Some(138),
    code: 26,
    old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
    new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x14, 0x00, 0x81, 0x00, 0x1d, 0x83, 0xab, 0xcf, 0x00, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x33, 0x5f, 0x30, 0x00]),
    op: EditOp::InsertAfter,
}];
// 1 further edit: 0 removals, 0 replacements, 1 insertion.
