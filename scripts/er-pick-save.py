#!/usr/bin/env python3
"""Pick a random valid (save file, slot) and decode it before anyone launches anything.

"Random" here never means "blind". The Autoload Identity Launch Gate in AGENTS.md is
explicit: a launch expected to autoload must not happen until the exact character identity
and slot are known from current save evidence. So this script's contract is decode-then-
report -- it returns the character's name, level and slot, and a caller that cannot print
those has no business launching.

Validity is the product'S own definition, not a guess
-----------------------------------------------------
  * File size must equal `EXPECTED_SAVE_FILE_BYTES`, read live from
    `crates/er-save-redirect/src/lib.rs` rather than copied here -- `validate_save_file_path`
    rejects anything else at runtime, so a picker using a different number would hand back
    saves the DLL refuses.
  * A slot counts as occupied when the container's `USER_DATA010.active_slot` bitmap marks it,
    and its body must then decode to a name that is not empty-like and a level > 0, decoded with
    `save-slot-oracle.py` (the evidence-bound decoder), never inferred from filenames. A container
    or slot that cannot be read or decoded is reported as that failure, never as an empty slot.

Why it picks a file first, then a slot
--------------------------------------
Decoding all ten slots of one save costs ~0.4s, so sweeping the whole corpus up front is
~35s -- past the 30s cap every shell op here lives under. Drawing a file and decoding only
its slots costs one tenth of a second, and a file with no occupied slot simply triggers a
redraw. The distribution is therefore uniform over save files rather than over characters;
that is a deliberate trade for staying inside the cap, and `--all` exists when a full
inventory is actually wanted.

Corpus root
-----------
`--root`, else `$ER_SAVE_CORPUS_ROOT`, else `<repo>/save-files`. The older enumerator in this
directory still defaults to a `/mnt/a/...` WSL path that does not exist on this machine, so
every run of it silently found nothing; the default here is the corpus that is actually
present. Staged redirect subtrees (`er-quickload-save-redirect-stage/`) are skipped -- they are
private copies the DLL writes, not sources.

Usage:
    python3 scripts/er-pick-save.py
    python3 scripts/er-pick-save.py --json --seed 1234
    python3 scripts/er-pick-save.py --container sl2
    python3 scripts/er-pick-save.py --all --json
    python3 scripts/er-pick-save.py --selftest
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ORACLE_SCRIPT = REPO_ROOT / "scripts" / "save-slot-oracle.py"
SAVE_REDIRECT_LIB = REPO_ROOT / "crates" / "er-save-redirect" / "src" / "lib.rs"
STAGE_DIR_MARKER = "er-quickload-save-redirect-stage"
# The same subtree under the name the DLL used before it was renamed. Both are private copies the
# save redirect writes, never sources, and both are still on disk: `save-files/25r/` carries an
# `er-effects-save-redirect-stage/eldenring/<steamid>/ER0000.co2` from 2026-08-22. Skipping only
# the current spelling let that copy into the draw, and on 2026-09-22 it was the only candidate
# `er-pick-save.py --slot 0 --root save-files/25r` returned -- so er-run-branch.py, which filters
# the draw down to the configured `save_file`, refused a launch saying the real ER0000.sl2 beside
# it held no occupied character.
LEGACY_STAGE_DIR_MARKER = "er-effects-save-redirect-stage"

# How many decoded launch-gate identities to keep. Entries are keyed by container state, so a stale
# one can never be returned -- this only stops the file growing without bound across sessions.
IDENTITY_CACHE_MAX_ENTRIES = 64

EXIT_OK = 0
EXIT_ERROR = 1

# A file with no occupied slot is possible (a wiped container), so a redraw needs a bound.
MAX_DRAWS = 25


def expected_save_bytes() -> int:
    """Read the product's own size invariant instead of duplicating the constant."""
    text = SAVE_REDIRECT_LIB.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"pub const EXPECTED_SAVE_FILE_BYTES:\s*u64\s*=\s*(0x[0-9a-fA-F_]+|[0-9_]+)", text
    )
    if not match:
        raise RuntimeError(
            f"EXPECTED_SAVE_FILE_BYTES not found in {SAVE_REDIRECT_LIB} -- "
            "the constant moved; fix this reader rather than hard-coding a number"
        )
    return int(match.group(1).replace("_", ""), 0)


def oracle():
    spec = importlib.util.spec_from_file_location("save_slot_oracle", ORACLE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main_checkout_root() -> Path | None:
    """The main working tree, when this file is running out of a linked git worktree.

    The corpus is untracked, so a worktree made with `git worktree add` starts without it, and
    every launcher that draws a save then refuses with "no save corpus found" -- a missing
    directory reported as a missing corpus, which reads like the saves are gone rather than like
    the checkout is new. In a linked worktree `.git` is a file holding `gitdir: <main>/.git/
    worktrees/<name>`, and that directory's `commondir` points back at the main `.git`, whose
    parent is the checkout the corpus lives in. Returns `None` in the main checkout itself, where
    `.git` is a directory and the plain `<repo>/save-files` candidate already applies.
    """
    marker = REPO_ROOT / ".git"
    if not marker.is_file():
        return None
    try:
        pointer = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "gitdir:"
    if not pointer.startswith(prefix):
        return None
    gitdir = Path(pointer[len(prefix) :].strip()).expanduser()
    if not gitdir.is_absolute():
        gitdir = (REPO_ROOT / gitdir).resolve()
    try:
        common = Path((gitdir / "commondir").read_text(encoding="utf-8").strip())
    except OSError:
        return None
    if not common.is_absolute():
        common = (gitdir / common).resolve()
    return common.parent


def resolve_root(cli_root: str | None) -> Path:
    sibling = main_checkout_root()
    for candidate in (
        cli_root,
        os.environ.get("ER_SAVE_CORPUS_ROOT"),
        REPO_ROOT / "save-files",
        sibling / "save-files" if sibling else None,
    ):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_dir():
            return path
    raise RuntimeError(
        "no save corpus found; pass --root or set ER_SAVE_CORPUS_ROOT to a directory of "
        f"ER0000.sl2 / ER0000.co2 files (looked in {REPO_ROOT / 'save-files'}"
        + (f" and {main_checkout_root() / 'save-files'}" if main_checkout_root() else "")
        + ")"
    )


def eligible_saves(root: Path, container: str, expected_bytes: int) -> list[Path]:
    """Source saves of the right container and the exact product-mandated size."""
    suffixes = {"sl2": {".sl2"}, "co2": {".co2"}, "both": {".sl2", ".co2"}}[container]
    found = []
    for path in sorted(root.rglob("ER0000.*")):
        if path.suffix.lower() not in suffixes:
            continue
        if STAGE_DIR_MARKER in path.parts or LEGACY_STAGE_DIR_MARKER in path.parts:
            continue
        try:
            if path.stat().st_size != expected_bytes:
                continue
        except OSError:
            continue
        found.append(path)
    return found


class SaveDecodeError(RuntimeError):
    """A container, or one slot of it, that could not be read or decoded.

    Kept apart from "this slot holds no character" on purpose. On 2026-10-23 `er-run-branch.py`
    refused a launch with "no occupied character at .../save-files/0/ER0000.sl2 slot 0", and the
    same decode a minute later read slot 0 as "Vagabond", level 9. That save sits on an
    autofs-mounted CIFS share, so the first read after the share idled out also mounted it. Whatever
    went wrong in that read was reported as an empty slot, because an unreadable bitmap, a decode
    that raised and a clear bitmap bit all came back as the same empty list.
    """


def read_save(path: Path, expected_bytes: int | None) -> bytes:
    """The whole container, or `SaveDecodeError` naming the path and the cause.

    The length is checked again although `eligible_saves` already compared `st_size`: that is the
    size the directory entry reported, and this is what the read returned. On a network share they
    are separate requests, and a container cut short decodes as a bitmap that is not there.
    """
    try:
        data = path.read_bytes()
    except OSError as err:
        raise SaveDecodeError(f"could not read {path}: {err}") from err
    if expected_bytes is not None and len(data) != expected_bytes:
        raise SaveDecodeError(
            f"read {len(data)} bytes of {path}, expected {expected_bytes} "
            "(the size its directory entry reported)"
        )
    return data


def decode_occupied(
    module, path: Path, only_slot: int | None = None, expected_bytes: int | None = None
) -> tuple[list[dict], list[str]]:
    """The occupied slots of one save with their identity, and the slots that failed to decode.

    Each slot asked for has exactly one of three outcomes, and they are never folded together:

      * bitmap bit clear: not occupied, and neither list mentions it;
      * bitmap bit set, body decodes to a character: an entry in the first list;
      * bitmap bit set, decode raises or finds no character: a line in the second list naming the
        slot, the path and the cause.

    A container that cannot be read, or whose bitmap cannot be read, raises `SaveDecodeError`,
    because no slot of it can be answered for.

    Occupancy is the `USER_DATA010.active_slot` bitmap, not "the body decodes".

    Deleting a character clears that bitmap and leaves the `USER_DATA00N` body in place, so a
    deleted slot still decodes to a plausible name, level, runes and stats. Testing the body alone
    therefore offers slots the game will not load, and this tool's whole contract is that the
    character is known before anything launches (AGENTS.md's Autoload Identity Launch Gate).

    Measured 2026-09-03: `save-files/50-Merchant-Unleveled/ER0000.sl2` decoded as ten occupied
    slots here while its bitmap holds exactly one, "Invader Merchant" level 50 in slot 0. The
    APPDATA container decoded as ten against a bitmap of four. Worse, `--seed 2073568449` -- the
    seed the `er-quickload` x `er-invasion-warp` conflict bisect ran four times -- picks slot 8 of
    that Merchant file, a stale body in a container with one live character. Every one of those
    "reproductions" was an invalid-slot launch, which is the boot path that faults at
    `game+0x10043`; the bisect blamed a DLL for what its own save picker had set up.

    An unreadable bitmap is "cannot answer", never a fallback to the body-only test that caused
    this, and never "nothing is occupied" either.
    """
    data = read_save(path, expected_bytes)
    bitmap = module.active_slot_bitmap(data)
    if bitmap is None:
        raise SaveDecodeError(
            f"the occupancy bitmap (USER_DATA010.active_slot) of {path} is unreadable "
            f"({len(data)} bytes read)"
        )
    results = []
    failures = []
    # `only_slot` narrows which slots are decoded and nothing else: the bitmap below still decides
    # whether the slot is real, so a targeted read refuses a deleted character exactly as a sweep
    # does. Decoding one slot of this container costs about three seconds, so a caller that wants
    # one identity must not pay for ten.
    wanted = range(module.SLOT_COUNT) if only_slot is None else (only_slot,)
    for slot in wanted:
        if not bitmap[slot]:
            continue
        try:
            decoded = module.decode_save_slot(data, path, slot)
        # `SystemExit` too: the oracle's `extract_slot` raises it for a missing `USER_DATA` entry,
        # and letting it through would end a whole sweep on one bad container.
        except (Exception, SystemExit) as err:
            failures.append(
                f"slot {slot} of {path} is marked occupied but did not decode: "
                f"{type(err).__name__}: {err}"
            )
            continue
        fields = decoded.get("decoded_fields") or {}
        name = fields.get("name") or ""
        level = fields.get("level")
        if (
            fields.get("name_empty_like")
            or module.name_empty_like(name)
            or not isinstance(level, int)
            or level <= 0
        ):
            failures.append(
                f"slot {slot} of {path} is marked occupied but decoded to no character "
                f"(name {name!r}, level {level!r}, layout {decoded.get('layout')!r})"
            )
            continue
        results.append(
            {
                "slot": slot,
                "name": name,
                "level": level,
                "runes": fields.get("runes"),
                "stats": fields.get("stats_named"),
                # Carried because it is half of what Seamless matches on. Its
                # `matchmaking_breakin_lobby_ykssr_199_6` filter is a `<level>_<weapon>` pair, so a
                # character reinforced far above its rune level searches a pool almost nobody is in
                # -- and that is invisible in a block that prints only the rune level. Measured
                # 2026-09-08 on the corpus: RL9 reads +0, RL90 +12, RL100 +20, while the character
                # getting no invasions read +25 at RL9.
                "matchmaking_weapon_level": fields.get("matchmaking_weapon_level"),
            }
        )
    return results, failures


def occupied_slots(
    module, path: Path, only_slot: int | None = None, expected_bytes: int | None = None
) -> list[dict]:
    """[`decode_occupied`] for a sweep: skip what fails, but say so on stderr.

    A sweep (`pick`, `inventory`) has other containers to try, so one it cannot read is skipped
    rather than ending the run. It is still named with its cause, because a skipped container that
    says nothing reads exactly like one that holds no character.
    """
    try:
        results, failures = decode_occupied(module, path, only_slot, expected_bytes)
    except SaveDecodeError as err:
        print(f"er-pick-save: skipping {path}: {err}", file=sys.stderr)
        return []
    for failure in failures:
        print(f"er-pick-save: skipping {failure}", file=sys.stderr)
    return results


def describe(path: Path, entry: dict, root: Path) -> dict:
    stat = path.stat()
    return {
        "save_file": str(path),
        "save_file_relative": str(path.relative_to(root)) if path.is_relative_to(root) else None,
        "container": path.suffix.lower().lstrip("."),
        # Half this corpus is writable on disk. The DLL stages privately so the source should
        # never be written, but a run that names its exposure is easier to trust than one that
        # assumes the guarantee held.
        "source_writable": bool(stat.st_mode & 0o200),
        **entry,
    }


def pick(root: Path, container: str, seed: int) -> dict:
    module = oracle()
    expected = expected_save_bytes()
    candidates = eligible_saves(root, container, expected)
    if not candidates:
        raise RuntimeError(
            f"no eligible {container} saves under {root} "
            f"(need ER0000.* of exactly {expected} bytes, outside {STAGE_DIR_MARKER}/)"
        )

    rng = random.Random(seed)
    pool = candidates[:]
    rng.shuffle(pool)
    for attempt, path in enumerate(pool[:MAX_DRAWS], start=1):
        slots = occupied_slots(module, path, expected_bytes=expected)
        if not slots:
            continue
        chosen = rng.choice(slots)
        return {
            "seed": seed,
            "draws": attempt,
            "eligible_files": len(candidates),
            "occupied_slots_in_file": len(slots),
            "corpus_root": str(root),
            **describe(path, chosen, root),
        }

    raise RuntimeError(
        f"drew {min(len(pool), MAX_DRAWS)} saves under {root} and none had an occupied slot"
    )


def identity_cache_path() -> Path:
    """Where decoded launch-gate identities are remembered between launches."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "er-pick-save" / "launch-gate-identity.json"


def cache_key(path: Path, slot: int) -> str:
    """Identity of a decode, made of everything that could change its answer.

    Size and modification time together are what the game changes when it writes a save, so a key
    built from them cannot return a stale character: the moment the container is rewritten, the key
    misses and the slot is decoded again. The path is in the key because two containers in one
    account directory hold different characters at the same slot number.
    """
    stat = path.stat()
    return f"{path}|{slot}|{stat.st_size}|{stat.st_mtime_ns}"


def cached_identity(path: Path, slot: int) -> dict | None:
    """The identity decoded for this exact container state, or `None` to decode it."""
    try:
        store = json.loads(identity_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = store.get(cache_key(path, slot)) if isinstance(store, dict) else None
    return entry if isinstance(entry, dict) else None


def remember_identity(path: Path, slot: int, entry: dict) -> None:
    """Record a decode so the next launch of the same save does not pay for it again.

    Best effort on purpose: an unwritable cache costs a slow launch, never a wrong one, so every
    failure here is swallowed and the gate goes on decoding.
    """
    try:
        target = identity_cache_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            store = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            store = {}
        if not isinstance(store, dict):
            store = {}
        # Keyed by container state, so entries for a rewritten save are dead weight rather than a
        # hazard. Kept bounded anyway: this is a launch gate, not an archive.
        store[cache_key(path, slot)] = entry
        if len(store) > IDENTITY_CACHE_MAX_ENTRIES:
            store = dict(list(store.items())[-IDENTITY_CACHE_MAX_ENTRIES:])
        target.write_text(json.dumps(store, indent=2), encoding="utf-8")
    except OSError:
        pass


def active_container(root: Path, container: str, expected_bytes: int) -> Path | None:
    """The eligible container the game most recently wrote, or `None` if there is none.

    A save directory holds more than one `ER0000.*` of the right size: vanilla writes `.sl2`,
    Seamless writes `.co2`, and both survive in the same account folder. Only one of them is the
    save a launch will actually load, and it is the one with the newest mtime -- the same rule
    `er-run-branch.py` already uses one function above to choose between account directories.

    Deciding it here is not a nicety. Decoding one slot of a 28.9 MB container measured 19.5s on
    2026-09-17, so a gate that decodes both spends 39s inside a 28s step bound and the launch is
    refused before the game is reached, with the identity correctly decoded and thrown away.
    """
    candidates = eligible_saves(root, container, expected_bytes)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def targeted(root: Path, container: str, slot: int) -> dict:
    """Decode exactly the slot asked for, and stop at the first file that holds it.

    This exists because [`inventory`] is the wrong instrument for the launch gate and was being
    used as it. `er-run-branch.py` needs one character's identity -- the one the configuration
    selects -- and was calling `--all`, which decodes every occupied slot of every eligible file
    and then throws all but one away. Measured 2026-09-17 against the live APPDATA container:
    twenty slot decodes, 67s of CPU, inside a 28s step bound, so every launch was refused before
    the game was reached. One slot is one decode.

    The bitmap is still the occupancy authority, so a slot the game will not load is refused here
    exactly as it is in [`occupied_slots`] -- the saving is in how many slots are decoded, never in
    which of them counts as real.

    Unlike a sweep, this has no other container to fall back on, so a read or decode failure raises
    [`SaveDecodeError`] naming the slot, the path and the cause. Only a clear bitmap bit comes back
    as an empty `targets`, which is what `er-run-branch.py` reports as "no occupied character".
    """
    module = oracle()
    expected = expected_save_bytes()
    active = active_container(root, container, expected)
    if active is None:
        raise RuntimeError(
            f"no eligible {container} saves under {root} "
            f"(need ER0000.* of exactly {expected} bytes, outside {STAGE_DIR_MARKER}/)"
        )
    if not 0 <= slot < module.SLOT_COUNT:
        raise RuntimeError(f"slot {slot} is outside 0..{module.SLOT_COUNT - 1}")

    # One container, not every eligible one. The others are a previous format's copy of the same
    # account and cannot be what this launch loads; decoding them doubles the gate's cost for an
    # answer that is filtered away immediately afterwards.
    #
    # Cached on the container's own size and mtime. Decoding slot 0 of the live 28.9 MB container
    # measured 38s on 2026-09-17, against the 28s bound `er-run-branch.py` puts on every step, so
    # the gate refused the launch having correctly decoded the character it was refusing to launch.
    # A save the game has not rewritten cannot hold a different character, so the second launch of
    # one reads the answer instead of re-deriving it.
    cached = cached_identity(active, slot)
    if cached is not None:
        return {"corpus_root": str(root), "count": 1, "targets": [cached]}
    try:
        decoded, failures = decode_occupied(module, active, only_slot=slot, expected_bytes=expected)
    except SaveDecodeError as err:
        raise SaveDecodeError(f"cannot decode slot {slot}: {err}") from err
    if failures:
        raise SaveDecodeError("; ".join(failures))
    entries = [describe(active, entry, root) for entry in decoded]
    # Only a decoded character reaches the cache; every failure above raised before this line.
    if entries:
        remember_identity(active, slot, entries[0])
    return {"corpus_root": str(root), "count": len(entries), "targets": entries}


def inventory(root: Path, container: str) -> dict:
    """Full sweep. Deliberately not the default: it is well past the 30s shell cap."""
    module = oracle()
    expected = expected_save_bytes()
    entries = []
    for path in eligible_saves(root, container, expected):
        for entry in occupied_slots(module, path, expected_bytes=expected):
            entries.append(describe(path, entry, root))
    return {"corpus_root": str(root), "count": len(entries), "targets": entries}


def render(result: dict) -> str:
    return "\n".join(
        [
            f"character  {result['name']}  RL{result['level']}",
            f"save       {result['save_file']}",
            f"slot       {result['slot']}   container .{result['container']}"
            + ("   SOURCE IS WRITABLE" if result["source_writable"] else "   source read-only"),
            f"draw       seed={result['seed']} draws={result['draws']} "
            f"({result['occupied_slots_in_file']} occupied slots in this file, "
            f"{result['eligible_files']} eligible files)",
        ]
    )


def selftest() -> int:
    ok = True

    def check(condition: bool, label: str) -> None:
        nonlocal ok
        if not condition:
            ok = False
        print(("  ok   " if condition else "  FAIL ") + label)

    expected = expected_save_bytes()
    check(expected == 0x1BA03D0, f"the product's size invariant is read live ({expected})")

    # The regression this tool shipped with, pinned so it cannot come back.
    #
    # Occupancy used to mean "the body decodes to a name and a level > 0". Deleting a character
    # clears the `USER_DATA010.active_slot` bitmap and leaves the body, so every deleted slot was
    # offered as a launch target. `--seed 2073568449` picked slot 8 of a container holding one
    # character, and the four-run conflict bisect that ran on that seed was therefore four
    # invalid-slot launches blamed on a DLL.
    #
    # Synthetic rather than corpus-dependent: two decodable slots, a bitmap that admits only the
    # first. A tool that reports both has lost the rule again.
    class _FakeModule:
        SLOT_COUNT = 2

        @staticmethod
        def active_slot_bitmap(_data: bytes) -> list[bool]:
            return [True, False]

        @staticmethod
        def decode_save_slot(_data: bytes, _path: Path, slot: int) -> dict:
            return {"decoded_fields": {"name": f"Live{slot}", "level": 10 + slot}}

        @staticmethod
        def name_empty_like(name: str) -> bool:
            return not name.strip()

    class _FakePath:
        @staticmethod
        def read_bytes() -> bytes:
            return b""

    picked = occupied_slots(_FakeModule, _FakePath)
    check(
        [entry["slot"] for entry in picked] == [0],
        "a slot the active_slot bitmap denies is NOT offered, even though its body decodes",
    )

    class _UnreadableBitmap(_FakeModule):
        @staticmethod
        def active_slot_bitmap(_data: bytes) -> None:
            return None

    class _NamedFakePath:
        """A path-shaped stand-in with a name, so a message can be checked for naming it."""

        def __str__(self) -> str:
            return "/selftest/sweep/ER0000.sl2"

        @staticmethod
        def read_bytes() -> bytes:
            return b""

    sweep_stderr = io.StringIO()
    with contextlib.redirect_stderr(sweep_stderr):
        skipped = occupied_slots(_UnreadableBitmap, _NamedFakePath())
    check(
        skipped == [],
        "an unreadable bitmap skips the file rather than falling back to the body-only test",
    )
    check(
        "/selftest/sweep/ER0000.sl2" in sweep_stderr.getvalue()
        and "bitmap" in sweep_stderr.getvalue(),
        "a sweep that skips an unreadable container says so on stderr, naming it",
    )

    # The launch gate's three answers, kept apart. On 2026-10-23 a save on an autofs CIFS share was
    # refused as "no occupied character" and decoded as "Vagabond" level 9 a minute later, because
    # an unreadable bitmap, a decode that raised and a clear bitmap bit all came back as one empty
    # list. `targeted` runs here against a real container of the product's size on disk, with the
    # decoder swapped for the fakes, so the file handling is the real code path.
    class _DecodeRaises(_FakeModule):
        @staticmethod
        def decode_save_slot(_data: bytes, _path: Path, _slot: int) -> dict:
            raise ValueError("no PlayerGameData candidate in this slot body")

    class _DecodeExits(_FakeModule):
        @staticmethod
        def decode_save_slot(_data: bytes, _path: Path, _slot: int) -> dict:
            raise SystemExit("slot USER_DATA000 not found in BND4 entries")

    class _EmptyBody(_FakeModule):
        @staticmethod
        def decode_save_slot(_data: bytes, _path: Path, _slot: int) -> dict:
            return {"layout": "unknown", "decoded_fields": {}}

    previous_cache_home = os.environ.get("XDG_CACHE_HOME")
    previous_oracle = globals()["oracle"]
    with tempfile.TemporaryDirectory(prefix="er-pick-save-selftest-") as scratch:
        gate_root = Path(scratch) / "corpus"
        gate_root.mkdir()
        gate_save = gate_root / "ER0000.sl2"
        with gate_save.open("wb") as handle:
            handle.truncate(expected)
        os.environ["XDG_CACHE_HOME"] = str(Path(scratch) / "cache")

        def run_targeted(fake_module, slot: int) -> tuple[dict | None, str | None]:
            globals()["oracle"] = lambda: fake_module
            try:
                return targeted(gate_root, "both", slot), None
            except RuntimeError as err:
                return None, str(err)
            finally:
                globals()["oracle"] = previous_oracle

        try:
            result, error = run_targeted(_UnreadableBitmap, 0)
            check(
                result is None
                and error is not None
                and str(gate_save) in error
                and "slot 0" in error
                and "bitmap" in error,
                f"the gate reports an unreadable bitmap as an error naming the path and slot ({error})",
            )
            result, error = run_targeted(_DecodeRaises, 0)
            check(
                result is None
                and error is not None
                and "no PlayerGameData candidate in this slot body" in error
                and str(gate_save) in error
                and "slot 0" in error,
                f"the gate reports a decode that raised with the exception's own text ({error})",
            )
            result, error = run_targeted(_DecodeExits, 0)
            check(
                result is None
                and error is not None
                and "USER_DATA000 not found" in error,
                "a decoder that raises SystemExit is reported, not allowed to end the process",
            )
            result, error = run_targeted(_EmptyBody, 0)
            check(
                result is None and error is not None and "decoded to no character" in error,
                "a slot the bitmap marks occupied whose body decodes to nothing is a decode failure",
            )
            result, error = run_targeted(_FakeModule, 1)
            check(
                error is None and result is not None and result["count"] == 0
                and result["targets"] == [],
                f"a slot whose bitmap bit is clear is still plain 'not occupied', no error ({error})",
            )
            check(
                not identity_cache_path().exists(),
                "no failure and no empty slot was written to the identity cache",
            )
            result, error = run_targeted(_FakeModule, 0)
            check(
                error is None
                and result is not None
                and [entry["name"] for entry in result["targets"]] == ["Live0"]
                and cached_identity(gate_save, 0) is not None,
                "an occupied slot still decodes, and that success is what the cache stores",
            )

            short = Path(scratch) / "short.sl2"
            short.write_bytes(b"\0" * 10)
            try:
                read_save(short, 20)
                short_error = None
            except SaveDecodeError as err:
                short_error = str(err)
            check(
                short_error is not None and "10 bytes" in short_error and str(short) in short_error,
                f"a read shorter than the product's size is reported as one ({short_error})",
            )
            try:
                read_save(Path(scratch) / "absent.sl2", None)
                missing_error = None
            except SaveDecodeError as err:
                missing_error = str(err)
            check(
                missing_error is not None and "absent.sl2" in missing_error,
                f"an unreadable file is reported as a read failure naming it ({missing_error})",
            )
        finally:
            globals()["oracle"] = previous_oracle
            if previous_cache_home is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = previous_cache_home

    try:
        root = resolve_root(None)
    except RuntimeError as err:
        print(f"  skip  {err}")
        print("selftest:", "PASS" if ok else "FAIL")
        return EXIT_OK if ok else EXIT_ERROR
    check(root.is_dir(), f"corpus root resolves to a real directory ({root})")

    both = eligible_saves(root, "both", expected)
    sl2 = eligible_saves(root, "sl2", expected)
    co2 = eligible_saves(root, "co2", expected)
    check(len(both) == len(sl2) + len(co2), "container filters partition the corpus exactly")
    check(len(both) > 0, f"the corpus has eligible saves ({len(both)})")
    check(
        all(STAGE_DIR_MARKER not in path.parts for path in both),
        "staged redirect copies are excluded from the source pool",
    )
    check(
        all(path.stat().st_size == expected for path in both),
        "every eligible save matches the product's exact byte length",
    )

    first = pick(root, "both", seed=1234)
    second = pick(root, "both", seed=1234)
    check(
        (first["save_file"], first["slot"]) == (second["save_file"], second["slot"]),
        "the same seed reproduces the same (file, slot) -- a broken run can be re-run",
    )
    others = {
        (pick(root, "both", seed=s)["save_file"], pick(root, "both", seed=s)["slot"])
        for s in range(20, 26)
    }
    check(len(others) > 1, f"different seeds pick different targets ({len(others)} distinct)")

    check(bool(first["name"]) and first["level"] > 0, "the pick carries a decoded identity")
    check(
        not oracle().name_empty_like(first["name"]),
        "the decoded name is not empty-like (the occupancy rule actually applied)",
    )

    only_sl2 = pick(root, "sl2", seed=7)
    check(only_sl2["container"] == "sl2", "a container filter is honoured by the pick")

    print("selftest:", "PASS" if ok else "FAIL")
    return EXIT_OK if ok else EXIT_ERROR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="save corpus root (default: $ER_SAVE_CORPUS_ROOT or ./save-files)")
    parser.add_argument(
        "--container",
        choices=("sl2", "co2", "both"),
        default="both",
        help="which save container to draw from (default: both -- ersc.dll is always loaded, "
        "and Seamless mode accepts both containers)",
    )
    parser.add_argument("--seed", type=int, help="RNG seed (default: random, always reported)")
    parser.add_argument("--all", action="store_true", help="inventory every valid target instead of picking")
    parser.add_argument(
        "--slot",
        type=int,
        help="decode only this slot and report it in the --all output shape. One decode instead of "
        "every occupied slot of every eligible file, which is what keeps a launch gate inside its "
        "step bound.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    try:
        root = resolve_root(args.root)
        if args.slot is not None:
            result = targeted(root, args.container, args.slot)
            print(json.dumps(result, indent=2) if args.json else f"{result['count']} valid targets")
            return EXIT_OK
        if args.all:
            result = inventory(root, args.container)
            print(json.dumps(result, indent=2) if args.json else f"{result['count']} valid targets")
            return EXIT_OK
        seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**31)
        result = pick(root, args.container, seed)
    # `OSError` as well: a `stat` of a save on a share that did not mount is a failure to report in
    # one line naming the path, not a traceback for the caller to quote.
    except (RuntimeError, OSError) as err:
        print(f"er-pick-save: {err}", file=sys.stderr)
        return EXIT_ERROR

    print(json.dumps(result, indent=2) if args.json else render(result))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
