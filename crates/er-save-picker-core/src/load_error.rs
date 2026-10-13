//! The screen shown when a load has failed and will not finish.
//!
//! Before this existed a frozen load had two endings, and both were wrong. The cover held a bar
//! that never moved, or, once its 20 s composite cap fired, it stopped drawing and left whatever the
//! game had underneath. On 2026-10-13 that was a title whose logo this mod hides and whose menu this
//! mod suppresses, so the player was left on pure black with no way out short of killing the process.
//!
//! This puts a plain statement on screen instead, with one button that closes the game. Closing is
//! the recovery because it is the one known to work: a restart rebuilds every piece of state the
//! failed load left behind, and no run has shown that returning to the title from a half-torn-down
//! switch is safe.
//!
//! It lives beside the picker overlay because it is drawn on the same full-frame cover and reads the
//! same keyboard, pad and mouse polls. A second copy of those polls would be a second set of
//! edge-detection state competing with the first for the same presses.

use std::sync::atomic::{AtomicBool, Ordering};

use er_loading_bar_core::draw_text_rgb as boot_draw_text_rgb;
use er_loading_bar_core::fill_rect_rgb as boot_fill_rect;
use er_loading_bar_core::{GLYPH_H, boot_text_scale, text_width};

pub use er_telemetry_core::counters::{
    LOAD_ERROR_SCREEN_ARM_COUNT, LOAD_ERROR_SCREEN_ARMED, LOAD_ERROR_SCREEN_DRAW_HITS,
    LOAD_ERROR_SCREEN_EXIT_REFUSED,
};

use crate::host::{append_autoload_debug, request_game_exit};
use crate::overlay::{
    MouseButton, PICKER_ACT_SELECT, PICKER_RGB_DETAIL, PICKER_RGB_DIM, PICKER_RGB_PANEL,
    PICKER_RGB_RULE, PICKER_RGB_SEL_BAR, PICKER_RGB_SEL_TEXT, PICKER_RGB_WARNING, picker_wrap_text,
    save_picker_mouse_click, save_picker_sample,
};

pub const LOAD_ERROR_TITLE: &str = "AN ERROR OCCURRED DURING LOADING";
pub const LOAD_ERROR_BODY: &str = "Please restart the game and try again.";
pub const LOAD_ERROR_BUTTON: &str = "CLOSE GAME";
const LOAD_ERROR_HINT: &str = "CLICK CLOSE GAME, OR PRESS ENTER / A";
const LOAD_ERROR_EXIT_REFUSED_TEXT: &str = "A save is still being written. Try again in a moment.";

/// Everything outside the panel. The screen replaces the frame rather than sitting on the bar and
/// the save-picker background, because a frozen bar beside the message reads as "still loading".
const LOAD_ERROR_RGB_BACKDROP: [u8; 3] = [0, 0, 0];

/// Lines reserved for the body, so the button does not move if the sentence wraps.
const LOAD_ERROR_BODY_LINES: usize = 2;

/// The host refused the last Close Game press, so the screen says why instead of looking dead.
static LOAD_ERROR_EXIT_REFUSED_SHOWN: AtomicBool = AtomicBool::new(false);

/// Raise the screen. Returns `true` only for the call that raised it; later calls are no-ops, so a
/// detector that fires every frame logs once.
///
/// Called from the cover's composite on the render thread, the one thread whose input polls see the
/// player's keys, so the clear below reaches the same state the input tick reads.
pub fn arm_load_error_screen(reason: &str) -> bool {
    if LOAD_ERROR_SCREEN_ARMED.swap(1, Ordering::SeqCst) != 0 {
        return false;
    }
    LOAD_ERROR_EXIT_REFUSED_SHOWN.store(false, Ordering::SeqCst);
    // Clear the "pressed since the last poll" state, so a press made before the screen existed -- a
    // player mashing at a frozen bar -- cannot close the game on the frame it appears. Nothing waits
    // here: every press from this point on counts.
    let _ = save_picker_sample();
    let _ = save_picker_mouse_click();
    let n = LOAD_ERROR_SCREEN_ARM_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
    append_autoload_debug(format_args!(
        "load-error: screen raised #{n} ({reason}) -- the load cannot finish; telling the player to restart and offering Close Game"
    ));
    true
}

/// Lower the screen, because the load recovered after all and the cover's own release now owns the
/// frame. A detector that can be wrong must be able to take its verdict back, or a slow load that
/// does finish would sit behind an error message over a playable world.
pub fn disarm_load_error_screen(reason: &str) {
    if LOAD_ERROR_SCREEN_ARMED.swap(0, Ordering::SeqCst) == 0 {
        return;
    }
    LOAD_ERROR_EXIT_REFUSED_SHOWN.store(false, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "load-error: screen lowered ({reason}) -- the load recovered, so the cover's normal release takes over"
    ));
}

/// True while the screen owns the cover: its drawing and its input.
pub fn load_error_screen_active() -> bool {
    LOAD_ERROR_SCREEN_ARMED.load(Ordering::SeqCst) != 0
}

/// One input poll while the screen is up. Render thread, from the picker overlay's input tick.
///
/// The keyboard is polled here, not taken from the picker's low-level hook, because arming clears
/// this poll's "pressed since the last call" state and the hook has no such state to clear.
pub(crate) fn load_error_input_tick() {
    let (_held, pressed) = save_picker_sample();
    let click = save_picker_mouse_click();
    if pressed & PICKER_ACT_SELECT != 0 {
        load_error_activate("enter-or-a");
        return;
    }
    if let Some(click) = click
        && click.button == MouseButton::Left
        && load_error_layout(click.w, click.h)
            .is_some_and(|layout| layout.button.contains(click.x, click.y))
    {
        load_error_activate("click");
    }
}

fn load_error_activate(source: &str) {
    append_autoload_debug(format_args!("load-error: Close Game pressed ({source})"));
    // Does not return when the host closes the game.
    if request_game_exit("load-error-close-game") {
        return;
    }
    LOAD_ERROR_SCREEN_EXIT_REFUSED.fetch_add(1, Ordering::SeqCst);
    LOAD_ERROR_EXIT_REFUSED_SHOWN.store(true, Ordering::SeqCst);
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Rect {
    pub(crate) x: usize,
    pub(crate) y: usize,
    pub(crate) w: usize,
    pub(crate) h: usize,
}

impl Rect {
    pub(crate) fn contains(self, x: usize, y: usize) -> bool {
        x >= self.x && x < self.x + self.w && y >= self.y && y < self.y + self.h
    }
}

/// Where everything on the screen goes, for one frame size. The rasteriser and the mouse hit test
/// both read this, so a click lands on the button that was drawn.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct LoadErrorLayout {
    scale: usize,
    title_scale: usize,
    line_h: usize,
    panel: Rect,
    text_x: usize,
    text_w: usize,
    title_y: usize,
    rule_y: usize,
    rule_h: usize,
    body_y: usize,
    status_y: usize,
    pub(crate) button: Rect,
    hint_y: usize,
}

pub(crate) fn load_error_layout(w: usize, h: usize) -> Option<LoadErrorLayout> {
    if w == 0 || h == 0 {
        return None;
    }
    // The boot bar's text rule, so the screen reads at the same size as everything else this mod
    // draws, with the heading one step above the body.
    let scale = boot_text_scale(h);
    let title_scale = scale + 1;
    let line_h = GLYPH_H * scale;
    let title_h = GLYPH_H * title_scale;
    let pad = line_h * 2;
    let gap = line_h;
    let rule_h = scale.max(1);
    let button_w = text_width(LOAD_ERROR_BUTTON, scale) + pad * 2;
    let button_h = line_h + scale * 8;
    let widest_text = text_width(LOAD_ERROR_TITLE, title_scale)
        .max(text_width(LOAD_ERROR_HINT, scale))
        .max(button_w);
    let panel_w = (widest_text + pad * 2).max(w / 2);
    let panel_h = pad
        + title_h
        + gap
        + rule_h
        + gap
        + LOAD_ERROR_BODY_LINES * line_h
        + gap
        + line_h
        + gap
        + button_h
        + gap
        + line_h
        + pad;
    if panel_w > w || panel_h > h {
        return None;
    }
    let panel = Rect {
        x: (w - panel_w) / 2,
        y: (h - panel_h) / 2,
        w: panel_w,
        h: panel_h,
    };
    let text_x = panel.x + pad;
    let text_w = panel_w - pad * 2;
    let title_y = panel.y + pad;
    let rule_y = title_y + title_h + gap;
    let body_y = rule_y + rule_h + gap;
    let status_y = body_y + LOAD_ERROR_BODY_LINES * line_h + gap;
    let button_y = status_y + line_h + gap;
    let button = Rect {
        x: panel.x + (panel_w - button_w) / 2,
        y: button_y,
        w: button_w,
        h: button_h,
    };
    let hint_y = button_y + button_h + gap;
    Some(LoadErrorLayout {
        scale,
        title_scale,
        line_h,
        panel,
        text_x,
        text_w,
        title_y,
        rule_y,
        rule_h,
        body_y,
        status_y,
        button,
        hint_y,
    })
}

/// `x` that centres `text` at `scale` inside `area`.
fn centred_x(area: Rect, text: &str, scale: usize) -> usize {
    area.x + area.w.saturating_sub(text_width(text, scale)) / 2
}

/// Draw the screen over the whole `w*h` RGBA8 frame. Returns `false` when the frame is too small to
/// hold it, in which case nothing is drawn.
pub fn overlay_load_error_onto(buf: &mut [u8], w: usize, h: usize) -> bool {
    let Some(l) = load_error_layout(w, h) else {
        return false;
    };
    boot_fill_rect(buf, w, h, 0, 0, w, h, LOAD_ERROR_RGB_BACKDROP);
    boot_fill_rect(
        buf,
        w,
        h,
        l.panel.x,
        l.panel.y,
        l.panel.w,
        l.panel.h,
        PICKER_RGB_PANEL,
    );
    boot_draw_text_rgb(
        buf,
        w,
        h,
        l.text_x,
        l.title_y,
        LOAD_ERROR_TITLE,
        PICKER_RGB_WARNING,
        l.title_scale,
    );
    boot_fill_rect(
        buf,
        w,
        h,
        l.text_x,
        l.rule_y,
        l.text_w,
        l.rule_h,
        PICKER_RGB_RULE,
    );
    let mut y = l.body_y;
    for line in picker_wrap_text(LOAD_ERROR_BODY, l.text_w, l.scale, LOAD_ERROR_BODY_LINES) {
        boot_draw_text_rgb(buf, w, h, l.text_x, y, &line, PICKER_RGB_DETAIL, l.scale);
        y += l.line_h;
    }
    if LOAD_ERROR_EXIT_REFUSED_SHOWN.load(Ordering::SeqCst) {
        for line in picker_wrap_text(LOAD_ERROR_EXIT_REFUSED_TEXT, l.text_w, l.scale, 1) {
            boot_draw_text_rgb(
                buf,
                w,
                h,
                l.text_x,
                l.status_y,
                &line,
                PICKER_RGB_WARNING,
                l.scale,
            );
        }
    }
    boot_fill_rect(
        buf,
        w,
        h,
        l.button.x,
        l.button.y,
        l.button.w,
        l.button.h,
        PICKER_RGB_SEL_BAR,
    );
    boot_draw_text_rgb(
        buf,
        w,
        h,
        centred_x(l.button, LOAD_ERROR_BUTTON, l.scale),
        l.button.y + (l.button.h - l.line_h) / 2,
        LOAD_ERROR_BUTTON,
        PICKER_RGB_SEL_TEXT,
        l.scale,
    );
    boot_draw_text_rgb(
        buf,
        w,
        h,
        centred_x(l.panel, LOAD_ERROR_HINT, l.scale),
        l.hint_y,
        LOAD_ERROR_HINT,
        PICKER_RGB_DIM,
        l.scale,
    );
    LOAD_ERROR_SCREEN_DRAW_HITS.fetch_add(1, Ordering::SeqCst);
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAMES: [(usize, usize); 5] = [
        (1280, 720),
        (1920, 1080),
        (2560, 1440),
        (3440, 1440),
        (3840, 2160),
    ];

    fn pixel(buf: &[u8], w: usize, x: usize, y: usize) -> [u8; 3] {
        let i = (y * w + x) * er_loading_bar_core::RGBA8_BPP;
        [buf[i], buf[i + 1], buf[i + 2]]
    }

    /// Every piece of text sits inside the panel and the panel inside the frame, at every common
    /// size: text drawn past the panel edge is text the player cannot read.
    #[test]
    fn everything_fits_inside_the_panel_at_common_frame_sizes() {
        for (w, h) in FRAMES {
            let l = load_error_layout(w, h).expect("a common frame size holds the screen");
            assert!(
                l.panel.x + l.panel.w <= w && l.panel.y + l.panel.h <= h,
                "{w}x{h}"
            );
            assert!(
                text_width(LOAD_ERROR_TITLE, l.title_scale) <= l.text_w,
                "{w}x{h}"
            );
            assert!(text_width(LOAD_ERROR_HINT, l.scale) <= l.text_w, "{w}x{h}");
            assert!(
                text_width(LOAD_ERROR_BUTTON, l.scale) < l.button.w,
                "{w}x{h}"
            );
            assert!(l.button.x >= l.panel.x && l.button.x + l.button.w <= l.panel.x + l.panel.w);
            assert!(l.hint_y + l.line_h <= l.panel.y + l.panel.h, "{w}x{h}");
        }
    }

    #[test]
    fn a_frame_too_small_draws_nothing_rather_than_a_clipped_screen() {
        assert!(load_error_layout(0, 1080).is_none());
        assert!(load_error_layout(200, 100).is_none());
        let mut buf = vec![7u8; 200 * 100 * er_loading_bar_core::RGBA8_BPP];
        assert!(!overlay_load_error_onto(&mut buf, 200, 100));
        assert!(buf.iter().all(|&b| b == 7));
    }

    /// The hit test and the rasteriser agree on where the button is.
    #[test]
    fn a_click_hits_the_drawn_button_and_nothing_else() {
        let (w, h) = (1920, 1080);
        let l = load_error_layout(w, h).unwrap();
        let (cx, cy) = (l.button.x + l.button.w / 2, l.button.y + l.button.h / 2);
        assert!(l.button.contains(cx, cy));
        assert!(!l.button.contains(l.button.x + l.button.w, cy));
        assert!(!l.button.contains(cx, l.button.y.saturating_sub(1)));
        assert!(!l.button.contains(w / 2, l.title_y));

        let mut buf = vec![0xffu8; w * h * er_loading_bar_core::RGBA8_BPP];
        assert!(overlay_load_error_onto(&mut buf, w, h));
        // The button's top-left corner is button fill; the frame's corner is backdrop.
        assert_eq!(pixel(&buf, w, l.button.x, l.button.y), PICKER_RGB_SEL_BAR);
        assert_eq!(pixel(&buf, w, 0, 0), LOAD_ERROR_RGB_BACKDROP);
        assert_eq!(pixel(&buf, w, l.panel.x, l.panel.y), PICKER_RGB_PANEL);
    }

    /// One test owns the process-wide latch, so the steps run in order rather than racing.
    #[test]
    fn the_screen_arms_once_and_never_exits_without_a_host() {
        assert!(!load_error_screen_active());
        assert!(arm_load_error_screen("test"));
        assert!(!arm_load_error_screen("test-again"));
        assert!(load_error_screen_active());
        // With no host installed, Close Game is refused and the screen says so.
        let refused_before = LOAD_ERROR_SCREEN_EXIT_REFUSED.load(Ordering::SeqCst);
        load_error_activate("test");
        assert_eq!(
            LOAD_ERROR_SCREEN_EXIT_REFUSED.load(Ordering::SeqCst),
            refused_before + 1
        );
        assert!(LOAD_ERROR_EXIT_REFUSED_SHOWN.load(Ordering::SeqCst));
        disarm_load_error_screen("test");
        assert!(!load_error_screen_active());
        assert!(!LOAD_ERROR_EXIT_REFUSED_SHOWN.load(Ordering::SeqCst));
    }
}
