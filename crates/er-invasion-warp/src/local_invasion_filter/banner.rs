//! The banner the player actually sees, and the one latch behind all three of its messages.
//!
//! Split out of `local_invasion_filter` on 2026-09-08 when that file crossed the 3200-line hard
//! limit. The cut is along a real seam rather than a convenient line number: everything here
//! answers "what does the player get told", and nothing here decides anything. The judging, the
//! session identification and the driving of ERSC's own actions all stay next door.
//!
//! All three announcements share [`super::REJECT_NOTICE`] on purpose, so the surface cannot
//! contradict itself about what it last said -- a rejection notice still on screen while an
//! arrival notice is written would read as the mod having rejected the invasion it just let
//! through.

use std::sync::atomic::Ordering;

use super::{NOTICE_FAILED, REJECT_NOTICE};

/// The Steam persona name of the host this match belongs to, or `None`.
///
/// # Two hops, both measured in a live game
///
/// `super::host_steam_id` reads `session+0x1d8`, which Seamless fills at the same transition that
/// puts the host's lobby in `+0x1d0` and clears on the way back to idle; then
/// `crate::lobby_publish::persona_name` asks Steam for the name behind that id. Neither hop is an
/// inference: run br-20260910-042516-3b5d caught the field being written on two separate
/// invasions, and the two ids it held answered "Paperplane" and "energygod18" when the call was
/// made against the running process.
///
/// `None` at either hop means the banner simply says where, as it did before. A name is an
/// addition to the line, never a precondition for it.
#[cfg(windows)]
fn host_name() -> Option<String> {
    crate::lobby_publish::persona_name(super::host_steam_id()?)
}

/// Host-side stub.
#[cfg(not(windows))]
pub(super) fn announce_failure(_enabled: bool, _attempt: u32) {}

/// Host-side stub.
#[cfg(not(windows))]
pub(super) fn announce_arrival(_enabled: bool, _destination: u32) {}

/// Report a destination that arrived while the filter was switched off.
///
/// Shares the one notice latch with the verdict banners, so the surface never contradicts itself
/// about what it last said.
#[cfg(windows)]
pub(super) fn announce_arrival(enabled: bool, destination: u32) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        let place = crate::place_name::place_name_for_block(destination);
        let host = host_name();
        guard.observe_arrival(enabled, destination, place.as_deref(), host.as_deref())
    };
    let Some(text) = announcement else {
        return;
    };
    // SAFETY: game thread, inside the join-data hook -- the same context and surface as the
    // verdict banners.
    if !unsafe { crate::announce::show(&text) } {
        if NOTICE_FAILED.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::standalone_log(format_args!(
            "local-invasion: could not show the arrival banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up yet."
        ));
    }
}

/// Tell the player a connection is dead, at the moment a working one would already have landed.
///
/// Shares the one notice latch with the other three messages, so the surface cannot leave a
/// rejection on screen while reporting a failure, or the reverse.
///
/// No place and no host name here, unlike every other banner: join data never arrived, so there is
/// no destination and no host id to resolve. Naming one would mean naming whoever the player was
/// last told about, which reads as a failure to reach somewhere they never got near.
/// Deleted 2026-09-16 along with its only call site, in `watch_for_failed_connect`.
///
/// That path had already stopped cancelling, because its deadline was derived from runs this mod
/// was shaping and cannot tell a slow connect from a dead one. The banner outlived the action and
/// went on telling the player "Invasion failed -- no connection" about a connect nothing was
/// acting on. `RejectNotice::observe_failure` is kept and still tested; nothing in the DLL calls
/// it, so restoring the notice means restoring a judgement that can be defended first.
#[cfg(windows)]
const _: () = ();

/// Host-side stub; the decision half is tested against [`er_invasion_warp_core::reject_notice`].
#[cfg(not(windows))]
pub(super) fn announce_success(_enabled: bool, _destination: u32) {}

/// Host build: no banner surface.
#[cfg(not(windows))]
pub(crate) fn announce_prefilter_step(_enabled: bool, _block: u32, _ordinal: usize, _total: usize) {
}

/// Host build: no banner surface.
#[cfg(not(windows))]
pub(crate) fn announce_search_everywhere(_enabled: bool, _nearby: usize, _mod_only: bool) {}

/// Host build: no banner surface.
#[cfg(not(windows))]
pub(crate) fn announce_found_host(_enabled: bool, _block: u32) {}

/// Host build: no banner surface.
#[cfg(not(windows))]
pub(crate) fn announce_nothing_to_search(_enabled: bool) {}

/// Host build: no banner surface.
#[cfg(not(windows))]
pub(crate) fn announce_cannot_search(_enabled: bool) {}

/// Put a successful invasion on the same banner the rejections use.
///
/// Shares [`RejectNotice`] with [`announce_rejection`] on purpose: one banner, one memory of what
/// it last said. That is what lets an arrival clear the rejection latch, so a later rejection at
/// the same place is announced instead of being swallowed as a repeat.
#[cfg(windows)]
pub(super) fn announce_success(enabled: bool, destination: u32) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        let place = crate::place_name::place_name_for_block(destination);
        let host = host_name();
        guard.observe_success(enabled, destination, place.as_deref(), host.as_deref())
    };
    let Some(text) = announcement else {
        return;
    };
    // SAFETY: game thread, inside the join-data hook -- the same context, and the same auto-closing
    // announcement surface, as the rejection banner.
    if !unsafe { crate::announce::show(&text) } {
        if NOTICE_FAILED.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::standalone_log(format_args!(
            "local-invasion: could not show the success banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up yet. The invasion still happened; \
             only the on-screen notice is missing."
        ));
    }
}

/// Say which place the widening search is asking for, on the same banner as everything else.
///
/// Shares [`RejectNotice`] with the rejection and success paths, so the banner keeps one memory of
/// what it last said: a step announced here clears the latch, and a rejection that follows is
/// spoken rather than swallowed as a repeat of something from before the search moved.
///
/// The name comes from [`crate::place_name::place_name_for_block`], which answers `None` until the
/// world map has been opened. That is a real gap and it is left visible rather than papered over
/// with a tile id: the count still tells the player the search is moving, which is the thing the
/// rotation would otherwise hide.
#[cfg(windows)]
pub(crate) fn announce_prefilter_step(enabled: bool, block: u32, ordinal: usize, total: usize) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        let place = crate::place_name::place_name_for_block(block);
        guard.observe_prefilter_step(enabled, ordinal, total, place.as_deref())
    };
    let Some(text) = announcement else {
        return;
    };
    // SAFETY: game thread, inside the lobby-query detour -- the same auto-closing announcement
    // surface the rejection banner uses.
    if !unsafe { crate::announce::show(&text) } {
        if NOTICE_FAILED.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::standalone_log(format_args!(
            "local-invasion: could not show the search banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up yet. The search is still widening; \
             only the on-screen notice is missing."
        ));
    }
}

/// Say that the search has run out of nearby places and dropped the location filter.
///
/// The rung this announces was previously invisible: `advance_ring` logged one line to the file
/// and returned, so the escalation happened silently and then happened again on every query round
/// for as long as the search ran. A player watching the screen saw a search that never changed.
///
/// Shares [`RejectNotice`] with every other banner here, which is what suppresses the repeat --
/// the everywhere rung is re-derived per round, so without the shared latch this would repaint
/// roughly every fifteen seconds.
#[cfg(windows)]
pub(crate) fn announce_search_everywhere(enabled: bool, nearby: usize, mod_only: bool) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        guard.observe_search_everywhere(enabled, nearby, mod_only)
    };
    let Some(text) = announcement else {
        return;
    };
    // SAFETY: game thread, inside the lobby-query detour -- the same auto-closing announcement
    // surface every other banner here uses.
    if !unsafe { crate::announce::show(&text) } {
        if NOTICE_FAILED.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::standalone_log(format_args!(
            "local-invasion: could not show the widened-search banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up yet. The search is still widening; \
             only the on-screen notice is missing."
        ));
    }
}

/// Say the sweep found somebody, naming the place it stopped on.
///
/// Called with the banner queue already cleared, which is the point: the queue and this line are
/// two halves of one fact. Leaving the queue running would keep naming the places the search has
/// just decided not to ask about, on top of the answer.
#[cfg(windows)]
pub(crate) fn announce_found_host(enabled: bool, block: u32) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        let place = crate::place_name::place_name_for_block(block);
        guard.observe_found_host(enabled, block, place.as_deref())
    };
    paint_or_log(announcement, "the found-a-host banner");
}

/// Say nobody anywhere is hosting, so the search goes out as an ordinary invasion.
///
/// Separate from the widened-search line because that one says the neighbourhood came back empty,
/// and on this path the neighbourhood was never asked: one pre-flight query settled it for
/// everywhere at once.
#[cfg(windows)]
pub(crate) fn announce_nothing_to_search(enabled: bool) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        guard.observe_nothing_to_search(enabled)
    };
    paint_or_log(announcement, "the nothing-to-search banner");
}

/// Paint a line, or say once why it could not be painted.
///
/// The three-line "show it, latch the failure, log it" tail was copied into every announcer here;
/// this is that tail, named. A banner that cannot reach the screen is never fatal -- the search it
/// describes is unaffected and only the notice is missing -- so the failure is reported once and
/// the caller carries on.
#[cfg(windows)]
fn paint_or_log(announcement: Option<String>, what: &str) {
    let Some(text) = announcement else {
        return;
    };
    // SAFETY: game thread -- the same auto-closing announcement surface every other banner uses.
    if !unsafe { crate::announce::show(&text) } {
        if NOTICE_FAILED.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::standalone_log(format_args!(
            "local-invasion: could not show {what} (\"{text}\") -- the message functions did not \
             verify, or the menu is not up yet. The search is unaffected; only the notice is missing."
        ));
    }
}

/// Tell the player the search they armed was dropped before it asked anybody.
///
/// Shares [`RejectNotice`] with every other banner here, which is what keeps this to one painting:
/// the refusal is re-derived on every tick that would otherwise drive the action, so without the
/// shared latch this repaints several times a second.
///
/// No place name, unlike the search banners. A search that never went out was not a search of
/// anywhere, and naming the tile it would have asked about reads as a search still running there.
#[cfg(windows)]
pub(crate) fn announce_cannot_search(enabled: bool) {
    let announcement = {
        let mut guard = match REJECT_NOTICE.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        guard.observe_cannot_search(enabled)
    };
    let Some(text) = announcement else {
        return;
    };
    // SAFETY: game task thread, the same auto-closing announcement surface as every other banner.
    if !unsafe { crate::announce::show(&text) } {
        if NOTICE_FAILED.swap(true, Ordering::SeqCst) {
            return;
        }
        crate::standalone_log(format_args!(
            "local-invasion: could not show the dropped-search banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up yet. The search is still dropped; \
             only the on-screen notice is missing."
        ));
    }
}
