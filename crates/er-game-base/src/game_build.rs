//! Which ELDEN RING this DLL is loaded into, read from the running image's own version resource.
//!
//! # Why this exists
//!
//! Every game address in this workspace is a 1.16.2 RVA. On 2026-08-27 the game shipped 1.17,
//! which moved code, and a detour installed at a 1.16.2 RVA landed in the MIDDLE of a different
//! 1.17 function: `0x1407ada40` is a real prologue in 1.16.2 (`push rbp; push rsi; push rdi`) and
//! `xor r15d, r15d` in 1.17. The result was an access violation ~3.5s into boot with a fabricated
//! `image_base | 0x64` pointer in `rcx` -- a crash whose backtrace points into game code and gives
//! no hint that the real fault was an address from a previous patch.
//!
//! A hook installed on an unrecognised build cannot be made safe by trying harder at the call
//! site; the address itself is meaningless. So the useful thing to know, before any detour goes
//! in, is simply *which build is this* -- and the image says so itself.
//!
//! # What it does NOT do
//!
//! It does not gate raw byte writes. [`crate::mem`]'s readers and `er-hook`'s
//! `write_code_byte` / `patch_3byte_stub` are used by version-AGNOSTIC code that discovers its
//! own addresses by scanning (`er-ersc-sigshim` rebuilds Seamless Co-op's lost AOB landmarks on
//! exactly the builds this module calls unsupported). Refusing those would break the one thing
//! that currently works on 1.17.

// Reading the running image's own PE headers is a Windows-only operation, and these externs are
// undefined at LINK time on the host -- which host-unit-tested crates hit through `describe_build`.
#[cfg(windows)]
use crate::mem::{game_module_base, read_bytes};

/// `FileVersion` of the build every RVA in this workspace was reverse-engineered against:
/// ELDEN RING 1.16.2, whose PE `FileVersion` is 2.6.2.0.
pub const SUPPORTED_FILE_VERSION: FileVersion = FileVersion {
    major: 2,
    minor: 6,
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
/// than an early return because the Win32 externs below are undefined at LINK time on the host,
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
/// This is what separates "an address from a previous patch" from "an address that has nothing to
/// do with the game build". A detour on `user32!CreateWindowExW` or `kernel32!ExitProcess` is
/// resolved through `GetProcAddress` at runtime and is equally correct on every ELDEN RING
/// version; only addresses INSIDE this range carry a version assumption.
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

/// Whether the running image is the build this workspace's RVAs were reverse-engineered against.
///
/// An unreadable version resource counts as unsupported: "cannot tell" and "wrong build" have
/// the same consequence for an address taken from a different patch.
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
// ADDRESS RESOLUTION ACROSS BUILDS.
//
// `is_supported_build` above answers "is this the build our RVAs were written against". This
// section answers the question that follows from a NO: where, if anywhere, did that address go.
//
// It lives here rather than in `er-hook` because the danger is not specific to detours. A detour
// on a stale address is caught by the hook path, but a stale address is equally reachable as a
// CALL (`transmute(base + RVA)`) and as a data pointer, and neither goes anywhere near MinHook.
// A call through a stale address is in fact the worse of the two: it transfers control into
// whatever now occupies those bytes, which on a patched build is routinely the middle of an
// unrelated function -- an execute-fault with no unwind information and no exception record
// naming anything of ours.
// ============================================================================

include!(concat!(env!("OUT_DIR"), "/address_map_1170.rs"));

/// Signature of the sink refusal/translation lines are written to.
pub type AddressLogFn = fn(core::fmt::Arguments<'_>);
static ADDRESS_LOGGER: core::sync::atomic::AtomicUsize = core::sync::atomic::AtomicUsize::new(0);

/// Install the sink for address-resolution lines. Call once, early, before any resolution.
///
/// Default is a no-op: this crate is a zero-dependency leaf and has no log of its own, so the
/// product DLL points this at the same file its hook refusals already go to.
pub fn set_address_logger(logger: AddressLogFn) {
    ADDRESS_LOGGER.store(logger as usize, core::sync::atomic::Ordering::Release);
}

/// Windows-only: the resolver is the sole caller, and on a host build there is no running game
/// to refuse an address for, so the sink would never be reached.
#[cfg(windows)]
fn address_log(args: core::fmt::Arguments<'_>) {
    let raw = ADDRESS_LOGGER.load(core::sync::atomic::Ordering::Acquire);
    if raw != 0 {
        // SAFETY: `raw` is only ever an `AddressLogFn` stored by `set_address_logger`.
        let logger: AddressLogFn = unsafe { core::mem::transmute::<usize, AddressLogFn>(raw) };
        logger(args);
    }
}

/// Where `address` lives on the RUNNING build, or `None` if that is not known.
///
/// * `Some(address)` unchanged -- the address is outside the game image (an import resolved by
///   `GetProcAddress` means the same thing on every patch), or the running build is the one the
///   address was written for.
/// * `Some(translated)` -- the build moved, and this address has a mapping that two independent
///   passes agreed on: `scripts/map-rvas-1162-to-1170.py` found where the function's masked
///   signature re-occurs, and `scripts/verify-rva-map-1170.py` then confirmed the normalised
///   instruction sequences match over the body. `scripts/audit-1170-hook-targets.py` separately
///   confirms the destination is a real function entry, by the references the image itself makes
///   to it.
/// * `None` -- the build moved and nothing here knows where to. The caller must not proceed.
///
/// `what` names the caller in the log line, because a refusal is only actionable if a reader can
/// tell which feature just went inert.
pub fn resolve_game_address(address: usize, what: &str) -> Option<usize> {
    #[cfg(windows)]
    {
        resolve_on_running_build(address, what)
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

#[cfg(windows)]
fn resolve_on_running_build(address: usize, what: &str) -> Option<usize> {
    if !is_game_image_address(address) || is_supported_build() {
        return Some(address);
    }
    let base = crate::mem::game_module_base().ok()?;
    let rva = (address - base) as u32;
    // ALREADY TRANSLATED. Resolution is not naturally idempotent: the table is keyed by 1.16.2 RVA
    // and its values are 1.17 RVAs, so asking it where a 1.17 address moved to finds no entry and
    // the honest answer is to refuse -- which is how a correctly translated address got REFUSED on
    // its second pass through, costing `er-armament-icons` its file-open observer at 0x1411ced80.
    // The call graph no longer resolves twice; this makes a future double-resolve harmless rather
    // than merely unlikely. Sound because the only addresses that are BOTH a 1.17 destination and
    // a 1.16.2 source are the ones that did not move, where both answers are the same address;
    // `verified_map_is_idempotent` fails the build's test run if a row ever makes that untrue.
    if already_translated(rva) {
        return Some(address);
    }
    if let Some((_, moved)) = VERIFIED_1162_TO_1170.iter().find(|(from, _)| *from == rva) {
        let translated = base + *moved as usize;
        address_log(format_args!(
            "ADDRESS TRANSLATED ({what}): 0x{address:x} -> 0x{translated:x} for {} \
             (verified same function; see docs/recon/rva-map-1162-to-1170.verified.tsv)",
            describe_build()
        ));
        return Some(translated);
    }
    address_log(format_args!(
        "ADDRESS REFUSED ({what}): 0x{address:x} -- {}, and this address has no verified mapping \
         for the running build, so using it would reach whatever code now occupies it",
        describe_build()
    ));
    None
}

/// Is `rva` a 1.17 destination that must NOT be translated again?
///
/// TRANSLATION WINS OVER THE SHORTCUT, and the order is the whole point. The shortcut exists so a
/// second pass over an already-translated address hands it back instead of refusing -- the bug
/// that cost er-armament-icons its file-open observer. But at 329 rows an address can be BOTH a
/// destination of one row and the source of a different one, and if the shortcut answered first
/// it would swallow that second row's translation silently.
///
/// So an address that is a source is always translated as a source. The shortcut applies only to
/// destinations that are not sources of some other row, plus the rows that did not move, where
/// both answers are the same address anyway.
///
/// A pure function of the table so it can be tested on the host, where there is no game to
/// resolve against.
#[cfg_attr(not(windows), allow(dead_code))]
fn already_translated(rva: u32) -> bool {
    let is_destination = VERIFIED_1162_TO_1170
        .iter()
        .any(|(from, moved)| *moved == rva && *from != rva);
    let is_source_of_a_move = VERIFIED_1162_TO_1170
        .iter()
        .any(|(from, moved)| *from == rva && *moved != rva);
    is_destination && !is_source_of_a_move
}

/// The address to DETOUR for `address` on the running build, or `None`.
///
/// # Why this is not [`resolve_game_address`]
///
/// Being the right address and being a safe place to write five bytes are different claims, and
/// only one of them is established by matching a function's signature. `resolve_game_address`
/// answers the first: it will happily return a pair carried by the whole-image `.pdata` map or
/// by counting code references, which is exactly what a CALL or a READ needs. A detour needs the
/// second as well -- that the destination is a real function ENTRY with a relocatable five-byte
/// prologue -- and nothing in a signature match speaks to that.
///
/// MEASURED, 2026-08-29. When the weaker rows were allowed to carry detours, er-armament-icons
/// installed five of them and the game died ~2.0s in, at the first overlay draw. Bisected over
/// eighteen DLLs: adding that one DLL to an otherwise-surviving set was the difference. Before
/// those rows existed the same five hooks were refused as unmapped and the game lived, so the
/// regression arrived with the coverage.
///
/// To promote a row into this table, `scripts/audit-1170-hook-targets.py` has to accept it.
#[cfg(windows)]
pub fn resolve_detour_address(address: usize, what: &str) -> Option<usize> {
    if !is_game_image_address(address) || is_supported_build() {
        return Some(address);
    }
    let base = crate::mem::game_module_base().ok()?;
    let rva = (address - base) as u32;
    if DETOUR_SAFE_1162_TO_1170
        .iter()
        .any(|(from, moved)| *moved == rva && from != moved)
    {
        return Some(address);
    }
    if let Some((_, moved)) = DETOUR_SAFE_1162_TO_1170
        .iter()
        .find(|(from, _)| *from == rva)
    {
        let translated = base + *moved as usize;
        address_log(format_args!(
            "ADDRESS TRANSLATED ({what}): 0x{address:x} -> 0x{translated:x} for {} \
             (byte-verified same function AND audited as a detour target)",
            describe_build()
        ));
        return Some(translated);
    }
    // Say WHICH refusal this is. An address that resolves for a call but not for a detour is a
    // different situation from one nothing can place, and conflating them sends the next reader
    // hunting for a mapping that already exists.
    let call_only = resolve_on_running_build_quiet(rva).is_some();
    address_log(format_args!(
        "ADDRESS REFUSED FOR DETOUR ({what}): 0x{address:x} -- {}, and {}",
        describe_build(),
        if call_only {
            "while this address HAS a mapping good enough to call, it has not been audited as a \
             detour target: a signature match does not say MinHook may write five bytes there"
        } else {
            "this address has no mapping at all for the running build"
        }
    ));
    None
}

/// Host builds have no game to detour.
#[cfg(not(windows))]
pub fn resolve_detour_address(address: usize, what: &str) -> Option<usize> {
    let _ = what;
    Some(address)
}

/// Does `rva` have ANY mapping? Used only to word a refusal accurately; logs nothing.
#[cfg(windows)]
fn resolve_on_running_build_quiet(rva: u32) -> Option<u32> {
    VERIFIED_1162_TO_1170
        .iter()
        .find(|(from, _)| *from == rva)
        .map(|(_, moved)| *moved)
}

/// How many verified translations this build carries. Read by the product's startup line so a log
/// says how much of the migration is actually present, rather than leaving it to be inferred.
pub fn verified_translation_count() -> usize {
    VERIFIED_1162_TO_1170.len()
}

#[cfg(test)]
mod tests {
    use super::VERIFIED_1162_TO_1170;

    /// Every row that MOVED must still be reachable as a source.
    ///
    /// The hazard the "already translated" shortcut creates is silent: an address that is a
    /// destination of one row and the source of a different one gets handed back untouched, and
    /// the second row never runs. At 27 rows the intersection was empty and this could not
    /// happen; at 329 it is not, which is how the test earned its keep.
    ///
    /// `already_translated` therefore lets translation win, and this asserts that property over
    /// the real table rather than assuming the data stays convenient.
    #[test]
    fn verified_map_is_idempotent() {
        let shadowed: Vec<(u32, u32)> = VERIFIED_1162_TO_1170
            .iter()
            .filter(|(from, moved)| from != moved)
            .filter(|(from, _)| super::already_translated(*from))
            .copied()
            .collect();
        assert!(
            shadowed.is_empty(),
            "these rows would be swallowed by the already-translated shortcut: {shadowed:#x?}"
        );
    }

    /// A destination that nothing else claims as a source is recognised, so a double resolve is
    /// still harmless -- the reason the shortcut exists at all.
    #[test]
    fn a_pure_destination_is_recognised_as_already_translated() {
        let pure = VERIFIED_1162_TO_1170.iter().find(|(from, moved)| {
            from != moved
                && !VERIFIED_1162_TO_1170
                    .iter()
                    .any(|(other, other_moved)| other == moved && other != other_moved)
        });
        if let Some((_, moved)) = pure {
            assert!(
                super::already_translated(*moved),
                "0x{moved:x} is a destination claimed by nothing else and was not recognised"
            );
        }
    }

    /// The table has to be usable by the resolver's linear scans without a duplicate source
    /// silently shadowing a later row. Two sources that agree would be harmless; two that
    /// disagree would make the answer depend on row order.
    #[test]
    fn verified_map_has_one_answer_per_source() {
        let mut seen: Vec<(u32, u32)> = Vec::new();
        for (from, moved) in VERIFIED_1162_TO_1170 {
            if let Some((_, first)) = seen.iter().find(|(other, _)| *other == from) {
                assert_eq!(
                    *first, moved,
                    "0x{from:x} appears twice with different destinations"
                );
            } else {
                seen.push((from, moved));
            }
        }
    }
}
