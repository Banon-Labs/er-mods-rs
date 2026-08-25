//! Level, attributes, starting class and memorised spells.
//!
//! Everything here is read back after it is written. A previous version of the equip path
//! reported "10/10" from a call counter while the character wore nothing, so no number in
//! this module is derived from a call having been made.

use er_build_import_core::BuildDoc;
use er_build_import_core::equip::EquipRef;
// Declared once in `er-game-base::rva`; the exporter reads the same two spell getters.
use er_game_base::rva::{
    GET_EQUIP_MAGIC_ID_RVA as GET_EQUIP_MAGIC_ID,
    GET_MAGIC_SLOTS_COUNT_RVA as GET_MAGIC_SLOTS_COUNT,
};

/// `GetMainPlayerStats(int out[10])`.
const GET_MAIN_PLAYER_STATS: usize = 0x788360;
/// `ApplyMainPlayerStats(const int in[10])` -- writes the fields **and** recomputes every
/// derived value: base/current max HP, FP and stamina, attack rating, max equip load,
/// resistance gauges and the spell-slot count. Writing the fields directly would leave all
/// of those stale, which is the whole reason this function is used.
const APPLY_MAIN_PLAYER_STATS: usize = 0x788cf0;

/// `CS::EquipMagicData::GetMagicSlotsCount(emd, SpecialEffect*)` -- pass null and it derives
/// the effect itself. Clamps to 14. THE source of capacity; never hardcode a number.
/// `EquipMagicInSlot(emd, ChrAsmSlot slot, uint magicParamId) -> bool`.
const EQUIP_MAGIC_IN_SLOT: usize = 0x250490;
/// `GameDataMan + 0x08 -> PlayerGameData*`.
const GAME_DATA_MAN_PLAYER_OFFSET: usize = 0x08;
/// `EquipGameData::equipMagicData`, a pointer.
const EQUIP_GAME_DATA_MAGIC_OFFSET: usize = 0x280;
/// `PlayerGameData::level`.
const PGD_LEVEL: usize = 0x68;
/// `PlayerGameData::archetype`, one byte: the starting class.
const PGD_ARCHETYPE: usize = 0xbf;
/// `PlayerGameData` attribute offsets, in **struct** order.
const PGD_VIGOR: usize = 0x3c;
const PGD_MIND: usize = 0x40;
const PGD_ENDURANCE: usize = 0x44;
const PGD_STRENGTH: usize = 0x48;
const PGD_DEXTERITY: usize = 0x4c;
const PGD_INTELLIGENCE: usize = 0x50;
const PGD_FAITH: usize = 0x54;
const PGD_ARCANE: usize = 0x58;

/// The ten-int array `GetMainPlayerStats` fills and `ApplyMainPlayerStats` consumes.
///
/// **Endurance and mind are swapped relative to the on-screen order.** Index 2 is endurance
/// (`PGD+0x44`) and index 3 is mind (`PGD+0x40`), verified in both functions' disassembly.
/// Getting this backwards imports a different, plausible-looking build.
const STAT_LEVEL: usize = 0;
const STAT_VIGOR: usize = 1;
const STAT_ENDURANCE: usize = 2;
const STAT_MIND: usize = 3;
const STAT_STRENGTH: usize = 4;
const STAT_DEXTERITY: usize = 5;
/// Index 6 is `baseDurability`. It round-trips verbatim and its meaning is not established,
/// so it is read and written back untouched rather than set to anything.
const STAT_INTELLIGENCE: usize = 7;
const STAT_FAITH: usize = 8;
const STAT_ARCANE: usize = 9;

/// Starting classes in `CharaInitParam` order: row `3000 + index`.
///
/// Taken from the planner's own class list, which is the list that produced the payload's
/// `characterClass` string, and which matches the game's row order.
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

type GetStatsFn = unsafe extern "system" fn(*mut i32);
type ApplyStatsFn = unsafe extern "system" fn(*const i32);
type SlotsCountFn = unsafe extern "system" fn(usize, usize) -> u32;
type EquipMagicFn = unsafe extern "system" fn(usize, i32, u32) -> u8;
type MagicIdFn = unsafe extern "system" fn(usize, i32) -> i32;

/// What the level/attribute write achieved, read back from `PlayerGameData`.
#[derive(Debug, Default)]
pub struct StatsOutcome {
    /// Level before and after.
    pub level: (i32, i32),
    /// Attributes that still differ from the build after applying, as `(name, want, got)`.
    pub wrong: Vec<(&'static str, i32, i32)>,
}

impl StatsOutcome {
    /// Whether every attribute and the level match the build.
    pub fn is_correct(&self) -> bool {
        self.wrong.is_empty()
    }
}

/// The live `PlayerGameData*`, or `None`.
///
/// # Safety
///
/// Game thread only.
pub unsafe fn player_game_data() -> Option<usize> {
    use eldenring::cs::GameDataMan;
    use fromsoftware_shared::FromStatic;

    let gdm = GameDataMan::instance_ptr().ok()? as usize;
    if gdm == 0 {
        return None;
    }
    // Safety: one pointer read at the documented offset, checked for null.
    let pgd = unsafe { *((gdm + GAME_DATA_MAN_PLAYER_OFFSET) as *const usize) };
    (pgd != 0).then_some(pgd)
}

/// The archetype index for a class name, or `None` if unrecognised.
pub fn archetype_of(class_name: &str) -> Option<u8> {
    CLASSES
        .iter()
        .position(|name| name.eq_ignore_ascii_case(class_name))
        .and_then(|index| u8::try_from(index).ok())
}

/// Set the starting class.
///
/// Writes `PlayerGameData::archetype` directly and deliberately does **not** call the native
/// class setter at `0x140787250`: that one re-runs character initialisation, which overwrites
/// all eight attributes and the level with the class defaults and re-issues starting gear --
/// erasing the build. Nothing caches the archetype, so the byte is the whole operation.
///
/// Returns the value read back, which is the only evidence the write landed.
///
/// # Safety
///
/// Game thread, character loaded.
pub unsafe fn set_class(pgd: usize, class_name: &str) -> Option<(u8, u8)> {
    let wanted = archetype_of(class_name)?;
    // Safety: a one-byte write into live save data at a verified offset, then a read back.
    unsafe {
        *((pgd + PGD_ARCHETYPE) as *mut u8) = wanted;
        Some((wanted, *((pgd + PGD_ARCHETYPE) as *const u8)))
    }
}

/// Apply the build's level and attributes.
///
/// # Safety
///
/// Game thread, character in the world. `ApplyMainPlayerStats` `DLPanic`s on a null
/// `CSMenuMan`/`WorldChrMan` and skips the vitals recompute if `mainPlayerIns` is null, so
/// the caller's in-world gate is a precondition, not a nicety.
pub unsafe fn apply_stats(module_base: usize, pgd: usize, doc: &BuildDoc) -> StatsOutcome {
    let get: GetStatsFn = unsafe { core::mem::transmute(module_base + GET_MAIN_PLAYER_STATS) };
    let apply: ApplyStatsFn =
        unsafe { core::mem::transmute(module_base + APPLY_MAIN_PLAYER_STATS) };

    let mut stats = [0i32; 10];
    // Safety: the engine fills exactly ten ints.
    unsafe { get(stats.as_mut_ptr()) };
    // Safety: reading the level field for the before/after report.
    let before = unsafe { *((pgd + PGD_LEVEL) as *const i32) };

    let want = |key: &str| doc.stats.get(key).copied().unwrap_or_default() as i32;
    stats[STAT_LEVEL] = want("rl");
    stats[STAT_VIGOR] = want("vig");
    stats[STAT_ENDURANCE] = want("vit"); // the planner calls Endurance "vit"
    stats[STAT_MIND] = want("mnd");
    stats[STAT_STRENGTH] = want("str");
    stats[STAT_DEXTERITY] = want("dex");
    stats[STAT_INTELLIGENCE] = want("int");
    stats[STAT_FAITH] = want("fth");
    stats[STAT_ARCANE] = want("arc");
    // index 6 (baseDurability) keeps whatever the getter returned.

    // Safety: the engine reads ten ints and recomputes everything derived from them.
    unsafe { apply(stats.as_ptr()) };

    // Read back from the struct, not from the array we just handed over.
    let mut outcome = StatsOutcome::default();
    // Safety: plain reads of live save data at verified offsets.
    let after = unsafe { *((pgd + PGD_LEVEL) as *const i32) };
    outcome.level = (before, after);
    let checks: [(&'static str, usize, i32); 9] = [
        ("level", PGD_LEVEL, want("rl")),
        ("vigor", PGD_VIGOR, want("vig")),
        ("mind", PGD_MIND, want("mnd")),
        ("endurance", PGD_ENDURANCE, want("vit")),
        ("strength", PGD_STRENGTH, want("str")),
        ("dexterity", PGD_DEXTERITY, want("dex")),
        ("intelligence", PGD_INTELLIGENCE, want("int")),
        ("faith", PGD_FAITH, want("fth")),
        ("arcane", PGD_ARCANE, want("arc")),
    ];
    for (name, offset, wanted) in checks {
        // Safety: as above.
        let got = unsafe { *((pgd + offset) as *const i32) };
        if got != wanted {
            outcome.wrong.push((name, wanted, got));
        }
    }
    outcome
}

/// What the spell pass achieved.
#[derive(Debug, Default)]
pub struct SpellOutcome {
    /// Memory slots the game says the character has.
    pub capacity: u32,
    /// Spells the build wants memorised.
    pub wanted: usize,
    /// Slots whose contents afterwards match the build.
    pub verified: usize,
    /// Spells dropped because the character has too few memory slots.
    pub over_capacity: usize,
    /// `(slot, expected, actual)` for the first few failures.
    pub mismatches: Vec<(i32, i32, i32)>,
}

/// Memorise the build's spells, in order, within the character's real slot count.
///
/// # Safety
///
/// Game thread, character loaded, `egd` live.
pub unsafe fn memorise_spells(module_base: usize, egd: usize, spells: &[EquipRef]) -> SpellOutcome {
    let mut outcome = SpellOutcome {
        wanted: spells.len(),
        ..SpellOutcome::default()
    };

    // Safety: one pointer read at the verified EquipGameData offset.
    let emd = unsafe { *((egd + EQUIP_GAME_DATA_MAGIC_OFFSET) as *const usize) };
    if emd == 0 {
        return outcome;
    }

    let slots_count: SlotsCountFn =
        unsafe { core::mem::transmute(module_base + GET_MAGIC_SLOTS_COUNT) };
    let equip: EquipMagicFn = unsafe { core::mem::transmute(module_base + EQUIP_MAGIC_IN_SLOT) };
    let magic_id: MagicIdFn = unsafe { core::mem::transmute(module_base + GET_EQUIP_MAGIC_ID) };

    // Ask the game, never assume: this accounts for Memory Stones and talismans, and the
    // engine clamps it to 14.
    // Safety: null SpecialEffect means "derive it from the player", which the function handles.
    outcome.capacity = unsafe { slots_count(emd, 0) };

    for (index, spell) in spells.iter().enumerate() {
        let Ok(slot) = i32::try_from(index) else {
            break;
        };
        if index >= outcome.capacity as usize {
            outcome.over_capacity += 1;
            continue;
        }
        // Safety: slot is within the reported capacity; the id is a MAGIC_PARAM row id.
        unsafe { equip(emd, slot, spell.param_id) };
        // Safety: the read-back.
        let actual = unsafe { magic_id(emd, slot) };
        let expected = spell.param_id as i32;
        if actual == expected {
            outcome.verified += 1;
        } else if outcome.mismatches.len() < 8 {
            outcome.mismatches.push((slot, expected, actual));
        }
    }

    outcome
}

/// `PlayerGameData::runeArcActive`.
const PGD_RUNE_ARC_ACTIVE: usize = 0xff;

/// Light the rune arc so an equipped great rune actually applies.
///
/// `CS::ChrIns::CanUseRuneArc` is `runeArcActive && IsHostLike(chrType)`, and
/// `ChrBigRuneSpEffectSlot::Update` applies the rune's SpEffects from there -- so this one bool
/// is what turns an equipped rune into a live buff. Returns the value read back.
///
/// # Safety
///
/// Game thread, character loaded.
pub unsafe fn activate_rune_arc() -> bool {
    let Some(pgd) = (unsafe { player_game_data() }) else {
        return false;
    };
    // Safety: one bool write into live save data at a verified offset, then a read back.
    unsafe {
        *((pgd + PGD_RUNE_ARC_ACTIVE) as *mut u8) = 1;
        *((pgd + PGD_RUNE_ARC_ACTIVE) as *const u8) != 0
    }
}
