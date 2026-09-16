//! Using the Challenger's Lynchpin without the wait and without the popup.
//!
//! Three separate mechanisms, each measured live on 2026-09-09 through Frida before any of it was
//! written here, and each recorded in `bd driving-an-inventory-item-use-needs-chrins-0x168`:
//!
//! 1. **Driving the use.** The inventory's `Use` command is only
//!    `CSMenuGaitemUseState::Request(&CSMenuMan->menuData->menuGaitemUseState, gaitem, arg)`, four
//!    stores into a 24-byte struct. `CS::PlayerIns::GetSelectedQuickSlotItemId` then overrides the
//!    real quick slot from that struct's `+0xc` unconditionally, which is the only reason an item
//!    that lives solely in the inventory can be used at all.
//! 2. **Making the use actually do something.** `ChrIns+0x168` is the repeat count TAE event 65
//!    loops on: `for (n = chrIns->field49_0x168; n != 0; n--)`. At zero the event fires and its
//!    whole body is skipped, so the animation plays, nothing is consumed, no effect applies and
//!    Seamless never hears about it. Five live drives died on exactly that and read as a broken
//!    item.
//! 3. **Shortening the animation.** `EquipParamGoods.goodsUseAnim`, the `u8` at row `+0x42`.
//!
//! # The animation lengths are measured, not chosen
//!
//! Each candidate was driven once and its clip read out of the TimeAct ring, where `animLength` is
//! the clip's length in the character's own seconds:
//!
//! | `goodsUseAnim` | TimeAct | seconds |
//! | --- | --- | --- |
//! | 66 (what ersc.dll stamps) | 50530 | 5.000 |
//! | 8 (vanilla invasion fingers) | 50030 | 3.900 |
//! | 6 (Tiny Great Pot) | 50230 | 3.167 |
//! | 17 (Throwing Dagger) | 55000 | 1.433 |
//!
//! `CSChrBehaviorModule::animSpeedGradientMultiplier` was tried first and is not the lever: the
//! field takes the write, reads back changed, and the animation still runs its full length.
//!
//! # Why the row is written at runtime rather than in `regulation.bin`
//!
//! The Lynchpin's row is not in the regulation at all. `ersc.dll` allocates 0xb0 bytes at init and
//! stamps `goodsUseAnim = 0x42` into it, so the only place the value exists is the live row that
//! `EquipParamGoods::GetEntry` hands back (bd
//! `goods-use-anim-is-equipparamgoods-0x42-lynchpin-is-runtime-synthesised-2026-09-09`). One byte,
//! once, after Seamless has registered its rows.
//!
//! # The popup is skipped, not accepted and dismissed
//!
//! The first working version took Seamless's option and then called
//! `CSMenuManImp::CloseMenu(CSMenuMan, -1)`. That dismissed *every* dialog the game opened,
//! including the one the same item raises to leave an invasion, which has an activation window --
//! and it trapped the player in an invasion with no way out. See
//! `bd auto-dismiss-must-be-scoped-not-every-dialog-2026-09-09`.
//!
//! So the dialog is never built into view instead: the detour on `OpenConversationChoicesMenu`
//! declines to call the original only when Seamless's session reads idle, which is true when the
//! item is offering to start a search and never true once an invasion is live. Every other dialog,
//! the leave prompt included, falls through untouched.

#[cfg(windows)]
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

#[cfg(windows)]
use crate::map_seams::{MapSeam, verify_seam};

/// `CSMenuMan+0x8` -> `CSMenuData`, then `+0x70` -> `CSMenuGaitemUseState`.
#[cfg(windows)]
const MENU_GAITEM_USE_STATE_OFFSET: usize = 0x70;
/// Within `CSMenuGaitemUseState`: request state, 0 idle, 1 requested, 2 latched.
#[cfg(windows)]
const USE_STATE_OFFSET: usize = 0x8;
/// The item id the quick-slot getter overrides with, carrying the `0x4` goods category nibble.
#[cfg(windows)]
const USE_ITEM_ID_OFFSET: usize = 0xc;
/// The inventory index the use consumes from.
#[cfg(windows)]
const USE_ITEM_IDX_OFFSET: usize = 0x10;
/// The argument the inventory command passes through; zero is what the menu sends for a plain use.
#[cfg(windows)]
const USE_ARG_OFFSET: usize = 0x14;
/// `ChrIns+0x168`, unnamed in the 1.16.2 dump: the repeat count TAE event 65 loops on.
#[cfg(windows)]
const CHR_INS_CONSUME_COUNT_OFFSET: usize = 0x168;
/// `PlayerGameData+0x2b0` -> `EquipGameData`.
#[cfg(windows)]
const PLAYER_GAME_DATA_EQUIP_GAME_DATA_OFFSET: usize = 0x2b0;
/// `EquipGameData+0x158` -> `EquipInventoryData`. `GetEquipInventoryData` is
/// `lea rax,[rcx+0x158]; ret` on both builds, so this is an offset and not a call.
#[cfg(windows)]
const EQUIP_INVENTORY_DATA_OFFSET: usize = 0x158;
/// `EquipParamGoods.goodsUseAnim`, `u8`, within a 0xb0-byte row.
#[cfg(windows)]
const GOODS_USE_ANIM_OFFSET: usize = 0x42;
/// The Challenger's Lynchpin, as the menu spells it: goods `8380003` with the goods category
/// nibble.
#[cfg(windows)]
const LYNCHPIN_ITEM_ID: u32 = 0x407f_de63;
/// The same id as the param table spells it.
#[cfg(windows)]
const LYNCHPIN_GOODS_ID: u32 = 0x7f_de63;
/// The Throwing Dagger's use animation: TimeAct 55000, 1.433 seconds, measured.
#[cfg(windows)]
const SHORT_USE_ANIM: u8 = 17;
/// How many frames the use-state override is held. It is cleared back to -1 within about nine
/// frames when no menu is open, so a single write is gone before the animation asks for it.
#[cfg(windows)]
const PIN_FRAMES: usize = 90;

/// `EquipParamGoods::GetEntry(EquipParamGoodsLookupResult *out, uint id) -> out`.
#[cfg(windows)]
const EQUIP_PARAM_GOODS_GET_ENTRY_RVA: u32 = 0x00d3_9df0;

/// `CS::CSMenuMan::OpenConversationChoicesMenu` -- the game function Seamless opens its option
/// menu through. Its own body stamps the menu id and hands a job to the menu system; declining to
/// call it is what keeps the menu from ever appearing.
#[cfg(windows)]
const OPEN_CONVERSATION_CHOICES_MENU: MapSeam = MapSeam {
    name: "CS::CSMenuMan::OpenConversationChoicesMenu",
    rva: 0x00e9_e4f0,
    prologue: &[
        0x40, 0x53, 0x48, 0x83, 0xec, 0x30, 0x48, 0x8b, 0xd9, 0x33, 0xd2,
    ],
    arg_count: 1,
};

/// The original `OpenConversationChoicesMenu`, once the detour is in.
#[cfg(windows)]
static ORIG_OPEN_CHOICES: AtomicUsize = AtomicUsize::new(0);
/// Whether the animation byte has been written this session.
#[cfg(windows)]
static ANIM_SHORTENED: AtomicUsize = AtomicUsize::new(0);
/// Whether the row lookup has already said it could not resolve. Without this the tick says so
/// once a frame: the first build of this module wrote 2,685 identical refusal lines into one
/// session's log, which is the shape that makes a log unreadable rather than informative.
#[cfg(windows)]
static ROW_REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);
/// The same latch for the popup skip, for the same reason.
#[cfg(windows)]
static SKIP_REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);
/// Frames of use-state override still owed.
#[cfg(windows)]
static PIN_FRAMES_LEFT: AtomicUsize = AtomicUsize::new(0);
/// `GameMan->isInOnlineMode`, the single byte `IsInOnlineMode()` returns.
///
/// The whole function is `return GLOBAL_GameMan->isInOnlineMode` -- 15 bytes, `mov rax,[rip+d]` /
/// `movzx eax,byte [rax+0xbc8]` / `ret` -- so the flag can be written directly and no detour has to
/// go anywhere near it. The offset is `0xbc8` on both 1.16.2 and 1.17.1, read out of the two
/// de-Arxan'd images; the singleton itself is [`er_game_base::rva::GAME_MAN_SINGLETON_RVA`], which
/// the address translation already carries.
#[cfg(windows)]
const GAME_MAN_IS_IN_ONLINE_MODE: usize = 0xbc8;
/// `menuGaitemUseState+0x8` once the engine has taken the request: 0 idle, 1 requested, 2 latched.
#[cfg(windows)]
const USE_STATE_REQUESTED: u8 = 1;
#[cfg(windows)]
const USE_STATE_LATCHED: u8 = 2;
/// `ChrIns+0x160` -- `tae_queued_use_item`, the id the character is actually using. It is `-1`
/// when nothing is in flight, so it doubles as the "still going" signal that keeps the pin alive.
#[cfg(windows)]
const CHR_INS_QUEUED_USE_ITEM: usize = 0x160;
/// The hard ceiling on a keep-alive, in frames. The longest use animation measured is the vanilla
/// invasion fingers' own, TimeAct 50030 at 3.900s, so ten seconds is generous without being open
/// ended if the field ever sticks.
#[cfg(windows)]
const PIN_FRAMES_MAX: usize = 600;
/// Frames spent on the use in flight, against [`PIN_FRAMES_MAX`].
#[cfg(windows)]
static PIN_FRAMES_SPENT: AtomicUsize = AtomicUsize::new(0);
/// Where the near+far handoff has got to: nothing, waiting for the pinned Lynchpin to latch, or
/// holding the use action down for its edge.
#[cfg(windows)]
static HANDOFF_STAGE: AtomicUsize = AtomicUsize::new(0);
#[cfg(windows)]
const HANDOFF_IDLE: usize = 0;
#[cfg(windows)]
const HANDOFF_WAITING_FOR_LATCH: usize = 1;
#[cfg(windows)]
const HANDOFF_PRESSING: usize = 2;
/// How long to wait between the Lynchpin being pinned and the use action being pressed, and how
/// long to hold it, in milliseconds rather than ticks.
///
/// Ticks are not frames here and that is the whole reason this did not work. This task runs far
/// faster than the display: a 90-tick pin window expired before a 20ms sampler could catch the
/// online flag it sets, measured on 2026-09-16, so a "150 frame" settle and a "30 frame" hold were
/// a small fraction of the 2.5s and 0.5s the drive that works uses. Counting ticks was me assuming
/// a tick rate I had already measured to be wrong.
#[cfg(windows)]
const HANDOFF_SETTLE_BEFORE_PRESS_MS: u64 = 2500;
#[cfg(windows)]
const HANDOFF_PRESS_HELD_MS: u64 = 500;
/// When the Lynchpin was pinned, as milliseconds since the process started. Zero means "not yet".
#[cfg(windows)]
static HANDOFF_PINNED_AT_MS: AtomicU64 = AtomicU64::new(0);
#[cfg(windows)]
static HANDOFF_PRESSED_AT_MS: AtomicU64 = AtomicU64::new(0);

/// Milliseconds since this module first asked, from a monotonic clock.
#[cfg(windows)]
fn now_ms() -> u64 {
    use std::sync::OnceLock;
    use std::time::Instant;

    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}
/// Whether the latch has been reported for the use in flight.
#[cfg(windows)]
static USE_ACKNOWLEDGED: AtomicUsize = AtomicUsize::new(0);
/// The value to put back, plus one, so that zero can mean "not currently held".
#[cfg(windows)]
static ONLINE_MODE_RESTORE: AtomicUsize = AtomicUsize::new(0);
/// The inventory index the current override is pinning.
#[cfg(windows)]
static PINNED_ITEM_IDX: AtomicUsize = AtomicUsize::new(0);
/// The item id the current override is pinning. The four stores used to name
/// [`LYNCHPIN_ITEM_ID`] directly, which made the whole driver a Lynchpin driver; the mechanism is
/// the same for any goods row, and the vanilla invasion fingers need it too.
#[cfg(windows)]
static PINNED_ITEM_ID: AtomicUsize = AtomicUsize::new(0);
/// An item id asked for from outside the game thread, resolved to an inventory index on the next
/// tick. [`request_use`] reads the inventory itself, so it may only be called from the game task;
/// an export cannot honour that, and a thread that reads a moving inventory list is the kind of
/// bug that shows up as a wrong item being used rather than as a fault.
#[cfg(windows)]
static REQUESTED_ITEM_ID: AtomicUsize = AtomicUsize::new(0);

/// Latched once the first non-Lynchpin menu is let through, so the line prints once.
#[cfg(windows)]
static FOREIGN_MENU_REPORTED: AtomicUsize = AtomicUsize::new(0);
/// Whether the pass-through branch has said so once. It was entirely silent until 2026-09-09,
/// which is how a log showing one skip and nothing else read as the feature working while every
/// use after the first showed the dialog.
#[cfg(windows)]
static PASS_REPORTED: AtomicUsize = AtomicUsize::new(0);
/// Dialogs this module declined to open, and dialogs it let through.
#[cfg(windows)]
static POPUPS_SKIPPED: AtomicUsize = AtomicUsize::new(0);
#[cfg(windows)]
static POPUPS_PASSED: AtomicUsize = AtomicUsize::new(0);

/// The live `EquipParamGoods` row for one goods id, or `None` before the param tables are up.
///
/// # Safety
///
/// Game task thread. The call is the engine's own lookup and takes no lock this module holds.
#[cfg(windows)]
pub(crate) unsafe fn goods_row(goods_id: u32) -> Option<usize> {
    let base = er_game_base::mem::game_module_base().ok()?;
    // The engine's own precondition, and the reason the first build of this module killed the
    // process 1140ms into boot. `EquipParamGoods::GetEntry` opens by loading
    // `GLOBAL_SoloParamRepository` and, when it is null, calls the assert at 1.17.1
    // `0x141ebb610` with file `0x1429caaa0` line 0xb4 -- and that assert path itself faults on a
    // null `rcx` this early, so the refusal is a `0xc0000005`, not a returned error. Reading the
    // slot first turns the engine's fatal precondition into this function's `None`.
    if er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::SOLO_PARAM_REPOSITORY_GLOBAL_RVA,
        "SOLO_PARAM_REPOSITORY_GLOBAL_RVA",
    ) == 0
    {
        return None;
    }
    let entry = er_game_base::mem::game_rva_named(
        EQUIP_PARAM_GOODS_GET_ENTRY_RVA,
        "EQUIP_PARAM_GOODS_GET_ENTRY_RVA",
    )
    .ok()?;
    // `EquipParamGoodsLookupResult` is `{ int paramId; int _pad; _EQUIP_PARAM_GOODS_ST *row; }`.
    let mut lookup: [usize; 2] = [usize::MAX, 0];
    type GetEntryFn = unsafe extern "system" fn(*mut usize, u32) -> *mut usize;
    // SAFETY: the address is version-translated and the shape is the engine's own two-argument
    // lookup; the out-parameter is this frame's stack.
    let get_entry: GetEntryFn = unsafe { core::mem::transmute(entry) };
    // SAFETY: as above.
    unsafe { get_entry(lookup.as_mut_ptr(), goods_id) };
    let row = lookup[1];
    (row != 0).then_some(row)
}

/// Write the shorter use animation onto the live row, once per session.
///
/// Returns whether the row was found and written. A row that is not there yet is not a failure:
/// `ersc.dll` registers it during its own init, so an early tick simply tries again next frame.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
pub unsafe fn shorten_use_animation() -> bool {
    if ANIM_SHORTENED.load(Ordering::SeqCst) != 0 {
        return true;
    }
    // SAFETY: game task thread; returns `None` rather than faulting before the tables exist.
    let Some(row) = (unsafe { goods_row(LYNCHPIN_GOODS_ID) }) else {
        if ROW_REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
            crate::standalone_log(format_args!(
                "lynchpin: the goods row is not resolvable yet -- either the param tables are not \
                 up or the lookup address was refused for this build. Retried every tick; this \
                 line is printed once."
            ));
        }
        return false;
    };
    let field = row + GOODS_USE_ANIM_OFFSET;
    // SAFETY: fault-tolerant read of one byte inside a row the engine just handed back.
    let before = unsafe { er_game_base::mem::safe_read_u8(field) };
    // SAFETY: same byte, inside the same row.
    unsafe { core::ptr::write_volatile(field as *mut u8, SHORT_USE_ANIM) };
    ANIM_SHORTENED.store(1, Ordering::SeqCst);
    crate::standalone_log(format_args!(
        "lynchpin: use animation {before:?} -> {SHORT_USE_ANIM} on the live row 0x{row:x}+0x42. \
         Measured lengths: 66 is TimeAct 50530 at 5.000s, 8 is 50030 at 3.900s, 6 is 50230 at \
         3.167s, 17 is 55000 at 1.433s. The row is not in regulation.bin -- ersc.dll allocates it \
         at init -- so this is the only place the value exists."
    ));
    true
}

/// `r14` as it stood when `OpenConversationChoicesMenu` was entered. The option-menu object is
/// `r14 - 0x120`.
///
/// # Measured, on the live game, not derived
///
/// A read-only Frida agent (`scripts/frida/ersc-owner-truth.js`) watched `ersc+0x241a0` and the
/// dialog opener in the same use of the item, on run `br-20260910-003108-c109`:
///
/// ```text
///   show:        osm=0x466ad518  session=0x466ac930  state=0x1  r12=0x7fde63
///   open_dialog: r12=0x45eae1c8  r14=0x466ad638  ret=0x18002438e
/// ```
///
/// `0x466ad638 - 0x120 == 0x466ad518`, exactly the object `show` was called with, and the return
/// address `0x18002438e` is inside `show` itself, so the opener is called from there directly. The
/// `0x120` is not a fitted constant either -- it is the third instruction of `show`:
///
/// ```text
///   ersc+0x241c8: mov  r12, rcx            <- rcx is the menu object
///   ersc+0x241cb: lea  r14, [rcx + 0x120]  <- and r14 keeps a fixed interior pointer to it
/// ```
///
/// Two earlier builds read `r12` instead, on the reasoning that `mov r12, rcx` makes it the menu
/// object. The register dump above is why that failed twice: at the opener `r12` is `0x45eae1c8`,
/// something else entirely, and at `show`'s own entry it is `0x7fde63` -- the Lynchpin's goods id,
/// arriving from show's caller. `r12` is only the menu object between `+0x241c8` and whatever
/// reuses it, which does not include this frame.
///
/// Nothing is written into `ersc.dll` for this: it is a register read in our own frame, so the
/// MinHook patch that faults a Themida-protected module at `0x140010043` is not involved.
///
/// A wrong read still cannot be believed -- `adopt_menu_object` requires `+0x58` to lead to an
/// object carrying a live session state before it stores anything.
#[cfg(windows)]
static SHOW_R14: AtomicUsize = AtomicUsize::new(0);

/// How far into the menu object `show` keeps `r14` pointing: `lea r14, [rcx + 0x120]`.
#[cfg(windows)]
const SHOW_R14_INTERIOR_OFFSET: usize = 0x120;

/// The registered detour: capture `r14`, then tail-jump to the real handler with the arguments
/// untouched.
///
/// It has to be naked. A plain Rust function may spill or reuse `r14` in its own prologue, so the
/// capture must be the very first instruction executed at the detour address.
///
/// # Safety
///
/// Installed as a bare detour on a byte-verified prologue; the jump preserves every argument
/// register.
#[cfg(windows)]
#[unsafe(naked)]
unsafe extern "system" fn open_choices_entry(a: usize, b: usize, c: usize, d: usize) -> usize {
    core::arch::naked_asm!(
        "mov qword ptr [rip + {slot}], r14",
        "jmp {handler}",
        slot = sym SHOW_R14,
        handler = sym open_choices_hook,
    )
}

/// The item id `CSMenuGaitemUseState` currently holds, or `None` when it cannot be read.
///
/// The detour this gates is on `CS::CSMenuMan::OpenConversationChoicesMenu`, a game function that
/// knows nothing about which item opened the menu. Without this check the skip fired for any
/// conversation-choices menu at all and drove an invasion from it.
///
/// Reported live 2026-09-10: "I used an item to open my world for other people to join and co-op,
/// and it started invading". Seamless builds several menus through that one function -- menu 0 is
/// `OPTIONSELECT_OPENWORLD`, menu 2 is the invasion menu -- and the old gate could not tell them
/// apart, because the only thing it asked was whether the session read idle, which is true for
/// both.
///
/// `CSMenuGaitemUseState+0xc` is the item id the game itself wrote for the use in flight, so it
/// names the item without this module having to guess from the menu.
#[cfg(windows)]
pub(crate) unsafe fn item_in_use() -> Option<u32> {
    let base = er_game_base::mem::game_module_base().ok()?;
    let menu_man = er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::CS_MENU_MAN_GLOBAL_RVA,
        "CS_MENU_MAN_GLOBAL_RVA",
    );
    if menu_man == 0 {
        return None;
    }
    // SAFETY: a global the engine owns, read through the fault-closed reader.
    let menu_data = unsafe {
        er_game_base::mem::safe_read_usize(
            menu_man + er_game_base::rva::CS_MENU_MAN_MENU_DATA_OFFSET,
        )
    }?;
    if menu_data == 0 {
        return None;
    }
    // SAFETY: the same struct `shorten_use_animation` writes, read rather than written.
    unsafe {
        er_game_base::mem::safe_read_i32(
            menu_data + MENU_GAITEM_USE_STATE_OFFSET + USE_ITEM_ID_OFFSET,
        )
    }
    .map(|raw| raw as u32)
}

/// The detour on `OpenConversationChoicesMenu`.
///
/// # Safety
///
/// Installed by the union on a byte-verified prologue; the ABI is `(dialog)`.
#[cfg(windows)]
unsafe extern "system" fn open_choices_hook(dialog: usize, b: usize, c: usize, d: usize) -> usize {
    // The Frida agent's `Interceptor.attach(ersc+0x241a0)` did this; the register read replaces it.
    // `adopt_menu_object` validates before storing, so a frame whose `r12` is not the menu object
    // is refused and the gate simply falls back.
    let menu_object = SHOW_R14
        .load(Ordering::SeqCst)
        .wrapping_sub(SHOW_R14_INTERIOR_OFFSET);
    let adopted = crate::local_invasion_filter::menu_object::adopt_menu_object(menu_object);
    // A hunt in flight means this menu is the player reaching for cancel, so it must be shown.
    //
    // Measured complaint, run br-20260910-012230-666e: the same item both starts and cancels a
    // search, and with the auto-loop running the session reads `0x01` idle for the instant between
    // our cancel and our re-invade. The gate saw idle, declined the dialog, and drove yet another
    // search -- so every attempt to cancel started one instead, and there was no point at which
    // the player could stop.
    //
    // Standing the loop down here is the same rule `show_observer` already applies to Seamless's
    // own menu: opening it is a deliberate act and it hands control back.
    if crate::local_invasion_filter::auto_search_armed() {
        crate::local_invasion_filter::stand_down_auto_search();
        POPUPS_PASSED.fetch_add(1, Ordering::SeqCst);
        let orig = ORIG_OPEN_CHOICES.load(Ordering::SeqCst);
        if orig == 0 {
            return 0;
        }
        // SAFETY: the union stored the trampoline for this exact target.
        return unsafe { core::mem::transmute::<usize, er_hook::UnionFn>(orig)(dialog, b, c, d) };
    }
    // Whose menu is this? An item that is not the Lynchpin gets its dialog, always.
    let using = unsafe { item_in_use() };
    if using != Some(LYNCHPIN_ITEM_ID) {
        if FOREIGN_MENU_REPORTED.swap(1, Ordering::SeqCst) == 0 {
            crate::standalone_log(format_args!(
                "lynchpin: a conversation-choices menu opened for item {using:#x?}, not the \
                 Lynchpin ({LYNCHPIN_ITEM_ID:#x}) -- letting it through untouched. This detour is \
                 on a game function that serves every such menu, and without this check it drove \
                 an invasion out of the co-op open-your-world menu. Printed once."
            ));
        }
        POPUPS_PASSED.fetch_add(1, Ordering::SeqCst);
        let orig = ORIG_OPEN_CHOICES.load(Ordering::SeqCst);
        if orig == 0 {
            return 0;
        }
        // SAFETY: the union stored the trampoline for this exact target.
        return unsafe { core::mem::transmute::<usize, er_hook::UnionFn>(orig)(dialog, b, c, d) };
    }
    let (idle, source) = crate::local_invasion_filter::popup_skip_gate_is_idle();
    if idle {
        POPUPS_SKIPPED.fetch_add(1, Ordering::SeqCst);
        // Inline, on this thread, which is the whole difference between this and the version that
        // hard-locked the game.
        //
        // Declining the dialog means the option is never chosen, so if nothing calls the invade
        // action no search starts at all -- the popup vanishes and the item does nothing. The
        // Frida prototype called `invadeAction(menuObject)` right here, from the replacement
        // itself, on the thread Seamless had already driven into: one entrant, no contention.
        //
        // `request_invade()` was used instead and it deadlocked, because arming moves the call to
        // the game task tick, and the player's next item use puts the game's own goods path inside
        // `ersc.dll` holding the session mutex at the same moment (run `br-20260909-234803-535c`:
        // `about to drive ERSC invade` with no successor line, 122 threads, 9 cpu ticks in 3s).
        //
        // `inside_ersc_callback()` does not refuse this: that guard is set by the ersc observers,
        // and this detour is on a game function and never enters it.
        // Through the real menu object when we have it, which is what Frida passed. The
        // synthesized-owner path is the fallback and it is the one that wedged the game, so it is
        // only reached when the register capture was refused.
        let started = if adopted {
            crate::local_invasion_filter::drive_invade_with_owner(
                menu_object,
                "the Lynchpin's own use",
            )
        } else {
            crate::local_invasion_filter::drive_invade_inline("the Lynchpin's own use")
        };
        crate::standalone_log(format_args!(
            "lynchpin: skipped Seamless's start-a-search popup and started the search inline \
             (started={started}, skipped {}, passed through {}) -- gate answered from {source}",
            POPUPS_SKIPPED.load(Ordering::SeqCst),
            POPUPS_PASSED.load(Ordering::SeqCst)
        ));
        return 0;
    }
    let passed = POPUPS_PASSED.fetch_add(1, Ordering::SeqCst) + 1;
    if PASS_REPORTED.swap(1, Ordering::SeqCst) == 0 {
        crate::standalone_log(format_args!(
            "lynchpin: a dialog was let through because the session does not read idle \
             (passed {passed}, skipped {}) -- gate answered from {source}. If this was \
             the item's own start-a-search prompt then the gate is wrong, not the \
             dialog. Printed once.",
            POPUPS_SKIPPED.load(Ordering::SeqCst)
        ));
    }
    let orig = ORIG_OPEN_CHOICES.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    // SAFETY: the union stored the trampoline for this exact target.
    unsafe { core::mem::transmute::<usize, er_hook::UnionFn>(orig)(dialog, b, c, d) }
}

/// Install the popup skip. Idempotent; returns whether the detour is in force.
///
/// # Safety
///
/// Game task thread, after the runtime is up.
#[cfg(windows)]
pub unsafe fn install_popup_skip() -> bool {
    if ORIG_OPEN_CHOICES.load(Ordering::SeqCst) != 0 {
        return true;
    }
    // SAFETY: game task thread; the seam verifies its own prologue and refuses otherwise.
    let address = match unsafe { verify_seam(&OPEN_CONVERSATION_CHOICES_MENU) } {
        Ok(address) => address,
        Err(error) => {
            if SKIP_REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "lynchpin: refused {} -- {error}; the popup will appear as Seamless built it. \
                     This line is printed once, not once per tick.",
                    OPEN_CONVERSATION_CHOICES_MENU.name
                ));
            }
            return false;
        }
    };
    // A bare `MhHook`, deliberately not `register_union_hook`, and the reason is the register
    // capture above.
    //
    // The union does not put `open_choices_entry` at the detour address -- it installs
    // `union_dispatch`, an ordinary Rust function that loads its head handler and calls it. That
    // prologue runs before the naked shim does, and it does not preserve `r12` for a callee: on
    // run `br-20260910-000334-453e` the capture came back `0x1` and the log said `refused an
    // option-menu object handed in at 0x1`. A bare hook puts the shim's first instruction at the
    // detour address, which is the only place the register is still Seamless's.
    //
    // The cost is the union's one guarantee: if another feature ever hooks this same function,
    // MinHook binds one detour per address and one of the two is silently dropped. Nothing else
    // in this DLL touches `OpenConversationChoicesMenu` today, and a capture that reads `1` is
    // worth nothing at all, so the trade is made here and written down rather than assumed.
    let hook = match unsafe {
        er_hook::MhHook::new(
            address as *mut core::ffi::c_void,
            open_choices_entry as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            crate::standalone_log(format_args!(
                "lynchpin: FAILED to create the popup-skip detour @0x{address:x} -- {status:?}. \
                 The address resolved and its prologue matched, so this is MinHook refusing a \
                 verified address; the popup will appear as Seamless built it"
            ));
            return false;
        }
    };
    ORIG_OPEN_CHOICES.store(hook.trampoline() as usize, Ordering::SeqCst);
    // SAFETY: the hook was created above; enabling is MinHook's own queued path.
    if unsafe { hook.queue_enable() }.is_err() {
        ORIG_OPEN_CHOICES.store(0, Ordering::SeqCst);
        return false;
    }
    // SAFETY: applies the queue this function just added to.
    match unsafe { er_hook::MH_ApplyQueued() } {
        er_hook::MH_STATUS::MH_OK => {
            crate::standalone_log(format_args!(
                "lynchpin: armed the popup skip on {} @0x{address:x} as a bare detour, so `r14` \
                 still points 0x120 into Seamless's option-menu object when it runs (measured, \
                 not assumed: `lea r14,[rcx+0x120]` at ersc+0x241cb, and a live dump gave \
                 r14=0x466ad638 against show's own osm=0x466ad518) -- a dialog is declined only \
                 while that object's session reads idle, so the leave-invasion prompt still opens",
                OPEN_CONVERSATION_CHOICES_MENU.name
            ));
            true
        }
        status => {
            ORIG_OPEN_CHOICES.store(0, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "lynchpin: FAILED to enable the popup skip @0x{address:x} -- {status:?}"
            ));
            false
        }
    }
}

/// `CS::PlayerIns::GetSelectedQuickSlotItemId(PlayerIns*, int *out)` -- what the character asks
/// when the use action fires, to find out which item it is using.
///
/// # Why the answer is given here rather than stamped into the struct
///
/// The engine's own override reads `menuGaitemUseState+0xc`, so the documented way to drive an
/// inventory-only item is to re-stamp that field for the length of the use. This module did that
/// from its `CSTaskImp` task and it never once produced a use -- measured on run
/// br-20260916-070713-aabd with a control: the Challenger's Lynchpin, pinned at inventory index
/// 1701, behaved exactly like the finger at 429, and nothing in `ChrIns+0x150..0x180` moved for
/// either. The request itself was accepted every time, `menuGaitemUseState+0x8` stepping `0 -> 2`
/// under an 8ms sampler, so the press was reaching the player and only the item was missing.
///
/// Nothing orders a game task against this reader, and a store that lands after its reader has run
/// is invisible to it however many frames it is repeated. Answering the question directly removes
/// the ordering from the problem: while a use of ours is in flight, this is the item.
///
/// The seam is `verify_seam`-gated like the others. Its prologue is identical on both builds
/// (`48 89 5c 24 10 57 48 83 ec 20 c7 02 ff ff ff ff` -- the `*out = -1` is right there in it), and
/// it is byte-identical to the shipped image in live memory, unlike `CanUseGoods`.
#[cfg(windows)]
const SELECTED_QUICK_SLOT_ITEM: crate::map_seams::MapSeam = crate::map_seams::MapSeam {
    name: "CS::PlayerIns::GetSelectedQuickSlotItemId",
    rva: 0x0065_65c0,
    prologue: &[0x48, 0x89, 0x5c, 0x24, 0x10, 0x57, 0x48, 0x83, 0xec, 0x20],
    arg_count: 2,
};

/// The trampoline for [`SELECTED_QUICK_SLOT_ITEM`].
#[cfg(windows)]
static ORIG_SELECTED_QUICK_SLOT: AtomicUsize = AtomicUsize::new(0);

/// Answer the character with the pinned item while one of our uses is in flight.
///
/// # Safety
///
/// Called by MinHook in place of the game's function, on the game's own thread.
#[cfg(windows)]
unsafe extern "system" fn selected_quick_slot_entry(player: usize, out: *mut i32) -> *mut i32 {
    static ANSWERED_SAID: AtomicUsize = AtomicUsize::new(0);

    type SelectedQuickSlotFn = unsafe extern "system" fn(usize, *mut i32) -> *mut i32;

    let orig = ORIG_SELECTED_QUICK_SLOT.load(Ordering::SeqCst);
    if orig == 0 {
        return out;
    }
    // SAFETY: the trampoline MinHook returned, with both arguments untouched. The original runs
    // first so that everything it does to `out` and to the pouch slot still happens.
    let result = unsafe { core::mem::transmute::<usize, SelectedQuickSlotFn>(orig)(player, out) };
    if PIN_FRAMES_LEFT.load(Ordering::SeqCst) == 0 {
        return result;
    }
    let pinned = PINNED_ITEM_ID.load(Ordering::SeqCst);
    if pinned == 0 || out.is_null() {
        return result;
    }
    // SAFETY: the out parameter the caller passed and the original just wrote through.
    unsafe { core::ptr::write_volatile(out, pinned as i32) };
    if ANSWERED_SAID.swap(1, Ordering::SeqCst) == 0 {
        crate::standalone_log(format_args!(
            "lynchpin: answered the quick-slot question with {pinned:#x} -- the character asked \
             which item it is using while one of ours was pinned. Printed once."
        ));
    }
    result
}

/// Put [`selected_quick_slot_entry`] in front of the game's getter.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn install_selected_quick_slot() -> bool {
    static REFUSAL_SAID: AtomicUsize = AtomicUsize::new(0);

    if ORIG_SELECTED_QUICK_SLOT.load(Ordering::SeqCst) != 0 {
        return true;
    }
    // SAFETY: game task thread; the seam checks its own prologue and refuses otherwise.
    let address = match unsafe { crate::map_seams::verify_seam(&SELECTED_QUICK_SLOT_ITEM) } {
        Ok(address) => address,
        Err(error) => {
            if REFUSAL_SAID.swap(1, Ordering::SeqCst) == 0 {
                crate::standalone_log(format_args!(
                    "lynchpin: refused {} -- {error}. A pinned use will be accepted and then use \
                     nothing, because the character has no way to learn which item it is. \
                     Printed once.",
                    SELECTED_QUICK_SLOT_ITEM.name
                ));
            }
            return false;
        }
    };
    let hook = match unsafe {
        er_hook::MhHook::new(
            address as *mut core::ffi::c_void,
            selected_quick_slot_entry as *mut core::ffi::c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            crate::standalone_log(format_args!(
                "lynchpin: failed to create the quick-slot detour @0x{address:x} -- {status:?}"
            ));
            return false;
        }
    };
    ORIG_SELECTED_QUICK_SLOT.store(hook.trampoline() as usize, Ordering::SeqCst);
    // SAFETY: the hook was created above; enabling is MinHook's own queued path.
    if unsafe { hook.queue_enable() }.is_err() {
        ORIG_SELECTED_QUICK_SLOT.store(0, Ordering::SeqCst);
        return false;
    }
    // SAFETY: applies the queue this function just added to.
    match unsafe { er_hook::MH_ApplyQueued() } {
        er_hook::MH_STATUS::MH_OK => {
            crate::standalone_log(format_args!(
                "lynchpin: armed the quick-slot answer on {} @0x{address:x}",
                SELECTED_QUICK_SLOT_ITEM.name
            ));
            true
        }
        status => {
            ORIG_SELECTED_QUICK_SLOT.store(0, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "lynchpin: MH_ApplyQueued refused the quick-slot detour -- {status:?}"
            ));
            false
        }
    }
}

/// Ask for the Lynchpin to be used, starting on the next tick.
///
/// Returns whether the item was found in the inventory to use.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
pub unsafe fn request_use() -> bool {
    // SAFETY: game task thread, which is this function's own contract.
    unsafe { request_use_item(LYNCHPIN_ITEM_ID) }
}

/// Use one held item by id, starting on the next tick.
///
/// Returns whether the item was found in the inventory to use. The id is the one the menu spells,
/// goods id with the category nibble -- `crate::vanilla_invasion_items::with_category` builds it
/// from a param row id.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
pub unsafe fn request_use_item(item_id: u32) -> bool {
    // SAFETY: game task thread; every read is fault-closed.
    let Some(index) = (unsafe { inventory_index(item_id) }) else {
        crate::standalone_log(format_args!(
            "lynchpin: asked to use the item, but it is not in the inventory -- it appears only \
             after sitting at a site of grace"
        ));
        return false;
    };
    PINNED_ITEM_IDX.store(index, Ordering::SeqCst);
    PINNED_ITEM_ID.store(item_id as usize, Ordering::SeqCst);
    USE_ACKNOWLEDGED.store(0, Ordering::SeqCst);
    PIN_FRAMES_SPENT.store(0, Ordering::SeqCst);
    PIN_FRAMES_LEFT.store(PIN_FRAMES, Ordering::SeqCst);
    crate::standalone_log(format_args!(
        "lynchpin: pinned item {item_id:#x} at inventory index {index} for {PIN_FRAMES} frame(s)"
    ));
    true
}

/// Ask for the Challenger's Lynchpin itself to be used, from any thread.
///
/// # Why the finger's near+far row routes here instead of calling the action
///
/// Calling `ersc+0x25850` directly is not the Lynchpin interaction, and an A/B on run
/// br-20260916-083935-5990 measured the difference on the matchmaking interface `ersc.dll` holds
/// at `ersc+0x21b610`, sweeping all 38 vtable slots so the count carries its own control:
///
/// | driven | `RequestLobbyList` | `AddRequestLobbyListStringFilter` |
/// | --- | --- | --- |
/// | `ersc+0x25850` called directly | 0 | 0 |
/// | the Lynchpin used as an item | 2 | 10 |
///
/// The direct call still moves `session+0x150` to `0x0e`, so it looks like a search and never
/// becomes one -- the state is a symptom of Seamless searching, not the cause of it. That is why
/// every search driven that way sat at `SEARCHING` and matched nobody.
#[cfg(windows)]
pub fn request_lynchpin_use_offthread() {
    // End the finger's own pin first, or this request waits behind it.
    //
    // `drain_requested_use` will not start a second use while one is in flight, and the finger's
    // pin is kept alive for as long as `tae_queued_use_item` holds it -- up to 600 frames, ten
    // seconds. This function is called from the finger's popup answer, so the finger has already
    // done its job; leaving its pin running only delays the handoff past the point where anything
    // is still waiting for it. Measured on run br-20260916-091135-c653: the handoff logged, the
    // Lynchpin was never pinned, and 38 of 38 matchmaking slots stayed at zero.
    //
    // Setting the counter to 1 rather than 0 is deliberate: the next tick then runs
    // `drive_pinned_use`'s own `left == 1` arm, which puts `menuGaitemUseState` back the way the
    // engine leaves it and restores `GameMan+0xbc8`. Zeroing it would skip that cleanup and leave
    // the game reading a finger as the selected quick item.
    if PIN_FRAMES_LEFT.load(Ordering::SeqCst) > 1 {
        // The pinned id is cleared too, or the keep-alive undoes this on the very next tick.
        //
        // `drive_pinned_use` resets the counter back to a full window for as long as
        // `tae_queued_use_item` still reads the pinned item, which it does here because the finger
        // is mid-use -- so shortening the counter alone achieved nothing and run
        // br-20260916-091849-c540 shows it: the handoff logged and the Lynchpin was never pinned,
        // because the counter never reached zero and `drain_requested_use` never ran. Zeroing the
        // id makes the keep-alive's comparison fail, the counter runs down to its `left == 1` arm,
        // and the cleanup there still restores `menuGaitemUseState` and `GameMan+0xbc8`.
        PINNED_ITEM_ID.store(0, Ordering::SeqCst);
        PIN_FRAMES_LEFT.store(1, Ordering::SeqCst);
    }
    request_use_item_offthread(LYNCHPIN_ITEM_ID);
    HANDOFF_PINNED_AT_MS.store(0, Ordering::SeqCst);
    HANDOFF_PRESSED_AT_MS.store(0, Ordering::SeqCst);
    HANDOFF_STAGE.store(HANDOFF_WAITING_FOR_LATCH, Ordering::SeqCst);
}

/// `er_quickload.dll`'s pad-injection export, or 0 when that DLL is not in the profile.
///
/// Resolved once and cached. A miss is inert: the handoff then pins the Lynchpin and waits for a
/// press that never comes, which is exactly what it did before this existed and is no worse.
#[cfg(windows)]
fn hold_pad() -> usize {
    use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
    use windows::core::s;

    static CACHED: AtomicUsize = AtomicUsize::new(0);
    const MISSING: usize = usize::MAX;

    match CACHED.load(Ordering::SeqCst) {
        0 => {}
        MISSING => return 0,
        address => return address,
    }
    // SAFETY: resolving one export from a module that may or may not be loaded.
    let resolved = unsafe {
        GetModuleHandleA(s!("er_quickload.dll"))
            .ok()
            .and_then(|module| GetProcAddress(module, s!("er_quickload_hold_xinput_pad")))
            .map_or(0, |address| address as usize)
    };
    CACHED.store(
        if resolved == 0 { MISSING } else { resolved },
        Ordering::SeqCst,
    );
    if resolved == 0 {
        crate::standalone_log(format_args!(
            "lynchpin: er_quickload.dll has no `er_quickload_hold_xinput_pad`, so the near+far \
             handoff can pin the Lynchpin but cannot press it. Load er-quickload in the profile."
        ));
    }
    resolved
}

/// Drive the use action for a handed-off Lynchpin, once its pin has latched.
///
/// # Why the handoff has to press at all
///
/// Pinning only answers the question "which item"; something still has to press Use. For the
/// finger that press is the player's own. The Lynchpin this hands off to has nobody pressing it,
/// and run br-20260916-085320-768e measured the consequence: the handoff logged, and then 38 of 38
/// matchmaking slots stayed at zero across two attempts, while driving the same Lynchpin by hand on
/// the same run produced `RequestLobbyList` and five filter calls.
///
/// # Why it waits for the latch instead of counting frames
///
/// `ChrIns+0x160` taking the item is the character saying it has accepted it. Pressing in the same
/// breath as the pin does not work -- four attempts that way produced nothing -- and a fixed delay
/// would be a guess at a frame budget dressed up as synchronisation.
///
/// The press is a single edge held for a few frames and then released. Holding it every frame is
/// what stopped the finger working: `GetSelectedGoodsUseAnim` lives in `HksEnv`, and a behaviour
/// script watching for a press sees one rising edge and then a level that never rises again.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn drive_handoff_press() {
    type HoldPadFn = unsafe extern "system" fn(u16, i16, i16) -> ();
    const PAD_A: u16 = 0x1000;

    match HANDOFF_STAGE.load(Ordering::SeqCst) {
        HANDOFF_WAITING_FOR_LATCH => {
            // Wait for the pin, not for the latch.
            //
            // Waiting on `ChrIns+0x160` was circular and mine: that field only takes the item once
            // the use has started, and the use starts because of this press. Run
            // br-20260916-090943-0e7e sat there forever -- the handoff logged, the decline was
            // gone, and the press line never printed because the condition could not become true.
            //
            // The pin being live is the right precondition: `drive_pinned_use` has by then written
            // the request and is answering the quick-slot question, so the press has something to
            // land on.
            let pinned_is_lynchpin =
                PINNED_ITEM_ID.load(Ordering::SeqCst) == LYNCHPIN_ITEM_ID as usize;
            if !pinned_is_lynchpin {
                return;
            }
            // Press a while after the pin, not the moment it appears.
            //
            // The recipe that works for the finger is pin, wait about two and a half seconds, then
            // one press -- pressing in the same breath as the pin produced nothing across four
            // attempts. This waited only for the pin to be live and pressed immediately, and run
            // br-20260916-091408-8820 shows the result: the Lynchpin pinned at inventory index
            // 1701, "the use action is pressed for 6 frame(s)" logged, and 38 of 38 matchmaking
            // slots still at zero.
            //
            // Measured on a clock rather than counted in ticks, for the reason on
            // `HANDOFF_SETTLE_BEFORE_PRESS_MS`. Read rather than slept on, because this runs on the
            // game task and a sleep here would stall the very frames it is waiting for.
            let pinned_at = HANDOFF_PINNED_AT_MS.load(Ordering::SeqCst);
            let now = now_ms();
            if pinned_at == 0 {
                HANDOFF_PINNED_AT_MS.store(now.max(1), Ordering::SeqCst);
                return;
            }
            if now.saturating_sub(pinned_at) < HANDOFF_SETTLE_BEFORE_PRESS_MS {
                return;
            }
            let hold = hold_pad();
            if hold == 0 {
                HANDOFF_STAGE.store(HANDOFF_IDLE, Ordering::SeqCst);
                return;
            }
            // SAFETY: the export resolved above, called with the button mask it documents.
            unsafe { core::mem::transmute::<usize, HoldPadFn>(hold)(PAD_A, 0, 0) };
            HANDOFF_PRESSED_AT_MS.store(now_ms().max(1), Ordering::SeqCst);
            HANDOFF_STAGE.store(HANDOFF_PRESSING, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "lynchpin: the handed-off Lynchpin settled for \
                 {HANDOFF_SETTLE_BEFORE_PRESS_MS}ms, so the use action is pressed and held for \
                 {HANDOFF_PRESS_HELD_MS}ms. Pin at this moment: {} tick(s) left on item {:#x} -- \
                 zero there would mean the quick-slot answer had already stopped and the press \
                 used whatever the real slot holds.",
                PIN_FRAMES_LEFT.load(Ordering::SeqCst),
                PINNED_ITEM_ID.load(Ordering::SeqCst)
            ));
        }
        HANDOFF_PRESSING => {
            let pressed_at = HANDOFF_PRESSED_AT_MS.load(Ordering::SeqCst);
            if now_ms().saturating_sub(pressed_at) < HANDOFF_PRESS_HELD_MS {
                return;
            }
            let hold = hold_pad();
            if hold != 0 {
                // SAFETY: as above; all zeroes releases.
                unsafe { core::mem::transmute::<usize, HoldPadFn>(hold)(0, 0, 0) };
            }
            HANDOFF_STAGE.store(HANDOFF_IDLE, Ordering::SeqCst);
        }
        _ => {}
    }
}

/// Ask for an item to be used from a thread the game does not own.
///
/// Stores the id only. The next [`tick`] resolves it against the inventory and arms the pin, so
/// every read of the game's own lists still happens on the game task.
#[cfg(windows)]
pub fn request_use_item_offthread(item_id: u32) {
    REQUESTED_ITEM_ID.store(item_id as usize, Ordering::SeqCst);
}

/// Drain an off-thread request, on the game task.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn drain_requested_use() {
    // A use already in flight makes this wait, rather than dropping the request.
    //
    // The order matters and the old order was a bug of mine: it took the request out first and then
    // refused it, so the id was gone. The near+far handoff asks for the Challenger's Lynchpin from
    // inside the finger's own popup, while the finger's use is still running, and run
    // br-20260916-090746-199e logged exactly that -- "declined to use 0x407fde63 -- a use is
    // already in flight" -- after which nothing latched and 38 of 38 matchmaking slots stayed at
    // zero. Leaving the id in place costs one atomic load a tick and the next tick picks it up.
    if PIN_FRAMES_LEFT.load(Ordering::SeqCst) != 0 {
        return;
    }
    let wanted = REQUESTED_ITEM_ID.swap(0, Ordering::SeqCst);
    if wanted == 0 {
        return;
    }
    // SAFETY: game task thread.
    if !unsafe { request_use_item(wanted as u32) } {
        crate::standalone_log(format_args!(
            "lynchpin: asked to use {wanted:#x}, but it is not in the inventory"
        ));
    }
}

/// The inventory index of one item id, or `None` when it is not held.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn inventory_index(item_id: u32) -> Option<usize> {
    let lookup = er_game_base::mem::game_rva_named(
        er_game_base::rva::GET_ITEM_INVENTORY_IDX_RVA as u32,
        "GET_ITEM_INVENTORY_IDX_RVA",
    )
    .ok()?;
    let base = er_game_base::mem::game_module_base().ok()?;
    let game_data_man = er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::GAME_DATA_MAN_GLOBAL_RVA,
        "GAME_DATA_MAN_GLOBAL_RVA",
    );
    if game_data_man == 0 {
        return None;
    }
    // SAFETY: as above; `PlayerGameData` is the first pointer inside `GameDataMan`.
    let player_game_data = unsafe { er_game_base::mem::safe_read_usize(game_data_man + 0x8) }?;
    if player_game_data == 0 {
        return None;
    }
    let inventory =
        player_game_data + PLAYER_GAME_DATA_EQUIP_GAME_DATA_OFFSET + EQUIP_INVENTORY_DATA_OFFSET;
    type GetItemIdxFn = unsafe extern "system" fn(usize, *const u32) -> i32;
    // SAFETY: version-translated address, engine's own two-argument getter.
    let get_item_idx: GetItemIdxFn = unsafe { core::mem::transmute(lookup) };
    // SAFETY: as above; the id is this frame's stack. The tagged spelling is the one the
    // inventory keys on -- the bare goods id answers -1.
    let index = unsafe { get_item_idx(inventory, &raw const item_id) };
    (index >= 0).then_some(index as usize)
}

/// The local `PlayerIns`, or `None` before the world exists.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn main_player_chr_ins() -> Option<usize> {
    let base = er_game_base::mem::game_module_base().ok()?;
    // Null until the world is up. Resolved for the running build rather than read raw: every
    // `.data` global moved on 1.17, so a raw read succeeds and hands back whatever now occupies
    // the 1.16.2 slot -- and this pointer is written through.
    let world_chr_man = er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::WORLD_CHR_MAN_GLOBAL_RVA,
        "WORLD_CHR_MAN_GLOBAL_RVA",
    );
    if world_chr_man == 0 {
        return None;
    }
    // SAFETY: as above. `PlayerIns` begins with its `ChrIns`, so the two addresses are the same.
    let player = unsafe {
        er_game_base::mem::safe_read_usize(
            world_chr_man + er_game_base::rva::WORLD_CHR_MAN_PLAYER_INS_OFFSET,
        )
    }?;
    (player != 0).then_some(player)
}

/// Say the game is online for exactly as long as one of our finger uses is in flight.
///
/// # Why this is necessary, and why it is this narrow
///
/// `CanUseGoods` refuses an invasion finger before the animation, and reading it showed why: the
/// verdict is one large `AND`, and `IsInOnlineMode()` is in it twice -- read at decompiled line 104
/// into `local_21f`, copied into `local_188` at line 300, and both terms appear in the condition at
/// lines 865-877. A false there refuses the item whatever else passes. Seamless keeps that flag
/// clear on purpose, so under Seamless every vanilla multiplayer item is permanently greyed out,
/// which is exactly the behaviour these three rows are being taken back from.
///
/// Detouring `CanUseGoods` is not available: Arxan stubs it in the live process, where its entry
/// reads `e9 ee 15 96 ff` against the image's `44 89 4c 24 20` -- 17 of 60 verified entries are
/// stubbed the same way, so the prologue gate refusing it is the protection working.
///
/// So the flag is written, not the code, and only inside the window this module already owns:
/// raised on the first frame of a pinned finger use and put back on the last, about 90 frames
/// later. The Challenger's Lynchpin does not get it, because that path already works without it and
/// a widening that changes a working path is a regression waiting to happen. Outside those frames
/// Seamless sees the flag exactly as it left it.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn hold_online_mode(base: usize, raise: bool) {
    let game_man = er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::GAME_MAN_SINGLETON_RVA,
        "GAME_MAN_SINGLETON_RVA",
    );
    if game_man == 0 {
        return;
    }
    let flag = (game_man + GAME_MAN_IS_IN_ONLINE_MODE) as *mut u8;
    if raise {
        // Re-asserted every frame of the window rather than written once on the first.
        //
        // Writing it once was not enough, and the reason is measured rather than assumed: a 20ms
        // sampler reading the byte out of `/proc` never once caught it set, across two drives that
        // both logged the raise. Something puts it back faster than the 90-frame window suggests.
        // Re-asserting costs one store per frame and removes the question of who else writes it.
        //
        // The value to put back is recorded on the first frame only, so a later frame cannot
        // record the 1 this function itself wrote and turn the restore into a no-op.
        if ONLINE_MODE_RESTORE.load(Ordering::SeqCst) == 0 {
            // SAFETY: fault-closed read of a singleton the game keeps for its whole life.
            let Some(before) =
                (unsafe { er_game_base::mem::safe_read_u8(game_man + GAME_MAN_IS_IN_ONLINE_MODE) })
            else {
                return;
            };
            ONLINE_MODE_RESTORE.store(before as usize + 1, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "lynchpin: holding `GameMan+0x{GAME_MAN_IS_IN_ONLINE_MODE:x}` at 1 for this finger \
                 use; it read {before}. CanUseGoods refuses every vanilla multiplayer item while \
                 that byte is clear, and Seamless keeps it clear."
            ));
        }
        // SAFETY: the byte `IsInOnlineMode` returns, in the singleton resolved above.
        unsafe { core::ptr::write_volatile(flag, 1) };
        return;
    }
    let held = ONLINE_MODE_RESTORE.swap(0, Ordering::SeqCst);
    if held == 0 {
        return;
    }
    // SAFETY: as above; restores the value read when the window opened.
    unsafe { core::ptr::write_volatile(flag, (held - 1) as u8) };
    crate::standalone_log(format_args!(
        "lynchpin: put `GameMan+0x{GAME_MAN_IS_IN_ONLINE_MODE:x}` back to {}",
        held - 1
    ));
}

/// Hold the use-state override for one frame, if a use was asked for.
///
/// # Safety
///
/// Game task thread.
#[cfg(windows)]
unsafe fn drive_pinned_use() {
    let left = PIN_FRAMES_LEFT.load(Ordering::SeqCst);
    if left == 0 {
        return;
    }
    PIN_FRAMES_LEFT.store(left - 1, Ordering::SeqCst);
    let spent = PIN_FRAMES_SPENT.fetch_add(1, Ordering::SeqCst);
    let Ok(base) = er_game_base::mem::game_module_base() else {
        return;
    };
    // Resolved rather than read raw, for the reason in `main_player_chr_ins`: this pointer is
    // written through, and a stale global would put those writes on whatever moved into the slot.
    let menu_man = er_game_base::mem::read_global_ptr(
        base,
        er_game_base::rva::CS_MENU_MAN_GLOBAL_RVA,
        "CS_MENU_MAN_GLOBAL_RVA",
    );
    if menu_man == 0 {
        return;
    }
    // SAFETY: as above.
    let Some(menu_data) = (unsafe {
        er_game_base::mem::safe_read_usize(
            menu_man + er_game_base::rva::CS_MENU_MAN_MENU_DATA_OFFSET,
        )
    }) else {
        return;
    };
    if menu_data == 0 {
        return;
    }
    let state = menu_data + MENU_GAITEM_USE_STATE_OFFSET;
    let index = PINNED_ITEM_IDX.load(Ordering::SeqCst) as i32;
    // SAFETY: the four stores the engine's own `Request` makes, into the struct it makes them in.
    unsafe {
        core::ptr::write_volatile(
            (state + USE_ITEM_ID_OFFSET) as *mut u32,
            PINNED_ITEM_ID.load(Ordering::SeqCst) as u32,
        );
        core::ptr::write_volatile((state + USE_ITEM_IDX_OFFSET) as *mut i32, index);
        core::ptr::write_volatile((state + USE_ARG_OFFSET) as *mut i32, 0);
    }
    // SAFETY: game task thread; fault-closed, and `None` before there is a player.
    if let Some(player) = unsafe { main_player_chr_ins() } {
        // SAFETY: the repeat count TAE event 65 loops on; at zero the event does nothing at all.
        unsafe {
            core::ptr::write_volatile((player + CHR_INS_CONSUME_COUNT_OFFSET) as *mut u32, 1)
        };
    }
    if crate::vanilla_invasion_items::routes_the_range_popup(
        PINNED_ITEM_ID.load(Ordering::SeqCst) as u32
    ) {
        // SAFETY: game task thread; fails closed when the singleton is not up.
        unsafe { hold_online_mode(base, true) };
    }
    // The request is re-raised until the engine acknowledges it, rather than written once.
    //
    // `+0x8` is a three-value state -- 0 idle, 1 requested, 2 latched -- and the player's own
    // per-frame action update is what reads the 1, drives the `USE_ITEM` action and latches it to
    // 2. Writing the 1 on a single frame only works if this task runs before that update in the
    // same frame, and nothing here guarantees the order: on run br-20260916-065936-0bb0 all four
    // stores were read back out of `/proc` while `ChrIns+0x164` never left `0xffffffff`, which is
    // what a request that was overwritten before anybody read it looks like.
    //
    // So: keep asking while the state reads idle, and stop the moment it reads latched. That is an
    // acknowledgement from the engine rather than a frame budget, and it cannot re-press the action
    // once the use is under way, because 2 is not 0.
    // Kept alive while the character is actually using the item, rather than for a fixed 90 frames.
    //
    // 90 frames is 1.5s and the vanilla fingers' own use animation is 3.900s (TimeAct 50030), so
    // the window expired well before the consume event at the end of the clip -- and expiring is
    // not passive here, it writes `-1` back over the item id and stops answering the quick-slot
    // question, which cancels the use it just started. Measured on run br-20260916-072232-b1cf:
    // `ChrIns+0x160` took the id and the popup never opened.
    //
    // `tae_queued_use_item` reading our id is the character saying it is still busy with it, so
    // that is what extends the window, bounded by `PIN_FRAMES_MAX` so a stuck field cannot hold the
    // override open for the rest of the session.
    if spent < PIN_FRAMES_MAX {
        let pinned = PINNED_ITEM_ID.load(Ordering::SeqCst) as i32;
        // SAFETY: fault-closed; `None` before there is a player.
        let still_using = unsafe { main_player_chr_ins() }.is_some_and(|player| {
            // SAFETY: as above.
            let queued =
                unsafe { er_game_base::mem::safe_read_i32(player + CHR_INS_QUEUED_USE_ITEM) };
            queued == Some(pinned)
        });
        // A handoff in flight keeps its own pin alive, because the press comes 2500ms later and
        // `PIN_FRAMES` counts 90 ticks -- a fraction of a second here, not a second and a half.
        //
        // Without this the pin expires long before the settle ends, `drive_pinned_use` returns
        // early, the quick-slot answer stops being given, and the press then uses whatever the real
        // quick slot holds rather than the Lynchpin. Run br-20260916-092608-2780 shows both halves
        // in one breath -- "pinned item 0x407fde63 ... for 90 frame(s)" next to "settled for
        // 2500ms" -- and four attempts produced no lobby calls at all.
        let handoff_in_flight = HANDOFF_STAGE.load(Ordering::SeqCst) != HANDOFF_IDLE
            && pinned == LYNCHPIN_ITEM_ID as i32;
        if still_using || handoff_in_flight {
            PIN_FRAMES_LEFT.store(PIN_FRAMES, Ordering::SeqCst);
        }
    }
    // SAFETY: fault-closed read of the struct this function already writes.
    let observed = unsafe { er_game_base::mem::safe_read_u8(state + USE_STATE_OFFSET) };
    if observed == Some(USE_STATE_LATCHED) && USE_ACKNOWLEDGED.swap(1, Ordering::SeqCst) == 0 {
        crate::standalone_log(format_args!(
            "lynchpin: the engine latched the use request -- `menuGaitemUseState+0x8` read \
             {USE_STATE_LATCHED}, so the player's action update has taken it"
        ));
    }
    // Raised on the first frame of the window and never again: the action is an edge, not a level.
    //
    // Counting the action path settled this. Across one drive, with a control proving the hooks
    // fire (`EquipParamGoods::GetEntry` 435333 -> 1521325):
    //
    //   action update 0x1403daa90      93 ->  325
    //   use-state latch 0x140768c60     0 ->  232
    //   FUN_140407fd0(pad, 7, 1)        0 ->  232, and the id really is 7, `USE_ITEM`
    //
    // So the press was reaching the character 232 times, once a frame for about four seconds,
    // because re-asking whenever the state read idle re-pressed it every frame after the engine
    // reset it. `GetSelectedGoodsUseAnim` is called from `HksEnv` -- the behaviour script picks the
    // animation -- and a script watching for a press sees one rising edge and then a level that
    // never rises again. Holding the button down forever is not pressing it.
    if left == PIN_FRAMES {
        // SAFETY: the request itself, into the struct the engine's own `Request` writes.
        unsafe {
            core::ptr::write_volatile((state + USE_STATE_OFFSET) as *mut u8, USE_STATE_REQUESTED)
        };
    }
    if left == 1 {
        // SAFETY: game task thread; a no-op when the window was never opened.
        unsafe { hold_online_mode(base, false) };
        // SAFETY: hand the struct back the way the engine leaves it, so nothing later reads the
        // Lynchpin as the selected quick item.
        unsafe {
            core::ptr::write_volatile((state + USE_ITEM_ID_OFFSET) as *mut i32, -1);
            core::ptr::write_volatile((state + USE_ITEM_IDX_OFFSET) as *mut i32, -1);
            core::ptr::write_volatile((state + USE_STATE_OFFSET) as *mut u8, 0);
        }
    }
}

/// One frame of this module's work: shorten the animation, arm the skip, hold any pinned use.
///
/// Every part is idempotent and every part fails closed, so a tick before the world exists costs
/// nothing and is retried.
///
/// # Safety
///
/// Game task thread, after `CSTaskImp` resolved.
#[cfg(windows)]
pub unsafe fn tick() {
    // SAFETY: game task thread; each is fault-closed and idempotent.
    unsafe {
        shorten_use_animation();
        crate::vanilla_invasion_items::enable_offline_use();
        crate::vanilla_invasion_items::install_bounds_popup_takeover();
        crate::vanilla_invasion_items::install_can_use_goods_widening();
        install_popup_skip();
        install_selected_quick_slot();
        drain_requested_use();
        drive_pinned_use();
        drive_handoff_press();
    }
    // No `install_menu_object_observer()` call here, and this is not an omission.
    //
    // Detouring `ersc.dll` to capture the menu object is what the local-invasion filter already
    // gates off, and installing it from here reproduced that crash exactly, measured on run
    // `br-20260909-234159-0a54`: `observing ersc show @0x1800241a0` at boot, then at +29493ms a
    // read fault at `game+0x11f42` -- the `0x140010043` page MinHook's ersc patch faults on --
    // followed by 215
    // recursive access violations with `rsp` marching down 0x1260 a frame until the thread was
    // gone. On screen that is a loading screen that never finishes. The filter's comment says
    // "~50s and 30.6s"; this was 29.5s.
    //
    // So the menu object has to arrive without a detour inside Seamless. `resolve_session` already
    // reaches the session that way -- "via a pointer in ersc's own writable data at 0x1806088f0"
    // -- and `er_invasion_warp_adopt_menu_object` takes one from outside the DLL. Until one of
    // those supplies `OSM`, `menu_object_session_is_idle` answers false and every dialog is let
    // through, which shows the popup rather than swallowing the prompt that leaves an invasion.
}

/// How many popups this module declined and how many it let through.
#[cfg(windows)]
#[must_use]
pub fn popup_tally() -> (usize, usize) {
    (
        POPUPS_SKIPPED.load(Ordering::SeqCst),
        POPUPS_PASSED.load(Ordering::SeqCst),
    )
}

#[cfg(test)]
mod tests {
    /// The measured animation lengths, kept where a reader of the module can check the claim
    /// without a game: the value written must be the shortest row in the table the doc comment
    /// records.
    #[test]
    fn seventeen_is_the_shortest_measured_animation() {
        let measured = [(66_u8, 5.000_f32), (8, 3.900), (6, 3.167), (17, 1.433)];
        let shortest = measured
            .iter()
            .min_by(|a, b| a.1.partial_cmp(&b.1).expect("no NaN in a literal table"))
            .expect("the table is not empty");
        assert_eq!(shortest.0, 17);
    }
}
