//! Import an Elden Ring build from an `er-build-planner` share link, into the character that is
//! already in the world.
//!
//! # Shape
//!
//! Two threads, split by what they are allowed to touch:
//!
//! * a **fetch worker** does the blocking HTTPS GET and parses the payload. It touches no game
//!   state at all, which is why it may block.
//! * a **game-thread step**, [`tick`], does everything else. Building the catalog reads the param
//!   tables and the message repository; granting mutates the inventory and the `CSGaitemImp`
//!   singleton; stats, spells and equipment all call native functions. All of that belongs on the
//!   thread the game runs its own tasks on.
//!
//! Neither sleeps. The worker blocks in WinHTTP; [`tick`] re-checks its preconditions once a frame
//! and does nothing until they hold, which is the natural shape here and also what
//! `scripts/check-no-timeouts.py` requires.
//!
//! # Why this is a library and not just the DLL
//!
//! There are two callers with the same needs and different triggers:
//!
//! * `er-build-import-dll` -- a standalone ME3 shell that imports the build named in
//!   `er-effects.toml` once, as soon as a character is in the world.
//! * `er-effects-rs` -- the product DLL, whose System>Quit **Load Build from URL** row imports on
//!   demand, as many times as the player asks.
//!
//! The second is why [`request`] exists at all: the original code ran exactly once per process,
//! latched by an `AtomicBool`. That latch is now a phase machine ([`Phase`]) whose terminal states
//! -- [`Phase::Done`] and [`Phase::Failed`] -- are ones [`request`] accepts, so a second import is
//! an ordinary state transition rather than a special case. Nothing resets the machine on a timer:
//! it stays on its last outcome until someone asks for another build.

#![cfg(windows)]

pub mod catalog;
pub mod character;
pub mod equip_native;
pub mod grant;
pub mod http;

use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use er_build_import::equip::{Capacity, equip_plan};
use er_build_import::{API_HOST, BuildDoc, build_path, model, plan::plan};

use windows::Win32::System::LibraryLoader::GetModuleHandleW;

/// Config key naming the planner build to import, re-exported so callers naming it in a log line
/// or a menu message cannot drift from the parser.
pub use er_build_import::BUILD_URL_KEY;

/// The config file, beside the game executable.
pub const CONFIG_FILE_NAME: &str = "er-effects.toml";

/// Identifies this client to the API owner, who runs the service for free.
const USER_AGENT: &str = "er-effects-rs build-import (+github.com/Banon-Labs)";

/// Log file name, written next to the game executable.
const LOG_NAME: &str = "er-build-import.log";

// ------------------------------------------------------------------ state

/// Where the importer is. Encoded as a `usize` so the whole machine is one lock-free atomic that a
/// menu row, a fetch worker and the game task can all read without ordering games.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(usize)]
pub enum Phase {
    /// Nothing in flight. The only phase [`request`] accepts.
    Idle = 0,
    /// A worker is blocked in WinHTTP.
    Fetching = 1,
    /// A build is parsed and waiting for the game thread to be able to apply it.
    Ready = 2,
    /// [`tick`] is inside the import.
    Importing = 3,
    /// The last import finished. The report was returned to whoever called [`tick`]; this phase is
    /// only "not busy, and the last thing that happened was a success".
    Done = 4,
    /// The last request failed before anything was applied; [`take_error`] says why, once.
    Failed = 5,
}

impl Phase {
    fn from_code(code: usize) -> Phase {
        match code {
            1 => Phase::Fetching,
            2 => Phase::Ready,
            3 => Phase::Importing,
            4 => Phase::Done,
            5 => Phase::Failed,
            _ => Phase::Idle,
        }
    }
}

static PHASE: AtomicUsize = AtomicUsize::new(Phase::Idle as usize);

/// The parsed build, handed from the fetch worker to the game task.
static DOC: Mutex<Option<BuildDoc>> = Mutex::new(None);

/// Why the last request failed. Kept separate from the report so a failure cannot be read as a
/// report with zero counts -- "granted 0/0" and "the fetch 404'd" are not the same event.
static LAST_ERROR: Mutex<Option<String>> = Mutex::new(None);

/// What one completed import actually did, measured by reading game memory back -- never by a call
/// having returned. Every field here is a read-back count.
#[derive(Clone, Debug, Default)]
pub struct Report {
    /// The build's own name, as the planner stored it.
    pub build_name: String,
    /// Items confirmed present in the inventory afterwards, out of those attempted.
    pub granted: (usize, usize),
    /// Equipment slots holding the requested param id afterwards, out of those requested.
    pub equipped: (usize, usize),
    /// Spells confirmed in a memory slot afterwards, out of those wanted.
    pub spells: (usize, usize),
    /// Physick tears confirmed in the flask afterwards, out of those wanted.
    pub physick: (usize, usize),
    /// Attributes still disagreeing with the build after the read-back. Zero means the character
    /// matches.
    pub attributes_wrong: usize,
    /// Character level after the import, read back from the player's game data.
    pub level: i32,
    /// Names the catalog could not resolve to an item id.
    pub unresolved: usize,
}

impl Report {
    /// One line for a menu help field or a log: what a player wants to know is whether it worked.
    pub fn summary(&self) -> String {
        format!(
            "{}/{} items, {}/{} gear, {}/{} spells, RL{}{}",
            self.granted.0,
            self.granted.1,
            self.equipped.0,
            self.equipped.1,
            self.spells.0,
            self.spells.1,
            self.level,
            if self.attributes_wrong == 0 {
                String::new()
            } else {
                format!(", {} attributes WRONG", self.attributes_wrong)
            }
        )
    }
}

/// Current phase.
pub fn phase() -> Phase {
    Phase::from_code(PHASE.load(Ordering::SeqCst))
}

/// TAKE the reason the last request failed, clearing it.
///
/// Taking rather than peeking, because the failure is ASYNCHRONOUS -- the fetch worker fails long
/// after [`request`] returned `Ok` -- so the only way a caller learns about it is by polling. A
/// peek would re-report the same failure on every frame; taking it reports each failure exactly
/// once, which is what a log wants.
pub fn take_error() -> Option<String> {
    LAST_ERROR.lock().ok().and_then(|mut guard| guard.take())
}

fn set_error(reason: String) {
    log_line(&format!("[build-import] FAILED: {reason}"));
    if let Ok(mut slot) = LAST_ERROR.lock() {
        *slot = Some(reason);
    }
    PHASE.store(Phase::Failed as usize, Ordering::SeqCst);
}

// ------------------------------------------------------------------ config

/// Read `build_url` out of the game-directory `er-effects.toml`.
///
/// The file lookup lives here because it needs the game directory; the PARSING lives in
/// `er-build-import`, which `cargo test` can reach.
pub fn configured_build_url() -> Option<String> {
    let path = er_game_base::log::game_directory_path()?.join(CONFIG_FILE_NAME);
    let contents = std::fs::read_to_string(path).ok()?;
    er_build_import::build_url_from_config(&contents).map(str::to_owned)
}

// ------------------------------------------------------------------ request

/// Why [`request`] refused. A refusal never changes [`phase`].
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RequestError {
    /// A fetch or import is already in flight.
    Busy,
    /// The URL carries no `?b=<id>`. The self-contained `?i=` form needs no network at all and is
    /// not supported here.
    NoShareId,
}

impl core::fmt::Display for RequestError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            RequestError::Busy => write!(f, "a build import is already in flight"),
            RequestError::NoShareId => {
                write!(
                    f,
                    "that link carries no ?b=<id> (the ?i= form is not supported)"
                )
            }
        }
    }
}

/// Start importing the build named by `url`. Returns as soon as the worker is spawned; the game
/// thread must keep calling [`tick`] for the import to actually happen.
///
/// Safe to call from any thread, including a menu action handler: nothing here touches game state.
pub fn request(url: &str) -> Result<(), RequestError> {
    let Some(share_id) = er_build_import::share_id_from_url(url) else {
        return Err(RequestError::NoShareId);
    };
    // Claim Idle/Done/Failed -> Fetching atomically. Losing this race means another caller (or the
    // standalone shell's boot import) already owns the machine.
    let claimed = [Phase::Idle, Phase::Done, Phase::Failed]
        .into_iter()
        .any(|from| {
            PHASE
                .compare_exchange(
                    from as usize,
                    Phase::Fetching as usize,
                    Ordering::SeqCst,
                    Ordering::SeqCst,
                )
                .is_ok()
        });
    if !claimed {
        return Err(RequestError::Busy);
    }
    if let Ok(mut slot) = LAST_ERROR.lock() {
        *slot = None;
    }
    let share_id = share_id.to_owned();
    std::thread::spawn(move || {
        if std::panic::catch_unwind(|| fetch_inner(&share_id)).is_err() {
            set_error("fetch worker PANICKED".to_owned());
        }
    });
    Ok(())
}

/// Start importing the build configured in `er-effects.toml`, if there is one. Returns `Ok(false)`
/// when the key is absent, which is the normal state for a player who has not set one.
pub fn request_configured() -> Result<bool, RequestError> {
    let Some(url) = configured_build_url() else {
        log_line(&format!(
            "[build-import] no `{BUILD_URL_KEY}` in {CONFIG_FILE_NAME} -- nothing to import"
        ));
        return Ok(false);
    };
    request(&url).map(|()| true)
}

/// The fetch proper. Runs on the worker thread; touches no game state.
fn fetch_inner(share_id: &str) {
    log_line(&format!("[build-import] fetch start, build {share_id}"));
    log_line(&format!(
        "[build-import] GET https://{API_HOST}{}",
        build_path(share_id)
    ));

    let body = match http::get(API_HOST, &build_path(share_id), USER_AGENT) {
        Ok(body) => body,
        Err(err) => return set_error(format!("fetch: {err}")),
    };
    log_line(&format!("[build-import] fetch ok, {} bytes", body.len()));

    let doc = match model::parse(&body) {
        Ok(doc) => doc,
        Err(err) => return set_error(format!("parse: {err}")),
    };

    let armour: usize = doc.protectors.values().map(|part| part.slots.len()).sum();
    log_line(&format!(
        "[build-import] parsed name={:?} class={:?} rl={:?} weaponUpgrade={}",
        doc.name,
        doc.character_class,
        doc.stats.get("rl"),
        doc.weapon_upgrade
    ));
    log_line(&format!(
        "[build-import] slots armaments={} spells={} talismans={} armour={} tools={}",
        doc.inventory.slots.len(),
        doc.spells.slots.len(),
        doc.talismans.slots.len(),
        armour,
        doc.items.tools.slots.len()
    ));

    // For every starting class the eight attributes sum to level + 79, so a payload failing this is
    // internally inconsistent and must not be imported.
    let attrs: i64 = ["vig", "mnd", "vit", "str", "dex", "int", "fth", "arc"]
        .iter()
        .filter_map(|key| doc.stats.get(*key))
        .sum();
    let level = doc.stats.get("rl").copied().unwrap_or_default();
    if level != attrs - 79 {
        return set_error(format!(
            "level {level} != sum(attrs) {attrs} - 79 = {}; the payload is internally inconsistent",
            attrs - 79
        ));
    }
    log_line(&format!(
        "[build-import] level check: {attrs} - 79 = {level} -> CONSISTENT"
    ));

    match DOC.lock() {
        Ok(mut slot) => {
            *slot = Some(doc);
            PHASE.store(Phase::Ready as usize, Ordering::SeqCst);
            log_line("[build-import] build handed to the game task");
        }
        Err(_) => set_error("the build slot is poisoned".to_owned()),
    }
}

// ------------------------------------------------------------------ game thread

/// One frame of the importer. Does nothing until a build is [`Phase::Ready`] AND the game can take
/// it, then runs the whole import once and returns the report.
///
/// # Safety
///
/// Game task thread only. Every step below is individually precondition-checked, but the thread
/// itself is not something this function can verify.
pub unsafe fn tick() -> Option<Report> {
    if phase() != Phase::Ready {
        return None;
    }
    if !catalog::params_ready() {
        return None;
    }
    // Safety: game thread; the helper is fault-checked and returns false at the title screen.
    if !unsafe { grant::player_present() } {
        return None;
    }
    let doc = DOC.lock().ok().and_then(|mut slot| slot.take())?;
    // Claim the run before doing it: a panic must not leave the task retrying every frame.
    PHASE.store(Phase::Importing as usize, Ordering::SeqCst);

    // Safety: the caller's contract (game task thread) carries through.
    let report = unsafe { import_now(&doc) };
    match report {
        Some(report) => {
            PHASE.store(Phase::Done as usize, Ordering::SeqCst);
            log_line(&format!(
                "[build-import] import complete: {}",
                report.summary()
            ));
            Some(report)
        }
        None => {
            set_error(
                "the message repository was unavailable, so no catalog could be built".to_owned(),
            );
            None
        }
    }
}

/// The import proper: catalog, plan, grant, equip, physick, great rune, class, stats, spells.
///
/// # Safety
///
/// Game task thread, params streamed, character in the world -- all three checked by [`tick`].
unsafe fn import_now(doc: &BuildDoc) -> Option<Report> {
    let module_base = module_base();
    let msg = catalog::msg_repository()?;

    // Safety: params_ready() proved the tables are streamed and `msg` came from the singleton.
    let (catalog, stats) = unsafe { catalog::build_from_game(msg, module_base) };
    log_line(&format!(
        "[build-import] catalog: {} named, {} unnamed, {} goods rows are spells",
        stats.named, stats.unnamed, stats.spell_rows
    ));

    let planned = plan(doc, &catalog);
    let equips = equip_plan(doc, &catalog, Capacity::default());
    log_line(&format!(
        "[build-import] planned: {} grants, {} unresolved, {} equip positions, {} rejected",
        planned.grants.len(),
        planned.unresolved.len(),
        equips.occupied(),
        equips.rejected.len()
    ));
    for missing in planned.unresolved.iter().take(12) {
        log_line(&format!(
            "[build-import]   UNRESOLVED {} {:?}",
            missing.kind.label(),
            missing.name
        ));
    }

    let mut report = Report {
        build_name: doc.name.clone(),
        unresolved: planned.unresolved.len(),
        ..Report::default()
    };

    // Safety: game thread, character loaded (checked by the caller), verified RVAs.
    let outcome = unsafe { grant::grant_all(module_base, &planned.grants) };
    report.granted = (outcome.confirmed, outcome.attempted);
    log_line(&format!(
        "[build-import] GRANTED: {}/{} confirmed present in the inventory ({} missing)",
        outcome.confirmed,
        outcome.attempted,
        outcome.missing.len()
    ));
    for id in outcome.missing.iter().take(12) {
        log_line(&format!("[build-import]   MISSING item id 0x{id:08X}"));
    }

    // Equip only what was actually granted: equipping an item the inventory does not hold cannot
    // work, and the outcome distinguishes those from real equip failures.
    if let Some(egd) = unsafe { grant::equip_game_data() } {
        // Safety: game thread, character loaded, items granted above.
        let worn = unsafe { equip_native::equip_all(module_base, egd, &equips) };
        report.equipped = (worn.verified, worn.requested);
        log_line(&format!(
            "[build-import] EQUIPPED (read back from the slots): {}/{} verified, {} silently \
             ignored, {} already correct, {} not in inventory",
            worn.verified,
            worn.requested,
            worn.silent_noop,
            worn.already,
            worn.not_in_inventory.len()
        ));
        for (slot, permitted) in &worn.gate {
            log_line(&format!("[build-import]   gate(slot {slot}) = {permitted}"));
        }
        for (slot, expected, actual) in &worn.mismatches {
            log_line(&format!(
                "[build-import]   SLOT {slot} expected param {expected} but holds {actual}"
            ));
        }
        log_line(&format!(
            "[build-import] QUICKBAR/POUCH/RUNE: {} positions written through the native dispatcher",
            worn.quick_written
        ));
        for (slot, id, idx) in &worn.trace {
            log_line(&format!(
                "[build-import]   dispatch slot {slot} (index {}) item 0x{id:08X} invIdx {idx}",
                slot - 0x16
            ));
        }
        for id in worn.not_in_inventory.iter().take(12) {
            log_line(&format!("[build-import]   NOT-IN-INVENTORY 0x{id:08X}"));
        }

        // Physick: log what the flask held before, so a pre-existing value is never mistaken for
        // something this importer wrote.
        // Safety: game thread, character loaded.
        let before = unsafe { equip_native::read_physick(module_base, egd) };
        let wanted_tears: Vec<(&str, u32)> = equips
            .physick
            .iter()
            .flatten()
            .map(|t| (t.name.as_str(), t.item_id))
            .collect();
        let filled = unsafe { equip_native::fill_physick(module_base, egd, &equips.physick) };
        let after = unsafe { equip_native::read_physick(module_base, egd) };
        report.physick = (filled, wanted_tears.len());
        log_line(&format!(
            "[build-import] PHYSICK: {filled}/{} verified. wants {:?}; flask was {:?} -> now {:?} \
             (-1 = empty)",
            wanted_tears.len(),
            wanted_tears,
            before.map(|v| format!("0x{v:08X}")),
            after.map(|v| format!("0x{v:08X}"))
        ));

        // Great rune: read the equipped rune back and light the rune arc.
        if let Some(rune) = equips.great_rune.as_ref() {
            // Safety: game thread; a native getter plus one bool in live save data.
            let equipped = unsafe { equip_native::equipped_great_rune(module_base, egd) };
            let active = unsafe { character::activate_rune_arc() };
            log_line(&format!(
                "[build-import] GREAT RUNE: {:?} -> GetEquippedGreatrune reports {equipped}, \
                 runeArcActive={active}",
                rune.name
            ));
        }
    }

    // Class BEFORE stats: the level-up menu derives its per-attribute floors from the archetype's
    // CharaInitParam row, so setting the class first means anything that re-reads those floors
    // already sees the right class.
    if let Some(pgd) = unsafe { character::player_game_data() } {
        match doc.character_class.as_deref() {
            Some(class) => match unsafe { character::set_class(pgd, class) } {
                Some((wanted, got)) => log_line(&format!(
                    "[build-import] CLASS: {class} -> archetype {wanted}, read back {got} ({})",
                    if wanted == got { "OK" } else { "MISMATCH" }
                )),
                None => log_line(&format!("[build-import] CLASS: unrecognised {class:?}")),
            },
            None => log_line("[build-import] CLASS: build names none, left alone"),
        }

        // Safety: game thread, character in the world (gated by the caller).
        let stats = unsafe { character::apply_stats(module_base, pgd, doc) };
        report.level = stats.level.1;
        report.attributes_wrong = stats.wrong.len();
        log_line(&format!(
            "[build-import] STATS: level {} -> {} ({} attributes wrong after read-back)",
            stats.level.0,
            stats.level.1,
            stats.wrong.len()
        ));
        for (name, want, got) in &stats.wrong {
            log_line(&format!(
                "[build-import]   {name}: wanted {want}, holds {got}"
            ));
        }
        if stats.is_correct() {
            log_line("[build-import] STATS: every attribute matches the build");
        }
    }

    // Spells last: ApplyMainPlayerStats recomputes the memory-slot count from Mind, so asking the
    // game for capacity before the stats are applied would use the OLD number.
    if let Some(egd) = unsafe { grant::equip_game_data() } {
        // Safety: game thread, character loaded.
        let spells = unsafe { character::memorise_spells(module_base, egd, &equips.spells) };
        report.spells = (spells.verified, spells.wanted);
        log_line(&format!(
            "[build-import] SPELLS (read back): {}/{} memorised, capacity {}, {} over capacity",
            spells.verified, spells.wanted, spells.capacity, spells.over_capacity
        ));
        for (slot, expected, actual) in &spells.mismatches {
            log_line(&format!(
                "[build-import]   SPELL slot {slot} expected {expected} but holds {actual}"
            ));
        }
    }

    Some(report)
}

/// Base address of the loaded game image.
fn module_base() -> usize {
    // Safety: a null module name asks for the process image, which always exists.
    unsafe { GetModuleHandleW(None) }
        .map(|handle| handle.0 as usize)
        .unwrap_or_default()
}

/// Append one line to the log beside the game executable, flushing immediately.
///
/// Per line rather than per run: a probe that dies partway is the interesting case, and a buffered
/// report loses precisely the evidence that matters when it does -- an earlier version accumulated
/// the whole report in a `String` and flushed at the end, so a worker that died left nothing at all
/// and looked exactly like a DLL that never loaded.
///
/// Routed through `er_game_base::log` so the file describes ONE run: the shared helper rotates the
/// previous run's log aside on first write instead of letting runs pile up in one file.
pub fn log_line(line: &str) {
    let path = er_game_base::log::game_directory_path()
        .map(|dir| dir.join(LOG_NAME))
        .unwrap_or_else(|| PathBuf::from(LOG_NAME));
    er_game_base::log::append_line(&path, format_args!("{line}"));
}
