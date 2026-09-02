//! Drawing the list, and joining whatever imgui already exists in the process.
//!
//! This DLL never installs a second `Present` hook. If another module in this workspace is
//! already hosting the overlay it registers as a GUEST and draws through it; if nobody is, it
//! hosts and dispatches guests itself. Two `Hudhook::apply()` calls in one process double-hook
//! `Present` and the second one silently renders nothing -- measured live on 2026-08-25, and the
//! reason `er_build_watermark_core::overlay_host` exists.
//!
//! The install is LAZY: nothing here runs until the picker is opened for the first time. A
//! session that never presses the picker hotkey ends with this DLL having hooked nothing at all,
//! which is the property the crate's entry in `scripts/me3-dll-conflicts.toml` is about.

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize, Ordering};

use er_build_watermark_core::overlay_host::{OverlayFrame, adopt_frame, register_with_host};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Condition, Context, StyleColor, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::log::possess_log;
use crate::picker::View;
use crate::picker::catalog::LABEL_MAX_CHARS;

/// Frames this module has drawn into. `0` while the picker has been opened means the overlay
/// never reached the swapchain, which is a different problem from an empty list.
static DRAWS: AtomicUsize = AtomicUsize::new(0);
/// Rows painted on the most recent draw.
static LAST_ROWS: AtomicUsize = AtomicUsize::new(0);
/// Set once this module is either hosting or registered as a guest.
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
/// The DLL's own module handle, stashed by [`arm`] so the lazy install has one to hand hudhook.
static HMODULE: AtomicUsize = AtomicUsize::new(0);

/// Where the panel sits, and how wide. First-use only -- the window is movable, so a player who
/// drags it keeps it there.
const PANEL_POSITION: [f32; 2] = [48.0, 96.0];
const PANEL_WIDTH: f32 = 420.0;

/// The stable half of the window label. imgui takes everything after `###` as the window's ID and
/// draws none of it, which is what lets the visible half carry a changing cursor position without
/// making a new window on every keypress.
const PANEL_ID: &str = "###er-npc-possess-picker";

pub(crate) fn draws() -> usize {
    DRAWS.load(Ordering::Relaxed)
}

pub(crate) fn last_rows() -> usize {
    LAST_ROWS.load(Ordering::Relaxed)
}

pub(crate) fn installed() -> bool {
    INSTALLED.load(Ordering::Relaxed) != 0
}

/// Remember the module handle. Called from `DllMain`; installs nothing.
pub(crate) fn arm(hmodule_raw: usize) {
    HMODULE.store(hmodule_raw, Ordering::SeqCst);
}

/// Draw the current view onto a live imgui frame.
fn draw(ui: &Ui) {
    DRAWS.fetch_add(1, Ordering::Relaxed);
    // LOCK-FREE FAST PATH, and this is the common one: once the overlay is installed this runs on
    // every `Present` for the rest of the process, and the list is closed for almost all of them.
    // Taking the picker mutex 60-144 times a second to be told "closed" would contend with the
    // game thread's own per-frame tick for nothing.
    if !crate::picker::is_drawing() {
        LAST_ROWS.store(0, Ordering::Relaxed);
        return;
    }
    let Some(view) = crate::picker::view() else {
        LAST_ROWS.store(0, Ordering::Relaxed);
        return;
    };
    LAST_ROWS.store(view.rows.len(), Ordering::Relaxed);
    // `###` PINS THE WINDOW ID, and it is not decoration. imgui derives a window's identity from
    // its label, and this label carries the cursor position -- so without the suffix every step
    // of the list would be a BRAND NEW window: the panel would snap back to its default place and
    // size on every keypress, and imgui would accumulate a fresh saved state for each of the 408
    // titles. Everything after `###` is the id and is never drawn.
    let title = format!(
        "possess: pick a creature  {}/{}{PANEL_ID}",
        view.position, view.total
    );
    ui.window(title)
        .position(PANEL_POSITION, Condition::FirstUseEver)
        .size([PANEL_WIDTH, 0.0], Condition::Always)
        .collapsible(false)
        .resizable(false)
        .build(|| draw_rows(ui, &view));
}

fn draw_rows(ui: &Ui, view: &View) {
    for row in &view.rows {
        let creature = &row.creature;
        // The id is always shown beside the name. A player who already knows they want `c4500`
        // should not have to know it is called Flying Dragon, and the id is what goes in the
        // config file.
        let line = format!(
            "{}{:<width$} c{:04}  {}",
            if row.selected { "> " } else { "  " },
            creature.clipped_label(),
            creature.chr_id,
            creature.shape(),
            width = LABEL_MAX_CHARS,
        );
        if row.selected {
            let tint = ui.push_style_color(StyleColor::Text, [1.0, 0.85, 0.35, 1.0]);
            ui.text(&line);
            tint.pop();
        } else if creature.is_mute() {
            // Dimmed rather than hidden: becoming one gets you a body that cannot attack, and
            // that is worth seeing in the list rather than after the possession.
            let tint = ui.push_style_color(StyleColor::Text, [0.55, 0.55, 0.55, 1.0]);
            ui.text(&line);
            tint.pop();
        } else {
            ui.text(&line);
        }
    }
    ui.separator();
    match &view.selected {
        // A mute creature in the shipped table has zero moves AND zero denials -- it is a variant
        // that owns a model but declares no animations of its own, so nothing was classified
        // rather than everything being withheld. Saying "0 animations were considered and all
        // withheld" was both self-contradictory and backwards.
        Some(creature) if creature.is_mute() => ui.text(format!(
            "c{:04} has no fireable move -- this variant declares no animations of its own",
            creature.chr_id
        )),
        Some(creature) => ui.text(format!(
            "c{:04}: {} moves, {} withheld  |  light {} heavy {} ranged {} movement {}",
            creature.chr_id,
            creature.moves,
            creature.denials,
            creature.buckets[0],
            creature.buckets[1],
            creature.buckets[2],
            creature.buckets[3],
        )),
        None => ui.text("no creatures -- the shipped moveset table is empty"),
    }
    ui.text("press your POSSESS hotkey to choose, the picker hotkey to close");
}

/// The guest entry point: adopt the host's imgui and draw.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // Adopt the host's context and allocators BEFORE touching `ui`. imgui's current context is a
    // per-DLL global, so this module's copy is null until this runs and `ui.io()` would fault.
    // SAFETY: `frame` is the host's live pointer.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    draw(ui);
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct PickerOverlay;

impl ImguiRenderLoop for PickerOverlay {
    fn initialize<'a>(&'a mut self, _ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        possess_log(format_args!("picker overlay: render loop initialized"));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests FIRST and before any early return: this module hosts the only imgui context in
        // the process, so returning early here draws nothing for every OTHER overlay too.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        draw(ui);
        // The watermark is NOT a guest -- it never registers one, because its loser path assumes
        // whichever module hosts will carry its rows directly. See er-invasion-path's render loop,
        // where omitting this left a whole session with no watermark at all.
        er_build_watermark_core::draw_rows(ui, possess_log);
    }
}

/// Join the process's overlay, hosting it if nobody else does. Idempotent.
///
/// Called the first time the picker opens rather than at load, so a session that never uses the
/// picker never installs anything.
pub(crate) fn install_once() {
    if INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    // ON ITS OWN THREAD, like every other shell here. `install()` below waits for the game's
    // window (bounded, but tens of seconds), takes a named kernel mutex, walks every loaded
    // module calling into their registrars, and may end in `Hudhook::apply()` -- which creates a
    // D3D12 device and suspends every thread in the process to write its detours. Its caller is
    // the recurring `FrameBegin` game task; doing any of that inline would stall the game, and
    // `er-invasion-path`'s `DllMain` says the same thing about the loader lock.
    let _ = std::thread::Builder::new()
        .name("er-npc-possess-overlay".to_owned())
        .spawn(install);
}

fn install() {
    if register_with_host(guest_draw) {
        possess_log(format_args!(
            "picker overlay: another module hosts the imgui context; registered as a GUEST (no \
             second Present hook)"
        ));
        return;
    }
    // The claim below waits for the game's window before touching the mutex, and every other
    // would-be host waits on that same window. The probe above therefore ran before anyone could
    // have designated themselves host, so losing the mutex here means one appeared in between --
    // ask again rather than giving up on a stale answer.
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            if er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw) {
                // INSTALLED stays set: this module is joined to an overlay and must not run the
                // install path again.
                possess_log(format_args!(
                    "picker overlay: another module won the overlay while this one waited for \
                     the window; registered as a GUEST (no second Present hook)"
                ));
            } else {
                INSTALLED.store(0, Ordering::SeqCst);
                possess_log(format_args!(
                    "picker overlay: a module owns the overlay but would not accept a guest -- \
                     the list cannot be drawn. The host speaks a different overlay ABI than this \
                     DLL's {:#06x}; rebuild the whole profile from one tree.",
                    er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(0, Ordering::SeqCst);
            possess_log(format_args!(
                "picker overlay: this process never got a sized top-level window, so there is \
                 nothing to draw the list on and no host to join. Not an ABI problem."
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(
        HMODULE.load(Ordering::SeqCst) as *mut c_void
    );
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(PickerOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            possess_log(format_args!(
                "picker overlay: hudhook dx12 overlay installed (this module HOSTS the imgui \
                 context)"
            ));
        }
        Err(error) => {
            INSTALLED.store(0, Ordering::SeqCst);
            possess_log(format_args!(
                "picker overlay: hudhook dx12 install failed: {error:?}"
            ));
        }
    }
}
