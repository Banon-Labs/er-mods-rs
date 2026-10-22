//! One line on whatever overlay the process already has. Never a second one.
//!
//! This DLL is a guest and only a guest. It never calls `Hudhook::apply()`, even when nothing else
//! in the process hosts an overlay -- two `apply()` calls double-hook `Present` and the second one
//! silently renders nothing, which is the failure `er_build_watermark_core::overlay_host` exists
//! to end. A diagnostic sentence is not worth owning the swapchain and putting a richer UI at risk
//! of being the one that loses.
//!
//! The consequence, stated rather than hidden: loaded alone, with no watermark and no path
//! overlay in the profile, this DLL draws nothing at all. The report is in the log either way, and
//! the log is the primary surface.
//!
//! The line is drawn in `er-overlay-theme`'s look: its body face, in its warning color. The faces
//! are handed to the host the first time there is a line to draw, at the frame's scale -- the same
//! request every themed panel makes, so the host hands back the handles it already has when one
//! asked first. Until they are built the line is set in the host's default font, as it always was.

#![cfg(windows)]

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_build_watermark_core::overlay_host::{OverlayFrame, adopt_frame, register_with_host};
use er_overlay_theme::{self as theme, Font, Theme};

use crate::log::conflict_log;

/// The verdict, in a form that fits on one line. Written by the game thread, read inside `Present`.
static LINE: Mutex<String> = Mutex::new(String::new());

/// Set once a host has accepted this module's draw callback.
static REGISTERED: AtomicBool = AtomicBool::new(false);

/// Frames drawn. Zero with `REGISTERED` true means the host never dispatched, which is a different
/// bug from never having registered.
static DRAWS: AtomicUsize = AtomicUsize::new(0);

/// Publish the line the overlay shows. Called whenever the verdict changes.
pub fn publish(line: String) {
    if let Ok(mut slot) = LINE.lock() {
        *slot = line;
    }
}

/// Has a host taken this module's callback?
pub fn registered() -> bool {
    REGISTERED.load(Ordering::Relaxed)
}

/// Frames this module has drawn into.
pub fn draws() -> usize {
    DRAWS.load(Ordering::Relaxed)
}

/// The guest entry point.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // Adopt the host's imgui context and allocators before touching `ui`: imgui's current context
    // is a per-DLL global, so this module's copy is null until this runs and the first `ui.io()`
    // would fault inside `Present`.
    // SAFETY: `frame` is the host's live pointer.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    let Ok(line) = LINE.lock() else {
        return;
    };
    if line.is_empty() {
        return;
    }
    DRAWS.fetch_add(1, Ordering::Relaxed);
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let scale = theme::panel_scale(unsafe { (*frame).ui_scale });
    // Once a session; every later call answers `None` and asks nothing of the host.
    let _ = theme::request_fonts(scale);
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { theme::frame_fonts(frame) };
    let _theme = theme::enter(Theme::new(
        scale,
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    ));
    // The foreground list, so the line sits above the game and above any window another overlay in
    // this process happens to be drawing. Below the watermark's own corner by a couple of rows, in
    // screen pixels as it always was.
    const ORIGIN: [f32; 2] = [12.0, 44.0];
    theme::with_font(ui, Font::Body, || {
        theme::foreground_painter(ui).text(ORIGIN, theme::WARNING_TEXT, line.as_str());
    });
}

/// Offer this module's draw to whichever module hosts the overlay.
///
/// Retried from the game frame until it lands: me3 loads natives in profile order, so the host may
/// not have claimed the context yet when this DLL first asks. Returns whether a host has it.
pub fn try_register() -> bool {
    if REGISTERED.load(Ordering::Relaxed) {
        return true;
    }
    if register_with_host(guest_draw) {
        REGISTERED.store(true, Ordering::Relaxed);
        conflict_log(format_args!(
            "overlay: registered as a GUEST on another module's imgui context"
        ));
        return true;
    }
    false
}

/// Say once, at report time, that nothing in the process hosts an overlay -- so a reader who
/// expected a line on screen learns why there is none instead of assuming the DLL is broken.
pub fn log_absent_host() {
    if !REGISTERED.load(Ordering::Relaxed) {
        conflict_log(format_args!(
            "overlay: no module in this process hosts an imgui overlay, and this DLL will not host \
             one (a second Present hook silently disables the first). The report is in this log \
             only."
        ));
    }
}
