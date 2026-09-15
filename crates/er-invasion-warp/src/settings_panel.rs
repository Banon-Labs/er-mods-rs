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

use er_invasion_warp_core::local_invasion::{LocalInvasionConfig, LocalInvasionMode};

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
            SettingEdit::CycleMode => {
                config.mode = next_mode(config.mode);
                applied += 1;
                changed.push(format!("mode={}", config.mode.as_str()));
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
        "hunt" => config.hunt,
        "reject_notice" => config.reject_notice,
        "map_pins" => config.map_pins,
        "steam_hooks" => config.steam_hooks,
        "dll_users_only" => config.dll_users_only,
        "ersc_observers" => config.ersc_observers,
        "ersc_show_observer" => config.ersc_show_observer,
        "ersc_lobby_key_observer" => config.ersc_lobby_key_observer,
        "ersc_invade_observer" => config.ersc_invade_observer,
        _ => false,
    }
}

/// Flip the boolean a row names. Returns whether the key was one this config has.
fn toggle_bool(config: &mut LocalInvasionConfig, key: &str) -> bool {
    match key {
        "enabled" => config.enabled = !config.enabled,
        "hunt" => config.hunt = !config.hunt,
        "reject_notice" => config.reject_notice = !config.reject_notice,
        "map_pins" => config.map_pins = !config.map_pins,
        "steam_hooks" => config.steam_hooks = !config.steam_hooks,
        "dll_users_only" => config.dll_users_only = !config.dll_users_only,
        "ersc_observers" => config.ersc_observers = !config.ersc_observers,
        "ersc_show_observer" => config.ersc_show_observer = !config.ersc_show_observer,
        "ersc_lobby_key_observer" => {
            config.ersc_lobby_key_observer = !config.ersc_lobby_key_observer;
        }
        "ersc_invade_observer" => config.ersc_invade_observer = !config.ersc_invade_observer,
        _ => return false,
    }
    true
}

/// The next value in `mode`'s cycle.
fn next_mode(mode: LocalInvasionMode) -> LocalInvasionMode {
    match mode {
        LocalInvasionMode::ExactOnly => LocalInvasionMode::PreferExactThenArea,
        LocalInvasionMode::PreferExactThenArea => LocalInvasionMode::NamedOnly,
        LocalInvasionMode::NamedOnly => LocalInvasionMode::ExactOnly,
    }
}

/// Build the rows the panel shows, from the config in force right now.
///
/// Every key appears. A key the panel cannot change appears read-only with the reason, because a
/// settings screen that silently omits a setting is indistinguishable from one that lost it.
fn build_view() -> SettingsView {
    let Some(config) = current_config() else {
        return SettingsView {
            rows: vec![SettingRow {
                key: "config",
                value: "unreadable".to_owned(),
                control: RowControl::ReadOnly,
                note: Some("the file beside the DLL could not be read; defaults are in force"),
            }],
        };
    };
    let key_name = er_invasion_warp_core::keybind::key_name;
    let mut rows = vec![
        toggle_row("enabled", config.enabled, None),
        SettingRow {
            key: "mode",
            value: config.mode.as_str().to_owned(),
            control: RowControl::Cycle,
            note: Some("exact / area / named"),
        },
        toggle_row("hunt", config.hunt, None),
        toggle_row("reject_notice", config.reject_notice, None),
        toggle_row("dll_users_only", config.dll_users_only, None),
        toggle_row("map_pins", config.map_pins, None),
        toggle_row("steam_hooks", config.steam_hooks, None),
        toggle_row("ersc_observers", config.ersc_observers, None),
        toggle_row("ersc_show_observer", config.ersc_show_observer, None),
        toggle_row(
            "ersc_lobby_key_observer",
            config.ersc_lobby_key_observer,
            None,
        ),
        toggle_row("ersc_invade_observer", config.ersc_invade_observer, None),
    ];
    rows.push(SettingRow {
        key: "named_locations",
        value: format!("{} name(s)", config.named_locations.len()),
        control: RowControl::ReadOnly,
        note: Some("not implemented: a typed place name has no known route to its FMG text id"),
    });
    for (key, len) in [
        ("allowed_blocks", config.allowed_blocks.len()),
        ("blocked_blocks", config.blocked_blocks.len()),
        (
            "named_location_text_ids",
            config.named_location_text_ids.len(),
        ),
    ] {
        rows.push(SettingRow {
            key,
            value: format!("{len} entr{}", if len == 1 { "y" } else { "ies" }),
            control: RowControl::ReadOnly,
            note: Some("edited with the mark keys on the world map"),
        });
    }
    for (key, code) in [
        ("mark_key", config.mark_key),
        ("unmark_key", config.unmark_key),
        ("enable_toggle_key", config.enable_toggle_key),
        ("settings_key", config.settings_key),
        ("warp_nearest_key", config.warp_nearest_key),
        ("warp_next_key", config.warp_next_key),
        ("warp_other_area_key", config.warp_other_area_key),
    ] {
        rows.push(SettingRow {
            key,
            value: key_name(code),
            control: RowControl::ReadOnly,
            note: Some("rebind by editing the file"),
        });
    }
    SettingsView { rows }
}

fn toggle_row(key: &'static str, value: bool, note: Option<&'static str>) -> SettingRow {
    SettingRow {
        key,
        value: value.to_string(),
        control: RowControl::Toggle(value),
        note,
    }
}
