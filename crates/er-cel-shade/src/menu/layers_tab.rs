//! The Layers tab (design 9): the layer stack grouped by stage, and under it the selected layer's
//! inspector with its Parameters, Code and Selector sub-tabs.
//!
//! # The panel edits a copy of the project
//!
//! The project file is the source of truth (design 7.7), and the worker reads it. The panel keeps
//! its own copy, changes that, and writes the file [`super::code::SAVE_DELAY`] after the last
//! change, through the file thread ([`super::files`]). Until the worker has read that save back
//! the change is pending, and every frame the panel writes the layers' headers and parameters
//! into the parameter block itself ([`crate::editor::write_layers`], the function the worker
//! uses) and the stages that have a drawing layer into `safety::LAYER_STAGES`. That is what makes
//! a slider, a toggle, solo, opacity, blend or a mask show in the frame it is moved: none of them
//! recompile, and none of them wait for the disk.
//!
//! A project the worker reads that the panel did not write -- an edit made in a Linux editor --
//! replaces the panel's copy, unless a save of the panel's is still waiting, which then
//! overwrites it. A project the panel wrote earlier and the worker reads late is recognised and
//! ignored, so a quick run of edits does not flicker back.
//!
//! # The stack
//!
//! Rows are grouped by stage in the order the stages run ([`rows`]), each group under its stage's
//! heading; Up and Down move a layer among the layers of its own stage, since the order across
//! stages means nothing. Each row has a status light ([`light`]), which a click on it turns the
//! layer on or off, the name, `S` for solo on the selected row and on any soloed one, and its
//! compile time or state ([`state_style`]). Alt+click on a row solos that layer alone
//! ([`solo_only`]); a right-click gives the same three as a menu. "+ Layer" makes a file from a
//! template ([`crate::editor::templates`]); Dup copies the selected layer's file; Del takes the
//! layer out of the project and leaves its file in `layers/`.
//!
//! # The layout
//!
//! The Parameters sub-tab is the Main mockup: the grouped stack, its button bar, the layer's
//! Name, Stage, Blend and Mask in two columns, Opacity, the sub-tabs, the parameters under their
//! category headings ([`super::widgets`]), and a footer pinned to the bottom with the layer's
//! state and file. The Code and Selector sub-tabs are their own mockups: the stack turns compact,
//! one row per layer with its stage in a column, and the button bar and the fields go, leaving the
//! room to the code or the selector.
//!
//! The Selector sub-tab edits the layer's selector text ([`Layer::select`]) as a whole, and its
//! screen mask through a combo and its bounds; a rewrite of the mask keeps the terms it does not
//! own ([`with_mask`]), which the family and instance selectors of P3 and P4 will read. Those
//! selectors, and Preview, Blink and Pick, are drawn as the mockup has them but do nothing yet,
//! and say which phase brings them.
//!
//! The stack's bookkeeping is pure and tested on the host; the drawing is the Windows half.

use super::theme::{self, Font};
use crate::editor::LayerStage;
use crate::editor::cel;
use crate::editor::mask::{self, Mask, MaskKind};
use crate::editor::project::{self, Layer, Project};
use crate::editor::templates::{TEMPLATES, Template};
use crate::editor::worker::LayerState;
use crate::vertex::ChipState;

/// One row of the stack.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Row {
    /// The heading over a stage's layers.
    Stage(LayerStage),
    Layer(u32),
}

/// The stack's rows: each stage that has a layer, in the order the stages run, with its layers in
/// project order under it.
#[must_use]
pub fn rows(project: &Project) -> Vec<Row> {
    let mut out = Vec::new();
    for stage in LayerStage::ALL {
        let mut layers = project
            .layers
            .iter()
            .filter(|layer| layer.stage == stage)
            .peekable();
        if layers.peek().is_some() {
            out.push(Row::Stage(stage));
        }
        out.extend(layers.map(|layer| Row::Layer(layer.id)));
    }
    out
}

/// The "+ Layer" menu: the templates under their stages' headings, the stages in the order they
/// run and each stage's templates in [`TEMPLATES`] order, so Vertex and its proof lift come first.
#[must_use]
pub fn template_groups() -> Vec<(LayerStage, Vec<&'static Template>)> {
    LayerStage::ALL
        .into_iter()
        .map(|stage| {
            let group: Vec<&'static Template> =
                TEMPLATES.iter().filter(|t| t.stage == stage).collect();
            (stage, group)
        })
        .filter(|(_, group)| !group.is_empty())
        .collect()
}

/// Move layer `id` above (`up`) or below the next layer of its own stage. False when there is
/// none to pass.
pub fn move_within_stage(project: &mut Project, id: u32, up: bool) -> bool {
    let Some(at) = project.layers.iter().position(|layer| layer.id == id) else {
        return false;
    };
    let stage = project.layers[at].stage;
    let other = if up {
        project.layers[..at]
            .iter()
            .rposition(|layer| layer.stage == stage)
    } else {
        project.layers[at + 1..]
            .iter()
            .position(|layer| layer.stage == stage)
            .map(|offset| at + 1 + offset)
    };
    match other {
        Some(other) => {
            project.layers.swap(at, other);
            true
        }
        None => false,
    }
}

/// Alt+click: solo layer `id` alone, or, when it already is the only layer soloed, solo none.
pub fn solo_only(project: &mut Project, id: u32) {
    let alone = project
        .layers
        .iter()
        .all(|layer| layer.solo == (layer.id == id));
    for layer in &mut project.layers {
        layer.solo = !alone && layer.id == id;
    }
}

/// Take layer `id` out of the project. Returns the layer to select next: the row below it, else
/// the row above, else none.
pub fn remove_layer(project: &mut Project, id: u32) -> Option<u32> {
    let order: Vec<u32> = rows(project)
        .into_iter()
        .filter_map(|row| match row {
            Row::Layer(id) => Some(id),
            Row::Stage(_) => None,
        })
        .collect();
    let at = order.iter().position(|&shown| shown == id)?;
    project.layers.retain(|layer| layer.id != id);
    order
        .get(at + 1)
        .or_else(|| at.checked_sub(1).and_then(|before| order.get(before)))
        .copied()
}

/// A copy of `layer` as layer `id`, for Dup: every setting and parameter value, not soloed. Its
/// file is named when the copy of the file is made. The copy is the player's own layer, not one a
/// preset sets, so it keeps no template (`crate::editor::cel`).
#[must_use]
pub fn copy_of(layer: &Layer, id: u32) -> Layer {
    Layer {
        id,
        name: format!("{} copy", layer.name),
        file: String::new(),
        solo: false,
        template: String::new(),
        ..layer.clone()
    }
}

/// Put a layer the file thread made into `project`: after layer `after`, or, with none, a stock
/// cel layer at the head of its stage (`cel::place`) and any other at the end.
pub fn insert_made(project: &mut Project, layer: Layer, after: Option<u32>) {
    if after.is_none() && cel::is_cel(&layer) {
        cel::insert(project, layer);
    } else {
        insert_after(project, layer, after);
    }
}

/// Where a new layer at `stage` goes: after the selected layer when it is at that stage, else
/// after the last layer of that stage, else at the end (`None`).
#[must_use]
pub fn place_for(project: &Project, stage: LayerStage, selected: Option<u32>) -> Option<u32> {
    selected
        .and_then(|id| project.layer(id))
        .filter(|layer| layer.stage == stage)
        .or_else(|| {
            project
                .layers
                .iter()
                .rev()
                .find(|layer| layer.stage == stage)
        })
        .map(|layer| layer.id)
}

/// Put `layer` into the project after layer `after`, or at the end.
pub fn insert_after(project: &mut Project, layer: Layer, after: Option<u32>) {
    let at = after
        .and_then(|id| project.layers.iter().position(|layer| layer.id == id))
        .map_or(project.layers.len(), |at| at + 1);
    project.layers.insert(at, layer);
}

/// Does the worker's new reading `read` of the project file replace the panel's copy `ours`? Only
/// when it was made elsewhere: it is not the copy, not one of the projects the panel wrote
/// (`sent`, which the worker can read back late, after a newer save), and no save of the panel's
/// is waiting (`save_waiting`) to overwrite it.
#[must_use]
pub fn adopt_reading(read: &Project, ours: &Project, sent: &[Project], save_waiting: bool) -> bool {
    read != ours && !sent.contains(read) && !save_waiting
}

/// A row's status light (design 9): green live, amber compiling, red error, grey off.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Light {
    Live,
    Busy,
    Error,
    Off,
}

/// The light and the short text at the end of `layer`'s row, from the worker's `state` for it.
#[must_use]
pub fn light(project: &Project, layer: &Layer, state: Option<&LayerState>) -> (Light, String) {
    match state {
        Some(LayerState::Error { running: true, .. }) => {
            (Light::Error, "error, last good running".to_owned())
        }
        Some(LayerState::Error { running: false, .. }) => (Light::Error, "error".to_owned()),
        Some(LayerState::NoCompiler { .. }) => (Light::Off, "no compiler".to_owned()),
        _ if !layer.enabled => (Light::Off, "off".to_owned()),
        _ if !project.is_on(layer) => (Light::Off, "muted by solo".to_owned()),
        None => (Light::Busy, "reading".to_owned()),
        Some(LayerState::Pending) => (Light::Busy, "compiling".to_owned()),
        Some(LayerState::Live { compile_ms, .. }) => (Light::Live, format!("{compile_ms:.0} ms")),
        Some(LayerState::Carrier { compile_ms }) => {
            (Light::Live, format!("{compile_ms:.0} ms, carrier"))
        }
    }
}

/// The font and colour of the words at the end of a stack row (Main, Code and Selector mockups): a
/// compile time in 11-pixel mono, other words in 11-pixel Plex; an error red, and the selected
/// row's words lighter than the rest.
#[must_use]
pub const fn state_style(lamp: Light, selected: bool) -> (Font, [f32; 4]) {
    match lamp {
        Light::Live if selected => (Font::MonoSmall, theme::TEXT_SECONDARY),
        Light::Live => (Font::MonoSmall, theme::TEXT_QUIET),
        Light::Error => (Font::Small, theme::ERROR_TEXT),
        Light::Busy if selected => (Font::Small, theme::SELECTED_NAME),
        Light::Busy | Light::Off => (Font::Small, theme::TEXT_QUIET),
    }
}

/// A layer file's path as the Parameters footer shows it (Main mockup): from the game's folder,
/// which `<Game>` stands for.
#[must_use]
pub fn shown_path(file: &str) -> String {
    format!(
        "<Game>\\{}\\{}\\{file}",
        project::DIRECTORY,
        project::LAYERS_DIRECTORY
    )
}

/// The bounds a mask gets when it is picked from the Mask combo.
#[must_use]
pub const fn default_bounds(kind: MaskKind) -> [f32; 4] {
    match kind {
        MaskKind::Depth => [0.0, 30.0, 0.0, 0.0],
        MaskKind::Facing => [0.0, 0.3, 0.0, 0.0],
        MaskKind::Luma => [0.5, 1.0, 0.0, 0.0],
        MaskKind::Rect => [0.0, 0.0, 0.5, 1.0],
        MaskKind::Everything | MaskKind::Geometry | MaskKind::Sky => [0.0; 4],
    }
}

/// What the Mask combo shows for `kind` on a layer at `stage`: its name, marked when the stage
/// cannot evaluate it. A vertex layer moves whole meshes, so no screen mask but Everything means
/// anything there, the ones every pixel stage has included.
#[must_use]
pub fn mask_preview(kind: MaskKind, stage: LayerStage) -> String {
    if kind.available_at(stage) {
        kind.label().to_owned()
    } else if stage.is_vertex() {
        format!("{} (not at Vertex)", kind.label())
    } else {
        format!("{} (Lighting only)", kind.label())
    }
}

/// The light of the vertex stage's chip: grey with no carrier, amber while groups are being built,
/// green once every group the carrier wants is live.
#[must_use]
pub const fn vertex_light(state: ChipState) -> Light {
    match state {
        ChipState::Idle => Light::Off,
        ChipState::Preparing => Light::Busy,
        ChipState::Live => Light::Live,
    }
}

/// How the Selector's Screen mask row shows a mask kind's bounds.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bounds {
    /// Fields: the two ends of a range, or a rectangle's two corners.
    pub count: usize,
    /// The unit after the fields; empty for none.
    pub unit: &'static str,
    /// The largest value a field takes; the smallest is 0.
    pub max: f32,
    /// What each field holds, under the mouse.
    pub tips: [&'static str; 4],
}

/// The bounds `kind`'s text holds, or `None` for a kind without any.
#[must_use]
pub const fn bounds_of(kind: MaskKind) -> Option<Bounds> {
    match kind {
        MaskKind::Everything | MaskKind::Geometry | MaskKind::Sky => None,
        MaskKind::Depth => Some(Bounds {
            count: 2,
            unit: "m",
            max: 100_000.0,
            tips: ["Nearest, in metres", "Farthest, in metres", "", ""],
        }),
        MaskKind::Facing => Some(Bounds {
            count: 2,
            unit: "",
            max: 1.0,
            tips: [
                "From: 0 is edge-on, 1 faces the camera",
                "To: 0 is edge-on, 1 faces the camera",
                "",
                "",
            ],
        }),
        MaskKind::Luma => Some(Bounds {
            count: 2,
            unit: "",
            max: 1_000.0,
            tips: ["Darkest luminance", "Brightest luminance", "", ""],
        }),
        MaskKind::Rect => Some(Bounds {
            count: 4,
            unit: "",
            max: 1.0,
            tips: [
                "Left, 0 to 1 across the screen",
                "Top, 0 to 1 down the screen",
                "Right, 0 to 1 across the screen",
                "Bottom, 0 to 1 down the screen",
            ],
        }),
    }
}

/// Is `word` of a selector its screen mask or its feather?
fn is_mask_word(word: &str) -> bool {
    if word.starts_with('~') {
        return true;
    }
    let body = word.strip_prefix('!').unwrap_or(word);
    let key = body.split_once(':').map_or(body, |(key, _)| key);
    MaskKind::ALL.iter().any(|kind| kind.key() == key)
}

/// The words of `select` that are neither its screen mask nor its feather: the family and
/// instance terms of P3 and P4, which [`mask::parse`] leaves out today. Split as it splits, with
/// `&` a separator wherever it is.
#[must_use]
pub fn other_terms(select: &str) -> Vec<&str> {
    select
        .split(|c: char| c == '&' || c.is_whitespace())
        .filter(|word| !word.is_empty() && !is_mask_word(word))
        .collect()
}

/// `select` with its screen mask and feather replaced by `mask`'s, and its other terms
/// ([`other_terms`]) kept after them.
#[must_use]
pub fn with_mask(select: &str, mask: &Mask) -> String {
    let mut out = mask.render();
    let others = other_terms(select);
    if !others.is_empty() {
        out.push_str(" & ");
        out.push_str(&others.join(" "));
    }
    out
}

/// `select` with its screen mask changed to `kind`, keeping its invert flag, its feather and its
/// other terms, with the kind's [`default_bounds`]. Unchanged when it already is that kind.
#[must_use]
pub fn with_kind(select: &str, kind: MaskKind) -> String {
    let (current, _) = mask::parse(select);
    if current.kind == kind {
        return select.to_owned();
    }
    let mask = Mask {
        kind,
        invert: current.invert,
        feather: current.feather,
        bounds: default_bounds(kind),
    };
    with_mask(select, &mask)
}

#[cfg(windows)]
pub(super) use process::Editor;

/// The Windows half: the panel's copy of the project, and the drawing.
#[cfg(windows)]
mod process {
    use std::path::Path;
    use std::sync::atomic::Ordering;
    use std::time::Instant;

    use hudhook::imgui::{StyleColor, Ui};

    use super::super::code::{CodeState, SAVE_DELAY};
    use super::super::files::{self, Job, Reply, Source, Written};
    use super::super::theme::{
        self, ButtonKind, ButtonSize, CardStyle, Font, SliderRow, Strip, fit_text,
    };
    use super::super::widgets::{self, Edit, categories};
    use super::super::{PARAM_EDITS, STACK_EDITS};
    use super::{
        Light, Row, adopt_reading, bounds_of, copy_of, insert_made, light, mask_preview,
        move_within_stage, place_for, remove_layer, rows, shown_path, solo_only, state_style,
        template_groups, vertex_light, with_kind, with_mask,
    };
    use crate::editor::annotations::ParamDecl;
    use crate::editor::cel;
    use crate::editor::mask::{self, MAX_FEATHER, MaskKind};
    use crate::editor::project::{self, Blend, Layer, Project};
    use crate::editor::templates::{self, Template};
    use crate::editor::worker::{self, LayerState, Status, layer_stages, silence_layers};
    use crate::editor::{LayerStage, write_layers};
    use crate::params;
    use crate::safety::{self, Mode};
    use crate::settings::LayerLook;
    use crate::vertex::ChipState;

    /// Projects this panel sent to the disk, newest last, kept so the worker reading back an older
    /// one is known for a late echo rather than an edit made elsewhere.
    const SENT_KEPT: usize = 8;

    /// The stack's share of the tab's height at most.
    const STACK_SHARE: f32 = 0.35;

    // Sizes at 1080p, from the Main, Code and Selector mockups.

    /// A stage heading in the stack: its padding above (the first heading's, then the others'),
    /// below, and its indent from the rows' edge.
    const HEADING_PAD_TOP_FIRST: f32 = 6.0;
    const HEADING_PAD_TOP: f32 = 8.0;
    const HEADING_PAD_BOTTOM: f32 = 2.0;
    const HEADING_INDENT: f32 = 6.0;
    /// A stack row's square solo button.
    const SOLO_SIDE: f32 = 22.0;
    /// The compact stack's stage column at the panel's usual width, and once the panel is at least
    /// [`WIDE_PANEL`] wide, as the Code sub-tab makes it.
    const STAGE_COLUMN: f32 = 96.0;
    const STAGE_COLUMN_WIDE: f32 = 110.0;
    const WIDE_PANEL: f32 = 600.0;
    /// The bar under the stack: its padding above, a side and below, and the gap between buttons.
    const BAR_PAD_TOP: f32 = 4.0;
    const BAR_PAD_X: f32 = 10.0;
    const BAR_PAD_BOTTOM: f32 = 10.0;
    const BAR_GAP: f32 = 4.0;
    /// The fields' grid: the gap between its columns and between its rows, and between a field's
    /// label and the field.
    const GRID_GAP: [f32; 2] = [12.0, 8.0];
    const FIELD_LABEL_GAP: f32 = 4.0;
    /// Extra room above a parameter heading after the first, and above the note under the list.
    const HEADING_EXTRA: f32 = 4.0;
    const NOTE_EXTRA: f32 = 2.0;
    /// The least the parameter list keeps when the panel is short; the inspector scrolls instead.
    const MIN_PARAMS_HEIGHT: f32 = 60.0;
    /// The footer: its padding under its rule, and the gap between its two lines.
    const FOOTER_PAD_TOP: f32 = 10.0;
    const FOOTER_GAP: f32 = 6.0;
    /// The Selector sub-tab: the gap between its blocks, its text field's height, the gap between
    /// the items of a row, a bound's field, the instance combo, the info card's gaps.
    const SELECTOR_GAP: f32 = 12.0;
    const SELECTOR_TEXT_HEIGHT: f32 = 28.0;
    const SELECTOR_ROW_GAP: f32 = 6.0;
    const BOUND_FIELD_WIDTH: f32 = 44.0;
    const INSTANCE_COMBO_WIDTH: f32 = 220.0;
    const CARD_GAP: f32 = 6.0;
    const CARD_ROW_GAP: f32 = 8.0;
    /// Pixels kept clear above the bottom padding of the Selector sub-tab, so its pinned buttons
    /// never make the tab scroll by a rounding.
    const PIN_SLACK: f32 = 1.0;

    /// Under a vertex layer's fields, where a pixel layer has Blend and Mask.
    const VERTEX_FIELDS_NOTE: &str = "Blend and mask do not apply to a vertex layer: it moves \
                                      the whole of every mesh, in every material group. Opacity \
                                      is how far towards its moved position each vertex goes.";

    /// Over a vertex layer's Selector sub-tab.
    const VERTEX_SELECTOR_NOTE: &str = "A vertex layer moves the whole of every mesh it \
                                        selects. A screen mask means nothing before the mesh is \
                                        on the screen, so Everything is the only one here.";

    /// The vertex stage's chip, under the mouse.
    const VERTEX_CHIP_TIP: &str = "The vertex stage: the vertex layers compile into one carrier, \
                                   which is spliced into the vertex shaders of each material \
                                   group the game draws, the groups on screen first. A group \
                                   moves once every one of its pipelines is built.";

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum SubTab {
        Parameters,
        Code,
        Selector,
    }

    impl SubTab {
        const ALL: [Self; 3] = [Self::Parameters, Self::Code, Self::Selector];
        const LABELS: [&'static str; 3] = ["Parameters", "Code", "Selector"];

        /// Does it show the compact stack, without the stack's buttons and the layer's fields?
        /// The Code and Selector mockups do, to leave the room to the code or the selector.
        fn compact(self) -> bool {
            self != Self::Parameters
        }

        /// The gap between the inspector's blocks: 12 in the Selector mockup, 10 elsewhere.
        fn gap(self) -> f32 {
            if self == Self::Selector {
                SELECTOR_GAP
            } else {
                theme::CONTENT_GAP
            }
        }
    }

    /// Something a stack row asked for, applied after the rows are drawn.
    enum Action {
        Select(u32),
        Enable(u32, bool),
        Solo(u32),
        SoloOnly(u32),
    }

    /// The Layers tab's state.
    pub(in crate::menu) struct Editor {
        /// The panel's copy of the project.
        project: Project,
        /// The project the worker last read, as last seen here.
        seen: Project,
        /// The last [`SENT_KEPT`] projects written, oldest first.
        sent: Vec<Project>,
        /// The copy has changes the worker has not read back yet.
        pending: bool,
        /// When the project is next written.
        save_due: Option<Instant>,
        selected: Option<u32>,
        sub: SubTab,
        code: CodeState,
        /// A new layer's file is being made.
        creating: bool,
        /// The last thing that went wrong with a file, shown over the inspector.
        note: String,
        /// The Selector's text while it is being typed, and the layer it is for. It is applied
        /// when the typing ends: Enter, a click elsewhere, another layer or another sub-tab.
        select_edit: Option<(u32, String)>,
    }

    impl Editor {
        pub(in crate::menu) fn new() -> Self {
            Self {
                project: Project::default(),
                seen: Project::default(),
                sent: Vec::new(),
                pending: false,
                save_due: None,
                selected: None,
                sub: SubTab::Parameters,
                code: CodeState::new(),
                creating: false,
                note: String::new(),
                select_edit: None,
            }
        }

        /// Is the Code sub-tab the one shown, which widens the panel?
        pub(in crate::menu) fn code_shown(&self) -> bool {
            self.sub == SubTab::Code && self.selected.is_some()
        }

        /// Is the Selector sub-tab the one shown, whose mockup keeps the toolbar to one line?
        pub(in crate::menu) fn selector_shown(&self) -> bool {
            self.sub == SubTab::Selector && self.selected.is_some()
        }

        /// The sub-tab shown, by its name in the telemetry file.
        pub(in crate::menu) fn sub_key(&self) -> &'static str {
            match self.sub {
                SubTab::Parameters => "parameters",
                SubTab::Code => "code",
                SubTab::Selector => "selector",
            }
        }

        /// Has the tab work left while the panel is closed: a change not yet read back, a save
        /// or a new layer's file on its way, or code not yet written.
        pub(in crate::menu) fn busy(&self) -> bool {
            self.pending || self.save_due.is_some() || self.creating || self.code.is_dirty()
        }

        /// The panel's copy of the project, for the Look tab's list of the stock cel layers.
        pub(in crate::menu) fn project(&self) -> &Project {
            &self.project
        }

        /// The values the project's stock cel layers hold (`cel::values_of`).
        pub(in crate::menu) fn cel_values(&self) -> LayerLook {
            cel::values_of(&self.project)
        }

        /// A preset's layer values: the stock cel layers the project has take them, and the ones
        /// the values need and the project lacks are made from their templates through the file
        /// thread, each at the head of its stage (`cel::apply`). Returns how many are being made.
        pub(in crate::menu) fn apply_cel(
            &mut self,
            values: &LayerLook,
            enabled: bool,
            dir: Option<&Path>,
        ) -> usize {
            let (changed, create) = cel::apply(&mut self.project, values, enabled);
            if changed {
                self.changed();
            }
            let Some(dir) = dir else {
                return 0;
            };
            let mut made = 0;
            for layer in create {
                let Some(stock) = cel::cel_layer(&layer.template) else {
                    continue;
                };
                if files::send(Job::Create {
                    layer,
                    source: Source::Text(stock.template().text.to_owned()),
                    layers: dir.join(project::LAYERS_DIRECTORY),
                    after: None,
                }) {
                    self.creating = true;
                    made += 1;
                }
            }
            made
        }

        /// Cel on or off for the stock cel layers (`cel::set_enabled`).
        pub(in crate::menu) fn set_cel_enabled(&mut self, enabled: bool) {
            if cel::set_enabled(&mut self.project, enabled) {
                self.changed();
            }
        }

        /// Show layer `id` in the inspector, on its Parameters sub-tab.
        pub(in crate::menu) fn select(&mut self, id: u32) {
            if self.project.layer(id).is_some() {
                self.end_select_edit();
                self.selected = Some(id);
                self.sub = SubTab::Parameters;
            }
        }

        /// Once a frame while the panel is open or [`Self::busy`]: the file thread's answers, the
        /// worker's view of the project, the saves that are due, and a pending change written into
        /// the parameter block.
        pub(in crate::menu) fn tick(&mut self, status: &Status, dir: Option<&Path>) {
            for reply in files::take_replies() {
                self.on_reply(reply);
            }
            self.sync(status);
            let Some(dir) = dir else {
                return;
            };
            if self.save_due.is_some_and(|due| Instant::now() >= due) {
                self.save(dir);
            }
            let state = self.code.open_id().and_then(|id| status.layers.get(&id));
            self.code.tick(dir, state);
            if self.pending {
                self.stage(status);
            }
        }

        fn on_reply(&mut self, reply: Reply) {
            if self.code.on_reply(&reply) {
                return;
            }
            match reply {
                Reply::Wrote {
                    what: Written::Project,
                    result: Err(why),
                } => self.note = format!("The project was not saved: {why}"),
                Reply::Created { mut layer, after } => {
                    self.creating = false;
                    if self.project.layer(layer.id).is_some() {
                        layer.id = self.project.next_id();
                    }
                    let id = layer.id;
                    let quiet = after.is_none() && cel::is_cel(&layer);
                    insert_made(&mut self.project, layer, after);
                    // A cel layer a preset made does not take the selection from the player.
                    if !quiet || self.selected.is_none() {
                        self.selected = Some(id);
                    }
                    self.changed();
                }
                Reply::CreateFailed { why } => {
                    self.creating = false;
                    self.note = format!("The layer was not made: {why}");
                }
                _ => {}
            }
        }

        /// Follow the worker's reading of the project file.
        fn sync(&mut self, status: &Status) {
            if status.project != self.seen {
                self.seen = status.project.clone();
                if adopt_reading(
                    &status.project,
                    &self.project,
                    &self.sent,
                    self.save_due.is_some(),
                ) {
                    self.project = status.project.clone();
                    self.sent.clear();
                }
            }
            // Nothing waits to be saved and the worker has what the panel has: the block is the
            // worker's to write again.
            if self.pending && self.save_due.is_none() && status.project == self.project {
                self.pending = false;
            }
        }

        /// The copy changed: write it into the block from now on, and to the disk soon.
        fn changed(&mut self) {
            self.pending = true;
            self.save_due = Some(Instant::now() + SAVE_DELAY);
            STACK_EDITS.fetch_add(1, Ordering::Relaxed);
        }

        fn save(&mut self, dir: &Path) {
            self.save_due = None;
            let sent = files::send(Job::Write {
                path: dir.join(project::FILE_NAME),
                text: project::render(&self.project),
                what: Written::Project,
            });
            if sent {
                self.sent.push(self.project.clone());
                if self.sent.len() > SENT_KEPT {
                    self.sent.remove(0);
                }
            } else {
                self.note = "The project cannot be saved: the panel's file thread is not running."
                    .to_owned();
            }
        }

        /// Write the copy's headers and parameters into the parameter block, and its drawing
        /// stages into `LAYER_STAGES`, exactly as the worker will once it reads the save. Only
        /// while `status` is the worker's newest packing (`worker::PACKING`): once the worker has
        /// staged a new version of a layer, a status taken before it describes registers that are
        /// no longer the live variants', and writing it would undo the worker's block until the
        /// next frame -- the very frame the new variants may be bound in.
        fn stage(&self, status: &Status) {
            let fidelity = safety::mode() == Mode::Fidelity;
            let mut current = false;
            params::stage(|block| {
                current = worker::PACKING.load(Ordering::Acquire) == status.packing;
                if !current {
                    return;
                }
                let _ = write_layers(block, &self.project, &status.slots, &status.params);
                if fidelity {
                    silence_layers(block);
                }
            });
            if current {
                let live: Vec<&[u32]> = status.live_layers.iter().map(Vec::as_slice).collect();
                safety::LAYER_STAGES.store(
                    layer_stages(&self.project, &live, &status.vertex_live),
                    Ordering::Release,
                );
            }
        }

        /// Apply the Selector's text, if it was being typed ([`Self::select_edit`]).
        fn end_select_edit(&mut self) {
            let Some((id, text)) = self.select_edit.take() else {
                return;
            };
            let applied = match self.project.layer_mut(id) {
                Some(layer) if layer.select != text => {
                    layer.select = text;
                    true
                }
                _ => false,
            };
            if applied {
                self.changed();
            }
        }

        /// The selected layer, made one that exists: the first when none is.
        fn settle_selection(&mut self) {
            if self
                .selected
                .is_none_or(|id| self.project.layer(id).is_none())
            {
                self.selected = self.project.layers.first().map(|layer| layer.id);
            }
        }

        /// The tab. `dir` is `<Game>/er-shader-editor`. The body it draws in has no padding: the
        /// stack, its bar and the inspector pad themselves as their mockups do.
        pub(in crate::menu) fn draw(&mut self, ui: &Ui, status: &Status, dir: Option<&Path>) {
            // The tick follows the worker only while the panel is open or busy, and it runs after
            // the drawing; a panel just opened shows what the worker has, not what it had.
            self.sync(status);
            let Some(dir) = dir else {
                theme::content(ui, "##inspector", || {
                    theme::note(
                        ui,
                        "The game's folder is not known, so there is no er-shader-editor folder \
                         to keep layers in.",
                    );
                });
                return;
            };
            self.settle_selection();
            if self.selected.is_none() {
                self.sub = SubTab::Parameters;
            }
            if self.sub != SubTab::Selector {
                self.end_select_edit();
            }
            let t = theme::current();
            let compact = self.sub.compact();
            let origin = ui.cursor_screen_pos().map(f32::round);
            let avail = ui.content_region_avail();
            let mut y = self.draw_stack(ui, status, compact, avail);
            if !compact {
                y = self.draw_stack_buttons(ui, dir, [origin[0], y], avail[0]);
            }
            ui.set_cursor_screen_pos([origin[0], y]);
            theme::rule(ui);
            ui.set_cursor_screen_pos([origin[0], y + t.stroke()]);
            // Up, Down, Dup and Del changed the stack: what is selected now.
            self.settle_selection();
            let index = self
                .selected
                .and_then(|id| self.project.layers.iter().position(|l| l.id == id));
            theme::content(ui, "##inspector", || {
                let _gap = theme::spacing(ui, self.sub.gap());
                if !self.note.is_empty() {
                    theme::text_wrapped(ui, Font::Label, theme::ERROR_TEXT, &self.note);
                }
                if let Some(index) = index {
                    self.draw_inspector(ui, status, dir, index);
                }
            });
        }

        /// The stack (Main mockup; compact in the Code and Selector mockups) in a region padded 6
        /// and 10, as tall as its rows up to [`STACK_SHARE`] of the tab, scrolling past that.
        /// Returns the y below it.
        fn draw_stack(&mut self, ui: &Ui, status: &Status, compact: bool, avail: [f32; 2]) -> f32 {
            let t = theme::current();
            let shown: Vec<Row> = rows(&self.project)
                .into_iter()
                .filter(|row| !compact || matches!(row, Row::Layer(_)))
                .collect();
            let row_height = t.px(theme::STACK_ROW_HEIGHT).round();
            let row_spacing = t.px(theme::STACK_ROW_SPACING).round();
            let heading_line = theme::line_height(ui, Font::Heading);
            let content = if shown.is_empty() {
                theme::line_height(ui, Font::Label)
            } else {
                let heights: f32 = shown
                    .iter()
                    .enumerate()
                    .map(|(at, row)| match row {
                        Row::Stage(_) => heading_height(at == 0, heading_line),
                        Row::Layer(_) => row_height,
                    })
                    .sum();
                heights + row_spacing * (shown.len() - 1) as f32
            };
            let pad = 2.0 * t.px(theme::STACK_PAD[1]);
            let cap = (avail[1] * STACK_SHARE).max(3.0 * row_height + pad);
            // A stroke of slack, so rounding never makes a stack that fits scroll.
            let height = (content + pad + t.stroke()).min(cap).ceil();
            let wide = avail[0] >= t.px(WIDE_PANEL);
            let stage_column = t
                .px(if wide {
                    STAGE_COLUMN_WIDE
                } else {
                    STAGE_COLUMN
                })
                .round();
            let mut actions = Vec::new();
            theme::region(ui, "##stack", theme::STACK_PAD, [0.0, height], || {
                let _spacing = theme::spacing(ui, theme::STACK_ROW_SPACING);
                if shown.is_empty() {
                    theme::quiet(ui, "No layers yet. + Layer adds one from a template.");
                }
                for (at, row) in shown.iter().enumerate() {
                    match *row {
                        Row::Stage(stage) => draw_heading(ui, stage, at == 0, status),
                        Row::Layer(id) => {
                            if let Some(layer) = self.project.layer(id) {
                                self.draw_row(
                                    ui,
                                    status,
                                    layer,
                                    compact,
                                    stage_column,
                                    &mut actions,
                                );
                            }
                        }
                    }
                }
            });
            let bottom = ui.item_rect_max()[1];
            for action in actions {
                match action {
                    Action::Select(id) => self.selected = Some(id),
                    Action::Enable(id, on) => {
                        if let Some(layer) = self.project.layer_mut(id) {
                            layer.enabled = on;
                        }
                        self.changed();
                    }
                    Action::Solo(id) => {
                        if let Some(layer) = self.project.layer_mut(id) {
                            layer.solo = !layer.solo;
                        }
                        self.changed();
                    }
                    Action::SoloOnly(id) => {
                        solo_only(&mut self.project, id);
                        self.selected = Some(id);
                        self.changed();
                    }
                }
            }
            bottom
        }

        /// One layer's row, 28 high (Main mockup): its light, which a click turns the layer on or
        /// off; in the compact stack its stage in a column of its own (Code and Selector
        /// mockups); its name; `S` for solo on the selected row of the full stack and on any
        /// soloed row; its compile time or state at the end. The selected row is gold.
        fn draw_row(
            &self,
            ui: &Ui,
            status: &Status,
            layer: &Layer,
            compact: bool,
            stage_column: f32,
            actions: &mut Vec<Action>,
        ) {
            let t = theme::current();
            let id = layer.id;
            let _id = ui.push_id(format!("layer{id}"));
            let selected = self.selected == Some(id);
            let at = ui.cursor_screen_pos().map(f32::round);
            let width = ui.content_region_avail()[0].max(1.0);
            let height = t.px(theme::STACK_ROW_HEIGHT).round();
            let pad = t.px(theme::STACK_ROW_PAD_X).round();
            let gap = t.px(theme::STACK_ROW_GAP).round();
            let dot = t.px(theme::DOT_SIZE);
            let max = [at[0] + width, at[1] + height];
            let middle = at[1] + height * 0.5;
            let paint = theme::painter(ui);
            let radius = t.px(theme::FRAME_RADIUS);
            if selected {
                paint.fill(at, max, theme::SELECTED_ROW_BG, radius);
                theme::register_rect("stack_selected_row", [at[0], at[1], width, height]);
            } else if ui.is_window_hovered() && ui.is_mouse_hovering_rect(at, max) {
                paint.fill(at, max, theme::TAB_HOVER_BG, radius);
            }

            // The buttons over the row come before the row's own, which Dear ImGui then lets
            // them keep the mouse from.
            let (lamp, state) = light(&self.project, layer, status.layers.get(&id));
            ui.set_cursor_screen_pos(at);
            if ui.invisible_button("##light", [(pad + dot + gap * 0.5).round(), height]) {
                actions.push(Action::Enable(id, !layer.enabled));
            }
            if ui.is_item_hovered() {
                theme::tooltip(
                    ui,
                    &[if layer.enabled {
                        "On. A click on the light turns the layer off; it never recompiles."
                    } else {
                        "Off. A click on the light turns the layer on; it never recompiles."
                    }],
                );
            }
            paint.dot([at[0] + pad + dot * 0.5, middle], theme::light_colour(lamp));

            let (state_font, state_colour) = state_style(lamp, selected);
            let mut right = max[0] - pad - theme::text_width(ui, state_font, &state);
            theme::with_font(ui, state_font, || {
                let line = ui.text_line_height();
                paint.text([right, middle - line * 0.5], state_colour, &state);
            });
            if layer.solo || (selected && !compact) {
                let side = t.px(SOLO_SIDE).round();
                right -= gap + side;
                ui.set_cursor_screen_pos([right, (middle - side * 0.5).round()]);
                if theme::square_toggle(ui, "S##solo", layer.solo) {
                    actions.push(Action::Solo(id));
                }
                if ui.is_item_hovered() {
                    theme::tooltip(
                        ui,
                        &[
                            "Solo: while any layer is soloed, only soloed layers draw. Alt+click a \
                           row to solo that layer alone.",
                        ],
                    );
                }
            }

            let mut x = at[0] + pad + dot + gap;
            if compact {
                let colour = if selected {
                    theme::SELECTED_STAGE
                } else {
                    theme::TEXT_QUIET
                };
                let stage = fit_text(
                    ui,
                    Font::Small,
                    &layer.stage.label().to_uppercase(),
                    stage_column,
                );
                theme::with_font(ui, Font::Small, || {
                    let line = ui.text_line_height();
                    paint.text([x, middle - line * 0.5], colour, &stage);
                });
                x += stage_column + gap;
            }
            let (name_font, name_colour) = if selected {
                (Font::Medium, theme::SELECTED_NAME)
            } else if lamp == Light::Off {
                (Font::Body, theme::TEXT_QUIET)
            } else {
                (Font::Body, theme::TEXT)
            };
            let name = if layer.name.is_empty() {
                "(no name)"
            } else {
                layer.name.as_str()
            };
            let name = fit_text(ui, name_font, name, (right - gap - x).max(0.0));
            theme::with_font(ui, name_font, || {
                let line = ui.text_line_height();
                paint.text([x, middle - line * 0.5], name_colour, &name);
            });

            ui.set_cursor_screen_pos(at);
            if ui.invisible_button("##row", [width, height]) {
                actions.push(if ui.io().key_alt {
                    Action::SoloOnly(id)
                } else {
                    Action::Select(id)
                });
            }
            if let Some(_menu) = ui.begin_popup_context_with_label("##row-menu") {
                if ui.selectable(if layer.enabled { "Turn off" } else { "Turn on" }) {
                    actions.push(Action::Enable(id, !layer.enabled));
                }
                if ui.selectable(if layer.solo { "Unsolo" } else { "Solo" }) {
                    actions.push(Action::Solo(id));
                }
                if ui.selectable("Solo this layer alone") {
                    actions.push(Action::SoloOnly(id));
                }
            }
        }

        /// The bar under the stack (Main mockup): + Layer in gold, then Up, Down, Dup and Del, 26
        /// high and 4 apart, padded 4 above and 10 a side and below. Returns the y below it.
        fn draw_stack_buttons(&mut self, ui: &Ui, dir: &Path, at: [f32; 2], width: f32) -> f32 {
            let t = theme::current();
            let size = ButtonSize::STACK;
            let height = t.px(size.height).round();
            let side = t.px(BAR_PAD_X).round();
            let top = at[1] + t.px(BAR_PAD_TOP).round();
            ui.set_cursor_screen_pos([at[0] + side, top]);
            let mut bar = theme::Row::with_width(ui, size.height, BAR_GAP, width - 2.0 * side);
            let add = "+ Layer##add";
            bar.next(
                ui,
                [
                    theme::button_width(ui, add, ButtonKind::Primary, size),
                    height,
                ],
            );
            {
                let _busy = ui.begin_disabled(self.creating);
                if theme::button(ui, add, ButtonKind::Primary, size) {
                    ui.open_popup("##templates");
                }
            }
            theme::register_item(ui, "add_layer_button");
            if let Some(_popup) = ui.begin_popup("##templates") {
                for (at, (stage, group)) in template_groups().into_iter().enumerate() {
                    if at > 0 {
                        theme::gap(ui, HEADING_EXTRA);
                    }
                    theme::section_heading(ui, stage.label());
                    for template in group {
                        if ui.selectable(format!("{}##{}", template.label, template.key)) {
                            self.add(template, dir);
                        }
                        if ui.is_item_hovered() {
                            theme::tooltip(
                                ui,
                                &[format!(
                                    "{} Starts at {}.",
                                    template.summary,
                                    template.stage.label()
                                )],
                            );
                        }
                    }
                }
            }
            if let Some(id) = self.selected {
                let mut secondary = |label: &str| {
                    let width = theme::button_width(ui, label, ButtonKind::Secondary, size);
                    bar.next(ui, [width, height]);
                    theme::button(ui, label, ButtonKind::Secondary, size)
                };
                if secondary("Up##up") && move_within_stage(&mut self.project, id, true) {
                    self.changed();
                }
                if secondary("Down##down") && move_within_stage(&mut self.project, id, false) {
                    self.changed();
                }
                {
                    let _busy = ui.begin_disabled(self.creating);
                    if secondary("Dup##dup") {
                        self.duplicate(id, dir);
                    }
                }
                if secondary("Del##del") {
                    self.delete(id, dir);
                }
                if ui.is_item_hovered() {
                    theme::tooltip(
                        ui,
                        &["Takes the layer out of the project. Its file stays in layers/."],
                    );
                }
            }
            bar.end(ui);
            top + height + t.px(BAR_PAD_BOTTOM).round()
        }

        fn add(&mut self, template: &Template, dir: &Path) {
            let (layer, text) = templates::new_layer(&self.project, template, None);
            let after = place_for(&self.project, layer.stage, self.selected);
            self.creating = files::send(Job::Create {
                layer,
                source: Source::Text(text),
                layers: dir.join(project::LAYERS_DIRECTORY),
                after,
            });
        }

        fn duplicate(&mut self, id: u32, dir: &Path) {
            let Some(layer) = self.project.layer(id).cloned() else {
                return;
            };
            if self.code.open_id() == Some(id) {
                // The copy is made after this write: the file thread keeps their order.
                self.code.flush(dir);
            }
            let layers = dir.join(project::LAYERS_DIRECTORY);
            self.creating = files::send(Job::Create {
                layer: copy_of(&layer, self.project.next_id()),
                source: Source::CopyOf(layers.join(&layer.file)),
                layers,
                after: Some(id),
            });
        }

        fn delete(&mut self, id: u32, dir: &Path) {
            if self.code.open_id() == Some(id) {
                self.code.close(dir);
            }
            self.selected = remove_layer(&mut self.project, id);
            self.changed();
        }

        /// The selected layer's inspector: its fields on the Parameters sub-tab, the sub-tabs,
        /// and the sub-tab's body. A sub-tab clicked is drawn from the next frame, with the stack
        /// in its form.
        fn draw_inspector(&mut self, ui: &Ui, status: &Status, dir: &Path, index: usize) {
            if !self.sub.compact() {
                self.draw_fields(ui, status, index);
            }
            let active = SubTab::ALL
                .iter()
                .position(|sub| *sub == self.sub)
                .unwrap_or(0);
            let clicked = theme::tab_strip(ui, "layer", &SubTab::LABELS, active, Strip::Sub);
            match self.sub {
                SubTab::Parameters => self.draw_parameters(ui, status, dir, index),
                SubTab::Code => {
                    let layer = self.project.layers[index].clone();
                    let state = status.layers.get(&layer.id);
                    self.code.draw(ui, &self.project, &layer, state, dir);
                }
                SubTab::Selector => self.draw_selector(ui, index),
            }
            if let Some(clicked) = clicked {
                self.sub = SubTab::ALL[clicked];
            }
        }

        /// The layer's fields (Main mockup): Name, Stage, Blend and Mask in two columns 12 apart,
        /// each under its 12-pixel label, then Opacity.
        fn draw_fields(&mut self, ui: &Ui, status: &Status, index: usize) {
            let t = theme::current();
            let mut layer = self.project.layers[index].clone();
            let before = layer.clone();
            let at = ui.cursor_screen_pos().map(f32::round);
            let width = ui.content_region_avail()[0].max(1.0);
            let [column_gap, row_gap] = GRID_GAP.map(|gap| t.px(gap).round());
            let column = ((width - column_gap) * 0.5).floor().max(1.0);
            let field_top = theme::line_height(ui, Font::Label) + t.px(FIELD_LABEL_GAP).round();
            let cell_height = field_top + t.px(theme::FRAME_HEIGHT).round();
            let paint = theme::painter(ui);
            let cell = |column_at: f32, row_at: f32, words: &str| {
                let x = at[0] + column_at * (column + column_gap);
                let y = at[1] + row_at * (cell_height + row_gap);
                theme::with_font(ui, Font::Label, || {
                    paint.text([x, y], theme::TEXT_QUIET, words);
                });
                ui.set_cursor_screen_pos([x, y + field_top]);
            };

            cell(0.0, 0.0, "Name");
            ui.set_next_item_width(column);
            ui.input_text("##name", &mut layer.name).build();
            cell(1.0, 0.0, "Stage");
            if let Some(_combo) = theme::combo(ui, "##stage", layer.stage.label(), column) {
                for stage in LayerStage::ALL {
                    if ui
                        .selectable_config(stage.label())
                        .selected(stage == layer.stage)
                        .build()
                    {
                        layer.stage = stage;
                    }
                }
            }
            if layer.stage.is_vertex() {
                // A vertex layer moves positions: there is no colour to blend and no screen to
                // mask, so the row is the vertex stage's chip across both columns instead.
                cell(0.0, 1.0, "Vertex stage");
                draw_vertex_chip(
                    ui,
                    status.vertex_state,
                    &status.vertex_chip,
                    2.0 * column + column_gap,
                );
            } else {
                cell(0.0, 1.0, "Blend");
                if let Some(_combo) = theme::combo(ui, "##blend", layer.blend.label(), column) {
                    for blend in Blend::ALL {
                        if ui
                            .selectable_config(blend.label())
                            .selected(blend == layer.blend)
                            .build()
                        {
                            layer.blend = blend;
                        }
                    }
                }
                cell(1.0, 1.0, "Mask");
                let (current, _) = mask::parse(&layer.select);
                let preview = mask_preview(current.kind, layer.stage);
                if let Some(_combo) = theme::combo(ui, "##mask", &preview, column) {
                    for kind in MaskKind::ALL
                        .into_iter()
                        .filter(|kind| kind.available_at(layer.stage))
                    {
                        if ui
                            .selectable_config(kind.label())
                            .selected(kind == current.kind)
                            .build()
                        {
                            layer.select = with_kind(&layer.select, kind);
                        }
                    }
                } else if ui.is_item_hovered() {
                    theme::tooltip(
                        ui,
                        &["Its bounds, invert and feather are on the Selector tab."],
                    );
                }
            }
            ui.set_cursor_screen_pos(at);
            ui.dummy([width, 2.0 * cell_height + row_gap]);
            if layer.stage.is_vertex() {
                theme::note(ui, VERTEX_FIELDS_NOTE);
            }

            let opacity = format!("{:.2}", layer.opacity);
            if SliderRow::OPACITY.draw(
                ui,
                "Opacity##opacity",
                &mut layer.opacity,
                [0.0, 1.0],
                2,
                &opacity,
            ) {
                // The two places it shows, a typed value too, which Dear ImGui does not round.
                layer.opacity = (layer.opacity * 100.0).round() / 100.0;
            }
            if layer.stage.is_vertex() && ui.is_item_hovered() {
                theme::tooltip(
                    ui,
                    &[
                        "How far towards its moved position each vertex goes: 0 leaves the mesh \
                       where the game put it, 1 moves it all the way.",
                    ],
                );
            }
            if layer != before {
                self.project.layers[index] = layer;
                self.changed();
            }
        }

        /// The Parameters sub-tab (Main mockup): the parameters under their category headings in
        /// a list that scrolls, the note that none of them recompiles, and the footer pinned to
        /// the bottom.
        fn draw_parameters(&mut self, ui: &Ui, status: &Status, dir: &Path, index: usize) {
            let t = theme::current();
            let layer = &self.project.layers[index];
            let state = status.layers.get(&layer.id);
            let (lamp, _) = light(&self.project, layer, state);
            let file = layer.file.clone();
            let words = state.map_or_else(
                || "not read by the worker yet".to_owned(),
                LayerState::label,
            );
            let width = ui.content_region_avail()[0];
            let words_height = theme::with_font(ui, Font::Body, || {
                ui.calc_text_size_with_opts(&words, false, (width - footer_indent()).max(1.0))[1]
            });
            let footer = t.stroke()
                + t.px(FOOTER_PAD_TOP).round()
                + words_height
                + t.px(FOOTER_GAP).round()
                + theme::line_height(ui, Font::MonoSmall);
            let list = (ui.content_region_avail()[1] - footer - t.px(theme::CONTENT_GAP).round())
                .max(t.px(MIN_PARAMS_HEIGHT))
                .floor();
            theme::region(ui, "##params", [0.0, 0.0], [0.0, list], || {
                self.draw_param_list(ui, status, index);
            });
            draw_footer(ui, dir, lamp, &words, state.is_none(), &file);
        }

        /// The parameters, each a row of [`widgets::draw`], under their `ui_category` headings
        /// (Main mockup's `LIFT` and `GENERAL`) when any has one.
        fn draw_param_list(&mut self, ui: &Ui, status: &Status, index: usize) {
            let id = self.project.layers[index].id;
            let decls = match status.params.get(&id) {
                None => {
                    theme::note(
                        ui,
                        match status.layers.get(&id) {
                            Some(LayerState::Error { .. }) => {
                                "This layer has no version that compiled yet, so it has no \
                                 parameters to show. The Code tab says what is wrong."
                            }
                            _ => "The worker has not read this layer yet.",
                        },
                    );
                    return;
                }
                Some(decls) if decls.iter().all(|decl| decl.hidden) => {
                    theme::note(ui, "This layer has no parameters to show.");
                    return;
                }
                Some(decls) => decls,
            };
            let groups = categories(decls);
            let named = groups.iter().any(|(category, _)| category.is_some());
            let shown: Vec<&ParamDecl> = groups
                .iter()
                .flat_map(|(_, members)| members.iter().copied())
                .collect();
            let label_width = widgets::label_width(ui, &shown, ui.content_region_avail()[0]);
            for (at, (category, members)) in groups.iter().enumerate() {
                if named {
                    if at > 0 {
                        theme::gap(ui, HEADING_EXTRA);
                    }
                    theme::section_heading(ui, category.unwrap_or("General"));
                }
                for decl in members {
                    let _id = ui.push_id(&decl.name);
                    let layer = &mut self.project.layers[index];
                    match widgets::draw(ui, decl, layer.param(&decl.name), label_width) {
                        Edit::None => continue,
                        Edit::Set(value) => layer.set_param(&decl.name, value),
                        Edit::Reset => layer.params.retain(|(name, _)| name != &decl.name),
                    }
                    PARAM_EDITS.fetch_add(1, Ordering::Relaxed);
                    self.changed();
                }
            }
            theme::gap(ui, NOTE_EXTRA);
            theme::note(
                ui,
                if self.project.layers[index].stage.is_vertex() {
                    "Changes show in the frame at once: sliders, toggles, solo and opacity never \
                     recompile the carrier."
                } else {
                    "Changes show in the frame at once: sliders, toggles, solo, opacity, blend and \
                     mask never recompile."
                },
            );
        }

        /// The Selector sub-tab (Selector mockup), from what there is today: the selector as
        /// text, its screen mask with the mask's bounds and invert, and its feather. The family
        /// chips, the instance, the info card and Preview, Blink and Pick are drawn as the mockup
        /// has them, do nothing, and say which phase brings them.
        fn draw_selector(&mut self, ui: &Ui, index: usize) {
            let t = theme::current();
            let (id, stage) = {
                let layer = &self.project.layers[index];
                (layer.id, layer.stage)
            };
            if self
                .select_edit
                .as_ref()
                .is_some_and(|(editing, _)| *editing != id)
            {
                self.end_select_edit();
            }

            field_label(ui, "As text");
            let committed = self.project.layers[index].select.clone();
            let mut text = self
                .select_edit
                .as_ref()
                .map_or_else(|| committed.clone(), |(_, text)| text.clone());
            {
                let _mono = theme::font(ui, Font::Mono);
                let _padding = theme::frame(ui, SELECTOR_TEXT_HEIGHT, theme::FRAME_PAD_X);
                let _field = [
                    StyleColor::FrameBg,
                    StyleColor::FrameBgHovered,
                    StyleColor::FrameBgActive,
                ]
                .map(|slot| ui.push_style_color(slot, theme::CODE_BG));
                ui.set_next_item_width(ui.content_region_avail()[0]);
                ui.input_text("##as-text", &mut text)
                    .enter_returns_true(true)
                    .build();
            }
            if ui.is_item_hovered() {
                theme::tooltip(
                    ui,
                    &["The layer's selector. Enter, or a click elsewhere, applies what is typed."],
                );
            }
            if ui.is_item_active() {
                self.select_edit = Some((id, text));
            } else {
                if text != committed {
                    self.select_edit = Some((id, text));
                }
                self.end_select_edit();
            }

            let mut select = self.project.layers[index].select.clone();
            let original = select.clone();
            let (mut mask, warnings) = mask::parse(&select);
            for warning in &warnings {
                theme::text_wrapped(ui, Font::Label, theme::WARNING_TEXT, warning);
            }

            if stage.is_vertex() {
                theme::note(ui, VERTEX_SELECTOR_NOTE);
            }
            field_label(ui, "Screen mask");
            let frame = t.px(theme::FRAME_HEIGHT).round();
            let before = mask;
            let mut picked = None;
            let kinds: Vec<MaskKind> = MaskKind::ALL
                .into_iter()
                .filter(|kind| kind.available_at(stage))
                .collect();
            let preview = mask_preview(mask.kind, stage);
            let combo_width = (kinds
                .iter()
                .map(|kind| kind.label())
                .chain([preview.as_str()])
                .map(|words| ui.calc_text_size(words)[0])
                .fold(0.0, f32::max)
                + 2.0 * t.px(theme::FRAME_PAD_X)
                + frame)
                .round();
            let mut row = theme::Row::begin(ui, theme::FRAME_HEIGHT, SELECTOR_ROW_GAP);
            row.next(ui, [combo_width, frame]);
            if let Some(_combo) = theme::combo(ui, "##screen-mask", &preview, combo_width) {
                for kind in &kinds {
                    if ui
                        .selectable_config(kind.label())
                        .selected(*kind == mask.kind)
                        .build()
                    {
                        picked = Some(*kind);
                    }
                }
            }
            if let Some(bounds) = bounds_of(mask.kind) {
                let invert = t.px(theme::CHECKBOX_SIZE).round()
                    + t.px(theme::CHECKBOX_GAP).round()
                    + ui.calc_text_size("Invert")[0];
                row.next_right(ui, [invert, ui.text_line_height()]);
                theme::checkbox(ui, "Invert##mask", &mut mask.invert);
                let field = t.px(BOUND_FIELD_WIDTH).round();
                for (at, tip) in bounds.tips.iter().enumerate().take(bounds.count) {
                    if at == bounds.count / 2 {
                        word(ui, &mut row, "to");
                    }
                    row.next(ui, [field, frame]);
                    ui.set_next_item_width(field);
                    let edited = {
                        let _mono = theme::font(ui, Font::Mono);
                        let _padding =
                            theme::frame(ui, theme::FRAME_HEIGHT, theme::MONO_FRAME_PAD_X);
                        ui.input_float(format!("##bound{at}"), &mut mask.bounds[at])
                            .display_format("%g")
                            .build()
                    };
                    if edited {
                        let value = mask.bounds[at];
                        mask.bounds[at] = if value.is_finite() {
                            value.clamp(0.0, bounds.max)
                        } else {
                            0.0
                        };
                    }
                    if ui.is_item_hovered() {
                        theme::tooltip(ui, &[tip]);
                    }
                }
                if !bounds.unit.is_empty() {
                    word(ui, &mut row, bounds.unit);
                }
            }
            row.end(ui);
            if let Some(kind) = picked {
                select = with_kind(&select, kind);
                mask = mask::parse(&select).0;
            } else if mask != before {
                select = with_mask(&select, &mask);
            }

            field_label(
                ui,
                "Family (P3): only * and ? are wildcards; brackets are literal",
            );
            let mut row = theme::Row::begin(ui, theme::FRAME_HEIGHT, SELECTOR_ROW_GAP);
            let add = "+ Family##family";
            row.next(
                ui,
                [
                    theme::button_width(ui, add, ButtonKind::Secondary, ButtonSize::SMALL),
                    frame,
                ],
            );
            disabled_button(
                ui,
                add,
                ButtonKind::Secondary,
                ButtonSize::SMALL,
                "Family selectors arrive with P3.",
            );
            note_after(ui, &mut row, "arrives with P3");
            row.end(ui);

            field_label(ui, "Instance (P4)");
            let mut row = theme::Row::begin(ui, theme::FRAME_HEIGHT, SELECTOR_ROW_GAP);
            let width = t.px(INSTANCE_COMBO_WIDTH).round();
            row.next(ui, [width, frame]);
            theme::unavailable(ui, || {
                let _never = theme::combo(ui, "##instance", "Any", width);
            });
            note_after(ui, &mut row, "arrives with P4");
            row.end(ui);

            if mask.kind.feathers() {
                let mut feather = mask.feather;
                let shown = format!("{feather:.0} px");
                if SliderRow::FEATHER.draw(
                    ui,
                    "Feather##selector",
                    &mut feather,
                    [0.0, MAX_FEATHER],
                    0,
                    &shown,
                ) {
                    mask.feather = feather.round();
                    select = with_mask(&select, &mask);
                }
            }

            theme::card(ui, "##selector-info", CardStyle::INFO, || {
                let _gap = theme::spacing(ui, CARD_GAP);
                let badge = "Per shader";
                let size = [
                    theme::text_width(ui, Font::MediumLabel, badge)
                        + 2.0 * t.px(theme::CHIP_PAD_X).round()
                        + 2.0 * t.stroke(),
                    t.px(theme::BADGE_HEIGHT).round(),
                ];
                let mut row = theme::Row::begin(ui, theme::BADGE_HEIGHT, CARD_ROW_GAP);
                row.next(ui, size);
                theme::badge(ui, badge);
                note_after(ui, &mut row, "arrives with P3");
                row.end(ui);
                theme::text_wrapped(
                    ui,
                    Font::Label,
                    theme::TEXT_SECONDARY,
                    "Matches by shader, so anything sharing a character material matches too.",
                );
            });

            draw_selector_actions(ui);

            if select != original {
                self.project.layers[index].select = select;
                self.changed();
            }
        }
    }

    /// The height of a stage heading in the stack, its `line` included.
    fn heading_height(first: bool, line: f32) -> f32 {
        let t = theme::current();
        let top = if first {
            HEADING_PAD_TOP_FIRST
        } else {
            HEADING_PAD_TOP
        };
        t.px(top).round() + line + t.px(HEADING_PAD_BOTTOM).round()
    }

    /// A stage's heading in the stack (Main mockup's `LIGHTING`): spaced semibold capitals,
    /// padded 6 above the first and 8 above the others, 2 below, set in 6 from the rows' edge.
    /// The Vertex heading carries the vertex stage's chip at its other end, its light and its
    /// words in 11-pixel Plex, where a row has its state.
    fn draw_heading(ui: &Ui, stage: LayerStage, first: bool, status: &Status) {
        let t = theme::current();
        let at = ui.cursor_screen_pos().map(f32::round);
        let width = ui.content_region_avail()[0].max(1.0);
        let line = theme::line_height(ui, Font::Heading);
        let top = if first {
            HEADING_PAD_TOP_FIRST
        } else {
            HEADING_PAD_TOP
        };
        let text_at = [
            at[0] + t.px(HEADING_INDENT).round(),
            at[1] + t.px(top).round(),
        ];
        ui.set_cursor_screen_pos(text_at);
        theme::section_heading(ui, stage.label());
        let heading_right = ui.item_rect_max()[0];
        if stage.is_vertex() {
            let pad = t.px(theme::STACK_ROW_PAD_X).round();
            let gap = t.px(theme::STACK_ROW_GAP).round();
            let dot = t.px(theme::DOT_SIZE);
            let room = (at[0] + width - pad - heading_right - 2.0 * gap - dot).max(0.0);
            let words = fit_text(ui, Font::Small, &status.vertex_chip, room);
            let words_width = theme::text_width(ui, Font::Small, &words);
            let right = at[0] + width - pad;
            let middle = text_at[1] + line * 0.5;
            let paint = theme::painter(ui);
            paint.dot(
                [right - words_width - gap - dot * 0.5, middle],
                theme::light_colour(vertex_light(status.vertex_state)),
            );
            theme::with_font(ui, Font::Small, || {
                let small = ui.text_line_height();
                paint.text(
                    [right - words_width, middle - small * 0.5],
                    theme::TEXT_QUIET,
                    &words,
                );
            });
            let chip_min = [right - words_width - gap - dot, text_at[1]];
            if ui.is_window_hovered() && ui.is_mouse_hovering_rect(chip_min, [right, middle + line])
            {
                theme::tooltip(ui, &[VERTEX_CHIP_TIP]);
            }
        }
        ui.set_cursor_screen_pos(at);
        ui.dummy([width, heading_height(first, line)]);
    }

    /// The vertex stage's chip as a field (a vertex layer's fields, where Blend and Mask are on a
    /// pixel layer): its light and its words in 12-pixel Plex, `width` wide and a frame high, at
    /// the cursor.
    fn draw_vertex_chip(ui: &Ui, state: ChipState, words: &str, width: f32) {
        let t = theme::current();
        let at = ui.cursor_screen_pos().map(f32::round);
        let height = t.px(theme::FRAME_HEIGHT).round();
        let dot = t.px(theme::DOT_SIZE);
        let gap = t.px(theme::DOT_GAP).round();
        let middle = at[1] + height * 0.5;
        let paint = theme::painter(ui);
        paint.dot(
            [at[0] + dot * 0.5, middle],
            theme::light_colour(vertex_light(state)),
        );
        let x = at[0] + dot + gap;
        let shown = fit_text(ui, Font::Label, words, (at[0] + width - x).max(0.0));
        theme::with_font(ui, Font::Label, || {
            let line = ui.text_line_height();
            paint.text([x, middle - line * 0.5], theme::TEXT_SECONDARY, &shown);
        });
        ui.dummy([width.max(1.0), height]);
        if ui.is_item_hovered() {
            theme::tooltip(ui, &[VERTEX_CHIP_TIP]);
        }
    }

    /// The footer's indent for the words after its light: the dot and a row's gap.
    fn footer_indent() -> f32 {
        let t = theme::current();
        (t.px(theme::DOT_SIZE) + t.px(theme::STACK_ROW_GAP)).round()
    }

    /// The Parameters footer (Main mockup), under the list: a rule across the content, the
    /// layer's light and state `words` (quiet while the worker has said nothing), and its file's
    /// path in quiet mono, the full path under the mouse.
    fn draw_footer(ui: &Ui, dir: &Path, lamp: Light, words: &str, quiet: bool, file: &str) {
        let t = theme::current();
        let at = ui.cursor_screen_pos().map(f32::round);
        let width = ui.content_region_avail()[0].max(1.0);
        let paint = theme::painter(ui);
        paint.hline(at[0], at[0] + width, at[1], theme::RULE);
        let top = at[1] + t.stroke() + t.px(FOOTER_PAD_TOP).round();
        let line = theme::line_height(ui, Font::Body);
        paint.dot(
            [at[0] + t.px(theme::DOT_SIZE) * 0.5, top + line * 0.5],
            theme::light_colour(lamp),
        );
        ui.set_cursor_screen_pos([at[0] + footer_indent(), top]);
        let colour = if quiet {
            theme::TEXT_QUIET
        } else {
            theme::TEXT
        };
        theme::text_wrapped(ui, Font::Body, colour, words);
        ui.set_cursor_screen_pos([at[0], ui.item_rect_max()[1] + t.px(FOOTER_GAP).round()]);
        theme::mono_quiet(ui, &shown_path(file));
        if ui.is_item_hovered() {
            let full = dir.join(project::LAYERS_DIRECTORY).join(file);
            theme::tooltip(ui, &[full.display().to_string()]);
        }
    }

    /// A field's label over it (Main and Selector mockups): 12-pixel quiet words, the field 4
    /// pixels below. Painted, not an item, so the field is the block's one item.
    fn field_label(ui: &Ui, words: &str) {
        let t = theme::current();
        let at = ui.cursor_screen_pos().map(f32::round);
        theme::with_font(ui, Font::Label, || {
            theme::painter(ui).text(at, theme::TEXT_QUIET, words);
            let below = at[1] + ui.text_line_height() + t.px(FIELD_LABEL_GAP).round();
            ui.set_cursor_screen_pos([at[0], below]);
        });
    }

    /// Quiet 13-pixel `words` next in `row`, painted: the Screen mask row's "to" and "m".
    fn word(ui: &Ui, row: &mut theme::Row, words: &str) {
        let size = ui.calc_text_size(words);
        let at = row.next(ui, size);
        theme::painter(ui).text(at, theme::TEXT_QUIET, words);
    }

    /// Quiet 12-pixel `words` next in `row`, as an item: "arrives with P3".
    fn note_after(ui: &Ui, row: &mut theme::Row, words: &str) {
        let size = [
            theme::text_width(ui, Font::Label, words),
            theme::line_height(ui, Font::Label),
        ];
        row.next(ui, size);
        theme::quiet(ui, words);
    }

    /// A button of `kind` and `size` that does nothing yet ([`theme::unavailable_button`]): a
    /// control whose feature a later phase brings. `why` shows under the mouse.
    fn disabled_button(ui: &Ui, label: &str, kind: ButtonKind, size: ButtonSize, why: &str) {
        if theme::unavailable_button(ui, label, kind, size) {
            theme::tooltip(ui, &[why]);
        }
    }

    /// Preview mask, Blink matches and Pick from the world (Selector mockup), pinned to the
    /// bottom of the tab and wrapping as the mockup's row does, with the line that says which
    /// phase brings each.
    fn draw_selector_actions(ui: &Ui) {
        let t = theme::current();
        let size = ButtonSize::LARGE;
        let actions = [
            (
                "Preview mask",
                "Arrives with P3: tints the pixels the selector matches.",
            ),
            (
                "Blink matches",
                "Arrives with P3: blinks what the selector matches.",
            ),
            (
                "Pick from the world",
                "Arrives with P4: a click on the frame makes a selector.",
            ),
        ];
        let widths: Vec<f32> = actions
            .iter()
            .map(|(label, _)| theme::button_width(ui, label, ButtonKind::Secondary, size))
            .collect();
        let gap = t.px(SELECTOR_ROW_GAP).round();
        let height = t.px(size.height).round();
        let at = ui.cursor_screen_pos().map(f32::round);
        let placed = theme::flow(&widths, ui.content_region_avail()[0], gap);
        let lines = placed.last().map_or(1, |(line, _)| line + 1) as f32;
        let block = lines * (height + gap) + theme::line_height(ui, Font::Label);
        let bottom = ui.window_pos()[1] + ui.window_size()[1]
            - t.px(theme::CONTENT_PAD[1]).round()
            - PIN_SLACK;
        let top = at[1].max((bottom - block).floor());
        for ((label, why), (line, x)) in actions.iter().zip(placed) {
            ui.set_cursor_screen_pos([at[0] + x, top + line as f32 * (height + gap)]);
            disabled_button(ui, label, ButtonKind::Secondary, size, why);
        }
        ui.set_cursor_screen_pos([at[0], top + lines * (height + gap)]);
        theme::quiet(ui, "Preview and Blink arrive with P3, Pick with P4.");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::project::Blend;

    fn layer(id: u32, stage: LayerStage) -> Layer {
        Layer::new(id, &format!("layer {id}"), &format!("{id}-x.hlsl"), stage)
    }

    fn project(layers: &[(u32, LayerStage)]) -> Project {
        Project {
            layers: layers.iter().map(|&(id, stage)| layer(id, stage)).collect(),
            ..Project::default()
        }
    }

    fn order(project: &Project) -> Vec<u32> {
        project.layers.iter().map(|layer| layer.id).collect()
    }

    #[test]
    fn the_stack_is_grouped_by_stage_in_the_order_stages_run() {
        let stack = project(&[
            (3, LayerStage::TonemapPost),
            (5, LayerStage::Lighting),
            (4, LayerStage::TonemapPost),
            (7, LayerStage::Albedo),
        ]);
        assert_eq!(
            rows(&stack),
            vec![
                Row::Stage(LayerStage::Lighting),
                Row::Layer(5),
                Row::Stage(LayerStage::Albedo),
                Row::Layer(7),
                Row::Stage(LayerStage::TonemapPost),
                Row::Layer(3),
                Row::Layer(4),
            ]
        );
        assert!(rows(&Project::default()).is_empty());
    }

    #[test]
    fn up_and_down_move_among_the_layers_of_one_stage() {
        let mut stack = project(&[
            (3, LayerStage::TonemapPost),
            (5, LayerStage::Lighting),
            (4, LayerStage::TonemapPost),
        ]);
        // 4 passes 3, over the Lighting layer between them.
        assert!(move_within_stage(&mut stack, 4, true));
        assert_eq!(order(&stack), vec![4, 5, 3]);
        // Nothing of its stage above it, nothing below the last.
        assert!(!move_within_stage(&mut stack, 4, true));
        assert!(!move_within_stage(&mut stack, 5, false));
        assert!(move_within_stage(&mut stack, 4, false));
        assert_eq!(order(&stack), vec![3, 5, 4]);
        assert!(!move_within_stage(&mut stack, 99, true));
    }

    #[test]
    fn alt_click_solos_one_layer_and_again_solos_none() {
        let mut stack = project(&[
            (1, LayerStage::TonemapPost),
            (2, LayerStage::TonemapPost),
            (3, LayerStage::Lighting),
        ]);
        stack.layers[1].solo = true;
        solo_only(&mut stack, 1);
        let soloed: Vec<bool> = stack.layers.iter().map(|layer| layer.solo).collect();
        assert_eq!(soloed, vec![true, false, false]);
        solo_only(&mut stack, 1);
        assert!(stack.layers.iter().all(|layer| !layer.solo));
    }

    #[test]
    fn deleting_selects_the_row_below_else_above() {
        let mut stack = project(&[
            (3, LayerStage::TonemapPost),
            (5, LayerStage::Lighting),
            (4, LayerStage::TonemapPost),
        ]);
        // Shown order is 5, 3, 4.
        assert_eq!(remove_layer(&mut stack, 3), Some(4));
        assert_eq!(remove_layer(&mut stack, 4), Some(5));
        assert_eq!(remove_layer(&mut stack, 5), None);
        assert!(stack.layers.is_empty());
        assert_eq!(remove_layer(&mut stack, 5), None);
    }

    #[test]
    fn a_copy_keeps_every_setting_but_its_id_name_file_and_solo() {
        let mut grade = layer(3, LayerStage::TonemapPost);
        grade.solo = true;
        grade.opacity = 0.5;
        grade.blend = Blend::Screen;
        grade.select = "luma:0.5..1".to_owned();
        grade.set_param("Strength", crate::editor::project::ParamValue::Number(0.25));
        grade.template = "cel_saturation".to_owned();
        let copy = copy_of(&grade, 9);
        assert_eq!(
            (copy.id, copy.name.as_str(), copy.file.as_str(), copy.solo),
            (9, "layer 3 copy", "", false)
        );
        assert_eq!(
            (copy.opacity, copy.blend, &copy.select, &copy.params),
            (0.5, Blend::Screen, &grade.select, &grade.params)
        );
        // A copy of a stock cel layer is the player's own: a preset leaves it alone.
        assert!(copy.template.is_empty());
    }

    #[test]
    fn a_made_cel_layer_goes_to_the_head_of_its_stage_and_any_other_where_it_was_asked() {
        let mut stack = project(&[(1, LayerStage::TonemapPost), (2, LayerStage::Lighting)]);
        let mut steps = layer(3, LayerStage::TonemapPost);
        steps.template = "cel_brightness_steps".to_owned();
        insert_made(&mut stack, steps, None);
        insert_made(&mut stack, layer(4, LayerStage::TonemapPost), None);
        let mut saturation = layer(5, LayerStage::TonemapPost);
        saturation.template = "cel_saturation".to_owned();
        insert_made(&mut stack, saturation, None);
        let mut asked = layer(6, LayerStage::TonemapPost);
        asked.template = "cel_saturation".to_owned();
        insert_made(&mut stack, asked, Some(1));
        assert_eq!(order(&stack), vec![5, 3, 1, 6, 2, 4]);
    }

    #[test]
    fn a_new_layer_goes_with_its_stage() {
        let mut stack = project(&[
            (1, LayerStage::Lighting),
            (2, LayerStage::TonemapPost),
            (3, LayerStage::TonemapPost),
        ]);
        // After the selected layer when it is at the new layer's stage, else after that stage's
        // last layer, else at the end.
        assert_eq!(place_for(&stack, LayerStage::TonemapPost, Some(2)), Some(2));
        assert_eq!(place_for(&stack, LayerStage::TonemapPost, Some(1)), Some(3));
        assert_eq!(place_for(&stack, LayerStage::Albedo, Some(1)), None);
        insert_after(&mut stack, layer(4, LayerStage::TonemapPost), Some(2));
        insert_after(&mut stack, layer(5, LayerStage::Albedo), None);
        assert_eq!(order(&stack), vec![1, 2, 4, 3, 5]);
    }

    #[test]
    fn only_a_project_made_elsewhere_replaces_the_panels_copy() {
        let first = project(&[(1, LayerStage::TonemapPost)]);
        let mut second = first.clone();
        second.layers[0].opacity = 0.5;
        let mut third = first.clone();
        third.layers[0].opacity = 0.25;
        let mut elsewhere = first.clone();
        elsewhere.layers[0].name = "renamed in a Linux editor".to_owned();
        let sent = [second.clone(), third.clone()];
        // The worker read back the latest save: nothing to take.
        assert!(!adopt_reading(&third, &third, &sent, false));
        // It read an older save late, after a newer one went out: not taken, the newer follows.
        assert!(!adopt_reading(&second, &third, &sent, false));
        // An edit made elsewhere is taken, unless a save of the panel's waits to overwrite it.
        assert!(adopt_reading(&elsewhere, &third, &sent, false));
        assert!(!adopt_reading(&elsewhere, &third, &sent, true));
        // With nothing written yet, the first project the worker reads is taken.
        assert!(adopt_reading(&first, &Project::default(), &[], false));
    }

    #[test]
    fn the_light_says_live_busy_error_or_off() {
        let mut stack = project(&[(1, LayerStage::TonemapPost), (2, LayerStage::TonemapPost)]);
        let live = LayerState::Live {
            compile_ms: 21.4,
            verified: 7,
        };
        let first = stack.layers[0].clone();
        assert_eq!(
            light(&stack, &first, Some(&live)),
            (Light::Live, "21 ms".to_owned())
        );
        assert_eq!(light(&stack, &first, None).0, Light::Busy);
        assert_eq!(
            light(&stack, &first, Some(&LayerState::Pending)).0,
            Light::Busy
        );
        assert_eq!(
            light(
                &stack,
                &first,
                Some(&LayerState::Carrier { compile_ms: 30.2 })
            ),
            (Light::Live, "30 ms, carrier".to_owned())
        );
        let error = LayerState::Error {
            summary: "x".to_owned(),
            running: true,
        };
        assert_eq!(
            light(&stack, &first, Some(&error)),
            (Light::Error, "error, last good running".to_owned())
        );
        stack.layers[1].solo = true;
        assert_eq!(
            light(&stack, &first, Some(&live)),
            (Light::Off, "muted by solo".to_owned())
        );
        stack.layers[0].enabled = false;
        let first = stack.layers[0].clone();
        assert_eq!(
            light(&stack, &first, Some(&live)),
            (Light::Off, "off".to_owned())
        );
    }

    #[test]
    fn a_rows_end_is_mono_for_a_time_and_red_for_an_error() {
        assert_eq!(
            state_style(Light::Live, false),
            (Font::MonoSmall, theme::TEXT_QUIET)
        );
        assert_eq!(
            state_style(Light::Live, true),
            (Font::MonoSmall, theme::TEXT_SECONDARY)
        );
        assert_eq!(
            state_style(Light::Error, false),
            (Font::Small, theme::ERROR_TEXT)
        );
        assert_eq!(
            state_style(Light::Busy, true),
            (Font::Small, theme::SELECTED_NAME)
        );
        assert_eq!(
            state_style(Light::Off, true),
            (Font::Small, theme::TEXT_QUIET)
        );
    }

    #[test]
    fn picking_a_mask_keeps_its_invert_and_feather() {
        let (luma, _) = mask::parse(&with_kind("!rect:0,0..1,1 ~2px", MaskKind::Luma));
        assert_eq!(
            luma,
            Mask {
                kind: MaskKind::Luma,
                invert: true,
                feather: 2.0,
                bounds: default_bounds(MaskKind::Luma),
            }
        );
        // The same kind leaves the text, bounds and all, as it was.
        assert_eq!(with_kind("depth:5..9m", MaskKind::Depth), "depth:5..9m");
        assert_eq!(with_kind("depth:5..9m", MaskKind::Everything), "everything");
        for kind in MaskKind::ALL {
            let (parsed, warnings) = mask::parse(&with_kind("everything", kind));
            assert!(warnings.is_empty(), "{kind:?}: {warnings:?}");
            assert_eq!(parsed.kind, kind);
            assert_eq!(parsed.bounds, default_bounds(kind), "{kind:?}");
        }
    }

    #[test]
    fn rewriting_the_mask_keeps_the_terms_it_does_not_own() {
        let select = "depth:0..30m & family:C[*] !M[Water]* ~2px";
        assert_eq!(other_terms(select), vec!["family:C[*]", "!M[Water]*"]);
        let (mut depth, _) = mask::parse(select);
        depth.bounds = [1.0, 20.0, 0.0, 0.0];
        depth.feather = 4.0;
        let written = with_mask(select, &depth);
        assert_eq!(written, "depth:1..20m ~4px & family:C[*] !M[Water]*");
        let (read, warnings) = mask::parse(&written);
        assert_eq!(read, depth);
        assert_eq!(warnings.len(), 2, "{warnings:?}");
        // Picking another kind keeps them too; Sky takes no feather.
        assert_eq!(
            with_kind(select, MaskKind::Sky),
            "sky & family:C[*] !M[Water]*"
        );
        assert!(other_terms("!rect:0,0..1,1 ~2px").is_empty());
        assert!(other_terms("").is_empty());
    }

    #[test]
    fn each_mask_kind_has_the_bounds_its_text_holds() {
        for kind in MaskKind::ALL {
            let text = with_kind("everything", kind);
            let bounds = bounds_of(kind);
            // Exactly the kinds whose text carries numbers have fields.
            assert_eq!(bounds.is_some(), text.contains(':'), "{kind:?}: {text}");
            if let Some(bounds) = bounds {
                assert!(matches!(bounds.count, 2 | 4), "{kind:?}");
                assert!(
                    bounds.tips[..bounds.count]
                        .iter()
                        .all(|tip| !tip.is_empty())
                );
                let defaults = default_bounds(kind);
                assert!(
                    defaults[..bounds.count]
                        .iter()
                        .all(|value| (0.0..=bounds.max).contains(value)),
                    "{kind:?}"
                );
            }
        }
        assert_eq!(
            bounds_of(MaskKind::Rect).map(|bounds| bounds.count),
            Some(4)
        );
        assert_eq!(
            bounds_of(MaskKind::Depth).map(|bounds| bounds.unit),
            Some("m")
        );
    }

    #[test]
    fn the_footer_and_the_mask_combo_say_what_they_show() {
        assert_eq!(
            shown_path("3-warm-grade.hlsl"),
            "<Game>\\er-shader-editor\\layers\\3-warm-grade.hlsl"
        );
        assert_eq!(
            mask_preview(MaskKind::Depth, LayerStage::Lighting),
            "Depth range"
        );
        assert_eq!(
            mask_preview(MaskKind::Depth, LayerStage::TonemapPost),
            "Depth range (Lighting only)"
        );
        // A vertex layer moves whole meshes: every mask but Everything is marked, the ones every
        // pixel stage has too.
        assert_eq!(
            mask_preview(MaskKind::Everything, LayerStage::Vertex),
            MaskKind::Everything.label()
        );
        assert_eq!(
            mask_preview(MaskKind::Rect, LayerStage::Vertex),
            format!("{} (not at Vertex)", MaskKind::Rect.label())
        );
    }

    #[test]
    fn the_vertex_stage_heads_the_stack_and_its_chip_has_a_light() {
        let stack = project(&[
            (3, LayerStage::TonemapPost),
            (8, LayerStage::Vertex),
            (5, LayerStage::Lighting),
        ]);
        // Geometry moves before any pixel runs, so the Vertex group comes first.
        assert_eq!(
            rows(&stack),
            vec![
                Row::Stage(LayerStage::Vertex),
                Row::Layer(8),
                Row::Stage(LayerStage::Lighting),
                Row::Layer(5),
                Row::Stage(LayerStage::TonemapPost),
                Row::Layer(3),
            ]
        );
        assert_eq!(place_for(&stack, LayerStage::Vertex, Some(3)), Some(8));
        // The "+ Layer" menu has every template once, under its stage, Vertex first and its
        // proof lift at the top.
        let groups = template_groups();
        assert_eq!(groups[0].0, LayerStage::Vertex);
        assert_eq!(groups[0].1[0].key, "proof_lift");
        let keys: Vec<&str> = groups[0].1.iter().map(|t| t.key).collect();
        assert_eq!(
            keys,
            [
                "proof_lift",
                "wobble",
                "sway",
                "breathe",
                "proof_lift_parity"
            ]
        );
        assert_eq!(
            groups.iter().map(|(_, group)| group.len()).sum::<usize>(),
            TEMPLATES.len()
        );
        assert!(
            groups
                .iter()
                .all(|(stage, group)| group.iter().all(|t| t.stage == *stage))
        );
        assert_eq!(vertex_light(ChipState::Idle), Light::Off);
        assert_eq!(vertex_light(ChipState::Preparing), Light::Busy);
        assert_eq!(vertex_light(ChipState::Live), Light::Live);
    }
}
