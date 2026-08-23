//! Turning a parsed build into concrete item grants.
//!
//! # The grant record
//!
//! The layout below is not invented here: it is the record the community's
//! long-standing `ItemGib` routine consumes, recovered from the planner's own
//! Cheat Engine exporter (`yF`), which emits per item
//! `dd <id+upgrade> <quantity> <reinforceLv:16><0:16> <weaponSkill>` and
//! terminates the array with `00000000 00000000 00000000 FFFFFFFF`.
//!
//! Three details are easy to get wrong and are pinned by tests:
//!
//! 1. The affinity is an **offset added into the item id** (`Occult` = +1200),
//!    not an index and not a separate field.
//! 2. The upgrade level is a **separate 16-bit field**, never folded into the id.
//! 3. `weaponSkill` is built from the ash of war's **param id**, not its
//!    category-tagged id -- ashes carry nibble 2, and including it corrupts the
//!    value.

use crate::catalog::{Catalog, Entry, Kind};
use crate::model::{BuildDoc, Slot};

/// Sentinel meaning "leave this armament's default skill alone".
pub const NO_SKILL: u32 = 0xFFFF_FFFF;

/// High bit set on an ash-of-war param id to form a `weaponSkill` value.
const SKILL_FLAG: u32 = 0x8000_0000;

/// Affinity name -> the offset it adds to an armament's item id.
const INFUSIONS: &[(&str, u32)] = &[
    ("Standard", 0),
    ("Heavy", 100),
    ("Keen", 200),
    ("Quality", 300),
    ("Fire", 400),
    ("Flame Art", 500),
    ("Lightning", 600),
    ("Sacred", 700),
    ("Magic", 800),
    ("Cold", 900),
    ("Poison", 1000),
    ("Blood", 1100),
    ("Occult", 1200),
];

/// Somber-armament upgrade remap, replicated verbatim from the planner.
///
/// Applied only when a slot carries an explicit upgrade *and* the armament takes
/// Somber Smithing Stones. The planner's intent here is not documented and the
/// mapping is reproduced rather than reinterpreted; [`somber_remap`] returns
/// `None` past the table's end so an out-of-range level is reported instead of
/// silently wrapping.
const SOMBER_REMAP: &[u16] = &[0, 1, 4, 6, 9, 11, 14, 16, 19, 21, 24, 25];

/// Flasks the exporter deliberately never grants -- the character already owns
/// them and duplicating them corrupts the flask UI.
const NEVER_GRANT: &[&str] = &["Flask of Crimson Tears", "Flask of Cerulean Tears"];

/// One item to hand to the player.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Grant {
    /// Category-tagged item id with any affinity offset already applied.
    pub item_id: u32,
    /// How many to give.
    pub quantity: u32,
    /// Upgrade level, as its own field.
    pub reinforce_lv: u16,
    /// Ash of war (`SKILL_FLAG | param_id`) or [`NO_SKILL`].
    pub weapon_skill: u32,
    /// What this grant is, for logs and for the user.
    pub label: String,
}

impl Grant {
    /// Encode as the 16-byte `ItemGib` record.
    pub fn to_record(&self) -> [u8; 16] {
        let mut out = [0u8; 16];
        out[0..4].copy_from_slice(&self.item_id.to_le_bytes());
        out[4..8].copy_from_slice(&self.quantity.to_le_bytes());
        out[8..10].copy_from_slice(&self.reinforce_lv.to_le_bytes());
        // out[10..12] stays zero: the exporter emits an explicit zero half-word.
        out[12..16].copy_from_slice(&self.weapon_skill.to_le_bytes());
        out
    }
}

/// An item the catalogue could not resolve.
///
/// Surfaced rather than dropped: a silently missing weapon is indistinguishable
/// from a broken importer, and this is exactly how the `Miséricorde` accent bug
/// was found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unresolved {
    /// Which catalogue was searched.
    pub kind: Kind,
    /// The name as the build spelled it.
    pub name: String,
}

/// Everything needed to reproduce a build in game.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Plan {
    /// Items to grant, in build order.
    pub grants: Vec<Grant>,
    /// Spell param ids to memorise, in slot order.
    pub equip_spells: Vec<u32>,
    /// Names that resolved to nothing.
    pub unresolved: Vec<Unresolved>,
}

impl Plan {
    /// Whether every referenced item resolved.
    pub fn is_complete(&self) -> bool {
        self.unresolved.is_empty()
    }
}

/// The offset an affinity adds to an armament id, or `None` if unrecognised.
pub fn infusion_offset(infusion: Option<&str>) -> Option<u32> {
    let wanted = infusion.unwrap_or("Standard");
    INFUSIONS
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case(wanted))
        .map(|(_, offset)| *offset)
}

/// Remap a requested upgrade level for a somber armament.
pub fn somber_remap(level: u16) -> Option<u16> {
    SOMBER_REMAP.get(usize::from(level)).copied()
}

impl Entry {
    /// The bare param row id, with the category nibble stripped.
    pub fn param_id(self) -> u32 {
        self.full_item_id & 0x0FFF_FFFF
    }
}

/// Compute the grants and spell list for `doc` using `catalog`.
pub fn plan(doc: &BuildDoc, catalog: &dyn Catalog) -> Plan {
    let mut out = Plan::default();

    for slot in &doc.inventory.slots {
        plan_weapon(doc, catalog, slot, &mut out);
    }
    for slot in &doc.talismans.slots {
        push_simple(catalog, Kind::Talisman, slot, 1, &mut out);
    }
    for slot in &doc.spells.slots {
        match catalog.lookup(Kind::Spell, &slot.name) {
            Some(found) => {
                out.equip_spells.push(found.param_id());
                out.grants.push(Grant {
                    item_id: found.full_item_id,
                    quantity: 1,
                    reinforce_lv: 0,
                    weapon_skill: NO_SKILL,
                    label: slot.name.clone(),
                });
            }
            None => out.unresolved.push(Unresolved {
                kind: Kind::Spell,
                name: slot.name.clone(),
            }),
        }
    }
    // BTreeMap iteration keeps body parts in a stable order across runs.
    for part in doc.protectors.values() {
        for slot in &part.slots {
            push_simple(catalog, Kind::Protector, slot, 1, &mut out);
        }
    }
    for slot in &doc.items.tools.slots {
        if NEVER_GRANT
            .iter()
            .any(|skip| skip.eq_ignore_ascii_case(&slot.name))
        {
            continue;
        }
        let quantity = catalog
            .lookup(Kind::Tool, &slot.name)
            .and_then(|found| found.max_stored)
            .unwrap_or(1);
        push_simple(catalog, Kind::Tool, slot, quantity, &mut out);
    }
    // The great rune is a goods item like any other and has to be in the inventory before it
    // can be equipped; nothing else in the payload implies it.
    if let Some(rune) = doc.great_rune.as_deref() {
        let slot = Slot {
            name: rune.to_owned(),
            ..Slot::default()
        };
        push_simple(catalog, Kind::GreatRune, &slot, 1, &mut out);
    }
    for tear in doc.items.crystal_tears.iter().flatten() {
        let slot = Slot {
            name: tear.clone(),
            ..Slot::default()
        };
        push_simple(catalog, Kind::Tool, &slot, 1, &mut out);
    }

    out
}

/// Grant an armament, applying affinity, upgrade level and ash of war.
fn plan_weapon(doc: &BuildDoc, catalog: &dyn Catalog, slot: &Slot, out: &mut Plan) {
    let Some(found) = catalog.lookup(Kind::Weapon, &slot.name) else {
        out.unresolved.push(Unresolved {
            kind: Kind::Weapon,
            name: slot.name.clone(),
        });
        return;
    };
    let Some(offset) = infusion_offset(slot.infusion.as_deref()) else {
        out.unresolved.push(Unresolved {
            kind: Kind::Weapon,
            name: format!("{} (unknown affinity {:?})", slot.name, slot.infusion),
        });
        return;
    };

    let requested = slot.upgrade.unwrap_or(doc.weapon_upgrade);
    let reinforce_lv = if slot.upgrade.is_some() && found.somber {
        match somber_remap(requested) {
            Some(mapped) => mapped,
            None => {
                out.unresolved.push(Unresolved {
                    kind: Kind::Weapon,
                    name: format!("{} (somber upgrade {requested} out of range)", slot.name),
                });
                return;
            }
        }
    } else {
        requested
    };

    let mut weapon_skill = NO_SKILL;
    if let Some(art) = slot.weapon_art.as_deref()
        && !art.eq_ignore_ascii_case("No Skill")
    {
        match catalog.lookup(Kind::AshOfWar, art) {
            Some(ash) => weapon_skill = SKILL_FLAG | ash.param_id(),
            None => {
                out.unresolved.push(Unresolved {
                    kind: Kind::AshOfWar,
                    name: art.to_owned(),
                });
            }
        }
    }

    out.grants.push(Grant {
        item_id: found.full_item_id + offset,
        quantity: 1,
        reinforce_lv,
        weapon_skill,
        label: slot.name.clone(),
    });
}

/// Grant a non-armament item, which carries no affinity, upgrade or skill.
fn push_simple(catalog: &dyn Catalog, kind: Kind, slot: &Slot, quantity: u32, out: &mut Plan) {
    match catalog.lookup(kind, &slot.name) {
        Some(found) => out.grants.push(Grant {
            item_id: found.full_item_id,
            quantity,
            reinforce_lv: 0,
            weapon_skill: NO_SKILL,
            label: slot.name.clone(),
        }),
        None => out.unresolved.push(Unresolved {
            kind,
            name: slot.name.clone(),
        }),
    }
}
