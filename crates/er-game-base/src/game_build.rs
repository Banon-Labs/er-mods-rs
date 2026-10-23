//! Which ELDEN RING this DLL is loaded into, read from the running image's own version resource,
//! and the gate every game address passes through before it is called, read or detoured.
//!
//! # Why this exists
//!
//! Every game address in this workspace is a 2.7.2.0 rva, measured on the de-Arxan'd image
//! `eldenring-deobf-1.17.2.bin`. An address only means something on the build it was measured on:
//! when 1.17 shipped on 2026-08-27 a detour installed at an address from the previous patch landed
//! in the middle of a different function (`0x1407ada40` was `push rbp; push rsi; push rdi` on the
//! old build, 1.16.2, and `xor r15d, r15d` on the new one, 1.17.0), and the game died 3.5 seconds into boot with a
//! fabricated pointer in `rcx` and a backtrace that pointed into game code. A call through a stale
//! address is worse still: it transfers control into whatever now occupies those bytes, with no
//! unwind information and no exception record naming anything of ours.
//!
//! So nothing here translates. On 2.7.2.0 an address is used as written; on any other build, or
//! when the version resource cannot be read, every game-image address is refused, once, loudly.
//! The next patch is a re-measurement of the constants, not a table that carries them.
//!
//! # What it does not do
//!
//! It does not gate `er-hook`'s `write_code_byte`, which takes an absolute address its caller
//! discovered by scanning; a scanned address is already the running build's, so there is nothing
//! for a build check to protect.

// Reading the running image's own PE headers is a Windows-only operation, and these externs are
// undefined at link time on the host -- which host-unit-tested crates hit through `describe_build`.
#[cfg(windows)]
use crate::mem::{game_module_base, read_bytes};

/// `FileVersion` of the one build every rva in this workspace is measured against: the ELDEN RING
/// Steam installed on 2026-10-20, PE `FileVersion` 2.7.2.0 (its de-Arxan'd image is
/// `eldenring-deobf-1.17.2.bin`).
pub const SUPPORTED_FILE_VERSION: FileVersion = FileVersion {
    major: 2,
    minor: 7,
    build: 2,
    revision: 0,
};

/// A PE `VS_FIXEDFILEINFO` file version, in display order.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct FileVersion {
    pub major: u16,
    pub minor: u16,
    pub build: u16,
    pub revision: u16,
}

impl core::fmt::Display for FileVersion {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(
            f,
            "{}.{}.{}.{}",
            self.major, self.minor, self.build, self.revision
        )
    }
}

/// Signature at the head of `VS_FIXEDFILEINFO`.
#[cfg(windows)]
const VS_FIXEDFILEINFO_SIGNATURE: u32 = 0xFEEF_04BD;

/// `IMAGE_DIRECTORY_ENTRY_RESOURCE`.
#[cfg(windows)]
const RESOURCE_DIRECTORY_INDEX: usize = 2;

/// `RT_VERSION`.
#[cfg(windows)]
const RT_VERSION: u32 = 16;

/// Bytes of `.rsrc` searched for the fixed-file-info signature once the resource directory has
/// been located. The version resource sits at the front of it; this bound keeps a corrupt or
/// unexpected directory from turning into a long scan.
#[cfg(windows)]
const VERSION_SEARCH_LIMIT: usize = 0x4000;
/// High bit of a resource directory entry's `OffsetToData`: set means the entry points at another
/// directory rather than at a data leaf.
#[cfg(windows)]
const RESOURCE_SUBDIRECTORY_FLAG: u32 = 0x8000_0000;

/// High bit of a resource directory entry's `Name`: set means the entry is keyed by a string,
/// not by an integer id. A different field from the one above, and conflating the two is why this
/// is spelled out.
#[cfg(windows)]
const RESOURCE_NAME_IS_STRING_FLAG: u32 = 0x8000_0000;

/// The running game image's `FileVersion`, or `None` when the headers or the version resource
/// cannot be read (which is itself a reason to treat the build as unrecognised).
///
/// A host build has no running game image, so it answers `None` -- the same answer, for the same
/// reason, as a Windows build whose headers are unreadable. It is a separate function body rather
/// than an early return because the Win32 externs below are undefined at link time on the host,
/// and crates that are host-unit-tested (`er-save-loader`) reach this through `describe_build`.
#[cfg(not(windows))]
pub fn game_file_version() -> Option<FileVersion> {
    None
}

/// The running game image's `FileVersion`; see the host counterpart above.
#[cfg(windows)]
pub fn game_file_version() -> Option<FileVersion> {
    let base = game_module_base().ok()?;
    let (rsrc_rva, rsrc_size) = resource_directory(base)?;
    let version_rva = version_resource_data(base, rsrc_rva)?;
    // Search from the version resource's own data, bounded by the section it lives in.
    let span = rsrc_size
        .saturating_sub(version_rva.saturating_sub(rsrc_rva))
        .min(VERSION_SEARCH_LIMIT);
    let mut buf = vec![0u8; span];
    if !unsafe { read_bytes(base + version_rva, &mut buf) } {
        return None;
    }
    let signature = VS_FIXEDFILEINFO_SIGNATURE.to_le_bytes();
    let at = buf.windows(signature.len()).position(|w| w == signature)?;
    // VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, dwFileVersionMS, dwFileVersionLS.
    let ms = u32::from_le_bytes(buf.get(at + 8..at + 12)?.try_into().ok()?);
    let ls = u32::from_le_bytes(buf.get(at + 12..at + 16)?.try_into().ok()?);
    Some(FileVersion {
        major: (ms >> 16) as u16,
        minor: (ms & 0xFFFF) as u16,
        build: (ls >> 16) as u16,
        revision: (ls & 0xFFFF) as u16,
    })
}

/// `(rva, size)` of the resource data directory.
#[cfg(windows)]
fn resource_directory(base: usize) -> Option<(usize, usize)> {
    let mut word = [0u8; 4];
    if !unsafe { read_bytes(base + 0x3C, &mut word) } {
        return None;
    }
    let pe = base + u32::from_le_bytes(word) as usize;
    let mut signature = [0u8; 4];
    if !unsafe { read_bytes(pe, &mut signature) } || &signature != b"PE\0\0" {
        return None;
    }
    let mut magic = [0u8; 2];
    if !unsafe { read_bytes(pe + 0x18, &mut magic) } {
        return None;
    }
    // Data directories start at +0x70 in PE32+ and +0x60 in PE32; ELDEN RING is PE32+, but the
    // offset is derived rather than assumed so a wrong guess cannot silently read garbage.
    let directories = if u16::from_le_bytes(magic) == 0x20B {
        pe + 0x18 + 0x70
    } else {
        pe + 0x18 + 0x60
    };
    let mut entry = [0u8; 8];
    if !unsafe { read_bytes(directories + RESOURCE_DIRECTORY_INDEX * 8, &mut entry) } {
        return None;
    }
    let rva = u32::from_le_bytes(entry[0..4].try_into().ok()?) as usize;
    let size = u32::from_le_bytes(entry[4..8].try_into().ok()?) as usize;
    if rva == 0 || size == 0 {
        return None;
    }
    Some((rva, size))
}

/// RVA of the first data block under the `RT_VERSION` type, walking type -> name -> language.
#[cfg(windows)]
fn version_resource_data(base: usize, rsrc_rva: usize) -> Option<usize> {
    let type_dir = resource_entry(base, rsrc_rva, rsrc_rva, Some(RT_VERSION))?;
    let name_dir = resource_entry(base, rsrc_rva, type_dir, None)?;
    let language_entry = resource_entry(base, rsrc_rva, name_dir, None)?;
    // A leaf entry points at IMAGE_RESOURCE_DATA_ENTRY: OffsetToData, Size, CodePage, Reserved.
    let mut leaf = [0u8; 4];
    if !unsafe { read_bytes(base + language_entry, &mut leaf) } {
        return None;
    }
    Some(u32::from_le_bytes(leaf) as usize)
}

/// One step of the resource tree walk. Returns the RVA the matching entry points at -- a
/// subdirectory RVA for the first two levels, a data-entry RVA for the last.
///
/// `want_id` selects an entry by id; `None` takes the first, which is what the name and language
/// levels want (the version resource has exactly one of each).
#[cfg(windows)]
fn resource_entry(
    base: usize,
    rsrc_rva: usize,
    directory_rva: usize,
    want_id: Option<u32>,
) -> Option<usize> {
    // IMAGE_RESOURCE_DIRECTORY: ...+0x0c NumberOfNamedEntries, +0x0e NumberOfIdEntries.
    let mut header = [0u8; 16];
    if !unsafe { read_bytes(base + directory_rva, &mut header) } {
        return None;
    }
    let named = u16::from_le_bytes(header[12..14].try_into().ok()?) as usize;
    let ids = u16::from_le_bytes(header[14..16].try_into().ok()?) as usize;
    for index in 0..named + ids {
        let mut entry = [0u8; 8];
        if !unsafe { read_bytes(base + directory_rva + 16 + index * 8, &mut entry) } {
            return None;
        }
        let name = u32::from_le_bytes(entry[0..4].try_into().ok()?);
        let offset = u32::from_le_bytes(entry[4..8].try_into().ok()?);
        if let Some(want) = want_id
            && (name & RESOURCE_NAME_IS_STRING_FLAG != 0 || name != want)
        {
            continue;
        }
        return Some(rsrc_rva + (offset & !RESOURCE_SUBDIRECTORY_FLAG) as usize);
    }
    None
}

/// `[base, base + SizeOfImage)` of the running game module, or `None` if the headers are
/// unreadable.
///
/// This is what separates "an address measured on one build" from "an address that has nothing
/// to do with the game build". A detour on `user32!CreateWindowExW` or `kernel32!ExitProcess` is
/// resolved through `GetProcAddress` at runtime and is equally correct on every ELDEN RING
/// version; only addresses inside this range carry a version assumption.
#[cfg(windows)]
pub fn game_image_range() -> Option<(usize, usize)> {
    let base = game_module_base().ok()?;
    let mut word = [0u8; 4];
    if !unsafe { read_bytes(base + 0x3C, &mut word) } {
        return None;
    }
    let pe = base + u32::from_le_bytes(word) as usize;
    let mut signature = [0u8; 4];
    if !unsafe { read_bytes(pe, &mut signature) } || &signature != b"PE\0\0" {
        return None;
    }
    // Optional header +0x38 is SizeOfImage in both PE32 and PE32+.
    let mut size = [0u8; 4];
    if !unsafe { read_bytes(pe + 0x18 + 0x38, &mut size) } {
        return None;
    }
    let size = u32::from_le_bytes(size) as usize;
    if size == 0 {
        return None;
    }
    Some((base, base + size))
}

/// Whether `address` lies inside the running game image, and therefore whether it is an address
/// whose meaning depends on which patch is installed.
///
/// A `None` image range answers `true`: if the headers cannot be read, treating an address as
/// version-sensitive is the side that refuses rather than the side that detours blind.
#[cfg(windows)]
pub fn is_game_image_address(address: usize) -> bool {
    match game_image_range() {
        Some((start, end)) => (start..end).contains(&address),
        None => true,
    }
}

/// Whether the running image is the build this workspace's rvas were measured against.
///
/// An unreadable version resource counts as unsupported: "cannot tell" and "wrong build" have
/// the same consequence for an address measured on 2.7.2.0.
pub fn is_supported_build() -> bool {
    game_file_version() == Some(SUPPORTED_FILE_VERSION)
}

/// One line naming the running build and the supported one, for a refusal log.
pub fn describe_build() -> String {
    match game_file_version() {
        Some(found) => {
            format!("game FileVersion {found} (this build supports {SUPPORTED_FILE_VERSION})")
        }
        None => {
            format!("game FileVersion UNREADABLE (this build supports {SUPPORTED_FILE_VERSION})")
        }
    }
}

// ============================================================================
// the address gate.
//
// It lives here rather than in `er-hook` because the danger is not specific to detours. A stale
// address is reachable as a call (`transmute(base + RVA)`) and as a data pointer as much as a
// hook target, and neither of the first two goes anywhere near MinHook.
// ============================================================================

use crate::detour_allowlist::DETOUR_ALLOWLIST;

/// Signature of the sink refusal lines are written to.
pub type AddressLogFn = fn(core::fmt::Arguments<'_>);
static ADDRESS_LOGGER: core::sync::atomic::AtomicUsize = core::sync::atomic::AtomicUsize::new(0);

use core::sync::atomic::{AtomicU64, Ordering};

/// Install the sink for address refusal lines. Call once, early, before any resolution.
///
/// Default is a no-op: this crate is a zero-dependency leaf and has no log of its own, so the
/// product DLL points this at the same file its hook refusals already go to.
pub fn set_address_logger(logger: AddressLogFn) {
    ADDRESS_LOGGER.store(logger as usize, Ordering::Release);
}

/// Windows-only: the resolvers are the sole callers, and on a host build there is no running game
/// to refuse an address for, so the sink would never be reached.
#[cfg(windows)]
fn address_log(args: core::fmt::Arguments<'_>) {
    let raw = ADDRESS_LOGGER.load(Ordering::Acquire);
    if raw != 0 {
        // SAFETY: `raw` is only ever an `AddressLogFn` stored by `set_address_logger`.
        let logger: AddressLogFn = unsafe { core::mem::transmute::<usize, AddressLogFn>(raw) };
        logger(args);
    }
}

/// Set once an unsupported build has been reported, so that line is written once rather than once
/// per address on a per-frame path.
#[cfg(windows)]
static UNSUPPORTED_BUILD_ANNOUNCED: core::sync::atomic::AtomicBool =
    core::sync::atomic::AtomicBool::new(false);

/// `true` on 2.7.2.0. Otherwise `false`, saying so in the log the first time.
///
/// Deliberately not an `ADDRESS REFUSED` line. That shape is per address and carries the address;
/// this refusal is not about an address -- every game address is refused -- so a line in that shape
/// would misreport the cause as one missing constant.
#[cfg(windows)]
fn supported_or_announce(what: core::fmt::Arguments<'_>) -> bool {
    if is_supported_build() {
        return true;
    }
    if !UNSUPPORTED_BUILD_ANNOUNCED.swap(true, Ordering::Relaxed) {
        address_log(format_args!(
            "UNSUPPORTED GAME BUILD (first asked by {what}): {} -- every game address in this DLL \
             was measured on {SUPPORTED_FILE_VERSION}, so on this build each one would reach \
             whatever code now occupies it. All of them are refused; a new patch needs the \
             constants re-measured",
            describe_build()
        ));
    }
    false
}

/// `address` on the running build, or `None` if it may not be used.
///
/// * `Some(address)` -- the address is outside the game image (an import resolved by
///   `GetProcAddress` means the same thing on every patch), or the running build is 2.7.2.0, the
///   build it was measured on.
/// * `None` -- the game is another build. The caller must not proceed.
///
/// `what` names the caller in the log line, because a refusal is only actionable if a reader can
/// tell which feature just went inert.
pub fn resolve_game_address(address: usize, what: &str) -> Option<usize> {
    resolve_game_address_fmt(address, format_args!("{what}"))
}

/// [`resolve_game_address`], with the label built by the caller's own `format_args!`.
///
/// The label is the only thing a reader of a refusal line has to go on, and the useful ones are
/// composite -- `er_game_base::mem::game_rva` wants to print the constant's name and the source
/// line that asked for it. Taking `Arguments` rather than `&str` lets it do that without a
/// `format!` allocation on every resolution, including the overwhelming majority that succeed.
pub fn resolve_game_address_fmt(address: usize, what: core::fmt::Arguments<'_>) -> Option<usize> {
    #[cfg(windows)]
    {
        (!is_game_image_address(address) || supported_or_announce(what)).then_some(address)
    }
    // Host builds have no running game to resolve against, and reaching the Win32 externs from a
    // host unit test is a link error rather than a wrong answer. `er-save-loader` runs its tests
    // on the host against a fake module base, so this passthrough is what keeps the resolver
    // callable from code that is also host-tested.
    #[cfg(not(windows))]
    {
        let _ = what;
        Some(address)
    }
}

/// Is `rva` one a constant detour may target? See [`DETOUR_ALLOWLIST`].
#[cfg_attr(not(windows), allow(dead_code))]
fn detour_allowed(rva: u32) -> bool {
    DETOUR_ALLOWLIST.binary_search(&rva).is_ok()
}

/// The address to detour for `address` on the running build, or `None`.
///
/// # Why this is not [`resolve_game_address`]
///
/// Being the right address and being a safe place to write five bytes are different claims. A
/// call or a read needs the first; a detour needs the second as well -- that the destination is a
/// real function entry with a relocatable prologue -- and a constant says nothing about that by
/// itself. So a game-image address is detoured only if its rva is in [`DETOUR_ALLOWLIST`], where
/// each row was compared instruction by instruction and both images agreed on the entry. A data
/// global, a vtable, or a mid-function call site stays callable and readable and is refused here.
///
/// Measured 2026-08-29: when weaker rows were allowed to carry detours, er-armament-icons installed
/// five of them and the game died 2.0 seconds in, at the first overlay draw.
///
/// A hook on an address the caller found at runtime goes through `er_hook`'s `*_runtime_derived`
/// entry points instead, which audit the live `.pdata` rather than consulting this list.
#[cfg(windows)]
pub fn resolve_detour_address(address: usize, what: &str) -> Option<usize> {
    if !is_game_image_address(address) {
        return Some(address);
    }
    if !supported_or_announce(format_args!("{what}")) {
        return None;
    }
    let base = game_module_base().ok()?;
    let rva = u32::try_from(address - base).ok()?;
    if detour_allowed(rva) {
        return Some(address);
    }
    // Bounded, not silenced -- see the refusal ledger. The first 12 are written in full, then a
    // went-quiet marker, then a restatement at each power of ten carrying the running count.
    let occurrence = note_refusal(&DETOUR_REFUSALS, &DETOUR_REFUSAL_OVERFLOW, rva);
    match refusal_line_for(occurrence) {
        RefusalLine::Full => address_log(format_args!(
            "ADDRESS REFUSED FOR DETOUR ({what}): 0x{address:x} -- this rva is not on the 2.7.2.0 \
             detour allowlist (`crates/er-game-base/src/detour_allowlist.rs`), so nothing says it is \
             a function entry MinHook may write five bytes at. Call and read it as before; hook it \
             only after measuring it and adding its row"
        )),
        RefusalLine::WentQuiet => address_log(format_args!(
            "ADDRESS REFUSED FOR DETOUR ({what}): 0x{address:x} -- refusal {occurrence} of this \
             address; further refusals of it are counted, not written, and restated at each power \
             of ten"
        )),
        RefusalLine::Milestone => address_log(format_args!(
            "ADDRESS REFUSED FOR DETOUR ({what}): 0x{address:x} -- refusal {occurrence} of this \
             address. Restated so the log carries how hot the dead path is, not just that it exists"
        )),
        RefusalLine::Suppressed => {}
    }
    None
}

/// Host builds have no game to detour.
#[cfg(not(windows))]
pub fn resolve_detour_address(address: usize, what: &str) -> Option<usize> {
    let _ = what;
    Some(address)
}

/// The rva of a call site on the running build, or `None` when it cannot be placed.
///
/// A call site is a return address: a byte in the middle of a function, captured off the live
/// stack by `RtlCaptureStackBackTrace` and compared -- as `frame - module_base` -- against a
/// constant. It is spelled as the containing function plus an offset because that is its
/// identity: the return of the Nth `call` in a named function. The offset is added here, in Rust,
/// and never enters [`DETOUR_ALLOWLIST`]; a mid-function address is not a detour licence.
///
/// `what` names the caller in the refusal line, so a reader can tell which comparison went inert.
#[cfg(windows)]
pub fn resolve_call_site_rva(
    function_rva: usize,
    offset_in_function: usize,
    what: &str,
) -> Option<usize> {
    let base = game_module_base().ok().filter(|&b| b != 0)?;
    let entry = resolve_game_address(base + function_rva, what)?;
    Some(entry.checked_sub(base)? + offset_in_function)
}

/// Host builds have no running game, so no stack frame can carry a game rva to compare against.
#[cfg(not(windows))]
pub fn resolve_call_site_rva(
    function_rva: usize,
    offset_in_function: usize,
    what: &str,
) -> Option<usize> {
    let _ = (function_rva, offset_in_function, what);
    None
}

/// [`resolve_call_site_rva`] for a call-site band anchored on one function.
pub fn resolve_call_site_band(
    function_rva: usize,
    start_offset: isize,
    end_offset: isize,
    what: &str,
) -> Option<core::ops::Range<usize>> {
    let entry = resolve_call_site_rva(function_rva, 0, what)?;
    let shift = |offset: isize| {
        if offset >= 0 {
            entry.checked_add(offset as usize)
        } else {
            entry.checked_sub(offset.unsigned_abs())
        }
    };
    let start = shift(start_offset)?;
    let end = shift(end_offset)?;
    (start < end).then_some(start..end)
}

// ============================================================================
// a detour refusal is a failure signal. Bound its repetition; never silence it.
//
// A missing licence must cost a feature loudly while a wrong one corrupts silently, so a refusal
// is written every time it first happens. The refusal path emits a formatted file append per call
// at whatever rate its caller runs, though, and a per-frame caller of a refused address once wrote
// 628 lines in one session. So the first [`REFUSALS_LOGGED_PER_ADDRESS`] refusals of an address
// are logged in full, unconditionally (12 rather than 1, because several callers refusing one
// address are several dead features and each gets its own line); refusal 13 logs a went-quiet
// marker, so a reader can tell "it stopped happening" from "it stopped being written down"; after
// that the address logs again at each power of ten with the running count, so the magnitude still
// reaches the log while its growth stays logarithmic.
//
// Every one of those lines keeps the `ADDRESS REFUSED FOR DETOUR (<label>): 0x<addr>` prefix.
// ============================================================================

/// Refusals of one address logged in full before it goes quiet.
///
/// 12 matches `er_hook::detour_site::MAX_REFUSAL_LINES`, the existing in-repo precedent for this
/// shape. It is deliberately not 1: a refusal names its caller, and one address is refused by
/// several callers whose labels differ, so a cap of 1 would report one dead feature and hide the
/// rest. `what` cannot be keyed on without formatting it into a `String` on the failure path, so
/// a loose per-address cap is how caller diversity survives.
const REFUSALS_LOGGED_PER_ADDRESS: u64 = 12;

/// Slots in the refusal ledger.
///
/// The worst case observed on one path was 72 distinct detour refusals in a boot (2026-08-29).
/// 512 slots and 16 probes hold more than three times that with nothing spilled, for 4 KiB of
/// statics, which `the_measured_worst_case_of_real_addresses_fits_without_spilling` checks over
/// real rvas. An address that still finds no slot is not silenced; see [`note_refusal`].
const REFUSAL_SLOTS: usize = 512;

/// Probe length for the open-addressed refusal ledger. See [`REFUSAL_SLOTS`].
#[cfg_attr(not(windows), allow(dead_code))]
const REFUSAL_PROBES: usize = 16;

/// Occupied-slot encoding: the low 32 bits hold `rva + 1`, so a zero word means free.
#[cfg_attr(not(windows), allow(dead_code))]
const REFUSAL_KEY_MASK: u64 = 0xffff_ffff;

/// One increment of the count packed into the high 32 bits of a slot.
#[cfg_attr(not(windows), allow(dead_code))]
const REFUSAL_COUNT_STEP: u64 = 1u64 << 32;

/// Stop incrementing here, leaving slack for threads already inside `fetch_add`, so a count can
/// never carry into the key bits and rename an address.
#[cfg_attr(not(windows), allow(dead_code))]
const REFUSAL_COUNT_CEILING: u64 = (u32::MAX as u64) - 64;

/// The powers of ten a bounded address logs at after going quiet. It stops at 1e9 because a
/// per-address count saturates at [`REFUSAL_COUNT_CEILING`].
#[cfg_attr(not(windows), allow(dead_code))]
const REFUSAL_MILESTONES: [u64; 8] = [
    100,
    1_000,
    10_000,
    100_000,
    1_000_000,
    10_000_000,
    100_000_000,
    1_000_000_000,
];

/// Refusals that found no ledger slot, counted together. See [`note_refusal`].
#[cfg_attr(not(windows), allow(dead_code))]
static DETOUR_REFUSAL_OVERFLOW: AtomicU64 = AtomicU64::new(0);

/// Per-address detour refusal counts.
#[cfg_attr(not(windows), allow(dead_code))]
static DETOUR_REFUSALS: [AtomicU64; REFUSAL_SLOTS] = [const { AtomicU64::new(0) }; REFUSAL_SLOTS];

/// Which line, if any, refusal number `occurrence` of one address should emit.
#[cfg_attr(not(windows), allow(dead_code))]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum RefusalLine {
    /// The full refusal, with the caller label and the reason.
    Full,
    /// The last line before this address goes quiet.
    WentQuiet,
    /// A power-of-ten restatement carrying the running count.
    Milestone,
    /// Counted, not written.
    Suppressed,
}

/// The bound, as a pure function of the occurrence number so it can be tested for its values
/// rather than for the shape of the code that calls it.
#[cfg_attr(not(windows), allow(dead_code))]
fn refusal_line_for(occurrence: u64) -> RefusalLine {
    if occurrence <= REFUSALS_LOGGED_PER_ADDRESS {
        return RefusalLine::Full;
    }
    if occurrence == REFUSALS_LOGGED_PER_ADDRESS + 1 {
        return RefusalLine::WentQuiet;
    }
    if REFUSAL_MILESTONES.contains(&occurrence) {
        return RefusalLine::Milestone;
    }
    RefusalLine::Suppressed
}

/// Slot an rva hashes to. Multiply-shift on the golden ratio, because rvas are dense, aligned and
/// clustered -- the low bits are nearly constant across a set of them, so `rva % SLOTS` would pile
/// the whole set onto a handful of slots and overflow a ledger with room to spare.
#[cfg_attr(not(windows), allow(dead_code))]
fn refusal_slot(rva: u32, slots: usize) -> usize {
    const GOLDEN: u64 = 0x9E37_79B9_7F4A_7C15;
    ((rva as u64).wrapping_mul(GOLDEN) >> 32) as usize % slots
}

/// Record one refusal of `rva` and return its 1-based occurrence number for that address.
///
/// Steady state, for an address already in the ledger: a multiply-shift, one relaxed load, a
/// compare and one relaxed `fetch_add`. No lock, no allocation, and nothing at all on the success
/// path. `Relaxed` throughout is right because no data is published through these words; the
/// compare-exchange is what makes exactly one racing thread claim a free slot.
///
/// An address that finds no free slot within [`REFUSAL_PROBES`] falls to a shared overflow
/// counter, which is itself bounded by the same rule.
#[cfg_attr(not(windows), allow(dead_code))]
fn note_refusal(slots: &[AtomicU64], overflow: &AtomicU64, rva: u32) -> u64 {
    let key = rva as u64 + 1;
    let start = refusal_slot(rva, slots.len());
    for probe in 0..REFUSAL_PROBES {
        let slot = &slots[(start + probe) % slots.len()];
        let mut current = slot.load(Ordering::Relaxed);
        loop {
            if current == 0 {
                match slot.compare_exchange_weak(
                    0,
                    REFUSAL_COUNT_STEP | key,
                    Ordering::Relaxed,
                    Ordering::Relaxed,
                ) {
                    Ok(_) => return 1,
                    // Lost the claim -- to this same address or to another one. Re-examine the
                    // word rather than probing on, or two threads racing on a fresh slot would
                    // give the same address two slots and two independent counts.
                    Err(actual) => current = actual,
                }
                continue;
            }
            if current & REFUSAL_KEY_MASK != key {
                break;
            }
            let count = current >> 32;
            if count >= REFUSAL_COUNT_CEILING {
                return count;
            }
            return (slot.fetch_add(REFUSAL_COUNT_STEP, Ordering::Relaxed) >> 32) + 1;
        }
    }
    overflow.fetch_add(1, Ordering::Relaxed) + 1
}

/// Is Seamless Co-op (`ersc.dll`) resident in this process?
///
/// A monotonic latch, not a sample. me3 defers native loading until after Arxan init and loads
/// Seamless through its me2 compatibility shim, so `ersc.dll` is not yet registered in the PEB when
/// a `DllMain` runs (+1ms) -- a raw module handle returns false that early and would wrongly gate
/// every Seamless decision to "vanilla". So this re-polls on each call until the module first
/// resolves, then latches true forever and never re-samples. That makes the answer correct at the
/// moment each call site needs it (title terms-of-service build ~+16.9s, save read on the first
/// game-task tick), regardless of the early false negative, and it never flaps back to false.
///
/// It lives here rather than in a product telemetry module because it has no product state at all,
/// and because the System>Quit row cloner reads it to decide which help line the
/// **Load Character from File** row advertises -- from `er-quit-menu-core`, across a crate boundary.
#[cfg(windows)]
pub fn seamless_coop_loaded() -> bool {
    use core::sync::atomic::AtomicUsize;

    /// 0 = not yet seen, 1 = observed resident. Once latched it never clears: Seamless is not
    /// unloaded mid-session.
    static LATCHED: AtomicUsize = AtomicUsize::new(0);
    const MODULE_NAME: &[u8] = b"ersc.dll\0";

    unsafe extern "system" {
        fn GetModuleHandleA(module_name: *const u8) -> isize;
    }

    if LATCHED.load(Ordering::Relaxed) != 0 {
        return true;
    }
    // Safety: a NUL-terminated ASCII name; the call only reads it and returns a handle or zero.
    let present = unsafe { GetModuleHandleA(MODULE_NAME.as_ptr()) } != 0;
    if present {
        LATCHED.store(1, Ordering::Relaxed);
    }
    present
}

/// Host builds have no module table to ask, and no Seamless to find.
#[cfg(not(windows))]
pub fn seamless_coop_loaded() -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::{
        AtomicU64, DETOUR_ALLOWLIST, Ordering, REFUSAL_COUNT_CEILING, REFUSAL_KEY_MASK,
        REFUSAL_PROBES, REFUSAL_SLOTS, REFUSALS_LOGGED_PER_ADDRESS, RefusalLine, detour_allowed,
        note_refusal, refusal_line_for, refusal_slot,
    };

    /// Total refusals recorded in one ledger, and how many distinct addresses hold a slot.
    fn refusal_totals(slots: &[AtomicU64], overflow: &AtomicU64) -> (u64, u32) {
        let mut total = overflow.load(Ordering::Relaxed);
        let mut addresses = 0u32;
        for slot in slots {
            let word = slot.load(Ordering::Relaxed);
            if word != 0 {
                addresses += 1;
                total += word >> 32;
            }
        }
        (total, addresses)
    }

    /// The allowlist is sorted and unique, because the resolver binary-searches it, and every row
    /// sits inside the primary `.text` of 2.7.2.0.
    #[test]
    fn the_detour_allowlist_is_sorted_unique_and_in_text() {
        /// End of the primary `.text` in `eldenring-deobf-1.17.2.bin`: rva `0x1000` plus its
        /// virtual size `0x29a6000`, read off the live section table on 2026-10-23.
        const TEXT_RVA_END_2720: u32 = 0x29a_7000;
        assert!(
            DETOUR_ALLOWLIST.windows(2).all(|pair| pair[0] < pair[1]),
            "DETOUR_ALLOWLIST is not strictly ascending, so binary_search can miss a row"
        );
        let outside: Vec<u32> = DETOUR_ALLOWLIST
            .iter()
            .copied()
            .filter(|&rva| !(0x1000..TEXT_RVA_END_2720).contains(&rva))
            .collect();
        assert!(
            outside.is_empty(),
            "allowlist rows outside .text cannot be function entries: {outside:#x?}"
        );
    }

    /// What the allowlist must and must not admit, from the port's own measurements.
    #[test]
    fn the_detour_allowlist_admits_entries_and_refuses_call_only_addresses() {
        // `CS::MapItemManImpl::GiveItems`, hooked by er-seamless-bugfixes.
        assert!(detour_allowed(0x0056_18b0));
        // The two three-byte leaves too short to patch: callable, never hookable.
        assert!(!detour_allowed(0x007a_f0a0));
        assert!(!detour_allowed(0x01c9_6490));
        // `WorldChrMan`'s global is data.
        assert!(!detour_allowed(0x03d6_d018));
    }

    /// Every refusal line keeps the `ADDRESS REFUSED FOR DETOUR (<label>): 0x<addr>` prefix.
    #[test]
    fn every_refusal_line_keeps_its_harvestable_prefix() {
        for statement in refusal_literals() {
            let message = statement
                .split_once("\"")
                .expect("a refusal is emitted from a string literal")
                .1;
            assert!(
                message.starts_with("ADDRESS REFUSED FOR DETOUR ({what}): 0x{address:x}"),
                "this refusal line no longer opens with the prefix log readers match:\n{message}"
            );
        }
    }

    /// The source above the test module, so the test's own pattern strings are not searched.
    fn source_above_tests() -> &'static str {
        include_str!("game_build.rs")
            .split_once("#[cfg(test)]")
            .expect("this module has a test cfg")
            .0
    }

    /// Each `address_log(format_args!(...))` statement that emits a refusal.
    ///
    /// The count is asserted where the population is built, so a reworded line cannot leave the
    /// search by being renamed out of it and leave the caller agreeing the survivors are fine.
    fn refusal_literals() -> Vec<&'static str> {
        const EXPECTED: usize = 3;
        let code = source_above_tests();
        let mut found = Vec::new();
        let mut cursor = 0usize;
        while let Some(hit) = code[cursor..].find("ADDRESS REFUSED") {
            let at = cursor + hit;
            cursor = at + 1;
            // Only the emitted literals, not the prose above them: every emitted line carries the
            // caller label right after the message name, which no doc comment does.
            if !code[at..(at + 40).min(code.len())].contains("({what}):") {
                continue;
            }
            let start = code[..at]
                .rfind("address_log(format_args!(")
                .expect("a refusal is emitted through address_log");
            let end = at
                + code[at..]
                    .find("))")
                    .expect("a closed address_log statement");
            found.push(&code[start..end]);
        }
        assert_eq!(
            found.len(),
            EXPECTED,
            "expected {EXPECTED} refusal literals -- full, went-quiet and milestone on the detour \
             path. Found {}: a line was deleted, added, or reworded out of the shape",
            found.len()
        );
        found
    }

    /// The first refusal of an address is always written, whichever address it is.
    ///
    /// This is the load-bearing half of the bound: a feature that goes inert has to cost a line
    /// the first time, and a bound that could swallow a first occurrence would convert a loud
    /// failure into a silent one.
    #[test]
    fn the_first_refusal_of_every_address_is_written_in_full() {
        let ledger: Vec<AtomicU64> = (0..REFUSAL_SLOTS).map(|_| AtomicU64::new(0)).collect();
        let overflow = AtomicU64::new(0);
        let addresses: Vec<u32> = core::iter::once(0)
            .chain((0..REFUSAL_SLOTS as u32 - 2).map(|n| 0x40_0000 + n * 0x10))
            .chain(core::iter::once(u32::MAX))
            .collect();
        let unwritten: Vec<u32> = addresses
            .iter()
            .copied()
            .filter(|rva| {
                refusal_line_for(note_refusal(&ledger, &overflow, *rva)) != RefusalLine::Full
            })
            .collect();
        assert!(
            unwritten.is_empty(),
            "{} of {} addresses had their first refusal suppressed: {unwritten:#x?}",
            unwritten.len(),
            addresses.len()
        );
    }

    /// The ledger holds the worst case actually measured, with headroom, without spilling.
    ///
    /// The addresses are real ones, the allowlist's own rvas, and that is the load-bearing choice:
    /// synthetic rvas at a fixed stride land one per probe run whatever mixing is applied, while
    /// real game addresses are near-uniformly 16-byte aligned and pile up under a low-bit index.
    #[test]
    fn the_measured_worst_case_of_real_addresses_fits_without_spilling() {
        /// 2026-08-29: 72 distinct detour refusals in one boot.
        const MEASURED_WORST_CASE: usize = 72;
        /// More than three times that, so the next bad day has room.
        const REQUIRED_HEADROOM: usize = 252;
        /// Distinct home slots the real addresses must reach; a low-bit index reaches a handful.
        const MIN_DISTINCT_HOME_SLOTS: usize = 48;

        let sources: Vec<u32> = DETOUR_ALLOWLIST.to_vec();
        assert!(
            sources.len() >= REQUIRED_HEADROOM,
            "the allowlist holds only {} rvas, too few to load the ledger to {REQUIRED_HEADROOM}",
            sources.len()
        );
        let home_slots: std::collections::BTreeSet<usize> = sources
            .iter()
            .take(MEASURED_WORST_CASE)
            .map(|rva| refusal_slot(*rva, REFUSAL_SLOTS))
            .collect();
        assert!(
            home_slots.len() >= MIN_DISTINCT_HOME_SLOTS,
            "{MEASURED_WORST_CASE} real game addresses reached only {} of {REFUSAL_SLOTS} home \
             slots, so the fits below would be luck, not headroom",
            home_slots.len()
        );
        for load in [MEASURED_WORST_CASE, REQUIRED_HEADROOM] {
            let ledger: Vec<AtomicU64> = (0..REFUSAL_SLOTS).map(|_| AtomicU64::new(0)).collect();
            let overflow = AtomicU64::new(0);
            for rva in sources.iter().take(load) {
                note_refusal(&ledger, &overflow, *rva);
            }
            let (_, held) = refusal_totals(&ledger, &overflow);
            assert_eq!(
                (overflow.load(Ordering::Relaxed), held as usize),
                (0, load),
                "{load} real addresses did not all get their own slot in a {REFUSAL_SLOTS}-slot \
                 ledger at {REFUSAL_PROBES} probes"
            );
        }
    }

    /// One address logs a bounded number of lines and an unbounded count.
    #[test]
    fn one_address_goes_quiet_but_its_count_keeps_running() {
        const REFUSALS: u64 = 5_000;
        let ledger: Vec<AtomicU64> = (0..REFUSAL_SLOTS).map(|_| AtomicU64::new(0)).collect();
        let overflow = AtomicU64::new(0);

        let lines: Vec<(u64, RefusalLine)> = (1..=REFUSALS)
            .map(|expected| {
                let occurrence = note_refusal(&ledger, &overflow, 0xcf_c130);
                assert_eq!(occurrence, expected, "the occurrence number skipped");
                (occurrence, refusal_line_for(occurrence))
            })
            .filter(|(_, line)| *line != RefusalLine::Suppressed)
            .collect();

        let written: Vec<u64> = lines.iter().map(|(occurrence, _)| *occurrence).collect();
        assert_eq!(
            written,
            vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 100, 1_000],
            "expected the first {REFUSALS_LOGGED_PER_ADDRESS} in full, then one went-quiet \
             marker, then a restatement at each power of ten -- and nothing else"
        );
        let (total, addresses) = refusal_totals(&ledger, &overflow);
        assert_eq!(total, REFUSALS, "the ledger must count every refusal");
        assert_eq!(addresses, 1, "one address must occupy exactly one slot");
    }

    /// Two addresses that hash to the same slot keep separate counts.
    #[test]
    fn colliding_addresses_keep_separate_counts() {
        let ledger: Vec<AtomicU64> = (0..REFUSAL_SLOTS).map(|_| AtomicU64::new(0)).collect();
        let overflow = AtomicU64::new(0);
        let first = 0xcf_c130u32;
        let slot = refusal_slot(first, REFUSAL_SLOTS);
        let second = (first + 1..u32::MAX)
            .find(|rva| refusal_slot(*rva, REFUSAL_SLOTS) == slot)
            .expect("some other rva hashes to the same slot");

        assert_eq!(note_refusal(&ledger, &overflow, first), 1);
        assert_eq!(note_refusal(&ledger, &overflow, first), 2);
        assert_eq!(
            note_refusal(&ledger, &overflow, second),
            1,
            "0x{second:x} collides with 0x{first:x} and inherited its count"
        );
        assert_eq!(note_refusal(&ledger, &overflow, first), 3);
        assert_eq!(note_refusal(&ledger, &overflow, second), 2);
        assert_eq!(refusal_totals(&ledger, &overflow), (5, 2));
    }

    /// An address that finds no slot is still reported, and still bounded.
    #[test]
    fn an_address_with_no_room_in_the_ledger_is_still_reported() {
        let ledger = [AtomicU64::new(0)];
        let overflow = AtomicU64::new(0);
        assert_eq!(note_refusal(&ledger, &overflow, 0x11_1111), 1);

        let homeless: Vec<RefusalLine> = (0..40)
            .map(|_| refusal_line_for(note_refusal(&ledger, &overflow, 0x22_2222)))
            .collect();
        assert_eq!(homeless.first(), Some(&RefusalLine::Full));
        assert!(homeless.contains(&RefusalLine::WentQuiet));
        assert_eq!(overflow.load(Ordering::Relaxed), 40);
    }

    /// The count cannot carry into the key bits and rename an address.
    #[test]
    fn a_saturated_count_never_corrupts_the_address_it_belongs_to() {
        let rva = 0xcf_c130u32;
        let ledger = [AtomicU64::new(
            (REFUSAL_COUNT_CEILING << 32) | (rva as u64 + 1),
        )];
        let overflow = AtomicU64::new(0);
        for _ in 0..8 {
            assert_eq!(note_refusal(&ledger, &overflow, rva), REFUSAL_COUNT_CEILING);
        }
        assert_eq!(
            ledger[0].load(Ordering::Relaxed) & REFUSAL_KEY_MASK,
            rva as u64 + 1,
            "the count carried into the key"
        );
        assert_eq!(overflow.load(Ordering::Relaxed), 0);
    }
}
