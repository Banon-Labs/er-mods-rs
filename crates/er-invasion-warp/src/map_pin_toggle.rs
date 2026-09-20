//! The in-game switch for the invasion pins, and the removal of the native pins it used to work.
//!
//! # What this replaces
//!
//! The pins used to be switched by `map_pins` in the TOML beside the DLL. A text file is a poor
//! switch for a thing the player looks at: changing it means leaving the game, and the value the
//! file holds is not the value on screen until the config is re-read. The switch now lives where
//! the pins do -- the world map's own Map Functions menu, whose `Multiplayer Status Display` row
//! this module reads. Flipping that row shows and hides our pins immediately, in memory, with no
//! file involved.
//!
//! The row's own label is the game's, not ours, and it is the only thing taken from the feature it
//! names. Nothing here reads multiplayer state, and nothing here writes it.
//!
//! # Why that row and not a key of our own
//!
//! Because it is the row whose pins we are replacing. It used to draw
//! `CS::WorldMapPlayRegionData` markers -- one per play region the matchmaking server reported
//! activity in -- and [`crate::map_seams::WORLDMAP_NATIVE_MARKER_BUILD`] is detoured to draw none
//! of them. That leaves the row with exactly one thing to control, which is what makes it an
//! honest switch rather than a borrowed one: a player who turns it on gets pins, and the pins they
//! get are ours.
//!
//! # The flag, measured rather than remembered
//!
//! `WorldMapViewModel + 0x3c5`, one byte, default zero. Its writer `FUN_140887580` opens
//! `mov [rcx+0x3c5], dl` followed by `mov [rax+0xff1], bl` -- the second store is the copy inside
//! `CSMenuProfileSaveLoad` (`0xfc8 + 0x29`) that makes the row remember its position across
//! sessions, which is why this module never writes the byte: the row owns it, and a write here
//! would desynchronise the screen from what the game has saved.
//!
//! Read live on 1.17.1 through Frida in run `br-20260919-063332-c93b`: ViewModel `0x212de180`,
//! `+0x3c5` reading `0` with the map shut and no input driven, reached through
//! `CSMenuMan -> CSPopupMenu+0x80 -> +0x250`.
//!
//! # How a flip reaches the screen
//!
//! By the row's layer mask, not by re-injecting. The pins are appended inside the
//! `WorldMapViewModel` constructor and that constructor runs at world load and nowhere else, so
//! there is no re-injection available to gate -- closing and reopening the map does not rebuild
//! the list. `WorldMapPinData::UpdateVisible` clears a row's draw flag unless the active layer's
//! bit is set in `row+0x60`, so zero there is a row invisible on every layer and the original
//! bits restore it.
//!
//! The bits to restore come from the row's own synthetic param row (`param+0x1E`), which this DLL
//! leaked and never frees, rather than from a table kept here. A pin carries a single coordinate
//! that is only meaningful in one converter's space, so showing every row on every layer would
//! draw pins at meaningless places; the param row already holds the one mask that is right for
//! that pin, and reading it back cannot drift from what the injection decided.

#[cfg(windows)]
use core::sync::atomic::{AtomicUsize, Ordering};

/// `WorldMapViewModel + 0x3c5` -- the Map Functions row that now switches our pins.
///
/// One byte. Zero is off, which is the value the game ships and the value a fresh profile holds.
pub const VIEW_MODEL_PIN_TOGGLE_OFFSET: usize = 0x3c5;

/// The row mask that hides a pin on every map layer.
///
/// Not a mask of ours: `UpdateVisible` tests `(row+0x60 >> layerBit) & 1`, so zero fails that test
/// for all three layers by construction.
pub const HIDDEN_LAYER_MASK: u32 = 0;

/// Last value applied to the rows, so a steady flag costs one atomic load per tick.
///
/// Three states rather than two, because "not applied yet" has to be distinguishable from "applied
/// off": a fresh injection leaves rows carrying their own bits, which looks the same as on, so a
/// two-state latch initialised to off would believe the map already matched a flag that says
/// hide and leave the pins showing.
#[cfg(windows)]
static APPLIED: AtomicUsize = AtomicUsize::new(APPLIED_UNKNOWN);

/// Nothing has been applied to the live rows yet.
#[cfg(windows)]
const APPLIED_UNKNOWN: usize = 2;

/// The ViewModel the last apply was made against. An apply is redone when the map is rebuilt,
/// because a new ViewModel's rows carry their injected masks again.
#[cfg(windows)]
static APPLIED_VIEW_MODEL: AtomicUsize = AtomicUsize::new(0);

/// Whether the in-game row currently asks for our pins.
///
/// `None` means the question cannot be answered right now -- there is no `WorldMapViewModel`,
/// which is the ordinary state outside a world. A caller must not read that as "off": nothing is
/// on screen to hide either.
#[cfg(windows)]
#[must_use]
pub fn pins_enabled() -> Option<bool> {
    let view_model = crate::map_hooks::authoritative_view_model()?;
    let value =
        unsafe { er_game_base::mem::safe_read_u8(view_model + VIEW_MODEL_PIN_TOGGLE_OFFSET) }?;
    Some(value != 0)
}

#[cfg(not(windows))]
#[must_use]
pub fn pins_enabled() -> Option<bool> {
    None
}

/// Read the row each tick and, when it has moved, repaint the live rows' layer masks to match.
///
/// # Safety
///
/// Game task thread. Every read is fault-closed and the writes are gated exactly as
/// [`crate::map_live_pins::restyle_live_pins`] gates its own -- see that module's ownership rule.
#[cfg(windows)]
pub unsafe fn tick() {
    let Some(enabled) = pins_enabled() else {
        // No world map exists, so there is nothing to show or hide. Forget what was applied: the
        // next ViewModel builds rows carrying their injected masks, and an apply latched against
        // the object that has gone would skip restoring them.
        APPLIED.store(APPLIED_UNKNOWN, Ordering::SeqCst);
        APPLIED_VIEW_MODEL.store(0, Ordering::SeqCst);
        return;
    };
    let Some(view_model) = crate::map_hooks::authoritative_view_model() else {
        return;
    };
    let wanted = usize::from(enabled);
    if APPLIED.load(Ordering::SeqCst) == wanted
        && APPLIED_VIEW_MODEL.load(Ordering::SeqCst) == view_model
    {
        return;
    }
    let applied = unsafe { crate::map_live_pins::set_pin_rows_visible(enabled) };
    match applied {
        Ok(rows) => {
            APPLIED.store(wanted, Ordering::SeqCst);
            APPLIED_VIEW_MODEL.store(view_model, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "map-pin-toggle: the map's own Multiplayer Status Display row is now {}; \
                 {rows} invasion pin row(s) repainted to match",
                if enabled { "on" } else { "off" }
            ));
        }
        Err(reason) => {
            // Deliberately not latched: the rows were not repainted, so the next tick must try
            // again rather than believe the map already agrees with the row.
            crate::standalone_log(format_args!(
                "map-pin-toggle: the row reads {} but the live rows were not repainted -- {reason}",
                if enabled { "on" } else { "off" }
            ));
        }
    }
}

#[cfg(not(windows))]
pub unsafe fn tick() {}

/// Whether the native-marker suppressor is installed.
#[cfg(windows)]
static SUPPRESSOR_INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// The trampoline slot the union hook fills in. Never entered -- see below.
#[cfg(windows)]
pub(crate) static ORIG_NATIVE_MARKER_BUILD: AtomicUsize = AtomicUsize::new(0);

/// Draw none of the game's own map markers.
///
/// Returning without entering the trampoline is the entire suppression. The marker list is
/// populated in this function and nowhere else, so a call that does not run leaves it as the menu
/// built it -- empty -- and leaves its sprite pool at zero. That is why this handler writes
/// nothing and frees nothing: there is no state to undo, only work not done.
///
/// It also sidesteps the hazard on this prologue. `FUN_1409c6730` opens `mov rax, rsp` and every
/// later frame reference derives from that anchor, so entering its trampoline with a pushed return
/// address shifts the frame; a handler that never enters cannot be wrong about it.
#[cfg(windows)]
unsafe extern "system" fn native_marker_build_hook(
    _map_menu: usize,
    _unused1: usize,
    _unused2: usize,
    _unused3: usize,
) -> usize {
    NATIVE_MARKER_BUILDS_SUPPRESSED.fetch_add(1, Ordering::SeqCst);
    0
}

/// How many native marker builds were refused, for the telemetry line.
#[cfg(windows)]
static NATIVE_MARKER_BUILDS_SUPPRESSED: AtomicUsize = AtomicUsize::new(0);

/// How many native marker builds this session declined to run.
#[cfg(windows)]
#[must_use]
pub fn native_marker_builds_suppressed() -> usize {
    NATIVE_MARKER_BUILDS_SUPPRESSED.load(Ordering::SeqCst)
}

#[cfg(not(windows))]
#[must_use]
pub fn native_marker_builds_suppressed() -> usize {
    0
}

/// Install the suppressor. Returns how many hooks bound.
///
/// Idempotent, and independent of every other map hook: losing it costs the removal of the native
/// markers and nothing else, so the pins and the row still work with the vanilla markers drawn
/// alongside them.
///
/// # Safety
///
/// Game task thread, after the runtime is up. MinHook must not run under the loader lock.
#[cfg(windows)]
pub unsafe fn install_native_marker_suppressor() -> usize {
    if SUPPRESSOR_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return 0;
    }
    let seam = crate::map_seams::WORLDMAP_NATIVE_MARKER_BUILD;
    let address = match unsafe { crate::map_seams::verify_seam(&seam) } {
        Ok(address) => address,
        Err(error) => {
            crate::standalone_log(format_args!(
                "map-pin-toggle: REFUSED {} -- {error}; the game's own map markers stay, so the \
                 Map Functions row will show them alongside our pins instead of ours alone",
                seam.name
            ));
            return 0;
        }
    };
    match unsafe {
        er_hook::register_union_hook(
            address,
            native_marker_build_hook as er_hook::UnionFn,
            &ORIG_NATIVE_MARKER_BUILD,
        )
    } {
        Ok(()) => {
            crate::standalone_log(format_args!(
                "map-pin-toggle: ARMED {} @0x{address:x} -- the game's own map markers are no \
                 longer built, so the Map Functions row draws our invasion pins and nothing else",
                seam.name
            ));
            1
        }
        Err(status) => {
            crate::standalone_log(format_args!(
                "map-pin-toggle: FAILED {} @0x{address:x} -- the address resolved and its \
                 prologue matched, but union registration returned {status:?}. The pins and the \
                 row are unaffected; the vanilla markers are simply still drawn",
                seam.name
            ));
            0
        }
    }
}

#[cfg(not(windows))]
pub unsafe fn install_native_marker_suppressor() -> usize {
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_toggle_offset_is_the_one_the_setter_writes() {
        // `FUN_140887580` opens `mov [rcx+0x3c5], dl`, and the seam's recorded prologue carries
        // those bytes. Reading the offset back out of the signature is what keeps the constant
        // and the address that proves it from drifting apart.
        let prologue = crate::map_seams::WORLDMAP_PIN_TOGGLE_SETTER.prologue;
        let store = prologue
            .windows(3)
            .position(|window| window == [0x88, 0x91, 0xc5])
            .expect("the setter signature must contain `mov [rcx+disp32], dl`");
        // The displacement's low byte follows the opcode and modrm; the signature is cut short of
        // the remaining three, which are zero in `0x3c5`.
        assert_eq!(prologue[store + 2], 0xc5);
        assert_eq!(VIEW_MODEL_PIN_TOGGLE_OFFSET & 0xff, 0xc5);
        assert_eq!(VIEW_MODEL_PIN_TOGGLE_OFFSET, 0x3c5);
    }

    #[test]
    fn hiding_is_the_absence_of_every_layer_bit() {
        // `UpdateVisible` tests one bit per layer, so the hide value cannot be a sentinel: it has
        // to be a mask that fails all three tests.
        for layer_bit in 0..3 {
            assert_eq!((HIDDEN_LAYER_MASK >> layer_bit) & 1, 0);
        }
    }
}
