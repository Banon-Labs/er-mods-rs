//! Which matchmaking bracket the far half of a search invades into.
//!
//! # What this is for, and where it applies
//!
//! `Both near and far` is two searches. The near half is this crate's own: a ring of map tiles
//! around the player, judged and narrowed by the DLL. When that ring answers nothing --
//! `lobby_preflight::hand_over_when_the_neighbourhood_is_empty` -- the DLL retires its whole
//! overlay and the far half runs as Seamless's own search, unfiltered. The player's own words for
//! what that second half does, 2026-09-18: "it essentially returns to Seamless default invasion
//! properties, whereby I invade someone in my bracket."
//!
//! This is the one thing that stays attached across that handover. The player names the bracket
//! they want to be sent to, and the far half's queries ask for it instead of their own.
//!
//! It does nothing to the near half. A search that has not reached the handover is still asking
//! about the neighbourhood at the player's own band, and narrowing that by bracket as well would
//! move two axes on one failed cycle -- the mistake `failed_cycle`'s module docs already record.
//!
//! # Why brackets are picked and not stepped
//!
//! This shipped once as six named tiers -- `Default`, `Hard`, `Harder`, `Hardest`, `Insano`,
//! `Godlike` -- each a fixed `+N` on both axes, with the top one jumping to the ceiling. Two things
//! were wrong with it and both became visible the moment the real bracket edges were known.
//!
//! The names said nothing. "Insano" does not tell a player they are about to ask for `RL101-125`,
//! and the same name meant a different fight for every character who picked it.
//!
//! And a fixed step leaves holes. From band 0 the reachable level bands were `0 1 2 3 4` and then
//! `8`, because the top tier was absolute -- so `RL126-150`, `RL151-200` and `RL201-300` could not
//! be asked for at all, while `RL301+` could. Closing that with more tiers costs three more rows
//! and a nine-stop cycle; naming the brackets costs nothing and cannot hole.
//!
//! So the panel offers the brackets themselves, with everything below the player's own greyed out.
//! There is no ceiling special case left: the last entry is just the last entry.
//!
//! # The encoding
//!
//! Seamless publishes `<level band>_<weapon band>` and filters it with `k_ELobbyComparisonEqual`,
//! so a host one digit away is indistinguishable from an empty world. Both halves are a
//! threshold-table lookup inside `ersc.dll` at `ersc+0xa97b0` -- the band is the index of the first
//! threshold at or above the character's value, or the table's length when the character is past
//! every threshold.
//!
//! Measured live 2026-09-18, run `br-20260918-235543-826e`, by
//! `scripts/frida/seamless-band-tables.js` reading the two vectors that function looks up. The read
//! is self-validating: on the same frame the arguments were `level = 9` and `weapon = 2`, and the
//! query a moment later carried `0_0` under key
//! `21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e`, which is what these tables
//! produce. Four earlier observations reproduce as well, and a test below holds all five.

/// Seamless's level-bracket edges, from the vector at `[rcx+0x70]`.
///
/// A character whose rune level is at or below `LEVEL_THRESHOLDS[i]`, and above the one before it,
/// is in band `i`; a character above all of them is in band [`MAX_LEVEL_BAND`].
pub const LEVEL_THRESHOLDS: [u32; 8] = [20, 40, 70, 100, 125, 150, 200, 300];

/// Seamless's weapon-bracket edges, from the vector at `[rcx+0x88]`. Read the same way as
/// [`LEVEL_THRESHOLDS`], against the character's highest weapon upgrade level.
pub const WEAPON_THRESHOLDS: [u32; 3] = [3, 12, 20];

/// The top level band, which is the number of thresholds: a character above every one of them
/// lands on the table's length, and nobody can publish higher.
pub const MAX_LEVEL_BAND: u32 = LEVEL_THRESHOLDS.len() as u32;

/// The top weapon band, on the same footing as [`MAX_LEVEL_BAND`].
pub const MAX_WEAPON_BAND: u32 = WEAPON_THRESHOLDS.len() as u32;

/// Seamless's own lookup, for one axis.
fn band_of(value: u32, thresholds: &[u32]) -> u32 {
    let at = thresholds.iter().position(|edge| *edge >= value);
    u32::try_from(at.unwrap_or(thresholds.len())).unwrap_or(0)
}

/// Which level band a rune level falls in.
#[must_use]
pub fn level_band(rune_level: u32) -> u32 {
    band_of(rune_level, &LEVEL_THRESHOLDS)
}

/// Which weapon band an upgrade level falls in.
#[must_use]
pub fn weapon_band(upgrade_level: u32) -> u32 {
    band_of(upgrade_level, &WEAPON_THRESHOLDS)
}

/// What a level band is called on the panel: the rune levels it actually covers.
///
/// The range and not the index, because the index is Seamless's bookkeeping and the range is what
/// a player recognises. `RL126-150` is a fight they can picture; band 5 is not.
#[must_use]
pub fn level_band_label(band: u32) -> String {
    let low = match band.checked_sub(1) {
        None => 1,
        Some(below) => LEVEL_THRESHOLDS
            .get(below as usize)
            .map_or(1, |edge| edge + 1),
    };
    match LEVEL_THRESHOLDS.get(band as usize) {
        Some(high) => format!("RL{low}-{high}"),
        None => format!("RL{low}+"),
    }
}

/// What a weapon band is called on the panel, in upgrade levels.
#[must_use]
pub fn weapon_band_label(band: u32) -> String {
    let low = match band.checked_sub(1) {
        None => 0,
        Some(below) => WEAPON_THRESHOLDS
            .get(below as usize)
            .map_or(0, |edge| edge + 1),
    };
    match WEAPON_THRESHOLDS.get(band as usize) {
        Some(high) => format!("+{low} to +{high}"),
        None => format!("+{low} and up"),
    }
}

/// Every level band, in order, as the panel lists them.
#[must_use]
pub fn level_band_labels() -> Vec<String> {
    (0..=MAX_LEVEL_BAND).map(level_band_label).collect()
}

/// Every weapon band, in order, as the panel lists them.
#[must_use]
pub fn weapon_band_labels() -> Vec<String> {
    (0..=MAX_WEAPON_BAND).map(weapon_band_label).collect()
}

/// The bracket pair the player has asked the far half to invade into.
///
/// `None` on an axis means "whatever mine is", which is what a fresh session holds and what a
/// player who cares about only one axis leaves the other at.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct BracketChoice {
    /// The level band to ask for, or `None` for the player's own.
    pub level: Option<u32>,
    /// The weapon band to ask for, or `None` for the player's own.
    pub weapon: Option<u32>,
}

impl BracketChoice {
    /// Nothing picked on either axis: Seamless's own behaviour, untouched.
    #[must_use]
    pub const fn own() -> Self {
        Self {
            level: None,
            weapon: None,
        }
    }

    /// Whether this asks for anything other than the player's own bracket.
    #[must_use]
    pub const fn is_own(self) -> bool {
        self.level.is_none() && self.weapon.is_none()
    }

    /// The band value the far half should ask for, given the one Seamless computed for this
    /// character.
    ///
    /// `None` means send Seamless's own value -- because nothing is picked, because the pick
    /// resolves to the character's own bracket anyway, or because `own` is not a band value and
    /// rewriting it would be an invention.
    ///
    /// Neither axis can resolve below the player's own band, whatever is stored. The panel greys
    /// those entries out, but a pick made at `RL40` and still held after levelling past `RL70` is
    /// the same defect arriving by a different route, and invading beneath your own band puts you
    /// on somebody weaker who never agreed to it.
    #[must_use]
    pub fn band_for(self, own: &str) -> Option<String> {
        let (own_level, own_weapon) = crate::band_ladder::split_band(own)?;
        let level = self
            .level
            .unwrap_or(own_level)
            .clamp(own_level, MAX_LEVEL_BAND);
        let weapon = self
            .weapon
            .unwrap_or(own_weapon)
            .clamp(own_weapon, MAX_WEAPON_BAND);
        let asked = format!("{level}_{weapon}");
        // A pick that lands back on the player's own band asks Seamless for exactly what it was
        // already going to send. Answering `None` there keeps the rewrite log honest: a line
        // saying "asking for 2_1 instead of 2_1" reads as a change that did not happen.
        (asked != own).then_some(asked)
    }

    /// One line describing what is picked, for the log and for the rows' note.
    #[must_use]
    pub fn describe(self) -> String {
        match (self.level, self.weapon) {
            (None, None) => "your own bracket -- Seamless's own behaviour".to_owned(),
            (Some(level), None) => {
                format!("{} at your own weapon bracket", level_band_label(level))
            }
            (None, Some(weapon)) => {
                format!("your own rune level at {}", weapon_band_label(weapon))
            }
            (Some(level), Some(weapon)) => format!(
                "{} at {}",
                level_band_label(level),
                weapon_band_label(weapon)
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every band this repo has ever observed on the wire, reproduced from the measured tables.
    ///
    /// This is what makes the read evidence instead of a number somebody typed. The live frame is
    /// the last row: the arguments were `RL9 +2` and the query that followed carried `0_0` under
    /// the band key. The four before it were recorded across three earlier evenings by entirely
    /// different means.
    ///
    /// One historical point is deliberately absent and is wrong rather than missing: bd
    /// `seamless-band-is-the-single-field-excluding-a-friend-proven-live-2026-09-18` records
    /// `RL9 -> level band 1`. The tables put `RL9` in band `0`, and the live wire capture on the
    /// same character agrees, so that line is superseded.
    #[test]
    fn the_measured_tables_reproduce_every_band_ever_seen_on_the_wire() {
        for (level, weapon, expected) in [
            (7u32, 0u32, "0_0"),
            (41, 0, "2_0"),
            (50, 8, "2_1"),
            (60, 12, "2_1"),
            (9, 2, "0_0"),
        ] {
            let seen = format!("{}_{}", level_band(level), weapon_band(weapon));
            assert_eq!(seen, expected, "RL{level} +{weapon}");
        }
    }

    /// A character past every threshold lands on the ceiling, which is what makes the last entry
    /// in each list reachable at all.
    #[test]
    fn a_character_above_every_threshold_lands_on_the_ceiling() {
        // 713 is the highest rune level the game allows; `+25` the highest standard reinforcement.
        assert_eq!(level_band(713), MAX_LEVEL_BAND);
        assert_eq!(weapon_band(25), MAX_WEAPON_BAND);
    }

    /// The lists are what the panel draws, so their length and their edges are the feature.
    ///
    /// Spelled out rather than derived: a label reading `RL126-150` is a promise about who the
    /// search will be sent to, and it has to match the thresholds exactly or it is a lie told in a
    /// dropdown.
    #[test]
    fn every_bracket_is_named_by_the_levels_it_actually_covers() {
        assert_eq!(
            level_band_labels(),
            vec![
                "RL1-20",
                "RL21-40",
                "RL41-70",
                "RL71-100",
                "RL101-125",
                "RL126-150",
                "RL151-200",
                "RL201-300",
                "RL301+",
            ]
        );
        assert_eq!(
            weapon_band_labels(),
            vec!["+0 to +3", "+4 to +12", "+13 to +20", "+21 and up"]
        );
    }

    /// Every label describes the band a character in it would be given, on both edges.
    ///
    /// The off-by-one this closes is real: the low edge of band `i` is the previous threshold plus
    /// one, and writing the previous threshold itself would put every boundary level in the row
    /// above its own.
    #[test]
    fn each_label_covers_exactly_the_levels_that_map_to_its_band() {
        for band in 0..=MAX_LEVEL_BAND {
            let low = match band.checked_sub(1) {
                None => 1,
                Some(below) => LEVEL_THRESHOLDS[below as usize] + 1,
            };
            assert_eq!(level_band(low), band, "the low edge of {band}");
            if let Some(high) = LEVEL_THRESHOLDS.get(band as usize) {
                assert_eq!(level_band(*high), band, "the high edge of {band}");
                assert_eq!(level_band(high + 1), band + 1, "one past the high edge");
            }
        }
    }

    /// Nothing picked rewrites nothing, which is what makes a fresh session safe.
    #[test]
    fn an_empty_choice_never_rewrites_the_band() {
        for own in ["0_0", "2_1", "8_3"] {
            assert_eq!(BracketChoice::own().band_for(own), None);
        }
    }

    /// Each axis is picked on its own, so a player who cares only about weapon upgrade leaves the
    /// rune level alone and still gets what they asked for.
    #[test]
    fn either_axis_can_be_picked_without_the_other() {
        let level_only = BracketChoice {
            level: Some(5),
            weapon: None,
        };
        assert_eq!(level_only.band_for("2_1").as_deref(), Some("5_1"));
        let weapon_only = BracketChoice {
            level: None,
            weapon: Some(3),
        };
        assert_eq!(weapon_only.band_for("2_1").as_deref(), Some("2_3"));
    }

    /// Every bracket at or above the player's own is reachable, which is the whole point of
    /// replacing the fixed steps. Under those, a band-0 character could ask for 0 through 4 and
    /// then 8, and bands 5, 6 and 7 could not be asked for at all.
    #[test]
    fn every_bracket_above_the_player_is_reachable_with_no_holes() {
        let own = "0_0";
        let reachable: Vec<u32> = (0..=MAX_LEVEL_BAND)
            .filter_map(|band| {
                let choice = BracketChoice {
                    level: Some(band),
                    weapon: None,
                };
                match choice.band_for(own) {
                    // Band 0 is the player's own and rewrites nothing, which still counts as
                    // reachable -- it is the row they are already in.
                    None => (band == 0).then_some(band),
                    Some(asked) => crate::band_ladder::split_band(&asked).map(|(level, _)| level),
                }
            })
            .collect();
        assert_eq!(reachable, (0..=MAX_LEVEL_BAND).collect::<Vec<_>>());
    }

    /// A pick below the player's own band is refused even when it is stored, because a character
    /// levels up and the pick does not.
    #[test]
    fn a_pick_below_the_players_own_bracket_never_reaches_the_wire() {
        let stale = BracketChoice {
            level: Some(1),
            weapon: Some(0),
        };
        // Picked at `RL21-40 +0-+3`, still held after levelling into `RL41-70` with a `+13` weapon.
        assert_eq!(
            stale.band_for("2_2"),
            None,
            "clamped back to the player's own on both axes, so nothing is asked"
        );
        let half_stale = BracketChoice {
            level: Some(1),
            weapon: Some(3),
        };
        assert_eq!(half_stale.band_for("2_2").as_deref(), Some("2_3"));
    }

    /// No choice ever asks below the player's own, on either axis, for any pair on either list.
    #[test]
    fn nothing_ever_descends() {
        for own_level in 0..=MAX_LEVEL_BAND {
            for own_weapon in 0..=MAX_WEAPON_BAND {
                let own = format!("{own_level}_{own_weapon}");
                for level in 0..=MAX_LEVEL_BAND {
                    for weapon in 0..=MAX_WEAPON_BAND {
                        let choice = BracketChoice {
                            level: Some(level),
                            weapon: Some(weapon),
                        };
                        let Some(asked) = choice.band_for(&own) else {
                            continue;
                        };
                        let (asked_level, asked_weapon) =
                            crate::band_ladder::split_band(&asked).expect("a band value");
                        assert!(
                            asked_level >= own_level && asked_weapon >= own_weapon,
                            "{own} -> {asked}"
                        );
                    }
                }
            }
        }
    }

    /// A value that is not a band is forwarded untouched. This runs on every string filter in the
    /// far half's query, which carries Seamless's own keys, so a rewrite of anything
    /// band-shaped-ish would corrupt a field nothing here has identified.
    #[test]
    fn only_a_band_shaped_value_is_rewritten() {
        let choice = BracketChoice {
            level: Some(8),
            weapon: Some(3),
        };
        for value in ["", "_", "2_", "_1", "m61_48_45_00", "true", "2-1", "x_1"] {
            assert_eq!(choice.band_for(value), None, "{value} is not a band");
        }
    }

    /// The description names both axes, because a row saying "RL126-150" while silently leaving
    /// the weapon bracket alone would describe half of what the search will ask for.
    #[test]
    fn the_description_names_whichever_axes_are_picked() {
        assert_eq!(
            BracketChoice::own().describe(),
            "your own bracket -- Seamless's own behaviour"
        );
        assert_eq!(
            BracketChoice {
                level: Some(5),
                weapon: Some(3)
            }
            .describe(),
            "RL126-150 at +21 and up"
        );
        assert!(
            BracketChoice {
                level: Some(5),
                weapon: None
            }
            .describe()
            .contains("your own weapon bracket")
        );
        assert!(
            BracketChoice {
                level: None,
                weapon: Some(3)
            }
            .describe()
            .contains("your own rune level")
        );
    }
}
