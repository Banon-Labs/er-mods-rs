//! Drawing the extra bars under the game's own HP bar, through whatever imgui already exists in
//! the process.
//!
//! No panel and no HP bar of our own: each frame the bars are placed from the game's HUD data
//! (see [`crate::layout`]), so they follow the game's bar as it moves and disappear whenever the
//! game hides it.
//!
//! This DLL never installs a second `Present` hook. If another module hosts the overlay (the
//! build watermark, in the profile this was written for) it registers as a guest and draws through
//! it; if nobody does, it hosts and dispatches guests itself. Two `Hudhook::apply()` calls in one
//! process double-hook `Present` and the second one silently renders nothing -- the reason
//! `er_build_watermark_core::overlay_host` exists.

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use er_build_watermark_core::overlay_host::{OverlayFrame, adopt_frame, register_with_host};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::layout::{self, Anchor, StageTransform};
use crate::log::bars_log;
use crate::model::{self, Row};

/// A snapshot older than this is not drawn: the game task stopped publishing (a load, a pause),
/// and the character it describes may be gone.
const MAX_SNAPSHOT_AGE_MS: u64 = 500;

const BAR_TRACK: [f32; 4] = [0.05, 0.05, 0.05, 0.75];
const ACTIVE_OUTLINE: [f32; 4] = [1.0, 1.0, 1.0, 0.9];

/// What the game task most recently published.
struct Snapshot {
    anchor: Anchor,
    rows: Vec<Row>,
    published_ms: u64,
}

static SNAPSHOT: Mutex<Option<Snapshot>> = Mutex::new(None);
static EPOCH: Mutex<Option<Instant>> = Mutex::new(None);

/// Frames this module has drawn bars into.
static DRAWS: AtomicUsize = AtomicUsize::new(0);
/// Frames dispatched to this module at all, drawn or not.
static FRAMES: AtomicUsize = AtomicUsize::new(0);
/// Set once the module is hosting or registered as a guest.
static INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// Milliseconds since this module first asked, on one monotonic clock shared by both threads.
pub(crate) fn now_ms() -> u64 {
    let Ok(mut epoch) = EPOCH.lock() else {
        return 0;
    };
    let start = *epoch.get_or_insert_with(Instant::now);
    u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX)
}

/// Publish this frame's bars and where the game's bar is. Called from the game thread.
pub(crate) fn publish(anchor: Anchor, rows: Vec<Row>) {
    let published_ms = now_ms();
    if let Ok(mut slot) = SNAPSHOT.lock() {
        *slot = Some(Snapshot {
            anchor,
            rows,
            published_ms,
        });
    }
}

/// Draw nothing. Called when nothing is locked on, or the game shows no bar for the target.
pub(crate) fn clear() {
    if let Ok(mut slot) = SNAPSHOT.lock() {
        *slot = None;
    }
}

pub(crate) fn draws() -> usize {
    DRAWS.load(Ordering::Relaxed)
}

pub(crate) fn frames() -> usize {
    FRAMES.load(Ordering::Relaxed)
}

pub(crate) fn installed() -> bool {
    INSTALLED.load(Ordering::Relaxed) != 0
}

/// Draw the current snapshot onto a live imgui frame.
fn draw(ui: &Ui) {
    FRAMES.fetch_add(1, Ordering::Relaxed);
    let now = now_ms();
    let Ok(slot) = SNAPSHOT.lock() else {
        return;
    };
    let Some(snapshot) = slot.as_ref() else {
        return;
    };
    if !model::is_fresh(snapshot.published_ms, now, MAX_SNAPSHOT_AGE_MS) || snapshot.rows.is_empty()
    {
        return;
    }
    let display = ui.io().display_size;
    let Some(stage) = StageTransform::for_display(display) else {
        return;
    };
    // The background list: under every imgui window, over the game -- the bars belong to the
    // game's HUD, not on top of another overlay's panel.
    let draw_list = ui.get_background_draw_list();
    for (index, row) in snapshot.rows.iter().enumerate() {
        let [left, top, right, bottom] = stage.rect(layout::bar_rect(snapshot.anchor, index));
        draw_list
            .add_rect([left, top], [right, bottom], BAR_TRACK)
            .filled(true)
            .build();
        let filled = left + (right - left) * row.fraction.clamp(0.0, 1.0);
        if filled > left {
            draw_list
                .add_rect([left, top], [filled, bottom], row.color)
                .filled(true)
                .build();
        }
        if row.active {
            draw_list
                .add_rect([left, top], [right, bottom], ACTIVE_OUTLINE)
                .thickness(stage.scale.max(1.0))
                .build();
        }
    }
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        bars_log(format_args!(
            "overlay: first bars drawn, {} row(s) under {:?}, display {}x{}, stage scale {:.3} \
             offset {:?}",
            snapshot.rows.len(),
            snapshot.anchor,
            display[0],
            display[1],
            stage.scale,
            stage.offset
        ));
    }
}

/// The guest entry point: adopt the host's imgui and draw.
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
struct BarsOverlay;

impl ImguiRenderLoop for BarsOverlay {
    fn initialize<'a>(&'a mut self, _ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        bars_log(format_args!("overlay: render loop initialized"));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first and before any early return: this module hosts the only imgui context in
        // the process, so returning early here draws nothing for every other overlay too.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        draw(ui);
        // The watermark never registers a guest; whichever module hosts carries its rows.
        er_build_watermark_core::draw_rows(ui, bars_log);
    }
}

/// Join the process's overlay, hosting it if nobody else does.
pub(crate) fn install(hmodule_raw: usize) {
    if INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    if register_with_host(guest_draw) {
        bars_log(format_args!(
            "overlay: another module hosts the imgui context; registered as a guest (no second \
             Present hook)"
        ));
        return;
    }
    // The claim waits for the game's window before touching the mutex, as every other would-be
    // host does, so losing it here means a host appeared in between: ask again rather than give
    // up on a stale answer.
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            if er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw) {
                bars_log(format_args!(
                    "overlay: another module won the overlay while this one waited for the \
                     window; registered as a guest"
                ));
            } else {
                INSTALLED.store(0, Ordering::SeqCst);
                bars_log(format_args!(
                    "overlay: a module owns the overlay but would not accept a guest -- the bars \
                     cannot be drawn. The host speaks a different overlay ABI than this DLL's \
                     {:#06x}; rebuild the whole profile from one tree.",
                    er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(0, Ordering::SeqCst);
            bars_log(format_args!(
                "overlay: this process never got a sized top-level window, so there is nothing to \
                 draw on and no host to join"
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(BarsOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            bars_log(format_args!(
                "overlay: hudhook dx12 overlay installed (this module hosts the imgui context)"
            ));
        }
        Err(error) => {
            INSTALLED.store(0, Ordering::SeqCst);
            bars_log(format_args!(
                "overlay: hudhook dx12 install failed: {error:?}"
            ));
        }
    }
}
