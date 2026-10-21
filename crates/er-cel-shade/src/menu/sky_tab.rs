//! The Sky tab: the time of day and the weather, set and held through the game's own calls
//! (`crate::sky`).
//!
//! The time slider scrubs the clock while it is dragged; Lock time holds it where it is (or where
//! the slider puts it) through area loads and the scripts that would move it. Day length sets the
//! pace while the clock runs. The weather combo asks for a weather, which lasts until a script or
//! an area load changes it, and Lock weather asks again whenever either does. The Sun section
//! holds the sun's angle and strength, the sky light, the exposure and the ray-traced shadows'
//! softness over whatever the time and the weather blend (`crate::sky::light`). Save keeps the
//! locks and the Sun section in `er-cel-shade.toml`, so the next start holds the same sky.
//!
//! Set in the Look tab's language: sections 14 apart under quiet uppercase headings, the
//! Parameters mockup's slider rows, notes in the quiet colour.

use hudhook::imgui::Ui;

use super::process::Menu;
use super::theme::{self, ButtonKind, ButtonSize, SliderRow, gap, tooltip};
use crate::sky::{
    self, Clock, DAY_MINUTES, Install, MAX_DAY_MINUTES, MIN_DAY_MINUTES, NATURAL, Seen, Sun,
    SunSeen, VANILLA_SPEED, WEATHERS, Wanted, day_minutes_for_speed, sun, weather_by_id,
};

/// The room above a section heading, on top of the 10 between blocks.
const HEADING_SPACE: f32 = 4.0;
/// The value column at 1080p, wide enough for `180 min` in 12-pixel mono.
const VALUE_WIDTH: f32 = 52.0;
/// The label column at 1080p.
const LABEL_WIDTH: f32 = 78.0;
/// The longest day the slider offers, in real minutes; the settings file takes up to a day.
const SLIDER_MAX_DAY: f32 = 180.0;
/// The times the quick buttons jump to.
const QUICK_TIMES: [(&str, u32); 5] = [
    ("Dawn", 6 * 60),
    ("Noon", 12 * 60),
    ("Golden hour", 17 * 60 + 30),
    ("Dusk", 19 * 60),
    ("Midnight", 0),
];

fn section(ui: &Ui, words: &str) {
    gap(ui, HEADING_SPACE);
    theme::section_heading(ui, words);
}

fn row() -> SliderRow {
    SliderRow {
        label_width: LABEL_WIDTH,
        value_width: VALUE_WIDTH,
        ..SliderRow::PARAMETER
    }
}

/// The weather's name in the tab, or its row id when the table does not name it.
fn weather_name(id: i16) -> String {
    weather_by_id(id).map_or_else(|| format!("Weather row {id}"), |w| w.label.to_owned())
}

impl Menu {
    pub(super) fn draw_sky_tab(&mut self, ui: &Ui) {
        let _spacing = theme::spacing(ui, theme::CONTENT_GAP);
        let (mut wanted, seen, install, sun_seen, sun_install) = sky::with(|s| {
            (
                s.wanted.clone(),
                s.seen,
                s.install,
                s.sun_seen,
                s.sun_install,
            )
        });
        let before = wanted.clone();
        match install {
            Install::Running => {}
            Install::Waiting => theme::note(ui, "Waiting for the game's task scheduler."),
            Install::Refused(why) => {
                theme::note(ui, &format!("The Sky tab cannot change this game: {why}."));
            }
        }
        if !seen.in_world {
            theme::note(
                ui,
                "Load into the world to see the clock. Locks you set now are held from the moment \
                 it loads.",
            );
        }
        if seen.client {
            theme::note(
                ui,
                "You are in someone else's world: the host's time wins, and only the host can set \
                 it.",
            );
        }
        self.draw_time(ui, &mut wanted, &seen);
        self.draw_weather(ui, &mut wanted, &seen);
        draw_sun(ui, &mut wanted.sun, &sun_seen, sun_install);
        if wanted != before {
            self.live.sky.lock_time = wanted.lock_time;
            self.live.sky.time = wanted.time;
            self.live.sky.day_minutes = wanted.day_minutes;
            self.live.sky.weather = wanted.weather;
            self.live.sky.lock_weather = wanted.lock_weather;
            self.live.sky.sun = wanted.sun;
            sky::with(|s| s.wanted = wanted);
        }
    }

    fn draw_time(&mut self, ui: &Ui, wanted: &mut Wanted, seen: &Seen) {
        section(ui, "Time of day");
        let now = if seen.in_world {
            seen.clock
        } else {
            wanted.time
        };
        let state = if !seen.in_world {
            "not in the world"
        } else if seen.frozen {
            "held"
        } else if seen.flowing {
            "running"
        } else {
            "paused by the game"
        };
        theme::text(
            ui,
            theme::Font::Mono,
            theme::TEXT,
            &format!("{:02}:{:02}:{:02}", now.hour, now.minute, now.second),
        );
        ui.same_line();
        theme::quiet(ui, state);

        let mut minutes = now.minutes() as f32;
        let shown = Clock::from_minutes(now.minutes()).render();
        if row().draw(
            ui,
            "Time",
            &mut minutes,
            [0.0, (DAY_MINUTES - 1) as f32],
            0,
            &shown,
        ) {
            ask_time(wanted, Clock::from_minutes(minutes.round() as u32));
        }
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "Drag to move the sun; the game moves the light, the sky and the shadows with it.",
                ],
            );
        }

        let size = ButtonSize::SMALL;
        for (index, (label, at)) in QUICK_TIMES.iter().enumerate() {
            if index > 0 {
                ui.same_line();
            }
            if theme::button(
                ui,
                &format!("{label}##sky-time"),
                ButtonKind::Secondary,
                size,
            ) {
                ask_time(wanted, Clock::from_minutes(*at));
            }
            if ui.is_item_hovered() {
                tooltip(ui, &[Clock::from_minutes(*at).render()]);
            }
        }

        let mut lock = wanted.lock_time;
        if theme::checkbox(ui, "Lock time", &mut lock) {
            wanted.lock_time = lock;
            if lock {
                // Hold the time on screen, and ask for it again after every area load.
                ask_time(wanted, now);
            }
        }
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "Holds the clock where it is, through area loads and the scripts that move \
                     it. The time slider still moves it.",
                    "The flag is the one the game's own scripts freeze time with.",
                ],
            );
        }

        let running = wanted.day_minutes.is_some();
        let mut day = wanted
            .day_minutes
            .or_else(|| day_minutes_for_speed(seen.speed))
            .or_else(|| day_minutes_for_speed(VANILLA_SPEED))
            .unwrap_or(60.0)
            .clamp(MIN_DAY_MINUTES, SLIDER_MAX_DAY);
        let shown = format!("{day:.0} min");
        let moved = off_if(ui, wanted.lock_time, || {
            row().draw(
                ui,
                "Day length",
                &mut day,
                [MIN_DAY_MINUTES, SLIDER_MAX_DAY],
                0,
                &shown,
            )
        });
        if moved {
            wanted.day_minutes = Some(day.round().clamp(MIN_DAY_MINUTES, MAX_DAY_MINUTES));
        }
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "How many real minutes a whole day takes. The game's own day is an hour.",
                    "Lock time holds the clock whatever this says.",
                ],
            );
        }
        if running {
            if theme::button(ui, "Game's pace##sky", ButtonKind::Secondary, size) {
                wanted.day_minutes = None;
            }
            if ui.is_item_hovered() {
                tooltip(ui, &["Back to the game's own pace: a day an hour long."]);
            }
        }
    }

    fn draw_weather(&mut self, ui: &Ui, wanted: &mut Wanted, seen: &Seen) {
        section(ui, "Weather");
        if seen.has_weather {
            let mut now = weather_name(seen.weather);
            if seen.weather_settling {
                now.push_str(", arriving");
            }
            if seen.wetness > 0.0 {
                now.push_str(&format!(", ground {:.0}% wet", seen.wetness * 100.0));
            }
            theme::secondary(ui, &format!("Now: {now}"));
        }
        let preview = weather_name(wanted.weather);
        let width = ui.content_region_avail()[0];
        if let Some(_combo) = theme::combo(ui, "##sky-weather", &preview, width) {
            for weather in &WEATHERS {
                if ui
                    .selectable_config(weather.label)
                    .selected(weather.id == wanted.weather)
                    .build()
                {
                    wanted.weather = weather.id;
                    wanted.weather_serial = wanted.weather_serial.wrapping_add(1);
                }
            }
        }
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "Asks the game for this weather at once. It lasts until a script or an area \
                     load changes it, unless it is locked.",
                    "Natural ends a weather asked for here and lets the area pick again.",
                ],
            );
        }
        let mut lock = wanted.lock_weather;
        let lockable = wanted.weather != NATURAL;
        if off_if(ui, !lockable, || {
            theme::checkbox(ui, "Lock weather", &mut lock)
        }) && lockable
        {
            wanted.lock_weather = lock;
            if lock {
                wanted.weather_serial = wanted.weather_serial.wrapping_add(1);
            }
        }
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "Asks again whenever a script or an area load changes the weather, so it \
                     holds everywhere.",
                ],
            );
        }
    }
}

/// The Sun section: each slider shows the game's own value until it is moved, and from then on
/// holds its own over the lighting the game blends every frame.
fn draw_sun(ui: &Ui, sun: &mut Sun, seen: &SunSeen, install: Install) {
    section(ui, "Sun");
    match install {
        Install::Running => {}
        Install::Waiting => theme::note(ui, "Waiting for the game's lighting."),
        Install::Refused(why) => {
            theme::note(
                ui,
                &format!("The Sun section cannot change this game: {why}."),
            );
        }
    }
    if !seen.present {
        theme::quiet(ui, "The world's lighting is not loaded yet.");
    }
    let game = |held: Option<f32>| if held.is_some() { "" } else { " (game)" };

    let mut height = sun.height.unwrap_or(seen.height);
    let shown = format!("{height:.0} deg{}", game(sun.height));
    if row().draw(
        ui,
        "Height",
        &mut height,
        [sun::HEIGHT.min, sun::HEIGHT.max],
        0,
        &shown,
    ) {
        sun.height = Some(height);
    }
    tip(
        ui,
        "How high the sun stands above the horizon, or the moon at night.",
    );

    let mut heading = sun.heading.unwrap_or(seen.heading);
    let shown = format!("{heading:.0} deg{}", game(sun.heading));
    if row().draw(
        ui,
        "Heading",
        &mut heading,
        [sun::HEADING.min, sun::HEADING.max],
        0,
        &shown,
    ) {
        sun.heading = Some(heading);
    }
    tip(
        ui,
        "Which way the light comes from, and so which way the shadows fall.",
    );

    scale_row(
        ui,
        "Strength",
        &mut sun.strength,
        &sun::STRENGTH,
        "The sun's light, times the game's.",
    );
    scale_row(
        ui,
        "Sky light",
        &mut sun.sky_light,
        &sun::SKY_LIGHT,
        "The light from the whole sky, which reaches into the shadows, times the game's.",
    );
    scale_row(
        ui,
        "Exposure",
        &mut sun.exposure,
        &sun::EXPOSURE,
        "The picture's brightness, times the game's. Raises everything evenly, shadows included.",
    );

    section(ui, "Soft shadows");
    theme::quiet(
        ui,
        "Ray-traced shadows only: the game's ray tracing must be on.",
    );
    let mut size = sun.size.unwrap_or(if seen.size > 0.0 {
        seen.size
    } else {
        sun::GAME_SIZE
    });
    let shown = format!("{size:.2} deg{}", game(sun.size));
    if row().draw(
        ui,
        "Sun size",
        &mut size,
        [sun::SIZE.min, sun::SIZE.max],
        2,
        &shown,
    ) {
        sun.size = Some(size);
    }
    tip(
        ui,
        "The sun's radius as the shadows see it. The game's is 0.29; around 3 gives a soft edge \
         that widens with distance from what casts it.",
    );
    let mut rays = sun.shadow_rays.unwrap_or(seen.shadow_rays.max(1)) as i32;
    let shown = format!(
        "{rays}{}",
        if sun.shadow_rays.is_some() {
            ""
        } else {
            " (game)"
        }
    );
    let range = [sun::SHADOW_RAYS.min as i32, sun::SHADOW_RAYS.max as i32];
    if row().draw_int(ui, "Shadow rays", &mut rays, range, &shown) {
        sun.shadow_rays = Some(rays.max(1) as u32);
    }
    tip(
        ui,
        "Rays each pixel casts toward the sun. A large sun needs more to keep its edge smooth.",
    );

    if sun.asks_anything()
        && theme::button(
            ui,
            "Game's light##sun",
            ButtonKind::Secondary,
            ButtonSize::SMALL,
        )
    {
        *sun = Sun::default();
    }
}

/// A multiplier row: 1.0 and "(game)" until it is moved.
fn scale_row(ui: &Ui, label: &str, value: &mut Option<f32>, range: &sun::Range, words: &str) {
    let mut factor = value.unwrap_or(1.0);
    let shown = format!(
        "x{factor:.2}{}",
        if value.is_some() { "" } else { " (game)" }
    );
    if row().draw(ui, label, &mut factor, [range.min, range.max], 2, &shown) {
        *value = Some(factor);
    }
    tip(ui, words);
}

fn tip(ui: &Ui, words: &str) {
    if ui.is_item_hovered() {
        tooltip(ui, &[words]);
    }
}

/// Ask the game for `time`: once now, and again after every area load while the time is locked.
fn ask_time(wanted: &mut Wanted, time: Clock) {
    wanted.time = time;
    wanted.time_serial = wanted.time_serial.wrapping_add(1).max(1);
}

/// `draw` greyed out and closed to input when `off`, as the panel draws a control that cannot be
/// used right now ([`theme::unavailable`]).
fn off_if<R>(ui: &Ui, off: bool, draw: impl FnOnce() -> R) -> R {
    if off {
        theme::unavailable(ui, draw)
    } else {
        draw()
    }
}
