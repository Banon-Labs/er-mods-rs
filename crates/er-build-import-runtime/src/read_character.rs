//! Reading the LIVE character back out of the game -- the exact inverse of the importer.
//!
//! # Why this is the easy direction
//!
//! The importer's hard problem is name -> id: the planner payload identifies every item by display
//! name, the game answers only id -> name, so [`crate::catalog`] enumerates several thousand rows
//! and inverts the result. Nothing here needs that. Every read below produces an ID, and turning an
//! id into a name is one call to the game's own getter ([`crate::catalog::name_for`]).
//!
//! # What is read, and what is deliberately NOT
//!
//! The scope is the LOADOUT: what the character is wearing, holding, has memorised, and is levelled
//! to. Not the whole inventory. That is a product decision with two independent justifications --
//! "my build" means the loadout rather than the six hundred crafting materials in the pouch, and the
//! share link carries its payload in the URL, so every item is bytes a browser has to swallow.
//!
//! # Every value here is a READ-BACK, never an assumption
//!
//! The equipment slots are read through `CS::EquipGameData::GetParamIdInSlot`, which is the same
//! oracle the importer verifies its own writes with -- and for the same reason recorded in
//! `equip_native`: the engine's equip call returns void and declines silently, so only the slot's
//! contents are evidence. An id that resolves to no name is DROPPED rather than guessed at, which
//! makes a garbage read produce a smaller build rather than a wrong one.

use er_build_import::catalog::Kind;
use er_build_import::equip::{
    ARMAMENT_CHR_ASM_SLOTS, CHR_ASM_SLOT_ACCESSORY_1, CHR_ASM_SLOT_PROTECTOR_HEAD, PROTECTOR_PARTS,
};
use er_build_import::plan::split_armament_id;

use crate::catalog::name_for;
use crate::character::player_game_data;

// Every game address this module calls is declared ONCE in `er-game-base::rva` and derived here.
// They are all shared: the importer verifies its writes through the same equipment and spell
// getters, and `er-armament-icons` walks the same three-hop gem chain for the HUD badge. See that
// module for what each one is and for the two out-parameter signatures that are easy to get wrong.
use er_game_base::rva::{
    GET_EQUIP_MAGIC_ID_RVA as GET_EQUIP_MAGIC_ID,
    GET_EQUIPPED_GREATRUNE_RVA as GET_EQUIPPED_GREATRUNE,
    GET_MAGIC_SLOTS_COUNT_RVA as GET_MAGIC_SLOTS_COUNT,
    GET_PARAM_ID_IN_SLOT_RVA as GET_PARAM_ID_IN_SLOT,
    GET_PHYSIC_TEAR_BY_SLOT_RVA as GET_PHYSIC_TEAR_BY_SLOT, WORLD_CHR_MAN_GLOBAL_RVA,
    WORLD_CHR_MAN_PLAYER_INS_OFFSET,
};

use crate::gaitem::{GaitemLookupResult, worn_weapon_handle};

/// `CS::EquipMagicData` pointer inside `EquipGameData`. Declared beside its only two readers
/// rather than centrally: it is a field offset of one struct, not a cross-cutting singleton.
const EQUIP_GAME_DATA_MAGIC_OFFSET: usize = 0x280;

/// `PlayerGameData` field offsets. Bound to the upstream typed layout so a struct change fails the
/// build rather than reading the wrong bytes at runtime.
mod pgd {
    use eldenring::cs::PlayerGameData;

    pub const LEVEL: usize = core::mem::offset_of!(PlayerGameData, level);
    pub const VIGOR: usize = core::mem::offset_of!(PlayerGameData, vigor);
    pub const MIND: usize = core::mem::offset_of!(PlayerGameData, mind);
    pub const ENDURANCE: usize = core::mem::offset_of!(PlayerGameData, endurance);
    pub const STRENGTH: usize = core::mem::offset_of!(PlayerGameData, strength);
    pub const DEXTERITY: usize = core::mem::offset_of!(PlayerGameData, dexterity);
    pub const INTELLIGENCE: usize = core::mem::offset_of!(PlayerGameData, intelligence);
    pub const FAITH: usize = core::mem::offset_of!(PlayerGameData, faith);
    pub const ARCANE: usize = core::mem::offset_of!(PlayerGameData, arcane);
    pub const ARCHETYPE: usize = core::mem::offset_of!(PlayerGameData, archetype);
    /// The character's HIGHEST weapon upgrade level, maintained by the game for matchmaking. Raw
    /// `+0..=+25`, not a bucket -- `CS::ChrIns::CheckWeaponLevelMismatch` guards it with `< 0x1a`.
    pub const MATCHING_WEAPON_LEVEL: usize =
        core::mem::offset_of!(PlayerGameData, matching_weapon_level);
    pub const MAX_HP_FLASK: usize = core::mem::offset_of!(PlayerGameData, max_hp_flask);
    pub const MAX_FP_FLASK: usize = core::mem::offset_of!(PlayerGameData, max_fp_flask);

    // The character NAME is a fixed UTF-16 array with no length field, so its bounds are the two
    // fields on either side of it. Derived here rather than taken from `er_game_base::pgd` because
    // this crate depends on `er-game-base` WITHOUT the `game-types` feature that compiles that
    // module -- and enabling a feature to reach two constants would change the feature graph of
    // every DLL this crate is linked into.
    pub const NAME: usize = core::mem::offset_of!(PlayerGameData, chr_type)
        + core::mem::size_of::<eldenring::cs::ChrType>();
    pub const NAME_LEN_U16: usize =
        (core::mem::offset_of!(PlayerGameData, gender) - NAME) / core::mem::size_of::<u16>();

    // The same two assertions `er_game_base::pgd` makes, so the two derivations cannot drift.
    const _: () = assert!(NAME == 0x9c);
    const _: () = assert!(NAME_LEN_U16 == 17);
}

/// Highest upgrade level `matching_weapon_level` can legitimately hold. A byte above this means we
/// are not looking at live save data, so the field is reported as unknown rather than exported.
const MATCHING_WEAPON_LEVEL_MAX: u8 = 25;

/// Starting classes in `CharaInitParam` order, i.e. indexed by `PlayerGameData::archetype`. The
/// same list the importer maps the other way; kept in one place there and read from here.
const CLASSES: &[&str] = &[
    "Vagabond",
    "Warrior",
    "Hero",
    "Bandit",
    "Astrologer",
    "Prophet",
    "Samurai",
    "Prisoner",
    "Confessor",
    "Wretch",
];

/// One equipped item, as the planner names it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReadSlot {
    /// Display name, straight from the game's own message repository.
    pub name: String,
    /// Affinity, for armaments only. `None` means Standard or not an armament.
    pub infusion: Option<String>,
    /// Ash of war on this armament, if the equipped gem resolved to one.
    pub weapon_art: Option<String>,
    /// Position within its category, which is also the planner's `equipIndex`.
    pub equip_index: u32,
}

/// The character, as the planner would describe it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CharacterRead {
    /// Character name, from `PlayerGameData`.
    pub name: String,
    /// Starting class, or `None` when the archetype byte is not one of the ten.
    pub character_class: Option<String>,
    /// `rl` plus the eight attributes, in the planner's own key spelling (note `vit` is ENDURANCE).
    pub stats: Vec<(&'static str, i64)>,
    /// Highest weapon upgrade level on the character, which is what the planner's `weaponUpgrade`
    /// means. `None` when the field failed its sanity bound.
    pub weapon_upgrade: Option<u16>,
    /// Equipped armaments, in planner index order.
    pub armaments: Vec<ReadSlot>,
    /// Equipped armour, keyed by [`PROTECTOR_PARTS`].
    pub protectors: Vec<(&'static str, ReadSlot)>,
    /// Equipped talismans.
    pub talismans: Vec<ReadSlot>,
    /// Memorised spells, in slot order.
    pub spells: Vec<ReadSlot>,
    /// Physick tears, in flask order; `None` for an empty half.
    pub crystal_tears: Vec<Option<String>>,
    /// Equipped great rune.
    pub great_rune: Option<String>,
    /// Whether the character is two-handing.
    pub two_handing: bool,
    /// Crimson and cerulean flask counts.
    pub flask_crimson: u32,
    pub flask_cerulean: u32,
    /// Memory slots the game says the character has -- reported so a truncated spell list is
    /// visibly a capacity limit rather than a failed read.
    pub magic_capacity: u32,
    /// Slots that held an item whose id resolved to NO name. Never silently dropped: a build that
    /// came out short says so.
    pub unnamed_slots: usize,
}

type ParamInSlotFn = unsafe extern "system" fn(usize, i32) -> i32;
type OutParamGetterFn = unsafe extern "system" fn(usize, *mut i32, i32) -> *mut i32;
type MagicIdFn = unsafe extern "system" fn(usize, i32) -> i32;
type SlotsCountFn = unsafe extern "system" fn(usize, usize) -> u32;

/// Read one equipment slot and name it, or `None` when the slot is empty or unnameable.
///
/// # Safety
///
/// Game thread, `egd` live, `msg` a live `MsgRepositoryImp*`.
unsafe fn read_slot(
    module_base: usize,
    msg: usize,
    egd: usize,
    slot: i32,
    kind: Kind,
    equip_index: u32,
) -> Option<ReadSlot> {
    // Safety: verified RVA; a pure read of the slot's current param id.
    let get: ParamInSlotFn = unsafe { core::mem::transmute(module_base + GET_PARAM_ID_IN_SLOT) };
    // Safety: the caller's contract.
    let raw = unsafe { get(egd, slot) };
    // An empty slot reads as -1, and the engine also uses large sentinels for "nothing"; anything
    // that is not a plausible row id is simply not an item.
    let param_id = u32::try_from(raw).ok()?;
    if param_id == 0 || param_id == u32::MAX {
        return None;
    }
    let (row_id, infusion) = if kind == Kind::Weapon {
        split_armament_id(param_id)
    } else {
        (param_id, None)
    };
    // Safety: the caller's contract carries through.
    let name = unsafe { name_for(kind, msg, module_base, row_id) }?;
    let weapon_art = if kind == Kind::Weapon {
        // Safety: same context; the whole chain is null-checked inside.
        unsafe { read_weapon_art(module_base, msg, slot) }
    } else {
        None
    };
    Some(ReadSlot {
        name,
        infusion: infusion.map(str::to_owned),
        weapon_art,
        equip_index,
    })
}

/// The ash of war on the armament in `slot`, named -- see [`worn_armament`] for how it is read.
///
/// # Safety
///
/// Game thread; `module_base` the loaded image base and `msg` a live `MsgRepositoryImp*`.
unsafe fn read_weapon_art(module_base: usize, msg: usize, slot: i32) -> Option<String> {
    // Safety: the caller's contract.
    let arts_id = unsafe { equipped_weapon_arts_id(module_base, slot) }?;
    // Safety: as above.
    unsafe { name_for(Kind::AshOfWar, msg, module_base, arts_id) }
}

/// What the armament worn in `slot` ACTUALLY is: the instance's own item id, and the
/// `SwordArtsParam` row it carries.
///
/// Both halves matter, and reporting only the second is what made the last failure unreadable.
/// A slot that holds the right ITEM but the wrong ash and a slot that holds a different armament
/// entirely produce the same "wrong arts row" -- and several copies of one armament, differing
/// only by the ash mounted on them, share one item id, so even the id alone cannot pick a copy.
/// With both in hand the log line adjudicates itself instead of listing what it might have been.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WornArmament {
    /// Category-tagged item id of the instance in the slot.
    pub item_id: u32,
    /// The `SwordArtsParam` row it carries, or `None` when it reports none.
    pub arts_id: Option<u32>,
}

/// Read the armament worn in `slot`, or `None` when the slot is empty.
///
/// Three native hops, none of them skippable: the slot names a gaitem handle, the handle names a
/// gaitem instance, and only the instance knows which gem is in the weapon. Deriving the arts id
/// from the weapon id instead (the menu path's `arts_id * 100` heuristic) misses every weapon whose
/// gem id is not derived that way.
///
/// `slot` is a `ChrAsmSlot` in exactly the numbering [`ARMAMENT_CHR_ASM_SLOTS`] and
/// `GetParamIdInSlot` use -- the handle getter bottoms out in a single
/// `chrAsm->equipmentGaItemHandles[slot]`, so the two read-backs are asking about the same slot.
///
/// # Safety
///
/// Game thread; `module_base` the loaded image base.
pub unsafe fn worn_armament(module_base: usize, slot: i32) -> Option<WornArmament> {
    let player = {
        // Safety: a fault-checked read of the singleton slot and one offset inside it.
        let world =
            unsafe { er_game_base::mem::safe_read_usize(module_base + WORLD_CHR_MAN_GLOBAL_RVA) }?;
        if world == 0 {
            return None;
        }
        // Safety: as above.
        let player =
            unsafe { er_game_base::mem::safe_read_usize(world + WORLD_CHR_MAN_PLAYER_INS_OFFSET) }?;
        if player == 0 {
            return None;
        }
        player
    };
    // Safety: the caller's contract; the slot came from the caller's fixed table.
    let handle = unsafe { worn_weapon_handle(module_base, player, slot) }?;
    // Safety: as above. The record is the engine's own, at the engine's own length -- see
    // `crate::gaitem` for what a short one costs.
    let mut lookup = unsafe { GaitemLookupResult::from_handle(module_base, handle) }?;
    // Safety: as above.
    let arts_id = unsafe { lookup.sword_arts_id(module_base) };
    Some(WornArmament {
        item_id: lookup.item_id,
        arts_id,
    })
}

/// The `SwordArtsParam` row the armament in `slot` is ACTUALLY holding, by way of its equipped gem.
///
/// # Safety
///
/// Game thread; `module_base` the loaded image base.
pub unsafe fn equipped_weapon_arts_id(module_base: usize, slot: i32) -> Option<u32> {
    // Safety: the caller's contract.
    unsafe { worn_armament(module_base, slot) }?.arts_id
}

/// Read the whole character.
///
/// Returns `None` only when the character is not in the world at all -- every finer-grained failure
/// leaves a field empty and is counted in [`CharacterRead::unnamed_slots`], because a build that is
/// missing one talisman is still worth sharing and a build that is silently missing one is not.
///
/// # Safety
///
/// Game task thread, params streamed, character in the world -- the same three preconditions the
/// importer's `tick` checks before it runs.
pub unsafe fn read_character(module_base: usize, msg: usize, egd: usize) -> Option<CharacterRead> {
    // Safety: the caller's contract.
    let pgd = unsafe { player_game_data() }?;
    let mut out = CharacterRead::default();

    // Safety: plain reads of live save data at offsets bound to the upstream typed layout.
    unsafe {
        let read_i32 = |offset: usize| *((pgd + offset) as *const i32) as i64;
        out.stats = vec![
            ("rl", read_i32(pgd::LEVEL)),
            ("vig", read_i32(pgd::VIGOR)),
            ("mnd", read_i32(pgd::MIND)),
            // The planner calls Endurance "vit". Verified, not inferred.
            ("vit", read_i32(pgd::ENDURANCE)),
            ("str", read_i32(pgd::STRENGTH)),
            ("dex", read_i32(pgd::DEXTERITY)),
            ("int", read_i32(pgd::INTELLIGENCE)),
            ("fth", read_i32(pgd::FAITH)),
            ("arc", read_i32(pgd::ARCANE)),
        ];
        let archetype = *((pgd + pgd::ARCHETYPE) as *const u8) as usize;
        out.character_class = CLASSES.get(archetype).map(|name| (*name).to_owned());
        let upgrade = *((pgd + pgd::MATCHING_WEAPON_LEVEL) as *const u8);
        out.weapon_upgrade = (upgrade <= MATCHING_WEAPON_LEVEL_MAX).then_some(u16::from(upgrade));
        out.flask_crimson = u32::from(*((pgd + pgd::MAX_HP_FLASK) as *const u8));
        out.flask_cerulean = u32::from(*((pgd + pgd::MAX_FP_FLASK) as *const u8));
    }
    // Safety: the name is read through the fault-checked reader, so an unmapped page yields a
    // short name rather than a fault.
    out.name = unsafe { read_character_name(pgd) };

    let mut unnamed = 0usize;
    let mut count_read = |slot: Option<ReadSlot>, occupied: bool| {
        if occupied && slot.is_none() {
            unnamed += 1;
        }
        slot
    };

    // Armaments, in the planner's index order rather than the engine's interleaved one.
    for (equip_index, slot) in ARMAMENT_CHR_ASM_SLOTS.into_iter().enumerate() {
        let equip_index = equip_index as u32;
        // Safety: the caller's contract.
        let read = unsafe { read_slot(module_base, msg, egd, slot, Kind::Weapon, equip_index) };
        if let Some(item) = count_read(read, false) {
            out.armaments.push(item);
        }
    }
    // Armour: head, chest, hands, legs, consecutive from ProtectorHead.
    for (offset, part) in PROTECTOR_PARTS.into_iter().enumerate() {
        let slot = CHR_ASM_SLOT_PROTECTOR_HEAD + offset as i32;
        // Safety: as above.
        let read = unsafe { read_slot(module_base, msg, egd, slot, Kind::Protector, 0) };
        if let Some(item) = count_read(read, false) {
            out.protectors.push((part, item));
        }
    }
    // Talismans.
    for index in 0..4i32 {
        let slot = CHR_ASM_SLOT_ACCESSORY_1 + index;
        // Safety: as above.
        let read = unsafe { read_slot(module_base, msg, egd, slot, Kind::Talisman, index as u32) };
        if let Some(item) = count_read(read, false) {
            out.talismans.push(item);
        }
    }

    // Spells. The capacity is asked for rather than assumed: it accounts for Memory Stones and
    // talismans, and the engine clamps it to 14.
    // Safety: one pointer read at the verified EquipGameData offset.
    let emd = unsafe { *((egd + EQUIP_GAME_DATA_MAGIC_OFFSET) as *const usize) };
    if emd != 0 {
        // Safety: verified RVAs.
        let slots_count: SlotsCountFn =
            unsafe { core::mem::transmute(module_base + GET_MAGIC_SLOTS_COUNT) };
        let magic_id: MagicIdFn = unsafe { core::mem::transmute(module_base + GET_EQUIP_MAGIC_ID) };
        // Safety: a null SpecialEffect means "derive it from the player".
        out.magic_capacity = unsafe { slots_count(emd, 0) };
        for slot in 0..out.magic_capacity.min(i32::MAX as u32) as i32 {
            // Safety: slot is within the capacity the engine just reported.
            let raw = unsafe { magic_id(emd, slot) };
            let Ok(row_id) = u32::try_from(raw) else {
                continue;
            };
            if row_id == 0 || row_id == u32::MAX {
                continue;
            }
            // Safety: the caller's contract.
            match unsafe { name_for(Kind::Spell, msg, module_base, row_id) } {
                Some(name) => out.spells.push(ReadSlot {
                    name,
                    infusion: None,
                    weapon_art: None,
                    equip_index: slot as u32,
                }),
                None => unnamed += 1,
            }
        }
    }

    // Physick. The field holds CATEGORY-TAGGED ids (a game-filled flask reads back as e.g.
    // `0x40001FC1`), so the nibble is masked off before the row is named.
    // Safety: verified RVA; an out-parameter getter, which is why the destination is ours.
    let get_tear: OutParamGetterFn =
        unsafe { core::mem::transmute(module_base + GET_PHYSIC_TEAR_BY_SLOT) };
    for index in 0..2i32 {
        let mut raw = -1i32;
        // Safety: our own slot, and the getter writes exactly one int through it.
        unsafe { get_tear(egd, &raw mut raw, index) };
        let tear = u32::try_from(raw)
            .ok()
            .filter(|id| *id != 0 && *id != u32::MAX)
            .and_then(|id| unsafe {
                name_for(Kind::Tool, msg, module_base, id & ITEM_ID_ROW_MASK)
            });
        out.crystal_tears.push(tear);
    }

    // Great rune. THREE arguments: the outer wrapper never writes R8, so the slot passes straight
    // through to `GetEquippedGreatrune(EquipItemData*, int *out, int slot)`, whose body begins
    // `*out = -1; if (slot == 0 && ...)`. Calling it with two leaves R8 holding whatever the call
    // site had and it reports -1 no matter what is equipped.
    // Safety: verified RVA; the out slot is ours and outlives the call.
    let get_rune: OutParamGetterFn =
        unsafe { core::mem::transmute(module_base + GET_EQUIPPED_GREATRUNE) };
    let mut rune = -1i32;
    // Safety: as above.
    unsafe { get_rune(egd, &raw mut rune, 0) };
    out.great_rune = u32::try_from(rune)
        .ok()
        .filter(|id| *id != 0 && *id != u32::MAX)
        .and_then(|id| unsafe {
            name_for(Kind::GreatRune, msg, module_base, id & ITEM_ID_ROW_MASK)
        });

    // Two-handing, from the same `ChrAsm::equipment.arm_style` the engine renders from.
    // Safety: a read of one enum-sized field at an offset bound to the upstream layout.
    out.two_handing = unsafe { read_arm_style(egd) }.is_some_and(|style| {
        style == ARM_STYLE_LEFT_BOTH_HANDS || style == ARM_STYLE_RIGHT_BOTH_HANDS
    });

    out.unnamed_slots = unnamed;
    Some(out)
}

/// Read the character's name out of `PlayerGameData`.
///
/// Stops at the first NUL and at the field's own bound, so neither a name that fills the array nor
/// one whose terminator was never written can run past it.
///
/// # Safety
///
/// `pgd` must be a live `PlayerGameData*`.
unsafe fn read_character_name(pgd: usize) -> String {
    let mut units = Vec::with_capacity(pgd::NAME_LEN_U16);
    for index in 0..pgd::NAME_LEN_U16 {
        // Safety: fault-checked; an unreadable address ends the name instead of faulting.
        let Some(unit) = (unsafe { er_game_base::mem::safe_read_u16(pgd + pgd::NAME + index * 2) })
        else {
            break;
        };
        if unit == 0 {
            break;
        }
        units.push(unit);
    }
    String::from_utf16(&units).unwrap_or_default()
}

/// Low 28 bits of a category-tagged item id, i.e. the param row.
const ITEM_ID_ROW_MASK: u32 = 0x0FFF_FFFF;

/// `ChrAsmArmStyle::LeftBothHands` / `RightBothHands` -- the two values that mean two-handing.
const ARM_STYLE_LEFT_BOTH_HANDS: u32 = 2;
const ARM_STYLE_RIGHT_BOTH_HANDS: u32 = 3;

/// `EquipGameData::chr_asm.equipment.arm_style`, bound to the upstream typed layout.
const EQUIP_GAME_DATA_ARM_STYLE_OFFSET: usize = {
    use eldenring::cs::{ChrAsm, EquipGameData};
    core::mem::offset_of!(EquipGameData, chr_asm) + core::mem::offset_of!(ChrAsm, equipment)
};

/// Read `ChrAsm::equipment.arm_style`.
///
/// # Safety
///
/// Game thread, `egd` live.
unsafe fn read_arm_style(egd: usize) -> Option<u32> {
    // Safety: a fault-checked read of one field at an offset the compiler derived from the
    // upstream struct, so a layout change breaks the build rather than the read. Read as `i32`
    // because that is the width the fault-checked reader offers; the enum has four values.
    unsafe { er_game_base::mem::safe_read_i32(egd + EQUIP_GAME_DATA_ARM_STYLE_OFFSET) }
        .map(|value| value as u32)
}
