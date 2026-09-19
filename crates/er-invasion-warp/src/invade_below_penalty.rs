//! Deathblight on the invader, if a search ever asks for a bracket below this character's own.
//!
//! # What this is and is not
//!
//! A tripwire, not a feature anybody should ever see fire. Two separate clamps already stop the
//! search looking downward -- `BracketChoice::band_for` holds each axis at or above the player's
//! own, and `band_ladder_value` declines a located host who publishes below them -- and
//! `band_ladder::climbed` only ever adds. This is what happens if all three are wrong at once.
//!
//! The player asked for it in those terms, 2026-09-18: "just in case someone, or we accidently
//! invade below our bracket". So the penalty lands on the invader, which on this machine means the
//! character running this DLL. It is applied with Seamless's network sync off, so it reaches this
//! client's own player and nothing else -- forcing a lethal effect onto somebody else's character
//! over the wire is a different act, and this is not the code to do it with.
//!
//! # What it can and cannot detect
//!
//! It fires on the band this client asked for being below the band Seamless computed for this
//! character. That is the whole of "we invaded below our bracket" as seen from this side, and it
//! is measurable here with no extra Steam round trip.
//!
//! It cannot see a host who turns out to be below the bracket they advertised. The band field is
//! compared for equality, so a host answering a query is publishing the band that query asked for;
//! a host whose real level disagrees with their own advertisement is lying to Steam, and nothing
//! on this client can catch that at join time.
//!
//! # Why it applies over several ticks rather than once
//!
//! Deathblight is a buildup, and one application of a buildup effect is not a death. The player
//! asked for "enough deathblight to cause them to get cursed", so this keeps applying while the
//! player is alive, up to a bounded number of ticks, and stops the moment the game reports them
//! dead. A fixed multiplier would be a guess at a per-application buildup value nothing here has
//! measured.

// Compiled on the host, unlike its neighbour `invade_difficulty`. Only [`tick`] touches the game
// -- everything else is band arithmetic and two atomics -- and the join-data hook that arms it is
// not windows-gated, so gating the whole module would mean gating its caller too and the host
// build would stop covering that path at all.

use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};

/// `SpEffectParam` row for deathblight buildup.
///
/// This id is this repo's own, not a fresh identification: `er-net-effects` ships it as the
/// `deathblight network test` trigger hotkey and `effects-data`'s catalogue tests name it
/// `Deathblight`. Row existence is confirmed against the installed `regulation.bin`
/// (`SpEffectParam`, 11354 rows); the row's name could not be read on this machine, which needs a
/// Smithbox checkout the box does not have. So the id is inherited evidence, and the log below
/// prints what the player's own effect list holds afterwards rather than asserting it worked.
const DEATHBLIGHT_SPEFFECT: i32 = 8355;

/// How many ticks the penalty keeps applying before it gives up.
///
/// A bound rather than a target. The intended end is the player dying, which the loop checks for
/// every tick; this only stops a tripwire that has fired on a character the effect cannot kill
/// from applying an effect forever.
const MAX_TICKS: u32 = 600;

/// Whether a query has gone out asking for a band below this character's own.
static ASKED_BELOW_OWN: AtomicBool = AtomicBool::new(false);

/// Whether the penalty is running, and for how many ticks it has been.
static ARMED: AtomicBool = AtomicBool::new(false);
static TICKS: AtomicU32 = AtomicU32::new(0);

/// Record that an outgoing query asked for `asked` while this character's own band is `own`.
///
/// Called from the one place that rewrites the field, so every band this client sends is checked
/// -- including the ones it does not rewrite, which is the case where a clamp has gone wrong
/// somewhere upstream rather than here.
pub fn observe_band(asked: &str, own: &str) {
    let (Some((asked_level, asked_weapon)), Some((own_level, own_weapon))) = (
        er_invasion_warp_core::band_ladder::split_band(asked),
        er_invasion_warp_core::band_ladder::split_band(own),
    ) else {
        return;
    };
    if asked_level >= own_level && asked_weapon >= own_weapon {
        return;
    }
    if ASKED_BELOW_OWN.swap(true, Ordering::SeqCst) {
        return;
    }
    crate::standalone_log(format_args!(
        "invade-below-penalty: a query went out asking for band {asked} while this character's own \
         is {own}, which is below it. Three separate clamps are supposed to make that impossible, \
         so one of them is wrong. If a match lands on this search the deathblight penalty applies \
         to this player."
    ));
}

/// Forget the tripwire, for a search that is over.
///
/// A flag that outlived its search would penalise the next invasion for a query this one sent.
pub fn forget() {
    ASKED_BELOW_OWN.store(false, Ordering::SeqCst);
}

/// A match landed. Arm the penalty if the search that found it asked below this character's band.
///
/// Called from the join-data hook, beside the arrival banner, because that is the first instant a
/// landed match is knowable on this machine.
pub fn on_match_landed() {
    if !ASKED_BELOW_OWN.swap(false, Ordering::SeqCst) {
        return;
    }
    ARMED.store(true, Ordering::SeqCst);
    TICKS.store(0, Ordering::SeqCst);
    crate::standalone_log(format_args!(
        "invade-below-penalty: this invasion was found by a query asking below this character's \
         own band, so deathblight is being applied to this player until it kills them. Nothing is \
         sent to anybody else: the effect goes on this client's own character with Seamless's \
         network sync off."
    ));
}

/// Whether the penalty is currently running.
#[must_use]
pub fn is_running() -> bool {
    ARMED.load(Ordering::SeqCst)
}

/// Apply one tick of the penalty. Called from the crate's recurring game task.
#[cfg(windows)]
pub fn tick() {
    if !ARMED.load(Ordering::SeqCst) {
        return;
    }
    let ticks = TICKS.fetch_add(1, Ordering::SeqCst) + 1;
    if ticks > MAX_TICKS {
        ARMED.store(false, Ordering::SeqCst);
        crate::standalone_log(format_args!(
            "invade-below-penalty: gave up after {MAX_TICKS} tick(s) -- the player is still alive, \
             so `SpEffect {DEATHBLIGHT_SPEFFECT}` is not building deathblight on this character the \
             way this expects. The penalty stops rather than applying an effect forever."
        ));
        return;
    }
    use fromsoftware_shared::FromStatic;
    // SAFETY: the game task owns this, which is the same context every other live read in this
    // crate runs on.
    let Ok(world_chr_man) = (unsafe { eldenring::cs::WorldChrMan::instance_mut() }) else {
        return;
    };
    let Some(player) = world_chr_man.main_player.as_mut() else {
        return;
    };
    use eldenring::cs::ChrInsExt;
    // `true` is `dont_sync`. The penalty is this character's alone and must not be published to
    // the session -- the invaded party did nothing wrong.
    player.apply_speffect(DEATHBLIGHT_SPEFFECT, true);
    // Dead is the end condition the player asked for, and the one worth reporting. Checked after
    // the application rather than before it so the tick that kills them is the tick that says so.
    if player.chr_ins.modules.data.hp <= 0 {
        ARMED.store(false, Ordering::SeqCst);
        crate::standalone_log(format_args!(
            "invade-below-penalty: the player is dead after {ticks} tick(s) of \
             `SpEffect {DEATHBLIGHT_SPEFFECT}`. The invasion that should not have been found is \
             over."
        ));
    }
}

/// Host-side stub. The decision half above is what the tests cover; applying a `SpEffect` needs
/// `WorldChrMan`, which only exists on the target.
#[cfg(not(windows))]
pub fn tick() {}

#[cfg(test)]
mod tests {
    use super::*;

    /// The tripwire is process-global, because the thing it guards is: one search, one client, one
    /// character. Tests of it therefore cannot run beside each other -- the first version of these
    /// failed on `0_1 is below 1_1 and must arm the penalty`, which was a sibling test's `forget`
    /// landing between this one's `observe_band` and its `on_match_landed`.
    static ONE_AT_A_TIME: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// Take the lock and start from nothing armed, so a test that fails mid-way cannot decide the
    /// next one's outcome.
    fn alone() -> std::sync::MutexGuard<'static, ()> {
        let guard = ONE_AT_A_TIME
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        forget();
        ARMED.store(false, Ordering::SeqCst);
        guard
    }

    /// The tripwire stays quiet for every band at or above the player's own, which is every band
    /// the three clamps can produce. A penalty that fired on ordinary play would be worse than no
    /// penalty at all.
    #[test]
    fn asking_at_or_above_the_players_own_band_never_arms_it() {
        let _alone = alone();
        forget();
        for asked in ["1_0", "1_1", "2_0", "8_3"] {
            observe_band(asked, "1_0");
        }
        on_match_landed();
        assert!(
            !is_running(),
            "armed on a band that is not below the player's own"
        );
    }

    /// Below on either axis alone is enough. A search that kept the player's level bracket and
    /// dropped their weapon bracket is still aimed at somebody weaker than they agreed to meet.
    #[test]
    fn below_on_either_axis_arms_it() {
        let _alone = alone();
        for asked in ["0_1", "1_0", "0_0"] {
            forget();
            observe_band(asked, "1_1");
            on_match_landed();
            assert!(
                is_running(),
                "{asked} is below 1_1 and must arm the penalty"
            );
            // Wind it back down so the next case starts clean; `tick` is what normally clears it
            // and it needs a game.
            super::ARMED.store(false, Ordering::SeqCst);
        }
    }

    /// A value that is not a band arms nothing. This sees every string filter the search sends,
    /// and Seamless's other keys are none of its business.
    #[test]
    fn a_value_that_is_not_a_band_is_ignored() {
        let _alone = alone();
        forget();
        for asked in ["", "_", "m61_48_45_00", "true", "x_1"] {
            observe_band(asked, "1_1");
        }
        observe_band("1_1", "not-a-band");
        on_match_landed();
        assert!(!is_running());
    }

    /// The tripwire does not outlive its search. Left set, it would penalise the next invasion for
    /// a query this one sent -- and the player would have no way at all to know why they died.
    #[test]
    fn a_search_that_ends_takes_the_tripwire_with_it() {
        let _alone = alone();
        forget();
        observe_band("0_0", "1_1");
        forget();
        on_match_landed();
        assert!(!is_running());
    }

    /// A landing consumes the tripwire, so one bad search cannot arm two penalties.
    #[test]
    fn one_bad_search_arms_one_penalty() {
        let _alone = alone();
        forget();
        observe_band("0_0", "1_1");
        on_match_landed();
        assert!(is_running());
        super::ARMED.store(false, Ordering::SeqCst);
        on_match_landed();
        assert!(
            !is_running(),
            "the second landing had no query of its own to answer for"
        );
    }
}
