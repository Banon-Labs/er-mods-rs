//! How far above their own matchmaking bracket a player has asked to invade.
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
//! This is the one thing that stays attached across that handover. It takes the bracket pair
//! Seamless computed for this character and asks for the pair `N` brackets above it instead, on
//! both axes at once.
//!
//! It does nothing to the near half. A search that has not reached the handover is still asking
//! about the neighbourhood at the player's own band, and narrowing that by difficulty as well
//! would move two axes on one failed cycle -- the mistake `failed_cycle`'s module docs already
//! record.
//!
//! # Why a flat offset rather than another ladder
//!
//! [`crate::band_ladder`] walks upward one rung at a time because it is hunting: nobody has been
//! seen, so it sweeps. This is the opposite act. The player has named the fight they want, and a
//! ladder underneath that would spend most of its cycles asking for brackets they did not choose.
//! One difficulty is one band pair, asked for as long as the far half runs.
//!
//! # The encoding
//!
//! Seamless publishes `<level band>_<weapon band>` and filters it with `k_ELobbyComparisonEqual`,
//! so a host one digit away is indistinguishable from an empty world. Both halves are a
//! threshold-table lookup inside `ersc.dll` -- the band is the index of the first threshold at or
//! above the character's value, or the table's length when the character is past every threshold.
//! That is what makes [`MAX_LEVEL_BAND`] and [`MAX_WEAPON_BAND`] meaningful: they are table
//! lengths, not guesses at how high anybody plays.
//!
//! The brackets are wide, which is what makes a single step worth offering as a difficulty. One
//! level bracket up from an `RL60` character is `RL71-100`; one weapon bracket up from `+12` is
//! `+13` to `+20`. See the two constants for the whole ladder.

/// The top level band Seamless can compute, which is the length of its level-threshold table.
///
/// Measured live on 2026-09-18, run `br-20260918-235543-826e`, by
/// `scripts/frida/seamless-band-tables.js` reading the vector at `[rcx+0x70]` inside `ersc+0xa97b0`
/// on `ersc 2.0.1`. The thresholds are `[20, 40, 70, 100, 125, 150, 200, 300]`, so every rune level
/// in the game falls in one of nine bands:
///
/// ```text
/// band   0      1       2       3        4         5         6         7         8
/// RL     1-20   21-40   41-70   71-100   101-125   126-150   151-200   201-300   301+
/// ```
///
/// Eight thresholds, so eight is the ceiling: the lookup answers with the table's length for a
/// character above every one of them, and no character can publish a higher number.
///
/// The read is self-validating rather than trusted. On the same frame the character's own
/// arguments were `level = 9`, `weapon = 2`, and the query that went out a moment later carried
/// `0_0` under key `21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e` -- which is
/// what this table produces for that character. Four earlier observations reproduce too: `RL7 +0`
/// gives `0_0`, `RL41` gives level band 2, `RL50 +8` and `RL60 +12` both give `2_1`.
pub const MAX_LEVEL_BAND: u32 = 8;

/// The top weapon band, on the same footing as [`MAX_LEVEL_BAND`] and from the same read.
///
/// The vector at `[rcx+0x88]` holds `[3, 12, 20]`, so the weapon axis has four bands to the level
/// axis's nine, and the bands are wide: every weapon from unupgraded to `+3` is one bracket.
///
/// ```text
/// band     0       1        2         3
/// upgrade  +0-+3   +4-+12   +13-+20   +21+
/// ```
pub const MAX_WEAPON_BAND: u32 = 3;

/// Which bracket the far half of a search asks for, relative to the player's own.
///
/// Ordered by how far above the player the fight is, because that order is also the order the
/// settings panel cycles through and a player reading it should not have to learn a second one.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord)]
pub enum InvadeDifficulty {
    /// The player's own bracket, on both axes. Seamless's own behaviour, untouched.
    #[default]
    Default,
    /// One bracket above, on level and on weapon upgrade.
    Hard,
    /// Two brackets above.
    Harder,
    /// Three brackets above.
    Hardest,
    /// Four brackets above.
    Insano,
    /// The top bracket on both axes, whatever the player's own is.
    ///
    /// Absolute rather than relative: [`MAX_LEVEL_BAND`] and [`MAX_WEAPON_BAND`] are where
    /// Seamless's two threshold tables run out, so this asks for the fight the game can offer
    /// rather than for a fixed number of steps from wherever the character happens to be.
    Godlike,
}

impl InvadeDifficulty {
    /// Every difficulty, in the order the panel cycles them.
    pub const ALL: [Self; 6] = [
        Self::Default,
        Self::Hard,
        Self::Harder,
        Self::Hardest,
        Self::Insano,
        Self::Godlike,
    ];

    /// What the settings panel calls this, and what the log calls it.
    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Default => "Default",
            Self::Hard => "Hard",
            Self::Harder => "Harder",
            Self::Hardest => "Hardest",
            Self::Insano => "Insano",
            Self::Godlike => "Godlike",
        }
    }

    /// One line saying what picking this actually changes, shown beside the row.
    ///
    /// Each one names the far half explicitly. A player who reads "one bracket above" on a row and
    /// then invades their neighbour at their own level has been told something untrue, and the
    /// only place that distinction can be made is here.
    #[must_use]
    pub const fn note(self) -> &'static str {
        match self {
            Self::Default => "far half asks your own bracket -- Seamless's own behaviour",
            Self::Hard => "far half asks one bracket up, level and weapon",
            Self::Harder => "far half asks two brackets up, level and weapon",
            Self::Hardest => "far half asks three brackets up, level and weapon",
            Self::Insano => "far half asks four brackets up, level and weapon",
            Self::Godlike => "far half asks the top bracket there is, level and weapon",
        }
    }

    /// How many brackets above the player's own this asks for, or `None` for the absolute top.
    #[must_use]
    pub const fn brackets_up(self) -> Option<u32> {
        match self {
            Self::Default => Some(0),
            Self::Hard => Some(1),
            Self::Harder => Some(2),
            Self::Hardest => Some(3),
            Self::Insano => Some(4),
            Self::Godlike => None,
        }
    }

    /// The next difficulty, wrapping back to [`Self::Default`] past the top.
    ///
    /// Wrapping rather than stopping is what makes a single button enough for the panel, the same
    /// choice `search_radius` already made.
    #[must_use]
    pub fn next(self) -> Self {
        let at = Self::ALL.iter().position(|d| *d == self).unwrap_or(0);
        Self::ALL[(at + 1) % Self::ALL.len()]
    }

    /// Its position in [`Self::ALL`], for the atomic the running DLL keeps it in.
    #[must_use]
    pub fn index(self) -> usize {
        Self::ALL.iter().position(|d| *d == self).unwrap_or(0)
    }

    /// The difficulty at `index`, falling back to [`Self::Default`] for anything out of range.
    ///
    /// Out of range cannot happen through [`Self::index`], and falling back rather than panicking
    /// means a torn or stale read costs the player Seamless's own behaviour instead of the game.
    #[must_use]
    pub fn from_index(index: usize) -> Self {
        Self::ALL.get(index).copied().unwrap_or(Self::Default)
    }

    /// The band value the far half should ask for, given the one Seamless computed for this
    /// character.
    ///
    /// `None` means ask for exactly what Seamless computed -- either because the player chose
    /// [`Self::Default`], or because `own` is not a band value and rewriting it would be an
    /// invention. The caller forwards Seamless's own string in both cases, so there is no path
    /// here that can silently rewrite a field this does not understand.
    ///
    /// Both axes are clamped at their ceiling. Asking for a band above the top matches nobody at
    /// all, which on screen is indistinguishable from the difficulty being broken.
    #[must_use]
    pub fn band_for(self, own: &str) -> Option<String> {
        let (level, weapon) = crate::band_ladder::split_band(own)?;
        let (level, weapon) = match self.brackets_up() {
            Some(0) => return None,
            Some(steps) => (
                level.saturating_add(steps).min(MAX_LEVEL_BAND),
                weapon.saturating_add(steps).min(MAX_WEAPON_BAND),
            ),
            None => (MAX_LEVEL_BAND, MAX_WEAPON_BAND),
        };
        let asked = format!("{level}_{weapon}");
        // A difficulty that lands back on the player's own band asks Seamless for exactly what it
        // was already going to send. Answering `None` there keeps the rewrite log honest: a line
        // saying "asking for 6_3 instead of 6_3" reads as a change that did not happen.
        (asked != own).then_some(asked)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Seamless's level thresholds, as read out of `[rcx+0x70]` on 2026-09-18.
    const MEASURED_LEVEL_THRESHOLDS: [u32; 8] = [20, 40, 70, 100, 125, 150, 200, 300];

    /// Seamless's weapon thresholds, from `[rcx+0x88]` in the same read.
    const MEASURED_WEAPON_THRESHOLDS: [u32; 3] = [3, 12, 20];

    /// Seamless's own lookup: the index of the first threshold at or above `value`, or the table's
    /// length when `value` is past every one of them.
    fn band(value: u32, thresholds: &[u32]) -> u32 {
        let at = thresholds.iter().position(|edge| *edge >= value);
        u32::try_from(at.unwrap_or(thresholds.len())).expect("a table of single digits")
    }

    /// The two ceilings are the two table lengths, and nothing else.
    ///
    /// Held as a test rather than as a comment because the constants are what `Godlike` asks for:
    /// set either one above its table and `Godlike` names a bracket nobody can be in, which on
    /// screen is a difficulty that silently finds no one.
    #[test]
    fn the_ceilings_are_the_measured_table_lengths() {
        assert_eq!(MAX_LEVEL_BAND as usize, MEASURED_LEVEL_THRESHOLDS.len());
        assert_eq!(MAX_WEAPON_BAND as usize, MEASURED_WEAPON_THRESHOLDS.len());
    }

    /// Every band this repo has ever observed on the wire, reproduced from the measured tables.
    ///
    /// This is what makes the read above evidence instead of a number somebody typed. The live
    /// frame is the last row: the arguments were `RL9 +2` and the query that followed carried
    /// `0_0` under the band key. The four before it were recorded across three earlier evenings by
    /// entirely different means.
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
            let seen = format!(
                "{}_{}",
                band(level, &MEASURED_LEVEL_THRESHOLDS),
                band(weapon, &MEASURED_WEAPON_THRESHOLDS)
            );
            assert_eq!(seen, expected, "RL{level} +{weapon}");
        }
    }

    /// A character past every threshold lands on the ceiling, which is the whole basis for
    /// `Godlike` being expressible at all.
    #[test]
    fn a_character_above_every_threshold_lands_on_the_ceiling() {
        // 713 is the highest rune level the game allows; `+25` the highest standard reinforcement.
        assert_eq!(band(713, &MEASURED_LEVEL_THRESHOLDS), MAX_LEVEL_BAND);
        assert_eq!(band(25, &MEASURED_WEAPON_THRESHOLDS), MAX_WEAPON_BAND);
        assert_eq!(
            InvadeDifficulty::Godlike.band_for("0_0").as_deref(),
            Some("8_3")
        );
    }

    /// The six rows, the offsets the player was promised, and the order they cycle in.
    #[test]
    fn the_ladder_of_difficulties_is_the_one_that_was_asked_for() {
        assert_eq!(
            InvadeDifficulty::ALL.map(|d| (d.label(), d.brackets_up())),
            [
                ("Default", Some(0)),
                ("Hard", Some(1)),
                ("Harder", Some(2)),
                ("Hardest", Some(3)),
                ("Insano", Some(4)),
                ("Godlike", None),
            ]
        );
    }

    #[test]
    fn cycling_wraps_back_to_the_players_own_bracket() {
        let mut seen = vec![InvadeDifficulty::Default];
        let mut at = InvadeDifficulty::Default;
        for _ in 0..InvadeDifficulty::ALL.len() {
            at = at.next();
            seen.push(at);
        }
        assert_eq!(seen.first(), seen.last(), "a full cycle comes back round");
        assert_eq!(seen.len(), InvadeDifficulty::ALL.len() + 1);
    }

    /// The default changes nothing at all, which is what makes it safe to leave on.
    #[test]
    fn the_default_never_rewrites_the_band() {
        for own in ["0_0", "2_1", "6_3"] {
            assert_eq!(InvadeDifficulty::Default.band_for(own), None);
        }
    }

    /// Both numbers move together, because a host at a higher character level usually carries a
    /// higher weapon upgrade too.
    #[test]
    fn a_bracket_up_moves_both_axes() {
        assert_eq!(
            InvadeDifficulty::Hard.band_for("1_0").as_deref(),
            Some("2_1")
        );
        assert_eq!(
            InvadeDifficulty::Harder.band_for("1_0").as_deref(),
            Some("3_2")
        );
        assert_eq!(
            InvadeDifficulty::Hardest.band_for("1_0").as_deref(),
            Some("4_3")
        );
    }

    /// Neither axis is ever asked for above its ceiling. A band past the top matches nobody, and a
    /// difficulty that matches nobody is indistinguishable from one that does not work.
    #[test]
    fn every_difficulty_clamps_at_the_top_band() {
        for difficulty in InvadeDifficulty::ALL {
            for own in ["0_0", "2_1", "5_2", "6_3"] {
                let Some(asked) = difficulty.band_for(own) else {
                    continue;
                };
                let (level, weapon) =
                    crate::band_ladder::split_band(&asked).expect("the rewrite is a band value");
                assert!(level <= MAX_LEVEL_BAND, "{difficulty:?} {own} -> {asked}");
                assert!(weapon <= MAX_WEAPON_BAND, "{difficulty:?} {own} -> {asked}");
            }
        }
    }

    /// Godlike is absolute, so it asks for the same pair whatever the player's own bracket is.
    #[test]
    fn godlike_asks_for_the_top_bracket_regardless_of_the_player() {
        let top = format!("{MAX_LEVEL_BAND}_{MAX_WEAPON_BAND}");
        for own in ["0_0", "1_0", "2_1", "5_2"] {
            assert_eq!(
                InvadeDifficulty::Godlike.band_for(own).as_deref(),
                Some(&*top)
            );
        }
        // Already there: no rewrite, because asking for the band already being sent is not a
        // change and must not be logged as one.
        assert_eq!(InvadeDifficulty::Godlike.band_for(&top), None);
    }

    /// No difficulty ever asks below the player's own bracket, on either axis. Invading beneath
    /// your own band puts you on somebody weaker who never agreed to it, and every row on this
    /// list is named for being harder.
    #[test]
    fn no_difficulty_ever_descends() {
        for difficulty in InvadeDifficulty::ALL {
            for own in ["0_0", "1_0", "2_1", "5_2", "6_3"] {
                let (own_level, own_weapon) = crate::band_ladder::split_band(own).expect("a band");
                let Some(asked) = difficulty.band_for(own) else {
                    continue;
                };
                let (level, weapon) = crate::band_ladder::split_band(&asked).expect("a band");
                assert!(
                    level >= own_level && weapon >= own_weapon,
                    "{difficulty:?} turned {own} into {asked}, which is below the player"
                );
            }
        }
    }

    /// A value that is not a band is forwarded untouched, whatever the difficulty.
    ///
    /// The far half's query carries Seamless's own keys and this runs on every string filter in
    /// it, so a difficulty that rewrote anything band-shaped-ish would corrupt a field it has
    /// never identified.
    #[test]
    fn only_a_band_shaped_value_is_rewritten() {
        for value in ["", "_", "2_", "_1", "m61_48_45_00", "true", "2-1", "x_1"] {
            for difficulty in InvadeDifficulty::ALL {
                assert_eq!(
                    difficulty.band_for(value),
                    None,
                    "{difficulty:?} rewrote {value:?}, which is not a band"
                );
            }
        }
    }

    /// The atomic the DLL keeps this in stores an index, so the round trip has to be exact.
    #[test]
    fn every_difficulty_survives_the_index_round_trip() {
        for difficulty in InvadeDifficulty::ALL {
            assert_eq!(InvadeDifficulty::from_index(difficulty.index()), difficulty);
        }
        assert_eq!(
            InvadeDifficulty::from_index(usize::MAX),
            InvadeDifficulty::Default,
            "a torn or stale read costs Seamless's own behaviour, never the game"
        );
    }

    /// Each row says what it does to the far half specifically, because the near half is not
    /// touched and a note that omitted that would be describing a different feature.
    #[test]
    fn every_row_explains_itself_and_says_which_half_it_changes() {
        for difficulty in InvadeDifficulty::ALL {
            assert!(
                difficulty.note().contains("far half"),
                "{difficulty:?} does not say which half of the search it changes"
            );
        }
    }
}
