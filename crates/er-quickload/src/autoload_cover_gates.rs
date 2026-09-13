//! What the boot autoload is allowed to hide, asked one cover at a time.
//!
//! A boot autoload walks the player past the logo splash, the online-login modal and the title's
//! own music on its way to a world the player asked for. Three levers exist to hide that walk, and
//! each of them is a cover for the autoload -- not a feature of its own:
//!
//! * the splash skip flips `STEP_BeginLogo`'s branch so the logo never plays;
//! * the online disable patches `GameMan::IsOnlineMode` to always-offline so the boot never raises
//!   `Unable to start in online mode`;
//! * the Wwise `PostEvent` mute drops every pre-world sound so the title's music does not start
//!   under a load the player never sees.
//!
//! # The defect these predicates replace
//!
//! Every one of the three answered a question that had no term for the autoload. `splash_skip_enabled`
//! was `!save_override_telemetry_only() || ...` and `online_disable_enabled` was
//! `!save_override_telemetry_only() || ...`, so both read "on unless this is a telemetry-only run" --
//! which is on, in every real run, whether or not anything is going to load. The mute had no gate at
//! all: its condition was `!in_world_seen || quickload_active || !player_present`, i.e. "before the
//! world, be silent", with nothing asking whether a world was even coming.
//!
//! Measured on the live run logged at `2026-09-13 10:13:36`, a DLL built
//! `--no-default-features --features quit-rows,menu-trace`, which cannot autoload because the
//! driver is compiled out:
//!
//! ```text
//! [+91ms]  online-disable: patched IsOnlineMode getter 0x14067ae80 -> xor eax,eax;ret (forces offline)
//! [+91ms]  splash-skip: patched 0x140b0da6d 0x74->0x7f
//! [+264ms] sound-post-event: hooked AK::SoundEngine::PostEvent core 0x14223a130
//! [+12224ms] sound-post-event: hit=1 muted=true ... quickload_phase=0 player_present=false
//! ```
//!
//! The player got a silent title with no splash, forced offline, and then no load -- every symptom
//! of an autoload and none of the autoload. `online_disable_enabled`'s own doc comment said it was
//! "gated (not always-on) so it never forces offline on a co-op/online launch that wants the getter
//! live", which its expression had not been true of for as long as the expression existed.
//!
//! Each predicate below takes one term per genuine consumer, the way
//! [`crate::menu_window_run_gate`] does for the `MenuWindowJob::Run` detour, and each is a pure
//! function outside this crate's `#[cfg(windows)]` half so a host test can reach it.
//!
//! In a default build nothing moves: `product_autoload_enabled()` is true, so all three answer the
//! same as the expressions they replace.

/// Whether this build flips the `STEP_BeginLogo` branch so the logo splash never plays.
///
/// The splash is skipped because something is about to carry the player through the title without
/// them watching it; with nothing loading, the logo is the game's own opening and stays.
pub const fn splash_skip_required(boot_autoload: bool, own_load: bool) -> bool {
    boot_autoload || own_load
}

/// Whether this build patches `GameMan::IsOnlineMode` to always-offline.
///
/// Forcing offline is how an autoload reaches the title with no login attempt and no
/// `Unable to start in online mode` modal in the way. It is also the one lever here that changes
/// what the player can do rather than what they see, so a build that drives no load must leave the
/// getter alone -- a Seamless Co-op or online launch wants it live.
pub const fn online_disable_required(boot_autoload: bool, own_stepper: bool) -> bool {
    boot_autoload || own_stepper
}

/// Whether a `AK::SoundEngine::PostEvent` submission is dropped instead of forwarded.
///
/// Two reasons to be silent, and they are not the same reason:
///
/// * `switch_active` -- a `System>Quit` switch is in flight, so the title is being passed through
///   on the way back to a world. True in every composition that has the cloned rows, which is why
///   it is its own term and not folded into the autoload;
/// * `boot_autoload` -- this build boots straight into a world, so the pre-world title belongs to a
///   walk the player never sees. Without it the pre-world terms mean "the title is the
///   destination", and a destination is audible.
pub const fn pre_world_audio_mute_required(
    boot_autoload: bool,
    switch_active: bool,
    in_world: bool,
    player_present: bool,
) -> bool {
    switch_active || (boot_autoload && (!in_world || !player_present))
}

#[cfg(test)]
mod autoload_cover_gates_tests {
    use super::{online_disable_required, pre_world_audio_mute_required, splash_skip_required};

    #[test]
    fn the_default_build_keeps_all_three_covers() {
        assert!(splash_skip_required(true, false));
        assert!(online_disable_required(true, false));
        assert!(pre_world_audio_mute_required(true, false, false, false));
    }

    #[test]
    fn a_build_that_drives_no_load_shows_the_logo_and_leaves_the_getter_live() {
        assert!(!splash_skip_required(false, false));
        assert!(!online_disable_required(false, false));
    }

    #[test]
    fn a_title_with_nothing_loading_behind_it_is_audible() {
        // The defect this module exists for: run 2026-09-13 10:13:36, built
        // `--no-default-features --features quit-rows,menu-trace`, logged
        // `sound-post-event: hit=1 muted=true ... quickload_phase=0` on a title that was the
        // destination, not a waypoint.
        assert!(!pre_world_audio_mute_required(false, false, false, false));
    }

    #[test]
    fn a_switch_through_the_title_is_silent_in_every_composition() {
        assert!(pre_world_audio_mute_required(false, true, false, false));
        assert!(pre_world_audio_mute_required(false, true, true, true));
    }

    #[test]
    fn a_world_with_its_player_in_it_is_audible() {
        assert!(!pre_world_audio_mute_required(true, false, true, true));
    }

    #[test]
    fn the_own_load_and_own_stepper_drives_keep_their_own_covers() {
        assert!(splash_skip_required(false, true));
        assert!(online_disable_required(false, true));
    }
}
