//! The game-thread half of the settings panel: what it shows, and what a click does.
//!
//! Everything that touches the config lives here, on the game task. The renderer in
//! [`crate::overlay`] owns nothing -- it clones a snapshot this module publishes and pushes back
//! intents this module drains. That split is not stylistic: `HotConfig::save` does an `fs::write`
//! followed by a `read_to_string` and a full reparse, and hudhook's render loop runs inside
//! `Present`.
//!
//! # Why a click writes the file
//!
//! Because the alternative does not survive the next second. The hot-reload watcher re-reads the
//! file about once a second and adopts what it finds, so a value held only in memory is clobbered
//! by the file that still disagrees with it. Writing back is also what `enable_toggle_key` and the
//! mark keys already promise -- the state a player is in survives a restart, and the file always
//! says what they are actually playing.
//!
//! Every mutation takes the same sequence a key press takes: reload first so a hand edit made
//! since the last poll wins, clone, mutate, `save`. The one difference is that all the edits
//! drained in a tick are applied to one clone and saved once, so six clicks in a frame cost one
//! write rather than six.

#![cfg(windows)]

use er_invasion_warp_core::local_invasion::LocalInvasionConfig;

use er_invasion_warp_core::local_invasion_config::HotConfig;

use crate::local_invasion_filter::{CONFIG, config_path, current_config};
use crate::overlay::{RowControl, SettingEdit, SettingRow, SettingsView};

/// `VK_F4` unless the config moves it -- and it may well need moving, which is why it is a config
/// key at all. Same fallback rule as every other key in this DLL: an unreadable config costs the
/// player their lists, never their keyboard.
fn settings_key_in_force() -> i32 {
    current_config().map_or(er_invasion_warp_core::keybind::VK_F4, |config| {
        config.settings_key
    })
}

/// The two rows on this panel that are not config keys.
///
/// Named once and used by both halves -- the rows that are built and the edits that are drained --
/// so the panel cannot end up offering a row whose click nothing recognises. Every other row is
/// keyed by its name in `er-invasion-warp.toml`; these two have no name there on purpose.
const LEVEL_BRACKET_ROW: &str = "invade rune level";
const WEAPON_BRACKET_ROW: &str = "invade weapon level";

/// The first entry in both bracket lists, which means "leave it at mine".
///
/// A row rather than a separate switch: the list already has to show where the player stands, and
/// an entry saying so is the same click as any other bracket.
const OWN_BRACKET_LABEL: &str = "mine -- don't change it";

const KEY_DOWN_MASK: i16 = -0x8000;

#[link(name = "user32")]
unsafe extern "system" {
    fn GetAsyncKeyState(vkey: i32) -> i16;
}

/// Edge-detected open/close key.
///
/// A private latch rather than a shared poller, for the reason `MarkKeys` records: a
/// `GetAsyncKeyState` read consumes the low "pressed since last call" bit, so two pollers sharing
/// one key eat each other's edge. This key is distinct from the mark, toggle and warp keys, so the
/// pollers never contend.
#[derive(Default)]
pub struct SettingsKey {
    was_down: bool,
    /// The key the latch above is about. When the config moves the binding, a latch left set says
    /// the new key was already held, and the next poll would swallow a real press.
    latched_key: i32,
}

impl SettingsKey {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Poll the key and toggle the panel on its falling-to-rising edge.
    pub fn poll(&mut self) {
        let key = settings_key_in_force();
        if key != self.latched_key {
            self.latched_key = key;
            self.was_down = false;
        }
        let down = unsafe { GetAsyncKeyState(key) } & KEY_DOWN_MASK != 0;
        if down && !self.was_down {
            let opening = !crate::overlay::is_open();
            crate::overlay::set_open(opening);
            crate::standalone_log(format_args!(
                "settings-panel: {} by keypress",
                if opening { "opened" } else { "closed" }
            ));
        }
        self.was_down = down;
    }
}

/// Run the panel's game-thread work for this tick: apply what was clicked, then republish what the
/// next frame should show.
///
/// Edits are applied before the view is rebuilt so a click is visible on the very next frame
/// rather than the one after it -- with them the other way round, every toggle would appear to lag
/// one frame behind the player.
pub fn tick(key: &mut SettingsKey) {
    install_click_suppression();
    key.poll();
    apply_pending_edits();
    if crate::overlay::is_open() {
        // Only while the panel is on screen: building the view clones the config, and doing that
        // every frame for a panel nobody opened is work the game thread does not owe anyone.
        crate::overlay::publish(build_view());
    }
}

/// Arm the DirectInput click suppression for this DLL, retrying until `dinput8.dll` is loaded.
///
/// Every DLL linking `er-dinput-suppress-core` gets its own copy of that crate's statics, which is
/// what lets two overlays each blank for their own rect instead of fighting over one global bool.
/// The cost is that each DLL must also register its own handler: setting the pointer flag without
/// installing does nothing at all, because the flag this DLL writes is read only by the handler
/// this DLL registered. Measured 2026-09-15, run `br-20260915-015140-25c4`: the panel drew 1657
/// frames and took a click with `clicks_kept_from_game=0`, so that click reached the game as a
/// weapon swing.
///
/// Called every tick rather than once: the install is idempotent and returns early once armed,
/// and `dinput8.dll` may not be loaded on the first frames.
fn install_click_suppression() {
    static SAID: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    static ARMED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    use std::sync::atomic::Ordering;
    if ARMED.load(Ordering::Relaxed) {
        return;
    }
    match unsafe { er_dinput_suppress_core::install_mouse_suppression() } {
        Ok(addr) => {
            ARMED.store(true, Ordering::Relaxed);
            crate::standalone_log(format_args!(
                "settings-panel: mouse click suppression armed at {addr:#x} -- a click on the \
                 panel no longer reaches the game as a swing"
            ));
        }
        Err(status) => {
            // Once, not every tick: the early frames legitimately fail while `dinput8.dll` loads,
            // and a per-frame line would bury the run log the way two 22k-line refusals already
            // did once.
            if !SAID.swap(true, Ordering::Relaxed) {
                crate::standalone_log(format_args!(
                    "settings-panel: mouse click suppression not armed yet: {status:?} (retrying \
                     every tick; a click on the panel also swings until it arms)"
                ));
            }
        }
    }
}

/// Apply every edit the panel recorded since the last tick, in one read-modify-write.
fn apply_pending_edits() {
    let edits = crate::overlay::drain_edits();
    if edits.is_empty() {
        return;
    }
    // Taken out before the config lock, because these two are not config keys and must never
    // become ones. They live in memory for the life of the process -- see
    // `crate::invade_difficulty` for the two reasons -- so they take none of the
    // reload-clone-mutate-save sequence below and write nothing to the player's file.
    //
    // Index 0 in both lists is the "your own bracket" row, so it maps to `None` rather than to
    // band 0: a player at `RL41-70` who picks the first entry means "stop asking for anything",
    // not "ask for RL1-20" -- which `band_for` would refuse anyway.
    let edits: Vec<SettingEdit> = edits
        .into_iter()
        .filter(|edit| match edit {
            SettingEdit::Choose(LEVEL_BRACKET_ROW, at) => {
                crate::invade_difficulty::pick_level(at.checked_sub(1).map(|band| band as u32));
                false
            }
            SettingEdit::Choose(WEAPON_BRACKET_ROW, at) => {
                crate::invade_difficulty::pick_weapon(at.checked_sub(1).map(|band| band as u32));
                false
            }
            _ => true,
        })
        .collect();
    if edits.is_empty() {
        return;
    }
    let path = config_path();
    let Ok(mut guard) = CONFIG.lock() else { return };
    let hot = guard.get_or_insert_with(HotConfig::default);
    // The file wins over anything stale: a hand edit made since the last poll must survive a
    // click, or the panel would overwrite the player's file with a copy from before it.
    let _ = hot.reload_if_changed(&path);
    let mut config = hot.current().clone();
    let mut applied = 0usize;
    // What each edit did, named, because the write log's job is to let a player decide whether
    // the toggle they just clicked is the value that moved. A count alone cannot answer that:
    // "1 change(s) written" is the same line whichever row was pressed.
    let mut changed: Vec<String> = Vec::new();
    for edit in &edits {
        match edit {
            SettingEdit::Toggle(key) => {
                if toggle_bool(&mut config, key) {
                    applied += 1;
                    changed.push(format!("{key}={}", read_bool(&config, key)));
                    // The panel's `enabled` row is the same switch as the toggle key, so it owes
                    // the player the same promise: off means no further searches start, not just
                    // that the next match is judged differently.
                    if *key == "enabled" && !config.enabled {
                        crate::local_invasion_filter::stand_down_hunt(
                            "you switched the filter off in the settings panel",
                        );
                    }
                } else {
                    crate::standalone_log(format_args!(
                        "settings-panel: no boolean named `{key}` -- the panel and the config \
                         disagree about what this DLL has, which is a build mismatch, not a \
                         player error"
                    ));
                }
            }
            SettingEdit::Cycle("search_radius") => {
                // 0 asks Steam only for the tile the player stands in; each step adds a ring
                // around it, and the cap is the one `search_ring` enforces. Wrapping back to 0
                // rather than stopping at the top is what makes a single button enough.
                config.prefilter_radius =
                    if config.prefilter_radius >= er_invasion_warp_core::search_ring::MAX_RADIUS {
                        0
                    } else {
                        config.prefilter_radius + 1
                    };
                applied += 1;
                changed.push(format!("prefilter_radius={}", config.prefilter_radius));
                // Raising the radius is the player asking for the widening search, and the
                // widening search cannot run without the two switches it rides on: the ring
                // starts from the tile hunt picks, and it runs inside the lobby-query detour
                // steam_hooks installs. Leaving them for the player to find means the row they
                // just clicked reports a number and changes nothing, which is how this setting
                // spent its first evening. Arm them here rather than explain them afterwards.
                if config.prefilter_radius > 0 {
                    if !config.hunt {
                        config.hunt = true;
                        changed.push("search_by_location=true (needed by the radius)".to_owned());
                    }
                    if !config.steam_hooks {
                        config.steam_hooks = true;
                        changed.push("steam_hooks=true (needed by the radius)".to_owned());
                    }
                }
            }
            SettingEdit::Cycle(key) => {
                crate::standalone_log(format_args!(
                    "settings-panel: no cycling row named `{key}` -- the panel and the config \
                     disagree about what this DLL has, which is a build mismatch, not a player \
                     error"
                ));
            }
            // The two bracket rows are the only `Choose` rows and they were drained above, before
            // this lock was taken. Anything reaching here is a row the renderer drew and nothing
            // applies, which is a build mismatch rather than a player error -- the same thing the
            // arm above says about a cycling row.
            SettingEdit::Choose(key, at) => {
                crate::standalone_log(format_args!(
                    "settings-panel: no list row named `{key}` to set to entry {at} -- the panel \
                     and this DLL disagree about what rows exist"
                ));
            }
        }
    }
    if applied == 0 {
        return;
    }
    let named = changed.join(" ");
    match hot.save(&path, &config) {
        Ok(true) => crate::standalone_log(format_args!(
            "settings-panel: wrote {named} to {} ({applied} change(s))",
            path.display()
        )),
        Ok(false) => crate::standalone_log(format_args!(
            "settings-panel: wrote {named} to {} ({applied} change(s)) but the file did not read \
             back as what was written -- something else is writing this file",
            path.display()
        )),
        Err(error) => crate::standalone_log(format_args!(
            "settings-panel: could not write {}: {error}",
            path.display()
        )),
    }
}

/// The value a boolean row now holds, for the write log. Returns `false` for a key this config
/// does not have, which `toggle_bool` has already refused and logged by the time this is reached.
fn read_bool(config: &LocalInvasionConfig, key: &str) -> bool {
    match key {
        "enabled" => config.enabled,
        "search_by_location" => config.hunt,
        "reject_notice" => config.reject_notice,
        "only_players_with_this_mod" => config.dll_users_only,
        _ => false,
    }
}

/// Flip the boolean a row names. Returns whether the key was one this config has.
fn toggle_bool(config: &mut LocalInvasionConfig, key: &str) -> bool {
    match key {
        "enabled" => config.enabled = !config.enabled,
        "search_by_location" => config.hunt = !config.hunt,
        "reject_notice" => config.reject_notice = !config.reject_notice,
        "only_players_with_this_mod" => config.dll_users_only = !config.dll_users_only,
        _ => return false,
    }
    true
}

/// Build the rows the panel shows, from the config in force right now.
///
/// Every key a player can act on appears, and a key the panel cannot change appears read-only
/// with the reason -- a settings screen that silently omits a setting a player set is
/// indistinguishable from one that lost it.
///
/// What it deliberately does not show, since 2026-09-15: the hook switches (`steam_hooks` and
/// the four `ersc_*`). Those install or withhold detours so a crash can be attributed
/// to one hook, they are still read out of the file, and every one of them is a way to break the
/// mod rather than to configure it -- `steam_hooks = false` silently takes `search_by_location`,
/// `search_radius` and the pool filter with it. A player scrolling a settings panel has no way to
/// know that, and the eight rows they had to scroll past to reach the three that matter were the
/// reason the panel read as more complicated than the feature.
fn build_view() -> SettingsView {
    let Some(config) = current_config() else {
        return SettingsView {
            rows: vec![SettingRow {
                key: "config",
                value: "unreadable".to_owned(),
                control: RowControl::ReadOnly,
                note: Some(
                    "the file beside the DLL could not be read; defaults are in force".to_owned(),
                ),
            }],
        };
    };
    let key_name = er_invasion_warp_core::keybind::key_name;
    let mut rows = vec![
        toggle_row("enabled", config.enabled, None),
        toggle_row(
            "search_by_location",
            config.hunt,
            // The cost, on the row that charges it. Hunt filters the lobby query on a key only
            // this DLL publishes, so with nobody else running this build every entry it finds is
            // stale and nothing lands -- which on screen is "Invasion failed -- no connection",
            // over and over, with the config line reporting everything healthy.
            Some(
                "aim at a place instead of taking whoever answers -- needs hosts running this mod"
                    .to_owned(),
            ),
        ),
        SettingRow {
            key: "search_radius",
            value: config.prefilter_radius.to_string(),
            control: RowControl::Cycle,
            // The note is the whole reason this row is worth clicking. A radius does nothing on
            // its own: the widening search runs inside the lobby-query detour `steam_hooks`
            // installs, and its first tile comes from `hunt`. Saying so on the row is the only
            // place a player finds out before concluding the feature is broken.
            note: Some(
                if config.prefilter_radius == 0 {
                    "0 = exactly where you stand; click to widen"
                } else if config.hunt && config.steam_hooks {
                    "rings of map tiles searched outward from where you stand"
                } else {
                    "inert -- needs search_by_location and steam_hooks on"
                }
                .to_owned(),
            ),
        },
        // There is no widening row. How far a search reaches is the row the finger asks for --
        // `Nearby only` or `Both near and far` -- and a panel toggle that could contradict it would
        // be the same defect this panel would then be advertising as a feature.
        //
        toggle_row("reject_notice", config.reject_notice, None),
        toggle_row("only_players_with_this_mod", config.dll_users_only, None),
    ];
    // The two bracket pickers, and the other half of the widening rule rather than an exception to
    // it: they do not decide how far the search reaches, they decide which bracket the far half
    // asks for once the finger's own row has already sent it there. Neither is written to the
    // file, so no config from last week can disagree with what they show.
    rows.extend(bracket_rows());
    // Both of these decide something: `search_by_location` asks Steam for the one marked
    // location, and refuses to run while that location is excluded. A third row sat here until
    // 2026-09-15 -- `named_location_text_ids`, which only `mode` ever read.
    for (key, len, note) in [
        (
            "allowed_blocks",
            config.allowed_blocks.len(),
            "marked with the mark key -- one mark is what search_by_location aims at",
        ),
        (
            "blocked_blocks",
            config.blocked_blocks.len(),
            "excluded with the unmark key -- an exclusion also stops search_by_location",
        ),
    ] {
        rows.push(SettingRow {
            key,
            value: format!("{len} entr{}", if len == 1 { "y" } else { "ies" }),
            control: RowControl::ReadOnly,
            note: Some(note.to_owned()),
        });
    }
    for (key, code) in [
        ("mark_key", config.mark_key),
        ("unmark_key", config.unmark_key),
        ("enable_toggle_key", config.enable_toggle_key),
        ("settings_key", config.settings_key),
    ] {
        rows.push(SettingRow {
            key,
            value: key_name(code),
            control: RowControl::ReadOnly,
            note: Some("rebind by editing the file".to_owned()),
        });
    }
    SettingsView { rows }
}

fn toggle_row(key: &'static str, value: bool, note: Option<String>) -> SettingRow {
    SettingRow {
        key,
        value: value.to_string(),
        control: RowControl::Toggle(value),
        note,
    }
}

/// The two bracket dropdowns.
///
/// Both lists open with [`OWN_BRACKET_LABEL`], so index `0` is "leave it at mine" and band `b`
/// sits at index `b + 1`. Everything below the player's own bracket is greyed rather than dropped:
/// a list that silently lost its first five rows would make the player's own position in it
/// unreadable, and the position is most of what the list is for.
///
/// When the player's own bracket cannot be read -- no character loaded, an implausible value --
/// nothing is greyed and the row says so. Offering every bracket is a worse answer than offering
/// the right ones; greying against a band nobody measured would be worse than both, because it
/// would look authoritative.
fn bracket_rows() -> Vec<SettingRow> {
    use er_invasion_warp_core::invade_difficulty as brackets;

    let own = crate::invade_difficulty::own_bands();
    let choice = crate::invade_difficulty::current();
    let describe = choice.describe();
    let mut rows = Vec::with_capacity(2);
    for (key, labels, picked, own_band, axis) in [
        (
            LEVEL_BRACKET_ROW,
            brackets::level_band_labels(),
            choice.level,
            own.map(|(level, _)| level),
            "rune level",
        ),
        (
            WEAPON_BRACKET_ROW,
            brackets::weapon_band_labels(),
            choice.weapon,
            own.map(|(_, weapon)| weapon),
            "weapon upgrade",
        ),
    ] {
        let mut options = Vec::with_capacity(labels.len() + 1);
        options.push(OWN_BRACKET_LABEL.to_owned());
        options.extend(labels);
        let note = match own_band {
            Some(band) => format!(
                "yours is {} -- {describe}",
                options
                    .get(band as usize + 1)
                    .map_or("unknown", String::as_str)
            ),
            None => format!("your own {axis} bracket could not be read, so nothing is greyed out"),
        };
        rows.push(SettingRow {
            key,
            value: String::new(),
            control: RowControl::Choose {
                selected: picked.map_or(0, |band| band as usize + 1),
                // Band `b` is at index `b + 1`, and the player's own band is selectable -- picking
                // it means the same as picking `mine`, which `band_for` already resolves to no
                // rewrite at all.
                first_enabled: own_band.map_or(0, |band| band as usize + 1),
                options,
            },
            note: Some(note),
        });
    }
    rows
}
