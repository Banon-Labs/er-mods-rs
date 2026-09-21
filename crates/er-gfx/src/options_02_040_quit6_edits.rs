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
/// The one edit that is not a cell: it applies whenever any cell is added, and not at all when
/// the tab is left as the vanilla pair.
pub const OPTIONS_02_040_SHAPE_EDIT: &[TagEdit] = &[
    TagEdit {
        sprite_id: None,
        code: 22,
        old_tag: &[0xbf, 0x05, 0x39, 0x00, 0x00, 0x00, 0x90, 0x00, 0x74, 0x38, 0x4f, 0x1e, 0x69, 0xe4, 0x96, 0xc0, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x0d, 0xd0, 0xe1, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x71, 0x19, 0xe3, 0x10, 0xd6, 0xe5, 0xd1, 0xf0, 0x80, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x15, 0xcf, 0x1e, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x72, 0xe9, 0xe3, 0x10, 0xd6, 0xe2, 0x30, 0x00],
        new_tag: Some(&[0xbf, 0x05, 0x39, 0x00, 0x00, 0x00, 0x90, 0x00, 0x74, 0x38, 0x4f, 0x1e, 0x69, 0xe4, 0x96, 0xc0, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x0d, 0xd0, 0xe1, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x71, 0x19, 0xe3, 0x10, 0xd6, 0xe5, 0xd1, 0xf0, 0x00, 0x01, 0x00, 0xff, 0x00, 0x00, 0x00, 0x10, 0x15, 0xcf, 0x1e, 0x69, 0xe5, 0xf1, 0x77, 0x9b, 0x72, 0xe9, 0xe3, 0x10, 0xd6, 0xe2, 0x30, 0x00]),
        op: EditOp::Replace,
    },
];

/// Every cell this module can place beyond the vanilla pair, in item-index order, so a grid of
/// `n` items takes the first `n - 2` of them.
///
/// A host asking for a tab of its own size does not have to know which rows this repo arms, and
/// this table does not have to know either: it is a ladder of cells, and the row set that
/// motivated any particular rung is not part of its contract.
pub const OPTIONS_02_040_CELL_EDITS: &[TagEdit] = &[
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
    // Item_3_0 -- item index 6. tx -3979 (the left column), ty 7800.
    TagEdit {
        sprite_id: Some(138),
        code: 26,
        old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
        new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x14, 0x00, 0x81, 0x00, 0x1d, 0x83, 0xab, 0xcf, 0x00, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x33, 0x5f, 0x30, 0x00]),
        op: EditOp::InsertAfter,
    },
    // Item_3_1 -- item index 7, the corner that makes a full two-by-four. tx +4780, ty 7800.
    TagEdit {
        sprite_id: Some(138),
        code: 26,
        old_tag: &[0xbf, 0x06, 0x14, 0x00, 0x00, 0x00, 0x26, 0x0f, 0x00, 0x89, 0x00, 0x16, 0x00, 0x27, 0x00, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x49, 0x6e, 0x66, 0x6f, 0x00],
        new_tag: Some(&[0xbf, 0x06, 0x13, 0x00, 0x00, 0x00, 0x26, 0x15, 0x00, 0x81, 0x00, 0x1c, 0x95, 0x63, 0xcf, 0x00, 0x49, 0x74, 0x65, 0x6d, 0x5f, 0x33, 0x5f, 0x31, 0x00]),
        op: EditOp::InsertAfter,
    },
];
// One shape replacement, and a ladder of cell insertions taken as a prefix.
//
// The last two rungs were measured 2026-09-20 in a live run with `save-game` listed in
// `er-quit-menu.toml`: at six cells the shell cloned a seventh row and reported
// `cols=2 rows=3 navigable_cells=6 item_count=7`, so the row existed with nowhere to be. With
// `Item_3_0` applied the same dialog reported `cols=2 rows=4 navigable_cells=8 item_count=7` and
// the row was on screen.
//
// Each rung is one `PlaceObject2` differing from its neighbour in three numbers, which is why
// they can be a table rather than a transform per count: `depth` advances by one, `tx` alternates
// between the two native columns (-3979 and +4780 twips), and `ty` steps 1100 twips (55 px) per
// row. Every one of them was held to the same gate -- the matrix encoder re-derived and made to
// reproduce the cells already in the table byte-for-byte before it emitted another.
//
// An odd item count leaves the last row ragged, and that is a supported shape rather than an
// accident: the measure loop probes the absent cell, gets nothing, and takes the same exit it
// takes on vanilla. Asking for one cell more than there are items is the failure to avoid -- a
// component on screen at an index past `SetItemCount` is hoverable and can never be chosen.
//
// The ladder stops where it does because nothing has rendered a cell below `ty = 7800`: whether
// the native panel has room for a further row or clips it is unmeasured, and a rung nobody has
// seen on screen is a guess with a fingerprint attached.

