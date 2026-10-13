//! Product-side compatibility shim for the save-picker extraction: the boot picker overlay lives
//! in `er-save-picker-core`; this module keeps existing product callsites stable while the remaining
//! picker/quit-menu seams are moved.

pub(crate) use er_save_picker_core::overlay::{
    SAVE_PICKER_KBD_HOOK_HITS, SAVE_PICKER_OVERLAY_ARMED, SAVE_PICKER_OVERLAY_DRAW_HITS,
    SAVE_PICKER_OVERLAY_HELD_POLLS, SAVE_PICKER_OVERLAY_INPUT_HITS, SAVE_PICKER_OVERLAY_OPEN_COUNT,
    SAVE_PICKER_OVERLAY_PICK_COUNT, SAVE_PICKER_OVERLAY_PICK_REJECT_COUNT,
    SAVE_PICKER_OVERLAY_POLL_COUNT, boot_stage_picked_save_for_character_choice,
    ensure_save_picker_keyboard_hook, missing_save_picker_selected_slot, overlay_save_picker_onto,
    save_picker_overlay_active, save_picker_overlay_presentable,
    save_picker_overlay_process_completion,
};

pub(crate) use er_save_picker_core::load_error::{
    LOAD_ERROR_SCREEN_ARM_COUNT, LOAD_ERROR_SCREEN_ARMED, LOAD_ERROR_SCREEN_DRAW_HITS,
    LOAD_ERROR_SCREEN_EXIT_REFUSED, arm_load_error_screen, disarm_load_error_screen,
    load_error_screen_active, overlay_load_error_onto,
};

/// Close Game on the load-error screen.
///
/// The same quit the boot picker's cancel uses (`boot_os_perform_cancel_exit`): release the input
/// block, then `ExitProcess(0)`. The one refusal is the one the Return to Desktop row also makes --
/// never while the save flow is mid-commit, because ending the process there tears the write in
/// half. A refusal returns `false` and the screen tells the player to try again.
pub(crate) fn load_error_close_game(reason: &str) -> bool {
    let stage =
        er_telemetry_core::counters::SAVE_FLOW_STAGE.load(std::sync::atomic::Ordering::SeqCst);
    if stage != er_title_flow::SAVE_FLOW_STAGE_IDLE {
        crate::telemetry::append_autoload_debug(format_args!(
            "load-error: REFUSING Close Game ({reason}) -- save flow stage {stage} is mid-commit, and ending the process now would tear that write in half"
        ));
        return false;
    }
    crate::experiments::release_input_block_now();
    crate::telemetry::append_autoload_debug(format_args!(
        "load-error: closing the game ({reason}) -- released the input block, calling ExitProcess(0)"
    ));
    unsafe { windows::Win32::System::Threading::ExitProcess(0) }
}

pub(crate) fn boot_arm_missing_save_picker_in_game() -> bool {
    er_save_picker_core::overlay::arm_boot_picker()
}

pub(crate) fn save_picker_overlay_input_tick() {
    crate::experiments::boot_open_missing_save_picker_if_pending();
    er_save_picker_core::overlay::save_picker_overlay_input_tick();
}
