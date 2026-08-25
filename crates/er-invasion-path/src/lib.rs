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

/// Everything the game task carries between frames.
#[cfg(windows)]
#[derive(Default)]
struct TaskState {
    toggle: KeyEdge,
    item: ItemTrigger,
    palette: Palette,
    targets: std::collections::HashMap<u64, TargetState>,
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

/// The item the local player is using this frame, if any.
///
/// # Safety
///
/// Must be called on the game thread.
#[cfg(windows)]
unsafe fn current_use_item() -> Option<u32> {
    use eldenring::cs::WorldChrMan;
    // SAFETY: singleton access on the game thread.
    let world_chr_man = unsafe { WorldChrMan::instance() }.ok()?;
    let player = world_chr_man.main_player.as_ref()?;
    player
        .chr_ins
        .tae_queued_use_item
        .as_valid()
        .map(|item| item.param_id())
}

/// One game frame.
#[cfg(windows)]
fn tick(state: &mut TaskState) {
    let config = config::config();
    let ticks = TICKS.fetch_add(1, Ordering::Relaxed);

    // SAFETY: the task runs on the game thread.
    let used_item = unsafe { current_use_item() };
    let by_key = state.toggle.pressed(config.toggle_key);
    let by_item = state.item.used(used_item, config.trigger_item_id);
    if by_key || by_item {
        let enabled = !ENABLED.fetch_xor(true, Ordering::SeqCst);
        if !enabled {
            render::clear();
            state.targets.clear();
        }
        path_log(format_args!(
            "toggle: overlay {} (by {})",
            if enabled { "ON" } else { "OFF" },
            if by_key { "key" } else { "item" }
        ));
    }

    if ENABLED.load(Ordering::SeqCst) && ticks.is_multiple_of(ROSTER_EVERY_TICKS) {
        rebuild(state, ticks, config);
    }

    if ticks.is_multiple_of(STATUS_LOG_TICKS) {
        path_log(format_args!(
            "status: enabled={} overlay_installed={} draws={} last_segments={} tracked_targets={} \
             routes_found={} arrows={} suppressed={}",
            ENABLED.load(Ordering::SeqCst),
            render::installed(),
            render::draws(),
            render::last_segments(),
            state.targets.len(),
            ROUTES_FOUND.load(Ordering::Relaxed),
            ARROWS_DRAWN.load(Ordering::Relaxed),
            SUPPRESSED.load(Ordering::Relaxed),
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

    let present: Vec<u64> = roster
        .remotes
        .iter()
        .map(|remote| remote.field_ins_handle)
        .collect();
    state.palette.retain(&present);
    state.targets.retain(|handle, _| present.contains(handle));

    let mut snapshot = Snapshot::default();
    for remote in &roster.remotes {
        // The rule this feature was asked for: close AND visible means you already know where
        // they are, so a line on the ground is clutter over the fight you are in.
        if remote.distance_meters < config.near_suppress_meters && remote.in_sight {
            SUPPRESSED.fetch_add(1, Ordering::Relaxed);
            state.targets.remove(&remote.field_ins_handle);
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
