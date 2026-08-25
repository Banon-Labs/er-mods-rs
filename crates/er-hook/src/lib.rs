//! Shared MinHook FFI wrapper + cross-DLL hook union.
//!
//! Extracted verbatim from `er-effects-rs/src/mh.rs` (consolidation only, behavior-preserving):
//! the MinHook-generic FFI (`MH_*` externs, `MH_STATUS`), the `MhHook` wrapper, and the hook union
//! (`register_union_hook` + the cross-DLL chaining) now live here so the three game cdylibs share one
//! copy and MinHook's C source is compiled once (build.rs) instead of in each crate.
//!
//! The product-specific `#[no_mangle] er_effects_union_register` C export is deliberately NOT here --
//! it stays defined in `er-effects-rs` so only `er_effects_rs.dll` exports that cross-DLL symbol.
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
// no-op (no logger installed). `er-effects-rs` installs its telemetry sink in DllMain BEFORE any hook
// is registered, so every line the old in-product union code emitted is still emitted, to the same
// log. Crates that only use the raw `MH_*` externs (er-reload-trace, er-input-harness) never
// touch the union and never install a logger; the seam stays inert for them.
// ============================================================================
/// Signature of a logging sink: the union/registry code hands it `format_args!` output.
pub type HookLogFn = fn(std::fmt::Arguments<'_>);
static HOOK_LOGGER: AtomicUsize = AtomicUsize::new(0);

/// Install the sink for union/registry log lines. Call once, early (before any hook registration) to
/// preserve the exact logging the in-product `mh.rs` union produced.
pub fn set_hook_logger(logger: HookLogFn) {
    HOOK_LOGGER.store(logger as usize, Ordering::Release);
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
// That is measured, not hypothetical. `er-effects-rs` and `er-armament-icons` both detour
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
/// (`crates/er-effects-rs/src/mh.rs`): `(target, handler, *mut orig_slot) -> 0 ok | -1 null slot |
/// positive `MH_STATUS` on MinHook failure`.
pub type UnionRegisterFn = unsafe extern "system" fn(usize, UnionFn, *mut usize) -> i32;

/// Which MinHook instance a [`register_shared_hook`] call ended up on. Worth logging: it is the
/// difference between "chained onto the product's detour" and "installed a second instance that
/// may be about to lose a trampoline race".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HookRoute {
    /// Chained into `er_effects_rs.dll`'s single union -- the product is co-loaded.
    ProductUnion,
    /// This DLL's own union -- the product is absent, or this IS the product.
    LocalUnion,
}

/// The product DLL as me3 loads it, matched by base name rather than by path.
#[cfg(windows)]
const PRODUCT_DLL_NAME: &[u8] = b"er_effects_rs.dll\0";
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
    unsafe {
        register_shared_hook_with_budget(
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
    unsafe { register_union_hook(target, handler, orig_slot) }.map(|()| HookRoute::LocalUnion)
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
// RAW CODE-PATCH PRIMITIVES (moved from `er-effects-rs/src/experiments/mem.rs`,
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
