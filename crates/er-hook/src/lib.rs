//! Shared MinHook FFI wrapper + cross-DLL hook union.
//!
//! Extracted verbatim from `er-quickload/src/mh.rs` (consolidation only, behavior-preserving):
//! the MinHook-generic FFI (`MH_*` externs, `MH_STATUS`), the `MhHook` wrapper, and the hook union
//! (`register_union_hook` + the cross-DLL chaining) now live here so the three game cdylibs share one
//! copy and MinHook's C source is compiled once (build.rs) instead of in each crate.
//!
//! The product-specific `#[no_mangle] er_effects_union_register` C export is deliberately NOT here --
//! it stays defined in `er-quickload` so only `er_quickload.dll` exports that cross-DLL symbol.
// PARITY: this crate transcribes MinHook's C ABI, so its names, casing and the items it
// declares-but-does-not-call are the upstream header's shape rather than this repo's.
// A per-item allow would mean annotating essentially every line of a binding file.
#![allow(dead_code, non_snake_case, non_camel_case_types, missing_docs)]

use std::ffi::c_void;
use std::ptr::null_mut;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

// ============================================================================
// LOGGING SEAM. `mh.rs` logged union-chain and registry-collision events through the product DLL's
// `telemetry::append_autoload_debug`. That sink is product-specific, so this shared crate calls
// through a function pointer the product installs at startup via `set_hook_logger`. Default is a
// no-op (no logger installed). `er-quickload` installs its telemetry sink in DllMain BEFORE any hook
// is registered, so every line the old in-product union code emitted is still emitted, to the same
// log. Crates that only use the raw `MH_*` externs (er-reload-trace, er-input-harness) never
// touch the union and never install a logger; the seam stays inert for them.
// ============================================================================
/// Signature of a logging sink: the union/registry code hands it `format_args!` output.
pub type HookLogFn = fn(std::fmt::Arguments<'_>);
static HOOK_LOGGER: AtomicUsize = AtomicUsize::new(0);

/// Install the sink for union/registry log lines. Call once, early (before any hook registration) to
/// preserve the exact logging the in-product `mh.rs` union produced.
///
/// It also installs the SAME sink for `er-game-base`'s address-resolution lines, rather than
/// leaving that a second call every caller has to remember. Every cdylib statically links its own
/// copy of both crates, so an uninstalled sink is silent PER DLL -- and on 2026-08-28 that cost a
/// diagnosis: `er-armament-icons` logged `MH_ERROR_UNSUPPORTED_FUNCTION`, which is both MinHook's
/// genuine "cannot hook this" AND the code `MhHook::new` returns when the build gate REFUSES an
/// address. With no sink installed there was no line saying which, for an address that is in the
/// verified translation table and so should not have been refused at all. One sink, one call.
pub fn set_hook_logger(logger: HookLogFn) {
    HOOK_LOGGER.store(logger as usize, Ordering::Release);
    er_game_base::game_build::set_address_logger(logger);
}

fn hook_log(args: std::fmt::Arguments<'_>) {
    let raw = HOOK_LOGGER.load(Ordering::Acquire);
    if raw != 0 {
        // SAFETY: `raw` is only ever a `HookLogFn` stored by `set_hook_logger`.
        let logger: HookLogFn = unsafe { std::mem::transmute::<usize, HookLogFn>(raw) };
        logger(args);
    }
}

// ============================================================================
// HOOK UNION (2026-07-16, user-directed). MinHook binds ONE detour per address,
// so two features hooking the same game function silently drop one -- the native-
// Windows menu race. This unions them: the FIRST feature to hook an address installs
// a single dispatcher detour (from a fixed pool, so no runtime codegen) that owns the
// real trampoline; every feature's handler is chained by pointing its existing `orig`
// slot at the NEXT handler, with the LAST handler's `orig` = the real game trampoline.
// A handler that calls its orig now calls the next handler in the chain (or the game),
// so existing handlers work unchanged and NO handler is ever silently dropped.
//
// Constraint: the shared signature is `extern "system" fn(usize,usize,usize,usize)->usize`
// -- correct for the integer/pointer <=4-arg game functions we contend on (menu/dialog
// Run/activate/build). A handler using fewer args just ignores the extras; unused
// register args are harmless. Not for float-arg or >4-stack-arg targets.
// ============================================================================
pub type UnionFn = unsafe extern "system" fn(usize, usize, usize, usize) -> usize;
// 96 slots: this DLL's own union targets PLUS a companion DLL's (the log-only
// er-reload-trace routes its ~40 native load/menu hooks through THIS DLL's union via
// the `er_effects_union_register` export, so a single MinHook instance owns every shared
// address instead of two instances corrupting each other's trampolines). One slot per
// unique game address; chained handlers on the same address share a slot.
const MAX_UNION_SLOTS: usize = 96;

struct UnionEntry {
    target: usize,
    trampoline: usize,
    /// handler fn ptr + its caller-owned `orig` slot, in chain order.
    handlers: Vec<(usize, &'static AtomicUsize)>,
}
static UNIONS: Mutex<Vec<UnionEntry>> = Mutex::new(Vec::new());
/// Lock-free head-handler per slot, read on every dispatch (no mutex in the hot path).
#[allow(clippy::declare_interior_mutable_const)]
static UNION_HEADS: [AtomicUsize; MAX_UNION_SLOTS] =
    [const { AtomicUsize::new(0) }; MAX_UNION_SLOTS];

unsafe extern "system" fn union_dispatch<const N: usize>(
    a: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    let head = UNION_HEADS[N].load(Ordering::Acquire);
    if head == 0 {
        return 0;
    }
    let f: UnionFn = unsafe { std::mem::transmute::<usize, UnionFn>(head) };
    unsafe { f(a, b, c, d) }
}

macro_rules! union_dispatchers {
    ($($n:literal)*) => { [ $( union_dispatch::<$n> as UnionFn ),* ] };
}
static DISPATCHERS: [UnionFn; MAX_UNION_SLOTS] = union_dispatchers!(
    0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23
    24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47
    48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 64 65 66 67 68 69 70 71
    72 73 74 75 76 77 78 79 80 81 82 83 84 85 86 87 88 89 90 91 92 93 94 95
);

/// Register `handler` on `target`, chaining through `orig_slot`. First registrant installs
/// the dispatcher + owns the trampoline; later ones append and no handler is ever dropped.
///
/// # Safety
/// `handler` must be a valid `UnionFn` matching the target's ABI; `orig_slot` must be the
/// static the handler reads to call its original.
pub unsafe fn register_union_hook(
    target: usize,
    handler: UnionFn,
    orig_slot: &'static AtomicUsize,
) -> Result<(), MH_STATUS> {
    // Resolved BEFORE anything else: `target` is the union's identity key, so a translated
    // address must be the key too -- otherwise one feature unions on the 1.16.2 address and
    // another on the 1.17 one, and MinHook ends up with two instances on the same function.
    let target = match resolve_target(target, &format!("register_union_hook 0x{target:x}")) {
        Some(resolved) => resolved,
        None => return Err(MH_STATUS::MH_ERROR_UNSUPPORTED_FUNCTION),
    };
    unsafe { register_union_hook_resolved(target, handler, orig_slot) }
}

/// [`register_union_hook`] on an address that has ALREADY been resolved for the running build.
///
/// RESOLUTION IS NOT IDEMPOTENT, and assuming it was is what made this split necessary. The
/// translation table is keyed by 1.16.2 RVA and its VALUES are 1.17 RVAs, so feeding a translated
/// address back in asks "where did 0x11d0b80 move to" -- a question with no entry, whose honest
/// answer is REFUSED. Measured 2026-08-28: `register_shared_hook` resolved, then handed the result
/// to `register_shared_hook_with_budget`, which resolved again; `er-armament-icons` lost its
/// file-open observer at 0x1411ced80 to `MH_ERROR_UNSUPPORTED_FUNCTION` even though that address is
/// in the verified table and its 1.17 prologue is byte-identical and perfectly hookable.
///
/// # Safety
/// Same contract as [`register_union_hook`], plus: `target` must already be correct for the
/// running build.
///
/// NOT `#[cfg(windows)]`, because its caller `register_union_hook` is not either -- gating only the
/// callee is a host build error, not a smaller binary.
unsafe fn register_union_hook_resolved(
    target: usize,
    handler: UnionFn,
    orig_slot: &'static AtomicUsize,
) -> Result<(), MH_STATUS> {
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        s => return Err(s),
    }
    let handler_addr = handler as usize;
    let mut unions = UNIONS.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(entry) = unions.iter_mut().find(|e| e.target == target) {
        // already skip a duplicate registration of the SAME handler (idempotent retries).
        if entry.handlers.iter().any(|(h, _)| *h == handler_addr) {
            return Ok(());
        }
        if let Some((_, prev_orig)) = entry.handlers.last() {
            prev_orig.store(handler_addr, Ordering::Release); // prev -> new
        }
        orig_slot.store(entry.trampoline, Ordering::Release); // new -> game orig
        entry.handlers.push((handler_addr, orig_slot));
        hook_log(format_args!(
            "HOOK UNION: game addr 0x{target:x} now chains {} handlers (added {})",
            entry.handlers.len(),
            as_dll_off(handler_addr)
        ));
        return Ok(());
    }
    let slot = unions.len();
    if slot >= MAX_UNION_SLOTS {
        return Err(MH_STATUS::MH_ERROR_MEMORY_ALLOC);
    }
    let mut trampoline = null_mut();
    unsafe {
        MH_CreateHook(
            target as *mut c_void,
            DISPATCHERS[slot] as *mut c_void,
            &mut trampoline,
        )
    }
    .ok()?;
    // ARM THE SLOT BEFORE ENABLING THE DETOUR. These two stores used to happen AFTER
    // `MH_EnableHook`, leaving a window in which the dispatcher was live but its head was still 0
    // -- and `union_dispatch` returns 0 for a null head WITHOUT calling the game. On a rarely-hit
    // target that window is invisible; on a hot one like the Scaleform file-open wrapper (called
    // throughout boot) a single unlucky call would hand the engine a NULL File* instead of the
    // asset it asked for. The dispatcher is unreachable until the detour is enabled, so publishing
    // the head first is free.
    UNION_HEADS[slot].store(handler_addr, Ordering::Release);
    orig_slot.store(trampoline as usize, Ordering::Release); // sole handler -> game orig
    match unsafe { MH_EnableHook(target as *mut c_void) } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ENABLED => {}
        s => {
            // Nothing is patched, so leave no armed head behind for a later slot reuse to inherit.
            UNION_HEADS[slot].store(0, Ordering::Release);
            orig_slot.store(0, Ordering::Release);
            return Err(s);
        }
    }
    unions.push(UnionEntry {
        target,
        trampoline: trampoline as usize,
        handlers: vec![(handler_addr, orig_slot)],
    });
    Ok(())
}

// ============================================================================
// CROSS-DLL UNION -- THE COMPANION SIDE (2026-08-23).
//
// `register_union_hook` above unions handlers inside ONE DLL, and cannot do more than that:
// its registry, its dispatcher pool and its MinHook instance are all statics, and a statically
// linked crate's statics are PER DLL. Two cdylibs that both link this crate therefore own two
// INDEPENDENT MinHook instances. If both detour one prologue, the second `MH_CreateHook` gets
// `MH_ERROR_ALREADY_CREATED`: the loser reports installed, never runs, and every feature behind
// it looks unimplemented -- nothing crashes and nothing logs an error.
//
// That is measured, not hypothetical. `er-quickload` and `er-armament-icons` both detour
// `TITLE_SCALEFORM_FILE_OPEN_RVA` (0x11ced80); in an eleven-native profile the product reported
// `file_open_observer_installed = true` with `file_open_hits = 0` for an entire session and every
// GFx swap it owns went silently vanilla, while the same build loaded ALONE reported 113 hits
// (bd armament-icons-and-product-share-scaleform-fileopen-rva-2026-08-23).
//
// The product DLL publishes its union as the `er_effects_union_register` C export, so the fix is
// for every OTHER DLL to register through that export instead of its own instance -- one MinHook
// instance owns the prologue and both handlers CHAIN. [`register_shared_hook`] is that call: it
// uses the product's union when the product is in the process and this DLL's own union when it is
// not, so a standalone run of the companion behaves exactly as before.
// ============================================================================

/// C-ABI shape of the product DLL's `er_effects_union_register` export
/// (`crates/er-quickload/src/mh.rs`): `(target, handler, *mut orig_slot) -> 0 ok | -1 null slot |
/// positive `MH_STATUS` on MinHook failure`.
pub type UnionRegisterFn = unsafe extern "system" fn(usize, UnionFn, *mut usize) -> i32;

/// Which MinHook instance a [`register_shared_hook`] call ended up on. Worth logging: it is the
/// difference between "chained onto the product's detour" and "installed a second instance that
/// may be about to lose a trampoline race".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HookRoute {
    /// Chained into `er_quickload.dll`'s single union -- the product is co-loaded.
    ProductUnion,
    /// This DLL's own union -- the product is absent, or this IS the product.
    LocalUnion,
}

/// The product DLL as me3 loads it, matched by base name rather than by path.
#[cfg(windows)]
const PRODUCT_DLL_NAME: &[u8] = b"er_quickload.dll\0";
// DELIBERATELY STILL `er_effects_`, after the 2026-08-26 rename of the crate to `er-quickload`
// and the repo to `er-mods-rs`. This name is an ABI, not branding: seven crates resolve it out of
// the product DLL by string through GetProcAddress, and users install these DLLs one at a time
// from separate releases. Renaming it would make an already-downloaded `er_invasion_warp.dll`
// fail to find the union next to a freshly built product, fall back to its own MinHook instance,
// and corrupt the shared trampoline -- with nothing in any gate to say so. The exports that DID
// move (`er_quickload_loading_screen_data`) have exactly one consumer, built in the same pass.
#[cfg(windows)]
const UNION_REGISTER_EXPORT: &[u8] = b"er_effects_union_register\0";

/// Default poll budget for [`register_shared_hook`]: ~1s at 25ms.
///
/// A budget is needed rather than a single probe because me3 loads natives in PROFILE ORDER and
/// nothing guarantees the product comes first -- `er-dll-closure.py` emits the product first for
/// exactly this reason, but a hand-written profile need not. A companion whose install thread runs
/// before the product's `LoadLibrary` would see no module at all, take the local union, and
/// recreate the collision this API exists to remove. Both natives are loaded within a few
/// milliseconds of each other, so this budget is orders of magnitude past the real race; the
/// fallback is correct behaviour, not a failure, so overshooting costs nothing but a late arm.
#[cfg(windows)]
const PRODUCT_RESOLVE_TRIES: u32 = 40;
#[cfg(windows)]
const PRODUCT_RESOLVE_SLEEP_MS: u32 = 25;

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetModuleHandleA(name: *const u8) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
    fn Sleep(ms: u32);
}

/// Resolve the product DLL's `er_effects_union_register` export, polling `tries` times at
/// `sleep_ms` intervals. `None` means the product is not in this process (a standalone companion
/// run) or this DLL *is* the product -- in both cases the caller owns the address itself.
///
/// Pass `tries = 1, sleep_ms = 0` for a non-blocking probe.
#[cfg(windows)]
pub fn resolve_product_union_register(tries: u32, sleep_ms: u32) -> Option<UnionRegisterFn> {
    for attempt in 0..tries.max(1) {
        let hmod = unsafe { GetModuleHandleA(PRODUCT_DLL_NAME.as_ptr()) };
        // Resolving our OWN export would route right back into the local union through a C-ABI
        // round trip. Same outcome, so this is a clarity guard rather than a correctness one --
        // but it also means the product can call `register_shared_hook` without special-casing.
        if !hmod.is_null() && hmod as usize != dll_base() {
            let proc = unsafe { GetProcAddress(hmod, UNION_REGISTER_EXPORT.as_ptr()) };
            if !proc.is_null() {
                // SAFETY: the export's C-ABI shape is fixed by the product DLL, and both images
                // stay mapped for the process lifetime, so the pointer stays valid.
                return Some(unsafe { std::mem::transmute::<*mut c_void, UnionRegisterFn>(proc) });
            }
        }
        if attempt + 1 < tries.max(1) && sleep_ms > 0 {
            unsafe { Sleep(sleep_ms) };
        }
    }
    None
}

/// Register `handler` on `target` through whichever union owns the process's MinHook instance for
/// it: the product DLL's when the product is co-loaded, this DLL's own otherwise.
///
/// Use this -- never a bare [`MhHook`] -- for any prologue a SECOND ME3 DLL might also detour.
/// `scripts/check-shared-hook-rvas.py` is the gate that finds those addresses;
/// `scripts/me3-dll-conflicts.toml` records each one.
///
/// # Safety
/// `handler` must be a valid [`UnionFn`] matching `target`'s ABI (<=4 integer/pointer args), and
/// `orig_slot` must be the `'static` cell that handler reads to call its original. Note that the
/// value stored there may be the NEXT handler in the chain rather than the game trampoline, so the
/// handler must call it through the 4-argument [`UnionFn`] signature, not the game's narrower one.
#[cfg(windows)]
pub unsafe fn register_shared_hook(
    target: usize,
    handler: UnionFn,
    orig_slot: &'static AtomicUsize,
) -> Result<HookRoute, MH_STATUS> {
    let target = match resolve_target(target, &format!("register_shared_hook 0x{target:x}")) {
        Some(resolved) => resolved,
        None => return Err(MH_STATUS::MH_ERROR_UNSUPPORTED_FUNCTION),
    };
    // The RESOLVED path, deliberately: resolving twice refuses. See
    // [`register_union_hook_resolved`] for what that cost.
    unsafe {
        register_shared_hook_resolved(
            target,
            handler,
            orig_slot,
            PRODUCT_RESOLVE_TRIES,
            PRODUCT_RESOLVE_SLEEP_MS,
        )
    }
}

/// [`register_shared_hook`] with an explicit resolve budget.
///
/// Pass `tries = 1, sleep_ms = 0` when the caller is driven by a GAME FRAME rather than by its own
/// install thread. The default budget exists because a companion's install thread can outrun me3's
/// `LoadLibrary` of the product; a game task tick cannot -- every native in the profile is loaded
/// long before `CSTaskImp` exists -- so one probe is already the right answer there, and the
/// polling budget would only be a stall on the game thread when the product is genuinely absent.
///
/// # Safety
/// Same contract as [`register_shared_hook`].
#[cfg(windows)]
pub unsafe fn register_shared_hook_with_budget(
    target: usize,
    handler: UnionFn,
    orig_slot: &'static AtomicUsize,
    tries: u32,
    sleep_ms: u32,
) -> Result<HookRoute, MH_STATUS> {
    let target = match resolve_target(
        target,
        &format!("register_shared_hook_with_budget 0x{target:x}"),
    ) {
        Some(resolved) => resolved,
        None => return Err(MH_STATUS::MH_ERROR_UNSUPPORTED_FUNCTION),
    };
    unsafe { register_shared_hook_resolved(target, handler, orig_slot, tries, sleep_ms) }
}

/// [`register_shared_hook_with_budget`] on an already-resolved address. See
/// [`register_union_hook_resolved`] for why the split exists.
///
/// # Safety
/// Same contract, plus: `target` must already be correct for the running build.
#[cfg(windows)]
unsafe fn register_shared_hook_resolved(
    target: usize,
    handler: UnionFn,
    orig_slot: &'static AtomicUsize,
    tries: u32,
    sleep_ms: u32,
) -> Result<HookRoute, MH_STATUS> {
    if let Some(register) = resolve_product_union_register(tries, sleep_ms) {
        // AtomicUsize is a repr(transparent) usize, so handing the product a `*mut usize` into our
        // own static is sound; our image outlives every dispatch.
        let slot_ptr = orig_slot.as_ptr();
        return match unsafe { register(target, handler, slot_ptr) } {
            0 => Ok(HookRoute::ProductUnion),
            // -1 is the export's null-slot rejection, which cannot happen here (the pointer comes
            // from a live static) -- reported as UNKNOWN rather than silently mapped to a status.
            code if code < 0 => Err(MH_STATUS::MH_UNKNOWN),
            code => Err(mh_status_from_i32(code)),
        };
    }
    unsafe { register_union_hook_resolved(target, handler, orig_slot) }
        .map(|()| HookRoute::LocalUnion)
}

/// Reconstruct an [`MH_STATUS`] from the `i32` the cross-DLL export returns.
fn mh_status_from_i32(code: i32) -> MH_STATUS {
    match code {
        0 => MH_STATUS::MH_OK,
        1 => MH_STATUS::MH_ERROR_ALREADY_INITIALIZED,
        2 => MH_STATUS::MH_ERROR_NOT_INITIALIZED,
        3 => MH_STATUS::MH_ERROR_ALREADY_CREATED,
        4 => MH_STATUS::MH_ERROR_NOT_CREATED,
        5 => MH_STATUS::MH_ERROR_ENABLED,
        6 => MH_STATUS::MH_ERROR_DISABLED,
        7 => MH_STATUS::MH_ERROR_NOT_EXECUTABLE,
        8 => MH_STATUS::MH_ERROR_UNSUPPORTED_FUNCTION,
        9 => MH_STATUS::MH_ERROR_MEMORY_ALLOC,
        10 => MH_STATUS::MH_ERROR_MEMORY_PROTECT,
        11 => MH_STATUS::MH_ERROR_MODULE_NOT_FOUND,
        12 => MH_STATUS::MH_ERROR_FUNCTION_NOT_FOUND,
        _ => MH_STATUS::MH_UNKNOWN,
    }
}

/// Central hook registry (2026-07-16). Every MinHook detour creation records its TARGET game address
/// here. MinHook binds only ONE detour per address: when a second feature hooks an address that is
/// already claimed, MH_CreateHook returns MH_ERROR_ALREADY_CREATED and the loser's handler NEVER runs.
/// Which detour wins depends on thread install order, so on native Windows it is a non-deterministic
/// race (Wine's scheduler happens to be consistent, which is why it looks fine there). This registry
/// turns that invisible race into an explicit LOGGED COLLISION at install time, naming the game offset
/// and both detours -- so a contested address (the root of the menu flakiness) is visible immediately
/// instead of surfacing as a flaky runtime bug. Idea + design credit: user, 2026-07-16.
static HOOK_REGISTRY: Mutex<Vec<(usize, usize)>> = Mutex::new(Vec::new());

/// Our DLL's load base, so detours can be reported as `dll+0xNNN` (identifiable against the map/disasm)
/// instead of an absolute pointer that shifts every launch.
fn dll_base() -> usize {
    use std::sync::OnceLock;
    static BASE: OnceLock<usize> = OnceLock::new();
    *BASE.get_or_init(|| {
        unsafe extern "system" {
            fn GetModuleHandleExW(flags: u32, addr: *const c_void, module: *mut *mut c_void)
            -> i32;
        }
        const FROM_ADDRESS: u32 = 0x4;
        const UNCHANGED_REFCOUNT: u32 = 0x2;
        let mut h: *mut c_void = null_mut();
        let anchor = dll_base as *const c_void; // any address inside our DLL
        if unsafe { GetModuleHandleExW(FROM_ADDRESS | UNCHANGED_REFCOUNT, anchor, &mut h) } != 0 {
            h as usize
        } else {
            0
        }
    })
}

fn as_dll_off(p: usize) -> String {
    let b = dll_base();
    if b != 0 && p >= b {
        format!("dll+0x{:x}", p - b)
    } else {
        format!("0x{p:x}")
    }
}

fn registry_record(target: usize, detour: usize, create_status: MH_STATUS) {
    if let Ok(mut reg) = HOOK_REGISTRY.lock() {
        let prior: Vec<String> = reg
            .iter()
            .filter(|(t, _)| *t == target)
            .map(|(_, d)| as_dll_off(*d))
            .collect();
        reg.push((target, detour));
        if !prior.is_empty() || create_status == MH_STATUS::MH_ERROR_ALREADY_CREATED {
            hook_log(format_args!(
                "HOOK REGISTRY COLLISION: game addr 0x{target:x} already hooked by detour(s) [{}], NOW ALSO detour {} (MH_CreateHook={create_status:?}) -- only ONE binds, the loser's handler never fires (silent native-Windows race source)",
                prior.join(", "),
                as_dll_off(detour)
            ));
        }
    }
}

#[allow(non_camel_case_types)]
#[must_use]
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MH_STATUS {
    MH_UNKNOWN = -1,
    MH_OK = 0,
    MH_ERROR_ALREADY_INITIALIZED,
    MH_ERROR_NOT_INITIALIZED,
    MH_ERROR_ALREADY_CREATED,
    MH_ERROR_NOT_CREATED,
    MH_ERROR_ENABLED,
    MH_ERROR_DISABLED,
    MH_ERROR_NOT_EXECUTABLE,
    MH_ERROR_UNSUPPORTED_FUNCTION,
    MH_ERROR_MEMORY_ALLOC,
    MH_ERROR_MEMORY_PROTECT,
    MH_ERROR_MODULE_NOT_FOUND,
    MH_ERROR_FUNCTION_NOT_FOUND,
}

unsafe extern "system" {
    pub fn MH_Initialize() -> MH_STATUS;
    pub fn MH_Uninitialize() -> MH_STATUS;
    pub fn MH_CreateHook(
        pTarget: *mut c_void,
        pDetour: *mut c_void,
        ppOriginal: *mut *mut c_void,
    ) -> MH_STATUS;
    pub fn MH_EnableHook(pTarget: *mut c_void) -> MH_STATUS;
    pub fn MH_QueueEnableHook(pTarget: *mut c_void) -> MH_STATUS;
    pub fn MH_DisableHook(pTarget: *mut c_void) -> MH_STATUS;
    pub fn MH_QueueDisableHook(pTarget: *mut c_void) -> MH_STATUS;
    pub fn MH_ApplyQueued() -> MH_STATUS;
}

impl MH_STATUS {
    pub fn ok_context(self, _context: &str) -> Result<(), MH_STATUS> {
        self.ok()
    }

    pub fn ok(self) -> Result<(), MH_STATUS> {
        if self == MH_STATUS::MH_OK {
            Ok(())
        } else {
            Err(self)
        }
    }
}

// ============================================================================
// BUILD GATE (2026-08-28). Every game address in this workspace is a 1.16.2 RVA. ELDEN RING 1.17
// moved code, and a detour installed at a stale RVA does not fail -- it lands mid-function and
// corrupts the game: `0x1407ada40` is a real prologue in 1.16.2 and `xor r15d, r15d` in 1.17, and
// hooking it killed a boot with an access violation whose backtrace blames game code.
//
// MinHook cannot catch this. It refuses only what it cannot DECODE (several hooks did come back
// MH_ERROR_UNSUPPORTED_FUNCTION on 1.17); mid-function bytes that happen to decode are installed
// happily. So the check has to be "is this the build these addresses came from", asked once, here,
// where every detour in every DLL of this workspace passes through.
//
// Scope is deliberately the DETOUR installers only. The raw byte primitives below
// (`write_code_byte`, `patch_3byte_stub`, `apply_xor_ret_stub`) stay ungated: the last two
// validate the byte they overwrite and abort on a mismatch, and `write_code_byte` is what
// `er-ersc-sigshim` uses to repair Seamless Co-op on exactly the builds this gate calls
// unsupported -- gating it would break the one thing that currently works on 1.17.
// ============================================================================

// Verified 1.16.2 -> 1.17 address pairs, generated by `build.rs` from
// `docs/recon/rva-map-1162-to-1170.verified.tsv`.

/// Where a detour should actually go, given the build that is running.
///
/// Three answers, and the middle one is the point of this whole migration:
///
/// * the address as given -- the running build is the one the RVA came from, or the address is
///   outside the game image (a Win32 detour, correct on every build);
/// * a TRANSLATED address -- the running build moved the function, and this pair was verified as
///   the same function: `scripts/map-rvas-1162-to-1170.py` found it by masked signature and
///   `scripts/verify-rva-map-1170.py` then confirmed the normalised instruction sequences are
///   identical over the body, not just the prologue;
/// * `None` -- the build moved the function and nothing here knows where to. Refusing is the only
///   safe answer: `0x1407ada40` is a real prologue in 1.16.2 and `xor r15d, r15d` in 1.17, and
///   detouring it killed a boot.
///
/// A translation is logged with both addresses, because a hook silently landing somewhere other
/// than where the source says is exactly the kind of thing a reader of a crash log needs told.
fn resolve_target(target: usize, what: &str) -> Option<usize> {
    // The table and the decision both live in `er-game-base`, because a stale address is just as
    // reachable as a direct CALL as it is as a detour, and one copy of the rule is the only way
    // both paths can agree. The hook log keeps its own line so a reader of the hook log is not
    // sent to a second file to find out that an address was moved.
    let resolved = er_game_base::game_build::resolve_game_address(target, what);
    match resolved {
        Some(address) if address != target => {
            hook_log(format_args!(
                "HOOK TRANSLATED ({what}): 0x{target:x} -> 0x{address:x}"
            ));
        }
        None => hook_log(format_args!(
            "HOOK REFUSED ({what}): {} -- this address has no verified mapping for the running \
             build, so installing here would detour whatever code now occupies it",
            er_game_base::game_build::describe_build()
        )),
        Some(_) => {}
    }
    resolved
}

/// Original address, hook function address, and trampoline for a given hook.
pub struct MhHook {
    addr: *mut c_void,
    hook_impl: *mut c_void,
    trampoline: *mut c_void,
}

impl MhHook {
    /// # Safety
    ///
    /// Installs native code detours; caller must ensure ABI and lifetime are valid.
    pub unsafe fn new(addr: *mut c_void, hook_impl: *mut c_void) -> Result<Self, MH_STATUS> {
        let addr =
            match resolve_target(addr as usize, &format!("MhHook::new 0x{:x}", addr as usize)) {
                Some(resolved) => resolved as *mut c_void,
                None => return Err(MH_STATUS::MH_ERROR_UNSUPPORTED_FUNCTION),
            };
        let mut trampoline = null_mut();
        let status = unsafe { MH_CreateHook(addr, hook_impl, &mut trampoline) };
        registry_record(addr as usize, hook_impl as usize, status);
        status.ok_context("MH_CreateHook")?;

        Ok(Self {
            addr,
            hook_impl,
            trampoline,
        })
    }

    pub fn trampoline(&self) -> *mut c_void {
        self.trampoline
    }

    /// # Safety
    ///
    /// Enables a native detour through MinHook's queued API.
    pub unsafe fn queue_enable(&self) -> Result<(), MH_STATUS> {
        unsafe { MH_QueueEnableHook(self.addr) }.ok_context("MH_QueueEnableHook")
    }

    /// # Safety
    ///
    /// Disables a native detour through MinHook's queued API.
    pub unsafe fn queue_disable(&self) -> Result<(), MH_STATUS> {
        unsafe { MH_QueueDisableHook(self.addr) }.ok_context("MH_QueueDisableHook")
    }
}

// ============================================================================
// RAW CODE-PATCH PRIMITIVES (moved from `er-quickload/src/experiments/mem.rs`,
// docs/plans/experiments-crate-targets.md S5). Behaviour-preserving move: the bodies are the
// product's, and every log string is unchanged. They belong here because they are the same
// "reach into the game image and rewrite bytes" capability MinHook itself provides, and both
// consumers (the product DLL and er-title-flow) already depend on this crate -- so hosting them
// here deletes the two `TitleFlowHost` fn-pointer seams that existed only to reach back into the
// product for them.
//
// Kept as two functions rather than one because their log text differs and this is a MOVE, not a
// redesign. `apply_xor_ret_stub` is `patch_3byte_stub` plus a success line and an
// "online-disable"-prefixed abort line; deduping them changes what a diagnostic log says and is
// deliberately left for a separate slice.
//
// The `windows` crate is NOT pulled in for this -- er-hook has zero `[dependencies]` and keeps it
// that way, following the raw-extern pattern already used above for `GetModuleHandleExW` and the
// `MH_*` family.
// ============================================================================

/// Init value for the `VirtualProtect` out-params; overwritten by the call.
const PAGE_PROTECT_UNSET: u32 = 0;
/// `PAGE_EXECUTE_READWRITE` (winnt.h), the protection a code patch needs.
const PAGE_EXECUTE_READWRITE: u32 = 0x40;
/// Win32 `BOOL` false; `VirtualProtect` returns zero on failure.
const WIN32_FALSE: i32 = 0;
/// `-1` cast to a handle: the current-process pseudo-handle `FlushInstructionCache` accepts
/// without an `OpenProcess` round-trip.
const CURRENT_PROCESS_PSEUDO_HANDLE: isize = -1;
/// Both primitives write exactly the 3 bytes of a `[u8; 3]` stub.
const STUB_LEN: usize = 3;
const BYTE_STEP: usize = 1;
const BYTE_START: usize = 0;

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn VirtualProtect(
        addr: *mut c_void,
        size: usize,
        new_protect: u32,
        old_protect: *mut u32,
    ) -> i32;
    /// Flush the CPU instruction cache after patching executable code so other threads see the
    /// new bytes (current-process pseudo-handle -1).
    fn FlushInstructionCache(process: isize, base: *const c_void, size: usize) -> i32;
}

/// Bytes touched by [`write_code_byte`]. Named so the protection, the store, and the cache flush
/// visibly agree on one length.
const ONE_CODE_BYTE: usize = 1;

/// The page operations a code-byte write performs, behind a seam. [`Win32CodePage`] is the only
/// production implementation; the seam exists because the two ways this primitive can be wrong are
/// both invisible to a compile check -- a page left `PAGE_EXECUTE_READWRITE` after the write, and a
/// refused protection change that stores the byte anyway -- so the SEQUENCE is asserted on the host
/// instead of only in a game. `er-scaleform-hooks` keeps its native hook owner testable the same
/// way.
trait CodePageOps {
    /// `VirtualProtect`: returns whether the protection change was allowed, writing the previous
    /// protection into `old_protect`.
    fn protect(&mut self, addr: usize, len: usize, new_protect: u32, old_protect: &mut u32)
    -> bool;

    /// # Safety
    ///
    /// `addr` must be writable for the duration of the call.
    unsafe fn store(&mut self, addr: usize, value: u8);

    /// Flush the instruction cache so threads already inside this code see the new byte.
    fn flush(&mut self, addr: usize, len: usize);
}

/// Shared body of [`write_code_byte`]: unlock, store, relock to the PREVIOUS protection, flush.
///
/// Returns whether the protection change was allowed. A refused change returns before the store,
/// so nothing is written and no protection is left changed.
///
/// # Safety
///
/// With [`Win32CodePage`], `address` must be a byte of currently-mapped code in this process that
/// is safe to overwrite; the store is an unsynchronised write into executable memory.
unsafe fn write_code_byte_with<O: CodePageOps>(ops: &mut O, address: usize, value: u8) -> bool {
    let mut old_protect = PAGE_PROTECT_UNSET;
    if !ops.protect(
        address,
        ONE_CODE_BYTE,
        PAGE_EXECUTE_READWRITE,
        &mut old_protect,
    ) {
        hook_log(format_args!(
            "write_code_byte: VirtualProtect failed at 0x{address:x}"
        ));
        return false;
    }
    unsafe { ops.store(address, value) };
    let mut restored = PAGE_PROTECT_UNSET;
    ops.protect(address, ONE_CODE_BYTE, old_protect, &mut restored);
    ops.flush(address, ONE_CODE_BYTE);
    true
}

/// The production [`CodePageOps`]: Win32 `VirtualProtect` + `FlushInstructionCache` against the
/// current process.
#[cfg(windows)]
struct Win32CodePage;

#[cfg(windows)]
impl CodePageOps for Win32CodePage {
    fn protect(
        &mut self,
        addr: usize,
        len: usize,
        new_protect: u32,
        old_protect: &mut u32,
    ) -> bool {
        let allowed = unsafe { VirtualProtect(addr as *mut c_void, len, new_protect, old_protect) };
        allowed != WIN32_FALSE
    }

    unsafe fn store(&mut self, addr: usize, value: u8) {
        unsafe { *(addr as *mut u8) = value };
    }

    fn flush(&mut self, addr: usize, len: usize) {
        unsafe { FlushInstructionCache(CURRENT_PROCESS_PSEUDO_HANDLE, addr as *const c_void, len) };
    }
}

/// Write a single byte of executable code at `address`, with the protection dance the write needs:
/// `PAGE_EXECUTE_READWRITE`, the store, the original protection back, then an instruction-cache
/// flush so threads already inside that code see the new byte.
///
/// Returns whether `VirtualProtect` allowed the write. It deliberately does NOT report whether the
/// byte landed: a caller patching game code should read it back, because another mod can own the
/// same address, and a successful `VirtualProtect` says nothing about that.
///
/// Unlike [`patch_3byte_stub`] and [`apply_xor_ret_stub`], this does NOT validate the byte it
/// overwrites. Those two abort when the existing byte is not the expected one, which is what stops
/// a version-drifted RVA from being patched mid-instruction; a caller of this primitive gets no
/// such guard and must check the address itself.
///
/// # Safety
///
/// `address` must be a byte of currently-mapped code in this process that is safe to overwrite.
/// The store is unsynchronised: it is a single byte, so it cannot tear, but a thread may execute
/// the patched instruction at any point during the call.
#[cfg(windows)]
pub unsafe fn write_code_byte(address: usize, value: u8) -> bool {
    unsafe { write_code_byte_with(&mut Win32CodePage, address, value) }
}

/// Write a self-contained 3-byte return stub at `base+rva` after validating the expected first
/// byte. RWX via VirtualProtect, write, restore, icache flush. Returns true on success. Shared by
/// the gate-force patches (foreground / sign-in / user-index).
#[cfg(windows)]
pub fn patch_3byte_stub(
    base: usize,
    rva: usize,
    expected_first: u8,
    stub: [u8; STUB_LEN],
    label: &str,
) -> bool {
    let target = (base + rva) as *mut u8;
    let existing = unsafe { *target };
    if existing != expected_first {
        hook_log(format_args!(
            "{label}: ABORT -- byte at 0x{:x} is 0x{existing:x}, expected 0x{expected_first:x}",
            base + rva
        ));
        return false;
    }
    let mut old_protect = PAGE_PROTECT_UNSET;
    let protect_ok = unsafe {
        VirtualProtect(
            target as *mut c_void,
            STUB_LEN,
            PAGE_EXECUTE_READWRITE,
            &mut old_protect,
        )
    };
    if protect_ok == WIN32_FALSE {
        hook_log(format_args!("{label}: VirtualProtect failed"));
        return false;
    }
    let mut i = BYTE_START;
    while i < STUB_LEN {
        unsafe { *target.add(i) = stub[i] };
        i += BYTE_STEP;
    }
    let mut restored = PAGE_PROTECT_UNSET;
    unsafe { VirtualProtect(target as *mut c_void, STUB_LEN, old_protect, &mut restored) };
    unsafe {
        FlushInstructionCache(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            target as *const c_void,
            STUB_LEN,
        )
    };
    true
}

/// Patch a 0x48-prologue function body to `xor eax,eax; ret` (return 0) at `base+rva`. Validates
/// the expected first byte, VirtualProtects RWX, writes the 3-byte stub, restores protection, and
/// flushes the icache. Used to force-offline the IsOnlineMode getter + login-readiness predicate.
///
/// `expected_first` and `stub` were `ONLINE_DISABLE_EXPECTED_FIRST` / `ONLINE_DISABLE_STUB` read
/// from product constants; they are parameters now because this crate cannot see the product's
/// constant tree. Callers pass the same two values, so the rendered log text is unchanged.
#[cfg(windows)]
pub fn apply_xor_ret_stub(
    base: usize,
    rva: usize,
    expected_first: u8,
    stub: [u8; STUB_LEN],
    label: &str,
) {
    let target = (base + rva) as *mut u8;
    let existing = unsafe { *target };
    if existing != expected_first {
        hook_log(format_args!(
            "online-disable: ABORT {label} -- byte at 0x{:x} is 0x{existing:x}, expected 0x{expected_first:x}",
            base + rva
        ));
        return;
    }
    let mut old_protect = PAGE_PROTECT_UNSET;
    let protect_ok = unsafe {
        VirtualProtect(
            target as *mut c_void,
            STUB_LEN,
            PAGE_EXECUTE_READWRITE,
            &mut old_protect,
        )
    };
    if protect_ok == WIN32_FALSE {
        hook_log(format_args!(
            "online-disable: VirtualProtect failed for {label}"
        ));
        return;
    }
    let mut i = BYTE_START;
    while i < STUB_LEN {
        unsafe { *target.add(i) = stub[i] };
        i += BYTE_STEP;
    }
    let mut restored = PAGE_PROTECT_UNSET;
    unsafe { VirtualProtect(target as *mut c_void, STUB_LEN, old_protect, &mut restored) };
    unsafe {
        FlushInstructionCache(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            target as *const c_void,
            STUB_LEN,
        )
    };
    hook_log(format_args!(
        "online-disable: patched {label} 0x{:x} -> xor eax,eax;ret (forces offline)",
        base + rva
    ));
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A page operation, recorded rather than performed.
    #[derive(Debug, PartialEq, Eq)]
    enum Op {
        Protect {
            addr: usize,
            len: usize,
            new_protect: u32,
        },
        Store {
            addr: usize,
            value: u8,
        },
        Flush {
            addr: usize,
            len: usize,
        },
    }

    /// Stands in for a real code page. `original_protect` is what it reports as the page's previous
    /// protection, so a test can assert that exact value is handed back on the second call.
    struct FakePage {
        ops: Vec<Op>,
        original_protect: u32,
        protect_allowed: bool,
    }

    impl FakePage {
        fn allowing(original_protect: u32) -> Self {
            Self {
                ops: Vec::new(),
                original_protect,
                protect_allowed: true,
            }
        }

        fn refusing() -> Self {
            Self {
                ops: Vec::new(),
                original_protect: 0,
                protect_allowed: false,
            }
        }
    }

    impl CodePageOps for FakePage {
        fn protect(
            &mut self,
            addr: usize,
            len: usize,
            new_protect: u32,
            old_protect: &mut u32,
        ) -> bool {
            self.ops.push(Op::Protect {
                addr,
                len,
                new_protect,
            });
            if !self.protect_allowed {
                return false;
            }
            *old_protect = self.original_protect;
            true
        }

        unsafe fn store(&mut self, addr: usize, value: u8) {
            self.ops.push(Op::Store { addr, value });
        }

        fn flush(&mut self, addr: usize, len: usize) {
            self.ops.push(Op::Flush { addr, len });
        }
    }

    const PAGE_EXECUTE_READ: u32 = 0x20;
    const TEST_ADDR: usize = 0x1234_5678;
    const TEST_BYTE: u8 = 0xcc;

    /// The whole sequence, in order: unlock to RWX, store, relock, flush.
    #[test]
    fn writes_between_unlocking_and_relocking_then_flushes() {
        let mut page = FakePage::allowing(PAGE_EXECUTE_READ);

        let wrote = unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

        assert!(wrote);
        assert_eq!(
            page.ops,
            vec![
                Op::Protect {
                    addr: TEST_ADDR,
                    len: ONE_CODE_BYTE,
                    new_protect: PAGE_EXECUTE_READWRITE,
                },
                Op::Store {
                    addr: TEST_ADDR,
                    value: TEST_BYTE,
                },
                Op::Protect {
                    addr: TEST_ADDR,
                    len: ONE_CODE_BYTE,
                    new_protect: PAGE_EXECUTE_READ,
                },
                Op::Flush {
                    addr: TEST_ADDR,
                    len: ONE_CODE_BYTE,
                },
            ]
        );
    }

    /// The hazard: a patched page left writable-and-executable for the rest of the process. The
    /// relock must name the protection the page actually had, not a guess and not RWX.
    #[test]
    fn does_not_leave_the_page_executable_and_writable() {
        for original in [PAGE_EXECUTE_READ, 0x02, 0x04, 0x80] {
            let mut page = FakePage::allowing(original);

            unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

            let last_protect = page
                .ops
                .iter()
                .filter_map(|op| match op {
                    Op::Protect { new_protect, .. } => Some(*new_protect),
                    _ => None,
                })
                .next_back()
                .expect("a protection change");
            assert_eq!(
                last_protect, original,
                "page relocked to the wrong protection"
            );
            assert_ne!(last_protect, PAGE_EXECUTE_READWRITE, "page left RWX");
        }
    }

    /// A refused protection change must abort before the store. Writing anyway would fault, or
    /// worse, succeed on a page that was already writable and hide the refusal.
    #[test]
    fn refused_protection_change_writes_nothing() {
        let mut page = FakePage::refusing();

        let wrote = unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

        assert!(!wrote);
        assert_eq!(
            page.ops,
            vec![Op::Protect {
                addr: TEST_ADDR,
                len: ONE_CODE_BYTE,
                new_protect: PAGE_EXECUTE_READWRITE,
            }],
            "nothing may follow a refused VirtualProtect"
        );
    }

    /// What the `ONE_CODE_BYTE` doc claims: the protection, the store and the flush cover the same
    /// one byte at the same address. A length that disagreed would unlock or flush a range the
    /// caller never asked about.
    #[test]
    fn every_page_operation_covers_the_same_single_byte() {
        let mut page = FakePage::allowing(PAGE_EXECUTE_READ);

        unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

        assert_eq!(ONE_CODE_BYTE, size_of::<u8>());
        for op in &page.ops {
            let (addr, len) = match op {
                Op::Protect { addr, len, .. } | Op::Flush { addr, len } => (*addr, *len),
                Op::Store { addr, .. } => (*addr, ONE_CODE_BYTE),
            };
            assert_eq!(addr, TEST_ADDR, "{op:?} touched a different address");
            assert_eq!(len, ONE_CODE_BYTE, "{op:?} covered a different length");
        }
    }
}
