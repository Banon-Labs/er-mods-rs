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
//! * It takes an **inventory index**, not a param id. The index is only valid after the item is
//!   granted, and WHICH index it is is the subject of the section below.
//! * Equipping an item into the slot it **already occupies toggles it off**. So every equip
//!   is preceded by `GetSlotIndexByItemIndex`, and skipped when the item is already there.
//!   Without that check a re-run strips the gear it just put on.
//!
//! # An item id cannot name one copy, so the equip does not ask by item id
//!
//! An ash of war lives on the **gaitem instance**, not in the item id. A build carrying four
//! Miséricordes that differ only by their ash gives three of them the identical item id
//! `0x000FB9C8`, and there is no id-shaped question whose answer is "the third one".
//! `EquipInventoryData::GetItemInventoryIdx` (`0x14024c560`) is exactly such a question -- its
//! whole body is `if (*itemId != -1) GetItemIndex(&inv->itemsData, itemId)` plus a null-handle
//! rejection -- and `InventoryItemsData::InsertItemIntoLookupMap` keeps the LOWEST index for a
//! repeated id. So it returns the same copy for all four positions, and `GetParamIdInSlot`, which
//! compares only the param id, then reports every one of them as verified.
//!
//! `plan.rs` grants the worn copy first to make that one answer the right one. That mitigates
//! exactly one position per id and cannot do more, which is why it is a mitigation and this is
//! the fix: the grant now hands each minted armament's `GaItemHandle` forward and the equip
//! resolves the inventory index from THAT, through
//! [`GET_ITEM_INDEX_BY_GAITEM_HANDLE`]. Where no handle is available the id lookup is still used
//! -- and [`EquipOutcome::by_item_id`] records that it was, because an unannounced fall back to
//! the ambiguous question is how this bug stayed invisible.

use er_build_import_core::equip::{
    CHR_ASM_SLOT_QUICK_BASE, EquipLedger, EquipRef, PlannedPosition, PositionKind, PositionResult,
};
use er_build_import_core::plan::ArmamentSkill;

/// `EquipItemToChrAsmSlot(ChrAsmSlot slot, MenuGaitem *item)`.
const EQUIP_ITEM_TO_CHR_ASM_SLOT: usize = 0x787c30;
// Both addresses are declared once in `er-game-base::rva`, which `er-better-refills` also
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
/// `CS::EquipInventoryData::GetItemIndexByGaitemHandle(EquipInventoryData*, uint *gaitemHandle)
/// -> int` -- the inventory question that can tell two copies of one armament apart.
///
/// Verified against the 1.16.2 dump and byte-checked in `eldenring-deobf.bin` (shift zero;
/// `41 56 48 83 EC 40 48 C7 44 24 20 FE FF FF FF`). It resolves the handle into a
/// `GaitemLookupResult`, reads the instance's item id, and then splits:
///
/// * **stackable** ids -- consumables, which have no per-instance identity -- fall back to the
///   same `GetItemIndex` the id lookup uses, because there is nothing to distinguish;
/// * **everything else**, armaments included, is answered by walking `0..=itemEntriesCount` and
///   returning the index of the entry whose own `InventoryItemEntry::GetGaitemHandle` EQUALS the
///   handle asked about. That is the exact-instance answer, and `-1` when the handle names
///   nothing in this inventory.
///
/// The engine uses it the same way: `FUN_140248670`, the ash-mounting path, feeds its result
/// straight to `EquipInventoryData::RemoveItem`. The first argument is
/// `&egd->equipInventoryData`, which is literally what `GetEquipInventoryData` returns, so it is
/// the pointer this module already holds.
const GET_ITEM_INDEX_BY_GAITEM_HANDLE: usize = 0x24c460;
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
/// `CS::EquipGameData::GetItemIdByQuickSlotIndex(egd, int *out, uint index) -> int*` --
/// THE QUICKBAR READ-BACK, and another out-parameter getter. Its whole body is
/// `if (index < 10) *out = entries[index + 0x16]; else *out = -1;`, so it answers for the ten
/// quickbar positions and refuses the pouch. The value it hands back is the CATEGORY-TAGGED item
/// id, not a bare param id.
const GET_ITEM_ID_BY_QUICK_SLOT_INDEX: usize = 0x247ee0;
/// `EquipGameData::equipmentEntries`, a `ChrAsmEquipEntries` -- 39 `int`s of category-tagged item
/// ids indexed by `ChrAsmSlot`, of which `0x16..0x1F` are `quickItem1..10` and `0x20..0x25` are
/// `pouch1..6`. This is the array the pouch writer `FUN_14024bb20` stores into
/// (`*(int *)(entries + (index + 0x20) * 4)`) and the array
/// [`GET_ITEM_ID_BY_QUICK_SLOT_INDEX`] reads, so a direct read of it is the same question the
/// game's own getter asks -- there just is no named getter that covers the pouch.
///
/// The offset is corroborated by its neighbour: `physicTears` sits at `840 + 156 = 996 = 0x3E4`,
/// which [`EQUIP_GAME_DATA_PHYSIC_TEARS`] already proves correct at runtime.
const EQUIP_GAME_DATA_EQUIPMENT_ENTRIES: usize = 0x348;

/// `WorldChrMan::mainPlayerIns`.
const WORLD_CHR_MAN_MAIN_PLAYER_INS: usize = 0x1e508;

/// `MenuGaitem` is 128 bytes; the equip path reads only two of its fields.
const MENU_GAITEM_SIZE: usize = 128;
/// `MenuGaitem::itemIdx`.
const MENU_GAITEM_ITEM_IDX: usize = 0x48;
/// `MenuGaitem::itemId`.
const MENU_GAITEM_ITEM_ID: usize = 0x4c;

type EquipFn = unsafe extern "system" fn(i32, *const u8);
type GetInventoryFn = unsafe extern "system" fn(usize) -> usize;
type GetItemIdxFn = unsafe extern "system" fn(usize, *const i32) -> i32;
/// `GetItemIndexByGaitemHandle(EquipInventoryData*, uint *gaitemHandle) -> int`. The handle is
/// read THROUGH the pointer and never written, so it is `*const`.
type GetItemIdxByHandleFn = unsafe extern "system" fn(usize, *const u32) -> i32;
type GetSlotFn = unsafe extern "system" fn(usize, i32) -> i32;
type GetParamIdInSlotFn = unsafe extern "system" fn(usize, i32) -> i32;
type GateFn = unsafe extern "system" fn(i32) -> u8;
type GetHandleFn = unsafe extern "system" fn(usize, *mut u32, u32) -> *mut u32;
type SetEntriesFn = unsafe extern "system" fn(usize, i32, *const u32, i32, bool, bool, bool);
type RefreshFn = unsafe extern "system" fn(usize);
type BroadcastFn = unsafe extern "system" fn(usize);
type SetQuickFn = unsafe extern "system" fn(usize, u32, *const u32, u32);
/// `CS::EquipGameData::GetItemIdByQuickSlotIndex(egd, int *out, uint index) -> int*` --
/// out-parameter form, like every other `Get*` here.
type GetQuickIdFn = unsafe extern "system" fn(usize, *mut i32, u32) -> *mut i32;
/// `CS::EquipGameData::GetPhysicTearBySlot(egd, int *out, uint slot) -> int*`.
///
/// An OUT-PARAMETER form, not a return-value getter: the body is literally
/// `*out = egd->physicTears[slot]`. Calling it as `(egd, slot)` puts the slot index in RDX and
/// the function writes through it -- a store to address 0, which took the game down once.
type GetTearFn = unsafe extern "system" fn(usize, *mut i32, u32) -> *mut i32;

/// Which minted armament instance belongs in which `ChrAsmSlot`.
///
/// # The join, and why it is on (item id, ash) rather than on order
///
/// The grant produces one [`ArmamentOutcome`] per armament, in plan order; the equip walks
/// positions in `ChrAsmSlot` order. Neither order is the other's, and matching them by position
/// would be an assumption about two lists built from different traversals of the build document.
///
/// So the join is on the pair that actually *identifies* an armament to a player: its item id and
/// the ash mounted on it. That pair is complete -- two copies agreeing on both are genuinely
/// interchangeable instances, so it does not matter which of them a position takes -- and it is
/// the smallest thing that is. An entry is consumed once claimed, so two positions asking for the
/// same armament with the same ash get two DIFFERENT copies rather than the same one twice.
///
/// The ash side of the pair comes from [`er_build_import_core::plan::equipped_armament_skills`], the
/// same table the post-import read-back is adjudicated against, and the gem encoding is decoded
/// by the grant's own [`crate::grant::gem_row_of`] rather than re-implemented here.
pub struct WornInstances {
    /// Every armament the grant minted a usable handle for.
    minted: Vec<Minted>,
    /// `ChrAsmSlot` -> the gem row the build wants worn there. An absent slot is a position that
    /// is not an armament at all, which is a different answer from an armament with no ash.
    wanted: std::collections::BTreeMap<i32, Option<u32>>,
}

/// One minted armament, and whether a position has already claimed it.
struct Minted {
    /// Category-tagged armament id, affinity included.
    item_id: u32,
    /// `EquipParamGem` row the plan asked to mount, or `None` for "no ash".
    gem: Option<u32>,
    /// The `GaItemHandle` the mint produced. Never zero: unusable ones are not kept.
    handle: u32,
    /// Claimed by a position already, so no second position may take it.
    taken: bool,
}

/// How a position's inventory index is going to be found.
enum Resolved {
    /// The handle of the exact instance minted for this position.
    Handle(u32),
    /// No handle; fall back to the ambiguous item-id lookup, for this stated reason.
    ById(&'static str),
}

impl WornInstances {
    /// Build the join from what the grant minted and what each armament slot should wear.
    ///
    /// # When this can pick the wrong ash, and why that is safe
    ///
    /// `wants` may name one slot more than once, because a planner payload can have several rows
    /// claiming one position. The last entry wins here, which is payload order;
    /// `equip::settle` breaks the same tie by the rows' `order` field, and
    /// `equipped_armament_skills` selects rows by the bare `equipIndex` while `equip_plan` selects
    /// them by the ACTIVE SET. Those agree on an ordinary payload and can disagree on a
    /// self-contradicting one -- which the import already reports as `CONTESTED`.
    ///
    /// When they do disagree this looks up an ash the plan did not place, finds no minted copy
    /// carrying it, and returns [`Resolved::ById`] with a reason. So the disagreement degrades to
    /// the old id lookup WITH A LOG LINE saying so, never to a confidently-wrong handle.
    pub fn new(armaments: &[crate::grant::ArmamentOutcome], wants: &[ArmamentSkill]) -> Self {
        Self {
            minted: armaments
                .iter()
                .filter(|arm| arm.handle != 0)
                .map(|arm| Minted {
                    item_id: arm.item_id,
                    gem: arm.wanted_gem,
                    handle: arm.handle,
                    taken: false,
                })
                .collect(),
            wanted: wants
                .iter()
                .map(|want| (want.slot, crate::grant::gem_row_of(want.weapon_skill)))
                .collect(),
        }
    }

    /// How many minted armaments are available to be claimed.
    pub fn available(&self) -> usize {
        self.minted.len()
    }

    /// Claim the instance that belongs in `slot`, or say why there is none.
    fn claim(&mut self, slot: i32, item_id: u32) -> Resolved {
        let Some(gem) = self.wanted.get(&slot).copied() else {
            return Resolved::ById(
                "this position is not an armament, so the grant minted no instance for it",
            );
        };
        // MATCHED WITHOUT THE UPGRADE LEVEL, which is the last two digits of an armament's item
        // id. The grant mints at the level the build asked for (`60500125`) while the equip plan
        // names the armament (`60500100`) -- it has no business deciding a level, and could not
        // anyway, since the clamp against this armament's real `ReinforceParamWeapon` rows is a
        // runtime question. Comparing the raw ids made every armament fall to the id lookup, and
        // then miss THERE too because the unlevelled id names nothing in the inventory: measured
        // 2026-08-23 as `0 position(s) found by minted gaitem handle` and 10/10 gear -> 8/10.
        let identity = armament_identity(item_id);
        let Some(found) = self
            .minted
            .iter_mut()
            .find(|arm| !arm.taken && armament_identity(arm.item_id) == identity && arm.gem == gem)
        else {
            return Resolved::ById(
                "no unclaimed minted armament matches this position's item id and ash",
            );
        };
        found.taken = true;
        Resolved::Handle(found.handle)
    }

    /// The levelled item id the grant actually minted for `slot`, if it minted one.
    ///
    /// The fall-back id lookup needs this: the plan's own id is unlevelled, so asking the
    /// inventory about it reports the armament missing when it is right there at +25.
    pub fn minted_id_for(&self, slot: i32, item_id: u32) -> Option<u32> {
        let gem = self.wanted.get(&slot).copied()?;
        let identity = armament_identity(item_id);
        self.minted
            .iter()
            .find(|arm| armament_identity(arm.item_id) == identity && arm.gem == gem)
            .map(|arm| arm.item_id)
    }
}

/// An armament's identity without its upgrade level: base row plus affinity.
///
/// The game's own normalisation -- `EquipParamWeapon::GetEntry` looks up `(paramId / 100) * 100`
/// precisely because the last two digits are the level.
fn armament_identity(item_id: u32) -> u32 {
    item_id / 100 * 100
}

/// What the equip pass observed while filling the plan.
///
/// # Where the counts are NOT
///
/// Deliberately: this struct holds evidence, never a score. The score lives in the
/// [`EquipLedger`] the caller opened over the PLAN, so a position the pass never visits stays
/// in the denominator instead of leaving with it. The field this replaced -- `quick_written`,
/// incremented once per dispatcher call and read back by nothing -- was both halves of that
/// mistake at once: a call count presented as a result, over a denominator that had already
/// dropped the positions it counted.
#[derive(Debug, Default)]
pub struct EquipOutcome {
    /// What the menu path's permission gate said, for the first few slots.
    pub gate: Vec<(i32, u8)>,
    /// Every quick/pouch/rune dispatch, as `(slot, wanted item id, inventory index, read back)`.
    /// The last element is the read-back, so a line of this trace is self-adjudicating.
    pub dispatch: Vec<(i32, u32, i32, i32)>,
    /// Item ids the inventory could not locate, i.e. the grant did not land.
    pub not_in_inventory: Vec<u32>,
    /// Positions whose inventory index came from the exact instance the grant minted.
    pub by_handle: usize,
    /// `(kind, slot, item id, why)` for every position that fell back to the item-id lookup.
    ///
    /// Kept in full rather than counted, and carrying its KIND, because the two cases are not
    /// equally serious. A talisman or a quickbar consumable has no per-instance identity to lose,
    /// so the id lookup is the right question for it and the entry is bookkeeping. An ARMAMENT
    /// falling back is the importer admitting it may have equipped an arbitrary twin, and that
    /// admission is worthless if the log buries it among the harmless ones.
    pub by_item_id: Vec<(PositionKind, i32, u32, &'static str)>,
    /// `(slot, item id, inventory index, the slot that already claimed it)` for every position
    /// whose inventory index was already spoken for by an earlier position in the same pass.
    ///
    /// EQUIPPING ONE ENTRY TWICE STRIPS THE FIRST SLOT. `EquipItemToChrAsmSlot` (`0x140787c30`)
    /// calls `FUN_140247160(egd, oldSlot, true)` -- unequip it from where it already sits --
    /// before writing the new slot, so a later position naming an entry an earlier position is
    /// already wearing tears the earlier one back off. The per-position read-back cannot see it:
    /// it runs before the position that will undo it. Observed 2026-08-23 on build
    /// 94252a868b4f2a, where the ledger recorded `12 planned = 12 verified` and both hands were
    /// empty by the end of the same import.
    pub index_collisions: Vec<(i32, u32, i32, i32)>,
    /// `(slot, expected, actual)` for the first few positions that read back wrong.
    pub mismatches: Vec<(i32, i32, i32)>,
    /// `(slot, expected, actual)` from the FINAL sweep, after every position has been written.
    ///
    /// The per-position read-back proves a write landed; only this proves it SURVIVED the rest
    /// of the pass. They are kept apart because a position that passes the first and fails the
    /// second is a different defect from one that never took.
    pub final_mismatches: Vec<(i32, i32, i32)>,
    /// The equip game data pointer was unusable, so nothing at all was attempted.
    pub no_inventory: bool,
}

/// Read what a quick/pouch/rune position currently holds. `-1` means empty.
///
/// Returns the CATEGORY-TAGGED item id, which is what all three of these positions store --
/// so the comparison is against [`EquipRef::item_id`], never `param_id`. Getting that wrong is
/// how the physick "verified 2/2" while showing error icons: a read-back only proves a value
/// round-tripped, so it has to be compared against the value the game would have written.
///
/// # Safety
///
/// Game thread, `egd` live, `index` in `0..=16`.
unsafe fn read_quick_position(
    module_base: usize,
    egd: usize,
    kind: PositionKind,
    index: u32,
) -> i32 {
    match kind {
        // The native getter, which covers exactly the ten quickbar positions.
        PositionKind::Quickbar => {
            let get: GetQuickIdFn =
                // Safety: verified 1.16.2 RVA within the loaded image.
                unsafe { core::mem::transmute(module_base + GET_ITEM_ID_BY_QUICK_SLOT_INDEX) };
            let mut out = -1i32;
            // Safety: out-parameter getter; the slot is ours and outlives the call.
            unsafe { get(egd, &raw mut out, index) };
            out
        }
        // No named getter reaches the pouch, so read the array the pouch writer stores into.
        PositionKind::Pouch => {
            let entry = egd
                + EQUIP_GAME_DATA_EQUIPMENT_ENTRIES
                + (CHR_ASM_SLOT_QUICK_BASE as usize + index as usize) * 4;
            // Safety: one int inside a fixed-size array of a live struct, at a verified offset.
            unsafe { *(entry as *const i32) }
        }
        // The rune is not in that array at all: the dispatcher's `index == 16` branch writes
        // `equipItemData + 0x88`, which is what GetEquippedGreatrune reads.
        PositionKind::GreatRune => unsafe { equipped_great_rune(module_base, egd) },
        // Every other kind is a ChrAsm equipment entry, answered by GetParamIdInSlot instead.
        _ => -1,
    }
}

/// Equip everything the ledger's plan asks for, recording each position's read-back into it.
///
/// `instances` carries the gaitem handles the grant minted; each armament position claims its own
/// so that copies of one armament differing only by ash reach the right hands. It is consumed as
/// the pass walks, which is why it is taken by mutable reference.
///
/// # Safety
///
/// Game thread, character in the world, items already granted.
pub unsafe fn equip_all(
    module_base: usize,
    egd: usize,
    ledger: &mut EquipLedger,
    instances: &mut WornInstances,
) -> EquipOutcome {
    let mut outcome = EquipOutcome::default();
    // `(slot, inventory index)` for every position this pass has already equipped, so a later
    // position cannot name an entry an earlier one is wearing. See the guard below for why that
    // is destructive rather than merely redundant.
    let mut claimed: Vec<(i32, i32)> = Vec::new();
    // Cloned so the ledger stays writable while the pass walks it. The list is at most ~20
    // entries, and holding a borrow of the thing being recorded into is not worth the saving.
    let planned: Vec<PlannedPosition> = ledger.planned().to_vec();

    // Safety: verified 1.16.2 RVAs within the loaded image.
    let equip: EquipFn = unsafe { core::mem::transmute(module_base + EQUIP_ITEM_TO_CHR_ASM_SLOT) };
    let get_inventory: GetInventoryFn =
        unsafe { core::mem::transmute(module_base + GET_EQUIP_INVENTORY_DATA) };
    let get_item_idx: GetItemIdxFn =
        unsafe { core::mem::transmute(module_base + GET_ITEM_INVENTORY_IDX) };
    let get_item_idx_by_handle: GetItemIdxByHandleFn =
        unsafe { core::mem::transmute(module_base + GET_ITEM_INDEX_BY_GAITEM_HANDLE) };
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
        outcome.no_inventory = true;
        for (at, position) in planned.iter().enumerate() {
            if position.kind == PositionKind::Physick {
                continue;
            }
            outcome.not_in_inventory.push(position.item.item_id);
            ledger.record(
                at,
                PositionResult::NotAttempted("the inventory pointer was null"),
            );
        }
        return outcome;
    }

    for (at, position) in planned.iter().enumerate() {
        // The physick is not a ChrAsmSlot and is written by `fill_physick`. It stays UNACCOUNTED
        // here on purpose: whoever writes it records it, and if nobody does, the ledger says so.
        if position.kind == PositionKind::Physick {
            continue;
        }
        let Some(slot) = position.slot else {
            ledger.record(
                at,
                PositionResult::NotAttempted("the plan gave this position no native slot"),
            );
            continue;
        };

        // THE ID TO ASK THE INVENTORY ABOUT, which for an armament is not the one the plan names:
        // the plan names the armament, the grant minted it at a level, and the level is part of
        // the id. Asking about the unlevelled id reports a +25 weapon as not in the inventory.
        let lookup_id = instances
            .minted_id_for(slot, position.item.item_id)
            .unwrap_or(position.item.item_id);
        let id = lookup_id as i32;

        // WHICH COPY. Asking by item id cannot answer that -- see the module header -- so the
        // handle the grant minted for this exact position is tried first, and the fall back to
        // the ambiguous question is recorded rather than taken quietly.
        let mut fell_back_to_id = None;
        let mut item_idx = match instances.claim(slot, position.item.item_id) {
            Resolved::Handle(handle) => {
                // Safety: the handle outlives the call and the inventory pointer is live; the
                // native reads the handle through the pointer and never writes it.
                let found = unsafe { get_item_idx_by_handle(inventory, &raw const handle) };
                if found < 0 {
                    // The instance was minted but this inventory does not hold it. That is a real
                    // finding, not a reason to silently equip a twin -- but the id lookup is
                    // still the better of the two remaining answers, so it is used and said.
                    fell_back_to_id =
                        Some("the minted handle names no entry in this inventory any more");
                }
                found
            }
            Resolved::ById(why) => {
                fell_back_to_id = Some(why);
                -1
            }
        };
        if item_idx < 0 {
            // Safety: `id` outlives the call; the inventory pointer is live.
            item_idx = unsafe { get_item_idx(inventory, &raw const id) };
        } else {
            outcome.by_handle += 1;
        }
        if let Some(why) = fell_back_to_id {
            outcome
                .by_item_id
                .push((position.kind, slot, lookup_id, why));
        }

        if item_idx < 0 {
            outcome.not_in_inventory.push(lookup_id);
            ledger.record(at, PositionResult::NotInInventory);
            continue;
        }

        // Quickbar, pouch and great-rune positions are not ChrAsm equipment entries at all -- the
        // engine routes them to a different writer, so SetEquipmentEntries would be the wrong
        // call even when the menu gate allows it. Unlike the equipment path this writer is a
        // plain assignment rather than a toggle, so there is nothing to check beforehand.
        if position.kind.is_quick_dispatch() {
            // Bounded before it is used: the pouch read-back below indexes a fixed-size array
            // with it, so an index the plan should never produce must not become a wild read.
            let index = match u32::try_from(slot - CHR_ASM_SLOT_QUICK_BASE) {
                Ok(index) if index <= QUICK_DISPATCH_MAX_INDEX => index,
                _ => {
                    ledger.record(
                        at,
                        PositionResult::NotAttempted(
                            "the slot is outside the quick/pouch/rune dispatcher's range",
                        ),
                    );
                    continue;
                }
            };
            let mut handle = [0u32; 2];
            // Safety: engine-owned inventory, validated index, our own handle buffer.
            unsafe { get_handle(inventory, handle.as_mut_ptr(), item_idx as u32) };
            // Safety: the native dispatcher for quick/pouch/rune.
            unsafe { set_quick(egd, index, handle.as_ptr(), item_idx as u32) };
            // Safety: same context; a read of the position that was just written.
            let actual = unsafe { read_quick_position(module_base, egd, position.kind, index) };
            outcome
                .dispatch
                .push((slot, position.item.item_id, item_idx, actual));
            ledger.record(at, verdict(&mut outcome, slot, id, actual));
            continue;
        }

        // ONE INVENTORY ENTRY, ONE SLOT. Equipping an entry that an earlier position in this very
        // pass is already wearing does not add a second copy -- `EquipItemToChrAsmSlot` unequips it
        // from the earlier slot first (`FUN_140247160(egd, oldSlot, true)`), so the later write
        // silently strips the earlier one and the earlier position's read-back, taken before this
        // one ran, still says Verified. Refusing here keeps a wrong answer out of the character
        // AND out of the log; the position is recorded as a failure, which is what it is.
        if let Some((held_by, _)) = claimed
            .iter()
            .copied()
            .find(|(_, claimed_idx)| *claimed_idx == item_idx)
        {
            outcome
                .index_collisions
                .push((slot, position.item.item_id, item_idx, held_by));
            ledger.record(
                at,
                PositionResult::NotAttempted(
                    "another position in this pass is already wearing this exact inventory entry; \
                     equipping it here would strip that slot instead of filling this one",
                ),
            );
            continue;
        }
        claimed.push((slot, item_idx));

        // Safety: same context. Asking where the item currently sits.
        let current = unsafe { get_slot(egd, item_idx) };
        if current == slot {
            // Calling the handler here would TOGGLE the item off.
            ledger.record(at, PositionResult::Already);
            continue;
        }

        // Ask the menu path's gate what it thinks, purely to record it.
        // Safety: a pure predicate over engine singletons, already known non-null here.
        let permitted = unsafe { gate(slot) };
        if outcome.gate.len() < GATE_ANSWERS_KEPT {
            outcome.gate.push((slot, permitted));
        }

        if permitted != 0 {
            let mut gaitem = [0u8; MENU_GAITEM_SIZE];
            gaitem[MENU_GAITEM_ITEM_IDX..MENU_GAITEM_ITEM_IDX + 4]
                .copy_from_slice(&item_idx.to_le_bytes());
            gaitem[MENU_GAITEM_ITEM_ID..MENU_GAITEM_ITEM_ID + 4].copy_from_slice(&id.to_le_bytes());
            // Safety: the handler reads only itemIdx and itemId on this path.
            unsafe { equip(slot, gaitem.as_ptr()) };
        } else {
            // The menu handler refuses outside its own context, so write through the layer it
            // itself uses once its gate passes. These are the engine's own setters -- the only
            // thing skipped is the menu's permission question.
            let mut handle = [0u32; 2];
            // Safety: engine-owned inventory, validated index, our own handle buffer.
            unsafe { get_handle(inventory, handle.as_mut_ptr(), item_idx as u32) };
            // Safety: the flags are the ones the menu path passes.
            unsafe { set_entries(egd, slot, handle.as_ptr(), item_idx, true, true, false) };
            // Safety: the refresh and broadcast the menu path always runs after a write.
            unsafe { refresh(egd) };
            if main_player != 0 {
                unsafe { broadcast(main_player) };
            }
        }

        // Read the slot back. The handler returns void and declines silently, so this is the
        // only thing that distinguishes "equipped" from "asked politely and was ignored".
        // The ChrAsm getter masks the category nibble off, so the comparison here is against
        // the BARE param id -- the opposite of the quick/pouch/rune read-back above.
        // Safety: same context; a plain read of the slot's current param id.
        let actual = unsafe { param_in_slot(egd, slot) };
        let expected = position.item.param_id as i32;
        ledger.record(at, verdict(&mut outcome, slot, expected, actual));
    }

    // THE SWEEP THAT ACTUALLY PROVES IT. Every read-back above happened before the positions after
    // it were written, so each one proves only that its own write landed -- not that it was still
    // there at the end. A position stripped by a later equip passes the first check and fails this
    // one, and that difference is the whole reason the two are recorded separately.
    for (at, position) in planned.iter().enumerate() {
        // Same exclusions the pass itself uses: the physick is not a ChrAsmSlot, and the
        // quick/pouch/rune positions read back through their own dispatcher rather than ChrAsm.
        if position.kind == PositionKind::Physick || position.kind.is_quick_dispatch() {
            continue;
        }
        let Some(slot) = position.slot else {
            continue;
        };
        // Safety: same context; a plain read of the slot's current param id.
        let actual = unsafe { param_in_slot(egd, slot) };
        let expected = position.item.param_id as i32;
        if actual != expected {
            if outcome.final_mismatches.len() < MISMATCHES_KEPT {
                outcome.final_mismatches.push((slot, expected, actual));
            }
            ledger.record(at, PositionResult::Mismatch { expected, actual });
        }
    }

    outcome
}

/// The dispatcher's highest index: 0..9 quickbar, 10..15 pouch, 16 great rune. `FUN_140249a50`
/// itself returns without doing anything past this, and the pouch read-back indexes a fixed-size
/// array, so anything higher is refused rather than passed on.
const QUICK_DISPATCH_MAX_INDEX: u32 = 16;
/// How many gate answers to keep for the log.
const GATE_ANSWERS_KEPT: usize = 8;
/// How many read-back mismatches to keep for the log.
const MISMATCHES_KEPT: usize = 8;

/// Turn one read-back into a verdict, recording the failing ones for the log.
fn verdict(outcome: &mut EquipOutcome, slot: i32, expected: i32, actual: i32) -> PositionResult {
    if actual == expected {
        return PositionResult::Verified;
    }
    if outcome.mismatches.len() < MISMATCHES_KEPT {
        outcome.mismatches.push((slot, expected, actual));
    }
    PositionResult::Mismatch { expected, actual }
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
