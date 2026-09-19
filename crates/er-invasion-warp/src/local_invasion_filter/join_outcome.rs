//! What became of a join: whether the engine failed it, and what its progress fields say.
//!
//! Split out of `local_invasion_filter.rs` on 2026-09-17 when that file crossed the 3,200-line hard
//! limit in `scripts/check-rust-file-sizes.py`, along the seam that was already there. Everything
//! here answers "did this attempt land, and if not what does the engine say about it": the dead
//! match `drop_a_match_the_engine_has_already_failed` clears, the `JoinProgress` read that feeds
//! it, and the trace that reports both. Nothing here decides whether to accept a destination --
//! that is the filter's own question and stays with it.
//!
//! The statics these read -- `DEAD_JOIN_SINCE_MS`, `AUTO_SEARCH_ARMED`, `PENDING_REINVADE` and the
//! rest -- stay in the parent, reached through `use super::*`. They are the filter's state, not
//! this module's, and moving them would split one machine across two files.

use super::*;

/// Drop a match the engine has already failed, without waiting for the player.
///
/// # The complaint
///
/// Reported live 2026-09-10: "Seamless claiming a live match means I have to use the item to leave
/// before I can invade again". That is the state this clears. Seamless keeps its session at an
/// active value after the engine's join has died, and nothing in the game takes it back down, so
/// the player is holding a match that cannot become an invasion and the only way out is to spend
/// the item on a cancel.
///
/// # Why this can act where the stall watchdog cannot
///
/// `crate::stall_watchdog` returns before it reads anything unless `AUTO_SEARCH_ARMED`, because it
/// is part of the hunt loop -- a search the player started themselves, or one whose loop has stood
/// down, gets no watchdog at all. And its trigger is a dwell: five seconds in a timed state, which
/// is a guess that something is wrong.
///
/// This is not a guess and not a dwell. `Verdict::Failed` means `lobbyState` is `CreateFailed` or
/// `JoinFailed` -- the engine went out to Steam and was told no -- so the attempt is over as a
/// matter of fact, whoever started it. Until the `Failed` verdict existed this reading was
/// `Verdict::Idle`, which also means "the engine never started", and the two cannot be told apart:
/// acting on `Idle` would have cancelled attempts that had not begun yet.
///
/// The grace window exists only so a frame sampled across a transition cannot fire it.
#[cfg(windows)]
fn drop_a_match_the_engine_has_already_failed(
    progress: &er_invasion_warp_core::join_progress::JoinProgress,
    ersc_claims_attempt: bool,
) {
    use er_invasion_warp_core::join_progress::Verdict;
    // `Failed` is the engine saying Steam told it no. `Idle` here is narrower than it sounds: the
    // verdict only reads `Idle` with `lobbyState == None`, no outstanding RPC and both phase timers
    // clear, which is `DisconnectCleanup` having run. Either way the engine has no session, and
    // Seamless is holding a match against one.
    let engine_has_no_session =
        progress.lobby_state == er_invasion_warp_core::join_progress::lobby_state::NONE;
    let grace = match progress.verdict() {
        Verdict::Failed => DEAD_JOIN_GRACE_MS,
        Verdict::Idle => TORN_DOWN_GRACE_MS,
        Verdict::Progressing | Verdict::Committed => {
            DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
            return;
        }
    };
    if !ersc_claims_attempt {
        DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
        return;
    }
    let now = now_ms();
    let since =
        match DEAD_JOIN_SINCE_MS.compare_exchange(0, now, Ordering::SeqCst, Ordering::SeqCst) {
            Ok(_) => now,
            Err(first) => first,
        };
    let held = now.saturating_sub(since);
    if held < grace {
        return;
    }
    let Ok(session) = resolve_session() else {
        return;
    };
    let Some(state) = read_session_state(session.abi, session.session) else {
        return;
    };
    if state == session.abi.state_idle {
        DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
        return;
    }
    // Searching is not a dead match. It is the state that means nobody has answered yet, and it is
    // unbounded by nature -- `connect_phase` already refuses to time it for that reason, and
    // `stall_watchdog`'s own doc records what happens when something does: "a player who started a
    // hunt got a search that killed itself, every time, which is what invasions just fail looks
    // like from their seat".
    //
    // This path reached it through `ersc_claims_attempt`, which is only `ersc_state != idle`, so
    // every searching frame read as an attempt Seamless was holding against an engine with no
    // session. That is also true of a search with nobody in range, and the two are identical from
    // here. Measured on run `br-20260916-011647-2736`: seven rounds, each cancelled at ~8s with
    // `lobby=0 proto=6`, so the prefilter ring never widened once and the everywhere rung was
    // unreachable.
    if state == session.abi.state_searching {
        DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
        return;
    }
    // A handshake in progress is not a dead match, and this detector was cancelling every one.
    //
    // Measured on run br-20260916-083935-5990: nineteen rounds, each one Seamless matching a host,
    // walking `0x0e -> 0x0f -> 0x12`, and sitting at `0x12` until this path cancelled it at ~8s
    // with `lobby=0 proto=6 rpc=0`. The same signature the searching carve-out above was written
    // from, one state later. The hunt therefore never got past the handshake in a run where
    // `RequestLobbyList reached our detour` and Seamless was answering -- so "Seamless's networking
    // is dormant" (bd er-effects-rs-bfln) was this code cancelling, not Seamless declining.
    //
    // `lobbyState == None` cannot mean the attempt is dead here, because in a Seamless invasion it
    // is `None` at every point: Seamless owns the session and the engine's lobby is never used.
    // The `state_in_world` carve-out below already concedes exactly that at `0x16`, and there is
    // no reason the premise would be false at `0x16`, true at `0x12`, and false again at `0x0e`.
    //
    // The set deferred to is `connect_phase`'s own, derived from ERSC's Cancel-row predicate, so
    // this cannot drift from the states the deadline already refuses to cancel. That path reached
    // the same conclusion for the same states and kept the observation while dropping the action
    // ("the deadline reports and no longer cancels"); this is the second door onto that action and
    // now answers the same way. What is given up is the 2026-09-10 complaint's recovery during a
    // connect only -- a genuinely stuck `0x12` now waits for Seamless's own timeout to return it
    // to idle, where this detector still acts.
    if connect_phase(session.abi, state)
        == er_invasion_warp_core::attempt_verdict::Phase::Connecting
    {
        DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
        actions::log_refusal_once(
            &CONNECTING_DROP_REFUSAL_SAID,
            format_args!(
                "local-invasion: NOT dropping this match -- the engine reports no session, but \
                 Seamless reads {state:#04x}, which is a connect in progress. The engine's lobby \
                 is never used in a Seamless invasion, so its being empty says nothing here. \
                 Cancelling this is what ended all nineteen matches of run br-20260916-083935-5990."
            ),
        );
        return;
    }
    // `state_in_world` is the player standing in the host's world, and this path must not touch it.
    //
    // The engine reading `lobbyState == None` is what brought us here, and in a Seamless invasion
    // that is not proof the attempt is dead: Seamless owns the session, and `0x16` is its word for
    // "the invasion landed". The two readings disagree because they describe different things, and
    // when they do, the one that can see the player is right.
    //
    // Acting anyway costs the game. The Cancel row is withdrawn at `0x16`, so `cancel_stalled_
    // attempt_inner` falls through to `OPTIONSELECT_LEAVEWORLD` and tears the player out of a live
    // invasion -- a hard lock in run br-20260915-025202-c779, and again in br-20260915-173901-7934,
    // the first run whose lobby query was genuinely unfiltered and so the first to reach a real
    // host at all. `connect_phase` already refuses this state for the deadline path; this caller
    // is a second door onto the same action and did not.
    //
    // What is given up is real and much smaller: a player genuinely stranded holding a dead match
    // at `0x16` now spends the invasion item to leave instead of being recovered automatically.
    // Nothing distinguishes stranded from invading by state alone, so the choice is between
    // occasionally costing an item and occasionally hard-locking the game.
    if state == session.abi.state_in_world {
        DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
        actions::log_refusal_once(
            &IN_WORLD_DROP_REFUSAL_SAID,
            format_args!(
                "local-invasion: NOT dropping this match -- the engine reports no session, but \
                 Seamless reads {state:#04x}, which is the player standing in the host's world. \
                 Cancelling here drives OPTIONSELECT_LEAVEWORLD, which has hard-locked the game \
                 twice. If the match really is dead, leaving costs the invasion item."
            ),
        );
        return;
    }
    // Cleared before the cancel rather than after it, so a cancel that is refused -- a poisoned
    // guard, a lock the wrong shape -- re-arms the window instead of latching this off forever.
    DEAD_JOIN_SINCE_MS.store(0, Ordering::SeqCst);
    let count = DEAD_JOIN_RECOVERIES.fetch_add(1, Ordering::SeqCst) + 1;
    crate::standalone_log(format_args!(
        "local-invasion: dropping a match the engine has no session for (#{count}) -- {progress}, \
         held {held}ms while Seamless still read {state:#04x}. Nothing in the game takes that back \
         down, so without this the player has to spend the invasion item to leave before invading \
         again."
    ));
    cancel_stalled_attempt_inner(session, state, held, engine_has_no_session);
}

#[cfg(windows)]
pub(crate) fn trace_join_progress(session: Result<(u32, u32), &'static str>) {
    let ersc_state = session.ok().map(|(state, _)| state);
    let idle_state = session.map_or(u32::MAX, |(_, idle)| idle);
    let Some(progress) = read_join_progress() else {
        return;
    };
    note_session_liveness(
        ersc_state,
        progress.lobby_state as u32,
        progress.protocol_state as u32,
    );
    // `Client` and nothing else. `call_for_warp` is tempting and WRONG: `WarpNextStageKick_` runs
    // for every warp including a plain fast travel, so latching on it would mark an ordinary grace
    // warp as an invasion.
    if progress.lobby_state == er_invasion_warp_core::join_progress::lobby_state::CLIENT {
        // The join landed, so the session is allowed to read idle again.
        JOIN_IN_FLIGHT.store(false, Ordering::SeqCst);
        // And the accept is settled: from here `INVASION_ACTUALLY_HAPPENED` owns the disarm.
        KEPT_JOIN_PENDING.store(false, Ordering::SeqCst);
    }
    resume_the_hunt_if_an_accepted_join_died(&progress);
    if progress.lobby_state == er_invasion_warp_core::join_progress::lobby_state::CLIENT
        && !INVASION_ACTUALLY_HAPPENED.swap(true, Ordering::SeqCst)
    {
        // The join landed. This is the moment "Invasion successful" is true -- measured at
        // 0.57-3.5s after join data on every real join, and never reached by a match that dies.
        let pending = PENDING_SUCCESS_BLOCK.swap(usize::MAX, Ordering::SeqCst);
        if pending != usize::MAX
            && let Some(config) = current_config()
        {
            banner::announce_success(config.reject_notice, pending as u32);
        }
    }
    // Pack the reading so an unchanged frame costs one atomic compare and no formatting.
    let packed = (u64::from(progress.lobby_state as u32) << 40)
        | (u64::from(progress.protocol_state as u32) << 24)
        | (u64::from(u8::from(progress.join_request_handle != 0)) << 16)
        | (u64::from(u8::from(progress.join_check_remain > 0.0)) << 8)
        | u64::from(u8::from(progress.call_for_warp));
    // Only an attempt ERSC actually claims can be a stalled one. An unresolvable session is not
    // evidence of anything, so it never counts.
    let ersc_claims_attempt = ersc_state.is_some_and(|state| state != idle_state);
    if progress.is_finished() && ersc_claims_attempt {
        JOIN_PROGRESS_IDLE_SAMPLES.fetch_add(1, Ordering::Relaxed);
    }
    drop_a_match_the_engine_has_already_failed(&progress, ersc_claims_attempt);
    if JOIN_PROGRESS_LAST.swap(packed, Ordering::SeqCst) == packed {
        return;
    }
    let since_join = match JOIN_DATA_AT_MS.load(Ordering::SeqCst) {
        0 => String::new(),
        at => format!(" +{}ms since join data", now_ms().saturating_sub(at)),
    };
    crate::standalone_log(format_args!(
        "join-progress: ersc={} {progress}{since_join}",
        match session {
            Ok((state, _)) => format!("{state:#04x}"),
            Err(reason) => reason.to_owned(),
        }
    ));
}

/// One fault-closed sample of the engine-side join fields.
#[cfg(windows)]
fn read_join_progress() -> Option<er_invasion_warp_core::join_progress::JoinProgress> {
    use er_invasion_warp_core::join_progress as jp;
    use er_invasion_warp_core::warp::{
        SESSION_LOBBY_STATE_OFFSET, SESSION_MANAGER_GLOBAL_RVA, SESSION_PROTOCOL_STATE_OFFSET,
    };

    let base = er_game_base::mem::game_module_base().ok()?;
    let manager = unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            base,
            SESSION_MANAGER_GLOBAL_RVA,
            "SESSION_MANAGER_GLOBAL_RVA",
        ))
    }?;
    if manager == 0 {
        return None;
    }
    // Resolved for the running build, like the session-manager read directly above it -- the two
    // sat side by side reading the same kind of global and only one of them asked. GameMan moved
    // 0x3d69918 -> 0x3d6d988 on 1.17, so the raw form returned a neighbouring global and the
    // call-for-warp byte was read out of it.
    let game_man = unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            base,
            jp::GAME_MAN_GLOBAL_RVA,
            "GAME_MAN_GLOBAL_RVA",
        ))
    }?;
    let call_for_warp = if game_man == 0 {
        false
    } else {
        unsafe { er_game_base::mem::safe_read_u8(game_man + jp::GAME_MAN_CALL_FOR_WARP_OFFSET) }
            .is_some_and(|byte| byte != 0)
    };
    Some(jp::JoinProgress {
        lobby_state: unsafe {
            er_game_base::mem::safe_read_i32(manager + SESSION_LOBBY_STATE_OFFSET)
        }?,
        protocol_state: unsafe {
            er_game_base::mem::safe_read_i32(manager + SESSION_PROTOCOL_STATE_OFFSET)
        }?,
        join_request_handle: unsafe {
            er_game_base::mem::safe_read_i32(manager + jp::SESSION_JOIN_REQUEST_HANDLE_OFFSET)
        }?,
        join_check_remain: unsafe {
            er_game_base::mem::safe_read_f32(manager + jp::SESSION_JOIN_CHECK_REMAIN_OFFSET)
        }?,
        wait_init_remain: unsafe {
            er_game_base::mem::safe_read_f32(manager + jp::SESSION_WAIT_INIT_REMAIN_OFFSET)
        }?,
        call_for_warp,
    })
}

/// How many frames the engine looked idle while ERSC still claimed an attempt.
#[must_use]
pub fn join_progress_idle_samples() -> usize {
    JOIN_PROGRESS_IDLE_SAMPLES.load(Ordering::Relaxed)
}

/// `(keeps, cancels, automatic re-searches, unenforced rejections)` so a run can be judged without
/// reading the log.
///
/// `cancels` survives because the stall watchdog still cancels a search that is going nowhere.
/// That is a different act: it abandons an attempt the player is waiting on, not a connection they
/// already have.
#[must_use]
pub fn tallies() -> (usize, usize, usize) {
    (
        KEEPS.load(Ordering::SeqCst),
        CANCELS.load(Ordering::SeqCst),
        REINVADES.load(Ordering::SeqCst),
    )
}

// `mod tests;` does not belong here: it rode along in the extracted range but its file is
// `local_invasion_filter/tests.rs`, the parent's, and it tests the parent's surface. It is declared
// there instead.

/// What to say after "cannot cancel -- SessionNotIdentified", built from the scan's live state.
///
/// Not an instruction to the player. The one thing that narrows the differential scan is another
/// invasion, and the numbers say how close it is, so the line reports progress and leaves it at
/// that. Returns a `&'static str` because the refusal is latched and logged once; the counts are
/// rendered into a leaked string only on the pass that actually prints.
#[cfg(windows)]
pub(crate) fn not_identified_detail() -> &'static str {
    let (held, rounds) = differential_scan::progress();
    if held == 0 {
        return ". No candidates are recorded, so the sweeper has not managed an idle pass yet -- \
                nothing to narrow.";
    }
    Box::leak(
        format!(
            ". The differential scan is down to {held} candidate(s) after {rounds} join(s); it \
             adopts one only when a single candidate is left, and each invasion narrows it \
             further. Opening Seamless's menu does NOT help -- the observer that would learn the \
             object from it is disabled because detouring ersc.dll faults the game."
        )
        .into_boxed_str(),
    )
}

#[cfg(not(windows))]
pub(crate) fn not_identified_detail() -> &'static str {
    ""
}
