//! Bakes the commit this build came from into `er-game-base`, for `build_id`.
//!
//! # Why the git sha and not a version string
//!
//! Every crate in this workspace is `version = "0.1.0"`, so the manifest version identifies
//! nothing. The question a log has to answer is "which source produced the binary that wrote
//! this line", and the only honest answer is a commit id plus whether the tree was clean.
//!
//! # Why `dirty` is load-bearing
//!
//! A sha alone invites a false certainty: `git show <sha>` looks authoritative, but if the
//! build came from a tree with uncommitted edits, that diff is not what ran. `+dirty` is the
//! flag that stops someone reading the wrong source with total confidence.
//!
//! # The staleness this cannot fully close
//!
//! `rerun-if-changed` on the git directory's `HEAD`, the ref it names, and its `index` catches
//! commits, checkouts and `git add`. It does not catch a bare edit to a tracked file that is never
//! staged -- cargo has no reason to rebuild this crate for that, so the `+dirty` suffix can
//! be absent from a build whose tree had edits. That is why the runtime line pairs this with the DLL's
//! own PE timestamp, which is written by the linker on every relink and cannot go stale.
//!
//! # Worktrees, where this silently baked a frozen commit
//!
//! In a linked worktree `.git` is a file holding `gitdir: <path>`, not a directory. Joining
//! `HEAD` and `index` onto it produced paths that cannot exist, every `exists()` was false, and
//! the script emitted no `rerun-if-changed` at all -- which cargo reads as "this build script
//! depends on nothing", so it never ran it again. The baked sha then froze at whatever `HEAD`
//! was the first time that target directory was populated, and every later build in that
//! worktree stamped a commit it was not built from.
//!
//! That is worse than no provenance, because the line still looks authoritative. It cost a push
//! on 2026-09-11: the evidence gate correctly refused a commit whose freshly-built DLL named a
//! commit from two days earlier. Agent work in this repo happens in worktrees, so the broken
//! case was the common one. The directory is now resolved by asking git, which answers for a
//! worktree and a plain checkout alike.

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

    // One pre-composed string, not a sha plus a flag the runtime re-joins.
    //
    // The first cut emitted them separately and let `build_id` append "+dirty" behind an
    // `if GIT_DIRTY == "true"`. Both sides of that comparison are compile-time constants, so
    // the optimiser resolves the branch and drops the untaken literal -- and the failure is
    // silent and one-directional: a build that folded the wrong way ships a sha with no
    // warning attached, which reads as a clean build. Composing here means the binary either
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
/// `HEAD` covers a checkout; the ref file it names covers a commit on that branch; `index`
/// covers a `git add`, which is what flips `+dirty` back to clean.
///
/// Both directories are asked for by name rather than assembled from `repo`. A linked worktree
/// has two: its own, holding that worktree's `HEAD` and `index`, and the common one, holding the
/// refs every worktree shares. Watching `HEAD` in the common directory would track the main
/// checkout's branch instead of this one's, and looking for the ref in the worktree directory
/// would find nothing.
fn declare_rerun(repo: &Path) {
    let Some(git_dir) = git(repo, &["rev-parse", "--absolute-git-dir"]).map(PathBuf::from) else {
        // Not a checkout at all, which `main` already handles by baking `unknown`. Nothing to
        // watch, and nothing to warn about.
        return;
    };
    let common_dir = git(
        repo,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )
    .map(PathBuf::from)
    .unwrap_or_else(|| git_dir.clone());
    for path in [git_dir.join("HEAD"), git_dir.join("index")] {
        if path.exists() {
            println!("cargo::rerun-if-changed={}", path.display());
        }
    }
    // `HEAD` normally holds `ref: refs/heads/<branch>`; a detached head holds a raw sha and
    // there is no second file to watch. The ref itself lives in the common directory, and may
    // be packed rather than loose -- in which case `packed-refs` is the file that moves.
    if let Ok(head) = std::fs::read_to_string(git_dir.join("HEAD"))
        && let Some(reference) = head.trim().strip_prefix("ref: ")
    {
        let loose = common_dir.join(reference);
        if loose.exists() {
            println!("cargo::rerun-if-changed={}", loose.display());
        } else {
            let packed = common_dir.join("packed-refs");
            if packed.exists() {
                println!("cargo::rerun-if-changed={}", packed.display());
            }
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
