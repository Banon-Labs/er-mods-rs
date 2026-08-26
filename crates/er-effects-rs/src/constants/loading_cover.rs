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
