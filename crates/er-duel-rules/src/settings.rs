//! `er-duel-rules.toml` beside the game: the two keys and whether the break list is on screen.
//! Pure, so the parser, the defaults and the rewrite are tested on the host.
//!
//! The rules themselves are not here: they live in `er-duel-rules.json`, the file every referee
//! reads (`er_duel_rules_core::config`). This file is only how this DLL is driven.
//!
//! A change made in game rewrites only its own line (`er_hotkey_config::set_scalar`), so the
//! player's comments survive, and an edit to the file takes effect within a second
//! (`er_hotkey_config::HotFile`).

use er_hotkey_config::{Chord, parse_chord};

/// The settings file's name, beside `eldenring.exe`.
pub const FILE_NAME: &str = "er-duel-rules.toml";

/// The key that opens and closes the rules menu.
///
/// The game binds no function key. F1, F3 to F10, Insert, Delete, `]`, `;` and numpad `*` are
/// taken by shells in this workspace (er-invasion-warp, er-npc-possess, er-cel-shade,
/// er-render-scale and the rest), and F12 and shift+Tab by the Steam overlay. F2 is used by none.
pub const DEFAULT_MENU_KEY: &str = "F2";

/// The key that hides and shows the rule-break list. A key matches only with exactly its own
/// modifiers ([`Settings`] keys are compared that way by the menu), so this and plain F2 never
/// both fire.
pub const DEFAULT_BREAKS_KEY: &str = "ctrl+F2";

/// Everything the settings file holds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    /// Opens and closes the rules menu, in `er-hotkey-config`'s spelling.
    pub menu_key: String,
    /// Hides and shows the rule-break list.
    pub breaks_key: String,
    /// The rule-break list is drawn. Breaks are filed and logged either way.
    pub show_breaks: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            menu_key: DEFAULT_MENU_KEY.to_owned(),
            breaks_key: DEFAULT_BREAKS_KEY.to_owned(),
            show_breaks: true,
        }
    }
}

/// The comment written above `menu_key` when the file has no such line.
pub const MENU_KEY_COMMENT: &str = "# The key that opens and closes the rules menu: a name such as \"F2\", \"ctrl+F2\", \"]\"\n\
     # or \"KP_Plus\". Escape also closes it.";

/// The comment written above `breaks_key` when the file has no such line.
pub const BREAKS_KEY_COMMENT: &str =
    "# The key that hides and shows the rule-break list in the corner of the screen.";

/// The comment written above `show_breaks` when the file has no such line.
pub const SHOW_BREAKS_COMMENT: &str = "# Whether the rule-break list is on screen. Every break is written to\n\
     # er-duel-rules-breaks.txt and er-duel-rules.log whether it is shown or not.";

/// The file the DLL writes when there is none, with the defaults. A test holds it equal to
/// [`render`] of [`Settings::default`] and to the comments above.
pub const DEFAULT_CONFIG_TOML: &str = r#"# er-duel-rules settings. The rules themselves (which are on, the numbers, your own bans) are
# in er-duel-rules.json, which the in-game menu, the Cheat Engine table and the Frida agent share.
# The menu rewrites the show_breaks line when you hide or show the list; everything else in this
# file is left as you wrote it.

# The key that opens and closes the rules menu: a name such as "F2", "ctrl+F2", "]"
# or "KP_Plus". Escape also closes it.
menu_key = "F2"

# The key that hides and shows the rule-break list in the corner of the screen.
breaks_key = "ctrl+F2"

# Whether the rule-break list is on screen. Every break is written to
# er-duel-rules-breaks.txt and er-duel-rules.log whether it is shown or not.
show_breaks = true
"#;

/// The file with `settings` in it: the default file with its values set.
#[must_use]
pub fn render(settings: &Settings) -> String {
    let text = with_key(DEFAULT_CONFIG_TOML, "menu_key", &settings.menu_key);
    let text = with_key(&text, "breaks_key", &settings.breaks_key);
    with_show_breaks(&text, settings.show_breaks)
}

/// `text` with one key line set to `spelling`, every other byte kept.
#[must_use]
pub fn with_key(text: &str, key: &str, spelling: &str) -> String {
    let comment = if key == "menu_key" {
        MENU_KEY_COMMENT
    } else {
        BREAKS_KEY_COMMENT
    };
    er_hotkey_config::set_scalar(text, key, &format!("\"{spelling}\""), comment)
}

/// `text` with its `show_breaks` line set, every other byte kept.
#[must_use]
pub fn with_show_breaks(text: &str, show: bool) -> String {
    er_hotkey_config::set_scalar(
        text,
        "show_breaks",
        if show { "true" } else { "false" },
        SHOW_BREAKS_COMMENT,
    )
}

/// The value of a `key = value` line, without its trailing comment and surrounding space.
fn value_of(line: &str) -> Option<(&str, &str)> {
    let line = line.trim();
    if line.is_empty() || line.starts_with('#') || line.starts_with('[') {
        return None;
    }
    let (key, rest) = line.split_once('=')?;
    let value = match rest.trim_start().strip_prefix('"') {
        // A quoted value runs to its closing quote, so a `#` inside it is not a comment.
        Some(quoted) => quoted.split_once('"').map_or(quoted, |(inside, _)| inside),
        None => rest.split('#').next().unwrap_or(rest).trim(),
    };
    Some((key.trim(), value))
}

/// Read the file: the settings, and a line for each value that was not understood. A value not
/// understood keeps its default, so a typo never leaves the menu without a key.
#[must_use]
pub fn parse(text: &str) -> (Settings, Vec<String>) {
    let mut settings = Settings::default();
    let mut warnings = Vec::new();
    let mut seen = Vec::new();
    for (number, line) in text.lines().enumerate() {
        let Some((key, value)) = value_of(line) else {
            continue;
        };
        if seen.contains(&key) {
            // The first assignment wins, as `set_scalar` rewrites the first.
            continue;
        }
        seen.push(key);
        let line = number + 1;
        match key {
            "menu_key" | "breaks_key" => {
                let slot = if key == "menu_key" {
                    &mut settings.menu_key
                } else {
                    &mut settings.breaks_key
                };
                match parse_chord(value) {
                    Ok(_) => value.clone_into(slot),
                    Err(error) => warnings.push(format!(
                        "line {line}: {key} `{value}` not understood ({error}); using {slot}"
                    )),
                }
            }
            "show_breaks" => match value {
                "true" => settings.show_breaks = true,
                "false" => settings.show_breaks = false,
                _ => warnings.push(format!(
                    "line {line}: show_breaks `{value}` is not true or false; using {}",
                    settings.show_breaks
                )),
            },
            other => warnings.push(format!("line {line}: unknown key `{other}`")),
        }
    }
    if parse_chord(&settings.menu_key).ok() == parse_chord(&settings.breaks_key).ok() {
        warnings.push(format!(
            "menu_key and breaks_key are the same key ({}); the menu keeps it and the list has \
             no key",
            settings.menu_key
        ));
    }
    (settings, warnings)
}

/// `spelling`'s chord, or `fallback`'s when it is not a key.
#[must_use]
pub fn chord_or(spelling: &str, fallback: &str) -> Option<Chord> {
    parse_chord(spelling)
        .or_else(|_| parse_chord(fallback))
        .ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_absent_file_is_the_defaults() {
        let (settings, warnings) = parse("");
        assert_eq!(settings, Settings::default());
        assert!(warnings.is_empty(), "{warnings:?}");
        assert!(settings.show_breaks);
    }

    #[test]
    fn the_rendered_file_reads_back_as_itself() {
        let settings = Settings {
            menu_key: "]".to_owned(),
            breaks_key: "alt+F2".to_owned(),
            show_breaks: false,
        };
        let (read, warnings) = parse(&render(&settings));
        assert_eq!(read, settings);
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(render(&Settings::default()), DEFAULT_CONFIG_TOML);
        assert_eq!(parse(DEFAULT_CONFIG_TOML).0, Settings::default());
        for comment in [MENU_KEY_COMMENT, BREAKS_KEY_COMMENT, SHOW_BREAKS_COMMENT] {
            assert!(DEFAULT_CONFIG_TOML.contains(comment), "{comment}");
        }
        assert!(DEFAULT_CONFIG_TOML.is_ascii());
    }

    #[test]
    fn a_value_not_understood_keeps_its_default_and_says_so() {
        let (settings, warnings) = parse("menu_key = \"Hyper\"\nshow_breaks = maybe\nzoom = 2\n");
        assert_eq!(settings, Settings::default());
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        let (_, warnings) = parse("breaks_key = \"F2\"\n");
        assert_eq!(
            warnings.len(),
            1,
            "the same key twice is reported: {warnings:?}"
        );
    }

    #[test]
    fn hiding_the_list_rewrites_only_its_line() {
        let text = "# mine\nmenu_key = \"F8\" # moved\nshow_breaks = true # on\n";
        let written = with_show_breaks(text, false);
        assert!(written.contains("# mine\nmenu_key = \"F8\" # moved\n"));
        assert!(written.contains("show_breaks = false # on"));
        assert!(!parse(&written).0.show_breaks);
        let added = with_show_breaks("menu_key = \"F2\"\n", false);
        assert!(!parse(&added).0.show_breaks);
    }

    #[test]
    fn the_two_default_keys_differ_only_by_ctrl() {
        let menu = parse_chord(DEFAULT_MENU_KEY).unwrap();
        let breaks = parse_chord(DEFAULT_BREAKS_KEY).unwrap();
        assert_eq!(menu.vk, breaks.vk);
        assert_ne!(menu.modifiers, breaks.modifiers);
        assert_eq!(chord_or("not a key", DEFAULT_MENU_KEY), Some(menu));
    }
}
