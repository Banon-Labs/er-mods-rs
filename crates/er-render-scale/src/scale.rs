//! Which scene sizes the screen allows, what the dynamic resolution update is told, and how the
//! size the game drew is read back. Pure, so all of it is tested on the host.
//!
//! # Clean divisors only
//!
//! The scene is the output size divided by a whole number, so every scene pixel becomes an exact
//! block of screen pixels when the composite upsamples it. Both sides must divide exactly, and
//! each scene side must also be a multiple of [`VIEWPORT_SNAP_PX`], because the view setup rounds
//! the viewport to that (measured 2026-10-15: `1/24` of 2160 came out 92, not 90). That makes the
//! offered divisors exactly the divisors of `gcd(width, height) / 4`; at 3840x2160 they are 1, 2,
//! 3, 4, 5, 6, 10, 12, 15, 20, 30 and 60. 1 is always offered: it is native, which needs no
//! rounding.
//!
//! # Two sizes that must agree
//!
//! `GxDrawContext+0x3c8` is the first output's size and `+0xefc` the size beside the primary
//! ratio. Both read 3840x2160 in a 4K run. A clean block mapping needs the scene target and the
//! screen to be the same size, so when they differ no scale is offered and the reason is shown.

use std::fmt;

/// The view setup rounds the scene viewport to a multiple of this many pixels.
pub const VIEWPORT_SNAP_PX: u32 = 4;

/// Native: the scene at the output size.
pub const NATIVE: u32 = 1;

/// Why no scale is offered.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// The game has no draw context yet, so there is no output size to divide.
    NoContext,
    /// An output size of zero, which the game holds before its first swapchain.
    Empty,
    /// The two sizes the draw context carries differ, so no divisor maps the scene onto the
    /// screen in whole blocks.
    SizesDisagree {
        output: [u32; 2],
        scene_target: [u32; 2],
    },
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoContext => f.write_str("the game has not made its draw context yet"),
            Self::Empty => f.write_str("the game reports a zero output size"),
            Self::SizesDisagree {
                output,
                scene_target,
            } => write!(
                f,
                "the output is {} but the scene target is {}, so no scale maps one onto the \
                 other in whole pixels",
                size_text(*output),
                size_text(*scene_target)
            ),
        }
    }
}

/// `WxH` with the multiplication sign, as the menu labels a size.
#[must_use]
pub fn size_text(size: [u32; 2]) -> String {
    format!("{}\u{d7}{}", size[0], size[1])
}

/// The output size a scale is taken of, or why none is offered, from the draw context's two sizes
/// (`None` when there is no draw context).
pub fn offered_output(sizes: Option<([u32; 2], [u32; 2])>) -> Result<[u32; 2], Refusal> {
    let Some((output, scene_target)) = sizes else {
        return Err(Refusal::NoContext);
    };
    if output[0] == 0 || output[1] == 0 {
        return Err(Refusal::Empty);
    }
    if output != scene_target {
        return Err(Refusal::SizesDisagree {
            output,
            scene_target,
        });
    }
    Ok(output)
}

/// Does `divisor` map a scene onto `output` in whole blocks, with both scene sides on the
/// viewport's grid?
#[must_use]
pub fn is_clean(divisor: u32, output: [u32; 2]) -> bool {
    if divisor == NATIVE {
        return true;
    }
    divisor != 0
        && output.iter().all(|&side| {
            side.is_multiple_of(divisor)
                && (side / divisor).is_multiple_of(VIEWPORT_SNAP_PX)
                && side / divisor > 0
        })
}

/// Every clean divisor of `output`, smallest first, native included.
#[must_use]
pub fn clean_divisors(output: [u32; 2]) -> Vec<u32> {
    let largest = output[0].min(output[1]) / VIEWPORT_SNAP_PX;
    std::iter::once(NATIVE)
        .chain((2..=largest).filter(|&divisor| is_clean(divisor, output)))
        .collect()
}

/// The scene size `divisor` draws at on `output`.
#[must_use]
pub fn scene_size(output: [u32; 2], divisor: u32) -> [u32; 2] {
    let divisor = divisor.max(1);
    [output[0] / divisor, output[1] / divisor]
}

/// The size the view setup drew, from its viewport ratio (`GxDrawContext+0xed0`) and the output
/// size: rounded to the nearest pixel, `None` for a ratio that is not a number.
#[must_use]
pub fn drawn_size(ratio: [f32; 2], output: [u32; 2]) -> Option<[u32; 2]> {
    let side = |ratio: f32, side: u32| {
        let pixels = (f64::from(ratio) * f64::from(side)).round();
        (pixels.is_finite() && pixels >= 0.0 && pixels <= f64::from(u32::MAX))
            .then_some(pixels as u32)
    };
    Some([side(ratio[0], output[0])?, side(ratio[1], output[1])?])
}

/// The divisor actually put in force: the one chosen when it is offered and clean for the output,
/// else native.
#[must_use]
pub fn applied_divisor(selected: u32, output: Result<[u32; 2], Refusal>) -> u32 {
    match output {
        Ok(output) if selected > NATIVE && is_clean(selected, output) => selected,
        _ => NATIVE,
    }
}

/// What the dynamic resolution update is told on one call.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Plan {
    /// Hold the scene at `scale` (`1 / divisor`): enable on, coupling off, min = max = `scale`,
    /// the LOD-bias gate off.
    Write { divisor: u32, scale: f32 },
    /// Put back the values the game had, and stop writing.
    Restore,
    /// Nothing to do: native, and nothing of ours in place.
    Leave,
}

/// The plan for one call, given the divisor chosen, the output size or why there is none, and
/// whether this DLL's values are in place on the config the game passed.
#[must_use]
pub fn plan(selected: u32, output: Result<[u32; 2], Refusal>, active: bool) -> Plan {
    match applied_divisor(selected, output) {
        NATIVE if active => Plan::Restore,
        NATIVE => Plan::Leave,
        divisor => Plan::Write {
            divisor,
            scale: 1.0 / divisor as f32,
        },
    }
}

/// The slider position of `divisor` among `divisors`, or native's when it is not one of them.
#[must_use]
pub fn index_of(divisors: &[u32], divisor: u32) -> usize {
    divisors.iter().position(|&d| d == divisor).unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    const UHD: [u32; 2] = [3840, 2160];

    #[test]
    fn a_4k_screen_offers_the_twelve_clean_divisors() {
        assert_eq!(
            clean_divisors(UHD),
            vec![1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60]
        );
        // Each one draws a scene on the viewport grid that divides the screen exactly.
        for divisor in clean_divisors(UHD) {
            let scene = scene_size(UHD, divisor);
            assert_eq!([scene[0] * divisor, scene[1] * divisor], UHD);
            if divisor > 1 {
                assert_eq!(scene[0] % VIEWPORT_SNAP_PX, 0, "{divisor}");
                assert_eq!(scene[1] % VIEWPORT_SNAP_PX, 0, "{divisor}");
            }
        }
        // 8 divides both sides but 2160/8 = 270 is off the 4-pixel grid; 24 gave 92 rows live.
        assert!(!is_clean(8, UHD));
        assert!(!is_clean(24, UHD));
        assert!(!is_clean(0, UHD));
    }

    #[test]
    fn other_screens_offer_their_own_divisors() {
        assert_eq!(
            clean_divisors([1920, 1080]),
            vec![1, 2, 3, 5, 6, 10, 15, 30]
        );
        assert_eq!(
            clean_divisors([2560, 1440]),
            vec![1, 2, 4, 5, 8, 10, 20, 40]
        );
        // A width off the grid at native still offers native, and nothing else.
        assert_eq!(clean_divisors([1366, 768]), vec![1]);
        assert_eq!(clean_divisors([0, 0]), vec![1]);
    }

    #[test]
    fn each_step_is_labelled_with_its_scene_size() {
        assert_eq!(size_text(scene_size(UHD, 3)), "1280\u{d7}720");
        assert_eq!(size_text(scene_size(UHD, 1)), "3840\u{d7}2160");
        assert_eq!(size_text(scene_size(UHD, 60)), "64\u{d7}36");
    }

    #[test]
    fn the_two_sizes_must_agree_before_anything_is_offered() {
        assert_eq!(offered_output(Some((UHD, UHD))), Ok(UHD));
        assert_eq!(offered_output(None), Err(Refusal::NoContext));
        assert_eq!(offered_output(Some(([0, 0], [0, 0]))), Err(Refusal::Empty));
        let differ = offered_output(Some((UHD, [2560, 1440])));
        assert_eq!(
            differ,
            Err(Refusal::SizesDisagree {
                output: UHD,
                scene_target: [2560, 1440]
            })
        );
        let reason = differ.err().map(|refusal| refusal.to_string());
        assert!(
            reason.is_some_and(|text| text.contains("3840\u{d7}2160") && text.contains("2560"))
        );
    }

    #[test]
    fn the_drawn_size_comes_back_out_of_the_viewport_ratio() {
        assert_eq!(drawn_size([1.0 / 3.0, 1.0 / 3.0], UHD), Some([1280, 720]));
        assert_eq!(drawn_size([1.0, 1.0], UHD), Some(UHD));
        // The live 1/24 measurement: the 4-pixel snap shows up as an inexact height.
        assert_eq!(drawn_size([0.041_67, 0.042_59], UHD), Some([160, 92]));
        assert_eq!(drawn_size([f32::NAN, 1.0], UHD), None);
        assert_eq!(drawn_size([-1.0, 1.0], UHD), None);
    }

    #[test]
    fn a_divisor_is_applied_only_when_it_is_offered_and_clean() {
        assert_eq!(applied_divisor(3, Ok(UHD)), 3);
        assert_eq!(applied_divisor(8, Ok(UHD)), NATIVE);
        assert_eq!(applied_divisor(3, Err(Refusal::NoContext)), NATIVE);
        assert_eq!(applied_divisor(0, Ok(UHD)), NATIVE);
        // A divisor clean at 4K but not at 1080p falls back to native when the screen changes.
        assert_eq!(applied_divisor(4, Ok([1920, 1080])), NATIVE);
    }

    #[test]
    fn the_update_is_written_while_scaled_and_restored_once_at_native() {
        assert_eq!(
            plan(3, Ok(UHD), false),
            Plan::Write {
                divisor: 3,
                scale: 1.0 / 3.0
            }
        );
        assert_eq!(
            plan(3, Ok(UHD), true),
            Plan::Write {
                divisor: 3,
                scale: 1.0 / 3.0
            }
        );
        assert_eq!(plan(1, Ok(UHD), true), Plan::Restore);
        assert_eq!(plan(1, Ok(UHD), false), Plan::Leave);
        // Sizes that stop agreeing while scaled put the game's values back.
        assert_eq!(
            plan(
                3,
                Err(Refusal::SizesDisagree {
                    output: UHD,
                    scene_target: [1920, 1080]
                }),
                true
            ),
            Plan::Restore
        );
    }

    #[test]
    fn the_slider_finds_a_divisor_or_starts_at_native() {
        let divisors = clean_divisors(UHD);
        assert_eq!(index_of(&divisors, 3), 2);
        assert_eq!(index_of(&divisors, 8), 0);
    }
}
