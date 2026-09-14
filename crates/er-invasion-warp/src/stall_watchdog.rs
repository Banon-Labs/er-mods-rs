//! Detects a Seamless connection that has stopped progressing, so the reject loop can recover.
//!
//! Policy (user, 2026-08-06, verbatim intent): the reject loop "never stops until they use the
//! lynchpin again, and it should self recover from seamless connection stalls. Invasion connections
//! should be reliably quick and for some reason, seamless doesn't auto-cancel its own connections
//! during these edge cases."
//!
//! So this is a stall detector, never a cap. It counts no attempts, bounds no total time, and has
//! no notion of giving up. The only thing it can conclude is "this particular handshake step has sat
//! still far longer than one ever does", and the only thing it can ask for is the cancel the player
//! could have pressed by hand -- after which the existing auto-search rearm carries on exactly as
//! before. A watchdog that could end the loop would be the wrong shape whatever its threshold.
//!
//! Which states are timed, and why that is the whole design
//! -------------------------------------------------------
//! Observed session walk for an invader, in the numbering of Seamless Co-op v1.9.9 -- the build
//! every timing measurement in this file was taken against:
//!
//! ```text
//!   0x00 idle -> 0x0d searching -> 0x0e -> 0x12 -> 0x13 -> 0x15 (join data)
//!             -> 0x22 cancelling -> 0x23 -> 0x00 idle -> restart
//! ```
//!
//! The searching state is unbounded by nature -- it means "looking, nobody matched yet", and a
//! player in a quiet bracket can legitimately sit there for many minutes. Measured 2026-08-06:
//! three consecutive filtered queries returned 0, 0, then 1 lobby. Timing that state would fire a
//! stall on a perfectly healthy empty search, which is the single most obvious way to get this
//! wrong.
//!
//! The handshake states are the opposite: measured `0x0d -> 0x15` in under 1s (n=10) and
//! `0x22 -> 0x00` in 2s or less (n=8), with exactly one outlier where cancelling hung for 30s. That
//! outlier is the failure this exists for, and the gap between 2s and 30s is where the threshold
//! goes.
//!
//! The idle state is untimed for the same reason as searching: it is a resting state, not a step.
//!
//! The v2.0.0 renumber, which this file was never carried through
//! --------------------------------------------------------------
//! Every code in that walk belongs to v1.9.9. The supported build is v2.0.x, which renumbered the
//! whole session-state enum by `+1`, and commit `fd554f9d` ("Re-pin er-invasion-warp against
//! Seamless Co-op v2.0.0") carried that through `local_invasion_filter.rs` and its `ersc` module
//! while leaving this file alone. So until 2026-09-13 the timed set still read:
//!
//! ```text
//!   0x0e   a search-to-connect handoff under v1.9.9
//!          -- and `ersc::Abi::state_searching` on the supported build
//!   0x12   a connect step under v1.9.9
//!          -- and a code the supported build's ABI does not name
//!   0x13   a connect step under v1.9.9
//!          -- and `ersc::Abi::state_offer_received` on the supported build
//! ```
//!
//! The first line is the live bug. `local_invasion_filter::trace_session_state` arms the reject
//! loop on entry to `state_searching`, and this watchdog then timed that same state and drove a
//! cancel five seconds later -- so a player who started a hunt got a search that killed itself,
//! every time, which is what "invasions just fail" looks like from their seat. The third line is
//! the `0x11` regression recorded on [`TIMED_STATES`] in different numbers:
//! `state_offer_received` is the restart backoff's progress marker, not a step to interrupt.
//!
//! ### Why `0x12` and `0x13` were dropped rather than shifted by one
//!
//! Because a shifted code would be a guess wearing a measurement's clothes, and the admission rule
//! on [`TIMED_STATES`] forbids exactly that. The enum is statically recoverable only where a
//! plaintext instruction stores a literal into the state field, which is what
//! `scripts/ersc-disas.py states` enumerates. Measured 2026-09-13, both modules read in place:
//!
//! ```text
//!   v1.9.9 at +0x110   0x1, 0x3, 0x6, 0x9, 0xd, 0x22 (x7), 0x23
//!   v2.0.1 at +0x150   0x2, 0x4, 0x5, 0x7, 0xa, 0xe,  0x23 (x7), 0x24
//! ```
//!
//! That is the same seven values under a uniform `+1`, site counts included, plus a `0x5` the
//! previous build never wrote. `0x11`, `0x12`, `0x13` and `0x14` appear in neither set: no
//! plaintext site writes them, so the scan cannot say what they became and there is nothing
//! offline to re-derive them from. They are not statically recoverable -- that is a property of
//! the module, not a measurement someone can be sent to take -- so under the admission rule they
//! leave rather than move.
//!
//! The consequence is that [`TIMED_STATES`] is empty and this detector currently does nothing. That
//! is the same trade the `0x11` entry records: a detector that cancels a recovery the system is
//! already performing is strictly worse than not existing. The mechanism stays intact and tested,
//! through [`StallWatchdog::with_timed_states`], so the first state measured under the supported
//! build drops into a clock that already works.

/// Session states of the supported build, mirroring `local_invasion_filter::ersc`. Duplicated
/// deliberately: this module is pure so it can be unit-tested without the game, and a `use` of the
/// game-facing module would drag in memory reads that cannot run on a test host.
///
/// The duplication is now checked rather than trusted. `the_state_codes_match_the_supported_abi`
/// reads the real `Abi` table and fails if any constant here drifts from it -- the guard that was
/// missing while these held v1.9.9's numbering for eleven days after the build moved, which is what
/// let `a_long_search_is_never_a_stall` pass against a code the game no longer uses.
pub mod state {
    /// `ersc::Abi::state_idle`.
    pub const IDLE: u32 = 0x01;
    /// `ersc::Abi::state_searching`. The state `local_invasion_filter` arms the reject loop on, so
    /// timing it cancels the hunt the player just started.
    pub const SEARCHING: u32 = 0x0e;
    /// `ersc::Abi::state_offer_received` -- the restart backoff's "this attempt is a real search"
    /// marker, reached within ~150 ms on a healthy attempt.
    pub const OFFER_RECEIVED: u32 = 0x13;
    /// `ersc::Abi::state_cancelling`.
    pub const CANCELLING: u32 = 0x23;
    /// The state a driven cancel settles through on its way back to idle. The `Abi` does not name
    /// it, so nothing pins it: it is v1.9.9's `0x23` carried across the uniform `+1`, and the
    /// static store scan supports that reading -- v1.9.9 writes `0x23` at one site and the
    /// supported build writes `0x24` at one site, the same one-site shape as the `0x22`/`0x23`
    /// pair that moved with it.
    pub const CANCEL_SETTLING: u32 = 0x24;
}

/// The only states this detector times, listed because each one was measured to be brief.
///
/// Empty since 2026-09-13. Every entry it held was measured under Seamless v1.9.9 and never
/// carried through the v2.0.0 renumber -- see this module's docs for what each one turned into and
/// why two of them could not be re-derived offline.
///
/// # This is an allowlist, and it is an allowlist because a blocklist shipped and broke a live run
///
/// The first version asked "is this state neither idle nor searching?" and timed everything else.
/// That makes every state nobody has measured a stall by default, which is exactly backwards: an
/// unmeasured state is one we know nothing about, and the safe treatment of it is to leave it
/// alone. Measured 2026-08-06, one run, after that version shipped:
///
/// ```text
///   0x11 -> 0x0d     0 times   <- Seamless's own retry, extinct
///   0x11 -> 0x22    33 times   <- this detector cancelling it instead
///   "connection stalled at state 0x11 for 5000ms"  x31
/// ```
///
/// `0x11` is the step Seamless passes through when a search found nobody and is about to go round
/// again -- the same `0x0e -> 0x11 -> 0x0d` cycle that ran 12 times in an earlier healthy capture.
/// Cancelling it every five seconds killed the retry the game was about to do by itself, and the
/// player could not match anyone for as long as the build was loaded. The detector was strictly
/// worse than not existing.
///
/// So: a state earns its place here by having been observed to complete quickly **on the build
/// this repo supports**. Anything absent -- unknown, renumbered by a Seamless update, or simply
/// never seen -- is never timed. A code measured on a previous build is in the second category,
/// not the first.
const TIMED_STATES: &[u32] = &[];

// 0x22 and 0x23 (v1.9.9 numbering, like every measurement above) are deliberately absent, and they
// used to be here. They are the unwind of a cancel that is already in flight, and the only remedy
// this watchdog has is to drive a cancel -- so on those two states it answers a cancel with another
// cancel.
//
// Measured on run br-20260909-020749-0a75, six times in one session. The sequence each time:
// `cancelled rejected match (#N)` puts the session at 0x23, five seconds later this fires
// `about to drive ERSC cancel (stalled attempt) -- state=0x23 CANCELLING`, and the session then
// holds 0x23 for 30,307ms before reaching 0x24 and idle. From the player's seat that is a search
// that dies and takes half a minute to come back, reported as "I'm just getting failed
// invasions".
//
// 30 seconds also fails this file's own admission test one line up: a state earns its place by
// having been observed to complete quickly, and 0x23 has now been measured at six times the
// threshold that is supposed to catch it. This is the same shape as the 0x11 mistake recorded
// above -- a detector cancelling the recovery the system was already performing, strictly worse
// than not existing.

// 0x15 (join data arrived, v1.9.9 numbering) is deliberately absent, and it used to be here.
//
// Judgement happens synchronously the moment join data lands, so a reject leaves 0x15 in zero
// ticks. The only way the session dwells there is after a match was kept -- and that dwell is the
// player loading into the host's world, which takes far longer than any handshake. Timing it
// cancelled a successful invasion five seconds after accepting it: the log read `keep 0x3c2a2400
// (ExactBlock)` and then `connection stalled at state 0x15 for 5000ms -- cancelled it`, and from
// the player's seat the invasion appeared and dismissed itself instantly (2026-08-06).
//
// Removing it is defence in depth, not the fix. The fix is that the watchdog does not run at all
// once a match is kept -- see the caller's arming gate -- because a successful join also walks
// 0x22 and 0x23, so no choice of timed states could have made this safe on its own.

/// How long a transient handshake state may sit without progressing before it counts as stalled.
///
/// Chosen from the measured spread, not from taste: healthy transitions complete in under 1s
/// (connect, n=10) and under 2s (cancel, n=8); the one observed stall lasted 30s. Five seconds is
/// above every healthy sample by more than 2x and far below the failure, so it cannot fire on a
/// slow-but-live handshake and cannot take 30s to notice a dead one.
pub const STALL_THRESHOLD_MS: u64 = 5_000;

/// What the watchdog wants done. There is exactly one action, and it is the one a player could
/// perform by hand -- the loop is never ended, only unstuck.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StallAction {
    /// Drive ERSC's "Cancel search". The session walks back to idle, the existing auto-search
    /// rearm restarts the hunt, and the loop continues as though the stall had never happened.
    CancelAndResearch,
}

/// Watches session-state observations and reports a state that has stopped progressing.
///
/// Fed by the same poll that already traces session state, so it needs no new detour. It holds no
/// counters that could accumulate toward a limit -- only "which state, and since when".
#[derive(Debug)]
pub struct StallWatchdog {
    /// Which states this instance may time. Always [`TIMED_STATES`] in the product; a test may
    /// name its own through [`StallWatchdog::with_timed_states`].
    timed: &'static [u32],
    /// The state currently being timed, and the timestamp it was entered. `None` while resting in
    /// an untimed state.
    timing: Option<(u32, u64)>,
    /// Set once a stall has been reported for the current entry into a state, so a single stall
    /// produces a single cancel rather than one per poll. Cleared by any state change, because a
    /// state change is progress and the next stall is a new event.
    reported: bool,
}

impl Default for StallWatchdog {
    fn default() -> Self {
        Self::new()
    }
}

impl StallWatchdog {
    /// `const` so it can initialise a `static Mutex<StallWatchdog>` directly. Deriving `Default`
    /// is not enough for that: `Default::default()` cannot run in a const context.
    #[must_use]
    pub const fn new() -> Self {
        Self::with_timed_states(TIMED_STATES)
    }

    /// The same detector over a membership list of the caller's choosing.
    ///
    /// It exists because [`TIMED_STATES`] is empty, which leaves the clock and the one-shot latch
    /// with no product state to demonstrate on -- and those have to already work on the day a state
    /// is measured under the supported build. Tests name a synthetic code here; nothing in the
    /// product calls it, and the product path goes through [`StallWatchdog::new`].
    #[must_use]
    pub const fn with_timed_states(timed: &'static [u32]) -> Self {
        Self {
            timed,
            timing: None,
            reported: false,
        }
    }

    /// Whether a state is one we have measured to be brief, and may therefore time.
    ///
    /// Fails closed on anything unrecognised: not timed, never cancelled.
    fn is_transient(&self, state: u32) -> bool {
        self.timed.contains(&state)
    }

    /// Feed one observation. `now_ms` is any monotonic millisecond clock.
    ///
    /// Returns `Some(action)` exactly once per stalled state entry. Re-entering the same state
    /// later is a new entry and can stall again -- which is the point, since a repeatedly stalling
    /// handshake must be repeatedly recovered, not silently tolerated after the first time.
    pub fn observe(&mut self, state: u32, now_ms: u64) -> Option<StallAction> {
        match self.timing {
            // A different state than we were timing: progress. Start over.
            Some((timed, _)) if timed != state => {
                let transient = self.is_transient(state);
                self.timing = transient.then_some((state, now_ms));
                self.reported = false;
                None
            }
            // Still in the same transient state -- the only case that can stall.
            Some((_, entered)) => {
                if self.reported {
                    return None;
                }
                // Saturating: a clock that appears to go backwards yields 0 elapsed and simply
                // does not fire, rather than underflowing into an instant false stall.
                if now_ms.saturating_sub(entered) >= STALL_THRESHOLD_MS {
                    self.reported = true;
                    return Some(StallAction::CancelAndResearch);
                }
                None
            }
            // Was resting; begin timing if this state is one that should be brief.
            None => {
                if self.is_transient(state) {
                    self.timing = Some((state, now_ms));
                    self.reported = false;
                }
                None
            }
        }
    }

    /// How long the current transient state has been held, for the trace line. `None` while resting.
    pub fn held_ms(&self, now_ms: u64) -> Option<u64> {
        self.timing
            .map(|(_, entered)| now_ms.saturating_sub(entered))
    }

    /// The state being timed, if any.
    pub fn timing_state(&self) -> Option<u32> {
        self.timing.map(|(s, _)| s)
    }

    /// Forget what is being timed, so a later observation starts a fresh clock.
    ///
    /// Called when the hunt stops -- a match was kept, the player cancelled, or they opened
    /// Seamless's menu. Without this, the elapsed time accumulated while hunting would carry into
    /// whatever the session does next and fire a stall against it immediately.
    pub fn stand_down(&mut self) {
        self.timing = None;
        self.reported = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The supported build's ABI table, read as text.
    ///
    /// `local_invasion_filter::ersc` is private to that module, so a sibling at the crate root
    /// cannot name it -- and this module is deliberately pure anyway. Reading the source is how
    /// `local_invasion_filter::tests` pins its own neighbours, and it is what makes the codes
    /// below a reference to the real constant rather than a second copy of a hex literal.
    const ERSC_SOURCE: &str = include_str!("local_invasion_filter/ersc.rs");

    /// Every session state the supported build's `Abi` gives a name to. A code that has a name has
    /// a meaning, and none of those meanings is "a handshake step held briefly".
    const NAMED_SESSION_STATES: [&str; 4] = [
        "state_idle",
        "state_searching",
        "state_cancelling",
        "state_offer_received",
    ];

    /// Read one state code out of the supported build's `Abi` table.
    ///
    /// Panics rather than falling back if the table cannot be parsed, so a rename or a reformat
    /// fails these tests loudly instead of quietly returning a default that would pass everything.
    fn supported_abi_state(field: &str) -> u32 {
        let table = ERSC_SOURCE
            .split_once("pub const SUPPORTED: &[Abi] = &[Abi {")
            .expect("the supported-build Abi table")
            .1
            .split_once("}];")
            .expect("the end of the Abi table")
            .0;
        let value = table
            .split_once(&format!("{field}: "))
            .unwrap_or_else(|| panic!("`{field}` is a field of the supported Abi"))
            .1
            .split_once(',')
            .expect("a field assignment ends with a comma")
            .0
            .trim();
        let hex = value
            .strip_prefix("0x")
            .unwrap_or_else(|| panic!("`{field}` is written as a hex literal, found `{value}`"));
        u32::from_str_radix(hex, 16).expect("a hex state code")
    }

    /// A code no Seamless build has ever written, so it can only mean "the mechanism under test".
    /// Above `0xff` on purpose: [`every_state_outside_the_measured_set_is_left_alone`] sweeps the
    /// real range and must not collide with it.
    const MECHANISM_STATE: u32 = 0x1_0000;
    const MECHANISM_STATE_B: u32 = 0x1_0001;
    const MECHANISM_SET: &[u32] = &[MECHANISM_STATE, MECHANISM_STATE_B];

    /// A detector whose membership list is synthetic, for the tests that exercise the clock and the
    /// latch rather than which states are timed.
    fn mechanism_watchdog() -> StallWatchdog {
        StallWatchdog::with_timed_states(MECHANISM_SET)
    }

    /// The regression this whole change exists for, stated against the constant the runtime path
    /// reads rather than against a copy of its value.
    ///
    /// `state_searching` was `0x0d` under v1.9.9 and is `0x0e` under the supported build, and
    /// `0x0e` sat in the timed set from the day of the renumber. `local_invasion_filter` arms the
    /// reject loop on entry to that state, so the watchdog cancelled every hunt five seconds after
    /// the player started it.
    #[test]
    fn the_searching_state_is_never_timed() {
        let searching = supported_abi_state("state_searching");
        assert!(
            !TIMED_STATES.contains(&searching),
            "`state_searching` is {searching:#04x} on the supported build, and timing it cancels \
             the hunt the player just started -- the state the reject loop arms on is the one \
             state that can never be timed",
        );
    }

    /// The general form: a code the `Abi` gives a name to is a code with a meaning, and none of
    /// those meanings is a brief handshake step. `state_offer_received` is the restart backoff's
    /// progress marker, `state_cancelling` is a recovery already under way, `state_idle` is a
    /// resting state.
    ///
    /// This is the pin the timed set had none of. It reads the supported build's real codes, so a
    /// Seamless renumber that moves any of them under a stale entry fails here rather than in a
    /// live run.
    #[test]
    fn no_timed_state_collides_with_a_named_session_state() {
        for field in NAMED_SESSION_STATES {
            let code = supported_abi_state(field);
            assert!(
                !TIMED_STATES.contains(&code),
                "`{field}` is {code:#04x} on the supported build; a named state is never a \
                 handshake step this detector may cancel. If a measurement says otherwise, record \
                 the measurement here rather than the number",
            );
        }
    }

    /// What stops the codes in [`state`] from describing a build the game no longer runs.
    ///
    /// They held v1.9.9's numbering while the supported build was v2.0.x, which is why
    /// `a_long_search_is_never_a_stall` could observe `0x0d`, find it absent from a timed set that
    /// contained `0x0e`, and pass while the live bug it names was shipping.
    #[test]
    fn the_state_codes_match_the_supported_abi() {
        assert_eq!(state::IDLE, supported_abi_state("state_idle"));
        assert_eq!(state::SEARCHING, supported_abi_state("state_searching"));
        assert_eq!(state::CANCELLING, supported_abi_state("state_cancelling"));
        assert_eq!(
            state::OFFER_RECEIVED,
            supported_abi_state("state_offer_received")
        );
    }

    /// The inverse of what this test asserted until 2026-09-09, and the reason it flipped.
    ///
    /// It used to require that a cancel hung at the cancelling state be recovered by driving a
    /// cancel. That is answering a cancel with another cancel, and run br-20260909-020749-0a75
    /// measured the cost six times: `cancelled rejected match (#N)` puts the session at the
    /// settling state, this fired five seconds later, and the session then held it for 30,307ms
    /// before reaching idle.
    ///
    /// A cancel that is settling is already the recovery. There is nothing for this watchdog to
    /// add to it, and its only action makes it worse.
    #[test]
    fn a_settling_cancel_is_never_a_stall() {
        for state in [state::CANCELLING, state::CANCEL_SETTLING] {
            let mut w = StallWatchdog::new();
            w.observe(state, 0);
            assert_eq!(
                w.observe(state, 3_600_000),
                None,
                "state {state:#04x} is a cancel already unwinding; driving another cancel into it \
                 is what wedged it for 30s",
            );
        }
    }

    /// The most obvious way to get this wrong. Searching means "nobody matched yet" and is
    /// unbounded; three consecutive live queries returned 0, 0, 1 on 2026-08-06. Timing it would
    /// fire on a healthy quiet bracket and cancel a search the player wanted to continue.
    ///
    /// `state::SEARCHING` is pinned to the supported build by
    /// [`the_state_codes_match_the_supported_abi`], which is what stops this from passing against
    /// a code the game stopped using.
    #[test]
    fn a_long_search_is_never_a_stall() {
        let mut w = StallWatchdog::new();
        for minute in 0..30 {
            assert_eq!(
                w.observe(state::SEARCHING, minute * 60_000),
                None,
                "searching for {minute} minutes is normal, not a stall",
            );
        }
    }

    /// The regression this module shipped, 2026-08-06, caught only by a live run.
    ///
    /// `0x11` is Seamless v1.9.9's "found nobody, go round again" step -- the `0x0e -> 0x11 ->
    /// 0x0d` cycle. The first version timed it because it was neither idle nor searching, cancelled
    /// it 31 times in one session, and the player could not match anyone at all while that build
    /// was loaded. The detector was worse than not existing.
    ///
    /// Both candidate codes are swept because the supported build's number for this step is not
    /// statically recoverable: `0x11` is what v1.9.9 used, `0x12` is where the enum-wide `+1` would
    /// put it, and no plaintext site in either module writes either value for the scan to confirm.
    #[test]
    fn the_retry_step_is_never_a_stall() {
        for retrying in [0x11_u32, 0x12] {
            let mut w = StallWatchdog::new();
            for second in 0..120 {
                assert_eq!(
                    w.observe(retrying, second * 1_000),
                    None,
                    "{retrying:#04x} is Seamless retrying by itself; cancelling it kills the search"
                );
            }
        }
    }

    /// The property, not the three states I happened to think of. Anything unmeasured must fail
    /// closed -- the previous test suite asserted only that idle and searching were exempt, which
    /// is precisely the belief that was wrong, so it passed while the bug shipped.
    ///
    /// This one cannot go stale across a renumber, which the single-code tests around it can: it
    /// sweeps the whole byte range, so whatever the retry, join or settling step is numbered on the
    /// current build, it is covered here too.
    #[test]
    fn every_state_outside_the_measured_set_is_left_alone() {
        for state in 0..=0xffu32 {
            if TIMED_STATES.contains(&state) {
                continue;
            }
            let mut w = StallWatchdog::new();
            w.observe(state, 0);
            assert_eq!(
                w.observe(state, 3_600_000),
                None,
                "state {state:#04x} was never measured, so it must never be cancelled -- an \
                 unknown state is not evidence of a stall",
            );
        }
    }

    /// ...and the measured ones still do their job, so failing closed did not disarm the feature.
    ///
    /// Vacuous while [`TIMED_STATES`] is empty, and kept for the entry that refills it: a state
    /// admitted without a working clock behind it would be a detector that reports nothing.
    #[test]
    fn every_measured_state_still_stalls() {
        for state in TIMED_STATES {
            let mut w = StallWatchdog::new();
            w.observe(*state, 0);
            assert_eq!(
                w.observe(*state, STALL_THRESHOLD_MS),
                Some(StallAction::CancelAndResearch),
                "state {state:#04x} is in the measured set and must still be recoverable",
            );
        }
    }

    /// The second regression, 2026-08-06: a kept match was cancelled 5s after being accepted.
    ///
    /// `0x15` is where a v1.9.9 session sits while the player loads into the host's world, which is
    /// far longer than any handshake. Both it and `0x16`, where the enum-wide `+1` would put it,
    /// are swept: neither is written by a plaintext site, so which one the supported build uses
    /// cannot be read offline.
    #[test]
    fn the_join_state_is_never_a_stall() {
        for join in [0x15_u32, 0x16] {
            let mut w = StallWatchdog::new();
            for second in 0..90 {
                assert_eq!(
                    w.observe(join, second * 1_000),
                    None,
                    "{join:#04x} is a live invasion loading in, not a stuck handshake",
                );
            }
        }
    }

    /// Standing down forgets the accumulated clock. Without this, time banked while hunting would
    /// carry into whatever the session does next and fire against it instantly.
    #[test]
    fn standing_down_forgets_the_clock() {
        let mut w = mechanism_watchdog();
        w.observe(MECHANISM_STATE, 0);
        w.stand_down();
        assert!(w.timing_state().is_none());
        assert_eq!(
            w.observe(MECHANISM_STATE, 4_000),
            None,
            "after standing down the clock restarts; 4s must not read as 4s already elapsed",
        );
        assert_eq!(
            w.observe(MECHANISM_STATE, 9_000),
            Some(StallAction::CancelAndResearch),
            "and the fresh clock still reaches the threshold on its own terms",
        );
    }

    /// Idle is a resting state, not a handshake step.
    #[test]
    fn idle_is_never_a_stall() {
        let mut w = StallWatchdog::new();
        assert_eq!(w.observe(state::IDLE, 0), None);
        assert_eq!(w.observe(state::IDLE, 600_000), None);
        assert!(w.timing_state().is_none());
    }

    /// A healthy handshake walks through several transient states quickly; each transition restarts
    /// the clock, so no individual step ever approaches the threshold.
    #[test]
    fn a_healthy_handshake_never_fires() {
        let mut w = mechanism_watchdog();
        let walk = [
            state::IDLE,
            state::SEARCHING,
            MECHANISM_STATE,
            MECHANISM_STATE_B,
            state::CANCELLING,
            state::IDLE,
        ];
        for (i, s) in walk.iter().enumerate() {
            // ~200ms per step: comfortably inside the measured sub-second connect.
            assert_eq!(w.observe(*s, i as u64 * 200), None, "state {s:#04x} fired");
        }
    }

    /// A step that is slow but still moving must not fire. The clock restarts on every transition,
    /// so total elapsed across a handshake is irrelevant -- only time in one state matters. This is
    /// what makes the detector a stall detector rather than a disguised total-time cap.
    #[test]
    fn slow_but_progressing_is_not_a_stall() {
        let mut w = mechanism_watchdog();
        let mut t = 0;
        for s in [
            MECHANISM_STATE,
            MECHANISM_STATE_B,
            MECHANISM_STATE,
            MECHANISM_STATE_B,
        ] {
            t += 4_000; // 4s per step, 16s total -- far past the threshold in aggregate
            assert_eq!(w.observe(s, t), None, "progress must reset the clock");
        }
    }

    /// One stall produces one cancel, not one per poll. The poll runs every frame; without this a
    /// single stall would drive dozens of cancels.
    #[test]
    fn a_stall_fires_once_per_entry() {
        let mut w = mechanism_watchdog();
        w.observe(MECHANISM_STATE, 0);
        assert!(w.observe(MECHANISM_STATE, 5_000).is_some());
        for t in 5..60 {
            assert_eq!(
                w.observe(MECHANISM_STATE, t * 1_000),
                None,
                "re-fired at {t}s"
            );
        }
    }

    /// ...but a later entry into the same state is a new event. A handshake that stalls every time
    /// must be recovered every time; suppressing the second one would strand the player after the
    /// first recovery, which is exactly the "never stops" policy being violated.
    #[test]
    fn a_second_stall_in_a_later_entry_fires_again() {
        let mut w = mechanism_watchdog();
        w.observe(MECHANISM_STATE, 0);
        assert!(w.observe(MECHANISM_STATE, 5_000).is_some());
        // recovered: back to idle, then searching, then stuck again
        w.observe(state::IDLE, 6_000);
        w.observe(state::SEARCHING, 7_000);
        w.observe(MECHANISM_STATE, 8_000);
        assert!(
            w.observe(MECHANISM_STATE, 13_000).is_some(),
            "a repeat stall must be recovered again -- the loop never gives up",
        );
    }

    /// A clock that appears to move backwards must not synthesise an instant stall.
    #[test]
    fn a_backwards_clock_does_not_fire() {
        let mut w = mechanism_watchdog();
        w.observe(MECHANISM_STATE, 10_000);
        assert_eq!(w.observe(MECHANISM_STATE, 1_000), None);
    }

    /// The trace needs to say how long a state has been held; resting states report nothing rather
    /// than a misleading zero.
    #[test]
    fn held_time_is_reported_only_while_timing() {
        let mut w = mechanism_watchdog();
        w.observe(state::IDLE, 1_000);
        assert_eq!(w.held_ms(2_000), None);
        w.observe(MECHANISM_STATE, 3_000);
        assert_eq!(w.held_ms(4_500), Some(1_500));
        assert_eq!(w.timing_state(), Some(MECHANISM_STATE));
    }

    /// The product detector times nothing, so nothing it is fed can produce an action. Stated
    /// directly rather than left implied by the sweep above, because this is the behaviour change
    /// the renumber fix makes: until a state is measured under the supported build, the safe answer
    /// is an inert detector.
    #[test]
    fn the_product_detector_currently_reports_no_action() {
        for state in 0..=0xffu32 {
            let mut w = StallWatchdog::new();
            for tick in 0..4 {
                assert_eq!(
                    w.observe(state, tick * STALL_THRESHOLD_MS),
                    None,
                    "state {state:#04x} produced an action from an empty measured set",
                );
            }
            assert_eq!(w.held_ms(0), None, "state {state:#04x} started a clock");
        }
    }

    /// The threshold must sit strictly between the healthy spread and the observed failure. Pinned
    /// so a later "tidy up the constant" cannot quietly move it onto either side.
    #[test]
    fn threshold_sits_between_measured_healthy_and_measured_stall() {
        const {
            assert!(
                STALL_THRESHOLD_MS > 2_000,
                "must not fire on the slowest healthy cancel (2s, n=8)"
            )
        };
        const {
            assert!(
                STALL_THRESHOLD_MS < 30_000,
                "must notice the observed 30s stall well before it ends"
            )
        };
    }
}
