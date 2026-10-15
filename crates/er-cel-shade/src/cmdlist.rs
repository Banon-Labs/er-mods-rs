//! The command-list detours: where a cel variant is bound in its game pipeline's place (design
//! 4.2), and where the probe is copied back to the CPU (design 5.3).
//!
//! # The redirect
//!
//! The game's pipelines are created exactly as it describes them; a port's cel variant is a second
//! pipeline beside it ([`crate::hooks`]). The swap happens when a pipeline is bound: the detours on
//! `SetPipelineState`, `Reset`, `ClearState` and `ID3D12Device::CreateCommandList` look the
//! pipeline the game passes up in [`crate::table`] and pass the variant on instead when [`decide`]
//! says so and the list can be tracked. GX calls `SetPipelineState` whenever its pipeline is dirty,
//! so a redirect per pipeline sees every bind.
//!
//! Each detour makes its decision inside `catch_unwind` and calls the original exactly once,
//! outside it. A panic binds what the game asked for and trips [`VANILLA`] and [`HOOK_FAULTED`]
//! ([`trip_vanilla`]); the module's panic hook has already logged where it came from. A panic that
//! unwound out of an `extern "system"` function would abort the game instead.
//!
//! # The gates
//!
//! [`decide`] redirects only while [`ACTIVE`] is set (some published entry has a variant), while
//! [`VANILLA`] is clear, while [`STAGE_ACTIVE`] holds the entry's stage bit ([`stage_bit`]) -- a
//! stage with no content binds the game's own pipeline -- and while [`PORTS_OFF`] does not hold the
//! entry's port kind: a port the fidelity test found unfaithful, or in a fidelity test every port
//! the test is not about. This module only reads [`STAGE_ACTIVE`], [`PORTS_OFF`] and [`VANILLA`];
//! the menu, the exports and the panic chord write them. [`ACTIVE`] follows the table
//! ([`refresh_active`]).
//!
//! # List slots
//!
//! A D3D12 command list is recorded by one thread at a time, so what this module remembers about a
//! list in progress lives in a thread-local array of [`LIST_SLOTS`] slots keyed by the list
//! pointer ([`ListSlots`]): the pipeline the game bound, the one actually bound, the root
//! signature's [`Binding`], which port kinds had their variant bound, whether the recording owes
//! the probe its barriers back, and the present it was last used in.
//!
//! A slot is freed at its list's `Close` on the same thread. One not used for [`STALE_PRESENTS`]
//! presents is free as well -- its list was closed on another thread -- and when no slot is free,
//! the one used longest ago that owes the probe nothing is taken. A slot that still owed the probe
//! when it went stale is counted as a miss. A bind that finds no slot binds the game's own
//! pipeline, because a variant is only ever bound where its barrier can be paid back at `Close`:
//! a miss means no redirect and no probe copy for that list, and misses are counted.
//!
//! # The probe's states
//!
//! The probe starts in `COMMON` and is returned to it by every recording that touches it, so each
//! barrier's "before" state is true by construction rather than resting on what a draw promoted:
//!
//! 1. the first redirect of a recording records `COMMON -> UNORDERED_ACCESS` into its list, before
//!    the variant is bound, and marks the slot ([`ListSlot::probe_owed`]);
//! 2. that list's `Close` records `UNORDERED_ACCESS -> COPY_SOURCE`, a `CopyBufferRegion` into slot
//!    `present % 8` of the readback ring, and `COPY_SOURCE -> COMMON`.
//!
//! A recording that bound a variant and never drew with it still ends where it started. Barriers
//! are not allowed in a bundle, so a bundle, which the game never creates, is never redirected. A
//! barrier recorded at bind time would also be refused inside a render pass; the static scan of the
//! GX backend found no `BeginRenderPass`, and the debug layer checks the sequence in gate W1.
//!
//! # Cost
//!
//! About 1,760 `SetPipelineState` and 105 `Reset` and `Close` calls a frame go through here on 9
//! recording threads (census `br-20261014-230719-4ec7`). None of these paths takes a lock,
//! allocates or logs, and the counters a hook bumps on most calls are thread-local, added to the
//! shared ones on a timed call. One call in [`SAMPLE_EVERY`] per thread is timed with the
//! performance counter, whatever is added to the call besides the original, into one sum per
//! [`Timing`].
//!
//! [`decide`], [`stage_bit`] and [`ListSlots`] are pure and tested on the host. The Windows half
//! holds the detours.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use crate::look_binding::Binding;
use crate::replace::PortKind;
use crate::table::Entry;

/// Some published table entry has a variant: without one there is nothing to redirect, and the
/// detours return before the table lookup. Kept by [`refresh_active`].
pub static ACTIVE: AtomicBool = AtomicBool::new(false);

/// Bind every pipeline as the game asked. Set by a panic caught in a detour ([`trip_vanilla`]);
/// otherwise written by the panic chord, the mode export and the menu's Vanilla checkbox.
pub static VANILLA: AtomicBool = AtomicBool::new(false);

/// A detour caught a panic. Never cleared: the session's ports stay suspect.
pub static HOOK_FAULTED: AtomicBool = AtomicBool::new(false);

/// Which detours caught a panic, one [`Detour::bit`] each.
pub static FAULTS: AtomicU32 = AtomicU32::new(0);

/// The stages with content, one [`crate::replace::Stage::bit`] each: a cel knob of the stage away
/// from its off value, an enabled layer there, or (from P2) the mask resolve. Staged by the menu at
/// start-up and then once per present, and by the mode export.
pub static STAGE_ACTIVE: AtomicU32 = AtomicU32::new(0);

/// The port kinds bound as the game asked whatever their stage holds, one [`PortKind::bit`] each:
/// in normal use the ports the fidelity test marked unfaithful, and during a fidelity test every
/// port the test is not about (design 14, P0.1 item 5). Staged with [`STAGE_ACTIVE`].
pub static PORTS_OFF: AtomicU32 = AtomicU32::new(0);

/// One call in this many per thread is timed.
pub const SAMPLE_EVERY: u32 = 256;

/// A timed call that took longer than this was preempted, and is counted apart rather than added
/// to its timing's sum: one scheduler slice inside a sample is milliseconds, so on a loaded
/// machine a handful of them is the whole mean. Measured 2026-10-14 at a load average of 31 on 16
/// cores: `SetPipelineState`, `Reset` and `Close`, which do different amounts of work, all read
/// 173 to 187 ns with these samples included.
pub const PREEMPTED_NS: u64 = 20_000;

/// Empty readings the timer floor is the mean of. A mean, not a minimum: the performance counter
/// ticks every 100 ns under Wine and on most Windows machines, so the least of a few readings of
/// one 30 ns call is 0, while their mean is the call's cost.
pub const FLOOR_TRIES: u32 = 4096;

/// The mean of `readings` at or under [`PREEMPTED_NS`], or 0 when there are none.
#[must_use]
pub fn floor_mean(readings: impl Iterator<Item = u64>) -> u64 {
    let (sum, count) = readings
        .filter(|&ns| ns <= PREEMPTED_NS)
        .fold((0_u64, 0_u64), |(sum, count), ns| {
            (sum.saturating_add(ns), count + 1)
        });
    sum.checked_div(count).unwrap_or(0)
}

/// Is the thread's `tick`th detour call one to time?
#[must_use]
pub fn sampled(tick: u32) -> bool {
    tick.is_multiple_of(SAMPLE_EVERY)
}

/// The detours this module installs, and the root-signature note it is handed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Detour {
    SetPipelineState = 0,
    Reset = 1,
    ClearState = 2,
    Close = 3,
    CreateCommandList = 4,
    /// The slot note in `look_binding`'s `SetGraphicsRootSignature` detour.
    SetRootSignature = 5,
}

impl Detour {
    pub const COUNT: usize = 6;
    pub const ALL: [Self; Self::COUNT] = [
        Self::SetPipelineState,
        Self::Reset,
        Self::ClearState,
        Self::Close,
        Self::CreateCommandList,
        Self::SetRootSignature,
    ];

    /// This detour's bit in [`FAULTS`].
    #[must_use]
    pub const fn bit(self) -> u32 {
        1 << self as u32
    }

    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::SetPipelineState => "SetPipelineState",
            Self::Reset => "Reset",
            Self::ClearState => "ClearState",
            Self::Close => "Close",
            Self::CreateCommandList => "CreateCommandList",
            Self::SetRootSignature => "SetGraphicsRootSignature",
        }
    }
}

/// What a timed call's cost is added to. The detours that run on every recording thread, with a
/// `Close` that records the probe copy apart from one that does not, because design 12 budgets
/// them apart: 40 ns for a plain `Close` against 2 us for one that records the copy.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Timing {
    SetPipelineState = 0,
    Reset = 1,
    ClearState = 2,
    Close = 3,
    CloseProbeCopy = 4,
}

impl Timing {
    pub const COUNT: usize = 5;
    pub const ALL: [Self; Self::COUNT] = [
        Self::SetPipelineState,
        Self::Reset,
        Self::ClearState,
        Self::Close,
        Self::CloseProbeCopy,
    ];

    /// The name the telemetry's `hooks` entries carry, and `scripts/check-shader-editor-probe.py
    /// --expect cost` reads.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::SetPipelineState => "SetPipelineState",
            Self::Reset => "Reset",
            Self::ClearState => "ClearState",
            Self::Close => "Close",
            Self::CloseProbeCopy => "CloseProbeCopy",
        }
    }
}

/// The detours named by a [`FAULTS`] value.
pub fn faulted(bits: u32) -> impl Iterator<Item = Detour> {
    Detour::ALL
        .into_iter()
        .filter(move |detour| bits & detour.bit() != 0)
}

/// A detour caught a panic: bind the game's own pipelines from now on. Only atomics, so it is safe
/// to call from any detour; the panic hook has already written the message and location to the
/// log, and the worker reports [`FAULTS`].
pub fn trip_vanilla(detour: Detour) {
    FAULTS.fetch_or(detour.bit(), Ordering::Relaxed);
    HOOK_FAULTED.store(true, Ordering::Release);
    VANILLA.store(true, Ordering::Release);
}

/// The [`crate::replace::Stage::bit`] of a table entry's `port` byte, or 0 when it names no port.
#[must_use]
pub const fn stage_bit(port: u8) -> u32 {
    match PortKind::from_u8(port) {
        Some(kind) => kind.stage().bit(),
        None => 0,
    }
}

/// What a bind does with the pipeline the game passed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    /// Bind it: it is no port, or nothing is being redirected.
    Pass,
    /// A port's own pipeline, bound as the game asked: its stage has no content, its kind is off,
    /// or it has no variant.
    PortPassed { kind: PortKind },
    /// Bind the port's cel variant in its place.
    Redirect { variant: usize, kind: PortKind },
}

impl Decision {
    /// The pipeline bound in place of `game` when the list can carry the redirect.
    #[must_use]
    pub fn bound(self, game: usize) -> usize {
        match self {
            Self::Redirect { variant, .. } => variant,
            Self::Pass | Self::PortPassed { .. } => game,
        }
    }

    /// The same bind with its redirect withdrawn: the port's own pipeline instead of its variant.
    #[must_use]
    pub fn withdrawn(self) -> Self {
        match self {
            Self::Redirect { kind, .. } => Self::PortPassed { kind },
            other => other,
        }
    }
}

/// The redirect decision for one bind: `entry` is the published table's entry for the game's
/// pipeline, if it has one.
///
/// A redirect needs all of: [`ACTIVE`] (`active`), not [`VANILLA`] (`vanilla`), an entry that is a
/// port and has a variant, its stage's bit in [`STAGE_ACTIVE`] (`stage_active`), and its kind's
/// bit clear in [`PORTS_OFF`] (`ports_off`). The detours return [`Decision::Pass`] before the
/// lookup when `active` is clear or `vanilla` set, which is the answer this gives for those too.
#[must_use]
pub fn decide(
    entry: Option<&Entry>,
    active: bool,
    vanilla: bool,
    stage_active: u32,
    ports_off: u32,
) -> Decision {
    if !active || vanilla {
        return Decision::Pass;
    }
    let Some(entry) = entry else {
        return Decision::Pass;
    };
    // Hunt and family entries (P2, P3) carry no port; their binds are never redirected.
    let Some(kind) = PortKind::from_u8(entry.port) else {
        return Decision::Pass;
    };
    if entry.redirect != 0
        && stage_active & stage_bit(entry.port) != 0
        && ports_off & u32::from(kind.bit()) == 0
    {
        Decision::Redirect {
            variant: entry.redirect,
            kind,
        }
    } else {
        Decision::PortPassed { kind }
    }
}

/// Slots per thread.
pub const LIST_SLOTS: usize = 8;

/// A slot not used for this many presents is free: its list was closed on another thread, or
/// released without a `Close`. A list being recorded binds on every frame it records in.
pub const STALE_PRESENTS: u64 = 2;

/// What is remembered about one command list while it records.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ListSlot {
    /// The list, as the pointer its methods are called with. 0 marks a free slot.
    pub list: usize,
    /// The pipeline the game last bound.
    pub game_pso: usize,
    /// The pipeline actually bound for it: its variant, or the same pipeline.
    pub bound_pso: usize,
    /// Where the editor's parameters sit in the root signature the list last set.
    pub binding: Binding,
    /// The port kinds whose variant this recording bound, one [`PortKind::bit`] each.
    pub port_bound_kinds: u8,
    /// This recording took the probe from `COMMON` to unordered access with a barrier of its own,
    /// at its first redirect. Its `Close` then owes the copy and the barriers back to `COMMON`.
    pub probe_owed: bool,
    /// The present count when the slot was last claimed or used.
    pub touched: u64,
}

impl ListSlot {
    pub const EMPTY: Self = Self {
        list: 0,
        game_pso: 0,
        bound_pso: 0,
        binding: Binding::NONE,
        port_bound_kinds: 0,
        probe_owed: false,
        touched: 0,
    };

    fn stale(&self, now: u64) -> bool {
        self.list != 0 && now.saturating_sub(self.touched) >= STALE_PRESENTS
    }
}

/// What one bind did: the pipeline that goes to the runtime, and whether the probe's barrier to
/// unordered access has to be recorded into the list first.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bind {
    pub bound: usize,
    pub barrier: bool,
}

/// One thread's slots: a list's slot is found by its pointer, claimed when the list is first seen,
/// and freed at its `Close` or once stale. Every update reports a miss as `None` or `false`: a null
/// list, or no slot to be had because every slot is in use and owes the probe its barriers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ListSlots {
    slots: [ListSlot; LIST_SLOTS],
    /// The slot found last. A thread usually records one list at a time, so a bind finds its slot
    /// at the first look.
    last: usize,
    /// Slots that went stale while their recording still owed the probe, not yet counted.
    lost: u32,
}

impl Default for ListSlots {
    fn default() -> Self {
        Self::new()
    }
}

impl ListSlots {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            slots: [ListSlot::EMPTY; LIST_SLOTS],
            last: 0,
            lost: 0,
        }
    }

    fn index_of(&self, list: usize) -> Option<usize> {
        if list == 0 {
            return None;
        }
        if self
            .slots
            .get(self.last)
            .is_some_and(|slot| slot.list == list)
        {
            return Some(self.last);
        }
        self.slots.iter().position(|slot| slot.list == list)
    }

    /// Where a list with no slot would go: a free slot, else the stalest stale one, else the one
    /// used longest ago that owes the probe nothing. `None` when every slot is in use and owes it.
    fn place(&self, now: u64) -> Option<usize> {
        if let Some(free) = self.slots.iter().position(|slot| slot.list == 0) {
            return Some(free);
        }
        let oldest = |candidates: &mut dyn Iterator<Item = (usize, &ListSlot)>| {
            candidates
                .min_by_key(|(_, slot)| slot.touched)
                .map(|(at, _)| at)
        };
        oldest(&mut self.slots.iter().enumerate().filter(|(_, s)| s.stale(now)))
            .or_else(|| oldest(&mut self.slots.iter().enumerate().filter(|(_, s)| !s.probe_owed)))
    }

    /// Start slot `at` over for `list`. A recording that still owed the probe is lost.
    fn restart(&mut self, at: usize, list: usize, now: u64) {
        if let Some(slot) = self.slots.get_mut(at) {
            if slot.probe_owed {
                self.lost = self.lost.saturating_add(1);
            }
            *slot = ListSlot {
                list,
                touched: now,
                ..ListSlot::EMPTY
            };
        }
    }

    /// The list's slot, claimed fresh if it has none or its own has gone stale.
    fn slot_for(&mut self, list: usize, now: u64) -> Option<&mut ListSlot> {
        if list == 0 {
            return None;
        }
        let at = match self.index_of(list) {
            Some(at) => {
                if self.slots.get(at).is_some_and(|slot| slot.stale(now)) {
                    self.restart(at, list, now);
                }
                at
            }
            None => {
                let at = self.place(now)?;
                self.restart(at, list, now);
                at
            }
        };
        self.last = at;
        let slot = self.slots.get_mut(at)?;
        slot.touched = now;
        Some(slot)
    }

    /// The list's slot, if it has one.
    #[must_use]
    pub fn get(&self, list: usize) -> Option<&ListSlot> {
        self.slots.get(self.index_of(list)?)
    }

    /// Slots in use.
    #[must_use]
    pub fn len(&self) -> usize {
        self.slots.iter().filter(|slot| slot.list != 0).count()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Could `list` be given a slot now?
    #[must_use]
    pub fn can_take(&self, list: usize, now: u64) -> bool {
        list != 0 && (self.index_of(list).is_some() || self.place(now).is_some())
    }

    /// Could a list not yet seen be given a slot now?
    #[must_use]
    pub fn has_room(&self, now: u64) -> bool {
        self.place(now).is_some()
    }

    /// Hand back the count of recordings lost since the last call, and start it over.
    pub fn take_lost(&mut self) -> u32 {
        std::mem::take(&mut self.lost)
    }

    /// `SetPipelineState`: the game bound `game` and `decision` says what to do with it. A redirect
    /// goes through only in a list that already owes the probe, or one `direct` says can take the
    /// barrier, which the caller then records before the bind. `None` for a miss, which binds
    /// `game`.
    pub fn set_pipeline(
        &mut self,
        list: usize,
        game: usize,
        decision: Decision,
        now: u64,
        direct: impl FnOnce() -> bool,
    ) -> Option<Bind> {
        let slot = self.slot_for(list, now)?;
        let mut bind = Bind {
            bound: game,
            barrier: false,
        };
        if let Decision::Redirect { variant, kind } = decision
            && (slot.probe_owed || direct())
        {
            bind.barrier = !slot.probe_owed;
            bind.bound = variant;
            slot.probe_owed = true;
            slot.port_bound_kinds |= kind.bit();
        }
        slot.game_pso = game;
        slot.bound_pso = bind.bound;
        Some(bind)
    }

    /// `ClearState`: [`ListSlots::set_pipeline`] for the pipeline it sets, and the root signature
    /// becomes none. What this recording already owes the probe stays owed.
    pub fn clear_state(
        &mut self,
        list: usize,
        game: usize,
        decision: Decision,
        now: u64,
        direct: impl FnOnce() -> bool,
    ) -> Option<Bind> {
        let bind = self.set_pipeline(list, game, decision, now, direct)?;
        if let Some(slot) = self.slot_for(list, now) {
            slot.binding = Binding::NONE;
        }
        Some(bind)
    }

    /// `Reset`, and a list created recording: a new recording starts, so the slot starts over,
    /// keyed to the list again, with `bound` as its initial pipeline. `redirected` names the kind
    /// when `bound` is a variant; the slot then owes the probe, and the caller records the barrier
    /// at once. `false` for a miss.
    pub fn reset(
        &mut self,
        list: usize,
        game: usize,
        bound: usize,
        redirected: Option<PortKind>,
        now: u64,
    ) -> bool {
        if list == 0 {
            return false;
        }
        let at = match self.index_of(list).or_else(|| self.place(now)) {
            Some(at) => at,
            None => return false,
        };
        self.restart(at, list, now);
        self.last = at;
        let Some(slot) = self.slots.get_mut(at) else {
            return false;
        };
        slot.game_pso = game;
        slot.bound_pso = bound;
        if let Some(kind) = redirected {
            slot.probe_owed = true;
            slot.port_bound_kinds = kind.bit();
        }
        true
    }

    /// `SetGraphicsRootSignature`.
    pub fn set_root_signature(&mut self, list: usize, binding: Binding, now: u64) -> bool {
        let Some(slot) = self.slot_for(list, now) else {
            return false;
        };
        slot.binding = binding;
        true
    }

    /// A list was just created at `list` with nothing to remember: drop any slot left at that
    /// address by a list released before its `Close`, so the new one cannot inherit it.
    pub fn forget(&mut self, list: usize) {
        if let Some(at) = self.index_of(list)
            && let Some(slot) = self.slots.get_mut(at)
        {
            *slot = ListSlot::EMPTY;
        }
    }

    /// `Close`: free the list's slot and hand back what it held. `None` when this thread has no
    /// slot for the list.
    pub fn close(&mut self, list: usize) -> Option<ListSlot> {
        let at = self.index_of(list)?;
        Some(std::mem::replace(self.slots.get_mut(at)?, ListSlot::EMPTY))
    }
}

#[cfg(windows)]
pub use process::{
    CLEAR_STATE, CLEAR_STATE_WITH_PSO, CLOSE, CLOSES_UNMATCHED, CREATE_COMMAND_LIST,
    CREATE_LIST_WITH_PSO, HOOK_NS_SAMPLES, HOOK_NS_SUM, HOOK_PREEMPTED, LIST_SLOT_MISSES,
    PASSTHROUGH_PORT_BINDS, PROBE_BARRIERS, PROBE_COPIES, PROBE_COPIES_SKIPPED, REDIRECTED_BINDS,
    RESET, RESET_WITH_PSO, SET_PIPELINE_STATE, SET_PSO_CALLS, clear_state_hook, close_hook,
    create_command_list_hook, hook_ns_totals, note_root_signature, refresh_active, reset_hook,
    set_pipeline_state_hook, timer_floor_ns,
};

/// The detours, the thread-local slots and the counters.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::cell::{Cell, RefCell};
    use std::mem::ManuallyDrop;
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
    use std::time::{Duration, Instant};

    use windows::Win32::Foundation::E_FAIL;
    use windows::Win32::Graphics::Direct3D12::{
        D3D12_COMMAND_LIST_TYPE, D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_RESOURCE_BARRIER,
        D3D12_RESOURCE_BARRIER_0, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES,
        D3D12_RESOURCE_BARRIER_FLAG_NONE, D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
        D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_SOURCE,
        D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATES,
        D3D12_RESOURCE_TRANSITION_BARRIER, ID3D12GraphicsCommandList_Vtbl, ID3D12Resource,
    };
    use windows::core::{GUID, HRESULT, Interface};

    use super::{
        ACTIVE, Bind, Decision, Detour, ListSlot, ListSlots, PORTS_OFF, STAGE_ACTIVE, Timing,
        VANILLA, decide, sampled, trip_vanilla,
    };
    use crate::look_binding::Binding;
    use crate::{params, probe, table};

    type SetPipelineStateFn = unsafe extern "system" fn(*mut c_void, *mut c_void);
    type ResetFn = unsafe extern "system" fn(*mut c_void, *mut c_void, *mut c_void) -> HRESULT;
    type ClearStateFn = unsafe extern "system" fn(*mut c_void, *mut c_void);
    type CloseFn = unsafe extern "system" fn(*mut c_void) -> HRESULT;
    type CreateCommandListFn = unsafe extern "system" fn(
        *mut c_void,
        u32,
        D3D12_COMMAND_LIST_TYPE,
        *mut c_void,
        *mut c_void,
        *const GUID,
        *mut *mut c_void,
    ) -> HRESULT;

    /// The trampolines MinHook returned, stored by `hooks` before each detour is enabled.
    pub static SET_PIPELINE_STATE: AtomicUsize = AtomicUsize::new(0);
    pub static RESET: AtomicUsize = AtomicUsize::new(0);
    pub static CLEAR_STATE: AtomicUsize = AtomicUsize::new(0);
    pub static CLOSE: AtomicUsize = AtomicUsize::new(0);
    pub static CREATE_COMMAND_LIST: AtomicUsize = AtomicUsize::new(0);

    /// Binds that went to a port's variant, from any detour. Exact: the vanilla proof reads its
    /// delta as 0.
    pub static REDIRECTED_BINDS: AtomicU64 = AtomicU64::new(0);
    /// Binds of a port's own pipeline while the redirect was open: its stage had no content, its
    /// kind was off, or its list could not carry the redirect.
    pub static PASSTHROUGH_PORT_BINDS: AtomicU64 = AtomicU64::new(0);
    /// `SetPipelineState` calls. Each thread adds its count at its timed calls, so this trails the
    /// truth by fewer than [`super::SAMPLE_EVERY`] calls per recording thread.
    pub static SET_PSO_CALLS: AtomicU64 = AtomicU64::new(0);
    /// Slot updates that found no slot, and recordings that went stale still owing the probe.
    /// Added at timed calls like [`SET_PSO_CALLS`], so it trails the same way.
    pub static LIST_SLOT_MISSES: AtomicU64 = AtomicU64::new(0);
    /// `Close` of a list this thread holds no slot for: one reset or recorded on another thread,
    /// or one that never bound a pipeline.
    pub static CLOSES_UNMATCHED: AtomicU64 = AtomicU64::new(0);
    /// Barriers taking the probe to unordered access, one per recording that redirected.
    pub static PROBE_BARRIERS: AtomicU64 = AtomicU64::new(0);
    /// Probe copies recorded at `Close`, and ones owed but skipped: no readback ring, or a list
    /// that is not a direct list.
    pub static PROBE_COPIES: AtomicU64 = AtomicU64::new(0);
    pub static PROBE_COPIES_SKIPPED: AtomicU64 = AtomicU64::new(0);
    /// Calls that passed a pipeline where most pass none, which the design leaves to be measured.
    pub static RESET_WITH_PSO: AtomicU64 = AtomicU64::new(0);
    pub static CLEAR_STATE_WITH_PSO: AtomicU64 = AtomicU64::new(0);
    pub static CREATE_LIST_WITH_PSO: AtomicU64 = AtomicU64::new(0);

    /// Nanoseconds and samples of the timed calls, per [`Timing`] (`Timing as usize`).
    pub static HOOK_NS_SUM: [AtomicU64; Timing::COUNT] =
        [const { AtomicU64::new(0) }; Timing::COUNT];
    pub static HOOK_NS_SAMPLES: [AtomicU64; Timing::COUNT] =
        [const { AtomicU64::new(0) }; Timing::COUNT];
    /// Timed calls over [`super::PREEMPTED_NS`], left out of the sums, per [`Timing`].
    pub static HOOK_PREEMPTED: [AtomicU64; Timing::COUNT] =
        [const { AtomicU64::new(0) }; Timing::COUNT];

    /// One recording thread's state.
    struct Local {
        slots: RefCell<ListSlots>,
        /// Detour calls on this thread, for the one-in-[`super::SAMPLE_EVERY`] timing.
        tick: Cell<u32>,
        /// `SetPipelineState` calls not yet added to [`SET_PSO_CALLS`].
        set_pso_pending: Cell<u32>,
        /// Misses not yet added to [`LIST_SLOT_MISSES`].
        misses_pending: Cell<u32>,
    }

    thread_local! {
        // `const` and free of destructors, so it is plain thread-local storage: no allocation on a
        // thread's first call and nothing to tear down at its exit.
        static LOCAL: Local = const {
            Local {
                slots: RefCell::new(ListSlots::new()),
                tick: Cell::new(0),
                set_pso_pending: Cell::new(0),
                misses_pending: Cell::new(0),
            }
        };
    }

    /// Run a detour's own work, binding `fallback` and tripping vanilla if it panics.
    fn guarded<T>(detour: Detour, fallback: T, work: impl FnOnce() -> T) -> T {
        catch_unwind(AssertUnwindSafe(work)).unwrap_or_else(|_| {
            trip_vanilla(detour);
            fallback
        })
    }

    fn nanos(elapsed: Duration) -> u64 {
        u64::try_from(elapsed.as_nanos()).unwrap_or(u64::MAX)
    }

    /// Add a thread's pending count to its shared counter, and start it over.
    fn flush(pending: &Cell<u32>, shared: &AtomicU64) {
        let count = pending.replace(0);
        if count != 0 {
            shared.fetch_add(u64::from(count), Ordering::Relaxed);
        }
    }

    /// The clock on one detour call. `Instant` is the performance counter on Windows.
    struct Clock {
        timing: Timing,
        started: Option<Instant>,
    }

    impl Clock {
        fn start(timing: Timing) -> Self {
            let started = LOCAL
                .try_with(|local| {
                    if timing == Timing::SetPipelineState {
                        local
                            .set_pso_pending
                            .set(local.set_pso_pending.get().wrapping_add(1));
                    }
                    let tick = local.tick.get().wrapping_add(1);
                    local.tick.set(tick);
                    if !sampled(tick) {
                        return None;
                    }
                    flush(&local.set_pso_pending, &SET_PSO_CALLS);
                    flush(&local.misses_pending, &LIST_SLOT_MISSES);
                    Some(Instant::now())
                })
                .ok()
                .flatten();
            Self { timing, started }
        }

        fn stop(self) {
            let timing = self.timing;
            self.stop_as(timing);
        }

        /// Stop, adding the time to `timing` rather than the one the clock was started for.
        fn stop_as(self, timing: Timing) {
            if let Some(started) = self.started {
                let at = timing as usize;
                let ns = nanos(started.elapsed());
                if ns > super::PREEMPTED_NS {
                    HOOK_PREEMPTED[at].fetch_add(1, Ordering::Relaxed);
                    return;
                }
                HOOK_NS_SUM[at].fetch_add(ns, Ordering::Relaxed);
                HOOK_NS_SAMPLES[at].fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// The timed totals over every [`Timing`]: `(nanoseconds, samples)`.
    #[must_use]
    pub fn hook_ns_totals() -> (u64, u64) {
        HOOK_NS_SUM.iter().zip(&HOOK_NS_SAMPLES).fold(
            (0, 0),
            |(sum, samples), (timing_sum, timing_samples)| {
                (
                    sum + timing_sum.load(Ordering::Relaxed),
                    samples + timing_samples.load(Ordering::Relaxed),
                )
            },
        )
    }

    /// What timing an empty stretch of code reads: the mean of [`super::FLOOR_TRIES`] tries. The
    /// calibration a report subtracts from each sample's mean.
    #[must_use]
    pub fn timer_floor_ns() -> u64 {
        super::floor_mean((0..super::FLOOR_TRIES).map(|_| nanos(Instant::now().elapsed())))
    }

    /// One more miss on this thread.
    fn miss() {
        let counted = LOCAL
            .try_with(|local| {
                local
                    .misses_pending
                    .set(local.misses_pending.get().saturating_add(1));
            })
            .is_ok();
        if !counted {
            LIST_SLOT_MISSES.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// Run `update` on this thread's slots. `None` when they cannot be reached: the thread is
    /// exiting, or a hook re-entered while they were borrowed. Recordings the update found lost go
    /// to the pending misses.
    fn with_slots<T>(update: impl FnOnce(&mut ListSlots) -> T) -> Option<T> {
        LOCAL
            .try_with(|local| {
                let mut slots = local.slots.try_borrow_mut().ok()?;
                let result = update(&mut slots);
                let lost = slots.take_lost();
                if lost != 0 {
                    local
                        .misses_pending
                        .set(local.misses_pending.get().saturating_add(lost));
                }
                Some(result)
            })
            .ok()
            .flatten()
    }

    /// The present count the slots are stamped with.
    fn now() -> u64 {
        params::presents()
    }

    /// The decision for one bind of `game`. Returns before the table lookup when nothing is being
    /// redirected, which agrees with [`decide`].
    fn decision_for(game: usize) -> Decision {
        let active = ACTIVE.load(Ordering::Relaxed);
        let vanilla = VANILLA.load(Ordering::Relaxed);
        if game == 0 || !active || vanilla {
            return Decision::Pass;
        }
        decide(
            table::get(game),
            active,
            vanilla,
            STAGE_ACTIVE.load(Ordering::Relaxed),
            PORTS_OFF.load(Ordering::Relaxed),
        )
    }

    /// Count one bind of `game` that `decision` was made for and `bound` went to the runtime.
    fn count(decision: Decision, game: usize, bound: usize) {
        match decision {
            Decision::Pass => {}
            _ if bound != game => {
                REDIRECTED_BINDS.fetch_add(1, Ordering::Relaxed);
            }
            Decision::Redirect { .. } | Decision::PortPassed { .. } => {
                PASSTHROUGH_PORT_BINDS.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// The decision for a pipeline passed as a list's initial or cleared state.
    fn initial_decision(game: usize, passed: &AtomicU64) -> Decision {
        if game != 0 {
            passed.fetch_add(1, Ordering::Relaxed);
        }
        decision_for(game)
    }

    /// The pipeline a bind sends to the runtime, once [`ListSlots::set_pipeline`] or
    /// [`ListSlots::clear_state`] has answered: the barrier recorded first when the bind owes it,
    /// the game's own pipeline for a miss, and the bind counted.
    ///
    /// # Safety
    ///
    /// `list` must be a live graphics command list that is recording.
    unsafe fn apply(
        list: *mut c_void,
        game: usize,
        decision: Decision,
        bind: Option<Bind>,
    ) -> usize {
        let Some(bind) = bind else {
            miss();
            count(decision, game, game);
            return game;
        };
        if bind.barrier {
            // SAFETY: the caller's contract; `direct` said the list takes barriers.
            unsafe { record_probe_barrier(list) };
        }
        count(decision, game, bind.bound);
        bind.bound
    }

    /// The trampoline in `slot`, once it has been stored.
    fn original(slot: &AtomicUsize) -> Option<usize> {
        match slot.load(Ordering::Acquire) {
            0 => None,
            found => Some(found),
        }
    }

    /// Bind `pso` on `list` through the `SetPipelineState` trampoline, past this module's detour.
    ///
    /// # Safety
    ///
    /// `list` must be a live graphics command list that is recording.
    unsafe fn bind_past_the_detour(list: *mut c_void, pso: usize) {
        if let Some(original) = original(&SET_PIPELINE_STATE) {
            // SAFETY: the trampoline MinHook returned for this exact function.
            let original: SetPipelineStateFn = unsafe { std::mem::transmute(original) };
            unsafe { original(list, pso as *mut c_void) };
        }
    }

    /// Keep [`ACTIVE`] equal to "some published entry has a variant". Called after every publish
    /// that can add one; a publish that removes the last one leaves it set, which costs a lookup
    /// per bind and nothing else.
    pub fn refresh_active() {
        let any = table::published().is_some_and(|published| published.redirects() != 0);
        ACTIVE.store(any, Ordering::Relaxed);
    }

    /// `SetGraphicsRootSignature` set a root signature with `binding` on `list`.
    pub fn note_root_signature(list: usize, binding: Binding) {
        guarded(Detour::SetRootSignature, (), || {
            let noted = with_slots(|slots| slots.set_root_signature(list, binding, now()));
            if noted != Some(true) {
                miss();
            }
        });
    }

    /// The command list's vtable, for the methods this module calls but does not hook.
    ///
    /// # Safety
    ///
    /// `list` must be a live command list. A COM object's first field is its vtable, and every
    /// graphics command list version shares this layout for the methods called through it.
    unsafe fn vtable<'a>(list: *mut c_void) -> &'a ID3D12GraphicsCommandList_Vtbl {
        unsafe { &**(list as *const *const ID3D12GraphicsCommandList_Vtbl) }
    }

    /// Does `list` take barriers and copies? Only a direct list does among those a graphics
    /// pipeline is bound on; a bundle does not.
    ///
    /// # Safety
    ///
    /// `list` must be a live command list.
    unsafe fn is_direct(list: *mut c_void) -> bool {
        let vtable = unsafe { vtable(list) };
        let kind = unsafe { (vtable.base__.GetType)(list) };
        kind == D3D12_COMMAND_LIST_TYPE_DIRECT
    }

    /// `ID3D12GraphicsCommandList::SetPipelineState`.
    ///
    /// # Safety
    ///
    /// Installed as the detour of `SetPipelineState`; called by D3D12's callers with a live command
    /// list, never directly.
    pub unsafe extern "system" fn set_pipeline_state_hook(list: *mut c_void, pso: *mut c_void) {
        let game = pso as usize;
        let bound = guarded(Detour::SetPipelineState, game, || {
            let clock = Clock::start(Timing::SetPipelineState);
            let decision = decision_for(game);
            // SAFETY: `list` is the live list being recorded.
            let bind = with_slots(|slots| {
                slots.set_pipeline(list as usize, game, decision, now(), || unsafe {
                    is_direct(list)
                })
            })
            .flatten();
            let bound = unsafe { apply(list, game, decision, bind) };
            clock.stop();
            bound
        });
        let Some(original) = original(&SET_PIPELINE_STATE) else {
            return;
        };
        // SAFETY: the trampoline MinHook returned for this exact function.
        let original: SetPipelineStateFn = unsafe { std::mem::transmute(original) };
        unsafe { original(list, bound as *mut c_void) };
    }

    /// `ID3D12GraphicsCommandList::Reset`: a new recording, whose initial pipeline is mapped like a
    /// bind. A variant goes in only where its barrier can follow at once, in a direct list with a
    /// slot to keep the debt in; the barrier is recorded right after the reset, before any draw.
    ///
    /// # Safety
    ///
    /// Installed as the detour of `Reset`; called by D3D12's callers, never directly.
    pub unsafe extern "system" fn reset_hook(
        list: *mut c_void,
        allocator: *mut c_void,
        pso: *mut c_void,
    ) -> HRESULT {
        let game = pso as usize;
        let decision = guarded(Detour::Reset, Decision::Pass, || {
            let clock = Clock::start(Timing::Reset);
            let mut decision = initial_decision(game, &RESET_WITH_PSO);
            if matches!(decision, Decision::Redirect { .. }) {
                let room = with_slots(|slots| slots.can_take(list as usize, now()));
                // SAFETY: `list` is a live list, closed or recording.
                if room != Some(true) || !unsafe { is_direct(list) } {
                    decision = decision.withdrawn();
                }
            }
            clock.stop();
            decision
        });
        let bound = decision.bound(game);
        let Some(original) = original(&RESET) else {
            return E_FAIL;
        };
        // SAFETY: the trampoline MinHook returned for this exact function.
        let original: ResetFn = unsafe { std::mem::transmute(original) };
        let result = unsafe { original(list, allocator, bound as *mut c_void) };
        // A refused reset leaves the list as it was, recording included, and bound nothing.
        if result.is_ok() {
            guarded(Detour::Reset, (), || {
                let redirected = match decision {
                    Decision::Redirect { kind, .. } => Some(kind),
                    _ => None,
                };
                let kept =
                    with_slots(|slots| slots.reset(list as usize, game, bound, redirected, now()))
                        == Some(true);
                // SAFETY: `list` was just reset, so it is recording.
                unsafe { start_recording(list, game, decision, bound, kept) };
            });
        }
        result
    }

    /// A list just started recording with `bound` as its pipeline, and `kept` says whether its
    /// slot holds it: record the probe's barrier when `bound` is a variant, or put the game's own
    /// pipeline back when the slot could not be kept after all. Count the bind.
    ///
    /// # Safety
    ///
    /// `list` must be a live command list that is recording, and a direct one when `bound` is a
    /// variant.
    unsafe fn start_recording(
        list: *mut c_void,
        game: usize,
        decision: Decision,
        bound: usize,
        kept: bool,
    ) {
        if !kept {
            miss();
        }
        let redirected = bound != game;
        if redirected && kept {
            unsafe { record_probe_barrier(list) };
        } else if redirected {
            // Checked before the call and lost since: a variant never stays bound without its
            // barrier.
            unsafe { bind_past_the_detour(list, game) };
        }
        count(decision, game, if kept { bound } else { game });
    }

    /// `ID3D12GraphicsCommandList::ClearState`.
    ///
    /// # Safety
    ///
    /// Installed as the detour of `ClearState`; called by D3D12's callers, never directly.
    pub unsafe extern "system" fn clear_state_hook(list: *mut c_void, pso: *mut c_void) {
        let game = pso as usize;
        let bound = guarded(Detour::ClearState, game, || {
            let clock = Clock::start(Timing::ClearState);
            let decision = initial_decision(game, &CLEAR_STATE_WITH_PSO);
            // SAFETY: `list` is the live list being recorded.
            let bind = with_slots(|slots| {
                slots.clear_state(list as usize, game, decision, now(), || unsafe {
                    is_direct(list)
                })
            })
            .flatten();
            // The barrier may precede `ClearState`, which leaves resource states alone.
            let bound = unsafe { apply(list, game, decision, bind) };
            clock.stop();
            bound
        });
        let Some(original) = original(&CLEAR_STATE) else {
            return;
        };
        // SAFETY: the trampoline MinHook returned for this exact function.
        let original: ClearStateFn = unsafe { std::mem::transmute(original) };
        unsafe { original(list, bound as *mut c_void) };
    }

    /// `ID3D12Device::CreateCommandList`: the list is created recording, with its initial pipeline
    /// mapped like a bind. As at `Reset`, a variant goes in only on a direct list with a slot free
    /// for it, and its barrier is recorded on the new list at once.
    ///
    /// # Safety
    ///
    /// Installed as the detour of `CreateCommandList`; called by D3D12's callers, never directly.
    pub unsafe extern "system" fn create_command_list_hook(
        device: *mut c_void,
        node_mask: u32,
        kind: D3D12_COMMAND_LIST_TYPE,
        allocator: *mut c_void,
        initial: *mut c_void,
        riid: *const GUID,
        list: *mut *mut c_void,
    ) -> HRESULT {
        let game = initial as usize;
        let decision = guarded(Detour::CreateCommandList, Decision::Pass, || {
            let decision = initial_decision(game, &CREATE_LIST_WITH_PSO);
            if matches!(decision, Decision::Redirect { .. })
                && (kind != D3D12_COMMAND_LIST_TYPE_DIRECT
                    || with_slots(|slots| slots.has_room(now())) != Some(true))
            {
                return decision.withdrawn();
            }
            decision
        });
        let bound = decision.bound(game);
        let Some(original) = original(&CREATE_COMMAND_LIST) else {
            return E_FAIL;
        };
        // SAFETY: the trampoline MinHook returned for this exact function.
        let original: CreateCommandListFn = unsafe { std::mem::transmute(original) };
        let result = unsafe {
            original(
                device,
                node_mask,
                kind,
                allocator,
                bound as *mut c_void,
                riid,
                list,
            )
        };
        if result.is_ok() && !list.is_null() && !unsafe { *list }.is_null() {
            // Every command-list interface shares the one object pointer its methods are called
            // with.
            let created = unsafe { *list };
            guarded(Detour::CreateCommandList, (), || match decision {
                Decision::Redirect { kind: port, .. } => {
                    let kept = with_slots(|slots| {
                        slots.reset(created as usize, game, bound, Some(port), now())
                    }) == Some(true);
                    // SAFETY: a direct list, created recording.
                    unsafe { start_recording(created, game, decision, bound, kept) };
                }
                other => {
                    // A slot left at this address by a list released before its `Close` must not
                    // pass its debt to this one; any other list claims a slot at its first bind.
                    with_slots(|slots| slots.forget(created as usize));
                    count(other, game, game);
                }
            });
        }
        result
    }

    /// `ID3D12GraphicsCommandList::Close`: free the list's slot, and first pay the probe back when
    /// the recording owes it: the copy into this present's readback slot and the barriers back to
    /// `COMMON`.
    ///
    /// # Safety
    ///
    /// Installed as the detour of `Close`; called by D3D12's callers with a live command list,
    /// never directly.
    pub unsafe extern "system" fn close_hook(list: *mut c_void) -> HRESULT {
        guarded(Detour::Close, (), || {
            let clock = Clock::start(Timing::Close);
            let slot = with_slots(|slots| slots.close(list as usize)).flatten();
            let copied = match slot {
                // SAFETY: `list` is the live list being closed, still recording.
                Some(ListSlot {
                    probe_owed: true, ..
                }) => unsafe { pay_probe_back(list) },
                Some(_) => false,
                None => {
                    CLOSES_UNMATCHED.fetch_add(1, Ordering::Relaxed);
                    false
                }
            };
            clock.stop_as(if copied {
                Timing::CloseProbeCopy
            } else {
                Timing::Close
            });
        });
        let Some(original) = original(&CLOSE) else {
            return E_FAIL;
        };
        // SAFETY: the trampoline MinHook returned for this exact function.
        let original: CloseFn = unsafe { std::mem::transmute(original) };
        unsafe { original(list) }
    }

    /// A transition barrier on a resource this DLL holds. The resource is borrowed: the field is
    /// `ManuallyDrop`, so the barrier neither takes nor releases a reference.
    ///
    /// # Safety
    ///
    /// `resource` must be a live `ID3D12Resource*`.
    unsafe fn transition(
        resource: usize,
        before: D3D12_RESOURCE_STATES,
        after: D3D12_RESOURCE_STATES,
    ) -> D3D12_RESOURCE_BARRIER {
        D3D12_RESOURCE_BARRIER {
            Type: D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
            Flags: D3D12_RESOURCE_BARRIER_FLAG_NONE,
            Anonymous: D3D12_RESOURCE_BARRIER_0 {
                Transition: ManuallyDrop::new(D3D12_RESOURCE_TRANSITION_BARRIER {
                    pResource: ManuallyDrop::new(Some(unsafe {
                        ID3D12Resource::from_raw(resource as *mut c_void)
                    })),
                    Subresource: D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES,
                    StateBefore: before,
                    StateAfter: after,
                }),
            },
        }
    }

    /// Record one transition of the probe into `list`.
    ///
    /// # Safety
    ///
    /// `list` must be a live direct command list that is recording, and `probe` the live probe.
    unsafe fn transition_probe(
        list: *mut c_void,
        probe: usize,
        before: D3D12_RESOURCE_STATES,
        after: D3D12_RESOURCE_STATES,
    ) {
        let vtable = unsafe { vtable(list) };
        let barrier = unsafe { transition(probe, before, after) };
        unsafe { (vtable.ResourceBarrier)(list, 1, &barrier) };
    }

    /// The first redirect of a recording: take the probe from `COMMON`, where every recording
    /// leaves it, to unordered access, before the variant that writes it is bound.
    ///
    /// # Safety
    ///
    /// `list` must be a live direct command list that is recording.
    unsafe fn record_probe_barrier(list: *mut c_void) {
        let probe = probe::probe_resource();
        if probe == 0 {
            // A variant exists only once the probe does (`hooks`), so this is never reached.
            return;
        }
        // SAFETY: the probe is held for the life of the process.
        unsafe {
            transition_probe(
                list,
                probe,
                D3D12_RESOURCE_STATE_COMMON,
                D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
            );
        }
        PROBE_BARRIERS.fetch_add(1, Ordering::Relaxed);
    }

    /// Pay back what [`record_probe_barrier`] took: copy the probe into this present's readback
    /// slot between a barrier to copy source and one back to `COMMON`. Returns whether the copy was
    /// recorded; without the readback ring the probe is still put back.
    ///
    /// # Safety
    ///
    /// `list` must be a live graphics command list that is still recording, whose recording
    /// recorded [`record_probe_barrier`].
    unsafe fn pay_probe_back(list: *mut c_void) -> bool {
        let probe = probe::probe_resource();
        let readback = probe::readback_resource();
        // Barriers and copies are not allowed in a bundle. A recording only owes the probe after
        // `is_direct` said yes, so this holds; it is checked again rather than trusted.
        let direct = unsafe { is_direct(list) };
        if probe == 0 || !direct {
            PROBE_COPIES_SKIPPED.fetch_add(1, Ordering::Relaxed);
            return false;
        }
        if readback == 0 {
            // SAFETY: the probe is held for the life of the process.
            unsafe {
                transition_probe(
                    list,
                    probe,
                    D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                    D3D12_RESOURCE_STATE_COMMON,
                );
            }
            PROBE_COPIES_SKIPPED.fetch_add(1, Ordering::Relaxed);
            return false;
        }
        let offset = (probe::write_slot(params::presents()) * probe::PROBE_BYTES) as u64;
        // SAFETY: both resources are held for the life of the process.
        unsafe {
            transition_probe(
                list,
                probe,
                D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                D3D12_RESOURCE_STATE_COPY_SOURCE,
            );
            (vtable(list).CopyBufferRegion)(
                list,
                readback as *mut c_void,
                offset,
                probe as *mut c_void,
                0,
                probe::PROBE_BYTES as u64,
            );
            transition_probe(
                list,
                probe,
                D3D12_RESOURCE_STATE_COPY_SOURCE,
                D3D12_RESOURCE_STATE_COMMON,
            );
        }
        PROBE_COPIES.fetch_add(1, Ordering::Relaxed);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::look_binding::Level;
    use crate::replace::Stage;
    use crate::table::{Builder, EntrySpec, NO_PORT};

    #[test]
    fn the_floor_is_a_mean_that_a_100_ns_counter_cannot_round_to_zero() {
        // A 30 ns call read on a counter that ticks every 100 ns: 0 seven times in ten, 100 the
        // rest. The least reading is 0; the mean is the call.
        let readings = (0..1000).map(|at| if at % 10 < 7 { 0 } else { 100 });
        assert_eq!(floor_mean(readings), 30);
        assert_eq!(floor_mean([20, 40, PREEMPTED_NS + 1].into_iter()), 30);
        assert_eq!(floor_mean([PREEMPTED_NS, 0].into_iter()), PREEMPTED_NS / 2);
        assert_eq!(floor_mean(std::iter::empty()), 0);
    }

    /// A plausible pipeline or list pointer: heap-like and 16-byte aligned.
    fn ptr(n: usize) -> usize {
        0x0000_01f0_0000_0000 + n * 0x40
    }

    fn full_binding() -> Binding {
        Binding::appended(Level::Full, 36).expect("fits")
    }

    fn redirect(kind: PortKind) -> Decision {
        Decision::Redirect {
            variant: ptr(900 + kind as usize),
            kind,
        }
    }

    const NOW: u64 = 100;

    /// Every list a direct list.
    fn direct() -> bool {
        true
    }

    #[test]
    fn each_port_kind_redirects_under_its_own_stage() {
        // Written out rather than derived: AO and AO with SSR light, the albedo pass, the tonemaps.
        assert_eq!(stage_bit(PortKind::Ao as u8), 1);
        assert_eq!(stage_bit(PortKind::AoSsr as u8), 1);
        assert_eq!(stage_bit(PortKind::CmbAlbedo as u8), 2);
        assert_eq!(stage_bit(PortKind::Tonemap as u8), 4);
        assert_eq!(stage_bit(PortKind::TonemapLut as u8), 4);
        for kind in PortKind::ALL {
            assert_eq!(stage_bit(kind as u8), kind.stage().bit(), "{kind:?}");
            assert_eq!(stage_bit(kind as u8).count_ones(), 1, "{kind:?}");
        }
        for port in PortKind::COUNT as u8..=u8::MAX {
            assert_eq!(stage_bit(port), 0, "{port}");
        }
        assert_eq!(stage_bit(NO_PORT), 0);
        let all: u32 = Stage::ALL.iter().map(|stage| stage.bit()).sum();
        assert_eq!(
            PortKind::ALL
                .iter()
                .fold(0, |bits, kind| bits | stage_bit(*kind as u8)),
            all
        );
    }

    #[test]
    fn decide_redirects_only_with_every_gate_open() {
        // Stage of each kind by index, written out: see `each_port_kind_redirects_under_its_own_stage`.
        const STAGE_OF: [u32; PortKind::COUNT] = [1, 1, 2, 4, 4];
        let variant = |kind: PortKind| ptr(1000 + kind as usize);
        let mut builder = Builder::new();
        // A Hunt-style entry, and one that names a pipeline but no port.
        assert!(builder.insert(EntrySpec::new(ptr(1))));
        assert!(builder.insert(EntrySpec::port(ptr(2), ptr(902), NO_PORT)));
        for kind in PortKind::ALL {
            // A port whose variant is not ready, and one whose variant is.
            assert!(builder.insert(EntrySpec {
                port: kind as u8,
                ..EntrySpec::new(ptr(10 + kind as usize))
            }));
            assert!(builder.insert(EntrySpec::port(
                ptr(20 + kind as usize),
                variant(kind),
                kind as u8
            )));
        }
        let table = builder.build(None);
        let mut keys = vec![0, ptr(1), ptr(2), ptr(99)];
        for kind in PortKind::ALL {
            keys.push(ptr(10 + kind as usize));
            keys.push(ptr(20 + kind as usize));
        }
        // No kind off, each kind off on its own, and every kind off.
        let mut offs = vec![0_u32];
        offs.extend(PortKind::ALL.iter().map(|kind| u32::from(kind.bit())));
        offs.push(u32::from(PortKind::ALL_BITS));

        let mut redirects = 0;
        for key in keys {
            let entry = table.get(key);
            for active in [false, true] {
                for vanilla in [false, true] {
                    // Every subset of the three stages, and a bit no stage owns.
                    for stage_active in (0..8).chain([8, 8 | 7]) {
                        for &ports_off in &offs {
                            let decision = decide(entry, active, vanilla, stage_active, ports_off);
                            let port = entry.and_then(|e| PortKind::from_u8(e.port));
                            let expected = match (entry, port) {
                                _ if !active || vanilla => Decision::Pass,
                                (Some(entry), Some(kind)) => {
                                    if entry.redirect != 0
                                        && stage_active & STAGE_OF[kind as usize] != 0
                                        && ports_off & (1 << kind as u32) == 0
                                    {
                                        Decision::Redirect {
                                            variant: entry.redirect,
                                            kind,
                                        }
                                    } else {
                                        Decision::PortPassed { kind }
                                    }
                                }
                                _ => Decision::Pass,
                            };
                            assert_eq!(
                                decision, expected,
                                "key {key:#x} active {active} vanilla {vanilla} \
                                 stages {stage_active:#b} off {ports_off:#b}"
                            );
                            let bound = decision.bound(key);
                            match decision {
                                Decision::Redirect { variant: to, kind } => {
                                    redirects += 1;
                                    assert_eq!(bound, to);
                                    assert_eq!(to, variant(kind));
                                    assert_eq!(decision.withdrawn(), Decision::PortPassed { kind });
                                }
                                _ => {
                                    assert_eq!(bound, key);
                                    assert_eq!(decision.withdrawn(), decision);
                                }
                            }
                        }
                    }
                }
            }
        }
        // Per kind: open gates only, the 4 of the 8 stage subsets plus `8 | 7` holding its stage,
        // and the 5 of the 7 off sets that leave the kind on (none, and the other four alone).
        assert_eq!(redirects, PortKind::COUNT * 5 * 5);
    }

    #[test]
    fn one_call_in_256_is_timed() {
        let timed = (1..=10 * SAMPLE_EVERY)
            .filter(|tick| sampled(*tick))
            .count();
        assert_eq!(timed, 10);
        assert!(sampled(0));
        assert!(!sampled(1));
        assert!(sampled(SAMPLE_EVERY));
        // The tick wraps without losing the rhythm: 2^32 is a multiple of 256.
        assert!(sampled(u32::MAX.wrapping_add(1)));
    }

    #[test]
    fn faults_name_their_detours() {
        let bits: Vec<u32> = Detour::ALL.iter().map(|detour| detour.bit()).collect();
        assert_eq!(bits, vec![1, 2, 4, 8, 16, 32]);
        let named: Vec<&str> = faulted(Detour::Reset.bit() | Detour::Close.bit())
            .map(Detour::name)
            .collect();
        assert_eq!(named, vec!["Reset", "Close"]);
        assert_eq!(faulted(0).count(), 0);
    }

    #[test]
    fn timings_are_indexed_by_their_number_and_named_once() {
        for (at, timing) in Timing::ALL.iter().enumerate() {
            assert_eq!(*timing as usize, at);
        }
        let names: Vec<&str> = Timing::ALL.iter().map(|timing| timing.name()).collect();
        assert_eq!(
            names,
            [
                "SetPipelineState",
                "Reset",
                "ClearState",
                "Close",
                "CloseProbeCopy"
            ]
        );
    }

    #[test]
    fn a_bind_finds_its_lists_slot_and_keeps_what_it_bound() {
        let mut slots = ListSlots::new();
        assert!(slots.is_empty());
        let first = slots
            .set_pipeline(ptr(1), ptr(100), redirect(PortKind::Tonemap), NOW, direct)
            .expect("slot");
        assert_eq!(
            first,
            Bind {
                bound: ptr(900 + PortKind::Tonemap as usize),
                barrier: true
            }
        );
        assert!(slots.set_root_signature(ptr(1), full_binding(), NOW));
        // A later plain bind on the same list moves the pipeline but not what was bound before.
        let plain = slots
            .set_pipeline(ptr(1), ptr(101), Decision::Pass, NOW + 1, direct)
            .expect("slot");
        assert_eq!(
            plain,
            Bind {
                bound: ptr(101),
                barrier: false
            }
        );
        assert_eq!(
            slots.get(ptr(1)),
            Some(&ListSlot {
                list: ptr(1),
                game_pso: ptr(101),
                bound_pso: ptr(101),
                binding: full_binding(),
                port_bound_kinds: PortKind::Tonemap.bit(),
                probe_owed: true,
                touched: NOW + 1,
            })
        );
        assert_eq!(slots.len(), 1);
        assert!(slots.get(ptr(2)).is_none());
    }

    #[test]
    fn only_a_recordings_first_redirect_owes_the_barrier() {
        let mut slots = ListSlots::new();
        let lut = redirect(PortKind::TonemapLut);
        let ao = redirect(PortKind::Ao);
        let barriers: Vec<bool> = [lut, Decision::Pass, ao, lut]
            .into_iter()
            .map(|decision| {
                slots
                    .set_pipeline(ptr(1), ptr(5), decision, NOW, direct)
                    .expect("slot")
                    .barrier
            })
            .collect();
        assert_eq!(barriers, [true, false, false, false]);
        let closed = slots.close(ptr(1)).expect("slot");
        assert!(closed.probe_owed);
        assert_eq!(
            closed.port_bound_kinds,
            PortKind::TonemapLut.bit() | PortKind::Ao.bit()
        );
        // The next recording of the same list starts owing nothing, so it owes the barrier again.
        let again = slots
            .set_pipeline(ptr(1), ptr(5), ao, NOW, direct)
            .expect("slot");
        assert!(again.barrier);
    }

    #[test]
    fn a_list_that_cannot_take_the_barrier_binds_the_game_pipeline() {
        let mut slots = ListSlots::new();
        let mut asked = 0;
        let bind = slots
            .set_pipeline(ptr(1), ptr(100), redirect(PortKind::Ao), NOW, || {
                asked += 1;
                false
            })
            .expect("slot");
        assert_eq!(
            bind,
            Bind {
                bound: ptr(100),
                barrier: false
            }
        );
        assert_eq!(asked, 1);
        let slot = slots.get(ptr(1)).expect("slot");
        assert!(!slot.probe_owed);
        assert_eq!(slot.port_bound_kinds, 0);
        // A plain bind never asks.
        let mut asked = false;
        slots.set_pipeline(ptr(2), ptr(7), Decision::Pass, NOW, || {
            asked = true;
            true
        });
        assert!(!asked);
    }

    #[test]
    fn interleaved_lists_each_keep_their_own_slot() {
        let mut slots = ListSlots::new();
        for round in 0..3 {
            for list in 1..=4 {
                let game = ptr(100 * list + round);
                assert!(
                    slots
                        .set_pipeline(ptr(list), game, Decision::Pass, NOW, direct)
                        .is_some()
                );
            }
        }
        assert_eq!(slots.len(), 4);
        for list in 1..=4 {
            assert_eq!(
                slots.get(ptr(list)).map(|slot| slot.game_pso),
                Some(ptr(100 * list + 2))
            );
        }
    }

    #[test]
    fn a_full_array_takes_the_oldest_slot_that_owes_nothing() {
        let mut slots = ListSlots::new();
        // Eight lists in one present; the even ones owe the probe.
        for list in 1..=LIST_SLOTS {
            let decision = if list % 2 == 0 {
                redirect(PortKind::Ao)
            } else {
                Decision::Pass
            };
            assert!(
                slots
                    .set_pipeline(ptr(list), ptr(list), decision, NOW, direct)
                    .is_some()
            );
        }
        // One present on nothing is stale yet, so a ninth list takes the first of the slots used
        // longest ago that owe nothing: list 1's.
        let now = NOW + 1;
        let late = ptr(LIST_SLOTS + 1);
        assert!(slots.can_take(late, now) && slots.has_room(now));
        assert!(
            slots
                .set_pipeline(late, ptr(1), Decision::Pass, now, direct)
                .is_some()
        );
        assert!(slots.get(ptr(1)).is_none());
        assert_eq!(slots.len(), LIST_SLOTS);
        assert_eq!(slots.take_lost(), 0);
        // Then the other lists that owe nothing, oldest first: the ninth list, used this present,
        // is passed over.
        for list in [3, 5, 7] {
            assert!(slots.set_root_signature(ptr(100 + list), Binding::NONE, now));
            assert!(slots.get(ptr(list)).is_none(), "{list}");
            assert!(slots.get(late).is_some(), "{list}");
        }
        // Every slot is now the probe's or one just taken, and the newest ones owe nothing; take
        // those too, then nothing is left to take.
        for extra in 0..4 {
            assert!(
                slots
                    .set_pipeline(
                        ptr(200 + extra),
                        ptr(1),
                        redirect(PortKind::Ao),
                        now,
                        direct
                    )
                    .is_some()
            );
        }
        let before = slots.clone();
        let refused = ptr(300);
        assert!(!slots.can_take(refused, now) && !slots.has_room(now));
        assert_eq!(
            slots.set_pipeline(refused, ptr(1), Decision::Pass, now, direct),
            None
        );
        assert!(!slots.reset(refused, ptr(1), ptr(1), None, now));
        assert!(!slots.set_root_signature(refused, full_binding(), now));
        assert_eq!(slots.close(refused), None);
        assert_eq!(slots, before);
        // The lists already in it are still served, and a closed one frees a slot for the next.
        assert!(
            slots
                .set_pipeline(ptr(2), ptr(3), Decision::Pass, now, direct)
                .is_some()
        );
        assert!(slots.close(ptr(4)).is_some());
        assert!(
            slots
                .set_pipeline(refused, ptr(1), Decision::Pass, now, direct)
                .is_some()
        );
    }

    #[test]
    fn a_stale_slot_is_free_and_a_stale_debt_is_counted() {
        let mut slots = ListSlots::new();
        // A list that redirected on this thread and was closed on another, and one that did not.
        assert!(
            slots
                .set_pipeline(ptr(1), ptr(1), redirect(PortKind::Ao), NOW, direct)
                .is_some()
        );
        assert!(
            slots
                .set_pipeline(ptr(2), ptr(2), Decision::Pass, NOW, direct)
                .is_some()
        );
        for list in 3..=LIST_SLOTS {
            let now = NOW + STALE_PRESENTS;
            assert!(
                slots
                    .set_pipeline(ptr(list), ptr(list), redirect(PortKind::Ao), now, direct)
                    .is_some()
            );
        }
        // One present short of stale, the array is full of debts and slots in use, and only the
        // one list that owes nothing can be taken.
        let soon = NOW + STALE_PRESENTS - 1;
        let mut early = slots.clone();
        assert!(
            early
                .set_pipeline(ptr(49), ptr(1), Decision::Pass, soon, direct)
                .is_some()
        );
        assert!(early.get(ptr(1)).is_some() && early.get(ptr(2)).is_none());
        assert_eq!(early.take_lost(), 0);
        let later = NOW + STALE_PRESENTS;
        // Two presents on, both old slots are free: the debt is lost and counted, once.
        assert!(
            slots
                .set_pipeline(ptr(50), ptr(1), Decision::Pass, later, direct)
                .is_some()
        );
        assert!(
            slots
                .set_pipeline(ptr(51), ptr(1), Decision::Pass, later, direct)
                .is_some()
        );
        assert!(slots.get(ptr(1)).is_none() && slots.get(ptr(2)).is_none());
        assert_eq!(slots.take_lost(), 1);
        assert_eq!(slots.take_lost(), 0);
    }

    #[test]
    fn a_list_bound_again_after_going_stale_inherits_nothing() {
        let mut slots = ListSlots::new();
        assert!(
            slots
                .set_pipeline(ptr(1), ptr(1), redirect(PortKind::CmbAlbedo), NOW, direct)
                .is_some()
        );
        assert!(slots.set_root_signature(ptr(1), full_binding(), NOW));
        // The list was closed and reset on another thread, and is recorded here again.
        let later = NOW + STALE_PRESENTS;
        let bind = slots
            .set_pipeline(ptr(1), ptr(1), Decision::Pass, later, direct)
            .expect("slot");
        assert!(!bind.barrier);
        let slot = slots.get(ptr(1)).expect("slot");
        assert_eq!(slot.port_bound_kinds, 0);
        assert!(!slot.probe_owed);
        assert_eq!(slot.binding, Binding::NONE);
        assert_eq!(slots.take_lost(), 1);
        // Within the window the slot is the same recording's and keeps its debt.
        assert!(
            slots
                .set_pipeline(ptr(2), ptr(2), redirect(PortKind::Ao), NOW, direct)
                .is_some()
        );
        let soon = NOW + STALE_PRESENTS - 1;
        let bind = slots
            .set_pipeline(ptr(2), ptr(2), redirect(PortKind::Ao), soon, direct)
            .expect("slot");
        assert!(!bind.barrier);
        assert!(slots.get(ptr(2)).is_some_and(|slot| slot.probe_owed));
    }

    #[test]
    fn close_hands_back_the_slot_and_frees_it() {
        let mut slots = ListSlots::new();
        assert!(slots.reset(ptr(1), 0, 0, None, NOW));
        assert!(
            slots
                .set_pipeline(ptr(1), ptr(100), redirect(PortKind::CmbAlbedo), NOW, direct)
                .is_some()
        );
        let closed = slots.close(ptr(1)).expect("slot");
        assert_eq!(closed.list, ptr(1));
        assert_eq!(closed.port_bound_kinds, PortKind::CmbAlbedo.bit());
        assert!(closed.probe_owed);
        assert!(slots.get(ptr(1)).is_none());
        assert!(slots.is_empty());
        // A second close, or one for a list never seen, finds nothing.
        assert_eq!(slots.close(ptr(1)), None);
        assert_eq!(slots.close(ptr(2)), None);
        // A list that bound no variant closes owing nothing.
        assert!(
            slots
                .set_pipeline(ptr(3), ptr(100), Decision::Pass, NOW, direct)
                .is_some()
        );
        let closed = slots.close(ptr(3)).expect("slot");
        assert!(!closed.probe_owed);
        assert_eq!(closed.port_bound_kinds, 0);
    }

    #[test]
    fn reset_re_keys_the_lists_slot() {
        let mut slots = ListSlots::new();
        assert!(
            slots
                .set_pipeline(ptr(1), ptr(100), redirect(PortKind::Ao), NOW, direct)
                .is_some()
        );
        assert!(slots.set_root_signature(ptr(1), full_binding(), NOW));
        assert!(
            slots
                .set_pipeline(ptr(2), ptr(200), Decision::Pass, NOW, direct)
                .is_some()
        );
        // A new recording on the same list starts over in the same slot; the old one was closed
        // elsewhere, so its debt is counted.
        assert!(slots.reset(ptr(1), ptr(300), ptr(300), None, NOW + 1));
        assert_eq!(
            slots.get(ptr(1)),
            Some(&ListSlot {
                list: ptr(1),
                game_pso: ptr(300),
                bound_pso: ptr(300),
                binding: Binding::NONE,
                port_bound_kinds: 0,
                probe_owed: false,
                touched: NOW + 1,
            })
        );
        assert_eq!(slots.take_lost(), 1);
        assert_eq!(slots.len(), 2);
        // An initial pipeline that was redirected owes the probe from the start.
        assert!(slots.reset(ptr(4), ptr(400), ptr(940), Some(PortKind::TonemapLut), NOW));
        let slot = slots.get(ptr(4)).expect("slot");
        assert!(slot.probe_owed);
        assert_eq!(slot.port_bound_kinds, PortKind::TonemapLut.bit());
        // So its first redirected bind owes no second barrier.
        let bind = slots
            .set_pipeline(ptr(4), ptr(400), redirect(PortKind::Tonemap), NOW, direct)
            .expect("slot");
        assert!(!bind.barrier);
        // The other list is untouched.
        assert_eq!(slots.get(ptr(2)).map(|slot| slot.game_pso), Some(ptr(200)));
    }

    #[test]
    fn a_new_list_at_a_reused_address_inherits_nothing() {
        let mut slots = ListSlots::new();
        assert!(
            slots
                .set_pipeline(ptr(1), ptr(1), redirect(PortKind::Ao), NOW, direct)
                .is_some()
        );
        // Released without a `Close`, and a new list created at the same address.
        slots.forget(ptr(1));
        assert!(slots.get(ptr(1)).is_none());
        let bind = slots
            .set_pipeline(ptr(1), ptr(1), redirect(PortKind::Ao), NOW, direct)
            .expect("slot");
        assert!(bind.barrier);
        // Forgetting a list never seen changes nothing.
        let before = slots.clone();
        slots.forget(ptr(9));
        assert_eq!(slots, before);
    }

    #[test]
    fn clear_state_forgets_the_root_signature_but_not_the_debt() {
        let mut slots = ListSlots::new();
        assert!(
            slots
                .set_pipeline(ptr(1), ptr(100), redirect(PortKind::AoSsr), NOW, direct)
                .is_some()
        );
        assert!(slots.set_root_signature(ptr(1), full_binding(), NOW));
        let bind = slots
            .clear_state(ptr(1), 0, Decision::Pass, NOW, direct)
            .expect("slot");
        assert_eq!(
            bind,
            Bind {
                bound: 0,
                barrier: false
            }
        );
        assert_eq!(
            slots.get(ptr(1)),
            Some(&ListSlot {
                list: ptr(1),
                game_pso: 0,
                bound_pso: 0,
                binding: Binding::NONE,
                port_bound_kinds: PortKind::AoSsr.bit(),
                probe_owed: true,
                touched: NOW,
            })
        );
    }

    #[test]
    fn a_null_list_never_takes_a_slot() {
        let mut slots = ListSlots::new();
        assert_eq!(
            slots.set_pipeline(0, ptr(1), redirect(PortKind::Ao), NOW, direct),
            None
        );
        assert!(!slots.reset(0, 0, 0, None, NOW));
        assert_eq!(slots.clear_state(0, 0, Decision::Pass, NOW, direct), None);
        assert!(!slots.set_root_signature(0, Binding::NONE, NOW));
        assert!(!slots.can_take(0, NOW));
        assert_eq!(slots.close(0), None);
        assert!(slots.get(0).is_none());
        assert!(slots.is_empty());
    }
}
