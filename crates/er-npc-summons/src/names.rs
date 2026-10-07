//! Names for the duel NPC and the Mimic Tear companions.
//!
//! One detour, on `GetChrName(MenuString* out, ChrIns*, bool decorate)`: the original runs, then a
//! character this DLL named gets `out->rawString` pointed at its name. Every reader of a
//! `MenuString` takes `rawString` when it is not null (`MenuString::Replace`, 1.16.2
//! `0x140763490`), and the `DLString` half is left as the original built it, for its destructor.
//!
//! Names are keyed by `ChrIns*` and checked against the handle at `ChrIns+0x8` on every call, so a
//! character freed and replaced at the same address does not inherit the name. The UTF-16 buffers
//! are leaked on purpose: a reader may hold `rawString` past this call, so a buffer must outlive
//! every `MenuString` that points at it. One buffer per distinct name, so the leak is bounded by
//! the config, not by the number of summons.
//!
//! Who calls `GetChrName` (1.16.2): `UpdateEnemyTags`, the summon and red-hunter network messages,
//! `SendHome` and `SendInvadingPhantomsHome`. Overhead plates for spirit summons are built by code
//! that would need mid-function hooks, which `er-hook` refuses, so companions get no plate.

#![cfg(windows)]

use std::collections::HashMap;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_game_base::mem::safe_read_usize;

use crate::addr::{self, chr_ins};
use crate::log::summons_log;

/// A named character: the handle it had when it was named, and the leaked name.
#[derive(Clone, Copy)]
struct Named {
    handle: usize,
    text: &'static [u16],
}

/// `ChrIns*` -> its name.
static NAMED: Mutex<Option<HashMap<usize, Named>>> = Mutex::new(None);
/// One leaked buffer per distinct name.
static INTERNED: Mutex<Option<HashMap<String, &'static [u16]>>> = Mutex::new(None);
/// The companion names by config slot (1..=4), from the last config read.
static COMPANIONS: Mutex<Vec<(u8, String)>> = Mutex::new(Vec::new());
/// Nothing is named: the detour returns after the original without taking a lock.
static ANY: AtomicBool = AtomicBool::new(false);
/// Calls that rewrote a name, for the log.
static REWRITES: AtomicUsize = AtomicUsize::new(0);

static ORIG: AtomicUsize = AtomicUsize::new(0);

type GetChrNameFn = unsafe extern "system" fn(usize, usize, u8) -> usize;

fn intern(name: &str) -> &'static [u16] {
    let mut table = INTERNED.lock().unwrap_or_else(|e| e.into_inner());
    let table = table.get_or_insert_with(HashMap::new);
    if let Some(text) = table.get(name) {
        return text;
    }
    let mut wide: Vec<u16> = name.encode_utf16().collect();
    wide.push(0);
    let text: &'static [u16] = Box::leak(wide.into_boxed_slice());
    table.insert(name.to_owned(), text);
    text
}

fn handle_of(chr: usize) -> Option<usize> {
    // SAFETY: fault-tolerant read.
    unsafe { safe_read_usize(chr + chr_ins::HANDLE) }
}

/// Name `chr` until it is gone. An empty name clears it.
pub(crate) fn set(chr: usize, name: &str) {
    if name.is_empty() {
        clear(chr);
        return;
    }
    let Some(handle) = handle_of(chr) else {
        return;
    };
    let text = intern(name);
    let mut named = NAMED.lock().unwrap_or_else(|e| e.into_inner());
    named
        .get_or_insert_with(HashMap::new)
        .insert(chr, Named { handle, text });
    ANY.store(true, Ordering::Release);
    summons_log(format_args!("names: 0x{chr:x} is now \"{name}\""));
}

/// Forget `chr`'s name.
pub(crate) fn clear(chr: usize) {
    let mut named = NAMED.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(map) = named.as_mut() {
        map.remove(&chr);
        ANY.store(!map.is_empty(), Ordering::Release);
    }
}

/// The companion names, from a config read.
pub(crate) fn set_companions(names: Vec<(u8, String)>) {
    *COMPANIONS.lock().unwrap_or_else(|e| e.into_inner()) = names;
}

/// Name a companion just built for config slot `slot`.
pub(crate) fn name_companion(slot: u8, chr: usize) {
    let name = COMPANIONS
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .find(|(s, _)| *s == slot)
        .map(|(_, name)| name.clone());
    if let Some(name) = name {
        set(chr, &name);
    }
}

/// Drop every entry whose character no longer holds the handle it was named with. Called from
/// the game task, so a stale entry costs a lookup for at most one frame.
pub(crate) fn prune() {
    if !ANY.load(Ordering::Acquire) {
        return;
    }
    let mut named = NAMED.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(map) = named.as_mut() {
        map.retain(|&chr, entry| handle_of(chr) == Some(entry.handle));
        ANY.store(!map.is_empty(), Ordering::Release);
    }
}

fn name_for(chr: usize) -> Option<&'static [u16]> {
    let named = NAMED.lock().ok()?;
    let entry = *named.as_ref()?.get(&chr)?;
    (handle_of(chr) == Some(entry.handle)).then_some(entry.text)
}

unsafe extern "system" fn get_chr_name_detour(out: usize, chr: usize, decorate: u8) -> usize {
    let original = ORIG.load(Ordering::Acquire);
    if original == 0 {
        return out;
    }
    // SAFETY: the trampoline of the function this replaces.
    let original: GetChrNameFn = unsafe { core::mem::transmute(original) };
    let result = unsafe { original(out, chr, decorate) };
    if out == 0 || chr == 0 || !ANY.load(Ordering::Acquire) {
        return result;
    }
    if let Some(text) = name_for(chr) {
        // SAFETY: `out` is the `MenuString` the original just filled; `rawString` is its first
        // field, and `text` is leaked, so it outlives the string.
        unsafe {
            core::ptr::write_volatile(
                (out + addr::MENU_STRING_RAW) as *mut *const u16,
                text.as_ptr(),
            );
        }
        if REWRITES.fetch_add(1, Ordering::AcqRel) == 0 {
            summons_log(format_args!(
                "names: first GetChrName rewrite, for 0x{chr:x}"
            ));
        }
    }
    result
}

/// Install the detour. Called once, from the install thread.
pub(crate) fn install() {
    match crate::mimic_hooks::hook(
        addr::GET_CHR_NAME,
        get_chr_name_detour as *const () as usize,
        &ORIG,
        "GetChrName",
    ) {
        // SAFETY: applies the queued enable.
        Ok(_hook) => match unsafe { er_hook::MH_ApplyQueued() } {
            er_hook::MH_STATUS::MH_OK => summons_log(format_args!(
                "names: GetChrName detoured; the duel NPC and companions are named in the HUD and \
                 network messages that read it. Overhead plates for companions are not drawn: \
                 they need mid-function hooks"
            )),
            status => summons_log(format_args!("names: MH_ApplyQueued failed: {status:?}")),
        },
        Err(why) => summons_log(format_args!("names: off -- {why}")),
    }
}
