//! Keeps the "Disallow Hostile Actions" status effect off the main player.
//!
//! # What the effect is
//!
//! SpEffectParam row 9621 is the only row with `stateInfo` 457 (`0x1c9`). Map event scripts apply
//! it -- Roundtable Hold's event 11102600, and the invasion-only no-attack regions elsewhere -- and
//! native code reads the state rather than the id: the pad update at 1.17.1 `0x1403daa90` disables
//! R1/R2/L1/L2 while `HasSpecialEffectWithStateInfo(se, 0x1c9)` holds, and `CanUseMagic` /
//! `CanUseGoods` refuse anything without `isUseNoAttackRegion` under the same state. See
//! `docs/er-mechanics/map-combat-restrictions.md`.
//!
//! # Where this says no
//!
//! At the single function that adds an entry to a `SpecialEffect` list: 1.16.2 `FUN_1404fd090`,
//! 1.17.1 `0x1404fde60`. It is the only caller of `NewSpecialEffectEntry` (1.16.2 `0x1404fb5f0`),
//! and its three callers are every way an effect goes on -- `CS::SpecialEffect::Apply` (reached
//! from apply-with-source `0x1403fade0`, which event scripts use) and the two direct wrappers at
//! 1.16.2 `0x1404f6e30` / `0x1404f6e90`. The detour looks the row up and returns `-1` -- the
//! function's own "refused" value, which a negative id already produces through its first branch
//! with no side effect -- when the row's `stateInfo` is 457 and the list belongs to the main player.
//!
//! The key is `stateInfo`, not the id, because the state is what the game checks: any row carrying
//! 457 locks the same buttons, so refusing by id alone would leave an equivalent row through.
//! Other characters keep the effect; the event scripts only aim it at the player.
//!
//! # Proof before code
//!
//! Prototyped live with `scripts/frida/speffect-9621-block.js` on 1.17.1, 2026-10-05: the same
//! predicate on the same function refused 9621 applied to the player through apply-with-source
//! (the call returned false, the list held no state-457 entry afterwards), while row 392 applied
//! through the identical call still landed.

// A windows `cdylib`: on a host build everything reached only from `DllMain` reads as dead, and
// the workspace denies warnings. The shipping target does not see this allow.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

use std::{
    fmt,
    path::PathBuf,
    sync::atomic::{AtomicU64, AtomicUsize, Ordering},
};

use er_game_base::log::{append_line, game_directory_path};

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;

const LOG_FILE_NAME: &str = "er-allow-hostile-actions.log";

/// `stateInfo` of "Disallow Hostile Actions" (SpEffectParam row 9621, the only row carrying it).
pub const DISALLOW_HOSTILE_ACTIONS_STATE_INFO: u16 = 457;

/// The add-entry function, 1.16.2 RVA. `er-hook` carries it to the running build
/// (1.17.1 `0x4fde60`).
///
/// `int FUN_1404fd090(SpecialEffect*, int id, FloatVector4*, FieldInsHandle, char,
/// float lifeReductionRate, byte, byte)`.
const SPECIAL_EFFECT_ADD_ENTRY_RVA: usize = 0x4fd090;

/// `GetSpEffectParam(SpEffectParamLookupResult* out, int id)`, 1.16.2 RVA (1.17.1 `0xd523a0`,
/// whose first five bytes are an Arxan `jmp` stub -- calling through it is what the game does).
const GET_SP_EFFECT_PARAM_RVA: u32 = 0xd5_05f0;

use er_game_base::rva::{WORLD_CHR_MAN_GLOBAL_RVA, WORLD_CHR_MAN_PLAYER_INS_OFFSET};

/// `SpecialEffect::owner`, the `ChrIns` the list belongs to.
const SPECIAL_EFFECT_OWNER_OFFSET: usize = 0x10;
/// `SP_EFFECT_PARAM_ST::stateInfo`, read by `HasSpecialEffectWithStateInfo` (1.17.1 `0x1404fa370`
/// as `movzwl 0x156(%rcx)`).
const PARAM_ROW_STATE_INFO_OFFSET: usize = 0x156;

/// The value the add returns for a refusal; `SpecialEffect::Apply` passes it on and apply-with-
/// source turns it into `false`.
const ADD_ENTRY_REFUSED: i32 = -1;

/// How many refusals are logged one by one before the log switches to every
/// [`REFUSAL_LOG_STRIDE`]th. Roundtable's event can re-apply the row each time its condition
/// becomes true again, so an unthrottled line per refusal could grow the log without bound.
const REFUSALS_LOGGED_INDIVIDUALLY: u64 = 16;
const REFUSAL_LOG_STRIDE: u64 = 256;

static LOG_SEQUENCE: AtomicU64 = AtomicU64::new(0);
static ORIG_ADD_ENTRY: AtomicUsize = AtomicUsize::new(0);
static GET_SP_EFFECT_PARAM: AtomicUsize = AtomicUsize::new(0);
static WORLD_CHR_MAN_SLOT: AtomicUsize = AtomicUsize::new(0);
static REFUSALS: AtomicU64 = AtomicU64::new(0);

#[cfg(windows)]
static START: std::sync::Once = std::sync::Once::new();

fn log_message(args: fmt::Arguments<'_>) {
    let path = game_directory_path()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
        .join(LOG_FILE_NAME);
    let seq = LOG_SEQUENCE.fetch_add(1, Ordering::SeqCst) + 1;
    append_line(&path, format_args!("[{seq:06}] {args}"));
}

/// Whether an add should be refused.
///
/// `owner` is the list's `ChrIns`, `main_player` the local `PlayerIns` (0 when there is none, as
/// on the title screen), and `state_info` the row's `stateInfo`, `None` when the row is absent.
pub fn should_refuse(id: i32, owner: usize, main_player: usize, state_info: Option<u16>) -> bool {
    id >= 0
        && main_player != 0
        && owner == main_player
        && state_info == Some(DISALLOW_HOSTILE_ACTIONS_STATE_INFO)
}

/// Whether refusal number `n` (1-based) gets its own log line.
fn refusal_is_logged(n: u64) -> bool {
    n <= REFUSALS_LOGGED_INDIVIDUALLY || n.is_multiple_of(REFUSAL_LOG_STRIDE)
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    _module: *mut core::ffi::c_void,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        er_game_base::panic_report::report_panics_to("er-allow-hostile-actions", log_message);
        er_hook::set_hook_logger(log_message);
        START.call_once(spawn_install_thread);
    }
    DLL_MAIN_SUCCESS
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_allow_hostile_actions_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

#[cfg(windows)]
fn spawn_install_thread() {
    let _ = std::thread::Builder::new()
        .name("er-allow-hostile-actions".to_owned())
        .spawn(|| {
            let found =
                er_game_base::wait::poll_until(|| er_game_base::mem::game_module_base().ok());
            let Some(base) = found else {
                log_message(format_args!(
                    "install: no game module base; nothing installed"
                ));
                return;
            };
            install(base);
        });
}

#[cfg(windows)]
fn install(base: usize) {
    use std::ffi::c_void;

    use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};

    let get_param =
        match er_game_base::mem::game_rva_named(GET_SP_EFFECT_PARAM_RVA, "GET_SP_EFFECT_PARAM_RVA")
        {
            Ok(address) => address,
            Err(why) => {
                log_message(format_args!(
                    "install: refused, GetSpEffectParam has no address on this build: {why}"
                ));
                return;
            }
        };
    let world_chr_man = er_game_base::mem::game_data_addr(
        base,
        WORLD_CHR_MAN_GLOBAL_RVA,
        "WORLD_CHR_MAN_GLOBAL_RVA",
    );
    if world_chr_man == 0 {
        log_message(format_args!(
            "install: refused, WorldChrMan global has no address on this build"
        ));
        return;
    }
    GET_SP_EFFECT_PARAM.store(get_param, Ordering::SeqCst);
    WORLD_CHR_MAN_SLOT.store(world_chr_man, Ordering::SeqCst);

    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            log_message(format_args!("install: MH_Initialize failed: {status:?}"));
            return;
        }
    }
    let target = base + SPECIAL_EFFECT_ADD_ENTRY_RVA;
    let hook = match unsafe { MhHook::new(target as *mut c_void, add_entry_hook as *mut c_void) } {
        Ok(hook) => hook,
        Err(status) => {
            log_message(format_args!(
                "install: MhHook::new(SpecialEffect add @1.16.2 0x{target:x}) failed: {status:?}"
            ));
            return;
        }
    };
    ORIG_ADD_ENTRY.store(hook.trampoline() as usize, Ordering::SeqCst);
    if let Err(status) = unsafe { hook.queue_enable() } {
        log_message(format_args!("install: queue_enable failed: {status:?}"));
        return;
    }
    match unsafe { MH_ApplyQueued() } {
        MH_STATUS::MH_OK => log_message(format_args!(
            "install: ACTIVE -- refusing stateInfo {DISALLOW_HOSTILE_ACTIONS_STATE_INFO} for the main player; add-entry @0x{:x}, GetSpEffectParam @0x{get_param:x}, WorldChrMan slot @0x{world_chr_man:x}",
            er_game_base::game_build::resolve_game_address(target, "SPECIAL_EFFECT_ADD_ENTRY_RVA")
                .unwrap_or(0),
        )),
        status => log_message(format_args!("install: MH_ApplyQueued failed: {status:?}")),
    }
}

/// `SpEffectParamLookupResult`, what `GetSpEffectParam` fills: the row pointer, the id, a flag.
#[repr(C)]
struct SpEffectParamLookup {
    row: *const u8,
    id: u32,
    flag: u8,
    _pad: [u8; 3],
}

#[cfg(windows)]
type GetSpEffectParamFn =
    unsafe extern "C" fn(*mut SpEffectParamLookup, i32) -> *mut SpEffectParamLookup;

#[cfg(windows)]
type AddEntryFn = unsafe extern "C" fn(*mut u8, i32, *const u8, u64, u8, f32, u8, u8) -> i32;

/// The row's `stateInfo`, through the game's own lookup.
#[cfg(windows)]
fn state_info_of(id: i32) -> Option<u16> {
    let address = GET_SP_EFFECT_PARAM.load(Ordering::Relaxed);
    if address == 0 {
        return None;
    }
    let lookup_fn: GetSpEffectParamFn = unsafe { std::mem::transmute(address) };
    let mut out = SpEffectParamLookup {
        row: std::ptr::null(),
        id: u32::MAX,
        flag: 0,
        _pad: [0; 3],
    };
    unsafe { lookup_fn(&raw mut out, id) };
    if out.row.is_null() {
        return None;
    }
    Some(unsafe {
        std::ptr::read_unaligned(out.row.add(PARAM_ROW_STATE_INFO_OFFSET).cast::<u16>())
    })
}

/// The local `PlayerIns`, or 0.
#[cfg(windows)]
fn main_player() -> usize {
    let slot = WORLD_CHR_MAN_SLOT.load(Ordering::Relaxed);
    if slot == 0 {
        return 0;
    }
    let world_chr_man = unsafe { std::ptr::read_volatile(slot as *const usize) };
    if world_chr_man == 0 {
        return 0;
    }
    unsafe {
        std::ptr::read_volatile((world_chr_man + WORLD_CHR_MAN_PLAYER_INS_OFFSET) as *const usize)
    }
}

/// Detour on the add-entry function. Exact signature: arguments five to eight are stack slots,
/// and the sixth is a float, so this cannot ride the integer-only hook union.
#[cfg(windows)]
#[allow(clippy::too_many_arguments)]
unsafe extern "C" fn add_entry_hook(
    special_effect: *mut u8,
    id: i32,
    position: *const u8,
    source_handle: u64,
    flag: u8,
    life_reduction_rate: f32,
    control_a: u8,
    control_b: u8,
) -> i32 {
    if id >= 0 && !special_effect.is_null() {
        let player = main_player();
        if player != 0 {
            let owner = unsafe {
                std::ptr::read_volatile(
                    special_effect
                        .add(SPECIAL_EFFECT_OWNER_OFFSET)
                        .cast::<usize>(),
                )
            };
            if owner == player && should_refuse(id, owner, player, state_info_of(id)) {
                let n = REFUSALS.fetch_add(1, Ordering::Relaxed) + 1;
                if refusal_is_logged(n) {
                    log_message(format_args!(
                        "refused SpEffect {id} (stateInfo {DISALLOW_HOSTILE_ACTIONS_STATE_INFO}) on the main player; refusals so far {n}"
                    ));
                }
                return ADD_ENTRY_REFUSED;
            }
        }
    }
    let orig = ORIG_ADD_ENTRY.load(Ordering::Relaxed);
    let original: AddEntryFn = unsafe { std::mem::transmute(orig) };
    unsafe {
        original(
            special_effect,
            id,
            position,
            source_handle,
            flag,
            life_reduction_rate,
            control_a,
            control_b,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PLAYER: usize = 0x8000_0000;

    #[test]
    fn refuses_state_457_on_the_main_player() {
        assert!(should_refuse(9621, PLAYER, PLAYER, Some(457)));
    }

    #[test]
    fn keys_on_state_info_not_on_the_id() {
        assert!(should_refuse(123_456, PLAYER, PLAYER, Some(457)));
        assert!(!should_refuse(9621, PLAYER, PLAYER, Some(0)));
    }

    #[test]
    fn leaves_other_characters_and_rows_alone() {
        assert!(!should_refuse(9621, PLAYER + 0x10, PLAYER, Some(457)));
        assert!(!should_refuse(392, PLAYER, PLAYER, Some(0)));
        assert!(!should_refuse(9621, PLAYER, PLAYER, None));
    }

    #[test]
    fn does_nothing_without_a_player_or_with_a_negative_id() {
        assert!(!should_refuse(9621, 0, 0, Some(457)));
        assert!(!should_refuse(-1, PLAYER, PLAYER, Some(457)));
    }

    #[test]
    fn refusal_log_is_throttled() {
        assert!((1..=REFUSALS_LOGGED_INDIVIDUALLY).all(refusal_is_logged));
        assert!(!refusal_is_logged(REFUSALS_LOGGED_INDIVIDUALLY + 1));
        assert!(refusal_is_logged(REFUSAL_LOG_STRIDE));
    }

    #[test]
    fn rvas_match_the_static_re() {
        assert_eq!(SPECIAL_EFFECT_ADD_ENTRY_RVA, 0x4fd090);
        assert_eq!(GET_SP_EFFECT_PARAM_RVA, 0xd505f0);
        assert_eq!(PARAM_ROW_STATE_INFO_OFFSET, 0x156);
        assert_eq!(SPECIAL_EFFECT_OWNER_OFFSET, 0x10);
    }
}
