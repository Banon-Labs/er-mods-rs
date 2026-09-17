//! One query that asks whether a location search is worth running at all.
//!
//! # The question, and the measurement that answers it
//!
//! A nearby search walks a ring of blocks, one Steam query per block, up to 49 of them at radius
//! three. Every one of those queries is wasted if nobody anywhere is publishing a block id, which
//! is the ordinary case while this mod has few users. The user asked for the obvious shortcut:
//! "are we able to look for all block IDs before we hone in on the region block ids we want to
//! invade? That way we can jump straight to seamless invasions if there isn't a single person
//! hosting with a block ID".
//!
//! Steam has no "this key exists" filter. The nearest thing is `k_ELobbyComparisonNotEqual`
//! against the empty string, and whether that behaves as an existence test depends on how the
//! backend treats a lobby that never set the key -- undocumented, so it was measured, live, with
//! both controls in the same run (2026-09-16, run br-20260916-235852-c2b4):
//!
//! ```text
//!   unfiltered control                                            50
//!   the advertisement pair, equality                              50
//!   POSITIVE  <advertisement key>            != ""                50
//!   the question  er_invasion_warp_map       != ""                 0
//!   NEGATIVE  a key nobody anywhere publishes != ""                 0
//! ```
//!
//! The positive control rules out "the operator returns nothing" and the negative control rules
//! out "the operator returns everything". Only with both does the middle row mean what it says: a
//! lobby that never set the key does not match. Every earlier zero in this repo lacked exactly
//! that pair, which is why they were uninterpretable.
//!
//! # What this does with the answer
//!
//! `Verdict::NobodyPublishes` tells the ladder not to bother: `hunt_target` returns `None`, the
//! query goes out with Seamless's own shape and nothing else, and the search behaves like a plain
//! Seamless invasion from the first round instead of after 49 empty ones.
//!
//! # The second question, and the phase it ends
//!
//! The same machinery answers a narrower one: which of these particular places has somebody in
//! it. [`live::arm_sweep`] takes the ring a nearby search covers and asks one query per place,
//! `er_invasion_warp_map` equal to that block. It is what makes `Both near and far` two phases
//! rather than one -- the near half runs until every nearby place has answered, and the far half
//! begins when they have all answered zero. Before it existed the ladder moved one place per
//! failed invasion attempt, so exhausting a ring of 49 would have taken most of an hour and the
//! far half was never reached.
//!
//! # What it never does
//!
//! Requests, and reads. No join, no create, no lobby write, and no game state touched at all. A
//! query narrows our own result set and changes nothing for any other player.

#[cfg(windows)]
pub use live::{Nearby, Verdict, arm, arm_sweep, clear_sweep, is_armed, nearby, tick, verdict};

/// Host-side stand-ins, so the rest of the crate compiles under `cargo test` on Linux.
#[cfg(not(windows))]
mod host {
    /// What the pre-flight concluded.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum Verdict {
        /// The query has not been answered yet, so the ladder should behave as it always has.
        Unknown,
        /// At least one host somewhere is publishing a block id, so narrowing can find somebody.
        SomebodyPublishes,
        /// Nobody anywhere is, so every block query would come back empty.
        NobodyPublishes,
    }

    /// What the neighbourhood sweep has established.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum Nearby {
        /// No sweep has been armed, so nothing is known and nothing is claimed.
        Idle,
        /// Still asking.
        Asking {
            /// How many places have answered.
            answered: usize,
            /// How many places there are.
            total: usize,
        },
        /// Somebody is hosting in this place.
        Found(u32),
        /// Every place was asked and every one came back empty.
        Empty(usize),
    }
}

#[cfg(not(windows))]
pub use host::{Nearby, Verdict};

/// Host-side stub.
#[cfg(not(windows))]
pub fn arm_sweep(_blocks: &[u32]) {}

/// Host-side stub.
#[cfg(not(windows))]
pub fn clear_sweep() {}

/// Host-side stub: nothing was ever asked, so nothing is known.
#[cfg(not(windows))]
#[must_use]
pub fn nearby() -> Nearby {
    Nearby::Idle
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn arm() {}

/// Host-side stub.
#[cfg(not(windows))]
pub fn tick() {}

/// Host-side stub.
#[cfg(not(windows))]
#[must_use]
pub fn is_armed() -> bool {
    false
}

/// Host-side stub: nothing was ever asked, so nothing is known.
#[cfg(not(windows))]
#[must_use]
pub fn verdict() -> Verdict {
    Verdict::Unknown
}

#[cfg(windows)]
mod live {
    use std::sync::atomic::{AtomicU32, AtomicU64, AtomicUsize, Ordering};

    use crate::lobby_publish::{LOBBY_MAP_KEY, advertisement_key, matchmaking_interface};

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetModuleHandleA(name: *const u8) -> isize;
        fn GetProcAddress(module: isize, name: *const u8) -> usize;
    }

    /// `void AddRequestLobbyListStringFilter(ISteamMatchmaking*, const char *key,
    /// const char *value, ELobbyComparison)`, the flat export.
    ///
    /// The flat name rather than vtable slot 5, unlike the publish path next door. A slot index is
    /// a property of one interface version and silently addresses a different method when that
    /// version moves; this name is the ABI Valve maintains. The same reasoning
    /// `FRIENDS_PERSONA_NAME` already records, and it is a filter rather than a detour, so there
    /// is no need to be on the same code path Seamless is.
    type AddStringFilterFn = unsafe extern "system" fn(usize, *const u8, *const u8, i32);
    /// `void AddRequestLobbyListDistanceFilter(ISteamMatchmaking*, ELobbyDistanceFilter)`.
    type AddDistanceFilterFn = unsafe extern "system" fn(usize, i32);
    /// `void AddRequestLobbyListResultCountFilter(ISteamMatchmaking*, int)`.
    type AddResultCountFn = unsafe extern "system" fn(usize, i32);
    /// `SteamAPICall_t RequestLobbyList(ISteamMatchmaking*)`.
    type RequestLobbyListFn = unsafe extern "system" fn(usize) -> u64;
    /// `ISteamUtils* SteamAPI_SteamUtils_v010(void)`.
    type UtilsAccessor = unsafe extern "system" fn() -> usize;
    /// `bool IsAPICallCompleted(ISteamUtils*, SteamAPICall_t, bool *failed)`.
    type IsCompletedFn = unsafe extern "system" fn(usize, u64, *mut bool) -> bool;
    /// `bool GetAPICallResult(ISteamUtils*, SteamAPICall_t, void *out, int size, int expected,
    /// bool *failed)`.
    type GetResultFn = unsafe extern "system" fn(usize, u64, *mut u8, i32, i32, *mut bool) -> bool;

    const UTILS_ACCESSOR: &str = "SteamAPI_SteamUtils_v010\0";
    const ADD_STRING_FILTER: &str = "SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter\0";
    const ADD_DISTANCE_FILTER: &str =
        "SteamAPI_ISteamMatchmaking_AddRequestLobbyListDistanceFilter\0";
    const ADD_RESULT_COUNT: &str =
        "SteamAPI_ISteamMatchmaking_AddRequestLobbyListResultCountFilter\0";
    const REQUEST_LOBBY_LIST: &str = "SteamAPI_ISteamMatchmaking_RequestLobbyList\0";
    const IS_API_CALL_COMPLETED: &str = "SteamAPI_ISteamUtils_IsAPICallCompleted\0";
    const GET_API_CALL_RESULT: &str = "SteamAPI_ISteamUtils_GetAPICallResult\0";

    /// `k_ELobbyComparisonNotEqual`, from `steamclientpublic.h`. The operator the existence test is
    /// built out of; see this module's header for the controls that prove it is one.
    const COMPARISON_NOT_EQUAL: i32 = 3;
    /// `k_ELobbyComparisonEqual`, for the advertisement pair.
    const COMPARISON_EQUAL: i32 = 0;
    /// `k_ELobbyDistanceFilterWorldwide`. Steam defaults to a regional filter, which would hide
    /// hosts on another continent and turn a real "somebody is publishing" into a false "nobody
    /// is" -- the one answer this module must never get wrong, because it is the answer that
    /// switches the ladder off.
    const DISTANCE_WORLDWIDE: i32 = 3;
    /// One result is enough. This asks whether the set is empty, not who is in it, and a smaller
    /// page is less work for Steam and for us.
    const RESULT_COUNT: i32 = 1;

    /// `LobbyMatchList_t::k_iCallback` -- `k_iSteamMatchmakingCallbacks` (500) plus 10. The struct
    /// is one `uint32 m_nLobbiesMatching`.
    const LOBBY_MATCH_LIST_CALLBACK: i32 = 510;
    const LOBBY_MATCH_LIST_SIZE: i32 = 4;

    /// The empty string the existence test compares against. Terminated by `send_query` along with
    /// every other filter value, so it is written here as what it is rather than as a C string.
    const EMPTY_VALUE: &str = "";

    /// Nothing has been asked.
    const STAGE_IDLE: usize = 0;
    /// A search wants an answer and the query has not gone out yet.
    const STAGE_WANTED: usize = 1;
    /// The query is out; `CALL` holds its handle.
    const STAGE_SENT: usize = 2;
    /// Steam answered; `MATCHING` holds the count.
    const STAGE_ANSWERED: usize = 3;

    static STAGE: AtomicUsize = AtomicUsize::new(STAGE_IDLE);
    static CALL: AtomicU64 = AtomicU64::new(0);
    static MATCHING: AtomicU32 = AtomicU32::new(0);
    static SAID: AtomicUsize = AtomicUsize::new(0);

    /// Set once the question has ever been answered, so the idle asker stops asking. `STAGE` alone
    /// cannot say this: a failed collect returns it to `STAGE_IDLE`, which is indistinguishable
    /// from never having asked.
    static MATCHING_SEEN: AtomicUsize = AtomicUsize::new(0);

    /// How many times the idle asker has armed the question this process.
    static IDLE_ASKS: AtomicUsize = AtomicUsize::new(0);

    /// Attempts allowed before the idle asker gives up. Steam is not resolvable on every tick a
    /// world is up, and a failed collect returns to `STAGE_IDLE`, so a single try would lose the
    /// answer to a transient. A backend that always fails stops here instead of asking forever.
    const MAX_IDLE_ASKS: usize = 8;

    /// What the pre-flight concluded.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum Verdict {
        /// The query has not been answered yet, so the ladder should behave as it always has.
        ///
        /// Deliberately not "assume nobody": a search armed one frame before the answer lands
        /// would otherwise skip its own ring on no evidence at all.
        Unknown,
        /// At least one host somewhere is publishing a block id, so narrowing can find somebody.
        SomebodyPublishes,
        /// Nobody anywhere is, so every block query would come back empty.
        NobodyPublishes,
    }

    fn export(name: &str) -> Option<usize> {
        let module = unsafe { GetModuleHandleA(c"steam_api64.dll".as_ptr().cast()) };
        if module == 0 {
            return None;
        }
        let address = unsafe { GetProcAddress(module, name.as_ptr()) };
        (address != 0).then_some(address)
    }

    fn utils() -> Option<usize> {
        let accessor = export(UTILS_ACCESSOR)?;
        let iface = unsafe { core::mem::transmute::<usize, UtilsAccessor>(accessor)() };
        (iface != 0).then_some(iface)
    }

    /// Ask the question. Idempotent: an answer already in hand is kept rather than re-asked.
    ///
    /// Re-asking on every search would be the safer-looking choice and is the wrong one. The answer
    /// changes only when somebody else installs this mod and opens their world, which is not a
    /// per-search event, and a query per search is exactly the cost this module exists to remove.
    pub fn arm() {
        let _ =
            STAGE.compare_exchange(STAGE_IDLE, STAGE_WANTED, Ordering::SeqCst, Ordering::SeqCst);
    }

    /// Whether a question is outstanding or answered, for a caller reporting what it is waiting on.
    #[must_use]
    pub fn is_armed() -> bool {
        STAGE.load(Ordering::SeqCst) != STAGE_IDLE
    }

    /// What the pre-flight concluded, for the ladder to act on.
    #[must_use]
    pub fn verdict() -> Verdict {
        if STAGE.load(Ordering::SeqCst) != STAGE_ANSWERED {
            return Verdict::Unknown;
        }
        if MATCHING.load(Ordering::SeqCst) == 0 {
            Verdict::NobodyPublishes
        } else {
            Verdict::SomebodyPublishes
        }
    }

    /// Send the query, or collect its answer. Call once per game tick.
    ///
    /// Runs on the game task, which is a thread the process already owns and already pumps Steam
    /// callbacks on, so the call handle can be polled through `ISteamUtils` without registering a
    /// callback of our own.
    pub fn tick() {
        ask_once_while_nothing_is_searching();
        match STAGE.load(Ordering::SeqCst) {
            STAGE_WANTED => send(),
            STAGE_SENT => collect(),
            _ => {}
        }
        // One `RequestLobbyList` in flight at a time.
        //
        // This used to run the sweep unconditionally, and the comment here said so deliberately:
        // the sweep's reads were worth five seconds because every sentence on screen should be
        // something a query answered. The cost was not five seconds. `send()` issues the
        // pre-flight and this line issued a sweep place in the same tick, two requests on one
        // `ISteamMatchmaking`, and the pre-flight lost every time -- run br-20260917-163602-8eb6
        // logged `the block-id existence query did not come back` three times out of three, so
        // `verdict()` never left `Unknown` and the `NobodyPublishes` shortcut was unreachable
        // code. The Frida trace names the collision: request 1 the existence pre-flight, request 2
        // a sweep place, `since_previous_ms: 0`.
        //
        // Holding the sweep costs the pre-flight's own latency once and buys its answer, which is
        // the answer that can make the whole 49-place walk unnecessary. A pre-flight that fails
        // anyway returns to `STAGE_IDLE`, and the sweep then proceeds exactly as it always did.
        let stage = STAGE.load(Ordering::SeqCst);
        if stage != STAGE_WANTED && stage != STAGE_SENT {
            sweep_tick();
        }
        hand_over_when_the_neighbourhood_is_empty();
    }

    /// Ask the existence question while the session is idle, so the answer exists before it is
    /// needed.
    ///
    /// Arming it when the item is used cannot work, and three runs measured why. The query is one
    /// `RequestLobbyList`, and using a finger starts a search that issues its own: run
    /// br-20260917-164642-cf98 logs `hunt: RequestLobbyList reached our detour for the first time`
    /// -- Seamless's own query, narrowed by our hunt filter -- directly above
    /// `preflight: the block-id existence query did not come back`, three runs out of three. A
    /// pre-flight sent into a live search does not survive it, so `verdict()` never left `Unknown`
    /// and every shortcut that reads it was unreachable code.
    ///
    /// Idle is the whole point, and it is what this module's `arm` doc already asked for: the
    /// answer "changes only when somebody else installs this mod and opens their world, which is
    /// not a per-search event". One question per session, asked when nothing competes for the
    /// interface, answered long before an item is used.
    ///
    /// Retried rather than one-shot because Steam is not resolvable at every tick a world is: an
    /// unresolvable interface leaves `send` at `STAGE_WANTED` and a failed collect returns to
    /// `STAGE_IDLE`, and neither is an answer. The cap stops a backend that always fails from
    /// asking forever.
    fn ask_once_while_nothing_is_searching() {
        if STAGE.load(Ordering::SeqCst) != STAGE_IDLE {
            return;
        }
        // A search of any kind is a competitor for the interface. `Nearby::Idle` is the state
        // where no ring is armed, which is the only time this is safe to ask.
        if !matches!(nearby(), Nearby::Idle) {
            return;
        }
        if MATCHING_SEEN.load(Ordering::SeqCst) != 0 {
            return;
        }
        let attempts = IDLE_ASKS.fetch_add(1, Ordering::SeqCst) + 1;
        if attempts > MAX_IDLE_ASKS {
            return;
        }
        arm();
        if attempts == 1 {
            crate::standalone_log(format_args!(
                "preflight: asking the block-id existence question while nothing is searching. A \
                 search issues its own RequestLobbyList and a pre-flight sent into one does not \
                 come back, so this is asked here instead of when an item is used -- once per \
                 session, at most {MAX_IDLE_ASKS} attempts if Steam is not resolvable yet."
            ));
        }
    }

    /// The near/far boundary, taken on the game task rather than inside the query detour.
    ///
    /// `Both near and far` is two searches, and the second one is Seamless's own. Until now the
    /// only thing that happened here was `hunt_target` declining to add a filter, which left our
    /// override -- `enabled`, `hunt`, `steam_hooks` -- in force over a search that was supposed to
    /// have stopped being ours. See `local_invasion_filter::hand_off_to_seamless`.
    ///
    /// Self-limiting: the handover clears the sweep, so the next tick reads `Nearby::Idle` and
    /// this does nothing until another search arms one.
    fn hand_over_when_the_neighbourhood_is_empty() {
        if !crate::local_invasion_filter::finger_reach_is_near_and_far() {
            return;
        }
        let Nearby::Empty(asked) = nearby() else {
            return;
        };
        crate::local_invasion_filter::hand_off_to_seamless(&format!(
            "all {asked} nearby place(s) answered zero, so the near half of `Both near and far` is \
             over and the far half is Seamless's own search"
        ));
    }

    /// Build one lobby query out of the filters given, send it, and hand back its handle.
    ///
    /// Every query this module sends has the same shape apart from its filters. Worldwide, because
    /// Steam's default is regional and would hide a host on another continent -- which would turn
    /// a real "somebody is publishing" into a false "nobody is", the one answer here that must
    /// never be wrong. One result, because these ask whether anybody is there rather than who.
    /// And Seamless's own advertisement pair whenever the key is known, so a stranger's unrelated
    /// lobby that happens to carry a key of the same name cannot answer for us.
    ///
    /// The filter strings are copied into owned, terminated buffers that outlive the request
    /// rather than being built per iteration: the filters are accumulated on the interface and
    /// consumed by `RequestLobbyList`, so anything Steam kept a pointer to has to still be there
    /// when that call runs.
    fn send_query(filters: &[(&str, &str, i32)]) -> Option<u64> {
        // Claimed before the first filter is written and released after the request that consumes
        // them. It also tells the hunt detour that the request about to arrive is ours, so nothing
        // is appended to a query whose filters are its entire meaning.
        let _own = crate::lobby_publish::stage_own_query();
        let iface = matchmaking_interface()?;
        let add_string = export(ADD_STRING_FILTER)?;
        let add_distance = export(ADD_DISTANCE_FILTER)?;
        let add_count = export(ADD_RESULT_COUNT)?;
        let request = export(REQUEST_LOBBY_LIST)?;
        let mut terminated: Vec<(Vec<u8>, Vec<u8>, i32)> = Vec::new();
        if let Some(key) = advertisement_key() {
            let mut key = key;
            key.push(0);
            let mut value = crate::lobby_publish::ADVERTISEMENT_MARKER_VALUE
                .as_bytes()
                .to_vec();
            value.push(0);
            terminated.push((key, value, COMPARISON_EQUAL));
        }
        for (key, value, comparison) in filters {
            terminated.push((
                format!("{key}\0").into_bytes(),
                format!("{value}\0").into_bytes(),
                *comparison,
            ));
        }
        // SAFETY: four exports resolved by name from a module that is loaded, called with the
        // process-wide interface singleton and pointers into buffers that outlive the request.
        let call = unsafe {
            core::mem::transmute::<usize, AddDistanceFilterFn>(add_distance)(
                iface,
                DISTANCE_WORLDWIDE,
            );
            core::mem::transmute::<usize, AddResultCountFn>(add_count)(iface, RESULT_COUNT);
            let add_string = core::mem::transmute::<usize, AddStringFilterFn>(add_string);
            for (key, value, comparison) in &terminated {
                add_string(iface, key.as_ptr(), value.as_ptr(), *comparison);
            }
            core::mem::transmute::<usize, RequestLobbyListFn>(request)(iface)
        };
        drop(terminated);
        Some(call)
    }

    /// What a sent query has come back with.
    enum Answer {
        /// Steam has not answered yet.
        Pending,
        /// The call did not come back. This is not an answer of zero, and treating it as one is
        /// the single mistake in this module that would silently remove the feature it gates.
        Failed,
        /// This many lobbies matched.
        Count(u32),
    }

    fn poll_query(call: u64) -> Answer {
        let Some(utils) = utils() else {
            return Answer::Pending;
        };
        let (Some(completed), Some(result)) =
            (export(IS_API_CALL_COMPLETED), export(GET_API_CALL_RESULT))
        else {
            return Answer::Pending;
        };
        let mut failed = false;
        // SAFETY: two exports resolved by name, called with the utils singleton and a handle the
        // sender stored, writing only into locals.
        let done = unsafe {
            core::mem::transmute::<usize, IsCompletedFn>(completed)(utils, call, &raw mut failed)
        };
        if !done {
            return Answer::Pending;
        }
        let mut payload = [0u8; LOBBY_MATCH_LIST_SIZE as usize];
        // SAFETY: the buffer is exactly the size the callback declares, and the expected id names
        // the struct Steam will write.
        let ok = unsafe {
            core::mem::transmute::<usize, GetResultFn>(result)(
                utils,
                call,
                payload.as_mut_ptr(),
                LOBBY_MATCH_LIST_SIZE,
                LOBBY_MATCH_LIST_CALLBACK,
                &raw mut failed,
            )
        };
        if !ok || failed {
            return Answer::Failed;
        }
        Answer::Count(u32::from_le_bytes(payload))
    }

    fn send() {
        let Some(call) = send_query(&[(LOBBY_MAP_KEY, EMPTY_VALUE, COMPARISON_NOT_EQUAL)]) else {
            return;
        };
        CALL.store(call, Ordering::SeqCst);
        STAGE.store(STAGE_SENT, Ordering::SeqCst);
    }

    fn collect() {
        let matching = match poll_query(CALL.load(Ordering::SeqCst)) {
            Answer::Pending => return,
            // Returning to idle means the next search asks again rather than skipping its ring on
            // a reading that never arrived.
            Answer::Failed => {
                STAGE.store(STAGE_IDLE, Ordering::SeqCst);
                crate::standalone_log(format_args!(
                    "preflight: the block-id existence query did not come back, so nothing is \
                     concluded and the next search asks again"
                ));
                return;
            }
            Answer::Count(matching) => matching,
        };
        MATCHING.store(matching, Ordering::SeqCst);
        MATCHING_SEEN.store(1, Ordering::SeqCst);
        STAGE.store(STAGE_ANSWERED, Ordering::SeqCst);
        // Abandon the ring here, not where it was armed.
        //
        // `queue_the_places_being_searched` asks `verdict()` too, and at that instant the answer
        // cannot exist: using the item arms the pre-flight and queues the ring in the same call,
        // so the query has not even been sent. That check is a fast path for a second use in a
        // session that already has an answer, and it can never fire on the first. This is the
        // moment the answer arrives, so this is where a ring that is now known to be 49 empty
        // queries gets dropped.
        //
        // An empty sweep is the sweep's own way of saying the near half is over: `arm_sweep`
        // marks it finished and `nearby()` reports `Empty(0)`, which is what
        // `hand_over_when_the_neighbourhood_is_empty` waits for. So the far half begins on this
        // tick instead of after 49 round-trips.
        if matching == 0 && !matches!(nearby(), Nearby::Idle) {
            clear_sweep();
            arm_sweep(&[]);
            crate::local_invasion_filter::search_banner::clear();
            crate::local_invasion_filter::banner::announce_nothing_to_search(
                true,
                crate::local_invasion_filter::finger_reach_is_nearby_only(),
            );
            crate::standalone_log(format_args!(
                "preflight: dropped the armed nearby ring -- nobody anywhere publishes a block \
                 id, so every one of its queries is known empty before it is sent. The place \
                 queue is cleared rather than recited and the search widens now."
            ));
        }
        if SAID.swap(1, Ordering::SeqCst) == 0 {
            crate::standalone_log(format_args!(
                "preflight: {matching} host(s) anywhere publish a block id under \
                 `{LOBBY_MAP_KEY}`. {} Measured with both controls on 2026-09-16: a key every \
                 host carries answered 50 and a key nobody carries answered 0, so a zero here is \
                 an empty set rather than a filter that matches nothing.",
                if matching == 0 {
                    "So the nearby ring would be 49 empty queries and is skipped -- the search \
                     goes straight out with Seamless's own shape."
                } else {
                    "So narrowing to a location can find somebody, and the ring is walked."
                }
            ));
        }
    }

    // ---------------------------------------------------------------------------------------
    // The neighbourhood sweep
    // ---------------------------------------------------------------------------------------

    /// One query per nearby place, so "there is nobody nearby" becomes something measured.
    ///
    /// The ring above answers a different question -- is anybody, anywhere, publishing at all --
    /// and its zero is the cheap way out. This is the expensive way in: when somebody is
    /// publishing, every place in the ring is asked about directly, and the answer decides where
    /// the search points and when the nearby half of it is over.
    ///
    /// # Why this exists rather than counting rotations
    ///
    /// The ladder in `lobby_publish` moves one rung per failed invasion attempt, and an attempt
    /// takes the better part of a minute. Forty-nine of them is not a phase a player sits
    /// through, so "nearby is exhausted" never arrived and `Both near and far` never got to the
    /// far half: "Once I exhaust all nearby locations when doing near+far, I don't transition into
    /// a seamless invasion scheme." A sweep asks all forty-nine in a few seconds, because a lobby
    /// query is a read and costs nothing but the round trip.
    ///
    /// The alternative considered and rejected was to let the banner's recital stand in for the
    /// search -- drain the list, declare the neighbourhood spent. That would put a sentence on
    /// screen saying places had been tried when nothing had asked about them.
    struct Sweep {
        /// Which sweep this is. A query that comes back after its sweep was replaced belongs to a
        /// neighbourhood the player has left, and is dropped rather than recorded.
        generation: u64,
        /// The ring, in the order the banner recites it.
        blocks: Vec<u32>,
        /// Which place the outstanding query is about, and how far the sweep has got.
        next: usize,
        /// The query in flight, if one is.
        call: Option<u64>,
        /// The first place found with somebody in it. The sweep stops there.
        found: Option<u32>,
        /// How many places came back with an answer.
        answered: usize,
        /// Whether there is anything left to ask.
        finished: bool,
    }

    static SWEEP: std::sync::Mutex<Option<Sweep>> = std::sync::Mutex::new(None);
    static SWEEP_GENERATION: AtomicU64 = AtomicU64::new(0);

    /// What the sweep has established about the neighbourhood.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum Nearby {
        /// No sweep has been armed, so nothing is known and nothing is claimed.
        Idle,
        /// Still asking.
        Asking {
            /// How many places have answered.
            answered: usize,
            /// How many places there are.
            total: usize,
        },
        /// Somebody is hosting in this place.
        Found(u32),
        /// Every place was asked and every one came back empty. Carries how many were asked, so
        /// the line that reports it can say what was actually measured.
        Empty(usize),
    }

    fn with_sweep<T>(f: impl FnOnce(&mut Option<Sweep>) -> T) -> T {
        let mut guard = match SWEEP.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        f(&mut guard)
    }

    /// Ask about every one of these places, in this order. Replaces any sweep still running.
    ///
    /// Replacing rather than merging: a second search supersedes the first, and its ring is drawn
    /// around wherever the player is standing now.
    pub fn arm_sweep(blocks: &[u32]) {
        let total = blocks.len();
        let generation = SWEEP_GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
        with_sweep(|slot| {
            *slot = Some(Sweep {
                generation,
                blocks: blocks.to_vec(),
                next: 0,
                call: None,
                found: None,
                answered: 0,
                finished: total == 0,
            });
        });
        crate::standalone_log(format_args!(
            "sweep: armed for {total} nearby place(s) -- one lobby query each, asking which of \
             them has a host in it. Nothing is published and no game state is touched."
        ));
    }

    /// Forget the sweep, for a search that has been stood down.
    pub fn clear_sweep() {
        with_sweep(|slot| *slot = None);
    }

    /// What the sweep knows, for the ladder to point at and for the far half to wait on.
    #[must_use]
    pub fn nearby() -> Nearby {
        with_sweep(|slot| match slot.as_ref() {
            None => Nearby::Idle,
            Some(sweep) => match sweep.found {
                Some(block) => Nearby::Found(block),
                None if sweep.finished => Nearby::Empty(sweep.answered),
                None => Nearby::Asking {
                    answered: sweep.answered,
                    total: sweep.blocks.len(),
                },
            },
        })
    }

    /// What this tick has to do, decided under the sweep's lock and done outside it.
    ///
    /// Nothing that touches Steam may run while `SWEEP` is held. The hunt detour takes the query
    /// staging lock and then reads [`nearby`], which takes `SWEEP`; a sweep that sent its query
    /// under `SWEEP` would take those two in the opposite order and the pair would deadlock the
    /// game thread against Seamless's.
    enum Work {
        /// Nothing to do this tick.
        Idle,
        /// Ask about this place.
        Send {
            generation: u64,
            index: usize,
            block: u32,
        },
        /// Collect the answer about this place.
        Poll {
            generation: u64,
            index: usize,
            call: u64,
        },
    }

    /// What the sweep decided this tick, acted on after the lock is dropped.
    enum Outcome {
        Nothing,
        Found(u32),
        Empty(usize),
    }

    fn sweep_work() -> Work {
        with_sweep(|slot| {
            let Some(sweep) = slot.as_ref() else {
                return Work::Idle;
            };
            if sweep.finished {
                return Work::Idle;
            }
            match (sweep.call, sweep.blocks.get(sweep.next).copied()) {
                (Some(call), _) => Work::Poll {
                    generation: sweep.generation,
                    index: sweep.next,
                    call,
                },
                (None, Some(block)) => Work::Send {
                    generation: sweep.generation,
                    index: sweep.next,
                    block,
                },
                // An empty ring. Nothing was asked, so nothing is concluded here; `arm_sweep`
                // already marked it finished and `nearby` reports `Empty(0)`.
                (None, None) => Work::Idle,
            }
        })
    }

    /// Reach the sweep this work belongs to, or nothing if it has been replaced or cleared.
    fn with_this_sweep<T>(
        generation: u64,
        index: usize,
        f: impl FnOnce(&mut Sweep) -> T,
    ) -> Option<T> {
        with_sweep(|slot| {
            let sweep = slot.as_mut()?;
            (sweep.generation == generation && sweep.next == index).then(|| f(sweep))
        })
    }

    /// Send the next place's query, or collect the outstanding one. Called once per game tick.
    fn sweep_tick() {
        let outcome = match sweep_work() {
            Work::Idle => Outcome::Nothing,
            Work::Send {
                generation,
                index,
                block,
            } => {
                let value = crate::lobby_publish::map_value(
                    er_invasion_warp_core::invasion_warp::BlockKey::from_raw(block),
                );
                match send_query(&[(LOBBY_MAP_KEY, value.as_str(), COMPARISON_EQUAL)]) {
                    Some(call) => {
                        with_this_sweep(generation, index, |sweep| sweep.call = Some(call));
                        Outcome::Nothing
                    }
                    // Steam was not resolvable this tick. The place keeps its turn.
                    None => Outcome::Nothing,
                }
            }
            Work::Poll {
                generation,
                index,
                call,
            } => match poll_query(call) {
                Answer::Pending => Outcome::Nothing,
                // The place is asked again rather than counted empty. A failed call counted as a
                // zero is how a neighbourhood full of hosts becomes a measured "nobody nearby".
                Answer::Failed => {
                    with_this_sweep(generation, index, |sweep| sweep.call = None);
                    Outcome::Nothing
                }
                Answer::Count(matching) => with_this_sweep(generation, index, |sweep| {
                    let block = sweep.blocks[index];
                    sweep.call = None;
                    sweep.answered += 1;
                    if matching > 0 {
                        sweep.found = Some(block);
                        sweep.finished = true;
                        return Outcome::Found(block);
                    }
                    sweep.next += 1;
                    if sweep.next >= sweep.blocks.len() {
                        sweep.finished = true;
                        return Outcome::Empty(sweep.answered);
                    }
                    Outcome::Nothing
                })
                .unwrap_or(Outcome::Nothing),
            },
        };
        match outcome {
            Outcome::Nothing => {}
            Outcome::Found(block) => {
                // The queue goes before the answer does. The sweep has stopped asking, so every
                // place still queued is one the search will never query, and the banner would go
                // on naming them at one per 100ms underneath the line that says where it landed.
                crate::local_invasion_filter::search_banner::clear();
                crate::local_invasion_filter::banner::announce_found_host(true, block);
                crate::standalone_log(format_args!(
                    "sweep: a host is in {} -- the search points there and stops widening; the \
                     place queue is cleared so the banner names the answer instead of the places \
                     it is no longer asking about.",
                    crate::lobby_publish::map_value(
                        er_invasion_warp_core::invasion_warp::BlockKey::from_raw(block)
                    )
                ));
            }
            Outcome::Empty(asked) => {
                crate::standalone_log(format_args!(
                    "sweep: all {asked} nearby place(s) answered zero, so the neighbourhood is \
                     measurably empty. `Both near and far` drops the location filter from here \
                     and the query goes out exactly as Seamless builds it; `Nearby only` keeps \
                     asking, because staying near is what that row is for."
                ));
                // Only the near-and-far row widens, so only it gets the banner. The notice option
                // gates it like every other line the search paints.
                if crate::local_invasion_filter::finger_reach_is_near_and_far() {
                    let notice = crate::local_invasion_filter::current_config_snapshot()
                        .is_none_or(|config| config.reject_notice);
                    // `mod_only` is false on purpose. It means "the widened query still carries a
                    // key only this mod's hosts publish", and here that key is precisely what is
                    // being dropped: the detour adds no filter at all from now on, so what goes
                    // out is Seamless's own query and the whole population answers it.
                    crate::local_invasion_filter::banner::announce_search_everywhere(
                        notice, asked, false,
                    );
                }
            }
        }
    }
}
