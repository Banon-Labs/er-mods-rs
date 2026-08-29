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
    // than merely unlikely. Sound because no 1.17 value is also a 1.16.2 key -- the intersection is
    // empty, and `verified_map_is_idempotent` fails the build's test run if that ever changes.
    if VERIFIED_1162_TO_1170.iter().any(|(_, moved)| *moved == rva) {
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

/// How many verified translations this build carries. Read by the product's startup line so a log
/// says how much of the migration is actually present, rather than leaving it to be inferred.
pub fn verified_translation_count() -> usize {
    VERIFIED_1162_TO_1170.len()
}

#[cfg(test)]
mod tests {
    use super::VERIFIED_1162_TO_1170;

    /// No 1.17 destination may also be a 1.16.2 source, or the "already translated" shortcut in
    /// `resolve_game_address` would swallow a real translation. Empty today; asserted so that a
    /// future row cannot make the shortcut silently wrong.
    #[test]
    fn verified_map_is_idempotent() {
        let sources: Vec<u32> = VERIFIED_1162_TO_1170
            .iter()
            .map(|(from, _)| *from)
            .collect();
        let clashes: Vec<u32> = VERIFIED_1162_TO_1170
            .iter()
            .map(|(_, moved)| *moved)
            .filter(|moved| sources.contains(moved))
            .collect();
        assert!(
            clashes.is_empty(),
            "a translated destination is also a source RVA: {clashes:#x?}"
        );
    }
}
