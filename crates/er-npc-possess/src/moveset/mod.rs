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
//! # The four parts
//!
//! * [`table`] -- what each creature can do, decided offline. Integers only.
//! * [`dispatch`] -- four fixed buttons onto 8-60 attacks, by distance band and rank cycling.
//! * [`watchdog`] -- forces idle when an animation strands the creature, and denies it afterwards.
//! * [`derived`] -- writes every decision, and every denial's reason, where the player can read it.
//!
//! # What is still a seam after this layer
//!
//! * **Nothing here is runtime-proven.** Every claim above is static: read out of the 1.16.2 dump,
//!   the unpacked corpus and `regulation.bin`. The game has not been launched against this code.
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
            "TABLE_VERSION = 2",
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
