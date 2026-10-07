//! The NPC duel: from the finger being used to the phantom being gone.
//!
//! ```text
//! Idle --FingerUsed--> Picking --Picked--> Spawning --Created--> Hidden --Ready--> Offered
//!                         |                    |                    |                 |
//!                     Cancelled            SpawnFailed         Broken/timeout      Joined
//!                         v                    v                    v                 v
//!                       Idle                 Idle          Remove -> Idle         Joined --Gone--> Idle
//! ```
//!
//! The rule the whole machine exists to keep (design doc section 2.2): nothing is spawned while
//! the picker is open, and the NPC is never visible before the sign is touched. The DLL disables
//! the character on the same game-thread call that created it, then feeds [`Event::Observed`]
//! every frame; [`verdict`] refuses to offer a sign for a character that is drawn, enabled, or not
//! loaded in time, and the machine removes it instead.
//!
//! The machine is pure: it takes events, returns [`Action`]s, and holds the character only as an
//! opaque handle. The DLL performs the actions.

use crate::config::Body;

/// `ChrSetEntry` (`ChrIns+0x10`) `+0x8` load status: 4 is "active", the value `IsDrawn` needs
/// and the one a Great Jar knight holds while it waits (measured 2026-10-06). 0..=3 are the load
/// in progress, 5 is unloading (bd `chrins-10-state8-draw-load-1171-2026-10-06`).
pub const LOAD_STATUS_ACTIVE: u8 = 4;
const LOAD_STATUS_UNLOADING: u8 = 5;

/// How long a hidden character may take to load before it is given up on. Yura went from 0 to 4
/// well inside a second on 2026-10-06; ten seconds at 60 fps is generous without letting a
/// character whose model never arrives sit in the world forever.
pub const LOAD_DEADLINE_FRAMES: u32 = 600;

/// How long the picker may stay open before the use is treated as abandoned.
pub const PICK_DEADLINE_FRAMES: u32 = 60 * 60;

/// The event entity id `CreateSummonChr` gives every character it builds (a constant store into
/// its spawn request, 1.17.1 `0x1404bb43b`). Every spirit ash, Mimic Tear companion and duel NPC
/// built that way shares it.
pub const SUMMON_ENTITY_ID: u32 = 35_000;

/// The event entity id the duel NPC is registered under instead, so its sign and
/// `GetChrInsByEntityId` find it and nothing else. `ChrSet::SpawnChr` registers the new
/// character in its set's entity map under the request's id, and that map is what the lookup
/// searches; the map is ordered and the lookup takes the first match, which is how a live Mimic
/// companion was found in the duel NPC's place on 2026-10-06 (bd `er-effects-rs-gqu9`). It stays
/// in the 30000..39999 band 35000 is in, which `GetChrInsByEntityId` treats as an id no map
/// owns. The DLL checks that nothing is registered under it before a spawn.
pub const DUEL_ENTITY_ID: u32 = 35_001;

/// An opaque handle for the spawned character (the DLL uses `ChrIns+0x8`).
pub type ChrHandle = u64;

/// What the DLL read off the hidden character this frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct HiddenObservation {
    /// `ChrSetEntry+0xa` bit 0.
    pub disabled: bool,
    /// `ChrSetEntry+0x8`.
    pub load_status: u8,
    /// `IsDrawn` (`0x1403f3930`).
    pub drawn: bool,
}

/// Whether a hidden character can be offered yet.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// Hidden and loaded: place the sign.
    Ready,
    /// Hidden, model still loading.
    Loading,
    /// It must not be offered; the reason is for the log.
    Broken(&'static str),
}

/// Judge one observation. `frames` is how long the character has existed.
#[must_use]
pub fn verdict(seen: HiddenObservation, frames: u32) -> Verdict {
    if seen.drawn {
        return Verdict::Broken("the hidden character is being drawn");
    }
    if !seen.disabled {
        return Verdict::Broken("the hidden character is not disabled");
    }
    if seen.load_status == LOAD_STATUS_UNLOADING {
        return Verdict::Broken("the hidden character is unloading");
    }
    if seen.load_status == LOAD_STATUS_ACTIVE {
        return Verdict::Ready;
    }
    if frames >= LOAD_DEADLINE_FRAMES {
        return Verdict::Broken("the hidden character's model did not load in time");
    }
    Verdict::Loading
}

/// Where the duel is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum State {
    Idle,
    /// The picker is open. Nothing has been spawned.
    Picking {
        frames: u32,
    },
    /// A spawn was requested for this roster row; waiting for the character.
    Spawning {
        pick: usize,
    },
    /// The character exists and is disabled; waiting for its model.
    Hidden {
        chr: ChrHandle,
        frames: u32,
    },
    /// The sign is down.
    Offered {
        chr: ChrHandle,
    },
    /// The phantom joined the player's world.
    Joined {
        chr: ChrHandle,
    },
}

/// What happened.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    /// The player used the Duelist's Furled Finger.
    FingerUsed,
    /// The player chose roster row `usize`.
    Picked(usize),
    /// The player closed the picker.
    Cancelled,
    /// One frame passed (drives the deadlines).
    Frame,
    /// The spawn produced this character.
    Created(ChrHandle),
    /// The spawn failed; the reason is for the log.
    SpawnFailed(String),
    /// The hidden character as read this frame.
    Observed(HiddenObservation),
    /// The sign keyed to the character was touched and the character is in the party.
    Joined,
    /// The character is dead, dismissed or otherwise gone.
    Gone,
}

/// What the DLL must do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    OpenPicker,
    ClosePicker,
    /// Create this body, then disable it in the same call before any frame draws it.
    Spawn(Body),
    /// Place the red sign keyed to the hidden character at the player's feet.
    PlaceSign(ChrHandle),
    /// Remove the character through the game's own unsummon path, never `RemoveChrIns`.
    Remove {
        chr: ChrHandle,
        why: String,
    },
    /// One line for the log.
    Log(String),
}

/// The duel, as data.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Duel {
    pub state: State,
    /// Roster size, so a pick can be bounds-checked here rather than at the call site.
    roster_len: usize,
}

impl Duel {
    #[must_use]
    pub fn new(roster_len: usize) -> Self {
        Self {
            state: State::Idle,
            roster_len,
        }
    }

    /// Advance on one event. `bodies` is the roster's bodies, indexed like the picker rows.
    pub fn step(&mut self, event: Event, bodies: &[Body]) -> Vec<Action> {
        let (next, actions) = transition(&self.state, event, self.roster_len, bodies);
        self.state = next;
        actions
    }
}

fn log(text: impl Into<String>) -> Action {
    Action::Log(text.into())
}

fn transition(
    state: &State,
    event: Event,
    roster_len: usize,
    bodies: &[Body],
) -> (State, Vec<Action>) {
    use Event as E;
    use State as S;
    match (state, event) {
        (S::Idle, E::FingerUsed) => (S::Picking { frames: 0 }, vec![Action::OpenPicker]),
        (S::Picking { .. }, E::Picked(pick)) if pick < roster_len && pick < bodies.len() => (
            S::Spawning { pick },
            vec![Action::ClosePicker, Action::Spawn(bodies[pick])],
        ),
        (S::Picking { .. }, E::Picked(pick)) => (
            S::Idle,
            vec![
                Action::ClosePicker,
                log(format!(
                    "picked row {pick}, but the roster has {roster_len}"
                )),
            ],
        ),
        (S::Picking { .. }, E::Cancelled) => (S::Idle, vec![Action::ClosePicker]),
        (S::Picking { frames }, E::Frame) if frames + 1 >= PICK_DEADLINE_FRAMES => (
            S::Idle,
            vec![Action::ClosePicker, log("the picker was left open; closed")],
        ),
        (S::Picking { frames }, E::Frame) => (S::Picking { frames: frames + 1 }, vec![]),
        (S::Spawning { .. }, E::Created(chr)) => (S::Hidden { chr, frames: 0 }, vec![]),
        (S::Spawning { pick }, E::SpawnFailed(why)) => (
            S::Idle,
            vec![log(format!("spawning roster row {pick} failed: {why}"))],
        ),
        (S::Hidden { chr, frames }, E::Observed(seen)) => match verdict(seen, *frames) {
            Verdict::Ready => (S::Offered { chr: *chr }, vec![Action::PlaceSign(*chr)]),
            Verdict::Loading => (
                S::Hidden {
                    chr: *chr,
                    frames: frames + 1,
                },
                vec![],
            ),
            Verdict::Broken(why) => (
                S::Idle,
                vec![Action::Remove {
                    chr: *chr,
                    why: why.to_owned(),
                }],
            ),
        },
        (S::Hidden { chr, .. } | S::Offered { chr }, E::Gone) => (
            S::Idle,
            vec![log(format!(
                "the duel character 0x{chr:x} vanished before it joined"
            ))],
        ),
        (S::Offered { chr }, E::Joined) => (S::Joined { chr: *chr }, vec![]),
        (S::Joined { .. }, E::Gone) => (S::Idle, vec![log("the duel is over")]),
        // A second finger use while a duel is set up or running is refused, not queued: one NPC
        // phantom holds a party slot and every sign verdict then fails (measured 2026-10-06).
        (state, E::FingerUsed) => (
            state.clone(),
            vec![log("a duel is already set up; the finger use was ignored")],
        ),
        (state, _) => (state.clone(), vec![]),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_duel_entity_is_its_own_and_in_the_global_band() {
        assert_ne!(DUEL_ENTITY_ID, SUMMON_ENTITY_ID);
        // `GetChrInsByEntityId` special-cases 10000..=20000, 30000 and 40000; the band 30000..39999
        // with no block is the one `IsEventEntityIdInBetween30kand39999AndAlsoGlobal` names.
        assert!((30_001..39_999).contains(&DUEL_ENTITY_ID));
    }

    const YURA: Body = Body {
        npc_param: 523_180_079,
        think: 523_180_000,
        chara_init: 23_180,
    };

    fn hidden(load_status: u8) -> HiddenObservation {
        HiddenObservation {
            disabled: true,
            load_status,
            drawn: false,
        }
    }

    #[test]
    fn nothing_is_spawned_until_a_pick() {
        let mut duel = Duel::new(1);
        assert_eq!(
            duel.step(Event::FingerUsed, &[YURA]),
            vec![Action::OpenPicker]
        );
        for _ in 0..10 {
            assert!(duel.step(Event::Frame, &[YURA]).is_empty());
        }
        assert_eq!(
            duel.step(Event::Picked(0), &[YURA]),
            vec![Action::ClosePicker, Action::Spawn(YURA)]
        );
    }

    #[test]
    fn the_full_happy_path() {
        let mut duel = Duel::new(1);
        duel.step(Event::FingerUsed, &[YURA]);
        duel.step(Event::Picked(0), &[YURA]);
        duel.step(Event::Created(0x1710_0014), &[YURA]);
        assert!(duel.step(Event::Observed(hidden(0)), &[YURA]).is_empty());
        assert_eq!(
            duel.step(Event::Observed(hidden(4)), &[YURA]),
            vec![Action::PlaceSign(0x1710_0014)]
        );
        duel.step(Event::Joined, &[YURA]);
        assert_eq!(duel.state, State::Joined { chr: 0x1710_0014 });
        duel.step(Event::Gone, &[YURA]);
        assert_eq!(duel.state, State::Idle);
    }

    #[test]
    fn a_visible_or_enabled_character_is_never_offered() {
        let drawn = HiddenObservation {
            drawn: true,
            ..hidden(4)
        };
        let enabled = HiddenObservation {
            disabled: false,
            ..hidden(4)
        };
        assert!(matches!(verdict(drawn, 0), Verdict::Broken(_)));
        assert!(matches!(verdict(enabled, 0), Verdict::Broken(_)));
        assert!(matches!(verdict(hidden(5), 0), Verdict::Broken(_)));
    }

    #[test]
    fn a_model_that_never_loads_is_removed() {
        let mut duel = Duel::new(1);
        duel.state = State::Hidden {
            chr: 7,
            frames: LOAD_DEADLINE_FRAMES,
        };
        let actions = duel.step(Event::Observed(hidden(3)), &[YURA]);
        assert!(matches!(
            actions.as_slice(),
            [Action::Remove { chr: 7, .. }]
        ));
        assert_eq!(duel.state, State::Idle);
    }

    #[test]
    fn an_out_of_range_pick_spawns_nothing() {
        let mut duel = Duel::new(1);
        duel.step(Event::FingerUsed, &[YURA]);
        let actions = duel.step(Event::Picked(3), &[YURA]);
        assert!(!actions.iter().any(|a| matches!(a, Action::Spawn(_))));
        assert_eq!(duel.state, State::Idle);
    }

    #[test]
    fn a_second_finger_use_is_ignored_while_a_duel_runs() {
        let mut duel = Duel::new(1);
        duel.state = State::Offered { chr: 1 };
        let actions = duel.step(Event::FingerUsed, &[YURA]);
        assert!(matches!(actions.as_slice(), [Action::Log(_)]));
        assert_eq!(duel.state, State::Offered { chr: 1 });
    }

    #[test]
    fn an_abandoned_picker_closes() {
        let mut duel = Duel::new(1);
        duel.state = State::Picking {
            frames: PICK_DEADLINE_FRAMES - 1,
        };
        let actions = duel.step(Event::Frame, &[YURA]);
        assert_eq!(actions[0], Action::ClosePicker);
        assert_eq!(duel.state, State::Idle);
    }
}
