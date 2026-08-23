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
/// `ChrAsmSlot` of quickbar position 0; the pouch continues at `+10`.
///
/// `ConvertChrAsmSlotToQuickItemOrPouchSlot` (`0x1402470b0`) maps `0x16..0x1F` to quickbar
/// `0..9` and `0x20..0x25` to pouch `10..15`, so the native slot is the planner position plus
/// `0x16`. Held here rather than in the runtime crate because the plan and the runtime must
/// agree on it, and a second copy is a second thing to get wrong.
pub const CHR_ASM_SLOT_QUICK_BASE: i32 = 0x16;
/// `ChrAsmSlot::GreatRune`, one position past the pouch -- the same dispatcher's `index == 16`.
pub const CHR_ASM_SLOT_GREAT_RUNE: i32 = 0x26;
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

    /// Total number of equip positions this plan will actually write.
    ///
    /// Derived from [`EquipPlan::positions`] rather than counted independently, because two
    /// counts of the same thing are two chances to disagree -- and when they disagreed, the
    /// larger one was printed as the denominator of a score the smaller one had already passed.
    /// Spells are NOT included: they are memorised by a different native and reported on their
    /// own line with their own denominator.
    pub fn occupied(&self) -> usize {
        self.positions().len()
    }

    /// Every position this plan intends to fill, in the order they are written.
    ///
    /// THIS is the denominator. A position that never reaches a slot has to show up here and
    /// then fail to be accounted for, which is what makes it visible; a pass that reports a
    /// score against the subset it happened to attempt can print a perfect run while dropping
    /// everything it never tried.
    pub fn positions(&self) -> Vec<PlannedPosition> {
        let mut out = Vec::new();

        for (index, entry) in self.armaments.iter().enumerate() {
            if let Some(item) = entry {
                out.push(PlannedPosition {
                    kind: PositionKind::Armament,
                    slot: u32::try_from(index).ok().and_then(armament_slot),
                    index,
                    item: item.clone(),
                });
            }
        }
        for (offset, entry) in [&self.head, &self.body, &self.arms, &self.legs]
            .into_iter()
            .enumerate()
        {
            if let Some(item) = entry {
                out.push(PlannedPosition {
                    kind: PositionKind::Protector,
                    slot: i32::try_from(offset)
                        .ok()
                        .map(|offset| CHR_ASM_SLOT_PROTECTOR_HEAD + offset),
                    index: offset,
                    item: item.clone(),
                });
            }
        }
        for (index, entry) in self.talismans.iter().enumerate() {
            if let Some(item) = entry {
                out.push(PlannedPosition {
                    kind: PositionKind::Talisman,
                    slot: i32::try_from(index)
                        .ok()
                        .map(|index| CHR_ASM_SLOT_ACCESSORY_1 + index),
                    index,
                    item: item.clone(),
                });
            }
        }
        for (index, entry) in self.quickbar.iter().enumerate() {
            if let Some(item) = entry {
                out.push(PlannedPosition {
                    kind: PositionKind::Quickbar,
                    slot: i32::try_from(index)
                        .ok()
                        .map(|index| CHR_ASM_SLOT_QUICK_BASE + index),
                    index,
                    item: item.clone(),
                });
            }
        }
        for (index, entry) in self.pouch.iter().enumerate() {
            if let Some(item) = entry {
                out.push(PlannedPosition {
                    kind: PositionKind::Pouch,
                    slot: i32::try_from(index + QUICKBAR_SLOTS)
                        .ok()
                        .map(|index| CHR_ASM_SLOT_QUICK_BASE + index),
                    index,
                    item: item.clone(),
                });
            }
        }
        if let Some(item) = self.great_rune.as_ref() {
            out.push(PlannedPosition {
                kind: PositionKind::GreatRune,
                slot: Some(CHR_ASM_SLOT_GREAT_RUNE),
                index: 0,
                item: item.clone(),
            });
        }
        for (index, entry) in self.physick.iter().enumerate() {
            if let Some(item) = entry {
                out.push(PlannedPosition {
                    kind: PositionKind::Physick,
                    slot: None,
                    index,
                    item: item.clone(),
                });
            }
        }

        out
    }
}

/// What kind of equip position a plan entry targets.
///
/// The kinds are not cosmetic: they select which native writes the position and which read-back
/// can prove it. Armaments, armour and talismans are `ChrAsm` equipment entries; quickbar, pouch
/// and great rune go through a different dispatcher entirely; the physick is a plain field.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PositionKind {
    /// A hand slot.
    Armament,
    /// One of the four armour pieces.
    Protector,
    /// A talisman slot.
    Talisman,
    /// A quickbar position, `0..10`.
    Quickbar,
    /// A pouch position, `0..6`.
    Pouch,
    /// The equipped great rune.
    GreatRune,
    /// A Flask of Wondrous Physick tear slot.
    Physick,
}

impl PositionKind {
    /// Lower-case name for a log line.
    pub fn label(self) -> &'static str {
        match self {
            PositionKind::Armament => "armament",
            PositionKind::Protector => "armour",
            PositionKind::Talisman => "talisman",
            PositionKind::Quickbar => "quickbar",
            PositionKind::Pouch => "pouch",
            PositionKind::GreatRune => "great rune",
            PositionKind::Physick => "physick",
        }
    }

    /// Whether this kind is written by the quick/pouch/rune dispatcher rather than by the
    /// `ChrAsm` equipment path.
    pub fn is_quick_dispatch(self) -> bool {
        matches!(
            self,
            PositionKind::Quickbar | PositionKind::Pouch | PositionKind::GreatRune
        )
    }
}

/// One position the plan intends to fill, and what should end up in it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannedPosition {
    /// Which family of position this is.
    pub kind: PositionKind,
    /// Native `ChrAsmSlot`, where the position has one. The physick is a field, not a slot.
    pub slot: Option<i32>,
    /// Position within its own kind.
    pub index: usize,
    /// What belongs there.
    pub item: EquipRef,
}

impl PlannedPosition {
    /// A short identification for a log line, naming the item so a failure is actionable.
    pub fn describe(&self) -> String {
        match self.slot {
            Some(slot) => format!(
                "{} {} (slot {slot}) {:?}",
                self.kind.label(),
                self.index,
                self.item.name
            ),
            None => format!("{} {} {:?}", self.kind.label(), self.index, self.item.name),
        }
    }
}

/// What happened to one planned position, decided by reading the game back.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PositionResult {
    /// The position holds the requested item, read back after writing.
    Verified,
    /// It already held it, so nothing was written. Equipping into the slot an item already
    /// occupies toggles it off, so this is a success, not a skip.
    Already,
    /// The grant did not land, so there was nothing to equip.
    NotInInventory,
    /// Something was written and the position holds something else afterwards.
    Mismatch {
        /// The id that was asked for.
        expected: i32,
        /// The id actually found.
        actual: i32,
    },
    /// Nothing was attempted, and why. Always a failure: an unattempted position is exactly the
    /// thing that used to vanish out of the denominator.
    NotAttempted(&'static str),
}

impl PositionResult {
    /// Whether the position ended up holding what the build asked for.
    pub fn is_success(&self) -> bool {
        matches!(self, PositionResult::Verified | PositionResult::Already)
    }

    /// Plain-language reason, for the failure list.
    pub fn reason(&self) -> String {
        match self {
            PositionResult::Verified => "verified".to_owned(),
            PositionResult::Already => "already correct".to_owned(),
            PositionResult::NotInInventory => "not in the inventory".to_owned(),
            PositionResult::Mismatch { expected, actual } => {
                format!("wanted {expected}, holds {actual}")
            }
            PositionResult::NotAttempted(why) => (*why).to_owned(),
        }
    }
}

/// Totals over a [`EquipLedger`], with the PLAN as the denominator.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct LedgerCounts {
    /// Positions the plan asked for.
    pub planned: usize,
    /// Positions read back holding the requested item.
    pub verified: usize,
    /// Positions that already held it.
    pub already: usize,
    /// Positions that were attempted and did not end up right.
    pub failed: usize,
    /// Positions for which no result was ever recorded -- the pass simply never visited them.
    pub unaccounted: usize,
}

impl LedgerCounts {
    /// Whether every planned position is accounted for and correct.
    pub fn reconciles(&self) -> bool {
        self.failed == 0 && self.unaccounted == 0
    }
}

/// Every planned position, and what became of it.
///
/// # Why this exists
///
/// The importer used to report equipping as `verified / attempted`. When a whole family of
/// positions was never attempted, they left the denominator with them, and a run that dropped
/// them printed `10/10 verified` -- a perfect score for a partial import. The ledger is
/// constructed from [`EquipPlan::positions`] BEFORE anything is written, so a position that is
/// never visited stays in the ledger as `unaccounted` and shows up in the headline.
#[derive(Debug, Clone, Default)]
pub struct EquipLedger {
    planned: Vec<PlannedPosition>,
    results: Vec<Option<PositionResult>>,
}

impl EquipLedger {
    /// Open a ledger over everything `plan` intends to fill.
    pub fn new(plan: &EquipPlan) -> Self {
        let planned = plan.positions();
        let results = vec![None; planned.len()];
        Self { planned, results }
    }

    /// The positions, in write order.
    pub fn planned(&self) -> &[PlannedPosition] {
        &self.planned
    }

    /// Record what happened to the position at `index` in [`EquipLedger::planned`].
    pub fn record(&mut self, index: usize, result: PositionResult) {
        if let Some(slot) = self.results.get_mut(index) {
            *slot = Some(result);
        }
    }

    /// Record against the first position of `kind` at position `index` within that kind.
    ///
    /// Returns whether a planned position matched. A `false` is itself an accounting fault: it
    /// means something was written that the plan never asked for.
    pub fn record_kind(
        &mut self,
        kind: PositionKind,
        index: usize,
        result: PositionResult,
    ) -> bool {
        let found = self
            .planned
            .iter()
            .position(|p| p.kind == kind && p.index == index);
        match found {
            Some(at) => {
                self.results[at] = Some(result);
                true
            }
            None => false,
        }
    }

    /// Totals against the plan.
    pub fn counts(&self) -> LedgerCounts {
        let mut counts = LedgerCounts {
            planned: self.planned.len(),
            ..LedgerCounts::default()
        };
        for result in &self.results {
            match result {
                None => counts.unaccounted += 1,
                Some(PositionResult::Verified) => counts.verified += 1,
                Some(PositionResult::Already) => counts.already += 1,
                Some(_) => counts.failed += 1,
            }
        }
        counts
    }

    /// Every position that did not end up holding what the build asked for, named.
    pub fn failures(&self) -> Vec<String> {
        self.planned
            .iter()
            .zip(&self.results)
            .filter(|(_, result)| !result.as_ref().is_some_and(PositionResult::is_success))
            .map(|(position, result)| {
                let why = result
                    .as_ref()
                    .map_or_else(|| "never attempted".to_owned(), PositionResult::reason);
                format!("{} [{why}]", position.describe())
            })
            .collect()
    }

    /// The one line. It reconciles against the plan, and when it does not, it says which
    /// positions are missing by name on the same line.
    pub fn headline(&self) -> String {
        let counts = self.counts();
        let head = format!(
            "{} planned = {} verified + {} already correct + {} failed + {} unaccounted",
            counts.planned, counts.verified, counts.already, counts.failed, counts.unaccounted
        );
        if counts.reconciles() {
            return format!("{head} -- every planned position holds the build's item");
        }
        let failures = self.failures();
        let shown = failures
            .iter()
            .take(FAILURES_ON_THE_HEADLINE)
            .cloned()
            .collect::<Vec<_>>()
            .join("; ");
        let more = failures.len().saturating_sub(FAILURES_ON_THE_HEADLINE);
        let tail = if more == 0 {
            String::new()
        } else {
            format!(" (+{more} more)")
        };
        format!(
            "{head} -- {} POSITION(S) NOT EQUIPPED: {shown}{tail}",
            failures.len()
        )
    }
}

/// How many failing positions the single headline line names before it truncates.
const FAILURES_ON_THE_HEADLINE: usize = 6;

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
