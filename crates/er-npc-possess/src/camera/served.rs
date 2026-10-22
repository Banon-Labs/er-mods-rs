//! The camera's own `LockCamParam` row, and the id it is served under: the decisions, without the
//! game, so `cargo test` proves them on the host.
//!
//! # Why the row is served rather than written
//!
//! Seamless Co-op matches players on `lobby_key`, a fingerprint of the param tables as they sit in
//! memory, compared for equality. Any write to a resident row moves that fingerprint and puts the
//! player in a matchmaking pool of one (bd
//! `death-persist-is-the-lobby-key-mover-seamless-201-2720-2026-10-22`). So the follow camera's row
//! is this DLL's copy, and the game is handed it by [`crate::camera::getter`], a detour on
//! `LookupLockCamParam(out, id)` -- the getter `ApplyZoomLerp` calls every frame -- which replaces
//! the row pointer in `out` for one id. The tables Seamless hashes are never touched.
//!
//! # Why an id the table does not contain
//!
//! The id goes into `ChrExFollowCam+0x468`, and it has to be one nothing else ever asks the getter
//! for, because the detour cannot tell callers apart. A row id of the table is the wrong kind of id
//! for that, for three reasons read out of the 2.7.2.0 image:
//!
//! * The `ChrExFollowCam` constructor looks up rows 0, 1000 and 1001 and caches the pointers it
//!   gets back at `+0x470`, `+0x478` and `+0x480` for the camera's whole life (the mount blend reads
//!   them). Row 1000 was this mod's old default, so a camera rebuilt mid-possession would have kept
//!   the possession's row as its Torrent row after release. An id the table lacks is never asked
//!   for there.
//! * The lock-on manager looks up ids out of its own state and out of the target character (the
//!   getter's callers at `0x140716aa3`, `0x1407176af` and `0x14071818c`), and the map region picks
//!   one through `+0x464`. "No `NpcParam` or `RideParam` names it" was never the same as "nothing
//!   asks for it".
//! * On a miss the getter writes a null row and `ApplyZoomLerp` skips the whole update, so no
//!   shipped content can ever name an id the table lacks: the camera would freeze. That makes such
//!   an id private to whoever serves it.
//!
//! The cost is the same miss: a frame that asks for the id while nothing is served gets a null row
//! and skips one update, keeping the previous frame's distance and pivot. Release puts `+0x468`
//! back before it stops serving, so at most one frame in flight can see that.
//!
//! # Why nothing is ever freed
//!
//! A frame in flight holds the row pointer the detour handed it until `ApplyZoomLerp` returns, on
//! whichever thread the camera updates on. Nothing synchronises that with the possession task, so a
//! copy is leaked rather than freed or reused: [`Served::withdraw`] only stops new lookups from
//! getting it. One copy is `0x80` bytes plus the id, made once per possession and once per config
//! edit that moves the framing.

// Pure state handling; ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use core::ptr;
use core::sync::atomic::{AtomicBool, AtomicPtr, Ordering};

/// The first id tried. Far above every `LockCamParam` id the shipped regulation uses (166 rows,
/// the highest 1,001,001, read with `scripts/er-param-read.py LockCamParam --limit 1000`), and
/// positive as an `i32`, because `ApplyZoomLerp` treats a negative `+0x468` as "no override" and
/// the getter answers a negative id without searching at all.
pub(crate) const SERVED_ID_FIRST: u32 = 2_000_000_000;

/// How many consecutive ids are tried before giving up. A regulation that fills 64 ids in a row
/// two billion up is not one anybody ships; the bound is there so the search cannot spin.
pub(crate) const SERVED_ID_TRIES: u32 = 64;

/// The first id from [`SERVED_ID_FIRST`] that `table_has` says is not a row, or `None` when every
/// one tried is. Every answer is positive as an `i32`.
pub(crate) fn pick_served_id(table_has: impl Fn(u32) -> bool) -> Option<u32> {
    (0..SERVED_ID_TRIES)
        .filter_map(|step| SERVED_ID_FIRST.checked_add(step))
        .filter(|id| i32::try_from(*id).is_ok())
        .find(|id| !table_has(*id))
}

/// What `ChrExFollowCam+0x468` held before a possession, as the value to put back on release.
///
/// The constructor writes -1 ("no override") and nothing in the game writes the field again, so
/// a value in this mod's served range can only be left over from a release that could not restore
/// it. Putting that back would leave the camera asking for an id nobody serves, which freezes it,
/// so it is read as the -1 it replaced.
pub(crate) fn original_override(read: i32) -> i32 {
    let ours = u32::try_from(read)
        .is_ok_and(|id| (SERVED_ID_FIRST..SERVED_ID_FIRST + SERVED_ID_TRIES).contains(&id));
    if ours { NO_OVERRIDE } else { read }
}

/// `ChrExFollowCam+0x468` as the constructor leaves it: `ApplyZoomLerp` skips a negative override.
pub(crate) const NO_OVERRIDE: i32 = -1;

/// One served copy: the id it answers and the row itself.
pub(crate) struct ServedRow<R> {
    pub(crate) id: u32,
    pub(crate) row: R,
    /// Set the first time a lookup is answered with this copy, so the log can say so once.
    announced: AtomicBool,
}

impl<R> ServedRow<R> {
    /// True exactly once: on the first call after the copy was published.
    pub(crate) fn first_serve(&self) -> bool {
        !self.announced.swap(true, Ordering::Relaxed)
    }
}

/// The copy the getter detour hands out, if any.
///
/// One atomic pointer, so the detour -- which runs on every `LockCamParam` lookup, from whatever
/// thread the camera updates on -- reads the id and the row it answers with as one value.
pub(crate) struct Served<R: 'static> {
    current: AtomicPtr<ServedRow<R>>,
}

/// `R: Sync` because a published copy is read from the camera's thread while the possession task
/// that made it runs on another.
impl<R: Sync + 'static> Served<R> {
    pub(crate) const fn new() -> Self {
        Self {
            current: AtomicPtr::new(ptr::null_mut()),
        }
    }

    /// Serve `row` under `id` from now on, replacing whatever was served before. The copy is moved
    /// to memory that is never freed (see the module docs) and its address is returned.
    pub(crate) fn publish(&self, id: u32, row: R) -> &'static ServedRow<R> {
        let leaked: &'static ServedRow<R> = Box::leak(Box::new(ServedRow {
            id,
            row,
            announced: AtomicBool::new(false),
        }));
        // `cast_mut` only because `AtomicPtr` stores `*mut`; nothing writes through it, and the
        // one mutable field is an atomic.
        self.current
            .store(ptr::from_ref(leaked).cast_mut(), Ordering::Release);
        leaked
    }

    /// Stop serving. A lookup already holding the copy keeps a valid pointer; a new lookup of the
    /// id gets whatever the game's own table says, which is a miss.
    pub(crate) fn withdraw(&self) {
        self.current.store(ptr::null_mut(), Ordering::Release);
    }

    /// The copy to answer a lookup of `asked` with, or `None` to leave the game's answer alone.
    ///
    /// `asked` is the getter's second argument as the union hands it over: the id is in `edx`, so
    /// the upper half of `rdx` is not part of it.
    pub(crate) fn answer(&self, asked: usize) -> Option<&'static ServedRow<R>> {
        let current = self.current.load(Ordering::Acquire);
        // SAFETY: the pointer is null or came from `Box::leak` in `publish`, and nothing frees it.
        let served = unsafe { current.as_ref() }?;
        (served.id == asked as u32).then_some(served)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A row stand-in the size of `LOCK_CAM_PARAM_ST`.
    type Row = [u8; 0x80];

    fn row(fill: u8) -> Row {
        [fill; 0x80]
    }

    #[test]
    fn nothing_is_served_until_something_is_published() {
        let served: Served<Row> = Served::new();
        assert!(served.answer(SERVED_ID_FIRST as usize).is_none());
    }

    #[test]
    fn only_the_served_id_is_answered() {
        let served: Served<Row> = Served::new();
        served.publish(SERVED_ID_FIRST, row(7));
        let hit = served
            .answer(SERVED_ID_FIRST as usize)
            .expect("the served id is answered");
        assert_eq!(hit.row, row(7));
        assert!(served.answer(0).is_none(), "the player's own row 0");
        assert!(
            served.answer(1000).is_none(),
            "the Torrent row the ctor caches"
        );
        assert!(served.answer(SERVED_ID_FIRST as usize + 1).is_none());
    }

    /// The id is a `u32` in `edx`; whatever the caller left in the upper half of `rdx` must not
    /// turn a lookup of the served id into a miss, or the camera freezes.
    #[test]
    fn the_upper_half_of_rdx_is_not_part_of_the_id() {
        let served: Served<Row> = Served::new();
        served.publish(SERVED_ID_FIRST, row(1));
        let dirty = (0xdead_beef_usize << 32) | SERVED_ID_FIRST as usize;
        assert!(served.answer(dirty).is_some());
    }

    /// Withdrawing stops new answers and leaves the copy a lookup already holds readable.
    #[test]
    fn a_withdrawn_copy_stays_readable_for_a_frame_in_flight() {
        let served: Served<Row> = Served::new();
        served.publish(SERVED_ID_FIRST, row(3));
        let in_flight = served.answer(SERVED_ID_FIRST as usize).expect("served");
        served.withdraw();
        assert!(served.answer(SERVED_ID_FIRST as usize).is_none());
        assert_eq!(in_flight.row, row(3), "never freed");
    }

    /// A config edit publishes a new copy over the old one; the old one is not freed either.
    #[test]
    fn a_republish_replaces_the_answer_and_keeps_the_old_copy_alive() {
        let served: Served<Row> = Served::new();
        served.publish(SERVED_ID_FIRST, row(1));
        let first = served.answer(SERVED_ID_FIRST as usize).expect("served");
        served.publish(SERVED_ID_FIRST, row(2));
        let second = served.answer(SERVED_ID_FIRST as usize).expect("served");
        assert_eq!(second.row, row(2));
        assert_eq!(first.row, row(1));
        assert!(!core::ptr::eq(first, second));
    }

    #[test]
    fn the_first_serve_is_reported_once_per_copy() {
        let served: Served<Row> = Served::new();
        let copy = served.publish(SERVED_ID_FIRST, row(0));
        assert!(copy.first_serve());
        assert!(!copy.first_serve());
        let next = served.publish(SERVED_ID_FIRST, row(0));
        assert!(next.first_serve(), "a new copy is announced again");
    }

    #[test]
    fn the_first_id_the_table_lacks_is_picked() {
        assert_eq!(pick_served_id(|_| false), Some(SERVED_ID_FIRST));
        assert_eq!(
            pick_served_id(|id| id < SERVED_ID_FIRST + 2),
            Some(SERVED_ID_FIRST + 2),
            "a regulation that took the first two ids"
        );
        assert_eq!(pick_served_id(|_| true), None, "every id tried is a row");
    }

    /// A leftover served id is never restored into the camera; anything else is put back as read.
    #[test]
    fn a_leftover_served_id_is_not_put_back() {
        assert_eq!(original_override(-1), NO_OVERRIDE);
        assert_eq!(original_override(1000), 1000, "a real row a map region set");
        let first = i32::try_from(SERVED_ID_FIRST).expect("positive");
        assert_eq!(original_override(first), NO_OVERRIDE);
        let last = i32::try_from(SERVED_ID_FIRST + SERVED_ID_TRIES - 1).expect("positive");
        assert_eq!(original_override(last), NO_OVERRIDE);
        assert_eq!(
            original_override(last + 1),
            last + 1,
            "outside the range this mod picks"
        );
    }

    /// A negative `+0x468` is "no override" to `ApplyZoomLerp`, and a negative id never reaches the
    /// getter's search, so every id this can pick must be positive as an `i32`.
    #[test]
    fn every_id_that_can_be_picked_is_positive_as_an_i32() {
        let last = SERVED_ID_FIRST + SERVED_ID_TRIES - 1;
        assert!(i32::try_from(last).is_ok_and(|id| id > 0));
        for skipped in 0..SERVED_ID_TRIES {
            let picked = pick_served_id(|id| id < SERVED_ID_FIRST + skipped);
            assert!(picked.is_some_and(|id| i32::try_from(id).is_ok_and(|id| id > 0)));
        }
    }
}
