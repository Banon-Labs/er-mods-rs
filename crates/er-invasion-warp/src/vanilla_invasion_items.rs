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
    let requested = match range {
        SearchRange::BothNearAndFar => {
            crate::lynchpin_use::request_lynchpin_use_offthread();
            crate::standalone_log(format_args!(
                "vanilla-fingers: Both near and far hands off to the Challenger's Lynchpin itself                  -- its item path is what queries Steam, where calling ersc's action directly only                  sets the state and never searches."
            ));
            true
        }
        SearchRange::NearbyOnly => {
            crate::local_invasion_filter::arm_invade_request("a vanilla invasion finger")
        }
    };
    let driven = false;
    announce_search(range);
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
