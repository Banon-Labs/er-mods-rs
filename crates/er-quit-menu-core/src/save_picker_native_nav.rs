//! Directional menu input read from the game's own `CS::MoveDir` resolver.
//!
//! The picker's drive strip wants four directions and gets them from
//! [`SavePickerMenuHooks`](crate::save_picker_menu::SavePickerMenuHooks). In the product that seam
//! is filled by an `InputBlocker`-backed latch fed from rawinput, DInput and XInput. A standalone
//! shell has none of that, so every one of those fields was `None`, `take_nav_edges_for` answered
//! `0` forever, and left/right did nothing on the drive strip while the mouse worked -- the shape
//! reported from run `br-20260912-212001-9610`.
//!
//! This module is the answer a shell can have: ask the engine which way the player pressed, by
//! calling the same function the game's own `CS::GridControl` calls to step its cursor.
//!
//! # The function, and how it was identified (1.16.2 dump on `:8765`)
//!
//! `FUN_140757c40(this, out)` is a two-line wrapper: it writes the byte `2` into a local, then
//! tails into `FUN_140757b60(this, out, &mode)`, the generic `CS::MoveDir` resolver. That resolver
//! builds a `std::function<CS::MoveDir(CS::CSEzMenuViewerPad const&)>` over the captured mode and
//! hands it to `FUN_14075d7b0`, whose body is:
//!
//! ```text
//! 14075d7d6  call FUN_140758050        ; rcx still `this` -- reads *(char*)this plus the global gates
//! 14075d7dd  jz   .refused
//! 14075d7e7  call FUN_14075d860        ; construct a CSEzMenuViewerPad for device 0 (rcx unused)
//! 14075d804  call qword ptr [rax+0x10] ; the lambda, with the out pointer in rdx
//! .refused:
//! 14075d82e  mov  qword ptr [rdi], rsi ; both components zeroed
//! ```
//!
//! The lambda is `FUN_140756510`, and it is the whole answer:
//!
//! ```text
//! provider = *(void**)(pad + 0x10);
//! if (provider->vtbl[5](provider, 0x1a, &mode)) { out->x =  0; out->y = -1; return; }
//! if (provider->vtbl[5](provider, 0x1b, &mode)) { out->x =  0; out->y = +1; return; }
//! if (provider->vtbl[5](provider, 0x1d, &mode)) { out->x = -1; out->y =  0; return; }
//! if (provider->vtbl[5](provider, 0x1c, &mode)) { out->x = +1; out->y =  0; return; }
//! out->x = 0; out->y = 0;
//! ```
//!
//! So the four directional menu codes are `0x1a` up, `0x1b` down, `0x1d` left, `0x1c` right.
//!
//! # Which component is horizontal, proved twice
//!
//! `FUN_14073b0c0` is the grid's cursor step, and it opens with
//! `step = dir[1] * max(1, grid->columns) + dir[0]`. The component at `+0x00` is added to the item
//! index directly and the one at `+0x04` is multiplied by the column count, so `+0x00` is the
//! column axis. The multi-column branch of the same function then bounds-checks
//! `dir[0] + (index % columns)` against `columns`, which only makes sense for a column delta.
//!
//! Independently, the vertical `ScrollBarV` update `FUN_14074f3c0` reads both modes and then
//! *ignores* the `+0x00` component outright (`if (x == 0) { if (y != 0) { step } }`), stepping its
//! value only on `+0x04` and lighting its up arrow on `y < 0` and its down arrow on `y > 0`. A
//! vertical bar ignoring the horizontal axis is the same assignment arrived at from the other side.
//!
//! A column index grows left to right, so `x == -1` is left and `x == +1` is right. That last step
//! is the one piece of this that is convention rather than arithmetic, which is why
//! [`sample`] logs the raw `x` it read: a live run reads the direction off the log without another
//! reverse-engineering pass, and swapping the two mask constants below would be the whole fix.
//!
//! # Why calling it is reading rather than fabricating
//!
//! `provider->vtbl[5]` is three instructions -- `PollInput(provider, (code + 0x32) * 4 + mode)` --
//! and `PollInput` is two red-black lookups and up to four condition evaluations over the pad's own
//! per-frame state. It writes nothing. The engine makes this exact call every frame that a menu
//! grid is up, and the answer this module acts on is the engine's, arrived at through the player's
//! own `CSPcKeyConfig` bindings for keyboard, mouse and pad alike.
//!
//! The one argument is a single byte meaning "this surface accepts menu input" -- `FUN_140758050`
//! is the only reader of `this` in the path, and `FUN_14075d860` takes `this` in `rcx` and never
//! touches it. The global readiness gates inside `FUN_140758050` (`CSMenuMan+0x798`,
//! `CSMenuMan+0x19`, the fade plate timer) still apply, so a frame the game would refuse input on
//! is refused here too, and the refusal path zeroes both components rather than leaving them.
//!
//! # Mode 2
//!
//! `FUN_140757c40` is the mode-2 wrapper and it is the one the grid uses: `CS::GridControl` stores
//! two `MoveDir(*)(out, this)` adapters at `grid+0x760`/`+0x768`, and both
//! (`FUN_14073c400`, the transposed one, and its straight sibling) call `FUN_140757c40`. Mode 2 is
//! therefore whatever the engine considers one cursor step, auto-repeat included. This module still
//! latches on the rising edge, which is correct whether mode 2 pulses or stays asserted for the
//! length of a hold -- a pulse becomes one step, a hold becomes one step, and neither can run away.

use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{game_data_addr, game_module_base, game_rva_named, safe_read_usize};
use er_game_base::rva::CS_MENU_MAN_GLOBAL_RVA;

use crate::host::append_autoload_debug;
use crate::save_picker_menu::{
    SAVE_PICKER_NAV_DOWN_MASK, SAVE_PICKER_NAV_LEFT_MASK, SAVE_PICKER_NAV_RIGHT_MASK,
    SAVE_PICKER_NAV_UP_MASK,
};

/// `FUN_140757c40` -- `CS::MoveDir GetMoveDir(mode 2)`, the mode `CS::GridControl` steps its cursor
/// with. 1.16.2 rva; `0x758a90` on 1.17, byte-identical over the whole `.pdata` extent
/// (`0x21` bytes in both images) including the `mov byte ptr [rsp+0x38], 2` that names the mode.
pub(crate) const MENU_MOVE_DIR_REPEAT_RVA: usize = 0x757c40;

/// `CS::MoveDir`: a column delta and a row delta, in that order.
#[repr(C)]
#[derive(Clone, Copy, Default, PartialEq, Eq)]
struct MoveDir {
    x: i32,
    y: i32,
}

/// The value the engine's own callers put in the byte `FUN_140758050` reads: this surface wants
/// menu input. Anything nonzero does, and the global gates still decide whether the frame counts.
const MENU_INPUT_ACCEPTED: u8 = 1;

/// The directions this reader answers for, and the reason it is not all four.
///
/// The defect is horizontal: the drive strip is the one control on the picker with no native hit
/// target of its own, so left and right had nowhere to come from. Up and down already work -- the
/// native list moves its own cursor for them, and
/// [`save_picker_menu_pump_edge_scroll`](crate::save_picker_menu::save_picker_menu_pump_edge_scroll)
/// is tuned around exactly that: it defers a press at an extreme row to the wrap the native list is
/// about to make, ages that deferral out, and tells a wrap from a mouse sweep by whether the
/// direction is still held. Handing it a second vertical source would change every one of those
/// judgements at once, for a direction nobody reported broken. So the vertical answer is read -- it
/// is in the log line below, as evidence for whoever wants it later -- and then withheld.
const NATIVE_NAV_SUPPLIED_MASK: usize = SAVE_PICKER_NAV_LEFT_MASK | SAVE_PICKER_NAV_RIGHT_MASK;

/// Directions asserted on the last sample, so an edge is a transition rather than a level.
static NAV_HELD: AtomicUsize = AtomicUsize::new(0);
/// Rising edges not yet drained by [`take_nav_edges_for`].
static NAV_EDGES: AtomicUsize = AtomicUsize::new(0);
/// Edges seen, for the rate limit on the log line below.
static NAV_EDGE_COUNT: AtomicUsize = AtomicUsize::new(0);
/// Whether the one-shot line naming the resolved address has been written.
static NAV_READER_ANNOUNCED: AtomicUsize = AtomicUsize::new(0);

/// Resolve the resolver for the running build, announcing the address once.
///
/// A refusal is announced once too. The previous behaviour of this seam was silence, and silence
/// is what let a dead left/right survive a run: nothing in the log distinguished "no nav source"
/// from "the player pressed nothing".
fn move_dir_fn() -> Option<unsafe extern "system" fn(*const u8, *mut MoveDir) -> *mut MoveDir> {
    let resolved = game_rva_named(MENU_MOVE_DIR_REPEAT_RVA as u32, "MENU_MOVE_DIR_REPEAT_RVA");
    if NAV_READER_ANNOUNCED.swap(1, Ordering::SeqCst) == 0 {
        match &resolved {
            Ok(address) => append_autoload_debug(format_args!(
                "save-picker-nav: native nav reader armed at 0x{address:x} (CS::MoveDir, mode 2)"
            )),
            Err(error) => append_autoload_debug(format_args!(
                "save-picker-nav: no native nav reader, left/right will do nothing: {error}"
            )),
        }
    }
    // Safety: the address is the verified 1.17 destination of the 1.16.2 resolver, whose signature
    // was read off its own disassembly: `rcx` the input-accepted byte, `rdx` the out `MoveDir`,
    // `rax` the same out pointer back.
    resolved.ok().map(|address| unsafe {
        std::mem::transmute::<
            usize,
            unsafe extern "system" fn(*const u8, *mut MoveDir) -> *mut MoveDir,
        >(address)
    })
}

/// True once the menu manager singleton exists.
///
/// `FUN_140758050` panics through `DLPanic` on a null `GLOBAL_CSMenuMan` rather than returning, so
/// the one singleton this path can reach before the game has built it is checked here. The others
/// it touches (`CSFade`, `FD4PadManager`) are reached only from inside a live menu frame, which is
/// the only kind of frame this module is called on.
fn menu_manager_live() -> bool {
    let Ok(base) = game_module_base() else {
        return false;
    };
    let slot = game_data_addr(base, CS_MENU_MAN_GLOBAL_RVA, "CS_MENU_MAN_GLOBAL_RVA");
    // Safety: a fault-safe read of a game global, zero when the address has no mapping.
    slot != 0 && unsafe { safe_read_usize(slot) }.is_some_and(|pointer| pointer != 0)
}

/// The direction mask a `CS::MoveDir` stands for.
fn mask_for(dir: MoveDir) -> usize {
    let mut mask = 0;
    if dir.x < 0 {
        mask |= SAVE_PICKER_NAV_LEFT_MASK;
    } else if dir.x > 0 {
        mask |= SAVE_PICKER_NAV_RIGHT_MASK;
    }
    if dir.y < 0 {
        mask |= SAVE_PICKER_NAV_UP_MASK;
    } else if dir.y > 0 {
        mask |= SAVE_PICKER_NAV_DOWN_MASK;
    }
    mask
}

/// Ask the engine which way the player is pressing, and latch what newly became true.
///
/// Sampling twice in one frame is harmless and expected: the picker drains left/right and up/down
/// in two separate calls per pump tick, and the pad state behind the answer is rebuilt once per
/// frame, so the second sample finds the same level and raises no second edge.
fn sample() {
    if !menu_manager_live() {
        return;
    }
    let Some(move_dir) = move_dir_fn() else {
        return;
    };
    let accepted = MENU_INPUT_ACCEPTED;
    let mut dir = MoveDir::default();
    // Safety: `accepted` is the single byte the path reads through `this`, and `dir` is an
    // eight-byte `CS::MoveDir` the callee writes in full on both its branches.
    unsafe { move_dir(&raw const accepted, &raw mut dir) };
    let held = mask_for(dir);
    let previous = NAV_HELD.swap(held, Ordering::SeqCst);
    let rising = held & !previous & NATIVE_NAV_SUPPLIED_MASK;
    if rising == 0 {
        return;
    }
    NAV_EDGES.fetch_or(rising, Ordering::SeqCst);
    let count = NAV_EDGE_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
    if count <= 20 || count.is_multiple_of(25) {
        append_autoload_debug(format_args!(
            "save-picker-nav: native move_dir #{count} x={} y={} held=0x{held:x} rising=0x{rising:x}",
            dir.x, dir.y
        ));
    }
}

/// The seam's install step. There is nothing to install: this reader calls a game function and
/// hooks nothing, which is the point of it. Kept as an entry point so the picker's pump has one
/// place to reach whether the host supplies a device layer or not.
pub fn ensure_nav_reader() {
    sample();
}

/// Drain the requested directions from the edge latch, sampling first so the latch is current.
pub fn take_nav_edges_for(mask: usize) -> usize {
    sample();
    NAV_EDGES.fetch_and(!mask, Ordering::SeqCst) & mask
}

/// Directions asserted on the engine's most recent answer, consuming nothing.
pub fn nav_held() -> usize {
    NAV_HELD.load(Ordering::SeqCst) & NATIVE_NAV_SUPPLIED_MASK
}

#[cfg(test)]
mod native_nav_tests {
    use super::*;

    #[test]
    fn move_dir_components_map_to_the_picker_directions() {
        assert_eq!(mask_for(MoveDir { x: 0, y: 0 }), 0);
        assert_eq!(
            mask_for(MoveDir { x: -1, y: 0 }),
            SAVE_PICKER_NAV_LEFT_MASK,
            "code 0x1d writes x = -1"
        );
        assert_eq!(
            mask_for(MoveDir { x: 1, y: 0 }),
            SAVE_PICKER_NAV_RIGHT_MASK,
            "code 0x1c writes x = +1"
        );
        assert_eq!(
            mask_for(MoveDir { x: 0, y: -1 }),
            SAVE_PICKER_NAV_UP_MASK,
            "code 0x1a writes y = -1"
        );
        assert_eq!(
            mask_for(MoveDir { x: 0, y: 1 }),
            SAVE_PICKER_NAV_DOWN_MASK,
            "code 0x1b writes y = +1"
        );
    }

    /// The resolver only ever reports one direction, but the latch is a mask and the drive strip
    /// drains a subset of it, so a diagonal must not lose the half nobody asked for.
    #[test]
    fn a_drain_leaves_the_directions_it_was_not_asked_for() {
        NAV_EDGES.store(
            SAVE_PICKER_NAV_LEFT_MASK | SAVE_PICKER_NAV_UP_MASK,
            Ordering::SeqCst,
        );
        let taken = NAV_EDGES.fetch_and(
            !(SAVE_PICKER_NAV_LEFT_MASK | SAVE_PICKER_NAV_RIGHT_MASK),
            Ordering::SeqCst,
        ) & (SAVE_PICKER_NAV_LEFT_MASK | SAVE_PICKER_NAV_RIGHT_MASK);
        assert_eq!(taken, SAVE_PICKER_NAV_LEFT_MASK);
        assert_eq!(
            NAV_EDGES.load(Ordering::SeqCst),
            SAVE_PICKER_NAV_UP_MASK,
            "up was not requested and must still be there for the scroll pump"
        );
        NAV_EDGES.store(0, Ordering::SeqCst);
    }

    /// `MoveDir` is written by the game through a raw pointer, so its shape is a contract.
    #[test]
    fn move_dir_is_two_packed_int32s() {
        assert_eq!(std::mem::size_of::<MoveDir>(), 8);
        assert_eq!(std::mem::align_of::<MoveDir>(), 4);
    }
}
