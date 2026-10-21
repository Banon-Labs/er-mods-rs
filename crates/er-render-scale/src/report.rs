//! What the game reports back, and the telemetry file the worker writes once a second. Pure: the
//! Windows half reads the game and the hooks into these structs, and everything that turns them
//! into a verdict or a line of JSON is tested on the host.
//!
//! # The two readings that prove it
//!
//! * **The size the game drew.** The view setup writes the viewport it used as a ratio of the
//!   output into `GxDrawContext+0xed0`; times the output size (`+0x3c8`) that is the scene size in
//!   pixels ([`crate::scale::drawn_size`]). `exact` is that size equal to the output divided by the
//!   divisor in force, so a rounding the divisor check did not foresee shows up as `false` instead
//!   of passing silently.
//! * **The game's own GPU time**, in milliseconds, at `*(GxDrawContext+0xf30)+0xd344`: the number
//!   the dynamic resolution controller steps on.
//!
//! The file is `redirected_artifact_path("ER_QUICKLOAD_RENDER_SCALE_TELEMETRY_PATH",
//! "er-render-scale-telemetry.json")`, replaced whole each second; `scripts/check-render-scale-probe.py`
//! reads it.

use std::fmt::Write as _;

use er_overlay_input::InputCounters;
use er_overlay_theme::{Face, FrameRecord};

use crate::scale::{self, Refusal};

/// The telemetry file's name in the game directory, when no launcher redirects it.
pub const TELEMETRY_FILE_NAME: &str = "er-render-scale-telemetry.json";
/// The variable a launcher redirects it with, into its run's directory.
pub const TELEMETRY_PATH_ENV: &str = "ER_QUICKLOAD_RENDER_SCALE_TELEMETRY_PATH";
/// Bumped when a field changes meaning; `scripts/check-render-scale-probe.py` refuses another.
pub const SCHEMA: u32 = 1;

/// The dynamic resolution controller's config, as read.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct DrsFields {
    /// `+0x00`: the controller runs at all.
    pub enable: u8,
    /// `+0x01`: the load balancer may override the current scale.
    pub couple: u8,
    /// `+0x1c` and `+0x20`: the clamp the update holds the current scale in.
    pub min: f32,
    pub max: f32,
    /// `+0x30`: the scale the setter was last handed.
    pub current: f32,
    /// `GXRenderingSystem+0x13a78`: the gate on the mip LOD bias the view setup applies.
    pub lod_bias_gate: u8,
}

/// The values the game had when this DLL first wrote, which go back at native.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Originals {
    pub captured: bool,
    pub enable: u8,
    pub couple: u8,
    pub min: f32,
    pub max: f32,
    pub lod_bias_gate: u8,
}

/// The detour's state.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct HookState {
    pub installed: bool,
    /// Why it was not installed, when it was not.
    pub refusal: Option<String>,
    pub calls: u64,
    pub writes: u64,
    pub restores: u64,
    /// Calls whose config was not the controller's, left alone.
    pub foreign_config: u64,
    /// Our values are in place on the controller's config.
    pub active: bool,
    /// The divisor the last write put in force; 1 while native.
    pub applied: u32,
    pub originals: Originals,
}

/// What the game holds now.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Readings {
    /// `GxDrawContext+0x3c8` and `+0xefc`; `None` with no draw context.
    pub sizes: Option<([u32; 2], [u32; 2])>,
    /// `GxDrawContext+0xed0`.
    pub viewport_ratio: Option<[f32; 2]>,
    /// `*(GxDrawContext+0xf30)+0xd344`.
    pub gpu_ms: Option<f32>,
    pub drs: Option<DrsFields>,
}

/// What the readings say about the divisor in force.
#[derive(Clone, Debug, PartialEq)]
pub struct View {
    /// The output size a scale is taken of, or why none is offered.
    pub offered: Result<[u32; 2], Refusal>,
    /// The divisors the slider steps through: the clean ones for this output, or native alone.
    pub divisors: Vec<u32>,
    /// The divisor the settings ask for, and the one in force.
    pub selected: u32,
    pub applied: u32,
    /// The scene size the divisor in force should draw, and the size the game drew.
    pub expected: Option<[u32; 2]>,
    pub drawn: Option<[u32; 2]>,
    /// `drawn == expected`.
    pub exact: bool,
}

/// The view for `selected` given `readings`. `applied` is the divisor the hook last put in force;
/// the expected size follows it, because that is what the game was told.
#[must_use]
pub fn view(selected: u32, applied: u32, readings: &Readings) -> View {
    let offered = scale::offered_output(readings.sizes);
    let divisors = match offered {
        Ok(output) => scale::clean_divisors(output),
        Err(_) => vec![scale::NATIVE],
    };
    let output = readings.sizes.map(|(output, _)| output);
    let expected = output.map(|output| scale::scene_size(output, applied));
    let drawn = match (readings.viewport_ratio, output) {
        (Some(ratio), Some(output)) => scale::drawn_size(ratio, output),
        _ => None,
    };
    View {
        offered,
        divisors,
        selected,
        applied,
        expected,
        drawn,
        exact: drawn.is_some() && drawn == expected,
    }
}

/// The menu's part of the file.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct MenuState {
    pub open: bool,
    pub menu_key: String,
    pub input: InputCounters,
    pub record: Option<FrameRecord>,
    pub font_handles: [u32; Face::COUNT],
    pub fonts_requested: bool,
}

/// The settings file's part.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SettingsState {
    pub path: String,
    pub divisor: u32,
    pub saves: u64,
    pub save_errors: u64,
    pub reloads: u64,
    pub warnings: Vec<String>,
}

/// One telemetry document.
#[derive(Clone, Debug, PartialEq)]
pub struct Report {
    pub pid: u32,
    pub written_unix_ms: u64,
    pub serial: u64,
    pub game_build_ok: bool,
    pub hook: HookState,
    pub readings: Readings,
    pub view: View,
    pub menu: MenuState,
    pub settings: SettingsState,
}

/// A JSON string literal for `text`.
fn quoted(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if u32::from(c) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", u32::from(c));
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// A float as JSON: `null` for one that is not a number, which JSON cannot spell.
fn number(value: f32) -> String {
    if value.is_finite() {
        format!("{value}")
    } else {
        "null".to_owned()
    }
}

fn pair_u32(value: Option<[u32; 2]>) -> String {
    value.map_or_else(|| "null".to_owned(), |[a, b]| format!("[{a}, {b}]"))
}

fn pair_f32(value: Option<[f32; 2]>) -> String {
    value.map_or_else(
        || "null".to_owned(),
        |[a, b]| format!("[{}, {}]", number(a), number(b)),
    )
}

fn optional_text(value: Option<&str>) -> String {
    value.map_or_else(|| "null".to_owned(), quoted)
}

impl Report {
    /// The document as JSON, one key per line, keys in a fixed order so two files diff cleanly.
    #[must_use]
    pub fn render(&self) -> String {
        let mut out = String::new();
        let view = &self.view;
        let hook = &self.hook;
        let readings = &self.readings;
        let output = readings.sizes.map(|(output, _)| output);
        let scene_target = readings.sizes.map(|(_, target)| target);
        let offer_refusal = view.offered.err().map(|refusal| refusal.to_string());
        let scene_sizes: Vec<String> = match view.offered {
            Ok(size) => view
                .divisors
                .iter()
                .map(|&d| pair_u32(Some(scale::scene_size(size, d))))
                .collect(),
            Err(_) => Vec::new(),
        };
        let divisors: Vec<String> = view.divisors.iter().map(u32::to_string).collect();
        let _ = writeln!(out, "{{");
        let _ = writeln!(out, "  \"schema\": {SCHEMA},");
        let _ = writeln!(out, "  \"pid\": {},", self.pid);
        let _ = writeln!(out, "  \"written_unix_ms\": {},", self.written_unix_ms);
        let _ = writeln!(out, "  \"serial\": {},", self.serial);
        let _ = writeln!(out, "  \"game_build_ok\": {},", self.game_build_ok);
        let _ = writeln!(out, "  \"selected_divisor\": {},", view.selected);
        let _ = writeln!(out, "  \"applied_divisor\": {},", view.applied);
        let _ = writeln!(out, "  \"output\": {},", pair_u32(output));
        let _ = writeln!(out, "  \"scene_target\": {},", pair_u32(scene_target));
        let _ = writeln!(out, "  \"sizes_agree\": {},", view.offered.is_ok());
        let _ = writeln!(
            out,
            "  \"offer_refusal\": {},",
            optional_text(offer_refusal.as_deref())
        );
        let _ = writeln!(out, "  \"divisors\": [{}],", divisors.join(", "));
        let _ = writeln!(out, "  \"scene_sizes\": [{}],", scene_sizes.join(", "));
        let _ = writeln!(out, "  \"expected\": {},", pair_u32(view.expected));
        let _ = writeln!(out, "  \"drawn\": {},", pair_u32(view.drawn));
        let _ = writeln!(
            out,
            "  \"viewport_ratio\": {},",
            pair_f32(readings.viewport_ratio)
        );
        let _ = writeln!(out, "  \"exact\": {},", view.exact);
        let _ = writeln!(
            out,
            "  \"gpu_ms\": {},",
            readings.gpu_ms.map_or_else(|| "null".to_owned(), number)
        );
        let _ = writeln!(out, "  \"hook\": {{");
        let _ = writeln!(out, "    \"installed\": {},", hook.installed);
        let _ = writeln!(
            out,
            "    \"refusal\": {},",
            optional_text(hook.refusal.as_deref())
        );
        let _ = writeln!(out, "    \"calls\": {},", hook.calls);
        let _ = writeln!(out, "    \"writes\": {},", hook.writes);
        let _ = writeln!(out, "    \"restores\": {},", hook.restores);
        let _ = writeln!(out, "    \"foreign_config\": {},", hook.foreign_config);
        let _ = writeln!(out, "    \"active\": {}", hook.active);
        let _ = writeln!(out, "  }},");
        match readings.drs {
            Some(drs) => {
                let _ = writeln!(
                    out,
                    "  \"drs\": {{\"enable\": {}, \"couple\": {}, \"min\": {}, \"max\": {}, \
                     \"current\": {}, \"lod_bias_gate\": {}}},",
                    drs.enable,
                    drs.couple,
                    number(drs.min),
                    number(drs.max),
                    number(drs.current),
                    drs.lod_bias_gate
                );
            }
            None => {
                let _ = writeln!(out, "  \"drs\": null,");
            }
        }
        let original = hook.originals;
        let _ = writeln!(
            out,
            "  \"original\": {{\"captured\": {}, \"enable\": {}, \"couple\": {}, \"min\": {}, \
             \"max\": {}, \"lod_bias_gate\": {}}},",
            original.captured,
            original.enable,
            original.couple,
            number(original.min),
            number(original.max),
            original.lod_bias_gate
        );
        self.render_menu(&mut out);
        self.render_settings(&mut out);
        let _ = writeln!(out, "}}");
        out
    }

    fn render_menu(&self, out: &mut String) {
        let menu = &self.menu;
        let input = menu.input;
        let _ = writeln!(out, "  \"menu\": {{");
        let _ = writeln!(out, "    \"open\": {},", menu.open);
        let _ = writeln!(out, "    \"menu_key\": {},", quoted(&menu.menu_key));
        let _ = writeln!(
            out,
            "    \"input\": {{\"taking\": {}, \"cursor_hooked\": {}, \"cursor_forced\": {}, \
             \"keyboard_armed\": {}, \"mouse_armed\": {}, \"keys_blanked\": {}, \
             \"mouse_reads_blanked\": {}, \"pad_hooked\": {}, \"pad_zeroed\": {}}},",
            input.taking,
            input.cursor_hooked,
            input.cursor_forced,
            input.keyboard_armed,
            input.mouse_armed,
            input.keys_blanked,
            input.mouse_reads_blanked,
            input.pad_hooked,
            input.pad_zeroed
        );
        let handles: Vec<String> = menu.font_handles.iter().map(u32::to_string).collect();
        let (draws, scale, font_scale, in_use, granted, rects) = match &menu.record {
            Some(record) => {
                let rects: Vec<String> = record
                    .named_rects()
                    .map(|(name, rect)| {
                        let value = rect.map_or_else(
                            || "null".to_owned(),
                            |[x, y, w, h]| {
                                format!(
                                    "[{}, {}, {}, {}]",
                                    number(x),
                                    number(y),
                                    number(w),
                                    number(h)
                                )
                            },
                        );
                        format!("{}: {value}", quoted(name))
                    })
                    .collect();
                (
                    record.draws,
                    record.scale,
                    record.font_scale,
                    record.fonts.in_use,
                    record.fonts.granted,
                    rects,
                )
            }
            None => (0, 0.0, 0.0, false, [false; Face::COUNT], Vec::new()),
        };
        let granted: Vec<String> = granted.iter().map(bool::to_string).collect();
        let _ = writeln!(
            out,
            "    \"fonts\": {{\"requested\": {}, \"handles\": [{}], \"granted\": [{}], \"in_use\": {}}},",
            menu.fonts_requested,
            handles.join(", "),
            granted.join(", "),
            in_use
        );
        let _ = writeln!(out, "    \"draws\": {draws},");
        let _ = writeln!(out, "    \"scale\": {},", number(scale));
        let _ = writeln!(out, "    \"font_scale\": {},", number(font_scale));
        let _ = writeln!(out, "    \"rects\": {{{}}}", rects.join(", "));
        let _ = writeln!(out, "  }},");
    }

    fn render_settings(&self, out: &mut String) {
        let settings = &self.settings;
        let warnings: Vec<String> = settings.warnings.iter().map(|w| quoted(w)).collect();
        let _ = writeln!(out, "  \"settings\": {{");
        let _ = writeln!(out, "    \"path\": {},", quoted(&settings.path));
        let _ = writeln!(out, "    \"divisor\": {},", settings.divisor);
        let _ = writeln!(out, "    \"saves\": {},", settings.saves);
        let _ = writeln!(out, "    \"save_errors\": {},", settings.save_errors);
        let _ = writeln!(out, "    \"reloads\": {},", settings.reloads);
        let _ = writeln!(out, "    \"warnings\": [{}]", warnings.join(", "));
        let _ = writeln!(out, "  }}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const UHD: [u32; 2] = [3840, 2160];

    fn readings(ratio: [f32; 2]) -> Readings {
        Readings {
            sizes: Some((UHD, UHD)),
            viewport_ratio: Some(ratio),
            gpu_ms: Some(5.4),
            drs: Some(DrsFields {
                enable: 1,
                couple: 0,
                min: 1.0 / 3.0,
                max: 1.0 / 3.0,
                current: 1.0 / 3.0,
                lod_bias_gate: 0,
            }),
        }
    }

    #[test]
    fn a_third_drawn_at_a_third_is_exact() {
        let view = view(3, 3, &readings([1.0 / 3.0, 1.0 / 3.0]));
        assert_eq!(view.expected, Some([1280, 720]));
        assert_eq!(view.drawn, Some([1280, 720]));
        assert!(view.exact);
        assert_eq!(view.divisors.len(), 12);
    }

    #[test]
    fn a_ratio_the_snap_moved_is_not_exact() {
        // The live 1/24 run: 160x92 where 160x90 was asked for.
        let view = view(24, 24, &readings([0.041_67, 0.042_59]));
        assert_eq!(view.drawn, Some([160, 92]));
        assert_eq!(view.expected, Some([160, 90]));
        assert!(!view.exact);
    }

    #[test]
    fn native_expects_the_whole_output() {
        let view = view(1, 1, &readings([1.0, 1.0]));
        assert_eq!(view.expected, Some(UHD));
        assert!(view.exact);
        // Chosen but not yet applied: the game was told native, so native is what is expected.
        let pending = view_of(3, 1, [1.0, 1.0]);
        assert_eq!(pending.expected, Some(UHD));
        assert!(pending.exact);
        assert_eq!(pending.selected, 3);
    }

    fn view_of(selected: u32, applied: u32, ratio: [f32; 2]) -> View {
        view(selected, applied, &readings(ratio))
    }

    #[test]
    fn no_context_offers_native_only_and_draws_nothing() {
        let view = view(3, 1, &Readings::default());
        assert_eq!(view.offered, Err(Refusal::NoContext));
        assert_eq!(view.divisors, vec![1]);
        assert_eq!(view.drawn, None);
        assert!(!view.exact);
    }

    #[test]
    fn the_document_carries_every_key_the_probe_check_reads() {
        let readings = readings([1.0 / 3.0, 1.0 / 3.0]);
        let report = Report {
            pid: 372,
            written_unix_ms: 1,
            serial: 2,
            game_build_ok: true,
            hook: HookState {
                installed: true,
                applied: 3,
                active: true,
                refusal: Some("a \"quoted\"\nreason".to_owned()),
                ..HookState::default()
            },
            view: view(3, 3, &readings),
            readings,
            menu: MenuState {
                open: true,
                menu_key: "F5".to_owned(),
                ..MenuState::default()
            },
            settings: SettingsState {
                path: "C:\\Game\\er-render-scale.toml".to_owned(),
                divisor: 3,
                ..SettingsState::default()
            },
        };
        let text = report.render();
        for key in [
            "\"schema\": 1",
            "\"selected_divisor\": 3",
            "\"applied_divisor\": 3",
            "\"output\": [3840, 2160]",
            "\"sizes_agree\": true",
            "\"expected\": [1280, 720]",
            "\"drawn\": [1280, 720]",
            "\"exact\": true",
            "\"gpu_ms\": 5.4",
            "\"installed\": true",
            "\"open\": true",
            "\"taking\": false",
            "\"menu_key\": \"F5\"",
            "\"divisors\": [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60]",
            "\"refusal\": \"a \\\"quoted\\\"\\nreason\"",
            "\"path\": \"C:\\\\Game\\\\er-render-scale.toml\"",
        ] {
            assert!(text.contains(key), "missing {key} in\n{text}");
        }
        assert!(text.is_ascii());
        // Balanced braces and brackets: a crude check that it is one JSON object.
        let opens = text.matches('{').count() + text.matches('[').count();
        let closes = text.matches('}').count() + text.matches(']').count();
        assert_eq!(opens, closes);
    }

    #[test]
    fn a_float_json_cannot_spell_is_null() {
        assert_eq!(number(f32::NAN), "null");
        assert_eq!(number(f32::INFINITY), "null");
        assert_eq!(number(0.5), "0.5");
        assert_eq!(quoted("\u{1}"), "\"\\u0001\"");
    }
}
