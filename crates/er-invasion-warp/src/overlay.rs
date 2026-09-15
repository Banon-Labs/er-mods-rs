//! The settings panel, and joining whatever imgui already exists in the process.
//!
//! This DLL never installs a second `Present` hook. If another module in this workspace already
//! hosts the overlay it registers as a guest and draws through that; if nobody does, it hosts and
//! dispatches guests itself. Two `Hudhook::apply()` calls in one process double-hook `Present`
//! and the second silently renders nothing.
//!
//! # Which case this DLL is actually in
//!
//! A guest, in every profile a player would use: `release-invasion-warp.me3` loads
//! `er_build_watermark.dll` at index 8 and `er_invasion_path.dll` at 9 against this one at 10, so
//! the watermark wins the mutex. That is why the panel takes its clicks through
//! `er-dinput-suppress-core` rather than through `MessageFilter`, which is a method on the
//! `ImguiRenderLoop` trait and therefore host-only. Reading the mouse needs no host status at
//! all -- a guest draws inside the host's single imgui context, so imgui's own per-window
//! hit-testing answers `is_mouse_clicked` normally. Only keeping that click off the game needs
//! the DirectInput half.
//!
//! # Which thread owns what
//!
//! The game task owns the config and every mutation; this module owns nothing. Each tick the task
//! publishes an owned [`SettingsView`] and the renderer clones it, so a frame that does not run
//! the tick leaves the last snapshot on screen rather than tearing. Edits travel the other way as
//! [`SettingEdit`] intents, drained by the next tick and applied through the same
//! reload-clone-mutate-save sequence a key press already uses -- which keeps `fs::write` off the
//! render thread, where hudhook runs inside `Present`.

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_build_watermark_core::overlay_host::{OverlayFrame, adopt_frame, register_with_host};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Context, MouseButton, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::standalone_log;

/// What the panel shows. Published by the game task, cloned by the renderer, owned by neither for
/// longer than a frame.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SettingsView {
    /// One row per config key, already formatted. Formatting on the game thread rather than in
    /// `Present` keeps the renderer free of anything that could take the config lock.
    pub(crate) rows: Vec<SettingRow>,
}

/// A single row: what it is called, what it currently reads, and whether the panel may change it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct SettingRow {
    pub(crate) key: &'static str,
    pub(crate) value: String,
    pub(crate) control: RowControl,
    /// Shown under the row when it cannot be edited, so a row that does nothing says why instead
    /// of looking broken.
    pub(crate) note: Option<&'static str>,
}

/// How a row responds to a click.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RowControl {
    /// A `bool`: clicking flips it.
    Toggle(bool),
    /// An enum with a fixed set of values: clicking advances to the next one.
    Cycle,
    /// Read-only in the panel -- a key binding, a list, or a setting the mod does not implement.
    ReadOnly,
}

/// A change the panel wants made. Applied on the game thread, never here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SettingEdit {
    /// Flip the boolean named by this key.
    Toggle(&'static str),
    /// Advance the row this key names to its next value.
    ///
    /// Keyed rather than one variant per setting: `mode` was the only cycling row when this was
    /// written, and hard-coding it meant the next one -- `prefilter_radius`, which steps 0..3 --
    /// could not be added to the panel at all without touching the renderer.
    Cycle(&'static str),
}

/// The rows the game task most recently published. Replaced whole, never edited in place.
static VIEW: Mutex<Option<SettingsView>> = Mutex::new(None);

/// Edits the panel has recorded and the game task has not yet drained.
static PENDING: Mutex<Vec<SettingEdit>> = Mutex::new(Vec::new());

/// Is the panel on screen? An `AtomicBool` read rather than the view mutex, because the closed
/// case is almost every frame and taking a lock 144 times a second to be told "closed" would
/// contend with the game thread's own tick for nothing.
static OPEN: AtomicBool = AtomicBool::new(false);

/// Frames this module has drawn into. `0` while the panel is open means the overlay never reached
/// the swapchain, which is a different problem from an empty view.
static DRAWS: AtomicUsize = AtomicUsize::new(0);

/// Set once this module is either hosting or registered as a guest.
static INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// Publish the rows the panel should show. Called from the game task, once per tick.
pub(crate) fn publish(view: SettingsView) {
    if let Ok(mut slot) = VIEW.lock() {
        *slot = Some(view);
    }
}

/// Take every edit the panel has recorded since the last call. Called from the game task, which
/// applies them to one cloned config and saves once -- so six clicks in one tick cost one write,
/// not six.
pub(crate) fn drain_edits() -> Vec<SettingEdit> {
    match PENDING.lock() {
        Ok(mut pending) => std::mem::take(&mut *pending),
        Err(poisoned) => std::mem::take(&mut *poisoned.into_inner()),
    }
}

/// Open or close the panel.
pub(crate) fn set_open(open: bool) {
    OPEN.store(open, Ordering::Relaxed);
    if !open {
        // A panel that is not drawn owns no pointer and may not go on swallowing clicks. Cleared
        // from the game thread as well as the render thread, so a stalled render loop cannot
        // leave the left mouse button blanked for good.
        er_dinput_suppress_core::set_pointer_over_overlay(false);
    }
}

/// Is the panel on screen?
#[must_use]
pub(crate) fn is_open() -> bool {
    OPEN.load(Ordering::Relaxed)
}

/// Frames drawn with the panel open.
#[must_use]
pub(crate) fn draws() -> usize {
    DRAWS.load(Ordering::Relaxed)
}

/// Record an edit for the game task to apply.
fn record(edit: SettingEdit) {
    let mut pending = match PENDING.lock() {
        Ok(pending) => pending,
        Err(poisoned) => poisoned.into_inner(),
    };
    // A click that repeats the pending edit for the same key would toggle it twice in one tick
    // and land back where it started, which reads on screen as the click doing nothing.
    if !pending.contains(&edit) {
        pending.push(edit);
    }
}

/// Draw the panel. The only drawing path, taken identically whether this module hosts the imgui
/// context or draws as a guest, so the two cannot drift.
/// How much bigger than imgui's default the panel's text is drawn.
///
/// The game runs at whatever resolution the player set and imgui's default face is sized for a
/// desktop window, so at 4K the unscaled panel is unreadable from a sofa.
const FONT_SCALE: f32 = 1.5;

/// The header, one string per rendered line.
///
/// Split by hand rather than left to imgui's wrapping, because the window is sized to fit these
/// lines: a wrapped header means the width calculation below was wrong, which is visible and
/// ugly. Keep each line short enough to stay a line.
const HEADER: [&str; 2] = [
    "Changes are written to er-invasion-warp.toml, beside the DLL.",
    "That file is rebuilt from the template, so comments you add to it are not kept.",
];

/// Padding either side of the widest line, in unscaled pixels. Covers the window border, the
/// scrollbar gutter and imgui's own frame padding with room to spare.
const WINDOW_PADDING_PX: f32 = 48.0;

/// Gap between the panel's bottom edge and the bottom of the screen.
const BOTTOM_MARGIN_PX: f32 = 64.0;

/// Draw the panel. The only drawing path, taken identically whether this module hosts the imgui
/// context or draws as a guest, so the two cannot drift.
fn draw(ui: &Ui) {
    if !OPEN.load(Ordering::Relaxed) {
        er_dinput_suppress_core::set_pointer_over_overlay(false);
        return;
    }
    DRAWS.fetch_add(1, Ordering::Relaxed);
    let Some(view) = VIEW.lock().ok().and_then(|slot| slot.clone()) else {
        return;
    };

    // Wide enough for the longest line it will actually draw. `calc_text_size` measures at the
    // global font scale, and the window scales its own contents on top of that, so the product of
    // the two is the width the text really takes.
    let widest = HEADER
        .iter()
        .map(|line| ui.calc_text_size(line)[0])
        .fold(0.0_f32, f32::max);
    let width = widest * FONT_SCALE + WINDOW_PADDING_PX;
    let display = ui.io().display_size;

    let mut pointer_owned = false;
    ui.window("er-invasion-warp settings")
        // Bottom centre, re-applied every time the panel opens rather than only the first time:
        // a player who dragged it somewhere and closed it expects the key to put it back where
        // the key always puts it.
        .position(
            [display[0] * 0.5, display[1] - BOTTOM_MARGIN_PX],
            hudhook::imgui::Condition::Appearing,
        )
        .position_pivot([0.5, 1.0])
        .size([width, 0.0], hudhook::imgui::Condition::Appearing)
        .build(|| {
            ui.set_window_font_scale(FONT_SCALE);
            for line in HEADER {
                ui.text_disabled(line);
            }
            ui.separator();
            for row in &view.rows {
                let label = format!("{} = {}", row.key, row.value);
                match row.control {
                    RowControl::Toggle(_) => {
                        if ui.button(&label) {
                            record(SettingEdit::Toggle(row.key));
                        }
                    }
                    RowControl::Cycle => {
                        if ui.button(&label) {
                            record(SettingEdit::Cycle(row.key));
                        }
                    }
                    RowControl::ReadOnly => ui.text_disabled(&label),
                }
                if let Some(note) = row.note {
                    ui.same_line();
                    ui.text_disabled(note);
                }
            }
            // Whether the pointer is inside this window, asked of imgui rather than of a rect this
            // module tracks itself: imgui already owns the hit test, and a hand-kept rectangle is
            // one more thing to be wrong when the window is dragged or resized.
            pointer_owned = ui.is_window_hovered_with_flags(
                hudhook::imgui::WindowHoveredFlags::ROOT_AND_CHILD_WINDOWS,
            );
        });

    // `want_capture_mouse` is the question actually being asked -- "is imgui using the mouse right
    // now" -- and it is the flag imgui maintains for precisely this decision. The hover test above
    // answers a narrower one and goes false the moment an item inside the window becomes active,
    // which is exactly when a click is happening. Measured 2026-09-15: the panel drew 1652 frames
    // and counted zero clicks while its own buttons were firing, so every press both worked the
    // row and swung the sword. Keeping the hover as well costs nothing and covers the frame after
    // a release, when capture has already been handed back.
    let pointer_owned = pointer_owned || ui.io().want_capture_mouse;

    // The click still reaches imgui -- hudhook feeds that from the window procedure. This keeps
    // the swing it would otherwise also trigger out of the game, and needs no host status.
    er_dinput_suppress_core::set_pointer_over_overlay(pointer_owned);
    if pointer_owned && ui.is_mouse_clicked(MouseButton::Left) {
        CLICKS.fetch_add(1, Ordering::Relaxed);
    }
}

/// Clicks taken by the panel. A live count is what separates "the panel drew" from "the panel is
/// reachable", which are different questions and used to have the same answer.
static CLICKS: AtomicUsize = AtomicUsize::new(0);

/// Clicks the panel has taken.
#[must_use]
pub(crate) fn clicks() -> usize {
    CLICKS.load(Ordering::Relaxed)
}

/// The guest entry point: adopt the host's imgui and draw.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // Adopt the host's context and allocators before touching `ui`. imgui's current context is a
    // per-DLL global, so this module's copy is null until this runs and `ui.io()` would fault.
    // SAFETY: `frame` is the host's live pointer.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    draw(ui);
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct SettingsOverlay;

impl ImguiRenderLoop for SettingsOverlay {
    fn initialize<'a>(&'a mut self, _ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        standalone_log(format_args!("overlay: render loop initialized"));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first and before any early return: this module would host the only imgui context
        // in the process, so returning early here draws nothing for every other overlay too.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        draw(ui);
        er_build_watermark_core::draw_rows(ui, standalone_log);
    }
}

/// This DLL's module base, recorded in `DllMain` because that is the only place it is handed to
/// us, and consumed later on the installer thread.
static MODULE: AtomicUsize = AtomicUsize::new(0);

/// Record the module base. Safe to call under the loader lock -- it is one atomic store.
pub(crate) fn remember_module(hmodule_raw: usize) {
    MODULE.store(hmodule_raw, Ordering::SeqCst);
}

/// Join the process's overlay from the installer thread.
///
/// Never call this from `DllMain`. hudhook's install takes locks and enumerates modules, and the
/// guest probe resolves an export out of whichever module hosts; under the loader lock that is a
/// deadlock, and the shape it takes is the worst one to diagnose -- the process stays alive and
/// busy and no window ever appears.
pub(crate) fn install_from_installer_thread() {
    install(MODULE.load(Ordering::SeqCst));
}

/// Join the process's overlay, hosting it if nobody else does.
fn install(hmodule_raw: usize) {
    if INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    if register_with_host(guest_draw) {
        standalone_log(format_args!(
            "overlay: another module hosts the imgui context; registered as a GUEST (no second \
             Present hook)"
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
                standalone_log(format_args!(
                    "overlay: another module won the overlay while this one waited for the \
                     window; registered as a GUEST (no second Present hook)"
                ));
            } else {
                INSTALLED.store(0, Ordering::SeqCst);
                standalone_log(format_args!(
                    "overlay: a module owns the overlay but would not accept a guest -- the \
                     settings panel cannot be drawn. The host speaks a different overlay ABI \
                     than this DLL's {:#06x}; rebuild the whole profile from one tree.",
                    er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(0, Ordering::SeqCst);
            standalone_log(format_args!(
                "overlay: this process never got a sized top-level window, so there is nothing \
                 to draw a panel on and no host to join. Not an ABI problem."
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(SettingsOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            standalone_log(format_args!(
                "overlay: hudhook dx12 overlay installed (this module HOSTS the imgui context)"
            ));
        }
        Err(error) => {
            INSTALLED.store(0, Ordering::SeqCst);
            standalone_log(format_args!(
                "overlay: hudhook dx12 install failed: {error:?}"
            ));
        }
    }
}
