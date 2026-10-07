//! Companion dressing, game side: fetch each companion's build, resolve it against the game's own
//! tables, and hold the `CharaInitParam` values its summon is built with.
//!
//! Three stages, split by what each may touch:
//!
//! * [`configure`] runs on a config reload. It reads each companion's URL with the importer's
//!   gate and starts one fetch thread per new URL. A changed URL makes the old fetch's result
//!   stale, which the generation number drops.
//! * The fetch thread does the HTTPS get and the parse (`er_build_import_core::model::parse`, the
//!   same parser the Load Build from URL row uses). It touches no game state.
//! * [`tick`] runs on the game task. Once the param tables and the message repository are up, it
//!   builds the importer's catalog once (names to item ids, from the game's own FMGs and params),
//!   turns each parsed build into row values (`er_npc_summons_core::dress`) and clamps every
//!   armament level against `ReinforceParamWeapon`.
//!
//! [`gear_for`] is what the `CreateSummonChr` detour asks. Every outcome, applied or not, is a
//! line in `er-npc-summons.log`.

#![cfg(windows)]

use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};

use er_build_import_core::catalog::MapCatalog;
use er_build_import_core::model::{self, BuildDoc};
use er_build_import_core::{API_HOST, build_path};
use er_build_import_runtime::catalog::{self, ReinforceLevels};
use er_npc_summons_core::config::Companion;
use er_npc_summons_core::dress::{self, CharaInitGear, Dressing};

use crate::log::summons_log;

/// Identifies this client to the planner's API, which is run for free.
const USER_AGENT: &str = "er-mods-rs npc-summons (+github.com/Banon-Labs)";

enum Status {
    Fetching,
    Parsed(Box<BuildDoc>),
    Ready { gear: CharaInitGear, build: String },
    Failed(String),
}

struct Entry {
    slot: u8,
    name: String,
    url: String,
    generation: u64,
    status: Status,
}

static ENTRIES: Mutex<Vec<Entry>> = Mutex::new(Vec::new());
static GENERATION: AtomicU64 = AtomicU64::new(0);
/// Built once: the item names and ids do not change while the game runs.
static CATALOG: Mutex<Option<MapCatalog>> = Mutex::new(None);

/// Follow a config reload: keep every companion whose URL did not change, fetch the rest.
pub(crate) fn configure(companions: &[Companion]) {
    let Ok(mut entries) = ENTRIES.lock() else {
        return;
    };
    let mut previous = std::mem::take(&mut *entries);
    for companion in companions {
        let label = format!("companion {} ({})", companion.slot, companion.name);
        let url = companion.build_url.clone().unwrap_or_default();
        match dress::dressing(companion) {
            Dressing::Own => {
                summons_log(format_args!(
                    "dress: {label}: no build_url, it wears its body's own CharaInitParam gear"
                ));
            }
            Dressing::Refused(why) => {
                summons_log(format_args!(
                    "dress: {label}: build_url refused ({}); it wears its body's own gear",
                    why.indicator()
                ));
                entries.push(Entry {
                    slot: companion.slot,
                    name: companion.name.clone(),
                    url,
                    generation: 0,
                    status: Status::Failed(format!("build_url refused: {}", why.indicator())),
                });
            }
            Dressing::Build(share_id) => {
                if let Some(index) = previous
                    .iter()
                    .position(|e| e.slot == companion.slot && e.url == url)
                {
                    let mut kept = previous.swap_remove(index);
                    kept.name = companion.name.clone();
                    entries.push(kept);
                    continue;
                }
                let generation = GENERATION.fetch_add(1, Ordering::AcqRel) + 1;
                entries.push(Entry {
                    slot: companion.slot,
                    name: companion.name.clone(),
                    url,
                    generation,
                    status: Status::Fetching,
                });
                summons_log(format_args!("dress: {label}: fetching build {share_id}"));
                let slot = companion.slot;
                let _ = std::thread::Builder::new()
                    .name("er-npc-summons-fetch".to_owned())
                    .spawn(move || fetch(slot, generation, &share_id));
            }
        }
    }
}

fn settle(slot: u8, generation: u64, status: Status) {
    let Ok(mut entries) = ENTRIES.lock() else {
        return;
    };
    if let Some(entry) = entries
        .iter_mut()
        .find(|e| e.slot == slot && e.generation == generation)
    {
        entry.status = status;
    }
}

/// The fetch thread: HTTPS get and parse, no game state.
fn fetch(slot: u8, generation: u64, share_id: &str) {
    let body = match er_game_base::http::get(API_HOST, &build_path(share_id), USER_AGENT) {
        Ok(body) => body,
        Err(err) => {
            let why = format!("fetching build {share_id} failed: {err}");
            summons_log(format_args!(
                "dress: companion {slot}: {why}; it wears its body's own gear"
            ));
            return settle(slot, generation, Status::Failed(why));
        }
    };
    match model::parse(&body) {
        Ok(doc) => {
            summons_log(format_args!(
                "dress: companion {slot}: build {share_id} fetched ({} bytes), {:?}",
                body.len(),
                doc.name
            ));
            settle(slot, generation, Status::Parsed(Box::new(doc)));
        }
        Err(err) => {
            let why = format!("build {share_id} did not parse: {err}");
            summons_log(format_args!(
                "dress: companion {slot}: {why}; it wears its body's own gear"
            ));
            settle(slot, generation, Status::Failed(why));
        }
    }
}

/// One frame on the game task: resolve every fetched build once the game can name items.
pub(crate) fn tick() {
    let Ok(mut entries) = ENTRIES.lock() else {
        return;
    };
    if !entries
        .iter()
        .any(|e| matches!(e.status, Status::Parsed(_)))
    {
        return;
    }
    if !catalog::params_ready() {
        return;
    }
    let Ok(mut cached) = CATALOG.lock() else {
        return;
    };
    if cached.is_none() {
        let Some(msg) = catalog::msg_repository() else {
            return;
        };
        let Ok(module_base) = er_game_base::mem::game_module_base() else {
            return;
        };
        // SAFETY: game task thread; `params_ready` proved the tables are streamed and `msg` is
        // the live message repository.
        let (built, stats, _quivers) = unsafe { catalog::build_from_game(msg, module_base) };
        summons_log(format_args!(
            "dress: item catalog built from the game, {} named entries",
            stats.named
        ));
        *cached = Some(built);
    }
    let Some(catalog) = cached.as_ref() else {
        return;
    };
    let levels = ReinforceLevels::read();
    for entry in entries.iter_mut() {
        let Status::Parsed(doc) = &entry.status else {
            continue;
        };
        let gear = dress::gear_from_build(doc, catalog);
        let values = gear.chara_init(|param_id, requested, character_default| {
            levels.game_level_for(param_id, requested, character_default)
        });
        let label = format!("companion {} ({})", entry.slot, entry.name);
        for line in &gear.not_applied {
            summons_log(format_args!("dress: {label}: not applied: {line}"));
        }
        entry.status = if gear.is_empty() {
            summons_log(format_args!(
                "dress: {label}: build {:?} wears nothing a CharaInitParam row can carry; it \
                 wears its body's own gear",
                doc.name
            ));
            Status::Failed(format!("build {:?} resolved to no gear", doc.name))
        } else {
            summons_log(format_args!(
                "dress: {label}: build {:?} resolved: {}",
                doc.name,
                gear.describe(&values)
            ));
            Status::Ready {
                gear: values,
                build: doc.name.clone(),
            }
        };
    }
}

/// The row values for the companion in config `slot`, with its build's name, or why there are
/// none and the companion keeps its body's own gear.
pub(crate) fn gear_for(slot: u8) -> Result<(CharaInitGear, String), String> {
    let entries = ENTRIES
        .lock()
        .map_err(|_| "the dressing table is poisoned".to_owned())?;
    let entry = entries
        .iter()
        .find(|e| e.slot == slot)
        .ok_or_else(|| "no build_url".to_owned())?;
    match &entry.status {
        Status::Ready { gear, build } => Ok((*gear, build.clone())),
        Status::Fetching => Err("its build is still being fetched".to_owned()),
        Status::Parsed(_) => Err("its build is fetched but the item catalog is not up".to_owned()),
        Status::Failed(why) => Err(why.clone()),
    }
}
