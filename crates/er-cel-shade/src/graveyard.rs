//! When something the GPU or a hook may still be reading can be released.
//!
//! A superseded redirect table, a replaced cel variant or a resized buffer can still be named by
//! command lists the GPU has not finished, and a table by a hook that loaded it a moment before it
//! was replaced. There is no fence to ask: appending our own work to the game's
//! `ExecuteCommandLists` device-removed the game about 28 s in on the native path
//! (`crates/er-quickload/src/experiments/gpu_frame_timing.rs`), so this DLL submits nothing and
//! counts instead. An item is released only once at least [`MIN_PRESENTS`] presents and
//! [`MIN_ELAPSED_MS`] of wall time have both passed since it was retired.
//!
//! Frames in flight are unmeasured (design section 16), so both margins are generous, and a full
//! graveyard leaks its oldest item and counts it rather than release anything early. A leak costs
//! memory; an early release costs the GPU reading a freed pipeline.
//!
//! # The present clock
//!
//! The render thread reports each present through [`on_present`], called from `menu::end_frame`,
//! which the overlay runs every frame. When no present has been reported for [`WALL_ONLY_MS`] -- the
//! overlay never drew, or the game stopped presenting -- the worker's [`wall_tick`] releases on wall
//! time alone, [`WALL_ONLY_MS`] after retirement.
//!
//! The policy ([`Graveyard`], [`releasable`], [`presents_live`]) is pure and tested on the host.
//! The Windows half holds the process's one graveyard and owns the COM references in it.

use std::collections::VecDeque;

/// Presents that must pass after retirement before an item is released.
pub const MIN_PRESENTS: u64 = 30;

/// Wall time that must pass after retirement as well, in milliseconds.
pub const MIN_ELAPSED_MS: u64 = 1_000;

/// With no present reported for this long, wall time alone decides, and releases an item this
/// long after its retirement.
pub const WALL_ONLY_MS: u64 = 2_000;

/// Items held at most. Retiring one more leaks the oldest.
pub const CAPACITY: usize = 512;

/// What the clocks say at one release pass.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Clock {
    /// Presents counted so far, or `None` when presents are not being reported and wall time alone
    /// decides.
    pub present: Option<u64>,
    /// Milliseconds since a fixed start.
    pub now_ms: u64,
}

/// May an item retired at present `retired_present` and time `retired_ms` be released now?
#[must_use]
pub fn releasable(retired_present: u64, retired_ms: u64, clock: Clock) -> bool {
    let elapsed = clock.now_ms.saturating_sub(retired_ms);
    match clock.present {
        Some(present) => {
            present.saturating_sub(retired_present) >= MIN_PRESENTS && elapsed >= MIN_ELAPSED_MS
        }
        None => elapsed >= WALL_ONLY_MS,
    }
}

/// Is the present clock live: was a present reported within the last [`WALL_ONLY_MS`]? When it is,
/// release passes belong to [`on_present`]; when it is not, to [`wall_tick`].
#[must_use]
pub fn presents_live(last_present_ms: Option<u64>, now_ms: u64) -> bool {
    last_present_ms.is_some_and(|at| now_ms.saturating_sub(at) < WALL_ONLY_MS)
}

/// One retired item and when it was retired.
struct Grave<T> {
    item: T,
    present: u64,
    ms: u64,
}

/// Retired items, oldest first.
pub struct Graveyard<T> {
    graves: VecDeque<Grave<T>>,
    leaked: u64,
}

impl<T> Default for Graveyard<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T> Graveyard<T> {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            graves: VecDeque::new(),
            leaked: 0,
        }
    }

    /// Hold `item` until it may be released. A full graveyard leaks its oldest item: it is never
    /// dropped, so whatever its drop would have released stays alive for the life of the process.
    pub fn retire(&mut self, item: T, present: u64, now_ms: u64) {
        if self.graves.len() >= CAPACITY
            && let Some(oldest) = self.graves.pop_front()
        {
            std::mem::forget(oldest.item);
            self.leaked += 1;
        }
        self.graves.push_back(Grave {
            item,
            present,
            ms: now_ms,
        });
    }

    /// Take every item that may be released now, oldest first, for the caller to drop outside
    /// whatever lock guards the graveyard. Stops at the first item that may not, because
    /// retirement times only grow.
    pub fn collect(&mut self, clock: Clock) -> Vec<T> {
        let mut released = Vec::new();
        while self
            .graves
            .front()
            .is_some_and(|grave| releasable(grave.present, grave.ms, clock))
        {
            if let Some(grave) = self.graves.pop_front() {
                released.push(grave.item);
            }
        }
        released
    }

    /// Items waiting.
    #[must_use]
    pub fn len(&self) -> usize {
        self.graves.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.graves.is_empty()
    }

    /// Items leaked because the graveyard was full.
    #[must_use]
    pub fn leaked(&self) -> u64 {
        self.leaked
    }
}

#[cfg(windows)]
pub use process::{
    Corpse, GRAVEYARD_LEN, LEAKED, OwnedCom, RELEASED, com_add_ref, com_release, now_ms,
    on_present, retire, retire_com, retire_table, wall_tick,
};

/// The process's one graveyard, and the COM references it owns.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
    use std::sync::{LazyLock, Mutex, MutexGuard, PoisonError};
    use std::time::Instant;

    use windows::core::{IUnknown_Vtbl, Interface};

    use super::{Clock, Graveyard, presents_live};
    use crate::table::Table;

    /// `AddRef` through the object's own vtable; the count it returns.
    ///
    /// # Safety
    ///
    /// `object` must be a live COM interface pointer.
    pub unsafe fn com_add_ref(object: usize) -> u32 {
        // SAFETY: every COM interface begins with the `IUnknown` vtable.
        unsafe {
            let vtable = *(object as *const *const IUnknown_Vtbl);
            ((*vtable).AddRef)(object as *mut c_void)
        }
    }

    /// `Release` through the object's own vtable; the count it returns.
    ///
    /// # Safety
    ///
    /// `object` must be a live COM interface pointer the caller holds a reference to, which this
    /// gives up.
    pub unsafe fn com_release(object: usize) -> u32 {
        // SAFETY: every COM interface begins with the `IUnknown` vtable.
        unsafe {
            let vtable = *(object as *const *const IUnknown_Vtbl);
            ((*vtable).Release)(object as *mut c_void)
        }
    }

    /// One reference to a COM object, released when dropped. A raw pointer rather than a `windows`
    /// interface, so it moves between threads whatever that interface's marker traits say.
    pub struct OwnedCom(usize);

    impl OwnedCom {
        /// Take over one reference.
        ///
        /// # Safety
        ///
        /// `raw` must be a live COM interface pointer, and the caller must own one reference to it,
        /// which passes to the result.
        #[must_use]
        pub unsafe fn from_raw(raw: usize) -> Self {
            Self(raw)
        }

        /// Take over the reference an interface holds.
        #[must_use]
        pub fn new<T: Interface>(object: T) -> Self {
            Self(object.into_raw() as usize)
        }

        #[must_use]
        pub fn as_raw(&self) -> usize {
            self.0
        }
    }

    impl Drop for OwnedCom {
        fn drop(&mut self) {
            if self.0 != 0 {
                // SAFETY: this owns one reference to a live object.
                unsafe { com_release(self.0) };
            }
        }
    }

    /// Anything the graveyard holds.
    pub enum Corpse {
        /// A superseded redirect table, which hooks may still be probing.
        Table(Box<Table>),
        /// A pipeline or a buffer that command lists the GPU has not finished may still name.
        Com(OwnedCom),
    }

    static GRAVEYARD: Mutex<Graveyard<Corpse>> = Mutex::new(Graveyard::new());

    static START: LazyLock<Instant> = LazyLock::new(Instant::now);

    /// `PRESENT_AT_MS` before the first present.
    const NEVER: u64 = u64::MAX;

    /// The present count last reported, and when.
    static PRESENT: AtomicU64 = AtomicU64::new(0);
    static PRESENT_AT_MS: AtomicU64 = AtomicU64::new(NEVER);

    /// Counters for the diagnostics and the stats export, readable without the lock.
    pub static GRAVEYARD_LEN: AtomicUsize = AtomicUsize::new(0);
    pub static LEAKED: AtomicU64 = AtomicU64::new(0);
    pub static RELEASED: AtomicU64 = AtomicU64::new(0);

    /// Milliseconds since this module first asked.
    #[must_use]
    pub fn now_ms() -> u64 {
        u64::try_from(START.elapsed().as_millis()).unwrap_or(u64::MAX)
    }

    fn lock() -> MutexGuard<'static, Graveyard<Corpse>> {
        // A panic while holding it leaves the graveyard as consistent as any push or pop does.
        GRAVEYARD.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn note(yard: &Graveyard<Corpse>) {
        GRAVEYARD_LEN.store(yard.len(), Ordering::Relaxed);
        LEAKED.store(yard.leaked(), Ordering::Relaxed);
    }

    /// Hold `corpse` until no hook and no unfinished command list can still name it.
    pub fn retire(corpse: Corpse) {
        let present = PRESENT.load(Ordering::Acquire);
        let now = now_ms();
        let mut yard = lock();
        yard.retire(corpse, present, now);
        note(&yard);
    }

    pub fn retire_table(table: Box<Table>) {
        retire(Corpse::Table(table));
    }

    /// Retire the reference an interface holds.
    pub fn retire_com<T: Interface>(object: T) {
        retire(Corpse::Com(OwnedCom::new(object)));
    }

    /// The present tick: `present` is the caller's count of presents so far. Called once per
    /// present from `menu::end_frame`.
    pub fn on_present(present: u64) {
        let now = now_ms();
        PRESENT.store(present, Ordering::Release);
        PRESENT_AT_MS.store(now, Ordering::Release);
        release(Clock {
            present: Some(present),
            now_ms: now,
        });
    }

    /// The worker's fallback: releases on wall time alone, and only while no present is being
    /// reported. Cheap to call often.
    pub fn wall_tick() {
        let now = now_ms();
        let at = PRESENT_AT_MS.load(Ordering::Acquire);
        if presents_live((at != NEVER).then_some(at), now) {
            return;
        }
        release(Clock {
            present: None,
            now_ms: now,
        });
    }

    fn release(clock: Clock) {
        let released = {
            let mut yard = lock();
            let released = yard.collect(clock);
            note(&yard);
            released
        };
        RELEASED.fetch_add(released.len() as u64, Ordering::Relaxed);
        // Dropped here, outside the lock: each `Com` drop calls into the driver.
        drop(released);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::rc::Rc;

    fn presents(present: u64, now_ms: u64) -> Clock {
        Clock {
            present: Some(present),
            now_ms,
        }
    }

    fn wall(now_ms: u64) -> Clock {
        Clock {
            present: None,
            now_ms,
        }
    }

    #[test]
    fn an_item_waits_for_both_30_presents_and_1_second() {
        let mut yard = Graveyard::new();
        yard.retire("old table", 100, 5_000);
        // Plenty of time, too few presents.
        assert!(yard.collect(presents(129, 60_000)).is_empty());
        // Plenty of presents, too little time.
        assert!(yard.collect(presents(10_000, 5_999)).is_empty());
        assert_eq!(yard.len(), 1);
        assert_eq!(yard.collect(presents(130, 6_000)), vec!["old table"]);
        assert!(yard.is_empty());
    }

    #[test]
    fn wall_time_alone_waits_2_seconds() {
        let mut yard = Graveyard::new();
        yard.retire(7, 0, 1_000);
        assert!(yard.collect(wall(2_999)).is_empty());
        assert_eq!(yard.collect(wall(3_000)), vec![7]);
    }

    #[test]
    fn release_is_oldest_first_and_stops_at_the_first_that_must_wait() {
        let mut yard = Graveyard::new();
        yard.retire(1, 0, 0);
        yard.retire(2, 10, 500);
        yard.retire(3, 40, 2_000);
        assert_eq!(yard.collect(presents(40, 1_500)), vec![1, 2]);
        assert_eq!(yard.collect(presents(70, 3_000)), vec![3]);
    }

    #[test]
    fn a_clock_behind_the_retirement_never_releases() {
        // A present count or a time from before the retirement saturates to zero elapsed.
        assert!(!releasable(100, 5_000, presents(50, 1_000)));
        assert!(!releasable(100, 5_000, wall(1_000)));
    }

    /// Counts its drops, to tell a leaked item from a released one.
    struct Counted {
        id: usize,
        drops: Rc<Cell<usize>>,
    }

    impl Drop for Counted {
        fn drop(&mut self) {
            self.drops.set(self.drops.get() + 1);
        }
    }

    #[test]
    fn past_512_the_oldest_is_leaked_never_released_early() {
        let drops = Rc::new(Cell::new(0));
        let mut yard = Graveyard::new();
        for id in 0..=CAPACITY {
            yard.retire(
                Counted {
                    id,
                    drops: Rc::clone(&drops),
                },
                0,
                0,
            );
        }
        assert_eq!(yard.len(), CAPACITY);
        assert_eq!(yard.leaked(), 1);
        // Leaked, not dropped: whatever it owned stays alive.
        assert_eq!(drops.get(), 0);
        // Nothing waits less than its margins because of the leak.
        assert!(yard.collect(presents(29, 999)).is_empty());
        let released = yard.collect(presents(30, 1_000));
        let ids: Vec<usize> = released.iter().map(|c| c.id).collect();
        assert_eq!(ids, (1..=CAPACITY).collect::<Vec<_>>());
        drop(released);
        assert_eq!(drops.get(), CAPACITY);
    }

    #[test]
    fn the_present_clock_is_live_for_2_seconds_after_a_present() {
        assert!(!presents_live(None, 0));
        assert!(presents_live(Some(1_000), 1_000));
        assert!(presents_live(Some(1_000), 2_999));
        assert!(!presents_live(Some(1_000), 3_000));
    }
}
