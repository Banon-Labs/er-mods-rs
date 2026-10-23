//! A free cursor and a still camera while the picker is open.
//!
//! In the world the game holds the cursor for the camera, and the only native way to get one is
//! to open one of its menus. Both halves of that come from one predicate, "a menu has the mouse",
//! `bool(CSMenuManImp*)` (2.7.2.0 `0x140766b00`). It reads
//! `CSMenuMan+0x1a` and the shown-menu bitmap at `+0x1c`, and it has three callers:
//!
//! * the cursor gate (2.7.2.0 `0x140e21b80`), `predicate && IsGameInForeground`, which decides
//!   whether `CSMouseMan` shows the cursor and stops clipping and re-centring it;
//! * the mouse X and Y axis readers (`0x140e2ca50`, `0x140e2cb40`), which return 0 while it is
//!   true; `ChrCam`'s input update and the mouse-flick lock-on switch read only those.
//!
//! So the detour answers true while the picker is open and asks the game otherwise. Foreground
//! handling is untouched: an unfocused game still keeps its cursor rules. Mouse buttons, keyboard
//! actions and the right stick are gated by direct reads of `CSMenuMan+0x1c`, not by this; the
//! left button is blanked over the picker by `er-dinput-suppress-core`.
//!
//! Measured live 2026-10-06 with `scripts/frida/npc-summons-gaps-probe.js`: the predicate answers
//! 0 in the world and 1 while a game menu is open. It is hooked by its 2.7.2.0 address on 2.7.2.0
//! only, after its opening bytes are checked, through the elected hub's runtime-derived registrar
//! so it shares one detour with every other menu DLL's handler. On its own union it lost the
//! read-then-patch race to the menu DLLs on 2026-10-23: the picker was open 88 seconds and the
//! handler answered 0 calls.

#![cfg(windows)]

use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::game_build::{describe_build, is_supported_build};
use er_game_base::mem::{game_module_base, read_bytes};
use er_hook::UnionFn;

use crate::addr::{MENU_HAS_MOUSE_PROLOGUE, MENU_HAS_MOUSE_RVA};
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

/// Install the detour on 2.7.2.0. Any other build, or other bytes at the address, leaves the
/// cursor and camera to the game and says so.
pub(crate) fn install() {
    // The opening bytes are compared below before anything is hooked.
    let Some(rva) = is_supported_build().then_some(MENU_HAS_MOUSE_RVA) else {
        summons_log(format_args!(
            "cursor: the 2.7.2.0 menu-mouse predicate is refused on this build ({}); the picker \
             gets no free cursor in the world",
            describe_build()
        ));
        return;
    };
    let Ok(base) = game_module_base() else {
        return;
    };
    let entry = base + rva;
    let mut bytes = [0u8; MENU_HAS_MOUSE_PROLOGUE.len()];
    // SAFETY: a fault-tolerant read of code bytes.
    let read = unsafe { read_bytes(entry, &mut bytes) };
    // Byte 0 may be a `jmp rel32` another module's detour wrote, which the hub follows; the rest
    // must match.
    let matches = read
        && (bytes == MENU_HAS_MOUSE_PROLOGUE
            || (bytes[0] == 0xe9 && bytes[5..] == MENU_HAS_MOUSE_PROLOGUE[5..]));
    if !matches {
        summons_log(format_args!(
            "cursor: 0x{entry:x} does not open like the menu-mouse predicate ({bytes:02x?}); not \
             hooked"
        ));
        return;
    }
    // Through the elected hub, as `er-overlay-input`'s `install_cursor` does for every other menu
    // DLL: one MinHook instance on the entry, every handler in its chain.
    // SAFETY: a runtime-derived 2.7.2.0 entry whose bytes were just checked; the handler calls its
    // slot through `UnionFn`.
    match unsafe {
        er_hook::register_shared_hook_following_arxan(entry, menu_has_mouse_union, &ORIG)
    } {
        Ok(route) => summons_log(format_args!(
            "cursor: menu-mouse predicate at 0x{entry:x} answered while the picker is open \
             ({route:?})"
        )),
        Err(status) => summons_log(format_args!(
            "cursor: menu-mouse predicate at 0x{entry:x} refused: {status:?}"
        )),
    }
}
