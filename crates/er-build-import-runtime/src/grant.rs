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
//!   hard-coded zero, and `InsertItem` writes it straight onto the instance through
//!   `GaitemLookupResult::SetReinforcement`. Every weapon it inserts is +0 until something puts
//!   the level back.
//!
//! The level is put back in BOTH places the engine keeps it: folded into the minted item id
//! (`er_build_import_core::plan::armament_item_id`, the half the player actually sees) and written to
//! the instance field afterwards, since `InsertItem` has just zeroed that one.
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

use er_build_import_core::plan::{Grant, NO_SKILL, armament_item_id};

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
// Declared once in `er-game-base::rva`; `er-better-refills` and `equip_native` read the
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
    /// The `GaItemHandle` this armament was minted as, or `0` when nothing usable was minted.
    ///
    /// THE ONLY NAME THAT DISTINGUISHES THIS COPY FROM ITS TWINS. An ash lives on the gaitem
    /// instance, not in the item id, so several copies of one armament carrying different ashes
    /// all share an item id -- and `EquipInventoryData::GetItemInventoryIdx` (0x14024c560) keys
    /// purely on that id: its body is `if (*itemId != -1) GetItemIndex(itemsData, itemId)`, and
    /// `InventoryItemsData::InsertItemIntoLookupMap` keeps the LOWEST index for a repeated id.
    /// One id, one answer, forever the same copy. Carrying the handle forward is what lets the
    /// equip ask `GetItemIndexByGaitemHandle` (0x14024c460) instead, which scans the entries for
    /// the one whose handle matches and therefore CAN separate them.
    ///
    /// # Why the number outlives our own reference
    ///
    /// [`grant_armament`] destructs its local handle before returning, as every native caller
    /// does. That is a refcount decrement, not an invalidation: `AddInventoryEquip` took the
    /// inventory's own reference first, so the entry stays live for exactly as long as the item
    /// stays in the inventory -- which is longer than the equip pass. When the insert is REFUSED
    /// the inventory took no reference and our release frees the table entry, so this is left
    /// zero rather than left dangling; `RemoveCSGaitemIns` bumps the handle's generation bits on
    /// free, so even a stale handle fails the lookup closed rather than naming a later item.
    pub handle: u32,
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
    /// Grants skipped because the character already had at least the quantity asked for.
    ///
    /// Counted rather than inferred: this is the difference between "the build was already
    /// satisfied here" and "the grant silently did nothing", which the report must not blur.
    pub already_held: usize,
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
    gem_row_of(grant.weapon_skill)
}

/// The `EquipParamGem` row a `weapon_skill` field names, or `None` for "no ash".
///
/// Public because the equip side asks the same question of the same encoding when it decides
/// WHICH minted copy belongs in a slot -- and two copies of that rule would be two chances for
/// the grant and the equip to disagree about what an armament is, which is precisely the
/// ambiguity the handle threading exists to remove.
pub fn gem_row_of(weapon_skill: u32) -> Option<u32> {
    if weapon_skill == NO_SKILL {
        return None;
    }
    if weapon_skill & ITEM_CATEGORY_MASK != er_build_import_core::plan::GEM_ITEM_CATEGORY {
        return None;
    }
    Some(weapon_skill & ITEM_ROW_MASK)
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

    // THE ID EACH GRANT ENDS UP UNDER, which for an armament is not `grant.item_id`: the upgrade
    // level lives in the id's last two digits, so a +25 weapon is a DIFFERENT id from the +0 one
    // the plan names. The read-back below has to ask about the id the inventory actually holds,
    // or every armament reports missing.
    let mut confirm_ids: Vec<u32> = Vec::with_capacity(grants.len());

    // Read the inventory BEFORE granting, so a grant can ask what is already held.
    //
    // A build says "this character HAS these items", not "add these items". Those read the same
    // on an empty character and differently on every other one: importing twice, or importing
    // onto a character that already owns something, used to add a second copy every time. For a
    // stackable that merely inflates a count; for the Flask of Wondrous Physick -- unique, one
    // per character -- the player ends up carrying two flasks, which is what surfaced this.
    //
    // A zero here is not fatal: it means the quantity cannot be measured, and granting
    // unconditionally is the old behaviour, which is wrong less often than granting nothing.
    // Safety: game thread, live EquipGameData.
    let inventory_before = unsafe { get_inventory(egd) };

    for grant in grants {
        if is_armament(grant) {
            // Safety: same context; the armament path is documented on the helper.
            if let Some(armament) = unsafe { grant_armament(module_base, egd, grant, &levels) } {
                confirm_ids.push(armament.item_id);
                outcome.armaments.push(armament);
                continue;
            }
            // The mint declined (no `CSGaitemImp`, or the table is full). Fall through to the
            // plain call so the player at least gets a bare weapon rather than nothing -- still
            // at the right level, which is the one part of an armament that id alone can carry.
        }
        let full_id = if is_armament(grant) {
            armament_item_id(
                grant.item_id,
                levels.clamp(grant.item_id & ITEM_ROW_MASK, grant.reinforce_lv),
            )
        } else {
            grant.item_id
        };
        confirm_ids.push(full_id);
        let id = full_id as i32;
        // RECONCILE TO THE TARGET rather than adding to whatever is there. The build names a
        // quantity the character should end up with, so only the shortfall is granted, and an
        // item already held in sufficient number is left completely alone.
        // COUNT EVERY ID THE NAME RESOLVES TO, not just the one the catalog happened to pick.
        //
        // Elden Ring gives each upgrade level of a flask its own goods row, and every row carries
        // the same name -- so "Flask of Wondrous Physick" is several ids. Asking about one of them
        // and getting zero does NOT mean the player has no physick; it means they have a
        // different row of it. That is what handed out a second flask beside the one already in
        // the inventory, and the same collision made the Crimson and Cerulean flasks report "not
        // in the inventory" while sitting in the player's belt.
        let shortfall: u32 = if inventory_before == 0 {
            grant.quantity
        } else {
            let mut held: i32 = 0;
            for candidate in std::iter::once(full_id).chain(grant.also_known_as.iter().copied()) {
                let candidate = candidate as i32;
                // Safety: engine-owned inventory pointer, read only; the id outlives the call.
                // A negative answer means "cannot say", not "owns a negative number".
                held = held.saturating_add(
                    unsafe { get_quantity(inventory_before, &raw const candidate) }.max(0),
                );
            }
            grant.quantity.saturating_sub(held.unsigned_abs())
        };
        if shortfall == 0 {
            // Say so. A skipped grant and a grant that never ran look identical in a log that
            // only records what it added, and "why do I have two flasks" is exactly the question
            // that needs this line to answer it.
            outcome.already_held += 1;
            crate::log_line(&format!(
                "[build-import]   ALREADY HELD, not granted: item 0x{full_id:08X}{} \
                 (build wants {}, character has at least that many)",
                if grant.also_known_as.is_empty() {
                    String::new()
                } else {
                    format!(
                        " (+{} id(s) under the same name)",
                        grant.also_known_as.len()
                    )
                },
                grant.quantity
            ));
            continue;
        }
        // `updateTrophyStats` and `updateAutoEquip` both true: the same arguments the engine's
        // own pickup path uses, so achievements and auto-equip behave as if the item were
        // found in the world.
        // Safety: game thread, live EquipGameData, and `id` outlives the call.
        unsafe { add(egd, &raw const id, shortfall, true, true) };
    }

    // Safety: same context; the inventory pointer is engine-owned and only read.
    let inventory = unsafe { get_inventory(egd) };
    if inventory == 0 {
        outcome.missing = grants.iter().map(|grant| grant.item_id).collect();
        return outcome;
    }
    for full_id in &confirm_ids {
        let id = *full_id as i32;
        // Safety: as above.
        let held = unsafe { get_quantity(inventory, &raw const id) };
        if held > 0 {
            outcome.confirmed += 1;
        } else {
            outcome.missing.push(*full_id);
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

    // THE LEVEL IS PART OF THE ITEM ID, and it has to be decided before the mint rather than
    // after it. The build's level is what the AUTHOR asked for, not necessarily a level this
    // armament has -- somber armaments stop at +10 and a build's `weaponUpgrade` is one number
    // for the whole character -- so the game is asked which `reinforceTypeId + level` rows exist
    // and the request is clamped to one of them.
    let wanted_level = levels.clamp(grant.item_id & ITEM_ROW_MASK, grant.reinforce_lv);
    let minted_id = armament_item_id(grant.item_id, wanted_level);

    // Safety: our own buffer, the live singleton, and ids that are plain integers.
    unsafe { mint(gaitem, handle.as_mut_ptr(), minted_id as i32, gem_argument) };
    if handle[0] == 0 {
        return None;
    }

    // Safety: the inventory takes its own reference to the handle; ours is released below.
    let inventory_index =
        unsafe { add_by_handle(egd, handle.as_mut_ptr(), grant.quantity, true, true) };

    // Set the instance field too. `AddInventoryEquip` -> `InsertItem` has just written a
    // hard-coded 0 into it, and the reference exporter sets BOTH halves; this is the half that
    // survives that zeroing.
    // Safety: game thread; the helper only reads and writes this one instance.
    let level = unsafe { apply_reinforcement(module_base, &handle, wanted_level) };
    // Safety: same context; a pure read through the instance's own gem slot.
    let arts_id = unsafe { read_arts_id(module_base, &handle) };

    // The handle is kept ONLY when the inventory actually took its own reference. A negative
    // return means `EquipInventoryData::InsertItem` refused (`AddInventoryEquip` sets
    // `lastItemAddResult = 4` and returns -1), so the release below is the last reference and
    // the table entry goes back on the free queue -- a number that no longer names this item.
    let owned_handle = if inventory_index < 0 { 0 } else { handle[0] };

    // Safety: releases exactly the reference the mint took, exactly as every native caller does.
    unsafe { release(handle.as_mut_ptr()) };

    Some(ArmamentOutcome {
        handle: owned_handle,
        label: grant.label.clone(),
        item_id: minted_id,
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
