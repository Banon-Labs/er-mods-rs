use super::*;

/// Restore `GLOBAL_CSGaitem` to constructor-pristine (empty gaitemInsTable + full free-queue) at a
/// clean title BEFORE the switch reload's fresh deserialize, so char#2's deserialize does not
/// exhaust the free-queue on char#1's leaked items (the AV at live 0x67141a, bd
/// system-quit-postswitch-crash-gaitem-freequeue-exhaustion-2026-07-02). Mechanism: sweep all
/// 0x1400 gaitemInsTable slots; for each occupied slot call the NATIVE per-item release
/// RemoveCSGaitemIns(gaitem, &entries[i].unindexedGaItemHandle) -- it destructs+deallocates the ins
/// (no leak) and returns index i to freeTableIdxQueue. This is the exact primitive the native
/// world/inventory teardown uses; we drive it because our lightweight return-title chain skips it.
///
/// SAVE-SAFETY / correctness preconditions (the CALLER must guarantee, and this fn re-checks what it
/// can): the old world is torn down (local player absent) so nothing live holds POINTERS to these
/// ins objects -- PlayerGameData/inventory hold only integer handles, which char#2's deserialize
/// overwrites. Structural validation (heap-aligned singleton, head/end within [0,0x1400)) fails
/// closed rather than sweeping a bogus pointer. Returns Some((released, slack_before, slack_after))
/// on success (slack = 0x13ff - free_count; healthy = slack_after 0), None if it declined.
pub(crate) unsafe fn own_load_reset_gaitem_singleton(base: usize) -> Option<(u32, u32, u32)> {
    const NULL: usize = TITLE_OWNER_SCAN_START_ADDRESS;
    const RING_USABLE: u32 = (CSGAITEM_TABLE_CAPACITY as u32) - 1; // 0x13ff (one sentinel slot)
    let gaitem = unsafe { safe_read_usize(base + GLOBAL_CSGAITEM_SINGLETON_RVA) }.unwrap_or(NULL);
    if gaitem == NULL || !unsafe { is_heap_aligned_ptr(gaitem) } {
        append_autoload_debug(format_args!(
            "gaitem-reset: GLOBAL_CSGaitem not resident/aligned (0x{gaitem:x}) -- declining pristine-restore (no-op)"
        ));
        return None;
    }
    let free_count = |head: u32, end: u32| -> u32 {
        // Ring distance head..end over capacity 0x1400 = number of poppable free indices.
        end.wrapping_sub(head)
            .wrapping_add(CSGAITEM_TABLE_CAPACITY as u32)
            % (CSGAITEM_TABLE_CAPACITY as u32)
    };
    let head0 =
        unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_HEAD_OFFSET) }.unwrap_or(-1) as u32;
    let end0 =
        unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_END_OFFSET) }.unwrap_or(-1) as u32;
    if head0 as usize >= CSGAITEM_TABLE_CAPACITY || end0 as usize >= CSGAITEM_TABLE_CAPACITY {
        append_autoload_debug(format_args!(
            "gaitem-reset: free-queue head/end out of range (head=0x{head0:x} end=0x{end0:x} cap=0x{:x}) -- singleton not the expected CSGaitemImp; declining (no-op)",
            CSGAITEM_TABLE_CAPACITY
        ));
        return None;
    }
    let slack_before = RING_USABLE.saturating_sub(free_count(head0, end0));
    let remove_ins: unsafe extern "system" fn(usize, usize) =
        unsafe { std::mem::transmute(base + CSGAITEM_REMOVE_INS_RVA) };
    let mut released: u32 = 0;
    for i in 0..CSGAITEM_TABLE_CAPACITY {
        let slot = gaitem + CSGAITEM_INS_TABLE_OFFSET + i * core::mem::size_of::<usize>();
        let ins = unsafe { safe_read_usize(slot) }.unwrap_or(NULL);
        if ins == NULL {
            continue;
        }
        // &entries[i].unindexedGaItemHandle -- its embedded index maps back to slot i (ctor seeds it,
        // alloc preserves it), so RemoveCSGaitemIns frees gaitemInsTable[i] and returns index i.
        let handle_ptr = gaitem + CSGAITEM_ENTRIES_OFFSET + i * CSGAITEM_ENTRY_STRIDE;
        unsafe { remove_ins(gaitem, handle_ptr) };
        released += 1;
    }
    let head1 =
        unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_HEAD_OFFSET) }.unwrap_or(-1) as u32;
    let end1 =
        unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_END_OFFSET) }.unwrap_or(-1) as u32;
    let slack_after = RING_USABLE.saturating_sub(free_count(head1, end1));
    SYSTEM_QUIT_GAITEM_RESET_INVOCATIONS.fetch_add(1, Ordering::SeqCst);
    SYSTEM_QUIT_GAITEM_RESET_RELEASED_COUNT.fetch_add(released as usize, Ordering::SeqCst);
    SYSTEM_QUIT_GAITEM_RESET_LAST_SLACK_BEFORE.store(slack_before as usize, Ordering::SeqCst);
    SYSTEM_QUIT_GAITEM_RESET_LAST_SLACK_AFTER.store(slack_after as usize, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "gaitem-reset: pristine-restore gaitem=0x{gaitem:x} released={released} free-queue head/end 0x{head0:x}/0x{end0:x} -> 0x{head1:x}/0x{end1:x} slack {slack_before}->{slack_after} (0=full); native RemoveCSGaitemIns 0x{:x} per occupied slot",
        base + CSGAITEM_REMOVE_INS_RVA
    ));
    Some((released, slack_before, slack_after))
}

/// THE PRECONDITION OF `deser(slot)` = `0x14067b290`, READ OUT OF LIVE RAM BEFORE THE CALL.
///
/// Returns `None` when the native slot deserialize may be called, or `Some(reason)` naming the
/// single check that failed. The caller MUST refuse to call `0x67b290` on `Some` -- every listed
/// failure ends the process rather than returning an error, so there is nothing to recover from
/// afterwards.
///
/// WHY THIS EXISTS -- the crash it is derived from, proven by artifact, not inferred. A boot-title
/// `deser(slot)` killed the game ~13s in and the DLL's own VEH wrote the frames
/// (`er-effects-crash-log.txt`, 2026-08-25 17:03:10):
///
/// ```text
/// access-violation rva=0x67141a  rcx=0x142a7e430[vt=0x140670f90]
/// callers=[#4=0x14067141a, #5=0x140260035, #6=0x140256c32, #7=0x14067be5c]
/// ```
///
/// which resolves against the 1.16.2 dump (shift 0) to exactly one chain:
/// `0x67b290 deser` -> `+0x67b30e call 0x67bd70` (ret `0x67b313`) -> `+0x67be5c call 0x256be0` ->
/// `+0x256c32 call PlayerGameData::Deserialize 0x25ffc0` -> `+0x260035 call
/// CSGaitemImp::Deserialize 0x671130` -> fault at `0x67141a`.
///
/// `0x67141a` is `CALL qword ptr [RAX + 0x30]`, two instructions after
/// `0x67140f MOV RCX,[RDI + RAX*0x8 + 0x8]` -- i.e. `gaitemInsTable[uVar2]`. The dump proves the
/// index: `rcx` came back `0x142a7e430`, which is `.rdata`, and its first qword `0x140670f90` is
/// code in the CSGaitem region -- that is the CSGaitemImp OBJECT'S OWN VTABLE, which is what
/// `[RDI + (-1)*8 + 8]` == `[RDI]` reads. So `uVar2 == -1`, the documented `gaitemInsTable[-1]`.
///
/// And `-1` has exactly one source. `CSGaitemImp::Deserialize` allocates a fresh handle per save
/// entry through `GetGaItemHandle{Weapon,Protector,Accessory,Goods,Gem}`, each of which calls
/// `GetUnindexedGaItemHandle` (0x672440), whose whole body is:
///
/// ```text
/// *out = 0;
/// if (head != end) { pop; *out = <real handle>; }   // ONLY when the free queue is non-empty
/// return out;                                        // head == end  ->  handle stays 0
/// ```
///
/// A zero handle fails `IsIndexedGaitemHandle`, the index stays `0xffffffff`, and the very next
/// statement dispatches through `gaitemInsTable[-1]`. **The precondition is therefore: the
/// CSGaitemImp free-index queue must be FULL before the deserialize runs**, because the loop is
/// `0x1400` iterations wide and may need one index per iteration.
///
/// The native game satisfies this for free and never had to think about it. `0x67b290` has exactly
/// ONE caller in the image -- `CS::MoveMapStep::DoSaveStuff` (0x140afbad0), reached only from
/// `CS::MoveMapStep::Update` (0x140aff640), whose body `DLPanic`s on `GLOBAL_DmgMan`,
/// `GLOBAL_CSMenuMan`, `GLOBAL_CSSessionManager`, `GLOBAL_CSFile` and three more singletons. It is
/// the IN-WORLD step, and by the time it runs the previous character's gaitem instances have been
/// released back to the queue by the world/session teardown. Our chain calls the same function from
/// the TITLE task, where the boot default character's instances are still resident, so the queue
/// runs dry part-way through the save's entries and the next one indexes `[-1]`.
///
/// The buffer is not the suspect: `0x67bd70` validates the 0x10-byte header version via
/// `FUN_1402624c0` and bails BEFORE `0x256be0` when it fails, so reaching frame `#6` at all proves
/// the resident buffer carried a structurally valid save.
///
/// The three latch checks are a second, independent death mode on the same call -- see
/// `CSGAITEM_DESERIALIZE_SCRATCH_OFFSET` -- and cost three reads.
pub(crate) unsafe fn gaitem_deserialize_blocker(base: usize) -> Option<&'static str> {
    const NULL: usize = TITLE_OWNER_SCAN_START_ADDRESS;
    const RING_USABLE: u32 = (CSGAITEM_TABLE_CAPACITY as u32) - 1; // 0x13ff (one sentinel slot)
    let gaitem = unsafe { safe_read_usize(base + GLOBAL_CSGAITEM_SINGLETON_RVA) }.unwrap_or(NULL);
    if gaitem == NULL || !unsafe { is_heap_aligned_ptr(gaitem) } {
        append_autoload_debug(format_args!(
            "gaitem-precondition: BLOCK -- GLOBAL_CSGaitem not resident/aligned (0x{gaitem:x}); the deserialize would run against an unknown object"
        ));
        return Some("singleton-unresolvable");
    }
    let scratch =
        unsafe { safe_read_usize(gaitem + CSGAITEM_DESERIALIZE_SCRATCH_OFFSET) }.unwrap_or(1);
    let serializing =
        unsafe { safe_read_u8(gaitem + CSGAITEM_IS_BEING_SERIALIZED_OFFSET) }.unwrap_or(1);
    let deserializing =
        unsafe { safe_read_u8(gaitem + CSGAITEM_IS_BEING_DESERIALIZED_OFFSET) }.unwrap_or(1);
    let head = unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_HEAD_OFFSET) }.unwrap_or(-1);
    let end = unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_END_OFFSET) }.unwrap_or(-1);
    if head < 0
        || end < 0
        || head as usize >= CSGAITEM_TABLE_CAPACITY
        || end as usize >= CSGAITEM_TABLE_CAPACITY
    {
        append_autoload_debug(format_args!(
            "gaitem-precondition: BLOCK -- free-queue head/end out of range (head={head} end={end} cap=0x{:x}); 0x{gaitem:x} is not the expected CSGaitemImp",
            CSGAITEM_TABLE_CAPACITY
        ));
        return Some("free-queue-out-of-range");
    }
    // Ring distance head..end over capacity 0x1400 = the number of poppable free indices.
    let free = (end as u32)
        .wrapping_sub(head as u32)
        .wrapping_add(CSGAITEM_TABLE_CAPACITY as u32)
        % (CSGAITEM_TABLE_CAPACITY as u32);
    let slack = RING_USABLE.saturating_sub(free);
    let blocker = if scratch != 0 {
        Some("deserialize-scratch-latched")
    } else if serializing != 0 {
        Some("is-being-serialized")
    } else if deserializing != 0 {
        Some("is-being-deserialized")
    } else if slack != 0 {
        Some("free-queue-not-full")
    } else {
        None
    };
    match blocker {
        Some(reason) => append_autoload_debug(format_args!(
            "gaitem-precondition: BLOCK ({reason}) gaitem=0x{gaitem:x} scratch=0x{scratch:x} serializing={serializing} deserializing={deserializing} free-queue head/end {head}/{end} free={free}/{RING_USABLE} slack={slack} -- native deser 0x67b290 would die (slack>0 -> gaitemInsTable[-1] AV at 0x67141a; a latch -> DLPanic in CSGaitem.cpp)"
        )),
        None => append_autoload_debug(format_args!(
            "gaitem-precondition: OK gaitem=0x{gaitem:x} free-queue head/end {head}/{end} free={free}/{RING_USABLE} slack=0 (full) scratch=0 latches clear -- native deser 0x67b290 may run"
        )),
    }
    blocker
}

/// SYNCHRONOUS fresh picked-slot feed-deserialize for the System->Quit->Load-Profile switch (the
/// continue_confirm hook calls this BEFORE forwarding, so the c30/PGD the confirm streams belong to
/// the PICKED slot -- bd system-quit-cleantitle-load-is-stale-restream-not-slot-source-2026-07-02).
/// Same proven mechanism as `own_load_drive` steps 1-4: read the on-disk save (native SAVE-DIR
/// builder path -- post-first-load the redirect has reverted, so this is the file the quit-save
/// just wrote), slice slot `want_slot`'s plaintext body, arm the gated 0x67b100 read detour, call
/// the native parser 0x67b290(slot) in-process. Returns true only when the parse produced a real
/// c30 + a real PlayerGameData fingerprint. Save-safe: read-only on the .sl2 (no SetState5, no
/// save write; the deserialize also repoints GameMan+0xac0 to `want_slot` as its normal byproduct).
pub(crate) unsafe fn own_load_feed_deserialize(base: usize, gm: usize, want_slot: i32) -> bool {
    const C30_ZERO: i32 = 0;
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    if gm == null || want_slot < OWN_STEPPER_SLOT_ZERO {
        append_autoload_debug(format_args!(
            "own-load-feed: rejected gm=0x{gm:x} slot={want_slot} -- need GameMan + explicit slot (no-write)"
        ));
        return false;
    }
    if own_load_save_rejection_terminal() {
        append_autoload_debug(format_args!(
            "own-load-feed: terminal save rejection already published (fingerprint=0x{:016x}) -- switch remains fail-closed without a resolver retry",
            own_load_save_rejection_fingerprint()
        ));
        return false;
    }
    let Some(sl2_bytes) = (unsafe { own_load_read_sl2_bytes(base) }) else {
        return false;
    };
    let body: &[u8] = match er_save_loader::bnd4::slot_body(&sl2_bytes, want_slot as usize) {
        Ok(b) => b,
        Err(e) => {
            append_autoload_debug(format_args!(
                "own-load-feed: slot_body(slot={want_slot}) failed: {e:?} -- ABORT (no-write)"
            ));
            return false;
        }
    };
    // Leak the sliced body so it stays valid for the detour to memcpy (one bounded copy per switch).
    let leaked: &'static [u8] = Box::leak(body.to_vec().into_boxed_slice());
    OWN_LOAD_BODY_PTR.store(leaked.as_ptr() as usize, Ordering::SeqCst);
    OWN_LOAD_BODY_LEN.store(leaked.len(), Ordering::SeqCst);
    if !install_own_load_hook() {
        append_autoload_debug(format_args!(
            "own-load-feed: hook install failed -- ABORT (no-write)"
        ));
        return false;
    }
    let c30_before =
        unsafe { safe_read_i32(gm + GAME_MAN_SAVED_MAP_C30_OFFSET) }.unwrap_or(GAME_MAN_C30_UNSET);
    OWN_LOAD_GATE.store(true, Ordering::SeqCst);
    let parser: unsafe extern "system" fn(i32) -> i32 =
        unsafe { std::mem::transmute(base + DESERIALIZE_SLOT_RVA) };
    let pret = unsafe { parser(want_slot) };
    OWN_LOAD_GATE.store(false, Ordering::SeqCst);
    let fed = OWN_LOAD_FED_BYTES.load(Ordering::SeqCst);
    let c30 =
        unsafe { safe_read_i32(gm + GAME_MAN_SAVED_MAP_C30_OFFSET) }.unwrap_or(GAME_MAN_C30_UNSET);
    let ac0 = unsafe { safe_read_i32(gm + FORCE_PLAY_GAME_GM_SLOT_AC0_OFFSET) }
        .unwrap_or(OWN_STEPPER_SLOT_NONE);
    let (fp_real, fp_level, fp_name_len) = unsafe { char_fingerprint(base) };
    let c30_real = c30 != GAME_MAN_C30_UNSET && c30 != C30_ZERO && c30 != FULLREAD_C30_M10_DEFAULT;
    let ok = c30_real && fp_real;
    if ok {
        OWN_STEPPER_MOUNT_C30.store(c30, Ordering::SeqCst);
        OWN_STEPPER_DESER_FIRED.store(OWN_STEPPER_DESER_FIRED_OK, Ordering::SeqCst);
    }
    append_autoload_debug(format_args!(
        "own-load-feed: parser 0x{:x}(slot={want_slot}) ret={pret} fed_bytes=0x{fed:x} c30 0x{c30_before:x}->0x{c30:x} c30_real={c30_real} ac0={ac0} fp_real={fp_real}(level={fp_level} name_len={fp_name_len}) ok={ok} (read-only deserialize; NO SetState5, NO save write)",
        base + DESERIALIZE_SLOT_RVA
    ));
    ok
}

// FD4-IO residency for the menu-free switch reload (bd er-effects-rs-9fmm, 2026-07-19) is now DEFAULT
// behavior in own_load_switch_reload_fire (the boot native-fullread SUBMIT -> DRAIN(b80==RESIDENT) ->
// COMMIT sequence), replacing the old resource-less one-shot. No marker/env gate.

pub(crate) use er_telemetry_core::counters::SWITCH_RELOAD_FD4IO_COMMITTED;
pub(crate) use er_telemetry_core::counters::SWITCH_RELOAD_FD4IO_DRAIN_WAITS;
/// Phase machine state for the reload FD4-IO SUBMIT/DRAIN (own_load_switch_reload_fire), persisted
/// across the caller's per-frame retries. 0=IDLE (do SUBMIT once), 1=DRAIN (tick until b80==3),
/// 2=COMMIT (fall through to feed+continue_confirm).
pub(crate) use er_telemetry_core::counters::SWITCH_RELOAD_FD4IO_PHASE;
// The phase VALUES moved next to the atomic in er-telemetry-core (2026-07-31, bd er-effects-rs-9jbe):
// er-title-flow's b78 guard now reads this phase to detect that fd4io owns GameMan+0xb78, and that
// crate must not depend on the root crate. This file remains the only WRITER of the phase machine.
pub(crate) use er_telemetry_core::counters::SWITCH_RELOAD_FD4IO_COMMIT;
pub(crate) use er_telemetry_core::counters::SWITCH_RELOAD_FD4IO_DRAIN;
pub(crate) use er_telemetry_core::counters::SWITCH_RELOAD_FD4IO_IDLE;
/// Bound the reload drain far below the boot's FULLREAD_DRAIN_MAX (1200): the b80 2->3 save-file read
/// residency is fast (~17 ticks at boot); if it does not resident within this many frames the read is
/// not draining at the clean-title timing -> fall through to COMMIT without residency (fail-soft to the
/// old behavior) rather than hang the switch.
const SWITCH_RELOAD_FD4IO_DRAIN_MAX: usize = 600;

/// PHASE-3 (bd PHASE3-render-release-is-CommonFinalize): max frames `own_load_switch_reload_fire` holds the
/// reload's continue_confirm waiting for the OUTGOING world's `_Common_Finalize`. In the success path the
/// outgoing world is released in-world (before the title owner even appears -- the scoped menuData+0x5d
/// ending-drive walks its MoveMapStep 18->19->20) so this wait is ~0. The bound only matters when the
/// native teardown never completes -> fail-soft to the OLD in-place reload (the two holds re-engage), so a
/// stalled outgoing teardown can never softlock the switch. ~15s at 60fps, well under the runtime cap.
const OUTGOING_TEARDOWN_WAIT_MAX: usize = 900;

/// Reset the switch-reload FD4-IO phase machine so a NEW switch re-runs SUBMIT -> DRAIN -> COMMIT.
/// Without this the one-shot stays claimed after the FIRST switch (PHASE stuck at COMMIT +
/// SWITCH_RELOAD_FD4IO_COMMITTED=1), so the SECOND switch's own_load_switch_reload_fire hits the
/// already-committed guard and returns immediately WITHOUT loading -> the 2nd reload (load3) never
/// initiates and the game sits at a clean/PRESS-ANY-BUTTON title (run 110005: switch #1 loaded load2
/// via SUBMIT/DRAIN/COMMIT; switch #2 armed + tore the world down but emitted NO reload-fd4io SUBMIT,
/// so load3 stalled at bar step 1). switch_slot_arm_programmatic calls this on every switch arm so each
/// switch gets a fresh phase machine.
pub(crate) fn reset_switch_reload_fd4io_phase() {
    SWITCH_RELOAD_FD4IO_PHASE.store(SWITCH_RELOAD_FD4IO_IDLE, Ordering::SeqCst);
    SWITCH_RELOAD_FD4IO_COMMITTED.store(0, Ordering::SeqCst);
    SWITCH_RELOAD_FD4IO_DRAIN_WAITS.store(0, Ordering::SeqCst);
}

/// Full per-switch latch reset for an ARMED switch reload: the FD4-IO phase machine AND the Phase-3
/// outgoing-world teardown latches (baseline snapshot + DONE/WAIT_TICKS/FAILSOFT). BOTH arm paths --
/// the programmatic `switch_slot_arm_programmatic` (agent/control-file drive) AND the USER ProfileSelect
/// `system_quit_arm_quickload_autoload` -- MUST call this or the two drift. That drift was the load3
/// softlock: the user path reset only FRESH_DESER_DONE/MENU_FREE_RELOAD_FIRED, leaving
/// `SWITCH_RELOAD_FD4IO_COMMITTED=1` stale from load2, so a user-driven load3 hit the already-committed
/// guard in `own_load_switch_reload_fire`, emitted NO SUBMIT, left FRESH_DESER_DONE=0, and the b78 guard
/// wrote GameMan requestedSaveSlotLoad=-1 every frame -> native pump gate false -> world torn down at
/// ENTERING WORLD (bd compounding-reload-two-roots-...-chainB-stale-fd4io-latch-b78-2026-07-23).
pub(crate) fn reset_switch_reload_latches() {
    reset_switch_reload_fd4io_phase();
    // Snapshot the finalize baseline for THIS switch + clear the per-switch teardown latches, so the reload
    // gate detects the OUTGOING world's `_Common_Finalize` (COMMON_FINALIZE_CALLS crossing the baseline).
    er_telemetry_core::counters::OUTGOING_TEARDOWN_BASELINE.store(
        er_telemetry_core::counters::COMMON_FINALIZE_CALLS.load(Ordering::SeqCst),
        Ordering::SeqCst,
    );
    er_telemetry_core::counters::OUTGOING_TEARDOWN_DONE.store(0, Ordering::SeqCst);
    er_telemetry_core::counters::OUTGOING_TEARDOWN_WAIT_TICKS.store(0, Ordering::SeqCst);
    er_telemetry_core::counters::OUTGOING_TEARDOWN_FAILSOFT.store(0, Ordering::SeqCst);
    // Per-switch WorldResWait defer-release hold latches: clear ARMED + residency/hold state so each
    // switch gets a fresh hold and a stale ARMED can never leak into a later load (bd reload-overlap-fix-
    // design-worldreswait-defer-release-on-streaming-settle-2026-07-24).
    reset_worldreswait_hold_latches();
}

/// SUBMIT the native full-save-read for `picked` so the FD4 IO worker pool loads it resident, exactly
/// as the boot native-fullread SUBMIT phase (slot_resolution.rs). Mirrors its calls/RVAs. Sets
/// GameMan+0xb80=2 (the deserialize arm); the DRAIN tick then advances it to RESIDENT(3).
unsafe fn own_load_fd4io_submit(base: usize, gm: usize, picked: i32) {
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    // Mark the slot occupied so the native save-load gate accepts it (idempotent, no other effect).
    let gdm = game_data_man_ptr_or_null();
    let summary = if gdm != null {
        unsafe { safe_read_usize(gdm + SLOT_MANAGER_CONTAINER_OFFSET) }.unwrap_or(null)
    } else {
        null
    };
    if summary != null {
        let mark: unsafe extern "system" fn(usize, i32) -> u8 =
            unsafe { std::mem::transmute(base + PROFILE_MARK_SLOT_USED_RVA) };
        let _ = unsafe { mark(summary, picked) };
    }
    // Resolve OUR slot + submit the full read (type-0xa; sets b80=2).
    unsafe { *((gm + GAME_MAN_SLOT_SELECT_B78_OFFSET) as *mut i32) = picked };
    let set_save_slot: unsafe extern "system" fn(i32) =
        unsafe { std::mem::transmute(base + FORCE_PLAY_GAME_SET_SAVE_SLOT_RVA) };
    unsafe { set_save_slot(picked) };
    let submit: unsafe extern "system" fn(i32) -> i32 =
        unsafe { std::mem::transmute(base + B80_FULL_LOAD_INITIATOR_RVA) };
    let sret = unsafe { submit(picked) };
    let b80 = unsafe { safe_read_i32(gm + GAME_MAN_LOAD_IN_PROGRESS_B80_OFFSET) }.unwrap_or(-1);
    append_autoload_debug(format_args!(
        "reload-fd4io: SUBMIT slot={picked} submit 0x{:x} ret={sret} b80={b80} -> DRAIN (replicating boot native-fullread residency before feed+continue_confirm)",
        base + B80_FULL_LOAD_INITIATOR_RVA
    ));
}

/// One DRAIN tick: pump the b80 IO lane + poll (exact boot native-fullread calls) and return the
/// current GameMan+0xb80 so the caller can detect RESIDENT(3).
unsafe fn own_load_fd4io_drain_tick(base: usize, gm: usize) -> i32 {
    let lane: unsafe extern "system" fn() -> i32 =
        unsafe { std::mem::transmute(base + B80_LANE1_DRIVER_RVA) };
    let _ = unsafe { lane() };
    let poll: unsafe extern "system" fn(u8, u8) -> i32 =
        unsafe { std::mem::transmute(base + B80_POLL_RVA) };
    let _ = unsafe { poll(FULLREAD_POLL_ARG, FULLREAD_POLL_ARG) };
    unsafe { safe_read_i32(gm + GAME_MAN_LOAD_IN_PROGRESS_B80_OFFSET) }.unwrap_or(-1)
}

/// MENU-FREE clean-title reload of the PICKED slot for a genuine System->Quit->Load-Profile switch.
/// The warm-rebuilt TitleTopDialog never reaches Loop post-return-title (press-start SceneObjProxy at
/// dialog+0xb78 unbound), so the title accept-byte/open-menu path deadlocks; native_fullread_tick also
/// stands down for a switch. Drive the picked slot through the same native-ownership commit the boot
/// autoload uses, exactly like the (now-dead) native-fullread DESER switch_feed_case
/// (slot_resolution.rs:275-296): reset the gaitem singleton -> feed the picked slot's on-disk bytes
/// through the native parser (real c30 + PGD) -> latch FRESH_DESER_DONE -> native continue_confirm
/// (intercepted by system_quit_continue_confirm_hook -> SetState5 streams the world + performs the
/// switch cleanup). ONE-SHOT per switch (SYSTEM_QUIT_SWITCH_MENU_FREE_RELOAD_FIRED). Returns true only
/// when it fired continue_confirm; false = "not yet / could not" (nothing consumed unless the one-shot
/// was legitimately claimed) and the caller keeps waiting. Caller MUST have proven the old world is
/// torn down (player absent) so the gaitem reset + deserialize never touch a live world.
/// See bd live-switch-teardown-fixed-now-menu-open-stall-2026-07-18 + the RE workflow.
pub(crate) unsafe fn own_load_switch_reload_fire(
    base: usize,
    gm: usize,
    owner: usize,
    picked: i32,
    n: u64,
) -> bool {
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    // (a) Validate the title owner FIRST -- it flickers during the warm rebuild. No state consumed:
    // a bad-owner frame returns false and the caller retries next frame. Must be a live owner with the
    // new-game flag clear (continue_confirm's LOAD branch; nonzero = NewGame path / mid-rebuild).
    if owner == null {
        return false;
    }
    let new_game_flag =
        match unsafe { safe_read_usize(owner + TITLE_OWNER_NEW_GAME_FLAG_284_OFFSET) } {
            Some(v) => v as u8,
            None => return false,
        };
    if new_game_flag != FULLREAD_OWNER_NEW_GAME_OK {
        return false;
    }
    // (a.5) PHASE-3 OUTGOING-WORLD TEARDOWN GATE (bd PHASE3-render-release-is-CommonFinalize). Hold the
    // reload's continue_confirm until the OUTGOING (pre-quit) world's native render-release has run
    // (COMMON_FINALIZE_CALLS crossed the per-switch baseline captured at arm), so the reload rebuilds a
    // FRESH world instead of loading in-place over the still-live WorldChrMan/CSDistViewManager/
    // g_GxDrawContext (the ~5x-heavier-render / 5-vblank bug). The outgoing world is driven to
    // _Common_Finalize in-world by the scoped menuData+0x5d ending-drive (title_tick_cover), which runs
    // BEFORE the title owner appears, so in the success path the finalize is already observed the first
    // time we reach here. Bounded + fail-soft: on timeout, latch FAILSOFT (the two in-place holds
    // re-engage via outgoing_teardown_suppresses_holds) and fall through to the OLD in-place reload, so a
    // stalled teardown can never softlock. Once DONE/FAILSOFT latches, this gate is skipped for the switch.
    if crate::experiments::gating::outgoing_teardown_enabled()
        && crate::experiments::gating::switch_reload_active()
        && er_telemetry_core::counters::OUTGOING_TEARDOWN_DONE.load(Ordering::SeqCst) == 0
        && er_telemetry_core::counters::OUTGOING_TEARDOWN_FAILSOFT.load(Ordering::SeqCst) == 0
    {
        let baseline =
            er_telemetry_core::counters::OUTGOING_TEARDOWN_BASELINE.load(Ordering::SeqCst);
        let calls = er_telemetry_core::counters::COMMON_FINALIZE_CALLS.load(Ordering::SeqCst);
        if calls > baseline {
            er_telemetry_core::counters::OUTGOING_TEARDOWN_DONE.store(1, Ordering::SeqCst);
            append_autoload_debug(format_args!(
                "outgoing-teardown: OBSERVED _Common_Finalize (calls={calls} > baseline={baseline}) -- OUTGOING world released; reload rebuilds FRESH (in-place holds stay disabled) (#{n})"
            ));
        } else {
            let waited = er_telemetry_core::counters::OUTGOING_TEARDOWN_WAIT_TICKS
                .fetch_add(1, Ordering::SeqCst)
                + 1;
            if waited >= OUTGOING_TEARDOWN_WAIT_MAX {
                er_telemetry_core::counters::OUTGOING_TEARDOWN_FAILSOFT.store(1, Ordering::SeqCst);
                append_autoload_debug(format_args!(
                    "outgoing-teardown: FAIL-SOFT after {waited} frames without _Common_Finalize (calls={calls} baseline={baseline}) -- falling back to OLD in-place reload; the two holds re-engage (#{n})"
                ));
                // fall through this frame to the normal reload path (old behavior; no softlock)
            } else {
                if waited == 1 || waited.is_multiple_of(120) {
                    append_autoload_debug(format_args!(
                        "outgoing-teardown: waiting for OUTGOING _Common_Finalize (calls={calls} baseline={baseline} waited={waited}/{OUTGOING_TEARDOWN_WAIT_MAX}) -- holding continue_confirm so the reload rebuilds fresh (#{n})"
                    ));
                }
                return false;
            }
        }
    }
    // (b) FD4-IO residency phase machine (DEFAULT behavior -- no marker/env toggle; bd er-effects-rs-9fmm):
    // SUBMIT the full read, DRAIN until GameMan+0xb80==RESIDENT(3), THEN fall through to
    // feed+continue_confirm -- so the reload's streamed world has the resources natively resident (the
    // boot path's behavior) instead of entering resource-less and reverting to title. Owner is already
    // validated, so a flickering frame never burns the one-shot (claimed by SWITCH_RELOAD_FD4IO_COMMITTED).
    {
        let phase = SWITCH_RELOAD_FD4IO_PHASE.load(Ordering::SeqCst);
        if phase == SWITCH_RELOAD_FD4IO_IDLE {
            if SWITCH_RELOAD_FD4IO_PHASE
                .compare_exchange(
                    SWITCH_RELOAD_FD4IO_IDLE,
                    SWITCH_RELOAD_FD4IO_DRAIN,
                    Ordering::SeqCst,
                    Ordering::SeqCst,
                )
                .is_ok()
            {
                SWITCH_RELOAD_FD4IO_DRAIN_WAITS.store(0, Ordering::SeqCst);
                unsafe { own_load_fd4io_submit(base, gm, picked) };
            }
            return false;
        }
        if phase == SWITCH_RELOAD_FD4IO_DRAIN {
            let b80 = unsafe { own_load_fd4io_drain_tick(base, gm) };
            let w = SWITCH_RELOAD_FD4IO_DRAIN_WAITS.fetch_add(1, Ordering::SeqCst);
            let resident = b80 == FULLREAD_B80_RESIDENT;
            if resident || w >= SWITCH_RELOAD_FD4IO_DRAIN_MAX {
                // Do NOT disarm b78 here. Unlike the boot native-fullread (which disarms on commit),
                // the System->Quit SWITCH path MUST keep GameMan+0xb78 armed (= picked slot) through
                // SetState5/MoveMap finalize: it is the warp target that MoveMapStep finalize case 8
                // consumes to warp the character and autoclear warpRequested before advancing mms18
                // (system_quit_repro_guards.rs:1720-1754). Clearing it early leaves the load with no
                // warp target -> warp_requested stuck at 1 and STEP_MoveMap self-loops at 18 (observed
                // in the b78-disarm build: world resident, real char, but mms18 next=18/done50=0
                // warp=1 forever).
                // WHO ACTUALLY CLEARS b78 (corrected 2026-08-01, bd er-effects-rs-0nie): NOT the
                // continue_confirm hook -- that stopped writing OWN_STEPPER_SLOT_NONE at 1a0ad8e4.
                // The remaining clearer on this path is system_quit_inworld_load_skip_hook
                // (system_quit_repro_guards.rs), which hooks SYSTEM_QUIT_INWORLD_LOAD_RVA 0x67b290 --
                // the SAME address as DESERIALIZE_SLOT_RVA that own_load_feed_deserialize calls
                // directly, so despite the "inworld" name it runs on this COMMIT feed. The other
                // writer, er-title-flow's b78 guard, cannot reach here: the line below sets
                // FRESH_DESER_DONE=1, which is one of that guard's window conditions, so its window
                // closes the moment COMMIT begins.
                append_autoload_debug(format_args!(
                    "reload-fd4io: DRAIN done b80={b80} waits={w} resident={resident}{} -> COMMIT (feed+continue_confirm); b78 kept armed (warp target) through finalize",
                    if resident {
                        ""
                    } else {
                        " (TIMEOUT -- committing without residency, fail-soft to old behavior)"
                    }
                ));
                SWITCH_RELOAD_FD4IO_PHASE.store(SWITCH_RELOAD_FD4IO_COMMIT, Ordering::SeqCst);
                // fall through to feed+continue_confirm this frame
            } else {
                return false; // keep draining
            }
        }
        // phase == COMMIT (reached this frame or a prior one): commit exactly once.
        if SWITCH_RELOAD_FD4IO_COMMITTED
            .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return false;
        }
    }
    // (c) Defuse the CSGaitemImp free-queue exhaustion AV (live 0x67141a): char#1's leaked gaitem
    // entries still populate the gaitem singleton at the clean title (the lightweight return-title
    // chain skips the native inventory teardown). Safe now because the old world is torn down.
    let _ = unsafe { own_load_reset_gaitem_singleton(base) };
    // (d) Feed the picked slot's on-disk bytes through the native parser -> GameMan+0xc30 becomes the
    // picked character's REAL map + a real PGD fingerprint. No FD4 IO SUBMIT/DRAIN, no b80==3 needed.
    if !unsafe { own_load_feed_deserialize(base, gm, picked) } {
        append_autoload_debug(format_args!(
            "own-load-switch-reload: feed-deserialize of picked slot {picked} FAILED -- NOT firing continue_confirm; switch fails closed (one-shot claimed, no re-attempt)"
        ));
        return false;
    }
    // (e) Latch native_slot_proven BEFORE firing: the continue_confirm hook reads FRESH_DESER_DONE==1
    // to take the forward->SetState5 path (not the no-proof forward) and to prevent any double-feed.
    SYSTEM_QUIT_CONTINUE_CONFIRM_FRESH_DESER_DONE.store(1, Ordering::SeqCst);
    // Record WHICH slot's deserialize completed (slot+1). The published-vs-loaded portrait oracle
    // compares against this, not `GameMan.save_slot` -- ac0 is written by our own `set_save_slot`
    // above and by the game's own selector, so it does not answer "which character loaded".
    er_telemetry_core::counters::SYSTEM_QUIT_FRESH_DESER_DONE_SLOT
        .store((picked + 1) as usize, Ordering::SeqCst);
    // (f) Re-read the freshly-mounted c30 + fingerprint and fire the GUARDED native continue_confirm
    // (own_load_continue_fire re-guards c30_real && fp_real && owner+0x284==0 internally -- the only
    // save-writing SetState5 is behind that hard guard).
    let c30 =
        unsafe { safe_read_i32(gm + GAME_MAN_SAVED_MAP_C30_OFFSET) }.unwrap_or(GAME_MAN_C30_UNSET);
    let c30_real = c30 != GAME_MAN_C30_UNSET && c30 != 0 && c30 != FULLREAD_C30_M10_DEFAULT;
    let (fp_real, fp_level, _nl) = unsafe { char_fingerprint(base) };
    append_autoload_debug(format_args!(
        "own-load-switch-reload: picked slot {picked} mounted (c30=0x{c30:x} c30_real={c30_real} fp_real={fp_real} level={fp_level}); firing native continue_confirm owner=0x{owner:x} (hook forwards -> SetState5 streams + performs switch cleanup) presses=0 (#{n})"
    ));
    // ARM the STEP_WorldResWait streaming-settle HOLD for THIS switch (bd reload-overlap-fix-design-
    // worldreswait-defer-release-on-streaming-settle-2026-07-24), at the SetState5/continue point -- the
    // same site as arm_request_move_map_fixup -- so the hold covers the upcoming RequestMoveMap -> MoveMap
    // -> STEP_WorldResWait. No-op unless the default-OFF opt-in marker is present AND this is a genuine
    // in-world switch (switch_reload_active && player was present at arm), so load1/boot are never touched.
    arm_worldreswait_hold();
    unsafe { own_load_continue_fire(base, owner, c30, c30_real, fp_real, fp_level, n) };
    true
}

/// Resolve `mss = GameDataMan->menuSystemSaveLoad = *(*(base + GAME_DATA_MAN_GLOBAL_RVA) +
/// GAME_DATA_MAN_MENU_SAVELOAD_60_OFFSET)` (static-verified: `GetMenuSystemSaveLoad` 0x140256410 is
/// exactly `GLOBAL_GameDataMan->menuSystemSaveLoad`). Returns `None` (never `null`/`0`) on any
/// fault-tolerant read failure. Pure reads.
pub(crate) unsafe fn resolve_menu_system_save_load(base: usize) -> Option<usize> {
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    let gdm = unsafe { safe_read_usize(base + GAME_DATA_MAN_GLOBAL_RVA) }
        .filter(|&v| v != null && v != 0)?;
    unsafe { safe_read_usize(gdm + GAME_DATA_MAN_MENU_SAVELOAD_60_OFFSET) }
        .filter(|&v| v != null && v != 0)
}

/// The "engine filled enough to drive our own load" gate -- distinct from "GameMan instance pointer
/// resolved" (`game_man_instance_resolved`), which flips true at BootPhase4, LONG before the load
/// machinery is usable. True iff GameDataMan + menuSystemSaveLoad (mss) resolve AND the TitleFlowContext
/// at `mss+0xa38` is a PLAUSIBLE heap pointer. The plausibility range matters: before the GameFlow
/// constructs the TitleFlowContext it reads back as uninitialized garbage (e.g. 0x8080808080808080),
/// which a `!= 0` check would wrongly accept -- then the LoadGame job's first `Run` derefs it and
/// access-violates (the ~25s AV observed when arming at the bare title). When this returns true, the
/// native LoadGame job (`own_load_pump_fire`) can be built + pumped without that crash. The bypass arms
/// its own-load on THIS, not on `game_man_instance_resolved`.
/// (loadgame-build-ctx-ready-precondition-2026-06-22)
pub(crate) unsafe fn loadgame_build_ctx_ready(base: usize) -> bool {
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    // CORRECTED (bd loadgame-owner-ctx-is-DIALOG-a38-not-mss-CORRECTION-2026-06-22): the buildable
    // TitleFlowContext is `*(CS::TitleTopDialog+0xa38)`, NOT `*(mss+0xa38)` (the mss reading was a red
    // herring -- r13 at the golden factory site is the dialog). Read it off the live dialog
    // (owner+0xe0, vtable-gated) via the cached title owner, so this arming signal matches exactly the
    // ctx `own_load_pump_fire` builds with.
    let owner = TITLE_OWNER_PTR.load(Ordering::SeqCst);
    if owner == null || owner == 0 {
        return false;
    }
    let dialog = unsafe { safe_read_usize(owner + TITLE_OWNER_MENU_HOLDER_E0_OFFSET) }.unwrap_or(0);
    if dialog == 0 {
        return false;
    }
    let dialog_vt = unsafe { safe_read_usize(dialog) }.unwrap_or(0);
    if dialog_vt != base + TITLE_TOP_DIALOG_VTABLE_RVA {
        return false;
    }
    let ctx = unsafe { safe_read_usize(dialog + DIALOG_OWNER_CTX_A38_OFFSET) }.unwrap_or(0);
    if !(ctx > OWNER_CTX_MIN_PLAUSIBLE_PTR && ctx < OWNER_CTX_MAX_PLAUSIBLE_PTR) {
        return false;
    }
    // Native `FUN_14082d090` checks this singleton before comparing regulation versions; our readiness
    // predicate must not claim the title/load context is usable before the same singleton exists.
    let regulation_manager =
        unsafe { safe_read_usize(base + GLOBAL_CS_REGULATION_MANAGER_RVA) }.unwrap_or(0);
    regulation_manager != 0 && regulation_manager != null
}

/// Whether the pre-flight detour on `CS::CSGaitemImp::Deserialize` is installed.
static GAITEM_DESER_PREFLIGHT_INSTALLED: AtomicUsize = AtomicUsize::new(0);
/// The trampoline back to the untouched native `CSGaitemImp::Deserialize`.
static GAITEM_DESER_PREFLIGHT_ORIG: AtomicUsize = AtomicUsize::new(0);

/// Read one `(handle, itemId)` pair from the stream's own buffer without disturbing its position.
///
/// The pair is what the native loop's two `ReadBytes(4)` calls are about to consume, so reading it
/// here answers "what is the loop about to see" rather than "what do we think is in the save".
unsafe fn gaitem_stream_entry(stream: usize, index: usize) -> Option<(u32, u32)> {
    let buf = unsafe { safe_read_usize(stream + DLMEMORY_INPUT_STREAM_BUF_OFFSET) }?;
    let position = unsafe { safe_read_usize(stream + DLMEMORY_INPUT_STREAM_POSITION_OFFSET) }?;
    let end = unsafe { safe_read_usize(stream + DLMEMORY_INPUT_STREAM_END_OFFSET) }?;
    let at = buf
        .checked_add(position)?
        .checked_add(index.checked_mul(8)?)?;
    // `end` is the buffer length, so the last readable byte is `buf + end`. Refuse rather than
    // read past it: a mispositioned stream is exactly the condition this exists to detect, and
    // detecting it by faulting would defeat the point.
    if position.checked_add(index * 8 + 8)? > end {
        return None;
    }
    let handle = unsafe { safe_read_i32(at) }? as u32;
    let item_id = unsafe { safe_read_i32(at + 4) }? as u32;
    Some((handle, item_id))
}

/// Why this entry cannot be a real serialized gaitem, if it cannot.
///
/// Two independent tests, and the second is the one that caught the live failure:
///
/// * **type nibble outside 0..=4** -- none of the five `GetGaItemHandle*` branches runs, the new
///   handle is never assigned, and `gaitemInsTable[-1]` is dispatched unguarded at `0x14067141a`.
/// * **bit 23 clear** -- `IsIndexedGaitemHandle(h) = (h >> 23) & 1` (`0x140682240`) is how the loop
///   decides whether the SAVED handle has an index. When it says no, `local_68` stays `-1` and the
///   very next statement is `scratch[local_68] = ...`, i.e. a 4-byte write immediately BEFORE a
///   `0x5000` heap allocation. The game never does that on a save it wrote, so an entry with bit 23
///   clear is proof the stream is not where the loop believes it is.
///
/// Handle 0 is SAFE and returns `None`: the loop skips those entries entirely, and a run of zeroes
/// is also the only case where the 8-byte stride is trustworthy.
fn gaitem_entry_defect(handle: u32) -> Option<&'static str> {
    if handle == 0 {
        return None;
    }
    if (handle >> GAITEM_HANDLE_TYPE_SHIFT) & GAITEM_HANDLE_TYPE_MASK >= GAITEM_HANDLE_TYPE_COUNT {
        return Some("type nibble outside 0..=4 -- no GetGaItemHandle branch runs");
    }
    if (handle >> GAITEM_HANDLE_INDEXED_BIT) & 1 == 0 {
        return Some(
            "bit 23 clear -- IsIndexedGaitemHandle says unindexed, so the loop writes scratch[-1]",
        );
    }
    None
}

/// The detour: log what the loop is about to read, and refuse the call outright if its first
/// entry cannot survive.
///
/// Only the FIRST entry is a gate. The per-item `gaitemInsTable[idx]->Deserialize(stream)`
/// consumes a variable number of bytes, so entries are not fixed-stride and everything after
/// entry 0 is at an offset this code cannot compute without re-implementing the parse. The
/// remaining entries are logged as a PREVIEW under exactly that caveat -- if the stream is
/// mispositioned they will be visibly garbage, which is the whole diagnostic value.
///
/// Refusing means returning without running the native body at all: the gaitem set is left empty.
/// That is a wrong inventory, and the chain upstream must treat it as a failed load -- but a wrong
/// inventory is recoverable and `gaitemInsTable[-1]` is not.
unsafe extern "system" fn gaitem_deserialize_preflight(imp: usize, stream: usize) {
    let base = game_module_base().unwrap_or(0);
    let gaitem = unsafe { safe_read_usize(base + GLOBAL_CSGAITEM_SINGLETON_RVA) }.unwrap_or(0);
    let head = unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_HEAD_OFFSET) }.unwrap_or(-1);
    let end = unsafe { safe_read_i32(gaitem + CSGAITEM_FREE_QUEUE_END_OFFSET) }.unwrap_or(-1);

    use std::fmt::Write as _;
    let mut preview = String::new();
    let mut fatal_first: Option<&'static str> = None;
    for index in 0..GAITEM_DESER_PREVIEW_ENTRIES {
        match unsafe { gaitem_stream_entry(stream, index) } {
            Some((handle, item_id)) => {
                let kind = (handle >> GAITEM_HANDLE_TYPE_SHIFT) & GAITEM_HANDLE_TYPE_MASK;
                let defect = gaitem_entry_defect(handle);
                if index == 0 {
                    fatal_first = defect;
                }
                let _ = write!(
                    preview,
                    " [{index}]h=0x{handle:08x} id=0x{item_id:08x} type={kind}{}",
                    match defect {
                        Some(reason) => format!(" FATAL({reason})"),
                        None => String::new(),
                    }
                );
            }
            None => {
                let _ = write!(preview, " [{index}]UNREADABLE");
                break;
            }
        }
    }
    append_autoload_debug(format_args!(
        "gaitem-deser-preflight: imp=0x{imp:x} stream=0x{stream:x} free-queue head/end {head}/{end}\
         -- entries the native loop is about to read (only [0] is a gate; later offsets are a\
         preview because per-item Deserialize consumes a variable length):{preview}"
    ));

    let orig = GAITEM_DESER_PREFLIGHT_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return;
    }
    // SAFETY: the trampoline MinHook produced for this exact signature.
    let orig: unsafe extern "system" fn(usize, usize) =
        unsafe { std::mem::transmute::<usize, unsafe extern "system" fn(usize, usize)>(orig) };

    let Some(reason) = fatal_first else {
        unsafe { orig(imp, stream) };
        return;
    };

    // SKIPPING THE CALL IS NOT AN OPTION, and that was measured rather than reasoned: the first
    // version of this returned without calling the original, and the process died anyway at
    // `game+0x671843` on `DLPanic("..\\..\\Source\\Game\\Gaitem\\CSGaitem.cpp", 0x307, "")`.
    // `Deserialize` and its paired finalize `0x671670` are a matched pair: the prologue allocates
    // the `0x5000` scratch into `+0x19028` and sets `+0x19031 = true`, and the finalize asserts on
    // finding them. Skip the body and the finalize asserts on what the body never set.
    //
    // So run the body -- against a buffer of zeroes. Handle 0 is the one value the loop is
    // GUARANTEED to survive: `if (local_res18[0] != 0)` skips the entry outright, no allocation, no
    // index, no dispatch. 0x1400 zeroed entries walk the whole loop, leave the latches exactly as
    // the finalize expects, and produce an empty gaitem set. An empty inventory is recoverable; a
    // `gaitemInsTable[-1]` vtable dispatch and a `scratch[-1]` heap write are not.
    let buf_field = stream + DLMEMORY_INPUT_STREAM_BUF_OFFSET;
    let position_field = stream + DLMEMORY_INPUT_STREAM_POSITION_OFFSET;
    let end_field = stream + DLMEMORY_INPUT_STREAM_END_OFFSET;
    let (Some(saved_buf), Some(saved_position), Some(saved_end)) = (
        unsafe { safe_read_usize(buf_field) },
        unsafe { safe_read_usize(position_field) },
        unsafe { safe_read_usize(end_field) },
    ) else {
        append_autoload_debug(format_args!(
            "gaitem-deser-preflight: entry [0] is not a serialized gaitem ({reason}), but the stream fields are unreadable -- running the native body UNSUBSTITUTED"
        ));
        unsafe { orig(imp, stream) };
        return;
    };
    append_autoload_debug(format_args!(
        "gaitem-deser-preflight: SUBSTITUTING zeroes -- entry [0] is not a serialized gaitem ({reason}). buf=0x{saved_buf:x} position={saved_position} end={saved_end}. The native body runs against {} zero bytes so its latches and its paired finalize 0x671670 stay consistent; the gaitem set will be EMPTY and this load must be treated as failed.",
        GAITEM_DESER_ZERO_FEED.len()
    ));
    let feed = GAITEM_DESER_ZERO_FEED.as_ptr() as usize;
    // SAFETY: three plain `usize` fields inside a stream object the callee already owns; restored
    // below before anything else can read them.
    unsafe {
        (buf_field as *mut usize).write(feed);
        (position_field as *mut usize).write(0);
        (end_field as *mut usize).write(GAITEM_DESER_ZERO_FEED.len());
        orig(imp, stream);
        (buf_field as *mut usize).write(saved_buf);
        (position_field as *mut usize).write(saved_position);
        (end_field as *mut usize).write(saved_end);
    }
}

/// Zeroes for the substitution above: `0x1400` entries of two `u32`s, plus slack so a read that
/// runs one entry long still lands inside it rather than past the end.
static GAITEM_DESER_ZERO_FEED: [u8; CSGAITEM_TABLE_CAPACITY * 8 + 64] =
    [0u8; CSGAITEM_TABLE_CAPACITY * 8 + 64];

/// Install the pre-flight detour, once.
///
/// Idempotent and non-fatal: a failure to hook leaves the chain exactly as it was, which is the
/// behaviour that crashed -- so the failure is logged loudly rather than swallowed.
pub(crate) fn install_gaitem_deser_preflight(base: usize) {
    if GAITEM_DESER_PREFLIGHT_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    match unsafe { crate::mh::MH_Initialize() } {
        crate::mh::MH_STATUS::MH_OK | crate::mh::MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            append_autoload_debug(format_args!(
                "gaitem-deser-preflight: MH_Initialize failed: {status:?} -- the native deserialize is UNGUARDED"
            ));
            return;
        }
    }
    let target = base + CSGAITEM_DESERIALIZE_RVA as usize;
    match unsafe {
        crate::mh::MhHook::new(
            target as *mut core::ffi::c_void,
            gaitem_deserialize_preflight as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => {
            GAITEM_DESER_PREFLIGHT_ORIG.store(hook.trampoline() as usize, Ordering::SeqCst);
            if let Err(status) = unsafe { hook.queue_enable() } {
                append_autoload_debug(format_args!(
                    "gaitem-deser-preflight: queue_enable failed: {status:?} -- UNGUARDED"
                ));
                return;
            }
            match unsafe { crate::mh::MH_ApplyQueued() } {
                crate::mh::MH_STATUS::MH_OK => {
                    crate::mh::leak_installed_hook(hook);
                    append_autoload_debug(format_args!(
                        "gaitem-deser-preflight: hooked CSGaitemImp::Deserialize 0x{target:x} -- logs the entries the loop will read and refuses a fatal entry [0]"
                    ));
                }
                status => append_autoload_debug(format_args!(
                    "gaitem-deser-preflight: MH_ApplyQueued failed: {status:?} -- UNGUARDED"
                )),
            }
        }
        Err(status) => append_autoload_debug(format_args!(
            "gaitem-deser-preflight: MhHook::new failed: {status:?} -- UNGUARDED"
        )),
    }
}
