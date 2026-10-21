//! The settings panel, and joining whatever imgui already exists in the process.
//!
//! This DLL never installs a second `Present` hook. If another module in this workspace already
//! hosts the overlay it registers as a guest and draws through that; if nobody does, it hosts and
//! dispatches guests itself. Two `Hudhook::apply()` calls in one process double-hook `Present`
//! and the second silently renders nothing.
//!
//! # Which case this DLL is actually in
//!
//! A guest, in every profile a player would use: `release-invasion-warp.me3` loads
//! `er_build_watermark.dll` at index 8 and `er_invasion_path.dll` at 9 against this one at 10, so
//! the watermark wins the mutex. That is why the panel takes its clicks through
//! `er-dinput-suppress-core` rather than through `MessageFilter`, which is a method on the
//! `ImguiRenderLoop` trait and therefore host-only. Reading the mouse needs no host status at
//! all -- a guest draws inside the host's single imgui context, so imgui's own per-window
//! hit-testing answers `is_mouse_clicked` normally. Only keeping that click off the game needs
//! the DirectInput half.
//!
//! # Which thread owns what
//!
//! The game task owns the config and every mutation; this module owns nothing. Each tick the task
//! publishes an owned [`SettingsView`] and the renderer clones it, so a frame that does not run
//! the tick leaves the last snapshot on screen rather than tearing. Edits travel the other way as
//! [`SettingEdit`] intents, drained by the next tick and applied through the same
//! reload-clone-mutate-save sequence a key press already uses -- which keeps `fs::write` off the
//! render thread, where hudhook runs inside `Present`.

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};

use er_build_watermark_core::overlay_host::{OverlayFrame, adopt_frame, register_with_host};
use er_overlay_theme::{self as theme, Font, Theme};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Condition, Context, MouseButton, Ui, WindowFlags, WindowHoveredFlags};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::standalone_log;

/// What the panel shows. Published by the game task, cloned by the renderer, owned by neither for
/// longer than a frame.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SettingsView {
    /// One row per config key, already formatted. Formatting on the game thread rather than in
    /// `Present` keeps the renderer free of anything that could take the config lock.
    pub(crate) rows: Vec<SettingRow>,
}

/// A single row: what it is called, what it currently reads, and whether the panel may change it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct SettingRow {
    pub(crate) key: &'static str,
    pub(crate) value: String,
    pub(crate) control: RowControl,
    /// Shown beside the row, so a row that does nothing says why instead of looking broken.
    ///
    /// Owned rather than `&'static str` since the bracket rows describe what is actually picked
    /// (`RL126-150 at +21 and up`), which is not a string that can be written at compile time.
    pub(crate) note: Option<String>,
}

/// How a row responds to a click.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum RowControl {
    /// A `bool`: clicking flips it.
    Toggle(bool),
    /// An enum with a fixed set of values: clicking advances to the next one.
    Cycle,
    /// A list to pick from, with a prefix of it greyed out.
    ///
    /// `first_enabled` is where the selectable entries start, which is how the bracket rows refuse
    /// to offer anything below the player's own without having to know what a bracket is. Greying
    /// rather than omitting, because a list that silently dropped its first five rows would make
    /// the player's own position in it unreadable.
    Choose {
        options: Vec<String>,
        selected: usize,
        first_enabled: usize,
    },
    /// Read-only in the panel -- a key binding, a list, or a setting the mod does not implement.
    ReadOnly,
}

/// A change the panel wants made. Applied on the game thread, never here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SettingEdit {
    /// Flip the boolean named by this key.
    Toggle(&'static str),
    /// Advance the row this key names to its next value.
    ///
    /// Keyed rather than one variant per setting: `mode` was the only cycling row when this was
    /// written, and hard-coding it meant the next one -- `prefilter_radius`, which steps 0..3 --
    /// could not be added to the panel at all without touching the renderer.
    Cycle(&'static str),
    /// Set the row this key names to the entry at this index.
    Choose(&'static str, usize),
}

/// The rows the game task most recently published. Replaced whole, never edited in place.
static VIEW: Mutex<Option<SettingsView>> = Mutex::new(None);

/// Edits the panel has recorded and the game task has not yet drained.
static PENDING: Mutex<Vec<SettingEdit>> = Mutex::new(Vec::new());

/// Is the panel on screen? An `AtomicBool` read rather than the view mutex, because the closed
/// case is almost every frame and taking a lock 144 times a second to be told "closed" would
/// contend with the game thread's own tick for nothing.
static OPEN: AtomicBool = AtomicBool::new(false);

/// Frames this module has drawn into. `0` while the panel is open means the overlay never reached
/// the swapchain, which is a different problem from an empty view.
static DRAWS: AtomicUsize = AtomicUsize::new(0);

/// Set once this module is either hosting or registered as a guest.
static INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// Publish the rows the panel should show. Called from the game task, once per tick.
pub(crate) fn publish(view: SettingsView) {
    if let Ok(mut slot) = VIEW.lock() {
        *slot = Some(view);
    }
}

/// Take every edit the panel has recorded since the last call. Called from the game task, which
/// applies them to one cloned config and saves once -- so six clicks in one tick cost one write,
/// not six.
pub(crate) fn drain_edits() -> Vec<SettingEdit> {
    match PENDING.lock() {
        Ok(mut pending) => std::mem::take(&mut *pending),
        Err(poisoned) => std::mem::take(&mut *poisoned.into_inner()),
    }
}

/// Open or close the panel.
pub(crate) fn set_open(open: bool) {
    OPEN.store(open, Ordering::Relaxed);
    if !open {
        // A panel that is not drawn owns no pointer and may not go on swallowing clicks. Cleared
        // from the game thread as well as the render thread, so a stalled render loop cannot
        // leave the left mouse button blanked for good.
        er_dinput_suppress_core::set_pointer_over_overlay(false);
    }
}

/// Is the panel on screen?
#[must_use]
pub(crate) fn is_open() -> bool {
    OPEN.load(Ordering::Relaxed)
}

/// Frames drawn with the panel open.
#[must_use]
pub(crate) fn draws() -> usize {
    DRAWS.load(Ordering::Relaxed)
}

/// Record an edit for the game task to apply.
fn record(edit: SettingEdit) {
    let mut pending = match PENDING.lock() {
        Ok(pending) => pending,
        Err(poisoned) => poisoned.into_inner(),
    };
    // A click that repeats the pending edit for the same key would toggle it twice in one tick
    // and land back where it started, which reads on screen as the click doing nothing.
    if !pending.contains(&edit) {
        pending.push(edit);
    }
}

/// The panel's title, in its header bar, and its window's name.
const TITLE: &str = "er-invasion-warp settings";

/// The lines under the header, one string per rendered line.
///
/// Split by hand rather than left to imgui's wrapping, because the panel is sized to fit these
/// lines: a wrapped line means the width calculation below was wrong, which is visible and ugly.
/// Keep each line short enough to stay a line.
const HEADER: [&str; 2] = [
    "Changes are written to er-invasion-warp.toml, beside the DLL.",
    "That file is rebuilt from the template, so comments you add to it are not kept.",
];

/// Gap between the panel's bottom edge and the bottom of the screen, at 1080p.
const BOTTOM_MARGIN: f32 = 64.0;

/// The body's height at 1080p before it has been measured once.
const BODY_HEIGHT_GUESS: f32 = 480.0;

/// The body's height last frame, in screen pixels, as `f32` bits; 0 before the first.
static BODY_HEIGHT: AtomicU32 = AtomicU32::new(0);

/// Frames drawn since the panel last opened, counted up to [`ANCHORED_FRAMES`].
static OPEN_FRAMES: AtomicU32 = AtomicU32::new(0);

/// The frames after it opens that the panel is put back at the bottom centre: the first, and the
/// one after it, which is the first drawn at the body's measured height rather than the guess (or
/// the last opening's height).
const ANCHORED_FRAMES: u32 = 2;

/// Draw the panel. The only drawing path, taken identically whether this module hosts the imgui
/// context or draws as a guest, so the two cannot drift. It is drawn in `er-overlay-theme`'s look,
/// as the render-scale panel is: the theme's window and panel styles, its faces, its helpers.
///
/// A guest passes the frame the host handed it, which carries the frame's scale and the panel's
/// fonts in the host's atlas. When this module hosts it draws outside any frame and passes null:
/// the scale is then the display's own, and the text is in the host's default font scaled to the
/// panel's size, because only a frame can name the fonts in the atlas.
///
/// # Safety
///
/// `frame` is null, or the pointer the overlay host just passed to [`guest_draw`], live for the
/// duration of this call.
unsafe fn draw(ui: &Ui, frame: *const OverlayFrame) {
    if !OPEN.load(Ordering::Relaxed) {
        OPEN_FRAMES.store(0, Ordering::Relaxed);
        er_dinput_suppress_core::set_pointer_over_overlay(false);
        return;
    }
    DRAWS.fetch_add(1, Ordering::Relaxed);
    let Some(view) = VIEW.lock().ok().and_then(|slot| slot.clone()) else {
        return;
    };

    let ui_scale = if frame.is_null() {
        er_build_watermark_core::font_request::ui_scale(ui.io().display_size[1])
    } else {
        // SAFETY: the caller's contract: a non-null `frame` is live for this call.
        unsafe { (*frame).ui_scale }
    };
    let scale = theme::panel_scale(ui_scale);
    if !frame.is_null() {
        // Once a session, the first time the panel is seen; every later call asks nothing.
        let _ = theme::request_fonts(scale);
    }
    // SAFETY: the caller's contract; `frame_fonts` takes null as no fonts.
    let fonts = unsafe { theme::frame_fonts(frame) };
    let _theme = theme::enter(Theme::new(
        scale,
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    ));
    let pointer_owned = draw_panel(ui, &view);

    // `want_capture_mouse` is the question actually being asked -- "is imgui using the mouse right
    // now" -- and it is the flag imgui maintains for precisely this decision. The hover test above
    // answers a narrower one and goes false the moment an item inside the window becomes active,
    // which is exactly when a click is happening. Measured 2026-09-15: the panel drew 1652 frames
    // and counted zero clicks while its own buttons were firing, so every press both worked the
    // row and swung the sword. Keeping the hover as well costs nothing and covers the frame after
    // a release, when capture has already been handed back.
    let pointer_owned = pointer_owned || ui.io().want_capture_mouse;

    // The click still reaches imgui -- hudhook feeds that from the window procedure. This keeps
    // the swing it would otherwise also trigger out of the game, and needs no host status.
    er_dinput_suppress_core::set_pointer_over_overlay(pointer_owned);
    if pointer_owned && ui.is_mouse_clicked(MouseButton::Left) {
        CLICKS.fetch_add(1, Ordering::Relaxed);
    }
}

/// The panel's window: the header bar, then the rows in a padded region, outlined in the theme's
/// rule. True while the pointer is over it.
fn draw_panel(ui: &Ui, view: &SettingsView) -> bool {
    let t = theme::current();
    let display = ui.io().display_size;
    // Wide enough for the longest line it will draw unwrapped. Measured before the window begins,
    // so at the font's own size; the window scales its text by the theme's font scale on top of
    // that, so the product is the width the text really takes.
    let lines = HEADER
        .iter()
        .map(|line| theme::text_width(ui, Font::Label, line))
        .fold(0.0_f32, f32::max);
    let title = theme::text_width(ui, Font::Strong, TITLE);
    let width = (lines * t.font_scale + 2.0 * t.px(theme::CONTENT_PAD[0]))
        .max(title * t.font_scale + t.px(theme::HEADER_PAD_LEFT + theme::HEADER_PAD_RIGHT))
        .ceil()
        .min(display[0].max(1.0));
    let header = t.px(theme::HEADER_HEIGHT).round();
    let margin = t.px(BOTTOM_MARGIN).round();
    // No taller than the screen leaves above the margin; the body scrolls past that.
    let room = (display[1] - margin - header - t.stroke()).max(1.0);
    let body = match f32::from_bits(BODY_HEIGHT.load(Ordering::Relaxed)) {
        measured if measured > 0.0 => measured,
        _ => t.px(BODY_HEIGHT_GUESS).round(),
    }
    .min(room);
    // Bottom centre, re-applied every time the panel opens rather than only the first time: a
    // player who dragged it somewhere and closed it expects the key to put it back where the key
    // always puts it. Applied on the frame after as well, when the body first has its measured
    // height, so the bottom edge lands on the margin rather than where the guess put it.
    let shown = OPEN_FRAMES.load(Ordering::Relaxed);
    let anchor = if shown < ANCHORED_FRAMES {
        OPEN_FRAMES.store(shown + 1, Ordering::Relaxed);
        Condition::Always
    } else {
        Condition::Never
    };
    let flags = WindowFlags::NO_TITLE_BAR
        | WindowFlags::NO_COLLAPSE
        | WindowFlags::NO_SAVED_SETTINGS
        | WindowFlags::NO_SCROLLBAR
        | WindowFlags::NO_RESIZE;
    let window_style = theme::window_style(ui);
    let begun = ui
        .window(TITLE)
        .flags(flags)
        .position([display[0] * 0.5, display[1] - margin], anchor)
        .position_pivot([0.5, 1.0])
        .size([width, header + t.stroke() + body], Condition::Always)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return false;
    };
    theme::apply_font_scale(ui);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    let pos = ui.window_pos();
    let size = ui.window_size();
    draw_header(ui, pos, size[0]);
    ui.set_cursor_screen_pos([pos[0], pos[1] + header + t.stroke()]);
    theme::region(
        ui,
        "##invasion-warp-settings-body",
        theme::CONTENT_PAD,
        [0.0, 0.0],
        || {
            draw_body(ui, view);
            let measured = ui.cursor_pos()[1] + t.px(theme::CONTENT_PAD[1]).round();
            BODY_HEIGHT.store(measured.ceil().to_bits(), Ordering::Relaxed);
        },
    );
    theme::painter(ui).outline(pos, [pos[0] + size[0], pos[1] + size[1]], theme::RULE, 0.0);
    // Whether the pointer is inside this window, asked of imgui rather than of a rect this module
    // tracks itself: imgui already owns the hit test, and a hand-kept rectangle is one more thing
    // to be wrong when the window is dragged.
    ui.is_window_hovered_with_flags(WindowHoveredFlags::ROOT_AND_CHILD_WINDOWS)
}

/// The header bar: the title on the left, a rule under it.
fn draw_header(ui: &Ui, pos: [f32; 2], width: f32) {
    let t = theme::current();
    let height = t.px(theme::HEADER_HEIGHT).round();
    let [left, top] = pos;
    let right = left + width;
    let paint = theme::painter(ui);
    paint.with_clip([left, top], [right, top + height + t.stroke()], || {
        paint.fill([left, top], [right, top + height], theme::HEADER_BG, 0.0);
        paint.hline(left, right, top + height, theme::RULE);
    });
    let line = theme::line_height(ui, Font::Strong);
    ui.set_cursor_screen_pos([
        left + t.px(theme::HEADER_PAD_LEFT).round(),
        top + ((height - line) * 0.5).round(),
    ]);
    theme::text(ui, Font::Strong, theme::TEXT, TITLE);
}

/// Inside the padded region: where changes go, a divider, then one row per setting.
fn draw_body(ui: &Ui, view: &SettingsView) {
    for line in HEADER {
        theme::quiet(ui, line);
    }
    theme::divider(ui);
    for row in &view.rows {
        draw_row(ui, row);
    }
}

/// One setting: a checkbox for a boolean, a button that steps a cycling value, a labelled list, or
/// the value as quiet text when the panel cannot change it; and its note, wrapped, under it.
fn draw_row(ui: &Ui, row: &SettingRow) {
    let label = format!("{} = {}", row.key, row.value);
    match &row.control {
        RowControl::Toggle(on) => {
            // The checkbox flips a copy; the game task flips the setting itself when it drains the
            // edit, and the next view shows it.
            let mut value = *on;
            if theme::checkbox(ui, row.key, &mut value) {
                record(SettingEdit::Toggle(row.key));
            }
        }
        RowControl::Cycle => {
            if theme::secondary_button(ui, &label) {
                record(SettingEdit::Cycle(row.key));
            }
        }
        RowControl::Choose {
            options,
            selected,
            first_enabled,
        } => {
            theme::quiet(ui, row.key);
            let preview = options.get(*selected).map_or("", String::as_str);
            let width = ui.content_region_avail()[0];
            if let Some(_open) = theme::combo(ui, &format!("##{}", row.key), preview, width) {
                // The list is a window of its own, a child of the region rather than of the panel,
                // so it takes the panel's font scale itself.
                theme::apply_font_scale(ui);
                for (at, option) in options.iter().enumerate() {
                    // Drawn and refused, not omitted. A player who cannot see the brackets beneath
                    // them cannot see where they are in the list.
                    if at < *first_enabled {
                        ui.text_disabled(option);
                        continue;
                    }
                    if ui
                        .selectable_config(option)
                        .selected(at == *selected)
                        .build()
                    {
                        record(SettingEdit::Choose(row.key, at));
                    }
                }
            }
        }
        RowControl::ReadOnly => theme::text(ui, Font::Body, theme::TEXT_QUIET, &label),
    }
    if let Some(note) = &row.note {
        theme::note(ui, note);
    }
}

/// Clicks taken by the panel. A live count is what separates "the panel drew" from "the panel is
/// reachable", which are different questions and used to have the same answer.
static CLICKS: AtomicUsize = AtomicUsize::new(0);

/// Clicks the panel has taken.
#[must_use]
pub(crate) fn clicks() -> usize {
    CLICKS.load(Ordering::Relaxed)
}

/// The guest entry point: adopt the host's imgui and draw.
///
/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for the duration of this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    // Adopt the host's context and allocators before touching `ui`. imgui's current context is a
    // per-DLL global, so this module's copy is null until this runs and `ui.io()` would fault.
    // SAFETY: `frame` is the host's live pointer.
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    // SAFETY: as above; `adopt_frame` accepted it, so it is the host's live frame.
    unsafe { draw(ui, frame) };
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct SettingsOverlay;

impl ImguiRenderLoop for SettingsOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        // The one default face every host builds, so a guest's fallback text is the same size
        // whichever DLL won the overlay.
        let font_px = er_build_watermark_core::overlay_host::add_host_default_font(ctx);
        standalone_log(format_args!(
            "overlay: render loop initialized, default font {font_px}px"
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first and before any early return: this module would host the only imgui context
        // in the process, so returning early here draws nothing for every other overlay too. The
        // settings panel is one of the guests, registered when this module became the host.
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        er_build_watermark_core::draw_rows(ui, standalone_log);
    }
}

/// This DLL's module base, recorded in `DllMain` because that is the only place it is handed to
/// us, and consumed later on the installer thread.
static MODULE: AtomicUsize = AtomicUsize::new(0);

/// Record the module base. Safe to call under the loader lock -- it is one atomic store.
pub(crate) fn remember_module(hmodule_raw: usize) {
    MODULE.store(hmodule_raw, Ordering::SeqCst);
}

/// Join the process's overlay from the installer thread.
///
/// Never call this from `DllMain`. hudhook's install takes locks and enumerates modules, and the
/// guest probe resolves an export out of whichever module hosts; under the loader lock that is a
/// deadlock, and the shape it takes is the worst one to diagnose -- the process stays alive and
/// busy and no window ever appears.
pub(crate) fn install_from_installer_thread() {
    install(MODULE.load(Ordering::SeqCst));
}

/// Join the process's overlay, hosting it if nobody else does.
fn install(hmodule_raw: usize) {
    if INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    if register_with_host(guest_draw) {
        standalone_log(format_args!(
            "overlay: another module hosts the imgui context; registered as a GUEST (no second \
             Present hook)"
        ));
        return;
    }
    // The claim below waits for the game's window before touching the mutex, and every other
    // would-be host waits on that same window. The probe above therefore ran before anyone could
    // have designated themselves host, so losing the mutex here means one appeared in between --
    // ask again rather than giving up on a stale answer.
    match er_build_watermark_core::claim_overlay_ownership() {
        er_build_watermark_core::OverlayClaim::Won => {}
        er_build_watermark_core::OverlayClaim::LostToAnotherModule => {
            if er_build_watermark_core::overlay_host::register_with_host_retrying(guest_draw) {
                standalone_log(format_args!(
                    "overlay: another module won the overlay while this one waited for the \
                     window; registered as a GUEST (no second Present hook)"
                ));
            } else {
                INSTALLED.store(0, Ordering::SeqCst);
                standalone_log(format_args!(
                    "overlay: a module owns the overlay but would not accept a guest -- the \
                     settings panel cannot be drawn. The host speaks a different overlay ABI \
                     than this DLL's {:#06x}; rebuild the whole profile from one tree.",
                    er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(0, Ordering::SeqCst);
            standalone_log(format_args!(
                "overlay: this process never got a sized top-level window, so there is nothing \
                 to draw a panel on and no host to join. Not an ABI problem."
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(SettingsOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            standalone_log(format_args!(
                "overlay: hudhook dx12 overlay installed (this module HOSTS the imgui context)"
            ));
            // The panel is a guest of its own host, so it reads the theme's fonts out of the same
            // frame every other guest gets; the render loop has no frame of its own to read them
            // from.
            if !er_build_watermark_core::overlay_host::register_guest(
                er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG,
                guest_draw,
            ) {
                standalone_log(format_args!(
                    "overlay: this module hosts the overlay but its own panel was not accepted as \
                     a guest, so the panel is not drawn"
                ));
            }
        }
        Err(error) => {
            INSTALLED.store(0, Ordering::SeqCst);
            standalone_log(format_args!(
                "overlay: hudhook dx12 install failed: {error:?}"
            ));
        }
    }
}
