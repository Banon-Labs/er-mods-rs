//! What one config edit asks the patches to do.
//!
//! The game task re-reads the config through `er_hotkey_config::HotFile` and hands the old and the
//! new [`Config`] to [`transitions`], which answers with the patches to put in and the patches to
//! take out. Everything here is free of Windows types, so the whole decision is `cargo test`-able
//! on the host; the game task only carries the answer out.
//!
//! # Why the diff is taken between two configs, not between a config and the game
//!
//! A patch that was asked for and then disarmed (the bytes at its address were not the measured
//! ones) is still on in the config. Diffing against what is actually patched would retry it every
//! time the file is read, and log the same refusal once a second for the rest of the session.
//! Diffing config against config retries it only when the player edits that option again, which is
//! when a retry can have a different answer -- another mod unloaded, or a typo corrected.

use er_hotkey_config::FileChange;

use crate::config::{self, Config};

/// What to do to one patch.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Action {
    /// The option went from off to on: verify the window and write the replacement byte.
    Apply,
    /// The option went from on to off: put back the byte saved when the patch went in.
    Restore,
}

/// One option whose state an edit changed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Transition {
    pub(crate) key: &'static str,
    pub(crate) action: Action,
}

/// The config a file change stands for.
///
/// A file that disappears mid-session means every option is off, the same as the all-off file
/// written on first run. Anything else would leave a patch in force that no file on disk asks for.
pub(crate) fn config_for(change: &FileChange) -> Config {
    match change {
        FileChange::Text(text) => config::parse(text),
        FileChange::Missing => Config::default(),
    }
}

/// The patches an edit from `old` to `new` turns on or off, in `keys` order.
///
/// `keys` is the registry's config keys. A key in either config that is not in `keys` is a typo or
/// a retired option and produces nothing; an option whose state did not change produces nothing,
/// so re-saving an unchanged file touches no game byte.
pub(crate) fn transitions(keys: &[&'static str], old: &Config, new: &Config) -> Vec<Transition> {
    keys.iter()
        .filter_map(|&key| {
            match (old.is_enabled(key), new.is_enabled(key)) {
                (false, true) => Some(Action::Apply),
                (true, false) => Some(Action::Restore),
                _ => None,
            }
            .map(|action| Transition { key, action })
        })
        .collect()
}

/// Whether a restore may write, given the byte at the patched address right now.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RestoreCheck {
    /// The byte is still the one this DLL wrote, so putting the original back undoes exactly that.
    Restore,
    /// The byte is already the original -- nothing to write.
    AlreadyOriginal,
    /// The byte is neither: something else rewrote it after this DLL did. Writing the original
    /// back would undo that other writer's change, so nothing is written.
    Foreign,
}

/// Decide a restore from the byte currently at the patched address.
pub(crate) fn check_restore(current: u8, replacement: u8, original: u8) -> RestoreCheck {
    if current == replacement {
        RestoreCheck::Restore
    } else if current == original {
        RestoreCheck::AlreadyOriginal
    } else {
        RestoreCheck::Foreign
    }
}

/// `ON` / `OFF`, for the transition log line.
pub(crate) const fn state_word(on: bool) -> &'static str {
    if on { "ON" } else { "OFF" }
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEYS: &[&str] = &[
        "keep_runes_on_death",
        "keep_rune_arc_on_death",
        "quicker_deaths",
    ];

    fn apply(key: &'static str) -> Transition {
        Transition {
            key,
            action: Action::Apply,
        }
    }

    fn restore(key: &'static str) -> Transition {
        Transition {
            key,
            action: Action::Restore,
        }
    }

    /// The first read is diffed against nothing, so it applies exactly what the file enables --
    /// the behaviour the DLL had when it read the file once at startup.
    #[test]
    fn the_first_read_applies_what_the_file_enables() {
        let first = config::parse("keep_runes_on_death = true\nquicker_deaths = true\n");
        assert_eq!(
            transitions(KEYS, &Config::default(), &first),
            vec![apply("keep_runes_on_death"), apply("quicker_deaths")]
        );
    }

    #[test]
    fn an_all_off_first_read_does_nothing() {
        let first = config::parse("keep_runes_on_death = false\n");
        assert!(transitions(KEYS, &Config::default(), &first).is_empty());
    }

    #[test]
    fn turning_an_option_off_restores_it() {
        let old = config::parse("keep_runes_on_death = true\n");
        let new = config::parse("keep_runes_on_death = false\n");
        assert_eq!(
            transitions(KEYS, &old, &new),
            vec![restore("keep_runes_on_death")]
        );
    }

    /// One edit can move several options at once, in both directions; each is reported once.
    #[test]
    fn a_mixed_edit_reports_each_change_in_registry_order() {
        let old = config::parse("keep_runes_on_death = true\nquicker_deaths = true\n");
        let new = config::parse("keep_rune_arc_on_death = true\nquicker_deaths = true\n");
        assert_eq!(
            transitions(KEYS, &old, &new),
            vec![
                restore("keep_runes_on_death"),
                apply("keep_rune_arc_on_death")
            ]
        );
    }

    /// A re-save, a comment edit, or reordered lines must not touch a single game byte.
    #[test]
    fn an_edit_that_changes_no_option_does_nothing() {
        let old = config::parse("quicker_deaths = true\n");
        let new = config::parse("# a new comment\n\n   quicker_deaths   =   true   # why not\n");
        assert!(transitions(KEYS, &old, &new).is_empty());
    }

    /// A key the registry does not know cannot name a patch, in either direction.
    #[test]
    fn unknown_keys_produce_nothing() {
        let old = config::parse("keep_runes = true\n");
        let new = config::parse("quicker_death = true\n");
        assert!(transitions(KEYS, &old, &new).is_empty());
    }

    /// Deleting the file mid-session turns every option off, so everything in force is restored.
    #[test]
    fn a_missing_file_restores_everything_in_force() {
        let old = config::parse("keep_runes_on_death = true\nkeep_rune_arc_on_death = true\n");
        let new = config_for(&FileChange::Missing);
        assert_eq!(
            transitions(KEYS, &old, &new),
            vec![
                restore("keep_runes_on_death"),
                restore("keep_rune_arc_on_death")
            ]
        );
    }

    #[test]
    fn a_text_change_is_parsed() {
        let config = config_for(&FileChange::Text("quicker_deaths = true\n".to_owned()));
        assert!(config.is_enabled("quicker_deaths"));
    }

    /// On, off, on: each edge is one action, so a patch can be put back in after a restore.
    #[test]
    fn toggling_back_and_forth_alternates_apply_and_restore() {
        let on = config::parse("quicker_deaths = true\n");
        let off = config::parse("quicker_deaths = false\n");
        assert_eq!(
            transitions(KEYS, &Config::default(), &on),
            vec![apply("quicker_deaths")]
        );
        assert_eq!(
            transitions(KEYS, &on, &off),
            vec![restore("quicker_deaths")]
        );
        assert_eq!(transitions(KEYS, &off, &on), vec![apply("quicker_deaths")]);
    }

    #[test]
    fn a_restore_writes_only_over_its_own_byte() {
        const RET: u8 = 0xc3;
        const ORIGINAL: u8 = 0x48;
        assert_eq!(check_restore(RET, RET, ORIGINAL), RestoreCheck::Restore);
        assert_eq!(
            check_restore(ORIGINAL, RET, ORIGINAL),
            RestoreCheck::AlreadyOriginal
        );
        assert_eq!(check_restore(0xcc, RET, ORIGINAL), RestoreCheck::Foreign);
    }

    #[test]
    fn state_words_match_the_log_vocabulary() {
        assert_eq!(state_word(true), "ON");
        assert_eq!(state_word(false), "OFF");
    }
}
