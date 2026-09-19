//! The one thing a chosen invasion-spawn point carries besides the catalog entry: where it is in
//! physics space.
//!
//! This module used to rank points as well -- nearest, next in catalog order, first in another
//! area -- for the F7/F8/F9 hotkeys. Those keys and their helpers were removed on 2026-09-15:
//! invasion locations are map markers rather than fast-travel destinations, so every press was
//! declined before the ranking ran, and the world map's own pins are the surface that replaced
//! them. Choosing a pin is the map's job and the map does not need a ranking from here.
//!
//! # Why the type stayed when the ranking went
//!
//! [`crate::warp::resolve_target`] returns one, and the arrival oracle compares the settled
//! read-back against its `world_position` to decide whether a warp arrived or merely mislanded.
//! That is the whole remaining use.
//!
//! # Fail-closed by construction
//!
//! `ConvertBlockCoordsToPhysicsCoords` (@ `0x14061e120`, byte-checked `MATCH ... shift 0`)
//! returns `false` when the target block's world info is not resident. Those targets must never
//! become [`ResolvedTarget`]s: a point that cannot be placed is a point that must not be warped
//! to, so the conversion stays at the call site and a failure is dropped rather than defaulted.

use crate::invasion_warp::InvasionWarpTarget;

/// A catalog target whose physics-space position is known.
///
/// Built by the caller from a successful `ConvertBlockCoordsToPhysicsCoords` call; a failed
/// conversion must be dropped, not defaulted.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ResolvedTarget {
    /// The catalog entry this came from, block-local position and all.
    pub target: InvasionWarpTarget,
    /// Physics-space `[x, y, z]`, as the engine's own conversion produced it.
    pub world_position: [f32; 3],
}

impl ResolvedTarget {
    #[must_use]
    pub const fn new(target: InvasionWarpTarget, world_position: [f32; 3]) -> Self {
        Self {
            target,
            world_position,
        }
    }

    /// Ordering-independent identity, forwarded from the catalog entry.
    #[must_use]
    pub const fn stable_id(&self) -> u64 {
        self.target.stable_id()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::invasion_warp::BlockKey;

    /// Every other test in this module ranked targets, and went with the ranking on 2026-09-15.
    /// This one is what remains worth asserting: identity is the catalog entry's, not a second
    /// one invented here, so a resolved target and the catalog entry it came from can never
    /// disagree about which point they are.
    #[test]
    fn identity_is_forwarded_from_the_catalog_entry() {
        let entry = InvasionWarpTarget::new(BlockKey::from_parts(60, 34, 51, 0), 7, [1.0; 3], 0.0);
        let resolved = ResolvedTarget::new(entry, [4.0, 5.0, 6.0]);
        assert_eq!(resolved.stable_id(), entry.stable_id());
        assert_eq!(resolved.world_position, [4.0, 5.0, 6.0]);
    }
}
