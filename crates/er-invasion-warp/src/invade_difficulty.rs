//! The live half of the bracket picker: what is selected, which brackets the player may pick, and
//! when the selection applies.
//!
//! # Why this is in memory and not in the config file
//!
//! Two reasons, and the player gave the first directly on 2026-09-18: "this runtime config that
//! doesn't persist". A bracket is a decision about the fight you want in the next ten minutes, not
//! a property of your installation, and every restart should start you back at Seamless's own
//! behaviour.
//!
//! The second reason is the shape of a bug this repo already paid for. `widen_to_anywhere` and
//! `widen_band_when_nearby_exhausted` were config keys that could decide how far a search reached,
//! and on 2026-09-18 a file still carrying one from a previous week turned a `Nearby only` search
//! into a whole-population one and dropped the player into two strangers' worlds. Both keys were
//! deleted. A setting that never reaches a file cannot go stale in one, cannot be hand-edited into
//! disagreeing with what the panel shows, and cannot outlive the session that asked for it -- so
//! this is reachable from the settings panel and from nowhere else.
//!
//! # When it applies
//!
//! Only during the far half of `Both near and far`, which begins at exactly one place:
//! `lobby_preflight::hand_over_when_the_neighbourhood_is_empty` calling
//! `local_invasion_filter::hand_off_to_seamless`. Before that the search is this DLL's own, aimed
//! at a ring of tiles at the player's own band; after it, every query is Seamless's, unfiltered,
//! and the band field is the only thing left that decides who can answer.
//!
//! [`in_far_half`] is a latch rather than a question asked of the ring, because the two are read
//! on different sides of one query round. Seamless adds its string filters and only then calls
//! `RequestLobbyList`, where `hunt_target` runs -- so a filter asking the ring what it decided this
//! round would be reading last round's answer. The handover happens on the game task, once, and the
//! latch it sets is true for every query that follows it.
//!
//! # Where the player's own bracket comes from
//!
//! Read from `PlayerGameData`, not latched off the wire. The panel has to grey out the brackets
//! below the player's own on the frame it opens, which is typically long before any query has gone
//! out, so a value learned from a search is a value that arrives too late to be useful.
//!
//! The chain, and the one trap in it:
//!
//! ```text
//! eldenring.exe + GAME_DATA_MAN_GLOBAL_RVA  ->  GameDataMan*
//! GameDataMan   + 0x08                      ->  PlayerGameData*
//! PlayerGameData + 0x68                     ->  rune level, i32
//! PlayerGameData + 0xe2                     ->  matching_weapon_level, u8
//! ```
//!
//! The rva is a 1.16.2 one and `er_game_base::mem::game_data_addr` is what translates it for the
//! running build -- reading it raw on 1.17.1 lands on `0xfffffffffffffff7` and faults. Measured
//! 2026-09-18 by `scripts/frida/own-bracket-from-pgd.js`, which hit exactly that before it was
//! given the mapped address, then agreed with the wire: it predicted `0_0` from `RL9 +2` and the
//! query Seamless sent a moment later carried `0_0`.

#![cfg(windows)]

use core::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_invasion_warp_core::invade_difficulty::{
    BracketChoice, MAX_LEVEL_BAND, MAX_WEAPON_BAND, level_band, weapon_band,
};

/// Stored as `band + 1` so that zero means "not picked", which is what an atomic starts at and
/// what a fresh session must hold.
const NOTHING_PICKED: usize = 0;

/// The level bracket the player picked, offset by one.
static LEVEL_PICK: AtomicUsize = AtomicUsize::new(NOTHING_PICKED);

/// The weapon bracket the player picked, offset by one.
static WEAPON_PICK: AtomicUsize = AtomicUsize::new(NOTHING_PICKED);

/// Whether the search has reached the far half, where the selection applies.
static FAR_HALF: AtomicBool = AtomicBool::new(false);

fn read_pick(slot: &AtomicUsize, ceiling: u32) -> Option<u32> {
    let stored = slot.load(Ordering::SeqCst);
    let band = u32::try_from(stored.checked_sub(1)?).ok()?;
    (band <= ceiling).then_some(band)
}

fn store_pick(slot: &AtomicUsize, band: Option<u32>) {
    slot.store(
        band.map_or(NOTHING_PICKED, |band| band as usize + 1),
        Ordering::SeqCst,
    );
}

/// The bracket pair currently selected.
#[must_use]
pub fn current() -> BracketChoice {
    BracketChoice {
        level: read_pick(&LEVEL_PICK, MAX_LEVEL_BAND),
        weapon: read_pick(&WEAPON_PICK, MAX_WEAPON_BAND),
    }
}

/// Pick a level bracket, or `None` to go back to the player's own.
pub fn pick_level(band: Option<u32>) {
    store_pick(&LEVEL_PICK, band.filter(|band| *band <= MAX_LEVEL_BAND));
    announce();
}

/// Pick a weapon bracket, or `None` to go back to the player's own.
pub fn pick_weapon(band: Option<u32>) {
    store_pick(&WEAPON_PICK, band.filter(|band| *band <= MAX_WEAPON_BAND));
    announce();
}

fn announce() {
    let choice = current();
    crate::standalone_log(format_args!(
        "invade-bracket: the far half will ask for {}. Held in memory only: it is gone at the next \
         launch and there is no line in the config file that could disagree with it.",
        choice.describe()
    ));
}

/// `GameDataMan` to `PlayerGameData`.
const PLAYER_GAME_DATA_OFFSET: usize = er_game_base::rva::GAME_DATA_MAN_PLAYER_GAME_DATA_08_OFFSET;

/// `PlayerGameData::level`.
///
/// Computed by `offset_of!` against `../fromsoftware-rs`'s `#[repr(C)]` definition rather than
/// typed as a literal, which is also what `er_game_base::pgd` asserts the value of: a field that
/// moves moves this with it instead of leaving a stale number that reads a neighbour.
const LEVEL_OFFSET: usize = core::mem::offset_of!(eldenring::cs::PlayerGameData, level);

/// `PlayerGameData::matching_weapon_level`.
const WEAPON_OFFSET: usize =
    core::mem::offset_of!(eldenring::cs::PlayerGameData, matching_weapon_level);

/// The highest rune level the game allows, and the highest standard reinforcement.
///
/// Used to refuse an implausible read rather than to clamp one. A level of `0x4d0f0000` is the
/// chain landing somewhere it should not, and turning that into band 8 would hand the panel a
/// confident wrong answer about who the player may invade.
const MAX_RUNE_LEVEL: i32 = 713;
const MAX_WEAPON_UPGRADE: u8 = 25;

/// The bracket pair this character is in, read live.
///
/// `None` when no character is loaded or the read is not plausible, and the panel greys nothing in
/// that case -- offering every bracket is a worse answer than offering none, but silently greying
/// against a garbage band would be worse than both.
#[must_use]
pub fn own_bands() -> Option<(u32, u32)> {
    let base = er_game_base::mem::game_module_base().ok()?;
    // `game_data_addr` is the translation layer, and skipping it is the whole trap: the rva is a
    // 1.16.2 one, every `.data` global moved on 1.17, and `base + rva` read raw lands on a
    // pointer into the image rather than on `GameDataMan`.
    let slot = er_game_base::mem::game_data_addr(
        base,
        er_game_base::rva::GAME_DATA_MAN_GLOBAL_RVA,
        "GAME_DATA_MAN_GLOBAL_RVA",
    );
    if slot == 0 {
        return None;
    }
    let global = unsafe { er_game_base::mem::safe_read_usize(slot) }?;
    if global == 0 {
        return None;
    }
    let pgd = unsafe { er_game_base::mem::safe_read_usize(global + PLAYER_GAME_DATA_OFFSET) }?;
    if pgd == 0 {
        return None;
    }
    let level = unsafe { er_game_base::mem::safe_read_i32(pgd + LEVEL_OFFSET) }?;
    let upgrade = unsafe { er_game_base::mem::safe_read_u8(pgd + WEAPON_OFFSET) }?;
    if level <= 0 || level > MAX_RUNE_LEVEL || upgrade > MAX_WEAPON_UPGRADE {
        return None;
    }
    Some((
        level_band(u32::try_from(level).ok()?),
        weapon_band(u32::from(upgrade)),
    ))
}

/// Record that the near half is over and the far half has started.
///
/// Called from `hand_off_to_seamless`, which is the single place that hands a running search back
/// to Seamless. Announced once per transition rather than once per process: a player who invades
/// four times in an evening should see the setting take effect four times, and a once-per-session
/// line cannot tell the second handover from a handover that did not happen.
pub fn enter_far_half() {
    if FAR_HALF.swap(true, Ordering::SeqCst) {
        return;
    }
    let choice = current();
    if choice.is_own() {
        crate::standalone_log(format_args!(
            "invade-bracket: the near half is over and no bracket is picked, so the far half asks \
             for this character's own and nothing here rewrites the query."
        ));
        return;
    }
    crate::standalone_log(format_args!(
        "invade-bracket: the near half is over, so the far half now asks for {}. Seamless compares \
         this field for equality, so from here only hosts in that bracket can answer any query \
         this search sends.",
        choice.describe()
    ));
}

/// Leave the far half, because this search is over.
///
/// Every path that ends a search calls this. A latch left set would apply the selection to the
/// opening query of the next invasion -- the near half, at a band the player never climbed to --
/// with nothing on screen to say why nobody nearby could be found.
pub fn leave_far_half() {
    FAR_HALF.store(false, Ordering::SeqCst);
}

/// Whether the running search has reached its far half.
#[must_use]
pub fn in_far_half() -> bool {
    FAR_HALF.load(Ordering::SeqCst)
}

/// The band this query should ask for instead of `own`, or `None` to send Seamless's own value.
///
/// # Why this binds to the whole search and not only to the far half
///
/// It was gated on [`in_far_half`] until 2026-09-18, on the reasoning that the near half is the
/// ring at the player's own band and the bracket belongs to the handover. Reported the same
/// evening: "it was my feature not working" -- the player had `RL71-100` selected and invaded
/// `RL20` characters anyway.
///
/// Run `br-20260919-004438-566d` timed it. They cancelled their first search (line 609), picked
/// `RL71-100` (631, 636), started a second one (644) and landed a match (663), with no
/// `the near half is over` line between the last two -- so that search never reached its far half
/// and the gate here returned `None` for every query it sent. The panel went on showing the pick.
///
/// Measured on the wire rather than read off the source, because a gate can have a caller nobody
/// remembered: `scripts/frida/near-half-ignores-the-bracket.js` picked bands 3 and 1 through the
/// crate's own export, started a plain search, and the only band that left the process was `1_0`
/// -- the character's own -- against a pick of `3_1`.
///
/// The near/far split decides where a search looks, which is the row the finger asks for. The
/// bracket decides who it may find, and the player named that. A setting that silently applies to
/// part of a search is worse than one that does not exist, because the panel keeps promising it.
///
/// This does not make the bracket a second widening axis. `failed_cycle`'s rule -- a failed cycle
/// may step the place or climb the band, never both -- governs the ladder, which is a guess this
/// module walks while nobody has been found. A pick is not a guess: it does not move on a failed
/// cycle and it takes no rung from anything.
///
/// `None` for a selection that resolves to the player's own bracket, and for a value that is not
/// band-shaped.
#[must_use]
pub fn band_for(own: &str) -> Option<String> {
    current().band_for(own)
}
