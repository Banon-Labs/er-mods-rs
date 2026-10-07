//! Keep every Mimic Tear companion on the player's team.
//!
//! A companion body comes from the `npc_param` its config names, and the turtles name
//! `523590024`, Moongrum's enemy row. The game builds the character on that row's team (6, an
//! enemy), not on the Mimic Tear's, so a companion is hostile to the player from the frame it
//! exists. The brain holds it back until something provokes it; on 2026-10-07 a duel provided
//! that, and the three companions killed the player.
//!
//! The AI lab had already measured and fixed this (`scripts/frida/spawn-npc.js` `mimicTeam`,
//! 2026-10-06): copy the player's `ChrIns+0x6c` team byte onto each companion at creation and
//! again every frame. This is that fix, moved into the DLL that builds the companions.

use std::sync::Mutex;

use er_game_base::mem::safe_read_u8;

use crate::game;
use crate::log::summons_log;

/// `ChrIns+0x6c`: the character's team type, one byte.
const CHR_TEAM_TYPE: usize = 0x6c;

/// Companions this DLL built, with the handle each held when it was built, so a slot the game
/// has since reused for another character is never written to.
static COMPANIONS: Mutex<Vec<(usize, u64)>> = Mutex::new(Vec::new());

fn player_team() -> Option<u8> {
    let player = game::main_player()?;
    // SAFETY: fault-tolerant read.
    unsafe { safe_read_u8(player + CHR_TEAM_TYPE) }
}

/// Write the player's team onto `chr`, which the caller has just proven is the character it
/// tracked. Returns the team it replaced, when it replaced one.
fn align(chr: usize, team: u8) -> Option<u8> {
    // SAFETY: fault-tolerant read.
    let was = unsafe { safe_read_u8(chr + CHR_TEAM_TYPE) }?;
    if was == team {
        return None;
    }
    // SAFETY: `chr` still holds the handle it was built with, so this is a live `ChrIns`, and
    // `+0x6c` is a byte inside it.
    unsafe { core::ptr::write_volatile((chr + CHR_TEAM_TYPE) as *mut u8, team) };
    Some(was)
}

/// Track a companion just built for config slot `slot`, and put it on the player's team now.
pub(crate) fn adopt(slot: u8, chr: usize) {
    let Some(handle) = game::chr_handle(chr) else {
        return;
    };
    COMPANIONS
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .push((chr, handle));
    let Some(team) = player_team() else {
        summons_log(format_args!(
            "team: companion {slot}: no main player to copy a team from; retried every frame"
        ));
        return;
    };
    match align(chr, team) {
        Some(was) => summons_log(format_args!(
            "team: companion {slot}: 0x{chr:x} built on team {was}, moved to the player's team {team}"
        )),
        None => summons_log(format_args!(
            "team: companion {slot}: 0x{chr:x} already on the player's team {team}"
        )),
    }
}

/// The tracked companions still holding the handle they were built with, as `ChrIns*`.
pub(crate) fn companions() -> Vec<usize> {
    COMPANIONS
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .filter(|&&(chr, handle)| game::chr_handle(chr) == Some(handle))
        .map(|&(chr, _)| chr)
        .collect()
}

/// Re-apply the player's team to every tracked companion and forget the ones that are gone.
/// Called from the game task every frame.
pub(crate) fn tick() {
    let mut companions = COMPANIONS.lock().unwrap_or_else(|e| e.into_inner());
    if companions.is_empty() {
        return;
    }
    companions.retain(|&(chr, handle)| game::chr_handle(chr) == Some(handle));
    let Some(team) = player_team() else {
        return;
    };
    for &(chr, _) in companions.iter() {
        if let Some(was) = align(chr, team) {
            summons_log(format_args!(
                "team: 0x{chr:x} had drifted to team {was}; back on the player's team {team}"
            ));
        }
    }
}
