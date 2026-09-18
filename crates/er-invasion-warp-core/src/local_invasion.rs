//! Which invasions are allowed to land, and where.
//!
//! # What this encodes
//!
//! Seamless decides the destination server-side and pushes it to the client in a
//! `ServerPushJoinData`; the block id is at `+0x00` of that struct, and it is written to
//! `GameMan+0xAC8` by `CS::SosSignMan::SetMultiplayJoinData` (`0x1406FB520`). Measured live
//! 2026-08-05: `+0x00` was the only offset in all 128 bytes whose u32 equalled the destination, so
//! the field is identified by correlation, not by the reversed field name (`matchPlayerCount`,
//! which would have pointed elsewhere).
//!
//! That is the whole basis for a location filter: at the moment `SetMultiplayJoinData` runs, the
//! destination is decided and the player has not moved. A predicate there can accept or reject
//! with certainty. Rejecting means cancelling the match, which was measured non-destructive -- the
//! session walks `0x22 -> 0x00` and a fresh search works afterwards.
//!
//! It also encodes a negative result, so nobody re-attempts it: the pre-match candidate table does
//! not predict the destination. Its block-shaped fields were the local character's own map
//! (`0x1c000000`, matching this save's `c30`) while the match that followed went to `0x3c353800`.
//! Filtering before connecting is therefore not available, and accept-then-reject is the shape
//! that works.
//!
//! # The anchor
//!
//! Every decision is relative to where the player is. There is always a nearest invasion map point
//! for the player's current position -- that is what `nearest_place_name_text_id` resolves against
//! the shipped warp rows, same-area only. The anchor carries the block and the place-name text ids
//! valid at that location, because a single warp destination can sit under more than one place
//! name: one name means one acceptable location, five names mean five.
//!
//! # Why the modes are shaped this way
//!
//! [`LocalInvasionMode::ExactOnly`] is the strictest useful thing -- the exact block the player
//! anchored to. [`LocalInvasionMode::PreferExactThenArea`] widens to the anchor's own place names
//! when the exact block is not on offer, which is the difference between "this tile" and
//! "somewhere in the Haligtree". [`LocalInvasionMode::NamedOnly`] ignores the anchor entirely and
//! honours a user-supplied list, for hunting a place you are not standing in.
//!
//! Disabled is the default. This filter cancels real matches, and a mod that silently rejects
//! other players' invasions because a config file appeared is worse than one that does nothing
//! until asked.

use std::collections::BTreeSet;

/// A `PlaceName` FMG text id. `-1` is "no name", and it is not a wildcard.
pub type PlaceNameTextId = i32;

/// The "no name" sentinel. `nearest_place_name_text_id` returns this when nothing resolves, and it
/// must never be treated as matching anything -- an unnamed candidate is not "everywhere".
pub const PLACE_NAME_NONE: PlaceNameTextId = -1;

/// Where the player is, and what that location is called.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InvasionAnchor {
    /// The player's current block id.
    pub block: u32,
    /// Place-name text ids valid at the anchor. Usually one; a location that sits under several
    /// names carries several, and each is an acceptable destination name in area mode.
    pub place_names: BTreeSet<PlaceNameTextId>,
}

impl InvasionAnchor {
    /// Build an anchor from a block and the place names resolved at it, dropping
    /// [`PLACE_NAME_NONE`] -- an unresolved name is not a name to match on.
    #[must_use]
    pub fn new(block: u32, place_names: impl IntoIterator<Item = PlaceNameTextId>) -> Self {
        Self {
            block,
            place_names: place_names
                .into_iter()
                .filter(|id| *id != PLACE_NAME_NONE)
                .collect(),
        }
    }

    /// How many distinct named locations this anchor makes acceptable in area mode. One name means
    /// one place to look, five names mean five.
    #[must_use]
    pub fn named_location_count(&self) -> usize {
        self.place_names.len()
    }
}

/// Why a candidate was rejected.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RejectReason {
    /// Not a rejection at all: the player asked for the search to stop, with the filter's switch
    /// or the settings panel. Carried through the same cancel path because the action is the same
    /// one -- ersc's own Cancel row -- and the reason is what the log line says happened.
    PlayerStopped,
    /// Not the anchor's block, in a mode that requires it.
    WrongBlock,
    /// Not one of the anchor's place names.
    WrongPlaceName,
    /// Not on the configured list.
    NotNamed,
    /// The candidate has no resolvable place name, in a mode that judges by name. Rejected rather
    /// than waved through: an unnamed destination is unknown, not universal.
    CandidateUnnamed,
    /// Name-based judging with nothing to judge against -- an anchor with no names, or an empty
    /// list. Fails closed, because the alternative is accepting everything while appearing to
    /// filter.
    NothingToMatchAgainst,
    /// The user excluded this exact location with Delete.
    ExcludedByUser,
}

/// The filter's configuration, as loaded from TOML.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalInvasionConfig {
    /// Master switch. Off by default.
    pub enabled: bool,
    /// Hunt MODE: narrow the outgoing lobby query to one location instead of rejecting answers.
    ///
    /// Off by default and separate from `enabled` because the two do opposite things to your reach.
    /// The reject filter declines matches and still sees every host; hunt asks Steam for a key only
    /// this DLL's users publish, so while it is on a host without the DLL is invisible to you. That
    /// is a trade the user must choose, never a default.
    pub hunt: bool,
    /// How many rings of neighbouring tiles the prefilter may widen to, when it finds nobody.
    ///
    /// `0` keeps today's behaviour exactly: ask for one tile and stop. Above zero the search asks
    /// for the player's own tile first and then walks outward a ring at a time, one tile per query
    /// round, because a Steam string filter is equality on a single value and several filters
    /// `and` together -- so widening is something that happens across rounds, never within one.
    ///
    /// Capped by [`crate::search_ring::MAX_RADIUS`]. Three rings is 48 neighbours, which at one
    /// tile per round is already a long search; the cap exists so a mistyped radius cannot become
    /// a rotation nobody can sit through.
    pub prefilter_radius: u8,
    /// After every tile in the ring has been asked for and answered nothing, drop the filter.
    ///
    /// Off by default, because it is a different bargain rather than more of the same one: with no
    /// filter the query returns the whole population again, vanilla hosts included, and where you
    /// land is then decided by the reject filter rather than by Steam. It is the rung a player
    /// takes deliberately when they would rather invade somewhere than nowhere.
    pub search_everywhere_when_exhausted: bool,
    /// When the nearby ring is exhausted, search one matchmaking band higher, and keep climbing.
    ///
    /// On by default, which is unusual in this struct and is the point: without it two friends one
    /// weapon-upgrade band apart cannot meet, and neither of them can tell why. Seamless publishes
    /// a `<level band>_<weapon band>` pair and filters it for equality, so a host one band away is
    /// not merely harder to reach -- she is indistinguishable from nobody being online. Measured
    /// 2026-09-17 on run `br-20260918-005917-483b`: a host publishing `2_2` answered zero of six
    /// searches from a client asking `2_1`, and answered the very next search that asked `2_2`.
    ///
    /// The climb starts only once the ring at the player's own band has been asked and answered
    /// nothing, so a same-band host is always preferred. Then the weapon band steps up one at a
    /// time, and when it runs out the level band takes a step and the weapon band starts again.
    ///
    /// Upward only, deliberately. Invading above your own band is a disadvantage the invader
    /// accepts to find a fight at all; dragging the search downward would put them on somebody
    /// weaker who never opted into that.
    ///
    /// `Nearby only` alone. `Both near and far` already has a wider rung to take -- it drops the
    /// location filter and asks the whole population at the player's own band -- so climbing bands
    /// there would widen two axes at once and nobody could say which one found the host.
    pub widen_band_when_nearby_exhausted: bool,
    /// Match only other players running this DLL with this option on.
    ///
    /// Rewrites Seamless's `lobby_key` into a pool of our own (see
    /// [`crate::lobby_pool`]). One value drives both the search filter and the publish, so the
    /// separation is symmetric: vanilla players cannot see you and you cannot see them, for
    /// hosting as much as for invading.
    ///
    /// Absolute, not a preference. While it is on the entire vanilla population is invisible in
    /// both directions, which is why it is off by default and wants to be a per-session choice.
    pub dll_users_only: bool,
    /// Announce a rejection on the game's own system-message banner.
    ///
    /// Off by default: it is a notification, and a notification nobody asked for is spam. When on,
    /// only a change of wrong destination is announced -- consecutive rejections at the same place
    /// stay silent, because Seamless retries roughly every 20 seconds and the same wrong place
    /// recurs constantly.
    pub reject_notice: bool,
    /// Inject invasion pins into the world map.
    ///
    /// On by default -- the pins are the feature. It is configurable because the map path is the
    /// crate's largest interaction with live engine memory (it appends 467 rows into the
    /// `WorldMapViewModel`'s row buffer from that object's own constructor), and until this key
    /// existed there was no way to run the DLL without that surgery. That made a whole class of
    /// question unanswerable: a fault that follows opening the map could not be attributed,
    /// because the only A/B available was "this DLL or no DLL", which changes nine other things
    /// at the same time.
    ///
    /// Turning it off withholds the `WorldMapViewModel` constructor observer and the world-map
    /// GFx hook. Everything else -- the local-invasion filter, the warp keys, the lobby pool --
    /// is untouched, so the two halves of the A/B differ by the map path and nothing else.
    pub map_pins: bool,
    /// Install the three Steam-matchmaking detours.
    ///
    /// On by default -- location publishing, hunt mode and the pool filter all need them. It is
    /// configurable for the same reason as [`Self::map_pins`]: these are the only detours this
    /// crate installs at addresses it did not derive statically. Each is read out of a live
    /// `ISteamMatchmaking` vtable slot at runtime and handed straight to MinHook, and the
    /// installers retry every tick until a read succeeds, so whatever the slot happens to hold at
    /// that instant becomes a five-byte patch target inside `steamclient64.dll`.
    ///
    /// Turning it off withholds `install_advertisement_observer`, `install_hunt_hook` and
    /// `install_pool_filter_hook` and nothing else, which makes that patching isolable from
    /// everything else the DLL does.
    pub steam_hooks: bool,
    /// Install the two read-only observers on Seamless Co-op's own code.
    ///
    /// On by default -- the `show` observer is the only way this DLL learns the Seamless menu
    /// object's address, and the lobby-key observer reports the one string that decides whether
    /// two Seamless players can see each other at all.
    ///
    /// It is configurable because these two are the only always-on detours this crate places
    /// inside `ersc.dll`, and they were the only always-on hooks on the stack of the
    /// `0x140010043` illegal-instruction crash: the fault's frames read
    /// `ersc.dll -> er_invasion_warp -> ersc.dll -> lsteamclient.dll`, and it did not reproduce at
    /// all in a run with this DLL excluded while the player completed a whole invasion. `map_pins`
    /// and `steam_hooks` have each already been A/B'd off with the crash still present, so this is
    /// what is left to isolate.
    ///
    /// Worth knowing while that A/B is open: both are installed through
    /// `er_hook::register_union_hook`, the entry point for a 1.16.2 game constant, even though
    /// their addresses are derived from the running `ersc.dll`. That path audits the destination
    /// by translating it through a table keyed by eldenring.exe RVAs, which has nothing to say
    /// about a module based at 0x180000000 -- so unlike every runtime-derived hook in this crate,
    /// these two never face `detour_site::write_site_is_sound`. The only thing standing behind
    /// them is `prologue_matches` against the pinned build's recorded bytes.
    ///
    /// Turning it off withholds `install_show_observer` and `install_lobby_key_observer` and
    /// nothing else; the local-invasion filter still judges matches, and the warp keys still work.
    pub ersc_observers: bool,
    /// Install the `show` observer specifically, when [`Self::ersc_observers`] is on.
    ///
    /// The master switch proved the pair is what crashes; these two split that pair so the next
    /// run names which one. Both default on, so turning the master on restores the previous
    /// behaviour exactly. Both patch sites were checked statically against
    /// `vendor-archive/seamless/ersc-2.0.1.dll` and are mechanically sound -- MinHook's five bytes
    /// land exactly on `push rbp; push r15; push r14`, a clean instruction boundary with nothing
    /// to relocate -- so whatever goes wrong is in the detour's semantics, not the patch.
    pub ersc_show_observer: bool,
    /// Install the lobby-key observer specifically, when [`Self::ersc_observers`] is on.
    ///
    /// See [`Self::ersc_show_observer`]. This is the one whose target Seamless documents as
    /// `SHA256_hex(AES_decrypt(ctx[0xB8]) ++ ...)` -- worth noting only because the crash lands
    /// inside AES-NI code, which is suggestive and not evidence.
    pub ersc_lobby_key_observer: bool,
    /// Install the invade-action observer specifically, when [`Self::ersc_observers`] is on.
    ///
    /// It has its own key rather than riding [`Self::ersc_show_observer`] because the two answer
    /// different questions and the pair is what the master switch was turned off for. This one is
    /// the only observer that fires when the player invades with an ITEM: `show` runs only when
    /// Seamless's own option menu is built, and the item path never builds it. Measured in run
    /// `br-20260908-230004-d163`: 13 matches judged and rejected, every one of them
    /// `NOT cancelled`, with zero `captured Seamless's option-menu object` lines in the log.
    ///
    /// A Frida `Interceptor` sat on this exact address for that entire run -- dozens of invades,
    /// no crash -- which says an inline hook here is survivable. It does not say MinHook's is:
    /// different patcher, different install mechanics. Turning this on alone is the smallest
    /// experiment that can tell the two apart.
    pub ersc_invade_observer: bool,
    /// Virtual-key code that marks the location the player is standing in.
    ///
    /// Configurable because the historical default `VK_INSERT` does not exist on a 60% keyboard,
    /// which locked the whole marking feature out for anyone using one.
    pub mark_key: crate::keybind::VirtualKey,
    /// Virtual-key code that un-marks it.
    pub unmark_key: crate::keybind::VirtualKey,
    /// Virtual-key code that flips [`Self::enabled`] and writes the file.
    ///
    /// The filter had no switch a player could reach mid-session: the only way to stop
    /// rejecting was to alt-tab and hand-edit the TOML, and the reason to want it is
    /// immediate -- a player who has been hunting one location decides to take whatever
    /// comes next, and by the time the file is saved the moment has passed.
    ///
    /// It writes the file rather than holding the answer in memory, so the switch survives
    /// a restart and so a player reading the config later sees the state they are actually
    /// playing in.
    pub enable_toggle_key: crate::keybind::VirtualKey,
    /// Virtual-key code for opening and closing the in-game settings panel.
    ///
    /// The panel is a second front end onto this file, not a replacement for it: it shows every
    /// key that is a setting rather than a hook diagnostic, writes each change straight back here,
    /// and re-reads the file so a hand edit made while it is open still wins. Bound by name like
    /// every other key, and for the same reason -- a hard-coded default is a key another mod in
    /// the profile may already own.
    pub settings_key: crate::keybind::VirtualKey,
    /// Exact blocks the user marked with Insert. One mark is what the location search aims at.
    pub allowed_blocks: BTreeSet<u32>,
    /// Exact blocks the user excluded with Delete. An exclusion beats a mark: it stops the
    /// location search from asking for that place even when it is the one marked.
    ///
    /// This is the third state the world map needs. `allowed` and "not allowed" are only two, and
    /// deriving the third from a judgement against the player's current position made the map
    /// answer a question it should not: standing in a hub, every destination read as a rejection,
    /// so the whole map rendered one uniform tier and conveyed nothing. Chosen / untouched /
    /// excluded are properties of a location, so they read the same from anywhere.
    pub blocked_blocks: BTreeSet<u32>,
}

impl Default for LocalInvasionConfig {
    fn default() -> Self {
        Self {
            // Off. See the module docs: this cancels real matches, so it must be asked for.
            enabled: false,
            // Off, and for a different reason than `enabled`. Hunt narrows the query to a key only
            // this DLL's users publish, so while it is on a host without the DLL cannot be seen at
            // all. Losing reach is not something to inherit from a default.
            hunt: false,
            prefilter_radius: 0,
            search_everywhere_when_exhausted: false,
            // On, and the only reach-widening default in this struct. The others trade away who
            // you can see; this one only adds hosts, upward, after the player's own band has been
            // asked and come back empty. Left off, the mod ships with a failure mode that reads as
            // "nobody is online" while a friend one weapon-upgrade band away hosts an open world.
            widen_band_when_nearby_exhausted: true,
            // OFF: it hides the entire vanilla population in both directions.
            dll_users_only: false,
            // OFF: a notification nobody asked for is spam.
            reject_notice: false,
            // ON: the pins are the point of the world-map half of this DLL.
            map_pins: true,
            // ON: location publishing and hunt mode both need these detours.
            steam_hooks: true,
            // Off by default, on measured evidence rather than caution. These two detours are the
            // only thing this DLL writes into `ersc.dll`, and arming them kills the game in ~25s.
            // Measured 2026-09-04 on the autoload route with everything else already ON: with them
            // off the process ran 251s and 110s with zero fault records across two arms; with them
            // on and nothing else changed it died at 24.9s with a fault at 0x140010043, no input
            // given. Five earlier faults at that address land in a 43.8-56.1s window, so this is
            // the same bug rather than a new one.
            //
            // Turning them off costs the local-invasion filter its session lookup and the lobby-key
            // line, both of which are inert anyway while `enabled` is false. It does not cost the
            // warp keys, the catalog or the map pins, which is what a user of this DLL is here for.
            // Set it back to true only once the mechanism is identified -- it is still unknown, and
            // a switch is a mitigation, not a fix.
            ersc_observers: false,
            // ON: both halves of the pair, so the master switch alone reproduces the old behaviour.
            ersc_show_observer: true,
            ersc_lobby_key_observer: true,
            ersc_invade_observer: true,
            // The historical keys stay the default, so an existing config and an existing player's
            // muscle memory both keep working without touching the file.
            mark_key: crate::keybind::VK_INSERT,
            unmark_key: crate::keybind::VK_DELETE,
            enable_toggle_key: crate::keybind::VK_F3,
            settings_key: crate::keybind::VK_F4,
            allowed_blocks: BTreeSet::new(),
            blocked_blocks: BTreeSet::new(),
        }
    }
}

/// Which of the three persistent states a location is in, independent of where the player stands.
///
/// This is what the world map colours by, and it is deliberately not derived from a judgement
/// against the player's position: that answers "would this match land right now, from here",
/// which changes as the player walks. A map needs an answer that does not.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum LocationChoice {
    /// The user marked it with Insert.
    Chosen,
    /// Neither marked nor excluded -- an invasion location the user has no opinion about.
    #[default]
    Untouched,
    /// The user excluded it with Delete.
    Excluded,
}

impl LocalInvasionConfig {
    /// What the map should say about a location, with no reference to where the player is.
    #[must_use]
    pub fn choice_for(&self, block: u32) -> LocationChoice {
        if self.blocked_blocks.contains(&block) {
            LocationChoice::Excluded
        } else if self.allowed_blocks.contains(&block) {
            LocationChoice::Chosen
        } else {
            LocationChoice::Untouched
        }
    }

    /// Insert: "I want to invade here." Clears any exclusion, so the two keys are inverses rather
    /// than two ways of reaching the same stuck state. Returns whether anything changed, so a
    /// hotkey can stay silent on a repeat press instead of rewriting the file every frame.
    pub fn mark_block(&mut self, block: u32) -> bool {
        let unblocked = self.blocked_blocks.remove(&block);
        self.allowed_blocks.insert(block) || unblocked
    }

    /// Delete: "I do not want to invade here." Clears any mark and records the exclusion.
    ///
    /// This used to merely un-mark, which made Delete a no-op on anything the user had not already
    /// chosen -- and left no way at all to say "not here". Excluding is the useful meaning, and it
    /// is what gives the world map its third tier.
    pub fn unmark_block(&mut self, block: u32) -> bool {
        let unmarked = self.allowed_blocks.remove(&block);
        self.blocked_blocks.insert(block) || unmarked
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_is_off() {
        assert!(
            !LocalInvasionConfig::default().enabled,
            "this cancels real matches; it has to be asked for, not inherited"
        );
    }

    #[test]
    fn an_anchor_reports_how_many_places_it_makes_acceptable() {
        assert_eq!(InvasionAnchor::new(1, [100]).named_location_count(), 1);
        assert_eq!(
            InvasionAnchor::new(1, [100, 200, 300, 400, 500]).named_location_count(),
            5,
            "five names means five places to look"
        );
    }

    #[test]
    fn place_name_none_is_never_stored_as_an_anchor_name() {
        let anchor = InvasionAnchor::new(1, [PLACE_NAME_NONE, 100, PLACE_NAME_NONE]);
        assert_eq!(anchor.named_location_count(), 1);
        assert!(!anchor.place_names.contains(&PLACE_NAME_NONE));
    }

    #[test]
    fn the_three_location_states_do_not_depend_on_where_the_player_is() {
        // The whole reason this exists. Deriving the map's tiers from `judge` made them a function
        // of the player's position, so standing in a hub painted every pin the same tier.
        let mut config = LocalInvasionConfig {
            enabled: true,
            ..Default::default()
        };
        config.mark_block(0x0f00_0000);
        config.unmark_block(0x3c35_3800);
        assert_eq!(config.choice_for(0x0f00_0000), LocationChoice::Chosen);
        assert_eq!(config.choice_for(0x3c35_3800), LocationChoice::Excluded);
        assert_eq!(config.choice_for(0x1234_0000), LocationChoice::Untouched);
        // No anchor is involved at all -- there is no parameter for one.
        assert_eq!(
            LocalInvasionConfig::default().choice_for(0x0f00_0000),
            LocationChoice::Untouched
        );
    }
}
