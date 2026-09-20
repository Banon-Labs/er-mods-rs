//! Keeping the profile-renderer table safe to walk, so `05_010_ProfileSelect` can be opened.
//!
//! The window this crate's opener submits renders one character model per save slot, and the
//! native refresh that draws them ([`er_loading_portrait_core::PROFILE_RENDERER_REFRESH_RVA`],
//! 1.17.1 `0x1409ab820`) walks all ten entries of the renderer table and dereferences
//! `table[slot] + 0x754` for every slot whose profile record exists -- with no null check. That is
//! the `er-effects-rs-j3r` access violation.
//!
//! The table is set up by exactly one native site, the `TitleTopDialog` constructor, so an in-world
//! ProfileSelect opened by this crate runs the refresh against whatever the last teardown left
//! behind. Measured 2026-09-11 on a product-less profile: pressing Load Character took
//! `0xc0000005` at `eldenring.exe+0x9ab874` reading `0x754`, with `rdi = *(0x143d71940) = 0`.
//!
//! # What this does about it
//!
//! [`profile_table_guard_body`] is the decision the product has run for months, moved here so a
//! host without the loading-cover pipeline can run it too. On entry to the native refresh it scans
//! all ten slots and then does one of three things:
//!
//! * every slot valid -- latch [`PROFILE_TABLE_WAS_POPULATED`] (proof the engine and `ResMan` are
//!   up) and let the refresh run;
//! * any slot null -- skip the refresh entirely for this call, which is the only way to stop it
//!   dereferencing the null it is about to read.
//!
//! There used to be a third answer between those two: an all-null table re-ran the native table
//! setup [`er_loading_portrait_core::PROFILE_TABLE_BUILDER_RVA`] and rescanned, so the refresh
//! could run against a table this code had built. It is gone, and the measurement that removed it
//! is in [`profile_table_guard_body`]. The short version: that builder cannot make a generation
//! that draws unless `TitleTopDialog` is the one calling it, and calling it destroys the
//! generation that was drawing. Skipping already prevents the access violation on its own, which
//! is all the builder call was ever needed for.
//!
//! # Two installers, deliberately
//!
//! The product installs this on a private `MhHook` and has done since before the hook union
//! existed. This crate installs it through [`er_hook::register_union_hook`], which its own
//! `Cargo.toml` requires of every detour it owns. The two never share a process --
//! `scripts/me3-dll-conflicts.toml` records the pair as a duplicate owner -- so the product's
//! installer is left exactly as it is rather than converted, and the body below is the single copy
//! of the decision both run.

use core::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{game_module_base, game_rva_for_hook, safe_read_usize};
use er_game_base::stack::trace_first_game_caller_rva;
use er_loading_portrait_core::{
    PROFILE_RENDERER_REFRESH_RVA, PROFILE_SELECT_TABLE_DIAG_LAST, PROFILE_SELECT_TABLE_DIAG_ORIG,
    PROFILE_SELECT_TABLE_GUARD_SKIP_COUNT, PROFILE_SELECT_TABLE_GUARD_SKIP_LAST,
    PROFILE_SELECT_TABLE_REPAIR_COUNT, PROFILE_TABLE_ALL_SLOTS_MASK, PROFILE_TABLE_WAS_POPULATED,
    TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA, TITLE_PROFILE_SLOT_COUNT,
    portrait_renderer_table_entry,
};
use er_title_flow::TITLE_OWNER_SCAN_START_ADDRESS;

use crate::host::{append_autoload_debug, append_crash_log};

/// Native profile refreshes entered, for the rate limit on the per-refresh line.
static PROFILE_TABLE_REFRESH_ENTRY_COUNT: AtomicUsize = AtomicUsize::new(0);

/// Two fields that tell a renderer bound to the title's Scaleform components from one that is not,
/// which is the same thing as telling one that can draw a portrait from one that never will.
///
/// Derived 2026-09-19 by diffing a full `0xa30` renderer object that was drawing against the
/// generation a bare call of the native table setup put in its place. A drawing renderer holds `2`
/// at `+0x94` and a handle at `+0xa0`; a built one holds zero in both, and still did forty seconds
/// later. `+0x48`, `+0x60` and `+0x78` separate them too; these two are the narrowest pair.
///
/// The obvious-looking field is the one to avoid. `+0x754` is what the refresh dereferences and
/// what the access violation was about, so it reads like the state of the draw -- it is not. It is
/// a request latch: the refresh sets it and `+0x755`, and the step machine clears them again, so
/// an idle renderer that has already drawn is byte-identical there to one that was never asked.
/// Measured through Frida on a live session, arming all ten and finding both bytes back at zero.
const PROFILE_RENDERER_BOUND_STATE_OFFSET: usize = 0x94;

/// The handle a bound renderer carries; zero on one the title never bound. See
/// [`PROFILE_RENDERER_BOUND_STATE_OFFSET`].
const PROFILE_RENDERER_BOUND_HANDLE_OFFSET: usize = 0xa0;

/// Slot pointers read out of the renderer table, plus the masks describing them.
struct TableScan {
    ptrs: [usize; TITLE_PROFILE_SLOT_COUNT],
    valid_mask: u32,
    null_mask: u32,
}

/// Read all ten renderer slots. A slot counts as valid only when its first word is the renderer
/// vtable -- a non-null pointer to something else is not a renderer and would fault the same way.
///
/// # Safety
///
/// Reads through the fault-safe readers; `base` is the game module base.
unsafe fn scan_table(base: usize) -> TableScan {
    let null = TITLE_OWNER_SCAN_START_ADDRESS;
    let want_vtable = er_game_base::mem::game_data_addr(
        base,
        TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA,
        "TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA",
    );
    let mut ptrs = [0usize; TITLE_PROFILE_SLOT_COUNT];
    let mut valid_mask = 0u32;
    let mut null_mask = 0u32;
    for (slot, entry) in ptrs.iter_mut().enumerate() {
        let renderer = unsafe { safe_read_usize(portrait_renderer_table_entry(base, slot as i32)) }
            .unwrap_or(0);
        *entry = renderer;
        let valid = renderer != 0
            && renderer != null
            && unsafe { safe_read_usize(renderer) }.unwrap_or(0) == want_vtable;
        if valid {
            valid_mask |= 1 << slot;
        } else {
            null_mask |= 1 << slot;
        }
    }
    TableScan {
        ptrs,
        valid_mask,
        null_mask,
    }
}

/// Whether the native refresh may run. `false` means skip it: a slot it is about to read is null.
///
/// Split from the detour so both installers share one decision and neither can drift from it.
///
/// # Safety
///
/// Called at the entry of the native refresh, on the menu thread.
pub unsafe fn profile_table_guard_body() -> bool {
    let Ok(base) = game_module_base() else {
        return true;
    };
    let scan = unsafe { scan_table(base) };
    let TableScan {
        ptrs,
        valid_mask,
        null_mask,
    } = scan;
    // Degraded is any null at all, including all-null. A healthy table is all ten valid, because
    // the native setup allocates all ten unconditionally.
    let degraded = null_mask != 0;
    let caller_rva = trace_first_game_caller_rva();
    // Every entry, healthy ones included, because a healthy refresh used to log nothing at all and
    // that is precisely the frame a portrait question needs.
    //
    // What it prints is the pair from [`PROFILE_RENDERER_BOUND_STATE_OFFSET`], so a reader can see
    // at the refresh's own entry whether the renderers it is about to feed are bound to anything.
    // A line whose slots read `bound=0 handle=0x0` describes ten portraits that will not appear no
    // matter what the refresh does, and that is a different bug from the one this guard is named
    // for -- it is the table having been rebuilt out from under the title.
    let entry = PROFILE_TABLE_REFRESH_ENTRY_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
    if entry <= 20 || entry.is_multiple_of(25) {
        let bound = |slot: usize| {
            unsafe { safe_read_usize(ptrs[slot] + PROFILE_RENDERER_BOUND_STATE_OFFSET) }
                .unwrap_or(0) as u32
        };
        let handle = |slot: usize| {
            unsafe { safe_read_usize(ptrs[slot] + PROFILE_RENDERER_BOUND_HANDLE_OFFSET) }
                .unwrap_or(0)
        };
        append_autoload_debug(format_args!(
            "profileselect-table-refresh #{entry}: caller_rva=0x{caller_rva:x} valid_mask=0x{valid_mask:x} null_mask=0x{null_mask:x} slot0=0x{:x}(bound={} handle=0x{:x}) slot1=0x{:x}(bound={} handle=0x{:x})",
            ptrs[0],
            bound(0),
            handle(0),
            ptrs[1],
            bound(1),
            handle(1)
        ));
    }
    let key = ((caller_rva & 0xffffff) << 20) | ((valid_mask as usize) << 10) | null_mask as usize;
    if degraded && PROFILE_SELECT_TABLE_DIAG_LAST.swap(key, Ordering::SeqCst) != key {
        append_crash_log(format_args!(
            "PROFILESELECT-TABLE-DIAG: degraded profile-renderer table before native builder (er-effects-rs-j3r) caller_rva=0x{caller_rva:x} valid_mask=0x{valid_mask:x} null_mask=0x{null_mask:x} entries=[0x{:x},0x{:x},0x{:x},0x{:x},0x{:x},0x{:x},0x{:x},0x{:x},0x{:x},0x{:x}]",
            ptrs[0],
            ptrs[1],
            ptrs[2],
            ptrs[3],
            ptrs[4],
            ptrs[5],
            ptrs[6],
            ptrs[7],
            ptrs[8],
            ptrs[9]
        ));
    } else if !degraded {
        PROFILE_SELECT_TABLE_DIAG_LAST.store(0, Ordering::SeqCst);
        // A fully valid table at refresh entry is the proof that the engine built renderers
        // successfully, which is what makes the repair below safe to attempt later.
        PROFILE_TABLE_WAS_POPULATED.store(1, Ordering::SeqCst);
    }
    // The repair that used to live here called the native table setup on a fully-empty table. It
    // is gone, and the reason is that it never worked -- it only looked like it did, because the
    // table it left behind passes every check this code can make.
    //
    // Measured 2026-09-19 on a live title screen that was drawing its portraits correctly: one
    // call of that builder (`scripts/frida/one-extra-table-build-blinds-the-portraits.js`)
    // replaced the ten renderers with a generation that was still not drawing forty seconds
    // later. Ten distinct non-null `CSEzOffscreenRend` pointers at `+0xa8`, so nothing failed to
    // allocate; the generation simply never reached the state a drawing one is in. Diffing a full
    // object against the generation it displaced: `+0x94` is `2` on a drawing renderer and `0` on
    // a built one, `+0xa0` holds a handle and is `0`, and so are `+0x48`, `+0x60` and `+0x78`.
    //
    // `TitleTopDialog` is the builder's only caller -- one call site, `0x1409a8444` -- and it
    // calls it inside the run of `SceneObjProxy::assignComponentWithName` binds that attach the
    // title's Scaleform components. Each renderer's offscreen target is constructed from a
    // per-slot name at `DAT_143b39840 + slot * 0x20`, and the builder's teardown hands the
    // previous ten to `CSDelayDeleteMan` instead of deleting them, so a generation built while
    // another still holds those ten names is never bound to anything and can never draw.
    //
    // What the user saw, which is what sent anyone looking: open the in-game Save Game menu once,
    // back out, quit to menu, open Load Game -- ten blank portraits, and re-entering the list did
    // not fix it. Re-issuing the game's own refresh through Frida armed all ten renderers and
    // still drew nothing, which is what ruled out the request and pointed here.
    //
    // Nothing is lost by not building. The skip below is what stops `er-effects-rs-j3r` from
    // dereferencing `[null + 0x754]`, and it stops it on its own; a portrait the builder makes in
    // this state was never going to appear either.
    if null_mask == PROFILE_TABLE_ALL_SLOTS_MASK {
        let n = PROFILE_SELECT_TABLE_REPAIR_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
        if n == 1 {
            append_autoload_debug(format_args!(
                "profileselect-table-refresh: the renderer table is empty; skipping the refresh rather than calling the native setup, which produces a generation that is never bound to the title's Scaleform components and cannot draw (er-effects-rs-j3r)"
            ));
        }
    }
    if null_mask != 0 {
        let n = PROFILE_SELECT_TABLE_GUARD_SKIP_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
        let skip_key = ((caller_rva & 0xffffff) << 10) | null_mask as usize;
        if PROFILE_SELECT_TABLE_GUARD_SKIP_LAST.swap(skip_key, Ordering::SeqCst) != skip_key {
            append_crash_log(format_args!(
                "PROFILESELECT-TABLE-GUARD SKIP #{n}: null/invalid renderer slots remain (null_mask=0x{null_mask:x}) -- skipping the native builder this call so it cannot AV at [null+0x754] (er-effects-rs-j3r)"
            ));
        }
        return false;
    }
    true
}

/// Make the profile-renderer table safe to open `05_010_ProfileSelect` against, building it if it
/// is empty. Returns whether the table is now fit to walk.
///
/// The opener needs this and the detour above cannot provide it: the detour only runs once the
/// native refresh is entered, and the refresh is only entered once the window has been submitted --
/// which is the thing the opener is deciding whether to do. Asking the host to build one does not
/// close the loop either, because the host entry is the loading-cover pipeline and a shell's
/// neutral default does nothing (measured 2026-09-11: four presses, each
/// `build_requested=false`, no picker).
///
/// # There is no readiness gate any more, because nothing is built
///
/// This used to weigh whether the engine was up before calling the native setup --
/// [`PROFILE_TABLE_WAS_POPULATED`] as a proxy, with the local `PlayerIns` as the stronger signal
/// for a product-less in-world load that had never seen a valid table. Both questions were about
/// when the builder is safe to call, and the builder is no longer called from anywhere in this
/// module. What remains is a single question with a single answer: an empty table may be opened
/// against when the refresh detour is installed to skip the refresh, and may not when it is not.
///
/// # Safety
///
/// Menu thread, with the game module base.
pub unsafe fn ensure_profile_table_ready(base: usize) -> bool {
    let scan = unsafe { scan_table(base) };
    if scan.null_mask == 0 {
        return true;
    }
    if scan.null_mask != PROFILE_TABLE_ALL_SLOTS_MASK {
        // Partly populated. The builder tears the existing ten down before constructing fresh ones,
        // so running it here would destroy live renderers the menu is using. The refresh detour
        // handles this case by skipping instead.
        append_autoload_debug(format_args!(
            "profileselect-table-guard: renderer table is partly populated (null_mask=0x{:x}); not rebuilding, because the native setup tears down the slots that are still live",
            scan.null_mask
        ));
        return false;
    }
    // An all-null table is safe to submit against as long as the refresh detour is installed: the
    // detour skips the refresh rather than letting it read `[null + 0x754]`. The skip is what
    // makes the picker openable here, and it always was -- the native setup this function used to
    // call was never what did it.
    //
    // That call is gone for the reason recorded in `profile_table_guard_body` above: a generation
    // the builder makes outside `TitleTopDialog`'s own construction sequence is never bound to the
    // title's Scaleform components, so it cannot draw a portrait, and making one destroys the
    // generation that was drawing. Measured live 2026-09-19 on a title screen that had portraits.
    // Our rows here are files with no character model to draw, so the picker loses nothing it ever
    // had.
    //
    // Read through `PROFILE_SELECT_TABLE_DIAG_ORIG` rather than this crate's `GUARD_INSTALLED`,
    // because the product installs the same detour from its own `MhHook` and never raises that
    // latch. The shared slot is non-zero once either installer has hooked, which is the question
    // being asked.
    if PROFILE_SELECT_TABLE_DIAG_ORIG.load(Ordering::SeqCst) != 0 {
        append_autoload_debug(format_args!(
            "profileselect-table-guard: renderer table is empty and the refresh detour is installed, so the refresh will be skipped rather than faulting; not calling the native setup, which cannot produce renderers that draw"
        ));
        return true;
    }
    append_autoload_debug(format_args!(
        "profileselect-table-guard: renderer table is empty and the refresh detour is not installed, so nothing would stop the native refresh reading [null + 0x754]; refusing to open ProfileSelect (er-effects-rs-j3r)"
    ));
    false
}

/// The detour this crate installs. Same body as the product's, reached through the union.
///
/// # Safety
///
/// Installed by `er-hook`; the game calls it at the entry of the native refresh.
unsafe extern "system" fn profile_table_guard_hook(
    a: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    // A panicking guard must not change what the game does, so a panic chains the original -- the
    // same fail-open the product's copy takes.
    let chain = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        profile_table_guard_body()
    }))
    .unwrap_or(true);
    if !chain {
        return 0;
    }
    let orig = PROFILE_SELECT_TABLE_DIAG_ORIG.load(Ordering::SeqCst);
    if orig == 0 || orig == usize::MAX {
        return 0;
    }
    // Safety: the union slot holds either the game trampoline or the next handler, both callable
    // under the union signature.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    unsafe { next(a, b, c, d) }
}
/// Raised once the guard is on the union, so a second host arming a character row does not add the
/// same handler again.
static GUARD_INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// Install the guard on the native profile-renderer refresh.
///
/// Only a host that does not already own this detour calls this. The product does own it, from its
/// own private `MhHook` installed at attach, and must not call this.
///
/// # Safety
///
/// Process attach or startup-hook context.
pub unsafe fn install_profile_table_guard() -> bool {
    // Once per process. Both character rows want this guard and two hosts can now arm rows in one
    // process, so without the latch the same handler would go onto the union twice and the native
    // refresh would run through it twice per call.
    if GUARD_INSTALLED
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return true;
    }
    let Ok(addr) = game_rva_for_hook(PROFILE_RENDERER_REFRESH_RVA as u32) else {
        GUARD_INSTALLED.store(0, Ordering::SeqCst);
        append_autoload_debug(format_args!(
            "profileselect-table-guard: failed to resolve the native profile refresh rva 0x{PROFILE_RENDERER_REFRESH_RVA:x}; ProfileSelect cannot be opened safely"
        ));
        return false;
    };
    match unsafe {
        er_hook::register_union_hook(
            addr,
            profile_table_guard_hook,
            &PROFILE_SELECT_TABLE_DIAG_ORIG,
        )
    } {
        Ok(()) => {
            append_autoload_debug(format_args!(
                "profileselect-table-guard: registered the native profile refresh 0x{addr:x} on the union; an empty renderer table is rebuilt and a still-degraded one skips the refresh"
            ));
            true
        }
        Err(status) => {
            append_autoload_debug(format_args!(
                "profileselect-table-guard: register_union_hook failed: {status:?}; ProfileSelect cannot be opened safely"
            ));
            false
        }
    }
}
