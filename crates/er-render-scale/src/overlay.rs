//! Joining the process's one imgui overlay, hosting it only if nothing else does, the way
//! er-cel-shade and er-npc-summons do: two `Hudhook::apply()` calls in one process double-hook
//! `Present` and the second renders nothing, which is why `er_build_watermark_core::overlay_host`
//! exists.
//!
//! # A panic in the panel
//!
//! [`guest_draw`] is `extern "C"`, so a panic unwinding out of it would abort the game. The menu is
//! drawn inside `catch_unwind`: imgui-rs's tokens end its windows as the panic unwinds, and after
//! one the panel is hidden for the session (`menu::hide_for_session`). The scale in force is the
//! hook's, which does not run here, so it carries on either way.
//!
//! # Fonts
//!
//! The panel's fonts live in the host's atlas, whichever module hosts, so each frame the guest
//! reads out of the frame the host passed which of them are built (`er_overlay_theme::frame_fonts`).
//! Until they are, the panel draws in the host's default font.

use std::ffi::c_void;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicBool, Ordering};

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, register_guest, register_with_host,
};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::log;

static INSTALLED: AtomicBool = AtomicBool::new(false);
/// A host took the guest draw, or this module hosts.
static JOINED: AtomicBool = AtomicBool::new(false);

/// Has the menu a frame to draw in?
#[must_use]
pub fn joined() -> bool {
    JOINED.load(Ordering::Acquire)
}

unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // SAFETY: `frame` is the host's live pointer for this call.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let ui_scale = unsafe { (*frame).ui_scale };
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { er_overlay_theme::frame_fonts(frame) };
    if catch_unwind(AssertUnwindSafe(|| {
        crate::menu::draw_ui(ui, ui_scale, &fonts);
    }))
    .is_err()
    {
        crate::menu::hide_for_session();
    }
}

struct RenderScaleOverlay;

impl ImguiRenderLoop for RenderScaleOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        // The one default face every host builds, so a guest's fallback text is the same size
        // whichever DLL won the overlay.
        let font_px = er_build_watermark_core::overlay_host::add_host_default_font(ctx);
        log(format_args!(
            "overlay: render loop initialized, default font {font_px}px"
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first: this module hosts the only imgui context, so returning early would draw
        // nothing for every other overlay. The menu is one of the guests.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        er_build_watermark_core::draw_rows(ui, log);
    }
}

/// Join the overlay, hosting it if nobody else does. Called once, off the loader lock: hudhook's
/// install takes locks and enumerates modules, and the guest probe resolves an export out of
/// another DLL.
pub fn install(hmodule_raw: usize) {
    if INSTALLED.swap(true, Ordering::SeqCst) {
        return;
    }
    if register_with_host(guest_draw) {
        JOINED.store(true, Ordering::Release);
        log(format_args!(
            "overlay: registered as a guest of another module's host"
        ));
        return;
    }
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            let joined =
                er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw);
            JOINED.store(joined, Ordering::Release);
            log(format_args!(
                "overlay: another module won the overlay; registered as its guest: {joined}"
            ));
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(false, Ordering::SeqCst);
            log(format_args!(
                "overlay: no game window, so no menu; the saved scale is still applied"
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(RenderScaleOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            let registered = register_guest(OVERLAY_ABI_TAG, guest_draw);
            JOINED.store(registered, Ordering::Release);
            log(format_args!(
                "overlay: hosting the imgui context; menu registered: {registered}"
            ));
        }
        Err(error) => {
            INSTALLED.store(false, Ordering::SeqCst);
            log(format_args!(
                "overlay: hudhook install failed ({error:?}), so no menu; the saved scale is \
                 still applied"
            ));
        }
    }
}
