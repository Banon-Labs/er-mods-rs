//! The project: which layers exist, in which order, at which stage, and how each is mixed in
//! (design 10). It is `er-shader-editor/project.ershade` beside the game; each layer's code is its
//! own file under `er-shader-editor/layers/`, which stays the single source of truth for that
//! code whether it was edited in game or in a Linux editor.
//!
//! ```text
//! format = 1
//! name = "Warm dusk"
//! game = "1.17.1"
//! [look]                       # optional; the keys of er-cel-shade.toml [look]
//! light_band_stops = 2.0
//! [[layer]]
//! id = 3
//! name = "Warm grade"
//! stage = "tonemap_post"
//! enabled = true
//! solo = false
//! opacity = 0.8
//! blend = "normal"
//! select = "everything"
//! file = "3-warm-grade.hlsl"
//! param.Strength = 0.6
//! param.Shadows = [0.0, 0.02, 0.05]
//! ```
//!
//! The parser is `settings.rs`'s: quoted strings, `#` comments, a warning for each line it cannot
//! read, and never a failure for the whole file, so a hand edit gone wrong costs that line. A
//! preset's embedded shader sections (`--- shader <file> sha256=<hex> ---` to `--- end ---`) are
//! the preset importer's to read (P1); a project skips them with one warning each.
//!
//! Layers are applied in the order the file lists them, each stage's in turn.

use super::LayerStage;
use crate::settings::{KNOBS, Look, parse_bool, parse_string, quote, strip_comment};

/// The directory beside the game executable that holds everything the editor reads and writes.
pub const DIRECTORY: &str = "er-shader-editor";
/// The project file, in [`DIRECTORY`].
pub const FILE_NAME: &str = "project.ershade";
/// Layer files, in [`DIRECTORY`].
pub const LAYERS_DIRECTORY: &str = "layers";
/// The last diagnostics of each layer, `diag/<id>.txt`, in [`DIRECTORY`].
pub const DIAG_DIRECTORY: &str = "diag";
/// The format this version writes and reads.
pub const FORMAT: u32 = 1;

/// How a layer's result is mixed into the colour below it, numbered as `stage_ctx.hlsli`'s
/// `ER_BLEND_*`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Blend {
    Normal = 0,
    Add = 1,
    Multiply = 2,
    Screen = 3,
}

impl Blend {
    pub const ALL: [Self; 4] = [Self::Normal, Self::Add, Self::Multiply, Self::Screen];

    #[must_use]
    pub const fn key(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::Add => "add",
            Self::Multiply => "multiply",
            Self::Screen => "screen",
        }
    }

    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Normal => "Normal",
            Self::Add => "Add",
            Self::Multiply => "Multiply",
            Self::Screen => "Screen",
        }
    }

    #[must_use]
    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|blend| blend.key() == key)
    }
}

/// A parameter's value as the project file holds it. The layer's annotations decide what it
/// means ([`super::param_values`]).
#[derive(Clone, Debug, PartialEq)]
pub enum ParamValue {
    Bool(bool),
    Number(f64),
    Vector(Vec<f64>),
}

impl ParamValue {
    fn render(&self) -> String {
        match self {
            Self::Bool(b) => b.to_string(),
            Self::Number(n) => format!("{n:?}"),
            Self::Vector(v) => {
                let parts: Vec<String> = v.iter().map(|n| format!("{n:?}")).collect();
                format!("[{}]", parts.join(", "))
            }
        }
    }

    fn parse(value: &str) -> Result<Self, String> {
        if let Ok(b) = parse_bool(value) {
            return Ok(Self::Bool(b));
        }
        let number = |text: &str| {
            text.trim()
                .parse::<f64>()
                .ok()
                .filter(|n| n.is_finite())
                .ok_or_else(|| format!("`{}` is not a number", text.trim()))
        };
        if let Some(inner) = value.strip_prefix('[').and_then(|v| v.strip_suffix(']')) {
            let numbers: Result<Vec<f64>, String> = inner.split(',').map(number).collect();
            return numbers.map(Self::Vector);
        }
        number(value).map(Self::Number)
    }
}

/// One layer of the stack.
#[derive(Clone, Debug, PartialEq)]
pub struct Layer {
    /// Unique in the project, above 0. The generated code's namespace is `L<id>`.
    pub id: u32,
    pub name: String,
    /// The layer's file in `layers/`: a plain file name, never a path.
    pub file: String,
    pub stage: LayerStage,
    /// Off is a parameter write, never a recompile: the layer stays compiled in.
    pub enabled: bool,
    /// While any enabled layer is soloed, only soloed layers draw.
    pub solo: bool,
    /// 0 to 1.
    pub opacity: f32,
    pub blend: Blend,
    /// The selector text (design 8); `editor::mask::parse` reads its screen mask.
    pub select: String,
    /// Values set from the panel, in the order the file lists them. A parameter with no entry
    /// here takes its annotated default.
    pub params: Vec<(String, ParamValue)>,
}

impl Layer {
    /// A new enabled layer with every other setting at its default.
    #[must_use]
    pub fn new(id: u32, name: &str, file: &str, stage: LayerStage) -> Self {
        Self {
            id,
            name: name.to_owned(),
            file: file.to_owned(),
            stage,
            enabled: true,
            solo: false,
            opacity: 1.0,
            blend: Blend::Normal,
            select: "everything".to_owned(),
            params: Vec::new(),
        }
    }

    #[must_use]
    pub fn param(&self, name: &str) -> Option<&ParamValue> {
        self.params
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value)
    }

    pub fn set_param(&mut self, name: &str, value: ParamValue) {
        match self.params.iter_mut().find(|(key, _)| key == name) {
            Some((_, slot)) => *slot = value,
            None => self.params.push((name.to_owned(), value)),
        }
    }
}

/// The whole project file.
#[derive(Clone, Debug, PartialEq)]
pub struct Project {
    pub format: u32,
    pub name: String,
    /// The game version the project was made on, informational.
    pub game: String,
    /// The cel look a preset carries. A project leaves it out: the look lives in
    /// `er-cel-shade.toml`.
    pub look: Option<Look>,
    /// In stack order.
    pub layers: Vec<Layer>,
}

impl Default for Project {
    fn default() -> Self {
        Self {
            format: FORMAT,
            name: String::new(),
            game: String::new(),
            look: None,
            layers: Vec::new(),
        }
    }
}

impl Project {
    /// An id no layer has.
    #[must_use]
    pub fn next_id(&self) -> u32 {
        self.layers.iter().map(|l| l.id).max().unwrap_or(0) + 1
    }

    #[must_use]
    pub fn layer(&self, id: u32) -> Option<&Layer> {
        self.layers.iter().find(|l| l.id == id)
    }

    #[must_use]
    pub fn layer_mut(&mut self, id: u32) -> Option<&mut Layer> {
        self.layers.iter_mut().find(|l| l.id == id)
    }

    /// Does `layer` draw: enabled, and soloed or no enabled layer soloed.
    #[must_use]
    pub fn is_on(&self, layer: &Layer) -> bool {
        let soloing = self.layers.iter().any(|l| l.enabled && l.solo);
        layer.enabled && (layer.solo || !soloing)
    }

    /// The port stages with a layer that draws, as [`crate::replace::Stage::bit`]s: the layers'
    /// share of `cmdlist::STAGE_ACTIVE`.
    #[must_use]
    pub fn active_stages(&self) -> u32 {
        self.layers
            .iter()
            .filter(|layer| self.is_on(layer))
            .fold(0, |bits, layer| bits | layer.stage.port_stage().bit())
    }
}

/// Is `name` a plain layer file name: no directory, no parent, a `.hlsl` file? A project names
/// only files in `layers/`, so a hand edit cannot point the editor anywhere else.
#[must_use]
pub fn valid_file_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('.')
        && !name.contains(['/', '\\', ':'])
        && name.ends_with(".hlsl")
        && name.chars().all(|c| !c.is_control())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Section {
    Top,
    Look,
    Layer,
}

/// A `[[layer]]` while it is read: its id may be missing until the file ends.
struct Reading {
    line: usize,
    id: Option<u32>,
    layer: Layer,
}

/// Parse a project file. Never fails: a line it cannot read is skipped and named in the returned
/// warnings, a layer with no file is dropped, and a layer with no id, or one another layer has,
/// is given a free one.
#[must_use]
pub fn parse(text: &str) -> (Project, Vec<String>) {
    let mut project = Project::default();
    let mut warnings = Vec::new();
    let mut section = Section::Top;
    let mut reading: Vec<Reading> = Vec::new();
    let mut look = Look::vanilla();
    let mut look_seen = false;
    let mut in_shader: Option<usize> = None;
    for (number, raw) in text.lines().enumerate() {
        let at = number + 1;
        let trimmed = raw.trim();
        if let Some(start) = in_shader {
            if trimmed == "--- end ---" {
                warnings.push(format!(
                    "lines {start}-{at}: an embedded shader is read when a preset is imported, \
                     not from the project; skipped"
                ));
                in_shader = None;
            }
            continue;
        }
        if trimmed.starts_with("--- shader ") {
            in_shader = Some(at);
            continue;
        }
        let line = strip_comment(raw).trim();
        if line.is_empty() {
            continue;
        }
        if line == "[look]" {
            section = Section::Look;
            look_seen = true;
            continue;
        }
        if line == "[[layer]]" {
            section = Section::Layer;
            reading.push(Reading {
                line: at,
                id: None,
                layer: Layer::new(0, "", "", LayerStage::TonemapPost),
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
                "format" => value
                    .parse::<u32>()
                    .map_err(|_| format!("`format` wants a whole number, not `{value}`"))
                    .map(|format| {
                        if format > FORMAT {
                            warnings.push(format!(
                                "line {at}: format {format} is newer than this editor's \
                                 {FORMAT}; what it understands is read"
                            ));
                        }
                        project.format = format;
                    }),
                "name" => parse_string(value).map(|v| project.name = v),
                "game" => parse_string(value).map(|v| project.game = v),
                _ => Err(format!("unknown key `{key}`")),
            },
            Section::Look => set_knob(&mut look, key, value),
            Section::Layer => match reading.last_mut() {
                Some(current) => set_layer_key(current, key, value),
                None => Err("no [[layer]] is open".to_owned()),
            },
        };
        if let Err(error) = result {
            warnings.push(format!("line {at}: {error}"));
        }
    }
    if let Some(start) = in_shader {
        warnings.push(format!(
            "line {start}: an embedded shader section has no `--- end ---`; the rest of the file \
             was skipped"
        ));
    }
    if look_seen {
        project.look = Some(look.clamped());
    }

    let mut used: Vec<u32> = Vec::new();
    let mut unnumbered = Vec::new();
    for Reading {
        line,
        id,
        mut layer,
    } in reading
    {
        if layer.file.is_empty() {
            warnings.push(format!(
                "line {line}: a [[layer]] with no `file` is dropped"
            ));
            continue;
        }
        match id {
            Some(id) if !used.contains(&id) => {
                used.push(id);
                layer.id = id;
                project.layers.push(layer);
            }
            Some(id) => {
                warnings.push(format!(
                    "line {line}: id {id} is already another layer's; this one gets a new id"
                ));
                unnumbered.push(project.layers.len());
                project.layers.push(layer);
            }
            None => {
                unnumbered.push(project.layers.len());
                project.layers.push(layer);
            }
        }
    }
    for index in unnumbered {
        let id = project
            .next_id()
            .max(used.iter().max().map_or(1, |m| m + 1));
        used.push(id);
        project.layers[index].id = id;
    }
    (project, warnings)
}

fn set_knob(look: &mut Look, key: &str, value: &str) -> Result<(), String> {
    let index = KNOBS
        .iter()
        .position(|k| k.key == key)
        .ok_or_else(|| format!("unknown look key `{key}`"))?;
    look.0[index] = value
        .parse::<f32>()
        .map_err(|_| format!("`{key}` wants a number, not `{value}`"))?;
    Ok(())
}

fn set_layer_key(current: &mut Reading, key: &str, value: &str) -> Result<(), String> {
    let layer = &mut current.layer;
    if let Some(name) = key.strip_prefix("param.") {
        let valid = name.starts_with(|c: char| c.is_ascii_alphabetic() || c == '_')
            && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_');
        if !valid {
            return Err(format!("`{name}` is not a parameter name"));
        }
        layer.set_param(name, ParamValue::parse(value)?);
        return Ok(());
    }
    match key {
        "id" => {
            let id = value
                .parse::<u32>()
                .ok()
                .filter(|id| *id > 0)
                .ok_or_else(|| format!("`id` wants a whole number above 0, not `{value}`"))?;
            current.id = Some(id);
        }
        "name" => layer.name = parse_string(value)?,
        "file" => {
            let file = parse_string(value)?;
            if !valid_file_name(&file) {
                return Err(format!(
                    "`{file}` is not a layer file name: a `.hlsl` name in layers/, with no \
                     directory"
                ));
            }
            layer.file = file;
        }
        "stage" => {
            let key = parse_string(value)?;
            layer.stage = LayerStage::from_key(&key).ok_or_else(|| {
                let known: Vec<&str> = LayerStage::ALL.iter().map(|s| s.key()).collect();
                format!("`{key}` is no stage; one of {}", known.join(", "))
            })?;
        }
        "enabled" => layer.enabled = parse_bool(value)?,
        "solo" => layer.solo = parse_bool(value)?,
        "opacity" => {
            let opacity = value
                .parse::<f32>()
                .ok()
                .filter(|v| v.is_finite())
                .ok_or_else(|| format!("`opacity` wants a number, not `{value}`"))?;
            layer.opacity = opacity.clamp(0.0, 1.0);
        }
        "blend" => {
            let key = parse_string(value)?;
            layer.blend = Blend::from_key(&key).ok_or_else(|| {
                let known: Vec<&str> = Blend::ALL.iter().map(|b| b.key()).collect();
                format!("`{key}` is no blend; one of {}", known.join(", "))
            })?;
        }
        "select" => layer.select = parse_string(value)?,
        _ => return Err(format!("unknown layer key `{key}`")),
    }
    Ok(())
}

/// The project file's text. The editor rewrites all of it on every save.
#[must_use]
pub fn render(project: &Project) -> String {
    let mut out = String::new();
    out.push_str(
        "# er-shader-editor project. The Shaders panel rewrites this file whenever the stack\n\
         # changes, so comments added here are not kept. Each layer's code is its own file in\n\
         # layers/, edited in game or in any editor.\n",
    );
    out.push_str(&format!("format = {}\n", project.format));
    if !project.name.is_empty() {
        out.push_str(&format!("name = {}\n", quote(&project.name)));
    }
    if !project.game.is_empty() {
        out.push_str(&format!("game = {}\n", quote(&project.game)));
    }
    if let Some(look) = &project.look {
        out.push_str("\n[look]\n");
        for (knob, value) in KNOBS.iter().zip(look.0.iter()) {
            out.push_str(&format!("{} = {value:?}\n", knob.key));
        }
    }
    for layer in &project.layers {
        out.push_str("\n[[layer]]\n");
        out.push_str(&format!("id = {}\n", layer.id));
        out.push_str(&format!("name = {}\n", quote(&layer.name)));
        out.push_str(&format!("stage = {}\n", quote(layer.stage.key())));
        out.push_str(&format!("enabled = {}\n", layer.enabled));
        out.push_str(&format!("solo = {}\n", layer.solo));
        out.push_str(&format!("opacity = {:?}\n", layer.opacity));
        out.push_str(&format!("blend = {}\n", quote(layer.blend.key())));
        out.push_str(&format!("select = {}\n", quote(&layer.select)));
        out.push_str(&format!("file = {}\n", quote(&layer.file)));
        for (name, value) in &layer.params {
            out.push_str(&format!("param.{name} = {}\n", value.render()));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replace::Stage;

    fn sample() -> Project {
        let mut grade = Layer::new(
            3,
            "Warm \"grade\" # one",
            "3-warm-grade.hlsl",
            LayerStage::TonemapPost,
        );
        grade.opacity = 0.8;
        grade.solo = true;
        grade.set_param("Strength", ParamValue::Number(0.6));
        grade.set_param("Shadows", ParamValue::Vector(vec![0.0, 0.02, 0.05]));
        grade.set_param("Keep", ParamValue::Bool(false));
        let mut fog = Layer::new(5, "Depth fog", "5-depth-fog.hlsl", LayerStage::Lighting);
        fog.enabled = false;
        fog.blend = Blend::Screen;
        fog.select = "!depth:0..10m ~2px".to_owned();
        Project {
            format: FORMAT,
            name: "Warm dusk".to_owned(),
            game: "1.17.1".to_owned(),
            look: Some(Look::vanilla()),
            layers: vec![grade, fog],
        }
    }

    #[test]
    fn a_saved_project_reads_back_as_it_was() {
        let project = sample();
        let text = render(&project);
        let (read, warnings) = parse(&text);
        assert!(warnings.is_empty(), "{warnings:?}\n{text}");
        assert_eq!(read, project);
        // And the design's own example reads with no warning.
        let design = "format = 1\nname = \"Warm dusk\"\ngame = \"1.17.1\"\n[look]                       \
                      # optional\nlight_band_stops = 2.0\n[[layer]]\nid = 3\nname = \"Warm grade\"\n\
                      stage = \"tonemap_post\"\nenabled = true\nopacity = 0.8\nblend = \"normal\"\n\
                      select = \"everything\"\nfile = \"3-warm-grade.hlsl\"\nparam.Strength = 0.6\n\
                      param.Shadows = [0.0, 0.02, 0.05]\n";
        let (read, warnings) = parse(design);
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(read.layers.len(), 1);
        assert_eq!(
            read.layers[0].param("Strength"),
            Some(&ParamValue::Number(0.6))
        );
        assert_eq!(read.look.map(|look| look.0[0]), Some(2.0));
    }

    #[test]
    fn a_bad_line_costs_that_line_only() {
        let text = "format = one\n[[layer]]\nid = 4\nstage = \"tonemap\"\nopacity = 0.5\n\
                    blend = \"overlay\"\nnonsense\nparam.2x = 1\nfile = \"4-a.hlsl\"\n\
                    param.Gain = [1, x]\nenabled = false\n";
        let (read, warnings) = parse(text);
        assert_eq!(warnings.len(), 6, "{warnings:?}");
        assert_eq!(read.format, FORMAT);
        let layer = &read.layers[0];
        assert_eq!(layer.id, 4);
        assert_eq!(layer.stage, LayerStage::TonemapPost);
        assert_eq!(layer.opacity, 0.5);
        assert_eq!(layer.blend, Blend::Normal);
        assert!(!layer.enabled);
        assert!(layer.params.is_empty());
        assert!(warnings[0].starts_with("line 1:"), "{warnings:?}");
    }

    #[test]
    fn layers_without_a_file_are_dropped_and_ids_are_made_unique() {
        let text = "[[layer]]\nid = 2\nfile = \"a.hlsl\"\n[[layer]]\nid = 2\nfile = \"b.hlsl\"\n\
                    [[layer]]\nname = \"no file\"\n[[layer]]\nfile = \"c.hlsl\"\n";
        let (read, warnings) = parse(text);
        let ids: Vec<(u32, &str)> = read
            .layers
            .iter()
            .map(|l| (l.id, l.file.as_str()))
            .collect();
        assert_eq!(ids, vec![(2, "a.hlsl"), (3, "b.hlsl"), (4, "c.hlsl")]);
        assert_eq!(warnings.len(), 2, "{warnings:?}");
    }

    #[test]
    fn a_layer_file_cannot_leave_the_layers_directory() {
        for bad in [
            "../x.hlsl",
            "a/b.hlsl",
            r"a\b.hlsl",
            "C:x.hlsl",
            ".hidden.hlsl",
            "x.txt",
            "",
        ] {
            assert!(!valid_file_name(bad), "{bad}");
        }
        assert!(valid_file_name("3-warm-grade.hlsl"));
        let (read, warnings) = parse("[[layer]]\nfile = \"../../eldenring.hlsl\"\n");
        assert!(read.layers.is_empty());
        assert_eq!(warnings.len(), 2, "{warnings:?}");
    }

    #[test]
    fn embedded_shaders_are_skipped_with_one_warning() {
        let text = "[[layer]]\nfile = \"a.hlsl\"\n--- shader a.hlsl sha256=00 ---\nfloat x = [1;\n\
                    # not a comment\n--- end ---\nenabled = false\n";
        let (read, warnings) = parse(text);
        assert_eq!(warnings.len(), 1, "{warnings:?}");
        assert!(!read.layers[0].enabled);
    }

    #[test]
    fn solo_decides_which_layers_draw() {
        let mut project = sample();
        let (grade, fog) = (project.layers[0].clone(), project.layers[1].clone());
        assert!(project.is_on(&grade));
        assert!(!project.is_on(&fog));
        assert_eq!(project.active_stages(), Stage::Tonemap.bit());
        project.layers[1].enabled = true;
        // The fog is enabled but the grade is soloed.
        assert!(!project.is_on(&project.layers[1].clone()));
        project.layers[0].solo = false;
        assert_eq!(
            project.active_stages(),
            Stage::Tonemap.bit() | Stage::Lighting.bit()
        );
        assert_eq!(project.next_id(), 6);
    }

    #[test]
    fn blends_are_numbered_as_stage_ctx_numbers_them() {
        let stage_ctx = include_str!("../../shaders/stage_ctx.hlsli");
        for blend in Blend::ALL {
            let define = format!(
                "#define ER_BLEND_{} {}",
                blend.key().to_ascii_uppercase(),
                blend as u8
            );
            assert!(stage_ctx.lines().any(|l| l.trim() == define), "{define}");
        }
    }
}
