//! A companion that cannot get back to the player, or to its enemy: `LAB_WORLD.tt_stuck_p` and
//! `LAB_WORLD.tt_stuck_e`, read by the turtles' brain to jump instead of walking.
//!
//! A port of the lab's `stuckCheck` (`scripts/frida/spawn-npc.js` on `feat/ai-lab-turtles`). It
//! judges each companion by its own movement over the last [`STUCK_MS`], which a distance alone
//! cannot: measured 2026-10-06, one stood about 66 m away for minutes while its approach goal kept
//! running, and on 2026-10-07 the brain's own distance-only fallback fired unstick jumps at 6-10 m
//! while the squad was following a moving player. Stuck means either:
//!
//! - pinned: more than [`STUCK_GAP`] m from the player and moved less than [`STUCK_MOVE`] m;
//! - losing: more than [`STUCK_FAR`] m away and no [`STUCK_GAIN`] m closer while the player, who
//!   could be running off, moved less than [`STUCK_MOVE`];
//!
//! and, toward its enemy, more than [`STUCK_ENEMY_GAP`] m from it and either not moving or not
//! closing while the enemy stays put. Each one is named by its distance to the player (or to its
//! enemy), which is a number the brain can work out for itself. The thresholds are the lab's,
//! tightened there on 2026-10-06 ("far too loose").

#![cfg_attr(not(windows), allow(dead_code))]

use std::collections::HashMap;

use crate::world::{distance, round_to};

pub(crate) const STUCK_MS: u64 = 1500;
pub(crate) const STUCK_GAP: f64 = 5.0;
pub(crate) const STUCK_FAR: f64 = 15.0;
pub(crate) const STUCK_MOVE: f64 = 1.0;
pub(crate) const STUCK_GAIN: f64 = 1.5;
pub(crate) const CHECK_MS: u64 = 250;
/// Beyond melee reach plus a margin: closer than this it is fighting, not travelling.
pub(crate) const STUCK_ENEMY_GAP: f64 = 3.5;

#[derive(Clone, Copy, Debug)]
struct Sample {
    at: u64,
    pos: [f32; 3],
    me: [f32; 3],
    gap: f64,
    enemy: Option<[f32; 3]>,
    enemy_gap: Option<f64>,
}

/// One companion, sampled.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Member {
    pub(crate) chr: usize,
    pub(crate) pos: [f32; 3],
    /// Its enemy's position, when it has one.
    pub(crate) enemy: Option<[f32; 3]>,
}

/// Why a companion counts as stuck.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Why {
    Pinned,
    Losing,
    Enemy,
}

/// What to send.
#[derive(Debug, PartialEq)]
pub(crate) struct Report {
    pub(crate) to_player: Vec<f64>,
    pub(crate) to_enemy: Vec<f64>,
    /// Set when who is stuck, and why, changed since the last report.
    pub(crate) changed: Option<Vec<(usize, Why)>>,
}

#[derive(Default)]
pub(crate) struct Stuck {
    trails: HashMap<usize, Vec<Sample>>,
    sent: Option<Vec<(usize, Why)>>,
}

impl Stuck {
    /// One check at `now` (ms) with the player at `me`. `None` when nothing is to be sent.
    pub(crate) fn update(&mut self, now: u64, me: [f32; 3], members: &[Member]) -> Option<Report> {
        self.trails
            .retain(|chr, _| members.iter().any(|m| m.chr == *chr));
        let mut to_player = Vec::new();
        let mut to_enemy = Vec::new();
        let mut why = Vec::new();
        for m in members {
            let gap = distance(m.pos, me);
            let trail = self.trails.entry(m.chr).or_default();
            trail.retain(|s| now.saturating_sub(s.at) <= STUCK_MS + CHECK_MS);
            trail.push(Sample {
                at: now,
                pos: m.pos,
                me,
                gap,
                enemy: None,
                enemy_gap: None,
            });
            let old = trail[0];
            if now.saturating_sub(old.at) < STUCK_MS {
                continue;
            }
            let moved = distance(m.pos, old.pos);
            let player_moved = distance(me, old.me);
            let pinned = gap > STUCK_GAP && moved < STUCK_MOVE;
            let losing = gap > STUCK_FAR && old.gap - gap < STUCK_GAIN && player_moved < STUCK_MOVE;
            if let Some(enemy) = m.enemy {
                let egap = distance(m.pos, enemy);
                let enemy_moved = old.enemy.map_or(0.0, |was| distance(enemy, was));
                let closed = old.enemy_gap.map_or(f64::INFINITY, |was| was - egap);
                if egap > STUCK_ENEMY_GAP
                    && (moved < STUCK_MOVE || (closed < STUCK_GAIN && enemy_moved < STUCK_MOVE))
                {
                    to_enemy.push(round_to(egap, 2));
                    why.push((m.chr, Why::Enemy));
                }
                if let Some(last) = trail.last_mut() {
                    last.enemy = Some(enemy);
                    last.enemy_gap = Some(egap);
                }
            }
            if !pinned && !losing {
                continue;
            }
            to_player.push(round_to(gap, 2));
            why.push((m.chr, if pinned { Why::Pinned } else { Why::Losing }));
        }
        let changed = self.sent.as_ref() != Some(&why);
        if !changed && to_player.is_empty() && to_enemy.is_empty() {
            return None;
        }
        self.sent = Some(why.clone());
        Some(Report {
            to_player,
            to_enemy,
            changed: changed.then_some(why),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn member(chr: usize, x: f32) -> Member {
        Member {
            chr,
            pos: [x, 0.0, 0.0],
            enemy: None,
        }
    }

    /// Run checks every [`CHECK_MS`] from `from` to `to` with fixed positions.
    fn run(s: &mut Stuck, from: u64, to: u64, me: [f32; 3], members: &[Member]) -> Option<Report> {
        let mut last = None;
        let mut t = from;
        while t <= to {
            last = s.update(t, me, members).or(last);
            t += CHECK_MS;
        }
        last
    }

    #[test]
    fn a_standing_companion_far_from_a_still_player_is_pinned() {
        let mut s = Stuck::default();
        // Nothing before the trail spans STUCK_MS.
        assert_eq!(
            s.update(0, [0.0; 3], &[member(1, 8.0)]),
            Some(Report {
                to_player: vec![],
                to_enemy: vec![],
                changed: Some(vec![]),
            })
        );
        let r = run(&mut s, CHECK_MS, STUCK_MS, [0.0; 3], &[member(1, 8.0)]).unwrap();
        assert_eq!(r.to_player, vec![8.0]);
        assert_eq!(r.changed, Some(vec![(1, Why::Pinned)]));
    }

    #[test]
    fn a_companion_following_a_moving_player_is_not_stuck() {
        // The case the brain-local check got wrong: 8 m behind, but moving with the player.
        let mut s = Stuck::default();
        let mut t = 0;
        let mut flagged = false;
        while t <= 5000 {
            let x = t as f32 / 250.0;
            if let Some(r) = s.update(t, [x + 8.0, 0.0, 0.0], &[member(1, x)]) {
                flagged |= !r.to_player.is_empty();
            }
            t += CHECK_MS;
        }
        assert!(!flagged);
    }

    #[test]
    fn close_to_the_player_is_never_stuck() {
        let mut s = Stuck::default();
        let r = run(&mut s, 0, 3000, [0.0; 3], &[member(1, 3.0)]);
        assert!(r.is_none_or(|r| r.to_player.is_empty()));
    }

    #[test]
    fn far_and_not_gaining_on_a_still_player_is_losing() {
        let mut s = Stuck::default();
        // Moving sideways 1 m per check, so not pinned, but never closer.
        let mut t = 0;
        let mut why = None;
        while t <= 3000 {
            let y = t as f32 / 250.0;
            let m = Member {
                chr: 1,
                pos: [20.0, y, 0.0],
                enemy: None,
            };
            if let Some(r) = s.update(t, [0.0; 3], &[m])
                && let Some(w) = r.changed
            {
                why = Some(w);
            }
            t += CHECK_MS;
        }
        assert_eq!(why, Some(vec![(1, Why::Losing)]));
    }

    #[test]
    fn a_still_companion_short_of_its_still_enemy_is_stuck_on_it() {
        let mut s = Stuck::default();
        let m = Member {
            chr: 1,
            pos: [1.0, 0.0, 0.0],
            enemy: Some([7.0, 0.0, 0.0]),
        };
        let r = run(&mut s, 0, 3000, [0.0; 3], &[m]).unwrap();
        assert_eq!(r.to_enemy, vec![6.0]);
        assert!(r.to_player.is_empty());
    }

    #[test]
    fn in_melee_with_its_enemy_is_not_stuck() {
        let mut s = Stuck::default();
        let m = Member {
            chr: 1,
            pos: [1.0, 0.0, 0.0],
            enemy: Some([3.0, 0.0, 0.0]),
        };
        let r = run(&mut s, 0, 3000, [0.0; 3], &[m]);
        assert!(r.is_none_or(|r| r.to_enemy.is_empty()));
    }

    #[test]
    fn a_cleared_list_is_sent_once() {
        let mut s = Stuck::default();
        run(&mut s, 0, 2000, [0.0; 3], &[member(1, 8.0)]).unwrap();
        // The player walks up to it.
        let r = s.update(2250, [7.0, 0.0, 0.0], &[member(1, 8.0)]).unwrap();
        assert!(r.to_player.is_empty());
        assert_eq!(r.changed, Some(vec![]));
        assert_eq!(s.update(2500, [7.0, 0.0, 0.0], &[member(1, 8.0)]), None);
    }
}
