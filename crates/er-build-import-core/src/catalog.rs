//! The item catalog: name -> item id, scoped by category.
//!
//! # Why lookup MUST be category-scoped
//!
//! Elden Ring reuses item names across categories, so a flat name->id map grants
//! the wrong item. Measured against the planner's own database (2063 entries),
//! seven names are ambiguous:
//!
//! | name | spell | ash of war | other |
//! |---|---|---|---|
//! | `Golden Vow` | 6600 | 60300 | consumable 2003170 |
//! | `Beast Claw` | 6820 | -- | weapon 68500000 |
//! | `Glintstone Pebble` | 4000 | 20300 | -- |
//! | `Glintblade Phalanx` | 4300 | 20000 | -- |
//! | `Carian Greatsword` | 4430 | 21900 | -- |
//! | `Carian Retaliation` | 4640 | 30500 | -- |
//! | `Thops's Barrier` | 4630 | 31000 | -- |
//!
//! A build that lists `Golden Vow` under `spells` and a weapon whose `weaponArt`
//! is `Golden Vow` means two different item ids from one string, so [`Catalog`]
//! takes the [`Kind`] as part of the key rather than as a hint.

use std::collections::HashMap;

use crate::name::fold;

/// Which of the planner's seven item databases a name should be looked up in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Kind {
    /// Armaments, including staves, seals and shields.
    Weapon,
    /// Head / body / arms / legs armour.
    Protector,
    /// Talismans.
    Talisman,
    /// Sorceries and incantations.
    Spell,
    /// Consumables, crafting items and pots.
    Tool,
    /// Ashes of war, referenced by a weapon slot's `weaponArt`.
    AshOfWar,
    /// Arrows and bolts.
    Ammo,
    /// Great runes.
    ///
    /// A separate kind rather than a subset of [`Kind::Tool`] because the goods table contains
    /// MORE THAN ONE row named e.g. "Godrick's Great Rune", and only rows 191..=196 are
    /// accepted by the game: `GetGreatruneEnumByGoodsId` switches on exactly those six and
    /// returns "none" for anything else. A plain name lookup picked row 8148 and the rune
    /// silently failed to equip.
    GreatRune,
}

impl Kind {
    /// Human-readable label, used in unresolved-item reports.
    pub fn label(self) -> &'static str {
        match self {
            Kind::Weapon => "weapon",
            Kind::Protector => "armour",
            Kind::Talisman => "talisman",
            Kind::Spell => "spell",
            Kind::Tool => "tool",
            Kind::AshOfWar => "ash of war",
            Kind::Ammo => "ammo",
            Kind::GreatRune => "great rune",
        }
    }
}

/// One catalog row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Entry {
    /// The category-tagged item id: `(category_nibble << 28) | param_id`.
    ///
    /// Verified across all 2063 catalog entries: the low 28 bits are exactly the
    /// param row id, and the nibble is 0 for weapons/ammo, 1 for protectors,
    /// 2 for talismans and ashes of war, 4 for goods (spells and tools).
    pub full_item_id: u32,
    /// How many the game lets you hold; `None` means "just one".
    pub max_stored: Option<u32>,
    /// Whether the armament upgrades with Somber Smithing Stones.
    pub somber: bool,
}

/// Resolves an item name, within a category, to its id.
///
/// A trait rather than a concrete table so the id source is swappable: an
/// offline table for tests, and (later) a resolver that reads the game's own
/// param/FMG data at runtime instead of shipping a copy of someone else's
/// database.
pub trait Catalog {
    /// Look up `name` (unfolded; the implementation folds it) within `kind`.
    fn lookup(&self, kind: Kind, name: &str) -> Option<Entry>;

    /// Ids OTHER than [`Self::lookup`]'s answer that the same name also resolves to.
    ///
    /// Elden Ring gives each upgrade level of a flask its own goods row, and every row carries the
    /// SAME name -- so "Flask of Crimson Tears" is a dozen ids, not one. A caller that treats
    /// `lookup`'s single answer as "the" id will conclude the player does not hold an item they
    /// are visibly carrying, because they hold a different row of it.
    ///
    /// Defaults to empty so an offline test table needs no collision machinery.
    fn alternates(&self, _kind: Kind, _name: &str) -> Vec<u32> {
        Vec::new()
    }
}

/// An in-memory catalog keyed by `(kind, folded name)`.
///
/// # Why a name can map to more than one id
///
/// The catalog is built by enumerating the game's param rows, asking each for its NAME, and
/// inverting. Names are not unique: several rows can share one, and a plain `insert` therefore
/// let the last row enumerated silently win. That is not a cosmetic loss -- the importer asks
/// "does the player already hold this?" about the id it resolved, so picking the other row means
/// asking about an item nobody has, concluding the player has none, and granting a duplicate. It
/// is exactly how importing a build produced a SECOND Flask of Wondrous Physick beside the one
/// already in the inventory.
///
/// So every id is kept. `lookup` still answers with one, because callers want one, but the
/// alternates are available to a caller that can ask a better question -- see
/// [`MapCatalog::alternates`], which the grant path uses to check every candidate for an already
/// held copy before it adds anything.
#[derive(Debug, Clone, Default)]
pub struct MapCatalog {
    entries: HashMap<(Kind, String), Entry>,
    /// Every entry that lost a name collision, in insertion order under its key.
    alternates: HashMap<(Kind, String), Vec<Entry>>,
}

impl MapCatalog {
    /// An empty catalog.
    pub fn new() -> Self {
        Self::default()
    }

    /// Add one row. Returns `self` so tables can be built in one expression.
    pub fn with(mut self, kind: Kind, name: &str, entry: Entry) -> Self {
        self.insert(kind, name, entry);
        self
    }

    /// Add one row.
    ///
    /// A second row under a name already present does NOT replace the first: the first stays the
    /// primary answer and the newcomer joins [`Self::alternates`]. Keeping the FIRST is what makes
    /// the resolution stable -- param enumeration order is the game's, not ours, and a catalog
    /// whose answers shuffle between patches is worse than one that answers consistently.
    pub fn insert(&mut self, kind: Kind, name: &str, entry: Entry) {
        let key = (kind, fold(name));
        match self.entries.get(&key) {
            Some(existing) if *existing == entry => {}
            Some(_) => self.alternates.entry(key).or_default().push(entry),
            None => {
                self.entries.insert(key, entry);
            }
        }
    }

    /// Other rows that share `name` within `kind`, in the order the game enumerated them.
    ///
    /// Empty for the overwhelming majority of items; non-empty is the signal that a bare id
    /// cannot identify what the player is holding.
    pub fn alternates(&self, kind: Kind, name: &str) -> &[Entry] {
        self.alternates
            .get(&(kind, fold(name)))
            .map_or(&[], Vec::as_slice)
    }

    /// Every `(kind, folded name)` that resolved to more than one id, with all of its ids.
    ///
    /// Reported at import time rather than discovered from a duplicated item later.
    pub fn collisions(&self) -> Vec<(Kind, String, Vec<u32>)> {
        let mut found: Vec<(Kind, String, Vec<u32>)> = self
            .alternates
            .iter()
            .map(|((kind, name), extra)| {
                let mut ids: Vec<u32> = self
                    .entries
                    .get(&(*kind, name.clone()))
                    .map(|primary| primary.full_item_id)
                    .into_iter()
                    .collect();
                ids.extend(extra.iter().map(|entry| entry.full_item_id));
                (*kind, name.clone(), ids)
            })
            .collect();
        found.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
        found
    }

    /// Number of rows.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the catalog holds no rows.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

impl Catalog for MapCatalog {
    fn lookup(&self, kind: Kind, name: &str) -> Option<Entry> {
        self.entries.get(&(kind, fold(name))).copied()
    }

    fn alternates(&self, kind: Kind, name: &str) -> Vec<u32> {
        MapCatalog::alternates(self, kind, name)
            .iter()
            .map(|entry| entry.full_item_id)
            .collect()
    }
}

/// Build a simple entry with no stack limit and standard upgrade material.
pub fn entry(full_item_id: u32) -> Entry {
    Entry {
        full_item_id,
        max_stored: None,
        somber: false,
    }
}
