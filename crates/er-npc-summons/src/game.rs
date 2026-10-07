//! The native calls and field reads. Every function here runs on the game thread (the `FrameBegin`
//! task or a detour the game called), and every address goes through `er_game_base`'s resolver,
//! so an address with no verified mapping for the running build refuses instead of jumping into
//! whatever code now sits there.
//!
//! The call shapes are the ones the Frida prototypes ran live on 1.17.1
//! (`scripts/frida/spawn-npc.js`, `scripts/frida/signs-to-player.js`); the memories that record
//! each measurement are named at the call.

#![cfg(windows)]

use core::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};

use er_game_base::mem::{
    game_module_base, game_rva_named, read_global_ptr, safe_read_i32, safe_read_u8,
    safe_read_usize, safe_write_i32,
};
use er_game_base::rva::{
    CS_EVENT_MAN_GLOBAL_RVA, GAME_MAN_SINGLETON_RVA, WORLD_CHR_MAN_GLOBAL_RVA,
};
use er_npc_summons_core::config::Body;
use er_npc_summons_core::duel::{DUEL_ENTITY_ID, HiddenObservation};
use windows::Win32::System::Threading::GetCurrentThreadId;

use crate::addr::{self, chr_ins, chr_set_entry, party, sign, summon_group, world_chr_man};
use crate::log::summons_log;

/// `FloatVector4`. The game loads these with `MOVAPS`, so the alignment is required, not tidy.
#[repr(C, align(16))]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct Vec4(pub(crate) [f32; 4]);

type CreateSummonChrFn = unsafe extern "system" fn(
    usize,       // SummonBuddyManager*
    *const i32,  // creator event id
    *const u64,  // creator steam id
    *const u32,  // BlockId
    u32,         // 0xffffffff at every retail call
    *const u64,  // FieldInsHandle, {-1, -1}
    u32,         // npcParamId
    i32,         // npcThinkId
    i32,         // charaInitParam
    *const Vec4, // block-local position
    f32,         // yaw
    bool,        // true at the retail call
    bool,        // hasMount
    u32,         // buddyStoneParamId
    u32,         // buddyParamId
    u32,         // dopingLevel
    bool,        // fromNetwork
    bool,        // hasMoghGreatRune
) -> usize;
type DisableFn = unsafe extern "system" fn(usize, bool);
type PlaceSignFn =
    unsafe extern "system" fn(usize, u8, *const u32, *const u32, i32, *const u32, i32, u8);
type SignSfxFn = unsafe extern "system" fn(usize, usize);
type ChrByEntityFn = unsafe extern "system" fn(*const u32, i32, i32) -> usize;
type PhysicsPosFn = unsafe extern "system" fn(usize, *mut Vec4);
type BlockToPhysicsFn = unsafe extern "system" fn(*mut Vec4, *const Vec4, *const u32) -> u8;
type IsDrawnFn = unsafe extern "system" fn(usize) -> u8;
type IdOutFn = unsafe extern "system" fn(usize, *mut c_void);
type RemoveChrFn = unsafe extern "system" fn(usize, usize);
type WarpUntrackFn = unsafe extern "system" fn(usize, u64);
type ChrFromHandleFn = unsafe extern "system" fn(usize, *const u64) -> usize;
type DeallocateFn = unsafe extern "system" fn(usize, usize);
type SpawnChrFn = unsafe extern "system" fn(usize, usize, usize, usize) -> usize;

/// The thread inside [`spawn_hidden`]'s `CreateSummonChr` call, or 0. The `SpawnChr` detour
/// rewrites the request's entity id only on that thread.
static DUEL_SPAWN_TID: AtomicU32 = AtomicU32::new(0);
static ORIG_SPAWN_CHR: AtomicUsize = AtomicUsize::new(0);
/// The `SpawnChr` detour is live.
static SPAWN_CHR_HOOKED: AtomicBool = AtomicBool::new(false);

fn this_thread() -> u32 {
    // SAFETY: no preconditions.
    unsafe { GetCurrentThreadId() }
}

/// `ChrSet::SpawnChr(ChrSet*, u8, ChrSpawnRequest*, int)`. Inside the duel spawn the request's
/// event entity is changed from 35000 to [`DUEL_ENTITY_ID`] before the character is created, so
/// `SpawnChr` registers it in its set's entity map under an id nothing else holds. The id is
/// copied into the character by `CreateCharacter` and read back through `GetEntityEventId` for
/// the registration, so the character and the map agree, which poking `ChrIns+0x1e8` afterwards
/// would not give.
unsafe extern "system" fn spawn_chr_detour(
    chr_set: usize,
    index: usize,
    request: usize,
    buddy_slot: usize,
) -> usize {
    let original = ORIG_SPAWN_CHR.load(Ordering::Acquire);
    if original == 0 {
        return 0;
    }
    if request != 0 && DUEL_SPAWN_TID.load(Ordering::Acquire) == this_thread() {
        // SAFETY: the request `CreateSummonChr` built on its stack and passes in r8.
        unsafe {
            safe_write_i32(
                request + addr::SPAWN_REQUEST_EVENT_ENTITY,
                DUEL_ENTITY_ID as i32,
            )
        };
    }
    // SAFETY: the trampoline of the function this replaces.
    let original: SpawnChrFn = unsafe { core::mem::transmute(original) };
    unsafe { original(chr_set, index, request, buddy_slot) }
}

/// Install the `SpawnChr` detour. Without it the duel spawn is refused: an NPC registered under
/// 35000 cannot be told apart from a live companion by its sign or by `GetChrInsByEntityId`.
pub(crate) fn install() {
    match crate::mimic_hooks::hook(
        addr::SPAWN_CHR,
        spawn_chr_detour as *const () as usize,
        &ORIG_SPAWN_CHR,
        "ChrSet::SpawnChr",
    ) {
        // SAFETY: applies the queued enable.
        Ok(_hook) => match unsafe { er_hook::MH_ApplyQueued() } {
            er_hook::MH_STATUS::MH_OK => {
                SPAWN_CHR_HOOKED.store(true, Ordering::Release);
                summons_log(format_args!(
                    "duel: ChrSet::SpawnChr detoured; the duel NPC is registered as entity \
                     {DUEL_ENTITY_ID}"
                ));
            }
            status => summons_log(format_args!(
                "duel: MH_ApplyQueued(SpawnChr) failed, duels are off: {status:?}"
            )),
        },
        Err(why) => summons_log(format_args!(
            "duel: SpawnChr not detoured, duels are off -- {why}"
        )),
    }
}

/// Resolve `rva` for the running build and view it as `F`.
///
/// # Safety
///
/// `F` must be the function's real signature.
unsafe fn native<F: Copy>(rva: u32, what: &'static str) -> Result<F, String> {
    let address = game_rva_named(rva, what)?;
    // SAFETY: `F` is a function pointer type the size of `usize` (caller's contract).
    Ok(unsafe { core::mem::transmute_copy::<usize, F>(&address) })
}

fn module_base() -> Result<usize, String> {
    game_module_base()
}

/// `WorldChrManImp*`, or `None` outside a loaded world.
pub(crate) fn world_chr_man() -> Option<usize> {
    let base = module_base().ok()?;
    let wcm = read_global_ptr(base, WORLD_CHR_MAN_GLOBAL_RVA, "WORLD_CHR_MAN_GLOBAL_RVA");
    (wcm != 0).then_some(wcm)
}

/// The local `PlayerIns*`.
pub(crate) fn main_player() -> Option<usize> {
    let wcm = world_chr_man()?;
    // SAFETY: fault-tolerant read.
    unsafe { safe_read_usize(wcm + world_chr_man::MAIN_PLAYER) }.filter(|&p| p != 0)
}

fn summon_buddy_manager() -> Option<usize> {
    let wcm = world_chr_man()?;
    // SAFETY: fault-tolerant read.
    unsafe { safe_read_usize(wcm + world_chr_man::SUMMON_BUDDY_MANAGER) }.filter(|&m| m != 0)
}

/// The character registered under an event entity id.
pub(crate) fn chr_by_entity(entity: u32) -> Option<usize> {
    // SAFETY: signature from the prototypes (`GetChrInsByEntityId(u32*, 0, 0)`).
    let call: ChrByEntityFn =
        unsafe { native(addr::GET_CHR_BY_ENTITY, "GET_CHR_BY_ENTITY") }.ok()?;
    let chr = unsafe { call(&entity, 0, 0) };
    (chr != 0).then_some(chr)
}

/// A character's physics position.
pub(crate) fn physics_pos(chr: usize) -> Option<Vec4> {
    // SAFETY: `GetPhysicsPos(ChrIns*, FloatVector4*)`.
    let call: PhysicsPosFn = unsafe { native(addr::GET_PHYSICS_POS, "GET_PHYSICS_POS") }.ok()?;
    let mut out = Vec4::default();
    unsafe { call(chr, &mut out) };
    out.0[..3].iter().all(|v| v.is_finite()).then_some(out)
}

/// A character's block, the way `ChrIns::GetBlockIdOrigin` reads it.
pub(crate) fn block_id(chr: usize) -> Option<u32> {
    // SAFETY: fault-tolerant reads.
    let primary = unsafe { safe_read_i32(chr + chr_ins::BLOCK_ID) }? as u32;
    if primary != u32::MAX {
        return Some(primary);
    }
    let fallback = unsafe { safe_read_i32(chr + chr_ins::BLOCK_ID_FALLBACK) }? as u32;
    (fallback != u32::MAX).then_some(fallback)
}

/// Where `block`'s origin is in physics space: `ConvertBlockCoordsToPhysicsCoords` of a zero
/// local position. The physics origin of a block moves when the world re-centres
/// (`WorldChrManImp::ShiftBlocks`), so this is read fresh every time it is needed.
pub(crate) fn block_origin(block: u32) -> Option<Vec4> {
    // SAFETY: `(FloatVector4* out, FloatVector4* local, BlockId*) -> bool`.
    let call: BlockToPhysicsFn =
        unsafe { native(addr::BLOCK_TO_PHYSICS, "BLOCK_TO_PHYSICS") }.ok()?;
    let mut out = Vec4::default();
    let zero = Vec4::default();
    let ok = unsafe { call(&mut out, &zero, &block) };
    (ok != 0).then_some(out)
}

/// `ChangeCharacterDisableState(chr, disable)`.
pub(crate) fn set_disabled(chr: usize, disable: bool) -> Result<(), String> {
    // SAFETY: `(ChrIns*, bool)`, bd `chr-enable-setter-and-join-enable-1171-2026-10-06`.
    let call: DisableFn = unsafe { native(addr::CHANGE_DISABLE_STATE, "CHANGE_DISABLE_STATE") }?;
    unsafe { call(chr, disable) };
    Ok(())
}

/// Create `body` at physics position `at` and hide it in the same call, before any frame can
/// draw it (design doc section 2.2). Returns the `ChrIns*`.
///
/// The call is `BuddyGenerator`'s own, as `spawn-npc.js` measured it: creator ids from the main
/// player, the player's block, block-local position, `0xffffffff` / `{-1,-1}` for the handles,
/// then `0,0,-1,-1,0,0,0`. The first of those differs from the prototype's `1` on purpose (see the
/// call). No network announce: the duel is local.
///
/// Two things are undone before the call returns, both on this thread and before any frame:
/// the character is registered as [`DUEL_ENTITY_ID`] rather than 35000 (the `SpawnChr` detour),
/// and its entry in the creator's summon group is unlinked, which is what the HUD's spirit-ash
/// bars, `DespawnAll` and `RemoveSummonsByOwnerEventId` read.
pub(crate) fn spawn_hidden(body: Body, at: Vec4) -> Result<usize, String> {
    if !SPAWN_CHR_HOOKED.load(Ordering::Acquire) {
        return Err(
            "ChrSet::SpawnChr is not detoured, so the NPC could not be given its own \
                    entity id"
                .to_owned(),
        );
    }
    if let Some(holder) = chr_by_entity(DUEL_ENTITY_ID) {
        return Err(format!(
            "entity {DUEL_ENTITY_ID} is already registered to 0x{holder:x}"
        ));
    }
    let player = main_player().ok_or("no main player")?;
    let manager = summon_buddy_manager().ok_or("no SummonBuddyManager")?;
    let block = block_id(player).ok_or("the player has no block")?;
    let origin = block_origin(block).ok_or("the player's block has no physics origin")?;
    let local = Vec4([
        at.0[0] - origin.0[0],
        at.0[1] - origin.0[1],
        at.0[2] - origin.0[2],
        0.0,
    ]);
    // SAFETY: signatures as declared above.
    let event_id_of: IdOutFn = unsafe { native(addr::PLAYER_EVENT_ID, "PLAYER_EVENT_ID") }?;
    let steam_id_of: IdOutFn = unsafe { native(addr::PLAYER_STEAM_ID, "PLAYER_STEAM_ID") }?;
    let create: CreateSummonChrFn =
        unsafe { native(addr::CREATE_SUMMON_CHR, "CREATE_SUMMON_CHR") }?;
    let mut event_id: i32 = 0;
    let mut steam_id: u64 = 0;
    unsafe {
        event_id_of(player, (&raw mut event_id).cast());
        steam_id_of(player, (&raw mut steam_id).cast());
    }
    let handle: u64 = u64::MAX;
    DUEL_SPAWN_TID.store(this_thread(), Ordering::Release);
    let chr = unsafe {
        create(
            manager,
            &event_id,
            &steam_id,
            &block,
            u32::MAX,
            &handle,
            body.npc_param as u32,
            body.think,
            body.chara_init,
            &local,
            0.0,
            // `args[11]`: "spawn waiting to appear". With 1, `SummonBuddyManager::Update` later
            // plays the appear animation, applies doping and enables the character
            // (`0x1404bdfc0` -> `0x1404be1d0`), undoing the hide below. With 0 none of that runs
            // (bd `createsummonchr-args-and-spawn-sfx-1171-2026-10-06`).
            false,
            false,
            u32::MAX,
            u32::MAX,
            0,
            false,
            false,
        )
    };
    DUEL_SPAWN_TID.store(0, Ordering::Release);
    if chr == 0 {
        return Err("CreateSummonChr returned null".to_owned());
    }
    set_disabled(chr, true)?;
    let entity = event_entity(chr);
    if entity != Some(DUEL_ENTITY_ID) || chr_by_entity(DUEL_ENTITY_ID) != Some(chr) {
        summons_log(format_args!(
            "duel: 0x{chr:x} carries entity {entity:?} and entity {DUEL_ENTITY_ID} finds {:?}; \
             removing it",
            chr_by_entity(DUEL_ENTITY_ID)
        ));
        let _ = unlink_from_group(manager, event_id, chr);
        let _ = unsummon(chr);
        return Err(format!(
            "the NPC was not registered as entity {DUEL_ENTITY_ID}"
        ));
    }
    match unlink_from_group(manager, event_id, chr) {
        Ok((before, after)) => summons_log(format_args!(
            "duel: 0x{chr:x} unlinked from summon group {event_id}: {before} -> {after} entries"
        )),
        Err(why) => summons_log(format_args!(
            "duel: 0x{chr:x} was not unlinked from summon group {event_id} ({why}); the HUD will \
             show its bar"
        )),
    }
    untrack_warp(manager, chr);
    Ok(chr)
}

/// The group node keyed `key` in `SummonBuddyManager`'s group tree (MSVC tree: head's parent is
/// the root, left/parent/right at `+0/+8/+0x10`, is-nil at `+0x19`, key at `+0x20`).
fn summon_group_node(manager: usize, key: i32) -> Option<usize> {
    // SAFETY: fault-tolerant reads.
    let head = unsafe { safe_read_usize(manager + summon_group::MANAGER_TREE_HEAD) }?;
    let mut node = unsafe { safe_read_usize(head + summon_group::NODE_PARENT) }?;
    for _ in 0..64 {
        if node == 0 || unsafe { safe_read_u8(node + summon_group::NODE_IS_NIL) } != Some(0) {
            return None;
        }
        let at = unsafe { safe_read_i32(node + summon_group::NODE_KEY) }?;
        if at == key {
            return Some(node);
        }
        let next = if key < at {
            summon_group::NODE_LEFT
        } else {
            summon_group::NODE_RIGHT
        };
        node = unsafe { safe_read_usize(node + next) }?;
    }
    None
}

/// Unlink and free `chr`'s entry in the group keyed `key`, the way the game's PostPhysics sweep
/// does (1.17.1 `0x1404b943c..0x1404b945f`): `prev->next = next`, `next->prev = prev`, the size
/// down by one, then the list allocator's `Deallocate(entry)`. The sentinel and the group node are
/// never touched; the game does not erase a group node either. Returns the size before and after.
///
/// The key is whatever was passed to `CreateSummonChr` as the creator id. Measured 2026-10-06:
/// the main player's group is keyed 0 on that session, and the new character is its tail entry.
fn unlink_from_group(manager: usize, key: i32, chr: usize) -> Result<(usize, usize), String> {
    let node = summon_group_node(manager, key).ok_or("no group under that key")?;
    // SAFETY: fault-tolerant reads; the writes go to entries just walked from the sentinel.
    let sentinel = unsafe { safe_read_usize(node + summon_group::NODE_LIST_HEAD) }
        .filter(|&h| h != 0)
        .ok_or("the group has no list")?;
    let before = unsafe { safe_read_usize(node + summon_group::NODE_LIST_SIZE) }
        .ok_or("the list size did not read")?;
    // From the tail backwards: the new character was pushed there.
    let mut entry = unsafe { safe_read_usize(sentinel + summon_group::ENTRY_PREV) }
        .ok_or("the list tail did not read")?;
    let mut found = None;
    for _ in 0..64 {
        if entry == sentinel || entry == 0 {
            break;
        }
        if unsafe { safe_read_usize(entry + summon_group::ENTRY_CHR) } == Some(chr) {
            found = Some(entry);
            break;
        }
        entry = unsafe { safe_read_usize(entry + summon_group::ENTRY_PREV) }
            .ok_or("an entry did not read")?;
    }
    let entry = found.ok_or("the character has no entry in the group")?;
    let next = unsafe { safe_read_usize(entry + summon_group::ENTRY_NEXT) }.ok_or("next")?;
    let prev = unsafe { safe_read_usize(entry + summon_group::ENTRY_PREV) }.ok_or("prev")?;
    let allocator = unsafe { safe_read_usize(node + summon_group::NODE_LIST_ALLOCATOR) }
        .filter(|&a| a != 0)
        .ok_or("the list has no allocator")?;
    let deallocate = unsafe { safe_read_usize(allocator) }
        .and_then(|vtable| unsafe { safe_read_usize(vtable + summon_group::ALLOCATOR_DEALLOCATE) })
        .filter(|&f| f != 0)
        .ok_or("the allocator's Deallocate did not read")?;
    // SAFETY: the same three writes and the same call the game's sweep makes on this list.
    unsafe {
        core::ptr::write_volatile((prev + summon_group::ENTRY_NEXT) as *mut usize, next);
        core::ptr::write_volatile((next + summon_group::ENTRY_PREV) as *mut usize, prev);
        core::ptr::write_volatile(
            (node + summon_group::NODE_LIST_SIZE) as *mut usize,
            before.saturating_sub(1),
        );
        let deallocate: DeallocateFn = core::mem::transmute(deallocate);
        deallocate(allocator, entry);
    }
    let after = unsafe { safe_read_usize(node + summon_group::NODE_LIST_SIZE) }.unwrap_or(0);
    Ok((before, after))
}

/// Stop the summon warp manager tracking `chr`: `CreateSummonChr` registered its handle, and the
/// manager would otherwise warp it back to the player. A handle it does not track is a no-op.
fn untrack_warp(manager: usize, chr: usize) {
    // SAFETY: fault-tolerant reads; the untrack has no null check, so a null manager is skipped.
    let Some(warp) = unsafe { safe_read_usize(manager + summon_group::MANAGER_WARP_MANAGER) }
        .filter(|&w| w != 0)
    else {
        return;
    };
    let Some(handle) = chr_handle(chr) else {
        return;
    };
    // SAFETY: `void(SummonBuddyWarpManager*, FieldInsHandle)`, handle by value.
    if let Ok(call) = unsafe { native::<WarpUntrackFn>(addr::WARP_UNTRACK, "WARP_UNTRACK") } {
        unsafe { call(warp, handle) };
    }
}

/// The character `handle` names now, through `WorldChrManImp::GetChrInsFromHandle`. This is the
/// liveness test: an entity lookup can find a different character with the same entity id.
pub(crate) fn chr_by_handle(handle: u64) -> Option<usize> {
    let wcm = world_chr_man()?;
    // SAFETY: `(WorldChrManImp*, FieldInsHandle*) -> ChrIns*`.
    let call: ChrFromHandleFn =
        unsafe { native(addr::GET_CHR_FROM_HANDLE, "GET_CHR_FROM_HANDLE") }.ok()?;
    let chr = unsafe { call(wcm, &handle) };
    (chr != 0).then_some(chr)
}

/// What the duel machine needs to judge a hidden character.
pub(crate) fn hidden_observation(chr: usize) -> Option<HiddenObservation> {
    // SAFETY: fault-tolerant reads; `IsDrawn(ChrIns*)`.
    let entry = unsafe { safe_read_usize(chr + chr_ins::CHR_SET_ENTRY) }.filter(|&e| e != 0)?;
    let load_status = unsafe { safe_read_u8(entry + chr_set_entry::LOAD_STATUS) }?;
    let flags = unsafe { safe_read_u8(entry + chr_set_entry::CONTROL_FLAGS) }?;
    let is_drawn: IsDrawnFn = unsafe { native(addr::IS_DRAWN, "IS_DRAWN") }.ok()?;
    let drawn = unsafe { is_drawn(chr) } != 0;
    Some(HiddenObservation {
        disabled: flags & 1 != 0,
        load_status,
        drawn,
    })
}

pub(crate) fn chr_handle(chr: usize) -> Option<u64> {
    // SAFETY: fault-tolerant read.
    unsafe { safe_read_usize(chr + chr_ins::HANDLE) }.map(|h| h as u64)
}

pub(crate) fn event_entity(chr: usize) -> Option<u32> {
    // SAFETY: fault-tolerant read.
    unsafe { safe_read_i32(chr + chr_ins::EVENT_ENTITY) }.map(|e| e as u32)
}

fn sign_man() -> Option<usize> {
    let base = module_base().ok()?;
    let event_man = read_global_ptr(base, CS_EVENT_MAN_GLOBAL_RVA, "CS_EVENT_MAN_GLOBAL_RVA");
    if event_man == 0 {
        return None;
    }
    // SAFETY: fault-tolerant reads.
    let ctrl = unsafe { safe_read_usize(event_man + sign::EVENT_MAN_CTRL) }.filter(|&c| c != 0)?;
    unsafe { safe_read_usize(ctrl + sign::CTRL_MAN) }.filter(|&m| m != 0)
}

/// Every `SosSignData*` in `SosSignMan`'s map, in order (MSVC `std::map`: head at `+0x10`, root =
/// head's parent, node `+0` left, `+8` parent, `+0x10` right, `+0x19` is-nil).
fn signs(man: usize) -> Vec<usize> {
    fn walk(node: usize, depth: u32, out: &mut Vec<usize>) {
        if node == 0 || depth > 64 {
            return;
        }
        // SAFETY: fault-tolerant reads.
        if unsafe { safe_read_u8(node + sign::NODE_IS_NIL) } != Some(0) {
            return;
        }
        walk(
            unsafe { safe_read_usize(node) }.unwrap_or(0),
            depth + 1,
            out,
        );
        if let Some(data) = unsafe { safe_read_usize(node + sign::NODE_DATA) }.filter(|&d| d != 0) {
            out.push(data);
        }
        walk(
            unsafe { safe_read_usize(node + 0x10) }.unwrap_or(0),
            depth + 1,
            out,
        );
    }
    let mut out = Vec::new();
    // SAFETY: fault-tolerant reads.
    if let Some(head) = unsafe { safe_read_usize(man + sign::MAN_MAP_HEAD) }.filter(|&h| h != 0) {
        walk(
            unsafe { safe_read_usize(head + 8) }.unwrap_or(0),
            0,
            &mut out,
        );
    }
    out
}

/// The sign keyed to `entity`, if one is down.
pub(crate) fn sign_for(entity: u32) -> Option<usize> {
    let man = sign_man()?;
    signs(man).into_iter().find(|&data| {
        // SAFETY: fault-tolerant read.
        unsafe { safe_read_i32(data + sign::DATA_NPC_ENTITY) }.map(|e| e as u32) == Some(entity)
    })
}

/// Place a red summon sign keyed to `entity` in `block`, then move it to physics position `at`.
/// Returns the `SosSignData*`. The game refuses a second sign for the same character, so an
/// existing one is moved instead of duplicated.
pub(crate) fn place_red_sign(
    entity: u32,
    block: u32,
    summon_flag: u32,
    dismiss_flag: u32,
    at: Vec4,
) -> Result<usize, String> {
    let man = sign_man().ok_or("no SosSignMan")?;
    if sign_for(entity).is_none() {
        // SAFETY: `PlaceNPCSummonSign` body, signature read from its EMEVD caller `0x14060b5a0`.
        let place: PlaceSignFn = unsafe { native(addr::PLACE_NPC_SIGN, "PLACE_NPC_SIGN") }?;
        let region: u32 = 0;
        unsafe {
            place(
                man,
                sign::TYPE_RED,
                &entity,
                &region,
                summon_flag as i32,
                &block,
                dismiss_flag as i32,
                0,
            );
        }
    }
    let data = sign_for(entity).ok_or("the game did not create the sign")?;
    for (i, value) in at.0[..3].iter().enumerate() {
        // SAFETY: `SosSignData+0x14` is the sign's physics position (measured live).
        unsafe { safe_write_i32(data + sign::DATA_POS + i * 4, value.to_bits() as i32) };
    }
    // SAFETY: `CreateSignSfx(SosSignMan*, SosSignData*)`.
    let sfx: SignSfxFn = unsafe { native(addr::CREATE_SIGN_SFX, "CREATE_SIGN_SFX") }?;
    unsafe { sfx(man, data) };
    Ok(data)
}

/// The `PartyMemberInfo` state of `handle`, or `None` when it is not in the party.
pub(crate) fn party_state(handle: u64) -> Option<i32> {
    let base = module_base().ok()?;
    let game_man = read_global_ptr(base, GAME_MAN_SINGLETON_RVA, "GAME_MAN_SINGLETON_RVA");
    if game_man == 0 {
        return None;
    }
    // SAFETY: fault-tolerant reads.
    let info = unsafe { safe_read_usize(game_man + party::GAME_MAN_PARTY) }.filter(|&p| p != 0)?;
    (0..party::ENTRY_COUNT).find_map(|i| {
        let entry = info + party::ENTRIES + i * party::ENTRY_SIZE;
        let held = unsafe { safe_read_usize(entry) }? as u64;
        (held == handle)
            .then(|| unsafe { safe_read_i32(entry + party::ENTRY_STATE) })
            .flatten()
    })
}

/// Remove the duel NPC through `SummonBuddyManager::RemoveChrIns(mgr, chr)`: the warp untrack,
/// then `WorldChrManImp::RemoveChrIns`, which queues the character on the delayed-delete list.
/// `NotifyBuddyUnsummon` would find nothing, because [`spawn_hidden`] unlinked the group entry it
/// looks the character up by. The game's own sweep makes this call and then unlinks; here the
/// entry is already gone, so no group walker can read the freed character.
pub(crate) fn unsummon(chr: usize) -> Result<(), String> {
    let manager = summon_buddy_manager().ok_or("no SummonBuddyManager")?;
    // SAFETY: `void(SummonBuddyManager*, ChrIns*)`.
    let call: RemoveChrFn = unsafe { native(addr::SUMMON_REMOVE_CHR, "SUMMON_REMOVE_CHR") }?;
    unsafe { call(manager, chr) };
    Ok(())
}
