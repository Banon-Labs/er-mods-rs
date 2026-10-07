//! A free cursor and a still camera while the picker is open.
//!
//! In the world the game holds the cursor for the camera, and the only native way to get one is
//! to open one of its menus. Both halves of that come from one predicate, "a menu has the mouse",
//! `bool(CSMenuManImp*)` (1.16.2 `FUN_140765800`, 1.17.1 `0x140766650`). It reads
//! `CSMenuMan+0x1a` and the shown-menu bitmap at `+0x1c`, and it has three callers:
//!
//! * the cursor gate (1.17.1 `0x140e20490`), `predicate && IsGameInForeground`, which decides
//!   whether `CSMouseMan` shows the cursor and stops clipping and re-centring it;
//! * the mouse X and Y axis readers (`0x140e2b360`, `0x140e2b450`), which return 0 while it is
//!   true; `ChrCam`'s input update and the mouse-flick lock-on switch read only those.
//!
//! So the detour answers true while the picker is open and asks the game otherwise. Foreground
//! handling is untouched: an unfocused game still keeps its cursor rules. Mouse buttons, keyboard
//! actions and the right stick are gated by direct reads of `CSMenuMan+0x1c`, not by this; the
//! left button is blanked over the picker by `er-dinput-suppress-core`.
//!
//! Measured live 2026-10-06 with `scripts/frida/npc-summons-gaps-probe.js`: the predicate answers
//! 0 in the world and 1 while a game menu is open. 1.17 added a clause to its body, so the
//! 1.16.2-to-1.17 ledger refuses the pair; it is hooked by its 1.17.1 address on 1.17.1 only, after
//! its opening bytes are checked, through the runtime-derived installer.

#![cfg(windows)]

use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::game_build::{MAPPED_FILE_VERSION_1171, game_file_version};
use er_game_base::mem::{game_module_base, read_bytes};
use er_hook::UnionFn;

use crate::addr::{MENU_HAS_MOUSE_1171_PROLOGUE, MENU_HAS_MOUSE_1171_RVA};
use crate::log::summons_log;

static ORIG: AtomicUsize = AtomicUsize::new(0);
/// Calls answered "a menu has the mouse" for the picker.
static FORCED: AtomicUsize = AtomicUsize::new(0);

/// The predicate, in the union's four-argument shape (it takes one).
unsafe extern "system" fn menu_has_mouse_union(
    menu_man: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    if crate::picker::is_open() {
        if FORCED.fetch_add(1, Ordering::Relaxed) == 0 {
            summons_log(format_args!(
                "cursor: the picker holds the mouse; the game frees the cursor and holds the camera"
            ));
        }
        return 1;
    }
    let next = ORIG.load(Ordering::Acquire);
    if next == 0 {
        return 0;
    }
    // SAFETY: the next handler or the trampoline, both `UnionFn`.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    unsafe { call(menu_man, b, c, d) }
}

/// How many predicate calls the picker has answered.
pub(crate) fn forced() -> usize {
    FORCED.load(Ordering::Relaxed)
}

/// Install the detour on 1.17.1. Any other build, or other bytes at the address, leaves the
/// cursor and camera to the game and says so.
pub(crate) fn install() {
    if game_file_version() != Some(MAPPED_FILE_VERSION_1171) {
        summons_log(format_args!(
            "cursor: not 1.17.1 ({:?}); the picker gets no free cursor in the world",
            game_file_version()
        ));
        return;
    }
    let Ok(base) = game_module_base() else {
        return;
    };
    let entry = base + MENU_HAS_MOUSE_1171_RVA;
    let mut bytes = [0u8; MENU_HAS_MOUSE_1171_PROLOGUE.len()];
    // SAFETY: a fault-tolerant read of code bytes.
    let read = unsafe { read_bytes(entry, &mut bytes) };
    // Byte 0 may be Arxan's `jmp rel32`, which the installer follows; the rest must match.
    let matches = read
        && (bytes == MENU_HAS_MOUSE_1171_PROLOGUE
            || (bytes[0] == 0xe9 && bytes[5..] == MENU_HAS_MOUSE_1171_PROLOGUE[5..]));
    if !matches {
        summons_log(format_args!(
            "cursor: 0x{entry:x} does not open like the menu-mouse predicate ({bytes:02x?}); not \
             hooked"
        ));
        return;
    }
    // SAFETY: a runtime-derived 1.17.1 entry whose bytes were just checked; the handler calls its
    // slot through `UnionFn`.
    match unsafe {
        er_hook::register_union_hook_runtime_derived_following_arxan(
            entry,
            menu_has_mouse_union,
            &ORIG,
        )
    } {
        Ok(()) => summons_log(format_args!(
            "cursor: menu-mouse predicate at 0x{entry:x} answered while the picker is open"
        )),
        Err(status) => summons_log(format_args!(
            "cursor: menu-mouse predicate at 0x{entry:x} refused: {status:?}"
        )),
    }
}
