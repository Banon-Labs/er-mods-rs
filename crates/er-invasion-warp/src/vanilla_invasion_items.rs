//! The vanilla invasion fingers, re-enabled under Seamless and pointed at the local search.
//!
//! # Why they are greyed out
//!
//! `CanUseGoods` decides whether an inventory row's `Use` is available, and its return is one long
//! `and` -- any false term greys the row. One term reads this flag off the item's own param row:
//!
//! ```text
//! local_188 = IsInOnlineMode()      when paramRow->disable_offline != 0
//! ```
//!
//! Seamless runs its own netcode with the game's online flag clear, so `IsInOnlineMode` is false
//! and that term drops. Three things were ruled out first, each measured on the live process:
//! `CanUseBreakInItem` returns 1, so the break-in gate passes; the loaded rows are byte-identical
//! to the installed `regulation.bin` across all `0x60` bytes, so Seamless rewrites nothing in the
//! param; and `IsInOnlineMode` is inlined into `CanUseGoods` on 1.17, so hooking that 15-byte
//! function caught zero calls and stubbing it changed nothing on screen.
//!
//! Clearing the bit removes the test, and this module used to do exactly that. It no longer does,
//! and the reason is not style: the write moves `lobby_key` and silently drops the player out of
//! every other Seamless player's matchmaking pool. The gate is answered instead, in
//! [`crate::can_use_goods_gate`], which writes no param byte. The constants below stay because the
//! popup takeover still needs to know which three rows these are.

/// `EquipParamGoods` row ids for the three fingers Seamless greys out.
#[cfg(windows)]
const BLOODY_FINGER_GOODS: u32 = 102;
#[cfg(windows)]
const FESTERING_BLOODY_FINGER_GOODS: u32 = 111;
#[cfg(windows)]
const RECUSANT_FINGER_GOODS: u32 = 112;

/// The category bits an inventory item id carries, from `goodsId & 0xfffffff | 0x40000000` as
/// `CanUseGoods` itself builds the key it looks up.
const ITEM_ID_CATEGORY: u32 = 0x4000_0000;

/// `_EQUIP_PARAM_GOODS_ST + 0x48` packs eight flags: `enable_live`, `enable_gray`, `enable_white`,
/// `enable_black`, `enable_multi`, `disable_offline`, `isEquip`, `isConsume`, in that order.
///
/// Below the paramdef drift at `0x4d`, so the offset holds on this build without re-measuring.
/// Read live on 1.17.1: rows 102 and 112 hold `0x63`, row 111 holds `0xe3`, bit 5 set in all three.
#[cfg(windows)]
const GOODS_FLAGS_OFFSET: usize = 0x48;
#[cfg(windows)]
const DISABLE_OFFLINE_BIT: u8 = 1 << 5;

/// `GoodsDialog.fmg` ids for the invasion-bounds popup and its two buttons.
///
/// Keying on the message id rather than on which finger was used is what keeps this attached to
/// the dialog the player is actually looking at: all three fingers raise the same popup, and the
/// prompt is the thing that identifies it.
///
/// Read out of the installed message tree, `item-msgbnd-dcx/GoodsDialog.fmg.xml`, where 20000010
/// is `Attempting to invade another world.\nSelect the bounds for the attempt.` and its
/// neighbours are the buttons. 20000011, `Cancel invasion of other world?`, is the prompt raised
/// to leave an invasion and must never be treated as this one -- that dialog has an activation
/// window, and a previous version of this crate trapped the player in an invasion by dismissing
/// it along with everything else.
pub const INVASION_BOUNDS_PROMPT_MSG: u32 = 20_000_010;
/// `Nearby only`.
pub const NEARBY_ONLY_MSG: u32 = 20_000_015;
/// `Both near and far`.
pub const BOTH_NEAR_AND_FAR_MSG: u32 = 20_000_016;

/// The two choices the game's own invasion popup offers, in its own words.
///
/// The labels are vanilla and stay vanilla: pressing `Use` on one of these fingers raises the
/// game's dialog with `Nearby Only (default)` and `Both near and far`, and this mod adapts to
/// those names rather than adding a vocabulary of its own beside them.
///
/// They already correspond to configuration this crate has -- `prefilter_radius` and
/// `search_everywhere_when_exhausted` on [`LocalInvasionConfig`]. What changes here is only where
/// the choice comes from: today it is an edit to the config file, and it should be the button the
/// player just pressed.
///
/// [`LocalInvasionConfig`]: er_invasion_warp_core::local_invasion::LocalInvasionConfig
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SearchRange {
    /// `Nearby Only (default)` -- a pool around the player that never widens past itself.
    ///
    /// Vanilla scopes this to the player's current region, so it is a set of places, not the one
    /// block they are standing in.
    NearbyOnly,
    /// `Both near and far` -- the same pool first, then anywhere at all once it is exhausted.
    BothNearAndFar,
}

impl SearchRange {
    /// Which button this is, by its `GoodsDialog` message id.
    #[must_use]
    pub const fn from_message_id(message_id: u32) -> Option<Self> {
        match message_id {
            NEARBY_ONLY_MSG => Some(Self::NearbyOnly),
            BOTH_NEAR_AND_FAR_MSG => Some(Self::BothNearAndFar),
            _ => None,
        }
    }

    /// The configured radius and exhaustion behaviour this choice asks for.
    ///
    /// Neither button ever means one block. Vanilla's own `Nearby only` scopes matchmaking to the
    /// player's current region -- already a pool of places -- so the difference between the two
    /// rows is not how wide the near search is, it is whether the search is ever allowed to stop
    /// being near:
    ///
    /// | button | radius | when the ring is exhausted |
    /// | --- | --- | --- |
    /// | `Nearby only` | the player's configured radius | stop |
    /// | `Both near and far` | the same radius | drop the filter and search everywhere |
    ///
    /// A one-block search is an empty search, which is why `Nearby only` keeps the radius rather
    /// than zeroing it.
    ///
    /// The exact vanilla pool is `playRegionId` equality -- `GLOBAL_FieldArea->playRegionParamId`
    /// is what `CanUseGoods` reads, and `isBreakInMultiRegion` is what widens past it. This crate
    /// filters by map tile rather than by region, so the radius stands in for the region pool
    /// until a region filter exists; the shape of the choice is the same either way.
    #[must_use]
    pub const fn as_config(self, configured_radius: u8) -> (u8, bool) {
        match self {
            Self::NearbyOnly => (configured_radius, false),
            Self::BothNearAndFar => (configured_radius, true),
        }
    }
}

/// The inventory item id for a goods row.
#[must_use]
pub const fn with_category(goods_id: u32) -> u32 {
    (goods_id & 0x0fff_ffff) | ITEM_ID_CATEGORY
}

/// Whether this item is one whose popup this module routes.
///
/// All three fingers are re-enabled and all three raise the same two-choice dialog, so the range
/// is decided by which button is pressed, never by which finger was used.
#[must_use]
pub fn routes_the_range_popup(item_id: u32) -> bool {
    item_id == with_category(102) || item_id == with_category(111) || item_id == with_category(112)
}

/// Clear `disable_offline` on the three fingers, once.
///
/// Returns whether every row was found and left with the bit clear. A row that is not up yet is
/// not a failure: the param tables arrive during boot, so an early tick simply tries again.
///
/// # Safety
///
/// Game task thread. Writes one byte inside a row the engine's own lookup handed back.
#[cfg(windows)]
pub unsafe fn enable_offline_use() -> bool {
    use core::sync::atomic::{AtomicUsize, Ordering};

    static DONE: AtomicUsize = AtomicUsize::new(0);
    static REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);

    if DONE.load(Ordering::SeqCst) != 0 {
        return true;
    }
    let mut cleared = 0;
    for goods_id in [
        BLOODY_FINGER_GOODS,
        FESTERING_BLOODY_FINGER_GOODS,
        RECUSANT_FINGER_GOODS,
    ] {
        // SAFETY: game task thread; answers `None` rather than faulting before the tables exist.
        let Some(row) = (unsafe { crate::lynchpin_use::goods_row(goods_id) }) else {
            if REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "vanilla-fingers: goods row {goods_id} is not resolvable yet -- the param \
                     tables are not up. Retried every tick; printed once."
                ));
            }
            return false;
        };
        let field = row + GOODS_FLAGS_OFFSET;
        // SAFETY: fault-tolerant read of one byte inside a row the engine just handed back.
        let Some(before) = (unsafe { er_game_base::mem::safe_read_u8(field) }) else {
            return false;
        };
        if before & DISABLE_OFFLINE_BIT == 0 {
            continue;
        }
        // SAFETY: same byte, inside the same row.
        unsafe { core::ptr::write_volatile(field as *mut u8, before & !DISABLE_OFFLINE_BIT) };
        cleared += 1;
        crate::standalone_log(format_args!(
            "vanilla-fingers: goods {goods_id} row 0x{row:x}+0x48 {before:#04x} -> {:#04x}",
            before & !DISABLE_OFFLINE_BIT
        ));
    }
    DONE.store(1, Ordering::SeqCst);
    crate::standalone_log(format_args!(
        "vanilla-fingers: {cleared} row(s) re-enabled -- CanUseGoods requires IsInOnlineMode when \
         disable_offline is set, and Seamless keeps the game's own online flag clear."
    ));
    true
}

/// Queue every place this search will ask about, so the banner can name them one at a time.
///
/// # Why this is here and not in the query detour
///
/// `banner::announce_prefilter_step` was only ever reached from inside the lobby-query detour, so
/// a place appeared on screen when and only when a `RequestLobbyList` went out. Seamless does not
/// issue that query -- measured across six runs with `SetLobbyData` through the same interface as
/// the control -- so the screen named the first tile once and then never again. Run
/// br-20260916-233426-b38b: `prefilter: asking for m60_52_53_00 (1 of 49)`, exactly one line, and
/// the player reported seeing the nearby banner with no places under it.
///
/// The ring does not need the query. It is arithmetic on the block the player is standing in, so
/// it is known the moment the popup is answered, and [`search_banner`] recites it on the game task
/// at one place a second.
///
/// A block that cannot be read clears the queue rather than reciting a stale ring: the last search
/// was somewhere else, and naming those places would be a search of somewhere the player has left.
#[cfg(windows)]
fn queue_the_places_being_searched() {
    use crate::local_invasion_filter::search_banner;

    let radius = crate::local_invasion_filter::current_config_snapshot()
        .map_or(1, |config| config.prefilter_radius);
    let Ok(base) = er_game_base::mem::game_module_base() else {
        search_banner::clear();
        return;
    };
    // SAFETY: game task thread, called from the bounds popup's own answer handler; the getter is
    // address-checked for this build and writes only the local it is given.
    let Some(block) = (unsafe { er_invasion_warp_core::warp::current_block_id(base) }) else {
        search_banner::clear();
        crate::standalone_log(format_args!(
            "vanilla-fingers: the block the player is standing in is not readable, so the search \
             banner has no places to name. The search itself is unaffected."
        ));
        return;
    };
    let ring = search_banner::nearby_ring(block, radius);
    // `Nearby only` is excluded here for the same reason it is excluded from `hunt_target`'s
    // `NobodyPublishes` branch: skipping the ring is a shortcut to the far half, and that row has
    // no far half to be short-cut to. The ring is its whole search, so the places have to be
    // queued, recited and asked, and the rotation has to keep cycling them for as long as the
    // player leaves the finger armed.
    //
    // User report, live on run br-20260917-193816-8621: "I just attempted to invade nearby, and it
    // didn't tell me any of the locations it searched in the banner ... Search should loop forever,
    // but only on the region's block ids, if I invade nearby." The log shows exactly the branch
    // below taken -- `sweep: armed for 0 nearby place(s)`, `skipped all 49 nearby place(s)` -- so
    // the banner had nothing to name and the rotation had nothing to rotate. The query that
    // followed was correctly filtered (`hunt: asking Steam for hosts at m61_48_45_00 only`), which
    // is why this reads as a missing banner rather than a wrong invasion: one place was asked, over
    // and over, instead of the region.
    if crate::local_invasion_filter::finger_reach_is_nearby_only() {
        search_banner::queue_ring(&ring);
        crate::lobby_preflight::arm_sweep(&ring);
        crate::standalone_log(format_args!(
            "vanilla-fingers: queued all {} nearby place(s) around block 0x{block:08x} at radius \
             {radius} even though the pre-flight found no host anywhere carrying a block id. \
             `Nearby only` does not take the skip: the ring is what that row searches, not a \
             prelude to something wider, so every place is named and asked and the rotation keeps \
             cycling them.",
            ring.len()
        ));
        return;
    }
    if crate::lobby_preflight::verdict() == crate::lobby_preflight::Verdict::NobodyPublishes {
        // An empty ring is the sweep's own way of saying the nearby half is over: `arm_sweep`
        // marks it finished and `nearby` reports `Empty(0)`, which is exactly the state 49 queries
        // would have reached. `Both near and far` therefore widens on this frame instead of after
        // 49 round-trips at 138ms each (measured, run br-20260917-155917-7c3a), and the player
        // gets the Seamless invade shortly after the places finish reciting rather than long after.
        //
        // `Unknown` deliberately does not take this branch, for the same reason `hunt_target`
        // refuses it: a search armed a frame before the answer lands must not skip its own ring on
        // no evidence.
        crate::lobby_preflight::arm_sweep(&[]);
        // Nothing is recited. Naming 48 places for a search that asks none of them describes a
        // search nobody is doing, and the player reads a screen that is still hunting when the
        // answer is already in. One line says what is true instead.
        search_banner::clear();
        crate::local_invasion_filter::banner::announce_nothing_to_search(
            true,
            crate::local_invasion_filter::finger_reach_is_nearby_only(),
        );
        crate::standalone_log(format_args!(
            "vanilla-fingers: skipped all {} nearby place(s) around block 0x{block:08x} at radius \
             {radius} -- the pre-flight already found no host anywhere carrying a block id, so \
             every one of those queries is known empty. The nearby half is over on this frame, the \
             place queue is left empty rather than recited, and the search widens now.",
            ring.len()
        ));
        return;
    }
    search_banner::queue_ring(&ring);
    // The same list, asked about rather than recited. The banner names where the search is
    // looking; the sweep is what makes that true, and it is also what ends the nearby half of
    // `Both near and far` -- a place that answers zero is a place that has been asked.
    crate::lobby_preflight::arm_sweep(&ring);
    crate::standalone_log(format_args!(
        "vanilla-fingers: queued {} place(s) for the search banner around \
         block 0x{block:08x} at radius {radius}, named one per \
         {}ms on the game task, and armed a lobby query for each of them",
        search_banner::pending(),
        search_banner::STEP_INTERVAL_MS
    ));
}

/// Host-side stub: there is no player standing anywhere and no banner to paint.
#[cfg(not(windows))]
fn queue_the_places_being_searched() {}

/// Put the range the player chose in force for this search, without touching their config.
///
/// The two buttons are the control surface the config file used to be, and they stay out of it:
/// using an item must not rewrite a player's settings, and it must not hinge on what those
/// settings happen to hold. A finger that only works when `search_by_location` is already on is a
/// finger that does nothing for most players and says nothing about why.
///
/// So the choice becomes an in-memory override that [`crate::local_invasion_filter::current_config`]
/// overlays on whatever was loaded, which is the single point every consumer of the config already
/// reads. It forces the three switches the widening search actually needs -- `enabled`, `hunt` and
/// `steam_hooks` -- because they are one mechanism rather than three preferences, and it expires
/// with the search.
///
/// `_remembered_radius` is unused now: the player's own radius reaches the search through the
/// overlay, so nothing here has to carry it. The parameter is kept so the call site still reads as
/// "this range, at that radius".
#[cfg(windows)]
pub fn adopt_search_range(range: SearchRange, _remembered_radius: u8) -> bool {
    use crate::local_invasion_filter::{FINGER_REACH_NEAR_AND_FAR, FINGER_REACH_NEARBY};

    let reach = match range {
        SearchRange::NearbyOnly => FINGER_REACH_NEARBY,
        SearchRange::BothNearAndFar => FINGER_REACH_NEAR_AND_FAR,
    };
    crate::local_invasion_filter::set_finger_reach(reach);
    crate::standalone_log(format_args!(
        "vanilla-fingers: {range:?} is in force for this search as an in-memory override -- the \
         player's er-invasion-warp.toml is not read for it and not written to."
    ));
    true
}

/// `CS::CSPlayerMenuCtrl` -- the vanilla invasion start, shared by all three fingers.
///
/// The ctrl's step machine dispatches on `+0x10`: 1 raises the bounds popup, 2 handles the answer,
/// 5 tears down. The step-2 handler reads the pressed row once and switches on `opmeMenuType`, and
/// all three fingers converge here when no search is already running.
///
/// The 1.16.2 -> 1.17 mapping was the weak kind, so the prologue is the gate rather than a
/// formality: `verify_seam` refuses outright if the bytes are not these.
#[cfg(windows)]
const START_VANILLA_INVASION: crate::map_seams::MapSeam = crate::map_seams::MapSeam {
    name: "CS::CSPlayerMenuCtrl::StartInvasionFromBoundsPopup",
    rva: 0x007c_1dd0,
    prologue: &[0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20],
    arg_count: 3,
};

/// The trampoline, once the detour is in.
#[cfg(windows)]
static ORIG_START_INVASION: core::sync::atomic::AtomicUsize =
    core::sync::atomic::AtomicUsize::new(0);

/// A range to answer the bounds popup with, regardless of the row pressed. Zero is the resting
/// value and means the player's own press decides.
#[cfg(windows)]
pub(crate) static FORCED_SEARCH_RANGE: core::sync::atomic::AtomicUsize =
    core::sync::atomic::AtomicUsize::new(0);
#[cfg(windows)]
pub(crate) const FORCED_RANGE_NONE: usize = 0;
#[cfg(windows)]
pub(crate) const FORCED_RANGE_NEARBY: usize = 1;
#[cfg(windows)]
pub(crate) const FORCED_RANGE_NEAR_AND_FAR: usize = 2;

/// `CSPlayerMenuCtrl + 0x3d` -- `isBreakInMultiRegion`, where vanilla records the pressed row.
///
/// Cross-checked against a live dump: `+0x8` `selectedGoodsItemId` held `0x40000070` while the
/// Recusant Finger's popup was up, and `+0x10` stepped `0 -> 1 -> 2 -> 3 -> 0` across one use.
#[cfg(windows)]
const CTRL_IS_BREAK_IN_MULTI_REGION: usize = 0x3d;
/// The ctrl's step, and the two values that mean a row was actually pressed.
#[cfg(windows)]
const CTRL_STEP_OFFSET: usize = 0x10;
#[cfg(windows)]
const CTRL_STEP_CHOSE_ROW: [i32; 2] = [3, 4];
#[cfg(windows)]
const MULTI_REGION_OFF: u8 = 0;

impl SearchRange {
    /// Which row vanilla recorded, read back from its own field.
    #[cfg(windows)]
    #[must_use]
    fn from_multi_region_flag(flag: u8) -> Self {
        if flag == MULTI_REGION_OFF {
            Self::NearbyOnly
        } else {
            Self::BothNearAndFar
        }
    }
}

/// Let vanilla record the choice, then put the mod's search in force with the range it chose.
///
/// The original runs first, deliberately: it reads the pressed row out of the popup, writes
/// `isBreakInMultiRegion`, and advances the menu's own step machine. Reproducing any of that would
/// be a second copy of a state machine the game owns.
///
/// `cancelled` is the popup's cancel flag, not the row. Cancelling stops the search rather than
/// starting one, which the player wants kept, so it passes straight through.
///
/// # Safety
///
/// Called by MinHook in place of the game's function, on the game's own menu thread.
#[cfg(windows)]
unsafe extern "system" fn start_invasion_entry(
    ctrl: usize,
    cancelled: usize,
    popup: usize,
    spare: usize,
) -> usize {
    use core::sync::atomic::Ordering;

    let orig = ORIG_START_INVASION.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    // SAFETY: the trampoline stored for this exact target, called with its own arguments.
    let answer = unsafe {
        core::mem::transmute::<usize, er_hook::UnionFn>(orig)(ctrl, cancelled, popup, spare)
    };
    if cancelled & 0xff != 0 {
        return answer;
    }
    // Act only on the frame a row was pressed, read from the step rather than the return value.
    // The original answers 1 even when the popup closes with no row picked -- it falls through to
    // a vtable call -- so gating on the return logged 216 adoptions for a handful of presses on
    // run `br-20260916-005322-27ca`, 214 of them idle frames reading the flag's default. Only the
    // two selection arms advance the step, both to `(selectedGoodsItemId == -1) + 3`.
    // SAFETY: one dword inside the object the game just finished writing, fault-closed.
    let Some(step) = (unsafe { er_game_base::mem::safe_read_i32(ctrl + CTRL_STEP_OFFSET) }) else {
        return answer;
    };
    if !CTRL_STEP_CHOSE_ROW.contains(&step) {
        return answer;
    }
    // SAFETY: one byte inside the same object, fault-closed.
    let Some(flag) =
        (unsafe { er_game_base::mem::safe_read_u8(ctrl + CTRL_IS_BREAK_IN_MULTI_REGION) })
    else {
        return answer;
    };
    let chosen = SearchRange::from_multi_region_flag(flag);
    // An override, for proving the `Both near and far` branch while the popup's cursor cannot be
    // driven to it.
    //
    // The popup answers row 0 every time under agent input: reproduced three times on
    // br-20260916-083008-92e8 and after, always `isBreakInMultiRegion=0`. A D-pad Down after the
    // use animation left it at 0, the same Down mid-animation stopped the chain firing at all, and
    // a full left-stick down likewise. Driving a cursor nobody can read back is how this repo
    // already lost a day to a `GridControl`, so the range is overridden here instead and the
    // navigation left as its own problem.
    //
    // Zero, the resting value, means "whatever the player pressed" -- so this is inert in a normal
    // session and cannot silently answer for somebody.
    let forced = FORCED_SEARCH_RANGE.load(Ordering::SeqCst);
    let range = match forced {
        FORCED_RANGE_NEARBY => SearchRange::NearbyOnly,
        FORCED_RANGE_NEAR_AND_FAR => SearchRange::BothNearAndFar,
        _ => chosen,
    };
    if forced != FORCED_RANGE_NONE {
        crate::standalone_log(format_args!(
            "vanilla-fingers: the popup answered {chosen:?} and an override replaced it with              {range:?}. This is a test hook, not a product path -- the player's press is what              decides when nothing has set it."
        ));
    }
    let adopted = adopt_search_range(range, 0);
    // Armed for the game task, never driven from this thread. Twice now.
    //
    // Driving inline was tried on 2026-09-16 because `lynchpin_use` does exactly that and works,
    // and it hard locked the game on the first Both near and far press. The log's last two lines
    // say why: the captured owner was refused for carrying `0x3b81506c3b8150c8` at `+0x58` instead
    // of the session, the synthesized owner was built as designed -- and the call into
    // `ersc+0x25850` then blocked the menu thread, which is the thread the whole game's UI runs on.
    //
    // The difference from `lynchpin_use` is the thread, not the owner. That file's detour sits on
    // `OpenConversationChoicesMenu`, which `ersc.dll` itself called, so the call re-enters a module
    // already on the stack with its own lock held by this very thread. The bounds popup is the
    // game's own menu, with no `ersc` frame beneath it, so the same call is a first acquire that can
    // and does block.
    //
    // Arming is worse in one way and better in every other: the call still blocks, but it blocks
    // on a thread of ours while the game keeps rendering. A hung search the player can walk away
    // from beats a frozen game.
    // `Both near and far` hands the search to Seamless's own item, not to its action function.
    //
    // Measured A/B, run br-20260916-083935-5990, all 38 slots of the matchmaking interface
    // `ersc.dll` holds swept so the counts carry their own control: calling `ersc+0x25850` directly
    // produced no lobby calls at all while moving `session+0x150` to `0x0e`, and using the Challenger's
    // Lynchpin as an item produced `RequestLobbyList` twice and
    // `AddRequestLobbyListStringFilter` ten times. The state write is a symptom of Seamless
    // searching, not the cause, so the direct call looked like a search and never became one.
    //
    // `Nearby only` keeps the direct call: that row is this mod's own block-filtered search, which
    // the filter drives itself, and it is not supposed to reach Seamless's matchmaking at all.
    // Recite the places this search will ask about, whichever row was chosen.
    //
    // Queued here rather than inside the query detour, which is where `announce_prefilter_step`
    // used to be reached from and why the screen only ever named one place. The ring is arithmetic
    // on the block the player is standing in, so it is known now; the query discovers nothing the
    // caller does not already have.
    // Ask, once, whether any host anywhere publishes a block id. A `no` turns the ring off before
    // its first query rather than after its forty-ninth; see `lobby_preflight` for the controls.
    //
    // The order of these two lines is the feature, and reversing them was the bug. The ring used
    // to be armed on the line before the question was asked, so no answer could ever turn it off
    // and the sentence above described something that did not happen. Measured through Frida on
    // run br-20260917-155917-7c3a: request 1 went out as the existence pre-flight
    // (`er_invasion_warp_map != ""`) and request 2 was a sweep place regardless, 138ms later.
    crate::lobby_preflight::arm();
    queue_the_places_being_searched();
    let requested = match range {
        // `Both near and far` drives Seamless's own invade action through the option-menu object
        // some seam has handed over, and presses nothing.
        //
        // The row used to hand off to the Challenger's Lynchpin, which pinned the item and then
        // held pad `A` for 500ms through `er_quickload_hold_xinput_pad`. `A` is jump. Run
        // br-20260916-233426-b38b is what that does to a player -- reported as "attempting to
        // search near and far makes me jump after I accept the item to use it" -- and the same
        // run's log says the press achieved nothing anyway: `the press was dropped --
        // ChrIns+0x160 reads Some(1073741936), not the pinned 0x407fde63`. So the handoff's only
        // observable effect was the jump.
        //
        // What does work is measured: on 2026-09-16 a frida session called `ersc+0x25850` with the
        // object `show` was called with and the player landed in another host's world, session
        // state walking `0x1 -> 0xe -> 0xf -> 0x13 -> 0x14 -> 0x16` with the invasion SpEffects
        // set. `drive_invade_with_owner` is that call, and it validates the owner before making
        // it -- `+0x58` must lead to a session, the session must be idle, and the lock shape must
        // be sane -- so a wrong pointer declines rather than wedging the menu thread the way the
        // synthesized owner did.
        SearchRange::BothNearAndFar => {
            match crate::local_invasion_filter::search_banner::captured_menu_object() {
                Some(owner) => crate::local_invasion_filter::drive_invade_with_owner(
                    owner,
                    "the vanilla finger's Both near and far",
                ),
                // No object in hand this frame, so arm the search rather than refuse it.
                //
                // Refusing was wrong twice over. It put "Cannot search yet -- Seamless has not
                // opened a menu this session" in front of a player: this module's own capture
                // state, recited at somebody who has no idea what a menu object is and can do
                // nothing whatever with the sentence. And it threw the press away for the state
                // of a single frame, when `arm_invade_request` exists for exactly this shape --
                // it runs on the next game tick that can resolve the session and finds it idle,
                // so the search starts by itself the moment an object arrives instead of needing
                // the player to use the item again.
                None => crate::local_invasion_filter::arm_invade_request(
                    "a vanilla invasion finger, near and far, with no menu object captured yet",
                ),
            }
        }
        SearchRange::NearbyOnly => {
            crate::local_invasion_filter::arm_invade_request("a vanilla invasion finger")
        }
    };
    // A row that could not start a search must put back the override it just adopted.
    //
    // `adopt_search_range` forces `enabled`, `hunt` and `steam_hooks` on for the duration of the
    // search, and `stand_down_hunt` is the only thing that clears them -- so a row that adopts and
    // then fails to start leaves the filter armed with nobody to retire it. The player then has a
    // live local-invasion filter judging and cancelling every match, including the ones Seamless's
    // own Challenger's Lynchpin brings in, with no search of ours running to justify it.
    //
    // Reported on run br-20260916-235321-0597 as "when I exhaust nearby, it doesn't transition to
    // seamless invades; additionally it doesn't allow me to use the lynchpin to do seamless
    // invades. Its in some bugged state." The log's matching pair is `Both near and far has no
    // option-menu object to drive Seamless through` followed by `requested=false`, with the
    // override left in force behind it.
    if !requested {
        crate::local_invasion_filter::stand_down_hunt(
            "the finger's search could not start, so its override is retired rather than left \
             armed with nothing running behind it",
        );
        crate::local_invasion_filter::search_banner::clear();
    }
    let driven = false;
    // Only claim a search that started.
    //
    // `announce_search` used to run unconditionally, so run br-20260917-000642-f680 put
    // "Searching for a world, near and far" on screen in the same breath as logging `Both near and
    // far has no option-menu object to drive Seamless through, so this search cannot start` -- a
    // banner that says the opposite of the log is worse than no banner, because the player waits
    // on it. The stand-down above has already cleared the place queue by this point, which is why
    // no location names followed it either.
    if requested {
        announce_search(range);
    } else {
        announce_search_refused();
    }
    crate::standalone_log(format_args!(
        "vanilla-fingers: the bounds popup chose {range:?} (isBreakInMultiRegion={flag}, \
         step={step}), adopted={adopted}, driven_inline={driven}, requested={requested}"
    ));
    answer
}

/// Arm the detour that takes the bounds popup's answer.
///
/// Idempotent and fail-closed: a build whose bytes do not match the recorded prologue is refused,
/// and the popup keeps vanilla behaviour.
///
/// # Safety
///
/// Game task thread, after the module base resolves.
#[cfg(windows)]
pub unsafe fn install_bounds_popup_takeover() -> bool {
    use core::sync::atomic::{AtomicUsize, Ordering};

    static REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);

    if ORIG_START_INVASION.load(Ordering::SeqCst) != 0 {
        return true;
    }
    // SAFETY: game task thread; the seam checks its own prologue and refuses otherwise.
    let address = match unsafe { crate::map_seams::verify_seam(&START_VANILLA_INVASION) } {
        Ok(address) => address,
        Err(error) => {
            if REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "vanilla-fingers: refused {} -- {error}. The fingers are usable but their \
                     popup still drives vanilla matchmaking, which reaches nobody in a Seamless \
                     session. Printed once.",
                    START_VANILLA_INVASION.name
                ));
            }
            return false;
        }
    };
    let hook = match unsafe {
        er_hook::MhHook::new(
            address as *mut core::ffi::c_void,
            start_invasion_entry as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            crate::standalone_log(format_args!(
                "vanilla-fingers: failed to create the bounds-popup detour @0x{address:x} -- \
                 {status:?}. The address resolved and its prologue matched."
            ));
            return false;
        }
    };
    ORIG_START_INVASION.store(hook.trampoline() as usize, Ordering::SeqCst);
    // SAFETY: the hook was created above; enabling is MinHook's own queued path.
    if unsafe { hook.queue_enable() }.is_err() {
        ORIG_START_INVASION.store(0, Ordering::SeqCst);
        return false;
    }
    // SAFETY: applies the queue this function just added to.
    match unsafe { er_hook::MH_ApplyQueued() } {
        er_hook::MH_STATUS::MH_OK => {
            crate::standalone_log(format_args!(
                "vanilla-fingers: armed the bounds-popup takeover on {} @0x{address:x}. Both rows \
                 keep a pool -- vanilla's own `Nearby only` scopes to a region, not one block.",
                START_VANILLA_INVASION.name
            ));
            true
        }
        status => {
            ORIG_START_INVASION.store(0, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "vanilla-fingers: MH_ApplyQueued refused the bounds-popup detour -- {status:?}"
            ));
            false
        }
    }
}

/// Say on screen that the search has started, in our own words.
///
/// Vanilla's own "attempting to invade" notice never appears for these fingers, and cannot: the
/// vanilla search is deliberately not started, so nothing raises it. Without a notice of our own
/// the item reads as inert -- measured the honest way, by the player pressing a row on run
/// `br-20260916-010050-862f` and seeing nothing at all while the session sat in `SEARCHING` for
/// eighteen seconds.
///
/// The wording follows the popup's, so the line names the row that was pressed rather than a
/// vocabulary the player has not seen. This is the same auto-closing surface the rejection and
/// prefilter banners use: no dialog and no button, it expires by itself.
#[cfg(windows)]
fn announce_search(range: SearchRange) {
    use core::sync::atomic::{AtomicUsize, Ordering};

    static BANNER_FAILED: AtomicUsize = AtomicUsize::new(0);

    let text = match range {
        SearchRange::NearbyOnly => "Searching for a world nearby",
        SearchRange::BothNearAndFar => "Searching for a world, near and far",
    };
    // SAFETY: the game's menu thread with the menu up, which is this surface's stated contract --
    // the popup the player just answered is still the thing on screen.
    if unsafe { crate::announce::show(text) } {
        return;
    }
    if BANNER_FAILED.swap(1, Ordering::SeqCst) == 0 {
        crate::standalone_log(format_args!(
            "vanilla-fingers: could not show the search banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up. The search is still armed; only the \
             on-screen notice is missing. Printed once."
        ));
    }
}

/// Say on screen that the search did not start, rather than leaving the claim that it did.
///
/// # Nothing here may name a part of this mod
///
/// The first draft of this said "Cannot search yet -- Seamless has not opened a menu this
/// session", and the user's response to seeing it in game was "what in the absolute hell is this".
/// It was right on the facts and useless as a message: a menu object is this module's bookkeeping,
/// the player cannot act on it, and a banner they cannot act on is worse than silence because it
/// reads as the mod being broken in a way they are expected to fix.
///
/// A player-facing line says what happened to the thing the player did. It never names a pointer,
/// a capture, a session, a detour or Seamless's internals.
///
/// This is reached far less often now that the near+far row arms instead of refusing -- it is left
/// for the case where nothing could be armed at all, which is a real outcome and still better said
/// than swallowed.
#[cfg(windows)]
fn announce_search_refused() {
    use core::sync::atomic::{AtomicUsize, Ordering};

    static REFUSAL_BANNER_FAILED: AtomicUsize = AtomicUsize::new(0);

    let text = "No invasion could be started";
    // SAFETY: the game's menu thread with the menu up, the same surface `announce_search` uses.
    if unsafe { crate::announce::show(text) } {
        return;
    }
    if REFUSAL_BANNER_FAILED.swap(1, Ordering::SeqCst) == 0 {
        crate::standalone_log(format_args!(
            "vanilla-fingers: could not show the refusal banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up. Printed once."
        ));
    }
}

/// Host-side stub.
#[cfg(not(windows))]
fn announce_search_refused() {}

#[cfg(test)]
mod tests {
    use super::{
        BOTH_NEAR_AND_FAR_MSG, INVASION_BOUNDS_PROMPT_MSG, NEARBY_ONLY_MSG, SearchRange,
        routes_the_range_popup, with_category,
    };

    /// Anchored against the Lynchpin, whose pair is already known good in `lynchpin_use`: goods
    /// `0x7fde63` is item `0x407fde63`.
    #[test]
    fn the_item_id_is_the_goods_id_with_its_category() {
        assert_eq!(with_category(0x7f_de63), 0x407f_de63);
        assert_eq!(with_category(102), 0x4000_0066);
        assert_eq!(with_category(111), 0x4000_006f);
    }

    /// All three fingers raise the same dialog, so all three route; nothing else does.
    #[test]
    fn every_finger_routes_and_nothing_else_does() {
        for goods in [102, 111, 112] {
            assert!(routes_the_range_popup(with_category(goods)));
        }
        assert!(!routes_the_range_popup(0x407f_de63));
    }

    /// Neither button collapses to a single block. Both keep the player's configured radius, and
    /// the only difference is whether the search may stop being near -- which is exactly what
    /// vanilla's `isBreakInMultiRegion` decides.
    #[test]
    fn neither_choice_ever_searches_one_block() {
        assert_eq!(SearchRange::NearbyOnly.as_config(3), (3, false));
        assert_eq!(SearchRange::BothNearAndFar.as_config(3), (3, true));
        for radius in 1..=3 {
            let (near, everywhere) = SearchRange::NearbyOnly.as_config(radius);
            assert_eq!(near, radius, "`Nearby only` keeps the pool it was given");
            assert!(!everywhere, "`Nearby only` never drops the filter");
        }
    }

    /// The buttons are identified by their own message ids, and the leave-invasion prompt next
    /// door is not one of them.
    #[test]
    fn each_button_is_named_by_its_message_id() {
        assert_eq!(
            SearchRange::from_message_id(NEARBY_ONLY_MSG),
            Some(SearchRange::NearbyOnly)
        );
        assert_eq!(
            SearchRange::from_message_id(BOTH_NEAR_AND_FAR_MSG),
            Some(SearchRange::BothNearAndFar)
        );
        assert_eq!(
            SearchRange::from_message_id(INVASION_BOUNDS_PROMPT_MSG),
            None
        );
        // `Cancel invasion of other world?` -- the leave prompt, which has an activation window.
        assert_eq!(SearchRange::from_message_id(20_000_011), None);
    }
}
