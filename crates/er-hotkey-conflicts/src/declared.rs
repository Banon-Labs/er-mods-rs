//! The hotkeys this workspace's own shells ship with, and the setting that moves each one.
//!
//! # Why a table, when the census observes
//!
//! The census finds two modules polling one key at run time, which works for any author's DLL.
//! For this workspace's own shells two more things are known before a run, and both were being
//! re-derived by hand: whether a new shell's default collides with one already shipped (the F7
//! incident in this crate's module docs, and every `CLAIMED_BY_SIBLING_SHELLS` list a shell kept
//! for itself since), and which config line a player edits to separate two that do. This table
//! answers both. The test below fails when two shipped defaults are the same chord, so a new
//! default is checked against every other one by `cargo test`, and the report names the setting to
//! change beside each collision it finds ([`setting_for`]).
//!
//! # What is in it
//!
//! The keys that act anywhere in the world: a menu that opens, a toggle, a mark. Keys a shell reads
//! only while its own menu is open -- the arrows, Enter, Backspace in er-npc-summons' picker,
//! er-npc-possess's picker and er-net-effects' expanded selector -- are left out on purpose: those
//! menus take the keys from the game while open and overlap with one another by design.

use er_hotkey_config::{Chord, parse_chord};

/// One shipped default.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Declared {
    /// The DLL's file name, as the census attributes calls to it.
    pub module: &'static str,
    /// Where a player moves it.
    pub setting: &'static str,
    /// The default, in `er-hotkey-config`'s spelling.
    pub default: &'static str,
}

const fn row(module: &'static str, setting: &'static str, default: &'static str) -> Declared {
    Declared {
        module,
        setting,
        default,
    }
}

/// Every shipped default that acts anywhere in the world.
pub const SHIPPED_DEFAULTS: &[Declared] = &[
    row(
        "er_invasion_warp.dll",
        "mark_key in er-invasion-warp.toml [local_invasion]",
        "Insert",
    ),
    row(
        "er_invasion_warp.dll",
        "unmark_key in er-invasion-warp.toml [local_invasion]",
        "Delete",
    ),
    row(
        "er_invasion_warp.dll",
        "enable_toggle_key in er-invasion-warp.toml [local_invasion]",
        "F3",
    ),
    row(
        "er_invasion_warp.dll",
        "settings_key in er-invasion-warp.toml [local_invasion]",
        "F4",
    ),
    row(
        "er_invasion_path.dll",
        "toggle_key in er-invasion-path.toml",
        "semicolon",
    ),
    row(
        "er_net_effects.dll",
        "selector_apply_key in er-net-effects.toml",
        "alt+quote",
    ),
    row(
        "er_net_effects.dll",
        "selector_expand_key in er-net-effects.toml",
        "alt+9",
    ),
    row(
        "er_net_effects.dll",
        "selector_mark_key in er-net-effects.toml",
        "alt+m",
    ),
    row(
        "er_net_effects.dll",
        "selector_show_hide_key in er-net-effects.toml",
        "alt+0",
    ),
    row(
        "er_net_effects.dll",
        "selector_show_hide_key in er-net-effects.toml",
        "alt+kp_0",
    ),
    row(
        "er_net_effects.dll",
        "selector_show_hide_key in er-net-effects.toml",
        "alt+insert",
    ),
    row("er_npc_possess.dll", "hotkey in er-npc-possess.toml", "F9"),
    row(
        "er_npc_possess.dll",
        "the picker's toggle in er-npc-possess.toml",
        "F10",
    ),
    row(
        "er_enemynpc_effects.dll",
        "hotkey in er-enemynpc-effects.toml",
        "ctrl+alt+c",
    ),
    row("er_cel_shade.dll", "menu_key in er-cel-shade.toml", "F6"),
    row(
        "er_cel_shade.dll",
        "the vanilla panic chord, which is not configurable",
        "ctrl+shift+F6",
    ),
    row(
        "er_render_scale.dll",
        "menu_key in er-render-scale.toml",
        "F5",
    ),
];

/// A row's chord, or `None` for a spelling the parser refuses (which the test forbids).
#[must_use]
pub fn chord(declared: &Declared) -> Option<Chord> {
    parse_chord(declared.default).ok()
}

/// The setting that moves `module`'s default on the key `vk`, when the table has one. A module may
/// have several on one key with different modifiers; the first is named.
#[must_use]
pub fn setting_for(module: &str, vk: u16) -> Option<&'static str> {
    SHIPPED_DEFAULTS
        .iter()
        .filter(|declared| declared.module.eq_ignore_ascii_case(module))
        .find(|declared| chord(declared).is_some_and(|chord| chord.vk == u32::from(vk)))
        .map(|declared| declared.setting)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_default_is_a_key_the_parser_reads() {
        for declared in SHIPPED_DEFAULTS {
            assert!(chord(declared).is_some(), "{declared:?}");
            assert!(declared.module.ends_with(".dll"), "{declared:?}");
        }
    }

    #[test]
    fn no_two_shipped_defaults_are_the_same_chord() {
        for (index, first) in SHIPPED_DEFAULTS.iter().enumerate() {
            for second in &SHIPPED_DEFAULTS[index + 1..] {
                assert_ne!(
                    chord(first),
                    chord(second),
                    "{} ({}) and {} ({}) ship the same default {}",
                    first.module,
                    first.setting,
                    second.module,
                    second.setting,
                    first.default
                );
            }
        }
    }

    #[test]
    fn the_render_scale_key_is_on_a_trigger_nothing_else_uses() {
        // F5 with any modifiers: no other shell's default fires on the same key.
        let Some(f5) = parse_chord("F5").ok() else {
            panic!("F5 parses");
        };
        let sharing: Vec<&Declared> = SHIPPED_DEFAULTS
            .iter()
            .filter(|declared| chord(declared).is_some_and(|chord| chord.vk == f5.vk))
            .collect();
        assert_eq!(sharing.len(), 1, "{sharing:?}");
        assert_eq!(sharing[0].module, "er_render_scale.dll");
    }

    #[test]
    fn a_collision_names_the_setting_to_change() {
        const VK_F5: u16 = 0x74;
        const VK_F6: u16 = 0x75;
        assert_eq!(
            setting_for("er_render_scale.dll", VK_F5),
            Some("menu_key in er-render-scale.toml")
        );
        assert_eq!(
            setting_for("ER_CEL_SHADE.DLL", VK_F6),
            Some("menu_key in er-cel-shade.toml")
        );
        assert_eq!(setting_for("er_render_scale.dll", VK_F6), None);
        assert_eq!(setting_for("someone_elses.dll", VK_F5), None);
    }
}
