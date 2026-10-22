//! Getting back to the game's own picture: the mode, the Vanilla switch, the panic chord, and which
//! stages have content (design 11).
//!
//! # Vanilla
//!
//! [`crate::cmdlist::VANILLA`] is the one flag the bind hooks read: while it is set every pipeline
//! is bound as the game asked. Four things ask for it, and [`Switches`] keeps them apart so that
//! clearing one cannot clear another:
//!
//! * the mode ([`Mode::Vanilla`]), set through the `er_shader_editor_set_mode_v1` export;
//! * the user: the panic chord ([`PANIC_CHORD`]) and the menu's Vanilla checkbox. The chord only
//!   ever sets it; the checkbox is the one way back;
//! * the compare key, held in the panel ([`Event::Compare`]), which clears only what it set: a
//!   panic chord or a mode export that arrives while it is held outlasts the key;
//! * a detour that caught a panic ([`crate::cmdlist::HOOK_FAULTED`]), which nothing clears.
//!
//! # Stage content
//!
//! A port is redirected only while its stage has content (design 1): a cel knob read by that
//! stage's ports away from its off value, or an editor layer there that draws.
//! [`stages_with_content`] answers the first from the look, the editor's worker keeps the second
//! in [`LAYER_STAGES`], and the menu stores the two together in
//! [`crate::cmdlist::STAGE_ACTIVE`] at start-up, once per present and whenever the mode export
//! changes the mode ([`staged`]).
//!
//! # Fidelity
//!
//! Mode 2, [`Mode::Fidelity`], redirects every stage with every knob at its off value, and the
//! editor turns every layer off for it (`editor::worker::silence_layers`), the stock cel layers
//! among them: the ports then run as transcriptions of the game's own passes, which is what the
//! fidelity proof compares against vanilla (design 14, P0.1 item 5). The proof runs it one port kind at a time
//! ([`ModeSetting::kinds`]), so a failure names its port, and a port that fails is marked
//! unfaithful: the settings file keeps the mark, and outside a fidelity test that kind is bound as
//! the game asked whatever its stage holds ([`crate::cmdlist::PORTS_OFF`]). Only a fidelity test
//! redirects an unfaithful kind, because the test is what decides the mark.
//!
//! # The panic chord
//!
//! A thread of its own polls [`PANIC_CHORD`] every [`PANIC_POLL_MS`], so it works even when the
//! overlay never draws, and only while the game has focus. A press logs `panic: vanilla`.
//!
//! The policy is pure and tested on the host. The Windows half holds the thread.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Mutex, PoisonError};

use crate::cmdlist::{HOOK_FAULTED, VANILLA};
use crate::replace::{PortKind, Stage, VERTEX_STAGE_BIT};
use crate::settings::{GPU_FLOATS, KNOB_COUNT, KNOBS, Look};

/// What the redirect is for, as the mode export sets it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u32)]
pub enum Mode {
    /// Ports redirected while their stage has content.
    Normal = 0,
    /// Every pipeline bound as the game asked.
    Vanilla = 1,
    /// Every port redirected, with every cel knob at its off value: the port-fidelity test.
    Fidelity = 2,
}

impl Mode {
    pub const ALL: [Self; 3] = [Self::Normal, Self::Vanilla, Self::Fidelity];

    /// The mode numbered `value`, as the export takes it.
    #[must_use]
    pub const fn from_u32(value: u32) -> Option<Self> {
        match value {
            0 => Some(Self::Normal),
            1 => Some(Self::Vanilla),
            2 => Some(Self::Fidelity),
            _ => None,
        }
    }

    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::Vanilla => "vanilla",
            Self::Fidelity => "fidelity",
        }
    }
}

/// Where a mode value carries the port kinds a fidelity test redirects.
pub const MODE_KINDS_SHIFT: u32 = 8;

/// A mode, and for [`Mode::Fidelity`] the port kinds it redirects.
///
/// The mode export takes it, and the stats report it, as one `u32`: the mode's number in the low
/// byte and the kinds' [`PortKind::bit`]s in the next. A kind byte of 0 means every kind, so
/// plain `2` is the whole fidelity test; the other modes take no kind byte.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ModeSetting {
    pub mode: Mode,
    /// The kinds a fidelity test redirects, never empty; 0 for the other modes.
    pub kinds: u8,
}

impl ModeSetting {
    pub const NORMAL: Self = Self {
        mode: Mode::Normal,
        kinds: 0,
    };

    /// A fidelity test of `kinds`; every kind when that is none.
    #[must_use]
    pub const fn fidelity(kinds: u8) -> Self {
        let kinds = kinds & PortKind::ALL_BITS;
        Self {
            mode: Mode::Fidelity,
            kinds: if kinds == 0 {
                PortKind::ALL_BITS
            } else {
                kinds
            },
        }
    }

    /// The setting a mode export value asks for, or `None` for one that names no mode, a kind that
    /// does not exist, kinds for a mode other than fidelity, or anything past the kind byte.
    #[must_use]
    pub const fn decode(value: u32) -> Option<Self> {
        if value >> (MODE_KINDS_SHIFT + 8) != 0 {
            return None;
        }
        let kinds = (value >> MODE_KINDS_SHIFT) as u8;
        let Some(mode) = Mode::from_u32(value & 0xff) else {
            return None;
        };
        match mode {
            Mode::Fidelity if kinds & !PortKind::ALL_BITS == 0 => Some(Self::fidelity(kinds)),
            Mode::Normal | Mode::Vanilla if kinds == 0 => Some(Self { mode, kinds: 0 }),
            _ => None,
        }
    }

    /// The value the stats report: [`ModeSetting::decode`] reads it back. Every kind is written
    /// as plain `2`, so the whole test reads back as the value that asked for it.
    #[must_use]
    pub const fn encode(self) -> u32 {
        let kinds = match self.mode {
            Mode::Fidelity if self.kinds != PortKind::ALL_BITS => self.kinds as u32,
            _ => 0,
        };
        self.mode as u32 | kinds << MODE_KINDS_SHIFT
    }
}

impl From<Mode> for ModeSetting {
    /// The mode alone; a fidelity test of every kind.
    fn from(mode: Mode) -> Self {
        match mode {
            Mode::Fidelity => Self::fidelity(PortKind::ALL_BITS),
            Mode::Normal | Mode::Vanilla => Self { mode, kinds: 0 },
        }
    }
}

/// The stage whose ports read each knob, in [`KNOBS`] order: the port source that names the
/// knob's `cel_*` define. A test reads the three port sources and holds this to them. Every knob
/// left is the AO port's; the Albedo and Tonemap knobs are stock layers now, whose stages get
/// their content through [`LAYER_STAGES`] like any layer's (`crate::editor::cel`).
pub const KNOB_STAGES: [Stage; KNOB_COUNT] = [
    Stage::Lighting, // light_band_stops
    Stage::Lighting, // outline_ink
    Stage::Lighting, // outline_width
    Stage::Lighting, // outline_depth
    Stage::Lighting, // outline_crease
];

/// Every stage's bit. Written out so `scripts/rva_symbols.py` can read it; the assert keeps it the
/// union of [`Stage::ALL`]'s bits.
pub const ALL_STAGES: u32 = 0b111;
const _: () = assert!(ALL_STAGES == stage_bits(&Stage::ALL));

/// The union of `stages`' bits.
const fn stage_bits(stages: &[Stage]) -> u32 {
    let mut bits = 0;
    let mut at = 0;
    while at < stages.len() {
        bits |= stages[at].bit();
        at += 1;
    }
    bits
}

/// The stages with content under this look: each stage one of whose knobs is away from its off
/// value, compared after [`Look::clamped`], which is what the shaders see. A disabled look has
/// none.
#[must_use]
pub fn stages_with_content(look: &Look, enabled: bool) -> u32 {
    if !enabled {
        return 0;
    }
    look.clamped()
        .0
        .iter()
        .zip(KNOBS.iter().zip(KNOB_STAGES))
        .filter(|(value, (knob, _))| **value != knob.off)
        .fold(0, |bits, (_, (_, stage))| bits | stage.bit())
}

/// What one present stages: the parameter block's cel registers,
/// [`crate::cmdlist::STAGE_ACTIVE`] and [`crate::cmdlist::PORTS_OFF`].
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Staged {
    pub look: [f32; GPU_FLOATS],
    pub stages: u32,
    /// Port kinds bound as the game asked, one [`PortKind::bit`] each.
    pub ports_off: u32,
}

/// What the vertex stage asks of a present: the proof mode, which needs the AO port bound
/// because the AO port writes the proof, and the calibration's hold, which keeps the vertex
/// stage off whatever its layers say (`crate::vertex::Control`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct VertexSwitches {
    pub proof: bool,
    pub hold_off: bool,
}

/// What a present stages under `setting` for the menu's look, the ports marked `unfaithful` and
/// the stages with a drawing editor layer, `layer_stages` ([`LAYER_STAGES`]). [`Mode::Fidelity`]
/// redirects every stage of the kinds under test, every other kind off, and publishes
/// [`Look::vanilla`]; the other modes follow the look and the layers and keep the unfaithful
/// ports off. Layers give their stage content whether or not cel is enabled, because the
/// checkbox turns cel off and not the editor. Vanilla needs nothing here: the bind hooks check
/// [`crate::cmdlist::VANILLA`] before the stages.
///
/// The vertex stage's bit ([`VERTEX_STAGE_BIT`]) comes from `layer_stages` alone, unless
/// `vertex.hold_off`; a fidelity test, which compares the ports alone, never sets it. The vertex
/// proof gives the Lighting stage content, so its port is bound to write the proof.
#[must_use]
pub fn staged(
    setting: ModeSetting,
    look: &Look,
    enabled: bool,
    unfaithful: u8,
    layer_stages: u32,
    vertex: VertexSwitches,
) -> Staged {
    match setting.mode {
        Mode::Fidelity => Staged {
            look: Look::vanilla().gpu_block(true),
            stages: ALL_STAGES,
            ports_off: u32::from(!setting.kinds & PortKind::ALL_BITS),
        },
        Mode::Normal | Mode::Vanilla => {
            let mut stages = stages_with_content(look, enabled)
                | (layer_stages & (ALL_STAGES | VERTEX_STAGE_BIT));
            if vertex.hold_off {
                stages &= !VERTEX_STAGE_BIT;
            }
            if vertex.proof {
                stages |= Stage::Lighting.bit();
            }
            Staged {
                look: look.gpu_block(enabled),
                stages,
                ports_off: u32::from(unfaithful & PortKind::ALL_BITS),
            }
        }
    }
}

/// The port stages with an editor layer that draws, one [`Stage::bit`] each: the editor's share
/// of [`crate::cmdlist::STAGE_ACTIVE`]. Written by the editor's worker whenever the stack changes,
/// and read by every present's [`staged`].
pub static LAYER_STAGES: AtomicU32 = AtomicU32::new(0);

/// The requests for vanilla other than a hook fault, which [`crate::cmdlist::HOOK_FAULTED`] holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Switches {
    pub mode: ModeSetting,
    /// Set by the panic chord or the checkbox, cleared only by the checkbox.
    pub user_vanilla: bool,
    /// The compare key is held in the panel. Set and cleared by that key alone.
    pub compare: bool,
}

/// One thing that changes [`Switches`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    /// The mode export.
    SetMode(ModeSetting),
    /// The menu's Vanilla checkbox, ticked or cleared.
    Checkbox(bool),
    /// The panic chord.
    PanicChord,
    /// The compare key went down or came up.
    Compare(bool),
}

impl Switches {
    pub const START: Self = Self {
        mode: ModeSetting::NORMAL,
        user_vanilla: false,
        compare: false,
    };

    /// The switches after `event`.
    #[must_use]
    pub fn after(self, event: Event) -> Self {
        match event {
            Event::SetMode(mode) => Self { mode, ..self },
            Event::Checkbox(true) | Event::PanicChord => Self {
                user_vanilla: true,
                ..self
            },
            // The checkbox shows vanilla however it was asked for, so clearing it clears the
            // mode's request as well as the user's. The compare key is not a request the
            // checkbox made, and it ends when the key comes up.
            Event::Checkbox(false) => Self {
                mode: match self.mode.mode {
                    Mode::Vanilla => ModeSetting::NORMAL,
                    _ => self.mode,
                },
                user_vanilla: false,
                ..self
            },
            Event::Compare(held) => Self {
                compare: held,
                ..self
            },
        }
    }

    /// Should every pipeline be bound as the game asked? `faulted` is
    /// [`crate::cmdlist::HOOK_FAULTED`].
    #[must_use]
    pub fn vanilla(self, faulted: bool) -> bool {
        faulted || self.user_vanilla || self.compare || self.mode.mode == Mode::Vanilla
    }
}

/// Edge detection for a held key: a press is the first poll that finds it down.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Edge {
    held: bool,
}

impl Edge {
    /// Report this poll's state; true only on the poll that finds the key newly down.
    pub fn pressed(&mut self, held: bool) -> bool {
        let pressed = held && !self.held;
        self.held = held;
        pressed
    }
}

/// The panic chord, in `er-hotkey-config`'s spelling.
pub const PANIC_CHORD: &str = "ctrl+shift+F6";

/// How often the panic thread polls the keyboard.
pub const PANIC_POLL_MS: u64 = 30;

/// The writer side of the switches. Never taken on a hot path: the bind hooks read only
/// [`crate::cmdlist::VANILLA`], and [`MODE`] mirrors the mode for lock-free readers.
static SWITCHES: Mutex<Switches> = Mutex::new(Switches::START);

/// The current [`ModeSetting`], as [`ModeSetting::encode`] writes it.
pub static MODE: AtomicU32 = AtomicU32::new(Mode::Normal as u32);

/// The ports marked unfaithful, one [`PortKind::bit`] each, as the menu last staged them from the
/// settings: for the stats, which report the mark the hooks are keeping.
pub static UNFAITHFUL: AtomicU32 = AtomicU32::new(0);

/// The current mode and its fidelity kinds.
#[must_use]
pub fn mode_setting() -> ModeSetting {
    ModeSetting::decode(MODE.load(Ordering::Acquire)).unwrap_or(ModeSetting::NORMAL)
}

/// The current mode.
#[must_use]
pub fn mode() -> Mode {
    mode_setting().mode
}

/// The switches as they stand.
#[must_use]
pub fn switches() -> Switches {
    *SWITCHES.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Apply `event` and store the vanilla flag it implies. Returns the switches before and after.
fn apply(event: Event) -> (Switches, Switches) {
    let mut switches = SWITCHES.lock().unwrap_or_else(PoisonError::into_inner);
    let before = *switches;
    let after = before.after(event);
    *switches = after;
    MODE.store(after.mode.encode(), Ordering::Release);
    // A swap rather than a store: if a detour's `trip_vanilla` stored true just before, the swap
    // reads that store and so sees its `HOOK_FAULTED` below; if it stores after, it wins anyway.
    VANILLA.swap(after.vanilla(false), Ordering::AcqRel);
    if HOOK_FAULTED.load(Ordering::Acquire) {
        VANILLA.store(true, Ordering::Release);
    }
    (before, after)
}

/// The mode export: switch to `setting` and return the setting before. The user's vanilla and a
/// hook fault still hold whatever the mode says. The caller restages, so the mode's look and
/// stages apply without waiting for the menu's next present.
pub fn set_mode(setting: ModeSetting) -> ModeSetting {
    apply(Event::SetMode(setting)).0.mode
}

/// The menu's Vanilla checkbox. Returns whether vanilla is now what was asked: clearing it is
/// refused after a hook fault.
pub fn set_vanilla(on: bool) -> bool {
    apply(Event::Checkbox(on));
    VANILLA.load(Ordering::Acquire) == on
}

/// The panel's compare key went down (`true`) or came up. Coming up clears only the key's own
/// request: a checkbox, a panic chord or a mode export made while it was held still stands.
pub fn set_compare(held: bool) {
    apply(Event::Compare(held));
}

/// The panic chord. Returns whether vanilla was already set.
pub fn panic_vanilla() -> bool {
    let already = VANILLA.load(Ordering::Acquire);
    apply(Event::PanicChord);
    already
}

#[cfg(windows)]
pub use process::{panic_chord, report_faults, spawn_panic_thread};

/// The panic chord's thread and the fault report.
#[cfg(windows)]
mod process {
    use std::sync::LazyLock;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::time::Duration;

    use er_hotkey_config::keys::{MODIFIER_CTRL, MODIFIER_SHIFT};
    use er_hotkey_config::{Chord, parse_chord};

    use super::{Edge, PANIC_CHORD, PANIC_POLL_MS, panic_vanilla};
    use crate::cmdlist::{self, Detour, FAULTS, HOOK_FAULTED};
    use crate::{log, menu};

    /// F6, for the fallback chord.
    const VK_F6: u32 = 0x75;

    static CHORD: LazyLock<Chord> = LazyLock::new(|| {
        parse_chord(PANIC_CHORD).unwrap_or(Chord {
            modifiers: MODIFIER_CTRL | MODIFIER_SHIFT,
            vk: VK_F6,
            dik: None,
        })
    });

    /// The panic chord, parsed.
    #[must_use]
    pub fn panic_chord() -> Chord {
        *CHORD
    }

    /// Start the thread that polls the panic chord. It runs for the life of the process.
    pub fn spawn_panic_thread() {
        let spawned = std::thread::Builder::new()
            .name("er-cel-shade-panic".to_owned())
            .spawn(|| {
                let chord = panic_chord();
                let mut edge = Edge::default();
                // Paced like the worker: a channel nothing sends on.
                let (_tick_tx, tick_rx) = std::sync::mpsc::channel::<()>();
                loop {
                    // The chord first: the focus check is two window queries, and the chord is
                    // almost never down.
                    let held = menu::chord_held(chord) && menu::game_has_focus();
                    if edge.pressed(held) {
                        let already = panic_vanilla();
                        log(format_args!(
                            "panic: vanilla ({PANIC_CHORD} pressed{}); every pipeline is bound as \
                             the game asked until the menu's Vanilla checkbox is cleared",
                            if already { ", already vanilla" } else { "" }
                        ));
                    }
                    let _ = tick_rx.recv_timeout(Duration::from_millis(PANIC_POLL_MS));
                }
            });
        if let Err(error) = spawned {
            log(format_args!(
                "panic chord: thread not started ({error}); {PANIC_CHORD} does nothing this \
                 session, the menu's Vanilla checkbox still works"
            ));
        }
    }

    /// The fault bits already logged.
    static REPORTED: AtomicU32 = AtomicU32::new(0);

    /// Log the detours that caught a panic, once per newly faulted detour. For the worker: the
    /// detours themselves only set atomics, and the panic hook has already logged each message.
    pub fn report_faults() {
        if !HOOK_FAULTED.load(Ordering::Acquire) {
            return;
        }
        let faults = FAULTS.load(Ordering::Acquire);
        let before = REPORTED.swap(faults, Ordering::AcqRel);
        if faults & !before == 0 {
            return;
        }
        let names: Vec<&str> = cmdlist::faulted(faults).map(Detour::name).collect();
        log(format_args!(
            "hook fault: vanilla until the game restarts; detours that caught a panic: {}",
            names.join(", ")
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::builtin_presets;

    /// The `cel_*` names a port source reads in code: comments and `#include` lines skipped.
    fn cel_names(source: &str) -> Vec<String> {
        let mut code = String::new();
        let mut rest = source;
        // Block comments first, so a `//` inside one cannot hide the code after it.
        while let Some(start) = rest.find("/*") {
            code.push_str(&rest[..start]);
            rest = rest[start..]
                .find("*/")
                .map_or("", |end| &rest[start + end + 2..]);
        }
        code.push_str(rest);
        let mut names = Vec::new();
        for line in code.lines() {
            let line = line.split("//").next().unwrap_or("");
            if line.trim_start().starts_with("#include") {
                continue;
            }
            let mut word = String::new();
            for c in line.chars().chain([' ']) {
                if c.is_ascii_alphanumeric() || c == '_' {
                    word.push(c);
                    continue;
                }
                if word.starts_with("cel_") && !names.contains(&word) {
                    names.push(word.clone());
                }
                word.clear();
            }
        }
        names
    }

    #[test]
    fn each_knob_belongs_to_the_stage_whose_port_reads_it() {
        // The port sources, each with the stage its blobs are redirected under: the AO source
        // compiles both AO ports, the tonemap source both tonemaps.
        let ports = [
            (
                "light_deferred_ao.hlsl",
                include_str!("../shaders/light_deferred_ao.hlsl"),
                Stage::Lighting,
            ),
            (
                "light_cmb_albedo.hlsl",
                include_str!("../shaders/light_cmb_albedo.hlsl"),
                Stage::Albedo,
            ),
            (
                "tonemap.hlsl",
                include_str!("../shaders/tonemap.hlsl"),
                Stage::Tonemap,
            ),
        ];
        let mut derived: Vec<Vec<Stage>> = vec![Vec::new(); KNOB_COUNT];
        for (file, source, stage) in ports {
            let names = cel_names(source);
            // Only the AO port still reads knobs; the other two run the game's maths alone.
            assert_eq!(
                names.is_empty(),
                stage != Stage::Lighting,
                "{file}: {names:?}"
            );
            for name in names {
                let key = name.trim_start_matches("cel_");
                let index = KNOBS
                    .iter()
                    .position(|knob| knob.key == key)
                    .unwrap_or_else(|| panic!("{file} reads {name}, which is no knob"));
                derived[index].push(stage);
            }
        }
        for (index, knob) in KNOBS.iter().enumerate() {
            assert_eq!(
                derived[index],
                vec![KNOB_STAGES[index]],
                "{} is read by {:?}",
                knob.key,
                derived[index]
            );
        }
        // Written out as well, so the table cannot drift from the design's map with the shaders.
        let by_name: Vec<(&str, Stage)> = KNOBS
            .iter()
            .zip(KNOB_STAGES)
            .map(|(knob, stage)| (knob.key, stage))
            .collect();
        assert_eq!(
            by_name,
            vec![
                ("light_band_stops", Stage::Lighting),
                ("outline_ink", Stage::Lighting),
                ("outline_width", Stage::Lighting),
                ("outline_depth", Stage::Lighting),
                ("outline_crease", Stage::Lighting),
            ]
        );
    }

    #[test]
    fn the_scan_skips_comments_and_includes() {
        let source = "#include \"cel_look.hlsli\"\n// cel_saturation\n/* cel_outline_ink\n */ \
                      float a = cel_paint_radius + cel_paint_radius; // cel_colour_levels\n";
        assert_eq!(cel_names(source), vec!["cel_paint_radius".to_owned()]);
    }

    /// A value inside the knob's range that is not its off value.
    fn moved(index: usize) -> f32 {
        let knob = KNOBS[index];
        if knob.off == knob.max {
            knob.min
        } else {
            knob.max
        }
    }

    #[test]
    fn stage_active_truth_table() {
        assert_eq!(ALL_STAGES, 0b111);
        assert_eq!(stages_with_content(&Look::vanilla(), true), 0);
        assert_eq!(stages_with_content(&Look::vanilla(), false), 0);

        // One knob away from off: its stage alone, and only while cel is on.
        for index in 0..KNOB_COUNT {
            let mut look = Look::vanilla();
            look.0[index] = moved(index);
            assert_eq!(
                stages_with_content(&look, true),
                KNOB_STAGES[index].bit(),
                "{}",
                KNOBS[index].key
            );
            assert_eq!(stages_with_content(&look, false), 0, "{}", KNOBS[index].key);
        }

        // The built-in presets, by hand: the knobs give Lighting alone, whatever the layers do;
        // Painterly has outlines and no bands, Vanilla nothing.
        let expected = [
            ("Wind Waker", 0b001),
            ("Bright toon", 0b001),
            ("Soft cel", 0b001),
            ("Comic ink", 0b001),
            ("Painterly", 0b001),
            ("Vanilla", 0),
        ];
        let presets = builtin_presets();
        assert_eq!(presets.len(), expected.len());
        for (preset, (name, bits)) in presets.iter().zip(expected) {
            assert_eq!(preset.name, name);
            assert_eq!(stages_with_content(&preset.look, true), bits, "{name}");
        }

        // What the shaders see decides: a value that clamps or rounds back to off is no content,
        // and an unreadable one is the knob's minimum.
        let mut look = Look::vanilla();
        look.0[2] = 0.5; // outline_width clamps to 1
        look.0[0] = f32::NAN; // light_band_stops becomes its minimum, 0
        assert_eq!(stages_with_content(&look, true), 0);
        look.0[3] = f32::NAN; // outline_depth becomes 0.005, away from 0.04
        assert_eq!(stages_with_content(&look, true), Stage::Lighting.bit());
    }

    #[test]
    fn each_mode_stages_its_look_and_stages() {
        let wind_waker = builtin_presets()[0].look;
        let comic_ink = builtin_presets()[3].look;
        let all = u32::from(PortKind::ALL_BITS);
        for look in [wind_waker, comic_ink, Look::vanilla()] {
            for enabled in [false, true] {
                for unfaithful in [0, PortKind::AoSsr.bit(), PortKind::ALL_BITS] {
                    for mode in Mode::ALL {
                        let staged = staged(
                            mode.into(),
                            &look,
                            enabled,
                            unfaithful,
                            0,
                            VertexSwitches::default(),
                        );
                        match mode {
                            Mode::Fidelity => {
                                assert_eq!(staged.stages, ALL_STAGES);
                                assert_eq!(staged.look, Look::vanilla().gpu_block(true));
                                // The whole test redirects every kind, marked or not.
                                assert_eq!(staged.ports_off, 0);
                            }
                            Mode::Normal | Mode::Vanilla => {
                                assert_eq!(staged.stages, stages_with_content(&look, enabled));
                                assert_eq!(staged.look, look.gpu_block(enabled));
                                assert_eq!(staged.ports_off, u32::from(unfaithful));
                            }
                        }
                    }
                    // A test of one kind turns every other kind off, whatever is marked.
                    for kind in PortKind::ALL {
                        let one = staged(
                            ModeSetting::fidelity(kind.bit()),
                            &look,
                            enabled,
                            unfaithful,
                            0,
                            VertexSwitches::default(),
                        );
                        assert_eq!(one.ports_off, all & !u32::from(kind.bit()), "{kind:?}");
                        assert_eq!(one.stages, ALL_STAGES);
                    }
                }
            }
        }
    }

    #[test]
    fn editor_layers_give_their_stage_content_whatever_cel_says() {
        let comic_ink = builtin_presets()[3].look;
        let lighting = Stage::Lighting.bit();
        let albedo = Stage::Albedo.bit();
        for (look, enabled, from_cel) in [
            (Look::vanilla(), true, 0),
            (comic_ink, true, 0b001),
            // Cel turned off leaves the layers' stages on.
            (comic_ink, false, 0),
        ] {
            for layers in [0, albedo, albedo | lighting, ALL_STAGES] {
                for mode in [Mode::Normal, Mode::Vanilla] {
                    let staged = staged(
                        mode.into(),
                        &look,
                        enabled,
                        0,
                        layers,
                        VertexSwitches::default(),
                    );
                    assert_eq!(staged.stages, from_cel | layers, "{layers:#b} {mode:?}");
                    // The layers move no knob.
                    assert_eq!(staged.look, look.gpu_block(enabled));
                }
                // A fidelity test redirects every stage anyway.
                let fidelity = staged(
                    Mode::Fidelity.into(),
                    &look,
                    enabled,
                    0,
                    layers,
                    VertexSwitches::default(),
                );
                assert_eq!(fidelity.stages, ALL_STAGES);
            }
        }
        // Bits past the three stages and the vertex stage are not stages.
        let stray = staged(
            Mode::Normal.into(),
            &Look::vanilla(),
            true,
            0,
            0b1_0000,
            VertexSwitches::default(),
        );
        assert_eq!(stray.stages, 0);
    }

    #[test]
    fn the_vertex_stage_follows_its_layers_its_hold_and_never_a_fidelity_test() {
        let vanilla = Look::vanilla();
        let off = VertexSwitches::default();
        let stage = |mode: Mode, layers: u32, vertex: VertexSwitches| {
            staged(mode.into(), &vanilla, true, 0, layers, vertex).stages
        };
        assert_eq!(stage(Mode::Normal, VERTEX_STAGE_BIT, off), VERTEX_STAGE_BIT);
        assert_eq!(
            stage(Mode::Vanilla, VERTEX_STAGE_BIT, off),
            VERTEX_STAGE_BIT
        );
        let hold = VertexSwitches {
            proof: false,
            hold_off: true,
        };
        assert_eq!(stage(Mode::Normal, VERTEX_STAGE_BIT, hold), 0);
        // The proof binds the AO port, which writes it.
        let proof = VertexSwitches {
            proof: true,
            hold_off: false,
        };
        assert_eq!(
            stage(Mode::Normal, VERTEX_STAGE_BIT, proof),
            VERTEX_STAGE_BIT | Stage::Lighting.bit()
        );
        assert_eq!(stage(Mode::Fidelity, VERTEX_STAGE_BIT, proof), ALL_STAGES);
    }

    #[test]
    fn modes_number_as_the_export_takes_them() {
        for mode in Mode::ALL {
            assert_eq!(Mode::from_u32(mode as u32), Some(mode));
            let setting = ModeSetting::from(mode);
            assert_eq!(setting.encode(), mode as u32, "{mode:?}");
            assert_eq!(ModeSetting::decode(mode as u32), Some(setting));
        }
        assert_eq!(Mode::from_u32(3), None);
        assert_eq!(Mode::from_u32(u32::MAX), None);
        let names: Vec<&str> = Mode::ALL.iter().map(|m| m.name()).collect();
        assert_eq!(names, ["normal", "vanilla", "fidelity"]);

        // A fidelity test of some kinds: the kinds in the second byte, read back as written.
        for kinds in 1..PortKind::ALL_BITS {
            let value = 2 | u32::from(kinds) << MODE_KINDS_SHIFT;
            let setting = ModeSetting::decode(value).expect("a fidelity test");
            assert_eq!(setting.mode, Mode::Fidelity);
            assert_eq!(setting.kinds, kinds);
            assert_eq!(setting.encode(), value, "{kinds:#b}");
        }
        // Every kind, spelled out or not, is plain 2.
        assert_eq!(
            ModeSetting::decode(0x1f02).map(ModeSetting::encode),
            Some(2)
        );
        assert_eq!(
            ModeSetting::decode(2).map(|s| s.kinds),
            Some(PortKind::ALL_BITS)
        );
        // Kinds for another mode, a kind that does not exist, or anything past the kind byte.
        for refused in [0x0100, 0x0101, 0x2002, 0x8002, 0x1_0002, 3, u32::MAX] {
            assert_eq!(ModeSetting::decode(refused), None, "{refused:#x}");
        }
    }

    #[test]
    fn each_request_for_vanilla_is_cleared_only_by_its_own_switch() {
        let normal = ModeSetting::from(Mode::Normal);
        let vanilla = ModeSetting::from(Mode::Vanilla);
        let fidelity_all = ModeSetting::from(Mode::Fidelity);
        let start = Switches::START;
        assert!(!start.vanilla(false));
        assert!(
            start.vanilla(true),
            "a hook fault is vanilla whatever else says"
        );

        // The mode alone.
        let mode1 = start.after(Event::SetMode(vanilla));
        assert!(mode1.vanilla(false));
        assert!(!mode1.after(Event::SetMode(normal)).vanilla(false));
        assert!(!mode1.after(Event::SetMode(fidelity_all)).vanilla(false));

        // The panic chord is one-way: no mode clears it.
        let panicked = start.after(Event::PanicChord);
        assert!(panicked.vanilla(false));
        for mode in Mode::ALL {
            assert!(
                panicked.after(Event::SetMode(mode.into())).vanilla(false),
                "{mode:?}"
            );
        }
        // The checkbox clears it.
        assert!(!panicked.after(Event::Checkbox(false)).vanilla(false));
        assert_eq!(panicked.after(Event::Checkbox(true)), panicked);

        // Clearing the checkbox in mode 1 returns the mode to normal, and leaves a fidelity test,
        // its kinds included, alone.
        let cleared = mode1.after(Event::Checkbox(false));
        assert_eq!(cleared.mode, normal);
        assert!(!cleared.vanilla(false));
        let one_kind = ModeSetting::fidelity(PortKind::Tonemap.bit());
        let fidelity = start
            .after(Event::SetMode(one_kind))
            .after(Event::Checkbox(true));
        assert!(fidelity.vanilla(false));
        let fidelity = fidelity.after(Event::Checkbox(false));
        assert_eq!(fidelity.mode, one_kind);
        assert!(!fidelity.vanilla(false));

        // Nothing clears a hook fault.
        for event in [
            Event::SetMode(normal),
            Event::SetMode(fidelity_all),
            Event::Checkbox(false),
            Event::Compare(false),
        ] {
            assert!(start.after(event).vanilla(true), "{event:?}");
        }
    }

    #[test]
    fn releasing_the_compare_key_clears_only_what_the_key_set() {
        let held = Switches::START.after(Event::Compare(true));
        assert!(held.vanilla(false));
        assert!(!held.after(Event::Compare(false)).vanilla(false));

        // The panic chord while the key is held outlasts the key.
        let panicked = held.after(Event::PanicChord).after(Event::Compare(false));
        assert!(panicked.vanilla(false));
        assert!(panicked.user_vanilla && !panicked.compare);

        // So does a mode 1 export, and the export's mode is left as it was.
        let vanilla = ModeSetting::from(Mode::Vanilla);
        let exported = held
            .after(Event::SetMode(vanilla))
            .after(Event::Compare(false));
        assert!(exported.vanilla(false));
        assert_eq!(exported.mode, vanilla);

        // The checkbox cleared while the key is held leaves the key's request until it comes up.
        let cleared = Switches::START
            .after(Event::Checkbox(true))
            .after(Event::Compare(true))
            .after(Event::Checkbox(false));
        assert!(cleared.vanilla(false));
        assert!(!cleared.after(Event::Compare(false)).vanilla(false));
    }

    #[test]
    fn a_held_key_presses_once() {
        let mut edge = Edge::default();
        let polls = [false, true, true, true, false, true, false, false, true];
        let presses: Vec<bool> = polls.iter().map(|held| edge.pressed(*held)).collect();
        assert_eq!(
            presses,
            [false, true, false, false, false, true, false, false, true]
        );
    }

    #[test]
    fn every_menu_label_is_ascii() {
        // The overlay's font has glyphs for ASCII, Latin-1 and Latin Extended-A and general
        // punctuation only, so anything else draws as a box. The panel's text lives in `menu/`
        // and in the settings; `menu`'s own test holds every literal there to the font's ranges,
        // and these files are plain ASCII throughout.
        for source in [
            include_str!("menu/mod.rs"),
            include_str!("menu/code.rs"),
            include_str!("menu/diagnostics_tab.rs"),
            include_str!("menu/files.rs"),
            include_str!("menu/hunt_tab.rs"),
            include_str!("menu/layers_tab.rs"),
            include_str!("menu/look_tab.rs"),
            include_str!("menu/panel_record.rs"),
            include_str!("menu/widgets.rs"),
            include_str!("settings.rs"),
            // The look the panel draws in, which lives in the shared theme crate.
            include_str!("../../er-overlay-theme/src/lib.rs"),
            include_str!("../../er-overlay-theme/src/draw.rs"),
        ] {
            assert!(source.is_ascii());
        }
    }
}
