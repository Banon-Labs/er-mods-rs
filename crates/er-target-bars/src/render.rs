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

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, frame_texture, register_guest, register_with_host,
};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Context, DrawListMut, TextureId, Ui};
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
/// Frames drawn with the game's bar art rather than flat colour.
static ART_DRAWS: AtomicUsize = AtomicUsize::new(0);
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

pub(crate) fn art_draws() -> usize {
    ART_DRAWS.load(Ordering::Relaxed)
}

/// The game's bar images as this frame's host has them: the base and the value-grey fill.
struct BarImages {
    base: TextureId,
    fill: TextureId,
}

/// Draw `rect` (display pixels) with the part `uv` of `texture`, as it is.
fn image(draw_list: &DrawListMut<'_>, texture: TextureId, rect: [f32; 4], uv: [f32; 4]) {
    draw_list
        .add_image(texture, [rect[0], rect[1]], [rect[2], rect[3]])
        .uv_min([uv[0], uv[1]])
        .uv_max([uv[2], uv[3]])
        .build();
}

/// Draw the current snapshot onto a live imgui frame.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe fn draw(ui: &Ui, frame: *const OverlayFrame) {
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
    // Both images or neither: a copy with the game's fill on a flat base, or the reverse, would
    // read as a bug rather than as the game's bar.
    // SAFETY: the caller's contract on `frame`.
    let images = crate::art::base_and_fill().and_then(|(base, fill)| {
        Some(BarImages {
            base: unsafe { frame_texture(frame, base) }?.0,
            fill: unsafe { frame_texture(frame, fill) }?.0,
        })
    });
    // The background list: under every imgui window, over the game -- the bars belong to the
    // game's HUD, not on top of another overlay's panel.
    let draw_list = ui.get_background_draw_list();
    for (index, row) in snapshot.rows.iter().enumerate() {
        let copy = layout::copy_at(snapshot.anchor, index);
        let base = stage.rect(copy.base);
        let (fill_rect, fill_uv) = copy.fill_at(row.fraction);
        let fill = stage.rect(fill_rect);
        match &images {
            Some(images) => {
                image(&draw_list, images.base, base, copy.base_uv);
                if fill[2] > fill[0] {
                    draw_list
                        .add_image(images.fill, [fill[0], fill[1]], [fill[2], fill[3]])
                        .uv_min([fill_uv[0], fill_uv[1]])
                        .uv_max([fill_uv[2], fill_uv[3]])
                        .col(crate::art::tint(row.color))
                        .build();
                }
            }
            None => {
                draw_list
                    .add_rect([base[0], base[1]], [base[2], base[3]], BAR_TRACK)
                    .filled(true)
                    .build();
                if fill[2] > fill[0] {
                    draw_list
                        .add_rect([fill[0], fill[1]], [fill[2], fill[3]], row.color)
                        .filled(true)
                        .build();
                }
            }
        }
        if row.active {
            draw_list
                .add_rect([base[0], base[1]], [base[2], base[3]], ACTIVE_OUTLINE)
                .thickness(stage.scale.max(1.0))
                .build();
        }
    }
    if images.is_some() && ART_DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        bars_log(format_args!(
            "overlay: first bars drawn with the game's bar art, {} row(s) under {:?}",
            snapshot.rows.len(),
            snapshot.anchor
        ));
    }
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        bars_log(format_args!(
            "overlay: first bars drawn ({}), {} row(s) under {:?}, display {}x{}, stage scale \
             {:.3} offset {:?}",
            if images.is_some() {
                "game art"
            } else {
                "flat, art not uploaded yet"
            },
            snapshot.rows.len(),
            snapshot.anchor,
            display[0],
            display[1],
            stage.scale,
            stage.offset
        ));
    }
}

/// The draw entry point, as a guest of whichever module hosts -- this one included, which
/// registers with itself so its own frames carry the host's texture table too.
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
    // SAFETY: as above.
    unsafe { draw(ui, frame) };
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct BarsOverlay;

impl ImguiRenderLoop for BarsOverlay {
    fn initialize<'a>(&'a mut self, _ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        bars_log(format_args!("overlay: render loop initialized"));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first and before any early return: this module hosts the only imgui context in
        // the process, so returning early here draws nothing for every other overlay too. The
        // bars are among the guests (see `install`), which is how they get the texture table.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
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
            // The bars draw as a guest of their own host: only an `OverlayFrame` carries the
            // uploaded textures, and `dispatch_guests` is what builds one.
            let registered = register_guest(OVERLAY_ABI_TAG, guest_draw);
            bars_log(format_args!(
                "overlay: hudhook dx12 overlay installed (this module hosts the imgui context; \
                 bars registered with it: {registered})"
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
