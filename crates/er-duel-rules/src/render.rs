//! The rule-break list: the newest breaks in the top-right corner, one line each with the time,
//! drawn through whatever imgui already exists in the process. Every break also goes to
//! `er-duel-rules-breaks.txt` (`crate::breaks::record_break`), so what scrolls off is kept.
//!
//! This DLL never installs a second `Present` hook. If another module hosts the overlay it
//! registers as a guest and draws through it; if nobody does, it hosts and dispatches guests
//! itself (`er_build_watermark_core::overlay_host`).

#![cfg(windows)]

use std::ffi::c_void;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, register_guest, register_with_host,
};
use er_duel_rules_core::feed::{Feed, Stamp, player_label};
use er_duel_rules_core::referee::Alert;
use er_overlay_theme::{self as theme, Font, Theme};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Condition, Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::breaks::record_break;
use crate::log::rules_log;

/// Gap between the list and the screen's top and right edges, at 1080p.
const EDGE: f32 = 16.0;
/// Padding inside the list, at 1080p (horizontal, vertical).
const LIST_PAD: [f32; 2] = [12.0, 10.0];

static FEED: Mutex<Feed> = Mutex::new(Feed::new());
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

/// The local wall-clock time, which is what a player reading the list or the file goes by.
fn local_stamp() -> Stamp {
    // SAFETY: `GetLocalTime` only fills and returns a `SYSTEMTIME`.
    let now = unsafe { windows::Win32::System::SystemInformation::GetLocalTime() };
    let byte = |v: u16| u8::try_from(v).unwrap_or(u8::MAX);
    Stamp {
        year: now.wYear,
        month: byte(now.wMonth),
        day: byte(now.wDay),
        hour: byte(now.wHour),
        minute: byte(now.wMinute),
        second: byte(now.wSecond),
    }
}

/// The local Steam name, asked of Steam until it first answers and kept from then on.
fn steam_name(feed: &mut Feed) -> Option<String> {
    if feed.steam_name().is_none()
        && let Some(name) = crate::steam::persona_name()
    {
        rules_log(format_args!(
            "steam: the local player is listed as {name:?}"
        ));
        feed.set_steam_name(Some(name));
    }
    feed.steam_name().map(str::to_owned)
}

/// List a break and append it to the text file. Called from the game thread.
pub(crate) fn push(alert: Alert) {
    let stamp = local_stamp();
    let Ok(mut feed) = FEED.lock() else {
        return;
    };
    steam_name(&mut feed);
    feed.push(stamp, alert);
    if let Some(entry) = feed.shown().next() {
        record_break(&entry.file_line());
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
    let steam = FEED.lock().ok().and_then(|mut feed| steam_name(&mut feed));
    let who = player_label(&alert.player, alert.is_local, steam.as_deref());
    format!("Attention: Rule break: {who}. {}", alert.text)
}

/// Empty the list on screen. The file and the log keep every break.
pub(crate) fn clear() {
    if let Ok(mut feed) = FEED.lock() {
        let steam = feed.steam_name().map(str::to_owned);
        *feed = Feed::new();
        feed.set_steam_name(steam);
    }
    rules_log(format_args!(
        "overlay: rule-break list cleared from the menu"
    ));
}

/// Draw the list in `er-overlay-theme`'s look. A guest passes the host's frame, which names the
/// theme's faces in the host's atlas; when this module hosts it passes null and the text is the
/// host's default face scaled to the theme's size.
///
/// # Safety
///
/// `frame` is null, or the pointer the overlay host just passed to [`guest_draw`], live for the
/// duration of this call.
unsafe fn draw(ui: &Ui, frame: *const OverlayFrame) {
    // Hidden by its key or the menu: the breaks are still filed and logged by `push`.
    if !crate::control::show_breaks() {
        return;
    }
    let Ok(feed) = FEED.lock() else {
        return;
    };
    if feed.is_empty() {
        return;
    }
    let display = ui.io().display_size;
    let ui_scale = if frame.is_null() {
        er_build_watermark_core::font_request::ui_scale(display[1])
    } else {
        // SAFETY: the caller's contract: a non-null `frame` is live for this call.
        unsafe { (*frame).ui_scale }
    };
    let scale = theme::panel_scale(ui_scale);
    if !frame.is_null() {
        // Once a session, the first time a break is listed; every later call asks nothing.
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
    let t = theme::current();
    let edge = t.px(EDGE).round();
    let window_style = theme::window_style(ui);
    let begun = ui
        .window("##er-duel-rules")
        .position([display[0] - edge, edge], Condition::Always)
        .position_pivot([1.0, 0.0])
        .no_decoration()
        .no_inputs()
        .no_nav()
        .focus_on_appearing(false)
        .always_auto_resize(true)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return;
    };
    theme::apply_font_scale(ui);
    let _style = theme::panel_style(ui);
    let pad = [t.px(LIST_PAD[0]).round(), t.px(LIST_PAD[1]).round()];
    let origin = ui.cursor_pos();
    ui.set_cursor_pos([origin[0] + pad[0], origin[1] + pad[1]]);
    {
        let _group = ui.begin_group();
        theme::text(ui, Font::Strong, theme::ERROR_TEXT, "Rule breaks");
        for entry in feed.shown() {
            theme::text(ui, Font::Mono, theme::TEXT_QUIET, &entry.stamp.time());
            ui.same_line();
            theme::text(ui, Font::Body, theme::TEXT, &entry.who());
            ui.same_line();
            theme::text(ui, Font::Body, theme::ERROR_TEXT, entry.rule());
            // What broke it, so a rule worded "Spells/Crystal Tears/Consumables" names the one.
            if !entry.alert.detail.is_empty() {
                ui.same_line();
                theme::text(ui, Font::Body, theme::TEXT_QUIET, &entry.alert.detail);
            }
        }
    }
    // The right and bottom padding: auto-resize fits the window to the last item.
    let end = ui.item_rect_max();
    ui.set_cursor_screen_pos([end[0], end[1]]);
    ui.dummy(pad);
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        rules_log(format_args!(
            "overlay: first rule-break list drawn, display {}x{}, panel scale {scale:.2}",
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
    // SAFETY: `frame` is the host's live pointer for this call.
    unsafe { draw(ui, frame) };
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let ui_scale = unsafe { (*frame).ui_scale };
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { theme::frame_fonts(frame) };
    // `guest_draw` is `extern "C"`, so a panic unwinding out of it would abort the game. imgui-rs's
    // tokens end the menu's windows as the panic unwinds, and the menu then stays shut for the
    // session; the referee and the list are untouched.
    if catch_unwind(AssertUnwindSafe(|| {
        crate::menu::draw_ui(ui, ui_scale, &fonts);
    }))
    .is_err()
    {
        crate::menu::hide_for_session();
    }
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct RulesOverlay;

impl ImguiRenderLoop for RulesOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        // The one default face every host builds, so a guest's fallback text is the same size
        // whichever DLL won the overlay.
        let font_px = er_build_watermark_core::overlay_host::add_host_default_font(ctx);
        rules_log(format_args!(
            "overlay: render loop initialized, default font {font_px}px"
        ));
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
