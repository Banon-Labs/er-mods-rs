//! The band census on the game task: send its lobby queries, keep its counts, and hand the panel a
//! snapshot to draw.
//!
//! The counting and the arithmetic are `er_invasion_warp_core::band_census`, tested on the host.
//! This half owns the one query in flight, the clock, the file the arrivals persist in, and the
//! view the renderer clones.
//!
//! # When it asks
//!
//! Only after the panel has been opened once this session, and from then on in the background, so
//! a player who opens it once and plays on gets an average that keeps growing. A player who never
//! opens it sends no query at all: forty lobby lists a minute is a cost every user of this DLL
//! would otherwise pay for a panel most of them never look at.
//!
//! And only while the Seamless session is idle and the neighbourhood sweep has nothing to send
//! (`lobby_preflight::tick` decides that). Steam keeps one lobby-list request in flight per
//! interface and the second one wins, so a census query sent into a search would cost Seamless its
//! answer (bd our-sweep-clobbers-seamless-own-lobby-query-2026-09-17).

#![cfg(windows)]

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::{Mutex, OnceLock, PoisonError};
use std::time::Instant;

use er_invasion_warp_core::band_census::{
    self as census, Answer, Arrivals, Cell, Census, Query, Split, View,
};

/// The file the arrivals persist in, beside `er-invasion-warp.toml`.
pub(crate) const FILE_NAME: &str = "er-invasion-warp-band-census.txt";

/// The census, created the first time the panel opens.
static CENSUS: Mutex<Option<Census>> = Mutex::new(None);
/// The query out now and its Steam call handle.
static IN_FLIGHT: Mutex<Option<(Query, u64)>> = Mutex::new(None);
/// Set when the panel first opens; the census asks nothing before it.
static ARMED: AtomicBool = AtomicBool::new(false);

/// The panel's keys, published by the game task for the renderer, as virtual-key codes.
static MENU_KEY: AtomicI32 = AtomicI32::new(er_invasion_warp_core::keybind::VK_F8);
static VIEW_KEY: AtomicI32 = AtomicI32::new(er_invasion_warp_core::keybind::VK_R);

/// What the renderer draws. Rebuilt by the game task while the panel is open.
static VIEW: Mutex<Option<PanelView>> = Mutex::new(None);

/// Everything the panel shows, owned, so the renderer never takes the census lock.
#[derive(Clone, Debug, Default)]
pub(crate) struct PanelView {
    /// The grid in each view, `None` while that view has nothing to show yet.
    pub(crate) hosts: Option<Vec<Cell>>,
    pub(crate) rate: Option<Vec<Cell>>,
    /// Hosts in every band in the last finished sweep.
    pub(crate) total: usize,
    /// Bands answered in the sweep under way.
    pub(crate) progress: Option<usize>,
    /// Minutes the rate is averaged over.
    pub(crate) watched_minutes: f64,
    /// This character's own band index, to mark its cell.
    pub(crate) own_band: Option<usize>,
    /// The counts are limited to this player's own matchmaking pool. False until Seamless has
    /// handed its key to Steam, and then the counts cover every pool.
    pub(crate) pool_known: bool,
    /// A search is running, so the census is holding off the interface.
    pub(crate) waiting_for_search: bool,
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Milliseconds since this module first asked, the census's clock.
fn now_ms() -> u64 {
    static START: OnceLock<Instant> = OnceLock::new();
    u64::try_from(START.get_or_init(Instant::now).elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn path() -> PathBuf {
    crate::local_invasion_filter::config_path().with_file_name(FILE_NAME)
}

/// Start counting: called when the panel opens. The first call reads the saved arrivals.
pub(crate) fn arm() {
    if ARMED.swap(true, Ordering::AcqRel) {
        return;
    }
    let arrivals = std::fs::read_to_string(path())
        .map(|text| Arrivals::from_text(&text))
        .unwrap_or_default();
    crate::standalone_log(format_args!(
        "band-census: armed by the panel. One lobby query per band while nothing is searching, \
         a sweep a minute; {:.0} minute(s) of arrivals carried over from {}",
        arrivals.minutes,
        path().display()
    ));
    *lock(&CENSUS) = Some(Census::new(arrivals));
}

/// Whether a census query is out, so nothing else sends on the interface until it is collected.
#[must_use]
pub(crate) fn in_flight() -> bool {
    lock(&IN_FLIGHT).is_some()
}

/// Send the next query or collect the one out. Called from `lobby_preflight::tick`, on the game
/// task, only while the session is idle and the sweep has nothing to do.
pub(crate) fn tick() {
    if !ARMED.load(Ordering::Acquire) {
        return;
    }
    let out = *lock(&IN_FLIGHT);
    match out {
        Some((query, call)) => {
            let Some(answer) = crate::lobby_preflight::poll_band_query(call) else {
                return;
            };
            *lock(&IN_FLIGHT) = None;
            let finished = {
                let mut slot = lock(&CENSUS);
                let Some(census) = slot.as_mut() else {
                    return;
                };
                let sweeps = census.sweeps;
                census.answer(now_ms(), &answer);
                if matches!(answer, Answer::Failed) {
                    crate::standalone_log(format_args!(
                        "band-census: the query for band {} did not come back; the sweep starts \
                         over in {} s and counts no arrivals across the gap",
                        query.band_value(),
                        census::RETRY_AFTER_FAILURE_MS / 1000
                    ));
                }
                (census.sweeps != sweeps).then(|| {
                    (
                        census.arrivals.to_text(),
                        census.snapshot().map_or(0, census::Snapshot::total),
                    )
                })
            };
            if let Some((text, total)) = finished {
                save(&text, total);
            }
        }
        None => {
            let query = {
                let mut slot = lock(&CENSUS);
                let Some(census) = slot.as_mut() else {
                    return;
                };
                census.due(now_ms())
            };
            let Some(query) = query else {
                return;
            };
            let dlc = match query.split {
                Split::Whole => None,
                Split::Dlc(value) => Some(value),
            };
            // Steam not resolvable this tick: nothing went out, and the query keeps its turn.
            if let Some(call) = crate::lobby_preflight::send_band_query(&query.band_value(), dlc) {
                *lock(&IN_FLIGHT) = Some((query, call));
            }
        }
    }
}

/// Write the arrivals after a finished sweep. Once a minute at most, from the game task.
fn save(text: &str, total: usize) {
    static FIRST_SAID: AtomicBool = AtomicBool::new(false);
    if let Err(error) = er_hotkey_config::write_atomic(&path(), text) {
        crate::standalone_log(format_args!(
            "band-census: could not save {}: {error}",
            path().display()
        ));
        return;
    }
    if !FIRST_SAID.swap(true, Ordering::Relaxed) {
        crate::standalone_log(format_args!(
            "band-census: first sweep finished, {total} open host(s) across every band; saved to \
             {}",
            path().display()
        ));
    }
}

/// Publish the panel's keys from the config, and while the panel is open its view. Called once per
/// game tick.
pub(crate) fn publish(config: Option<&er_invasion_warp_core::local_invasion::LocalInvasionConfig>) {
    if let Some(config) = config {
        MENU_KEY.store(config.band_census_key, Ordering::Relaxed);
        VIEW_KEY.store(config.band_census_view_key, Ordering::Relaxed);
    }
    if !crate::band_panel::is_open() {
        return;
    }
    let view = {
        let slot = lock(&CENSUS);
        let Some(census) = slot.as_ref() else {
            return;
        };
        PanelView {
            hosts: census::cells(census, View::Hosts),
            rate: census::cells(census, View::Rate),
            total: census.snapshot().map_or(0, census::Snapshot::total),
            progress: census.progress(),
            watched_minutes: census.arrivals.minutes,
            own_band: crate::invade_difficulty::own_bands()
                .map(|(level, weapon)| census::band_index(level as usize, weapon as usize)),
            pool_known: crate::lobby_publish::seamless_match_key().is_some(),
            waiting_for_search: !crate::local_invasion_filter::session_is_idle(),
        }
    };
    *lock(&VIEW) = Some(view);
}

/// The view the game task last published.
#[must_use]
pub(crate) fn view() -> Option<PanelView> {
    lock(&VIEW).clone()
}

/// Sweeps finished and queries that failed this session, for the heartbeat; both zero until the
/// panel first opens.
#[must_use]
pub(crate) fn tallies() -> (u64, u64) {
    lock(&CENSUS)
        .as_ref()
        .map_or((0, 0), |census| (census.sweeps, census.failures))
}

/// The menu key and the view key in force.
#[must_use]
pub(crate) fn keys() -> (i32, i32) {
    (
        MENU_KEY.load(Ordering::Relaxed),
        VIEW_KEY.load(Ordering::Relaxed),
    )
}
