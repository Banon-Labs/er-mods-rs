// === Anti-anti-debug (ported from Dasaav-dsv/ProDebug, corrected for ER 1.16.1) ===========
// FromSoft's Arxan inserts timed anti-debug checks that detect a debugger/VEH and swallow debug
// exceptions (which is why our INT3 #BP never reached our VEH). ProDebug patches these checks out
// by pattern. The GitHub ProDebug.dll crashes 1.16.1 because it scans GetModuleHandle(NULL) (the
// wrong module base under the LazyLoader/wine -> wild +0x140000000 deref). We port the same
// patterns but scan our correctly-resolved game_module_base()'s .text only. Each entry is
// (find_pattern, patch_pattern) as hex strings with "??" wildcards; in the patch, every non-??
// byte overwrites the matched bytes at that offset (so no numeric literals -> no magic-number
// lint). Patches neutralize the timed-check branches (e.g. force the conditional jumps to fall
// through). Verified offline match counts on 1.16.1: check1s=181, check1l=1, check2=138, check3=10.
pub(crate) static ANTI_ANTIDEBUG_CHECKS: &[(&str, &str)] = &[
    (
        "7A ?? 75 ?? B9 ?? ?? ?? ?? E8 ?? ?? ?? ?? F3 0F 11 05",
        "?? 02 ?? 00",
    ),
    (
        "0F 8A ?? ?? ?? ?? 0F 85 ?? ?? ?? ?? B9 ?? ?? ?? ?? E8 ?? ?? ?? ?? F3 0F 11 05",
        "?? ?? 06 00 00 00 ?? ?? 00 00 00 00",
    ),
    ("73 ?? 0F 2F ?? 76 ?? 48 8D 15", "?? 00"),
    (
        "72 ?? 48 8D 4C 24 ?? E8 ?? ?? ?? ?? 90 48 8B 05 ?? ?? ?? ?? FF D0",
        "EB",
    ),
];
/// Pattern wildcard token.
pub(crate) const PATTERN_WILDCARD: &str = "??";
/// PE header field offsets used to locate the .text section at the live module base.
pub(crate) const PE_DOS_LFANEW_OFFSET: usize = 0x3c;
pub(crate) const PE_FILE_NUM_SECTIONS_OFFSET: usize = 0x6;
pub(crate) const PE_FILE_SIZE_OPT_HEADER_OFFSET: usize = 0x14;
pub(crate) const PE_OPT_HEADER_OFFSET: usize = 0x18;
pub(crate) const PE_SECTION_HEADER_SIZE: usize = 0x28;
#[allow(dead_code)] // Retained RE constant: no live reader today, kept with the table it was decoded into.
pub(crate) const PE_SECTION_NAME_LEN: usize = 8;
pub(crate) const PE_SECTION_VSIZE_OFFSET: usize = 0x8;
pub(crate) const PE_SECTION_VADDR_OFFSET: usize = 0xc;
/// The executable section name we scan/patch.
pub(crate) const PE_TEXT_SECTION_NAME: &[u8] = b".text";
/// Once-guard for the anti-anti-debug patch (0 = not yet applied).
pub(crate) use er_telemetry_core::counters::ANTI_ANTIDEBUG_APPLIED;
pub(crate) const ANTI_ANTIDEBUG_NOT_APPLIED: usize = 0;
pub(crate) const ANTI_ANTIDEBUG_STEP: usize = 1;
pub(crate) const ANTI_ANTIDEBUG_COUNT_INIT: usize = 0;
/// Masks to extract u32/u16 PE header fields from an 8-byte read.
pub(crate) const PE_U32_MASK: usize = 0xffff_ffff;
pub(crate) const PE_U16_MASK: usize = 0xffff;
/// First section index for the .text scan.
pub(crate) const PE_SECTION_SCAN_START: usize = 0;
/// Current-process pseudo-handle (-1) for FlushInstructionCache, + whole-process flush size.
pub(crate) const ER_CURRENT_PROCESS_PSEUDO_HANDLE: isize = -1;
pub(crate) const FLUSH_WHOLE_PROCESS_SIZE: usize = 0;
/// FromSoft assert wrapper 0x141ebcd00 (calls the core 0x141ebce30 which, in the
/// default mode, deliberately crashes via a null write at 0x141ebcef9). Hooking
/// it captures the failing assertion's expr/message/file (its rcx/rdx/r8 are
/// .rdata wide-string pointers) before the crash. 2.7.2.0 rva.
pub(crate) const ASSERT_WRAPPER_RVA: usize = 0x1ebcd00;
pub(crate) const MAX_ASSERT_LOG_LINES: usize = 16;
pub(crate) const BOOTSTRAP_TELEMETRY_UNSEEN: usize = 0;
pub(crate) const BOOTSTRAP_TELEMETRY_SEEN_VALUE: usize = 1;
pub(crate) const BOOTSTRAP_EVENT_DLL_MAIN_ATTACH: &str = "dllmain_attach";
pub(crate) const BOOTSTRAP_EVENT_CONTINUE_TRACE_REQUESTED: &str = "continue_trace_thread_requested";
pub(crate) const BOOTSTRAP_EVENT_GAME_TASK_REQUESTED: &str = "game_task_thread_requested";
pub(crate) const BOOTSTRAP_EVENT_OVERLAY_SKIPPED_AUTOLOAD: &str = "overlay_skipped_autoload_only";
pub(crate) const BOOTSTRAP_EVENT_GAME_TASK_THREAD_STARTED: &str = "game_task_thread_started";
pub(crate) const BOOTSTRAP_EVENT_GAME_TASK_WAITING_INSTANCE: &str = "game_task_waiting_instance";
pub(crate) const BOOTSTRAP_EVENT_GAME_TASK_INSTANCE_READY: &str = "game_task_instance_ready";
pub(crate) const BOOTSTRAP_EVENT_GAME_TASK_RECURRING_REGISTERED: &str =
    "game_task_recurring_registered";
pub(crate) const BOOTSTRAP_EVENT_TELEMETRY_WRITE: &str = "telemetry_write";
/// Boot missing-save picker cancel -> quit. Recorded here, and not only in the telemetry JSON,
/// because this channel is append-only, lock-free and reachable from any thread: it is the one
/// record that survives a game task frozen while holding the state mutex, which is exactly the
/// condition under which the cancel path runs.
pub(crate) const BOOTSTRAP_EVENT_BOOT_PICKER_CANCEL_EXIT: &str = "boot_picker_cancel_exit";
pub(crate) const BOOTSTRAP_EVENT_POLICY_TELEMETRY_SNAPSHOT: &str = "policy_telemetry_snapshot";
pub(crate) const BOOTSTRAP_EVENT_CONTINUE_TRACE_STARTED: &str = "continue_trace_started";
pub(crate) const BOOTSTRAP_EVENT_CONTINUE_TRACE_APPLIED: &str = "continue_trace_applied";
pub(crate) const BOOTSTRAP_EVENT_CONTINUE_TRACE_APPLY_FAILED: &str = "continue_trace_apply_failed";
pub(crate) const BOOTSTRAP_DETAIL_START: &str = "start";
pub(crate) const BOOTSTRAP_DETAIL_DONE: &str = "done";
pub(crate) const BOOTSTRAP_DETAIL_PLAYER_AVAILABLE: &str = "player_available";
pub(crate) const BOOTSTRAP_DETAIL_PLAYER_UNAVAILABLE: &str = "player_unavailable";
pub(crate) const INITIAL_GAME_TASK_TICKS: u64 = 0;
pub(crate) const GAME_TASK_TICK_INCREMENT: u64 = 1;
pub(crate) const TASK_INSTANCE_WAIT_LOG_INTERVAL: u64 = 4096;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_MAX_CONFIRM_PULSES: u32 = 16;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_DEFAULT_INTERVAL_TICKS: u64 = 30;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_INITIAL_LAST_PULSE_TICK: u64 = 0;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_CONFIRM_HOOK_FRAMES: usize = 4;
pub(crate) const SAFE_INPUT_KEY_UP_STATE: i16 = 0;
pub(crate) const VK_RETURN_KEY: usize = 0x0d;
pub(crate) const VK_SPACE_KEY: usize = 0x20;
#[cfg(feature = "autoload")]
pub(crate) const KEYDOWN_LPARAM: isize = 1;
#[cfg(feature = "autoload")]
pub(crate) const KEYUP_LPARAM: isize = 0xc0000001u32 as isize;
pub(crate) const DIK_RETURN: usize = 0x1c;
pub(crate) const DIK_SPACE: usize = 0x39;
pub(crate) const DIRECT_INPUT_CREATE_DEVICE_VTBL_INDEX: usize = 3;
pub(crate) const DIRECT_INPUT_DEVICE_GET_STATE_VTBL_INDEX: usize = 9;
pub(crate) const HRESULT_SUCCESS_FLOOR: i32 = 0;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_DIRECT_INPUT_WAIT_TICKS: u64 = 300;
// The 2.7.2.0 TitleStep ctor (0x140b0cd90) stores this derived vtable to owner+0
// (`lea rax,[0x142b68d40]; mov [rdi],rax` at 0x140b0cdb5). A vtable 0x10 lower
// is the base/parent one, which an earlier value used by mistake, so the owner
// scan never matched the live object.
pub(crate) use er_title_flow::TitleSessionRva;

pub(crate) use er_title_flow::TitleOwnerLayout;

#[repr(C)]
#[allow(dead_code)] // Retained RE layout: decoded struct shape, nothing constructs it today.
pub(crate) struct TitleOwnerLoadJobLayout {
    pub(crate) unknown_000: [u8; 0xd8],
    pub(crate) pending: i32,
}

pub(crate) use er_title_flow::TITLE_OWNER_STATE_OFFSET;
pub(crate) use er_title_flow::TITLE_OWNER_STATE_COMMITTED_OFFSET;
pub(crate) use er_title_flow::TraceSampleLimit;

#[cfg(feature = "quit-rows")]
pub(crate) use er_title_flow::TITLE_OWNER_SCAN_COUNTDOWN_READY;
pub(crate) use er_title_flow::MenuTraceRva;

pub(crate) const TRACE_MENU_CONTINUE_WRAPPER_RVA: u32 = MenuTraceRva::ContinueWrapper as u32;
pub(crate) const TRACE_MENU_NEW_OR_LOAD_WRAPPER_RVA: u32 = MenuTraceRva::NewOrLoadWrapper as u32;
pub(crate) const TRACE_MENU_OTHER_LOAD_WRAPPER_RVA: u32 =
    er_save_loader::MENU_OTHER_LOAD_WRAPPER_RVA;
#[allow(dead_code)] // Retained RE address: decoded from the game binary, no live caller today.
pub(crate) const TRACE_MENU_TASK_UPDATE_TABLE_RVA: u32 = MenuTraceRva::TaskUpdateTable as u32;
pub(crate) const TRACE_TASK_ENQUEUE_RVA: u32 = MenuTraceRva::TaskEnqueue as u32;
pub(crate) const RESULT_EVENT_HANDLER_RVA: u32 = MENU_JOB_EMIT_RESULT_RVA;
pub(crate) const RESULT_ACTION_BUILDER_RVA: u32 = 0x00747d00;
pub(crate) const RESULT_EVENT_WRAPPER_BUILDER_RVA: u32 = 0x00745d60;
pub(crate) const TRACE_UNKNOWN_TABLE_RVA: u32 = 0;
/// How far into `RESULT_ACTION_BUILDER_RVA` the trace treats a captured frame as "from the result
/// action builder". Its 2.7.2.0 `.pdata` extent is `0x747d00..0x748180` (0x480 bytes), so this
/// covers most of the body; it is a diagnostic attribution window, not a product gate.
pub(crate) const RESULT_ACTION_BUILDER_TRACE_SIZE: usize = 0x360;

/// The result-action-builder attribution band as RVAs on the running build.
///
/// Both endpoints are offsets from one 2.7.2.0 function entry; `None` on any other build.
pub(crate) fn result_action_builder_trace_band() -> Option<std::ops::Range<usize>> {
    er_game_base::game_build::resolve_call_site_band(
        RESULT_ACTION_BUILDER_RVA as usize,
        0,
        RESULT_ACTION_BUILDER_TRACE_SIZE as isize,
        "RESULT_ACTION_BUILDER_RVA (result-action trace attribution band)",
    )
}

/// The function containing the disabled-`Continue` idle-insert call site, `FUN_140765590`
/// (2.7.2.0 `.pdata` extent `0x765590..0x7656bc`).
///
/// At `+0x97` it calls the idle ctor `0x1407ae2b0`, so `+0x9c` is the return address a live stack
/// frame carries. When the build is not 2.7.2.0, [`menu_continue_idle_insert_call_site`] returns
/// `None` and the trace falls through to its object-identity matches (`arg0`/`arg1` against the
/// idle ctor's own pointers).
pub(crate) const MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA: usize = 0x765590;
/// Offset of the idle-insert call's return within [`MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA`].
pub(crate) const MENU_CONTINUE_IDLE_INSERT_CALL_OFFSET: usize = 0x9c;
/// Start of the looser "somewhere in this caller" window, as an offset within the same function.
pub(crate) const MENU_CONTINUE_IDLE_INSERT_BAND_START_OFFSET: isize = 0x20;
/// End of that window: the function's own end (`0x7656bc`), rounded to the next entry.
pub(crate) const MENU_CONTINUE_IDLE_INSERT_BAND_END_OFFSET: isize = 0x130;

/// The exact idle-insert call site as an RVA on the running build, or `None` when unmapped.
pub(crate) fn menu_continue_idle_insert_call_site() -> Option<usize> {
    er_game_base::game_build::resolve_call_site_rva(
        MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA,
        MENU_CONTINUE_IDLE_INSERT_CALL_OFFSET,
        "MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA (disabled-Continue idle insert call site)",
    )
}

/// The looser idle-insert caller window as RVAs on the running build, or `None` when unmapped.
pub(crate) fn menu_continue_idle_insert_caller_band() -> Option<std::ops::Range<usize>> {
    er_game_base::game_build::resolve_call_site_band(
        MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA,
        MENU_CONTINUE_IDLE_INSERT_BAND_START_OFFSET,
        MENU_CONTINUE_IDLE_INSERT_BAND_END_OFFSET,
        "MENU_CONTINUE_IDLE_INSERT_CALLER_FN_RVA (idle insert caller window)",
    )
}

#[repr(C)]
pub(crate) struct MenuTaskStateLayout {
    pub(crate) state_code: i32,
    pub(crate) payload_code: i32,
    pub(crate) delay_bits: u32,
    pub(crate) unknown_0c: [u8; 0x24],
    pub(crate) payload_ptr: usize,
}

pub(crate) const MENU_TASK_STATE_PAYLOAD_PTR_OFFSET: usize =
    core::mem::offset_of!(MenuTaskStateLayout, payload_ptr);
pub(crate) const MENU_TASK_STATE_DELAY_OFFSET: usize =
    core::mem::offset_of!(MenuTaskStateLayout, delay_bits);
pub(crate) const TASK_ENQUEUE_TRACE_LIMIT: usize = 256;
pub(crate) const NO_SAFE_INPUT_CONFIRM_FRAMES: usize = 0;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_CONFIRM_FRAME_DECREMENT: usize = 1;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_NO_CONFIRM_PULSES: u32 = 0;
pub(crate) const SAFE_INPUT_FIRST_PULSE_INDEX: u32 = 0;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_NEXT_PULSE_OFFSET: u32 = 1;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_POST_MAP_MIN_CONFIRM_COUNT: u32 = 5;
#[cfg(feature = "autoload")]
pub(crate) const SAFE_INPUT_INITIAL_DELAY_TICKS: u64 = 0;
pub(crate) const WINDOW_PID_UNSET: u32 = 0;
pub(crate) const ENUM_WINDOWS_STOP_NUMERIC: i32 = 0;
pub(crate) const ENUM_WINDOWS_CONTINUE_NUMERIC: i32 = 1;
pub(crate) const DIRECT_INPUT_KEY_DOWN_MASK: u8 = 0x80;
pub(crate) use er_title_flow::MENU_TRACE_UNSEEN_SEQ;
#[cfg(feature = "autoload")]
pub(crate) const POST_MAP_CONTINUATION_STATE_QWORD: usize = 2;
pub(crate) use er_title_flow::TITLE_OWNER_SCAN_START_ADDRESS;
pub(crate) use er_title_flow::TITLE_NATIVE_JOB_NOT_CALLED;



// ── Title-animation speedup lever (pab_dismiss -> menu_open) ─────────────────────────────────
// The title/menu transition is a Scaleform/GFx animation advanced by the FD4 frame-delta f32 the
// STEP_MenuJobWait tick (2.7.2.0 0x140b0efd0) reads from its task_data+0x08 and forwards to
// CS::TitleTopDialog::update. FadeIn->Loop / TextFadeOut completion is frame-count checked
// (current==total), not time-gated, so scaling this delta makes the animation reach its end frame
// in fewer wall-clock frames -- every downstream predicate (Scaleform tick, completion compare,
// (flags&0x8f)>1 settle gate) is satisfied naturally; nothing is bypassed and the load does not
// desync. bd autoload-menu-speed-lever-framedelta-2026-06-22.
#[allow(dead_code)] // Retained RE constant: no live reader today, kept with the table it was decoded into.
pub(crate) const TITLE_ANIM_SPEEDUP_MAX: f32 = 16.0;
/// Default-on for real autoload runs (no opt-in). Any value > 1.0 arms the FadeIn skip; the magnitude
/// no longer scales anything (the dt-scale and frame-burst levers were both runtime-falsified -- bd
/// title-anim-framedelta-lever-FALSIFIED-runtime-2026-06-24 + pab-to-menuopen-real-breakdown-build-not-
/// anim-2026-06-24 -- the FadeIn is wall-clock/present-bound, so we skip it at the completion predicate
/// instead). Kept as an f32 toggle so the existing env/file override (set to 1.0 = off) still works.
pub(crate) const TITLE_ANIM_SPEEDUP_DEFAULT: f32 = 4.0;
/// Part-A title-cover masquerade: `STEP_BeginTitle`'s only native visual side effect is wrapper
/// 0x140820d20 building the `05_000_Title` MenuWindowJob through factory 0x1407ade30. Suppressing
/// this wrapper hides the native press-any-button/title Scaleform while leaving TitleStep state,
/// FixOrderJobSequence, native Continue/save-load state, and STEP_PlayGame untouched. It must never
/// touch the global resident-UI flag (CSMenuMan+0x21 / STEP_Wait). 2.7.2.0 rvas.
pub(crate) const TITLE_NATIVE_MENU_VISUAL_BEGIN_TITLE_RVA: usize = 0x820d20;
pub(crate) const TITLE_NATIVE_MENU_VISUAL_TITLE_INFORMATION_RVA: usize = 0x820c00;
/// The factory is `MENU_WINDOW_JOB_NATIVE_CTOR_B_RVA`, and is spelled as that constant rather than
/// as a second literal. Its only consumer is a log line.
pub(crate) const TITLE_NATIVE_MENU_VISUAL_FACTORY_RVA: usize =
    MENU_WINDOW_JOB_NATIVE_CTOR_B_RVA as usize;
pub(crate) const TITLE_NATIVE_MENU_VISUAL_NAME: &str = "05_000_Title";
pub(crate) const TITLE_PAB_INFORMATION_VISUAL_NAME: &str = "05_020_TitleInformation";
pub(crate) const TITLE_NATIVE_MENU_VISUAL_SUPPRESS_NOT_INSTALLED: usize = 0;
pub(crate) const TITLE_NATIVE_MENU_VISUAL_SUPPRESS_INSTALLED_YES: usize = 1;
pub(crate) static TITLE_NATIVE_MENU_VISUAL_SUPPRESS_ORIG: AtomicUsize =
    AtomicUsize::new(HOOK_ORIGINAL_UNSET);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_SUPPRESS_INSTALLED: AtomicUsize =
    AtomicUsize::new(TITLE_NATIVE_MENU_VISUAL_SUPPRESS_NOT_INSTALLED);
pub(crate) use er_telemetry_core::counters::TITLE_NATIVE_MENU_VISUAL_SUPPRESSED_BUILDS;
pub(crate) static TITLE_NATIVE_MENU_VISUAL_LAST_OUT_SLOT: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_LAST_PREV_OUT: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_LAST_ARG_RDX: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_LAST_ARG_R8: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_LAST_CALLER_RVA: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
/// Native `MenuWindowJob*` and live window preserved by the BeginTitle wrapper. The render-only
/// suppressor uses these to clear the native title draw bit without removing the job from the native
/// title sequence.
pub(crate) static TITLE_NATIVE_MENU_VISUAL_NATIVE_JOB: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_NATIVE_WINDOW: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_PAB_INFORMATION_VISUAL_ORIG: AtomicUsize =
    AtomicUsize::new(HOOK_ORIGINAL_UNSET);
pub(crate) use er_telemetry_core::counters::TITLE_PAB_INFORMATION_VISUAL_INSTALLED;
pub(crate) use er_telemetry_core::counters::TITLE_PAB_INFORMATION_VISUAL_BUILDS;
pub(crate) static TITLE_PAB_INFORMATION_VISUAL_LAST_JOB: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_PAB_INFORMATION_VISUAL_LAST_WINDOW: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_PAB_INFORMATION_VISUAL_LAST_CALLER_RVA: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
/// Render-only Part-A suppression: `MenuWindowJob::Run` writes the native window visible flags at
/// `GLOBAL_CSMenuMan->field106_0x90[id]`: the Run body sets `|=1` before calling FadeIn, and the
/// FadeIn helper at 0x1407460d0 sets `|=3`. User-visible runtime falsified the old `0x2`
/// draw-bit-only assumption: the title logo / PAB / Continue can still show with flags==1. Therefore
/// product suppression clears the full native-visible mask for the preserved `05_000_Title` window.
/// 2.7.2.0 rva.
pub(crate) const TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RVA: usize = 0x7460d0;
/// Offset, within `MenuWindowJob::Run` ([`MENU_WINDOW_JOB_RUN_RVA`]), of the return after its call
/// to the FadeIn helper above: 2.7.2.0 has `call 0x1407460d0` at `0x1407ae85b`, ending at
/// `0x7ae4f0 + 0x370`.
pub(crate) const TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RUN_CALL_OFFSET: usize = 0x370;
/// Current-branch GFx SetVisible return site inside the native title MenuWindowJob FadeIn helper,
/// as an offset within [`TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RVA`].
///
/// It is a return address compared against a live stack frame: 2.7.2.0 has the `E8` to the GFx
/// SetVisible setter `0x140734640` at `0x1407460fd`, ending at `0x7460d0 + 0x32`.
pub(crate) const TITLE_GFX_VISIBLE_TITLE_FADEIN_CALL_OFFSET: usize = 0x32;

/// The GFx-SetVisible call site inside the title FadeIn helper, as an RVA on the running build.
pub(crate) fn title_gfx_visible_title_fadein_caller_rva() -> Option<usize> {
    er_game_base::game_build::resolve_call_site_rva(
        TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RVA,
        TITLE_GFX_VISIBLE_TITLE_FADEIN_CALL_OFFSET,
        "TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RVA (title FadeIn GFx SetVisible call site)",
    )
}

/// The FadeIn-helper call site inside `MenuWindowJob::Run`, as an RVA on the running build.
pub(crate) fn title_native_menu_visual_window_fadein_run_caller_rva() -> Option<usize> {
    er_game_base::game_build::resolve_call_site_rva(
        MENU_WINDOW_JOB_RUN_RVA,
        TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RUN_CALL_OFFSET,
        "MENU_WINDOW_JOB_RUN_RVA (FadeIn-helper call site)",
    )
}
/// Within the title FadeIn GFx SetVisible callsite, this observed visible-call ordinal produces
/// the user-visible flash/glare during the autoload transition. Keep the name behavioral: the
/// underlying Scaleform object identity is still unknown.
pub(crate) const TITLE_05_000_FADEIN_FLASH_VISIBLE_ORDINAL: usize = 2;
pub(crate) const CS_MENU_MAN_GLOBAL_RVA: usize = er_game_base::rva::CS_MENU_MAN_GLOBAL_RVA;
/// OptionSetting tab-select visibility pass `FUN_14093cdb0` (2.7.2.0 rva 0x93cdb0):
/// `fn(CompositeOptionSettingDialog* composite, int tabIndex, u8* r8, u8* r9)`. It sets the current
/// pane (`composite+0xb8 = cache[tabIndex]`, building via the switch dispatch only if the cache slot is
/// null), then iterates the 10 cached pane dialogs at `composite+0x68` and calls `SetVisible(dialog+0x1200,
/// current==dialog)` on each -- showing only the active tab's pane, hiding the rest. This is the game's
/// own per-tab visibility application. Re-invoking it on restore re-shows the active OptionSetting pane
/// that our hide/restore left with DisplayInfo.Visible=0 (the blank Game Options pane).
#[allow(dead_code)] // Retained RE address: decoded from the game binary, no live caller today.
pub(crate) const OPTIONSETTING_TAB_SELECT_VISIBILITY_RVA: usize = 0x93cdb0;
// The OptionSetting composite layout now lives in `er-title-flow`, so the standalone quit-menu
// shells can read the same offsets the product does rather than keeping a second copy.
// The three composite offsets below are walked only by the rows' OptionSetting pane
// hide/restore; the visible-flags mask is read by the title-visual suppression, which ships
// either way. One import for each lifetime rather than a gate inside a brace group.
#[cfg(feature = "quit-rows")]
pub(crate) use er_title_flow::{
    OPTIONSETTING_COMPOSITE_CURRENT_PANE_OFFSET, OPTIONSETTING_COMPOSITE_OFFSET,
    OPTIONSETTING_COMPOSITE_PANE_CACHE_COUNT,
};
pub(crate) use er_title_flow::TITLE_NATIVE_MENU_VISUAL_VISIBLE_FLAGS_MASK;
pub(crate) const TITLE_NATIVE_MENU_VISUAL_RENDER_SUPPRESS_NOT_INSTALLED: usize = 0;
pub(crate) const TITLE_NATIVE_MENU_VISUAL_RENDER_SUPPRESS_INSTALLED_YES: usize = 1;
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_SUPPRESS_ORIG: AtomicUsize =
    AtomicUsize::new(HOOK_ORIGINAL_UNSET);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_SUPPRESS_INSTALLED: AtomicUsize =
    AtomicUsize::new(TITLE_NATIVE_MENU_VISUAL_RENDER_SUPPRESS_NOT_INSTALLED);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_SUPPRESSED_WINDOWS: AtomicUsize =
    AtomicUsize::new(0);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_LAST_WINDOW: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_LAST_FLAGS_BEFORE: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_LAST_FLAGS_AFTER: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_NATIVE_MENU_VISUAL_RENDER_LAST_CALLER_RVA: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
/// Part-B custom cover target: `05_010_ProfileSelect` is an existing Scaleform surface with
/// `MENU_DummyProfileFace_01..10` symbols that the profile renderer maps to
/// `SYSTEX_Menu_Profile00..09` (via CSMenuProfModelRend / active-screen render targets). The wrapper
/// below is the native `05_010_ProfileSelect` MenuWindowJob builder (its neighbour that builds
/// L"05_000_Title" is a different function, not a shifted copy). We use it as the initial custom
/// cover surface instead of trying to remap `05_001_Title_Logo`, which has no dummy-profile symbol.
pub(crate) const TITLE_CUSTOM_COVER_PROFILE_SELECT_WRAPPER_RVA: usize = PROFILE_SELECT_WRAPPER_RVA as usize;
pub(crate) const TITLE_CUSTOM_COVER_PROFILE_SELECT_NAME: &str = "05_010_ProfileSelect";
/// Native full-screen black Scaleform/MenuWindowJob surface. 2.7.2.0 0x140794e50 builds
/// `01_900_Black` with the same MenuWindow/SceneProxy host ABI as the title wrappers. This is the
/// first diagnostic carrier for proving an engine-owned custom surface can stay above press any
/// button / Continue.
pub(crate) const TITLE_CUSTOM_COVER_BLACK_WRAPPER_RVA: usize = 0x794e50;
pub(crate) const TITLE_CUSTOM_COVER_BLACK_NAME: &str = "01_900_Black";
pub(crate) const TITLE_CUSTOM_COVER_DUMMY_PROFILE_SYMBOL: &str = "MENU_DummyProfileFace_01";
pub(crate) use er_title_flow::TITLE_CUSTOM_COVER_SYSTEX_TARGET;
pub(crate) use er_title_flow::TITLE_CUSTOM_COVER_PROFILE_RENDERER_CLASS;
pub(crate) use er_telemetry_core::counters::TITLE_CUSTOM_COVER_PROFILE_SOURCE_SAMPLE_CALLS;
pub(crate) use er_telemetry_core::counters::TITLE_CUSTOM_COVER_PROFILE_SELECT_BUILDS;
pub(crate) static TITLE_CUSTOM_COVER_PROFILE_SELECT_LAST_RET: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_CUSTOM_COVER_PROFILE_SELECT_LAST_JOB: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_CUSTOM_COVER_PROFILE_SELECT_LAST_CALLER_RVA: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) use er_telemetry_core::counters::TITLE_CUSTOM_COVER_BLACK_BUILDS;
pub(crate) static TITLE_CUSTOM_COVER_BLACK_LAST_RET: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_CUSTOM_COVER_BLACK_LAST_JOB: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_CUSTOM_COVER_BLACK_LAST_CALLER_RVA: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
/// MenuWindowJob::Run (2.7.2.0 0x1407ae4f0). Part B uses the native
/// title job's own pump context to run the separately-built ProfileSelect cover job alongside the
/// preserved title job, instead of replacing the authoritative BeginTitle out-slot.
#[allow(dead_code)] // Retained RE address: decoded from the game binary, no live caller today.
pub(crate) const MENU_WINDOW_JOB_RUN_RVA: usize = 0x7ae4f0;
/// `CS::TitleStep+0x128` -- the element count of the `DLFixedVector<MenuWindow*>` at
/// `TitleStep+0xe0` that `STEP_MenuJobWait` pumps through 1.16.2 `FUN_140733f20`.
///
/// Zeroed in the `TitleStep` constructor, grown as the title builds its windows, decremented by
/// `MENU_WINDOW_LIST_REMOVE_RVA` (0x140735070; 1.16.2 `FUN_140733d70`) inside the teardown. Read in
/// a live world it is the whole defect as a number: a
/// non-zero count while a real map is mounted means title windows are still being updated over it,
/// which is what the drain in `own_load::loaders::load_drive` waits on before it lets a switch
/// commit. Derived from `er-title-flow`'s `TitleOwnerLayout` rather than re-declared, so the two
/// cannot disagree about where the count sits.
pub(crate) use er_title_flow::TITLE_OWNER_MENU_WINDOW_COUNT_128_OFFSET;
#[allow(dead_code)] // Retained diagnostic state: no live reader today, kept with its sibling telemetry.
pub(crate) static TITLE_CUSTOM_COVER_RUN_ORIG: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
pub(crate) use er_telemetry_core::counters::TITLE_CUSTOM_COVER_RUN_RECURSION;
/// PAB detour -> system_quit_menu_window_run_post call count. Confirms the deterministic-winner wiring
/// (2026-07-15 install-race fix) is live at runtime: >0 means PAB is driving run_post on MenuWindowJob::Run
/// passes, so the hide + slot-activation-gate latches get written regardless of the MinHook race.
pub(crate) use er_telemetry_core::counters::PAB_RUN_POST_CALLS;
#[allow(dead_code)] // Retained diagnostic state: no live reader today, kept with its sibling telemetry.
pub(crate) static TITLE_CUSTOM_COVER_RUN_LAST_NATIVE_JOB: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
#[allow(dead_code)] // Retained diagnostic state: no live reader today, kept with its sibling telemetry.
pub(crate) static TITLE_CUSTOM_COVER_RUN_LAST_COVER_JOB: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
#[allow(dead_code)] // Retained diagnostic state: no live reader today, kept with its sibling telemetry.
pub(crate) static TITLE_CUSTOM_COVER_RUN_LAST_COVER_WINDOW: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
#[allow(dead_code)] // Retained diagnostic state: no live reader today, kept with its sibling telemetry.
pub(crate) static TITLE_CUSTOM_COVER_RUN_LAST_RET: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const TITLE_CUSTOM_COVER_GX_TEXTURE_RESOURCE_OFFSET: usize = 0x10;

// Mechanism and its version-anchored Scaleform identities live in `er-scaleform-hooks` (R8).
// The product retains only the telemetry readers used by its runtime-oracle writer.
pub(crate) use er_telemetry_core::counters::SCALEFORM_DESC_ADVANCE_INSTALLED;
pub(crate) use er_telemetry_core::counters::SCALEFORM_DESC_PROVIDER_NULL_HITS;
/// Read-only latch of the native CSFakeLoadingScreen singleton visible during the black/progress
/// loading UI. Sampled from telemetry writes; no hooks or native calls.
pub(crate) use er_telemetry_core::counters::FAKE_LOADING_SCREEN_SAMPLE_COUNT;
pub(crate) use er_telemetry_core::counters::FAKE_LOADING_SCREEN_VISIBLE_SAMPLES;
pub(crate) static FAKE_LOADING_SCREEN_LAST_PTR: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static FAKE_LOADING_SCREEN_LAST_VISIBLE: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static FAKE_LOADING_SCREEN_LAST_FIELD_C: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static FAKE_LOADING_SCREEN_LAST_FIELD_10: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) use er_telemetry_core::counters::RENDER_LOADING_LAYER_SAMPLE_COUNT;
pub(crate) use er_telemetry_core::counters::RENDER_LOADING_LAYER_NONNULL_SAMPLES;
pub(crate) static RENDER_LOADING_LAYER_LAST_RENDMAN: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static RENDER_LOADING_LAYER_LAST_CSGRAPHICS: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static RENDER_LOADING_LAYER_LAST_CSSCALEFORM: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) use er_telemetry_core::counters::RENDER_LOADING_LAYER_LAST_SLOTS_MASK;
pub(crate) use er_telemetry_core::counters::RENDER_LOADING_LAYER_VISIBLE_SLOTS_MASK;
/// `CS::CSFakeLoadingScreenImp` -- the full-screen fade/cover plate the game draws during a map load to
/// hide the world teardown/rebuild behind the now-loading UI. RE'd from its ctor (1.16.2 0x140bbeee0,
/// vtable 0x142b803b8; not re-measured on 2.7.2.0) which is called from `CSDrawStep`, so this object lives in the render pipeline, not
/// the menu system. `visible` (+0x8) is the byte the draw step checks to decide whether to draw the cover;
/// the ctor inits it to 0 and the map-load system raises it while a load is in flight. Clearing it exposes
/// whatever the renderer is drawing underneath (the "disable the loading screen, watch the world pop in"
/// experiment). Singleton = `*(base + RuntimeGlobalRva::FakeLoadingScreenSingleton)`.
#[repr(C)]
pub(crate) struct CSFakeLoadingScreenImp {
    pub(crate) vftable: usize,
    pub(crate) visible: u8,
    pub(crate) unknown_009: [u8; 3],
    pub(crate) field_0c: u32,
    pub(crate) field_10: u64,
}

pub(crate) const FAKE_LOADING_SCREEN_VISIBLE_OFFSET: usize =
    core::mem::offset_of!(CSFakeLoadingScreenImp, visible);

const _: () = assert!(core::mem::offset_of!(CSFakeLoadingScreenImp, visible) == 0x8);
/// Now-loading background portrait forge. The pseudorandom loading-screen background is
/// `helper->replaceTexInfo` (a CSScaleformReplaceTexInfo*), produced for symbol `MENU_Load_%05d` by
/// `GetOrCreateReplaceTexInfo`, whose symbol-bind step is `FUN_140d6cc70` (2.7.2.0). We
/// full-replace that bind for `MENU_Load_*`: build an er-tpf TPF named
/// exactly the requested symbol, turn it into a TpfResCap container via the game's in-memory
/// `CreateTpfResCap` factory, wrap it in a TpfFileCap, and hand it back on the rti so the unmodified
/// per-frame CSScaleform pump registers our texture name and GFx composites the portrait as the
/// loading background. `fn(rti: *mut CSScaleformReplaceTexInfo /rcx/, symbol: *mut DLString<u16>
/// /rdx/) -> u8` (1 = bound; producer then lists the rti).
#[allow(dead_code)] // Retained RE address: decoded from the game binary, no live caller today.
pub(crate) const LOADING_BG_REPLACE_BIND_RVA: usize = 0xd6cc70;
// `CREATE_TPF_RESCAP_RVA` moved to `er_loading_portrait_core::tpf_textures` with the loading-cover
// crate extraction, beside `CREATE_TPF_RES_CAP_RVA` (its only alias) and the cover texture keys
// that are its only readers. `constants.rs`'s glob puts it back in this flat namespace.
/// `CS::TpfFileCap::TpfFileCap` ctor (2.7.2.0 0x140225f60, same rva as 1.16.2). `fn(this: *mut /0x98
/// from MainHeap/, loadTask=0) -> this`; only inits the FD4FileCap base and zeroes `+0x90`.
#[allow(dead_code)] // Retained RE address: decoded from the game binary, no live caller today.
pub(crate) const TPF_FILE_CAP_CTOR_RVA: usize = 0x225f60;
// `GAME_HEAP_ALLOC_RVA` moved to `er_game_base::rva` with the software keyboard, its only caller,
// which now lives in `er-quit-menu-core` so a standalone quit-menu shell can open the link field.
/// `DLString<wchar_t>::substr` (2.7.2.0 0x140116c70, same rva as 1.16.2). `fn(dest /rcx/, src /rdx/,
/// start /r8 = 0/, count /r9 = usize::MAX = to-end/) -> dest`; copies the symbol into the rti symbol.
#[allow(dead_code)] // Retained RE address: decoded from the game binary, no live caller today.
pub(crate) const DLSTRING_WCHAR_SUBSTR_RVA: usize = 0x116c70;
// `GLOBAL_TpfRepository` singleton pointer (deref -> rcx for CreateTpfResCap) is defined below as
// the existing `GLOBAL_TPF_REPOSITORY_RVA` (2.7.2.0 0x3d7b048).
/// `GLOBAL_MainHeapAllocator` singleton pointer (data, 2.7.2.0 0x143d8e380; identical RVA to the repo's
/// `runtime_heap_allocator`). Deref -> the allocator object for the 0x98-byte TpfFileCap allocation.
pub(crate) const GLOBAL_MAIN_HEAP_ALLOCATOR_RVA: usize =
    er_game_base::rva::GLOBAL_MAIN_HEAP_ALLOCATOR_RVA;
/// CSScaleformReplaceTexInfo (size 0x50) field offsets.
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const REPLACE_TEX_INFO_REFCOUNT_OFFSET: usize = 0x8; // i32 DLReferenceCountObject refcount
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const REPLACE_TEX_INFO_SYMBOL_OFFSET: usize = 0x10; // DLString<u16>
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const REPLACE_TEX_INFO_ENCODING_OFFSET: usize = 0x38; // u8
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const REPLACE_TEX_INFO_TPF_FILE_CAP_OFFSET: usize = 0x40; // TpfFileCap*
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const REPLACE_TEX_INFO_READY_OFFSET: usize = 0x48; // u8 (leave 0 so the pump processes it)
/// TpfFileCap (size 0x98) field offsets.
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const TPF_FILE_CAP_LOAD_STATE_OFFSET: usize = 0x88; // u8
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const TPF_FILE_CAP_FLAGS_OFFSET: usize = 0x89; // u8
#[allow(dead_code)] // Retained RE offset: decoded struct layout, no live reader today.
pub(crate) const TPF_FILE_CAP_TEX_RESCAP_OFFSET: usize = 0x90; // -> TpfResCap container
#[allow(dead_code)] // Retained RE constant: no live reader today, kept with the table it was decoded into.
pub(crate) const TPF_FILE_CAP_LOADED_STATE: u8 = 4;
#[allow(dead_code)] // Retained RE constant: no live reader today, kept with the table it was decoded into.
pub(crate) const TPF_FILE_CAP_READY_FLAG_BIT: u8 = 0x20;
#[allow(dead_code)] // Retained RE constant: no live reader today, kept with the table it was decoded into.
pub(crate) const TPF_FILE_CAP_ALLOC_SIZE: usize = 0x98;
#[allow(dead_code)] // Retained RE constant: no live reader today, kept with the table it was decoded into.
pub(crate) const TPF_FILE_CAP_ALLOC_ALIGN: usize = 8;



// Relocated from constants/portrait_lookat.rs (portrait crate split): the title-cover
// Scaleform bind-observer block is title-cover domain and stays product-side.
/// Passive observer for native Scaleform image-symbol -> system texture bindings.
/// 2.7.2.0 `FUN_1407464c0`. It receives an owning resource/list field
/// in rcx and a pair of DLString<char> values in rdx. Do not call it from product code; observe native
/// calls to learn valid owner/resource contexts for SYSTEX-backed surfaces.
pub(crate) const TITLE_SCALEFORM_BIND_OBSERVER_RVA: usize = 0x7464c0;
pub(crate) static TITLE_SCALEFORM_BIND_OBSERVER_ORIG: AtomicUsize =
    AtomicUsize::new(HOOK_ORIGINAL_UNSET);
pub(crate) use er_telemetry_core::counters::TITLE_SCALEFORM_BIND_OBSERVER_INSTALLED;
pub(crate) use er_telemetry_core::counters::TITLE_SCALEFORM_BIND_OBSERVER_HITS;
pub(crate) use er_telemetry_core::counters::TITLE_SCALEFORM_BIND_OBSERVER_SYSTEX_HITS;
pub(crate) static TITLE_SCALEFORM_BIND_OBSERVER_LAST_OWNER: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_SCALEFORM_BIND_OBSERVER_LAST_PAIR: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_SCALEFORM_BIND_OBSERVER_LAST_SYMBOL_PTR: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
pub(crate) static TITLE_SCALEFORM_BIND_OBSERVER_LAST_TARGET_PTR: AtomicUsize =
    AtomicUsize::new(TITLE_OWNER_SCAN_START_ADDRESS);
/// Experimental visible-surface bind rewrite for the replayed ProfileSelect cover: the native
/// SYSTEX profile texture normally targets `MENU_DummyProfileFace_01`; rewrite slot0 to the
/// visibly placed `MENU_FL_40135_Profile` surface and expose it as a distinct oracle.
pub(crate) const TITLE_PROFILE_VISIBLE_SURFACE_SYMBOL: &str = "MENU_FL_40135_Profile";
// The four counters that were meant to record that rewrite -- _BIND_REWRITES, _BIND_LAST_OWNER,
// _BIND_LAST_PAIR, _BIND_LAST_SYMBOL_PTR -- were removed 2026-08-31. The rewrite above was never
// implemented, so none of them had a write site and the five oracles they fed reported absence
// forever. The symbol constant is kept: title_resources_stats_text.rs genuinely uses it.
