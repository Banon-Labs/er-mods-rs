//! Keeping the window open long enough to read, when there is nobody to hold it open.
//!
//! # The bug this exists for
//!
//! A console program started from Explorer gets a console of its own, and Windows destroys that
//! console the instant the process exits. Double-clicking the installer therefore produced a
//! window that appeared and vanished -- and the faster it failed, the less of it there was to
//! see. The case that actually reached a player is the one where the game cannot be found: the
//! message naming what to do about it was on screen for a few milliseconds.
//!
//! # Why it cannot simply always wait
//!
//! The same executable is run from a terminal, from scripts, and from this repo's own checks.
//! A program that waits for a keypress on exit hangs every one of those, which is a worse bug
//! than the one being fixed because it is silent.
//!
//! So the wait is conditional on how the process was started, and the question has a direct
//! answer: [`GetConsoleProcessList`] reports how many processes are attached to this console.
//! Started from a shell, the shell is attached too and the count is at least two. Started from
//! Explorer, the console was made for this process alone and the count is exactly one. The
//! documented return is "the number of process identifiers stored in the buffer", with zero
//! reserved for failure "because every console has at least one process associated with it", so
//! a one is an answer and not an absence.
//!
//! [`GetConsoleProcessList`]: https://learn.microsoft.com/en-us/windows/console/getconsoleprocesslist

use std::io::{self, IsTerminal, Write};

/// Whether this process appears to have been started by double-clicking it.
///
/// Always false where the question cannot be asked, which is every non-Windows build. A Linux
/// run of this binary is a developer or a test, and neither wants a keypress.
pub fn launched_from_explorer() -> bool {
    should_wait(attached_process_count())
}

/// Whether a console with this many processes attached belongs to this process alone.
///
/// Split out from the call so the rule is testable without a console. Zero is the documented
/// failure return, and the honest reading of a failure is "no evidence this was double-clicked"
/// -- waiting on it would hang a run with no console at all.
fn should_wait(attached: u32) -> bool {
    attached == 1
}

/// Whether input is coming from a person at a keyboard rather than a pipe or a file.
///
/// What this gates is the prompt for a game directory: asking a question of a script produces a
/// program that appears to hang, and answering it with whatever the pipe held next is worse.
pub fn input_is_interactive() -> bool {
    io::stdin().is_terminal()
}

/// Hold the window open until a key is pressed, but only when closing it would destroy the only
/// copy of what is on screen.
///
/// Called on every exit path, successful or not. A player who double-clicked the installer
/// wants to read the "installed 9 mods, launch it with..." lines just as much as an error.
pub fn wait_before_the_window_closes() {
    if !launched_from_explorer() {
        return;
    }
    println!("\nPress any key to close this window.");
    let _ = io::stdout().flush();
    // Anything already queued is a leftover from the picker -- the release edge of the enter
    // that confirmed the install, most often. Read without draining it first and the wait ends
    // before the player has seen anything, which is the bug this function exists to fix.
    drain_pending_input();
    let mut stdin = io::stdin();
    let _ = crate::tui::next_key(&mut stdin);
}

#[cfg(windows)]
fn attached_process_count() -> u32 {
    // The buffer has to hold at least one identifier and the count has to be greater than zero,
    // or the call fails with `ERROR_INVALID_PARAMETER`. One element is enough: a console with
    // more processes than that returns the number required rather than storing anything, which
    // is the number wanted here.
    let mut attached = [0u32; 1];
    unsafe { platform::GetConsoleProcessList(attached.as_mut_ptr(), 1) }
}

#[cfg(not(windows))]
fn attached_process_count() -> u32 {
    0
}

#[cfg(windows)]
fn drain_pending_input() {
    if let Some(input) = platform::input_handle() {
        unsafe { platform::FlushConsoleInputBuffer(input) };
    }
}

#[cfg(not(windows))]
fn drain_pending_input() {}

#[cfg(windows)]
mod platform {
    const STD_INPUT_HANDLE: u32 = -10i32 as u32;
    const INVALID_HANDLE_VALUE: isize = -1;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        pub fn GetConsoleProcessList(list: *mut u32, count: u32) -> u32;
        pub fn FlushConsoleInputBuffer(handle: isize) -> i32;
        fn GetStdHandle(handle_id: u32) -> isize;
    }

    pub fn input_handle() -> Option<isize> {
        let raw = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
        (raw != 0 && raw != INVALID_HANDLE_VALUE).then_some(raw)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_console_owned_by_this_process_alone_is_a_double_click() {
        assert!(should_wait(1));
    }

    #[test]
    fn a_shell_attached_to_the_console_means_nobody_is_waiting() {
        // Two is a command prompt; more is a nested shell or a build tool. None of them want a
        // keypress before the process exits.
        assert!(!should_wait(2));
        assert!(!should_wait(3));
        assert!(!should_wait(64));
    }

    #[test]
    fn a_failed_call_does_not_wait() {
        // Zero is the documented failure return, and it is also what the non-Windows build
        // reports. Waiting on it would hang a run that has no console to hold open.
        assert!(!should_wait(0));
    }

    #[test]
    fn a_build_with_no_console_never_waits() {
        // On anything but Windows this has to be false unconditionally, because the pause it
        // gates reads a key and nothing would ever press one in a test or a pipeline.
        #[cfg(not(windows))]
        assert!(!launched_from_explorer());
    }
}
