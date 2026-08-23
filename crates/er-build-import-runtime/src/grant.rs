//! Handing the build's items to the player, through the game's own inventory call.
//!
//! # Why not the community `ItemGib` table
//!
//! The Cheat Engine workflow this replaces writes a 16-byte record array and calls a routine
//! found by signature scan. The game already exports the operation:
//! `CS::EquipGameData::AddInventoryEquipByItemId` is what the engine itself uses, it resolves
//! the gaitem handle through `CSGaitemImp`, and it takes the same category-tagged item id the
//! catalog produces. Calling it means the engine owns the mutation, not us.
//!
//! # Why an armament does not go through that call
//!
//! Because two thirds of an armament are not in its item id, and that call has nowhere to put
//! them:
//!
//! * `AddInventoryEquipByItemId` -> `GetGaitemHandleByItemId` mints a BARE weapon gaitem. There
//!   is no gem in it, so the weapon carries whatever skill its `EquipParamWeapon` row names --
//!   for an infused row, usually none at all.
//! * `AddInventoryEquip` -> `EquipInventoryData::InsertItem(..., reinforcement = 0)` passes a
//!   hard-coded zero. Every weapon it inserts is +0, and the level cannot be smuggled into the
//!   id either: `EquipParamWeapon::GetEntry` looks up `(paramId / 100) * 100`.
//!
//! So an armament is minted the way the engine mints one that drops out of the world already
//! carrying an ash -- `EquipParamCustomWeapon` goes through
//! `CSGaitemImp::GetGaItemHandleWeaponWithGem(csGaitem, &handle, weaponId, gemId)` -- and its
//! reinforcement is written onto the instance afterwards through the same virtual the engine's
//! own `InsertItem` uses. That is why [`Grant::weapon_skill`] and [`Grant::reinforce_lv`] finally
//! have somewhere to go; before this they were computed, logged as resolved, and dropped.
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
//! holds rather than how many calls were made. Armaments get a second read-back, because
//! quantity cannot see an ash: the minted instance is asked for its own arts id and reinforcement
//! through `GetSwordArtsParamForWeapon` / `GaitemLookupResult::GetReinforcement`.

use er_build_import::plan::{Grant, NO_SKILL};

/// `CS::EquipGameData::AddInventoryEquipByItemId(egd, int *itemId, u32 amount,
/// bool updateTrophyStats, bool updateAutoEquip) -> int`.
const ADD_INVENTORY_EQUIP_BY_ITEM_ID: usize = 0x246840;
/// `CS::EquipGameData::AddInventoryEquip(egd, uint *gaItemHandle, u32 amount,
/// bool updateTrophyStats, bool updateAutoEquip) -> int` -- returns the inventory index.
///
/// The half of the call above that takes a handle somebody else minted, which is the only way to
/// hand the inventory an instance that already carries a gem.
const ADD_INVENTORY_EQUIP: usize = 0x246480;
/// `CS::CSGaitemImp::GetGaItemHandleWeaponWithGem(CSGaitemImp*, uint *out, int weaponId,
/// int gemId)` (Ghidra `FUN_140671ce0`).
///
/// Mints a weapon gaitem and, when `gemId >= 0`, a gem gaitem mounted in its slot 0 -- the exact
/// pair `EquipParamCustomWeapon` drops go through. A negative `gemId` skips the gem entirely, so
/// this one entry point covers armaments with and without an ash.
const GET_GAITEM_HANDLE_WEAPON_WITH_GEM: usize = 0x671ce0;
/// `GaItemHandle::~GaItemHandle(uint *handle)`.
///
/// Releases the reference the mint took. `CSGaitemImp` is a BOUNDED refcounted table (0x1400
/// entries) and every native caller of the mint destructs its local handle once the inventory has
/// taken its own reference; skipping it leaks table entries until the free queue is exhausted,
/// which this repository has already crashed on once.
const GAITEM_HANDLE_DTOR: usize = 0x682480;
/// `CS::GaitemLookupResult::SetReinforcement(GaitemLookupResult*, int)` -- `vtable + 0x20` on the
/// resolved instance, the same virtual `EquipInventoryData::InsertItem` calls.
const SET_REINFORCEMENT: usize = 0x672e00;
/// `CS::GaitemLookupResult::GetReinforcement(GaitemLookupResult*) -> int` -- `vtable + 0x18`.
const GET_REINFORCEMENT: usize = 0x672740;
// Declared once in `er-game-base::rva`; `er-better-refills-dll` and `equip_native` read the
// same values from there.
use er_game_base::rva::{
    GET_EQUIP_INVENTORY_DATA_RVA as GET_EQUIP_INVENTORY_DATA,
    GET_QUANTITY_BY_ITEM_ID_RVA as GET_QUANTITY_BY_ITEM_ID, GLOBAL_CSGAITEM_RVA,
};

use crate::gaitem::GaitemLookupResult;

/// `GameDataMan::main_player_game_data`, read as a raw pointer.
///
/// The typed upstream field is an `OwnedPtr`, which asserts non-null; before a character is
/// loaded this slot really is null, so it is read as a plain pointer and checked.
const GAME_DATA_MAN_PLAYER_OFFSET: usize = 0x08;
/// `PlayerGameData::equipGameData`, held by value.
const PLAYER_GAME_DATA_EQUIP_OFFSET: usize = 0x2b0;

/// The high nibble every item id carries its category in.
const ITEM_CATEGORY_MASK: u32 = 0xF000_0000;
/// The row id under that nibble.
const ITEM_ROW_MASK: u32 = 0x0FFF_FFFF;
/// The category nibble that means "armament" -- the only kind that carries a gem or a level.
const WEAPON_CATEGORY: u32 = 0x0000_0000;

type AddInventoryFn = unsafe extern "system" fn(usize, *const i32, u32, bool, bool) -> i32;
type AddInventoryByHandleFn = unsafe extern "system" fn(usize, *mut u32, u32, bool, bool) -> i32;
type MintWeaponFn = unsafe extern "system" fn(usize, *mut u32, i32, i32) -> *mut u32;
type HandleDtorFn = unsafe extern "system" fn(*mut u32);
type GetInventoryFn = unsafe extern "system" fn(usize) -> usize;
type GetQuantityFn = unsafe extern "system" fn(usize, *const i32) -> i32;
type SetReinforcementFn = unsafe extern "system" fn(*mut GaitemLookupResult, i32);
type GetReinforcementFn = unsafe extern "system" fn(*mut GaitemLookupResult) -> i32;

/// What one armament actually came out as, read off the instance the grant minted.
///
/// The whole point of this record is that it is measured, not assumed: `arts_id` is what
/// `GetSwordArtsParamForWeapon` says the new weapon holds, not what the plan asked for.
#[derive(Debug, Clone)]
pub struct ArmamentOutcome {
    /// The armament's display name.
    pub label: String,
    /// Category-tagged armament id, affinity included.
    pub item_id: u32,
    /// `EquipParamGem` row the plan asked to mount, or `None` for "no ash".
    pub wanted_gem: Option<u32>,
    /// `SwordArtsParam` row the minted instance reports, or `None` when it reports nothing.
    pub arts_id: Option<u32>,
    /// Upgrade level actually requested of the game: the build's level, clamped to one this
    /// armament has a `ReinforceParamWeapon` row for.
    pub wanted_level: u16,
    /// Upgrade level read back off the instance.
    pub level: i32,
    /// Inventory index the game filed it under; `-1` when the insert was refused.
    pub inventory_index: i32,
}

impl ArmamentOutcome {
    /// Whether the instance came out carrying an ash at all.
    pub fn has_ash(&self) -> bool {
        self.arts_id.is_some()
    }
}

/// What actually ended up in the inventory.
#[derive(Debug, Default)]
pub struct GrantOutcome {
    /// Grants the plan asked for.
    pub attempted: usize,
    /// Grants confirmed present afterwards by reading the inventory back.
    pub confirmed: usize,
    /// Item ids that were requested but could not be found afterwards.
    pub missing: Vec<u32>,
    /// Per-armament read-back, in grant order.
    pub armaments: Vec<ArmamentOutcome>,
}

impl GrantOutcome {
    /// Armaments that asked for an ash and came out without one.
    pub fn armaments_missing_their_ash(&self) -> impl Iterator<Item = &ArmamentOutcome> {
        self.armaments
            .iter()
            .filter(|arm| arm.wanted_gem.is_some() && !arm.has_ash())
    }
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

/// Whether a grant is an armament, i.e. the only kind that can carry a gem or a level.
fn is_armament(grant: &Grant) -> bool {
    grant.item_id & ITEM_CATEGORY_MASK == WEAPON_CATEGORY
}

/// The `EquipParamGem` row a grant's `weapon_skill` names, or `None` for "no ash".
///
/// The sentinel and the category nibble are both checked: a `weapon_skill` that is neither
/// [`NO_SKILL`] nor a gem-tagged id is a value this code does not understand, and mounting the
/// low bits of it anyway is how a wrong-table id gets silently mounted as a gem.
fn gem_row(grant: &Grant) -> Option<u32> {
    if grant.weapon_skill == NO_SKILL {
        return None;
    }
    if grant.weapon_skill & ITEM_CATEGORY_MASK != er_build_import::plan::GEM_ITEM_CATEGORY {
        return None;
    }
    Some(grant.weapon_skill & ITEM_ROW_MASK)
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

    // Read once: the clamp needs the whole `ReinforceParamWeapon` id set, and every armament in
    // the plan asks it the same question.
    let levels = crate::catalog::ReinforceLevels::read();

    for grant in grants {
        if is_armament(grant) {
            // Safety: same context; the armament path is documented on the helper.
            if let Some(armament) = unsafe { grant_armament(module_base, egd, grant, &levels) } {
                outcome.armaments.push(armament);
                continue;
            }
            // The mint declined (no `CSGaitemImp`, or the table is full). Fall through to the
            // plain call so the player at least gets a bare weapon rather than nothing.
        }
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

/// Grant ONE armament, with its ash mounted and its upgrade level set, and read both back.
///
/// Returns `None` when the game declined to mint a handle at all, which is the caller's cue to
/// fall back to the plain by-item-id call.
///
/// # Safety
///
/// Game thread, `egd` a live `EquipGameData*`, `module_base` the loaded image base.
unsafe fn grant_armament(
    module_base: usize,
    egd: usize,
    grant: &Grant,
    levels: &crate::catalog::ReinforceLevels,
) -> Option<ArmamentOutcome> {
    // Safety: a fault-checked read of one pointer-sized slot in the loaded image.
    let gaitem = unsafe { er_game_base::mem::safe_read_usize(module_base + GLOBAL_CSGAITEM_RVA) }?;
    if gaitem == 0 {
        return None;
    }

    // Safety: verified 1.16.2 RVAs inside the loaded image.
    let mint: MintWeaponFn =
        unsafe { core::mem::transmute(module_base + GET_GAITEM_HANDLE_WEAPON_WITH_GEM) };
    let add_by_handle: AddInventoryByHandleFn =
        unsafe { core::mem::transmute(module_base + ADD_INVENTORY_EQUIP) };
    let release: HandleDtorFn = unsafe { core::mem::transmute(module_base + GAITEM_HANDLE_DTOR) };

    let wanted_gem = gem_row(grant);
    // `-1` is the engine's own "no gem": the mint tests `-1 < gemId` before touching the slot.
    let gem_argument = wanted_gem.map_or(-1i32, |row| row as i32);

    // The mint writes a `GaItemHandle` here. Four words rather than the two the struct needs,
    // because the engine's own callers reserve sixteen bytes for it and over-reserving on our
    // own stack costs nothing.
    let mut handle = [0u32; 4];
    // Safety: our own buffer, the live singleton, and ids that are plain integers.
    unsafe {
        mint(
            gaitem,
            handle.as_mut_ptr(),
            (grant.item_id & ITEM_ROW_MASK) as i32,
            gem_argument,
        )
    };
    if handle[0] == 0 {
        return None;
    }

    // Safety: the inventory takes its own reference to the handle; ours is released below.
    let inventory_index =
        unsafe { add_by_handle(egd, handle.as_mut_ptr(), grant.quantity, true, true) };

    // The build's level is what the AUTHOR asked for, not necessarily a level this armament has.
    // Somber armaments stop at +10 and a build's `weaponUpgrade` is one number for the character,
    // so the game is asked which levels exist rather than trusted to survive an invented one.
    let wanted_level = levels.clamp(grant.item_id & ITEM_ROW_MASK, grant.reinforce_lv);
    // Safety: game thread; the helper only reads and writes this one instance.
    let level = unsafe { apply_reinforcement(module_base, &handle, wanted_level) };
    // Safety: same context; a pure read through the instance's own gem slot.
    let arts_id = unsafe { read_arts_id(module_base, &handle) };

    // Safety: releases exactly the reference the mint took, exactly as every native caller does.
    unsafe { release(handle.as_mut_ptr()) };

    Some(ArmamentOutcome {
        label: grant.label.clone(),
        item_id: grant.item_id,
        wanted_gem,
        arts_id,
        wanted_level,
        level,
        inventory_index,
    })
}

/// Set an armament instance's upgrade level and read it back. Returns the level the game reports.
///
/// # Safety
///
/// Game thread; `handle` must name a live weapon gaitem.
unsafe fn apply_reinforcement(module_base: usize, handle: &[u32; 4], level: u16) -> i32 {
    // Safety: delegated; the shared record is the length the engine writes.
    let Some(mut lookup) = (unsafe { GaitemLookupResult::from_handle(module_base, handle[0]) })
    else {
        return -1;
    };
    // Safety: verified RVAs; both are one-line forwarders to the instance's own virtual.
    let set: SetReinforcementFn = unsafe { core::mem::transmute(module_base + SET_REINFORCEMENT) };
    let get: GetReinforcementFn = unsafe { core::mem::transmute(module_base + GET_REINFORCEMENT) };
    // Safety: the lookup resolved a live instance, which is what both forwarders dereference.
    unsafe { set(&raw mut lookup, i32::from(level)) };
    // Safety: as above.
    unsafe { get(&raw mut lookup) }
}

/// The `SwordArtsParam` row an armament instance actually holds, through its own equipped gem.
///
/// # Safety
///
/// Game thread; `handle` must name a live weapon gaitem.
unsafe fn read_arts_id(module_base: usize, handle: &[u32; 4]) -> Option<u32> {
    // Safety: delegated to the shared record, which owns both hops.
    let mut lookup = unsafe { GaitemLookupResult::from_handle(module_base, handle[0]) }?;
    // Safety: as above.
    unsafe { lookup.sword_arts_id(module_base) }
}
