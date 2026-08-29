//! Bakes the commit this build came from into `er-game-base`, for `build_id`.
//!
//! # Why the git sha and not a version string
//!
//! Every crate in this workspace is `version = "0.1.0"`, so the manifest version identifies
//! nothing. The question a log has to answer is "which SOURCE produced the binary that wrote
//! this line", and the only honest answer is a commit id plus whether the tree was clean.
//!
//! # Why `dirty` is load-bearing
//!
//! A sha alone invites a false certainty: `git show <sha>` looks authoritative, but if the
//! build came from a tree with uncommitted edits, that diff is NOT what ran. `+dirty` is the
//! flag that stops someone reading the wrong source with total confidence.
//!
//! # The staleness this cannot fully close
//!
//! `rerun-if-changed` on `.git/HEAD`, the ref it names, and `.git/index` catches commits,
//! checkouts and `git add`. It does NOT catch a bare edit to a tracked file that is never
//! staged -- cargo has no reason to rebuild this crate for that, so the `+dirty` suffix can
//! be absent from a build whose tree had edits. That is why the runtime line pairs this with the DLL's
//! own PE timestamp, which is written by the linker on every relink and cannot go stale.

use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;

/// How much of the sha to keep. Twelve hex digits is unambiguous in a repo of this size and
/// still fits a log line beside everything else.
const SHA_LENGTH: &str = "--short=12";

/// Stand-in when the tree is not a git checkout (a source archive, a vendored copy). Spelled
/// out rather than left empty so a log line reads as "we looked and there was nothing" instead
/// of looking like a formatting bug.
const UNKNOWN: &str = "unknown";

fn main() {
    let repo = repo_root();
    emit_address_map(&std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets this"));
    declare_rerun(&repo);

    let sha = git(&repo, &["rev-parse", SHA_LENGTH, "HEAD"]).unwrap_or_else(|| UNKNOWN.to_string());
    // `--untracked-files=no`: a stray scratch file in the working directory says nothing about
    // whether the code that built is the code at the sha, and treating it as dirty would make
    // the flag fire so often it stops being read.
    let dirty = git(&repo, &["status", "--porcelain", "--untracked-files=no"])
        .map(|out| !out.trim().is_empty())
        .unwrap_or(false);

    // ONE pre-composed string, not a sha plus a flag the runtime re-joins.
    //
    // The first cut emitted them separately and let `build_id` append "+dirty" behind an
    // `if GIT_DIRTY == "true"`. Both sides of that comparison are compile-time constants, so
    // the optimiser resolves the branch and drops the untaken literal -- and the failure is
    // silent and one-directional: a build that folded the wrong way ships a sha with no
    // warning attached, which reads as a CLEAN build. Composing here means the binary either
    // contains the right string or contains nothing, with no branch in between to get this
    // wrong.
    let description = if dirty { format!("{sha}+dirty") } else { sha };
    println!("cargo::rustc-env=ER_BUILD_GIT={description}");
}

/// Workspace root: this crate lives at `<root>/crates/er-game-base`.
fn repo_root() -> PathBuf {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets this"));
    manifest
        .parent()
        .and_then(Path::parent)
        .map(PathBuf::from)
        .unwrap_or(manifest)
}

/// Rebuild when the commit, the checked-out branch, or the index moves.
///
/// `.git/HEAD` covers a checkout; the ref file it names covers a commit on that branch;
/// `.git/index` covers a `git add`, which is what flips `DIRTY` back to clean.
fn declare_rerun(repo: &Path) {
    let git_dir = repo.join(".git");
    for relative in ["HEAD", "index"] {
        let path = git_dir.join(relative);
        if path.exists() {
            println!("cargo::rerun-if-changed={}", path.display());
        }
    }
    // `HEAD` normally holds `ref: refs/heads/<branch>`; a detached HEAD holds a raw sha and
    // there is no second file to watch.
    if let Ok(head) = std::fs::read_to_string(git_dir.join("HEAD"))
        && let Some(reference) = head.trim().strip_prefix("ref: ")
    {
        let path = git_dir.join(reference);
        if path.exists() {
            println!("cargo::rerun-if-changed={}", path.display());
        }
    }
}

/// Run git in the repo, returning trimmed stdout only on a clean exit. Every failure mode --
/// git absent, not a checkout, a broken repository -- collapses to `None` so a build never
/// fails over provenance metadata.
fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .current_dir(repo)
        .args(args)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8(output.stdout).ok()?.trim().to_string())
}

/// Where the verified 1.16.2 -> 1.17 address pairs come from. Tracked, generated by
/// `scripts/verify-rva-map-1170.py`, and read here rather than transcribed: a hand-copied address
/// table is the exact failure this whole migration is about.
const VERIFIED_MAP: &str = "../../docs/recon/rva-map-1162-to-1170.verified.tsv";
/// How much of a body an `IDENTICAL` verdict must cover before it counts. `NEAR`, `DIVERGES` and
/// `IDENTICAL-SHORT` rows are left out on purpose: an address that merely looks similar is how a
/// detour lands mid-function.
///
/// It does not apply to `BYTE-IDENTICAL`, where the verifier compared the function's entire
/// `.pdata` extent and found it unchanged. That distinction matters: it is what stopped short
/// functions being refused for being short. `PROXY_IS_BOUND` (0x733150) is 21 bytes and identical
/// in 1.17, and counting its 7 instructions as thin evidence kept an er-armament-icons detour
/// refused on a function that had not changed at all.
const MIN_VERIFIED_INSNS: u32 = 12;
/// Mappings held back despite verifying, because the HANDLER at that address is what turned out
/// to be stale. See the file's own header for the distinction it exists to record.
const QUARANTINE: &str = "../../docs/recon/rva-1170-quarantine.tsv";
/// The SECOND, weaker source of pairs, selected by `scripts/select-needed-1170-rows.py` from a
/// whole-image alignment of both `.pdata` function tables.
///
/// # Why a weaker source is worth having
///
/// The verified map covers 27 addresses, all of them hook targets, because each row costs a
/// byte-by-byte instruction comparison. The calls are the larger exposure: 159 sites reach the
/// game through a raw `transmute(base + *_RVA)`, and an unmapped one is not a degraded feature
/// but a dead process -- `0x1405eefb0` on 1.17 is the second byte of a five-byte `call`, and the
/// `9a` it lands on is a far call, invalid in long mode.
///
/// # What it is allowed to decide
///
/// These pairs come from masked-signature identity, calibrated against 41 pairs derived without
/// it (62 of the bundle's 96 fields come from RTTI class names, which involve no pattern matching
/// at all) with zero disagreements. That is strong enough to CALL. It is not the same claim as
/// the verified map, so the verified map wins wherever both cover an address, and a detour still
/// owes `audit-1170-hook-targets.py` a check that the destination has room for the patch.
const FUNCTION_MAP: &str = "../../docs/recon/rva-map-1162-to-1170.needed.tsv";
/// The THIRD source: globals, vtables and tables, which the function map structurally cannot
/// cover because a datum has no content to compare -- at rest a global is eight zero bytes like
/// every other global.
///
/// They moved too, and not by a constant: most `.data` globals shifted +0x4070 between 2.6.2.0
/// and 2.7.0.0, `runtime_heap_allocator` +0x4080, `multiplay_properties` +0x4000, and
/// `cs_system_step` +0x4080 while its neighbours went +0x4070. Reading a stale one is quiet and
/// then fatal: `GLOBAL_TEX_REPOSITORY_RVA` went unread-and-unnoticed into `CreateTpfResCap`,
/// which divided by zero and took the game down 894ms after load -- with a correctly translated
/// function address one frame up, which is what made it look like the translation's fault.
///
/// Each row is carried by the code that references it, and only rows where independent
/// references agree are used; see the file's own header.
const DATA_MAP: &str = "../../docs/recon/rva-map-1162-to-1170.data.tsv";
/// Rows from the weaker maps that have ALSO passed the detour checks, and so may carry a hook.
///
/// A signature match says an address is the right one. It says nothing about whether MinHook may
/// write five bytes there. `scripts/audit-1170-hook-targets.py --promote` supplies the second
/// claim: the 1.17 destination is a real function entry by the image's own forward references,
/// and its opening five bytes relocate. 187 of 277 candidates pass; the 90 that do not stay out,
/// and their reasons are recorded in the file.
const AUDITED_DETOURS: &str = "../../docs/recon/rva-1170-detour-audited.tsv";
/// The needed map, put through the SAME byte comparison the verified map gets.
///
/// This is what makes a detour promotable. `FUNCTION_MAP` says where a signature re-occurs, which
/// is enough to call; this file says the normalised instruction sequences agree over the body AND
/// what each image's own `.pdata` declares about the two endpoints. Both claims are required
/// before a row reaches `DETOUR_SAFE_1162_TO_1170`.
///
/// MEASURED, and the reason the byte comparison is not optional: of the five detours
/// er-armament-icons installed when the game died at the first overlay draw on 2026-08-29, four
/// are the same function in 1.17 and one -- `HUD_WEAPON_SLOT_UPDATE`, 1.16.2 `0x8d2110` -- was
/// paired by signature with a function sharing 18% of its instruction shape. The entry-and-
/// prologue audit passed it. This comparison rejects it, and `scripts/verify-rva-map-1170.py
/// --selftest` asserts that separation so it cannot quietly regress.
const NEEDED_VERIFIED_MAP: &str = "../../docs/recon/rva-map-1162-to-1170.needed-verified.tsv";

/// Entry evidence a row must carry before it may hold a detour, from the verdict table's last
/// column.
///
/// `BOTH-ENTRIES` is the ordinary case: both images' `.pdata` declare a function starting there.
/// `NEITHER-ENTRY` is accepted too, and deliberately: `SCALEFORM_HANDLER_CTOR_RVA` (0x11a8870)
/// sits 0x20 bytes before the entry Ghidra names, in BOTH builds, and that symmetry is itself
/// evidence the pair is aligned. What is refused is an ASYMMETRIC verdict -- a source that is an
/// entry paired with a destination that is not, or the reverse -- because that is precisely the
/// shape of a mapping that landed mid-function.
const DETOURABLE_ENTRY_EVIDENCE: [&str; 2] = ["BOTH-ENTRIES", "NEITHER-ENTRY"];

/// 1.16.2 RVAs a verdict table says are paired with the WRONG 1.17 function.
///
/// This is not the same as an unverified row. The whole-image signature map carries hundreds of
/// pairs nobody has compared, and they stay -- an unexamined pair is the ordinary case and the
/// calls need the coverage. A `DIVERGES` verdict is different: the comparison ran and disagreed,
/// so the row is positive evidence of a wrong address, and a wrong address reached as a CALL is
/// how `0x1405eefb0` took a boot down. Those are dropped from the call map as well as the detour
/// one.
fn refuted_sources(path: &Path) -> Vec<u32> {
    println!("cargo:rerun-if-changed={}", path.display());
    let mut out = Vec::new();
    let Ok(text) = std::fs::read_to_string(path) else {
        return out;
    };
    for line in text.lines() {
        if line.starts_with('#') || line.trim().is_empty() {
            continue;
        }
        let fields: Vec<&str> = line.split('\t').collect();
        if fields.len() < 3 || fields[2] != "DIVERGES" {
            continue;
        }
        if let Ok(va) = u64::from_str_radix(fields[0].trim().trim_start_matches("0x"), 16) {
            out.push((va - 0x140000000) as u32);
        }
    }
    out
}

/// Pairs from a `verify-rva-map-1170.py` verdict table that are good enough to DETOUR.
fn detourable_pairs(path: &Path) -> Vec<(u32, u32)> {
    println!("cargo:rerun-if-changed={}", path.display());
    let mut rows = Vec::new();
    let Ok(text) = std::fs::read_to_string(path) else {
        return rows;
    };
    for line in text.lines() {
        if line.starts_with('#') || line.trim().is_empty() {
            continue;
        }
        let fields: Vec<&str> = line.split('\t').collect();
        // Seven columns: the entry evidence is the seventh, and a table written before it existed
        // is refused rather than assumed safe.
        if fields.len() < 7 {
            continue;
        }
        match fields[2] {
            // The whole function, byte for byte, in both images. There is no minimum to impose:
            // the comparison covered everything there was, and a 21-byte function proved that way
            // is proved harder than a 400-byte one sampled over 120 instructions.
            "BYTE-IDENTICAL" => {}
            // Normalised instruction sequences agree. How MUCH of the body they agree over is the
            // whole question, hence the floor.
            "IDENTICAL" => {
                if fields[4].trim().parse::<u32>().unwrap_or(0) < MIN_VERIFIED_INSNS {
                    continue;
                }
            }
            _ => continue,
        }
        if !DETOURABLE_ENTRY_EVIDENCE.contains(&fields[6].trim()) {
            continue;
        }
        let parse = |s: &str| u64::from_str_radix(s.trim().trim_start_matches("0x"), 16).ok();
        if let (Some(old), Some(new)) = (parse(fields[0]), parse(fields[1])) {
            // Stored as RVAs: the table has to survive a relocated image base.
            rows.push(((old - 0x140000000) as u32, (new - 0x140000000) as u32));
        }
    }
    rows
}

/// 1.16.2 RVAs excluded from translation.
fn quarantined(root_dir: &str) -> Vec<u32> {
    let path = Path::new(root_dir).join(QUARANTINE);
    println!("cargo:rerun-if-changed={}", path.display());
    let mut out = Vec::new();
    if let Ok(text) = std::fs::read_to_string(&path) {
        for line in text.lines() {
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            if let Some(rva) = line.split('\t').next()
                && let Ok(value) = u32::from_str_radix(rva.trim().trim_start_matches("0x"), 16)
            {
                out.push(value);
            }
        }
    }
    out
}

/// Emit the translation table `er-hook` consults when the running build is not 1.16.2.
fn emit_address_map(root_dir: &str) {
    let mut rows: Vec<(u32, u32)> = detourable_pairs(&Path::new(root_dir).join(VERIFIED_MAP));
    // Both verdict tables feed the DETOUR set. They differ only in which candidate map they were
    // run over -- the byte-search one and the whole-image one -- and a row from either has passed
    // the same two tests, so there is no reason to trust one and not the other.
    let mut detour_safe: Vec<(u32, u32)> = rows.clone();
    detour_safe.extend(detourable_pairs(
        &Path::new(root_dir).join(NEEDED_VERIFIED_MAP),
    ));
    let verified: Vec<u32> = rows.iter().map(|(old, _)| *old).collect();
    let function_map = Path::new(root_dir).join(FUNCTION_MAP);
    println!("cargo:rerun-if-changed={}", function_map.display());
    if let Ok(text) = std::fs::read_to_string(&function_map) {
        for line in text.lines() {
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            let fields: Vec<&str> = line.split('\t').collect();
            if fields.len() < 2 {
                continue;
            }
            let parse = |s: &str| u32::from_str_radix(s.trim().trim_start_matches("0x"), 16).ok();
            if let (Some(old), Some(new)) = (parse(fields[0]), parse(fields[1]))
                && !verified.contains(&old)
            {
                rows.push((old, new));
            }
        }
    }
    let data_map = Path::new(root_dir).join(DATA_MAP);
    println!("cargo:rerun-if-changed={}", data_map.display());
    if let Ok(text) = std::fs::read_to_string(&data_map) {
        let known: Vec<u32> = rows.iter().map(|(old, _)| *old).collect();
        for line in text.lines() {
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            let fields: Vec<&str> = line.split('\t').collect();
            if fields.len() < 2 {
                continue;
            }
            let parse = |s: &str| u32::from_str_radix(s.trim().trim_start_matches("0x"), 16).ok();
            if let (Some(old), Some(new)) = (parse(fields[0]), parse(fields[1]))
                && !known.contains(&old)
            {
                rows.push((old, new));
            }
        }
    }
    let mut held_back = quarantined(root_dir);
    // A comparison that ran and disagreed is evidence against the pair, wherever it would be used.
    held_back.extend(refuted_sources(
        &Path::new(root_dir).join(NEEDED_VERIFIED_MAP),
    ));
    held_back.extend(refuted_sources(&Path::new(root_dir).join(VERIFIED_MAP)));
    rows.retain(|(old, _)| !held_back.contains(old));
    rows.sort_unstable();
    rows.dedup_by_key(|(old, _)| *old);
    // STILL NOT WIRED IN, and now superseded rather than merely distrusted.
    //
    // `--promote` audits the 277 signature/reference-mapped rows and passes 187 on entry-and-
    // prologue grounds: each destination is a real function entry by the image's own forward
    // references. Feeding those into the detour table put the crash straight back. The reason is
    // visible in one row: `HUD_WEAPON_SLOT_UPDATE` (0x8d2110) passed that audit and is paired
    // with a 1.17 function sharing 18% of its instruction shape -- a safe place to land inside
    // the wrong function.
    //
    // What replaced it answers both questions with better evidence for each. Semantic identity
    // comes from the byte comparison in NEEDED_VERIFIED_MAP, which rejects that row. Entry
    // evidence comes from the same file's last column, read out of each image's `.pdata` -- the
    // linker's own record of where functions begin, rather than a count of forward references.
    //
    // The audited file stays only as a reading aid for the rows that remain refused.
    let _ = AUDITED_DETOURS;
    let mut detour_rows = detour_safe;
    detour_rows.retain(|(old, _)| !held_back.contains(old));
    detour_rows.sort_unstable();
    detour_rows.dedup_by_key(|(old, _)| *old);

    let mut out = String::from(
        "// GENERATED by er-game-base/build.rs. Do not edit by hand.\n\
         //\n\
         // TWO tables, because being the right address is not the same claim as being a safe\n\
         // place to write five bytes.\n\
         //\n\
         // VERIFIED_1162_TO_1170 is every pair from all three sources: byte-verified, whole-image\n\
         // signature pairing, and code-reference carrying. Good enough to CALL or to READ.\n\
         //\n\
         // DETOUR_SAFE_1162_TO_1170 is the subset carrying BOTH claims a detour needs: the\n\
         // normalised instruction sequences agree over the body (same function), and each image\'s\n\
         // own .pdata agrees about where the two endpoints sit relative to a function start\n\
         // (somewhere MinHook may write). MEASURED on 2026-08-29: when rows holding only the\n\
         // second claim were allowed to carry detours, er-armament-icons installed five of them\n\
         // and the game died ~2.0s in, at the first overlay draw -- one of the five is paired with\n\
         // a function sharing 18% of its instruction shape. Nothing about a signature match, or\n\
         // about a safe place to land, says the code there does the same job.\n",
    );
    out.push_str(&format!(
        "pub(crate) const VERIFIED_1162_TO_1170: [(u32, u32); {}] = [\n",
        rows.len()
    ));
    for (old, new) in &rows {
        out.push_str(&format!("    ({old:#x}, {new:#x}),\n"));
    }
    out.push_str("];\n");
    out.push_str(&format!(
        // Only the Windows resolver reads it; a host build still compiles the file.
        "#[allow(dead_code)]\npub(crate) const DETOUR_SAFE_1162_TO_1170: [(u32, u32); {}] = [\n",
        detour_rows.len()
    ));
    for (old, new) in &detour_rows {
        out.push_str(&format!("    ({old:#x}, {new:#x}),\n"));
    }
    out.push_str("];\n");
    let out_dir = env::var("OUT_DIR").unwrap();
    std::fs::write(Path::new(&out_dir).join("address_map_1170.rs"), out).unwrap();
}
