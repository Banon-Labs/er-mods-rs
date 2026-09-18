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

    /// Every rung a lap walks, in order, nearest bands first.
    ///
    /// The order replaced a weapon-first sweep on 2026-09-18. That sweep asked all seven weapon
    /// bands before stepping the level once, which put a host two level bands away at rung 16 of 35
    /// -- measured live on run `br-20260918-224517-b8a9`, where an RL9 `+2` character asking `0_0`
    /// climbed `0_1 0_2 0_3 0_4 0_5 0_6 1_0 1_1` over about two minutes while the friend it was
    /// looking for published `2_1` the whole time. Ring ordering reaches that `2_1` at rung five.
    ///
    /// The player's own words for the shape they wanted, 2026-09-18: "its supposed to go 0_0, 1_0,
    /// 1_1, 2_1, 2_2, 3_3" -- the diagonal, because a host at a higher character level usually
    /// carries a higher weapon upgrade too, so the two numbers move together in the population
    /// rather than independently. Their first three rungs are the first three here.
    ///
    /// `0_1` is inserted at rung three, which their sequence does not have. A pure diagonal visits
    /// 11 of the 35 pairs, and dropping the other 24 would leave a host at `0_3` or `1_5` permanently
    /// unreachable; worse, it pushes a host one weapon band up at the player's own level from rung
    /// one to rung eleven. Ring ordering keeps the diagonal at the front and still names every pair.
    ///
    /// Coverage is asserted by a test rather than promised here: the order is a preference, but a
    /// band this never asks for is a host the player can never meet.
    #[must_use]
    pub fn lap() -> Vec<Self> {
        let mut order: Vec<Self> = (0..=MAX_LEVEL_STEPS)
            .flat_map(|level_steps| {
                (0..=MAX_WEAPON_BAND).map(move |weapon_steps| Self {
                    level_steps,
                    weapon_steps,
                })
            })
            .collect();
        // Ring by ring outward, level band ahead of weapon band inside a ring.
        //
        // `max` and not `level + weapon`: the ring is how far the furthest of the two bands has
        // moved, which is what makes `1_1` a near rung rather than a distant one. That single choice
        // is what puts the player's own diagonal at the front -- `0_0`, `1_0`, `1_1`, then `2_1`
        // four rungs later -- while leaving every pair in the lap.
        order.sort_by_key(|rung| {
            (
                rung.level_steps.max(rung.weapon_steps),
                core::cmp::Reverse(rung.level_steps),
                rung.weapon_steps,
            )
        });
        order
    }

    /// The next rung up, or `None` once the ladder is spent.
    ///
    /// A position in [`Self::lap`] rather than arithmetic on the two axes, because the order is no
    /// longer something either axis can decide alone: the diagonal comes first and the leftover
    /// pairs follow it.
    ///
    /// A rung the lap does not contain -- which no caller should produce -- answers `None` rather
    /// than guessing, so a bad rung ends the ladder and restarts it instead of wandering off.
    #[must_use]
    pub fn next(self) -> Option<Self> {
        let order = Self::lap();
        let at = order.iter().position(|rung| *rung == self)?;
        order.get(at + 1).copied()
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
    // Not `const` any more: the order it walks is [`Self::lap`], which allocates.
    #[must_use]
    pub fn next_or_restart(self) -> (Self, bool) {
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
    // Both axes are offsets from the player's own band. This used to replace the weapon band with
    // `rung.weapon_steps` whenever the level stepped, on the reasoning that a new level band starts
    // its weapon bands again from the bottom -- and that was survivable only while the ladder swept
    // one axis at a time. On the diagonal it silently throws the player's own weapon band away: a
    // `2_1` character at rung `1_1` would ask `3_1`, meaning level +1 and weapon minus one, which is
    // a band nobody on the diagonal intended to visit.
    let climbed_level = level.saturating_add(rung.level_steps);
    let climbed_weapon = weapon.saturating_add(rung.weapon_steps);
    Some(format!("{climbed_level}_{climbed_weapon}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The whole walk, spelled out, because the order is the feature.
    ///
    /// Asserted as one literal sequence rather than as properties: the previous test checked three
    /// indices and a last element, and every one of those still passed while the ladder was taking
    /// sixteen rungs to reach a band three rungs away.
    #[test]
    fn the_ladder_climbs_the_diagonal_level_first() {
        let mut rung = Rung::own();
        let mut seen = vec![(rung.level_steps, rung.weapon_steps)];
        while let Some(next) = rung.next() {
            seen.push((next.level_steps, next.weapon_steps));
            rung = next;
        }
        assert_eq!(
            seen[..7].to_vec(),
            vec![(0, 0), (1, 0), (1, 1), (0, 1), (2, 0), (2, 1), (2, 2)],
            "the near rungs, and the player's own diagonal at the front of them"
        );
        assert_eq!(
            seen.len(),
            (MAX_LEVEL_STEPS as usize + 1) * (MAX_WEAPON_BAND as usize + 1),
            "the lap still names every pair; the order changed, the coverage did not"
        );
    }

    /// Every pair appears exactly once. The order is a preference; the coverage is a contract, and
    /// a band this never asks for is a host the player can never meet.
    #[test]
    fn the_lap_names_every_pair_once() {
        let lap = Rung::lap();
        let mut sorted = lap.clone();
        sorted.sort_by_key(|rung| (rung.level_steps, rung.weapon_steps));
        sorted.dedup();
        assert_eq!(
            sorted.len(),
            lap.len(),
            "a pair appears twice, so one cycle is spent asking a band already asked"
        );
        assert_eq!(
            lap.len(),
            (MAX_LEVEL_STEPS as usize + 1) * (MAX_WEAPON_BAND as usize + 1)
        );
    }

    /// The rung that cost an evening. It was sixteen failed cycles away; it is now five.
    ///
    /// Held as an upper bound rather than an equality, because the number is a consequence of the
    /// ordering and a future ordering may better it. What must not happen again is a near host
    /// sitting most of a lap away: at roughly fifteen seconds a cycle, sixteen rungs is four
    /// minutes of a player holding a finger and being told nobody is online.
    #[test]
    fn a_host_two_level_bands_up_is_a_near_rung() {
        let mut rung = Rung::own();
        let mut rungs = 0usize;
        while rung.level_steps != 2 || rung.weapon_steps != 1 {
            rung = rung.next().expect("2_1 must be on the ladder at all");
            rungs += 1;
        }
        assert_eq!(rungs, 5, "2_1 is five failed cycles away, not sixteen");
        assert!(
            rungs * 4 < (MAX_LEVEL_STEPS as usize + 1) * (MAX_WEAPON_BAND as usize + 1),
            "a two-band host must be in the first quarter of the lap, not most of the way through"
        );
    }

    /// Neither axis is ever stepped past its own cap, which is what makes the reset unnecessary.
    #[test]
    fn no_rung_exceeds_either_cap() {
        let mut rung = Rung::own();
        while let Some(next) = rung.next() {
            assert!(next.level_steps <= MAX_LEVEL_STEPS, "level cap: {next:?}");
            assert!(next.weapon_steps <= MAX_WEAPON_BAND, "weapon cap: {next:?}");
            rung = next;
        }
    }

    /// The ladder wraps instead of parking on its last rung, so a search held open keeps coming
    /// back to the player's own band. Parking there meant every later query asked the band
    /// furthest from them and nobody at their own level could be returned.
    #[test]
    fn a_spent_ladder_starts_again_at_the_players_own_band() {
        // The last rung is whatever the lap ends on, not a pair anyone can name by hand -- under
        // ring ordering `4_6` is the first rung of the outermost ring, not the last.
        let top = *Rung::lap().last().expect("the lap is not empty");
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

    /// The two hosts this ladder was built for, and how many failed cycles each now costs.
    ///
    /// Both were measured live and both used to be a whole sweep away. `2_2` against a `2_1`
    /// player is the 2026-09-17 case -- six searches at `2_1` returned nothing and the first at
    /// `2_2` returned her lobby. `2_1` against a `0_0` player is the 2026-09-18 case, where the
    /// old weapon-first sweep put the friend at rung sixteen and the player never reached him.
    #[test]
    fn the_hosts_that_were_measured_unreachable_are_near_rungs_now() {
        let lap = Rung::lap();
        let rung_of = |player: &str, host: &str| -> usize {
            lap.iter()
                .position(|rung| climbed(player, *rung).as_deref() == Some(host))
                .unwrap_or_else(|| panic!("{host} is not reachable from {player} at all"))
        };
        assert_eq!(rung_of("2_1", "2_2"), 3, "one weapon band up, same level");
        assert_eq!(
            rung_of("0_0", "2_1"),
            5,
            "two level bands up, one weapon band"
        );
    }

    /// A level step carries the player's own weapon band with it.
    ///
    /// The inverse of this was asserted until 2026-09-18, when the ladder swept one axis at a time
    /// and a level step reset the weapon band to the rung's own count. On a diagonal that reset
    /// silently subtracts: a `2_3` player at rung `1_1` would ask `3_1`, a weapon band below their
    /// own, which is a fight they never opted into.
    #[test]
    fn a_level_step_carries_the_players_own_weapon_band() {
        let rung = Rung {
            level_steps: 1,
            weapon_steps: 1,
        };
        assert_eq!(climbed("2_3", rung).as_deref(), Some("3_4"));
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

    /// No rung ever asks below the player's own band on either axis.
    ///
    /// This used to assert something stricter -- that each rung is above the one before it -- and
    /// that held only while the ladder swept a single axis. Ring ordering revisits a lower level
    /// band after a higher one (`1_1` then `0_1`), so the sequence is not monotone and should not be:
    /// what the player is owed is that the search never looks downward from where they are, since
    /// invading beneath your own band puts you on somebody weaker who never agreed to it. That is a
    /// property of every rung against the player, not of consecutive rungs against each other.
    #[test]
    fn climbing_never_descends() {
        let mut rung = Rung::own();
        let own = (2u32, 1u32);
        while let Some(next) = rung.next() {
            let asked = climbed("2_1", next).expect("a band value climbs");
            let parsed = split_band(&asked).expect("the climb produces a band value");
            assert!(
                parsed.0 >= own.0 && parsed.1 >= own.1,
                "{asked} is below the player's own {own:?}"
            );
            rung = next;
        }
    }
}
