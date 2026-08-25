//! Which characters count as players, and a tally of what a roster walk actually saw.
//!
//! Deliberately not inside [`crate::game`], which is `#[cfg(windows)]` and therefore never
//! compiled by `cargo test`. The classification below is a list of integers that decides whether
//! anything is drawn at all, and a wrong entry fails in the two worst ways available: silently
//! drawing nothing, or drawing a line to a bloodstain. Putting it here is what lets the host test
//! it without a game.

/// `ChrType` values that are emphatically not another player.
///
/// Everything the game spawns from a map is `Npc`; the ghost kinds are the translucent replays of
/// bloodstains, messages and other people's bonfire animations, which have positions and no
/// bodies.
///
/// The list is what gets EXCLUDED rather than what gets included, on purpose. An allow-list of the
/// phantom types this workspace knows about would silently draw nothing at all if Seamless typed
/// its remote players as something not on it, and "nothing drawn" is indistinguishable from "the
/// navmesh found no route". Failing towards drawing an extra character is a visible, correctable
/// mistake; failing towards drawing nobody is the one that wastes an invasion.
///
/// `Local` (0) is absent for the same reason. The local player is rejected by ADDRESS, which is
/// exact; excluding the type as well would drop a remote player in any session that types them
/// `Local`, which is precisely the possibility the wide sweep exists to survive.
const NON_PLAYER_CHR_TYPES: [i32; 7] = [
    -1, // None
    3,  // Ghost
    4,  // Ghost1
    5,  // Npc
    10, // BloodstainGhost
    11, // BonfireGhost
    14, // MessageGhost
];

/// `ChrType::Npc`, the ordinary map character.
pub(crate) const NPC_CHR_TYPE: i32 = 5;

/// Is this character kind one a route should be drawn to?
pub(crate) fn is_player_kind(chr_type: i32) -> bool {
    !NON_PLAYER_CHR_TYPES.contains(&chr_type)
}

/// Distinct `chr_type` values the census will name before it stops adding new ones.
const MAX_CENSUS_TYPES: usize = 16;

/// What a roster walk saw, so a frame that draws nothing can say why.
///
/// Without this, an invasion that produces no line has three indistinguishable explanations: the
/// roster found nobody, the navmesh refused every request, or the projection dropped every
/// segment. Only the first is answered here, and it is answered by counting rather than by
/// inferring afterwards.
#[derive(Default)]
pub(crate) struct Census {
    /// ChrSets walked, including the inline player set.
    pub(crate) sets: usize,
    /// Characters seen across all of them, live or not.
    pub(crate) characters: usize,
    /// `(chr_type, count)` for every distinct type seen, so an unexpected typing shows up as a
    /// number rather than as silence.
    pub(crate) by_chr_type: Vec<(i32, usize)>,
    /// True when `player_chr_set` yielded nobody and the wider sweep had to run.
    pub(crate) widened: bool,
}

impl Census {
    /// Record one character of `chr_type`.
    pub(crate) fn count(&mut self, chr_type: i32) {
        self.characters += 1;
        if let Some((_, count)) = self
            .by_chr_type
            .iter_mut()
            .find(|(seen, _)| *seen == chr_type)
        {
            *count += 1;
        } else if self.by_chr_type.len() < MAX_CENSUS_TYPES {
            // Bounded: a distinct-value list built from live memory is only as short as that
            // memory is sane, and a log line is not worth an unbounded allocation.
            self.by_chr_type.push((chr_type, 1));
        }
    }
}

impl std::fmt::Display for Census {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "sets={} characters={} widened={} types=[",
            self.sets, self.characters, self.widened
        )?;
        for (index, (chr_type, count)) in self.by_chr_type.iter().enumerate() {
            if index > 0 {
                write!(f, " ")?;
            }
            write!(f, "{chr_type}:{count}")?;
        }
        write!(f, "]")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The named phantom and invader kinds from `ChrType`, every one of which is a real person.
    const PLAYER_KINDS: [i32; 9] = [
        1,  // WhitePhantom
        2,  // Duelist
        8,  // GrayPhantom
        13, // Arena
        15, // BloodyFinger
        16, // Recusant
        17, // BluePhantom
        18, // FesteringBloodyFinger
        0,  // Local -- a remote player typed as one must still be reachable
    ];

    #[test]
    fn every_named_player_kind_is_drawn_to() {
        for kind in PLAYER_KINDS {
            assert!(is_player_kind(kind), "chr_type {kind} should be a target");
        }
    }

    #[test]
    fn map_characters_and_ghosts_are_not_drawn_to() {
        for kind in [NPC_CHR_TYPE, -1, 3, 4, 10, 11, 14] {
            assert!(
                !is_player_kind(kind),
                "chr_type {kind} should not be a target"
            );
        }
    }

    /// The whole point of an exclusion list rather than an allow-list: a value this build has
    /// never seen is a player, not a silent nothing. If Seamless types its remote players 23, the
    /// line still draws.
    #[test]
    fn an_unrecognised_kind_is_treated_as_a_player_rather_than_dropped() {
        for kind in [19, 20, 21, 22, 23, 99, i32::MAX] {
            assert!(
                is_player_kind(kind),
                "an unknown chr_type {kind} must fail towards drawing"
            );
        }
    }

    #[test]
    fn the_local_type_is_not_excluded_because_the_local_player_is_rejected_by_address() {
        const LOCAL: i32 = 0;
        assert!(is_player_kind(LOCAL));
    }

    #[test]
    fn a_census_tallies_each_kind_once_and_totals_them_all() {
        let mut census = Census::default();
        for kind in [5, 5, 5, 1, 16, 1] {
            census.count(kind);
        }
        assert_eq!(census.characters, 6);
        assert_eq!(census.by_chr_type.len(), 3);
        let npcs = census
            .by_chr_type
            .iter()
            .find(|(kind, _)| *kind == 5)
            .map(|(_, count)| *count);
        assert_eq!(npcs, Some(3));
    }

    /// Garbage memory must not turn a diagnostic into an allocation loop.
    #[test]
    fn a_census_stops_naming_new_kinds_once_it_has_enough() {
        let mut census = Census::default();
        for kind in 0..1000 {
            census.count(kind);
        }
        assert_eq!(census.characters, 1000);
        assert_eq!(census.by_chr_type.len(), MAX_CENSUS_TYPES);
    }

    #[test]
    fn a_census_reads_as_one_line() {
        let mut census = Census {
            sets: 2,
            widened: true,
            ..Census::default()
        };
        census.count(5);
        census.count(1);
        assert_eq!(
            census.to_string(),
            "sets=2 characters=2 widened=true types=[5:1 1:1]"
        );
    }
}
