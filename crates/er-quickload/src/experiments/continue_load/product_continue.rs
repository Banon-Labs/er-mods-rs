use std::{fs, sync::atomic::Ordering};

use eldenring::cs::PlayerIns;

#[allow(unused_imports)]
use crate::*;
#[allow(unused_imports)]
use crate::{crashlog::*, ffi::*, hooks::*, telemetry::*};

use super::*;

pub(crate) use er_telemetry_core::counters::PRODUCT_CONTINUE_EMPTY_PROFILE_ESCALATED;
pub(crate) use er_telemetry_core::counters::PRODUCT_CONTINUE_EMPTY_PROFILE_TICKS;
use er_telemetry_core::counters::{
    PRODUCT_CONTINUE_NO_PLAYER_PICKER_OFFERED, PRODUCT_CONTINUE_NO_PLAYER_TICKS,
};

/// Does the container the game will actually read hold a character in the configured slot?
///
/// The parse lives in `er_save_loader::bnd4::container_holds_character`, which owns the
/// `USER_DATA010.active_slot` bitmap and the reason it, not a decodable body, is the authority.
/// What is shim-only is the two things that cannot leave the DLL: which container this process
/// resolved, and the caching.
///
/// CACHED, and it has to be: the caller is a per-frame boot path and the container is ~29 MB, so an
/// uncached read here would be a 29 MB read per frame on the game thread -- the same shape as the
/// per-call log open that already cost framerate once. The answer cannot change during a boot
/// (the file is whatever the game opened; the slot comes from a `OnceLock`), and `None` is cached
/// too so an unreadable container is not retried sixty times a second.
fn configured_slot_holds_a_character(slot: i32) -> Option<bool> {
    static ANSWER: std::sync::OnceLock<Option<bool>> = std::sync::OnceLock::new();
    *ANSWER.get_or_init(|| {
        let slot = usize::try_from(slot).ok()?;
        let path = crate::configured_or_default_save_file()?;
        let held = er_save_loader::bnd4::container_holds_character(&path, slot)?;
        append_autoload_debug(format_args!(
            "product-core-autoload: container truth for the configured slot -- slot={slot} holds_a_character={held} container='{}'",
            path.display()
        ));
        Some(held)
    })
}

/// The configured slot's fingerprint, having first repaired a `CS::ProfileSummary` the game's own
/// boot read left empty.
///
/// The game deserializes that table exactly once per boot. Measured run 2026-09-05 20:58:51: the
/// wait step polled four times, got the "completed, result code 0" answer instead of the `3` that
/// fills, advanced without calling `GetProfileSummary`, and all ten records stayed zeroed for the
/// rest of the boot -- while the container the runtime had open held all ten characters and our own
/// decoder read every one of them. Waiting out `EMPTY_PROFILE_ESCALATE_TICKS` cannot recover that:
/// there is no second native read to wait for.
///
/// So when the container on disk says this slot holds a character and the live record still says it
/// does not, rebuild the records from that container -- the same writer, throttle and drift watch
/// the picked path already ships. Both guards matter: `profile_real` skips this entirely on a boot
/// whose native read worked, and `Some(true)` from the container means a genuinely vacant slot still
/// takes the old escalate-to-picker path rather than being rewritten from nothing.
unsafe fn fingerprint_slot_repairing_an_empty_summary(slot: i32) -> (bool, i32, u32, usize) {
    let live = unsafe { profile_slot_fingerprint(slot) };
    if live.0 || configured_slot_holds_a_character(slot) != Some(true) {
        return live;
    }
    if !refresh_boot_default_profile_summary() {
        return live;
    }
    unsafe { profile_slot_fingerprint(slot) }
}

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
    // `null` is `usize::MIN` = 0, and so is a refused `game_data_addr`: without the screen an
    // unreadable dialog and an unmapped RVA agree at zero and this reports ready at a title with
    // no dialog at all.
    let want_dialog_vt = er_game_base::mem::game_data_addr(
        base,
        TITLE_TOP_DIALOG_VTABLE_RVA,
        "TITLE_TOP_DIALOG_VTABLE_RVA",
    );
    want_dialog_vt != null && dialog_vt == want_dialog_vt
}
/// `CS::MenuItem`'s constant-false accept predicate: a 3-byte `xor eax,eax; ret` leaf a row carries
/// at `+0xf8` while it is not accept-ready.
///
/// Mapped 2026-08-30 as `0x7add70 -> 0x7aebf0`, after being the one constant here with no 1.17
/// row -- and the reason it was missing is worth keeping. It is a `.pdata`-less leaf, invisible to
/// the whole-image function-table alignment, and nothing calls IT: its address is only ever taken,
/// so the caller-vote tools were blind to it too until they learned to count `lea`s. The evidence
/// is a unanimous 1-of-1 -- each image contains exactly one rip-relative reference to its address,
/// both at byte offset +0xa5 inside `0x7acf80 -> 0x7ade00` (`IDENTICAL-WHOLE`, 151 insns, `.pdata`
/// 0x232 in both), both spelled `48 8d 05 44 0d 00 00`.
///
/// Its ledger verdict is `IDENTICAL-LEAF-NOPATCH`: both 3-byte bodies compared in full and equal,
/// and MinHook's own rules refuse the site, so `er-game-base` admits the row to the CALL/READ map
/// and never to the detour one. That is exactly the shape this site needs -- it only compares.
/// The verdict exists because the two used to be one decision: `IDENTICAL-SHORT` refused the hook
/// and withdrew the address from comparing, and this constant was what paid for it.
const MENU_ITEM_ACCEPT_IDLE_RVA: usize = 0x007add70;

/// `CS::MenuItem`'s real accept predicate: the row is selectable. `0x7ad810 -> 0x7ae690`.
const MENU_ITEM_ACCEPT_NATIVE_RVA: usize = 0x007ad810;

/// Is `accept_predicate` the constant-false idle predicate? Resolved, and never satisfied by zero:
/// a refusal resolves to 0 and an uninitialised row reads 0 at `+0xf8`.
fn accept_predicate_is_idle(base: usize, accept_predicate: usize) -> bool {
    let idle = er_game_base::mem::game_data_addr(
        base,
        MENU_ITEM_ACCEPT_IDLE_RVA,
        "MENU_ITEM_ACCEPT_IDLE_RVA",
    );
    idle != 0 && accept_predicate == idle
}

pub(crate) fn record_continue_candidate(item: usize, accept_predicate: usize, base: usize) {
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
            "MENU-CONTINUE-CANDIDATE accept predicate changed item=0x{item:x} prior=0x{prior:x} now=0x{accept_predicate:x}"
        ));
    }
    let native_accept = er_game_base::mem::game_data_addr(
        base,
        MENU_ITEM_ACCEPT_NATIVE_RVA,
        "MENU_ITEM_ACCEPT_NATIVE_RVA",
    );
    if base != null && native_accept != null && accept_predicate == native_accept {
        MENU_CONTINUE_CANDIDATE_NATIVE_ACCEPT_HITS.fetch_add(1, Ordering::SeqCst);
    } else if base != null && accept_predicate_is_idle(base, accept_predicate) {
        MENU_CONTINUE_CANDIDATE_IDLE_ACCEPT_HITS.fetch_add(1, Ordering::SeqCst);
    } else {
        MENU_CONTINUE_CANDIDATE_OTHER_ACCEPT_HITS.fetch_add(1, Ordering::SeqCst);
    }
}
pub(crate) unsafe fn product_continue_item_action(base: usize) -> Option<NativeContinueItemAction> {
    const DOCALL_VTABLE_SLOT_10: usize = 0x10;
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    let item = MENU_CONTINUE_ITEM.load(Ordering::SeqCst);
    if item == null {
        let candidate = MENU_CONTINUE_CANDIDATE_ITEM.load(Ordering::SeqCst);
        if candidate != null {
            append_autoload_debug(format_args!(
                "product-core-autoload: ignoring diagnostic Continue candidate=0x{candidate:x}; waiting for semantic native-accept MENU_CONTINUE_ITEM instead"
            ));
        }
        return None;
    }
    let item_vt = unsafe { safe_read_usize(item) }?;
    if item_vt
        != er_game_base::mem::game_data_addr(
            base,
            MENU_WINDOW_JOB_VTABLE_RVA,
            "MENU_WINDOW_JOB_VTABLE_RVA",
        )
    {
        append_autoload_debug(format_args!(
            "product-core-autoload: native Continue MenuWindowJob rejected item=0x{item:x} vt=0x{item_vt:x} expected=0x{:x}",
            er_game_base::mem::game_data_addr(
                base,
                MENU_WINDOW_JOB_VTABLE_RVA,
                "MENU_WINDOW_JOB_VTABLE_RVA"
            )
        ));
        return None;
    }
    let functor = unsafe { safe_read_usize(item + MENU_ITEM_FUNCTOR_A8_OFFSET) }?;
    if functor == null {
        return None;
    }
    let functor_vt = unsafe { safe_read_usize(functor) }?;
    let do_call = unsafe { safe_read_usize(functor_vt + DOCALL_VTABLE_SLOT_10) }?;
    // Resolved, and never satisfied by zero. `MenuTitleContinue::_Do_call` moved on 1.17
    // (0x764b80 -> 0x7659d0), so the raw comparison could not match and every native Continue
    // MenuWindowJob was rejected here -- the autoload's own path to the Continue row, refused on a
    // stale address rather than on anything about the item, and silently.
    let expected_do_call = er_game_base::mem::game_data_addr(
        base,
        MENU_TITLE_CONTINUE_DOCALL_RVA,
        "MENU_TITLE_CONTINUE_DOCALL_RVA",
    );
    if expected_do_call == 0 || do_call != expected_do_call {
        append_autoload_debug(format_args!(
            "product-core-autoload: native Continue MenuWindowJob rejected item=0x{item:x} functor=0x{functor:x} docall=0x{do_call:x} expected=0x{expected_do_call:x}"
        ));
        return None;
    }
    const MENU_ITEM_ACCEPT_PREDICATE_F8_OFFSET: usize = 0xf8;
    let accept_predicate = unsafe { safe_read_usize(item + MENU_ITEM_ACCEPT_PREDICATE_F8_OFFSET) }?;
    record_continue_candidate(item, accept_predicate, base);
    // The idle predicate is a rejection, and the native-accept check below rejects the same items
    // for the same reason, so a refusal here costs the precise log line and not the decision.
    if accept_predicate_is_idle(base, accept_predicate) {
        append_autoload_debug(format_args!(
            "product-core-autoload: native Continue MenuWindowJob rejected item=0x{item:x} accept_predicate=0x{accept_predicate:x} (constant false idle predicate) -- not a semantic accept-ready Continue item"
        ));
        return None;
    }
    if accept_predicate
        != er_game_base::mem::game_data_addr(
            base,
            MENU_ITEM_ACCEPT_NATIVE_RVA,
            "MENU_ITEM_ACCEPT_NATIVE_RVA",
        )
    {
        append_autoload_debug(format_args!(
            "product-core-autoload: native Continue MenuWindowJob rejected item=0x{item:x} accept_predicate=0x{accept_predicate:x} expected native accept predicate 0x{:x}",
            er_game_base::mem::game_data_addr(
                base,
                MENU_ITEM_ACCEPT_NATIVE_RVA,
                "MENU_ITEM_ACCEPT_NATIVE_RVA"
            )
        ));
        return None;
    }
    if MENU_CONTINUE_ITEM
        .compare_exchange(
            TITLE_OWNER_SCAN_START_ADDRESS,
            item,
            Ordering::SeqCst,
            Ordering::SeqCst,
        )
        .is_ok()
    {
        append_autoload_debug(format_args!(
            "product-core-autoload: promoted candidate native Continue MenuWindowJob item=0x{item:x} accept_predicate=0x{accept_predicate:x}"
        ));
    }
    let result = unsafe { safe_read_usize(item + MENU_ITEM_DIALOG_RESULT_130_OFFSET) }?;
    if result == null {
        return None;
    }
    let result_vt = unsafe { safe_read_usize(result) }?;
    if !vtable_in_game_image(result_vt, base) {
        append_autoload_debug(format_args!(
            "product-core-autoload: native Continue MenuWindowJob rejected item=0x{item:x} result=0x{result:x} result_vt=0x{result_vt:x}"
        ));
        return None;
    }
    Some(NativeContinueItemAction {
        item,
        result,
        result_vt,
        functor,
        do_call,
    })
}
pub(crate) unsafe fn submit_native_continue_item_action(
    action: NativeContinueItemAction,
    base: usize,
) -> Option<i32> {
    const MENU_ITEM_RESULT_MODE_UNKNOWN: i32 = i32::MIN;
    let diagnostic_mode = unsafe { safe_read_i32(action.result + MENU_ITEM_RESULT_MODE_58_OFFSET) }
        .unwrap_or(MENU_ITEM_RESULT_MODE_UNKNOWN);
    let event_handler =
        unsafe { safe_read_usize(action.result_vt + MENU_ITEM_RESULT_EVENT_SLOT_60_OFFSET) }?;
    if !vtable_in_game_image(event_handler, base) {
        append_autoload_debug(format_args!(
            "product-core-autoload: native Continue submit ABI rejected item=0x{:x} result=0x{:x} result_vt=0x{:x} event_handler=0x{event_handler:x} diagnostic_mode={diagnostic_mode}",
            action.item, action.result, action.result_vt
        ));
        return None;
    }
    #[allow(dead_code)] // Retained: Decoded FD4 event-payload shape for the Continue wrapper; the current submit path logs the ABI instead of building the event.
    const CONTINUE_WRAPPER_EVENT_WORDS: usize = 2;
    #[allow(dead_code)] // Retained: Word index within the decoded Continue event payload; see CONTINUE_WRAPPER_EVENT_WORDS.
    const CONTINUE_WRAPPER_EVENT_CODE_INDEX: usize = 0;
    #[allow(dead_code)] // Retained: Word index within the decoded Continue event payload; see CONTINUE_WRAPPER_EVENT_WORDS.
    const CONTINUE_WRAPPER_EVENT_PAYLOAD_INDEX: usize = 1;
    let native_submit = er_game_base::mem::game_data_addr(
        base,
        MENU_WINDOW_CLOSE_WITH_FAILED_RVA,
        "MENU_WINDOW_CLOSE_WITH_FAILED_RVA",
    );
    // Logged, not called -- but printing a 1.16.2 address as though it described the running build
    // is how a stale constant survives review. `0x7a91e0 -> 0x7aa060` on 1.17.
    let fd4_event_constructor = er_game_base::mem::game_data_addr(
        base,
        FD4_EVENT_CONSTRUCTOR_RVA,
        "FD4_EVENT_CONSTRUCTOR_RVA",
    );
    let native_submit_fn: unsafe extern "system" fn(usize) =
        unsafe { std::mem::transmute(native_submit) };
    append_autoload_debug(format_args!(
        "product-core-autoload: native Continue submit ABI proven item=0x{:x} result=0x{:x} result_vt=0x{:x} event_handler=0x{event_handler:x} native_submit=0x{native_submit:x} fd4_event_ctor=0x{fd4_event_constructor:x} diagnostic_mode={diagnostic_mode} -- result+0x58 logged only, never used as readiness",
        action.item, action.result, action.result_vt
    ));
    unsafe { native_submit_fn(action.result) };
    append_autoload_debug(format_args!(
        "product-core-autoload: native Continue submit dispatcher returned after event_handler=0x{event_handler:x} -- modal-confirm wait remains disabled downstream until loaded evidence"
    ));
    Some(diagnostic_mode)
}
/// Hand the user the save picker when the boot is provably dead rather than merely slow.
///
/// # Why a contradiction rather than a timeout
///
/// The four facts `read_boot_progress_facts` returns are read out of the running game: a local
/// player, the `InGameStep` request code, the live `MenuJob` pointer and the loading-screen mode.
/// If all four say nothing is happening, nothing is queued that could ever produce a character, so
/// waiting longer cannot change the answer -- and the tick count is only a three-frame debounce
/// against sampling a gap between two handoffs, never a duration.
///
/// A timeout was written here first and was wrong for the reason a timeout is always wrong on this
/// path: it cannot tell a slow load from a dead one, so it either fires on a machine that was
/// still working or hides a real gap behind minutes of waiting during development. A slow load
/// keeps its loading screen up and its request pending the whole way through, which is exactly
/// what this reads.
///
/// # Safety
///
/// Game task thread, the context `product_continue_autoload_tick` already requires.
/// What is actually wrong with the save this boot gave up on, in a sentence the player can act on.
///
/// "Nothing was ever queued for it" describes our own loader and leaves the player with no move to
/// make. The container on disk answers the question they are really asking -- is my save broken,
/// or is this mod broken -- and it answers it in the only place that can: by opening the file and
/// looking at its slots. Three outcomes, three different actions:
///
/// * unreadable, or no characters at all -- the file is the problem, pick another;
/// * characters, but not in the slot this run asked for -- name the slots that do exist;
/// * the requested slot holds a real character -- then the save is fine and this mod failed, which
///   is worth saying plainly rather than implying the player's save is bad.
///
/// Reading 28 MB on the game task would be unacceptable on a live boot. This one is already dead
/// by four independent measurements, and the picker it is about to raise reads the same file.
fn picker_detail_for_configured_save(slot: i32) -> ConfiguredSaveTruth {
    let Some(path) = crate::experiments::configured_or_default_save_file() else {
        return ConfiguredSaveTruth::nothing_to_load(
            "No save file is configured for this run.".to_owned(),
        );
    };
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.display().to_string());
    let Ok(bytes) = std::fs::read(&path) else {
        return ConfiguredSaveTruth::nothing_to_load(format!(
            "{name} could not be read from disk."
        ));
    };
    let slots = er_save_picker_core::slots::parse_save_character_slots(&bytes);
    if slots.is_empty() {
        return ConfiguredSaveTruth::nothing_to_load(format!("{name} holds no characters at all."));
    }
    match slots.iter().find(|info| info.slot as i32 == slot) {
        Some(found) => ConfiguredSaveTruth {
            loadable: Some((path, found.slot as usize)),
            detail: format!(
                "{name} slot {slot} holds {} at level {}, so the save itself is fine -- this mod failed to start the load.",
                found.name, found.level
            ),
        },
        None => {
            let held: Vec<String> = slots
                .iter()
                .map(|info| format!("{} in slot {}", info.name, info.slot))
                .collect();
            ConfiguredSaveTruth::nothing_to_load(format!(
                "{name} has no character in slot {slot}. It holds {}.",
                held.join(", ")
            ))
        }
    }
}

/// What the configured save actually holds in the slot this run asked for.
///
/// One read of the container answers two different questions, and before this only the second one
/// was asked. `detail` is the sentence the picker shows when the user has to choose; `loadable` is
/// the save and slot the mod can commit by itself when there is nothing to choose between --
/// the configured character is right there and the user already named it.
struct ConfiguredSaveTruth {
    /// The configured container and the slot in it that holds a character, when one does.
    loadable: Option<(std::path::PathBuf, usize)>,
    /// Why the picker is being raised, in a sentence the player can act on.
    detail: String,
}

impl ConfiguredSaveTruth {
    fn nothing_to_load(detail: String) -> Self {
        Self {
            loadable: None,
            detail,
        }
    }
}

unsafe fn product_continue_offer_picker_if_boot_is_dead(
    base: usize,
    owner: usize,
    slot: i32,
    tick: u64,
) {
    let offered = PRODUCT_CONTINUE_NO_PLAYER_PICKER_OFFERED.load(Ordering::SeqCst) != 0;
    // Safety: game task thread, and `owner` is the title step this tick was handed.
    let player_present = unsafe { PlayerIns::local_player_mut() }.is_ok();
    let facts =
        unsafe { er_title_flow::boot_hold::read_boot_progress_facts(base, owner, player_present) };
    let ticks = er_title_flow::boot_hold::dead_boot_next_ticks(
        PRODUCT_CONTINUE_NO_PLAYER_TICKS.load(Ordering::SeqCst) as u64,
        facts,
    );
    PRODUCT_CONTINUE_NO_PLAYER_TICKS.store(ticks as usize, Ordering::SeqCst);
    if er_title_flow::boot_hold::no_player_action(ticks, offered)
        != er_title_flow::boot_hold::NoPlayerAction::OfferPicker
    {
        return;
    }
    PRODUCT_CONTINUE_NO_PLAYER_PICKER_OFFERED.store(1, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "product-core-autoload: *** this boot is dead, not slow (slot={slot} tick={tick}) *** -- {facts:?} for {ticks} consecutive ticks: no player, no in-game step request, no menu job and no loading screen, so nothing is running and nothing is queued that could ever produce a character; arming the missing-save picker so the user can choose a save that loads"
    ));
    let truth = picker_detail_for_configured_save(slot);
    er_save_picker_core::reason::record_reason_detail(truth.detail.clone());
    let armed = crate::experiments::offer_missing_save_picker(
        er_save_picker_core::reason::MissingSaveReason::BootNeverStartedTheLoad,
    );
    append_autoload_debug(format_args!(
        "product-core-autoload: dead-boot picker arm requested for slot={slot} -> armed_by_this_call={armed}"
    ));
    // ...and then answer it ourselves, when the answer is not in doubt.
    //
    // The arm is what makes the boot's save-data job wait and re-read, so it has to happen; what
    // does not have to happen is asking the user to pick the save they already configured. Every
    // 1.17.1 boot measured on 2026-09-13 reached here -- both of the autoload's row
    // identifications are unsatisfiable on this build -- and exactly one run went on to a live
    // character: br-20260913-030551-d8d9, where a save was committed through this same completion.
    // So the commit is the loader, and the picker is its fallback rather than its only path.
    //
    // A refusal leaves the picker up carrying its own reason, which is the case where the user
    // genuinely does have to choose.
    let Some((path, picked_slot)) = truth.loadable else {
        return;
    };
    append_autoload_debug(format_args!(
        "product-core-autoload: the configured save holds a character in slot {picked_slot}, so committing it instead of asking -- '{}'",
        path.display()
    ));
    let committed = er_save_picker_core::overlay::commit_missing_save_selection(
        &path,
        picked_slot,
        "configured-save",
    );
    append_autoload_debug(format_args!(
        "product-core-autoload: configured-save commit for slot={picked_slot} -> committed={committed}{}",
        if committed {
            ""
        } else {
            "; the picker stays up with the refusal on screen"
        }
    ));
}

pub(crate) unsafe fn product_continue_autoload_tick(
    owner: usize,
    base: usize,
    gm: usize,
    slot: i32,
    tick: u64,
    ready: &ProductCoreAutoloadReady,
) {
    const PRODUCT_CONTINUE_C30_ZERO: i32 = 0;
    const PRODUCT_CONTINUE_B80_MODAL_WAIT: i32 = 1;
    const PRODUCT_CONTINUE_NEW_GAME_BLOCKED: u8 = 1;
    const PRODUCT_CONTINUE_WAIT_LOG_TICKS: u64 = 30;
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    let phase = FULLREAD_PHASE.load(Ordering::SeqCst);
    let read_i32 = |off: usize| unsafe { safe_read_i32(gm + off) }.unwrap_or(GAME_MAN_C30_UNSET);

    // Before any phase branch, because the case this covers reaches none of them. Every other
    // hand-back in this file and in `slot_resolution` fires from a branch that decided "this save
    // cannot be loaded" -- so a boot that never gets far enough to decide anything has no exit at
    // all, and the player is left at a title that will never move. Measured on run
    // br-20260913-023348-3b9d: `SWITCH-ORACLE #1980 slot=1 player=false`, `boot-view DECISION` with
    // every handoff false, 388 title-logo hide calls, no further progress of any kind.
    //
    // The recourse is the one the other exits already use: `arm_missing_save_picker_after_boot`
    // arms the game's own in-game picker and is one-shot by construction, so a per-frame tick
    // cannot re-arm or spam it. Nothing is written into game state here -- the native picker owns
    // the choice and the retry runs through the native full-read chain, exactly as it does when a
    // configured save is missing.
    unsafe { product_continue_offer_picker_if_boot_is_dead(base, owner, slot, tick) };

    if phase == FULLREAD_PHASE_DONE {
        return;
    }

    if phase == FULLREAD_PHASE_SUBMIT {
        // Switch-SAFETY (System->Quit->Load-Profile): for the in-world character switch (not a boot
        // autoload), the return-title chain we submitted is still tearing down the old world. Firing
        // the Continue-load now sets GameMan saveState/b80=2 and DoSaveStuff deserializes the picked
        // slot into the still-live world -> crash in CSGaitemImp::Deserialize (live 0x67141a). Defer
        // until the old world is actually gone (local player absent), so the load runs at a clean
        // title exactly like the boot autoload does. The boot path has no System-Quit phase, and at a
        // fresh title there is no local player, so this gate passes immediately there.
        // See bd system-quit-load-profile-trigger-resolved.
        if SYSTEM_QUIT_QUICKLOAD_PHASE.load(Ordering::SeqCst) != SYSTEM_QUIT_QUICKLOAD_PHASE_IDLE
            && unsafe { PlayerIns::local_player_mut() }.is_ok()
        {
            if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                append_autoload_debug(format_args!(
                    "product-core-autoload: SWITCH deferring Continue-load until old world torn down -- local player still present slot={slot} tick={tick}"
                ));
            }
            return;
        }
        if !unsafe { product_continue_action_ready(ready, base, gm, slot) } {
            if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                append_autoload_debug(format_args!(
                    "product-core-autoload: Continue submit gated off dialog=0x{:x} menu_latch={} slot={slot} -- semantic menu readiness not stable",
                    ready.title_dialog, ready.menu_opened_latch
                ));
            }
            return;
        }
        let b80_before = read_i32(GAME_MAN_SAVE_STATE_B80_OFFSET);
        if b80_before != OWN_STEPPER_B80_IDLE {
            if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                append_autoload_debug(format_args!(
                    "product-core-autoload: waiting for native preview/load b80={b80_before} to become idle before Continue row fire -- no SetState5"
                ));
            }
            return;
        }
        let (profile_real, profile_map, profile_level, profile_name_len) =
            unsafe { fingerprint_slot_repairing_an_empty_summary(slot) };
        // Consecutive, and reset by a single real read. A boot whose ProfileSummary is still
        // filling can reach this check before the save-data job has parsed it, so the count has to
        // measure an unbroken run of empty-like reads -- not how long the autoload has been alive.
        let empty_ticks = PRODUCT_CONTINUE_EMPTY_PROFILE_TICKS.load(Ordering::SeqCst) as u64;
        let empty_ticks =
            er_title_flow::boot_hold::empty_profile_next_ticks(empty_ticks, profile_real);
        PRODUCT_CONTINUE_EMPTY_PROFILE_TICKS.store(empty_ticks as usize, Ordering::SeqCst);
        if !profile_real {
            let escalated = PRODUCT_CONTINUE_EMPTY_PROFILE_ESCALATED.load(Ordering::SeqCst) != null;
            // Ask the container before spending the patience. The 1800-tick wait exists to tell a
            // ProfileSummary that is still filling apart from a slot that is genuinely vacant, and
            // it is the right answer for the first case. For the second the container on disk knows
            // already, so waiting is pure loss -- see `configured_slot_holds_a_character` for the
            // 2026-09-03 run this cost. Unreadable (`None`) keeps the old behaviour exactly.
            let action = if !escalated && configured_slot_holds_a_character(slot) == Some(false) {
                er_title_flow::boot_hold::EmptyProfileAction::Escalate
            } else {
                er_title_flow::boot_hold::empty_profile_action(
                    empty_ticks,
                    PRODUCT_CONTINUE_WAIT_LOG_TICKS,
                    escalated,
                )
            };
            match action {
                er_title_flow::boot_hold::EmptyProfileAction::Escalate => {
                    // The dead end ends here. Waiting longer cannot help: this branch has
                    // republished the identical fingerprint every tick for the whole threshold
                    // window, so the profile is not filling, it is absent. Reject our own selection
                    // and hand the choice to the user -- the picker's pick supersedes it, and the
                    // retry runs through the native full-read chain (which reads the picked
                    // container directly) instead of re-fingerprinting this slot.
                    PRODUCT_CONTINUE_EMPTY_PROFILE_ESCALATED
                        .store(OWN_STEPPER_CALL_INC, Ordering::SeqCst);
                    append_autoload_debug(format_args!(
                        "product-core-autoload: *** GIVING UP on the Continue slot after {empty_ticks} consecutive empty-like ticks (slot={slot} map=0x{profile_map:x} level={profile_level} name_len={profile_name_len} tick={tick}) *** -- this save cannot be loaded; arming the missing-save picker so the user can choose one that can"
                    ));
                    let armed = offer_missing_save_picker(
                        er_save_picker_core::reason::MissingSaveReason::ContinueSlotEmpty,
                    );
                    append_autoload_debug(format_args!(
                        "product-core-autoload: late picker arm requested for slot={slot} map=0x{profile_map:x} level={profile_level} name_len={profile_name_len} -> armed_by_this_call={armed}"
                    ));
                }
                er_title_flow::boot_hold::EmptyProfileAction::Log => {
                    append_autoload_debug(format_args!(
                        "product-core-autoload: Continue slot profile is empty-like (slot={slot} map=0x{profile_map:x} level={profile_level} name_len={profile_name_len}); waiting {empty_ticks}/{} ticks before rejecting this save and arming the missing-save picker -- no native Load Game fallback, no legal-popup auto-accept, no Continue submit, and no input",
                        er_title_flow::boot_hold::EMPTY_PROFILE_ESCALATE_TICKS
                    ));
                }
                er_title_flow::boot_hold::EmptyProfileAction::Wait => {}
            }
            return;
        }
        let Some(action) = (unsafe { product_continue_item_action(base) }) else {
            // The continue latch is UNSATISFIABLE at the title, so this is not a wait -- it is the
            // path. `MENU_CONTINUE_ITEM` latches only on a MenuWindowJob whose docall matches
            // `MENU_TITLE_CONTINUE_DOCALL_RVA` and whose accept predicate is
            // `MENU_ITEM_ACCEPT_NATIVE_RVA`, and the 1.16.2 curated dump names both:
            //   * 0x140764b80 is an adjustor thunk (`ADD RCX,8 ; JMP 0x140763fc0`) onto a function
            //     that allocates 0xaa0 and constructs **CS::BackScreen** (a CS::FullScreenMenu)
            //     with a "Fade" proxy -- the black fade screen, built by the `L"01_900_Black"`
            //     factory 0x140764290 whose only code xref is CSMenuManImp::Update;
            //   * 0x1407ad810 is `GLOBAL_CSMenuMan != 0 && !FUN_140765f20(GLOBAL_CSMenuMan)` -- a
            //     global "menu manager not busy" check, stored by the generic MenuWindowJob ctors,
            //     so it says nothing about Continue.
            // Measured 2026-09-05 21:32: 416/416 candidate observations idle,
            // `native_accept_hits = 0`, `accept_changes = 0`, and the autoload parked forever.
            //
            // `title_menu_action_ready` is the identification that is grounded: TitleTopDialog
            // vtable, the [dialog+0xa48] registry, a MenuMemberFuncJob vtable, and a member_fn that
            // resolves through at most six thunk hops to the live Load-Game dialog factory. Firing
            // its node through the native run 0x1409aaba0 is the game's own path -- no forged
            // context, no input, no direct deserialize.
            let owner = {
                let latched = TITLE_OWNER_PTR.load(Ordering::SeqCst);
                if latched != null {
                    latched
                } else {
                    TITLE_SETSTATE_TRACE_LAST_OWNER.load(Ordering::SeqCst)
                }
            };
            let node = if owner == null {
                None
            } else {
                unsafe { er_title_flow::title_menu_action_ready(owner, base) }
            };
            match node {
                Some(node) => {
                    unsafe { *((gm + GAME_MAN_SLOT_SELECT_B78_OFFSET) as *mut i32) = slot };
                    unsafe { fire_product_title_load_action(node, base, tick, slot) };
                }
                None => {
                    if tick % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 {
                        append_autoload_debug(format_args!(
                            "product-core-autoload: waiting for the semantic Load-Game MenuMemberFuncJob node (owner=0x{owner:x} dialog=0x{:x} slot={slot}) -- TitleTopDialog/registry/node/member_fn not all validated yet; the Continue MenuWindowJob latch is unsatisfiable and is no longer waited on",
                            ready.title_dialog
                        ));
                    }
                }
            }
            return;
        };
        unsafe { *((gm + GAME_MAN_SLOT_SELECT_B78_OFFSET) as *mut i32) = slot };
        let set_save_slot: unsafe extern "system" fn(i32) = unsafe {
            std::mem::transmute(
                match crate::experiments::gated_game_fn(
                    FORCE_PLAY_GAME_SET_SAVE_SLOT_RVA,
                    "FORCE_PLAY_GAME_SET_SAVE_SLOT_RVA",
                ) {
                    Some(address) => address,
                    None => return,
                },
            )
        };
        unsafe { set_save_slot(slot) };
        OWN_STEPPER_EXPECTED_SLOT.store(slot, Ordering::SeqCst);
        OWN_STEPPER_CONFIRMED.store(TITLE_OWNER_SCAN_START_ADDRESS, Ordering::SeqCst);
        OWN_STEPPER_DESER_FIRED.store(OWN_STEPPER_DESER_NOT_FIRED, Ordering::SeqCst);
        OWN_STEPPER_MOUNT_C30.store(GAME_MAN_C30_UNSET, Ordering::SeqCst);
        OWN_STEPPER_IO_WAS_SET.store(OWN_STEPPER_IO_WAS_SET_NO, Ordering::SeqCst);
        let Some(result_mode) = (unsafe { submit_native_continue_item_action(action, base) })
        else {
            return;
        };
        let b80 = read_i32(GAME_MAN_SAVE_STATE_B80_OFFSET);
        let ac0 = read_i32(FORCE_PLAY_GAME_GM_SLOT_AC0_OFFSET);
        let b78 = read_i32(GAME_MAN_SLOT_SELECT_B78_OFFSET);
        let c30 = read_i32(GAME_MAN_SAVED_MAP_C30_OFFSET);
        let (fp_real, fp_level, fp_name_len) = unsafe { char_fingerprint(base) };
        append_autoload_debug(format_args!(
            "product-core-autoload: *** SUBMITTED native Continue MenuWindowJob result mode={result_mode} submit=0x{:x}(result=0x{:x}, result_vt=0x{:x}, item=0x{:x}, functor=0x{:x}, docall=0x{:x}) after set_save_slot({slot}) b78={b78} ac0={ac0} c30=0x{c30:x} b80={b80} fp_real={fp_real}(level={fp_level} name_len={fp_name_len}) dialog=0x{:x} menu_latch={} tick={tick} -- no input/direct_load/direct_build/raw deserialize/direct_confirm ***",
            er_game_base::mem::game_data_addr(
                base,
                MENU_WINDOW_CLOSE_WITH_FAILED_RVA,
                "MENU_WINDOW_CLOSE_WITH_FAILED_RVA"
            ),
            action.result,
            action.result_vt,
            action.item,
            action.functor,
            action.do_call,
            ready.title_dialog,
            ready.menu_opened_latch
        ));
        timeline_event(
            "T_native_continue_action",
            tick,
            format_args!(
                "slot={slot} item=0x{:x} result=0x{:x} b80={b80}",
                action.item, action.result
            ),
        );
        FULLREAD_DRAIN_WAITS.store(null, Ordering::SeqCst);
        FULLREAD_PHASE.store(FULLREAD_PHASE_GUARD, Ordering::SeqCst);
        return;
    }

    if phase == FULLREAD_PHASE_GUARD {
        let expected = OWN_STEPPER_EXPECTED_SLOT.load(Ordering::SeqCst);
        let ac0 = read_i32(FORCE_PLAY_GAME_GM_SLOT_AC0_OFFSET);
        let c30 = read_i32(GAME_MAN_SAVED_MAP_C30_OFFSET);
        let b80 = read_i32(GAME_MAN_SAVE_STATE_B80_OFFSET);
        let latched = OWN_STEPPER_MOUNT_C30.load(Ordering::SeqCst);
        let deser_ok = OWN_STEPPER_DESER_FIRED.load(Ordering::SeqCst) == OWN_STEPPER_DESER_FIRED_OK;
        let (fp_real, fp_level, fp_name_len) = unsafe { char_fingerprint(base) };
        let slot_identity = unsafe { requested_slot_identity(expected, c30) };
        let waits = FULLREAD_DRAIN_WAITS.fetch_add(OWN_STEPPER_CALL_INC, Ordering::SeqCst) as u64;
        let c30_available =
            c30 == latched && c30 != GAME_MAN_C30_UNSET && c30 != PRODUCT_CONTINUE_C30_ZERO;
        let c30_sane = c30_available && (c30 != GAME_MAN_NEWGAME_DEFAULT_MAP || fp_real);
        let c30_loaded = c30 != GAME_MAN_C30_UNSET && c30 != PRODUCT_CONTINUE_C30_ZERO;
        let c30_loaded_sane = c30_loaded && (c30 != GAME_MAN_NEWGAME_DEFAULT_MAP || fp_real);
        let new_game_flag =
            unsafe { safe_read_usize(owner + TITLE_OWNER_NEW_GAME_FLAG_284_OFFSET) }
                .map(|v| v as u8)
                .unwrap_or(PRODUCT_CONTINUE_NEW_GAME_BLOCKED);
        let commit = native_fullread_commit_enabled();
        let b80_idle = b80 == OWN_STEPPER_B80_IDLE;
        let b80_modal_wait = b80 == PRODUCT_CONTINUE_B80_MODAL_WAIT;
        let native_confirmed =
            OWN_STEPPER_CONFIRMED.load(Ordering::SeqCst) != TITLE_OWNER_SCAN_START_ADDRESS;
        let modal_disable_ready = commit
            && !native_confirmed
            && b80_modal_wait
            && fp_real
            && slot_identity.matches
            && ac0 == expected
            && expected != OWN_STEPPER_SLOT_NONE
            && c30_loaded_sane
            && new_game_flag == FULLREAD_OWNER_NEW_GAME_OK;
        if modal_disable_ready {
            let shim = &raw mut OWN_STEPPER_SHIM;
            unsafe { (*shim)[OWN_STEPPER_SHIM_OWNER_IDX] = owner };
            let shim_ptr = shim as usize;
            let confirm: unsafe extern "system" fn(usize) = unsafe {
                std::mem::transmute(
                    match crate::experiments::gated_game_fn(
                        CONTINUE_CONFIRM_RVA,
                        "CONTINUE_CONFIRM_RVA",
                    ) {
                        Some(address) => address,
                        None => return,
                    },
                )
            };
            append_autoload_debug(format_args!(
                "product-core-autoload: MODAL-CONFIRM-DISABLED loaded evidence ac0={ac0} expected={expected} c30=0x{c30:x} fp_real={fp_real}(level={fp_level} name_len={fp_name_len}) slot_identity=true(profile=0x{:x} profile_map=0x{:x} profile_level={} profile_name_len={}) b80={b80} owner+0x284={new_game_flag} -> continue_confirm shim=0x{shim_ptr:x} owner=0x{owner:x} (no confirm input)",
                slot_identity.profile_summary,
                slot_identity.profile_map,
                slot_identity.profile_level,
                slot_identity.profile_name_len
            ));
            timeline_event(
                "T_modal_confirm_disabled",
                tick,
                format_args!("ac0={ac0} c30=0x{c30:x} b80={b80}"),
            );
            unsafe { confirm(shim_ptr) };
            OWN_STEPPER_CONFIRMED.store(OWN_STEPPER_CALL_INC, Ordering::SeqCst);
            append_autoload_debug(format_args!(
                "product-core-autoload: STAGE2-SETSTATE5 fired via disabled modal confirm owner=0x{owner:x} -- native pump now streams the real world"
            ));
        }
        let native_confirmed =
            OWN_STEPPER_CONFIRMED.load(Ordering::SeqCst) != TITLE_OWNER_SCAN_START_ADDRESS;
        let proceed = commit
            && (deser_ok || modal_disable_ready)
            && native_confirmed
            && fp_real
            && slot_identity.matches
            && ac0 == expected
            && expected != OWN_STEPPER_SLOT_NONE
            && (c30_sane || c30_loaded_sane)
            && (b80_idle || modal_disable_ready)
            && new_game_flag == FULLREAD_OWNER_NEW_GAME_OK;
        if waits % PRODUCT_CONTINUE_WAIT_LOG_TICKS == null as u64 || proceed {
            append_autoload_debug(format_args!(
                "product-core-autoload: Continue post-click GUARD waits={waits} commit={commit} deser_ok={deser_ok} native_confirmed={native_confirmed} ac0={ac0} expected={expected} c30=0x{c30:x} latched=0x{latched:x} c30_sane={c30_sane} fp_real={fp_real}(level={fp_level} name_len={fp_name_len}) slot_identity={} profile=0x{:x} profile_map=0x{:x} profile_level={} profile_name_len={} pgd_level={} pgd_name_len={} owner+0x284={new_game_flag} b80={b80} proceed={proceed} -- waiting for requested-slot native b80/c30 writer + native continue_confirm/SetState5",
                slot_identity.matches,
                slot_identity.profile_summary,
                slot_identity.profile_map,
                slot_identity.profile_level,
                slot_identity.profile_name_len,
                slot_identity.pgd_level,
                slot_identity.pgd_name_len
            ));
        }
        if !proceed {
            if waits >= FULLREAD_DRAIN_MAX {
                append_autoload_debug(format_args!(
                    "product-core-autoload: Continue post-click GUARD timeout waits={waits} commit={commit} deser_ok={deser_ok} ac0={ac0} expected={expected} c30=0x{c30:x} latched=0x{latched:x} c30_sane={c30_sane} fp_real={fp_real}(level={fp_level} name_len={fp_name_len}) slot_identity={} profile=0x{:x} profile_map=0x{:x} profile_level={} profile_name_len={} pgd_level={} pgd_name_len={} owner+0x284={new_game_flag} b80={b80} -- DONE (NO SetState5)",
                    slot_identity.matches,
                    slot_identity.profile_summary,
                    slot_identity.profile_map,
                    slot_identity.profile_level,
                    slot_identity.profile_name_len,
                    slot_identity.pgd_level,
                    slot_identity.pgd_name_len
                ));
                FULLREAD_PHASE.store(FULLREAD_PHASE_DONE, Ordering::SeqCst);
                OWN_STEPPER_PHASE.store(OWN_STEPPER_PHASE_DONE, Ordering::SeqCst);
            }
            return;
        }
        append_autoload_debug(format_args!(
            "product-core-autoload: STAGE2-MOUNT-COMMIT native Continue row guard pass ac0={ac0} expected={expected} c30=0x{c30:x} fp_real={fp_real}(level={fp_level} name_len={fp_name_len}) slot_identity=true owner+0x284={new_game_flag} b80={b80} -- native continue_confirm/SetState5 already fired"
        ));
        timeline_event("T_playgame", tick, format_args!("ac0={ac0} c30=0x{c30:x}"));
        FULLREAD_PHASE.store(FULLREAD_PHASE_DONE, Ordering::SeqCst);
        OWN_STEPPER_PHASE.store(OWN_STEPPER_PHASE_DONE, Ordering::SeqCst);
    }
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
    OWN_STEPPER_EXPECTED_SLOT.store(slot, Ordering::SeqCst);
    OWN_STEPPER_DESER_FIRED.store(OWN_STEPPER_DESER_NOT_FIRED, Ordering::SeqCst);
    OWN_STEPPER_MOUNT_C30.store(GAME_MAN_C30_UNSET, Ordering::SeqCst);
    OWN_STEPPER_IO_WAS_SET.store(OWN_STEPPER_IO_WAS_SET_NO, Ordering::SeqCst);
    OWN_STEPPER_DIALOG.store(null, Ordering::SeqCst);
    OWN_STEPPER_SELECTOR_STEP.store(null, Ordering::SeqCst);
    OWN_STEPPER_SELECTOR_CTX.store(null, Ordering::SeqCst);
    reset_phase_timer(&OWN_STEPPER_S2_PHASE_STARTED_MS);
    // Check the resolution before it becomes a function pointer. `game_data_addr` answers 0 when
    // the running build has no verified mapping for the RVA, and `mem.rs` says of it in as many
    // words: "never use this for a call target. Zero is a safe address to fail a read at and a
    // fatal one to jump to." This transmuted the result straight into a fn pointer and called it.
    //
    // It is not a live crash today -- MENU_MEMBER_FUNC_JOB_RUN_RVA (0x9aaba0) is mapped for 1.17
    // (-> 0x9abd40), so the address that arrives here is the right one. It is the contract that was
    // broken: nothing at this site established that, and the day the row leaves the map this jumps
    // to address 0.
    let run_addr = er_game_base::mem::game_data_addr(
        base,
        MENU_MEMBER_FUNC_JOB_RUN_RVA,
        "MENU_MEMBER_FUNC_JOB_RUN_RVA",
    );
    if run_addr == null {
        append_autoload_debug(format_args!(
            "product-core-autoload: native TitleTopDialog Load-Game run REFUSED for this build (MENU_MEMBER_FUNC_JOB_RUN_RVA has no verified mapping) node=0x{node:x} slot={slot} tick={tick} -- NOT firing; the one-shot is consumed because a refusal does not become a hit on a later tick"
        ));
        return;
    }
    let run: unsafe extern "system" fn(usize) =
        unsafe { std::mem::transmute::<usize, unsafe extern "system" fn(usize)>(run_addr) };
    append_autoload_debug(format_args!(
        "product-core-autoload: *** FIRING native TitleTopDialog Load-Game run 0x{run_addr:x}(rcx=node=0x{node:x}) vt=0x{node_vt:x} member_dialog=0x{member_dialog:x} member_fn=0x{member_fn:x} member_adjust=0x{member_adjust:x} window_item=0x{window_item:x} slot={slot} tick={tick} -- no direct_build/forged ctx ***"
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
// The DETERMINISTIC menu input probe driver (`menu_input_probe`) stood here: a per-frame
// Down->Confirm schedule injected at the native keystate bitmap, used as a measurement oracle
// for whether the d180 leaf-Update ticks on highlight alone. Its only caller was the
// `input_probe_enabled()` branch in product_core_own_stepper/fallback_drives.rs, and that gate
// has returned a literal `false` since it was written, so the probe never ran. Deleted with the
// branch rather than left as an orphan that reads like a live input path.
/// Observe-only native-load tick (native_load_enabled(), gated off by default). Runs each frame
/// instead of the own_stepper forcing logic, then the caller pass-throughs to OWN_STEPPER_ORIG_IDX10
/// so the native title machine advances untouched (the user drives past press-any-button + modals).
/// Keep vs the normal own_stepper: it does not SetState(owner,2/3), does not clear the beginlogo
/// gate, does not self-fire the registrar 0x1409b24e0, does not run direct_build / cold_char_mount.
/// It ONLY: (1) read-only checks whether the live TitleTopDialog menu/action is rendered and
/// semantically validated (TitleTopDialog vtable, [dialog+0xa48] registry, Load-Game
/// MenuMemberFuncJob node/action chain); (2) one-SHOT: fires that native run
/// MENU_MEMBER_FUNC_JOB_RUN_RVA (0x1409aaba0, rcx=node) -- which builds the live registered
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
            std::mem::transmute(
                match crate::experiments::gated_game_fn(
                    FACE_DATA_COPY_FROM_BUFFER_RVA,
                    "FACE_DATA_COPY_FROM_BUFFER_RVA",
                ) {
                    Some(address) => address,
                    None => return false,
                },
            );
        let copy_chr_asm: unsafe extern "system" fn(usize, usize) -> usize = std::mem::transmute(
            match crate::experiments::gated_game_fn(CHR_ASM_COPY_RVA, "CHR_ASM_COPY_RVA") {
                Some(address) => address,
                None => return false,
            },
        );
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
