// ============================================================================================
// DETERMINISTIC MENU INPUT PROBE -- REMOVED (autoload/title-flow slice). The probe injected a
// Down tap then a Confirm at frames WE chose, to answer whether the Load-Game leaf d180 ticks its
// leaf Update on HIGHLIGHT alone or only at Confirm. Its driver (`menu_input_probe`) and its
// `input_probe_enabled()` call site were both unreachable -- the gate returned a literal `false` --
// so the schedule constants below it (DOWN_START/DOWN_TAP_FRAMES/HIGHLIGHT_FRAMES/CONFIRM_START/
// CONFIRM_TAP_FRAMES/LOG_INTERVAL) and the FRAME/D180_PRECONFIRM/DOWN_LEAF_BASELINE counters had
// no reader or writer left and went with it.
//
// INPUT_PROBE_ACTIVE went too. It gated a second MENU_D180_LEAF_TICKED accounting arm in
// experiments/trace/menu_constructor_capture.rs, and with the probe deleted it had no writer --
// which scripts/check-oracle-writers.py rejects, because a permanently-0 counter cannot be told
// apart from a feature that ran and did nothing. MENU_D180_LEAF_TICKED below is kept: the
// latching arm in that same file still bumps it on the live path.
// ============================================================================================
/// Count of genuine d180 leaf-Update ticks (bumped ONLY by cap_menu_item_update_hook when the
/// ticked item classifies to dialog_factory). Distinct from MENU_LOAD_GAME_ITEM, which the static
/// sequence-iter walk can also set without d180 actually ticking.
pub(crate) static MENU_D180_LEAF_TICKED: AtomicUsize = AtomicUsize::new(MENU_TRACE_UNSEEN_SEQ);

// ============================================================================================
// SELF-DRIVEN GAMEPAD NAV INJECTION (instrument-capture). Distinct from the disproven
// inputmgr+0x90 keystate write (PROVEN non-functional): this injects at the XInput poll source
// (XInputGetState, the stage the game actually reads gamepad from), so a synthesized D-pad Down
// reaches the real input pipeline. The block stays ON (user input suppressed) while the hook
// fabricates the pad state on a schedule, cycling the title-menu cursor so the input/focus-gated
// row populate fires and the row-push/csmenu-ctor hooks capture WHO triggers it -- with the
// user's input blocked so nothing pollutes. Capture-only: D-pad Down nav, NEVER Confirm/A (no
// load, no save write).
