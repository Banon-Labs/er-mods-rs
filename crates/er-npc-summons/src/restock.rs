//! The companions' consumables: heals refilled, and food, greases, throwables and boluses kept in
//! their quick slots and pouch for the turtles' brain to use.
//!
//! A port of the lab's `setHeals` / `addHeals` / `stockFood` (`scripts/frida/spawn-npc.js` on
//! `feat/ai-lab-turtles`):
//!
//! - heals are goods 50201 (`CharaInitParam` item; SpEffect 19391 heals 30% of max HP), set to
//!   [`HEALS`] when a companion is adopted and whenever the player rests at a grace. A rest is the
//!   player starting the sit animations 68010 / 68011, or their flask count rising (measured
//!   2026-10-05: sitting with full flasks leaves the count at 14). Drunk to zero, the entry is
//!   gone, and is put back through the game's own `AddInventoryEquip`;
//! - each [`FOODS`] row is kept in its quick slot (pouch slots follow the ten quick slots, so slot
//!   10 is pouch 0): the first comes at once, each later one `restock_ms` after the last was used.
//!   A quick slot is `{handle u32, inventory index i32}` at `EquipGameData+0x290 + 8N`, mirrored as
//!   an item id at `+0x3a0 + 4N`, and an inventory index is the key item capacity plus the
//!   position in the normal list (measured 2026-10-06 on a turtle). The two greases are stocked
//!   only for a right-hand weapon that takes grease (`EquipParamWeapon.isEnhance`).

#![cfg_attr(not(windows), allow(dead_code))]

use std::collections::{HashMap, HashSet};

/// The heals every companion is refilled to: a simulated player carries one FP flask and the rest
/// HP, so 13 of the 14.
pub(crate) const HEALS: u32 = 13;
pub(crate) const HEAL_GOODS: u32 = 50201;
pub(crate) const CHECK_MS: u64 = 1000;
pub(crate) const GRACE_SIT_ANIMS: [i32; 2] = [68010, 68011];

/// A goods item id: category nibble 4 over the row.
pub(crate) const fn goods_item(goods: u32) -> u32 {
    0x4000_0000 | goods
}

/// A goods gaitem handle: goods are not indexed, so it is the category bits over the row (a
/// turtle's heal read `0xb000c419` for 50201).
pub(crate) const fn goods_handle(goods: u32) -> u32 {
    0xb000_0000 | goods
}

/// The player's flasks: goods 1000..1100, both colours and every upgrade.
pub(crate) fn is_flask(item: u32) -> bool {
    item & 0xf000_0000 == 0x4000_0000 && (1000..1100).contains(&(item & 0x0fff_ffff))
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Food {
    pub(crate) name: &'static str,
    pub(crate) goods: u32,
    pub(crate) slot: usize,
    pub(crate) restock_ms: u64,
    pub(crate) qty: u32,
    pub(crate) needs_enhance: bool,
}

const fn food(name: &'static str, goods: u32, slot: usize, restock_ms: u64, qty: u32) -> Food {
    Food {
        name,
        goods,
        slot,
        restock_ms,
        qty,
        needs_enhance: false,
    }
}

const fn grease(name: &'static str, goods: u32, slot: usize) -> Food {
    Food {
        name,
        goods,
        slot,
        restock_ms: 2000,
        qty: 1,
        needs_enhance: true,
    }
}

/// The lab's table, slot for slot.
pub(crate) const FOODS: [Food; 15] = [
    // Scorpion Stew: physical negation up and 8 HP a second; its only cooldown is the restock.
    food("stew", 2001202, 1, 60_000, 1),
    // Boiled Crab: physical negation greatly up.
    food("crab", 820, 2, 5000, 1),
    // Dragonbolt Grease (60 s) and Drawstring Dragonbolt Grease (25 s).
    grease("grease", 2001410, 3),
    grease("drawstring", 2001510, 4),
    // Throwables from the turtles' builds, a full stack at a time (EquipParamGoods maxNum).
    food("kukri", 1730, 5, 3000, 30),
    food("harpoon", 2001710, 6, 3000, 5),
    food("hefty-pot", 2000690, 7, 3000, 10),
    food("fetid-pot", 330, 8, 3000, 10),
    food("albinauric-pot", 610, 9, 3000, 10),
    food("spark-aromatic", 3510, 10, 3000, 10),
    // Boluses for `cure`: poison, rot, bleed.
    food("neutralizing", 900, 11, 3000, 5),
    food("preserving", 940, 12, 3000, 5),
    food("stanching", 910, 13, 3000, 5),
    // Uplifting Aromatic, and Mimic's Veil (not consumed).
    food("uplifting", 3500, 14, 3000, 10),
    food("mimic-veil", 3040, 15, 3000, 1),
];

/// What to do about one food on one companion.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Step {
    /// Nothing yet.
    Wait,
    /// It was just used up; say so once.
    Eaten,
    /// Grant `qty` through the game.
    Grant,
    /// It is in the inventory at this position; make sure the slot points at it.
    Slot(usize),
}

#[derive(Default)]
pub(crate) struct Stock {
    given: HashSet<(usize, usize)>,
    eaten_at: HashMap<(usize, usize), u64>,
}

impl Stock {
    /// Decide for food `index` on `chr` at `now` (ms); `at` is its position in the normal
    /// inventory list, when it is there.
    pub(crate) fn step(&mut self, now: u64, chr: usize, index: usize, at: Option<usize>) -> Step {
        let key = (chr, index);
        if let Some(at) = at {
            self.eaten_at.remove(&key);
            return Step::Slot(at);
        }
        if self.given.contains(&key) {
            let Some(&eaten) = self.eaten_at.get(&key) else {
                self.eaten_at.insert(key, now);
                return Step::Eaten;
            };
            if now.saturating_sub(eaten) < FOODS[index].restock_ms {
                return Step::Wait;
            }
        }
        Step::Grant
    }

    /// A grant of food `index` to `chr` landed.
    pub(crate) fn granted(&mut self, chr: usize, index: usize) {
        self.given.insert((chr, index));
    }

    /// Forget companions that are gone.
    pub(crate) fn retain(&mut self, squad: &[usize]) {
        self.given.retain(|(c, _)| squad.contains(c));
        self.eaten_at.retain(|(c, _), _| squad.contains(c));
    }
}

/// The player resting at a grace.
#[derive(Default)]
pub(crate) struct Grace {
    sat: bool,
    flasks: Option<u32>,
}

impl Grace {
    /// One sample; true on the frame a rest begins.
    pub(crate) fn update(&mut self, anim: Option<i32>, flasks: u32) -> bool {
        let sat = anim.is_some_and(|a| GRACE_SIT_ANIMS.contains(&a));
        let rose = self.flasks.is_some_and(|before| flasks > before);
        let rest = (sat && !self.sat) || rose;
        self.sat = sat;
        self.flasks = Some(flasks);
        rest
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_table_matches_the_lab() {
        let slots: Vec<usize> = FOODS.iter().map(|f| f.slot).collect();
        assert_eq!(slots, (1..=15).collect::<Vec<_>>());
        assert_eq!(goods_handle(HEAL_GOODS), 0xb000_c419);
        assert_eq!(goods_item(HEAL_GOODS), 0x4000_c419);
    }

    #[test]
    fn flasks_are_goods_1000_to_1099() {
        assert!(is_flask(goods_item(1000)));
        assert!(is_flask(goods_item(1099)));
        assert!(!is_flask(goods_item(1100)));
        assert!(!is_flask(1050));
    }

    #[test]
    fn the_first_is_granted_at_once_and_the_next_after_the_restock() {
        let mut s = Stock::default();
        assert_eq!(s.step(0, 1, 0, None), Step::Grant);
        s.granted(1, 0);
        assert_eq!(s.step(10, 1, 0, Some(4)), Step::Slot(4));
        // Eaten at 100: reported once, then held for the stew's 60 s.
        assert_eq!(s.step(100, 1, 0, None), Step::Eaten);
        assert_eq!(s.step(30_000, 1, 0, None), Step::Wait);
        assert_eq!(s.step(60_100, 1, 0, None), Step::Grant);
    }

    #[test]
    fn a_failed_grant_is_retried_at_once() {
        let mut s = Stock::default();
        assert_eq!(s.step(0, 1, 1, None), Step::Grant);
        assert_eq!(s.step(1000, 1, 1, None), Step::Grant);
    }

    #[test]
    fn a_rest_is_sitting_down_or_flasks_rising() {
        let mut g = Grace::default();
        assert!(!g.update(None, 14));
        assert!(g.update(Some(68010), 14));
        assert!(!g.update(Some(68011), 14));
        assert!(!g.update(None, 10));
        assert!(g.update(None, 14));
    }
}
