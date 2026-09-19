//! Calls a dead invasion attempt at the moment a working one would already have landed.
//!
//! # The complaint
//!
//! User, 2026-09-15, verbatim:
//!
//! ```text
//! I make a connection to a player, something happens that causes the connection to eventually
//! time out. I have to wait X seconds before I'm alerted by Seamless that it failed to invade. A
//! user knows MUCH sooner if they failed to invade because the failed to invade message comes
//! MUCH later than an invasion success ever would land.
//! ```
//!
//! The last sentence is the mechanism. The player is not waiting on an answer Seamless has yet to
//! compute; they are waiting out a timeout whose answer they can already infer, because a working
//! invasion would have put them in the host's world by now. So nothing has to be read out of
//! `ersc`: what is needed is the deadline by which a success always arrives.
//!
//! # The deadline, measured
//!
//! Every session-state transition is logged with its dwell, so 262 runs of `~/.cache/er-me3-runs`
//! already held the answer. Timing from entry to the connect state -- `0x0f`, where a host has been
//! matched -- to the invasion being real:
//!
//! ```text
//!   SUCCESS  n=313   min 182ms    median 275ms    p90 329ms    max 441ms
//!   FAILURE  n= 91   min 2346ms   median 5144ms                max 8036ms
//! ```
//!
//! No success exceeded 441ms and no failure resolved before 2346ms. The gap between the two is
//! empty, which is what lets one threshold separate them without being a taste setting.
//!
//! ## The failure column is contaminated; the success column is not
//!
//! Until 2026-09-13 `0x12` sat in `stall_watchdog::TIMED_STATES`, so 85 of those 92 attempts were
//! cancelled by this mod at exactly 5000ms rather than by Seamless -- the log says `connection
//! stalled at state 0x12 for 5000ms` 85 times. Only seven ended on their own: 2254, 4343, 5157,
//! 7874, 7878, 7937 and 14993ms, each going to cancelling or back to searching, none to the world.
//!
//! The deadline below is derived from the success column alone, which the watchdog could not have
//! touched -- its threshold was 5000ms and the slowest success was 441ms. The failure numbers
//! appear only to show the threshold is not sitting on top of them.
//!
//! # A clock, not the state `0x12`
//!
//! `0x12` is written from inside Themida's VM and can be observed but not read -- see
//! `local_invasion_filter::trace_session_field_writes`. Seven clean samples pointing one way is
//! suggestive, not a fact to gate an action on, and a Seamless update renumbers the enum without
//! warning. A deadline needs neither: it asks whether the thing that proves success has happened
//! yet, which survives both.
//!
//! # Why this is allowed to drive a cancel when `stall_watchdog` was not
//!
//! That detector answered a stall by cancelling, and twice cancelled a recovery the game was
//! already performing: the `0x11` retry it killed 33 times in one run, and the `0x12` connect it
//! cut short 85 times above. Both were worse than not existing. The difference is not the action,
//! it is what earns it. Those fired on a state that had merely sat still, which an unmeasured state
//! is entitled to do. This fires only once a connect has outlived every success ever recorded --
//! and the caller still refuses unless `ersc`'s own hide-predicate would have drawn a Cancel row,
//! so the worst case is driving a row the player could have clicked themselves.

/// How long after a host is matched a successful invasion is allowed to take before the attempt is
/// called lost.
///
/// The sample this was derived from contains no remote invasion at all, and on 2026-09-17 it
/// killed the first one anybody has seen.
///
/// The old value was 1500ms: 3.4x the slowest success on record (441ms, n=313) and inside the
/// earliest self-resolving failure (2254ms). The arithmetic was right about the data and the data
/// was the problem. The paragraph that set it said so itself -- "all 313 successes were measured
/// on one machine's connection" -- and then bounded a connect to a stranger with them anyway.
///
/// What it cost, on run br-20260917-195855-46cf. A real host was found and the log says so:
///
/// ```text
/// sweep: a host is in m61_48_45_00 -- the search points there and stops widening
/// hunt: decision=sweep_hit -- a nearby place answered with a host in it
/// local-invasion: session state 0x0e SEARCHING -> 0x0f -> 0x12
/// local-invasion: the connection at state 0x0012 has not landed in 1500ms ... Calling it lost
/// ```
///
/// That repeated for as long as the finger stayed armed, and the player's report was "I found a
/// host in Highroad Cross and it says -- invading, but it doesn't invade". The connect was ours to
/// wait on and we cancelled it about a second and a half in, every time.
///
/// The engine's own patience is the number that should have been copied, and it was measured
/// rather than assumed: `scripts/frida/session-join-timers.js` on run br-20260917-205031-edd3 read
/// `CSSessionManagerImp`'s `joinCheckTimeout` at **431.6 seconds** remaining. That field is an
/// `FD4Time` counting down, so its value is what the engine armed itself to wait -- 287 times
/// longer than this constant. A deadline that short is not a safety margin over the engine, it is
/// a race against the thing it claims to observe.
///
/// 45 seconds keeps the constant doing the one job it was written for -- shortening the wait after
/// a genuinely dead match, since the earliest self-resolving failure on record is 2254ms and
/// Seamless's own timeout is far longer -- while putting it well past any handshake a live connect
/// has been seen to need. It can no longer beat a real invasion to the verdict, which is the whole
/// property the original paragraph claimed and the measurement disproved.
pub const CONNECT_DEADLINE_MS: u64 = 45_000;

/// Where an attempt is, as far as the deadline needs to care.
///
/// Deliberately not the session-state enum. Four phases is all the clock needs, and keeping the raw
/// codes out means a Seamless renumber cannot quietly change what it decides -- the mapping is one
/// `match` in the caller, where a build change is handled already.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// No attempt in flight, a cancel unwinding included: that attempt is over either way.
    Idle,
    /// Looking for a host, nobody matched yet.
    ///
    /// Never timed, and this is the most important line in the type. A quiet bracket leaves a
    /// player here for minutes legitimately -- one measured search sat 280 seconds -- so a clock on
    /// this phase reports healthy searches as failures. That mistake shipped once already, from the
    /// watchdog, where timing the searching state cancelled the hunt the player had just started.
    Searching,
    /// A host was matched and the connection is being made. The only phase with a deadline.
    Connecting,
    /// The invasion is real: the player is in the host's world.
    Arrived,
}

/// What the clock concluded.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// The connect outlived every success on record. Tell the player, and cancel it if the session
    /// is somewhere a cancel is offered.
    Failed,
}

/// Times the connect phase of one attempt against [`CONNECT_DEADLINE_MS`].
///
/// Fed from the poll that already traces session state, so it costs no new detour and no new read.
/// Nothing here accumulates across attempts: the only state is whether a connect is being timed,
/// and since when.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AttemptVerdict {
    /// When the current connect began, on the caller's monotonic clock. `None` outside one.
    connecting_since_ms: Option<u64>,
    /// Whether this attempt has been called, so it is announced once rather than every frame for as
    /// long as the dead connection takes to unwind -- up to 15 seconds above, or some 450 frames of
    /// the same sentence.
    called: bool,
}

impl AttemptVerdict {
    /// A tracker with no attempt in flight.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            connecting_since_ms: None,
            called: false,
        }
    }

    /// Feed the current phase and the time now. Returns a verdict on the first frame past the
    /// deadline, and nothing on any frame after it.
    ///
    /// `now_ms` is passed in rather than read so this stays testable off the game; the caller hands
    /// it the same tick clock the transition log already stamps.
    pub fn observe(&mut self, now_ms: u64, phase: Phase) -> Option<Verdict> {
        match phase {
            // A new attempt is a new question, and so is the end of an old one. Both clear the
            // latch, so the next failure speaks even though the last one did.
            Phase::Idle | Phase::Searching => {
                self.connecting_since_ms = None;
                self.called = false;
                None
            }
            // An arrival settles the attempt. The latch is set rather than cleared so the states a
            // successful invasion later unwinds through cannot be read as a fresh connect and
            // called lost -- the shape of the bug that cancelled a successful invasion five seconds
            // after accepting it.
            Phase::Arrived => {
                self.connecting_since_ms = None;
                self.called = true;
                None
            }
            Phase::Connecting => {
                let since = *self.connecting_since_ms.get_or_insert(now_ms);
                // Saturating because a clock that went backwards must not wrap into an instant
                // verdict; the honest reading of a bad timestamp is that no time has passed.
                if self.called || now_ms.saturating_sub(since) < CONNECT_DEADLINE_MS {
                    return None;
                }
                self.called = true;
                Some(Verdict::Failed)
            }
        }
    }

    /// Whether the attempt in flight has already been called lost.
    #[must_use]
    pub const fn called(&self) -> bool {
        self.called
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The measured extremes, asserted so an edit to the deadline has to face them.
    const SLOWEST_RECORDED_SUCCESS_MS: u64 = 441;
    /// What the engine itself arms a join to wait, read live rather than reasoned about:
    /// `scripts/frida/session-join-timers.js` on run br-20260917-205031-edd3 found
    /// `CSSessionManagerImp`'s `joinCheckTimeout` holding 431.6 seconds.
    const ENGINE_JOIN_PATIENCE_MS: u64 = 431_587;

    #[test]
    fn the_deadline_outlasts_every_connect_and_still_beats_the_engine() {
        // `const` blocks rather than plain asserts, at clippy's suggestion and to its credit: both
        // operands are constants, so this is decidable at compile time and a bad deadline should
        // fail the build rather than one test run.
        const {
            assert!(
                CONNECT_DEADLINE_MS > SLOWEST_RECORDED_SUCCESS_MS,
                "a deadline under the slowest success calls working invasions lost"
            );
        }
        // The upper bound used to be the fastest self-resolving failure, 2254ms, on the reasoning
        // that a slower deadline saves the player no waiting. That premise came from the same
        // one-machine sample as the successes, and holding to it is what cancelled a real remote
        // connect at 1.5s on run br-20260917-195855-46cf -- the player found a host in Highroad
        // Cross and never invaded it. Saving a second of waiting is worth nothing if the thing
        // being waited on is destroyed to do it.
        //
        // The engine's own countdown is the ceiling that means something: stay under it and the
        // deadline still shortens a dead match, because the engine is the slower of the two.
        const {
            assert!(
                CONNECT_DEADLINE_MS < ENGINE_JOIN_PATIENCE_MS,
                "a deadline past the engine's own join timeout never fires, so it is not a deadline"
            );
        }
    }

    #[test]
    fn a_success_at_the_slowest_recorded_speed_is_never_called_lost() {
        let mut v = AttemptVerdict::new();
        assert_eq!(v.observe(0, Phase::Searching), None);
        assert_eq!(v.observe(10, Phase::Connecting), None);
        assert_eq!(
            v.observe(SLOWEST_RECORDED_SUCCESS_MS, Phase::Connecting),
            None
        );
        assert_eq!(v.observe(SLOWEST_RECORDED_SUCCESS_MS, Phase::Arrived), None);
    }

    #[test]
    fn a_long_search_is_never_called_lost() {
        let mut v = AttemptVerdict::new();
        for minute in 0..10 {
            assert_eq!(
                v.observe(minute * 60_000, Phase::Searching),
                None,
                "a quiet bracket is not a failure"
            );
        }
    }

    #[test]
    fn a_connect_past_the_deadline_is_called_once() {
        let mut v = AttemptVerdict::new();
        assert_eq!(v.observe(1_000, Phase::Connecting), None);
        assert_eq!(
            v.observe(1_000 + CONNECT_DEADLINE_MS, Phase::Connecting),
            Some(Verdict::Failed)
        );
        for frame in 1..450 {
            assert_eq!(
                v.observe(1_000 + CONNECT_DEADLINE_MS + frame * 33, Phase::Connecting),
                None,
                "the same sentence must not be shown every frame"
            );
        }
    }

    #[test]
    fn the_next_attempt_speaks_again() {
        let mut v = AttemptVerdict::new();
        v.observe(0, Phase::Connecting);
        assert_eq!(
            v.observe(CONNECT_DEADLINE_MS, Phase::Connecting),
            Some(Verdict::Failed)
        );
        assert_eq!(v.observe(20_000, Phase::Idle), None);
        assert_eq!(v.observe(21_000, Phase::Connecting), None);
        assert_eq!(
            v.observe(21_000 + CONNECT_DEADLINE_MS, Phase::Connecting),
            Some(Verdict::Failed),
            "a second failed invasion is news too"
        );
    }

    #[test]
    fn an_arrival_silences_the_states_it_unwinds_through() {
        let mut v = AttemptVerdict::new();
        v.observe(0, Phase::Searching);
        v.observe(100, Phase::Connecting);
        v.observe(300, Phase::Arrived);
        // The longest invasion in the sample set ran 465 seconds; its unwind must stay silent.
        for second in 1..500 {
            assert_eq!(
                v.observe(300 + second * 1_000, Phase::Connecting),
                None,
                "an invasion that happened cannot be reported as one that failed"
            );
        }
    }

    #[test]
    fn a_backwards_clock_does_not_manufacture_a_verdict() {
        let mut v = AttemptVerdict::new();
        assert_eq!(v.observe(10_000, Phase::Connecting), None);
        assert_eq!(v.observe(5, Phase::Connecting), None);
    }
}
