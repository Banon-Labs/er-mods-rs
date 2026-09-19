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
pub use live::{
    Nearby, Verdict, arm, arm_sweep, clear_sweep, end_search, found_host_band, is_armed, nearby,
    tick, verdict,
};

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

/// Host-side stub: no sweep ran, so no host's band was read.
#[cfg(not(windows))]
#[must_use]
pub fn found_host_band() -> Option<String> {
    None
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn end_search() {}

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
    use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering};

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
    /// `CSteamID GetLobbyByIndex(ISteamMatchmaking*, int)`, the id returned as its `uint64`.
    type GetLobbyByIndexFn = unsafe extern "system" fn(usize, i32) -> u64;
    /// `const char *GetLobbyData(ISteamMatchmaking*, CSteamID, const char *key)`. Steam owns the
    /// buffer and reuses it, so a value is copied out before the next call.
    type GetLobbyDataFn = unsafe extern "system" fn(usize, u64, *const u8) -> *const u8;
    /// `ISteamMatchmaking* SteamAPI_SteamMatchmaking_v009(void)`.
    type MatchmakingAccessor = unsafe extern "system" fn() -> usize;

    const UTILS_ACCESSOR: &str = "SteamAPI_SteamUtils_v010\0";
    const ADD_STRING_FILTER: &str = "SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter\0";
    const ADD_DISTANCE_FILTER: &str =
        "SteamAPI_ISteamMatchmaking_AddRequestLobbyListDistanceFilter\0";
    const ADD_RESULT_COUNT: &str =
        "SteamAPI_ISteamMatchmaking_AddRequestLobbyListResultCountFilter\0";
    const REQUEST_LOBBY_LIST: &str = "SteamAPI_ISteamMatchmaking_RequestLobbyList\0";
    const IS_API_CALL_COMPLETED: &str = "SteamAPI_ISteamUtils_IsAPICallCompleted\0";
    const GET_API_CALL_RESULT: &str = "SteamAPI_ISteamUtils_GetAPICallResult\0";
    const MATCHMAKING_ACCESSOR: &str = "SteamAPI_SteamMatchmaking_v009\0";
    const GET_LOBBY_BY_INDEX: &str = "SteamAPI_ISteamMatchmaking_GetLobbyByIndex\0";
    const GET_LOBBY_DATA: &str = "SteamAPI_ISteamMatchmaking_GetLobbyData\0";

    /// A lobby value is capped by Steam at `k_cubChatMetadataMax`. The walk stops at the
    /// terminator; this only bounds an unterminated buffer.
    const LOBBY_VALUE_MAX: usize = 8192;

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

    fn matchmaking() -> Option<usize> {
        let accessor = export(MATCHMAKING_ACCESSOR)?;
        let iface = unsafe { core::mem::transmute::<usize, MatchmakingAccessor>(accessor)() };
        (iface != 0).then_some(iface)
    }

    /// One lobby a finished query named, with the keys this mod publishes already read off it.
    #[derive(Clone, Debug, Default, PartialEq, Eq)]
    pub struct FoundLobby {
        /// The lobby's own `CSteamID`, which is what a join is addressed to.
        pub id: u64,
        /// `er_invasion_warp_map` -- the block the host is standing in.
        pub map: String,
        /// `er_invasion_warp_effects` -- what invading them would be like.
        pub effects: String,
        /// Seamless's `lobby_key`. A host whose key differs is in another param pool.
        pub lobby_key: String,
        /// Seamless's `<level band>_<weapon band>` pair, as this host publishes it.
        ///
        /// Read for the same reason the pool key is: Seamless compares it for equality, so a host
        /// whose band differs cannot be returned however well every other field matches. Knowing
        /// the value the sweep's own hit publishes is what lets a search ask for that band directly
        /// instead of climbing the ladder blindly towards it -- measured 2026-09-18, the ladder
        /// needed five failed cycles to reach a host it had already positively identified.
        pub band: String,
    }

    /// Read one key off a lobby, copying Steam's buffer before anything can invalidate it.
    ///
    /// Steam hands back an interior pointer it owns and reuses, so holding it across the next call
    /// reads as an intermittently wrong string rather than as a crash.
    fn lobby_value(iface: usize, read: GetLobbyDataFn, lobby: u64, key: &str) -> String {
        let name = format!("{key}\0");
        // SAFETY: an export resolved by name, called with the interface singleton, a lobby id the
        // caller took from `GetLobbyByIndex`, and a terminated key that outlives the call.
        let raw = unsafe { read(iface, lobby, name.as_ptr()) };
        if raw.is_null() {
            return String::new();
        }
        let mut bytes = Vec::new();
        for offset in 0..LOBBY_VALUE_MAX {
            // SAFETY: walking a terminated C string Steam owns, bounded by the cap above.
            let byte = unsafe { *raw.add(offset) };
            if byte == 0 {
                break;
            }
            bytes.push(byte);
        }
        // A lobby value is written by another player's client, so its bytes are not ours to
        // trust; this is display and comparison text either way.
        // UTF-8 Lossy: one malformed byte must not discard the whole host.
        String::from_utf8_lossy(&bytes).into_owned()
    }

    /// Fetch the lobbies a finished query matched, with their keys.
    ///
    /// Until now this module asked Steam for a count and threw the answer away: `poll_query` read
    /// the single `uint32` of `LobbyMatchList_t` and nothing here ever called `GetLobbyByIndex`.
    /// A count is what let the banner say "found a host in Highroad Cross" while nobody held the
    /// lobby that sentence was about.
    ///
    /// Proven live before it was written, which is the order this repo works in
    /// (`scripts/er-prove-lobby-handoff.py`, run `br-20260918-003310-606c`): the same flat exports
    /// in the same sequence returned lobby `109775241801277563` carrying
    /// `er_invasion_warp_map = m61_48_45_00` and `er_invasion_warp_effects = allow_invaders`.
    /// Seamless's advertised-availability flag, hashed like the rest of its key names.
    ///
    /// Matched by name because the name is stable for the one Seamless build this repo supports.
    /// A host publishing `false` here is excluded from every query this game sends, whatever its
    /// band or block says, so it is the one key worth re-reading before trusting an old hit.
    const AVAILABLE_KEY: &str = "91489e05e1c2c5e7701b2d92ec209a8acd594349f1e21e73422430b114a7c467";

    /// Seamless's `<level band>_<weapon band>` key, hashed like its others.
    ///
    /// Matched by name for the one build this repo supports. The value's `<digits>_<digits>` shape
    /// is what identifies it across builds, and
    /// `er_invasion_warp_core::band_ladder::looks_like_band` is where that test lives -- this
    /// constant only saves reading every key off a lobby to find the one matching it.
    const SEAMLESS_BAND_KEY: &str =
        "21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e";

    /// The band the host the sweep found publishes, when there is one and it is band-shaped.
    ///
    /// This is what lets a search ask for the band its target is actually in. The ladder still owns
    /// the case where no host has been identified -- it is a walk through bands nobody has been seen
    /// in -- but climbing towards a host whose band has already been read off their own lobby is
    /// five wasted failed cycles, measured 2026-09-18 against a friend publishing `2_1` while the
    /// search asked `0_0` and every other field already matched.
    #[must_use]
    pub fn found_host_band() -> Option<String> {
        with_sweep(|slot| {
            let sweep = slot.as_ref()?;
            sweep.found?;
            sweep
                .lobbies
                .iter()
                .map(|lobby| lobby.band.clone())
                .find(|band| er_invasion_warp_core::band_ladder::looks_like_band(band))
        })
    }

    /// Whether any of these lobbies still advertises as available.
    ///
    /// `true` when the answer cannot be read at all -- no interface, no exports -- because failing
    /// to ask is not evidence that a host has gone. Dropping a good hit because Steam was briefly
    /// unreachable would cost the search the one host it had found.
    #[must_use]
    pub fn host_is_still_advertising(lobbies: &[u64]) -> bool {
        if lobbies.is_empty() {
            return true;
        }
        let Some(iface) = matchmaking() else {
            return true;
        };
        let Some(get_data) = export(GET_LOBBY_DATA) else {
            return true;
        };
        // SAFETY: an export resolved by name from a module that is loaded.
        let read = unsafe { core::mem::transmute::<usize, GetLobbyDataFn>(get_data) };
        lobbies
            .iter()
            .any(|id| lobby_value(iface, read, *id, AVAILABLE_KEY) == "true")
    }

    #[must_use]
    pub fn fetch_lobbies(matching: u32) -> Vec<FoundLobby> {
        let Some(iface) = matchmaking() else {
            return Vec::new();
        };
        let (Some(by_index), Some(get_data)) = (export(GET_LOBBY_BY_INDEX), export(GET_LOBBY_DATA))
        else {
            return Vec::new();
        };
        // SAFETY: two exports resolved by name from a module that is loaded.
        let by_index = unsafe { core::mem::transmute::<usize, GetLobbyByIndexFn>(by_index) };
        // SAFETY: as above.
        let read = unsafe { core::mem::transmute::<usize, GetLobbyDataFn>(get_data) };

        let mut found = Vec::new();
        for index in 0..matching.min(RESULT_COUNT.unsigned_abs()) {
            let Ok(index) = i32::try_from(index) else {
                break;
            };
            // SAFETY: the index is below the count Steam reported for this very query.
            let id = unsafe { by_index(iface, index) };
            if id == 0 {
                continue;
            }
            found.push(FoundLobby {
                id,
                map: lobby_value(iface, read, id, crate::lobby_publish::LOBBY_MAP_KEY),
                effects: lobby_value(
                    iface,
                    read,
                    id,
                    crate::lobby_publish::LOBBY_HOST_EFFECTS_KEY,
                ),
                lobby_key: lobby_value(iface, read, id, crate::lobby_publish::LOBBY_KEY_NAME),
                band: lobby_value(iface, read, id, SEAMLESS_BAND_KEY),
            });
        }
        found
    }

    /// Say whether the hosts the sweep found are in a pool this player's search can reach.
    ///
    /// Joining them is not the answer, and that was measured the hard way: entering a host's
    /// advertisement lobby by hand put the player in her world as a co-op guest, not as an
    /// invader. A bare `JoinLobby` is a co-op arrival, so the join Seamless never makes was never
    /// a step this mod should make on its behalf. The proven join chain lives in
    /// `scripts/frida/hand-lobby-to-seamless.js`, where an unproven mechanism belongs.
    ///
    /// What a non-member can read is enough. `GetLobbyData` needs no membership -- proven by
    /// enumerating all nine of a live host's published keys from outside her lobby -- so her
    /// `lobby_key` is in hand for free, and that key decides whether any search can return her.
    /// Seamless compares it for equality, so a host whose key differs is invisible to every query
    /// this game sends, however close she is standing and however many of this mod's keys she
    /// publishes.
    ///
    /// Our own side of the comparison costs nothing either. `lobby_publish::seamless_match_key`
    /// already answers it, from Seamless's key recorded on the way past either hook and from the
    /// value live on the advertisement lobby, with this player's pool transform applied -- which is
    /// precisely the string a query of ours filters on. No observer to enable, no config to opt
    /// into.
    fn report_found_lobbies() {
        let lobbies = with_sweep(|slot| {
            slot.as_ref()
                .map(|sweep| sweep.lobbies.clone())
                .unwrap_or_default()
        });
        if lobbies.is_empty() {
            crate::standalone_log(format_args!(
                "sweep: the place answered with a host but no lobby came back from \
                 `GetLobbyByIndex`, so there is nothing to read. The count and the entries \
                 disagreeing means the query was superseded between the two calls."
            ));
            return;
        }
        let ours = crate::lobby_publish::seamless_match_key();
        for lobby in lobbies {
            match ours.as_deref() {
                Some(mine) if mine == lobby.lobby_key => {
                    crate::standalone_log(format_args!(
                        "sweep: lobby {} is in this player's pool -- map={} effects={} \
                         lobby_key={}. The search can return this host, so a failure to connect \
                         from here is not a matchmaking-pool failure.",
                        lobby.id,
                        lobby.map,
                        lobby.effects,
                        head(&lobby.lobby_key)
                    ));
                }
                Some(mine) => {
                    crate::standalone_log(format_args!(
                        "sweep: lobby {} is unreachable -- its `lobby_key` is {} and this game \
                         searches for {}. Seamless compares that key for equality, so this host \
                         answers the sweep's count and can never answer the search that follows. \
                         The two players are running different mod sets; nothing on this side \
                         fixes it.",
                        lobby.id,
                        head(&lobby.lobby_key),
                        head(mine)
                    ));
                }
                None => {
                    crate::standalone_log(format_args!(
                        "sweep: lobby {} publishes `lobby_key` {}, and this game has not written \
                         one of its own yet, so the pools cannot be compared. Seamless computes \
                         its key when a search starts; before that there is nothing recorded to \
                         compare against.",
                        lobby.id,
                        head(&lobby.lobby_key)
                    ));
                }
            }
        }
    }

    /// The readable head of a 64-character key, for a log line carrying two of them.
    fn head(key: &str) -> &str {
        &key[..key.len().min(12)]
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
        //
        // The same one-in-flight rule binds the sweep against Seamless's own query, and that is
        // what `session_is_idle` below is for. Nothing held the sweep off Seamless: on run
        // `br-20260918-000408-dc85` the sweep armed for 49 places at log line 149 and kept asking
        // straight through Seamless's search, and by the measurement above the second request is
        // the one that wins.
        //
        // Every observation of a failed invasion fits that one cause. Measured on
        // `br-20260918-000408-dc85`, with the host reachable throughout:
        //
        // ```text
        // her lobby, on the exact query Seamless sends   matching=1  (er-lobby-search-proof.py)
        // Seamless calls to GetLobbyData / JoinLobby     0           (invasion-connect-probe.js)
        // outbound P2P packets                          0
        // cycles                                        0x0e -> 0x0f -> 0x12 -> 15s -> 0x0e
        // ```
        //
        // A lobby list that never reaches Seamless's handler produces exactly that: nothing to
        // read, nobody to dial, and a fifteen-second timeout it retries forever.
        //
        // This guard was removed once, on the grounds that run `br-20260917-235507-1c27` had it
        // installed and failed anyway. That run cannot carry the argument: its query was narrowed
        // to a tile whose own pre-flight had already answered zero, so no invasion could have
        // landed there whatever the sweep did. The guard and an unnarrowed query have never run
        // together, and together is what this is.
        //
        // The cost the removal was buying back is paid elsewhere instead: the sweep walks its ring
        // only while nothing is searching, which is ordinary play before the finger is ever used.
        // A neighbourhood mapped in advance is what the found-a-host banner then names.
        let stage = STAGE.load(Ordering::SeqCst);
        if stage != STAGE_WANTED
            && stage != STAGE_SENT
            && crate::local_invasion_filter::session_is_idle()
        {
            walk_the_ring_while_nothing_is_searching();
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
        // Seamless's own match key, so this asks about the population its search can actually
        // return.
        //
        // Without it the two queries disagree by construction. `lobby_key` is a SHA-256 over the
        // loaded param tables and Seamless filters its invasion search on it with
        // `k_ELobbyComparisonEqual`, so a host whose mods differ is in another pool and is
        // unreachable no matter what else matches. This query carried no such filter, so it counted
        // the whole Seamless population and answered "somebody is publishing this location" about
        // hosts the search that follows can never return -- and the player was told "Found a host
        // in X -- invading" about a world their game cannot reach. Reported by the user on
        // 2026-09-17 after thirty-two such matches, none of which ever connected.
        //
        // `None` before Seamless hands the key to Steam, and then this filter is simply absent
        // rather than blank: an empty value asks for a pool nobody is in, which would turn "we
        // have not seen the key yet" into "nobody is anywhere".
        //
        // The key comes from `lobby_publish`, which reads it off Steam, rather than from an
        // observer on Seamless's own builder: detouring `ersc+0xad6e0` killed run
        // `br-20260917-222254-be6f` 33 seconds in with `STATUS_ILLEGAL_INSTRUCTION` at
        // `eldenring.exe+0x10043`.
        if let Some(key) = crate::lobby_publish::seamless_match_key() {
            terminated.push((
                format!("{}\0", crate::lobby_publish::LOBBY_KEY_NAME).into_bytes(),
                format!("{key}\0").into_bytes(),
                COMPARISON_EQUAL,
            ));
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
        //
        // And it is not dropped for `Nearby only`, which is the third place that row has to be
        // carved out of this short-circuit. Dropping the ring hands over to the far half; that row
        // has no far half, so the hand-over is a hand-over to nothing and the player watches a
        // banner with no places in it. User, live on run br-20260917-193816-8621: "Search should
        // loop forever, but only on the region's block ids, if I invade nearby." Keeping the ring
        // armed is what keeps the rotation cycling the region, and a host who arms this mod later
        // in the session is then found on a subsequent pass rather than never.
        if matching == 0
            && !matches!(nearby(), Nearby::Idle)
            && !crate::local_invasion_filter::finger_reach_is_nearby_only()
        {
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
        /// The lobbies that place's query actually named, keys and all.
        ///
        /// The block above says where; this says who, and until it existed the answer to "who" was
        /// thrown away with the rest of the `LobbyMatchList_t` the moment its count was read.
        lobbies: Vec<FoundLobby>,
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
        // An identical ring keeps the walk it has already done instead of starting it over.
        //
        // This is what lets the near half of `Both near and far` ever finish. `tick` walks the
        // ring only while the Seamless session is idle, and using an invasion finger arms the
        // sweep and starts the search on the same frame, so a plain replace threw away whatever
        // the idle ticks had measured and re-armed a 49-place ring into a session that would
        // never be idle again. `next` then stayed at 0, `nearby` stayed `Asking`, and the
        // `Nearby::Empty` branch in `lobby_publish::hunt_target` -- the only thing that drops the
        // location filter for that row -- was unreachable code.
        //
        // Measured on run `br-20260918-020159-9b03`: `sweep: armed for 49 nearby place(s)` and
        // then not one place answered across twenty-three search cycles.
        // A sweep that has already found a host is not re-armed for any ring, identical or not.
        //
        // The identical-ring guard below is not enough, and the gap cost the whole of 2026-09-18.
        // Measured on run `br-20260918-225557-5fc3`: the sweep found the host --
        // `sweep: a host is in m32_02_00_00 -- the search points there and stops widening` -- and
        // then the legacy table became readable, the ring was rebuilt from 3 places to 11, the
        // rings compared unequal, and the answer was replaced by a fresh 11-place walk. Every query
        // after that asked an overworld tile (`m60_36_47_00`, `m60_38_47_00`, ...) while the host
        // published `m32_02_00_00`, so the one host the sweep had positively identified became
        // unreachable by the search that identified him.
        //
        // A hit outranks a ring because of what each one is. The ring is a guess about where a host
        // might be; the hit is Steam's own answer that a host occupies a named block. Discarding the
        // second because the first grew is backwards, and the band ladder already owns the case
        // where a hit goes stale -- `failed_cycle::advance_place_on_failed_cycle` calls
        // `clear_sweep` after `CYCLES_BEFORE_THE_SWEEP_IS_STALE` cycles fail against it.
        // A hit is only worth keeping while the host it names is still advertising. Re-read the
        // lobby rather than trusting the answer: a host who closes their world leaves the lobby in
        // place with `91489e05... = false`, so the id stays readable and the hit stays plausible
        // while being useless. Measured 2026-09-18 on lobby `109775241925634276`, which went from
        // `available = true` to `false` while this sweep still pointed the search at it, and every
        // query kept naming a block whose host could no longer be returned by any query at all.
        let found = with_sweep(|slot| {
            let sweep = slot.as_ref()?;
            let block = sweep.found?;
            Some((
                block,
                sweep
                    .lobbies
                    .iter()
                    .map(|lobby| lobby.id)
                    .collect::<Vec<_>>(),
            ))
        });
        let found = match found {
            Some((block, lobbies)) if host_is_still_advertising(&lobbies) => Some(block),
            Some((block, _)) => {
                crate::standalone_log(format_args!(
                    "sweep: the host this search was pointed at, in {}, no longer advertises as \
                     available, so the hit is dropped and the {total}-place ring is armed. A closed \
                     world leaves its lobby readable with the availability flag cleared, which is \
                     why the id being resolvable is not evidence that anybody is in it.",
                    crate::lobby_publish::map_value(
                        er_invasion_warp_core::invasion_warp::BlockKey::from_raw(block)
                    )
                ));
                clear_sweep();
                None
            }
            None => None,
        };
        if let Some(block) = found {
            crate::standalone_log(format_args!(
                "sweep: a host is already known to be in {} and the search is pointed there, so \
                 this {total}-place ring is not armed over the top of it. Rebuilding the ring used \
                 to replace the answer, and every query after that asked a tile the host was not \
                 in. A hit that goes stale is dropped by the failed-cycle counter, not by a ring \
                 that happened to grow.",
                crate::lobby_publish::map_value(
                    er_invasion_warp_core::invasion_warp::BlockKey::from_raw(block)
                )
            ));
            return;
        }
        let reused = with_sweep(|slot| match slot.as_ref() {
            Some(sweep) if sweep.blocks == blocks => Some((sweep.answered, sweep.finished)),
            _ => None,
        });
        if let Some((answered, finished)) = reused {
            crate::standalone_log(format_args!(
                "sweep: the ring of {total} nearby place(s) is already the one being walked -- \
                 {answered} answered, finished={finished} -- so those answers are kept rather \
                 than asked again from the start. Re-arming here is what used to delete the walk \
                 the idle ticks had done."
            ));
            return;
        }
        let generation = SWEEP_GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
        with_sweep(|slot| {
            *slot = Some(Sweep {
                generation,
                blocks: blocks.to_vec(),
                next: 0,
                call: None,
                found: None,
                lobbies: Vec::new(),
                answered: 0,
                finished: total == 0,
            });
        });
        crate::standalone_log(format_args!(
            "sweep: armed for {total} nearby place(s) -- one lobby query each, asking which of \
             them has a host in it. Nothing is published and no game state is touched."
        ));
    }

    /// The tile the idle walk last drew a ring around, or [`NO_CENTRE`] if it has not run.
    static PREWALK_CENTRE: AtomicU32 = AtomicU32::new(NO_CENTRE);

    /// Whether the ring now being walked was built with the world map's legacy table in hand.
    ///
    /// False while no world map exists yet, which is when the first ring of a session is built.
    /// The walk re-arms on a block change alone, so without this a dungeon entered during that
    /// window keeps its one-block ring for as long as the player stays in it.
    static PREWALK_USED_LEGACY_TABLE: AtomicBool = AtomicBool::new(false);

    /// Not a block id. Block ids are packed `mAA_BB_CC_DD` bytes, so the all-ones word is one no
    /// map can produce, and it is distinct from the zero that `current_block` uses for unreadable.
    const NO_CENTRE: u32 = u32::MAX;

    /// Keep a ring armed around wherever the player is standing, while nothing is searching.
    ///
    /// The near half of `Both near and far` asks Steam one query per nearby place, and a query
    /// issued while Seamless is searching does not come back -- only one `RequestLobbyList` can be
    /// in flight on the interface, and the one Seamless sends is the one that wins. That is why
    /// `sweep_tick` above is gated on the session being idle, and why arming the ring at the
    /// moment an invasion finger is used could never work: the finger starts the search on the
    /// same frame, so the ring was handed a session that would not be idle again.
    ///
    /// Walking it during ordinary play is the version of this that can finish. The module header
    /// has described it that way since the gate went in -- "the sweep walks its ring only while
    /// nothing is searching, which is ordinary play before the finger is ever used" -- and nothing
    /// implemented it, so the ring was armed at the one moment it could not move. By the time the
    /// item is used the neighbourhood is mapped, `nearby` already answers `Found` or `Empty`, and
    /// `lobby_publish::hunt_target` picks the near half or the far half on the first query.
    ///
    /// Re-armed only when the centre tile changes, so a player standing still costs one atomic
    /// load per tick and a player walking re-maps around wherever they arrive. [`arm_sweep`] keeps
    /// an identical ring's answers, so the finger's own call is a no-op on a walk already done.
    fn walk_the_ring_while_nothing_is_searching() {
        let Some(here) = crate::lobby_publish::current_block() else {
            return;
        };
        let here = here.raw();
        // A ring built before the world map existed is a ring built without the legacy table, and
        // in a legacy dungeon that is the difference between a neighbourhood and one block. The
        // walk re-arms on a block change and nothing else, so that first ring would stand for as
        // long as the player stayed put -- which is exactly the case a dungeon search is. Once the
        // table is readable the ring is rebuilt for the block already being walked.
        //
        // `authoritative_view_model` is live for the whole of a loaded world rather than only
        // while the map is open, so this waits on the load, not on the player opening anything.
        let table_now = !crate::map_hooks::legacy_regions_for_search().is_empty();
        let table_before = PREWALK_USED_LEGACY_TABLE.swap(table_now, Ordering::SeqCst);
        let table_arrived = table_now && !table_before;
        if PREWALK_CENTRE.swap(here, Ordering::SeqCst) == here && !table_arrived {
            return;
        }
        let radius = crate::local_invasion_filter::current_config_snapshot()
            .map_or(1, |config| config.prefilter_radius);
        let ring = crate::local_invasion_filter::search_banner::nearby_ring(here, radius);
        if table_arrived {
            crate::standalone_log(format_args!(
                "sweep: the world map's legacy table became readable, so the ring around the block \
                 already being walked is rebuilt -- {} place(s) now. Built before the world map \
                 exists, a ring in a legacy dungeon is the one block the player stands in, and the \
                 walk only re-arms when they move.",
                ring.len()
            ));
        }
        arm_sweep(&ring);
    }

    /// Forget the sweep, for a search that has been stood down.
    ///
    /// Nothing else to give back: the sweep reads lobbies and never enters one, so a stood-down
    /// search leaves no membership behind in anybody's lobby.
    pub fn clear_sweep() {
        with_sweep(|slot| *slot = None);
        // Let the idle walk map the neighbourhood again. Without this the centre still matches the
        // tile the player is standing in, so the walk declines to re-arm and the ring the search
        // just discarded is never rebuilt -- a second use of the item would then find `nearby`
        // answering `Idle` with nothing behind it.
        PREWALK_CENTRE.store(NO_CENTRE, Ordering::SeqCst);
    }

    /// Forget the sweep and the band it was climbing together, for a search that is over rather than
    /// widening.
    ///
    /// Kept apart from [`clear_sweep`], which the band ladder itself calls between rungs: a rung
    /// must survive the ring being re-armed under it, and must not survive the search ending.
    pub fn end_search() {
        clear_sweep();
        crate::lobby_publish::reset_band();
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
                        // The count is no longer the end of it. Until now a positive count set a
                        // block and nothing ever held the lobby the banner was about: measured on
                        // run `br-20260918-002055-bcc4`, `GetLobbyByIndex` was called zero times
                        // in 46 search cycles while a host sat reachable throughout.
                        sweep.lobbies = fetch_lobbies(matching);
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
                // Only a search somebody started may say so on screen.
                //
                // This walk runs during ordinary play -- that is the whole design, so the
                // neighbourhood is mapped before the item is ever used -- and it painted "Found a
                // host -- asking Seamless to join <place>" the moment the player walked into a
                // block somebody was hosting in. Measured on run `br-20260918-192951-e31c`: the
                // notice went up at log line 194, directly after map injection, and the first
                // `the bounds popup chose NearbyOnly` is line 866. The player's words: it "pops up
                // when I go into a location where someone is hosting before I even touch the
                // recusant finger".
                //
                // Both halves of that sentence were false there. Nothing was asking Seamless
                // anything, because no search was armed; and the sweep asks Steam for a count and
                // keeps the lobbies for a search that may never be started. What the walk found is
                // still worth the log line below -- it is the answer `hunt_target` reads when a
                // search does begin -- so the finding is kept and only the claim is withdrawn.
                let a_search_asked_for_this = crate::local_invasion_filter::finger_reach()
                    != crate::local_invasion_filter::FINGER_REACH_NONE;
                // `enabled` is the player's reject-notice option, like every other line the search
                // paints. It was a literal `true` here, so this one banner ignored the setting.
                let notice = crate::local_invasion_filter::current_config_snapshot()
                    .is_none_or(|config| config.reject_notice);
                if a_search_asked_for_this {
                    crate::local_invasion_filter::banner::announce_found_host(notice, block);
                }
                crate::standalone_log(format_args!(
                    "sweep: a host is in {} -- the search points there and stops widening; the \
                     place queue is cleared so the banner names the answer instead of the places \
                     it is no longer asking about.",
                    crate::lobby_publish::map_value(
                        er_invasion_warp_core::invasion_warp::BlockKey::from_raw(block)
                    )
                ));
                report_found_lobbies();
            }
            Outcome::Empty(asked) => {
                crate::standalone_log(format_args!(
                    "sweep: all {asked} nearby place(s) answered zero, so the neighbourhood is \
                     measurably empty. `Both near and far` drops the location filter from here \
                     and the query goes out exactly as Seamless builds it; `Nearby only` keeps \
                     asking, because staying near is what that row is for."
                ));
                // `Nearby only` has one more thing to try before it gives up on the neighbourhood:
                // the same ring, one matchmaking band higher. An empty ring at this player's band
                // is not evidence that nobody is nearby -- Seamless matches its band value for
                // equality, so a host one weapon-upgrade band away produces exactly this zero.
                //
                // Not for `Both near and far`: that row's next rung is dropping the location
                // filter, taken directly below, and climbing a band at the same moment would widen
                // place and band together with no way to attribute a later hit to either.
                // The band ladder does not climb here, deliberately. This branch cannot be reached
                // during a live search: `sweep_tick` runs only while the Seamless session is idle,
                // so the ring freezes at place 1 and `Outcome::Empty` never arrives. Measured on
                // `br-20260918-015127-6a6f`, where the ladder was wired here and never once fired.
                // `local_invasion_filter::climb_band_on_failed_cycle` owns the climb instead,
                // counting the search cycles that actually happen.
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
