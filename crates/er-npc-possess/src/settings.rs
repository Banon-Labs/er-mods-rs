//! The schema: every table in `er-npc-possess.toml` that is not a hotkey.
//!
//! Almost nothing here is CONSUMED yet -- stack layer 1 owns the config file, the hotkeys and the
//! seam, and the possession engine that reads these values lands in a later layer. They are
//! parsed, validated and reported anyway, for one reason: the file a player tunes now has to be
//! the file the later layers read. A schema that appears a table at a time makes every early
//! config a migration.
//!
//! # The rule every field here follows
//!
//! A value that does not parse KEEPS THE ONE ALREADY IN FORCE and says so. Not the built-in
//! default -- that silently drags someone back off a setting they had deliberately moved -- and
//! not zero. It is the same rule `er_hotkey_config::Binding` enforces for keys, applied to the
//! scalars, because a typo in `speed_scale` is exactly as invisible as a typo in `hotkey`.
//!
//! # The one table that is not live
//!
//! [`TargetSettings`] is the whole of `[target]`, and it is deliberately a struct of its own
//! rather than four fields on the config: changing who you are about to possess must not take
//! effect while you are possessing somebody. See `config::PossessConfig::stage_target`.

// Windows-only crate in practice; this module is pure parsing and stays ungated so its tests run
// on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::toml::Document;

/// Section names, spelled once.
pub(crate) const TARGET_SECTION: &str = "target";
pub(crate) const MAPPING_SECTION: &str = "mapping";
pub(crate) const BUTTONS_SECTION: &str = "buttons";
pub(crate) const MOVEMENT_SECTION: &str = "movement";
/// `[chr.c4500]`, `[chr.c2130]`, ... -- open-ended, one per character id.
pub(crate) const CHR_SECTION_PREFIX: &str = "chr.";

/// Collects `"<section>.<key>" = <value>` for every value that could not be read, so one log line
/// can name all of them and the player can find each in their file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct Rejections(Vec<String>);

impl Rejections {
    /// Record one unreadable value. `path` is `<section>.<key>`, or a bare key at the top level.
    pub(crate) fn push(&mut self, path: &str, value: &str) {
        self.0.push(format!("{path}={value:?}"));
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub(crate) fn summary(&self) -> String {
        self.0.join(" ")
    }
}

/// Read a value the caller knows how to parse, keeping `current` when it is absent or junk.
fn take<T>(
    current: &mut T,
    doc: &Document,
    section: &str,
    key: &str,
    rejections: &mut Rejections,
    parse: impl FnOnce(&str) -> Option<T>,
) {
    let Some(raw) = doc.scalar(section, key) else {
        return;
    };
    match parse(raw) {
        Some(value) => *current = value,
        None => rejections.push(&qualify(section, key), raw),
    }
}

fn qualify(section: &str, key: &str) -> String {
    if section.is_empty() {
        key.to_owned()
    } else {
        format!("{section}.{key}")
    }
}

/// `true` / `false`, any case. Anything else is a rejection rather than a silent `false`: a
/// misspelled boolean that reads as "off" is indistinguishable from the feature being broken.
pub(crate) fn parse_bool(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    }
}

fn parse_f32(raw: &str) -> Option<f32> {
    raw.trim().parse::<f32>().ok().filter(|v| v.is_finite())
}

fn parse_i32(raw: &str) -> Option<i32> {
    raw.trim().parse::<i32>().ok()
}

/// How the DLL decides WHICH character to possess.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum TargetMode {
    /// Whatever the player is locked on to.
    #[default]
    LockOn,
    /// The closest character the selection filter accepts.
    Nearest,
    /// Whatever the camera centre is pointed at.
    Crosshair,
    /// The literal [`TargetSettings::chr_id`], ignoring where the player is looking.
    ChrId,
}

impl TargetMode {
    pub(crate) fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "lock_on" | "lockon" => Some(Self::LockOn),
            "nearest" => Some(Self::Nearest),
            "crosshair" => Some(Self::Crosshair),
            "chr_id" | "chrid" => Some(Self::ChrId),
            _ => None,
        }
    }

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::LockOn => "lock_on",
            Self::Nearest => "nearest",
            Self::Crosshair => "crosshair",
            Self::ChrId => "chr_id",
        }
    }
}

/// `[target]`. THE ONLY TABLE THAT IS NOT LIVE.
///
/// Every other setting here takes effect on the next reload, roughly a second after the file is
/// saved. This one cannot: it decides who you are, and swapping that out from under an in-flight
/// possession would mean the mapping, the camera and the moveset all belonging to a different
/// character than the body on screen. An edit is STAGED and adopted at the next possession.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct TargetSettings {
    pub(crate) mode: TargetMode,
    /// Used only when `mode == ChrId`, e.g. 45000.
    pub(crate) chr_id: i32,
    pub(crate) release_on_death: bool,
}

impl Default for TargetSettings {
    fn default() -> Self {
        Self {
            mode: TargetMode::LockOn,
            chr_id: 0,
            release_on_death: true,
        }
    }
}

impl TargetSettings {
    /// Read `[target]` out of a document. Named `apply_from` rather than `apply` because the
    /// caller does NOT apply the result to what is in force -- it stages it. See
    /// `crate::config::PossessConfig::adopt_staged_target`.
    pub(crate) fn apply_from(&mut self, doc: &Document, rejections: &mut Rejections) {
        let s = TARGET_SECTION;
        take(
            &mut self.mode,
            doc,
            s,
            "mode",
            rejections,
            TargetMode::parse,
        );
        take(&mut self.chr_id, doc, s, "chr_id", rejections, parse_i32);
        take(
            &mut self.release_on_death,
            doc,
            s,
            "release_on_death",
            rejections,
            parse_bool,
        );
    }

    pub(crate) fn summary(&self) -> String {
        format!(
            "mode={} chr_id={} release_on_death={}",
            self.mode.name(),
            self.chr_id,
            self.release_on_death
        )
    }
}

/// How a controller input turns into one of the possessed character's attacks.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum MappingModel {
    /// Range band plus a combo rank picks the attack. The default, and the only one with a
    /// per-input meaning that survives a character with thirty attacks and four buttons.
    #[default]
    Context,
    /// The range band picks a contiguous THIRD of the bucket's rank order rather than filtering
    /// by reach: close gets the low ranks, far the high ones. More predictable than
    /// [`Self::Context`] and less situationally right.
    Layered,
    /// No range filtering at all -- every press walks the whole bucket. For creatures whose
    /// attacks mostly came back with an unmeasured reach, where filtering removes moves for no
    /// good reason.
    Slots,
}

impl MappingModel {
    pub(crate) fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "context" => Some(Self::Context),
            "layered" => Some(Self::Layered),
            "slots" => Some(Self::Slots),
            _ => None,
        }
    }

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Context => "context",
            Self::Layered => "layered",
            Self::Slots => "slots",
        }
    }
}

/// What happens to an input the mapping has nothing for.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum UnboundInputs {
    /// Give it the nearest attack in the same bucket. Fewer dead buttons, less predictable.
    #[default]
    Promote,
    /// Leave it dead. Predictable, and the honest choice for a character with few attacks.
    Deny,
}

impl UnboundInputs {
    pub(crate) fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "promote" => Some(Self::Promote),
            "deny" => Some(Self::Deny),
            _ => None,
        }
    }

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Promote => "promote",
            Self::Deny => "deny",
        }
    }
}

/// `[mapping]`. Live.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct MappingSettings {
    pub(crate) model: MappingModel,
    /// Neutral time after which the attack rank falls back to 0.
    pub(crate) combo_window_ms: u32,
    /// `close < bands_m.0 < mid < bands_m.1 < far`, in metres.
    pub(crate) bands_m: (f32, f32),
    /// Offer the possessed creature's grabs.
    ///
    /// ON by default, and this reversed once the corpus was actually measured. The old default
    /// was `false` on the reasoning that a grab is unfair; the reasoning was about the wrong
    /// direction of the interaction (you are the one grabbing) and the scope was wrong too --
    /// grabs are the signature move of most bosses, not a niche category.
    ///
    /// **IT NOW GATES 153 REAL MOVES ACROSS 78 CREATURES.** It used to gate nothing, and the
    /// reason it did is worth keeping written down, because the mistake was in the model rather
    /// than in the data. A grab is not a 4000-band animation and it is not TimeAct event 304: it
    /// is an ORDINARY, already-fireable attack whose `AtkParam_Npc` row has `throwTypeId != 0`.
    /// `ApplyDamage` reads that column off the hit that landed and calls
    /// `CSChrThrowModule::InitThrow` before calculating any damage; the throw system then drives
    /// both parties into the 4000-band clips through the bare behaviour names `W_ThrowAtk` and
    /// `W_ThrowDef`. So the old sweep's finding was true and pointed at the wrong half -- no event
    /// name in the 4000 band has a transition behind it because those clips are never addressed by
    /// id at all. Marking THOSE as the grabs is what left this flag matching nothing.
    ///
    /// Every initiator is in the 3000 band, and every one of them was already being offered as a
    /// plain attack, so turning this off now genuinely removes something.
    pub(crate) allow_grabs: bool,
    pub(crate) unbound_inputs: UnboundInputs,
    /// How long the possessed creature may animate, go nowhere and be asked for nothing before
    /// the watchdog forces it back to idle. See [`crate::moveset::watchdog`].
    pub(crate) watchdog_seconds: f32,
}

impl Default for MappingSettings {
    fn default() -> Self {
        Self {
            model: MappingModel::Context,
            combo_window_ms: 1200,
            bands_m: (4.0, 12.0),
            allow_grabs: true,
            unbound_inputs: UnboundInputs::Promote,
            watchdog_seconds: 4.0,
        }
    }
}

impl MappingSettings {
    fn apply(&mut self, doc: &Document, rejections: &mut Rejections) {
        let s = MAPPING_SECTION;
        take(
            &mut self.model,
            doc,
            s,
            "model",
            rejections,
            MappingModel::parse,
        );
        take(
            &mut self.combo_window_ms,
            doc,
            s,
            "combo_window_ms",
            rejections,
            |raw| raw.trim().parse::<u32>().ok(),
        );
        take(
            &mut self.allow_grabs,
            doc,
            s,
            "allow_grabs",
            rejections,
            parse_bool,
        );
        take(
            &mut self.unbound_inputs,
            doc,
            s,
            "unbound_inputs",
            rejections,
            UnboundInputs::parse,
        );
        // A zero or negative watchdog would mean "force idle immediately", which cancels every
        // attack on the frame it starts. Rejected rather than clamped: somebody who typed 0 meant
        // something, and silently turning it into 4 would hide that they cannot have it.
        take(
            &mut self.watchdog_seconds,
            doc,
            s,
            "watchdog_seconds",
            rejections,
            |raw| parse_f32(raw).filter(|value| *value > 0.0),
        );

        // The bands are one setting in two numbers, so they move together or not at all: half an
        // edit -- a readable near band and a junk far one -- would leave the two inconsistent
        // with each other, which is worse than keeping both.
        if let Some(items) = doc.array(s, "bands_m") {
            match (
                items.len(),
                items.first().copied().and_then(parse_f32),
                items.get(1).copied().and_then(parse_f32),
            ) {
                (2, Some(near), Some(far)) if near > 0.0 && far > near => {
                    self.bands_m = (near, far);
                }
                _ => rejections.push(&qualify(s, "bands_m"), &format!("[{}]", items.join(", "))),
            }
        }
    }

    pub(crate) fn summary(&self) -> String {
        format!(
            "model={} combo_window_ms={} bands_m=[{},{}] allow_grabs={} unbound_inputs={} \
             watchdog_seconds={}",
            self.model.name(),
            self.combo_window_ms,
            self.bands_m.0,
            self.bands_m.1,
            self.allow_grabs,
            self.unbound_inputs.name(),
            self.watchdog_seconds
        )
    }
}

/// The bucket an input draws its attack from. Identical on every character, which is the point:
/// the same button means the same KIND of thing whoever you are wearing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Bucket {
    Light,
    Heavy,
    Ranged,
    Movement,
}

impl Bucket {
    pub(crate) fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "light" => Some(Self::Light),
            "heavy" => Some(Self::Heavy),
            "ranged" => Some(Self::Ranged),
            "movement" => Some(Self::Movement),
            _ => None,
        }
    }

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Light => "light",
            Self::Heavy => "heavy",
            Self::Ranged => "ranged",
            Self::Movement => "movement",
        }
    }
}

/// `[buttons]`. Live.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ButtonSettings {
    pub(crate) r1: Bucket,
    pub(crate) r2: Bucket,
    pub(crate) l1: Bucket,
    pub(crate) l2: Bucket,
}

impl Default for ButtonSettings {
    fn default() -> Self {
        Self {
            r1: Bucket::Light,
            r2: Bucket::Heavy,
            l1: Bucket::Ranged,
            l2: Bucket::Movement,
        }
    }
}

impl ButtonSettings {
    fn apply(&mut self, doc: &Document, rejections: &mut Rejections) {
        let s = BUTTONS_SECTION;
        take(&mut self.r1, doc, s, "r1", rejections, Bucket::parse);
        take(&mut self.r2, doc, s, "r2", rejections, Bucket::parse);
        take(&mut self.l1, doc, s, "l1", rejections, Bucket::parse);
        take(&mut self.l2, doc, s, "l2", rejections, Bucket::parse);
    }

    pub(crate) fn summary(&self) -> String {
        format!(
            "r1={} r2={} l1={} l2={}",
            self.r1.name(),
            self.r2.name(),
            self.l1.name(),
            self.l2.name()
        )
    }
}

/// `[movement]`. Live.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct MovementSettings {
    /// Turn the possessed body toward the stick rather than snapping to it.
    pub(crate) heading_converge: bool,
    /// Stick deflection inside this cone is treated as "no turn asked for".
    pub(crate) turn_deadzone_deg: f32,
    /// Move only by the animation's own root motion, never by writing a velocity.
    pub(crate) root_motion_only: bool,
    pub(crate) speed_scale: f32,
}

impl Default for MovementSettings {
    fn default() -> Self {
        Self {
            heading_converge: true,
            turn_deadzone_deg: 20.0,
            root_motion_only: true,
            speed_scale: 1.0,
        }
    }
}

impl MovementSettings {
    fn apply(&mut self, doc: &Document, rejections: &mut Rejections) {
        let s = MOVEMENT_SECTION;
        take(
            &mut self.heading_converge,
            doc,
            s,
            "heading_converge",
            rejections,
            parse_bool,
        );
        take(
            &mut self.turn_deadzone_deg,
            doc,
            s,
            "turn_deadzone_deg",
            rejections,
            |raw| parse_f32(raw).filter(|v| (0.0..=180.0).contains(v)),
        );
        take(
            &mut self.root_motion_only,
            doc,
            s,
            "root_motion_only",
            rejections,
            parse_bool,
        );
        take(
            &mut self.speed_scale,
            doc,
            s,
            "speed_scale",
            rejections,
            |raw| parse_f32(raw).filter(|v| *v > 0.0),
        );
    }

    pub(crate) fn summary(&self) -> String {
        format!(
            "heading_converge={} turn_deadzone_deg={} root_motion_only={} speed_scale={}",
            self.heading_converge, self.turn_deadzone_deg, self.root_motion_only, self.speed_scale
        )
    }
}

/// One `[chr.cXXXX]` table. Live, and open-ended: the parser reports the ids the FILE contains
/// rather than looking up a list this build was compiled knowing.
///
/// Every field is optional because an override is a delta. `speed_scale` absent here means "use
/// `[movement].speed_scale`", which is not the same as "1.0".
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct ChrOverride {
    /// The section suffix as written, e.g. `c4500`.
    pub(crate) chr: String,
    pub(crate) turn_rate_deg_per_sec: Option<f32>,
    pub(crate) camera_distance_scale: Option<f32>,
    pub(crate) speed_scale: Option<f32>,
    /// `pin = { r2 = 3046 }` -- force one input onto one animation id.
    pub(crate) pin: Vec<(String, i32)>,
    /// Animation ids this character must never be given.
    pub(crate) unusable: Vec<i32>,
    /// Animation ids to force back IN, overriding the auto-classifier's verdict.
    pub(crate) usable: Vec<i32>,
}

impl ChrOverride {
    fn read(doc: &Document, chr: &str, rejections: &mut Rejections) -> Self {
        let section = format!("{CHR_SECTION_PREFIX}{chr}");
        let mut over = Self {
            chr: chr.to_owned(),
            ..Self::default()
        };
        let positive = |key: &str, slot: &mut Option<f32>, rejections: &mut Rejections| {
            if let Some(raw) = doc.scalar(&section, key) {
                match parse_f32(raw).filter(|v| *v > 0.0) {
                    Some(value) => *slot = Some(value),
                    None => rejections.push(&qualify(&section, key), raw),
                }
            }
        };
        positive(
            "turn_rate_deg_per_sec",
            &mut over.turn_rate_deg_per_sec,
            rejections,
        );
        positive(
            "camera_distance_scale",
            &mut over.camera_distance_scale,
            rejections,
        );
        positive("speed_scale", &mut over.speed_scale, rejections);

        if let Some(pairs) = doc.inline_table(&section, "pin") {
            for (input, value) in pairs {
                match parse_i32(value) {
                    Some(id) => over.pin.push((input.to_owned(), id)),
                    None => rejections.push(&qualify(&section, &format!("pin.{input}")), value),
                }
            }
        }
        for (key, slot) in [
            ("unusable", &mut over.unusable),
            ("usable", &mut over.usable),
        ] {
            let Some(items) = doc.array(&section, key) else {
                continue;
            };
            for item in items {
                match parse_i32(item) {
                    Some(id) => slot.push(id),
                    None => rejections.push(&qualify(&section, key), item),
                }
            }
        }
        over
    }
}

/// Everything in the file that is not a hotkey and not the master switch.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Tables {
    pub(crate) mapping: MappingSettings,
    pub(crate) buttons: ButtonSettings,
    pub(crate) movement: MovementSettings,
    pub(crate) chr_overrides: Vec<ChrOverride>,
}

impl Tables {
    /// Apply a whole document. `[target]` is deliberately NOT here -- it is staged separately
    /// because it is the one table that must not move mid-possession.
    pub(crate) fn apply(&mut self, doc: &Document, rejections: &mut Rejections) {
        self.mapping.apply(doc, rejections);
        self.buttons.apply(doc, rejections);
        self.movement.apply(doc, rejections);

        // REPLACED, not merged. A `[chr.cXXXX]` table the player DELETED has to stop applying,
        // and merging into what is already in force would make deletion a no-op -- the one edit
        // that is impossible to debug, because the file no longer mentions the setting that is
        // still in effect.
        self.chr_overrides = doc
            .sections_under(CHR_SECTION_PREFIX)
            .into_iter()
            .map(|chr| ChrOverride::read(doc, chr, rejections))
            .collect();
    }

    pub(crate) fn summary(&self) -> String {
        format!(
            "[mapping] {} | [buttons] {} | [movement] {} | chr_overrides={}",
            self.mapping.summary(),
            self.buttons.summary(),
            self.movement.summary(),
            self.chr_overrides.len()
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read(text: &str) -> (Tables, TargetSettings, Rejections) {
        let doc = Document::parse(text);
        let mut tables = Tables::default();
        let mut target = TargetSettings::default();
        let mut rejections = Rejections::default();
        tables.apply(&doc, &mut rejections);
        target.apply_from(&doc, &mut rejections);
        (tables, target, rejections)
    }

    #[test]
    fn an_empty_file_yields_the_built_in_defaults() {
        let (tables, target, rejections) = read("");
        assert_eq!(tables, Tables::default());
        assert_eq!(target, TargetSettings::default());
        assert!(rejections.is_empty());
        assert_eq!(target.mode, TargetMode::LockOn);
        assert_eq!(tables.mapping.bands_m, (4.0, 12.0));
        assert_eq!(tables.buttons.r1, Bucket::Light);
    }

    #[test]
    fn every_table_reads_back_what_the_file_says() {
        let (tables, target, rejections) = read(
            r#"
[target]
mode = "chr_id"
chr_id = 45000
release_on_death = false

[mapping]
model = "slots"
combo_window_ms = 800
bands_m = [2.5, 20.0]
allow_grabs = true
unbound_inputs = "deny"

[buttons]
r1 = "heavy"; r2 = "light"; l1 = "movement"; l2 = "ranged"

[movement]
heading_converge = false
turn_deadzone_deg = 5.0
root_motion_only = false
speed_scale = 1.25
"#,
        );
        assert!(rejections.is_empty(), "{}", rejections.summary());
        assert_eq!(target.mode, TargetMode::ChrId);
        assert_eq!(target.chr_id, 45000);
        assert!(!target.release_on_death);
        assert_eq!(tables.mapping.model, MappingModel::Slots);
        assert_eq!(tables.mapping.combo_window_ms, 800);
        assert_eq!(tables.mapping.bands_m, (2.5, 20.0));
        assert!(tables.mapping.allow_grabs);
        assert_eq!(tables.mapping.unbound_inputs, UnboundInputs::Deny);
        assert_eq!(tables.buttons.r1, Bucket::Heavy);
        assert_eq!(tables.buttons.l2, Bucket::Ranged);
        assert!(!tables.movement.heading_converge);
        assert_eq!(tables.movement.speed_scale, 1.25);
    }

    /// THE RULE. Junk keeps the value that was working and names itself, so the player can find
    /// the line. It does not reset to the built-in default and it does not read as zero/false.
    #[test]
    fn a_junk_value_keeps_the_one_in_force_and_names_itself() {
        let doc = Document::parse("[movement]\nspeed_scale = \"fast\"\n");
        let mut movement = MovementSettings {
            speed_scale: 1.4,
            ..MovementSettings::default()
        };
        let mut rejections = Rejections::default();
        movement.apply(&doc, &mut rejections);
        assert_eq!(movement.speed_scale, 1.4, "not 1.0, and certainly not 0.0");
        assert_eq!(rejections.summary(), "movement.speed_scale=\"fast\"");
    }

    /// A misspelled boolean must not read as `false`. "off" looks exactly like the feature not
    /// working, and the player has no way to tell which they are looking at.
    #[test]
    fn a_misspelled_boolean_is_a_rejection_not_an_off_switch() {
        assert_eq!(parse_bool("TRUE"), Some(true));
        assert_eq!(parse_bool("false"), Some(false));
        assert_eq!(parse_bool("yes"), None);
        assert_eq!(parse_bool("1"), None);

        let (_, target, rejections) = read("[target]\nrelease_on_death = yes\n");
        assert!(target.release_on_death, "kept the default, which is true");
        assert_eq!(rejections.summary(), "target.release_on_death=\"yes\"");
    }

    /// Half an edit to the range bands would leave the two numbers inconsistent, so they move
    /// together or not at all.
    #[test]
    fn the_range_bands_are_all_or_nothing() {
        for junk in [
            "[mapping]\nbands_m = [4.0]\n",
            "[mapping]\nbands_m = [12.0, 4.0]\n",
            "[mapping]\nbands_m = [4.0, near]\n",
            "[mapping]\nbands_m = [0.0, 12.0]\n",
            "[mapping]\nbands_m = [4.0, 12.0, 30.0]\n",
        ] {
            let (tables, _, rejections) = read(junk);
            assert_eq!(tables.mapping.bands_m, (4.0, 12.0), "{junk}");
            assert!(!rejections.is_empty(), "{junk}");
        }
    }

    #[test]
    fn per_chr_overrides_are_read_from_whatever_ids_the_file_names() {
        let (tables, _, rejections) = read(
            r#"
[chr.c4500]
turn_rate_deg_per_sec = 45.0
camera_distance_scale = 2.4
speed_scale = 1.15
pin = { r2 = 3046 }
unusable = [3300, 3301]
usable = [3120]

[chr.c9999]
speed_scale = 0.5
"#,
        );
        assert!(rejections.is_empty(), "{}", rejections.summary());
        assert_eq!(tables.chr_overrides.len(), 2);
        let first = &tables.chr_overrides[0];
        assert_eq!(first.chr, "c4500");
        assert_eq!(first.turn_rate_deg_per_sec, Some(45.0));
        assert_eq!(first.camera_distance_scale, Some(2.4));
        assert_eq!(first.pin, vec![("r2".to_owned(), 3046)]);
        assert_eq!(first.unusable, vec![3300, 3301]);
        assert_eq!(first.usable, vec![3120]);
        assert_eq!(tables.chr_overrides[1].chr, "c9999");
        // An absent override is None, not a default -- the difference between "leave it alone"
        // and "force it to 1.0".
        assert_eq!(tables.chr_overrides[1].turn_rate_deg_per_sec, None);
    }

    /// Deleting a `[chr.*]` table has to stop it applying. Merging would make deletion the one
    /// edit with no effect, and a setting still in force that the file no longer mentions is
    /// undebuggable.
    #[test]
    fn deleting_a_chr_table_removes_its_override() {
        let mut tables = Tables::default();
        let mut rejections = Rejections::default();
        tables.apply(
            &Document::parse("[chr.c4500]\nspeed_scale = 1.15\n[chr.c2130]\nspeed_scale = 0.8\n"),
            &mut rejections,
        );
        assert_eq!(tables.chr_overrides.len(), 2);
        tables.apply(
            &Document::parse("[chr.c2130]\nspeed_scale = 0.8\n"),
            &mut rejections,
        );
        assert_eq!(tables.chr_overrides.len(), 1);
        assert_eq!(tables.chr_overrides[0].chr, "c2130");
    }

    /// Every enum spelling in the shipped default file resolves, in either case.
    #[test]
    fn every_shipped_enum_spelling_resolves() {
        for (raw, expected) in [
            ("lock_on", TargetMode::LockOn),
            ("NEAREST", TargetMode::Nearest),
            ("crosshair", TargetMode::Crosshair),
            ("chr_id", TargetMode::ChrId),
        ] {
            assert_eq!(TargetMode::parse(raw), Some(expected), "{raw}");
        }
        assert_eq!(TargetMode::parse("locked"), None);
        for (raw, expected) in [
            ("context", MappingModel::Context),
            ("Layered", MappingModel::Layered),
            ("slots", MappingModel::Slots),
        ] {
            assert_eq!(MappingModel::parse(raw), Some(expected), "{raw}");
        }
        assert_eq!(MappingModel::parse("contextual"), None);
        assert_eq!(UnboundInputs::parse("deny"), Some(UnboundInputs::Deny));
        assert_eq!(UnboundInputs::parse("drop"), None);
        assert_eq!(Bucket::parse("Ranged"), Some(Bucket::Ranged));
        assert_eq!(Bucket::parse("spell"), None);
    }

    /// The summaries are what the log line is made of, so they have to name every field: a
    /// setting missing from here is one that silently moved.
    #[test]
    fn the_summaries_name_every_field() {
        let summary = Tables::default().summary();
        for field in [
            "model=",
            "combo_window_ms=",
            "bands_m=",
            "allow_grabs=",
            "unbound_inputs=",
            "r1=",
            "r2=",
            "l1=",
            "l2=",
            "heading_converge=",
            "turn_deadzone_deg=",
            "root_motion_only=",
            "speed_scale=",
            "chr_overrides=",
        ] {
            assert!(summary.contains(field), "{field} missing from {summary}");
        }
        let target = TargetSettings::default().summary();
        for field in ["mode=", "chr_id=", "release_on_death="] {
            assert!(target.contains(field), "{field} missing from {target}");
        }
    }
}
