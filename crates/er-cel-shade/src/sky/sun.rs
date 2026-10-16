//! The Sky tab's Sun section: where the sun stands, how strong it and the sky light are, the
//! exposure, and how soft the ray-traced shadows are. Each is written over the lighting the game
//! blends each frame ([`super::light`]); `None` leaves the game's own value.
//!
//! # What the game keeps
//!
//! Mapped in bd `er-gparam-sun-lighting-pipeline-1171-2026-10-15` and proven live through Frida
//! (`scripts/frida/sun-control.js`, bd `er-sky-gparam-override-frida-proven-1171-2026-10-15`):
//! `GXParamDB::Update` blends every gparam field for the time of day and the weather into one
//! param instance per (bank, id) once a frame, and the jobs after it derive the light's direction
//! and the ambient from those instances and pack them for the GPU. So a field written as that
//! function returns is the field the frame draws with.
//!
//! The sun is `DirLight0` of every `GXLightSet` instance (the moon at night): an angle pair the
//! direction is derived from, and an intensity in its colours' alpha. The sky light is the
//! hemisphere colours' alpha. Exposure is the tone map's first field. The soft shadows are the
//! ray-traced shadows' cone: rays toward the sun spread over a cone of this radius, which is the
//! sun's apparent size, so a larger cone gives a wider penumbra. The game's own is 0.005 rad
//! (0.29 degrees) and 0.05 rad looked right live; it does nothing with ray tracing off.

use std::collections::BTreeMap;

/// What the Sun section asks for. Every field is `None` for the game's own value.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Sun {
    /// Degrees above the horizon.
    pub height: Option<f32>,
    /// Compass direction, degrees.
    pub heading: Option<f32>,
    /// Multiplier on the sun's intensity.
    pub strength: Option<f32>,
    /// Multiplier on the sky's (hemisphere) light.
    pub sky_light: Option<f32>,
    /// Multiplier on the tone map's exposure.
    pub exposure: Option<f32>,
    /// The sun's radius as ray-traced shadows see it, degrees.
    pub size: Option<f32>,
    /// Rays per pixel toward the sun; more keep a wide penumbra from going grainy.
    pub shadow_rays: Option<u32>,
}

/// A slider's range and the settings key it is saved under.
pub struct Range {
    pub key: &'static str,
    pub min: f32,
    pub max: f32,
}

pub const HEIGHT: Range = Range {
    key: "sun_height",
    min: 0.0,
    max: 90.0,
};
pub const HEADING: Range = Range {
    key: "sun_heading",
    min: -180.0,
    max: 180.0,
};
pub const STRENGTH: Range = Range {
    key: "sun_strength",
    min: 0.0,
    max: 4.0,
};
pub const SKY_LIGHT: Range = Range {
    key: "sky_light",
    min: 0.0,
    max: 4.0,
};
pub const EXPOSURE: Range = Range {
    key: "exposure",
    min: 0.25,
    max: 4.0,
};
pub const SIZE: Range = Range {
    key: "sun_size",
    min: 0.0,
    max: 10.0,
};
pub const SHADOW_RAYS: Range = Range {
    key: "shadow_rays",
    min: 1.0,
    max: 8.0,
};

/// The game's own ray-traced shadow cone, in degrees (0.005 rad).
pub const GAME_SIZE: f32 = 0.286_478_9;

impl Sun {
    /// Is anything asked for?
    #[must_use]
    pub fn asks_anything(&self) -> bool {
        *self != Self::default()
    }

    /// The float fields, with their ranges, for parsing and rendering.
    fn floats(&mut self) -> [(&'static Range, &mut Option<f32>); 6] {
        [
            (&HEIGHT, &mut self.height),
            (&HEADING, &mut self.heading),
            (&STRENGTH, &mut self.strength),
            (&SKY_LIGHT, &mut self.sky_light),
            (&EXPOSURE, &mut self.exposure),
            (&SIZE, &mut self.size),
        ]
    }

    /// Set the key a `[sky]` line names; `Ok(false)` when the key is not the Sun section's.
    pub fn set(&mut self, key: &str, value: &str) -> Result<bool, String> {
        if key == SHADOW_RAYS.key {
            let rays = value
                .parse::<u32>()
                .ok()
                .filter(|v| (1..=SHADOW_RAYS.max as u32).contains(v))
                .ok_or_else(|| {
                    format!(
                        "`shadow_rays` wants 1 to {}, not `{value}`",
                        SHADOW_RAYS.max
                    )
                })?;
            self.shadow_rays = Some(rays);
            return Ok(true);
        }
        for (range, field) in self.floats() {
            if key == range.key {
                let v = value
                    .parse::<f32>()
                    .ok()
                    .filter(|v| v.is_finite())
                    .ok_or_else(|| format!("`{key}` wants a number, not `{value}`"))?;
                *field = Some(v.clamp(range.min, range.max));
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// The Sun section's lines in `[sky]`.
    pub fn render(&self, out: &mut String) {
        out.push_str(
            "# The Sun section. Leave a line out for the game's own value. Height and heading are\n\
             # degrees; strength, sky_light and exposure multiply the game's; sun_size is the sun's\n\
             # radius in degrees as ray-traced shadows see it (the game's is 0.29), and shadow_rays\n\
             # the rays each pixel casts toward it (the game's is 1). Both need ray tracing on.\n",
        );
        let mut copy = *self;
        for (range, field) in copy.floats() {
            match field {
                Some(v) => out.push_str(&format!("{} = {v:?}\n", range.key)),
                None => out.push_str(&format!("# {} =\n", range.key)),
            }
        }
        match self.shadow_rays {
            Some(rays) => out.push_str(&format!("{} = {rays}\n", SHADOW_RAYS.key)),
            None => out.push_str(&format!("# {} =\n", SHADOW_RAYS.key)),
        }
    }
}

/// What the game had on the last frame the Sun section read it, before anything it wrote.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct SunSeen {
    /// A light set was found: the world's lighting is loaded.
    pub present: bool,
    pub height: f32,
    pub heading: f32,
    /// `DirLight0`'s intensity.
    pub strength: f32,
    pub exposure: f32,
    /// The ray-traced shadow cone, degrees.
    pub size: f32,
    pub shadow_rays: u32,
    /// The detour has run at least once.
    pub running: bool,
}

/// How a held field is written.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Want {
    /// This float, whatever the game had.
    Float(f32),
    /// The game's float times this.
    Scale(f32),
    /// This integer.
    Int(u32),
}

/// One field the section writes: what the game had there, and what the section last wrote.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Held {
    game: u32,
    written: u32,
}

/// Every field the section writes, so a scale never compounds and a field it stops writing gets
/// the game's value back.
///
/// The game's database may rewrite a field every frame or only when its blend changes, and this
/// does not need to know which: a field that still holds what was written last frame was left
/// alone, so the game's value is the one remembered; anything else is a new game value.
#[derive(Debug, Default)]
pub struct Holds {
    held: BTreeMap<usize, Held>,
    touched: Vec<usize>,
}

impl Holds {
    /// Start a frame.
    pub fn begin(&mut self) {
        self.touched.clear();
    }

    /// The game's value at `addr`, which now holds `now`.
    #[must_use]
    pub fn game_value(&self, addr: usize, now: u32) -> u32 {
        match self.held.get(&addr) {
            Some(held) if held.written == now => held.game,
            _ => now,
        }
    }

    /// The bits to write at `addr` for `want`, given that it now holds `now`.
    pub fn hold(&mut self, addr: usize, now: u32, want: Want) -> u32 {
        let game = self.game_value(addr, now);
        let written = match want {
            Want::Float(v) => v.to_bits(),
            Want::Scale(factor) => (f32::from_bits(game) * factor).to_bits(),
            Want::Int(v) => v,
        };
        self.held.insert(addr, Held { game, written });
        self.touched.push(addr);
        written
    }

    /// End a frame: every field held before and not this frame is let go, and each one that still
    /// holds what was written is returned with the game's value to put back. `read` gives a
    /// field's current bits, or `None` if it can no longer be read.
    pub fn end(&mut self, read: impl Fn(usize) -> Option<u32>) -> Vec<(usize, u32)> {
        let mut restore = Vec::new();
        let touched = std::mem::take(&mut self.touched);
        self.held.retain(|addr, held| {
            if touched.contains(addr) {
                return true;
            }
            if read(*addr) == Some(held.written) {
                restore.push((*addr, held.game));
            }
            false
        });
        self.touched = touched;
        restore
    }

    /// Is anything held?
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.held.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bits(v: f32) -> u32 {
        v.to_bits()
    }

    #[test]
    fn a_scale_never_compounds_whether_or_not_the_game_rewrites() {
        let mut holds = Holds::default();
        // The game leaves the field alone after the first frame.
        holds.begin();
        let first = holds.hold(0x10, bits(2.0), Want::Scale(1.5));
        assert_eq!(f32::from_bits(first), 3.0);
        holds.end(|_| None);
        holds.begin();
        let again = holds.hold(0x10, first, Want::Scale(1.5));
        assert_eq!(f32::from_bits(again), 3.0);
        holds.end(|_| None);
        // The game blends a new value in.
        holds.begin();
        let blended = holds.hold(0x10, bits(4.0), Want::Scale(1.5));
        assert_eq!(f32::from_bits(blended), 6.0);
        assert_eq!(holds.game_value(0x10, blended), bits(4.0));
    }

    #[test]
    fn letting_go_puts_back_the_game_value_only_over_its_own_write() {
        let mut holds = Holds::default();
        holds.begin();
        let a = holds.hold(0x10, bits(0.005), Want::Float(0.05));
        let b = holds.hold(0x20, 1, Want::Int(4));
        assert!(holds.end(|_| None).is_empty());
        // Next frame only 0x20 is still asked for.
        holds.begin();
        holds.hold(0x20, b, Want::Int(4));
        let restore = holds.end(|addr| (addr == 0x10).then_some(a));
        assert_eq!(restore, vec![(0x10, bits(0.005))]);
        // A field the game has since rewritten is left as the game wrote it.
        holds.begin();
        let restore = holds.end(|_| Some(99));
        assert!(restore.is_empty());
        assert!(holds.is_empty());
    }

    #[test]
    fn settings_round_trip_and_leave_unset_lines_out() {
        let sun = Sun {
            height: Some(30.0),
            heading: Some(-45.0),
            strength: None,
            sky_light: Some(1.5),
            exposure: Some(1.25),
            size: Some(2.9),
            shadow_rays: Some(4),
        };
        let mut text = String::new();
        sun.render(&mut text);
        let mut back = Sun::default();
        for line in text.lines() {
            let line = crate::settings::strip_comment(line).trim();
            if let Some((key, value)) = line.split_once('=') {
                assert_eq!(back.set(key.trim(), value.trim()), Ok(true), "{line}");
            }
        }
        assert_eq!(back, sun);
        assert!(!Sun::default().asks_anything());
    }

    #[test]
    fn a_bad_value_names_the_key_and_others_are_not_claimed() {
        let mut sun = Sun::default();
        assert!(sun.set("sun_size", "big").unwrap_err().contains("sun_size"));
        assert!(sun.set("shadow_rays", "0").is_err());
        assert_eq!(sun.set("weather", "\"rain\""), Ok(false));
        assert_eq!(sun.set("sun_height", "200"), Ok(true));
        assert_eq!(sun.height, Some(90.0));
    }
}
