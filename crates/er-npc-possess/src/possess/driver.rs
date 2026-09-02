//! The engine itself: what possession start, each frame, and release actually do.
//!
//! Split out of `possess/mod.rs` so the module tree stays honest about what is host-testable.
//! Everything in here reads or writes live game memory through the windows-only `eldenring`
//! bindings; the offset table, the thunk emitter, the teardown ordering and the movement math are
//! siblings of this file precisely so they can be proved by `cargo test` without a game.
//!
//! See the module docs on [`crate::possess`] for what the whole thing does and what it leaves out.

use er_game_base::game_build::{describe_build, game_file_version};

use crate::engine::{PossessionEngine, PossessionOutcome, PossessionRequest};
use crate::input::FaceEdges;
use crate::log::possess_log;
use crate::moveset::dispatch::{Context, Dispatcher, Input, Locomotion};
use crate::moveset::table::{self, Denial};
use crate::moveset::watchdog::{Sample, Verdict, Watchdog};
use crate::moveset::{derived, table as moveset_table};
use crate::possess::game::{self, Chr};
use crate::possess::teardown::{Reason, Step, Teardown};
use crate::possess::thunk::Thunk;
use crate::possess::{intent, layout};

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

/// One possession in flight.
struct Possessing {
    creature: Chr,
    player: Chr,
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
}

impl NpcPossessionEngine {
    pub(crate) const fn new() -> Self {
        Self { active: None }
    }

    /// Everything that has to be true of the player's body while somebody else is being worn.
    ///
    /// Re-applied every frame rather than set once, and each of the three has its own reason:
    /// the alpha has a per-frame decay modifier the engine drives, the mute is cleared by four
    /// (re)spawn/teleport paths, and `debugFlags` is cheap enough that checking whether it drifted
    /// costs more than writing it.
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

    /// Run the release, in order, whatever fails. See [`teardown`].
    fn release_with(&mut self, reason: Reason) -> PossessionOutcome {
        let Some(state) = self.active.take() else {
            // Releasing when nothing is possessed must be safe and quiet: the shutdown path calls
            // it unconditionally, and so does the state machine after a refusal.
            return PossessionOutcome::Accepted;
        };
        let mut run = Teardown::new(reason);
        let thunk_address = state.thunk.object_address();
        let release_point = state.release_point();
        run.run(|step| match step {
            Step::RestoreBody => Self::restore(&state),
            // Nothing to undo: the per-frame driver stops the moment `active` is `None`, which
            // happened at the `take()` above. The step exists so the ORDER is a thing the state
            // machine enforces rather than a comment.
            Step::StopColocating => true,
            Step::MovePlayer => state.player.request_move(release_point, None),
            Step::ClearCameraOverride => game::set_camera_override(None),
            Step::ClearManipulatorOverride => {
                state.creature.clear_manipulator_override(thunk_address)
            }
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
    fn tick_moveset(state: &mut Possessing, stick_active: bool) {
        let Some(dispatcher) = state.moveset.as_mut() else {
            return;
        };
        let pressed = state.face.feed(crate::input::read_face_inputs());
        // CONSUMED, not held. See [`Sample::input_consumed`]: a press that fired nothing is
        // evidence of being stuck, not evidence against it, so a player mashing at a softlock must
        // not be able to hold the watchdog off indefinitely.
        let consumed = core::mem::take(&mut state.fired_last_frame);

        if let Some(animation) = state.creature.current_animation() {
            let sample = Sample {
                animation,
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
                    dispatcher.on_neutral();
                    possess_log(format_args!(
                        "moveset: animation {blame} left the creature stuck with no root motion \
                         for {} ms -- forced back to idle and denied for the rest of this \
                         session. It is in {} as unusable-at-runtime; copy it into \
                         er-npc-possess.toml under [chr.c{:04}] to keep it denied.",
                        state.elapsed_ms,
                        crate::config::DERIVED_CONFIG_FILE_NAME,
                        state.chr_id,
                    ));
                    Self::write_derived(state.chr_id, dispatcher);
                    return;
                }
            }
        }

        if pressed == 0 {
            return;
        }
        // ONE REQUEST PER FRAME, and only into an empty slot. `requestAnimationId` holds a single
        // id which `CSChrEventModule::Update` consumes once per frame; a second write before that
        // runs discards the first silently. Two buttons in one frame therefore has to mean one
        // attack, not one attack lost.
        if state.creature.animation_request_pending() {
            return;
        }
        let Some(input) = Input::ALL
            .iter()
            .copied()
            .find(|input| pressed & (1 << Self::input_bit(*input)) != 0)
        else {
            return;
        };
        let context = Context {
            distance_m: game::nearest_hostile_distance(state.creature),
            locomotion: if stick_active {
                Locomotion::Moving
            } else {
                Locomotion::Neutral
            },
            now_ms: state.elapsed_ms,
        };
        match dispatcher.press(input, context) {
            Ok(chosen) => {
                if Self::fire(state.creature, chosen) {
                    state.watchdog.armed_with(chosen.fire);
                    state.fired_last_frame = true;
                }
            }
            Err(reason) => {
                // Said once per possession per input rather than per press: a player holding a
                // dead button would otherwise write the log file at sixty lines a second.
                if !state.reported_dead_input[Self::input_bit(input)] {
                    state.reported_dead_input[Self::input_bit(input)] = true;
                    possess_log(format_args!(
                        "moveset: {} has nothing to fire on c{:04} ({}). See {} for what this \
                         creature has and why the rest was withheld.",
                        input.name(),
                        state.chr_id,
                        reason.explanation(),
                        crate::config::DERIVED_CONFIG_FILE_NAME,
                    ));
                }
            }
        }
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
    fn write_derived(chr_id: u32, dispatcher: &Dispatcher) {
        let text = derived::render(chr_id, dispatcher.moveset(), &dispatcher.summary());
        let _ = std::fs::write(crate::config::DERIVED_CONFIG_FILE_NAME, text);
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

        // Co-locate: the player's real body follows the creature, which is what keeps the net
        // position, the compass and the release point all correct with one mechanism.
        state
            .player
            .request_move(state.last_position, state.creature.yaw());

        let movement = crate::config::movement();
        let stick = game::read_move_stick();
        let write = intent::intent(
            state.last_position,
            state.creature.yaw().unwrap_or(0.0),
            stick,
            movement.speed_scale,
        );
        Self::tick_moveset(state, stick.is_some());
        if !state.creature.write_move_intent(write) && first_frame {
            // Said once, on the frame it is first known, rather than sixty times a second.
            possess_log(format_args!(
                "movement: the AiIns layout canary did not pass, so no movement intent is being \
                 written -- the character is possessed and camera-followed but will not walk. \
                 AiIns field offsets come from the 1.16.2 dump and the 1.17 sweep did not cover \
                 that struct"
            ));
        }
    }
}

impl PossessionEngine for NpcPossessionEngine {
    fn possess(&mut self, request: &PossessionRequest) -> PossessionOutcome {
        if self.active.is_some() {
            return PossessionOutcome::Refused("already possessing".to_owned());
        }
        let Some(player) = game::main_player() else {
            return PossessionOutcome::Refused("no local player".to_owned());
        };
        let (target, candidates) = game::pick_target(request.target, player);
        let Some(creature) = target else {
            return PossessionOutcome::Refused(game::describe_no_target(
                request.target.mode,
                candidates,
            ));
        };
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
        let moveset = Self::build_moveset(chr_id, request);
        let state = Possessing {
            creature,
            player,
            thunk,
            debug_flags_offset,
            release_on_death: request.target.release_on_death,
            last_position: creature.position().unwrap_or([0.0; 3]),
            last_grounded: creature.last_grounded_position(),
            last_on_ground: creature.standing_on_solid_ground().unwrap_or(false),
            frames: 0,
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
        };
        match state.moveset.as_ref() {
            Some(dispatcher) => {
                possess_log(format_args!(
                    "moveset: c{chr_id:04} {}",
                    dispatcher.summary()
                ));
                Self::write_derived(chr_id, dispatcher);
            }
            None => possess_log(format_args!(
                "moveset: c{chr_id:04} is not in the shipped table, so this character has no                  attacks. That is expected for a variant that owns a model but no animations of                  its own -- 163 of the 408 the offline sweep looked at are like that. Movement,                  camera and release all still work."
            )),
        }
        possess_log(format_args!(
            "possession: ChrIns=0x{:x} com=0x{real_com:x} thunk=0x{:x} camera=override player=0x{:x} \
             mode={} candidates={candidates} -- the body stays lock-on-able (that needs a \
             SpEffect, not a field write)",
            creature.address(),
            state.thunk.object_address(),
            player.address(),
            request.target.mode.name(),
        ));
        debug_assert_eq!(state.thunk.real_com(), real_com);
        self.active = Some(state);
        PossessionOutcome::Accepted
    }

    fn release(&mut self) -> PossessionOutcome {
        self.release_with(Reason::Hotkey)
    }

    fn tick(&mut self) {
        self.tick_active();
    }

    fn is_active(&self) -> bool {
        self.active.is_some()
    }

    fn shutdown(&mut self) {
        // The one release that MUST happen even though nobody asked: an armed `ChrCtrl+0x3b0` in a
        // process that is about to unload this DLL is a DLPanic the next time that character is
        // torn down, with our code no longer present to explain it.
        if self.active.is_some() {
            self.release_with(Reason::Shutdown);
        }
    }
}
