//! The ids `MsgRepository::GetAndFormat` was most recently asked for, and who asked.
//!
//! A dialog fetches its text through that one call in the frames before the box is built, so the
//! detour that records ids here names the message that is about to appear. It is what identified
//! `GRD` 110000 as the box behind the native first Quit row -- a message previously known only from
//! a screenshot of its English text, which no later run can check and no oracle can read.
//!
//! It lives in this crate rather than in `er-quickload` because its only writer is the Save Game
//! row's text detour, and that detour is moving here so any host can carry the row.

use std::sync::atomic::{AtomicUsize, Ordering};

/// Reserved for a slot nothing has written yet. `MsgRepository::GetAndFormat` treats anything
/// below 1 as "no message" (see the guard quoted in `er-invasion-warp`'s place-name notes), so 0
/// is not a real id under any fmg.
const RECENT_MSG_TEXT_ID_EMPTY: usize = 0;
/// How many ids to keep. A dialog pulls a title, a body and its button captions, and the frames
/// around it pull a few of their own, so a dozen covers one box with room either side.
const RECENT_MSG_TEXT_ID_SLOTS: usize = 12;
/// The ids `MsgRepository::GetAndFormat` was most recently asked for.
///
/// A dialog fetches its text through that call in the frames before the box itself is built, so
/// recording the ids there and printing them beside each `msgbox-builder` line names the message
/// that is about to appear. The warning this was added for -- the one the title shows about the
/// previous session -- has never been identified by anything but a screenshot of its English text,
/// which no later run can check and no oracle can read.
static RECENT_MSG_TEXT_IDS: [AtomicUsize; RECENT_MSG_TEXT_ID_SLOTS] =
    [const { AtomicUsize::new(RECENT_MSG_TEXT_ID_EMPTY) }; RECENT_MSG_TEXT_ID_SLOTS];
/// Total ids recorded. Also the write cursor, modulo the slot count.
static RECENT_MSG_TEXT_ID_CURSOR: AtomicUsize = AtomicUsize::new(0);

/// Record one id the game asked `MsgRepository::GetAndFormat` for, with the fmg it came from.
///
/// The id alone does not name a message: the same number means different things in different
/// fmgs, and the call carries the fmg's short tag (`GRD`, `GRMT`, `GRHK`, ...) as a wide string.
/// One `usize` read covers its first four characters -- `[b0, 0, b1, 0, b2, 0, b3, 0]` -- so the
/// tag packs into the top half of the same atomic as the id. An unreadable pointer records the id
/// with no tag rather than nothing.
///
/// # Safety
///
/// `abbrev` is the pointer the game passed; it is read fault-safe and may be null or garbage.
///
/// Game target only: the fault-safe reader it dereferences `abbrev` through lives in
/// `er-game-base`, which this crate links on `cfg(windows)`. The ring buffer and
/// [`recent_msg_text_ids`] below are pure and stay on both targets, so the formatting this
/// module exists for is still host-testable.
#[cfg(windows)]
pub unsafe fn note_msg_text_id(text_id: i32, abbrev: usize) {
    if text_id < 1 {
        return;
    }
    let wide = unsafe { er_game_base::mem::safe_read_usize(abbrev) }.unwrap_or(0);
    let mut tag: u32 = 0;
    for character in 0..4 {
        let byte = (wide >> (character * 16)) as u8;
        if !byte.is_ascii_alphanumeric() && byte != b'_' {
            break;
        }
        tag |= u32::from(byte) << (character * 8);
    }
    let packed = (tag as usize) << 32 | (text_id as u32) as usize;
    let slot = RECENT_MSG_TEXT_ID_CURSOR.fetch_add(1, Ordering::SeqCst) % RECENT_MSG_TEXT_ID_SLOTS;
    RECENT_MSG_TEXT_IDS[slot].store(packed, Ordering::SeqCst);
}

/// The recorded ids, oldest first, for one log line.
///
/// The only writer is `note_msg_text_id`, called from the Save Game row's
/// `MsgRepository::GetAndFormat` detour, so a build without `save-game-row` records nothing. It says
/// that here rather than printing an empty list, because an empty list reads as "the game formatted
/// no message", which would be a lie about the one line this field exists to answer.
pub fn recent_msg_text_ids() -> String {
    let written = RECENT_MSG_TEXT_ID_CURSOR.load(Ordering::SeqCst);
    let first = written.saturating_sub(RECENT_MSG_TEXT_ID_SLOTS);
    let mut out = String::new();
    for index in first..written {
        let packed = RECENT_MSG_TEXT_IDS[index % RECENT_MSG_TEXT_ID_SLOTS].load(Ordering::SeqCst);
        if packed == RECENT_MSG_TEXT_ID_EMPTY {
            continue;
        }
        if !out.is_empty() {
            out.push(' ');
        }
        let tag = (packed >> 32) as u32;
        for character in 0..4 {
            let byte = (tag >> (character * 8)) as u8;
            if byte == 0 {
                break;
            }
            out.push(char::from(byte));
        }
        out.push(':');
        out.push_str(&(packed as u32).to_string());
    }
    out
}
