#!/usr/bin/env python3
"""Warp to each candidate spawn point in turn and measure what happens to the player.

This is the ground-truth harness the offline audits are scored against. For every target it
warps, waits for the load to finish, then dwells and polls the player's own vitals -- so the
verdict comes from the engine rather than from a guess about geometry:

  hp                  `CSChrDataModule+0x138`, the field `GetHpRate` divides
  onSolidGround       `CSChrPhysicsModule+0x92`, the field `ChrIns::IsStandingOnSolidGround` reads
  position            `CSChrPhysicsModule+0x70`, physics space

`onSolidGround` is the discriminator, not death. A point can drop the player a long way into
water, a load boundary, or a bottomless area that reloads them rather than killing them, and a
point can also sit five metres up with a harmless landing. Recording all three separates those.

Each row is written as it completes, so the run can be read while it is still going and a crash
loses only the point in flight.

Usage:
    uv run --with frida python3 scripts/er-warp-sweep.py targets.jsonl --out results.jsonl
    python3 scripts/er-warp-sweep.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import sys
import time

ENDPOINT = os.environ.get("ER_FRIDA_ENDPOINT", "127.0.0.1:27042")
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frida", "warp-to-point.js")
BLOCK_NAME = re.compile(r"^m(\d{2})_(\d{2})_(\d{2})_(\d{2})$")

# How far the player may settle from the request and still count as "arrived there".
ARRIVAL_RADIUS = 12.0
# A drop larger than this during the dwell is a fall, not a step down.
FALL_METRES = 10.0
# `CSSessionManagerImp+0x10`, the value `TriggerAreaReload` requires before entering the
# map re-entry, and `BlockId` none.
PROTOCOL_STATE_IN_GAME = 6
BLOCK_ID_NONE = "0xffffffff"
# Ceiling on any single blocking wait for an agent message.
MAX_WAIT_SECONDS = 30.0
# How long past one sample period to wait for the per-frame pass before reading directly. Above
# 1 so ordinary frame jitter does not trigger a direct read beside every frame sample.
FRAME_GRACE = 1.5


class VitalsStream:
    """Vitals samples sent by the agent from the game's own per-frame pass.

    The agent reads the player on the main thread at a frame boundary and `send()`s a sample at
    most once per period; this side blocks on the message queue for the next one. When no frame
    sample arrives within `FRAME_GRACE` periods -- the pass does not run during a load or a
    softlocked session -- it takes one direct `vitals()` read instead, tagged `source: rpc`, so a
    stalled pass shows up as such rather than as a gap.

    Warp reports from the same hook are kept in `warps`, so the frame a warp executed on is known
    and samples from before it can be told apart.
    """

    def __init__(self, script, messages: queue.Queue, period: float):
        self.script = script
        self.messages = messages
        self.period = period
        self.warps: list[dict] = []
        self.errors: list[str] = []

    def start(self) -> None:
        self.script.exports_sync.stream(int(self.period * 1000))

    def stop(self) -> None:
        self.script.exports_sync.unstream()

    def drain(self) -> None:
        """Drop queued samples, keeping warp reports."""
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                return
            self._take(message)

    def _take(self, message: dict):
        """Record a non-sample message; return the vitals payload if it was a sample."""
        if message.get("type") == "error":
            self.errors.append(str(message.get("description")))
            print(f"agent error: {message.get('description')}", file=sys.stderr, flush=True)
            return None
        if message.get("type") != "send":
            return None
        payload = message.get("payload") or {}
        if payload.get("type") == "warp":
            self.warps.append(payload["report"])
        elif payload.get("type") == "vitals":
            return payload["vitals"]
        return None

    def next(self) -> dict:
        """The next sample: a frame sample if one arrives in time, else one direct read."""
        deadline = time.monotonic() + min(self.period * FRAME_GRACE, MAX_WAIT_SECONDS)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                break
            v = self._take(message)
            if v is not None:
                return v
        return self.script.exports_sync.vitals()

    def wait_warp(self, seconds: float):
        """The report of the warp just queued, or `None` after `seconds` (capped)."""
        deadline = time.monotonic() + min(seconds, MAX_WAIT_SECONDS)
        while not self.warps:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                return None
            self._take(message)
        return self.warps.pop(0)


def block_id(name):
    if isinstance(name, int):
        return name
    if name.startswith("0x"):
        return int(name, 16)
    m = BLOCK_NAME.match(name)
    if not m:
        raise ValueError(f"not a block name: {name}")
    a, b, r, i = (int(g) for g in m.groups())
    return (a << 24) | (b << 16) | (r << 8) | i


def classify(arrival, samples, target_y):
    """Verdict for one dwell, from the samples alone."""
    if not samples:
        return "no-samples"
    hps = [s["hp"] for s in samples if s.get("hp") is not None]
    if hps and min(hps) <= 0:
        return "died"
    ys = [s["player"]["y"] for s in samples if s.get("player")]
    grounded = [s["onSolidGround"] for s in samples if s.get("onSolidGround") is not None]
    if ys and arrival is not None and (arrival - min(ys)) > FALL_METRES:
        return "fell"
    if grounded and not any(grounded):
        return "never-grounded"
    # Health wobbles by a few points every second from regeneration and chip, so any change at
    # all labelled 58 of 64 points `took-damage` and buried the signal. Only a loss worth a
    # twentieth of the bar counts.
    if hps and (max(hps) - min(hps)) > max(1, int(0.05 * max(hps))):
        return "took-damage"
    return "stood"


def selftest():
    failures = []
    cases = {"m30_10_00_00": 0x1E0A0000, "m60_50_38_00": 0x3C322600}
    for name, want in cases.items():
        if block_id(name) != want:
            failures.append(f"block_id({name}) wrong")

    def s(hp, y, ground):
        return {"hp": hp, "player": {"x": 0.0, "y": y, "z": 0.0}, "onSolidGround": ground}

    checks = [
        ("died", classify(10.0, [s(500, 10.0, True), s(0, 10.0, True)], 10.0)),
        ("fell", classify(10.0, [s(500, 10.0, False), s(500, -80.0, False)], 10.0)),
        ("never-grounded", classify(10.0, [s(500, 10.0, False), s(500, 9.5, False)], 10.0)),
        ("stood", classify(10.0, [s(500, 10.0, True), s(500, 10.0, True)], 10.0)),
        ("took-damage", classify(10.0, [s(400, 10.0, True), s(500, 10.0, True)], 10.0)),
        # Regeneration and chip move health by a few points every second; that is standing
        # still, not damage, and calling it damage labelled 58 of 64 points on the second run.
        ("stood", classify(10.0, [s(2717, 10.0, True), s(2637, 10.0, True)], 10.0)),
        ("no-samples", classify(10.0, [], 10.0)),
    ]
    for want, got in checks:
        if got != want:
            failures.append(f"classify: want {want}, got {got}")
    # A fall that ends in death is reported as death, because that is the stronger fact.
    got = classify(10.0, [s(500, 10.0, False), s(0, -400.0, False)], 10.0)
    if got != "died":
        failures.append(f"fall-then-death: want died, got {got}")

    # The readiness gate. Each clause is one way the sweep softlocked the game on 2026-09-21,
    # so each is tested by removing it alone from an otherwise-ready reading.
    ready = {
        "protocolState": PROTOCOL_STATE_IN_GAME,
        "spawnFlag": 0,
        "block": "0x1e0a0000",
        "player": {"x": 0.0, "y": 0.0, "z": 0.0},
        "hp": 500,
    }
    if not ready_to_warp(ready):
        failures.append("ready_to_warp rejected a ready reading")
    for field, value in (
        ("protocolState", 7),
        ("spawnFlag", 1),
        ("block", BLOCK_ID_NONE),
        ("player", None),
        ("hp", 0),
    ):
        bad = dict(ready)
        bad[field] = value
        if ready_to_warp(bad):
            failures.append(f"ready_to_warp accepted {field}={value}")
    if not os.path.exists(AGENT):
        failures.append(f"agent missing: {AGENT}")
    failures.extend(stream_selftest())
    for f in failures:
        print(f"FAIL {f}")
    print("selftest: " + ("FAILED" if failures else "ok"))
    return 1 if failures else 0


def stream_selftest():
    """`VitalsStream` against a stand-in script, no game needed."""
    failures = []

    class Exports:
        def __init__(self):
            self.direct_reads = 0

        def vitals(self):
            self.direct_reads += 1
            return {"source": "rpc", "frame": 9, "t": 0}

        def stream(self, period_ms):
            return 0

        def unstream(self):
            return 0

    class Script:
        def __init__(self):
            self.exports_sync = Exports()

    def sample(frame):
        return {"type": "send", "payload": {"type": "vitals", "vitals": {"frame": frame, "source": "frame"}}}

    q: queue.Queue = queue.Queue()
    script = Script()
    stream = VitalsStream(script, q, 0.02)

    q.put(sample(3))
    got = stream.next()
    if got.get("source") != "frame" or got.get("frame") != 3:
        failures.append(f"next() did not return the queued frame sample: {got}")
    got = stream.next()
    if got.get("source") != "rpc" or script.exports_sync.direct_reads != 1:
        failures.append(f"next() on a silent pass did not fall back to one direct read: {got}")

    q.put(sample(4))
    q.put({"type": "send", "payload": {"type": "warp", "report": {"kicked": True, "frame": 5}}})
    q.put(sample(5))
    report = stream.wait_warp(1.0)
    if not report or report.get("frame") != 5:
        failures.append(f"wait_warp did not return the warp report: {report}")
    got = stream.next()
    if got.get("frame") != 5:
        failures.append(f"the sample after the warp report was lost: {got}")

    q.put(sample(6))
    q.put({"type": "send", "payload": {"type": "warp", "report": {"kicked": True, "frame": 7}}})
    stream.drain()
    if not q.empty() or len(stream.warps) != 1:
        failures.append("drain() should empty the queue and keep the warp report")
    stream.warps.clear()
    if stream.wait_warp(0.02) is not None:
        failures.append("wait_warp returned a report from an empty queue")
    return failures


def connect(messages: queue.Queue):
    import frida

    dev = frida.get_device_manager().add_remote_device(ENDPOINT)
    pid = None
    for proc in dev.enumerate_processes():
        if proc.name.lower() == "eldenring.exe":
            pid = proc.pid
            break
    if pid is None:
        sys.exit(f"eldenring.exe not visible through {ENDPOINT}")
    session = dev.attach(pid)
    script = session.create_script(open(AGENT, encoding="utf-8").read())
    script.on("message", lambda m, d: messages.put(m))
    script.load()
    return session, script, pid


def ready_to_warp(v):
    """Is the game in a state where issuing another warp is safe?

    Four conditions, and the session one is why this function exists. `SetupMapReentry` writes
    `protocolState = WaitReentryToMap` as its first statement, so the gate is self-latching: a
    warp issued before the engine has driven the session back to `InGame` skips the re-entry and
    kicks the stage anyway. That softlocks the game with `protocolState = 7`, `mainPlayer = 0`
    and the spawn slot still armed -- measured live 2026-09-21 on the thirteenth warp of a sweep
    that did not check.
    """
    return (
        v.get("protocolState") == PROTOCOL_STATE_IN_GAME
        and v.get("spawnFlag") == 0
        and v.get("block") not in (None, BLOCK_ID_NONE)
        and v.get("player") is not None
        and (v.get("hp") or 0) > 0
    )


def wait_until_ready(stream, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = stream.next()
        if ready_to_warp(v):
            return v
    return None


def wait_for_arrival(stream, script, want_block, target, timeout):
    """Poll until the player is alive and the session is back `InGame` in the destination.

    The `protocolState == InGame` clause is the whole point, and leaving it out invents falls.
    While the load is still running, `GetPhysicsPosition` hands back the block-local coordinate
    the warp requested; the instant the session reaches `InGame` it hands back the real physics
    coordinate instead. Traced live 2026-09-21 on `m60_39_50_00` #5: `y = 755.52` with
    `ground=False` and `state=7` for a second and a half, then `state=6` and `y = 3.52` with
    `ground=True` in the very next sample. A dwell that began before that flip recorded a 752 m
    plunge that never happened -- which is exactly `arrival_y`, and why all fourteen of the first
    sweep's "falls" landed at `y` near zero regardless of map.

    Matching on position is therefore not enough either: during the load the position matches
    the request perfectly, because it is the request.

    The warp's own report comes first: it names the frame the warp ran on, and a sample from an
    earlier frame describes the place being left. A warp the agent refused never arrives, so it
    returns `None` at once rather than after the whole timeout.
    """
    deadline = time.monotonic() + timeout
    report = stream.wait_warp(timeout)
    if report is None or not report.get("kicked"):
        if report is not None:
            print(f"  warp not kicked: {json.dumps(report)}", flush=True)
        return None
    warp_frame = report.get("frame", 0)
    want = "0x%x" % want_block
    agreed = 0
    while time.monotonic() < deadline:
        v = stream.next()
        if v.get("frame", 0) < warp_frame:
            continue
        if (
            v.get("block") == want
            and v.get("protocolState") == PROTOCOL_STATE_IN_GAME
            and v.get("player")
            and v.get("hp") is not None
            and v["hp"] > 0
        ):
            p = v["player"]
            world = script.exports_sync.convert(want_block, target[0], target[1], target[2])
            if world is None:
                continue
            # All three axes, and twice in a row. `protocolState` reaches `InGame` up to one
            # sample before the position stops being reported block-local, so a check that
            # only looks at x and z can pass inside that window and then record the frame
            # switch as a plunge -- which is what four of the second sweep's points did, each
            # with `grounded` at 1.0 for the whole dwell, an impossible reading for a real
            # fall. Requiring y to agree too closes the window, and requiring two consecutive
            # agreements means a single straddling sample cannot open it again.
            if (
                math.dist((p["x"], p["y"], p["z"]), (world["x"], world["y"], world["z"]))
                <= ARRIVAL_RADIUS
            ):
                agreed += 1
                if agreed >= 2:
                    v["target_physics"] = world
                    return v
            else:
                agreed = 0
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="?", help="jsonl with map/index/x/y/z/yaw per line")
    ap.add_argument("--out", help="jsonl to append results to")
    ap.add_argument(
        "--dwell", type=float, default=10.0, help="maximum seconds to watch after landing"
    )
    ap.add_argument(
        "--settle",
        type=float,
        default=1.0,
        help="leave the dwell once the player has been on solid ground this long",
    )
    ap.add_argument("--arrival-timeout", type=float, default=120.0)
    ap.add_argument(
        "--ready-timeout",
        type=float,
        default=90.0,
        help="seconds to wait for the session to return to InGame before aborting the sweep",
    )
    ap.add_argument("--rate", type=float, default=5.0, help="samples per second")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.targets:
        ap.error("need a targets jsonl (or --selftest)")

    targets = [json.loads(line) for line in open(args.targets, encoding="utf-8") if line.strip()]
    if args.limit:
        targets = targets[: args.limit]

    if args.rate <= 0:
        ap.error("--rate must be positive")
    messages: queue.Queue = queue.Queue()
    session, script, pid = connect(messages)
    stream = VitalsStream(script, messages, 1.0 / args.rate)
    stream.start()
    print(f"attached pid={pid}; {len(targets)} targets, dwell {args.dwell}s", flush=True)
    out = open(args.out, "a", encoding="utf-8") if args.out else None

    for n, t in enumerate(targets, 1):
        bid = block_id(t["map"])
        yaw = t.get("yaw_rad")
        if yaw is None:
            yaw = math.radians(t.get("yaw_deg", 0.0))
        target = (t["x"], t["y"], t["z"])
        before = wait_until_ready(stream, args.ready_timeout)
        if before is None:
            last = script.exports_sync.vitals()
            print(
                f"[{n}/{len(targets)}] ABORT -- the game never came back to a warpable state "
                f"(protocolState={last.get('protocolState')} spawnFlag={last.get('spawnFlag')} "
                f"block={last.get('block')})",
                flush=True,
            )
            if out:
                out.write(json.dumps({"map": t["map"], "index": t.get("index"), "verdict": "aborted-not-ready"}) + "\n")
                out.flush()
            break
        # Anything queued so far describes the place being left.
        stream.drain()
        stream.warps.clear()
        script.exports_sync.warp(bid, t["x"], t["y"], t["z"], yaw)
        landed = wait_for_arrival(stream, script, bid, target, args.arrival_timeout)
        row = {
            "map": t["map"],
            "index": t.get("index"),
            "x": t["x"],
            "y": t["y"],
            "z": t["z"],
            "expected": t.get("expected"),
            "from_block": before.get("block"),
        }
        if landed is None:
            row["verdict"] = "never-arrived"
            row["samples"] = 0
        else:
            arrival_y = landed["player"]["y"]
            samples = []
            end = time.monotonic() + args.dwell
            # The dwell is a ceiling, not a duration. A good landing latches
            # `onSolidGround` within a few frames and there is nothing further to learn from
            # it, so the loop leaves as soon as the ground has held for `--settle` seconds;
            # only a point that never grounds spends the full budget. That is what makes a
            # 123-point sweep minutes rather than an hour, without weakening the one signal
            # a short fixed dwell would miss: a fall is exactly the case that does not exit
            # early.
            #
            # Settling is timed on the agent's clock (`t`, taken when the sample was read), so
            # queue latency on this side cannot stretch or shrink it.
            settled_since = None
            while time.monotonic() < end:
                v = stream.next()
                samples.append(v)
                read_at = v.get("t", 0) / 1000.0
                if v.get("onSolidGround") and (v.get("hp") or 0) > 0:
                    settled_since = settled_since if settled_since is not None else read_at
                    if read_at - settled_since >= args.settle:
                        break
                else:
                    settled_since = None
            row["arrival_y"] = arrival_y
            row["verdict"] = classify(arrival_y, samples, t["y"])
            row["samples"] = len(samples)
            ys = [s["player"]["y"] for s in samples if s.get("player")]
            row["min_y"] = min(ys) if ys else None
            row["drop"] = (arrival_y - min(ys)) if ys else None
            hps = [s["hp"] for s in samples if s.get("hp") is not None]
            row["min_hp"] = min(hps) if hps else None
            grounded = [s["onSolidGround"] for s in samples if s.get("onSolidGround") is not None]
            row["grounded_fraction"] = (
                round(sum(1 for g in grounded if g) / len(grounded), 3) if grounded else None
            )
        print(f"[{n}/{len(targets)}] {row['map']} #{row.get('index')} -> {row['verdict']}", flush=True)
        if out:
            out.write(json.dumps(row) + "\n")
            out.flush()

    if out:
        out.close()
    stream.stop()
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
