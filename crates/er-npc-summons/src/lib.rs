//! `er_npc_summons.dll`: NPC duel signs and a custom Mimic Tear.
//!
//! Design: `docs/plans/npc-duel-signs-and-custom-mimic.md`. Every decision (the config, the duel
//! state machine, the hidden-NPC verdict, the companion plan) is in `er-npc-summons-core` and is
//! host-tested; this crate carries it into the game.
//!
//! # What this DLL does to the game
//!
//! * Duels: the Duelist's Furled Finger ([`finger`]) opens a picker ([`picker`]) instead of placing
//!   the player's own red sign. The chosen NPC is created through the spirit-ash spawn call and
//!   disabled before any frame draws it; once its model is loaded a red NPC summon sign keyed to it
//!   is placed at the player's feet ([`game`]). Touching the sign is the game's own phantom join.
//!   The NPC is registered under its own entity id and unlinked from the player's summon group,
//!   so neither its sign nor the spirit-ash HUD can mistake it for a companion. While the picker
//!   is open the game gets neither its keys and buttons ([`pad`], `er-dinput-suppress-core`) nor
//!   the mouse ([`cursor`]).
//! * The finger is still refused where the game bars summoning ([`finger`]).
//! * Mimic Tear: four detours rewrite what `BuddyGenerator` summons for a Mimic Tear request
//!   ([`mimic_hooks`]), and each companion with a `build_url` is built wearing that build's gear
//!   ([`dress`]), carries its configured name ([`names`]) and, with `ai = { brain = "x" }`, runs
//!   a Lua brain loaded into the AI state ([`brains`]).
//!
//! # The log is the oracle
//!
//! `er-npc-summons.log` beside the game records the config as read, every hook install, every
//! duel transition with the character's address, and every Mimic Tear summon.

mod addr;
mod log;

#[cfg(windows)]
mod brains;
#[cfg(windows)]
mod cursor;
#[cfg(windows)]
mod dress;
#[cfg(windows)]
mod finger;
#[cfg(windows)]
mod game;
#[cfg(windows)]
mod mimic_hooks;
#[cfg(windows)]
mod names;
#[cfg(windows)]
mod overlay;
#[cfg(windows)]
mod pad;
#[cfg(windows)]
mod picker;

#[cfg(windows)]
use std::sync::Once;
#[cfg(windows)]
use std::time::SystemTime;

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use er_npc_summons_core::config::{Ai, Body, Config};
#[cfg(windows)]
use er_npc_summons_core::duel::{Action, Duel, Event, State};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};
#[cfg(windows)]
use windows::Win32::{Foundation::HINSTANCE, System::SystemServices::DLL_PROCESS_ATTACH};

#[cfg(windows)]
use crate::log::{reset_log_file, summons_log};

const DLL_MAIN_SUCCESS: i32 = 1;

/// The config file, beside the game executable.
#[cfg(windows)]
const CONFIG_FILE_NAME: &str = "er-npc-summons.toml";
/// Frames between config file checks.
#[cfg(windows)]
const CONFIG_POLL_FRAMES: u32 = 60;
/// `PartyMemberInfo` state of a joined phantom.
#[cfg(windows)]
const PARTY_JOINED: i32 = 4;
/// How far from the player the hidden NPC and its sign go, in metres along physics x.
#[cfg(windows)]
const SIGN_DISTANCE_M: f32 = 1.5;

#[cfg(windows)]
static START: Once = Once::new();

/// What the game task carries between frames.
#[cfg(windows)]
struct TaskState {
    config: Config,
    config_stamp: Option<SystemTime>,
    frames: u32,
    duel: Duel,
    edges: picker::Edges,
    /// Where the current duel's NPC and sign stand (physics space).
    sign_at: Option<game::Vec4>,
    /// The current duel NPC's event entity, read once it exists (`duel::DUEL_ENTITY_ID` when the
    /// spawn went as intended); the key its sign is placed under.
    entity: Option<u32>,
    /// The current duel NPC's `FieldInsHandle`, the key its liveness is judged by. An entity
    /// lookup is not: on 2026-10-06 every summon-path character carried entity 35000, and the
    /// lookup found a live Mimic companion in the duel NPC's place (bd `er-effects-rs-gqu9`).
    handle: Option<u64>,
    /// The roster name of the NPC being spawned, from the pick.
    picked_name: Option<String>,
}

#[cfg(windows)]
fn config_path() -> std::path::PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(CONFIG_FILE_NAME)
}

/// Re-read the config when the file changed. A missing file is the defaults.
#[cfg(windows)]
fn refresh_config(state: &mut TaskState) {
    let path = config_path();
    let stamp = std::fs::metadata(&path).and_then(|m| m.modified()).ok();
    if stamp == state.config_stamp && state.frames > 0 {
        return;
    }
    state.config_stamp = stamp;
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let config = Config::parse(&text);
    for problem in &config.problems {
        summons_log(format_args!("config: {problem}"));
    }
    summons_log(format_args!(
        "config: {} -- duels {} ({} NPC(s)), mimic {} ({} companion(s))",
        if stamp.is_some() {
            path.display().to_string()
        } else {
            "no file, defaults".to_owned()
        },
        if config.duel.enabled { "on" } else { "off" },
        config.duel.roster.len(),
        if config.mimic.enabled { "on" } else { "off" },
        config.mimic.companions.len()
    ));
    finger::set_enabled(config.duel.enabled);
    let companions: &[er_npc_summons_core::config::Companion] = if config.mimic.enabled {
        &config.mimic.companions
    } else {
        &[]
    };
    names::set_companions(
        companions
            .iter()
            .map(|companion| (companion.slot, companion.name.clone()))
            .collect(),
    );
    brains::configure(
        companions
            .iter()
            .filter_map(|companion| match &companion.ai {
                Ai::Brain(name) => Some((name.clone(), companion.think())),
                Ai::Native | Ai::LikeNpc(_) => None,
            })
            .collect(),
    );
    let plan = if config.mimic.enabled {
        dress::configure(&config.mimic.companions);
        er_npc_summons_core::mimic::plan(&config.mimic.companions)
    } else {
        dress::configure(&[]);
        Vec::new()
    };
    mimic_hooks::set_plan(plan);
    if state.duel.state == State::Idle {
        state.duel = Duel::new(config.duel.roster.len());
    }
    state.config = config;
}

#[cfg(windows)]
fn bodies(config: &Config) -> Vec<Body> {
    config.duel.roster.iter().map(|npc| npc.body).collect()
}

/// Perform the machine's actions; returns any event an action produced.
#[cfg(windows)]
fn perform(state: &mut TaskState, actions: Vec<Action>) -> Option<Event> {
    let mut follow = None;
    for action in actions {
        match action {
            Action::OpenPicker => {
                let names = state
                    .config
                    .duel
                    .roster
                    .iter()
                    .map(|npc| npc.name.clone())
                    .collect();
                picker::open(names);
                summons_log(format_args!("duel: finger used, picker open"));
            }
            Action::ClosePicker => picker::close(),
            Action::Spawn(body) => {
                let at = game::main_player()
                    .and_then(game::physics_pos)
                    .map(|mut pos| {
                        pos.0[0] += SIGN_DISTANCE_M;
                        pos
                    });
                follow = Some(match at {
                    None => Event::SpawnFailed("the player's position did not read".to_owned()),
                    Some(at) => match game::spawn_hidden(body, at) {
                        Ok(chr) => {
                            state.sign_at = Some(at);
                            state.entity = game::event_entity(chr);
                            state.handle = game::chr_handle(chr);
                            if let Some(name) = state.picked_name.as_deref() {
                                names::set(chr, name);
                            }
                            summons_log(format_args!(
                                "duel: spawned npc {} hidden as 0x{chr:x} (entity {:?}, handle \
                                 {:?})",
                                body.npc_param, state.entity, state.handle
                            ));
                            Event::Created(chr as u64)
                        }
                        Err(why) => Event::SpawnFailed(why),
                    },
                });
            }
            Action::PlaceSign(chr) => {
                let placed = (|| {
                    let entity = state.entity.ok_or("the NPC's entity id did not read")?;
                    let player = game::main_player().ok_or("no main player")?;
                    let block = game::block_id(player).ok_or("the player has no block")?;
                    let at = state.sign_at.ok_or("no sign position")?;
                    game::place_red_sign(
                        entity,
                        block,
                        state.config.duel.summon_flag,
                        state.config.duel.dismiss_flag,
                        at,
                    )
                })();
                match placed {
                    Ok(sign) => summons_log(format_args!(
                        "duel: red sign 0x{sign:x} placed for 0x{chr:x}"
                    )),
                    Err(why) => {
                        summons_log(format_args!("duel: the sign was not placed: {why}"));
                        if alive(state, chr) {
                            let _ = game::unsummon(chr as usize);
                        }
                        forget(state, chr);
                        follow = Some(Event::Gone);
                    }
                }
            }
            Action::Remove { chr, why } => {
                let result = if alive(state, chr) {
                    game::unsummon(chr as usize)
                } else {
                    Err("already gone".to_owned())
                };
                summons_log(format_args!("duel: removed 0x{chr:x} ({why}): {result:?}"));
                forget(state, chr);
            }
            Action::Log(line) => summons_log(format_args!("duel: {line}")),
        }
    }
    follow
}

#[cfg(windows)]
fn step(state: &mut TaskState, event: Event) {
    let bodies = bodies(&state.config);
    let mut next = Some(event);
    // Bounded: each action can produce at most one follow-up event.
    for _ in 0..4 {
        let Some(event) = next.take() else {
            return;
        };
        let actions = state.duel.step(event, &bodies);
        next = perform(state, actions);
    }
}

/// Is the duel NPC still alive: does the handle it was created with still resolve to it?
#[cfg(windows)]
fn alive(state: &TaskState, chr: u64) -> bool {
    state
        .handle
        .and_then(game::chr_by_handle)
        .is_some_and(|found| found as u64 == chr)
}

/// The duel is over: forget the NPC.
#[cfg(windows)]
fn forget(state: &mut TaskState, chr: u64) {
    names::clear(chr as usize);
    state.entity = None;
    state.handle = None;
    state.sign_at = None;
}

#[cfg(windows)]
fn tick(state: &mut TaskState) {
    if state.frames.is_multiple_of(CONFIG_POLL_FRAMES) {
        refresh_config(state);
    }
    state.frames = state.frames.wrapping_add(1);
    dress::tick();
    brains::tick();
    if finger::take_finger_use() {
        step(state, Event::FingerUsed);
    }
    match picker::poll(&mut state.edges) {
        Some(picker::Choice::Picked(index)) => {
            summons_log(format_args!("duel: picker chose row {index}"));
            state.picked_name = state
                .config
                .duel
                .roster
                .get(index)
                .map(|npc| npc.name.clone());
            step(state, Event::Picked(index));
        }
        Some(picker::Choice::Cancelled) => {
            summons_log(format_args!("duel: picker cancelled"));
            step(state, Event::Cancelled);
        }
        None => {}
    }
    step(state, Event::Frame);
    names::prune();
    match state.duel.state.clone() {
        State::Hidden { chr, .. } => {
            let seen = alive(state, chr)
                .then(|| game::hidden_observation(chr as usize))
                .flatten();
            match seen {
                Some(seen) => step(state, Event::Observed(seen)),
                None => {
                    summons_log(format_args!(
                        "duel: hidden 0x{chr:x} is gone (its handle no longer resolves to it)"
                    ));
                    forget(state, chr);
                    step(state, Event::Gone);
                }
            }
        }
        State::Offered { chr } => {
            let joined = state
                .handle
                .and_then(game::party_state)
                .is_some_and(|s| s >= PARTY_JOINED);
            if joined {
                summons_log(format_args!("duel: 0x{chr:x} joined as a red phantom"));
                step(state, Event::Joined);
            } else if !alive(state, chr) {
                summons_log(format_args!(
                    "duel: offered 0x{chr:x} is gone (its handle no longer resolves to it)"
                ));
                forget(state, chr);
                step(state, Event::Gone);
            }
        }
        State::Joined { chr } => {
            let in_party = state.handle.and_then(game::party_state).is_some();
            let live = alive(state, chr);
            if !live || !in_party {
                if live {
                    let _ = game::unsummon(chr as usize);
                }
                summons_log(format_args!(
                    "duel: joined 0x{chr:x} ended (alive {live}, in party {in_party})"
                ));
                forget(state, chr);
                step(state, Event::Gone);
            }
        }
        State::Idle | State::Picking { .. } | State::Spawning { .. } => {}
    }
}

#[cfg(windows)]
fn spawn_game_task() {
    let _ = std::thread::Builder::new()
        .name("er-npc-summons-task".to_owned())
        .spawn(move || {
            // Bounded: an unbounded spin on this singleton once starved the wineserver.
            let Some(task) =
                er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
            else {
                summons_log(format_args!("CSTaskImp never appeared; staying inert"));
                return;
            };
            let mut state = TaskState {
                config: Config::default(),
                config_stamp: None,
                frames: 0,
                duel: Duel::new(0),
                edges: picker::Edges::default(),
                sign_at: None,
                entity: None,
                handle: None,
                picked_name: None,
            };
            summons_log(format_args!("game task registering on FrameBegin"));
            task.run_recurring(
                move |_data: &FD4TaskData| tick(&mut state),
                CSTaskGroupIndex::FrameBegin,
            );
        });
}

#[cfg(windows)]
fn install(module_base: usize) {
    reset_log_file();
    // A refused address or detour is logged here instead of failing silently.
    er_hook::set_hook_logger(summons_log);
    summons_log(format_args!(
        "attach: module_base={module_base:#x}; duel signs + custom Mimic Tear, overlay ABI {:#06x}",
        er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
    ));
    finger::install();
    mimic_hooks::install();
    game::install();
    names::install();
    brains::install();
    cursor::install();
    spawn_game_task();
    overlay::install(module_base);
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. On attach it only starts an installer thread: hook installs and
/// the overlay take locks and enumerate modules, neither of which belongs under the loader lock.
pub unsafe extern "system" fn DllMain(
    module: HINSTANCE,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        er_game_base::panic_report::report_panics_to("er-npc-summons", crate::log::summons_log);
        let module_base = module.0 as usize;
        START.call_once(|| {
            let _ = std::thread::Builder::new()
                .name("er-npc-summons-install".to_owned())
                .spawn(move || install(module_base));
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_npc_summons_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

// If this module wins the imgui context, every other overlay in the process has to find it.
#[cfg(windows)]
er_build_watermark_core::export_overlay_host!();
