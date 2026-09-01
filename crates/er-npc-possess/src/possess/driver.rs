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
use crate::log::possess_log;
use crate::possess::game::{self, Chr};
use crate::possess::teardown::{Reason, Step, Teardown};
use crate::possess::thunk::Thunk;
use crate::possess::{intent, layout};

/// Fully opaque, for the player's own body while somebody else is being worn.
const ALPHA_INVISIBLE: f32 = 0.0;
/// ...and back.
const ALPHA_OPAQUE: f32 = 1.0;

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
        let write = intent::intent(
            state.last_position,
            state.creature.yaw().unwrap_or(0.0),
            game::read_move_stick(),
            movement.speed_scale,
        );
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
        };
        possess_log(format_args!(
            "possession: ChrIns=0x{:x} com=0x{real_com:x} thunk=0x{:x} camera=override player=0x{:x} \
             mode={} candidates={candidates} -- attacks are NOT wired in this layer, and the body \
             stays lock-on-able (that needs a SpEffect, not a field write)",
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
