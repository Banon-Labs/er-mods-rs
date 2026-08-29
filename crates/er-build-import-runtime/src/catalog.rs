//! Building the item catalog from the running game.
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

use er_build_import_core::catalog::{Entry, Kind, MapCatalog};

/// `MsgRepositoryImp::LookupEntry` returns a placeholder like `?GoodsName?` for a row that
/// has no name. Those are misses, not items, and a catalog that accepted them would
/// happily resolve `"?GoodsName?"` to whichever row asked last.
const PLACEHOLDER_PREFIX: char = '?';

/// The OTHER placeholder, and the one that actually ships in the message files: FromSoftware names
/// its dummy and unused rows `[ERROR]`, `[ERROR]Type 1`, `[ERROR]type 10`, and so on. There are
/// hundreds of them -- they are why a catalog build reports around a hundred colliding names, all
/// of them this one string -- and not one is an item. Treating them as names lets a slot holding a
/// dummy row export as an item nothing can resolve, and lets a build asking for `"[error]"` import
/// whichever dummy row was enumerated last. Verified against the shipped message files:
/// `python3 scripts/er-item-name.py ProtectorName 1000` answers `'[ERROR]Type 1'`.
const ERROR_PLACEHOLDER_PREFIX: &str = "[error]";

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
    /// `weaponArt` says -- so it is not what the catalog keys on. It is used as the test for
    /// whether a gem row is a real, obtainable ash rather than a development placeholder,
    /// which is how the ash catalog picks between several gems carrying one skill.
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
/// `repr(u32)` because the gem category's discriminant sets bit 31, which does not fit a signed
/// pointer-width discriminant on a 32-bit target -- and the tags ARE u32 item ids, not indices.
#[derive(Clone, Copy)]
#[repr(u32)]
enum Tag {
    Weapon = 0x0000_0000,
    Protector = 0x1000_0000,
    Accessory = 0x2000_0000,
    Goods = 0x4000_0000,
    /// `EquipParamGem`, i.e. an ash of war as an ITEM.
    ///
    /// Same evidence as the others, from the same switch: `GetGaitemHandleByItemId` sends
    /// `itemId >> 28 == 8` to `GetGaItemHandleGem`, and `GaitemLookupResult::GetSwordArtsParamId`
    /// takes an id apart as `0x8000_0000 | EquipParamGem row`.
    Gem = 0x8000_0000,
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
/// ASHES OF WAR ARE THE ONE CATEGORY WHOSE NAME AND ID COME FROM DIFFERENT TABLES, which is
/// why they are not in this list and are built by [`insert_ashes_of_war`] instead.
///
/// A build's `weaponArt` names the *skill* ("Bloodhound's Step"), which is a `SwordArtsParam`
/// row and is named out of the arts bundle. But the thing the game can PUT ON A WEAPON is the
/// *gem* ("Ash of War: Bloodhound's Step"), an `EquipParamGem` row -- `GetSwordArtsParamId`
/// reads `0x8000_0000 | gem row`, and `GetGaItemHandleGem` mints from a gem row. So the catalog
/// keys on the arts name and stores the GEM id.
///
/// Resolving to the `SwordArtsParam` row instead is the bug this replaces, and it was invisible:
/// every ash in a build resolved, the importer reported `0 unresolved`, and not one weapon came
/// out carrying its ash. The planner's own ids were the tell all along -- 80100, 401000, 505000
/// are multiples of 100 in gem space (`gem = arts * 100` for most ashes), not arts rows.
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
/// table holds other rows with the same display names, so restricting the catalog to this
/// range is what makes the name lookup land on a rune the engine will actually equip.
const GREAT_RUNE_ROWS: core::ops::RangeInclusive<u32> = 191..=196;

/// What a catalog build found, for the log.
#[derive(Debug, Default)]
pub struct BuildStats {
    /// Rows that produced a usable name.
    pub named: usize,
    /// Rows whose name was absent or a `?placeholder?`.
    pub unnamed: usize,
    /// How many goods rows were classified as spells.
    pub spell_rows: usize,
    /// Ashes whose ONLY `EquipParamGem` row draws no icon, so the tile badge falls back to the
    /// literal `ICON` placeholder. Non-zero is not a crash and not a wrong ash -- the name and
    /// the skill are right -- but it is the exact defect a player reports as "the ash symbol
    /// says ICON", so it is a number here rather than a discovery in-game.
    pub iconless_ashes: usize,
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
    // Case-insensitively, because the shipped files spell it both `[ERROR]Type 1` and
    // `[ERROR]type 10` -- a case-sensitive test would let half of them through. Sliced with `get`
    // rather than `[..7]`: item names are UTF-16 from the game and a byte-index slice through a
    // multi-byte character PANICS, which in this crate means taking the game down to avoid
    // exporting a placeholder.
    if text
        .get(..ERROR_PLACEHOLDER_PREFIX.len())
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case(ERROR_PLACEHOLDER_PREFIX))
    {
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

/// The name getter for one category, or `None` for a category the game does not name this way.
///
/// [`Kind::AshOfWar`] is answered explicitly rather than out of [`SOURCES`]: it is not in that
/// table (its rows come from the gem table, see [`insert_ashes_of_war`]), but it IS named by
/// `GetArtsName` keyed on a `SwordArtsParam` row, which is exactly what the EXPORT direction has
/// in hand when it asks.
fn getter_rva_for(kind: Kind) -> Option<usize> {
    if kind == Kind::AshOfWar {
        return Some(rva::GET_ARTS_NAME);
    }
    SOURCES
        .iter()
        .find(|source| source.kind == kind)
        .map(|source| source.getter_rva)
}

/// Ask the game what ONE row is called -- the export direction.
///
/// # Why this exists next to [`build_from_game`] rather than inverting it
///
/// The importer needs name -> id, which the game cannot answer, so it enumerates every row and
/// inverts. The EXPORTER needs id -> name, which is the direction the game answers natively: one
/// call, no table. Building the whole catalog to read it backwards would be a few thousand calls
/// to answer a question the getter answers directly -- and it would also be WRONG in one case,
/// because the inverted map is keyed by folded name and two rows can fold together.
///
/// `Kind` still matters: the same row id means different items in different tables, so the caller
/// must say which table the id came from.
///
/// # Safety
///
/// `msg` must be a live `MsgRepositoryImp*` and `module_base` the loaded image base.
pub unsafe fn name_for(kind: Kind, msg: usize, module_base: usize, row_id: u32) -> Option<String> {
    let getter = module_base + getter_rva_for(kind)?;
    // Safety: `getter` is a verified RVA within the loaded module and `msg` is the caller's live
    // repository pointer.
    unsafe { name_of(getter, msg, row_id) }
}

/// Insert one resolved row.
///
/// `somber` is left false ON PURPOSE, and setting it would be a regression rather than a fix.
/// It only feeds [`er_build_import_core::plan::somber_remap`], a table this repository reproduces from
/// the planner without knowing its intent, and the importer no longer needs to guess how far an
/// armament upgrades: [`ReinforceLevels`] asks the game which `reinforceTypeId + level` rows
/// exist and clamps to the highest one the build can have. Reviving the flag would put a guessed
/// level back in front of a measured one.
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
        SoloParamRepository,
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
        // Built from the gem table by `insert_ashes_of_war`, which needs BOTH ids per row.
        Kind::AshOfWar => Vec::new(),
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

/// Build the catalog from the running game.
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

    // Safety: the caller's contract carries through unchanged.
    unsafe { insert_ashes_of_war(&mut catalog, &mut stats, msg, module_base) };

    (catalog, stats)
}

/// Add every ash of war, keyed by SKILL name and valued by GEM id.
///
/// # Why this is not just another [`Source`]
///
/// Every other category answers one question with one id: enumerate the rows of table T, ask T's
/// name getter about each row, store that row. An ash needs two tables at once. The name the
/// planner writes is the skill's (`GetArtsName`, keyed on a `SwordArtsParam` row), while the id
/// the game can act on is the gem's (`EquipParamGem`, which is what `GetGaItemHandleGem` mints and
/// what `GetSwordArtsParamId` decodes). `EquipParamGem.swordArtsParamId` is the join.
///
/// # Which gem, when several carry the same skill
///
/// The gem table holds development and placeholder rows alongside the real ashes, and more than
/// one row can name the same skill. Rather than trust `arts * 100` -- a heuristic that lands on
/// an UNRELATED row for some ashes (arts 309 "Thops's Barrier" -> gem 30900 is "No Skill";
/// Igon's Drake Hunt is arts 4210 but gem 548000) -- this walks the whole table and keeps, per
/// skill, the first of: a gem the game gives a display name to, else the lowest row id. Both
/// tie-breaks are order-independent, so the catalog is the same on every run.
///
/// # Safety
///
/// `msg` must be a live `MsgRepositoryImp*` and `module_base` the loaded image base, with the
/// param tables streamed (see [`params_ready`]).
unsafe fn insert_ashes_of_war(
    catalog: &mut MapCatalog,
    stats: &mut BuildStats,
    msg: usize,
    module_base: usize,
) {
    use eldenring::cs::{EquipParamGem, SoloParamRepository};
    use fromsoftware_shared::FromStatic;
    use std::collections::BTreeMap;

    // Safety: read-only enumeration behind the populated-singleton check.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return;
    };
    // Resolved for the running build rather than added blind: `name_of` transmutes these into
    // function pointers and CALLS them, and its safety comment says the caller guarantees the
    // address is one of the verified getters -- which was not true while this was bare addition.
    // On a build that moved the code, an unresolvable getter means no ash names, not a call into
    // whatever now occupies the address.
    let (Some(arts_getter), Some(gem_getter)) = (
        er_game_base::game_build::resolve_game_address(
            module_base + rva::GET_ARTS_NAME,
            "catalog GetArtsName",
        ),
        er_game_base::game_build::resolve_game_address(
            module_base + rva::GET_GEM_NAME,
            "catalog GetGemName",
        ),
    ) else {
        return;
    };

    // skill row -> (this gem IS the canonical `arts * 100` row, it draws an icon, it has an
    // item name, gem row).
    //
    // THE ICON IS THE FIRST KEY, and it is why this is not just "named, else lowest row".
    // MEASURED 2026-08-23: a build imported with every ash NAME correct and the ash badge on the
    // tile rendering the literal placeholder text `ICON`. The names were right because the label
    // comes from `EquipParamGem.swordArtsParamId` -> `GetArtsName`, which a development row
    // carries just as faithfully as a real one; the icon was missing because that row has none.
    // The old tiebreak fell back to THE LOWEST ROW ID whenever no gem for a skill was named, and
    // the lowest rows are exactly the placeholder rows -- which is how ids like 103, 146, 185 and
    // 191 ended up mounted on the player's weapons next to real ashes at 401000 and 22800.
    //
    // `0` and `u16::MAX` are both treated as "no icon": the field is unsigned, so a row that
    // means "none" can only say so with one of the two ends, and neither names a real texture.
    let mut best: BTreeMap<u32, (bool, bool, bool, u32)> = BTreeMap::new();
    let mut iconless = 0usize;
    // arts row -> (how many gem rows carry it, the icon id of the one that won). Only consulted
    // for the ashes that end up iconless, but it has to be gathered during the walk.
    let mut gem_shape: BTreeMap<u32, (usize, u16)> = BTreeMap::new();
    for (gem_id, row) in repo.rows::<EquipParamGem>() {
        let Ok(arts_id) = u32::try_from(row.sword_arts_param_id()) else {
            continue;
        };
        if arts_id == 0 {
            continue;
        }
        // Safety: a verified getter RVA and the caller's live repository pointer.
        let named = unsafe { name_of(gem_getter, msg, gem_id) }.is_some();
        let icon = row.icon_id();
        let has_icon = icon != 0 && icon != u16::MAX;
        // THE CANONICAL ROW WINS OUTRIGHT. `gem == arts * 100` is where the purchasable "Ash of
        // War: <skill>" item lives for the overwhelming majority of skills, and it is only
        // reached here after the walk has already CONFIRMED this row carries this arts id -- so
        // the heuristic the module header warns about (arts 309 -> gem 30900 is "No Skill",
        // Igon's Drake Hunt is arts 4210 but gem 548000) cannot fire: a row that does not carry
        // the skill never becomes a candidate for it, and those skills fall through to the keys
        // below exactly as before.
        //
        // WITHOUT THIS, "lowest row id" decides -- and it is wrong, because a block of low
        // four-digit rows around 1000-1020 carries the same skills as the real ash items and is
        // named and iconned enough to beat nothing. MEASURED 2026-08-23: Flaming Strike (arts 214)
        // resolved to gem 1010 instead of 21400 and the tile drew the `ICON` placeholder, with the
        // ash NAME still correct because that comes from `swordArtsParamId`. Broadsword took 1002
        // over 10300, Star Fist 1013 over 50200, Rusted Anchor 1010 over 21400.
        let canonical = gem_id == arts_id.saturating_mul(100);
        let candidate = (canonical, has_icon, named, gem_id);
        gem_shape
            .entry(arts_id)
            .and_modify(|seen| seen.0 += 1)
            .or_insert((1, icon));
        best.entry(arts_id)
            .and_modify(|held| {
                // The canonical row beats everything; then an icon beats no icon; then a
                // named gem beats an unnamed one; among equals the lower row id wins.
                if (canonical, has_icon, named, core::cmp::Reverse(gem_id))
                    > (held.0, held.1, held.2, core::cmp::Reverse(held.3))
                {
                    *held = candidate;
                }
            })
            .or_insert(candidate);
    }

    for (arts_id, (_, has_icon, _, gem_id)) in best {
        // Counted, not silently accepted: an ash whose ONLY gem row draws no icon will still
        // render `ICON` on the tile, and that has to be a number in the log rather than a
        // surprise on the player's weapon.
        if !has_icon {
            iconless += 1;
            // NAMED, not just counted. "5 ashes have no icon" cannot be acted on; "Flaming Strike
            // has no icon, its winning gem is row N with iconId 0, and it has M gem rows" says
            // straight away whether the row is a genuine placeholder or whether `0` is a real
            // icon id this filter is wrongly rejecting.
            let (rows, icon) = gem_shape.get(&arts_id).copied().unwrap_or((0, 0));
            // Safety: a verified getter RVA and the caller's live repository pointer.
            let label = unsafe { name_of(arts_getter, msg, arts_id) }
                .unwrap_or_else(|| format!("<unnamed arts {arts_id}>"));
            crate::log_line(&format!(
                "[build-import]   ICONLESS ASH {label:?} arts {arts_id} -> gem {gem_id} \
                 (iconId {icon}, {rows} gem row(s) carry this skill)"
            ));
        }
        // Safety: as above.
        match unsafe { name_of(arts_getter, msg, arts_id) } {
            Some(name) => {
                catalog.insert(
                    Kind::AshOfWar,
                    &name,
                    Entry {
                        full_item_id: (Tag::Gem as u32) | gem_id,
                        max_stored: None,
                        somber: false,
                    },
                );
                stats.named += 1;
            }
            None => stats.unnamed += 1,
        }
    }
    stats.iconless_ashes = iconless;
}

/// Whether a param table has been streamed in.
///
/// `rows()` cannot answer this: `SoloParamRepository::get_param_file` does
/// `holder.get_res_cap(0).expect(...)`, so asking a not-yet-loaded table for its rows
/// PANICS rather than yielding nothing. That is what killed the first two catalog runs.
/// The res cap itself is an `Option`, so checking it is the non-destructive question.
fn holder_ready<P: eldenring::cs::SoloParam>(repo: &eldenring::cs::SoloParamRepository) -> bool {
    repo.solo_param_holders
        .get(P::INDEX as usize)
        .and_then(|holder| holder.get_res_cap(0))
        .is_some()
}

/// Whether every table the catalog reads is loaded.
///
/// All of them, not just one: a partially-streamed repository would let the first category
/// through and then panic on a later one, which is the same failure with a longer fuse.
pub fn params_ready() -> bool {
    use eldenring::cs::{
        EquipParamAccessory, EquipParamGem, EquipParamGoods, EquipParamProtector, EquipParamWeapon,
        Magic, ReinforceParamWeapon, SoloParamRepository, SwordArtsParam,
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
        // The ash catalog joins the gem table to the arts table; asking either one for rows
        // before it is streamed PANICS rather than yielding nothing. `ReinforceLevels` reads the
        // third table for the same reason, and has the same failure mode.
        && holder_ready::<EquipParamGem>(repo)
        && holder_ready::<ReinforceParamWeapon>(repo)
        && holder_ready::<Magic>(repo)
}

/// The live `CS::MsgRepositoryImp*`, or `None` before it exists.
///
/// Read out of the game's own global rather than through a typed upstream singleton. There IS a
/// `MsgRepositoryImp` in `fromsoftware-rs` -- but only in a local fork, not at the revision CI
/// pins, so importing it compiles on one machine and fails everywhere else. The address lives in
/// `er-game-base::rva` beside the other cross-crate ones.
///
/// A zero is "not constructed yet", not an error: the whole point of this being an `Option` is
/// that the caller re-asks next frame instead of dereferencing a null the engine would DLPanic on.
pub fn msg_repository() -> Option<usize> {
    let base = er_game_base::mem::game_module_base().ok()?;
    // Safety: a fault-checked read of one pointer-sized slot in the loaded image.
    let repository = unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            base,
            er_game_base::rva::MSG_REPOSITORY_GLOBAL_RVA,
            "MSG_REPOSITORY_GLOBAL_RVA",
        ))
    }?;
    (repository != 0).then_some(repository)
}

/// The `SwordArtsParam` row an `EquipParamGem` row carries, straight off the live table.
///
/// The importer needs this to check its own work: the build names a gem, the worn weapon reports
/// an arts row, and only `EquipParamGem.swordArtsParamId` says whether those are the same ash.
/// Comparing display names instead would turn a wrong-row bug into a string-matching bug.
pub fn arts_row_for_gem(gem_row: u32) -> Option<u32> {
    use eldenring::cs::{EquipParamGem, SoloParamRepository};
    use fromsoftware_shared::FromStatic;

    // Safety: read-only row access behind the populated-singleton check.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return None;
    };
    repo.rows::<EquipParamGem>()
        .find(|(id, _)| *id == gem_row)
        .and_then(|(_, row)| u32::try_from(row.sword_arts_param_id()).ok())
        .filter(|arts| *arts != 0)
}

/// Which upgrade levels the game actually has rows for, so a requested level cannot invent one.
///
/// # Why a level has to be clamped at all
///
/// `EquipParamWeapon::GetEntry(paramId)` splits its argument: the armament row is
/// `(paramId / 100) * 100`, and the level is `paramId % 100`, which it turns into
/// `ReinforceParamWeapon::GetEntry(row.reinforceTypeId + level)`. Somber armaments stop at +10,
/// so a level of 25 asks for a reinforce row that does not exist and the lookup comes back with a
/// NULL row for everything downstream to read.
///
/// The importer cannot tell somber from standard on its own: the build's `weaponUpgrade` is one
/// number for the whole character, the per-slot override is usually absent, and the planner's
/// somber remap is a table this repository reproduces without knowing its intent. So it does not
/// guess -- it asks which `reinforceTypeId + level` rows exist and takes the highest one at or
/// below what the build wanted.
pub struct ReinforceLevels {
    /// Every `ReinforceParamWeapon` row id present in the live table.
    rows: std::collections::BTreeSet<u32>,
}

impl ReinforceLevels {
    /// Read the table. Empty when the repository is not up, which clamps everything to +0 rather
    /// than writing a level nothing can back.
    pub fn read() -> Self {
        use eldenring::cs::{ReinforceParamWeapon, SoloParamRepository};
        use fromsoftware_shared::FromStatic;

        // Safety: read-only enumeration behind the populated-singleton check.
        let rows = match unsafe { SoloParamRepository::instance() } {
            Ok(repo) => repo
                .rows::<ReinforceParamWeapon>()
                .map(|(id, _)| id)
                .collect(),
            Err(_) => std::collections::BTreeSet::new(),
        };
        Self { rows }
    }

    /// The GAME level to store for an armament the build wants at `requested`.
    ///
    /// Two numbers reach this function on DIFFERENT scales, and `is_character_default` says which
    /// one this is (see `er_build_import_core::plan::Grant::upgrade_is_character_default`):
    ///
    /// * a per-slot `upgrade` is already the game's level, so it is only clamped;
    /// * the character-wide `weaponUpgrade` is in regular smithing-stone levels for EVERY
    ///   armament, so for a somber one it is mapped down first -- `weaponUpgrade: 17` means the
    ///   game's +7 there, and clamping 17 instead would silently hand over a maxed +10.
    ///
    /// Somber is MEASURED rather than flagged: an armament whose highest existing
    /// `reinforceTypeId + level` row is [`er_build_import_core::plan::MAX_SOMBER_LEVEL`] is one,
    /// which is the same question [`Self::clamp`] already answers.
    pub fn game_level_for(
        &self,
        weapon_param_id: u32,
        requested: u16,
        is_character_default: bool,
    ) -> u16 {
        use er_build_import_core::plan::{
            MAX_REGULAR_LEVEL, MAX_SOMBER_LEVEL, somber_level_for_regular,
        };

        if !is_character_default {
            return self.clamp(weapon_param_id, requested);
        }
        let max = self.clamp(weapon_param_id, MAX_REGULAR_LEVEL);
        let wanted = if max == MAX_SOMBER_LEVEL {
            somber_level_for_regular(requested)
        } else {
            requested
        };
        self.clamp(weapon_param_id, wanted)
    }

    /// The highest level at or below `requested` that `weapon_param_id` actually has a row for.
    ///
    /// Returns 0 when the armament row cannot be read, which is the level the game would have
    /// stored anyway before this code existed.
    pub fn clamp(&self, weapon_param_id: u32, requested: u16) -> u16 {
        use eldenring::cs::{EquipParamWeapon, SoloParamRepository};
        use fromsoftware_shared::FromStatic;

        if self.rows.is_empty() {
            return 0;
        }
        // Safety: read-only row access behind the populated-singleton check.
        let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
            return 0;
        };
        // The armament's own row is the +0 row of its affinity, which is what the plan builds.
        let Some(reinforce_type) = repo
            .rows::<EquipParamWeapon>()
            .find(|(id, _)| *id == weapon_param_id)
            .map(|(_, row)| i64::from(row.reinforce_type_id()))
        else {
            return 0;
        };
        (0..=requested)
            .rev()
            .find(|level| {
                u32::try_from(reinforce_type + i64::from(*level))
                    .is_ok_and(|id| self.rows.contains(&id))
            })
            .unwrap_or(0)
    }
}
