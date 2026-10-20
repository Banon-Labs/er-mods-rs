//! The game-directory config that decides which patches are in force.
//!
//! Three booleans, re-read while the game runs. It is deliberately not a general TOML parser: the
//! whole grammar is `key = true` / `key = false`, which keeps the crate free of a parser dependency
//! and keeps this file host-testable without a game.
//!
//! # Where it lives
//!
//! `er-convenient-deaths.toml` in the game directory, beside `er-convenient-deaths.log` and the
//! other `er-*.toml` files. It used to sit beside this DLL under the DLL's own name. The only
//! reason the code gave for that was not to guess a directory when the DLL's own path was unknown;
//! the game directory is resolved the same way the log already is, so nothing is lost by moving.
//!
//! # Why every patch is off until asked for
//!
//! Loading a DLL is normally this workspace's enable switch -- a `[[natives]]` entry is the
//! toggle. That does not fit here, because the three patches are not one feature: keeping every
//! rune you have ever earned is a different decision from shortening a fade-out, and a player who
//! wants the second has no way to decline the first if presence enables both. So presence gets the
//! DLL, and the file gets the three decisions -- each of which can be changed, and undone, while
//! the game runs.

/// The file's name, in the game directory. Written on first run if absent.
pub(crate) const CONFIG_FILE_NAME: &str = "er-convenient-deaths.toml";

/// Which patches the player asked for.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct Config {
    enabled: Vec<String>,
}

impl Config {
    /// Whether `key` was set to `true`.
    pub(crate) fn is_enabled(&self, key: &str) -> bool {
        self.enabled.iter().any(|found| found == key)
    }

    /// How many keys were switched on. Only the tests ask: the runtime diffs configs instead.
    #[cfg(test)]
    pub(crate) fn enabled_count(&self) -> usize {
        self.enabled.len()
    }
}

/// Parse the config.
///
/// Unknown keys are ignored rather than rejected: this file is edited by hand, and a typo that
/// silently disables one patch is a better outcome than one that refuses to load the rest. Every
/// reload logs each option's state, so a typo shows up there as an option that stayed off.
pub(crate) fn parse(contents: &str) -> Config {
    let mut enabled = Vec::new();
    for line in contents.lines() {
        let line = line.split('#').next().unwrap_or("").trim();
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if value.trim() == "true" {
            enabled.push(key.trim().to_owned());
        }
    }
    Config { enabled }
}

/// The file written when none exists, which is also the documentation a player reads.
pub(crate) fn boilerplate(keys: &[(&str, &str)]) -> String {
    let mut out = String::from(
        "# er-convenient-deaths\n\
         #\n\
         # Every option below is OFF. Each one rewrites a single byte of the game's own code.\n\
         #\n\
         # Changes apply live, with no restart: this file is re-read about once a second while\n\
         # the game runs. Setting an option to true patches its byte in; setting it back to\n\
         # false puts the game's original byte back. er-convenient-deaths.log beside this file\n\
         # records each change.\n\
         #\n\
         # Each option is applied only if the bytes at its address are the ones it was measured\n\
         # against, which is why a game update turns a patch off rather than breaking it.\n\
         #\n\
         # These are single-player conveniences. They change what dying costs, so do not expect\n\
         # them to be welcome in someone else's session.\n\
         #\n",
    );
    for (key, effect) in keys {
        out.push_str(&format!("\n# {effect}\n{key} = false\n"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_set_true_is_enabled() {
        let config = parse("keep_runes_on_death = true\n");
        assert!(config.is_enabled("keep_runes_on_death"));
        assert_eq!(config.enabled_count(), 1);
    }

    #[test]
    fn false_and_absent_are_both_off() {
        let config = parse("keep_runes_on_death = false\n");
        assert!(!config.is_enabled("keep_runes_on_death"));
        assert!(!config.is_enabled("quicker_deaths"));
        assert_eq!(config.enabled_count(), 0);
    }

    /// The default file must not enable anything -- that is the whole contract of writing it.
    #[test]
    fn the_boilerplate_written_on_first_run_enables_nothing() {
        let keys = [
            ("keep_runes_on_death", "keeps your runes"),
            ("quicker_deaths", "shortens the fade"),
        ];
        let config = parse(&boilerplate(&keys));
        assert_eq!(config.enabled_count(), 0);
        for (key, _) in keys {
            assert!(!config.is_enabled(key), "{key} was on in the default file");
        }
    }

    /// Every key the registry offers has to appear in the file a player is handed, or the only
    /// way to discover it is to read this source.
    #[test]
    fn the_boilerplate_names_every_key_it_is_given() {
        let keys = [("keep_runes_on_death", "a"), ("quicker_deaths", "b")];
        let written = boilerplate(&keys);
        for (key, effect) in keys {
            assert!(written.contains(key), "{key} missing from the default file");
            assert!(written.contains(effect), "{key}'s effect line is missing");
        }
    }

    /// The header is what a player reads before editing, and it used to say a change needed a
    /// restart. It must now say the opposite, and never both.
    #[test]
    fn the_boilerplate_says_changes_apply_live() {
        let written = boilerplate(&[]);
        assert!(written.contains("Changes apply live"));
        assert!(!written.contains("needs a restart"));
    }

    #[test]
    fn a_commented_out_key_is_not_enabled() {
        let config = parse("# keep_runes_on_death = true\n");
        assert!(!config.is_enabled("keep_runes_on_death"));
    }

    #[test]
    fn a_trailing_comment_does_not_defeat_the_value() {
        let config = parse("quicker_deaths = true # why not\n");
        assert!(config.is_enabled("quicker_deaths"));
    }

    #[test]
    fn whitespace_and_blank_lines_are_tolerated() {
        let config = parse("\n\n   quicker_deaths   =   true   \n\n");
        assert!(config.is_enabled("quicker_deaths"));
    }

    /// A value that is not exactly `true` is off. Anything else would make `tru` or `1` a
    /// difficulty change nobody asked for.
    #[test]
    fn only_the_literal_true_enables() {
        for value in ["1", "yes", "True", "TRUE", "tru", "\"true\""] {
            let config = parse(&format!("quicker_deaths = {value}\n"));
            assert!(
                !config.is_enabled("quicker_deaths"),
                "{value} should not have enabled the patch"
            );
        }
    }
}
