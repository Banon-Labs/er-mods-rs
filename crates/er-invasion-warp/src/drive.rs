//! The arrival oracle that decides whether a warp actually worked.
//!
//! # The oracle is still live, and still needed
//!
//! [`note_external_warp`] is how the world-map confirm hook hands over a warp it issued, and the
//! arrival classification below is the only thing that can say a warp arrived rather than merely
//! being requested. That is a distinction worth keeping wired up even while nothing issues one:
//! if the policy is ever revisited, the evidence path is the part that took real runs to build.
//!
//! # The three warp hotkeys are gone (2026-09-15)
//!
//! F7 "nearest", F8 "next in catalog order" and F9 "first point in another area" were removed
//! along with `warp_nearest_key` / `warp_next_key` / `warp_other_area_key` and the selection
//! helpers behind them. They had already stopped moving anybody: invasion locations are markers
//! rather than fast-travel destinations, so every press was declined by
//! [`er_invasion_warp_core::warp::WarpPolicy::MarkersOnly`] before the catalog was even read.
//!
//! What replaced them is the world map. The pins are injected into the map's own warp row list,
//! so choosing one goes through the game's confirm path -- the surface a player already knows,
//! with no binding to collide with another mod. Three keys that answered "declined" were three
//! more things to explain and three more defaults to keep out of somebody else's way.
//!
//! The payload they were built to exercise is untouched: catalog -> engine coordinate conversion
//! -> explicit spawn -> stage kick -> settled read-back all still run, driven by the map confirm
//! and judged by the oracle below.
//!
//! # What "it worked" means here
//!
//! Not "the DLL loaded", not "no crash", and not "the hotkey fired". A warp counts only when
//! the player is read back in the destination block within
//! [`er_invasion_warp_core::oracles::INVASION_WARP_POSITION_TOLERANCE_METRES`] of the requested
//! point ([`WarpArrival::Arrived`]). Landing in the right block at the wrong place is the
//! signature of the explicit-spawn slot not taking -- the engine falls back to the block's
//! default spawn -- and is reported as [`WarpArrival::Mislanded`], a failure.

use er_invasion_warp_core::warp::{WARP_ARRIVAL_TICK_BUDGET, WarpArrival, WarpOutcome};

#[cfg(windows)]
use er_invasion_warp_core::warp::classify_arrival;

/// How often the driver logs a heartbeat while idle, in game-task ticks (~60/s, so ~10s).
///
/// Without it, "the hotkey never fired" and "the driver never ran" and "the window never had
/// focus" are the same silence, and a run cannot tell them apart.
#[cfg(windows)]
const HEARTBEAT_TICK_INTERVAL: u64 = 600;

/// Tick at which the loaded-mod roster is logged, once.
///
/// Deliberately later than the first frame: me3 loads each `[[natives]]` entry in sequence, so a
/// roster taken at our own DllMain would list whichever DLLs preceded us and silently omit the
/// rest. At 60 ticks the loader is long finished and the list is the whole profile.
const ROSTER_LOG_TICK: u64 = 60;

#[cfg(windows)]
#[link(name = "user32")]
unsafe extern "system" {
    fn GetForegroundWindow() -> isize;
    fn GetWindowThreadProcessId(hwnd: isize, pid: *mut u32) -> u32;
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetCurrentProcessId() -> u32;
}

#[cfg(windows)]
/// Only react when Elden Ring itself has focus, so alt-tabbing to another window and pressing
/// F7 there does not teleport the player. Shared with the local-invasion mark keys, which need
/// exactly the same guard for exactly the same reason.
pub fn game_has_focus() -> bool {
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd == 0 {
        return false;
    }
    let mut pid: u32 = 0;
    unsafe { GetWindowThreadProcessId(hwnd, &raw mut pid) };
    pid != 0 && pid == unsafe { GetCurrentProcessId() }
}

#[cfg(windows)]
/// What the driver is doing right now.
enum DriveState {
    /// Nothing in flight.
    Idle,
    /// A warp was issued; waiting for the world to settle so arrival can be judged.
    AwaitingArrival {
        outcome: Box<WarpOutcome>,
        ticks_waited: u32,
    },
}

/// A warp issued from outside this driver -- i.e. by the world-map confirm hook -- handed over so
/// the driver's arrival watcher can judge it.
///
/// Why this exists. `classify_arrival` was wired only into the keyboard driver, so a warp the
/// player triggered by selecting a map pin was never checked at all: the confirm hook logged
/// "LOCAL warp to block ..." the moment the stage kick was issued and stopped there. That line
/// says the explicit-spawn slot latched, not that the player went anywhere -- and on 2026-08-04 a
/// user reported warping doing nothing while the log recorded a dozen consecutive "successes"
/// and `er-invasion-warp-run.json` was never written at all, because nothing on that path ever
/// published it. The product path now produces the same arrival evidence the driver does.
///
/// The hook runs on the menu/UI callsite, not the game task, so the outcome is parked here and
/// adopted by the next tick rather than judged in place.
#[cfg(windows)]
static EXTERNAL_WARP: std::sync::Mutex<Option<WarpOutcome>> = std::sync::Mutex::new(None);

/// Hand a warp issued outside the driver to the driver's arrival watcher.
///
/// Last writer wins: if a second warp is confirmed before the first has settled, the newer one is
/// what the player is actually waiting on. Poisoning is recovered from rather than propagated --
/// a warp must never be able to panic the game thread.
#[cfg(windows)]
pub fn note_external_warp(outcome: WarpOutcome) {
    let mut slot = match EXTERNAL_WARP.lock() {
        Ok(slot) => slot,
        Err(poisoned) => poisoned.into_inner(),
    };
    *slot = Some(outcome);
}

/// Take the parked external warp, if any.
#[cfg(windows)]
fn take_external_warp() -> Option<WarpOutcome> {
    let mut slot = match EXTERNAL_WARP.lock() {
        Ok(slot) => slot,
        Err(poisoned) => poisoned.into_inner(),
    };
    slot.take()
}

#[cfg(windows)]
/// The whole driver. One instance, owned by the game task.
pub struct InvasionWarpDrive {
    state: DriveState,
    warps_issued: u32,
    warps_arrived: u32,
    /// Game-task ticks seen, for the heartbeat cadence.
    ticks: u64,
}

#[cfg(windows)]
impl Default for InvasionWarpDrive {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(windows)]
impl InvasionWarpDrive {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            state: DriveState::Idle,
            warps_issued: 0,
            warps_arrived: 0,
            ticks: 0,
        }
    }

    /// One game-task tick.
    ///
    /// # Safety
    ///
    /// Must run on the game task thread with the runtime singletons resolved.
    pub unsafe fn tick(&mut self, log: fn(std::fmt::Arguments<'_>), publish: fn(&str)) {
        if !er_invasion_warp_core::host::invasion_warp_enabled() {
            return;
        }
        let Ok(base) = er_game_base::mem::game_module_base() else {
            return;
        };

        // A warp the world-map confirm hook issued is adopted before anything else, so the
        // product path gets the same arrival proof the keyboard driver has always had. Only when
        // idle: an in-flight warp is still the one being judged.
        if matches!(self.state, DriveState::Idle)
            && let Some(outcome) = take_external_warp()
        {
            self.warps_issued = self.warps_issued.saturating_add(1);
            log(format_args!(
                "invasion-warp: adopting a map-confirm warp to block {} for arrival checking \
                 (session gate: {}); the confirm log line only proves the spawn slot latched, \
                 this is what proves the player moved",
                outcome.target.block,
                outcome.session_gate.describe()
            ));
            self.state = DriveState::AwaitingArrival {
                outcome: Box::new(outcome),
                ticks_waited: 0,
            };
        }

        // An in-flight warp is judged first: the settled read-back is the proof, and it must be
        // taken before any new press is honoured.
        if let DriveState::AwaitingArrival {
            outcome,
            ticks_waited,
        } = &mut self.state
        {
            *ticks_waited = ticks_waited.saturating_add(1);
            let settled = unsafe { settled_reading(base) };
            let expected = unsafe {
                er_invasion_warp_core::warp::resolve_target(base, &outcome.target)
                    .map(|resolved| resolved.world_position)
            };
            let arrival = classify_arrival(outcome, *ticks_waited, settled, expected);
            match arrival {
                WarpArrival::Pending { .. } => return,
                WarpArrival::Arrived { .. } => {
                    self.warps_arrived = self.warps_arrived.saturating_add(1);
                }
                WarpArrival::Mislanded { .. } | WarpArrival::TimedOut { .. } => {}
            }
            let outcome = *outcome.clone();
            log(format_args!(
                "invasion-warp: {}",
                describe_arrival(&outcome, &arrival)
            ));
            publish(&warp_oracle_json(
                &outcome,
                &arrival,
                expected,
                self.warps_issued,
                self.warps_arrived,
            ));
            self.state = DriveState::Idle;
            return;
        }

        self.ticks = self.ticks.wrapping_add(1);

        // Once, late enough that every `[[natives]]` entry has had its DllMain run. Logging the
        // roster at our own DllMain would name only the DLLs the loader happened to reach first
        // and would read as "the others are missing" -- a false negative about the very thing
        // this line exists to measure. `latest_release` is None until the release lookup lands,
        // so every mod reports `unknown`, which is deliberately not `STALE`.
        if self.ticks == ROSTER_LOG_TICK {
            log(format_args!(
                "{}",
                er_game_base::build_id::roster_line(&er_game_base::build_id::published_main_shas())
            ));
        }

        let focused = game_has_focus();

        // The heartbeat exists because every "nothing happened" path here is silent, and during
        // the first live run they were indistinguishable: no focus, no driver and no game task
        // all looked identical in the log. `focused` is still worth printing with the warp keys
        // gone -- the mark keys and the settings key are read by other pollers under the same
        // guard, so a window that never had focus explains their silence too.
        if self.ticks.is_multiple_of(HEARTBEAT_TICK_INTERVAL) {
            // (passed, queried) per bucket -- the visibility oracle. `ours 0/N` with a healthy
            // shipped ratio means our rows are reaching the filter and being rejected, which is
            // a field problem; `ours 0/0` means the filter never saw them at all, which is a
            // different bug entirely.
            let verdicts = crate::map_hooks::filter_verdicts();
            // (ctor hits, injections that appended, injections that appended nothing). These
            // are the "every map view, every map open" oracle: opens and injections must stay
            // equal, and skips must stay zero. A screenshot cannot tell the difference between
            // "no pins were injected" and "pins were injected but are not drawn"; this can.
            let (opens, injections, skips) = crate::map_hooks::injection_tallies();
            // (gfx parses seen, world-map movies recognised, edited movies served, derive
            // failures). `served=0` means the pins are on the fallback vanilla icon rather than
            // the red one -- the difference between "the icon did not change" and "the icon
            // could not be installed", which is otherwise a question only a screenshot answers.
            let (_, map_movies, red_served, red_failures) = crate::map_gfx::gfx_tallies();
            let player = unsafe { er_invasion_warp_core::warp::player_physics_position(base) };
            // Which block the player is in. The position alone cannot answer "did the warp move
            // me": it is block-local, so arriving in a different block can read as a similar
            // triple, and on 2026-08-04 a whole run's worth of heartbeats could not distinguish
            // a warp that worked from one that did nothing. The block id can.
            let block = unsafe { er_invasion_warp_core::warp::current_block_id(base) };
            // MSB invasion-point coverage: (points, maps read). The `.aip` table cannot describe
            // any legacy dungeon, so `msb[0/0]` while standing in one means the second source is
            // not running -- a distinct failure from "running but nothing placed".
            let (msb_points, msb_maps) = crate::map_hooks::msb_coverage();
            // The rejection banner's counters. Surfaced here because a banner that never fires and
            // a banner that fires perfectly are otherwise identical in the log -- success was
            // silent, so the only evidence was the absence of a failure line, which is not
            // evidence at all for a feature whose whole job is to appear on screen. `shown` counts
            // banners whose text was read back out of the game's own rawString/length after
            // writing; `refused` counts attempts dropped before display.
            let (banners_shown, banners_refused) = crate::announce::tally();
            // `shown` counts placements; `drawn`/`empty` count what the game measured afterwards.
            // The pair is the point: a blank banner shipped once with shown=1 and no way to see it
            // in telemetry, so the number that can disagree with success is the one worth printing.
            let (banners_drawn, banners_empty) = crate::announce::measurement_tally();
            // What the local-invasion filter actually did, as opposed to whether it is armed.
            // `unenforced` is the one that matters: a match this module judged a rejection and then
            // could not cancel proceeds anyway, which from the player's seat is the mod being off.
            // It was reported that way on 2026-09-06 ("I didn't only invade locally. It might be
            // disabled?") after a run whose log carried four such rejections and no counter for
            // them -- the heartbeat printed a healthy-looking line beside an inert filter.
            // This used to carry a fourth number, counting matches judged a rejection that could
            // not be cancelled and proceeded anyway. The location filter was deleted on
            // 2026-09-15, so nothing rejects a connected match and nothing can fail to enforce
            // one. `cancels` is still real: the deadline abandons a connect going nowhere.
            let (keeps, cancels, reinvades) = crate::local_invasion_filter::tallies();
            // What this host has told the world about where it is standing. Zero published while
            // hosting means an invader filtering on location cannot see this host at all -- a
            // state that was previously indistinguishable from not hosting, because every failure
            // path in the publish is a silent no-op.
            let (adverts, advert_refusals) = crate::lobby_publish::publish_tallies();
            // The settings panel, as two numbers that fail differently. `drawn` is frames the
            // panel rendered into: zero while it is open means this DLL's guest registration
            // never reached the host's frame, which looks on screen exactly like the key not
            // working. `clicks` is presses it took: a panel that draws and never takes a click
            // is one whose DirectInput suppression did not arm, and every press on it is
            // reaching the game as a swing instead.
            let panel_open = crate::overlay::is_open();
            let panel_draws = crate::overlay::draws();
            let panel_clicks = crate::overlay::clicks();
            let panel_suppressed = er_dinput_suppress_core::suppressed_mouse_clicks();
            log(format_args!(
                "invasion-warp: heartbeat tick={} focused={focused} \
                 block={} player={} pins={} msb[{msb_points} points/{msb_maps} \
                 maps] map[opens={opens} injected={injections} skipped={skips}] \
                 icon[movie={map_movies} red_served={red_served} derive_failed={red_failures}] \
                 filter[ours {}/{} shipped {}/{}] \
                 advert[published={adverts} refused={advert_refusals}] \
                 local[kept={keeps} cancelled={cancels} rearmed={reinvades}] \
                 banner[shown={banners_shown} refused={banners_refused} \
                 drawn={banners_drawn} empty={banners_empty}] \
                 panel[open={panel_open} drawn={panel_draws} clicks={panel_clicks} \
                 clicks_kept_from_game={panel_suppressed}] \
                 -- invasion locations are markers, not warp destinations: the map's own confirm \
                 is declined while a search is armed, and the pins are drawn dimmed to show it",
                self.ticks,
                block.map_or_else(|| "none".to_string(), |b| format!("{b:#010x}")),
                player.map_or_else(
                    || "none".to_string(),
                    |p| format!("[{:.1}, {:.1}, {:.1}]", p[0], p[1], p[2])
                ),
                crate::map_hooks::pins_injected(),
                verdicts.1,
                verdicts.0,
                verdicts.3,
                verdicts.2,
            ));
        }
    }
}

#[cfg(windows)]
/// The settled `(block, position)` reading arrival is judged against, or `None` while either
/// half is unavailable (mid-load the player does not exist).
///
/// # Safety
///
/// Game task thread.
unsafe fn settled_reading(base: usize) -> Option<(u32, [f32; 3])> {
    let block = unsafe { er_invasion_warp_core::warp::current_block_id(base) }?;
    let position = unsafe { er_invasion_warp_core::warp::player_physics_position(base) }?;
    Some((block, position))
}

/// One human-readable line describing how a warp ended.
#[must_use]
pub fn describe_arrival(outcome: &WarpOutcome, arrival: &WarpArrival) -> String {
    match arrival {
        WarpArrival::Pending { ticks_waited } => {
            format!("warp pending after {ticks_waited} ticks")
        }
        WarpArrival::Arrived {
            final_block,
            final_position,
            ticks_waited,
        } => format!(
            "ARRIVED block {final_block:#010x} pos [{:.2}, {:.2}, {:.2}] after {ticks_waited} \
             ticks (requested block {:#010x})",
            final_position[0], final_position[1], final_position[2], outcome.effective_block
        ),
        WarpArrival::Mislanded {
            final_block,
            final_position,
        } => format!(
            "MISLANDED block {final_block:#010x} pos [{:.2}, {:.2}, {:.2}] -- expected block \
             {:#010x} near [{:.2}, {:.2}, {:.2}]; the explicit-spawn slot did not take",
            final_position[0],
            final_position[1],
            final_position[2],
            outcome.effective_block,
            outcome.spawn_position[0],
            outcome.spawn_position[1],
            outcome.spawn_position[2]
        ),
        WarpArrival::TimedOut { ticks_waited } => format!(
            "UNPROVEN: the world never settled within {ticks_waited} ticks (budget \
             {WARP_ARRIVAL_TICK_BUDGET})"
        ),
    }
}

/// The warp oracle document, using the names `er_invasion_warp_core::oracles` reserved for them.
#[must_use]
pub fn warp_oracle_json(
    outcome: &WarpOutcome,
    arrival: &WarpArrival,
    expected_physics: Option<[f32; 3]>,
    warps_issued: u32,
    warps_arrived: u32,
) -> String {
    use er_invasion_warp_core::oracles::{
        ORACLE_INVASION_WARP_FINAL_BLOCK, ORACLE_INVASION_WARP_FINAL_POSITION,
        ORACLE_INVASION_WARP_REQUESTED_BLOCK, ORACLE_INVASION_WARP_REQUESTED_POSITION,
        ORACLE_INVASION_WARP_REQUESTED_YAW, ORACLE_INVASION_WARP_SELECTED_ID,
        ORACLE_INVASION_WARP_SESSION_TOUCHES, encode_position_oracle, encode_scalar_oracle,
    };
    let (verdict, passed) = match arrival {
        WarpArrival::Arrived { .. } => ("arrived", true),
        WarpArrival::Mislanded { .. } => ("mislanded", false),
        WarpArrival::TimedOut { .. } => ("unproven_timeout", false),
        WarpArrival::Pending { .. } => ("pending", false),
    };
    let (final_block, final_position) = match arrival {
        WarpArrival::Arrived {
            final_block,
            final_position,
            ..
        }
        | WarpArrival::Mislanded {
            final_block,
            final_position,
        } => (Some(*final_block), Some(*final_position)),
        _ => (None, None),
    };
    let requested = encode_position_oracle(outcome.spawn_position);
    let final_encoded = final_position.map(encode_position_oracle);
    // The two position fields are in different spaces and must be labelled as such.
    // `requested_position` is the block-local .aip value read back out of GameMan+0xc90 --
    // that is what the engine was handed. `final_position` is the player's physics position.
    // They coincide only where the block origin happens to be ~0, which made a live run look
    // self-consistent by luck. `expected_position_physics` is the requested point put through
    // the engine's own conversion, and is the value the arrival verdict compares against.
    let expected_encoded = expected_physics.map(encode_position_oracle);
    format!(
        "{{\"{ORACLE_INVASION_WARP_SELECTED_ID}\":{selected},\
         \"{ORACLE_INVASION_WARP_REQUESTED_BLOCK}\":{requested_block},\
         \"{ORACLE_INVASION_WARP_REQUESTED_POSITION}\":[{rx},{ry},{rz}],\
         \"{ORACLE_INVASION_WARP_REQUESTED_YAW}\":{yaw},\
         \"requested_position_space\":\"block_local\",\
         \"expected_position_physics\":{expected_position_json},\
         \"final_position_space\":\"physics\",\
         \"{ORACLE_INVASION_WARP_FINAL_BLOCK}\":{final_block_json},\
         \"{ORACLE_INVASION_WARP_FINAL_POSITION}\":{final_position_json},\
         \"{ORACLE_INVASION_WARP_SESSION_TOUCHES}\":{session_touches},\
         \"effective_block\":{effective_block},\"spawn_flag\":{spawn_flag},\
         \"warps_issued\":{warps_issued},\"warps_arrived\":{warps_arrived},\
         \"verdict\":\"{verdict}\",\"passed\":{passed},\
         \"session_touches_note\":\"vanilla TriggerAreaReload calls SetupMapReentry when \
         protocolState==InGame; this counts that call rather than asserting zero\"}}",
        selected = outcome.target.stable_id(),
        requested_block = outcome.requested_block,
        rx = requested[0],
        ry = requested[1],
        rz = requested[2],
        yaw = encode_scalar_oracle(outcome.spawn_yaw),
        expected_position_json = expected_encoded.map_or_else(
            || "null".to_string(),
            |p| format!("[{},{},{}]", p[0], p[1], p[2])
        ),
        final_block_json = final_block.map_or_else(|| "null".to_string(), |b| b.to_string()),
        final_position_json = final_encoded.map_or_else(
            || "null".to_string(),
            |p| format!("[{},{},{}]", p[0], p[1], p[2])
        ),
        session_touches = outcome.session_touches,
        effective_block = outcome.effective_block,
        spawn_flag = outcome.spawn_flag,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    // Imported here rather than relying on the module-level import, which is cfg(windows):
    // these tests are the host-side coverage of the arrival verdicts and must build on Linux.
    use er_invasion_warp_core::invasion_warp::{BlockKey, InvasionWarpTarget};

    fn outcome() -> WarpOutcome {
        WarpOutcome {
            target: InvasionWarpTarget::new(
                BlockKey::from_parts(60, 34, 51, 0),
                7,
                [10.0, 20.0, 30.0],
                -1.25,
            ),
            origin_block: 0x3C21_2200,
            requested_block: 0x3C22_3300,
            effective_block: 0x3C22_3300,
            spawn_flag: 1,
            spawn_position: [10.0, 20.0, 30.0],
            spawn_yaw: -1.25,
            session_touches: 1,
            session_gate: er_invasion_warp_core::warp::SessionGate::Entered,
        }
    }

    /// The keys this crate binds must not ship colliding with each other. A player can still
    /// create a collision by hand -- and is warned when they do -- but the defaults must not.
    ///
    /// It was five keys until 2026-09-15; the three warp keys went with the feature. The two that
    /// remain plus the two function keys are the whole surface now, and the assertion is kept
    /// rather than dropped as trivial: the shipped defaults are what a player who never opens the
    /// file is playing with, and F3/F4 sitting next to each other is exactly the kind of pair a
    /// later edit collides by hand.
    #[test]
    fn the_shipped_keys_do_not_collide_with_each_other() {
        let shipped = er_invasion_warp_core::local_invasion_config::parse_local_invasion_config(
            er_invasion_warp_core::local_invasion_config::DEFAULT_CONFIG_TOML,
        )
        .config;
        let keys = [
            shipped.mark_key,
            shipped.unmark_key,
            shipped.enable_toggle_key,
            shipped.settings_key,
        ];
        for (index, key) in keys.iter().enumerate() {
            assert!(
                !keys[index + 1..].contains(key),
                "two shipped bindings are both {}",
                er_invasion_warp_core::keybind::key_name(*key)
            );
        }
    }

    #[test]
    fn only_an_arrival_reports_passed() {
        // The whole point: a warp that was merely issued must never read as success.
        let cases = [
            (
                WarpArrival::Arrived {
                    final_block: 0x3C22_3300,
                    final_position: [10.0, 20.0, 30.0],
                    ticks_waited: 5,
                },
                true,
            ),
            (
                WarpArrival::Mislanded {
                    final_block: 0x3C22_3300,
                    final_position: [900.0, 0.0, 900.0],
                },
                false,
            ),
            (WarpArrival::TimedOut { ticks_waited: 60 }, false),
            (WarpArrival::Pending { ticks_waited: 1 }, false),
        ];
        for (arrival, expected) in cases {
            let json = warp_oracle_json(&outcome(), &arrival, None, 1, u32::from(expected));
            assert!(
                json.contains(&format!("\"passed\":{expected}")),
                "{arrival:?} -> {json}"
            );
        }
    }

    #[test]
    fn a_pending_warp_reports_null_final_values_rather_than_zeros() {
        // A zero would read as "settled at the origin"; null says "not measured yet".
        let json = warp_oracle_json(
            &outcome(),
            &WarpArrival::Pending { ticks_waited: 3 },
            None,
            1,
            0,
        );
        assert!(
            json.contains("\"oracle_invasion_warp_final_block\":null"),
            "{json}"
        );
        assert!(
            json.contains("\"oracle_invasion_warp_final_position\":null"),
            "{json}"
        );
    }

    #[test]
    fn the_two_position_fields_are_labelled_with_their_coordinate_space() {
        // A live run had requested_position (block-local) numerically equal to final_position
        // (physics) because that block's origin was ~0, which made a mixed-space document look
        // self-consistent by luck. The labels stop that reading.
        let json = warp_oracle_json(
            &outcome(),
            &WarpArrival::Arrived {
                final_block: 0x3C22_3300,
                final_position: [10.0, 20.0, 30.0],
                ticks_waited: 9,
            },
            Some([1000.0, 20.0, 30.0]),
            1,
            1,
        );
        assert!(
            json.contains("\"requested_position_space\":\"block_local\""),
            "{json}"
        );
        assert!(
            json.contains("\"final_position_space\":\"physics\""),
            "{json}"
        );
        // The physics-space expectation is the value the verdict actually compared against,
        // and it must be present and distinct from the block-local one.
        assert!(
            json.contains("\"expected_position_physics\":[1000000,20000,30000]"),
            "{json}"
        );
    }

    #[test]
    fn the_session_touch_count_is_reported_not_asserted_to_be_zero() {
        let json = warp_oracle_json(
            &outcome(),
            &WarpArrival::Pending { ticks_waited: 0 },
            None,
            1,
            0,
        );
        assert!(
            json.contains("\"oracle_invasion_warp_session_touches\":1"),
            "{json}"
        );
        assert!(json.contains("session_touches_note"), "{json}");
    }

    #[test]
    fn the_mislanding_description_names_the_explicit_spawn_slot_as_the_suspect() {
        let text = describe_arrival(
            &outcome(),
            &WarpArrival::Mislanded {
                final_block: 0x3C22_3300,
                final_position: [900.0, 0.0, 900.0],
            },
        );
        assert!(text.contains("MISLANDED"), "{text}");
        assert!(text.contains("explicit-spawn"), "{text}");
    }

    #[test]
    fn the_arrival_description_carries_the_settled_block_and_position() {
        let text = describe_arrival(
            &outcome(),
            &WarpArrival::Arrived {
                final_block: 0x3C22_3300,
                final_position: [10.0, 20.0, 30.0],
                ticks_waited: 12,
            },
        );
        assert!(text.contains("ARRIVED"), "{text}");
        assert!(text.contains("12"), "{text}");
    }

    #[test]
    fn the_selected_id_survives_into_the_oracle_document() {
        let outcome = outcome();
        let json = warp_oracle_json(
            &outcome,
            &WarpArrival::Pending { ticks_waited: 0 },
            None,
            1,
            0,
        );
        assert!(
            json.contains(&format!(
                "\"oracle_invasion_warp_selected_id\":{}",
                outcome.target.stable_id()
            )),
            "{json}"
        );
    }
}
