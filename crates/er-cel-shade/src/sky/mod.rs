//! The Sky tab's model: the time of day and the weather the player asked for, what the game reports
//! back each frame, and how the game spells both.
//!
//! The menu writes [`Wanted`] and reads [`Seen`]; the game-thread task in [`game`] does the reverse,
//! and makes every change through the game's own calls from its own thread. Nothing here touches the
//! game, so all of it is tested on the host.
//!
//! # What the game keeps
//!
//! On 1.17.1, mapped statically in bd `er-time-of-day-worldareatime-1171-2026-10-15` and
//! `er-weather-worldareaweather-1171-2026-10-15` and proven live through Frida in
//! `er-sky-time-weather-frida-proven-1171-2026-10-15`. The clock is a packed date in `WorldAreaTime`; it runs at 24 game seconds per real second, so a
//! day lasts an hour. The weather is a `WeatherParam` row id in `WorldAreaWeather`. Both objects
//! are rebuilt on every area load, so the task re-applies a lock whenever either pointer changes.

#[cfg(windows)]
pub mod game;
#[cfg(windows)]
pub mod light;
pub mod sun;

use std::sync::{Mutex, MutexGuard};

pub use sun::{Sun, SunSeen};

/// A time of day, to the second.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Clock {
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
}

/// Minutes in a day.
pub const DAY_MINUTES: u32 = 24 * 60;

impl Clock {
    /// The time a `WorldAreaTime` packed date holds: hour in bits 34-38, minute in 39-44 and second
    /// in 45-50.
    #[must_use]
    pub const fn from_packed(packed: u64) -> Self {
        Self {
            hour: ((packed >> 34) & 0x1f) as u8,
            minute: ((packed >> 39) & 0x3f) as u8,
            second: ((packed >> 45) & 0x3f) as u8,
        }
    }

    /// Minutes since midnight.
    #[must_use]
    pub const fn minutes(self) -> u32 {
        self.hour as u32 * 60 + self.minute as u32
    }

    /// The time `minutes` after midnight, wrapped into one day.
    #[must_use]
    pub const fn from_minutes(minutes: u32) -> Self {
        let minutes = minutes % DAY_MINUTES;
        Self {
            hour: (minutes / 60) as u8,
            minute: (minutes % 60) as u8,
            second: 0,
        }
    }

    /// `HH:MM`, as the settings file and the tab write it.
    #[must_use]
    pub fn render(self) -> String {
        format!("{:02}:{:02}", self.hour, self.minute)
    }

    /// `HH:MM`, or `HH:MM:SS`.
    pub fn parse(text: &str) -> Result<Self, String> {
        let mut parts = text.split(':');
        let mut next = |limit: u8, what: &str| -> Result<u8, String> {
            let part = parts.next().unwrap_or("0");
            part.parse::<u8>()
                .ok()
                .filter(|v| *v < limit)
                .ok_or_else(|| format!("`{text}` has no {what} in `{part}`"))
        };
        let clock = Self {
            hour: next(24, "hour")?,
            minute: next(60, "minute")?,
            second: next(60, "second")?,
        };
        if parts.next().is_some() {
            return Err(format!("`{text}` is not HH:MM"));
        }
        Ok(clock)
    }
}

/// The game's own pace: 24 game seconds per real second, a day an hour long.
pub const VANILLA_SPEED: f32 = 24.0;
/// The shortest and longest days the tab offers, in real minutes.
pub const MIN_DAY_MINUTES: f32 = 1.0;
pub const MAX_DAY_MINUTES: f32 = 24.0 * 60.0;

/// The `WorldAreaTime` speed that makes a day last `minutes` real minutes.
#[must_use]
pub fn speed_for_day_minutes(minutes: f32) -> f32 {
    DAY_MINUTES as f32 / minutes.clamp(MIN_DAY_MINUTES, MAX_DAY_MINUTES)
}

/// How many real minutes a day lasts at `speed`, or `None` for a stopped clock.
#[must_use]
pub fn day_minutes_for_speed(speed: f32) -> Option<f32> {
    (speed > 0.0 && speed.is_finite()).then(|| DAY_MINUTES as f32 / speed)
}

/// One weather the game can be asked for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Weather {
    /// The `WeatherParam` row id `ChangeWeather` takes.
    pub id: i16,
    /// Its name in the settings file.
    pub key: &'static str,
    /// Its name in the tab.
    pub label: &'static str,
}

/// The weather the game picks for itself: asking for it ends an asked-for weather
/// (`ChangeWeather(-1)` restores the one it replaced, and the area's own rolls resume).
pub const NATURAL: i16 = -1;

/// Every weather the game has effects for, in the order the tab lists them, natural first. The ids
/// are the internal `WeatherParam` rows the EMEVD weather enum maps to (MapWeatherType
/// 0x140577410); rows 83-88 exist but carry no effects and 99 no script reaches, so they are left
/// out. Names follow Paramdex.
pub const WEATHERS: [Weather; 17] = [
    Weather {
        id: NATURAL,
        key: "natural",
        label: "Natural (the game picks)",
    },
    Weather {
        id: 0,
        key: "default",
        label: "Area default",
    },
    Weather {
        id: 1,
        key: "cloudless",
        label: "Cloudless",
    },
    Weather {
        id: 10,
        key: "flat_clouds",
        label: "Flat clouds",
    },
    Weather {
        id: 11,
        key: "puffy_clouds",
        label: "Puffy clouds",
    },
    Weather {
        id: 60,
        key: "windy_puffy_clouds",
        label: "Windy puffy clouds",
    },
    Weather {
        id: 21,
        key: "rainy_clouds",
        label: "Rainy clouds",
    },
    Weather {
        id: 82,
        key: "scattered_rain",
        label: "Scattered rain",
    },
    Weather {
        id: 20,
        key: "rain",
        label: "Rain",
    },
    Weather {
        id: 30,
        key: "windy_rain",
        label: "Windy rain",
    },
    Weather {
        id: 40,
        key: "snow",
        label: "Snow",
    },
    Weather {
        id: 41,
        key: "heavy_snow",
        label: "Heavy snow",
    },
    Weather {
        id: 50,
        key: "fog",
        label: "Fog",
    },
    Weather {
        id: 51,
        key: "heavy_fog",
        label: "Heavy fog",
    },
    Weather {
        id: 31,
        key: "windy_fog",
        label: "Windy fog",
    },
    Weather {
        id: 52,
        key: "rainy_heavy_fog",
        label: "Rainy heavy fog",
    },
    Weather {
        id: 81,
        key: "snowy_heavy_fog",
        label: "Snowy heavy fog",
    },
];

/// The weather with this id, if the table has it.
#[must_use]
pub fn weather_by_id(id: i16) -> Option<&'static Weather> {
    WEATHERS.iter().find(|w| w.id == id)
}

/// The weather with this settings-file name.
#[must_use]
pub fn weather_by_key(key: &str) -> Option<&'static Weather> {
    WEATHERS.iter().find(|w| w.key == key)
}

/// The Sky tab's part of `er-cel-shade.toml`, under `[sky]`: what a start applies before the
/// player touches anything. A time is applied at start only when it is locked, and a weather only
/// when it is locked, so a saved file never changes a world the player left alone. The Sun
/// section's lines are written only once set, so each one present is applied.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SkySettings {
    pub lock_time: bool,
    pub time: Clock,
    pub day_minutes: Option<f32>,
    pub weather: i16,
    pub lock_weather: bool,
    pub sun: Sun,
}

impl Default for SkySettings {
    fn default() -> Self {
        let wanted = Wanted::default();
        Self {
            lock_time: wanted.lock_time,
            time: wanted.time,
            day_minutes: wanted.day_minutes,
            weather: wanted.weather,
            lock_weather: wanted.lock_weather,
            sun: wanted.sun,
        }
    }
}

impl SkySettings {
    /// Set the key a `[sky]` line names.
    pub fn set(&mut self, key: &str, value: &str) -> Result<(), String> {
        use crate::settings::{parse_bool, parse_string};
        if self.sun.set(key, value)? {
            return Ok(());
        }
        match key {
            "lock_time" => parse_bool(value).map(|v| self.lock_time = v),
            "time" => parse_string(value)
                .and_then(|text| Clock::parse(&text))
                .map(|v| self.time = v),
            "day_minutes" => value
                .parse::<f32>()
                .ok()
                .filter(|v| v.is_finite())
                .map(|v| self.day_minutes = Some(v.clamp(MIN_DAY_MINUTES, MAX_DAY_MINUTES)))
                .ok_or_else(|| format!("`day_minutes` wants a number of minutes, not `{value}`")),
            "weather" => parse_string(value).and_then(|name| {
                let weather = weather_by_key(&name).ok_or_else(|| {
                    let known: Vec<&str> = WEATHERS.iter().map(|w| w.key).collect();
                    format!("`{name}` is no weather; one of {}", known.join(", "))
                })?;
                self.weather = weather.id;
                Ok(())
            }),
            "lock_weather" => parse_bool(value).map(|v| self.lock_weather = v),
            _ => Err(format!("unknown key `{key}`")),
        }
    }

    /// The `[sky]` section's text.
    pub fn render(&self, out: &mut String) {
        use crate::settings::quote;
        out.push_str("[sky]\n");
        out.push_str("# Hold the clock at `time` (HH:MM), from the moment the world loads.\n");
        out.push_str(&format!("lock_time = {}\n", self.lock_time));
        out.push_str(&format!("time = {}\n", quote(&self.time.render())));
        out.push_str(&format!(
            "# How many real minutes a day lasts; the game's own is 60. Leave it out for the \
             game's pace\n# ({MIN_DAY_MINUTES} to {MAX_DAY_MINUTES}).\n"
        ));
        match self.day_minutes {
            Some(minutes) => out.push_str(&format!("day_minutes = {minutes:?}\n")),
            None => out.push_str("# day_minutes = 60.0\n"),
        }
        let names: Vec<&str> = WEATHERS.iter().map(|w| w.key).collect();
        out.push_str(&format!(
            "# Hold this weather from the moment the world loads: one of\n# {}.\n",
            names.join(", ")
        ));
        let weather = weather_by_id(self.weather).map_or("natural", |w| w.key);
        out.push_str(&format!("weather = {}\n", quote(weather)));
        out.push_str(&format!("lock_weather = {}\n", self.lock_weather));
        self.sun.render(out);
    }

    /// What a start asks of the game: the locked time and the locked weather, and the pace.
    #[must_use]
    pub fn wanted_at_start(&self) -> Wanted {
        let weather = if self.lock_weather {
            self.weather
        } else {
            NATURAL
        };
        Wanted {
            lock_time: self.lock_time,
            time: self.time,
            time_serial: u32::from(self.lock_time),
            day_minutes: self.day_minutes,
            weather,
            weather_serial: u32::from(weather != NATURAL),
            lock_weather: self.lock_weather,
            sun: self.sun,
        }
    }
}

/// What the tab asks for once a reloaded or reverted `[sky]` replaces `old`: the file's locks, with
/// a locked time asked for again, and a weather asked for again whenever the file's differs from
/// the one asked for before (which is how a weather the file does not lock gets ended).
#[must_use]
pub fn adopted(old: &Wanted, sky: &SkySettings) -> Wanted {
    let mut wanted = sky.wanted_at_start();
    wanted.time_serial = old.time_serial.wrapping_add(wanted.time_serial);
    let ask_weather = wanted.weather_serial != 0 || wanted.weather != old.weather;
    wanted.weather_serial = old.weather_serial.wrapping_add(u32::from(ask_weather));
    wanted
}

/// Make a reloaded or reverted `[sky]` what the tab asks for ([`adopted`]).
pub fn adopt(sky: &SkySettings) {
    with(|s| s.wanted = adopted(&s.wanted, sky));
}

/// What the Sky tab asks for. The menu owns it; the task reads a copy each frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Wanted {
    /// Hold the clock still. Set through the same flag EMEVD's "freeze time" sets, and only cleared
    /// again by the task if it was the task that set it.
    pub lock_time: bool,
    /// The time last asked for.
    pub time: Clock,
    /// Bumped every time a time is asked for; the task asks the game once per value.
    pub time_serial: u32,
    /// How long a day lasts, in real minutes. `None` leaves the game's own pace alone.
    pub day_minutes: Option<f32>,
    /// The weather asked for, or [`NATURAL`].
    pub weather: i16,
    /// Bumped every time a weather is asked for.
    pub weather_serial: u32,
    /// Ask again whenever the weather drifts from [`Self::weather`] or the area reloads.
    pub lock_weather: bool,
    /// The Sun section's overrides, written over the blended lighting every frame.
    pub sun: Sun,
}

impl Default for Wanted {
    fn default() -> Self {
        Self {
            lock_time: false,
            time: Clock {
                hour: 12,
                minute: 0,
                second: 0,
            },
            time_serial: 0,
            day_minutes: None,
            weather: NATURAL,
            weather_serial: 0,
            lock_weather: false,
            sun: Sun::default(),
        }
    }
}

impl Wanted {
    /// Is anything asked of the game at all? A start with nothing asked makes no call.
    #[must_use]
    pub fn asks_anything(&self) -> bool {
        self.lock_time
            || self.day_minutes.is_some()
            || self.weather != NATURAL
            || self.sun.asks_anything()
    }
}

/// What the game reported on the last frame the task ran.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Seen {
    /// A `WorldAreaTime` exists: a character is in the world.
    pub in_world: bool,
    pub clock: Clock,
    /// The freeze flag is set, by the tab or by the game.
    pub frozen: bool,
    /// The clock moved on the last update (it stops for cutscenes, menus and warps too).
    pub flowing: bool,
    /// Game seconds per real second.
    pub speed: f32,
    /// A `WorldAreaWeather` exists.
    pub has_weather: bool,
    pub weather: i16,
    /// A change is still being applied (assets and the weather's lighting loading).
    pub weather_settling: bool,
    /// The weather came from a script or from the tab rather than the area's own rolls.
    pub weather_event: bool,
    /// How wet the world is, 0 to 1.
    pub wetness: f32,
    /// This session cannot set the time: a co-op client takes the host's.
    pub client: bool,
}

/// How far the task got installing itself, for the tab's status line.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Install {
    #[default]
    Waiting,
    Running,
    /// Not 1.17.1, or the game's bytes are not the ones checked: nothing is called.
    Refused(&'static str),
}

/// Everything shared between the menu and the task.
#[derive(Debug, Default)]
pub struct Shared {
    pub wanted: Wanted,
    pub seen: Seen,
    pub install: Install,
    /// The Sun section's half, from the lighting detour rather than the frame task.
    pub sun_seen: SunSeen,
    pub sun_install: Install,
}

static SHARED: Mutex<Option<Shared>> = Mutex::new(None);

/// The shared state, made on first use. A panic while it was held leaves it usable: every field
/// is plain data, valid whichever write the panic interrupted.
pub fn shared() -> MutexGuard<'static, Option<Shared>> {
    SHARED
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Run `f` on the shared state.
pub fn with<R>(f: impl FnOnce(&mut Shared) -> R) -> R {
    let mut guard = shared();
    f(guard.get_or_insert_with(Shared::default))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packed_clock_reads_the_bits_the_game_writes() {
        let packed = (19_u64 << 34) | (30_u64 << 39) | (5_u64 << 45) | 0x3ff_u64;
        assert_eq!(
            Clock::from_packed(packed),
            Clock {
                hour: 19,
                minute: 30,
                second: 5
            }
        );
    }

    #[test]
    fn minutes_round_trip_and_wrap() {
        let clock = Clock::from_minutes(19 * 60 + 30);
        assert_eq!(clock.render(), "19:30");
        assert_eq!(clock.minutes(), 19 * 60 + 30);
        assert_eq!(Clock::from_minutes(DAY_MINUTES + 5).render(), "00:05");
    }

    #[test]
    fn parse_takes_minutes_or_seconds_and_refuses_nonsense() {
        assert_eq!(
            Clock::parse("07:05").map(Clock::render),
            Ok("07:05".to_owned())
        );
        assert_eq!(
            Clock::parse("23:59:59"),
            Ok(Clock {
                hour: 23,
                minute: 59,
                second: 59
            })
        );
        assert!(Clock::parse("24:00").is_err());
        assert!(Clock::parse("12:60").is_err());
        assert!(Clock::parse("12:00:00:00").is_err());
        assert!(Clock::parse("noon").is_err());
    }

    #[test]
    fn vanilla_pace_is_an_hour_long_day() {
        assert!((speed_for_day_minutes(60.0) - VANILLA_SPEED).abs() < 1e-4);
        assert_eq!(day_minutes_for_speed(VANILLA_SPEED), Some(60.0));
        assert_eq!(day_minutes_for_speed(0.0), None);
        assert!((speed_for_day_minutes(0.0) - 1440.0).abs() < 1e-3);
    }

    #[test]
    fn weather_table_has_unique_ids_and_keys() {
        for (i, a) in WEATHERS.iter().enumerate() {
            for b in &WEATHERS[i + 1..] {
                assert_ne!(a.id, b.id);
                assert_ne!(a.key, b.key);
            }
        }
        assert_eq!(weather_by_key("rain").map(|w| w.id), Some(20));
        assert_eq!(weather_by_id(NATURAL).map(|w| w.key), Some("natural"));
    }

    #[test]
    fn nothing_is_asked_by_default() {
        assert!(!Wanted::default().asks_anything());
        assert!(!SkySettings::default().wanted_at_start().asks_anything());
    }

    #[test]
    fn settings_round_trip_through_their_text() {
        let sky = SkySettings {
            lock_time: true,
            time: Clock {
                hour: 19,
                minute: 30,
                second: 0,
            },
            day_minutes: Some(12.5),
            weather: 20,
            lock_weather: true,
            sun: Sun {
                size: Some(2.9),
                shadow_rays: Some(4),
                ..Sun::default()
            },
        };
        let mut text = String::new();
        sky.render(&mut text);
        let mut back = SkySettings::default();
        for line in text.lines().skip(1) {
            let line = crate::settings::strip_comment(line).trim();
            if let Some((key, value)) = line.split_once('=') {
                back.set(key.trim(), value.trim()).expect(line);
            }
        }
        assert_eq!(back, sky);
    }

    #[test]
    fn a_start_applies_only_what_is_locked() {
        let mut sky = SkySettings {
            weather: 40,
            ..SkySettings::default()
        };
        assert_eq!(sky.wanted_at_start().weather, NATURAL);
        assert_eq!(sky.wanted_at_start().weather_serial, 0);
        sky.lock_weather = true;
        sky.lock_time = true;
        let wanted = sky.wanted_at_start();
        assert_eq!((wanted.weather, wanted.weather_serial), (40, 1));
        assert_eq!(wanted.time_serial, 1);
    }

    #[test]
    fn a_reload_asks_again_only_for_what_changes_or_is_locked() {
        let old = Wanted {
            time_serial: 7,
            weather: 20,
            weather_serial: 3,
            ..Wanted::default()
        };
        // An unlocked file: the clock is left alone and the rain asked for here is ended.
        let plain = adopted(&old, &SkySettings::default());
        assert_eq!(plain.time_serial, 7);
        assert_eq!((plain.weather, plain.weather_serial), (NATURAL, 4));
        // A locked file: both are asked for again.
        let locked = SkySettings {
            lock_time: true,
            lock_weather: true,
            weather: 20,
            ..SkySettings::default()
        };
        let again = adopted(&old, &locked);
        assert_eq!(again.time_serial, 8);
        assert_eq!((again.weather, again.weather_serial), (20, 4));
        // Natural before and after: nothing to ask.
        let calm = adopted(&Wanted::default(), &SkySettings::default());
        assert_eq!((calm.time_serial, calm.weather_serial), (0, 0));
    }

    #[test]
    fn a_bad_line_names_the_problem() {
        let mut sky = SkySettings::default();
        assert!(sky.set("weather", "\"hail\"").unwrap_err().contains("hail"));
        assert!(sky.set("time", "\"25:00\"").is_err());
        assert!(sky.set("day_minutes", "soon").is_err());
        assert!(sky.set("sun", "1").is_err());
    }
}
