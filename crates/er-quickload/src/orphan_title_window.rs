//! When a title menu window has outlived the title, as a pure decision.
//!
//! Its own module, and outside the `#[cfg(windows)]` half of this crate, so the decision runs under
//! `cargo test -p er-quickload` on the host rather than only being type-checked by the
//! cross-compile.
//!
//! # The defect, and what was actually holding the window
//!
//! `PRESS ANY BUTTON` and the publisher footer stay on screen over a character loaded through
//! `System>Quit -> Load Character from File`. The world is up and audible underneath: run
//! `br-20260913-162421-3421` reaches `T_controllable` and `LOAD-CORRECTNESS name="Hero" level=7` at
//! `+49666ms`, and on the same frame logs
//!
//! ```text
//! title-dialog-orphan: observed frame=2116 owner=0xbed78800 dialog=0x1edc3880 owner_window_count=1
//! ```
//!
//! so the surviving window is known, counted, and left alone -- because until now this crate had
//! nothing to close it with.
//!
//! Two measurements out of a live process (pid 3031799, read through `scripts/er-live-fields.py`)
//! name the cause, and neither of them is the job:
//!
//! * The surviving window is the `TitleTopDialog` at `TitleStep+0xe0`. Its `MenuWindow+0x3b0`
//!   re-entry latch read 0 six minutes later, so `MenuWindow::Close` had never run on it once.
//! * The gate that was supposed to take it down called `TITLE_TOP_DIALOG_CLEANUP_RVA`, which the
//!   1.16.2 decompile shows is `CS::TitleTopDialog::~TitleTopDialog` -- it rewrites the vtable,
//!   frees `+0xd60`, destroys six `CSScaleformValue`s and chains to `~MenuWindow`. It deregisters
//!   nothing and closes nothing. It was also latched once per process, which is exactly why the
//!   first load looked clean and every later one did not.
//!
//! # Who should have torn the window down
//!
//! `CS::TitleStep` is a twelve-state machine whose table is built at `FUN_1400a4f50` into
//! `0x143d71580`, and `STEP_BeginTitle` (index 3, `0x140b0c5b0`) composes the title's `MenuJob`
//! chain -- `FUN_14081f9f0` builds the `05_000_Title` job -- and submits it through `FUN_140b0e530`,
//! which assigns it into `TitleStep+0x130` and requests state 10, `STEP_MenuJobWait`. That step
//! (`0x140b0d400`) does one thing that matters here: it calls
//! `ExecuteMenuJob(&TitleStep->field85_0x130, dt)`.
//!
//! Two native functions reap the title, and both are gated on a job reporting a terminal result:
//!
//! * `ExecuteMenuJob` (`0x1407a9600`) runs the job, then `if (MenuJobResult::ShouldContinue(&r))`
//!   unrefs it and writes null back over `TitleStep+0x130`. `ShouldContinue` is `0x1407a9200`, three
//!   instructions -- `CMP dword ptr [RCX],0x1; SETA AL; RET` -- so it is `state > Continue(1)`, i.e.
//!   the predicate is "has a result", not "wants another tick".
//! * `CS::MenuWindowJob::Run` (`0x1407ad1c0`) ends by reading its own window's result at
//!   `MenuWindow+0x1e8`, and on the same `ShouldContinue` calls `FUN_1407ada40` -- the teardown that
//!   deregisters the window from `CSMenuMan` -- and propagates the terminal result to its caller.
//!
//! # Why neither runs on a switch
//!
//! `continue_confirm` (`0x140b0e180`) ends with `FUN_140b0d960(titleStep, 5)`, which writes
//! `FD4StepTemplateBase.requestedState = 5` (`STEP_PlayGame`). The step machine leaves
//! `STEP_MenuJobWait` on the next tick, so `ExecuteMenuJob` is never called against that job again,
//! and `STEP_PlayGame` / `STEP_GameStepWait` never touch `+0x130`. The only other writer of that
//! slot is `FUN_140b0e530`, reachable only from the four title-entry steps, none of which runs again
//! until the next return to title. Meanwhile the window's `MenuWindowJob` is still in `CSMenuMan`'s
//! own pump, so it keeps drawing.
//!
//! On the vanilla title the same `continue_confirm` runs, but it runs as the `TitleTopDialog`
//! Continue row's functor from inside the job chain: the dialog job returns its terminal result on
//! that same tick and the two reapers above fire before the step change takes effect. Our call comes
//! from outside the chain, so nothing ever terminates.
//!
//! # What this crate may do about it
//!
//! Ask the window to close, through the game's own per-window close: `FUN_1407ac890(MenuWindow*)`
//! builds a `Failed` `MenuJobResult` and invokes the window's own virtual at `vtable+0x60`. It is
//! the identical call `CS::MenuWindowJob::Run` makes when its close policy at `job+0xf0` returns
//! that verdict, so every step after it -- the window setting its result, `FUN_1407ada40`
//! deregistering it, the chain reaping, `ExecuteMenuJob` nulling `TitleStep+0x130` -- is the game's
//! own code in the game's own order on the menu-pump thread.
//!
//! The request alone is half a teardown, and the half it does is worse than doing nothing. Measured
//! 2026-09-11 19:57 on a build that asked and committed straight away: the close fired, the window
//! stopped drawing, `owner+0x128` read 1 before and 1 after, and the still-registered window
//! swallowed the player's input on a world telemetry called controllable. The deregistration lives
//! in `FUN_1407ada40`, which runs only when the window's job is run -- so the commit has to be held
//! back for the frames `STEP_MenuJobWait` needs. That hold is `title_menu_drained_for_commit` in
//! `experiments::own_load::loaders::load_drive`; what belongs here is the judgement of which window
//! may be asked at all.
//!
//! The rejected alternative is recorded here because it is the obvious one and it is already known
//! to crash: overwriting `TitleStep+0x130` with a fresh job orphaned the title `IfElseJob`'s sibling
//! `CS::MenuWindowJob`s and faulted inside `CS::DLFixedVector::push_back 0x140733fea`. Killing the
//! parent does not deregister the children; asking each window to close does.

/// Resource names of the title surfaces this crate will ask to close.
///
/// These are the game's own `MenuWindowJob` filenames, read from `job+0x60`, not names of ours.
/// `05_000_Title` hosts `PressStart` / `StaticSystemText_101000` -- the `PRESS ANY BUTTON` prompt --
/// and `05_020_TitleInformation` is the publisher and copyright footer under it. `05_001_Title_Logo`
/// is listed with them because `AcquireMenuResource` fetches it one millisecond after `05_000_Title`
/// on both the boot title and the rebuilt one, so it arrives and departs with them.
///
/// The list is exhaustive on purpose. Every other window the pump offers -- a message box, the
/// in-world menus, the `05_010_ProfileSelect` rows -- belongs to someone, and this crate closing one
/// would be answering for them.
pub const TITLE_SURFACE_RESOURCE_NAMES: &[&str] = &[
    "05_000_Title",
    "05_001_Title_Logo",
    "05_020_TitleInformation",
];

/// Close requests spent per switch before the gate gives up.
///
/// A request is one call to the native per-window close. The window answers it by setting its own
/// result, which takes at least the next pump tick to be read, so a small budget covers the windows
/// present (three at most) plus a few frames of latency. When the budget is gone the gate stops
/// asking and the orphan stays, which is the behaviour that shipped before this module existed -- a
/// gate that could ask forever would turn a cosmetic defect into a per-frame call into menu code,
/// which is worse.
pub const MAX_CLOSE_REQUESTS_PER_SWITCH: usize = 16;

/// The map id `GameMan+0xc30` holds when no real world is mounted.
///
/// Named again here rather than imported because this module is the host-testable half of the crate
/// and must not reach into the `#[cfg(windows)]` constants. `FULLREAD_C30_M10_DEFAULT` in
/// `constants::autoload_state` is the same number and is the one the runtime path reads.
pub const C30_TITLE_DEFAULT: i32 = 0xa01_0000;

/// Whether the pump is looking at one of the title's own surfaces.
#[must_use]
pub fn is_title_surface(resource: &str) -> bool {
    TITLE_SURFACE_RESOURCE_NAMES.contains(&resource)
}

/// Whether this crate should ask the native owner to close the window running under `resource`.
///
/// Three independent conditions, each of which alone would be wrong:
///
/// * `resource` is a title surface. Anything else belongs to the player.
/// * `c30` names a real map. During the switch's own return to title `c30` is `C30_TITLE_DEFAULT`
///   and the title is legitimately on screen -- the switch is waiting on it. Closing there would
///   break the very flow this fixes. This is a live read of `GameMan+0xc30`, not a latch, so a
///   future return to title re-protects the title automatically.
/// * `switch_committed` -- `SYSTEM_QUIT_CONTINUE_CONFIRM_FRESH_DESER_DONE`, the durable "a switch
///   reload committed" latch, re-armed to 0 for the next switch. Without it a title surface drawn
///   during any other real-world moment would be closed by a crate that had not put it there.
///
/// The budget is last so an exhausted one reads as a refusal rather than as the window not being an
/// orphan.
#[must_use]
pub fn orphan_title_window_close_required(
    resource: &str,
    c30: i32,
    switch_committed: bool,
    close_requests_spent: usize,
) -> bool {
    is_title_surface(resource)
        && c30 != C30_TITLE_DEFAULT
        && switch_committed
        && close_requests_spent < MAX_CLOSE_REQUESTS_PER_SWITCH
}

/// Whether the switch may ask the engine to close the title menu it just made the game rebuild.
///
/// The other predicate in this module judges a window by its resource name, because it is consulted
/// from the menu pump where a name is in hand. This one is consulted from the switch drive, where
/// no name is in hand: the caller has the title owner's own window count at `owner+0x128` and the
/// holder slot at `owner+0xe0`, so those are what it judges. The identity of that slot is settled
/// separately and at the call site, by reading the window's vtable -- this predicate never stands in
/// for that check.
///
/// `switch_committed` is the same `SYSTEM_QUIT_CONTINUE_CONFIRM_FRESH_DESER_DONE` latch the
/// resource-name predicate reads, and it carries the same weight here. Without it the boot autoload
/// levers that also reach `own_load_continue_fire` would ask the boot title to close the menu it is
/// about to hand the world over through, which is a flow this crate has no business touching.
///
/// The budget is the same one the resource-name predicate uses, for the same reason: a close that
/// has to be asked more than a handful of times is not going to be answered, and a load must never
/// be held hostage to a teardown.
#[must_use]
pub fn switch_title_menu_close_required(
    window_count: usize,
    switch_committed: bool,
    close_requests_spent: usize,
) -> bool {
    window_count > 0 && switch_committed && close_requests_spent < MAX_CLOSE_REQUESTS_PER_SWITCH
}

#[cfg(test)]
mod orphan_title_window_tests {
    use super::{
        C30_TITLE_DEFAULT, MAX_CLOSE_REQUESTS_PER_SWITCH, is_title_surface,
        orphan_title_window_close_required, switch_title_menu_close_required,
    };

    /// A real map id from a switch this module documents: `br-20260913-162421-3421` mounted
    /// `0xe000000`.
    const C30_REAL_MAP: i32 = 0x0e00_0000;

    /// The defect itself: `05_000_Title` still pumping after the switch put a real world up.
    #[test]
    fn the_press_any_button_window_is_closed_once_the_switch_has_a_world() {
        assert!(orphan_title_window_close_required(
            "05_000_Title",
            C30_REAL_MAP,
            true,
            0
        ));
    }

    /// The footer arrives with the prompt and has to leave with it, so it is named separately.
    #[test]
    fn the_publisher_footer_is_closed_on_the_same_terms() {
        assert!(orphan_title_window_close_required(
            "05_020_TitleInformation",
            C30_REAL_MAP,
            true,
            0
        ));
    }

    /// The window the switch is waiting for. Between the world loss and the feed, `c30` is the title
    /// default and the title is doing its job; closing it here would break the switch.
    #[test]
    fn the_title_is_left_alone_while_the_switch_is_still_returning_to_it() {
        assert!(!orphan_title_window_close_required(
            "05_000_Title",
            C30_TITLE_DEFAULT,
            true,
            0
        ));
    }

    /// A title surface on screen in a real world that this crate did not switch into is not ours to
    /// close.
    #[test]
    fn a_title_surface_outside_a_committed_switch_is_not_touched() {
        assert!(!orphan_title_window_close_required(
            "05_000_Title",
            C30_REAL_MAP,
            false,
            0
        ));
    }

    /// The rule that keeps this from becoming a dialog dismisser. Every window the menu pump offers
    /// that is not a title surface is refused, including the ones this crate builds itself.
    #[test]
    fn no_other_window_the_pump_offers_is_ever_closed() {
        for resource in [
            "02_000_IngameTop",
            "02_040_OptionSetting",
            "05_010_ProfileSelect",
            "02_990_textinput_patheditor",
            "01_900_Black",
            "",
        ] {
            assert!(
                !is_title_surface(resource),
                "{resource} is not a title surface"
            );
            assert!(
                !orphan_title_window_close_required(resource, C30_REAL_MAP, true, 0),
                "{resource} must never be closed by this crate"
            );
        }
    }

    /// An exhausted budget refuses, so a window that never answers cannot be asked every frame.
    #[test]
    fn the_budget_stops_the_gate_asking_forever() {
        assert!(!orphan_title_window_close_required(
            "05_000_Title",
            C30_REAL_MAP,
            true,
            MAX_CLOSE_REQUESTS_PER_SWITCH
        ));
        assert!(orphan_title_window_close_required(
            "05_000_Title",
            C30_REAL_MAP,
            true,
            MAX_CLOSE_REQUESTS_PER_SWITCH - 1
        ));
    }

    #[test]
    fn the_switch_asks_for_a_close_only_while_the_title_still_holds_a_window() {
        assert!(switch_title_menu_close_required(1, true, 0));
        assert!(
            !switch_title_menu_close_required(0, true, 0),
            "a drained owner is the pass condition, not a reason to ask again"
        );
    }

    /// The boot autoload levers reach the same commit. A title that has not been rebuilt by a switch
    /// is the one the game is about to hand the world over through, and it is not ours to close.
    #[test]
    fn the_switch_never_asks_before_its_own_reload_has_committed() {
        assert!(!switch_title_menu_close_required(1, false, 0));
    }

    #[test]
    fn the_switch_close_budget_is_the_same_one_the_pump_uses() {
        assert!(switch_title_menu_close_required(
            1,
            true,
            MAX_CLOSE_REQUESTS_PER_SWITCH - 1
        ));
        assert!(
            !switch_title_menu_close_required(1, true, MAX_CLOSE_REQUESTS_PER_SWITCH),
            "a load must never be held hostage to a teardown that is not answering"
        );
    }
}
