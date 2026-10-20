//! The player's stance and who the enemies have picked: `LAB_WORLD.tt_crouch`, `tt_veil` and
//! `tt_targeted_p`. All three replan the brain's AIs when they change, as in the lab.
//!
//! Ports of the lab's `crouchCheck`, `veilCheck` and `targetedCheck` (`scripts/frida/spawn-npc.js`
//! on `feat/ai-lab-turtles`):
//!
//! - crouching: SpEffect [`STEALTH`] 8001 "[HKS] Is Stealth", measured 2026-10-06 to appear with
//!   the crouch animations 10390000 / 10300000 and leave with the stand-up 10390001. Alone it
//!   flapped while moving crouched (every 2-3 s in crouch-move 10320110), so the crouch animation
//!   family 103xxxxx counts too, minus the stand-up, and a change must hold for
//!   [`CROUCH_STEADY_MS`] before it is sent;
//! - Mimic's Veil: the player's SpEffect [`MIMIC_VEIL`] 503040, held until the veil breaks;
//! - targeted: a companion an enemy's AI has picked as its `TARGET_ENE_0` (user rule 2026-10-06:
//!   a turtle an enemy locks onto is free to engage), named by its distance to the player.

#![cfg_attr(not(windows), allow(dead_code))]

pub(crate) const STEALTH: i32 = 8001;
pub(crate) const MIMIC_VEIL: i32 = 503040;
pub(crate) const CROUCH_STEADY_MS: u64 = 400;
const CROUCH_FAMILY: core::ops::RangeInclusive<i32> = 10_300_000..=10_399_999;
const STAND_UP: i32 = 10_390_001;

/// Whether the player is crouched, from their SpEffects and newest animation.
pub(crate) fn crouched(stealth: bool, anim: Option<i32>) -> bool {
    let anim = anim.map_or(-1, |a| a % 100_000_000);
    stealth || (CROUCH_FAMILY.contains(&anim) && anim != STAND_UP)
}

#[derive(Default)]
pub(crate) struct Crouch {
    seen: Option<(bool, u64)>,
    sent: Option<bool>,
}

impl Crouch {
    /// One check at `now` (ms); the value to send when a change has held long enough.
    pub(crate) fn update(&mut self, now: u64, on: bool) -> Option<bool> {
        if self.seen.is_none_or(|(was, _)| was != on) {
            self.seen = Some((on, now));
        }
        let since = self.seen.map_or(now, |(_, at)| at);
        if self.sent == Some(on) || now.saturating_sub(since) < CROUCH_STEADY_MS {
            return None;
        }
        self.sent = Some(on);
        Some(on)
    }
}

/// A value sent only when it changes.
#[derive(Default)]
pub(crate) struct OnChange<T: PartialEq + Clone> {
    sent: Option<T>,
}

impl<T: PartialEq + Clone> OnChange<T> {
    pub(crate) fn update(&mut self, value: T) -> Option<T> {
        if self.sent.as_ref() == Some(&value) {
            return None;
        }
        self.sent = Some(value.clone());
        Some(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crouch_comes_from_stealth_or_the_crouch_animations() {
        assert!(crouched(true, None));
        assert!(crouched(false, Some(10_320_110)));
        assert!(!crouched(false, Some(10_390_001)));
        assert!(!crouched(false, Some(68010)));
        // The section above 1e8 is dropped first, as the lab did.
        assert!(crouched(false, Some(110_300_000)));
    }

    #[test]
    fn a_crouch_change_must_hold_before_it_is_sent() {
        let mut c = Crouch::default();
        assert_eq!(c.update(0, false), None);
        assert_eq!(c.update(400, false), Some(false));
        assert_eq!(c.update(500, true), None);
        // Flapped back before it held: nothing.
        assert_eq!(c.update(600, false), None);
        assert_eq!(c.update(700, true), None);
        assert_eq!(c.update(1100, true), Some(true));
        assert_eq!(c.update(2000, true), None);
    }

    #[test]
    fn on_change_sends_each_value_once() {
        let mut v = OnChange::default();
        assert_eq!(v.update(vec![(1usize, 2usize)]), Some(vec![(1, 2)]));
        assert_eq!(v.update(vec![(1, 2)]), None);
        assert_eq!(v.update(vec![]), Some(vec![]));
    }
}
