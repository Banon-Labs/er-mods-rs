//! Widening the search outward, one query round at a time.
//!
//! # The constraint that shapes all of this
//!
//! A Steam lobby-list string filter is an equality test on one value, and several filters `and`
//! together. So a single `RequestLobbyList` can ask for exactly one location; asking for two
//! matches nobody, which is why `lobby_publish::hunt_refusal` declines to narrow when more than
//! one block is marked.
//!
//! What the transport forbids in one query it permits across many. The filter value is recomputed
//! on every `RequestLobbyList`, and Seamless re-queries on a loop, so successive rounds can name
//! successive tiles. This module decides which tile each round asks for.
//!
//! # Why it counts out loud
//!
//! Rotating silently would make "no invasions found" mean nothing: the player cannot tell an empty
//! ring from a ring that has three tiles left to try. That is the same ambiguity `hunt_refusal`
//! exists to prevent, moved from configuration into timing, so it gets the same treatment -- every
//! step reports which tile it is asking for and how far through the ring it is, and the caller
//! turns that into a sentence naming the place.
//!
//! # Order
//!
//! Centre first, then outward by Chebyshev ring, so the common case -- somebody in your own tile --
//! is as fast as it is today and widening only costs rounds when the centre is empty. Within a ring
//! the order is stable rather than clever: a player who watches the count climb twice should see
//! the same sequence both times.

use crate::invasion_warp::BlockKey;

/// The overworld grid pitch, in metres.
///
/// Read out of `CS::WorldMapAreaConverter::ConvertMsbCoordsToMapCoords`, which adds
/// `(blockByte - converterBlock) * DAT_1429ce8b4` to a position before scaling it into map space;
/// that constant is `256.0`. Recorded here because it is the reason a neighbouring tile is a
/// neighbouring *place* rather than an arbitrary id: the grid is regular and one step is 256m.
pub const OVERWORLD_TILE_METRES: f32 = 256.0;

/// How far out the ring may reach, in tiles.
///
/// Three rings is 48 neighbours, which at one tile per query round is already a long search. The
/// cap exists so a misconfigured radius cannot turn into a rotation nobody can sit through.
pub const MAX_RADIUS: u8 = 3;

/// One step of the widening search.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Step {
    /// The block this round should ask Steam for.
    pub block: BlockKey,
    /// How many steps have been taken including this one, starting at 1.
    pub ordinal: usize,
    /// How many steps the ring holds in total.
    pub total: usize,
}

impl Step {
    /// True when this is the player's own tile, the first thing tried.
    #[must_use]
    pub fn is_centre(&self) -> bool {
        self.ordinal == 1
    }
}

/// Every tile to try, centre first then outward, for a search anchored at `centre`.
///
/// Only overworld tiles have neighbours worth asking for: a legacy dungeon's block id encodes a
/// dungeon and a floor rather than a position on a grid, so stepping its bytes produces a
/// different dungeon, not a nearby place. For those the ring is the centre alone, which degrades
/// to exactly today's behaviour.
///
/// Neighbours are clamped rather than wrapped. A tile at the edge of the grid has fewer
/// neighbours, and asking for a wrapped one would be asking for somewhere across the map.
#[must_use]
pub fn ring(centre: BlockKey, radius: u8) -> Vec<BlockKey> {
    let mut out = vec![centre];
    if !is_overworld(centre) {
        return out;
    }
    let radius = radius.min(MAX_RADIUS);
    let (cx, cz) = (i32::from(centre.block()), i32::from(centre.region()));
    for distance in 1..=i32::from(radius) {
        for dz in -distance..=distance {
            for dx in -distance..=distance {
                // Only the shell of this ring: the interior was covered by a smaller distance.
                if dx.abs() != distance && dz.abs() != distance {
                    continue;
                }
                let (x, z) = (cx + dx, cz + dz);
                if !(0..=255).contains(&x) || !(0..=255).contains(&z) {
                    continue;
                }
                out.push(BlockKey::from_parts(
                    centre.area(),
                    x as u8,
                    z as u8,
                    centre.index(),
                ));
            }
        }
    }
    out
}

/// True when this block is a tile on the overworld grid.
///
/// Areas 60 and 61 are the Lands Between and the Shadow realm; every other area is a legacy
/// dungeon, a cave or an interior, where the block and region bytes are not grid coordinates.
#[must_use]
pub fn is_overworld(block: BlockKey) -> bool {
    matches!(block.area(), 60 | 61)
}

/// A search that widens on its own, and can say where it is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SearchRing {
    tiles: Vec<BlockKey>,
    next: usize,
}

impl SearchRing {
    /// Start a search anchored at `centre`.
    #[must_use]
    pub fn new(centre: BlockKey, radius: u8) -> Self {
        Self {
            tiles: ring(centre, radius),
            next: 0,
        }
    }

    /// The tile the next query round should ask for, or `None` when the ring is exhausted.
    ///
    /// Exhaustion is a real answer and the caller must act on it: it is the moment the ladder
    /// either gives up or, when the player has opted in, drops the filter and asks everywhere.
    pub fn advance(&mut self) -> Option<Step> {
        let block = *self.tiles.get(self.next)?;
        self.next += 1;
        Some(Step {
            block,
            ordinal: self.next,
            total: self.tiles.len(),
        })
    }

    /// How many tiles this ring holds.
    #[must_use]
    pub fn len(&self) -> usize {
        self.tiles.len()
    }

    /// True when the ring holds nothing to ask for. Never true in practice -- a ring always has
    /// its centre -- but `clippy::len_without_is_empty` is right that a length wants a companion.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.tiles.is_empty()
    }

    /// True when every tile has been asked for.
    #[must_use]
    pub fn exhausted(&self) -> bool {
        self.next >= self.tiles.len()
    }

    /// Begin again at the centre, for a search the player restarted.
    pub fn rewind(&mut self) {
        self.next = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn overworld(x: u8, z: u8) -> BlockKey {
        BlockKey::from_parts(60, x, z, 0)
    }

    /// The centre is always tried first, so the common case costs exactly one round.
    #[test]
    fn the_centre_comes_first() {
        let mut search = SearchRing::new(overworld(51, 36), 1);
        let first = search.advance().expect("a ring always has its centre");
        assert_eq!(first.block, overworld(51, 36));
        assert!(first.is_centre());
        assert_eq!(first.ordinal, 1);
    }

    /// One ring around a tile is its eight neighbours, and nothing else.
    #[test]
    fn a_radius_of_one_is_the_eight_neighbours() {
        let tiles = ring(overworld(51, 36), 1);
        assert_eq!(tiles.len(), 9, "the centre plus eight");
        assert_eq!(tiles[0], overworld(51, 36));
        for (x, z) in [
            (50, 35),
            (51, 35),
            (52, 35),
            (50, 36),
            (52, 36),
            (50, 37),
            (51, 37),
            (52, 37),
        ] {
            assert!(tiles.contains(&overworld(x, z)), "missing {x},{z}");
        }
    }

    /// A second ring adds only its own shell, never a tile the first already covered.
    #[test]
    fn rings_do_not_repeat_a_tile() {
        let tiles = ring(overworld(51, 36), 2);
        assert_eq!(tiles.len(), 25, "5x5 with no duplicates");
        let mut seen = tiles.clone();
        seen.sort_unstable();
        seen.dedup();
        assert_eq!(seen.len(), tiles.len());
    }

    /// The grid edge clamps rather than wraps: a wrapped neighbour is somewhere else entirely.
    #[test]
    fn the_grid_edge_clamps_instead_of_wrapping() {
        let tiles = ring(overworld(0, 0), 1);
        assert_eq!(tiles.len(), 4, "a corner has three neighbours");
        assert!(!tiles.iter().any(|t| t.block() == 255 || t.region() == 255));
    }

    /// A legacy dungeon's bytes are not coordinates, so it has no neighbours to ask for.
    #[test]
    fn a_legacy_block_has_no_ring() {
        let stormveil = BlockKey::from_parts(10, 0, 0, 0);
        assert!(!is_overworld(stormveil));
        assert_eq!(ring(stormveil, 3), vec![stormveil]);
    }

    /// The count the banner shows is the ring's own, and it reaches the end exactly once.
    #[test]
    fn the_count_runs_to_the_end_and_stops() {
        let mut search = SearchRing::new(overworld(51, 36), 1);
        let mut ordinals = Vec::new();
        while let Some(step) = search.advance() {
            assert_eq!(step.total, 9);
            ordinals.push(step.ordinal);
        }
        assert_eq!(ordinals, (1..=9).collect::<Vec<_>>());
        assert!(search.exhausted());
        assert_eq!(search.advance(), None, "exhaustion is stable");
    }

    /// Restarting a search starts at the player's own tile again.
    #[test]
    fn rewinding_returns_to_the_centre() {
        let mut search = SearchRing::new(overworld(51, 36), 1);
        search.advance();
        search.advance();
        search.rewind();
        assert!(search.advance().is_some_and(|step| step.is_centre()));
    }

    /// The radius cap is enforced where the ring is built, not left to the caller.
    #[test]
    fn a_radius_beyond_the_cap_is_clamped() {
        assert_eq!(
            ring(overworld(51, 36), 200).len(),
            ring(overworld(51, 36), MAX_RADIUS).len()
        );
    }

    /// The tile pitch is the engine's own constant, not a guess.
    #[test]
    fn the_tile_pitch_is_the_measured_constant() {
        const { assert!(OVERWORLD_TILE_METRES == 256.0) };
    }
}
