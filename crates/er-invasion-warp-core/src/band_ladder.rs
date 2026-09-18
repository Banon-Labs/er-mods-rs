//! Climb Seamless's matchmaking band when the nearby ring comes back empty.
//!
//! # The field
//!
//! Seamless publishes one lobby-data value of the form `<level band>_<weapon band>` and filters on
//! it with `k_ELobbyComparisonEqual`. Its key name is hashed per build, so nothing here may match
//! on the key: [`looks_like_band`] recognises the value's shape instead, which is stable across
//! builds in a way the hash is not.
//!
//! The encoding was pinned from two runs of one machine, each carrying the character's telemetry
//! beside the value it produced:
//!
//! ```text
//! run br-20260915-190211-db2f   level 7    weapon 110000  (+0)   published 0_0
//! run br-20260918-005917-483b   level 60   weapon 3040412 (+12)  searched  2_1
//! ```
//!
//! # Why climbing is worth a default
//!
//! Equality on that field makes a host one band away indistinguishable from an empty world.
//! Measured on `br-20260918-005917-483b`: six consecutive searches asking `2_1`, aimed at the
//! host's own map tile, returned zero lobbies; the next search asking `2_2` returned her lobby at
//! index 0, and the Seamless session walked `0x0e -> 0x0f -> 0x13 -> 0x14 -> 0x16` in about half a
//! second. One digit was the whole difference between an evening of nothing and an invasion.
//!
//! # The shape of the climb
//!
//! Upward only, one rung per exhausted ring, weapon band first:
//!
//! ```text
//! 2_1  ->  2_2  ->  2_3  ->  ...  ->  2_MAX  ->  3_0  ->  3_1  ->  ...
//! ```
//!
//! Weapon band first because it is the cheaper mismatch to cross: a band of upgrade level is a
//! smaller gap than a band of character level, and the player asked to meet somebody rather than
//! to fight fair. The level band moves only when the weapon bands above have all been asked.

/// The highest weapon band this ladder will ask for before stepping the level band.
///
/// Observed values run to `3` (two strangers' advertisements, 2026-09-16), and no measurement has
/// produced the real ceiling. The constant is deliberately a little above what has been seen: a
/// band nobody occupies costs one empty ring and the ladder moves on, while a ceiling set too low
/// would silently refuse to look at hosts who exist.
pub const MAX_WEAPON_BAND: u32 = 6;

/// How far the level band may climb before the ladder stops.
///
/// Stopping matters more here than above. Each level rung is a whole weapon ladder underneath it,
/// so an unbounded climb is a rotation the player cannot sit through, and by the time it has run
/// this far the answer is that nobody in reach is hosting.
pub const MAX_LEVEL_STEPS: u32 = 4;

/// One rung: how far above the player's own band this search is asking.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Rung {
    /// Level bands above the player's own.
    pub level_steps: u32,
    /// Weapon bands above the player's own, within this level band.
    pub weapon_steps: u32,
}

impl Rung {
    /// The player's own band, which is where every search starts.
    #[must_use]
    pub const fn own() -> Self {
        Self {
            level_steps: 0,
            weapon_steps: 0,
        }
    }

    /// Whether this rung asks for anything other than the player's own band.
    #[must_use]
    pub const fn is_own(self) -> bool {
        self.level_steps == 0 && self.weapon_steps == 0
    }

    /// The next rung up, or `None` once the ladder is spent.
    ///
    /// The weapon band climbs first and the level band takes a step only when it is exhausted.
    /// Stepping the level band resets the weapon steps to zero rather than carrying them: the two
    /// numbers are separate bands, and `3_7` is not a place anybody is.
    #[must_use]
    pub const fn next(self) -> Option<Self> {
        if self.weapon_steps < MAX_WEAPON_BAND {
            return Some(Self {
                level_steps: self.level_steps,
                weapon_steps: self.weapon_steps + 1,
            });
        }
        if self.level_steps < MAX_LEVEL_STEPS {
            return Some(Self {
                level_steps: self.level_steps + 1,
                weapon_steps: 0,
            });
        }
        None
    }

    /// The next rung up, starting the ladder over at the player's own band once it is spent.
    ///
    /// A spent ladder used to stay on its top rung for as long as the finger was held, which means
    /// a search that had climbed past everybody kept asking the one band furthest from the player
    /// and nothing else -- a host who showed up at the player's own level afterwards could not be
    /// returned by any query the search still sent. User directive 2026-09-18: it "keeps going up
    /// in weapon level and character level in terms of matching queues until it finds a player or
    /// maxes out, and then it should repeat from the bottom of the character's current weapon
    /// levels and character levels".
    ///
    /// The lap is reported rather than silent, so the value the caller returns says which of the
    /// two things happened: climbing one rung, or starting the ladder again.
    #[must_use]
    pub const fn next_or_restart(self) -> (Self, bool) {
        match self.next() {
            Some(next) => (next, false),
            None => (Self::own(), true),
        }
    }
}

/// Whether a lobby-data value has the shape of Seamless's band pair.
///
/// Matched on the value, never the key: 2.0.x hashes its key names per build, so a key match would
/// need re-measuring every update while `<digits>_<digits>` does not.
#[must_use]
pub fn looks_like_band(value: &str) -> bool {
    split_band(value).is_some()
}

/// The two numbers in a band value, if it is one.
#[must_use]
pub fn split_band(value: &str) -> Option<(u32, u32)> {
    let (level, weapon) = value.split_once('_')?;
    if level.is_empty() || weapon.is_empty() {
        return None;
    }
    if !level.bytes().all(|b| b.is_ascii_digit()) || !weapon.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some((level.parse().ok()?, weapon.parse().ok()?))
}

/// The value a search on `rung` should ask for, given what Seamless computed for this player.
///
/// Returns `None` when the input is not a band value or when the rung is the player's own, so a
/// caller can forward Seamless's own string untouched and leave no room for an identity rewrite of
/// a value that only happened to contain an underscore.
///
/// The level band is offset rather than replaced, because the player's own band is the origin the
/// ladder climbs from and nothing here knows the absolute numbering.
#[must_use]
pub fn climbed(value: &str, rung: Rung) -> Option<String> {
    if rung.is_own() {
        return None;
    }
    let (level, weapon) = split_band(value)?;
    let climbed_level = level.saturating_add(rung.level_steps);
    // A level step means starting the weapon bands again from the bottom of that band, not
    // carrying this player's own weapon band into it.
    let climbed_weapon = if rung.level_steps == 0 {
        weapon.saturating_add(rung.weapon_steps)
    } else {
        rung.weapon_steps
    };
    Some(format!("{climbed_level}_{climbed_weapon}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_ladder_climbs_weapon_bands_before_level_bands() {
        let mut rung = Rung::own();
        let mut seen = Vec::new();
        while let Some(next) = rung.next() {
            seen.push((next.level_steps, next.weapon_steps));
            rung = next;
        }
        assert_eq!(seen[0], (0, 1));
        assert_eq!(seen[MAX_WEAPON_BAND as usize - 1], (0, MAX_WEAPON_BAND));
        assert_eq!(seen[MAX_WEAPON_BAND as usize], (1, 0));
        assert_eq!(
            seen.last().copied(),
            Some((MAX_LEVEL_STEPS, MAX_WEAPON_BAND))
        );
    }

    /// The ladder wraps instead of parking on its last rung, so a search held open keeps coming
    /// back to the player's own band. Parking there meant every later query asked the band
    /// furthest from them and nobody at their own level could be returned.
    #[test]
    fn a_spent_ladder_starts_again_at_the_players_own_band() {
        let top = Rung {
            level_steps: MAX_LEVEL_STEPS,
            weapon_steps: MAX_WEAPON_BAND,
        };
        assert_eq!(top.next(), None, "this is the last rung");
        assert_eq!(top.next_or_restart(), (Rung::own(), true));

        let (after, restarted) = Rung::own().next_or_restart();
        assert!(!restarted, "a climb is not a lap");
        assert_eq!(
            after,
            Rung::own().next().expect("a ladder has a first rung")
        );
    }

    /// A whole lap visits every rung once and returns to the bottom, so the rotation the player
    /// asked for is a rotation rather than a climb that stops.
    #[test]
    fn a_lap_covers_every_rung_and_comes_back() {
        let mut rung = Rung::own();
        let mut visited = vec![rung];
        loop {
            let (next, restarted) = rung.next_or_restart();
            if restarted {
                assert_eq!(next, Rung::own());
                break;
            }
            visited.push(next);
            rung = next;
        }
        let rungs = (MAX_LEVEL_STEPS as usize + 1) * (MAX_WEAPON_BAND as usize + 1);
        assert_eq!(visited.len(), rungs);
        assert_eq!(visited[0], Rung::own());
    }

    #[test]
    fn the_first_rung_reaches_the_host_that_was_measured_unreachable() {
        // The live case: this player searched `2_1`, the host published `2_2`, and six searches at
        // `2_1` returned nothing while the first at `2_2` returned her lobby.
        let first = Rung::own().next().expect("a ladder has a first rung");
        assert_eq!(climbed("2_1", first).as_deref(), Some("2_2"));
    }

    #[test]
    fn a_level_step_starts_the_weapon_bands_again_rather_than_carrying_them() {
        let rung = Rung {
            level_steps: 1,
            weapon_steps: 0,
        };
        assert_eq!(climbed("2_3", rung).as_deref(), Some("3_0"));
    }

    #[test]
    fn the_players_own_rung_never_rewrites_anything() {
        assert_eq!(climbed("2_1", Rung::own()), None);
    }

    #[test]
    fn only_a_band_shaped_value_is_climbed() {
        for value in [
            "",
            "_",
            "2_",
            "_1",
            "m61_48_45_00",
            "true",
            "2-1",
            "x_1",
            "2_y",
        ] {
            assert!(!looks_like_band(value), "{value} is not a band");
            assert_eq!(
                climbed(
                    value,
                    Rung {
                        level_steps: 0,
                        weapon_steps: 1
                    }
                ),
                None,
                "{value} must not be rewritten"
            );
        }
        assert!(looks_like_band("0_0"));
        assert!(looks_like_band("2_1"));
        assert!(looks_like_band("6_3"));
    }

    #[test]
    fn climbing_never_descends() {
        let mut rung = Rung::own();
        let mut previous = (2u32, 1u32);
        while let Some(next) = rung.next() {
            let asked = climbed("2_1", next).expect("a band value climbs");
            let parsed = split_band(&asked).expect("the climb produces a band value");
            assert!(
                parsed.0 > previous.0 || (parsed.0 == previous.0 && parsed.1 > previous.1),
                "{asked} is not above {previous:?}"
            );
            previous = parsed;
            rung = next;
        }
    }
}
