//! Putting the panel on screen, through whatever imgui already exists in the process.
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

use crate::log::bars_log;
use crate::model::{self, Row};

/// A panel older than this is not drawn: the game task stopped publishing (a load, a pause), and
/// the character it describes may be gone.
const MAX_SNAPSHOT_AGE_MS: u64 = 500;

/// Panel sizes at 1080p; everything is multiplied by the frame's UI scale.
const PANEL_WIDTH: f32 = 380.0;
const PANEL_TOP_FRACTION: f32 = 0.09;
const PADDING: f32 = 8.0;
const LABEL_WIDTH: f32 = 96.0;
const ROW_GAP: f32 = 4.0;
const BAR_ROUNDING: f32 = 2.0;
const PANEL_BACKGROUND: [f32; 4] = [0.0, 0.0, 0.0, 0.55];
const BAR_TRACK: [f32; 4] = [0.15, 0.15, 0.15, 0.85];
const TEXT_COLOR: [f32; 4] = [0.95, 0.93, 0.88, 1.0];
const ACTIVE_OUTLINE: [f32; 4] = [1.0, 1.0, 1.0, 0.9];

/// What the game task most recently published.
struct Snapshot {
    header: String,
    rows: Vec<Row>,
    published_ms: u64,
}

static SNAPSHOT: Mutex<Option<Snapshot>> = Mutex::new(None);
static EPOCH: Mutex<Option<Instant>> = Mutex::new(None);

/// Frames this module has drawn a panel into.
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

/// Publish this frame's panel. Called from the game thread.
pub(crate) fn publish(header: String, rows: Vec<Row>) {
    let published_ms = now_ms();
    if let Ok(mut slot) = SNAPSHOT.lock() {
        *slot = Some(Snapshot {
            header,
            rows,
            published_ms,
        });
    }
}

/// Hide the panel. Called when nothing is locked on.
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
fn draw(ui: &Ui, scale: f32) {
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
    let [screen_width, screen_height] = ui.io().display_size;
    let font = ui.current_font_size();
    let row_height = (font + 6.0).max(16.0 * scale);
    let width = PANEL_WIDTH * scale;
    let padding = PADDING * scale;
    let gap = ROW_GAP * scale;
    let label_width = LABEL_WIDTH * scale;
    let rows = snapshot.rows.len() as f32;
    let height = padding * 2.0 + font + gap + rows * (row_height + gap);
    let left = ((screen_width - width) / 2.0).max(0.0);
    let top = screen_height * PANEL_TOP_FRACTION;

    // The foreground list, so the panel sits above the game and above any imgui window another
    // overlay in this process draws.
    let draw_list = ui.get_foreground_draw_list();
    draw_list
        .add_rect([left, top], [left + width, top + height], PANEL_BACKGROUND)
        .filled(true)
        .rounding(4.0 * scale)
        .build();
    draw_list.add_text(
        [left + padding, top + padding],
        TEXT_COLOR,
        &snapshot.header,
    );

    let bar_left = left + padding + label_width;
    let bar_right = left + width - padding;
    let mut y = top + padding + font + gap;
    for row in &snapshot.rows {
        let text_y = y + (row_height - font) / 2.0;
        draw_list.add_text([left + padding, text_y], TEXT_COLOR, row.label);
        draw_list
            .add_rect([bar_left, y], [bar_right, y + row_height], BAR_TRACK)
            .filled(true)
            .rounding(BAR_ROUNDING * scale)
            .build();
        let filled = bar_left + (bar_right - bar_left) * row.fraction.clamp(0.0, 1.0);
        if filled > bar_left {
            draw_list
                .add_rect([bar_left, y], [filled, y + row_height], row.color)
                .filled(true)
                .rounding(BAR_ROUNDING * scale)
                .build();
        }
        if row.active {
            draw_list
                .add_rect([bar_left, y], [bar_right, y + row_height], ACTIVE_OUTLINE)
                .rounding(BAR_ROUNDING * scale)
                .thickness(2.0 * scale)
                .build();
        }
        let text_width = ui.calc_text_size(&row.text)[0];
        draw_list.add_text(
            [bar_right - padding - text_width, text_y],
            TEXT_COLOR,
            &row.text,
        );
        y += row_height + gap;
    }
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        bars_log(format_args!(
            "overlay: first panel drawn, {} row(s), display {screen_width}x{screen_height}, \
             scale {scale:.2}",
            snapshot.rows.len()
        ));
    }
}

/// The guest entry point: adopt the host's imgui and draw.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // SAFETY: `frame` is the host's live pointer; read before adopting so a null is a no-op.
    let host_scale = if frame.is_null() {
        0.0
    } else {
        unsafe { (*frame).ui_scale }
    };
    // Adopt the host's context and allocators before touching `ui`: imgui's current context is a
    // per-DLL global, so this module's copy is null until this runs.
    // SAFETY: `frame` is the host's live pointer.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    let scale = if host_scale.is_finite() && host_scale > 0.0 {
        host_scale
    } else {
        model::ui_scale_for(ui.io().display_size[1])
    };
    draw(ui, scale);
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
        let scale = model::ui_scale_for(ui.io().display_size[1]);
        draw(ui, scale);
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
                    "overlay: a module owns the overlay but would not accept a guest -- the panel \
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
