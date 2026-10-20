//! Each port's GPU time (design 12): how long the GPU spends on a port's pass in a frame, so adding
//! a layer shows what it costs and a layer-on, layer-off pair gives the layer's own cost.
//!
//! # What is measured
//!
//! Whenever a port's pipeline is bound on a direct command list -- its cel variant, or the game's
//! own pipeline while the port's stage has no content -- the `SetPipelineState` detour writes a
//! timestamp into the list (`EndQuery` of a `D3D12_QUERY_TYPE_TIMESTAMP` query), and at the list's
//! next `SetPipelineState`, `ClearState` or `Close` another ([`crate::cmdlist`]). The two are a
//! span. At `Close` the list resolves its spans' queries into this present's slot of a readback
//! ring, and once a present the render thread reads the slot written [`READ_LAG_PRESENTS`]
//! presents earlier ([`Reader`]). A frame's figure for a port kind is the sum of its spans whose
//! list first bound a port during that present: a variant bound several times on a list, and on
//! several lists, adds up.
//!
//! # What the number means
//!
//! The GPU writes a timestamp once the work recorded before it has finished. A span is therefore
//! the time from the last work before the bind finishing to the last draw with the port's pipeline
//! finishing: the pass's draws, its share of the editor's probe barrier, and nothing of the work
//! before it, which overlap credits to that earlier work. It is GPU time on one queue, not frame
//! time: a pass that overlaps other work on the GPU costs the frame less than its span.
//!
//! # Limits
//!
//! * A timestamp means something only beside another from the same queue. A span begins and ends
//!   in one command list, so on one queue, and only spans are ever added.
//! * Ticks become microseconds with `GetTimestampFrequency` of the game's direct queue, read in
//!   the `CreateCommandQueue` detour when the queue is created on the device the editor's buffers
//!   live on ([`Source::GameQueue`]). Without one -- a device whose queues were made before the
//!   detour, or through `ID3D12Device9::CreateCommandQueue1` -- it is read off a direct queue the
//!   DLL creates on the same device for the purpose and releases at once ([`Source::OwnQueue`]):
//!   D3D12 states the frequency per queue, and queues of one type on one node run on the same
//!   engine, so that is the game's frequency as well. Every direct queue's frequency is logged.
//! * Only direct lists take timestamps among those a graphics pipeline is bound on. A variant is
//!   only ever bound on one, and the game's own port pipeline is timed only where `GetType` says
//!   so; nothing else is timed.
//! * There is no fence. A span whose timestamps are not newer than the newest one its slot held a
//!   ring earlier has not been written yet and is counted late, never read; a list closed on
//!   another thread than it bound on is never resolved and is counted late the same way. A
//!   resolve that lands more than a ring late can still be read as the slot's next frame; a
//!   nonzero late count is the sign to look for.
//! * A bind that finds no list slot ([`crate::cmdlist`]'s misses) is neither redirected nor timed,
//!   and a list's binds past its chunk's [`CHUNK_SPANS`] are counted dropped.
//! * The present a list's first timed bind fell in names its frame. A pass recorded across a
//!   present boundary goes to the earlier one, so a frame can hold two of a port's passes and the
//!   next none; the mean over [`HISTORY_FRAMES`] frames is the steady figure.
//! * In Vanilla mode nothing is timed: the detours return before the table lookup, which is what
//!   Vanilla means. A stage with no content binds the game's own port pipeline, and that is timed,
//!   so the cost of a stage's last layer is its figure with the layer on less its figure with it
//!   off.
//! * The queries cost something of their own, unmeasured: under vkd3d-proton a timestamp is a
//!   Vulkan `vkCmdWriteTimestamp` and a resolve a query-pool copy.
//!
//! # The ring
//!
//! Eight slots, one per present as the probe's readback ring has ([`crate::probe`]). Each holds
//! [`SLOT_CHUNKS`] chunks of [`CHUNK_SPANS`] spans; a list takes a chunk at its first timed bind
//! ([`claim`]) and resolves the chunk's used queries at its `Close`. Query `q` resolves to byte
//! `8 q` of the readback buffer, and a span's queries are `q` (its begin) and `q + 1` (its end).
//! A tag byte per span, written at the begin, says which port kind and which pipeline it timed.
//!
//! The layout, the claim, the tags and the reader's arithmetic are pure and tested on the host. The
//! Windows half owns the query heap and the readback buffer and reads them once a present.

use std::sync::atomic::{AtomicU64, Ordering};

use crate::probe::READBACK_SLOTS;
use crate::replace::{PortKind, Stage};

/// Slots in the ring: one per present, as many as the probe's readback ring.
pub const SLOTS: usize = READBACK_SLOTS;

/// How many presents the CPU reads behind the present a slot was written for. The probe reads
/// three behind and checks each record's frame; a timestamp carries no frame, frames in flight are
/// unmeasured (design 16), and with three frames queued the lists of three presents ago can still
/// be on the GPU, so this reads five behind and still leaves three presents before the slot is
/// written again. A lag that proves short shows as late spans in the telemetry.
pub const READ_LAG_PRESENTS: u64 = 5;

/// Spans in one chunk, and its queries: a begin and an end each.
pub const CHUNK_SPANS: usize = 16;
pub const CHUNK_QUERIES: usize = 2 * CHUNK_SPANS;

/// Chunks one slot hands out a present: as many lists as that may time a port in one present.
pub const SLOT_CHUNKS: usize = 32;

/// Queries per slot, in the heap, and spans in the whole ring.
pub const SLOT_QUERIES: usize = SLOT_CHUNKS * CHUNK_QUERIES;
pub const QUERIES: usize = SLOTS * SLOT_QUERIES;
pub const SPANS: usize = QUERIES / 2;

/// Bytes of one resolved timestamp, and of the readback buffer.
pub const TIMESTAMP_BYTES: usize = 8;
pub const READBACK_BYTES: usize = QUERIES * TIMESTAMP_BYTES;

// A slot's chunk count fits the low byte of its state, a list's span count a `u8`, every query a
// `u32`, and the slot read is neither the one being written nor one about to be.
const _: () = assert!(SLOT_CHUNKS < 0x100);
const _: () = assert!(CHUNK_SPANS <= u8::MAX as usize);
const _: () = assert!(QUERIES < u32::MAX as usize);
const _: () = assert!(READ_LAG_PRESENTS >= 3 && READ_LAG_PRESENTS < SLOTS as u64);

/// Design section 12's budget for one port's GPU time a frame, in microseconds: above it the
/// Diagnostics tab draws the figure amber, and `check-shader-editor-probe.py --expect gpu-cost`
/// fails it.
pub const PORT_BUDGET_US: f64 = 1_500.0;

/// Frames the mean is taken over.
pub const HISTORY_FRAMES: usize = 32;

/// A span longer than this many seconds is no pass's time: torn or unwritten memory.
pub const MAX_SPAN_SECONDS: u64 = 1;

/// The slot a present's chunks and readback bytes are in.
#[must_use]
pub const fn slot_of(present: u64) -> usize {
    (present % SLOTS as u64) as usize
}

/// The present whose slot the CPU reads during present `present`, or none that early.
#[must_use]
pub fn read_stamp(present: u64) -> Option<u64> {
    present.checked_sub(READ_LAG_PRESENTS)
}

/// The first query of chunk `chunk` of slot `slot`.
#[must_use]
pub const fn chunk_base(slot: usize, chunk: usize) -> u32 {
    (slot * SLOT_QUERIES + chunk * CHUNK_QUERIES) as u32
}

/// A slot's state: the present it counts chunks for, and how many it has handed out.
#[must_use]
pub const fn pack(stamp: u64, chunks: usize) -> u64 {
    (stamp << 8) | chunks as u64
}

/// The chunks a slot whose state is `state` handed out during present `stamp`: 0 when it counts
/// for another present.
#[must_use]
pub const fn chunks_for(state: u64, stamp: u64) -> usize {
    if state >> 8 == stamp {
        (state & 0xff) as usize
    } else {
        0
    }
}

/// A chunk for a list during present `present`, from a slot whose state is `state`: the state to
/// store and the chunk's number in the slot. The count starts over in a present the slot has not
/// counted; `None` when every chunk of the present is out.
#[must_use]
pub const fn claim(state: u64, present: u64) -> Option<(u64, usize)> {
    let used = chunks_for(state, present);
    if used < SLOT_CHUNKS {
        Some((pack(present, used + 1), used))
    } else {
        None
    }
}

/// A span's tag: present, whether the cel variant was bound, and the port kind.
const TAG_PRESENT: u8 = 0x80;
const TAG_VARIANT: u8 = 0x40;
const TAG_KIND: u8 = 0x3f;

/// The tag of a span timing `kind` with its cel variant bound (`variant`) or the game's own
/// pipeline. Never 0, which marks a span not begun.
#[must_use]
pub const fn tag(kind: PortKind, variant: bool) -> u8 {
    TAG_PRESENT | if variant { TAG_VARIANT } else { 0 } | kind as u8
}

/// What a tag names: the port kind and whether its variant was bound. `None` for 0.
#[must_use]
pub const fn untag(tag: u8) -> Option<(PortKind, bool)> {
    if tag & TAG_PRESENT == 0 {
        return None;
    }
    match PortKind::from_u8(tag & TAG_KIND) {
        Some(kind) => Some((kind, tag & TAG_VARIANT != 0)),
        None => None,
    }
}

/// Spans begun, spans a port bind could not time (no chunk left in the present, or the list's
/// chunk full), chunks handed out and lists that resolved theirs, over the session. Bumped off the
/// common path: a few times a frame.
pub static SPANS_BEGUN: AtomicU64 = AtomicU64::new(0);
pub static SPANS_DROPPED: AtomicU64 = AtomicU64::new(0);
pub static CHUNKS_CLAIMED: AtomicU64 = AtomicU64::new(0);
pub static RESOLVES: AtomicU64 = AtomicU64::new(0);

/// A span begun: one more for [`SPANS_BEGUN`].
pub fn count_begun() {
    SPANS_BEGUN.fetch_add(1, Ordering::Relaxed);
}

/// A port bind that could not be timed.
pub fn count_dropped() {
    SPANS_DROPPED.fetch_add(1, Ordering::Relaxed);
}

/// A chunk's queries resolved at a `Close`.
pub fn count_resolve() {
    RESOLVES.fetch_add(1, Ordering::Relaxed);
}

/// The session's counts, as the telemetry writes them.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counters {
    pub begun: u64,
    pub dropped: u64,
    pub chunks: u64,
    pub resolves: u64,
}

impl Counters {
    #[must_use]
    pub fn read() -> Self {
        let read = |counter: &AtomicU64| counter.load(Ordering::Relaxed);
        Self {
            begun: read(&SPANS_BEGUN),
            dropped: read(&SPANS_DROPPED),
            chunks: read(&CHUNKS_CLAIMED),
            resolves: read(&RESOLVES),
        }
    }
}

/// Where the tick rate came from.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(u8)]
pub enum Source {
    /// Not known yet: ticks are kept and nothing is converted.
    #[default]
    Unknown = 0,
    /// `GetTimestampFrequency` of the game's direct queue, read when the game created it.
    GameQueue = 1,
    /// `GetTimestampFrequency` of a direct queue the DLL created on the game's device.
    OwnQueue = 2,
}

impl Source {
    #[must_use]
    pub const fn from_u8(value: u8) -> Self {
        match value {
            1 => Self::GameQueue,
            2 => Self::OwnQueue,
            _ => Self::Unknown,
        }
    }

    /// Its name in the telemetry file.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::GameQueue => "game_direct_queue",
            Self::OwnQueue => "own_direct_queue",
        }
    }
}

/// One span as the readback holds it: its tag and its two timestamps.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ReadSpan {
    pub tag: u8,
    pub begin: u64,
    pub end: u64,
}

/// Which pipelines a port kind's spans in a frame timed.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Bound {
    #[default]
    None,
    Variant,
    Game,
    Both,
}

impl Bound {
    /// Its name in the telemetry file.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Variant => "variant",
            Self::Game => "game",
            Self::Both => "both",
        }
    }
}

/// One frame's spans, added up per port kind (`PortKind as usize`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Frame {
    /// The present the frame's lists first bound a port in.
    pub stamp: u64,
    /// Ticks with the cel variant bound, and with the game's own pipeline bound.
    pub variant_ticks: [u64; PortKind::COUNT],
    pub game_ticks: [u64; PortKind::COUNT],
    /// Spans read with the cel variant bound, and with the game's own pipeline bound.
    pub variant_spans: [u32; PortKind::COUNT],
    pub game_spans: [u32; PortKind::COUNT],
    /// Spans begun whose timestamps were not written yet, or never will be.
    pub late: u32,
    /// Spans whose timestamps make no duration: the end before the begin, or longer than
    /// [`MAX_SPAN_SECONDS`].
    pub refused: u32,
    /// The newest end read, fresh or not: what this slot's next pass compares against.
    pub newest: u64,
}

impl Frame {
    /// Every tick of `kind`, whichever pipeline it was on.
    #[must_use]
    pub fn ticks(&self, kind: PortKind) -> u64 {
        let at = kind as usize;
        self.variant_ticks[at] + self.game_ticks[at]
    }

    /// `kind`'s spans read, whichever pipeline they timed.
    #[must_use]
    pub fn spans(&self, kind: PortKind) -> u32 {
        let at = kind as usize;
        self.variant_spans[at] + self.game_spans[at]
    }

    /// Which pipelines `kind`'s spans timed.
    #[must_use]
    pub fn bound(&self, kind: PortKind) -> Bound {
        let at = kind as usize;
        match (self.variant_spans[at] > 0, self.game_spans[at] > 0) {
            (false, false) => Bound::None,
            (true, false) => Bound::Variant,
            (false, true) => Bound::Game,
            (true, true) => Bound::Both,
        }
    }

    /// Does the frame say anything: a span read, or none begun at all. A frame whose every span was
    /// late or refused is left out of the figures rather than read as nothing drawn.
    #[must_use]
    pub fn known(&self) -> bool {
        PortKind::ALL.into_iter().any(|kind| self.spans(kind) > 0)
            || self.late == 0 && self.refused == 0
    }
}

/// Add up one frame's spans. `written_before` is the newest end its slot held a ring earlier: a
/// span not newer than that has not been written for this frame. `max_ticks` is the longest span
/// accepted.
#[must_use]
pub fn frame(
    stamp: u64,
    spans: impl IntoIterator<Item = ReadSpan>,
    written_before: u64,
    max_ticks: u64,
) -> Frame {
    let mut frame = Frame {
        stamp,
        newest: written_before,
        ..Frame::default()
    };
    for span in spans {
        let Some((kind, variant)) = untag(span.tag) else {
            continue;
        };
        frame.newest = frame.newest.max(span.end);
        if span.begin <= written_before {
            frame.late += 1;
            continue;
        }
        let Some(ticks) = span.end.checked_sub(span.begin).filter(|t| *t <= max_ticks) else {
            frame.refused += 1;
            continue;
        };
        let at = kind as usize;
        if variant {
            frame.variant_ticks[at] += ticks;
            frame.variant_spans[at] += 1;
        } else {
            frame.game_ticks[at] += ticks;
            frame.game_spans[at] += 1;
        }
    }
    frame
}

/// Ticks as microseconds at `frequency` ticks a second; `None` while the rate is unknown.
#[must_use]
pub fn micros(ticks: f64, frequency: u64) -> Option<f64> {
    (frequency != 0).then(|| ticks * 1.0e6 / frequency as f64)
}

/// The last [`HISTORY_FRAMES`] known frames' ticks per port kind.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct History {
    ticks: [[u64; PortKind::COUNT]; HISTORY_FRAMES],
    len: usize,
    next: usize,
}

impl Default for History {
    fn default() -> Self {
        Self::new()
    }
}

impl History {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            ticks: [[0; PortKind::COUNT]; HISTORY_FRAMES],
            len: 0,
            next: 0,
        }
    }

    /// Remember one frame, forgetting the oldest once full.
    pub fn push(&mut self, frame: &Frame) {
        if let Some(slot) = self.ticks.get_mut(self.next) {
            *slot = PortKind::ALL.map(|kind| frame.ticks(kind));
        }
        self.next = (self.next + 1) % HISTORY_FRAMES;
        self.len = (self.len + 1).min(HISTORY_FRAMES);
    }

    /// Frames remembered.
    #[must_use]
    pub const fn len(&self) -> usize {
        self.len
    }

    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// The mean ticks a frame per kind, or `None` before a frame.
    #[must_use]
    pub fn mean(&self) -> Option<[f64; PortKind::COUNT]> {
        if self.len == 0 {
            return None;
        }
        let mut sums = [0.0; PortKind::COUNT];
        for frame in self.ticks.iter().take(self.len) {
            for (sum, ticks) in sums.iter_mut().zip(frame) {
                *sum += *ticks as f64;
            }
        }
        Some(sums.map(|sum| sum / self.len as f64))
    }
}

/// What the Diagnostics tab and the telemetry show: the last known frame, the mean over the
/// history, the rate, and what was lost on the way.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Report {
    /// Ticks a second; 0 while unknown.
    pub frequency: u64,
    pub source: Source,
    /// The last known frame, `None` before one.
    pub last: Option<Frame>,
    /// Mean ticks a frame per kind over the last [`History::len`] known frames.
    pub mean_ticks: Option<[f64; PortKind::COUNT]>,
    pub history: usize,
    /// Known frames read over the session, and frames read whose every span was late or refused.
    pub frames: u64,
    pub unknown_frames: u64,
    /// Spans counted late and refused over the session.
    pub late: u64,
    pub refused: u64,
}

impl Report {
    /// `kind`'s microseconds in the last known frame.
    #[must_use]
    pub fn last_us(&self, kind: PortKind) -> Option<f64> {
        micros(self.last?.ticks(kind) as f64, self.frequency)
    }

    /// `kind`'s mean microseconds a frame.
    #[must_use]
    pub fn mean_us(&self, kind: PortKind) -> Option<f64> {
        micros(self.mean_ticks?[kind as usize], self.frequency)
    }

    /// `kind`'s spans in the last known frame.
    #[must_use]
    pub fn spans_last(&self, kind: PortKind) -> u32 {
        self.last.map_or(0, |frame| frame.spans(kind))
    }

    /// Which pipelines `kind`'s spans timed in the last known frame.
    #[must_use]
    pub fn bound_last(&self, kind: PortKind) -> Bound {
        self.last.map_or(Bound::None, |frame| frame.bound(kind))
    }

    fn stage_sum(stage: Stage, of: impl Fn(PortKind) -> Option<f64>) -> Option<f64> {
        PortKind::ALL
            .into_iter()
            .filter(|kind| kind.stage() == stage)
            .map(of)
            .sum()
    }

    /// A stage's microseconds in the last known frame: every port kind of the stage together.
    #[must_use]
    pub fn stage_last_us(&self, stage: Stage) -> Option<f64> {
        Self::stage_sum(stage, |kind| self.last_us(kind))
    }

    /// A stage's mean microseconds a frame.
    #[must_use]
    pub fn stage_mean_us(&self, stage: Stage) -> Option<f64> {
        Self::stage_sum(stage, |kind| self.mean_us(kind))
    }
}

/// The render thread's side: each slot's newest timestamp from its last pass, the history, and
/// the report it keeps.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Reader {
    newest: [u64; SLOTS],
    history: History,
    report: Report,
}

impl Reader {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            newest: [0; SLOTS],
            history: History::new(),
            report: Report {
                frequency: 0,
                source: Source::Unknown,
                last: None,
                mean_ticks: None,
                history: 0,
                frames: 0,
                unknown_frames: 0,
                late: 0,
                refused: 0,
            },
        }
    }

    /// Read the spans of the frame `stamp`, whose slot is `slot_of(stamp)`, at `frequency` ticks a
    /// second from `source`.
    pub fn read(
        &mut self,
        stamp: u64,
        spans: impl IntoIterator<Item = ReadSpan>,
        frequency: u64,
        source: Source,
    ) -> &Report {
        let slot = slot_of(stamp);
        let before = self.newest.get(slot).copied().unwrap_or(0);
        let max_ticks = if frequency == 0 {
            u64::MAX
        } else {
            frequency.saturating_mul(MAX_SPAN_SECONDS)
        };
        let read = frame(stamp, spans, before, max_ticks);
        if let Some(newest) = self.newest.get_mut(slot) {
            *newest = read.newest;
        }
        let report = &mut self.report;
        report.frequency = frequency;
        report.source = source;
        report.late += u64::from(read.late);
        report.refused += u64::from(read.refused);
        if read.known() {
            self.history.push(&read);
            report.last = Some(read);
            report.frames += 1;
        } else {
            report.unknown_frames += 1;
        }
        report.mean_ticks = self.history.mean();
        report.history = self.history.len();
        &self.report
    }

    #[must_use]
    pub const fn report(&self) -> &Report {
        &self.report
    }
}

#[cfg(windows)]
pub use process::{
    chunk, create, note_begin, note_queue, on_present, query_heap, readback_resource, report,
    running,
};

/// The query heap, the readback buffer and their reading.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, AtomicUsize, Ordering};
    use std::sync::{Mutex, PoisonError};

    use windows::Win32::Graphics::Direct3D12::{
        D3D12_COMMAND_LIST_TYPE, D3D12_COMMAND_LIST_TYPE_COMPUTE, D3D12_COMMAND_LIST_TYPE_COPY,
        D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_HEAP_TYPE_READBACK, D3D12_QUERY_HEAP_DESC,
        D3D12_QUERY_HEAP_TYPE_TIMESTAMP, D3D12_RANGE, D3D12_RESOURCE_FLAG_NONE,
        D3D12_RESOURCE_STATE_COPY_DEST, ID3D12CommandQueue, ID3D12Device, ID3D12QueryHeap,
    };
    use windows::core::{IUnknown, Interface};

    use super::{
        CHUNK_SPANS, CHUNKS_CLAIMED, QUERIES, READBACK_BYTES, ReadSpan, Reader, Report,
        SLOT_CHUNKS, SLOTS, SPANS, SPANS_BEGUN, Source, TIMESTAMP_BYTES, chunk_base, chunks_for,
        claim, read_stamp, slot_of,
    };
    use crate::look_binding::committed_buffer;
    use crate::{hooks, log};

    /// The query heap and the readback buffer, as raw pointers held for the life of the process,
    /// the readback's CPU address, and the device they were made on, by its address. 0 before
    /// [`create`].
    static HEAP: AtomicUsize = AtomicUsize::new(0);
    static READBACK: AtomicUsize = AtomicUsize::new(0);
    static READBACK_MAPPED: AtomicUsize = AtomicUsize::new(0);
    static DEVICE: AtomicUsize = AtomicUsize::new(0);
    static RUNNING: AtomicBool = AtomicBool::new(false);

    /// Each slot's state ([`super::pack`]).
    static SLOT_STATE: [AtomicU64; SLOTS] = [const { AtomicU64::new(0) }; SLOTS];

    /// Each span's tag, by its begin query over two. Cleared when its chunk is handed out.
    static TAGS: [AtomicU8; SPANS] = [const { AtomicU8::new(0) }; SPANS];

    /// The tick rate and where it came from.
    static FREQUENCY: AtomicU64 = AtomicU64::new(0);
    static SOURCE: AtomicU8 = AtomicU8::new(Source::Unknown as u8);

    /// A queue the game created, as the `CreateCommandQueue` detour saw it.
    #[derive(Clone, Copy, Debug)]
    struct Queue {
        device: usize,
        kind: D3D12_COMMAND_LIST_TYPE,
        frequency: Option<u64>,
    }

    /// Every queue seen. A handful a session.
    static QUEUES: Mutex<Vec<Queue>> = Mutex::new(Vec::new());

    /// The render thread's reader; the menu and the worker read its report.
    static READER: Mutex<Reader> = Mutex::new(Reader::new());

    /// Our own queue was tried for the rate, so it is not tried again.
    static OWN_QUEUE_TRIED: AtomicBool = AtomicBool::new(false);

    /// Create the query heap and the readback buffer on `device`, beside the probe. Called once,
    /// with the probe; a failure leaves the timer off and changes nothing else.
    ///
    /// # Safety
    ///
    /// `device` must be the live device the editor's buffers are created on.
    pub unsafe fn create(device: &ID3D12Device) -> Result<(), String> {
        let desc = D3D12_QUERY_HEAP_DESC {
            Type: D3D12_QUERY_HEAP_TYPE_TIMESTAMP,
            Count: QUERIES as u32,
            NodeMask: 0,
        };
        let mut heap: Option<ID3D12QueryHeap> = None;
        unsafe { device.CreateQueryHeap(&desc, &mut heap) }
            .map_err(|error| format!("timestamp query heap: {error}"))?;
        let heap = heap.ok_or_else(|| "timestamp query heap: none returned".to_owned())?;
        let readback = unsafe {
            committed_buffer(
                device,
                D3D12_HEAP_TYPE_READBACK,
                READBACK_BYTES as u64,
                D3D12_RESOURCE_FLAG_NONE,
                D3D12_RESOURCE_STATE_COPY_DEST,
            )
        }
        .map_err(|error| format!("timestamp readback: {error}"))?;
        let mut mapped: *mut c_void = std::ptr::null_mut();
        let everything = D3D12_RANGE {
            Begin: 0,
            End: READBACK_BYTES,
        };
        unsafe { readback.Map(0, Some(&everything), Some(&mut mapped)) }
            .map_err(|error| format!("timestamp readback: Map failed: {error}"))?;
        if mapped.is_null() {
            return Err("timestamp readback: Map returned no address".to_owned());
        }
        // Both kept for the life of the process: lists the GPU has not finished name them.
        DEVICE.store(device.as_raw() as usize, Ordering::Release);
        HEAP.store(heap.into_raw() as usize, Ordering::Release);
        READBACK_MAPPED.store(mapped as usize, Ordering::Release);
        READBACK.store(readback.into_raw() as usize, Ordering::Release);
        RUNNING.store(true, Ordering::Release);
        log(format_args!(
            "gpu time: {QUERIES} timestamp queries, readback {SLOTS} x {} bytes, mapped",
            READBACK_BYTES / SLOTS
        ));
        Ok(())
    }

    /// The timer exists: port binds are timed.
    #[must_use]
    pub fn running() -> bool {
        RUNNING.load(Ordering::Acquire)
    }

    /// The query heap as a raw `ID3D12QueryHeap*`, 0 before it exists. Borrowed.
    #[must_use]
    pub fn query_heap() -> usize {
        HEAP.load(Ordering::Acquire)
    }

    /// The readback buffer as a raw `ID3D12Resource*`, 0 before it exists. Query `q` resolves to
    /// byte `8 q`.
    #[must_use]
    pub fn readback_resource() -> usize {
        READBACK.load(Ordering::Acquire)
    }

    /// A chunk for a list during present `present`, by its first query; its spans' tags cleared.
    /// `None` without the timer or when the present's chunks are all out.
    #[must_use]
    pub fn chunk(present: u64) -> Option<u32> {
        if !running() {
            return None;
        }
        // `slot_of` is the present modulo `SLOTS`, so the index is always inside the ring.
        let slot = slot_of(present);
        let mut current = SLOT_STATE[slot].load(Ordering::Acquire);
        let number = loop {
            let (next, number) = claim(current, present)?;
            match SLOT_STATE[slot].compare_exchange_weak(
                current,
                next,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => break number,
                Err(seen) => current = seen,
            }
        };
        let base = chunk_base(slot, number);
        let first = base as usize / 2;
        for tag in TAGS.iter().skip(first).take(CHUNK_SPANS) {
            tag.store(0, Ordering::Relaxed);
        }
        CHUNKS_CLAIMED.fetch_add(1, Ordering::Relaxed);
        Some(base)
    }

    /// A span was begun at query `query` with tag `tag`.
    pub fn note_begin(query: u32, tag: u8) {
        let span = query as usize / 2;
        if span < SPANS {
            TAGS[span].store(tag, Ordering::Release);
        }
        SPANS_BEGUN.fetch_add(1, Ordering::Relaxed);
    }

    fn kind_name(kind: D3D12_COMMAND_LIST_TYPE) -> &'static str {
        match kind {
            D3D12_COMMAND_LIST_TYPE_DIRECT => "direct",
            D3D12_COMMAND_LIST_TYPE_COMPUTE => "compute",
            D3D12_COMMAND_LIST_TYPE_COPY => "copy",
            _ => "other",
        }
    }

    /// The `CreateCommandQueue` detour created `queue`, of `kind`, on `device`: keep its tick rate.
    ///
    /// # Safety
    ///
    /// `queue` must be the live queue the call returned, of any command-queue interface.
    pub unsafe fn note_queue(device: usize, kind: D3D12_COMMAND_LIST_TYPE, queue: *mut c_void) {
        let Some(unknown) = (unsafe { IUnknown::from_raw_borrowed(&queue) }) else {
            return;
        };
        let read = unknown
            .cast::<ID3D12CommandQueue>()
            .and_then(|queue| unsafe { queue.GetTimestampFrequency() });
        let frequency = read.as_ref().ok().copied();
        match &read {
            Ok(hz) => log(format_args!(
                "gpu time: {} queue on device 0x{device:x}, timestamps at {hz} Hz",
                kind_name(kind)
            )),
            Err(error) => log(format_args!(
                "gpu time: {} queue on device 0x{device:x} gives no timestamp frequency: {error}",
                kind_name(kind)
            )),
        }
        QUEUES
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(Queue {
                device,
                kind,
                frequency,
            });
    }

    /// The tick rate, settled the first time it is asked for after the timer exists: the game's
    /// first direct queue on the timer's device, else a direct queue of our own on that device.
    fn frequency() -> (u64, Source) {
        let known = FREQUENCY.load(Ordering::Acquire);
        if known != 0 {
            return (known, Source::from_u8(SOURCE.load(Ordering::Acquire)));
        }
        let device = DEVICE.load(Ordering::Acquire);
        let (found, disagree) = {
            let queues = QUEUES.lock().unwrap_or_else(PoisonError::into_inner);
            let mut rates = queues
                .iter()
                .filter(|queue| {
                    queue.device == device && queue.kind == D3D12_COMMAND_LIST_TYPE_DIRECT
                })
                .filter_map(|queue| queue.frequency);
            let first = rates.next();
            (first, rates.any(|rate| Some(rate) != first))
        };
        if let Some(rate) = found {
            if disagree {
                log(format_args!(
                    "gpu time: the game's direct queues report different timestamp rates; the \
                     first, {rate} Hz, converts every span"
                ));
            }
            return settle(rate, Source::GameQueue);
        }
        if OWN_QUEUE_TRIED.swap(true, Ordering::AcqRel) {
            return (0, Source::Unknown);
        }
        let heap = HEAP.load(Ordering::Acquire) as *mut c_void;
        // SAFETY: the heap is held for the life of the process; borrowed here to name its device.
        let owner = unsafe { ID3D12QueryHeap::from_raw_borrowed(&heap) }.and_then(|heap| {
            let mut device: Option<ID3D12Device> = None;
            unsafe { heap.GetDevice(&mut device) }.ok()?;
            device
        });
        let Some(owner) = owner else {
            log(format_args!(
                "gpu time: the query heap names no device; ticks stay ticks"
            ));
            return (0, Source::Unknown);
        };
        // SAFETY: the device the editor's buffers live on, alive while the heap is.
        match unsafe { hooks::own_queue_frequency(&owner) } {
            Ok(rate) => {
                log(format_args!(
                    "gpu time: no direct queue of the game's was seen on device 0x{device:x}; \
                     a direct queue of our own on it reads {rate} Hz, and was released"
                ));
                settle(rate, Source::OwnQueue)
            }
            Err(error) => {
                log(format_args!(
                    "gpu time: no tick rate ({error}); spans are kept in ticks and shown as -"
                ));
                (0, Source::Unknown)
            }
        }
    }

    fn settle(rate: u64, source: Source) -> (u64, Source) {
        SOURCE.store(source as u8, Ordering::Release);
        FREQUENCY.store(rate, Ordering::Release);
        (rate, source)
    }

    /// One timestamp out of the mapped readback.
    ///
    /// # Safety
    ///
    /// `mapped` must be the mapped readback and `query` inside it.
    unsafe fn timestamp(mapped: usize, query: usize) -> u64 {
        // SAFETY: the caller's contract; the GPU may write it meanwhile, which a torn value's
        // refusal or lateness covers.
        unsafe { std::ptr::read_volatile((mapped + query * TIMESTAMP_BYTES) as *const u64) }
    }

    /// Once a present, after the parameter block is published: read the spans of the frame
    /// [`super::READ_LAG_PRESENTS`] behind into the report.
    pub fn on_present(present: u64) {
        let Some(stamp) = read_stamp(present) else {
            return;
        };
        let mapped = READBACK_MAPPED.load(Ordering::Acquire);
        if !running() || mapped == 0 {
            return;
        }
        let (frequency, source) = frequency();
        let slot = slot_of(stamp);
        let chunks = SLOT_STATE
            .get(slot)
            .map_or(0, |state| chunks_for(state.load(Ordering::Acquire), stamp))
            .min(SLOT_CHUNKS);
        // Every begun span of the frame's chunks, read in place: nothing is allocated a present.
        let spans = (0..chunks).flat_map(move |chunk| {
            let base = chunk_base(slot, chunk) as usize;
            (0..CHUNK_SPANS).filter_map(move |span| {
                let begin = base + 2 * span;
                let tag = TAGS.get(begin / 2)?.load(Ordering::Acquire);
                (tag != 0).then(|| ReadSpan {
                    tag,
                    // SAFETY: both queries are inside the mapped readback.
                    begin: unsafe { timestamp(mapped, begin) },
                    end: unsafe { timestamp(mapped, begin + 1) },
                })
            })
        });
        READER
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .read(stamp, spans, frequency, source);
    }

    /// The report as the render thread last left it, or `None` while the timer is off.
    #[must_use]
    pub fn report() -> Option<Report> {
        running().then(|| {
            *READER
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .report()
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_ring_is_eight_presents_of_chunks_read_five_behind() {
        assert_eq!(SLOTS, 8);
        assert_eq!(QUERIES, 8 * 32 * 32);
        assert_eq!(READBACK_BYTES, 64 * 1024);
        assert_eq!(read_stamp(4), None);
        assert_eq!(read_stamp(5), Some(0));
        for present in 5..200 {
            let read = read_stamp(present).expect("late enough");
            // The slot read is never the one being written, nor the next three to be.
            for ahead in 0..3 {
                assert_ne!(slot_of(read), slot_of(present + ahead), "{present}+{ahead}");
            }
        }
        // Chunks tile their slot, and slots tile the heap, with nothing past its end.
        assert_eq!(chunk_base(0, 1), CHUNK_QUERIES as u32);
        assert_eq!(chunk_base(1, 0), SLOT_QUERIES as u32);
        let last = chunk_base(SLOTS - 1, SLOT_CHUNKS - 1) as usize + CHUNK_QUERIES;
        assert_eq!(last, QUERIES);
    }

    #[test]
    fn a_slot_hands_out_its_chunks_once_a_present() {
        let mut state = 0;
        for want in 0..SLOT_CHUNKS {
            let (next, got) = claim(state, 9).expect("room");
            assert_eq!(got, want);
            state = next;
        }
        assert_eq!(chunks_for(state, 9), SLOT_CHUNKS);
        assert_eq!(claim(state, 9), None);
        // The slot's next present starts the count over; another present's count reads as none.
        assert_eq!(chunks_for(state, 17), 0);
        assert_eq!(claim(state, 17), Some((pack(17, 1), 0)));
        // Present 0 against a slot never touched.
        assert_eq!(claim(0, 0), Some((pack(0, 1), 0)));
    }

    #[test]
    fn tags_name_the_kind_and_the_pipeline_and_are_never_zero() {
        for kind in PortKind::ALL {
            for variant in [false, true] {
                let byte = tag(kind, variant);
                assert_ne!(byte, 0);
                assert_eq!(untag(byte), Some((kind, variant)));
            }
        }
        assert_eq!(untag(0), None);
        assert_eq!(untag(TAG_PRESENT | 0x3f), None);
    }

    fn span(kind: PortKind, variant: bool, begin: u64, end: u64) -> ReadSpan {
        ReadSpan {
            tag: tag(kind, variant),
            begin,
            end,
        }
    }

    #[test]
    fn a_frame_sums_a_kinds_spans_across_binds_and_lists() {
        let read = frame(
            40,
            [
                // The tonemap variant bound twice on one list, and once on another.
                span(PortKind::Tonemap, true, 1_000, 1_300),
                span(PortKind::Tonemap, true, 2_000, 2_050),
                span(PortKind::Tonemap, true, 5_000, 5_150),
                // AO's own pipeline, its stage off.
                span(PortKind::Ao, false, 3_000, 3_400),
                // Not begun: no tag.
                ReadSpan {
                    tag: 0,
                    begin: 9,
                    end: 99_999,
                },
            ],
            500,
            u64::MAX,
        );
        assert_eq!(read.ticks(PortKind::Tonemap), 500);
        assert_eq!(read.spans(PortKind::Tonemap), 3);
        assert_eq!(read.bound(PortKind::Tonemap), Bound::Variant);
        assert_eq!(read.game_ticks[PortKind::Ao as usize], 400);
        assert_eq!(read.bound(PortKind::Ao), Bound::Game);
        assert_eq!(read.bound(PortKind::CmbAlbedo), Bound::None);
        assert_eq!(read.newest, 5_150);
        assert_eq!((read.late, read.refused), (0, 0));
        assert!(read.known());
    }

    #[test]
    fn spans_not_newer_than_the_slots_last_pass_are_late_and_nonsense_is_refused() {
        let read = frame(
            40,
            [
                // Still holding what the slot held a ring ago.
                span(PortKind::Ao, true, 900, 950),
                span(PortKind::Ao, true, 1_000, 1_000),
                // Fresh.
                span(PortKind::Ao, true, 2_000, 2_100),
                // An end before its begin, and one past the longest span.
                span(PortKind::CmbAlbedo, true, 3_000, 2_999),
                span(PortKind::Tonemap, true, 4_000, 4_000 + 1_001),
                // A zero-length span is a span.
                span(PortKind::TonemapLut, false, 6_000, 6_000),
            ],
            1_000,
            1_000,
        );
        assert_eq!(read.late, 2);
        assert_eq!(read.refused, 2);
        assert_eq!(read.ticks(PortKind::Ao), 100);
        assert_eq!(read.spans(PortKind::TonemapLut), 1);
        assert_eq!(read.ticks(PortKind::Tonemap), 0);
        assert_eq!(read.newest, 6_000);
        // A frame whose every span was late says nothing; one with nothing begun says "none".
        let late = frame(41, [span(PortKind::Ao, true, 5, 9)], 1_000, 1_000);
        assert!(!late.known());
        assert_eq!(late.newest, 1_000);
        let quiet = frame(42, [], 1_000, 1_000);
        assert!(quiet.known());
        assert_eq!(quiet.ticks(PortKind::Ao), 0);
    }

    #[test]
    fn a_kind_bound_both_ways_in_a_frame_says_so() {
        let read = frame(
            1,
            [
                span(PortKind::Tonemap, true, 10, 20),
                span(PortKind::Tonemap, false, 30, 45),
            ],
            0,
            u64::MAX,
        );
        assert_eq!(read.bound(PortKind::Tonemap), Bound::Both);
        assert_eq!(read.ticks(PortKind::Tonemap), 25);
        // A variant span of zero ticks is still the variant's.
        let zero = frame(1, [span(PortKind::Ao, true, 10, 10)], 0, u64::MAX);
        assert_eq!(zero.bound(PortKind::Ao), Bound::Variant);
    }

    #[test]
    fn the_reader_compares_each_slot_with_its_own_last_pass() {
        let mut reader = Reader::new();
        let hz = 1_000_000_000;
        // Frame 8 in slot 0, read fresh.
        let report = *reader.read(
            8,
            [span(PortKind::Tonemap, true, 100, 400_100)],
            hz,
            Source::GameQueue,
        );
        assert_eq!(report.last_us(PortKind::Tonemap), Some(400.0));
        assert_eq!(report.mean_us(PortKind::Tonemap), Some(400.0));
        // Slot 0 a ring later, still holding frame 8's timestamps: late, and the figures stand.
        let report = *reader.read(
            16,
            [span(PortKind::Tonemap, true, 100, 400_100)],
            hz,
            Source::GameQueue,
        );
        assert_eq!(report.late, 1);
        assert_eq!(report.unknown_frames, 1);
        assert_eq!(report.last.map(|frame| frame.stamp), Some(8));
        // Another slot is compared with its own pass, not slot 0's.
        let report = *reader.read(
            9,
            [span(PortKind::Tonemap, true, 50, 200_050)],
            hz,
            Source::GameQueue,
        );
        assert_eq!(report.last_us(PortKind::Tonemap), Some(200.0));
        assert_eq!(report.mean_us(PortKind::Tonemap), Some(300.0));
        assert_eq!(report.history, 2);
        assert_eq!(report.frames, 2);
        // A frame with no port drawn is a frame of nothing, and pulls the mean down.
        let report = *reader.read(10, [], hz, Source::GameQueue);
        assert_eq!(report.last_us(PortKind::Tonemap), Some(0.0));
        assert_eq!(report.mean_us(PortKind::Tonemap), Some(200.0));
        assert_eq!(report.stage_mean_us(Stage::Tonemap), Some(200.0));
        assert_eq!(report.stage_mean_us(Stage::Lighting), Some(0.0));
        assert_eq!(report.source, Source::GameQueue);
    }

    #[test]
    fn without_a_rate_ticks_are_kept_and_nothing_is_converted() {
        let mut reader = Reader::new();
        let report = *reader.read(
            3,
            [span(PortKind::Ao, true, 10, u64::MAX / 2)],
            0,
            Source::Unknown,
        );
        assert_eq!(
            report.last.map(|frame| frame.ticks(PortKind::Ao)),
            Some(u64::MAX / 2 - 10)
        );
        assert_eq!(report.last_us(PortKind::Ao), None);
        assert_eq!(report.mean_us(PortKind::Ao), None);
        assert_eq!(report.stage_last_us(Stage::Lighting), None);
        assert_eq!(micros(1_500.0, 1_000_000), Some(1_500.0));
        assert_eq!(micros(1.0, 0), None);
    }

    #[test]
    fn the_history_keeps_the_last_frames_only() {
        let mut history = History::new();
        assert!(history.is_empty() && history.mean().is_none());
        for at in 0..(HISTORY_FRAMES as u64 + 8) {
            let mut frame = Frame::default();
            frame.variant_ticks[PortKind::Ao as usize] = if at < 8 { 1_000 } else { 10 };
            history.push(&frame);
        }
        assert_eq!(history.len(), HISTORY_FRAMES);
        assert_eq!(
            history.mean().map(|mean| mean[PortKind::Ao as usize]),
            Some(10.0)
        );
    }

    #[test]
    fn source_names_round_trip() {
        for source in [Source::Unknown, Source::GameQueue, Source::OwnQueue] {
            assert_eq!(Source::from_u8(source as u8), source);
        }
        assert_eq!(Source::from_u8(9), Source::Unknown);
        assert_eq!(Source::GameQueue.name(), "game_direct_queue");
        assert_eq!(Bound::Both.name(), "both");
    }
}
