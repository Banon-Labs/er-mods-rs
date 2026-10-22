//! Joining the process's one imgui overlay, hosting it only if nothing else does, the way
//! `er-target-bars` does: two `Hudhook::apply()` calls in one process double-hook `Present` and
//! the second renders nothing, which is why `er_build_watermark_core::overlay_host` exists.
//!
//! The picker is drawn only as a guest, even while this module hosts: the theme's fonts
//! (`er_overlay_theme::frame_fonts`) and the display's scale arrive in the frame the host passes
//! its guests.

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, Ordering};

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, register_guest, register_with_host,
};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::log::summons_log;

static INSTALLED: AtomicBool = AtomicBool::new(false);

unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // SAFETY: `frame` is the host's live pointer for this call.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let ui_scale = unsafe { (*frame).ui_scale };
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { er_overlay_theme::frame_fonts(frame) };
    crate::picker::draw(ui, ui_scale, &fonts);
}

struct SummonsOverlay;

impl ImguiRenderLoop for SummonsOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        // The one default face every host builds, so a guest's fallback text is the same size
        // whichever DLL won the overlay.
        let font_px = er_build_watermark_core::overlay_host::add_host_default_font(ctx);
        summons_log(format_args!(
            "overlay: render loop initialized, default font {font_px}px"
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first: this module hosts the only imgui context, so returning early would draw
        // nothing for every other overlay. The picker is one of the guests.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        er_build_watermark_core::draw_rows(ui, summons_log);
    }
}

/// Join the overlay, hosting it if nobody else does. Called once, off the loader lock.
pub(crate) fn install(hmodule_raw: usize) {
    if INSTALLED.swap(true, Ordering::SeqCst) {
        return;
    }
    if register_with_host(guest_draw) {
        summons_log(format_args!(
            "overlay: registered as a guest of another module's host"
        ));
        return;
    }
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            let joined =
                er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw);
            summons_log(format_args!(
                "overlay: another module won the overlay; registered as its guest: {joined}"
            ));
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(false, Ordering::SeqCst);
            summons_log(format_args!(
                "overlay: no game window, so the duel picker cannot be drawn"
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(SummonsOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            let registered = register_guest(OVERLAY_ABI_TAG, guest_draw);
            summons_log(format_args!(
                "overlay: hosting the imgui context; picker registered: {registered}"
            ));
        }
        Err(error) => {
            INSTALLED.store(false, Ordering::SeqCst);
            summons_log(format_args!("overlay: hudhook install failed: {error:?}"));
        }
    }
}
