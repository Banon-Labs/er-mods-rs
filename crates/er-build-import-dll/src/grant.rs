//! Handing the build's items to the player, through the game's own inventory call.
//!
//! # Why not the community `ItemGib` table
//!
//! The Cheat Engine workflow this replaces writes a 16-byte record array and calls a routine
//! found by signature scan. The game already exports the operation:
//! `CS::EquipGameData::AddInventoryEquipByItemId` is what the engine itself uses, it resolves
//! the gaitem handle through `CSGaitemImp`, and it takes the same category-tagged item id the
//! catalogue produces. Calling it means the engine owns the mutation, not us.
//!
//! # Why this runs on the game thread
//!
//! It mutates the player's inventory and touches the `CSGaitemImp` singleton. Doing that from
//! the fetch worker would race the game's own inventory code. The caller must invoke
//! [`grant_all`] from a `CSTaskImp` task.
//!
//! # Why every grant is read back
//!
//! `AddInventoryEquipByItemId` returns an int this code does not pretend to understand, and a
//! call that "succeeded" is not evidence the item exists. Each grant is confirmed with
//! `EquipInventoryData::GetQuantityByItemId`, so the log reports what the inventory actually
//! holds rather than how many calls were made.

use er_build_import::plan::Grant;

/// `CS::EquipGameData::AddInventoryEquipByItemId(egd, int *itemId, u32 amount,
/// bool updateTrophyStats, bool updateAutoEquip) -> int`.
const ADD_INVENTORY_EQUIP_BY_ITEM_ID: usize = 0x246840;
// Declared once in `er-game-base::rva`; `er-better-refills-dll` and `equip_native` read the
// same values from there.
use er_game_base::rva::{
    GET_EQUIP_INVENTORY_DATA_RVA as GET_EQUIP_INVENTORY_DATA,
    GET_QUANTITY_BY_ITEM_ID_RVA as GET_QUANTITY_BY_ITEM_ID,
};

/// `GameDataMan::main_player_game_data`, read as a raw pointer.
///
/// The typed upstream field is an `OwnedPtr`, which asserts non-null; before a character is
/// loaded this slot really is null, so it is read as a plain pointer and checked.
const GAME_DATA_MAN_PLAYER_OFFSET: usize = 0x08;
/// `PlayerGameData::equipGameData`, held by value.
const PLAYER_GAME_DATA_EQUIP_OFFSET: usize = 0x2b0;

type AddInventoryFn = unsafe extern "system" fn(usize, *const i32, u32, bool, bool) -> i32;
type GetInventoryFn = unsafe extern "system" fn(usize) -> usize;
type GetQuantityFn = unsafe extern "system" fn(usize, *const i32) -> i32;

/// What actually ended up in the inventory.
#[derive(Debug, Default)]
pub struct GrantOutcome {
    /// Grants the plan asked for.
    pub attempted: usize,
    /// Grants confirmed present afterwards by reading the inventory back.
    pub confirmed: usize,
    /// Item ids that were requested but could not be found afterwards.
    pub missing: Vec<u32>,
}

/// The live `EquipGameData*`, or `None` before a character is loaded.
///
/// # Safety
///
/// Must be called on the game thread; the pointers it walks are engine-owned.
pub unsafe fn equip_game_data() -> Option<usize> {
    use eldenring::cs::GameDataMan;
    use fromsoftware_shared::FromStatic;

    let gdm = GameDataMan::instance_ptr().ok()? as usize;
    if gdm == 0 {
        return None;
    }
    // Safety: the layout is `GameDataMan + 0x08 -> PlayerGameData*`, and the value is checked
    // for null rather than assumed present -- it genuinely is null at the title screen.
    let pgd = unsafe { *((gdm + GAME_DATA_MAN_PLAYER_OFFSET) as *const usize) };
    if pgd == 0 {
        return None;
    }
    Some(pgd + PLAYER_GAME_DATA_EQUIP_OFFSET)
}

/// `WorldChrMan::mainPlayerIns`.
const WORLD_CHR_MAN_MAIN_PLAYER_INS: usize = 0x1e508;

/// Whether the player exists **in the world**, not merely in save data.
///
/// This is deliberately stricter than "PlayerGameData is non-null". That pointer goes live
/// during the loading screen, long before a `PlayerIns` exists -- and the equip path's own
/// gate (`FUN_140788a90`) dereferences `WorldChrMan->mainPlayerIns` unconditionally. Running
/// the import on the weaker check crashed the game with a null-deref inside that gate
/// (access violation at game+0x788adb, 2026-08-22). Granting only needs `EquipGameData`, but
/// there is no reason to grant into a character that cannot yet wear anything, so the whole
/// import waits for this.
///
/// # Safety
///
/// Game thread only.
pub unsafe fn player_present() -> bool {
    use eldenring::cs::WorldChrMan;
    use fromsoftware_shared::FromStatic;

    // Safety: delegated; the helper is itself fault-checked.
    if unsafe { equip_game_data() }.is_none() {
        return false;
    }
    let Ok(wcm) = WorldChrMan::instance_ptr() else {
        return false;
    };
    let wcm = wcm as usize;
    if wcm == 0 {
        return false;
    }
    // Safety: reading one pointer at a verified offset inside a live singleton, and checking
    // it for null rather than assuming it is populated.
    let player = unsafe { *((wcm + WORLD_CHR_MAN_MAIN_PLAYER_INS) as *const usize) };
    player != 0
}

/// Give the player every item in `grants`, then read the inventory back to confirm.
///
/// # Safety
///
/// Must run on the game thread, with a character loaded (see [`player_present`]) and
/// `module_base` the loaded image base.
pub unsafe fn grant_all(module_base: usize, grants: &[Grant]) -> GrantOutcome {
    let mut outcome = GrantOutcome {
        attempted: grants.len(),
        ..GrantOutcome::default()
    };

    let Some(egd) = (unsafe { equip_game_data() }) else {
        outcome.missing = grants.iter().map(|grant| grant.item_id).collect();
        return outcome;
    };

    // Safety: the RVAs are verified 1.16.2 addresses inside the loaded image.
    let add: AddInventoryFn =
        unsafe { core::mem::transmute(module_base + ADD_INVENTORY_EQUIP_BY_ITEM_ID) };
    let get_inventory: GetInventoryFn =
        unsafe { core::mem::transmute(module_base + GET_EQUIP_INVENTORY_DATA) };
    let get_quantity: GetQuantityFn =
        unsafe { core::mem::transmute(module_base + GET_QUANTITY_BY_ITEM_ID) };

    for grant in grants {
        let id = grant.item_id as i32;
        // `updateTrophyStats` and `updateAutoEquip` both true: the same arguments the engine's
        // own pickup path uses, so achievements and auto-equip behave as if the item were
        // found in the world.
        // Safety: game thread, live EquipGameData, and `id` outlives the call.
        unsafe { add(egd, &raw const id, grant.quantity, true, true) };
    }

    // Safety: same context; the inventory pointer is engine-owned and only read.
    let inventory = unsafe { get_inventory(egd) };
    if inventory == 0 {
        outcome.missing = grants.iter().map(|grant| grant.item_id).collect();
        return outcome;
    }
    for grant in grants {
        let id = grant.item_id as i32;
        // Safety: as above.
        let held = unsafe { get_quantity(inventory, &raw const id) };
        if held > 0 {
            outcome.confirmed += 1;
        } else {
            outcome.missing.push(grant.item_id);
        }
    }

    outcome
}
