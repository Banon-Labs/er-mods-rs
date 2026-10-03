//! R3 in the item list cycles 0, 1, 2, 3, 0; view 3 hides the game's three panels and draws our
//! own weapon board in their place.
//!
//! # The step
//!
//! R3 in `CS::GaitemSelectDialog` calls a 39-byte function (1.16.2 `0x1409970c0`, 1.17.1
//! `0x140998260`, found by a write watchpoint on the mode it changes):
//!
//! ```text
//! r8 = *(u64*)(this+0xb48)          ; pane count, 3 for the item list
//! if (r8 < 1) return;
//! mode = (mode + 1) % r8            ; mode at this+0x8f8
//! tail-call apply(&mode, mode)
//! ```
//!
//! `apply` calls each pane's `vtable+0x10(pane, &shown)` with `shown = (i == index)`. The detour
//! replaces the step with the same arithmetic over `count + 1`. It has no translation row, so it is
//! found by its bytes in the running image, and `apply` is read out of its own tail jump.
//!
//! # View 3
//!
//! The pane callbacks return at once when told `shown = false`; each one only lays out the shared
//! `DetailStatusViewParts` when its view is entered. So an out-of-range index changes nothing on
//! screen, and view 3 instead enters view 0's layout by calling pane 0's own callback with
//! `shown = true` -- the game's view that has no right or center panel (measured live: with it, the
//! screen is empty but for what we draw). The left panel, the item list itself, is faded to alpha 0 and
//! brought back on the next view. Alpha and not `Visible`, because a hidden item list stops taking
//! R3.
//!
//! R3's enable predicate (1.16.2 `0x140995990`, 1.17.1 `0x140996b30`) returns false unless
//! `0 <= mode < count`, which disables R3 in view 3; it is detoured to return true there.
//!
//! `MenuWindowJob::Run` (1.16.2 `0x1407ad1c0`) names the item list's window (`job+0x130`) and notices
//! when it closes in view 3. It is shared with er-quit-menu and the product, so it is registered
//! through the hook union.
//!
//! # Drawing
//!
//! A guest of the process's overlay host draws the board (`board.rs`) on the game's own frame. A separate top-level
//! window cannot be transparent under Wine here: a colour key and per-pixel alpha both left a black
//! screen over the game.

#[cfg(windows)]
mod board;
#[cfg(windows)]
mod imp;
#[cfg(windows)]
mod menu_font;

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_r3_view_host_stub() -> i32 {
    1
}
