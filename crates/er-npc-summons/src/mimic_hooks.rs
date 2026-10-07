//! The custom Mimic Tear: three detours scoped to one `BuddyGenerator` call.
//!
//! Using Mimic Tear Ashes runs the game's own spirit-ash flow: the item use, FP cost, summoning
//! pool range check, one-summon replacement, dismissal and the Seamless broadcast all stay native.
//! Only the values `BuddyGenerator` reads change, and only inside a call whose request is a Mimic
//! Tear (bd `mimic-tear-summon-hijack-1171-2026-10-06`, prototype `scripts/frida/mimic-tear-turtles.js`):
//!
//! 1. `BuddyGenerator` detour: when `SummonBuddyManager+0x20` is `207000 + level` and the config
//!    has companions, mark this thread as hijacking for the duration of the original call, and
//!    put the human row back afterwards.
//! 2. `GetBuddyList` detour: every list entry becomes the human row 20700001 (so no request
//!    carries 20700000 and the copy-the-player branch never arms), and the list is grown or
//!    shrunk to one entry per companion with the game's own node insert.
//! 3. `GetBuddyParam` detour: each first-loop read of the human row gets the next companion's
//!    npc, think, charaInit and formation offset written into the row before `BuddyGenerator`
//!    copies them out.
//!
//! 4. `CreateSummonChr` detour: for each companion `BuddyGenerator` builds, write its build's gear
//!    into the `CharaInitParam` row the call is about to build from, and put the row back when the
//!    call returns. The call is synchronous: `ChrSet::SpawnChr` -> `CreateCharacter` reads the row
//!    and mints the gear before it returns (see `er_npc_summons_core::dress` for the evidence).
//!
//! Each companion built is also named for its slot ([`crate::names`]); a companion's Lua brain is
//! loaded by [`crate::brains`], keyed by the think id the plan gives it.

#![cfg(windows)]

use core::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use eldenring::cs::{CharaInitParam, SoloParamRepository};
use eldenring::param::CHARACTER_INIT_PARAM;
use er_game_base::mem::{
    game_module_base, game_rva_for_hook, game_rva_named, safe_read_i32, safe_read_u8,
    safe_read_usize,
};
use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};
use er_npc_summons_core::dress::CharaInitGear;
use er_npc_summons_core::mimic::{self, HUMAN_ROW, MIMIC_TRIGGER, RowValues};
use fromsoftware_shared::FromStatic;
use windows::Win32::System::Diagnostics::Debug::RtlCaptureStackBackTrace;
use windows::Win32::System::Threading::GetCurrentThreadId;

use crate::addr::{self, buddy_param};
use crate::log::summons_log;

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

/// The values each first-loop read was given in the current hijacked call, not yet built.
static PENDING: Mutex<Vec<RowValues>> = Mutex::new(Vec::new());
/// Companions built in the current hijacked call that wore their build's gear.
static DRESSED: AtomicUsize = AtomicUsize::new(0);

/// The human row's original bytes while a hijacked call has it patched.
struct SavedRow {
    row: usize,
    bytes: [u8; buddy_param::SAVE],
}
static SAVED: Mutex<Option<SavedRow>> = Mutex::new(None);

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

fn restore_row() {
    let Ok(mut saved) = SAVED.lock() else {
        return;
    };
    if let Some(row) = saved.take() {
        // SAFETY: `row.row` is the BuddyParam row this call read and patched; the param table is
        // resident for the life of the process.
        unsafe {
            core::ptr::copy_nonoverlapping(row.bytes.as_ptr(), row.row as *mut u8, row.bytes.len());
        }
    }
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
    restore_row();
    HIJACK_TID.store(0, Ordering::Release);
    summons_log(format_args!(
        "mimic: Mimic Tear +{} summoned {} companion(s), {} dressed from a build URL",
        level.unwrap_or(0),
        READS.load(Ordering::Acquire),
        DRESSED.load(Ordering::Acquire)
    ));
}

fn hijacking() -> bool {
    HIJACK_TID.load(Ordering::Acquire) == this_thread()
}

/// # Safety
///
/// `address` must be a writable pointer-sized location in the game heap.
unsafe fn write_usize(address: usize, value: usize) {
    // SAFETY: caller's contract.
    unsafe { core::ptr::write_volatile(address as *mut usize, value) };
}

/// Rewrite the Mimic Tear's buddy list: every entry the human row, one entry per companion.
///
/// MSVC `std::list<int>`: list `+8` head node, `+0x10` size; node `+0` next, `+8` prev, `+0x10`
/// value. Growth uses the game's own insert, then the three link writes the game's own fill does
/// (`0x1404bcef0..0x1404bcf26`). Shrinking unlinks the surplus nodes and leaves them allocated:
/// a few bytes per summon, against freeing game-heap memory from outside the allocator that
/// owns it.
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

fn patch_row(row: usize, values: RowValues) {
    // SAFETY: `row` is the resident BuddyParam row `GetBuddyParam` just returned; offsets are
    // `BUDDY_PARAM_ST`.
    unsafe {
        let base = row as *mut u8;
        core::ptr::write_volatile(base.add(buddy_param::NPC).cast::<i32>(), values.npc_param);
        core::ptr::write_volatile(base.add(buddy_param::THINK).cast::<i32>(), values.think);
        core::ptr::write_volatile(
            base.add(buddy_param::CHARA_INIT).cast::<i32>(),
            values.chara_init,
        );
        core::ptr::write_volatile(base.add(buddy_param::X).cast::<f32>(), values.offset.x);
        core::ptr::write_volatile(base.add(buddy_param::Z).cast::<f32>(), values.offset.z);
        core::ptr::write_volatile(base.add(buddy_param::YAW).cast::<f32>(), values.offset.yaw);
    }
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
    // SAFETY: fault-tolerant read of the lookup's `+8` row pointer.
    let Some(row) = (unsafe { safe_read_usize(lookup + 8) }).filter(|&r| r != 0) else {
        return result;
    };
    if let Ok(mut saved) = SAVED.lock()
        && saved.is_none()
    {
        let mut bytes = [0u8; buddy_param::SAVE];
        // SAFETY: the resident param row.
        unsafe {
            core::ptr::copy_nonoverlapping(row as *const u8, bytes.as_mut_ptr(), bytes.len())
        };
        *saved = Some(SavedRow { row, bytes });
    }
    let read = READS.fetch_add(1, Ordering::AcqRel);
    if let Some(values) = mimic::entry_for_read(&plan(), read) {
        patch_row(row, values);
        if let Ok(mut pending) = PENDING.lock() {
            pending.push(values);
        }
    }
    result
}

/// Write a build's gear into a `CharaInitParam` row: the fields `CreateCharacter`'s row applier
/// mints from (`er_npc_summons_core::dress`). Every armament slot gets type 0, `EquipParamWeapon`.
fn write_gear(row: &mut CHARACTER_INIT_PARAM, gear: &CharaInitGear) {
    row.set_equip_wep_right(gear.right[0]);
    row.set_equip_subwep_right(gear.right[1]);
    row.set_equip_subwep_right3(gear.right[2]);
    row.set_equip_wep_left(gear.left[0]);
    row.set_equip_subwep_left(gear.left[1]);
    row.set_equip_subwep_left3(gear.left[2]);
    row.set_wep_param_type_right1(0);
    row.set_wep_param_type_right2(0);
    row.set_wep_param_type_right3(0);
    row.set_wep_param_type_left1(0);
    row.set_wep_param_type_left2(0);
    row.set_wep_param_type_left3(0);
    row.set_equip_helm(gear.protectors[0]);
    row.set_equip_armer(gear.protectors[1]);
    row.set_equip_gaunt(gear.protectors[2]);
    row.set_equip_leg(gear.protectors[3]);
    row.set_equip_arrow(gear.ammo[0]);
    row.set_equip_bolt(gear.ammo[1]);
    row.set_equip_sub_arrow(gear.ammo[2]);
    row.set_equip_sub_bolt(gear.ammo[3]);
    row.set_arrow_num(gear.ammo_count[0]);
    row.set_bolt_num(gear.ammo_count[1]);
    row.set_sub_arrow_num(gear.ammo_count[2]);
    row.set_sub_bolt_num(gear.ammo_count[3]);
    row.set_equip_accessory01(gear.talismans[0]);
    row.set_equip_accessory02(gear.talismans[1]);
    row.set_equip_accessory03(gear.talismans[2]);
    row.set_equip_accessory04(gear.talismans[3]);
}

/// Patch the companion's `CharaInitParam` row and return its original contents, or why not.
fn dress_row(chara_init: i32, gear: &CharaInitGear) -> Result<CHARACTER_INIT_PARAM, String> {
    let id = u32::try_from(chara_init)
        .map_err(|_| format!("charaInit {chara_init} builds no human, so there is no row"))?;
    // SAFETY: game thread, inside `BuddyGenerator`; the repository is resident once a summon runs.
    let repo = unsafe { SoloParamRepository::instance_mut() }
        .map_err(|_| "SoloParamRepository is not up".to_owned())?;
    let row = repo
        .get_mut::<CharaInitParam>(id)
        .ok_or_else(|| format!("CharaInitParam {id} does not exist"))?;
    let saved = row.clone();
    write_gear(row, gear);
    Ok(saved)
}

fn restore_chara_init(chara_init: i32, saved: CHARACTER_INIT_PARAM) {
    let Ok(id) = u32::try_from(chara_init) else {
        return;
    };
    // SAFETY: as in `dress_row`.
    if let Ok(repo) = unsafe { SoloParamRepository::instance_mut() }
        && let Some(row) = repo.get_mut::<CharaInitParam>(id)
    {
        *row = saved;
    }
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
        }
        chr
    };
    let (gear, build) = match crate::dress::gear_for(slot) {
        Ok(found) => found,
        Err(why) => {
            summons_log(format_args!(
                "dress: companion {slot}: not dressed ({why}); built in charaInit {chara_init}'s own \
                 gear"
            ));
            return named(call());
        }
    };
    let saved = match dress_row(chara_init, &gear) {
        Ok(saved) => saved,
        Err(why) => {
            summons_log(format_args!(
                "dress: companion {slot}: not dressed ({why}); built as configured"
            ));
            return named(call());
        }
    };
    let chr = named(call());
    restore_chara_init(chara_init, saved);
    if chr != 0 {
        DRESSED.fetch_add(1, Ordering::AcqRel);
    }
    summons_log(format_args!(
        "dress: companion {slot}: built 0x{chr:x} from charaInit {chara_init} wearing build \
         {build:?} (R {:?}, L {:?}, armour {:?}, talismans {:?}, ammo {:?}); row restored",
        gear.right, gear.left, gear.protectors, gear.talismans, gear.ammo
    ));
    if chr != 0 {
        apply_face(slot, chr);
    }
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
fn apply_face(slot: u8, chr: usize) {
    let Some(face) = crate::dress::face_for(slot) else {
        summons_log(format_args!(
            "dress: companion {slot}: its build names no face; it keeps the body's own"
        ));
        return;
    };
    let (Ok(base), Some(pgd)) = (
        game_module_base(),
        // SAFETY: a fault-tolerant read off the character just built.
        unsafe { safe_read_usize(chr + CHR_PLAYER_GAME_DATA) }.filter(|&p| p != 0),
    ) else {
        summons_log(format_args!(
            "dress: companion {slot}: 0x{chr:x} has no PlayerGameData; face not applied"
        ));
        return;
    };
    // SAFETY: the game thread, inside the CreateSummonChr detour, with `pgd` read off the
    // character the call just built.
    let outcome =
        unsafe { er_build_import_runtime::face::adopt_build_face(base, pgd, Some(&face)) };
    summons_log(format_args!(
        "dress: companion {slot}: face {}",
        outcome.label()
    ));
}

/// Find and check `BuddyGenerator`'s first-loop return site: the 5 bytes before it must be a
/// `call rel32` whose target is `GetBuddyParam`. Anything else and the hijack stays off.
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
    Ok(ret)
}

/// Create and queue one bare detour on a 1.16.2 rva; the caller applies the queue.
pub(crate) fn hook(
    rva: u32,
    detour: usize,
    original: &AtomicUsize,
    what: &str,
) -> Result<MhHook, String> {
    // Unresolved on purpose: `MhHook::new` translates through the detour map itself.
    let target = game_rva_for_hook(rva)?;
    // SAFETY: `detour` has the target's exact signature.
    let hook = unsafe { MhHook::new(target as *mut c_void, detour as *mut c_void) }
        .map_err(|status| format!("MhHook::new({what} @0x{target:x}) failed: {status:?}"))?;
    original.store(hook.trampoline() as usize, Ordering::Release);
    // SAFETY: queued; applied together below.
    unsafe { hook.queue_enable() }.map_err(|status| format!("queue_enable({what}): {status:?}"))?;
    Ok(hook)
}

/// Install the four detours. Called once, from the install thread.
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
    LOOP1_RET.store(ret, Ordering::Release);
    summons_log(format_args!(
        "mimic: BuddyGenerator, GetBuddyList, GetBuddyParam and CreateSummonChr detours \
         installed; first-loop return site 0x{ret:x} verified"
    ));
}
