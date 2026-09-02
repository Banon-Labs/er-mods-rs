//! THE MOVESET LAYER -- stack layer 3, the seam layer 2 left open.
//!
//! Layer 2 could wear a creature, move it and point a camera at it, and said in its own module
//! docs that attacks were left out because the animation-name question was unresolved. It is
//! resolved, and this is the answer.
//!
//! # Firing an attack is a field write
//!
//! `CS::CSChrEventModule::RequestAnimation` has a two-line body: a renderer-visibility call whose
//! effect the `ChrIns` update overwrites every frame, and one `int` store into
//! `CSChrEventModule+0x18 requestAnimationId`. So the whole firing mechanism is
//! `chr.request_animation(id)` writing an `i32`, and **this layer resolves no game function
//! address either** -- the property that makes the crate survive a game patch is intact through
//! all three layers.
//!
//! `CSChrEventModule::Update` consumes the request once per frame, formats
//! `DLString::FormatW(L"%s%04d", L"W_Event", animId)`, and resolves it against the same behaviour
//! world `PlayAnimationByBehaviorName` uses. The graph picks the clip, the clip carries its
//! TimeAct binding, and the hitbox, VFX, sound and root motion all arrive with it. None of that
//! had to be reimplemented, which is the entire reason this design drives ANIMATION ids rather
//! than behaviour ids.
//!
//! ## What is established about that root motion, and what is not
//!
//! The sentence above used to assert the displacement without anyone having watched it, so here
//! is the line between the two:
//!
//! * **This crate cannot be eating it.** The only thing possession writes anywhere near a
//!   character's own displacement is the co-location request, and that is written to the PLAYER:
//!   `driver.rs` calls `state.player.request_move(...)`, `state.player` is `game::main_player()`,
//!   and `request_move` resolves `self.chr_ctrl()` -- the player's. The creature's `ChrCtrl` proxy
//!   fields and flags are never touched, so the drain that snaps a body and zeroes its delta only
//!   ever reaches the one that is supposed to be dragged along.
//! * **It is the same mechanism walking rides on.** `crate::possess::intent` writes an AI move
//!   REQUEST; `[vt+0x50]` turns that into a normalised direction and hands it to
//!   `CSChrActionRequestModule`, the player's own request module, and the behaviour graph answers
//!   with locomotion clips. Nothing on that path takes a velocity. So attack root motion and walk
//!   displacement are one mechanism, not two, and they stand or fall together.
//! * **Nobody has measured a lunge.** [`watchdog::Sample::root_motion_squared`] is the semaphore
//!   that would say -- `CSChrBehaviorModule+0x30`, now byte-verified on 1.17, so it is reading the
//!   right field on the installed build. What it reports for a known-displacing attack has not
//!   been captured, and the watchdog's own conjunction hides it: an attack is fired by a press, so
//!   `input_consumed` is true and the "going nowhere" arm is never reached on the frames that
//!   would answer the question. Capturing it needs a live run.
//!
//! [`watchdog::Sample::root_motion_squared`]: crate::moveset::watchdog::Sample::root_motion_squared
//!
//! # The one thing that cannot be checked at runtime
//!
//! An animation id the creature's behaviour graph has no transition for resolves cleanly and then
//! **does nothing**. No error, no exception, nothing on screen. The median creature declares 1366
//! event names and can fire 580 of them, so "just try it" would be wrong more than half the time
//! and would fail invisibly every time.
//!
//! That is why [`table`] exists: the fireability gate runs OFFLINE, over the unpacked corpus,
//! where the graph can be read -- and what ships is the answer. See
//! `scripts/er-moveset-table-gen.py`.
//!
//! # Grabs, and the animation this layer first mistook for one
//!
//! Layer 3 shipped saying no creature's grab could be fired, having swept all 408 creatures and
//! found that no event name in the 4000 band has a transition behind it under any prefix. That
//! sweep result is correct. The conclusion drawn from it was not, because the 4000-band clip is
//! not the grab.
//!
//! `CS::ChrDamageModule::ApplyDamage` looks up the `AtkParam` row of the hit that just landed and
//! reads `throwTypeId`. If it is non-zero it calls
//! `CSChrThrowModule::InitThrow(attackerThrowModule, victimChrIns, throwTypeId)` **before any
//! damage is calculated**, and returns early when the throw is accepted.
//! `CSThrowNode::ValidateAttemptAndReturnParamId` scans `ThrowParam` for a row matching (attacker
//! `ChrIns::npcId`, victim `ChrIns::npcId`, `throwTypeId`); on a match `CSChrThrowModule` sets
//! both nodes' roles and `PlayThrowAnim` plays `W_ThrowAtk` on the attacker and `W_ThrowDef` on
//! the victim. Those are BARE names with no id in them -- which is exactly why nothing in the 4000
//! band is reachable by an event name, and why the fireability sweep was right and the label was
//! wrong.
//!
//! So the thing to fire is the INITIATOR, and it was already in the table as a plain attack: 153
//! of them across 78 creatures, every one in the 3000 band, all already fireable. Marking them
//! costs **no game address, no offset and no hook** -- the whole change is an extra param join in
//! the generator. What the runtime gained is a reason: [`table::Throw`] carries the victim chr id
//! and range the throw system will demand, `allow_grabs` finally withholds something, and a grab
//! that needs a creature victim is not offered when there is none in range.
//!
//! # The four parts
//!
//! * [`table`] -- what each creature can do, decided offline. Integers only.
//! * [`dispatch`] -- four fixed buttons onto 8-60 attacks, by distance band and rank cycling.
//! * [`chain`] -- WHEN a press is allowed to happen: chain, wait, or fire. Never interrupt.
//! * [`watchdog`] -- forces idle when an animation strands the creature, and denies it afterwards.
//! * [`derived`] -- writes every decision, and every denial's reason, where the player can read it.
//!
//! # Cancel discipline, which is the fifth thing and arrived last
//!
//! Firing was a field write from the first commit, and the field write is honoured whenever it
//! lands -- `CSChrEventModule::Update` gates it on death, a throw, an HKS-driven flag and five
//! item-pickup ids, and on NOTHING about the animation currently playing. So for as long as this
//! layer only decided WHICH move, a second press mid-swing cancelled the first, and the mod both
//! chained and interrupted depending on when you pressed.
//!
//! [`chain`] is the answer, and the rule it applies is the game's own: TAE event type 0 with
//! `FlagType` 86 sets the `taeCancels` bit that `CS::CSAiFunc::IsEnableCancelAttack` reads to let
//! a creature's AI chain out of a swing. Reading that bit is the whole oracle; the shipped table
//! carries the same window measured offline, as the fallback and as something the derived report
//! can print per move.
//!
//! # What is still a seam after this layer
//!
//! * **Nothing here is runtime-proven.** Every claim above is static: read out of the 1.16.2 dump,
//!   the unpacked corpus and `regulation.bin`. The game has not been launched against this code.
//! * **The cancel window is read one frame late.** The possession ticks in
//!   `CSTaskGroupIndex::FrameBegin`; `CS::ChrIns::PreBehaviorSafe` clears the transient
//!   `taeCancels` bits and the TimeAct events re-set them later in the same frame. 16 ms on a
//!   window whose median width is 800 ms, and not something this layer can fix without a second
//!   task in a later group.
//! * **ONE game function address, on the fallback path only.** `requestAnimationId` spells
//!   `W_Event%04d` and nothing else, and `W_Event` is a broad alias layer rather than a total one
//!   -- 88.4% num==anim-id across the corpus against 100% for `W_Step` in 6000-6023. So dodges,
//!   goal actions and the ride sets have no `W_Event` name at all and the field write cannot ask
//!   for them, however fireable they are. Those ids are fired through
//!   `PlayAnimationByBehaviorName` with a name built from the prefix the generator resolved from
//!   that creature's own event table, which costs the crate's zero-address property FOR THOSE
//!   MOVES ONLY. `W_Event` is preferred wherever it resolves, so the great majority never touch
//!   it. Losing every dodge in the game to keep a property was not a trade worth making.
//! * **The distance the dispatcher bands on is to the nearest hostile, not to a lock-on target.**
//!   `CSLockTgtMan` is not modelled by this crate, and reaching it would need either an address or
//!   an offset nobody here has verified on 1.17.
//! * **Reach for a move whose only damage row is a zero-damage marker is [`table::Reach::Unknown`],**
//!   so it is offered in every band rather than the right one.
//! * **Root-motion TRAVEL never made it into the table.** It is the documented fallback for reach
//!   when there is no hit capsule, and getting it means deserialising the animation itself, which
//!   the offline toolchain does not do.
//! * **A grab whose victim is the PLAYER is REFUSED while the possession is running.** Settled
//!   statically on both builds, and not a limitation of this layer -- see
//!   [`crate::possess::layout::chr_ins::INVINCIBLE`] for the trace and
//!   `NpcPossessionEngine::neuter` for why it is kept. In short: `ApplyDamage` is the only caller
//!   of `InitThrow`, `HitChr` the only caller of `ApplyDamage`, and both hit-resolution routines
//!   that reach `HitChr` with a real attacker clear the victim through `IsImmuneToAttack` first --
//!   which reads the `chrFlags1c5 & 0x10` the neuter sets. `IsImmuneToThrow`, the adjacent vtable
//!   slot (`+0x1E0` against `+0x1D8`), does NOT read that bit, but it only guards the throw's own
//!   downstream pre-check; it never gets reached, because the hit that would START the throw is
//!   dropped first. The escape hatch in the predicate is `AtkParam.isDisableNoDamage`, which 188
//!   of the 206 rows with a non-zero `throwTypeId` leave at 0. The initiator plays either way; a
//!   grab that is refused is a swing that misses, and the derived file says so per move.
//! * **What is still unproven about grabs** is the co-op case: `ThrowParam.DefChrId` 0 matches any
//!   `ChrIns::npcId` 0, which a SECOND player in the session also has, and this crate makes only
//!   the possessing player's own body immune. Nothing here has been runtime-tested.

pub(crate) mod chain;
pub(crate) mod derived;
pub(crate) mod dispatch;
pub(crate) mod table;
pub(crate) mod watchdog;

#[cfg(test)]
mod tests {
    /// The generator and this crate must agree on what the numbers in the table MEAN. They are
    /// written in two languages and there is no shared header, so the codes are asserted against
    /// the generator's source text -- if somebody renumbers a bucket on one side, this goes red
    /// instead of the moveset quietly turning every heavy attack into a ranged one.
    #[test]
    fn the_generator_and_the_parser_agree_on_every_code() {
        let generator = include_str!("../../../../scripts/er-moveset-table-gen.py");
        for expected in [
            "BUCKET_LIGHT, BUCKET_HEAVY, BUCKET_RANGED, BUCKET_MOVEMENT = 0, 1, 2, 3",
            "REACH_UNKNOWN, REACH_CLOSE, REACH_MID, REACH_FAR = 0, 1, 2, 3",
            "DENY_NOT_FIREABLE = 1",
            "DENY_NO_CLIP = 2",
            "DENY_NO_DAMAGE_WINDOW = 3",
            "DENY_MISSING_ATK_ROW = 4",
            "DENY_SPEFFECT_ONLY = 5",
            "DENY_UNRESOLVED_BEHAVIOR = 6",
            // The prefix table is a second shared vocabulary: the generator writes an index into
            // it and this crate turns that index back into the literal it will build a name from.
            // Getting them out of step would fire `W_Step` names at `W_GoalAction` ids.
            "'W_Event',",
            "'W_Attack',",
            "'W_Step',",
            "'W_GuardAttack',",
            "'W_GoalAction',",
            "'W_Ride_Attack_',",
            "'W_RideStep',",
            "'W_Ridden_Enemy_Attack',",
            "'W_Ride_Enemy_Attack',",
            "'W_Ridden_Enemy_Step',",
            "'W_Ride_Enemy_Step',",
            "DENY_UNUSABLE_AT_RUNTIME = 9",
            // The grab join. The column name is the whole mechanism: an attack is a grab because
            // its AtkParam row says so, not because its animation id is in a band.
            "DENY_THROW_RESULT_CLIP = 10",
            "ATK_THROW_FIELD = 'throwTypeId'",
            "'AtkChrId', 'DefChrId', 'throwTypeId', 'Dist'",
            // The chain window. 86 is the CREATURE cancel-into-attack flag and 4 is the PLAYER
            // one; swapping them silently would produce a table with almost no windows in it,
            // which reads as "this game has no combos" rather than as a bug.
            "CANCEL_ATTACK_FLAG_TYPES = (86, 4)",
            "CHR_ACTION_FLAG_ARG = 0",
            "TABLE_VERSION = 4",
        ] {
            assert!(
                generator.contains(expected),
                "the generator no longer says `{expected}` -- the shipped table's codes and this \
                 crate's enums have drifted apart"
            );
        }
    }

    /// The generator's neutral boundary and the watchdog's must be the same number, or the
    /// watchdog either never arms or never disarms.
    #[test]
    fn the_generator_and_the_watchdog_agree_on_where_the_attack_band_starts() {
        let generator = include_str!("../../../../scripts/er-moveset-table-gen.py");
        assert!(generator.contains("ATTACK_BAND = (3000, 3999)"));
        assert_eq!(super::watchdog::NEUTRAL_ANIMATION_CEILING, 3000);
    }
}
