//! The thing that stops a bad animation from ending the session.
//!
//! # Why this exists at all
//!
//! The offline classifier gates on everything it can see: a transition consumes the event, the
//! target state has a clip, the TimeAct opens a damage window, the behaviour row resolves. What it
//! cannot see is whether the animation TERMINATES for a creature nobody is steering. Some states
//! only exit on a condition the AI would have satisfied -- and the AI is exactly what possession
//! turned off, because `[vt+0x48] UpdateAi` is no-oped. A state like that leaves the player stuck
//! in a pose with no way out, which ends the session rather than merely disappointing them.
//!
//! So the classifier is allowed to be wrong, and this catches it: non-neutral, no root motion, no
//! input consumed, for long enough that nothing else explains it. The animation is then forced
//! back to idle and written into `er-npc-possess.derived.toml` as `unusable`, so the SAME move
//! cannot cost the player a second session. The classifier heals from its own failures rather than
//! needing a corpus change.
//!
//! # Why all three conditions, and not just the timer
//!
//! Each one alone has a legitimate long case. A slow wind-up is non-neutral for seconds. A stance
//! or a charge has no root motion by design. A player who put the pad down is consuming no input.
//! Only the conjunction -- animating, going nowhere, and nobody asking for anything -- has no
//! innocent reading.

// Pure state machine over observations; ungated so `cargo test` proves it on the host.
#![cfg_attr(not(windows), allow(dead_code))]

/// Below this, an animation id is idle, locomotion or a turn -- i.e. the creature is available for
/// input again. The attack band starts at 3000 and the generator ships nothing below it.
pub(crate) const NEUTRAL_ANIMATION_CEILING: i32 = 3000;

/// Root-motion magnitude squared under which the creature counts as going nowhere. Squared to
/// avoid a square root on a per-frame path; `0.01` is a tenth of a unit per frame.
const STILL_ROOT_MOTION_SQUARED: f32 = 0.01;

/// What the driver saw this frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Sample {
    /// The animation the TimeAct queue says is playing, from
    /// `CSChrTimeActModule::anim_queue[read_idx].anim_id`.
    pub(crate) animation: i32,
    /// `CSChrBehaviorModule::root_motion`, squared magnitude.
    pub(crate) root_motion_squared: f32,
    /// Did the engine ACT on the player's input this frame -- did a request actually land?
    ///
    /// CONSUMED, not held, and the distinction is the whole value of this field. Reading it as
    /// "the player is touching the controller" hands the softlock case a free pass: somebody
    /// stuck in a pose mashes and holds, which would reset the timer on every frame and mean the
    /// watchdog could never fire in the one situation it exists for. Input that produced nothing
    /// is evidence OF being stuck, not evidence against it.
    pub(crate) input_consumed: bool,
    pub(crate) now_ms: u64,
}

impl Sample {
    const fn is_neutral(self) -> bool {
        self.animation < NEUTRAL_ANIMATION_CEILING
    }

    fn is_going_nowhere(self) -> bool {
        self.root_motion_squared.is_finite() && self.root_motion_squared < STILL_ROOT_MOTION_SQUARED
    }
}

/// What the watchdog wants done.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Verdict {
    /// Nothing to do.
    Fine,
    /// The creature came back to a neutral state; the dispatcher's combo cursors should reset.
    ReturnedToNeutral,
    /// Stuck. Force idle, and deny this animation for the rest of the session.
    ForceIdle {
        /// The animation to fire to get out -- always the idle clip.
        idle: i32,
        /// The animation that stuck, to be written into the derived file as `unusable`. This is
        /// the FIRED id, not the played one, because that is what the config would have to name.
        blame: i32,
    },
}

/// One possession's stuck-detector.
#[derive(Clone, Debug)]
pub(crate) struct Watchdog {
    /// How long the three conditions must hold together before forcing idle.
    threshold_ms: u64,
    /// The animation to request to return to idle. `W_Event0000` -> `a000_000000`.
    idle_animation: i32,
    /// The move currently being watched, and when it stopped looking healthy.
    armed: Option<Armed>,
    /// Was the previous sample non-neutral? Used to fire [`Verdict::ReturnedToNeutral`] exactly
    /// once per return rather than on every neutral frame.
    was_busy: bool,
}

#[derive(Clone, Copy, Debug)]
struct Armed {
    /// The id the dispatcher fired, which is what the derived file has to name.
    fired: i32,
    /// The first moment all three stuck conditions held. Cleared the moment any of them stops.
    suspect_since_ms: Option<u64>,
}

impl Watchdog {
    pub(crate) const fn new(threshold_ms: u64, idle_animation: i32) -> Self {
        Self {
            threshold_ms,
            idle_animation,
            armed: None,
            was_busy: false,
        }
    }

    /// A move was just requested. Watch it.
    pub(crate) const fn armed_with(&mut self, fired: i32) {
        self.armed = Some(Armed {
            fired,
            suspect_since_ms: None,
        });
        self.was_busy = true;
    }

    /// Test-only: the driver never asks, because [`Self::observe`] already handles the unarmed
    /// case. It exists so the tests can assert that a verdict really disarmed rather than
    /// inferring it from the next call's behaviour.
    #[cfg(test)]
    pub(crate) const fn is_armed(&self) -> bool {
        self.armed.is_some()
    }

    /// One frame. Called whether or not anything was fired.
    pub(crate) fn observe(&mut self, sample: Sample) -> Verdict {
        if sample.is_neutral() {
            // Whatever was playing has finished. This is the ordinary exit and it disarms.
            self.armed = None;
            if core::mem::replace(&mut self.was_busy, false) {
                return Verdict::ReturnedToNeutral;
            }
            return Verdict::Fine;
        }
        self.was_busy = true;
        let Some(armed) = self.armed.as_mut() else {
            // Non-neutral without anything of ours in flight: a hit reaction, a death, a
            // gimmick the world started. Not ours to police -- forcing idle out of a stagger
            // would be a worse bug than the one this guards against.
            return Verdict::Fine;
        };
        if !sample.is_going_nowhere() || sample.input_consumed {
            armed.suspect_since_ms = None;
            return Verdict::Fine;
        }
        let since = *armed.suspect_since_ms.get_or_insert(sample.now_ms);
        if sample.now_ms.saturating_sub(since) < self.threshold_ms {
            return Verdict::Fine;
        }
        let blame = armed.fired;
        self.armed = None;
        Verdict::ForceIdle {
            idle: self.idle_animation,
            blame,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const THRESHOLD_MS: u64 = 4000;
    const IDLE: i32 = 0;

    const fn stuck(now_ms: u64) -> Sample {
        Sample {
            animation: 3005,
            root_motion_squared: 0.0,
            input_consumed: false,
            now_ms,
        }
    }

    fn watchdog() -> Watchdog {
        let mut dog = Watchdog::new(THRESHOLD_MS, IDLE);
        dog.armed_with(3005);
        dog
    }

    #[test]
    fn a_frozen_animation_is_forced_back_to_idle_and_blamed() {
        let mut dog = watchdog();
        assert_eq!(dog.observe(stuck(0)), Verdict::Fine);
        assert_eq!(dog.observe(stuck(THRESHOLD_MS - 1)), Verdict::Fine);
        assert_eq!(
            dog.observe(stuck(THRESHOLD_MS)),
            Verdict::ForceIdle {
                idle: IDLE,
                blame: 3005
            }
        );
        assert!(!dog.is_armed(), "firing the verdict must disarm");
    }

    #[test]
    fn root_motion_alone_is_enough_to_clear_the_suspicion() {
        let mut dog = watchdog();
        assert_eq!(dog.observe(stuck(0)), Verdict::Fine);
        let moving = Sample {
            root_motion_squared: 1.0,
            ..stuck(1000)
        };
        assert_eq!(dog.observe(moving), Verdict::Fine);
        // The clock restarts from the next suspect frame, not from arming.
        assert_eq!(dog.observe(stuck(2000)), Verdict::Fine);
        assert_eq!(dog.observe(stuck(2000 + THRESHOLD_MS - 1)), Verdict::Fine);
        assert_eq!(
            dog.observe(stuck(2000 + THRESHOLD_MS)),
            Verdict::ForceIdle {
                idle: IDLE,
                blame: 3005
            }
        );
    }

    /// THE DEFECT THIS FIELD'S DEFINITION EXISTS TO CLOSE. A player who is genuinely stuck mashes
    /// buttons; if "input" meant "a button is down" rather than "a request landed", every one of
    /// those presses would reset the timer and the watchdog could never fire in the exact
    /// situation it is for.
    #[test]
    fn mashing_at_a_softlock_does_not_hold_the_watchdog_off() {
        let mut dog = watchdog();
        for step in 0..20 {
            // Buttons going down every frame, and NOTHING landing -- which is what being stuck
            // looks like from the driver's side, because the request slot never clears.
            let mashing = Sample {
                input_consumed: false,
                ..stuck(step * 500)
            };
            if let Verdict::ForceIdle { blame, .. } = dog.observe(mashing) {
                assert_eq!(blame, 3005);
                return;
            }
        }
        panic!("the watchdog never fired while the player mashed at a frozen animation");
    }

    #[test]
    fn input_alone_is_enough_to_clear_the_suspicion() {
        let mut dog = watchdog();
        assert_eq!(dog.observe(stuck(0)), Verdict::Fine);
        let asking = Sample {
            input_consumed: true,
            ..stuck(3999)
        };
        assert_eq!(dog.observe(asking), Verdict::Fine);
        assert_eq!(
            dog.observe(stuck(3999 + THRESHOLD_MS)),
            Verdict::Fine,
            "the timer restarted, so one threshold from the CLEARED frame is not yet enough"
        );
    }

    #[test]
    fn a_long_but_moving_animation_is_never_forced() {
        let mut dog = watchdog();
        for step in 0..100 {
            let moving = Sample {
                root_motion_squared: 0.5,
                ..stuck(step * 1000)
            };
            assert_eq!(dog.observe(moving), Verdict::Fine);
        }
    }

    #[test]
    fn returning_to_neutral_disarms_and_reports_it_once() {
        let mut dog = watchdog();
        assert_eq!(dog.observe(stuck(0)), Verdict::Fine);
        let idle = Sample {
            animation: 0,
            ..stuck(100)
        };
        assert_eq!(dog.observe(idle), Verdict::ReturnedToNeutral);
        assert!(!dog.is_armed());
        assert_eq!(
            dog.observe(idle),
            Verdict::Fine,
            "the return is an edge, not a level -- the dispatcher must not be reset every frame"
        );
    }

    #[test]
    fn a_stagger_nobody_asked_for_is_not_policed() {
        // Never armed: the creature is in a hit reaction the world started.
        let mut dog = Watchdog::new(THRESHOLD_MS, IDLE);
        for step in 0..20 {
            let reacting = Sample {
                animation: 7010,
                ..stuck(step * 1000)
            };
            assert_eq!(
                dog.observe(reacting),
                Verdict::Fine,
                "forcing idle out of a hit reaction would be a worse bug than the one this guards"
            );
        }
    }

    #[test]
    fn the_neutral_ceiling_matches_the_attack_band_the_generator_ships() {
        // The generator's ATTACK_BAND starts at 3000; anything the table offers is at or above it,
        // so anything below is by construction not one of ours.
        assert_eq!(NEUTRAL_ANIMATION_CEILING, 3000);
        for chr in crate::moveset::table::chr_ids() {
            let Some(moveset) = crate::moveset::table::lookup(chr) else {
                continue;
            };
            for entry in &moveset.moves {
                assert!(
                    entry.fire >= NEUTRAL_ANIMATION_CEILING,
                    "c{chr} ships {} below the neutral ceiling, so firing it would look neutral \
                     and the watchdog would never arm on it",
                    entry.fire
                );
            }
        }
    }
}
