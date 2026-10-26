//! This player's companions' outfits, sent to every other player through the game's own packet,
//! so a player without this DLL sees them dressed too.
//!
//! The vanilla Mimic Tear shows the summoner's gear to the other players through packet 0x29
//! subtype 4: `CS::PlayerIns::CopyAnother` (2.7.2.0 `0x140654ac0`) fills a `PcInfo` (level and
//! stats, weapons with their ashes, armour, talismans, quick items, spells, face, voice) and
//! broadcasts it, and each other player's handler (`0x1404e8080`) applies it to its copy of the
//! character with `PopulateFromPcInfo`. The handler checks only that the copy is an NPC whose
//! `PlayerGameData+0xab8` is set, which the copy has because the request named a sync-target
//! `CharaInitParam` row ([`er_npc_summons_core::mimic::SYNC_CHARA_INIT`]). Nothing names the
//! sender's DLL, so this needs none on the other side. The name is not in a `PcInfo`.
//!
//! `CopyAnother(chr, chr)` re-applies a companion's own outfit and broadcasts it. It runs a few
//! frames after the companion is built, because `BuddyGenerator` broadcasts the summon itself
//! (packet 78) only after `CreateSummonChr` returns and an outfit for a character the other player
//! has not built yet is dropped. It runs again a few seconds later, in case the summon reached
//! them late, and twice more after anyone joins the session, since a joiner has no copy until the
//! game sends them the summon. Static RE only, bd
//! `npc-pcinfo-packet41-gear-sync-to-vanilla-peers-2720-2026-10-26`; whether Seamless Co-op
//! relays packet 0x29 is unmeasured.

#![cfg(windows)]

use std::sync::Mutex;
use std::sync::atomic::{AtomicU8, Ordering};

use er_game_base::mem::{game_module_base, read_bytes, safe_read_u8, safe_read_usize};

use crate::addr::{COPY_ANOTHER_PROLOGUE, COPY_ANOTHER_RVA, pgd_sync};
use crate::game;
use crate::log::summons_log;

/// Frames after a build before the first send: `BuddyGenerator` has returned and packet 78 left.
const FIRST_SEND_FRAMES: u32 = 2;
/// The second send, for a summon that reached another player late.
const RESEND_FRAMES: u32 = 180;
/// After someone joins: once their world has had time to build the companions, and once more.
const JOIN_SEND_FRAMES: [u32; 2] = [240, 720];

/// `ChrIns+0x580`: the character's `PlayerGameData`.
const CHR_PLAYER_GAME_DATA: usize = 0x580;

type CopyAnotherFn = unsafe extern "system" fn(usize, usize);

struct Entry {
    slot: u8,
    chr: usize,
    handle: u64,
    /// Frames at which to send, in any order, and why, for the log.
    due: Vec<(u32, &'static str)>,
}

struct State {
    frame: u32,
    entries: Vec<Entry>,
    players: usize,
}

static STATE: Mutex<State> = Mutex::new(State {
    frame: 0,
    entries: Vec::new(),
    players: 0,
});

/// 0 unchecked, 1 `CopyAnother` matches its pinned opening, 2 it does not.
static CHECKED: AtomicU8 = AtomicU8::new(0);

fn with<T>(f: impl FnOnce(&mut State) -> T) -> T {
    let mut guard = STATE.lock().unwrap_or_else(|e| e.into_inner());
    f(&mut guard)
}

/// A companion this player summoned was just built at `chr` for config `slot`.
pub(crate) fn companion_built(slot: u8, chr: usize) {
    let Some(handle) = game::chr_handle(chr) else {
        return;
    };
    with(|s| {
        let now = s.frame;
        s.entries.retain(|e| e.handle != handle);
        s.entries.push(Entry {
            slot,
            chr,
            handle,
            due: vec![
                (now + FIRST_SEND_FRAMES, "after its summon"),
                (now + RESEND_FRAMES, "a few seconds after its summon"),
            ],
        });
    });
}

/// `CopyAnother`, once its opening has been compared with the pinned bytes.
fn copy_another() -> Option<CopyAnotherFn> {
    let entry = game_module_base().ok()? + COPY_ANOTHER_RVA;
    match CHECKED.load(Ordering::Acquire) {
        1 => {}
        2 => return None,
        _ => {
            let mut bytes = [0u8; COPY_ANOTHER_PROLOGUE.len()];
            // SAFETY: a fault-tolerant read of code bytes.
            let read = unsafe { read_bytes(entry, &mut bytes) };
            if !read || bytes != COPY_ANOTHER_PROLOGUE {
                CHECKED.store(2, Ordering::Release);
                summons_log(format_args!(
                    "gear-sync: CopyAnother at 0x{entry:x} reads {bytes:02x?}, not its 2.7.2.0 \
                     opening; companions' outfits are not sent to other players"
                ));
                return None;
            }
            CHECKED.store(1, Ordering::Release);
        }
    }
    // SAFETY: the 2.7.2.0 `CopyAnother(PlayerIns* to, PlayerIns* from)`, checked by its bytes.
    Some(unsafe { core::mem::transmute::<usize, CopyAnotherFn>(entry) })
}

/// Send one companion's outfit. Runs on the game task.
fn send(slot: u8, chr: usize, players: usize, why: &str) {
    let Some(copy) = copy_another() else {
        return;
    };
    // SAFETY: fault-tolerant reads off a character whose handle was just confirmed.
    let pgd = unsafe { safe_read_usize(chr + CHR_PLAYER_GAME_DATA) }.filter(|&p| p != 0);
    let Some(pgd) = pgd else {
        summons_log(format_args!(
            "gear-sync: companion {slot} 0x{chr:x} has no PlayerGameData; outfit not sent"
        ));
        return;
    };
    let target = unsafe { safe_read_u8(pgd + pgd_sync::SYNC_TARGET) }.unwrap_or(0);
    if target == 0 {
        summons_log(format_args!(
            "gear-sync: companion {slot} 0x{chr:x} is not a sync target (its row's sync bit was \
             not set), so the game would not send its outfit; not sent"
        ));
        return;
    }
    // SAFETY: the game task; `chr` is this player's own companion, which `CopyAnother` accepts
    // as its source (an NPC owned here) and re-applies to itself.
    unsafe { copy(chr, chr) };
    let applied = unsafe { safe_read_u8(pgd + pgd_sync::PC_INFO_APPLIED) }.unwrap_or(0);
    if applied != 0 {
        summons_log(format_args!(
            "gear-sync: companion {slot} 0x{chr:x}: outfit broadcast as packet 0x29 ({why}; {players} \
             other player(s) in the session)"
        ));
    } else {
        summons_log(format_args!(
            "gear-sync: companion {slot} 0x{chr:x}: CopyAnother ran but PlayerGameData+0xab9 is \
             still clear, so it did not broadcast ({why})"
        ));
    }
}

/// One frame on the game task.
pub(crate) fn tick() {
    let players = crate::net::session_peers(0).len();
    let due = with(|s| {
        s.frame = s.frame.wrapping_add(1);
        let now = s.frame;
        s.entries
            .retain(|e| game::chr_handle(e.chr) == Some(e.handle));
        let joined = players > s.players;
        s.players = players;
        let mut out = Vec::new();
        for entry in &mut s.entries {
            if joined {
                entry.due.extend(
                    JOIN_SEND_FRAMES
                        .iter()
                        .map(|d| (now + d, "a player joined")),
                );
            }
            if let Some(&(_, why)) = entry.due.iter().find(|&&(at, _)| at <= now) {
                entry.due.retain(|&(at, _)| at > now);
                out.push((entry.slot, entry.chr, why));
            }
        }
        out
    });
    // Sent outside the lock: `CopyAnother` runs game code that may build, apply and broadcast.
    for (slot, chr, why) in due {
        send(slot, chr, players, why);
    }
}
