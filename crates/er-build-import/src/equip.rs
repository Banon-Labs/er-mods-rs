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
//! | armaments | `inventory.slots[].equipSet` | 0..6, right hand then left |
//! | armour | `protectors.<part>.slots[].equipSet` | non-null = this is the worn one |
//! | talismans | `talismans.slots[].equipSet` | 0..4 |
//! | spells | `spells.slots[].order` | memorisation order; there is no equip position |
//! | quickbar | `items.tools.slots[].equipIndex` | `< 10` -> quickbar position |
//! | pouch | `items.tools.slots[].equipIndex` | `10..16` -> up, right, left, down, 1, 2 |
//! | physick | `items.crystalTears` | two entries, `null` when empty |
//! | great rune | `greatRune` | name |
//! | two-handing | `is2h` | bool |
//!
//! The quickbar/pouch split is not guesswork: the planner renders a tool's badge
//! with `equipIndex < QUICKBAR(10)` -> quickbar `equipIndex + 1`, and otherwise
//! `equipIndex - 10` selecting up/right/left/down/1/2 out of `POUCH(16)` total
//! positions. Tools carry no `equipSet` -- the planner never passes a category
//! for them -- so `equipIndex` is the whole story there.
//!
//! # Loadout sets, and why `equipIndex` alone is not enough
//!
//! A build carries several named loadouts per category (`sets.weapons`,
//! `sets.talismans`, `sets.protectors`), one of them flagged `active`. Every
//! equippable row then carries `equipSet`, an array **indexed by set** whose
//! value is the equip position that item holds in that set:
//!
//! ```text
//! {"name": "Nagakiba", "equipSet": [null, null, null, 0]}   // set 3, position 0
//! ```
//!
//! `equipIndex` is a *cache* of `equipSet[active]`, nothing more. The planner
//! writes both together (`setSlotEquipIndex` assigns `equipIndex` and then
//! `equipSet[activeIndex]`), rewrites `equipIndex` from `equipSet` whenever the
//! author switches set, and `splice`s `equipSet` when a set is deleted -- which
//! is what proves the array is positional by set rather than a list of set ids.
//! A row with no `equipSet` at all predates sets; the planner's own migration
//! turns it into `[equipIndex]`, i.e. set 0.
//!
//! Reading `equipSet` at the active index therefore selects the *same* rows that
//! `equipIndex` did. It is not a bug fix on its own -- it is what makes the
//! selection derived rather than inherited, so a build whose active set is not
//! set 0 cannot quietly import the wrong loadout.
//!
//! # Contested positions
//!
//! Real payloads do contradict themselves: several rows can claim one position
//! *within the active set*, because the planner never clears the position off the
//! row that used to hold it. Build `82086df03c4b8e` has three rows on armament
//! position 0 and two on position 4. That is not recoverable from the data, so
//! the tie is broken the way the planner's own equip-slot components break it,
//! and every loser is reported:
//!
//! * armaments, talismans, quickbar and pouch **fold last-wins** --
//!   `slots.filter(equipIndex != null).reduce((acc, s) => (acc[s.equipIndex] = s, acc))`
//!   overwrites, so the row latest in the list is the one the author sees;
//! * armour takes the **first** -- `slots.find(s => s.equipIndex != null)`.
//!
//! Losers land in [`EquipPlan::rejected`] as well as [`EquipPlan::contested`], so
//! a caller that only counts rejections still cannot miss a collision.

use crate::catalog::{Catalog, Kind};
use crate::model::BuildDoc;
use std::collections::BTreeMap;

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

// THE ARMAMENT SLOT MAP, OWNED HERE BECAUSE TWO DIRECTIONS NOW USE IT.
//
// The planner blocks its six armament indices three-per-hand (`equipIndex >= 3 ? row 1 : row 0`),
// while `ChrAsmSlot` INTERLEAVES them (`0 = WeaponLeft1, 1 = WeaponRight1, 2 = WeaponLeft2`, ...).
// The importer needs planner -> slot; the exporter needs slot -> planner. Keeping one table and
// deriving both means the pair cannot drift into a hand swap that only shows up in a round trip.
//
// INFERRED, and the one mapping in the importer not proven from the binary: the planner's first
// block is taken to be the RIGHT hand, because the game's own Status screen lists `R Armament 1..3`
// before `L Armament 1..3` and the planner mirrors that layout. If imported builds come out
// hand-swapped, THIS TABLE is the single line to flip -- and flipping it moves both directions at
// once, which is the whole reason it is here.

/// `ChrAsmSlot` of each planner armament index, in planner order.
pub const ARMAMENT_CHR_ASM_SLOTS: [i32; 6] = [1, 3, 5, 0, 2, 4];

/// Map a planner armament index (0..6) to its `ChrAsmSlot`.
///
/// ```
/// assert_eq!(er_build_import::equip::armament_slot(0), Some(1)); // right hand, first
/// assert_eq!(er_build_import::equip::armament_slot(3), Some(0)); // left hand, first
/// assert_eq!(er_build_import::equip::armament_slot(6), None);
/// ```
pub fn armament_slot(planner_index: u32) -> Option<i32> {
    ARMAMENT_CHR_ASM_SLOTS
        .get(usize::try_from(planner_index).ok()?)
        .copied()
}

/// Map a `ChrAsmSlot` back to the planner armament index that owns it.
///
/// ```
/// assert_eq!(er_build_import::equip::armament_planner_index(1), Some(0));
/// assert_eq!(er_build_import::equip::armament_planner_index(0), Some(3));
/// assert_eq!(er_build_import::equip::armament_planner_index(9), None);
/// ```
pub fn armament_planner_index(slot: i32) -> Option<u32> {
    ARMAMENT_CHR_ASM_SLOTS
        .iter()
        .position(|candidate| *candidate == slot)
        .and_then(|index| u32::try_from(index).ok())
}

/// `ChrAsmSlot::ProtectorHead`; chest, hands and legs follow consecutively.
pub const CHR_ASM_SLOT_PROTECTOR_HEAD: i32 = 12;
/// `ChrAsmSlot::Accessory1`; the other three talisman slots follow consecutively.
pub const CHR_ASM_SLOT_ACCESSORY_1: i32 = 17;
/// The planner's four armour keys, in `ChrAsmSlot` order from [`CHR_ASM_SLOT_PROTECTOR_HEAD`].
/// `ProtectorIndexToChrAsmSlot` is literally `index + ProtectorHead`, in this order.
pub const PROTECTOR_PARTS: [&str; 4] = ["head", "body", "arms", "legs"];

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

/// A position claimed by more than one row of the active set.
///
/// Not an error in the importer: the payload itself says two things at once, and
/// this records which one was believed. The winner is chosen by the same rule the
/// planner's own equip-slot components use, so the import matches what the build's
/// author sees on screen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Contested {
    /// Which position, e.g. `armament slot 0`.
    pub position: String,
    /// The item that was placed there.
    pub winner: String,
    /// The other claimants, in payload order.
    pub losers: Vec<String>,
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
    /// Positions that more than one row of the active set claimed.
    pub contested: Vec<Contested>,
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
///
/// Only the active set of each category is equipped, and a position two rows of
/// that set both claim is resolved -- never silently -- into
/// [`EquipPlan::contested`] as well as [`EquipPlan::rejected`].
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

    let active_weapons = doc.sets.active_weapons();
    let mut armaments = Vec::new();
    for slot in &doc.inventory.slots {
        let Some(index) = slot.equip_index_in_set(active_weapons) else {
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
        armaments.push(Claim {
            order: slot.order,
            index: index as usize,
            item: EquipRef {
                item_id: found.full_item_id + offset,
                param_id: found.param_id() + offset,
                name: slot.name.clone(),
            },
        });
    }
    settle(
        &mut out.armaments,
        armaments,
        |index| format!("armament slot {index}"),
        Contest::LastWins,
        &mut out.rejected,
        &mut out.contested,
    );

    // Armour carries a set membership, not a position: the planner writes a
    // constant `1` for every worn piece and resolves the part by which list the
    // row lives in, so the value is deliberately not read here.
    let active_protectors = doc.sets.active_protectors();
    for (part, list) in &doc.protectors {
        let mut claims = Vec::new();
        for slot in &list.slots {
            if slot.equip_index_in_set(active_protectors).is_none() {
                continue;
            }
            let Some(found) = catalog.lookup(Kind::Protector, &slot.name) else {
                out.rejected.push(Rejected {
                    name: slot.name.clone(),
                    reason: format!("{part} armour not in catalog"),
                });
                continue;
            };
            claims.push(Claim {
                order: slot.order,
                index: 0,
                item: EquipRef {
                    item_id: found.full_item_id,
                    param_id: found.param_id(),
                    name: slot.name.clone(),
                },
            });
        }
        let target = match part.as_str() {
            "head" => &mut out.head,
            "body" => &mut out.body,
            "arms" => &mut out.arms,
            "legs" => &mut out.legs,
            other => {
                for claim in claims {
                    out.rejected.push(Rejected {
                        name: claim.item.name,
                        reason: format!("unknown armour part {other:?}"),
                    });
                }
                continue;
            }
        };
        settle(
            std::slice::from_mut(target),
            claims,
            |_| format!("{part} armour"),
            Contest::FirstWins,
            &mut out.rejected,
            &mut out.contested,
        );
    }

    let active_talismans = doc.sets.active_talismans();
    let mut talismans = Vec::new();
    for slot in &doc.talismans.slots {
        let Some(index) = slot.equip_index_in_set(active_talismans) else {
            continue;
        };
        let Some(found) = catalog.lookup(Kind::Talisman, &slot.name) else {
            out.rejected.push(Rejected {
                name: slot.name.clone(),
                reason: "talisman not in catalog".into(),
            });
            continue;
        };
        talismans.push(Claim {
            order: slot.order,
            index: index as usize,
            item: EquipRef {
                item_id: found.full_item_id,
                param_id: found.param_id(),
                name: slot.name.clone(),
            },
        });
    }
    settle(
        &mut out.talismans,
        talismans,
        |index| format!("talisman slot {index}"),
        Contest::LastWins,
        &mut out.rejected,
        &mut out.contested,
    );

    // Spells carry no equip position; memorisation follows `order`, and the
    // memory slot count -- not the build -- decides how many actually fit.
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

    let mut quickbar = Vec::new();
    let mut pouch = Vec::new();
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
        let (target, index) = match index.checked_sub(QUICKBAR_SLOTS) {
            None => (&mut quickbar, index),
            Some(in_pouch) => (&mut pouch, in_pouch),
        };
        target.push(Claim {
            order: slot.order,
            index,
            item,
        });
    }
    settle(
        &mut out.quickbar,
        quickbar,
        |index| format!("quickbar position {index}"),
        Contest::LastWins,
        &mut out.rejected,
        &mut out.contested,
    );
    settle(
        &mut out.pouch,
        pouch,
        |index| format!("pouch position {index}"),
        Contest::LastWins,
        &mut out.rejected,
        &mut out.contested,
    );

    let mut physick = Vec::new();
    for (index, tear) in doc.items.crystal_tears.iter().enumerate() {
        let Some(tear) = tear else { continue };
        let Some(found) = catalog.lookup(Kind::Tool, tear) else {
            out.rejected.push(Rejected {
                name: tear.clone(),
                reason: "physick tear not in catalog".into(),
            });
            continue;
        };
        physick.push(Claim {
            order: index as i64,
            index,
            item: EquipRef {
                item_id: found.full_item_id,
                param_id: found.param_id(),
                name: tear.clone(),
            },
        });
    }
    settle(
        &mut out.physick,
        physick,
        |index| format!("physick slot {index}"),
        Contest::LastWins,
        &mut out.rejected,
        &mut out.contested,
    );

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

/// One row of the active set asking for one position.
struct Claim {
    /// The row's position in its category list, which is the order the planner
    /// folds them in.
    order: i64,
    /// The position asked for.
    index: usize,
    /// What to put there.
    item: EquipRef,
}

/// Which claimant a contested position goes to.
///
/// Both values are copied from the planner's own components rather than chosen:
/// see the module docs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Contest {
    /// The row latest in the list, as a `reduce` that overwrites produces.
    LastWins,
    /// The row earliest in the list, as `find` produces.
    FirstWins,
}

/// Place every claim, resolving collisions and reporting them.
///
/// Claims are folded in `order`, so the outcome depends on the payload's own
/// numbering rather than on however the slot list happened to be serialised.
fn settle(
    slots: &mut [Option<EquipRef>],
    mut claims: Vec<Claim>,
    label: impl Fn(usize) -> String,
    contest: Contest,
    rejected: &mut Vec<Rejected>,
    contested: &mut Vec<Contested>,
) {
    claims.sort_by_key(|claim| claim.order);

    let mut by_position: BTreeMap<usize, Vec<EquipRef>> = BTreeMap::new();
    for claim in claims {
        by_position.entry(claim.index).or_default().push(claim.item);
    }

    for (index, mut claimants) in by_position {
        let position = label(index);
        if index >= slots.len() {
            for item in claimants {
                rejected.push(Rejected {
                    name: item.name,
                    reason: format!("{position} is out of range ({} available)", slots.len()),
                });
            }
            continue;
        }
        let winner = match contest {
            Contest::LastWins => claimants.pop(),
            Contest::FirstWins => (!claimants.is_empty()).then(|| claimants.remove(0)),
        };
        let Some(winner) = winner else { continue };
        if !claimants.is_empty() {
            for loser in &claimants {
                rejected.push(Rejected {
                    name: loser.name.clone(),
                    reason: format!("{position} already claimed by {:?}", winner.name),
                });
            }
            contested.push(Contested {
                position,
                winner: winner.name.clone(),
                losers: claimants.into_iter().map(|item| item.name).collect(),
            });
        }
        slots[index] = Some(winner);
    }
}
