//! Cancel discipline: when a press may leave the animation that is already playing.
//!
//! # The engine has none, and that is a measured fact rather than a suspicion
//!
//! Firing an attack is a write to `CSChrEventModule+0x18 requestAnimationId`, and **nothing on
//! that path consults what the creature is currently doing**. `CS::CSChrEventModule::Update`
//! (1.16.2 `0x14043a580`) gates the request on exactly five things, none of which is the current
//! animation:
//!
//! * `!CS::ChrIns::IsDead`;
//! * `componentContainer->actionFlag->actionAnimationFlags` bit 12 clear -- and that bit has three
//!   `or dword [rax+0x10], 0x1000` sites in the whole image, two of them inside `SetTextScale` /
//!   `SetFontSize` (debug text, a different structure at the same offset) and the third inside
//!   `HksAct` (`0x14040cbd0`), the Lua action-script host. It is script-driven state, not a
//!   per-swing marker;
//! * `!CS::CSChrThrowModule::IsInTrow`;
//! * `FUN_14043acc0`, which is not a cancel check at all -- its whole body compares the requested
//!   id against `PlayerCommonParam::animeID_MaterialItemPick`, `animeID_DropItemPick`,
//!   `animeID_SleepCollectorItemPick` and two `GameSystemCommonParam` treasure pickup ids, and
//!   defers those five to `actionFlag->field_0x28` instead of playing them;
//! * `field_0x70 == 0`.
//!
//! Everything else goes straight to `FUN_140c14400`, which formats `W_Event%04d` and hands it to
//! the behaviour graph -- which accepts it, mid-swing, every time. So the cancel the player sees
//! is not a bug in how this crate writes the field. It is the absence of any rule at all, and the
//! rule has to live here.
//!
//! # ...but it does know, one module along
//!
//! What the request path does not consult, the animation system writes down. TAE event type 0
//! (`ChrActionFlag`) with `FlagType` 86 sets bit 5 of
//! `CSChrActionRequestModule::taeCancels`, `CS::ChrIns::PreBehaviorSafe` clears that bit every
//! frame, and `CS::CSAiFunc::IsEnableCancelAttack` reads it to decide whether a creature's own AI
//! may chain out of the swing it is in. So the game already computes, per creature, per frame,
//! per animation, the exact predicate this layer needs -- for CREATURES, which is what a
//! possession is wearing. Reading that bit is [`Availability::Chainable`], and the answer is the
//! engine's rather than ours.
//!
//! It costs no game address: [`crate::possess::game::Chr::attack_cancel_allowed`] reads the field
//! and applies the same two tests the engine's own leaf predicate applies. See
//! [`crate::possess::layout::chr_action_request_module`] for the byte proof on both builds.
//!
//! # The offline window, which is the fallback and the report
//!
//! The same TimeAct event has a start and an end in animation-local seconds, and those are
//! readable offline from the corpus -- so the shipped table carries the START as
//! [`crate::moveset::table::Move::chain_from_cs`], and this layer compares it against
//! `CSChrTimeActModule::animQueue[readIdx].localTime`, the creature's own playhead. That is the
//! answer used when the module chain does not read, and it is also what lets
//! `er-npc-possess.derived.toml` say per move whether the game authored a real window for it or
//! whether the mod is going to make the player wait out the animation.
//!
//! A move with no window from either source is [`Availability::Committed`] for its whole length,
//! so a press during it waits for the animation to end rather than guessing. That is the
//! least-bad rule and it is never an interrupt.
//!
//! The fallback is slightly more permissive than the engine, and only in one direction. The
//! table carries the window's START and not its END, so once the offline window has opened this
//! layer treats the rest of the clip as chainable, while the live bit goes false again when the
//! event's end time passes. Measured on c4500, most windows run to within a frame or two of the
//! clip's end, so the difference is a short tail at the very end of a recovery -- and it only
//! applies at all when the module pointer failed to read, which is the path where the choice is
//! between this and nothing. Carrying the end as a second column is the fix if it ever matters.
//!
//! # How much of the moveset this actually covers, and what the rest falls back to
//!
//! 5,086 of the 6,921 shipped moves carry a window. Split by what they are, that is **4,576 of
//! the 4,669 ATTACKS (98.0%)** against 510 of the 2,252 movement moves -- and the split is the
//! shape of the data rather than a hole in it, because FlagType 86 is the flag for cancelling an
//! ATTACK and a dodge is not one. So a press during an attack chains on the game's own window
//! almost always, and a press during a dodge usually waits the dodge out, which for a
//! half-second clip is what waiting means anyway.
//!
//! # What is read one frame late
//!
//! The possession ticks in `CSTaskGroupIndex::FrameBegin`. `CS::ChrIns::PreBehaviorSafe` clears
//! the transient `taeCancels` bits and the TimeAct events re-set them during the behaviour
//! update, both later in the same frame -- so every value this layer reads describes the window
//! the PREVIOUS frame established. 16 ms against a window whose median width is 800 ms, and
//! acting on the window the last frame established is the correct reading of a press anyway.

// Pure state machine over observations; ungated so `cargo test` proves it on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::moveset::dispatch::Input;
use crate::moveset::watchdog::NEUTRAL_ANIMATION_CEILING;

/// What the creature says it is doing this frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Playing {
    /// `animQueue[readIdx].animId`.
    pub(crate) animation: i32,
    /// `animQueue[readIdx].localTime`, seconds into the clip. `None` when the field could not be
    /// read, which is the same answer a missing module pointer gives.
    pub(crate) elapsed_s: Option<f32>,
    /// `CSChrActionRequestModule::taeCancels`, resolved through the engine's own predicate.
    /// `Some(true)` is the game saying a chain is allowed right now. `None` is "did not read",
    /// which is why this is not a `bool`.
    pub(crate) cancel_allowed: Option<bool>,
}

/// May a press be honoured right now?
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Availability {
    /// Nothing is playing that a press could cancel -- idle, locomotion, a turn.
    Idle,
    /// An attack is playing and has already done its work. A follow-up here is a chain.
    Chainable,
    /// An attack is playing and leaving it now would cancel a hit that has not landed.
    Committed,
}

impl Availability {
    /// Everything except [`Self::Committed`] lets a press through.
    ///
    /// [`Self::Idle`] and [`Self::Chainable`] are kept apart even though they answer this the
    /// same way, because they are different facts about the creature and the difference is what
    /// a future log line or report would need. Collapsing them into a bool here would throw that
    /// away at the only point it is known.
    pub(crate) const fn accepts_a_press(self) -> bool {
        !matches!(self, Self::Committed)
    }
}

/// Resolve what the creature will accept, from what the engine says and what the table measured.
///
/// The engine wins when it answers. `cancel_allowed` is the same predicate the game's own AI is
/// gated on, evaluated by the game, for this creature, on this frame; the table's window is an
/// offline reading of the TimeAct event that SETS that bit, so when the two could disagree the
/// live one is the one that is right. The table is what answers when the module chain does not
/// read, and it is what the derived report can print before a possession has even started.
///
/// `chain_from_s` is the moment the playing move's window opens, or `None` when the generator
/// found no window on it.
///
/// Failing open on an unreadable clock is deliberate. `None` for `playing` means the TimeAct
/// module pointer did not read, which is exactly the state the crate was in before this layer
/// existed; answering [`Availability::Committed`] there would buffer every press behind a reading
/// that is never going to arrive and leave the buttons dead. A creature that is genuinely stuck is
/// [`crate::moveset::watchdog`]'s problem and always was.
pub(crate) fn availability(playing: Option<Playing>, chain_from_s: Option<f32>) -> Availability {
    let Some(playing) = playing else {
        return Availability::Idle;
    };
    if playing.animation < NEUTRAL_ANIMATION_CEILING {
        return Availability::Idle;
    }
    if let Some(allowed) = playing.cancel_allowed {
        return if allowed {
            Availability::Chainable
        } else {
            Availability::Committed
        };
    }
    match (chain_from_s, playing.elapsed_s) {
        // `>=` and not `>`: the window opening on the exact frame a press lands should let it
        // through, the same inclusive reading the combo window uses.
        (Some(from), Some(elapsed)) if elapsed.is_finite() && elapsed >= from => {
            Availability::Chainable
        }
        _ => Availability::Committed,
    }
}

/// What happened to a press.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Held {
    /// Nothing was waiting; this press is now.
    Queued,
    /// A press was already waiting and this one took its place.
    Replaced,
}

/// What the frame owes the player.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Release {
    Nothing,
    /// The buffered press may fire now.
    Fire(Input),
    /// The buffered press waited too long and is gone.
    Expired(Input),
}

/// One buffered press, and only one.
///
/// A player who mashes six times through a long wind-up must not get six attacks out the other
/// side, so the sixth press replaces the fifth rather than joining a queue. And a press that is
/// never released has to rot: `window_ms` after it landed it is dropped, so an attack you gave up
/// on does not arrive seconds later while you are walking away.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Buffer {
    held: Option<(Input, u64)>,
    window_ms: u32,
}

impl Buffer {
    pub(crate) const fn new(window_ms: u32) -> Self {
        Self {
            held: None,
            window_ms,
        }
    }

    pub(crate) const fn is_holding(&self) -> bool {
        self.held.is_some()
    }

    /// Take a press that cannot be honoured yet.
    pub(crate) const fn hold(&mut self, input: Input, now_ms: u64) -> Held {
        let outcome = if self.held.is_some() {
            Held::Replaced
        } else {
            Held::Queued
        };
        self.held = Some((input, now_ms));
        outcome
    }

    /// One frame. Expiry is checked first, and that ordering is the setting doing its job: a
    /// press that has outlived its window is gone even on the very frame the animation ends,
    /// because otherwise a ten-second clip would deliver every press ever made during it.
    pub(crate) fn release(&mut self, now_ms: u64, availability: Availability) -> Release {
        let Some((input, pressed_ms)) = self.held else {
            return Release::Nothing;
        };
        if now_ms.saturating_sub(pressed_ms) > u64::from(self.window_ms) {
            self.held = None;
            return Release::Expired(input);
        }
        if availability.accepts_a_press() {
            self.held = None;
            return Release::Fire(input);
        }
        Release::Nothing
    }

    /// Drop whatever is waiting, without firing it.
    pub(crate) const fn forget(&mut self) {
        self.held = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A creature whose `taeCancels` did not read, so only the offline window can answer.
    const fn playing(animation: i32, elapsed_s: f32) -> Option<Playing> {
        Some(Playing {
            animation,
            elapsed_s: Some(elapsed_s),
            cancel_allowed: None,
        })
    }

    /// A creature the engine answered for.
    const fn asked(animation: i32, elapsed_s: f32, allowed: bool) -> Option<Playing> {
        Some(Playing {
            animation,
            elapsed_s: Some(elapsed_s),
            cancel_allowed: Some(allowed),
        })
    }

    #[test]
    fn the_engines_own_answer_beats_the_offline_window_in_both_directions() {
        // Table says "not yet" (0.4 of 0.5), engine says the window is open. The engine is
        // evaluating the very TimeAct event the table read offline, on this frame, so it wins.
        assert_eq!(
            availability(asked(3000, 0.4, true), Some(0.5)),
            Availability::Chainable
        );
        // ...and the other way. Table says open, engine says no.
        assert_eq!(
            availability(asked(3000, 5.0, true), None),
            Availability::Chainable
        );
        assert_eq!(
            availability(asked(3000, 5.0, false), Some(0.5)),
            Availability::Committed
        );
    }

    #[test]
    fn the_engine_cannot_make_a_neutral_animation_cancellable_because_there_is_nothing_to_cancel() {
        assert_eq!(availability(asked(0, 0.0, true), None), Availability::Idle);
    }

    #[test]
    fn a_neutral_animation_is_idle_however_the_window_reads() {
        // Below the attack band: idle, locomotion or a turn. Nothing to cancel.
        assert_eq!(
            availability(playing(2999, 0.0), Some(9.0)),
            Availability::Idle
        );
        assert_eq!(availability(playing(0, 0.0), None), Availability::Idle);
    }

    #[test]
    fn an_attack_is_committed_until_its_window_opens_and_chainable_after() {
        assert_eq!(
            availability(playing(3000, 0.4), Some(0.5)),
            Availability::Committed
        );
        assert_eq!(
            availability(playing(3000, 0.5), Some(0.5)),
            Availability::Chainable,
            "the window is inclusive on the frame it opens"
        );
        assert_eq!(
            availability(playing(3000, 5.0), Some(0.5)),
            Availability::Chainable
        );
    }

    #[test]
    fn a_move_with_no_measured_window_stays_committed_for_its_whole_length() {
        // The honest answer for a move the generator could not measure: never interrupt it, wait
        // for it to end. `Chainable` here would be a guess dressed as a measurement.
        assert_eq!(
            availability(playing(3000, 0.0), None),
            Availability::Committed
        );
        assert_eq!(
            availability(playing(3000, 600.0), None),
            Availability::Committed
        );
    }

    #[test]
    fn an_unreadable_clock_fails_open_rather_than_deadening_every_button() {
        assert_eq!(availability(None, Some(0.5)), Availability::Idle);
        // The id read but the time did not: still committed, because the window cannot be
        // evaluated -- but the animation is known to be playing, so waiting is right.
        assert_eq!(
            availability(
                Some(Playing {
                    animation: 3000,
                    elapsed_s: None,
                    cancel_allowed: None,
                }),
                Some(0.5)
            ),
            Availability::Committed
        );
    }

    #[test]
    fn a_nan_clock_is_treated_as_unmeasured() {
        assert_eq!(
            availability(playing(3000, f32::NAN), Some(0.5)),
            Availability::Committed
        );
        assert_eq!(
            availability(playing(3000, f32::INFINITY), Some(0.5)),
            Availability::Committed
        );
    }

    #[test]
    fn a_press_that_cannot_be_honoured_waits_and_fires_when_the_animation_lets_go() {
        let mut buffer = Buffer::new(1000);
        assert_eq!(buffer.hold(Input::R1, 0), Held::Queued);
        assert_eq!(
            buffer.release(100, Availability::Committed),
            Release::Nothing,
            "still mid-swing"
        );
        assert_eq!(
            buffer.release(200, Availability::Chainable),
            Release::Fire(Input::R1)
        );
        assert_eq!(buffer.release(300, Availability::Idle), Release::Nothing);
        assert!(!buffer.is_holding());
    }

    #[test]
    fn the_animation_ending_releases_a_buffered_press_just_as_a_chain_window_does() {
        let mut buffer = Buffer::new(1000);
        buffer.hold(Input::R2, 0);
        assert_eq!(
            buffer.release(500, Availability::Idle),
            Release::Fire(Input::R2)
        );
    }

    #[test]
    fn mashing_six_times_leaves_one_press_waiting_and_not_six() {
        let mut buffer = Buffer::new(1000);
        assert_eq!(buffer.hold(Input::R1, 0), Held::Queued);
        for step in 1..6 {
            assert_eq!(buffer.hold(Input::R2, step * 10), Held::Replaced);
        }
        assert_eq!(
            buffer.release(100, Availability::Idle),
            Release::Fire(Input::R2),
            "the last press is the one that survives"
        );
        assert_eq!(buffer.release(110, Availability::Idle), Release::Nothing);
    }

    #[test]
    fn a_press_nobody_came_back_for_expires_instead_of_arriving_late() {
        let mut buffer = Buffer::new(1000);
        buffer.hold(Input::L1, 0);
        assert_eq!(
            buffer.release(1000, Availability::Committed),
            Release::Nothing,
            "the window is inclusive"
        );
        assert_eq!(
            buffer.release(1001, Availability::Committed),
            Release::Expired(Input::L1)
        );
        assert!(!buffer.is_holding());
    }

    #[test]
    fn expiry_beats_a_window_that_opens_on_the_same_frame() {
        let mut buffer = Buffer::new(100);
        buffer.hold(Input::L2, 0);
        assert_eq!(
            buffer.release(5_000, Availability::Idle),
            Release::Expired(Input::L2),
            "a ten-second clip must not deliver every press ever made during it"
        );
    }

    #[test]
    fn forgetting_drops_the_press_without_firing_it() {
        let mut buffer = Buffer::new(1000);
        buffer.hold(Input::R1, 0);
        buffer.forget();
        assert_eq!(buffer.release(10, Availability::Idle), Release::Nothing);
    }
}
