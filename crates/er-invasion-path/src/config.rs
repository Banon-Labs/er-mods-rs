//! `er-invasion-path.toml`, re-read while the game runs.
//!
//! Hand-parsed rather than pulled through a TOML crate, matching every other standalone shell in
//! this workspace: a handful of scalars does not justify a dependency in a DLL that is
//! cross-compiled into the game process.
//!
//! The file is live. Editing it -- the toggle key most of all -- takes effect within about a
//! second, without restarting the game, which is the difference between finding a free key in one
//! sitting and finding one across four launches.

// Windows-only in practice; kept portable so the parser below is covered by `cargo test` on the
// host, where the windows-gated modules that consume it are compiled out.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{
    fs,
    path::PathBuf,
    sync::{Arc, RwLock},
};

use er_invasion_warp_core::keybind::{VirtualKey, key_name, parse_key};

use crate::{geometry, log::path_log};

const CONFIG_FILE_NAME: &str = "er-invasion-path.toml";

/// Default toggle key.
///
/// It was `F7`, chosen because Elden Ring itself binds nothing to it. That reasoning was
/// incomplete and the omission was caught live on 2026-08-25: `er-invasion-warp` polls `VK_F7`
/// every frame in the same process, so in a profile holding both, pressing the key WARPED the
/// player instead of drawing anything -- and nothing anywhere said why. The question is never
/// "does the GAME use this key", it is "does any DLL sharing this process use it", and the mods
/// loaded beside you are not something a default can know.
///
/// `;` is free of every binding this workspace's shells poll (`F1`, `F7`-`F9`, `Insert`,
/// `Delete`, `]`, numpad-multiply, the arrows, `ctrl+alt+c`), exists on every layout including
/// 60%, and is not a key anyone hits by accident. That makes it a better default, not a safe
/// one -- a third-party mod can still claim it, which is why the durable answer is a detector
/// that reports collisions at runtime rather than a cleverer guess here.
pub(crate) const DEFAULT_TOGGLE_KEY_NAME: &str = "semicolon";

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

# Key that toggles the overlay on and off. A NAME, not a number: ";", "]", "KP_Plus", "Insert".
# A raw virtual-key code such as 0xba is accepted too.
#
# Pick one no OTHER mod in your profile already reads. The default was F7 until a live run found
# er-invasion-warp polling F7 every frame -- the key warped the player instead of drawing a path,
# and nothing warned about it. The game's own bindings are not the only thing to avoid.
toggle_key = "semicolon"

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

/// The parsed config, plus the exact text it was parsed from.
///
/// The text is kept so a reload can decide whether anything actually changed by COMPARING
/// CONTENT rather than by trusting a timestamp. `mtime` has one-second granularity on several
/// filesystems, so an edit saved within the same second as the previous read is invisible to it
/// -- and "I changed the key and nothing happened" is exactly the bug this is meant to prevent.
/// The file is a kilobyte; reading it once a second is cheaper than being wrong about it.
struct Loaded {
    config: Arc<PathConfig>,
    text: String,
}

static CONFIG: RwLock<Option<Loaded>> = RwLock::new(None);

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

/// Read the file, writing the commented default when there is not one yet.
///
/// A file that exists but cannot be read (locked, mid-save by the editor) returns `None` rather
/// than the default text: overwriting a config the player is editing, or silently reverting them
/// to defaults for one unlucky second, are both worse than skipping this reload.
fn read_source(path: &PathBuf) -> Option<String> {
    match fs::read_to_string(path) {
        Ok(text) => Some(text),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let _ = fs::write(path, DEFAULT_CONFIG_TOML);
            Some(DEFAULT_CONFIG_TOML.to_owned())
        }
        Err(_) => None,
    }
}

/// Read the config, writing the commented default file when there is not one yet.
pub(crate) fn init_config() -> Arc<PathConfig> {
    config()
}

/// The config as it stands right now.
///
/// Returns an `Arc` rather than a reference because the config is no longer immortal: a frame
/// takes one snapshot and uses it consistently, while a reload on another tick swaps in a new one
/// without disturbing anything already holding the old.
pub(crate) fn config() -> Arc<PathConfig> {
    if let Some(loaded) = CONFIG
        .read()
        .unwrap_or_else(|error| error.into_inner())
        .as_ref()
    {
        return Arc::clone(&loaded.config);
    }
    let path = config_path();
    let text = read_source(&path).unwrap_or_else(|| DEFAULT_CONFIG_TOML.to_owned());
    let config = Arc::new(parse_config(&text, path));
    let mut slot = CONFIG.write().unwrap_or_else(|error| error.into_inner());
    // Another thread may have won the race; keep whatever is already there so two callers in the
    // same instant cannot end up holding configs that disagree.
    let loaded = slot.get_or_insert(Loaded {
        config: Arc::clone(&config),
        text,
    });
    Arc::clone(&loaded.config)
}

/// What a re-read of the file means, given what was already loaded.
///
/// Split out from [`reload_if_changed`] so it can be tested without a filesystem or a global: the
/// lock handling around it is mechanical, and this is the part that can be wrong.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ReloadOutcome {
    /// Byte-identical to what is loaded. The overwhelmingly common case.
    Unchanged,
    /// Something in the file changed. `previous_key_text` is `Some` only when the BINDING is what
    /// changed, because that is the one change with state attached to it -- the edge detector has
    /// to forget whichever key was held.
    Changed { previous_key_text: Option<String> },
}

fn classify(
    previous_text: Option<&str>,
    previous_key_text: Option<&str>,
    text: &str,
    new_key_text: &str,
) -> ReloadOutcome {
    if previous_text == Some(text) {
        return ReloadOutcome::Unchanged;
    }
    ReloadOutcome::Changed {
        previous_key_text: previous_key_text
            .filter(|previous| *previous != new_key_text)
            .map(str::to_owned),
    }
}

/// What a reload did, so the caller can log it and react to a changed binding.
pub(crate) struct Reloaded {
    pub(crate) config: Arc<PathConfig>,
    /// The key that WAS bound, when the binding is what changed.
    pub(crate) previous_key_text: Option<String>,
}

/// Re-read the config file and swap it in if its contents changed.
///
/// This is what makes the settings editable while the game is running: change the key in the toml,
/// save, and the next call picks it up. Returns `None` when nothing changed, which is the case on
/// almost every call.
///
/// A file that is unreadable this instant, or whose text is byte-identical to what is already
/// loaded, is not a change. A file whose `toggle_key` is malformed is a change -- to a config that
/// keeps working -- because [`parse_config`] falls back to the built-in default and says so rather
/// than leaving the feature unbindable.
pub(crate) fn reload_if_changed() -> Option<Reloaded> {
    let path = config_path();
    let text = read_source(&path)?;
    // Cheap path first: hold the read lock only long enough to compare, and parse nothing at all
    // when the file has not been touched -- which is every call but the one that matters.
    {
        let slot = CONFIG.read().unwrap_or_else(|error| error.into_inner());
        if slot.as_ref().is_some_and(|loaded| loaded.text == text) {
            return None;
        }
    }
    let config = Arc::new(parse_config(&text, path));
    let mut slot = CONFIG.write().unwrap_or_else(|error| error.into_inner());
    let outcome = classify(
        slot.as_ref().map(|loaded| loaded.text.as_str()),
        slot.as_ref()
            .map(|loaded| loaded.config.toggle_key_text.as_str()),
        &text,
        &config.toggle_key_text,
    );
    let ReloadOutcome::Changed { previous_key_text } = outcome else {
        // Another thread reloaded the identical text between the two locks.
        return None;
    };
    *slot = Some(Loaded {
        config: Arc::clone(&config),
        text,
    });
    Some(Reloaded {
        config,
        previous_key_text,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> PathConfig {
        parse_config(text, PathBuf::from("test.toml"))
    }

    /// The common case, and the one that must not churn: the file is re-read every second, and a
    /// reload that re-parses and re-logs on every one of those reads would fill the log with
    /// nothing and reset the edge detector sixty times a minute.
    #[test]
    fn an_untouched_file_is_not_a_reload() {
        assert_eq!(
            classify(
                Some(DEFAULT_CONFIG_TOML),
                Some("semicolon"),
                DEFAULT_CONFIG_TOML,
                "semicolon"
            ),
            ReloadOutcome::Unchanged
        );
    }

    #[test]
    fn a_changed_binding_reports_what_it_was_bound_to_before() {
        let edited = DEFAULT_CONFIG_TOML.replace("semicolon", "F9");
        assert_eq!(
            classify(Some(DEFAULT_CONFIG_TOML), Some("semicolon"), &edited, "F9"),
            ReloadOutcome::Changed {
                previous_key_text: Some("semicolon".to_owned())
            }
        );
    }

    /// Editing some OTHER setting is still a reload -- the new value has to take effect -- but it
    /// is not a binding change, so the edge detector must be left alone.
    #[test]
    fn changing_a_setting_other_than_the_key_is_not_a_binding_change() {
        let edited = DEFAULT_CONFIG_TOML.replace("max_targets = 6", "max_targets = 3");
        assert_ne!(
            edited, DEFAULT_CONFIG_TOML,
            "the fixture must actually differ"
        );
        assert_eq!(
            classify(
                Some(DEFAULT_CONFIG_TOML),
                Some("semicolon"),
                &edited,
                "semicolon"
            ),
            ReloadOutcome::Changed {
                previous_key_text: None
            }
        );
    }

    /// The first read has nothing to compare against and must not be mistaken for "unchanged",
    /// which would leave the config empty.
    #[test]
    fn the_very_first_read_counts_as_a_change() {
        assert_eq!(
            classify(None, None, DEFAULT_CONFIG_TOML, "semicolon"),
            ReloadOutcome::Changed {
                previous_key_text: None
            }
        );
    }

    /// A mistyped key must leave the feature BOUND to something the player can press, not
    /// unbound. Reloading is when this matters most: the player is editing live, and a typo that
    /// silently killed the toggle would look exactly like the DLL crashing.
    #[test]
    fn a_malformed_key_reloads_to_the_default_rather_than_to_nothing() {
        let edited = DEFAULT_CONFIG_TOML.replace(
            "toggle_key = \"semicolon\"",
            "toggle_key = \"not-a-key-at-all\"",
        );
        assert_ne!(
            edited, DEFAULT_CONFIG_TOML,
            "the fixture must actually differ"
        );
        let parsed = parse(&edited);
        assert_eq!(
            parsed.toggle_key,
            parse_key(DEFAULT_TOGGLE_KEY_NAME).expect("the built-in default parses")
        );
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
        assert!(
            DEFAULT_CONFIG_TOML.contains(&format!("toggle_key = \"{DEFAULT_TOGGLE_KEY_NAME}\""))
        );
    }

    #[test]
    fn the_default_key_is_none_of_the_ones_this_workspace_already_polls() {
        // F7 shipped as the default and collided with er-invasion-warp, which polls it every
        // frame -- the key warped the player rather than toggling this overlay. These are the
        // virtual keys the sibling shells read; the default must not be among them.
        const CLAIMED_BY_SIBLING_SHELLS: &[(&str, VirtualKey)] = &[
            ("er-invasion-warp F1", 0x70),
            ("er-invasion-warp F7", 0x76),
            ("er-invasion-warp F8", 0x77),
            ("er-invasion-warp F9", 0x78),
            ("er-invasion-warp Insert", 0x2d),
            ("er-invasion-warp Delete", 0x2e),
            ("er-invasion-warp ]", 0xdd),
            ("er-net-effects numpad *", 0x6a),
        ];
        let default = parse_key(DEFAULT_TOGGLE_KEY_NAME).expect("the default parses");
        for (owner, key) in CLAIMED_BY_SIBLING_SHELLS {
            assert_ne!(
                default, *key,
                "the default toggle key is already read by {owner}"
            );
        }
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
