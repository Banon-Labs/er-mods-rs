//! The cel look's stock layers (design 17): the four cel knobs that left the ports, as three
//! ordinary layers of the project, and the two things that carry values between those layers and
//! the rest of the look -- the one-time move of a `[look]` written before them into the project,
//! and a preset applied to the project.
//!
//! # What moved and what stayed
//!
//! | Knobs | Layer ([`CEL_LAYERS`]) | Stage |
//! |---|---|---|
//! | paint radius, colour levels | `cel_surfaces` | Albedo |
//! | saturation | `cel_saturation` | TonemapPost |
//! | brightness steps (`screen_levels`) | `cel_brightness_steps` | TonemapPost |
//!
//! Each template is the maths its port ran, line for line, and each runs where that maths ran:
//! the albedo port's cel worked on the colour the Albedo stage is handed, and the tonemap port's
//! right before the TonemapPost stage. Paint radius and colour levels share one layer because
//! both work on the base colour before the metalness is multiplied in, which only a layer that
//! returns the whole product can see; split in two, the second would have to divide the
//! metalness back out, which is not exact. The light bands and the outlines stayed in the AO port
//! ([`crate::settings::KNOBS`]): the outline inks the specular output too, which a Lighting layer
//! cannot reach, and the bands must run before the outline, which a layer cannot.
//!
//! # Which layers are the cel layers
//!
//! The layers whose [`Layer::template`] is a [`CEL_LAYERS`] key. They are ordinary layers: the
//! player may edit their code, reorder them, mask them or delete them. A preset touches only them,
//! and only the first layer of each template, so a copy the player made of one stays theirs.
//!
//! # The move
//!
//! [`move_into_project`] runs once, the first start with a settings file that has not moved yet
//! (`Settings::cel_layers`). For each layer whose knobs are away from off it writes the
//! template's file into `layers/` and a `[[layer]]` into `project.ershade` with the knobs' values
//! as its parameters and cel's on or off as its `enabled`. A project the player already has keeps
//! every byte it had: the new sections are inserted as text before its first `[[layer]]`, so its
//! comments, its own layers and their order stay as they were. The cel layers go first because
//! the hard-coded cel ran before every layer of its stage, and the order across stages means
//! nothing. A template the project already has a layer of is not added again, so a move that is
//! run a second time -- the settings file could not be saved the first time -- adds nothing.
//!
//! The pure half -- the table, the values, the placing and the text -- is tested on the host, and
//! so is the move itself, on a temporary directory.

use std::path::Path;
use std::sync::LazyLock;

use super::LayerStage;
use super::annotations::{self, ParamDecl};
use super::project::{self, Layer, ParamValue, Project};
use super::templates::{self, Template};
use crate::settings::{LayerLook, layer_knob};

/// One stock cel layer: its template, and which of the [`crate::settings::LAYER_KNOBS`] each of
/// its parameters holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CelLayer {
    /// The `templates::TEMPLATES` key, which [`Layer::template`] records.
    pub template: &'static str,
    /// `(parameter name, index into LAYER_KNOBS)`.
    pub params: &'static [(&'static str, usize)],
}

/// The stock cel layers, in the order they go into a stack: the surfaces at Albedo, then
/// saturation before brightness steps at TonemapPost, which is the order the tonemap port ran
/// them in.
pub const CEL_LAYERS: [CelLayer; 3] = [
    CelLayer {
        template: "cel_surfaces",
        params: &[
            ("PaintRadius", layer_knob::PAINT_RADIUS),
            ("ColourLevels", layer_knob::COLOUR_LEVELS),
        ],
    },
    CelLayer {
        template: "cel_saturation",
        params: &[("Saturation", layer_knob::SATURATION)],
    },
    CelLayer {
        template: "cel_brightness_steps",
        params: &[("Steps", layer_knob::SCREEN_LEVELS)],
    },
];

impl CelLayer {
    /// Its template.
    ///
    /// # Panics
    ///
    /// Never: a test holds every [`CEL_LAYERS`] key to `templates::TEMPLATES`.
    #[must_use]
    pub fn template(&self) -> &'static Template {
        templates::find(self.template).unwrap_or(&templates::TEMPLATES[0])
    }

    #[must_use]
    pub fn stage(&self) -> LayerStage {
        self.template().stage
    }

    /// Does the layer change the picture with these values? The shader's own test: a radius that
    /// rounds to 0 and fewer than two levels or steps leave the colour as it was, as does a
    /// saturation of exactly 1.
    #[must_use]
    pub fn draws(&self, values: &LayerLook) -> bool {
        let values = values.clamped();
        self.params.iter().any(|&(_, knob)| {
            let value = values.0[knob];
            match knob {
                layer_knob::PAINT_RADIUS => value >= 1.0,
                layer_knob::COLOUR_LEVELS | layer_knob::SCREEN_LEVELS => value >= 2.0,
                _ => value != 1.0,
            }
        })
    }
}

/// The [`CEL_LAYERS`] entry for a template key.
#[must_use]
pub fn cel_layer(template: &str) -> Option<&'static CelLayer> {
    CEL_LAYERS.iter().find(|cel| cel.template == template)
}

/// Is `layer` one of the stock cel layers?
#[must_use]
pub fn is_cel(layer: &Layer) -> bool {
    cel_layer(&layer.template).is_some()
}

/// Each cel template's parameters as its annotations declare them, for their defaults.
static DECLARED: LazyLock<Vec<Vec<ParamDecl>>> = LazyLock::new(|| {
    CEL_LAYERS
        .iter()
        .map(|cel| annotations::parse(cel.template().text, 0).params)
        .collect()
});

/// The value of `cel`'s parameter `name` in `layer`: the project's, else the template's default.
fn param_of(layer: &Layer, at: usize, name: &str) -> f32 {
    let default = DECLARED
        .get(at)
        .and_then(|decls| decls.iter().find(|decl| decl.name == name))
        .map_or(0.0, |decl| decl.default[0] as f32);
    match layer.param(name) {
        Some(ParamValue::Number(value)) => *value as f32,
        Some(ParamValue::Bool(value)) => f32::from(u8::from(*value)),
        Some(ParamValue::Vector(_)) | None => default,
    }
}

/// The first layer of the project made from `cel`'s template.
#[must_use]
pub fn find<'a>(project: &'a Project, cel: &CelLayer) -> Option<&'a Layer> {
    project
        .layers
        .iter()
        .find(|layer| layer.template == cel.template)
}

/// The values the project's cel layers hold: each layer's parameters, or the knob's off value
/// when the project has no layer of that template. Whether a layer is enabled does not enter:
/// that is cel on or off, which the settings keep apart from the values.
#[must_use]
pub fn values_of(project: &Project) -> LayerLook {
    let mut values = LayerLook::vanilla();
    for (at, cel) in CEL_LAYERS.iter().enumerate() {
        if let Some(layer) = find(project, cel) {
            for &(name, knob) in cel.params {
                values.0[knob] = param_of(layer, at, name);
            }
        }
    }
    values.clamped()
}

/// A knob's value as the project file writes it: the shortest decimal that reads back as the same
/// `f32`, so a moved `1.35` is `1.35` in the file and the same bits in the parameter block.
fn param_value(value: f32) -> ParamValue {
    ParamValue::Number(
        format!("{value}")
            .parse::<f64>()
            .unwrap_or(f64::from(value)),
    )
}

/// Set `cel`'s parameters in `layer` to `values`.
fn set_params(layer: &mut Layer, cel: &CelLayer, values: &LayerLook) {
    let values = values.clamped();
    for &(name, knob) in cel.params {
        layer.set_param(name, param_value(values.0[knob]));
    }
}

/// A new layer of `cel` with id `id`, its parameters at `values`, enabled when cel is on and the
/// values draw. Its file is `<id>-<slug>.hlsl`; the caller writes it, or another free name.
#[must_use]
pub fn new_layer(id: u32, cel: &CelLayer, values: &LayerLook, enabled: bool) -> (Layer, String) {
    let template = cel.template();
    let mut layer = Layer::new(
        id,
        template.label,
        &templates::layer_file_name(id, template.label),
        template.stage,
    );
    layer.template = cel.template.to_owned();
    layer.enabled = enabled && cel.draws(values);
    set_params(&mut layer, cel, values);
    (layer, template.text.to_owned())
}

/// Where a new cel layer goes in `project.layers`: after the cel layers that come before it in
/// [`CEL_LAYERS`] at its stage, else before the first layer of its stage, else at the end. So the
/// cel layers run first at their stage, in the order the ports ran their maths.
#[must_use]
pub fn place(project: &Project, layer: &Layer) -> usize {
    let order = |template: &str| CEL_LAYERS.iter().position(|cel| cel.template == template);
    let mine = order(&layer.template).unwrap_or(CEL_LAYERS.len());
    let earlier_cel = project.layers.iter().rposition(|other| {
        other.stage == layer.stage && order(&other.template).is_some_and(|theirs| theirs < mine)
    });
    if let Some(at) = earlier_cel {
        return at + 1;
    }
    project
        .layers
        .iter()
        .position(|other| other.stage == layer.stage)
        .unwrap_or(project.layers.len())
}

/// Put a new cel layer into `project` at [`place`].
pub fn insert(project: &mut Project, layer: Layer) {
    let at = place(project, &layer);
    project.layers.insert(at, layer);
}

/// Apply a preset's layer values to `project`: each existing cel layer takes `values` and is
/// enabled when cel is on and the values draw; each cel layer the values need and the project
/// lacks is returned, with ids from the project's next free one, for the caller to make (its file
/// first). Returns whether `project` changed.
pub fn apply(project: &mut Project, values: &LayerLook, enabled: bool) -> (bool, Vec<Layer>) {
    let mut changed = false;
    let mut create = Vec::new();
    let mut next = project.next_id();
    for cel in &CEL_LAYERS {
        let existing = project
            .layers
            .iter_mut()
            .find(|layer| layer.template == cel.template);
        match existing {
            Some(layer) => {
                let before = layer.clone();
                set_params(layer, cel, values);
                layer.enabled = enabled && cel.draws(values);
                changed |= *layer != before;
            }
            None if cel.draws(values) => {
                create.push(new_layer(next, cel, values, enabled).0);
                next += 1;
            }
            None => {}
        }
    }
    (changed, create)
}

/// Cel on or off: every cel layer is enabled when `enabled` and its own values draw. Returns
/// whether `project` changed.
pub fn set_enabled(project: &mut Project, enabled: bool) -> bool {
    let mut changed = false;
    for layer in &mut project.layers {
        let Some(cel) = cel_layer(&layer.template) else {
            continue;
        };
        let mut values = LayerLook::vanilla();
        let at = CEL_LAYERS
            .iter()
            .position(|other| other.template == cel.template)
            .unwrap_or(0);
        for &(name, knob) in cel.params {
            values.0[knob] = param_of(layer, at, name);
        }
        let on = enabled && cel.draws(&values);
        changed |= layer.enabled != on;
        layer.enabled = on;
    }
    changed
}

/// The cel layers a move adds to `project`: one for each template whose knobs in `moved` are away
/// from off and that the project has no layer of yet, with ids from its next free one.
#[must_use]
pub fn layers_to_move(project: &Project, moved: &LayerLook, enabled: bool) -> Vec<(Layer, String)> {
    let mut next = project.next_id();
    let mut out = Vec::new();
    for cel in &CEL_LAYERS {
        if find(project, cel).is_some() || !cel.draws(moved) {
            continue;
        }
        out.push(new_layer(next, cel, moved, enabled));
        next += 1;
    }
    out
}

/// `text`, a project file, with `layers`' sections inserted before its first `[[layer]]`, or at its
/// end when it has none, and every other line as it was. An empty file becomes a whole new
/// project of `layers`.
#[must_use]
pub fn insert_into_text(text: &str, layers: &[Layer]) -> String {
    if text.trim().is_empty() {
        return project::render(&Project {
            layers: layers.to_vec(),
            ..Project::default()
        });
    }
    let sections: String = layers
        .iter()
        .map(|layer| format!("{}\n", project::render_layer(layer)))
        .collect();
    match project::first_layer_line(text) {
        Some(line) => {
            let at = text
                .split_inclusive('\n')
                .take(line)
                .map(str::len)
                .sum::<usize>();
            format!("{}{sections}{}", &text[..at], &text[at..])
        }
        None => {
            let gap = if text.ends_with('\n') { "\n" } else { "\n\n" };
            format!("{text}{gap}{}", sections.trim_end_matches('\n')) + "\n"
        }
    }
}

/// Move the four knobs into the project in `editor_dir` (`<Game>/er-shader-editor`): make each
/// new layer's file in `layers/` under the first free name, then write the project with their
/// sections inserted. Returns the layers added, which is none when every knob is at off or the
/// project already has every layer. Nothing is written when there is nothing to add.
///
/// # Errors
///
/// Why a file could not be read or written. Layer files made before the failure stay in
/// `layers/`, unnamed by the project, and the next move takes other names.
pub fn move_into_project(
    editor_dir: &Path,
    moved: &LayerLook,
    enabled: bool,
) -> Result<Vec<Layer>, String> {
    let project_path = editor_dir.join(project::FILE_NAME);
    let text = match std::fs::read(&project_path) {
        Ok(bytes) => String::from_utf8(bytes)
            .map_err(|_| format!("{} is not UTF-8", project_path.display()))?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(error) => {
            return Err(format!(
                "{} cannot be read: {error}",
                project_path.display()
            ));
        }
    };
    let (project, _) = project::parse(&text);
    let wanted = layers_to_move(&project, moved, enabled);
    if wanted.is_empty() {
        return Ok(Vec::new());
    }
    let layers_dir = editor_dir.join(project::LAYERS_DIRECTORY);
    std::fs::create_dir_all(&layers_dir)
        .map_err(|error| format!("{} cannot be made: {error}", layers_dir.display()))?;
    let mut added = Vec::new();
    for (mut layer, source) in wanted {
        layer.file = create_file(&layers_dir, &layer, &source)?;
        added.push(layer);
    }
    let updated = insert_into_text(&text, &added);
    let staging = editor_dir.join(format!("{}.moving", project::FILE_NAME));
    std::fs::write(&staging, &updated)
        .map_err(|error| format!("{} cannot be written: {error}", staging.display()))?;
    std::fs::rename(&staging, &project_path)
        .map_err(|error| format!("{} cannot be replaced: {error}", project_path.display()))?;
    Ok(added)
}

/// Write `source` as `layer`'s file in `dir` under the first of its free candidate names, never
/// over a file already there. Returns the name.
fn create_file(dir: &Path, layer: &Layer, source: &str) -> Result<String, String> {
    use std::io::Write as _;
    for name in crate::menu::files::candidate_names(layer.id, &layer.name) {
        if !project::valid_file_name(&name) {
            continue;
        }
        let path = dir.join(&name);
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(mut file) => {
                file.write_all(source.as_bytes())
                    .map_err(|error| format!("{} cannot be written: {error}", path.display()))?;
                return Ok(name);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(format!("{} cannot be made: {error}", path.display())),
        }
    }
    Err(format!(
        "no free file name for layer {} in {}",
        layer.id,
        dir.display()
    ))
}

/// The values in `look` of `cel`'s knobs, for the log.
#[must_use]
pub fn describe(cel: &CelLayer, look: &LayerLook) -> String {
    let look = look.clamped();
    cel.params
        .iter()
        .map(|&(name, knob)| format!("{name}={}", look.0[knob]))
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::annotations::ParamType;
    use crate::editor::prepare;
    use crate::settings::{LAYER_KNOB_COUNT, LAYER_KNOBS, builtin_presets};

    #[test]
    fn every_moved_knob_is_one_parameter_of_one_stock_layer_with_its_range() {
        let mut seen = [0; LAYER_KNOB_COUNT];
        for (at, cel) in CEL_LAYERS.iter().enumerate() {
            assert_eq!(
                cel.template().key,
                cel.template,
                "{} has no template",
                cel.template
            );
            let decls = &DECLARED[at];
            assert_eq!(decls.len(), cel.params.len(), "{}", cel.template);
            for &(name, knob) in cel.params {
                seen[knob] += 1;
                let decl = decls
                    .iter()
                    .find(|decl| decl.name == name)
                    .unwrap_or_else(|| panic!("{} declares no {name}", cel.template));
                let old = LAYER_KNOBS[knob];
                // The slider the Look tab had is the slider the layer has.
                assert_eq!(decl.min, Some(f64::from(old.min)), "{name}");
                assert_eq!(decl.max, Some(f64::from(old.max)), "{name}");
                assert_eq!(decl.ty == ParamType::Int, old.integer, "{name}");
                assert_eq!(decl.ty.components(), 1, "{name}");
            }
        }
        assert_eq!(seen, [1; LAYER_KNOB_COUNT]);
    }

    #[test]
    fn off_is_the_shaders_off() {
        let surfaces = &CEL_LAYERS[0];
        let saturation = &CEL_LAYERS[1];
        let steps = &CEL_LAYERS[2];
        let vanilla = LayerLook::vanilla();
        for cel in &CEL_LAYERS {
            assert!(!cel.draws(&vanilla), "{}", cel.template);
        }
        let with = |knob: usize, value: f32| {
            let mut look = vanilla;
            look.0[knob] = value;
            look
        };
        assert!(!surfaces.draws(&with(layer_knob::PAINT_RADIUS, 0.4)));
        assert!(surfaces.draws(&with(layer_knob::PAINT_RADIUS, 1.0)));
        assert!(!surfaces.draws(&with(layer_knob::COLOUR_LEVELS, 1.0)));
        assert!(surfaces.draws(&with(layer_knob::COLOUR_LEVELS, 2.0)));
        assert!(!steps.draws(&with(layer_knob::SCREEN_LEVELS, 1.0)));
        assert!(steps.draws(&with(layer_knob::SCREEN_LEVELS, 2.0)));
        assert!(saturation.draws(&with(layer_knob::SATURATION, 0.0)));
        assert!(!saturation.draws(&with(layer_knob::SATURATION, 1.0)));
        // A knob of one layer is no content of another.
        assert!(!saturation.draws(&with(layer_knob::PAINT_RADIUS, 3.0)));
    }

    /// Values every stock layer draws with. No built-in preset has all three on: Wind Waker leaves
    /// saturation at 1, Bright toon the steps at 0.
    fn every_layer() -> LayerLook {
        LayerLook([3.0, 6.0, 6.0, 1.2])
    }

    fn user_layer(id: u32, stage: LayerStage) -> Layer {
        Layer::new(id, "mine", &format!("{id}-mine.hlsl"), stage)
    }

    #[test]
    fn a_preset_builds_its_layers_and_reads_back_as_itself() {
        let mut project = Project {
            layers: vec![
                user_layer(4, LayerStage::TonemapPost),
                user_layer(2, LayerStage::Lighting),
            ],
            ..Project::default()
        };
        for preset in builtin_presets() {
            let (_, create) = apply(&mut project, &preset.layers, true);
            for layer in create {
                insert(&mut project, layer);
            }
            assert_eq!(values_of(&project), preset.layers, "{}", preset.name);
            for cel in &CEL_LAYERS {
                if let Some(layer) = find(&project, cel) {
                    assert_eq!(layer.enabled, cel.draws(&preset.layers), "{}", preset.name);
                }
            }
        }
        // Each template once, and the player's two layers untouched.
        for cel in &CEL_LAYERS {
            let count = project
                .layers
                .iter()
                .filter(|l| l.template == cel.template)
                .count();
            assert_eq!(count, 1, "{}", cel.template);
        }
        assert!(
            project
                .layers
                .iter()
                .any(|l| l.id == 4 && l.template.is_empty())
        );
        // The cel layers come first at TonemapPost, saturation before steps.
        let tonemap: Vec<&str> = project
            .layers
            .iter()
            .filter(|l| l.stage == LayerStage::TonemapPost)
            .map(|l| l.template.as_str())
            .collect();
        assert_eq!(tonemap, ["cel_saturation", "cel_brightness_steps", ""]);
        // Cel off disables every cel layer and touches nothing else; on brings back the ones
        // whose values draw.
        let wind_waker = builtin_presets()[0].layers;
        assert_eq!(apply(&mut project, &wind_waker, true).1, Vec::new());
        let before = project.clone();
        assert!(set_enabled(&mut project, false));
        for (now, was) in project.layers.iter().zip(&before.layers) {
            assert_eq!(now.enabled, was.enabled && !is_cel(was), "{}", now.id);
        }
        assert!(set_enabled(&mut project, true));
        assert_eq!(project, before);
    }

    #[test]
    fn steps_made_alone_still_go_after_an_existing_saturation() {
        let mut project = Project::default();
        let values = builtin_presets()[0].layers;
        let (saturation, _) = new_layer(1, &CEL_LAYERS[1], &values, true);
        let mine = user_layer(2, LayerStage::TonemapPost);
        project.layers = vec![mine, saturation];
        let (steps, _) = new_layer(3, &CEL_LAYERS[2], &values, true);
        insert(&mut project, steps);
        let order: Vec<u32> = project.layers.iter().map(|l| l.id).collect();
        assert_eq!(order, [2, 1, 3]);
    }

    #[test]
    fn a_moved_value_keeps_its_bits() {
        // 1.35 as an `f32` is 1.35000002384...; the file says 1.35 and the block gets the f32.
        let mut look = LayerLook::vanilla();
        look.0[layer_knob::SATURATION] = 1.35;
        let (layer, text) = new_layer(5, &CEL_LAYERS[1], &look, true);
        let rendered = project::render_layer(&layer);
        assert!(rendered.contains("param.Saturation = 1.35\n"), "{rendered}");
        let Ok(prepared) = prepare(&layer, &text, 0) else {
            panic!("the saturation template did not prepare");
        };
        let decl = &prepared.params[0];
        let words = decl.words(
            super::super::param_values(decl, layer.param("Saturation")).unwrap_or(decl.default),
        );
        assert_eq!(words, vec![1.35_f32.to_bits()]);
    }

    #[test]
    fn the_move_inserts_before_the_first_layer_and_keeps_every_other_line() {
        let text = "# my own notes\nformat = 1\nname = \"Wobble set\"\n\n[[layer]]\nid = 3\n\
                    name = \"Wobble\"\nstage = \"tonemap_post\"\nfile = \"3-wobble.hlsl\"\n\
                    param.Amount = 0.5  # keep\n";
        let (before, warnings) = project::parse(text);
        assert!(warnings.is_empty(), "{warnings:?}");
        let moved = every_layer();
        let adding: Vec<Layer> = layers_to_move(&before, &moved, true)
            .into_iter()
            .map(|(layer, _)| layer)
            .collect();
        assert_eq!(
            adding.iter().map(|l| l.id).collect::<Vec<_>>(),
            [4, 5, 6],
            "ids after the project's own"
        );
        let after_text = insert_into_text(text, &adding);
        // The original text is all still there: taking the inserted sections out gives it back.
        let sections: String = adding
            .iter()
            .map(|layer| format!("{}\n", project::render_layer(layer)))
            .collect();
        assert!(after_text.contains(&format!("\n\n{sections}[[layer]]\nid = 3\n")));
        assert_eq!(after_text.replacen(&sections, "", 1), text);
        let (after, warnings) = project::parse(&after_text);
        assert!(warnings.is_empty(), "{warnings:?}");
        let mut expected = before.clone();
        expected.layers.splice(0..0, adding.iter().cloned());
        assert_eq!(after, expected);
        assert_eq!(values_of(&after), moved);
        // A second move adds nothing.
        assert!(layers_to_move(&after, &moved, true).is_empty());
        // An empty file is a project of the cel layers alone.
        let fresh = insert_into_text("", &adding);
        assert_eq!(project::parse(&fresh).0.layers, adding);
        // A file with no layer gets them at its end.
        let bare = "format = 1\nname = \"x\"";
        let (with, warnings) = project::parse(&insert_into_text(bare, &adding));
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!((with.name.as_str(), with.layers), ("x", adding));
    }

    #[test]
    fn a_layer_line_inside_an_embedded_shader_is_not_the_first_layer() {
        let text = "format = 1\n--- shader a.hlsl sha256=00 ---\n[[layer]]\n--- end ---\n\
                    [[layer]]\nfile = \"a.hlsl\"\n";
        assert_eq!(project::first_layer_line(text), Some(4));
        assert_eq!(project::first_layer_line("format = 1\n"), None);
        assert_eq!(project::first_layer_line("  [[layer]]  # x\n"), Some(0));
    }

    #[test]
    fn nothing_to_move_when_every_knob_is_off() {
        let comic_ink = builtin_presets()[3].layers;
        let adding = layers_to_move(&Project::default(), &comic_ink, true);
        // Comic ink leaves the surfaces and saturation alone and steps the picture.
        let templates: Vec<&str> = adding.iter().map(|(l, _)| l.template.as_str()).collect();
        assert_eq!(templates, ["cel_brightness_steps"]);
        assert!(layers_to_move(&Project::default(), &LayerLook::vanilla(), true).is_empty());
        // Cel off moves the values with the layers off.
        let off = layers_to_move(&Project::default(), &comic_ink, false);
        assert!(off.iter().all(|(layer, _)| !layer.enabled));
    }

    /// A directory of its own under the system's temporary directory, emptied first.
    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("er-cel-move-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn the_move_writes_the_files_and_keeps_a_project_the_player_has() {
        let dir = scratch("keeps");
        let layers = dir.join(project::LAYERS_DIRECTORY);
        std::fs::create_dir_all(&layers).expect("scratch directory");
        let text = "format = 1\n[[layer]]\nid = 1\nname = \"Wobble\"\nstage = \"tonemap_post\"\n\
                    file = \"1-wobble.hlsl\"\n";
        std::fs::write(dir.join(project::FILE_NAME), text).expect("project");
        // A file left in layers/ by a deleted layer whose id comes round again is not overwritten.
        std::fs::write(layers.join("2-cel-surfaces.hlsl"), "// mine\n").expect("old file");
        let moved = every_layer();
        let added = move_into_project(&dir, &moved, true).expect("the move");
        assert_eq!(added.len(), 3);
        assert_eq!(added[0].file, "2-cel-surfaces-2.hlsl");
        assert_eq!(
            std::fs::read_to_string(layers.join("2-cel-surfaces.hlsl")).ok(),
            Some("// mine\n".to_owned())
        );
        for layer in &added {
            let written = std::fs::read_to_string(layers.join(&layer.file)).unwrap_or_default();
            let Some(cel) = cel_layer(&layer.template) else {
                panic!("{} is no cel layer", layer.template);
            };
            assert_eq!(written, cel.template().text, "{}", layer.file);
        }
        let read = std::fs::read_to_string(dir.join(project::FILE_NAME)).unwrap_or_default();
        let (project, warnings) = project::parse(&read);
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(project.layers.len(), 4);
        assert_eq!(project.layers[3].name, "Wobble");
        assert_eq!(values_of(&project), moved);
        assert!(!dir.join("project.ershade.moving").exists());
        // Run again, as after a settings file that could not be saved: nothing more.
        assert_eq!(move_into_project(&dir, &moved, true), Ok(Vec::new()));
        assert_eq!(
            std::fs::read_to_string(dir.join(project::FILE_NAME)).unwrap_or_default(),
            read
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_move_makes_a_project_where_there_is_none() {
        let dir = scratch("fresh");
        let moved = builtin_presets()[1].layers;
        let added = move_into_project(&dir, &moved, true).expect("the move");
        let read = std::fs::read_to_string(dir.join(project::FILE_NAME)).unwrap_or_default();
        assert_eq!(project::parse(&read).0.layers, added);
        // Nothing to move writes nothing.
        let empty = scratch("nothing");
        assert_eq!(
            move_into_project(&empty, &LayerLook::vanilla(), true),
            Ok(Vec::new())
        );
        assert!(!empty.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
