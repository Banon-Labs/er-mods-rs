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
//! It does not gate `er-hook`'s `write_code_byte`, which is used by version-AGNOSTIC code that
//! discovers its own addresses by scanning (`er-ersc-sigshim` rebuilds Seamless Co-op's lost AOB
//! landmarks, in a FOREIGN module, on exactly the builds this module calls unsupported). Refusing
//! that would break the one thing that currently works on 1.17.
//!
//! `patch_3byte_stub` / `apply_xor_ret_stub` were named here too until 2026-08-30 and should not
//! have been: they take a 1.16.2 `rva`, not a scanned address, so they have something to translate
//! and now go through [`resolve_game_address`] like everything else.

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
    resolve_game_address_fmt(address, format_args!("{what}"))
}

/// [`resolve_game_address`], with the label built by the caller's own `format_args!`.
///
/// The label is the only thing a reader of a refusal line has to go on, and the useful ones are
/// composite -- `er_game_base::mem::game_rva` wants to print the constant's name AND the source
/// line that asked for it. Taking `Arguments` rather than `&str` lets it do that without a
/// `format!` allocation on every resolution, including the overwhelming majority that succeed.
pub fn resolve_game_address_fmt(address: usize, what: core::fmt::Arguments<'_>) -> Option<usize> {
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
fn resolve_on_running_build(address: usize, what: core::fmt::Arguments<'_>) -> Option<usize> {
    if !is_game_image_address(address) || is_supported_build() {
        return Some(address);
    }
    let base = crate::mem::game_module_base().ok()?;
    let rva = (address - base) as u32;
    // ALREADY TRANSLATED. Resolution is not naturally idempotent: the table is keyed by 1.16.2 RVA
    // and its values are 1.17 RVAs, so asking it where a 1.17 address moved to finds no entry and
    // the honest answer is to refuse -- which is how a correctly translated address got REFUSED on
    // its second pass through, costing `er-armament-icons` its file-open observer at 0x1411ced80.
    // The shortcut makes that second pass hand the address back instead of refusing it.
    //
    // IT DOES NOT MAKE A DOUBLE RESOLVE SAFE, and nothing in this function can. The shortcut
    // declines on exactly the addresses where a double resolve goes WRONG: an address that is both
    // a 1.17 destination and the 1.16.2 source of a DIFFERENT row is a source, so translation wins
    // (it must -- see `already_translated_in`), and resolving it a second time returns a third,
    // unrelated function with no error, no refusal and no log line. That is not hypothetical: it
    // is what happened to 0x7ac890 -> 0x7ad710 -> 0x7ae590 on 2026-08-30.
    //
    // So the invariant this depends on is "resolve exactly once", which spans six crates and a
    // `GetProcAddress` boundary. It is a CONVENTION -- there is no type here that distinguishes a
    // 1.16.2 RVA from a 1.17 one, and no test in this file establishes it. The machine check is
    // `scripts/check-1170-translation-collisions.py`, run from `scripts/check.sh`, which fails on
    // any collision not recorded in `scripts/1170-translation-collisions.baseline.tsv` (three at
    // the time of writing; an empty baseline means the tables carry none). The test below,
    // `every_verified_row_resolves_to_its_own_destination`, checks something weaker and different:
    // that the shortcut never swallows a source the table should have translated.
    match table_answer(&VERIFIED_1162_TO_1170, rva) {
        TableAnswer::AlreadyTranslated => return Some(address),
        TableAnswer::MovedTo(moved) => {
            let translated = base + moved as usize;
            address_log(format_args!(
                "ADDRESS TRANSLATED ({what}): 0x{address:x} -> 0x{translated:x} for {} \
                 (verified same function; see docs/recon/rva-map-1162-to-1170.verified.tsv)",
                describe_build()
            ));
            return Some(translated);
        }
        TableAnswer::Unmapped => {}
    }
    address_log(format_args!(
        "ADDRESS REFUSED ({what}): 0x{address:x} -- {}, and this address has no verified mapping \
         for the running build, so using it would reach whatever code now occupies it",
        describe_build()
    ));
    None
}

/// Is `rva` a 1.17 destination of this table that must NOT be translated again?
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
/// Both tables need this rule and they need the SAME rule. The detour table used to ask a looser
/// question -- is this address any row's destination -- which at 27 rows was safe by accident:
/// nothing was both. At 216 rows two addresses are (`0x6156c0` and `0x7ad710`), and the loose
/// form would hand back a stale 1.16.2 source untranslated on the grounds that some other row
/// moved a different function onto it. That loose form is what
/// `translation_wins_over_the_shortcut_on_a_collision` exists to keep out, on a table it builds
/// itself rather than on whichever rows the ledgers happen to hold this week.
///
/// A pure function of the table so it can be tested on the host, where there is no game to
/// resolve against. It was called through a `VERIFIED_1162_TO_1170`-shaped wrapper named
/// `already_translated` until 2026-08-30; [`table_answer`] took that over.
#[cfg_attr(not(windows), allow(dead_code))]
fn already_translated_in(table: &[(u32, u32)], rva: u32) -> bool {
    let is_destination = table
        .iter()
        .any(|(from, moved)| *moved == rva && *from != rva);
    let is_source_of_a_move = table
        .iter()
        .any(|(from, moved)| *from == rva && *moved != rva);
    is_destination && !is_source_of_a_move
}

/// What a table says about one RVA, in the order the resolvers ask it.
///
/// Both resolvers used to inline this decision, and the tests then re-implemented it a third
/// time -- which is how a test could go on passing while claiming to cover the order of the two
/// checks it never actually ran. One function, asked by the resolvers and by the tests.
#[cfg_attr(not(windows), allow(dead_code))]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum TableAnswer {
    /// A 1.17 destination that no row claims as a source: hand it back unchanged.
    AlreadyTranslated,
    /// A 1.16.2 source; the running build has that function at this RVA.
    MovedTo(u32),
    /// No row names this RVA as a source. The caller must not proceed.
    Unmapped,
}

/// [`already_translated_in`] and the table lookup, in the order that makes translation win.
///
/// The order is the whole point and is asserted by
/// `translation_wins_over_the_shortcut_on_a_collision`: an address that is both a destination and
/// a source is answered as a SOURCE, because the shortcut declines on it.
#[cfg_attr(not(windows), allow(dead_code))]
fn table_answer(table: &[(u32, u32)], rva: u32) -> TableAnswer {
    if already_translated_in(table, rva) {
        return TableAnswer::AlreadyTranslated;
    }
    match table.iter().find(|(from, _)| *from == rva) {
        Some((_, moved)) => TableAnswer::MovedTo(*moved),
        None => TableAnswer::Unmapped,
    }
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
    // Same rule, same order, same function as the CALL path above -- including the part it cannot
    // establish. See the comment there.
    match table_answer(&DETOUR_SAFE_1162_TO_1170, rva) {
        TableAnswer::AlreadyTranslated => return Some(address),
        TableAnswer::MovedTo(moved) => {
            let translated = base + moved as usize;
            address_log(format_args!(
                "ADDRESS TRANSLATED ({what}): 0x{address:x} -> 0x{translated:x} for {} \
                 (byte-verified same function AND audited as a detour target)",
                describe_build()
            ));
            return Some(translated);
        }
        TableAnswer::Unmapped => {}
    }
    // Say WHICH refusal this is. There are three of them and they send a reader to three
    // different places, so reporting them as one wasted a day: 65 addresses were investigated as
    // missing map coverage when they were already-translated addresses arriving for a second
    // opinion, and the map that produced them was sitting right there.
    let call_only = resolve_on_running_build_quiet(rva).is_some();
    let arrived_translated = VERIFIED_1162_TO_1170
        .iter()
        .find(|(from, moved)| *moved == rva && *from != rva)
        .map(|(from, _)| *from);
    address_log(format_args!(
        "ADDRESS REFUSED FOR DETOUR ({what}): 0x{address:x} -- {}, and {}",
        describe_build(),
        match (arrived_translated, call_only) {
            // A caller resolved this through `game_rva` before asking to hook it, so the address
            // is right and the question is only whether its ROW may carry a detour.
            (Some(source), _) => format!(
                "this is already the translation of 1.16.2 0x{:x}, whose row is not detour-safe: \
                 the pair is not verified identical over the body, or the two images disagree \
                 about where a function starts there",
                source as usize + base
            ),
            (None, true) =>
                "while this address HAS a mapping good enough to call, it has not been audited as \
                 a detour target: a signature match does not say MinHook may write five bytes \
                 there"
                    .to_string(),
            (None, false) => "this address has no mapping at all for the running build".to_string(),
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

/// The RVA of a CALL SITE on the running build, or `None` when it cannot be placed.
///
/// # The class of bug this closes
///
/// A call site is a RETURN ADDRESS: a byte in the middle of a function, captured off the live
/// stack by `RtlCaptureStackBackTrace` and compared -- as `frame - module_base` -- against a
/// 1.16.2 constant. Nine such comparisons existed in this workspace on 2026-08-30, and every one
/// of them failed in PERFECT SILENCE on 1.17: no hook is installed, no address is resolved, so
/// there is no `HOOK REFUSED` and no `ADDRESS REFUSED`. The comparison simply never matches and
/// the feature behind it never runs. Two user-visible features were dead this way -- the three
/// cloned rows on the System>Quit tab, and the title FadeIn suppression -- with nothing in any
/// log to say so.
///
/// # Why it takes a function and an offset rather than one address
///
/// The address map is keyed on `.pdata` function STARTS, because that is what a masked signature
/// can identify and what the linker records. A mid-function address is not a function start, so
/// it can never appear in the map -- `scripts/select-needed-1170-rows.py` cannot even see it.
///
/// What a call site DOES have is a stable identity: it is the return of the Nth `call` in a named
/// function, and the offset of that call within its function survives the move whenever the body
/// is unchanged. So the mappable half is the containing function, and the offset rides along.
/// `scripts/derive-callsite-1170.py` prints the evidence for a given site: the `.pdata` record
/// that contains it, the map's pair for that function, and the callee each image's `E8` reaches
/// at the same offset -- which must be the same function under the map.
///
/// # Why this is not a detour licence
///
/// It resolves through [`resolve_game_address`], which reads the CALL/READ table. A detour needs
/// `resolve_detour_address` and its separate, stricter table. That separation is the whole point:
/// putting a mid-function address in a verdict table would license it as a DETOUR target --
/// `DETOURABLE_ENTRY_EVIDENCE` accepts `NEITHER-ENTRY` -- and MinHook would then write five bytes
/// into the middle of a live function. The offset here is added in Rust, AFTER resolution, and
/// never enters a table at all.
///
/// `what` names the caller in the refusal line, so a reader can tell which comparison went inert.
#[cfg(windows)]
pub fn resolve_call_site_rva(
    function_rva: usize,
    offset_in_function: usize,
    what: &str,
) -> Option<usize> {
    let base = crate::mem::game_module_base().ok().filter(|&b| b != 0)?;
    let entry = resolve_game_address(base + function_rva, what)?;
    Some(entry.checked_sub(base)? + offset_in_function)
}

/// Host builds have no running game, so no stack frame can carry a game RVA to compare against.
#[cfg(not(windows))]
pub fn resolve_call_site_rva(
    function_rva: usize,
    offset_in_function: usize,
    what: &str,
) -> Option<usize> {
    let _ = (function_rva, offset_in_function, what);
    None
}

/// [`resolve_call_site_rva`] for a call-site BAND anchored on one function.
///
/// A band whose endpoints sit at fixed offsets from a single named function translates exactly
/// when that function does. A band spanning MANY functions does NOT: between 2.6.2.0 and 2.7.0.0
/// neighbouring functions moved by different deltas (+0xdf0, +0xe20, +0xe30, +0xe40, +0xe80,
/// +0xe90 and +0x1560 all occur inside `0x7a3000..0x7a4000` alone), so its width is not preserved
/// and no anchor can carry it. Such a band has to be refused, not translated -- see the caller in
/// `system_quit_dialog_handlers.rs` that does exactly that.
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
    use super::{DETOUR_SAFE_1162_TO_1170, TableAnswer, VERIFIED_1162_TO_1170, table_answer};

    /// VACUOUS QUANTIFICATION, and why every test below counts what it walked.
    ///
    /// Two tests here used to filter the table to rows where `from != moved` and then assert a
    /// predicate whose second conjunct is `!is_source_of_a_move(rva)` -- false, by that very
    /// filter. The filtered set and the asserted property could not co-occur, so the assertion ran
    /// over an empty set and passed unconditionally. Nothing in the output distinguished "walked
    /// 476 rows, all fine" from "walked none", and the doc on `resolve_on_running_build` cited one
    /// of them as proof that a hazard the data exhibits three times could not happen.
    ///
    /// So: no test here concludes anything from a filtered set without first asserting the set it
    /// filtered is the size it should be. This is the floor for both tables -- an order of
    /// magnitude below the ~470 and ~370 rows they carry, which is enough to catch a generator
    /// that emitted a handful of rows or none, and loose enough that deleting bad rows (which is
    /// wanted) never trips it.
    const MIN_EXPECTED_ROWS: usize = 100;

    /// A hook and a call on the SAME function must reach the same address.
    ///
    /// The two tables are generated from different files and nothing about their construction
    /// forces them to agree. If they ever disagree, a feature that both calls a function and
    /// detours it installs its hook at one address and invokes another -- the trampoline never
    /// fires, and the symptom is a silently inert feature rather than a crash, which is worse.
    #[test]
    fn every_detour_row_agrees_with_the_call_map() {
        assert_table_is_populated(&DETOUR_SAFE_1162_TO_1170, "DETOUR_SAFE_1162_TO_1170");
        let disagreements: Vec<(u32, u32, Option<u32>)> = DETOUR_SAFE_1162_TO_1170
            .iter()
            .map(|(from, moved)| {
                let called = VERIFIED_1162_TO_1170
                    .iter()
                    .find(|(other, _)| other == from)
                    .map(|(_, other_moved)| *other_moved);
                (*from, *moved, called)
            })
            .filter(|(_, moved, called)| *called != Some(*moved))
            .collect();
        assert!(
            disagreements.is_empty(),
            "detour rows the call map places elsewhere (from, detour, call): {disagreements:#x?}"
        );
    }

    /// One source, one answer -- on the detour table as on the call one.
    #[test]
    fn detour_map_has_one_answer_per_source() {
        assert_table_is_populated(&DETOUR_SAFE_1162_TO_1170, "DETOUR_SAFE_1162_TO_1170");
        let mut seen: Vec<u32> = DETOUR_SAFE_1162_TO_1170
            .iter()
            .map(|(from, _)| *from)
            .collect();
        seen.sort_unstable();
        let before = seen.len();
        seen.dedup();
        assert_eq!(before, seen.len(), "the detour table names a source twice");
    }

    /// THE RULE, on a table this test builds itself. The one test here that no ledger edit can
    /// make vacuous.
    ///
    /// `0xb` below is both the destination of the first row and the source of the second -- the
    /// exact shape of `0x7ac890 -> 0x7ad710 -> 0x7ae590`, which on 2026-08-30 returned a third,
    /// unrelated function with no error and no log line. If the shortcut answered first, `0xb`
    /// would be handed back as already-translated and the second row would never run.
    ///
    /// The real-table tests below are worth having, but their power to catch a loosened
    /// `already_translated_in` depends on a collision existing in the ledgers, and the three that
    /// exist today are slated for deletion. This one does not depend on the data at all.
    #[test]
    fn translation_wins_over_the_shortcut_on_a_collision() {
        const TABLE: [(u32, u32); 5] = [
            (0xa, 0xb),
            // The collision: `0xb` is a destination above and a source here.
            (0xb, 0xc),
            // A row that did not move, and that nothing else points at.
            (0x20, 0x20),
            // A row that did not move but IS another row's destination. Both answers are `0x30`,
            // so the shortcut claiming it is harmless -- the one case where it may.
            (0x30, 0x30),
            (0x31, 0x30),
        ];

        assert_eq!(
            table_answer(&TABLE, 0xa),
            TableAnswer::MovedTo(0xb),
            "a plain source must translate"
        );
        assert_eq!(
            table_answer(&TABLE, 0xb),
            TableAnswer::MovedTo(0xc),
            "an address that is BOTH a destination and a source must be answered as a source; \
             AlreadyTranslated here drops the second row silently, which is the whole hazard"
        );
        assert_eq!(
            table_answer(&TABLE, 0xc),
            TableAnswer::AlreadyTranslated,
            "a destination that nothing else sources is what the shortcut is FOR: a second \
             resolve of it must hand it back, not refuse it"
        );
        assert_eq!(
            table_answer(&TABLE, 0x20),
            TableAnswer::MovedTo(0x20),
            "a row that did not move still answers from the table"
        );
        assert_eq!(
            table_answer(&TABLE, 0x30),
            TableAnswer::AlreadyTranslated,
            "the shortcut may claim a source only when its answer is the row's own destination"
        );
        assert_eq!(
            table_answer(&TABLE, 0x31),
            TableAnswer::MovedTo(0x30),
            "a source whose destination did not move is still a source"
        );
        assert_eq!(
            table_answer(&TABLE, 0x99),
            TableAnswer::Unmapped,
            "an address no row names must be refused, not guessed at"
        );
    }

    /// Every row must resolve to its OWN destination -- on the call table.
    ///
    /// WHAT THIS REPLACED. `verified_map_is_idempotent` filtered to rows where `from != moved` and
    /// then asked `already_translated(from)`, whose second conjunct is
    /// `!is_source_of_a_move(rva)` -- false for every row the filter kept. The set it asserted
    /// over was empty by construction and the test could not fail. Its name promised more than it
    /// checked as well: resolution is NOT idempotent on this data, because
    /// `resolve(resolve(0x614870))` is `0x616510`, a different function.
    ///
    /// What is left is the property the old doc actually described: no row is swallowed by the
    /// shortcut. Every row is resolved through the same [`table_answer`] the resolvers call, and
    /// the answer is compared to that row's own destination -- no filter in front of it, so the
    /// number of rows checked is the number of rows there are.
    #[test]
    fn every_verified_row_resolves_to_its_own_destination() {
        assert_every_row_resolves_to_itself(&VERIFIED_1162_TO_1170, "VERIFIED_1162_TO_1170");
    }

    /// The same rule on the DETOUR table, which is generated from a different file and used by a
    /// different resolver. Replaces `detour_table_translation_wins_over_the_shortcut`, which
    /// carried the same contradiction between its filter and its predicate.
    #[test]
    fn every_detour_row_resolves_to_its_own_destination() {
        assert_every_row_resolves_to_itself(&DETOUR_SAFE_1162_TO_1170, "DETOUR_SAFE_1162_TO_1170");
    }

    /// EVERY destination that nothing else claims as a source is recognised, so a double resolve
    /// of one is handed back rather than refused -- the reason the shortcut exists at all.
    ///
    /// The predecessor took the FIRST such row with `find` and then wrapped its assertion in
    /// `if let Some(..)`, so a table with no pure destination -- or an empty one -- passed in
    /// silence. This walks all of them and says how many it expected.
    #[test]
    fn every_pure_destination_is_recognised_as_already_translated() {
        let pure: Vec<u32> = VERIFIED_1162_TO_1170
            .iter()
            .filter(|(from, moved)| from != moved)
            .map(|(_, moved)| *moved)
            .filter(|moved| {
                !VERIFIED_1162_TO_1170
                    .iter()
                    .any(|(other, other_moved)| other == moved && other != other_moved)
            })
            .collect();
        assert!(
            pure.len() >= MIN_EXPECTED_ROWS,
            "only {} of {} rows in VERIFIED_1162_TO_1170 have a destination nothing else sources; \
             under {MIN_EXPECTED_ROWS} means the table is not what this test thinks it is, and \
             the assertion below would be concluding from almost nothing",
            pure.len(),
            VERIFIED_1162_TO_1170.len()
        );
        let unrecognised: Vec<u32> = pure
            .iter()
            .copied()
            .filter(|moved| super::already_translated_in(&VERIFIED_1162_TO_1170, *moved))
            .count()
            .eq(&pure.len())
            .then(Vec::new)
            .unwrap_or_else(|| {
                pure.iter()
                    .copied()
                    .filter(|moved| !super::already_translated_in(&VERIFIED_1162_TO_1170, *moved))
                    .collect()
            });
        assert!(
            unrecognised.is_empty(),
            "these destinations are claimed by no other row's source and were still not \
             recognised as already-translated, so a second resolve of them REFUSES: {unrecognised:#x?}"
        );
    }

    /// The table has to be usable by the resolver's linear scans without a duplicate source
    /// silently shadowing a later row. Two sources that agree would be harmless; two that
    /// disagree would make the answer depend on row order.
    #[test]
    fn verified_map_has_one_answer_per_source() {
        assert_table_is_populated(&VERIFIED_1162_TO_1170, "VERIFIED_1162_TO_1170");
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

    /// A conclusion drawn from walking a table is worth exactly as much as the walk was long.
    fn assert_table_is_populated(table: &[(u32, u32)], name: &str) {
        assert!(
            table.len() >= MIN_EXPECTED_ROWS,
            "{name} holds {} rows. Everything asserted about it is a claim over that set, and \
             under {MIN_EXPECTED_ROWS} rows the generator produced almost nothing -- a green tick \
             here would mean the check ran over an empty table, not that the table is good",
            table.len()
        );
    }

    /// `resolve(from) == moved`, for every row, through the resolvers' own decision function.
    fn assert_every_row_resolves_to_itself(table: &[(u32, u32)], name: &str) {
        assert_table_is_populated(table, name);
        let wrong: Vec<(u32, u32, TableAnswer)> = table
            .iter()
            .map(|&(from, moved)| (from, moved, table_answer(table, from)))
            .filter(|&(from, moved, answer)| match answer {
                // The shortcut may claim a source only when the address it hands back IS what the
                // row would have returned. That is the rows that did not move, and only them.
                TableAnswer::AlreadyTranslated => from != moved,
                TableAnswer::MovedTo(to) => to != moved,
                TableAnswer::Unmapped => true,
            })
            .collect();
        assert!(
            wrong.is_empty(),
            "{name}: {} of {} rows do not resolve to their own destination. A row answered \
             AlreadyTranslated is one the shortcut swallowed -- its translation never runs and \
             nothing logs that it did not. (from, destination, answer): {wrong:#x?}",
            wrong.len(),
            table.len()
        );
    }
}
