//! The one present hook this DLL owns, and the two panels that draw on it.
//!
//! # Why this is its own module
//!
//! It used to be the back half of [`crate::picker::render`], which was fine while the picker was
//! the only thing this crate drew. It is not any more: the attack-set panel
//! ([`crate::moveset::banner`]) is a second surface with a completely different lifetime -- the
//! picker is up for a few seconds while you choose, the banner is up for the whole possession --
//! and it must not come with a second `Present` hook. Two `Hudhook::apply()` calls in one process
//! double-hook `Present` and the second one silently renders nothing, measured live on 2026-08-25,
//! and that is the entire reason `er_build_watermark_core::overlay_host` exists.
//!
//! So the host-join lives here, once, and both panels are drawn from [`draw`]. Adding a third
//! surface later means adding a call to that function and nothing else.
//!
//! # The install is lazy, and now has two triggers
//!
//! Nothing here runs until something needs to be drawn: the first time the picker opens, or the
//! first time a possession publishes a banner. A session that does neither ends with this DLL
//! having hooked nothing at all, which is the property the crate's entry in
//! `scripts/me3-dll-conflicts.toml` is about.
//!
//! The second trigger is not a nicety. Before it existed the banner could only appear in a session
//! where the PICKER had been opened first -- and the live session of 2026-09-02 was exactly the
//! other case: `picker_overlay=false` for its whole length while `state=active` possessed a
//! creature, so an indicator gated on the picker's install would have drawn nothing at all.
//!
//! # `frames` is not `panel draws`, and the difference cost a diagnosis
//!
//! The status line used to report a counter called `picker_draws` that this module incremented on
//! every `Present` regardless of whether the picker was open, beside `picker_rows`, which is zero
//! whenever the picker is closed. `picker_draws=3747 picker_rows=0` was therefore the correct
//! reading for a closed picker, and it was read as a picker that had drawn 3747 blank frames.
//! Verified against that same session's log: every status line with `picker_open=true` carried
//! `picker_rows=15`, and every line with `picker_rows=0` carried `picker_open=false` -- 611 status
//! lines, no counter-example. Nothing was broken and the counter said so; the name did not.
//!
//! The counters are therefore split by what they actually count: [`frames`] is Present frames this
//! module drew into, and each panel counts its own builds.
//!
//! # The look
//!
//! Both panels draw in the workspace's shared theme (`er-overlay-theme`): its window and panel
//! styles, its colors, and its fonts, which the overlay host builds into its atlas and hands back
//! in each frame. That is why this module draws only from [`guest_draw`], even while it hosts: the
//! fonts and the display's scale arrive in the frame the host passes its guests, so a host that
//! drew its own panels directly would draw them without either.

#![cfg(windows)]

use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize, Ordering};

use er_build_watermark_core::overlay_host::{
    OVERLAY_ABI_TAG, OverlayFrame, adopt_frame, register_guest, register_with_host,
};
use er_overlay_theme::{self as theme, Font, FrameFonts, Theme};
use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Condition, Context, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::log::possess_log;
use crate::moveset::banner::{self, Banner};

/// Present frames this module drew into, whether or not either panel had anything to say. `0`
/// after [`install_once`] has run means the overlay never reached the swapchain, which is a
/// different fault from a panel that is simply closed.
static FRAMES: AtomicUsize = AtomicUsize::new(0);
/// Frames the attack-set panel was built on.
static BANNER_DRAWS: AtomicUsize = AtomicUsize::new(0);
/// Set once this module is either hosting or registered as a guest.
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
/// The DLL's own module handle, stashed by [`arm`] so the lazy install has one to hand hudhook.
static HMODULE: AtomicUsize = AtomicUsize::new(0);

/// Gap between the attack-set panel and the top-right corner of the screen, at 1080p; scaled with
/// the panel like everything else in it.
const BANNER_MARGIN: f32 = 24.0;
/// Opaque enough to read white text over a bright sky, transparent enough not to be a hole in the
/// frame. The panel sits over gameplay for the whole possession, so this is a different judgement
/// from the picker's, which is up for seconds at a time and replaces what is behind it.
const BANNER_BG_ALPHA: f32 = 0.55;
/// The window ID. Nothing after `###` is drawn, and this panel draws no title bar at all, but the
/// id still has to be STABLE: imgui derives window identity from the label, so letting the page
/// number into it would make every page turn a brand-new window with its own saved state.
const BANNER_ID: &str = "###er-npc-possess-attack-sets";

pub(crate) fn frames() -> usize {
    FRAMES.load(Ordering::Relaxed)
}

pub(crate) fn banner_draws() -> usize {
    BANNER_DRAWS.load(Ordering::Relaxed)
}

pub(crate) fn installed() -> bool {
    INSTALLED.load(Ordering::Relaxed) != 0
}

/// Remember the module handle. Called from `DllMain`; installs nothing.
pub(crate) fn arm(hmodule_raw: usize) {
    HMODULE.store(hmodule_raw, Ordering::SeqCst);
}

/// One frame: every panel this DLL owns, in back-to-front order, in the theme at the display's
/// scale (`ui_scale`, the display's height over 1080) and in the `fonts` the host has built of
/// the ones [`request_theme_fonts`] asked for.
fn draw(ui: &Ui, ui_scale: f32, fonts: &FrameFonts) {
    FRAMES.fetch_add(1, Ordering::Relaxed);
    let frame_theme = Theme::new(
        theme::panel_scale(ui_scale),
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    );
    let _theme = theme::enter(frame_theme);
    crate::picker::render::draw(ui);
    draw_banner(ui);
}

/// Hand the theme's faces to the overlay host, the first time either panel is drawn open; every
/// later call does nothing. The host's default font is drawn with until they are built.
pub(crate) fn request_theme_fonts() {
    let scale = theme::current().scale;
    if let Some(handles) = theme::request_fonts(scale) {
        possess_log(format_args!(
            "overlay: panel fonts: Plex Sans regular, medium and semibold and JetBrains Mono \
             handed to the overlay host at scale {scale}: handles {handles:?}. A 0 is a face the \
             host refused; a handle another panel already holds is that panel's fonts, shared"
        ));
    }
}

/// The attack-set panel, top right, for as long as something is possessed.
///
/// Lock-free fast path first, and it is the common one: once installed this runs on every
/// `Present` for the rest of the process, and nothing is possessed on almost all of them.
fn draw_banner(ui: &Ui) {
    // One call per drawn frame, and `take_frame` is named for it: this is what spends the header
    // highlight, so calling it twice in a frame would burn the flash at double rate.
    let Some((banner, flashing)) = banner::take_frame() else {
        return;
    };
    BANNER_DRAWS.fetch_add(1, Ordering::Relaxed);
    request_theme_fonts();
    let t = theme::current();
    let display = ui.io().display_size;
    let margin = t.px(BANNER_MARGIN).round();
    let window_style = theme::window_style(ui);
    let begun = ui
        .window(BANNER_ID)
        // Pinned to the corner, with the pivot on the panel's own top-right so the auto-sized
        // width does not have to be known in advance. `Always` rather than `FirstUseEver`
        // because this is an indicator rather than a tool: it must not be draggable off screen,
        // and it must follow a resolution change instead of being left where the old one put it.
        .position([display[0] - margin, margin], Condition::Always)
        .position_pivot([1.0, 0.0])
        .always_auto_resize(true)
        .no_decoration()
        .movable(false)
        // It must never eat an input. It is on screen during play, over a game whose mouse and
        // gamepad this DLL deliberately does not claim; a panel that took focus or swallowed a
        // click would be a worse defect than the one it was written to fix.
        .no_inputs()
        .focus_on_appearing(false)
        .bring_to_front_on_focus(false)
        // Not persisted to imgui.ini: it has a fixed place, so remembering a position for it
        // would only give a future session a chance to restore a stale one.
        .save_settings(false)
        // The theme's panel background, let further through: the panel sits over gameplay.
        .bg_alpha(BANNER_BG_ALPHA)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return;
    };
    theme::apply_font_scale(ui);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    // The theme's window style has no padding, so the lines are set in from the edges by hand: a
    // group at the padding's offset, then an empty item that far past its far corner, which is
    // what the window sizes itself to.
    let pad = [
        t.px(theme::CONTENT_PAD[0]).round(),
        t.px(theme::CONTENT_PAD[1]).round(),
    ];
    ui.set_cursor_pos(pad);
    ui.group(|| banner_rows(ui, &banner, flashing));
    ui.set_cursor_screen_pos(ui.item_rect_max());
    ui.dummy(pad);
    let pos = ui.window_pos();
    let size = ui.window_size();
    theme::painter(ui).outline(pos, [pos[0] + size[0], pos[1] + size[1]], theme::RULE, 0.0);
}

/// The panel's lines, in mono: [`Banner`] lines its columns up with spaces.
fn banner_rows(ui: &Ui, banner: &Banner, flashing: bool) {
    // The creature and the footer are present but subordinate to the hands, so they are quiet.
    theme::text(ui, Font::Mono, theme::TEXT_QUIET, &banner.title());
    for hand in &banner.hands {
        // The highlight is the half of this feature with a deadline. The complaint was not only
        // "I cannot see which set I am on", it was "I cannot see the change happen" -- so the
        // hand whose key was just pressed is the one that changes color, and only for
        // `banner::FLASH`. The accent's bright half is the theme's color for what just changed.
        let hot = flashing && banner.flash == Some(hand.hand);
        let color = if hot {
            theme::ACCENT_HOVER
        } else {
            theme::TEXT
        };
        theme::text(ui, Font::Mono, color, &hand.header());
        for lead in &hand.leads {
            theme::mono(ui, &lead.line());
        }
    }
    if let Some(footer) = banner.footer() {
        theme::text(ui, Font::Mono, theme::TEXT_QUIET, &footer);
    }
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
    // SAFETY: `adopt_frame` returned a frame, so the pointer is live for this call.
    let ui_scale = unsafe { (*frame).ui_scale };
    // SAFETY: the host's pointer for this call, as above.
    let fonts = unsafe { theme::frame_fonts(frame) };
    draw(ui, ui_scale, &fonts);
}

/// This module's own render loop, used only when nothing else in the process hosts one.
struct PossessOverlay;

impl ImguiRenderLoop for PossessOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        // The one default face every host builds, so a guest's fallback text is the same size
        // whichever DLL won the overlay.
        let font_px = er_build_watermark_core::overlay_host::add_host_default_font(ctx);
        possess_log(format_args!(
            "overlay: render loop initialized, default font {font_px}px"
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        // Guests first and before any early return: this module hosts the only imgui context in
        // the process, so returning early here draws nothing for every other overlay too. This
        // module's own panels are one of the guests (see the module docs).
        er_build_watermark_core::overlay_host::dispatch_guests(ui);
        // The watermark is not a guest -- it never registers one, because its loser path assumes
        // whichever module hosts will carry its rows directly. See er-invasion-path's render loop,
        // where omitting this left a whole session with no watermark at all.
        er_build_watermark_core::draw_rows(ui, possess_log);
    }
}

/// Join the process's overlay, hosting it if nobody else does. Idempotent.
///
/// Called the first time the picker opens or the first time a possession publishes an attack-set
/// banner, rather than at load, so a session that does neither installs nothing.
pub(crate) fn install_once() {
    if INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    // On its own thread, like every other shell here. `install()` below waits for the game's
    // window (bounded, but tens of seconds), takes a named kernel mutex, walks every loaded
    // module calling into their registrars, and may end in `Hudhook::apply()` -- which creates a
    // D3D12 device and suspends every thread in the process to write its detours. Its caller is
    // the recurring `FrameBegin` game task; doing any of that inline would stall the game, and
    // `er-invasion-path`'s `DllMain` says the same thing about the loader lock.
    let _ = std::thread::Builder::new()
        .name("er-npc-possess-overlay".to_owned())
        .spawn(install);
}

fn install() {
    if register_with_host(guest_draw) {
        possess_log(format_args!(
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
                // Installed stays set: this module is joined to an overlay and must not run the
                // install path again.
                possess_log(format_args!(
                    "overlay: another module won the overlay while this one waited for the \
                     window; registered as a GUEST (no second Present hook)"
                ));
            } else {
                INSTALLED.store(0, Ordering::SeqCst);
                possess_log(format_args!(
                    "overlay: a module owns the overlay but would not accept a guest -- neither \
                     the creature list nor the attack-set panel can be drawn. The host speaks a \
                     different overlay ABI than this DLL's {:#06x}; rebuild the whole profile \
                     from one tree.",
                    er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
                ));
            }
            return;
        }
        er_build_watermark_core::OverlayClaim::NoWindow => {
            INSTALLED.store(0, Ordering::SeqCst);
            possess_log(format_args!(
                "overlay: this process never got a sized top-level window, so there is nothing to \
                 draw on and no host to join. Not an ABI problem."
            ));
            return;
        }
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(
        HMODULE.load(Ordering::SeqCst) as *mut c_void
    );
    match hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(PossessOverlay)
        .with_hmodule(hmodule)
        .build()
        .apply()
    {
        Ok(()) => {
            er_build_watermark_core::overlay_host::become_host();
            // Drawn as this host's own guest, so the panels get their fonts and the display's
            // scale out of a frame like every other guest.
            let registered = register_guest(OVERLAY_ABI_TAG, guest_draw);
            possess_log(format_args!(
                "overlay: hudhook dx12 overlay installed (this module HOSTS the imgui context)"
            ));
            if !registered {
                possess_log(format_args!(
                    "overlay: the host refused this module's own panels as a guest; neither the \
                     creature list nor the attack-set panel can be drawn"
                ));
            }
        }
        Err(error) => {
            INSTALLED.store(0, Ordering::SeqCst);
            possess_log(format_args!(
                "overlay: hudhook dx12 install failed: {error:?}"
            ));
        }
    }
}
