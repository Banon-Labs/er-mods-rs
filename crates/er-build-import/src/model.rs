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
    /// The named loadout sets, one list per equip category. Absent on builds
    /// authored before the planner grew sets.
    #[serde(default)]
    pub sets: Sets,
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
    /// The equip position this slot holds **in the active set**.
    ///
    /// A cache of `equipSet[active]`, not an independent fact -- see
    /// [`Slot::equip_index_in_set`], which is what the importer reads.
    #[serde(default, rename = "equipIndex")]
    pub equip_index: Option<u32>,
    /// The equip position this slot holds in *each* set, indexed by set.
    ///
    /// Holes are sets the item is not equipped in, and the array is only as long
    /// as the highest set that uses the item. Absent on pre-sets builds.
    #[serde(default, rename = "equipSet")]
    pub equip_set: Option<Vec<Option<u32>>>,
}

impl Slot {
    /// The equip position this slot holds in set `set_index`, if any.
    ///
    /// `equipSet` is authoritative when present; [`Slot::equip_index`] is only
    /// its active-set entry, kept in step by the planner. When `equipSet` is
    /// absent the build predates sets, which is the same thing as being equipped
    /// in set 0 and nowhere else -- exactly what the planner's own migration
    /// (`equipSet = [equipIndex]`) makes of such a row the moment sets appear.
    ///
    /// ```
    /// use er_build_import::model;
    /// let doc = model::parse(
    ///     r#"{"inventory":{"slots":[{"name":"Shamshir","equipSet":[null,2]}]}}"#,
    /// )
    /// .expect("parses");
    /// let slot = &doc.inventory.slots[0];
    /// assert_eq!(slot.equip_index_in_set(0), None);
    /// assert_eq!(slot.equip_index_in_set(1), Some(2));
    /// ```
    pub fn equip_index_in_set(&self, set_index: usize) -> Option<u32> {
        match self.equip_set.as_ref() {
            Some(sets) => sets.get(set_index).copied().flatten(),
            None if set_index == 0 => self.equip_index,
            None => None,
        }
    }
}

/// The loadout sets a build carries, one independent list per equip category.
///
/// The planner lets an author keep several loadouts side by side -- a bow set, a
/// two-handing set -- and exactly one per category is active. Each category
/// switches independently, so there is no single "active set" for a build.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Sets {
    /// Armament sets.
    #[serde(default)]
    pub weapons: Vec<EquipSet>,
    /// Talisman sets.
    #[serde(default)]
    pub talismans: Vec<EquipSet>,
    /// Armour sets, shared by all four body parts.
    #[serde(default)]
    pub protectors: Vec<EquipSet>,
}

impl Sets {
    /// The active armament set.
    pub fn active_weapons(&self) -> usize {
        active_index(&self.weapons)
    }

    /// The active talisman set.
    pub fn active_talismans(&self) -> usize {
        active_index(&self.talismans)
    }

    /// The active armour set.
    pub fn active_protectors(&self) -> usize {
        active_index(&self.protectors)
    }
}

/// One named loadout set.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct EquipSet {
    /// The author's label for it, e.g. `Bows`.
    #[serde(default)]
    pub name: String,
    /// Whether this is the set the planner is currently showing.
    #[serde(default)]
    pub active: bool,
}

/// Which set of `list` is active, defaulting to the first.
///
/// A build with no `sets` key at all, or one where nothing carries `active`,
/// still has to import *something*; set 0 is what the planner renders in both
/// cases, and it is the set a pre-sets `equipIndex` belongs to.
fn active_index(list: &[EquipSet]) -> usize {
    list.iter().position(|set| set.active).unwrap_or(0)
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
