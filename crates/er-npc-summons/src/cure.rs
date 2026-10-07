//! Which companion needs a bolus: `LAB_WORLD.tt_cure`, read by the turtles' brain.
//!
//! A port of the lab's `statusCheck` (`scripts/frida/spawn-npc.js` on `feat/ai-lab-turtles`).
//! Every [`CHECK_MS`], a companion with poison or rot running (status category 10004 / 10005) is
//! cured of it; otherwise one whose poison, rot or bleed gauge is at least [`STATUS_FILL`] full is
//! dosed against the fullest. The gauges are `CSChrResistModule` (modules `+0x20`): each counts
//! down from the resistance and procs below 1 (`docs/er-mechanics/status.md` s2 on that branch).
//!
//! The brain cannot be told which character it is, so each request is the triple (distance to the
//! player, HP share, goods id), flattened, and the brain matches the first two against itself.
//! Re-sent every check while anyone needs a cure, since distances move.

#![cfg_attr(not(windows), allow(dead_code))]

use crate::world::round_to;

pub(crate) const CHECK_MS: u64 = 400;
pub(crate) const STATUS_FILL: f64 = 0.5;
/// Neutralizing, Preserving and Stanching Boluses: poison, rot and bleed, by gauge index.
const GAUGE_CURE: [(usize, u32); 3] = [(0, 900), (1, 940), (2, 910)];
/// A running poison or rot, by status category.
const RUNNING_CURE: [(u16, u32); 2] = [(10004, 900), (10005, 940)];

/// The bolus `chr` needs, from its running status categories (in the order they were found) and
/// its gauges.
pub(crate) fn cure_for(cats: &[u16], gauges: Option<&[(i32, i32); 7]>) -> Option<u32> {
    for c in cats {
        if let Some(&(_, item)) = RUNNING_CURE.iter().find(|(cat, _)| cat == c) {
            return Some(item);
        }
    }
    let gauges = gauges?;
    let mut worst = STATUS_FILL;
    let mut item = None;
    for (i, cure) in GAUGE_CURE {
        let (left, max) = gauges[i];
        let filled = if max > 0 {
            1.0 - f64::from(left) / f64::from(max)
        } else {
            0.0
        };
        if filled >= worst {
            worst = filled;
            item = Some(cure);
        }
    }
    item
}

/// One companion's request.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Need {
    pub(crate) chr: usize,
    pub(crate) to_player: f64,
    pub(crate) hp_share: f64,
    pub(crate) item: u32,
}

#[derive(Default)]
pub(crate) struct Cure {
    sent: Option<Vec<(usize, u32)>>,
}

impl Cure {
    /// The flattened list to send, and whether who needs what changed.
    pub(crate) fn update(&mut self, needs: &[Need]) -> Option<(Vec<f64>, bool)> {
        let key: Vec<(usize, u32)> = needs.iter().map(|n| (n.chr, n.item)).collect();
        let changed = self.sent.as_ref() != Some(&key);
        if !changed && needs.is_empty() {
            return None;
        }
        self.sent = Some(key);
        let flat = needs
            .iter()
            .flat_map(|n| {
                [
                    round_to(n.to_player, 1),
                    round_to(n.hp_share, 2),
                    f64::from(n.item),
                ]
            })
            .collect();
        Some((flat, changed))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gauges(fill: [(i32, i32); 3]) -> [(i32, i32); 7] {
        let mut g = [(100, 100); 7];
        g[..3].copy_from_slice(&fill);
        g
    }

    #[test]
    fn a_running_status_wins_over_build_up() {
        let g = gauges([(10, 100), (100, 100), (100, 100)]);
        assert_eq!(cure_for(&[10005], Some(&g)), Some(940));
        assert_eq!(cure_for(&[10003, 10004], Some(&g)), Some(900));
    }

    #[test]
    fn the_fullest_gauge_past_half_picks_the_bolus() {
        assert_eq!(cure_for(&[], Some(&gauges([(100, 100); 3]))), None);
        assert_eq!(
            cure_for(&[], Some(&gauges([(60, 100), (100, 100), (40, 100)]))),
            Some(910)
        );
        assert_eq!(
            cure_for(&[], Some(&gauges([(50, 100), (100, 100), (100, 100)]))),
            Some(900)
        );
        // An unset resistance counts as empty.
        assert_eq!(
            cure_for(&[], Some(&gauges([(0, 0), (100, 100), (100, 100)]))),
            None
        );
    }

    #[test]
    fn requests_are_flat_triples_resent_while_anyone_needs_one() {
        let mut c = Cure::default();
        let need = Need {
            chr: 1,
            to_player: 3.12,
            hp_share: 0.456,
            item: 900,
        };
        assert_eq!(c.update(&[need]), Some((vec![3.1, 0.46, 900.0], true)));
        assert_eq!(c.update(&[need]), Some((vec![3.1, 0.46, 900.0], false)));
        assert_eq!(c.update(&[]), Some((vec![], true)));
        assert_eq!(c.update(&[]), None);
    }
}
