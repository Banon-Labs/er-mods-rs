#!/usr/bin/env python3
"""Send the Steam lobby query ourselves and show what a nearby-only search actually asks for.

Answers the question the observers could not: what does a search shaped like Seamless's look like
on the wire, and what changes when our block key is added to it. Nothing here watches Seamless --
`lobby_publish`'s detour sits on the body of the function `SteamMatchMaking009`'s slot 4 points at
and has never been entered, while slot 20 on the same interface catches ersc's own `SetLobbyData`
writes. So this sends the query instead of waiting for one.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-lobby-search-proof.py

Three rounds, in order, because each one is the control for the next:

  1. unfiltered   -- every lobby this app can see. A count above zero proves the query path works,
                     which is the control every previous zero in this repo lacked.
  2. seamless     -- the advertisement filters, read off a real lobby in round 1 rather than
                     hard-coded, because Seamless 2.0.x hashes its key names per build.
  3. nearby       -- the same filters plus `er_invasion_warp_map` equal to one block, once per
                     block in the ring around the player. A string filter is an equality test, so
                     a set of places costs a query each; that is what the ring is for.

Read-only throughout: request, and the getters. No join, no create, no lobby write.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import queue
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "lobby-search-proof.js"
EVIDENCE_SCRIPT = REPO / "scripts" / "er-frida-evidence.py"

# Hard cap on any single blocked wait, matching every other wait in this repo.
WAIT_SECONDS = 30.0

# The game runtime cap, read from the one file that holds it rather than repeated here.
CAP_FILE = REPO / ".auto" / "runtime_timeout_cap_seconds"

# The value that identifies a Seamless advertisement lobby. The key that carries it is hashed per
# build, so the value is what is matched on and the key is recovered from the lobby itself.
ADVERTISEMENT_VALUE = "yknx3_seamless_master_lobby"

# `lobby_publish::LOBBY_MAP_KEY`.
LOBBY_MAP_KEY = "er_invasion_warp_map"

# `k_ELobbyDistanceFilterWorldwide`.
DISTANCE_WORLDWIDE = 3

# `k_ELobbyComparisonNotEqual`, from `steamclientpublic.h`. The comparison an existence test has to
# be built out of: there is no "has this key" filter, so the nearest thing is "this key's value is
# not the empty string", and whether a lobby that never set the key counts as empty is the entire
# question the existence probe answers.
COMPARISON_NOT_EQUAL = 3

# A key no lobby anywhere can be carrying, for the negative control.
ABSENT_KEY = "er_invasion_warp_key_that_nobody_publishes"

# The overworld areas whose index byte the engine packs as binary coded decimal, and whose block
# and region bytes are grid coordinates rather than a dungeon and a floor.
OVERWORLD_AREAS = (60, 61)

# `search_ring::MAX_RADIUS`.
MAX_RADIUS = 3


def cap_seconds() -> float:
    try:
        return float(CAP_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 300.0


def evidence_module():
    """`scripts/er-frida-evidence.py` as a module object.

    Loaded from its path because the filename carries hyphens and no `import` statement can spell
    it. Its `__name__` is not `__main__` here, so its argument parser does not run.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("er_frida_evidence", EVIDENCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {EVIDENCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_evidence(pid: int, messages: int, seconds: float) -> None:
    """Append what this session did, for the gate that reads it.

    The same call `scripts/er-frida-watch.py` makes on exit, and for the same reason: this tool is
    a Frida session that attaches to the game and receives messages, so it is a measurement, and a
    measurement that does not record itself leaves the next Rust edit refused for no reason. The
    count is taken here by the tool rather than typed by a caller.

    Nothing it can do may change this tool's exit code. A failed record costs a gate that has to be
    opened by measuring again; a failed record that turns a good run into a non-zero exit costs the
    belief that the run worked at all.
    """
    try:
        evidence_module().record(str(AGENT), int(pid), int(messages), float(seconds))
    except Exception as exc:  # noqa: BLE001 -- a recording failure must never fail the run
        print(f"could not record frida evidence ({exc}); the queries above still happened",
              file=sys.stderr)


def spell(area: int, block: int, region: int, index: int) -> str:
    """The engine's own spelling, which both sides of an equality filter have to agree on."""
    return f"m{area:02d}_{block:02d}_{region:02d}_{index:02d}"


def parse_raw(raw: int) -> tuple[int, int, int, int]:
    area = (raw >> 24) & 0xFF
    block = (raw >> 16) & 0xFF
    region = (raw >> 8) & 0xFF
    packed = raw & 0xFF
    index = (packed >> 4) * 10 + (packed & 0x0F) if area in OVERWORLD_AREAS else packed
    return area, block, region, index


def ring(raw: int, radius: int) -> list[str]:
    """Every block a nearby search should ask for, centre first then outward.

    Mirrors `er_invasion_warp_core::search_ring::ring`. A legacy dungeon has no neighbours worth
    asking for, because its block and region bytes encode a dungeon and a floor rather than a
    position, so stepping them lands somewhere unrelated; for those the ring is the centre alone.
    """
    area, block, region, index = parse_raw(raw)
    out = [spell(area, block, region, index)]
    if area not in OVERWORLD_AREAS:
        return out
    radius = min(radius, MAX_RADIUS)
    for distance in range(1, radius + 1):
        for dz in range(-distance, distance + 1):
            for dx in range(-distance, distance + 1):
                if abs(dx) != distance and abs(dz) != distance:
                    continue
                x, z = block + dx, region + dz
                if not (0 <= x <= 255 and 0 <= z <= 255):
                    continue
                out.append(spell(area, x, z, index))
    return out


def advertisement_filters(lobbies: list[dict]) -> tuple[list[list[str]], dict[str, int]]:
    """The key and value pairs a real Seamless advertisement is carrying, and how popular each is.

    Taken from a lobby rather than from a note, because 2.0.x hashes its key names and this repo
    has already paid once for treating a recorded list as current.

    The first draft intersected the keys of every advertisement and came back empty against 50 live
    lobbies, which is the answer for the wrong reason: the hash of a key name differs per Seamless
    build, so a host on another build carries the advertisement value under a name this build has
    never seen and the intersection deletes everything. What is wanted is the key that the largest
    group agrees on, so the modal pair wins and the tally is returned with it -- a key only half
    the hosts carry is a filter that hides the other half, and that has to be visible rather than
    inferred from an empty result.
    """
    ads = [lob for lob in lobbies if ADVERTISEMENT_VALUE in (lob.get("keys") or {}).values()]
    if not ads:
        return [], {}
    carriers: dict[str, int] = {}
    for lob in ads:
        for key, value in (lob.get("keys") or {}).items():
            if value == ADVERTISEMENT_VALUE:
                carriers[key] = carriers.get(key, 0) + 1
    modal = max(carriers, key=lambda k: carriers[k])
    cohort = [lob for lob in ads if (lob.get("keys") or {}).get(modal) == ADVERTISEMENT_VALUE]
    # Within the group that agrees on the advertisement key, keep the other keys they all spell
    # identically. A per-host value such as a session id or a password hash differs between them
    # and would narrow the query to one host, which is not what a search is for.
    shared: dict[str, str] = dict(cohort[0].get("keys") or {})
    for lob in cohort[1:]:
        keys = lob.get("keys") or {}
        for key in list(shared):
            if keys.get(key) != shared[key]:
                del shared[key]
    return [[key, value] for key, value in sorted(shared.items())], carriers


def selftest() -> int:
    source = AGENT.read_text(encoding="utf-8") if AGENT.is_file() else ""
    # Caelid's grid, chosen because a two-digit index would expose a packing mistake and this one
    # does not have one: the ring around an overworld tile is the centre plus eight neighbours.
    centre = 0x3C_33_24_00
    around = ring(centre, 1)
    dungeon = ring(0x0A_00_00_00, 3)
    checks = [
        ("the agent exists", AGENT.is_file()),
        ("it runs on the game's own tick", "XInputGetState" in source),
        ("it sends the query rather than watching for one", "RequestLobbyList" in source),
        ("it reads answers through the call handle", "GetAPICallResult" in source),
        ("it never joins a lobby", "_JoinLobby" not in source and "JoinLobby" not in source),
        ("the centre is asked for first", around[0] == "m60_51_36_00"),
        ("one ring is nine places", len(around) == 9),
        ("the ring is the centre and its neighbours", set(around) == {
            spell(60, x, z, 0) for x in (0x32, 0x33, 0x34) for z in (0x23, 0x24, 0x25)
        }),
        ("three rings is forty nine places", len(ring(centre, 3)) == 49),
        ("a radius above the cap is clamped", ring(centre, 9) == ring(centre, MAX_RADIUS)),
        ("a dungeon has no neighbours", dungeon == ["m10_00_00_00"]),
        ("a two-digit index survives the packing", parse_raw(0x3C_33_24_12)[3] == 12),
        ("the map key matches the one we publish", LOBBY_MAP_KEY in source),
    ]
    bad = sum(0 if ok else 1 for _, ok in checks)
    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    print("selftest: ok" if not bad else f"selftest: {bad} check(s) failed")
    return 0 if not bad else 1


def drain(events: "queue.Queue", deadline: float, wanted: int) -> list[dict]:
    """Collect result messages until `wanted` have arrived or the window closes."""
    got: list[dict] = []
    while len(got) < wanted:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            payload = events.get(timeout=min(remaining, WAIT_SECONDS))
        except queue.Empty:
            continue
        kind = payload.get("kind")
        if kind == "result":
            got.append(payload["result"])
            record = payload["result"]
            print(f"  <- {record['label']:<28} matching={record['matching']:<5} "
                  f"read={record['reported']}")
        else:
            print(f"  .. {payload.get('line')}")
    return got


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radius", type=int, default=1,
                        help="how many tiles out the nearby ring reaches (capped at 3)")
    parser.add_argument("--block",
                        help="the centre, as a raw block id (0x3c343500) or a spelling "
                             "(m60_52_53_00). A raw id still builds the ring around it; a "
                             "spelling asks for that one place. Use it when the in-process "
                             "getter cannot answer, which it cannot before the world is up.")
    parser.add_argument("--seconds", type=float, default=cap_seconds())
    parser.add_argument("--json", help="write the whole answer set to this path")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.seconds > cap_seconds():
        print(f"refusing: {args.seconds}s is above the canonical cap of {cap_seconds()}s")
        return 2

    import frida

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    procs = [p for p in device.enumerate_processes() if "eldenring" in p.name.lower()]
    if not procs:
        print("no eldenring.exe visible to the frida server")
        return 3
    session = device.attach(procs[0].pid)
    script = session.create_script(AGENT.read_text(encoding="utf-8"))

    events: queue.Queue = queue.Queue()
    seen = {"messages": 0}
    started = time.monotonic()

    def on_message(message, _data):
        if message.get("type") == "send":
            seen["messages"] += 1
            events.put(message["payload"])
        else:
            print(f"  !! {message}")

    script.on("message", on_message)
    script.load()

    report = script.exports_sync.report()
    if not report.get("ready"):
        print(f"refusing to query: the agent is inert -- {report.get('why')}")
        record_evidence(procs[0].pid, seen["messages"], time.monotonic() - started)
        session.detach()
        return 4
    print(f"attached to pid {procs[0].pid}; steam exports resolved")

    deadline = time.monotonic() + args.seconds

    # Round one. Unfiltered, worldwide, so an empty answer means nobody is advertising rather than
    # "your filter excluded everyone" -- the ambiguity every previous zero in this repo carried.
    script.exports_sync.query("unfiltered control", {
        "distance": DISTANCE_WORLDWIDE, "resultCount": 50,
    })
    control = drain(events, deadline, 1)
    if not control:
        print("the unfiltered query never came back; nothing below is interpretable")
        record_evidence(procs[0].pid, seen["messages"], time.monotonic() - started)
        session.detach()
        return 5

    lobbies = control[0].get("lobbies") or []
    ads = [lob for lob in lobbies if ADVERTISEMENT_VALUE in (lob.get("keys") or {}).values()]
    print(f"\nunfiltered: {control[0]['matching']} lobbies, {len(ads)} of them "
          f"seamless advertisements")
    for lob in ads[:4]:
        print(f"  advertisement {lob['id']} owner={lob['owner']} members={lob['members']} "
              f"keys={lob['keyCount']}")
        for key, value in sorted((lob.get("keys") or {}).items()):
            print(f"      {key} = {value}")

    filters, carriers = advertisement_filters(lobbies)
    if carriers:
        print("\nkeys carrying the advertisement value, and how many hosts spell it that way:")
        for key, count in sorted(carriers.items(), key=lambda kv: -kv[1]):
            print(f"      {count:>3} hosts  {key}")
    if filters:
        print("\nthe shape those hosts agree on, which is what rounds two and three ask for:")
        for key, value in filters:
            print(f"      {key} = {value}")
    else:
        print("\nno seamless advertisement was visible, so the search shape cannot be read off "
              "one. Rounds two and three go out with the block key alone.")

    # Round two. The advertisement shape on its own, which is the search Seamless itself would
    # issue if it issued one through this interface.
    script.exports_sync.query("seamless shape", {
        "distance": DISTANCE_WORLDWIDE, "resultCount": 50, "strings": filters,
    })
    # Round two and a half: can one query ask "is anybody at all carrying a block id", so a search
    # can skip the whole ring when the answer is no?
    #
    # Steam has no "this key exists" filter, so the test has to be built out of `NotEqual` against
    # the empty string. Whether that works at all depends on something undocumented -- how the
    # backend treats a lobby that never set the key -- so it is asked with both controls around it
    # rather than on its own:
    #
    #   positive  a key every advertisement demonstrably carries. If this comes back ~0 the
    #             operator is not an existence test and the probe below means nothing.
    #   negative  a key nobody anywhere publishes. If this comes back non-zero then a missing key
    #             counts as "not equal to empty", so the operator matches everything and again
    #             cannot answer the question.
    #
    # Only if positive is high and negative is zero does the middle reading carry information.
    probe_key = filters[0][0] if filters else None
    if probe_key:
        script.exports_sync.query("exists: a key every host carries", {
            "distance": DISTANCE_WORLDWIDE, "resultCount": 50,
            "strings": [[probe_key, "", COMPARISON_NOT_EQUAL]],
        })
    script.exports_sync.query("exists: any block id at all", {
        "distance": DISTANCE_WORLDWIDE, "resultCount": 50,
        "strings": [[LOBBY_MAP_KEY, "", COMPARISON_NOT_EQUAL]],
    })
    script.exports_sync.query("exists: a key nobody carries", {
        "distance": DISTANCE_WORLDWIDE, "resultCount": 50,
        "strings": [[ABSENT_KEY, "", COMPARISON_NOT_EQUAL]],
    })
    probes = 3 if probe_key else 2
    # Round three. The same, once per block, which is the nearby-only search.
    here = script.exports_sync.block()
    centre_raw = None
    if args.block:
        centre = args.block
        places = [centre]
        print(f"\ncentre given on the command line: {centre}")
    elif here:
        centre_raw = here["raw"]
        places = ring(centre_raw, args.radius)
        print(f"\ncentre read from the player: {here['name']} "
              f"(raw {centre_raw:#010x}); ring of {len(places)} at radius {args.radius}")
    else:
        places = []
        print("\nthe player's block could not be read, so there is no nearby ring to ask for")

    for place in places:
        script.exports_sync.queryBlock(f"nearby {place}", {
            "distance": DISTANCE_WORLDWIDE, "resultCount": 50, "strings": filters,
        }, place)

    answers = drain(events, deadline, 1 + probes + len(places))

    print("\n=== what each query asked for and what came back ===")
    rows = [control[0]] + answers
    for record in rows:
        strings = record["spec"].get("strings") or []
        # A filter is `[key, value]` or `[key, value, comparison]`; the existence probes use the
        # third form, and unpacking two names off three values raised `ValueError` right at the
        # summary, after every query had already been answered.
        shown = ", ".join(
            f"{pair[0]}{'!=' if len(pair) > 2 and pair[2] == COMPARISON_NOT_EQUAL else '='}"
            f"{pair[1] or '<empty>'}"
            for pair in strings
        ) or "(no filters)"
        print(f"  {record['label']:<28} matching={record['matching']:<5} {shown}")

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps({"control": control, "answers": answers}, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")

    record_evidence(procs[0].pid, seen["messages"], time.monotonic() - started)
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
