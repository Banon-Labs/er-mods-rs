//! The Sun section's game half: a detour on `GXParamDB::Update` that writes [`Sun`] over the
//! lighting the game has just blended, and reads what the game had into [`SunSeen`].
//!
//! `GXParamDB::Update(this, f32 time, u32)` runs once a frame, always on one render thread, and
//! blends every gparam field for the time of day and the weather into the param instances. The
//! jobs that run after it derive the sun's direction from its angles and the ambient from the
//! hemisphere colours, then pack both for the GPU, so a field written as the function returns is
//! the one the frame draws with. That is the order `scripts/frida/sun-control.js` proved live
//! (bd `er-sky-gparam-override-frida-proven-1171-2026-10-15`, and for the shadow cone
//! `er-rt-shadow-cone-radius-soft-shadows-user-verified-1171-2026-10-15`).
//!
//! The second argument is a float in `xmm1`, which the union dispatchers in `er-hook` cannot
//! carry, so this is a detour of its own with the exact signature.
//!
//! # Where the fields are
//!
//! `GXRenderingSystem` (`[0x1447f37a8]`) holds the environment manager inline at `+0xc500`; each
//! gparam group's manager pointer sits at a fixed offset in it, and each manager keeps its
//! instances in a two-level list map (bank, then id). A light set's fields are at fixed offsets.
//! The tone map's and the ray tracing's are found through the accessor table each instance
//! carries, by the offset the field was registered at, and a field is written only if the table
//! lists exactly that address with that size.
//!
//! # Reads and writes
//!
//! Every pointer is read through `ReadProcessMemory` (`read_bytes`), so a stale one fails closed.
//! A field is written with a plain store only after the block holding it was read on this thread
//! in the same call: the instance is the one the game's own job reads next.

use std::ffi::c_void;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};

use er_game_base::game_build::{MAPPED_FILE_VERSION_1171, game_file_version};
use er_game_base::mem::{game_module_base, read_bytes};
use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};

use super::sun::{Holds, Sun, SunSeen, Want};
use super::{Install, with};
use crate::log;

include!(concat!(env!("OUT_DIR"), "/generated_light_prologues.rs"));

/// `GXParamDB::Update(this, f32 time, u32)`.
const PARAM_DB_UPDATE_1171_RVA: usize = 0x01a4_e7a0;
/// `GXRenderingSystem*`.
const RENDERING_SYSTEM_1171_RVA: usize = 0x047f_37a8;
/// `GXEnvironmentManager`, inline in the rendering system.
const ENVIRONMENT: usize = 0xc500;

/// A gparam group: its manager's offset in the environment manager, and its list map's in the
/// manager.
struct Group {
    manager: usize,
    map: usize,
}

const LIGHT_SET: Group = Group {
    manager: 0x28,
    map: 0x320,
};
const TONE_MAP: Group = Group {
    manager: 0x7e0,
    map: 0x18,
};
const RAY_TRACING: Group = Group {
    manager: 0x880,
    map: 0x18,
};

/// A `GXLightSet` instance: `DirLight0` (the sun, or the moon) at `+0x40`, inside a 0x300 block
/// whose bank is at `+0x294`.
const LIGHT_SET_SIZE: usize = 0x300;
const LIGHT_SET_BANK: usize = 0x294;
const SUN: usize = 0x40;
/// `DirLight0`'s colours' alphas (its intensity) and its angle pair, radians.
const SUN_DIFFUSE_ALPHA: usize = SUN + 0x1c;
const SUN_SPECULAR_ALPHA: usize = SUN + 0x2c;
const SUN_PITCH: usize = SUN + 0x30;
const SUN_YAW: usize = SUN + 0x34;
/// The hemisphere colours' alphas: the sky light from above and below.
const HEMI_UP_ALPHA: usize = 0x1e0 + 0xc;
const HEMI_DOWN_ALPHA: usize = 0x1f0 + 0xc;

/// An instance's accessor table: where it sits in the instance, and the registered offset of its
/// first entry.
struct Table {
    offset: usize,
    first: usize,
}

const TONE_TABLE: Table = Table {
    offset: 0xb0,
    first: 0x100,
};
const RT_TABLE: Table = Table {
    offset: 0x70,
    first: 0x58,
};
/// Registered offsets: the tone map's exposure, the ray-traced shadow cone and its rays.
const EXPOSURE: usize = 0x100;
const SHADOW_CONE: usize = 0x6c;
const SHADOW_RAYS: usize = 0x5c;
/// Accessor table: entry count, entries pointer, entry stride, an entry's field address and size.
const TABLE_COUNT: usize = 0x24;
const TABLE_ENTRIES: usize = 0x28;
const ENTRY_STRIDE: usize = 0x30;
const ENTRY_FIELD: usize = 0x08;
const ENTRY_SIZE: usize = 0x28;

/// List bounds no real map comes near, so a corrupt one ends the walk.
const MAX_BANKS: usize = 64;
const MAX_PER_BANK: usize = 64;
const MAX_TABLE: usize = 64;

/// Frames between reads for the tab while nothing is asked or held.
const SEEN_EVERY: u64 = 10;

type ParamDbUpdateFn = unsafe extern "system" fn(usize, f32, u32);

/// The trampoline to the game's own `GXParamDB::Update`.
static ORIGINAL: AtomicUsize = AtomicUsize::new(0);
/// The game module's base, for the rendering system's global.
static BASE: AtomicUsize = AtomicUsize::new(0);

#[derive(Default)]
struct State {
    holds: Holds,
    frame: u64,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

fn refuse(why: &'static str) {
    log(format_args!("sun: {why}; the Sun section does nothing"));
    with(|s| s.sun_install = Install::Refused(why));
}

/// Detour `GXParamDB::Update`. Called from the Sky tab's install thread once the game's task
/// scheduler exists, so the game's code is in place.
pub fn install() {
    if game_file_version() != Some(MAPPED_FILE_VERSION_1171) {
        refuse("not 1.17.1");
        return;
    }
    let Ok(base) = game_module_base() else {
        refuse("no game module");
        return;
    };
    // Version-gated rather than translated: the check above returned on anything but 1.17.1, and
    // the opening bytes are compared below before anything is hooked, so the 1.17.1 rva is the
    // right one by construction and `game_rva`'s 1.16.2 ledger has nothing to add.
    let entry = base + PARAM_DB_UPDATE_1171_RVA;
    let mut bytes = [0u8; PARAM_DB_UPDATE_1171_PROLOGUE.len()];
    // SAFETY: a fault-tolerant read of code bytes.
    let read = unsafe { read_bytes(entry, &mut bytes) };
    // Byte 0 may be Arxan's `jmp rel32`, which the installer follows; the rest must match.
    let matches = read
        && (bytes == PARAM_DB_UPDATE_1171_PROLOGUE
            || (bytes[0] == 0xe9 && bytes[5..] == PARAM_DB_UPDATE_1171_PROLOGUE[5..]));
    if !matches {
        log(format_args!("sun: 0x{entry:x} opens {bytes:02x?}"));
        refuse("GXParamDB::Update does not open as expected");
        return;
    }
    BASE.store(base, Ordering::Release);
    // SAFETY: MinHook's own initialisation; a second call reports it already ran.
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            log(format_args!("sun: MH_Initialize -> {status:?}"));
            refuse("MinHook would not start");
            return;
        }
    }
    // SAFETY: a 1.17.1 entry derived from the running module whose opening bytes were just
    // checked, and a detour with its exact signature.
    let hook = match unsafe {
        MhHook::new_following_arxan(entry as *mut c_void, param_db_update as *mut c_void)
    } {
        Ok(hook) => hook,
        Err(status) => {
            log(format_args!("sun: detour on 0x{entry:x} -> {status:?}"));
            refuse("GXParamDB::Update could not be detoured");
            return;
        }
    };
    // Stored before enabling: the render thread calls through the detour the moment it is live.
    ORIGINAL.store(hook.trampoline() as usize, Ordering::Release);
    // SAFETY: enabling the hook just created.
    let enabled = unsafe { hook.queue_enable() }.and_then(|()| unsafe { MH_ApplyQueued() }.ok());
    if let Err(status) = enabled {
        log(format_args!("sun: enabling the detour -> {status:?}"));
        refuse("GXParamDB::Update could not be detoured");
        return;
    }
    // The detour must outlive every caller; the process ends before it would be released.
    let _kept = Box::leak(Box::new(hook));
    log(format_args!(
        "sun: GXParamDB::Update at 0x{entry:x} detoured"
    ));
    with(|s| s.sun_install = Install::Running);
}

/// The game's `GXParamDB::Update`, then the Sun section's writes.
unsafe extern "system" fn param_db_update(db: usize, time: f32, count: u32) {
    let original = ORIGINAL.load(Ordering::Acquire);
    // SAFETY: the trampoline MinHook made for this function, stored before the detour went live,
    // called with the arguments the game passed.
    unsafe { core::mem::transmute::<usize, ParamDbUpdateFn>(original)(db, time, count) };
    // A panic must not unwind into the game's frame.
    let _ = catch_unwind(AssertUnwindSafe(after_update));
}

fn after_update() {
    let sun = with(|s| s.wanted.sun);
    let mut guard = STATE.lock().unwrap_or_else(PoisonError::into_inner);
    let state = guard.get_or_insert_with(State::default);
    state.frame += 1;
    if !sun.asks_anything() && state.holds.is_empty() && !state.frame.is_multiple_of(SEEN_EVERY) {
        return;
    }
    let mut seen = SunSeen {
        running: true,
        ..SunSeen::default()
    };
    let holds = &mut state.holds;
    holds.begin();
    let rendering = read_usize(BASE.load(Ordering::Acquire) + RENDERING_SYSTEM_1171_RVA);
    if let Some(rendering) = rendering.filter(|p| *p != 0) {
        let environment = rendering + ENVIRONMENT;
        light_sets(environment, &sun, holds, &mut seen);
        tone_maps(environment, &sun, holds, &mut seen);
        ray_tracing(environment, &sun, holds, &mut seen);
    }
    for (addr, bits) in holds.end(read_u32) {
        // SAFETY: `end` returns only fields it just read back as holding the section's own write.
        unsafe { store(addr, bits) };
    }
    drop(guard);
    with(|s| s.sun_seen = seen);
}

fn light_sets(environment: usize, sun: &Sun, holds: &mut Holds, seen: &mut SunSeen) {
    for (bank, instance) in instances(environment, &LIGHT_SET) {
        let mut block = vec![0u8; LIGHT_SET_SIZE];
        // SAFETY: a fault-tolerant read.
        if !unsafe { read_bytes(instance, &mut block) } || i32_at(&block, LIGHT_SET_BANK) != bank {
            continue;
        }
        let now = |offset: usize| u32_at(&block, offset);
        let game = |holds: &Holds, offset: usize| {
            f32::from_bits(holds.game_value(instance + offset, now(offset)))
        };
        if !seen.present {
            seen.present = true;
            seen.height = -game(holds, SUN_PITCH).to_degrees();
            seen.heading = game(holds, SUN_YAW).to_degrees();
            seen.strength = game(holds, SUN_DIFFUSE_ALPHA);
        }
        let mut hold = |offset: usize, want: Option<Want>| {
            put(holds, instance + offset, now(offset), want);
        };
        // The game's pitch is negative above the horizon.
        hold(SUN_PITCH, sun.height.map(|h| Want::Float(-h.to_radians())));
        hold(SUN_YAW, sun.heading.map(|h| Want::Float(h.to_radians())));
        hold(SUN_DIFFUSE_ALPHA, sun.strength.map(Want::Scale));
        hold(SUN_SPECULAR_ALPHA, sun.strength.map(Want::Scale));
        hold(HEMI_UP_ALPHA, sun.sky_light.map(Want::Scale));
        hold(HEMI_DOWN_ALPHA, sun.sky_light.map(Want::Scale));
    }
}

fn tone_maps(environment: usize, sun: &Sun, holds: &mut Holds, seen: &mut SunSeen) {
    let mut first = true;
    for (_, instance) in instances(environment, &TONE_MAP) {
        let Some(at) = field(instance, &TONE_TABLE, EXPOSURE, 4) else {
            continue;
        };
        let Some(now) = read_u32(at) else {
            continue;
        };
        if first {
            first = false;
            seen.exposure = f32::from_bits(holds.game_value(at, now));
        }
        put(holds, at, now, sun.exposure.map(Want::Scale));
    }
}

fn ray_tracing(environment: usize, sun: &Sun, holds: &mut Holds, seen: &mut SunSeen) {
    let mut first = true;
    for (_, instance) in instances(environment, &RAY_TRACING) {
        let cone = field(instance, &RT_TABLE, SHADOW_CONE, 4)
            .and_then(|at| read_u32(at).map(|now| (at, now)));
        let rays = field(instance, &RT_TABLE, SHADOW_RAYS, 4)
            .and_then(|at| read_u32(at).map(|now| (at, now)));
        if first {
            first = false;
            if let Some((at, now)) = cone {
                seen.size = f32::from_bits(holds.game_value(at, now)).to_degrees();
            }
            if let Some((at, now)) = rays {
                seen.shadow_rays = holds.game_value(at, now);
            }
        }
        if let Some((at, now)) = cone {
            put(
                holds,
                at,
                now,
                sun.size.map(|d| Want::Float(d.to_radians())),
            );
        }
        if let Some((at, now)) = rays {
            put(holds, at, now, sun.shadow_rays.map(Want::Int));
        }
    }
}

/// Hold `addr` at `want` for this frame, writing only when the bits differ.
fn put(holds: &mut Holds, addr: usize, now: u32, want: Option<Want>) {
    let Some(want) = want else {
        return;
    };
    let bits = holds.hold(addr, now, want);
    if bits != now {
        // SAFETY: the caller read `now` from `addr` on this thread in this call.
        unsafe { store(addr, bits) };
    }
}

/// Every (bank, instance) a group's list map holds.
fn instances(environment: usize, group: &Group) -> Vec<(i32, usize)> {
    let mut out = Vec::new();
    let Some(manager) = read_usize(environment + group.manager).filter(|p| *p != 0) else {
        return out;
    };
    let Some(head) = read_usize(manager + group.map).filter(|p| *p != 0) else {
        return out;
    };
    let mut node = read_usize(head).unwrap_or(head);
    for _ in 0..MAX_BANKS {
        if node == head || node == 0 {
            break;
        }
        let Some(outer) = read_array::<0x20>(node) else {
            break;
        };
        let bank = i32_at(&outer, 0x10);
        let inner = usize_at(&outer, 0x18);
        if let Some(inner_head) = read_usize(inner + 0x10).filter(|p| *p != 0) {
            let mut inner_node = read_usize(inner_head).unwrap_or(inner_head);
            for _ in 0..MAX_PER_BANK {
                if inner_node == inner_head || inner_node == 0 {
                    break;
                }
                let Some(entry) = read_array::<0x20>(inner_node) else {
                    break;
                };
                let instance = usize_at(&entry, 0x18);
                if instance != 0 {
                    out.push((bank, instance));
                }
                inner_node = usize_at(&entry, 0);
            }
        }
        node = usize_at(&outer, 0);
    }
    out
}

/// The address of the field registered at `registered`, if the instance's accessor table lists
/// exactly that address with that size. The table's first entry gives the instance's offset from
/// registered offsets.
fn field(instance: usize, table: &Table, registered: usize, size: u32) -> Option<usize> {
    let header = read_array::<0x30>(instance + table.offset)?;
    let count = (u32_at(&header, TABLE_COUNT) as usize).min(MAX_TABLE);
    let entries = usize_at(&header, TABLE_ENTRIES);
    if count == 0 || entries == 0 {
        return None;
    }
    let mut list = vec![0u8; count * ENTRY_STRIDE];
    // SAFETY: a fault-tolerant read.
    if !unsafe { read_bytes(entries, &mut list) } {
        return None;
    }
    let want = usize_at(&list, ENTRY_FIELD)
        .wrapping_sub(table.first)
        .wrapping_add(registered);
    (0..count)
        .map(|i| i * ENTRY_STRIDE)
        .any(|at| {
            usize_at(&list, at + ENTRY_FIELD) == want && u32_at(&list, at + ENTRY_SIZE) == size
        })
        .then_some(want)
}

fn read_array<const N: usize>(addr: usize) -> Option<[u8; N]> {
    let mut out = [0u8; N];
    // SAFETY: a fault-tolerant read.
    unsafe { read_bytes(addr, &mut out) }.then_some(out)
}

fn read_usize(addr: usize) -> Option<usize> {
    read_array::<8>(addr).map(usize::from_le_bytes)
}

fn read_u32(addr: usize) -> Option<u32> {
    read_array::<4>(addr).map(u32::from_le_bytes)
}

fn u32_at(bytes: &[u8], at: usize) -> u32 {
    bytes
        .get(at..at + 4)
        .and_then(|b| b.try_into().ok())
        .map_or(0, u32::from_le_bytes)
}

fn i32_at(bytes: &[u8], at: usize) -> i32 {
    u32_at(bytes, at) as i32
}

fn usize_at(bytes: &[u8], at: usize) -> usize {
    bytes
        .get(at..at + 8)
        .and_then(|b| b.try_into().ok())
        .map_or(0, usize::from_le_bytes)
}

/// One 4-byte store to a param field.
///
/// # Safety
///
/// `addr` must be a field the caller read through `read_bytes` on this thread in this call.
unsafe fn store(addr: usize, bits: u32) {
    // SAFETY: per the contract above; the field is 4-byte aligned in every instance read.
    unsafe { core::ptr::write_volatile(addr as *mut u32, bits) };
}
