//! `er-positional-voice.toml` beside the game. Two keys; a missing file, key or unreadable value
//! keeps the default.
//!
//! ```toml
//! # Volume applied to every voice (1 = as received). 8 is about +18 dB.
//! gain = 8.0
//! # Play your own microphone at your own character, to test alone.
//! loopback = false
//! ```

use crate::gain::DEFAULT_GAIN;

pub const FILE_NAME: &str = "er-positional-voice.toml";

/// What the DLL writes when the file is absent, so the keys are discoverable.
pub const DEFAULT_TEXT: &str = r#"# Volume applied to every voice (1 = as received). 8 is about +18 dB.
gain = 8.0
# Play your own microphone at your own character, to test alone.
loopback = false
"#;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Config {
    pub gain: f32,
    pub loopback: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            gain: DEFAULT_GAIN,
            loopback: false,
        }
    }
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
    fn reads_keys_and_ignores_junk() {
        let c = Config::parse("gain = 2 # quieter\nloopback = true\nother = 1\n");
        assert_eq!(
            c,
            Config {
                gain: 2.0,
                loopback: true
            }
        );
        let c = Config::parse("gain = loud\ngain = -1\ngain = 1e9\nloopback = yes");
        assert_eq!(c, Config::default());
    }
}
