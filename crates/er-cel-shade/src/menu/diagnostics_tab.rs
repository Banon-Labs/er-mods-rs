//! The Diagnostics tab (design 9), laid out as the canvas's Diagnostics mockup: what the hooks
//! cost against design section 12's budgets, each port kind's variants, its GPU time a frame and
//! how fresh its probe region is, the root signatures by how much was appended to them, what the
//! redirects and the graveyard hold over the session, what the panel counted, and the compiler in
//! a footer pinned to the bottom.
//!
//! The GPU column is the mockup's addition for design 12's port budget: each kind's mean GPU
//! microseconds a frame over the last frames ([`crate::gpu_time`]), amber above 1.5 ms. Its
//! tooltip has the last frame's figure and which pipeline was timed, and the line under the table
//! each stage's total, which is what adding or removing a layer moves.
//!
//! Every figure is one the hooks or the worker already keep; a figure with nothing behind it yet is
//! a quiet `-`. Measure cost comes with P1, so its button is drawn faded and cannot be pressed.
//! What the mockup's tables have no column for -- each detour's timed calls, each port's live
//! variant and verification report, the binds behind the redirects -- is in the tooltip of the row
//! it belongs to. The pinned footer, the gaps and the tooltips are the theme's, which the Look tab
//! draws with too.

use crate::cmdlist::Timing;
use crate::editor::compiler::IN_PROCESS_WARNING;
use crate::editor::worker::percentile;
use crate::gpu_time::{self, Bound, Counters, Report, Source};
use crate::look_binding::Level;
use crate::replace::{PortKind, Stage};
use crate::settings::render_ports;

/// A probe region whose header was written within this many presents counts as live.
pub const PROBE_LIVE_PRESENTS: u32 = 10;

/// Design section 12's budget for what one timed call adds, in nanoseconds: the budgets
/// `scripts/check-shader-editor-probe.py --expect cost` judges a run by.
#[must_use]
pub const fn budget_ns(timing: Timing) -> u64 {
    match timing {
        Timing::SetPipelineState => 20,
        Timing::Reset | Timing::ClearState | Timing::Close => 40,
        Timing::CloseProbeCopy => 2_000,
    }
}

/// Design section 12's budget for every hook together, per frame, in nanoseconds.
pub const FRAME_BUDGET_NS: u64 = 200_000;

/// A timing with fewer timed calls than this is shown and not judged, as the probe checker does.
pub const MIN_JUDGED_SAMPLES: u64 = 16;

/// The longest probe age the ports table writes out; older reads `9999+ frames`.
pub const MAX_AGE_SHOWN: u32 = 9_999;

/// The compiler as the toolbar's chip says it: "dxc 1.9 helper, signed", or what is wrong, from
/// the worker's description of its choice (`editor::compiler::Choice`'s `Display`).
#[must_use]
pub fn compiler_chip(description: &str) -> String {
    if let Some(helper) = description.strip_prefix("compiler: dxc ") {
        let version = helper.split_whitespace().next().unwrap_or("?");
        return format!("dxc {version} helper, signed");
    }
    if description.starts_with(IN_PROCESS_WARNING) {
        return "compiler in-process".to_owned();
    }
    if description.starts_with("compiler not started") {
        return "compiler not started yet".to_owned();
    }
    if description.starts_with("compiler helper down") {
        return "compiler helper down".to_owned();
    }
    if description.starts_with("no compiler") {
        return "no compiler".to_owned();
    }
    if description.is_empty() {
        return "editor not running".to_owned();
    }
    description.chars().take(40).collect()
}

/// How a line of the compiler's footer reads.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Plain,
    /// Amber: the compiler runs in the game's process, where a crash in it ends the game.
    Warning,
    /// Red: there is nothing to compile with.
    Error,
}

/// The compiler's footer, from the worker's description of its choice.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CompilerLines {
    /// The mockup's first line: "helper process, dxc 1.9, signs its own output".
    pub summary: String,
    /// The `dxcompiler.dll` in use, the game's directory written `<Game>` as the mockup writes it.
    pub path: Option<String>,
    pub tone: Tone,
}

/// The text of `description` from just after `start` up to `end`, or to its end without one.
fn between<'a>(description: &'a str, start: &str, end: &str) -> Option<&'a str> {
    let rest = &description[description.find(start)? + start.len()..];
    Some(rest.find(end).map_or(rest, |at| &rest[..at]))
}

/// The compiler's footer for `description`, `editor::compiler::Choice` as the worker displays it,
/// with paths under `game_dir` written from `<Game>`.
#[must_use]
pub fn compiler_lines(description: &str, game_dir: Option<&str>) -> CompilerLines {
    let path = |start: &str, end: &str| {
        between(description, start, end)
            .filter(|path| !path.is_empty())
            .map(|path| with_game_prefix(path, game_dir))
    };
    if let Some(helper) = description.strip_prefix("compiler: dxc ") {
        let version = helper.split_whitespace().next().unwrap_or("?");
        return CompilerLines {
            summary: format!("helper process, dxc {version}, signs its own output"),
            path: path(", compiler ", ", pid "),
            tone: Tone::Plain,
        };
    }
    if description.starts_with(IN_PROCESS_WARNING) {
        let version = between(description, "(dxc ", " ")
            .filter(|version| !version.is_empty())
            .unwrap_or("?");
        return CompilerLines {
            summary: format!(
                "in the game's process, dxc {version}: a compiler crash ends the game"
            ),
            path: path("in the game's process (", "):"),
            tone: Tone::Warning,
        };
    }
    let (summary, tone) = if let Some(rest) = description.strip_prefix("compiler not started: ") {
        (format!("not started: {rest}"), Tone::Plain)
    } else if let Some(rest) = description.strip_prefix("compiler ") {
        (rest.to_owned(), Tone::Error)
    } else if description.starts_with("no compiler") {
        (description.to_owned(), Tone::Error)
    } else if description.is_empty() {
        ("editor not running".to_owned(), Tone::Plain)
    } else {
        (description.to_owned(), Tone::Plain)
    };
    CompilerLines {
        summary,
        path: None,
        tone,
    }
}

/// `path` with `game_dir` at its start written `<Game>`, as the mockups write paths; unchanged
/// when it is not under it. Windows paths, so the comparison ignores case.
#[must_use]
pub fn with_game_prefix(path: &str, game_dir: Option<&str>) -> String {
    let Some(dir) = game_dir
        .map(|dir| dir.trim_end_matches(['\\', '/']))
        .filter(|dir| !dir.is_empty())
    else {
        return path.to_owned();
    };
    match (path.get(..dir.len()), path.get(dir.len()..)) {
        (Some(head), Some(rest))
            if head.eq_ignore_ascii_case(dir) && rest.starts_with(['\\', '/']) =>
        {
            format!("<Game>{rest}")
        }
        _ => path.to_owned(),
    }
}

/// Milliseconds the hooks add to a frame, averaged over the session: each detour's sampled
/// calls, less the timer floor, scaled by `sample_every` to every call, over `presents`. `None`
/// before any call was timed or any present counted. `timings` is each detour's
/// `(nanoseconds summed, samples)`.
#[must_use]
pub fn hook_ms_per_frame(
    timings: &[(u64, u64)],
    floor_ns: u64,
    sample_every: u32,
    presents: u64,
) -> Option<f64> {
    if presents == 0 || timings.iter().all(|(_, samples)| *samples == 0) {
        return None;
    }
    let total_ns: f64 = timings
        .iter()
        .filter(|(_, samples)| *samples != 0)
        .map(|&(sum, samples)| {
            let mean = (sum / samples).saturating_sub(floor_ns);
            mean as f64 * samples as f64 * f64::from(sample_every)
        })
        .sum();
    Some(total_ns / presents as f64 / 1.0e6)
}

/// The median, the 95th percentile and the largest of `values`, or `None` for none.
#[must_use]
pub fn spread(values: &[f32]) -> Option<(f32, f32, f32)> {
    let p50 = percentile(values, 50.0)?;
    let p95 = percentile(values, 95.0)?;
    let max = percentile(values, 100.0)?;
    Some((p50, p95, max))
}

/// [`spread`] as one line: `p50 21.0 ms, p95 48.0 ms, max 60.0 ms (12 runs)`.
#[must_use]
pub fn spread_text(values: &[f32]) -> String {
    match spread(values) {
        Some((p50, p95, max)) => format!(
            "p50 {p50:.1} ms, p95 {p95:.1} ms, max {max:.1} ms ({} {})",
            values.len(),
            if values.len() == 1 { "run" } else { "runs" }
        ),
        None => "none yet".to_owned(),
    }
}

/// The least and the most of `values`, milliseconds, as the Lifetime grid writes variant creation:
/// `1.2 to 13.1 ms`, or one figure when they are the same. `None` for none.
#[must_use]
pub fn range_text(values: &[f32]) -> Option<String> {
    let finite = || values.iter().copied().filter(|value| value.is_finite());
    let least = finite().reduce(f32::min)?;
    let most = finite().reduce(f32::max)?;
    let (least, most) = (format!("{least:.1}"), format!("{most:.1}"));
    Some(if least == most {
        format!("{least} ms")
    } else {
        format!("{least} to {most} ms")
    })
}

/// The median and the 95th percentile of `values`, milliseconds: `21.0 / 48.0 ms`. `None` for none.
#[must_use]
pub fn p50_p95_text(values: &[f32]) -> Option<String> {
    let (p50, p95, _) = spread(values)?;
    Some(format!("{p50:.1} / {p95:.1} ms"))
}

/// The micro sign and an s: U+00B5, which the host's glyph ranges hold, and not the Greek mu,
/// U+03BC, which they do not.
const MICROSECONDS: &str = "\u{b5}s";

/// `value` to one decimal place, without a trailing `.0`, then `unit`.
fn trimmed(value: f64, unit: &str) -> String {
    let figure = format!("{value:.1}");
    let figure = figure.strip_suffix(".0").unwrap_or(&figure);
    format!("{figure} {unit}")
}

/// `ns` nanoseconds as the tables write a duration: `141 ns`, `2.5 us`, `77 us`, `1.25 ms`, with
/// the micro sign ([`MICROSECONDS`]) where these comments write a `u`.
#[must_use]
pub fn duration_text(ns: f64) -> String {
    if !ns.is_finite() || ns < 0.0 {
        "-".to_owned()
    } else if ns < 999.5 {
        format!("{ns:.0} ns")
    } else if ns < 999_950.0 {
        trimmed(ns / 1.0e3, MICROSECONDS)
    } else {
        format!("{:.2} ms", ns / 1.0e6)
    }
}

/// A timing's name in the cost table: the telemetry's name, with the copying `Close` spelled out.
#[must_use]
pub const fn timing_label(timing: Timing) -> &'static str {
    match timing {
        Timing::CloseProbeCopy => "Close with probe copy",
        _ => timing.name(),
    }
}

/// A cost against its budget.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// Nothing timed yet.
    Untimed,
    /// Timed, but on fewer than [`MIN_JUDGED_SAMPLES`] calls.
    Unjudged,
    Within,
    Over,
}

/// `mean_ns` over `samples` timed calls against `budget_ns`.
#[must_use]
pub fn verdict(mean_ns: Option<u64>, samples: u64, budget_ns: u64) -> Verdict {
    match mean_ns {
        None => Verdict::Untimed,
        Some(_) if samples < MIN_JUDGED_SAMPLES => Verdict::Unjudged,
        Some(mean) if mean <= budget_ns => Verdict::Within,
        Some(_) => Verdict::Over,
    }
}

/// The article before `n` read aloud: an 8, an 11, an 18, an 82, an 800, an 11000; a 20, a 110.
#[must_use]
pub fn article(n: u64) -> &'static str {
    let digits = n.to_string();
    let lead = match digits.len() % 3 {
        0 => 3,
        lead => lead,
    };
    let group = &digits[..lead];
    if group.starts_with('8') || group == "11" || group == "18" {
        "an"
    } else {
        "a"
    }
}

/// The cost section's heading, before it is set in capitals: "Hook cost, 1 call in 256, less an
/// 82 ns timer floor".
#[must_use]
pub fn hook_cost_heading(sample_every: u32, floor_ns: u64) -> String {
    format!(
        "Hook cost, 1 call in {sample_every}, less {} {floor_ns} ns timer floor",
        article(floor_ns)
    )
}

/// How fresh a port kind's probe region is, as the ports table's last column says.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProbeAge {
    /// The kind's stage has no content, so none of its ports are redirected.
    StageOff,
    /// The kind is bound as the game asked: marked unfaithful, or outside a fidelity test.
    PortOff,
    /// The kind wrote its region this many presents ago.
    Frames(u32),
    /// The region is unwritten, or the other kind that shares it wrote it last.
    NotDrawn,
}

impl ProbeAge {
    /// Its words in the table: `3 frames`, `not drawn`, `stage off`, `port off`.
    #[must_use]
    pub fn text(self) -> String {
        match self {
            Self::StageOff => "stage off".to_owned(),
            Self::PortOff => "port off".to_owned(),
            Self::Frames(1) => "1 frame".to_owned(),
            Self::Frames(age) if age > MAX_AGE_SHOWN => format!("{MAX_AGE_SHOWN}+ frames"),
            Self::Frames(age) => format!("{age} frames"),
            Self::NotDrawn => "not drawn".to_owned(),
        }
    }

    /// Written within [`PROBE_LIVE_PRESENTS`]: drawn in full text, the rest quiet.
    #[must_use]
    pub const fn is_live(self) -> bool {
        matches!(self, Self::Frames(age) if age <= PROBE_LIVE_PRESENTS)
    }
}

/// How fresh `kind`'s probe is, with `stages` the stages that have content (`Stage::bit`s),
/// `ports_off` the kinds bound as the game asked (`PortKind::bit`s), and `written` its region's
/// header: the kind its variant id names, and how many presents ago it was written.
#[must_use]
pub fn probe_age(
    kind: PortKind,
    stages: u32,
    ports_off: u8,
    written: Option<(Option<PortKind>, u32)>,
) -> ProbeAge {
    if stages & kind.stage().bit() == 0 {
        return ProbeAge::StageOff;
    }
    if ports_off & kind.bit() != 0 {
        return ProbeAge::PortOff;
    }
    match written {
        Some((Some(writer), age)) if writer == kind => ProbeAge::Frames(age),
        _ => ProbeAge::NotDrawn,
    }
}

/// The line under the ports table: `11 variants, 0 refused, 0 left as the game drew them, none
/// marked unfaithful`.
#[must_use]
pub fn ports_summary(variants: usize, refused: usize, left: usize, unfaithful: u8) -> String {
    let marked = if unfaithful == 0 {
        "none marked unfaithful".to_owned()
    } else {
        format!("{} marked unfaithful", render_ports(unfaithful))
    };
    format!(
        "{variants} {}, {refused} refused, {left} left as the game drew them, {marked}",
        if variants == 1 { "variant" } else { "variants" }
    )
}

/// A GPU figure in microseconds as the tables write a duration, or `-` while unknown.
#[must_use]
pub fn gpu_text(us: Option<f64>) -> String {
    us.map_or_else(|| "-".to_owned(), |us| duration_text(us * 1.0e3))
}

/// Is a port's GPU time a frame over design section 12's budget: drawn amber.
#[must_use]
pub fn gpu_over_budget(us: Option<f64>) -> bool {
    us.is_some_and(|us| us > gpu_time::PORT_BUDGET_US)
}

/// Which pipelines a kind's spans timed, in words.
#[must_use]
pub const fn bound_words(bound: Bound) -> &'static str {
    match bound {
        Bound::None => "no pipeline",
        Bound::Variant => "its cel variant",
        Bound::Game => "the game's own pipeline",
        Bound::Both => "the variant and the game's own pipeline",
    }
}

/// A port row's GPU tooltip line: `GPU: 412.5 us last frame, 2 spans of its cel variant; mean
/// 405 us over 32 frames, budget 1.50 ms.`
#[must_use]
pub fn gpu_tip(report: Option<&Report>, kind: PortKind) -> String {
    let Some(report) = report else {
        return "GPU: not timed, the timer is off this session".to_owned();
    };
    let spans = report.spans_last(kind);
    let last = match spans {
        0 => "not bound last frame".to_owned(),
        _ => format!(
            "{} last frame, {spans} {} of {}",
            gpu_text(report.last_us(kind)),
            if spans == 1 { "span" } else { "spans" },
            bound_words(report.bound_last(kind))
        ),
    };
    format!(
        "GPU: {last}; mean {} over {} {}, budget {}.",
        gpu_text(report.mean_us(kind)),
        report.history,
        if report.history == 1 {
            "frame"
        } else {
            "frames"
        },
        gpu_text(Some(gpu_time::PORT_BUDGET_US))
    )
}

/// The line under the ports table: each stage's mean GPU time a frame, `GPU a frame: lighting 412
/// us, albedo 120 us, tonemap 330 us`.
#[must_use]
pub fn gpu_stages_line(report: Option<&Report>) -> String {
    let Some(report) = report else {
        return "GPU a frame: not timed this session".to_owned();
    };
    let stages: Vec<String> = Stage::ALL
        .into_iter()
        .map(|stage| format!("{} {}", stage.name(), gpu_text(report.stage_mean_us(stage))))
        .collect();
    format!("GPU a frame: {}", stages.join(", "))
}

/// The line under the stages' GPU line for the vertex stage, which has no port: its chip
/// (`crate::vertex::chip`), and that no GPU figure applies to it.
#[must_use]
pub fn vertex_stage_line(chip: &str) -> String {
    format!("Vertex stage: {chip}; GPU not timed")
}

/// The vertex stage's line, under the mouse.
pub const VERTEX_STAGE_TIP: &str = "Vertex layers run inside the game's own mesh vertex shaders, \
                                    in every pass that draws a mesh. No port of ours is bound for \
                                    them, so no span times them, and the stage has no GPU figure \
                                    rather than a made-up one: its cost is inside the game's own \
                                    passes, which are not timed.";

/// A tick rate as the tooltip says it: `1000 MHz`, or in hertz when not a whole number of them.
#[must_use]
pub fn rate_text(frequency: u64) -> String {
    if frequency == 0 {
        "unknown".to_owned()
    } else if frequency.is_multiple_of(1_000_000) {
        format!("{} MHz", frequency / 1_000_000)
    } else {
        format!("{frequency} Hz")
    }
}

/// Where the rate came from, in words.
#[must_use]
pub const fn source_words(source: Source) -> &'static str {
    match source {
        Source::Unknown => "no queue yet",
        Source::GameQueue => "the game's direct queue",
        Source::OwnQueue => "a direct queue of ours on the game's device",
    }
}

/// The tooltip of the stages' GPU line: what the figures are, the rate, and what was lost.
#[must_use]
pub fn gpu_stage_tip(report: Option<&Report>, counters: Counters) -> Vec<String> {
    let mut lines = vec![
        "A port's GPU time is from its pipeline's bind to the next bind on the same command list: \
         its draws, and none of the work before them. A frame adds up every bind; the figures are \
         the mean of the last frames, and a stage is its ports together."
            .to_owned(),
    ];
    match report {
        Some(report) => {
            lines.push(format!(
                "Ticks at {} from {}, read {} presents behind with no fence wait.",
                rate_text(report.frequency),
                source_words(report.source),
                gpu_time::READ_LAG_PRESENTS
            ));
            lines.push(format!(
                "Spans: {} begun, {} late, {} refused, {} dropped; {} resolves.",
                counters.begun, report.late, report.refused, counters.dropped, counters.resolves
            ));
        }
        None => lines.push("The timer is off this session; er-cel-shade.log says why.".to_owned()),
    }
    lines
}

/// The root signatures by level, most first, as the mockup sets them: `full 4`, `cbv+probe 0`,
/// `cbv 0`, `untouched 5`; true for the last, which is drawn quiet. `counts` is indexed by
/// `Level as usize`.
#[must_use]
pub fn ladder_figures(counts: [usize; 4]) -> [(String, bool); 4] {
    Level::LADDER.map(|level| {
        let name = match level {
            Level::Full => "full",
            Level::CbvProbe => "cbv+probe",
            Level::Cbv => "cbv",
            Level::None => "untouched",
        };
        (
            format!("{name} {}", counts[level as usize]),
            level == Level::None,
        )
    })
}

/// The widths of a table's columns `available` wide with `gap` between them: each fixed width as
/// it is, and what they leave shared among the others (`None`), as CSS's `minmax(0, 1fr)` does.
#[must_use]
pub fn column_widths(fixed: &[Option<f32>], available: f32, gap: f32) -> Vec<f32> {
    let taken: f32 =
        fixed.iter().flatten().sum::<f32>() + gap * fixed.len().saturating_sub(1) as f32;
    let fills = fixed.iter().filter(|width| width.is_none()).count().max(1) as f32;
    let share = ((available - taken) / fills).max(0.0);
    fixed.iter().map(|width| width.unwrap_or(share)).collect()
}

#[cfg(windows)]
pub(super) use process::draw;

/// Drawing the tab.
#[cfg(windows)]
mod process {
    use std::sync::LazyLock;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

    use hudhook::imgui::Ui;

    use super::super::theme::{
        self, ButtonKind, ButtonSize, Font, SECTION_GAP, footer, gap, tooltip,
    };
    use super::super::{
        CODE_SAVES, COMPLETIONS_ACCEPTED, EDIT_CALLBACKS, PARAM_EDITS, PASTES, PROJECT_SAVES,
        STACK_EDITS, UI_PANICS, UI_US_LAST, UI_US_MAX,
    };
    use super::{
        FRAME_BUDGET_NS, Tone, VERTEX_STAGE_TIP, Verdict, budget_ns, column_widths, compiler_lines,
        duration_text, gpu_over_budget, gpu_stage_tip, gpu_stages_line, gpu_text, gpu_tip,
        hook_cost_heading, hook_ms_per_frame, ladder_figures, p50_p95_text, ports_summary,
        probe_age, range_text, spread_text, timing_label, verdict, vertex_stage_line,
    };
    use crate::cmdlist::{self, SAMPLE_EVERY, Timing};
    use crate::editor::worker::{SERIAL_MASK, Status, region_of};
    use crate::exports::{self, mean_ns, variant_kind};
    use crate::gpu_time::{self, Counters};
    use crate::probe::{self, RegionRecord};
    use crate::replace::PortKind;
    use crate::{graveyard, hooks, look_binding, params, safety, table};

    /// The mockup's gaps at 1080p, beside the theme's 14 between sections: 6 from a heading to
    /// what it heads, 4 between a table's rows and 10 between its columns, 14 between the root
    /// signature figures, 8 between a button and its note.
    const HEADING_GAP: f32 = 6.0;
    const TABLE_ROW_GAP: f32 = 4.0;
    const TABLE_COLUMN_GAP: f32 = 10.0;
    const FIGURE_GAP: f32 = 14.0;
    const BUTTON_NOTE_GAP: f32 = 8.0;

    /// The game's directory as a string, for writing paths from `<Game>`.
    static GAME_DIR: LazyLock<Option<String>> = LazyLock::new(|| {
        er_game_base::log::game_directory_path().and_then(|dir| dir.to_str().map(str::to_owned))
    });

    /// A table column: a width at 1080p, or `None` to share what the fixed ones leave; and
    /// whether its cells are right-aligned.
    #[derive(Clone, Copy)]
    struct Column {
        width: Option<f32>,
        right: bool,
    }

    const FILL: Column = Column {
        width: None,
        right: false,
    };

    const fn right(width: f32) -> Column {
        Column {
            width: Some(width),
            right: true,
        }
    }

    /// The cost table: Detour, Per call, Budget. The ports table: Kind, Variants, GPU, Probe age.
    /// The Lifetime and Panel grids: two equal columns.
    const COST_COLUMNS: [Column; 3] = [FILL, right(70.0), right(70.0)];
    const PORT_COLUMNS: [Column; 4] = [FILL, right(60.0), right(70.0), right(90.0)];
    const PAIR_COLUMNS: [Column; 2] = [FILL, FILL];
    const SINGLE_COLUMN: [Column; 1] = [FILL];

    /// One cell: pieces of words drawn one after another, in one font.
    #[derive(Clone)]
    struct Cell {
        pieces: Vec<(String, [f32; 4])>,
        font: Font,
    }

    impl Cell {
        fn new(font: Font, words: impl Into<String>, colour: [f32; 4]) -> Self {
            Self {
                pieces: vec![(words.into(), colour)],
                font,
            }
        }

        /// A 12-pixel mono value, as the cost and ports tables set theirs.
        fn mono(words: impl Into<String>, colour: [f32; 4]) -> Self {
            Self::new(Font::Mono, words, colour)
        }

        /// A table's quiet 12-pixel heading.
        fn heading(words: &str) -> Self {
            Self::new(Font::Label, words, theme::TEXT_QUIET)
        }

        fn then(mut self, words: impl Into<String>, colour: [f32; 4]) -> Self {
            self.pieces.push((words.into(), colour));
            self
        }

        fn width(&self, ui: &Ui) -> f32 {
            theme::with_font(ui, self.font, || {
                self.pieces
                    .iter()
                    .map(|(words, _)| ui.calc_text_size(words)[0])
                    .sum()
            })
        }

        /// Draw it with its top-left at `at`.
        fn draw(&self, ui: &Ui, at: [f32; 2]) {
            let paint = theme::painter(ui);
            theme::with_font(ui, self.font, || {
                let mut x = at[0];
                for (words, colour) in &self.pieces {
                    paint.text([x, at[1]], *colour, words);
                    x += ui.calc_text_size(words)[0];
                }
            });
        }
    }

    /// A label and its value in a two-column grid, and what its tooltip adds.
    struct Entry {
        cell: Cell,
        tip: Vec<String>,
    }

    impl Entry {
        /// `label` in full text, then `value`, or a quiet `-` when there is none.
        fn new(label: &str, value: Option<String>) -> Self {
            let cell = Cell::new(Font::Label, label, theme::TEXT);
            let cell = match value {
                Some(value) => cell.then(value, theme::TEXT),
                None => cell.then("-", theme::TEXT_QUIET),
            };
            Self {
                cell,
                tip: Vec::new(),
            }
        }

        fn tip(mut self, lines: Vec<String>) -> Self {
            self.tip = lines;
            self
        }
    }

    fn rounded(point: [f32; 2]) -> [f32; 2] {
        [point[0].round(), point[1].round()]
    }

    /// A section heading and the gap under it.
    fn heading(ui: &Ui, words: &str) {
        theme::section_heading(ui, words);
        gap(ui, HEADING_GAP);
    }

    /// `rows` of cells at the cursor across the content, as the mockup's CSS grids set them: rows
    /// [`TABLE_ROW_GAP`] apart, each as tall as its tallest font with its cells centred in it,
    /// columns [`TABLE_COLUMN_GAP`] apart. Each row is one item. Returns the row and the column
    /// under the mouse.
    fn table(ui: &Ui, columns: &[Column], rows: &[Vec<Cell>]) -> Option<(usize, usize)> {
        let t = theme::current();
        let at = rounded(ui.cursor_screen_pos());
        let width = ui.content_region_avail()[0].max(1.0);
        let gap = t.px(TABLE_COLUMN_GAP).round();
        let fixed: Vec<Option<f32>> = columns
            .iter()
            .map(|column| column.width.map(|width| t.px(width).round()))
            .collect();
        let widths = column_widths(&fixed, width, gap);
        let mut lefts = Vec::with_capacity(widths.len());
        let mut x = at[0];
        for width in &widths {
            lefts.push(x);
            x += width + gap;
        }
        let mouse = ui.io().mouse_pos;
        let mut y = at[1];
        let mut hovered = None;
        for (index, row) in rows.iter().enumerate() {
            if index > 0 {
                y += t.px(TABLE_ROW_GAP).round();
            }
            let height = row
                .iter()
                .map(|cell| theme::line_height(ui, cell.font))
                .fold(1.0_f32, f32::max);
            for (cell, (column, (left, width))) in row
                .iter()
                .zip(columns.iter().zip(lefts.iter().zip(&widths)))
            {
                let line = theme::line_height(ui, cell.font);
                let x = if column.right {
                    left + width - cell.width(ui)
                } else {
                    *left
                };
                cell.draw(ui, [x, y + (height - line) * 0.5]);
            }
            ui.set_cursor_screen_pos([at[0], y]);
            ui.dummy([width, height]);
            if ui.is_item_hovered() {
                hovered = lefts
                    .iter()
                    .zip(&widths)
                    .position(|(left, width)| mouse[0] >= *left && mouse[0] < left + width)
                    .map(|column| (index, column));
            }
            y += height;
        }
        hovered
    }

    /// `entries` in a grid of two equal columns, as the Lifetime mockup sets them, or one column
    /// when an entry is wider than half the content. Shows the tooltip of the entry under the
    /// mouse.
    fn pairs(ui: &Ui, entries: &[Entry]) {
        let t = theme::current();
        let gap = t.px(TABLE_COLUMN_GAP).round();
        let half = (ui.content_region_avail()[0] - gap) * 0.5;
        let fits = entries.iter().all(|entry| entry.cell.width(ui) <= half);
        let columns: &[Column] = if fits { &PAIR_COLUMNS } else { &SINGLE_COLUMN };
        let rows: Vec<Vec<Cell>> = entries
            .chunks(columns.len())
            .map(|chunk| chunk.iter().map(|entry| entry.cell.clone()).collect())
            .collect();
        if let Some((row, column)) = table(ui, columns, &rows)
            && let Some(entry) = entries.get(row * columns.len() + column)
            && !entry.tip.is_empty()
        {
            tooltip(ui, &entry.tip);
        }
    }

    /// `cells` in a row that wraps, `gap` (1080p) apart across and down, as CSS's `flex-wrap`
    /// sets the root signature figures. One item; true while the mouse is over it.
    fn figures(ui: &Ui, cells: &[Cell], gap: f32) -> bool {
        let t = theme::current();
        let at = rounded(ui.cursor_screen_pos());
        let width = ui.content_region_avail()[0].max(1.0);
        let sizes: Vec<[f32; 2]> = cells
            .iter()
            .map(|cell| [cell.width(ui), theme::line_height(ui, cell.font)])
            .collect();
        let gap = t.px(gap).round();
        let (offsets, height) = theme::place_flow(&sizes, width, gap, gap);
        for (cell, offset) in cells.iter().zip(offsets) {
            cell.draw(ui, [at[0] + offset[0], at[1] + offset[1]]);
        }
        ui.set_cursor_screen_pos(at);
        ui.dummy([width, height.max(1.0)]);
        ui.is_item_hovered()
    }

    /// The tab, with the editor's `status` as the worker last published it.
    pub(in crate::menu) fn draw(ui: &Ui, status: &Status) {
        // Every vertical gap is the mockup's, placed by `gap`; Dear ImGui adds none.
        let _spacing = theme::spacing(ui, 0.0);
        let present = params::presents();
        draw_hook_cost(ui, present);
        gap(ui, SECTION_GAP);
        draw_ports(ui, status, present);
        gap(ui, SECTION_GAP);
        draw_root_signatures(ui);
        gap(ui, SECTION_GAP);
        draw_lifetime(ui, status);
        gap(ui, SECTION_GAP);
        draw_panel_counters(ui);
        footer(ui, "##compiler-footer", || draw_compiler(ui, status));
    }

    /// The colour a cost is drawn in: green within its budget, red over it.
    const fn verdict_colour(verdict: Verdict) -> [f32; 4] {
        match verdict {
            Verdict::Untimed => theme::TEXT_QUIET,
            Verdict::Unjudged => theme::TEXT,
            Verdict::Within => theme::OK_TEXT,
            Verdict::Over => theme::ERROR_TEXT,
        }
    }

    /// Hook cost: each timing's added nanoseconds a call against its budget, all of them per
    /// frame against 200 us, then Measure cost.
    fn draw_hook_cost(ui: &Ui, present: u64) {
        let relaxed = |a: &AtomicU64| a.load(Ordering::Relaxed);
        let floor = relaxed(&exports::TIMER_FLOOR_NS);
        heading(ui, &hook_cost_heading(SAMPLE_EVERY, floor));
        let timings: Vec<(u64, u64)> = Timing::ALL
            .into_iter()
            .map(|timing| {
                (
                    relaxed(&cmdlist::HOOK_NS_SUM[timing as usize]),
                    relaxed(&cmdlist::HOOK_NS_SAMPLES[timing as usize]),
                )
            })
            .collect();
        let quiet = theme::TEXT_QUIET;
        let mut rows = vec![vec![
            Cell::heading("Detour"),
            Cell::heading("Per call"),
            Cell::heading("Budget"),
        ]];
        for (timing, &(sum, samples)) in Timing::ALL.into_iter().zip(&timings) {
            let mean = mean_ns(sum, samples, floor);
            let judged = verdict(mean, samples, budget_ns(timing));
            rows.push(vec![
                Cell::mono(timing_label(timing), theme::TEXT),
                Cell::mono(
                    mean.map_or_else(|| "-".to_owned(), |ns| duration_text(ns as f64)),
                    verdict_colour(judged),
                ),
                Cell::mono(duration_text(budget_ns(timing) as f64), quiet),
            ]);
        }
        let per_frame = hook_ms_per_frame(&timings, floor, SAMPLE_EVERY, present);
        let samples: u64 = timings.iter().map(|(_, samples)| samples).sum();
        let per_frame_ns = per_frame.map(|ms| ms * 1.0e6);
        let judged = verdict(
            per_frame_ns.map(|ns| ns.round() as u64),
            samples,
            FRAME_BUDGET_NS,
        );
        rows.push(vec![
            Cell::mono("All hooks, per frame", theme::TEXT),
            Cell::mono(
                per_frame_ns.map_or_else(|| "-".to_owned(), duration_text),
                verdict_colour(judged),
            ),
            Cell::mono(duration_text(FRAME_BUDGET_NS as f64), quiet),
        ]);
        let hovered = table(ui, &COST_COLUMNS, &rows).map(|(row, _)| row);
        match hovered.and_then(|row| row.checked_sub(1)) {
            Some(index) if index < Timing::COUNT => {
                let timing = Timing::ALL[index];
                let (_, samples) = timings[index];
                let preempted = relaxed(&cmdlist::HOOK_PREEMPTED[index]);
                let mut lines = vec![
                    format!(
                        "{samples} calls timed, {preempted} preempted and left out. Budget {} a \
                         call (design section 12); fewer than {} timed calls are not judged.",
                        duration_text(budget_ns(timing) as f64),
                        super::MIN_JUDGED_SAMPLES
                    ),
                    format!("Telemetry name: {}", timing.name()),
                ];
                if timing == Timing::CloseProbeCopy {
                    lines.push(
                        "Also a Close that resolves the GPU-time queries of a list that bound a \
                         port."
                            .to_owned(),
                    );
                }
                tooltip(ui, &lines);
            }
            Some(_) => tooltip(
                ui,
                &[
                    format!(
                        "Every timed call counted for the {SAMPLE_EVERY} it stands for, averaged \
                         over the session's presents."
                    ),
                    format!(
                        "Pipelines passed to Reset, ClearState, CreateCommandList: {}, {}, {}",
                        relaxed(&cmdlist::RESET_WITH_PSO),
                        relaxed(&cmdlist::CLEAR_STATE_WITH_PSO),
                        relaxed(&cmdlist::CREATE_LIST_WITH_PSO)
                    ),
                ],
            ),
            None => {}
        }
        gap(ui, HEADING_GAP);
        draw_measure_cost(ui);
    }

    /// Measure cost and its note. P1 brings it, so it is drawn faded and cannot be pressed
    /// ([`theme::unavailable_button`]).
    fn draw_measure_cost(ui: &Ui) {
        let t = theme::current();
        let label = "Measure cost##p1";
        let note = "alternates vanilla and modified every 60 frames; comes with P1";
        let size = ButtonSize::ACTION;
        let button = [
            theme::button_width(ui, label, ButtonKind::Primary, size),
            t.px(size.height).round(),
        ];
        let words = [
            theme::text_width(ui, Font::Label, note),
            theme::line_height(ui, Font::Label),
        ];
        let at = rounded(ui.cursor_screen_pos());
        let width = ui.content_region_avail()[0].max(1.0);
        let gap = t.px(BUTTON_NOTE_GAP).round();
        let (offsets, height) = theme::place_flow(&[button, words], width, gap, gap);
        ui.set_cursor_screen_pos(rounded([at[0] + offsets[0][0], at[1] + offsets[0][1]]));
        let hovered = theme::unavailable_button(ui, label, ButtonKind::Primary, size);
        theme::register_item(ui, "measure_cost_button");
        ui.set_cursor_screen_pos(rounded([at[0] + offsets[1][0], at[1] + offsets[1][1]]));
        theme::quiet(ui, note);
        ui.set_cursor_screen_pos(at);
        ui.dummy([width, height.max(1.0)]);
        if hovered {
            tooltip(
                ui,
                &[
                    "Measure cost comes with P1: it will alternate vanilla and modified every 60 \
                     frames and report the mean change in frame time.",
                ],
            );
        }
    }

    /// The colour of a port's GPU figure: amber over its budget, quiet while unknown.
    fn gpu_colour(us: Option<f64>) -> [f32; 4] {
        if gpu_over_budget(us) {
            theme::WARNING_TEXT
        } else if us.is_some() {
            theme::TEXT
        } else {
            theme::TEXT_QUIET
        }
    }

    /// Ports: each kind's variants, its GPU time a frame and how fresh its probe region is, then
    /// the session's totals and each stage's GPU time.
    fn draw_ports(ui: &Ui, status: &Status, present: u64) {
        heading(ui, "Ports");
        let stages = cmdlist::STAGE_ACTIVE.load(Ordering::Relaxed);
        let ports_off = cmdlist::PORTS_OFF.load(Ordering::Relaxed) as u8;
        let regions = probe::read_latest(present);
        let gpu = gpu_time::report();
        let record_of = |kind: PortKind| {
            regions
                .iter()
                .find(|record| record.region == region_of(kind))
        };
        let quiet = theme::TEXT_QUIET;
        let mut rows = vec![vec![
            Cell::heading("Kind"),
            Cell::heading("Variants"),
            Cell::heading("GPU"),
            Cell::heading("Probe age"),
        ]];
        for kind in PortKind::ALL {
            let written = record_of(kind).map(|record| {
                (
                    variant_kind(record.header.variant_id),
                    probe::frame_age(present, record.header.frame),
                )
            });
            let age = probe_age(kind, stages, ports_off, written);
            let gpu_us = gpu.and_then(|report| report.mean_us(kind));
            rows.push(vec![
                Cell::mono(kind.key(), theme::TEXT),
                Cell::mono(
                    hooks::PORTS_READY[kind as usize]
                        .load(Ordering::Relaxed)
                        .to_string(),
                    theme::TEXT,
                ),
                Cell::mono(gpu_text(gpu_us), gpu_colour(gpu_us)),
                Cell::mono(age.text(), if age.is_live() { theme::TEXT } else { quiet }),
            ]);
        }
        let hovered = table(ui, &PORT_COLUMNS, &rows).map(|(row, _)| row);
        if let Some(kind) = hovered
            .and_then(|row| row.checked_sub(1))
            .and_then(|index| PortKind::ALL.get(index).copied())
        {
            let mut lines = port_tip(kind, status, record_of(kind), present);
            lines.push(gpu_tip(gpu.as_ref(), kind));
            tooltip(ui, &lines);
        }
        gap(ui, HEADING_GAP);
        theme::text_wrapped(ui, Font::Label, quiet, &gpu_stages_line(gpu.as_ref()));
        if ui.is_item_hovered() {
            tooltip(ui, &gpu_stage_tip(gpu.as_ref(), Counters::read()));
        }
        gap(ui, HEADING_GAP);
        theme::text_wrapped(
            ui,
            Font::Label,
            quiet,
            &vertex_stage_line(&status.vertex_chip),
        );
        if ui.is_item_hovered() {
            tooltip(ui, &[VERTEX_STAGE_TIP]);
        }
        gap(ui, HEADING_GAP);
        let relaxed = |a: &AtomicUsize| a.load(Ordering::Relaxed);
        let unfaithful = safety::UNFAITHFUL.load(Ordering::Relaxed) as u8;
        theme::text_wrapped(
            ui,
            Font::Label,
            quiet,
            &ports_summary(
                relaxed(&hooks::VARIANTS),
                relaxed(&hooks::REWRITES_REFUSED),
                relaxed(&hooks::PORTS_LEFT_VANILLA),
                unfaithful,
            ),
        );
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "Variants: cel variants created. Refused: pipelines of ours the driver \
                     refused. Left as the game drew them: ports left vanilla before a variant \
                     was tried. Unfaithful: ports the fidelity test found unfaithful, bound as \
                     the game asks.",
                ],
            );
        }
    }

    /// The tooltip of `kind`'s row: its live variant, its probe region's header and its last
    /// verification report.
    fn port_tip(
        kind: PortKind,
        status: &Status,
        record: Option<&RegionRecord>,
        present: u64,
    ) -> Vec<String> {
        let live = status.live_variant[kind as usize];
        let mut lines = vec![format!(
            "{}: live variant {live:#010x}{}",
            kind.replacement().name,
            if live & SERIAL_MASK == 0 {
                ", the embedded cel port"
            } else {
                ""
            }
        )];
        let region = region_of(kind).name();
        lines.push(match record {
            Some(record) => format!(
                "Probe region {region}: {} presents old, written by {}, {}x{}, {} of {} samples \
                 current",
                probe::frame_age(present, record.header.frame),
                variant_kind(record.header.variant_id).map_or("an unknown variant", PortKind::key),
                record.header.width,
                record.header.height,
                record.samples.len(),
                probe::SAMPLES
            ),
            None => format!("Probe region {region}: not written"),
        });
        if let Some(report) = &status.reports[kind as usize] {
            lines.push(format!("Verification: {report}"));
        }
        lines
    }

    /// Root signatures: how many the game made, by what was appended to them; and what was left
    /// alone, when anything was.
    fn draw_root_signatures(ui: &Ui) {
        heading(ui, "Root signatures");
        let cells: Vec<Cell> = ladder_figures(look_binding::ladder_counts())
            .into_iter()
            .map(|(words, untouched)| {
                Cell::mono(
                    words,
                    if untouched {
                        theme::TEXT_QUIET
                    } else {
                        theme::TEXT
                    },
                )
            })
            .collect();
        let relaxed = |a: &AtomicUsize| a.load(Ordering::Relaxed);
        let no_room = relaxed(&look_binding::SKIPPED_NO_ROOM);
        let other = relaxed(&look_binding::SKIPPED_OTHER);
        let no_slot = relaxed(&look_binding::TABLE_FULL);
        let fixed = relaxed(&hooks::NO_ROOT_SIGNATURE_FIXED);
        let none_found = relaxed(&hooks::NO_ROOT_SIGNATURE_LEFT);
        if figures(ui, &cells, FIGURE_GAP) {
            tooltip(
                ui,
                &[
                    "Root signatures the game created, by what was appended: full carries the \
                     parameter block, the probe and the draw constants; cbv+probe the first two; \
                     cbv the block alone; untouched nothing."
                        .to_owned(),
                    format!(
                        "Left as the game made them: {no_room} out of room, {other} other; \
                         {no_slot} found no table slot."
                    ),
                    format!(
                        "Pipelines with no root signature given one from their shaders: {fixed} \
                         (none found: {none_found})."
                    ),
                ],
            );
        }
        if no_room + other + no_slot + none_found > 0 {
            gap(ui, HEADING_GAP);
            theme::text_wrapped(
                ui,
                Font::Label,
                theme::TEXT_QUIET,
                &format!(
                    "left as the game made them: {no_room} out of room, {other} other, {no_slot} \
                     with no table slot; {none_found} pipelines found no root signature"
                ),
            );
        }
    }

    /// Lifetime: what the redirects, the graveyard and the editor's compiles came to over the
    /// session.
    fn draw_lifetime(ui: &Ui, status: &Status) {
        heading(ui, "Lifetime");
        let relaxed = |a: &AtomicUsize| a.load(Ordering::Relaxed);
        let relaxed64 = |a: &AtomicU64| a.load(Ordering::Relaxed);
        let published = table::published();
        let (compile_last, create_last) = exports::last_ms();
        let graveyard_len = relaxed(&graveyard::GRAVEYARD_LEN);
        let leaked = relaxed64(&graveyard::LEAKED);
        let entries = [
            Entry::new(
                "Redirects live: ",
                Some(published.map_or(0, |t| t.redirects()).to_string()),
            )
            .tip(vec![
                format!(
                    "In a table of {}; {} tables published.",
                    published.map_or(0, |t| t.len()),
                    relaxed64(&table::PUBLISHES)
                ),
                format!(
                    "Binds: {} to a variant, {} of a port with no content, about {} \
                     SetPipelineState calls.",
                    relaxed64(&cmdlist::REDIRECTED_BINDS),
                    relaxed64(&cmdlist::PASSTHROUGH_PORT_BINDS),
                    relaxed64(&cmdlist::SET_PSO_CALLS)
                ),
                format!(
                    "Parameters bound to command lists: {}.",
                    relaxed(&look_binding::BINDS)
                ),
            ]),
            Entry::new(
                "Graveyard: ",
                Some(format!("{graveyard_len}, leaked {leaked}")),
            )
            .tip(vec![format!(
                "{graveyard_len} waiting, {} released, {leaked} leaked.",
                relaxed64(&graveyard::RELEASED)
            )]),
            Entry::new(
                "Game pipelines released: ",
                Some(relaxed64(&table::GAME_PSOS_RELEASED).to_string()),
            )
            .tip(vec![format!(
                "References found gone: {}.",
                relaxed64(&table::REAPED_LOST)
            )]),
            Entry::new(
                "List-slot misses: ",
                Some(relaxed64(&cmdlist::LIST_SLOT_MISSES).to_string()),
            )
            .tip(vec![format!(
                "Slot updates that found no slot, and recordings that went stale still owing the \
                 probe. Lists closed with no slot: {}.",
                relaxed64(&cmdlist::CLOSES_UNMATCHED)
            )]),
            Entry::new("Variant create: ", range_text(&status.create_ms)).tip(vec![
                format!("Recent creates: {}", spread_text(&status.create_ms)),
                format!("Last create: {create_last:.2} ms"),
            ]),
            Entry::new("Compile p50 / p95: ", p50_p95_text(&status.compile_ms)).tip(vec![
                format!("Recent compiles: {}", spread_text(&status.compile_ms)),
                format!("Last compile: {compile_last:.1} ms"),
            ]),
            Entry::new(
                "Pipeline records: ",
                Some(format!(
                    "{}, {} KB",
                    relaxed(&exports::RECORDS),
                    relaxed(&exports::RECORD_BYTES) / 1024
                )),
            ),
            Entry::new(
                "Probe copies: ",
                Some(format!(
                    "{}, skipped {}",
                    relaxed64(&cmdlist::PROBE_COPIES),
                    relaxed64(&cmdlist::PROBE_COPIES_SKIPPED)
                )),
            )
            .tip(vec![format!(
                "Copies of the probe recorded at Close, and ones owed but skipped. Barriers at a \
                 first redirect: {}.",
                relaxed64(&cmdlist::PROBE_BARRIERS)
            )]),
        ];
        pairs(ui, &entries);
    }

    /// Panel: what the player did in the panel, its own cost, and the input it kept from the game.
    fn draw_panel_counters(ui: &Ui) {
        heading(ui, "Panel");
        let read = |counter: &AtomicU64| counter.load(Ordering::Relaxed);
        let panics = read(&UI_PANICS);
        let mut panic_entry = Entry::new("Caught panics: ", Some(panics.to_string()));
        if panics != 0 {
            panic_entry.cell.pieces[1].1 = theme::ERROR_TEXT;
        }
        let micros = |counter: &AtomicU64| duration_text(read(counter) as f64 * 1.0e3);
        let input = er_overlay_input::counters();
        let entries = [
            Entry::new(
                "Code edits: ",
                Some(format!(
                    "{}, pastes {}, completions {}",
                    read(&EDIT_CALLBACKS),
                    read(&PASTES),
                    read(&COMPLETIONS_ACCEPTED)
                )),
            ),
            Entry::new(
                "Saved: ",
                Some(format!(
                    "{} layers, {} projects",
                    read(&CODE_SAVES),
                    read(&PROJECT_SAVES)
                )),
            ),
            Entry::new("Parameter edits: ", Some(read(&PARAM_EDITS).to_string())),
            Entry::new("Stack edits: ", Some(read(&STACK_EDITS).to_string())),
            Entry::new(
                "Panel time: ",
                Some(format!(
                    "{}, most {}",
                    micros(&UI_US_LAST),
                    micros(&UI_US_MAX)
                )),
            ),
            panic_entry,
            Entry::new(
                "Cursor freed: ",
                Some(format!(
                    "{}{}",
                    input.cursor_forced,
                    if input.cursor_hooked {
                        ""
                    } else {
                        ", not hooked"
                    }
                )),
            ),
            Entry::new(
                "Mouse reads blanked: ",
                Some(input.mouse_reads_blanked.to_string()),
            ),
            Entry::new("Key reads blanked: ", Some(input.keys_blanked.to_string())),
            Entry::new("Pad reads blanked: ", Some(input.pad_zeroed.to_string())),
        ];
        pairs(ui, &entries);
    }

    /// The compiler's footer (Diagnostics mockup): which compiler runs and the `dxcompiler.dll`
    /// it loaded, and the sources that crashed it.
    fn draw_compiler(ui: &Ui, status: &Status) {
        theme::section_heading(ui, "Compiler");
        let lines = compiler_lines(&status.compiler, GAME_DIR.as_deref());
        let colour = match lines.tone {
            Tone::Plain => theme::TEXT,
            Tone::Warning => theme::WARNING_TEXT,
            Tone::Error => theme::ERROR_TEXT,
        };
        theme::text_wrapped(ui, Font::Label, colour, &lines.summary);
        if ui.is_item_hovered() && !status.compiler.is_empty() {
            tooltip(ui, &[status.compiler.as_str()]);
        }
        if let Some(path) = &lines.path {
            theme::text_wrapped(ui, Font::MonoSmall, theme::TEXT_QUIET, path);
        }
        if status.poisoned != 0 {
            theme::text_wrapped(
                ui,
                Font::Label,
                theme::ERROR_TEXT,
                &format!(
                    "{} source(s) crashed the compiler three times and are not compiled again",
                    status.poisoned
                ),
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::compiler::Choice;
    use crate::probe::Region;
    use crate::replace::Stage;

    #[test]
    fn the_chip_names_the_compiler_shortly() {
        let helper = Choice::Helper {
            describe: "dxc 1.9 (1.9.2609.5) commit 5512-01b62ad4 helper, signed (C:\\x.exe, pid 4)"
                .to_owned(),
        };
        assert_eq!(compiler_chip(&helper.to_string()), "dxc 1.9 helper, signed");
        let in_process = Choice::InProcess {
            describe: "dxc 1.9".to_owned(),
            why_not_helper: "no exe".to_owned(),
        };
        assert_eq!(
            compiler_chip(&in_process.to_string()),
            "compiler in-process"
        );
        assert_eq!(
            compiler_chip(&Choice::Unchosen.to_string()),
            "compiler not started yet"
        );
        let down = Choice::HelperDown {
            why: "x".to_owned(),
            failures: 3,
        };
        assert_eq!(compiler_chip(&down.to_string()), "compiler helper down");
        let none = Choice::NoCompiler {
            why: "dxcompiler.dll missing".to_owned(),
        };
        assert_eq!(compiler_chip(&none.to_string()), "no compiler");
        assert_eq!(compiler_chip(""), "editor not running");
    }

    #[test]
    fn the_footer_reads_the_compiler_as_the_mockup_does() {
        let game = "C:\\Steam\\ELDEN RING\\Game";
        // The helper's own description, as `HelperCompiler::describe` writes it.
        let helper = Choice::Helper {
            describe: "dxc 1.9 (1.9.2609.5) commit 5512-01b62ad4 helper, signed \
                       (C:\\Steam\\ELDEN RING\\Game\\er-shader-editor\\compiler\\\
                       er-shader-compiler.exe, compiler c:\\steam\\elden ring\\game\\\
                       er-shader-editor\\compiler\\dxcompiler.dll, pid 4, started in 120 ms, \
                       build 7)"
                .to_owned(),
        };
        assert_eq!(
            compiler_lines(&helper.to_string(), Some(game)),
            CompilerLines {
                summary: "helper process, dxc 1.9, signs its own output".to_owned(),
                path: Some("<Game>\\er-shader-editor\\compiler\\dxcompiler.dll".to_owned()),
                tone: Tone::Plain,
            }
        );
        // `InProcessCompiler::describe`, inside the choice's own words.
        let in_process = Choice::InProcess {
            describe: format!(
                "dxc 1.9 in the game's process (D:\\elsewhere\\dxcompiler.dll): \
                 {IN_PROCESS_WARNING}"
            ),
            why_not_helper: "no exe".to_owned(),
        };
        assert_eq!(
            compiler_lines(&in_process.to_string(), Some(game)),
            CompilerLines {
                summary: "in the game's process, dxc 1.9: a compiler crash ends the game"
                    .to_owned(),
                path: Some("D:\\elsewhere\\dxcompiler.dll".to_owned()),
                tone: Tone::Warning,
            }
        );
        let unchosen = compiler_lines(&Choice::Unchosen.to_string(), Some(game));
        assert_eq!(unchosen.summary, "not started: nothing compiled yet");
        assert_eq!((unchosen.path, unchosen.tone), (None, Tone::Plain));
        let down = Choice::HelperDown {
            why: "x".to_owned(),
            failures: 3,
        };
        let down = compiler_lines(&down.to_string(), None);
        assert!(down.summary.starts_with("helper down after 3"), "{down:?}");
        assert_eq!(down.tone, Tone::Error);
        let none = Choice::NoCompiler {
            why: "dxcompiler.dll missing".to_owned(),
        };
        assert_eq!(compiler_lines(&none.to_string(), None).tone, Tone::Error);
        assert_eq!(compiler_lines("", None).summary, "editor not running");
    }

    #[test]
    fn a_path_under_the_game_is_written_from_it() {
        let game = Some("Z:\\games\\Game\\");
        assert_eq!(
            with_game_prefix("z:\\GAMES\\game\\er-cel-shade.toml", game),
            "<Game>\\er-cel-shade.toml"
        );
        // A sibling whose name starts the same is not under it.
        assert_eq!(
            with_game_prefix("Z:\\games\\Game2\\x.dll", game),
            "Z:\\games\\Game2\\x.dll"
        );
        assert_eq!(with_game_prefix("Z:\\x.dll", None), "Z:\\x.dll");
        assert_eq!(with_game_prefix("Z:\\x.dll", Some("")), "Z:\\x.dll");
        // Never sliced inside a character.
        assert_eq!(with_game_prefix("\u{e9}\u{e9}", Some("a")), "\u{e9}\u{e9}");
    }

    #[test]
    fn hook_cost_is_scaled_to_every_call_and_spread_over_the_presents() {
        // 100 samples of a 120 ns mean with a 20 ns floor: 100 ns each, standing for 25,600
        // calls, over 10 presents: 0.256 ms a frame.
        let ms = hook_ms_per_frame(&[(12_000, 100), (0, 0)], 20, 256, 10);
        assert!(ms.is_some_and(|ms| (ms - 0.256).abs() < 1e-9), "{ms:?}");
        assert_eq!(hook_ms_per_frame(&[(0, 0)], 20, 256, 10), None);
        assert_eq!(hook_ms_per_frame(&[(12_000, 100)], 20, 256, 0), None);
        // A mean under the floor counts as nothing rather than wrapping.
        assert_eq!(hook_ms_per_frame(&[(1_000, 100)], 20, 256, 10), Some(0.0));
    }

    #[test]
    fn the_spread_is_median_p95_and_max() {
        let values: Vec<f32> = (1..=20).map(|v| v as f32).collect();
        assert_eq!(spread(&values), Some((10.0, 19.0, 20.0)));
        assert_eq!(spread(&[]), None);
        assert_eq!(spread_text(&[]), "none yet");
        assert_eq!(
            spread_text(&[4.0]),
            "p50 4.0 ms, p95 4.0 ms, max 4.0 ms (1 run)"
        );
        assert_eq!(
            range_text(&[13.1, 1.2, 4.0]).as_deref(),
            Some("1.2 to 13.1 ms")
        );
        assert_eq!(range_text(&[4.0, 4.0]).as_deref(), Some("4.0 ms"));
        assert_eq!(range_text(&[f32::NAN]), None);
        assert_eq!(range_text(&[]), None);
        assert_eq!(p50_p95_text(&values).as_deref(), Some("10.0 / 19.0 ms"));
        assert_eq!(p50_p95_text(&[]), None);
    }

    /// The integer the probe checker assigns to `name` at the start of a line, underscores and all.
    fn python_int(text: &str, name: &str) -> Option<u64> {
        let line = text
            .lines()
            .find(|line| line.starts_with(&format!("{name} = ")))?;
        line.split('=').nth(1)?.trim().replace('_', "").parse().ok()
    }

    #[test]
    fn the_budgets_are_the_probe_checkers() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scripts/check-shader-editor-probe.py");
        let Ok(text) = std::fs::read_to_string(&path) else {
            panic!("{} cannot be read", path.display());
        };
        let Some(block) = text
            .split("COST_BUDGET_NS = {")
            .nth(1)
            .and_then(|rest| rest.split('}').next())
        else {
            panic!("no COST_BUDGET_NS in {}", path.display());
        };
        for timing in Timing::ALL {
            let key = format!("\"{}\":", timing.name());
            let budget = block
                .lines()
                .find_map(|line| line.trim().strip_prefix(&key))
                .and_then(|value| {
                    value
                        .trim()
                        .trim_end_matches(',')
                        .replace('_', "")
                        .parse()
                        .ok()
                });
            assert_eq!(budget, Some(budget_ns(timing)), "{timing:?}");
        }
        assert_eq!(python_int(&text, "FRAME_BUDGET_NS"), Some(FRAME_BUDGET_NS));
        assert_eq!(
            python_int(&text, "MIN_COST_SAMPLES"),
            Some(MIN_JUDGED_SAMPLES)
        );
    }

    #[test]
    fn costs_are_written_and_judged_as_the_mockup_shows_them() {
        assert_eq!(duration_text(141.0), "141 ns");
        assert_eq!(duration_text(999.4), "999 ns");
        assert_eq!(duration_text(2_500.0), "2.5 \u{b5}s");
        assert_eq!(duration_text(2_000.0), "2 \u{b5}s");
        assert_eq!(duration_text(77_000.0), "77 \u{b5}s");
        assert_eq!(duration_text(77_430.0), "77.4 \u{b5}s");
        assert_eq!(duration_text(1_250_000.0), "1.25 ms");
        assert_eq!(duration_text(f64::NAN), "-");
        assert_eq!(
            timing_label(Timing::CloseProbeCopy),
            "Close with probe copy"
        );
        assert_eq!(timing_label(Timing::Reset), "Reset");
        assert_eq!(verdict(None, 100, 20), Verdict::Untimed);
        assert_eq!(verdict(Some(141), 15, 20), Verdict::Unjudged);
        assert_eq!(verdict(Some(20), 16, 20), Verdict::Within);
        assert_eq!(verdict(Some(141), 16, 20), Verdict::Over);
    }

    #[test]
    fn gpu_figures_read_as_durations_and_turn_amber_over_the_port_budget() {
        use crate::gpu_time::{ReadSpan, Reader, tag};
        assert_eq!(gpu_text(None), "-");
        assert_eq!(gpu_text(Some(412.5)), "412.5 \u{b5}s");
        assert_eq!(gpu_text(Some(1_520.0)), "1.52 ms");
        assert!(!gpu_over_budget(None));
        assert!(!gpu_over_budget(Some(1_500.0)));
        assert!(gpu_over_budget(Some(1_500.5)));
        assert_eq!(rate_text(1_000_000_000), "1000 MHz");
        assert_eq!(rate_text(19_200_000), "19200000 Hz");
        assert_eq!(rate_text(0), "unknown");
        let mut reader = Reader::new();
        let span = |kind: PortKind, variant: bool, begin: u64, ticks: u64| ReadSpan {
            tag: tag(kind, variant),
            begin,
            end: begin + ticks,
        };
        reader.read(
            9,
            [
                span(PortKind::Tonemap, true, 10, 412_500),
                span(PortKind::Ao, false, 500_000, 1_600_000),
            ],
            1_000_000_000,
            Source::GameQueue,
        );
        let report = *reader.report();
        assert_eq!(
            gpu_tip(Some(&report), PortKind::Tonemap),
            "GPU: 412.5 \u{b5}s last frame, 1 span of its cel variant; mean 412.5 \u{b5}s over 1 \
             frame, budget 1.50 ms."
        );
        assert_eq!(
            gpu_tip(Some(&report), PortKind::CmbAlbedo),
            "GPU: not bound last frame; mean 0 ns over 1 frame, budget 1.50 ms."
        );
        assert!(gpu_tip(None, PortKind::Ao).contains("timer is off"));
        assert_eq!(
            gpu_stages_line(Some(&report)),
            "GPU a frame: lighting 1.60 ms, albedo 0 ns, tonemap 412.5 \u{b5}s"
        );
        assert!(gpu_over_budget(report.mean_us(PortKind::Ao)));
        let tip = gpu_stage_tip(Some(&report), Counters::default());
        assert!(tip[1].starts_with("Ticks at 1000 MHz from the game's direct queue, read 5"));
        assert_eq!(gpu_stage_tip(None, Counters::default()).len(), 2);
        assert_eq!(
            bound_words(Bound::Game),
            "the game's own pipeline",
            "the words for a stage with no content"
        );
        // The vertex stage has no port and no span: it says so instead of showing a figure.
        assert_eq!(
            vertex_stage_line("live 40/40 groups, 212/540 pipelines"),
            "Vertex stage: live 40/40 groups, 212/540 pipelines; GPU not timed"
        );
        assert!(!gpu_stages_line(Some(&report)).contains("vertex"));
        assert!(VERTEX_STAGE_TIP.contains("no GPU figure"));
    }

    #[test]
    fn the_heading_reads_the_floor_aloud() {
        for (n, expected) in [
            (0, "a"),
            (8, "an"),
            (11, "an"),
            (18, "an"),
            (20, "a"),
            (82, "an"),
            (110, "a"),
            (180, "a"),
            (800, "an"),
            (8_000, "an"),
            (11_000, "an"),
            (100_000, "a"),
        ] {
            assert_eq!(article(n), expected, "{n}");
        }
        assert_eq!(
            hook_cost_heading(256, 82),
            "Hook cost, 1 call in 256, less an 82 ns timer floor"
        );
    }

    #[test]
    fn a_ports_probe_age_says_why_it_has_none() {
        let lighting = Stage::Lighting.bit();
        let all = Stage::ALL
            .into_iter()
            .fold(0, |bits, stage| bits | stage.bit());
        assert_eq!(
            probe_age(PortKind::Ao, lighting, 0, Some((Some(PortKind::Ao), 3))),
            ProbeAge::Frames(3)
        );
        // AO with SSR shares AO's region; AO wrote it.
        assert_eq!(
            probe_age(PortKind::AoSsr, lighting, 0, Some((Some(PortKind::Ao), 3))),
            ProbeAge::NotDrawn
        );
        assert_eq!(
            probe_age(PortKind::CmbAlbedo, lighting, 0, None),
            ProbeAge::StageOff
        );
        assert_eq!(
            probe_age(PortKind::Tonemap, all, PortKind::Tonemap.bit(), None),
            ProbeAge::PortOff
        );
        assert_eq!(
            probe_age(PortKind::TonemapLut, all, 0, Some((None, 3))),
            ProbeAge::NotDrawn
        );
        assert_eq!(ProbeAge::Frames(3).text(), "3 frames");
        assert_eq!(ProbeAge::Frames(1).text(), "1 frame");
        assert_eq!(ProbeAge::Frames(12_000).text(), "9999+ frames");
        assert_eq!(ProbeAge::NotDrawn.text(), "not drawn");
        assert_eq!(ProbeAge::StageOff.text(), "stage off");
        assert!(ProbeAge::Frames(PROBE_LIVE_PRESENTS).is_live());
        assert!(!ProbeAge::Frames(PROBE_LIVE_PRESENTS + 1).is_live());
        assert!(!ProbeAge::NotDrawn.is_live());
        // The regions the table reads are the ones the worker says each kind writes.
        assert_eq!(
            crate::editor::worker::region_of(PortKind::TonemapLut),
            Region::Tonemap
        );
    }

    #[test]
    fn the_ports_line_and_the_figures_read_as_the_mockup() {
        assert_eq!(
            ports_summary(11, 0, 0, 0),
            "11 variants, 0 refused, 0 left as the game drew them, none marked unfaithful"
        );
        assert_eq!(
            ports_summary(1, 2, 3, PortKind::Ao.bit() | PortKind::Tonemap.bit()),
            "1 variant, 2 refused, 3 left as the game drew them, ao,tonemap marked unfaithful"
        );
        let mut counts = [0; 4];
        counts[Level::Full as usize] = 4;
        counts[Level::None as usize] = 5;
        assert_eq!(
            ladder_figures(counts),
            [
                ("full 4".to_owned(), false),
                ("cbv+probe 0".to_owned(), false),
                ("cbv 0".to_owned(), false),
                ("untouched 5".to_owned(), true),
            ]
        );
    }

    #[test]
    fn columns_share_what_the_fixed_ones_leave() {
        assert_eq!(
            column_widths(&[None, Some(70.0), Some(70.0)], 432.0, 10.0),
            vec![272.0, 70.0, 70.0]
        );
        assert_eq!(
            column_widths(&[None, None], 432.0, 10.0),
            vec![211.0, 211.0]
        );
        // Too narrow: the shared column is empty, never negative.
        assert_eq!(
            column_widths(&[None, Some(70.0)], 50.0, 10.0),
            vec![0.0, 70.0]
        );
    }
}
