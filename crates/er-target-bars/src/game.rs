//! The locked-on character, read out of the live game as plain numbers.
//!
//! Read-only throughout: no detour, no write, no game function called. Every read happens on the
//! game thread from inside a `FrameBegin` task, so the ChrSets being walked are the ones the
//! engine owns at that instant and a freed character is simply not in them.
//!
//! Every offset was byte-checked against the installed 1.17.1 (`eldenring-deobf-1.17.1.bin`)
//! and then read live by `scripts/frida/target-bars-probe.js` on 2026-10-04 (213 readings across
//! several enemies, every lock resolved with a matching handle). The ones fromsoftware-rs also
//! names are pinned to its layout at compile time below, so the two cannot drift apart silently.

#![cfg(windows)]

use std::mem::offset_of;

use eldenring::cs::{
    CSChrDataModule, CSChrSuperArmorModule, ChrIns, ChrInsModuleContainer, ChrSet, PlayerIns,
    WorldChrMan,
};
use fromsoftware_shared::FromStatic;

use crate::model::{
    self, LockState, STATUS_COUNT, StanceReading, Status, StatusGauge, TargetReading,
};

/// `ChrIns+0x8`, the character's own `FieldInsHandle`; `LockTgtMan` copies it to the player's
/// lock-on slot (1.17.1 `0x1407180d2`).
const CHR_HANDLE: usize = 0x8;
/// `ChrIns+0x178`, the `SpecialEffect` container.
const CHR_SPECIAL_EFFECT: usize = 0x178;
/// `ChrIns+0x190`, the module container.
const CHR_MODULES: usize = 0x190;
/// `PlayerIns+0x6b0`, the lock-on target handle (read at 1.17.1 `0x140344e3a`).
const PLAYER_LOCKED_ON_ENEMY: usize = 0x6b0;

/// Module container slots: data `+0x0`, resist `+0x20`, super armor `+0x40`. The resist slot is
/// private in fromsoftware-rs, so it is read at its offset.
const MODULE_DATA: usize = 0x0;
const MODULE_RESIST: usize = 0x20;
const MODULE_SUPER_ARMOR: usize = 0x40;

/// `CSChrResistModule`: gauge `i32[7]` at `+0x10` (1.17.1 `0x14043de14`), resistance `i32[7]` at
/// `+0x2c` (`0x14043e9c5`).
const RESIST_GAUGE: usize = 0x10;
const RESIST_RESISTANCE: usize = 0x2c;

/// `SpecialEffect` head at `+0x8`; an entry carries its param row at `+0x0`, next at `+0x30`, its
/// remaining time at `+0x40` and flags at `+0x60` (the engine's own walk, 1.17.1 `0x1404fd490`).
const SPECIAL_EFFECT_HEAD: usize = 0x8;
const ENTRY_PARAM_ROW: usize = 0x0;
const ENTRY_NEXT: usize = 0x30;
const ENTRY_TIMER: usize = 0x40;
const ENTRY_FLAGS: usize = 0x60;
/// Entries with any of these flags are skipped by the engine's walk; they are being removed.
const ENTRY_DEAD_FLAGS: u32 = 0x800c_0003;
/// `SpEffectParam.stateInfo`, `u16` at `+0x156` (1.17.1 `0x14043e7e5`).
const PARAM_STATE_INFO: usize = 0x156;
/// Upper bound on a SpEffect list walk, so a list torn mid-edit cannot loop forever.
const ENTRY_WALK_LIMIT: usize = 512;

// The offsets above that fromsoftware-rs also names, held to its layout.
const _: () = assert!(offset_of!(ChrIns, field_ins_handle) == CHR_HANDLE);
const _: () = assert!(offset_of!(ChrIns, special_effect) == CHR_SPECIAL_EFFECT);
const _: () = assert!(offset_of!(ChrIns, modules) == CHR_MODULES);
const _: () = assert!(offset_of!(PlayerIns, locked_on_enemy) == PLAYER_LOCKED_ON_ENEMY);
const _: () = assert!(offset_of!(ChrInsModuleContainer, data) == MODULE_DATA);
const _: () = assert!(offset_of!(ChrInsModuleContainer, super_armor) == MODULE_SUPER_ARMOR);
const _: () = assert!(offset_of!(CSChrDataModule, hp) == 0x138);
const _: () = assert!(offset_of!(CSChrDataModule, max_hp) == 0x13c);
const _: () = assert!(offset_of!(CSChrDataModule, fp) == 0x148);
const _: () = assert!(offset_of!(CSChrDataModule, max_fp) == 0x14c);
const _: () = assert!(offset_of!(CSChrDataModule, stamina) == 0x154);
const _: () = assert!(offset_of!(CSChrDataModule, max_stamina) == 0x158);
const _: () = assert!(offset_of!(CSChrSuperArmorModule, sa_durability) == 0x10);
const _: () = assert!(offset_of!(CSChrSuperArmorModule, sa_durability_max) == 0x14);
const _: () = assert!(offset_of!(CSChrSuperArmorModule, recover_time) == 0x1c);

/// What one frame's look at the lock-on slot found.
pub(crate) enum Lookup {
    /// No world, or no local player yet.
    NoWorld,
    /// The lock-on slot says nothing, or something that is not a character.
    Unlocked,
    /// A character handle that no live character answers to.
    Stale(u64),
    /// The target, read.
    Found {
        handle: u64,
        address: usize,
        reading: TargetReading,
    },
}

/// Read `T` at `base + offset`.
///
/// # Safety
///
/// `base + offset` must be readable for `T` on the game thread.
unsafe fn read<T: Copy>(base: usize, offset: usize) -> T {
    unsafe { std::ptr::read_unaligned((base + offset) as *const T) }
}

/// A pointer field, or `None` when it is null or not plausibly a heap object.
///
/// # Safety
///
/// `base + offset` must be readable on the game thread.
unsafe fn pointer_at(base: usize, offset: usize) -> Option<usize> {
    let value: usize = unsafe { read(base, offset) };
    // SAFETY: integer arithmetic only, see the function's own docs.
    unsafe { er_game_base::mem::is_heap_aligned_ptr(value) }.then_some(value)
}

fn handle_of(chr_ins: &ChrIns) -> u64 {
    let handle = chr_ins.field_ins_handle;
    model::handle_bits(handle.selector.0, handle.block_id.0)
}

/// Every live character in the world as `(address, handle)`, set by set.
///
/// The same walk `er-npc-possess` and `er-enemynpc-effects` make, plus the player set: a lock-on
/// target can be another player. Duplicates (a set reachable twice) are skipped by address.
fn candidates(world_chr_man: &WorldChrMan) -> Vec<(usize, u64)> {
    let mut sets: Vec<usize> = Vec::with_capacity(64);
    sets.push(std::ptr::from_ref(&world_chr_man.player_chr_set) as usize);
    sets.push(std::ptr::from_ref(&world_chr_man.summon_buddy_chr_set) as usize);
    sets.push(std::ptr::from_ref(&world_chr_man.open_field_chr_set.base) as usize);
    for chr_set in world_chr_man.chr_sets.iter().flatten() {
        let address = chr_set.as_ptr() as usize;
        if !sets.contains(&address) {
            sets.push(address);
        }
    }
    let mut out = Vec::with_capacity(256);
    for address in sets {
        // SAFETY: every address is a ChrSet the world itself holds, inline or in `chr_sets`.
        // A `PlayerIns` begins with its `ChrIns`, so the player set reads correctly as one.
        let chr_set = unsafe { &*(address as *const ChrSet<ChrIns>) };
        for chr_ins in chr_set.characters() {
            let chr_ins: &ChrIns = chr_ins;
            out.push((std::ptr::from_ref(chr_ins) as usize, handle_of(chr_ins)));
        }
    }
    out
}

/// The status rows live on the target: per status, `Some(seconds left)` while one is.
///
/// # Safety
///
/// `chr` must be a live `ChrIns`, read on the game thread.
unsafe fn active_statuses(chr: usize) -> [Option<Option<f32>>; STATUS_COUNT] {
    let mut active = [None; STATUS_COUNT];
    let Some(container) = (unsafe { pointer_at(chr, CHR_SPECIAL_EFFECT) }) else {
        return active;
    };
    let mut entry: usize = unsafe { read(container, SPECIAL_EFFECT_HEAD) };
    for _ in 0..ENTRY_WALK_LIMIT {
        // SAFETY: integer arithmetic only.
        if !unsafe { er_game_base::mem::is_heap_aligned_ptr(entry) } {
            break;
        }
        // SAFETY: a list entry the engine links from this character's container.
        let flags: u32 = unsafe { read(entry, ENTRY_FLAGS) };
        if flags & ENTRY_DEAD_FLAGS == 0
            && let Some(row) = unsafe { pointer_at(entry, ENTRY_PARAM_ROW) }
            && let Some(status) = Status::from_state_info(unsafe { read(row, PARAM_STATE_INFO) })
        {
            let timer: f32 = unsafe { read(entry, ENTRY_TIMER) };
            active[status.index()] = Some(model::shown_seconds(timer));
        }
        entry = unsafe { read(entry, ENTRY_NEXT) };
    }
    active
}

/// Read the target at `chr`.
///
/// # Safety
///
/// `chr` must be a live `ChrIns` found in a ChrSet this frame, read on the game thread.
unsafe fn read_target(chr: usize, main_player_vtable: usize) -> TargetReading {
    // SAFETY: the caller's contract.
    let chr_ins = unsafe { &*(chr as *const ChrIns) };
    let vtable: usize = unsafe { read(chr, 0) };
    let mut reading = TargetReading {
        // Every player is a `CS::PlayerIns` and shares the main player's vtable; enemies are
        // `CS::EnemyIns` (docs/recon/lockon-filter-findings.md section 5).
        is_player: vtable == main_player_vtable,
        npc_param_id: chr_ins.npc_param_id,
        ..TargetReading::default()
    };
    if let Some(modules) = unsafe { pointer_at(chr, CHR_MODULES) } {
        if let Some(data) = unsafe { pointer_at(modules, MODULE_DATA) } {
            // SAFETY: the data module the character owns.
            let data = unsafe { &*(data as *const CSChrDataModule) };
            reading.hp = data.hp;
            reading.hp_max = data.max_hp;
            reading.fp = data.fp;
            reading.fp_max = data.max_fp;
            reading.stamina = data.stamina;
            reading.stamina_max = data.max_stamina;
        }
        if let Some(sa) = unsafe { pointer_at(modules, MODULE_SUPER_ARMOR) } {
            // SAFETY: the super armor module the character owns.
            let sa = unsafe { &*(sa as *const CSChrSuperArmorModule) };
            reading.stance = Some(StanceReading {
                current: sa.sa_durability,
                max: sa.sa_durability_max,
                recover_in: sa.recover_time,
            });
        }
        if let Some(resist) = unsafe { pointer_at(modules, MODULE_RESIST) } {
            let mut gauges = [StatusGauge::default(); STATUS_COUNT];
            for (index, gauge) in gauges.iter_mut().enumerate() {
                // SAFETY: two `i32[7]` arrays inside the 0xc0-byte resist module.
                *gauge = StatusGauge {
                    gauge: unsafe { read(resist, RESIST_GAUGE + index * 4) },
                    resistance: unsafe { read(resist, RESIST_RESISTANCE + index * 4) },
                };
            }
            reading.statuses = Some(gauges);
        }
    }
    reading.active = unsafe { active_statuses(chr) };
    reading
}

/// Look at the lock-on slot and read whoever it names.
///
/// # Safety
///
/// Must be called on the game thread.
pub(crate) unsafe fn lookup() -> Lookup {
    // SAFETY: singleton access on the game thread; `Err` before the world exists.
    let Ok(world_chr_man) = (unsafe { WorldChrMan::instance() }) else {
        return Lookup::NoWorld;
    };
    let Some(main_player) = world_chr_man.main_player.as_ref() else {
        return Lookup::NoWorld;
    };
    let player = std::ptr::from_ref::<PlayerIns>(main_player) as usize;
    // During boot the singleton exists before the player does and the slot can hold a stale
    // pointer, so the player is screened before any field is read (the er-invasion-path rule).
    // SAFETY: integer arithmetic, then a fault-tolerant read.
    if !unsafe { er_game_base::mem::is_heap_aligned_ptr(player) } {
        return Lookup::NoWorld;
    }
    let Ok(module_base) = er_game_base::mem::game_module_base() else {
        return Lookup::NoWorld;
    };
    let Some(player_vtable) = (unsafe { er_game_base::mem::safe_read_usize(player) }) else {
        return Lookup::NoWorld;
    };
    if !er_game_base::mem::vtable_in_game_image(player_vtable, module_base) {
        return Lookup::NoWorld;
    }
    let handle = main_player.locked_on_enemy;
    let want = model::handle_bits(handle.selector.0, handle.block_id.0);
    if model::lock_state(handle.selector.0) != LockState::Character {
        return Lookup::Unlocked;
    }
    let Some(address) = model::find_by_handle(want, candidates(world_chr_man)) else {
        return Lookup::Stale(want);
    };
    // SAFETY: an address read out of a live ChrSet this frame; the vtable check is a last screen
    // against a half-constructed slot.
    let vtable: usize = unsafe { read(address, 0) };
    if !er_game_base::mem::vtable_in_game_image(vtable, module_base) {
        return Lookup::Stale(want);
    }
    Lookup::Found {
        handle: want,
        address,
        // SAFETY: as above.
        reading: unsafe { read_target(address, player_vtable) },
    }
}
