//! Starting classes, in the order the GAME stores them.
//!
//! `PlayerGameData::archetype` is one byte and `ArchetypeToInitParamId` is literally
//! `archetype + 3000`, so the byte is an index into `CharaInitParam` rows 3000..=3009 and the
//! order is that table's, not any list's on any website.
//!
//! # The order was wrong, and it was wrong in a way that reads as plausible
//!
//! Both halves of this repository carried the planner's DISPLAY order, in which Samurai comes
//! sixth. In `CharaInitParam` it is Confessor. So a Confessor exported as `Samurai`, and a build
//! saying `Samurai` imported as a Confessor -- two names swapped in a list of ten, which no smoke
//! test notices and which is exactly what was reported.
//!
//! # Proven from the game's own data, not from a wiki
//!
//! Read straight out of the installed `regulation.bin`: rows 3000..=3009 carry a starting level at
//! row offset 192 and a vigour at 194, and the pair identifies each class uniquely (`Confessor` is
//! the only class starting at level 10, `Samurai` the only level-9 class with 12 vigour). The rows
//! come out:
//!
//! ```text
//! 3000 Vagabond    lvl  9  vig 15      3005 Prophet     lvl  7  vig 10
//! 3001 Warrior     lvl  8  vig 11      3006 Confessor   lvl 10  vig 10
//! 3002 Hero        lvl  7  vig 14      3007 Samurai     lvl  9  vig 12
//! 3003 Bandit      lvl  5  vig 10      3008 Prisoner    lvl  9  vig 11
//! 3004 Astrologer  lvl  6  vig  9      3009 Wretch      lvl  1  vig 10
//! ```
//!
//! Reproduce with `scripts/regulation-params.py` (the row bytes) against the starting stats the
//! planner publishes for each class.

/// The ten starting classes, indexed by `PlayerGameData::archetype`.
///
/// See the module docs: this is `CharaInitParam` row order, and the two classes that look
/// interchangeable in it -- Confessor at 6, Samurai at 7 -- are the whole reason it is stated once
/// here rather than transcribed wherever it is needed.
pub const STARTING_CLASSES: [&str; 10] = [
    "Vagabond",
    "Warrior",
    "Hero",
    "Bandit",
    "Astrologer",
    "Prophet",
    "Confessor",
    "Samurai",
    "Prisoner",
    "Wretch",
];

/// `CharaInitParam` row id of the first starting class.
pub const FIRST_CHARA_INIT_PARAM_ROW: u32 = 3000;

/// The class an archetype byte names.
///
/// ```
/// use er_build_import_core::class::class_for_archetype;
/// assert_eq!(class_for_archetype(6), Some("Confessor"));
/// assert_eq!(class_for_archetype(7), Some("Samurai"));
/// assert_eq!(class_for_archetype(10), None);
/// ```
#[must_use]
pub fn class_for_archetype(archetype: u8) -> Option<&'static str> {
    STARTING_CLASSES.get(usize::from(archetype)).copied()
}

/// The archetype byte for a class name, however the build spelled it.
///
/// ```
/// use er_build_import_core::class::archetype_for_class;
/// assert_eq!(archetype_for_class("confessor"), Some(6));
/// assert_eq!(archetype_for_class("Samurai"), Some(7));
/// assert_eq!(archetype_for_class("Tarnished"), None);
/// ```
#[must_use]
pub fn archetype_for_class(name: &str) -> Option<u8> {
    STARTING_CLASSES
        .iter()
        .position(|class| class.eq_ignore_ascii_case(name))
        .and_then(|index| u8::try_from(index).ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The starting level and vigour of each class, keyed by the archetype byte -- the two fields
    /// read out of `CharaInitParam` rows 3000..=3009, which is what pins the order.
    const LEVEL_AND_VIGOUR: [(u8, u8); 10] = [
        (9, 15),
        (8, 11),
        (7, 14),
        (5, 10),
        (6, 9),
        (7, 10),
        (10, 10),
        (9, 12),
        (9, 11),
        (1, 10),
    ];

    /// The same figures as the planner publishes them, keyed by NAME. A class is identified by the
    /// pair, so this table and the one above agreeing is the order being right.
    const PUBLISHED: [(&str, u8, u8); 10] = [
        ("Vagabond", 9, 15),
        ("Warrior", 8, 11),
        ("Hero", 7, 14),
        ("Bandit", 5, 10),
        ("Astrologer", 6, 9),
        ("Prophet", 7, 10),
        ("Samurai", 9, 12),
        ("Prisoner", 9, 11),
        ("Confessor", 10, 10),
        ("Wretch", 1, 10),
    ];

    #[test]
    fn each_archetype_names_the_class_whose_starting_stats_that_row_holds() {
        for (archetype, (level, vigour)) in LEVEL_AND_VIGOUR.into_iter().enumerate() {
            let archetype = u8::try_from(archetype).expect("ten classes fit in a byte");
            let name = class_for_archetype(archetype).expect("every archetype names a class");
            let published = PUBLISHED
                .iter()
                .find(|(class, _, _)| *class == name)
                .expect("every class is published");
            assert_eq!(
                (published.1, published.2),
                (level, vigour),
                "archetype {archetype} says {name}, whose stats are {published:?}, but \
                 CharaInitParam row {} holds level {level} vigour {vigour}",
                u32::from(archetype) + FIRST_CHARA_INIT_PARAM_ROW,
            );
        }
    }

    #[test]
    fn the_two_that_were_swapped_are_the_way_the_game_has_them() {
        // The reported bug: a Confessor exported as `Samurai`.
        assert_eq!(class_for_archetype(6), Some("Confessor"));
        assert_eq!(class_for_archetype(7), Some("Samurai"));
        assert_eq!(archetype_for_class("Confessor"), Some(6));
        assert_eq!(archetype_for_class("Samurai"), Some(7));
    }

    #[test]
    fn every_name_round_trips() {
        for (index, name) in STARTING_CLASSES.into_iter().enumerate() {
            let archetype = archetype_for_class(name).expect("a listed class resolves");
            assert_eq!(usize::from(archetype), index);
            assert_eq!(class_for_archetype(archetype), Some(name));
        }
    }
}
