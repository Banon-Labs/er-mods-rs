//! Vertex layers in game (design `docs/plans/er-shader-editor-vertex.md`): the game's own mesh
//! vertex shaders patched with the user's vertex layers, and the patched pipelines bound in place
//! of the game's.
//!
//! # From a carrier to moved meshes
//!
//! 1. Every pipeline the game creates is shown to [`on_created`], whatever path it took
//!    (`CreatePipelineState`, `LoadPipeline`, `LoadGraphicsPipeline`, and
//!    `CreateGraphicsPipelineState` through the stream its description makes). One whose vertex
//!    shader's debug name is a mesh pass ([`names::mesh_name`]) is recorded with a reference on it
//!    and its root signature, under its material group: every pass of one material.
//! 2. The editor's worker compiles the vertex layers into a carrier, has the helper check it, and
//!    hands it to the [`Builder`] with the table swap that ends the previous carrier's variants,
//!    which the next present applies with the parameter block packed for the new layers.
//! 3. While a carrier is live, every recorded pipeline has a counting entry in the redirect table,
//!    so the groups on screen are known. The builder takes the groups bound in the last 10 s first
//!    ([`plan::build_order`]): it has the helper splice the carrier into each distinct vertex
//!    shader of the group, checks every variant by the nine rules ([`rules`]), creates the
//!    variants a present's budget at a time ([`plan::Budget`]), and when every pipeline of the group
//!    has one, publishes them all in one table swap. A group is never partly moved.
//! 4. A pipeline the game creates for a group that is live suspends the whole group at once
//!    (`table::flag::VERTEX_SUSPENDED`, an atomic on each entry) until the builder has its variant
//!    too and publishes the group again.
//!
//! # The proof
//!
//! A file `er-shader-editor/vertex-proof` ([`Control`]) or the two exports switch the proof mode
//! on, narrow the passes redirected, or hold the vertex stage off for a calibration. While the
//! mode is on, the AO port writes the vertex proof region of the probe, each present's copy of it
//! is kept ([`ProofFrame`]), and the cel worker appends them to `er-cel-shade-vertex-proof.jsonl`
//! beside the telemetry, which `scripts/check-shader-editor-probe.py --expect vertex-*` reads.
//!
//! [`names`], [`rules`], [`plan`], [`Control`] and the proof frames' text are pure and tested on
//! the host. The Windows half holds the registry, the builder and the files.

pub mod names;
pub mod plan;
pub mod rules;

use std::fmt::Write as _;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::probe::VertexSample;

/// How a pipeline reached D3D12.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Path {
    /// `ID3D12Device2::CreatePipelineState`.
    Stream = 0,
    /// `ID3D12PipelineLibrary1::LoadPipeline`.
    LoadStream = 1,
    /// `ID3D12PipelineLibrary::LoadGraphicsPipeline`.
    LoadGraphics = 2,
    /// `ID3D12Device::CreateGraphicsPipelineState`.
    GraphicsDesc = 3,
}

impl Path {
    pub const COUNT: usize = 4;
    pub const ALL: [Self; Self::COUNT] = [
        Self::Stream,
        Self::LoadStream,
        Self::LoadGraphics,
        Self::GraphicsDesc,
    ];

    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Stream => "stream",
            Self::LoadStream => "load_pipeline",
            Self::LoadGraphics => "load_graphics",
            Self::GraphicsDesc => "graphics_desc",
        }
    }
}

/// Hold the vertex stage off whatever the layers say: the proof's calibration, set by
/// [`Control::hold_off`]. The menu's staging clears the vertex bit while it is set.
pub static HOLD_OFF: AtomicBool = AtomicBool::new(false);

/// The proof's switches, as `er-shader-editor/vertex-proof` writes them: `key = value` lines,
/// `#` comments. An absent file is every switch off.
///
/// ```text
/// proof = 1          # the vertex proof mode: 0 off
/// passes = dpt,dpta  # the passes redirected; empty or `all` for every one
/// vertex = off       # hold the vertex stage off, for a calibration
/// ```
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Control {
    pub proof: u32,
    pub passes: u32,
    pub hold_off: bool,
}

impl Default for Control {
    fn default() -> Self {
        Self {
            proof: 0,
            passes: names::Pass::ALL_BITS,
            hold_off: false,
        }
    }
}

impl Control {
    /// The file's name in `er-shader-editor/`.
    pub const FILE_NAME: &str = "vertex-proof";

    /// Read the file's text; a line it cannot read is named in the warnings and changes nothing.
    #[must_use]
    pub fn parse(text: &str) -> (Self, Vec<String>) {
        let mut control = Self::default();
        let mut warnings = Vec::new();
        for (number, raw) in text.lines().enumerate() {
            let line = raw.split('#').next().unwrap_or_default().trim();
            if line.is_empty() {
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                warnings.push(format!("line {}: expected `key = value`", number + 1));
                continue;
            };
            let (key, value) = (key.trim(), value.trim().trim_matches('"'));
            match key {
                "proof" => match value.parse::<u32>() {
                    Ok(mode) => control.proof = mode,
                    Err(_) => warnings.push(format!(
                        "line {}: `proof` wants a whole number, not `{value}`",
                        number + 1
                    )),
                },
                "passes" => {
                    let (mask, unknown) = if value.eq_ignore_ascii_case("all") {
                        (names::Pass::ALL_BITS, Vec::new())
                    } else {
                        names::Pass::parse_mask(value)
                    };
                    for token in unknown {
                        warnings.push(format!("line {}: `{token}` is no pass", number + 1));
                    }
                    control.passes = mask;
                }
                "vertex" => match value {
                    "on" => control.hold_off = false,
                    "off" => control.hold_off = true,
                    other => warnings.push(format!(
                        "line {}: `vertex` is `on` or `off`, not `{other}`",
                        number + 1
                    )),
                },
                other => warnings.push(format!("line {}: unknown key `{other}`", number + 1)),
            }
        }
        (control, warnings)
    }

    /// Apply the switches: the proof mode into the next parameter block, the pass mask into the
    /// bind hooks, the hold into the staging.
    pub fn apply(&self) {
        #[cfg(windows)]
        crate::params::set_vertex_proof(self.proof);
        crate::cmdlist::VERTEX_PASSES
            .store(self.passes | !names::Pass::ALL_BITS, Ordering::Relaxed);
        HOLD_OFF.store(self.hold_off, Ordering::Release);
    }
}

/// One present's copy of the vertex proof region.
#[derive(Clone, Debug, PartialEq)]
pub struct ProofFrame {
    /// The present the copy was read at.
    pub present: u64,
    /// The frame most of the samples were written in; the others are left out.
    pub frame: u32,
    /// The time that frame's parameter block carried, when it is still known.
    pub time: Option<f32>,
    /// What the vertex stage was at the present the copy was read: on, the pass mask, the
    /// carrier's generation.
    pub vertex_on: bool,
    pub passes: u32,
    pub generation: u64,
    pub samples: Vec<VertexSample>,
}

impl ProofFrame {
    /// The frame `samples` agree on, and the samples of it.
    #[must_use]
    pub fn majority(samples: &[VertexSample]) -> Option<(u32, Vec<VertexSample>)> {
        let mut counts: Vec<(u32, usize)> = Vec::new();
        for sample in samples {
            match counts.iter_mut().find(|(frame, _)| *frame == sample.frame) {
                Some((_, count)) => *count += 1,
                None => counts.push((sample.frame, 1)),
            }
        }
        let (frame, _) = counts
            .iter()
            .max_by_key(|(frame, count)| (*count, *frame))
            .copied()?;
        Some((
            frame,
            samples
                .iter()
                .filter(|sample| sample.frame == frame)
                .copied()
                .collect(),
        ))
    }

    /// One line of `er-cel-shade-vertex-proof.jsonl`: `p` the present, `f` the frame, `t` its
    /// time or null, `v` the vertex stage on, `m` the pass mask, `g` the generation, and `x` the
    /// samples as `[index, depth, normal agreement, occlusion, light]`. A value that is not
    /// finite is written as null.
    #[must_use]
    pub fn to_json(&self) -> String {
        let number = |out: &mut String, value: f32| {
            if value.is_finite() {
                let _ = write!(out, "{value}");
            } else {
                out.push_str("null");
            }
        };
        let mut out = format!("{{\"p\":{},\"f\":{},\"t\":", self.present, self.frame);
        match self.time {
            Some(time) => number(&mut out, time),
            None => out.push_str("null"),
        }
        let _ = write!(
            out,
            ",\"v\":{},\"m\":{},\"g\":{},\"x\":[",
            self.vertex_on, self.passes, self.generation
        );
        for (at, sample) in self.samples.iter().enumerate() {
            if at != 0 {
                out.push(',');
            }
            let _ = write!(out, "[{},", sample.index);
            number(&mut out, sample.depth);
            out.push(',');
            number(&mut out, sample.normal_dot);
            out.push(',');
            number(&mut out, sample.occlusion);
            out.push(',');
            number(&mut out, sample.light);
            out.push(']');
        }
        out.push_str("]}");
        out
    }
}

/// What the vertex stage is doing, as the panel's chip shows it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ChipState {
    /// No carrier: there is no vertex layer, or none has compiled.
    #[default]
    Idle,
    /// A carrier is live and some of the groups it wants are still being built.
    Preparing,
    /// Every group the carrier wants is live.
    Live,
}

/// What the chip is made from: the builder's counts.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ChipCounts {
    /// The carrier's generation, 0 with none.
    pub generation: u64,
    pub groups_live: usize,
    pub groups_wanted: usize,
    pub pipelines_live: usize,
    pub pipelines_wanted: usize,
    /// The carrier's swap has not reached a present yet.
    pub waiting_for_present: bool,
}

/// The panel's chip for the vertex stage: its state and its words, `preparing 31/40 groups,
/// 212/540 pipelines`, or `no vertex layers` with no carrier.
#[must_use]
pub fn chip(counts: &ChipCounts) -> (ChipState, String) {
    if counts.generation == 0 {
        return (ChipState::Idle, "no vertex layers".to_owned());
    }
    let state = if counts.groups_live < counts.groups_wanted {
        ChipState::Preparing
    } else {
        ChipState::Live
    };
    let words = format!(
        "{} {}/{} groups, {}/{} pipelines{}",
        match state {
            ChipState::Preparing => "preparing",
            ChipState::Idle | ChipState::Live => "live",
        },
        counts.groups_live,
        counts.groups_wanted,
        counts.pipelines_live,
        counts.pipelines_wanted,
        if counts.waiting_for_present {
            ", waiting for a present"
        } else {
            ""
        }
    );
    (state, words)
}

/// The proof file's name, beside the telemetry file.
pub const PROOF_FILE_NAME: &str = "er-cel-shade-vertex-proof.jsonl";

/// Frames kept for the file between two of the cel worker's writes.
pub const PROOF_FRAMES_KEPT: usize = 600;

/// The proof file stops growing past this many bytes; a run that needs more starts a new proof.
pub const PROOF_FILE_LIMIT: u64 = 64 << 20;

#[cfg(windows)]
mod process;

#[cfg(windows)]
pub use process::{
    Builder, Status, er_shader_editor_set_vertex_passes_v1, er_shader_editor_set_vertex_proof_v1,
    flush_proof, note_panic, on_created, on_present, status, telemetry_json,
};

#[cfg(test)]
mod tests {
    use super::*;
    use names::Pass;

    #[test]
    fn the_control_file_reads_its_switches_and_names_what_it_cannot() {
        let (control, warnings) =
            Control::parse("# the oracle S run\nproof = 1\npasses = dpt, DptA\nvertex = on\n");
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(
            control,
            Control {
                proof: 1,
                passes: Pass::Dpt.bit() | Pass::DptA.bit(),
                hold_off: false
            }
        );
        let (control, warnings) =
            Control::parse("proof = yes\npasses = all\nvertex = off\nspeed = 3\nnonsense\n");
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        assert_eq!(
            control,
            Control {
                proof: 0,
                passes: Pass::ALL_BITS,
                hold_off: true
            }
        );
        assert_eq!(Control::parse("").0, Control::default());
    }

    #[test]
    fn the_chip_says_what_the_stage_is_doing() {
        assert_eq!(
            chip(&ChipCounts::default()),
            (ChipState::Idle, "no vertex layers".to_owned())
        );
        let mut counts = ChipCounts {
            generation: 3,
            groups_live: 31,
            groups_wanted: 40,
            pipelines_live: 212,
            pipelines_wanted: 540,
            waiting_for_present: false,
        };
        assert_eq!(
            chip(&counts),
            (
                ChipState::Preparing,
                "preparing 31/40 groups, 212/540 pipelines".to_owned()
            )
        );
        counts.groups_live = 40;
        counts.waiting_for_present = true;
        assert_eq!(
            chip(&counts),
            (
                ChipState::Live,
                "live 40/40 groups, 212/540 pipelines, waiting for a present".to_owned()
            )
        );
    }

    fn sample(index: usize, frame: u32) -> VertexSample {
        VertexSample {
            index,
            depth: 10.0 + index as f32,
            normal_dot: 0.9,
            occlusion: 1.0,
            light: 2.5,
            frame,
        }
    }

    #[test]
    fn a_frame_keeps_the_samples_most_of_them_agree_on() {
        let samples = [sample(0, 7), sample(1, 8), sample(2, 8), sample(3, 6)];
        let Some((frame, kept)) = ProofFrame::majority(&samples) else {
            panic!("no majority");
        };
        assert_eq!(frame, 8);
        assert_eq!(kept.iter().map(|s| s.index).collect::<Vec<_>>(), [1, 2]);
        assert_eq!(ProofFrame::majority(&[]), None);
    }

    #[test]
    fn a_proof_frame_is_one_line_of_json() {
        let mut frame = ProofFrame {
            present: 103,
            frame: 100,
            time: Some(1.5),
            vertex_on: true,
            passes: Pass::ALL_BITS,
            generation: 2,
            samples: vec![sample(4, 100)],
        };
        frame.samples[0].normal_dot = f32::NAN;
        assert_eq!(
            frame.to_json(),
            "{\"p\":103,\"f\":100,\"t\":1.5,\"v\":true,\"m\":1023,\"g\":2,\"x\":[[4,14,null,1,2.5]]}"
        );
        frame.time = None;
        frame.samples.clear();
        assert!(
            frame
                .to_json()
                .ends_with("\"t\":null,\"v\":true,\"m\":1023,\"g\":2,\"x\":[]}")
        );
    }
}
