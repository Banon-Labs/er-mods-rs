//! Where the net-effects bar sits on screen.
//!
//! Ungated on purpose: pure geometry with no Windows or imgui types crossing the boundary, so
//! the right-edge anchor and the equal padding are proven by
//! `cargo test` on the host instead of by a game launch and a screenshot.

// Windows-only in practice; kept portable so the geometry below is asserted on the host.
#![cfg_attr(not(windows), allow(dead_code))]

// Every size below is in pixels at 1080p, as the overlay theme gives its sizes, and is multiplied
// by the theme's panel scale where it is used.

/// Distance from the panel to each screen edge it is anchored to.
///
/// One number for both the top and the right edge on purpose: the bar should read as pinned to
/// the corner, and two different numbers read as a mistake even when neither is wrong.
pub(crate) const SCREEN_MARGIN: f32 = 48.0;

/// Border-to-text padding, identical on all four sides.
pub(crate) const PANEL_PADDING: f32 = 14.0;

/// Blank space between two text rows.
///
/// Deliberately not folded into a row's height: the old layout advanced by a 26px row for 13px
/// text, so the leftover 13px landed under the last row and the bottom margin was twice the top.
pub(crate) const ROW_GAP: f32 = 8.0;

/// Corner radius of the panel and of the toggle button drawn inside it.
pub(crate) const PANEL_ROUNDING: f32 = 6.0;

/// `[x0, y0, x1, y1]` in screen pixels.
pub(crate) type Rect = [f32; 4];

/// The resolved geometry of one frame of the bar.
pub(crate) struct PanelLayout {
    /// The full background panel.
    pub(crate) panel: Rect,
    /// Left edge of every text row.
    pub(crate) text_x: f32,
    /// Top of the first (header) row.
    pub(crate) first_row_y: f32,
    /// Distance from one row's top to the next row's top.
    pub(crate) row_advance: f32,
}

/// Lay out a right-anchored panel that fits `content_width` of text across `rows` rows, its
/// margins, padding and gaps multiplied by `scale` (the theme's panel scale; anything not a
/// positive number is 1).
///
/// The panel is sized to its content rather than to a fixed width, which is what makes the left
/// and right padding equal instead of leaving a ragged gap on whichever side the text stops short.
pub(crate) fn panel_layout(
    display: [f32; 2],
    content_width: f32,
    row_height: f32,
    rows: usize,
    scale: f32,
) -> PanelLayout {
    let scale = if scale.is_finite() && scale > 0.0 {
        scale
    } else {
        1.0
    };
    let margin = SCREEN_MARGIN * scale;
    let padding = PANEL_PADDING * scale;
    let row_gap = ROW_GAP * scale;
    let rows = rows.max(1);
    let display_width = display[0].max(1.0);
    // Never let the anchor push the panel off the left edge on a narrow display: the widest the
    // panel may get still leaves the margin on both sides.
    let max_width = (display_width - margin * 2.0).max(padding * 2.0);
    let width = (content_width.max(0.0) + padding * 2.0).min(max_width);
    let row_height = row_height.max(1.0);
    let content_height = row_height * rows as f32 + row_gap * (rows - 1) as f32;
    let height = content_height + padding * 2.0;

    let x1 = display_width - margin;
    let x0 = x1 - width;
    let y0 = margin;
    let y1 = y0 + height;

    PanelLayout {
        panel: [x0, y0, x1, y1],
        text_x: x0 + padding,
        first_row_y: y0 + padding,
        row_advance: row_height + row_gap,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DISPLAY: [f32; 2] = [1920.0, 1080.0];
    /// The theme's body text at 1080p: Plex Sans at a 13-pixel em, 1.3 lines to the em.
    const ROW_HEIGHT: f32 = 16.9;
    /// The theme's panel scale at 1080p.
    const SCALE: f32 = 1.0;

    fn assert_close(actual: f32, expected: f32, what: &str) {
        assert!(
            (actual - expected).abs() < 0.001,
            "{what}: expected {expected}, got {actual}"
        );
    }

    #[test]
    fn the_panel_is_anchored_to_the_right_edge() {
        let layout = panel_layout(DISPLAY, 400.0, ROW_HEIGHT, 4, SCALE);
        assert_close(
            DISPLAY[0] - layout.panel[2],
            SCREEN_MARGIN,
            "right screen margin",
        );
        assert!(
            layout.panel[0] > DISPLAY[0] * 0.5,
            "a right-anchored panel starts in the right half of the screen, not at x={}",
            layout.panel[0]
        );
    }

    #[test]
    fn the_top_and_right_screen_margins_are_the_same() {
        let layout = panel_layout(DISPLAY, 400.0, ROW_HEIGHT, 4, SCALE);
        assert_close(layout.panel[1], SCREEN_MARGIN, "top screen margin");
        assert_close(
            DISPLAY[0] - layout.panel[2],
            SCREEN_MARGIN,
            "right screen margin",
        );
    }

    #[test]
    fn all_four_paddings_are_equal() {
        let content_width = 400.0;
        let rows = 4;
        let layout = panel_layout(DISPLAY, content_width, ROW_HEIGHT, rows, SCALE);

        assert_close(layout.text_x - layout.panel[0], PANEL_PADDING, "left pad");
        assert_close(
            layout.panel[2] - (layout.text_x + content_width),
            PANEL_PADDING,
            "right pad",
        );
        assert_close(
            layout.first_row_y - layout.panel[1],
            PANEL_PADDING,
            "top pad",
        );

        let last_row_bottom =
            layout.first_row_y + layout.row_advance * (rows - 1) as f32 + ROW_HEIGHT;
        assert_close(
            layout.panel[3] - last_row_bottom,
            PANEL_PADDING,
            "bottom pad",
        );
    }

    #[test]
    fn the_bottom_pad_stays_equal_at_every_row_count() {
        for rows in 1..8 {
            let layout = panel_layout(DISPLAY, 400.0, ROW_HEIGHT, rows, SCALE);
            let last_row_bottom =
                layout.first_row_y + layout.row_advance * (rows - 1) as f32 + ROW_HEIGHT;
            assert_close(
                layout.panel[3] - last_row_bottom,
                PANEL_PADDING,
                &format!("bottom pad at {rows} rows"),
            );
        }
    }

    #[test]
    fn a_narrow_display_keeps_the_panel_on_screen() {
        let display = [640.0, 480.0];
        let layout = panel_layout(display, 5000.0, ROW_HEIGHT, 4, SCALE);
        assert!(
            layout.panel[0] >= SCREEN_MARGIN - 0.001,
            "the panel ran off the left edge: x0={}",
            layout.panel[0]
        );
        assert_close(
            display[0] - layout.panel[2],
            SCREEN_MARGIN,
            "right screen margin on a narrow display",
        );
    }

    #[test]
    fn the_margins_and_padding_follow_the_display_scale() {
        let display = [3840.0, 2160.0];
        let rows = 4;
        let row_height = ROW_HEIGHT * 2.0;
        let layout = panel_layout(display, 800.0, row_height, rows, 2.0);
        assert_close(
            display[0] - layout.panel[2],
            SCREEN_MARGIN * 2.0,
            "right margin",
        );
        assert_close(layout.panel[1], SCREEN_MARGIN * 2.0, "top margin");
        assert_close(
            layout.text_x - layout.panel[0],
            PANEL_PADDING * 2.0,
            "left pad",
        );
        assert_close(
            layout.row_advance,
            row_height + ROW_GAP * 2.0,
            "row advance",
        );
        let last_row_bottom =
            layout.first_row_y + layout.row_advance * (rows - 1) as f32 + row_height;
        assert_close(
            layout.panel[3] - last_row_bottom,
            PANEL_PADDING * 2.0,
            "bottom pad",
        );
        // A scale that is not a positive number lays the panel out at 1080p sizes.
        for bad in [0.0, -1.0, f32::NAN, f32::INFINITY] {
            let fallback = panel_layout(DISPLAY, 400.0, ROW_HEIGHT, rows, bad);
            assert_close(
                fallback.panel[1],
                SCREEN_MARGIN,
                "top margin at a bad scale",
            );
        }
    }
}
