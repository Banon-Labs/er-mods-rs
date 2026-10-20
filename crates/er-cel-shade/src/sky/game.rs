//! The Sky tab's game-thread half: a `FrameBegin` task that reads the clock and the weather into
//! [`Seen`], and makes the changes [`Wanted`] asks for through the game's own calls.
//!
//! `FrameBegin` runs before the field area's update, which runs the weather's update and then the
//! clock's, so a request made here is applied the same frame by the object that owns it. That is
//! the order the Frida prototype (`scripts/frida/sky-control.js`) proved from inside the two updates.
//!
//! # What it calls, and what it writes
//!
//! - A time: `WorldAreaTime::RequestTime(this, h, m, s)`, the call EMEVD's "set time" makes. It
//!   clamps, refuses on a co-op client, and leaves the next update to jump the clock and refresh
//!   the lighting.
//! - A weather: `WorldAreaWeather::ChangeWeather(this, type, lifespan, immediate)`, the call
//!   EMEVD's weather instruction ends in, with a lifespan of -1 (never expires, and the area's
//!   random rolls stop) and immediate set. -1 as the type ends it again.
//! - The lock on the clock is the byte EMEVD's "freeze time" sets (`SetTimeFrozen`, 0x1405f6240,
//!   is one store to it once a `WorldAreaTime` exists), written here only while one does. The game
//!   carries it across area loads itself.
//! - The pace has no call: it is the speed pair at `WorldAreaTime+0x34/+0x38` (game seconds per
//!   real second; 24 in vanilla), written together because a client's resync copies +0x34 into
//!   +0x38. What it was is kept and written back when the tab lets go.
//!
//! Every address was measured on 1.17.1 and is carried to the running build by
//! `carry_1171_rva` (the identity on 1.17.1, the segment carry on 2.7.2.0, refused elsewhere);
//! both calls are made only after their opening bytes match the generated prologues.

use std::sync::atomic::{AtomicBool, Ordering};

use er_game_base::game_build::{carry_1171_rva, game_file_version};
use er_game_base::mem::{
    game_module_base, read_bytes, safe_read_f32, safe_read_i32, safe_read_u8, safe_read_u16,
    safe_read_usize, safe_write_i32,
};

use super::{Clock, Install, NATURAL, Seen, Wanted, speed_for_day_minutes, with};
use crate::log;

include!(concat!(env!("OUT_DIR"), "/generated_sky_prologues.rs"));

/// `WorldAreaTime*`, null outside the world.
const WORLD_AREA_TIME_1171_RVA: usize = 0x03d6_d368;
/// The freeze flag; time does not pass while it is set.
const TIME_FROZEN_1171_RVA: usize = 0x03d6_d370;
/// `WorldAreaWeather*`, null outside the world.
const WORLD_AREA_WEATHER_1171_RVA: usize = 0x03d6_d3f0;
/// `CSSessionManager*`; its lobby state at +0xc is 6 on a co-op client.
const SESSION_MANAGER_1171_RVA: usize = 0x03d7_e540;
/// `WorldAreaTime::RequestTime(this, u32 hour, u32 minute, u32 second)`.
const REQUEST_TIME_1171_RVA: usize = 0x0064_42a0;
/// `WorldAreaWeather::ChangeWeather(this, i16 type, f32 lifespan, bool immediate)`.
const CHANGE_WEATHER_1171_RVA: usize = 0x0064_7940;

/// `WorldAreaTime`: the packed date the clock is read from.
const TIME_PACKED: usize = 0x08;
/// `WorldAreaTime`: the base pace a client's resync restores, and the pace the update uses.
const TIME_SPEED_BASE: usize = 0x34;
const TIME_SPEED: usize = 0x38;
/// `WorldAreaTime`: the clock moved on the last update.
const TIME_FLOWING: usize = 0x46;
/// `WorldAreaWeather`: 0 once a change has been applied, 1 while one is.
const WEATHER_STATE: usize = 0x18;
/// `WorldAreaWeather`: the current `WeatherParam` row id.
const WEATHER_TYPE: usize = 0x2a;
/// `WorldAreaWeather`: the current weather came from a script (or the tab), not the area's rolls.
const WEATHER_EVENT: usize = 0xf9;
/// `WorldAreaWeather`: how wet the world is, 0 to 1.
const WEATHER_WETNESS: usize = 0x178;
/// `CSSessionManager`: the lobby state, and its value on a co-op client.
const LOBBY_STATE: usize = 0x0c;
const LOBBY_CLIENT: i32 = 6;

/// Frames between asks while a locked weather has drifted, so a script that keeps changing it
/// costs one ask a second rather than one a frame.
const WEATHER_REASK_FRAMES: u64 = 60;

type RequestTimeFn = unsafe extern "system" fn(usize, u32, u32, u32);
type ChangeWeatherFn = unsafe extern "system" fn(usize, i16, f32, u8);

/// Set once the task is running.
pub static RUNNING: AtomicBool = AtomicBool::new(false);

/// The task's own memory between frames.
struct Task {
    /// The four `.data` globals, carried to the running build.
    world_area_time: usize,
    time_frozen: usize,
    world_area_weather: usize,
    session_manager: usize,
    request_time: RequestTimeFn,
    change_weather: ChangeWeatherFn,
    frame: u64,
    /// The objects last seen; a new one means an area loaded.
    clock_object: usize,
    weather_object: usize,
    /// The last [`Wanted::time_serial`] and [`Wanted::weather_serial`] asked of the game.
    time_serial: u32,
    weather_serial: u32,
    /// The tab set the freeze flag, so the tab may clear it.
    froze: bool,
    /// The pace before the tab first changed it, to write back when it lets go.
    vanilla_speed: Option<f32>,
    /// The tab asked for a weather and has not ended it.
    asked_weather: bool,
    last_weather_ask: u64,
}

fn refuse(why: &'static str) {
    log(format_args!("sky: {why}; the Sky tab only reads"));
    with(|s| s.install = Install::Refused(why));
}

/// Where the 1.17.1 address `rva_1171` is in the running module, or `None` when the carry
/// refuses this build.
fn carried(base: usize, rva_1171: usize) -> Option<usize> {
    let rva = carry_1171_rva(rva_1171)?;
    Some(base + rva)
}

/// `entry` if its opening bytes are `prologue`.
fn checked_entry(entry: usize, prologue: &[u8]) -> Option<usize> {
    let mut bytes = vec![0u8; prologue.len()];
    // SAFETY: a fault-tolerant read of code bytes.
    let read = unsafe { read_bytes(entry, &mut bytes) };
    (read && bytes == prologue).then_some(entry)
}

/// Install the task on its own thread: it waits on the game's task scheduler, which does not
/// belong on the loader thread or in front of the overlay. The Sun section's lighting detour
/// follows once that wait is over, when the game's code is in place.
pub fn spawn_install() {
    let spawned = std::thread::Builder::new()
        .name("er-cel-shade-sky".to_owned())
        .spawn(|| {
            install();
            super::light::install();
        });
    if let Err(error) = spawned {
        log(format_args!("sky: install thread not started ({error})"));
    }
}

fn install() {
    use eldenring::cs::{CSTaskGroupIndex, CSTaskImp};
    use eldenring::fd4::FD4TaskData;
    use fromsoftware_shared::{FromStatic, SharedTaskImpExt};

    let Ok(base) = game_module_base() else {
        refuse("no game module");
        return;
    };
    // Measured on 1.17.1 and carried from there: `game_rva`'s 1.16.2 ledger has no row for any
    // of them. A build the carry does not cover refuses here, before anything is read or called.
    let (
        Some(request_entry),
        Some(change_entry),
        Some(world_area_time),
        Some(time_frozen),
        Some(world_area_weather),
        Some(session_manager),
    ) = (
        carried(base, REQUEST_TIME_1171_RVA),
        carried(base, CHANGE_WEATHER_1171_RVA),
        carried(base, WORLD_AREA_TIME_1171_RVA),
        carried(base, TIME_FROZEN_1171_RVA),
        carried(base, WORLD_AREA_WEATHER_1171_RVA),
        carried(base, SESSION_MANAGER_1171_RVA),
    )
    else {
        log(format_args!(
            "sky: no carry of the 1.17.1 addresses to {:?}",
            game_file_version()
        ));
        refuse("no carry of the 1.17.1 addresses to this build");
        return;
    };
    let Some(request) = checked_entry(request_entry, &REQUEST_TIME_1171_PROLOGUE) else {
        refuse("RequestTime does not open as expected");
        return;
    };
    let Some(change) = checked_entry(change_entry, &CHANGE_WEATHER_1171_PROLOGUE) else {
        refuse("ChangeWeather does not open as expected");
        return;
    };
    // Bounded, as every wait on the scheduler in this workspace is (er_game_base::wait).
    let Some(scheduler) = er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
    else {
        refuse("the game's task scheduler never appeared");
        return;
    };
    // SAFETY: both entries are 1.17.1 functions carried to the running build whose opening bytes
    // were just compared with the generated prologues; the signatures are the ones the Frida
    // prototype called them with.
    let (request_time, change_weather) = unsafe {
        (
            core::mem::transmute::<usize, RequestTimeFn>(request),
            core::mem::transmute::<usize, ChangeWeatherFn>(change),
        )
    };
    let mut task = Task {
        world_area_time,
        time_frozen,
        world_area_weather,
        session_manager,
        request_time,
        change_weather,
        frame: 0,
        clock_object: 0,
        weather_object: 0,
        time_serial: 0,
        weather_serial: 0,
        froze: false,
        vanilla_speed: None,
        asked_weather: false,
        last_weather_ask: 0,
    };
    log(format_args!(
        "sky: RequestTime 0x{request:x}, ChangeWeather 0x{change:x}; running from FrameBegin"
    ));
    with(|s| s.install = Install::Running);
    RUNNING.store(true, Ordering::Relaxed);
    scheduler.run_recurring(
        move |_data: &FD4TaskData| task.tick(),
        CSTaskGroupIndex::FrameBegin,
    );
}

fn read_ptr(addr: usize) -> usize {
    // SAFETY: a fault-tolerant read.
    unsafe { safe_read_usize(addr) }.unwrap_or(0)
}

fn read_f32(addr: usize) -> f32 {
    // SAFETY: a fault-tolerant read.
    unsafe { safe_read_f32(addr) }.unwrap_or(0.0)
}

fn read_u8(addr: usize) -> u8 {
    // SAFETY: a fault-tolerant read.
    unsafe { safe_read_u8(addr) }.unwrap_or(0)
}

fn write_f32(addr: usize, value: f32) {
    // SAFETY: a fault-tolerant write of a field this module names, on the game thread that owns it.
    unsafe { safe_write_i32(addr, value.to_bits() as i32) };
}

impl Task {
    fn tick(&mut self) {
        self.frame += 1;
        let wanted = with(|s| s.wanted.clone());
        let session = read_ptr(self.session_manager);
        // SAFETY: a fault-tolerant read.
        let client =
            session != 0 && unsafe { safe_read_i32(session + LOBBY_STATE) } == Some(LOBBY_CLIENT);
        let mut seen = Seen {
            client,
            ..Seen::default()
        };
        self.clock(&wanted, &mut seen);
        self.weather(&wanted, &mut seen);
        with(|s| s.seen = seen);
    }

    fn clock(&mut self, wanted: &Wanted, seen: &mut Seen) {
        let clock = read_ptr(self.world_area_time);
        if clock == 0 {
            self.clock_object = 0;
            return;
        }
        let fresh = clock != self.clock_object;
        self.clock_object = clock;

        // A time asked for is asked once; a locked one again whenever an area load builds a new
        // clock, so the load cannot hand back the saved time.
        if wanted.time_serial != self.time_serial || (fresh && wanted.lock_time) {
            self.time_serial = wanted.time_serial;
            if wanted.time_serial != 0 {
                let Clock {
                    hour,
                    minute,
                    second,
                } = wanted.time;
                // SAFETY: the checked RequestTime, on the game thread, with the live object.
                unsafe {
                    (self.request_time)(clock, hour.into(), minute.into(), second.into());
                }
            }
        }

        let frozen_at = self.time_frozen;
        let frozen = read_u8(frozen_at) != 0;
        if wanted.lock_time && !frozen {
            // SAFETY: the flag `SetTimeFrozen` stores, with a clock in the world as it requires.
            unsafe { write_frozen(frozen_at, 1) };
            self.froze = true;
        } else if !wanted.lock_time && self.froze {
            // SAFETY: as above; only the tab's own freeze is undone.
            unsafe { write_frozen(frozen_at, 0) };
            self.froze = false;
        }

        match wanted.day_minutes {
            Some(minutes) => {
                let speed = speed_for_day_minutes(minutes);
                if self.vanilla_speed.is_none() {
                    self.vanilla_speed = Some(read_f32(clock + TIME_SPEED_BASE));
                }
                if (read_f32(clock + TIME_SPEED) - speed).abs() > 1e-3
                    || (read_f32(clock + TIME_SPEED_BASE) - speed).abs() > 1e-3
                {
                    write_f32(clock + TIME_SPEED_BASE, speed);
                    write_f32(clock + TIME_SPEED, speed);
                }
            }
            None => {
                if let Some(speed) = self.vanilla_speed.take() {
                    write_f32(clock + TIME_SPEED_BASE, speed);
                    write_f32(clock + TIME_SPEED, speed);
                }
            }
        }

        // SAFETY: a fault-tolerant read.
        let packed = unsafe { safe_read_usize(clock + TIME_PACKED) }.unwrap_or(0) as u64;
        seen.in_world = true;
        seen.clock = Clock::from_packed(packed);
        seen.frozen = read_u8(frozen_at) != 0;
        seen.flowing = read_u8(clock + TIME_FLOWING) != 0;
        seen.speed = read_f32(clock + TIME_SPEED);
    }

    fn weather(&mut self, wanted: &Wanted, seen: &mut Seen) {
        let weather = read_ptr(self.world_area_weather);
        if weather == 0 {
            self.weather_object = 0;
            return;
        }
        let fresh = weather != self.weather_object;
        self.weather_object = weather;
        // SAFETY: fault-tolerant reads.
        let current = unsafe { safe_read_u16(weather + WEATHER_TYPE) }.unwrap_or(0) as i16;
        let settling = unsafe { safe_read_i32(weather + WEATHER_STATE) }.unwrap_or(0) != 0;

        let asked = wanted.weather_serial != self.weather_serial;
        let locked = wanted.lock_weather && wanted.weather != NATURAL;
        let drifted = !settling
            && current != wanted.weather
            && self.frame.saturating_sub(self.last_weather_ask) >= WEATHER_REASK_FRAMES;
        if asked || (locked && (fresh || drifted)) {
            self.weather_serial = wanted.weather_serial;
            self.last_weather_ask = self.frame;
            if wanted.weather != NATURAL {
                // SAFETY: the checked ChangeWeather, on the game thread, with the live object.
                unsafe { (self.change_weather)(weather, wanted.weather, -1.0, 1) };
                self.asked_weather = true;
            } else if self.asked_weather && !fresh {
                // Only the tab's own weather is ended: a script's stays, and a new area already
                // started from its own.
                // SAFETY: as above.
                unsafe { (self.change_weather)(weather, NATURAL, 0.0, 1) };
                self.asked_weather = false;
            }
        }
        if fresh && wanted.weather == NATURAL {
            self.asked_weather = false;
        }

        seen.has_weather = true;
        seen.weather = current;
        seen.weather_settling = settling;
        seen.weather_event = read_u8(weather + WEATHER_EVENT) != 0;
        seen.wetness = read_f32(weather + WEATHER_WETNESS);
    }
}

/// One byte store to the freeze flag, as `SetTimeFrozen` makes it. A byte rather than the dword
/// [`safe_write_i32`] writes, because the next byte is another flag (the private stop flag).
///
/// # Safety
///
/// `addr` must be [`TIME_FROZEN_1171_RVA`] carried to the running build: a byte of `.data`,
/// always mapped and writable.
unsafe fn write_frozen(addr: usize, value: u8) {
    // SAFETY: per the contract above.
    unsafe { core::ptr::write_volatile(addr as *mut u8, value) };
}
