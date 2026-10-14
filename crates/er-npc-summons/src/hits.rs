//! Hits on the squad and by the player: `LAB_WORLD.tt_ally_hit`, `tt_ally_victim` and
//! `tt_player_hit`, which provoke the turtles' brain.
//!
//! A port of the lab's `squadHit` (`scripts/frida/spawn-npc.js` on `feat/ai-lab-turtles`), driven
//! by a detour on `CalculateDamage2`, which runs once per landed hit (attacker argument 1, victim
//! `[argument 0 + 0x8]`; measured live by the lab's `chainsaw-driver.js` and `damageHook`).
//!
//! - A hostile landing a hit on the player or a companion bumps `tt_ally_hit`, a count, so every
//!   turtle can tell a new hit from one it has answered, and names the victim in
//!   `tt_ally_victim`: -1 for the player, else the companion's index. Any attacker but the player
//!   and the squad counts; measured 2026-10-06, the enemy hitting the turtles was team 7, so a team
//!   test would have let squad hits through.
//! - The player landing a hit on anything but the squad bumps `tt_player_hit` (user, 2026-10-06:
//!   they held back while the player fought and joined only once hit themselves).
//!
//! Both are numbers: measured 2026-10-06, a string victim made the lab's bridge raise on all 250
//! squad hits, so no turtle ever saw one. Quiet facts, read at each turtle's next decision.
//!
//! The detour only classifies and counts; the game task publishes. A squad hit also records the
//! attacker's animation and how far into it the hit landed, which `swings` learns windups from.

#![cfg_attr(not(windows), allow(dead_code))]

/// What one landed hit means for the squad.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Hit {
    /// The player hit something outside the squad.
    ByPlayer,
    /// A hostile hit the player (`-1`) or companion `n`.
    OnSquad(i32),
}

/// Classify a hit of `attacker` on `victim`, given the player and the squad in index order.
pub(crate) fn classify(
    victim: usize,
    attacker: usize,
    player: usize,
    squad: &[usize],
) -> Option<Hit> {
    let in_squad = |c: usize| squad.contains(&c);
    if player != 0 && attacker == player && victim != player && !in_squad(victim) {
        return Some(Hit::ByPlayer);
    }
    let who = if player != 0 && victim == player {
        -1
    } else {
        squad.iter().position(|&c| c == victim)? as i32
    };
    if (player != 0 && attacker == player) || in_squad(attacker) {
        return None;
    }
    Some(Hit::OnSquad(who))
}

#[cfg(windows)]
pub(crate) mod live {
    //! The detour, the snapshot it classifies against, and the publisher.

    use std::sync::Mutex;
    use std::sync::atomic::{AtomicI32, AtomicU32, AtomicUsize, Ordering};

    use super::{Hit, classify};
    use crate::addr;
    use crate::facts::{self, Notify};
    use crate::log::summons_log;
    use crate::world::{GameMem, Mem, anim_of, npc_param_of};

    const SQUAD_MAX: usize = 8;
    static PLAYER: AtomicUsize = AtomicUsize::new(0);
    static SQUAD: [AtomicUsize; SQUAD_MAX] = [const { AtomicUsize::new(0) }; SQUAD_MAX];
    static SQUAD_LEN: AtomicUsize = AtomicUsize::new(0);

    static PLAYER_HITS: AtomicU32 = AtomicU32::new(0);
    static SQUAD_HITS: AtomicU32 = AtomicU32::new(0);
    static LAST_VICTIM: AtomicI32 = AtomicI32::new(0);
    /// `(NpcParam, animation, time into it)` of attackers at a squad hit, for `swings`.
    static WINDUPS: Mutex<Vec<(i32, i32, f32)>> = Mutex::new(Vec::new());
    const WINDUPS_MAX: usize = 64;

    static ORIG: AtomicUsize = AtomicUsize::new(0);
    type CalcFn = unsafe extern "system" fn(usize, usize, usize, u64, u8);

    /// The player and the squad the detour classifies against; set by the game task each frame.
    pub(crate) fn set_squad(player: usize, squad: &[usize]) {
        PLAYER.store(player, Ordering::Release);
        let n = squad.len().min(SQUAD_MAX);
        for (i, &chr) in squad.iter().take(n).enumerate() {
            SQUAD[i].store(chr, Ordering::Release);
        }
        SQUAD_LEN.store(n, Ordering::Release);
    }

    fn snapshot() -> (usize, [usize; SQUAD_MAX], usize) {
        let mut squad = [0; SQUAD_MAX];
        let n = SQUAD_LEN.load(Ordering::Acquire);
        for (out, slot) in squad.iter_mut().zip(SQUAD.iter()).take(n) {
            *out = slot.load(Ordering::Acquire);
        }
        (PLAYER.load(Ordering::Acquire), squad, n)
    }

    fn observe(victim_module: usize, attacker: usize) {
        let Some(victim) = GameMem.usize(victim_module + addr::DAMAGE_MODULE_OWNER) else {
            return;
        };
        let (player, squad, n) = snapshot();
        if n == 0 {
            return;
        }
        match classify(victim, attacker, player, &squad[..n]) {
            Some(Hit::ByPlayer) => {
                PLAYER_HITS.fetch_add(1, Ordering::AcqRel);
            }
            Some(Hit::OnSquad(who)) => {
                LAST_VICTIM.store(who, Ordering::Release);
                SQUAD_HITS.fetch_add(1, Ordering::AcqRel);
                if let (Some(npc), Some(anim)) = (
                    npc_param_of(&GameMem, attacker),
                    anim_of(&GameMem, attacker),
                ) && let Ok(mut w) = WINDUPS.try_lock()
                    && w.len() < WINDUPS_MAX
                {
                    w.push((npc, anim.anim, anim.time));
                }
            }
            None => {}
        }
    }

    unsafe extern "system" fn calc_detour(
        victim_module: usize,
        attacker: usize,
        info: usize,
        p4: u64,
        p5: u8,
    ) {
        if victim_module != 0 && attacker != 0 {
            observe(victim_module, attacker);
        }
        let orig = ORIG.load(Ordering::Acquire);
        if orig != 0 {
            // SAFETY: the trampoline of the function this replaces, with its own arguments.
            let orig: CalcFn = unsafe { core::mem::transmute(orig) };
            unsafe { orig(victim_module, attacker, info, p4, p5) }
        }
    }

    /// Install the detour. Called once, from the install thread.
    pub(crate) fn install() {
        // SAFETY: MinHook init is idempotent across DLLs.
        match unsafe { er_hook::MH_Initialize() } {
            er_hook::MH_STATUS::MH_OK | er_hook::MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
            status => {
                summons_log(format_args!("hits: off -- MH_Initialize: {status:?}"));
                return;
            }
        }
        match crate::mimic_hooks::hook(
            addr::CALCULATE_DAMAGE2,
            calc_detour as *const () as usize,
            &ORIG,
            "CalculateDamage2",
        ) {
            Ok(_hook) => {
                // SAFETY: applies the queued enable.
                match unsafe { er_hook::MH_ApplyQueued() } {
                    er_hook::MH_STATUS::MH_OK => {
                        summons_log(format_args!("hits: CalculateDamage2 detoured"));
                    }
                    status => summons_log(format_args!("hits: MH_ApplyQueued failed: {status:?}")),
                }
            }
            Err(why) => summons_log(format_args!("hits: off -- {why}")),
        }
    }

    /// The windup samples taken since the last call.
    pub(crate) fn take_windups() -> Vec<(i32, i32, f32)> {
        WINDUPS
            .lock()
            .map(|mut w| std::mem::take(&mut *w))
            .unwrap_or_default()
    }

    #[derive(Default)]
    pub(crate) struct Publisher {
        player_hits: u32,
        squad_hits: u32,
    }

    impl Publisher {
        /// Publish the counts when they moved.
        pub(crate) fn tick(&mut self) {
            let player_hits = PLAYER_HITS.load(Ordering::Acquire);
            if player_hits != self.player_hits {
                self.player_hits = player_hits;
                facts::set_number("tt_player_hit", f64::from(player_hits), Notify::Quiet);
            }
            let squad_hits = SQUAD_HITS.load(Ordering::Acquire);
            if squad_hits != self.squad_hits {
                self.squad_hits = squad_hits;
                let victim = LAST_VICTIM.load(Ordering::Acquire);
                facts::set_number("tt_ally_hit", f64::from(squad_hits), Notify::Quiet);
                facts::set_number("tt_ally_victim", f64::from(victim), Notify::Quiet);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PLAYER: usize = 0x100;
    const A: usize = 0x200;
    const B: usize = 0x300;
    const ENEMY: usize = 0x900;

    #[test]
    fn a_hostile_hitting_the_squad_names_the_victim() {
        let squad = [A, B];
        assert_eq!(
            classify(PLAYER, ENEMY, PLAYER, &squad),
            Some(Hit::OnSquad(-1))
        );
        assert_eq!(classify(A, ENEMY, PLAYER, &squad), Some(Hit::OnSquad(0)));
        assert_eq!(classify(B, ENEMY, PLAYER, &squad), Some(Hit::OnSquad(1)));
    }

    #[test]
    fn the_player_hitting_an_outsider_counts_as_a_player_hit() {
        assert_eq!(classify(ENEMY, PLAYER, PLAYER, &[A]), Some(Hit::ByPlayer));
    }

    #[test]
    fn friendly_fire_counts_for_nothing() {
        let squad = [A, B];
        assert_eq!(classify(A, PLAYER, PLAYER, &squad), None);
        assert_eq!(classify(A, B, PLAYER, &squad), None);
        assert_eq!(classify(PLAYER, A, PLAYER, &squad), None);
    }

    #[test]
    fn hits_among_outsiders_count_for_nothing() {
        assert_eq!(classify(ENEMY, ENEMY + 1, PLAYER, &[A]), None);
    }

    #[test]
    fn without_a_player_only_squad_victims_count() {
        assert_eq!(classify(A, ENEMY, 0, &[A]), Some(Hit::OnSquad(0)));
        assert_eq!(classify(ENEMY, A, 0, &[A]), None);
    }
}
