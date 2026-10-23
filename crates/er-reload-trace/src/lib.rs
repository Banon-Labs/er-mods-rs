use std::{
    ffi::c_void,
    fmt,
    fs::File,
    io::Write,
    sync::atomic::{AtomicI32, AtomicU64, AtomicUsize, Ordering},
    sync::{Mutex, OnceLock},
};

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;
const CURRENT_PROCESS_PSEUDO_HANDLE: isize = -1;
const LOG_PATH: &str = "er-reload-trace.log";

const GAME_MAN_SINGLETON_RVA: usize = er_game_base::rva::GAME_MAN_SINGLETON_RVA;
const GAME_DATA_MAN_GLOBAL_RVA: usize = er_game_base::rva::GAME_DATA_MAN_GLOBAL_RVA;
/// Mounted-EBL-archive registry -- the `DLIO::DLFileDeviceManager` singleton, whose 2.7.2.0 VA is
/// `0x14484d548`.
///
/// It was `0x448464a8` here until 2026-08-30, and that was a transcription SLIP, not a migration
/// casualty. The 1.16.2 VA was `0x1448464a8`, an RVA of `0x48464a8`; the old constant had dropped a
/// leading `0x14` instead of the full image base, leaving an RVA of 0x448464a8 -- about 1.1 GB past
/// the end of a 0x5e08000-byte image, on every build. `read_usize` is `ReadProcessMemory`-backed
/// and the call site ended in `.unwrap_or(0)`, so the read never faulted and never logged: the
/// mount census in every trace line this DLL wrote until then reported `mounted_registry=0x0`
/// because the address was unmappable, not because nothing was mounted.
const MOUNTED_ARCHIVE_REGISTRY_RVA: usize = er_game_base::rva::DL_FILE_DEVICE_MANAGER_SINGLETON_RVA;

// MoveMapStep finalize advancer, 2.7.2.0 rva 0xafbeb0 (1.16.1 dump name FUN_140afa7c0).
// param_1 (rcx) = MoveMapStep; field25_0x12a = the finalize
// sub-state (0..9). cVar10 (the ending request it computes) is written to menuData+0x5e; its rt5d input
// is menuData+0x5d. Hooking this proves whether the advancer is even ticked for load2 and what it reads.
const MOVEMAPSTEP_FINALIZE_12A_OFFSET: usize = 0x12a;
/// Child teardown, 2.7.2.0 rva 0xeb8a20 (1.16.1 dump name FUN_140eb54e0). STEP_MoveMap_Update calls
/// it to tear down the MoveMapStep child (whose EzChildStepBase = MoveMapStep + 0x108). Hooking it +
/// logging the child_base-0x108 MoveMapStep state(+0x48)/field25(+0x12a) shows whether load2's
/// MoveMapStep child (state==18) is torn down at field25<9 (teardown mechanism) or never appears here
/// (never re-scheduled). rva 0xafbeb0 = the advancer; the mms= pointer in each log distinguishes loads.
const CHILD_TEARDOWN_RVA: usize = er_game_base::rva::EZ_CHILDSTEP_RESET_RVA;
const MOVEMAPSTEP_CHILD_EZSTEP_OFFSET: usize = 0x108;
const MOVEMAPSTEP_STATE_48_OFFSET: usize = 0x48;
const CS_MENU_MAN_GLOBAL_RVA: usize = er_game_base::rva::CS_MENU_MAN_GLOBAL_RVA;
const CS_MENU_MAN_MENU_DATA_OFFSET: usize = 0x8;
const MENU_DATA_RT5D_OFFSET: usize = 0x5d;
const MENU_DATA_ENDING_5E_OFFSET: usize = 0x5e;

// Case-7 (7->8) save-drain gate flags read by the finalize advancer (0x140afbeb0):
// ShouldSave() reads GameMan->saveRequested (0xb72); 0x14067a670() reads GameMan+0xb73 (gated by
// bc4!=3). The suppressed in-world quit-save leaves these set, so !ShouldSave()/!0x14067a670() fail
// and load2 parks at field25=7 even after rt5d unblocks case 0. The rt5d drive clears them natively.
#[allow(
    dead_code,
    reason = "retained RE fact: the case-7 save-drain gate offset, kept after the drive that cleared it was removed"
)]
const GAME_MAN_SAVE_REQUESTED_B72_OFFSET: usize = 0xb72;
#[allow(
    dead_code,
    reason = "retained RE fact: the case-7 save-drain gate offset, kept after the drive that cleared it was removed"
)]
const GAME_MAN_FIELD_B73_OFFSET: usize = 0xb73;
const GAME_MAN_REQUESTED_SLOT_B78_OFFSET: usize = 0xb78;
/// `CS::GameMan::saveState` -- the one-slot arbiter over the SL device, stamped by both the save
/// lane (1) and the load lane (2), not a "load phase". Renamed 2026-08-31 from
/// `GAME_MAN_LOAD_PHASE_B80_OFFSET`; the witnesses and the full value table are on the
/// declaration in `er_title_flow::GAME_MAN_SAVE_STATE_B80_OFFSET`.
const GAME_MAN_SAVE_STATE_B80_OFFSET: usize = 0xb80;
const GAME_MAN_SAVE_SLOT_AC0_OFFSET: usize = 0xac0;
/// `CS::GameMan::stayInMultipleAreaBlockId` -- Not "the current map", which is what this constant
/// claimed until 2026-08-31. The map-move target is `moveMapStepBlockId` at **+0x14**, and
/// `SetMoveMapStepBlockId` (`0x14067bed0`) writes it from this field via the getter
/// `0x14067a860`. So +0xc30 is the source of the next map move: seeded from slot body+0x04 by
/// the deserializer `0x14067d070` on load, and maintained by the stay-in-multiplay path
/// (`0x14067c2a0`, `0x14067bdc0`) during play. See
/// `er_title_flow::GAME_MAN_SAVED_MAP_C30_OFFSET` for the complete access set.
const GAME_MAN_STAY_IN_MULTIPLAY_AREA_BLOCK_ID_C30_OFFSET: usize = 0xc30;
/// `GameMan + 0xdf0` -- the length of the `DLString<wchar_t>` inside the `FD4FilePathBase` that
/// starts at `GameMan + 0xdd0`. It is not a "resident device" pointer; the old name was invented
/// from the value's shape and nothing ever measured it.
///
/// The constructor hands the whole `0xdd0..0xe08` region off in one `lea` and never touches the
/// interior through `this`, which is why a `this`-relative displacement census reports nothing at
/// 0xdf0 -- the same silence that let `CS_SYSTEM_STEP_CURRENT_STATE_OFFSET` sit wrong for its
/// whole life. The interior is written through that `lea`'s register instead:
///
/// ```text
///   14067644b  lea  rdi, [rsi+0xdd0]      ; FD4FilePathBase, 1.16.2; 2.7.2.0 0x14067774b
///   140676456  lea  rbx, [rdi+8]          ; the DLString's allocator slot at 0xdd8
///   140676461  call 0x140120390           ; DLString<wchar_t>* InitAllocator(DLString<wchar_t>*)
///   140676466  mov  qword [rbx+0x20], 7   ; capacity  -> GameMan+0xdf8
///   14067646e  lea  rax, [rbx+8]          ; the string proper at GameMan+0xde0
///   140676472  mov  qword [rax+0x10], r14 ; LENGTH    -> GameMan+0xdf0   <-- this field
///   140676476  cmp  qword [rax+0x18], 8   ; capacity vs the inline-buffer bound
/// ```
///
/// Ghidra's 1.16.2 `GameMan` type agrees independently: `FD4FilePathBase` at `0xdd0`, spanning to
/// `0xe08`. The whole GameMan constructor aligns 1296/1296 against 1.17 with zero moved offsets,
/// and the `lea` that anchors this region is at the same `0xdd0` in both images.
///
/// What reads it, in the decompiler's own words -- a third witness, independent of both the type
/// and the constructor, and the reason this is logged in decimal. Both save gates spell the test
/// as a string length, not as a handle:
///
/// ```text
///   0x14067a480: if ((GLOBAL_GameMan->field479_0xdd0).string.length != 0) { saveState = 3; return 0; }
///   0x14067c400: if (saveState != 3) return 0;
///                  if ((GLOBAL_GameMan->field479_0xdd0).string.length != 0) { saveState = 0; return 1; }
/// ```
///
/// So the gate is "a file path is set", and the value it compares against is `0`. That is why
/// this read must stay an `Option` rather than `unwrap_or(0)`: a failed read rendered as `0` is
/// indistinguishable from an empty path, i.e. from the gate being open.
const GAME_MAN_FILE_PATH_STRING_LEN_DF0_OFFSET: usize = 0xdf0;
// Load-submit gate fields (bd load-submit-67dc00-gate-offsets-to-instrument-pin-load2-divergence).
// save_dispatch_combined -> submit 0x14067ef00 bails (0x14067f42f) unless these GameMan[0x143d709a8]
// flags are clear/set (2.7.2.0 VAs). Logging them at the finalize-advancer heartbeat (which fires
// for load2 in the stuck window) pins which gate is the sole load2 divergence vs load1, without
// Ghidra and without forcing state. cb1/cb2/bca/b5e are byte flags; the global at rva 0x3d6f108 must be non-null.
const GAME_MAN_SUBMIT_GATE_CB1_OFFSET: usize = 0xcb1;
const GAME_MAN_SUBMIT_GATE_CB2_OFFSET: usize = 0xcb2;
/// `CS::GameMan::eventWorldType`, a byte. Renamed 2026-08-31 from
/// `GAME_MAN_SUBMIT_GATE_BCA_OFFSET`: the submit path does read it (`0x14067ef00` branches on
/// `GLOBAL_GameMan->eventWorldType != 0`), but naming a field after one consumer buried the fact
/// that it is a named field with its own accessor pair -- `EventWorldType` (`0x14067ab20`,
/// `movzx eax, byte ptr [rax+0xbca]`) and `SetWorldEventType` (`0x14067c1b0`,
/// `mov byte ptr [rax+0xbca], cl`) -- and grouped it with cb1/cb2/b5e, which genuinely have no
/// name in either the Ghidra type or `fromsoftware-rs`. `IsMyWorld` (`0x14067b290`) is the third
/// reader and tests it against 0. `fromsoftware-rs` declares it as the enum
/// `pub event_world_type: EventWorldType`.
const GAME_MAN_EVENT_WORLD_TYPE_BCA_OFFSET: usize = 0xbca;
const GAME_MAN_SUBMIT_GATE_B5E_OFFSET: usize = 0xb5e;
const SUBMIT_GATE_GLOBAL_RVA: usize = er_game_base::rva::SAVE_DATA_SUBSYSTEM_GATE_RVA;
const GAME_DATA_MAN_PLAYER_GAME_DATA_08_OFFSET: usize = 0x08;

const HOOK_ORIGINAL_UNSET: usize = 0;

type TraceHookFn = unsafe extern "system" fn(usize, usize, usize, usize) -> usize;

/// Bounded wait for the product DLL to map + export `er_effects_union_register_2720`, asked once so the
/// answer is settled before any hook is registered. ~1s at 25ms, matching `er-hook`'s own budget.
///
/// This is a presence question only -- `er_hook::register_shared_hook` does the routing. The old
/// ~3s hand-rolled poll also decided which code path installed the hooks, and its timeout dropped a
/// multi-DLL run onto a completely ungated MinHook path. There is no such path left: both answers
/// now resolve the address through the gate, so a late product load costs a chained handler, never
/// a stale write.
const PRODUCT_RESOLVE_TRIES: u32 = 40;
const PRODUCT_RESOLVE_SLEEP_MS: u32 = 25;

/// Addresses the product DLL owns with a bare `MhHook` (not its union) in the sq-repro reload mode
/// this trace runs alongside: 0x67c500 = SYSTEM_QUIT_REQUEST_LOAD_SLOT, 0x67c590 =
/// SYSTEM_QUIT_INWORLD_LOAD (the reload's picked-slot deserialize proof). These are the product's
/// 2.7.2.0 rvas and must equal the HookSpec rows below, since the skip compares by value.
/// Registering our observer there would create the union dispatcher on that address first if our install thread wins the
/// race, making the product's later `MhHook::new` return ALREADY_CREATED and silently dropping the
/// product's critical reload hook. So whenever the product DLL is in the process we skip these two --
/// the product's own menu-trace union hooks + its inworld-load debug line already log the same
/// deserialize events. A standalone trace run, with no product DLL present, still installs them
/// (through this DLL's own gated union, so the address is still checked against the running build).
const PRODUCT_OWNED_SKIP_RVAS: &[usize] = &[0x67c500, 0x67c590];

static LOG_FILE: OnceLock<Option<Mutex<File>>> = OnceLock::new();
static EVENT_SEQ: AtomicU64 = AtomicU64::new(0);
/// Finalize-advancer instrumentation: total calls + last-seen field25_0x12a (so a run logs on every
/// sub-state change + a periodic heartbeat, instead of per-frame spam).
static FIN_ADVANCER_CALLS: AtomicU64 = AtomicU64::new(0);
static LAST_FIN12A: AtomicI32 = AtomicI32::new(-2);
/// rt5d diagnostic drive (bd decisive-load2-divergence-is-rt5d-menudata5d): load1's finalize naturally
/// gets menuData+0x5d(rt5d)=1 and walks field25 0..9; load2's stays 0 and parks at field25=0 forever.
/// Once a single MoveMapStep has been stuck at field25=0 for RT5D_DRIVE_THRESHOLD consecutive advancer
/// calls (load1 flips at ~call#133, so this only ever fires for a genuinely-stuck load2), supply rt5d=1
/// once so the game's own finalize completes -- then observe complete(field25->9, movable) vs teardown.
#[allow(
    dead_code,
    reason = "retained RE fact: the removed rt5d drive's stuck-call threshold, kept with its sibling drive statics"
)]
const RT5D_DRIVE_THRESHOLD: u64 = 30;
static RT5D_DRIVE_MMS: AtomicUsize = AtomicUsize::new(0);
static RT5D_DRIVE_ZERO_STREAK: AtomicU64 = AtomicU64::new(0);
static RT5D_DRIVE_DONE_MMS: AtomicUsize = AtomicUsize::new(0);
/// The most recent MoveMapStep whose finalize was seen advancing (field25>=5) -- i.e. a load that
/// completes on its own (load1, or a driven load2). Once set, any different mms stuck at field25=0 is
/// the divergent next load; drive it after only RT5D_DRIVE_THRESHOLD stuck calls (a short run may never
/// reach a large global count). Also lets the same logic catch load3 after load2 completes.
static COMPLETION_SEEN_MMS: AtomicUsize = AtomicUsize::new(0);
static ORIG_FINALIZE_ADVANCER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
/// Child-teardown instrumentation (rva 0xeb8a20): every teardown logs the child + its MoveMapStep
/// state/field25, so load2's MoveMapStep child teardown (if any) is visible.
static ORIG_CHILD_TEARDOWN: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static CHILD_TEARDOWN_CALLS: AtomicU64 = AtomicU64::new(0);

static ORIG_MENU_CONTINUE_WRAPPER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MENU_NEW_OR_LOAD_WRAPPER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MENU_OTHER_LOAD_WRAPPER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_NATIVE_SUBMIT: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_RESULT_EVENT_HANDLER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_RESULT_ACTION_BUILDER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_RESULT_EVENT_WRAPPER_BUILDER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_TASK_ENQUEUE: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_SET_SAVE_SLOT: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_SAVE_REQUEST_PROFILE: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_REQUEST_SAVE: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CURRENT_SLOT_LOAD: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CONTINUE_LOAD: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_COMBINED_LOAD: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MAP_LOAD: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_SAVE_LOAD_STATE_INIT: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_B80_PREVIEW: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_TITLE_CONFIRM: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_REQUEST_LOAD_SLOT: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_REQUEST_PROFILE_READ: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_B80_POLL: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_SLOT_DESER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_DISPATCHER2: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_DOSAVE_STUFF: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MAP_REQUEST_DO: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MAP_WORK: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_SETSTATE: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_LOAD_ACTIVATE: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_LOAD_ACTIVATE2: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_BUILDER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_SELECTOR_TICK: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_MENU_DESER: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_CAP_DIALOG_FACTORY: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MENU_WINDOW_JOB_CTOR: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MENU_WINDOW_JOB_NATIVE_CTOR_B: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);
static ORIG_MENU_WINDOW_JOB_IDLE_CTOR: AtomicUsize = AtomicUsize::new(HOOK_ORIGINAL_UNSET);

struct HookSpec {
    name: &'static str,
    rva: usize,
    detour: TraceHookFn,
    original: &'static AtomicUsize,
}

// No raw MinHook FFI here, deliberately, and this crate is why the rule exists.
//
// Until 2026-08-30 this file imported the raw `MH_CreateHook` / `MH_EnableHook` externs and called
// them on a hand-built `base + spec.rva`. `er-hook`'s 1.17 resolve gate lives inside `MhHook::new`,
// `register_union_hook` and `register_shared_hook` -- none of which were on that path -- so on the
// 1.17 image all 40 targets were stale 1.16.2 addresses and MinHook wrote 34 five-byte JMPs into
// live code, 19 of them splitting an instruction (`STEP_BeginLogo`, `STEP_MsbLoad`,
// `_CheckEndingRequest`, the GameMan accessor). It failed invisibly: 34 `installed` lines, zero
// refusals, zero detour events, and no crash record. Every registration now goes through
// `er_hook::register_shared_hook`, which resolves (or refuses) the address for the running build
// and picks the product's single MinHook instance when the product DLL is co-loaded.
//
// NOTE for whoever owns `scripts/check-reload-trace-policy.py`: its `has_minhook` fact is the
// literal presence of the two names above anywhere in this crate's sources, so it is currently
// satisfied by this comment rather than by any call. The assertion it stands for -- "must use
// MinHook trampolines for pass-through instrumentation" -- is still true (via `er-hook`'s union),
// but the fact should be re-pointed at `register_shared_hook`, and the raw externs should become a
// deny for this crate, so the bypass this comment describes cannot be reintroduced silently.

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetModuleHandleA(name: *const u8) -> *mut c_void;
    fn GetTickCount64() -> u64;
    fn ReadProcessMemory(
        process: isize,
        base: *const c_void,
        buffer: *mut c_void,
        size: usize,
        read: *mut usize,
    ) -> i32;
}

/// This run's trace path: the launcher's redirect, else `LOG_PATH` beside `eldenring.exe`.
///
/// The biggest producer in the REPO, and until now the least movable. This trace runs at roughly
/// 655 MB/hour, and it used to resolve as a bare CWD-relative name that no launcher could move, so
/// every launch rotated the previous run's trace to `.prev` and the launch after that destroyed it
/// outright. The redirect (and the game-directory fallback behind it) lives in
/// `er_game_base::log`, shared with every other per-run artifact so there is one convention for
/// where a run's evidence goes rather than one per crate.
///
/// This is an output path, not a runtime gate: nothing this DLL does changes with it, and the
/// crate reads no environment itself — `.auto/reload_trace_policy.rego` still holds.
fn log_path() -> std::path::PathBuf {
    er_game_base::log::redirected_artifact_path("ER_QUICKLOAD_RELOAD_TRACE_PATH", LOG_PATH)
}

/// Cached appending handle onto a log this process freshened on its first write. The one-shot
/// truncation lives in `er_game_base::log`, so the handle can be held for the whole run.
fn open_log_file() -> Option<Mutex<File>> {
    er_game_base::log::open_fresh_run_append(&log_path()).map(Mutex::new)
}

/// Start this run's trace clean at attach. Rotates the previous run's file to `.log.prev`
/// rather than destroying it (this used to be a bare `File::create`).
fn reset_log_file() {
    er_game_base::log::begin_fresh_run(&log_path());
}

fn log_line(args: fmt::Arguments<'_>) {
    let Some(lock) = LOG_FILE.get_or_init(open_log_file) else {
        return;
    };
    let Ok(mut file) = lock.lock() else {
        return;
    };
    let tick = unsafe { GetTickCount64() };
    let seq = EVENT_SEQ.fetch_add(1, Ordering::SeqCst) + 1;
    let _ = writeln!(file, "[{seq:06} +{tick}ms] {args}");
}

fn game_base() -> Option<usize> {
    let base = unsafe { GetModuleHandleA(std::ptr::null()) } as usize;
    (base != 0).then_some(base)
}

unsafe fn read_usize(addr: usize) -> Option<usize> {
    let mut value = 0usize;
    let mut read = 0usize;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut usize as *mut c_void,
            std::mem::size_of::<usize>(),
            &mut read,
        )
    };
    (ok != 0 && read == std::mem::size_of::<usize>()).then_some(value)
}

unsafe fn read_i32(addr: usize) -> Option<i32> {
    let mut value = 0i32;
    let mut read = 0usize;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut i32 as *mut c_void,
            std::mem::size_of::<i32>(),
            &mut read,
        )
    };
    (ok != 0 && read == std::mem::size_of::<i32>()).then_some(value)
}

unsafe fn read_u8(addr: usize) -> Option<u8> {
    let mut value = 0u8;
    let mut read = 0usize;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut u8 as *mut c_void,
            1,
            &mut read,
        )
    };
    (ok != 0 && read == 1).then_some(value)
}

/// Dump a window of qwords from the MoveMapStep header (own process, guarded reads). Why: load2's
/// MoveMapStep Update (0x140b01000) stops being ticked by the FD4 scheduler after ~6 ticks while
/// load1 ticks it ~145x to completion (bd load2-real-blocker-movemapstep-child-advancer-tick-never-runs).
/// The advancer fires 1:1 with that Update, so dumping the mms header at each tick lets a run diff
/// load1 (keeps ticking) vs load2 (drops out) and reveal which header field (step-machine state /
/// active flag / scheduler link) changes when ticking stops. Offsets +0x00..+0x58, plus the child
/// ezstep pointer region around +0x108. Read-only.
fn mms_header_window(a: usize) -> String {
    let mut s = String::from("mmshdr[");
    let mut off = 0usize;
    while off <= 0x58 {
        match unsafe { read_usize(a + off) } {
            Some(v) => s.push_str(&format!("+{off:x}=0x{v:x} ")),
            None => s.push_str(&format!("+{off:x}=? ")),
        }
        off += 8;
    }
    // child ezstep base region (mms+0x108) + the finalize substate byte (+0x12a) neighbourhood
    for off in [0x100usize, 0x108, 0x110, 0x118, 0x128] {
        match unsafe { read_usize(a + off) } {
            Some(v) => s.push_str(&format!("+{off:x}=0x{v:x} ")),
            None => s.push_str(&format!("+{off:x}=? ")),
        }
    }
    s.push(']');
    s
}

/// Custom detour for the MoveMapStep finalize advancer (0xafbeb0). Logs field25_0x12a before/after the
/// native call plus menuData 0x5d(rt5d)/0x5e(cVar10 out) -- Only on a sub-state change or every 600th
/// call (heartbeat), so a frozen load2 (field25 stuck at 0) is visible without per-frame flooding while
/// a healthy walk 0->9 logs every transition. rcx (`a`) = MoveMapStep.
unsafe extern "system" fn hook_finalize_advancer(a: usize, b: usize, c: usize, d: usize) -> usize {
    let calls = FIN_ADVANCER_CALLS.fetch_add(1, Ordering::SeqCst) + 1;
    let fin_before = unsafe { read_u8(a + MOVEMAPSTEP_FINALIZE_12A_OFFSET) }.map_or(-1, i32::from);
    let menu = game_base().and_then(|base| unsafe {
        read_usize(er_game_base::mem::game_data_addr(
            base,
            CS_MENU_MAN_GLOBAL_RVA,
            "CS_MENU_MAN_GLOBAL_RVA",
        ))
    });
    let menu_data = menu.and_then(|m| unsafe { read_usize(m + CS_MENU_MAN_MENU_DATA_OFFSET) });
    // NOTE: the rt5d/save-flag drive was removed (bd correction-rt5d-drive-tears-down-load2). Driving
    // menuData+0x5d=1 (+ clearing saveRequested/0xb73) did complete load2's finalize 0..9, but that
    // TORE the player down (post-completion: present=False, havok=None, mms=-1 at ~60fps = a player-less
    // world) -- load2's player is not movable at fin=0 when the finalize runs, unlike load1. So the
    // finalize-drive is a proven dead end; this hook is log-only again so traces show the natural load2.
    // The old drive statics/consts are retained for reference but intentionally unused.
    let _ = (
        &RT5D_DRIVE_MMS,
        &RT5D_DRIVE_ZERO_STREAK,
        &RT5D_DRIVE_DONE_MMS,
        &COMPLETION_SEEN_MMS,
    );
    let ret = unsafe { call_original(&ORIG_FINALIZE_ADVANCER, a, b, c, d) };
    let fin_after = unsafe { read_u8(a + MOVEMAPSTEP_FINALIZE_12A_OFFSET) }.map_or(-1, i32::from);
    let m5d = menu_data
        .and_then(|md| unsafe { read_u8(md + MENU_DATA_RT5D_OFFSET) })
        .map_or(-1, i32::from);
    let m5e = menu_data
        .and_then(|md| unsafe { read_u8(md + MENU_DATA_ENDING_5E_OFFSET) })
        .map_or(-1, i32::from);
    let last = LAST_FIN12A.swap(fin_after, Ordering::SeqCst);
    // Log every advancer tick with the mms header window. load2's Update ticks only ~6x before the FD4
    // scheduler drops it (load1 ~145x), so the per-tick header lets a run diff which mms field flips
    // when load2 stops ticking. Volume is bounded (a few hundred lines/run) -- acceptable for a
    // diagnostic (bd load2-real-blocker-movemapstep-child-advancer-tick-never-runs).
    let _ = (fin_before, last);
    log_line(format_args!(
        "finalize_advancer call#{calls} mms=0x{a:x} field25_12a {fin_before}->{fin_after} menuData_5d={m5d} 5e={m5e} {} {}",
        mms_header_window(a),
        snapshot()
    ));
    ret
}

/// Log-only detour for the child teardown (rva 0xeb8a20). Logs every teardown with the
/// child_base-0x108 MoveMapStep state/field25 so load2's MoveMapStep child teardown (state==18) is
/// visible -- or its absence proves the child is never re-scheduled rather than torn down. mms= ptr
/// distinguishes load1 vs load2.
unsafe extern "system" fn hook_child_teardown(a: usize, b: usize, c: usize, d: usize) -> usize {
    let n = CHILD_TEARDOWN_CALLS.fetch_add(1, Ordering::SeqCst) + 1;
    let mms = a.wrapping_sub(MOVEMAPSTEP_CHILD_EZSTEP_OFFSET);
    let st = unsafe { read_i32(mms + MOVEMAPSTEP_STATE_48_OFFSET) }.unwrap_or(-999);
    let fin = unsafe { read_u8(mms + MOVEMAPSTEP_FINALIZE_12A_OFFSET) }.map_or(-1, i32::from);
    log_line(format_args!(
        "child_teardown call#{n} child_base=0x{a:x} mms=0x{mms:x} state={st} field25={fin} {}",
        snapshot()
    ));
    unsafe { call_original(&ORIG_CHILD_TEARDOWN, a, b, c, d) }
}

fn snapshot() -> String {
    let Some(base) = game_base() else {
        return "base=<unresolved>".to_owned();
    };
    let gm = unsafe {
        read_usize(er_game_base::mem::game_data_addr(
            base,
            GAME_MAN_SINGLETON_RVA,
            "GAME_MAN_SINGLETON_RVA",
        ))
    }
    .unwrap_or(0);
    let gdm = unsafe {
        read_usize(er_game_base::mem::game_data_addr(
            base,
            GAME_DATA_MAN_GLOBAL_RVA,
            "GAME_DATA_MAN_GLOBAL_RVA",
        ))
    }
    .unwrap_or(0);
    // Through the build gate, then checked for zero. `game_data_addr` answers 0 when the running
    // build is not 2.7.2.0, and `read_usize(0)` fails the same way a genuinely null global
    // reads -- so without this branch a refusal and "nothing is mounted" print identically, which
    // is the confident-false-negative this whole migration keeps producing.
    let mounted_addr = er_game_base::mem::game_data_addr(
        base,
        MOUNTED_ARCHIVE_REGISTRY_RVA,
        "MOUNTED_ARCHIVE_REGISTRY_RVA",
    );
    let mounted = if mounted_addr == 0 {
        None
    } else {
        Some(unsafe { read_usize(mounted_addr) })
    };

    let b78 = unsafe { read_i32(gm + GAME_MAN_REQUESTED_SLOT_B78_OFFSET) };
    let b80 = unsafe { read_i32(gm + GAME_MAN_SAVE_STATE_B80_OFFSET) };
    let ac0 = unsafe { read_i32(gm + GAME_MAN_SAVE_SLOT_AC0_OFFSET) };
    let c30 = unsafe { read_i32(gm + GAME_MAN_STAY_IN_MULTIPLAY_AREA_BLOCK_ID_C30_OFFSET) };
    // A count of characters, kept as `Option` and printed in decimal. `.unwrap_or(0)` was wrong
    // twice over: 0 is the value the two gates below test for, so an unreadable read printed as
    // "the path is empty" -- the reading that makes both gates look open.
    let path_len = unsafe { read_usize(gm + GAME_MAN_FILE_PATH_STRING_LEN_DF0_OFFSET) };
    let pgd = unsafe { read_usize(gdm + GAME_DATA_MAN_PLAYER_GAME_DATA_08_OFFSET) }.unwrap_or(0);

    // Load-submit gate fields (see the *_SUBMIT_GATE_* consts): diff load1 vs load2 to find the gate
    // that keeps load2's combined_load submit bailing so the world load never completes.
    let g_cb1 = unsafe { read_u8(gm + GAME_MAN_SUBMIT_GATE_CB1_OFFSET) };
    let g_cb2 = unsafe { read_u8(gm + GAME_MAN_SUBMIT_GATE_CB2_OFFSET) };
    let g_bca = unsafe { read_u8(gm + GAME_MAN_EVENT_WORLD_TYPE_BCA_OFFSET) };
    let g_b5e = unsafe { read_u8(gm + GAME_MAN_SUBMIT_GATE_B5E_OFFSET) };
    // Through the build gate: `SAVE_DATA_SUBSYSTEM_GATE_RVA` moved +0x4070 on 1.17, and again on
    // 2.7.2.0. Read raw on another build, this line prints an unrelated global as the submit gate --
    // and the whole point of the line is to diff load1 against load2 to find the gate that is
    // holding, which a wrong-but-plausible value makes actively misleading.
    let g_glob = unsafe {
        read_usize(er_game_base::mem::game_data_addr(
            base,
            SUBMIT_GATE_GLOBAL_RVA,
            "SAVE_DATA_SUBSYSTEM_GATE_RVA",
        ))
    }
    .unwrap_or(0);

    format!(
        "base=0x{base:x} gm=0x{gm:x} b78={} b80={} ac0={} c30={} path_len_df0={} gdm=0x{gdm:x} pgd=0x{pgd:x} mounted_registry={} submit[cb1={} cb2={} bca={} b5e={} glob=0x{g_glob:x}]",
        fmt_i32(b78),
        fmt_i32(b80),
        fmt_i32(ac0),
        fmt_c30(c30),
        fmt_len(path_len),
        fmt_mounted(mounted),
        fmt_u8(g_cb1),
        fmt_u8(g_cb2),
        fmt_u8(g_bca),
        fmt_u8(g_b5e),
    )
}

fn fmt_i32(value: Option<i32>) -> String {
    value.map_or_else(|| "<unreadable>".to_owned(), |value| value.to_string())
}

fn fmt_c30(value: Option<i32>) -> String {
    value.map_or_else(|| "<unreadable>".to_owned(), |value| format!("0x{value:x}"))
}

fn fmt_u8(value: Option<u8>) -> String {
    value.map_or_else(|| "<unreadable>".to_owned(), |value| value.to_string())
}

/// A `DLString` length, printed in decimal so no reader chases it as an address.
///
/// It was `df0=0x{:x}` for this line's whole life, under a constant named
/// `GAME_MAN_RESIDENT_DEVICE_DF0_OFFSET`, so the log said "pointer" in both the key and the
/// formatting. A hex-rendered small integer beside `gm=0x...` and `pgd=0x...` is a number that
/// looks like an address and is not one, and this repo has already lost runs to exactly that:
/// an oracle reported `-95247096` -- the low half of a live `DLAllocator*` -- as a state enum
/// for an unknown number of runs, because a wrong-but-readable value faults nothing.
fn fmt_len(value: Option<usize>) -> String {
    value.map_or_else(|| "<unreadable>".to_owned(), |value| value.to_string())
}

/// Three outcomes the mount census used to print identically as `0x0`: the running build refused
/// the address (outer `None`), the read failed (inner `None`), or the global really is what it
/// says. Collapsing the first two into `0x0` is what let a 1.1 GB-out-of-range constant look like
/// "nothing is mounted" for the DLL's entire existence.
fn fmt_mounted(value: Option<Option<usize>>) -> String {
    match value {
        None => "<refused>".to_owned(),
        Some(None) => "<unreadable>".to_owned(),
        Some(Some(pointer)) => format!("0x{pointer:x}"),
    }
}

unsafe fn call_original(
    original: &'static AtomicUsize,
    a: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    let original = original.load(Ordering::SeqCst);
    if original == HOOK_ORIGINAL_UNSET {
        return 0;
    }
    let original: TraceHookFn = unsafe { std::mem::transmute(original) };
    unsafe { original(a, b, c, d) }
}

unsafe fn trace_hook(
    name: &'static str,
    original: &'static AtomicUsize,
    a: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    log_line(format_args!(
        "{name} ENTER rcx=0x{a:x} rdx=0x{b:x} r8=0x{c:x} r9=0x{d:x} {}",
        snapshot()
    ));
    let ret = unsafe { call_original(original, a, b, c, d) };
    log_line(format_args!(
        "{name} LEAVE ret=0x{ret:x} rcx=0x{a:x} rdx=0x{b:x} r8=0x{c:x} r9=0x{d:x} {}",
        snapshot()
    ));
    ret
}

macro_rules! define_trace_hook {
    ($fn_name:ident, $original:ident, $label:literal) => {
        unsafe extern "system" fn $fn_name(a: usize, b: usize, c: usize, d: usize) -> usize {
            unsafe { trace_hook($label, &$original, a, b, c, d) }
        }
    };
}

// Each label names the hooked function, never its address. Until the 2.7.2.0 port every label
// ended in the function's 1.16.2 address (`native_submit_7ac890`), which named an address the game
// no longer had. `scripts/summarize-reload-trace-log.py` maps those old labels onto these, so a log
// from before the port still summarizes. The four save-path functions carry the name their
// constants have had since 2026-08-01, because none of them loads anything: `save_dispatch_system`
// (`SAVE_DISPATCH_SYSTEM_RVA`, once `current_slot_load`), `save_write_to_slot`
// (`SAVE_WRITE_TO_SLOT_RVA`, once `continue_load`), `save_dispatch_combined`
// (`SAVE_DISPATCH_COMBINED_RVA`, once `combined_load`) and `save_dispatch_entry0b`
// (`SAVE_DISPATCH_ENTRY0B_RVA`, once `map_load`). `er-quickload`'s continue trace spells these four
// save-path labels the same way; its other labels are its own.
define_trace_hook!(
    hook_menu_continue_wrapper,
    ORIG_MENU_CONTINUE_WRAPPER,
    "menu_continue_wrapper"
);
define_trace_hook!(
    hook_menu_new_or_load_wrapper,
    ORIG_MENU_NEW_OR_LOAD_WRAPPER,
    "menu_new_or_load_wrapper"
);
define_trace_hook!(
    hook_menu_other_load_wrapper,
    ORIG_MENU_OTHER_LOAD_WRAPPER,
    "menu_other_load_wrapper"
);
define_trace_hook!(hook_native_submit, ORIG_NATIVE_SUBMIT, "native_submit");
define_trace_hook!(
    hook_result_event_handler,
    ORIG_RESULT_EVENT_HANDLER,
    "result_event_handler"
);
define_trace_hook!(
    hook_result_action_builder,
    ORIG_RESULT_ACTION_BUILDER,
    "result_action_builder"
);
define_trace_hook!(
    hook_result_event_wrapper_builder,
    ORIG_RESULT_EVENT_WRAPPER_BUILDER,
    "result_event_wrapper_builder"
);
define_trace_hook!(hook_task_enqueue, ORIG_TASK_ENQUEUE, "task_enqueue");
define_trace_hook!(hook_set_save_slot, ORIG_SET_SAVE_SLOT, "set_save_slot");
define_trace_hook!(
    hook_save_request_profile,
    ORIG_SAVE_REQUEST_PROFILE,
    "save_request_profile"
);
define_trace_hook!(hook_request_save, ORIG_REQUEST_SAVE, "request_save");
define_trace_hook!(
    hook_current_slot_load,
    ORIG_CURRENT_SLOT_LOAD,
    "save_dispatch_system"
);
define_trace_hook!(hook_continue_load, ORIG_CONTINUE_LOAD, "save_write_to_slot");
define_trace_hook!(
    hook_combined_load,
    ORIG_COMBINED_LOAD,
    "save_dispatch_combined"
);
define_trace_hook!(hook_map_load, ORIG_MAP_LOAD, "save_dispatch_entry0b");
define_trace_hook!(
    hook_save_load_state_init,
    ORIG_SAVE_LOAD_STATE_INIT,
    "save_load_state_init"
);
define_trace_hook!(hook_b80_preview, ORIG_B80_PREVIEW, "b80_preview");
define_trace_hook!(hook_title_confirm, ORIG_TITLE_CONFIRM, "title_confirm");
define_trace_hook!(
    hook_request_load_slot,
    ORIG_REQUEST_LOAD_SLOT,
    "request_load_slot"
);
define_trace_hook!(
    hook_request_profile_read,
    ORIG_REQUEST_PROFILE_READ,
    "request_profile_read"
);
define_trace_hook!(hook_b80_poll, ORIG_B80_POLL, "b80_poll");
define_trace_hook!(hook_slot_deser, ORIG_SLOT_DESER, "slot_deser");
define_trace_hook!(hook_dispatcher2, ORIG_DISPATCHER2, "movemap_dispatcher2");
define_trace_hook!(
    hook_dosave_stuff,
    ORIG_DOSAVE_STUFF,
    "movemap_do_save_stuff"
);
define_trace_hook!(hook_map_request_do, ORIG_MAP_REQUEST_DO, "map_request_do");
define_trace_hook!(hook_map_work, ORIG_MAP_WORK, "map_work");
define_trace_hook!(hook_cap_setstate, ORIG_CAP_SETSTATE, "cap_setstate");
define_trace_hook!(
    hook_cap_load_activate,
    ORIG_CAP_LOAD_ACTIVATE,
    "cap_load_activate"
);
define_trace_hook!(
    hook_cap_load_activate2,
    ORIG_CAP_LOAD_ACTIVATE2,
    "cap_load_activate2"
);
define_trace_hook!(hook_cap_builder, ORIG_CAP_BUILDER, "cap_builder");
define_trace_hook!(
    hook_cap_selector_tick,
    ORIG_CAP_SELECTOR_TICK,
    "cap_selector_tick"
);
define_trace_hook!(hook_cap_menu_deser, ORIG_CAP_MENU_DESER, "cap_menu_deser");
define_trace_hook!(
    hook_cap_dialog_factory,
    ORIG_CAP_DIALOG_FACTORY,
    "cap_dialog_factory"
);
define_trace_hook!(
    hook_menu_window_job_ctor,
    ORIG_MENU_WINDOW_JOB_CTOR,
    "menu_window_job_ctor"
);
define_trace_hook!(
    hook_menu_window_job_native_ctor_b,
    ORIG_MENU_WINDOW_JOB_NATIVE_CTOR_B,
    "menu_window_job_native_ctor_b"
);
define_trace_hook!(
    hook_menu_window_job_idle_ctor,
    ORIG_MENU_WINDOW_JOB_IDLE_CTOR,
    "menu_window_job_idle_ctor"
);

static HOOKS: &[HookSpec] = &[
    HookSpec {
        name: "menu_continue_wrapper",
        rva: 0x82cf60,
        detour: hook_menu_continue_wrapper,
        original: &ORIG_MENU_CONTINUE_WRAPPER,
    },
    HookSpec {
        name: "menu_new_or_load_wrapper",
        rva: 0x82cf20,
        detour: hook_menu_new_or_load_wrapper,
        original: &ORIG_MENU_NEW_OR_LOAD_WRAPPER,
    },
    HookSpec {
        name: "menu_other_load_wrapper",
        rva: 0x82cfa0,
        detour: hook_menu_other_load_wrapper,
        original: &ORIG_MENU_OTHER_LOAD_WRAPPER,
    },
    HookSpec {
        name: "native_submit",
        rva: 0x7adbc0,
        detour: hook_native_submit,
        original: &ORIG_NATIVE_SUBMIT,
    },
    HookSpec {
        name: "result_event_handler",
        rva: 0x748180,
        detour: hook_result_event_handler,
        original: &ORIG_RESULT_EVENT_HANDLER,
    },
    HookSpec {
        name: "result_action_builder",
        rva: 0x747d00,
        detour: hook_result_action_builder,
        original: &ORIG_RESULT_ACTION_BUILDER,
    },
    HookSpec {
        name: "result_event_wrapper_builder",
        rva: 0x745d60,
        detour: hook_result_event_wrapper_builder,
        original: &ORIG_RESULT_EVENT_WRAPPER_BUILDER,
    },
    HookSpec {
        name: "task_enqueue",
        rva: 0x7a8e90,
        detour: hook_task_enqueue,
        original: &ORIG_TASK_ENQUEUE,
    },
    HookSpec {
        name: "set_save_slot",
        rva: 0x67bb10,
        detour: hook_set_save_slot,
        original: &ORIG_SET_SAVE_SLOT,
    },
    HookSpec {
        name: "save_request_profile",
        rva: 0x67b720,
        detour: hook_save_request_profile,
        original: &ORIG_SAVE_REQUEST_PROFILE,
    },
    HookSpec {
        name: "request_save",
        rva: 0x67b820,
        detour: hook_request_save,
        original: &ORIG_REQUEST_SAVE,
    },
    HookSpec {
        name: "save_dispatch_system",
        rva: 0x67c870,
        detour: hook_current_slot_load,
        original: &ORIG_CURRENT_SLOT_LOAD,
    },
    HookSpec {
        name: "save_write_to_slot",
        rva: 0x67ca50,
        detour: hook_continue_load,
        original: &ORIG_CONTINUE_LOAD,
    },
    HookSpec {
        name: "save_dispatch_combined",
        rva: 0x67cc40,
        detour: hook_combined_load,
        original: &ORIG_COMBINED_LOAD,
    },
    HookSpec {
        name: "save_dispatch_entry0b",
        rva: 0x67cf10,
        detour: hook_map_load,
        original: &ORIG_MAP_LOAD,
    },
    HookSpec {
        name: "save_load_state_init",
        rva: 0x67c330,
        detour: hook_save_load_state_init,
        original: &ORIG_SAVE_LOAD_STATE_INIT,
    },
    HookSpec {
        name: "b80_preview",
        rva: 0x67c7e0,
        detour: hook_b80_preview,
        original: &ORIG_B80_PREVIEW,
    },
    HookSpec {
        name: "title_confirm",
        rva: 0xb0fd50,
        detour: hook_title_confirm,
        original: &ORIG_TITLE_CONFIRM,
    },
    HookSpec {
        name: "request_load_slot",
        rva: 0x67c500,
        detour: hook_request_load_slot,
        original: &ORIG_REQUEST_LOAD_SLOT,
    },
    HookSpec {
        name: "request_profile_read",
        rva: 0x67c4a0,
        detour: hook_request_profile_read,
        original: &ORIG_REQUEST_PROFILE_READ,
    },
    HookSpec {
        name: "b80_poll",
        rva: 0x67a480,
        detour: hook_b80_poll,
        original: &ORIG_B80_POLL,
    },
    HookSpec {
        name: "slot_deser",
        rva: 0x67c590,
        detour: hook_slot_deser,
        original: &ORIG_SLOT_DESER,
    },
    HookSpec {
        name: "movemap_dispatcher2",
        rva: 0xafd060,
        detour: hook_dispatcher2,
        original: &ORIG_DISPATCHER2,
    },
    HookSpec {
        name: "movemap_do_save_stuff",
        rva: 0xafd2b0,
        detour: hook_dosave_stuff,
        original: &ORIG_DOSAVE_STUFF,
    },
    HookSpec {
        name: "map_request_do",
        rva: 0x8383d0,
        detour: hook_map_request_do,
        original: &ORIG_MAP_REQUEST_DO,
    },
    HookSpec {
        name: "map_work",
        rva: 0x830f90,
        detour: hook_map_work,
        original: &ORIG_MAP_WORK,
    },
    HookSpec {
        name: "cap_setstate",
        rva: 0xb0f530,
        detour: hook_cap_setstate,
        original: &ORIG_CAP_SETSTATE,
    },
    HookSpec {
        name: "cap_load_activate",
        rva: 0x9a5cc0,
        detour: hook_cap_load_activate,
        original: &ORIG_CAP_LOAD_ACTIVATE,
    },
    HookSpec {
        name: "cap_load_activate2",
        rva: 0x9ade60,
        detour: hook_cap_load_activate2,
        original: &ORIG_CAP_LOAD_ACTIVATE2,
    },
    HookSpec {
        name: "cap_builder",
        rva: 0x8279b0,
        detour: hook_cap_builder,
        original: &ORIG_CAP_BUILDER,
    },
    HookSpec {
        name: "cap_selector_tick",
        rva: 0x8281f0,
        detour: hook_cap_selector_tick,
        original: &ORIG_CAP_SELECTOR_TICK,
    },
    HookSpec {
        name: "cap_menu_deser",
        rva: 0x82d6e0,
        detour: hook_cap_menu_deser,
        original: &ORIG_CAP_MENU_DESER,
    },
    HookSpec {
        name: "cap_dialog_factory",
        rva: 0x81fe00,
        detour: hook_cap_dialog_factory,
        original: &ORIG_CAP_DIALOG_FACTORY,
    },
    HookSpec {
        name: "menu_window_job_ctor",
        rva: 0x7adbf0,
        detour: hook_menu_window_job_ctor,
        original: &ORIG_MENU_WINDOW_JOB_CTOR,
    },
    HookSpec {
        name: "menu_window_job_native_ctor_b",
        rva: 0x7ade30,
        detour: hook_menu_window_job_native_ctor_b,
        original: &ORIG_MENU_WINDOW_JOB_NATIVE_CTOR_B,
    },
    HookSpec {
        name: "menu_window_job_idle_ctor",
        rva: 0x7ae2b0,
        detour: hook_menu_window_job_idle_ctor,
        original: &ORIG_MENU_WINDOW_JOB_IDLE_CTOR,
    },
    // title_native_ready_733150 removed 2026-08-30. The address is real and unchanged (1.16.2
    // `bool FUN_140733150(SceneObjProxy*)` -> `(scaleformValue.dataType & 0x8f) != 0`, "is this
    // proxy bound to a real GFx display object"; byte-identical at 2.7.2.0 0x734450). It was dropped
    // because observing it here was duplicative and, co-loaded, actively misleading:
    //
    //   DUPLICATIVE. `er-quickload` detours the same prologue for the same purpose as
    //   `cap_title_native_ready` (its `trace/menu_trace_hooks.rs`, via
    //   `er_title_flow::TITLE_NATIVE_READY_PREDICATE_RVA`), gated by `trace_continue_enabled()` =
    //   `product_autoload_enabled()` -- i.e. On in a default product run. Every profile that
    //   carries this DLL also carries the product (`~/Elden/group-1170.me3`,
    //   `main-all-dlls.me3`, `main-all-dlls-no-mushroom.me3`, `invasion-path-test.me3`), so the
    //   observation was never actually lost by removing it here.
    //
    //   Misleading. `er-armament-icons` calls this predicate -- it does not detour it -- from
    //   `hud_badge.rs` and four sites in its `lib.rs`, on the TilePopulate path (its own counters
    //   heartbeat every 512 fires). `trace_hook` writes two unthrottled lines per call, each
    //   embedding a `snapshot()` that reads GameMan, under the log file's mutex. In the profiles
    //   above that meant every inventory tile emitting a pair of log lines labelled
    //   `title_native_ready_733150` -- attributing another DLL's icon queries to the native title
    //   flow, in a DLL whose entire product is a truthful trace.
    //
    // This is the one prologue this crate shared with a shell it had no union relationship to, and
    // `scripts/check-shared-hook-rvas.py` is what surfaced it. Restoring the row is legitimate for
    // a standalone title-flow investigation (`~/Elden/sweep-er-reload-trace.me3`, no product, no
    // armament-icons); if you do, expect the gate to ask for a conflict-table entry naming
    // er-armament-icons, and read that entry's reasoning before writing one.
    HookSpec {
        name: "finalize_advancer",
        rva: 0xafbeb0,
        detour: hook_finalize_advancer,
        original: &ORIG_FINALIZE_ADVANCER,
    },
    // loadlist_init_aec480 (STEP_MoveMap_LoadlistInit) removed 2026-08-30: 0xaec480 was never a
    // function entry on any build. On 1.16.2 it is inside `mov word ptr [rbp-0x28], r12w`
    // (`66 44 89 65 d8`, 0x140aec47f..0x140aec483), so the five-byte JMP truncated a live
    // instruction, and the crate's own 1.16.2 trace -- 323,338 lines -- never logged this hook
    // once. The real entry was 0xaec570 on 1.16.2 (`er_game_base::rva::STEP_MOVEMAP_LOADLIST_INIT_RVA`,
    // 0xaedd40 on 2.7.2.0); the
    // shift tool mislanded at 0xaec480 in its -0xf0 sub-region, as
    // `er-title-flow/src/title_load_step_hooks.rs` already recorded. That entry is a product hook
    // (the union chains a base MinHook the product owns and the trace-DLL copy never fired), so
    // this crate does not re-derive it -- it drops the wrong address rather than carrying it.
    HookSpec {
        name: "child_teardown",
        rva: CHILD_TEARDOWN_RVA,
        detour: hook_child_teardown,
        original: &ORIG_CHILD_TEARDOWN,
    },
    // child_done_query_eb5530 (2.7.2.0 rva 0xeb8a90, 1.16.2 0xeb5530 -- the child-done query
    // STEP_MoveMap_Update calls, tearing the MoveMapStep child down when it returns nonzero)
    // removed: the product DLL now owns that prologue with its override hook
    // (child_done_query_override_detour); a second trace hook here would chain and muddy the override.
    // Its log-only trace detour + statics were deleted with the spec entry rather than left parked.
];

/// Install every trace observer, through `er-hook`'s gated registrar -- and resolve exactly once.
///
/// There used to be two paths here and only one of them was safe. The "unioned" path handed the
/// address to the product DLL's `er_effects_union_register_2720`, which resolves it; the standalone
/// fallback called the raw MinHook externs on `base + spec.rva`, which does not. Which one a boot
/// took depended on a poll of the product module, so the single-DLL sweep profile -- and any
/// multi-DLL run where the product loaded late -- patched 34 stale 1.16.2 addresses into the live
/// 1.17 image. Both paths now go through the gate.
///
/// Every `spec.rva` is a 2.7.2.0 rva. Each branch below is given `base + rva` and checks it once:
/// the product's export checks it for the product-present branch, `register_union_hook` for the
/// standalone one. Each check is the build test plus the 2.7.2.0 detour allowlist, so an address
/// is either hooked as written or refused, never moved. (While the rvas were 1.16.2 and the check
/// translated them, a second resolve could move a detour into a different function and report
/// success; that is why this code never resolves twice.)
fn install_hooks() {
    reset_log_file();
    log_line(format_args!(
        "er-reload-trace attach: trampoline/log-only build; no input, save redirect, autoload, game task, or game-state writes"
    ));
    let Some(base) = game_base() else {
        log_line(format_args!("install abort: game module base unresolved"));
        return;
    };
    // Asked once, and no longer load-bearing for safety: a timeout now costs a chained handler,
    // not an ungated write. `er-hook` owns the module lookup so this crate no longer carries its
    // own `GetProcAddress` poll.
    let product =
        er_hook::resolve_product_union_register(PRODUCT_RESOLVE_TRIES, PRODUCT_RESOLVE_SLEEP_MS);
    log_line(format_args!(
        "hook routing: product DLL {} -- all {} addresses are resolved for the running build (or REFUSED) before MinHook sees them",
        if product.is_some() {
            "present; handlers chain on its single MinHook instance via er_effects_union_register_2720"
        } else {
            "absent; this DLL's own er_hook union owns the prologues"
        },
        HOOKS.len()
    ));
    let mut armed = 0_usize;
    let mut refused = 0_usize;
    let mut skipped = 0_usize;
    for spec in HOOKS {
        if product.is_some() && PRODUCT_OWNED_SKIP_RVAS.contains(&spec.rva) {
            skipped += 1;
            log_line(format_args!(
                "hook {} rva=0x{:x} SKIPPED -- the product DLL owns this prologue with a bare MinHook; registering here would preempt its critical reload hook",
                spec.name, spec.rva
            ));
            continue;
        }
        if install_one(base, spec, product) {
            armed += 1;
        } else {
            refused += 1;
        }
    }
    log_line(format_args!(
        "install complete armed={armed} refused={refused} skipped={skipped} of {} {}",
        HOOKS.len(),
        snapshot()
    ));
}

/// Register one trace observer, returning whether it is armed.
///
/// A refusal is per hook and never aborts the installer. The value of this DLL is the observers
/// whose addresses the running build does have, and taking the whole run down because one row is
/// missing from the map trades a diagnosable partial trace for no trace at all -- the same rule
/// `er-better-refills` and `system_quit_ownership_repro.rs` follow (bd
/// `one-refused-hook-must-not-abort-the-installer-2026-08-30`).
fn install_one(base: usize, spec: &HookSpec, product: Option<er_hook::UnionRegisterFn>) -> bool {
    let requested = base + spec.rva;
    let outcome = match product {
        // SAFETY: the export's C-ABI shape is fixed by the product DLL, `spec.detour` is exactly
        // its `UnionFn`, and `spec.original` is a live `'static` cell in this image. The address is
        // handed over unchecked on purpose -- the export checks it, so it is checked once (see
        // `install_hooks`).
        Some(register) => match unsafe { register(requested, spec.detour, spec.original.as_ptr()) }
        {
            0 => Ok("product union"),
            code => Err(format!("er_effects_union_register_2720 rc={code}")),
        },
        // SAFETY: same handler/slot contract; `register_union_hook` checks the address against the
        // running build before MinHook is given it, and refuses rather than patching a stale one.
        None => {
            match unsafe { er_hook::register_union_hook(requested, spec.detour, spec.original) } {
                Ok(()) => Ok("local union"),
                Err(status) => Err(format!("{status:?}")),
            }
        }
    };
    match outcome {
        Ok(route) => {
            log_line(format_args!(
                "hook {} rva=0x{:x} requested=0x{requested:x} ARMED via {route}",
                spec.name, spec.rva
            ));
            true
        }
        Err(why) => {
            log_line(format_args!(
                "hook {} rva=0x{:x} requested=0x{requested:x} REFUSED ({why}) -- nothing was written at this address; continuing with the remaining hooks",
                spec.name, spec.rva
            ));
            false
        }
    }
}

/// Windows loader entry point.
///
/// # Safety
/// Called by the Windows loader with the loader lock held. `module`/`reserved` are the loader's own
/// pointers and are not dereferenced here; the attach path only spawns a thread, so no loader-lock
/// reentrancy is introduced.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn DllMain(
    _module: *mut c_void,
    reason: u32,
    _reserved: *mut c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // One sink for this DLL's hook + address lines. Without it a refused address is
        // silent here, because every cdylib links its own copy of er-hook/er-game-base.
        // A rust_panic in a cdylib loaded into the game is otherwise anonymous: the message goes to a
        // stderr nobody reads, and what survives is a 0xe06d7363 record naming the module and nothing
        // else. Two boots were lost to one before this existed. See er_game_base::panic_report.
        er_game_base::panic_report::report_panics_to("er-reload-trace", log_line);
        er_hook::set_hook_logger(log_line);
        let _ = std::thread::Builder::new()
            .name("er-reload-trace-install".to_owned())
            .spawn(install_hooks);
    }
    DLL_MAIN_SUCCESS
}
