#!/usr/bin/env python3
"""Which run, if any, proves that the code at a commit actually executed.

One implementation, three readers
---------------------------------
`.cupcake/signals/runtime_evidence_for_head.sh` (the verdict a `git push` is gated on),
`.cupcake/signals/runtime_evidence_note.sh` (the sentence the denial quotes) and
`scripts/check-runtime-evidence.sh` (the pre-push hook) all ask the same question. They used
to answer it with three separate copies of the same scan, and copies drift: the note said one
log was to blame while the verdict was computed from another, which is how a refusal sent an
agent hunting a log that had nothing to do with it.

Where a run leaves its evidence
-------------------------------
Every shell in this workspace opens its own log with one identity line:

    build git=e0e3a2e11e80+dirty module=er_quit_menu.dll base=0x6ffff9de0000 pe=0x6aa4c5a9 (...)

Two directories hold those logs and both count:

  * the run root, `~/.cache/er-me3-runs/br-*/`, which only `scripts/er-run-branch.py` writes;
  * the `ELDEN RING/Game` directory, where a `~/Elden/launch.sh` run writes instead -- the way
    the user launches. Scanning only the first is why a push proven by a live run was refused
    on 2026-09-11: the evidence existed, in the other directory, unread.

The sha on that line, never a file timestamp, is what ties a run to code. An earlier version
compared mtimes and called a log written by a two-commit-old build "evidence", because the
process was still running and still writing.

When `+dirty` is still evidence
-------------------------------
`+dirty` says the tree carried uncommitted changes when that shell was built. That does not by
itself mean the shell contains any of them: this workspace routinely has several agents editing
unrelated crates, so a tree is nearly always dirty somewhere, and refusing every such log makes
the guard unpassable for a change that did run.

A dirty log is accepted only when four separate facts line up, each of which refuses on its own:

  1. the log names the commit being pushed -- so nothing has been committed since that build;
  2. the artifact on disk carries the same `PE` timestamp the running module reported, which is
     the link identity of that exact binary;
  3. `scripts/er-dll-provenance.py verify` passes, so the compiled dependency closure recorded
     at build time is still byte-identical to the working tree;
  4. `git status` reports nothing uncommitted anywhere in that closure, so the working tree
     copy of it is the committed copy.

Together those say: the binary that ran was compiled from source identical to what the commit
contains. A fifth requirement keeps the accept honest about scope -- every crate the push
changes must be inside the closure of some accepted dirty log, so the evidence covers the code
being pushed rather than merely coexisting with it.

When a log naming another commit is still evidence
--------------------------------------------------
The repo's rule is to commit after a run shows a change is worth keeping. That run's shells are
built from the parent plus the uncommitted change, so every one of them says
`build git=<parent>+dirty`, and the commit made afterwards is never the sha on any log. Measured
2026-10-15: a push of `dfc0a9f9` was refused because the newest log said `72ef6a9b+dirty`,
although `er-dll-provenance.py verify` reported the shells that ran fresh at `dfc0a9f9`. Read by
sha alone, the rule demanded a second launch for every commit.

So a log is also accepted when the binary that wrote it was built from exactly the source the
pushed commit holds:

  1. an artifact whose `PE` timestamp equals the log's is found in the pushed checkout's
     `target/` (or this checkout's), so it is the binary that ran;
  2. the provenance record beside it describes that file -- its `artifact_sha256` is the file's;
  3. the record's `source_sha` equals the same hash computed over the files the commit itself
     holds for the record's closure (`git ls-tree` and `git cat-file`, not the working tree).

A build that compiled anything the commit does not contain -- an untracked file, an edit made
after the commit or left out of it -- hashes differently and is refused. The scope requirement
above applies here too.

Usage:
    python3 scripts/er-runtime-evidence.py --head <sha> [--repo <checkout>] [--exhaustive] < changed-paths
    python3 scripts/er-runtime-evidence.py --selftest

Reads the pushed diff's paths on stdin, one per line; without them no dirty log can be
accepted, because requirement five cannot be checked.

Writes to stdout, in this order:

    note <one sentence naming what ran, for the human message>
    match <where> <sha>                 only when a run proves the commit
    candidate <sha> <where>             clean builds the caller may carry forward from

Exit status: 0 a run proves it, 1 none does, 2 neither log directory exists (unmeasurable,
which callers treat as "allow" -- a check that cannot see must not invent a verdict).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import struct
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The checkout being pushed, when it is not this one. `--repo` sets it; the signals pass the
# working tree `scripts/cupcake_push_target_repo.py` resolved, because a worktree builds into its
# own `target/` and that is where the binary that ran is.
PUSH_REPO: list[pathlib.Path] = [REPO_ROOT]

BUILD_LINE = re.compile(
    r"^build git=(?P<sha>[0-9a-f]+)(?P<dirty>\+dirty)?"
    r"(?:\s+module=(?P<module>\S+))?"
    r"(?:[^\n]*?\bpe=0x(?P<pe>[0-9a-fA-F]+))?"
)

EXIT_MATCH = 0
EXIT_NO_MATCH = 1
EXIT_UNMEASURABLE = 2

# How many distinct clean build shas to collect before giving up on the run root. The caller
# turns each one into a reverse-dependency walk, so an uncapped list costs it minutes; and an
# older sha reaches the tip across strictly more commits, so if the newest cannot carry the
# diff forward, an older one cannot either.
CANDIDATES = 2

GIT_TIMEOUT_SECONDS = 20


_LOADED: dict[str, object] = {}


def load_script(name: str, filename: str):
    """Import a sibling script under a dashed file name, once per process.

    Registered in `sys.modules` before it executes, because `@dataclass` resolves its own
    module out of that table: `scripts/er_run_lib.py` raises `AttributeError` on `NoneType`
    partway through import without it, which a caller then reads as "the game directory does
    not exist" rather than "the module failed to load".
    """
    if name in _LOADED:
        return _LOADED[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    _LOADED[name] = module
    return module


class Entry:
    """One log's identity line, with where it was found."""

    def __init__(self, path: pathlib.Path, where: str, found: re.Match, mtime: int) -> None:
        self.path = path
        self.where = where
        self.sha = found.group("sha")
        self.dirty = bool(found.group("dirty"))
        self.module = found.group("module") or ""
        self.pe = int(found.group("pe"), 16) if found.group("pe") else None
        self.mtime = mtime

    def names(self, sha: str) -> bool:
        return self.sha.startswith(sha) or sha.startswith(self.sha)

    def described(self) -> str:
        if self.module and not self.module.startswith("<"):
            return f"{self.path.name} ({self.module})"
        return self.path.name


# ------------------------------------------------------------------------------------------
# Where the logs are
# ------------------------------------------------------------------------------------------


def run_root() -> pathlib.Path:
    """The branch-launch run root. `ER_ME3_RUN_ROOT` overrides it, as in every caller."""
    default = pathlib.Path.home() / ".cache" / "er-me3-runs"
    return pathlib.Path(os.environ.get("ER_ME3_RUN_ROOT", default))


def game_dir() -> pathlib.Path | None:
    """The `ELDEN RING/Game` directory, where a launcher run's DLLs write their logs.

    `ER_GAME_DIR` wins, as in the other tools that read game-directory artifacts; otherwise the
    path comes from `er_run_lib.game_dir`, the one owner of that layout in this repo, so the
    `ME3_STEAM_DIR` override and the default Steam root are not spelled a second time here.
    """
    explicit = os.environ.get("ER_GAME_DIR")
    if explicit:
        return pathlib.Path(explicit)
    try:
        return pathlib.Path(load_script("er_run_lib", "er_run_lib.py").game_dir())
    except Exception:  # noqa: BLE001 -- a signal that cannot resolve a path must not crash
        return None


def read_identity(path: pathlib.Path, where: str) -> Entry | None:
    try:
        mtime = int(path.stat().st_mtime)
        with path.open(encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
    except OSError:
        return None
    found = BUILD_LINE.match(first)
    if not found:
        return None
    return Entry(path, where, found, mtime)


def collect(head: str, exhaustive: bool) -> tuple[list[Entry], list[str]]:
    """Every identity line worth reading, newest source first, plus the roots that existed.

    The run root is walked newest directory first and stops once enough distinct clean shas
    have been seen, because it holds hundreds of runs and this is read on every Bash tool call.
    The game directory is a single slot -- one log per shell, overwritten each launch -- so it
    is always read whole, which also means a match there can never be missed by the cap.
    """
    entries: list[Entry] = []
    roots: list[str] = []

    runs = run_root()
    if runs.is_dir():
        roots.append(str(runs))
        distinct: set[str] = set()
        try:
            directories = sorted((d for d in runs.iterdir() if d.is_dir()), reverse=True)
        except OSError:
            directories = []
        for run in directories:
            if not exhaustive and len(distinct) >= CANDIDATES:
                break
            for artifact in sorted(run.glob("er-*.log")):
                entry = read_identity(artifact, f"{run.name}/{artifact.name}")
                if entry is None:
                    continue
                entries.append(entry)
                if not entry.dirty:
                    distinct.add(entry.sha)

    game = game_dir()
    if game is not None and game.is_dir():
        roots.append(str(game))
        for artifact in sorted(game.glob("er-*.log")):
            entry = read_identity(artifact, f"game-dir/{artifact.name}")
            if entry is not None:
                entries.append(entry)

    entries.sort(key=lambda entry: entry.mtime, reverse=True)
    return entries, roots


# ------------------------------------------------------------------------------------------
# The dirty-log proof
# ------------------------------------------------------------------------------------------


def git(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def artifact_dir() -> pathlib.Path:
    """Where a build leaves its DLLs, matching `scripts/er-build-dlls.sh`."""
    target = os.environ.get("ER_BUILD_TARGET", "x86_64-pc-windows-msvc")
    return REPO_ROOT / "target" / target / "release"


def pe_timestamp(artifact: pathlib.Path) -> int | None:
    """The `COFF TimeDateStamp` of a `PE` file: its link identity, four bytes after the signature.

    This is the value a loaded module reports on its own identity line, so comparing the two is
    what ties the binary on disk to the binary that ran.
    """
    try:
        with artifact.open("rb") as handle:
            head = handle.read(0x400)
    except OSError:
        return None
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    offset = struct.unpack_from("<I", head, 0x3C)[0]
    if offset + 12 > len(head) or head[offset : offset + 4] != b"PE\0\0":
        return None
    return struct.unpack_from("<I", head, offset + 8)[0]


def package_for(module: str) -> str | None:
    """The cargo package that produces `module`, from the shipped-shell list.

    Four crates override `[lib] name`, so swapping dashes for underscores is not the map.
    """
    try:
        pairs = load_script("me3_dll_list", "me3-dll-list.py").dll_pairs()
    except Exception:  # noqa: BLE001 -- an unreadable list means "cannot prove", not a crash
        return None
    stem = module[:-4] if module.endswith(".dll") else module
    for package, artifact in pairs:
        if artifact == stem:
            return package
    return None


def closure_members(package: str) -> list[str] | None:
    """The in-repo crates whose source compiles into `package`."""
    try:
        provenance = load_script("er_dll_provenance", "er-dll-provenance.py")
        members, _external = provenance.forward_closure(package)
    except Exception:  # noqa: BLE001
        return None
    return members


def provenance_is_fresh(package: str, artifact: pathlib.Path) -> tuple[bool, str]:
    """Does the record beside `artifact` still describe this source tree?"""
    try:
        provenance = load_script("er_dll_provenance", "er-dll-provenance.py")
        code, failures = provenance.verify(package, artifact)
    except Exception as err:  # noqa: BLE001
        return False, f"its provenance record could not be read ({err})"
    if code == 0 and not failures:
        return True, ""
    return False, failures[0] if failures else "its provenance record does not match"


_DIRTY_CRATES: list[set[str] | None] = []


def dirty_crate_names() -> set[str] | None:
    """Crates carrying anything uncommitted: modified, staged, or untracked and not ignored.

    Read with `-z` so a path needing quotes cannot be mis-split, and matched by pattern rather
    than by field position so a rename's second record counts too. Over-collecting here only
    ever refuses more. Read once per process, because one scan asks it per candidate log and a
    run writes 26 of them.
    """
    if _DIRTY_CRATES:
        return _DIRTY_CRATES[0]
    out = git("status", "--porcelain", "-z", "--", "crates")
    if out is None:
        answer = None
    else:
        answer = {m.group(1) for m in re.finditer(r"crates/([A-Za-z0-9_.+-]+)/", out)}
    _DIRTY_CRATES.append(answer)
    return answer


def shell_compiled_crates() -> set[str] | None:
    """Every in-repo crate that some shipped shell compiles, or None when that cannot be read.

    A crate outside all of them (a host tool, an asset library such as `er-flver`, whose only
    users are `er-objectkit` and a dev-dependency) never reaches the game, so no run can prove
    a change to it. Measured 2026-10-22: a push carrying clippy fixes in `er-flver`'s examples was
    refused after a live run of the product at that commit, because `er-flver` "is not compiled
    into any shell that ran" -- and it is compiled into no shell at all.
    """
    try:
        pairs = load_script("me3_dll_list", "me3-dll-list.py").dll_pairs()
    except Exception:  # noqa: BLE001 -- an unreadable list means "cannot narrow", not a crash
        return None
    found: set[str] = set()
    for package, _artifact in pairs:
        members = closure_members(package)
        if members is None:
            return None
        found.update(members)
    return found


def crates_in(paths: list[str]) -> set[str]:
    """The crate directories a list of repo-relative paths touches."""
    found = set()
    for path in paths:
        match = re.match(r"crates/([^/]+)/", path)
        if match:
            found.add(match.group(1))
    return found


def dirty_build_is_committed(entry: Entry) -> tuple[list[str] | None, str]:
    """Prove the binary that wrote this dirty log was compiled from committed source.

    Returns `(closure, "")` when it holds and `(None, reason)` when it does not. The reason is
    written to be read by whoever hit the refusal, so it names the artifact and the crate.
    """
    if not entry.module or entry.module.startswith("<"):
        return None, "its log names no module, so the artifact it came from cannot be found"
    if entry.pe is None:
        return None, (
            "its log carries no pe stamp, so the binary it came from cannot be tied to it"
        )

    package = package_for(entry.module)
    if package is None:
        return None, f"{entry.module} is not a shell this workspace builds"

    artifact = artifact_dir() / entry.module
    if not artifact.is_file():
        where = artifact
        try:
            where = artifact.relative_to(REPO_ROOT)
        except ValueError:
            pass
        return None, f"nothing is built at {where}"

    on_disk = pe_timestamp(artifact)
    if on_disk is None:
        return None, f"{entry.module} on disk could not be read as a pe file"
    if on_disk != entry.pe:
        return None, (
            f"{entry.module} has been relinked since that run "
            f"(pe 0x{on_disk:08x} on disk, 0x{entry.pe:08x} in the log)"
        )

    members = closure_members(package)
    if members is None:
        return None, f"the dependency closure of {package} could not be resolved"

    dirty = dirty_crate_names()
    if dirty is None:
        return None, "git could not report what is uncommitted"
    overlap = sorted(dirty.intersection(members))
    if overlap:
        return None, (
            f"{overlap[0]} is uncommitted and compiles into {entry.module}, "
            "so that binary is not this commit"
        )

    # Last, because it is the only expensive step: it hashes every file in the closure. The
    # cheap facts above reject nearly everything first, which keeps this signal's cost where its
    # header promises when a run is being re-read on every Bash tool call.
    fresh, why = provenance_is_fresh(package, artifact)
    if not fresh:
        return None, f"{why.rstrip('.')} -- rebuild and rerun"

    return members, ""


# ------------------------------------------------------------------------------------------
# The provenance proof: a log naming another commit whose binary holds this commit's source
# ------------------------------------------------------------------------------------------


def git_in(repo: pathlib.Path, *args: str, stdin: bytes | None = None) -> bytes | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def artifact_candidates(module: str) -> list[pathlib.Path]:
    """Where the binary that wrote a log may sit: the pushed checkout's build, then this one's."""
    target = os.environ.get("ER_BUILD_TARGET", "x86_64-pc-windows-msvc")
    found: list[pathlib.Path] = []
    for root in (PUSH_REPO[0], REPO_ROOT):
        candidate = root / "target" / target / "release" / module
        if candidate not in found:
            found.append(candidate)
    return found


def eligible_source(path: str) -> bool:
    """`er-dll-provenance.py`'s own filter, so the two hashes are over the same files."""
    try:
        provenance = load_script("er_dll_provenance", "er-dll-provenance.py")
        return provenance.provenance_eligible_source(path)
    except Exception:  # noqa: BLE001 -- without the filter the hashes cannot be compared
        return False


_TREE_HASHES: dict[tuple[str, tuple[str, ...]], str | None] = {}


def tree_source_sha(commit: str, members: list[str]) -> str | None:
    """`er-dll-provenance.py`'s `source_sha`, computed over the files `commit` holds.

    That hash is taken from a working tree at build time: each file's repo-relative path, a NUL
    and the sha256 of its bytes, in the order `sorted()` puts the file `Path`s in. This reads the
    same files out of the commit instead, so equal hashes mean the build compiled exactly what
    the commit contains. A file the build compiled that the commit lacks -- untracked, or edited
    after -- makes them differ.
    """
    key = (commit, tuple(members))
    if key in _TREE_HASHES:
        return _TREE_HASHES[key]
    answer = None
    repo = PUSH_REPO[0]
    listing = git_in(
        repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", *[f"crates/{m}" for m in members]
    )
    if listing is not None:
        blobs: dict[str, str] = {}
        for record in listing.decode("utf-8", "surrogateescape").split("\0"):
            if not record:
                continue
            meta, _, path = record.partition("\t")
            fields = meta.split()
            if len(fields) == 3 and fields[1] == "blob" and eligible_source(path):
                blobs[path] = fields[2]
        answer = hash_blobs(repo, blobs)
    _TREE_HASHES[key] = answer
    return answer


def hash_blobs(repo: pathlib.Path, blobs: dict[str, str]) -> str | None:
    """The provenance digest over `{path: blob id}`, reading every blob in one `git` process."""
    ordered = sorted(REPO_ROOT / path for path in blobs)
    names = [path.relative_to(REPO_ROOT).as_posix() for path in ordered]
    request = "".join(f"{blobs[name]}\n" for name in names).encode()
    out = git_in(repo, "cat-file", "--batch", stdin=request)
    if out is None:
        return None
    digest = hashlib.sha256()
    cursor = 0
    for name in names:
        header_end = out.find(b"\n", cursor)
        if header_end < 0:
            return None
        header = out[cursor:header_end].split()
        if len(header) != 3 or header[1] != b"blob":
            return None
        size = int(header[2])
        body = out[header_end + 1 : header_end + 1 + size]
        cursor = header_end + 1 + size + 1
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def built_from_head_source(
    entry: Entry, head: str, wanted: set[str]
) -> tuple[list[str] | None, str]:
    """Prove the binary that wrote this log was built from exactly the source `head` holds.

    Returns `(closure, "")` when it holds and `(None, reason)` when it does not. The cheap facts
    come first -- this runs inside a signal on every Bash call -- and the two hashes last.
    """
    if not entry.module or entry.module.startswith("<") or entry.pe is None:
        return None, "its log names no module or carries no pe stamp"

    # The record that describes the binary that ran. One that carries the run's own pe stamp says
    # so by itself, whatever has been linked over the file since. An older record carries no stamp,
    # and is tied to the run through the file beside it instead, which must still carry the stamp
    # and still be the file the record hashed.
    artifact = None
    record: dict | None = None
    stamped = False
    for candidate in artifact_candidates(entry.module):
        found = read_record(candidate)
        if found is not None and found.get("pe_timestamp") == entry.pe:
            artifact, record, stamped = candidate, found, True
            break
        if (
            artifact is None
            and found is not None
            and found.get("pe_timestamp") is None
            and candidate.is_file()
            and pe_timestamp(candidate) == entry.pe
        ):
            artifact, record = candidate, found
    if artifact is None or record is None:
        return None, f"no build of {entry.module} with a provenance record carries the pe stamp that run reported"
    # The package is the record's own, not this checkout's shell list: a worktree being pushed can
    # carry a shell this checkout has never heard of. Measured 2026-10-15 on `er_render_scale.dll`.
    # The record still has to name a package the module belongs to and a closure that contains it.
    package = record.get("package")
    members = record.get("closure")
    if (
        not isinstance(package, str)
        or not isinstance(members, list)
        or package not in members
        or record.get("artifact", entry.module) != entry.module
        or not isinstance(record.get("source_sha"), str)
    ):
        return None, f"the provenance record beside {entry.module} does not describe it"
    if wanted and not wanted.intersection(members):
        return None, f"{entry.module} compiles none of the crates this push changes"

    cache = proof_cache(head, artifact)
    if cache is not None:
        try:
            remembered = json.loads(cache.read_text(encoding="utf-8"))
            if remembered.get("ok"):
                return members, ""
            return None, str(remembered.get("why", "it did not prove out"))
        except (OSError, ValueError):
            pass
    why = artifact_matches_head(entry, head, artifact, record, members, package, stamped)
    if cache is not None:
        try:
            cache.write_text(json.dumps({"ok": not why, "why": why}), encoding="utf-8")
        except OSError:
            pass
    return (None, why) if why else (members, "")


def read_record(artifact: pathlib.Path) -> dict | None:
    try:
        found = json.loads(
            artifact.with_name(artifact.name + ".provenance.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def artifact_matches_head(
    entry: Entry,
    head: str,
    artifact: pathlib.Path,
    record: dict,
    members: list[str],
    package: str,
    stamped: bool,
) -> str:
    """The hashes. Empty when the record describes the run's binary and `head`'s source."""
    if not stamped:
        try:
            on_disk = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError:
            return f"{entry.module} could not be read"
        if record.get("artifact_sha256") != on_disk:
            return f"{entry.module} is not the file its provenance record describes"
    at_head = tree_source_sha(head, members)
    if at_head is None:
        return f"the source {head} holds for {package} could not be read"
    if record["source_sha"] != at_head:
        return (
            f"{entry.module} was built from source that differs from what {head} holds "
            f"(recorded {record['source_sha'][:12]}, the commit hashes to {at_head[:12]})"
        )
    return ""


def proof_cache(head: str, artifact: pathlib.Path) -> pathlib.Path | None:
    """Where one artifact's answer for one commit is kept between Bash calls.

    Keyed by the commit, the artifact's path, size and mtime, and its record's mtime, so a
    rebuild or a rewritten record is a new key rather than a stale answer.
    """
    record = artifact.with_name(artifact.name + ".provenance.json")
    try:
        stat, record_stat = artifact.stat(), record.stat()
    except OSError:
        return None
    key = "|".join(
        [head, str(artifact), str(stat.st_size), str(stat.st_mtime_ns), str(record_stat.st_mtime_ns)]
    )
    directory = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "er-mods-rs-evidence"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return directory / f"provenance-{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"


# ------------------------------------------------------------------------------------------
# The scan
# ------------------------------------------------------------------------------------------


def scan(head: str, changed: list[str], exhaustive: bool) -> tuple[int, list[str]]:
    """Answer for `head`. Returns (exit status, output lines)."""
    entries, roots = collect(head, exhaustive)
    if not roots:
        return EXIT_UNMEASURABLE, [
            f"note no run root at {run_root()} and no game directory at {game_dir()} "
            "-- cannot measure"
        ]

    for entry in entries:
        if not entry.dirty and entry.names(head):
            return EXIT_MATCH, [
                f"note {entry.described()} was built from {head} and ran",
                f"match {entry.where} {entry.sha}",
            ]

    # No clean log names the commit. A dirty one still can, when its own sources were committed
    # and its closure covers everything being pushed. Both halves are checked here because
    # either alone would accept a run that never executed the code in the push.
    wanted = crates_in(changed)
    # Only a crate some shell compiles can be asked of a run. When none of the changed crates is
    # one, there is nothing a run could prove -- the line check-runtime-evidence.sh already draws
    # for a deletion. When the shell list cannot be read, nothing is narrowed, and neither is a
    # crate this checkout does not have: the shell list describes this checkout's workspace, so it
    # cannot say a crate outside it is host-only, and asking for a run is the safe direction.
    compiled = shell_compiled_crates()
    if compiled is not None and wanted:
        host_only = sorted(c for c in wanted - compiled if (REPO_ROOT / "crates" / c).is_dir())
        wanted -= set(host_only)
        if not wanted:
            return EXIT_MATCH, [
                f"note no crate this push changes compiles into a shell ({', '.join(host_only)}) "
                "-- no run can prove it, and none is asked for"
            ]
    covered: set[str] = set()
    proven: list[Entry] = []
    refusals: list[tuple[Entry, str]] = []
    seen: set[tuple[str, int | None]] = set()
    for entry in entries:
        if not (entry.dirty and entry.names(head)):
            continue
        key = (entry.module, entry.pe)
        if key in seen:
            continue
        seen.add(key)
        members, why = dirty_build_is_committed(entry)
        if members is None:
            refusals.append((entry, why))
            continue
        proven.append(entry)
        covered.update(members)

    if proven and wanted and wanted.issubset(covered):
        first = proven[0]
        return EXIT_MATCH, [
            f"note {first.described()} was built from {head} with its own sources committed, "
            "and ran",
            f"match {first.where} {first.sha}",
        ]

    # Then any log, whatever commit it names, whose binary was built from the source this commit
    # holds -- the run that came before the commit it justified. Stops as soon as the push is
    # covered, because every attempt past the cheap checks hashes an artifact.
    built: list[Entry] = []
    provenance_refusals: dict[tuple[str, int | None], str] = {}
    if wanted:
        for entry in entries:
            if wanted.issubset(covered):
                break
            key = (entry.module, entry.pe)
            if key in provenance_refusals or any(
                (other.module, other.pe) == key for other in proven + built
            ):
                continue
            members, why = built_from_head_source(entry, head, wanted)
            if members is None:
                provenance_refusals[key] = why
                continue
            built.append(entry)
            covered.update(members)

    if built and wanted.issubset(covered):
        first = built[0]
        return EXIT_MATCH, [
            f"note {first.described()} was built at {first.sha[:8]}"
            f"{' with uncommitted work' if first.dirty else ''} from exactly the source {head} "
            "holds, by its provenance record, and ran",
            f"match {first.where} {first.sha}",
        ]

    note = no_match_note(head, entries, proven, wanted, covered, refusals)
    if built:
        missing = sorted(wanted - covered)
        note = (
            f"{note}; {len(built)} shell(s) that ran hold {head}'s own source by their provenance "
            f"records, but {', '.join(missing[:4])}{' and more' if len(missing) > 4 else ''} "
            "is compiled into none that can be tied to a run -- the binary that ran has been "
            "relinked since, or was never built with a provenance record"
        )
    elif entries and not proven:
        newest = entries[0]
        why = provenance_refusals.get((newest.module, newest.pe))
        if why:
            note = f"{note}; nor does its provenance prove it: {why}"
    lines = [f"note {note}"]
    emitted: set[str] = set()
    for entry in entries:
        if entry.dirty or entry.sha in emitted:
            continue
        emitted.add(entry.sha)
        lines.append(f"candidate {entry.sha} {entry.where}")
        if len(emitted) >= CANDIDATES and not exhaustive:
            break
    return EXIT_NO_MATCH, lines


def no_match_note(
    head: str,
    entries: list[Entry],
    proven: list[Entry],
    wanted: set[str],
    covered: set[str],
    refusals: list[tuple[Entry, str]],
) -> str:
    """One sentence saying why nothing proved `head`, naming what to do about it."""
    if proven:
        missing = sorted(wanted - covered) or ["nothing this push changes"]
        return (
            f"{proven[0].described()} ran this commit's own code, but {missing[0]} "
            "is not compiled into any shell that ran -- run one that is"
        )
    if refusals:
        entry, why = refusals[0]
        return f"{entry.described()} ran code built at {head}, but {why}"
    if not entries:
        return "no log in either the run root or the game directory carries a build line"
    newest = entries[0]
    # The dirt is named but not blamed: what refused this log is the sha, and an earlier version
    # of this sentence said only "was built from a DIRTY tree", which reads as the reason and
    # sent the agent looking for uncommitted files instead of for a run at the right commit.
    aside = " (with work uncommitted at the time)" if newest.dirty else ""
    return (
        f"the newest log {newest.described()} was built from {newest.sha[:8]}{aside}, not {head}"
    )


# ------------------------------------------------------------------------------------------
# Selftest
# ------------------------------------------------------------------------------------------


def selftest() -> int:  # noqa: C901 -- a list of cases reads better than a table of lambdas
    import tempfile

    failures = 0

    def check(condition: bool, label: str) -> None:
        nonlocal failures
        if condition:
            print(f"  ok    {label}")
        else:
            print(f"  FAIL  {label}")
            failures += 1

    def run(head: str, changed: list[str] | None = None) -> tuple[int, list[str]]:
        return scan(head, changed or [], exhaustive=True)

    # The cases below name made-up crates, so the real shell list would narrow them away. They
    # run with no narrowing; the narrowing has cases of its own further down.
    real_shell_compiled_crates = globals()["shell_compiled_crates"]
    globals()["shell_compiled_crates"] = lambda: None

    identity = "build git={sha}{dirty} module={module} base=0x1 pe=0x{pe} (t)\nmore\n"

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        runs = tmp / "runs"
        game = tmp / "game"
        (runs / "br-good").mkdir(parents=True)
        game.mkdir()
        os.environ["ER_ME3_RUN_ROOT"] = str(runs)
        os.environ["ER_GAME_DIR"] = str(game)

        # The gap this file was written for: a run launched the way the user launches writes
        # into the game directory, and only the run root used to be read.
        (game / "er-quit-menu.log").write_text(
            identity.format(sha="deadbeef1234", dirty="", module="er_quit_menu.dll", pe="6aa4c5a9"),
            encoding="utf-8",
        )
        code, lines = run("deadbeef1234")
        check(code == EXIT_MATCH, "a clean game-directory log naming the commit is evidence")
        check(
            any(line.startswith("match game-dir/er-quit-menu.log") for line in lines),
            "the match names the game-directory log it came from",
        )
        code, _ = run("deadbeef")
        check(code == EXIT_MATCH, "an abbreviated commit sha matches the log's full sha")

        code, lines = run("0e0842402b18")
        check(code == EXIT_NO_MATCH, "a game-directory log for a different sha is refused")
        check(
            any("not 0e0842402b18" in line for line in lines),
            "the refusal says which build the log carried instead",
        )

        # The run root still counts, and a match there is found with the game directory present.
        (runs / "br-good" / "er-invasion-warp.log").write_text(
            identity.format(
                sha="aaaaaaaaaaaa", dirty="", module="er_invasion_warp.dll", pe="6a000001"
            ),
            encoding="utf-8",
        )
        code, lines = run("aaaaaaaaaaaa")
        check(code == EXIT_MATCH, "a clean run-root log naming the commit is still evidence")

        # A dirty log with no artifact behind it proves nothing, and says why.
        (game / "er-focus-input.log").write_text(
            identity.format(
                sha="bbbbbbbbbbbb", dirty="+dirty", module="er_focus_input.dll", pe="6a000002"
            ),
            encoding="utf-8",
        )
        code, lines = run("bbbbbbbbbbbb", ["crates/er-focus-input/src/lib.rs"])
        check(code == EXIT_NO_MATCH, "a dirty log whose artifact cannot be checked is refused")
        check(
            any("er_focus_input.dll" in line and "note" in line for line in lines),
            "the refusal names the module that wrote the log, not only the file name",
        )

        del os.environ["ER_ME3_RUN_ROOT"]
        del os.environ["ER_GAME_DIR"]
        os.environ["ER_ME3_RUN_ROOT"] = str(tmp / "absent")
        os.environ["ER_GAME_DIR"] = str(tmp / "absent")
        code, _ = run("deadbeef1234")
        check(
            code == EXIT_UNMEASURABLE,
            "with neither directory present the answer is unmeasurable",
        )
        del os.environ["ER_ME3_RUN_ROOT"]
        del os.environ["ER_GAME_DIR"]

    # The dirty-log proof, with its collaborators replaced so the cases are exact. Each fact is
    # removed in turn from an otherwise passing set, because a proof nobody has watched refuse
    # is not a proof.
    entry = Entry(
        pathlib.Path("/tmp/er-quit-menu.log"),
        "game-dir/er-quit-menu.log",
        BUILD_LINE.match(
            "build git=deadbeef1234+dirty module=er_quit_menu.dll base=0x1 pe=0x6aa4c5a9 (t)"
        ),
        0,
    )
    saved = (
        globals()["package_for"],
        globals()["artifact_dir"],
        globals()["pe_timestamp"],
        globals()["provenance_is_fresh"],
        globals()["closure_members"],
        globals()["dirty_crate_names"],
    )
    try:
        with tempfile.TemporaryDirectory() as raw:
            fake = pathlib.Path(raw)
            (fake / "er_quit_menu.dll").write_bytes(b"not really a pe")
            globals()["package_for"] = lambda module: "er-quit-menu"
            globals()["artifact_dir"] = lambda: fake
            globals()["pe_timestamp"] = lambda artifact: 0x6AA4C5A9
            globals()["provenance_is_fresh"] = lambda package, artifact: (True, "")
            globals()["closure_members"] = lambda package: ["er-quit-menu", "er-quit-menu-core"]
            globals()["dirty_crate_names"] = lambda: {"er-input-harness"}

            members, why = dirty_build_is_committed(entry)
            check(
                members == ["er-quit-menu", "er-quit-menu-core"] and not why,
                "a dirty build whose own closure is committed proves the commit ran",
            )

            globals()["dirty_crate_names"] = lambda: {"er-quit-menu-core"}
            members, why = dirty_build_is_committed(entry)
            check(
                members is None and "er-quit-menu-core" in why,
                "uncommitted source inside the closure refuses, naming the crate",
            )

            globals()["dirty_crate_names"] = lambda: {"er-input-harness"}
            globals()["pe_timestamp"] = lambda artifact: 0x11111111
            members, why = dirty_build_is_committed(entry)
            check(
                members is None and "relinked" in why,
                "an artifact relinked since the run refuses: it is not the binary that ran",
            )

            globals()["pe_timestamp"] = lambda artifact: 0x6AA4C5A9
            globals()["provenance_is_fresh"] = lambda package, artifact: (
                False,
                "SOURCE MOVED since this DLL was built",
            )
            members, why = dirty_build_is_committed(entry)
            check(
                members is None and "SOURCE MOVED" in why,
                "source that moved after the build refuses, whatever git status says",
            )

            globals()["provenance_is_fresh"] = lambda package, artifact: (True, "")
            globals()["dirty_crate_names"] = lambda: None
            members, why = dirty_build_is_committed(entry)
            check(members is None, "git failing to report leaves the dirty log unproven")

            # And the scope requirement, through the scan: the proof holds, but the push
            # changes a crate that is not compiled into the shell that ran.
            globals()["dirty_crate_names"] = lambda: {"er-input-harness"}
            with tempfile.TemporaryDirectory() as raw2:
                tmp2 = pathlib.Path(raw2)
                game2 = tmp2 / "game"
                game2.mkdir(parents=True)
                (game2 / "er-quit-menu.log").write_text(
                    identity.format(
                        sha="deadbeef1234",
                        dirty="+dirty",
                        module="er_quit_menu.dll",
                        pe="6aa4c5a9",
                    ),
                    encoding="utf-8",
                )
                os.environ["ER_ME3_RUN_ROOT"] = str(tmp2 / "absent")
                os.environ["ER_GAME_DIR"] = str(game2)

                code, lines = run("deadbeef1234", ["crates/er-quit-menu/src/lib.rs"])
                check(
                    code == EXIT_MATCH,
                    "a dirty log covering the changed crate is accepted as evidence",
                )
                code, lines = run("deadbeef1234", ["crates/er-invasion-warp/src/lib.rs"])
                check(
                    code == EXIT_NO_MATCH
                    and any("er-invasion-warp" in line for line in lines),
                    "a dirty log that does not compile the changed crate is refused",
                )
                code, lines = run("deadbeef1234", [])
                check(
                    code == EXIT_NO_MATCH,
                    "with no changed paths to check against, a dirty log is not accepted",
                )

                # A crate no shell compiles is not asked of the run.
                globals()["shell_compiled_crates"] = lambda: {
                    "er-quit-menu",
                    "er-quit-menu-core",
                    "er-invasion-warp",
                }
                code, lines = run(
                    "deadbeef1234",
                    ["crates/er-quit-menu/src/lib.rs", "crates/er-flver/examples/flver-obj.rs"],
                )
                check(
                    code == EXIT_MATCH,
                    "a host-only crate beside a covered one does not refuse the covered run",
                )
                code, lines = run("deadbeef1234", ["crates/er-invasion-warp/src/lib.rs"])
                check(
                    code == EXIT_NO_MATCH,
                    "a crate a shell compiles still has to be covered by the run",
                )
                # A commit no log names: only the narrowing can let it through.
                code, lines = run("cafebabe5678", ["crates/er-flver/examples/flver-obj.rs"])
                check(
                    code == EXIT_MATCH and any("er-flver" in line for line in lines),
                    "a push changing only host-only crates needs no run, and says which crates",
                )
                code, lines = run("cafebabe5678", ["crates/er-invasion-warp/src/lib.rs"])
                check(
                    code != EXIT_MATCH,
                    "a push changing a shell's crate with no run at all is not let through",
                )
                globals()["shell_compiled_crates"] = lambda: None
                code, lines = run("cafebabe5678", ["crates/er-flver/examples/flver-obj.rs"])
                check(
                    code != EXIT_MATCH,
                    "an unreadable shell list narrows nothing",
                )
                del os.environ["ER_ME3_RUN_ROOT"]
                del os.environ["ER_GAME_DIR"]
    finally:
        (
            globals()["package_for"],
            globals()["artifact_dir"],
            globals()["pe_timestamp"],
            globals()["provenance_is_fresh"],
            globals()["closure_members"],
            globals()["dirty_crate_names"],
        ) = saved
        globals()["shell_compiled_crates"] = real_shell_compiled_crates

    # The real shell list: the product compiles er-build-import-core, and nothing compiles
    # er-flver. Skipped when the workspace layout cannot be read.
    compiled = shell_compiled_crates()
    if compiled is None:
        print("  skip  the shipped-shell list could not be read")
    else:
        check("er-build-import-core" in compiled, "a real shell compiles er-build-import-core")
        check("er-flver" not in compiled, "no real shell compiles er-flver")

    # Its cases name made-up crates too, so it also runs with no narrowing.
    globals()["shell_compiled_crates"] = lambda: None
    try:
        selftest_provenance(check)
    finally:
        globals()["shell_compiled_crates"] = real_shell_compiled_crates

    # The pe reader against a real artifact, when one is built: the fake above proves the
    # comparison, not the parser.
    real = artifact_dir() / "er_crash_logging.dll"
    if real.is_file():
        stamp = pe_timestamp(real)
        check(isinstance(stamp, int) and stamp > 0, f"a real DLL yields a pe stamp (0x{stamp:08x})")
    else:
        print(f"  skip  no built DLL at {real} -- the pe parser is exercised by the fake only")

    print("er-runtime-evidence selftest:", "PASS" if failures == 0 else f"{failures} failure(s)")
    return 0 if failures == 0 else 1


def selftest_provenance(check) -> None:  # noqa: C901 -- one scenario, told in order
    """A run built at the parent with the change uncommitted, then the change committed.

    The record's `source_sha` is made by `er-dll-provenance.py` itself, over a scratch working
    tree, so `tree_source_sha` is checked against the hash it exists to reproduce rather than
    against its own output. Failures are counted through `check`.
    """
    import tempfile

    provenance = load_script("er_dll_provenance", "er-dll-provenance.py")
    saved_env = {name: os.environ.get(name) for name in ("ER_ME3_RUN_ROOT", "ER_GAME_DIR", "XDG_RUNTIME_DIR")}
    saved = (globals()["package_for"], globals()["pe_timestamp"], PUSH_REPO[0], provenance.REPO_ROOT)
    _TREE_HASHES.clear()
    try:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            repo = tmp / "repo"
            crate = repo / "crates" / "er-x"
            (crate / "src").mkdir(parents=True)

            def git_run(*args: str) -> str:
                return subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30
                ).stdout.strip()

            git_run("init", "-q")
            git_run("config", "user.email", "selftest@example.invalid")
            git_run("config", "user.name", "selftest")
            (crate / "Cargo.toml").write_text('[package]\nname = "er-x"\n', encoding="utf-8")
            (crate / "src" / "lib.rs").write_text("pub fn a() {}\n", encoding="utf-8")
            git_run("add", "-A")
            git_run("commit", "-q", "-m", "parent")
            parent = git_run("rev-parse", "HEAD")

            # The run: built from the parent with the change still uncommitted.
            (crate / "src" / "lib.rs").write_text("pub fn a() {}\npub fn b() {}\n", encoding="utf-8")
            provenance.REPO_ROOT = repo
            built_sha, _count = provenance.source_sha(["er-x"])
            provenance.REPO_ROOT = saved[3]
            # ...and the commit the run justified.
            git_run("commit", "-q", "-a", "-m", "the change that ran")
            tip = git_run("rev-parse", "HEAD")

            release = repo / "target" / "x86_64-pc-windows-msvc" / "release"
            release.mkdir(parents=True)
            dll = release / "er_x.dll"
            dll.write_bytes(b"MZ not really a pe, the stamp is faked below")
            record_path = release / "er_x.dll.provenance.json"

            def write_record(
                source: str, artifact_sha: str | None = None, pe: int | None = None
            ) -> None:
                fields = {
                    "package": "er-x",
                    "closure": ["er-x"],
                    "source_sha": source,
                    "artifact_sha256": artifact_sha or hashlib.sha256(dll.read_bytes()).hexdigest(),
                }
                if pe is not None:
                    fields["pe_timestamp"] = pe
                record_path.write_text(json.dumps(fields), encoding="utf-8")

            game = tmp / "game"
            game.mkdir()
            os.environ["ER_ME3_RUN_ROOT"] = str(tmp / "absent")
            os.environ["ER_GAME_DIR"] = str(game)
            os.environ["XDG_RUNTIME_DIR"] = str(tmp / "runtime")
            PUSH_REPO[0] = repo
            globals()["package_for"] = lambda module: "er-x"
            globals()["pe_timestamp"] = lambda artifact: 0x6A000010
            changed = ["crates/er-x/src/lib.rs"]

            check(
                tree_source_sha(tip, ["er-x"]) == built_sha,
                "the commit's tree hashes to what er-dll-provenance.py recorded for the same files",
            )

            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_NO_MATCH and any("no log" in line for line in lines),
                "with no log at all the push is refused",
            )

            (game / "er-x.log").write_text(
                f"build git={parent[:12]}+dirty module=er_x.dll base=0x1 pe=0x6a000010 (t)\n",
                encoding="utf-8",
            )
            write_record(built_sha)
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_MATCH and any("provenance" in line for line in lines),
                "a parent+dirty log whose binary holds the commit's source proves the commit ran",
            )

            # A build that carried an edit the commit does not: same parent, same stamp.
            (crate / "src" / "lib.rs").write_text(
                "pub fn a() {}\npub fn b() {}\npub fn c() {}\n", encoding="utf-8"
            )
            provenance.REPO_ROOT = repo
            other_sha, _count = provenance.source_sha(["er-x"])
            provenance.REPO_ROOT = saved[3]
            write_record(other_sha)
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_NO_MATCH and any("differs from what" in line for line in lines),
                "a parent+dirty log built from source the commit does not hold is refused",
            )

            write_record(built_sha, artifact_sha="0" * 64)
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_NO_MATCH and any("is not the file" in line for line in lines),
                "a record that does not describe the artifact on disk proves nothing",
            )

            write_record(built_sha)
            code, lines = scan(tip, ["crates/er-y/src/lib.rs"], exhaustive=True)
            check(
                code == EXIT_NO_MATCH,
                "a matching build that compiles none of the changed crates does not cover the push",
            )

            globals()["pe_timestamp"] = lambda artifact: 0x11111111
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_NO_MATCH and any("pe stamp" in line for line in lines),
                "an artifact relinked since the run is not the binary that ran",
            )

            # A record that carries the run's own stamp describes the binary that ran by itself,
            # whatever has been linked over the file since -- the 2026-10-15 case, a bare cargo
            # relink nine minutes after the run that wrote no record.
            write_record(built_sha, artifact_sha="0" * 64, pe=0x6A000010)
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_MATCH,
                "a record stamped with the run's pe proves the run although the file was relinked",
            )
            write_record(other_sha, artifact_sha="0" * 64, pe=0x6A000010)
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_NO_MATCH and any("differs from what" in line for line in lines),
                "a record stamped with the run's pe but other source is refused",
            )
            write_record(built_sha, pe=0x22222222)
            code, lines = scan(tip, changed, exhaustive=True)
            check(
                code == EXIT_NO_MATCH,
                "a record stamped by a later build does not speak for the run",
            )
    finally:
        globals()["package_for"], globals()["pe_timestamp"], PUSH_REPO[0], provenance.REPO_ROOT = saved
        _TREE_HASHES.clear()
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # The same hash over this repository, when a small closure is clean here: the commit's tree
    # and the working tree must agree, which pins `tree_source_sha` to the real file layout.
    members = closure_members("er-crash-logging")
    head = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--", *[f"crates/{m}" for m in members or []])
    if members and head and status == "":
        working, _count = provenance.source_sha(members)
        check(
            tree_source_sha(head.strip(), members) == working,
            "a clean closure hashes the same from HEAD's tree as from the working tree",
        )
    else:
        print("  skip  er-crash-logging's closure is not clean here -- real-tree hash not compared")


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime evidence for a commit.")
    parser.add_argument("--head", help="the commit to look for")
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="read every run directory rather than stopping at the newest few",
    )
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        help="the checkout being pushed, when it is not this one; its target/ holds the "
        "binaries that ran",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if not args.head:
        parser.error("--head is required")
    if args.repo is not None:
        PUSH_REPO[0] = args.repo.resolve()

    changed = []
    if not sys.stdin.isatty():
        changed = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]

    code, lines = scan(args.head, changed, args.exhaustive)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
