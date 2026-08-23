//! The subset of a planner build document this crate reads.
//!
//! The real payload carries far more (computed AR, absorption, resistances, view
//! state, tags, author). Everything not needed to grant and equip items is
//! ignored rather than modelled, so an upstream field addition cannot fail the
//! parse.

use serde::Deserialize;
use std::collections::BTreeMap;

/// A build as served by `GET /inventories/{id}`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BuildDoc {
    /// The share id, i.e. the `?b=` value.
    #[serde(default)]
    pub id: String,
    /// The build's display name.
    #[serde(default)]
    pub name: String,
    /// Starting class, when the author picked one.
    #[serde(default, rename = "characterClass")]
    pub character_class: Option<String>,
    /// Default armament upgrade level, applied to any weapon slot that does not
    /// override it.
    #[serde(default, rename = "weaponUpgrade")]
    pub weapon_upgrade: u16,
    /// Requested level and attributes (`rl`, `vig`, `str`, ...).
    #[serde(default)]
    pub stats: BTreeMap<String, i64>,
    /// Armaments. Named `inventory` upstream.
    #[serde(default)]
    pub inventory: SlotList,
    /// Sorceries and incantations, in memorisation order.
    #[serde(default)]
    pub spells: SlotList,
    /// Talismans.
    #[serde(default)]
    pub talismans: SlotList,
    /// Armour, keyed by body part (`head`, `body`, `arms`, `legs`).
    #[serde(default)]
    pub protectors: BTreeMap<String, SlotList>,
    /// Consumables, flask and physick configuration.
    #[serde(default)]
    pub items: Items,
    /// Whether the build two-hands its main armament.
    #[serde(default, rename = "is2h")]
    pub two_handing: bool,
    /// Equipped great rune, when the author chose one.
    #[serde(default, rename = "greatRune")]
    pub great_rune: Option<String>,
}

/// A category's slot list.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct SlotList {
    /// The slots themselves.
    #[serde(default)]
    pub slots: Vec<Slot>,
}

/// One item in a build.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Slot {
    /// The item's display name -- the only identifier the payload carries.
    #[serde(default)]
    pub name: String,
    /// Position within its list.
    #[serde(default)]
    pub order: i64,
    /// Per-slot upgrade level overriding [`BuildDoc::weapon_upgrade`].
    #[serde(default)]
    pub upgrade: Option<u16>,
    /// Affinity, e.g. `Occult`. Absent means `Standard`.
    #[serde(default)]
    pub infusion: Option<String>,
    /// Ash of war on this armament, if any.
    #[serde(default, rename = "weaponArt")]
    pub weapon_art: Option<String>,
    /// Present when the slot is actually equipped, giving the equip position.
    #[serde(default, rename = "equipIndex")]
    pub equip_index: Option<u32>,
}

impl Slot {
    /// Whether this slot is equipped rather than merely carried.
    pub fn is_equipped(&self) -> bool {
        self.equip_index.is_some()
    }
}

/// Consumables and flasks.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Items {
    /// Consumable/crafting items.
    #[serde(default)]
    pub tools: SlotList,
    /// Physick tears; entries are `null` when a slot is left empty.
    #[serde(default, rename = "crystalTears")]
    pub crystal_tears: Vec<Option<String>>,
    /// Flask allocation: `level` (Sacred Tears), `total`, and the crimson/cerulean split.
    #[serde(default)]
    pub flasks: Flasks,
}

/// Flask allocation.
#[derive(Debug, Clone, Copy, Default, Deserialize)]
pub struct Flasks {
    /// Flask potency, i.e. Sacred Tears drunk.
    #[serde(default)]
    pub level: u32,
    /// Total flasks, i.e. Golden Seeds spent.
    #[serde(default)]
    pub total: u32,
    /// Flasks allocated to Crimson Tears.
    #[serde(default)]
    pub crimson: u32,
    /// Flasks allocated to Cerulean Tears.
    #[serde(default)]
    pub cerulean: u32,
}

/// Parse a planner build document.
///
/// # Errors
///
/// Returns the underlying `serde_json` error when the payload is not a build
/// document.
pub fn parse(json: &str) -> Result<BuildDoc, serde_json::Error> {
    serde_json::from_str(json)
}
