//! With another player in the session, allow only the effects that are safe to be seen.
//!
//! # The rule
//!
//! An allowlist, and the direction is the whole design. `SpEffectParam` has 11354 rows; 810
//! of them are fit to put on a character somebody else is looking at. A blocklist would have
//! to enumerate the other 10544 and would still pass anything it had not heard of -- every
//! row of the next patch, and every id a player types into a hand-made catalog. This refuses
//! what it does not recognise.
//!
//! Membership is derived, never hand-listed. `data/pvp-allowed-effects.json` is produced by
//! `scripts/generate-pvp-allowed.py` from three properties the master catalog already holds:
//!
//! - `is_visuals_only` -- the effect changes nothing but what the character looks like. The
//!   same predicate that builds the `visuals-only` selector catalog, imported rather than
//!   restated, so what the player can scroll and what the gate permits cannot drift apart.
//! - not `pvp.status_icon` -- the row has no `iconId`, so the engine puts nothing in the
//!   recipient's status bar. A row that does is a state the game means the player to be told
//!   about, which is what a mod must not hand them silently.
//! - not `pvp.cures_target` -- the row's `stateInfo` is not one the cure switch at 1.16.2
//!   `0x1404fc190` accepts as a curer. A curer makes the engine walk the recipient's
//!   active-effect list and delete entries from it, so pushing one at a peer takes effects
//!   off them rather than adding one.
//!
//! Measured against a hand-marked pass over the 843-row `visuals-only` catalog: of the 41
//! effects marked as unfit, 33 fall outside the allowlist. The eight that survive are the
//! camouflage family, two AI replanning timers, one enemy-only row and one retribution
//! counter -- no property shared by those and absent from the rest has been found, so they
//! are left in rather than papered over with a list. See
//! `docs/recon/marked-effect-provenance.md`.
//!
//! # When it fires
//!
//! Whenever another real player is in the session and the effect is not on the list. Alone in
//! a world there is nobody to protect, so the whole catalog stays available.
//!
//! There is no sync setting left for it to consult. Effects always go on the wire; the list
//! is the only thing deciding who may be affected. The flag that used to exist was the
//! player's own preference, changeable from the config file, a driver command or the
//! selector, which made it an off switch for this gate held by the person it restrains.

// Windows-only in practice. The decision logic is portable so `cargo test` on the host covers
// it, which is where `scripts/check.sh` runs these tests.
#![cfg_attr(not(windows), allow(dead_code))]

/// The ids that may travel to a peer, sorted so the lookup is a binary search.
///
/// The generator emits them sorted and a test in `er-quickload-data` asserts it, so the
/// invariant is checked where the data is rather than assumed here -- and `new` sorts again
/// anyway, because an unsorted list would answer "not allowed" for most of itself, which
/// looks exactly like the gate being over-strict rather than broken.
pub(crate) struct AllowedEffects {
    ids: Vec<i32>,
}

impl AllowedEffects {
    pub(crate) fn new(mut ids: Vec<i32>) -> Self {
        ids.sort_unstable();
        ids.dedup();
        Self { ids }
    }

    pub(crate) fn len(&self) -> usize {
        self.ids.len()
    }

    pub(crate) fn contains(&self, id: i32) -> bool {
        self.ids.binary_search(&id).is_ok()
    }

    /// The ids themselves, for the synthetic catalog that lets a player scroll them.
    pub(crate) fn ids(&self) -> &[i32] {
        &self.ids
    }
}

/// Whether this effect must be turned away.
///
/// One session fact decides it: is somebody else here. Deliberately not two.
///
/// This also tested a `network_sync` flag, on the reasoning that an unsynced effect never
/// reaches a peer so there is nobody to protect. That reasoning handed the player an off
/// switch: the flag was theirs three ways -- a line in `er-net-effects.toml`, a `network off`
/// driver command, and a selector toggle -- so one edit or one keypress re-opened all 11354
/// rows in a live session. A restriction that exists to protect somebody else's game cannot
/// be switchable by the person it restrains, so the flag was deleted rather than defaulted
/// and effects now always sync.
///
/// Returns true to refuse. An empty allowlist therefore refuses everything while a peer is
/// present, which is the correct direction for a table that failed to load: the mod goes
/// quiet rather than pushing 11354 unvetted rows at somebody.
pub(crate) fn refuses(allowed: &AllowedEffects, id: i32, peers_present: bool) -> bool {
    if !peers_present {
        return false;
    }
    !allowed.contains(id)
}

#[cfg(windows)]
mod runtime {
    use std::sync::OnceLock;

    use eldenring::cs::CSSessionManager;
    use fromsoftware_shared::FromStatic;

    use super::AllowedEffects;

    static ALLOWED: OnceLock<AllowedEffects> = OnceLock::new();

    /// The parsed embedded allowlist, built once.
    ///
    /// A parse failure yields an empty list and a log line rather than a panic. Empty is
    /// fail-closed here -- every effect is refused while a peer is present -- so the failure
    /// costs the player their effects in multiplayer instead of costing a stranger their
    /// game, and a DLL that takes the process down on load is worse than either.
    pub(crate) fn allowed() -> &'static AllowedEffects {
        ALLOWED.get_or_init(|| match er_quickload_data::embedded_pvp_allowed() {
            Ok(parsed) => {
                let allowed = AllowedEffects::new(parsed.effects);
                crate::log::net_effects_log(format_args!(
                    "pvp-gate: {} effects allowed with a peer present (regulation {}, \
                     filtered by {})",
                    allowed.len(),
                    parsed.source.binder_version,
                    parsed.disqualifying_tags.join(" + ")
                ));
                allowed
            }
            Err(error) => {
                crate::log::net_effects_log(format_args!(
                    "pvp-gate: embedded allowlist failed to parse ({error}) -- every effect \
                     will be refused while a peer is present"
                ));
                AllowedEffects::new(Vec::new())
            }
        })
    }

    /// Is another real player in this session.
    ///
    /// Read from `CSSessionManager::players`, the session's player vector. Its length counts
    /// the peers this client knows about, so a non-empty vector means somebody else is here.
    /// False when the singleton is absent: before the session manager exists there is nobody
    /// to protect, and a gate that fired on a missing pointer would refuse effects on the
    /// title screen.
    ///
    /// # Safety
    ///
    /// Called from the game task, which is the main thread, satisfying `instance`'s
    /// requirement that no mutable reference is live and the game is not mutating the fields
    /// being read.
    pub(crate) fn peers_present() -> bool {
        let Ok(session) = (unsafe { CSSessionManager::instance() }) else {
            return false;
        };
        session.players.len() > 0
    }
}

#[cfg(windows)]
pub(crate) use runtime::{allowed, peers_present};

#[cfg(test)]
mod tests {
    use super::*;

    fn allowed() -> AllowedEffects {
        AllowedEffects::new(vec![300, 100, 200])
    }

    #[test]
    fn an_unsorted_list_still_finds_every_one_of_its_own_ids() {
        let allowed = allowed();
        assert_eq!(allowed.len(), 3);
        for id in [100, 200, 300] {
            assert!(allowed.contains(id), "id {id} went missing after the sort");
        }
        assert!(!allowed.contains(150));
    }

    #[test]
    fn duplicate_ids_collapse() {
        assert_eq!(AllowedEffects::new(vec![7, 7, 7]).len(), 1);
    }

    #[test]
    fn nothing_is_refused_when_alone() {
        assert!(!refuses(&allowed(), 999, false));
    }

    #[test]
    fn an_allowed_effect_passes_with_a_peer_present() {
        assert!(!refuses(&allowed(), 200, true));
    }

    #[test]
    fn an_effect_outside_the_list_is_refused_with_a_peer_present() {
        assert!(refuses(&allowed(), 999, true));
    }

    #[test]
    fn an_empty_list_refuses_everything_rather_than_allowing_it() {
        let empty = AllowedEffects::new(Vec::new());
        assert!(refuses(&empty, 1, true));
        // ...and still lets a solo player use their whole catalog.
        assert!(!refuses(&empty, 1, false));
    }
}
