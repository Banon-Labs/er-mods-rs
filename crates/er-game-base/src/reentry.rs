//! One thread-local re-entrancy latch, for the code paths that can be called from inside
//! themselves.
//!
//! # Why this keeps being needed
//!
//! Three separate places in this workspace have now been killed by the same shape, and each one
//! grew its own copy of the same latch before this module existed:
//!
//! | where | the loop |
//! |---|---|
//! | `save_policy_logs::append_autoload_debug` | the logger's own file open re-enters the `CreateFileW` detour, which logs |
//! | `er_save_redirect::reentry` | a detour's own `fs::read` re-enters the detour |
//! | `crashlog::veh_exit_hooks::crash_vectored_handler` | describing a fault faults, and a VEH is re-entered for its own faults |
//!
//! All three are SAME-THREAD synchronous recursion, so the latch is thread-local: a process-wide
//! flag would wrongly mute a legitimate concurrent call on another thread, which in the logger's
//! case silently drops most of the log.
//!
//! None of them are bounded by a call budget. A budget is checked once per entry and each entry
//! costs a whole stack frame, so the stack always runs out first -- measured on the VEH at 4704
//! bytes a level against a 1 MiB stack, dead after ~220 of a permitted 256.
//!
//! # The "unanswerable means nested" rule
//!
//! [`ReentryLatch::enter`] returns `None` when the thread-local cannot be reached at all -- during
//! thread teardown, or a fault early enough that TLS is not up. That counts as nested on purpose:
//! refusing one diagnostic costs a line, and guessing the other way costs the process.

use core::cell::Cell;
use core::sync::atomic::{AtomicUsize, Ordering};

/// A thread-local "am I already inside?" latch plus a count of what it refused.
///
/// Declare one per guarded path in a `thread_local!` and pair it with a `static` counter:
///
/// ```
/// use er_game_base::reentry::ReentryLatch;
/// use core::sync::atomic::{AtomicUsize, Ordering};
///
/// static REFUSALS: AtomicUsize = AtomicUsize::new(0);
/// thread_local! {
///     static LATCH: ReentryLatch = const { ReentryLatch::new() };
/// }
///
/// fn guarded(depth: &core::cell::Cell<usize>) {
///     let Some(_token) = ReentryLatch::enter(&LATCH, &REFUSALS) else {
///         return;
///     };
///     depth.set(depth.get() + 1);
///     guarded(depth); // the re-entrant call the latch exists to refuse
/// }
///
/// let depth = core::cell::Cell::new(0);
/// guarded(&depth);
/// assert_eq!(depth.get(), 1);
/// assert_eq!(REFUSALS.load(Ordering::SeqCst), 1);
/// ```
pub struct ReentryLatch {
    inside: Cell<bool>,
}

impl ReentryLatch {
    /// A latch nobody is inside yet.
    pub const fn new() -> Self {
        Self {
            inside: Cell::new(false),
        }
    }

    /// `Some(token)` only for the OUTERMOST entry on this thread; `None` for a nested one, which
    /// also bumps `refusals`. The flag clears when the token is dropped, including on an early
    /// return or an unwind.
    pub fn enter(
        latch: &'static std::thread::LocalKey<Self>,
        refusals: &'static AtomicUsize,
    ) -> Option<ReentryToken> {
        let entered = latch
            .try_with(|latch| !latch.inside.replace(true))
            .unwrap_or(false);
        if entered {
            Some(ReentryToken { latch })
        } else {
            refusals.fetch_add(1, Ordering::SeqCst);
            None
        }
    }
}

impl Default for ReentryLatch {
    fn default() -> Self {
        Self::new()
    }
}

/// Holds a [`ReentryLatch`] closed for as long as it lives.
pub struct ReentryToken {
    latch: &'static std::thread::LocalKey<ReentryLatch>,
}

impl Drop for ReentryToken {
    fn drop(&mut self) {
        let _ = self.latch.try_with(|latch| latch.inside.set(false));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    static TEST_REFUSALS: AtomicUsize = AtomicUsize::new(0);
    thread_local! {
        static TEST_LATCH: ReentryLatch = const { ReentryLatch::new() };
    }

    /// The counter and the flag are process-global, so these take turns.
    static TESTS: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn serialize() -> std::sync::MutexGuard<'static, ()> {
        TESTS.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn enter() -> Option<ReentryToken> {
        ReentryLatch::enter(&TEST_LATCH, &TEST_REFUSALS)
    }

    /// One entry per thread at a time, and the flag must CLEAR when the token dies -- a latch that
    /// leaked its flag would silence every later call on that thread, which is the same bug
    /// wearing the opposite costume.
    #[test]
    fn a_second_entry_on_the_same_thread_is_refused_until_the_first_is_dropped() {
        let _serialized = serialize();
        let before = TEST_REFUSALS.load(Ordering::SeqCst);
        let outer = enter().expect("a fresh thread is not nested");
        assert!(enter().is_none());
        assert_eq!(TEST_REFUSALS.load(Ordering::SeqCst), before + 1);
        drop(outer);
        assert!(enter().is_some());
        assert_eq!(TEST_REFUSALS.load(Ordering::SeqCst), before + 1);
    }

    /// Per THREAD, not per process: two threads faulting or logging at once are two independent
    /// events, and a process-wide flag would drop the second one for no reason.
    #[test]
    fn the_latch_is_per_thread_not_per_process() {
        let _serialized = serialize();
        let outer = enter().expect("a fresh thread is not nested");
        let other_thread_entered = std::thread::spawn(|| enter().is_some())
            .join()
            .expect("thread joins");
        assert!(other_thread_entered);
        drop(outer);
    }

    /// Models the production descent: a body whose own work re-enters it. Without the latch this
    /// recurses until the stack dies, which is exactly what the VEH did on 2026-08-28.
    #[test]
    fn a_body_that_re_enters_itself_runs_once() {
        let _serialized = serialize();
        fn body(entries: &Cell<usize>) {
            let Some(_token) = enter() else {
                return;
            };
            entries.set(entries.get() + 1);
            assert!(
                entries.get() <= 1,
                "the latch let it recurse: {} entries",
                entries.get()
            );
            body(entries);
        }

        let entries = Cell::new(0);
        body(&entries);
        assert_eq!(entries.get(), 1, "exactly one entry survives the latch");
    }
}
