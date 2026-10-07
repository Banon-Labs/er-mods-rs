//! A swing about to land on the squad: `LAB_WORLD.tt_roll`, `tt_roll_d`, `tt_roll_p` and
//! `tt_roll_parry`, which the turtles' brain rolls (or parries) on.
//!
//! A port of the lab's `readSwings` (`scripts/frida/spawn-npc.js` on `feat/ai-lab-turtles`).
//! Every frame, each character near the squad (`watched`: alive, within [`READ_RADIUS`] m of a
//! companion, re-listed every [`READ_LIST_MS`]) has its newest TimeAct entry read; an attack
//! animation (`anim % 1000000` in 3000..=3999) that has run to [`ROLL_LEAD`] s short of its windup
//! fires once. The windup is learned per `NpcParam:anim` from the time into the animation at which
//! that swing has landed on the squad before (the `hits` detour), else [`DEFAULT_WINDUP`].
//!
//! Only companions within [`ROLL_RADIUS`] m of the attacker are warned (measured 2026-10-06:
//! warning every one within `READ_RADIUS` kept all three rolling instead of attacking).
//! `tt_roll_d` holds each warned companion's distance to the attacker, which a turtle in battle
//! matches against its own enemy; `tt_roll_p` its distance to the player, which an idle one
//! matches instead (measured 2026-10-06: an enemy hit all three for seconds while none of them
//! entered battle). `tt_roll` counts signals so a turtle can tell a new one.
//!
//! `tt_roll_parry` is the lab's "this swing has never got through a parry" bit. The lab learned it
//! from hits on a turtle whose ash, chosen by its art planner, was Parry; that planner is not part
//! of this port, so no swing is ever marked as failed and the bit is always 1.

#![cfg_attr(not(windows), allow(dead_code))]

use std::collections::HashMap;

use crate::world::{Anim, distance, round_to};

pub(crate) const READ_RADIUS: f64 = 8.0;
pub(crate) const READ_LIST_MS: u64 = 300;
pub(crate) const ROLL_RADIUS: f64 = 4.5;
pub(crate) const ROLL_LEAD: f32 = 0.3;
pub(crate) const DEFAULT_WINDUP: f32 = 0.5;
const ANIM_SECTION: i32 = 1_000_000;
const ATTACK_ANIM_MIN: i32 = 3000;
const ATTACK_ANIM_MAX: i32 = 3999;

pub(crate) fn is_attack_anim(anim: i32) -> bool {
    anim >= 0 && (ATTACK_ANIM_MIN..=ATTACK_ANIM_MAX).contains(&(anim % ANIM_SECTION))
}

#[derive(Clone, Copy)]
struct Seen {
    write: u32,
    anim: i32,
    fired: bool,
}

/// One signal's facts.
#[derive(Debug, PartialEq)]
pub(crate) struct Roll {
    pub(crate) count: u32,
    pub(crate) to_attacker: Vec<f64>,
    pub(crate) to_player: Vec<f64>,
}

#[derive(Default)]
pub(crate) struct Swings {
    seen: HashMap<usize, Seen>,
    windups: HashMap<(i32, i32), f32>,
    signals: u32,
}

impl Swings {
    /// A swing landed `time` s into `anim`: keep the earliest such time as its windup.
    pub(crate) fn learn(&mut self, npc: i32, anim: i32, time: f32) -> bool {
        if !is_attack_anim(anim) || !time.is_finite() {
            return false;
        }
        let w = self.windups.entry((npc, anim)).or_insert(f32::INFINITY);
        if time < *w {
            *w = time;
            return true;
        }
        false
    }

    pub(crate) fn windup(&self, npc: i32, anim: i32) -> f32 {
        self.windups
            .get(&(npc, anim))
            .copied()
            .filter(|w| w.is_finite())
            .unwrap_or(DEFAULT_WINDUP)
    }

    /// Forget characters no longer watched.
    pub(crate) fn retain(&mut self, watched: &[usize]) {
        self.seen.retain(|chr, _| watched.contains(chr));
    }

    /// One watched character's newest animation entry. `squad` and `player` are positions.
    pub(crate) fn check(
        &mut self,
        chr: usize,
        npc: i32,
        anim: Anim,
        at: [f32; 3],
        squad: &[[f32; 3]],
        player: Option<[f32; 3]>,
    ) -> Option<Roll> {
        let seen = self.seen.entry(chr).or_insert(Seen {
            write: anim.write,
            anim: anim.anim,
            fired: false,
        });
        if seen.write != anim.write || seen.anim != anim.anim {
            *seen = Seen {
                write: anim.write,
                anim: anim.anim,
                fired: false,
            };
        }
        if seen.fired || !is_attack_anim(anim.anim) {
            return None;
        }
        let windup = self.windup(npc, anim.anim);
        if anim.time < windup - ROLL_LEAD {
            return None;
        }
        // Re-borrow: `windup` read the map above.
        if let Some(seen) = self.seen.get_mut(&chr) {
            seen.fired = true;
        }
        let mut to_attacker = Vec::new();
        let mut to_player = Vec::new();
        for &q in squad {
            let d = distance(at, q);
            if d > ROLL_RADIUS {
                continue;
            }
            to_attacker.push(round_to(d, 2));
            if let Some(me) = player {
                to_player.push(round_to(distance(q, me), 2));
            }
        }
        if to_attacker.is_empty() {
            return None;
        }
        self.signals += 1;
        Some(Roll {
            count: self.signals,
            to_attacker,
            to_player,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn anim(write: u32, anim: i32, time: f32) -> Anim {
        Anim { write, anim, time }
    }

    #[test]
    fn attack_animations_are_the_3000s_of_a_section() {
        assert!(is_attack_anim(3000));
        assert!(is_attack_anim(21_003_010));
        assert!(!is_attack_anim(21_002_999));
        assert!(!is_attack_anim(-1));
    }

    #[test]
    fn a_swing_fires_once_at_its_windup_minus_the_lead() {
        let mut s = Swings::default();
        let squad = [[1.0, 0.0, 0.0]];
        let player = Some([4.0, 4.0, 0.0]);
        assert_eq!(
            s.check(9, 1, anim(5, 3010, 0.1), [0.0; 3], &squad, player),
            None
        );
        let r = s
            .check(9, 1, anim(5, 3010, 0.25), [0.0; 3], &squad, player)
            .unwrap();
        assert_eq!(r.count, 1);
        assert_eq!(r.to_attacker, vec![1.0]);
        assert_eq!(r.to_player, vec![5.0]);
        assert_eq!(
            s.check(9, 1, anim(5, 3010, 0.4), [0.0; 3], &squad, player),
            None
        );
        // The next swing (a new queue entry) fires again.
        assert_eq!(
            s.check(9, 1, anim(6, 3010, 0.3), [0.0; 3], &squad, player)
                .map(|r| r.count),
            Some(2)
        );
    }

    #[test]
    fn a_learned_windup_moves_the_signal() {
        let mut s = Swings::default();
        assert!(s.learn(1, 3010, 1.2));
        assert!(!s.learn(1, 3010, 1.5));
        assert!(!s.learn(1, 7000, 0.1));
        let squad = [[1.0, 0.0, 0.0]];
        assert_eq!(
            s.check(9, 1, anim(5, 3010, 0.5), [0.0; 3], &squad, None),
            None
        );
        assert!(
            s.check(9, 1, anim(5, 3010, 0.95), [0.0; 3], &squad, None)
                .is_some()
        );
    }

    #[test]
    fn only_companions_in_reach_are_warned() {
        let mut s = Swings::default();
        let squad = [[1.0, 0.0, 0.0], [10.0, 0.0, 0.0]];
        let r = s
            .check(9, 1, anim(5, 3010, 0.3), [0.0; 3], &squad, None)
            .unwrap();
        assert_eq!(r.to_attacker, vec![1.0]);
        let far = [[10.0, 0.0, 0.0]];
        assert_eq!(
            s.check(8, 1, anim(5, 3010, 0.3), [0.0; 3], &far, None),
            None
        );
    }

    #[test]
    fn non_attacks_never_fire() {
        let mut s = Swings::default();
        assert_eq!(
            s.check(9, 1, anim(5, 7000, 5.0), [0.0; 3], &[[0.5, 0.0, 0.0]], None),
            None
        );
    }
}
