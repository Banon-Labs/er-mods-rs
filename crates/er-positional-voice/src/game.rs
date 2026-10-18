//! The game side, all on the game thread: the bank, the session's players, and one
//! `DLMOW::MOAudioInputObject` per speaking player, placed at their character and fed their PCM.
//!
//! Every call here is the sequence the Cheat Engine table ran in the sessions where positional
//! voice was heard (`er-voice-chat.CT`, 2026-10-17), with the same arguments. The addresses are
//! offsets from the game's base in `er_positional_voice_core::sites`, checked before any is
//! called.

#![cfg(windows)]

use std::ffi::c_void;

use eldenring::cs::{CSSessionManager, GameDataMan, WorldChrMan};
use er_positional_voice_core::{
    bank, sites,
    slots::{Lookup, PlayStep, SlotState, Slots},
};
use fromsoftware_shared::FromStatic;

use crate::log::voice_log;

/// The game's base and every function this module calls, once [`Game::resolve`] has checked them.
pub(crate) struct Game {
    base: usize,
}

/// Why the game cannot be used: a site's bytes differ from 1.17.1.
pub(crate) fn check_sites(base: usize) -> Result<(), String> {
    let mismatch = sites::first_mismatch(|rva| {
        let mut out = [0u8; 8];
        // SAFETY: `read_bytes` fails closed on an unmapped address.
        unsafe { er_game_base::mem::read_bytes(base + rva, &mut out) }.then_some(out)
    });
    match mismatch {
        None => Ok(()),
        Some((site, got)) => Err(format!(
            "eldenring.exe+{:X} ({}) reads {:02X?}, expected {:02X?}: this is not Elden Ring 1.17.1",
            site.rva, site.name, got, site.bytes
        )),
    }
}

impl Game {
    pub(crate) fn new(base: usize) -> Self {
        Self { base }
    }

    /// A game function by offset, typed by the caller.
    ///
    /// # Safety
    ///
    /// `F` must be the function's real signature; [`check_sites`] passed for this base.
    unsafe fn f<F: Copy>(&self, rva: usize) -> F {
        let addr = self.base + rva;
        debug_assert_eq!(size_of::<F>(), size_of::<usize>());
        // SAFETY: as the caller promises.
        unsafe { std::mem::transmute_copy(&addr) }
    }

    fn read_usize(&self, rva: usize) -> usize {
        // SAFETY: fails closed.
        unsafe { er_game_base::mem::safe_read_usize(self.base + rva) }.unwrap_or(0)
    }
}

/// 16-byte aligned scratch, for the bank and Wwise's out-parameters.
#[repr(C, align(16))]
struct Aligned<const N: usize>([u8; N]);

/// Load the bank unless this revision already is, as the table did: `GetPositioningInfo` answers
/// 1 only for a loaded node, and `LoadBankMemoryCopy` answers 69 when an older revision holds the
/// bank id, which is unloaded once first.
pub(crate) fn load_bank(game: &Game) -> Result<&'static str, String> {
    type Query = unsafe extern "C" fn(u32, *mut u8) -> u32;
    type Load = unsafe extern "C" fn(*const u8, u32, *mut u32) -> u32;
    type Unload = unsafe extern "C" fn(u32, *const u8, u64) -> u32;
    // SAFETY: signatures as the table called them; sites checked.
    let (query, load, unload) = unsafe {
        (
            game.f::<Query>(sites::AK_GET_POSITIONING_INFO),
            game.f::<Load>(sites::AK_LOAD_BANK_MEMORY_COPY),
            game.f::<Unload>(sites::AK_UNLOAD_BANK),
        )
    };
    let mut info = Box::new(Aligned([0u8; 0x400]));
    // SAFETY: game thread; `info` is 0x400 writable bytes.
    if unsafe { query(bank::SOUND_ID, info.0.as_mut_ptr()) } == 1 {
        return Ok("already loaded");
    }
    let bytes = bank::build();
    let mut buf = Box::new(Aligned([0u8; 1024]));
    buf.0[..bytes.len()].copy_from_slice(&bytes);
    let mut id = 0u32;
    // SAFETY: Wwise copies the bank before returning (LoadBankMemoryCopy).
    let mut r = unsafe { load(buf.0.as_ptr(), bytes.len() as u32, &mut id) };
    if r == 69 {
        // SAFETY: as above.
        let u = unsafe { unload(bank::BANK_ID, std::ptr::null(), 0) };
        if u != 1 {
            return Err(format!("UnloadBank of an older revision returned {u}"));
        }
        // SAFETY: as above.
        r = unsafe { load(buf.0.as_ptr(), bytes.len() as u32, &mut id) };
        if r == 1 {
            return Ok("loaded after unloading an older revision");
        }
    }
    if r == 1 {
        Ok("loaded")
    } else {
        Err(format!("LoadBankMemoryCopy returned {r} (1 = loaded)"))
    }
}

/// The other players in the session: every entry with a SteamID that is not ours.
///
/// # Safety
///
/// Game thread.
pub(crate) unsafe fn session_peers(local: u64, out: &mut Vec<u64>) {
    out.clear();
    // SAFETY: game thread.
    let Ok(session) = (unsafe { CSSessionManager::instance() }) else {
        return;
    };
    for p in session.players.iter() {
        let id = p.base.steam_id;
        if id != 0 && id != local && !p.is_local_player && !out.contains(&id) {
            out.push(id);
        }
    }
}

/// The game's own voice chat settings: Network > Voice Chat (`GameSettings+0x14`, kept per save)
/// and the voice chat volume under Sound (0..=10). Off and 0 until the settings exist.
///
/// # Safety
///
/// Game thread.
pub(crate) unsafe fn voice_settings() -> (bool, u8) {
    // SAFETY: game thread.
    let Ok(data) = (unsafe { GameDataMan::instance() }) else {
        return (false, 0);
    };
    let s = &data.game_settings;
    (s.voice_chat, s.voice_volume)
}

/// Whether the local player is in a world, which is when the bank and voices can be set up.
///
/// # Safety
///
/// Game thread.
pub(crate) unsafe fn in_world() -> bool {
    // SAFETY: game thread.
    unsafe { WorldChrMan::instance() }.is_ok_and(|w| w.main_player.is_some())
}

/// Counters for the status line.
#[derive(Default)]
pub(crate) struct Stats {
    pub created: u64,
    pub create_failed: u64,
    pub dropped_full: u64,
    pub pushes: u64,
    pub pushes_failed: u64,
    pub placed: u64,
    pub unplaced: u64,
    pub plays: u64,
}

/// One voice object.
struct Voice {
    object: *mut u8,
    entity: *mut u8,
}

pub(crate) struct Voices {
    slots: Slots,
    objects: Vec<Option<Voice>>,
    /// Wwise game object names are hashed into ids, so a per-process nonce keeps ours apart from
    /// any a Cheat Engine table registered earlier in this process.
    nonce: u32,
    pub stats: Stats,
}

// SAFETY: the pointers are game objects this task made; the task is the only code that touches
// them, and it runs on the game thread.
unsafe impl Send for Voices {}

impl Voices {
    pub(crate) fn new(nonce: u32) -> Self {
        Self {
            slots: Slots::default(),
            objects: Vec::new(),
            nonce,
            stats: Stats::default(),
        }
    }

    /// Play `pcm` (16-bit mono, 48 kHz) as `key`'s voice, at their character, or at ours when
    /// `local` (the loopback test).
    ///
    /// # Safety
    ///
    /// Game thread.
    pub(crate) unsafe fn play(&mut self, game: &Game, key: u64, local: bool, pcm: &[u8]) {
        let index = match self.slots.find_or_claim(key) {
            Lookup::Full => {
                self.stats.dropped_full += 1;
                return;
            }
            Lookup::Found(i) => i,
            Lookup::Claimed(i) => {
                // SAFETY: game thread.
                let made = unsafe { self.create(game, i) };
                let slot = self.slots.get_mut(i).expect("just claimed");
                match made {
                    Some(v) => {
                        slot.state = SlotState::Ready;
                        self.stats.created += 1;
                        voice_log(format_args!(
                            "voice: slot {i} made for {key:#018x} (object {:p})",
                            v.object
                        ));
                        self.objects.resize_with(i + 1, || None);
                        self.objects[i] = Some(v);
                    }
                    None => {
                        slot.state = SlotState::Failed;
                        self.stats.create_failed += 1;
                        voice_log(format_args!("voice: slot {i} for {key:#018x} failed"));
                    }
                }
                i
            }
        };
        let Some(Some(voice)) = self.objects.get(index) else {
            return;
        };
        let (object, entity) = (voice.object, voice.entity);

        type SetPosition = unsafe extern "C" fn(*mut u8, *const f32) -> u32;
        type Push = unsafe extern "C" fn(*mut u8, *const u8, u32) -> u32;
        type Play = unsafe extern "C" fn(*mut u8) -> u32;
        type EventOf = unsafe extern "C" fn(u32) -> u32;
        // SAFETY: signatures as the table called them; sites checked.
        let (set_position, push, play, event_of) = unsafe {
            (
                game.f::<SetPosition>(sites::MO_GAME_OBJECT_SET_POSITION),
                game.f::<Push>(sites::MO_AUDIO_INPUT_PUSH),
                game.f::<Play>(sites::MO_AUDIO_INPUT_PLAY),
                game.f::<EventOf>(sites::AK_GET_EVENT_ID_FROM_PLAYING_ID),
            )
        };

        // SAFETY: game thread.
        match unsafe { position_of(game, key, local) } {
            Some(p) => {
                // Position, then front (0,0,1) and top (0,1,0), as the table placed it.
                let at: [f32; 12] = [
                    p[0], p[1], p[2], 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                ];
                // SAFETY: a ready object; 12 floats.
                if unsafe { set_position(object, at.as_ptr()) } == 1 {
                    self.stats.placed += 1;
                }
            }
            None => self.stats.unplaced += 1,
        }
        // SAFETY: a ready object; `pcm` is read during the call.
        if unsafe { push(object, pcm.as_ptr(), pcm.len() as u32) } == 1 {
            self.stats.pushes += 1;
        } else {
            self.stats.pushes_failed += 1;
        }
        let slot = self.slots.get_mut(index).expect("found");
        let step = slot.after_push();
        let start = match step {
            PlayStep::Play => true,
            // SAFETY: a query on a playing id; 0 means it ended.
            PlayStep::CheckAlive => (unsafe { event_of(slot.playing_id) }) == 0,
            PlayStep::Nothing => false,
        };
        if start {
            // SAFETY: a ready object.
            if unsafe { play(object) } == 1 {
                // SAFETY: the entity outlives its object; Play wrote the playing id there.
                slot.playing_id = unsafe {
                    entity
                        .add(sites::ENTITY_PLAYING_ID)
                        .cast::<u32>()
                        .read_unaligned()
                };
                self.stats.plays += 1;
            } else {
                slot.playing_id = 0;
            }
        }
    }

    /// Make and register slot `i`'s audio input object: the table's `er_vp_create`.
    ///
    /// # Safety
    ///
    /// Game thread.
    unsafe fn create(&self, game: &Game, i: usize) -> Option<Voice> {
        type Alloc = unsafe extern "C" fn(u64, u32, *mut c_void) -> *mut u8;
        type Ctor = unsafe extern "C" fn(*mut u8);
        type SetName = unsafe extern "C" fn(*mut u8, *const u8);
        type Format = unsafe extern "C" fn(*mut u8, u32, u32, u32);
        type EventName = unsafe extern "C" fn(*mut u8, *const u8);
        type Init = unsafe extern "C" fn(*mut u8, u32, u32, *mut u8, *mut u8, *mut u8, *mut u32);
        type Manager = unsafe extern "C" fn() -> *mut u8;
        type Register = unsafe extern "C" fn(*mut u8, *mut u8) -> u32;
        type Entity = unsafe extern "C" fn(*mut u8, u32) -> *mut u8;
        // SAFETY: signatures as the table called them; sites checked.
        let (alloc, ctor, set_name, format, event_name, init, manager, register, entity_of) = unsafe {
            (
                game.f::<Alloc>(sites::ALLOC),
                game.f::<Ctor>(sites::MO_AUDIO_INPUT_CTOR),
                game.f::<SetName>(sites::MO_GAME_OBJECT_SET_NAME),
                game.f::<Format>(sites::AUDIO_FORMAT),
                game.f::<EventName>(sites::EVENT_NAME),
                game.f::<Init>(sites::MO_AUDIO_INPUT_INIT),
                game.f::<Manager>(sites::AUDIO_MANAGER),
                game.f::<Register>(sites::AUDIO_MANAGER_REGISTER),
                game.f::<Entity>(sites::AUDIO_INPUT_ENTITY),
            )
        };
        let allocator = match game.read_usize(sites::ALLOCATOR_OVERRIDE) {
            0 => game.read_usize(sites::ALLOCATOR_PRIMARY),
            a => a,
        };
        if allocator == 0 {
            return None;
        }
        // SAFETY: the game's allocator, as its own audio input setup calls it.
        let object = unsafe {
            alloc(
                sites::MO_AUDIO_INPUT_SIZE as u64,
                8,
                allocator as *mut c_void,
            )
        };
        if object.is_null() {
            return None;
        }
        // SAFETY: a fresh allocation of that size.
        unsafe { object.write_bytes(0, sites::MO_AUDIO_INPUT_SIZE) };
        // The name stays alive for the object's life, which is the process's.
        let name: &'static [u8] = Box::leak(
            format!("ErVoicePos_{}_{i}\0", self.nonce)
                .into_bytes()
                .into_boxed_slice(),
        );
        let mut fmt = Aligned([0u8; 0x20]);
        let mut play = Aligned([0u8; 0x80]);
        let mut stop = Aligned([0u8; 0x80]);
        let mut channels: [u32; 8] = [
            u32::MAX,
            u32::MAX,
            0,
            u32::MAX,
            u32::MAX,
            u32::MAX,
            u32::MAX,
            u32::MAX,
        ];
        // SAFETY: the table's sequence, with buffers of the sizes it gave.
        unsafe {
            ctor(object);
            set_name(object, name.as_ptr());
            format(fmt.0.as_mut_ptr(), crate::steam::SAMPLE_RATE, 0x4101, 16);
            event_name(play.0.as_mut_ptr(), bank::PLAY_EVENT_NAME.as_ptr());
            event_name(stop.0.as_mut_ptr(), bank::STOP_EVENT_NAME.as_ptr());
            init(
                object,
                0x10000,
                0x80,
                play.0.as_mut_ptr(),
                stop.0.as_mut_ptr(),
                fmt.0.as_mut_ptr(),
                channels.as_mut_ptr(),
            );
        }
        // SAFETY: as above.
        if unsafe { register(manager(), object) } != 1 {
            return None;
        }
        // SAFETY: the entity id field of a registered object.
        let entity_id = unsafe {
            object
                .add(sites::MO_AUDIO_INPUT_ENTITY_ID)
                .cast::<u32>()
                .read_unaligned()
        };
        // SAFETY: as above; the manager's entity table is at +0x38.
        let entity = unsafe { entity_of(manager().add(0x38), entity_id) };
        (!entity.is_null()).then_some(Voice { object, entity })
    }
}

/// Where `key`'s character stands: the main player when `local`, else the player the game's
/// session maps that SteamID to. `None` when there is no such character.
///
/// # Safety
///
/// Game thread.
unsafe fn position_of(game: &Game, key: u64, local: bool) -> Option<[f32; 3]> {
    type ByStemId = unsafe extern "C" fn(*const c_void, *const u64) -> *const u8;
    // SAFETY: game thread.
    let world = unsafe { WorldChrMan::instance() }.ok()?;
    let player: *const u8 = if local {
        let main = world.main_player.as_ref()?;
        std::ptr::from_ref(&**main).cast()
    } else {
        // SAFETY: the signature the table called it with; sites checked.
        let by_id = unsafe { game.f::<ByStemId>(sites::GET_PLAYER_INS_BY_STEAM_ID) };
        // SAFETY: game thread; `key` is read during the call.
        let p = unsafe { by_id(std::ptr::from_ref(world).cast(), &key) };
        if p.is_null() {
            return None;
        }
        p
    };
    // PlayerIns +0x190 modules, +0x68 physics, +0x70 position: the table's chain.
    // SAFETY: each step fails closed.
    unsafe {
        let modules = er_game_base::mem::safe_read_usize(player as usize + 0x190)?;
        let physics = er_game_base::mem::safe_read_usize(modules.checked_add(0x68)?)?;
        let at = physics.checked_add(0x70)?;
        Some([
            er_game_base::mem::safe_read_f32(at)?,
            er_game_base::mem::safe_read_f32(at + 4)?,
            er_game_base::mem::safe_read_f32(at + 8)?,
        ])
    }
}
