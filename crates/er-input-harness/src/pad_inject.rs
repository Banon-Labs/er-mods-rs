//! In-world menu drive via the CS VIRTUAL-KEY layer (bd MENU-INPUT-LAYER-virtual-key-array-source-plus-
//! 0x88). The per-key array lives at `FD4PadDevice+0x88`, where the device is
//! `*(*(base+0x485dc20) + 0x18 + dev*8)` -- `FD4PadManager::padDevices[dev]`; index = `id-1000`, ids
//! 1000..1080, a `1` byte = "down this frame". It is rebuilt EVERY frame from GLOBAL_DLUserInputManager
//! by the builders
//! (deobf FUN_140240f20/FUN_1402411e0 dump = deobf 0x140240e70/0x140241130, CORRECTED 2026-07-23). Raw pad buttons (+0x890/+0x9f0) and inputmgr+0x90 are BOTH
//! off the read path (proven at runtime across 3 cycles). So we MinHook the builders and, AFTER the
//! original rebuilds the array, write our desired key id into `padDevices[dev]+0x88` (a pre-original write is
//! wiped by the rebuild). Edge-triggered: hold `1` one frame then `0` >=1 frame.
//!
//! The `id -> action` map (which of 1000..1080 = up/down/confirm/tab) is DLUID virtual-key numbering,
//! recovered empirically by the `probe` mode sweeping `set_vk_id` across the id range and watching the
//! menu respond.

use std::ffi::c_void;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};

use crate::log::harness_log;

// CORRECTED 2026-07-23 (bd ROOTCAUSE-padinject-builder-RVAs-are-wrong / CORRECTED-inworld-input-writer):
// the prior RVAs (0x240dc0/0x241080/0x2634b0) were WRONG -- they pointed at neighboring thunk stubs, so
// the MinHook detour never fired in-world (probe run4: builder_fires=0) and no injected input reached the
// menu. Recovered from the Ghidra dump: the writer 0x1426634a0's callers are the real builders
// 0x140240e70 / 0x140241130. Each builder loops ids 1000..0x438 and, for each down key, calls the
// writer with (device = padDevices[dev], id).
//
// RE-CONFIRMED 2026-08-31 against the 1.16.2 dump on :8765, which is what the MCP now serves; the
// old "dump = deobf, shifted" bookkeeping above and the `dump-deobf-shift` reference that used to
// stand here are both obsolete (that tool was cross-version and has been deleted -- for 1.16.2 the
// dump VA, the deobf VA and the runtime VA are the same address). The three entries below are byte
// checks in `eldenring-deobf.bin` (builder prologue `mov [rsp+8],rcx; push rbp/rsi/rdi/r12`; writer
// `lea eax,[rdx-0x3e8]; cmp eax,0x50` = id-1000 bounds-checked 0..80), and all three carry HELD on
// 1.17: the builders sit at the SAME addresses with 195/195 instructions aligned, and the writer
// pairs to 0x142665cb0 byte-for-byte.
const BUILDER_A_RVA: usize = 0x240e70; // rebuilds padDevices[dev]+0x88, loops ids 1000..1080
const BUILDER_B_RVA: usize = 0x241130; // FUN_1402411e0 (dump): twin builder (second device/slot)
const WRITER_RVA: usize = 0x26634a0; // writes padDevices[dev]+0x88[id-1000]=1 per down key; 1.17 0x2665cb0
const FD4_PAD_MANAGER_RVA: usize = 0x485dc20;
/// `FD4PadManager::padDevices` -- `DLFixedVector<FD4PadDevice*,4>` at +0x18 (Ghidra structure), the
/// array the game's own key writer indexes. Element `dev` is at `manager + 0x18 + dev*8`.
///
/// The `DLFixedVector` accessor emits an alignment fudge, `(-(u32)(manager + 0x18)) & 7`, before the
/// index; `manager` is 8-byte-aligned heap, so that term is always 0 and the address really is
/// `manager + 0x18 + dev*8`. Witnessed HELD on 1.17 by builder A (0x140240e70, 195/195 instructions
/// aligned, base rsi/rcx = the manager).
const PAD_MGR_DEVICES_18_OFFSET: usize = 0x18;
/// `FD4PadManager::padDevices.count`. `padDevices` is 0x30 bytes at +0x18, so its count sits at
/// +0x40 -- which is where the game itself bounds-checks (`cmp rbp, [rcx+0x40]` at the head of every
/// builder). Also witnessed HELD on 1.17 by that same alignment.
const PAD_DEVICES_COUNT_40_OFFSET: usize = 0x40;
/// Highest device index the fixed vector can hold, per its `DLFixedVector<...,4>` declaration. Used
/// only to clamp a count read out of live memory.
const PAD_DEVICES_MAX: usize = 4;
/// The per-key "down this frame" array on `FD4::FD4PadDevice`; entry `id-1000` is at
/// `device + 0x88 + (id-1000)*2` (2-byte stride, low byte written).
///
/// OWNER AND VALUE BOTH RE-MEASURED 2026-08-31 (bd
/// `vk-array-88-owner-is-FD4PadDevice-not-CSInGamePad-and-held-on-1170-2026-08-31`).
///
/// * The offset did NOT move on 1.17. The one function that writes this array, 1.16.2 0x1426634a0,
///   pairs to 1.17 0x142665cb0 and is byte-identical -- `mov byte [rcx+rdx*2+0x88],1` after a
///   `cmp eax,0x50` bound on `id-1000` -- so 0x88 is measured in both images, not carried.
/// * The OWNER is `FD4::FD4PadDevice`, not `CS::CSInGamePad`. All FOUR call sites of that writer
///   (0x140240e70, 0x140241130, 0x140e321b0, 0x140e32470) load `rcx` from `padDevices[dev]`, with no
///   exception; `FD4PadManager::Init` fills that array with `HeapAlloc(0x3c0)` + `FD4PadDevice::
///   FD4PadDevice` + `FD4PadDevice::vftable`. `FD4PadDevice`'s constructor (0x142663880 ->
///   0x142666090) aligns 168/168 with ZERO moved offsets and its allocation size is still 0x3c0.
///
/// The `padMaps` accessor FUN_1402413f0 is deliberately NOT declared here any more: it returns the
/// CSInGamePad, which merely HOLDS the device at `+0x10` (Ghidra names the type `CSInGamePad0x10`
/// after that field, and its constructor 0x1426647a0 does `param_1[2] = padDevices[dev]`), so it is
/// one indirection away from this array rather than a route to it.
///
/// That correction is load-bearing, because the previous code wrote this offset onto the
/// CSInGamePad from `padMaps` instead. The CSInGamePad is `HeapAlloc(0x98)` = 152 bytes, so
/// `0x88 + (id-1000)*2` leaves the object at id 1008 and every id above it wrote past the end of a
/// live game allocation -- silently, since the write is fault-safe and a heap overrun does not
/// fault. `scripts/check-object-field-offsets-1170.py` now pins this row.
const VK_ARRAY_88_OFFSET: usize = 0x88;
const VK_ID_MIN: u32 = 1000;
const VK_ID_MAX: u32 = 1080;
const HEAP_LO: usize = 0x10000;

/// The virtual-key id the drive currently wants held (0 = released). Set through `set_vk_id`; the
/// id->action map this was meant to be driven by was never recovered (see `set_vk_id`).
static DESIRED_VK_ID: AtomicU32 = AtomicU32::new(0);
static ORIG_BUILDER_A: AtomicUsize = AtomicUsize::new(0);
static ORIG_BUILDER_B: AtomicUsize = AtomicUsize::new(0);
static ORIG_WRITER: AtomicUsize = AtomicUsize::new(0);
static HOOKS_ACTIVE: AtomicUsize = AtomicUsize::new(0);
// Instrumentation (bd PROCESS-instrument-autonomously): did the hooks fire, and does my computed source
// match the game's real writer source? Answers the "wrong function / wrong object" questions with no
// user input.
static BUILDER_FIRES: AtomicU32 = AtomicU32::new(0);
static WRITER_FIRES: AtomicU32 = AtomicU32::new(0);
static GAME_SOURCE: AtomicUsize = AtomicUsize::new(0); // rcx of the real writer = the game's source
static MY_SOURCE: AtomicUsize = AtomicUsize::new(0); // source my inject_vk computed
static CACHED_PAD: AtomicUsize = AtomicUsize::new(0); // resolved FD4PadDevice, cached to skip per-frame RPM
/// One-shot latch for the "could not resolve a device" line. Without it the message would repeat
/// every frame, which is why the previous code said nothing at all -- and saying nothing is how the
/// drive stayed inert for six weeks with no fault, no refusal line and no counter moving.
static INERT_LOGGED: AtomicUsize = AtomicUsize::new(0);
static OBSERVED_IDS: [AtomicU32; 3] = [AtomicU32::new(0), AtomicU32::new(0), AtomicU32::new(0)]; // 81-bit set of ids the game's writer fired (id-1000)

/// Snapshot for the probe/drive to log: (builder_fires, writer_fires, game_source, my_source, obs_ids).
pub fn pad_snapshot() -> (u32, u32, usize, usize, [u32; 3]) {
    (
        BUILDER_FIRES.load(Ordering::SeqCst),
        WRITER_FIRES.load(Ordering::SeqCst),
        GAME_SOURCE.load(Ordering::SeqCst),
        MY_SOURCE.load(Ordering::SeqCst),
        [
            OBSERVED_IDS[0].load(Ordering::SeqCst),
            OBSERVED_IDS[1].load(Ordering::SeqCst),
            OBSERVED_IDS[2].load(Ordering::SeqCst),
        ],
    )
}

/// Probe API: inject a RAW virtual-key id (1000..1080) into `padDevices[dev]+0x88` each frame (0 = release).
///
/// This stays a RAW-id API on purpose. A typed `PadButton` wrapper used to sit in front of it and was
/// removed 2026-08-21: the planned `padDevices[dev]+0x88` id -> action map was never recovered, so every one of
/// its variants mapped to id `0` and the enum carried no reverse-engineered information at all.
///
/// The evidence behind that is negative and specific (bd
/// `DECISIVE-source88-does-NOT-drive-pausemenu-fullsweep`): ids 1000..1080 were swept against the
/// in-world pause menu and NONE produced a reproducible job/flags/tab/return-title response. The menu
/// is driven through `inputmgr+0x90+eventId` (`crate::input_inject::tap_menu_event`) instead. This entry
/// point survives only for explicit raw-id diagnostics, should a later RE pass find the real consumer.
pub fn set_vk_id(id: u32) {
    DESIRED_VK_ID.store(id, Ordering::SeqCst);
}

/// After a builder rebuilds `padDevices[dev]+0x88`, stamp the desired key id down. `manager` is the builder's
/// first arg (GLOBAL_FD4PadManager); `dev` is its device index (edx).
unsafe fn inject_vk(manager: usize, dev: usize) {
    let id = DESIRED_VK_ID.load(Ordering::SeqCst);
    if !(VK_ID_MIN..=VK_ID_MAX).contains(&id) {
        return;
    }
    let dev = dev & 0xffff_ffff;
    if manager < HEAP_LO {
        return;
    }
    let source = unsafe { *((manager + PAD_MGR_DEVICES_18_OFFSET + dev * 8) as *const usize) };
    MY_SOURCE.store(source, Ordering::SeqCst);
    if source < HEAP_LO {
        return;
    }
    // SAFETY: `source` is `padDevices[dev]`, the live `FD4PadDevice`, read exactly the way the game's
    // own writer computes its `this` (`mov rcx,[rcx+rsi+0x18]` at all four of its call sites);
    // +0x88+(id-1000)*2 is the per-key byte the
    // builder itself writes (RE-verified writer 0x1426634a0).
    unsafe {
        *((source + VK_ARRAY_88_OFFSET + ((id - VK_ID_MIN) as usize) * 2) as *mut u8) = 1;
    }
}

/// PER-FRAME DIRECT stamp of `id` into `FD4PadDevice+0x88`, resolving the device from the game base
/// (bd DECISIVE-builder-not-perframe-in-menu-need-perframe-direct-stamp). The builder that
/// `builder_*_hook` stamps after does NOT run per-frame while a menu is open (builder_fires stuck), so
/// builder-hook injection is too sparse to drive the menu; the array is read every frame, so the drive
/// must WRITE it every frame. `id`=0 (or out of range) is a no-op release. Guarded by HEAP_LO on every
/// deref; never panics.
///
/// THE DEVICE IS `padDevices[dev]`, NOT THE `padMaps` CSInGamePad. Between 2026-07-23 and 2026-08-31
/// this walked `padMaps` (+0x48) for a `CS::CSInGamePad_UserInput1` by TypeID and stamped THAT object's
/// +0x88. Every one of the four call sites of the game's own writer 0x1426634a0 loads `rcx` from
/// `manager + 0x18 + dev*8` instead, so the array is a field of `FD4::FD4PadDevice` and the tree-walk
/// was aimed at the wrong object -- one that is only `HeapAlloc(0x98)` = 152 bytes, so ids from 1008 up
/// wrote PAST THE END of a live game allocation. It never fired in practice (the TypeID needles are
/// `.data` RVAs with no 1.17 mapping, so `game_data_addr` refused them and the search matched nothing),
/// which is the only reason the overrun was never observed rather than a reason it was safe.
pub unsafe fn stamp_vk_direct(base: usize, id: u32, val: u8) {
    if !(VK_ID_MIN..=VK_ID_MAX).contains(&id) || base < HEAP_LO {
        return;
    }
    // FAULT-SAFE reads (ReadProcessMemory pseudo-handle) so a wrong offset returns None instead of
    // CRASHING the game (raw derefs froze run10/run11).
    let rd = |p: usize| -> Option<usize> {
        if p < HEAP_LO {
            None
        } else {
            unsafe { crate::win32::read_usize(p) }
        }
    };
    let off = VK_ARRAY_88_OFFSET + ((id - VK_ID_MIN) as usize) * 2;
    // CACHE the resolved device (bd BISECT-stamp_vk_direct-stops-drive): per-frame RPM walking stalls
    // the CSTaskImp task and STOPS the drive. Resolve once, then do ONE fault-safe write per frame.
    let cached = CACHED_PAD.load(Ordering::SeqCst);
    if cached >= HEAP_LO {
        unsafe {
            let _ = crate::win32::write_u8(cached + off, val);
        }
        return;
    }
    let Some(manager) = rd(er_game_base::mem::game_data_addr(
        base,
        FD4_PAD_MANAGER_RVA,
        "FD4_PAD_MANAGER_RVA",
    ))
    .filter(|m| *m >= HEAP_LO) else {
        report_inert("GLOBAL_FD4PadManager did not resolve or read back");
        return;
    };
    // The game bounds-checks `dev` against padDevices.count before every write; do the same, and clamp
    // to the vector's declared capacity so a garbage count cannot walk off the struct.
    let ndev = rd(manager + PAD_DEVICES_COUNT_40_OFFSET)
        .unwrap_or(1)
        .min(PAD_DEVICES_MAX);
    for dev in 0..ndev {
        let Some(device) =
            rd(manager + PAD_MGR_DEVICES_18_OFFSET + dev * 8).filter(|d| *d >= HEAP_LO)
        else {
            continue;
        };
        MY_SOURCE.store(device, Ordering::SeqCst);
        CACHED_PAD.store(device, Ordering::SeqCst);
        unsafe {
            let _ = crate::win32::write_u8(device + off, val);
        }
        harness_log!("pad-inject: padDevices[{dev}] = 0x{device:x} (vk array at +0x88)");
        return;
    }
    report_inert("no usable padDevices entry under the manager");
}

/// Say ONCE that the in-world virtual-key drive could not resolve a device. A drive that quietly
/// does nothing is indistinguishable from a drive whose input the game ignored, and this path has
/// already produced that exact confusion once.
fn report_inert(why: &str) {
    if INERT_LOGGED
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok()
    {
        harness_log!("pad-inject: {why}; the in-world virtual-key drive is INERT this run");
    }
}

unsafe extern "system" fn builder_a_hook(manager: usize, dev: usize, c: usize, d: usize) -> usize {
    BUILDER_FIRES.fetch_add(1, Ordering::SeqCst);
    let orig = ORIG_BUILDER_A.load(Ordering::SeqCst);
    let ret = if orig != 0 {
        let f: unsafe extern "system" fn(usize, usize, usize, usize) -> usize =
            unsafe { std::mem::transmute(orig) };
        unsafe { f(manager, dev, c, d) }
    } else {
        0
    };
    unsafe { inject_vk(manager, dev) };
    ret
}

unsafe extern "system" fn builder_b_hook(manager: usize, dev: usize, c: usize, d: usize) -> usize {
    BUILDER_FIRES.fetch_add(1, Ordering::SeqCst);
    let orig = ORIG_BUILDER_B.load(Ordering::SeqCst);
    let ret = if orig != 0 {
        let f: unsafe extern "system" fn(usize, usize, usize, usize) -> usize =
            unsafe { std::mem::transmute(orig) };
        unsafe { f(manager, dev, c, d) }
    } else {
        0
    };
    unsafe { inject_vk(manager, dev) };
    ret
}

/// Instrumentation hook on the real per-key writer (FUN_1426634a0): captures the game's actual `source`
/// (rcx) and the ids it writes (edx), so we can compare to our computed source and see the id map.
unsafe extern "system" fn writer_hook(source: usize, id: usize, c: usize, d: usize) -> usize {
    WRITER_FIRES.fetch_add(1, Ordering::SeqCst);
    GAME_SOURCE.store(source, Ordering::SeqCst);
    let vid = (id & 0xffff_ffff) as u32;
    if (VK_ID_MIN..=VK_ID_MAX).contains(&vid) {
        let rel = vid - VK_ID_MIN;
        let word = (rel / 32) as usize;
        if word < 3 {
            OBSERVED_IDS[word].fetch_or(1u32 << (rel % 32), Ordering::SeqCst);
        }
    }
    let orig = ORIG_WRITER.load(Ordering::SeqCst);
    if orig != 0 {
        let f: unsafe extern "system" fn(usize, usize, usize, usize) -> usize =
            unsafe { std::mem::transmute(orig) };
        unsafe { f(source, id, c, d) }
    } else {
        0
    }
}

fn install_one(
    base: usize,
    rva: usize,
    detour: *mut c_void,
    orig: &AtomicUsize,
    name: &str,
) -> bool {
    let addr = (base + rva) as *mut c_void;
    match unsafe { MhHook::new(addr, detour) } {
        Ok(hook) => {
            orig.store(hook.trampoline() as usize, Ordering::SeqCst);
            if unsafe { hook.queue_enable() }.is_ok() {
                // The hook must stay installed for the life of the process. `er_hook` declares no
                // `Drop` impl at all (uninstalling is `MH_RemoveHook`, which nothing here calls), so
                // letting the handle fall out of scope leaks it by construction -- the
                // `std::mem::forget` that used to sit here was a no-op dressed up as an intent.
                harness_log!("pad-inject: hooked {name} at 0x{:x}", addr as usize);
                true
            } else {
                harness_log!("pad-inject: {name} queue_enable failed");
                false
            }
        }
        Err(status) => {
            harness_log!("pad-inject: {name} MhHook::new failed: {status:?}");
            false
        }
    }
}

/// Install the virtual-key builder hooks once. Returns true when active.
pub fn install_pad_poll_hook(base: usize) -> bool {
    let _ = FD4_PAD_MANAGER_RVA; // manager arg is passed to the builders; global kept for reference
    if HOOKS_ACTIVE.load(Ordering::SeqCst) != 0 {
        return true;
    }
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            harness_log!("pad-inject: MH_Initialize failed: {status:?}");
            return false;
        }
    }
    let a = install_one(
        base,
        BUILDER_A_RVA,
        builder_a_hook as *mut c_void,
        &ORIG_BUILDER_A,
        "builder_a",
    );
    let b = install_one(
        base,
        BUILDER_B_RVA,
        builder_b_hook as *mut c_void,
        &ORIG_BUILDER_B,
        "builder_b",
    );
    let w = install_one(
        base,
        WRITER_RVA,
        writer_hook as *mut c_void,
        &ORIG_WRITER,
        "writer(instrument)",
    );
    if (a || b || w) && matches!(unsafe { MH_ApplyQueued() }, MH_STATUS::MH_OK) {
        HOOKS_ACTIVE.store(1, Ordering::SeqCst);
        harness_log!(
            "pad-inject: virtual-key builder hooks active (inject into padDevices+0x88; a={a} b={b})"
        );
        true
    } else {
        harness_log!("pad-inject: MH_ApplyQueued failed or no hook");
        false
    }
}
