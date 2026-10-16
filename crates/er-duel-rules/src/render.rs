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
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Condition, Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::breaks::record_break;
use crate::log::rules_log;

/// The list is authored for a 1080-line display and scaled from there.
const REFERENCE_HEIGHT: f32 = 1080.0;
const FONT_SCALE_AT_1080: f32 = 1.1;
/// Gap between the list and the screen's top and right edges, as a fraction of its height.
const EDGE_FRACTION: f32 = 0.015;
const BACKGROUND_ALPHA: f32 = 0.55;

const TITLE: [f32; 4] = [1.0, 0.27, 0.22, 1.0];
const TIME: [f32; 4] = [0.62, 0.60, 0.55, 1.0];
const PLAYER: [f32; 4] = [1.0, 0.96, 0.88, 1.0];
const RULE: [f32; 4] = [1.0, 0.55, 0.45, 1.0];

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

fn draw(ui: &Ui) {
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
    let scale = FONT_SCALE_AT_1080 * (display[1] / REFERENCE_HEIGHT).max(0.5);
    let edge = display[1] * EDGE_FRACTION;
    ui.window("##er-duel-rules")
        .position([display[0] - edge, edge], Condition::Always)
        .position_pivot([1.0, 0.0])
        .bg_alpha(BACKGROUND_ALPHA)
        .no_decoration()
        .no_inputs()
        .no_nav()
        .focus_on_appearing(false)
        .always_auto_resize(true)
        .build(|| {
            ui.set_window_font_scale(scale);
            ui.text_colored(TITLE, "Rule breaks");
            for entry in feed.shown() {
                ui.text_colored(TIME, entry.stamp.time());
                ui.same_line();
                ui.text_colored(PLAYER, entry.who());
                ui.same_line();
                ui.text_colored(RULE, entry.rule());
                // What broke it, so a rule worded "Spells/Crystal Tears/Consumables" names the one.
                if !entry.alert.detail.is_empty() {
                    ui.same_line();
                    ui.text_colored(TIME, &entry.alert.detail);
                }
            }
        });
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        rules_log(format_args!(
            "overlay: first rule-break list drawn, display {}x{}, font scale {scale:.2}",
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
    // `guest_draw` is `extern "C"`, so a panic unwinding out of it would abort the game. imgui-rs's
    // tokens end the menu's windows as the panic unwinds, and the menu then stays shut for the
    // session; the referee and the list are untouched.
    if catch_unwind(AssertUnwindSafe(|| crate::menu::draw_ui(ui))).is_err() {
        crate::menu::hide_for_session();
    }
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
