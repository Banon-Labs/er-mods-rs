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
/// Only rows the verifier judged the SAME FUNCTION -- normalised instruction sequences identical
/// over at least this many instructions. `NEAR`, `DIVERGES` and `IDENTICAL-SHORT` rows are left
/// out on purpose: an address that merely looks similar is how a detour lands mid-function.
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
    let map_path = Path::new(root_dir).join(VERIFIED_MAP);
    println!("cargo:rerun-if-changed={}", map_path.display());
    let mut rows: Vec<(u32, u32)> = Vec::new();
    if let Ok(text) = std::fs::read_to_string(&map_path) {
        for line in text.lines() {
            if line.starts_with('#') || line.trim().is_empty() {
                continue;
            }
            let fields: Vec<&str> = line.split('\t').collect();
            if fields.len() < 5 || fields[2] != "IDENTICAL" {
                continue;
            }
            let compared: u32 = fields[4].trim().parse().unwrap_or(0);
            if compared < MIN_VERIFIED_INSNS {
                continue;
            }
            let parse = |s: &str| u64::from_str_radix(s.trim().trim_start_matches("0x"), 16).ok();
            if let (Some(old), Some(new)) = (parse(fields[0]), parse(fields[1])) {
                // Stored as RVAs: the table has to survive a relocated image base.
                rows.push(((old - 0x140000000) as u32, (new - 0x140000000) as u32));
            }
        }
    }
    // Everything gathered so far came from the byte-comparison verifier, and only those rows may
    // carry a DETOUR. See `emit_address_map`'s tail for why the distinction is load-bearing.
    let detour_safe: Vec<(u32, u32)> = rows.clone();
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
    let held_back = quarantined(root_dir);
    rows.retain(|(old, _)| !held_back.contains(old));
    rows.sort_unstable();
    rows.dedup_by_key(|(old, _)| *old);
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
         // DETOUR_SAFE_1162_TO_1170 is the subset that has also been shown to be a real function\n\
         // ENTRY with a relocatable five-byte prologue -- currently the byte-verified rows, which\n\
         // scripts/audit-1170-hook-targets.py checks. MEASURED on 2026-08-29: when the weaker rows\n\
         // were allowed to carry detours, er-armament-icons installed five of them and the game\n\
         // died ~2.0s in, at the first overlay draw. Before those rows existed the same hooks were\n\
         // refused and the game lived. Nothing about a signature match says MinHook may patch there.\n",
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
