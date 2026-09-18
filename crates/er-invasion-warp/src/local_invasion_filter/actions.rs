//! Driving Seamless's own option actions: cancelling a match, starting a search, and getting a
//! stalled attempt moving again.
//!
//! Cut out of `local_invasion_filter` on 2026-09-10, when that file crossed the 3200-line hard
//! limit. The seam is the one the parent's own docs already draw: everything here answers "how does
//! this mod press a button Seamless drew", and nothing here decides which matches deserve it.
//! Judging a destination, identifying the session and telling the player what happened all stay
//! next door, the last of them in [`super::banner`].
//!
//! Every call out of this file is the option callback a player's own click invokes, passed
//! `(owner, 0, 1, 1)` because the callee reads `rcx` and nothing else. So the questions this file
//! exists to answer are all about the owner and the moment: whose `this` is safe to hand Seamless
//! ([`ersc_owner_or_refuse`], [`synthesized_owner`]), whether the session's own lock says the
//! action would survive being called now, and what to do when an attempt stops progressing
//! ([`watch_for_stall`], [`cancel_stalled_attempt`]). Each refusal leaves the match alone, which is
//! the fail-closed direction the parent documents.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_invasion_warp_core::local_invasion::RejectReason;

use super::{
    ATTEMPT_DRIVEN, ATTEMPT_VERDICT, AUTO_SEARCH_ARMED, CANCELS, ErscActionFn, FAILED_CONNECTS,
    INVADE_ACTION_REFUSAL_SAID, INVADE_ACTION_UNCALLABLE, INVADE_GUARD_REFUSAL_SAID,
    INVADE_IN_FLIGHT, INVASION_ACTUALLY_HAPPENED, JOIN_IN_FLIGHT, MISMATCHED_ARM_SAID, NoSession,
    OurCall, PENDING_REINVADE, REINVADES, RESTART_BACKOFF, SELF_RECOVERIES, STALL_RECOVERIES,
    STALL_WATCHDOG, SeamlessSession, USE_IN_FLIGHT_SAID, cancel_row_refusal, ersc, ersc_action,
    inside_ersc_callback, lock_shape_refusal, module_backing, not_identified_detail,
    note_state_after_our_action, now_ms, read_session_state, report_lock_preconditions,
    resolve_ersc_abi, resolve_session, session_guard_refuses, session_scan,
};

/// Refuse to invoke a Seamless action with a null `this`, and say why.
///
/// This is the guard for a crash that actually happened, twice, on 2026-09-04. Every ersc action
/// here is called as `action(session.osm, ..)`, so `osm` lands in RCX as the object the callee
/// dereferences immediately. `scan_for_session` resolves the session without hooking Seamless, but
/// it has no `osm` to hand -- only the `show` detour ever supplied one -- so it returns `osm: 0`,
/// and `cancel(0, 0, 1, 1)` walked straight into a null dereference inside `ersc.dll`.
///
/// The measured chain, from the crash records of two runs with the 19-DLL profile:
///   ersc.dll+0x258da  (cancel + 0xa, `context_rcx=0x0`, 0xc0000005)
///   er_invasion_warp.dll+0x9a6b / +0x9025   <- this filter
///   ersc.dll+0x2820a / +0x636e75 / +0x28a85e
/// followed by 23 x `0xc0000026` STATUS_INVALID_UNWIND_TARGET at `ntdll.dll+0x669a8` -- the unwind
/// out of the fault could not cross our detoured frames, because MinHook registers no unwind info
/// for its trampolines. So the process died with no fatal record and no DllMain detach, leaving a
/// zombie leader with ~128 lingering threads: the "hard kill" signature this investigation kept
/// meeting and could not explain.
///
/// A declined action costs one unfiltered match. A null `this` costs the session.
///
/// Not `cfg`-gated: its callers are not either, and the host build exercises their state machine.
/// `[rcx + 0x58]` -- the only field ERSC's invade and cancel actions read off their `this`.
///
/// See `synthesized_owner` for the disassembly both actions open with.
const OWNER_SESSION_OFFSET: usize = 0x58;

/// What `CSMenuGaitemUseState+0xc` holds when no item is being used.
///
/// The field is an item id and its empty value is `-1`, which reads back as this. Treating any
/// `Some` as a use in flight is what made the item-use gate never open: run
/// br-20260916-035624-a813 logged `holding the armed search while item 0xffffffff is still being
/// used` and drove nothing.
const NO_ITEM_IN_USE: u32 = 0xffff_ffff;

fn ersc_owner_or_refuse(session: &SeamlessSession, what: &str) -> Option<usize> {
    // The identification, not just a guard on the call. `resolve_session` accepts a candidate on
    // `plausible_session_pointer` -- at least 0x10000 and 8-aligned -- plus a state field holding one
    // of four codes, tested across up to 2^18 candidate qwords, and it has already false-positived
    // live once (0x3dfadb, 2026-09-06, see the note on `identifies_a_session`). Four small integers
    // at known offsets is a weak signature over that many candidates.
    //
    // A live `_Mtx_internal_imp_t` at `session+0x100` is a much stronger one, and it is free: the
    // action's own first act is to lock it, so an object that fails this check is an object the
    // action would have thrown on. Measured on run br-20260908-185557-aa9c, where five invasions
    // produced two rejections and both refused here: `_Type` was not a shape MSVC's mutex
    // constructors write, and `osm_tag_matches` reported the `seamless` tag absent on the same
    // owner. That is the pointer being wrong, not the lock being held.
    if let Some((module, offset)) = module_backing(session.session) {
        log_refusal_once(
            &OWNER_REFUSAL_SAID,
            format_args!(
                "local-invasion: refusing to drive ERSC's {what} -- the session resolved to \
                 {:#x}, which is {module}+{offset:#x}, inside a loaded module. A session is \
                 allocated; it is never static image data. Handing this to Seamless is what wedged \
                 the game on 2026-09-08: ersc+0x25871 locked a mutex that was really \
                 eldenring.exe+0x3c0cdc0, nothing unlocks a static global, and the main thread \
                 waited until the 30-second stall watchdog fired.",
                session.session
            ),
        );
        return None;
    }
    if let Some(refusal) = lock_shape_refusal(session) {
        // The trailing sentence is chosen per arm. It used to be one fixed paragraph asserting
        // that identification had failed, appended to all five refusals -- and three of them do
        // not mean that. `lock_shape_refusal` reports a busy lock as well as a wrong shape, and a
        // busy lock is a moment, not a verdict about the pointer. Measured 2026-09-09 on run
        // br-20260909-212201-a69e: the crate drove its own request, hit "another thread holds the
        // session mutex", and the fixed suffix declared the session misidentified -- so the next
        // hour went to the identification instead of to a retry that would have worked.
        let detail = if refusal.contains("holds the session mutex") {
            "That is the lock being BUSY, not the pointer being wrong: the mutex header read back \
             and carries a shape MSVC's constructors write. It is retried on a later tick."
                .to_owned()
        } else {
            format!(
                "That is the pointer being wrong, not the action: the header at \
                 session+{SESSION_MUTEX_OFFSET:#x} is not one a live session carries, so driving \
                 ersc.dll with it would call into an object that is not a session. See open issue \
                 er-effects-rs-9i0g."
            )
        };
        log_refusal_once(
            &OWNER_REFUSAL_SAID,
            format_args!("local-invasion: refusing to drive ERSC's {what} -- {refusal}. {detail}"),
        );
        return None;
    }
    if session.osm != 0 {
        // The captured owner has to be readable where ERSC reads it, and that is one place:
        // `[rcx + 0x58]`, the first instruction of both actions. An owner that fails this is not
        // an owner, and handing it over is a fault inside `ersc.dll` rather than a refusal here.
        //
        // Measured twice, both times the same instruction. Run `br-20260916-004838-87fe`:
        // `access-violation addr=0x18002585a rcx=0x736046b8 fault_addr=0x73604710`, which is
        // `rcx+0x58`. Run `br-20260916-010425-1eb0`, from the game task this time:
        // `rcx=0x75bf1d30[unreadable]`, backtrace `ersc.dll+0x25850 <- er_invasion_warp.dll`. The
        // checks above had passed it as "plausible, tag absent" on both occasions -- they judge
        // the session, and a stale captured OSM is a separate pointer that none of them read.
        // Readable is not the same as right, and both failures end in the same place.
        //
        // Readability was the only test here, and `report_lock_preconditions` was already printing
        // the stronger one beside it without anything acting on the answer: run
        // br-20260916-025336-64fc drove the invade with `owner+0x58=0x0 (disagrees, so the action
        // will lock a different object than the one read)` in its own line. Zero is readable. It
        // is also the null Seamless dereferences, and the call never came back.
        //
        // So the test is agreement with the session this tick resolved. A captured owner pointing
        // at a different session is pointing at the previous match's, which is freed.
        // SAFETY: fault-closed read of the one field the action dereferences.
        let carried =
            unsafe { er_game_base::mem::safe_read_usize(session.osm + OWNER_SESSION_OFFSET) };
        if carried != Some(session.session) {
            log_refusal_once(
                &OWNER_REFUSAL_SAID,
                format_args!(
                    "local-invasion: not driving ERSC's {what} through the captured owner {:#x} \
                     -- it carries {} at +{OWNER_SESSION_OFFSET:#x}, not the session this tick \
                     resolved ({:#x}), and that field is the only thing both actions dereference. \
                     Falling back to a synthesized owner, which holds the right session there by \
                     construction.",
                    session.osm,
                    match carried {
                        Some(value) => format!("{value:#x}"),
                        None => "nothing readable".to_owned(),
                    },
                    session.session
                ),
            );
            return Some(synthesized_owner(session.session));
        }
        return Some(session.osm);
    }
    // No OSM was found, and one is not needed. See `synthesized_owner`.
    Some(synthesized_owner(session.session))
}

/// A `this` of our own for ERSC's option actions, holding the session where they read it.
///
/// # Why this replaces the search that could not finish
///
/// Both driven actions were read end to end out of the installed `ersc.dll` (v2.0.1,
/// `scripts/disas-ersc.py --whole`), and they are 0x64 and 0x75 bytes:
///
/// ```text
/// cancel  ersc+0x258d0                     invade  ersc+0x25850
///   mov  rdi, [rcx + 0x58]                   mov  rdi, [rcx + 0x58]
///   lea  rsi, [rdi + 0x100]                  cmp  dword [rdi + 0x150], 1
///   ...  everything else via rdi             ...  everything else via rdi
/// ```
///
/// `rcx` is read exactly once, at `+0x58`, and never referenced again -- every later access is
/// `rdi`-relative, and `rdi` is the session. So the object ERSC calls `this` is, to these two
/// functions, nothing but a box with a session pointer at `+0x58`. Whose box it is has no bearing
/// on what they do.
///
/// That dissolves open issue er-effects-rs-9i0g rather than solving it. Finding Seamless's own
/// menu object was never the requirement; it was an assumption, and it cost four false-positive
/// identifications, a wedged main thread, two killed processes, a sweep of the whole address space
/// that turned up fifteen candidates, and a discriminator that then found none of the fifteen was
/// referenced by `ersc.dll` at all. None of that had to happen. The function was 0x64 bytes and
/// reading it settles the question in one look -- which is the argument for reading the binary
/// first, made at our own expense.
///
/// The shim is strictly safer than the object it replaces: it is ours, it lives for the process,
/// its lifetime cannot end under a call, and it cannot be a misidentification. The session pointer
/// written into it is still the one every check upstream has already agreed on -- this changes who
/// holds the session, never which session is held.
///
/// Allocated once and leaked on purpose: ERSC reads it during a call this thread is inside, so it
/// must outlive any borrow, and a process-lifetime allocation of 0x60 bytes is the cheapest way to
/// promise that.
fn synthesized_owner(session: usize) -> usize {
    /// Big enough to contain the one field ERSC reads, and aligned the way MSVC's `operator new`
    /// would align a real one.
    #[repr(C, align(16))]
    struct OwnerShim([u8; 0x60]);

    let shim = SYNTHESIZED_OWNER.load(Ordering::Acquire);
    let shim = if shim == 0 {
        let fresh = Box::into_raw(Box::new(OwnerShim([0u8; 0x60]))) as usize;
        match SYNTHESIZED_OWNER.compare_exchange(0, fresh, Ordering::AcqRel, Ordering::Acquire) {
            Ok(_) => fresh,
            Err(winner) => {
                // Another thread won. Reclaim ours rather than leaking two.
                drop(unsafe { Box::from_raw(fresh as *mut OwnerShim) });
                winner
            }
        }
    } else {
        shim
    };
    // Rewritten before every use rather than once: the session is a heap object whose address
    // changes between matches, and a stale pointer here would hand ERSC a freed session -- the
    // one way this shim could be more dangerous than the search it replaces.
    // SAFETY: `shim` is our own 0x60-byte allocation, live for the process, and
    // `ersc::NEXT_OBJECT_OFFSET + 8` is 0x60.
    unsafe {
        std::ptr::write_unaligned((shim + ersc::NEXT_OBJECT_OFFSET) as *mut usize, session);
    }
    if !SYNTHESIZED_OWNER_SAID.swap(true, Ordering::SeqCst) {
        crate::standalone_log(format_args!(
            "local-invasion: driving ERSC through a synthesized owner at {shim:#x} carrying the \
             session at +{:#x}. Both actions read `rcx` exactly once, at that offset, and work \
             through the session for everything after -- read end to end out of ersc+0x258d0 \
             (0x64 bytes) and ersc+0x25850 (0x75 bytes). Seamless's own menu object is not \
             required and is no longer looked for (er-effects-rs-9i0g).",
            ersc::NEXT_OBJECT_OFFSET
        ));
    }
    shim
}

/// The process-lifetime shim allocation, or 0 before it is made.
static SYNTHESIZED_OWNER: AtomicUsize = AtomicUsize::new(0);
/// Said once, not per rejected match.
static SYNTHESIZED_OWNER_SAID: AtomicBool = AtomicBool::new(false);

/// One line per run of ticks that declined an armed search for a non-idle session.
static REINVADE_NOT_IDLE_SAID: AtomicBool = AtomicBool::new(false);

/// Latch for the identification refusal above, so a wrong pointer says so once rather than per frame.
static OWNER_REFUSAL_SAID: Mutex<Option<String>> = Mutex::new(None);

/// Where the session's `std::mutex` starts, named here because the refusal message quotes it.
/// Derived from the guard offset rather than written twice: `guard == mutex + _Count`, and `_Count`
/// is at `+0x4c` of an `_Mtx_internal_imp_t`.
const SESSION_MUTEX_OFFSET: usize = 0x100;

/// One line per distinct refusal, not one per frame.
///
/// `drive_pending_reinvade` runs on the game task, so an unchanged refusal was being written every
/// frame -- 22735 identical lines in the run that first exercised this. Keyed on the reason, so a
/// session whose shape changes still gets a fresh line, and the counters below stay honest about
/// how often the refusal actually fired.
pub(super) fn log_refusal_once(latch: &Mutex<Option<String>>, message: std::fmt::Arguments<'_>) {
    let text = format!("{message}");
    let mut guard = match latch.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    if guard.as_deref() == Some(text.as_str()) {
        return;
    }
    crate::standalone_log(format_args!("{text}"));
    *guard = Some(text);
}

static INVADE_REFUSAL_SAID: Mutex<Option<String>> = Mutex::new(None);
static STALLED_REFUSAL_SAID: Mutex<Option<String>> = Mutex::new(None);

/// Arm a search without first insisting the session is resolvable right now.
///
/// [`request_invade`] refuses when `resolve_session` cannot identify Seamless's session at the
/// instant it is called, and drops the request on the floor. That is wrong for a request made from
/// a menu detour: the player pressed a button, and whether the resolver happens to recognise the
/// session on that one frame is not something they chose. Measured on run
/// `br-20260916-005630-402a`: the vanilla finger's popup was answered, `request_invade` came back
/// `SessionNotIdentified`, and the search never started -- in a run whose own log had resolved the
/// session minutes earlier and printed its address.
///
/// Arming instead of resolving costs nothing, because the resolving already happens somewhere
/// better. The game task re-resolves the session every tick before it calls
/// [`drive_pending_reinvade`], and that function re-checks idleness and re-validates the owner
/// before it drives anything. So a request that arrives during a transient simply waits for the
/// next tick that can act on it, which is what the player meant by pressing the row.
///
/// Both flags are still needed: `PENDING_REINVADE` is what the drain consumes and
/// `AUTO_SEARCH_ARMED` is what it checks first, so setting one arms a request refused on the same
/// tick it is made.
#[cfg(windows)]
pub fn arm_invade_request(why: &str) -> bool {
    // A new search starts at the player's own tile. Without this the ring resumes wherever the
    // last one was abandoned, so using the item again announces a search starting and then asks
    // about somewhere twenty tiles away.
    crate::lobby_publish::restart_search_ladder();
    // Take the first rung here, so the opening banner names where the search is actually looking.
    // Nothing else can: the only other caller is the Steam filter detour, and it does not run
    // while Seamless searches -- every run so far reports `filter[ours 0/0]`. That is why the
    // player saw one notice per use of the item and nothing afterwards.
    crate::lobby_publish::advance_search_place();
    AUTO_SEARCH_ARMED.store(true, Ordering::SeqCst);
    PENDING_REINVADE.store(true, Ordering::SeqCst);
    crate::standalone_log(format_args!(
        "local-invasion: a search was armed for {why} -- it runs on the next game tick that can \
         resolve the session and finds it idle, rather than being refused for the state of this \
         one frame."
    ));
    true
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn arm_invade_request(_why: &str) -> bool {
    false
}

/// Arm a search for the next game tick, from outside this DLL.
///
/// Backs `er_invasion_warp_request_invade`; see that export for why the caller may not simply call
/// the invade action itself. Both flags are needed: `PENDING_REINVADE` is what
/// `drive_pending_reinvade` drains, and `AUTO_SEARCH_ARMED` is what it checks first -- setting
/// only one arms a request that is refused on the same tick it is made.
#[cfg(windows)]
pub fn request_invade() -> bool {
    // The precondition is a session, not the hooked menu object. This used to demand `OSM != 0`,
    // which is only ever set by a detour on `ersc.dll` -- and every such detour is disabled here
    // because installing one faults the game at `eldenring.exe+0x10043`. So the gate could never
    // open under the shipping configuration, and the export reported "nothing to drive" while the
    // drive path itself was perfectly able to run: `ersc_owner_or_refuse` synthesizes a `this` of
    // its own when no menu object was captured (see `synthesized_owner` -- both actions read `rcx`
    // exactly once, at `+0x58`, so a box of ours serves), and needs only a session to put in it.
    //
    // Asking `resolve_session` is therefore both correct and stricter in the way that matters: it
    // re-validates the pointer on every call rather than trusting a cached one.
    // The session is reported, not required. Demanding one here threw away real requests for the
    // state of a single frame: `resolve_session` answers `SessionNotIdentified` after a map
    // change until the scan finds the new one, and a request made in that window was refused
    // outright rather than waiting a tick. The finger popup hit exactly this and had to stop
    // using this function; the export deserves the same treatment rather than a second rule.
    //
    // `drive_pending_reinvade` re-resolves on every tick and declines until it can, so an
    // unresolvable session costs a delay, never the request.
    if let Err(cause) = resolve_session() {
        crate::standalone_log(format_args!(
            "local-invasion: a search was requested from outside this DLL while no session was \
             resolvable ({cause:?}) -- arming it anyway, for the first tick that can resolve one."
        ));
    }
    arm_invade_request("a request from outside this DLL")
}

/// Start the search now, on the calling thread, instead of arming it for the game tick.
///
/// # Why the inline form has to exist
///
/// [`request_invade`] hands the call to `drive_pending_reinvade` on the game task tick, and that
/// hand-off is a deadlock when the caller is a detour that Seamless is already inside. Measured on
/// run `br-20260909-234803-535c`: the player used the item a second time, so the game's own goods
/// path was in `ersc.dll` holding the session mutex, our tick called the invade action for the
/// armed request on another thread, and the log stops dead after `about to drive ERSC invade` with
/// the process alive at 122 threads and `cpu_ticks_in_3000ms=9`.
///
/// The Frida prototype never hit that because it called the action from the replacement itself --
/// the thread already inside Seamless, so there was only ever one entrant. This is that shape.
///
/// Only call it from a frame Seamless drove into. From a game-task tick, use [`request_invade`].
#[cfg(windows)]
pub fn drive_invade_inline(why: &str) -> bool {
    let Ok(session) = resolve_session() else {
        return false;
    };
    if read_session_state(session.abi, session.session) != Some(session.abi.state_idle) {
        return false;
    }
    if session_guard_refuses(session.abi, session.session).is_some() {
        return false;
    }
    let Some(invade) = ersc_action(
        session.abi,
        session.abi.invade_action_rva,
        session.abi.invade_prologue,
    ) else {
        return false;
    };
    let Some(owner) = ersc_owner_or_refuse(&session, why) else {
        return false;
    };
    if let Some(refusal) = lock_shape_refusal(&session) {
        crate::standalone_log(format_args!(
            "local-invasion: declining to start the search inline ({why}) -- {refusal}"
        ));
        return false;
    }
    let _call = OurCall::enter();
    // SAFETY: byte-verified action address for the recognised build, called with the owner
    // `ersc_owner_or_refuse` validated, on the thread Seamless is already on.
    unsafe { invade(owner, 0, 1, 1) };
    drop(_call);
    // Same reason as `drive_invade_with_owner`: a search this DLL drove is one it watched
    // begin, so it owns the loop rather than hoping the tracker samples the edge.
    AUTO_SEARCH_ARMED.store(true, Ordering::SeqCst);
    note_state_after_our_action(session, why);
    crate::standalone_log(format_args!(
        "local-invasion: started the search inline ({why}) on the calling thread -- one entrant,          so it cannot contend with the game's own goods path the way an armed request did"
    ));
    true
}

/// [`drive_invade_inline`], but driving Seamless through an owner the caller has -- the real
/// option-menu object -- instead of one `ersc_owner_or_refuse` synthesizes.
///
/// # Why the synthesized owner is not good enough here
///
/// It was measured wedging the game. Run `br-20260910-000334-453e`, from the log in order:
/// `REFUSED an option-menu object handed in at 0x1` (the register capture came back as `1`, so
/// nothing was adopted), then `driving ERSC through a synthesized owner at 0x54fc3a70`, then the
/// process sitting at 122 threads and `cpu_ticks_in_3000ms=8` -- blocked on a lock.
///
/// The Frida prototype passed the object `show` was called with and never wedged. A synthesized
/// box satisfies `rcx+0x58` and nothing else, which is enough for the reads the action makes on
/// the way in and not enough for whatever it does after.
///
/// The owner is still validated, not trusted: `+0x58` must lead to an object whose state reads a
/// code this build defines, the session must be idle, and the lock shape must be sane. A caller
/// handing in a wrong pointer gets `false`, not a wedge.
#[cfg(windows)]
pub fn drive_invade_with_owner(owner: usize, why: &str) -> bool {
    if owner == 0 {
        return false;
    }
    let Some(abi) = resolve_ersc_abi() else {
        return false;
    };
    // The one hop the actions make. A pointer that does not lead to a session here is not the
    // menu object, whatever else it may be.
    let Some(session) =
        (unsafe { er_game_base::mem::safe_read_usize(owner + ersc::NEXT_OBJECT_OFFSET) })
            .filter(|session| *session != 0)
    else {
        return false;
    };
    if read_session_state(abi, session) != Some(abi.state_idle) {
        return false;
    }
    if session_guard_refuses(abi, session).is_some() {
        return false;
    }
    let resolved = SeamlessSession {
        osm: owner,
        session,
        abi,
    };
    if let Some(refusal) = lock_shape_refusal(&resolved) {
        crate::standalone_log(format_args!(
            "local-invasion: declining to drive ERSC through the handed-in owner 0x{owner:x} \
             ({why}) -- {refusal}"
        ));
        return false;
    }
    let Some(invade) = ersc_action(abi, abi.invade_action_rva, abi.invade_prologue) else {
        return false;
    };
    let _call = OurCall::enter();
    // SAFETY: byte-verified action for the recognised build, called with the real menu object the
    // caller observed, on the thread Seamless is already on -- the Frida prototype's exact shape.
    unsafe { invade(owner, 0, 1, 1) };
    drop(_call);
    note_state_after_our_action(resolved, why);
    crate::standalone_log(format_args!(
        "local-invasion: drove ERSC invade through the REAL menu object 0x{owner:x} ({why}), \
         session 0x{session:x} -- no synthesized owner, one entrant, same as the Frida prototype"
    ));
    true
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn drive_invade_with_owner(_owner: usize, _why: &str) -> bool {
    false
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn drive_invade_inline(_why: &str) -> bool {
    false
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn request_invade() -> bool {
    false
}

/// Can the invade action be called at all right now, and say so exactly once per change.
///
/// Wraps [`ersc_action`] so the recovery loop can ask the question without the answer costing a
/// log line per tick. `ersc_action` already reports an unrecognised prologue, but it reports it on
/// every call, which is the other half of what made the spin unreadable: 6061 identical refusal
/// lines interleaved with 6056 identical restart lines.
///
/// Reports the transition in both directions. A latch that only ever says "broken" leaves a
/// recovered session looking dead, which is the same false negative one level up.
/// # The call succeeding is not the search starting
///
/// Driving `ersc+0x25850` from the game task moves the session to `0x0e SEARCHING` and produces
/// no Steam traffic at all. Measured with every other precondition satisfied on run
/// br-20260916-145227-68b5: the session resolved, this check reported `reads as itself again`,
/// the action ran, the state moved, and all 38 matchmaking slots stayed at zero.
///
/// The one run that did query -- br-20260916-100817-3fe0 -- called the same function with the
/// same owner from a different place: the popup-skip path, inside Seamless's own dialog, logging
/// `started the search inline`. So the difference between a search and a state write is the
/// calling context, not the arguments.
///
/// Two contexts have been tried and neither queries from here: the game task (this one), and a
/// Frida call from `CS::FeSystemAnnounceView::Update`
/// (bd seamless-state-0x0e-alone-does-not-make-it-query-steam). The bounds popup's own thread
/// hard-locked the game twice and is not a third option.
fn invade_action_callable(abi: &ersc::Abi) -> bool {
    let callable = {
        // A thread inside an ersc callback is a temporary refusal, not a broken build --
        // `ersc_action` declines there too, and latching on it would stand the hunt down for the
        // rest of the session over one badly-timed tick.
        if inside_ersc_callback() {
            return !INVADE_ACTION_UNCALLABLE.load(Ordering::SeqCst);
        }
        ersc_action(abi, abi.invade_action_rva, abi.invade_prologue).is_some()
    };
    if callable {
        if INVADE_ACTION_UNCALLABLE.swap(false, Ordering::SeqCst) {
            crate::standalone_log(format_args!(
                "local-invasion: the invade action reads as itself again -- the hunt can restart \
                 searches from here"
            ));
        }
        return true;
    }
    if !INVADE_ACTION_UNCALLABLE.swap(true, Ordering::SeqCst) {
        crate::standalone_log(format_args!(
            "local-invasion: not restarting the search -- ersc+{:#x} does not hold the bytes this module measured, so the invade action cannot be called. Expected {:02x?}, read {:02x?}. Cancelling still works, it reads a different entry.",
            abi.invade_action_rva,
            abi.invade_prologue,
            super::ersc_module_base()
                .map(|base| {
                    // Byte at a time through the fault-closed reader, because there is no
                    // slice form of it and a refusal that cannot show what it read is the
                    // reason this line existed for a day without being diagnosable.
                    (0..abi.invade_prologue.len())
                        .map(|i| unsafe {
                            er_game_base::mem::safe_read_u8(base + abi.invade_action_rva + i)
                                .unwrap_or(0)
                        })
                        .collect::<Vec<u8>>()
                })
                .unwrap_or_default(),
        ));
    }
    false
}

/// Fire the queued re-invade once the session is genuinely idle.
///
/// Disarms before calling, so a session that fails to leave idle costs one extra invade at most
/// rather than one per frame.
pub(super) fn drive_pending_reinvade(session: SeamlessSession) {
    let pending = PENDING_REINVADE.load(Ordering::SeqCst);
    let armed = AUTO_SEARCH_ARMED.load(Ordering::SeqCst);
    if !pending || !armed {
        // Said once per arming, and only when one flag stands without the other -- the shape that
        // means something took the request away between arming and this tick. Both clear is the
        // ordinary resting state and says nothing. Run br-20260916-024013-f7ff armed a search on a
        // live session, took the ladder's first rung, and then produced no drive line and no
        // refusal line at all, which left this return as the only remaining explanation and no way
        // to tell which half of it fired.
        // Only while the session is idle. `pending=false armed=true` is also the ordinary shape of
        // a search that is in flight -- the drive consumed the pending flag and the arming stands
        // until the attempt ends -- so at any other state this line calls a healthy search
        // permanently broken. Run br-20260917-004351-2fe4 printed it at state 0x0e, nine lines
        // before the same search was released normally.
        let idle = read_session_state(session.abi, session.session) == Some(session.abi.state_idle);
        if idle && (pending != armed) && !MISMATCHED_ARM_SAID.swap(true, Ordering::SeqCst) {
            crate::standalone_log(format_args!(
                "local-invasion: an armed search is not being driven and never will be --                  pending={pending} armed={armed}. Something cleared one flag without the other                  after the request was made; opening Seamless's own menu is the usual cause."
            ));
        }
        return;
    }
    MISMATCHED_ARM_SAID.store(false, Ordering::SeqCst);
    // `invade` returns immediately unless the session is idle, so this is the same precondition
    // ERSC itself enforces -- checked here so a no-op call is not counted as a restart.
    let state = read_session_state(session.abi, session.session);
    if state != Some(session.abi.state_idle) {
        // Said once, because the silent version of this return swallowed a whole self-driven
        // attempt: run br-20260909-212533-0b91 armed a search through
        // `er_invasion_warp_request_invade`, logged `armed for the next game tick`, and then
        // printed nothing at all -- the request stayed pending and this line declined it on every
        // tick without a word. A request that is accepted and then never acted on has to say why.
        if !REINVADE_NOT_IDLE_SAID.swap(true, Ordering::SeqCst) {
            crate::standalone_log(format_args!(
                "local-invasion: an armed search is not being driven -- the resolved session reads                  {} and the invade action only runs from {:#04x} idle. The request stays pending                  and is retried; if this is the last word in the log, the resolved pointer is not                  a session that ever goes idle.",
                match state {
                    Some(value) => format!("{value:#04x}"),
                    None => "unreadable".to_owned(),
                },
                session.abi.state_idle
            ));
        }
        return;
    }
    REINVADE_NOT_IDLE_SAID.store(false, Ordering::SeqCst);
    // Not while the player is still using something.
    //
    // Both hard locks and both hangs arrived one tick after a finger press, and the one call that
    // returned -- br-20260916-030928-9e37, driven from Frida -- had no item use in flight. The
    // game's own goods path runs inside `ersc.dll` and takes the session mutex while a use is
    // being processed, so a drive that lands in that window is a second acquire of a lock the
    // game is holding, and `lock_shape_refusal`'s single reading is a sample taken before the
    // race rather than a fact about it.
    //
    // `CSMenuGaitemUseState+0xc` is the game's own record of the use in flight, which is the
    // native owner of this question. `None` means nothing is being used and the drive may go.
    #[cfg(windows)]
    // `-1` is the field's empty value, not an item.
    if let Some(item) = unsafe { crate::lynchpin_use::item_in_use() }
        && item != NO_ITEM_IN_USE
    {
        if !USE_IN_FLIGHT_SAID.swap(true, Ordering::SeqCst) {
            crate::standalone_log(format_args!(
                "local-invasion: holding the armed search while item {item:#x} is still being \
                 used -- the game's own goods path is inside ersc.dll with the session mutex, and \
                 driving into that window is what blocked every previous attempt. The request \
                 stays armed and goes on the first tick after the use completes."
            ));
        }
        return;
    }
    #[cfg(windows)]
    USE_IN_FLIGHT_SAID.store(false, Ordering::SeqCst);
    // Both bails below used to return without a word, and the silence cost a whole run. Measured
    // br-20260916-020020-8468: 1,836 `the attempt ended without us cancelling it` lines, zero
    // `about to drive ERSC invade`, zero session-state transitions, and -- confirmed through a
    // live Frida hook on the interface vtable itself -- zero calls to
    // `ISteamMatchmaking::RequestLobbyList` or `AddRequestLobbyListStringFilter`. Every one of
    // those attempts declined here and re-armed on the next tick, so the loop counted 1,836
    // attempts that never happened and the prefilter ladder climbed over all of them.
    if let Some(refusal) = session_guard_refuses(session.abi, session.session) {
        PENDING_REINVADE.store(false, Ordering::SeqCst);
        log_refusal_once(
            &INVADE_GUARD_REFUSAL_SAID,
            format_args!(
                "local-invasion: an armed search was dropped before it could be driven --                  {refusal:?}. No attempt was made, so nothing the loop counts after this is an                  attempt either."
            ),
        );
        say_the_search_was_dropped();
        return;
    }
    let Some(invade) = ersc_action(
        session.abi,
        session.abi.invade_action_rva,
        session.abi.invade_prologue,
    ) else {
        PENDING_REINVADE.store(false, Ordering::SeqCst);
        log_refusal_once(
            &INVADE_ACTION_REFUSAL_SAID,
            format_args!(
                "local-invasion: an armed search was dropped because ersc+{:#x} would not hand                  out its invade action. No attempt was made.",
                session.abi.invade_action_rva
            ),
        );
        say_the_search_was_dropped();
        return;
    };
    PENDING_REINVADE.store(false, Ordering::SeqCst);
    let Some(owner) = ersc_owner_or_refuse(&session, "invade") else {
        return;
    };
    report_lock_preconditions(&session, owner, "invade");
    if let Some(refusal) = lock_shape_refusal(&session) {
        log_refusal_once(
            &INVADE_REFUSAL_SAID,
            format_args!(
                "local-invasion: not restarting the search -- {refusal}. The reading this refused \
                 on is in the line immediately above."
            ),
        );
        say_the_search_was_dropped();
        return;
    }
    // Off the game's thread, because this call is allowed to block and the game is not.
    //
    // It blocked on 2026-09-16, run br-20260916-014344-dc40: `about to drive ERSC invade ...
    // state=0x1 idle`, every precondition clear, the session mutex reading free and unowned at the
    // instant of the check -- and no successor line ever. The player's game hard locked on a
    // Nearby Only press. `lynchpin_use` had already measured the same signature from the same
    // cause and written it down: arming moves the call to the game task tick, and the game's own
    // goods path can be inside `ersc.dll` holding the session mutex at that moment. A free mutex
    // one instruction earlier is not a promise that the call returns.
    //
    // The Lynchpin answers this by calling inline on the thread Seamless already drove into. The
    // vanilla finger popup has no such thread -- it is the game's own menu, with no `ersc.dll`
    // frame beneath it -- so the answer here is a thread of our own. If ERSC blocks, this worker
    // blocks, waits for the mutex the goods path is holding, and proceeds when it frees. The game
    // keeps rendering either way, which is the property that was missing.
    //
    // One at a time: the restart loop runs an attempt roughly every fifteen seconds and a blocked
    // worker would otherwise accumulate one thread per round.
    if INVADE_IN_FLIGHT.swap(true, Ordering::SeqCst) {
        crate::standalone_log(format_args!(
            "local-invasion: not restarting the search -- the previous invade call has not \
             returned from ersc.dll yet. It is waiting on a lock rather than failing, and \
             starting a second would queue behind it."
        ));
        return;
    }
    let invade_address = invade as usize;
    let osm = session.osm;
    let session_address = session.session;
    let abi = session.abi;
    let spawned = std::thread::Builder::new()
        .name("er-invasion-warp invade".to_owned())
        .spawn(move || {
            let session = SeamlessSession {
                osm,
                session: session_address,
                abi,
            };
            {
                let _call = OurCall::enter();
                // Set before the call, not after: the call is allowed to block, and an attempt
                // that is in flight is still an attempt.
                ATTEMPT_DRIVEN.store(true, Ordering::SeqCst);
                // SAFETY: `ersc_action` verified this address against the prologue for the
                // supported build, and `owner` was read back before the caller released the game
                // thread. The arguments are the ones `ersc+0x25850` reads.
                unsafe {
                    core::mem::transmute::<usize, ErscActionFn>(invade_address)(owner, 0, 1, 1)
                };
            }
            INVADE_IN_FLIGHT.store(false, Ordering::SeqCst);
            // Claim the searching state we just caused, before the tracer can read it as the user
            // pressing the option and arm a loop that is already armed.
            note_state_after_our_action(session, "restart search");
            let count = REINVADES.fetch_add(1, Ordering::SeqCst) + 1;
            crate::standalone_log(format_args!(
                "local-invasion: search restarted automatically (#{count}) -- press Cancel search \
                 yourself to stop"
            ));
        });
    if spawned.is_err() {
        INVADE_IN_FLIGHT.store(false, Ordering::SeqCst);
        crate::standalone_log(format_args!(
            "local-invasion: could not start the thread that drives ERSC's invade, so this \
             restart is skipped. The hunt stays armed and the next tick tries again."
        ));
    }
}

/// Re-arm the search when an attempt died without us cancelling it.
///
/// # The gap this closes
///
/// [`drive_pending_reinvade`] only ever fired for a match we rejected, because `PENDING_REINVADE`
/// is set in [`cancel_match`] and nowhere else. Every other way an attempt can end -- a host that
/// vanished, a connection that never completed, a refusal from the far side -- left the session
/// sitting at idle with the loop still armed and nothing to restart it, so the player had to reach
/// for the finger again. Measured 2026-08-06: of ten `0x15 -> 0x22` unwinds in one session, only
/// four were ours; the other six ended the hunt silently.
///
/// The standing instruction is that the loop runs until the player uses the lynchpin again, so
/// "the session went idle on its own while we are still hunting" is a restart, not a stop.
///
/// # Why this cannot resume a search after a successful invasion
///
/// A successful join looks identical in session state -- `KEEP` was followed by the same
/// `0x15 -> 0x22 -> 0x23 -> 0x00` unwind a rejection produces, so idle alone cannot tell them
/// apart. It does not have to: [`Verdict::Keep`] clears `AUTO_SEARCH_ARMED`, so a kept match
/// leaves the loop disarmed and this function returns immediately. The same is true of the
/// player's own cancel and of opening Seamless's menu, both of which disarm.
pub(super) fn arm_self_recovery(session: SeamlessSession) {
    if !AUTO_SEARCH_ARMED.load(Ordering::SeqCst) || PENDING_REINVADE.load(Ordering::SeqCst) {
        return;
    }
    if read_session_state(session.abi, session.session) != Some(session.abi.state_idle) {
        return;
    }
    // An invasion that happened is not an attempt that died.
    //
    // The doc above assumed `Verdict::Keep` would have disarmed the loop first. Measured
    // 2026-08-17, it does not: that session logged zero keeps and eleven rejects (`mode=area` with
    // no named locations rejects everything it judges), so the loop stayed armed through three real
    // invasions -- and this function restarted the hunt while the player was still on the loading
    // screen back to their own world. They arrived home coloured as an invader with a Seamless name
    // popup reading `[Unknown]`, because a fresh invasion was already in flight.
    //
    // So the disarm is taken from what the engine did rather than from what our filter decided.
    if INVASION_ACTUALLY_HAPPENED.swap(false, Ordering::SeqCst) {
        // Disarm as a kept match would have: the hunt is over until the player asks for another.
        AUTO_SEARCH_ARMED.store(false, Ordering::SeqCst);
        if let Ok(mut backoff) = RESTART_BACKOFF.lock() {
            backoff.stand_down();
        }
        crate::standalone_log(format_args!(
            "local-invasion: that attempt became a real invasion (the session reached \
             LobbyState::Client) -- NOT restarting the hunt. Use the lynchpin again when you want \
             another one"
        ));
        return;
    }
    // Is there an action to restart with at all? Asked here, where the decision to re-arm is made,
    // rather than in `drive_pending_reinvade`, where it arrives too late to prevent the spin: an
    // uncallable action there clears the flag silently, and this function re-arms on the next tick
    // having learned nothing. Probing costs one prologue comparison on a path that already reads
    // session state every tick.
    if !invade_action_callable(session.abi) {
        return;
    }
    // How badly did the last attempt go? Restarting instantly is right when Seamless actually
    // searched -- its own ~15s retry paces the loop and nothing here is felt. It is wrong when
    // Seamless refused instantly: measured 2026-08-06, eleven restarts in 38.9s during an area
    // transition, four times the normal query rate, because idle alone cannot tell a 15-second
    // search from a 33-millisecond refusal.
    {
        let now = now_ms();
        let Ok(mut backoff) = RESTART_BACKOFF.lock() else {
            return;
        };
        let delay = backoff.attempt_ended(now);
        if !backoff.may_restart(now) {
            // Held. Return without arming. The next tick re-enters, finds no recorded start (the
            // attempt was already consumed), scores that as a normal attempt costing nothing, and
            // simply re-checks the hold -- so the delay elapses without accumulating further
            // penalty, and the restart fires on the first tick after it expires.
            if delay > 0 {
                crate::standalone_log(format_args!(
                    "local-invasion: that attempt was refused in under a second (#{} in a row) -- \
                     waiting {delay}ms before searching again, so a passing refusal does not turn \
                     into a query storm. The hunt is still on.",
                    backoff.consecutive()
                ));
            }
            return;
        }
    }
    // Was there an attempt at all? A decline is not an attempt that ended, and counting it as one
    // is what produced 1,836 phantom attempts in one run while nothing reached Steam. The retry
    // still happens -- `PENDING_REINVADE` is set either way -- but nothing is counted, the ladder
    // does not move, and the player is not told a place was tried when none was.
    if !ATTEMPT_DRIVEN.swap(false, Ordering::SeqCst) {
        PENDING_REINVADE.store(true, Ordering::SeqCst);
        return;
    }
    PENDING_REINVADE.store(true, Ordering::SeqCst);
    // The attempt at this place found nobody, so the next one asks somewhere else -- and says so.
    // This is the only thing that moves the ladder now. It used to move inside the Steam
    // query detour, which does not fire while Seamless is searching, so the ring stood still and
    // the player got one notice per use of the item and silence through every restart after it.
    #[cfg(windows)]
    crate::lobby_publish::advance_search_place();
    let count = SELF_RECOVERIES.fetch_add(1, Ordering::SeqCst) + 1;
    crate::standalone_log(format_args!(
        "local-invasion: the attempt ended without us cancelling it (#{count}) -- restarting the \
         search, because you have not stopped hunting. Press Cancel search yourself to stop"
    ));
}

/// Cancel an attempt that has stopped progressing, so the loop can recover from a Seamless stall.
///
/// Seamless does not auto-cancel its own connection in these edge cases, which is why a hung
/// handshake otherwise sits forever. The action driven here is the same "Cancel search" the player
/// could press, and the restart afterwards is the ordinary one -- nothing here ends the hunt.
fn cancel_stalled_attempt(session: SeamlessSession, state: u32, held_ms: u64) {
    cancel_stalled_attempt_inner(session, state, held_ms, false);
}

/// As above, with the option to drive the action in a state where Seamless hides its own Cancel
/// row.
///
/// `engine_has_no_session` is the only thing that unlocks that, and it is not a preference. The
/// row predicate exists so this mod does not drive an action outside the preconditions its author
/// arranged for a live session -- but when `CSSessionManagerImp` reads `lobbyState == None` with
/// no outstanding RPC and both phase timers clear, `DisconnectCleanup` has already run and there
/// is no live session left to protect. What remains is Seamless holding a match against a session
/// the engine destroyed, which is the state the player cannot leave without spending the item.
///
/// Measured on run br-20260910-022111-750c: five attempts to clear it, every one refused with
/// `the session is in state 0x16, and ERSC's own hide-predicate draws its Cancel row only for
/// [0xe, 0xf, 0x10, 0x12]`, while the player stayed stuck. The guard was right about the row and
/// wrong about the situation.
///
/// The guard-poison and mutex-shape refusals above are not relaxed by this: those are about
/// whether the call is safe to make, not about whether the row is on screen.
/// Host-side stub: there is no ERSC action to drive off the game.
#[cfg(not(windows))]
pub(super) fn cancel_stalled_attempt_inner(
    _session: SeamlessSession,
    _state: u32,
    _held_ms: u64,
    _engine_has_no_session: bool,
) {
}

#[cfg(windows)]
pub(super) fn cancel_stalled_attempt_inner(
    session: SeamlessSession,
    state: u32,
    held_ms: u64,
    engine_has_no_session: bool,
) {
    if session_guard_refuses(session.abi, session.session).is_some() {
        return;
    }
    let Some(cancel) = ersc_action(
        session.abi,
        session.abi.cancel_action_rva,
        session.abi.cancel_prologue,
    ) else {
        return;
    };
    let Some(owner) = ersc_owner_or_refuse(&session, "cancel") else {
        return;
    };
    report_lock_preconditions(&session, owner, "cancel (stalled attempt)");
    if let Some(refusal) = lock_shape_refusal(&session) {
        log_refusal_once(
            &STALLED_REFUSAL_SAID,
            format_args!(
                "local-invasion: not cancelling the stalled attempt -- {refusal}. The reading this \
                 refused on is in the line immediately above."
            ),
        );
        return;
    }
    // The Cancel row is not the only way out, and where it is withdrawn the right answer is the
    // other row rather than overriding the refusal.
    //
    // `OPTIONSELECT_LEAVEWORLD` (`ersc+0x259d0`, menu 3's only row) is hidden by its predicate
    // `ersc+0x26ac0` only at idle, so Seamless draws it in every state where the Cancel row's
    // `ersc+0x26b40` has withdrawn -- `0x16` included, which is the state a stranded player is
    // actually in. Both actions do the same thing: take the session mutex, write `0x23` to
    // `session+0x150`, unlock. So this drives a row the player could have clicked, which is the
    // invariant the refusal exists to protect, instead of driving one they could not.
    let mut action = cancel;
    let mut what = "cancel";
    if let Some(refusal) = cancel_row_refusal(&session) {
        let leave_world = ersc_action(
            session.abi,
            session.abi.leave_world_action_rva,
            session.abi.leave_world_prologue,
        );
        match leave_world {
            // `Leave world` is itself withdrawn at idle, and there is nothing to leave from there.
            Some(leave) if state != session.abi.state_idle => {
                action = leave;
                what = "leave world";
                crate::standalone_log(format_args!(
                    "local-invasion: {refusal} -- driving OPTIONSELECT_LEAVEWORLD instead, which \
                     ERSC does draw in this state and which performs the same 0x23 transition."
                ));
            }
            _ => {
                if !engine_has_no_session {
                    log_refusal_once(
                        &STALLED_REFUSAL_SAID,
                        format_args!(
                            "local-invasion: not cancelling the stalled attempt -- {refusal}"
                        ),
                    );
                    return;
                }
                crate::standalone_log(format_args!(
                    "local-invasion: driving the cancel anyway -- {refusal}, and \
                     OPTIONSELECT_LEAVEWORLD did not resolve either. The engine has already torn \
                     its session down, so there is no live session for that predicate to protect \
                     and leaving it alone strands the player."
                ));
            }
        }
    }
    let _call = OurCall::enter();
    unsafe { action(owner, 0, 1, 1) };
    drop(_call);
    note_state_after_our_action(session, what);
    if AUTO_SEARCH_ARMED.load(Ordering::SeqCst) {
        PENDING_REINVADE.store(true, Ordering::SeqCst);
    }
    let count = STALL_RECOVERIES.fetch_add(1, Ordering::SeqCst) + 1;
    crate::standalone_log(format_args!(
        "local-invasion: connection stalled at state {state:#04x} for {held_ms}ms (#{count}) -- \
         cancelled it. Seamless does not auto-cancel these, and a healthy handshake takes under \
         two seconds"
    ));
}

/// Feed the session state to the stall detector and act on what it says.
///
/// Deliberately state-driven rather than time-capped: `SEARCHING` means "nobody has matched yet"
/// and is unbounded by nature, so it is never timed. Only the brief handshake steps are.
pub(super) fn watch_for_stall(session: SeamlessSession) {
    // Only recover while actually hunting. If the loop is not armed there is nothing to recover,
    // and running anyway is how this cancelled a successful invasion five seconds after accepting
    // it (2026-08-06): `Verdict::Keep` fired, the session sat in 0x15 loading the host's world,
    // and the watchdog called that a stalled handshake. From the player's seat the invasion
    // appeared and dismissed itself at once.
    //
    // Note this cannot be fixed by choosing better states to time: a successful join walks 0x22
    // and 0x23 exactly like a cancel does. Whether we are still hunting is the only thing that
    // separates "this handshake is stuck" from "this invasion is under way", and `Verdict::Keep`
    // already clears the armed flag, as do the player's own cancel and opening Seamless's menu.
    if !AUTO_SEARCH_ARMED.load(Ordering::SeqCst) {
        if let Ok(mut guard) = STALL_WATCHDOG.lock() {
            guard.stand_down();
        }
        // The backoff stands down here too, on the same condition and in the same place, rather
        // than at each of the sites that disarm. There are three of those today -- a kept match,
        // the player's own cancel, opening Seamless's menu -- and a fourth added later would
        // silently miss a per-site call. This branch already runs every tick the loop is not
        // armed, so it cannot be forgotten. Without it, a hunt stopped mid-backoff would hand its
        // penalty to the next one the player starts.
        if let Ok(mut backoff) = RESTART_BACKOFF.lock() {
            backoff.stand_down();
        }
        return;
    }
    let Some(state) = read_session_state(session.abi, session.session) else {
        return;
    };
    let now_ms = now_ms();
    let action = {
        let mut guard = match STALL_WATCHDOG.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        guard.observe(state, now_ms)
    };
    if action == Some(crate::stall_watchdog::StallAction::CancelAndResearch) {
        cancel_stalled_attempt(session, state, crate::stall_watchdog::STALL_THRESHOLD_MS);
    }
}

/// Which phase of an attempt a raw session state is, for the connect deadline.
///
/// # This is an allowlist, and it is an allowlist because a blocklist shipped and broke a live run
///
/// The first version asked "is this state neither idle, searching, nor cancelling?" and called
/// everything else a connect. That makes every state nobody has measured timeable by default, which
/// is exactly backwards. Measured on run br-20260915-025202-c779, the first live run it shipped in:
///
/// ```text
///   0x14 -> 0x16  held 124ms          <- the player is now in the host's world
///   the connection at state 0x0016 has not landed in 1500ms -- calling it lost
///   ERSC would draw its own Cancel row: no  -- driving OPTIONSELECT_LEAVEWORLD instead
/// ```
///
/// `0x16` is a successful invasion, and the aggregation the deadline itself came from says so in
/// as many words: 313 attempts reached it and dwelt there between 771ms and 465 seconds, because
/// that dwell is the invasion. Timing it tore the player out of a live invasion and hard-locked
/// the game. The same mistake, on the same state, that `stall_watchdog` records twice.
///
/// So a state is a connect only by being on the list below. Anything absent -- unknown, renumbered
/// by a Seamless update, or simply never seen -- is [`Phase::Idle`] and carries no deadline.
///
/// # The list is ERSC's own, not one of ours
///
/// The timed set is `lock_report::cancel_row_offered` minus searching: `0x0f`, `0x10`, `0x12`. That
/// is the predicate Seamless uses to decide whether to draw its Cancel row, so every state this
/// times is one the player could have cancelled by hand, and the action taken on a verdict can only
/// ever be the button they were already being offered. Deriving the set from the predicate rather
/// than listing states means the two cannot drift apart.
///
/// Searching is excluded for the reason recorded on [`Phase::Searching`]: it is unbounded by
/// nature, one measured search sat 280 seconds, and timing it cancels healthy hunts.
pub(super) fn connect_phase(
    abi: &ersc::Abi,
    state: u32,
) -> er_invasion_warp_core::attempt_verdict::Phase {
    use er_invasion_warp_core::attempt_verdict::Phase;
    // Checked first, and before the state is consulted at all. A successful invasion unwinds
    // through the same cancelling states a dead one does, so the state alone cannot tell them
    // apart -- this latch can, and it is the same signal `watch_for_stall` was given after the
    // watchdog cancelled an invasion five seconds after accepting it.
    if INVASION_ACTUALLY_HAPPENED.load(Ordering::SeqCst) {
        return Phase::Arrived;
    }
    // Never timed, and never folded into the branch below even though the predicate names it.
    if state == abi.state_searching {
        return Phase::Searching;
    }
    if super::lock_report::cancel_row_offered(state) {
        return Phase::Connecting;
    }
    // Everything else, including every state nobody has measured. An unmeasured state is one we
    // know nothing about, and the safe treatment of it is to leave it alone.
    Phase::Idle
}

/// Call a connect lost once it outlives every success on record, then tell the player and cancel it.
///
/// # What this is for
///
/// The wait the player sits through after a failed match is not Seamless computing an answer -- it
/// is a timeout counting down on an answer already decided, and the player can infer it because a
/// working invasion would have put them in the host's world by now. Measured across 262 runs: no
/// success ever took longer than 441ms from the match, and no failure ever resolved sooner than
/// 2346ms. [`er_invasion_warp_core::attempt_verdict`] carries the full table and the contamination
/// note that goes with it.
///
/// # Why this may drive a cancel when [`watch_for_stall`] proved so dangerous
///
/// The danger there was never the action; it was what earned it. That detector fired on a state
/// that had merely sat still, which an unmeasured state is entitled to do, and so it twice
/// cancelled a recovery the game was already performing. This fires only on a connect that has
/// outlived every recorded success, and [`cancel_stalled_attempt`] still refuses unless ERSC's own
/// hide-predicate would have drawn a Cancel row -- `0x12`, where the dead connects sit, is inside
/// that set. So the worst case is pressing a button the player could have pressed.
///
/// The arming gate is copied from [`watch_for_stall`] for the reason recorded there, and is the
/// second of the two defences: with the loop unarmed there is no hunt to call lost.
pub(super) fn watch_for_failed_connect(session: SeamlessSession) {
    if !AUTO_SEARCH_ARMED.load(Ordering::SeqCst) {
        // Reset rather than leave the clock running. A hunt that ends mid-connect must not hand a
        // part-elapsed deadline to the next one the player starts.
        if let Ok(mut guard) = ATTEMPT_VERDICT.lock() {
            *guard = er_invasion_warp_core::attempt_verdict::AttemptVerdict::new();
        }
        return;
    }
    let Some(state) = read_session_state(session.abi, session.session) else {
        return;
    };
    let phase = connect_phase(session.abi, state);
    let verdict = {
        let mut guard = match ATTEMPT_VERDICT.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        guard.observe(now_ms(), phase)
    };
    if verdict != Some(er_invasion_warp_core::attempt_verdict::Verdict::Failed) {
        return;
    }
    let attempt = FAILED_CONNECTS.fetch_add(1, Ordering::SeqCst) + 1;
    crate::standalone_log(format_args!(
        "local-invasion: the connection at state {state:#06x} has not landed in {}ms, and no \
         invasion on record has ever taken longer than 441ms from the match (n=313). Calling it \
         lost and cancelling, so the hunt can restart now instead of after Seamless's timeout.",
        er_invasion_warp_core::attempt_verdict::CONNECT_DEADLINE_MS
    ));
    // The banner goes with the cancel it used to accompany.
    //
    // This path stopped cancelling because its deadline was derived from runs this mod was already
    // shaping, so it cannot tell a slow connect from a dead one. The failure notice was left
    // behind, which means the player is told "Invasion failed -- no connection" about a connect
    // nothing is acting on and that may still land. The user's report is that sentence verbatim,
    // twice, and the second time with "I bet if I use my lynchpin without this mod, I would get
    // one" -- a banner that announces a failure the mod has admitted it cannot judge is worse than
    // silence, because it reads as the mod's verdict on an invasion that was still arriving.
    //
    // The log line above stays. It is the diagnostic, and it costs the player nothing.
    explain_if_hunt_is_emptying_the_pool(attempt);
    // Defence in depth over the allowlist above. `cancel_stalled_attempt_inner` falls back to
    // OPTIONSELECT_LEAVEWORLD when the Cancel row is not offered, and that fallback is what turned
    // a misjudged state into a player torn out of a live invasion. It is right for the stall
    // detector, which runs on attempts the engine has already abandoned. It is wrong here: if this
    // path ever reaches a state Seamless would not draw a Cancel row for, the phase mapping was
    // wrong and the correct action is none.
    if !super::lock_report::cancel_row_offered(state) {
        crate::standalone_log(format_args!(
            "local-invasion: not cancelling after all -- state {state:#06x} is outside the set \
             ERSC draws its own Cancel row for, so the deadline judged a state it should never \
             have been timing. The banner stands; nothing was driven."
        ));
        return;
    }
    // The deadline reports and no longer cancels.
    //
    // User ground truth, 2026-09-16, while invading: "Because of the er-invasion-warp feature it
    // says no connection. I normally can invade people." That sentence names this code twice --
    // "no connection" is this path's own banner text, from `RejectNotice::observe_failure`, and the
    // cancel below is what turned a connect that was still in progress into a dead one.
    //
    // The deadline was derived from an aggregation of this mod's own runs (n=313, slowest success
    // 441ms), so it describes connects that completed while the mod was shaping them, not connects
    // as the player experiences them without it. Timing a distribution measured through the
    // instrument being calibrated is how all four members of this family went wrong: the stall
    // watchdog cancelled a kept match at 0x15, cancelled a 0x11 retry 33x in one run, and the first
    // connect deadline tore the player out of a live invasion at 0x16. Every one was a detector
    // acting on a state whose real dwell nobody had measured from outside.
    //
    // So the observation stays -- the log line and the banner still say the connect is slow, which
    // is the diagnostic worth having -- and the action goes. Restoring it needs a dwell
    // distribution measured with this mod not driving, which `--without er-invasion-warp` now makes
    // a one-command run.
    let _ = cancel_stalled_attempt;
}

/// Per-site latches for [`log_refusal_once`]. Separate so a cancel refusal cannot silence an invade
/// one that happens to read the same.
static CANCEL_REFUSAL_SAID: Mutex<Option<String>> = Mutex::new(None);
/// Drive ERSC's own "Cancel search" for a rejected match.
///
/// This calls the exact option callback the user's click calls, with `(OSM, 0, 1, 1)`. The zero is
/// not a guess: the cancel action reads `rcx` and nothing else -- so no
/// captured argument is required and none is invented. Everything past this point -- tearing the
/// match down, returning the session to idle -- is Seamless's own code doing what it always does.
///
/// Returns whether Seamless was actually driven. `false` means the match stands: the caller must
/// not tell the player it was rejected.
pub(super) fn cancel_match(reason: RejectReason) -> bool {
    let session = match resolve_session() {
        Ok(session) => session,
        Err(cause) => {
            // Latched. The patient retry re-arms this every tick while the session is merely not
            // ready yet, and an unlatched line here wrote 431 identical entries in one run.
            log_refusal_once(
                &CANCEL_REFUSAL_SAID,
                format_args!(
                    "local-invasion: cannot cancel ({reason:?}) -- {cause:?}, so the match is LEFT \
                 ALONE and will land wherever the server sent it{}",
                    match cause {
                        NoSession::SessionNotIdentified => not_identified_detail(),
                        _ => "",
                    }
                ),
            );
            return false;
        }
    };
    if let Some(why) = session_guard_refuses(session.abi, session.session) {
        crate::standalone_log(format_args!(
            "local-invasion: cannot cancel ({reason:?}) -- {why}. Leaving the session alone rather \
             than tripping its abort path"
        ));
        return false;
    }
    let Some(cancel) = ersc_action(
        session.abi,
        session.abi.cancel_action_rva,
        session.abi.cancel_prologue,
    ) else {
        return false;
    };
    let Some(owner) = ersc_owner_or_refuse(&session, "cancel") else {
        return false;
    };
    report_lock_preconditions(&session, owner, "cancel");
    if let Some(refusal) = lock_shape_refusal(&session) {
        log_refusal_once(
            &CANCEL_REFUSAL_SAID,
            format_args!(
                "local-invasion: cannot cancel ({reason:?}) -- {refusal}. The match is LEFT ALONE. \
                 The reading this refused on is in the line immediately above."
            ),
        );
        return false;
    }
    // Reported, never obeyed. The set behind this reading is ERSC's hide-PREDICATE at
    // `ersc+0x26b40` -- when Seamless draws its Cancel row -- and the cancel action at
    // `ersc+0x258d0` has no state precondition at all: read end to end it locks the mutex at
    // `session+0x100`, compares `[session+0x14c]` against `0x7fffffff`, and writes `0x23`. Nothing
    // there consults `+0x150`.
    //
    // Treating a UI rule as a safety rule cost the whole feature. Measured 2026-09-08 in one
    // evening: a Frida-driven cancel succeeded eight times from `state_before 22` (`0x16`), every
    // one landing on `state_after 35` (`0x23`), with no crash -- and on the very next run this
    // same reading refused `state 0x16` and the rejected invasion proceeded. The guard that does
    // matter is `session_guard_refuses`, checked above and left in force.
    if let Some(note) = cancel_row_refusal(&session) {
        log_refusal_once(
            &CANCEL_REFUSAL_SAID,
            format_args!(
                "local-invasion: cancelling ({reason:?}) from a state Seamless would not have \
                 drawn its own Cancel row in -- {note}. Driving it anyway: the action itself has \
                 no state precondition, and this state is measured to cancel cleanly."
            ),
        );
    }
    // The one state reading that still refuses, because it is not about the row: an idle session
    // while a join is in flight cannot be the real session -- the player is mid-search, so the
    // real one reads `state_searching`. Keeping a pointer proven wrong means every later rejection
    // refuses on the same reading, which is exactly what run br-20260909-000018-d691 did.
    //
    // Dropping the cache here rather than at the verdict is deliberate: `drive_pending_cancel`
    // re-arms this rejection for `CANCEL_RETRY_TICKS`, so the sweeper gets its seconds while the
    // rejection is still live. Invalidating at the verdict deleted the session at the moment it
    // was needed, which is the mistake this replaces.
    if read_session_state(session.abi, session.session) == Some(session.abi.state_idle)
        && JOIN_IN_FLIGHT.load(Ordering::SeqCst)
    {
        crate::standalone_log(format_args!(
            "local-invasion: dropping the cached session at {:#x} -- it reads idle while a join \
             is in flight, which the real session cannot do. The sweeper looks again; the \
             rejection stays armed meanwhile.",
            session.session
        ));
        // Windows-only: the scan it invalidates does not exist on the host, where these tests run.
        #[cfg(windows)]
        session_scan::invalidate_cached_session();
        return false;
    }
    // Nothing is refused here, and the reading that looked like it should refuse does not.
    //
    // Measured on run br-20260910-012622-fd23: four cancels, each driven immediately after a
    // `join-progress` line, and all four of those lines read `Progressing`. Two settled in ~1.6s
    // and two took ~30.2s, so that verdict does not separate them and a guard on it would have
    // refused every cancel this filter has ever driven. `JOIN_IN_FLIGHT` is worse still: it is set
    // on every match and cleared in exactly one place, `lobby_state::CLIENT`, which is the moment
    // a join lands -- a match this filter rejects never lands, so gating on it refuses the cancel
    // forever rather than for a tick.
    //
    // The 30s is not spent deciding whether to cancel; it is spent inside `0x23` afterwards. The
    // two fast cancels passed through `lobby=7 proto=1 joinCheck=30.0 -> proto=2 joinCheck=29.7`
    // and left 0x23 three tenths of a second into that countdown; the two slow ones never reached
    // `lobby=7` at all and sat out its full 30.0s. `joinCheck`/`waitInit` are f32 seconds, which is
    // why no `30000` immediate was ever found in ersc's `.text`.
    let _call = OurCall::enter();
    unsafe { cancel(owner, 0, 1, 1) };
    drop(_call);
    note_state_after_our_action(session, "cancel");
    let fired = CANCELS.fetch_add(1, Ordering::SeqCst) + 1;
    // Search again once the session settles. Armed here, fired from the tick -- ERSC's own tick
    // does not run while the session is idle, which is why the frida attempt to re-invade from
    // inside an ERSC callback never fired.
    //
    // The arm is unconditional, and that is the fix for the complaint that a rejection ends the
    // hunt: cancelling a match this filter rejected is the hunt, whoever started the search.
    //
    // It used to require `AUTO_SEARCH_ARMED` to already be true, and that flag is set in exactly
    // one other place -- the state tracker, on sampling the `0x01 -> 0x0e` edge. That edge took
    // 38ms on run br-20260909-233549-72f5, which is inside one tick at 40fps, so it is missable;
    // and a search Seamless started from its own menu never produces it for us at all. Measured on
    // run br-20260910-011752-2910: `REJECT 0x0b000000 (WrongBlock)` followed immediately by
    // `cancelled rejected match (#1) -- ... auto re-search is disarmed, so this stops here`. The
    // player then watched the invasion item time out and had to use it again, which is not a delay
    // before the next search -- it is no next search at all.
    //
    // Standing the loop down stays possible and stays the player's call: opening Seamless's own
    // menu clears the flag (`show_observer`), and that is a deliberate act, unlike a sampling miss.
    //
    // Every reason but one is a rejection, and a rejection is the hunt. [`RejectReason::PlayerStopped`]
    // is the player saying stop, and re-arming on it is how a call-off became an invasion.
    //
    // Measured on run `br-20260918-032135-4b68`, in the order the log wrote it:
    //
    // ```text
    //   the bounds popup chose NearbyOnly ... requested=true
    //   auto re-search stood down -- you used the finger again and confirmed the invasion
    //     search should be called off. Nothing here will start another search until you ask for one.
    //   about to drive ERSC cancel -- state=0x12
    //   cancelled rejected match (#1) -- session returns to idle and the search restarts automatically
    //   0x12 -> 0x23 CANCELLING -> 0x24 -> 0x01 IDLE
    //   about to drive ERSC invade -- state=0x1 IDLE
    //   0x01 IDLE -> 0x0e SEARCHING (driven by us: restart search)
    //   hunt: decision=no_finger -- the query goes out exactly as Seamless built it
    //   ... 0x13 -> 0x14 -> 0x16, host Steam id 76561198027062262
    // ```
    //
    // `stand_down_hunt` cleared the flag four lines above; this store put it straight back, and
    // `drive_pending_reinvade` fired two milliseconds after the session reached idle. The search it
    // started was worse than the one the player stopped: `stand_down_hunt` had already retired the
    // finger, so `hunt_target` answered `no_finger` and the query went out unnarrowed, with
    // `apply_finger_override` no longer forcing `enabled`, so nothing judged what came back. A
    // `Nearby only` call-off became a whole-population Seamless invasion. Player, 2026-09-17: "I
    // called off invading nearby only, and as soon as I did, I invaded someone in seamless. Only
    // near+far should ever hit seamless when near exhausts."
    //
    // The disarm is not merely "do not arm". `stand_down_hunt` runs before the cancel is driven, so
    // anything that armed the loop in between -- the state tracker riding the unwind, a join this
    // filter accepted dying -- would outlive the stop it was meant to end.
    if reason == RejectReason::PlayerStopped {
        AUTO_SEARCH_ARMED.store(false, Ordering::SeqCst);
        PENDING_REINVADE.store(false, Ordering::SeqCst);
        crate::standalone_log(format_args!(
            "local-invasion: cancelled the search you stopped (#{fired}) -- the session returns to \
             idle and stays there. Nothing restarts it: this cancel is the player's stop, not a \
             rejected destination, and the loop is left disarmed the way `stand_down_hunt` asked."
        ));
        return true;
    }
    AUTO_SEARCH_ARMED.store(true, Ordering::SeqCst);
    PENDING_REINVADE.store(true, Ordering::SeqCst);
    crate::standalone_log(format_args!(
        "local-invasion: cancelled rejected match (#{fired}) -- session returns to idle and the \
         search restarts automatically. Press Cancel search yourself, or open Seamless's own menu, \
         to stand the loop down."
    ));
    true
}

/// How many connects in a row must die before hunt is named as the likely reason.
///
/// One dead host is ordinary -- a player quit, a lobby entry went stale. A run of them while hunt
/// is on is a different thing, and it has one overwhelmingly likely cause.
const HUNT_EMPTY_POOL_STREAK: u32 = 3;

/// Say that hunt is probably why nothing lands, once a streak makes it the likely answer.
///
/// Hunt adds a Steam lobby-list filter on a key only this DLL publishes, so the only hosts it can
/// match are ones running this build. When nobody else is, the entries it does find are stale: the
/// connect reaches the join state and never lands, the deadline calls it lost, and the player sees
/// "Invasion failed -- no connection" on every attempt with nothing explaining it.
///
/// Measured 2026-09-15 on a live session: with hunt on, every attempt died at the deadline; with
/// hunt off and nothing else changed, the next attempt loaded into an invasion. The mechanism was
/// clear from the config line and the failure was not, which is the gap this closes -- the log
/// already said what happened, over and over, and never once said why.
///
/// Said once per streak, not once per failure: repeating it every 1500ms would bury the line that
/// carries the state and the timing.
fn explain_if_hunt_is_emptying_the_pool(attempt: u32) {
    if attempt != HUNT_EMPTY_POOL_STREAK {
        return;
    }
    let Some(config) = super::current_config() else {
        return;
    };
    if !config.hunt {
        return;
    }
    crate::standalone_log(format_args!(
        "local-invasion: {attempt} connects in a row have died at the deadline with hunt on. Hunt \
         filters the lobby query on a key only this DLL publishes, so the only hosts it can match \
         are ones running this build -- if nobody else is, every entry it finds is stale and no \
         connection can land. This is hunt working as designed against an empty pool, not a \
         failure of the connection. Set hunt = false in er-invasion-warp.toml to match everybody \
         again; note that prefilter_radius only acts inside the hunt-filtered query, so it goes \
         inert with it."
    ));
}

/// Put the dropped search on the banner, gated on the same notice option as every other message.
///
/// Three bails in `drive_pending_reinvade` used to end a search with a log line and nothing on
/// screen. From the chair that is identical to a search quietly running: run
/// br-20260916-040126-e719 left "Searching for an invasion in Foot of the Forge" up while the
/// search had already been dropped on its first tick, and the player waited on it.
#[cfg(not(windows))]
fn say_the_search_was_dropped() {}

#[cfg(windows)]
fn say_the_search_was_dropped() {
    let notice = crate::local_invasion_filter::current_config_snapshot()
        .is_none_or(|config| config.reject_notice);
    crate::local_invasion_filter::banner::announce_cannot_search(notice);
}
