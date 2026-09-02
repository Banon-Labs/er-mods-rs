//! The engine itself: what possession start, each frame, and release actually do.
//!
//! Split out of `possess/mod.rs` so the module tree stays honest about what is host-testable.
//! Everything in here reads or writes live game memory through the windows-only `eldenring`
//! bindings; the offset table, the thunk emitter, the teardown ordering and the movement math are
//! siblings of this file precisely so they can be proved by `cargo test` without a game.
//!
//! See the module docs on [`crate::possess`] for what the whole thing does and what it leaves out.

use er_game_base::game_build::{describe_build, game_file_version};

use crate::camera;
use crate::engine::{PossessionEngine, PossessionOutcome, PossessionRequest};
use crate::hud;
use crate::input::FaceEdges;
use crate::log::possess_log;
use crate::moveset::chain::{Availability, Held, Playing};
use crate::moveset::dispatch::{Context, Dispatcher, Input, Locomotion, NoMove, Press, Released};
use crate::moveset::table::{self, Denial};
use crate::moveset::watchdog::{Sample, Verdict, Watchdog};
use crate::moveset::{derived, table as moveset_table};
use crate::possess::game::{self, Chr};
use crate::possess::teardown::{Reason, Step, Teardown};
use crate::possess::thunk::Thunk;
use crate::possess::{intent, layout};
use crate::settings::TargetMode;
use crate::spawn::game as spawn_game;
use crate::spawn::placement;
use crate::spawn::readiness::{Gate, Poll, Readiness};
use crate::spawn::request::SpawnSpec;

/// Fully opaque, for the player's own body while somebody else is being worn.
const ALPHA_INVISIBLE: f32 = 0.0;
/// ...and back.
const ALPHA_OPAQUE: f32 = 1.0;

/// The animation the watchdog fires to get a stuck creature back to neutral.
///
/// `W_Event0000` -> `a000_000000`, the base idle. Every creature has it: it is the bottom of the
/// 0-999 idle band that the animation-id banding convention reserves, and the graph's `%04d`
/// formatting spells zero as four digits rather than as a special case.
const IDLE_ANIMATION: i32 = 0;

/// A creature THIS MOD CREATED, carried by whatever is wearing it so the teardown knows there is
/// something to give back.
///
/// `None` on a `Possessing` means the map placed the character and removing it is not ours to do.
#[derive(Clone, Copy)]
struct SpawnedBody {
    spawned: spawn_game::Spawned,
    /// `[spawn].despawn_on_release`. `false` leaves the creature standing when the possession
    /// ends, which is a real choice and not a failure -- the log says which it was.
    despawn_on_release: bool,
}

/// A spawn that has happened and is not yet drivable.
///
/// A state of its own rather than a flag on [`Possessing`], because almost nothing that is true of
/// a possession is true here: there is no thunk, no camera override, no neutered body, and the
/// creature cannot be driven or even read through for most of it. The one thing it shares is that
/// the hotkey has been pressed and the player is waiting.
struct Pending {
    spawned: spawn_game::Spawned,
    player: Chr,
    /// The request that started it, replayed into [`NpcPossessionEngine::enter`] once ready.
    request: PossessionRequest,
    readiness: Readiness,
    /// `layout::ene_dat_cap_offsets` for the running build, resolved once at spawn rather than per
    /// frame. `None` makes the asset gate undecidable, which the readiness machine skips.
    caps: Option<(usize, usize)>,
    /// Where the creature is put the instant it becomes placeable, at the CONFIGURED distance.
    /// Captured at spawn, from the player's position THEN -- so a player who walks away while it
    /// loads does not drag the spawn point with them.
    ///
    /// The fallback rather than the answer: [`Self::place_from`] and
    /// [`Self::configured_distance_m`] are what the real placement is recomputed from once the
    /// creature's own capsule can be read. This point is used when it cannot.
    place_at: [f32; 3],
    /// The player's position at the press, so the placement can be re-derived at a different
    /// distance without moving the spawn point to wherever the player has since walked.
    place_from: [f32; 3],
    /// `[spawn].distance_m`, kept because it is now a FLOOR rather than the whole answer. See
    /// [`crate::spawn::placement`].
    configured_distance_m: f32,
    place_yaw: f32,
    started: std::time::Instant,
    /// The last gate a line was written about, so progress is logged on CHANGE rather than sixty
    /// times a second.
    reported: Option<Gate>,
    despawn_on_release: bool,
    chr_id: u32,
}

/// One possession in flight.
struct Possessing {
    creature: Chr,
    player: Chr,
    /// Set only when this mod created the creature; see [`SpawnedBody`].
    spawned: Option<SpawnedBody>,
    /// Kept alive for exactly as long as `ChrCtrl+0x3b0` points at it. Dropping it frees the page,
    /// so it must outlive the clear -- which is why teardown owns this whole struct and drops it
    /// only after [`Step::ClearManipulatorOverride`] has run.
    thunk: Thunk,
    /// `ChrIns.debugFlags` for the running build, or `None` on a build nobody has measured -- in
    /// which case the no-attack neuter is skipped rather than written eight bytes off target.
    debug_flags_offset: Option<usize>,
    release_on_death: bool,
    /// THE CREATURE'S POSITION, CACHED EVERY FRAME.
    ///
    /// Death must never need a late read. `WorldChrManImp::RemoveChrIns` nulls the camera override
    /// DURING removal, so reading the corpse's position at that point is a use-after-free race.
    /// This is read while the creature is definitely alive and used afterwards.
    last_position: [f32; 3],
    /// Likewise, and the fallback when the creature dies airborne.
    last_grounded: Option<[f32; 3]>,
    /// Was the creature on solid ground at the last good read?
    last_on_ground: bool,
    frames: u64,
    /// THE MOVESET, or `None` when this creature is not in the shipped table.
    ///
    /// `None` is a real and ordinary state -- 163 of the 408 creatures the generator looked at
    /// have nothing fireable of their own -- so it is an `Option` rather than an empty dispatcher
    /// that would silently swallow every press.
    moveset: Option<Dispatcher>,
    /// Rising-edge detector for the four face inputs.
    face: FaceEdges,
    /// THE CAMERA, sized to this creature. Present even when nothing was adapted, because it also
    /// carries the reason -- see [`crate::camera`].
    camera: camera::Session,
    watchdog: Watchdog,
    /// Milliseconds since the possession started. The clock the combo window and the watchdog
    /// both measure against.
    elapsed_ms: u64,
    started: std::time::Instant,
    /// `NpcParam` id / 10000 -- `4500` for a Flying Dragon. The table's key, and what the derived
    /// file is written under. Captured once at possession start, because the creature could
    /// despawn before it is next needed.
    chr_id: u32,
    /// Whether a "this button has nothing to fire" line has already been logged, per input. The
    /// log opens and closes the file per line, so an unguarded one on a held button would cost
    /// sixty opens a second.
    reported_dead_input: [bool; 4],
    /// Did a request actually land on the previous frame? Read one frame late because the
    /// watchdog runs before the firing does, and consumed on read.
    fired_last_frame: bool,
    /// Has the "your press is waiting rather than cancelling" line been said this possession?
    ///
    /// Once, not per press, for the same reason as [`Self::reported_dead_input`]: the log opens
    /// and closes the file per line, and this is the state a player mashing through a long
    /// animation is in on every one of sixty frames a second.
    reported_buffered_press: bool,
}

impl Possessing {
    /// Where the player should be standing once this is over.
    ///
    /// No sphere cast: the creature's own `standingOnSolidGround` answers whether its last
    /// position already qualifies, and `lastGroundedPosition` is a point it demonstrably stood on
    /// when it does not. A cast would be better under a flying dragon and is the one thing here
    /// that needs a game address, so it is left out of this layer deliberately.
    fn release_point(&self) -> [f32; 3] {
        if self.last_on_ground {
            return self.last_position;
        }
        self.last_grounded.unwrap_or(self.last_position)
    }
}

/// The real engine. One possession at a time, by construction.
#[derive(Default)]
pub(crate) struct NpcPossessionEngine {
    active: Option<Possessing>,
    /// A spawn waiting to become drivable. Mutually exclusive with [`Self::active`]: the pending
    /// one becomes the active one, and nothing else can start while either is set.
    pending: Option<Pending>,
}

impl NpcPossessionEngine {
    pub(crate) const fn new() -> Self {
        Self {
            active: None,
            pending: None,
        }
    }

    /// Everything that has to be true of the player's body while somebody else is being worn.
    ///
    /// Re-applied every frame rather than set once, and each of the four has its own reason:
    /// the alpha has a per-frame decay modifier the engine drives, the mute is cleared by four
    /// (re)spawn/teleport paths, `debugFlags` is cheap enough that checking whether it drifted
    /// costs more than writing it, and `lastGroundedPosition` has to track the body every frame or
    /// it is not tracking it at all.
    ///
    /// **THE INVINCIBILITY BIT IS NOT THE WHOLE OF BODY SAFETY, AND THAT IS THE CORRECTION THIS
    /// COMMENT CARRIES.** `layout::chr_ins::INVINCIBLE` was re-verified on 1.17 rather than
    /// assumed -- `ChrIns::IsImmuneToAttack` at `0x1403f3dc0` is `TESTB $0x10,0x1c5(%r9)`, the same
    /// instruction 1.16.2 has at `0x1403f3c76`, and `+0x1c5` sits below the `+0x3b8` insertion that
    /// grew `ChrIns` on this build, so it could not have moved. What the bit gates is HIT
    /// RESOLUTION, and nothing else. `CSChrFallModule`'s landing handler charges for a fall of
    /// `lastGroundedPosition.y - position.y` and dispatches a fall-death call above a threshold
    /// WITHOUT ever asking whether the victim is immune -- and this engine teleports the player's
    /// body to the creature's root every frame, through whatever arc a leaping creature takes.
    /// [`game::Chr::pin_last_grounded`] carries the byte proof; it is here because a neuter that
    /// only covers the hit path is a neuter that lets a Fallingstar Beast's leap kill the body it
    /// is carrying.
    ///
    /// **THE INVINCIBILITY IS ALSO WHY THE POSSESSED CREATURE'S GRABS NEVER LAND, and that is
    /// kept deliberately.** `layout::chr_ins::INVINCIBLE` carries the trace: every route into the
    /// throw system is behind `IsImmuneToAttack`, which reads exactly this bit, and the only
    /// legal victim for 189 of the 190 creature `ThrowParam` rows is the player's own body. The
    /// two shapes the fix could take were both checked and both refused. Dropping the bit for a
    /// grab window makes the body damageable by EVERYTHING for that window and lets the creature
    /// the player is wearing throw, hurt and kill them -- a state this crate's teardown does not
    /// model, since `release_on_death` watches the CREATURE. Keeping it unhittable by everything
    /// except the possessed creature is not expressible: the predicate has no per-attacker
    /// exemption, and the game's one throw entry that skips it (`RequestThrow_AllChr`, driven
    /// from `ChrCtrl` for a non-DEFAULT manipulator) builds its `ThrowData` with `throwTypeId`
    /// zero, so it can only ever match a backstab/riposte row and never a creature grab.
    ///
    /// The co-location is an independent second refusal even if the bit were dropped:
    /// [`Self::tick_active`]'s `request_move` keeps the body at the creature's own root, so the
    /// attacker-to-defender vector `ThrowPoseChecks` builds is ~zero, `NormalizeVector` answers
    /// the zero vector, and the `DiffAngMyToDef` cone test compares a dot product of zero against
    /// `cos(angle)` -- failing for every row under 90 degrees.
    fn neuter(state: &Possessing, first_frame: bool) {
        state.player.set_invincible(true);
        state.player.set_alpha(ALPHA_INVISIBLE);
        // `stop_playing` is a one-shot the engine consumes, so it is asked for only once.
        state.player.set_muted(true, first_frame);
        if let Some(offset) = state.debug_flags_offset {
            state.player.set_no_attack(offset, true);
        }
        // The co-location target for THIS frame, which is where the body is about to be put. See
        // the doc above for why the fall path needs this and the invincibility bit cannot supply
        // it.
        state.player.pin_last_grounded(state.last_position);
    }

    /// Undo [`Self::neuter`]. Returns whether every part of it took.
    fn restore(state: &Possessing) -> bool {
        let invincible = state.player.set_invincible(false);
        let alpha = state.player.set_alpha(ALPHA_OPAQUE);
        let muted = state.player.set_muted(false, false);
        let flags = state
            .debug_flags_offset
            .is_none_or(|offset| state.player.set_no_attack(offset, false));
        invincible && alpha && muted && flags
    }

    /// Cancel a spawn that never became drivable, or that the player pressed the key out of.
    ///
    /// A separate path from [`Self::release_with`] on purpose: none of the six possession steps
    /// applies -- nothing was neutered, no camera was moved, no override was installed -- and
    /// running them would report six successes for work that was never done. The only thing owed is
    /// the creature.
    fn cancel_pending(pending: Pending, reason: Reason, despawn: bool) -> bool {
        let removed = if despawn && pending.despawn_on_release {
            spawn_game::despawn(pending.spawned.creature)
        } else {
            false
        };
        possess_log(format_args!(
            "spawn: cancelled c{:04} in roster slot {} after {} ms (reason={}) -- creature {}",
            pending.chr_id,
            pending.spawned.slot,
            pending.started.elapsed().as_millis(),
            reason.name(),
            if removed {
                "removed"
            } else if !despawn {
                "LEFT IN THE WORLD; RemoveChrIns is a game call and this is not the game thread"
            } else if pending.despawn_on_release {
                "COULD NOT BE REMOVED and is still in the world"
            } else {
                "left standing, as [spawn].despawn_on_release asks"
            }
        ));
        removed
    }

    /// Run the release, in order, whatever fails. See [`teardown`].
    fn release_with(&mut self, reason: Reason) -> PossessionOutcome {
        // A press that lands while a spawn is still coming up cancels it. Handled before the
        // active state because the two are mutually exclusive and this one has its own teardown.
        if let Some(pending) = self.pending.take() {
            // A HOTKEY PRESS INSIDE THE READINESS WINDOW IS IGNORED, not treated as a cancel.
            // A spawn takes up to `[spawn].readiness_ms` to become drivable and NOTHING ON SCREEN
            // says one is in flight, so a player who taps twice -- or holds the key a beat too
            // long -- used to create a creature and delete it 166 ms later, which is
            // indistinguishable from the key doing nothing at all. Measured live 2026-09-02:
            // `spawn: cancelled c2110 in roster slot 6 after 166 ms (reason=hotkey)`. The
            // deadline is what ends a spawn that will never arrive; a second press is not.
            // Shutdown still cancels, because there is no later frame to arrive in.
            if reason != Reason::Shutdown {
                possess_log(format_args!(
                    "possess-hotkey: IGNORED -- a spawn has been coming up for {} ms. A press \
                     while one is in flight is not a cancel; wait for it, or let \
                     [spawn].readiness_ms end it",
                    pending.started.elapsed().as_millis(),
                ));
                self.pending = Some(pending);
                return PossessionOutcome::Accepted;
            }
            Self::cancel_pending(pending, reason, false);
            return PossessionOutcome::Accepted;
        }
        // `mut` because the camera restore below mutates the saved state as it unwinds it.
        let Some(mut state) = self.active.take() else {
            // Releasing when nothing is possessed must be safe and quiet: the shutdown path calls
            // it unconditionally, and so does the state machine after a refusal.
            return PossessionOutcome::Accepted;
        };
        let mut run = Teardown::new(reason);
        let thunk_address = state.thunk.object_address();
        let release_point = state.release_point();
        run.run(|step| match step {
            // Infallible -- one atomic store -- so this always reports success. It is a step
            // rather than a line at the top of this function because the ORDER is the thing the
            // state machine exists to enforce: every step below reads through the creature.
            Step::StopHudRetarget => {
                hud::stop();
                true
            }
            Step::RestoreBody => Self::restore(&state),
            // Nothing to undo: the per-frame driver stops the moment `active` is `None`, which
            // happened at the `take()` above. The step exists so the ORDER is a thing the state
            // machine enforces rather than a comment.
            Step::StopColocating => true,
            // PINNED BEFORE THE MOVE, not after: this is the last teleport the body takes and it
            // is the one most likely to be DOWNWARD -- `release_point` falls back to the creature's
            // `lastGroundedPosition` when it died airborne, which is by construction below where
            // the body has been riding. Without this the very act of giving the body back reads to
            // `CSChrFallModule` as a fall of exactly that height. See
            // [`game::Chr::pin_last_grounded`].
            Step::MovePlayer => {
                state.player.pin_last_grounded(release_point);
                state.player.request_move(release_point, None)
            }
            Step::RestoreCameraSize => state.camera.restore(),
            Step::ClearCameraOverride => game::set_camera_override(None),
            // The AI move request is cancelled BEFORE the override is cleared, because after it
            // the creature is the AI's again and our last order is a run command it did not
            // issue. `stop_move_intent` writes exactly what `CS::AiIns::ClearMoveRequest` writes,
            // so what the AI wakes up to is a state its own code produces. Its failure is not
            // this step's failure: the override clear is THE step that must happen, and a
            // creature that jogs for one frame is not a reason to report the release broken.
            Step::ClearManipulatorOverride => {
                state.creature.stop_move_intent();
                state.creature.clear_manipulator_override(thunk_address)
            }
            // AFTER the override clear, always; see `Step::DespawnCreature`, whose discriminant is
            // that ordering and whose test fails if anyone moves it.
            Step::DespawnCreature => match state.spawned {
                // The map placed this character. Removing it is not ours to do.
                None => true,
                // The player asked for it to stay. Nothing failed.
                Some(body) if !body.despawn_on_release => true,
                // THE GAME ALREADY REMOVED IT, which is the only thing `CreatureGone` can mean:
                // `Chr::is_live` fails when `ChrCtrl.owner` no longer points back at the `ChrIns`,
                // and that is a destroyed character rather than a transient read. `RemoveChrIns`
                // hands its argument to `CSDelayDeleteMan`, so calling it on a character the game
                // has already queued for destruction queues a freed `ChrIns` for a SECOND one --
                // and `spawn_game::despawn`'s own guard cannot catch it, because it proves the
                // first qword is mapped and freed heap still is.
                //
                // `crate::spawn::readiness::Poll::Vanished` already refuses this on the pending
                // path with the same reasoning; the active path did not, and a release whose whole
                // reason is "the creature is gone" is exactly where it bites. This is the release
                // that ran, and did not finish, in the reference log.
                Some(body) if reason == Reason::CreatureGone => {
                    possess_log(format_args!(
                        "spawn: the creature in roster slot {} was removed by the GAME, so \
                         RemoveChrIns is NOT being called on it -- a second removal would queue an \
                         already-freed ChrIns for a second destruction",
                        body.spawned.slot
                    ));
                    true
                }
                // `DLL_PROCESS_DETACH` RUNS ON A THREAD THAT IS NOT THE GAME THREAD, under the
                // loader lock, with the other threads possibly already gone. `RemoveChrIns` walks
                // four singletons, calls a virtual on the character and DLPanics if any of them is
                // missing -- so it is refused here, and an orphaned NPC is accepted instead.
                //
                // That is a real, deliberate restriction and not an oversight: the creature is an
                // ordinary `EnemyIns` in the buddy roster with its AI already given back by the
                // step above, and the next map load removes it. A crash inside `DllMain` would not
                // be survivable and would be attributed to whatever unloaded us.
                Some(body) => {
                    if reason == Reason::Shutdown {
                        possess_log(format_args!(
                            "spawn: the creature in roster slot {} is being LEFT IN THE WORLD -- \
                             RemoveChrIns is a game call and DLL_PROCESS_DETACH is not the game \
                             thread. It has its own AI back and the next map load clears it",
                            body.spawned.slot
                        ));
                        true
                    } else {
                        spawn_game::despawn(body.spawned.creature)
                    }
                }
            },
            Step::LiftSaveSuppression => true,
        });
        possess_log(format_args!("{}", run.line()));
        if run.has_critical_failure() {
            // The page STAYS MAPPED. `ChrCtrl+0x3b0` still points into it, and freeing memory the
            // game is about to dispatch through turns a DLPanic into an arbitrary jump. Leaking
            // one page is the cheaper of the two.
            core::mem::forget(state.thunk);
            return PossessionOutcome::Refused(
                "the manipulator override could not be cleared".to_owned(),
            );
        }
        // Everything else has happened, so the page can go.
        drop(state);
        PossessionOutcome::Accepted
    }

    /// Look the creature up in the shipped table and apply the player's `[chr.cNNNN]` corrections.
    ///
    /// The order is deliberate: `unusable` is applied BEFORE `usable`, so a player who lists the
    /// same animation in both gets it -- the more specific instruction ("offer this") wins over
    /// the broader one, rather than the answer depending on which line they typed first.
    fn build_moveset(chr_id: u32, request: &PossessionRequest) -> Option<Dispatcher> {
        let mut moveset = moveset_table::lookup(chr_id)?;
        let overrides = crate::config::chr_override(chr_id);
        if let Some(over) = overrides.as_ref() {
            for animation in &over.unusable {
                moveset.deny(*animation, Denial::UnusableAtRuntime);
            }
            for animation in &over.usable {
                moveset.admit(*animation);
            }
        }
        if moveset.is_empty() {
            // Everything this creature had was denied -- by the table, by the player, or both.
            // A dispatcher over nothing would answer every press with `NoMoveset`, which is the
            // same outcome with more moving parts.
            return None;
        }
        let mut dispatcher = Dispatcher::new(moveset, request.mapping, request.buttons);
        for (name, animation) in overrides.iter().flat_map(|over| over.pin.iter()) {
            // Case-insensitive: the config file spells these lowercase and so does `Input::name`,
            // but `R2 = 3046` is the obvious typo and rejecting it teaches nothing.
            match Input::ALL
                .iter()
                .find(|input| name.eq_ignore_ascii_case(input.name()))
            {
                Some(input) => dispatcher.pin(*input, *animation),
                None => possess_log(format_args!(
                    "moveset: [chr.c{chr_id:04}] pin names input \"{name}\", which is not one of \
                     r1/r2/l1/l2 -- ignored"
                )),
            }
        }
        Some(dispatcher)
    }

    /// One frame of the moveset: watch what is playing, then act on what was pressed.
    ///
    /// ORDER MATTERS AND IS NOT ARBITRARY. The watchdog runs FIRST, so a press arriving on the
    /// same frame a stuck animation is detected does not queue behind it -- the forced idle wins
    /// and the press is spent on a frame that could not have fired anyway. Firing first would
    /// leave the new request overwritten by the idle a line later, which reads to the player as a
    /// button that sometimes does nothing.
    ///
    /// `moving` is whether this frame actually ASKED the creature to move -- the gait written into
    /// `AiIns.walkType`, not whether a stick was touched. The two differ on exactly the frames
    /// where the request was refused (an unreadable heading, a junk `speed_scale`), and on those
    /// the body is standing still, so offering it a running attack would be wrong.
    fn tick_moveset(state: &mut Possessing, moving: bool) {
        let Some(dispatcher) = state.moveset.as_mut() else {
            return;
        };
        let pressed = state.face.feed(crate::input::read_face_inputs());
        // CONSUMED, not held. See [`Sample::input_consumed`]: a press that fired nothing is
        // evidence of being stuck, not evidence against it, so a player mashing at a softlock must
        // not be able to hold the watchdog off indefinitely.
        let consumed = core::mem::take(&mut state.fired_last_frame);
        // What the creature is doing, and whether it is willing to be left. Read ONCE per frame
        // and shared by the release and the press, so a buffered press and a fresh one on the
        // same frame cannot disagree about what is playing.
        let playing = state.creature.current_animation().map(|animation| Playing {
            animation,
            elapsed_s: state.creature.current_animation_elapsed(),
            cancel_allowed: state.creature.attack_cancel_allowed(),
        });
        // `mut` because a release that fires spends the frame's one request slot, which makes
        // anything else pressed this frame mid-animation by definition -- see below.
        let mut availability = dispatcher.availability(playing);

        if let Some(current) = playing {
            let sample = Sample {
                animation: current.animation,
                // An unreadable root motion is treated as MOVING, i.e. not stuck. Failing the
                // other way would force idle out of a perfectly good attack whenever the module
                // pointer happened not to read.
                root_motion_squared: state.creature.root_motion_squared().unwrap_or(f32::MAX),
                input_consumed: consumed,
                now_ms: state.elapsed_ms,
            };
            match state.watchdog.observe(sample) {
                Verdict::Fine => {}
                Verdict::ReturnedToNeutral => dispatcher.on_neutral(),
                Verdict::ForceIdle { idle, blame } => {
                    state.creature.request_animation(idle);
                    dispatcher
                        .moveset_mut()
                        .deny(blame, Denial::UnusableAtRuntime);
                    // A press queued behind an animation that has just been declared unusable is
                    // stale: it was aimed at continuing a chain that turned out to be a softlock.
                    // Dropped explicitly, because `on_neutral` deliberately does NOT reset while
                    // something is waiting.
                    dispatcher.forget_buffered_press();
                    dispatcher.on_neutral();
                    // THE NUMBER IS THE POSSESSION CLOCK, NOT THE STUCK DURATION, and the wording
                    // used to say the opposite -- "stuck ... for {elapsed_ms} ms" reported a
                    // 29,701 ms hang on a watchdog whose threshold is four seconds. The stuck
                    // duration lives in `Watchdog::armed.suspect_since_ms` and is not on
                    // `Verdict::ForceIdle`; until it is, this says what it actually knows. How
                    // long it was stuck is `[mapping].watchdog_seconds`, by construction.
                    possess_log(format_args!(
                        "moveset: animation {blame} stopped producing root motion and was forced \
                         back to idle {} ms into this possession, after the full \
                         [mapping].watchdog_seconds -- denied for the rest of this session. It is \
                         in {} as unusable-at-runtime; copy it into er-npc-possess.toml under \
                         [chr.c{:04}] to keep it denied.",
                        state.elapsed_ms,
                        crate::config::DERIVED_CONFIG_FILE_NAME,
                        state.chr_id,
                    ));
                    Self::write_derived(
                        state.chr_id,
                        &state.camera,
                        state.creature,
                        Some(dispatcher),
                    );
                    return;
                }
            }
        }

        // Nothing pressed and nothing waiting: there is no work, and in particular no reason to
        // pay for the distance sweep a `Context` needs.
        if pressed == 0 && !dispatcher.is_holding() {
            return;
        }
        // ONE REQUEST PER FRAME, and only into an empty slot. `requestAnimationId` holds a single
        // id which `CSChrEventModule::Update` consumes once per frame; a second write before that
        // runs discards the first silently. Two buttons in one frame therefore has to mean one
        // attack, not one attack lost.
        if state.creature.animation_request_pending() {
            return;
        }
        let context = Context {
            distance_m: game::nearest_hostile_distance(state.creature),
            locomotion: if moving {
                Locomotion::Moving
            } else {
                Locomotion::Neutral
            },
            now_ms: state.elapsed_ms,
        };

        // The waiting press goes first, and it has to. A press held through an attack is older
        // than anything arriving this frame, and the frame the animation lets go is exactly the
        // frame a fresh press would also be allowed -- so spending the slot on the new one would
        // silently eat the old one and give the player one attack for two presses.
        match dispatcher.release(context, availability) {
            Released::Nothing => {}
            Released::Fire(_, chosen) => {
                Self::fire_chosen(
                    state.creature,
                    &mut state.watchdog,
                    &mut state.fired_last_frame,
                    chosen,
                );
                // The frame's one request slot is now spent on an attack that has just started,
                // so anything ALSO pressed this frame is by definition arriving mid-animation.
                // Saying so rather than returning is what keeps it: it buffers instead of being
                // thrown away, and the chain carries on.
                availability = Availability::Committed;
            }
            Released::Expired(input) => possess_log(format_args!(
                "moveset: the {} press made while the creature was mid-attack waited out \
                 [mapping] input_buffer_ms and was dropped. Raise it if you want presses made \
                 early in a long animation to still arrive.",
                input.name(),
            )),
            Released::Empty(input, reason) => {
                Self::report_dead_input(&mut state.reported_dead_input, state.chr_id, input, reason)
            }
        }

        if pressed == 0 {
            return;
        }
        let Some(input) = Input::ALL
            .iter()
            .copied()
            .find(|input| pressed & (1 << Self::input_bit(*input)) != 0)
        else {
            return;
        };
        match dispatcher.press(input, context, availability) {
            Press::Fire(chosen) => Self::fire_chosen(
                state.creature,
                &mut state.watchdog,
                &mut state.fired_last_frame,
                chosen,
            ),
            Press::Waiting(held) => {
                // Once per possession, not per press. The point is to tell a player who expected
                // a cancel that the press was KEPT rather than eaten; repeating it sixty times a
                // second would bury the log.
                if !state.reported_buffered_press {
                    state.reported_buffered_press = true;
                    possess_log(format_args!(
                        "moveset: {} landed while the creature was still committed to what it is \
                         playing, so it is waiting rather than cancelling it. It fires the moment \
                         the game says a chain is allowed, or when the animation ends -- \
                         whichever comes first. {}",
                        input.name(),
                        match held {
                            Held::Queued => "Nothing else was waiting.",
                            Held::Replaced => "It replaced an earlier press; only one is kept.",
                        },
                    ));
                }
            }
            Press::Nothing(reason) => {
                Self::report_dead_input(&mut state.reported_dead_input, state.chr_id, input, reason)
            }
        }
    }

    /// Fire a chosen move and record that the frame spent its one request on it.
    ///
    /// Takes the three fields it touches rather than `&mut Possessing`, because every caller is
    /// holding a mutable borrow of the sibling `moveset` field while it calls this.
    fn fire_chosen(
        creature: Chr,
        watchdog: &mut Watchdog,
        fired_last_frame: &mut bool,
        chosen: table::Move,
    ) {
        if Self::fire(creature, chosen) {
            watchdog.armed_with(chosen.fire);
            *fired_last_frame = true;
        }
    }

    /// Said once per possession per input rather than per press: a player holding a dead button
    /// would otherwise write the log file at sixty lines a second.
    fn report_dead_input(reported: &mut [bool; 4], chr_id: u32, input: Input, reason: NoMove) {
        if reported[Self::input_bit(input)] {
            return;
        }
        reported[Self::input_bit(input)] = true;
        possess_log(format_args!(
            "moveset: {} has nothing to fire on c{:04} ({}). See {} for what this creature has \
             and why the rest was withheld.",
            input.name(),
            chr_id,
            reason.explanation(),
            crate::config::DERIVED_CONFIG_FILE_NAME,
        ));
    }

    /// Fire one move, by whichever of the two paths its prefix says.
    ///
    /// THE SPLIT IS THE POINT. `Prefix::Event` -- 4,667 of the shipped moves -- is a write to
    /// `CSChrEventModule+0x18` and resolves no game address, so it keeps working on a build nobody
    /// has mapped. Everything else builds the event name the graph actually declares and calls
    /// `PlayAnimationByBehaviorName`, which costs the one address this crate resolves. Preferring
    /// the field write is not an optimisation, it is what confines the address to the minority of
    /// moves that cannot be reached any other way.
    fn fire(creature: Chr, chosen: table::Move) -> bool {
        if chosen.prefix.is_field_write() {
            return creature.request_animation(chosen.fire);
        }
        // `%04d`, exactly: the graph's literals are `W_Step6000` and `W_Event0000`, minimum width
        // four, longer ids printed raw. A three-digit id spelled without the leading zero resolves
        // to nothing and no-ops silently, which is the failure this whole layer exists to avoid.
        let mut name: Vec<u16> = chosen.prefix.name().encode_utf16().collect();
        name.extend(format!("{:04}", chosen.fire).encode_utf16());
        name.push(0);
        creature.play_animation_by_name(&name)
    }

    const fn input_bit(input: Input) -> usize {
        match input {
            Input::R1 => 0,
            Input::R2 => 1,
            Input::L1 => 2,
            Input::L2 => 3,
        }
    }

    /// Write `er-npc-possess.derived.toml`.
    ///
    /// Best-effort by design: the game directory can be read-only, and a possession that works is
    /// worth more than a report about it. A failed write is silent because the only thing lost is
    /// a diagnostic, and a log line about failing to write a log-adjacent file is noise.
    ///
    /// The camera and HUD blocks are appended rather than interleaved, so each layer owns its own
    /// stretch of one file. `camera` is taken as a plain reference rather than reached through
    /// `state` because the moveset call site is holding a mutable borrow of a sibling field.
    fn write_derived(
        chr_id: u32,
        camera: &camera::Session,
        creature: Chr,
        dispatcher: Option<&Dispatcher>,
    ) {
        let mut text = match dispatcher {
            Some(dispatcher) => {
                derived::render(chr_id, dispatcher.moveset(), &dispatcher.summary())
            }
            // WRITTEN ANYWAY for a creature with no shipped moveset, because it still gets a
            // camera and still gets the HUD block -- and skipping the write would leave the
            // PREVIOUS possession's file on disk, describing a different character, which is
            // worse than no report when its whole job is to explain numbers the player is
            // looking at right now. `render` already has a block for a moveset that is empty.
            None => derived::render(
                chr_id,
                &moveset_table::Moveset::default(),
                "no shipped moveset",
            ),
        };
        text.push_str(&camera.derived_block(chr_id));
        text.push_str(&Self::hud_block(chr_id, creature));
        let _ = std::fs::write(crate::config::DERIVED_CONFIG_FILE_NAME, text);
    }

    /// The `[hud]` block: which character the bars are reading, and why, for THIS creature.
    fn hud_block(chr_id: u32, creature: Chr) -> String {
        let decision = match hud::outcome().off_reason() {
            Some(off) => hud::Decision::Off(off),
            None if !crate::config::hud().enabled => hud::Decision::Off(hud::Off::Disabled),
            None => match hud::read_source(creature.address()) {
                Some(source) => hud::Decision::Driving(source),
                None => hud::Decision::Off(hud::Off::Unreadable),
            },
        };
        hud::render_derived(chr_id, &decision)
    }

    /// One frame of an active possession. Called from the FrameBegin task.
    fn tick_active(&mut self) {
        let Some(state) = self.active.as_mut() else {
            return;
        };
        // THE LIVENESS CHECK COMES FIRST. Everything below reads through the creature, and a
        // despawn between frames is ordinary rather than exceptional.
        if !state.creature.is_live() {
            possess_log(format_args!(
                "possession: the possessed character stopped resolving after {} frames",
                state.frames
            ));
            self.release_with(Reason::CreatureGone);
            return;
        }
        if state.release_on_death && state.creature.is_dead() {
            possess_log(format_args!(
                "possession: the possessed character died after {} frames",
                state.frames
            ));
            self.release_with(Reason::CreatureDied);
            return;
        }

        state.frames = state.frames.saturating_add(1);
        let first_frame = state.frames == 1;
        // A real clock rather than frames-times-sixteen: the combo window is specified in
        // milliseconds and the frame rate is not sixty. On a machine holding thirty, a
        // frame-counted window would be twice as long as the file says it is.
        state.elapsed_ms = u64::try_from(state.started.elapsed().as_millis()).unwrap_or(u64::MAX);

        // CACHE BEFORE ANYTHING ELSE. Death and despawn both make this unreadable, and the release
        // point has to come from a read taken while the creature was definitely alive.
        if let Some(position) = state.creature.position() {
            state.last_position = position;
        }
        if let Some(grounded) = state.creature.last_grounded_position() {
            state.last_grounded = Some(grounded);
        }
        if let Some(on_ground) = state.creature.standing_on_solid_ground() {
            state.last_on_ground = on_ground;
        }

        Self::neuter(state, first_frame);

        // Something else can null the camera override -- `RemoveChrIns` does it on despawn, which
        // the liveness check above has already ruled out for this frame. Re-asserting is one
        // compare in the common case and the difference between a working camera and a silently
        // detached one in every other.
        if !game::camera_override_is(state.creature) {
            game::set_camera_override(Some(state.creature));
        }
        // ...and the same for the camera's SIZE. Nothing in the game writes
        // `ChrExFollowCam+0x468`, but the constructor sets it to -1, so a camera rebuilt by a warp
        // or a map load mid-possession would quietly go back to framing a Tarnished.
        //
        // `refresh` first, because it may reinstall the whole patch: `[camera]` is LIVE, so a
        // saved edit re-derives the row and re-points the override the same way a fresh possession
        // would. It costs one relaxed atomic load on the frames nothing has been edited.
        if state.camera.refresh() {
            possess_log(format_args!("{}", state.camera.log_line()));
            Self::write_derived(
                state.chr_id,
                &state.camera,
                state.creature,
                state.moveset.as_ref(),
            );
        }
        state.camera.reassert();

        // Point the HUD at the creature, EVERY frame rather than once at possession start, so
        // that toggling `[hud] enabled` in the config file takes effect mid-possession like every
        // other live table. Two atomic loads and a store; the detour itself does the work.
        if crate::config::hud().enabled {
            hud::follow(state.creature.address());
        } else {
            hud::stop();
        }

        // Co-locate: the player's real body follows the creature, which is what keeps the net
        // position, the compass and the release point all correct with one mechanism.
        state
            .player
            .request_move(state.last_position, state.creature.yaw());

        let movement = crate::config::movement();
        let stick = game::read_move_stick(&movement);
        let write = intent::intent(
            state.last_position,
            state.creature.yaw().unwrap_or(0.0),
            stick,
            movement.speed_scale,
            movement.turn_deadzone_deg,
        );
        Self::tick_moveset(state, write.moving());
        if !state.creature.write_move_intent(write) && first_frame {
            // Said once, on the frame it is first known, rather than sixty times a second.
            possess_log(format_args!(
                "movement: the AiIns layout canary did not pass, so no movement intent is being \
                 written -- the character is possessed and camera-followed but will not walk. \
                 The AiIns offsets are byte-proven on 1.16.2 and 1.17, so this is a THIRD build"
            ));
        }
    }
}

impl NpcPossessionEngine {
    /// Ask the game to create the creature `[spawn]` names, and start waiting for it.
    ///
    /// Returns `Accepted` the moment the `ChrIns` exists -- NOT when it is drivable. The state
    /// machine above treats that as an active possession, which is right: the player pressed the
    /// key, something happened, and pressing again must cancel it rather than start a second one.
    fn begin_spawn(&mut self, request: &PossessionRequest, player: Chr) -> PossessionOutcome {
        let settings = request.target.spawn;
        let Some(position) = player.position() else {
            return PossessionOutcome::Refused("the player's position did not read".to_owned());
        };
        let yaw = player.yaw().unwrap_or(0.0);
        // In front of the player, at the player's own height, using the SAME basis the movement
        // target uses -- see `intent::ahead_of` for why that is one function and not two.
        let place_at = intent::ahead_of(position, yaw, settings.distance_m);
        let spec = SpawnSpec {
            chr_id: settings.chr_id,
            npc_param_id: settings.resolved_npc_param_id(),
            npc_think_id: settings.npc_think_id,
            position: place_at,
            yaw,
        };
        let spawned = match spawn_game::spawn(&spec) {
            Ok(spawned) => spawned,
            Err(reason) => {
                Self::write_spawn_refusal(settings.chr_id, &reason);
                return PossessionOutcome::Refused(reason);
            }
        };
        possess_log(format_args!(
            "spawn: created c{:04} (NpcParam {}) as ChrIns=0x{:x} in buddy roster slot {} -- \
             waiting up to {} ms for it to become drivable. Nothing is pumped while it loads: \
             EneDatManImp::Update walks every slot each frame from the game's own STEP_Update",
            settings.chr_id,
            spec.npc_param_id,
            spawned.creature.address(),
            spawned.slot,
            settings.readiness_ms,
        ));
        self.pending = Some(Pending {
            spawned,
            player,
            request: *request,
            readiness: Readiness::new(u64::from(settings.readiness_ms)),
            caps: layout::ene_dat_cap_offsets(game_file_version()),
            place_at,
            place_from: position,
            configured_distance_m: settings.distance_m,
            place_yaw: yaw,
            started: std::time::Instant::now(),
            reported: None,
            despawn_on_release: settings.despawn_on_release,
            chr_id: settings.chr_id,
        });
        PossessionOutcome::Accepted
    }

    /// One frame of waiting for a spawned creature to come up.
    fn tick_pending(&mut self) {
        let Some(pending) = self.pending.as_mut() else {
            return;
        };
        let elapsed = u64::try_from(pending.started.elapsed().as_millis()).unwrap_or(u64::MAX);
        let caps = pending.caps;
        let spawned = pending.spawned;
        let verdict = pending
            .readiness
            .observe(elapsed, |gate| spawn_game::evaluate(&spawned, caps, gate));
        match verdict {
            Poll::Waiting(gate) => {
                // Once per gate CHANGE. A per-frame line on a five-second wait is three hundred
                // identical lines, which is the same as no line at all.
                if pending.reported != Some(gate) {
                    pending.reported = Some(gate);
                    possess_log(format_args!(
                        "spawn: c{:04} is waiting on {} ({} ms elapsed)",
                        pending.chr_id,
                        gate.name(),
                        elapsed
                    ));
                }
            }
            Poll::Ready => {
                let pending = self.pending.take().expect("checked above");
                self.finish_spawn(pending, elapsed);
            }
            // THE GAME took it away -- `EnemyIns::InitializeCharacterRendering` self-despawns a
            // character whose caps loaded but yielded no FLVER. The pointer is already dead, so
            // this drops it and MUST NOT call `RemoveChrIns` again.
            Poll::Vanished => {
                let pending = self.pending.take().expect("checked above");
                let reason = format!(
                    "the game removed c{:04} itself after {elapsed} ms, which is what it does to a \
                     character whose assets loaded but produced no model",
                    pending.chr_id
                );
                possess_log(format_args!("spawn: {reason}"));
                Self::write_spawn_refusal(pending.chr_id, &reason);
            }
            Poll::Expired(gate) => {
                let reached = pending.readiness.reached();
                let pending = self.pending.take().expect("checked above");
                // BOTH the gate it died on AND how far it ever got. "waiting on chrres-loaded"
                // reads the same whether nothing at all happened or everything up to the assets
                // did, and those are different problems.
                let reason = format!(
                    "c{:04} was still waiting on {} after {elapsed} ms (furthest stage reached: \
                     {}): {}",
                    pending.chr_id,
                    gate.name(),
                    reached.map_or("none", Gate::name),
                    gate.stuck_means()
                );
                possess_log(format_args!("spawn: giving up -- {reason}"));
                Self::write_spawn_refusal(pending.chr_id, &reason);
                Self::cancel_pending(pending, Reason::SpawnTimedOut, true);
            }
        }
    }

    /// The creature is drivable. Put it where the press asked for it and wear it.
    fn finish_spawn(&mut self, pending: Pending, elapsed: u64) {
        // Placed HERE and not at spawn time, because the creature's position field is not read on
        // the creature path of `CreateCharacter` at all -- `InitEnemyChrBaseData` supplies the base
        // data and the request's vectors are consumed only by the PlayerIns branch. So the request
        // says where it should be and this is what actually puts it there, through the same proxy
        // drain co-location uses.
        //
        // ...AND IT IS ALSO THE FIRST MOMENT THE CREATURE'S OWN SIZE CAN BE READ, which is why the
        // size-aware distance is derived here rather than at the press. `CSChrPhysicsModule` does
        // not exist until `InitForEnemy` has run; by this point it does, and `hit_radius` is the
        // real `NpcParam.hitRadius` rather than a guess. See `crate::spawn::placement` for why
        // `[spawn].distance_m` is now a floor.
        let placement = placement::place(
            pending.configured_distance_m,
            pending.spawned.creature.hit_radius(),
            pending.player.hit_radius(),
        );
        let place_at = if placement.creature_radius_m.is_some() {
            intent::ahead_of(pending.place_from, pending.place_yaw, placement.distance_m)
        } else {
            pending.place_at
        };
        let placed = pending
            .spawned
            .creature
            .request_move(place_at, Some(pending.place_yaw));
        possess_log(format_args!(
            "spawn: c{:04} became drivable after {elapsed} ms; {}",
            pending.chr_id,
            if placed {
                format!("placed in front of the player {}", placement.describe())
            } else {
                "COULD NOT BE PLACED, so it is wherever InitEnemyChrBaseData left it".to_owned()
            }
        ));
        let body = SpawnedBody {
            spawned: pending.spawned,
            despawn_on_release: pending.despawn_on_release,
        };
        let provenance = format!("mode=spawn slot={}", pending.spawned.slot);
        match self.enter(
            pending.spawned.creature,
            pending.player,
            &pending.request,
            &provenance,
            Some(body),
        ) {
            PossessionOutcome::Accepted => {}
            outcome => {
                // We made it and we cannot wear it, so we take it away -- an untracked creature is
                // one nothing will ever remove.
                let reason = format!(
                    "c{:04} loaded but could not be possessed: {}",
                    pending.chr_id,
                    outcome.describe()
                );
                possess_log(format_args!("spawn: {reason}"));
                Self::write_spawn_refusal(pending.chr_id, &reason);
                if pending.despawn_on_release {
                    spawn_game::despawn(pending.spawned.creature);
                }
            }
        }
    }

    /// `er-npc-possess.derived.toml`, for a press that produced no creature.
    ///
    /// Best-effort, like the moveset report it replaces: the game directory can be read-only, and a
    /// log line about failing to write a log-adjacent file is noise.
    fn write_spawn_refusal(chr_id: u32, reason: &str) {
        let _ = std::fs::write(
            crate::config::DERIVED_CONFIG_FILE_NAME,
            derived::render_spawn_refusal(chr_id, reason),
        );
    }

    /// Wear `creature`: install the thunk, move the camera, neuter the player's body and build the
    /// moveset.
    ///
    /// Extracted so the found-a-target path and the spawned-one path are the SAME code. They
    /// differ only in where the creature came from -- and `spawned` is that difference, carried
    /// into the teardown so it knows whether it owes the world a despawn.
    fn enter(
        &mut self,
        creature: Chr,
        player: Chr,
        request: &PossessionRequest,
        provenance: &str,
        spawned: Option<SpawnedBody>,
    ) -> PossessionOutcome {
        let Some(real_com) = creature.real_manipulator() else {
            return PossessionOutcome::Refused("the target has no manipulator".to_owned());
        };
        let Some(thunk) = Thunk::build(real_com) else {
            return PossessionOutcome::Refused("could not reserve the thunk page".to_owned());
        };
        if !creature.install_manipulator_override(thunk.object_address()) {
            // Either the slot was already occupied -- a second copy of this mod, or an assumption
            // that has stopped holding -- or the write itself did not land. Both are worse to
            // force than to decline.
            return PossessionOutcome::Refused("ChrCtrl+0x3b0 was not free".to_owned());
        }
        if !game::set_camera_override(Some(creature)) {
            // Roll the ONE thing that is already installed back before giving up, or the creature
            // is left brainless with nobody driving it.
            creature.clear_manipulator_override(thunk.object_address());
            return PossessionOutcome::Refused("could not move the camera".to_owned());
        }

        let debug_flags_offset = layout::debug_flags_offset(game_file_version());
        if debug_flags_offset.is_none() {
            possess_log(format_args!(
                "neuter: ChrIns.debugFlags has no known offset for {} -- the no-attack flag is \
                 being SKIPPED rather than written to a guessed address (it is +0x530 on 1.16.2 \
                 and +0x538 on 1.17, and the wrong one lands on a live neighbouring field)",
                describe_build()
            ));
        }
        let chr_id = creature.npc_param_id().unwrap_or(0);
        // THE CAMERA, last of the installs and after everything that can still refuse -- so there
        // is nothing to roll back if it fails, and nothing left behind if it succeeds and a later
        // step does not. `Session::begin` never panics and answers with a reason rather than an
        // error; see [`crate::camera`].
        let camera = camera::Session::begin(creature.address(), chr_id);
        possess_log(format_args!("{}", camera.log_line()));
        let moveset = Self::build_moveset(chr_id, request);
        let state = Possessing {
            creature,
            player,
            spawned,
            thunk,
            debug_flags_offset,
            release_on_death: request.target.release_on_death,
            last_position: creature.position().unwrap_or([0.0; 3]),
            last_grounded: creature.last_grounded_position(),
            last_on_ground: creature.standing_on_solid_ground().unwrap_or(false),
            frames: 0,
            camera,
            watchdog: Watchdog::new(
                // Seconds in the file, milliseconds in the engine. Clamped rather than cast bare:
                // a config value large enough to overflow would wrap to a tiny threshold and force
                // idle on every attack, which is the opposite of what the number asked for.
                (f64::from(request.mapping.watchdog_seconds) * 1000.0).clamp(1.0, 60_000.0) as u64,
                IDLE_ANIMATION,
            ),
            moveset,
            face: FaceEdges::default(),
            elapsed_ms: 0,
            started: std::time::Instant::now(),
            chr_id,
            reported_dead_input: [false; 4],
            fired_last_frame: false,
            reported_buffered_press: false,
        };
        Self::write_derived(chr_id, &state.camera, creature, state.moveset.as_ref());
        match state.moveset.as_ref() {
            Some(dispatcher) => {
                possess_log(format_args!(
                    "moveset: c{chr_id:04} {}",
                    dispatcher.summary()
                ));
            }
            None => possess_log(format_args!(
                "moveset: c{chr_id:04} is not in the shipped table, so this character has no \
                 attacks. That is expected for a variant that owns a model but no animations of \
                 its own -- 163 of the 408 the offline sweep looked at are like that. Movement, \
                 camera, the HUD and release all still work."
            )),
        }
        possess_log(format_args!(
            "possession: ChrIns=0x{:x} com=0x{real_com:x} thunk=0x{:x} camera=override player=0x{:x} \
             {provenance} spawned={} -- the body stays lock-on-able (that needs a SpEffect, not a \
             field write)",
            creature.address(),
            state.thunk.object_address(),
            player.address(),
            match state.spawned {
                None => "no (the map placed this one)",
                Some(body) if body.despawn_on_release => "yes, and it is removed again on release",
                Some(_) => "yes, and [spawn].despawn_on_release leaves it standing",
            },
        ));
        debug_assert_eq!(state.thunk.real_com(), real_com);
        self.active = Some(state);
        PossessionOutcome::Accepted
    }
}

impl PossessionEngine for NpcPossessionEngine {
    fn possess(&mut self, request: &PossessionRequest) -> PossessionOutcome {
        if self.active.is_some() {
            return PossessionOutcome::Refused("already possessing".to_owned());
        }
        if self.pending.is_some() {
            return PossessionOutcome::Refused("a spawn is still coming up".to_owned());
        }
        let Some(player) = game::main_player() else {
            return PossessionOutcome::Refused("no local player".to_owned());
        };
        // THE MODE THAT CREATES RATHER THAN FINDS. It returns before any of the possession
        // machinery, because there is nothing to possess yet -- see `tick_pending`.
        if request.target.mode == TargetMode::Spawn {
            return self.begin_spawn(request, player);
        }
        let (target, candidates) = game::pick_target(request.target, player);
        let Some(creature) = target else {
            return PossessionOutcome::Refused(game::describe_no_target(
                request.target.mode,
                candidates,
            ));
        };
        let provenance = format!(
            "mode={} candidates={candidates}",
            request.target.mode.name()
        );
        self.enter(creature, player, request, &provenance, None)
    }

    fn release(&mut self) -> PossessionOutcome {
        self.release_with(Reason::Hotkey)
    }

    fn tick(&mut self) {
        if self.pending.is_some() {
            self.tick_pending();
            return;
        }
        self.tick_active();
    }

    /// A spawn in flight counts as active.
    ///
    /// Otherwise `tick_engine` would reconcile the toggle back to idle on the first frame of the
    /// wait, and the player's next press would start a SECOND spawn while the first was still
    /// loading -- two creatures, one of them untracked and never removed.
    fn is_active(&self) -> bool {
        self.active.is_some() || self.pending.is_some()
    }

    /// Hold a config reload off while a spawn is coming up.
    ///
    /// The pending spawn carries the request it was started with, and it is replayed into the
    /// possession when the creature becomes drivable. Letting `[spawn]` move in between would mean
    /// the roster slot was created under one set of rules and worn under another -- and, if
    /// `despawn_on_release` moved, torn down under a third.
    fn accepts_reload(&self) -> bool {
        self.pending.is_none()
    }

    fn shutdown(&mut self) {
        // The one release that MUST happen even though nobody asked: an armed `ChrCtrl+0x3b0` in a
        // process that is about to unload this DLL is a DLPanic the next time that character is
        // torn down, with our code no longer present to explain it.
        //
        // A PENDING SPAWN GOES THROUGH THE SAME CALL, and this is why the check covers both: the
        // creature it created is real and registered even though nothing is possessed yet, so
        // "nothing to release" would be wrong. `release_with` declines the game call on this
        // thread and says the creature is being left; see `Step::DespawnCreature`.
        if self.active.is_some() || self.pending.is_some() {
            self.release_with(Reason::Shutdown);
        }
    }
}
