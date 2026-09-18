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
        search_banner::queue_ring(&ring, true);
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
    search_banner::queue_ring(
        &ring,
        crate::local_invasion_filter::finger_reach_is_nearby_only(),
    );
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

/// `CS::CSPlayerMenuCtrl::ConfirmCancelInvasionSearch` -- the yes/no a finger raises once a
/// search of its own is already running.
///
/// # Why a second seam, and how the game chooses between them
///
/// The step machine (1.16.2 `0x1407c24f0`) asks the game's own `BreakInManager` before it routes
/// a finger's press, once per finger type:
///
/// ```text
///   case INVADE_BLOODY_FINGER:   active = IsSearchActive_RedInvasionA(netMan->breakInManager);
///   case INVADE_WORLD_RECUSANT:  active = IsSearchActive_RedIvasionB(...);
///   case INVADE_WORLD_FESTERING: active = IsSearchActive_RedInvasionALimited(...);
///     if (!active) { StartInvasionFromBoundsPopup(ctrl, ..); return; }   // 0x1407c1dd0
///     ConfirmCancelInvasionSearch(ctrl, ..);                             // 0x1407c1f90
/// ```
///
/// So the second press of a finger never reaches [`START_VANILLA_INVASION`] at all -- it lands
/// here, on the prompt `GoodsDialog` 20000011 spells `Cancel invasion of other world?`. Nothing in
/// this crate was watching it, which is the whole of the reported bug: "it allows me to then use
/// it again to toggle it off. This doesn't seem to cancel my seamless searching or the banner from
/// reading back or appearing." Vanilla's own search was called off; ours kept running and the
/// place recital kept reciting, because neither had been told.
///
/// # The prologue cannot tell the two apart, so something else has to
///
/// These two functions are near-identical twins -- the same opening `0x46` bytes, byte for byte,
/// in both builds -- so the signature that guards every other seam here is a drift check and
/// nothing more. What identifies them is the address, and the address comes from the
/// 1.16.2 -> 1.17 map. A row that pointed this seam at its twin would put the call-off handler on
/// the start path, where its gate fires on the first row the player ever presses and stands the
/// search down a frame after arming it: a silent, total failure of the feature.
///
/// [`the_cancel_seam_is_not_its_twin`] closes that off with the one byte sequence the twins do not
/// share. `c6 43 3d` (`mov byte [rbx+0x3d], imm8`, the `isBreakInMultiRegion` write) sits at
/// `+0x87` and `+0xa7` of the start handler and nowhere at all in this one, in 1.16.2 and in
/// 1.17.1 alike.
#[cfg(windows)]
const CANCEL_VANILLA_INVASION: crate::map_seams::MapSeam = crate::map_seams::MapSeam {
    name: "CS::CSPlayerMenuCtrl::ConfirmCancelInvasionSearch",
    rva: 0x007c_1f90,
    prologue: &[0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20],
    arg_count: 3,
};

/// The trampoline for [`CANCEL_VANILLA_INVASION`], once its detour is in.
#[cfg(windows)]
static ORIG_CANCEL_INVASION: core::sync::atomic::AtomicUsize =
    core::sync::atomic::AtomicUsize::new(0);

/// `mov byte [rbx+0x3d], imm8` -- the `isBreakInMultiRegion` write, present only in the twin.
#[cfg(windows)]
// AOB signature: searched for inside an already-resolved function body, never written over one.
// These three bytes are the opcode and modrm of `mov byte [rbx+0x3d], imm8` with the immediate
// deliberately left off, so the pattern matches both stores the start handler makes (`+0x87` and
// `+0xa7`) whatever value each writes. `the_cancel_seam_is_not_its_twin` scans the seam's `.pdata`
// extent for it to tell two functions apart whose opening `0x46` bytes are byte-identical in both
// builds. An assembled instruction is the wrong shape for that: a prologue is a fixed sequence at
// a known entry, and this is a truncated one hunted at an unknown offset.
const IS_BREAK_IN_MULTI_REGION_WRITE: [u8; 3] = [0xc6, 0x43, 0x3d];

/// How far into the resolved function the twin test reads: this seam's own `.pdata` extent.
///
/// `verify-rva-map-1170.py` reports `PDATA:0xae/0xae` for this pair and `PDATA:0xd2/0xd2` for the
/// twin, so a window of `0xae` stays inside the function being tested and still reaches both of
/// the twin's writes -- the first is at `+0x87`, measured at that offset in `eldenring-deobf.bin`,
/// `eldenring-deobf-1.17.bin` and `eldenring-deobf-1.17.1.bin` alike, with none in this one.
#[cfg(windows)]
const TWIN_TEST_WINDOW: usize = 0xae;

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
/// `CSPlayerMenuCtrl + 0x8` -- `selectedGoodsItemId`, the item whose popup this is.
///
/// The one field that distinguishes a finger's range dialog from any other state this controller
/// passes through. The step alone cannot: `+0x10` runs `0 -> 1 -> 2 -> 3 -> 0` across a single
/// use, so 3 is a value the object holds in the ordinary course of events and a gate on it fires
/// for whatever else brings the controller through the same number.
#[cfg(windows)]
const CTRL_SELECTED_GOODS: usize = 0x8;
/// The ctrl's step, and the two values that mean a row was actually pressed.
#[cfg(windows)]
const CTRL_STEP_OFFSET: usize = 0x10;
#[cfg(windows)]
const CTRL_STEP_CHOSE_ROW: [i32; 2] = [3, 4];
/// The step the ctrl holds for as long as the bounds popup is on screen with no row pressed.
///
/// Measured live on run `br-20260918-041114-10d1` through a Frida hook on this same handler: with
/// the popup up, the ctrl tick reported `step=2` at every 900-call heartbeat, indefinitely, and
/// never any other value.
#[cfg(windows)]
const CTRL_STEP_BOUNDS_POPUP_OPEN: i32 = 2;
/// `CSMenuManImp + 0x90` -- `field99_0x90`, the shown-menu-window flags, one byte per window.
///
/// Not a keystate array and not indexed by an input event id, which is the correction this whole
/// block exists to record. Ghidra's curated `CSMenuManImp` declares it `byte[70]`, sitting between
/// `windowJob` at `+0x88` and the next field at `+0xd6`, and both readers in the 1.17 image index
/// it by a menu-window index they first resolve from a menu KIND:
///
/// ```text
///   getShownMenuFlags 0x1407664f0:  idx = FUN_140767df0(scratch, kind); if (idx < 0x47)
///                                   flags |= ... (field99_0x90[idx] & 1)
///   FUN_1407c3210:                  idx = FUN_140768c70(scratch, 2 / 0x1c / 0x1f / 0x20);
///                                   ... (field99_0x90[idx] & 3) == 3
/// ```
///
/// Every read tests `& 1` or `& 3`, and the bound is the array's own length. So an index is a
/// window, not a button, and a bit is a window's state.
#[cfg(windows)]
const SHOWN_MENU_WINDOW_FLAGS_OFFSET: usize = 0x90;
/// How many windows that array holds -- `byte[70]` in the curated `CSMenuManImp`, and `< 0x47` in
/// every one of the game's own bounds checks.
///
/// The previous walk here read `0x80` entries, so `0x46` bytes of it were fields past the end of
/// the array being reported as menu events.
#[cfg(windows)]
const SHOWN_MENU_WINDOW_COUNT: usize = 0x46;
/// The last window census reported, so a change is one line and a quiet frame is none.
#[cfg(windows)]
static SHOWN_MENU_WINDOWS_SAID: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
/// `GLOBAL_CSPcKeyConfig` -- the singleton holding what the player has each action bound to.
///
/// Read out of the game's own menu-input layer rather than picked: `FUN_140756e50` loads
/// `qword ptr [0x143d61f08]`, null-checks it against the `FD4Singleton` assert, and passes it
/// straight to [the action lookup](`KEY_CONFIG_ACTION_TABLE_OFFSET`). The rva sits in `.data`,
/// whose addresses the 1.17.0 -> 1.17.1 step left alone -- that patch moved `.text` entries at or
/// above rva `0xafefe9` by `0x70` and nothing else -- so this address is the installed build's.
#[cfg(windows)]
const CS_PC_KEY_CONFIG_GLOBAL_RVA: usize = 0x3d6_1f08;
/// Where the rebindable-action table starts inside `CSPcKeyConfig`, and how a row is addressed.
///
/// `FUN_140242ab0(cfg, out, action, deviceKind)` is the whole definition:
///
/// ```text
///   if (action < 0x36) { row = cfg + action * 0x14 + 0x440; FUN_140242b00(row, out, kind); }
///   else                 FUN_140242a90(out);            // unbound
/// ```
#[cfg(windows)]
const KEY_CONFIG_ACTION_TABLE_OFFSET: usize = 0x440;
#[cfg(windows)]
const KEY_CONFIG_ACTION_STRIDE: usize = 0x14;
/// The action row the game's menu back-out lives in.
///
/// Identified from the table's own contents, not from a name -- the 1.17 dump carries none for
/// this layer. `FUN_140242b00` splits a row by device: kind 0 reads word 0, kind 1 reads words
/// 1-2, kind 2 reads words 3-4. Word 3 of rows `0x2c`/`0x2d` is `9`/`10`, which is exactly what
/// `FUN_140756e50` compares its kind-2 answer against, so word 0 is the pad button and word 1 the
/// keyboard key.
///
/// Word 1 turns out to be a DirectInput scancode biased by [`KEYBOARD_CODE_DIK_BIAS`], which seven
/// rows of the live table prove at once: rows `0x01`-`0x04` hold `0x11`/`0x1f`/`0x1e`/`0x20`
/// (`W`/`S`/`A`/`D`, the movement block), row `0x18` holds `0x01` (`Escape`, beside the pad's
/// start button), row `0x34` holds `0x1c` (`Enter`) and row `0x35` holds `0x0e` (`Backspace`).
/// Under that decode row `0x25` holds `0x10` -- `Q` -- and row `0x22` holds `0x12` -- `E`. The
/// player, 2026-09-18: "I can press q to go back from a menu normally". Rows `0x22` and `0x25`
/// are also the pair the menu-input query family asks about together (`FUN_14075dd40` takes
/// `0x22`, `FUN_14075dcc0` takes `0x25`, adjacent records in one descriptor array).
#[cfg(windows)]
const MENU_BACK_ACTION: usize = 0x25;
/// Word 0 of a row: the pad button code, in the game's own `2000`..`2013` numbering.
#[cfg(windows)]
const KEY_CONFIG_PAD_CODE_OFFSET: usize = 0x0;
/// Word 1 of a row: the keyboard key.
#[cfg(windows)]
const KEY_CONFIG_KEYBOARD_CODE_OFFSET: usize = 0x4;
/// What word 1 adds to a DirectInput scancode. See [`MENU_BACK_ACTION`] for the seven rows that
/// pin it.
#[cfg(windows)]
const KEYBOARD_CODE_DIK_BIAS: i32 = 0x45;
/// What the game writes into a row's word for a device the action is not bound on.
#[cfg(windows)]
const KEY_CONFIG_UNBOUND: i32 = -1;
/// Bit `0x80` of a DirectInput scancode means the `0xe0`-prefixed key of that number.
#[cfg(windows)]
const DIK_EXTENDED_BIT: u32 = 0x80;
/// The `0xe0` prefix spelled the way `MapVirtualKeyW` wants a scancode carrying one.
#[cfg(windows)]
const SCANCODE_EXTENDED_PREFIX: u32 = 0xe000;
/// `MAPVK_VSC_TO_VK_EX` -- scancode to virtual key, keeping left and right apart.
#[cfg(windows)]
const MAPVK_VSC_TO_VK_EX: u32 = 3;

/// What the player's key config currently binds the menu back-out to: `(pad code, keyboard key)`.
#[cfg(windows)]
fn menu_back_binding() -> Option<(i32, i32)> {
    let base = er_game_base::mem::game_module_base().ok()?;
    let config = er_game_base::mem::read_global_ptr(
        base,
        CS_PC_KEY_CONFIG_GLOBAL_RVA,
        "GLOBAL_CSPcKeyConfig",
    );
    if config == 0 {
        return None;
    }
    let row = config.checked_add(
        KEY_CONFIG_ACTION_TABLE_OFFSET + MENU_BACK_ACTION * KEY_CONFIG_ACTION_STRIDE,
    )?;
    // SAFETY: both reads are bounds-checked `safe_read_i32`, at a row the game's own lookup
    // addresses the same way, in a singleton that has just been null-checked.
    let pad = unsafe { er_game_base::mem::safe_read_i32(row + KEY_CONFIG_PAD_CODE_OFFSET) }?;
    let key = unsafe { er_game_base::mem::safe_read_i32(row + KEY_CONFIG_KEYBOARD_CODE_OFFSET) }?;
    Some((pad, key))
}

/// Turn a key-config keyboard word into the virtual key `GetAsyncKeyState` answers for.
///
/// The layout the player is typing on does the translating, not a table of ours: a scancode names
/// a physical key and `MapVirtualKeyW` asks the active layout what that key produces, so `Q` on
/// azerty resolves to `A` without this code knowing azerty exists.
#[cfg(windows)]
fn virtual_key_for(keyboard_code: i32) -> Option<i32> {
    let scancode = u32::try_from(keyboard_code.checked_sub(KEYBOARD_CODE_DIK_BIAS)?).ok()?;
    if scancode == 0 || scancode > u32::from(u8::MAX) {
        return None;
    }
    let scancode = if scancode & DIK_EXTENDED_BIT == 0 {
        scancode
    } else {
        SCANCODE_EXTENDED_PREFIX | (scancode & !DIK_EXTENDED_BIT)
    };
    // SAFETY: a `user32` call taking two integers and returning one, with no pointer anywhere.
    let virtual_key = unsafe { MapVirtualKeyW(scancode, MAPVK_VSC_TO_VK_EX) };
    i32::try_from(virtual_key).ok().filter(|key| *key != 0)
}

/// The key this player backs out of menus with, asked of the game on every poll.
///
/// Not a constant and no longer a setting of ours, and the four builds it took to get here are the
/// argument. The first closed on `Escape` alone; this player uses `Q`. The second added `Q` beside
/// `Escape`, and the answer was "Just so you're aware, user's can rebind keys and gamepad
/// buttons", which no list of constants satisfies however long. The third put the key in
/// `er-invasion-warp.toml`, and the answer to that was "That's a terrible solution. The game
/// already allows users to configure buttons ... the game should just track what keys are used and
/// we don't have to in a config."
///
/// It does track them, in [`CS_PC_KEY_CONFIG_GLOBAL_RVA`], and this reads the same row the game's
/// own menu-input layer reads. Rebind the back-out in Key Bindings and the next poll sees the new
/// key; there is nothing to keep in sync and nothing for a player to edit.
///
/// The fallback is only for a frame where the singleton is not up yet or the row reads unbound.
/// Losing that read should cost a player nothing, and `Q` is what this build's own table holds.
#[cfg(windows)]
fn cancel_key_in_force() -> i32 {
    menu_back_binding()
        .filter(|(_, key)| *key != KEY_CONFIG_UNBOUND)
        .and_then(|(_, key)| virtual_key_for(key))
        .unwrap_or(er_invasion_warp_core::keybind::VK_Q)
}
/// `XINPUT_GAMEPAD_B` -- the pad half, and not a guess at all: `B` is the game's own cancel.
#[cfg(windows)]
const XINPUT_GAMEPAD_B: u16 = 0x2000;
/// The pad slot read. A second pad answers on another slot, and a run where the player's pad is
/// not slot 0 should read no buttons rather than quietly read somebody else's.
#[cfg(windows)]
const XINPUT_PLAYER_SLOT: u32 = 0;
/// `XINPUT_STATE` is `DWORD dwPacketNumber` then `XINPUT_GAMEPAD`, whose first field is
/// `WORD wButtons`, so the buttons sit at `+4` and the whole struct is 16 bytes.
#[cfg(windows)]
const XINPUT_STATE_SIZE: usize = 16;
#[cfg(windows)]
const XINPUT_BUTTONS_OFFSET: usize = 4;
/// Bit 15 of a `GetAsyncKeyState` answer: down right now.
///
/// Only this bit is read. Bit 0 is "pressed since the last call" and it is consumed by whoever
/// reads it, so touching it here would eat the edge `local_invasion_filter::hotkeys` is watching
/// for on its own keys -- and eating a player's keypress to detect a keypress is a bug wearing an
/// oracle's coat.
#[cfg(windows)]
const KEY_DOWN_MASK: i16 = -0x8000;
/// How far the cancel census sweeps. The Win32 virtual-key space is `0x01`..=`0xfe`.
#[cfg(windows)]
const VK_CENSUS_MAX: i32 = 0xfe;
/// Whether the cancel input was already down on the previous frame, so the store happens on the
/// edge and not on the level.
///
/// The handler runs once a frame for as long as the dialog is up, so a level test fires for every
/// frame the button is held -- dozens of writes for one press, into a ctrl that left the popup on
/// the second of them.
#[cfg(windows)]
static CANCEL_WAS_DOWN: core::sync::atomic::AtomicBool = core::sync::atomic::AtomicBool::new(false);
/// Whether the cancel input has been seen released since this popup opened.
///
/// An edge detector alone is not enough when the press that opened the dialog is still down on the
/// frame it first ticks: a latch starting at "not down" reads frame one as a rising edge and
/// closes a popup the player never saw.
#[cfg(windows)]
static CANCEL_RELEASED_SINCE_OPEN: core::sync::atomic::AtomicBool =
    core::sync::atomic::AtomicBool::new(false);
/// The last cancel-census line, so a press is one line and a quiet frame is none.
#[cfg(windows)]
static PRESSED_INPUTS_SAID: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
#[cfg(windows)]
const MULTI_REGION_OFF: u8 = 0;
/// Said once when a popup that is not a finger's reaches the takeover, so a menu that trips the
/// step every frame does not fill the log with the same sentence.
#[cfg(windows)]
static UNROUTED_POPUP_SAID: core::sync::atomic::AtomicBool =
    core::sync::atomic::AtomicBool::new(false);

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
    // Everything about the back-out is decided ahead of the original, because the back-out is an
    // argument to it and not a repair afterwards -- see [`player_backed_out`] for the run where
    // the repair lost to the original's own return value three times inside one popup.
    //
    // SAFETY: one dword inside the ctrl this handler was called with, fault-closed.
    let popup_is_up = unsafe { er_game_base::mem::safe_read_i32(ctrl + CTRL_STEP_OFFSET) }
        == Some(CTRL_STEP_BOUNDS_POPUP_OPEN);
    let backing_out = popup_is_up && cancelled & 0xff == 0 && {
        report_shown_menu_windows(cancelled);
        report_pressed_cancel_inputs();
        player_backed_out()
    };
    let cancelled = if backing_out { 1 } else { cancelled };
    // SAFETY: the trampoline stored for this exact target, called with its own arguments.
    let answer = unsafe {
        core::mem::transmute::<usize, er_hook::UnionFn>(orig)(ctrl, cancelled, popup, spare)
    };
    if backing_out {
        crate::standalone_log(format_args!(
            "vanilla-fingers: you cancelled at the invasion-bounds popup, so the game's own \
             back-out arm ran -- this handler was given cancelled=1 and it answered {answer}, the \
             same call and the same answer vanilla makes when its own press term fires. That term \
             cannot fire here: it looks up menu entry id 0xf and no menu owner in the process \
             carries one, measured on run br-20260918-051844-3aec."
        ));
        return answer;
    }
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
    // The popup is up and nobody has pressed a row. Report what the menu system is showing,
    // because the one thing this dialog cannot currently do is close.
    //
    // The player, 2026-09-17: the bounds popup "cannot be backed out of with normal means" -- and
    // the game formats a `Back` key guide for it (`GRHK:110000` in the msgbox-builder line), so a
    // back-out is advertised and does nothing.
    //
    // # The trigger that used to be here, and the static reading that removed it
    //
    // A first pass read `CSMenuMan + 0x90 + 0x2b` and treated bit 2 as the player's `Back`, on the
    // strength of two live samples in which that byte was the only one in the span to move, `0x03
    // -> 0x07`. It closed the popup by writing step 5. The player, one build later: "the menu pops
    // up and then closes automatically."
    //
    // It is not a keystate. `field99_0x90` is `byte[70]` on the curated `CSMenuManImp`, and both of
    // the 1.17 image's readers index it by a menu-window index resolved from a menu kind, bounded
    // `< 0x47`, testing `& 1` or `& 3` -- see [`SHOWN_MENU_WINDOW_FLAGS_OFFSET`]. Index `0x2b` is a
    // window and bit 2 is one of that window's flags, which the menu system raises by itself a
    // frame or two after this dialog appears. Run `br-20260918-050115-c1ba` is that failure in the
    // log: three raises at `+65378ms`, `+66286ms`, `+69167ms`, each followed within a frame by the
    // census reporting `0x2b=0x03 -> 0x2b=0x07` and this module announcing a `Back` nobody pressed.
    //
    // # Where the real back-out lives, measured since
    //
    // The `dl` this detour receives as `cancelled` is not computed where the earlier note said. The
    // ctrl tick `FUN_1407c2ae0` takes `(ctrl, param_2)` and, at step 2, calls
    // `FUN_1407c3210(ctrl, param_2)`, whose whole body is
    //
    // ```text
    //   return backOut | shownWindowKind2 | shownWindowKind1c | (shownKind1f && shownKind20);
    // ```
    //
    // with `backOut` being the tick's own `param_2` passed through. So the three window terms are
    // vanilla's "another menu came up, abandon this" and `param_2` is the press. `param_2` in turn
    // is `A || B` from `FUN_140660f70`: `A = owner[0x1c5] >> 1 & 1`, `B = FUN_1404fa370(owner +
    // 0x178, 0xf)` -- a pending-event lookup that walks the list at `source + 0x8` for a child
    // whose `+0x156` is the event id. That corrects the earlier note claiming `0x14066161b` "is not
    // the frame that feeds this ctrl": it is, through the switch arm at case `0x15`/`0x16`.
    //
    // # Why the mod supplies the close instead of making vanilla's fire
    //
    // Both halves of `param_2` were measured live on run `br-20260918-051844-3aec`, through a Frida
    // hook on predicate B, which is handed the owner every frame. With the popup on screen for
    // about 2,000 sampled frames, the owner belonging to this ctrl -- matched by `owner + 0x6a0` --
    // never changed once: its flag byte read `0x8` every frame, so `A`'s bit 1 is clear, and its
    // entry list held `0x131` four times, `0x143`, `0x116`, `0x93` and eight zeroes, fixed.
    //
    // Then the decisive one. Across all 55 menu owners the process holds, the entry ids present are
    //
    // ```text
    //   0x0 0x8 0x47 0x78 0x8f 0x93 0x10b 0x116 0x130 0x131
    //   0x143 0x187 0x18f 0x19c 0x19d 0x1a3 0x1a4 0x1a9 0x1fb
    // ```
    //
    // and `0xf` is on none of them. Predicate B looks up an entry that is not registered anywhere in
    // the process, so it cannot answer yes for any owner, ever. The press is not being dropped:
    // there is nothing for it to set. That also retires the older reading that Back "fires
    // 0x12f/0x130/0x131/0x19c/0x1a4" -- those are these same steady registrations, present every
    // frame whether or not anybody presses.
    //
    // So vanilla's back-out cannot run here, and this mod is what put the dialog on screen
    // (`crate::can_use_goods_gate` forces `CS::CanUseGoods` true for the three fingers inside a
    // Seamless session, and without it the raiser skips the dialog entirely). The mod owes the way
    // out, and [`player_backed_out`] is it: the player's own cancel, read off a real keystate, fed
    // to the original as the `cancelled` argument vanilla's own press term would have set.
    if step == CTRL_STEP_BOUNDS_POPUP_OPEN {
        return answer;
    }
    // The controller is not showing the popup, so the next one that opens needs its own release
    // before a press counts. Without this reset the arming survives from one dialog to the next and
    // the second popup closes on the button still down from the first.
    #[cfg(windows)]
    CANCEL_RELEASED_SINCE_OPEN.store(false, core::sync::atomic::Ordering::SeqCst);
    if !CTRL_STEP_CHOSE_ROW.contains(&step) {
        return answer;
    }
    // SAFETY: one byte inside the same object, fault-closed.
    let Some(flag) =
        (unsafe { er_game_base::mem::safe_read_u8(ctrl + CTRL_IS_BREAK_IN_MULTI_REGION) })
    else {
        return answer;
    };
    // Which item raised this popup. Without it the takeover has no idea what it is adopting.
    //
    // Reported twice by the user, in the same words both times: "Found a host in Highroad Cross --
    // invading ( I stood still and did not invade)", then "but I did not invade. Seamless produces
    // a message when I'm invading". Run br-20260917-224016-b8a1 is that report: the takeover
    // adopted `NearbyOnly` at `step=3`, armed a search, and `local-invasion: about to drive ERSC
    // invade` drove Seamless's own invade action -- the log's own words for it are "this drives a
    // row the user could not have clicked". Nothing above it names an item, because nothing above
    // it ever read one.
    //
    // The two gates that were here cannot carry that weight. `isBreakInMultiRegion` is a byte that
    // reads 0 at rest and 0 for `Nearby only`, so it never refuses anything. And the step is a
    // small integer on `CSPlayerMenuCtrl`, an object the whole player menu shares; a comment ten
    // lines up already records the step returning to 3 on a frame no row was pressed, which is
    // what the 214 spurious adoptions on run br-20260916-005322-27ca were.
    //
    // A goods id cannot be produced by accident: it is `0x40000000 | row`, and only the three
    // fingers route this dialog. A popup that is not one of theirs is somebody else's business.
    let selected = unsafe { er_game_base::mem::safe_read_i32(ctrl + CTRL_SELECTED_GOODS) }
        .map(|item| item as u32);
    let Some(selected) = selected.filter(|item| routes_the_range_popup(*item)) else {
        if !UNROUTED_POPUP_SAID.swap(true, Ordering::SeqCst) {
            crate::standalone_log(format_args!(
                "vanilla-fingers: the bounds popup reached step={step} with selectedGoodsItemId={:?},                  which is not one of the three invasion fingers -- declining it. This module drives                  Seamless's invade action, so adopting a popup nobody raised with a finger starts an                  invasion the player never asked for. Printed once; the decline itself is every time.",
                selected.map(|item| format!("{item:#x}")),
            ));
        }
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
        // The recital goes with it, inside `stand_down_hunt` since 2026-09-17 -- every caller of
        // that function means stop, and each of them was clearing the queue separately or, in
        // three cases, not at all.
        crate::local_invasion_filter::stand_down_hunt(
            "the finger's search could not start, so its override is retired rather than left \
             armed with nothing running behind it",
        );
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
    // Names the item this popup belongs to, because the line below arms an invasion search and
    // there is no other record of what raised it.
    crate::standalone_log(format_args!(
        "vanilla-fingers: the bounds popup chose {range:?} (isBreakInMultiRegion={flag}, \
         step={step}), adopted={adopted}, driven_inline={driven}, requested={requested}, \
         selectedGoodsItemId={selected:#x}"
    ));
    answer
}

/// Log which menu windows the front end has up while the bounds popup waits, on change only.
///
/// # What this is for
///
/// Two of the four terms that make the popup close are in this array, and the census is how a run
/// says which. `FUN_1407c3210` -- the function whose return value arrives at this module as
/// `cancelled` -- ORs the tick's own back-out argument with three shown-window tests, so a window
/// coming up is a legitimate reason for this dialog to tear itself down and is worth telling apart
/// from a press in the log.
///
/// # What it is not, and the build that proved it
///
/// It is not a keystate census and it cannot name the player's `Back`. An earlier form of this
/// function walked the same bytes calling them menu events, and the module closed the popup on
/// bit 2 of index `0x2b`. `field99_0x90` is `byte[70]` indexed by a menu-window index; that bit
/// belongs to a window, the menu system sets it a frame or two after this dialog appears, and the
/// popup closed itself on every raise. Run `br-20260918-050115-c1ba` carries all three.
///
/// The walk is bounded to the array's own length now. The previous one ran `0x80` entries and so
/// reported `0x46` bytes of unrelated `CSMenuManImp` fields as if they were part of it.
///
/// # Safety
///
/// Game menu thread, and every read is fault-closed: an unresolved global or an unmapped page
/// yields `None` and the sample is skipped rather than faulting. It writes nothing.
#[cfg(windows)]
fn report_shown_menu_windows(cancelled: usize) {
    let Ok(module_base) = er_game_base::mem::game_module_base() else {
        return;
    };
    // SAFETY: fault-closed read of a data global whose address is translated for the running build.
    let Some(manager) = (unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            module_base,
            er_game_base::rva::CS_MENU_MAN_GLOBAL_RVA,
            "CS_MENU_MAN_GLOBAL_RVA",
        ))
    })
    .filter(|&pointer| pointer > 0x10000) else {
        return;
    };
    let mut shown: Vec<String> = Vec::new();
    for window in 0..SHOWN_MENU_WINDOW_COUNT {
        // SAFETY: one byte inside the manager's own shown-window array, fault-closed, and the walk
        // stops at the length Ghidra declares and the game's own bounds checks use.
        let Some(flags) = (unsafe {
            er_game_base::mem::safe_read_u8(manager + SHOWN_MENU_WINDOW_FLAGS_OFFSET + window)
        }) else {
            return;
        };
        if flags != 0 {
            shown.push(format!("{window:#04x}={flags:#04x}"));
        }
    }
    let now = if shown.is_empty() {
        format!("dl={cancelled:#x} windows=none")
    } else {
        format!("dl={cancelled:#x} windows={}", shown.join(","))
    };
    let Ok(mut said) = SHOWN_MENU_WINDOWS_SAID.lock() else {
        return;
    };
    if said.as_deref() == Some(now.as_str()) {
        return;
    }
    let before = said.clone().unwrap_or_else(|| "nothing yet".to_owned());
    *said = Some(now.clone());
    crate::standalone_log(format_args!(
        "vanilla-fingers: the bounds popup is open and the menu state changed {before} -> {now} \
         (`CSMenuManImp+0x90`, one byte per menu window, and the `dl` this handler was passed). A \
         window appearing is one of the four terms that close this dialog; the player's Back is \
         the `dl` term and it has never yet been anything but zero."
    ));
}

/// Host-side stub: there is no menu manager to read off the target.
#[cfg(not(windows))]
fn report_shown_menu_windows(_cancelled: usize) {}

#[cfg(windows)]
#[link(name = "user32")]
unsafe extern "system" {
    fn GetAsyncKeyState(vkey: i32) -> i16;
    fn MapVirtualKeyW(code: u32, map_type: u32) -> u32;
}

/// Whether one virtual key is down right now, asking only for the bit that is not consumed.
#[cfg(windows)]
fn key_is_down(vkey: i32) -> bool {
    // SAFETY: a `user32` call taking one integer and returning one, with no pointer anywhere.
    (unsafe { GetAsyncKeyState(vkey) } & KEY_DOWN_MASK) != 0
}

/// The slot-0 pad's button mask, or `None` when no pad answers.
///
/// `XInputGetState` is resolved from a module the game has already loaded rather than
/// `LoadLibrary`d, so a profile where the player is on keyboard adds nothing to the process and
/// simply reads `None`. The game loads `xinput1_4.dll` on this build -- measured 2026-09-17 by
/// resolving the export off the live process -- and the older names are tried after it because a
/// resolution that fails is cheaper than an assumption that does not.
#[cfg(windows)]
fn pad_buttons() -> Option<u16> {
    use core::sync::atomic::{AtomicUsize, Ordering};
    use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
    use windows::core::s;

    static CACHED: AtomicUsize = AtomicUsize::new(0);
    const MISSING: usize = usize::MAX;

    let resolved = match CACHED.load(Ordering::SeqCst) {
        0 => {
            // SAFETY: resolving one export from modules that may or may not be loaded.
            let found = unsafe {
                [
                    s!("xinput1_4.dll"),
                    s!("xinput1_3.dll"),
                    s!("xinput9_1_0.dll"),
                ]
                .into_iter()
                .find_map(|name| {
                    GetModuleHandleA(name)
                        .ok()
                        .and_then(|module| GetProcAddress(module, s!("XInputGetState")))
                })
                .map_or(0, |address| address as usize)
            };
            CACHED.store(if found == 0 { MISSING } else { found }, Ordering::SeqCst);
            found
        }
        MISSING => 0,
        address => address,
    };
    if resolved == 0 {
        return None;
    }
    let mut state = [0u8; XINPUT_STATE_SIZE];
    // SAFETY: the resolved `XInputGetState`, called with its own signature and a buffer of exactly
    // the size the struct it fills declares.
    let status = unsafe {
        core::mem::transmute::<usize, unsafe extern "system" fn(u32, *mut u8) -> u32>(resolved)(
            XINPUT_PLAYER_SLOT,
            state.as_mut_ptr(),
        )
    };
    if status != 0 {
        return None;
    }
    Some(u16::from_le_bytes([
        state[XINPUT_BUTTONS_OFFSET],
        state[XINPUT_BUTTONS_OFFSET + 1],
    ]))
}

/// Whether the player is holding a cancel right now, on either input the game itself accepts.
#[cfg(windows)]
fn cancel_is_down() -> bool {
    key_is_down(cancel_key_in_force())
        || pad_buttons().is_some_and(|mask| mask & XINPUT_GAMEPAD_B != 0)
}

/// Name every key and pad button held while the bounds popup waits, on change only.
///
/// # What this is for
///
/// One value: whichever key the player's cancel actually is, against the
/// [`cancel_key_in_force`] this code closes on. It has already earned its place once -- the first
/// build here closed on `Escape` alone, and this census is what turned "I can't close it with Q"
/// into `keys=0x51` on `br-20260918-161158-02c4`, a number rather than an argument. It is also how
/// a wrong [`MENU_BACK_ACTION`] would show itself: the key the player holds appears here beside
/// the key the game's config says that action is bound to, and the two disagreeing is the whole
/// diagnosis.
///
/// The pad half needs no such census -- `B` is the game's own cancel -- but it is printed beside
/// the keys so a run says which device the player is actually on.
///
/// # Safety
///
/// `GetAsyncKeyState` takes and returns an integer. Bit 0 is deliberately never read: it is the
/// consumed "pressed since the last call" bit, and `local_invasion_filter::hotkeys` is polling its
/// own keys through the same call.
#[cfg(windows)]
fn report_pressed_cancel_inputs() {
    let cancel = cancel_key_in_force();
    let mut held: Vec<String> = Vec::new();
    for vkey in 1..=VK_CENSUS_MAX {
        if key_is_down(vkey) {
            held.push(format!("{vkey:#04x}"));
        }
    }
    let pad = pad_buttons().map_or_else(|| "no pad".to_owned(), |mask| format!("{mask:#06x}"));
    let now = if held.is_empty() {
        format!("keys=none pad={pad}")
    } else {
        format!("keys={} pad={pad}", held.join(","))
    };
    let Ok(mut said) = PRESSED_INPUTS_SAID.lock() else {
        return;
    };
    if said.as_deref() == Some(now.as_str()) {
        return;
    }
    *said = Some(now.clone());
    let bound = menu_back_binding().map_or_else(
        || "unreadable, so the fallback is in force".to_owned(),
        |(pad_code, keyboard_code)| format!("pad code {pad_code}, keyboard word {keyboard_code}"),
    );
    crate::standalone_log(format_args!(
        "vanilla-fingers: the bounds popup is open and what you are holding changed to {now}. Your \
         menu back-out is {} ({:#04x}), read from the game's own key config at action \
         {MENU_BACK_ACTION:#04x} ({bound}), and pad B ({XINPUT_GAMEPAD_B:#06x}) closes it too. \
         Rebind it in Key Bindings and this follows on the next frame.",
        er_invasion_warp_core::keybind::key_name(cancel),
        cancel,
    ));
}

/// Host-side stub: there is no keyboard or pad attached to a test binary.
#[cfg(not(windows))]
fn report_pressed_cancel_inputs() {}

/// Give the bounds popup the back-out the game advertises and, in this session, cannot deliver.
///
/// # What the player loses without this
///
/// The dialog is inescapable. Reported 2026-09-17, twice: it "cannot be backed out of with normal
/// means", then "I also can't close it myself". The game formats a `Back` key guide for it --
/// `GRHK:110000` in the msgbox-builder line -- so the prompt offers an exit that does nothing.
///
/// # Why the mod owes it
///
/// This mod is what puts the dialog on screen: `crate::can_use_goods_gate` forces `CS::CanUseGoods`
/// true for the three invasion fingers inside a Seamless session, and without that the raiser skips
/// the popup entirely. And vanilla's own back-out cannot fire here, which is measured rather than
/// assumed -- `dl`'s press term is `A || B`, `A` is a flag bit at `owner + 0x1c5` that stayed clear
/// for ~2,000 frames, and `B` looks up menu entry id `0xf`, which exists on none of the 55 menu
/// owners the live process holds. Nothing outside can make that arm fire.
///
/// # Why this only answers, and never writes the step itself
///
/// It used to write step 5 into the ctrl straight after the original returned, which is where
/// `0x1407c2c64`'s `movl $0x5, 0x10(%rcx)` puts it. Run `br-20260918-170827-5e73` is that idea
/// failing three times inside one popup: pad `B` was held, the write fired and logged, and the
/// next frame's census reported the dialog still open at step 2.
///
/// The reason is the function's own shape. It is
///
/// ```text
///   if (cancelled) { ctrl[0x10] = 5; return 1; }
///   ... read the pressed row; if none, return 0 ...
/// ```
///
/// so the back-out is not only a store, it is also the `1` the caller gets back. Running the
/// original with `cancelled = 0` takes the row path, which answers `0` for "nothing happened", and
/// patching the step behind it leaves the tick holding that `0` to return upward. The answer and
/// the field have to move together, so the way to move them together is to let the game move both:
/// this decides whether the player backed out, and the detour hands `cancelled = 1` to the
/// original. Nothing writes the step but the game's own instruction.
///
/// # Why the release latch
///
/// A bare edge detector is not enough. The previous attempt at this trigger read a menu-window flag
/// that rises by itself a frame or two after the dialog appears, and the popup closed on every
/// raise -- "the menu pops up and then closes automatically". A real keystate cannot do that, but
/// the press that confirmed the item can still be down on the frame the dialog first ticks, so a
/// press only counts once a release has been seen while this popup was up.
///
/// Consumes the press when it answers `true`, so one push is one back-out.
#[cfg(windows)]
fn player_backed_out() -> bool {
    use core::sync::atomic::Ordering;

    let down = cancel_is_down();
    let was_down = CANCEL_WAS_DOWN.swap(down, Ordering::SeqCst);
    // A release, at any point while the dialog is up, is what arms the detector.
    if !down {
        CANCEL_RELEASED_SINCE_OPEN.store(true, Ordering::SeqCst);
        return false;
    }
    // Still the press that raised the popup: it has never been let go, so this is not a new one.
    if was_down || !CANCEL_RELEASED_SINCE_OPEN.load(Ordering::SeqCst) {
        return false;
    }
    CANCEL_RELEASED_SINCE_OPEN.store(false, Ordering::SeqCst);
    true
}

/// Host-side stub: there is no input to read.
#[cfg(not(windows))]
fn player_backed_out() -> bool {
    false
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

/// Take the player's `Yes` on `Cancel invasion of other world?` and stop the search this mod is
/// actually running.
///
/// The original runs first, for the same reason it does on the start path: it reads the pressed
/// row out of the popup and advances the menu's own step machine, and vanilla's own search is
/// vanilla's to call off.
///
/// What it cannot call off is ours. The vanilla `BreakInManager` search and the Seamless search
/// this crate drives are two different searches that the same item starts, so cancelling one left
/// the other running, the sweep armed, the finger override in force and the place recital cycling
/// its ring -- the screen still naming locations for a search the player had just stopped.
///
/// # What the gate is, and why it is not the reach
///
/// `cancelled` is the popup's own back-out, so it passes straight through: the player declined to
/// cancel and the search must survive. Past that, the step and the item are the same pair the
/// start path uses -- the original advances the step only on `Yes`, and a goods id cannot be
/// produced by accident since only the three fingers route this dialog.
///
/// There is deliberately no test on [`crate::local_invasion_filter::finger_reach`]. A search that
/// has already handed its far half to Seamless holds `FINGER_REACH_NONE` while still very much
/// running, and the player pressing `Yes` means stop either way. Everything below is a no-op when
/// there was nothing to stop: `stand_down_hunt` logs only when the loop was armed, `end_search`
/// forgets a sweep that may not exist, and `cancel_live_search_for_player` declines an idle
/// session.
///
/// # Safety
///
/// Called by MinHook in place of the game's function, on the game's own menu thread.
#[cfg(windows)]
unsafe extern "system" fn cancel_invasion_entry(
    ctrl: usize,
    cancelled: usize,
    popup: usize,
    spare: usize,
) -> usize {
    use core::sync::atomic::Ordering;

    let orig = ORIG_CANCEL_INVASION.load(Ordering::SeqCst);
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
    // SAFETY: one dword inside the object the game just finished writing, fault-closed.
    let Some(step) = (unsafe { er_game_base::mem::safe_read_i32(ctrl + CTRL_STEP_OFFSET) }) else {
        return answer;
    };
    if !CTRL_STEP_CHOSE_ROW.contains(&step) {
        return answer;
    }
    // SAFETY: one dword inside the same object, fault-closed.
    let selected = unsafe { er_game_base::mem::safe_read_i32(ctrl + CTRL_SELECTED_GOODS) }
        .map(|item| item as u32);
    let Some(selected) = selected.filter(|item| routes_the_range_popup(*item)) else {
        return answer;
    };
    // `stand_down_hunt` retires the whole search, the place recital and the banner's repeat latch
    // included, so the line painted below cannot land under another place name.
    crate::local_invasion_filter::stand_down_hunt(
        "you used the finger again and confirmed the invasion search should be called off",
    );
    // The search is over rather than widening, so the band ladder goes back with it. A rung left
    // standing would start the next invasion at a band the player never climbed to.
    crate::lobby_preflight::end_search();
    announce_search_called_off();
    crate::standalone_log(format_args!(
        "vanilla-fingers: the player called the invasion search off from the finger's own cancel \
         prompt (step={step}, selectedGoodsItemId={selected:#x}). The re-search loop, the finger's \
         range override, the nearby sweep, the band ladder and the place recital are all retired, \
         and Seamless's own search is cancelled where its state still offers a Cancel row."
    ));
    answer
}

/// Whether the address this seam resolves to is the call-off handler and not its start-path twin.
///
/// The one check that does not trust the address map. See [`CANCEL_VANILLA_INVASION`] for what a
/// swapped row would cost; the byte sequence is the `isBreakInMultiRegion` write, which the start
/// handler performs twice and this one never.
///
/// # Safety
///
/// Game task thread. Reads a window of the running image through the fault-closed reader.
#[cfg(windows)]
unsafe fn the_cancel_seam_is_not_its_twin(address: usize) -> bool {
    let mut body = [0_u8; TWIN_TEST_WINDOW];
    // SAFETY: a read of mapped executable bytes through the fault-closed reader.
    if !unsafe { er_game_base::mem::read_bytes(address, &mut body) } {
        return false;
    }
    !body
        .windows(IS_BREAK_IN_MULTI_REGION_WRITE.len())
        .any(|window| window == IS_BREAK_IN_MULTI_REGION_WRITE)
}

/// Arm the detour that takes the finger's own call-off prompt.
///
/// Idempotent and fail-closed, like its sibling: a build whose bytes do not match the recorded
/// prologue is refused, and so is an address that reads as the start-path twin.
///
/// # Safety
///
/// Game task thread, after the module base resolves.
#[cfg(windows)]
pub unsafe fn install_cancel_prompt_takeover() -> bool {
    use core::sync::atomic::{AtomicUsize, Ordering};

    static REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);

    if ORIG_CANCEL_INVASION.load(Ordering::SeqCst) != 0 {
        return true;
    }
    // SAFETY: game task thread; the seam checks its own prologue and refuses otherwise.
    let address = match unsafe { crate::map_seams::verify_seam(&CANCEL_VANILLA_INVASION) } {
        Ok(address) => address,
        Err(error) => {
            if REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "vanilla-fingers: refused {} -- {error}. Using a finger a second time still \
                     calls vanilla's own search off, but this mod's search keeps running behind \
                     it and the place recital keeps reciting. Printed once.",
                    CANCEL_VANILLA_INVASION.name
                ));
            }
            return false;
        }
    };
    // `verify_seam` hands back the unresolved 1.16.2 address, which is what MinHook is given and
    // what it resolves itself. The twin test has to read the bytes that will actually be patched,
    // so it asks the same resolver for the same answer -- a second call, and a quiet one: the
    // translation ledger logs an address once.
    let resolved =
        er_game_base::game_build::resolve_detour_address(address, CANCEL_VANILLA_INVASION.name)
            .unwrap_or(address);
    // SAFETY: game task thread; the reader is fault-closed and the window is bounded.
    if !unsafe { the_cancel_seam_is_not_its_twin(resolved) } {
        if REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
            crate::standalone_log(format_args!(
                "vanilla-fingers: refused {} @0x{resolved:x} -- the body writes \
                 `isBreakInMultiRegion`, which only the start-path twin does, so the address map \
                 has pointed this seam at {}. Installing here would stand every search down one \
                 frame after arming it. Printed once.",
                CANCEL_VANILLA_INVASION.name, START_VANILLA_INVASION.name
            ));
        }
        return false;
    }
    let hook = match unsafe {
        er_hook::MhHook::new(
            address as *mut core::ffi::c_void,
            cancel_invasion_entry as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            crate::standalone_log(format_args!(
                "vanilla-fingers: failed to create the cancel-prompt detour @0x{address:x} -- \
                 {status:?}. The address resolved and its prologue matched."
            ));
            return false;
        }
    };
    ORIG_CANCEL_INVASION.store(hook.trampoline() as usize, Ordering::SeqCst);
    // SAFETY: the hook was created above; enabling is MinHook's own queued path.
    if unsafe { hook.queue_enable() }.is_err() {
        ORIG_CANCEL_INVASION.store(0, Ordering::SeqCst);
        return false;
    }
    // SAFETY: applies the queue this function just added to.
    match unsafe { er_hook::MH_ApplyQueued() } {
        er_hook::MH_STATUS::MH_OK => {
            crate::standalone_log(format_args!(
                "vanilla-fingers: armed the cancel-prompt takeover on {} @0x{address:x}. Using a \
                 finger a second time now stops this mod's search as well as vanilla's.",
                CANCEL_VANILLA_INVASION.name
            ));
            true
        }
        status => {
            ORIG_CANCEL_INVASION.store(0, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "vanilla-fingers: MH_ApplyQueued refused the cancel-prompt detour -- {status:?}"
            ));
            false
        }
    }
}

/// Host-side stub.
#[cfg(not(windows))]
pub fn install_cancel_prompt_takeover() -> bool {
    false
}

/// Say on screen that the search the player just called off has stopped.
///
/// Without it the last search banner sits on screen through its own fade, still naming a place,
/// for a search that no longer exists -- which reads as the cancel having failed. That is the same
/// complaint the recital half of this fix answers, one surface further along.
#[cfg(windows)]
fn announce_search_called_off() {
    use core::sync::atomic::{AtomicUsize, Ordering};

    static CALLED_OFF_BANNER_FAILED: AtomicUsize = AtomicUsize::new(0);

    let text = "Invasion search called off";
    // SAFETY: the game's menu thread with the menu up, the same surface `announce_search` uses --
    // the prompt the player just answered is still the thing on screen.
    if unsafe { crate::announce::show(text) } {
        return;
    }
    if CALLED_OFF_BANNER_FAILED.swap(1, Ordering::SeqCst) == 0 {
        crate::standalone_log(format_args!(
            "vanilla-fingers: could not show the called-off banner (\"{text}\") -- the message \
             functions did not verify, or the menu is not up. The search is still stopped; only \
             the on-screen notice is missing. Printed once."
        ));
    }
}

/// Host-side stub.
#[cfg(not(windows))]
fn announce_search_called_off() {}

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
