//! Everything that decides what the panel shows, with no game memory in sight.
//!
//! The game-facing half (`game.rs`) turns the locked-on character into a [`TargetReading`] of
//! plain numbers; this module turns that into rows. Keeping the split this sharp is what lets
//! `cargo test` on Linux prove the parts a player would notice being wrong: a bar that overflows,
//! a stamina bar on a character with no stamina, a status row that never goes away, or a panel
//! left drawing a character that is no longer there.

// Several items here are consumed only by the Windows build; the host build keeps them for tests.
#![cfg_attr(not(windows), allow(dead_code))]

/// Number of status gauges `CSChrResistModule` carries.
pub const STATUS_COUNT: usize = 7;

/// A status ailment, in the order `CSChrResistModule` stores its gauges.
///
/// The order is the engine's: `FUN_14043d8a0` (1.17.1 `0x14043de00`) indexes the gauge array with
/// it and sets bit `1 << index` of the proc flags, and `ApplySpEffectStatusClearFlags` (1.17.1
/// `0x14043e7b0`) maps each `stateInfo` onto the same bit.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Status {
    Poison,
    ScarletRot,
    BloodLoss,
    DeathBlight,
    Frostbite,
    Sleep,
    Madness,
}

impl Status {
    /// Every status, in gauge order.
    pub const ALL: [Status; STATUS_COUNT] = [
        Status::Poison,
        Status::ScarletRot,
        Status::BloodLoss,
        Status::DeathBlight,
        Status::Frostbite,
        Status::Sleep,
        Status::Madness,
    ];

    /// Index into the resist module's gauge and resistance arrays.
    pub fn index(self) -> usize {
        match self {
            Status::Poison => 0,
            Status::ScarletRot => 1,
            Status::BloodLoss => 2,
            Status::DeathBlight => 3,
            Status::Frostbite => 4,
            Status::Sleep => 5,
            Status::Madness => 6,
        }
    }

    /// `SpEffectParam.stateInfo` of the row that is live on a character while this status is.
    ///
    /// Read out of `ApplySpEffectStatusClearFlags` (1.16.2 `0x14043e250`, 1.17.1 `0x14043e7b0`):
    /// 2 sets bit 0, 5 bit 1, 6 bit 2, `0x74` bit 3, `0x104` bit 4, `0x1b4` bit 5, `0x1b5` bit 6.
    pub fn state_info(self) -> u16 {
        match self {
            Status::Poison => 2,
            Status::ScarletRot => 5,
            Status::BloodLoss => 6,
            Status::DeathBlight => 0x74,
            Status::Frostbite => 0x104,
            Status::Sleep => 0x1b4,
            Status::Madness => 0x1b5,
        }
    }

    /// The status a live SpEffect row's `stateInfo` belongs to, if any.
    pub fn from_state_info(state_info: u16) -> Option<Status> {
        Status::ALL
            .into_iter()
            .find(|status| status.state_info() == state_info)
    }

    /// The name the game uses on its own HUD.
    pub fn label(self) -> &'static str {
        match self {
            Status::Poison => "Poison",
            Status::ScarletRot => "Scarlet Rot",
            Status::BloodLoss => "Blood Loss",
            Status::DeathBlight => "Death Blight",
            Status::Frostbite => "Frostbite",
            Status::Sleep => "Sleep",
            Status::Madness => "Madness",
        }
    }

    /// Bar colour, close to the colour of the game's own build-up meter for the status.
    pub fn color(self) -> [f32; 4] {
        match self {
            Status::Poison => [0.55, 0.75, 0.25, 1.0],
            Status::ScarletRot => [0.85, 0.35, 0.15, 1.0],
            Status::BloodLoss => [0.75, 0.08, 0.12, 1.0],
            Status::DeathBlight => [0.55, 0.50, 0.60, 1.0],
            Status::Frostbite => [0.55, 0.85, 0.95, 1.0],
            Status::Sleep => [0.60, 0.55, 0.90, 1.0],
            Status::Madness => [0.95, 0.70, 0.15, 1.0],
        }
    }
}

/// A resistance at or above this is the game's "immune", and the status never gets a row.
///
/// Measured live 2026-10-04 with `scripts/frida/target-bars-probe.js`: two ordinary enemies
/// (NpcParam 30001014 and 46000014) both carried 999 for death blight, the status no ordinary
/// enemy can suffer, while every other resistance was a real threshold (41..=2050).
pub const IMMUNE_RESISTANCE: i32 = 999;

/// One status gauge as `CSChrResistModule` holds it.
///
/// The gauge starts at the resistance and falls: each hit subtracts its build-up, a value below
/// 1 is a proc, and the proc resets it to the resistance (1.17.1 `0x14043de00`; the refill
/// `0x14043e9a0` clamps it to `[0, resistance]` every frame). So the build-up a player thinks of
/// is `resistance - gauge`. Measured live: a frostbite hit on NpcParam 46000014 took the gauge
/// from 316 to 236 with the resistance staying 316.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct StatusGauge {
    pub gauge: i32,
    pub resistance: i32,
}

impl StatusGauge {
    /// Build-up so far, `0..=resistance`.
    pub fn buildup(self) -> i32 {
        if self.resistance <= 0 {
            return 0;
        }
        (self.resistance - self.gauge).clamp(0, self.resistance)
    }

    /// The character cannot suffer this status at all.
    pub fn immune(self) -> bool {
        self.resistance >= IMMUNE_RESISTANCE
    }
}

/// The poise ("stance") module, as `CSChrSuperArmorModule` holds it.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct StanceReading {
    /// `+0x10`, what is left before the stance breaks.
    pub current: f32,
    /// `+0x14`, the effective maximum the per-frame update writes back every frame.
    pub max: f32,
    /// `+0x1c`, seconds until the stance starts to refill. Negative once it is refilling.
    pub recover_in: f32,
}

/// Everything read off the locked-on character in one frame, as plain numbers.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct TargetReading {
    /// The target is a `PlayerIns` (another player, or an NPC built as one).
    pub is_player: bool,
    /// `ChrIns::npc_param_id`, shown so a reader can tell two enemies apart.
    pub npc_param_id: i32,
    pub hp: i32,
    pub hp_max: i32,
    pub fp: i32,
    pub fp_max: i32,
    pub stamina: i32,
    pub stamina_max: i32,
    /// `None` when the character has no super armor module.
    pub stance: Option<StanceReading>,
    /// `None` when the character has no resist module.
    pub statuses: Option<[StatusGauge; STATUS_COUNT]>,
    /// Per status, `Some(seconds left)` while its SpEffect row is live on the target. The inner
    /// time is `None` when the entry has no finite timer.
    pub active: [Option<Option<f32>>; STATUS_COUNT],
}

impl TargetReading {
    /// Which statuses have a live SpEffect row this frame.
    pub fn live_flags(&self) -> [bool; STATUS_COUNT] {
        self.active.map(|entry| entry.is_some())
    }
}

/// What one extra bar under the game's HP bar is about. HP itself is never one: the game already
/// draws it, and a second copy was exactly what the user rejected on 2026-10-04.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RowKind {
    Stance,
    Stamina,
    Fp,
    Status(Status),
}

/// One extra bar, ready to draw.
#[derive(Clone, Debug, PartialEq)]
pub struct Row {
    pub kind: RowKind,
    pub label: &'static str,
    /// `0.0..=1.0`, how much of the bar is filled.
    pub fraction: f32,
    /// Text drawn over the bar, right-aligned.
    pub text: String,
    pub color: [f32; 4],
    /// The status is active right now (or was within the hold window).
    pub active: bool,
}

/// `CSChrDataModule`'s constructor stores 1 into every FP and stamina field (1.17.1
/// `0x140436610`, byte-identical to 1.16.2 `0x1404360b0`), and only a param initialiser replaces
/// it. A maximum at or below this is "never given a pool", not a pool of one. Ordinary enemies
/// measured live read FP max 0 and stamina max 54 and 163.
pub const PLACEHOLDER_POOL_MAX: i32 = 1;

/// Longest a live SpEffect timer is believed, in seconds. Rows with no end are stored with huge or
/// negative timers, and printing those would be noise.
const MAX_SHOWN_SECONDS: f32 = 999.0;

/// How long a status keeps reading as active after its SpEffect row is gone, in seconds.
///
/// Blood loss and madness procs live for one second (`effectEndurance` 1 on their weapon rows,
/// status.md section 1a); the hold keeps the proc on screen long enough to read without inventing
/// a longer effect.
pub const ACTIVE_HOLD_SECONDS: f64 = 1.5;

/// `cur / max` clamped to `0.0..=1.0`; zero for an empty or nonsense maximum.
pub fn fraction(current: f32, max: f32) -> f32 {
    if !(max.is_finite() && current.is_finite()) || max <= 0.0 {
        return 0.0;
    }
    (current / max).clamp(0.0, 1.0)
}

/// Integer form of [`fraction`].
pub fn fraction_i32(current: i32, max: i32) -> f32 {
    fraction(current as f32, max as f32)
}

/// Does the character have this resource pool at all? Decided from the maximum the game gave it.
pub fn has_pool(max: i32) -> bool {
    max > PLACEHOLDER_POOL_MAX
}

/// Does the stance module hold a real maximum?
pub fn has_stance(stance: &StanceReading) -> bool {
    stance.max.is_finite() && stance.max > 0.0 && stance.current.is_finite()
}

/// Seconds worth printing, or `None` for a timer that is not counting down to anything.
pub fn shown_seconds(seconds: f32) -> Option<f32> {
    (seconds.is_finite() && seconds > 0.0 && seconds <= MAX_SHOWN_SECONDS).then_some(seconds)
}

/// Statuses worth a row: the ones filling and the ones active, never an immune one. Gauge order.
pub fn visible_statuses(
    statuses: Option<&[StatusGauge; STATUS_COUNT]>,
    active: &[bool; STATUS_COUNT],
) -> Vec<Status> {
    Status::ALL
        .into_iter()
        .filter(|status| {
            let index = status.index();
            let gauge = statuses.map(|gauges| gauges[index]);
            if gauge.is_some_and(StatusGauge::immune) {
                return false;
            }
            let filling = gauge.is_some_and(|gauge| gauge.buildup() > 0);
            filling || active[index]
        })
        .collect()
}

/// The extra bars for one reading, top to bottom. `active` is the held activity from
/// [`ActiveHold`].
pub fn panel_rows(reading: &TargetReading, active: &[bool; STATUS_COUNT]) -> Vec<Row> {
    let mut rows = Vec::with_capacity(3 + STATUS_COUNT);
    if let Some(stance) = reading.stance.filter(has_stance) {
        let mut text = format!("{:.0} / {:.0}", stance.current.max(0.0), stance.max);
        if let Some(seconds) = shown_seconds(stance.recover_in) {
            text.push_str(&format!("  regen in {seconds:.1}s"));
        }
        rows.push(Row {
            kind: RowKind::Stance,
            label: "Stance",
            fraction: fraction(stance.current, stance.max),
            text,
            color: [0.85, 0.70, 0.30, 1.0],
            active: false,
        });
    }
    if has_pool(reading.stamina_max) {
        rows.push(Row {
            kind: RowKind::Stamina,
            label: "Stamina",
            fraction: fraction_i32(reading.stamina, reading.stamina_max),
            text: format!("{} / {}", reading.stamina.max(0), reading.stamina_max),
            color: [0.25, 0.65, 0.25, 1.0],
            active: false,
        });
    }
    if has_pool(reading.fp_max) {
        rows.push(Row {
            kind: RowKind::Fp,
            label: "FP",
            fraction: fraction_i32(reading.fp, reading.fp_max),
            text: format!("{} / {}", reading.fp.max(0), reading.fp_max),
            color: [0.20, 0.40, 0.85, 1.0],
            active: false,
        });
    }
    for status in visible_statuses(reading.statuses.as_ref(), active) {
        let index = status.index();
        let gauge = reading
            .statuses
            .map(|gauges| gauges[index])
            .unwrap_or_default();
        let is_active = active[index];
        let (filled, text) = if is_active {
            let seconds = reading.active[index].flatten().and_then(shown_seconds);
            let text = match seconds {
                Some(seconds) => format!("ACTIVE  {seconds:.1}s"),
                None => "ACTIVE".to_owned(),
            };
            (1.0, text)
        } else {
            (
                fraction_i32(gauge.buildup(), gauge.resistance),
                format!("{} / {}", gauge.buildup(), gauge.resistance),
            )
        };
        rows.push(Row {
            kind: RowKind::Status(status),
            label: status.label(),
            fraction: filled,
            text,
            color: status.color(),
            active: is_active,
        });
    }
    rows
}

/// Keeps a status reading as active for [`ACTIVE_HOLD_SECONDS`] after its row disappears.
#[derive(Clone, Debug, Default)]
pub struct ActiveHold {
    last_seen: [Option<f64>; STATUS_COUNT],
}

impl ActiveHold {
    /// Feed this frame's live flags at time `now` (seconds, any monotonic origin) and get back the
    /// flags to draw.
    pub fn observe(&mut self, now: f64, live: &[bool; STATUS_COUNT]) -> [bool; STATUS_COUNT] {
        let mut held = [false; STATUS_COUNT];
        for index in 0..STATUS_COUNT {
            if live[index] {
                self.last_seen[index] = Some(now);
            }
            held[index] = self.last_seen[index]
                .is_some_and(|seen| now >= seen && now - seen <= ACTIVE_HOLD_SECONDS);
        }
        held
    }

    /// Forget everything, for a new target.
    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

/// Raw `FieldInsHandle` qword: the selector in the low half, the block id in the high half.
pub fn handle_bits(selector: u32, block_id: i32) -> u64 {
    u64::from(selector) | (u64::from(block_id as u32) << 32)
}

/// The selector the lock-on manager writes when nothing is locked on.
///
/// `LockTgtMan`'s update (1.17.1 `0x14071806a`) stores `-1` over the whole handle at
/// `PlayerIns+0x6b0` when the lock drops, and `FieldInsHandle::is_empty` tests the selector half.
pub const EMPTY_SELECTOR: u32 = u32::MAX;

/// Selector type nibble (bits 28..31) of a character handle.
///
/// The lock-on point resolver (1.17.1 `0x140714c00`) refuses any handle whose
/// `selector & 0xf0000000` is not `0x10000000` before asking `WorldChrMan` for its owner. Every
/// live lock-on measured on 2026-10-04 carried selector type 1.
pub const CHR_SELECTOR_TYPE: u32 = 1;

/// Why the panel is or is not showing anyone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LockState {
    /// The handle is empty: nothing is locked on.
    NotLockedOn,
    /// Something is locked on but its handle does not name a character.
    NotACharacter,
    /// A character handle.
    Character,
}

/// Classify the lock-on handle before any lookup is attempted.
pub fn lock_state(selector: u32) -> LockState {
    if selector == EMPTY_SELECTOR {
        LockState::NotLockedOn
    } else if selector >> 28 == CHR_SELECTOR_TYPE {
        LockState::Character
    } else {
        LockState::NotACharacter
    }
}

/// Find the live character whose handle is `want` among `(address, handle)` candidates.
///
/// This is the stale-handle rejection. The lock-on handle is a name, not a pointer, and it can
/// outlive its character by a frame when the target dies or unloads. The candidates are read out
/// of the ChrSets the world currently owns, so a character that is gone is simply absent and the
/// answer is `None` -- nothing ever dereferences an address remembered from an earlier frame. A
/// null address never matches.
pub fn find_by_handle<I>(want: u64, candidates: I) -> Option<usize>
where
    I: IntoIterator<Item = (usize, u64)>,
{
    if lock_state(want as u32) != LockState::Character {
        return None;
    }
    candidates
        .into_iter()
        .find(|&(address, handle)| address != 0 && handle == want)
        .map(|(address, _)| address)
}

/// Is a reading published at `published_ms` still fit to draw at `now_ms`?
///
/// The game task stops publishing during loads and menus that pause the world; a panel that kept
/// drawing its last reading would show a character that may already be freed.
pub fn is_fresh(published_ms: u64, now_ms: u64, max_age_ms: u64) -> bool {
    now_ms >= published_ms && now_ms - published_ms <= max_age_ms
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gauges(pairs: [(i32, i32); STATUS_COUNT]) -> [StatusGauge; STATUS_COUNT] {
        pairs.map(|(gauge, resistance)| StatusGauge { gauge, resistance })
    }

    /// NpcParam 46000014 as the live probe read it on 2026-10-04.
    fn enemy() -> TargetReading {
        TargetReading {
            is_player: false,
            npc_param_id: 46_000_014,
            hp: 3_148,
            hp_max: 3_148,
            fp: 0,
            fp_max: 0,
            stamina: 163,
            stamina_max: 163,
            stance: Some(StanceReading {
                current: 65.0,
                max: 65.0,
                recover_in: -0.02,
            }),
            statuses: Some(gauges([
                (229, 229),
                (229, 229),
                (229, 229),
                (999, 999),
                (316, 316),
                (41, 41),
                (2050, 2050),
            ])),
            active: [None; STATUS_COUNT],
        }
    }

    fn kinds(rows: &[Row]) -> Vec<RowKind> {
        rows.iter().map(|row| row.kind).collect()
    }

    #[test]
    fn fractions_clamp_and_survive_a_zero_maximum() {
        assert_eq!(fraction(50.0, 100.0), 0.5);
        assert_eq!(fraction(150.0, 100.0), 1.0);
        assert_eq!(fraction(-5.0, 100.0), 0.0);
        assert_eq!(fraction(5.0, 0.0), 0.0);
        assert_eq!(fraction(f32::NAN, 10.0), 0.0);
        assert_eq!(fraction(1.0, f32::INFINITY), 0.0);
        assert_eq!(fraction_i32(1, 4), 0.25);
    }

    #[test]
    fn buildup_is_resistance_minus_gauge() {
        // The live frostbite hit: 316 -> 236.
        let hit = StatusGauge {
            gauge: 236,
            resistance: 316,
        };
        assert_eq!(hit.buildup(), 80);
        let full = StatusGauge {
            gauge: 316,
            resistance: 316,
        };
        assert_eq!(full.buildup(), 0);
        // A torn read outside [0, resistance] must not overflow the bar.
        let over = StatusGauge {
            gauge: -20,
            resistance: 300,
        };
        assert_eq!(over.buildup(), 300);
        let none = StatusGauge {
            gauge: 0,
            resistance: 0,
        };
        assert_eq!(none.buildup(), 0);
    }

    #[test]
    fn an_ordinary_enemy_gets_stance_and_stamina_but_no_hp_copy_and_no_fp() {
        let rows = panel_rows(&enemy(), &[false; STATUS_COUNT]);
        assert_eq!(kinds(&rows), vec![RowKind::Stance, RowKind::Stamina]);
        assert_eq!(rows[1].text, "163 / 163");
    }

    #[test]
    fn the_constructor_placeholder_is_no_pool() {
        let mut reading = enemy();
        reading.fp = 1;
        reading.fp_max = 1;
        reading.stamina = 1;
        reading.stamina_max = 1;
        let rows = panel_rows(&reading, &[false; STATUS_COUNT]);
        assert_eq!(kinds(&rows), vec![RowKind::Stance]);
    }

    #[test]
    fn a_target_with_both_pools_gets_both_bars() {
        let mut reading = enemy();
        reading.is_player = true;
        reading.fp = 40;
        reading.fp_max = 120;
        let rows = panel_rows(&reading, &[false; STATUS_COUNT]);
        assert_eq!(
            kinds(&rows),
            vec![RowKind::Stance, RowKind::Stamina, RowKind::Fp]
        );
        assert_eq!(rows[2].text, "40 / 120");
    }

    #[test]
    fn the_stance_row_says_when_it_regenerates() {
        let mut reading = enemy();
        reading.stance = Some(StanceReading {
            current: 40.0,
            max: 80.0,
            recover_in: 2.5,
        });
        let rows = panel_rows(&reading, &[false; STATUS_COUNT]);
        assert_eq!(rows[0].text, "40 / 80  regen in 2.5s");
        assert_eq!(rows[0].fraction, 0.5);
        // At rest the timer sits just below zero (live: -0.0196); nothing to count down.
        let rows = panel_rows(&enemy(), &[false; STATUS_COUNT]);
        assert_eq!(rows[0].text, "65 / 65");
    }

    #[test]
    fn a_zero_maximum_stance_is_no_stance_bar() {
        let mut reading = enemy();
        reading.stance = Some(StanceReading::default());
        let rows = panel_rows(&reading, &[false; STATUS_COUNT]);
        assert!(rows.iter().all(|row| row.kind != RowKind::Stance));
    }

    #[test]
    fn only_filling_or_active_statuses_get_rows() {
        let mut reading = enemy();
        let mut statuses = reading.statuses.unwrap();
        statuses[Status::Frostbite.index()].gauge = 236;
        reading.statuses = Some(statuses);
        let mut active = [false; STATUS_COUNT];
        active[Status::BloodLoss.index()] = true;
        reading.active[Status::BloodLoss.index()] = Some(Some(0.8));
        let rows = panel_rows(&reading, &active);
        let statuses: Vec<RowKind> = kinds(&rows)
            .into_iter()
            .filter(|kind| matches!(kind, RowKind::Status(_)))
            .collect();
        assert_eq!(
            statuses,
            vec![
                RowKind::Status(Status::BloodLoss),
                RowKind::Status(Status::Frostbite)
            ]
        );
        let frost = rows
            .iter()
            .find(|row| row.kind == RowKind::Status(Status::Frostbite))
            .unwrap();
        assert_eq!(frost.text, "80 / 316");
        assert!((frost.fraction - 80.0 / 316.0).abs() < 1e-6);
        let bleed = rows
            .iter()
            .find(|row| row.kind == RowKind::Status(Status::BloodLoss))
            .unwrap();
        assert!(bleed.active);
        assert_eq!(bleed.fraction, 1.0);
        assert_eq!(bleed.text, "ACTIVE  0.8s");
    }

    #[test]
    fn an_immune_status_never_gets_a_row() {
        let mut reading = enemy();
        let mut statuses = reading.statuses.unwrap();
        // Even a torn read that makes blight look half full, or a live row of it.
        statuses[Status::DeathBlight.index()].gauge = 500;
        reading.statuses = Some(statuses);
        let mut active = [false; STATUS_COUNT];
        active[Status::DeathBlight.index()] = true;
        let rows = panel_rows(&reading, &active);
        assert!(
            rows.iter()
                .all(|row| row.kind != RowKind::Status(Status::DeathBlight))
        );
        // A low real threshold is not immunity.
        let sleep = StatusGauge {
            gauge: 41,
            resistance: 41,
        };
        assert!(!sleep.immune());
    }

    #[test]
    fn an_active_status_with_no_finite_timer_says_only_active() {
        let mut reading = enemy();
        let mut active = [false; STATUS_COUNT];
        active[Status::Poison.index()] = true;
        reading.active[Status::Poison.index()] = Some(None);
        let rows = panel_rows(&reading, &active);
        assert_eq!(rows.last().unwrap().text, "ACTIVE");
        reading.active[Status::Poison.index()] = Some(Some(-1.0));
        let rows = panel_rows(&reading, &active);
        assert_eq!(rows.last().unwrap().text, "ACTIVE");
    }

    #[test]
    fn a_target_without_a_resist_module_still_shows_active_statuses() {
        let mut reading = enemy();
        reading.statuses = None;
        let mut active = [false; STATUS_COUNT];
        active[Status::Sleep.index()] = true;
        let rows = panel_rows(&reading, &active);
        assert_eq!(rows.last().unwrap().kind, RowKind::Status(Status::Sleep));
    }

    #[test]
    fn state_info_round_trips_for_every_status() {
        for status in Status::ALL {
            assert_eq!(Status::from_state_info(status.state_info()), Some(status));
        }
        assert_eq!(Status::from_state_info(0), None);
        assert_eq!(Status::from_state_info(0x2b), None);
        let indexes: Vec<usize> = Status::ALL.iter().map(|status| status.index()).collect();
        assert_eq!(indexes, (0..STATUS_COUNT).collect::<Vec<_>>());
    }

    #[test]
    fn live_flags_follow_the_active_entries() {
        let mut reading = enemy();
        reading.active[Status::Madness.index()] = Some(None);
        let flags = reading.live_flags();
        assert!(flags[Status::Madness.index()]);
        assert_eq!(flags.iter().filter(|flag| **flag).count(), 1);
    }

    #[test]
    fn a_one_second_proc_is_held_long_enough_to_read() {
        let mut hold = ActiveHold::default();
        let mut live = [false; STATUS_COUNT];
        live[Status::BloodLoss.index()] = true;
        assert!(hold.observe(10.0, &live)[Status::BloodLoss.index()]);
        let quiet = [false; STATUS_COUNT];
        assert!(hold.observe(11.0, &quiet)[Status::BloodLoss.index()]);
        assert!(hold.observe(11.5, &quiet)[Status::BloodLoss.index()]);
        assert!(!hold.observe(11.6, &quiet)[Status::BloodLoss.index()]);
        // A new target forgets the old one's procs.
        hold.observe(20.0, &live);
        hold.reset();
        assert!(!hold.observe(20.1, &quiet)[Status::BloodLoss.index()]);
    }

    #[test]
    fn the_lock_on_handle_is_classified_before_any_lookup() {
        assert_eq!(lock_state(u32::MAX), LockState::NotLockedOn);
        // The live lock on NpcParam 46000014: handle 0x0a000000_100000ca.
        assert_eq!(lock_state(0x1000_00ca), LockState::Character);
        assert_eq!(lock_state(0x3000_0001), LockState::NotACharacter);
        assert_eq!(lock_state(0), LockState::NotACharacter);
    }

    #[test]
    fn a_stale_handle_resolves_to_nothing() {
        let target = handle_bits(0x1000_00ca, 0x0a00_0000);
        assert_eq!(target, 0x0a00_0000_1000_00ca);
        let other = handle_bits(0x1000_00cb, 0x0a00_0000);
        assert_eq!(
            find_by_handle(target, [(0x1000, other), (0x2000, target)]),
            Some(0x2000)
        );
        // The target died and its slot is gone from every set: no address, no dereference.
        assert_eq!(find_by_handle(target, [(0x1000, other)]), None);
        // A null slot never matches, even with the right handle.
        assert_eq!(find_by_handle(target, [(0, target)]), None);
        // An empty or non-character handle never searches at all.
        assert_eq!(find_by_handle(u64::MAX, [(0x1000, u64::MAX)]), None);
        let geom = handle_bits(0x6000_0001, -1);
        assert_eq!(find_by_handle(geom, [(0x1000, geom)]), None);
    }

    #[test]
    fn handle_bits_keep_a_negative_block_id_in_the_high_half_only() {
        assert_eq!(handle_bits(0x1000_0001, -1), 0xffff_ffff_1000_0001);
        assert_eq!(handle_bits(u32::MAX, -1), u64::MAX);
    }

    #[test]
    fn a_reading_goes_stale_when_the_game_task_stops_publishing() {
        assert!(is_fresh(1_000, 1_100, 500));
        assert!(!is_fresh(1_000, 1_600, 500));
        assert!(!is_fresh(2_000, 1_000, 500));
    }
}
