#!/usr/bin/env python3
"""Does Seamless's item-handler closure chain stay the same object while the session state moves?

`er_invasion_warp` caches the closure address the needle scan found and revalidates it on every
`resolve_session`. What it revalidates WITH decides whether the cache works at all: run
br-20260917-025609-d24a re-ran `identifies_a_session` there and paid 90 full address-space walks in
one run, because that predicate asks "does this look like a session" -- a state-sensitive question
-- rather than "is this still the same object".

This measures the assumption the cheaper check rests on. Read the chain repeatedly from one fixed
closure address and report the pointers beside the live state. If the pointers hold while the state
changes, revalidating by pointer is sound.

Sampling is tied to the game's own frame tick, not to a timer: `scripts/frida/pad-frames.js` counts
`XInputGetState` calls, so a sample every N frames is a sample the game actually advanced through.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

REPO = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = 24
FRAMES_BETWEEN_SAMPLES = 40


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        agent = (REPO / "scripts/frida/find-ersc-owner-closure.js").read_text()
        assert "chain: chain" in agent, "the agent exposes no chain re-read"
        assert "rw-" in agent and "rwx" in agent, "the scan must cover both protections"
        print("selftest: ok")
        return 0

    import frida

    from er_pad_frames import Pad  # noqa: E402

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    pid = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"][0]
    sess = dev.attach(pid)

    finder = sess.create_script(
        (REPO / "scripts/frida/find-ersc-owner-closure.js").read_text())
    finder.on("message", lambda m, _d: None)
    finder.load()
    pad = Pad(sess)

    found = finder.exports_sync.scan()
    if not found.get("ok") or not found.get("believable"):
        print(f"no closure to follow: {found.get('why', found)}")
        return 2
    first = found["believable"][0]
    at = first["closure"]
    print(f"closure {at} -> owner {first['owner']} session {first['session']} "
          f"state {first['state']}", flush=True)

    owners: set[str] = set()
    sessions: set[str] = set()
    states: set[str] = set()
    failures = 0
    for _ in range(args.samples):
        # Advance the game, not the wall clock.
        pad.tap(0, hold_frames=0, gap_frames=FRAMES_BETWEEN_SAMPLES)
        sample = finder.exports_sync.chain(at)
        if not sample.get("ok"):
            failures += 1
            continue
        owners.add(sample["owner"])
        sessions.add(sample["session"])
        states.add(str(sample["state"]))

    print(f"samples={args.samples} unreadable={failures} "
          f"owners={sorted(owners)} sessions={sorted(sessions)} states={sorted(states)}",
          flush=True)
    if failures:
        print("VERDICT: the chain became unreadable during the run -- a pointer check would also "
              "have failed, so the cache genuinely has to re-walk")
        return 3
    if len(owners) != 1 or len(sessions) != 1:
        print("VERDICT: the object MOVED -- caching its address is wrong and the walk has to repeat")
        return 4
    if len(states) == 1:
        print("VERDICT: pointers held, but the state never changed, so this run does not "
              "distinguish a pointer check from a predicate check")
        return 5
    print("VERDICT: the pointers held constant across " + str(len(states)) +
          " distinct states -- revalidating by pointer is sound, and the predicate was what "
          "threw the cache away")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
