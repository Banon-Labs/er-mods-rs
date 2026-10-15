//! `er-render-scale.toml` beside the game: the menu key and the divisor. Pure, so the parser, the
//! defaults and the rewrite are tested on the host.
//!
//! The menu writes the divisor back with `er_hotkey_config::set_scalar`, which rewrites that one
//! line in place, so a player's comments and key survive the menu saving. The worker rereads the
//! file when its text changes (`er_hotkey_config::HotFile`), so an edit to either line takes
//! effect without a restart.

use er_hotkey_config::{Chord, parse_chord};

/// The settings file's name, beside `eldenring.exe`.
pub const FILE_NAME: &str = "er-render-scale.toml";

/// The default menu key.
///
/// Chosen the way er-cel-shade's `DEFAULT_MENU_KEY` (F6) was: the game binds no function key, and
/// of the function keys F1, F3, F4, F6, F7, F8, F9 and F10 are taken by shells in this workspace
/// (er-invasion-warp, er-npc-possess, er-cel-shade and the rest), as are Insert, Delete, `]`, `;`,
/// numpad `*` and ctrl+alt+c. F5 is used by none of them, sits beside the Shaders panel's F6, and
/// is not one of the keys the Steam overlay or Proton take (shift+Tab, F12). The table of every
/// shipped default lives in `er-hotkey-conflicts` (`declared.rs`), whose test fails when two of
/// them collide.
pub const DEFAULT_MENU_KEY: &str = "F5";

/// The largest divisor the file may hold; nothing on a real screen comes close.
pub const MAX_DIVISOR: u32 = 4096;

/// Everything the settings file holds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    /// The key that opens and closes the menu, in `er-hotkey-config`'s spelling.
    pub menu_key: String,
    /// The scene is drawn at the output size divided by this; 1 is native. Applied only when it is
    /// a clean divisor of the screen the game draws to (`crate::scale::is_clean`).
    pub divisor: u32,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            menu_key: DEFAULT_MENU_KEY.to_owned(),
            divisor: crate::scale::NATIVE,
        }
    }
}

/// The comment written above `menu_key` when the file has no such line.
pub const MENU_KEY_COMMENT: &str = "# The key that opens and closes the render scale menu: a name such as \"F5\", \"ctrl+F5\",\n\
     # \"]\" or \"KP_Plus\". Escape also closes it.";

/// The comment written above `divisor` when the file has no such line.
pub const DIVISOR_COMMENT: &str = "# The 3D scene is drawn at the screen's size divided by this, and the UI stays at the\n\
     # screen's size. 1 is native. The menu offers only the values that divide the screen into\n\
     # whole pixels (at 3840x2160: 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30 and 60); any other\n\
     # value here is kept but draws at native until the screen allows it.";

/// The file the DLL writes when there is none, with the defaults. A literal, so the installer's
/// settings walkthrough (`scripts/gen-installer-settings.py`) offers exactly these bytes; a test
/// holds it equal to [`render`] of [`Settings::default`] and to the two comments above.
pub const DEFAULT_CONFIG_TOML: &str = r#"# er-render-scale settings. The in-game menu rewrites the divisor line when you move the
# slider; everything else in this file is left as you wrote it.

# The key that opens and closes the render scale menu: a name such as "F5", "ctrl+F5",
# "]" or "KP_Plus". Escape also closes it.
menu_key = "F5"

# The 3D scene is drawn at the screen's size divided by this, and the UI stays at the
# screen's size. 1 is native. The menu offers only the values that divide the screen into
# whole pixels (at 3840x2160: 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30 and 60); any other
# value here is kept but draws at native until the screen allows it.
divisor = 1
"#;

/// The file with `settings` in it: the default file with its two values set.
#[must_use]
pub fn render(settings: &Settings) -> String {
    let keyed = er_hotkey_config::set_scalar(
        DEFAULT_CONFIG_TOML,
        "menu_key",
        &format!("\"{}\"", settings.menu_key),
        MENU_KEY_COMMENT,
    );
    with_divisor(&keyed, settings.divisor)
}

/// `text` with its divisor line set to `divisor`, every other byte kept.
#[must_use]
pub fn with_divisor(text: &str, divisor: u32) -> String {
    er_hotkey_config::set_scalar(text, "divisor", &divisor.to_string(), DIVISOR_COMMENT)
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
        match key {
            "menu_key" => match parse_chord(value) {
                Ok(_) => value.clone_into(&mut settings.menu_key),
                Err(error) => warnings.push(format!(
                    "line {}: menu_key `{value}` not understood ({error}); using {}",
                    number + 1,
                    settings.menu_key
                )),
            },
            "divisor" => match value.parse::<u32>() {
                Ok(divisor) if (1..=MAX_DIVISOR).contains(&divisor) => settings.divisor = divisor,
                _ => warnings.push(format!(
                    "line {}: divisor `{value}` is not a whole number from 1 to {MAX_DIVISOR}; \
                     using {}",
                    number + 1,
                    settings.divisor
                )),
            },
            other => warnings.push(format!("line {}: unknown key `{other}`", number + 1)),
        }
    }
    (settings, warnings)
}

/// The menu key's chord, or the default's when the spelling is not one.
#[must_use]
pub fn menu_chord(spelling: &str) -> Chord {
    parse_chord(spelling)
        .or_else(|_| parse_chord(DEFAULT_MENU_KEY))
        .unwrap_or_else(|_| Chord::from_virtual_key(VK_F5))
}

/// `VK_F5`, for the one case where even the default's spelling fails to parse.
const VK_F5: u32 = 0x74;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_absent_file_is_the_defaults() {
        let (settings, warnings) = parse("");
        assert_eq!(settings, Settings::default());
        assert!(warnings.is_empty());
        assert_eq!(settings.menu_key, "F5");
        assert_eq!(settings.divisor, 1);
    }

    #[test]
    fn the_rendered_file_reads_back_as_itself() {
        let settings = Settings {
            menu_key: "ctrl+F5".to_owned(),
            divisor: 3,
        };
        let (read, warnings) = parse(&render(&settings));
        assert_eq!(read, settings);
        assert!(warnings.is_empty(), "{warnings:?}");
        // The defaults are the literal the installer offers, and the comments are its own.
        assert_eq!(render(&Settings::default()), DEFAULT_CONFIG_TOML);
        assert_eq!(parse(DEFAULT_CONFIG_TOML).0, Settings::default());
        assert!(DEFAULT_CONFIG_TOML.contains(MENU_KEY_COMMENT));
        assert!(DEFAULT_CONFIG_TOML.contains(DIVISOR_COMMENT));
        assert!(DEFAULT_CONFIG_TOML.is_ascii());
    }

    #[test]
    fn a_value_not_understood_keeps_its_default_and_says_so() {
        let (settings, warnings) = parse("menu_key = \"Hyper\"\ndivisor = 0\nzoom = 2\n");
        assert_eq!(settings, Settings::default());
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        assert!(warnings[0].contains("menu_key"));
        assert!(warnings[1].contains("divisor"));
        assert!(warnings[2].contains("zoom"));
        let (settings, _) = parse("divisor = 5000\n");
        assert_eq!(settings.divisor, 1);
    }

    #[test]
    fn comments_and_the_first_assignment_rule() {
        let text = "menu_key = \"]\" # the bracket\ndivisor = 4 # quarter\ndivisor = 2\n";
        let (settings, warnings) = parse(text);
        assert_eq!(settings.menu_key, "]");
        assert_eq!(settings.divisor, 4);
        assert!(warnings.is_empty());
        // A `#` inside quotes is part of the value, not a comment.
        assert_eq!(
            value_of("menu_key = \"a#b\" # c"),
            Some(("menu_key", "a#b"))
        );
        assert_eq!(value_of("# divisor = 3"), None);
    }

    #[test]
    fn the_slider_rewrites_only_the_divisor_line() {
        let text = "# mine\nmenu_key = \"F8\" # moved off F5\ndivisor = 2 # half\n";
        let written = with_divisor(text, 3);
        assert!(written.contains("# mine\nmenu_key = \"F8\" # moved off F5\n"));
        assert!(written.contains("divisor = 3 # half"));
        assert_eq!(parse(&written).0.divisor, 3);
        // A file with no divisor line gains one, with its comment.
        let added = with_divisor("menu_key = \"F5\"\n", 4);
        assert_eq!(parse(&added).0.divisor, 4);
        assert!(added.contains("divisor = 4"));
    }

    #[test]
    fn the_menu_key_falls_back_to_f5() {
        assert_eq!(menu_chord("F5"), menu_chord("not a key"));
        assert_eq!(menu_chord(DEFAULT_MENU_KEY).vk, VK_F5);
    }
}
