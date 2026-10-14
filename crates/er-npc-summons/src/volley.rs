//! Permission to throw: `LAB_WORLD.tt_volley`, `tt_volley_hp`, `tt_volley_cats` and
//! `tt_volley_human`, read by the turtles' brain before a throwable volley.
//!
//! A port of the lab's `volleyCheck` (`scripts/frida/spawn-npc.js` on `feat/ai-lab-turtles`; user
//! rule 2026-10-06): the player has held lock-on on the same living enemy for [`VOLLEY_LOCK_MS`].
//! The lock-on target is `PlayerIns+0x6b0`, a handle (`scripts/frida/target-bars-probe.js`). While
//! it holds, `tt_volley` is 1 and `tt_volley_hp` is the enemy's HP; a turtle throws only when its
//! own target's HP equals it, which is how it tells the player's enemy from another. With it go the
//! status categories already running on the enemy (`tt_volley_cats`, so no turtle throws one it
//! would refuse) and whether it is a real player (`tt_volley_human`). Everything is re-sent when
//! any of it changes, and `tt_volley` drops to 0 on unlock, a new target, or the enemy's death.

#![cfg_attr(not(windows), allow(dead_code))]

pub(crate) const VOLLEY_LOCK_MS: u64 = 2000;

/// The player's living lock-on target.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Target {
    pub(crate) handle: u64,
    pub(crate) hp: i32,
    /// Running status categories (`SpEffectParam.spCategory` 10003..=10010), sorted.
    pub(crate) cats: Vec<u16>,
    pub(crate) human: bool,
}

/// What to send.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Facts {
    pub(crate) on: bool,
    pub(crate) hp: i32,
    pub(crate) cats: Vec<u16>,
    pub(crate) human: bool,
}

#[derive(Default)]
pub(crate) struct Volley {
    lock: Option<(u64, u64)>,
    sent: Option<Facts>,
}

impl Volley {
    /// One check at `now` (ms). `None` when nothing changed.
    pub(crate) fn update(&mut self, now: u64, target: Option<&Target>) -> Option<Facts> {
        match target {
            None => self.lock = None,
            Some(t) if self.lock.is_none_or(|(h, _)| h != t.handle) => {
                self.lock = Some((t.handle, now));
            }
            Some(_) => {}
        }
        let on = self
            .lock
            .is_some_and(|(_, since)| now.saturating_sub(since) >= VOLLEY_LOCK_MS);
        let facts = match (on, target) {
            (true, Some(t)) => Facts {
                on: true,
                hp: t.hp,
                cats: t.cats.clone(),
                human: t.human,
            },
            _ => Facts {
                on: false,
                hp: -1,
                cats: Vec::new(),
                human: false,
            },
        };
        if self.sent.as_ref() == Some(&facts) {
            return None;
        }
        self.sent = Some(facts.clone());
        Some(facts)
    }
}

/// The status categories a volley has to respect: the in-progress ones the game refuses a second
/// of (bd `er-status-reproc-lockout-spcategory-2026-09-29`).
pub(crate) fn is_status_category(c: u16) -> bool {
    (10003..=10010).contains(&c)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target(handle: u64, hp: i32) -> Target {
        Target {
            handle,
            hp,
            cats: vec![10004],
            human: false,
        }
    }

    #[test]
    fn a_lock_held_two_seconds_permits_a_volley() {
        let mut v = Volley::default();
        let t = target(7, 500);
        assert_eq!(v.update(0, Some(&t)).map(|f| f.on), Some(false));
        assert_eq!(v.update(1999, Some(&t)), None);
        let f = v.update(2000, Some(&t)).unwrap();
        assert!(f.on);
        assert_eq!(f.hp, 500);
        assert_eq!(f.cats, vec![10004]);
    }

    #[test]
    fn hp_changes_are_resent_and_unlock_drops_it() {
        let mut v = Volley::default();
        v.update(0, Some(&target(7, 500)));
        v.update(2000, Some(&target(7, 500)));
        assert_eq!(
            v.update(2100, Some(&target(7, 400))).map(|f| f.hp),
            Some(400)
        );
        let off = v.update(2200, None).unwrap();
        assert!(!off.on);
        assert_eq!(off.hp, -1);
    }

    #[test]
    fn a_new_target_restarts_the_clock() {
        let mut v = Volley::default();
        v.update(0, Some(&target(7, 500)));
        v.update(2000, Some(&target(7, 500)));
        let f = v.update(2100, Some(&target(8, 500))).unwrap();
        assert!(!f.on);
        assert!(v.update(4100, Some(&target(8, 500))).unwrap().on);
    }

    #[test]
    fn status_categories() {
        assert!(is_status_category(10003));
        assert!(is_status_category(10010));
        assert!(!is_status_category(10011));
    }
}
