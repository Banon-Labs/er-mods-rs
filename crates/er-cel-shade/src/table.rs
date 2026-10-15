//! The redirect table the command-list hooks read on every pipeline bind (design 6.1).
//!
//! # Shape
//!
//! An immutable open-addressing table keyed by the game's pipeline pointer. It holds only the
//! pipelines whose behaviour this DLL changes -- a cel variant bound in place of a port (P0.1), a
//! hide or a mask (P2, P3) -- plus Hunt's counters while Hunt is open (P2). In P0.1 that is five
//! entries, against about 8,900 pipelines a session creates.
//!
//! A reader loads the published pointer with `Acquire` and probes: no lock, no allocation, and per
//! probe a multiply, a shift and a compare. About 1,760 binds a frame on 9 recording threads go
//! through it (census `br-20261014-230719-4ec7`), which is why it is neither a locked map nor an
//! epoch scheme with a fence on every read.
//!
//! A writer never edits a published table. It builds a new one ([`Builder`]), carries the counters
//! across by key, publishes it with `Release`, and retires the old one to [`crate::graveyard`],
//! which frees it once no hook can still be probing it. Writers are rare -- a port's creation, a
//! publish from the worker, Hunt opening -- and take one mutex.
//!
//! An edit can also be handed to the next present instead (`defer`), which applies it right after
//! that present publishes its parameter block (`crate::params::publish_then`). The editor's worker
//! publishes a new layer variant that way: the variant reads the block's layer registers, which
//! the worker staged for it in the same breath, so the two must change on the same present.
//!
//! # References
//!
//! An entry that changes behaviour holds one `AddRef` on the game's pipeline, so that pipeline's
//! address cannot be freed and reused by another while an entry names it. The reaper ([`reap`],
//! every [`REAP_PERIOD_MS`] on the worker) finds the entries whose pipeline only this DLL still
//! holds, drops them and releases the reference, which also measures whether the game frees
//! pipelines at all.
//!
//! An entry's variants (its redirect and its mask pipeline) belong to the table from the first
//! publish that carries them. When no published entry names one any more it goes to the
//! graveyard, because command lists the GPU has not finished may still bind it.
//!
//! The core ([`Table`], [`Builder`], [`diff`], [`reap_plan`]) is pure and tested on the host. The
//! Windows half publishes, counts the references and runs the reaper.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU32, Ordering};

/// [`Entry::port`] of an entry that is not a port, such as a Hunt or family entry. Port kinds are
/// numbered below it.
pub const NO_PORT: u8 = u8::MAX;

/// [`Entry::flags`] bits. Reserved in P0.1, which sets none of them.
pub mod flag {
    /// Skip every draw recorded with this pipeline (P2).
    pub const HIDE: u32 = 1 << 0;
    /// Hide on alternate frames, so a family can be found on screen (P2).
    pub const BLINK: u32 = 1 << 1;
    /// Duplicate this pipeline's draws with its mask variant (P3).
    pub const MASK: u32 = 1 << 2;
    /// The pipeline renders to the main view's G-buffer format: the duplication guard (P3).
    pub const MAIN_VIEW_RT: u32 = 1 << 3;
    /// The bits that change what the game draws, and so make an entry hold a reference. Blink is
    /// a hide on alternate frames, so it counts as one.
    pub const BEHAVIOUR: u32 = HIDE | BLINK | MASK;
}

/// The smallest table built, so even an empty one probes like any other.
pub const MIN_CAPACITY: usize = 16;

/// Fibonacci hashing's multiplier: 2^64 divided by the golden ratio.
pub const HASH_MULTIPLIER: u64 = 0x9E37_79B9_7F4A_7C15;

/// A pipeline pointer is a heap allocation aligned to at least 16 bytes, so its low four bits
/// carry nothing.
const KEY_ALIGN_BITS: u32 = 4;

/// How often the reaper runs on the worker.
pub const REAP_PERIOD_MS: u64 = 10_000;

/// One game pipeline the hooks act on.
#[derive(Debug)]
pub struct Entry {
    /// The game's pipeline, as the pointer it binds. 0 marks an empty slot.
    pub key: usize,
    /// The cel variant bound in its place, or 0.
    pub redirect: usize,
    /// The port kind (`PortKind as u8`), or [`NO_PORT`].
    pub port: u8,
    /// [`flag`] bits. Changed only through [`update`], under the writer lock.
    pub flags: AtomicU32,
    /// The mask pipeline its draws are duplicated with (P3), or 0.
    pub mask_variant: usize,
    /// The family bits its mask pipeline writes (P3).
    pub bits: u8,
    /// Binds counted while Hunt is open (P2). Carried across a rebuild by key.
    pub binds: AtomicU32,
    /// Draws counted while Hunt is open (P2). Carried across a rebuild by key.
    pub draws: AtomicU32,
}

impl Entry {
    fn empty() -> Self {
        Self::from_spec(&EntrySpec::new(0))
    }

    fn from_spec(spec: &EntrySpec) -> Self {
        Self {
            key: spec.key,
            redirect: spec.redirect,
            port: spec.port,
            flags: AtomicU32::new(spec.flags),
            mask_variant: spec.mask_variant,
            bits: spec.bits,
            binds: AtomicU32::new(0),
            draws: AtomicU32::new(0),
        }
    }

    /// This entry without its counters.
    #[must_use]
    pub fn spec(&self) -> EntrySpec {
        EntrySpec {
            key: self.key,
            redirect: self.redirect,
            port: self.port,
            flags: self.flags.load(Ordering::Relaxed),
            mask_variant: self.mask_variant,
            bits: self.bits,
        }
    }

    /// Does this entry hold a reference on its game pipeline?
    #[must_use]
    pub fn holds_reference(&self) -> bool {
        self.spec().holds_reference()
    }
}

/// What a writer says about one game pipeline: an [`Entry`] without its counters.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EntrySpec {
    pub key: usize,
    /// A pipeline the caller created and hands over with one reference. The table owns it from the
    /// first publish that carries it.
    pub redirect: usize,
    pub port: u8,
    pub flags: u32,
    /// Handed over like `redirect`.
    pub mask_variant: usize,
    pub bits: u8,
}

impl EntrySpec {
    /// An entry that changes nothing yet: no redirect, no port, no flags.
    #[must_use]
    pub const fn new(key: usize) -> Self {
        Self {
            key,
            redirect: 0,
            port: NO_PORT,
            flags: 0,
            mask_variant: 0,
            bits: 0,
        }
    }

    /// A port bound as `redirect` instead of the game's `key`.
    #[must_use]
    pub const fn port(key: usize, redirect: usize, port: u8) -> Self {
        Self {
            redirect,
            port,
            ..Self::new(key)
        }
    }

    /// Does this entry change behaviour, and so hold a reference on its game pipeline?
    #[must_use]
    pub fn holds_reference(&self) -> bool {
        self.redirect != 0 || self.mask_variant != 0 || self.flags & flag::BEHAVIOUR != 0
    }

    /// The pipelines of ours this entry names.
    pub fn variants(self) -> impl Iterator<Item = usize> {
        [self.redirect, self.mask_variant]
            .into_iter()
            .filter(|variant| *variant != 0)
    }
}

/// A table's capacity for this many entries: a power of two, at least twice the entries, so a
/// probe always reaches an empty slot.
#[must_use]
pub fn capacity_for(entries: usize) -> usize {
    entries
        .saturating_mul(2)
        .max(MIN_CAPACITY)
        .next_power_of_two()
}

/// The slot a key's probe starts at, for a table whose capacity is `2^(64 - shift)`.
fn home(key: usize, shift: u32) -> usize {
    (((key as u64) >> KEY_ALIGN_BITS).wrapping_mul(HASH_MULTIPLIER) >> shift) as usize
}

/// An immutable open-addressing table: linear probing, keys never removed in place.
#[derive(Debug)]
pub struct Table {
    slots: Box<[Entry]>,
    /// `64 - log2(capacity)`, so the hash's top bits index a slot.
    shift: u32,
    len: usize,
    redirects: usize,
}

impl Table {
    /// The entry for this game pipeline. A null key is never an entry.
    #[must_use]
    pub fn get(&self, key: usize) -> Option<&Entry> {
        if key == 0 {
            return None;
        }
        let mask = self.slots.len() - 1;
        let mut at = home(key, self.shift);
        for _ in 0..self.slots.len() {
            let entry = &self.slots[at];
            if entry.key == key {
                return Some(entry);
            }
            if entry.key == 0 {
                return None;
            }
            at = (at + 1) & mask;
        }
        None
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.len
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    #[must_use]
    pub fn capacity(&self) -> usize {
        self.slots.len()
    }

    /// Entries with a redirect: nonzero is what makes the bind hook worth entering.
    #[must_use]
    pub fn redirects(&self) -> usize {
        self.redirects
    }

    /// Every entry, in slot order.
    pub fn iter(&self) -> impl Iterator<Item = &Entry> {
        self.slots.iter().filter(|entry| entry.key != 0)
    }
}

/// The entries of the next table, by key.
#[derive(Clone, Debug, Default)]
pub struct Builder {
    specs: BTreeMap<usize, EntrySpec>,
}

impl Builder {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Every entry of `table`, as of now.
    #[must_use]
    pub fn from_table(table: Option<&Table>) -> Self {
        Self {
            specs: table
                .into_iter()
                .flat_map(Table::iter)
                .map(|entry| (entry.key, entry.spec()))
                .collect(),
        }
    }

    /// Add or replace the entry for `spec.key`. A null key cannot be an entry; it is refused with
    /// `false` and nothing changes.
    pub fn insert(&mut self, spec: EntrySpec) -> bool {
        if spec.key == 0 {
            return false;
        }
        self.specs.insert(spec.key, spec);
        true
    }

    pub fn remove(&mut self, key: usize) -> Option<EntrySpec> {
        self.specs.remove(&key)
    }

    #[must_use]
    pub fn get(&self, key: usize) -> Option<&EntrySpec> {
        self.specs.get(&key)
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.specs.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.specs.is_empty()
    }

    /// The table, with each entry's counters copied from `previous` by key. Counts a hook adds to
    /// `previous` after this returns are lost, which a Hunt counter can afford.
    #[must_use]
    pub fn build(&self, previous: Option<&Table>) -> Table {
        let capacity = capacity_for(self.specs.len());
        let shift = u64::BITS - capacity.trailing_zeros();
        let mask = capacity - 1;
        let mut slots: Box<[Entry]> = (0..capacity).map(|_| Entry::empty()).collect();
        let mut redirects = 0;
        for spec in self.specs.values() {
            let entry = Entry::from_spec(spec);
            if let Some(old) = previous.and_then(|table| table.get(spec.key)) {
                entry
                    .binds
                    .store(old.binds.load(Ordering::Relaxed), Ordering::Relaxed);
                entry
                    .draws
                    .store(old.draws.load(Ordering::Relaxed), Ordering::Relaxed);
            }
            // Ends at an empty slot: the capacity is at least twice the entries.
            let mut at = home(spec.key, shift);
            while slots[at].key != 0 {
                at = (at + 1) & mask;
            }
            slots[at] = entry;
            if spec.redirect != 0 {
                redirects += 1;
            }
        }
        Table {
            slots,
            shift,
            len: self.specs.len(),
            redirects,
        }
    }
}

/// What publishing a table in place of another does to the references this DLL holds.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Diff {
    /// Game pipelines whose entry now holds a reference and did not: `AddRef` before publishing.
    pub add_ref: Vec<usize>,
    /// Game pipelines whose entry held a reference and no longer does: `Release` after publishing.
    pub release: Vec<usize>,
    /// Variants no entry of the new table names: to the graveyard after publishing.
    pub retire: Vec<usize>,
}

/// The reference changes of publishing `new` in place of `old`.
#[must_use]
pub fn diff(old: Option<&Table>, new: &Table) -> Diff {
    let held = |table: Option<&Table>| -> BTreeSet<usize> {
        table
            .into_iter()
            .flat_map(Table::iter)
            .filter(|entry| entry.holds_reference())
            .map(|entry| entry.key)
            .collect()
    };
    let variants = |table: Option<&Table>| -> BTreeSet<usize> {
        table
            .into_iter()
            .flat_map(Table::iter)
            .flat_map(|entry| entry.spec().variants())
            .collect()
    };
    let (held_old, held_new) = (held(old), held(Some(new)));
    let (variants_old, variants_new) = (variants(old), variants(Some(new)));
    Diff {
        add_ref: held_new.difference(&held_old).copied().collect(),
        release: held_old.difference(&held_new).copied().collect(),
        retire: variants_old.difference(&variants_new).copied().collect(),
    }
}

/// What the reaper does with one held entry, from the count its probe `Release` returned.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reap {
    /// Someone besides this DLL still holds the pipeline.
    Keep,
    /// Only this DLL holds it: the game let go. Drop the entry and release the reference.
    Drop,
    /// The probe found the object already destroyed, so the reference this DLL counted was not
    /// there. Drop the entry and release nothing, since a second release would free it again.
    Lost,
}

/// The reaper's decision. The probe is an `AddRef` then a `Release`, so the count the `Release`
/// returns is the count before the probe.
#[must_use]
pub fn reap_decision(count_after_release: u32) -> Reap {
    match count_after_release {
        0 => Reap::Lost,
        1 => Reap::Drop,
        _ => Reap::Keep,
    }
}

/// The entries one reaper pass drops.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReapPlan {
    /// The game let these go; release this DLL's reference after republishing.
    pub dropped: Vec<usize>,
    /// Already destroyed; release nothing.
    pub lost: Vec<usize>,
}

/// Probe every entry that holds a reference, and only those: an entry that holds none names a
/// pipeline that may already be freed. `probe` does the `AddRef` and `Release` and returns the
/// `Release`'s count.
pub fn reap_plan(table: &Table, mut probe: impl FnMut(usize) -> u32) -> ReapPlan {
    let mut plan = ReapPlan::default();
    for entry in table.iter().filter(|entry| entry.holds_reference()) {
        match reap_decision(probe(entry.key)) {
            Reap::Keep => {}
            Reap::Drop => plan.dropped.push(entry.key),
            Reap::Lost => plan.lost.push(entry.key),
        }
    }
    plan
}

/// Is the reaper due, last run at `last_ms`?
#[must_use]
pub fn reap_due(last_ms: u64, now_ms: u64) -> bool {
    now_ms.saturating_sub(last_ms) >= REAP_PERIOD_MS
}

#[cfg(windows)]
pub use process::{
    GAME_PSOS_RELEASED, PUBLISHES, REAPED_LOST, apply_deferred, defer, get, is_applied, published,
    reap, update, worker_tick,
};

/// The published table, its writer, and the reaper.
#[cfg(windows)]
mod process {
    use std::sync::atomic::{AtomicPtr, AtomicU64, Ordering};
    use std::sync::{Mutex, PoisonError};

    use super::{Builder, Entry, Table, diff, reap_due, reap_plan};
    use crate::graveyard::{self, Corpse, OwnedCom, com_add_ref, com_release};
    use crate::log;

    /// The published table, or null before the first publish.
    static TABLE: AtomicPtr<Table> = AtomicPtr::new(std::ptr::null_mut());

    /// Serialises writers. Readers never take it. Lock order: the port registry, then this, then
    /// [`DEFERRED`].
    static WRITER: Mutex<()> = Mutex::new(());

    /// An edit handed to the next present.
    type Deferred = Box<dyn FnOnce(&mut Builder) + Send>;

    /// Edits waiting for the next present, oldest first, each with its ticket.
    static DEFERRED: Mutex<Vec<(u64, Deferred)>> = Mutex::new(Vec::new());
    /// The newest ticket handed out, and the newest applied.
    static ISSUED: AtomicU64 = AtomicU64::new(0);
    static APPLIED: AtomicU64 = AtomicU64::new(0);

    /// Hand `edit` to the next present, which runs it as [`update`] would, right after it publishes
    /// the parameter block. Returns the edit's ticket, for [`is_applied`]. A variant `edit`
    /// inserts belongs to the table from that publish; until then it is the edit's.
    pub fn defer(edit: impl FnOnce(&mut Builder) + Send + 'static) -> u64 {
        let mut deferred = DEFERRED.lock().unwrap_or_else(PoisonError::into_inner);
        let ticket = ISSUED.fetch_add(1, Ordering::AcqRel) + 1;
        deferred.push((ticket, Box::new(edit)));
        ticket
    }

    /// Apply every deferred edit in the order it was handed over, each as a publish of its own, so
    /// a variant one edit inserts and the next replaces is retired like any other. For the
    /// present, and for the editor's worker when no present comes. Returns how many were applied.
    pub fn apply_deferred() -> usize {
        // Most presents have nothing to apply, and those take no lock.
        if APPLIED.load(Ordering::Acquire) >= ISSUED.load(Ordering::Acquire) {
            return 0;
        }
        let _writer = WRITER.lock().unwrap_or_else(PoisonError::into_inner);
        let edits = std::mem::take(&mut *DEFERRED.lock().unwrap_or_else(PoisonError::into_inner));
        let count = edits.len();
        // Every edit taken counts as applied even if one panics, which drops the rest with their
        // variants: a ticket that can never be applied would hold the worker's catch-up back for
        // good, and take this lock on every present after.
        let _done = MarkApplied(edits.last().map_or(0, |(ticket, _)| *ticket));
        for (ticket, edit) in edits {
            update_locked(edit, &[]);
            APPLIED.fetch_max(ticket, Ordering::AcqRel);
        }
        count
    }

    /// Marks every ticket up to its own applied when dropped, unwinding included.
    struct MarkApplied(u64);

    impl Drop for MarkApplied {
        fn drop(&mut self) {
            APPLIED.fetch_max(self.0, Ordering::AcqRel);
        }
    }

    /// Has the edit with this ticket been applied?
    #[must_use]
    pub fn is_applied(ticket: u64) -> bool {
        APPLIED.load(Ordering::Acquire) >= ticket
    }

    /// When the reaper last ran, in [`graveyard::now_ms`] time.
    static LAST_REAP_MS: AtomicU64 = AtomicU64::new(0);

    /// Game pipelines the reaper found released by the game, and references it found missing.
    pub static GAME_PSOS_RELEASED: AtomicU64 = AtomicU64::new(0);
    pub static REAPED_LOST: AtomicU64 = AtomicU64::new(0);

    /// Tables published.
    pub static PUBLISHES: AtomicU64 = AtomicU64::new(0);

    /// The published table, for the hook call in progress.
    ///
    /// The `'static` is the graveyard's promise rather than the type system's: a replaced table
    /// stays allocated for at least 30 presents and 1 s. Use it within one hook call and never
    /// store it.
    #[must_use]
    pub fn published() -> Option<&'static Table> {
        // SAFETY: null, or a table published by `update_locked` and not freed until the graveyard
        // releases it, long after any call that loaded it here has returned.
        unsafe { TABLE.load(Ordering::Acquire).as_ref() }
    }

    /// The entry for a game pipeline in the published table, with [`published`]'s lifetime.
    #[must_use]
    pub fn get(key: usize) -> Option<&'static Entry> {
        published()?.get(key)
    }

    /// Edit the published table: `edit` starts from its entries, and the result is built, has its
    /// references taken and released, and is published. A variant handed over in an inserted
    /// spec belongs to the table from here on; a key gets its `AddRef` here, so the caller takes
    /// none.
    pub fn update(edit: impl FnOnce(&mut Builder)) {
        let _writer = WRITER.lock().unwrap_or_else(PoisonError::into_inner);
        update_locked(edit, &[]);
    }

    /// [`update`] with the writer lock already held. `lost` are keys whose reference is already
    /// gone and must not be released.
    fn update_locked(edit: impl FnOnce(&mut Builder), lost: &[usize]) {
        let old = published();
        let mut builder = Builder::from_table(old);
        edit(&mut builder);
        let new = Box::new(builder.build(old));
        let mut changes = diff(old, &new);
        changes.release.retain(|key| !lost.contains(key));
        for &key in &changes.add_ref {
            // SAFETY: the creation hook that handed over this key's spec holds the pipeline it
            // just created, and a later edit names a pipeline the table already references.
            unsafe { com_add_ref(key) };
        }
        let previous = TABLE.load(Ordering::Acquire);
        TABLE.store(Box::into_raw(new), Ordering::Release);
        PUBLISHES.fetch_add(1, Ordering::Relaxed);
        if !previous.is_null() {
            // SAFETY: `previous` came from `Box::into_raw` above in an earlier publish and is no
            // longer published; the graveyard frees it once no hook can be probing it.
            graveyard::retire(Corpse::Table(unsafe { Box::from_raw(previous) }));
        }
        for variant in changes.retire {
            // SAFETY: the table owned one reference to each variant it named.
            graveyard::retire(Corpse::Com(unsafe { OwnedCom::from_raw(variant) }));
        }
        for key in changes.release {
            // SAFETY: the reference taken when this key's entry first held one.
            unsafe { com_release(key) };
        }
    }

    /// One reaper pass: drop the entries whose pipeline only this DLL still holds, republish, and
    /// release those references. Returns how many pipelines the game had released.
    pub fn reap() -> usize {
        let _writer = WRITER.lock().unwrap_or_else(PoisonError::into_inner);
        let Some(table) = published() else {
            return 0;
        };
        let plan = reap_plan(table, |key| {
            // SAFETY: `reap_plan` probes only entries holding a reference, so the object lives.
            unsafe {
                com_add_ref(key);
                com_release(key)
            }
        });
        if plan.dropped.is_empty() && plan.lost.is_empty() {
            return 0;
        }
        update_locked(
            |builder| {
                for key in plan.dropped.iter().chain(&plan.lost) {
                    builder.remove(*key);
                }
            },
            &plan.lost,
        );
        GAME_PSOS_RELEASED.fetch_add(plan.dropped.len() as u64, Ordering::Relaxed);
        REAPED_LOST.fetch_add(plan.lost.len() as u64, Ordering::Relaxed);
        log(format_args!(
            "reaper: the game released {} pipeline(s); {} reference(s) were already gone",
            plan.dropped.len(),
            plan.lost.len()
        ));
        plan.dropped.len()
    }

    /// The worker's periodic call: the reaper every [`super::REAP_PERIOD_MS`], and the graveyard's
    /// wall-time fallback. Cheap to call often.
    pub fn worker_tick() {
        let now = graveyard::now_ms();
        if reap_due(LAST_REAP_MS.load(Ordering::Relaxed), now) {
            LAST_REAP_MS.store(now, Ordering::Relaxed);
            reap();
        }
        graveyard::wall_tick();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// A plausible pipeline pointer: heap-like and 16-byte aligned.
    fn pso(n: usize) -> usize {
        0x0000_01f0_0000_0000 + n * 0x40
    }

    fn table_of(specs: &[EntrySpec]) -> Table {
        let mut builder = Builder::new();
        for spec in specs {
            assert!(builder.insert(*spec));
        }
        builder.build(None)
    }

    #[test]
    fn hit_miss_and_null_key() {
        let table = table_of(&[
            EntrySpec::port(pso(1), pso(1001), 0),
            EntrySpec::port(pso(2), pso(1002), 3),
        ]);
        let hit = table.get(pso(2)).expect("hit");
        assert_eq!((hit.key, hit.redirect, hit.port), (pso(2), pso(1002), 3));
        assert!(table.get(pso(3)).is_none());
        assert!(table.get(0).is_none());
        assert_eq!(table.redirects(), 2);
        assert!(Builder::new().build(None).get(pso(1)).is_none());
        assert!(Builder::new().build(None).get(0).is_none());
    }

    #[test]
    fn a_null_key_is_never_inserted() {
        let mut builder = Builder::new();
        assert!(!builder.insert(EntrySpec::port(0, pso(9), 1)));
        assert!(builder.is_empty());
    }

    #[test]
    fn capacity_is_a_power_of_two_at_least_twice_the_entries() {
        for entries in 0..=20_000 {
            let capacity = capacity_for(entries);
            assert!(capacity.is_power_of_two(), "{entries}: {capacity}");
            assert!(capacity >= 2 * entries, "{entries}: {capacity}");
            assert!(capacity >= MIN_CAPACITY);
        }
    }

    #[test]
    fn built_tables_find_every_key_at_any_size_up_to_20000() {
        for entries in [0, 1, 2, 7, 8, 9, 15, 16, 17, 255, 4_096, 8_900, 20_000] {
            let specs: Vec<EntrySpec> = (1..=entries).map(|n| EntrySpec::new(pso(n))).collect();
            let table = table_of(&specs);
            assert_eq!(table.len(), entries);
            assert!(table.capacity() >= 2 * table.len());
            for spec in &specs {
                assert_eq!(table.get(spec.key).map(|e| e.key), Some(spec.key));
            }
            assert!(table.get(pso(entries + 1)).is_none());
            assert_eq!(table.iter().count(), entries);
        }
    }

    #[test]
    fn colliding_keys_probe_past_each_other_and_wrap() {
        // Keys that share a home slot in a 16-slot table, found by search.
        let shift = u64::BITS - MIN_CAPACITY.trailing_zeros();
        let last = MIN_CAPACITY - 1;
        let colliding: Vec<usize> = (1..100_000)
            .map(pso)
            .filter(|key| home(*key, shift) == last)
            .take(4)
            .collect();
        assert_eq!(colliding.len(), 4);
        let specs: Vec<EntrySpec> = colliding.iter().map(|key| EntrySpec::new(*key)).collect();
        let table = table_of(&specs);
        assert_eq!(table.capacity(), MIN_CAPACITY);
        for key in &colliding {
            assert_eq!(table.get(*key).map(|e| e.key), Some(*key));
        }
    }

    #[test]
    fn a_rebuild_preserves_counters_by_key() {
        let first = table_of(&[
            EntrySpec::port(pso(1), pso(101), 0),
            EntrySpec::new(pso(2)),
            EntrySpec::new(pso(3)),
        ]);
        let entry = first.get(pso(1)).expect("entry");
        entry.binds.fetch_add(41, Ordering::Relaxed);
        entry.draws.fetch_add(7, Ordering::Relaxed);
        first
            .get(pso(3))
            .expect("entry")
            .binds
            .fetch_add(5, Ordering::Relaxed);

        let mut builder = Builder::from_table(Some(&first));
        builder.remove(pso(2));
        assert!(builder.insert(EntrySpec::new(pso(4))));
        // Replacing an entry keeps its counters: they follow the key, not the spec.
        assert!(builder.insert(EntrySpec::port(pso(3), pso(103), 2)));
        let second = builder.build(Some(&first));

        let carried = second.get(pso(1)).expect("carried");
        assert_eq!(carried.binds.load(Ordering::Relaxed), 41);
        assert_eq!(carried.draws.load(Ordering::Relaxed), 7);
        assert_eq!(carried.redirect, pso(101));
        let replaced = second.get(pso(3)).expect("replaced");
        assert_eq!(replaced.binds.load(Ordering::Relaxed), 5);
        assert_eq!(replaced.redirect, pso(103));
        assert!(second.get(pso(2)).is_none());
        assert_eq!(
            second
                .get(pso(4))
                .expect("new")
                .binds
                .load(Ordering::Relaxed),
            0
        );
        // The first table is untouched.
        assert!(first.get(pso(2)).is_some());
        assert_eq!(first.get(pso(3)).expect("old").redirect, 0);
    }

    #[test]
    fn from_table_keeps_live_flags() {
        let table = table_of(&[EntrySpec::new(pso(1))]);
        table
            .get(pso(1))
            .expect("entry")
            .flags
            .store(flag::HIDE, Ordering::Relaxed);
        let builder = Builder::from_table(Some(&table));
        assert_eq!(builder.get(pso(1)).map(|s| s.flags), Some(flag::HIDE));
    }

    #[test]
    fn only_behaviour_changing_entries_hold_references() {
        assert!(!EntrySpec::new(pso(1)).holds_reference());
        assert!(EntrySpec::port(pso(1), pso(2), 0).holds_reference());
        for bit in [flag::HIDE, flag::BLINK, flag::MASK] {
            let spec = EntrySpec {
                flags: bit,
                ..EntrySpec::new(pso(1))
            };
            assert!(spec.holds_reference(), "{bit}");
        }
        let main_view = EntrySpec {
            flags: flag::MAIN_VIEW_RT,
            ..EntrySpec::new(pso(1))
        };
        assert!(!main_view.holds_reference());
        let masked = EntrySpec {
            mask_variant: pso(9),
            ..EntrySpec::new(pso(1))
        };
        assert!(masked.holds_reference());
    }

    #[test]
    fn diff_takes_releases_and_retires_what_changed() {
        let first = table_of(&[EntrySpec::port(pso(1), pso(101), 0), EntrySpec::new(pso(2))]);
        assert_eq!(
            diff(None, &first),
            Diff {
                add_ref: vec![pso(1)],
                release: vec![],
                retire: vec![],
            }
        );

        // A new variant for the same key: the old variant retires, the key's reference stays.
        let mut builder = Builder::from_table(Some(&first));
        builder.insert(EntrySpec::port(pso(1), pso(201), 0));
        // A Hunt-only entry becomes hidden: it now holds a reference.
        builder.insert(EntrySpec {
            flags: flag::HIDE,
            ..EntrySpec::new(pso(2))
        });
        let second = builder.build(Some(&first));
        assert_eq!(
            diff(Some(&first), &second),
            Diff {
                add_ref: vec![pso(2)],
                release: vec![],
                retire: vec![pso(101)],
            }
        );

        // The port is dropped: its reference is released and its variant retires.
        let mut builder = Builder::from_table(Some(&second));
        builder.remove(pso(1));
        let third = builder.build(Some(&second));
        assert_eq!(
            diff(Some(&second), &third),
            Diff {
                add_ref: vec![],
                release: vec![pso(1)],
                retire: vec![pso(201)],
            }
        );
        assert_eq!(diff(Some(&third), &third), Diff::default());
    }

    #[test]
    fn the_reaper_drops_on_a_release_count_of_one_and_keeps_otherwise() {
        assert_eq!(reap_decision(1), Reap::Drop);
        for count in [2, 3, 17, u32::MAX] {
            assert_eq!(reap_decision(count), Reap::Keep, "{count}");
        }
        assert_eq!(reap_decision(0), Reap::Lost);
    }

    #[test]
    fn the_reaper_probes_only_entries_holding_a_reference() {
        let table = table_of(&[
            EntrySpec::port(pso(1), pso(101), 0),
            EntrySpec::port(pso(2), pso(102), 1),
            EntrySpec::port(pso(3), pso(103), 2),
            EntrySpec::new(pso(4)),
        ]);
        let counts = HashMap::from([(pso(1), 1), (pso(2), 3), (pso(3), 0)]);
        let mut probed = Vec::new();
        let plan = reap_plan(&table, |key| {
            probed.push(key);
            counts[&key]
        });
        probed.sort_unstable();
        assert_eq!(probed, vec![pso(1), pso(2), pso(3)]);
        assert_eq!(
            plan,
            ReapPlan {
                dropped: vec![pso(1)],
                lost: vec![pso(3)],
            }
        );
    }

    #[test]
    fn the_reaper_runs_every_10_seconds() {
        assert!(!reap_due(0, 9_999));
        assert!(reap_due(0, 10_000));
        assert!(!reap_due(20_000, 25_000));
        // A clock behind the last run is not due.
        assert!(!reap_due(20_000, 1_000));
    }
}
