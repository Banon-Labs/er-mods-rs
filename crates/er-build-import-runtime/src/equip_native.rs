//! Putting the granted items on the character, through the menu's own equip handler.
//!
//! # The handler, not a reimplementation
//!
//! `EquipItemToChrAsmSlot` is what the inventory UI calls. It resolves the gaitem handle,
//! clears whatever previously occupied the slot, writes the equipment entry and fires
//! `BroadCastEquipmentChange`. Doing those four things by hand would be reimplementing a
//! function the game already exports, so this builds the one argument it needs and calls it.
//!
//! # Two traps it hides
//!
//! * It takes an **inventory index**, not a param id. The index comes from
//!   `EquipInventoryData::GetItemInventoryIdx`, and is only valid after the item is granted.
//! * Equipping an item into the slot it **already occupies toggles it off**. So every equip
//!   is preceded by `GetSlotIndexByItemIndex`, and skipped when the item is already there.
//!   Without that check a re-run strips the gear it just put on.

use er_build_import::equip::{EquipPlan, EquipRef, armament_slot};

/// `EquipItemToChrAsmSlot(ChrAsmSlot slot, MenuGaitem *item)`.
const EQUIP_ITEM_TO_CHR_ASM_SLOT: usize = 0x787c30;
// Both addresses are declared once in `er-game-base::rva`, which `er-better-refills-dll` also
// reads them from.
use er_game_base::rva::{
    GET_EQUIP_INVENTORY_DATA_RVA as GET_EQUIP_INVENTORY_DATA,
    GET_EQUIPPED_GREATRUNE_RVA as GET_EQUIPPED_GREATRUNE,
    GET_ITEM_INVENTORY_IDX_RVA as GET_ITEM_INVENTORY_IDX,
    GET_PARAM_ID_IN_SLOT_RVA as GET_PARAM_ID_IN_SLOT,
    GET_PHYSIC_TEAR_BY_SLOT_RVA as GET_PHYSIC_TEAR_BY_SLOT,
};
/// `CS::EquipGameData::GetSlotIndexByItemIndex(egd, itemIdx) -> ChrAsmSlot`.
const GET_SLOT_INDEX_BY_ITEM_INDEX: usize = 0x248440;
/// `FUN_140788a90(ChrAsmSlot) -> bool` -- the permission gate `EquipItemToChrAsmSlot` consults
/// before doing anything. Probed only, never relied on: when it says no, that function returns
/// void having silently done nothing.
const EQUIP_PERMISSION_GATE: usize = 0x788a90;
/// `CS::EquipInventoryData::GetGaItemHandleByIndex(inv, uint *out, uint itemIdx) -> uint*`.
const GET_GAITEM_HANDLE_BY_INDEX: usize = 0x24c7b0;
/// `CS::EquipGameData::SetEquipmentEntries(egd, slot, uint *gaitemHandle, int itemIdx,
/// bool, bool, bool isArrowOrBolt)` -- the actual equipment writer.
const SET_EQUIPMENT_ENTRIES: usize = 0x249160;
/// `FUN_140249a90(egd)` -- the post-write refresh the menu path always calls.
const EQUIP_REFRESH: usize = 0x249a90;
/// `BroadCastEquipmentChange(PlayerIns*)` -- tells the rest of the engine the loadout moved.
const BROADCAST_EQUIPMENT_CHANGE: usize = 0x658c90;
/// `FUN_140249a50(egd, index, uint *gaitemHandle, uint itemIdx)` -- the single native entry for
/// the quickbar, the pouch and the great rune. It dispatches internally:
/// `index < 10` -> `EquipItemData::SetQuickSlotItem`, `10..16` -> the pouch writer, `16` ->
/// `SetGreatRune`. So all three of those live behind one call, and the index is exactly
/// `ChrAsmSlot - 0x16`, which is what `ConvertChrAsmSlotToQuickItemOrPouchSlot` computes.
const SET_QUICK_OR_POUCH_OR_RUNE: usize = 0x249a50;
/// `CS::EquipGameData::GetPhysicTearBySlot(egd, slot) -> int` -- the physick read-back.
/// `EquipGameData::physicTears`, `int[3]` (empty == `-1`). Confirmed by the getter's own
/// addressing, `MOV ECX,dword ptr [RCX + RAX*0x4 + 0x3e4]`. Recorded, not written: see
/// [`read_physick`] for why writing it directly produced error icons in the flask.
const EQUIP_GAME_DATA_PHYSIC_TEARS: usize = 0x3e4;
/// The lowest `ChrAsmSlot` handled by the quick/pouch/rune writer.
const CHR_ASM_SLOT_QUICK_MIN: i32 = 0x16;
/// `ChrAsmSlot::GreatRune`, the dispatcher's `index == 16` case.
const CHR_ASM_SLOT_GREAT_RUNE: i32 = 0x26;

/// `WorldChrMan::mainPlayerIns`.
const WORLD_CHR_MAN_MAIN_PLAYER_INS: usize = 0x1e508;

/// `MenuGaitem` is 128 bytes; the equip path reads only two of its fields.
const MENU_GAITEM_SIZE: usize = 128;
/// `MenuGaitem::itemIdx`.
const MENU_GAITEM_ITEM_IDX: usize = 0x48;
/// `MenuGaitem::itemId`.
const MENU_GAITEM_ITEM_ID: usize = 0x4c;

/// `ChrAsmSlot::ProtectorHead`; chest, hands and legs follow consecutively.
const CHR_ASM_SLOT_PROTECTOR_HEAD: i32 = 12;
/// `ChrAsmSlot::Accessory1`; the other three talisman slots follow consecutively.
const CHR_ASM_SLOT_ACCESSORY_1: i32 = 17;
/// `ChrAsmSlot` base for the quickbar; pouch continues from `+10`.
const CHR_ASM_SLOT_QUICK_BASE: i32 = 0x16;

type EquipFn = unsafe extern "system" fn(i32, *const u8);
type GetInventoryFn = unsafe extern "system" fn(usize) -> usize;
type GetItemIdxFn = unsafe extern "system" fn(usize, *const i32) -> i32;
type GetSlotFn = unsafe extern "system" fn(usize, i32) -> i32;
type GetParamIdInSlotFn = unsafe extern "system" fn(usize, i32) -> i32;
type GateFn = unsafe extern "system" fn(i32) -> u8;
type GetHandleFn = unsafe extern "system" fn(usize, *mut u32, u32) -> *mut u32;
type SetEntriesFn = unsafe extern "system" fn(usize, i32, *const u32, i32, bool, bool, bool);
type RefreshFn = unsafe extern "system" fn(usize);
type BroadcastFn = unsafe extern "system" fn(usize);
type SetQuickFn = unsafe extern "system" fn(usize, u32, *const u32, u32);
/// `CS::EquipGameData::GetPhysicTearBySlot(egd, int *out, uint slot) -> int*`.
///
/// An OUT-PARAMETER form, not a return-value getter: the body is literally
/// `*out = egd->physicTears[slot]`. Calling it as `(egd, slot)` puts the slot index in RDX and
/// the function writes through it -- a store to address 0, which took the game down once.
type GetTearFn = unsafe extern "system" fn(usize, *mut i32, u32) -> *mut i32;

/// What the equip pass achieved.
#[derive(Debug, Default)]
pub struct EquipOutcome {
    /// Positions the plan asked to fill.
    pub requested: usize,
    /// Positions whose contents afterwards match what was asked for. THE number that counts.
    pub verified: usize,
    /// Positions where the handler was called but the slot did not end up holding the item.
    pub silent_noop: usize,
    /// Items already in the right slot, so skipped rather than toggled off.
    pub already: usize,
    /// Items the inventory could not locate, i.e. the grant did not land.
    pub not_in_inventory: Vec<u32>,
    /// `(slot, expected param id, actual param id)` for the first few failures.
    pub mismatches: Vec<(i32, i32, i32)>,
    /// What the menu path's permission gate said, for the first few slots.
    pub gate: Vec<(i32, u8)>,
    /// Quickbar / pouch / great-rune positions written through the native dispatcher.
    pub quick_written: usize,
    /// Every request, as `(slot, item id, inventory index)`, so a missing one is visible
    /// instead of having to be inferred from a total that does not add up.
    pub trace: Vec<(i32, u32, i32)>,
}

/// One equip request: the target native slot and the item to put in it.
struct Request<'a> {
    slot: i32,
    item: &'a EquipRef,
}

/// Collect every position the plan wants filled, as native slot numbers.
fn requests(plan: &EquipPlan) -> Vec<Request<'_>> {
    let mut out = Vec::new();

    for (index, entry) in plan.armaments.iter().enumerate() {
        if let (Some(item), Ok(index)) = (entry.as_ref(), u32::try_from(index))
            && let Some(slot) = armament_slot(index)
        {
            out.push(Request { slot, item });
        }
    }
    // ProtectorIndexToChrAsmSlot is literally `index + ProtectorHead`, in head/chest/hands/legs
    // order -- which is the order these four fields are listed in.
    for (offset, entry) in [&plan.head, &plan.body, &plan.arms, &plan.legs]
        .into_iter()
        .enumerate()
    {
        if let (Some(item), Ok(offset)) = (entry.as_ref(), i32::try_from(offset)) {
            out.push(Request {
                slot: CHR_ASM_SLOT_PROTECTOR_HEAD + offset,
                item,
            });
        }
    }
    for (index, entry) in plan.talismans.iter().enumerate() {
        if let (Some(item), Ok(index)) = (entry.as_ref(), i32::try_from(index)) {
            out.push(Request {
                slot: CHR_ASM_SLOT_ACCESSORY_1 + index,
                item,
            });
        }
    }
    // ConvertChrAsmSlotToQuickItemOrPouchSlot maps 0x16..0x1F to quickbar 0..9 and 0x20..0x25
    // to pouch 10..15, so the native slot is the planner's position plus 0x16.
    for (index, entry) in plan.quickbar.iter().chain(plan.pouch.iter()).enumerate() {
        if let (Some(item), Ok(index)) = (entry.as_ref(), i32::try_from(index)) {
            out.push(Request {
                slot: CHR_ASM_SLOT_QUICK_BASE + index,
                item,
            });
        }
    }
    // The great rune rides the same dispatcher one position past the pouch: that function's
    // `index == 16` branch is SetGreatRune, and 16 is exactly 0x26 - 0x16.
    if let Some(item) = plan.great_rune.as_ref() {
        out.push(Request {
            slot: CHR_ASM_SLOT_GREAT_RUNE,
            item,
        });
    }

    out
}

/// Equip everything the plan asks for.
///
/// # Safety
///
/// Game thread, character in the world, items already granted.
pub unsafe fn equip_all(module_base: usize, egd: usize, plan: &EquipPlan) -> EquipOutcome {
    let wanted = requests(plan);
    let mut outcome = EquipOutcome {
        requested: wanted.len(),
        ..EquipOutcome::default()
    };

    // Safety: verified 1.16.2 RVAs within the loaded image.
    let equip: EquipFn = unsafe { core::mem::transmute(module_base + EQUIP_ITEM_TO_CHR_ASM_SLOT) };
    let get_inventory: GetInventoryFn =
        unsafe { core::mem::transmute(module_base + GET_EQUIP_INVENTORY_DATA) };
    let get_item_idx: GetItemIdxFn =
        unsafe { core::mem::transmute(module_base + GET_ITEM_INVENTORY_IDX) };
    let get_slot: GetSlotFn =
        unsafe { core::mem::transmute(module_base + GET_SLOT_INDEX_BY_ITEM_INDEX) };
    let param_in_slot: GetParamIdInSlotFn =
        unsafe { core::mem::transmute(module_base + GET_PARAM_ID_IN_SLOT) };
    let gate: GateFn = unsafe { core::mem::transmute(module_base + EQUIP_PERMISSION_GATE) };
    let get_handle: GetHandleFn =
        unsafe { core::mem::transmute(module_base + GET_GAITEM_HANDLE_BY_INDEX) };
    let set_entries: SetEntriesFn =
        unsafe { core::mem::transmute(module_base + SET_EQUIPMENT_ENTRIES) };
    let refresh: RefreshFn = unsafe { core::mem::transmute(module_base + EQUIP_REFRESH) };
    let broadcast: BroadcastFn =
        unsafe { core::mem::transmute(module_base + BROADCAST_EQUIPMENT_CHANGE) };
    let set_quick: SetQuickFn =
        unsafe { core::mem::transmute(module_base + SET_QUICK_OR_POUCH_OR_RUNE) };

    // Resolved once: BroadCastEquipmentChange wants the live PlayerIns.
    let main_player = {
        use eldenring::cs::WorldChrMan;
        use fromsoftware_shared::FromStatic;
        match WorldChrMan::instance_ptr() {
            Ok(wcm) if !wcm.is_null() => {
                // Safety: one pointer read at a verified offset in a live singleton.
                unsafe { *((wcm as usize + WORLD_CHR_MAN_MAIN_PLAYER_INS) as *const usize) }
            }
            _ => 0,
        }
    };

    // Safety: engine-owned pointer, read only.
    let inventory = unsafe { get_inventory(egd) };
    if inventory == 0 {
        outcome.not_in_inventory = wanted.iter().map(|req| req.item.item_id).collect();
        return outcome;
    }

    for request in &wanted {
        let id = request.item.item_id as i32;
        // Safety: `id` outlives the call; the inventory pointer is live.
        let item_idx = unsafe { get_item_idx(inventory, &raw const id) };
        if request.slot >= CHR_ASM_SLOT_QUICK_MIN {
            outcome
                .trace
                .push((request.slot, request.item.item_id, item_idx));
        }
        if item_idx < 0 {
            outcome.not_in_inventory.push(request.item.item_id);
            continue;
        }

        // Quickbar, pouch and great-rune slots are not ChrAsm equipment entries at all -- the
        // engine routes them to a different writer, so SetEquipmentEntries would be the wrong
        // call even when the menu gate allows it.
        if request.slot >= CHR_ASM_SLOT_QUICK_MIN {
            let mut handle = [0u32; 2];
            // Safety: engine-owned inventory, validated index, our own handle buffer.
            unsafe { get_handle(inventory, handle.as_mut_ptr(), item_idx as u32) };
            let index = (request.slot - CHR_ASM_SLOT_QUICK_MIN) as u32;
            // Safety: the native dispatcher for quick/pouch/rune.
            unsafe { set_quick(egd, index, handle.as_ptr(), item_idx as u32) };
            outcome.quick_written += 1;
            continue;
        }

        // Safety: same context. Asking where the item currently sits.
        let current = unsafe { get_slot(egd, item_idx) };
        if current == request.slot {
            // Calling the handler here would TOGGLE the item off.
            outcome.already += 1;
            continue;
        }

        // Ask the menu path's gate what it thinks, purely to record it.
        // Safety: a pure predicate over engine singletons, already known non-null here.
        let permitted = unsafe { gate(request.slot) };
        if outcome.gate.len() < 8 {
            outcome.gate.push((request.slot, permitted));
        }

        if permitted != 0 {
            let mut gaitem = [0u8; MENU_GAITEM_SIZE];
            gaitem[MENU_GAITEM_ITEM_IDX..MENU_GAITEM_ITEM_IDX + 4]
                .copy_from_slice(&item_idx.to_le_bytes());
            gaitem[MENU_GAITEM_ITEM_ID..MENU_GAITEM_ITEM_ID + 4].copy_from_slice(&id.to_le_bytes());
            // Safety: the handler reads only itemIdx and itemId on this path.
            unsafe { equip(request.slot, gaitem.as_ptr()) };
        } else {
            // The menu handler refuses outside its own context, so write through the layer it
            // itself uses once its gate passes. These are the engine's own setters -- the only
            // thing skipped is the menu's permission question.
            let mut handle = [0u32; 2];
            // Safety: engine-owned inventory, validated index, our own handle buffer.
            unsafe { get_handle(inventory, handle.as_mut_ptr(), item_idx as u32) };
            // Safety: the flags are the ones the menu path passes.
            unsafe {
                set_entries(
                    egd,
                    request.slot,
                    handle.as_ptr(),
                    item_idx,
                    true,
                    true,
                    false,
                )
            };
            // Safety: the refresh and broadcast the menu path always runs after a write.
            unsafe { refresh(egd) };
            if main_player != 0 {
                unsafe { broadcast(main_player) };
            }
        }

        // Read the slot back. The handler returns void and declines silently, so this is the
        // only thing that distinguishes "equipped" from "asked politely and was ignored".
        // Safety: same context; a plain read of the slot's current param id.
        let actual = unsafe { param_in_slot(egd, request.slot) };
        let expected = request.item.param_id as i32;
        if actual == expected {
            outcome.verified += 1;
        } else {
            outcome.silent_noop += 1;
            if outcome.mismatches.len() < 8 {
                outcome.mismatches.push((request.slot, expected, actual));
            }
        }
    }

    outcome
}

/// Fill the Flask of Wondrous Physick.
///
/// `EquipGameData::physicTears` is `int[3]` at `+0x3e4` (empty == `-1`), confirmed by the
/// getter's own addressing `MOV ECX,[RCX + RAX*4 + 0x3e4]`. No native setter for it exists in
/// the dump under any searchable name, so this writes the field -- but it now writes the right
/// KIND of value, which the first attempt did not.
///
/// The first attempt stored the tear's bare param id and produced error icons in game. The
/// field actually holds the **category-tagged** id: a flask populated by the game itself reads
/// back as `0x40001FC1`, i.e. nibble 4 (goods) in the high nibble. Writing `11003` where
/// `0x40002AFB` belongs gives the UI an id it cannot resolve, which is exactly an error icon.
///
/// That first attempt also "verified 2/2", because it compared the read-back against the same
/// wrong value it had written. A read-back only proves a value round-tripped; it cannot prove
/// the value means what you think. The comparison here is against the tagged id for that reason.
///
/// # Safety
///
/// Game thread, `egd` live.
pub unsafe fn fill_physick(module_base: usize, egd: usize, tears: &[Option<EquipRef>]) -> usize {
    let get_tear: GetTearFn =
        unsafe { core::mem::transmute(module_base + GET_PHYSIC_TEAR_BY_SLOT) };
    let mut verified = 0;
    for (index, tear) in tears.iter().enumerate().take(2) {
        let Some(tear) = tear else { continue };
        // The TAGGED id, not param_id.
        let wanted = tear.item_id as i32;
        // Safety: one int of an int[3] at a verified offset in live save data.
        unsafe { *((egd + EQUIP_GAME_DATA_PHYSIC_TEARS + index * 4) as *mut i32) = wanted };
        let mut got = -1i32;
        // Safety: the native out-parameter getter.
        unsafe { get_tear(egd, &raw mut got, index as u32) };
        if got == wanted {
            verified += 1;
        }
    }
    verified
}

/// Read what the flask currently holds, for the log.
///
/// # Safety
///
/// Game thread, `egd` live.
pub unsafe fn read_physick(module_base: usize, egd: usize) -> [i32; 2] {
    let get_tear: GetTearFn =
        unsafe { core::mem::transmute(module_base + GET_PHYSIC_TEAR_BY_SLOT) };
    let mut out = [-1i32; 2];
    for (index, slot) in out.iter_mut().enumerate() {
        let mut got = -1i32;
        // Safety: the native out-parameter getter.
        unsafe { get_tear(egd, &raw mut got, index as u32) };
        *slot = got;
    }
    out
}

/// Read back the equipped great rune. `-1` means none.
///
/// Another OUT-PARAMETER getter, like `GetPhysicTearBySlot`: the disassembly is
/// `ADD RCX,0x288 / MOV RBX,RDX / CALL ... / MOV RAX,RBX`, i.e. RDX is a pointer the callee
/// writes through and RAX is just that same pointer handed back. Treating it as a scalar
/// getter passes the caller's second argument as a destination address and stores through it.
///
/// # Safety
///
/// Game thread, `egd` live.
pub unsafe fn equipped_great_rune(module_base: usize, egd: usize) -> i32 {
    // THREE arguments. The outer wrapper at 0x140247900 is only
    //     ADD RCX,0x288 / MOV RBX,RDX / CALL 0x14024f390 / MOV RAX,RBX
    // -- it never writes R8, so the slot argument passes straight through from the caller into
    // `CS::EquipItemData::GetEquippedGreatrune(EquipItemData*, int *out, int slot)`, whose body
    // begins `*out = -1; if (slot == 0 && ...)`. Calling it with two arguments leaves R8 holding
    // whatever the call site happened to have, the `slot == 0` test fails, and it reports -1 no
    // matter what is equipped. That produced three runs of "the rune will not equip" when the
    // rune was fine and the QUESTION was malformed.
    type Fn_ = unsafe extern "system" fn(usize, *mut i32, i32) -> *mut i32;
    // Safety: verified RVA; the out slot is ours and outlives the call.
    let get: Fn_ = unsafe { core::mem::transmute(module_base + GET_EQUIPPED_GREATRUNE) };
    let mut out = -1i32;
    unsafe { get(egd, &raw mut out, 0) };
    out
}
