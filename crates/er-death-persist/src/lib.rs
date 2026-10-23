//! Persist transformation body-buff SpEffects through death, without writing a param row.
//!
//! Rock Heart (SpEffect 19980), Priestess Heart (19981), and Lamenter's Mask
//! (19982) are the only SpEffectParam rows with `saveCategory = 5`. The game's
//! `SpecialEffect::ShouldBeSaved(saveCategory, isHpZero)` drops category 5
//! from the PlayerGameData saved-effects table the moment HP reaches zero, and
//! the respawn path restores effects only from that table -- that is the whole
//! "you lose dragon form when you die" rule. Category 3 is persist-through-
//! death in code and unused by every row in the shipped regulation, so a heart
//! row that reads as category 3 makes the game's own save/restore machinery
//! carry the transformation across death. Persistence lives in each
//! character's own saved-effects table, so characters that never used a heart
//! are unaffected and saves stay vanilla-compatible.
//!
//! The visible transformation is the root effect's VFX: each heart's
//! `vfxId` points at a SpEffectVfxParam row carrying a full-body
//! `transformProtectorId` (Rock 21201000 -> 5040000, Priestess 21202000 ->
//! 5050000, Lamenter 21203000 -> 5170000), and those rows ship with
//! `isVisibleDeadChr = 0` -- FromSoft's own "hide this VFX on a corpse"
//! switch. That is what strips the dragon body the instant death registers
//! even though the root effect survives the death purge. Reading
//! `isVisibleDeadChr` as set on the roots' VFX rows keeps the transformation
//! shown through the death window.
//!
//! The chains of short-lived effects each heart respawns through
//! `cycleOccurrenceSpEffectId` (2s duration, no save category,
//! `dontDeleteOnDead = 0`) also read as `dontDeleteOnDead` so their
//! transformation states (477/478/479/508) ride out `ChrIns::_Dead`'s purge,
//! which removes exactly finite, unsaved, unflagged entries at death.
//!
//! # The changed rows are this DLL's own copies
//!
//! Seamless Co-op filters its lobby search on `lobby_key`, a fingerprint of the param tables as
//! they sit in memory, compared for equality. This DLL used to make the changes above in the
//! resident rows, and that alone put the player in a matchmaking pool of one: on 2.7.2.0 with
//! Seamless 2.0.1 the search key was `2d11f036...` with it loaded and the common `724e9aa8...`
//! without it (bd `death-persist-is-the-lobby-key-mover-seamless-201-2720-2026-10-22`).
//!
//! So the resident rows stay as shipped. Once the params are loaded, the DLL copies the rows it
//! needs, changes the copies, and detours the two typed getters every reader of those rows goes
//! through, answering with the copy for those ids:
//!
//! - `GetSpEffectParam(out, id)` fills `out = {row* @+0, id @+8, u8 @+0xc}`. The fill and the
//!   refresh of a `SpecialEffectEntry` pass the entry itself as `out`, so the entry's `param_data`
//!   is the copy, and `CollectSaveEffects` (saveCategory), the death keep filter
//!   (`dontDeleteOnDead`) and the respawn restore all read through that pointer.
//! - The SpEffectVfxParam getter fills `out = {id @+0, row* @+8, u16 @+0x10}`. The per-frame VFX
//!   update calls it and hands `out` to the dead-character visibility check, the only reader of
//!   `isVisibleDeadChr`.
//!
//! The static reading behind both is recorded in bd `er-effects-rs-711x`. Copies are leaked on
//! purpose: a live `SpecialEffectEntry` keeps the pointer it was given, so a copy must outlive
//! every entry that could hold it, which is the process.

#![cfg(windows)]

use std::{
    collections::{BTreeSet, HashMap},
    ffi::c_void,
    sync::{
        OnceLock,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
};

use er_game_base::game_build::{describe_build, is_supported_build};
use er_game_base::log::{append_line, game_directory_path};
use er_game_base::mem::{game_module_base, read_bytes};
use er_hook::UnionFn;

use eldenring::{
    cs::{
        CSTaskGroupIndex, CSTaskImp, SoloParam, SoloParamRepository, SpEffectParam,
        SpEffectVfxParam,
    },
    fd4::FD4TaskData,
    param::SP_EFFECT_PARAM_ST,
};
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};

const DLL_MAIN_SUCCESS: i32 = 1;
const DLL_PROCESS_ATTACH: u32 = 1;
const SP_EFFECT_PARAM_INDEX: usize = SpEffectParam::INDEX as usize;
const SP_EFFECT_VFX_PARAM_INDEX: usize = SpEffectVfxParam::INDEX as usize;
const PRIMARY_RES_CAP_INDEX: usize = 0;
const NO_PATCH_ATTEMPTS: u32 = 0;
const FIRST_PATCH_ATTEMPT: u32 = 1;
const PATCH_RETRY_LOG_INTERVAL: u32 = 100_000;
const PATCH_RETRY_REMAINDER: u32 = 0;

/// `SP_EFFECT_SAVE_CATEGORY` used only by the DLC transformation hearts;
/// `ShouldBeSaved(5, isHpZero)` returns `!isHpZero`, so it is dropped from the
/// death-time saved-effects snapshot.
const TRANSFORM_SAVE_CATEGORY: i8 = 5;
/// `SP_EFFECT_SAVE_CATEGORY` that `ShouldBeSaved` accepts unconditionally
/// (persists through death). No row in the shipped regulation uses it, so the
/// per-category save slot cannot collide with another effect.
const PERSIST_THROUGH_DEATH_SAVE_CATEGORY: i8 = 3;
/// `cycleOccurrenceSpEffectId` value marking the end of a cycle chain.
const NO_CYCLE_OCCURRENCE: i32 = -1;

// The getters' addresses and window offsets, shared with build.rs; see `getter_sites.rs`.
include!(concat!(env!("CARGO_MANIFEST_DIR"), "/getter_sites.rs"));
// The windows themselves, assembled by build.rs from named instructions and checked against the
// 2.7.2.0 image, and read from the live 2.7.2.0 process on 2026-10-22.
include!(concat!(env!("OUT_DIR"), "/generated_getter_windows.rs"));

/// A typed param getter `fn(out, id)` this DLL answers for, and the bytes that identify it.
struct Getter {
    name: &'static str,
    /// The entry on 2.7.2.0, used only when the running build is that one.
    rva: usize,
    /// Offset of the row pointer in the getter's out struct.
    row_slot: usize,
    /// `(offset from the entry, window)` pairs that must all match before anything is hooked:
    /// the frame setup, the table index handed to `GetParamResCap`, and the row-pointer stores.
    pins: &'static [(usize, &'static [u8])],
}

const SP_EFFECT_GETTER: Getter = Getter {
    name: "GetSpEffectParam",
    rva: GET_SP_EFFECT_PARAM_RVA,
    row_slot: SP_EFFECT_OUT_ROW_SLOT,
    pins: &[
        (GETTER_FRAME_WINDOW_OFFSET, SP_EFFECT_FRAME_WINDOW),
        (SP_EFFECT_TABLE_WINDOW_OFFSET, SP_EFFECT_TABLE_WINDOW),
        (
            SP_EFFECT_ROW_STORE_WINDOW_OFFSET,
            SP_EFFECT_ROW_STORE_WINDOW,
        ),
    ],
};

const SP_EFFECT_VFX_GETTER: Getter = Getter {
    name: "GetSpEffectVfxParam",
    rva: GET_SP_EFFECT_VFX_PARAM_RVA,
    row_slot: SP_EFFECT_VFX_OUT_ROW_SLOT,
    pins: &[
        (GETTER_FRAME_WINDOW_OFFSET, SP_EFFECT_VFX_FRAME_WINDOW),
        (
            SP_EFFECT_VFX_TABLE_WINDOW_OFFSET,
            SP_EFFECT_VFX_TABLE_WINDOW,
        ),
        (
            SP_EFFECT_VFX_HIT_STORE_WINDOW_OFFSET,
            SP_EFFECT_VFX_HIT_STORE_WINDOW,
        ),
        (
            SP_EFFECT_VFX_MISS_STORE_WINDOW_OFFSET,
            SP_EFFECT_VFX_MISS_STORE_WINDOW,
        ),
    ],
};

/// One changed row: the id the getter is asked for and the address of this DLL's copy.
struct RowCopy {
    id: u32,
    row: usize,
    /// Set the first time the getter hands this copy out, so the log records it once.
    announced: AtomicBool,
}

/// The copies a getter serves, sorted by id. Set once, before the getter is hooked.
static SP_EFFECT_COPIES: OnceLock<&'static [RowCopy]> = OnceLock::new();
static SP_EFFECT_VFX_COPIES: OnceLock<&'static [RowCopy]> = OnceLock::new();

/// The next handler or the game trampoline, for each getter.
static ORIG_SP_EFFECT_GETTER: AtomicUsize = AtomicUsize::new(0);
static ORIG_SP_EFFECT_VFX_GETTER: AtomicUsize = AtomicUsize::new(0);

static START_PATCH_TASK: AtomicBool = AtomicBool::new(false);
static INSTALL_DONE: AtomicBool = AtomicBool::new(false);

#[unsafe(no_mangle)]
/// # Safety
///
/// This is called by Windows when the DLL is loaded. Do not call it directly.
pub unsafe extern "system" fn DllMain(
    _hmodule: *mut c_void,
    reason: u32,
    _reserved: *mut c_void,
) -> i32 {
    if reason != DLL_PROCESS_ATTACH {
        return DLL_MAIN_SUCCESS;
    }
    // First, before anything that can panic. A panic in a cdylib crosses an
    // `extern "system"` boundary and becomes an abort, which does not dispatch to a
    // vectored handler -- so no crash record is written at all and the process simply
    // vanishes. Enforced by `scripts/check-panic-reporter-installed.py`.
    er_game_base::panic_report::report_panics_to("er-death-persist", log_args_sink);
    // So a refused hook or address says so in this DLL's log instead of nowhere.
    er_hook::set_hook_logger(log_args_sink);

    if START_PATCH_TASK
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_ok()
    {
        std::thread::spawn(spawn_param_patch_task);
    }

    DLL_MAIN_SUCCESS
}

fn spawn_param_patch_task() {
    write_runtime_log("patch task started");
    let mut attempts = NO_PATCH_ATTEMPTS;
    // Bounded (2026-08-29): the unbounded form of this loop starved the wineserver and hung a
    // whole boot -- see er_game_base::wait. The attempt counter and its throttled log are kept;
    // what changed is that the wait backs off in user space and ends.
    let cs_task = er_game_base::wait::poll_until(|| match unsafe { CSTaskImp::instance() } {
        Ok(instance) => Some(instance),
        Err(error) => {
            attempts = attempts.saturating_add(FIRST_PATCH_ATTEMPT);
            if attempts == FIRST_PATCH_ATTEMPT
                || attempts % PATCH_RETRY_LOG_INTERVAL == PATCH_RETRY_REMAINDER
            {
                write_runtime_log(&format!(
                    "waiting for CSTaskImp attempt={attempts} error={error:?}"
                ));
            }
            None
        }
    });
    let Some(cs_task) = cs_task else {
        write_runtime_log("CSTaskImp never appeared; this shell stays inert rather than spinning");
        return;
    };
    write_runtime_log(&format!("found CSTaskImp after {attempts} retry attempts"));

    cs_task.run_recurring(
        move |_: &FD4TaskData| {
            if INSTALL_DONE.load(Ordering::Acquire) {
                return;
            }
            let Some(copies) = build_row_copies() else {
                return;
            };
            INSTALL_DONE.store(true, Ordering::Release);
            install(copies);
        },
        CSTaskGroupIndex::FrameBegin,
    );
}

/// The rows this DLL changes, copied and changed. `None` until both tables are loaded and the
/// hearts are found in them.
fn build_row_copies() -> Option<Copies> {
    // SAFETY: This recurring task runs on the game's task/main thread, where the repository is
    // read. Nothing here writes to it.
    let repository = unsafe { SoloParamRepository::instance().ok()? };
    for index in [SP_EFFECT_PARAM_INDEX, SP_EFFECT_VFX_PARAM_INDEX] {
        repository
            .solo_param_holders
            .get(index)?
            .get_res_cap(PRIMARY_RES_CAP_INDEX)?;
    }

    let mut cycle_by_id = HashMap::new();
    let mut roots = BTreeSet::new();
    let mut root_vfx_rows = BTreeSet::new();
    for (row_id, row) in repository.rows::<SpEffectParam>() {
        cycle_by_id.insert(row_id, row.cycle_occurrence_sp_effect_id());
        if row.save_category() == TRANSFORM_SAVE_CATEGORY {
            roots.insert(row_id);
            root_vfx_rows.extend(vfx_row_ids(row));
        }
    }
    if roots.is_empty() {
        return None;
    }
    let chain_rows = collect_cycle_chain_rows(&roots, &cycle_by_id);

    // Rows come out in id order, so each list is sorted for the getters' binary search.
    let mut copies = Copies::default();
    for (row_id, row) in repository.rows::<SpEffectParam>() {
        let root = roots.contains(&row_id);
        let chain = chain_rows.contains(&row_id) && !row.dont_delete_on_dead();
        if !root && !chain {
            continue;
        }
        let mut copy = row.clone();
        if root {
            copy.set_save_category(PERSIST_THROUGH_DEATH_SAVE_CATEGORY);
            copies.roots.push(row_id);
        }
        if chain {
            copy.set_dont_delete_on_dead(true);
            copies.chain_rows.push(row_id);
        }
        copies.sp_effect.push(leak_copy(row_id, copy));
    }
    for (row_id, row) in repository.rows::<SpEffectVfxParam>() {
        if root_vfx_rows.contains(&row_id) && !row.is_visible_dead_chr() {
            let mut copy = row.clone();
            copy.set_is_visible_dead_chr(true);
            copies.vfx_rows.push(row_id);
            copies.sp_effect_vfx.push(leak_copy(row_id, copy));
        }
    }
    Some(copies)
}

/// Move a changed row to a heap address that is never freed; see the module docs for why.
fn leak_copy<T>(id: u32, row: T) -> RowCopy {
    RowCopy {
        id,
        row: Box::leak(Box::new(row)) as *const T as usize,
        announced: AtomicBool::new(false),
    }
}

/// Publish the copies, then hook the getters that serve them.
fn install(copies: Copies) {
    let Copies {
        roots,
        chain_rows,
        vfx_rows,
        sp_effect,
        sp_effect_vfx,
    } = copies;
    write_runtime_log(&format!(
        "copied, not written: SpEffectParam saveCategory {TRANSFORM_SAVE_CATEGORY} -> \
         {PERSIST_THROUGH_DEATH_SAVE_CATEGORY} for rows {roots:?}; dontDeleteOnDead on \
         cycle-chain rows {chain_rows:?}; SpEffectVfxParam isVisibleDeadChr on rows {vfx_rows:?}. \
         The resident rows are untouched."
    ));
    let _ = SP_EFFECT_COPIES.set(Vec::leak(sp_effect));
    let _ = SP_EFFECT_VFX_COPIES.set(Vec::leak(sp_effect_vfx));

    match hook_getter(&SP_EFFECT_GETTER, sp_effect_getter, &ORIG_SP_EFFECT_GETTER) {
        Ok(entry) => write_runtime_log(&format!(
            "{} at 0x{entry:x} answers with the copies; transformations persist through death",
            SP_EFFECT_GETTER.name
        )),
        Err(why) => write_runtime_log(&format!(
            "{why}; not hooked, so transformations are lost on death as in the unmodded game"
        )),
    }
    match hook_getter(
        &SP_EFFECT_VFX_GETTER,
        sp_effect_vfx_getter,
        &ORIG_SP_EFFECT_VFX_GETTER,
    ) {
        Ok(entry) => write_runtime_log(&format!(
            "{} at 0x{entry:x} answers with the copies; the transformation stays visible on a \
             corpse",
            SP_EFFECT_VFX_GETTER.name
        )),
        Err(why) => write_runtime_log(&format!(
            "{why}; not hooked, so the transformation is hidden while dead as in the unmodded game"
        )),
    }
}

/// Check `getter`'s bytes on the running build and register `handler` on it.
fn hook_getter(
    getter: &Getter,
    handler: UnionFn,
    orig: &'static AtomicUsize,
) -> Result<usize, String> {
    let rva = is_supported_build().then_some(getter.rva).ok_or_else(|| {
        format!(
            "{}: 2.7.2.0 rva 0x{:x} refused -- {}",
            getter.name,
            getter.rva,
            describe_build()
        )
    })?;
    let base = game_module_base().map_err(|error| format!("{}: {error:?}", getter.name))?;
    let entry = base + rva;
    for &(offset, expected) in getter.pins {
        let mut bytes = vec![0u8; expected.len()];
        // SAFETY: a fault-tolerant read of code bytes.
        let read = unsafe { read_bytes(entry + offset, &mut bytes) };
        if !read || bytes != expected {
            return Err(format!(
                "{} at 0x{entry:x}: +0x{offset:x} reads {bytes:02x?}, expected {expected:02x?}",
                getter.name
            ));
        }
    }
    // SAFETY: a runtime-derived entry whose bytes were just checked; the handler calls its slot
    // through `UnionFn`, and both getters take two integer arguments.
    unsafe { er_hook::register_union_hook_runtime_derived_following_arxan(entry, handler, orig) }
        .map_err(|status| format!("{} at 0x{entry:x} refused: {status:?}", getter.name))?;
    Ok(entry)
}

/// `GetSpEffectParam` in the union's four-argument shape.
unsafe extern "system" fn sp_effect_getter(out: usize, id: usize, c: usize, d: usize) -> usize {
    // SAFETY: the slot holds the next handler or the trampoline.
    let result = unsafe { call_next(&ORIG_SP_EFFECT_GETTER, out, id, c, d) };
    serve(
        SP_EFFECT_COPIES.get().copied(),
        out,
        SP_EFFECT_GETTER.row_slot,
        id,
        "SpEffectParam",
    );
    result
}

/// The SpEffectVfxParam getter in the union's four-argument shape.
unsafe extern "system" fn sp_effect_vfx_getter(out: usize, id: usize, c: usize, d: usize) -> usize {
    // SAFETY: the slot holds the next handler or the trampoline.
    let result = unsafe { call_next(&ORIG_SP_EFFECT_VFX_GETTER, out, id, c, d) };
    serve(
        SP_EFFECT_VFX_COPIES.get().copied(),
        out,
        SP_EFFECT_VFX_GETTER.row_slot,
        id,
        "SpEffectVfxParam",
    );
    result
}

/// # Safety
/// `slot` holds zero or a function with the [`UnionFn`] shape.
unsafe fn call_next(slot: &AtomicUsize, a: usize, b: usize, c: usize, d: usize) -> usize {
    let next = slot.load(Ordering::Acquire);
    if next == 0 {
        return 0;
    }
    // SAFETY: per the contract above.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    unsafe { call(a, b, c, d) }
}

/// Point `out`'s row slot at this DLL's copy when `id` is one of the changed rows.
///
/// Runs on every lookup of the table, from any thread, so the common path is one atomic load and
/// a binary search over a handful of ids. A miss -- the table has no such row -- is left a miss.
fn serve(copies: Option<&'static [RowCopy]>, out: usize, row_slot: usize, id: usize, table: &str) {
    let Some(copies) = copies else {
        return;
    };
    if out == 0 {
        return;
    }
    // The id arrives in `edx`; the upper half of `rdx` is not part of it.
    let id = id as u32;
    let Ok(index) = copies.binary_search_by_key(&id, |copy| copy.id) else {
        return;
    };
    let copy = &copies[index];
    let slot = (out + row_slot) as *mut usize;
    // SAFETY: `out` is the caller's lookup struct, whose row slot the getter has just written.
    unsafe {
        if slot.read_unaligned() == 0 {
            return;
        }
        slot.write_unaligned(copy.row);
    }
    if !copy.announced.swap(true, Ordering::Relaxed) {
        write_runtime_log(&format!(
            "served the {table} copy of row {id} for the first time"
        ));
    }
}

/// All SpEffectVfxParam row ids a SpEffectParam row references.
fn vfx_row_ids(row: &SP_EFFECT_PARAM_ST) -> impl Iterator<Item = u32> {
    [
        row.vfx_id(),
        row.vfx_id1(),
        row.vfx_id2(),
        row.vfx_id3(),
        row.vfx_id4(),
        row.vfx_id5(),
        row.vfx_id6(),
        row.vfx_id7(),
    ]
    .into_iter()
    .filter_map(|id| u32::try_from(id).ok())
}

/// Every row reachable from a root through `cycleOccurrenceSpEffectId` links.
/// The visited set doubles as a guard against cyclic chains.
fn collect_cycle_chain_rows(
    roots: &BTreeSet<u32>,
    cycle_by_id: &HashMap<u32, i32>,
) -> BTreeSet<u32> {
    let mut chain_rows = BTreeSet::new();
    for root in roots {
        let mut next = cycle_by_id
            .get(root)
            .copied()
            .unwrap_or(NO_CYCLE_OCCURRENCE);
        while next != NO_CYCLE_OCCURRENCE {
            let Ok(row_id) = u32::try_from(next) else {
                break;
            };
            if !chain_rows.insert(row_id) {
                break;
            }
            next = cycle_by_id
                .get(&row_id)
                .copied()
                .unwrap_or(NO_CYCLE_OCCURRENCE);
        }
    }
    chain_rows
}

/// What [`build_row_copies`] found: the changed row ids for the log, and the copies to serve.
#[derive(Default)]
struct Copies {
    roots: Vec<u32>,
    chain_rows: Vec<u32>,
    vfx_rows: Vec<u32>,
    sp_effect: Vec<RowCopy>,
    sp_effect_vfx: Vec<RowCopy>,
}

/// Written next to the game executable, like every other ME3 shell in this
/// workspace. It used to land in `%LOCALAPPDATA%/ErDeathPersist/`, which put it
/// outside the one directory `scripts/er-run-branch.py` watches to confirm a DLL
/// actually loaded -- so a run loading only this shell had no witness and was
/// reported as a game that never started.
const RUNTIME_LOG_NAME: &str = "er-death-persist.log";

/// Line counter for the log. The file describes exactly one process run (see
/// `er_game_base::log::begin_fresh_run`), so ordering within it is the whole story.
static LOG_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// The sink `report_panics_to` and `er_hook::set_hook_logger` write through, which take
/// `fmt::Arguments` where this crate logs `&str`.
///
/// Small and duplicated per shell on purpose: the hook is installed per DLL because every cdylib
/// statically links its own `er-game-base`, so there is no shared place this could live and still
/// be the thing that runs in this module.
fn log_args_sink(args: core::fmt::Arguments<'_>) {
    write_runtime_log(&args.to_string());
}

fn write_runtime_log(message: &str) {
    let Some(directory) = game_directory_path() else {
        return;
    };
    let seq = LOG_SEQUENCE.fetch_add(1, Ordering::SeqCst) + 1;
    append_line(
        &directory.join(RUNTIME_LOG_NAME),
        format_args!("[{seq:06}] {message}"),
    );
}
