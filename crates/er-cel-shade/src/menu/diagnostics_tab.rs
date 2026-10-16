//! The Diagnostics tab (design 9): the counters the hooks keep and never log themselves, and the
//! editor's own -- which compiler runs, how long compiles and variant creation take, each port
//! kind's last verification report, and what the panel counted.
//!
//! The catalogue size and Measure cost come with P2 and P1; the tab has no line for them yet.

use crate::editor::worker::percentile;

/// A probe region whose header was written within this many presents counts as live.
pub const PROBE_LIVE_PRESENTS: u32 = 10;

/// The compiler as the toolbar's chip says it: "dxc 1.9 helper, signed", or what is wrong, from
/// the worker's description of its choice (`editor::compiler::Choice`'s `Display`).
#[must_use]
pub fn compiler_chip(description: &str) -> String {
    use crate::editor::compiler::IN_PROCESS_WARNING;
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

#[cfg(windows)]
pub(super) use process::draw;

/// Drawing the tab.
#[cfg(windows)]
mod process {
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

    use hudhook::imgui::{TreeNodeFlags, Ui};

    use super::{hook_ms_per_frame, spread_text};
    use crate::cmdlist::{self, SAMPLE_EVERY, Timing};
    use crate::editor::worker::Status;
    use crate::exports::{self, mean_ns, variant_kind};
    use crate::probe::{self, Region};
    use crate::replace::{PortKind, Stage};
    use crate::settings::render_ports;
    use crate::{graveyard, hooks, input, look_binding, params, table};

    fn heading(ui: &Ui, text: &str) {
        ui.spacing();
        ui.text_colored(super::super::HEADING_TEXT, text);
    }

    /// The tab, with the editor's `status` as the worker last published it.
    pub(in crate::menu) fn draw(ui: &Ui, status: &Status) {
        let relaxed = |a: &AtomicUsize| a.load(Ordering::Relaxed);
        let relaxed64 = |a: &AtomicU64| a.load(Ordering::Relaxed);

        heading(ui, "COMPILER");
        ui.text_wrapped(&status.compiler);
        if status.in_process {
            ui.text_colored(
                super::super::WARNING_TEXT,
                "A crash in the compiler ends the game while it runs in process.",
            );
        }
        ui.text(format!("Compile: {}", spread_text(&status.compile_ms)));
        ui.text(format!(
            "Variant create: {}",
            spread_text(&status.create_ms)
        ));
        let (compile_ms, create_ms) = exports::last_ms();
        ui.text(format!(
            "Last compile {compile_ms:.1} ms, last create {create_ms:.2} ms"
        ));
        if status.poisoned != 0 {
            ui.text_colored(
                super::super::ERROR_TEXT,
                format!(
                    "{} source(s) crashed the compiler three times and are not compiled again",
                    status.poisoned
                ),
            );
        }

        heading(ui, "PORTS");
        for kind in PortKind::ALL {
            let live = status.live_variant[kind as usize];
            ui.text(format!(
                "{}: {} ready, live variant {live:#010x}{}",
                kind.replacement().name,
                relaxed(&hooks::PORTS_READY[kind as usize]),
                if live & 0x00ff_ffff == 0 {
                    " (the embedded cel port)"
                } else {
                    ""
                }
            ));
            if let Some(report) = &status.reports[kind as usize]
                && ui.collapsing_header(
                    format!("Verification of {}##report{}", kind.key(), kind as u8),
                    TreeNodeFlags::empty(),
                )
            {
                ui.text_wrapped(report);
            }
        }
        ui.text(format!(
            "{} variants, {} refused by the driver, {} left as the game drew them",
            relaxed(&hooks::VARIANTS),
            relaxed(&hooks::REWRITES_REFUSED),
            relaxed(&hooks::PORTS_LEFT_VANILLA)
        ));
        ui.text(format!(
            "Pipeline records kept: {} ({} KB)",
            relaxed(&exports::RECORDS),
            relaxed(&exports::RECORD_BYTES) / 1024
        ));

        heading(ui, "ROOT SIGNATURES");
        ui.text(look_binding::ladder_line());
        ui.text(format!(
            "Left as the game made them: {} out of room, {} other; {} found no table slot",
            relaxed(&look_binding::SKIPPED_NO_ROOM),
            relaxed(&look_binding::SKIPPED_OTHER),
            relaxed(&look_binding::TABLE_FULL)
        ));
        ui.text(format!(
            "Pipelines given a root signature: {} (none found: {})",
            relaxed(&hooks::NO_ROOT_SIGNATURE_FIXED),
            relaxed(&hooks::NO_ROOT_SIGNATURE_LEFT)
        ));

        heading(ui, "REDIRECT AND LIFETIME");
        let published = table::published();
        let stages = cmdlist::STAGE_ACTIVE.load(Ordering::Relaxed);
        let names: Vec<&str> = Stage::ALL
            .into_iter()
            .filter(|stage| stages & stage.bit() != 0)
            .map(Stage::name)
            .collect();
        ui.text(format!(
            "Stages with content: {}",
            if names.is_empty() {
                "none".to_owned()
            } else {
                names.join(", ")
            }
        ));
        let off = cmdlist::PORTS_OFF.load(Ordering::Relaxed) as u8;
        ui.text(format!(
            "Ports off: {}",
            if off == 0 {
                "none".to_owned()
            } else {
                render_ports(off)
            }
        ));
        ui.text(format!(
            "Live redirects {} in a table of {}; {} tables published",
            published.map_or(0, |t| t.redirects()),
            published.map_or(0, |t| t.len()),
            relaxed64(&table::PUBLISHES)
        ));
        ui.text(format!(
            "Binds: {} to a variant, {} of a port with no content, about {} SetPipelineState calls",
            relaxed64(&cmdlist::REDIRECTED_BINDS),
            relaxed64(&cmdlist::PASSTHROUGH_PORT_BINDS),
            relaxed64(&cmdlist::SET_PSO_CALLS)
        ));
        ui.text(format!(
            "Parameters bound to command lists: {}",
            relaxed(&look_binding::BINDS)
        ));
        ui.text(format!(
            "Game pipelines released: {} (references found gone: {})",
            relaxed64(&table::GAME_PSOS_RELEASED),
            relaxed64(&table::REAPED_LOST)
        ));
        ui.text(format!(
            "Graveyard: {} waiting, {} released, {} leaked",
            relaxed(&graveyard::GRAVEYARD_LEN),
            relaxed64(&graveyard::RELEASED),
            relaxed64(&graveyard::LEAKED)
        ));

        heading(ui, "PROBE");
        let present = params::presents();
        let regions = probe::read_latest(present);
        for region in Region::ALL {
            match regions.iter().find(|record| record.region == region) {
                Some(record) => ui.text(format!(
                    "{}: {} presents old, {}, {}x{}, {} of {} samples current",
                    region.name(),
                    probe::frame_age(present, record.header.frame),
                    variant_kind(record.header.variant_id)
                        .map_or("an unknown variant", |kind| kind.replacement().name),
                    record.header.width,
                    record.header.height,
                    record.samples.len(),
                    probe::SAMPLES
                )),
                None => ui.text(format!("{}: not written", region.name())),
            }
        }
        ui.text(format!(
            "Barriers at a first redirect: {}; copies at Close: {} (skipped {}); lists closed with \
             no slot: {}",
            relaxed64(&cmdlist::PROBE_BARRIERS),
            relaxed64(&cmdlist::PROBE_COPIES),
            relaxed64(&cmdlist::PROBE_COPIES_SKIPPED),
            relaxed64(&cmdlist::CLOSES_UNMATCHED)
        ));

        heading(ui, "HOOK COST");
        let floor = relaxed64(&exports::TIMER_FLOOR_NS);
        ui.text(format!(
            "ns added per call, 1 call in {SAMPLE_EVERY} timed, less a timer floor of {floor} ns:"
        ));
        let timings: Vec<(u64, u64)> = Timing::ALL
            .into_iter()
            .map(|timing| {
                (
                    relaxed64(&cmdlist::HOOK_NS_SUM[timing as usize]),
                    relaxed64(&cmdlist::HOOK_NS_SAMPLES[timing as usize]),
                )
            })
            .collect();
        for (timing, &(sum, samples)) in Timing::ALL.into_iter().zip(&timings) {
            match mean_ns(sum, samples, floor) {
                Some(mean) => ui.text(format!("  {}: {mean} ({samples} timed)", timing.name())),
                None => ui.text(format!("  {}: not timed yet", timing.name())),
            }
        }
        match hook_ms_per_frame(&timings, floor, SAMPLE_EVERY, present) {
            Some(ms) => ui.text(format!(
                "All hooks: about {ms:.3} ms a frame over the session"
            )),
            None => ui.text("All hooks: not timed yet"),
        }
        ui.text(format!(
            "List slot misses: {}",
            relaxed64(&cmdlist::LIST_SLOT_MISSES)
        ));
        ui.text(format!(
            "Pipelines passed to Reset, ClearState, CreateCommandList: {}, {}, {}",
            relaxed64(&cmdlist::RESET_WITH_PSO),
            relaxed64(&cmdlist::CLEAR_STATE_WITH_PSO),
            relaxed64(&cmdlist::CREATE_LIST_WITH_PSO)
        ));

        heading(ui, "PANEL");
        let counted = super::super::telemetry_counters();
        for (name, value) in counted {
            ui.text(format!("{name}: {value}"));
        }
        ui.text(format!(
            "Cursor freed: {} (hooked: {}); game reads blanked: mouse {}, keys {}, pad {}",
            relaxed(&input::CURSOR_FORCED),
            input::CURSOR_HOOKED.load(Ordering::Relaxed),
            er_dinput_suppress_core::suppressed_mouse_reads(),
            er_dinput_suppress_core::suppressed_keys(),
            relaxed(&input::PAD_SUPPRESSED)
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::compiler::Choice;

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
    }
}
