//! A walkable path to every other player in your session, drawn on the ground they would walk.
//!
//! Press the configured key (or use the configured item) during an invasion and every other
//! player in the session gets a coloured route from your feet to theirs, laid along the terrain,
//! bolder the closer they are. A player the navmesh cannot reach from where you stand gets a
//! glowing arrow out of your body pointing straight at them instead. A player who is both close
//! and in plain sight gets nothing -- you can already see them.
//!
//! # What makes the line trustworthy
//!
//! The route is not ours. It is the answer `CSHkAiWorld` gives its own characters when they ask
//! how to walk somewhere, so it goes around the cliff rather than off it. See [`navpath`] for the
//! call chain and where each address came from.
//!
//! # What this DLL does to the game
//!
//! Nothing. No detours, no memory writes, no param edits (which is what breaks Seamless
//! invasions), no input injection, no network traffic. It reads the roster, asks the navmesh a
//! question, and draws. It ships as its own ME3 `[[natives]]` entry and shares no state with any
//! other module in this workspace.

// Ungated on purpose: the config parser, the projection maths and the colour bookkeeping are pure
// and are exercised by `cargo test` on the host, where the game-facing modules compile out.
mod census;
mod config;
mod geometry;
mod log;
mod routes;

#[cfg(windows)]
mod game;
#[cfg(windows)]
mod navpath;
#[cfg(windows)]
mod render;

#[cfg(windows)]
use std::sync::{
    Once,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};
#[cfg(windows)]
use windows::Win32::{Foundation::HINSTANCE, System::SystemServices::DLL_PROCESS_ATTACH};

#[cfg(windows)]
use crate::{
    log::{path_log, reset_log_file},
    routes::{Palette, Route, RouteShape, Snapshot},
};

const DLL_MAIN_SUCCESS: i32 = 1;

/// Frames between status lines. At 60fps, roughly every ten seconds.
#[cfg(windows)]
const STATUS_LOG_TICKS: usize = 600;

/// Frames between config re-reads. At 60fps, about once a second.
///
/// Not every frame: this is a filesystem read, and sixty a second to notice an edit a human made
/// by hand is sixty times more than the job needs. Once a second is faster than anyone can alt-tab
/// back to the game.
#[cfg(windows)]
const CONFIG_RELOAD_TICKS: usize = 60;

/// Frames between roster reads.
///
/// Not every frame: reading the roster costs a raycast per player, and a path recomputed 60 times
/// a second would also flicker as the navmesh answers land one frame apart. Six times a second is
/// faster than anyone can run out of a corridor.
#[cfg(windows)]
const ROSTER_EVERY_TICKS: usize = 10;

/// Frames a completed route is kept before its target is re-asked.
///
/// A route is only wrong once someone has moved a body-length or two, and the search is not free
/// -- it runs on the AI world's own job alongside every NPC in the map. Half a second of staleness
/// on a line drawn across a hillside is invisible.
#[cfg(windows)]
const ROUTE_REFRESH_TICKS: usize = 30;

#[cfg(windows)]
static START: Once = Once::new();
/// Is the overlay switched on?
#[cfg(windows)]
static ENABLED: AtomicBool = AtomicBool::new(false);
#[cfg(windows)]
static TICKS: AtomicUsize = AtomicUsize::new(0);
/// Routes successfully returned by the navmesh, for the status line.
#[cfg(windows)]
static ROUTES_FOUND: AtomicUsize = AtomicUsize::new(0);
/// Targets that fell back to the direction arrow.
#[cfg(windows)]
static ARROWS_DRAWN: AtomicUsize = AtomicUsize::new(0);
/// Targets suppressed by the close-and-visible rule.
#[cfg(windows)]
static SUPPRESSED: AtomicUsize = AtomicUsize::new(0);

#[cfg(windows)]
unsafe extern "system" {
    fn GetAsyncKeyState(vkey: i32) -> i16;
}

/// `GetAsyncKeyState`'s high bit: the key is down right now.
#[cfg(windows)]
const KEY_DOWN_MASK: i16 = -0x8000;
/// Its low bit: the key was pressed since the previous call ON THIS THREAD. Both bits are needed
/// -- a press and release entirely inside one frame sets only the low one, and polling the high
/// bit alone would drop it.
#[cfg(windows)]
const KEY_PRESSED_SINCE_MASK: i16 = 0x0001;

/// Edge detector for one virtual key.
#[cfg(windows)]
#[derive(Default)]
struct KeyEdge {
    was_down: bool,
}

#[cfg(windows)]
impl KeyEdge {
    fn pressed(&mut self, vkey: i32) -> bool {
        // SAFETY: a Win32 call taking a virtual-key code and returning a bitfield.
        let state = unsafe { GetAsyncKeyState(vkey) };
        let down = (state & KEY_DOWN_MASK) != 0;
        let pressed_since = (state & KEY_PRESSED_SINCE_MASK) != 0;
        let edge = (down && !self.was_down) || pressed_since;
        self.was_down = down;
        edge
    }
}

/// Whether the configured item was used since the last poll.
///
/// `ChrIns::tae_queued_use_item` (`ChrIns+0x160`) is what the animation system reads to decide
/// which goods row a use-item animation applies, so it names the item at the moment it is used.
/// Watching for a TRANSITION to the configured id is what stops holding the item from toggling
/// the overlay on and off sixty times a second.
#[cfg(windows)]
#[derive(Default)]
struct ItemTrigger {
    last_seen: Option<u32>,
}

#[cfg(windows)]
impl ItemTrigger {
    fn used(&mut self, current: Option<u32>, wanted: i32) -> bool {
        let fired =
            wanted > 0 && current == Some(wanted as u32) && self.last_seen != Some(wanted as u32);
        self.last_seen = current;
        fired
    }
}

/// One target's in-flight or completed route.
#[cfg(windows)]
struct TargetState {
    pending: Option<navpath::PendingRequest>,
    /// The last complete route, kept while the next one is computed so the line does not blink
    /// out every time it is refreshed.
    route: Option<Vec<[f32; 3]>>,
    /// Tick the current route was accepted, for [`ROUTE_REFRESH_TICKS`].
    computed_at: usize,
    /// True when the navmesh has said there is no way to walk there.
    unreachable: bool,
}

#[cfg(windows)]
impl Default for TargetState {
    fn default() -> Self {
        Self {
            pending: None,
            route: None,
            computed_at: 0,
            unreachable: true,
        }
    }
}

/// The one-shot proof that the navmesh call chain works, run without a second player.
///
/// Every address in [`navpath`] is byte-verified static RE, and until this existed none of it had
/// ever executed: a request is only issued for a remote player, so eleven raw function pointers
/// and a container walk would have run for the first time in the middle of an invasion. An access
/// violation there costs the session it was meant to prove, and a silent refusal there is
/// indistinguishable from "the navmesh says there is no way to walk to them".
///
/// So on the first enable, one route is requested to the nearest ordinary map character -- which
/// is standing on the navmesh by construction -- and the outcome is logged. Nothing is drawn from
/// it. A solo run in any field area now answers the question the invasion should not have to.
#[cfg(windows)]
#[derive(Default)]
enum SelfCheck {
    /// Not yet attempted since the overlay was last switched on.
    #[default]
    Idle,
    /// Asked, waiting for the AI world's job to answer.
    Waiting {
        pending: navpath::PendingRequest,
        distance_meters: f32,
    },
    /// Answered once. It is not retried: the chain either runs or it does not, and repeating a
    /// diagnostic every time the roster is rebuilt is how a log becomes unreadable.
    Done,
}

/// Everything the game task carries between frames.
#[cfg(windows)]
#[derive(Default)]
struct TaskState {
    toggle: KeyEdge,
    item: ItemTrigger,
    palette: Palette,
    targets: std::collections::HashMap<u64, TargetState>,
    self_check: SelfCheck,
    /// Requests nobody wants the answer to any more, kept until the engine answers them anyway.
    ///
    /// See [`abandon`]. This list is why a request is never simply dropped.
    draining: Vec<navpath::PendingRequest>,
}

/// Give up on a request's ANSWER without giving up on the request.
///
/// Dropping a `PendingRequest` looks free and is not. The engine allocates each request from a
/// fixed-size ring on the `CSHkAiWorld` -- `world+0x130` is the slot array (stride `0x68`),
/// `world+0x138` its capacity, `world+0x13c` a round-robin cursor -- and `FUN_140be1bb0` walks
/// that ring for a slot whose `+0x40` is zero, returning 0 when every slot is taken. A slot is
/// cleared in exactly one place: `FUN_140bf3320`, reachable only from the fetch this crate calls
/// in `poll`. That release is also what `hkUnref`s the Havok path and frees the two heap buffers
/// hanging off `+0x50` and `+0x58`.
///
/// So an unfetched request holds its slot, its Havok reference and its allocations FOREVER. And
/// the ring is not ours -- it is the one every NPC in the map allocates from, so filling it stops
/// the game's own characters pathfinding. Four places here would otherwise drop one: a player
/// leaving, the overlay switching off, the self-check re-arming, and -- constantly, during exactly
/// the fight this feature exists for -- a target closing to inside the suppression distance while
/// a request for them is still in flight.
///
/// The fix is not to cancel: there is no cancel. It is to keep polling until the engine answers
/// and then throw the answer away, which costs one `is_ready` read per frame per abandoned
/// request and returns the slot the moment it is free.
#[cfg(windows)]
fn abandon(draining: &mut Vec<navpath::PendingRequest>, pending: Option<navpath::PendingRequest>) {
    if let Some(pending) = pending {
        draining.push(pending);
    }
}

/// Poll everything in the drain list and discard whatever has answered.
///
/// Runs every frame, including while the overlay is off, because a slot the engine is holding on
/// our behalf is not freed by the player pressing a key.
#[cfg(windows)]
fn drain_abandoned(state: &mut TaskState) {
    if state.draining.is_empty() {
        return;
    }
    state.draining.retain_mut(|pending| {
        // SAFETY: the task runs on the game thread; each request is polled until it answers and
        // is then dropped, which is the contract `poll` documents.
        matches!(unsafe { pending.poll() }, navpath::PollOutcome::Pending)
    });
}

#[cfg(windows)]
fn wait_for_task_instance() -> &'static CSTaskImp {
    loop {
        match unsafe { CSTaskImp::instance() } {
            Ok(instance) => return instance,
            Err(_) => std::thread::yield_now(),
        }
    }
}

/// One game frame.
#[cfg(windows)]
fn tick(state: &mut TaskState) {
    let ticks = TICKS.fetch_add(1, Ordering::Relaxed);

    // Settings are editable while the game runs: change the key in the toml, save, press it. The
    // file is re-read on a slow cadence and compared by CONTENT, so an edit saved in the same
    // second as the previous read is still seen -- a timestamp check would miss it, and "I
    // changed the key and nothing happened" is the whole failure this avoids.
    if ticks.is_multiple_of(CONFIG_RELOAD_TICKS)
        && let Some(reloaded) = config::reload_if_changed()
    {
        if let Some(previous) = reloaded.previous_key_text.as_deref() {
            // Whichever key was physically down at the moment of the swap must not count as a
            // press of the NEW binding, so the edge detector starts over.
            state.toggle = KeyEdge::default();
            path_log(format_args!(
                "config: reloaded -- toggle key {previous} -> {}",
                reloaded.config.toggle_key_text
            ));
        } else {
            path_log(format_args!(
                "config: reloaded -- toggle key unchanged ({})",
                reloaded.config.toggle_key_text
            ));
        }
    }

    let config = config::config();

    let by_key = state.toggle.pressed(config.toggle_key);
    // Read the player ONLY when an item trigger is actually configured. With the default
    // `trigger_item_id = 0` there is nothing to compare against, so polling the player every
    // frame buys nothing and costs a pointer chase through a world that may not exist yet --
    // which is precisely what killed the game at ~100ms before this guard existed.
    let by_item = if config.trigger_item_id > 0 {
        // SAFETY: the task runs on the game thread; the reader screens the player pointer.
        let used_item = unsafe { game::current_use_item() };
        state.item.used(used_item, config.trigger_item_id)
    } else {
        false
    };
    if by_key || by_item {
        let enabled = !ENABLED.fetch_xor(true, Ordering::SeqCst);
        if !enabled {
            render::clear();
            for (_, target) in state.targets.drain() {
                abandon(&mut state.draining, target.pending);
            }
            // Re-armed rather than left Done: switching the overlay off and on is what a user does
            // after moving somewhere else, and the navmesh answer is a property of where they are
            // standing.
            if let SelfCheck::Waiting { pending, .. } =
                std::mem::replace(&mut state.self_check, SelfCheck::Idle)
            {
                abandon(&mut state.draining, Some(pending));
            }
        }
        path_log(format_args!(
            "toggle: overlay {} (by {})",
            if enabled { "ON" } else { "OFF" },
            if by_key { "key" } else { "item" }
        ));
    }

    // Before anything else, and regardless of whether the overlay is on: a request the engine is
    // still holding on our behalf is not released by the player pressing a key.
    drain_abandoned(state);

    if ENABLED.load(Ordering::SeqCst) && ticks.is_multiple_of(ROSTER_EVERY_TICKS) {
        rebuild(state, ticks, &config);
    }

    if ticks.is_multiple_of(STATUS_LOG_TICKS) {
        path_log(format_args!(
            "status: enabled={} overlay_installed={} draws={} last_segments={} tracked_targets={} \
             routes_found={} arrows={} suppressed={} draining={}",
            ENABLED.load(Ordering::SeqCst),
            render::installed(),
            render::draws(),
            render::last_segments(),
            state.targets.len(),
            ROUTES_FOUND.load(Ordering::Relaxed),
            ARROWS_DRAWN.load(Ordering::Relaxed),
            SUPPRESSED.load(Ordering::Relaxed),
            // A number that should sit at zero and briefly tick up. If it climbs and stays
            // climbing, requests are being abandoned faster than the engine answers them, and
            // the world's shared request ring is the thing at risk -- not this overlay.
            state.draining.len(),
        ));
    }
}

/// Read the roster, advance every in-flight navmesh request, and publish the frame's routes.
#[cfg(windows)]
fn rebuild(state: &mut TaskState, ticks: usize, config: &config::PathConfig) {
    // SAFETY: the task runs on the game thread.
    let Some(roster) = (unsafe { game::roster(config.max_targets) }) else {
        // No world: no local player to draw from. Not an error, just the title screen.
        render::clear();
        return;
    };

    advance_self_check(state, &roster);

    // The line that makes an empty screen readable. A run that draws nothing has three
    // indistinguishable causes -- nobody in the roster, every navmesh request refused, every
    // segment dropped in projection -- and this answers the first one with a count instead of
    // leaving it to be guessed at afterwards.
    if ticks.is_multiple_of(STATUS_LOG_TICKS) {
        path_log(format_args!(
            "roster: remotes={} {}",
            roster.remotes.len(),
            roster.census
        ));
    }

    let present: Vec<u64> = roster
        .remotes
        .iter()
        .map(|remote| remote.field_ins_handle)
        .collect();
    state.palette.retain(&present);
    // A player who left the session still has a request in the engine's ring. Take it with us.
    let departed: Vec<u64> = state
        .targets
        .keys()
        .filter(|handle| !present.contains(handle))
        .copied()
        .collect();
    for handle in departed {
        if let Some(target) = state.targets.remove(&handle) {
            abandon(&mut state.draining, target.pending);
        }
    }

    let mut snapshot = Snapshot::default();
    for remote in &roster.remotes {
        // The rule this feature was asked for: close AND visible means you already know where
        // they are, so a line on the ground is clutter over the fight you are in.
        if remote.distance_meters < config.near_suppress_meters && remote.in_sight {
            SUPPRESSED.fetch_add(1, Ordering::Relaxed);
            // The site that would have hurt most: a target crossing INTO the suppression
            // distance is the normal course of a fight, and it happens with a request in flight
            // every time. Dropping it here would burn a slot out of the world's shared ring on
            // every approach.
            if let Some(target) = state.targets.remove(&remote.field_ins_handle) {
                abandon(&mut state.draining, target.pending);
            }
            continue;
        }

        let target = state.targets.entry(remote.field_ins_handle).or_default();

        // Collect a finished search before starting another, or the ring fills with requests
        // nobody ever drains.
        if let Some(pending) = target.pending.as_mut() {
            // SAFETY: game thread; each request is polled until it answers and then dropped.
            match unsafe { pending.poll() } {
                navpath::PollOutcome::Pending => {}
                navpath::PollOutcome::Route(points) => {
                    ROUTES_FOUND.fetch_add(1, Ordering::Relaxed);
                    target.route = Some(points.into_iter().map(game::lift_waypoint).collect());
                    target.unreachable = false;
                    target.computed_at = ticks;
                    target.pending = None;
                }
                navpath::PollOutcome::NoRoute => {
                    target.route = None;
                    target.unreachable = true;
                    target.computed_at = ticks;
                    target.pending = None;
                }
            }
        }

        let stale = ticks.saturating_sub(target.computed_at) >= ROUTE_REFRESH_TICKS;
        if target.pending.is_none() && (stale || (target.route.is_none() && !target.unreachable)) {
            // SAFETY: game thread; both `ChrIns` pointers came from this frame's roster walk.
            match unsafe {
                navpath::request(
                    roster.local_chr_ins,
                    roster.local_field_ins_handle,
                    roster.local_position,
                    remote.position,
                )
            } {
                Ok(pending) => target.pending = Some(pending),
                Err(refusal) => {
                    // A refusal is a fact about the world (no navmesh here, that player is stood
                    // somewhere no character can walk), not a transient error to retry hard.
                    target.unreachable = true;
                    target.computed_at = ticks;
                    if ticks.is_multiple_of(STATUS_LOG_TICKS) {
                        path_log(format_args!(
                            "navmesh: no route request for target at {:.0}m -- {refusal:?}",
                            remote.distance_meters
                        ));
                    }
                }
            }
        }

        let slot = state.palette.slot_for(remote.field_ins_handle);
        let shape = match target.route.as_ref() {
            Some(points) if points.len() >= 2 => RouteShape::Walk(points.clone()),
            _ => {
                ARROWS_DRAWN.fetch_add(1, Ordering::Relaxed);
                let origin = game::arrow_origin(roster.local_position);
                // The head opens towards the world's up axis. The camera's would be better, but
                // the camera belongs to the render thread; a world-up head is never invisible,
                // only occasionally seen edge-on.
                let Some(arrow) = geometry::arrow(
                    origin,
                    remote.position,
                    config.arrow_meters,
                    [0.0, 1.0, 0.0],
                ) else {
                    continue;
                };
                RouteShape::Arrow(arrow)
            }
        };
        snapshot.routes.push(Route::new(
            shape,
            slot,
            remote.distance_meters,
            config.bold_at_meters,
            config.faint_at_meters,
        ));
    }
    render::publish(snapshot);
}

/// Drive [`SelfCheck`] one step. Costs one navmesh request, once per enable, and draws nothing.
#[cfg(windows)]
fn advance_self_check(state: &mut TaskState, roster: &game::Roster) {
    match std::mem::replace(&mut state.self_check, SelfCheck::Done) {
        SelfCheck::Done => {}
        SelfCheck::Idle => {
            // SAFETY: the task runs on the game thread.
            let Some((target, distance_meters)) =
                (unsafe { game::npc_probe_target(roster.local_position) })
            else {
                // Nowhere with a character to route to -- a menu, a boss arena already cleared, a
                // hub. Left Idle so the check happens in the first place that can answer it.
                state.self_check = SelfCheck::Idle;
                return;
            };
            // SAFETY: game thread; the local `ChrIns` came from this frame's roster walk and the
            // destination from a live physics module.
            match unsafe {
                navpath::request(
                    roster.local_chr_ins,
                    roster.local_field_ins_handle,
                    roster.local_position,
                    target,
                )
            } {
                Ok(pending) => {
                    path_log(format_args!(
                        "selfcheck: asked the navmesh for a route to a map character {distance_meters:.0}m away"
                    ));
                    state.self_check = SelfCheck::Waiting {
                        pending,
                        distance_meters,
                    };
                }
                Err(refusal) => {
                    // A refusal is still evidence: it proves the globals resolved and the
                    // endpoint snap ran, and it names which of the two failed.
                    path_log(format_args!(
                        "selfcheck: REFUSED at {distance_meters:.0}m -- {refusal:?}"
                    ));
                }
            }
        }
        SelfCheck::Waiting {
            mut pending,
            distance_meters,
        } => {
            // SAFETY: game thread; polled until it answers and then dropped.
            match unsafe { pending.poll() } {
                navpath::PollOutcome::Pending => {
                    state.self_check = SelfCheck::Waiting {
                        pending,
                        distance_meters,
                    };
                }
                navpath::PollOutcome::Route(points) => {
                    // The line this whole diagnostic exists to produce: the request was enqueued
                    // on the AI world's job, the poll saw it complete, the fetch drained it, and
                    // the container walk survived reading it.
                    path_log(format_args!(
                        "selfcheck: PASS -- {} waypoints over {distance_meters:.0}m",
                        points.len()
                    ));
                }
                navpath::PollOutcome::NoRoute => {
                    path_log(format_args!(
                        "selfcheck: no route to a map character {distance_meters:.0}m away -- the \
                         chain ran and answered, which is what this checks"
                    ));
                }
            }
        }
    }
}

#[cfg(windows)]
fn spawn_game_task() {
    let _ = std::thread::Builder::new()
        .name("er-invasion-path-task".to_owned())
        .spawn(move || {
            path_log(format_args!("game task thread waiting for CSTaskImp"));
            let task = wait_for_task_instance();
            path_log(format_args!("game task registering FrameBegin tick"));
            let mut state = TaskState::default();
            task.run_recurring(
                move |_data: &FD4TaskData| tick(&mut state),
                CSTaskGroupIndex::FrameBegin,
            );
        });
}

#[cfg(windows)]
fn install(module_base: usize) {
    reset_log_file();
    let config = config::init_config();
    ENABLED.store(config.start_enabled, Ordering::SeqCst);
    path_log(format_args!(
        "er-invasion-path attach: toggle_key={:?} trigger_item_id={} near_suppress={}m \
         bold_at={}m faint_at={}m max_targets={} config={}",
        config.toggle_key_text,
        config.trigger_item_id,
        config.near_suppress_meters,
        config.bold_at_meters,
        config.faint_at_meters,
        config.max_targets,
        config.config_path.display()
    ));
    render::install(module_base);
    spawn_game_task();
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. On attach it only starts an installer thread -- hudhook's
/// install takes locks and enumerates modules, neither of which belongs under the loader lock.
pub unsafe extern "system" fn DllMain(
    module: HINSTANCE,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        let module_base = module.0 as usize;
        START.call_once(|| {
            let _ = std::thread::Builder::new()
                .name("er-invasion-path-install".to_owned())
                .spawn(move || install(module_base));
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_invasion_path_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

// If THIS module wins the imgui context, every other overlay in the process has to be able to
// find it by name.
#[cfg(windows)]
er_build_watermark_core::export_overlay_host!();

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn holding_the_trigger_item_fires_once_not_every_frame() {
        let mut trigger = ItemTrigger::default();
        assert!(trigger.used(Some(2110), 2110));
        assert!(!trigger.used(Some(2110), 2110));
        assert!(!trigger.used(Some(2110), 2110));
        // Putting it away and using it again is a second toggle.
        assert!(!trigger.used(None, 2110));
        assert!(trigger.used(Some(2110), 2110));
    }

    #[test]
    fn a_different_item_never_fires_the_trigger() {
        let mut trigger = ItemTrigger::default();
        assert!(!trigger.used(Some(1000), 2110));
        assert!(!trigger.used(Some(9999), 2110));
    }

    #[test]
    fn the_item_trigger_is_off_when_no_item_is_configured() {
        let mut trigger = ItemTrigger::default();
        // Zero means "hotkey only"; no item use may toggle the overlay.
        assert!(!trigger.used(Some(0), 0));
        assert!(!trigger.used(Some(2110), 0));
    }
}
