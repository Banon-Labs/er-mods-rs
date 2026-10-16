//! The popup: a stack of rule-break notices at the top of the screen, through whatever imgui
//! already exists in the process.
//!
//! This DLL never installs a second `Present` hook. If another module hosts the overlay it
//! registers as a guest and draws through it; if nobody does, it hosts and dispatches guests
//! itself (`er_build_watermark_core::overlay_host`).

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, register_guest, register_with_host,
};
use er_duel_rules_core::referee::Alert;
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Condition, Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::log::rules_log;

/// How long one notice stays up.
const NOTICE_MS: u64 = 8_000;
/// Notices shown at once; older ones wait their turn off screen.
const MAX_SHOWN: usize = 4;
/// The notice is authored for a 1080-line display and scaled from there.
const REFERENCE_HEIGHT: f32 = 1080.0;
const FONT_SCALE_AT_1080: f32 = 1.6;
/// Top edge of the stack, as a fraction of the display height: under the boss bar's row of the
/// HUD and clear of the compass.
const TOP_FRACTION: f32 = 0.11;

const ATTENTION: [f32; 4] = [1.0, 0.27, 0.22, 1.0];
const BODY: [f32; 4] = [1.0, 0.96, 0.88, 1.0];
const DETAIL: [f32; 4] = [0.75, 0.72, 0.66, 1.0];

struct Notice {
    alert: Alert,
    shown_ms: u64,
}

static NOTICES: Mutex<Vec<Notice>> = Mutex::new(Vec::new());
static EPOCH: Mutex<Option<Instant>> = Mutex::new(None);
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
static DRAWS: AtomicUsize = AtomicUsize::new(0);

/// Milliseconds since this module first asked, one clock for both threads.
pub(crate) fn now_ms() -> u64 {
    let Ok(mut epoch) = EPOCH.lock() else {
        return 0;
    };
    let start = *epoch.get_or_insert_with(Instant::now);
    u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX)
}

/// `Notice::shown_ms` of a notice still waiting for room on screen.
const NOT_SHOWN: u64 = u64::MAX;

/// Queue a notice. Called from the game thread.
pub(crate) fn push(alert: Alert) {
    if let Ok(mut notices) = NOTICES.lock() {
        notices.push(Notice {
            alert,
            shown_ms: NOT_SHOWN,
        });
    }
}

pub(crate) fn installed() -> bool {
    INSTALLED.load(Ordering::Relaxed) != 0
}

pub(crate) fn draws() -> usize {
    DRAWS.load(Ordering::Relaxed)
}

/// "Attention: Rule break: <player>. <rule>" as one line.
pub(crate) fn headline(alert: &Alert) -> String {
    let who = if alert.is_local {
        format!("{} (you)", alert.player)
    } else {
        alert.player.clone()
    };
    format!("Attention: Rule break: {who}. {}", alert.rule.text())
}

fn draw(ui: &Ui) {
    let now = now_ms();
    let Ok(mut notices) = NOTICES.lock() else {
        return;
    };
    // A notice's clock starts when it first reaches the screen, so a burst is read in turn
    // rather than expiring unseen behind the first four.
    notices.retain(|n| n.shown_ms == NOT_SHOWN || now.saturating_sub(n.shown_ms) < NOTICE_MS);
    for notice in notices.iter_mut().take(MAX_SHOWN) {
        if notice.shown_ms == NOT_SHOWN {
            notice.shown_ms = now;
        }
    }
    if notices.is_empty() {
        return;
    }
    let display = ui.io().display_size;
    let scale = FONT_SCALE_AT_1080 * (display[1] / REFERENCE_HEIGHT).max(0.5);
    ui.window("##er-duel-rules")
        .position(
            [display[0] * 0.5, display[1] * TOP_FRACTION],
            Condition::Always,
        )
        .position_pivot([0.5, 0.0])
        .bg_alpha(0.82)
        .no_decoration()
        .no_inputs()
        .no_nav()
        .focus_on_appearing(false)
        .always_auto_resize(true)
        .build(|| {
            ui.set_window_font_scale(scale);
            for (index, notice) in notices.iter().take(MAX_SHOWN).enumerate() {
                if index > 0 {
                    ui.separator();
                }
                let alert = &notice.alert;
                ui.text_colored(ATTENTION, "Attention: Rule break");
                let who = if alert.is_local {
                    format!("{} (you)", alert.player)
                } else {
                    alert.player.clone()
                };
                ui.text_colored(BODY, format!("{who}. {}", alert.rule.text()));
                if !alert.detail.is_empty() {
                    ui.text_colored(DETAIL, &alert.detail);
                }
            }
        });
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        rules_log(format_args!(
            "overlay: first notice drawn, display {}x{}, font scale {scale:.2}",
            display[0], display[1]
        ));
    }
}

/// The draw entry point, as a guest of whichever module hosts.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // Adopt the host's context and allocators before touching `ui`: imgui's current context is a
    // per-DLL global, so this module's copy is null until this runs.
    // SAFETY: `frame` is the host's live pointer.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    draw(ui);
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct RulesOverlay;

impl ImguiRenderLoop for RulesOverlay {
    fn initialize<'a>(&'a mut self, _ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        rules_log(format_args!("overlay: render loop initialized"));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first and before any early return: this module hosts the only imgui context in
        // the process, so returning early here draws nothing for every other overlay too.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        // The watermark never registers a guest; whichever module hosts carries its rows.
        er_build_watermark_core::draw_rows(ui, rules_log);
    }
}

/// Join the process's overlay, hosting it if nobody else does.
pub(crate) fn install(hmodule_raw: usize) {
    if INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    if register_with_host(guest_draw) {
        rules_log(format_args!(
            "overlay: another module hosts the imgui context; registered as a guest (no second \
             Present hook)"
        ));
        return;
    }
    // The claim waits for the game's window before touching the mutex, as every other would-be
    // host does, so losing it here means a host appeared in between: ask again.
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            if er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw) {
                rules_log(format_args!(
                    "overlay: another module won the overlay while this one waited for the \
                     window; registered as a guest"
                ));
            } else {
                INSTALLED.store(0, Ordering::SeqCst);
                rules_log(format_args!(
                    "overlay: a module owns the overlay but would not accept a guest -- notices \
                     cannot be drawn. The host speaks a different overlay ABI than this DLL's \
                     {:#06x}; rebuild the whole profile from one tree.",
                    OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(0, Ordering::SeqCst);
            rules_log(format_args!(
                "overlay: this process never got a sized top-level window, so there is nothing to \
                 draw on and no host to join"
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(RulesOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            let registered = register_guest(OVERLAY_ABI_TAG, guest_draw);
            rules_log(format_args!(
                "overlay: hudhook dx12 overlay installed (this module hosts the imgui context; \
                 notices registered with it: {registered})"
            ));
        }
        Err(error) => {
            INSTALLED.store(0, Ordering::SeqCst);
            rules_log(format_args!(
                "overlay: hudhook dx12 install failed: {error:?}"
            ));
        }
    }
}
