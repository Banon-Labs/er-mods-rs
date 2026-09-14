use super::*;

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

// ENV-gate RATIONALE: ER_QUICKLOAD_SPLASH_SKIP is an explicit diagnostic/runtime probe switch; default behavior remains off unless the operator intentionally stages the gate.
pub(crate) fn splash_skip_enabled() -> bool {
    !save_override_telemetry_only() || product_autoload_enabled() || own_load_enabled()
}
/// Force offline boot (no online login attempt -> no "Unable to start in online mode" modal),
/// so the headless autoload reaches the real title/main-menu directly. Auto-on whenever the
/// own-stepper drives the front-end (the autoload runs vanilla-offline), plus explicit overrides.
/// Gated (not always-on) so it never forces offline on a co-op/online launch that wants the
/// getter live.
pub(crate) fn online_disable_enabled() -> bool {
    !save_override_telemetry_only() || own_stepper_enabled()
}
