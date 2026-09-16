//! Decides when a rejection is worth putting on screen, and what it should say.
//!
//! Kept separate from the code that displays it so the policy can be tested on a host with no game
//! attached. The display side is a game-thread call into `CSPopupMenu`; this half is arithmetic.
//!
//! # Why this is not simply "one banner per rejection"
//!
//! Measured 2026-08-06: Seamless retries roughly every 20 seconds, and during a hunt in a busy
//! bracket most of those retries end in a rejection at the same wrong place — the same host, or
//! the same popular area, coming back around. A banner every time would be wallpaper within a
//! minute, and a notification the player learns to ignore is worse than none: it costs screen space
//! and teaches them the mod is noisy.
//!
//! So a place is announced when it is new relative to the last thing announced, and consecutive
//! rejections at that same place stay silent. Moving to a different wrong place is genuinely new
//! information and is announced again, including a return to somewhere announced earlier — the
//! player's question is "where am I being sent right now", not "where have I ever been sent".

use core::fmt::Write as _;

use crate::invasion_warp::BlockKey;
use crate::local_invasion::RejectReason;

/// The longest host name the banner will carry.
///
/// A Steam persona can be 32 characters, and the announce surface truncates rather than wraps, so
/// a maximal name plus a place name would push the place off the end -- and the place is the half
/// the player cannot get anywhere else. Cut the name instead, visibly.
pub const HOST_NAME_MAX_CHARS: usize = 20;

/// Append ` -- <host>` when the host is known.
///
/// Separate from the three builders so all of them read the same, and so the truncation rule lives
/// in one place. A `None` host leaves the line exactly as it was before this existed, which is what
/// every test written before the host was reachable still asserts.
fn append_host(text: &mut String, host: Option<&str>) {
    let Some(host) = host else {
        return;
    };
    let host = host.trim();
    if host.is_empty() {
        return;
    }
    let _ = write!(text, " -- ");
    for (index, character) in host.chars().enumerate() {
        if index == HOST_NAME_MAX_CHARS {
            text.push('\u{2026}');
            break;
        }
        text.push(character);
    }
}

/// A few words naming why a destination was refused, for the banner.
///
/// The player can already see where from the block name; what they cannot see is whether the mod
/// refused it on the rule they set, on a place they excluded by hand, or because it could not
/// resolve a name at all. Those lead to different actions — move, un-exclude, or open the map —
/// so collapsing them into a bare "rejected" wastes the notification.
///
/// Deliberately terse: the banner's width is bounded by the donor string's allocation, and an
/// overrun is silently truncated rather than wrapped.
#[must_use]
pub const fn reason_phrase(reason: RejectReason) -> &'static str {
    match reason {
        // The ordinary case during a hunt, and the two halves read very differently to a player.
        //
        // `WrongBlock` is a destination whose block differs from the anchor's -- and in Seamless
        // that is routinely the same place in someone else's world. Calling it "elsewhere" then
        // contradicts what the player can see: reported live 2026-09-08, standing in The First
        // Step, the banner read `Rejected The First Step (elsewhere)`. The place was right; the
        // world was not, and that is what the phrase has to say.
        // The world, not the action. "cancelled" was tried here and is worse than what it
        // replaced: the banner already opens with `Rejected`, so the second word repeated the
        // action and left the one fact the player cannot see -- which world -- unsaid. Reported
        // live 2026-09-09 as unreadable, from a session where it fired eight times.
        //
        // A wrong block is routinely the same place in another player's world, so no wording built
        // on geography can be both short and unconfusing here; "another world" and "not this one"
        // were each tried and each needed explaining. The other reasons below stay distinct
        // because they lead to different actions -- move, un-exclude, open the map -- while these
        // two lead to the same one: keep hunting.
        RejectReason::WrongBlock | RejectReason::WrongPlaceName => "another world",
        RejectReason::NotNamed => "not on your list",
        // Actionable in a way the others are not: the map has not been opened, so no destination
        // has a name and everything fails closed. Saying "unnamed" would read as the game's fault.
        RejectReason::CandidateUnnamed => "open your map",
        RejectReason::NothingToMatchAgainst => "open your map",
        RejectReason::ExcludedByUser => "you excluded it",
        // Not a rejection, and the banner has to stop saying it is: the player pressed the switch,
        // so the one fact they cannot already see is that the request landed rather than being
        // swallowed.
        RejectReason::PlayerStopped => "you stopped it",
    }
}

/// The last thing this banner said, so a repeat of it can stay quiet.
///
/// A success and a rejection are different announcements about the same place, which is why this
/// is one enum rather than two independent latches: an invasion that finally lands at a block the
/// player was repeatedly rejected from must speak, and a rejection after a success must speak too.
/// Two separate latches would have each suppressed the other's news.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Announced {
    Rejected(u32, RejectReason),
    Succeeded(u32),
    /// A destination the mod did not judge -- the filter's master switch is off, so this is the
    /// server's choice reported as-is.
    Arrived(u32),
    /// A connection that outlived every recorded success, carrying which attempt it was.
    ///
    /// The ordinal is what makes two failures in a row two pieces of news. A payload-free variant
    /// would make the second one a repeat of the first and swallow it, which is the opposite of
    /// what a player retrying a failing hunt needs to see.
    Failed(u32),
    /// Which step of the widening search is being asked for, by ordinal.
    ///
    /// Keyed by ordinal rather than by tile so a ring that comes back round to a tile it has
    /// already tried still counts as news: the number is what tells the player the search is
    /// moving, and suppressing a repeat would make a stalled rotation look like a working one.
    Searching(usize),
    /// The ring is spent and the search has dropped the location filter.
    ///
    /// Payload-free, unlike [`Self::Searching`], because this rung does not move: every round
    /// after it asks the same unfiltered question. Announcing it once is the whole point -- the
    /// log repeated the same sentence on every query round and the banner said nothing at all,
    /// so from the player's seat a search that had already widened looked identical to one still
    /// grinding through nearby tiles. Reported 2026-09-15 as "I have not observed it going from
    /// searching nearby to searching everywhere"; it had been searching everywhere for minutes.
    SearchingEverywhere,
}

/// Tracks what was last announced so repeats can be suppressed.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct RejectNotice {
    /// The most recent announcement. `None` before the first one.
    last_announced: Option<Announced>,
    /// Rejections suppressed since that announcement, for the telemetry line.
    suppressed: usize,
}

impl RejectNotice {
    /// Announce which place the widening search is asking for, and how far through it is.
    ///
    /// `place` is the name when one is known and `None` when it is not. A tile id is deliberately
    /// not used as a substitute: `m60_51_36_00` tells a player nothing, and a banner that shows it
    /// is worse than one that just counts. The count alone is still useful -- it is what separates
    /// "nobody is nearby" from "we have three tiles left to ask about".
    ///
    /// Returns `None` when this exact step was the last thing announced, so a query loop that
    /// re-asks for the same tile does not repaint the banner every frame.
    pub fn observe_prefilter_step(
        &mut self,
        enabled: bool,
        ordinal: usize,
        total: usize,
        place: Option<&str>,
    ) -> Option<String> {
        let repeat = self.last_announced == Some(Announced::Searching(ordinal));
        self.last_announced = Some(Announced::Searching(ordinal));
        self.suppressed = 0;
        if repeat || !enabled {
            return None;
        }
        // The first step is the player's own tile, and saying "1 of 9 nearby" about where they are
        // standing reads as a failure before anything has failed.
        if ordinal == 1 {
            return Some(match place {
                Some(name) => format!("Searching for an invasion in {name}"),
                None => "Searching for an invasion where you are".to_string(),
            });
        }
        let nearby = total.saturating_sub(1);
        Some(match place {
            Some(name) => format!(
                "No invasion where you are -- searching {} of {nearby} nearby locations ({name})",
                ordinal - 1
            ),
            None => format!(
                "No invasion where you are -- searching {} of {nearby} nearby locations",
                ordinal - 1
            ),
        })
    }

    /// Announce that the widening search has run out of nearby places and dropped the filter.
    ///
    /// Returns `None` on a repeat, which is the common case by a wide margin: the everywhere rung
    /// is re-derived on every query round, so this is asked roughly every fifteen seconds for as
    /// long as the search runs.
    ///
    /// `nearby` is how many places were tried before giving up, and it is worth carrying because
    /// the two cases read completely differently to a player. Forty-eight tried and empty is a
    /// quiet neighbourhood; one tried is a legacy dungeon, where a block id encodes a dungeon and
    /// a floor rather than a grid position, so there are no neighbours to ask about and the radius
    /// the player set could never have applied.
    pub fn observe_search_everywhere(
        &mut self,
        enabled: bool,
        nearby: usize,
        mod_only: bool,
    ) -> Option<String> {
        let repeat = self.last_announced == Some(Announced::SearchingEverywhere);
        self.last_announced = Some(Announced::SearchingEverywhere);
        self.suppressed = 0;
        if repeat || !enabled {
            return None;
        }
        // "Everywhere" is a lie while hunt is on, and it is the lie the player acts on.
        //
        // Dropping the location filter leaves the hunt filter, which asks Steam for a key only
        // hosts running this build publish. Measured 2026-09-15, run br-20260915-161554-f2a2:
        // every match found that way reached state `0x12` and died at the connect deadline,
        // because the entries were stale -- nobody else was running it. Turning hunt off in the
        // same session, with no restart, landed an invasion within 27 seconds.
        //
        // So the widened search is not a search of everywhere. It is a search of everyone running
        // this mod, which on most evenings is nobody, and a banner that says otherwise sends the
        // player off to wait for an invasion that cannot arrive.
        let reach = if mod_only {
            " -- but still only hosts running this mod"
        } else {
            ""
        };
        Some(match nearby {
            0 => format!("No nearby locations to search here -- looking everywhere instead{reach}"),
            1 => format!("No invasion where you are -- looking everywhere instead{reach}"),
            n => {
                format!("No invasion in {n} nearby locations -- looking everywhere instead{reach}")
            }
        })
    }

    #[must_use]
    pub const fn new() -> Self {
        Self {
            last_announced: None,
            suppressed: 0,
        }
    }

    /// Feed one rejected destination. Returns the text to display, or `None` to stay silent.
    ///
    /// `enabled` is threaded through rather than checked by the caller so that a disabled notice
    /// still advances the state — otherwise turning it on mid-session would announce a stale place
    /// the player was rejected from minutes ago.
    /// `place` is the area's own name when the world map has been read this session, and `None`
    /// before that. It is passed in rather than looked up here so this type stays testable off the
    /// game: resolving a name means calling into the engine's message repository.
    pub fn observe(
        &mut self,
        enabled: bool,
        block: u32,
        reason: RejectReason,
        place: Option<&str>,
        host: Option<&str>,
    ) -> Option<String> {
        let repeat = self.last_announced == Some(Announced::Rejected(block, reason));
        self.last_announced = Some(Announced::Rejected(block, reason));
        if repeat {
            self.suppressed = self.suppressed.saturating_add(1);
            return None;
        }
        self.suppressed = 0;
        if !enabled {
            return None;
        }
        let mut text = String::new();
        // Short on purpose: the banner's width is bounded by the donor string's allocation, and a
        // message that overflows is silently truncated rather than wrapped. Where and why, because
        // the place alone does not tell the player whether to move, un-exclude somewhere, or open
        // their map.
        //
        // The name when there is one. A block id is precise and unreadable -- being told you were
        // rejected from `m60_50_39_00` on a banner that closes itself in a couple of seconds is a
        // lookup task, not a notification. The id remains the fallback rather than being dropped,
        // because it is the only thing available before the map has been opened, and a rejection
        // with no place at all would be strictly worse than an unfriendly one.
        match place {
            Some(place) if !place.is_empty() => {
                let _ = write!(text, "Rejected {place} ({})", reason_phrase(reason));
            }
            _ => {
                let _ = write!(
                    text,
                    "Rejected {} ({})",
                    BlockKey::from_raw(block),
                    reason_phrase(reason)
                );
            }
        }
        append_host(&mut text, host);
        Some(text)
    }

    /// Feed a destination the filter accepted. Returns the text to display, or `None`.
    ///
    /// The bug this FIXES: the banner announced every rejection and then said nothing when the
    /// hunt finally succeeded, so the last thing left on screen was a rejection -- the player was
    /// told where they were not going and never told they had arrived. Worse, the rejection latch
    /// was never cleared by the success, so a later rejection at that same block stayed silent as
    /// a "repeat" of an announcement from before the invasion that happened in between.
    ///
    /// Same shape as [`Self::observe`]: state advances even when the notice is disabled, and the
    /// place name is resolved by the caller so this type stays testable off the game.
    pub fn observe_success(
        &mut self,
        enabled: bool,
        block: u32,
        place: Option<&str>,
        host: Option<&str>,
    ) -> Option<String> {
        let repeat = self.last_announced == Some(Announced::Succeeded(block));
        self.last_announced = Some(Announced::Succeeded(block));
        // A success ends the run of rejections it followed; the count belongs to that run.
        self.suppressed = 0;
        if repeat || !enabled {
            return None;
        }
        let mut text = String::new();
        // Says the outcome first and the place second, exactly like the rejection line, so the two
        // read as the same banner reporting opposite results rather than as two unrelated messages.
        match place {
            Some(place) if !place.is_empty() => {
                let _ = write!(text, "Invasion successful: {place}");
            }
            // Before the world map has been read nothing has a name, so the id is the fallback --
            // the same trade the rejection line makes, for the same reason.
            _ => {
                let _ = write!(text, "Invasion successful: {}", BlockKey::from_raw(block));
            }
        }
        append_host(&mut text, host);
        Some(text)
    }

    /// Feed a destination that arrived while the filter was switched off.
    ///
    /// The banner is not a by-product of filtering. With the master switch off nothing is judged,
    /// so there is no verdict to report -- but where the server just sent the player is still the
    /// single most useful thing this surface can say, and it is what the player will want when
    /// this grows into a fuller readout.
    ///
    /// Deliberately worded as a statement of fact rather than approval: the mod did not choose
    /// this destination and must not appear to have blessed it.
    pub fn observe_arrival(
        &mut self,
        enabled: bool,
        block: u32,
        place: Option<&str>,
        host: Option<&str>,
    ) -> Option<String> {
        let repeat = self.last_announced == Some(Announced::Arrived(block));
        self.last_announced = Some(Announced::Arrived(block));
        self.suppressed = 0;
        if repeat || !enabled {
            return None;
        }
        let mut text = String::new();
        match place {
            Some(place) if !place.is_empty() => {
                let _ = write!(text, "Invading {place}");
            }
            _ => {
                let _ = write!(text, "Invading {}", BlockKey::from_raw(block));
            }
        }
        append_host(&mut text, host);
        Some(text)
    }

    /// Feed an attempt the deadline called lost. Returns the text to display, or `None`.
    ///
    /// `attempt` distinguishes one failed hunt from the next; see [`Announced::Failed`]. The caller
    /// owns the count because this type deliberately accumulates nothing across attempts.
    ///
    /// No place and no host: join data never arrived, so there is no destination to name. Saying
    /// where would mean naming the last place the player *was* told about, which reads as a
    /// rejection from somewhere they never reached.
    pub fn observe_failure(&mut self, enabled: bool, attempt: u32) -> Option<String> {
        let repeat = self.last_announced == Some(Announced::Failed(attempt));
        self.last_announced = Some(Announced::Failed(attempt));
        // A failure ends the run of rejections it followed, exactly as a success does; the count
        // belongs to that run.
        self.suppressed = 0;
        if repeat || !enabled {
            return None;
        }
        // Worded as the outcome first, like the other three, so the banner reads as one surface
        // reporting four results rather than four unrelated messages. "No connection" rather than
        // "timed out" on purpose: Seamless's timeout has not fired yet, and claiming it had would
        // be reporting something this mod did not observe.
        Some("Invasion failed -- no connection".to_string())
    }

    /// How many rejections have been suppressed since the last announcement.
    #[must_use]
    pub const fn suppressed(&self) -> usize {
        self.suppressed
    }

    /// Forget what was last announced, so the next rejection speaks up again.
    ///
    /// Called when a search ends — a new hunt is a new question, and the first rejection of it is
    /// worth hearing even if it lands somewhere announced during the previous one.
    pub fn reset(&mut self) {
        self.last_announced = None;
        self.suppressed = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The first step is where the player is standing, and must not read as a failure.
    #[test]
    fn the_first_step_does_not_announce_a_failure_before_anything_failed() {
        let mut notice = RejectNotice::default();
        let said = notice
            .observe_prefilter_step(true, 1, 9, Some("Liurnia Lake Shore"))
            .expect("the first step is news");
        assert!(said.contains("Liurnia Lake Shore"));
        assert!(
            !said.contains("No invasion"),
            "step one is the search starting, not a place that came back empty: {said}"
        );
    }

    /// From the second step on, the count is what resolves the ambiguity.
    #[test]
    fn a_later_step_counts_the_nearby_locations_excluding_the_centre() {
        let mut notice = RejectNotice::default();
        notice.observe_prefilter_step(true, 1, 9, None);
        let said = notice
            .observe_prefilter_step(true, 4, 9, Some("Stormhill"))
            .expect("a new ordinal is news");
        assert!(
            said.contains("3 of 8"),
            "the centre is not a nearby location: {said}"
        );
        assert!(said.contains("Stormhill"));
    }

    /// A tile id is never shown in place of a name.
    ///
    /// `m60_51_36_00` tells a player nothing, so the banner counts instead. The count alone still
    /// separates "nobody is nearby" from "three tiles left to ask about", which is the whole
    /// reason the rotation announces itself.
    #[test]
    fn a_missing_name_leaves_the_count_rather_than_showing_a_tile_id() {
        let mut notice = RejectNotice::default();
        notice.observe_prefilter_step(true, 1, 9, None);
        let said = notice
            .observe_prefilter_step(true, 2, 9, None)
            .expect("a new ordinal is news");
        assert!(said.contains("1 of 8"));
        assert!(
            !said.contains("m60"),
            "no tile id may reach a player: {said}"
        );
    }

    /// Re-asking for the same step does not repaint the banner every frame.
    #[test]
    fn the_same_step_twice_is_announced_once() {
        let mut notice = RejectNotice::default();
        assert!(notice.observe_prefilter_step(true, 2, 9, None).is_some());
        assert!(notice.observe_prefilter_step(true, 2, 9, None).is_none());
        assert!(
            notice.observe_prefilter_step(true, 3, 9, None).is_some(),
            "moving on is news again"
        );
    }

    /// With the notice switched off the search still advances, silently.
    #[test]
    fn a_disabled_notice_announces_nothing_but_still_tracks_the_step() {
        let mut notice = RejectNotice::default();
        assert_eq!(notice.observe_prefilter_step(false, 2, 9, None), None);
        assert_eq!(
            notice.observe_prefilter_step(true, 2, 9, None),
            None,
            "the step was still recorded, so re-announcing it would be a repeat"
        );
    }

    const LIMGRAVE: u32 = 0x3c2a_2400; // m60_42_36_00

    /// The host is an addition to the line, never a replacement for the place.
    ///
    /// The place is the only thing the player cannot get from anywhere else; a persona name is a
    /// courtesy. So the assertion is on both halves being present and in that order, not on the
    /// whole string, which is what would break the next time the wording moves.
    #[test]
    fn a_known_host_is_named_after_the_place_on_all_three_lines() {
        for text in [
            RejectNotice::default()
                .observe(
                    true,
                    LIMGRAVE,
                    RejectReason::WrongPlaceName,
                    Some("Limgrave"),
                    Some("energygod18"),
                )
                .expect("a first rejection speaks"),
            RejectNotice::default()
                .observe_success(true, LIMGRAVE, Some("Limgrave"), Some("energygod18"))
                .expect("a first success speaks"),
            RejectNotice::default()
                .observe_arrival(true, LIMGRAVE, Some("Limgrave"), Some("energygod18"))
                .expect("a first arrival speaks"),
        ] {
            let place = text.find("Limgrave").expect("the place survives: {text}");
            let host = text.find("energygod18").expect("the host is named: {text}");
            assert!(place < host, "place first, host second: {text}");
        }
    }

    /// An unknown host leaves the line byte-identical to what it was before hosts were reachable.
    #[test]
    fn an_unknown_host_changes_nothing() {
        assert_eq!(
            RejectNotice::default().observe_arrival(true, LIMGRAVE, Some("Limgrave"), None),
            Some("Invading Limgrave".to_owned())
        );
        assert_eq!(
            RejectNotice::default().observe_arrival(true, LIMGRAVE, Some("Limgrave"), Some("  ")),
            Some("Invading Limgrave".to_owned()),
            "a blank name is not a name"
        );
    }

    /// A maximal persona name must not push the place off a surface that truncates rather than
    /// wraps.
    #[test]
    fn a_long_host_name_is_cut_and_the_place_survives() {
        let text = RejectNotice::default()
            .observe_arrival(
                true,
                LIMGRAVE,
                Some("Limgrave"),
                Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            )
            .expect("a first arrival speaks");
        assert!(text.starts_with("Invading Limgrave -- "), "{text}");
        assert!(text.ends_with('\u{2026}'), "the cut is visible: {text}");
        let name = text
            .split_once(" -- ")
            .expect("the separator is there: {text}")
            .1;
        assert_eq!(
            name.chars().filter(|c| *c == 'a').count(),
            HOST_NAME_MAX_CHARS,
            "{text}"
        );
    }
    const ELSEWHERE: u32 = 0x1501_0000; // m21_01_00_00

    /// A different block is routinely the same place in another player's world, and the banner has
    /// to say so. Reported live 2026-09-08: standing in The First Step, the notice read
    /// `Rejected The First Step (elsewhere)` -- naming the place the player was looking at and
    /// then calling it somewhere else.
    #[test]
    fn a_wrong_block_is_reported_as_another_world_not_as_elsewhere() {
        // The name of this test is the contract, and for a while the assertion under it read
        // "cancelled" while the name promised "another world" -- a test that could never fail and
        // never tell the truth. The banner already opens with `Rejected`, so a second word for the
        // same action says nothing; the fact the player cannot see is which world it was.
        for reason in [RejectReason::WrongBlock, RejectReason::WrongPlaceName] {
            assert_eq!(reason_phrase(reason), "another world");
        }
        // The reasons that lead somewhere else must stay distinguishable, or this has traded one
        // confusion for a worse one: these three each ask the player to do a different thing.
        assert_eq!(reason_phrase(RejectReason::NotNamed), "not on your list");
        assert_eq!(
            reason_phrase(RejectReason::ExcludedByUser),
            "you excluded it"
        );
        assert_eq!(
            reason_phrase(RejectReason::CandidateUnnamed),
            "open your map"
        );
    }

    #[test]
    fn the_first_rejection_at_a_place_is_announced() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
            .expect("announced");
        assert!(text.contains("m60_42_36_00"), "names the place: {text}");
        assert!(text.starts_with("Rejected"), "says what happened: {text}");
    }

    #[test]
    fn a_named_area_is_printed_instead_of_the_block_id() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe(
                true,
                LIMGRAVE,
                RejectReason::WrongPlaceName,
                Some("Limgrave"),
                None,
            )
            .expect("announced");
        assert!(text.contains("Limgrave"), "names the area: {text}");
        assert!(
            !text.contains("m60_42_36_00"),
            "the raw block id must not survive alongside the name -- the banner is one short line \
             and the id is the part a player cannot read: {text}"
        );
        assert!(
            text.contains("another world"),
            "still says what happened: {text}"
        );
    }

    #[test]
    fn the_block_id_is_the_fallback_when_nothing_has_a_name_yet() {
        // Before the world map is opened nothing has a name, which is the same condition that makes
        // `area` mode fail closed. A rejection with no place at all would be worse than an
        // unfriendly one, so the id stays as the fallback rather than being dropped.
        let mut notice = RejectNotice::new();
        let text = notice
            .observe(
                true,
                LIMGRAVE,
                RejectReason::NothingToMatchAgainst,
                None,
                None,
            )
            .expect("announced");
        assert!(text.contains("m60_42_36_00"), "{text}");
        assert!(text.contains("open your map"), "{text}");
    }

    #[test]
    fn an_empty_name_falls_back_rather_than_printing_nothing() {
        // A resolver that returns Some("") would otherwise render "Rejected  (elsewhere)", which
        // reads as a bug in the mod rather than as a rejection.
        let mut notice = RejectNotice::new();
        let text = notice
            .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, Some(""), None)
            .expect("announced");
        assert!(text.contains("m60_42_36_00"), "{text}");
    }

    /// The point of the module. Seamless retries every ~20s and the same wrong place recurs; a
    /// banner every time is wallpaper, and a notification the player ignores is worse than none.
    #[test]
    fn consecutive_rejections_at_the_same_place_stay_silent() {
        let mut notice = RejectNotice::new();
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some()
        );
        for _ in 0..20 {
            assert_eq!(
                notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None),
                None
            );
        }
        assert_eq!(notice.suppressed(), 20);
    }

    #[test]
    fn a_different_place_is_new_information_and_is_announced() {
        let mut notice = RejectNotice::new();
        notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None);
        let text = notice
            .observe(true, ELSEWHERE, RejectReason::WrongPlaceName, None, None)
            .expect("announced");
        assert!(text.contains("m21_01_00_00"), "{text}");
        assert_eq!(
            notice.suppressed(),
            0,
            "the suppressed run resets on a change"
        );
    }

    /// Returning somewhere announced earlier is announced again: the player's question is where
    /// they are being sent now, not where they have ever been sent.
    #[test]
    fn returning_to_an_earlier_place_announces_again() {
        let mut notice = RejectNotice::new();
        notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None);
        notice.observe(true, ELSEWHERE, RejectReason::WrongPlaceName, None, None);
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some()
        );
    }

    /// Disabled must still advance the state. Otherwise switching the option on mid-session would
    /// announce a place the player was rejected from minutes ago, as though it had just happened.
    #[test]
    fn disabled_stays_silent_but_still_tracks_where_we_are() {
        let mut notice = RejectNotice::new();
        assert_eq!(
            notice.observe(false, LIMGRAVE, RejectReason::WrongPlaceName, None, None),
            None
        );
        // Same place, now enabled: still silent, because we already know we are being sent there.
        assert_eq!(
            notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None),
            None
        );
        // Somewhere new, though, is genuinely new.
        assert!(
            notice
                .observe(true, ELSEWHERE, RejectReason::WrongPlaceName, None, None)
                .is_some()
        );
    }

    /// A fresh search is a fresh question; the first rejection of it is worth hearing again.
    #[test]
    fn a_reset_makes_the_next_rejection_speak_up() {
        let mut notice = RejectNotice::new();
        notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None);
        assert_eq!(
            notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None),
            None
        );
        notice.reset();
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some()
        );
        assert_eq!(notice.suppressed(), 0);
    }

    /// The banner is width-bounded by the donor string's allocation, so the text has to stay short
    /// or it is silently truncated. Checked across every reason, not one -- the bound is only
    /// meaningful if it holds for the longest phrase, and adding a wordier reason later is exactly
    /// how this would regress unnoticed.
    #[test]
    fn every_message_stays_short_enough_for_the_banner() {
        for reason in [
            RejectReason::WrongBlock,
            RejectReason::WrongPlaceName,
            RejectReason::NotNamed,
            RejectReason::CandidateUnnamed,
            RejectReason::NothingToMatchAgainst,
            RejectReason::ExcludedByUser,
        ] {
            let mut notice = RejectNotice::new();
            let text = notice
                .observe(true, LIMGRAVE, reason, None, None)
                .expect("announced");
            assert!(
                text.chars().count() <= 40,
                "banner text must stay short; {reason:?} gave {} chars: {text}",
                text.chars().count()
            );
            assert!(
                text.contains("m60_42_36_00"),
                "still names the place: {text}"
            );
        }
    }

    /// The reason is the whole point of the notice: Where alone does not tell the player whether to
    /// move, un-exclude a place, or open their map.
    #[test]
    fn the_message_names_why_not_just_where() {
        let mut notice = RejectNotice::new();
        let excluded = notice
            .observe(true, LIMGRAVE, RejectReason::ExcludedByUser, None, None)
            .expect("announced");
        assert!(excluded.contains("excluded"), "{excluded}");
        let mut notice = RejectNotice::new();
        let elsewhere = notice
            .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
            .expect("announced");
        assert_ne!(
            excluded, elsewhere,
            "different causes must read differently"
        );
    }

    /// A different reason at the same place is new information -- the place stopped being refused
    /// for the old cause -- so it is announced rather than suppressed as a repeat.
    #[test]
    fn a_new_reason_at_the_same_place_is_announced() {
        let mut notice = RejectNotice::new();
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some()
        );
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::ExcludedByUser, None, None)
                .is_some()
        );
    }

    #[test]
    fn a_success_is_announced_with_the_place_name() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe_success(true, LIMGRAVE, Some("Limgrave"), None)
            .expect("announced");
        assert!(
            text.starts_with("Invasion successful"),
            "says what happened: {text}"
        );
        assert!(text.contains("Limgrave"), "names the place: {text}");
    }

    #[test]
    fn a_success_falls_back_to_the_block_id_like_a_rejection_does() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe_success(true, LIMGRAVE, None, None)
            .expect("announced");
        assert!(text.contains("m60_42_36_00"), "{text}");
        let empty = RejectNotice::new()
            .observe_success(true, LIMGRAVE, Some(""), None)
            .expect("announced");
        assert!(
            empty.contains("m60_42_36_00"),
            "an empty name must not print nothing: {empty}"
        );
    }

    #[test]
    fn a_success_clears_the_rejection_latch_so_a_later_rejection_speaks() {
        // The reported bug. Rejected at a place, invaded successfully, rejected at that same place
        // again: the third event is news and was being swallowed as a repeat of the first.
        let mut notice = RejectNotice::new();
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some()
        );
        assert!(
            notice
                .observe_success(true, ELSEWHERE, None, None)
                .is_some()
        );
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some(),
            "a rejection after a success is new information, not a repeat"
        );
    }

    #[test]
    fn a_success_after_rejections_is_always_announced() {
        let mut notice = RejectNotice::new();
        for _ in 0..5 {
            notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None);
        }
        assert!(
            notice
                .observe_success(true, LIMGRAVE, Some("Limgrave"), None)
                .is_some(),
            "arriving where you were previously rejected is the whole point of the hunt"
        );
    }

    #[test]
    fn the_same_success_twice_stays_quiet() {
        let mut notice = RejectNotice::new();
        assert!(notice.observe_success(true, LIMGRAVE, None, None).is_some());
        assert!(
            notice.observe_success(true, LIMGRAVE, None, None).is_none(),
            "one arrival, one banner"
        );
    }

    #[test]
    fn a_success_clears_the_suppressed_run_it_ended() {
        let mut notice = RejectNotice::new();
        notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None);
        notice.observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None);
        assert_eq!(notice.suppressed(), 1);
        notice.observe_success(true, LIMGRAVE, None, None);
        assert_eq!(notice.suppressed(), 0, "the run of rejections is over");
    }

    #[test]
    fn a_disabled_notice_still_advances_on_success() {
        // Turning the option on mid-session must not replay an arrival from minutes ago.
        let mut notice = RejectNotice::new();
        assert!(
            notice
                .observe_success(false, LIMGRAVE, None, None)
                .is_none()
        );
        assert!(
            notice.observe_success(true, LIMGRAVE, None, None).is_none(),
            "the state advanced while silent, so this is still the same arrival"
        );
    }

    #[test]
    fn an_arrival_is_announced_when_the_filter_is_switched_off() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe_arrival(true, LIMGRAVE, Some("Limgrave"), None)
            .expect("announced");
        assert!(text.contains("Limgrave"), "names the place: {text}");
        assert!(
            !text.contains("successful") && !text.contains("Rejected"),
            "an unjudged arrival must claim neither a verdict nor an outcome: {text}"
        );
    }

    #[test]
    fn an_arrival_falls_back_to_the_block_id_like_the_others() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe_arrival(true, LIMGRAVE, None, None)
            .expect("announced");
        assert!(text.contains("m60_42_36_00"), "{text}");
    }

    #[test]
    fn the_same_arrival_twice_stays_quiet_but_a_new_one_speaks() {
        let mut notice = RejectNotice::new();
        assert!(notice.observe_arrival(true, LIMGRAVE, None, None).is_some());
        assert!(notice.observe_arrival(true, LIMGRAVE, None, None).is_none());
        assert!(
            notice
                .observe_arrival(true, ELSEWHERE, None, None)
                .is_some()
        );
    }

    #[test]
    fn an_arrival_and_a_verdict_do_not_suppress_each_other() {
        // The three kinds share one latch, so each must count as news after either other kind.
        let mut notice = RejectNotice::new();
        assert!(notice.observe_arrival(true, LIMGRAVE, None, None).is_some());
        assert!(
            notice
                .observe(true, LIMGRAVE, RejectReason::WrongPlaceName, None, None)
                .is_some(),
            "a rejection after an arrival at the same block is a different statement"
        );
        assert!(
            notice.observe_success(true, LIMGRAVE, None, None).is_some(),
            "and so is an arrival that became a real invasion"
        );
    }

    #[test]
    fn a_disabled_notice_still_advances_on_arrival() {
        let mut notice = RejectNotice::new();
        assert!(
            notice
                .observe_arrival(false, LIMGRAVE, None, None)
                .is_none()
        );
        assert!(
            notice.observe_arrival(true, LIMGRAVE, None, None).is_none(),
            "turning the notice on must not replay an arrival from minutes ago"
        );
    }
    /// The rung that was invisible. Announced once, then suppressed -- it is re-derived on every
    /// query round, and repainting the banner every fifteen seconds is what the shared latch
    /// exists to prevent.
    #[test]
    fn widening_to_everywhere_is_announced_once_and_then_suppressed() {
        let mut notice = RejectNotice::new();
        assert_eq!(
            notice.observe_search_everywhere(true, 48, false).as_deref(),
            Some("No invasion in 48 nearby locations -- looking everywhere instead")
        );
        assert_eq!(notice.observe_search_everywhere(true, 48, false), None);
        assert_eq!(notice.observe_search_everywhere(true, 48, false), None);
    }

    /// A ring of one is a legacy dungeon, where the radius could never have applied. Saying
    /// "no invasion in 0 nearby locations" there would be arithmetic rather than English.
    #[test]
    fn a_ring_with_no_neighbours_says_so_instead_of_counting_zero() {
        let mut notice = RejectNotice::new();
        let text = notice
            .observe_search_everywhere(true, 0, false)
            .expect("the first escalation is news");
        assert!(
            text.contains("No nearby locations to search here"),
            "{text}"
        );
        assert!(!text.contains('0'), "{text}");
    }

    /// A step between two exhaustions makes the second one news again, the same way two failed
    /// connections in a row are two pieces of news rather than one repeated.
    #[test]
    fn a_step_between_two_exhaustions_unsuppresses_the_second() {
        let mut notice = RejectNotice::new();
        assert!(notice.observe_search_everywhere(true, 8, false).is_some());
        assert!(
            notice
                .observe_prefilter_step(true, 2, 9, Some("Limgrave"))
                .is_some()
        );
        assert!(notice.observe_search_everywhere(true, 8, false).is_some());
    }

    /// Gated on the same option as every other banner: somebody who turned notices off does not
    /// start getting them because their search widened.
    #[test]
    fn the_widened_search_banner_respects_the_notice_switch() {
        let mut notice = RejectNotice::new();
        assert_eq!(notice.observe_search_everywhere(false, 48, false), None);
    }
}
