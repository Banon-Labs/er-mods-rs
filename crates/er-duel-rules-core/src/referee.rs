//! Which rule breaks become a popup, and the one rule that needs history: blue flasks per round.

use std::collections::{HashMap, HashSet};

use crate::facts::GameFacts;
use crate::player::PlayerSnapshot;
use crate::rules::{CERULEAN_FLASK_LIMIT, Rule, Violation, check};
use crate::tables;

/// A break that has been continuously present, or seen again within this long, does not pop
/// again: a grease on a restricted weapon is one popup, not one per frame. Once it has been gone
/// this long, its return is news.
pub const REARM_MS: u64 = 10_000;

/// One popup.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Alert {
    pub key: u64,
    pub player: String,
    pub is_local: bool,
    pub rule: Rule,
    pub detail: String,
}

#[derive(Default)]
struct Flasks {
    /// A Cerulean flask row was on the player last frame.
    drinking: bool,
    /// Flasks started this round.
    used: u32,
}

#[derive(Default)]
pub struct Referee {
    last_seen: HashMap<(u64, Rule, String), u64>,
    flasks: HashMap<u64, Flasks>,
    alive: HashMap<u64, bool>,
}

impl Referee {
    /// Judge one frame of every player and return the breaks that are new.
    pub fn observe(
        &mut self,
        now_ms: u64,
        players: &[PlayerSnapshot],
        facts: &impl GameFacts,
    ) -> Vec<Alert> {
        let present: HashSet<u64> = players.iter().map(|p| p.key).collect();
        self.alive.retain(|key, _| present.contains(key));
        self.flasks.retain(|key, _| present.contains(key));

        // A round ends when someone dies; everybody's flasks are refilled for the next one.
        let mut round_over = false;
        for p in players {
            let alive = p.hp > 0;
            if self.alive.insert(p.key, alive) == Some(true) && !alive {
                round_over = true;
            }
        }
        if round_over {
            for flasks in self.flasks.values_mut() {
                flasks.used = 0;
            }
        }

        let mut alerts = Vec::new();
        for p in players {
            let mut violations = check(p, facts);
            if let Some(v) = self.count_flask(p) {
                violations.push(v);
            }
            for v in violations {
                let key = (p.key, v.rule, v.detail.clone());
                let fresh = self
                    .last_seen
                    .get(&key)
                    .is_none_or(|&last| now_ms.saturating_sub(last) >= REARM_MS);
                self.last_seen.insert(key, now_ms);
                if fresh {
                    alerts.push(Alert {
                        key: p.key,
                        player: p.name.clone(),
                        is_local: p.is_local,
                        rule: v.rule,
                        detail: v.detail,
                    });
                }
            }
        }
        self.last_seen
            .retain(|_, &mut last| now_ms.saturating_sub(last) < REARM_MS);
        alerts
    }

    fn count_flask(&mut self, p: &PlayerSnapshot) -> Option<Violation> {
        let drinking = p
            .speffects
            .iter()
            .any(|id| tables::CERULEAN_FLASK.contains(id));
        let flasks = self.flasks.entry(p.key).or_default();
        if drinking && !flasks.drinking {
            flasks.used += 1;
        }
        flasks.drinking = drinking;
        (flasks.used > CERULEAN_FLASK_LIMIT).then(|| Violation {
            rule: Rule::CeruleanFlasks,
            detail: format!("Flask of Cerulean Tears number {} this round", flasks.used),
        })
    }

    /// Blue flasks `key` has started this round.
    pub fn flasks_used(&self, key: u64) -> u32 {
        self.flasks.get(&key).map_or(0, |f| f.used)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::facts::test_support::Table;
    use crate::player::NO_ITEM;
    use crate::rules::RUNE_LEVEL;

    fn player(key: u64) -> PlayerSnapshot {
        PlayerSnapshot {
            key,
            name: format!("p{key}"),
            level: RUNE_LEVEL,
            armor: [1, NO_ITEM, NO_ITEM, NO_ITEM],
            ..PlayerSnapshot::default()
        }
    }

    fn facts() -> Table {
        let mut table = Table::default();
        table.armor.insert(1, 40.0);
        table
    }

    #[test]
    fn a_lasting_break_pops_once_and_again_only_after_it_was_gone() {
        let facts = facts();
        let mut referee = Referee::default();
        let mut p = player(7);
        p.level = 150;
        assert_eq!(referee.observe(0, &[p.clone()], &facts).len(), 1);
        for t in (16..30_000).step_by(16) {
            assert!(referee.observe(t, &[p.clone()], &facts).is_empty(), "t={t}");
        }
        p.level = RUNE_LEVEL;
        referee.observe(30_000, &[p.clone()], &facts);
        p.level = 150;
        assert!(
            referee.observe(35_000, &[p.clone()], &facts).is_empty(),
            "gone only 5 s"
        );
        p.level = RUNE_LEVEL;
        referee.observe(36_000, &[p.clone()], &facts);
        p.level = 150;
        assert_eq!(referee.observe(50_000, &[p], &facts).len(), 1);
    }

    #[test]
    fn the_third_blue_flask_of_a_round_breaks_the_rule_and_a_death_refills() {
        let facts = facts();
        let mut referee = Referee::default();
        let flask = tables::CERULEAN_FLASK[0];
        let mut p = player(1);
        let other = player(2);
        let mut now = 0;
        let mut drink = |referee: &mut Referee, p: &mut PlayerSnapshot| {
            p.speffects = vec![flask];
            now += 100;
            let a = referee.observe(now, &[p.clone(), other.clone()], &facts);
            p.speffects.clear();
            now += 100;
            referee.observe(now, &[p.clone(), other.clone()], &facts);
            a
        };
        assert!(drink(&mut referee, &mut p).is_empty());
        assert!(drink(&mut referee, &mut p).is_empty());
        let third = drink(&mut referee, &mut p);
        assert_eq!(third.len(), 1);
        assert_eq!(third[0].rule, Rule::CeruleanFlasks);
        assert_eq!(referee.flasks_used(1), 3);
        // The other player dies: a new round.
        let mut dead = other.clone();
        dead.hp = 0;
        referee.observe(10_000, &[p.clone(), dead], &facts);
        assert_eq!(referee.flasks_used(1), 0);
    }

    #[test]
    fn players_who_leave_are_forgotten() {
        let facts = facts();
        let mut referee = Referee::default();
        let mut p = player(3);
        p.speffects = vec![tables::CERULEAN_FLASK[0]];
        referee.observe(0, &[p], &facts);
        assert_eq!(referee.flasks_used(3), 1);
        referee.observe(16, &[], &facts);
        assert_eq!(referee.flasks_used(3), 0);
    }
}
