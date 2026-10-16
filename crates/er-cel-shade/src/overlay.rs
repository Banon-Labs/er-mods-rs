//! Joining the process's one imgui overlay, hosting it only if nothing else does, the way
//! `er-npc-summons` does: two `Hudhook::apply()` calls in one process double-hook `Present` and the
//! second renders nothing, which is why `er_build_watermark_core::overlay_host` exists.

use std::ffi::c_void;
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
    crate::menu::draw(ui);
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
                "overlay: no game window, so the menu cannot be drawn"
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
            log(format_args!("overlay: hudhook install failed: {error:?}"));
        }
    }
}
