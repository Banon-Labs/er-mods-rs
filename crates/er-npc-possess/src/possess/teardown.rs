//! THE ORDER RELEASE HAPPENS IN, as a state machine that cannot be run out of order or stopped
//! half way.
//!
//! # Why the order is not a comment in one function
//!
//! Release runs down three different paths -- the hotkey, the possessed creature dying, and
//! `DLL_PROCESS_DETACH` -- and each one is a place where somebody can write the steps in a
//! plausible order that is subtly the wrong one. Four of the seven have a consequence if they move:
//!
//! * **The camera must be cleared AFTER the player has been moved.** Clearing first gives a
//!   visible frame of the old body standing wherever it was; moving first means the camera snaps
//!   to a player already standing in the right place.
//! * **`ChrCtrl+0x3b0` must be cleared BEFORE anything can tear the character down.**
//!   `ChrCtrl::Unref` compares that slot against zero and **DLPanics** when it is non-null. This
//!   is the step that must run even when every step before it failed.
//! * **A spawned creature is removed AFTER that clear**, for exactly the same reason from the other
//!   side: `WorldChrManImp::RemoveChrIns` hands the character to `CSDelayDeleteMan`, and the
//!   destruction that eventually follows is the thing that runs `ChrCtrl::Unref`. Despawning first
//!   arms the crash the step before it exists to prevent -- on a delay, in a destructor.
//! * **Save suppression is lifted LAST**, because `PlayerIns::UpdateSafePosition` and
//!   `UpdateBlockPosition` write the save's respawn fields the instant that gate opens, and they
//!   must not do it from a position the player is about to leave.
//!
//! # The property that matters more than the order
//!
//! **A failing step does not stop the run.** A release that gives up half way leaves a live
//! `ChrCtrl+0x3b0`, and the next time that character is unloaded the game DLPanics -- so the
//! failure mode of "be careful" is a crash minutes later in an unrelated place. [`Teardown`]
//! therefore records what failed and keeps going, and the tests below pin that.

// Pure state handling; stays ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

/// One step of a release, in the order it must happen.
///
/// The discriminants are the order. `Step::ALL` is the whole sequence and the tests assert it is
/// sorted, so adding a step in the wrong place fails rather than reorders the release.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Step {
    /// Invincibility off, alpha back to opaque, `debugFlags` cleared. The player's own body
    /// becomes an ordinary character again.
    RestoreBody = 0,
    /// Stop writing the player's proxy position every frame. Nothing to undo -- the last write
    /// already landed -- but the per-frame driver has to be told before the player is moved, or it
    /// would put them back on the creature next frame.
    StopColocating = 1,
    /// Put the player on the resolved ground point. Usually a no-op in effect, because
    /// co-location already left them exactly there; it matters when the creature died airborne and
    /// the point resolves to its last grounded position instead.
    MovePlayer = 2,
    /// Clear `WorldChrManDbg+0xb8`. Camera and lock-on return to the real player.
    ClearCameraOverride = 3,
    /// Clear `ChrCtrl+0x3b0` on the creature and give it its AI back. THE STEP THAT MUST HAPPEN.
    ClearManipulatorOverride = 4,
    /// Hand a creature this mod SPAWNED back to the game. A no-op for a creature the map placed.
    ///
    /// **AFTER [`Self::ClearManipulatorOverride`], AND THAT IS A CRASH IF IT MOVES.**
    /// `WorldChrManImp::RemoveChrIns` passes the character to `CSDelayDeleteMan`, whose eventual
    /// destruction runs `ChrCtrl::Unref`, which DLPanics on a non-null `ChrCtrl+0x3b0`. Removing
    /// first therefore arms the exact crash the step before it exists to prevent -- and arms it on
    /// a delay, in a destructor, minutes later.
    DespawnCreature = 5,
    /// Whatever was holding the save off, released. Last, always.
    LiftSaveSuppression = 6,
}

impl Step {
    /// The release, in order.
    pub(crate) const ALL: [Self; 7] = [
        Self::RestoreBody,
        Self::StopColocating,
        Self::MovePlayer,
        Self::ClearCameraOverride,
        Self::ClearManipulatorOverride,
        Self::DespawnCreature,
        Self::LiftSaveSuppression,
    ];

    /// For the log line.
    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::RestoreBody => "restore-body",
            Self::StopColocating => "stop-colocating",
            Self::MovePlayer => "move-player",
            Self::ClearCameraOverride => "clear-camera-override",
            Self::ClearManipulatorOverride => "clear-manipulator-override",
            Self::DespawnCreature => "despawn-creature",
            Self::LiftSaveSuppression => "lift-save-suppression",
        }
    }

    /// Is this a step whose failure leaves the game in a state that will crash later?
    ///
    /// Exactly one qualifies. A failed `RestoreBody` leaves the player invisible until the next
    /// possession, which is bad and survivable; a failed `ClearManipulatorOverride` leaves a
    /// DLPanic armed inside `ChrCtrl::Unref` for whenever that creature is unloaded.
    ///
    /// `DespawnCreature` deliberately does NOT qualify, and the difference is the point: its
    /// failure leaves a live NPC standing in the world, which is visible, survivable, and cleaned
    /// up by the next map load. It gets a [`Self::failure_note`] instead, because "despawn-creature
    /// failed" does not tell the reader what is now standing behind them.
    pub(crate) const fn is_critical(self) -> bool {
        matches!(self, Self::ClearManipulatorOverride)
    }

    /// What the reader needs to know when THIS step fails, spelled out because the consequence is
    /// nowhere near the cause.
    ///
    /// `None` for the steps whose failure is self-explanatory from the name.
    pub(crate) const fn failure_note(self) -> Option<&'static str> {
        match self {
            Self::ClearManipulatorOverride => {
                Some("ChrCtrl+0x3b0 IS STILL ARMED; ChrCtrl::Unref will DLPanic")
            }
            Self::DespawnCreature => Some(
                "the creature this mod created is STILL IN THE WORLD and nothing else will remove \
                 it; it holds one of the fourteen buddy roster slots until the next map load",
            ),
            _ => None,
        }
    }
}

/// Why a release was asked for. Carried into the log so "it let go on its own" and "the player
/// pressed the key" are never the same line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Reason {
    /// The possess hotkey, pressed again.
    Hotkey,
    /// The possessed creature died. `[target].release_on_death`.
    CreatureDied,
    /// The possessed creature stopped being readable -- despawned, unloaded, or the pointer chain
    /// stopped resolving.
    CreatureGone,
    /// A spawned creature never became drivable inside `[spawn].readiness_ms`.
    ///
    /// Its own reason rather than [`Self::CreatureGone`], which is what it was first written as and
    /// is the opposite claim: the creature is still THERE, and still ours to remove. Reporting a
    /// deadline as a disappearance would send the reader looking for what removed it.
    SpawnTimedOut,
    /// `DLL_PROCESS_DETACH`, or the shell shutting down. The path that exists purely so the
    /// override slot is not left armed in a process that is about to unload us.
    Shutdown,
}

impl Reason {
    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Hotkey => "hotkey",
            Self::CreatureDied => "creature-died",
            Self::CreatureGone => "creature-gone",
            Self::SpawnTimedOut => "spawn-timed-out",
            Self::Shutdown => "shutdown",
        }
    }
}

/// A release in progress: runs [`Step::ALL`] in order, records what failed, and never stops early.
#[derive(Clone, Debug)]
pub(crate) struct Teardown {
    reason: Reason,
    done: Vec<Step>,
    failed: Vec<Step>,
}

impl Teardown {
    pub(crate) const fn new(reason: Reason) -> Self {
        Self {
            reason,
            done: Vec::new(),
            failed: Vec::new(),
        }
    }

    /// Run the whole release. `step` performs one step and answers whether it worked.
    ///
    /// Every step is attempted, in order, whatever the ones before it answered. See the module
    /// docs for why "stop on the first failure" is the wrong shape here.
    pub(crate) fn run(&mut self, mut step: impl FnMut(Step) -> bool) {
        for one in Step::ALL {
            if step(one) {
                self.done.push(one);
            } else {
                self.failed.push(one);
            }
        }
    }

    /// Did every step work?
    fn is_clean(&self) -> bool {
        self.failed.is_empty()
    }

    /// Did a step whose failure arms a later crash fail?
    pub(crate) fn has_critical_failure(&self) -> bool {
        self.failed.iter().copied().any(Step::is_critical)
    }

    /// `release: reason=hotkey steps=6/6 ok` -- or the same line naming what did not work.
    pub(crate) fn line(&self) -> String {
        let mut line = format!(
            "release: reason={} steps={}/{}",
            self.reason.name(),
            self.done.len(),
            Step::ALL.len()
        );
        if self.is_clean() {
            line.push_str(" ok");
            return line;
        }
        line.push_str(" FAILED=[");
        for (index, failure) in self.failed.iter().enumerate() {
            if index > 0 {
                line.push(' ');
            }
            line.push_str(failure.name());
        }
        line.push(']');
        // Spelled out because the consequence is nowhere near the cause -- a DLPanic the next time
        // that character is unloaded, or an NPC left standing in the world -- and neither is
        // guessable from the step's name.
        for failure in &self.failed {
            if let Some(note) = failure.failure_note() {
                line.push_str(" -- ");
                line.push_str(note);
            }
        }
        line
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The order IS the discriminants, and the list must be sorted. A step inserted in the wrong
    /// place fails here rather than silently reordering the release.
    #[test]
    fn the_steps_are_in_the_order_they_must_happen() {
        let mut sorted = Step::ALL;
        sorted.sort_unstable();
        assert_eq!(sorted, Step::ALL);
        assert_eq!(Step::ALL[0], Step::RestoreBody);
        assert_eq!(*Step::ALL.last().unwrap(), Step::LiftSaveSuppression);
    }

    /// The two orderings with a visible consequence, asserted as relations rather than indices so
    /// they survive a step being inserted between them.
    #[test]
    fn the_player_moves_before_the_camera_is_released_and_the_save_is_last() {
        assert!(
            Step::MovePlayer < Step::ClearCameraOverride,
            "clearing the camera first shows a frame of the abandoned body"
        );
        assert!(
            Step::StopColocating < Step::MovePlayer,
            "co-location still running would put the player back on the creature"
        );
        for step in Step::ALL {
            if step != Step::LiftSaveSuppression {
                assert!(
                    step < Step::LiftSaveSuppression,
                    "{} must precede the save gate opening",
                    step.name()
                );
            }
        }
    }

    /// A clean run does every step once, in order.
    #[test]
    fn a_clean_run_performs_every_step_in_order() {
        let mut seen = Vec::new();
        let mut teardown = Teardown::new(Reason::Hotkey);
        teardown.run(|step| {
            seen.push(step);
            true
        });
        assert_eq!(seen, Step::ALL.to_vec());
        assert!(teardown.is_clean());
        assert!(!teardown.has_critical_failure());
        assert_eq!(teardown.line(), "release: reason=hotkey steps=7/7 ok");
    }

    /// THE ORDERING THAT IS A CRASH IF IT MOVES. `WorldChrManImp::RemoveChrIns` hands the character
    /// to `CSDelayDeleteMan`, and the delayed destruction runs `ChrCtrl::Unref`, which DLPanics on a
    /// non-null `ChrCtrl+0x3b0`. Despawning before the override is cleared therefore arms the exact
    /// crash the clear exists to prevent -- and arms it on a delay, in a destructor.
    #[test]
    fn the_creature_is_despawned_only_after_its_override_slot_has_been_cleared() {
        assert!(
            Step::ClearManipulatorOverride < Step::DespawnCreature,
            "RemoveChrIns before the override clear is a delayed DLPanic in ChrCtrl::Unref"
        );
        // ...and the run order matches, not merely the discriminants.
        let clear = Step::ALL
            .iter()
            .position(|step| *step == Step::ClearManipulatorOverride)
            .expect("present");
        let despawn = Step::ALL
            .iter()
            .position(|step| *step == Step::DespawnCreature)
            .expect("present");
        assert!(clear < despawn);
    }

    /// A despawn that did not happen leaves a live NPC nobody will remove, which is not guessable
    /// from the step's name -- and is NOT the crash class, so it must not claim to be.
    #[test]
    fn a_failed_despawn_names_the_orphan_and_is_not_reported_as_a_crash() {
        let mut teardown = Teardown::new(Reason::Hotkey);
        teardown.run(|step| step != Step::DespawnCreature);
        assert!(
            !teardown.has_critical_failure(),
            "an orphan is not a DLPanic"
        );
        let line = teardown.line();
        assert!(line.contains("despawn-creature"), "{line}");
        assert!(line.contains("STILL IN THE WORLD"), "{line}");
        assert!(!line.contains("DLPanic"), "{line}");
    }

    /// THE PROPERTY THAT MATTERS. A step failing must not stop the run -- in particular the
    /// override clear must still be attempted after everything before it has failed.
    #[test]
    fn a_failing_step_does_not_stop_the_ones_after_it() {
        let mut seen = Vec::new();
        let mut teardown = Teardown::new(Reason::CreatureDied);
        teardown.run(|step| {
            seen.push(step);
            // Everything before the override clear fails.
            step >= Step::ClearManipulatorOverride
        });
        assert_eq!(seen, Step::ALL.to_vec(), "every step was still attempted");
        assert!(!teardown.is_clean());
        assert!(
            !teardown.has_critical_failure(),
            "the one that matters did run"
        );
        let line = teardown.line();
        assert!(line.contains("reason=creature-died"), "{line}");
        assert!(line.contains("steps=3/7"), "{line}");
        assert!(line.contains("restore-body"), "{line}");
        assert!(!line.contains("DLPanic"), "{line}");
    }

    /// Exactly one step arms a later crash, and its failure says so in words rather than leaving
    /// the reader to notice which of six names is the dangerous one.
    #[test]
    fn only_the_override_clear_is_critical_and_it_says_so_when_it_fails() {
        for step in Step::ALL {
            assert_eq!(
                step.is_critical(),
                step == Step::ClearManipulatorOverride,
                "{}",
                step.name()
            );
        }
        let mut teardown = Teardown::new(Reason::Shutdown);
        teardown.run(|step| step != Step::ClearManipulatorOverride);
        assert!(teardown.has_critical_failure());
        let line = teardown.line();
        assert!(line.contains("ChrCtrl+0x3b0 IS STILL ARMED"), "{line}");
        assert!(line.contains("DLPanic"), "{line}");
        assert!(line.contains("steps=6/7"), "{line}");
    }

    /// Every reason spells itself for the log, so "it let go on its own" and "the player pressed
    /// the key" are never the same line.
    #[test]
    fn every_reason_has_its_own_name() {
        let names: Vec<&str> = [
            Reason::Hotkey,
            Reason::CreatureDied,
            Reason::CreatureGone,
            Reason::SpawnTimedOut,
            Reason::Shutdown,
        ]
        .into_iter()
        .map(Reason::name)
        .collect();
        let mut unique = names.clone();
        unique.sort_unstable();
        unique.dedup();
        assert_eq!(unique.len(), names.len(), "{names:?}");
    }
}
