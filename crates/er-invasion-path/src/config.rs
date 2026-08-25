//! `er-invasion-path.toml`, read once at attach.
//!
//! Hand-parsed rather than pulled through a TOML crate, matching every other standalone shell in
//! this workspace: a handful of scalars does not justify a dependency in a DLL that is
//! cross-compiled into the game process.

// Windows-only in practice; kept portable so the parser below is covered by `cargo test` on the
// host, where the windows-gated modules that consume it are compiled out.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{fs, path::PathBuf, sync::OnceLock};

use er_invasion_warp_core::keybind::{VirtualKey, key_name, parse_key};

use crate::{geometry, log::path_log};

const CONFIG_FILE_NAME: &str = "er-invasion-path.toml";

/// Default toggle key. `F7` exists on every keyboard including 60% layouts, and Elden Ring binds
/// nothing to it.
pub(crate) const DEFAULT_TOGGLE_KEY_NAME: &str = "F7";

/// Below this separation, in metres, a target with clear line of sight gets no path at all.
///
/// The rule the feature was asked for: if you can see them and they are close, a line on the
/// ground is clutter over the fight you are already in.
pub(crate) const DEFAULT_NEAR_SUPPRESS_METERS: f32 = 10.0;

/// How many remote players get a path. Six covers a full Seamless session's worth of phantoms;
/// past that the screen is more line than game.
pub(crate) const DEFAULT_MAX_TARGETS: usize = 6;

/// Length of the "no walkable route" arrow, in metres of world space.
pub(crate) const DEFAULT_ARROW_METERS: f32 = 3.0;

/// Item whose USE toggles the overlay, when `trigger_item_id` is set.
///
/// Zero means "no item trigger, hotkey only". There is no default item: silently binding a real
/// consumable would make that item behave differently for anyone who installed the DLL without
/// reading this file.
pub(crate) const DEFAULT_TRIGGER_ITEM_ID: i32 = 0;

const DEFAULT_CONFIG_TOML: &str = r#"# er-invasion-path standalone DLL configuration.
#
# Draws a walkable route from you to every other player in the session, on the ground, following
# the terrain. The route is the engine's own Havok-AI navmesh path -- the same graph the game's
# characters walk -- so it goes round cliffs and through doorways rather than through them.
#
# One path per player, each its own colour. The closer a player is, the bolder their path.
# A player the navmesh cannot reach from where you stand gets a glowing arrow out of your body
# pointing at them instead of a path.

# Key that toggles the overlay on and off. A NAME, not a number: "F7", "]", "KP_Plus", "Insert".
# A raw virtual-key code such as 0x76 is accepted too.
toggle_key = "F7"

# Item whose use ALSO toggles the overlay, by EquipParamGoods row id. 0 disables the item trigger
# and leaves the key as the only way in. Example: 2110 is Furlcalling Finger Remedy.
trigger_item_id = 0

# Players closer than this many metres WITH a clear line of sight to you get no path drawn: you
# can already see them. Blocked line of sight still draws, however close they are.
near_suppress_meters = 10.0

# A path at or inside this distance is drawn at full width and opacity.
bold_at_meters = 20.0
# A path at this distance is drawn at its faintest. Further than this stays at that faintest
# weight rather than vanishing, so "far away" and "no route" never look the same.
faint_at_meters = 150.0

# Most players to draw at once.
max_targets = 6

# Length in metres of the arrow drawn when no walkable route exists.
arrow_meters = 3.0

# Start with the overlay already on, instead of waiting for the first toggle.
start_enabled = false
"#;

#[derive(Clone, Debug)]
pub(crate) struct PathConfig {
    pub(crate) config_path: PathBuf,
    pub(crate) toggle_key: VirtualKey,
    pub(crate) toggle_key_text: String,
    pub(crate) trigger_item_id: i32,
    pub(crate) near_suppress_meters: f32,
    pub(crate) bold_at_meters: f32,
    pub(crate) faint_at_meters: f32,
    pub(crate) max_targets: usize,
    pub(crate) arrow_meters: f32,
    pub(crate) start_enabled: bool,
}

static CONFIG: OnceLock<PathConfig> = OnceLock::new();

fn config_path() -> PathBuf {
    PathBuf::from(CONFIG_FILE_NAME)
}

/// Strip an inline `#` comment and surrounding whitespace/quotes from a value.
fn scalar(raw: &str) -> &str {
    match raw.split_once('#') {
        Some((before, _)) => before,
        None => raw,
    }
    .trim()
    .trim_matches('"')
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

/// A positive, finite float setting, or the default.
///
/// Rejecting non-finite and negative values here rather than at the draw is what keeps a
/// fat-fingered config from producing a NaN distance that silently suppresses every path.
fn positive_float(text: &str, key: &str, default: f32) -> f32 {
    setting(text, key)
        .and_then(|value| value.parse::<f32>().ok())
        .filter(|value| value.is_finite() && *value >= 0.0)
        .unwrap_or(default)
}

fn parse_config(text: &str, path: PathBuf) -> PathConfig {
    let toggle_key_text = setting(text, "toggle_key")
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_TOGGLE_KEY_NAME)
        .to_owned();
    let toggle_key = match parse_key(&toggle_key_text) {
        Ok(key) => key,
        Err(error) => {
            // Naming the fallback matters: a player who mistyped a key would otherwise press
            // something that does nothing, with no way to tell that from a broken feature.
            let fallback = parse_key(DEFAULT_TOGGLE_KEY_NAME).expect("the built-in default parses");
            path_log(format_args!(
                "config: toggle_key {error}; falling back to {} ({DEFAULT_TOGGLE_KEY_NAME})",
                key_name(fallback)
            ));
            fallback
        }
    };
    let bold_at_meters = positive_float(text, "bold_at_meters", geometry::DEFAULT_BOLD_AT_METERS);
    let mut faint_at_meters =
        positive_float(text, "faint_at_meters", geometry::DEFAULT_FAINT_AT_METERS);
    if faint_at_meters <= bold_at_meters {
        // `boldness` treats an inverted pair as "always faintest", which is a legal but useless
        // overlay. Repairing it here, loudly, beats shipping a config that draws every path at
        // minimum weight and looks like a rendering bug.
        path_log(format_args!(
            "config: faint_at_meters ({faint_at_meters}) must exceed bold_at_meters \
             ({bold_at_meters}); using {}",
            geometry::DEFAULT_FAINT_AT_METERS
        ));
        faint_at_meters = bold_at_meters + geometry::DEFAULT_FAINT_AT_METERS;
    }
    PathConfig {
        config_path: path,
        toggle_key,
        toggle_key_text,
        trigger_item_id: setting(text, "trigger_item_id")
            .and_then(|value| value.parse::<i32>().ok())
            .unwrap_or(DEFAULT_TRIGGER_ITEM_ID),
        near_suppress_meters: positive_float(
            text,
            "near_suppress_meters",
            DEFAULT_NEAR_SUPPRESS_METERS,
        ),
        bold_at_meters,
        faint_at_meters,
        max_targets: setting(text, "max_targets")
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|count| *count > 0)
            .unwrap_or(DEFAULT_MAX_TARGETS),
        arrow_meters: positive_float(text, "arrow_meters", DEFAULT_ARROW_METERS),
        start_enabled: setting(text, "start_enabled").is_some_and(|value| value == "true"),
    }
}

/// Read the config, writing the commented default file when there is not one yet.
pub(crate) fn init_config() -> &'static PathConfig {
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

pub(crate) fn config() -> &'static PathConfig {
    init_config()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> PathConfig {
        parse_config(text, PathBuf::from("test.toml"))
    }

    #[test]
    fn the_shipped_default_file_parses_to_the_documented_defaults() {
        let parsed = parse(DEFAULT_CONFIG_TOML);
        assert_eq!(parsed.toggle_key_text, DEFAULT_TOGGLE_KEY_NAME);
        assert_eq!(parsed.trigger_item_id, DEFAULT_TRIGGER_ITEM_ID);
        assert_eq!(parsed.near_suppress_meters, DEFAULT_NEAR_SUPPRESS_METERS);
        assert_eq!(parsed.bold_at_meters, geometry::DEFAULT_BOLD_AT_METERS);
        assert_eq!(parsed.faint_at_meters, geometry::DEFAULT_FAINT_AT_METERS);
        assert_eq!(parsed.max_targets, DEFAULT_MAX_TARGETS);
        assert_eq!(parsed.arrow_meters, DEFAULT_ARROW_METERS);
        assert!(!parsed.start_enabled);
    }

    #[test]
    fn the_shipped_defaults_match_the_prose_in_the_shipped_file() {
        // The comment block promises 10 metres and one path per player; a default that drifted
        // from its own documentation is a bug report waiting to happen.
        assert!(DEFAULT_CONFIG_TOML.contains("near_suppress_meters = 10.0"));
        assert!(DEFAULT_CONFIG_TOML.contains("toggle_key = \"F7\""));
    }

    #[test]
    fn an_empty_file_still_yields_working_defaults() {
        let parsed = parse("");
        assert_eq!(parsed.toggle_key_text, DEFAULT_TOGGLE_KEY_NAME);
        assert_eq!(parsed.max_targets, DEFAULT_MAX_TARGETS);
        assert!(parsed.faint_at_meters > parsed.bold_at_meters);
    }

    #[test]
    fn settings_override_the_defaults() {
        let parsed = parse(
            "toggle_key = \"]\"\ntrigger_item_id = 2110\nnear_suppress_meters = 4.5\n\
             bold_at_meters = 8\nfaint_at_meters = 60\nmax_targets = 2\narrow_meters = 1.5\n\
             start_enabled = true\n",
        );
        assert_eq!(parsed.toggle_key, parse_key("]").expect("] is a key"));
        assert_eq!(parsed.trigger_item_id, 2110);
        assert_eq!(parsed.near_suppress_meters, 4.5);
        assert_eq!(parsed.bold_at_meters, 8.0);
        assert_eq!(parsed.faint_at_meters, 60.0);
        assert_eq!(parsed.max_targets, 2);
        assert_eq!(parsed.arrow_meters, 1.5);
        assert!(parsed.start_enabled);
    }

    #[test]
    fn an_unparseable_key_falls_back_instead_of_disabling_the_feature() {
        let parsed = parse("toggle_key = \"nonsense\"\n");
        assert_eq!(
            parsed.toggle_key,
            parse_key(DEFAULT_TOGGLE_KEY_NAME).expect("F7 is a key")
        );
    }

    #[test]
    fn a_commented_out_setting_is_not_a_setting() {
        let parsed = parse("# max_targets = 1\nmax_targets = 3 # trailing comment\n");
        assert_eq!(parsed.max_targets, 3);
    }

    #[test]
    fn nonsense_distances_do_not_reach_the_draw() {
        let parsed = parse("near_suppress_meters = -5\nbold_at_meters = nan\nmax_targets = 0\n");
        assert_eq!(parsed.near_suppress_meters, DEFAULT_NEAR_SUPPRESS_METERS);
        assert!(parsed.bold_at_meters.is_finite());
        assert_eq!(parsed.max_targets, DEFAULT_MAX_TARGETS);
    }

    #[test]
    fn an_inverted_distance_pair_is_repaired_rather_than_drawn() {
        let parsed = parse("bold_at_meters = 100\nfaint_at_meters = 10\n");
        assert!(
            parsed.faint_at_meters > parsed.bold_at_meters,
            "inverted thresholds would make every path draw at minimum weight"
        );
        // And the repaired pair must still produce a real ramp rather than a constant.
        let near = geometry::boldness(0.0, parsed.bold_at_meters, parsed.faint_at_meters);
        let far = geometry::boldness(1_000.0, parsed.bold_at_meters, parsed.faint_at_meters);
        assert!(near > far);
    }
}
