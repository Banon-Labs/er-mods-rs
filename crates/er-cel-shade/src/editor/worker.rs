//! The editor's worker (design 7.5 to 7.7): the thread `er-shader-editor-worker`, which turns the
//! files a user edits into live variants.
//!
//! # Each tick
//!
//! Every [`POLL_PERIOD`] the worker:
//!
//! 1. reads `er-shader-editor/project.ershade` and every layer file it names again when its size
//!    or modification time changed, and prepares each changed layer ([`super::prepare`]: the
//!    annotations, the lint, the packer). A layer that fails keeps its last good version in the
//!    stack ([`Layers`]) and gets `diag/<id>.txt`;
//! 2. generates the source of every port kind of each port stage from the stack
//!    ([`super::codegen::generate`]) and queues the stage when any of its kinds' sources is neither
//!    the live variant's nor the one last tried. At most one compile per stage waits, and a newer
//!    edit replaces it ([`Queue`]);
//! 3. compiles every kind of a queued stage with `-D ER_VARIANT_ID=<kind << 24 | serial>`,
//!    verifies each blob against every live port record of its kind ([`crate::verify`], all seven
//!    rules), and creates one variant per record through the `CreatePipelineState` trampoline from
//!    the record's stream with the pixel shader replaced. Only when every kind of the stage got
//!    that far does anything change: the layers move to the new version, the parameter block is
//!    packed for it, and the table swap to every variant of the stage is handed to the next
//!    present in the same breath ([`crate::table`]'s `defer`), which publishes the block and
//!    applies the swap together, so a variant never reads a block packed for another version. The
//!    scan runs again between compiles, so an edit made during one replaces what is queued behind
//!    it. An empty stack needs no compiler: its source is the embedded port's, so the embedded
//!    blob is published again;
//! 4. gives the live variant to a port the game created after it was published;
//! 5. writes every layer's header and parameters into the next parameter block
//!    ([`super::write_layers`]) and the stages with a drawing layer compiled into a live variant
//!    into `crate::safety::LAYER_STAGES`, which the next present ORs into `STAGE_ACTIVE`;
//! 6. watches the probe for the first frame a published variant drew, for the edit-to-pixels
//!    latency.
//!
//! Each publish logs one line per kind beginning `editor: edit -> variant <id>` with the time each
//! step took: lint, compile, verify, create and publish, and the first probe frame on a later line.
//!
//! # A stage is never split
//!
//! Lighting runs in two port kinds and Tonemap in two (design 7.3). Their variants are built from
//! one stack and published in one swap, so the two passes of a stage always carry the same
//! version of its layers, and a layer's last good version ([`Layers`]) is the one compiled into
//! the live variants. The parameter block is packed from those versions ([`Layers::live_params`]),
//! never from a newer one still being compiled, so the registers a live variant reads always hold
//! the uniforms it was compiled with, and the panel's widgets edit what actually runs.
//!
//! # A failure keeps the last good variant
//!
//! A source that does not compile, a blob a rule refuses, a pipeline the driver refuses and a
//! compiler that gave up on a source leave every kind of the stage on its live variant. The layers
//! to blame -- the ones the compiler's errors name, else the ones edited since they last made it
//! into a variant ([`culprits`]) -- go back to their last good version in the stack and show
//! Error, with the compiler's output or the rule-by-rule report in `diag/<id>.txt`. That puts the
//! stack back on the live source, so other layers can still be edited, and a broken layer stays
//! out until its file changes again: the very same text saved again is recognised and keeps its
//! error instead of compiling again ([`Layers::fail`]). With no compiler at all the edited layers
//! wait the same way, showing why, and come back when the compiler's files change.
//!
//! A crash, a timeout or an answer out of step says nothing certain about the source, so the stage
//! is sent again, up to [`TRANSIENT_RETRIES`] times, before its edits are blamed. A panic while
//! building a stage ends that build alone: the variants it made are released, its edited layers
//! show an internal error, and the stage can be built again.
//!
//! # Slots of layers a live variant still reads
//!
//! A variant reads its layers' slots of the parameter block. A layer removed from the project
//! keeps its slot, written as off, while a live variant still has it compiled in, so a new layer
//! never lands in a slot an old variant would read as its own ([`assign_slots_keeping`]).
//!
//! # For the proof
//!
//! An empty file named [`SELFTEST_CRASH_FILE`] in the editor's directory makes the worker delete
//! it and send the helper its `SelftestCrash` request; the log then shows the helper dying and a
//! new one answering its handshake (design 14, P0.2a item 7). The edit-to-pixels line names the
//! variant id `scripts/check-shader-editor-probe.py --variant-id` takes.
//!
//! The bookkeeping ([`Queue`], [`Layers`], [`culprits`], [`assign_slots_keeping`],
//! [`silence_layers`], [`layer_stages`]) is pure and tested on the host. The Windows half is the
//! thread, the files and the D3D12 calls.

use std::collections::BTreeMap;
use std::time::{Duration, SystemTime};

use er_game_base::fnv1a::{FNV1A64_OFFSET_BASIS, fnv1a64_extend};
use er_shader_compiler::{ENTRY_POINT, PROFILE};

use super::annotations::ParamDecl;
use super::codegen::Generated;
use super::compiler::source_hash_parts;
use super::diagnostics::{Diagnostic, Severity};
use super::project::{Layer, Project};
use super::{LayerStage, MAX_LAYERS, PreparedLayer, assign_slots, slot_header};
use crate::dxbc::DxbcError;
use crate::look_binding::REGISTER_SPACE;
use crate::params::Block;
use crate::probe::Region;
use crate::record::PipelineRecord;
use crate::replace::{PortKind, SUBOBJECT_ROOT_SIGNATURE, Stage};
use crate::verify::PsInterface;

/// How often the worker looks at the files (design 7.7).
pub const POLL_PERIOD: Duration = Duration::from_millis(250);

/// Ticks a published variant has to show up in the probe before the wait for it is given up: 30 s.
/// A kind the scene does not draw, such as the AO pass the screen-space reflection setting turned
/// off, never shows.
pub const FIRST_FRAME_TICKS: u32 = 120;

/// Compiles one tick runs at most, the scans between them included. Each compile publishes or
/// marks its source tried, so the queue drains well before this; it bounds a tick regardless.
pub const JOBS_PER_TICK: usize = 16;

/// Builds of one stage's source that may end in a crash, a timeout or an answer out of step
/// before the stage's edited layers are blamed for it.
pub const TRANSIENT_RETRIES: u32 = 3;

/// Ticks a table swap handed to the present may wait before the worker applies it itself: 500 ms.
/// A present applies it within a frame; only a session with no present, such as one whose
/// overlay never drew, leaves it to this.
pub const DEFERRED_TICKS: u32 = 2;

/// The file in the editor's directory that crashes the helper on purpose.
pub const SELFTEST_CRASH_FILE: &str = "selftest-crash";

/// `diag/` file for compiler messages about the editor's own code.
pub const INTERNAL_DIAG_FILE: &str = "internal.txt";

/// Diagnostics of one failure written to the log at most; the rest are in `diag/<id>.txt`.
pub const LOGGED_DIAGNOSTICS: usize = 8;

/// Compile and create times kept for the panel's p50 and p95.
pub const RECENT_TIMINGS: usize = 64;

/// The serial bits of a variant id: `editor::variant_id` keeps the kind above them.
pub const SERIAL_MASK: u32 = 0x00ff_ffff;

/// The port stages, for arrays indexed by `Stage as usize`.
pub const STAGES: usize = Stage::ALL.len();

/// The builds waiting per port stage (design 7.7): one may be in flight, one may wait behind it,
/// and a newer edit replaces the one waiting.
#[derive(Debug)]
pub struct Queue<J> {
    queued: [Option<J>; STAGES],
    in_flight: [bool; STAGES],
}

impl<J> Default for Queue<J> {
    fn default() -> Self {
        Self::new()
    }
}

impl<J> Queue<J> {
    #[must_use]
    pub fn new() -> Self {
        Self {
            queued: std::array::from_fn(|_| None),
            in_flight: [false; STAGES],
        }
    }

    /// Queue `job` for `stage`; returns the job it replaced.
    pub fn push(&mut self, stage: Stage, job: J) -> Option<J> {
        self.queued[stage as usize].replace(job)
    }

    /// Drop the job waiting for `stage`: the live variants already are what it would build.
    pub fn cancel(&mut self, stage: Stage) -> Option<J> {
        self.queued[stage as usize].take()
    }

    /// The next job to run: the first stage, in [`Stage::ALL`] order, with a job waiting and none
    /// in flight. That stage is in flight until [`Queue::finish`].
    pub fn start_next(&mut self) -> Option<(Stage, J)> {
        let stage = Stage::ALL.into_iter().find(|stage| {
            !self.in_flight[*stage as usize] && self.queued[*stage as usize].is_some()
        })?;
        let job = self.queued[stage as usize].take()?;
        self.in_flight[stage as usize] = true;
        Some((stage, job))
    }

    pub fn finish(&mut self, stage: Stage) {
        self.in_flight[stage as usize] = false;
    }

    #[must_use]
    pub fn is_queued(&self, stage: Stage) -> bool {
        self.queued[stage as usize].is_some()
    }

    #[must_use]
    pub fn is_in_flight(&self, stage: Stage) -> bool {
        self.in_flight[stage as usize]
    }

    #[must_use]
    pub fn is_idle(&self) -> bool {
        self.queued.iter().all(Option::is_none) && !self.in_flight.contains(&true)
    }
}

/// What a file's size and modification time were when it was last read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Stamp {
    pub len: u64,
    pub modified: Option<SystemTime>,
}

/// What a layer was prepared from: when any of it changes, the layer is prepared again.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PrepareKey {
    pub file: String,
    pub stage: LayerStage,
    pub slot: Option<usize>,
    /// `None` for a file that could not be read.
    pub stamp: Option<Stamp>,
}

/// How a layer stands, for the log and the panel's stack rows.
#[derive(Clone, Debug, PartialEq)]
pub enum LayerState {
    /// Read and waiting for its compile.
    Pending,
    /// In every live variant of its stage that compiled since it changed.
    Live { compile_ms: f32, verified: usize },
    /// Its last edit did not make it into a variant. `running` is whether an earlier version
    /// still draws.
    Error { summary: String, running: bool },
    /// Waiting for a compiler.
    NoCompiler { why: String },
}

impl LayerState {
    /// The status line under the layer's inspector.
    #[must_use]
    pub fn label(&self) -> String {
        match self {
            Self::Pending => "compiling".to_owned(),
            Self::Live {
                compile_ms,
                verified,
            } => format!(
                "Live: compiled in {compile_ms:.0} ms, verified {verified} of {}",
                crate::verify::Rule::ALL.len()
            ),
            Self::Error { summary, running } => format!(
                "Error{}: {summary}",
                if *running {
                    ", last good running"
                } else {
                    ", not drawn"
                }
            ),
            Self::NoCompiler { why } => format!("no compiler: {why}"),
        }
    }
}

#[derive(Clone, Debug)]
struct Entry {
    key: Option<PrepareKey>,
    /// The version compiled in: the latest that prepared, or after a failure the last good one.
    candidate: Option<PreparedLayer>,
    /// The version last compiled into a published variant: the one the live variants carry.
    last_good: Option<PreparedLayer>,
    state: LayerState,
    /// The state its last good version earned, for when an edit is undone.
    live: Option<LayerState>,
    /// The last version that failed to make it into a variant, and how: read again, it stays out
    /// with that state rather than being compiled again.
    failed: Option<(PreparedLayer, LayerState)>,
}

impl Entry {
    fn new() -> Self {
        Self {
            key: None,
            candidate: None,
            last_good: None,
            state: LayerState::Pending,
            live: None,
            failed: None,
        }
    }
}

/// Every layer's versions: the one to compile in and the last good one.
#[derive(Clone, Debug, Default)]
pub struct Layers {
    entries: BTreeMap<u32, Entry>,
}

impl Layers {
    /// Must layer `id` be prepared again for `key`?
    #[must_use]
    pub fn needs_prepare(&self, id: u32, key: &PrepareKey) -> bool {
        self.entries
            .get(&id)
            .is_none_or(|entry| entry.key.as_ref() != Some(key))
    }

    /// Prepare every layer again at the next scan, and give every version that failed another
    /// try: the panel's "Compile now", and a change to the compiler's files.
    pub fn forget_keys(&mut self) {
        for entry in self.entries.values_mut() {
            entry.key = None;
            entry.failed = None;
        }
    }

    /// Record what preparing layer `id` from `key` gave. A layer that failed goes back to its
    /// last good version, or out of the stack when it has none; so does the very version that
    /// last failed to make it into a variant ([`Self::fail`]), which keeps that failure's state.
    /// An id now naming another file is another layer, whose last good version is not the old
    /// file's. Returns whether the version compiled in changed.
    pub fn prepared(
        &mut self,
        id: u32,
        key: PrepareKey,
        outcome: Result<PreparedLayer, Vec<Diagnostic>>,
    ) -> bool {
        let entry = self.entries.entry(id).or_insert_with(Entry::new);
        if entry.key.as_ref().is_some_and(|old| old.file != key.file) {
            entry.last_good = None;
            entry.live = None;
            entry.failed = None;
        }
        entry.key = Some(key);
        let before = entry.candidate.clone();
        match outcome {
            Ok(layer) => {
                let known = entry
                    .failed
                    .as_ref()
                    .filter(|(failed, _)| *failed == layer)
                    .map(|(_, state)| state.clone());
                if let Some(state) = known {
                    entry.candidate = entry.last_good.clone();
                    entry.state = state;
                } else {
                    entry.state = if entry.last_good.as_ref() == Some(&layer) {
                        entry.live.clone().unwrap_or(LayerState::Pending)
                    } else {
                        LayerState::Pending
                    };
                    entry.candidate = Some(layer);
                }
            }
            Err(diagnostics) => {
                entry.candidate = entry.last_good.clone();
                let summary = diagnostics
                    .iter()
                    .find(|d| d.severity == Severity::Error)
                    .map_or_else(|| "it did not prepare".to_owned(), ToString::to_string);
                entry.state = LayerState::Error {
                    summary,
                    running: entry.last_good.is_some(),
                };
            }
        }
        entry.candidate != before
    }

    /// Forget every layer but `ids`.
    pub fn retain(&mut self, ids: &[u32]) {
        self.entries.retain(|id, _| ids.contains(id));
    }

    /// The versions to compile in, in `order`; a layer with none is left out.
    #[must_use]
    pub fn effective(&self, order: &[u32]) -> Vec<PreparedLayer> {
        order
            .iter()
            .filter_map(|id| self.entries.get(id)?.candidate.clone())
            .collect()
    }

    /// Those of `ids` whose version to compile in is not their last good one.
    #[must_use]
    pub fn changed(&self, ids: &[u32]) -> Vec<u32> {
        ids.iter()
            .copied()
            .filter(|id| {
                self.entries
                    .get(id)
                    .is_some_and(|entry| entry.candidate != entry.last_good)
            })
            .collect()
    }

    /// Put those of `ids` that changed back to their last good version; returns them.
    pub fn revert(&mut self, ids: &[u32]) -> Vec<u32> {
        let reverted = self.changed(ids);
        for id in &reverted {
            if let Some(entry) = self.entries.get_mut(id) {
                entry.candidate = entry.last_good.clone();
            }
        }
        reverted
    }

    /// `ids` did not make it into a variant. Each gets `state(id, running)`, `running` being
    /// whether an earlier version of it still draws; each that changed since its last good version
    /// goes back to it, and the version that failed is remembered with that state, so the same
    /// version read again -- the same text saved again -- keeps the failure instead of being
    /// compiled again. Returns the layers put back.
    pub fn fail(&mut self, ids: &[u32], state: impl Fn(u32, bool) -> LayerState) -> Vec<u32> {
        let mut reverted = Vec::new();
        for id in ids {
            let Some(entry) = self.entries.get_mut(id) else {
                continue;
            };
            let now = state(*id, entry.last_good.is_some());
            if entry.candidate != entry.last_good {
                if let Some(candidate) = entry.candidate.take() {
                    entry.failed = Some((candidate, now.clone()));
                }
                entry.candidate = entry.last_good.clone();
                reverted.push(*id);
            }
            entry.state = now;
        }
        reverted
    }

    /// `ids` made it into a published variant as they are now.
    pub fn promote(&mut self, ids: &[u32], state: &LayerState) {
        for id in ids {
            if let Some(entry) = self.entries.get_mut(id) {
                entry.last_good = entry.candidate.clone();
                entry.state = state.clone();
                entry.live = Some(state.clone());
                entry.failed = None;
            }
        }
    }

    pub fn set_state(&mut self, id: u32, state: LayerState) {
        if let Some(entry) = self.entries.get_mut(&id) {
            entry.state = state;
        }
    }

    #[must_use]
    pub fn state(&self, id: u32) -> Option<&LayerState> {
        self.entries.get(&id).map(|entry| &entry.state)
    }

    #[must_use]
    pub fn candidate(&self, id: u32) -> Option<&PreparedLayer> {
        self.entries.get(&id)?.candidate.as_ref()
    }

    /// Does an earlier version of layer `id` draw?
    #[must_use]
    pub fn is_running(&self, id: u32) -> bool {
        self.entries
            .get(&id)
            .is_some_and(|entry| entry.last_good.is_some())
    }

    /// Every layer's parameters as the version compiled into the live variants -- its last good
    /// one -- declares them: what [`super::write_layers`] packs and the panel's widgets edit. A
    /// version still compiling, or one that failed, is not what the running shaders read, so its
    /// declarations wait until it is published. A layer with no version in a live variant has
    /// none.
    #[must_use]
    pub fn live_params(&self) -> BTreeMap<u32, Vec<ParamDecl>> {
        self.entries
            .iter()
            .filter_map(|(id, entry)| Some((*id, entry.last_good.as_ref()?.params.clone())))
            .collect()
    }

    #[must_use]
    pub fn states(&self) -> BTreeMap<u32, LayerState> {
        self.entries
            .iter()
            .map(|(id, entry)| (*id, entry.state.clone()))
            .collect()
    }
}

/// The layers to blame for a failed variant of a stack holding `job_layers`: those of `named`
/// (the layers the compiler's errors are in) that changed since they last made it into a
/// variant, or every layer of the stack that changed when none of those did.
#[must_use]
pub fn culprits(named: &[u32], job_layers: &[u32], layers: &Layers) -> Vec<u32> {
    let changed = layers.changed(job_layers);
    let named: Vec<u32> = changed
        .iter()
        .copied()
        .filter(|id| named.contains(id))
        .collect();
    if named.is_empty() { changed } else { named }
}

/// Slots for the project's layers, keeping the slot of every layer in `live` -- the layers each
/// live variant has compiled in -- that the project no longer has: that variant reads the slot
/// until it is replaced, so no new layer may take it. [`super::write_layers`] writes such a slot
/// as off.
#[must_use]
pub fn assign_slots_keeping(
    project: &Project,
    live: &[&[u32]],
    previous: &BTreeMap<u32, usize>,
) -> BTreeMap<u32, usize> {
    let mut layers: Vec<Layer> = Vec::new();
    for id in live.iter().flat_map(|ids| ids.iter()).copied() {
        if project.layer(id).is_none() && !layers.iter().any(|layer| layer.id == id) {
            layers.push(Layer::new(id, "", "removed.hlsl", LayerStage::TonemapPost));
        }
    }
    layers.extend(project.layers.iter().cloned());
    assign_slots(&layers, previous)
}

/// Turn every layer slot off in `block`: a fidelity test compares the ports alone (design 14,
/// P0.1 item 5), and a port variant carries the layers compiled into it.
pub fn silence_layers(block: &mut Block) {
    for slot in 0..MAX_LAYERS {
        let header = slot_header(slot);
        if let Some(mut words) = block.register(header) {
            words[0] = 0.0_f32.to_bits();
            block.set_register(header, words);
        }
    }
}

/// The port stages with a layer that draws and is compiled into a live variant, as
/// [`crate::replace::Stage::bit`]s: `crate::safety::LAYER_STAGES`. `live` holds the layers of each
/// kind's live variant, indexed in [`PortKind::ALL`] order, and a stage's bit comes from the
/// kinds that actually have the layer, never from the stage the project now names: a layer just
/// moved to another stage still draws where it is compiled in until the move is built, and a
/// stage is never redirected to a port that does not have the layer. A layer still waiting for its
/// first compile, or for a compiler, gives its stage nothing. Turning a layer off is a header
/// write that leaves it compiled in, which is why the project, not `live`, says whether it draws.
#[must_use]
pub fn layer_stages(project: &Project, live: &[&[u32]]) -> u32 {
    PortKind::ALL
        .into_iter()
        .zip(live)
        .filter(|(_, ids)| {
            project
                .layers
                .iter()
                .any(|layer| project.is_on(layer) && ids.contains(&layer.id))
        })
        .fold(0, |bits, (kind, _)| bits | kind.stage().bit())
}

/// The probe region a kind's variants write.
#[must_use]
pub const fn region_of(kind: PortKind) -> Region {
    match kind {
        PortKind::Ao | PortKind::AoSsr => Region::Ao,
        PortKind::CmbAlbedo => Region::Albedo,
        PortKind::Tonemap | PortKind::TonemapLut => Region::Tonemap,
    }
}

/// The port kinds of `stage`, in [`PortKind::ALL`] order.
#[must_use]
pub fn kinds_of(stage: Stage) -> Vec<PortKind> {
    PortKind::ALL
        .into_iter()
        .filter(|kind| kind.stage() == stage)
        .collect()
}

/// One hash for a stage's build from the hashes of its kinds' sources: what a transient failure is
/// counted against.
#[must_use]
pub fn stage_hash(hashes: &[u64]) -> u64 {
    hashes.iter().fold(FNV1A64_OFFSET_BASIS, |hash, kind| {
        fnv1a64_extend(hash, &kind.to_le_bytes())
    })
}

/// The interface a kind's game pixel shader shows, read from the embedded port with its space42
/// parameters taken out: the port's signatures and binds equal the original's plus exactly those
/// (design 2). A variant compiled before the game has created any pipeline of its kind is checked
/// against this, and again against the real original when one arrives.
///
/// # Errors
///
/// The embedded port cannot be read, which `replace.rs`'s own tests rule out.
pub fn embedded_interface(kind: PortKind) -> Result<PsInterface, DxbcError> {
    let mut interface = PsInterface::read(kind.replacement().dxil)?;
    interface.binds.retain(|bind| bind.space != REGISTER_SPACE);
    Ok(interface)
}

/// The hash a generated source is compared by: [`super::compiler::source_hash`] of the request it
/// would be compiled from, which leaves the variant id out.
#[must_use]
pub fn generated_hash(generated: &Generated) -> u64 {
    source_hash_parts(
        generated.file_name,
        &generated.source,
        ENTRY_POINT,
        PROFILE,
        &generated.defines,
    )
}

/// The serial after `serial`: 1 to [`SERIAL_MASK`], wrapping past 0, which is the embedded
/// variant's.
#[must_use]
pub const fn next_serial(serial: u32) -> u32 {
    (serial % SERIAL_MASK) + 1
}

/// The root signature a variant of `record` with pixel shader `ps` is created with: the pointer
/// in the root signature subobject of the very stream `record.with_ps(ps)` builds. 0 when there
/// is none.
#[must_use]
pub fn stream_root_signature(record: &PipelineRecord, ps: &[u8]) -> usize {
    let Some(stream) = record.with_ps(ps) else {
        return 0;
    };
    let Some(at) = record
        .subobjects()
        .iter()
        .find(|subobject| subobject.ty == SUBOBJECT_ROOT_SIGNATURE)
        .map(|subobject| subobject.offset)
    else {
        return 0;
    };
    stream
        .bytes()
        .get(at..at + size_of::<usize>())
        .and_then(|bytes| <[u8; size_of::<usize>()]>::try_from(bytes).ok())
        .map_or(0, usize::from_le_bytes)
}

/// Line and column, both from 1, of byte `at` in `bytes`: where a file stops being UTF-8.
#[must_use]
pub fn position_of(bytes: &[u8], at: usize) -> (u32, u32) {
    let before = &bytes[..at.min(bytes.len())];
    let line = before.iter().filter(|byte| **byte == b'\n').count() + 1;
    let line_start = before
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map_or(0, |newline| newline + 1);
    (line as u32, (before.len() - line_start) as u32 + 1)
}

/// The value at percentile `p` (0 to 100) of `values`, nearest rank; `None` for none.
#[must_use]
pub fn percentile(values: &[f32], p: f32) -> Option<f32> {
    let mut sorted: Vec<f32> = values.iter().copied().filter(|v| v.is_finite()).collect();
    if sorted.is_empty() {
        return None;
    }
    sorted.sort_by(f32::total_cmp);
    let rank = ((p.clamp(0.0, 100.0) / 100.0) * sorted.len() as f32).ceil() as usize;
    sorted.get(rank.saturating_sub(1)).copied()
}

/// What the panel shows of the editor (P0.2b): the compiler, each layer's state and the timings.
/// The worker replaces it after every tick.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Status {
    /// The compiler chip: which compiler, or why there is none.
    pub compiler: String,
    /// The compiler runs in the game's process, so a crash in it ends the game.
    pub in_process: bool,
    pub layers: BTreeMap<u32, LayerState>,
    /// Each kind's live variant id; serial 0 is the embedded port.
    pub live_variant: [u32; PortKind::COUNT],
    /// Recent compile and variant-creation times in milliseconds, oldest first.
    pub compile_ms: Vec<f32>,
    pub create_ms: Vec<f32>,
    /// Each kind's last verification report ([`crate::verify::report`]).
    pub reports: [Option<String>; PortKind::COUNT],
    /// Sources marked "crashes the compiler".
    pub poisoned: usize,
    /// The project as the worker last read it, each layer's slot, the parameters of the version
    /// of each layer compiled into the live variants ([`Layers::live_params`]), and the layers
    /// compiled into each kind's live variant. With these the panel writes a header or parameter
    /// change into the parameter block the frame it is made ([`super::write_layers`],
    /// [`layer_stages`]), before the worker reads the saved project back.
    pub project: Project,
    pub slots: BTreeMap<u32, usize>,
    pub params: BTreeMap<u32, Vec<ParamDecl>>,
    pub live_layers: [Vec<u32>; PortKind::COUNT],
    /// The generation of the layer packing these were taken with. The panel writes the block from
    /// this status only while the worker's own packing is still this one (`PACKING`), so a status
    /// read just before the worker staged a new version cannot write the old packing over it.
    pub packing: u64,
}

#[cfg(windows)]
pub use process::{PACKING, request_rescan, spawn, status};

/// The thread, the files, the compiler and the D3D12 calls.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::collections::{BTreeMap, BTreeSet, VecDeque};
    use std::io::ErrorKind;
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, LazyLock, Mutex, PoisonError, mpsc};
    use std::time::{Duration, Instant, SystemTime};

    use er_shader_compiler::DXCOMPILER_DLL;
    use er_shader_compiler::protocol::Request;
    use windows::Win32::Graphics::Direct3D12::{ID3D12Device2, ID3D12RootSignature};
    use windows::core::Interface;

    use super::super::codegen::{self, Generated, layer_path};
    use super::super::compiler::{
        self, CompileError, Compiler, HELPER_EXE, ProcessStarter, Supervisor, VARIANT_ID_DEFINE,
    };
    use super::super::diagnostics::{self, Diagnostic, Severity};
    use super::super::project::{self, Layer, Project};
    use super::super::{prepare, variant_id, write_layers};
    use super::{
        DEFERRED_TICKS, FIRST_FRAME_TICKS, INTERNAL_DIAG_FILE, JOBS_PER_TICK, LOGGED_DIAGNOSTICS,
        LayerState, Layers, POLL_PERIOD, PrepareKey, Queue, RECENT_TIMINGS, SELFTEST_CRASH_FILE,
        SERIAL_MASK, STAGES, Stamp, Status, TRANSIENT_RETRIES, assign_slots_keeping, culprits,
        embedded_interface, generated_hash, kinds_of, layer_stages, next_serial, position_of,
        region_of, silence_layers, stage_hash, stream_root_signature,
    };
    use crate::look_binding::{self, Level};
    use crate::record::{self, PortRecord};
    use crate::replace::{PortKind, Stage};
    use crate::safety::{self, Mode};
    use crate::table::{self, Builder, EntrySpec};
    use crate::verify::{self, Offender, PsInterface, Rule, RuleFailure, Space42Set};
    use crate::{cmdlist, exports, graveyard, hooks, log, menu, params, probe};

    static STATUS: LazyLock<Mutex<Status>> = LazyLock::new(|| Mutex::new(Status::default()));
    static RESCAN: AtomicBool = AtomicBool::new(false);

    /// The generation of the layer packing in the staged parameter block: bumped under the
    /// block's lock every time the worker stages one, and carried in [`Status::packing`].
    pub static PACKING: AtomicU64 = AtomicU64::new(0);

    /// What the panel shows, as of the worker's last tick.
    #[must_use]
    pub fn status() -> Status {
        STATUS
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
    }

    /// Read every file again at the next tick, changed or not: the panel's "Compile now".
    pub fn request_rescan() {
        RESCAN.store(true, Ordering::Release);
    }

    /// Start the worker. It runs for the life of the process.
    pub fn spawn() {
        let spawned = std::thread::Builder::new()
            .name("er-shader-editor-worker".to_owned())
            .spawn(|| {
                // Paced like the cel worker: a channel nothing sends on.
                let (_tick_tx, tick_rx) = mpsc::channel::<()>();
                let mut worker = Worker::new();
                let mut panicked = false;
                loop {
                    let _ = tick_rx.recv_timeout(POLL_PERIOD);
                    // A panic in one tick must not end the editor for the session; the panic hook
                    // has logged where it came from.
                    if catch_unwind(AssertUnwindSafe(|| worker.tick())).is_err() && !panicked {
                        panicked = true;
                        log(format_args!(
                            "editor: a worker tick panicked; the worker carries on (logged once)"
                        ));
                    }
                }
            });
        if let Err(error) = spawned {
            log(format_args!(
                "editor: worker not started ({error}); layers are not read this session, the \
                 embedded cel ports still run"
            ));
        }
    }

    fn ms(elapsed: Duration) -> f32 {
        elapsed.as_secs_f32() * 1000.0
    }

    /// Milliseconds since `edited`, the modification time of the file whose edit this was.
    fn since(edited: Option<SystemTime>) -> String {
        edited
            .and_then(|at| SystemTime::now().duration_since(at).ok())
            .map_or_else(|| "-".to_owned(), |elapsed| format!("{:.0}", ms(elapsed)))
    }

    fn newer(a: Option<SystemTime>, b: Option<SystemTime>) -> Option<SystemTime> {
        match (a, b) {
            (Some(a), Some(b)) => Some(a.max(b)),
            (a, b) => a.or(b),
        }
    }

    fn stamp_of(path: &Path) -> Option<Stamp> {
        let metadata = std::fs::metadata(path).ok()?;
        Some(Stamp {
            len: metadata.len(),
            modified: metadata.modified().ok(),
        })
    }

    /// A diagnostic about a layer's file as a whole, with no line in it.
    fn file_error(layer: &Layer, message: String) -> Diagnostic {
        Diagnostic {
            file: Some(layer_path(&layer.file)),
            line: 0,
            column: None,
            severity: Severity::Error,
            message,
            notes: Vec::new(),
            layer: Some(layer.id),
        }
    }

    /// Read and prepare one layer's file.
    fn prepare_file(
        layer: &Layer,
        path: &Path,
        slot: Option<usize>,
    ) -> Result<super::PreparedLayer, Vec<Diagnostic>> {
        let Some(slot) = slot else {
            return Err(vec![file_error(
                layer,
                format!(
                    "no free parameter slot: the parameter block has {}",
                    super::MAX_LAYERS
                ),
            )]);
        };
        let bytes = std::fs::read(path).map_err(|error| {
            vec![file_error(
                layer,
                format!("{} cannot be read: {error}", path.display()),
            )]
        })?;
        let text = String::from_utf8(bytes).map_err(|error| {
            let (line, column) = position_of(error.as_bytes(), error.utf8_error().valid_up_to());
            vec![Diagnostic::in_layer(
                layer.id,
                &layer.file,
                line,
                column,
                Severity::Error,
                "the file is not UTF-8 from here; save it as UTF-8",
            )]
        })?;
        prepare(layer, &text, slot)
    }

    /// The project file, read. A missing file is an empty project; a file that cannot be read is
    /// `None`, and the project read last stays.
    fn read_project(path: &Path) -> Option<(Project, Vec<String>)> {
        match std::fs::read(path) {
            Ok(bytes) => match String::from_utf8(bytes) {
                Ok(text) => Some(project::parse(&text)),
                Err(error) => {
                    let (line, column) =
                        position_of(error.as_bytes(), error.utf8_error().valid_up_to());
                    log(format_args!(
                        "editor: {} is not UTF-8 at line {line} column {column}; the project read \
                         before stays",
                        project::FILE_NAME
                    ));
                    None
                }
            },
            Err(error) if error.kind() == ErrorKind::NotFound => {
                Some((Project::default(), Vec::new()))
            }
            Err(error) => {
                log(format_args!(
                    "editor: {} cannot be read ({error}); the project read before stays",
                    project::FILE_NAME
                ));
                None
            }
        }
    }

    /// The device a port record's root signature belongs to, which its variants are created on.
    /// The record holds a reference on the root signature for as long as the caller holds it.
    fn device_of(root_signature: usize) -> Result<ID3D12Device2, String> {
        let raw = root_signature as *mut c_void;
        // SAFETY: a root signature the port record holds a reference on.
        let Some(root) = (unsafe { ID3D12RootSignature::from_raw_borrowed(&raw) }) else {
            return Err("the record names no root signature".to_owned());
        };
        let mut device: Option<ID3D12Device2> = None;
        // SAFETY: a live device child and an out interface.
        unsafe { root.GetDevice(&mut device) }
            .map_err(|error| format!("the root signature's device: {error}"))?;
        device.ok_or_else(|| "the root signature has no ID3D12Device2".to_owned())
    }

    /// Rules 3 to 6 refused, because there is no original to compare with.
    fn nothing_to_compare(why: &str) -> Vec<RuleFailure> {
        [
            Rule::Linkage,
            Rule::Outputs,
            Rule::Resources,
            Rule::Features,
        ]
        .into_iter()
        .map(|rule| RuleFailure {
            rule,
            offender: Offender::Program,
            detail: format!("the game's pixel shader to compare with: {why}"),
        })
        .collect()
    }

    /// All seven rules for `blob` standing in for each of `targets`' pixel shaders, or for the
    /// embedded port's interface when there are none yet. Each failure once, by rule.
    fn verify_against(
        kind: PortKind,
        blob: &[u8],
        targets: &[Arc<PortRecord>],
    ) -> Vec<RuleFailure> {
        let mut all: Vec<RuleFailure> = Vec::new();
        let mut add = |failures: Vec<RuleFailure>| {
            for failure in failures {
                if !all.contains(&failure) {
                    all.push(failure);
                }
            }
        };
        if targets.is_empty() {
            match embedded_interface(kind) {
                Ok(interface) => {
                    add(verify::verify(blob, &interface, Space42Set::FULL)
                        .err()
                        .unwrap_or_default());
                }
                Err(error) => add(nothing_to_compare(&error.to_string())),
            }
        }
        for port in targets {
            let Some(ps) = port.record.ps() else {
                add(nothing_to_compare("the record holds no pixel shader"));
                continue;
            };
            let interface = match PsInterface::read(ps) {
                Ok(interface) => interface,
                Err(error) => {
                    add(nothing_to_compare(&error.to_string()));
                    continue;
                }
            };
            let level = look_binding::binding(port.record.root_signature())
                .map_or(Level::None, |binding| binding.level);
            add(
                verify::verify(blob, &interface, Space42Set::carried_by(level))
                    .err()
                    .unwrap_or_default(),
            );
            let used = stream_root_signature(&port.record, blob);
            if let Err(failure) = verify::check_root_signature(port.record.root_signature(), used) {
                add(vec![failure]);
            }
        }
        all.sort_by_key(|failure| failure.rule);
        all
    }

    /// One variant of `port` with `blob` as its pixel shader, holding one reference.
    fn create_one(port: &PortRecord, blob: &[u8]) -> Result<usize, String> {
        let device = device_of(port.record.root_signature())?;
        // SAFETY: the device the record's root signature was created on, and a record whose
        // stream owns everything it points at.
        unsafe { hooks::create_variant(device.as_raw() as usize, &port.record, blob) }.map_err(
            |hr| {
                format!(
                    "CreatePipelineState refused it for game pipeline {:#x}: hr=0x{:08x}",
                    port.game, hr.0 as u32
                )
            },
        )
    }

    /// One variant made for a publish: its kind, the game pipeline it stands in for, and the
    /// variant, holding one reference.
    type Made = (PortKind, usize, usize);

    /// Variants made for a publish that has not happened yet. Whatever is still here when it is
    /// dropped -- after a refusal, or a panic unwinding -- is released, because nothing has bound
    /// it.
    #[derive(Default)]
    struct Unpublished(Vec<Made>);

    impl Unpublished {
        fn push(&mut self, kind: PortKind, game: usize, variant: usize) {
            self.0.push((kind, game, variant));
        }

        /// The game pipelines given a variant of `kind`, and the variant each.
        fn of(&self, kind: PortKind) -> BTreeMap<usize, usize> {
            self.0
                .iter()
                .filter(|(made, _, _)| *made == kind)
                .map(|&(_, game, variant)| (game, variant))
                .collect()
        }

        /// Hand the variants over to whoever publishes them.
        fn take(&mut self) -> Vec<Made> {
            std::mem::take(&mut self.0)
        }
    }

    impl Drop for Unpublished {
        fn drop(&mut self) {
            for (_, _, variant) in self.0.drain(..) {
                // SAFETY: a pipeline made for a publish that never happened, never bound.
                unsafe { graveyard::com_release(variant) };
            }
        }
    }

    /// Put each made variant in `builder` in place of its game pipeline. A game pipeline whose
    /// entry is gone -- the reaper dropped it, which is also when its record is pruned -- or is
    /// not that kind's port gets nothing, and its variant is released. A record replaced for the
    /// same game pipeline describes that same pipeline, so its variant still fits it. Returns what
    /// was given, by kind.
    fn publish_into(builder: &mut Builder, made: Vec<Made>) -> Vec<Made> {
        let mut given = Vec::with_capacity(made.len());
        for (kind, game, variant) in made {
            match builder.get(game).copied() {
                Some(spec) if spec.port == kind as u8 => {
                    builder.insert(EntrySpec {
                        redirect: variant,
                        ..spec
                    });
                    given.push((kind, game, variant));
                }
                // SAFETY: a pipeline made for this publish and never published.
                _ => unsafe {
                    graveyard::com_release(variant);
                },
            }
        }
        given
    }

    /// [`publish_into`] now, for a variant whose parameters are already in the published block:
    /// the catch-up's, for a game pipeline created after its kind's variant was published.
    fn publish_now(made: Vec<Made>) -> Vec<Made> {
        let mut given = Vec::new();
        table::update(|builder| given = publish_into(builder, made));
        cmdlist::refresh_active();
        given
    }

    /// One port kind's share of a stage's build.
    struct KindJob {
        kind: PortKind,
        generated: Generated,
        hash: u64,
    }

    /// A build waiting for its stage: the source of every kind of the stage.
    struct Job {
        stage: Stage,
        /// The layers compiled in, the same for every kind of the stage.
        layers: Vec<u32>,
        kinds: Vec<KindJob>,
        /// When the worker saw the change, and the modification time of the file that changed.
        detected: Instant,
        edited: Option<SystemTime>,
        lint_ms: f32,
    }

    impl Job {
        fn hash(&self) -> u64 {
            let hashes: Vec<u64> = self.kinds.iter().map(|kind| kind.hash).collect();
            stage_hash(&hashes)
        }

        fn hashes(&self) -> Vec<(PortKind, u64)> {
            self.kinds
                .iter()
                .map(|kind| (kind.kind, kind.hash))
                .collect()
        }
    }

    /// One kind's variant, compiled and waiting to be verified and created.
    struct Built {
        kind: PortKind,
        hash: u64,
        variant: u32,
        blob: Arc<[u8]>,
        compile_ms: f32,
    }

    /// One kind's live variant and how it got there.
    struct KindState {
        live_hash: u64,
        live_variant: u32,
        live_blob: Arc<[u8]>,
        live_layers: Vec<u32>,
        /// The source last compiled, whether or not it went live.
        attempted_hash: u64,
        serial: u32,
        /// Game pipelines given a variant of the live blob by this worker, and the variant each.
        given: BTreeMap<usize, usize>,
        /// Game pipelines the live blob was refused for, so the catch-up does not retry them.
        refused: BTreeSet<usize>,
        report: Option<String>,
        /// The table swap to the live variants, handed to the present (`table::defer`); 0 for
        /// none. The catch-up leaves the kind alone until it is applied.
        ticket: u64,
    }

    /// A published variant waiting to show up in the probe.
    struct FirstFrame {
        kind: PortKind,
        variant_id: u32,
        detected: Instant,
        edited: Option<SystemTime>,
        ticks: u32,
    }

    struct Worker {
        /// `<Game>/er-shader-editor`.
        dir: Option<PathBuf>,
        compiler: Supervisor<ProcessStarter>,
        compiler_stamps: Vec<Option<Stamp>>,
        project: Project,
        project_stamp: Option<Stamp>,
        project_read: bool,
        slots: BTreeMap<u32, usize>,
        layers: Layers,
        queue: Queue<Job>,
        kinds: Vec<KindState>,
        /// Per stage: the build a transient failure was last counted against, and how many it
        /// has had in a row.
        retries: [(u64, u32); STAGES],
        params_dirty: bool,
        fidelity: bool,
        param_warnings: Vec<String>,
        pending: Vec<FirstFrame>,
        compile_ms: VecDeque<f32>,
        create_ms: VecDeque<f32>,
        /// Ticks the newest deferred swap has waited for a present.
        unpresented_ticks: u32,
        unpresented_logged: bool,
        codegen_failed: bool,
        diag_failed: bool,
        selftest_blocked: bool,
    }

    impl Worker {
        fn new() -> Self {
            let dir =
                er_game_base::log::game_directory_path().map(|game| game.join(project::DIRECTORY));
            let directories =
                compiler::search_directories(compiler::dll_directory(), dir.as_deref());
            let names: Vec<String> = directories
                .iter()
                .map(|d| d.display().to_string())
                .collect();
            match &dir {
                Some(dir) => log(format_args!(
                    "editor: worker started; reads {} every {} ms; compiler looked for in [{}]",
                    dir.join(project::FILE_NAME).display(),
                    POLL_PERIOD.as_millis(),
                    names.join(", ")
                )),
                None => log(format_args!(
                    "editor: worker started without the game's directory; no layers this session"
                )),
            }
            let kinds = PortKind::ALL
                .into_iter()
                .map(|kind| {
                    let live_hash = codegen::generate(kind, &[]).map_or(0, |g| generated_hash(&g));
                    KindState {
                        live_hash,
                        live_variant: variant_id(kind, 0),
                        live_blob: Arc::from(kind.replacement().dxil),
                        live_layers: Vec::new(),
                        attempted_hash: live_hash,
                        serial: 0,
                        given: BTreeMap::new(),
                        refused: BTreeSet::new(),
                        report: None,
                        ticket: 0,
                    }
                })
                .collect();
            Self {
                dir,
                compiler: Supervisor::new(ProcessStarter { directories }),
                compiler_stamps: Vec::new(),
                project: Project::default(),
                project_stamp: None,
                project_read: false,
                slots: BTreeMap::new(),
                layers: Layers::default(),
                queue: Queue::new(),
                kinds,
                retries: [(0, 0); STAGES],
                params_dirty: false,
                fidelity: false,
                param_warnings: Vec::new(),
                pending: Vec::new(),
                compile_ms: VecDeque::new(),
                create_ms: VecDeque::new(),
                unpresented_ticks: 0,
                unpresented_logged: false,
                codegen_failed: false,
                diag_failed: false,
                selftest_blocked: false,
            }
        }

        fn tick(&mut self) {
            let Some(dir) = self.dir.clone() else {
                return;
            };
            self.apply_unpresented();
            self.selftest_crash(&dir);
            self.watch_compiler_files();
            self.scan(&dir);
            for _ in 0..JOBS_PER_TICK {
                let Some((stage, job)) = self.queue.start_next() else {
                    break;
                };
                let layers = job.layers.clone();
                let hashes = job.hashes();
                // A panic in one build ends that build alone: the variants it made are released
                // as it unwinds, and the stage is finished either way, so it can be built again.
                let built = catch_unwind(AssertUnwindSafe(|| self.process(&dir, stage, job)));
                self.queue.finish(stage);
                if built.is_err() {
                    self.internal_failure(&dir, stage, &layers, &hashes);
                }
                // An edit made during that build replaces what waits behind it.
                self.scan(&dir);
            }
            self.catch_up();
            self.restage();
            self.watch_first_frames();
            self.publish_status();
        }

        fn log_compiler_events(&mut self) {
            for event in self.compiler.take_events() {
                log(format_args!("editor: {event}"));
            }
        }

        /// Write `diag/<name>`, or remove it for empty text. A failure is logged once.
        fn write_diag(&mut self, dir: &Path, name: &str, text: &str) {
            let folder = dir.join(project::DIAG_DIRECTORY);
            let path = folder.join(name);
            let result = if text.is_empty() {
                match std::fs::remove_file(&path) {
                    Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
                    other => other,
                }
            } else {
                std::fs::create_dir_all(&folder)
                    .and_then(|()| er_hotkey_config::write_atomic(&path, text))
            };
            if let Err(error) = result
                && !self.diag_failed
            {
                self.diag_failed = true;
                log(format_args!(
                    "editor: could not write {} ({error}); diagnostics are in this log only \
                     (logged once)",
                    path.display()
                ));
            }
        }

        fn layer_diag(&mut self, dir: &Path, id: u32, text: &str) {
            self.write_diag(dir, &format!("{id}.txt"), text);
        }

        /// Step 0: a table swap handed to the present that no present applied within
        /// [`DEFERRED_TICKS`] ticks is applied here. A present applies it within a frame; a
        /// session whose overlay never draws has no present to, and its parameter block never
        /// changes either, so nothing waits for anything there.
        fn apply_unpresented(&mut self) {
            let newest = self
                .kinds
                .iter()
                .map(|state| state.ticket)
                .max()
                .unwrap_or(0);
            if newest == 0 || table::is_applied(newest) {
                self.unpresented_ticks = 0;
                return;
            }
            self.unpresented_ticks += 1;
            if self.unpresented_ticks < DEFERRED_TICKS {
                return;
            }
            self.unpresented_ticks = 0;
            if table::apply_deferred() > 0 {
                cmdlist::refresh_active();
                if !self.unpresented_logged {
                    self.unpresented_logged = true;
                    log(format_args!(
                        "editor: no present applied a variant swap within {} ms, so the worker \
                         did; this session's overlay may not draw (logged once)",
                        POLL_PERIOD.as_millis() * u128::from(DEFERRED_TICKS)
                    ));
                }
            }
        }

        /// The file named [`SELFTEST_CRASH_FILE`] asks for the helper to be crashed once.
        fn selftest_crash(&mut self, dir: &Path) {
            let path = dir.join(SELFTEST_CRASH_FILE);
            if std::fs::metadata(&path).is_err() {
                self.selftest_blocked = false;
                return;
            }
            if self.selftest_blocked {
                return;
            }
            if let Err(error) = std::fs::remove_file(&path) {
                self.selftest_blocked = true;
                log(format_args!(
                    "editor: selftest-crash: {} could not be removed ({error}), so the helper is \
                     not crashed: the file would ask again on every tick",
                    path.display()
                ));
                return;
            }
            log(format_args!(
                "editor: selftest-crash requested by {}",
                path.display()
            ));
            match self.compiler.selftest_crash() {
                Ok(how) => log(format_args!(
                    "editor: selftest-crash: the helper died: {how}"
                )),
                Err(why) => log(format_args!("editor: selftest-crash: not done: {why}")),
            }
            self.log_compiler_events();
        }

        /// A compiler file appearing, vanishing or changing lets the supervisor choose again --
        /// or, while the in-process compiler runs, try the helper again -- and gives every layer
        /// that found no compiler, or failed, another go: each is prepared again from its file.
        fn watch_compiler_files(&mut self) {
            let directories = self.compiler.starter().directories.clone();
            let stamps: Vec<Option<Stamp>> = directories
                .iter()
                .flat_map(|dir| [dir.join(HELPER_EXE), dir.join(DXCOMPILER_DLL)])
                .map(|path| stamp_of(&path))
                .collect();
            if stamps == self.compiler_stamps {
                return;
            }
            let first = self.compiler_stamps.is_empty();
            self.compiler_stamps = stamps;
            if first {
                return;
            }
            self.compiler.reconsider();
            self.layers.forget_keys();
            for state in &mut self.kinds {
                if state.attempted_hash != state.live_hash {
                    state.attempted_hash = 0;
                }
            }
            log(format_args!(
                "editor: the compiler's files changed; {}",
                self.compiler.choice()
            ));
        }

        /// Steps 1 and 2 of a tick: read what changed, and queue the stages whose source moved.
        fn scan(&mut self, dir: &Path) {
            let rescan = RESCAN.swap(false, Ordering::AcqRel);
            if rescan {
                self.layers.forget_keys();
                self.project_stamp = None;
                self.project_read = false;
                for state in &mut self.kinds {
                    state.attempted_hash = 0;
                }
            }
            let mut edited: Option<SystemTime> = None;
            let project_path = dir.join(project::FILE_NAME);
            let stamp = stamp_of(&project_path);
            if !self.project_read || stamp != self.project_stamp {
                self.project_read = true;
                self.project_stamp = stamp;
                if let Some((project, warnings)) = read_project(&project_path) {
                    for warning in warnings.iter().take(LOGGED_DIAGNOSTICS) {
                        log(format_args!("editor: {}: {warning}", project::FILE_NAME));
                    }
                    if project != self.project {
                        log(format_args!(
                            "editor: {} read: {} layer(s)",
                            project::FILE_NAME,
                            project.layers.len()
                        ));
                        self.project = project;
                        self.params_dirty = true;
                        edited = newer(edited, stamp.and_then(|s| s.modified));
                    }
                }
            }

            let live: Vec<&[u32]> = self
                .kinds
                .iter()
                .map(|state| state.live_layers.as_slice())
                .collect();
            let slots = assign_slots_keeping(&self.project, &live, &self.slots);
            if slots != self.slots {
                self.slots = slots;
                self.params_dirty = true;
            }

            let mut lint_ms = 0.0;
            let layers_dir = dir.join(project::LAYERS_DIRECTORY);
            for layer in self.project.layers.clone() {
                let path = layers_dir.join(&layer.file);
                let stamp = stamp_of(&path);
                let key = PrepareKey {
                    file: layer.file.clone(),
                    stage: layer.stage,
                    slot: self.slots.get(&layer.id).copied(),
                    stamp,
                };
                if !self.layers.needs_prepare(layer.id, &key) {
                    continue;
                }
                let started = Instant::now();
                let outcome = prepare_file(&layer, &path, key.slot);
                lint_ms += ms(started.elapsed());
                if let Err(found) = &outcome {
                    self.report_prepare_errors(dir, &layer, found);
                }
                if self.layers.prepared(layer.id, key, outcome) {
                    edited = newer(edited, stamp.and_then(|s| s.modified));
                }
            }
            let order: Vec<u32> = self.project.layers.iter().map(|l| l.id).collect();
            self.layers.retain(&order);

            let effective = self.layers.effective(&order);
            let detected = Instant::now();
            for stage in Stage::ALL {
                let mut kinds = Vec::new();
                for kind in kinds_of(stage) {
                    match codegen::generate(kind, &effective) {
                        Ok(generated) => {
                            let hash = generated_hash(&generated);
                            kinds.push(KindJob {
                                kind,
                                generated,
                                hash,
                            });
                        }
                        Err(error) => {
                            if !self.codegen_failed {
                                self.codegen_failed = true;
                                log(format_args!(
                                    "editor: internal (please report): the source of kind {} \
                                     could not be generated: {error}; stage {} is not built \
                                     (logged once)",
                                    kind as u8,
                                    stage.name()
                                ));
                            }
                            kinds.clear();
                            break;
                        }
                    }
                }
                if kinds.is_empty() {
                    continue;
                }
                let live = kinds
                    .iter()
                    .all(|job| job.hash == self.kinds[job.kind as usize].live_hash);
                if live {
                    for job in &kinds {
                        self.kinds[job.kind as usize].attempted_hash = job.hash;
                    }
                    self.queue.cancel(stage);
                    continue;
                }
                let tried = kinds.iter().all(|job| {
                    let state = &self.kinds[job.kind as usize];
                    job.hash == state.live_hash || job.hash == state.attempted_hash
                });
                if tried {
                    continue;
                }
                let layers = kinds
                    .first()
                    .map(|job| job.generated.layers.clone())
                    .unwrap_or_default();
                self.queue.push(
                    stage,
                    Job {
                        stage,
                        layers,
                        kinds,
                        detected,
                        edited,
                        lint_ms,
                    },
                );
            }
        }

        fn report_prepare_errors(&mut self, dir: &Path, layer: &Layer, found: &[Diagnostic]) {
            self.layer_diag(dir, layer.id, &diagnostics::render(found));
            for diagnostic in found
                .iter()
                .filter(|d| d.severity == Severity::Error)
                .take(LOGGED_DIAGNOSTICS)
            {
                log(format_args!("editor: layer {}: {diagnostic}", layer.id));
            }
        }

        /// Step 3 for one stage: compile every kind, verify and create every variant, and only
        /// then move the stage to the new version and hand the swap to the present.
        fn process(&mut self, dir: &Path, stage: Stage, job: Job) {
            let mut built: Vec<Built> = Vec::with_capacity(job.kinds.len());
            for kind_job in &job.kinds {
                let kind = kind_job.kind;
                if kind_job.hash == self.kinds[kind as usize].live_hash {
                    continue;
                }
                if job.layers.is_empty() {
                    // The empty stack is the embedded port's own source.
                    built.push(Built {
                        kind,
                        hash: kind_job.hash,
                        variant: variant_id(kind, 0),
                        blob: Arc::from(kind.replacement().dxil),
                        compile_ms: 0.0,
                    });
                    continue;
                }
                let state = &mut self.kinds[kind as usize];
                state.serial = next_serial(state.serial);
                let variant = variant_id(kind, state.serial);
                let mut defines = kind_job.generated.defines.clone();
                defines.push((VARIANT_ID_DEFINE.to_owned(), variant.to_string()));
                let request = Request::compile(
                    0,
                    kind_job.generated.file_name,
                    kind_job.generated.source.clone(),
                    defines,
                );
                let started = Instant::now();
                let result = self.compiler.compile(request);
                let compile_ms = exports::note_compile(started.elapsed());
                self.log_compiler_events();
                match result {
                    Err(error) => {
                        self.compile_refused(dir, &job, kind, &error);
                        return;
                    }
                    Ok(response) if !response.ok => {
                        self.compile_failed(dir, &job, kind, variant, &response.diagnostics);
                        return;
                    }
                    Ok(response) => {
                        push_recent(&mut self.compile_ms, compile_ms);
                        built.push(Built {
                            kind,
                            hash: kind_job.hash,
                            variant,
                            blob: Arc::from(response.blob),
                            compile_ms,
                        });
                    }
                }
            }
            if built.is_empty() {
                self.mark_attempted(&job);
                return;
            }

            // Every variant of the stage, verified and created with no lock held: the records are
            // cloned out of the registry, which the game's creation detour takes synchronously.
            let mut made = Unpublished::default();
            let mut timings: Vec<(f32, f32, usize)> = Vec::with_capacity(built.len());
            for item in &built {
                let targets = record::ports_of(item.kind);
                let verify_started = Instant::now();
                let failures = verify_against(item.kind, &item.blob, &targets);
                let verify_ms = ms(verify_started.elapsed());
                if !failures.is_empty() {
                    self.refused(dir, &job, item, &failures);
                    return;
                }
                let create_started = Instant::now();
                for port in &targets {
                    match create_one(port, &item.blob) {
                        Ok(variant) => made.push(item.kind, port.game, variant),
                        Err(why) => {
                            self.not_created(dir, &job, item, &why);
                            return;
                        }
                    }
                }
                let create_ms = if targets.is_empty() {
                    0.0
                } else {
                    let create_ms = exports::note_create(create_started.elapsed());
                    push_recent(&mut self.create_ms, create_ms);
                    create_ms
                };
                timings.push((verify_ms, create_ms, targets.len()));
            }

            // The stage moves to the new version, the block is packed for it, and the swap goes
            // to the next present with that block.
            let publish_started = Instant::now();
            for item in &built {
                let given = made.of(item.kind);
                let state = &mut self.kinds[item.kind as usize];
                state.live_hash = item.hash;
                state.live_variant = item.variant;
                state.live_blob = Arc::clone(&item.blob);
                state.live_layers.clone_from(&job.layers);
                state.given = given;
                state.refused.clear();
                state.report = Some(verify::report(&[]));
            }
            self.mark_attempted(&job);
            let live = LayerState::Live {
                compile_ms: built.iter().map(|item| item.compile_ms).fold(0.0, f32::max),
                verified: Rule::ALL.len(),
            };
            self.layers.promote(&job.layers, &live);
            self.retries[stage as usize] = (0, 0);
            let ticket = self.write_block(made.take());
            if ticket != 0 {
                // With no pipeline of a kind to swap, a swap still waiting for its present keeps
                // holding that kind's catch-up back.
                for item in &built {
                    self.kinds[item.kind as usize].ticket = ticket;
                }
            }
            let publish_ms = ms(publish_started.elapsed());

            for id in job.layers.clone() {
                let warnings = self
                    .layers
                    .candidate(id)
                    .map(|layer| diagnostics::render(&layer.warnings))
                    .unwrap_or_default();
                self.layer_diag(dir, id, &warnings);
            }
            for (item, (verify_ms, create_ms, pipelines)) in built.iter().zip(timings) {
                self.pending.retain(|pending| pending.kind != item.kind);
                if pipelines > 0 {
                    self.pending.push(FirstFrame {
                        kind: item.kind,
                        variant_id: item.variant,
                        detected: job.detected,
                        edited: job.edited,
                        ticks: 0,
                    });
                }
                log(format_args!(
                    "editor: edit -> variant {:#010x} kind={} ({}) stage={} layers={:?} \
                     pipelines={pipelines}{} lint_ms={:.2} compile_ms={:.2} verify_ms={verify_ms:.2} \
                     create_ms={create_ms:.2} publish_ms={publish_ms:.2} detect_to_publish_ms={:.0} \
                     edit_to_publish_ms={}",
                    item.variant,
                    item.kind as u8,
                    item.kind.replacement().name,
                    stage.name(),
                    job.layers,
                    if pipelines == 0 {
                        " (none of this kind yet: given to each the game creates)"
                    } else {
                        ""
                    },
                    job.lint_ms,
                    item.compile_ms,
                    ms(job.detected.elapsed()),
                    since(job.edited),
                ));
            }
        }

        /// Every kind of `job` has had its source tried.
        fn mark_attempted(&mut self, job: &Job) {
            for kind_job in &job.kinds {
                self.kinds[kind_job.kind as usize].attempted_hash = kind_job.hash;
            }
        }

        /// A rule refused `item`'s blob before any pipeline was created.
        fn refused(&mut self, dir: &Path, job: &Job, item: &Built, failures: &[RuleFailure]) {
            let report = verify::report(failures);
            for failure in failures.iter().take(LOGGED_DIAGNOSTICS) {
                log(format_args!(
                    "editor: variant {:#010x} kind={} refused: {failure}",
                    item.variant, item.kind as u8
                ));
            }
            let summary = failures
                .first()
                .map_or_else(String::new, ToString::to_string);
            let text = format!(
                "variant {:#010x} for {} was refused before any pipeline was created\n{report}",
                item.variant,
                item.kind.replacement().name
            );
            self.blame(dir, job, item.kind, &[], &summary, &text);
            self.kinds[item.kind as usize].report = Some(report);
        }

        /// The driver refused one of `item`'s pipelines.
        fn not_created(&mut self, dir: &Path, job: &Job, item: &Built, why: &str) {
            log(format_args!(
                "editor: variant {:#010x} kind={} not created: {why}; the stage's live variants \
                 keep running",
                item.variant, item.kind as u8
            ));
            let text = format!("variant {:#010x}: {why}\n", item.variant);
            self.blame(dir, job, item.kind, &[], why, &text);
        }

        /// A failed build of `job`: put its culprits back to their last good versions, mark them,
        /// and remember the source as tried.
        fn blame(
            &mut self,
            dir: &Path,
            job: &Job,
            kind: PortKind,
            named: &[u32],
            summary: &str,
            text: &str,
        ) {
            let culprits = culprits(named, &job.layers, &self.layers);
            if culprits.is_empty() {
                log(format_args!(
                    "editor: kind={}: no edited layer to undo; variant {:#010x} keeps running",
                    kind as u8, self.kinds[kind as usize].live_variant
                ));
            }
            self.layers.fail(&culprits, |_, running| LayerState::Error {
                summary: summary.to_owned(),
                running,
            });
            for id in culprits {
                self.layer_diag(dir, id, text);
            }
            self.mark_attempted(job);
        }

        /// The compiler gave no answer for `kind`'s source.
        fn compile_refused(&mut self, dir: &Path, job: &Job, kind: PortKind, error: &CompileError) {
            log(format_args!(
                "editor: kind={} not compiled: {error}; variant {:#010x} keeps running",
                kind as u8, self.kinds[kind as usize].live_variant
            ));
            if let CompileError::Unavailable(why) = error {
                // The edits wait for a compiler, out of the stack meanwhile, and come back when
                // the compiler's files change.
                let changed = self.layers.changed(&job.layers);
                self.layers
                    .fail(&changed, |_, _| LayerState::NoCompiler { why: why.clone() });
                self.mark_attempted(job);
                return;
            }
            if error.is_transient() {
                let hash = job.hash();
                let (seen, tries) = &mut self.retries[job.stage as usize];
                if *seen != hash {
                    *seen = hash;
                    *tries = 0;
                }
                *tries += 1;
                if *tries < TRANSIENT_RETRIES {
                    log(format_args!(
                        "editor: stage {} is built again ({} of {TRANSIENT_RETRIES} tries): the \
                         compiler's failure says nothing certain about the source",
                        job.stage.name(),
                        *tries
                    ));
                    return;
                }
                self.retries[job.stage as usize] = (0, 0);
            }
            let summary = error.to_string();
            let text = format!("{summary}\n");
            self.blame(dir, job, kind, &[], &summary, &text);
        }

        /// The compiler answered `kind`'s source with errors.
        fn compile_failed(
            &mut self,
            dir: &Path,
            job: &Job,
            kind: PortKind,
            variant: u32,
            output: &str,
        ) {
            let mut found = diagnostics::parse(output);
            let files: Vec<(u32, String)> = job
                .layers
                .iter()
                .filter_map(|id| Some((*id, self.layers.candidate(*id)?.file.clone())))
                .collect();
            let pairs: Vec<(u32, &str)> = files.iter().map(|(id, f)| (*id, f.as_str())).collect();
            diagnostics::attribute(&mut found, &pairs);
            let errors = found
                .iter()
                .filter(|d| d.severity == Severity::Error)
                .count();
            log(format_args!(
                "editor: variant {variant:#010x} kind={} did not compile: {errors} error(s); \
                 variant {:#010x} keeps running",
                kind as u8, self.kinds[kind as usize].live_variant
            ));
            for diagnostic in found.iter().take(LOGGED_DIAGNOSTICS) {
                match diagnostic.layer {
                    Some(id) => log(format_args!("editor: layer {id}: {diagnostic}")),
                    None => log(format_args!(
                        "editor: internal (please report): {diagnostic}"
                    )),
                }
            }
            if found.is_empty() {
                for line in output.lines().take(LOGGED_DIAGNOSTICS) {
                    log(format_args!("editor: compiler: {line}"));
                }
            }
            let internal: Vec<Diagnostic> =
                found.iter().filter(|d| d.is_internal()).cloned().collect();
            if !internal.is_empty() {
                self.write_diag(dir, INTERNAL_DIAG_FILE, &diagnostics::render(&internal));
            }
            let named: Vec<u32> = found
                .iter()
                .filter(|d| d.severity == Severity::Error)
                .filter_map(|d| d.layer)
                .collect::<BTreeSet<u32>>()
                .into_iter()
                .collect();
            let blamed = culprits(&named, &job.layers, &self.layers);
            let summary_of = |id: u32| {
                found
                    .iter()
                    .find(|d| d.layer == Some(id) && d.severity == Severity::Error)
                    .map_or_else(|| "the compile failed".to_owned(), ToString::to_string)
            };
            // Each named layer gets its own messages, and a culprit with none the whole output.
            let marked: BTreeSet<u32> = named.iter().chain(&blamed).copied().collect();
            for &id in &marked {
                let own: Vec<Diagnostic> = found
                    .iter()
                    .filter(|d| d.layer == Some(id))
                    .cloned()
                    .collect();
                let text = if own.is_empty() {
                    format!("{output}\n")
                } else {
                    diagnostics::render(&own)
                };
                self.layer_diag(dir, id, &text);
            }
            let state_of = |id: u32, running: bool| LayerState::Error {
                summary: summary_of(id),
                running,
            };
            self.layers.fail(&blamed, state_of);
            for id in marked.iter().copied().filter(|id| !blamed.contains(id)) {
                let running = self.layers.is_running(id);
                self.layers.set_state(id, state_of(id, running));
            }
            self.mark_attempted(job);
        }

        /// A build of `stage` panicked: its variants were released as it unwound. Its edited
        /// layers go back to their last good versions and show an internal error, and its sources
        /// count as tried, so the same panic is not hit again on every tick.
        fn internal_failure(
            &mut self,
            dir: &Path,
            stage: Stage,
            layers: &[u32],
            hashes: &[(PortKind, u64)],
        ) {
            log(format_args!(
                "editor: internal (please report): building stage {} panicked; its live variants \
                 keep running, and the panic hook logged where",
                stage.name()
            ));
            let summary = "internal (please report): building this stage panicked; \
                           er-cel-shade.log says where";
            let changed = self.layers.changed(layers);
            self.layers.fail(&changed, |_, running| LayerState::Error {
                summary: summary.to_owned(),
                running,
            });
            for id in changed {
                self.layer_diag(dir, id, &format!("{summary}\n"));
            }
            for &(kind, hash) in hashes {
                self.kinds[kind as usize].attempted_hash = hash;
            }
        }

        /// Step 4: a game pipeline of a kind whose live blob is the editor's, created after it was
        /// published (or whose entry the game's own creation path replaced), gets a variant too.
        /// A kind whose swap still waits for its present is left until it is applied; its
        /// parameters are in the published block from then on, so the catch-up publishes at once.
        fn catch_up(&mut self) {
            for kind in PortKind::ALL {
                let state = &mut self.kinds[kind as usize];
                if state.live_variant & SERIAL_MASK == 0 {
                    continue;
                }
                if state.ticket != 0 && !table::is_applied(state.ticket) {
                    continue;
                }
                let blob = Arc::clone(&state.live_blob);
                let variant_id = state.live_variant;
                let mine = record::ports_of(kind);
                state
                    .given
                    .retain(|game, _| mine.iter().any(|port| port.game == *game));
                state
                    .refused
                    .retain(|game| mine.iter().any(|port| port.game == *game));
                for port in &mine {
                    let Some(current) = table::get(port.game).map(|entry| entry.redirect) else {
                        continue;
                    };
                    if state.refused.contains(&port.game)
                        || state.given.get(&port.game) == Some(&current)
                    {
                        continue;
                    }
                    let failures = verify_against(kind, &blob, std::slice::from_ref(port));
                    if let Some(failure) = failures.first() {
                        state.refused.insert(port.game);
                        log(format_args!(
                            "editor: variant {variant_id:#010x} refused for game pipeline \
                             {:#x}, which keeps its cel port: {failure}",
                            port.game
                        ));
                        continue;
                    }
                    match create_one(port, &blob) {
                        Ok(variant) => {
                            for (_, game, given) in publish_now(vec![(kind, port.game, variant)]) {
                                state.given.insert(game, given);
                                log(format_args!(
                                    "editor: variant {variant_id:#010x} given to game \
                                     pipeline {game:#x}, created after it was published"
                                ));
                            }
                        }
                        Err(why) => {
                            state.refused.insert(port.game);
                            log(format_args!(
                                "editor: variant {variant_id:#010x}: {why}; that pipeline \
                                 keeps its cel port"
                            ));
                        }
                    }
                }
            }
        }

        /// Step 5: the parameter block and the stages, when something they come from changed.
        fn restage(&mut self) {
            if (safety::mode() == Mode::Fidelity) != self.fidelity {
                self.params_dirty = true;
            }
            if self.params_dirty {
                self.write_block(Vec::new());
            }
        }

        /// Pack the live versions' parameters into the staged block and, in the same hold on the
        /// block, hand `made`'s table swap to the next present, which publishes the block and
        /// applies the swap together (`params::publish_then`). Then the stages with a drawing
        /// layer. Returns the swap's ticket, or 0 with nothing to swap.
        fn write_block(&mut self, made: Vec<Made>) -> u64 {
            self.params_dirty = false;
            let fidelity = safety::mode() == Mode::Fidelity;
            self.fidelity = fidelity;
            let params = self.layers.live_params();
            let (project, slots) = (&self.project, &self.slots);
            let mut warnings = Vec::new();
            let mut ticket = 0;
            params::stage(|block| {
                warnings = write_layers(block, project, slots, &params);
                if fidelity {
                    silence_layers(block);
                }
                PACKING.fetch_add(1, Ordering::AcqRel);
                if !made.is_empty() {
                    ticket = table::defer(move |builder| {
                        publish_into(builder, made);
                    });
                }
            });
            if warnings != self.param_warnings {
                for warning in warnings.iter().take(LOGGED_DIAGNOSTICS) {
                    log(format_args!("editor: {warning}"));
                }
                self.param_warnings = warnings;
            }
            let live: Vec<&[u32]> = self
                .kinds
                .iter()
                .map(|state| state.live_layers.as_slice())
                .collect();
            let stages = layer_stages(&self.project, &live);
            if safety::LAYER_STAGES.swap(stages, Ordering::AcqRel) != stages {
                // Now, rather than at the next present, which may be a while when no overlay
                // draws.
                menu::restage();
                let names: Vec<&str> = Stage::ALL
                    .into_iter()
                    .filter(|stage| stages & stage.bit() != 0)
                    .map(Stage::name)
                    .collect();
                log(format_args!(
                    "editor: stages with a drawing layer: [{}]",
                    names.join(", ")
                ));
            }
            // At once, not at the end of the tick: the panel writes the block only from a status
            // of the newest packing, and a build of another stage may follow this one.
            self.publish_status();
            ticket
        }

        /// Step 6: the first probe frame each published variant drew.
        fn watch_first_frames(&mut self) {
            if self.pending.is_empty() {
                return;
            }
            let regions = probe::read_latest(params::presents());
            self.pending.retain_mut(|pending| {
                let region = region_of(pending.kind);
                let seen = regions.iter().any(|record| {
                    record.region == region && record.header.variant_id == pending.variant_id
                });
                if seen {
                    log(format_args!(
                        "editor: first probe frame of variant {:#010x} kind={} \
                         detect_to_pixels_ms={:.0} edit_to_pixels_ms={}",
                        pending.variant_id,
                        pending.kind as u8,
                        ms(pending.detected.elapsed()),
                        since(pending.edited)
                    ));
                    return false;
                }
                pending.ticks += 1;
                if pending.ticks >= FIRST_FRAME_TICKS {
                    log(format_args!(
                        "editor: variant {:#010x} kind={} not seen in the probe after {} ticks; \
                         the scene may not draw that pass",
                        pending.variant_id, pending.kind as u8, pending.ticks
                    ));
                    return false;
                }
                true
            });
        }

        fn publish_status(&mut self) {
            let status = Status {
                compiler: self.compiler.choice().to_string(),
                in_process: matches!(self.compiler.choice(), compiler::Choice::InProcess { .. }),
                layers: self.layers.states(),
                live_variant: std::array::from_fn(|kind| self.kinds[kind].live_variant),
                compile_ms: self.compile_ms.iter().copied().collect(),
                create_ms: self.create_ms.iter().copied().collect(),
                reports: std::array::from_fn(|kind| self.kinds[kind].report.clone()),
                poisoned: self.compiler.ledger().poisoned(),
                project: self.project.clone(),
                slots: self.slots.clone(),
                params: self.layers.live_params(),
                live_layers: std::array::from_fn(|kind| self.kinds[kind].live_layers.clone()),
                packing: PACKING.load(Ordering::Acquire),
            };
            *STATUS.lock().unwrap_or_else(PoisonError::into_inner) = status;
        }
    }

    fn push_recent(recent: &mut VecDeque<f32>, value: f32) {
        if recent.len() >= RECENT_TIMINGS {
            recent.pop_front();
        }
        recent.push_back(value);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::prepare;
    use crate::params;
    use crate::record::{ShaderBytecode, ShaderStore};
    use crate::replace::test_support::{push, test_layout};

    fn layer(id: u32, stage: LayerStage) -> Layer {
        Layer::new(id, "test", &format!("{id}-test.hlsl"), stage)
    }

    fn prepared(id: u32, stage: LayerStage, text: &str) -> PreparedLayer {
        match prepare(&layer(id, stage), text, 0) {
            Ok(layer) => layer,
            Err(found) => panic!("{found:?}"),
        }
    }

    const GOOD: &str = "float3 apply(float3 c, TonemapCtx ctx) { return c; }\n";
    const BETTER: &str = "float3 apply(float3 c, TonemapCtx ctx) { return c.bgr; }\n";
    const WITH_UNIFORM: &str = "uniform float Strength = 0.5;\n\
                                float3 apply(float3 c, TonemapCtx ctx) { return c * Strength; }\n";

    fn key(id: u32, len: u64) -> PrepareKey {
        PrepareKey {
            file: format!("{id}-test.hlsl"),
            stage: LayerStage::TonemapPost,
            slot: Some(0),
            stamp: Some(Stamp {
                len,
                modified: None,
            }),
        }
    }

    fn broken(id: u32) -> Result<PreparedLayer, Vec<Diagnostic>> {
        Err(vec![Diagnostic::in_layer(
            id,
            &format!("{id}-test.hlsl"),
            2,
            5,
            Severity::Error,
            "use of undeclared identifier 'Strenth'",
        )])
    }

    fn error(summary: &str) -> impl Fn(u32, bool) -> LayerState {
        let summary = summary.to_owned();
        move |_, running| LayerState::Error {
            summary: summary.clone(),
            running,
        }
    }

    #[test]
    fn a_newer_edit_replaces_the_one_waiting_for_its_stage() {
        let mut queue: Queue<&str> = Queue::new();
        assert!(queue.is_idle());
        assert_eq!(queue.push(Stage::Tonemap, "edit 1"), None);
        assert_eq!(queue.push(Stage::Tonemap, "edit 2"), Some("edit 1"));
        assert_eq!(queue.start_next(), Some((Stage::Tonemap, "edit 2")));
        assert!(queue.is_in_flight(Stage::Tonemap));
        // While it builds, two more edits arrive: the later one waits, the earlier is dropped.
        assert_eq!(queue.push(Stage::Tonemap, "edit 3"), None);
        assert_eq!(queue.push(Stage::Tonemap, "edit 4"), Some("edit 3"));
        // One in flight per stage: nothing else starts for it.
        assert_eq!(queue.start_next(), None);
        queue.finish(Stage::Tonemap);
        assert_eq!(queue.start_next(), Some((Stage::Tonemap, "edit 4")));
        queue.finish(Stage::Tonemap);
        assert!(queue.is_idle());
    }

    #[test]
    fn stages_queue_apart_and_start_in_stage_order() {
        let mut queue: Queue<u32> = Queue::new();
        queue.push(Stage::Tonemap, 5);
        queue.push(Stage::Lighting, 1);
        queue.push(Stage::Albedo, 4);
        assert_eq!(queue.start_next(), Some((Stage::Lighting, 1)));
        // Another stage starts while the first is in flight.
        assert_eq!(queue.start_next(), Some((Stage::Albedo, 4)));
        assert_eq!(queue.cancel(Stage::Tonemap), Some(5));
        assert!(!queue.is_queued(Stage::Tonemap));
        assert_eq!(queue.start_next(), None);
        queue.finish(Stage::Lighting);
        queue.finish(Stage::Albedo);
        assert!(queue.is_idle());
    }

    #[test]
    fn each_stage_builds_every_kind_it_has() {
        assert_eq!(kinds_of(Stage::Lighting), [PortKind::Ao, PortKind::AoSsr]);
        assert_eq!(kinds_of(Stage::Albedo), [PortKind::CmbAlbedo]);
        assert_eq!(
            kinds_of(Stage::Tonemap),
            [PortKind::Tonemap, PortKind::TonemapLut]
        );
        let all: usize = Stage::ALL
            .into_iter()
            .map(|stage| kinds_of(stage).len())
            .sum();
        assert_eq!(all, PortKind::COUNT);
        // Every layer stage's kinds are one port stage's, so one build carries all of them.
        for stage in LayerStage::ALL {
            assert_eq!(stage.port_kinds(), kinds_of(stage.port_stage()));
        }
        // The stage's hash moves with any kind's.
        assert_ne!(stage_hash(&[1, 2]), stage_hash(&[1, 3]));
        assert_ne!(stage_hash(&[1, 2]), stage_hash(&[2, 1]));
        assert_eq!(stage_hash(&[7, 9]), stage_hash(&[7, 9]));
    }

    #[test]
    fn a_broken_edit_keeps_the_last_good_version_compiled_in() {
        let mut layers = Layers::default();
        assert!(layers.needs_prepare(3, &key(3, 10)));
        let good = prepared(3, LayerStage::TonemapPost, GOOD);
        assert!(layers.prepared(3, key(3, 10), Ok(good.clone())));
        assert!(!layers.needs_prepare(3, &key(3, 10)));
        assert_eq!(layers.state(3), Some(&LayerState::Pending));
        assert_eq!(layers.changed(&[3]), vec![3]);
        let live = LayerState::Live {
            compile_ms: 21.0,
            verified: 7,
        };
        layers.promote(&[3], &live);
        assert!(layers.changed(&[3]).is_empty());

        // A typo: the good version stays in the stack and the layer shows the error.
        assert!(!layers.prepared(3, key(3, 11), broken(3)));
        assert_eq!(layers.effective(&[3]), vec![good.clone()]);
        let Some(LayerState::Error { summary, running }) = layers.state(3) else {
            panic!("no error state");
        };
        assert!(summary.contains("Strenth"), "{summary}");
        assert!(*running);
        // Fixed back to what it was: live again, with nothing to recompile.
        assert!(!layers.prepared(3, key(3, 10), Ok(good.clone())));
        assert_eq!(layers.state(3), Some(&live));

        // A new layer that never compiled is out of the stack while it is broken.
        assert!(!layers.prepared(4, key(4, 1), broken(4)));
        assert_eq!(layers.effective(&[3, 4]), vec![good]);
        assert!(!layers.is_running(4));
    }

    #[test]
    fn the_layers_to_blame_are_the_named_ones_that_changed_else_every_change() {
        let mut layers = Layers::default();
        for id in [1, 2, 3] {
            layers.prepared(
                id,
                key(id, 1),
                Ok(prepared(id, LayerStage::TonemapPost, GOOD)),
            );
        }
        layers.promote(&[1, 2, 3], &LayerState::Pending);
        layers.prepared(
            2,
            key(2, 2),
            Ok(prepared(2, LayerStage::TonemapPost, BETTER)),
        );
        layers.prepared(
            3,
            key(3, 2),
            Ok(prepared(3, LayerStage::TonemapPost, BETTER)),
        );
        // The compiler named layer 3, which changed.
        assert_eq!(culprits(&[3], &[1, 2, 3], &layers), vec![3]);
        // It named layer 1, which did not change: the edits are to blame.
        assert_eq!(culprits(&[1], &[1, 2, 3], &layers), vec![2, 3]);
        // A refused blob names no layer.
        assert_eq!(culprits(&[], &[1, 2, 3], &layers), vec![2, 3]);

        // Undoing them puts the stack back where the live variant has it.
        let before: Vec<PreparedLayer> = layers.effective(&[1, 2, 3]);
        assert_eq!(layers.fail(&[3], error("refused")), vec![3]);
        let after = layers.effective(&[1, 2, 3]);
        assert_eq!(after[2], prepared(3, LayerStage::TonemapPost, GOOD));
        assert_eq!(after[1], before[1]);
        assert_eq!(layers.changed(&[1, 2, 3]), vec![2]);
        // It stays out until its file changes again.
        assert!(!layers.needs_prepare(3, &key(3, 2)));
        assert!(layers.needs_prepare(3, &key(3, 3)));
    }

    #[test]
    fn the_same_failed_text_saved_again_keeps_its_error_and_is_not_compiled() {
        let mut layers = Layers::default();
        let v1 = prepared(5, LayerStage::TonemapPost, GOOD);
        layers.prepared(5, key(5, 1), Ok(v1.clone()));
        let live = LayerState::Live {
            compile_ms: 20.0,
            verified: 7,
        };
        layers.promote(&[5], &live);
        // Version 2 adds a uniform and fails to compile.
        let v2 = prepared(5, LayerStage::TonemapPost, WITH_UNIFORM);
        assert!(layers.prepared(5, key(5, 2), Ok(v2.clone())));
        assert_eq!(layers.state(5), Some(&LayerState::Pending));
        assert_eq!(layers.fail(&[5], error("did not compile")), vec![5]);
        assert_eq!(layers.effective(&[5]), vec![v1.clone()]);

        // The very same text saved again: a new stamp, the same version. It stays out, with its
        // error, and nothing about the stack changes, so nothing is queued.
        assert!(!layers.prepared(5, key(5, 3), Ok(v2.clone())));
        assert_eq!(layers.effective(&[5]), vec![v1.clone()]);
        assert_eq!(
            layers.state(5),
            Some(&LayerState::Error {
                summary: "did not compile".to_owned(),
                running: true
            })
        );
        // The block is packed for what runs: version 1 declares no uniform.
        assert_eq!(layers.live_params().get(&5), Some(&Vec::new()));

        // Other text is compiled.
        let v3 = prepared(5, LayerStage::TonemapPost, BETTER);
        assert!(layers.prepared(5, key(5, 4), Ok(v3.clone())));
        assert_eq!(layers.effective(&[5]), vec![v3]);
        assert_eq!(layers.state(5), Some(&LayerState::Pending));

        // "Compile now", or the compiler's files changing, gives the failed version another go.
        layers.prepared(5, key(5, 5), Ok(v2.clone()));
        assert_eq!(layers.effective(&[5]), vec![v1]);
        layers.forget_keys();
        assert!(layers.prepared(5, key(5, 5), Ok(v2.clone())));
        assert_eq!(layers.effective(&[5]), vec![v2]);
    }

    #[test]
    fn the_block_is_packed_from_the_version_the_live_variants_carry() {
        let mut layers = Layers::default();
        // Never compiled: nothing to pack.
        layers.prepared(
            6,
            key(6, 1),
            Ok(prepared(6, LayerStage::TonemapPost, WITH_UNIFORM)),
        );
        assert!(layers.live_params().is_empty());
        // Published: its uniform is packed.
        layers.promote(&[6], &LayerState::Pending);
        assert_eq!(layers.live_params()[&6].len(), 1);
        // A newer version without the uniform is compiling: the live one is still packed.
        layers.prepared(6, key(6, 2), Ok(prepared(6, LayerStage::TonemapPost, GOOD)));
        assert_eq!(layers.live_params()[&6].len(), 1);
        // With no compiler, the edit waits out of the stack, and the block stays as it runs.
        let changed = layers.changed(&[6]);
        layers.fail(&changed, |_, _| LayerState::NoCompiler {
            why: "no dxcompiler.dll".to_owned(),
        });
        assert_eq!(layers.live_params()[&6].len(), 1);
        assert!(matches!(
            layers.state(6),
            Some(LayerState::NoCompiler { .. })
        ));
        // Published at last: the new declarations are packed.
        layers.forget_keys();
        layers.prepared(6, key(6, 3), Ok(prepared(6, LayerStage::TonemapPost, GOOD)));
        layers.promote(&[6], &LayerState::Pending);
        assert!(layers.live_params()[&6].is_empty());
    }

    #[test]
    fn a_removed_layer_keeps_its_slot_while_a_live_variant_reads_it() {
        let mut project = Project {
            layers: vec![
                layer(1, LayerStage::TonemapPost),
                layer(2, LayerStage::Lighting),
            ],
            ..Project::default()
        };
        let first = assign_slots_keeping(&project, &[], &BTreeMap::new());
        assert_eq!(first, BTreeMap::from([(1, 0), (2, 1)]));

        // Layer 1 is removed and layer 7 added, while the live tonemap variant still has 1.
        project.layers = vec![layer(2, LayerStage::Lighting), layer(7, LayerStage::Albedo)];
        let live: [&[u32]; 2] = [&[1], &[2]];
        let held = assign_slots_keeping(&project, &live, &first);
        assert_eq!(held.get(&1), Some(&0));
        assert_eq!(held.get(&2), Some(&1));
        assert_eq!(held.get(&7), Some(&2));

        // Once no live variant has it, the slot is free again.
        let live: [&[u32]; 1] = [&[2]];
        let freed = assign_slots_keeping(&project, &live, &held);
        assert_eq!(freed.get(&1), None);
        assert_eq!(freed.get(&7), Some(&2));
        let mut later = project.clone();
        later.layers.push(layer(9, LayerStage::Albedo));
        assert_eq!(assign_slots_keeping(&later, &[], &freed).get(&9), Some(&0));
    }

    #[test]
    fn a_fidelity_test_turns_every_layer_off_and_leaves_the_rest() {
        let mut block = Block::new();
        for slot in [0, 5, MAX_LAYERS - 1] {
            block.set_register(slot_header(slot), [1.0_f32.to_bits(), 7, 8, 9]);
            block.set_register(slot_header(slot) + 1, [4; 4]);
        }
        block.set_register(params::reg::CEL, [3; 4]);
        silence_layers(&mut block);
        for slot in [0, 5, MAX_LAYERS - 1] {
            assert_eq!(block.register(slot_header(slot)), Some([0, 7, 8, 9]));
            assert_eq!(block.register(slot_header(slot) + 1), Some([4; 4]));
        }
        assert_eq!(block.register(params::reg::CEL), Some([3; 4]));
    }

    #[test]
    fn only_drawing_layers_in_a_live_variant_give_their_stage_content() {
        let mut off = layer(3, LayerStage::Albedo);
        off.enabled = false;
        let mut project = Project {
            layers: vec![
                layer(1, LayerStage::TonemapPre),
                layer(2, LayerStage::Lighting),
                off,
            ],
            ..Project::default()
        };
        // By kind: Ao, AoSsr, CmbAlbedo, Tonemap, TonemapLut. Layer 1 is live in both tonemaps, 3
        // in the albedo port; 2 never compiled.
        let live: [&[u32]; PortKind::COUNT] = [&[], &[], &[3], &[1], &[1]];
        // The lighting layer has no variant and the albedo one is off.
        assert_eq!(layer_stages(&project, &live), Stage::Tonemap.bit());
        // Turned back on, the albedo layer draws with no recompile.
        project.layers[2].enabled = true;
        assert_eq!(
            layer_stages(&project, &live),
            Stage::Tonemap.bit() | Stage::Albedo.bit()
        );
        // A layer soloed elsewhere silences the rest.
        project.layers[0].solo = true;
        assert_eq!(layer_stages(&project, &live), Stage::Tonemap.bit());
        assert_eq!(layer_stages(&project, &[]), 0);
    }

    #[test]
    fn a_layer_moved_to_another_stage_draws_where_it_is_compiled_in() {
        // Layer 4 is live in the tonemap kinds; the panel has just set its stage to Lighting.
        let project = Project {
            layers: vec![layer(4, LayerStage::Lighting)],
            ..Project::default()
        };
        let live: [&[u32]; PortKind::COUNT] = [&[], &[], &[], &[4], &[4]];
        // The tonemap ports keep it until the move is built, and the AO ports, which do not have
        // it, are not redirected for it.
        assert_eq!(layer_stages(&project, &live), Stage::Tonemap.bit());
        // Once the lighting build is live, the bits follow it.
        let moved: [&[u32]; PortKind::COUNT] = [&[4], &[4], &[], &[], &[]];
        assert_eq!(layer_stages(&project, &moved), Stage::Lighting.bit());
        // One kind of a stage is enough for the stage.
        let one: [&[u32]; PortKind::COUNT] = [&[], &[4], &[], &[], &[]];
        assert_eq!(layer_stages(&project, &one), Stage::Lighting.bit());
    }

    #[test]
    fn an_id_naming_another_file_does_not_fall_back_to_the_old_files_code() {
        let mut layers = Layers::default();
        let old = prepared(6, LayerStage::TonemapPost, GOOD);
        layers.prepared(6, key(6, 1), Ok(old));
        layers.promote(&[6], &LayerState::Pending);
        // The layer is deleted and a new one takes its id, in a file that does not prepare.
        let mut other = key(6, 1);
        other.file = "6-something-else.hlsl".to_owned();
        assert!(layers.prepared(6, other, broken(6)));
        assert!(layers.effective(&[6]).is_empty());
        assert!(!layers.is_running(6));
    }

    #[test]
    fn each_kind_reads_its_own_probe_region() {
        assert_eq!(region_of(PortKind::Ao), Region::Ao);
        assert_eq!(region_of(PortKind::AoSsr), Region::Ao);
        assert_eq!(region_of(PortKind::CmbAlbedo), Region::Albedo);
        assert_eq!(region_of(PortKind::Tonemap), Region::Tonemap);
        assert_eq!(region_of(PortKind::TonemapLut), Region::Tonemap);
    }

    #[test]
    fn the_embedded_interface_is_each_port_less_its_space42_parameters() {
        for kind in PortKind::ALL {
            let Ok(interface) = embedded_interface(kind) else {
                panic!("{kind:?} did not read");
            };
            assert!(!interface.binds.is_empty(), "{kind:?}");
            assert!(
                interface
                    .binds
                    .iter()
                    .all(|bind| bind.space != REGISTER_SPACE),
                "{kind:?}"
            );
            // The port itself passes against it with the full ABI, and not with none of it.
            let dxil = kind.replacement().dxil;
            assert!(
                crate::verify::verify(dxil, &interface, crate::verify::Space42Set::FULL).is_ok(),
                "{kind:?}"
            );
            assert!(
                crate::verify::verify(dxil, &interface, crate::verify::Space42Set::NONE).is_err(),
                "{kind:?}"
            );
        }
    }

    #[test]
    fn an_empty_stack_hashes_as_the_embedded_source_and_the_variant_id_does_not_count() {
        for kind in PortKind::ALL {
            let Ok(empty) = super::super::codegen::generate(kind, &[]) else {
                panic!("{kind:?}");
            };
            let Ok(again) = super::super::codegen::generate(kind, &[]) else {
                panic!("{kind:?}");
            };
            assert_eq!(generated_hash(&empty), generated_hash(&again));
            let one = [prepared(1, LayerStage::TonemapPost, GOOD)];
            let Ok(with) = super::super::codegen::generate(kind, &one) else {
                panic!("{kind:?}");
            };
            // Only the tonemap kinds take a tonemap layer.
            assert_eq!(
                generated_hash(&with) != generated_hash(&empty),
                kind.stage() == Stage::Tonemap,
                "{kind:?}"
            );
        }
    }

    #[test]
    fn serials_count_from_1_and_never_reach_the_embedded_0() {
        assert_eq!(next_serial(0), 1);
        assert_eq!(next_serial(1), 2);
        assert_eq!(next_serial(SERIAL_MASK - 1), SERIAL_MASK);
        assert_eq!(next_serial(SERIAL_MASK), 1);
        assert_eq!(
            super::super::variant_id(PortKind::Tonemap, next_serial(SERIAL_MASK)),
            0x0300_0001
        );
    }

    #[test]
    fn the_root_signature_is_read_from_the_stream_the_variant_is_made_from() {
        const ROOT: usize = 0x0000_02a0_1234_5670;
        let game_ps = b"DXBC game pixel shader".to_vec();
        let mut stream = Vec::new();
        push(&mut stream, 14, &3_u32.to_le_bytes(), 4);
        push(&mut stream, 0, &ROOT.to_le_bytes(), 8);
        let shader = ShaderBytecode {
            bytecode: game_ps.as_ptr(),
            length: game_ps.len(),
        };
        // SAFETY: a live `ShaderBytecode`, read as its 16 bytes.
        let bytes = unsafe {
            std::slice::from_raw_parts(
                (&raw const shader).cast::<u8>(),
                size_of::<ShaderBytecode>(),
            )
        };
        push(&mut stream, 2, bytes, 8);
        // SAFETY: the stream's one pointer aims at `game_ps`, live for the copy.
        let record =
            unsafe { PipelineRecord::copy(&stream, &test_layout, &mut ShaderStore::new()) };
        let Ok(record) = record else {
            panic!("not copied");
        };
        assert_eq!(stream_root_signature(&record, b"DXBC variant"), ROOT);
        assert_eq!(record.root_signature(), ROOT);

        // A stream with no root signature subobject gives 0, which rule 7 refuses.
        let mut bare = Vec::new();
        push(&mut bare, 2, bytes, 8);
        // SAFETY: as above.
        let bare = unsafe { PipelineRecord::copy(&bare, &test_layout, &mut ShaderStore::new()) };
        let Ok(bare) = bare else {
            panic!("not copied");
        };
        assert_eq!(stream_root_signature(&bare, b"DXBC variant"), 0);
    }

    #[test]
    fn a_file_that_stops_being_utf8_is_placed_on_its_line() {
        let bytes = b"uniform float A = 1;\nfloat3 \xff";
        let Err(error) = String::from_utf8(bytes.to_vec()) else {
            panic!("read as UTF-8");
        };
        assert_eq!(position_of(bytes, error.utf8_error().valid_up_to()), (2, 8));
        assert_eq!(position_of(b"\xff", 0), (1, 1));
        assert_eq!(position_of(b"ab", 99), (1, 3));
    }

    #[test]
    fn percentiles_are_nearest_rank() {
        let values = [40.0, 10.0, 30.0, 20.0, f32::NAN];
        assert_eq!(percentile(&values, 50.0), Some(20.0));
        assert_eq!(percentile(&values, 95.0), Some(40.0));
        assert_eq!(percentile(&values, 0.0), Some(10.0));
        assert_eq!(percentile(&[], 50.0), None);
    }

    #[test]
    fn layer_states_read_as_the_panel_shows_them() {
        let live = LayerState::Live {
            compile_ms: 24.4,
            verified: 7,
        };
        assert_eq!(live.label(), "Live: compiled in 24 ms, verified 7 of 7");
        let error = LayerState::Error {
            summary: "x".to_owned(),
            running: true,
        };
        assert_eq!(error.label(), "Error, last good running: x");
        assert!(LayerState::Pending.label().is_ascii());
    }
}
