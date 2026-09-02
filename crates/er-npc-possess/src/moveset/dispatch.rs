//! Four buttons onto a moveset of eight to sixty animations, without a per-creature layout.
//!
//! # Why four fixed buttons and not a slot per attack
//!
//! Creatures have between 8 and 60 fireable attacks with no shared structure -- a rat, Malenia and
//! a Flying Dragon share exactly three things: they have attacks, they have locomotion, and they
//! can turn. So a static slot map would have to be authored 268 times and would still leave most
//! of every moveset unreachable. Instead the button says what KIND of thing to do (`r1` light, `r2`
//! heavy, `l1` ranged, `l2` movement, identical on every creature), and three things pick WHICH:
//!
//! * the **distance band**, so a press at range means something different from a press in melee;
//! * the **rank cursor**, which walks the bucket in the generator's fixed order, so repeated
//!   presses give the whole moveset rather than the same swing forever;
//! * the **combo window**, which resets that cursor once the player stops -- otherwise the cursor
//!   would drift and the same button would give a different attack every time you picked the pad
//!   up.
//!
//! # The three models
//!
//! [`MappingModel::Context`] is the default and the one the rest of this doc describes.
//! [`MappingModel::Layered`] ignores reach and instead gives each distance band its own contiguous
//! slice of the bucket's rank order, which is more predictable and less situationally correct.
//! [`MappingModel::Slots`] does no filtering at all: every press walks the whole bucket. It exists
//! for creatures whose reach classification came back mostly [`Reach::Unknown`], where filtering
//! removes moves for no good reason.
//!
//! # What is a heuristic here, and named as one
//!
//! Locomotion shifts the effective distance band one step CLOSER while the creature is moving, on
//! the reasoning that an attack chosen mid-run lands after the run has closed some of the gap.
//! That is a guess about intent, not a measurement of root-motion travel -- the generator has no
//! travel distance, because getting one means deserialising the animation itself.

// Pure decision logic over the table and the config; no game memory. Ungated so `cargo test`
// proves it on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::moveset::chain::{self, Availability, Buffer, Held, Playing, Release};
use crate::moveset::table::{Move, Moveset, Reach};
use crate::settings::{Bucket, ButtonSettings, MappingModel, MappingSettings, UnboundInputs};

/// The four face inputs, fixed on every creature.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Input {
    R1,
    R2,
    L1,
    L2,
}

impl Input {
    pub(crate) const ALL: [Self; 4] = [Self::R1, Self::R2, Self::L1, Self::L2];

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::R1 => "r1",
            Self::R2 => "r2",
            Self::L1 => "l1",
            Self::L2 => "l2",
        }
    }

    const fn index(self) -> usize {
        match self {
            Self::R1 => 0,
            Self::R2 => 1,
            Self::L1 => 2,
            Self::L2 => 3,
        }
    }

    const fn bucket(self, buttons: ButtonSettings) -> Bucket {
        match self {
            Self::R1 => buttons.r1,
            Self::R2 => buttons.r2,
            Self::L1 => buttons.l1,
            Self::L2 => buttons.l2,
        }
    }
}

/// How far away the thing you are pointing at is, in the same three bands as [`Reach`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Band {
    Close,
    Mid,
    Far,
}

impl Band {
    /// One band closer, saturating. The locomotion heuristic; see the module docs.
    const fn closer(self) -> Self {
        match self {
            Self::Far => Self::Mid,
            Self::Mid | Self::Close => Self::Close,
        }
    }

    const fn matches(self, reach: Reach) -> bool {
        match reach {
            // The generator declined to measure this one, so it is offered everywhere rather than
            // nowhere. Removing unmeasured moves would quietly shrink the movesets of every
            // creature whose attacks are bullet-only or marker-only.
            Reach::Unknown => true,
            Reach::Close => matches!(self, Self::Close),
            Reach::Mid => matches!(self, Self::Mid | Self::Close),
            Reach::Far => matches!(self, Self::Far | Self::Mid),
        }
    }

    const fn index(self) -> usize {
        match self {
            Self::Close => 0,
            Self::Mid => 1,
            Self::Far => 2,
        }
    }
}

/// Is the creature standing still or moving? Drives both the locomotion heuristic and the combo
/// reset.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Locomotion {
    Neutral,
    Moving,
}

/// What the world looks like at the moment of a press.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Context {
    /// Metres to the lock-on target, or to the nearest hostile when there is none. `None` when
    /// nothing is in range at all, which reads as [`Band::Far`].
    ///
    /// It is also the reading a creature-victim grab is checked against; see
    /// [`crate::moveset::table::Throws::reachable`], which deliberately does NOT apply it to a
    /// player-victim grab.
    pub(crate) distance_m: Option<f32>,
    pub(crate) locomotion: Locomotion,
    /// Milliseconds since possession start. Monotonic; the combo window is measured against it.
    pub(crate) now_ms: u64,
}

impl Context {
    fn band(self, bands: (f32, f32)) -> Band {
        let raw = match self.distance_m {
            None => Band::Far,
            Some(distance) if distance < bands.0 => Band::Close,
            Some(distance) if distance < bands.1 => Band::Mid,
            Some(_) => Band::Far,
        };
        match self.locomotion {
            Locomotion::Moving => raw.closer(),
            Locomotion::Neutral => raw,
        }
    }
}

/// Why a press produced nothing, so the log can say something better than silence.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum NoMove {
    /// The creature has no moves in this bucket and `unbound_inputs = "deny"`.
    EmptyBucket,
    /// The bucket has moves, but none whose reach suits the current distance band, and no
    /// fallback was allowed.
    NothingInBand,
    /// The whole moveset is empty -- an unknown creature, or one the generator found nothing
    /// fireable on.
    NoMoveset,
    /// Everything this button could have fired is a grab, and `allow_grabs = false`.
    ///
    /// Worth its own reason rather than folding into [`Self::NothingInBand`]: the fix is one line
    /// of config, and a player who set the flag months ago will not connect a dead button to it.
    GrabsWithheld,
    /// Everything this button could have fired is a grab whose `ThrowParam` row demands a CREATURE
    /// victim, and no creature is inside that row's `Dist`. See
    /// [`crate::moveset::table::Throws::reachable`].
    NoThrowVictim,
}

impl NoMove {
    /// One clause, written to be read mid-sentence in the possession log.
    pub(crate) const fn explanation(self) -> &'static str {
        match self {
            Self::EmptyBucket => "that bucket is empty for this creature",
            Self::NothingInBand => "nothing in this bucket suits the range",
            Self::NoMoveset => "this creature has no shipped moveset",
            Self::GrabsWithheld => "everything here is a grab and allow_grabs is off",
            Self::NoThrowVictim => {
                "everything here is a grab that needs another creature within its throw range"
            }
        }
    }
}

/// What one press turned into.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Press {
    /// Fire this now -- a fresh attack, or a chain out of one that has already landed.
    Fire(Move),
    /// The creature is still committed to what it is doing. The press is waiting, and
    /// [`Dispatcher::release`] will spend it. Not a refusal, and not a cancel.
    Waiting(Held),
    /// This button has nothing to give on this creature.
    Nothing(NoMove),
}

/// What a waiting press turned into, once the creature would take it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Released {
    /// Nothing was waiting, or what is waiting is still waiting.
    Nothing,
    /// The waiting press fires now.
    Fire(Input, Move),
    /// The waiting press came due and the button had nothing to give after all -- possible
    /// because the world moved between the press and the release, and the creature is no longer
    /// standing where it was.
    Empty(Input, NoMove),
    /// The waiting press outlived `[mapping] input_buffer_ms` and is gone.
    Expired(Input),
}

/// One possession's worth of input mapping.
#[derive(Clone, Debug)]
pub(crate) struct Dispatcher {
    moveset: Moveset,
    mapping: MappingSettings,
    buttons: ButtonSettings,
    /// Per-input rank cursor. Indexed by [`Input::index`].
    cursor: [u16; 4],
    /// When the last press landed, for the combo window.
    last_press_ms: Option<u64>,
    /// Animation ids pinned by `[chr.*] pin`, indexed like `cursor`.
    pins: [Option<i32>; 4],
    /// The one press waiting for the current animation to let go. See [`crate::moveset::chain`].
    buffer: Buffer,
}

impl Dispatcher {
    pub(crate) fn new(moveset: Moveset, mapping: MappingSettings, buttons: ButtonSettings) -> Self {
        Self {
            moveset,
            mapping,
            buttons,
            cursor: [0; 4],
            last_press_ms: None,
            pins: [None; 4],
            buffer: Buffer::new(mapping.input_buffer_ms),
        }
    }

    pub(crate) fn moveset(&self) -> &Moveset {
        &self.moveset
    }

    pub(crate) fn moveset_mut(&mut self) -> &mut Moveset {
        &mut self.moveset
    }

    /// Apply a `[chr.*] pin = { r2 = 3046 }` entry. Unknown input names are ignored by the caller.
    pub(crate) fn pin(&mut self, input: Input, fire: i32) {
        self.pins[input.index()] = Some(fire);
    }

    /// The creature is back in a neutral state: drop every cursor to rank 0.
    ///
    /// This, not the timer, is the primary reset. A player who finishes a combo and stands still
    /// expects the next press to be the first attack again, whether that took 200 ms or two
    /// seconds.
    ///
    /// A waiting press suspends it, and without that the whole feature reads as broken. The
    /// animation ending is the moment a buffered press is released, and the watchdog announces
    /// that same moment as a return to neutral -- one frame EARLIER, because it runs first by
    /// design. Resetting there would hand the release a cursor of 0, so a press made during the
    /// first swing would replay the first swing instead of continuing to the second, and the
    /// player would see a combo that never advances. The creature has not stopped attacking; it
    /// is between two attacks of the same chain.
    pub(crate) fn on_neutral(&mut self) {
        if self.buffer.is_holding() {
            return;
        }
        self.cursor = [0; 4];
        self.last_press_ms = None;
    }

    /// May this move be offered at all, given the config and who is standing nearby?
    ///
    /// Two separate refusals, both of which the derived file spells out rather than leaving the
    /// player to guess:
    ///
    /// * `allow_grabs = false` withholds every grab. Since the `ThrowParam` join this withholds
    ///   153 real, fireable attacks across 78 creatures -- before it, the flag matched nothing.
    /// * a grab whose `ThrowParam` row demands a CREATURE victim is withheld unless something is
    ///   within that row's `Dist`. `ValidateAttemptAndReturnParamId` would refuse the throw
    ///   anyway; the difference is that the press is spent on a swing that could still land
    ///   instead of on one that provably cannot become a grab.
    ///
    /// A grab whose victim is the player is never withheld for distance -- see
    /// [`crate::moveset::table::Throws::reachable`].
    fn offerable(&self, entry: &Move, distance_m: Option<f32>) -> bool {
        if !entry.grab() {
            return true;
        }
        self.mapping.allow_grabs && entry.throws.reachable(distance_m)
    }

    /// The candidate list for one input in one context, in rank order.
    fn candidates(&self, input: Input, band: Band, distance_m: Option<f32>) -> Vec<&Move> {
        let bucket = input.bucket(self.buttons);
        let in_bucket: Vec<&Move> = self
            .moveset
            .bucket(bucket)
            .filter(|entry| self.offerable(entry, distance_m))
            .collect();
        match self.mapping.model {
            MappingModel::Slots => in_bucket,
            MappingModel::Context => in_bucket
                .into_iter()
                .filter(|entry| band.matches(entry.reach))
                .collect(),
            MappingModel::Layered => {
                // Split the bucket into three contiguous slices of the rank order and give the
                // band its own. Low ranks are the generator's short/cheap end, so close gets them.
                let total = in_bucket.len();
                if total < 3 {
                    return in_bucket;
                }
                let slice = total / 3;
                let start = band.index() * slice;
                let end = if band == Band::Far {
                    total
                } else {
                    start + slice
                };
                in_bucket[start..end].to_vec()
            }
        }
    }

    /// The order promotion tries buckets in when the asked-for one is empty.
    ///
    /// It ends by trying EVERY bucket, and it has to. The obvious design -- walk toward `Light`
    /// and stop, on the reasoning that anything with attacks has a light one -- is false against
    /// the shipped table: c120 is entirely `Ranged`, so `r1` had nothing to promote to and the
    /// button was dead. `every_creature_in_the_shipped_table_answers_every_button_in_every_band`
    /// is the test that found it and the one that keeps it fixed.
    ///
    /// The order still expresses a preference: melee before ranged before movement, so a promoted
    /// `r1` gives something as close to "a light attack" as the creature can manage rather than
    /// whichever bucket happened to be checked first.
    const PROMOTION_ORDER: [Bucket; 4] = [
        Bucket::Light,
        Bucket::Heavy,
        Bucket::Ranged,
        Bucket::Movement,
    ];

    /// Which animation one press gives, ignoring whether it may be fired yet.
    ///
    /// Private on purpose. [`Self::press`] is the only way in, because the cancel discipline it
    /// applies has to be structural: a caller that could reach this directly would be able to
    /// interrupt an attack again by accident, which is the exact bug this layer exists to remove.
    fn choose(&mut self, input: Input, context: Context) -> Result<Move, NoMove> {
        if self.moveset.is_empty() {
            return Err(NoMove::NoMoveset);
        }
        if let Some(fire) = self.pins[input.index()]
            && let Some(entry) = self.moveset.find(fire)
        {
            // A pin is the player overriding the whole mechanism for one button. It does not
            // advance the cursor, because there is nothing to advance through.
            self.last_press_ms = Some(context.now_ms);
            return Ok(*entry);
        }

        let band = context.band(self.mapping.bands_m);
        let mut chosen: Vec<Move> = self
            .candidates(input, band, context.distance_m)
            .into_iter()
            .copied()
            .collect();
        let asked = input.bucket(self.buttons);
        if chosen.is_empty() && self.mapping.unbound_inputs == UnboundInputs::Promote {
            // Widen in two steps, cheapest first: the SAME bucket ignoring the band, then another
            // bucket. Dropping the band before dropping the bucket keeps the button's MEANING
            // intact, which is the whole property the fixed layout exists for -- a promoted `r1`
            // should be a light attack out of range before it is a heavy one in range.
            let whole_bucket = |bucket| -> Vec<Move> {
                self.moveset
                    .bucket(bucket)
                    .filter(|entry| self.offerable(entry, context.distance_m))
                    .copied()
                    .collect()
            };
            chosen = whole_bucket(asked);
            for bucket in Self::PROMOTION_ORDER {
                if !chosen.is_empty() {
                    break;
                }
                if bucket != asked {
                    chosen = whole_bucket(bucket);
                }
            }
        }
        if chosen.is_empty() {
            return Err(self.why_nothing(input, context.distance_m));
        }
        chosen.sort_by_key(|entry| entry.rank);

        let expired = self.last_press_ms.is_none_or(|last| {
            context.now_ms.saturating_sub(last) > self.mapping.combo_window_ms.into()
        });
        let slot = self.cursor[input.index()];
        let index = if expired {
            0
        } else {
            usize::from(slot) % chosen.len()
        };
        self.cursor[input.index()] = u16::try_from(index + 1).unwrap_or(0);
        self.last_press_ms = Some(context.now_ms);
        Ok(chosen[index])
    }

    /// What the creature will accept right now, given what it is playing.
    ///
    /// The window belongs to the move being played, not to the one about to be asked for, which is
    /// why the lookup goes through [`Moveset::playing`] and not [`Moveset::find`].
    pub(crate) fn availability(&self, playing: Option<Playing>) -> Availability {
        let window = playing
            .and_then(|state| self.moveset.playing(state.animation))
            .and_then(Move::chain_from_s);
        chain::availability(playing, window)
    }

    /// Drive one press. The only way in, and the only place the cancel rule is applied.
    ///
    /// Three answers, which are the three things a press can honestly mean:
    ///
    /// * the creature is idle or the playing attack has already landed -- fire, and that is either
    ///   a fresh attack or a chain;
    /// * the playing attack is still committed -- HOLD the press. It fires from
    ///   [`Self::release`] the moment the animation lets go, and the attack that was already
    ///   running is not disturbed;
    /// * the button has nothing to give on this creature -- say which of the [`NoMove`] reasons.
    pub(crate) fn press(
        &mut self,
        input: Input,
        context: Context,
        availability: Availability,
    ) -> Press {
        if availability.accepts_a_press() {
            return match self.choose(input, context) {
                Ok(chosen) => Press::Fire(chosen),
                Err(reason) => Press::Nothing(reason),
            };
        }
        Press::Waiting(self.buffer.hold(input, context.now_ms))
    }

    /// One frame. Fires the press that was waiting, if the creature will now take it.
    pub(crate) fn release(&mut self, context: Context, availability: Availability) -> Released {
        match self.buffer.release(context.now_ms, availability) {
            Release::Nothing => Released::Nothing,
            Release::Expired(input) => Released::Expired(input),
            Release::Fire(input) => match self.choose(input, context) {
                Ok(chosen) => Released::Fire(input, chosen),
                Err(reason) => Released::Empty(input, reason),
            },
        }
    }

    /// Is a press waiting? The driver asks before paying for the distance reading a
    /// [`Context`] needs on a frame where nothing was pressed.
    pub(crate) const fn is_holding(&self) -> bool {
        self.buffer.is_holding()
    }

    /// Throw away a waiting press without firing it. Used when the watchdog forces idle: that
    /// animation was denied for the rest of the session and a queued follow-up to it is stale.
    pub(crate) const fn forget_buffered_press(&mut self) {
        self.buffer.forget();
    }

    /// Which of the reasons in [`NoMove`] applies, once a press has come up empty.
    ///
    /// The grab reasons are checked FIRST and only when they explain the WHOLE bucket. A bucket
    /// that also holds ordinary attacks came up empty for a range or promotion reason, and blaming
    /// the grabs would point the player at the wrong setting.
    fn why_nothing(&self, input: Input, distance_m: Option<f32>) -> NoMove {
        let bucket = input.bucket(self.buttons);
        let mut any = false;
        let mut all_grabs = true;
        for entry in self.moveset.bucket(bucket) {
            any = true;
            all_grabs &= entry.grab();
        }
        if !any {
            return NoMove::EmptyBucket;
        }
        if all_grabs {
            if !self.mapping.allow_grabs {
                return NoMove::GrabsWithheld;
            }
            if !self
                .moveset
                .bucket(bucket)
                .any(|entry| entry.throws.reachable(distance_m))
            {
                return NoMove::NoThrowVictim;
            }
        }
        NoMove::NothingInBand
    }

    /// A one-line description of what this creature can do, for the log.
    pub(crate) fn summary(&self) -> String {
        let count = |bucket| self.moveset.bucket(bucket).count();
        let grabs = self
            .moveset
            .moves
            .iter()
            .filter(|entry| entry.grab())
            .count();
        format!(
            "moves={} (light={} heavy={} ranged={} movement={}) denied={} model={} grabs={} ({} \
             throw initiator{})",
            self.moveset.moves.len(),
            count(Bucket::Light),
            count(Bucket::Heavy),
            count(Bucket::Ranged),
            count(Bucket::Movement),
            self.moveset.denials.len(),
            self.mapping.model.name(),
            if self.mapping.allow_grabs {
                "on"
            } else {
                "off"
            },
            grabs,
            if grabs == 1 { "" } else { "s" },
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::moveset::table::parse_line;

    fn moveset(line: &str) -> Moveset {
        parse_line(line).expect("well-formed test line").1
    }

    fn dispatcher(line: &str) -> Dispatcher {
        Dispatcher::new(
            moveset(line),
            MappingSettings::default(),
            ButtonSettings::default(),
        )
    }

    const fn context(distance: f32, now_ms: u64) -> Context {
        Context {
            distance_m: Some(distance),
            locomotion: Locomotion::Neutral,
            now_ms,
        }
    }

    #[test]
    fn the_default_button_layout_is_the_documented_one() {
        let buttons = ButtonSettings::default();
        assert_eq!(Input::R1.bucket(buttons), Bucket::Light);
        assert_eq!(Input::R2.bucket(buttons), Bucket::Heavy);
        assert_eq!(Input::L1.bucket(buttons), Bucket::Ranged);
        assert_eq!(Input::L2.bucket(buttons), Bucket::Movement);
    }

    #[test]
    fn repeated_presses_walk_the_bucket_in_rank_order_and_wrap() {
        // Three light attacks, ranks 0..2.
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:1 3002:0:2:1");
        let fired: Vec<i32> = (0..5)
            .map(|step| {
                engine
                    .choose(Input::R1, context(1.0, 100 * step))
                    .expect("light bucket is populated")
                    .fire
            })
            .collect();
        assert_eq!(fired, vec![3000, 3001, 3002, 3000, 3001]);
    }

    #[test]
    fn the_combo_window_expiring_resets_the_cursor_to_rank_zero() {
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:1 3002:0:2:1");
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3000
        );
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 500)).unwrap().fire,
            3001
        );
        // Default combo_window_ms is 1200; 500 -> 2000 is 1500 of silence.
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 2000)).unwrap().fire,
            3000,
            "a press after the combo window must start the chain again"
        );
    }

    #[test]
    fn a_press_exactly_on_the_combo_window_boundary_still_continues_the_chain() {
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:1");
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3000
        );
        let window = u64::from(MappingSettings::default().combo_window_ms);
        assert_eq!(
            engine.choose(Input::R1, context(1.0, window)).unwrap().fire,
            3001,
            "the window is inclusive; only strictly later presses reset"
        );
    }

    #[test]
    fn returning_to_neutral_resets_every_cursor() {
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:1");
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3000
        );
        engine.on_neutral();
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 10)).unwrap().fire,
            3000,
            "neutral, not the clock, is the primary reset"
        );
    }

    #[test]
    fn distance_selects_between_a_close_and_a_far_attack() {
        // One close-reach light attack and one far-reach light attack.
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:3");
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3000
        );
        engine.on_neutral();
        assert_eq!(
            engine.choose(Input::R1, context(30.0, 0)).unwrap().fire,
            3001
        );
    }

    #[test]
    fn moving_shifts_the_band_one_step_closer() {
        // Rank 0 is a MID-reach attack, rank 1 a far-reach one.
        let mut engine = dispatcher("4500 3000:0:0:2 3001:0:1:3");
        // 30 m standing still is Far, and a mid-reach attack would whiff there, so only the
        // far-reach one is a candidate.
        assert_eq!(
            engine.choose(Input::R1, context(30.0, 0)).unwrap().fire,
            3001
        );
        engine.on_neutral();
        // Moving, the same 30 m reads as Mid -- which accepts both the mid-reach attack and the
        // far-reach one -- so rank 0 comes back into play.
        let moving = Context {
            distance_m: Some(30.0),
            locomotion: Locomotion::Moving,
            now_ms: 0,
        };
        assert_eq!(engine.choose(Input::R1, moving).unwrap().fire, 3000);
    }

    /// A close-reach attack must NOT be offered at mid range even when the creature is running:
    /// the heuristic shifts the band by one step, it does not abolish reach.
    #[test]
    fn the_locomotion_shift_is_one_step_and_not_a_free_pass() {
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:3");
        let moving = Context {
            distance_m: Some(30.0),
            locomotion: Locomotion::Moving,
            now_ms: 0,
        };
        assert_eq!(
            engine.choose(Input::R1, moving).unwrap().fire,
            3001,
            "Far shifted to Mid still excludes a close-reach swing"
        );
    }

    /// The failure that produced [`Dispatcher::PROMOTION_ORDER`]: c120's whole moveset is
    /// `Ranged`, so promotion that walked toward `Light` and stopped left `r1` dead.
    #[test]
    fn promotion_reaches_a_bucket_that_is_not_on_the_way_to_light() {
        let mut engine = dispatcher("120 3000:2:0:3 3001:2:1:3");
        let chosen = engine
            .choose(Input::R1, context(1.0, 0))
            .expect("a creature with only ranged attacks must still answer r1");
        assert_eq!(chosen.bucket, Bucket::Ranged);
    }

    #[test]
    fn an_unmeasured_reach_is_offered_in_every_band() {
        let mut engine = dispatcher("4500 3000:0:0:0");
        for distance in [0.5, 8.0, 40.0] {
            engine.on_neutral();
            assert_eq!(
                engine.choose(Input::R1, context(distance, 0)).unwrap().fire,
                3000,
                "reach unknown must not mean reach nowhere"
            );
        }
    }

    #[test]
    fn grabs_are_offered_by_default_and_withheld_when_the_config_says_so() {
        // c2120's real grab: a 3000-band attack that starts a throw on the player.
        let line = "2120 3022g0,100:0:0:1";
        let mut allowed = dispatcher(line);
        assert!(
            MappingSettings::default().allow_grabs,
            "the shipped default must be to allow grabs -- they are the signature move of most \
             bosses, and this flag now withholds 153 real attacks"
        );
        assert_eq!(
            allowed.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3022
        );

        let mapping = MappingSettings {
            allow_grabs: false,
            ..MappingSettings::default()
        };
        let mut denied = Dispatcher::new(moveset(line), mapping, ButtonSettings::default());
        assert_eq!(
            denied.choose(Input::R1, context(1.0, 0)),
            Err(NoMove::GrabsWithheld),
            "the reason has to name the setting, or a player who set it months ago will read a \
             dead button as a broken mod"
        );
    }

    /// A grab whose `ThrowParam` row demands a CREATURE victim is not offered when there is no
    /// creature inside that row's `Dist` -- the throw system would refuse it anyway, and the press
    /// is better spent on something that can land.
    #[test]
    fn a_creature_victim_grab_is_withheld_when_nothing_is_in_throw_range() {
        let line = "4280 3006g3300,100:0:0:1";
        let mut engine = dispatcher(line);
        assert_eq!(
            engine.choose(Input::R1, context(5.0, 0)).unwrap().fire,
            3006
        );

        let mut far = dispatcher(line);
        assert_eq!(
            far.choose(Input::R1, context(40.0, 0)),
            Err(NoMove::NoThrowVictim)
        );

        let mut empty = dispatcher(line);
        assert_eq!(
            empty.choose(
                Input::R1,
                Context {
                    distance_m: None,
                    locomotion: Locomotion::Neutral,
                    now_ms: 0,
                }
            ),
            Err(NoMove::NoThrowVictim)
        );
    }

    /// ...and the converse, which is the case that covers 189 of the game's 190 creature throw
    /// rows: the victim is the PLAYER, whose body the possession keeps co-located with the
    /// creature. A hostile-distance reading is about somebody else and must not veto it.
    #[test]
    fn a_player_victim_grab_is_offered_however_far_away_the_nearest_hostile_is() {
        let mut engine = dispatcher("2120 3022g0,100:0:0:1");
        assert_eq!(
            engine
                .choose(
                    Input::R1,
                    Context {
                        distance_m: None,
                        locomotion: Locomotion::Neutral,
                        now_ms: 0,
                    }
                )
                .unwrap()
                .fire,
            3022
        );
    }

    /// A bucket that ALSO holds ordinary attacks came up empty for a range reason, so blaming the
    /// grabs would point the player at a setting that is not the problem.
    #[test]
    fn a_mixed_bucket_is_never_blamed_on_the_grab_settings() {
        let mapping = MappingSettings {
            allow_grabs: false,
            unbound_inputs: UnboundInputs::Deny,
            ..MappingSettings::default()
        };
        // One grab and one close-range attack; press at long range with promotion off.
        let mut engine = Dispatcher::new(
            moveset("2120 3022g0,100:0:0:1 3023:0:1:1"),
            mapping,
            ButtonSettings::default(),
        );
        assert_eq!(
            engine.choose(Input::R1, context(40.0, 0)),
            Err(NoMove::NothingInBand)
        );
    }

    #[test]
    fn every_no_move_reason_explains_itself() {
        for reason in [
            NoMove::EmptyBucket,
            NoMove::NothingInBand,
            NoMove::NoMoveset,
            NoMove::GrabsWithheld,
            NoMove::NoThrowVictim,
        ] {
            let text = reason.explanation();
            assert!(!text.is_empty(), "{reason:?}");
            assert!(
                !text.ends_with('.'),
                "{reason:?} is read mid-sentence: {text}"
            );
        }
    }

    #[test]
    fn an_empty_bucket_promotes_by_default_and_denies_when_told_to() {
        // Nothing ranged; `l1` has to come from somewhere or do nothing.
        let line = "4500 3000:0:0:1 3001:1:0:1";
        let mut promoting = dispatcher(line);
        assert!(
            promoting.choose(Input::L1, context(1.0, 0)).is_ok(),
            "promote must find the heavy bucket"
        );

        let mapping = MappingSettings {
            unbound_inputs: UnboundInputs::Deny,
            ..MappingSettings::default()
        };
        let mut denying = Dispatcher::new(moveset(line), mapping, ButtonSettings::default());
        assert_eq!(
            denying.choose(Input::L1, context(1.0, 0)),
            Err(NoMove::EmptyBucket)
        );
    }

    #[test]
    fn promotion_prefers_dropping_the_band_over_changing_the_bucket() {
        // A light attack that suits nothing at 40 m, and a heavy one that does. Promotion should
        // still hand back the LIGHT attack, because `r1` means light.
        let mut engine = dispatcher("4500 3000:0:0:1 3001:1:0:3");
        assert_eq!(
            engine.choose(Input::R1, context(40.0, 0)).unwrap().bucket,
            Bucket::Light
        );
    }

    #[test]
    fn a_pin_overrides_the_whole_mechanism_for_one_button() {
        let mut engine = dispatcher("4500 3000:0:0:1 3001:0:1:1 3002:0:2:1");
        engine.pin(Input::R1, 3002);
        for step in 0..3 {
            assert_eq!(
                engine
                    .choose(Input::R1, context(1.0, step * 100))
                    .unwrap()
                    .fire,
                3002,
                "a pinned button does not cycle"
            );
        }
    }

    #[test]
    fn a_pin_naming_an_animation_the_creature_does_not_have_falls_back_to_cycling() {
        let mut engine = dispatcher("4500 3000:0:0:1");
        engine.pin(Input::R1, 9999);
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3000
        );
    }

    #[test]
    fn an_empty_moveset_reports_that_rather_than_panicking() {
        let mut engine = Dispatcher::new(
            Moveset::default(),
            MappingSettings::default(),
            ButtonSettings::default(),
        );
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)),
            Err(NoMove::NoMoveset)
        );
    }

    #[test]
    fn the_slots_model_ignores_distance_entirely() {
        let mapping = MappingSettings {
            model: MappingModel::Slots,
            ..MappingSettings::default()
        };
        let mut engine = Dispatcher::new(
            moveset("4500 3000:0:0:1 3001:0:1:3"),
            mapping,
            ButtonSettings::default(),
        );
        // Rank 0 is close-reach; at 40 m the context model would have skipped it.
        assert_eq!(
            engine.choose(Input::R1, context(40.0, 0)).unwrap().fire,
            3000
        );
    }

    #[test]
    fn the_layered_model_gives_each_band_its_own_slice_of_the_rank_order() {
        let mapping = MappingSettings {
            model: MappingModel::Layered,
            ..MappingSettings::default()
        };
        let line = "4500 3000:0:0:1 3001:0:1:1 3002:0:2:1 3003:0:3:1 3004:0:4:1 3005:0:5:1";
        let mut engine = Dispatcher::new(moveset(line), mapping, ButtonSettings::default());
        assert_eq!(
            engine.choose(Input::R1, context(1.0, 0)).unwrap().fire,
            3000
        );
        engine.on_neutral();
        assert_eq!(
            engine.choose(Input::R1, context(8.0, 0)).unwrap().fire,
            3002
        );
        engine.on_neutral();
        assert_eq!(
            engine.choose(Input::R1, context(40.0, 0)).unwrap().fire,
            3004
        );
    }

    #[test]
    fn every_creature_in_the_shipped_table_answers_every_button_in_every_band() {
        // The dispatcher must never leave a real creature with a dead button under the shipped
        // defaults. This is the property `unbound_inputs = "promote"` exists for, checked against
        // the whole table rather than one hand-picked boss.
        let mut checked = 0;
        for chr in crate::moveset::table::chr_ids() {
            let Some(moveset) = crate::moveset::table::lookup(chr) else {
                continue;
            };
            if moveset.is_empty() {
                continue;
            }
            let mut engine = Dispatcher::new(
                moveset,
                MappingSettings::default(),
                ButtonSettings::default(),
            );
            for input in Input::ALL {
                for distance in [1.0, 8.0, 40.0] {
                    engine.on_neutral();
                    assert!(
                        engine.choose(input, context(distance, 0)).is_ok(),
                        "c{chr} {} is dead at {distance} m",
                        input.name()
                    );
                }
            }
            checked += 1;
        }
        assert!(checked > 200, "only {checked} creatures had a moveset");
    }

    // ---------------------------------------------------------------------------------------
    // Cancel discipline. Five cases, and between them they are the whole feature.
    // ---------------------------------------------------------------------------------------

    /// c4500's a3000 as the table ships it: the window opens at 9.47 s.
    const CHAINING: &str = "4500 3000w947:0:0:1 3001w853:0:1:1 3002w610:0:2:1";

    /// What the creature's TimeAct would say mid-swing, with the engine declining to answer.
    const fn mid(elapsed_s: f32) -> Option<Playing> {
        Some(Playing {
            animation: 3000,
            elapsed_s: Some(elapsed_s),
            cancel_allowed: None,
        })
    }

    #[test]
    fn a_press_while_the_window_is_open_chains_immediately() {
        let mut engine = dispatcher(CHAINING);
        let open = engine.availability(mid(9.5));
        assert_eq!(open, Availability::Chainable);
        assert_eq!(
            engine.press(Input::R1, context(1.0, 0), open),
            Press::Fire(*engine.moveset().find(3000).expect("rank 0"))
        );
    }

    #[test]
    fn a_press_while_the_creature_is_still_committed_waits_instead_of_firing() {
        let mut engine = dispatcher(CHAINING);
        let shut = engine.availability(mid(1.0));
        assert_eq!(shut, Availability::Committed);
        assert_eq!(
            engine.press(Input::R1, context(1.0, 0), shut),
            Press::Waiting(Held::Queued)
        );
        assert!(engine.is_holding());
        // ...and NOTHING was fired, which is the whole point. The cursor must not have moved
        // either: a press that did not happen cannot have advanced the chain.
        assert_eq!(engine.release(context(1.0, 10), shut), Released::Nothing);
    }

    #[test]
    fn the_waiting_press_fires_when_the_animation_ends() {
        let mut engine = dispatcher(CHAINING);
        engine.press(Input::R1, context(1.0, 0), Availability::Committed);
        // The creature drops back to a neutral animation; the watchdog announces that as a return
        // to neutral on the same frame, which `on_neutral` must not act on while a press waits.
        engine.on_neutral();
        let idle = engine.availability(None);
        assert_eq!(idle, Availability::Idle);
        assert_eq!(
            engine.release(context(1.0, 500), idle),
            Released::Fire(Input::R1, *engine.moveset().find(3000).expect("rank 0"))
        );
    }

    /// A press made during the FIRST attack must give the SECOND one. Getting this wrong is not
    /// subtle from the player's chair: the combo visibly refuses to advance, replaying the same
    /// swing forever.
    #[test]
    fn a_press_buffered_through_one_attack_continues_the_chain_rather_than_restarting_it() {
        let mut engine = dispatcher(CHAINING);
        let first = engine.press(Input::R1, context(1.0, 0), Availability::Idle);
        assert_eq!(first, Press::Fire(*engine.moveset().find(3000).unwrap()));
        // Mid-swing press: buffered.
        engine.press(Input::R1, context(1.0, 200), Availability::Committed);
        // The watchdog sees the creature come back to neutral and says so...
        engine.on_neutral();
        // ...and the buffered press must still be rank 1, not rank 0.
        assert_eq!(
            engine.release(context(1.0, 900), Availability::Idle),
            Released::Fire(Input::R1, *engine.moveset().find(3001).unwrap()),
        );
        // Now that nothing is waiting, a return to neutral DOES reset the chain.
        engine.on_neutral();
        assert_eq!(
            engine.press(Input::R1, context(1.0, 3000), Availability::Idle),
            Press::Fire(*engine.moveset().find(3000).unwrap()),
        );
    }

    #[test]
    fn a_waiting_press_that_outlives_its_window_is_dropped_rather_than_arriving_late() {
        let mut engine = dispatcher(CHAINING);
        engine.press(Input::R1, context(1.0, 0), Availability::Committed);
        let window = u64::from(MappingSettings::default().input_buffer_ms);
        assert_eq!(
            engine.release(context(1.0, window), Availability::Committed),
            Released::Nothing,
            "the window is inclusive"
        );
        assert_eq!(
            engine.release(context(1.0, window + 1), Availability::Committed),
            Released::Expired(Input::R1)
        );
        assert!(!engine.is_holding());
    }

    /// The regression this whole layer exists for. No sequence of presses at any point inside a
    /// committed animation may produce a fire, whatever the button and however many of them there
    /// are -- because a fire is a write to `requestAnimationId`, and that write is the cancel.
    #[test]
    fn no_press_during_a_committed_animation_can_ever_fire() {
        let mut engine = dispatcher(CHAINING);
        for step in 0..40u64 {
            let elapsed = 0.05 * step as f32;
            let availability = engine.availability(mid(elapsed));
            assert_eq!(
                availability,
                Availability::Committed,
                "a3000's window opens at 9.47 s; {elapsed} s must still be committed"
            );
            for input in Input::ALL {
                assert!(
                    matches!(
                        engine.press(input, context(1.0, step * 16), availability),
                        Press::Waiting(_)
                    ),
                    "{} at {elapsed} s fired instead of waiting",
                    input.name()
                );
            }
            assert_eq!(
                engine.release(context(1.0, step * 16), availability),
                Released::Nothing
            );
        }
    }

    /// A move the generator found no window on is committed for its whole length -- so it is
    /// waited out rather than guessed at, and a press during it still is not lost.
    #[test]
    fn a_move_with_no_measured_window_is_waited_out_and_the_press_survives_it() {
        let mut engine = dispatcher("4500 3034:0:0:0 3035:0:1:0");
        let playing = Some(Playing {
            animation: 3034,
            elapsed_s: Some(120.0),
            cancel_allowed: None,
        });
        assert_eq!(engine.availability(playing), Availability::Committed);
        assert_eq!(
            engine.press(Input::R1, context(1.0, 0), Availability::Committed),
            Press::Waiting(Held::Queued)
        );
        assert_eq!(
            engine.release(context(1.0, 100), Availability::Idle),
            Released::Fire(Input::R1, *engine.moveset().find(3034).unwrap())
        );
    }
}
