//! The timing of a board section taller than the screen leaves it: held at the top, carried down at
//! a reading pace, held at the bottom, carried back up, and round again.
//!
//! Lengths are canvas units, the board's own space, in which type sizes are set too, so the pace
//! reads the same at every resolution. Kept free of imgui so the host can test it.

/// How long the section holds still at either end, in seconds.
pub const SCROLL_PAUSE_S: f32 = 2.0;
/// How fast the section moves between the ends, in canvas units a second: about one and a half
/// lines of gear text, 32 px a second on a 1080-high screen.
pub const SCROLL_SPEED: f32 = 36.0;

/// How far down the content is scrolled `t` seconds into the cycle, for content `content_h` tall
/// in a viewport `viewport_h` tall. Zero whenever the content fits.
pub fn scroll_offset(content_h: f32, viewport_h: f32, t: f32) -> f32 {
    let travel = content_h - viewport_h;
    // A NaN height reads as fitting too.
    if travel.is_nan() || travel <= 0.0 || !t.is_finite() {
        return 0.0;
    }
    let run = travel / SCROLL_SPEED;
    let period = 2.0 * (SCROLL_PAUSE_S + run);
    let t = t.rem_euclid(period);
    let offset = if t < SCROLL_PAUSE_S {
        0.0
    } else if t < SCROLL_PAUSE_S + run {
        (t - SCROLL_PAUSE_S) * SCROLL_SPEED
    } else if t < 2.0 * SCROLL_PAUSE_S + run {
        travel
    } else {
        travel - (t - 2.0 * SCROLL_PAUSE_S - run) * SCROLL_SPEED
    };
    offset.clamp(0.0, travel)
}

#[cfg(test)]
mod tests {
    use super::*;

    const VIEW: f32 = 400.0;
    /// A five-second run at [`SCROLL_SPEED`].
    const RUN: f32 = 5.0;
    const CONTENT: f32 = VIEW + RUN * SCROLL_SPEED;
    const PERIOD: f32 = 2.0 * (SCROLL_PAUSE_S + RUN);

    fn close(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-3
    }

    #[test]
    fn content_that_fits_never_moves() {
        for t in [0.0, 1.0, 3.0, 100.0, 1e6] {
            assert_eq!(scroll_offset(VIEW, VIEW, t), 0.0);
            assert_eq!(scroll_offset(VIEW - 50.0, VIEW, t), 0.0);
            assert_eq!(scroll_offset(f32::NAN, VIEW, t), 0.0);
        }
    }

    #[test]
    fn holds_at_the_top_for_the_first_pause() {
        for t in [0.0, 0.5, SCROLL_PAUSE_S - 0.01] {
            assert_eq!(scroll_offset(CONTENT, VIEW, t), 0.0, "t = {t}");
        }
    }

    #[test]
    fn moves_down_at_the_set_speed() {
        let t = SCROLL_PAUSE_S + 1.0;
        assert!(close(scroll_offset(CONTENT, VIEW, t), SCROLL_SPEED));
        let t = SCROLL_PAUSE_S + 2.5;
        assert!(close(scroll_offset(CONTENT, VIEW, t), 2.5 * SCROLL_SPEED));
    }

    #[test]
    fn holds_at_the_bottom_for_the_second_pause() {
        let travel = CONTENT - VIEW;
        for t in [
            SCROLL_PAUSE_S + RUN,
            SCROLL_PAUSE_S + RUN + 1.0,
            2.0 * SCROLL_PAUSE_S + RUN - 0.01,
        ] {
            assert!(close(scroll_offset(CONTENT, VIEW, t), travel), "t = {t}");
        }
    }

    #[test]
    fn moves_back_up_and_repeats() {
        let travel = CONTENT - VIEW;
        let t = 2.0 * SCROLL_PAUSE_S + RUN + 1.0;
        assert!(close(
            scroll_offset(CONTENT, VIEW, t),
            travel - SCROLL_SPEED
        ));
        // A millisecond before the cycle ends, a millisecond's travel from the top.
        let near_top = scroll_offset(CONTENT, VIEW, PERIOD - 0.001);
        assert!(close(near_top, 0.001 * SCROLL_SPEED), "{near_top}");
        for t in [0.0, 1.0, SCROLL_PAUSE_S + 2.0, SCROLL_PAUSE_S + RUN + 1.0] {
            assert!(close(
                scroll_offset(CONTENT, VIEW, t),
                scroll_offset(CONTENT, VIEW, t + 3.0 * PERIOD)
            ));
        }
    }

    #[test]
    fn never_leaves_the_content() {
        let travel = CONTENT - VIEW;
        let mut t = 0.0;
        while t < 2.0 * PERIOD {
            let offset = scroll_offset(CONTENT, VIEW, t);
            assert!((0.0..=travel).contains(&offset), "t = {t}: {offset}");
            t += 0.01;
        }
    }
}
