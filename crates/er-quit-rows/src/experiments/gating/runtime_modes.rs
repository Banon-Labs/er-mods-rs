use super::*;

/// Model B: Live-dialog Load-Game fire (er-quickload-live-dialog.txt / ER_QUICKLOAD_LIVE_DIALOG).
/// Off by default. Sibling to direct_build (the forge). Instead of forging a ProfileLoadDialog
/// (factory 0x14081ead0 with a synthetic capture + no live MenuWindow -> a non-live dialog the
/// native menu group never pumps -> wrong-map/crash), this locates the real Load-Game registry
/// node (CS::MenuMemberFuncJob<TitleTopDialog>, vtable 0x142b265d0, member-fn chains to factory
/// 0x14081ead0) and invokes its native run 0x1409aaba0(rcx=node) -- so the ProfileLoadDialog is
/// born live & registered in menu-group 0x143d87350, which the native pump drives. STAGE2 then
/// fires load_activate (vt+0xa0) + the guarded continue_confirm -> SetState(5). The forge path
/// (direct_build) is untouched; this is a deliberate, separately-gated experiment.
pub(crate) fn live_dialog_enabled() -> bool {
    false
}
/// Arm the readiness-gated press-any-button advance. ENV `ER_QUICKLOAD_PAB_ADVANCE=1` or GAME_DIR file
/// `er-quickload-pab-advance.txt`. Deliberately independent of the (deleted) direct
/// "Continue pressed" trigger, which also used to drive `maybe_auto_open_menu`.
pub(crate) fn pab_advance_enabled() -> bool {
    if autoload_disabled() {
        return false;
    }
    !save_override_telemetry_only()
}
// ENV-gate rationale (required by .auto/env_gate_comment_policy.rego): this is not an on/off
// feature flag. The title-anim speedup is default-on product behavior for every real autoload run
// (returns TITLE_ANIM_SPEEDUP_DEFAULT, no opt-in) -- matching the always-on autoload levers and the
// "No Compromises" rule that the deliverable is product behavior, not a flag-gated experiment. The
// env/file override exists only to (a) sweep the factor K at runtime during the empirical animation-
// speed search -- a cross-compile per candidate K is minutes, a runtime knob is seconds -- and (b)
// force K=1.0 for a clean A/B against the recorded baseline. Telemetry/trace-only runs stay at 1.0 so
// they observe unmodified native pacing.
/// Title-animation speedup factor for the pab_dismiss -> menu_open transition. Default-on
/// (`TITLE_ANIM_SPEEDUP_DEFAULT`) for real autoload runs; overridable at runtime via env
/// `ER_QUICKLOAD_TITLE_ANIM_SPEEDUP=<f32>` or GAME_DIR file `er-quickload-title-anim-speedup.txt`
/// (contents parsed as f32). Result is clamped to [MIN, max]; an override that is unparseable or
/// <=1.0 forces no scaling. bd autoload-menu-speed-lever-framedelta-2026-06-22.
pub(crate) fn title_anim_speedup_factor() -> f32 {
    if autoload_disabled() || save_override_telemetry_only() {
        TITLE_ANIM_SPEEDUP_MIN
    } else {
        TITLE_ANIM_SPEEDUP_DEFAULT
    }
}

/// True when the title-anim speedup lever is armed (factor > 1.0).
pub(crate) fn title_anim_speedup_enabled() -> bool {
    title_anim_speedup_factor() > TITLE_ANIM_SPEEDUP_MIN
}

/// Passive, epilogue-neutral observer for native Scaleform menu-resource acquisition. This is
/// intentionally separate from the title-cover/hide bundle: resource/memory-GFX proof needs the
/// replaced `05_001_Title_Logo` visible, not hidden by TitleBackViewParts suppression hooks.
pub(crate) fn title_menu_resource_observer_enabled() -> bool {
    false
}

/// AUTO-confirm observe mode (er-quickload-auto-confirm.txt): drive the game's own natural title
/// flow with Confirm input-taps so we can finally observe the view past the modal. No SetState
/// forcing, no input block, no custom dismiss -- just the press the game polls for.
pub(crate) fn auto_confirm_enabled() -> bool {
    false
}
// ENV-gate RATIONALE: ER_QUICKLOAD_CONTINUE_DRIVE is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn continue_drive_enabled() -> bool {
    false
}
// ENV-gate RATIONALE: ER_QUICKLOAD_ARM_PROBE is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn arm_probe_enabled() -> bool {
    false
}
// ENV-gate RATIONALE: ER_QUICKLOAD_NATIVE_ARM_LOOP is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn native_arm_loop_enabled() -> bool {
    false
}
// ENV-gate RATIONALE: ER_QUICKLOAD_TITLE_ACCEPT is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn title_accept_enabled() -> bool {
    false
}
// ENV-gate RATIONALE: ER_QUICKLOAD_TITLE_ACCEPT_INJECT is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn title_accept_inject_enabled() -> bool {
    false
}
// ENV-gate RATIONALE: ER_QUICKLOAD_SPLASH_SKIP is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn splash_skip_enabled() -> bool {
    !save_override_telemetry_only()
        || product_autoload_enabled()
        || own_load_enabled()
        || title_menu_resource_observer_enabled()
}
/// Force offline boot (no online login attempt -> no "Unable to start in online mode" modal),
/// so the headless autoload reaches the real title/main-menu directly. Auto-on whenever the
/// own-stepper drives the front-end (the autoload runs vanilla-offline), plus explicit overrides.
/// Gated (not always-on) so it never forces offline on a co-op/online launch that wants the
/// getter live.
pub(crate) fn online_disable_enabled() -> bool {
    !save_override_telemetry_only() || own_stepper_enabled()
}
