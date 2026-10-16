//! Which rule breaks become a popup, and the rules that need history: blue flasks per round, and
//! a build that changes once two players share a world.

use std::collections::{HashMap, HashSet};

use crate::facts::{CATEGORY_LEFT_WEAPON_BUFF, CATEGORY_RIGHT_WEAPON_BUFF, GameFacts};
use crate::player::{ATTRIBUTE_NAMES, Hand, PlayerSnapshot};
use crate::rules::{CERULEAN_FLASK_LIMIT, Rule, Violation, check, violation};
use crate::tables;

/// A build as the ruleset means it: level, attributes and everything equipped.
#[derive(Clone, Debug, PartialEq)]
struct Build {
    level: u32,
    attributes: [u32; 8],
    weapons_left: [i32; 3],
    weapons_right: [i32; 3],
    armor: [i32; 4],
    talismans: [i32; 4],
    physick: [i32; 3],
}

impl Build {
    fn of(p: &PlayerSnapshot) -> Self {
        Self {
            level: p.level,
            attributes: p.attributes,
            weapons_left: p.weapons_left,
            weapons_right: p.weapons_right,
            armor: p.armor,
            talismans: p.talismans,
            physick: p.physick,
        }
    }
}

/// The build a player entered the match with.
struct Entered {
    build: Build,
    /// The armour has differed from `build.armor` since the last time it matched.
    armor_changed: bool,
}

/// How long a weapon buff may outlive a change of the weapon in its hand. The game strips the
/// hand's buffs in the same pre-update pass that takes the new weapon (1.16.2 `FUN_140660120` ->
/// `FUN_1404f6c00` right, `RemoveWepParam2And6SpEffects` left), so anything past a few frames of
/// read order was carried across: a buff transfer.
pub const BUFF_TRANSFER_GRACE_MS: u64 = 250;

/// How long a player has, from entering the match or respawning, to light a Rune Arc before the
/// Better Multiplayer Rune Arc rule calls it missing. A death puts the arc out, so every round
/// starts without one.
pub const RUNE_ARC_GRACE_MS: u64 = 20_000;

/// One hand of the local player: its weapon and weapon buffs last frame.
#[derive(Default)]
struct HandBuffs {
    weapon: i32,
    buffs: Vec<i32>,
    /// The buffs the hand held when its weapon last changed, until judged.
    carried: Vec<i32>,
    changed_at: u64,
}

/// `slot: old -> new` for every slot of `name` that differs.
fn slot_changes<const N: usize>(name: &str, old: &[i32; N], new: &[i32; N]) -> Vec<String> {
    (0..N)
        .filter(|&i| old[i] != new[i])
        .map(|i| format!("{name} {}: {} -> {}", i + 1, old[i], new[i]))
        .collect()
}

/// A lasting break -- an effect on the player, a grip, equipment in a match -- that has been
/// continuously present, or seen again within this long, is the same break: a grease on a
/// restricted weapon is one row, not one per frame. Once it has been gone this long, its return is
/// a new break with its own row. Long enough to ride out a frame or two an effect goes unseen, short
/// enough that a second use is its own row. An event ([`crate::rules::Violation::event`]) is never
/// merged at all.
pub const REARM_MS: u64 = 1_000;

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
    /// When each player last came alive: entered the world or respawned.
    alive_since: HashMap<u64, u64>,
    /// Builds fixed at the first frame a player shared the world with an opponent.
    entered: HashMap<u64, Entered>,
    /// The local player's hands, by `Hand as usize`. Remote players are left out: their buff
    /// removal arrives over the network, and how late is not measured.
    hands: [Option<HandBuffs>; 2],
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
        // "Switching builds is prohibited once you enter the same world as your opponent": with
        // nobody to fight there is no match, and the next opponent starts a new one.
        let in_match = players.len() >= 2;
        if in_match {
            self.entered.retain(|key, _| present.contains(key));
        } else {
            self.entered.clear();
        }

        // A round ends when someone dies; everybody's flasks are refilled for the next one.
        self.alive_since.retain(|key, _| present.contains(key));
        let mut round_over = false;
        for p in players {
            let alive = p.hp > 0;
            let was = self.alive.insert(p.key, alive);
            if was == Some(true) && !alive {
                round_over = true;
            }
            if alive && was != Some(true) {
                self.alive_since.insert(p.key, now_ms);
            }
        }
        // Better Multiplayer lets every player light a Rune Arc, and the ruleset's reason for
        // allowing it is that a duel where only one side has one is not fair. A session is BMP when
        // the local game has the module loaded: every player in it runs BMP.
        let better_multiplayer = players
            .iter()
            .any(|p| p.local_settings.is_some_and(|s| s.better_multiplayer));
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
            if in_match {
                violations.extend(self.build_changes(p));
            }
            if in_match
                && better_multiplayer
                && p.hp > 0
                && !p.rune_arc_active()
                && self
                    .alive_since
                    .get(&p.key)
                    .is_some_and(|&since| now_ms.saturating_sub(since) >= RUNE_ARC_GRACE_MS)
            {
                violations.push(violation(
                    Rule::RuneArc,
                    format!(
                        "no active Rune Arc {} s into the round",
                        RUNE_ARC_GRACE_MS / 1000
                    ),
                ));
            }
            if p.is_local {
                violations.extend(self.carried_buffs(now_ms, p, facts));
            }
            for v in violations {
                if v.on_equipment && !in_match {
                    continue;
                }
                let fresh = if v.event {
                    true
                } else {
                    let key = (p.key, v.rule, v.detail.clone());
                    let fresh = self
                        .last_seen
                        .get(&key)
                        .is_none_or(|&last| now_ms.saturating_sub(last) >= REARM_MS);
                    self.last_seen.insert(key, now_ms);
                    fresh
                };
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
        (flasks.used > CERULEAN_FLASK_LIMIT).then(|| {
            violation(
                Rule::CeruleanFlasks,
                format!("Flask of Cerulean Tears number {} this round", flasks.used),
            )
        })
    }

    /// A weapon buff the local player kept across a change of the weapon in its hand.
    fn carried_buffs(
        &mut self,
        now_ms: u64,
        p: &PlayerSnapshot,
        facts: &impl GameFacts,
    ) -> Vec<Violation> {
        let mut out = Vec::new();
        for hand in [Hand::Left, Hand::Right] {
            let buffs: Vec<i32> = p
                .speffects
                .iter()
                .copied()
                .filter(|&id| {
                    facts.effect(id).is_some_and(|e| {
                        e.category
                            == match hand {
                                Hand::Left => CATEGORY_LEFT_WEAPON_BUFF,
                                Hand::Right => CATEGORY_RIGHT_WEAPON_BUFF,
                            }
                    })
                })
                .collect();
            let weapon = p.active(hand);
            let h = self.hands[hand as usize].get_or_insert_with(|| HandBuffs {
                weapon,
                ..HandBuffs::default()
            });
            if weapon != h.weapon {
                h.carried = std::mem::take(&mut h.buffs);
                h.changed_at = now_ms;
            }
            if !h.carried.is_empty()
                && now_ms.saturating_sub(h.changed_at) >= BUFF_TRANSFER_GRACE_MS
            {
                if h.carried.iter().any(|id| buffs.contains(id)) {
                    out.push(violation(
                        Rule::BuffTransfer,
                        format!("weapon buff kept across a weapon change to {weapon}"),
                    ));
                }
                h.carried.clear();
            }
            h.weapon = weapon;
            h.buffs = buffs;
        }
        out
    }

    /// What has changed in `p`'s build since they entered the match.
    fn build_changes(&mut self, p: &PlayerSnapshot) -> Vec<Violation> {
        // Level 0 is a remote player whose data has not arrived yet: not a build to fix.
        if p.level == 0 {
            return Vec::new();
        }
        let now = Build::of(p);
        let Some(entered) = self.entered.get_mut(&p.key) else {
            self.entered.insert(
                p.key,
                Entered {
                    build: now,
                    armor_changed: false,
                },
            );
            return Vec::new();
        };
        let old = &entered.build;
        let mut out = Vec::new();
        let mut stats: Vec<String> = (0..ATTRIBUTE_NAMES.len())
            .filter(|&i| old.attributes[i] != now.attributes[i])
            .map(|i| {
                format!(
                    "{} {} -> {}",
                    ATTRIBUTE_NAMES[i], old.attributes[i], now.attributes[i]
                )
            })
            .collect();
        if old.level != now.level {
            stats.insert(0, format!("level {} -> {}", old.level, now.level));
        }
        if !stats.is_empty() {
            out.push(violation(Rule::Respec, stats.join(", ")));
        }
        let mut gear = slot_changes("right weapon", &old.weapons_right, &now.weapons_right);
        gear.extend(slot_changes(
            "left weapon",
            &old.weapons_left,
            &now.weapons_left,
        ));
        gear.extend(slot_changes("talisman", &old.talismans, &now.talismans));
        gear.extend(slot_changes("tear", &old.physick, &now.physick));
        let armor = slot_changes("armor", &old.armor, &now.armor);
        if armor.is_empty() {
            // Pieces taken off and put back: the poise reset the ruleset blacklists.
            if entered.armor_changed {
                entered.armor_changed = false;
                out.push(violation(
                    Rule::ArmorSwapPoiseReset,
                    "armor taken off and put back on",
                ));
            }
        } else {
            entered.armor_changed = true;
            gear.extend(armor);
        }
        if !gear.is_empty() {
            out.push(violation(Rule::SwitchingBuilds, gear.join(", ")));
        }
        out
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
        let opponent = player(8);
        p.level = 150;
        let both = |p: &PlayerSnapshot| [p.clone(), opponent.clone()];
        assert_eq!(referee.observe(0, &both(&p), &facts).len(), 1);
        for t in (16..30_000).step_by(16) {
            assert!(referee.observe(t, &both(&p), &facts).is_empty(), "t={t}");
        }
        p.level = RUNE_LEVEL;
        referee.observe(30_000, &both(&p), &facts);
        p.level = 150;
        assert!(
            referee.observe(30_500, &both(&p), &facts).is_empty(),
            "gone only 0.5 s"
        );
        p.level = RUNE_LEVEL;
        referee.observe(31_000, &both(&p), &facts);
        p.level = 150;
        assert_eq!(referee.observe(33_000, &both(&p), &facts).len(), 1);
    }

    #[test]
    fn every_use_of_a_banned_skill_is_its_own_break() {
        let facts = facts();
        let mut referee = Referee::default();
        let mut p = player(7);
        // a<600 + swordArtsTypeNew>_<clip>: Bloodhound's Step (156).
        let bloodhounds_step = (600 + tables::skill::BLOODHOUNDS_STEP) * 1_000_000;
        let mut uses = 0;
        for t in [0, 300, 600] {
            p.new_anims = vec![bloodhounds_step];
            uses += referee.observe(t, &[p.clone()], &facts).len();
            p.new_anims.clear();
            referee.observe(t + 16, &[p.clone()], &facts);
        }
        assert_eq!(uses, 3);
    }

    #[test]
    fn equipment_is_judged_once_an_opponent_shares_the_world_and_acts_always() {
        let facts = facts();
        let mut referee = Referee::default();
        // Measured 2026-10-16: a base RL9 character judged on its first frame in the world, 176 ms
        // before a build import made it RL125.
        let mut p = player(7);
        p.level = 9;
        p.is_local = true;
        assert!(referee.observe(0, &[p.clone()], &facts).is_empty());
        p.speffects = vec![tables::SPIRIT_ASH_TRIGGERS[0].id];
        let alone = referee.observe(100, &[p.clone()], &facts);
        assert_eq!(
            alone.iter().map(|a| a.rule).collect::<Vec<_>>(),
            [Rule::SpiritAshes]
        );
        p.speffects.clear();
        let met = referee.observe(200, &[p.clone(), player(8)], &facts);
        assert_eq!(
            met.iter().map(|a| a.rule).collect::<Vec<_>>(),
            [Rule::RuneLevel]
        );
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

    fn rules_of(alerts: &[Alert]) -> Vec<Rule> {
        alerts.iter().map(|a| a.rule).collect()
    }

    #[test]
    fn a_build_is_fixed_once_an_opponent_shares_the_world() {
        let facts = facts();
        let mut referee = Referee::default();
        let mut p = player(1);
        p.attributes = [40, 20, 25, 20, 40, 9, 9, 7];
        p.weapons_right = [2_000_000, NO_ITEM, NO_ITEM];
        let other = player(2);
        // Alone: no match, so changing everything is preparation.
        referee.observe(0, std::slice::from_ref(&p), &facts);
        p.weapons_right[0] = 3_000_000;
        assert!(
            referee
                .observe(100, std::slice::from_ref(&p), &facts)
                .is_empty()
        );
        // The opponent arrives: this is the build p entered with.
        assert!(
            referee
                .observe(200, &[p.clone(), other.clone()], &facts)
                .is_empty()
        );
        p.weapons_right[1] = 2_000_000;
        let a = referee.observe(300, &[p.clone(), other.clone()], &facts);
        assert_eq!(rules_of(&a), vec![Rule::SwitchingBuilds]);
        assert_eq!(a[0].detail, "right weapon 2: -1 -> 2000000");
        p.weapons_right[1] = NO_ITEM;
        p.attributes[0] = 45;
        let a = referee.observe(400, &[p.clone(), other.clone()], &facts);
        assert_eq!(rules_of(&a), vec![Rule::Respec]);
        assert_eq!(a[0].detail, "Vigor 40 -> 45");
        // The opponent leaves; the next one starts a new match from the build p has now.
        referee.observe(500, std::slice::from_ref(&p), &facts);
        assert!(referee.observe(600, &[p.clone(), other], &facts).is_empty());
    }

    #[test]
    fn armor_taken_off_and_put_back_is_the_poise_reset() {
        let facts = facts();
        let mut referee = Referee::default();
        let mut p = player(1);
        // A second heavy piece, so the swap itself never drops p into a light roll.
        p.armor[1] = 1;
        let other = player(2);
        referee.observe(0, &[p.clone(), other.clone()], &facts);
        p.armor[0] = NO_ITEM;
        let a = referee.observe(100, &[p.clone(), other.clone()], &facts);
        assert_eq!(rules_of(&a), vec![Rule::SwitchingBuilds]);
        p.armor[0] = 1;
        let a = referee.observe(200, &[p.clone(), other.clone()], &facts);
        assert_eq!(rules_of(&a), vec![Rule::ArmorSwapPoiseReset]);
        assert!(referee.observe(300, &[p, other], &facts).is_empty());
    }

    #[test]
    fn a_weapon_buff_kept_across_a_weapon_change_is_a_buff_transfer() {
        let mut facts = facts();
        let buff = 9001;
        facts.effects.insert(
            buff,
            crate::facts::EffectFacts {
                category: CATEGORY_RIGHT_WEAPON_BUFF,
                ..Default::default()
            },
        );
        let mut referee = Referee::default();
        let mut p = player(1);
        p.is_local = true;
        p.weapons_right = [2_000_000, 3_000_000, NO_ITEM];
        p.speffects = vec![buff];
        referee.observe(0, std::slice::from_ref(&p), &facts);
        // The game strips the buff with the change: nothing to report.
        p.right_slot = 1;
        p.speffects.clear();
        referee.observe(16, std::slice::from_ref(&p), &facts);
        assert!(
            referee
                .observe(400, std::slice::from_ref(&p), &facts)
                .is_empty()
        );
        // Back to slot 0, buffed again, then a switch that keeps it.
        p.right_slot = 0;
        p.speffects = vec![buff];
        referee.observe(500, std::slice::from_ref(&p), &facts);
        p.right_slot = 1;
        assert!(
            referee
                .observe(516, std::slice::from_ref(&p), &facts)
                .is_empty(),
            "grace"
        );
        let a = referee.observe(800, std::slice::from_ref(&p), &facts);
        assert_eq!(rules_of(&a), vec![Rule::BuffTransfer]);
    }

    #[test]
    fn in_better_multiplayer_everyone_lights_a_rune_arc() {
        let facts = facts();
        let mut referee = Referee::default();
        let mut me = player(1);
        me.is_local = true;
        me.local_settings = Some(crate::player::LocalSettings {
            better_multiplayer: true,
            target_fps: 60.0,
            ..Default::default()
        });
        me.rune_arc_flag = true;
        let mut other = player(2);
        referee.observe(0, &[me.clone(), other.clone()], &facts);
        assert!(
            referee
                .observe(RUNE_ARC_GRACE_MS - 1, &[me.clone(), other.clone()], &facts)
                .is_empty(),
            "still time to light one"
        );
        let a = referee.observe(RUNE_ARC_GRACE_MS, &[me.clone(), other.clone()], &facts);
        assert_eq!(rules_of(&a), vec![Rule::RuneArc]);
        assert_eq!(a[0].key, 2);
        // A Great Rune effect row counts as lit, the flag or not.
        other.speffects = vec![tables::RUNE_ARC_EFFECTS[0].id];
        assert!(
            referee
                .observe(
                    RUNE_ARC_GRACE_MS + 100,
                    &[me.clone(), other.clone()],
                    &facts
                )
                .is_empty()
        );
        // Outside Better Multiplayer nobody is asked to.
        me.local_settings = None;
        other.speffects.clear();
        assert!(
            referee
                .observe(RUNE_ARC_GRACE_MS * 3, &[me, other], &facts)
                .is_empty()
        );
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
