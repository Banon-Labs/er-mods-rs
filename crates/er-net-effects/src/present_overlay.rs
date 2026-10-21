//! The "ER NET EFFECTS" bar, drawn in the shared overlay theme (`er-overlay-theme`) on the
//! foreground draw list of the process's one imgui overlay.
//!
//! The bar is always drawn as a guest: of another module's host, or of this module's own when it
//! hosts, so every frame it draws in carries the theme's fonts out of the host's atlas
//! (`er_overlay_theme::frame_fonts`). Until those are built it draws in the host's default font.

use std::{
    ffi::c_void,
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
};

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, register_guest,
};
use er_overlay_theme::{self as theme, Font, FrameFonts, Painter, Theme};
use hudhook::{
    ImguiRenderLoop, RenderContext,
    hooks::dx12::ImguiDx12Hooks,
    imgui::{Context, Ui},
};

use crate::{
    crash_telemetry,
    effects::effect_selector_text,
    input_suppression,
    log::net_effects_log,
    overlay_layout::{PANEL_ROUNDING, Rect, panel_layout},
};

static HUDHOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);
static HUDHOOK_RENDER_HITS: AtomicUsize = AtomicUsize::new(0);
static HUDHOOK_VISIBLE_HITS: AtomicUsize = AtomicUsize::new(0);
static OVERLAY_COLLAPSED: AtomicBool = AtomicBool::new(START_COLLAPSED);
static OVERLAY_TOGGLE_KEYS: AtomicUsize = AtomicUsize::new(0);

/// A shown bar is the full effect list (user request 2026-10-21: no click to expand). Whether it
/// is drawn at all is the show/hide key's job; only the driver's `collapse` command minimizes it.
const START_COLLAPSED: bool = false;

const OVERLAY_TITLE: &str = "ER NET EFFECTS";

/// The title: the theme's title face.
const TITLE_FONT: Font = Font::Strong;
/// The effect lines under the title: the theme's body text, 16.9 pixels high at 1080p. The bar
/// drew its own default face at 16.25 before it had the theme -- 25% over Dear ImGui's 13, to be
/// read at a glance while playing -- so the body size keeps it as legible.
const LINE_FONT: Font = Font::Body;

/// Draw the bar into a host's frame: another module's, or this module's own when it hosts.
///
/// # Safety
///
/// `frame` is the pointer the overlay host passes for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // Adopt the host's imgui context and allocators before touching `ui`. Without this the
    // context global in this DLL is null and `ui.io()` faults on the first field read.
    // SAFETY: `frame` is the pointer the host just handed us, live for this call.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let ui_scale = unsafe { (*frame).ui_scale };
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { theme::frame_fonts(frame) };
    draw_bar(ui, ui_scale, &fonts);
}

/// Put the bar on screen, hosting the imgui context or joining whoever already does.
///
/// Both paths draw the identical bar. Which one is taken is decided by load order, and load
/// order is not something this crate gets to choose: me3 loads natives in profile order, so
/// `er_build_watermark.dll` (b) is mapped before `er_net_effects.dll` (n) and would otherwise
/// win the swapchain by alphabet. Before this, losing that race meant the bar was installed,
/// never rendered, and never logged an error -- the user simply found it gone.
pub(crate) fn install_present_overlay_hook(hmodule_raw: usize) {
    if HUDHOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }

    // Try to join an existing host before claiming anything. A guest costs no `Present` hook at
    // all, which is strictly better than a second one.
    if er_build_watermark_core::overlay_host::register_with_host(guest_draw) {
        crash_telemetry::hudhook_apply_ok();
        net_effects_log(format_args!(
            "present-overlay: another module hosts the imgui context; registered as a GUEST \
             (no second Present hook)"
        ));
        return;
    }

    // Nobody hosts one yet -- but the claim below waits for the game's window before it touches
    // the mutex, and every other would-be host is waiting on that same window. So the probe above
    // was taken before anyone could have designated themselves, and losing the mutex here means a
    // host appeared in between. Ask again on that path rather than giving up: a single stale probe
    // is precisely how this bar went missing.
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            if er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw) {
                crash_telemetry::hudhook_apply_ok();
                net_effects_log(format_args!(
                    "present-overlay: another module won the overlay while this one waited for \
                     the window; registered as a GUEST (no second Present hook)"
                ));
            } else {
                crash_telemetry::hudhook_apply_failed();
                net_effects_log(format_args!(
                    "present-overlay: a module owns the overlay but would not accept a guest -- \
                     the bar cannot be drawn. The host speaks a different overlay ABI than this \
                     DLL's {:#06x}; rebuild the whole profile from one tree.",
                    er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            crash_telemetry::hudhook_apply_failed();
            net_effects_log(format_args!(
                "present-overlay: this process never got a sized top-level window, so there is \
                 nothing to draw the bar on and no host to join. Not an ABI problem."
            ));
            return;
        }
    }

    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    let result = hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(NetEffectsOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply();
    match result {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            crash_telemetry::hudhook_apply_ok();
            net_effects_log(format_args!(
                "present-overlay: hudhook dx12 overlay installed (this module HOSTS the imgui \
                 context; the watermark and any other overlay draw through it)"
            ));
            // The bar is a guest of its own host, so it reads the theme's fonts out of the same
            // frame every other guest gets; the render loop has no frame of its own to read them
            // from.
            if !register_guest(OVERLAY_ABI_TAG, guest_draw) {
                net_effects_log(format_args!(
                    "present-overlay: this module hosts the overlay but its own bar was not \
                     accepted as a guest, so the bar is not drawn"
                ));
            }
        }
        Err(error) => {
            HUDHOOK_INSTALLED.store(0, Ordering::SeqCst);
            crash_telemetry::hudhook_apply_failed();
            net_effects_log(format_args!(
                "present-overlay: hudhook dx12 overlay install failed: {error:?}"
            ));
        }
    }
}

/// Is the bar currently minimized to its single button?
pub(crate) fn overlay_collapsed() -> bool {
    OVERLAY_COLLAPSED.load(Ordering::Relaxed)
}

/// How many times the bar was expanded or minimized from the keyboard or a driver command.
///
/// Emitted beside the click count rather than folded into it, because the two answer different
/// questions: a session with clicks proves the pointer was free, and a session with only keys is
/// the normal one. Both being zero while `hudhook_render_count` climbs is the exact signature of
/// a bar nobody could open.
pub(crate) fn overlay_toggle_keys() -> usize {
    OVERLAY_TOGGLE_KEYS.load(Ordering::Relaxed)
}

/// Expand the bar -- the expand key. It no longer minimizes: a shown bar is always the list.
///
/// Lives here rather than in `effects` because `OVERLAY_COLLAPSED` is read by the render thread
/// every frame and is deliberately the one place the collapsed state lives; mirroring it into the
/// game-thread state would lag the render loop by a frame (see `effects::selector_input_state`).
pub(crate) fn expand_by_key() {
    OVERLAY_TOGGLE_KEYS.fetch_add(1, Ordering::Relaxed);
    if OVERLAY_COLLAPSED.swap(false, Ordering::Relaxed) {
        net_effects_log(format_args!("present-overlay: bar maximized by key"));
    }
}

/// Put the bar into an explicit state, for the `er-net-effects-command.txt` driver.
///
/// The command path exists so the expand/collapse machine can be exercised without a pointer and
/// without a keyboard -- the click path had no such handle, which is part of why it went a whole
/// session unproven.
pub(crate) fn set_collapsed_by_command(collapsed: bool) {
    if OVERLAY_COLLAPSED.swap(collapsed, Ordering::Relaxed) == collapsed {
        return;
    }
    OVERLAY_TOGGLE_KEYS.fetch_add(1, Ordering::Relaxed);
    net_effects_log(format_args!(
        "present-overlay: bar {} by driver command",
        if collapsed { "minimized" } else { "maximized" }
    ));
}

/// The host-side render loop. Carries no state of its own: everything the draw needs between
/// frames lives in the statics above, because the guest path has no instance to hold it.
struct NetEffectsOverlay;

impl ImguiRenderLoop for NetEffectsOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render_context: &'a mut dyn RenderContext) {
        // The default face every host gives its atlas, at one size, so the face a guest falls
        // back to does not depend on which module won the overlay. The bar itself draws in the
        // theme's faces once they are built.
        let font_px = er_build_watermark_core::overlay_host::add_host_default_font(ctx);
        net_effects_log(format_args!(
            "present-overlay: default font sized {font_px}px"
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        // This module hosts the context, so every other overlay in the process draws here or
        // nowhere -- the bar among them, registered as a guest of this host. The watermark is
        // drawn after the guests so it stays on top of them.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        er_build_watermark_core::draw_rows(ui, net_effects_log);
    }
}

/// Draw the bar, at `ui_scale` (the frame's display height over 1080) in the theme `fonts` the
/// host has built. The only drawing path, reached through `guest_draw` whether this module hosts
/// the imgui context or is a guest inside another module's render loop -- so the two cannot
/// drift and a bug can never be "only in the guest case".
fn draw_bar(ui: &Ui, ui_scale: f32, fonts: &FrameFonts) {
    // Counted here, not in the host `render()`, because the guest path does not go through it.
    // Keeping the counters on the host path made `hudhook_render_count` read 0 for a bar that was
    // drawing perfectly well as a guest -- an oracle that reports the old bug's exact signature
    // for a working feature is worse than no oracle.
    crash_telemetry::hudhook_render_enter();
    let _guard = RenderExitGuard;
    {
        static INIT: std::sync::Once = std::sync::Once::new();
        INIT.call_once(|| {
            crash_telemetry::hudhook_initialize();
            net_effects_log(format_args!(
                "present-overlay: hudhook render loop initialized"
            ));
        });
    }
    let hits = HUDHOOK_RENDER_HITS.fetch_add(1, Ordering::SeqCst) + 1;
    let display = ui.io().display_size;
    if hits == 1 {
        net_effects_log(format_args!(
            "present-overlay: hudhook first render display={:.0}x{:.0}",
            display[0], display[1]
        ));
    }
    let selector_text = effect_selector_text();
    if selector_text.trim().is_empty() {
        // Nothing on screen owns the pointer, so nothing may swallow a click.
        forget_button();
        return;
    }
    let visible_hits = HUDHOOK_VISIBLE_HITS.fetch_add(1, Ordering::SeqCst) + 1;
    crash_telemetry::hudhook_render_visible();
    if visible_hits == 1 {
        net_effects_log(format_args!(
            "present-overlay: hudhook first visible selector display={:.0}x{:.0} text='{selector_text}'",
            display[0], display[1]
        ));
    }

    let scale = theme::panel_scale(ui_scale);
    // Asked once, the first frame the bar is on screen, by when the game has its display. The
    // host builds them before its next frame; this frame draws in its default font.
    if let Some(handles) = theme::request_fonts(scale) {
        net_effects_log(format_args!(
            "present-overlay: theme fonts handed to the overlay host at scale {scale}: handles \
             {handles:?} (a 0 is a face the host refused; a handle another panel holds is \
             shared)"
        ));
    }
    let _theme = theme::enter(Theme::new(
        scale,
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    ));

    let collapsed = OVERLAY_COLLAPSED.load(Ordering::Relaxed);
    draw_foreground_selector(ui, display, &selector_text, collapsed);
    // Nothing on the bar takes a click, so no click is kept from the game.
    input_suppression::set_pointer_over_overlay(false);
}

/// Closes the render window however `draw_bar` leaves -- including its early return when the
/// selector text is empty, which a plain call at the end of the function would skip.
struct RenderExitGuard;

impl Drop for RenderExitGuard {
    fn drop(&mut self) {
        crash_telemetry::hudhook_render_exit();
    }
}

/// The bar is off screen: it owns no pointer, so it may not swallow a click.
fn forget_button() {
    input_suppression::set_pointer_over_overlay(false);
}

/// Draw the bar: the title, then the effect lines unless the driver collapsed it.
fn draw_foreground_selector(ui: &Ui, display: [f32; 2], selector_text: &str, collapsed: bool) {
    let t = theme::current();
    // The title and the lines share a 13-pixel em, so one row height fits both.
    let row_height = theme::line_height(ui, LINE_FONT).max(theme::line_height(ui, TITLE_FONT));
    let lines = if collapsed {
        Vec::new()
    } else {
        selector_lines(selector_text)
    };
    let header_width = theme::text_width(ui, TITLE_FONT, OVERLAY_TITLE);
    let content_width = lines.iter().fold(header_width, |widest, line| {
        widest.max(theme::text_width(ui, LINE_FONT, line))
    });
    let layout = panel_layout(display, content_width, row_height, lines.len() + 1, t.scale);

    let paint = theme::foreground_painter(ui);
    let radius = t.px(PANEL_ROUNDING);
    let [panel_min, panel_max] = corners(layout.panel);
    paint.fill(panel_min, panel_max, theme::HUD_PANEL_BG, radius);

    draw_shadowed_text(
        ui,
        &paint,
        TITLE_FONT,
        [layout.text_x, layout.first_row_y],
        OVERLAY_TITLE,
    );
    for (index, line) in lines.iter().enumerate() {
        draw_shadowed_text(
            ui,
            &paint,
            LINE_FONT,
            [
                layout.text_x,
                layout.first_row_y + layout.row_advance * (index as f32 + 1.0),
            ],
            line,
        );
    }
}

/// A rectangle's top-left and bottom-right corners, as the theme's painter takes them.
fn corners(rect: Rect) -> [[f32; 2]; 2] {
    [[rect[0], rect[1]], [rect[2], rect[3]]]
}

/// `text` in `which` with its top-left at `at`, over the theme's shadow one rule's width down and
/// to the right, so it reads over a bright world.
fn draw_shadowed_text(ui: &Ui, paint: &Painter<'_>, which: Font, at: [f32; 2], text: &str) {
    let offset = theme::current().stroke();
    theme::with_font(ui, which, || {
        paint.text([at[0] + offset, at[1] + offset], theme::TEXT_SHADOW, text);
        paint.text(at, theme::TEXT, text);
    });
}

fn selector_lines(text: &str) -> Vec<String> {
    let parts = text.split(" | ").collect::<Vec<_>>();
    if parts.len() >= 4 {
        return vec![
            parts[0].to_owned(),
            parts[1].to_owned(),
            parts[2].to_owned(),
            parts[3..].join(" | "),
        ];
    }
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}
