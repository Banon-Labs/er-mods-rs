//! The look: the knobs the in-game menu edits, the block of floats the shaders read, the built-in
//! presets, and the file the menu saves them to.
//!
//! Nothing here touches D3D12 or the game, so all of it is tested on the host.
//!
//! # One table drives everything
//!
//! [`KNOBS`] is in the order of the `cel_*` names `shaders/editor_abi.hlsli` defines onto the first
//! registers of the editor's parameter block, so the block the GPU reads is the knob values
//! followed by padding ([`Look::gpu_block`]). The menu draws one slider per entry and the file
//! writes one line per entry, so adding a knob is one row here, one `#define` in the `.hlsli`, and
//! the shader code that reads it.

use crate::replace::PortKind;

/// One knob: its key in the file, its label and help in the menu, and its range.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Knob {
    /// Key in `er-cel-shade.toml`.
    pub key: &'static str,
    /// Menu heading the knob is drawn under.
    pub group: &'static str,
    /// Slider label.
    pub label: &'static str,
    /// One line, shown as the slider's tooltip and as the comment above its line in the file.
    pub help: &'static str,
    pub min: f32,
    pub max: f32,
    /// Whole numbers only: a radius in samples, a number of levels.
    pub integer: bool,
    /// The value that leaves the game's own picture alone, or for a knob that only tunes another
    /// (an outline's width), a sensible starting point once the other is turned up.
    pub off: f32,
}

/// Every knob, in the order the shaders' `cel_*` names read them.
pub const KNOBS: [Knob; KNOB_COUNT] = [
    Knob {
        key: "light_band_stops",
        group: "Light",
        label: "Band width (stops)",
        help: "Lighting is rounded to bands this many exposure stops wide. 0 is smooth light.",
        min: 0.0,
        max: 4.0,
        integer: false,
        off: 0.0,
    },
    Knob {
        key: "outline_ink",
        group: "Outlines",
        label: "Ink",
        help: "How dark the outlines are: 0 draws none, 1 is black.",
        min: 0.0,
        max: 1.0,
        integer: false,
        off: 0.0,
    },
    Knob {
        key: "outline_width",
        group: "Outlines",
        label: "Width (px at 1080p)",
        help: "Outline thickness, scaled with the resolution so it looks the same at 4K.",
        min: 1.0,
        max: 4.0,
        integer: true,
        off: 1.0,
    },
    Knob {
        key: "outline_depth",
        group: "Outlines",
        label: "Silhouette threshold",
        help: "How big a jump in depth starts a silhouette line. Lower draws more lines.",
        min: 0.005,
        max: 0.2,
        integer: false,
        off: 0.04,
    },
    Knob {
        key: "outline_crease",
        group: "Outlines",
        label: "Crease threshold",
        help: "How sharp a fold must be to get a line: 0.3 is a gentle bend, 1.0 a right angle.",
        min: 0.05,
        max: 1.5,
        integer: false,
        off: 0.7,
    },
    Knob {
        key: "paint_radius",
        group: "Surfaces",
        label: "Paint radius (samples)",
        help: "Merges texture detail into flat patches with sharp edges. 0 keeps the textures. \
               Costly above 4.",
        min: 0.0,
        max: 6.0,
        integer: true,
        off: 0.0,
    },
    Knob {
        key: "colour_levels",
        group: "Surfaces",
        label: "Colour levels",
        help: "Levels per colour channel of every surface. 0 keeps every shade.",
        min: 0.0,
        max: 16.0,
        integer: true,
        off: 0.0,
    },
    Knob {
        key: "screen_levels",
        group: "Final picture",
        label: "Brightness steps",
        help: "Rounds the finished picture's brightness to this many steps; reaches hair, fur and \
               glowing things the lighting passes never see. 0 is off.",
        min: 0.0,
        max: 16.0,
        integer: true,
        off: 0.0,
    },
    Knob {
        key: "saturation",
        group: "Final picture",
        label: "Saturation",
        help: "Colour strength of the finished picture. 1 is the game's own.",
        min: 0.0,
        max: 2.0,
        integer: false,
        off: 1.0,
    },
];

/// Number of knobs.
pub const KNOB_COUNT: usize = 9;

/// Floats in the GPU block: the knobs padded to a whole number of 16-byte registers.
///
/// A literal, because `scripts/rva_symbols.py` evaluates constants to prove which addresses
/// nothing declares, and a method call is an expression it cannot evaluate. The assertion below
/// keeps it equal to `KNOB_COUNT` rounded up to four.
pub const GPU_FLOATS: usize = 12;
const _: () = assert!(GPU_FLOATS == KNOB_COUNT.div_ceil(4) * 4);

/// Index of each knob in [`KNOBS`], for code that needs one by name.
pub mod knob {
    pub const LIGHT_BAND_STOPS: usize = 0;
    pub const SATURATION: usize = 8;
}

/// One complete look, one value per [`KNOBS`] entry.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Look(pub [f32; KNOB_COUNT]);

impl Look {
    /// The game's own picture: every effect off, saturation unchanged.
    #[must_use]
    pub fn vanilla() -> Self {
        Self(KNOBS.map(|k| k.off))
    }

    /// Every value pulled into its knob's range and rounded where the knob is whole numbers. A
    /// file edited by hand can say anything; the shaders only ever see this.
    #[must_use]
    pub fn clamped(self) -> Self {
        let mut values = self.0;
        for (value, knob) in values.iter_mut().zip(KNOBS.iter()) {
            let mut v = if value.is_finite() { *value } else { knob.min };
            if knob.integer {
                v = v.round();
            }
            *value = v.clamp(knob.min, knob.max);
        }
        Self(values)
    }

    /// The constant buffer the shaders read. A disabled look is [`Look::vanilla`], so turning the
    /// effect off keeps the sliders where they were.
    #[must_use]
    pub fn gpu_block(self, enabled: bool) -> [f32; GPU_FLOATS] {
        let look = if enabled {
            self.clamped()
        } else {
            Self::vanilla()
        };
        let mut block = [0.0; GPU_FLOATS];
        block[..KNOB_COUNT].copy_from_slice(&look.0);
        block
    }
}

/// A named look.
#[derive(Clone, Debug, PartialEq)]
pub struct Preset {
    pub name: String,
    pub look: Look,
}

/// The presets that ship with the DLL. They cannot be overwritten or deleted from the menu.
///
/// "Wind Waker" is the look measured on screen on 2026-10-14 (run `br-20261014-210938-1960`):
/// the values `scripts/build-cel-shaders.py` compiled in before the look moved to the menu.
#[must_use]
pub fn builtin_presets() -> Vec<Preset> {
    let preset = |name: &str, values: [f32; KNOB_COUNT]| Preset {
        name: name.to_owned(),
        look: Look(values),
    };
    vec![
        preset("Wind Waker", [2.0, 0.9, 1.0, 0.04, 0.7, 3.0, 6.0, 6.0, 1.0]),
        preset(
            "Bright toon",
            [2.0, 0.85, 1.0, 0.04, 0.7, 3.0, 8.0, 0.0, 1.35],
        ),
        preset("Soft cel", [1.5, 0.6, 1.0, 0.05, 0.8, 2.0, 10.0, 0.0, 1.1]),
        preset("Comic ink", [2.5, 1.0, 2.0, 0.03, 0.5, 0.0, 0.0, 4.0, 1.0]),
        preset("Painterly", [0.0, 0.3, 1.0, 0.06, 0.9, 5.0, 0.0, 0.0, 1.15]),
        preset("Vanilla", Look::vanilla().0),
    ]
}

/// The preset used when there is no settings file.
pub const DEFAULT_PRESET: &str = "Wind Waker";

/// The default menu key. F3, F4, F7, F8, F9, F10, Insert and Delete are taken by other mods in
/// this repo (`er-invasion-warp`, `er-npc-possess`, `er-hotkey-conflicts`), and the game binds no
/// function key.
pub const DEFAULT_MENU_KEY: &str = "F6";

/// Everything the settings file holds.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    /// The key that opens and closes the menu, in `er-hotkey-config`'s spelling.
    pub menu_key: String,
    pub enabled: bool,
    /// The preset the look was last taken from. Informational: the look may have been edited
    /// since, and is saved in full under `[look]` either way.
    pub preset: String,
    pub look: Look,
    /// The player's own presets, after the built-in ones in the menu.
    pub presets: Vec<Preset>,
    /// The ports the fidelity test found unfaithful, one [`PortKind::bit`] each: they are bound as
    /// the game asked whatever the look says, until the menu or this file clears them (design 14,
    /// P0.1 item 5). Written in the file by [`PortKind::key`].
    pub unfaithful_ports: u8,
}

impl Default for Settings {
    fn default() -> Self {
        let look = builtin_presets()
            .into_iter()
            .find(|p| p.name == DEFAULT_PRESET)
            .map_or_else(Look::vanilla, |p| p.look);
        Self {
            menu_key: DEFAULT_MENU_KEY.to_owned(),
            enabled: true,
            preset: DEFAULT_PRESET.to_owned(),
            look,
            presets: Vec::new(),
            unfaithful_ports: 0,
        }
    }
}

/// The ports a comma list of [`PortKind::key`]s names, as [`PortKind::bit`]s. An empty list is
/// none.
pub fn parse_ports(list: &str) -> Result<u8, String> {
    let mut bits = 0;
    for word in list
        .split(',')
        .map(str::trim)
        .filter(|word| !word.is_empty())
    {
        let kind = PortKind::from_key(word).ok_or_else(|| {
            let known: Vec<&str> = PortKind::ALL.iter().map(|kind| kind.key()).collect();
            format!("`{word}` is no port; one of {}", known.join(", "))
        })?;
        bits |= kind.bit();
    }
    Ok(bits)
}

/// The comma list of [`PortKind::key`]s that `bits` names, in [`PortKind::ALL`] order.
#[must_use]
pub fn render_ports(bits: u8) -> String {
    PortKind::in_set(bits)
        .map(PortKind::key)
        .collect::<Vec<_>>()
        .join(",")
}

/// The settings file's name, beside the game executable.
pub const FILE_NAME: &str = "er-cel-shade.toml";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Section {
    Top,
    Look,
    Preset,
}

/// Parse a settings file. Never fails: a line it cannot read is skipped and named in the returned
/// warnings, and anything missing keeps its default, so a hand edit gone wrong costs that line
/// rather than the whole look.
#[must_use]
pub fn parse(text: &str) -> (Settings, Vec<String>) {
    let mut settings = Settings::default();
    let mut warnings = Vec::new();
    let mut section = Section::Top;
    for (number, raw) in text.lines().enumerate() {
        let line = strip_comment(raw).trim();
        if line.is_empty() {
            continue;
        }
        let at = number + 1;
        if line == "[look]" {
            section = Section::Look;
            continue;
        }
        if line == "[[preset]]" {
            section = Section::Preset;
            settings.presets.push(Preset {
                name: String::new(),
                look: settings.look,
            });
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            warnings.push(format!("line {at}: expected `key = value`"));
            continue;
        };
        let (key, value) = (key.trim(), value.trim());
        let result = match section {
            Section::Top => match key {
                "menu_key" => parse_string(value).map(|v| settings.menu_key = v),
                "enabled" => parse_bool(value).map(|v| settings.enabled = v),
                "preset" => parse_string(value).map(|v| settings.preset = v),
                "unfaithful_ports" => parse_string(value)
                    .and_then(|list| parse_ports(&list))
                    .map(|bits| settings.unfaithful_ports = bits),
                _ => Err(format!("unknown key `{key}`")),
            },
            Section::Look => set_knob(&mut settings.look, key, value),
            Section::Preset => match settings.presets.last_mut() {
                Some(preset) if key == "name" => parse_string(value).map(|v| preset.name = v),
                Some(preset) => set_knob(&mut preset.look, key, value),
                None => Err("no preset is open".to_owned()),
            },
        };
        if let Err(error) = result {
            warnings.push(format!("line {at}: {error}"));
        }
    }
    settings.look = settings.look.clamped();
    let before = settings.presets.len();
    settings
        .presets
        .retain(|p| !p.name.is_empty() && !is_builtin(&p.name));
    if settings.presets.len() != before {
        warnings.push("a [[preset]] with no name or a built-in name was dropped".to_owned());
    }
    for preset in &mut settings.presets {
        preset.look = preset.look.clamped();
    }
    (settings, warnings)
}

/// Is this the name of a built-in preset?
#[must_use]
pub fn is_builtin(name: &str) -> bool {
    builtin_presets().iter().any(|p| p.name == name)
}

fn set_knob(look: &mut Look, key: &str, value: &str) -> Result<(), String> {
    let index = KNOBS
        .iter()
        .position(|k| k.key == key)
        .ok_or_else(|| format!("unknown key `{key}`"))?;
    look.0[index] = value
        .parse::<f32>()
        .map_err(|_| format!("`{key}` wants a number, not `{value}`"))?;
    Ok(())
}

fn parse_bool(value: &str) -> Result<bool, String> {
    match value {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err(format!("wants true or false, not `{value}`")),
    }
}

fn parse_string(value: &str) -> Result<String, String> {
    let inner = value
        .strip_prefix('"')
        .and_then(|v| v.strip_suffix('"'))
        .ok_or_else(|| format!("wants a quoted string, not `{value}`"))?;
    let mut out = String::new();
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            match chars.next() {
                Some(escaped @ ('"' | '\\')) => out.push(escaped),
                _ => return Err("only \\\" and \\\\ escapes are understood".to_owned()),
            }
        } else {
            out.push(c);
        }
    }
    Ok(out)
}

/// The line up to a `#` that is not inside a quoted string.
fn strip_comment(line: &str) -> &str {
    let mut quoted = false;
    let mut escaped = false;
    for (at, c) in line.char_indices() {
        match c {
            _ if escaped => escaped = false,
            '\\' if quoted => escaped = true,
            '"' => quoted = !quoted,
            '#' if !quoted => return &line[..at],
            _ => {}
        }
    }
    line
}

fn quote(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        if matches!(c, '"' | '\\') {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}

fn render_look(out: &mut String, look: &Look, with_help: bool) {
    for (knob, value) in KNOBS.iter().zip(look.0.iter()) {
        if with_help {
            out.push_str(&format!("# {} ({} to {})\n", knob.help, knob.min, knob.max));
        }
        out.push_str(&format!("{} = {value:?}\n", knob.key));
    }
}

/// The settings file's text. The menu rewrites the whole file on every save, so the header says
/// that comments added by hand are not kept.
#[must_use]
pub fn render(settings: &Settings) -> String {
    let mut out = String::new();
    out.push_str(
        "# er-cel-shade settings. The in-game menu writes this file when you press Save, and\n\
         # rewrites all of it, so comments you add here are not kept. Values may also be edited\n\
         # here and loaded with the menu's Reload button.\n\n",
    );
    out.push_str("# The key that opens the menu: a name such as F6, Home or ctrl+F6.\n");
    out.push_str(&format!("menu_key = {}\n", quote(&settings.menu_key)));
    out.push_str("# false shows the game's own picture without unloading anything.\n");
    out.push_str(&format!("enabled = {}\n", settings.enabled));
    out.push_str("# The preset the look below was last taken from.\n");
    out.push_str(&format!("preset = {}\n", quote(&settings.preset)));
    out.push_str(
        "# Passes whose cel port drew differently from the game's own with cel at its off values,\n\
         # found by the fidelity test. They are drawn as the game draws them whatever the look\n\
         # says. Empty clears it; names are ao, ao_ssr, cmb_albedo, tonemap and tonemap_lut.\n",
    );
    out.push_str(&format!(
        "unfaithful_ports = {}\n\n",
        quote(&render_ports(settings.unfaithful_ports))
    ));
    out.push_str("[look]\n");
    render_look(&mut out, &settings.look, true);
    for preset in &settings.presets {
        out.push_str("\n[[preset]]\n");
        out.push_str(&format!("name = {}\n", quote(&preset.name)));
        render_look(&mut out, &preset.look, false);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_knob_count_matches_the_table() {
        assert_eq!(KNOBS.len(), KNOB_COUNT);
        assert_eq!(GPU_FLOATS % 4, 0);
        assert_eq!(KNOBS[knob::LIGHT_BAND_STOPS].key, "light_band_stops");
        assert_eq!(KNOBS[knob::SATURATION].key, "saturation");
    }

    #[test]
    fn the_abi_header_reads_each_knob_where_gpu_block_puts_it() {
        // `shaders/editor_abi.hlsli` is the other half of the GPU block's layout: one
        // `#define cel_<key> (er_p[register].component)` per knob. A knob added, moved or reordered
        // on one side only would make a shader read another knob's value.
        let hlsl = include_str!("../shaders/editor_abi.hlsli");
        let declared: Vec<(String, String)> = hlsl
            .lines()
            .filter_map(|l| l.trim().strip_prefix("#define cel_"))
            .filter_map(|l| l.split_once(char::is_whitespace))
            .map(|(key, place)| (key.to_owned(), place.trim().to_owned()))
            .collect();
        let expected: Vec<(String, String)> = KNOBS
            .iter()
            .enumerate()
            .map(|(index, knob)| {
                let component = ["x", "y", "z", "w"][index % 4];
                let place = format!("(er_p[{}].{component})", index / 4);
                (knob.key.to_owned(), place)
            })
            .collect();
        assert_eq!(declared, expected);
        // The cel registers begin the block, so a knob's place in `er_p` is its place in
        // `gpu_block` counted in registers.
        assert!(hlsl.lines().any(|l| l.trim() == "#define ER_P_CEL 0"));
    }

    #[test]
    fn cel_look_is_the_abi_header() {
        // The ports include `cel_look.hlsli` for the look; it must bring the ABI header's names and
        // declare nothing of its own, or a knob could exist in two places.
        let hlsl = include_str!("../shaders/cel_look.hlsli");
        let code: Vec<&str> = hlsl
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with("//"))
            .collect();
        assert_eq!(code, ["#include \"editor_abi.hlsli\""]);
    }

    #[test]
    fn every_preset_is_inside_its_ranges() {
        for preset in builtin_presets() {
            assert_eq!(preset.look, preset.look.clamped(), "{}", preset.name);
        }
    }

    #[test]
    fn disabled_uploads_the_vanilla_look() {
        let block = Look(builtin_presets()[0].look.0).gpu_block(false);
        assert_eq!(block[..KNOB_COUNT], Look::vanilla().0);
        assert_eq!(block[knob::SATURATION], 1.0);
        assert_eq!(block[knob::LIGHT_BAND_STOPS], 0.0);
    }

    #[test]
    fn a_saved_file_reads_back_as_it_was() {
        let mut look = Settings::default().look;
        look.0[1] = 0.5;
        let settings = Settings {
            menu_key: "ctrl+F6".to_owned(),
            enabled: false,
            preset: "My \"good\" one # really".to_owned(),
            look,
            presets: vec![Preset {
                name: "Night \\ ink".to_owned(),
                look: Look([1.0, 1.0, 2.0, 0.02, 0.4, 0.0, 4.0, 8.0, 1.5]),
            }],
            unfaithful_ports: PortKind::AoSsr.bit() | PortKind::TonemapLut.bit(),
        };
        let text = render(&settings);
        assert!(
            text.contains("\nunfaithful_ports = \"ao_ssr,tonemap_lut\"\n"),
            "{text}"
        );
        let (read, warnings) = parse(&text);
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(read, settings);
    }

    #[test]
    fn unfaithful_ports_are_named_by_their_keys() {
        assert_eq!(parse_ports(""), Ok(0));
        assert_eq!(parse_ports(" ao , tonemap ,"), Ok(1 | 8));
        assert_eq!(parse_ports("ao,ao"), Ok(1));
        assert!(parse_ports("ao,sky").is_err());
        assert_eq!(render_ports(0), "");
        assert_eq!(
            render_ports(PortKind::ALL_BITS),
            "ao,ao_ssr,cmb_albedo,tonemap,tonemap_lut"
        );
        for bits in 0..=PortKind::ALL_BITS {
            assert_eq!(parse_ports(&render_ports(bits)), Ok(bits), "{bits:#b}");
        }
        // A name the file cannot read costs its line, and the ports stay as they were.
        let (read, warnings) = parse("unfaithful_ports = \"ao,fog\"\nenabled = false\n");
        assert_eq!(read.unfaithful_ports, 0);
        assert!(!read.enabled);
        assert_eq!(warnings.len(), 1, "{warnings:?}");
        assert_eq!(parse("unfaithful_ports = \"\"\n").0.unfaithful_ports, 0);
    }

    #[test]
    fn a_bad_line_costs_that_line_only() {
        let text = "enabled = maybe\n[look]\noutline_ink = lots\nsaturation = 1.5\nnonsense\n";
        let (read, warnings) = parse(text);
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        assert!(read.enabled);
        assert_eq!(read.look.0[knob::SATURATION], 1.5);
        assert_eq!(read.look.0[1], Settings::default().look.0[1]);
    }

    #[test]
    fn out_of_range_and_fractional_values_are_pulled_in() {
        let (read, _) = parse("[look]\npaint_radius = 2.6\nsaturation = 9\noutline_ink = -1\n");
        assert_eq!(read.look.0[5], 3.0);
        assert_eq!(read.look.0[knob::SATURATION], 2.0);
        assert_eq!(read.look.0[1], 0.0);
    }

    #[test]
    fn a_file_cannot_shadow_a_built_in_preset() {
        let (read, warnings) = parse("[[preset]]\nname = \"Vanilla\"\nsaturation = 2\n");
        assert!(read.presets.is_empty());
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn an_empty_file_is_the_default() {
        assert_eq!(parse("").0, Settings::default());
    }
}
