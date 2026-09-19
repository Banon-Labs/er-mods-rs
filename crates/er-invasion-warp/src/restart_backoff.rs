//! Slows the search restart down when Seamless is refusing attempts instantly.
//!
//! # The failure this exists to stop
//!
//! Measured live 2026-08-06. The player walked into a new map and Seamless began refusing every
//! search immediately, through a state (`0x14`) nobody had seen before:
//!
//! ```text
//!   0x0d SEARCHING -> 0x0e -> 0x11 -> 0x14 -> 0x00 IDLE -> (our restart) -> ...
//! ```
//!
//! `0x11` was held for **33 ms** instead of its usual ~15 s. Eleven restarts landed in 38.9 seconds
//! — one cycle every 3.5 s against a normal 15 s — roughly four times the usual query rate at
//! Steam, for as long as the condition lasted (~40 s, until the area settled).
//!
//! The reject loop is supposed to run until the player stops it, so the restart itself is correct.
//! What is missing is any sense of *how badly the last attempt went*: [`arm_self_recovery`] fires
//! the moment the session reaches idle, no matter whether the attempt ran a full 15-second search
//! or died in 33 milliseconds.
//!
//! # Why the stall watchdog cannot cover this
//!
//! `stall_watchdog` times states that are held **too long** and cancels them. Every state in the
//! spin above is held far too *short*. The two detectors are looking for opposite shapes of the
//! same problem — a handshake that stopped progressing, versus one that never started — and
//! neither generalises to the other.
//!
//! # Why a delay and not a cap
//!
//! A count- or time-capped loop would eventually stop hunting, which is exactly what the standing
//! instruction forbids: the loop runs until the player uses the lynchpin again. Backing off slows
//! a doomed retry without ever abandoning it, so the fast-fail window costs a handful of queries
//! instead of hundreds, and the loop is still running when the condition clears.
//!
//! On the normal path this is inert: Seamless's own ~15 s retry paces the loop, no attempt is
//! short, and no delay is ever applied.

/// An attempt that ends faster than this did not really try — Seamless refused it rather than
/// searching. The normal no-match cycle is ~15 s end to end and the shortest healthy handshake
/// observed is well over a second, so this sits far below anything legitimate.
pub const FAST_FAIL_MS: u64 = 1_000;

/// First delay applied after a fast failure, doubling per consecutive one.
pub const BASE_DELAY_MS: u64 = 1_000;

/// Ceiling on the delay. Chosen just under Seamless's own ~15 s retry so that even fully backed
/// off, this never becomes the slowest thing in the loop — the player should not be able to tell
/// a backed-off retry from an ordinary one.
pub const MAX_DELAY_MS: u64 = 8_000;

/// The idle window every ended attempt gets, whether or not it failed fast.
///
/// This module used to return `0` for any attempt that lasted longer than [`FAST_FAIL_MS`], which
/// is every ordinary one -- the no-match cycle is about fifteen seconds. Measured on run
/// `br-20260917-234208-1e25`, the consequence was two bugs wearing one face:
///
/// ```text
/// session state 0x23 CANCELLING -> 0x24        held 1 ticks / 42ms
/// session state 0x24 -> 0x01 IDLE              held 1 ticks / 42ms
/// session state 0x01 IDLE -> 0x0e SEARCHING    held 0 ticks / 2ms (driven by us: restart search)
/// ```
///
/// Idle lasted two milliseconds. The player could not stop the hunt -- a cancel they drove, or one
/// driven for them, was overwritten before the next frame -- and the neighbourhood sweep, which
/// only runs while the session is idle, never got a tick, so the search stayed aimed at whichever
/// tile it started on. The report that found it: "she just re-opened her world, but I don't think
/// I can stop my initial search and it's not hitting her". Both halves of that sentence are this
/// constant being zero.
///
/// Two seconds is chosen against the thing it has to fit inside rather than picked round: the
/// cycle it sits in is ~15 s, so this is an eighth of it and cannot become the slowest step, and
/// it is long enough for the sweep to advance several places per pass. The sweep keeps its queue
/// across windows, so the ring is walked a slice at a time rather than needing one idle stretch
/// long enough for all 49.
pub const IDLE_WINDOW_MS: u64 = 2_000;

/// Tracks how the recent attempts have been going, and how long to wait before the next one.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct RestartBackoff {
    /// When the current attempt began, if one is in flight.
    started_ms: Option<u64>,
    /// Consecutive fast failures. Reset by any attempt that makes real progress.
    consecutive: u32,
    /// Earliest time the next restart may fire.
    hold_until_ms: Option<u64>,
}

impl RestartBackoff {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            started_ms: None,
            consecutive: 0,
            hold_until_ms: None,
        }
    }

    /// An attempt has started. Called when the session leaves idle.
    pub fn attempt_started(&mut self, now_ms: u64) {
        self.started_ms = Some(now_ms);
    }

    /// The attempt got far enough to be a real search, so whatever was wrong has cleared.
    ///
    /// Called on reaching the handshake states. This is what stops a single bad patch of network
    /// from penalising the rest of the session: one good attempt wipes the accumulated delay.
    pub fn attempt_made_progress(&mut self) {
        self.consecutive = 0;
        self.hold_until_ms = None;
    }

    /// The attempt ended and the session is idle. Returns the delay to wait before restarting.
    ///
    /// Zero means restart immediately, which is the normal case.
    pub fn attempt_ended(&mut self, now_ms: u64) -> u64 {
        let elapsed = self
            .started_ms
            .take()
            .map(|start| now_ms.saturating_sub(start));
        // No recorded start means we cannot judge it -- treat as normal rather than inventing a
        // penalty from a missing measurement.
        let Some(elapsed) = elapsed else {
            return 0;
        };
        if elapsed >= FAST_FAIL_MS {
            // A healthy attempt clears the penalty, but it does not earn an instant restart: see
            // `IDLE_WINDOW_MS` for the two-millisecond idle this used to produce and what it broke.
            self.consecutive = 0;
            self.hold_until_ms = Some(now_ms.saturating_add(IDLE_WINDOW_MS));
            return IDLE_WINDOW_MS;
        }
        // Saturating shift: `consecutive` is bounded below anyway, but a delay that wrapped to a
        // small number would silently restore the spin this module exists to prevent.
        let delay = BASE_DELAY_MS
            .saturating_mul(1u64 << self.consecutive.min(16))
            .min(MAX_DELAY_MS);
        self.consecutive = self.consecutive.saturating_add(1);
        self.hold_until_ms = Some(now_ms.saturating_add(delay));
        delay
    }

    /// True when a restart is allowed to fire now.
    pub fn may_restart(&mut self, now_ms: u64) -> bool {
        match self.hold_until_ms {
            Some(until) if now_ms < until => false,
            Some(_) => {
                self.hold_until_ms = None;
                true
            }
            None => true,
        }
    }

    /// Consecutive fast failures seen, for the log line.
    #[must_use]
    pub const fn consecutive(&self) -> u32 {
        self.consecutive
    }

    /// Forget everything. Called when the loop disarms, so a new hunt starts clean rather than
    /// inheriting a delay earned by the previous one.
    pub fn stand_down(&mut self) {
        self.started_ms = None;
        self.consecutive = 0;
        self.hold_until_ms = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The normal path must be untouched. A no-match cycle runs ~15s; it must never be delayed,
    /// no matter how many of them happen, or this would slow down the ordinary hunt it is meant
    /// to leave alone.
    #[test]
    fn an_ordinary_fifteen_second_cycle_is_never_penalised() {
        let mut backoff = RestartBackoff::new();
        let mut now = 0;
        for _ in 0..20 {
            backoff.attempt_started(now);
            now += 15_000;
            assert_eq!(
                backoff.attempt_ended(now),
                IDLE_WINDOW_MS,
                "a full search is not a failure, so it earns the plain idle window and no penalty"
            );
            // The window is a hold, not a penalty: it does not escalate and it does elapse.
            assert!(!backoff.may_restart(now), "the window has not passed yet");
            now += IDLE_WINDOW_MS;
            assert!(backoff.may_restart(now));
        }
        assert_eq!(backoff.consecutive(), 0);
    }

    /// The window is what lets a player stop the hunt, so it must actually hold the restart.
    ///
    /// Run `br-20260917-234208-1e25` cancelled a stuck search and the loop drove a new one two
    /// milliseconds later, which is both "I cannot stop my initial search" and a neighbourhood
    /// sweep that never gets a tick to re-aim with.
    #[test]
    fn a_cancelled_search_leaves_a_window_a_player_can_act_in() {
        let mut backoff = RestartBackoff::new();
        backoff.attempt_started(0);
        assert_eq!(backoff.attempt_ended(15_000), IDLE_WINDOW_MS);
        assert!(!backoff.may_restart(15_000), "restarted in the same frame");
        assert!(
            !backoff.may_restart(15_002),
            "restarted two milliseconds later"
        );
        assert!(!backoff.may_restart(15_000 + IDLE_WINDOW_MS - 1));
        assert!(backoff.may_restart(15_000 + IDLE_WINDOW_MS));
    }

    /// The measured failure. The live spin ran a whole cycle in ~200ms; the delay must escalate.
    #[test]
    fn consecutive_fast_failures_escalate_the_delay() {
        let mut backoff = RestartBackoff::new();
        let mut now = 0;
        let mut delays = Vec::new();
        for _ in 0..5 {
            backoff.attempt_started(now);
            now += 200; // the observed spin cycle
            delays.push(backoff.attempt_ended(now));
            now += delays[delays.len() - 1];
        }
        assert_eq!(delays, vec![1_000, 2_000, 4_000, 8_000, 8_000]);
    }

    /// The delay must never exceed the cap, or a backed-off retry would become slower than
    /// Seamless's own 15s cycle and the player would notice the loop dragging.
    #[test]
    fn the_delay_is_capped_below_the_native_retry() {
        let mut backoff = RestartBackoff::new();
        let mut now = 0;
        let mut last = 0;
        for _ in 0..40 {
            backoff.attempt_started(now);
            now += 50;
            last = backoff.attempt_ended(now);
            now += last;
        }
        assert_eq!(last, MAX_DELAY_MS);
        const {
            assert!(
                MAX_DELAY_MS < 15_000,
                "a backed-off retry must stay faster than Seamless's own retry"
            )
        };
    }

    /// One good attempt clears the penalty. The live condition lasted ~40s and then cleared on its
    /// own; the loop must return to full speed immediately, not stay throttled for the session.
    #[test]
    fn progress_wipes_the_accumulated_delay() {
        let mut backoff = RestartBackoff::new();
        let mut now = 0;
        for _ in 0..4 {
            backoff.attempt_started(now);
            now += 100;
            now += backoff.attempt_ended(now);
        }
        assert!(backoff.consecutive() > 0);

        backoff.attempt_started(now);
        backoff.attempt_made_progress(); // reached the handshake
        now += 15_000;
        // Back to the plain idle window rather than to zero: the penalty is gone, and what is left
        // is the window every ended attempt gets so the sweep can re-aim and a cancel can stick.
        assert_eq!(backoff.attempt_ended(now), IDLE_WINDOW_MS);
        assert_eq!(backoff.consecutive(), 0);
        assert!(backoff.may_restart(now + IDLE_WINDOW_MS));
    }

    /// The hold must actually hold, and then release -- a delay that never expires would stop the
    /// hunt, which is the one outcome forbidden outright.
    #[test]
    fn the_hold_expires_and_the_loop_always_resumes() {
        let mut backoff = RestartBackoff::new();
        backoff.attempt_started(0);
        let delay = backoff.attempt_ended(100);
        assert_eq!(delay, BASE_DELAY_MS);
        assert!(!backoff.may_restart(100), "held immediately after failing");
        assert!(
            !backoff.may_restart(100 + delay - 1),
            "still held just before"
        );
        assert!(backoff.may_restart(100 + delay), "released on time");
        assert!(backoff.may_restart(100 + delay), "and stays released");
    }

    /// An attempt whose start was never recorded cannot be judged, and must not be penalised on
    /// a guess -- the DLL can attach mid-search, with a session already in flight.
    #[test]
    fn an_attempt_with_no_recorded_start_is_treated_as_normal() {
        let mut backoff = RestartBackoff::new();
        assert_eq!(backoff.attempt_ended(5_000), 0);
        assert_eq!(backoff.consecutive(), 0);
        assert!(backoff.may_restart(5_000));
    }

    /// Disarming clears the penalty, so the player's next hunt starts at full speed rather than
    /// inheriting a delay from the last one.
    #[test]
    fn standing_down_clears_the_penalty() {
        let mut backoff = RestartBackoff::new();
        backoff.attempt_started(0);
        backoff.attempt_ended(50);
        assert!(!backoff.may_restart(50));
        backoff.stand_down();
        assert!(backoff.may_restart(50));
        assert_eq!(backoff.consecutive(), 0);
    }

    /// A clock that appears to move backwards must not produce a negative-elapsed panic or an
    /// enormous hold; saturating arithmetic degrades it to "normal attempt".
    #[test]
    fn a_backwards_clock_cannot_wedge_the_loop() {
        let mut backoff = RestartBackoff::new();
        backoff.attempt_started(10_000);
        assert_eq!(backoff.attempt_ended(5_000), BASE_DELAY_MS);
        assert!(backoff.may_restart(u64::MAX));
    }
}
