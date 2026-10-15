//! Joining the process's one imgui overlay, hosting it only if nothing else does, the way
//! `er-npc-summons` does: two `Hudhook::apply()` calls in one process double-hook `Present` and the
//! second renders nothing, which is why `er_build_watermark_core::overlay_host` exists.
//!
//! # A panic in the panel
//!
//! [`guest_draw`] is `extern "C"`, so a panic unwinding out of it would abort the game. The
//! Shaders panel is drawn inside `catch_unwind`: imgui-rs's tokens end its windows as the panic
//! unwinds, and after one the panel is hidden for the session (`menu::hide_for_session`). The
//! per-present staging and publishing run after it in a `catch_unwind` of their own, so the look
//! and the layers keep drawing whatever became of the panel.
//!
//! # Fonts
//!
//! The panel's fonts live in the host's atlas, whichever module hosts, so each frame the guest
//! reads out of the frame the host passed which of them are built (`menu::theme::frame_fonts`)
//! and hands that to the panel. Until they are, the panel draws in the host's default font.

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

unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // SAFETY: `frame` is the host's live pointer for this call.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let ui_scale = unsafe { (*frame).ui_scale };
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { crate::menu::theme::frame_fonts(frame) };
    if catch_unwind(AssertUnwindSafe(|| {
        crate::menu::draw_ui(ui, ui_scale, &fonts);
    }))
    .is_err()
    {
        crate::menu::hide_for_session();
    }
    static END_FAILED: AtomicBool = AtomicBool::new(false);
    if catch_unwind(AssertUnwindSafe(|| crate::menu::end_frame(ui))).is_err()
        && !END_FAILED.swap(true, Ordering::Relaxed)
    {
        log(format_args!(
            "overlay: a panic while staging the frame's parameters; logged once, the panic hook \
             has where"
        ));
    }
}

struct CelShadeOverlay;

impl ImguiRenderLoop for CelShadeOverlay {
    fn initialize<'a>(&'a mut self, _ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        log(format_args!("overlay: render loop initialized"));
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
            log(format_args!(
                "overlay: another module won the overlay; registered as its guest: {joined}"
            ));
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(false, Ordering::SeqCst);
            log(format_args!(
                "overlay: no game window, so nothing runs once a present: no menu, the saved look \
                 cannot change live, and the present count the probe's clock runs on stays 0. \
                 The light bands and outlines still draw with the saved look; layers, the cel \
                 layers among them, do not, because their parameters are published once a \
                 present."
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(CelShadeOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            let registered = register_guest(OVERLAY_ABI_TAG, guest_draw);
            log(format_args!(
                "overlay: hosting the imgui context; menu registered: {registered}"
            ));
        }
        Err(error) => {
            INSTALLED.store(false, Ordering::SeqCst);
            log(format_args!(
                "overlay: hudhook install failed ({error:?}), so nothing runs once a present: no \
                 menu, the saved look cannot change live, and the present count the probe's \
                 clock runs on stays 0. The light bands and outlines still draw with the saved \
                 look; layers, the cel layers among them, do not, because their parameters are \
                 published once a present."
            ));
        }
    }
}
