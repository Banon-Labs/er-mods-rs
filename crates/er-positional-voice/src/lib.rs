//! Every player's voice comes out of their character, with distance falloff and the game's own
//! reverb zones, in Seamless Co-op sessions.
//!
//! # What this DLL does
//!
//! - A thread of its own reads the microphone through `ISteamUser::GetVoice` while you are in a
//!   world with other players, and sends each compressed chunk to every one of them over
//!   `ISteamNetworkingMessages` on its own channel. It accepts chunks only from players in the
//!   session, decodes them with `ISteamUser::DecompressVoice` at 48 kHz and applies the volume.
//! - A `FrameBegin` game task loads a small Wwise bank (`er_positional_voice_core::bank`), keeps
//!   the session's SteamIDs from `CSSessionManager`, and plays each player's PCM through a
//!   `DLMOW::MOAudioInputObject` of the game's, placed at that player's character every chunk.
//!
//! Both ends need this DLL: it is its own voice chat, not a change to anyone else's.
//!
//! # What it does not touch
//!
//! Seamless Co-op. Nothing in `ersc.dll` is hooked, patched, read or written, and none of its
//! calls are intercepted. Seamless's own voice chat stays as it ships (off). No game code is
//! patched either: every game function is called as the game calls it, after its first eight
//! bytes have been checked against 2.7.2.0 (`er_positional_voice_core::sites`).
//!
//! # The log is the oracle
//!
//! `er-positional-voice.log` beside the game records the site check, Steam coming up, the bank
//! load, every change in the session's players, every voice object made, and a status line every
//! ten seconds with the chunks sent, received, decoded and played.

mod log;

#[cfg(windows)]
mod game;
#[cfg(windows)]
mod steam;

#[cfg(windows)]
use std::{
    collections::{HashMap, VecDeque},
    sync::{
        LazyLock, Mutex, Once, OnceLock,
        atomic::{AtomicU64, Ordering},
        mpsc::{Receiver, SyncSender, sync_channel},
    },
};

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use er_positional_voice_core::{config::Config, gain, packet};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, SharedTaskImpExt};
#[cfg(windows)]
use windows::Win32::{Foundation::HINSTANCE, System::SystemServices::DLL_PROCESS_ATTACH};

#[cfg(windows)]
use crate::log::{reset_log_file, voice_log};

const DLL_MAIN_SUCCESS: i32 = 1;

/// Frames between status lines and between session reads. At 60fps, ten seconds and half one.
#[cfg(windows)]
const STATUS_LOG_TICKS: u64 = 600;
#[cfg(windows)]
const SESSION_READ_TICKS: u64 = 30;
/// Voice-thread wakes between config rereads and session accepts: about a second at 60fps.
#[cfg(windows)]
const ONCE_A_SECOND: u64 = 60;

/// The game task's per-frame wake-up for the voice thread, which polls Steam once a frame. A
/// capacity of one coalesces frames the voice thread was too busy to see.
#[cfg(windows)]
static FRAME: OnceLock<SyncSender<()>> = OnceLock::new();
/// Decoded chunks held per player before the oldest is dropped: about a second.
#[cfg(windows)]
const QUEUE_MAX: usize = 50;

/// What the two threads share.
#[cfg(windows)]
#[derive(Default)]
struct Shared {
    /// The other players in the session, from the game task.
    peers: Vec<u64>,
    in_world: bool,
    /// The game's own settings, from the game task: Network > Voice Chat, and the voice chat
    /// volume (0..=10) under Sound.
    voice_on: bool,
    volume: u8,
    /// Our own SteamID, from the voice thread.
    local: u64,
    /// Decoded PCM by SteamID, and whether it is our own (the loopback test).
    queue: HashMap<u64, (bool, VecDeque<Vec<u8>>)>,
}

#[cfg(windows)]
static SHARED: LazyLock<Mutex<Shared>> = LazyLock::new(Mutex::default);

#[cfg(windows)]
fn shared() -> std::sync::MutexGuard<'static, Shared> {
    SHARED
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(windows)]
#[derive(Default)]
struct Counters {
    captured: AtomicU64,
    sent: AtomicU64,
    send_failed: AtomicU64,
    received: AtomicU64,
    rejected: AtomicU64,
    decoded: AtomicU64,
    decode_failed: AtomicU64,
    queue_dropped: AtomicU64,
    recording_starts: AtomicU64,
    /// The last `GetAvailableVoice` result (`EVoiceResult`), for the status line.
    last_available: AtomicU64,
}

#[cfg(windows)]
static COUNTERS: Counters = Counters {
    captured: AtomicU64::new(0),
    sent: AtomicU64::new(0),
    send_failed: AtomicU64::new(0),
    received: AtomicU64::new(0),
    rejected: AtomicU64::new(0),
    decoded: AtomicU64::new(0),
    decode_failed: AtomicU64::new(0),
    queue_dropped: AtomicU64::new(0),
    recording_starts: AtomicU64::new(0),
    last_available: AtomicU64::new(u64::MAX),
};

#[cfg(windows)]
fn count(c: &AtomicU64) {
    c.fetch_add(1, Ordering::Relaxed);
}

#[cfg(windows)]
static START: Once = Once::new();

#[cfg(windows)]
fn load_config() -> Config {
    use er_positional_voice_core::config::{DEFAULT_TEXT, FILE_NAME};
    let Some(dir) = er_game_base::log::game_directory_path() else {
        return Config::default();
    };
    let path = dir.join(FILE_NAME);
    match std::fs::read_to_string(&path) {
        Ok(text) => Config::parse(&text),
        Err(_) => {
            let _ = std::fs::write(&path, DEFAULT_TEXT);
            Config::default()
        }
    }
}

/// When the config file was last written, or `None` when it cannot be read.
#[cfg(windows)]
fn config_stamp() -> Option<std::time::SystemTime> {
    let dir = er_game_base::log::game_directory_path()?;
    let path = dir.join(er_positional_voice_core::config::FILE_NAME);
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// Queue decoded PCM for the game task.
#[cfg(windows)]
fn enqueue(key: u64, local: bool, pcm: Vec<u8>) {
    let mut s = shared();
    let (_, q) = s
        .queue
        .entry(key)
        .or_insert_with(|| (local, VecDeque::new()));
    if q.len() >= QUEUE_MAX {
        q.pop_front();
        count(&COUNTERS.queue_dropped);
    }
    q.push_back(pcm);
}

/// The voice thread: microphone, send, receive, decode.
#[cfg(windows)]
fn voice_thread(config: Config, frames: Receiver<()>) {
    use steam::{NotYet, Steam, VOICE_NOT_RECORDING, VOICE_OK};
    let mut waited_logged = false;
    let steam = loop {
        // The game task is gone when its sender is: nothing left to do.
        if frames.recv().is_err() {
            return;
        }
        match Steam::load() {
            Ok(s) => break s,
            Err(NotYet::Missing(why)) => {
                voice_log(format_args!("steam: {why}; voice chat stays off"));
                return;
            }
            Err(NotYet::NotUp) => {
                if !waited_logged {
                    voice_log(format_args!("steam: waiting for SteamAPI_Init"));
                    waited_logged = true;
                }
            }
        }
    };
    let local = steam.local_steam_id();
    shared().local = local;
    voice_log(format_args!("steam: up, local SteamID {local:#018x}"));

    let mut compressed = vec![0u8; packet::MAX_PAYLOAD];
    let mut pcm = vec![0u8; 64 * 1024];
    let mut polls: u64 = 0;
    let mut config = config;
    let mut config_seen = config_stamp();
    loop {
        if frames.recv().is_err() {
            return;
        }
        polls += 1;
        // The config file is reread when it changes, so loopback and gain can be set mid-game.
        if polls.is_multiple_of(ONCE_A_SECOND) {
            let stamp = config_stamp();
            if stamp != config_seen {
                config_seen = stamp;
                let next = load_config();
                if next != config {
                    voice_log(format_args!(
                        "config: reloaded, gain {} loopback {}",
                        next.gain, next.loopback
                    ));
                    config = next;
                }
            }
        }
        let (peers, in_world, voice_on, volume) = {
            let s = shared();
            (s.peers.clone(), s.in_world, s.voice_on, s.volume)
        };
        // Voice Chat off in the game's Network settings: no microphone, nothing sent, nothing
        // played. What arrives meanwhile is read and dropped so it does not pile up in Steam.
        if !in_world || !voice_on || (peers.is_empty() && !config.loopback) {
            if in_world {
                steam.receive(256, |_, _| {});
            }
            continue;
        }
        let gain = gain::with_volume(config.gain, volume);
        // A player who speaks first opens a session we have not sent on yet; accepting it is
        // what lets their voice through before ours has gone the other way. Once a second.
        if polls.is_multiple_of(ONCE_A_SECOND) {
            for &p in &peers {
                if steam.accept(p) {
                    voice_log(format_args!("steam: accepted session from {p:#018x}"));
                }
            }
        }

        let (r, waiting) = steam.available_voice();
        COUNTERS
            .last_available
            .store(u64::from(r), Ordering::Relaxed);
        if r == VOICE_NOT_RECORDING {
            steam.start_recording();
            count(&COUNTERS.recording_starts);
        } else if r == VOICE_OK && waiting > 0 {
            let (r, n) = steam.get_voice(&mut compressed);
            if r == VOICE_OK && n > 0 {
                count(&COUNTERS.captured);
                let chunk = &compressed[..n];
                if let Some(message) = packet::encode(chunk) {
                    for &p in &peers {
                        if steam.send(p, &message) == 1 {
                            count(&COUNTERS.sent);
                        } else {
                            count(&COUNTERS.send_failed);
                        }
                    }
                }
                if config.loopback {
                    let (r, m) = steam.decompress(chunk, &mut pcm);
                    if r == VOICE_OK && m > 0 {
                        let mut out = pcm[..m].to_vec();
                        gain::apply(&mut out, gain);
                        enqueue(local, true, out);
                    }
                }
            }
        }

        steam.receive(256, |from, bytes| {
            count(&COUNTERS.received);
            let Some(from) = from.filter(|f| peers.contains(f)) else {
                count(&COUNTERS.rejected);
                return;
            };
            let Some(chunk) = packet::decode(bytes) else {
                count(&COUNTERS.rejected);
                return;
            };
            let (r, m) = steam.decompress(chunk, &mut pcm);
            if r != VOICE_OK || m == 0 {
                count(&COUNTERS.decode_failed);
                return;
            }
            count(&COUNTERS.decoded);
            let mut out = pcm[..m].to_vec();
            gain::apply(&mut out, gain);
            enqueue(from, false, out);
        });
    }
}

/// What the game task carries between frames.
#[cfg(windows)]
struct TaskState {
    game: game::Game,
    voices: game::Voices,
    ticks: u64,
    /// `None` until the first frame in a world; then whether the bank is usable.
    bank: Option<bool>,
    peers: Vec<u64>,
    /// The game's voice chat switch and volume as last logged.
    settings: (bool, u8),
    /// The last status line written.
    last_status: String,
}

/// One game frame.
#[cfg(windows)]
fn tick(state: &mut TaskState) {
    state.ticks += 1;
    if let Some(frame) = FRAME.get() {
        // Full means the voice thread has a wake-up pending already.
        let _ = frame.try_send(());
    }
    // SAFETY: the task runs on the game thread.
    let in_world = unsafe { game::in_world() };
    // SAFETY: game thread.
    let (voice_on, volume) = unsafe { game::voice_settings() };
    if (voice_on, volume) != state.settings {
        voice_log(format_args!(
            "settings: voice chat {voice_on}, voice volume {volume}/10"
        ));
        state.settings = (voice_on, volume);
    }
    let local = {
        let mut s = shared();
        s.in_world = in_world;
        s.voice_on = voice_on;
        s.volume = volume;
        s.local
    };
    if !voice_on {
        shared().queue.clear();
    }
    if !in_world {
        shared().queue.clear();
        return;
    }
    if state.bank.is_none() {
        let r = game::load_bank(&state.game);
        match &r {
            Ok(how) => voice_log(format_args!("bank: {how}")),
            Err(why) => voice_log(format_args!("bank: {why}; voices will not play")),
        }
        state.bank = Some(r.is_ok());
    }
    if state.ticks.is_multiple_of(SESSION_READ_TICKS) {
        let mut now = Vec::new();
        // SAFETY: game thread.
        unsafe { game::session_peers(local, &mut now) };
        if now != state.peers {
            voice_log(format_args!("session: players {now:#018x?}"));
            state.peers.clone_from(&now);
            shared().peers = now;
        }
    }
    let queued = std::mem::take(&mut shared().queue);
    if state.bank == Some(true) {
        for (key, (local, chunks)) in queued {
            for pcm in chunks {
                // SAFETY: game thread.
                unsafe { state.voices.play(&state.game, key, local, &pcm) };
            }
        }
    }
    if state.ticks.is_multiple_of(STATUS_LOG_TICKS) {
        let c = &COUNTERS;
        let v = &state.voices.stats;
        let l = |a: &AtomicU64| a.load(Ordering::Relaxed);
        let line = format!(
            "status: peers={} captured={} sent={} send_failed={} received={} rejected={} \
             decoded={} decode_failed={} queue_dropped={} recording_starts={} last_available={} | voices made={} \
             failed={} full={} pushes={} push_failed={} placed={} unplaced={} plays={}",
            state.peers.len(),
            l(&c.captured),
            l(&c.sent),
            l(&c.send_failed),
            l(&c.received),
            l(&c.rejected),
            l(&c.decoded),
            l(&c.decode_failed),
            l(&c.queue_dropped),
            l(&c.recording_starts),
            l(&c.last_available),
            v.created,
            v.create_failed,
            v.dropped_full,
            v.pushes,
            v.pushes_failed,
            v.placed,
            v.unplaced,
            v.plays,
        );
        // Only when something moved: an idle game writes nothing.
        if line != state.last_status {
            voice_log(format_args!("{line}"));
            state.last_status = line;
        }
    }
}

#[cfg(windows)]
fn install() {
    reset_log_file();
    let base = match er_game_base::mem::game_module_base() {
        Ok(b) => b,
        Err(why) => {
            voice_log(format_args!(
                "attach: no game base ({why}); voice chat stays off"
            ));
            return;
        }
    };
    if let Err(why) = game::check_sites(base) {
        voice_log(format_args!("attach: {why}; voice chat stays off"));
        return;
    }
    let config = load_config();
    voice_log(format_args!(
        "attach: game base {base:#x}, all {} 2.7.2.0 sites matched on {}; gain {} \
         loopback {}; nothing in ersc.dll is touched",
        er_positional_voice_core::sites::SITES.len(),
        er_game_base::game_build::describe_build(),
        config.gain,
        config.loopback
    ));
    let (frame, frames) = sync_channel(1);
    let _ = FRAME.set(frame);
    let _ = std::thread::Builder::new()
        .name("er-positional-voice".to_owned())
        .spawn(move || voice_thread(config, frames));

    // Bounded: an unbounded spin on this singleton once starved the wineserver (er_game_base::wait).
    let Some(task) = er_game_base::wait::poll_until(|| unsafe { CSTaskImp::instance() }.ok())
    else {
        voice_log(format_args!(
            "CSTaskImp never appeared; voices will not play"
        ));
        return;
    };
    let nonce = std::process::id();
    let mut state = TaskState {
        game: game::Game::new(base),
        voices: game::Voices::new(nonce),
        ticks: 0,
        bank: None,
        peers: Vec::new(),
        settings: (false, u8::MAX),
        last_status: String::new(),
    };
    voice_log(format_args!("game task registering FrameBegin tick"));
    task.run_recurring(
        move |_data: &FD4TaskData| tick(&mut state),
        CSTaskGroupIndex::FrameBegin,
    );
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. On attach it only starts an installer thread; nothing that
/// takes a lock or calls Steam runs under the loader lock.
pub unsafe extern "system" fn DllMain(
    _module: HINSTANCE,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // First, before anything that can panic: a panic crossing `extern "system"` is an abort
        // that leaves no record, and this hook turns it into a file:line in the log.
        er_game_base::panic_report::report_panics_to("er-positional-voice", crate::log::voice_log);
        START.call_once(|| {
            let _ = std::thread::Builder::new()
                .name("er-positional-voice-install".to_owned())
                .spawn(install);
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_positional_voice_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}
