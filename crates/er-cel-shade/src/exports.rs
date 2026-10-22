//! The diagnostic exports Frida calls (design 11.1), and the telemetry file the worker writes once
//! a second.
//!
//! # Exports
//!
//! * `er_shader_editor_stats_v1(out: *mut StatsV1) -> u32` fills [`StatsV1`] and returns its size
//!   in bytes, or 0 for a null pointer. The layout is fixed: [`StatsV1::LAYOUT`] lists every field
//!   with its offset, and a test holds the struct to it, so a reader written against one build
//!   reads the next.
//! * `er_shader_editor_set_mode_v1(mode: u32) -> u32` switches to the [`ModeSetting`] `mode`
//!   encodes -- 0 normal, 1 vanilla, 2 fidelity, with the port kinds a fidelity test redirects in
//!   the second byte, 0 there for every kind -- and returns the setting before, encoded the same
//!   way, or [`SET_MODE_REFUSED`] for a value that is no setting.
//! * `er_shader_editor_set_unfaithful_v1(ports: u32) -> u32` marks exactly the port kinds whose
//!   [`PortKind::bit`]s `ports` holds as unfaithful, keeps the mark in the settings file, and
//!   returns the mark before, or [`SET_MODE_REFUSED`] for a bit that names no kind. The fidelity
//!   proof calls it for the kinds that fail (design 14, P0.1 item 5).
//!
//! All three are always present and release behaviour does not depend on them: nothing in the DLL
//! calls them. `scripts/frida/shader-editor-stats.js` reaches them through `Module.getExportByName`, so
//! Frida never intercepts a prologue this DLL has detoured.
//!
//! # Telemetry
//!
//! Once a second the worker writes the stats, the root-signature ladder, the hook timings, each
//! port's GPU time a frame, last and mean, with its stage's total (`gpu_time`, null while the
//! timer is off; [`crate::gpu_time`] says what the number means), the diagnostic counters, the
//! Shaders panel's last frame -- where its header, toolbar, nav and named parts landed in screen
//! pixels, its dock, scale and fonts ([`FrameRecord`]) -- and the decoded probe -- every written
//! region's header, its frame age and its samples -- as one JSON object ([`render_telemetry`]) to
//! `redirected_artifact_path("ER_QUICKLOAD_CEL_SHADE_TELEMETRY_PATH", "er-cel-shade-telemetry.json")`,
//! replacing the file atomically, so a reader never sees half of one. The variable is an output
//! redirect for launchers, like every other `ER_QUICKLOAD_*_PATH`, and changes no behaviour.
//!
//! The layout, the renderer and the timing arithmetic are pure and tested on the host. The Windows
//! half reads the counters and holds the exports.

use std::fmt::Write as _;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use crate::cmdlist::{self, SAMPLE_EVERY, Timing};
use crate::gpu_time::{self, Counters, Report};
use crate::menu::panel_record::{PanelLabels, RECT_NAMES, ReferenceSnapshot};
use crate::menu::theme::{Face, FrameRecord};
use crate::probe::{self, Region, RegionRecord};
use crate::replace::{PortKind, Stage};
use crate::safety::ModeSetting;

/// What `er_shader_editor_stats_v1` writes. `repr(C)` with no padding: every field is listed in
/// [`StatsV1::LAYOUT`].
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct StatsV1 {
    /// Presents counted by the parameter ring; the frame the probe headers carry.
    pub presents: u64,
    /// The [`ModeSetting`], as [`ModeSetting::encode`] writes it: the mode's number in the low
    /// byte, and in a fidelity test of some kinds their bits in the next.
    pub mode: u32,
    /// 1 while every pipeline is bound as the game asked, for whatever reason.
    pub vanilla: u32,
    /// Root signature creations at each binding level.
    pub rs_full: u64,
    pub rs_cbv_probe: u64,
    pub rs_cbv: u64,
    pub rs_none: u64,
    /// Cel variants created per port kind, indexed by `PortKind as usize`.
    pub ports_ready: [u64; PortKind::COUNT],
    /// Binds that went to a variant, from every bind detour. Exact.
    pub redirected_binds: u64,
    /// Binds of a port's own pipeline while its stage had no content.
    pub passthrough_port_binds: u64,
    /// `SetPipelineState` calls; trails by fewer than 256 per recording thread.
    pub set_pso_calls: u64,
    /// Nanoseconds and samples of the timed detour calls, over every [`Timing`], before the timer
    /// floor is subtracted. The telemetry has them per timing, which is what the cost check reads.
    pub hook_ns_sum: u64,
    pub hook_ns_samples: u64,
    /// Command-list slot updates that found no slot.
    pub list_slot_misses: u64,
    /// Items waiting in the graveyard, and items it leaked because it was full.
    pub graveyard_len: u64,
    pub leaked: u64,
    /// Game pipelines the reaper found released by the game.
    pub game_psos_released: u64,
    /// Each probe region's header frame, in [`Region::ALL`] order; 0 for a region not written.
    pub probe_frame: [u32; 3],
    /// Each probe region's header variant id, in the same order: the port kind in the top byte.
    pub variant_id: [u32; 3],
    /// Milliseconds of the last runtime compile (none before the editor's compiler exists) and of
    /// the last cel variant created.
    pub compile_ms_last: f32,
    pub create_ms_last: f32,
    /// The ports marked unfaithful, one [`PortKind::bit`] each.
    pub unfaithful_ports: u32,
    /// The port kinds the hooks bind as the game asked this present: the unfaithful ones, or in a
    /// fidelity test every kind it is not about.
    pub ports_off: u32,
}

/// One field of [`StatsV1`] as a reader in another language sees it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Field {
    pub name: &'static str,
    pub offset: usize,
    /// `u64`, `u32` or `f32`.
    pub ty: &'static str,
    /// Elements: 1 for a scalar.
    pub count: usize,
}

impl Field {
    const fn new(name: &'static str, offset: usize, ty: &'static str, count: usize) -> Self {
        Self {
            name,
            offset,
            ty,
            count,
        }
    }

    /// Bytes of one element.
    #[must_use]
    pub const fn element_bytes(self) -> usize {
        match self.ty.as_bytes() {
            b"u64" => 8,
            _ => 4,
        }
    }
}

impl StatsV1 {
    /// Every field in order, with its byte offset.
    pub const LAYOUT: [Field; 23] = [
        Field::new("presents", 0, "u64", 1),
        Field::new("mode", 8, "u32", 1),
        Field::new("vanilla", 12, "u32", 1),
        Field::new("rs_full", 16, "u64", 1),
        Field::new("rs_cbv_probe", 24, "u64", 1),
        Field::new("rs_cbv", 32, "u64", 1),
        Field::new("rs_none", 40, "u64", 1),
        Field::new("ports_ready", 48, "u64", PortKind::COUNT),
        Field::new("redirected_binds", 88, "u64", 1),
        Field::new("passthrough_port_binds", 96, "u64", 1),
        Field::new("set_pso_calls", 104, "u64", 1),
        Field::new("hook_ns_sum", 112, "u64", 1),
        Field::new("hook_ns_samples", 120, "u64", 1),
        Field::new("list_slot_misses", 128, "u64", 1),
        Field::new("graveyard_len", 136, "u64", 1),
        Field::new("leaked", 144, "u64", 1),
        Field::new("game_psos_released", 152, "u64", 1),
        Field::new("probe_frame", 160, "u32", 3),
        Field::new("variant_id", 172, "u32", 3),
        Field::new("compile_ms_last", 184, "f32", 1),
        Field::new("create_ms_last", 188, "f32", 1),
        Field::new("unfaithful_ports", 192, "u32", 1),
        Field::new("ports_off", 196, "u32", 1),
    ];
}

/// Bytes `er_shader_editor_stats_v1` writes and returns.
pub const STATS_V1_BYTES: usize = 200;
const _: () = assert!(std::mem::size_of::<StatsV1>() == STATS_V1_BYTES);

/// What `er_shader_editor_set_mode_v1` returns for a value that is no setting, and
/// `er_shader_editor_set_unfaithful_v1` for a bit that names no port kind.
pub const SET_MODE_REFUSED: u32 = u32::MAX;

/// The telemetry file's name beside the game, and the variable a launcher redirects it with.
pub const TELEMETRY_FILE_NAME: &str = "er-cel-shade-telemetry.json";
pub const TELEMETRY_PATH_ENV: &str = "ER_QUICKLOAD_CEL_SHADE_TELEMETRY_PATH";

/// Milliseconds of the last runtime compile and of the last variant created, as `f32` bits.
static COMPILE_MS_LAST: AtomicU32 = AtomicU32::new(0);
static CREATE_MS_LAST: AtomicU32 = AtomicU32::new(0);

fn millis(elapsed: Duration) -> f32 {
    elapsed.as_secs_f32() * 1000.0
}

/// Record how long creating a cel variant took; returns it in milliseconds.
pub fn note_create(elapsed: Duration) -> f32 {
    let ms = millis(elapsed);
    CREATE_MS_LAST.store(ms.to_bits(), Ordering::Relaxed);
    ms
}

/// Record how long a runtime compile took (the editor's compiler); returns it in milliseconds.
pub fn note_compile(elapsed: Duration) -> f32 {
    let ms = millis(elapsed);
    COMPILE_MS_LAST.store(ms.to_bits(), Ordering::Relaxed);
    ms
}

/// The last compile and create times, in milliseconds.
#[must_use]
pub fn last_ms() -> (f32, f32) {
    (
        f32::from_bits(COMPILE_MS_LAST.load(Ordering::Relaxed)),
        f32::from_bits(CREATE_MS_LAST.load(Ordering::Relaxed)),
    )
}

/// The mean added cost of a timed call: the samples' mean less the timer floor, or `None` before
/// any call was timed.
#[must_use]
pub fn mean_ns(sum: u64, samples: u64, floor: u64) -> Option<u64> {
    (samples != 0).then(|| (sum / samples).saturating_sub(floor))
}

/// The port kind a probe header's variant id names, from its top byte.
#[must_use]
pub fn variant_kind(variant_id: u32) -> Option<PortKind> {
    PortKind::from_u8((variant_id >> 24) as u8)
}

/// Each written region's header frame and variant id, in [`Region::ALL`] order.
#[must_use]
pub fn probe_headers(regions: &[RegionRecord]) -> ([u32; 3], [u32; 3]) {
    let mut frames = [0; 3];
    let mut variants = [0; 3];
    for (at, region) in Region::ALL.into_iter().enumerate() {
        if let Some(record) = regions.iter().find(|record| record.region == region) {
            frames[at] = record.header.frame;
            variants[at] = record.header.variant_id;
        }
    }
    (frames, variants)
}

/// Everything one telemetry file carries.
#[derive(Clone, Debug)]
pub struct Telemetry<'a> {
    /// Milliseconds since the DLL started counting.
    pub written_ms: u64,
    pub stats: StatsV1,
    /// [`crate::cmdlist::STAGE_ACTIVE`].
    pub stage_active: u32,
    pub hook_faulted: bool,
    /// [`crate::cmdlist::FAULTS`].
    pub faults: u32,
    /// Per timing, indexed by `Timing as usize`: nanoseconds and samples of its timed calls.
    pub hook_ns: [(u64, u64); Timing::COUNT],
    /// Per timing: timed calls left out of `hook_ns` as preempted ([`cmdlist::PREEMPTED_NS`]).
    pub hook_preempted: [u64; Timing::COUNT],
    pub timer_floor_ns: u64,
    /// Named counters for diagnostics, in the order given.
    pub counters: Vec<(&'static str, u64)>,
    /// The Shaders panel's last frame, `None` before it ever drew.
    pub panel: Option<FrameRecord>,
    /// The ports' GPU time and the timer's counts, `None` while the timer is off.
    pub gpu: Option<(Report, Counters)>,
    /// The probe as read at `stats.presents`.
    pub regions: &'a [RegionRecord],
    /// Members rendered elsewhere, each ending in a comma and a newline: the vertex stage's and
    /// gate V0.R1's (`crate::vertex`). Empty on the host.
    pub members: String,
}

/// A JSON number for `value`, or `null` for a NaN or an infinity, which JSON cannot spell.
fn number(out: &mut String, value: f32) {
    if value.is_finite() {
        let _ = write!(out, "{value}");
    } else {
        out.push_str("null");
    }
}

fn floats(out: &mut String, values: &[f32]) {
    out.push('[');
    for (at, value) in values.iter().enumerate() {
        if at != 0 {
            out.push_str(", ");
        }
        number(out, *value);
    }
    out.push(']');
}

fn integers(out: &mut String, values: impl IntoIterator<Item = u64>) {
    out.push('[');
    for (at, value) in values.into_iter().enumerate() {
        if at != 0 {
            out.push_str(", ");
        }
        let _ = write!(out, "{value}");
    }
    out.push(']');
}

/// The telemetry file's text: one JSON object. Every key is a fixed ASCII name, so nothing needs
/// escaping.
#[must_use]
pub fn render_telemetry(telemetry: &Telemetry<'_>) -> String {
    let stats = &telemetry.stats;
    let present = stats.presents;
    let mut out = String::with_capacity(32 * 1024);
    out.push_str("{\n  \"schema\": 1,\n");
    let _ = writeln!(out, "  \"written_ms\": {},", telemetry.written_ms);
    let mode = ModeSetting::decode(stats.mode).map_or("unknown", |setting| setting.mode.name());
    let _ = writeln!(out, "  \"mode_name\": \"{mode}\",");

    out.push_str("  \"stats\": {\n");
    let _ = writeln!(out, "    \"presents\": {},", stats.presents);
    let _ = writeln!(out, "    \"mode\": {},", stats.mode);
    let _ = writeln!(out, "    \"vanilla\": {},", stats.vanilla);
    let _ = writeln!(out, "    \"rs_full\": {},", stats.rs_full);
    let _ = writeln!(out, "    \"rs_cbv_probe\": {},", stats.rs_cbv_probe);
    let _ = writeln!(out, "    \"rs_cbv\": {},", stats.rs_cbv);
    let _ = writeln!(out, "    \"rs_none\": {},", stats.rs_none);
    out.push_str("    \"ports_ready\": ");
    integers(&mut out, stats.ports_ready);
    out.push_str(",\n");
    let _ = writeln!(out, "    \"redirected_binds\": {},", stats.redirected_binds);
    let _ = writeln!(
        out,
        "    \"passthrough_port_binds\": {},",
        stats.passthrough_port_binds
    );
    let _ = writeln!(out, "    \"set_pso_calls\": {},", stats.set_pso_calls);
    let _ = writeln!(out, "    \"hook_ns_sum\": {},", stats.hook_ns_sum);
    let _ = writeln!(out, "    \"hook_ns_samples\": {},", stats.hook_ns_samples);
    let _ = writeln!(out, "    \"list_slot_misses\": {},", stats.list_slot_misses);
    let _ = writeln!(out, "    \"graveyard_len\": {},", stats.graveyard_len);
    let _ = writeln!(out, "    \"leaked\": {},", stats.leaked);
    let _ = writeln!(
        out,
        "    \"game_psos_released\": {},",
        stats.game_psos_released
    );
    out.push_str("    \"probe_frame\": ");
    integers(&mut out, stats.probe_frame.map(u64::from));
    out.push_str(",\n    \"variant_id\": ");
    integers(&mut out, stats.variant_id.map(u64::from));
    out.push_str(",\n    \"compile_ms_last\": ");
    number(&mut out, stats.compile_ms_last);
    out.push_str(",\n    \"create_ms_last\": ");
    number(&mut out, stats.create_ms_last);
    let _ = write!(
        out,
        ",\n    \"unfaithful_ports\": {},\n    \"ports_off\": {}",
        stats.unfaithful_ports, stats.ports_off
    );
    out.push_str("\n  },\n");

    let _ = writeln!(
        out,
        "  \"ladder\": {{\"full\": {}, \"cbv_probe\": {}, \"cbv\": {}, \"none\": {}}},",
        stats.rs_full, stats.rs_cbv_probe, stats.rs_cbv, stats.rs_none
    );
    let _ = writeln!(out, "  \"stage_active\": {},", telemetry.stage_active);
    let _ = writeln!(out, "  \"hook_faulted\": {},", telemetry.hook_faulted);
    let faulted: Vec<String> = cmdlist::faulted(telemetry.faults)
        .map(|detour| format!("\"{}\"", detour.name()))
        .collect();
    let _ = writeln!(out, "  \"faulted_detours\": [{}],", faulted.join(", "));

    out.push_str("  \"hooks\": [\n");
    for (at, timing) in Timing::ALL.into_iter().enumerate() {
        let (sum, samples) = telemetry.hook_ns[timing as usize];
        let preempted = telemetry.hook_preempted[timing as usize];
        let mean = mean_ns(sum, samples, telemetry.timer_floor_ns)
            .map_or_else(|| "null".to_owned(), |mean| mean.to_string());
        let _ = write!(
            out,
            "    {{\"detour\": \"{}\", \"ns_sum\": {sum}, \"samples\": {samples}, \"mean_ns\": {mean}, \
             \"preempted\": {preempted}}}",
            timing.name()
        );
        out.push_str(if at + 1 < Timing::COUNT { ",\n" } else { "\n" });
    }
    out.push_str("  ],\n");
    let _ = writeln!(out, "  \"timer_floor_ns\": {},", telemetry.timer_floor_ns);
    // One call in this many per recording thread is timed, so a timing's calls number about its
    // samples times this; `scripts/check-shader-editor-probe.py --expect cost` reads it.
    let _ = writeln!(out, "  \"hook_sample_every\": {SAMPLE_EVERY},");

    out.push_str("  \"gpu_time\": ");
    match &telemetry.gpu {
        Some((report, counters)) => render_gpu(&mut out, report, counters),
        None => out.push_str("null"),
    }
    out.push_str(",\n");

    out.push_str("  \"counters\": {");
    for (at, (name, value)) in telemetry.counters.iter().enumerate() {
        out.push_str(if at == 0 { "\n" } else { ",\n" });
        let _ = write!(out, "    \"{name}\": {value}");
    }
    out.push_str("\n  },\n");
    out.push_str(&telemetry.members);

    out.push_str("  \"panel\": ");
    match &telemetry.panel {
        Some(panel) => render_panel(&mut out, panel),
        None => out.push_str("null"),
    }
    out.push_str(",\n");

    let _ = writeln!(out, "  \"probe\": {{\n    \"present\": {present},");
    out.push_str("    \"regions\": [");
    for (at, record) in telemetry.regions.iter().enumerate() {
        out.push_str(if at == 0 { "\n" } else { ",\n" });
        render_region(&mut out, record, present);
    }
    out.push_str("\n    ]\n  }\n}\n");
    out
}

/// Microseconds as the telemetry writes them, to the nanosecond, or `null` while unknown.
fn micros(out: &mut String, value: Option<f64>) {
    match value.filter(|value| value.is_finite()) {
        Some(value) => {
            let _ = write!(out, "{value:.3}");
        }
        None => out.push_str("null"),
    }
}

/// The ports' GPU time as a JSON object: the rate, what was lost, and per port kind and per stage
/// the last known frame's microseconds and the mean over the history.
/// `scripts/check-shader-editor-probe.py --expect gpu-cost` reads it.
fn render_gpu(out: &mut String, report: &Report, counters: &Counters) {
    let frame = report
        .last
        .map_or_else(|| "null".to_owned(), |frame| frame.stamp.to_string());
    let _ = write!(
        out,
        "{{\n    \"frequency_hz\": {}, \"source\": \"{}\", \"read_lag_presents\": {}, \
         \"budget_us\": {}, \"history_frames\": {},\n    \"frame\": {frame}, \"frames\": {}, \
         \"unknown_frames\": {}, \"mean_frames\": {},\n    \"spans_begun\": {}, \"spans_dropped\": \
         {}, \"chunks\": {}, \"resolves\": {}, \"late\": {}, \"refused\": {},\n    \"ports\": [",
        report.frequency,
        report.source.name(),
        gpu_time::READ_LAG_PRESENTS,
        gpu_time::PORT_BUDGET_US,
        gpu_time::HISTORY_FRAMES,
        report.frames,
        report.unknown_frames,
        report.history,
        counters.begun,
        counters.dropped,
        counters.chunks,
        counters.resolves,
        report.late,
        report.refused,
    );
    for (at, kind) in PortKind::ALL.into_iter().enumerate() {
        out.push_str(if at == 0 { "\n" } else { ",\n" });
        let ticks = report.last.map_or(0, |frame| frame.ticks(kind));
        let _ = write!(
            out,
            "      {{\"kind\": \"{}\", \"index\": {}, \"us_last\": ",
            kind.key(),
            kind as u8
        );
        micros(out, report.last_us(kind));
        out.push_str(", \"us_mean\": ");
        micros(out, report.mean_us(kind));
        let _ = write!(
            out,
            ", \"spans_last\": {}, \"bound_last\": \"{}\", \"ticks_last\": {ticks}}}",
            report.spans_last(kind),
            report.bound_last(kind).name()
        );
    }
    out.push_str("\n    ],\n    \"stages\": [");
    for (at, stage) in Stage::ALL.into_iter().enumerate() {
        out.push_str(if at == 0 { "\n" } else { ",\n" });
        let _ = write!(
            out,
            "      {{\"stage\": \"{}\", \"us_last\": ",
            stage.name()
        );
        micros(out, report.stage_last_us(stage));
        out.push_str(", \"us_mean\": ");
        micros(out, report.stage_mean_us(stage));
        out.push('}');
    }
    out.push_str("\n    ]\n  }");
}

/// The panel's last frame as a JSON object: its state, its fonts, the Code sub-tab's reference
/// column (open, its search field focused, its stage, its query's length and the entries it kept
/// of all it has), and each part drawn that frame under its [`RECT_NAMES`] name as `[x, y, width,
/// height]` in screen pixels. A part not drawn that frame is left out.
fn render_panel(out: &mut String, panel: &FrameRecord) {
    let labels = PanelLabels::read(panel);
    let _ = write!(
        out,
        "{{\n    \"draws\": {}, \"present\": {}, \"open\": {}, \"collapsed\": {}, \
         \"dock\": \"{}\", \"tab\": \"{}\", \"code_view\": {}, \"sub\": \"{}\",\n    \"scale\": ",
        panel.draws,
        panel.present,
        panel.open,
        panel.collapsed,
        labels.dock,
        labels.tab,
        labels.code_view,
        labels.sub
    );
    number(out, panel.scale);
    out.push_str(", \"font_scale\": ");
    number(out, panel.font_scale);
    out.push_str(", \"display\": ");
    floats(out, &panel.display);
    let fonts = &panel.fonts;
    let _ = write!(
        out,
        ",\n    \"fonts\": {{\"requested\": {}, \"request_scale\": ",
        fonts.requested
    );
    number(out, fonts.request_scale);
    out.push_str(", \"faces\": [");
    for (at, face) in Face::ALL.into_iter().enumerate() {
        if at != 0 {
            out.push_str(", ");
        }
        let _ = write!(
            out,
            "{{\"name\": \"{}\", \"handle\": {}, \"granted\": {}}}",
            face.name(),
            fonts.handles[at],
            fonts.granted[at]
        );
    }
    let reference = ReferenceSnapshot::read(panel);
    let _ = write!(
        out,
        "], \"in_use\": {}}},\n    \"reference\": {{\"open\": {}, \"searching\": {}, \"stage\": \
         \"{}\", \"query_chars\": {}, \"shown\": {}, \"total\": {}}},\n    \"rects\": {{",
        fonts.in_use,
        reference.open,
        reference.searching,
        reference.stage,
        reference.query_chars,
        reference.shown,
        reference.total
    );
    let mut first = true;
    for (name, rect) in RECT_NAMES.iter().zip(&panel.rects) {
        let Some(rect) = rect else {
            continue;
        };
        out.push_str(if first { "\n" } else { ",\n" });
        first = false;
        let _ = write!(out, "      \"{name}\": ");
        floats(out, rect);
    }
    out.push_str(if first { "}" } else { "\n    }" });
    let _ = write!(
        out,
        ",\n    \"rects_refused\": {}\n  }}",
        panel.rects_refused
    );
}

fn render_region(out: &mut String, record: &RegionRecord, present: u64) {
    let header = record.header;
    let kind = variant_kind(header.variant_id);
    let _ = writeln!(out, "      {{\"region\": \"{}\",", record.region.name());
    match kind {
        Some(kind) => {
            let _ = writeln!(
                out,
                "       \"kind\": {}, \"pass\": \"{}\",",
                kind as u8,
                kind.replacement().name
            );
        }
        None => {
            let _ = writeln!(
                out,
                "       \"kind\": {}, \"pass\": null,",
                header.variant_id >> 24
            );
        }
    }
    let _ = writeln!(
        out,
        "       \"variant_id\": {}, \"frame\": {}, \"frame_age\": {},",
        header.variant_id,
        header.frame,
        probe::frame_age(present, header.frame)
    );
    let _ = writeln!(
        out,
        "       \"width\": {}, \"height\": {}, \"max_x\": {}, \"max_y\": {}, \"stale\": {},",
        header.width, header.height, header.max_x, header.max_y, record.stale
    );
    out.push_str("       \"samples\": [");
    for (at, sample) in record.samples.iter().enumerate() {
        out.push_str(if at == 0 { "\n" } else { ",\n" });
        let _ = write!(out, "        {{\"index\": {}, \"pre\": ", sample.index);
        floats(out, &sample.pre);
        out.push_str(", \"post\": ");
        floats(out, &sample.post);
        let _ = write!(out, ", \"aux\": {}}}", sample.aux);
    }
    out.push_str("]}");
}

#[cfg(windows)]
pub use process::{
    RECORD_BYTES, RECORDS, TIMER_FLOOR_NS, er_shader_editor_set_mode_v1,
    er_shader_editor_set_unfaithful_v1, er_shader_editor_stats_v1, stats_now, tick,
};

/// The exports, the counters they read, and the worker's telemetry tick.
#[cfg(windows)]
mod process {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};

    use er_game_base::log::redirected_artifact_path;

    use super::{
        SET_MODE_REFUSED, STATS_V1_BYTES, StatsV1, TELEMETRY_FILE_NAME, TELEMETRY_PATH_ENV,
        Telemetry, last_ms, mean_ns, probe_headers, render_telemetry,
    };
    use crate::cmdlist::{self, Timing};
    use crate::look_binding::{self, Level};
    use crate::probe::{self, Region, RegionRecord};
    use crate::replace::PortKind;
    use crate::safety::{self, ModeSetting};
    use crate::settings::render_ports;
    use crate::{gpu_time, graveyard, hooks, log, menu, params, record, table};

    /// Port records kept and the bytes they own, as the worker last counted them.
    pub static RECORDS: AtomicUsize = AtomicUsize::new(0);
    pub static RECORD_BYTES: AtomicUsize = AtomicUsize::new(0);
    /// The timer floor the worker last measured, for the menu's per-call means.
    pub static TIMER_FLOOR_NS: AtomicU64 = AtomicU64::new(0);

    /// How often the stats line is logged when nothing it watches has changed.
    const STATS_LOG_PERIOD_MS: u64 = 30_000;

    fn count(value: usize) -> u64 {
        value as u64
    }

    /// The stats as they stand, with the probe as read at present `present`.
    #[must_use]
    pub fn stats_now(present: u64, regions: &[RegionRecord]) -> StatsV1 {
        let relaxed = |a: &AtomicUsize| count(a.load(Ordering::Relaxed));
        let ladder = look_binding::ladder_counts();
        let (sum, samples) = cmdlist::hook_ns_totals();
        let (probe_frame, variant_id) = probe_headers(regions);
        let (compile_ms_last, create_ms_last) = last_ms();
        StatsV1 {
            presents: present,
            mode: safety::mode_setting().encode(),
            vanilla: u32::from(cmdlist::VANILLA.load(Ordering::Acquire)),
            rs_full: count(ladder[Level::Full as usize]),
            rs_cbv_probe: count(ladder[Level::CbvProbe as usize]),
            rs_cbv: count(ladder[Level::Cbv as usize]),
            rs_none: count(ladder[Level::None as usize]),
            ports_ready: std::array::from_fn(|kind| relaxed(&hooks::PORTS_READY[kind])),
            redirected_binds: cmdlist::REDIRECTED_BINDS.load(Ordering::Relaxed),
            passthrough_port_binds: cmdlist::PASSTHROUGH_PORT_BINDS.load(Ordering::Relaxed),
            set_pso_calls: cmdlist::SET_PSO_CALLS.load(Ordering::Relaxed),
            hook_ns_sum: sum,
            hook_ns_samples: samples,
            list_slot_misses: cmdlist::LIST_SLOT_MISSES.load(Ordering::Relaxed),
            graveyard_len: relaxed(&graveyard::GRAVEYARD_LEN),
            leaked: graveyard::LEAKED.load(Ordering::Relaxed),
            game_psos_released: table::GAME_PSOS_RELEASED.load(Ordering::Relaxed),
            probe_frame,
            variant_id,
            compile_ms_last,
            create_ms_last,
            unfaithful_ports: safety::UNFAITHFUL.load(Ordering::Relaxed),
            ports_off: cmdlist::PORTS_OFF.load(Ordering::Relaxed),
        }
    }

    /// `er_shader_editor_stats_v1`: fill `*out` and return [`STATS_V1_BYTES`], or 0 for a null
    /// pointer or a panic while reading.
    ///
    /// # Safety
    ///
    /// `out` must be null or point at [`STATS_V1_BYTES`] writable bytes; no alignment is needed.
    #[unsafe(no_mangle)]
    pub unsafe extern "C" fn er_shader_editor_stats_v1(out: *mut StatsV1) -> u32 {
        if out.is_null() {
            return 0;
        }
        let read = catch_unwind(AssertUnwindSafe(|| {
            let present = params::presents();
            stats_now(present, &probe::read_latest(present))
        }));
        let Ok(stats) = read else {
            return 0;
        };
        // SAFETY: the caller's contract.
        unsafe { out.write_unaligned(stats) };
        STATS_V1_BYTES as u32
    }

    /// A mode setting as the log spells it: the mode, and a fidelity test's kinds.
    fn setting_name(setting: ModeSetting) -> String {
        match setting.mode {
            safety::Mode::Fidelity => format!("fidelity of `{}`", render_ports(setting.kinds)),
            mode => mode.name().to_owned(),
        }
    }

    /// `er_shader_editor_set_mode_v1`: switch to the setting `mode` encodes and return the setting
    /// before, encoded, or [`SET_MODE_REFUSED`] for a value that is no setting. The new mode's look
    /// and stages are staged at once, so they apply even while the overlay draws nothing.
    #[unsafe(no_mangle)]
    pub extern "C" fn er_shader_editor_set_mode_v1(mode: u32) -> u32 {
        let Some(setting) = ModeSetting::decode(mode) else {
            return SET_MODE_REFUSED;
        };
        catch_unwind(|| {
            let before = safety::set_mode(setting);
            menu::restage();
            log(format_args!(
                "mode: {} (was {}) through the export; vanilla={} ports_off=`{}`",
                setting_name(setting),
                setting_name(before),
                cmdlist::VANILLA.load(Ordering::Acquire),
                render_ports(cmdlist::PORTS_OFF.load(Ordering::Relaxed) as u8)
            ));
            before.encode()
        })
        .unwrap_or(SET_MODE_REFUSED)
    }

    /// `er_shader_editor_set_unfaithful_v1`: mark exactly the port kinds `ports` names as
    /// unfaithful, keep the mark in the settings file, and return the mark before, or
    /// [`SET_MODE_REFUSED`] for a bit that names no kind.
    #[unsafe(no_mangle)]
    pub extern "C" fn er_shader_editor_set_unfaithful_v1(ports: u32) -> u32 {
        let Ok(bits) = u8::try_from(ports) else {
            return SET_MODE_REFUSED;
        };
        if bits & !PortKind::ALL_BITS != 0 {
            return SET_MODE_REFUSED;
        }
        catch_unwind(|| {
            let before = menu::set_unfaithful(bits);
            log(format_args!(
                "unfaithful ports: `{}` (was `{}`) through the export; they are drawn as the game \
                 draws them until the menu or the settings file clears them",
                render_ports(bits),
                render_ports(before)
            ));
            u32::from(before)
        })
        .unwrap_or(SET_MODE_REFUSED)
    }

    /// The named counters the telemetry and the stats line carry beyond [`StatsV1`].
    fn counters() -> Vec<(&'static str, u64)> {
        let relaxed = |a: &AtomicUsize| count(a.load(Ordering::Relaxed));
        let published = table::published();
        vec![
            ("variants", relaxed(&hooks::VARIANTS)),
            ("ports_left_vanilla", relaxed(&hooks::PORTS_LEFT_VANILLA)),
            ("rewrites_refused", relaxed(&hooks::REWRITES_REFUSED)),
            (
                "no_root_signature_fixed",
                relaxed(&hooks::NO_ROOT_SIGNATURE_FIXED),
            ),
            (
                "no_root_signature_left",
                relaxed(&hooks::NO_ROOT_SIGNATURE_LEFT),
            ),
            ("look_binds", relaxed(&look_binding::BINDS)),
            ("rs_table_full", relaxed(&look_binding::TABLE_FULL)),
            (
                "rs_skipped_no_room",
                relaxed(&look_binding::SKIPPED_NO_ROOM),
            ),
            ("rs_skipped_other", relaxed(&look_binding::SKIPPED_OTHER)),
            (
                "redirects_live",
                count(published.map_or(0, |table| table.redirects())),
            ),
            (
                "table_entries",
                count(published.map_or(0, |table| table.len())),
            ),
            ("table_publishes", table::PUBLISHES.load(Ordering::Relaxed)),
            ("reaped_lost", table::REAPED_LOST.load(Ordering::Relaxed)),
            (
                "graveyard_released",
                graveyard::RELEASED.load(Ordering::Relaxed),
            ),
            (
                "closes_unmatched",
                cmdlist::CLOSES_UNMATCHED.load(Ordering::Relaxed),
            ),
            (
                "probe_barriers",
                cmdlist::PROBE_BARRIERS.load(Ordering::Relaxed),
            ),
            (
                "probe_copies",
                cmdlist::PROBE_COPIES.load(Ordering::Relaxed),
            ),
            (
                "probe_copies_skipped",
                cmdlist::PROBE_COPIES_SKIPPED.load(Ordering::Relaxed),
            ),
            (
                "reset_with_pso",
                cmdlist::RESET_WITH_PSO.load(Ordering::Relaxed),
            ),
            (
                "clear_state_with_pso",
                cmdlist::CLEAR_STATE_WITH_PSO.load(Ordering::Relaxed),
            ),
            (
                "create_list_with_pso",
                cmdlist::CREATE_LIST_WITH_PSO.load(Ordering::Relaxed),
            ),
            ("records", relaxed(&RECORDS)),
            ("record_bytes", relaxed(&RECORD_BYTES)),
        ]
        .into_iter()
        // The Shaders panel's own: edit callbacks, pastes, saves, edits, panics, its frame time.
        .chain(menu::telemetry_counters())
        .collect()
    }

    /// What the stats line watches: a change logs it at once.
    type Watched = (
        u32,
        u32,
        u32,
        u32,
        [u64; PortKind::COUNT],
        u64,
        u64,
        u64,
        u64,
        u64,
    );

    struct LogState {
        last: Option<Watched>,
        at_ms: u64,
    }

    static STATS_LOGGED: Mutex<LogState> = Mutex::new(LogState {
        last: None,
        at_ms: 0,
    });

    /// A telemetry write failed; latched so a read-only directory costs one line, not one a second.
    static WRITE_FAILED: AtomicBool = AtomicBool::new(false);
    static PATH_LOGGED: AtomicBool = AtomicBool::new(false);

    /// The worker's once-a-second step: refresh the cached counts, write the telemetry file, and
    /// log the stats line when it is due.
    pub fn tick() {
        let floor = cmdlist::timer_floor_ns();
        TIMER_FLOOR_NS.store(floor, Ordering::Relaxed);
        let (records, bytes) = record::with_ports(|ports| {
            (
                ports.len(),
                ports
                    .iter()
                    .map(|port| port.record.owned_bytes())
                    .sum::<usize>(),
            )
        });
        RECORDS.store(records, Ordering::Relaxed);
        RECORD_BYTES.store(bytes, Ordering::Relaxed);

        let present = params::presents();
        let regions = probe::read_latest(present);
        let stats = stats_now(present, &regions);
        let counters = counters();
        let now = graveyard::now_ms();
        let stage_active = cmdlist::STAGE_ACTIVE.load(Ordering::Relaxed);
        let telemetry = Telemetry {
            written_ms: now,
            stats,
            stage_active,
            hook_faulted: cmdlist::HOOK_FAULTED.load(Ordering::Acquire),
            faults: cmdlist::FAULTS.load(Ordering::Acquire),
            hook_ns: Timing::ALL.map(|timing| {
                (
                    cmdlist::HOOK_NS_SUM[timing as usize].load(Ordering::Relaxed),
                    cmdlist::HOOK_NS_SAMPLES[timing as usize].load(Ordering::Relaxed),
                )
            }),
            hook_preempted: Timing::ALL
                .map(|timing| cmdlist::HOOK_PREEMPTED[timing as usize].load(Ordering::Relaxed)),
            timer_floor_ns: floor,
            counters,
            panel: menu::theme::last_record(),
            gpu: gpu_time::report().map(|report| (report, gpu_time::Counters::read())),
            regions: &regions,
            members: crate::vertex::telemetry_json(),
        };
        write_telemetry(&render_telemetry(&telemetry));
        log_stats(&telemetry, now);
        crate::vertex::flush_proof();
    }

    fn write_telemetry(text: &str) {
        let path = redirected_artifact_path(TELEMETRY_PATH_ENV, TELEMETRY_FILE_NAME);
        if !PATH_LOGGED.swap(true, Ordering::Relaxed) {
            log(format_args!("telemetry: {} once a second", path.display()));
        }
        match er_hotkey_config::write_atomic(&path, text) {
            Ok(()) => {
                if WRITE_FAILED.swap(false, Ordering::Relaxed) {
                    log(format_args!("telemetry: writing again"));
                }
            }
            Err(error) => {
                if !WRITE_FAILED.swap(true, Ordering::Relaxed) {
                    log(format_args!(
                        "telemetry: could not write {}: {error}",
                        path.display()
                    ));
                }
            }
        }
    }

    fn counter(telemetry: &Telemetry<'_>, name: &str) -> u64 {
        telemetry
            .counters
            .iter()
            .find(|(counter, _)| *counter == name)
            .map_or(0, |(_, value)| *value)
    }

    /// Log one line of the stats when what it watches changed, and every
    /// [`STATS_LOG_PERIOD_MS`] otherwise. The hooks never log; this is where their counters reach
    /// the log.
    fn log_stats(telemetry: &Telemetry<'_>, now: u64) {
        let stats = &telemetry.stats;
        let watched: Watched = (
            stats.mode,
            stats.vanilla,
            telemetry.stage_active,
            stats.ports_off,
            stats.ports_ready,
            counter(telemetry, "variants"),
            counter(telemetry, "rewrites_refused"),
            counter(telemetry, "ports_left_vanilla"),
            stats.game_psos_released,
            stats.leaked,
        );
        {
            let mut logged = STATS_LOGGED
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let changed = logged.last != Some(watched);
            if !changed && now.saturating_sub(logged.at_ms) < STATS_LOG_PERIOD_MS {
                return;
            }
            logged.last = Some(watched);
            logged.at_ms = now;
        }
        let per_call = mean_ns(
            stats.hook_ns_sum,
            stats.hook_ns_samples,
            telemetry.timer_floor_ns,
        )
        .map_or_else(|| "-".to_owned(), |mean| mean.to_string());
        let ages: Vec<String> = Region::ALL
            .into_iter()
            .map(|region| {
                telemetry
                    .regions
                    .iter()
                    .find(|record| record.region == region)
                    .map_or_else(
                        || "-".to_owned(),
                        |record| probe::frame_age(stats.presents, record.header.frame).to_string(),
                    )
            })
            .collect();
        // Each port kind's mean GPU microseconds a frame, `-` while unknown.
        let gpu_us: Vec<String> = PortKind::ALL
            .into_iter()
            .map(|kind| {
                telemetry
                    .gpu
                    .and_then(|(report, _)| report.mean_us(kind))
                    .map_or_else(|| "-".to_owned(), |us| format!("{us:.0}"))
            })
            .collect();
        log(format_args!(
            "stats: presents={} mode={} vanilla={} stages={:#05b} ports_off=`{}` \
             unfaithful=`{}` variants={} refused={} left_vanilla={} ports_ready={:?} \
             redirects={} redirected_binds={} passthrough_port_binds={} set_pso_calls={} \
             hook_ns_per_call={per_call} list_slot_misses={} probe_barriers={} probe_copies={} \
             probe_copies_skipped={} probe_frame_age=[{}] gpu_us_mean=[{}] graveyard={} \
             leaked={} game_psos_released={} create_ms_last={:.2}",
            stats.presents,
            ModeSetting::decode(stats.mode).map_or_else(|| "unknown".to_owned(), setting_name),
            stats.vanilla,
            telemetry.stage_active,
            render_ports(stats.ports_off as u8),
            render_ports(stats.unfaithful_ports as u8),
            counter(telemetry, "variants"),
            counter(telemetry, "rewrites_refused"),
            counter(telemetry, "ports_left_vanilla"),
            stats.ports_ready,
            counter(telemetry, "redirects_live"),
            stats.redirected_binds,
            stats.passthrough_port_binds,
            stats.set_pso_calls,
            stats.list_slot_misses,
            counter(telemetry, "probe_barriers"),
            counter(telemetry, "probe_copies"),
            counter(telemetry, "probe_copies_skipped"),
            ages.join(","),
            gpu_us.join(","),
            stats.graveyard_len,
            stats.leaked,
            stats.game_psos_released,
            stats.create_ms_last,
        ));
    }
}

#[cfg(test)]
mod tests {
    use std::mem::{align_of, offset_of, size_of};

    use super::*;
    use crate::cmdlist::Detour;
    use crate::probe::{Header, Sample};

    #[test]
    fn stats_v1_is_laid_out_as_its_table_says() {
        assert_eq!(size_of::<StatsV1>(), STATS_V1_BYTES);
        assert_eq!(align_of::<StatsV1>(), 8);
        let actual = [
            offset_of!(StatsV1, presents),
            offset_of!(StatsV1, mode),
            offset_of!(StatsV1, vanilla),
            offset_of!(StatsV1, rs_full),
            offset_of!(StatsV1, rs_cbv_probe),
            offset_of!(StatsV1, rs_cbv),
            offset_of!(StatsV1, rs_none),
            offset_of!(StatsV1, ports_ready),
            offset_of!(StatsV1, redirected_binds),
            offset_of!(StatsV1, passthrough_port_binds),
            offset_of!(StatsV1, set_pso_calls),
            offset_of!(StatsV1, hook_ns_sum),
            offset_of!(StatsV1, hook_ns_samples),
            offset_of!(StatsV1, list_slot_misses),
            offset_of!(StatsV1, graveyard_len),
            offset_of!(StatsV1, leaked),
            offset_of!(StatsV1, game_psos_released),
            offset_of!(StatsV1, probe_frame),
            offset_of!(StatsV1, variant_id),
            offset_of!(StatsV1, compile_ms_last),
            offset_of!(StatsV1, create_ms_last),
            offset_of!(StatsV1, unfaithful_ports),
            offset_of!(StatsV1, ports_off),
        ];
        assert_eq!(actual.len(), StatsV1::LAYOUT.len());
        for (field, offset) in StatsV1::LAYOUT.iter().zip(actual) {
            assert_eq!(field.offset, offset, "{}", field.name);
        }
        // The fields tile the struct: no padding a reader in another language would have to
        // guess at, and nothing past the end.
        let mut next = 0;
        for field in StatsV1::LAYOUT {
            assert_eq!(field.offset, next, "{} leaves a gap", field.name);
            assert_eq!(field.offset % field.element_bytes(), 0, "{}", field.name);
            next += field.element_bytes() * field.count;
        }
        assert_eq!(next, STATS_V1_BYTES);
        // Array lengths agree with the types they mirror.
        assert_eq!(StatsV1::LAYOUT[7].count, PortKind::COUNT);
        assert_eq!(StatsV1::LAYOUT[17].count, Region::ALL.len());
        assert_eq!(StatsV1::LAYOUT[18].count, Region::ALL.len());
    }

    #[test]
    fn a_stats_v1_reads_back_from_its_bytes_at_the_listed_offsets() {
        let stats = StatsV1 {
            presents: 0x0102_0304_0506_0708,
            mode: 2,
            vanilla: 1,
            ports_ready: [1, 0, 1, 0, 1],
            probe_frame: [7, 0, 9],
            variant_id: [0, 0, 4 << 24],
            create_ms_last: 1.5,
            unfaithful_ports: 0b10,
            ports_off: 0b1_1101,
            ..StatsV1::default()
        };
        // SAFETY: a `repr(C)` struct of integers and floats with no padding, read as bytes.
        let bytes: &[u8] =
            unsafe { std::slice::from_raw_parts((&raw const stats).cast::<u8>(), STATS_V1_BYTES) };
        let word = |at: usize| {
            u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
        };
        let at = |name: &str| {
            StatsV1::LAYOUT
                .iter()
                .find(|field| field.name == name)
                .map(|field| field.offset)
                .unwrap_or(usize::MAX)
        };
        assert_eq!(word(at("presents")), 0x0506_0708);
        assert_eq!(word(at("mode")), 2);
        assert_eq!(word(at("vanilla")), 1);
        assert_eq!(word(at("ports_ready") + 2 * 8), 1);
        assert_eq!(word(at("probe_frame") + 2 * 4), 9);
        assert_eq!(word(at("variant_id") + 2 * 4), 4 << 24);
        assert_eq!(f32::from_bits(word(at("create_ms_last"))), 1.5);
        assert_eq!(word(at("unfaithful_ports")), 0b10);
        assert_eq!(word(at("ports_off")), 0b1_1101);
    }

    #[test]
    fn mean_ns_subtracts_the_floor_and_needs_a_sample() {
        assert_eq!(mean_ns(0, 0, 10), None);
        assert_eq!(mean_ns(5_000, 100, 20), Some(30));
        assert_eq!(mean_ns(500, 100, 20), Some(0));
    }

    #[test]
    fn create_and_compile_times_are_kept_in_milliseconds() {
        assert_eq!(note_create(Duration::from_micros(2_500)), 2.5);
        assert_eq!(note_compile(Duration::from_millis(18)), 18.0);
        assert_eq!(last_ms(), (18.0, 2.5));
    }

    #[test]
    fn the_variant_id_names_its_port_kind_in_the_top_byte() {
        for kind in PortKind::ALL {
            assert_eq!(variant_kind((kind as u32) << 24), Some(kind));
            assert_eq!(
                variant_kind(((kind as u32) << 24) | 0x00ab_cdef),
                Some(kind)
            );
        }
        assert_eq!(variant_kind(0xff00_0000), None);
    }

    fn region(region: Region, frame: u32, variant_id: u32, samples: Vec<Sample>) -> RegionRecord {
        RegionRecord {
            region,
            header: Header {
                frame,
                variant_id,
                width: 1920,
                height: 1080,
                max_x: 1919,
                max_y: 1079,
            },
            samples,
            stale: 2,
        }
    }

    #[test]
    fn probe_headers_follow_the_region_order() {
        let regions = [
            region(Region::Tonemap, 30, 4 << 24, Vec::new()),
            region(Region::Ao, 31, 1 << 24, Vec::new()),
        ];
        assert_eq!(
            probe_headers(&regions),
            ([31, 0, 30], [1 << 24, 0, 4 << 24])
        );
        assert_eq!(probe_headers(&[]), ([0; 3], [0; 3]));
    }

    /// A strict reader of the JSON this module writes: objects, arrays, strings without escapes,
    /// numbers, `true`, `false` and `null`. Returns the bytes after one value.
    fn json_value(text: &[u8]) -> Result<&[u8], String> {
        let text = text.trim_ascii_start();
        match text.first() {
            Some(b'{') => json_list(&text[1..], b'}', true),
            Some(b'[') => json_list(&text[1..], b']', false),
            Some(b'"') => json_string(text),
            Some(b't') => text
                .strip_prefix(b"true")
                .ok_or_else(|| "bad true".to_owned()),
            Some(b'f') => text
                .strip_prefix(b"false")
                .ok_or_else(|| "bad false".to_owned()),
            Some(b'n') => text
                .strip_prefix(b"null")
                .ok_or_else(|| "bad null".to_owned()),
            Some(b'-' | b'0'..=b'9') => {
                let end = text
                    .iter()
                    .position(|c| !matches!(c, b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9'))
                    .unwrap_or(text.len());
                let number = std::str::from_utf8(&text[..end]).map_err(|e| e.to_string())?;
                number
                    .parse::<f64>()
                    .map_err(|_| format!("bad number {number}"))?;
                Ok(&text[end..])
            }
            other => Err(format!("unexpected {other:?}")),
        }
    }

    fn json_string(text: &[u8]) -> Result<&[u8], String> {
        let body = text
            .strip_prefix(b"\"")
            .ok_or_else(|| format!("expected a string at {:?}", text.first()))?;
        let close = body
            .iter()
            .position(|c| *c == b'"')
            .ok_or_else(|| "unterminated string".to_owned())?;
        if body[..close].contains(&b'\\') {
            return Err("an escape in a string this module never escapes".to_owned());
        }
        Ok(&body[close + 1..])
    }

    fn json_list(mut text: &[u8], close: u8, keyed: bool) -> Result<&[u8], String> {
        text = text.trim_ascii_start();
        if let Some(rest) = text.strip_prefix(&[close]) {
            return Ok(rest);
        }
        loop {
            if keyed {
                text = json_string(text.trim_ascii_start())?.trim_ascii_start();
                text = text
                    .strip_prefix(b":")
                    .ok_or_else(|| "missing colon".to_owned())?;
            }
            text = json_value(text)?.trim_ascii_start();
            match text.first() {
                Some(b',') => text = &text[1..],
                Some(c) if *c == close => return Ok(&text[1..]),
                other => return Err(format!("expected , or {} not {other:?}", close as char)),
            }
        }
    }

    fn telemetry_with<'a>(regions: &'a [RegionRecord]) -> Telemetry<'a> {
        Telemetry {
            written_ms: 12_345,
            stats: StatsV1 {
                presents: 100,
                mode: 2,
                ports_ready: [1, 1, 1, 0, 1],
                probe_frame: [97, 0, 96],
                variant_id: [1 << 24, 0, 4 << 24],
                create_ms_last: f32::NAN,
                compile_ms_last: f32::INFINITY,
                ..StatsV1::default()
            },
            stage_active: 7,
            hook_faulted: true,
            faults: Detour::Close.bit() | Detour::Reset.bit(),
            hook_ns: [(5_000, 100), (0, 0), (0, 0), (900, 3), (0, 0)],
            hook_preempted: [2, 0, 0, 0, 0],
            timer_floor_ns: 20,
            counters: vec![("variants", 4), ("rewrites_refused", 0)],
            panel: None,
            gpu: None,
            regions,
            members: "  \"r1\": {\"lists\": 3, \"frames\": 1},\n".to_owned(),
        }
    }

    #[test]
    fn the_telemetry_carries_each_ports_gpu_time() {
        use crate::gpu_time::{ReadSpan, Reader, Source, tag};
        let mut reader = Reader::new();
        let span = |kind: PortKind, variant: bool, begin: u64, ticks: u64| ReadSpan {
            tag: tag(kind, variant),
            begin,
            end: begin + ticks,
        };
        // 1 GHz: the tonemap variant twice, 300 us and 112.5 us; AO's own pipeline, 1.6 ms.
        reader.read(
            41,
            [
                span(PortKind::Tonemap, true, 10, 300_000),
                span(PortKind::Tonemap, true, 900_000, 112_500),
                span(PortKind::Ao, false, 2_000_000, 1_600_000),
            ],
            1_000_000_000,
            Source::GameQueue,
        );
        let counters = Counters {
            begun: 9,
            dropped: 1,
            chunks: 4,
            resolves: 3,
        };
        let telemetry = Telemetry {
            gpu: Some((*reader.report(), counters)),
            ..telemetry_with(&[])
        };
        let text = render_telemetry(&telemetry);
        let rest = json_value(text.as_bytes()).unwrap_or_else(|error| panic!("{error}\n{text}"));
        assert!(rest.trim_ascii().is_empty());
        for needle in [
            "\"gpu_time\": {\n    \"frequency_hz\": 1000000000, \"source\": \"game_direct_queue\", \
             \"read_lag_presents\": 5, \"budget_us\": 1500, \"history_frames\": 32",
            "\"frame\": 41, \"frames\": 1, \"unknown_frames\": 0, \"mean_frames\": 1",
            "\"spans_begun\": 9, \"spans_dropped\": 1, \"chunks\": 4, \"resolves\": 3, \"late\": 0, \
             \"refused\": 0",
            "{\"kind\": \"tonemap\", \"index\": 3, \"us_last\": 412.500, \"us_mean\": 412.500, \
             \"spans_last\": 2, \"bound_last\": \"variant\", \"ticks_last\": 412500}",
            "{\"kind\": \"ao\", \"index\": 0, \"us_last\": 1600.000, \"us_mean\": 1600.000, \
             \"spans_last\": 1, \"bound_last\": \"game\"",
            "{\"kind\": \"cmb_albedo\", \"index\": 2, \"us_last\": 0.000, \"us_mean\": 0.000, \
             \"spans_last\": 0, \"bound_last\": \"none\"",
            "{\"stage\": \"lighting\", \"us_last\": 1600.000, \"us_mean\": 1600.000}",
            "{\"stage\": \"tonemap\", \"us_last\": 412.500",
        ] {
            assert!(text.contains(needle), "missing {needle}\n{text}");
        }
        // Without a rate every figure is null, and with the timer off the object is.
        let unrated = Telemetry {
            gpu: Some((*Reader::new().report(), Counters::default())),
            ..telemetry_with(&[])
        };
        let text = render_telemetry(&unrated);
        assert!(json_value(text.as_bytes()).is_ok(), "{text}");
        assert!(text.contains("\"frame\": null"));
        assert!(text.contains("\"us_last\": null, \"us_mean\": null"));
        let text = render_telemetry(&telemetry_with(&[]));
        assert!(text.contains("\"gpu_time\": null,"));
    }

    #[test]
    fn the_telemetry_carries_the_panels_last_frame() {
        let mut panel = FrameRecord {
            draws: 42,
            present: 100,
            open: true,
            scale: 1.0,
            font_scale: f32::NAN,
            display: [1920.0, 1080.0],
            rects: vec![None; RECT_NAMES.len()],
            ..FrameRecord::default()
        };
        PanelLabels {
            dock: "right",
            tab: "layers",
            sub: "parameters",
            code_view: false,
        }
        .store(&mut panel);
        panel.fonts.requested = true;
        panel.fonts.request_scale = 1.0;
        panel.fonts.handles = [5, 6, 0, 8, 9, 10];
        panel.fonts.granted = [true, true, false, true, true, true];
        panel.fonts.in_use = true;
        ReferenceSnapshot {
            open: true,
            stage: "tonemap_post",
            query_chars: 5,
            shown: 3,
            total: 286,
            ..ReferenceSnapshot::default()
        }
        .store(&mut panel);
        let at = |name: &str| RECT_NAMES.iter().position(|known| *known == name);
        if let Some(header) = at("header") {
            panel.rects[header] = Some([1460.0, 0.0, 460.0, 33.0]);
        }
        if let Some(reference) = at("reference_panel") {
            panel.rects[reference] = Some([1610.0, 300.0, 296.0, 520.0]);
        }
        if let Some(nav) = at("nav_active") {
            panel.rects[nav] = Some([1470.5, 134.0, 64.0, 28.0]);
        }
        let telemetry = Telemetry {
            panel: Some(panel),
            ..telemetry_with(&[])
        };
        let text = render_telemetry(&telemetry);
        let rest = json_value(text.as_bytes()).unwrap_or_else(|error| panic!("{error}\n{text}"));
        assert!(
            rest.trim_ascii().is_empty(),
            "trailing text after the object"
        );
        for needle in [
            "\"panel\": {\n    \"draws\": 42, \"present\": 100, \"open\": true, \"collapsed\": \
             false, \"dock\": \"right\", \"tab\": \"layers\", \"code_view\": false, \"sub\": \
             \"parameters\"",
            "\"font_scale\": null",
            "\"request_scale\": 1, \"faces\": [",
            "\"display\": [1920, 1080]",
            "{\"name\": \"plex_sans_semibold\", \"handle\": 0, \"granted\": false}",
            "\"in_use\": true",
            "\"reference\": {\"open\": true, \"searching\": false, \"stage\": \"tonemap_post\", \
             \"query_chars\": 5, \"shown\": 3, \"total\": 286}",
            "\"reference_panel\": [1610, 300, 296, 520]",
            "\"header\": [1460, 0, 460, 33]",
            "\"nav_active\": [1470.5, 134, 64, 28]",
            "\"rects_refused\": 0",
        ] {
            assert!(text.contains(needle), "missing {needle}\n{text}");
        }
        // A frame with no parts drawn, and no frame at all, are both valid JSON.
        let empty = Telemetry {
            panel: Some(FrameRecord::default()),
            ..telemetry_with(&[])
        };
        let text = render_telemetry(&empty);
        assert!(json_value(text.as_bytes()).is_ok(), "{text}");
        assert!(text.contains("\"rects\": {}"));
        let text = render_telemetry(&telemetry_with(&[]));
        assert!(text.contains("\"panel\": null,"));
    }

    #[test]
    fn the_telemetry_is_one_json_object_with_every_region() {
        let samples = vec![
            Sample {
                index: 0,
                pre: [0.25, 0.5, 1.0],
                post: [0.75, 0.5, 0.0],
                aux: 0x3f80_0000,
            },
            Sample {
                index: 9,
                pre: [f32::NAN, f32::INFINITY, -0.0],
                post: [1e-40, 3.4e38, -2.5],
                aux: 0,
            },
        ];
        let regions = vec![
            region(Region::Ao, 97, 1 << 24, samples),
            region(Region::Tonemap, 96, 0x7f00_0001, Vec::new()),
        ];
        let text = render_telemetry(&telemetry_with(&regions));
        let rest = json_value(text.as_bytes()).unwrap_or_else(|error| panic!("{error}\n{text}"));
        assert!(
            rest.trim_ascii().is_empty(),
            "trailing text after the object"
        );
        for needle in [
            "\"mode_name\": \"fidelity\"",
            "\"ports_ready\": [1, 1, 1, 0, 1]",
            "\"probe_frame\": [97, 0, 96]",
            "\"create_ms_last\": null",
            "\"compile_ms_last\": null",
            "\"faulted_detours\": [\"Reset\", \"Close\"]",
            "\"detour\": \"SetPipelineState\", \"ns_sum\": 5000, \"samples\": 100, \"mean_ns\": 30, \
             \"preempted\": 2",
            "\"detour\": \"Reset\", \"ns_sum\": 0, \"samples\": 0, \"mean_ns\": null",
            "\"detour\": \"CloseProbeCopy\", \"ns_sum\": 0",
            "\"hook_sample_every\": 256",
            "\"unfaithful_ports\": 0,\n    \"ports_off\": 0",
            "\"region\": \"ao\"",
            "\"kind\": 1, \"pass\": \"GXLightDeferredAOWithSsr\"",
            "\"frame\": 97, \"frame_age\": 3",
            "\"kind\": 127, \"pass\": null",
            "\"pre\": [null, null, -0]",
            "\"variants\": 4",
            "\"ladder\": {\"full\": 0",
        ] {
            assert!(text.contains(needle), "missing {needle}\n{text}");
        }
    }

    #[test]
    fn the_telemetry_is_valid_with_nothing_to_report() {
        let telemetry = Telemetry {
            counters: Vec::new(),
            ..telemetry_with(&[])
        };
        let text = render_telemetry(&telemetry);
        let rest = json_value(text.as_bytes()).unwrap_or_else(|error| panic!("{error}\n{text}"));
        assert!(rest.trim_ascii().is_empty());
        assert!(text.contains("\"regions\": [\n    ]"));
    }

    #[test]
    fn the_reader_refuses_what_json_does_not_allow() {
        for bad in [
            "{\"a\": NaN}",
            "{\"a\": inf}",
            "[1, ]",
            "{\"a\" 1}",
            "[1 2]",
            "{",
        ] {
            assert!(json_value(bad.as_bytes()).is_err(), "{bad}");
        }
        assert!(json_value(b"{\"a\": [1, -2.5e3, true, null], \"b\": {}}").is_ok());
    }
}
