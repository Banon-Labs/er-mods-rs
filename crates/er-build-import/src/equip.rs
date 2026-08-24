//! What the character should be *wearing*, as opposed to merely carrying.
//!
//! Granting items is only half an import: a build is not reproduced until the
//! right armaments are in the right hands, the right spells are memorised in the
//! right order, and the consumables sit in the quickbar positions the author
//! chose. This module turns a [`BuildDoc`] into that target state, and -- just as
//! importantly -- refuses to ask for a state the game cannot hold.
//!
//! # Where the equip data lives in the payload
//!
//! | target | source | encoding |
//! |---|---|---|
//! | armaments | `inventory.slots[].equipIndex` | 0..6, right hand then left |
//! | armour | `protectors.<part>.slots[].equipIndex` | present = this is the worn one |
//! | talismans | `talismans.slots[].equipIndex` | 0..4 |
//! | spells | `spells.slots[].order` | memorisation order; there is no `equipIndex` |
//! | quickbar | `items.tools.slots[].equipIndex` | `< 10` -> quickbar position |
//! | pouch | `items.tools.slots[].equipIndex` | `10..16` -> up, right, left, down, 1, 2 |
//! | physick | `items.crystalTears` | two entries, `null` when empty |
//! | great rune | `greatRune` | name |
//! | two-handing | `is2h` | bool |
//!
//! The quickbar/pouch split is not guesswork: the planner renders a tool's badge
//! with `equipIndex < QUICKBAR(10)` -> quickbar `equipIndex + 1`, and otherwise
//! `equipIndex - 10` selecting up/right/left/down/1/2 out of `POUCH(16)` total
//! positions.

use crate::catalog::{Catalog, Kind};
use crate::model::BuildDoc;

/// Armament slots: three right hand, three left.
pub const ARMAMENT_SLOTS: usize = 6;
/// Talisman slots at full Talisman Pouch count.
pub const TALISMAN_SLOTS: usize = 4;
/// Quickbar positions.
pub const QUICKBAR_SLOTS: usize = 10;
/// Pouch positions: up, right, left, down, then two more.
pub const POUCH_SLOTS: usize = 6;
/// Physick tear slots.
pub const PHYSICK_SLOTS: usize = 2;

/// An item selected for a specific equip position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EquipRef {
    /// Category-tagged item id, affinity offset included for armaments.
    pub item_id: u32,
    /// Bare param row id, which is what most native equip calls take.
    pub param_id: u32,
    /// Display name, for logs.
    pub name: String,
}

/// Why a requested equip could not be honoured.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rejected {
    /// The item that was dropped.
    pub name: String,
    /// Plain-language reason.
    pub reason: String,
}

/// How much the character can actually hold.
///
/// Defaults describe a character that has received the setup items an import
/// grants alongside the build: three Talisman Pouches (1 base + 3 = 4 slots) and
/// eight Memory Stones (1 base + 8 = 9 memorisation slots). Callers that know
/// better -- because they read the live character -- should say so rather than
/// let the import overrun and have the game silently drop spells.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Capacity {
    /// Talisman slots available.
    pub talismans: usize,
    /// Spell memorisation slots available.
    pub memory_slots: usize,
}

impl Default for Capacity {
    fn default() -> Self {
        Self {
            talismans: TALISMAN_SLOTS,
            memory_slots: 9,
        }
    }
}

/// The target equipment state.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EquipPlan {
    /// Armaments by slot; `None` means "leave empty".
    pub armaments: Vec<Option<EquipRef>>,
    /// Head armour.
    pub head: Option<EquipRef>,
    /// Body armour.
    pub body: Option<EquipRef>,
    /// Arm armour.
    pub arms: Option<EquipRef>,
    /// Leg armour.
    pub legs: Option<EquipRef>,
    /// Talismans by slot.
    pub talismans: Vec<Option<EquipRef>>,
    /// Spells in memorisation order.
    pub spells: Vec<EquipRef>,
    /// Quickbar contents by position.
    pub quickbar: Vec<Option<EquipRef>>,
    /// Pouch contents by position.
    pub pouch: Vec<Option<EquipRef>>,
    /// Physick tears.
    pub physick: Vec<Option<EquipRef>>,
    /// Great rune, if any.
    pub great_rune: Option<EquipRef>,
    /// Whether to two-hand.
    pub two_handing: bool,
    /// Everything that could not be placed, with a reason.
    pub rejected: Vec<Rejected>,
}

impl EquipPlan {
    /// Whether every requested equip was placed.
    pub fn is_complete(&self) -> bool {
        self.rejected.is_empty()
    }

    /// Total number of positions this plan will actually write.
    pub fn occupied(&self) -> usize {
        let slots = [
            &self.armaments,
            &self.talismans,
            &self.quickbar,
            &self.pouch,
            &self.physick,
        ];
        slots
            .iter()
            .map(|s| s.iter().filter(|e| e.is_some()).count())
            .sum::<usize>()
            + [&self.head, &self.body, &self.arms, &self.legs]
                .iter()
                .filter(|e| e.is_some())
                .count()
            + self.spells.len()
            + usize::from(self.great_rune.is_some())
    }
}

/// Compute the target equipment state for `doc`.
pub fn equip_plan(doc: &BuildDoc, catalog: &dyn Catalog, capacity: Capacity) -> EquipPlan {
    let mut out = EquipPlan {
        armaments: vec![None; ARMAMENT_SLOTS],
        talismans: vec![None; capacity.talismans.min(TALISMAN_SLOTS)],
        quickbar: vec![None; QUICKBAR_SLOTS],
        pouch: vec![None; POUCH_SLOTS],
        physick: vec![None; PHYSICK_SLOTS],
        two_handing: doc.two_handing,
        ..EquipPlan::default()
    };

    for slot in &doc.inventory.slots {
        let Some(index) = slot.equip_index else {
            continue;
        };
        let Some(found) = catalog.lookup(Kind::Weapon, &slot.name) else {
            out.rejected.push(Rejected {
                name: slot.name.clone(),
                reason: "armament not in catalog".into(),
            });
            continue;
        };
        let offset = crate::plan::infusion_offset(slot.infusion.as_deref()).unwrap_or(0);
        let item = EquipRef {
            item_id: found.full_item_id + offset,
            param_id: found.param_id() + offset,
            name: slot.name.clone(),
        };
        place(
            &mut out.armaments,
            index as usize,
            item,
            "armament slot",
            &mut out.rejected,
        );
    }

    for (part, list) in &doc.protectors {
        for slot in &list.slots {
            if slot.equip_index.is_none() {
                continue;
            }
            let Some(found) = catalog.lookup(Kind::Protector, &slot.name) else {
                out.rejected.push(Rejected {
                    name: slot.name.clone(),
                    reason: format!("{part} armour not in catalog"),
                });
                continue;
            };
            let item = EquipRef {
                item_id: found.full_item_id,
                param_id: found.param_id(),
                name: slot.name.clone(),
            };
            let target = match part.as_str() {
                "head" => &mut out.head,
                "body" => &mut out.body,
                "arms" => &mut out.arms,
                "legs" => &mut out.legs,
                other => {
                    out.rejected.push(Rejected {
                        name: slot.name.clone(),
                        reason: format!("unknown armour part {other:?}"),
                    });
                    continue;
                }
            };
            if target.is_some() {
                out.rejected.push(Rejected {
                    name: slot.name.clone(),
                    reason: format!("{part} already has an equipped piece"),
                });
                continue;
            }
            *target = Some(item);
        }
    }

    for slot in &doc.talismans.slots {
        let Some(index) = slot.equip_index else {
            continue;
        };
        let Some(found) = catalog.lookup(Kind::Talisman, &slot.name) else {
            out.rejected.push(Rejected {
                name: slot.name.clone(),
                reason: "talisman not in catalog".into(),
            });
            continue;
        };
        let item = EquipRef {
            item_id: found.full_item_id,
            param_id: found.param_id(),
            name: slot.name.clone(),
        };
        place(
            &mut out.talismans,
            index as usize,
            item,
            "talisman slot",
            &mut out.rejected,
        );
    }

    // Spells carry no equipIndex; memorisation follows `order`, and the memory
    // slot count -- not the build -- decides how many actually fit.
    let mut spells: Vec<_> = doc.spells.slots.iter().collect();
    spells.sort_by_key(|slot| slot.order);
    for slot in spells {
        let Some(found) = catalog.lookup(Kind::Spell, &slot.name) else {
            out.rejected.push(Rejected {
                name: slot.name.clone(),
                reason: "spell not in catalog".into(),
            });
            continue;
        };
        if out.spells.len() >= capacity.memory_slots {
            out.rejected.push(Rejected {
                name: slot.name.clone(),
                reason: format!("only {} memory slots available", capacity.memory_slots),
            });
            continue;
        }
        out.spells.push(EquipRef {
            item_id: found.full_item_id,
            param_id: found.param_id(),
            name: slot.name.clone(),
        });
    }

    for slot in &doc.items.tools.slots {
        let Some(index) = slot.equip_index else {
            continue;
        };
        let Some(found) = catalog.lookup(Kind::Tool, &slot.name) else {
            out.rejected.push(Rejected {
                name: slot.name.clone(),
                reason: "tool not in catalog".into(),
            });
            continue;
        };
        let item = EquipRef {
            item_id: found.full_item_id,
            param_id: found.param_id(),
            name: slot.name.clone(),
        };
        let index = index as usize;
        if index < QUICKBAR_SLOTS {
            place(
                &mut out.quickbar,
                index,
                item,
                "quickbar position",
                &mut out.rejected,
            );
        } else {
            place(
                &mut out.pouch,
                index - QUICKBAR_SLOTS,
                item,
                "pouch position",
                &mut out.rejected,
            );
        }
    }

    for (index, tear) in doc.items.crystal_tears.iter().enumerate() {
        let Some(tear) = tear else { continue };
        let Some(found) = catalog.lookup(Kind::Tool, tear) else {
            out.rejected.push(Rejected {
                name: tear.clone(),
                reason: "physick tear not in catalog".into(),
            });
            continue;
        };
        let item = EquipRef {
            item_id: found.full_item_id,
            param_id: found.param_id(),
            name: tear.clone(),
        };
        place(
            &mut out.physick,
            index,
            item,
            "physick slot",
            &mut out.rejected,
        );
    }

    if let Some(rune) = doc.great_rune.as_deref() {
        match catalog.lookup(Kind::GreatRune, rune) {
            Some(found) => {
                out.great_rune = Some(EquipRef {
                    item_id: found.full_item_id,
                    param_id: found.param_id(),
                    name: rune.to_owned(),
                });
            }
            None => out.rejected.push(Rejected {
                name: rune.to_owned(),
                reason: "great rune not in catalog".into(),
            }),
        }
    }

    out
}

/// Put `item` at `index`, rejecting rather than overwriting or growing.
fn place(
    slots: &mut [Option<EquipRef>],
    index: usize,
    item: EquipRef,
    what: &str,
    rejected: &mut Vec<Rejected>,
) {
    match slots.get_mut(index) {
        None => rejected.push(Rejected {
            name: item.name,
            reason: format!("{what} {index} is out of range ({} available)", slots.len()),
        }),
        Some(existing) if existing.is_some() => rejected.push(Rejected {
            name: item.name,
            reason: format!("{what} {index} already taken"),
        }),
        Some(empty) => *empty = Some(item),
    }
}
