//! Import an Elden Ring build from an `er-build-planner` share link, in-game.
//!
//! # Shape
//!
//! Two threads, split by what they are allowed to touch:
//!
//! * a **fetch worker** does the blocking HTTPS GET and parses the payload. It touches no
//!   game state at all, which is why it may block.
//! * a **`CSTaskImp` FrameBegin task** does everything else. Building the catalogue reads the
//!   param tables and the message repository; granting mutates the inventory and the
//!   `CSGaitemImp` singleton. Both belong on the game thread.
//!
//! Neither sleeps. The worker blocks in WinHTTP; the task re-checks its preconditions once a
//! frame and does nothing until they hold, which is the natural shape here and also what
//! `scripts/check-no-timeouts.py` requires.
//!
//! # What it does not do yet
//!
//! Equipping, spells, quickbar, level, class and great rune are not wired. This build grants
//! items and reads the inventory back to prove they arrived.

#![cfg(windows)]

pub mod catalogue;
pub mod character;
pub mod equip_native;
pub mod grant;
mod http;

use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use er_build_import::equip::{Capacity, equip_plan};
use er_build_import::{API_HOST, BuildDoc, build_path, model, plan::plan};

use windows::Win32::Foundation::{HINSTANCE, TRUE};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::SystemServices::DLL_PROCESS_ATTACH;

/// The build to import.
const PROOF_BUILD_ID: &str = "af97a9da874151";

/// Identifies this client to the API owner, who runs the service for free.
const USER_AGENT: &str = "er-effects-rs build-import probe (+github.com/Banon-Labs)";

/// Log file name, written next to the game executable.
const LOG_NAME: &str = "er-build-import.log";

/// The parsed build, handed from the fetch worker to the game task.
static DOC: Mutex<Option<BuildDoc>> = Mutex::new(None);

/// Set once the import has run, so the task does it exactly once.
static IMPORTED: AtomicBool = AtomicBool::new(false);

/// DLL entry point. Spawns the worker and the task registrar, and returns.
///
/// # Safety
///
/// Called by the loader. Nothing slow or reentrant runs under the loader lock.
#[unsafe(no_mangle)]
pub extern "system" fn DllMain(_module: HINSTANCE, reason: u32, _reserved: *mut ()) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        std::thread::spawn(fetch_worker);
        std::thread::spawn(register_task);
    }
    TRUE.0
}

// ------------------------------------------------------------------ fetch

/// Fetch and parse the build. Runs off the game thread; never touches game state.
fn fetch_worker() {
    if std::panic::catch_unwind(fetch_inner).is_err() {
        log_line("[build-import] fetch worker PANICKED");
    }
}

/// The fetch proper.
fn fetch_inner() {
    log_line(&format!(
        "[build-import] probe start, build {PROOF_BUILD_ID}"
    ));
    log_line(&format!(
        "[build-import] GET https://{API_HOST}{}",
        build_path(PROOF_BUILD_ID)
    ));

    let body = match http::get(API_HOST, &build_path(PROOF_BUILD_ID), USER_AGENT) {
        Ok(body) => body,
        Err(err) => {
            log_line(&format!("[build-import] FETCH FAILED: {err}"));
            return;
        }
    };
    log_line(&format!("[build-import] fetch ok, {} bytes", body.len()));

    let doc = match model::parse(&body) {
        Ok(doc) => doc,
        Err(err) => {
            log_line(&format!("[build-import] PARSE FAILED: {err}"));
            return;
        }
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

    // For every starting class the eight attributes sum to level + 79, so a payload failing
    // this is internally inconsistent and must not be imported.
    let attrs: i64 = ["vig", "mnd", "vit", "str", "dex", "int", "fth", "arc"]
        .iter()
        .filter_map(|key| doc.stats.get(*key))
        .sum();
    let level = doc.stats.get("rl").copied().unwrap_or_default();
    if level != attrs - 79 {
        log_line(&format!(
            "[build-import] REFUSING: level {level} != sum(attrs) {attrs} - 79 = {}",
            attrs - 79
        ));
        return;
    }
    log_line(&format!(
        "[build-import] level check: {attrs} - 79 = {level} -> CONSISTENT"
    ));

    if let Ok(mut slot) = DOC.lock() {
        *slot = Some(doc);
        log_line("[build-import] build handed to the game task");
    }
}

// ------------------------------------------------------------------ game task

/// Register the FrameBegin task that owns every game-touching step.
fn register_task() {
    use eldenring::cs::{CSTaskGroupIndex, CSTaskImp};
    use eldenring::fd4::FD4TaskData;
    use fromsoftware_shared::{FromStatic, SharedTaskImpExt};

    let task = loop {
        match unsafe { CSTaskImp::instance() } {
            Ok(task) => break task,
            // No sleep (banned by scripts/check-no-timeouts.py): yield and re-poll, the same
            // shape er-invasion-warp-dll and er-telemetry-dll use.
            Err(_) => std::thread::yield_now(),
        }
    };
    log_line("[build-import] CSTaskImp resolved; registering FrameBegin import task");

    let handle = task.run_recurring(
        move |_data: &FD4TaskData| {
            // Safety: this closure runs on the game task thread, which is the context every
            // step below requires; each step is individually precondition-checked.
            unsafe { import_tick() };
        },
        CSTaskGroupIndex::FrameBegin,
    );
    // The handle cancels the task on drop, and the task must outlive this bootstrap thread.
    std::mem::forget(handle);
}

/// One frame of the importer. Does nothing until every precondition holds, then runs once.
///
/// # Safety
///
/// Game task thread only.
unsafe fn import_tick() {
    if IMPORTED.load(Ordering::Relaxed) {
        return;
    }
    if !catalogue::params_ready() {
        return;
    }
    // Safety: game thread; the helper is fault-checked and returns None at the title screen.
    if !unsafe { grant::player_present() } {
        return;
    }
    let Some(doc) = DOC.lock().ok().and_then(|mut slot| slot.take()) else {
        return;
    };
    // Claim the run before doing it: a panic must not leave the task retrying every frame.
    IMPORTED.store(true, Ordering::Relaxed);

    let module_base = module_base();
    let Some(msg) = catalogue::msg_repository() else {
        log_line("[build-import] message repository unavailable -- no catalogue");
        return;
    };

    // Safety: params_ready() proved the tables are streamed and `msg` came from the singleton.
    let (catalog, stats) = unsafe { catalogue::build_from_game(msg, module_base) };
    log_line(&format!(
        "[build-import] catalogue: {} named, {} unnamed, {} goods rows are spells",
        stats.named, stats.unnamed, stats.spell_rows
    ));

    let planned = plan(&doc, &catalog);
    let equips = equip_plan(&doc, &catalog, Capacity::default());
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

    // Safety: game thread, character loaded (player_present above), verified RVAs.
    let outcome = unsafe { grant::grant_all(module_base, &planned.grants) };
    log_line(&format!(
        "[build-import] GRANTED: {}/{} confirmed present in the inventory ({} missing)",
        outcome.confirmed,
        outcome.attempted,
        outcome.missing.len()
    ));
    for id in outcome.missing.iter().take(12) {
        log_line(&format!("[build-import]   MISSING item id 0x{id:08X}"));
    }
    // Equip only what was actually granted: equipping an item the inventory does not hold
    // cannot work, and the outcome distinguishes those from real equip failures.
    if let Some(egd) = unsafe { grant::equip_game_data() } {
        // Safety: game thread, character loaded, items granted above.
        let worn = unsafe { equip_native::equip_all(module_base, egd, &equips) };
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

        // Physick: log what the flask held before, so a pre-existing value is never mistaken
        // for something this importer wrote.
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

    // Class BEFORE stats: the level-up menu derives its per-attribute floors from the
    // archetype's CharaInitParam row, so setting the class first means anything that re-reads
    // those floors already sees the right class.
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

        // Safety: game thread, character in the world (gated above).
        let stats = unsafe { character::apply_stats(module_base, pgd, &doc) };
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

    // Spells last: ApplyMainPlayerStats recomputes the memory-slot count from Mind, so asking
    // the game for capacity before the stats are applied would use the OLD number.
    if let Some(egd) = unsafe { grant::equip_game_data() } {
        // Safety: game thread, character loaded.
        let spells = unsafe { character::memorise_spells(module_base, egd, &equips.spells) };
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

    log_line("[build-import] import tick complete");
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
/// Per line rather than per run: a probe that dies partway is the interesting case, and a
/// buffered report loses precisely the evidence that matters when it does -- an earlier version
/// accumulated the whole report in a `String` and flushed at the end, so a worker that died
/// left nothing at all and looked exactly like a DLL that never loaded.
///
/// Routed through `er_game_base::log` so the file describes ONE run: the shared helper rotates
/// the previous run's log aside on first write instead of letting runs pile up in one file.
fn log_line(line: &str) {
    let path = er_game_base::log::game_directory_path()
        .map(|dir| dir.join(LOG_NAME))
        .unwrap_or_else(|| PathBuf::from(LOG_NAME));
    er_game_base::log::append_line(&path, format_args!("{line}"));
}
