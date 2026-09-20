//! Whether this process may serve the derived `05_010_ProfileSelect` movie, as a pure decision.
//!
//! Its own module outside the `#[cfg(windows)]` half of the crate, for the same reason its sibling
//! in `er-quickload` is: the answer decides what a user sees, it has been wrong before, and a wrong
//! answer builds and logs exactly like a right one.
//!
//! # What the derived movie does to the window
//!
//! `er_gfx::title_05_010::stats_panel` is not an additive edit. It hides the 128x128 face box
//! (`Icon_0`) behind an alpha-0 colour transform, shifts `PlayerName`, the `Level` FMG caption and
//! the `Level` value left into the freed strip, adds the `ErStats` / `ErCharStats` / `Backing` /
//! `DriveCell_*` / `CurrentPath` fields, and recompacts the row stack from five 156px rows in a
//! 780px viewport to ten rows at 52px pitch, rescaling each row's backing and cursor chrome to
//! match. Every one of those changes makes room for content something else has to write.
//!
//! # The regression this exists for
//!
//! [`crate::arm::arm_standalone`] served that movie for any host that opened `05_010_ProfileSelect`,
//! which includes a shell arming **Load Character** and nothing else. Such a shell writes none of
//! the fields the edit adds: [`crate::profile_row_chrome::install_row_populate_hooks`] is the seam
//! that fills them and it has never had a caller, so `character_row_facts`, `editor_runtime_tick`,
//! `current_player_row_populate_post` and `drive_row_native_cursor` are all `None`. What reaches
//! the screen is the edit's layout with the game's bare text in it: no portraits, ten cramped rows,
//! the name and level shifted into a gap nothing fills, and no attribute line.
//!
//! Measured live 2026-09-19 on the installer's own profile, `er-quit-load-character.log`:
//! `05_010 stats-panel runtime edit derived in=14400 out=17813 validated=true` followed by
//! `served 05_010_profileselect (the picker's own cache key) ... memory_replacement=true`, and then
//! not one row-dressing line -- no `stats-text: staged merged PlayerName`, no
//! `save-picker: hid N row field(s)`. The same run's `er-quit-rows-debug.log` from a shell that
//! does carry a decoder has eleven of them, including
//! `staged merged PlayerName slot=0 header='Vagabond, RL 9 WL 25'`.
//!
//! A shell that carries a decoder is unaffected: it hooks the row populate from its own copy and
//! decodes the `.sl2` behind it, so it can fill what it serves. `er-quickload` is that shell; the
//! `er-quit-rows` fork whose log is quoted above was another, until it was deleted on 2026-09-20.
//!
//! # Why a browse row is reason enough on its own
//!
//! The picker's rows are files, not characters, so they need no decoded save -- their content is
//! the drive strip, the current-path bar and the file's last-saved time, and
//! `save_picker_menu::save_picker_row_slot_info` supplies all three from inside this crate. Those
//! fields exist only in the derived movie, so a host arming a browse row both needs the edit and
//! can dress what it asks for.

/// Whether this process serves the derived `05_010_ProfileSelect` movie.
///
/// `browse_rows_armed` is "this host puts the picker's own file rows on that window" -- **Load
/// Character from File** or the **Save Game** destination browser, both of which this crate
/// dresses itself. `host_dresses_character_rows` is a host having installed
/// [`crate::profile_row_chrome::RowPopulateHooks`] with a character-row answer in it.
///
/// Either is sufficient and neither can speak for the other. Neither means the window renders the
/// way the game ships it, which is the correct answer for a shell that cannot fill the edit rather
/// than a degraded one.
#[must_use]
pub fn profile_select_chrome_required(
    browse_rows_armed: bool,
    host_dresses_character_rows: bool,
) -> bool {
    browse_rows_armed || host_dresses_character_rows
}

#[cfg(test)]
mod profile_select_chrome_gate_tests {
    use super::profile_select_chrome_required;

    /// The regression: a shell arming only **Load Character** has no browse rows and no host to
    /// answer about a character, so it must leave the window alone rather than re-lay it out and
    /// write nothing into the space it made.
    #[test]
    fn load_character_alone_does_not_serve_the_edit() {
        assert!(!profile_select_chrome_required(false, false));
    }

    /// A browse row is dressed from inside this crate, so it needs no host.
    #[test]
    fn a_browse_row_is_reason_enough_by_itself() {
        assert!(profile_select_chrome_required(true, false));
    }

    /// A host with a decoded save fills the character fields, so it earns the edit with no picker
    /// row in the set.
    #[test]
    fn a_host_that_dresses_character_rows_is_reason_enough_by_itself() {
        assert!(profile_select_chrome_required(false, true));
    }

    #[test]
    fn both_reasons_together_still_serve_it() {
        assert!(profile_select_chrome_required(true, true));
    }
}
