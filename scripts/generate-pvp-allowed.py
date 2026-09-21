#!/usr/bin/env python3
"""Derive the effects this mod may put on a player while a peer is in the session.

An allowlist, not a blocklist, and the direction matters. There are 11354 rows in
``SpEffectParam``; the ones fit to travel to another player are a small, describable
subset, and everything else -- including a row nobody has looked at, and a row added by a
future patch -- must be refused by default. A blocklist gets that backwards: it passes
whatever it has not heard of, which is the whole catalog on the day the game updates.

Membership is one predicate over three properties the master catalog already carries:

``is_visuals_only``
    the effect changes nothing but what the character looks like. Imported from
    ``generate-effect-discriminator-catalogs.py`` rather than restated, so the catalog the
    selector scrolls and the set the gate allows cannot drift apart -- they are the same
    function.

not ``pvp.status_icon``
    the row has no ``iconId``, so the engine puts nothing in the recipient's status bar. A
    row that does is a state the game means the player to be told about, which is exactly
    what a mod must not hand them silently.

not ``pvp.cures_target``
    the row's ``stateInfo`` is not one the cure switch at 1.16.2 ``0x1404fc190`` accepts as
    a curer. A curer makes the engine walk the recipient's active-effect list and delete
    entries from it, so pushing one at a peer takes effects off them rather than adding one.

This file is the derived artifact. The rules live in the two generators; a hand edit here
is reverted by the next regeneration without anyone being told.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT_MASTER = REPO_ROOT / "data" / "effect-master-catalog.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "pvp-allowed-effects.json"

DISCRIMINATORS = REPO_ROOT / "scripts" / "generate-effect-discriminator-catalogs.py"

# The tags that disqualify an otherwise cosmetic row. Both are emitted by
# scripts/generate-effect-master-catalog.py, which reads them out of the regulation.
DISQUALIFYING_TAGS = ("pvp.status_icon", "pvp.cures_target")

SCHEMA_VERSION = 1


def load_discriminators():
    """Import the catalog generator as a module, for its `is_visuals_only`.

    By path because the file name has dashes in it and is a script rather than a package.
    Restating the predicate here instead would leave two copies to disagree, and the
    disagreement would be invisible: both would still produce a plausible list.
    """
    spec = importlib.util.spec_from_file_location("er_discriminators", DISCRIMINATORS)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import discriminators from {DISCRIMINATORS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(master: dict, is_visuals_only) -> dict:
    """Return the allowlist for one already-parsed master catalog."""
    source = master.get("source", {})
    allowed = []
    for effect in master.get("effects", []):
        if not is_visuals_only(effect):
            continue
        tags = effect.get("tags", ())
        if any(tag in tags for tag in DISQUALIFYING_TAGS):
            continue
        allowed.append(effect["id"])
    allowed.sort()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "pvp_allowed_effects",
        "source": {
            "param": source.get("param", ""),
            "binder_version": source.get("binder_version", ""),
            "regulation_file": source.get("regulation_file", ""),
        },
        "disqualifying_tags": list(DISQUALIFYING_TAGS),
        "effects": allowed,
    }


def selftest() -> int:
    """Prove the three terms on hand-built rows rather than on the shipped catalog.

    A test that reads the real catalog cannot fail when the derivation is wrong in the same
    direction the catalog is, so this one supplies rows whose verdict is known by hand.
    """
    module = load_discriminators()
    cosmetic = {"tags": ["presentation.vfx", "lifetime"], "fields": {"vfxId": 1}}
    cases = [
        ({"id": 1, **cosmetic}, True, "a purely cosmetic row is allowed"),
        (
            {"id": 2, "tags": ["presentation.vfx", "pvp.status_icon"], "fields": {"vfxId": 1}},
            False,
            "a status icon disqualifies a cosmetic row",
        ),
        (
            {"id": 3, "tags": ["presentation.vfx", "pvp.cures_target"], "fields": {"vfxId": 1}},
            False,
            "a curer disqualifies a cosmetic row",
        ),
        (
            {"id": 4, "tags": ["presentation.vfx", "stat.hp"], "fields": {"vfxId": 1}},
            False,
            "a stat change is not visuals-only",
        ),
        (
            {"id": 5, "tags": ["presentation.vfx", "combat.damage"], "fields": {"vfxId": 1}},
            False,
            "a damage change is not visuals-only",
        ),
        (
            {"id": 6, "tags": ["presentation.vfx", "ai.perception"], "fields": {"vfxId": 1}},
            False,
            "an ai change is not visuals-only",
        ),
        ({"id": 7, "tags": [], "fields": {}}, False, "a row with no vfx at all is not allowed"),
    ]
    master = {"source": {}, "effects": [case[0] for case in cases]}
    table = build(master, module.is_visuals_only)
    got = set(table["effects"])
    failures = []
    for effect, expected, why in cases:
        if (effect["id"] in got) != expected:
            failures.append(f"{why} -- id {effect['id']} came out {effect['id'] in got}")
    if table["effects"] != sorted(table["effects"]):
        failures.append("ids are not sorted")
    for failure in failures:
        print(f"selftest: {failure}", file=sys.stderr)
    print(f"selftest: {'failed' if failures else 'ok'} ({len(failures)} problems)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--master", type=pathlib.Path, default=DEFAULT_MASTER)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="derive and compare against the file on disk instead of writing it",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if not args.master.is_file():
        print(f"missing master catalog: {args.master}", file=sys.stderr)
        print(
            "run scripts/generate-effect-master-catalog.py first -- it needs the installed "
            "regulation.bin, which this script deliberately never reads itself",
            file=sys.stderr,
        )
        return 2

    module = load_discriminators()
    master = json.loads(args.master.read_text(encoding="utf-8"))
    table = build(master, module.is_visuals_only)
    body = json.dumps(table, indent=2, sort_keys=False) + "\n"

    if args.check:
        if not args.output.is_file():
            print(f"missing derived table: {args.output}", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != body:
            print(
                f"{args.output} is stale -- re-run {pathlib.Path(__file__).name} without "
                "--check",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output}: up to date ({len(table['effects'])} allowed effects)")
        return 0

    args.output.write_text(body, encoding="utf-8")
    total = len(master.get("effects", []))
    print(
        f"wrote {len(table['effects'])} allowed effects to {args.output} "
        f"({total - len(table['effects'])} of {total} rows refused with a peer present)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
