//! `er-positional-voice.toml` beside the game. A missing file, key or unreadable value keeps the
//! default.
//!
//! ```toml
//! # Volume applied to every voice (1 = as received). 8 is about +18 dB.
//! gain = 8.0
//! # Play your own microphone at your own character, to test alone.
//! loopback = false
//! # The key that turns voice chat on and off: the game's own Network > Voice Chat setting.
//! toggle_key = "F7"
//! # Say "voice chat on" / "voice chat off" after the key: "en", or "" for silence.
//! announce = "en"
//! ```

use er_hotkey_config::keys::{Chord, parse_chord};

use crate::announce::{Announce, DEFAULT_ANNOUNCE};
use crate::gain::DEFAULT_GAIN;

pub const FILE_NAME: &str = "er-positional-voice.toml";

/// The toggle key when the file does not name one. Clear of every other default in this
/// workspace: F2 duel rules, F3/F4/F8 invasion warp, F5 render scale, F6 cel shade, F9 NPC
/// possess, F10 the NPC summons picker.
pub const DEFAULT_TOGGLE_KEY: &str = "F7";

/// What the DLL writes when the file is absent, so the keys are discoverable.
pub const DEFAULT_TEXT: &str = r#"# Volume applied to every voice (1 = as received). 8 is about +18 dB.
gain = 8.0
# Play your own microphone at your own character, to test alone.
loopback = false
# The key that turns voice chat on and off: the game's own Network > Voice Chat setting.
# A name such as "F7", "ctrl+V" or "KP_Plus"; "" for no key.
toggle_key = "F7"
# Say "voice chat on" / "voice chat off" after the key: "en", or "" for silence.
announce = "en"
"#;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Config {
    pub gain: f32,
    pub loopback: bool,
    /// `None` when the file names no key (`""`).
    pub toggle_key: Option<Chord>,
    pub announce: Announce,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            gain: DEFAULT_GAIN,
            loopback: false,
            toggle_key: parse_chord(DEFAULT_TOGGLE_KEY).ok(),
            announce: DEFAULT_ANNOUNCE,
        }
    }
}

/// A TOML string value without its quotes; a bare value as written.
fn unquote(value: &str) -> &str {
    value
        .strip_prefix('"')
        .and_then(|v| v.strip_suffix('"'))
        .unwrap_or(value)
}

impl Config {
    pub fn parse(text: &str) -> Self {
        let mut c = Self::default();
        for line in text.lines() {
            let line = line.split('#').next().unwrap_or("").trim();
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            let value = value.trim();
            match key.trim() {
                "gain" => {
                    if let Ok(g) = value.parse::<f32>()
                        && g.is_finite()
                        && (0.0..=64.0).contains(&g)
                    {
                        c.gain = g;
                    }
                }
                "loopback" => match value {
                    "true" => c.loopback = true,
                    "false" => c.loopback = false,
                    _ => {}
                },
                "toggle_key" => match unquote(value).trim() {
                    "" => c.toggle_key = None,
                    name => {
                        if let Ok(chord) = parse_chord(name) {
                            c.toggle_key = Some(chord);
                        }
                    }
                },
                "announce" => {
                    if let Some(a) = Announce::parse(unquote(value)) {
                        c.announce = a;
                    }
                }
                _ => {}
            }
        }
        c
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_text_is_the_default() {
        assert_eq!(Config::parse(DEFAULT_TEXT), Config::default());
    }

    #[test]
    fn the_default_key_parses() {
        assert!(Config::default().toggle_key.is_some());
    }

    #[test]
    fn reads_keys_and_ignores_junk() {
        let c = Config::parse(
            "gain = 2 # quieter\nloopback = true\nother = 1\ntoggle_key = \"ctrl+V\"\nannounce = \"\"\n",
        );
        assert_eq!(c.gain, 2.0);
        assert!(c.loopback);
        assert_eq!(c.toggle_key, parse_chord("ctrl+V").ok());
        assert_eq!(c.announce, Announce::Silent);
        let c = Config::parse(
            "gain = loud\ngain = -1\ngain = 1e9\nloopback = yes\ntoggle_key = \"NoSuchKey\"\nannounce = \"xx\"",
        );
        assert_eq!(c, Config::default());
    }

    #[test]
    fn an_empty_key_unbinds() {
        assert_eq!(Config::parse("toggle_key = \"\"").toggle_key, None);
    }
}
