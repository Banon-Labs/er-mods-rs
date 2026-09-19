//! Refuse to show a network message the game has no words for.
//!
//! # What the player sees without this
//!
//! A popup reading `?NetworkMessage?`. That is not a message: it is what `CS::MsgRepository` hands
//! back when a network-message id has no entry in its FMG -- the category name it was asked to
//! look in, wrapped in question marks, standing in for text that does not exist. Reported by the
//! player on 2026-09-18 while invading, three times in one session.
//!
//! # The id, measured
//!
//! `2621201` (`0x27FF11`), caught live on run `br-20260918-174500-8024` by
//! `scripts/frida/who-shows-network-message.js`, which hooks the lookup rather than the display:
//!
//! ```text
//!   {'tag':'network-message-MISSING','id':2621201,'text':'?NetworkMessage?',
//!    'stack':['eldenring.exe+0x5f7c7c','eldenring.exe+0x74769f','eldenring.exe+0x746393', ...]}
//! ```
//!
//! Every shipped msgbnd -- base, `menu_dlc01`, `menu_dlc02` -- holds `%null%` at that id, and its
//! neighbour `2621200` is `You died. Returning to your world.`
//!
//! # Why the display and not the lookup
//!
//! `CS::MenuString` is `{ wchar_t *rawString; DLString<wchar_t> }`: text, never an id. So the
//! display funnel cannot know which id it is showing, and the lookup cannot know whether anything
//! will show it -- but only the display decides whether a popup happens. Cutting there also means
//! one test covers every id that ever comes back empty, rather than a list of numbers that grows
//! every time the game gains a message.
//!
//! # Why this is safe to drop
//!
//! A `?`-prefixed message is one the game had no words for, so nothing is hidden from the player
//! that they could have read. The refusal returns `false` without calling the original, which is
//! the same answer the game itself gives when `GLOBAL_CSFeMan` is null -- a shape every caller
//! already handles.
//!
//! # What this does not fix
//!
//! Whatever computes `2621201`. If that is an off-by-one against the death notice at `2621200` --
//! a strong reading of the neighbouring text, and not a measured one -- then the player is also
//! losing a notice they were meant to see, and the arithmetic at `eldenring.exe+0x5f7c7c` is still
//! the thing to read. This module removes a popup that says nothing; it does not restore one that
//! should have said something.
//!
//! # Proven before it was written
//!
//! `scripts/frida/suppress-null-network-message.js` is this same cut, as a Frida `replace`, run
//! against `br-20260918-174500-8024`: one `dropped-null-network-message` line carrying
//! `text='?NetworkMessage?'`, and the player confirmed the popup did not appear.

#[cfg(windows)]
use core::sync::atomic::{AtomicUsize, Ordering};

/// `CS::CSFeMan::ShowNetworkMessage(CSFeManImp*, ushort priority, bool forcePlay, MenuString*,
/// bool)` -- the single funnel every network message goes through on its way to the screen.
///
/// Its whole body is `SummonMsgData::SummonMsgData(&local, priority, forcePlay, message, x)` then
/// `SummonMsgQueue::AddEntry(&fe->summonMsgQueue, &local)`, so a message that does not reach it
/// does not become a popup.
///
/// The rva is 1.16.2, like every seam here, and the build gate translates it: on the installed
/// 1.17 build this function is at `0x14076f190`, which `scripts/map-rvas-1162-to-1170.py` found as
/// a unique 47-byte signature match with 26 fixed bytes. Writing that 1.17 address here instead
/// was tried first and refused at runtime on `br-20260918-180825-3ece` -- `no verified detour
/// mapping for the running build` -- because `verify_seam` reads a seam's rva as 1.16.2 and looks
/// the pair up in the ledger rather than trusting a number. The prologue is the 1.17 one, since
/// the translated address is where those bytes are read back from.
///
/// Five arguments, so this seam must not ride `er_hook`'s four-argument union dispatcher -- see
/// the argument-count trap in `map_seams`. It gets its own typed trampoline instead.
#[cfg(windows)]
const SHOW_NETWORK_MESSAGE: crate::map_seams::MapSeam = crate::map_seams::MapSeam {
    name: "CS::CSFeMan::ShowNetworkMessage",
    rva: 0x0076_e310,
    prologue: &[
        0x40, 0x53, 0x48, 0x81, 0xec, 0xa0, 0x00, 0x00, 0x00, 0x48, 0xc7, 0x44, 0x24, 0x30,
    ],
    arg_count: 5,
};

/// The trampoline, once the detour is in.
#[cfg(windows)]
static ORIG_SHOW_NETWORK_MESSAGE: AtomicUsize = AtomicUsize::new(0);

/// How many placeholder messages have been refused, for the log line and for telemetry.
#[cfg(windows)]
static DROPPED: AtomicUsize = AtomicUsize::new(0);

/// The trampoline's own shape. Five arguments, returned `bool`.
#[cfg(windows)]
type ShowNetworkMessageFn = unsafe extern "system" fn(usize, u16, bool, usize, bool) -> bool;

/// `CS::MenuString::rawString` -- the wide string the message carries, at offset 0 of a 56-byte
/// two-field type whose other field is the `DLString` that owns it.
#[cfg(windows)]
const MENU_STRING_RAW_OFFSET: usize = 0;

/// How far into the text to look for the marker. A real message can be long; the marker is at the
/// very front, so one code unit would do and a handful costs nothing.
#[cfg(windows)]
const PLACEHOLDER_PROBE_UNITS: usize = 1;

/// `?` -- the first code unit of a missing entry, which `CS::MsgRepository` writes around the
/// category name it failed to find in.
#[cfg(windows)]
const PLACEHOLDER_MARKER: u16 = b'?' as u16;

/// Whether this message is the placeholder rather than text.
///
/// Fault-closed in the direction that shows the message: an unreadable string is passed through,
/// because refusing a message we could not read would hide real notices on any layout surprise.
#[cfg(windows)]
fn is_placeholder(message: usize) -> bool {
    if message == 0 {
        return false;
    }
    // SAFETY: one pointer read at offset 0 of the `MenuString` the game just passed, fault-closed.
    let Some(raw) =
        (unsafe { er_game_base::mem::safe_read_usize(message + MENU_STRING_RAW_OFFSET) })
    else {
        return false;
    };
    if raw == 0 {
        return false;
    }
    // SAFETY: the first code unit of the wide string that pointer names, fault-closed.
    let Some(first) = (unsafe { er_game_base::mem::safe_read_u16(raw) }) else {
        return false;
    };
    let _ = PLACEHOLDER_PROBE_UNITS;
    first == PLACEHOLDER_MARKER
}

/// Drop a message with no text; pass everything else to the game untouched.
///
/// # Safety
///
/// Called by MinHook in place of the game's function, on whichever thread raised the message.
#[cfg(windows)]
unsafe extern "system" fn show_network_message_entry(
    fe: usize,
    priority: u16,
    force_play: bool,
    message: usize,
    tail: bool,
) -> bool {
    let orig = ORIG_SHOW_NETWORK_MESSAGE.load(Ordering::SeqCst);
    if orig == 0 {
        return false;
    }
    if is_placeholder(message) {
        let dropped = DROPPED.fetch_add(1, Ordering::SeqCst) + 1;
        // Said on change of count rather than once: a popup that returns is a popup that returns,
        // and a latch here would report the first and hide the rest.
        crate::standalone_log(format_args!(
            "null-network-message: refused a network message with no text (drop #{dropped}, \
             priority {priority}). The game looked up an id its own message tree has no entry for \
             -- measured as 2621201 on run br-20260918-174500-8024, which every shipped msgbnd \
             holds as `%null%` -- so what would have appeared is the literal `?NetworkMessage?`. \
             Nothing readable was hidden."
        ));
        return false;
    }
    // SAFETY: the trampoline stored for this exact target, called with its own arguments.
    unsafe {
        core::mem::transmute::<usize, ShowNetworkMessageFn>(orig)(
            fe, priority, force_play, message, tail,
        )
    }
}

/// How many placeholder popups have been refused this session.
#[cfg(windows)]
#[must_use]
pub fn dropped() -> usize {
    DROPPED.load(Ordering::SeqCst)
}

/// Host-side stub: nothing is hooked off the target.
#[cfg(not(windows))]
#[must_use]
pub fn dropped() -> usize {
    0
}

/// Arm the refusal. Idempotent, and refuses rather than guesses on an unrecognised build.
///
/// # Safety
///
/// Game task thread, after `CSTaskImp` resolved.
#[cfg(windows)]
pub unsafe fn install() -> bool {
    static REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);

    if ORIG_SHOW_NETWORK_MESSAGE.load(Ordering::SeqCst) != 0 {
        return true;
    }
    // SAFETY: game task thread; the seam checks its own prologue and refuses otherwise.
    let address = match unsafe { crate::map_seams::verify_seam(&SHOW_NETWORK_MESSAGE) } {
        Ok(address) => address,
        Err(error) => {
            if REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "null-network-message: refused {} -- {error}. Network messages are untouched, \
                     so a message the game has no text for will show as `?NetworkMessage?` the \
                     way it did before this module existed. Printed once.",
                    SHOW_NETWORK_MESSAGE.name
                ));
            }
            return false;
        }
    };
    let hook = match unsafe {
        er_hook::MhHook::new(
            address as *mut core::ffi::c_void,
            show_network_message_entry as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            crate::standalone_log(format_args!(
                "null-network-message: failed to create the detour @0x{address:x} -- {status:?}. \
                 The address resolved and its prologue matched."
            ));
            return false;
        }
    };
    ORIG_SHOW_NETWORK_MESSAGE.store(hook.trampoline() as usize, Ordering::SeqCst);
    // SAFETY: the hook was created above; enabling is MinHook's own queued path.
    if unsafe { hook.queue_enable() }.is_err() {
        ORIG_SHOW_NETWORK_MESSAGE.store(0, Ordering::SeqCst);
        return false;
    }
    // SAFETY: applies the queue this function just added to.
    match unsafe { er_hook::MH_ApplyQueued() } {
        er_hook::MH_STATUS::MH_OK => {
            crate::standalone_log(format_args!(
                "null-network-message: armed on {} @0x{address:x}. A network message whose text \
                 came back as the `?...?` placeholder is refused instead of shown; everything \
                 with words in it is passed through untouched.",
                SHOW_NETWORK_MESSAGE.name
            ));
            true
        }
        status => {
            ORIG_SHOW_NETWORK_MESSAGE.store(0, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "null-network-message: MH_ApplyQueued refused the detour -- {status:?}"
            ));
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The seam takes five arguments, so it must never be given to the four-argument union
    /// dispatcher -- which would silently drop the trailing `bool` and the `MenuString` pointer's
    /// neighbours. `map_seams` records the count precisely so this is a check rather than a memory.
    #[cfg(windows)]
    #[test]
    fn the_seam_is_too_wide_for_the_union_dispatcher() {
        assert!(!SHOW_NETWORK_MESSAGE.fits_union_dispatcher());
    }

    /// Nothing has been dropped before anything runs, so a telemetry read at boot is not a lie.
    #[test]
    fn nothing_is_dropped_before_the_hook_is_armed() {
        assert_eq!(dropped(), 0);
    }
}
