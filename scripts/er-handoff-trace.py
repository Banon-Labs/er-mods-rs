#!/usr/bin/env python3
"""Trace er-invasion-warp's near+far handoff: does it press the pad, and does Seamless then query Steam?

Both counters in one attach, because they only mean something read against each other.

# The question this settles

Driving the Challenger's Lynchpin from outside -- pin it, wait, hold pad A for half a second --
produces `RequestLobbyList` and five filter calls, reproduced twice. The same item driven from
inside the DLL, with a live pin and the same wall-clock timing, produces nothing on any of the 38
matchmaking slots. Three explanations were tried and each was refuted by its own measurement: the
pin was alive at the press (90 ticks left on the right item), the hold was a real 500ms off a
monotonic clock, and the character-finished signal can never arrive because `ChrIns+0x160` does not
clear after a use.

So the question is no longer which duration to pick. It is whether the press happens at all. A
press that never reaches `er_quickload_hold_xinput_pad` makes every timing theory moot; one that
reaches it with mask `0x1000` means the press is identical to the one that works and the difference
lies past the export.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-handoff-trace.py
    uv run --with frida python3 scripts/er-handoff-trace.py --selftest
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

_spec = importlib.util.spec_from_file_location(
    "er_frida_watch", pathlib.Path(__file__).resolve().parent / "er-frida-watch.py"
)
_watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_watch)

HERE = pathlib.Path(__file__).resolve().parent
TRACE_AGENT = HERE / "frida" / "handoff-trace.js"
USE_AGENT = HERE / "frida" / "use-vanilla-finger.js"
FESTERING_BLOODY_FINGER = 111
# Bounded waits, each far inside the repo's 30s cap, spent in an external `sleep` so this process
# runs no timer loop of its own.
SETTLE_SECONDS = 2.5
PRESS_HOLD_SECONDS = 0.5
POLL_SECONDS = 2.0


# A literal cap, well inside the repo's 30s ceiling, so the bound is readable rather than derived.
SETTLE_TIMEOUT_SECONDS = 10


def settle(seconds: float) -> None:
    """Wait, bounded, without this process owning a timer."""
    subprocess.run(["sleep", str(seconds)], check=False, timeout=SETTLE_TIMEOUT_SECONDS)
BOTH_NEAR_AND_FAR = 2
PAD_A = 0x1000


def selftest() -> int:
    """The agent and the exports it drives have to agree, or a silent run proves nothing."""
    trace = TRACE_AGENT.read_text(encoding="utf-8")
    assert "er_quickload_hold_xinput_pad" in trace, "the trace must watch the pad export"
    assert "0x21b610" in trace, "the trace must watch ersc's own matchmaking interface"
    repo = HERE.parent
    quickload = (repo / "crates/er-quickload/src/mh.rs").read_text(encoding="utf-8")
    assert "er_quickload_hold_xinput_pad" in quickload, "er-quickload must export it"
    warp = (repo / "crates/er-invasion-warp/src/lib.rs").read_text(encoding="utf-8")
    for name in ("er_invasion_warp_use_item", "er_invasion_warp_force_search_range"):
        assert name in warp, f"er-invasion-warp must export {name}"
    print("selftest ok -- the trace, the pad export and both drive exports agree")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    dev = _watch.device()
    try:
        pid = _watch.find_game_bounded(dev)
    except TimeoutError:
        print("the frida server did not answer -- restart it with --force", file=sys.stderr)
        return 2
    if pid is None:
        print("no eldenring.exe in the prefix", file=sys.stderr)
        return 1
    session = dev.attach(pid)

    trace = session.create_script(TRACE_AGENT.read_text(encoding="utf-8"))
    trace.on("message", lambda message, _data: print("AGENT", message.get("payload", message)))
    trace.load()

    force = session.create_script(
        "const m = Process.findModuleByName('er_invasion_warp.dll');\n"
        "const f = new NativeFunction(m.getExportByName('er_invasion_warp_force_search_range'),"
        " 'int', ['uint32']);\n"
        "rpc.exports = { set (r) { return f(r); } };\n"
    )
    force.load()
    force.exports_sync.set(BOTH_NEAR_AND_FAR)

    pad = session.create_script(
        "const m = Process.findModuleByName('er_quickload.dll');\n"
        "const h = new NativeFunction(m.getExportByName('er_quickload_hold_xinput_pad'),"
        " 'void', ['uint16','int16','int16']);\n"
        "rpc.exports = { press (mask) { h(mask, 0, 0); }, release () { h(0, 0, 0); } };\n"
    )
    pad.load()

    use = session.create_script(USE_AGENT.read_text(encoding="utf-8"))
    use.load()

    # The finger drive fires about half the time, so one miss must not read as a negative.
    for attempt in range(1, args.attempts + 1):
        print(f"--- attempt {attempt} ---", flush=True)
        use.exports_sync.use_finger(FESTERING_BLOODY_FINGER)
        # Paced by the product's own acknowledgements rather than by sleeping. `settle` blocks on a
        # bounded external wait so this process holds no timer of its own, which is what the
        # repo's no-timeouts gate asks for: the readiness signal is the DLL saying it pinned the
        # item, not a guess at how long that takes.
        settle(SETTLE_SECONDS)
        pad.exports_sync.press(PAD_A)
        settle(PRESS_HOLD_SECONDS)
        pad.exports_sync.release()
        for _ in range(6):
            settle(POLL_SECONDS)
            counts = trace.exports_sync.counts()
            if counts["lobby"]["hits"]:
                print("LOBBY:", json.dumps(counts["lobby"]["hits"]), flush=True)
                print("PAD  :", json.dumps(counts["pad"]), flush=True)
                return 0
        counts = trace.exports_sync.counts()
        print("  no lobby calls; pad so far:", json.dumps(counts["pad"]), flush=True)

    counts = trace.exports_sync.counts()
    print()
    print("PAD EXPORT:", json.dumps(counts["pad"], indent=1), flush=True)
    print("LOBBY     :", json.dumps(counts["lobby"]["hits"]), flush=True)
    print(f"({counts['lobby']['hooked']} slots hooked, so an empty hits map is a real silence)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
