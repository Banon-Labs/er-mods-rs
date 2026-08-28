//! The two 1.17 drifts that stop Seamless Co-op v1.9.9 from booting, and what each one needs.
//!
//! ersc uses its AOB signatures for two different jobs, and the difference decides the fix:
//!
//! * **A locator.** The match is only a landmark; what ersc keeps is something the matched
//!   bytes *point at*. [`allocator_locator`] is one: it reads the `call` operand to get the
//!   game's `DLAllocator` getter. A rebuilt copy of the shape elsewhere is as good as the
//!   original, so this is fixed with a decoy in a cave and no game code is touched.
//!
//! * **A hook target.** The match *is* the function ersc detours. [`scadutree_getter`] is one:
//!   Seamless overrides `GetScadutreeBlessing` so a guest reports the session's blessing level.
//!   A decoy would be catastrophic here in the quiet way -- ersc would hook a cave nothing
//!   calls, boot cleanly, and silently lose the feature. The real entry has to carry the bytes
//!   ersc expects, so the entry is re-shaped and the real body relocated.

use crate::cave::{CAVE_FILL, CaveAllocator, write_verified};
use crate::scan::{Match, pattern, scan};

/// Candidate sites recorded before a scan gives up. More than the expected count means the
/// image is not shaped the way a fixup's reasoning requires, and it refuses to act.
const MAX_HITS: usize = 8;
/// `jmp rel32`.
const OP_JMP_REL32: u8 = 0xE9;
/// Length of `jmp rel32`.
const JMP_LEN: usize = 5;

/// What a fixup did, for the log.
pub(crate) enum Outcome {
    /// The image already satisfies ersc; nothing was written.
    NotNeeded(String),
    Installed(String),
    Refused(String),
}

/// The rel32 that makes an instruction ending at `from + end` reach `target`.
fn rel32(from: usize, end: usize, target: usize) -> Option<i32> {
    i32::try_from((target as isize).checked_sub((from + end) as isize)?).ok()
}

/// Target of a RIP-relative operand at `bytes[rel_at..]` in an instruction ending at
/// `site + end`.
fn rip_target(site: usize, bytes: &[u8], rel_at: usize, end: usize) -> Option<usize> {
    let rel = i32::from_le_bytes([
        *bytes.get(rel_at)?,
        *bytes.get(rel_at + 1)?,
        *bytes.get(rel_at + 2)?,
        *bytes.get(rel_at + 3)?,
    ]);
    site.checked_add(end)?.checked_add_signed(rel as isize)
}

/// Reject a scan that did not find exactly one site, naming what it did find.
fn exactly_one(hits: Vec<Match>, what: &str) -> Result<Match, String> {
    match hits.len() {
        1 => Ok(hits.into_iter().next().expect("length checked")),
        0 => Err(format!(
            "no {what} in .text; this game build is shaped differently and the site cannot be \
             identified by inspection"
        )),
        n => Err(format!(
            "{n} candidate {what} sites, expected exactly 1; picking one would be a guess: {:x?}",
            hits.iter().map(|hit| hit.address).collect::<Vec<_>>()
        )),
    }
}

/// `E8 rel32 / 48 8B 15 rel32 / 48 8D 4B disp8` -- ersc's landmark for the allocator getter,
/// with the trailing displacement free because that is the byte 1.17 changed (`0x20`->`0x58`).
const ALLOCATOR_SHAPE: &str = "E8 ? ? ? ? 48 8B 15 ? ? ? ? 48 8D 4B ?";
/// The displacement ersc's literal ends with.
const ALLOCATOR_ERSC_DISP: u8 = 0x20;
/// Offsets inside that 16-byte shape.
const CALL_REL_AT: usize = 1;
const CALL_END: usize = 5;
const MOV_REL_AT: usize = 8;
const MOV_END: usize = 12;
const LEA_DISP_AT: usize = 15;
const ALLOCATOR_LEN: usize = 16;

/// Rebuild ersc's allocator landmark in a cave, with both RIP-relative operands re-based so
/// they resolve to the same targets the real (drifted) site resolves to.
pub(crate) fn allocator_locator(
    text_start: usize,
    text_len: usize,
    caves: &mut CaveAllocator,
) -> Outcome {
    let hits = scan(text_start, text_len, &pattern(ALLOCATOR_SHAPE), MAX_HITS);
    if let Some(ready) = hits
        .iter()
        .find(|hit| hit.bytes[LEA_DISP_AT] == ALLOCATOR_ERSC_DISP)
    {
        return Outcome::NotNeeded(format!(
            "allocator landmark: ersc's bytes already present at 0x{:x}",
            ready.address
        ));
    }
    let site = match exactly_one(hits, "allocator landmark") {
        Ok(site) => site,
        Err(why) => return Outcome::Refused(why),
    };
    let (Some(call_target), Some(global_target)) = (
        rip_target(site.address, &site.bytes, CALL_REL_AT, CALL_END),
        rip_target(site.address, &site.bytes, MOV_REL_AT, MOV_END),
    ) else {
        return Outcome::Refused(format!(
            "allocator landmark: operands at 0x{:x} do not resolve inside the address space",
            site.address
        ));
    };
    let Some(cave) = caves.alloc(ALLOCATOR_LEN) else {
        return Outcome::Refused("allocator landmark: no cave large enough".to_string());
    };
    let (Some(call_rel), Some(mov_rel)) = (
        rel32(cave, CALL_END, call_target),
        rel32(cave, MOV_END, global_target),
    ) else {
        return Outcome::Refused(format!(
            "allocator landmark: cave 0x{cave:x} is out of rel32 range of its targets"
        ));
    };
    let mut decoy = site.bytes.clone();
    decoy[CALL_REL_AT..CALL_END].copy_from_slice(&call_rel.to_le_bytes());
    decoy[MOV_REL_AT..MOV_END].copy_from_slice(&mov_rel.to_le_bytes());
    decoy[LEA_DISP_AT] = ALLOCATOR_ERSC_DISP;
    let expected = vec![CAVE_FILL; ALLOCATOR_LEN];
    match write_verified(cave, &decoy, Some(&expected)) {
        Ok(()) => Outcome::Installed(format!(
            "allocator landmark: site 0x{:x} (disp 0x{:02x}), allocator getter 0x{call_target:x}, \
             string global 0x{global_target:x} -> decoy at 0x{cave:x} {decoy:02x?}",
            site.address, site.bytes[LEA_DISP_AT]
        )),
        Err(why) => Outcome::Refused(format!("allocator landmark: {why}")),
    }
}

/// `GetScadutreeBlessing(PlayerGameData*)`: `cmp byte [rcx+flag],0 / je / movzx eax,[rcx+ovr] /
/// ret / movzx eax,[rcx+0xfc] / ret`. The two DLC-blessing field offsets are free because 1.17
/// moved them (`0xab5`/`0xab4` -> `0xabd`/`0xabc`); the shape around them is what identifies
/// the function.
const SCADUTREE_SHAPE: &str =
    "80 B9 ? ? 00 00 00 74 08 0F B6 81 ? ? 00 00 C3 0F B6 81 ? ? 00 00 C3";
/// Bytes ersc's literal requires at the function's entry (`cmp byte [rcx+0xab5], 0`).
///
// AOB signature: this is ersc's own search string, transcribed from the box it puts on
// screen ("No such pattern \"80 B9 B5 0A 00 00 00\"", signatures.cpp:1399), and its
// correctness test is byte-equality with THAT literal, not with any assembler's
// preferred encoding. `iced-x86` would be free to emit an equivalent-but-different
// encoding of `cmp byte ptr [rcx+0xab5], 0`, and an equivalent encoding is a silent
// failure here: ersc's scan would find nothing and abort exactly as it does unpatched.
// The bytes are also never executed for their meaning -- the flags this `cmp` sets are
// discarded by the unconditional `jmp` written directly after it.
const SCADUTREE_ERSC_ENTRY: [u8; 7] = [0x80, 0xB9, 0xB5, 0x0A, 0x00, 0x00, 0x00];
/// Length of the whole function, and therefore of the relocated copy.
const SCADUTREE_LEN: usize = 25;

/// Re-shape `GetScadutreeBlessing`'s entry to the bytes ersc's scan requires, and relocate the
/// real body so behaviour is unchanged.
///
/// The entry becomes ersc's `cmp` -- whose flags are then discarded -- followed by a `jmp` to a
/// faithful copy of the original function in a cave. That layout survives the detour ersc is
/// about to install: a trampoline that copies the leading `cmp` and returns to `entry+7` lands
/// on the `jmp` and reaches the real body. Bytes past the `jmp` are filled with `0xCC` so a
/// hook that assumes a longer prologue faults where it happens instead of quietly returning a
/// wrong blessing level.
pub(crate) fn scadutree_getter(
    text_start: usize,
    text_len: usize,
    caves: &mut CaveAllocator,
) -> Outcome {
    let hits = scan(text_start, text_len, &pattern(SCADUTREE_SHAPE), MAX_HITS);
    if let Some(ready) = hits
        .iter()
        .find(|hit| hit.bytes[..SCADUTREE_ERSC_ENTRY.len()] == SCADUTREE_ERSC_ENTRY)
    {
        return Outcome::NotNeeded(format!(
            "scadutree getter: ersc's bytes already present at 0x{:x}",
            ready.address
        ));
    }
    let site = match exactly_one(hits, "scadutree getter") {
        Ok(site) => site,
        Err(why) => return Outcome::Refused(why),
    };
    let Some(cave) = caves.alloc(SCADUTREE_LEN) else {
        return Outcome::Refused("scadutree getter: no cave large enough".to_string());
    };
    // The body is position-independent: two `movzx` on rcx, one short `je` internal to the
    // copy, two `ret`. Copying it verbatim is therefore correct with no fixups.
    if let Err(why) = write_verified(cave, &site.bytes, Some(&[CAVE_FILL; SCADUTREE_LEN])) {
        return Outcome::Refused(format!("scadutree getter: relocating the body: {why}"));
    }
    let Some(jmp_rel) = rel32(site.address + SCADUTREE_ERSC_ENTRY.len(), JMP_LEN, cave) else {
        return Outcome::Refused(format!(
            "scadutree getter: cave 0x{cave:x} is out of jmp range of 0x{:x}",
            site.address
        ));
    };
    let mut entry = Vec::with_capacity(SCADUTREE_LEN);
    entry.extend_from_slice(&SCADUTREE_ERSC_ENTRY);
    entry.push(OP_JMP_REL32);
    entry.extend_from_slice(&jmp_rel.to_le_bytes());
    entry.resize(SCADUTREE_LEN, CAVE_FILL);
    match write_verified(site.address, &entry, Some(&site.bytes)) {
        Ok(()) => Outcome::Installed(format!(
            "scadutree getter: entry 0x{:x} {:02x?} -> ersc-shaped entry + jmp to relocated body \
             at 0x{cave:x}",
            site.address, site.bytes
        )),
        Err(why) => Outcome::Refused(format!("scadutree getter: re-shaping the entry: {why}")),
    }
}
