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
}

/// An in-memory catalog keyed by `(kind, folded name)`.
#[derive(Debug, Clone, Default)]
pub struct MapCatalog {
    entries: HashMap<(Kind, String), Entry>,
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
    pub fn insert(&mut self, kind: Kind, name: &str, entry: Entry) {
        self.entries.insert((kind, fold(name)), entry);
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
}

/// Build a simple entry with no stack limit and standard upgrade material.
pub fn entry(full_item_id: u32) -> Entry {
    Entry {
        full_item_id,
        max_stored: None,
        somber: false,
    }
}
