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
