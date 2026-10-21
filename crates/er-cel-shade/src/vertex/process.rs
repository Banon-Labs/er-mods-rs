//! The Windows half of [`super`]: the registry of mesh pipelines the creation hooks fill, the
//! builder the editor's worker drives, the proof frames the present keeps, and the exports.

use core::ffi::c_void;
use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, LazyLock, Mutex, MutexGuard, PoisonError};
use std::time::Instant;

use er_game_base::log::redirected_artifact_path;
use er_shader_compiler::protocol::{PatchRequest, PatchResponse, PreviousKind};
use windows::Win32::Graphics::Direct3D12::{ID3D12Device2, ID3D12RootSignature};
use windows::core::Interface;

use super::names::{self, Pass};
use super::plan::{self, Activity, Budget, Phase};
use super::rules::{self, VsInterface};
use super::{HOLD_OFF, PROOF_FILE_LIMIT, PROOF_FILE_NAME, PROOF_FRAMES_KEPT, Path, ProofFrame};
use crate::dxbc::{self, DEBUG_NAME_PART};
use crate::exports::{TELEMETRY_FILE_NAME, TELEMETRY_PATH_ENV};
use crate::graveyard::{self, Corpse, OwnedCom, com_add_ref, com_release};
use crate::record::{self, PipelineRecord, ShaderBytecode};
use crate::replace::{self, Payload, SUBOBJECT_ROOT_SIGNATURE, SUBOBJECT_VS, VERTEX_STAGE_BIT};
use crate::table::{self, EntrySpec, VERTEX_PORT, flag};
use crate::{cmdlist, hooks, log, look_binding, params, probe};

/// A game vertex shader by its length and FNV-1, which is how the record store shares them.
type VsKey = (usize, u64);

/// How often the builder reads the bind counters, syncs the counting entries and logs.
const SCAN_MS: u64 = 250;
/// How often it probes the recorded pipelines for ones the game let go.
const REAP_MS: u64 = 10_000;
/// How often it logs its line while it has work.
const LOG_MS: u64 = 2_000;
/// Timings kept for the percentiles.
const TIMINGS_KEPT: usize = 512;
/// Groups the telemetry details, busiest first.
const DETAILED_GROUPS: usize = 32;
/// Refusals logged in full.
const REFUSALS_LOGGED: u64 = 16;

/// Mesh pipelines seen on each creation path, recorded on each, and not recorded because the
/// description could not be copied.
static SEEN: [AtomicU64; Path::COUNT] = [const { AtomicU64::new(0) }; Path::COUNT];
static RECORDED: [AtomicU64; Path::COUNT] = [const { AtomicU64::new(0) }; Path::COUNT];
static NOT_RECORDED: AtomicU64 = AtomicU64::new(0);
/// Live groups suspended because the game created a pipeline for them.
static SUSPENSIONS: AtomicU64 = AtomicU64::new(0);
/// Panics caught in the creation hook's share.
static PANICS: AtomicU64 = AtomicU64::new(0);
/// Recorded pipelines the game let go of.
static REAPED: AtomicU64 = AtomicU64::new(0);
/// Records made, for each one's serial.
static SERIALS: AtomicU64 = AtomicU64::new(0);

/// One recorded mesh pipeline: a reference on the game's pipeline and on its root signature, so
/// neither address can be reused while it is known here, and its description.
struct MeshPipeline {
    game: OwnedCom,
    /// Which record of `game` this is: an address the game let go and reused is another record.
    serial: u64,
    group: u32,
    pass: Pass,
    vs_key: VsKey,
    record: PipelineRecord,
    root: Option<OwnedCom>,
}

impl Drop for MeshPipeline {
    fn drop(&mut self) {
        // Variants made from this description may still be in flight, so the root signature
        // they were created with goes to the graveyard, as a port record's does.
        if let Some(root) = self.root.take() {
            graveyard::retire(Corpse::Com(root));
        }
    }
}

/// One material group: every recorded pipeline whose vertex shader's debug name has its key.
struct Group {
    key: String,
    members: BTreeSet<usize>,
    /// Its entries carry variants of the current carrier.
    live: bool,
    /// A pipeline arrived while it was live: it is suspended and must be built again.
    dirty: bool,
}

#[derive(Default)]
struct Registry {
    pipelines: HashMap<usize, Arc<MeshPipeline>>,
    groups: Vec<Group>,
    index: HashMap<String, u32>,
}

static REGISTRY: LazyLock<Mutex<Registry>> = LazyLock::new(|| Mutex::new(Registry::default()));

fn registry() -> MutexGuard<'static, Registry> {
    REGISTRY.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Count a panic the creation hook caught in [`on_created`].
pub fn note_panic() {
    if PANICS.fetch_add(1, Ordering::Relaxed) == 0 {
        log(format_args!(
            "vertex: a panic while recording a pipeline; its group is not recorded whole and is \
             not built (logged once)"
        ));
    }
}

/// The debug name of the vertex shader in `stream`, and its bytes.
///
/// # Safety
///
/// `stream` must be one D3D12 just accepted, its shader pointers valid.
unsafe fn vertex_shader<'a>(
    stream: &'a [u8],
    layout: &dyn Fn(u32) -> Option<Payload>,
) -> Option<(&'a [u8], String)> {
    let found = replace::subobjects(stream, layout).ok()?;
    let at = found.iter().find(|s| s.ty == SUBOBJECT_VS)?.offset;
    if at + size_of::<ShaderBytecode>() > stream.len() {
        return None;
    }
    // SAFETY: inside the stream, checked above.
    let shader: ShaderBytecode =
        unsafe { std::ptr::read_unaligned(stream.as_ptr().add(at).cast()) };
    if shader.bytecode.is_null() || shader.length == 0 {
        return None;
    }
    // SAFETY: the caller's contract: the bytecode is valid for its length.
    let bytes = unsafe { std::slice::from_raw_parts(shader.bytecode, shader.length) };
    let name = dxbc::Container::parse(bytes).ok()?.ildn().ok()?.to_owned();
    Some((bytes, name))
}

/// The game just created `game` from `stream` on `path`: record it when its vertex shader is a
/// mesh pass's (design 7.1). A pipeline for a group that is live suspends the group.
///
/// # Safety
///
/// `game` must be the live pipeline just created from `stream`, which D3D12 just accepted, and
/// `root_signature` 0 or its live root signature.
pub unsafe fn on_created(
    path: Path,
    game: usize,
    stream: &[u8],
    root_signature: usize,
    layout: &dyn Fn(u32) -> Option<Payload>,
) {
    let Some((vs, debug_name)) = (unsafe { vertex_shader(stream, layout) }) else {
        return;
    };
    let Some(mesh) = names::mesh_name(&debug_name) else {
        return;
    };
    SEEN[path as usize].fetch_add(1, Ordering::Relaxed);
    let record = match unsafe { record::capture(stream, layout) } {
        Ok(record) => record,
        Err(error) => {
            if NOT_RECORDED.fetch_add(1, Ordering::Relaxed) < 4 {
                log(format_args!(
                    "vertex: a {} pipeline of `{}` via {} is not recorded: {error}; its group is \
                     not built",
                    mesh.pass.token(),
                    mesh.group,
                    path.name()
                ));
            }
            return;
        }
    };
    let vs_key = (vs.len(), replace::fnv1_64(vs));
    // SAFETY: the pipeline just created and its root signature are alive; each reference taken
    // here is the one the record holds.
    let (game_ref, root) = unsafe {
        com_add_ref(game);
        let root = (root_signature != 0).then(|| {
            com_add_ref(root_signature);
            OwnedCom::from_raw(root_signature)
        });
        (OwnedCom::from_raw(game), root)
    };
    let mut registry = registry();
    let group = match registry.index.get(&mesh.group) {
        Some(&group) => group,
        None => {
            let group = registry.groups.len() as u32;
            registry.groups.push(Group {
                key: mesh.group.clone(),
                members: BTreeSet::new(),
                live: false,
                dirty: false,
            });
            registry.index.insert(mesh.group, group);
            group
        }
    };
    let pipeline = Arc::new(MeshPipeline {
        game: game_ref,
        serial: SERIALS.fetch_add(1, Ordering::Relaxed) + 1,
        group,
        pass: mesh.pass,
        vs_key,
        record,
        root,
    });
    if let Some(old) = registry.pipelines.insert(game, pipeline)
        && let Some(previous) = registry.groups.get_mut(old.group as usize)
        && old.group != group
    {
        previous.members.remove(&game);
    }
    let Some(entry) = registry.groups.get_mut(group as usize) else {
        return;
    };
    entry.members.insert(game);
    if entry.live {
        // Every pipeline of the group binds the game's own until the builder has this one's
        // variant too: no group is ever partly moved.
        for member in &entry.members {
            if let Some(found) = table::get(*member) {
                found
                    .flags
                    .fetch_or(flag::VERTEX_SUSPENDED, Ordering::AcqRel);
            }
        }
        entry.dirty = true;
        SUSPENSIONS.fetch_add(1, Ordering::Relaxed);
    }
    RECORDED[path as usize].fetch_add(1, Ordering::Relaxed);
}

/// The device a root signature belongs to, which a variant of its pipeline is created on.
fn device_of(root_signature: usize) -> Result<ID3D12Device2, String> {
    let raw = root_signature as *mut c_void;
    // SAFETY: a root signature the record holds a reference on.
    let Some(root) = (unsafe { ID3D12RootSignature::from_raw_borrowed(&raw) }) else {
        return Err("the record names no root signature".to_owned());
    };
    let mut device: Option<ID3D12Device2> = None;
    // SAFETY: a live device child and an out interface.
    unsafe { root.GetDevice(&mut device) }
        .map_err(|error| format!("the root signature's device: {error}"))?;
    device.ok_or_else(|| "the root signature has no ID3D12Device2".to_owned())
}

/// A pointer-sized value of `stream` at the root signature subobject, 0 when there is none.
fn stream_root(record: &PipelineRecord, stream: &[u8]) -> usize {
    record
        .subobjects()
        .iter()
        .find(|subobject| subobject.ty == SUBOBJECT_ROOT_SIGNATURE)
        .and_then(|subobject| stream.get(subobject.offset..subobject.offset + size_of::<usize>()))
        .and_then(|bytes| <[u8; size_of::<usize>()]>::try_from(bytes).ok())
        .map_or(0, usize::from_le_bytes)
}

/// A spliced vertex shader, or why there is none.
#[derive(Clone)]
enum Patched {
    Ok(Arc<[u8]>),
    Refused { class: String, detail: String },
}

/// One group's build for the current carrier.
#[derive(Default)]
struct GroupBuild {
    phase: Option<Phase>,
    /// Variants made and not yet published, which the builder owns: game pipeline to variant.
    made: BTreeMap<usize, (usize, u64)>,
    /// Variants the table owns: game pipeline to variant and the record serial it was made for.
    published: BTreeMap<usize, (usize, u64)>,
    /// Game pipelines still to create a variant for.
    pending: Vec<usize>,
    splice_ms: f32,
    create_ms_max: f32,
}

impl GroupBuild {
    fn phase(&self) -> Phase {
        self.phase.clone().unwrap_or(Phase::Pending)
    }

    /// Release every variant made and not published.
    fn release_made(&mut self) {
        for (_, (variant, _)) in std::mem::take(&mut self.made) {
            // SAFETY: a variant made by the builder, never published, so never bound.
            unsafe { com_release(variant) };
        }
    }
}

/// The carrier the builder builds from.
struct Generation {
    id: u64,
    carrier: Vec<u8>,
    hash: u64,
    layers: Vec<u32>,
    /// The table swap that ends the previous carrier's variants; nothing is published before the
    /// present applies it.
    ticket: u64,
    compile_ms: f32,
}

/// What the telemetry and the panel show of the vertex stage.
#[derive(Clone, Debug, Default)]
pub struct Status {
    pub generation: u64,
    pub carrier_hash: u64,
    pub carrier_ms: f32,
    pub layers: Vec<u32>,
    pub waiting_for_present: bool,
    pub recorded: usize,
    pub groups_total: usize,
    pub groups_wanted: usize,
    pub phases: BTreeMap<&'static str, usize>,
    pub pipelines_wanted: usize,
    pub pipelines_live: usize,
    pub vs_patched: usize,
    pub vs_refused: usize,
    pub refusals: BTreeMap<String, u64>,
    pub splice_ms: Vec<f32>,
    pub create_ms: Vec<f32>,
    pub details: Vec<GroupDetail>,
}

/// One group, as the telemetry details it.
#[derive(Clone, Debug, Default)]
pub struct GroupDetail {
    pub key: String,
    pub phase: String,
    pub pipelines: usize,
    pub passes: u32,
    pub binds_recent: u64,
    pub splice_ms: f32,
    pub create_ms_max: f32,
    pub why: String,
}

impl Status {
    /// The panel's chip, its state and its words: `preparing 31/40 groups, 212/540 pipelines`
    /// ([`super::chip`]).
    #[must_use]
    pub fn chip(&self) -> (super::ChipState, String) {
        super::chip(&super::ChipCounts {
            generation: self.generation,
            groups_live: self.phases.get("live").copied().unwrap_or(0),
            groups_wanted: self.groups_wanted,
            pipelines_live: self.pipelines_live,
            pipelines_wanted: self.pipelines_wanted,
            waiting_for_present: self.waiting_for_present,
        })
    }
}

static STATUS: LazyLock<Mutex<Status>> = LazyLock::new(|| Mutex::new(Status::default()));

/// The builder's last status.
#[must_use]
pub fn status() -> Status {
    STATUS
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
}

/// The editor worker's half of the vertex stage: it builds the current carrier's variants group by
/// group and publishes each group whole.
pub struct Builder {
    generation: Option<Generation>,
    next_id: u64,
    builds: HashMap<u32, GroupBuild>,
    activity: HashMap<u32, Activity>,
    patched: HashMap<VsKey, Patched>,
    interfaces: HashMap<VsKey, Arc<VsInterface>>,
    current: Option<u32>,
    budget: Budget,
    splice_ms: VecDeque<f32>,
    create_ms: VecDeque<f32>,
    refusals: BTreeMap<String, u64>,
    refusals_logged: u64,
    next_scan_ms: u64,
    next_reap_ms: u64,
    next_log_ms: u64,
    logged_idle: bool,
}

impl Default for Builder {
    fn default() -> Self {
        Self::new()
    }
}

fn push_recent(recent: &mut VecDeque<f32>, value: f32) {
    if recent.len() >= TIMINGS_KEPT {
        recent.pop_front();
    }
    recent.push_back(value);
}

impl Builder {
    #[must_use]
    pub fn new() -> Self {
        Self {
            generation: None,
            next_id: 0,
            builds: HashMap::new(),
            activity: HashMap::new(),
            patched: HashMap::new(),
            interfaces: HashMap::new(),
            current: None,
            budget: Budget::default(),
            splice_ms: VecDeque::new(),
            create_ms: VecDeque::new(),
            refusals: BTreeMap::new(),
            refusals_logged: 0,
            next_scan_ms: 0,
            next_reap_ms: REAP_MS,
            next_log_ms: 0,
            logged_idle: true,
        }
    }

    /// The layers compiled into the current carrier.
    #[must_use]
    pub fn live_layers(&self) -> &[u32] {
        self.generation
            .as_ref()
            .map_or(&[], |generation| generation.layers.as_slice())
    }

    /// The current carrier's hash, 0 with none.
    #[must_use]
    pub fn carrier_hash(&self) -> u64 {
        self.generation
            .as_ref()
            .map_or(0, |generation| generation.hash)
    }

    /// The edit that ends every vertex redirect and keeps the counting entries, for the present
    /// that publishes a new carrier's parameter block. Its variants retire as one grave.
    pub fn clear_edit() -> table::Edit {
        Box::new(|builder: &mut table::Builder| {
            for key in builder.vertex_keys() {
                if let Some(spec) = builder.get(key).copied() {
                    builder.insert(EntrySpec {
                        redirect: 0,
                        flags: spec.flags & !flag::VERTEX_SUSPENDED,
                        ..spec
                    });
                }
            }
        })
    }

    /// Forget the previous carrier's builds: its unpublished variants are released, and its
    /// published ones belong to the table, which the clear edit retires.
    fn forget_builds(&mut self) {
        for build in self.builds.values_mut() {
            build.release_made();
        }
        self.builds.clear();
        self.patched.clear();
        self.current = None;
        let mut registry = registry();
        for group in &mut registry.groups {
            group.live = false;
            group.dirty = false;
        }
    }

    /// A new carrier: `carrier` is the compiled container the helper accepted, `hash` its source's
    /// hash, `layers` the layers in it, and `ticket` the deferred swap that clears the old
    /// redirects with the block packed for these layers.
    pub fn begin(
        &mut self,
        carrier: Vec<u8>,
        hash: u64,
        layers: Vec<u32>,
        ticket: u64,
        compile_ms: f32,
    ) {
        self.forget_builds();
        self.next_id += 1;
        log(format_args!(
            "vertex: carrier {} ({hash:016x}, {} bytes, layers {layers:?}) compiled in \
             {compile_ms:.1} ms; groups build as they are seen",
            self.next_id,
            carrier.len()
        ));
        self.generation = Some(Generation {
            id: self.next_id,
            carrier,
            hash,
            layers,
            ticket,
            compile_ms,
        });
        self.next_scan_ms = 0;
        self.logged_idle = false;
    }

    /// The deferred swap the current carrier waits for: [`Self::begin`] is called before the
    /// block is packed, because the block reads [`Self::live_layers`], so the ticket arrives here.
    pub fn set_ticket(&mut self, ticket: u64) {
        if let Some(generation) = &mut self.generation {
            generation.ticket = ticket;
        }
    }

    /// The deferred swap the current carrier waits for, 0 with none.
    #[must_use]
    pub fn ticket(&self) -> u64 {
        self.generation
            .as_ref()
            .map_or(0, |generation| generation.ticket)
    }

    /// No vertex layer any more: the redirects and the counting entries go with the next swap.
    pub fn end(&mut self) {
        if self.generation.take().is_some() {
            log(format_args!(
                "vertex: no vertex layer; every mesh pipeline binds the game's own"
            ));
        }
        self.forget_builds();
        self.next_scan_ms = 0;
    }

    /// One step of work, bounded: a scan when one is due, then one batch of splicing, or the
    /// pipelines this present's budget allows, or a publish. Returns whether more work waits, for
    /// the worker's pacing. `patch` sends a request to the helper.
    pub fn step(
        &mut self,
        patch: &mut dyn FnMut(PatchRequest) -> Result<PatchResponse, String>,
    ) -> bool {
        let now = graveyard::now_ms();
        if now >= self.next_scan_ms {
            self.next_scan_ms = now + SCAN_MS;
            self.scan(now);
        }
        let Some(generation) = &self.generation else {
            return false;
        };
        if !table::is_applied(generation.ticket) {
            return true;
        }
        if let Some(group) = self.current {
            self.create_some(group);
            return true;
        }
        let order: Vec<(u32, Activity)> = self
            .activity
            .iter()
            .map(|(group, activity)| (*group, *activity))
            .collect();
        let next = plan::build_order(&order, now).into_iter().find(|group| {
            matches!(
                self.builds.get(group).map(GroupBuild::phase),
                None | Some(Phase::Pending)
            )
        });
        match next {
            Some(group) => {
                self.prepare(group, patch);
                true
            }
            None => {
                if !self.logged_idle {
                    self.logged_idle = true;
                    self.log_line("every group seen so far is built");
                }
                false
            }
        }
    }

    /// The periodic part: counting entries, bind activity, dirty groups, the reaper, the status
    /// and the log line.
    fn scan(&mut self, now: u64) {
        if now >= self.next_reap_ms {
            self.next_reap_ms = now + REAP_MS;
            self.reap();
        }
        self.sync_counting();
        let registry = registry();
        for (at, group) in registry.groups.iter().enumerate() {
            let total: u64 = group
                .members
                .iter()
                .filter_map(|member| table::get(*member))
                .map(|entry| u64::from(entry.binds.load(Ordering::Relaxed)))
                .sum();
            self.activity.entry(at as u32).or_default().read(total, now);
            if group.dirty
                && let Some(build) = self.builds.get_mut(&(at as u32))
                && build.phase() == Phase::Live
            {
                build.phase = Some(Phase::Pending);
                self.logged_idle = false;
            }
        }
        let status = self.status_of(&registry, now);
        drop(registry);
        *STATUS.lock().unwrap_or_else(PoisonError::into_inner) = status;
        if self.generation.is_some() && now >= self.next_log_ms && !self.logged_idle {
            self.next_log_ms = now + LOG_MS;
            self.log_line("building");
        }
    }

    /// Keep a counting entry for every recorded pipeline while a carrier exists, and none while
    /// none does.
    fn sync_counting(&self) {
        let wanted: BTreeMap<usize, Pass> = if self.generation.is_some() {
            registry()
                .pipelines
                .iter()
                .map(|(game, pipeline)| (*game, pipeline.pass))
                .collect()
        } else {
            BTreeMap::new()
        };
        let present: BTreeSet<usize> = table::published()
            .map(|table| {
                table
                    .iter()
                    .filter(|entry| entry.port == VERTEX_PORT)
                    .map(|entry| entry.key)
                    .collect()
            })
            .unwrap_or_default();
        let missing: Vec<(usize, Pass)> = wanted
            .iter()
            .filter(|(game, _)| !present.contains(game))
            .map(|(game, pass)| (*game, *pass))
            .collect();
        let stale: Vec<usize> = present
            .iter()
            .filter(|game| !wanted.contains_key(game))
            .copied()
            .collect();
        if missing.is_empty() && stale.is_empty() {
            return;
        }
        table::update(|builder| {
            for (game, pass) in missing {
                builder.insert(EntrySpec {
                    port: VERTEX_PORT,
                    bits: pass as u8,
                    ..EntrySpec::new(game)
                });
            }
            for game in stale {
                builder.remove(game);
            }
        });
        cmdlist::refresh_active();
    }

    /// Drop the records of pipelines the game let go of: their only references left are this
    /// registry's and, while one is published, the table's.
    fn reap(&mut self) {
        let mut gone = Vec::new();
        {
            let registry = registry();
            for game in registry.pipelines.keys() {
                let ours =
                    1 + u32::from(table::get(*game).is_some_and(table::Entry::holds_reference));
                // SAFETY: the registry holds a reference, so the object is alive.
                let count = unsafe {
                    com_add_ref(*game);
                    com_release(*game)
                };
                if count <= ours {
                    gone.push(*game);
                }
            }
        }
        if gone.is_empty() {
            return;
        }
        let mut registry = registry();
        for game in &gone {
            if let Some(pipeline) = registry.pipelines.remove(game)
                && let Some(group) = registry.groups.get_mut(pipeline.group as usize)
            {
                group.members.remove(game);
            }
        }
        drop(registry);
        for build in self.builds.values_mut() {
            for game in &gone {
                build.published.remove(game);
                if let Some((variant, _)) = build.made.remove(game) {
                    // SAFETY: made by the builder and never published.
                    unsafe { com_release(variant) };
                }
                build.pending.retain(|pending| pending != game);
            }
        }
        REAPED.fetch_add(gone.len() as u64, Ordering::Relaxed);
        log(format_args!(
            "vertex: the game released {} recorded mesh pipeline(s)",
            gone.len()
        ));
    }

    /// Refuse `group` for `why`: it stays vanilla for this carrier.
    fn refuse(&mut self, group: u32, class: &str, why: String) {
        let build = self.builds.entry(group).or_default();
        build.release_made();
        build.pending.clear();
        build.phase = Some(Phase::Refused(why.clone()));
        *self.refusals.entry(class.to_owned()).or_default() += 1;
        if self.current == Some(group) {
            self.current = None;
        }
        if self.refusals_logged < REFUSALS_LOGGED {
            self.refusals_logged += 1;
            let key = registry()
                .groups
                .get(group as usize)
                .map(|g| g.key.clone())
                .unwrap_or_default();
            log(format_args!(
                "vertex: group `{key}` stays vanilla ({class}): {why}"
            ));
        }
    }

    /// The group's members, as the registry holds them now.
    fn members(group: u32) -> Vec<Arc<MeshPipeline>> {
        let registry = registry();
        registry
            .groups
            .get(group as usize)
            .map(|g| {
                g.members
                    .iter()
                    .filter_map(|member| registry.pipelines.get(member).cloned())
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Splice whatever the group's shaders lack, check every pipeline of the group, and start its
    /// creation; or refuse it.
    fn prepare(
        &mut self,
        group: u32,
        patch: &mut dyn FnMut(PatchRequest) -> Result<PatchResponse, String>,
    ) {
        let members = Self::members(group);
        if members.is_empty() {
            self.refuse(
                group,
                "empty",
                "no recorded pipeline is left in it".to_owned(),
            );
            return;
        }
        let Some(generation) = &self.generation else {
            return;
        };
        let carrier = generation.carrier.clone();
        let mut missing: Vec<(VsKey, Arc<[u8]>)> = Vec::new();
        for member in &members {
            if self.patched.contains_key(&member.vs_key)
                || missing.iter().any(|(key, _)| *key == member.vs_key)
            {
                continue;
            }
            match member.record.vs_shared() {
                Some(vs) => missing.push((member.vs_key, vs)),
                None => {
                    self.refuse(group, "no_vs", "a pipeline has no vertex shader".to_owned());
                    return;
                }
            }
        }
        let started = Instant::now();
        let sizes: Vec<usize> = missing.iter().map(|(_, vs)| vs.len()).collect();
        for range in plan::batches(&sizes) {
            let items = &missing[range];
            self.patch_batch(&carrier, items, patch);
        }
        let splice_ms = started.elapsed().as_secs_f32() * 1000.0;
        // Every shader of the group must have patched, or none of it moves.
        for member in &members {
            match self.patched.get(&member.vs_key) {
                Some(Patched::Ok(_)) => {}
                Some(Patched::Refused { class, detail }) => {
                    let (class, detail) = (class.clone(), detail.clone());
                    self.refuse(
                        group,
                        &class,
                        format!("its {} shader: {detail}", member.pass.token()),
                    );
                    return;
                }
                None => {
                    self.refuse(group, "unpatched", "a shader got no answer".to_owned());
                    return;
                }
            }
        }
        // Rules 1 to 6 and the blob's half of 7, per pipeline: the root signature decides
        // whether the parameter block is readable.
        for member in &members {
            let Some(Patched::Ok(blob)) = self.patched.get(&member.vs_key).cloned() else {
                continue;
            };
            let interface = match self.interface(member) {
                Ok(interface) => interface,
                Err(why) => {
                    self.refuse(group, "interface", why);
                    return;
                }
            };
            let a_vertex = look_binding::binding(member.record.root_signature())
                .is_some_and(|binding| binding.a_vertex);
            let failures = rules::verify_vertex(&blob, &interface, a_vertex);
            if !failures.is_empty() {
                let report = rules::report(&failures, true);
                self.refuse(
                    group,
                    &format!("rule{}", failures[0].rule.number()),
                    format!(
                        "its {} pipeline {:#x}: {}",
                        member.pass.token(),
                        member.game.as_raw(),
                        report.replace('\n', "; ")
                    ),
                );
                return;
            }
        }
        let build = self.builds.entry(group).or_default();
        build.splice_ms += splice_ms;
        // Pipelines whose variant is already made or published for this very record are kept.
        let have = |game: usize, serial: u64, build: &GroupBuild| {
            build.made.get(&game).is_some_and(|(_, s)| *s == serial)
                || build
                    .published
                    .get(&game)
                    .is_some_and(|(_, s)| *s == serial)
        };
        let pending: Vec<usize> = members
            .iter()
            .filter(|member| !have(member.game.as_raw(), member.serial, build))
            .map(|member| member.game.as_raw())
            .collect();
        build.pending = pending;
        build.phase = Some(Phase::Creating {
            made: members.len() - build.pending.len(),
            total: members.len(),
        });
        self.current = Some(group);
    }

    /// The interface of a member's original vertex shader, read once per shader.
    fn interface(&mut self, member: &MeshPipeline) -> Result<Arc<VsInterface>, String> {
        if let Some(found) = self.interfaces.get(&member.vs_key) {
            return Ok(Arc::clone(found));
        }
        let vs = member
            .record
            .vs()
            .ok_or_else(|| "the record holds no vertex shader".to_owned())?;
        let interface = Arc::new(
            VsInterface::read(vs)
                .map_err(|error| format!("the original does not read: {error}"))?,
        );
        self.interfaces
            .insert(member.vs_key, Arc::clone(&interface));
        Ok(interface)
    }

    /// One patch request; a helper failure on several shaders is tried again one at a time, so one
    /// shader that crashes the assembler costs only itself.
    fn patch_batch(
        &mut self,
        carrier: &[u8],
        items: &[(VsKey, Arc<[u8]>)],
        patch: &mut dyn FnMut(PatchRequest) -> Result<PatchResponse, String>,
    ) {
        let request = PatchRequest {
            id: 0,
            carrier: carrier.to_vec(),
            previous: true,
            items: items.iter().map(|(_, vs)| vs.to_vec()).collect(),
        };
        let response = match patch(request) {
            Ok(response) if response.items.len() == items.len() => response,
            Ok(response) => {
                for (key, _) in items {
                    self.patched.insert(
                        *key,
                        Patched::Refused {
                            class: "helper".to_owned(),
                            detail: format!(
                                "{} answers for {} shaders",
                                response.items.len(),
                                items.len()
                            ),
                        },
                    );
                }
                return;
            }
            Err(why) if items.len() > 1 => {
                log(format_args!(
                    "vertex: a batch of {} shaders failed ({why}); each is sent alone",
                    items.len()
                ));
                for item in items {
                    self.patch_batch(carrier, std::slice::from_ref(item), patch);
                }
                return;
            }
            Err(why) => {
                self.patched.insert(
                    items[0].0,
                    Patched::Refused {
                        class: "helper".to_owned(),
                        detail: why,
                    },
                );
                return;
            }
        };
        if !response.carrier_ok {
            for (key, _) in items {
                self.patched.insert(
                    *key,
                    Patched::Refused {
                        class: "carrier".to_owned(),
                        detail: response.carrier_detail.clone(),
                    },
                );
            }
            return;
        }
        for ((key, original), item) in items.iter().zip(response.items) {
            let outcome = if item.ok {
                push_recent(&mut self.splice_ms, item.ms.iter().sum());
                // The original's debug name is copied in, so diagnostics still name the variant,
                // and the container is signed again over it (design 7.3).
                let named = match VsInterface::read(original).ok().and_then(|i| i.debug_name) {
                    Some(ildn) => dxbc::with_part(&item.blob, DEBUG_NAME_PART, &ildn)
                        .map_err(|error| error.to_string()),
                    None => Ok(item.blob),
                };
                match named {
                    Ok(blob) => {
                        if item.previous == PreviousKind::Skipped {
                            *self
                                .refusals
                                .entry("previous_skipped".to_owned())
                                .or_default() += 1;
                        }
                        Patched::Ok(Arc::from(blob))
                    }
                    Err(why) => Patched::Refused {
                        class: "debug_name".to_owned(),
                        detail: why,
                    },
                }
            } else {
                Patched::Refused {
                    class: item.class,
                    detail: item.detail,
                }
            };
            self.patched.insert(*key, outcome);
        }
    }

    /// Create the group's pending variants while this present's budget lasts; publish it when
    /// none is left.
    fn create_some(&mut self, group: u32) {
        // The budget belongs to the present; where no present comes -- an overlay that never
        // draws -- to each 50 ms instead, so creation never stops for want of one.
        let present = (params::presents() << 20) | ((graveyard::now_ms() / 50) & 0xf_ffff);
        loop {
            let Some(build) = self.builds.get_mut(&group) else {
                self.current = None;
                return;
            };
            let Some(&game) = build.pending.last() else {
                break;
            };
            if !self.budget.allows(present) {
                return;
            }
            let pipeline = registry().pipelines.get(&game).cloned();
            let Some(pipeline) = pipeline else {
                // The reaper took it; the group is published without it.
                build.pending.pop();
                continue;
            };
            let Some(Patched::Ok(blob)) = self.patched.get(&pipeline.vs_key).cloned() else {
                self.refuse(group, "unpatched", "a shader lost its patch".to_owned());
                return;
            };
            let started = Instant::now();
            let created = create(&pipeline, &blob);
            let ms = started.elapsed().as_secs_f32() * 1000.0;
            self.budget.spend(present, ms);
            push_recent(&mut self.create_ms, ms);
            match created {
                Ok(variant) => {
                    let build = self.builds.entry(group).or_default();
                    build.pending.pop();
                    build.made.insert(game, (variant, pipeline.serial));
                    build.create_ms_max = build.create_ms_max.max(ms);
                    if let Some(Phase::Creating { made, .. }) = &mut build.phase {
                        *made += 1;
                    }
                }
                Err((class, why)) => {
                    self.refuse(
                        group,
                        class,
                        format!("its {} pipeline {game:#x}: {why}", pipeline.pass.token()),
                    );
                    return;
                }
            }
        }
        self.publish(group);
        self.current = None;
    }

    /// Every pipeline of the group has its variant: put them all in the table in one swap. A
    /// pipeline the game created since is caught here, under the registry's lock, which the
    /// creation hook takes too, and the group is built again rather than published short of one.
    fn publish(&mut self, group: u32) {
        let mut registry = registry();
        let Some(members) = registry
            .groups
            .get(group as usize)
            .map(|g| g.members.clone())
        else {
            return;
        };
        let Some(build) = self.builds.get_mut(&group) else {
            return;
        };
        let mut entries: Vec<(usize, usize, Pass)> = Vec::with_capacity(members.len());
        for member in &members {
            let Some(pipeline) = registry.pipelines.get(member) else {
                continue;
            };
            let variant = build
                .made
                .get(member)
                .or_else(|| build.published.get(member))
                .filter(|(_, serial)| *serial == pipeline.serial)
                .map(|(variant, _)| *variant);
            match variant {
                Some(variant) => entries.push((*member, variant, pipeline.pass)),
                None => {
                    // A pipeline arrived during the build: build again.
                    build.phase = Some(Phase::Pending);
                    return;
                }
            }
        }
        table::update(|builder| {
            for (game, variant, pass) in &entries {
                let flags = builder.get(*game).map_or(0, |spec| spec.flags);
                builder.insert(EntrySpec {
                    redirect: *variant,
                    port: VERTEX_PORT,
                    bits: *pass as u8,
                    flags: flags & !flag::VERTEX_SUSPENDED,
                    ..EntrySpec::new(*game)
                });
            }
        });
        cmdlist::refresh_active();
        for (game, (variant, serial)) in std::mem::take(&mut build.made) {
            build.published.insert(game, (variant, serial));
        }
        build.published.retain(|game, _| members.contains(game));
        build.phase = Some(Phase::Live);
        if let Some(entry) = registry.groups.get_mut(group as usize) {
            entry.live = true;
            entry.dirty = false;
        }
        drop(registry);
        self.logged_idle = false;
    }

    fn status_of(&self, registry: &Registry, now: u64) -> Status {
        let mut status = Status {
            recorded: registry.pipelines.len(),
            groups_total: registry.groups.len(),
            refusals: self.refusals.clone(),
            splice_ms: self.splice_ms.iter().copied().collect(),
            create_ms: self.create_ms.iter().copied().collect(),
            vs_patched: self
                .patched
                .values()
                .filter(|p| matches!(p, Patched::Ok(_)))
                .count(),
            vs_refused: self
                .patched
                .values()
                .filter(|p| matches!(p, Patched::Refused { .. }))
                .count(),
            ..Status::default()
        };
        if let Some(generation) = &self.generation {
            status.generation = generation.id;
            status.carrier_hash = generation.hash;
            status.carrier_ms = generation.compile_ms;
            status.layers = generation.layers.clone();
            status.waiting_for_present = !table::is_applied(generation.ticket);
        }
        let mut phases: Vec<Phase> = Vec::new();
        let mut details: Vec<(u64, GroupDetail)> = Vec::new();
        for (at, group) in registry.groups.iter().enumerate() {
            let at = at as u32;
            let activity = self.activity.get(&at).copied().unwrap_or_default();
            if activity.first_bound_ms.is_none() {
                continue;
            }
            status.groups_wanted += 1;
            status.pipelines_wanted += group.members.len();
            let build = self.builds.get(&at);
            let phase = build.map_or(Phase::Pending, GroupBuild::phase);
            if phase == Phase::Live {
                status.pipelines_live += group.members.len();
            }
            let passes = group
                .members
                .iter()
                .filter_map(|member| registry.pipelines.get(member))
                .fold(0, |bits, pipeline| bits | pipeline.pass.bit());
            details.push((
                if activity.recent(now) {
                    activity.delta
                } else {
                    0
                },
                GroupDetail {
                    key: group.key.clone(),
                    phase: phase.word().to_owned(),
                    pipelines: group.members.len(),
                    passes,
                    binds_recent: activity.delta,
                    splice_ms: build.map_or(0.0, |b| b.splice_ms),
                    create_ms_max: build.map_or(0.0, |b| b.create_ms_max),
                    why: match &phase {
                        Phase::Refused(why) => why.clone(),
                        _ => String::new(),
                    },
                },
            ));
            phases.push(phase);
        }
        status.phases = plan::phase_counts(&phases);
        details.sort_by_key(|(binds, _)| core::cmp::Reverse(*binds));
        status.details = details
            .into_iter()
            .take(DETAILED_GROUPS)
            .map(|(_, detail)| detail)
            .collect();
        status
    }

    fn log_line(&self, what: &str) {
        let status = status();
        let splice = plan::percentile(&status.splice_ms, 50.0).unwrap_or(0.0);
        let create50 = plan::percentile(&status.create_ms, 50.0).unwrap_or(0.0);
        let create95 = plan::percentile(&status.create_ms, 95.0).unwrap_or(0.0);
        let refused: u64 = status
            .refusals
            .iter()
            .filter(|(class, _)| class.as_str() != "previous_skipped")
            .map(|(_, count)| *count)
            .sum();
        log(format_args!(
            "vertex: build {} carrier={:.1}ms groups={}/{} vs={} psos={}/{} refused={refused} \
             refusals={:?} splice_ms_p50={splice:.2} create_ms_p50={create50:.2} \
             create_ms_p95={create95:.2} recorded={} {what}",
            status.generation,
            status.carrier_ms,
            status.phases.get("live").copied().unwrap_or(0),
            status.groups_wanted,
            status.vs_patched,
            status.pipelines_live,
            status.pipelines_wanted,
            status.refusals,
            status.recorded,
        ));
    }
}

/// Create one variant: rule 9 and the caller's half of rule 7 on the stream it is made from, then
/// `CreatePipelineState` through the trampoline.
fn create(pipeline: &MeshPipeline, blob: &[u8]) -> Result<usize, (&'static str, String)> {
    let record = &pipeline.record;
    let stream = record.with_vs(blob).ok_or((
        "rule9",
        "the record has no vertex shader subobject".to_owned(),
    ))?;
    let at = record.vs_offset().ok_or((
        "rule9",
        "the record has no vertex shader subobject".to_owned(),
    ))?;
    let failures = rules::check_stream(
        record.stream(),
        stream.bytes(),
        at..at + size_of::<ShaderBytecode>(),
        record.stream_output_entries(),
        record.view_instance_count(),
        record.root_signature(),
        stream_root(record, stream.bytes()),
    );
    if let Some(first) = failures.first() {
        return Err((
            if first.rule == rules::VertexRule::Stream {
                "rule9"
            } else {
                "rule7"
            },
            first.to_string(),
        ));
    }
    let device = device_of(record.root_signature()).map_err(|why| ("device", why))?;
    // SAFETY: the device the record's root signature was created on, and a stream whose pointers
    // aim at the record and at `blob`, both alive for the call.
    unsafe { hooks::create_from_stream(device.as_raw() as usize, &stream) }.map_err(|hr| {
        (
            "create",
            format!("CreatePipelineState refused it: hr=0x{:08x}", hr.0 as u32),
        )
    })
}

/// Proof frames read since the cel worker last wrote them.
static PROOF: Mutex<VecDeque<ProofFrame>> = Mutex::new(VecDeque::new());
/// The proof mode the file was last started for, so a new proof starts a new file.
static PROOF_FILE_MODE: Mutex<u32> = Mutex::new(0);

/// The present's share: while the proof mode is on, keep this present's copy of the vertex proof
/// region. Never waits for the cel worker: a frame it cannot store for the lock is dropped.
pub fn on_present(present: u64) {
    cmdlist::r1_present();
    let mode = params::vertex_proof();
    if mode == 0 {
        return;
    }
    let Some(bytes) = probe::read_slot(present).and_then(probe::read_slot_bytes) else {
        return;
    };
    let samples = probe::decode_vertex(&bytes);
    let Some((frame, samples)) = ProofFrame::majority(&samples) else {
        return;
    };
    let generation = status_generation();
    let frame = ProofFrame {
        present,
        frame,
        time: params::time_of(u64::from(frame)),
        vertex_on: cmdlist::STAGE_ACTIVE.load(Ordering::Relaxed) & VERTEX_STAGE_BIT != 0
            && !HOLD_OFF.load(Ordering::Relaxed),
        passes: cmdlist::VERTEX_PASSES.load(Ordering::Relaxed) & Pass::ALL_BITS,
        generation,
        samples,
    };
    if let Ok(mut frames) = PROOF.try_lock() {
        if frames.len() >= PROOF_FRAMES_KEPT {
            frames.pop_front();
        }
        frames.push_back(frame);
    }
}

/// The current carrier's generation, as the last status has it, without waiting for its lock.
fn status_generation() -> u64 {
    STATUS.try_lock().map_or(0, |status| status.generation)
}

/// `er-cel-shade-vertex-proof.jsonl`, beside the telemetry file.
fn proof_path() -> PathBuf {
    let telemetry = redirected_artifact_path(TELEMETRY_PATH_ENV, TELEMETRY_FILE_NAME);
    telemetry.with_file_name(PROOF_FILE_NAME)
}

/// The cel worker's share: append the frames read since the last call, one line each. A proof
/// mode switched on anew starts the file over.
pub fn flush_proof() {
    let frames: Vec<ProofFrame> = PROOF
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .drain(..)
        .collect();
    let mode = params::vertex_proof();
    let mut started = PROOF_FILE_MODE
        .lock()
        .unwrap_or_else(PoisonError::into_inner);
    let path = proof_path();
    let fresh = mode != 0 && *started == 0;
    *started = mode;
    if frames.is_empty() && !fresh {
        return;
    }
    let opened = if fresh {
        // The previous run's file is set aside first, so a later append in this process cannot
        // set aside the lines written here.
        er_game_base::log::begin_fresh_run(&path);
        std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&path)
            .ok()
    } else {
        er_game_base::log::open_fresh_run_append(&path)
    };
    let Some(mut file) = opened else {
        return;
    };
    if file.metadata().map_or(0, |m| m.len()) > PROOF_FILE_LIMIT {
        return;
    }
    let mut text = String::new();
    for frame in &frames {
        text.push_str(&frame.to_json());
        text.push('\n');
    }
    let _ = file.write_all(text.as_bytes());
}

/// `er_shader_editor_set_vertex_passes_v1`: redirect vertex entries only in the passes `mask`
/// holds, one `Pass::bit` each, and return the mask before. For oracle S; release behaviour never
/// calls it.
#[unsafe(no_mangle)]
pub extern "C" fn er_shader_editor_set_vertex_passes_v1(mask: u32) -> u32 {
    let before = cmdlist::VERTEX_PASSES.swap(mask | !Pass::ALL_BITS, Ordering::AcqRel);
    log(format_args!(
        "vertex: passes {mask:#06x} through the export (was {:#06x})",
        before & Pass::ALL_BITS
    ));
    before & Pass::ALL_BITS
}

/// `er_shader_editor_set_vertex_proof_v1`: set the vertex proof mode the next parameter block
/// carries, 0 off, and return the mode before.
#[unsafe(no_mangle)]
pub extern "C" fn er_shader_editor_set_vertex_proof_v1(mode: u32) -> u32 {
    let before = params::set_vertex_proof(mode);
    crate::menu::restage();
    log(format_args!(
        "vertex: proof mode {mode} through the export (was {before})"
    ));
    before
}

fn ms_summary(out: &mut String, name: &str, values: &[f32]) {
    let at = |p: f32| plan::percentile(values, p).unwrap_or(0.0);
    let max = values.iter().copied().fold(0.0_f32, f32::max);
    let _ = write!(
        out,
        "\"{name}\": {{\"n\": {}, \"p50\": {:.3}, \"p95\": {:.3}, \"max\": {max:.3}}}",
        values.len(),
        at(50.0),
        at(95.0)
    );
}

/// The telemetry's `vertex` and `r1` members, each followed by a comma and a newline.
#[must_use]
pub fn telemetry_json() -> String {
    let status = status();
    let mut out = String::with_capacity(4096);
    out.push_str("  \"vertex\": {\n");
    let _ = writeln!(
        out,
        "    \"generation\": {}, \"carrier_hash\": \"{:016x}\", \"carrier_ms\": {:.3},",
        status.generation, status.carrier_hash, status.carrier_ms
    );
    let layers: Vec<String> = status.layers.iter().map(u32::to_string).collect();
    let _ = writeln!(
        out,
        "    \"layers\": [{}], \"waiting_for_present\": {},",
        layers.join(", "),
        status.waiting_for_present
    );
    let stage_on = cmdlist::STAGE_ACTIVE.load(Ordering::Relaxed) & VERTEX_STAGE_BIT != 0;
    let _ = writeln!(
        out,
        "    \"stage_on\": {stage_on}, \"hold_off\": {}, \"proof_mode\": {}, \"passes\": {},",
        HOLD_OFF.load(Ordering::Relaxed),
        params::vertex_proof(),
        cmdlist::VERTEX_PASSES.load(Ordering::Relaxed) & Pass::ALL_BITS
    );
    let paths: Vec<String> = Path::ALL
        .iter()
        .map(|path| {
            format!(
                "\"{}\": [{}, {}]",
                path.name(),
                SEEN[*path as usize].load(Ordering::Relaxed),
                RECORDED[*path as usize].load(Ordering::Relaxed)
            )
        })
        .collect();
    let _ = writeln!(
        out,
        "    \"paths_seen_recorded\": {{{}}}, \"not_recorded\": {}, \"hook_panics\": {},",
        paths.join(", "),
        NOT_RECORDED.load(Ordering::Relaxed),
        PANICS.load(Ordering::Relaxed)
    );
    let _ = writeln!(
        out,
        "    \"recorded\": {}, \"groups_total\": {}, \"groups_wanted\": {}, \
         \"pipelines_wanted\": {}, \"pipelines_live\": {},",
        status.recorded,
        status.groups_total,
        status.groups_wanted,
        status.pipelines_wanted,
        status.pipelines_live
    );
    let phases: Vec<String> = status
        .phases
        .iter()
        .map(|(word, count)| format!("\"{word}\": {count}"))
        .collect();
    let _ = writeln!(out, "    \"groups\": {{{}}},", phases.join(", "));
    let refusals: Vec<String> = status
        .refusals
        .iter()
        .map(|(class, count)| format!("\"{class}\": {count}"))
        .collect();
    let _ = writeln!(
        out,
        "    \"vs_patched\": {}, \"vs_refused\": {}, \"refusals\": {{{}}},",
        status.vs_patched,
        status.vs_refused,
        refusals.join(", ")
    );
    out.push_str("    ");
    ms_summary(&mut out, "splice_ms", &status.splice_ms);
    out.push_str(",\n    ");
    ms_summary(&mut out, "create_ms", &status.create_ms);
    out.push_str(",\n");
    let _ = writeln!(
        out,
        "    \"suspensions\": {}, \"reaped\": {},",
        SUSPENSIONS.load(Ordering::Relaxed),
        REAPED.load(Ordering::Relaxed)
    );
    let binds: Vec<String> = Pass::ALL
        .iter()
        .map(|pass| {
            format!(
                "\"{}\": [{}, {}]",
                pass.token(),
                cmdlist::VERTEX_BINDS[*pass as usize].load(Ordering::Relaxed),
                cmdlist::VERTEX_REDIRECTS[*pass as usize].load(Ordering::Relaxed)
            )
        })
        .collect();
    let _ = writeln!(out, "    \"binds_redirects\": {{{}}},", binds.join(", "));
    out.push_str("    \"details\": [");
    for (at, detail) in status.details.iter().enumerate() {
        out.push_str(if at == 0 { "\n" } else { ",\n" });
        let _ = write!(
            out,
            "      {{\"group\": \"{}\", \"phase\": \"{}\", \"pipelines\": {}, \"passes\": {}, \
             \"binds\": {}, \"splice_ms\": {:.3}, \"create_ms_max\": {:.3}, \"why\": \"{}\"}}",
            json_text(&detail.key),
            detail.phase,
            detail.pipelines,
            detail.passes,
            detail.binds_recent,
            detail.splice_ms,
            detail.create_ms_max,
            json_text(&detail.why)
        );
    }
    out.push_str("\n    ]\n  },\n");
    let _ = writeln!(
        out,
        "  \"r1\": {{\"lists\": {}, \"lists_straddling\": {}, \"frames\": {}, \
         \"frames_mixed\": {}, \"max_span\": {}}},",
        cmdlist::R1_LISTS.load(Ordering::Relaxed),
        cmdlist::R1_LISTS_STRADDLING.load(Ordering::Relaxed),
        cmdlist::R1_FRAMES.load(Ordering::Relaxed),
        cmdlist::R1_FRAMES_MIXED.load(Ordering::Relaxed),
        cmdlist::R1_MAX_SPAN.load(Ordering::Relaxed)
    );
    out
}

/// `text` as the inside of a JSON string: quotes, backslashes and control characters escaped.
fn json_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out
}
