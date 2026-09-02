//! `er-npc-possess.toml`, written on first run and re-read while the game runs.
//!
//! Same shape as `er-enemynpc-effects` and `er-refill-all`, for the same reasons: the file lives
//! in the game's own directory (the process CWD), the DLL writes a fully commented default the
//! first time it cannot find one, and `er_hotkey_config::HotFile` notices edits by comparing the
//! file's TEXT rather than its mtime -- mtime has one-second resolution on the filesystems a Wine
//! prefix sits on, so two saves inside a second are invisible to it, which reads as "changing the
//! key did nothing".
//!
//! # The one thing this file does that the other two do not
//!
//! **`[target]` is not live.** Everything else here takes effect on the next reload, about a
//! second after the file is saved. `[target]` decides WHO you are about to become, and moving that
//! while a possession is in flight would leave the mapping, the camera and the moveset describing
//! a different character than the body on screen. So an edit to it is STAGED: the value on disk is
//! remembered, the value IN FORCE does not move, and [`adopt_staged_target`] promotes one to the
//! other at the next possession. The log says `[target] staged` rather than `[target] now`, so
//! nobody is left believing an edit took that has not.
//!
//! # The second file
//!
//! A later layer writes `er-npc-possess.derived.toml` -- one line per animation, auto-classified,
//! for the player to correct. It is NOT implemented here, and nothing below assumes there is only
//! one file: [`PossessConfig::apply`] takes text rather than a path, the watcher is a field rather
//! than a global, and [`DERIVED_CONFIG_FILE_NAME`] is already spelled out. Adding it is a second
//! `HotFile` in [`ConfigState`] and a second `apply`, not a rewrite.

// Windows-only crate in practice; this module is pure text handling and stays ungated so its
// tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{
    fs,
    path::PathBuf,
    sync::{
        Mutex, MutexGuard, OnceLock,
        atomic::{AtomicUsize, Ordering},
    },
};

use er_hotkey_config::{
    Binding, BindingUpdate, FileChange, HotFile, chord_name,
    keys::{Chord, KeyParseError, parse_chord},
    pad::{PadChord, pad_chord_name, parse_pad_chord},
};

use crate::{
    engine::PossessionRequest,
    log::possess_log,
    settings::{CameraSettings, ChrOverride, MovementSettings, Rejections, Tables, TargetSettings},
    toml::Document,
};

const CONFIG_FILE_NAME: &str = "er-npc-possess.toml";

/// The same name, for log lines that have to tell the player which of the two files they should
/// be typing in. Separate so the private constant stays private to the path logic.
pub(crate) const CONFIG_FILE_NAME_FOR_LOG: &str = CONFIG_FILE_NAME;

/// The auto-classified moveset table a later layer WRITES and the player then edits. Named here
/// so the second file has a spelling before it has an implementation; nothing reads it yet.
pub(crate) const DERIVED_CONFIG_FILE_NAME: &str = "er-npc-possess.derived.toml";

const DEFAULT_HOTKEY: &str = "F9";
/// Both sticks clicked. Not a vanilla pair -- L3 and R3 each do something on their own, and this
/// DLL does not suppress either, so the individual actions still happen.
const DEFAULT_GAMEPAD_HOTKEY: &str = "ls+rs";
const DEFAULT_RADIAL: &str = "DPadDown";

const DEFAULT_CONFIG_TOML: &str = r##"# er-npc-possess.toml -- the standalone "become an NPC" DLL.
#
# EDITS TAKE EFFECT WHILE THE GAME RUNS. This file is re-read about once a second; there is no
# need to restart, and the log names the old value and the new one each time one moves. The one
# exception is the whole [target] table, which is explained at that table.
#
# WHAT WORKS TODAY. Press the hotkey and you BECOME the character [target] selects: the camera and
# lock-on move to it, your own body goes invisible, silent, invincible and unable to attack, and it
# is carried along with the creature every frame so other players see you standing where it stands
# and releasing drops you exactly there. The left stick walks the creature. Press again -- or let
# it die -- to get out.
#
# ATTACKING. Four buttons, the same on every creature: R1 light, R2 heavy, L1 ranged, L2 movement
# (on mouse and keyboard, the game's own default: click, shift+click, right click, shift+right
# click). Which attack you get depends on how far away the nearest enemy is and how many times you
# have pressed the button in a row -- see [mapping]. Every creature's attacks were classified
# offline; er-npc-possess.derived.toml is written each time you possess something and lists what
# that creature can do and, for anything withheld, exactly why.
#
# WHAT IS NOT WIRED YET, so you can tell a missing feature from a broken one:
#   * The `radial` binding is parsed and reported and there is no wheel to open.
#   * Range is measured to the nearest enemy, NOT to what you are locked on to.
#   * GRABS. No creature's grab can be fired -- see allow_grabs below. Dodges CAN: they are
#     fired under their real W_Step name rather than through the field write.
#   * Your own body stays LOCK-ON-ABLE while you are away from it. It cannot be hurt and cannot be
#     seen, so this is an oddity rather than a hazard.
#   * turn_deadzone_deg, heading_converge and root_motion_only are RESERVED. speed_scale is live.
#
# A value this file does not understand is REPORTED AND IGNORED, and the last value that worked
# stays in force. A typo never leaves you with no hotkey and never silently resets a setting.

# LIVE. Master switch. false leaves the DLL loaded and inert: neither hotkey fires.
enabled = true

# LIVE. Keyboard binding. Press to possess what you are targeting, press again to release.
# Modifiers ctrl/alt/shift plus one key, e.g. "ctrl+shift+p". Empty means no keyboard binding.
# The same key names as every other er-* DLL in this profile:
#
#   A..Z, 0..9, F1..F15
#   Insert Delete Home End PageUp PageDown Backspace Tab Enter Escape Space
#   Left Up Right Down PrintScreen ScrollLock NumLock Pause CapsLock
#   punctuation by symbol or name: - = [ ] \ ; ' , . / `  (Minus, Equals, LeftBracket,
#     RightBracket, Backslash, Semicolon, Quote, Comma, Period, Slash, Grave)
#   keypad: KP_0..KP_9, KP_Plus, KP_Minus, KP_Multiply, KP_Divide, KP_Period, KP_Enter
hotkey = "F9"

# LIVE. Controller binding for the same thing. Every named button must be held together; order
# does not matter, and holding other buttons as well is fine. Empty disables it.
#
#   select (back, share, view)   start (options, menu)
#   a/cross  b/circle  x/square  y/triangle
#   lb/l1  rb/r1  ls/l3  rs/r3
#   dpad_up  dpad_down  dpad_left  dpad_right
#
# These buttons are NOT suppressed, so whatever they do in vanilla still happens as well.
gamepad_hotkey = "ls+rs"

# LIVE BINDING, RESERVED BEHAVIOUR. Hold for the possessed character's attack wheel. The binding
# is read and re-bound live today and the log names it; there is no wheel to open until the
# mapping layer lands.
radial = "DPadDown"

# THE ONLY TABLE HERE THAT IS NOT LIVE.
#
# It decides who you are about to become. Changing it mid-possession would leave the mapping, the
# camera and the moveset belonging to a different character than the body on screen, so an edit
# is STAGED and takes effect at the NEXT possession -- release and press again. The log says
# "[target] staged" when it reads one, and "[target] adopted" when it takes.
[target]
# lock_on   -- whatever you are locked on to
# nearest   -- the closest character that passes the selection filter
# crosshair -- whatever the middle of the screen is pointed at. NOT IMPLEMENTED YET: it currently
#              behaves exactly like "nearest", because aiming by camera needs the camera's forward
#              vector and that is not reversed. The spelling is kept so this file does not have to
#              change when it lands.
# chr_id    -- the literal chr_id below, ignoring where you are looking
# spawn     -- CREATE the creature [spawn] names, in front of you, and become that. The only mode
#              that puts something new in the world rather than borrowing something already there.
mode = "lock_on"
# Used only when mode = "chr_id". This is an NpcParam row, e.g. 45000000 for a Flying Dragon.
chr_id = 0
# Hand the body back when it dies, instead of staying inside a corpse.
release_on_death = true

# USED ONLY WHEN mode = "spawn", above. Staged with [target] and adopted at the next possession,
# for the same reason: it decides who you become.
#
# ANY four-digit creature works. Assets load on demand -- the character's own ChrRes step machine
# goes and fetches its chrbnd, anibnd, behbnd and texbnd -- so you are NOT limited to creatures
# that happen to be loaded near you, and there is no residency check to fail.
#
# WHAT DOES FAIL is an id with no chrbnd on disk at all, and it fails by WAITING: there is no error
# state anywhere in the game's asset step machines, so such a character sits half-built forever
# rather than reporting anything. readiness_ms below is the deadline that turns that into a message;
# when it expires the creature is removed and er-npc-possess.derived.toml says which stage it died
# at and what that means.
[spawn]
# The MODEL number. 4500 becomes c4500. 0..9999.
chr_id = 4500
# The NpcParam row, which drives stats, behaviour and which resources get loaded. 0 derives
# chr_id * 10000 -- 45000000 for c4500 -- which is the row the shipped moveset table is keyed by,
# so leaving this at 0 is the answer that keeps the attacks matching the body.
npc_param_id = 0
# The NpcThinkParam row. Rarely worth setting: an invalid one is not a failure, because the lookup
# pre-initialises its result and the loader treats the resulting empty AI script as satisfied.
npc_think_id = 0
# How far in front of you it appears, in metres. 1 to 50. Not zero: a creature placed exactly on
# you is one you are standing inside for the frame before possession takes.
distance_m = 3.0
# How long to wait for it to become drivable before giving up and removing it. 1000 to 60000 ms.
readiness_ms = 5000
# Take the creature away again when the possession ends. Leaving it is not free -- it is a live NPC
# nothing else will ever remove, and it holds one of the fourteen roster slots the game's own
# dynamic spawner shares -- but it is a real choice and the log says which one was made.
#
# ONE CASE IGNORES THIS, and it is not a bug: if the game is closing (the DLL is being unloaded),
# the creature is left standing. Removing it is a call into the game and that path does not run on
# the game's thread. It gets its own AI back either way and the next map load clears it.
despawn_on_release = true

# LIVE. How one press turns into one of the possessed character's attacks.
[mapping]
# context -- the range band picks which attacks are eligible, then repeated presses walk them in
#            order. The default, and the only model that still means something on a character with
#            thirty attacks and four buttons.
# layered -- range picks a THIRD of the attack list rather than filtering by reach. More
#            predictable, less situationally right.
# slots   -- range is ignored entirely; every press walks the whole bucket. Use this on a creature
#            whose attacks mostly came back with an unknown reach.
model = "context"
# Neutral time after which the attack rank falls back to 0. Standing still resets it too, and
# that is the one that usually fires first.
combo_window_ms = 1200
# close < 4m < mid < 12m < far. Two numbers, both required, near first. Measured to the nearest
# enemy. While you are moving, the band is shifted one step CLOSER, on the reasoning that an
# attack you start mid-run lands after the run has closed some of the gap.
bands_m = [4.0, 12.0]
# Offer the creature's grabs. ON as a policy -- a grab is a real attack and withholding it by
# default would quietly remove the signature move of most bosses.
#
# It currently gates NOTHING, and that is worth knowing rather than discovering. Grab animations
# live in the 4000 band (Malenia's are 4100 and 4101) and NO event name in that band has a
# transition behind it on any creature swept -- not W_Event, not W_Attack, not any prefix. So the
# graph's event layer cannot reach them at all, by either firing path. This is a different wall
# from the dodges, which the by-name path did solve. When a route to them is found, this setting
# is already the right way round.
allow_grabs = true
# promote -- an input whose bucket is empty borrows from another one rather than doing nothing.
#            It drops the RANGE requirement first and only then changes bucket, so the button
#            keeps meaning what it says for as long as possible.
# deny    -- it stays dead
unbound_inputs = "promote"
# Softlock guard. If the creature is animating, going nowhere, and you have not asked for
# anything for this long, the animation is forced back to idle and never offered again this
# session -- and it is written into er-npc-possess.derived.toml so you can see which one it was.
# Some animations only exit on a condition the AI would have met, and the AI is what possession
# switches off.
watchdog_seconds = 4.0

# LIVE. Which bucket each input draws from. Identical on every character, which is the point:
# the same button means the same KIND of thing whoever you are wearing.
# Buckets: light, heavy, ranged, movement.
#
# l2 is the creature's own dodges and steps, animation 6000-6023. The graph never names those
# W_Event -- only W_Step -- so they are fired by name rather than by the field write this mod
# prefers. That is the one place the mod calls a game function. A creature with no fireable step
# gets nothing in this bucket and l2 promotes to an attack instead.
[buttons]
r1 = "light"
r2 = "heavy"
l1 = "ranged"
l2 = "movement"

# How the possessed body moves. LIVE -- an edit applies within a second, mid-possession.
[movement]
# RESERVED. Turn toward the stick over time instead of snapping to it. The body currently turns
# toward wherever it has been told to walk, at the rate its own NpcParam gives it.
heading_converge = true
# RESERVED. Stick deflection inside this cone counts as "no turn asked for". 0..180.
turn_deadzone_deg = 20.0
# RESERVED. Move only by the animation's own root motion, never by writing a velocity. This is
# already how it works -- the mod asks the character's own AI to walk somewhere and the engine's
# locomotion does the rest -- so there is nothing yet for the "false" setting to mean.
root_motion_only = true
# LIVE. How far ahead of itself the possessed character is told to walk, as a multiplier. Higher
# is a longer stride between re-aims and a body that runs on further after you let go; lower is
# twitchier and stops sooner.
speed_scale = 1.0

# How the camera frames the creature you are wearing. LIVE -- save the file and the framing moves
# about a second later, mid-possession, without letting go.
#
# Vanilla's follow camera is tuned for a 1.8 m Tarnished, so wearing an Ancient Dragon puts the
# camera inside the model. The mod reads the creature's OWN physics capsule height and writes
# three numbers -- distance, pivot height and how far the camera may pitch down -- into a
# LockCamParam row nothing else in the game references, for the length of the possession. Every
# byte goes back on release. At player size the numbers it derives ARE the vanilla ones, so
# possessing something human-sized looks like nothing happened.
#
# er-npc-possess.derived.toml records what it read, what it wrote, and -- if it did not -- why.
[camera]
# The off switch. false frames every creature the way your own body would be framed.
enabled = true
# Which LockCamParam row is patched in memory. 1000 is one of 73 rows the shipped regulation never
# references. If you run a regulation mod that DOES use it, the mod refuses rather than stealing it
# and says so in the log; pick another free row here. It must already exist as a row -- an id the
# table does not contain makes the engine's camera update do nothing at all.
param_row = 1000
# How hard the camera backs off as the creature gets bigger: distance = 3.8 * (height / 1.5) ^ this.
# 0 pins it at your own camera's distance whatever you are wearing; 1 scales it straight with
# height, which puts a Fire Giant's camera 73 m out. 0..2.
distance_exponent = 0.7
# Metres. A ceiling on the formula above -- but NOT on the clearance the camera keeps over the
# creature's own collision radius, because a camera far away is a bad shot and a camera inside the
# model is no shot at all.
distance_max = 40.0

# Per-character overrides, one table per chr id, added as you need them. The DLL reads whatever ids
# this file happens to name -- there is no fixed list -- so a chr nobody has written down yet works
# the moment you add its table. camera_distance_scale, pin, unusable and usable are LIVE;
# turn_rate_deg_per_sec and speed_scale are RESERVED.
#
# Left commented out on purpose: the ids below are an EXAMPLE of the shape, not a tuned profile,
# and a shipped default that pins real animation ids for a character nobody has measured would be
# a lie the first player to read it would take at face value. Uncomment and edit.
#
# [chr.c4500]
# turn_rate_deg_per_sec = 45.0
# # LIVE. Multiplies [camera].distance_exponent's answer for this character only. Above 1 pulls
# # the camera back -- quadrupeds and anything wider than it is tall usually want it -- below 1
# # brings it in. It cannot push the camera inside the creature's own collision radius.
# camera_distance_scale = 2.4
# speed_scale = 1.15
# # force one input onto one animation id
# pin = { r2 = 3046 }
# # animation ids this character must never be given
# unusable = [3300, 3301]
# # ...and ids to force back in, overriding the auto-classifier
# usable = [3120]
"##;

/// The settings in force, and nothing that needs Windows -- so the whole reload decision is
/// `cargo test`-able on the host.
#[derive(Clone, Debug)]
pub(crate) struct PossessConfig {
    pub(crate) config_path: PathBuf,
    pub(crate) enabled: bool,
    keyboard: Binding<Option<Chord>>,
    pub(crate) keyboard_text: String,
    /// Not an `er_hotkey_config::Binding`: that type's parser must fail with `KeyParseError`,
    /// whose `Unknown` message tells the reader to pick a KEY -- and listing keyboard names at
    /// somebody who mistyped a pad button is a worse answer than none. The keep-the-last-working
    /// -value rule it exists to enforce is reimplemented in [`Self::apply_pad`], which is the
    /// half that matters. Same call `er-refill-all` makes.
    gamepad: PadChord,
    pub(crate) gamepad_text: String,
    radial: PadChord,
    pub(crate) radial_text: String,
    /// `[target]` as the possession engine would see it right now.
    target_in_force: TargetSettings,
    /// `[target]` as the FILE says. Differs from the above between an edit and the next
    /// possession; see the module docs.
    target_on_disk: TargetSettings,
    pub(crate) tables: Tables,
}

/// The per-frame slice of the config: four Copy values, so the input path does not clone the
/// per-character override tables sixty times a second.
#[derive(Clone, Copy, Debug)]
pub(crate) struct LiveBindings {
    pub(crate) enabled: bool,
    pub(crate) keyboard: Option<Chord>,
    pub(crate) gamepad: PadChord,
    pub(crate) radial: PadChord,
}

/// What re-reading the file did, in the terms the log line needs. `Default` is "nothing moved",
/// which is what almost every poll produces.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct ConfigUpdate {
    pub(crate) enabled_moved: Option<(bool, bool)>,
    /// `(old name, new name)`. THIS is the edge-reset signal for the keyboard.
    pub(crate) keyboard_moved: Option<(String, String)>,
    pub(crate) keyboard_rejected: Option<(String, String)>,
    pub(crate) gamepad_moved: Option<(String, String)>,
    pub(crate) gamepad_rejected: Option<(String, String)>,
    pub(crate) radial_moved: Option<(String, String)>,
    pub(crate) radial_rejected: Option<(String, String)>,
    /// `[target]` changed ON DISK and is waiting for the next possession. NOT in force.
    pub(crate) target_staged: Option<(String, String)>,
    /// One of the live tables moved; carries the whole new summary rather than a per-field diff,
    /// because these are reserved settings and the useful log line is "here is what is in force
    /// now", not "combo_window_ms went from 1200 to 800".
    pub(crate) tables_moved: Option<String>,
    pub(crate) rejections: Rejections,
}

impl ConfigUpdate {
    /// Did anything move? A poll that changed nothing must produce no log line at all.
    pub(crate) fn is_quiet(&self) -> bool {
        self.enabled_moved.is_none()
            && self.keyboard_moved.is_none()
            && self.keyboard_rejected.is_none()
            && self.gamepad_moved.is_none()
            && self.gamepad_rejected.is_none()
            && self.radial_moved.is_none()
            && self.radial_rejected.is_none()
            && self.target_staged.is_none()
            && self.tables_moved.is_none()
            && self.rejections.is_empty()
    }

    /// Did a binding the input path edge-detects on move?
    ///
    /// Reported for the LOG, not as a control signal: `crate::input::Edges::rebind` compares each
    /// of the three bindings against its own latch every tick, which is what stops an edit to the
    /// pad chord from clearing the keyboard latch and manufacturing a press out of a held key.
    /// Routing the reset through one crate-wide boolean is exactly the bug that would reintroduce.
    pub(crate) fn bindings_moved(&self) -> bool {
        self.keyboard_moved.is_some() || self.gamepad_moved.is_some() || self.radial_moved.is_some()
    }
}

/// An empty value is a real setting -- "no keyboard binding" -- not a parse failure.
fn parse_optional_chord(raw: &str) -> Result<Option<Chord>, KeyParseError> {
    if raw.trim().is_empty() {
        return Ok(None);
    }
    parse_chord(raw).map(Some)
}

fn keyboard_name(chord: Option<Chord>) -> String {
    chord.map_or_else(|| "(none)".to_owned(), chord_name)
}

fn default_pad(spelling: &str) -> PadChord {
    parse_pad_chord(spelling).expect("a built-in default pad chord parses")
}

impl PossessConfig {
    fn new(path: PathBuf) -> Self {
        Self {
            config_path: path,
            enabled: true,
            keyboard: Binding::new(parse_optional_chord(DEFAULT_HOTKEY).ok().flatten()),
            keyboard_text: DEFAULT_HOTKEY.to_owned(),
            gamepad: default_pad(DEFAULT_GAMEPAD_HOTKEY),
            gamepad_text: DEFAULT_GAMEPAD_HOTKEY.to_owned(),
            radial: default_pad(DEFAULT_RADIAL),
            radial_text: DEFAULT_RADIAL.to_owned(),
            target_in_force: TargetSettings::default(),
            target_on_disk: TargetSettings::default(),
            tables: Tables::default(),
        }
    }

    pub(crate) fn bindings(&self) -> LiveBindings {
        LiveBindings {
            enabled: self.enabled,
            keyboard: self.keyboard.code(),
            gamepad: self.gamepad,
            radial: self.radial,
        }
    }

    /// The `[target]` table a possession starting NOW would use.
    pub(crate) const fn target(&self) -> TargetSettings {
        self.target_in_force
    }

    /// The `[target]` edit waiting for the next possession, if there is one.
    pub(crate) fn staged_target(&self) -> Option<TargetSettings> {
        (self.target_on_disk != self.target_in_force).then_some(self.target_on_disk)
    }

    /// Promote a staged `[target]` into force. Called at the START of a possession, never during
    /// one. Returns the `(from, to)` summaries when something actually moved.
    pub(crate) fn adopt_staged_target(&mut self) -> Option<(String, String)> {
        let staged = self.staged_target()?;
        let before = self.target_in_force.summary();
        self.target_in_force = staged;
        Some((before, self.target_in_force.summary()))
    }

    /// Everything an engine needs to start a possession.
    pub(crate) fn request(&self) -> PossessionRequest {
        PossessionRequest {
            target: self.target_in_force,
            mapping: self.tables.mapping,
            buttons: self.tables.buttons,
        }
    }

    /// One pad binding, with the keep-the-last-working-value rule spelled out.
    fn apply_pad(
        current: &mut PadChord,
        text: &mut String,
        raw: &str,
        moved: &mut Option<(String, String)>,
        rejected: &mut Option<(String, String)>,
    ) {
        // An empty value is a real setting -- "no controller binding" -- not a typo.
        let parsed = if raw.trim().is_empty() {
            Ok(PadChord::default())
        } else {
            parse_pad_chord(raw)
        };
        match parsed {
            Ok(chord) if chord == *current => {
                // Record the spelling they used, so the status line echoes their file rather than
                // the last spelling of the same chord. NOT a change.
                *text = raw.to_owned();
            }
            Ok(chord) => {
                let before = pad_chord_name(*current);
                *current = chord;
                *text = raw.to_owned();
                *moved = Some((before, pad_chord_name(chord)));
            }
            // A REJECTION IS NOT A CHANGE and the last working chord stays. Not the shipped
            // default -- that would drag somebody back onto a collision they had just escaped.
            Err(error) => {
                *rejected = Some((format!("{raw:?}: {error}"), pad_chord_name(*current)));
            }
        }
    }

    /// Apply one file's text to the settings in force.
    ///
    /// An absent key keeps what is already in force: the built-in default on the first load, the
    /// last good value on a reload. That is what makes deleting a line the same as never having
    /// written it.
    pub(crate) fn apply(&mut self, text: &str) -> ConfigUpdate {
        let doc = Document::parse(text);
        let mut update = ConfigUpdate::default();
        let mut rejections = Rejections::default();

        if let Some(raw) = doc.scalar("", "enabled") {
            match crate::settings::parse_bool(raw) {
                Some(enabled) if enabled != self.enabled => {
                    update.enabled_moved = Some((self.enabled, enabled));
                    self.enabled = enabled;
                }
                Some(_) => {}
                // Not a silent `false`: a misspelled master switch that reads as "off" is
                // indistinguishable from the mod being broken.
                None => rejections.push("enabled", raw),
            }
        }

        if let Some(raw) = doc.scalar("", "hotkey") {
            let before = keyboard_name(self.keyboard.code());
            match self.keyboard.apply(raw, parse_optional_chord) {
                BindingUpdate::Unchanged => self.keyboard_text = raw.to_owned(),
                BindingUpdate::Changed { .. } => {
                    self.keyboard_text = raw.to_owned();
                    update.keyboard_moved = Some((before, keyboard_name(self.keyboard.code())));
                }
                BindingUpdate::Rejected { value, error, kept } => {
                    update.keyboard_rejected =
                        Some((format!("{value:?}: {error}"), keyboard_name(kept)));
                }
            }
        }

        if let Some(raw) = doc.scalar("", "gamepad_hotkey") {
            Self::apply_pad(
                &mut self.gamepad,
                &mut self.gamepad_text,
                raw,
                &mut update.gamepad_moved,
                &mut update.gamepad_rejected,
            );
        }
        if let Some(raw) = doc.scalar("", "radial") {
            Self::apply_pad(
                &mut self.radial,
                &mut self.radial_text,
                raw,
                &mut update.radial_moved,
                &mut update.radial_rejected,
            );
        }

        // [target] is STAGED, never applied. The `on_disk` copy is rebuilt from the value in
        // FORCE rather than from itself, so a table the player deleted returns to the built-in
        // default the same way every other absent setting does.
        let mut on_disk = self.target_in_force;
        on_disk.apply_from(&doc, &mut rejections);
        if on_disk != self.target_on_disk {
            self.target_on_disk = on_disk;
            if on_disk != self.target_in_force {
                update.target_staged = Some((self.target_in_force.summary(), on_disk.summary()));
            }
        }

        let before = self.tables.clone();
        self.tables.apply(&doc, &mut rejections);
        if self.tables != before {
            update.tables_moved = Some(self.tables.summary());
        }
        update.rejections = rejections;
        update
    }
}

/// The live settings plus the watcher that keeps them current.
///
/// The derived moveset file is a SECOND `HotFile` field here when it lands, not a rewrite of this
/// struct -- see the module docs.
struct ConfigState {
    config: PossessConfig,
    hot: HotFile,
}

static CONFIG: OnceLock<Mutex<ConfigState>> = OnceLock::new();

/// Incremented by [`poll_reload`] every time a reload moved something. See [`generation`].
static GENERATION: AtomicUsize = AtomicUsize::new(0);

/// CWD-relative, which is the game directory: ME3 launches `eldenring.exe` from it, so this lands
/// beside `eldenring.exe` next to every other er-* DLL's config.
fn config_path() -> PathBuf {
    PathBuf::from(CONFIG_FILE_NAME)
}

fn state() -> MutexGuard<'static, ConfigState> {
    let state = CONFIG.get_or_init(|| {
        let path = config_path();
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(_) => {
                let _ = fs::write(&path, DEFAULT_CONFIG_TOML);
                DEFAULT_CONFIG_TOML.to_owned()
            }
        };
        let mut hot = HotFile::new(path.clone());
        // Adopt what we just read -- including a default we just WROTE -- so the first poll a
        // second from now is not a spurious reload of text nothing has touched. A reload resets
        // the key edge detectors, and one at that moment is a press nobody made.
        hot.adopt(text.clone());
        let mut config = PossessConfig::new(path);
        let update = config.apply(&text);
        if !update.is_quiet() {
            possess_log(format_args!("config: first read -- {}", describe(&update)));
        }
        Mutex::new(ConfigState { config, hot })
    });
    // A poisoned lock means a previous holder panicked. The settings inside are still a valid
    // config, and refusing to read them would disable the feature over a fault that has happened.
    match state.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

/// Read the config, writing the commented default file when there is not one yet.
pub(crate) fn init_config() -> PossessConfig {
    state().config.clone()
}

/// The four values the per-frame input path needs.
pub(crate) fn bindings() -> LiveBindings {
    state().config.bindings()
}

/// Everything an engine needs to start a possession, plus the staged-`[target]` promotion that
/// happens at exactly that moment.
///
/// Doing both under one lock is the point: the request handed to the engine has to be the one
/// the log line describes, and a promotion that happened between the two would make them differ.
pub(crate) fn take_request() -> (PossessionRequest, Option<(String, String)>) {
    let mut guard = state();
    let adopted = guard.config.adopt_staged_target();
    (guard.config.request(), adopted)
}

/// The `[movement]` table IN FORCE, read fresh.
///
/// A per-frame accessor rather than a field on [`PossessionRequest`], because `[movement]` is a
/// LIVE table: `speed_scale` is the one setting a player tunes by saving the file and watching
/// what changes, and snapshotting it at possession start would make it the one setting that
/// mysteriously needs a re-possess. `[target]` is snapshotted for the opposite reason -- see
/// [`PossessConfig::adopt_staged_target`].
/// The `[chr.cNNNN]` overrides for one creature, or `None` when the file names none.
///
/// Read at possession START rather than snapshotted into [`PossessionRequest`]: the override is a
/// growable `Vec` of pins and animation ids, and `PossessionRequest` is `Copy` on purpose so that
/// the log line describing a possession is a value rather than a borrow of a lock.
pub(crate) fn chr_override(chr_id: u32) -> Option<ChrOverride> {
    let wanted = format!("c{chr_id:04}");
    state()
        .config
        .tables
        .chr_overrides
        .iter()
        .find(|over| over.chr == wanted)
        .cloned()
}

pub(crate) fn movement() -> MovementSettings {
    state().config.tables.movement
}

/// The `[camera]` table IN FORCE.
pub(crate) fn camera() -> CameraSettings {
    state().config.tables.camera
}

/// How many reloads have MOVED something, since the process started.
///
/// The camera layer watches this rather than re-reading its two settings sixty times a second:
/// `[camera]` costs a lock and the per-character `camera_distance_scale` costs a `format!` and a
/// clone, and neither changes between edits to the file. One relaxed atomic load per frame buys
/// the same liveness for nothing. See `camera::Session::refresh`.
pub(crate) fn generation() -> usize {
    GENERATION.load(Ordering::Relaxed)
}

/// Re-read the file if it changed, and report what moved.
///
/// `None` when nothing happened, which is the overwhelmingly common case and the one that must
/// stay silent in the log.
pub(crate) fn poll_reload() -> Option<ConfigUpdate> {
    let mut guard = state();
    let ConfigState { config, hot } = &mut *guard;
    match hot.poll()? {
        FileChange::Text(text) => {
            let update = config.apply(&text);
            if update.is_quiet() {
                return None;
            }
            // Bumped only when something ACTUALLY moved, which is what makes this usable as a
            // "are my settings stale" test rather than a "was the file touched" one.
            GENERATION.fetch_add(1, Ordering::Relaxed);
            Some(update)
        }
        FileChange::Missing => {
            // Keep the settings that were working. A deleted config is not an instruction to
            // unbind the hotkey, and rewriting the default here would fight a player mid-edit.
            possess_log(format_args!(
                "config: {} disappeared; keeping the settings already in force",
                config.config_path.display()
            ));
            None
        }
    }
}

/// One line describing a reload, or nothing when nothing moved.
pub(crate) fn describe(update: &ConfigUpdate) -> String {
    let mut parts = Vec::new();
    if let Some((from, to)) = update.enabled_moved {
        parts.push(format!("enabled {from} -> {to}"));
    }
    for (label, moved) in [
        ("hotkey", &update.keyboard_moved),
        ("gamepad_hotkey", &update.gamepad_moved),
        ("radial", &update.radial_moved),
    ] {
        if let Some((from, to)) = moved {
            parts.push(format!("{label} {from} -> {to}"));
        }
    }
    for (label, rejected) in [
        ("hotkey", &update.keyboard_rejected),
        ("gamepad_hotkey", &update.gamepad_rejected),
        ("radial", &update.radial_rejected),
    ] {
        if let Some((value, kept)) = rejected {
            parts.push(format!("{label} {value}; STAYING ON {kept}"));
        }
    }
    if let Some((from, to)) = &update.target_staged {
        parts.push(format!(
            "[target] STAGED {from} -> {to} (takes effect at the next possession, not now)"
        ));
    }
    if let Some(summary) = &update.tables_moved {
        parts.push(format!("tables now {summary}"));
    }
    if !update.rejections.is_empty() {
        parts.push(format!(
            "unreadable values kept at their previous settings: {}",
            update.rejections.summary()
        ));
    }
    parts.join(" | ")
}

pub(crate) fn log_update(update: &ConfigUpdate) {
    if update.is_quiet() {
        return;
    }
    possess_log(format_args!("config: {}", describe(update)));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::{Bucket, MappingModel, TargetMode, UnboundInputs};

    fn from_default() -> PossessConfig {
        let mut config = PossessConfig::new(PathBuf::from(CONFIG_FILE_NAME));
        config.apply(DEFAULT_CONFIG_TOML);
        config
    }

    /// THE ROUND TRIP. The file the DLL writes on first run must parse back to exactly the
    /// built-in defaults -- otherwise the shipped file silently means something other than what
    /// the code does, and the difference only shows up in somebody's session.
    #[test]
    fn the_shipped_default_file_parses_back_to_the_built_in_defaults() {
        let fresh = PossessConfig::new(PathBuf::from(CONFIG_FILE_NAME));
        let mut loaded = fresh.clone();
        let update = loaded.apply(DEFAULT_CONFIG_TOML);

        assert!(
            update.rejections.is_empty(),
            "the shipped file must not contain a value the shipped parser rejects: {}",
            update.rejections.summary()
        );
        assert!(
            update.is_quiet(),
            "reading the default file must move nothing: {}",
            describe(&update)
        );
        assert_eq!(loaded.enabled, fresh.enabled);
        assert_eq!(loaded.bindings().keyboard, fresh.bindings().keyboard);
        assert_eq!(loaded.bindings().gamepad, fresh.bindings().gamepad);
        assert_eq!(loaded.bindings().radial, fresh.bindings().radial);
        assert_eq!(loaded.target(), fresh.target());
        assert_eq!(loaded.tables, fresh.tables);
    }

    /// ...and those defaults are the ones the file's own text claims, read independently of the
    /// constants above. A default that drifts from its comment is a lie in the player's editor.
    #[test]
    fn the_shipped_default_file_says_what_it_means() {
        let config = from_default();
        assert!(config.enabled);
        assert_eq!(
            config.bindings().keyboard,
            Some(parse_chord("F9").expect("F9"))
        );
        assert_eq!(pad_chord_name(config.bindings().gamepad), "LS+RS");
        assert_eq!(pad_chord_name(config.bindings().radial), "DPad_Down");
        assert_eq!(config.target().mode, TargetMode::LockOn);
        assert_eq!(config.target().chr_id, 0);
        assert!(config.target().release_on_death);
        assert_eq!(config.tables.mapping.model, MappingModel::Context);
        assert_eq!(config.tables.mapping.combo_window_ms, 1200);
        assert_eq!(config.tables.mapping.bands_m, (4.0, 12.0));
        // ON, and asserted rather than assumed: TAE event 304 is 100% of the 4000 animation band
        // and every boss grab in the game, so a `false` here silently removes the signature move
        // of most of what anybody would want to possess.
        assert!(config.tables.mapping.allow_grabs);
        assert_eq!(config.tables.mapping.unbound_inputs, UnboundInputs::Promote);
        assert_eq!(config.tables.mapping.watchdog_seconds, 4.0);
        assert_eq!(config.tables.buttons.r1, Bucket::Light);
        assert_eq!(config.tables.movement.turn_deadzone_deg, 20.0);
        // The example per-chr table ships COMMENTED OUT, so a fresh install overrides nothing.
        assert!(config.tables.chr_overrides.is_empty());
    }

    /// A rebind is accepted, names both ends, and reports itself as a binding move -- which is
    /// what resets the edge detectors.
    #[test]
    fn a_rebind_is_accepted_and_reports_both_ends() {
        let mut config = from_default();
        let update = config.apply("hotkey = \"F8\"\ngamepad_hotkey = \"select+start\"\n");
        assert_eq!(
            update.keyboard_moved,
            Some(("F9".to_owned(), "F8".to_owned()))
        );
        assert_eq!(
            update.gamepad_moved,
            Some(("LS+RS".to_owned(), "Select+Start".to_owned()))
        );
        assert!(update.bindings_moved());
        assert_eq!(
            config.bindings().keyboard,
            Some(parse_chord("F8").expect("F8"))
        );
    }

    /// THE RULE THAT MATTERS. A typo keeps the key that was working. It does not fall back to the
    /// built-in default, and it does not turn the binding off.
    #[test]
    fn a_rejected_binding_keeps_the_last_working_one() {
        let mut config = from_default();
        config.apply("hotkey = \"F8\"\ngamepad_hotkey = \"select+start\"\n");

        let update = config.apply("hotkey = \"Winkey\"\ngamepad_hotkey = \"select+turbo\"\n");
        assert!(!update.bindings_moved(), "a rejection is not a rebind");
        let (value, kept) = update.keyboard_rejected.expect("keyboard rejection");
        assert!(value.contains("Winkey"), "{value}");
        assert_eq!(
            kept, "F8",
            "F8 is the last one that WORKED, not the default"
        );
        let (value, kept) = update.gamepad_rejected.expect("gamepad rejection");
        assert!(value.contains("turbo"), "{value}");
        assert_eq!(kept, "Select+Start");
        assert_eq!(
            config.bindings().keyboard,
            Some(parse_chord("F8").expect("F8"))
        );
    }

    /// A value that means the SAME key is not a change. Reporting one resets the edge detector,
    /// and a key held at that instant fires without being pressed.
    #[test]
    fn respelling_a_binding_is_not_a_change() {
        let mut config = from_default();
        for spelling in ["F9", "f9", "  F9  "] {
            let update = config.apply(&format!("hotkey = \"{spelling}\"\n"));
            assert!(update.is_quiet(), "{spelling}: {}", describe(&update));
        }
        // Same for the pad, where order and case are both free.
        let update = config.apply("gamepad_hotkey = \"RS + LS\"\n");
        assert!(update.is_quiet(), "{}", describe(&update));
    }

    /// An empty binding is a real setting -- "no binding" -- and must not be read as a typo.
    #[test]
    fn an_empty_binding_disables_that_device_rather_than_being_rejected() {
        let mut config = from_default();
        let update = config.apply("hotkey = \"\"\ngamepad_hotkey = \"\"\n");
        assert!(update.keyboard_rejected.is_none());
        assert!(update.gamepad_rejected.is_none());
        assert_eq!(config.bindings().keyboard, None);
        assert!(!config.bindings().gamepad.is_bound());
    }

    /// THE NOT-LIVE TABLE. An edit to `[target]` is staged and does NOT move what a possession
    /// starting now would use.
    #[test]
    fn a_target_edit_is_staged_and_does_not_take_effect_until_the_next_possession() {
        let mut config = from_default();
        let update = config.apply("[target]\nmode = \"chr_id\"\nchr_id = 45000\n");

        let (from, to) = update.target_staged.clone().expect("staged");
        assert!(from.contains("mode=lock_on"), "{from}");
        assert!(to.contains("mode=chr_id"), "{to}");
        assert!(
            !update.bindings_moved(),
            "staging a target must not reset a key edge detector"
        );
        // Still lock_on IN FORCE, which is what an engine would be handed.
        assert_eq!(config.target().mode, TargetMode::LockOn);
        assert_eq!(config.request().target.mode, TargetMode::LockOn);
        assert_eq!(
            config.staged_target().map(|t| t.mode),
            Some(TargetMode::ChrId)
        );

        // The next possession promotes it, and only then.
        let moved = config.adopt_staged_target().expect("adopted");
        assert!(moved.1.contains("chr_id=45000"), "{}", moved.1);
        assert_eq!(config.target().mode, TargetMode::ChrId);
        assert_eq!(config.target().chr_id, 45000);
        assert_eq!(config.staged_target(), None, "nothing left staged");
        assert_eq!(
            config.adopt_staged_target(),
            None,
            "and it does not re-fire"
        );
    }

    /// Editing `[target]` back to what is in force clears the staging rather than leaving a
    /// promotion queued that would be a no-op.
    #[test]
    fn a_target_edit_reverted_before_the_next_possession_stages_nothing() {
        let mut config = from_default();
        config.apply("[target]\nmode = \"nearest\"\n");
        assert!(config.staged_target().is_some());
        let update = config.apply("[target]\nmode = \"lock_on\"\n");
        assert_eq!(config.staged_target(), None);
        assert!(update.target_staged.is_none());
        assert_eq!(config.adopt_staged_target(), None);
    }

    /// Everything that is NOT `[target]` is live: it moves on the reload, with no re-possession.
    #[test]
    fn the_other_tables_are_live() {
        let mut config = from_default();
        let update = config.apply("[mapping]\nmodel = \"slots\"\ncombo_window_ms = 400\n");
        assert!(update.tables_moved.is_some());
        assert_eq!(config.tables.mapping.model, MappingModel::Slots);
        assert_eq!(config.tables.mapping.combo_window_ms, 400);
        assert!(
            !update.bindings_moved(),
            "a table edit must not reset the key edge detectors"
        );
    }

    /// A junk scalar anywhere is collected and named, and never silently applied.
    #[test]
    fn unreadable_values_are_collected_into_one_line() {
        let mut config = from_default();
        let update = config.apply("enabled = maybe\n[movement]\nspeed_scale = fast\n");
        assert!(config.enabled, "a misspelled master switch is not 'off'");
        let line = describe(&update);
        assert!(line.contains("enabled=\"maybe\""), "{line}");
        assert!(line.contains("movement.speed_scale=\"fast\""), "{line}");
    }

    /// A poll that changes nothing must produce no log line at all, or the log is a wall of
    /// "the config reloaded" that hides the one line that matters.
    #[test]
    fn re_reading_the_same_text_is_silent() {
        let mut config = from_default();
        for _ in 0..5 {
            let update = config.apply(DEFAULT_CONFIG_TOML);
            assert!(update.is_quiet(), "{}", describe(&update));
        }
    }

    /// The two file names the DLL owns, spelled once each and asserted so a rename cannot quietly
    /// leave the derived file pointing at the config.
    #[test]
    fn the_config_and_derived_files_are_distinct_and_cwd_relative() {
        assert_eq!(CONFIG_FILE_NAME, "er-npc-possess.toml");
        assert_eq!(DERIVED_CONFIG_FILE_NAME, "er-npc-possess.derived.toml");
        assert_ne!(CONFIG_FILE_NAME, DERIVED_CONFIG_FILE_NAME);
        assert_eq!(config_path(), PathBuf::from(CONFIG_FILE_NAME));
        assert!(
            config_path().is_relative(),
            "the game's own directory is the CWD; an absolute path would be somebody's machine"
        );
    }
}
