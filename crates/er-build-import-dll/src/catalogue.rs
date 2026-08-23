//! Building the item catalogue from the running game.
//!
//! The planner payload names items and gives no ids, so importing a build needs a
//! name -> id map. The obvious source is the planner's own bundled database, but the
//! game already holds the same table -- in the player's language, at the player's patch
//! level, DLC included -- so this asks the game instead of shipping a copy of someone
//! else's dataset.
//!
//! # Two halves, both borrowed rather than invented
//!
//! *Rows* come from [`SoloParamRepository::rows`], which yields `(row_id, &row)` for a
//! param table. *Names* come from the game's own per-category getters, each a one-call
//! wrapper around `MsgRepositoryImp::LookupEntry` that walks its own bundle chain.
//!
//! Calling those wrappers rather than reimplementing the chain is not fastidiousness.
//! `GetGoodsName` consults **four** bundles (`0x6f`, `0xa`, `0x13f`, `0x1a3`), while the
//! neighbouring categories look like a tidy base/dlc01 pair -- an importer that inferred
//! the pattern from its neighbours would silently lose two whole bundles of consumables.
//!
//! # The map runs the wrong way, on purpose
//!
//! The game answers *id -> name*; the importer needs *name -> id*. So the table is built
//! by enumerating every row, asking for its name, and inverting. That is a few thousand
//! calls once, not per lookup.

use std::collections::BTreeSet;

use er_build_import::catalog::{Entry, Kind, MapCatalog};

/// `MsgRepositoryImp::LookupEntry` returns a placeholder like `?GoodsName?` for a row that
/// has no name. Those are misses, not items, and a catalogue that accepted them would
/// happily resolve `"?GoodsName?"` to whichever row asked last.
const PLACEHOLDER_PREFIX: char = '?';

/// RVAs of the game's per-category name getters, verified in the 1.16.2 dump.
///
/// Each takes `(MsgRepositoryImp*, u32 row_id)` and returns a NUL-terminated `wchar_t*`.
mod rva {
    /// `MsgRepositoryImp::GetWeaponName` -- base bundle `0x136`.
    pub const GET_WEAPON_NAME: usize = 0xd11370;
    /// `MsgRepositoryImp::GetProtectorName` -- base bundle `0x139`.
    pub const GET_PROTECTOR_NAME: usize = 0xd10d90;
    /// `MsgRepositoryImp::GetAccessoryName` -- base bundle `0x13c`, DLC `0x1a0`.
    pub const GET_ACCESSORY_NAME: usize = 0xd0fda0;
    /// `MsgRepositoryImp::GetGoodsName` -- bundles `0x6f`, `0xa`, `0x13f`, `0x1a3`.
    pub const GET_GOODS_NAME: usize = 0xd10600;
    /// `MsgRepositoryImp::GetGemName` -- base bundle `0x142`, DLC `0x1a6`.
    ///
    /// Names the *gem item* ("Ash of War: Lion's Claw"), which is NOT what a build's
    /// `weaponArt` says. Kept for reference; the importer uses [`GET_ARTS_NAME`].
    #[allow(dead_code)]
    pub const GET_GEM_NAME: usize = 0xd103d0;
    /// `MsgRepositoryImp::GetArtsName` -- base bundle `0x14b`.
    ///
    /// Names the *skill* ("Lion's Claw"), keyed by `SwordArtsParam` row id -- which is
    /// exactly what a build's `weaponArt` carries and what `weaponSkill` encodes.
    pub const GET_ARTS_NAME: usize = 0xd0ff70;
}

/// The category tag the game ORs into the high nibble of an item id.
///
/// Confirmed by the game's own code rather than inferred from the planner's data:
/// `EquipGreatRune` rejects anything whose `id & 0xF0000000` is not `0x40000000` and takes
/// the row id as `id & 0x0FFFFFFF`.
#[derive(Clone, Copy)]
enum Tag {
    Weapon = 0x0000_0000,
    Protector = 0x1000_0000,
    Accessory = 0x2000_0000,
    Goods = 0x4000_0000,
}

/// `(MsgRepositoryImp*, row_id) -> wchar_t*`
type NameGetter = unsafe extern "system" fn(usize, u32) -> *const u16;

/// One category's recipe: where its rows live, what to call them, and how to tag them.
struct Source {
    kind: Kind,
    tag: Tag,
    getter_rva: usize,
}

/// Every category the importer resolves.
///
/// Ashes of war come from `SwordArtsParam`, NOT `EquipParamGem`. The distinction is the
/// whole reason the first catalogue left ten of them unresolved: a gem is the *item* you
/// pick up ("Ash of War: Bloodhound's Step"), while a build's `weaponArt` names the
/// *skill* ("Bloodhound's Step"), and the two live in different tables under different
/// name bundles. The planner agrees -- its ash ids are 80100, 401000, 505000, which are
/// `SwordArtsParam` rows -- and so does the game: `weaponSkill` is `0x80000000 | skillId`.
///
/// Spells and tools are BOTH `EquipParamGoods` rows sharing one name bundle -- a sorcery's
/// item id really is `0x4 << 28 | goodsRowId`. They are separated by whether the same row
/// id also exists in `MAGIC_PARAM_ST`, which is the game's own distinction, not a heuristic.
const SOURCES: &[Source] = &[
    Source {
        kind: Kind::Weapon,
        tag: Tag::Weapon,
        getter_rva: rva::GET_WEAPON_NAME,
    },
    Source {
        kind: Kind::Protector,
        tag: Tag::Protector,
        getter_rva: rva::GET_PROTECTOR_NAME,
    },
    Source {
        kind: Kind::Talisman,
        tag: Tag::Accessory,
        getter_rva: rva::GET_ACCESSORY_NAME,
    },
    Source {
        kind: Kind::AshOfWar,
        tag: Tag::Accessory,
        getter_rva: rva::GET_ARTS_NAME,
    },
    Source {
        kind: Kind::Spell,
        tag: Tag::Goods,
        getter_rva: rva::GET_GOODS_NAME,
    },
    Source {
        kind: Kind::Tool,
        tag: Tag::Goods,
        getter_rva: rva::GET_GOODS_NAME,
    },
    Source {
        kind: Kind::GreatRune,
        tag: Tag::Goods,
        getter_rva: rva::GET_GOODS_NAME,
    },
];

/// The only goods rows the game accepts as great runes.
///
/// `GetGreatruneEnumByGoodsId` (0x140d39ab0) switches on exactly `0xbf..=0xc4` -- Godrick,
/// Radahn, Morgott, Rykard, Mohg, Malenia -- and returns "none" for everything else. The goods
/// table holds other rows with the same display names, so restricting the catalogue to this
/// range is what makes the name lookup land on a rune the engine will actually equip.
const GREAT_RUNE_ROWS: core::ops::RangeInclusive<u32> = 191..=196;

/// What a catalogue build found, for the log.
#[derive(Debug, Default)]
pub struct BuildStats {
    /// Rows that produced a usable name.
    pub named: usize,
    /// Rows whose name was absent or a `?placeholder?`.
    pub unnamed: usize,
    /// How many goods rows were classified as spells.
    pub spell_rows: usize,
}

/// Read a NUL-terminated UTF-16 string, bounded so a bad pointer cannot run away.
///
/// # Safety
///
/// `ptr` must be null or point to a NUL-terminated UTF-16 string.
unsafe fn read_wide(ptr: *const u16) -> Option<String> {
    if ptr.is_null() {
        return None;
    }
    // Item names are short; the bound exists so a corrupt pointer fails instead of scanning.
    const MAX_CHARS: usize = 256;
    let mut units = Vec::new();
    for offset in 0..MAX_CHARS {
        // Safety: bounded by MAX_CHARS and stopped at the first NUL.
        let unit = unsafe { *ptr.add(offset) };
        if unit == 0 {
            break;
        }
        units.push(unit);
    }
    let text = String::from_utf16(&units).ok()?;
    if text.is_empty() || text.starts_with(PLACEHOLDER_PREFIX) {
        return None;
    }
    Some(text)
}

/// Ask the game for one row's display name.
///
/// # Safety
///
/// `getter` must be the address of a live `(MsgRepositoryImp*, u32) -> wchar_t*` function
/// and `msg` a valid `MsgRepositoryImp*`.
unsafe fn name_of(getter: usize, msg: usize, row_id: u32) -> Option<String> {
    // Safety: the caller guarantees the address is one of the verified getters.
    let getter: NameGetter = unsafe { core::mem::transmute::<usize, NameGetter>(getter) };
    // Safety: the getter is a plain lookup; it does not retain the pointer it returns.
    unsafe { read_wide(getter(msg, row_id)) }
}

/// Insert one resolved row.
fn insert(catalog: &mut MapCatalog, source: &Source, row_id: u32, name: &str) {
    catalog.insert(
        source.kind,
        name,
        Entry {
            full_item_id: (source.tag as u32) | row_id,
            max_stored: None,
            somber: false,
        },
    );
}

/// Row ids for one category, read from the live param tables.
///
/// Returns an empty list rather than failing when the repository is not up yet; the caller
/// sees that as zero named rows, which is the honest report for "the game is not ready".
fn row_ids(kind: Kind, spells: &BTreeSet<u32>) -> Vec<u32> {
    use eldenring::cs::{
        EquipParamAccessory, EquipParamGoods, EquipParamProtector, EquipParamWeapon,
        SoloParamRepository, SwordArtsParam,
    };
    use fromsoftware_shared::FromStatic;

    // Safety: `instance()` returns a reference only when the singleton is populated, and the
    // rows are read, never written.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return Vec::new();
    };
    match kind {
        Kind::Weapon => repo.rows::<EquipParamWeapon>().map(|(id, _)| id).collect(),
        Kind::Protector => repo
            .rows::<EquipParamProtector>()
            .map(|(id, _)| id)
            .collect(),
        Kind::Talisman => repo
            .rows::<EquipParamAccessory>()
            .map(|(id, _)| id)
            .collect(),
        Kind::AshOfWar => repo.rows::<SwordArtsParam>().map(|(id, _)| id).collect(),
        // The goods table carries both; membership in MAGIC_PARAM_ST decides which.
        Kind::Spell => repo
            .rows::<EquipParamGoods>()
            .map(|(id, _)| id)
            .filter(|id| spells.contains(id))
            .collect(),
        Kind::Tool => repo
            .rows::<EquipParamGoods>()
            .map(|(id, _)| id)
            .filter(|id| !spells.contains(id))
            .collect(),
        Kind::GreatRune => GREAT_RUNE_ROWS.collect(),
        Kind::Ammo => Vec::new(),
    }
}

/// Every row id present in `MAGIC_PARAM_ST`, i.e. every goods row that is really a spell.
fn spell_row_ids() -> BTreeSet<u32> {
    use eldenring::cs::{Magic, SoloParamRepository};
    use fromsoftware_shared::FromStatic;

    // Safety: as above -- a read-only enumeration behind a populated-singleton check.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return BTreeSet::new();
    };
    repo.rows::<Magic>().map(|(id, _)| id).collect()
}

/// Build the catalogue from the running game.
///
/// # Safety
///
/// `msg` must be a live `MsgRepositoryImp*`, `module_base` the loaded image base, and the
/// game must be far enough along that the message repository and param tables are populated.
pub unsafe fn build_from_game(msg: usize, module_base: usize) -> (MapCatalog, BuildStats) {
    let spells = spell_row_ids();
    let mut catalog = MapCatalog::new();
    let mut stats = BuildStats {
        spell_rows: spells.len(),
        ..BuildStats::default()
    };

    for source in SOURCES {
        let getter = module_base + source.getter_rva;
        for row_id in row_ids(source.kind, &spells) {
            // Safety: `getter` is a verified RVA within the loaded module and `msg` is the
            // caller's live repository pointer.
            match unsafe { name_of(getter, msg, row_id) } {
                Some(name) => {
                    insert(&mut catalog, source, row_id, &name);
                    stats.named += 1;
                }
                None => stats.unnamed += 1,
            }
        }
    }

    (catalog, stats)
}

/// Whether a param table has been streamed in.
///
/// `rows()` cannot answer this: `SoloParamRepository::get_param_file` does
/// `holder.get_res_cap(0).expect(...)`, so asking a not-yet-loaded table for its rows
/// PANICS rather than yielding nothing. That is what killed the first two catalogue runs.
/// The res cap itself is an `Option`, so checking it is the non-destructive question.
fn holder_ready<P: eldenring::cs::SoloParam>(repo: &eldenring::cs::SoloParamRepository) -> bool {
    repo.solo_param_holders
        .get(P::INDEX as usize)
        .and_then(|holder| holder.get_res_cap(0))
        .is_some()
}

/// Whether every table the catalogue reads is loaded.
///
/// All of them, not just one: a partially-streamed repository would let the first category
/// through and then panic on a later one, which is the same failure with a longer fuse.
pub fn params_ready() -> bool {
    use eldenring::cs::{
        EquipParamAccessory, EquipParamGoods, EquipParamProtector, EquipParamWeapon, Magic,
        SoloParamRepository, SwordArtsParam,
    };
    use fromsoftware_shared::FromStatic;

    // Safety: read-only field access behind the populated-singleton check.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return false;
    };
    holder_ready::<EquipParamWeapon>(repo)
        && holder_ready::<EquipParamProtector>(repo)
        && holder_ready::<EquipParamAccessory>(repo)
        && holder_ready::<EquipParamGoods>(repo)
        && holder_ready::<SwordArtsParam>(repo)
        && holder_ready::<Magic>(repo)
}

/// The live `MsgRepositoryImp*`, or `None` before it exists.
pub fn msg_repository() -> Option<usize> {
    use eldenring::cs::MsgRepositoryImp;
    use fromsoftware_shared::FromStatic;

    MsgRepositoryImp::instance_ptr()
        .ok()
        .map(|ptr| ptr as usize)
        .filter(|ptr| *ptr != 0)
}
