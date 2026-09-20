//! One row table per process, not one per DLL.
//!
//! `ROW_SET`, `ROW_ACTIONS` and `ARM_ONCE` in [`crate::row_cloner`] are plain Rust statics, and a
//! static is per-cdylib: every ME3 native that links this crate gets its own copy. So two DLLs that
//! each carry some of the Quit rows built two row tables, each claiming the same native row indices,
//! and a press resolved against whichever table the routing detour happened to reach. Measured on
//! run br-20260913-135314-4804, with the product and `er_quit_menu.dll` both loaded: the product's
//! table came back `Return to Desktop=#5:Some(Ours(GenerateBuildLink))` -- the game's own Return to
//! Desktop row bound to a cloned row's action.
//!
//! The cure is the one `er-hook` already applies to detours: elect a single owner for the process
//! and have everybody else register through it. `er-hook` used to elect by name, looking up
//! `er_quickload.dll`'s `er_effects_union_register` export, which held only while one DLL was
//! always the product; since 2026-09-19 it elects by export walk instead, on this module's own
//! rule (`er_hook::elect_union_host`) and this module's own walk. Rows still need a second
//! election because a build may carry any subset of them, or none, and a row host is not
//! necessarily a hook hub: the first caller into [`crate::row_cloner::arm`] publishes its own
//! `er_quit_rows_register` export, and every later caller finds that address and hands its rows
//! and flows to the owner rather than arming a second cloner.
//!
//! The owner is found by walking the loader's module list, not through a shared mapping: see
//! [`row_hosts`] for the run where a `Local\` section came back per-caller under Proton. A walk
//! that finds nothing is not fatal -- the caller owns the table locally, which is the single-DLL
//! behaviour this crate had before.

use std::sync::atomic::{AtomicUsize, Ordering};

/// The exported registrar's C-ABI shape.
///
/// `bits` is [`crate::row_cloner::RowSet`] in its published bit form, and `actions` points at a
/// `#[repr(C)]` [`crate::row_cloner::QuitRowActions`] the callee copies before returning -- it never
/// retains the pointer, so the caller may pass a stack value. Returns 0 on success.
pub type QuitRowsRegisterFn =
    unsafe extern "C" fn(bits: usize, actions: *const crate::row_cloner::QuitRowActions) -> u32;

/// The companion export: has this DLL already installed the row cloner?
pub type QuitRowsArmedFn = unsafe extern "C" fn() -> u32;

const REGISTER_EXPORT: &[u8] = b"er_quit_rows_register\0";
const ARMED_EXPORT: &[u8] = b"er_quit_rows_armed\0";

/// Every loaded module that exports the registrar, as `(base, register, armed)`.
///
/// Walked out of the PEB rather than asked of a named kernel object. The first version of this used
/// a `Local\` file mapping to publish the owner, and under Proton that silently produced a separate
/// section per caller: run br-20260913-140957-b22b logged both DLLs claiming the table, at mapped
/// addresses `0x4fca0000` and `0x4f520000`, so each compare-exchange saw a fresh zero. A module walk
/// asks the loader what is actually in the process and cannot be faked by a name that does not bind.
///
/// The walk itself moved to [`er_hook::loaded_module_bases`] on 2026-09-19, when `er-hook` began
/// electing its own hook-union hub by the same rule this module elects a row owner by. One PEB
/// walk, read by both elections, rather than a second copy drifting beside the first.
#[cfg(windows)]
fn row_hosts() -> Vec<(usize, usize, usize)> {
    use er_hook::loaded_module_bases;
    use windows::Win32::Foundation::HMODULE;
    use windows::Win32::System::LibraryLoader::GetProcAddress;
    use windows::core::PCSTR;

    let mut hosts = Vec::new();
    for base in loaded_module_bases() {
        let module = HMODULE(base as *mut std::ffi::c_void);
        let register = unsafe { GetProcAddress(module, PCSTR(REGISTER_EXPORT.as_ptr())) }
            .map(|address| address as usize)
            .unwrap_or(0);
        if register == 0 {
            continue;
        }
        let armed = unsafe { GetProcAddress(module, PCSTR(ARMED_EXPORT.as_ptr())) }
            .map(|address| address as usize)
            .unwrap_or(0);
        hosts.push((base, register, armed));
    }
    hosts.sort_unstable();
    hosts
}

#[cfg(not(windows))]
fn row_hosts() -> Vec<(usize, usize, usize)> {
    // Host builds run the pure row-table tests, which have one table and no second DLL.
    Vec::new()
}

/// Has this DLL installed the cloner? Read by the other row hosts through the export below.
static ARMED: AtomicUsize = AtomicUsize::new(0);

/// Record that this DLL now owns the process's row table.
pub(crate) fn note_armed() {
    ARMED.store(1, Ordering::SeqCst);
}

/// Release the ownership marker after a failed install, so another host may take it.
pub(crate) fn release() {
    ARMED.store(0, Ordering::SeqCst);
}

/// Whether this DLL holds the row table, for the other row hosts to read.
///
/// # Safety
/// Takes no arguments and touches no pointers; `unsafe` only to carry the C ABI.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn er_quit_rows_armed() -> u32 {
    ARMED.load(Ordering::SeqCst) as u32
}

/// Who owns the process's row table: nobody yet, this DLL, or another one.
pub(crate) enum Election {
    /// This DLL just claimed it. Install the cloner and the routing detours.
    Owner,
    /// Another DLL owns it; call this address instead of arming a second table.
    Delegate(QuitRowsRegisterFn),
    /// No shared mapping and no export -- behave as the only DLL in the process.
    Alone,
}

/// Claim the process's row table for this DLL, or find who already holds it.
///
/// Two questions, in order. Has another loaded DLL already armed -- the loader knows, because the
/// answer is an export it can be asked for. If not, the lowest module base wins, which every host
/// computes identically from the same module list, so the decision needs no shared state at all.
///
/// Every outcome is logged. The first attempt at this shipped silent, and run
/// br-20260913-140547-c9db then loaded two row hosts with nothing in either log to say why neither
/// had delegated. An election that cannot be read is not an election.
pub(crate) fn elect() -> Election {
    let module = crate::row_cloner::this_dll_module();
    let hosts = row_hosts();
    let mine = hosts
        .iter()
        .find(|(base, _, _)| *base == module)
        .map(|(_, register, _)| *register)
        .unwrap_or(0);
    if mine == 0 {
        crate::host::append_autoload_debug(format_args!(
            "quit-row-registry: this DLL exports no er_quit_rows_register (module=0x{module:x}, {} row host(s) seen) -- owning the row table locally",
            hosts.len()
        ));
        note_armed();
        return Election::Alone;
    }
    for (base, register, armed) in &hosts {
        if *base == module || *armed == 0 {
            continue;
        }
        let is_armed: QuitRowsArmedFn = unsafe { std::mem::transmute(*armed) };
        if unsafe { is_armed() } != 0 {
            crate::host::append_autoload_debug(format_args!(
                "quit-row-registry: module 0x{base:x} already holds the process row table -- delegating (its registrar=0x{register:x}, ours=0x{mine:x})"
            ));
            let registrar: QuitRowsRegisterFn = unsafe { std::mem::transmute(*register) };
            return Election::Delegate(registrar);
        }
    }
    // Nobody has armed yet. The lowest base wins, and every host that walks this same list reaches
    // the same answer, so a host that loses here will delegate when its own arm runs.
    let winner = hosts.first().map(|(base, _, _)| *base).unwrap_or(module);
    if winner != module {
        let (_, register, _) = hosts[0];
        crate::host::append_autoload_debug(format_args!(
            "quit-row-registry: module 0x{winner:x} has the lower base and will own the row table -- delegating (its registrar=0x{register:x}, ours=0x{mine:x})"
        ));
        let registrar: QuitRowsRegisterFn = unsafe { std::mem::transmute(register) };
        return Election::Delegate(registrar);
    }
    crate::host::append_autoload_debug(format_args!(
        "quit-row-registry: claimed the process row table (module=0x{module:x} registrar=0x{mine:x}, {} row host(s) seen)",
        hosts.len()
    ));
    note_armed();
    Election::Owner
}
