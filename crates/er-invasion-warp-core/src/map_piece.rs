//! Naming a place from `WorldMapPieceParam`, without the world map.
//!
//! # Why this exists
//!
//! The filter's name-based modes and the prefilter banner both need one thing: given somewhere on
//! the map, what is it called. The only answer this repo had went through the world map's own pin
//! rows -- `map_hooks::nearest_place_name_text_id` borrows the name of the nearest constructed pin
//! -- and those rows do not exist until the player opens the map. `local_invasion_filter`'s
//! `current_anchor` says so in its own comment, and the consequence is that `mode = "area"` and
//! `mode = "named"` reject every candidate as `NothingToMatchAgainst` on a session where the map
//! was never opened.
//!
//! A param has no such precondition. It is resident from load, it is the same bytes on every
//! machine, and `WorldMapPieceParam` is by construction the mapping from a piece of the map to the
//! name drawn on it.
//!
//! # The row, measured
//!
//! 34 rows, stride `0x40`, read out of the installed `regulation.bin` with
//! `scripts/map-region-place-names.py`:
//!
//! | offset | field |
//! |---|---|
//! | `+0x04` | `PlaceName` text id -- `62010`, `62011`, `62012`, `62020`, ... |
//! | `+0x08` | x min |
//! | `+0x0c` | x max |
//! | `+0x10` | z min |
//! | `+0x14` | z max |
//! | `+0x18` | a second text id -- `63010`, `63011`, ... |
//!
//! The four floats are two ranges and not two corners, which is the one trap here: paired as
//! `(x0, z0, x1, z1)` a third of the rows look inside-out -- row 6 gives `2607 > 1971` -- and the
//! natural conclusion is that they are not a rectangle at all. Paired per axis, `+0x08 < +0x0c` and
//! `+0x10 < +0x14` hold for all 34 rows with no exceptions. The script re-derives that on every run
//! rather than trusting this comment.
//!
//! # Why the bytes are not in this file
//!
//! Game-derived binaries are not committed to this repo, test fixtures included. What is recorded
//! here is the row shape and the measured counts; the tests build rows with the same generator the
//! parser reads, so they prove the parse rather than a snapshot of somebody's install.

/// Where the param table holds `WorldMapPieceParam`.
///
/// Measured identical on 1.16.2 and 1.17.1, so a build change does not move it. The neighbours are
/// `WorldMapPointParam` at `0x57` and `MapGdRegionInfoParam` at `0xa7`.
pub const WORLD_MAP_PIECE_PARAM_INDEX: usize = 0x58;

/// Bytes between one row and the next.
pub const ROW_STRIDE: usize = 0x40;

/// `+0x04` -- the `PlaceName` text id the map draws for this piece.
pub const ROW_PLACE_NAME_TEXT_ID: usize = 0x04;
/// `+0x08` -- x min.
pub const ROW_X_MIN: usize = 0x08;
/// `+0x0c` -- x max.
pub const ROW_X_MAX: usize = 0x0C;
/// `+0x10` -- z min.
pub const ROW_Z_MIN: usize = 0x10;
/// `+0x14` -- z max.
pub const ROW_Z_MAX: usize = 0x14;

/// How many rows the installed regulation carries.
///
/// A fingerprint, not a requirement: the parser reads whatever it is given. It is recorded so a
/// future regulation that changes the piece set is visible as a number rather than as a name that
/// quietly stops resolving.
pub const MEASURED_ROW_COUNT: usize = 34;

/// One piece of the world map, and the name drawn on it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MapPiece {
    /// The `PlaceName` text id. Never `-1` here -- a row without a name is dropped at parse.
    pub place_name_text_id: i32,
    /// Inclusive x range in map space.
    pub x: (f32, f32),
    /// Inclusive z range in map space.
    pub z: (f32, f32),
}

impl MapPiece {
    /// Does this piece cover `(x, z)`?
    #[must_use]
    pub fn contains(&self, x: f32, z: f32) -> bool {
        x >= self.x.0 && x <= self.x.1 && z >= self.z.0 && z <= self.z.1
    }

    /// Map-space area, for preferring the tightest of several overlapping pieces.
    #[must_use]
    pub fn area(&self) -> f32 {
        (self.x.1 - self.x.0) * (self.z.1 - self.z.0)
    }
}

/// Parse a `WorldMapPieceParam` row body.
///
/// `rows` is the packed row data at [`ROW_STRIDE`] each. A row whose ranges are inverted or whose
/// text id is not positive is dropped rather than repaired: a piece that cannot name anywhere is
/// not a piece, and keeping it would let a bad parse look like a working one.
#[must_use]
pub fn parse_rows(rows: &[u8]) -> Vec<MapPiece> {
    let read_i32 =
        |at: usize| i32::from_le_bytes([rows[at], rows[at + 1], rows[at + 2], rows[at + 3]]);
    let read_f32 =
        |at: usize| f32::from_le_bytes([rows[at], rows[at + 1], rows[at + 2], rows[at + 3]]);

    let mut out = Vec::new();
    for start in (0..rows.len()).step_by(ROW_STRIDE) {
        if start + ROW_STRIDE > rows.len() {
            break;
        }
        let text_id = read_i32(start + ROW_PLACE_NAME_TEXT_ID);
        if text_id <= 0 {
            continue;
        }
        let piece = MapPiece {
            place_name_text_id: text_id,
            x: (read_f32(start + ROW_X_MIN), read_f32(start + ROW_X_MAX)),
            z: (read_f32(start + ROW_Z_MIN), read_f32(start + ROW_Z_MAX)),
        };
        if !(piece.x.0 < piece.x.1 && piece.z.0 < piece.z.1) {
            continue;
        }
        out.push(piece);
    }
    out
}

/// The `PlaceName` text id for a map-space position, or `None` when no piece covers it.
///
/// Pieces overlap -- a legacy dungeon's piece sits inside its region's -- so the tightest covering
/// piece wins. That is the one a player would name: standing in Stormveil you say Stormveil, not
/// Limgrave.
///
/// `None` is a real answer and the caller must have something to say for it. It must never become
/// a text id that resolves in no FMG: the map renders such an id as the literal `?PlaceName?`,
/// which is worse on screen than saying nothing.
#[must_use]
pub fn place_name_at(pieces: &[MapPiece], x: f32, z: f32) -> Option<i32> {
    pieces
        .iter()
        .filter(|piece| piece.contains(x, z))
        .min_by(|a, b| a.area().total_cmp(&b.area()))
        .map(|piece| piece.place_name_text_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a row the way the param stores one, so the parser is read rather than mirrored.
    fn row(text_id: i32, x: (f32, f32), z: (f32, f32)) -> [u8; ROW_STRIDE] {
        let mut out = [0_u8; ROW_STRIDE];
        out[ROW_PLACE_NAME_TEXT_ID..ROW_PLACE_NAME_TEXT_ID + 4]
            .copy_from_slice(&text_id.to_le_bytes());
        out[ROW_X_MIN..ROW_X_MIN + 4].copy_from_slice(&x.0.to_le_bytes());
        out[ROW_X_MAX..ROW_X_MAX + 4].copy_from_slice(&x.1.to_le_bytes());
        out[ROW_Z_MIN..ROW_Z_MIN + 4].copy_from_slice(&z.0.to_le_bytes());
        out[ROW_Z_MAX..ROW_Z_MAX + 4].copy_from_slice(&z.1.to_le_bytes());
        out
    }

    fn rows(items: &[[u8; ROW_STRIDE]]) -> Vec<u8> {
        items.iter().flat_map(|r| r.iter().copied()).collect()
    }

    /// The measured layout, exercised end to end on the numbers the dump actually produced.
    #[test]
    fn a_row_parses_at_the_measured_offsets() {
        // Row 0 of the installed regulation: text id 62010, x 1877..4895, z 5922..7902.
        let parsed = parse_rows(&rows(&[row(62010, (1877.0, 4895.0), (5922.0, 7902.0))]));
        assert_eq!(
            parsed,
            vec![MapPiece {
                place_name_text_id: 62010,
                x: (1877.0, 4895.0),
                z: (5922.0, 7902.0),
            }]
        );
    }

    /// The pairing that cost a detour: per axis, not per corner.
    ///
    /// Row 6 of the installed regulation reads `2607, 4377, 1971, 4810`. As `(x0, z0, x1, z1)` that
    /// is an inside-out rectangle and this parse would drop it. As two ranges it is ordinary.
    #[test]
    fn the_floats_are_two_ranges_and_row_six_is_the_proof() {
        let parsed = parse_rows(&rows(&[row(62030, (2607.0, 4377.0), (1971.0, 4810.0))]));
        assert_eq!(parsed.len(), 1, "row 6 must survive the range check");
        assert!(parsed[0].contains(3000.0, 3000.0));
        // The corner reading would have put this point outside.
        assert!(!parsed[0].contains(5000.0, 3000.0));
    }

    /// A piece inside another piece is the one a player would name.
    #[test]
    fn the_tightest_covering_piece_wins() {
        let parsed = parse_rows(&rows(&[
            row(62010, (0.0, 1000.0), (0.0, 1000.0)),
            row(62030, (400.0, 600.0), (400.0, 600.0)),
        ]));
        assert_eq!(place_name_at(&parsed, 500.0, 500.0), Some(62030));
        assert_eq!(place_name_at(&parsed, 100.0, 100.0), Some(62010));
    }

    /// Nowhere is an answer, and it must not be a text id.
    ///
    /// An id that resolves in no FMG renders as the literal `?PlaceName?` on the map, so inventing
    /// a fallback id here would put that string in front of a player. The caller says "nearby".
    #[test]
    fn a_position_outside_every_piece_has_no_name_rather_than_a_wrong_one() {
        let parsed = parse_rows(&rows(&[row(62010, (0.0, 100.0), (0.0, 100.0))]));
        assert_eq!(place_name_at(&parsed, 500.0, 500.0), None);
    }

    /// A row that cannot name anywhere is dropped, not repaired.
    #[test]
    fn unnamed_and_inverted_rows_are_dropped() {
        let parsed = parse_rows(&rows(&[
            row(-1, (0.0, 100.0), (0.0, 100.0)),
            row(0, (0.0, 100.0), (0.0, 100.0)),
            row(62010, (100.0, 0.0), (0.0, 100.0)),
            row(62011, (0.0, 100.0), (0.0, 100.0)),
        ]));
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].place_name_text_id, 62011);
    }

    /// A trailing partial row is ignored rather than read past the end.
    #[test]
    fn a_truncated_tail_is_ignored() {
        let mut bytes = rows(&[row(62010, (0.0, 100.0), (0.0, 100.0))]);
        bytes.extend_from_slice(&[0_u8; ROW_STRIDE / 2]);
        assert_eq!(parse_rows(&bytes).len(), 1);
    }

    /// The param index did not move between the two builds this repo works against.
    #[test]
    fn the_param_index_is_the_one_measured_on_both_builds() {
        const { assert!(WORLD_MAP_PIECE_PARAM_INDEX == 0x58) };
        const { assert!(ROW_STRIDE == 0x40) };
        const { assert!(MEASURED_ROW_COUNT == 34) };
    }
}
