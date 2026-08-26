use std::{fs, sync::atomic::Ordering};

use eldenring::cs::PlayerIns;

#[allow(unused_imports)]
use crate::*;
#[allow(unused_imports)]
use crate::{crashlog::*, ffi::*, hooks::*, telemetry::*};

use super::*;

pub(crate) unsafe fn product_continue_action_ready(
    ready: &ProductCoreAutoloadReady,
    base: usize,
    gm: usize,
    slot: i32,
) -> bool {
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    if slot < OWN_STEPPER_SLOT_ZERO
        || gm == null
        || OWN_STEPPER_MENU_OPENED.load(Ordering::SeqCst) == OWN_STEPPER_MENU_OPENED_NO
    {
        return false;
    }
    let dialog_vt = unsafe { safe_read_usize(ready.title_dialog) }.unwrap_or(null);
    dialog_vt == base + TITLE_TOP_DIALOG_VTABLE_RVA
}
/// DIAGNOSTIC ONLY -- record a live `01_900_Black` backscreen-overlay `MenuWindowJob`.
///
/// This used to be `record_continue_candidate` and the counters still carry the old
/// `MENU_CONTINUE_CANDIDATE_*` names for telemetry-schema stability, but the row it observes is
/// NOT a Continue row. Its `+0xa8` functor `_Do_call` thunk is
/// `MENU_TITLE_BACKSCREEN_OVERLAY_DOCALL_RVA` (0x764b80), which reaches `FUN_140764290` ->
/// `FUN_1407acf80` and builds the Scaleform movie `L"01_900_Black"` -- the fade/backscreen overlay.
/// Its three siblings under the same functor vtable 0x142a9b9c8 build `L"01_910_Fade"`,
/// `L"02_903_NowLoading2"` and `L"02_904_NowLoading3"`; the whole family is the loading/backscreen
/// overlay set built from `CSMenuManImp::Update` @0x140766980. Nothing here selects a save slot,
/// and nothing here may be promoted into a load driver.
pub(crate) fn record_backscreen_overlay_candidate(
    item: usize,
    accept_predicate: usize,
    base: usize,
) {
    const MENU_ITEM_ACCEPT_IDLE_RVA: usize = 0x007add70;
    const MENU_ITEM_ACCEPT_NATIVE_RVA: usize = 0x007ad810;
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    if item == null {
        return;
    }
    MENU_CONTINUE_CANDIDATE_HITS.fetch_add(1, Ordering::SeqCst);
    MENU_CONTINUE_CANDIDATE_ITEM.store(item, Ordering::SeqCst);
    let prior = MENU_CONTINUE_CANDIDATE_LAST_ACCEPT.swap(accept_predicate, Ordering::SeqCst);
    if prior != null && prior != accept_predicate {
        MENU_CONTINUE_CANDIDATE_ACCEPT_CHANGES.fetch_add(1, Ordering::SeqCst);
        append_continue_trace(format_args!(
            "MENU-BACKSCREEN-OVERLAY-CANDIDATE accept predicate changed item=0x{item:x} prior=0x{prior:x} now=0x{accept_predicate:x}"
        ));
    }
    if base != null && accept_predicate == base + MENU_ITEM_ACCEPT_NATIVE_RVA {
        MENU_CONTINUE_CANDIDATE_NATIVE_ACCEPT_HITS.fetch_add(1, Ordering::SeqCst);
    } else if base != null && accept_predicate == base + MENU_ITEM_ACCEPT_IDLE_RVA {
        MENU_CONTINUE_CANDIDATE_IDLE_ACCEPT_HITS.fetch_add(1, Ordering::SeqCst);
    } else {
        MENU_CONTINUE_CANDIDATE_OTHER_ACCEPT_HITS.fetch_add(1, Ordering::SeqCst);
    }
}
/// PRODUCT AUTOLOAD DRIVE -- arm-gate, then hand off to the native save-read + native-confirm chain.
///
/// WHAT THIS REPLACED, AND WHY (2026-08-25). Until now this function waited at
/// `FULLREAD_PHASE_SUBMIT` for a "native Continue MenuWindowJob": a `MenuWindowJob` whose `+0xa8`
/// functor `_Do_call` slot equalled 0x764b80 and whose `+0xf8` accept predicate equalled the native
/// 0x7ad810. Every load-bearing part of that identification is false:
///   * 0x764b80 is not a Continue row. Through functor vtable 0x142a9b9c8 it reaches
///     `FUN_140764290`, which builds the Scaleform movie `L"01_900_Black"` -- the fade/backscreen
///     overlay -- via the idle ctor `FUN_1407acf80`. Its siblings build `L"01_910_Fade"`,
///     `L"02_903_NowLoading2"`, `L"02_904_NowLoading3"`. It is the loading-overlay family built
///     from `CSMenuManImp::Update` @0x140766980.
///   * that functor vtable is shared by four builders, and `MENU_WINDOW_JOB_VTABLE_RVA`
///     (0x2aa97e8) is the GENERIC `MenuWindowJob` vtable, so both halves of the test were
///     non-specific anyway.
///
/// The constant's own doc admitted its provenance was "the +0xa8 action on the first focused
/// MenuWindowJob after native TitleTopDialog::open_menu" -- an ordering guess. Three measured runs
/// captured zero real Continue rows, so the arm below (GameMan+0xb78, `set_save_slot`, the save
/// read) never ran, `GameMan+0xb80` never left 0, and the user saw a frozen loading bar.
///
/// WHAT IT DOES NOW. There is no title MenuJob that loads a character; the title's own
/// `MenuMemberFuncJob<TitleTopDialog>` census is closed at two member functions
/// (`TITLE_MEMBER_FN_LOGOUT_RESET_RVA` / `TITLE_MEMBER_FN_MENU_SHOW_RVA`), neither of which reads a
/// save slot. So this arms the gates that must hold before any save write and then hands the whole
/// SUBMIT -> DRAIN -> DESER -> GUARD -> COMMIT machine to `native_fullread_tick`, the same native
/// chain already shipped for the missing-save picker and explicit `save_file` sources, and the one
/// `switch_reload.rs` replicates for the in-world System->Quit switch:
///   SUBMIT  `MarkProfileIndexAsUsed` -> `GameMan+0xb78 = slot` -> `set_save_slot(slot)` ->
///           `0x14067b1a0` (byte-verified `movl $0x2,0xb80(%rax)` -- this IS the b80 0->2 edge)
///   DRAIN   lane + poll until `GameMan+0xb80 == 3` (RESIDENT)
///   DESER   `0x14067b290(slot)` -> `GameMan+0xc30` = the character's REAL map
///   GUARD   c30 real + character fingerprint (the hard gate on the sole save write)
///   COMMIT  `continue_confirm 0x140b0e180` -> byte-verified `mov [TitleStep+0xbc], GameMan+0xc30`
///           then `TitleStep::RequestState(5)`; then disarm `GameMan+0xb78`.
///
/// The DESER-before-COMMIT ordering is not optional: `continue_confirm` copies `GameMan+0xc30` into
/// `TitleStep+0xbc` (`0x140b0e1a7 call 0x140679560` reads `0xc30(%rax)`, `0x140b0e1b7 mov
/// %eax,0xbc(%rcx)`), so confirming before the slot is deserialized would enter the world at the
/// new-game default map. No private `MenuJob` is built or pumped anywhere in that chain.
pub(crate) unsafe fn product_continue_autoload_tick(
    owner: usize,
    base: usize,
    gm: usize,
    slot: i32,
    tick: u64,
    ready: &ProductCoreAutoloadReady,
) {
    const PRODUCT_CONTINUE_WAIT_LOG_TICKS: u64 = 30;
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    let phase = FULLREAD_PHASE.load(Ordering::SeqCst);
    let read_i32 = |off: usize| unsafe { safe_read_i32(gm + off) }.unwrap_or(GAME_MAN_C30_UNSET);

    if phase == FULLREAD_PHASE_DONE {
        return;
    }

    // SWITCH-SAFETY (System->Quit->Load-Profile): for the in-world character switch (not a boot
    // autoload), the return-title chain we submitted is still tearing down the OLD world. Starting
    // the read now sets GameMan saveState/b80=2 and DoSaveStuff deserializes the picked slot INTO
    // the still-live world -> crash in CSGaitemImp::Deserialize (live 0x67141a). Defer until the
    // old world is actually gone (local player absent), so the load runs at a clean title exactly
    // like the boot autoload does. The boot path has no System-Quit phase, and at a fresh title
    // there is no local player, so this gate passes immediately there.
    // See bd system-quit-load-profile-trigger-RESOLVED.
    if SYSTEM_QUIT_QUICKLOAD_PHASE.load(Ordering::SeqCst) != SYSTEM_QUIT_QUICKLOAD_PHASE_IDLE
        && unsafe { PlayerIns::local_player_mut() }.is_ok()
    {
        if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
            append_autoload_debug(format_args!(
                "product-core-autoload: SWITCH deferring native save-read until old world torn down -- local player still present slot={slot} tick={tick}"
            ));
        }
        return;
    }

    if phase == FULLREAD_PHASE_SUBMIT {
        if !unsafe { product_continue_action_ready(ready, base, gm, slot) } {
            if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                append_autoload_debug(format_args!(
                    "product-core-autoload: native save-read gated off dialog=0x{:x} menu_latch={} slot={slot} -- semantic menu readiness not stable",
                    ready.title_dialog, ready.menu_opened_latch
                ));
            }
            return;
        }
        let b80_before = read_i32(GAME_MAN_LOAD_IN_PROGRESS_B80_OFFSET);
        if b80_before != OWN_STEPPER_B80_IDLE {
            if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                append_autoload_debug(format_args!(
                    "product-core-autoload: waiting for native preview/load b80={b80_before} to become idle before arming the save read -- no SetState5"
                ));
            }
            return;
        }
        let (profile_real, profile_map, profile_level, profile_name_len) =
            unsafe { profile_slot_fingerprint(slot) };
        if !profile_real {
            if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                append_autoload_debug(format_args!(
                    "product-core-autoload: slot profile is empty-like (slot={slot} map=0x{profile_map:x} level={profile_level} name_len={profile_name_len}); fail-closed with no native Load Game fallback, no legal-popup auto-accept, no save read, and no input"
                ));
            }
            return;
        }
        OWN_STEPPER_EXPECTED_SLOT.store(slot, Ordering::SeqCst);
        OWN_STEPPER_CONFIRMED.store(TITLE_OWNER_SCAN_START_ADDRESS, Ordering::SeqCst);
        OWN_STEPPER_DESER_FIRED.store(OWN_STEPPER_DESER_NOT_FIRED, Ordering::SeqCst);
        OWN_STEPPER_MOUNT_C30.store(GAME_MAN_C30_UNSET, Ordering::SeqCst);
        OWN_STEPPER_IO_WAS_SET.store(OWN_STEPPER_IO_WAS_SET_NO, Ordering::SeqCst);
        append_autoload_debug(format_args!(
            "product-core-autoload: *** ARMING native save read slot={slot} b80={b80_before} dialog=0x{:x} menu_latch={} profile(level={profile_level} name_len={profile_name_len} map=0x{profile_map:x}) tick={tick} -- no menu row, no input, no direct_load/direct_build/raw deserialize ***",
            ready.title_dialog, ready.menu_opened_latch
        ));
        timeline_event(
            "T_native_save_read_arm",
            tick,
            format_args!("slot={slot} b80={b80_before}"),
        );
    }

    // Native enqueue + native pump ownership: the game's own save-read/deserialize/confirm calls,
    // one phase per frame. No MenuJob is created, retained or pumped here.
    unsafe { native_fullread_tick(owner, base, tick, slot) };
}
pub(crate) unsafe fn fire_product_title_load_action(
    action: MenuActionNode,
    base: usize,
    tick: u64,
    slot: i32,
) {
    if OWN_STEPPER_TITLE_FIRED.swap(OWN_STEPPER_CALL_INC, Ordering::SeqCst)
        != TITLE_OWNER_SCAN_START_ADDRESS
    {
        return;
    }
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    let node = action.node;
    let node_vt = action.node_vt;
    let member_dialog = action.member_dialog;
    let member_fn = action.member_fn;
    let member_adjust = action.member_adjust;
    let window_item = action.window_item;
    // CENSUS GUARD. `title_menu_action_ready` is a readiness signal, not a load action: every
    // `MenuMemberFuncJob<TitleTopDialog>` in the image wraps either 0x9b2f00 or 0x9b35f0 (see
    // TITLE_MEMBER_FN_LOGOUT_RESET_RVA). 0x9b2f00 calls `CSServerInterface::StartLogOutJob` and
    // `TitleFlowContext::Reset`, i.e. it CLOSES the title menu -- running it from the autoload
    // would log out and reset the flow, never load a character. Refuse it loudly instead.
    if member_fn == base + TITLE_MEMBER_FN_LOGOUT_RESET_RVA {
        append_autoload_debug(format_args!(
            "product-core-autoload: REFUSING to run MenuMemberFuncJob node=0x{node:x} member_fn=0x{member_fn:x} -- that is the title logout/TitleFlowContext::Reset step, not a character load (census: member_fn is only 0x{:x} or 0x{:x}; neither loads a save)",
            base + TITLE_MEMBER_FN_LOGOUT_RESET_RVA,
            base + TITLE_MEMBER_FN_MENU_SHOW_RVA
        ));
        return;
    }
    OWN_STEPPER_EXPECTED_SLOT.store(slot, Ordering::SeqCst);
    OWN_STEPPER_DESER_FIRED.store(OWN_STEPPER_DESER_NOT_FIRED, Ordering::SeqCst);
    OWN_STEPPER_MOUNT_C30.store(GAME_MAN_C30_UNSET, Ordering::SeqCst);
    OWN_STEPPER_IO_WAS_SET.store(OWN_STEPPER_IO_WAS_SET_NO, Ordering::SeqCst);
    OWN_STEPPER_DIALOG.store(null, Ordering::SeqCst);
    OWN_STEPPER_SELECTOR_STEP.store(null, Ordering::SeqCst);
    OWN_STEPPER_SELECTOR_CTX.store(null, Ordering::SeqCst);
    reset_phase_timer(&OWN_STEPPER_S2_PHASE_STARTED_MS);
    let run: unsafe extern "system" fn(usize) = unsafe {
        std::mem::transmute::<usize, unsafe extern "system" fn(usize)>(
            base + MENU_MEMBER_FUNC_JOB_RUN_RVA,
        )
    };
    append_autoload_debug(format_args!(
        "product-core-autoload: *** FIRING native TitleTopDialog Load-Game run 0x{:x}(rcx=node=0x{node:x}) vt=0x{node_vt:x} member_dialog=0x{member_dialog:x} member_fn=0x{member_fn:x} member_adjust=0x{member_adjust:x} window_item=0x{window_item:x} slot={slot} tick={tick} -- no direct_build/forged ctx ***",
        base + MENU_MEMBER_FUNC_JOB_RUN_RVA
    ));
    timeline_event(
        "T_native_load_action",
        tick,
        format_args!("node=0x{node:x} member_fn=0x{member_fn:x}"),
    );
    unsafe { run(node) };
    append_autoload_debug(format_args!(
        "product-core-autoload: native TitleTopDialog Load-Game run returned; waiting for ProfileLoadDialog factory hook capture"
    ));
}
/// DETERMINISTIC MENU INPUT PROBE driver. Runs each frame (in PHASE_MENU_BUILD, after the menu is
/// open) when `input_probe_enabled()`. Schedule (probe-frame `f`, see lib.rs consts):
///   [0, DOWN_START)                 SETTLE   -- baseline, no input (rows empty headless?)
///   [DOWN_START, +DOWN_TAP_FRAMES)  DOWN     -- inject one Down (Continue->Load Game)
///   [DOWN_START, CONFIRM_START)     HIGHLIGHT-- NO input; watch MENU_D180_LEAF_TICKED grow?
///   [CONFIRM_START, +CONFIRM_TAP)   CONFIRM  -- inject Confirm; native load fires (captured)
/// The decisive signal is whether the genuine d180 leaf-Update tick count grows during HIGHLIGHT
/// (before Confirm). Pure reads + the two keystate-bit writes; no SetState here (the Confirm drives
/// the native load). `dump_titletop_menu_entries` logs the live router_this row vector each interval.
pub(crate) unsafe fn menu_input_probe(owner: usize, base: usize) {
    const NULL: usize = TITLE_OWNER_SCAN_START_ADDRESS;
    INPUT_PROBE_ACTIVE.store(OWN_STEPPER_CALL_INC, Ordering::SeqCst);
    let inputmgr =
        unsafe { safe_read_usize(base + SELECTBOT_INPUT_MANAGER_GLOBAL_RVA) }.unwrap_or(NULL);
    let f = INPUT_PROBE_FRAME.fetch_add(OWN_STEPPER_CALL_INC, Ordering::SeqCst) as u64;
    let item = MENU_LOAD_GAME_ITEM.load(Ordering::SeqCst);
    let leaf_ticks = MENU_D180_LEAF_TICKED.load(Ordering::SeqCst);

    let in_down =
        (INPUT_PROBE_DOWN_START..INPUT_PROBE_DOWN_START + INPUT_PROBE_DOWN_TAP_FRAMES).contains(&f);
    let in_highlight = (INPUT_PROBE_DOWN_START..INPUT_PROBE_CONFIRM_START).contains(&f);
    let in_confirm = (INPUT_PROBE_CONFIRM_START
        ..INPUT_PROBE_CONFIRM_START + INPUT_PROBE_CONFIRM_TAP_FRAMES)
        .contains(&f);

    if inputmgr != NULL {
        if in_down {
            // Inject BOTH vertical-move events (one is Down, one Up; Up saturates at the top so
            // from Continue only Down moves -> lands on Load Game). Edge-triggered &1.
            unsafe {
                *((inputmgr + INPUTMGR_BITMAP_90_OFFSET + MENU_EVENT_MOVE_A_00) as *mut u8) |=
                    MENU_EVENT_PRESSED_BIT;
                *((inputmgr + INPUTMGR_BITMAP_90_OFFSET + MENU_EVENT_MOVE_B_45) as *mut u8) |=
                    MENU_EVENT_PRESSED_BIT;
            }
        }
        if in_confirm {
            unsafe {
                *((inputmgr + INPUTMGR_BITMAP_90_OFFSET + MENU_EVENT_CONFIRM_3D) as *mut u8) |=
                    MENU_EVENT_PRESSED_BIT;
            }
        }
    }

    // DECISIVE one-shot: d180's leaf Update ticked during the highlight window (after Down, before
    // Confirm). Snapshot taken at DOWN_START; any growth here means highlight ALONE ticks d180.
    if in_highlight
        && leaf_ticks > INPUT_PROBE_DOWN_LEAF_BASELINE.load(Ordering::SeqCst)
        && INPUT_PROBE_D180_PRECONFIRM.swap(OWN_STEPPER_CALL_INC, Ordering::SeqCst) == NULL
    {
        let (l, c, cur) = unsafe { dump_titletop_menu_entries(owner, base) };
        append_autoload_debug(format_args!(
            "INPUT-PROBE: *** d180 LEAF-TICKED during HIGHLIGHT (pre-confirm) f={f} ticks={leaf_ticks} item=0x{item:x} cursor={cur} load_entry=0x{:x} cont_entry=0x{:x} *** -> highlight ALONE ticks d180; zero-input functor-invoke route VIABLE",
            l.unwrap_or(NULL),
            c.unwrap_or(NULL)
        ));
    }

    if f == INPUT_PROBE_DOWN_START {
        // Latch the leaf-tick baseline at the moment Down begins, so HIGHLIGHT growth is measured
        // strictly from here (ignores any pre-Down ticks).
        INPUT_PROBE_DOWN_LEAF_BASELINE.store(leaf_ticks, Ordering::SeqCst);
        append_autoload_debug(format_args!(
            "INPUT-PROBE: DOWN inject f={f} inputmgr=0x{inputmgr:x} leaf_baseline={leaf_ticks} -- highlight window [{}..{}) before Confirm",
            INPUT_PROBE_DOWN_START, INPUT_PROBE_CONFIRM_START
        ));
    }
    if f == INPUT_PROBE_CONFIRM_START {
        let pre = INPUT_PROBE_D180_PRECONFIRM.load(Ordering::SeqCst) != NULL;
        append_autoload_debug(format_args!(
            "INPUT-PROBE: CONFIRM inject f={f} d180_leaf_ticked_on_highlight={pre} ticks_now={leaf_ticks} -- {} (load now fires via Confirm)",
            if pre {
                "highlight WAS sufficient"
            } else {
                "highlight did NOT tick d180 -> needs static walk / focus is required"
            }
        ));
    }
    if f % INPUT_PROBE_LOG_INTERVAL == NULL as u64 {
        let phase = if in_down {
            "DOWN"
        } else if in_confirm {
            "CONFIRM"
        } else if in_highlight {
            "HIGHLIGHT"
        } else if f < INPUT_PROBE_DOWN_START {
            "SETTLE"
        } else {
            "POST"
        };
        append_autoload_debug(format_args!(
            "INPUT-PROBE: f={f} phase={phase} d180_item=0x{item:x} leaf_ticks={leaf_ticks}"
        ));
        let _ = unsafe { dump_titletop_menu_entries(owner, base) };
    }
}
/// OBSERVE-ONLY NATIVE-LOAD tick (native_load_enabled(), gated OFF by default). Runs each frame
/// INSTEAD of the own_stepper forcing logic, then the caller pass-throughs to OWN_STEPPER_ORIG_IDX10
/// so the NATIVE title machine advances untouched (the user drives past press-any-button + modals).
/// KEEP vs the normal own_stepper: it does NOT SetState(owner,2/3), does NOT clear the beginlogo
/// gate, does NOT self-fire the registrar 0x1409b24e0, does NOT run direct_build / cold_char_mount.
/// It ONLY: (1) read-only checks whether the live TitleTopDialog menu/action is rendered and
/// semantically validated (TitleTopDialog vtable, [dialog+0xa48] registry, Load-Game
/// MenuMemberFuncJob node/action chain); (2) ONE-SHOT: fires that native run
/// MENU_MEMBER_FUNC_JOB_RUN_RVA (0x1409aaba0, rcx=node) -- which builds the LIVE registered
/// ProfileLoadDialog the native pump drives. After firing it observes (the caller keeps writing the
/// golden oracle as the native pump hopefully loads the char). Pure read-only until the single fire.
#[allow(dead_code)] // Retained: Staged-save slot seeder for the deprecated staged-save probe path; the RE it encodes (ProfileSummary slot layout, FaceData::CopyFromBuffer, ChrAsm copy) is the reason it stays.
unsafe fn seed_profile_summary_slot_from_staged_save(
    base: usize,
    profile_summary: usize,
    slot: i32,
) -> bool {
    const NULL: usize = TITLE_OWNER_SCAN_START_ADDRESS;
    const SAVE_BODY_PLAYER_GAME_DATA_OFFSET: usize = 0xebae;
    // Native ProfileSummary slot layout: `FaceData` wrapper at slot+0x38; its inner
    // `FaceDataBuffer` (`FACE` magic) starts at slot+0x40. 2026-06-27 native row dumps showed
    // the staged SL2 inner `FaceDataBuffer` bytes match the native row exactly, but the saved
    // `FaceData` wrapper header does not. Mirror `FUN_14025f9b0`: call
    // `FaceData::CopyFromBuffer` (FACE_DATA_COPY_FROM_BUFFER_RVA, shared constant) instead of
    // memcpy'ing the saved wrapper over the live slot. The native row builder passes slot+0x1a8
    // to the equipment renderer (CHR_ASM_COPY_RVA, shared constant) instead of leaving a
    // zero/default `ChrAsm` that only proves renderer plumbing.
    if profile_summary == NULL
        || slot < OWN_STEPPER_SLOT_ZERO
        || slot as usize >= TITLE_PROFILE_SLOT_COUNT
    {
        return false;
    }
    let Some(save_path) = configured_or_default_save_file() else {
        append_autoload_debug(format_args!(
            "native-profile-capture: ProfileSummary seed unavailable -- no configured save_file and no active default save"
        ));
        return false;
    };
    let Ok(mut save_bytes) = fs::read(&save_path) else {
        append_autoload_debug(format_args!(
            "native-profile-capture: staged ProfileSummary seed failed to read '{}'",
            save_path.display()
        ));
        return false;
    };
    normalize_save_bytes_to_active_steam_id(base, &mut save_bytes, "native-profile-capture-seed");
    let Ok(body) = er_save_loader::bnd4::slot_body(&save_bytes, slot as usize) else {
        append_autoload_debug(format_args!(
            "native-profile-capture: staged ProfileSummary seed failed to locate USER_DATA{slot:03} in '{}'",
            save_path.display()
        ));
        return false;
    };
    let min_name_len =
        SAVE_BODY_PLAYER_GAME_DATA_OFFSET + PGD_NAME_9C_OFFSET + PROFILE_SUMMARY_NAME_BYTES;
    let min_face_len = SAVE_BODY_PLAYER_GAME_DATA_OFFSET
        + PGD_FACE_DATA_OFFSET
        + FACE_DATA_BUFFER_OFFSET
        + FACE_DATA_BUFFER_TOTAL_SIZE;
    let min_chr_asm_len = SAVE_BODY_PLAYER_GAME_DATA_OFFSET
        + PGD_EQUIP_GAME_DATA_OFFSET
        + EQUIP_GAME_DATA_CHR_ASM_OFFSET
        + CHR_ASM_SIZE;
    if body.len() < min_name_len || body.len() < min_face_len || body.len() < min_chr_asm_len {
        append_autoload_debug(format_args!(
            "native-profile-capture: staged ProfileSummary seed body too short len={} for PGD offset 0x{SAVE_BODY_PLAYER_GAME_DATA_OFFSET:x} required_name=0x{min_name_len:x} required_face=0x{min_face_len:x} required_chr_asm=0x{min_chr_asm_len:x}",
            body.len()
        ));
        return false;
    }
    let pgd = body
        .as_ptr()
        .wrapping_add(SAVE_BODY_PLAYER_GAME_DATA_OFFSET) as usize;
    let slot_data = profile_summary_record_address(profile_summary, slot as usize);
    unsafe {
        core::ptr::write_bytes(slot_data as *mut u8, 0, PROFILE_SUMMARY_RECORD_STRIDE);
        core::ptr::copy_nonoverlapping(
            (pgd + PGD_NAME_9C_OFFSET) as *const u8,
            slot_data as *mut u8,
            PROFILE_SUMMARY_NAME_BYTES,
        );
        *(slot_data.wrapping_add(PROFILE_SUMMARY_LEVEL_OFFSET) as *mut i32) =
            *((pgd + PGD_LEVEL_68_OFFSET) as *const i32);
        *(slot_data.wrapping_add(PROFILE_SUMMARY_PLAYTIME_OFFSET) as *mut u32) = 0;
        *(slot_data.wrapping_add(PROFILE_SUMMARY_RUNE_MEMORY_OFFSET) as *mut i32) =
            *((pgd + PGD_RUNE_MEMORY_70_OFFSET) as *const i32);
        let copy_face_data_from_buffer: unsafe extern "system" fn(usize, usize) =
            std::mem::transmute(base + FACE_DATA_COPY_FROM_BUFFER_RVA);
        let copy_chr_asm: unsafe extern "system" fn(usize, usize) -> usize =
            std::mem::transmute(base + CHR_ASM_COPY_RVA);
        copy_face_data_from_buffer(
            slot_data.wrapping_add(PROFILE_SUMMARY_FACE_DATA_OFFSET),
            pgd + PGD_FACE_DATA_OFFSET + FACE_DATA_BUFFER_OFFSET,
        );
        copy_chr_asm(
            slot_data.wrapping_add(PROFILE_SUMMARY_CHR_ASM_OFFSET),
            pgd + PGD_EQUIP_GAME_DATA_OFFSET + EQUIP_GAME_DATA_CHR_ASM_OFFSET,
        );
        *(slot_data.wrapping_add(PROFILE_SUMMARY_GENDER_OFFSET) as *mut u8) =
            *((pgd + PGD_GENDER_BE_OFFSET) as *const u8);
        *(slot_data.wrapping_add(PROFILE_SUMMARY_ARCHETYPE_OFFSET) as *mut u8) =
            *((pgd + PGD_ARCHETYPE_BF_OFFSET) as *const u8);
        *(slot_data.wrapping_add(PROFILE_SUMMARY_STARTING_GIFT_OFFSET) as *mut u8) =
            *((pgd + PGD_STARTING_GIFT_C3_OFFSET) as *const u8);
        *(slot_data.wrapping_add(PROFILE_SUMMARY_FIELD_C4_OFFSET) as *mut u8) =
            *((pgd + 0xc4) as *const u8);
        *(profile_summary.wrapping_add(PROFILE_SUMMARY_ACTIVE_FLAGS_OFFSET + slot as usize)
            as *mut u8) = 1;
    }
    let level = unsafe { *((slot_data + PROFILE_SUMMARY_LEVEL_OFFSET) as *const i32) };
    append_autoload_debug(format_args!(
        "native-profile-capture: staged ProfileSummary seed wrote slot={slot} from '{}' pgd_off=0x{SAVE_BODY_PLAYER_GAME_DATA_OFFSET:x} slot_data=0x{slot_data:x} level={level} (scalar + native FaceData::CopyFromBuffer + native ChrAsm copy)",
        save_path.display()
    ));
    true
}
