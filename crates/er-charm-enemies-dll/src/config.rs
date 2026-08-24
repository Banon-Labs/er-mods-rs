//! `er-charm-enemies.toml`, read once at attach.
//!
//! Hand-parsed rather than pulled through a TOML crate: four scalar settings do not justify a
//! dependency in a DLL that is cross-compiled into the game process.

// Windows-only in practice; kept portable so the parser below is covered by `cargo test` on the
// host, where the windows-gated modules that consume it are compiled out.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{fs, path::PathBuf, sync::OnceLock};

use crate::{
    keys::{Hotkey, parse_hotkey},
    log::charm_log,
};

const CONFIG_FILE_NAME: &str = "er-charm-enemies.toml";

/// SpEffect 20503350, `[Item] Charming Branch` in `SpEffectParam`.
///
/// Its `stateInfo` is 132 (`0x84`), and that number is the whole charm mechanism:
/// `CS::ChrIns::GetTeamType` (`0x1403f1a60`, 1.16.2) reports `TeamType::Charmed` for any character
/// whose `SpecialEffect` container holds an effect with that state, and reports the character's own
/// `teamType` otherwise. Nothing else gates it -- there is no `enableCharm` check on that path --
/// so putting this row on a `ChrIns` is exactly what the thrown item does to whatever it hits.
///
/// The base-game `Bewitching Branch` (503350) is the same 180-second `stateInfo` 132 effect and
/// works identically here.
pub(crate) const DEFAULT_EFFECT_ID: i32 = 20503350;

const DEFAULT_HOTKEY: &str = "ctrl+alt+c";

const DEFAULT_CONFIG_TOML: &str = r#"# er-charm-enemies standalone DLL configuration.
#
# Press the hotkey in-game to toggle "charm every loaded enemy" on and off. While it is on the
# DLL re-applies the effect to any loaded enemy that is not currently under it, so newly spawned
# enemies are charmed as they appear and the 180-second duration never lapses.
#
# Modifiers: ctrl, alt, shift (either side of the keyboard counts). One trigger key.
hotkey = "ctrl+alt+c"
# SpEffectParam row to apply. 20503350 is Charming Branch; 503350 is Bewitching Branch, the
# base-game item with the same charm state and duration.
effect_id = 20503350
# Strip the effect from every charmed enemy when the hotkey toggles the feature back off. With
# this false the charm instead lapses on its own, up to 180 seconds later.
remove_on_disable = true
"#;

#[derive(Clone, Debug)]
pub(crate) struct CharmConfig {
    pub(crate) config_path: PathBuf,
    pub(crate) hotkey: Hotkey,
    pub(crate) hotkey_text: String,
    pub(crate) effect_id: i32,
    pub(crate) remove_on_disable: bool,
}

static CONFIG: OnceLock<CharmConfig> = OnceLock::new();

fn config_path() -> PathBuf {
    PathBuf::from(CONFIG_FILE_NAME)
}

/// Strip an inline `#` comment and surrounding whitespace/quotes from a value.
fn scalar(raw: &str) -> &str {
    let value = match raw.split_once('#') {
        Some((before, _)) => before,
        None => raw,
    }
    .trim();
    value.trim_matches('"')
}

fn setting<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.starts_with('#'))
        .find_map(|line| {
            let (name, value) = line.split_once('=')?;
            (name.trim() == key).then(|| scalar(value))
        })
}

fn parse_config(text: &str, path: PathBuf) -> CharmConfig {
    let hotkey_text = setting(text, "hotkey")
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_HOTKEY)
        .to_owned();
    let hotkey = match parse_hotkey(&hotkey_text) {
        Ok(hotkey) => hotkey,
        Err(error) => {
            charm_log(format_args!(
                "config: {error}; falling back to {DEFAULT_HOTKEY}"
            ));
            parse_hotkey(DEFAULT_HOTKEY).expect("the built-in default hotkey parses")
        }
    };
    let effect_id = setting(text, "effect_id")
        .and_then(|value| value.parse::<i32>().ok())
        .unwrap_or(DEFAULT_EFFECT_ID);
    let remove_on_disable = setting(text, "remove_on_disable").is_none_or(|value| value != "false");
    CharmConfig {
        config_path: path,
        hotkey,
        hotkey_text,
        effect_id,
        remove_on_disable,
    }
}

/// Read the config, writing the commented default file when there is not one yet.
pub(crate) fn init_config() -> &'static CharmConfig {
    CONFIG.get_or_init(|| {
        let path = config_path();
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(_) => {
                let _ = fs::write(&path, DEFAULT_CONFIG_TOML);
                DEFAULT_CONFIG_TOML.to_owned()
            }
        };
        parse_config(&text, path)
    })
}

pub(crate) fn config() -> &'static CharmConfig {
    init_config()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::keys::{MODIFIER_ALT, MODIFIER_CTRL};

    fn parse(text: &str) -> CharmConfig {
        parse_config(text, PathBuf::from("test.toml"))
    }

    #[test]
    fn the_shipped_default_file_parses_to_the_documented_defaults() {
        let parsed = parse(DEFAULT_CONFIG_TOML);
        assert_eq!(parsed.hotkey_text, DEFAULT_HOTKEY);
        assert_eq!(parsed.hotkey.modifiers, MODIFIER_CTRL | MODIFIER_ALT);
        assert_eq!(parsed.effect_id, DEFAULT_EFFECT_ID);
        assert!(parsed.remove_on_disable);
    }

    #[test]
    fn an_empty_file_still_yields_working_defaults() {
        let parsed = parse("");
        assert_eq!(parsed.effect_id, DEFAULT_EFFECT_ID);
        assert_eq!(parsed.hotkey_text, DEFAULT_HOTKEY);
        assert!(parsed.remove_on_disable);
    }

    #[test]
    fn settings_override_the_defaults() {
        let parsed =
            parse("hotkey = \"shift+f9\"\neffect_id = 503350\nremove_on_disable = false\n");
        assert_eq!(parsed.hotkey_text, "shift+f9");
        assert_eq!(parsed.effect_id, 503350);
        assert!(!parsed.remove_on_disable);
    }

    #[test]
    fn an_unparseable_hotkey_falls_back_instead_of_disabling_the_feature() {
        let parsed = parse("hotkey = \"ctrl+alt+nonsense\"\n");
        assert_eq!(parsed.hotkey.modifiers, MODIFIER_CTRL | MODIFIER_ALT);
        assert_eq!(parsed.hotkey.key, 0x2e);
    }

    #[test]
    fn a_commented_out_setting_is_not_a_setting() {
        let parsed = parse("# effect_id = 1\neffect_id = 503350 # trailing comment\n");
        assert_eq!(parsed.effect_id, 503350);
    }
}
