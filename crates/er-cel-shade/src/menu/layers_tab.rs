//! The Layers tab (design 9): the layer stack grouped by stage, and under it the selected layer's
//! inspector with its Parameters and Code sub-tabs.
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
//! Rows are grouped by stage in the order the stages run ([`rows`]); Up and Down move a layer
//! among the layers of its own stage, since the order across stages means nothing. Each row has
//! an on/off box, a status light ([`light`]), the name, `S` for solo, and its compile time or
//! state. Alt+click on a row solos that layer alone ([`solo_only`]). "+ Layer" makes a file from
//! a template ([`crate::editor::templates`]); Dup copies the selected layer's file; Del takes the
//! layer out of the project and leaves its file in `layers/`.
//!
//! The stack's bookkeeping is pure and tested on the host; the drawing is the Windows half.

use crate::editor::LayerStage;
use crate::editor::mask::{self, Mask, MaskKind};
use crate::editor::project::{Layer, Project};
use crate::editor::worker::LayerState;

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
/// file is named when the copy of the file is made.
#[must_use]
pub fn copy_of(layer: &Layer, id: u32) -> Layer {
    Layer {
        id,
        name: format!("{} copy", layer.name),
        file: String::new(),
        solo: false,
        ..layer.clone()
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
    }
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

/// `select` with its screen mask changed to `kind`, keeping its invert flag and feather, with the
/// kind's [`default_bounds`]. Unchanged when it already is that kind.
#[must_use]
pub fn with_kind(select: &str, kind: MaskKind) -> String {
    let (current, _) = mask::parse(select);
    if current.kind == kind {
        return select.to_owned();
    }
    Mask {
        kind,
        invert: current.invert,
        feather: current.feather,
        bounds: default_bounds(kind),
    }
    .render()
}

#[cfg(windows)]
pub(super) use process::Editor;

/// The Windows half: the panel's copy of the project, and the drawing.
#[cfg(windows)]
mod process {
    use std::path::Path;
    use std::sync::atomic::Ordering;
    use std::time::Instant;

    use hudhook::imgui::{Drag, StyleColor, Ui};

    use super::super::code::{CodeState, SAVE_DELAY};
    use super::super::files::{self, Job, Reply, Source, Written};
    use super::super::widgets::{self, Edit, categories};
    use super::super::{BUTTON_ON, ERROR_TEXT, HEADING_TEXT, PARAM_EDITS, STACK_EDITS, status_dot};
    use super::{
        Row, adopt_reading, copy_of, insert_after, light, move_within_stage, place_for,
        remove_layer, rows, solo_only, with_kind,
    };
    use crate::editor::mask::{self, MaskKind};
    use crate::editor::project::{self, Blend, Layer, Project};
    use crate::editor::templates::{self, TEMPLATES, Template};
    use crate::editor::worker::{self, LayerState, Status, layer_stages, silence_layers};
    use crate::editor::{LayerStage, write_layers};
    use crate::params;
    use crate::safety::{self, Mode};

    /// Projects this panel sent to the disk, newest last, kept so the worker reading back an older
    /// one is known for a late echo rather than an edit made elsewhere.
    const SENT_KEPT: usize = 8;

    /// The stack's share of the tab's height at most.
    const STACK_SHARE: f32 = 0.35;

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum SubTab {
        Parameters,
        Code,
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
        /// The last thing that went wrong with a file, shown under the stack.
        note: String,
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
            }
        }

        /// Is the Code sub-tab the one shown, which widens the panel?
        pub(in crate::menu) fn code_shown(&self) -> bool {
            self.sub == SubTab::Code && self.selected.is_some()
        }

        /// Has the tab work left while the panel is closed: a change not yet read back, a save
        /// or a new layer's file on its way, or code not yet written.
        pub(in crate::menu) fn busy(&self) -> bool {
            self.pending || self.save_due.is_some() || self.creating || self.code.is_dirty()
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
                    insert_after(&mut self.project, layer, after);
                    self.selected = Some(id);
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
                safety::LAYER_STAGES.store(layer_stages(&self.project, &live), Ordering::Release);
            }
        }

        /// The tab. `dir` is `<Game>/er-shader-editor`.
        pub(in crate::menu) fn draw(&mut self, ui: &Ui, status: &Status, dir: Option<&Path>) {
            // The tick follows the worker only while the panel is open or busy, and it runs after
            // the drawing; a panel just opened shows what the worker has, not what it had.
            self.sync(status);
            let Some(dir) = dir else {
                ui.text_wrapped(
                    "The game's folder is not known, so there is no er-shader-editor folder to \
                     keep layers in.",
                );
                return;
            };
            if self
                .selected
                .is_none_or(|id| self.project.layer(id).is_none())
            {
                self.selected = self.project.layers.first().map(|layer| layer.id);
            }
            self.draw_stack(ui, status);
            self.draw_stack_buttons(ui, dir);
            if !self.note.is_empty() {
                let _colour = ui.push_style_color(StyleColor::Text, ERROR_TEXT);
                ui.text_wrapped(&self.note);
            }
            ui.separator();
            match self
                .selected
                .and_then(|id| self.project.layers.iter().position(|l| l.id == id))
            {
                Some(index) => self.draw_inspector(ui, status, dir, index),
                None => {
                    self.sub = SubTab::Parameters;
                    ui.text_disabled("No layers yet. + Layer adds one from a template.");
                }
            }
        }

        fn draw_stack(&mut self, ui: &Ui, status: &Status) {
            let all = rows(&self.project);
            let row = ui.frame_height_with_spacing();
            let wanted = all.len().max(1) as f32 * row + row * 0.5;
            let cap = (ui.content_region_avail()[1] * STACK_SHARE).max(row * 3.0);
            let Some(_stack) = ui
                .child_window("##stack")
                .size([0.0, wanted.min(cap)])
                .begin()
            else {
                return;
            };
            if all.is_empty() {
                ui.text_disabled("No layers yet.");
            }
            let mut actions = Vec::new();
            for shown in all {
                match shown {
                    Row::Stage(stage) => {
                        ui.text_colored(HEADING_TEXT, stage.label().to_ascii_uppercase());
                    }
                    Row::Layer(id) => {
                        if let Some(layer) = self.project.layer(id) {
                            self.draw_row(ui, status, layer, &mut actions);
                        }
                    }
                }
            }
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
        }

        fn draw_row(&self, ui: &Ui, status: &Status, layer: &Layer, actions: &mut Vec<Action>) {
            let id = layer.id;
            let _id = ui.push_id(format!("layer{id}"));
            let mut on = layer.enabled;
            if ui.checkbox("##on", &mut on) {
                actions.push(Action::Enable(id, on));
            }
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text("On or off. Never recompiles.");
            }
            ui.same_line();
            let (lamp, state) = light(&self.project, layer, status.layers.get(&id));
            status_dot(ui, lamp);
            ui.same_line();
            let button = ui.frame_height();
            let spacing = ui.clone_style().item_spacing[0];
            let right = button + spacing + ui.calc_text_size(&state)[0];
            let name_width = (ui.content_region_avail()[0] - right - spacing).max(button);
            let name = if layer.name.is_empty() {
                "(no name)"
            } else {
                layer.name.as_str()
            };
            if ui
                .selectable_config(format!("{name}##row"))
                .selected(self.selected == Some(id))
                .size([name_width, 0.0])
                .build()
            {
                actions.push(if ui.io().key_alt {
                    Action::SoloOnly(id)
                } else {
                    Action::Select(id)
                });
            }
            ui.same_line();
            let colour = layer
                .solo
                .then(|| ui.push_style_color(StyleColor::Button, BUTTON_ON));
            if ui.button_with_size("S", [button, button]) {
                actions.push(Action::Solo(id));
            }
            drop(colour);
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text(
                    "Solo: while any layer is soloed, only soloed layers draw. Alt+click a row \
                     to solo that layer alone.",
                );
            }
            ui.same_line();
            ui.text_disabled(&state);
        }

        fn draw_stack_buttons(&mut self, ui: &Ui, dir: &Path) {
            {
                let _busy = ui.begin_disabled(self.creating);
                if ui.button("+ Layer") {
                    ui.open_popup("##templates");
                }
            }
            if let Some(_popup) = ui.begin_popup("##templates") {
                for template in &TEMPLATES {
                    if ui.selectable(format!("{}##{}", template.label, template.key)) {
                        self.add(template, dir);
                    }
                    if ui.is_item_hovered() {
                        let _tooltip = ui.begin_tooltip();
                        ui.text(format!(
                            "{} Starts at {}.",
                            template.summary,
                            template.stage.label()
                        ));
                    }
                }
            }
            let Some(id) = self.selected else {
                return;
            };
            ui.same_line();
            if ui.button("Up") && move_within_stage(&mut self.project, id, true) {
                self.changed();
            }
            ui.same_line();
            if ui.button("Down") && move_within_stage(&mut self.project, id, false) {
                self.changed();
            }
            ui.same_line();
            {
                let _busy = ui.begin_disabled(self.creating);
                if ui.button("Dup") {
                    self.duplicate(id, dir);
                }
            }
            ui.same_line();
            if ui.button("Del") {
                self.delete(id, dir);
            }
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text("Takes the layer out of the project. Its file stays in layers/.");
            }
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

        fn draw_inspector(&mut self, ui: &Ui, status: &Status, dir: &Path, index: usize) {
            let mut layer = self.project.layers[index].clone();
            let before = layer.clone();
            let style = ui.clone_style();
            let spacing = style.item_spacing[0];
            let label_width = ui.calc_text_size("Opacity")[0] + spacing;
            let start = ui.cursor_pos()[0];
            let column = (ui.content_region_avail()[0] - spacing) / 2.0;
            let cell = |at: usize, label: &str| {
                let x = start + at as f32 * (column + spacing);
                if at != 0 {
                    ui.same_line_with_pos(x);
                }
                ui.align_text_to_frame_padding();
                ui.text_disabled(label);
                ui.same_line_with_pos(x + label_width);
                ui.set_next_item_width((column - label_width).max(1.0));
            };

            cell(0, "Name");
            ui.input_text("##name", &mut layer.name).build();
            cell(1, "Stage");
            if let Some(_combo) = ui.begin_combo("##stage", layer.stage.label()) {
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
            cell(0, "Blend");
            if let Some(_combo) = ui.begin_combo("##blend", layer.blend.label()) {
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
            cell(1, "Mask");
            let (current, _) = mask::parse(&layer.select);
            let preview = if current.kind.available_at(layer.stage) {
                current.kind.label().to_owned()
            } else {
                format!("{} (Lighting only)", current.kind.label())
            };
            if let Some(_combo) = ui.begin_combo("##mask", preview) {
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
            }
            draw_mask_bounds(ui, &mut layer.select);
            ui.align_text_to_frame_padding();
            ui.text_disabled("Opacity");
            ui.same_line_with_pos(start + label_width);
            ui.set_next_item_width(-1.0);
            ui.slider_config("##opacity", 0.0_f32, 1.0)
                .display_format("%.2f")
                .build(&mut layer.opacity);
            if layer != before {
                self.project.layers[index] = layer;
                self.changed();
            }

            if let Some(_tabs) = ui.tab_bar("##layer-tabs") {
                if let Some(_tab) = ui.tab_item("Parameters") {
                    self.sub = SubTab::Parameters;
                    self.draw_parameters(ui, status, dir, index);
                }
                if let Some(_tab) = ui.tab_item("Code") {
                    self.sub = SubTab::Code;
                    let layer = self.project.layers[index].clone();
                    let state = status.layers.get(&layer.id);
                    self.code.draw(ui, &self.project, &layer, state, dir);
                }
                // The Selector sub-tab -- the selector text with its precision chip and live
                // match (design 8) -- arrives with the family selectors of P3.
            }
        }

        fn draw_parameters(&mut self, ui: &Ui, status: &Status, dir: &Path, index: usize) {
            let id = self.project.layers[index].id;
            let state = status.layers.get(&id);
            match status.params.get(&id) {
                None => ui.text_wrapped(match state {
                    Some(LayerState::Error { .. }) => {
                        "This layer has no version that compiled yet, so it has no parameters to \
                         show. The Code tab says what is wrong."
                    }
                    _ => "The worker has not read this layer yet.",
                }),
                Some(decls) if decls.iter().all(|decl| decl.hidden) => {
                    ui.text_disabled("This layer has no parameters to show.");
                }
                Some(decls) => {
                    let groups = categories(decls);
                    let named = groups.iter().any(|(category, _)| category.is_some());
                    for (category, members) in groups {
                        if named {
                            ui.spacing();
                            ui.text_colored(
                                HEADING_TEXT,
                                category.map_or_else(|| "GENERAL".to_owned(), str::to_uppercase),
                            );
                        }
                        for decl in members {
                            let _id = ui.push_id(&decl.name);
                            let layer = &mut self.project.layers[index];
                            let edit = widgets::draw(ui, decl, layer.param(&decl.name));
                            match edit {
                                Edit::None => continue,
                                Edit::Set(value) => layer.set_param(&decl.name, value),
                                Edit::Reset => layer.params.retain(|(name, _)| name != &decl.name),
                            }
                            PARAM_EDITS.fetch_add(1, Ordering::Relaxed);
                            self.changed();
                        }
                    }
                    ui.spacing();
                    ui.text_disabled(
                        "Changes show in the frame at once: sliders, toggles, solo, opacity, \
                         blend and mask never recompile.",
                    );
                }
            }
            ui.separator();
            let layer = &self.project.layers[index];
            let (lamp, _) = light(&self.project, layer, state);
            status_dot(ui, lamp);
            ui.same_line();
            match state {
                Some(state) => ui.text_wrapped(state.label()),
                None => ui.text_disabled("not read by the worker yet"),
            }
            ui.text_disabled(
                dir.join(project::LAYERS_DIRECTORY)
                    .join(&layer.file)
                    .display()
                    .to_string(),
            );
        }
    }

    /// The bounds, invert flag and feather of `select`'s mask, for the kinds that have them.
    fn draw_mask_bounds(ui: &Ui, select: &mut String) {
        let (mut mask, _) = mask::parse(select);
        let before = mask;
        let (label, count, speed, max) = match mask.kind {
            MaskKind::Everything | MaskKind::Geometry | MaskKind::Sky => return,
            MaskKind::Depth => ("Metres##bounds", 2, 0.1, 100_000.0),
            MaskKind::Facing => ("Facing##bounds", 2, 0.005, 1.0),
            MaskKind::Luma => ("Luminance##bounds", 2, 0.01, 1_000.0),
            MaskKind::Rect => ("x0 y0 x1 y1##bounds", 4, 0.005, 1.0),
        };
        Drag::new(label)
            .range(0.0_f32, max)
            .speed(speed)
            .display_format("%.3f")
            .build_array(ui, &mut mask.bounds[..count]);
        ui.checkbox("Invert##mask", &mut mask.invert);
        if mask.kind.feathers() {
            ui.same_line();
            ui.set_next_item_width(ui.content_region_avail()[0] * 0.5);
            Drag::new("Feather px##mask")
                .range(0.0_f32, mask::MAX_FEATHER)
                .speed(0.1)
                .display_format("%.1f")
                .build(ui, &mut mask.feather);
        }
        if mask != before {
            *select = mask.render();
        }
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
        let copy = copy_of(&grade, 9);
        assert_eq!(
            (copy.id, copy.name.as_str(), copy.file.as_str(), copy.solo),
            (9, "layer 3 copy", "", false)
        );
        assert_eq!(
            (copy.opacity, copy.blend, &copy.select, &copy.params),
            (0.5, Blend::Screen, &grade.select, &grade.params)
        );
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
}
