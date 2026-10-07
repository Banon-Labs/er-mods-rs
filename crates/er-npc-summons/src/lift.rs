//! Is the player on a lift, and which companions are not on it with them.
//!
//! The first `facts` producer: `LAB_WORLD.player_on_lift` and `LAB_WORLD.tt_lift_off_p`, which the
//! turtles' brain reads in `TT_on_lift`. A port of the lab's `liftCheck` (`scripts/frida/
//! spawn-npc.js` on `feat/ai-lab-turtles`), whose every offset was measured live on 1.17.1 (bd
//! `lift-detection-ground-handle-1171-2026-10-05`):
//!
//! - what a character stands on is its physics module's `+0x240` field-ins handle, with `+0x92`
//!   saying it stands at all. The low dword's top nibble is the kind; a lift is an asset, kind 6,
//!   or one of its collision parts, kind 8. In Raya Lucaria the player on a lift read
//!   `0x0e000000_61201a26` and stepping off read `0x0e000000_01200041` (kind 0, map collision);
//! - which assets are lifts: argument 3 of the common EMEVD lift events 90005500/90005501 and
//!   their 9000550x variants, the 63 entity ids in [`LIFT_ENTITY_IDS`]; their live handles come
//!   from `CSWorldGeomMan`'s block map, refreshed every [`REFRESH`];
//! - a handle is matched on its block (high dword) and index (low 20 bits), which holds for both
//!   kinds, so a lift's asset and its collision parts all map to the lift's entity id.
//!
//! While the player is airborne the last answer stands, so a jump on a lift does not flap the fact.
//! A companion is aboard when it stands on the same lift, however far from the player: measured
//! 2026-10-06, the turtles stopped 1.8-2 m away on a lift and kept re-boarding against a fixed
//! radius.

#![cfg_attr(not(windows), allow(dead_code))]

use std::collections::HashMap;

use crate::addr::{geom, physics};
use crate::world::{Mem, distance_cm, physics_of, pos_of, walk_map};

/// Entity ids of lift assets: argument 3 of EMEVD events 90005500/90005501 and the 9000550x
/// variants, read out of the extracted event files. Sorted, for `binary_search`.
pub(crate) const LIFT_ENTITY_IDS: [u32; 63] = [
    10001510, 10001515, 10001520, 11001510, 11001515, 11001520, 11001525, 11001530, 11001535,
    11001610, 11051525, 11051530, 11051535, 11051610, 12011510, 12011515, 12011520, 12011525,
    12021520, 12021525, 12051510, 12071515, 12071525, 13001510, 13001515, 13001520, 13001525,
    13001530, 14001510, 14001515, 14001520, 15001520, 15001525, 15001620, 15001625, 16001510,
    16001520, 16001525, 16001530, 18001510, 18001515, 20001510, 20001515, 20001520, 20001525,
    20011510, 20011515, 20011520, 20011525, 20011530, 20011535, 21001510, 21001515, 21001520,
    21001525, 21001530, 21001535, 21011510, 21011515, 21011520, 21011525, 21011530, 21021510,
];

/// How often the player's ground is checked, and the lift handles re-read.
pub(crate) const CHECK: std::time::Duration = std::time::Duration::from_millis(250);
pub(crate) const REFRESH: std::time::Duration = std::time::Duration::from_secs(3);

/// Bounds on one refresh: loaded blocks, and entities in one block.
const BLOCKS_MAX: usize = 4096;
const ENTITIES_MAX: usize = 20_000;

/// The ground kinds that are an asset (6) or an asset's collision part (8).
const KIND_ASSET: u32 = 6;
const KIND_ASSET_PART: u32 = 8;

pub(crate) fn is_lift_entity(id: u32) -> bool {
    LIFT_ENTITY_IDS.binary_search(&id).is_ok()
}

/// A field-ins handle's block and index: the part a lift's asset and its collision parts share.
pub(crate) fn handle_key(lo: u32, hi: u32) -> u64 {
    (u64::from(hi) << 20) | u64::from(lo & 0xf_ffff)
}

/// What a character stands on.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Ground {
    pub(crate) lo: u32,
    pub(crate) hi: u32,
    pub(crate) standing: bool,
}

impl Ground {
    pub(crate) fn kind(self) -> u32 {
        self.lo >> 28
    }

    /// The lift this is, when it is one.
    pub(crate) fn lift(self, lifts: &HashMap<u64, u32>) -> Option<u32> {
        matches!(self.kind(), KIND_ASSET | KIND_ASSET_PART)
            .then(|| lifts.get(&handle_key(self.lo, self.hi)).copied())
            .flatten()
    }
}

impl std::fmt::Display for Ground {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "0x{:x}_{:08x} kind {}", self.hi, self.lo, self.kind())
    }
}

pub(crate) fn ground_of(mem: &impl Mem, chr: usize) -> Option<Ground> {
    let p = physics_of(mem, chr)?;
    Some(Ground {
        lo: mem.u32(p + physics::GROUND_HANDLE)?,
        hi: mem.u32(p + physics::GROUND_HANDLE + 4)?,
        standing: mem.u8(p + physics::STANDING)? != 0,
    })
}

/// Every loaded lift's handles, by [`handle_key`], from `CSWorldGeomMan` at `man`. `None` when a
/// walk did not finish.
pub(crate) fn lift_handles(mem: &impl Mem, man: usize) -> Option<HashMap<u64, u32>> {
    let mut found = HashMap::new();
    let mut failed = false;
    let blocks = mem.usize(man + geom::MAN_BLOCKS)?;
    walk_map(mem, blocks, BLOCKS_MAX, |block| {
        if failed {
            return;
        }
        let Some(data) = mem.usize(block + geom::BLOCK_NODE_DATA) else {
            failed = true;
            return;
        };
        if data == 0 {
            return;
        }
        let Some(entities) = mem.usize(data + geom::BLOCK_ENTITIES) else {
            failed = true;
            return;
        };
        let walked = walk_map(mem, entities, ENTITIES_MAX, |node| {
            let Some(id) = mem.u32(node + geom::ENTITY_NODE_ID) else {
                return;
            };
            if !is_lift_entity(id) {
                return;
            }
            let lo = mem.u32(node + geom::ENTITY_NODE_HANDLE);
            let hi = mem.u32(node + geom::ENTITY_NODE_HANDLE + 4);
            if let (Some(lo), Some(hi)) = (lo, hi) {
                found.insert(handle_key(lo, hi), id);
            }
        });
        failed |= walked.is_none();
    })?;
    (!failed).then_some(found)
}

/// One sample of a character: its ground and position.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Sample {
    pub(crate) chr: usize,
    pub(crate) ground: Ground,
    pub(crate) pos: [f32; 3],
}

/// What one check changed.
#[derive(Debug, Default, PartialEq)]
pub(crate) struct Change {
    /// The player got on (`Some(id)`) or off (`None`) a lift.
    pub(crate) boarded: Option<Option<u32>>,
    /// The distances of the companions off the player's lift, when the list is to be sent.
    pub(crate) off: Option<Vec<f64>>,
    /// The companions off the lift, when their count changed: address and ground.
    pub(crate) off_detail: Option<Vec<(usize, Ground, f64)>>,
}

/// The lift answer between checks.
#[derive(Default)]
pub(crate) struct Tracker {
    /// The lift the player stands on; `None` before the first standing sample.
    on: Option<Option<u32>>,
    off_count: Option<usize>,
}

impl Tracker {
    /// The lift the player was last seen standing on.
    pub(crate) fn current(&self) -> Option<u32> {
        self.on.flatten()
    }

    /// Fold in one check. `player` is `None` when there is no player to sample.
    pub(crate) fn update(
        &mut self,
        player: Option<Sample>,
        squad: &[Sample],
        lifts: &HashMap<u64, u32>,
    ) -> Change {
        let mut change = Change::default();
        let Some(player) = player else {
            return change;
        };
        // Airborne: keep the last answer rather than flapping off and on.
        if !player.ground.standing {
            return change;
        }
        let lift = player.ground.lift(lifts);
        if self.on != Some(lift) {
            self.on = Some(lift);
            change.boarded = Some(lift);
        }
        let mut off = Vec::new();
        let mut detail = Vec::new();
        if let Some(lift) = lift {
            for c in squad {
                let same = c.ground.lift(lifts) == Some(lift)
                    || (c.ground.lo == player.ground.lo && c.ground.hi == player.ground.hi);
                if !same {
                    let d = distance_cm(c.pos, player.pos);
                    off.push(d);
                    detail.push((c.chr, c.ground, d));
                }
            }
        }
        // While anyone is off the lift their distances move, so the list is sent every check;
        // an empty list only when it first becomes empty.
        if self.off_count != Some(off.len()) {
            self.off_count = Some(off.len());
            change.off_detail = Some(detail);
            change.off = Some(off);
        } else if !off.is_empty() {
            change.off = Some(off);
        }
        change
    }
}

/// Sample a character for [`Tracker::update`].
pub(crate) fn sample(mem: &impl Mem, chr: usize) -> Option<Sample> {
    Some(Sample {
        chr,
        ground: ground_of(mem, chr)?,
        pos: pos_of(mem, chr)?,
    })
}

#[cfg(windows)]
pub(crate) mod live {
    //! The game-task side: timing, the reads, the facts and the log.

    use std::collections::HashMap;
    use std::sync::Mutex;
    use std::time::Instant;

    use er_game_base::mem::{game_module_base, read_global_ptr};

    use super::{CHECK, REFRESH, Tracker, lift_handles, sample};
    use crate::addr::CS_WORLD_GEOM_MAN_GLOBAL_RVA;
    use crate::facts::{self, Notify};
    use crate::log::summons_log;
    use crate::world::GameMem;
    use crate::{game, team};

    struct State {
        tracker: Tracker,
        lifts: HashMap<u64, u32>,
        checked_at: Option<Instant>,
        refreshed_at: Option<Instant>,
        refresh_failed: bool,
    }

    static STATE: Mutex<Option<State>> = Mutex::new(None);

    fn world_geom_man() -> Option<usize> {
        let base = game_module_base().ok()?;
        let man = read_global_ptr(
            base,
            CS_WORLD_GEOM_MAN_GLOBAL_RVA,
            "CS_WORLD_GEOM_MAN_GLOBAL_RVA",
        );
        (man != 0).then_some(man)
    }

    fn refresh(state: &mut State) {
        let Some(man) = world_geom_man() else {
            return;
        };
        match lift_handles(&GameMem, man) {
            Some(lifts) => {
                if lifts.len() != state.lifts.len() {
                    let mut ids: Vec<u32> = lifts.values().copied().collect();
                    ids.sort_unstable();
                    ids.dedup();
                    summons_log(format_args!(
                        "lift: {} lift handle(s) loaded, entities {ids:?}",
                        lifts.len()
                    ));
                }
                state.lifts = lifts;
                state.refresh_failed = false;
            }
            None => {
                // The previous handles stay; a map being rebuilt is read again next refresh.
                if !state.refresh_failed {
                    summons_log(format_args!(
                        "lift: the block map did not walk; keeping {} handle(s)",
                        state.lifts.len()
                    ));
                }
                state.refresh_failed = true;
            }
        }
    }

    /// Called from the game task every frame.
    pub(crate) fn tick() {
        let now = Instant::now();
        let mut guard = STATE.lock().unwrap_or_else(|e| e.into_inner());
        let state = guard.get_or_insert_with(|| State {
            tracker: Tracker::default(),
            lifts: HashMap::new(),
            checked_at: None,
            refreshed_at: None,
            refresh_failed: false,
        });
        if state.checked_at.is_some_and(|at| now - at < CHECK) {
            return;
        }
        state.checked_at = Some(now);
        if state.refreshed_at.is_none_or(|at| now - at >= REFRESH) {
            state.refreshed_at = Some(now);
            refresh(state);
        }
        let player = game::main_player().and_then(|p| sample(&GameMem, p));
        let squad: Vec<_> = team::companions()
            .into_iter()
            .filter_map(|c| sample(&GameMem, c))
            .collect();
        let player_ground = player.map(|p| p.ground);
        let change = state.tracker.update(player, &squad, &state.lifts);
        if let Some(lift) = change.boarded {
            facts::set_bool("player_on_lift", lift.is_some(), Notify::Replan);
            let ground = player_ground.map(|g| g.to_string()).unwrap_or_default();
            match lift {
                Some(id) => summons_log(format_args!("lift: player on lift {id} ({ground})")),
                None => summons_log(format_args!("lift: player off the lift ({ground})")),
            }
        }
        if let Some(off) = change.off {
            facts::set_numbers("tt_lift_off_p", off, Notify::Quiet);
        }
        if let Some(detail) = change.off_detail
            && state.tracker.current().is_some()
        {
            let list: Vec<String> = detail
                .iter()
                .map(|(chr, ground, d)| format!("0x{chr:x} on {ground} at {d} m"))
                .collect();
            summons_log(format_args!(
                "lift: {} companion(s) off the player's lift{}{}",
                detail.len(),
                if list.is_empty() { "" } else { ": " },
                list.join(", ")
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::world::fake::FakeMem;

    // Raya Lucaria, 2026-10-05: lifts 14001510/15/20 at block 0x0e000000, index 6693/6694/6695.
    const BLOCK: u32 = 0x0e00_0000;
    const ON_LIFT: Ground = Ground {
        lo: 0x6120_1a26,
        hi: BLOCK,
        standing: true,
    };
    const OFF_LIFT: Ground = Ground {
        lo: 0x0120_0041,
        hi: BLOCK,
        standing: true,
    };

    fn raya_lifts() -> HashMap<u64, u32> {
        [(0x1a25, 14001510), (0x1a26, 14001515), (0x1a27, 14001520)]
            .into_iter()
            .map(|(index, id)| (handle_key(index, BLOCK), id))
            .collect()
    }

    fn at(chr: usize, ground: Ground, pos: [f32; 3]) -> Sample {
        Sample { chr, ground, pos }
    }

    #[test]
    fn lift_ids_are_sorted_and_unique() {
        assert!(LIFT_ENTITY_IDS.windows(2).all(|w| w[0] < w[1]));
        assert!(is_lift_entity(14001515));
        assert!(!is_lift_entity(14001516));
    }

    #[test]
    fn handle_key_drops_the_kind_and_keeps_block_and_index() {
        // The asset (kind 6) and a collision part (kind 8) of one lift share a key.
        assert_eq!(
            handle_key(0x6120_1a26, BLOCK),
            handle_key(0x8000_1a26, BLOCK)
        );
        assert_ne!(
            handle_key(0x6120_1a26, BLOCK),
            handle_key(0x6120_1a26, BLOCK + 1)
        );
        assert_eq!(
            handle_key(0x6120_1a26, BLOCK),
            (u64::from(BLOCK) << 20) | 0x1a26
        );
    }

    #[test]
    fn the_measured_handles_classify() {
        let lifts = raya_lifts();
        assert_eq!(ON_LIFT.kind(), 6);
        assert_eq!(ON_LIFT.lift(&lifts), Some(14001515));
        assert_eq!(OFF_LIFT.kind(), 0);
        assert_eq!(OFF_LIFT.lift(&lifts), None);
        // Map collision with a lift's index is not the lift.
        let map_collision = Ground {
            lo: 0x0000_1a26,
            ..ON_LIFT
        };
        assert_eq!(map_collision.lift(&lifts), None);
    }

    #[test]
    fn boarding_and_leaving_are_reported_once() {
        let lifts = raya_lifts();
        let mut t = Tracker::default();
        let first = t.update(Some(at(1, OFF_LIFT, [0.0; 3])), &[], &lifts);
        assert_eq!(first.boarded, Some(None));
        assert_eq!(first.off, Some(Vec::new()));
        assert_eq!(
            t.update(Some(at(1, OFF_LIFT, [0.0; 3])), &[], &lifts),
            Change::default()
        );
        assert_eq!(
            t.update(Some(at(1, ON_LIFT, [0.0; 3])), &[], &lifts)
                .boarded,
            Some(Some(14001515))
        );
        assert_eq!(t.current(), Some(14001515));
        assert_eq!(
            t.update(Some(at(1, OFF_LIFT, [0.0; 3])), &[], &lifts)
                .boarded,
            Some(None)
        );
    }

    #[test]
    fn airborne_and_missing_players_keep_the_last_answer() {
        let lifts = raya_lifts();
        let mut t = Tracker::default();
        t.update(Some(at(1, ON_LIFT, [0.0; 3])), &[], &lifts);
        let jumping = Ground {
            standing: false,
            ..OFF_LIFT
        };
        assert_eq!(
            t.update(Some(at(1, jumping, [0.0; 3])), &[], &lifts),
            Change::default()
        );
        assert_eq!(t.update(None, &[], &lifts), Change::default());
        assert_eq!(t.current(), Some(14001515));
    }

    #[test]
    fn companions_on_the_same_lift_are_aboard_at_any_distance() {
        let lifts = raya_lifts();
        let mut t = Tracker::default();
        // A collision part of the same lift, 2 m off: aboard. Map collision, 5 m off: not.
        let part = Ground {
            lo: 0x8000_1a26,
            ..ON_LIFT
        };
        let squad = [
            at(2, part, [2.0, 0.0, 0.0]),
            at(3, OFF_LIFT, [3.0, 4.0, 0.0]),
        ];
        let change = t.update(Some(at(1, ON_LIFT, [0.0; 3])), &squad, &lifts);
        assert_eq!(change.off, Some(vec![5.0]));
        assert_eq!(change.off_detail.map(|d| d.len()), Some(1));
        // Same count, still sent while someone is off; detail only on a count change.
        let squad = [
            at(2, part, [2.0, 0.0, 0.0]),
            at(3, OFF_LIFT, [0.0, 4.0, 0.0]),
        ];
        let change = t.update(Some(at(1, ON_LIFT, [0.0; 3])), &squad, &lifts);
        assert_eq!(change.off, Some(vec![4.0]));
        assert_eq!(change.off_detail, None);
        // Everyone aboard: an empty list once, then nothing.
        let squad = [
            at(2, part, [2.0, 0.0, 0.0]),
            at(3, ON_LIFT, [1.0, 0.0, 0.0]),
        ];
        let change = t.update(Some(at(1, ON_LIFT, [0.0; 3])), &squad, &lifts);
        assert_eq!(change.off, Some(Vec::new()));
        let change = t.update(Some(at(1, ON_LIFT, [0.0; 3])), &squad, &lifts);
        assert_eq!(change.off, None);
    }

    #[test]
    fn a_companion_on_a_different_lift_is_off() {
        let lifts = raya_lifts();
        let mut t = Tracker::default();
        let other = Ground {
            lo: 0x6120_1a27,
            ..ON_LIFT
        };
        let change = t.update(
            Some(at(1, ON_LIFT, [0.0; 3])),
            &[at(2, other, [0.0, 10.0, 0.0])],
            &lifts,
        );
        assert_eq!(change.off, Some(vec![10.0]));
    }

    #[test]
    fn lift_handles_are_read_out_of_the_block_maps() {
        let mut mem = FakeMem::default();
        let man = 0x10_0000;
        let blocks_head = 0x20_0000;
        mem.put_usize(man + geom::MAN_BLOCKS, blocks_head);
        // Two blocks, one with no data.
        mem.put_tree(blocks_head, &[0x21_0000, 0x22_0000]);
        mem.put_usize(0x21_0000 + geom::BLOCK_NODE_DATA, 0x30_0000);
        mem.put_usize(0x22_0000 + geom::BLOCK_NODE_DATA, 0);
        let entities_head = 0x40_0000;
        mem.put_usize(0x30_0000 + geom::BLOCK_ENTITIES, entities_head);
        let nodes = [0x41_0000, 0x42_0000, 0x43_0000];
        mem.put_tree(entities_head, &nodes);
        for (node, id, lo) in [
            (nodes[0], 14001510u32, 0x6120_1a25u32),
            (nodes[1], 14001599, 0x6120_1a30),
            (nodes[2], 14001515, 0x6120_1a26),
        ] {
            mem.put_u32(node + geom::ENTITY_NODE_ID, id);
            mem.put_u32(node + geom::ENTITY_NODE_HANDLE, lo);
            mem.put_u32(node + geom::ENTITY_NODE_HANDLE + 4, BLOCK);
        }
        let found = lift_handles(&mem, man).unwrap();
        assert_eq!(found.len(), 2);
        assert_eq!(ON_LIFT.lift(&found), Some(14001515));
    }

    #[test]
    fn a_half_walked_block_map_is_no_answer() {
        let mut mem = FakeMem::default();
        let man = 0x10_0000;
        mem.put_usize(man + geom::MAN_BLOCKS, 0x20_0000);
        mem.put_tree(0x20_0000, &[0x21_0000]);
        // The block's data pointer reads, its entity map head does not.
        mem.put_usize(0x21_0000 + geom::BLOCK_NODE_DATA, 0x30_0000);
        assert_eq!(lift_handles(&mem, man), None);
    }

    #[test]
    fn a_character_samples_through_its_physics_module() {
        let mut mem = FakeMem::default();
        mem.put_usize(0x5000 + physics::CHR_MODULES, 0x6000);
        mem.put_usize(0x6000 + physics::MODULES_PHYSICS, 0x7000);
        for (i, v) in [1.0f32, 2.0, 3.0].into_iter().enumerate() {
            mem.put_f32(0x7000 + physics::POS + 4 * i, v);
        }
        mem.put_u32(0x7000 + physics::GROUND_HANDLE, ON_LIFT.lo);
        mem.put_u32(0x7000 + physics::GROUND_HANDLE + 4, ON_LIFT.hi);
        mem.put_u8(0x7000 + physics::STANDING, 1);
        let s = sample(&mem, 0x5000).unwrap();
        assert_eq!(s.ground, ON_LIFT);
        assert_eq!(s.pos, [1.0, 2.0, 3.0]);
        assert_eq!(ON_LIFT.to_string(), "0xe000000_61201a26 kind 6");
    }
}
