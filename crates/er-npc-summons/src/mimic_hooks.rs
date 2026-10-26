//! The custom Mimic Tear: detours scoped to one `BuddyGenerator` call, and no write to a param row.
//!
//! Using Mimic Tear Ashes runs the game's own spirit-ash flow: the item use, FP cost, summoning
//! pool range check, one-summon replacement, dismissal and the Seamless broadcast all stay native.
//! Only the values `BuddyGenerator` reads change, and only inside a call whose request is a Mimic
//! Tear (bd `mimic-tear-summon-hijack-1171-2026-10-06`, prototype `scripts/frida/mimic-tear-turtles.js`):
//!
//! 1. `BuddyGenerator` detour: when `SummonBuddyManager+0x20` is `207000 + level` and the config
//!    has companions, mark this thread as hijacking for the duration of the original call.
//! 2. `GetBuddyList` detour: every list entry becomes the human row 20700001 (so no request
//!    carries 20700000 and the copy-the-player branch never arms), and the list is grown or
//!    shrunk to one entry per companion with the game's own node insert.
//! 3. `GetBuddyParam` detour: each first-loop read of the human row is answered with a copy of the
//!    row carrying the next companion's npc, think, charaInit and formation offset, which
//!    `BuddyGenerator` copies out of the lookup it passed.
//! 4. `CreateSummonChr` detour: for each companion `BuddyGenerator` builds, the call is built from a
//!    copy of its `CharaInitParam` row carrying its build's gear. The call is synchronous:
//!    `ChrSet::SpawnChr` -> `CreateCharacter` looks the row up and mints the gear before it returns
//!    (see `er_npc_summons_core::dress` for the evidence).
//! 5. `GetCharaInitParam`, through the union: during that one call, on that thread, the lookup of
//!    the companion's row is answered with the dressed copy.
//!
//! # No resident row is written
//!
//! Seamless Co-op's matchmaking `lobby_key` is a fingerprint of the param tables as loaded,
//! compared for equality, so writing any resident row puts the player in a lobby pool of their own
//! (bd `death-persist-is-the-lobby-key-mover-seamless-201-2720-2026-10-22`). Both getters this
//! module answers fill `out = {id @+0, row* @+8}` (`getter_sites.rs`), and their callers read the
//! row through `out+8` after they return: `BuddyGenerator` at 2.7.2.0 `0x1404bc083`,
//! `CreateCharacter` through the row applier `0x140258bd0`. So the detours read the row the getter
//! found, change a copy, and replace `out+8` with the copy. The copies are kept by
//! `er_npc_summons_core::row_copy`: interned by content, never freed, never written once handed
//! out, so any pointer the game keeps stays valid and repeated summons reuse the same few copies.
//!
//! Two readers a copy cannot reach. `UpdateTriggerSpEffectMap` (2.7.2.0 `0x1404bd040`) walks the
//! resident BuddyParam rows by index for `triggerSpEffectId`; the copy leaves that field as the row
//! has it, so nothing is lost. Three debug-menu readers walk the resident `CharaInitParam` rows by
//! index; none is on the summon path (`er_npc_summons_core::dress`).
//!
//! Each companion built is also named for its slot ([`crate::names`]); a companion's Lua brain is
//! loaded by [`crate::brains`], keyed by the think id the plan gives it.
//!
//! # Other players
//!
//! The same `CreateSummonChr` detour runs on every other player's machine when the game builds
//! their copy of a companion from packet 78 (`fromNetwork = 1`). Each companion built here is
//! announced to them from inside this detour ([`crate::net::announce_built`]), and a copy built
//! here from someone else's packet is dressed and named from what that player announced
//! ([`remote_create`]), through the same row copy and the same `GetCharaInitParam` handler.
//!
//! A player without this DLL is reached through the game itself. Every human companion's request
//! names the sync-target row [`mimic::SYNC_CHARA_INIT`] in place of its body's row, and this
//! player's lookup of that row is answered with a copy of the body's row, so the summoner's
//! companion is built as before. The other player's copy is built from their own row 26050, which
//! lets their game accept the outfit [`crate::gear_sync`] broadcasts afterwards.

#![cfg(windows)]

use core::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};

use er_game_base::game_build::{describe_build, is_supported_build};
use er_game_base::mem::{
    game_module_base, game_rva_for_hook, game_rva_named, read_bytes, safe_read_i32, safe_read_u8,
    safe_read_usize,
};
use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook, UnionFn};
use er_npc_summons_core::dress::{CHARA_INIT_ROW_SIZE, CharaInitGear};
use er_npc_summons_core::mimic::{self, BUDDY_ROW_SIZE, HUMAN_ROW, MIMIC_TRIGGER, RowValues};
use er_npc_summons_core::net::{Basis, Triple};
use er_npc_summons_core::row_copy::RowCopies;
use windows::Win32::System::Diagnostics::Debug::RtlCaptureStackBackTrace;
use windows::Win32::System::Threading::GetCurrentThreadId;

use crate::addr;
use crate::log::summons_log;

// The two getters' 2.7.2.0 addresses and window offsets, shared with build.rs.
include!(concat!(env!("CARGO_MANIFEST_DIR"), "/getter_sites.rs"));
// The windows themselves, assembled by build.rs from named instructions and checked against the
// 2.7.2.0 image.
include!(concat!(env!("OUT_DIR"), "/generated_getter_windows.rs"));

// The row sizes the copies carry are the paramdef's, as fromsoftware-rs lays the rows out.
const _: () = assert!(core::mem::size_of::<eldenring::param::BUDDY_PARAM_ST>() == BUDDY_ROW_SIZE);
const _: () =
    assert!(core::mem::size_of::<eldenring::param::CHARACTER_INIT_PARAM>() == CHARA_INIT_ROW_SIZE);

/// `(offset from the getter's entry, window)` pairs that must all match before a getter's out
/// struct is trusted: the frame setup, the table index that names the table, and both stores of
/// `out = {id, row*}`.
type Pins = &'static [(usize, &'static [u8])];

const BUDDY_PARAM_PINS: Pins = &[
    (GETTER_FRAME_WINDOW_OFFSET, BUDDY_PARAM_FRAME_WINDOW),
    (GETTER_TABLE_WINDOW_OFFSET, BUDDY_PARAM_TABLE_WINDOW),
    (GETTER_OUT_STORE_WINDOW_OFFSET, BUDDY_PARAM_OUT_STORE_WINDOW),
    (
        GETTER_NEGATIVE_ID_STORE_WINDOW_OFFSET,
        BUDDY_PARAM_NEGATIVE_ID_STORE_WINDOW,
    ),
];

const CHARA_INIT_PARAM_PINS: Pins = &[
    (GETTER_FRAME_WINDOW_OFFSET, CHARA_INIT_PARAM_FRAME_WINDOW),
    (GETTER_TABLE_WINDOW_OFFSET, CHARA_INIT_PARAM_TABLE_WINDOW),
    (
        GETTER_OUT_STORE_WINDOW_OFFSET,
        CHARA_INIT_PARAM_OUT_STORE_WINDOW,
    ),
    (
        GETTER_NEGATIVE_ID_STORE_WINDOW_OFFSET,
        CHARA_INIT_PARAM_NEGATIVE_ID_STORE_WINDOW,
    ),
];

/// The companions' row values, refreshed whenever the config is reloaded.
static PLAN: Mutex<Vec<RowValues>> = Mutex::new(Vec::new());

/// The thread inside a hijacked `BuddyGenerator` call, or 0.
static HIJACK_TID: AtomicU32 = AtomicU32::new(0);
/// First-loop reads of the human row seen in the current hijacked call.
static READS: AtomicUsize = AtomicUsize::new(0);
/// Where `BuddyGenerator`'s first-loop `GetBuddyParam` call returns, or 0 when unverified.
static LOOP1_RET: AtomicUsize = AtomicUsize::new(0);

static ORIG_GENERATOR: AtomicUsize = AtomicUsize::new(0);
static ORIG_LIST: AtomicUsize = AtomicUsize::new(0);
static ORIG_PARAM: AtomicUsize = AtomicUsize::new(0);
static ORIG_CREATE: AtomicUsize = AtomicUsize::new(0);
/// The next union handler or the trampoline of `GetCharaInitParam`.
static ORIG_CHARA_INIT_GETTER: AtomicUsize = AtomicUsize::new(0);
/// Set once the `GetCharaInitParam` handler is registered; without it no companion is dressed.
static CHARA_INIT_HOOKED: AtomicBool = AtomicBool::new(false);

/// The values each first-loop read was given in the current hijacked call, not yet built.
static PENDING: Mutex<Vec<RowValues>> = Mutex::new(Vec::new());
/// Companions built in the current hijacked call that wore their build's gear.
static DRESSED: AtomicUsize = AtomicUsize::new(0);

/// Every row copy handed to the game, BuddyParam and CharaInitParam alike, by content.
static COPIES: Mutex<RowCopies> = Mutex::new(RowCopies::new());

/// The `CharaInitParam` row one `CreateSummonChr` call is dressing, and in what.
struct Dress {
    /// The row the call asks for.
    id: u32,
    /// The row whose contents the copy starts from: the body's own, when the request names
    /// [`mimic::SYNC_CHARA_INIT`] in its place, otherwise `id`.
    source: u32,
    /// The build's gear, or `None` to keep the source row's own.
    gear: Option<CharaInitGear>,
}
/// Set for exactly the length of one dressed `CreateSummonChr` call.
static DRESS: Mutex<Option<Dress>> = Mutex::new(None);
/// The thread making that call, or 0. The getter's handler reads only this on its common path.
static DRESS_TID: AtomicU32 = AtomicU32::new(0);
/// Lookups of the dressed row answered with the copy during the current call.
static DRESS_SERVED: AtomicUsize = AtomicUsize::new(0);
/// The copy last served, for the log.
static DRESS_COPY: AtomicUsize = AtomicUsize::new(0);

type GeneratorFn = unsafe extern "system" fn(usize);
type ListFn = unsafe extern "system" fn(usize, i32, usize);
type ParamFn = unsafe extern "system" fn(usize, i32) -> usize;
type ListInsertFn = unsafe extern "system" fn(usize, usize, usize, *const i32) -> usize;
/// `CreateSummonChr`, the same 18 arguments `game.rs` calls it with. The four flags are taken as
/// `u8`: the detour forwards whatever byte the caller passed rather than asserting it is 0 or 1.
type CreateFn = unsafe extern "system" fn(
    usize, // SummonBuddyManager*
    usize, // creator event id*
    usize, // creator steam id*
    usize, // BlockId*
    u32,   // unused, 0xffffffff
    usize, // FieldInsHandle*
    i32,   // npcParamId
    i32,   // npcThinkId
    i32,   // charaInitParam
    usize, // block-local position*
    f32,   // yaw
    u8,    // spawnHidden
    u8,    // hasMount
    u32,   // buddyStoneParamId
    u32,   // buddyParamId
    u32,   // dopingLevel
    u8,    // fromNetwork
    u8,    // hasMoghGreatRune
) -> usize;

/// Replace the companion plan (from a config reload).
pub(crate) fn set_plan(plan: Vec<RowValues>) {
    if let Ok(mut slot) = PLAN.lock() {
        *slot = plan;
    }
}

fn plan() -> Vec<RowValues> {
    PLAN.lock().map(|p| p.clone()).unwrap_or_default()
}

fn this_thread() -> u32 {
    // SAFETY: no preconditions.
    unsafe { GetCurrentThreadId() }
}

/// The return address of the detour that calls this, i.e. the game's call site.
#[inline(never)]
fn caller_return_address() -> usize {
    let mut frames = [core::ptr::null_mut::<c_void>(); 1];
    // Skip this function's own frame; the next is the detour's return address.
    // SAFETY: writes at most one pointer into `frames`.
    let captured = unsafe { RtlCaptureStackBackTrace(2, &mut frames, None) };
    if captured == 0 { 0 } else { frames[0] as usize }
}

/// How many row copies exist, for the log.
fn copies_held() -> usize {
    COPIES.lock().map(|copies| copies.len()).unwrap_or(0)
}

unsafe extern "system" fn generator_detour(manager: usize) {
    let original = ORIG_GENERATOR.load(Ordering::Acquire);
    if original == 0 {
        return;
    }
    // SAFETY: the trampoline of the function this replaces.
    let original: GeneratorFn = unsafe { core::mem::transmute(original) };
    // SAFETY: fault-tolerant read of `SummonBuddyManager+0x20`.
    let request = unsafe { safe_read_i32(manager + addr::BUDDY_MANAGER_REQUEST) }.unwrap_or(-1);
    let level = mimic::mimic_level(request);
    if level.is_none() || plan().is_empty() || LOOP1_RET.load(Ordering::Acquire) == 0 {
        unsafe { original(manager) };
        return;
    }
    HIJACK_TID.store(this_thread(), Ordering::Release);
    READS.store(0, Ordering::Release);
    DRESSED.store(0, Ordering::Release);
    if let Ok(mut pending) = PENDING.lock() {
        pending.clear();
    }
    unsafe { original(manager) };
    HIJACK_TID.store(0, Ordering::Release);
    summons_log(format_args!(
        "mimic: Mimic Tear +{} summoned {} companion(s), {} dressed from a build URL; {} row \
         copies held, no resident param row written",
        level.unwrap_or(0),
        READS.load(Ordering::Acquire),
        DRESSED.load(Ordering::Acquire),
        copies_held()
    ));
}

fn hijacking() -> bool {
    HIJACK_TID.load(Ordering::Acquire) == this_thread()
}

/// # Safety
///
/// `address` must be a writable pointer-sized location in the game heap or on a game stack.
unsafe fn write_usize(address: usize, value: usize) {
    // SAFETY: caller's contract.
    unsafe { core::ptr::write_volatile(address as *mut usize, value) };
}

/// Rewrite the Mimic Tear's buddy list: every entry the human row, one entry per companion.
///
/// MSVC `std::list<int>`: list `+8` head node, `+0x10` size; node `+0` next, `+8` prev, `+0x10`
/// value. Growth uses the game's own insert, then the three link writes the game's own fill does
/// (2.7.2.0 `0x1404bcfd0..0x1404bd006`). Shrinking unlinks the surplus nodes and leaves them
/// allocated: a few bytes per summon, against freeing game-heap memory from outside the allocator
/// that owns it.
fn rewrite_list(list: usize, wanted: usize) -> Option<usize> {
    // SAFETY: fault-tolerant reads; the writes below go to nodes these reads just walked.
    let head = unsafe { safe_read_usize(list + 8) }.filter(|&h| h != 0)?;
    let mut node = unsafe { safe_read_usize(head) }?;
    let mut seen = 0usize;
    while node != head && node != 0 && seen < 64 {
        let next = unsafe { safe_read_usize(node) }?;
        if seen < wanted {
            unsafe { core::ptr::write_volatile((node + 0x10) as *mut i32, HUMAN_ROW) };
        } else {
            let prev = unsafe { safe_read_usize(node + 8) }?;
            unsafe {
                write_usize(prev, next);
                write_usize(next + 8, prev);
            }
            let size = unsafe { safe_read_usize(list + 0x10) }?;
            unsafe { write_usize(list + 0x10, size.saturating_sub(1)) };
        }
        seen += 1;
        node = next;
    }
    // SAFETY: `node*(List*, next, prev, int* value)`, the insert the fill uses.
    let insert = game_rva_named(addr::BUDDY_LIST_INSERT, "BUDDY_LIST_INSERT").ok()?;
    let insert: ListInsertFn = unsafe { core::mem::transmute(insert) };
    let value = HUMAN_ROW;
    while unsafe { safe_read_usize(list + 0x10) }? < wanted {
        let tail = unsafe { safe_read_usize(head + 8) }?;
        let added = unsafe { insert(list, head, tail, &value) };
        if added == 0 {
            break;
        }
        let size = unsafe { safe_read_usize(list + 0x10) }?;
        unsafe {
            write_usize(list + 0x10, size + 1);
            write_usize(head + 8, added);
            let prev = safe_read_usize(added + 8)?;
            write_usize(prev, added);
        }
    }
    unsafe { safe_read_usize(list + 0x10) }
}

unsafe extern "system" fn list_detour(manager: usize, sp_effect: i32, list: usize) {
    let original = ORIG_LIST.load(Ordering::Acquire);
    if original == 0 {
        return;
    }
    // SAFETY: the trampoline of the function this replaces.
    let original: ListFn = unsafe { core::mem::transmute(original) };
    unsafe { original(manager, sp_effect, list) };
    if !hijacking() || sp_effect != MIMIC_TRIGGER || list == 0 {
        return;
    }
    let wanted = plan().len();
    if rewrite_list(list, wanted) != Some(wanted) {
        summons_log(format_args!(
            "mimic: could not shape the buddy list to {wanted} entries"
        ));
    }
}

/// Answer one getter call with a copy: read the row `out+8` points at, let `change` lay this DLL's
/// values into a copy of its `size` bytes, and point `out+8` at the interned copy. The row itself
/// is only read. On `Err`, `out` is left exactly as the getter filled it.
fn serve_copy(
    out: usize,
    size: usize,
    change: impl FnOnce(&mut [u8]) -> bool,
) -> Result<usize, String> {
    serve_copy_of(out, None, size, change)
}

/// [`serve_copy`], starting from the resident row at `source` instead of the one the getter found
/// when it is given.
fn serve_copy_of(
    out: usize,
    source: Option<usize>,
    size: usize,
    change: impl FnOnce(&mut [u8]) -> bool,
) -> Result<usize, String> {
    let slot = out + GETTER_OUT_ROW_SLOT;
    // SAFETY: fault-tolerant read of the out struct the getter just filled.
    let found = unsafe { safe_read_usize(slot) }
        .filter(|&row| row != 0)
        .ok_or("the getter found no row")?;
    let row = source.unwrap_or(found);
    let mut bytes = vec![0u8; size];
    // SAFETY: a fault-tolerant read of the resident row; nothing is written to it.
    if !unsafe { read_bytes(row, &mut bytes) } {
        return Err(format!("the row at 0x{row:x} did not read"));
    }
    if !change(&mut bytes) {
        return Err(format!(
            "a copy of 0x{size:x} bytes is too short for its fields"
        ));
    }
    let copy = COPIES
        .lock()
        .map_err(|_| "the row-copy table is poisoned".to_owned())?
        .intern(&bytes)
        .map_err(|why| why.to_string())?;
    // SAFETY: `slot` is the caller's lookup struct, whose row pointer the getter just wrote; the
    // copy outlives every reader (`er_npc_summons_core::row_copy`).
    unsafe { write_usize(slot, copy) };
    Ok(copy)
}

unsafe extern "system" fn param_detour(lookup: usize, id: i32) -> usize {
    let original = ORIG_PARAM.load(Ordering::Acquire);
    if original == 0 {
        return 0;
    }
    let ret = caller_return_address();
    // SAFETY: the trampoline of the function this replaces.
    let original: ParamFn = unsafe { core::mem::transmute(original) };
    let result = unsafe { original(lookup, id) };
    if !hijacking() || id != HUMAN_ROW || ret != LOOP1_RET.load(Ordering::Acquire) {
        return result;
    }
    // SAFETY: fault-tolerant read of the lookup's row pointer.
    if unsafe { safe_read_usize(lookup + GETTER_OUT_ROW_SLOT) }.is_none_or(|row| row == 0) {
        return result;
    }
    let read = READS.fetch_add(1, Ordering::AcqRel);
    let Some(values) = mimic::entry_for_read(&plan(), read) else {
        return result;
    };
    match serve_copy(lookup, BUDDY_ROW_SIZE, |row| values.write_into(row)) {
        Ok(_copy) => {
            if let Ok(mut pending) = PENDING.lock() {
                pending.push(values);
            }
        }
        Err(why) => summons_log(format_args!(
            "mimic: read {read} of row {HUMAN_ROW} (companion {}): {why}; this request keeps the \
             row's own values",
            values.slot
        )),
    }
    result
}

/// `GetCharaInitParam(out, id)` in the union's four-argument shape (it takes two).
unsafe extern "system" fn chara_init_getter(out: usize, id: usize, c: usize, d: usize) -> usize {
    let next = ORIG_CHARA_INIT_GETTER.load(Ordering::Acquire);
    if next == 0 {
        return 0;
    }
    // SAFETY: the next handler or the trampoline, both `UnionFn`.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    let result = unsafe { call(out, id, c, d) };
    // The common path, for every other lookup on every thread: one atomic load.
    let dressing = DRESS_TID.load(Ordering::Acquire);
    if dressing != 0 && out != 0 && dressing == this_thread() {
        // The id arrives in `edx`; the upper half of `rdx` is not part of it.
        serve_dressed(out, id as u32, call);
    }
    result
}

/// The resident `CharaInitParam` row `id`, looked up through `next` (the handler after this one,
/// or the getter itself) into a lookup struct of this function's own.
fn resident_chara_init_row(next: UnionFn, id: u32) -> Option<usize> {
    let mut lookup = [0usize; 4];
    // SAFETY: the getter fills `{id @+0, row* @+8}` of a lookup struct it is handed; this one is
    // 32 bytes on this stack and outlives the call.
    unsafe { next(lookup.as_mut_ptr() as usize, id as usize, 0, 0) };
    Some(lookup[GETTER_OUT_ROW_SLOT / core::mem::size_of::<usize>()]).filter(|&row| row != 0)
}

/// Answer the dressing call's lookup of its companion's row with the dressed copy.
fn serve_dressed(out: usize, id: u32, next: UnionFn) {
    let (source, gear) = match DRESS.lock() {
        Ok(dress) => match dress.as_ref() {
            Some(dress) if dress.id == id => (dress.source, dress.gear),
            _ => return,
        },
        Err(_) => return,
    };
    let source_row = if source == id {
        None
    } else {
        match resident_chara_init_row(next, source) {
            Some(row) => Some(row),
            None => {
                summons_log(format_args!(
                    "dress: GetCharaInitParam({id}): the body's row {source} was not found; this \
                     lookup gets row {id}'s own contents"
                ));
                return;
            }
        }
    };
    let sync = source_row.is_some();
    let change = |row: &mut [u8]| {
        gear.is_none_or(|gear| gear.write_into(row)) && (!sync || mimic::mark_sync_target(row))
    };
    match serve_copy_of(out, source_row, CHARA_INIT_ROW_SIZE, change) {
        Ok(copy) => {
            DRESS_SERVED.fetch_add(1, Ordering::AcqRel);
            DRESS_COPY.store(copy, Ordering::Release);
        }
        Err(why) => summons_log(format_args!(
            "dress: GetCharaInitParam({id}): {why}; this lookup gets the row's own gear"
        )),
    }
}

/// Dress the next `GetCharaInitParam(id)` lookups on this thread, until [`end_dress`].
fn begin_dress(id: u32, source: u32, gear: Option<CharaInitGear>) {
    if let Ok(mut dress) = DRESS.lock() {
        *dress = Some(Dress { id, source, gear });
    }
    DRESS_SERVED.store(0, Ordering::Release);
    DRESS_COPY.store(0, Ordering::Release);
    DRESS_TID.store(this_thread(), Ordering::Release);
}

/// Stop dressing; how many lookups were answered with the copy, and the copy's address.
fn end_dress() -> (usize, usize) {
    DRESS_TID.store(0, Ordering::Release);
    if let Ok(mut dress) = DRESS.lock() {
        *dress = None;
    }
    (
        DRESS_SERVED.load(Ordering::Acquire),
        DRESS_COPY.load(Ordering::Acquire),
    )
}

#[allow(clippy::too_many_arguments)]
unsafe extern "system" fn create_detour(
    manager: usize,
    event_id: usize,
    steam_id: usize,
    block: usize,
    unused: u32,
    handle: usize,
    npc: i32,
    think: i32,
    chara_init: i32,
    pos: usize,
    yaw: f32,
    hidden: u8,
    mount: u8,
    stone: u32,
    buddy_param: u32,
    doping: u32,
    from_network: u8,
    mogh: u8,
) -> usize {
    let original = ORIG_CREATE.load(Ordering::Acquire);
    if original == 0 {
        return 0;
    }
    // SAFETY: the trampoline of the function this replaces.
    let original: CreateFn = unsafe { core::mem::transmute(original) };
    let call = || unsafe {
        original(
            manager,
            event_id,
            steam_id,
            block,
            unused,
            handle,
            npc,
            think,
            chara_init,
            pos,
            yaw,
            hidden,
            mount,
            stone,
            buddy_param,
            doping,
            from_network,
            mogh,
        )
    };
    if from_network != 0 && !hijacking() {
        return remote_create(
            steam_id,
            handle,
            Triple {
                npc,
                think,
                chara_init,
            },
            call,
        );
    }
    if !hijacking() {
        return call();
    }
    let slot = PENDING
        .lock()
        .ok()
        .and_then(|mut pending| mimic::claim(&mut pending, npc, think, chara_init));
    let Some(slot) = slot else {
        summons_log(format_args!(
            "dress: CreateSummonChr(npc {npc}, think {think}, charaInit {chara_init}) matched no \
             companion; built as configured"
        ));
        return call();
    };
    let named = |chr: usize| {
        if chr != 0 {
            crate::names::name_companion(slot, chr);
            crate::team::adopt(slot, chr);
            // Before `BuddyGenerator` broadcasts packet 78 for it, so a peer usually holds the
            // announcement when its own `CreateSummonChr` runs.
            crate::net::announce_built(slot, chr);
            // After it, so every other player's copy exists when its outfit arrives.
            crate::gear_sync::companion_built(slot, chr);
        }
        chr
    };
    // The body's own row, which the request names the sync row in place of.
    let body = plan()
        .iter()
        .find(|values| values.slot == slot)
        .map_or(chara_init, |values| values.body_chara_init);
    let gear = match crate::dress::gear_for(slot) {
        Ok(found) => Some(found),
        Err(why) => {
            summons_log(format_args!(
                "dress: companion {slot}: no build ({why}); it wears charaInit {body}'s own gear"
            ));
            None
        }
    };
    if !CHARA_INIT_HOOKED.load(Ordering::Acquire) {
        summons_log(format_args!(
            "dress: companion {slot}: not dressed (GetCharaInitParam is not hooked on this build); \
             built in charaInit {chara_init}'s own gear"
        ));
        return named(call());
    }
    let (Ok(id), Ok(source)) = (u32::try_from(chara_init), u32::try_from(body)) else {
        summons_log(format_args!(
            "dress: companion {slot}: not dressed (charaInit {chara_init} builds no human, so \
             there is no row); built as configured"
        ));
        return named(call());
    };
    if gear.is_none() && source == id {
        return named(call());
    }
    begin_dress(id, source, gear.as_ref().map(|(gear, _)| *gear));
    let chr = call();
    let (served, copy) = end_dress();
    let chr = named(chr);
    if served == 0 {
        summons_log(format_args!(
            "dress: companion {slot}: built 0x{chr:x}, but GetCharaInitParam was never asked for \
             row {id} on this thread during the call, so it wears the row's own gear"
        ));
        return chr;
    }
    let from = if source == id {
        format!("a copy of charaInit {id}")
    } else {
        format!(
            "a copy of its body's charaInit {source}, marked a sync target and served as row {id} \
             so other players accept its outfit"
        )
    };
    match gear {
        Some((gear, build)) => {
            if chr != 0 {
                DRESSED.fetch_add(1, Ordering::AcqRel);
            }
            summons_log(format_args!(
                "dress: companion {slot}: built 0x{chr:x} from {from} (0x{copy:x}, served {served} \
                 time(s)) wearing build {build:?} (R {:?}, L {:?}, armour {:?}, talismans {:?}, \
                 ammo {:?}); the resident row was not written",
                gear.right, gear.left, gear.protectors, gear.talismans, gear.ammo
            ));
        }
        None => summons_log(format_args!(
            "dress: companion {slot}: built 0x{chr:x} from {from} (0x{copy:x}, served {served} \
             time(s)) in that row's own gear; the resident row was not written"
        )),
    }
    if chr != 0 {
        apply_face(
            &format!("companion {slot}"),
            crate::dress::face_for(slot),
            chr,
        );
    }
    chr
}

/// A character the game builds from another player's packet 78 (`fromNetwork = 1`, from
/// `SummonBuddyManager::CreateBuddyFromPacket`, 2.7.2.0 `0x1404b7d80`, call at `0x1404b7eed`).
/// `steam_id` points at the summoner's SteamID and `handle` at the `FieldInsHandle` the packet
/// carried, which the character is built under (`ChrSet::SpawnSummonBuddy`, `0x1404932f0`). That
/// spawn reaches `ChrInsFactory::CreateCharacter` (`0x140403e60`, called at `0x1404933dd`) before
/// it returns, so the companion's `CharaInitParam` lookup happens inside this call on this thread,
/// exactly as on the summoner's side.
///
/// When the summoner told this player which companion it is ([`crate::net`]), it is built from a
/// copy of its row wearing the summoner's build and named as the summoner named it. Anything else
/// is built exactly as the packet says, and recorded so a roster arriving later still names it.
fn remote_create(
    steam_id: usize,
    handle: usize,
    triple: Triple,
    call: impl FnOnce() -> usize,
) -> usize {
    // SAFETY: fault-tolerant reads of the caller's two eight-byte arguments.
    let owner = unsafe { safe_read_usize(steam_id) }.map(|v| v as u64);
    let handle = unsafe { safe_read_usize(handle) }.map(|v| v as u64);
    let (Some(owner), Some(handle)) = (owner, handle) else {
        return call();
    };
    let Triple {
        npc,
        think,
        chara_init,
    } = triple;
    let who = format!("owner {owner:#018x} handle 0x{handle:x}");
    let Some(resolved) = crate::net::resolve_remote(owner, handle, triple) else {
        let chr = call();
        crate::net::record_remote(owner, handle, triple, chr, None);
        summons_log(format_args!(
            "net: remote CreateSummonChr {who} (npc {npc}, think {think}, charaInit {chara_init}) \
             built 0x{chr:x} as the packet says: its summoner has announced no companion on this \
             body"
        ));
        return chr;
    };
    let entry = resolved.entry.clone();
    let label = format!(
        "remote companion {who} slot {} \"{}\" ({})",
        entry.slot,
        entry.name,
        match resolved.basis {
            Basis::Announced => "announced",
            Basis::Guessed => "guessed from its body",
        }
    );
    let gear = match entry.share_id.as_deref() {
        None => Err("its summoner gives it no build".to_owned()),
        Some(id) => crate::dress::remote_gear_for(id),
    };
    let dressed = match (gear, u32::try_from(chara_init)) {
        (Err(why), _) => Err(why),
        (Ok(_), Err(_)) => Err(format!("charaInit {chara_init} builds no human")),
        (Ok(_), Ok(_)) if !CHARA_INIT_HOOKED.load(Ordering::Acquire) => {
            Err("GetCharaInitParam is not hooked on this build".to_owned())
        }
        (Ok(found), Ok(id)) => Ok((found, id)),
    };
    let (chr, outcome) = match dressed {
        Err(why) => (
            call(),
            format!("not dressed ({why}), wears charaInit {chara_init}'s own gear"),
        ),
        Ok(((gear, build), id)) => {
            begin_dress(id, id, Some(gear));
            let chr = call();
            let (served, copy) = end_dress();
            let outcome = if served == 0 {
                format!(
                    "not dressed: GetCharaInitParam was never asked for row {id} on this thread \
                     during the call, so it wears the row's own gear"
                )
            } else {
                format!(
                    "dressed from a copy of charaInit {id} (0x{copy:x}, served {served} time(s)) \
                     wearing build {build:?}; the resident row was not written"
                )
            };
            (chr, outcome)
        }
    };
    crate::net::record_remote(owner, handle, triple, chr, Some(resolved));
    if chr != 0 {
        crate::names::set(chr, &entry.name);
        let face = entry
            .share_id
            .as_deref()
            .and_then(crate::dress::remote_face_for);
        apply_face(&label, face, chr);
    }
    summons_log(format_args!("net: {label}: built 0x{chr:x}, {outcome}"));
    chr
}

/// `ChrIns+0x580`: the character's `PlayerGameData`, which an NPC built from a `CharaInitParam`
/// row has too (its face block is filled from that row's `FaceParam`).
const CHR_PLAYER_GAME_DATA: usize = 0x580;

/// Give a freshly built companion its build's face, through the game's own
/// `PlayerGameData::CopyFaceDataFromBuffer` (the importer's [`adopt_build_face`]). Done on the
/// creation frame, while the model is still streaming in, so the hair and face-mesh ids land with
/// the sliders. Proven live 2026-10-06 by `scripts/frida/mimic-turtle-faces.js`: the same call
/// on the same frame gave three companions their blindfold colours.
///
/// [`adopt_build_face`]: er_build_import_runtime::face::adopt_build_face
fn apply_face(who: &str, face: Option<er_build_import_core::sliders::SliderMap>, chr: usize) {
    let Some(face) = face else {
        summons_log(format_args!(
            "dress: {who}: its build names no face; it keeps the body's own"
        ));
        return;
    };
    let (Ok(base), Some(pgd)) = (
        game_module_base(),
        // SAFETY: a fault-tolerant read off the character just built.
        unsafe { safe_read_usize(chr + CHR_PLAYER_GAME_DATA) }.filter(|&p| p != 0),
    ) else {
        summons_log(format_args!(
            "dress: {who}: 0x{chr:x} has no PlayerGameData; face not applied"
        ));
        return;
    };
    // SAFETY: the game thread, inside the CreateSummonChr detour, with `pgd` read off the
    // character the call just built.
    let outcome =
        unsafe { er_build_import_runtime::face::adopt_build_face(base, pgd, Some(&face)) };
    summons_log(format_args!("dress: {who}: face {}", outcome.label()));
}

/// Compare `pins` against the code at `entry` on the running build.
fn pins_match(name: &str, entry: usize, pins: Pins) -> Result<(), String> {
    for &(offset, expected) in pins {
        let mut bytes = vec![0u8; expected.len()];
        // SAFETY: a fault-tolerant read of code bytes.
        let read = unsafe { read_bytes(entry + offset, &mut bytes) };
        if !read || bytes != expected {
            return Err(format!(
                "{name} at 0x{entry:x}: +0x{offset:x} reads {bytes:02x?}, expected {expected:02x?}"
            ));
        }
    }
    Ok(())
}

/// Find and check `BuddyGenerator`'s first-loop return site: the 5 bytes before it must be a
/// `call rel32` whose target is `GetBuddyParam`, and `GetBuddyParam` must carry the windows that
/// say it fills `out = {id, row*}` from BuddyParam. Anything else and the hijack stays off.
fn verify_loop1_ret() -> Result<usize, String> {
    let generator = game_rva_named(addr::BUDDY_GENERATOR, "BUDDY_GENERATOR")?;
    let get_param = game_rva_named(addr::GET_BUDDY_PARAM, "GET_BUDDY_PARAM")?;
    let ret = generator + addr::BUDDY_GENERATOR_LOOP1_RET;
    // SAFETY: fault-tolerant reads of code bytes.
    let opcode = unsafe { safe_read_u8(ret - 5) }.ok_or("the call site did not read")?;
    let rel = unsafe { safe_read_i32(ret - 4) }.ok_or("the call site did not read")?;
    let target = ret.wrapping_add_signed(rel as isize);
    if opcode != 0xe8 || target != get_param {
        return Err(format!(
            "BuddyGenerator+0x{:x} is not a call to GetBuddyParam (opcode 0x{opcode:02x}, \
             target 0x{target:x}, expected 0x{get_param:x})",
            addr::BUDDY_GENERATOR_LOOP1_RET
        ));
    }
    pins_match("GetBuddyParam", get_param, BUDDY_PARAM_PINS)?;
    Ok(ret)
}

/// Create and queue one bare detour on a 2.7.2.0 rva; the caller applies the queue.
pub(crate) fn hook(
    rva: u32,
    detour: usize,
    original: &AtomicUsize,
    what: &str,
) -> Result<MhHook, String> {
    // Unchecked on purpose: `MhHook::new` checks the build and the detour allowlist itself.
    let target = game_rva_for_hook(rva)?;
    // SAFETY: `detour` has the target's exact signature.
    let hook = unsafe { MhHook::new(target as *mut c_void, detour as *mut c_void) }
        .map_err(|status| format!("MhHook::new({what} @0x{target:x}) failed: {status:?}"))?;
    original.store(hook.trampoline() as usize, Ordering::Release);
    // SAFETY: queued; applied together below.
    unsafe { hook.queue_enable() }.map_err(|status| format!("queue_enable({what}): {status:?}"))?;
    Ok(hook)
}

/// Register the `GetCharaInitParam` handler. It is not on the detour allowlist, so its bytes are
/// compared before anything is hooked through the runtime-derived entry point.
fn install_chara_init_getter() -> Result<usize, String> {
    let rva = is_supported_build()
        .then_some(GET_CHARA_INIT_PARAM_RVA)
        .ok_or_else(|| {
            format!(
                "2.7.2.0 GetCharaInitParam 0x{GET_CHARA_INIT_PARAM_RVA:x} refused -- {}",
                describe_build()
            )
        })?;
    let base = game_module_base().map_err(|error| format!("GetCharaInitParam: {error:?}"))?;
    let entry = base + rva;
    pins_match("GetCharaInitParam", entry, CHARA_INIT_PARAM_PINS)?;
    // SAFETY: a runtime-derived entry whose windows were just checked; the handler calls its slot
    // through `UnionFn`, and the getter takes two integer arguments.
    unsafe {
        er_hook::register_union_hook_runtime_derived_following_arxan(
            entry,
            chara_init_getter,
            &ORIG_CHARA_INIT_GETTER,
        )
    }
    .map_err(|status| format!("GetCharaInitParam at 0x{entry:x} refused: {status:?}"))?;
    Ok(entry)
}

/// Install the four detours and the getter handler. Called once, from the install thread.
pub(crate) fn install() {
    let ret = match verify_loop1_ret() {
        Ok(ret) => ret,
        Err(why) => {
            summons_log(format_args!(
                "mimic: hijack off, nothing installed -- {why}"
            ));
            return;
        }
    };
    // SAFETY: MinHook init is idempotent across DLLs.
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            summons_log(format_args!("mimic: MH_Initialize failed: {status:?}"));
            return;
        }
    }
    let installed = [
        hook(
            addr::BUDDY_GENERATOR,
            generator_detour as *const () as usize,
            &ORIG_GENERATOR,
            "BuddyGenerator",
        ),
        hook(
            addr::GET_BUDDY_LIST,
            list_detour as *const () as usize,
            &ORIG_LIST,
            "GetBuddyList",
        ),
        hook(
            addr::GET_BUDDY_PARAM,
            param_detour as *const () as usize,
            &ORIG_PARAM,
            "GetBuddyParam",
        ),
        hook(
            addr::CREATE_SUMMON_CHR,
            create_detour as *const () as usize,
            &ORIG_CREATE,
            "CreateSummonChr",
        ),
    ];
    // `MhHook` has no `Drop`: each detour stays installed once applied, so nothing is kept.
    for result in installed {
        match result {
            Ok(_hook) => {}
            Err(why) => {
                summons_log(format_args!("mimic: hijack off -- {why}"));
                return;
            }
        }
    }
    // SAFETY: applies the four queued enables at once.
    match unsafe { MH_ApplyQueued() } {
        MH_STATUS::MH_OK => {}
        status => {
            summons_log(format_args!("mimic: MH_ApplyQueued failed: {status:?}"));
            return;
        }
    }
    match install_chara_init_getter() {
        Ok(entry) => {
            CHARA_INIT_HOOKED.store(true, Ordering::Release);
            summons_log(format_args!(
                "dress: GetCharaInitParam at 0x{entry:x} answers a dressed companion's lookup \
                 with a copy of its row"
            ));
        }
        Err(why) => summons_log(format_args!(
            "dress: {why}; companions are summoned in their bodies' own gear"
        )),
    }
    LOOP1_RET.store(ret, Ordering::Release);
    summons_log(format_args!(
        "mimic: BuddyGenerator, GetBuddyList, GetBuddyParam and CreateSummonChr detours \
         installed; first-loop return site 0x{ret:x} and GetBuddyParam's out layout verified; \
         every changed row is a copy"
    ));
}
