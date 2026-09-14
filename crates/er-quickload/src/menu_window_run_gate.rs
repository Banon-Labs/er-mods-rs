//! Who needs the product's `CS::MenuWindowJob::Run` detour, asked one consumer at a time.
//!
//! `pab_node_update_detour` is the product's only handler on `PAB_NODE_UPDATE_RVA` (0x7ad1c0),
//! which is the same address as `MENU_WINDOW_JOB_RUN_RVA` -- the game's menu pump executing one
//! window. Three separate pieces of the product ride that one detour:
//!
//! * the boot autoload's readiness press-any-button advance, `pab_advance_try`;
//! * the cloned System>Quit rows' post-run work, `system_quit_menu_window_run_post`, which is the
//!   sole writer of the ProfileSelect window latch;
//! * the Save Game row's destination browser, `save_flow_menu_pump`. The press only stages a
//!   request, because opening the browser records and submits a `MenuJob` and that is legal only
//!   inside a menu-pump pass; something has to run in the pump and discharge it.
//! * the Load Build from URL row's link field, `build_url_editor_menu_pump`, for the same reason
//!   and through the same post-run frame. Its press latches the field `Pending` and a submit is a
//!   `MenuJob` too, so the same missing detour left it queued forever. Measured on the same pair of
//!   runs as the browser below: the module build logged
//!   `system-quit-build-url: link field requested on dialog=0x31b84080` with
//!   `system_quit_load_build_url_editor_open_count = 1` and every accepted / cancelled / refused /
//!   imported counter still zero.
//!
//! Its install gate named only the first of the three. `task_tick` asked `pab_advance_enabled()`,
//! which derives from `autoload_disabled()`, so a build carrying the rows and not the boot
//! autoload installed no detour at all and the Save Game press staged a request nobody consumed.
//! Measured on run `br-20260913-154820-c63f`, built `--no-default-features --features
//! quit-rows,menu-trace`: `save-flow: row press #1 dialog=0x2a004080 -> opening the destination
//! list`, then 3.1 seconds later `save-flow: destination browser never opened after 180 ticks --
//! ending the flow, the user's save did not happen`. That run logged zero `pab-run-post` lines and
//! zero `pab-advance-hook` lines; the default build's run `br-20260913-154443-65a2` logged 52 and
//! 1, and its browser opened on the first pump pass that saw the request.
//!
//! Splitting the question by consumer is safe because the detour body already self-gates per
//! consumer: `pab_advance_try` returns on its first line unless `pab_advance_enabled()`, so
//! installing the detour for the rows does not advance anyone past press-any-button.
//!
//! In a default build nothing moves: `pab_advance_enabled()` is already true, so the detour was
//! installed before this predicate existed and is installed by it now. The one configuration that
//! does move is a default build run with the `er-quickload-diag-no-autoload.txt` measurement
//! marker, or with the telemetry-only save override -- either turns the first term off, and the
//! rows' post-run work and the Save Game browser now keep their pump rather than going down with
//! the autoload they never belonged to.

/// Whether this build installs the product's own `MenuWindowJob::Run` detour.
///
/// One term per consumer, so a consumer that is present gets the pump whether or not the other one
/// is:
///
/// * `boot_autoload` -- the press-any-button advance needs the pass to reach `pab_advance_try`;
/// * `quit_rows` -- in a build with the cloned rows, both the rows' post-run work and the Save
///   Game row's destination browser run from this detour.
///
/// A `save-game-row` build without the cloned rows is deliberately not a third term. There the
/// pump is `er_quit_menu_core::menu_pump`'s own detour, armed beside the row in
/// `layout_global_hooks`, and a second owner on the same address would lose the `MH_CreateHook`
/// race rather than chain with it -- which is the race that removed
/// `install_system_quit_menu_window_job_run_hook` on 2026-07-15.
pub const fn menu_window_run_detour_required(boot_autoload: bool, quit_rows: bool) -> bool {
    boot_autoload || quit_rows
}

#[cfg(test)]
mod tests {
    use super::menu_window_run_detour_required;

    #[test]
    fn the_boot_autoload_keeps_its_detour_in_a_build_with_no_cloned_rows() {
        assert!(menu_window_run_detour_required(true, false));
    }

    #[test]
    fn the_cloned_rows_get_a_pump_without_asking_the_boot_autoload() {
        // The defect this module exists for: run `br-20260913-154820-c63f`, built
        // `--no-default-features --features quit-rows,menu-trace`, where the Save Game row staged
        // a destination-browser request and no pump ever discharged it.
        assert!(menu_window_run_detour_required(false, true));
    }

    #[test]
    fn the_default_build_is_unchanged() {
        assert!(menu_window_run_detour_required(true, true));
    }

    #[test]
    fn a_build_with_neither_consumer_installs_nothing() {
        assert!(!menu_window_run_detour_required(false, false));
    }
}
