#!/usr/bin/env python3
"""Read event flags out of an ER0000.sl2/.co2 slot, located by live CSEventFlagMan block memory.

The save stores the event flag block memory as-is. Where that block sits inside a slot is found
here empirically, not from a layout table: a Frida agent sends 64-byte windows of the live block
memory at known block offsets, and the slot offset at which every window matches is the start of
the flag section. A flag's position comes from the same live manager (group -> block location,
`location * blockBytes + (id % perBlock) / 8`, bit `7 - id % 8`), so no table of flag groups is
needed either.

    python3 scripts/save-event-flag-read.py <frida-log.jsonl> <save> <slot> [<save> <slot> ...]

The log is `er-frida-watch.py --log` output; the last `state` report carrying `blockAnchor` and
`flagLocations` is used. Only reads files.
"""

import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("save_slot_oracle", HERE / "save-slot-oracle.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def last_report(log):
    report = None
    for line in open(log):
        payload = (json.loads(line).get("message") or {}).get("payload") or {}
        r = payload.get("report") or {}
        if r.get("blockAnchor") and r.get("flagLocations"):
            report = r
    if report is None:
        raise SystemExit(f"{log}: no state report with blockAnchor")
    return report


def section_start(body, windows):
    """Every slot offset where the first window matches and all the others agree."""
    first = windows[0]
    needle = bytes.fromhex(first["hex"])
    out = []
    i = body.find(needle)
    while i != -1:
        start = i - first["off"]
        if start >= 0 and all(
            body[start + w["off"]:start + w["off"] + len(w["hex"]) // 2] == bytes.fromhex(w["hex"])
            for w in windows[1:]
        ):
            out.append(start)
        i = body.find(needle, i + 1)
    return out


def main():
    log = sys.argv[1]
    report = last_report(log)
    windows = report["blockAnchor"]["windows"]
    pairs = sys.argv[2:]
    for k in range(0, len(pairs), 2):
        path, slot = pathlib.Path(pairs[k]), int(pairs[k + 1])
        body, _ = oracle.extract_slot(path.read_bytes(), slot)
        # Windows that hold only zeros match everywhere; require at least one non-zero window first.
        dense = sorted(windows, key=lambda w: -sum(1 for b in bytes.fromhex(w["hex"]) if b))
        dense = [w for w in dense if sum(1 for b in bytes.fromhex(w["hex"]) if b) >= 8]
        if not dense:
            print(f"{path} slot {slot}: every anchor window is nearly all zero; cannot locate the section")
            continue
        starts = section_start(body, dense)
        # A save written earlier differs from live memory in places, so fall back to a majority.
        if not starts:
            votes = {}
            for w in dense:
                needle = bytes.fromhex(w["hex"])
                if not any(needle):
                    continue
                i = body.find(needle)
                while i != -1:
                    votes[i - w["off"]] = votes.get(i - w["off"], 0) + 1
                    i = body.find(needle, i + 1)
            best = sorted(votes.items(), key=lambda kv: -kv[1])[:3]
            print(f"{path} slot {slot}: no all-window match; votes {[(hex(s), v) for s, v in best]}")
            starts = [best[0][0]] if best and best[0][1] >= 2 else []
        if len(starts) != 1:
            print(f"{path} slot {slot}: flag section start ambiguous or absent: "
                  f"{len(starts)} candidates {[hex(s) for s in starts[:10]]}")
            continue
        start = starts[0]
        print(f"{path} slot {slot}: flag section at slot+{start:#x}")
        for f in report["flagLocations"]:
            if f.get("mode") != 1:
                print(f"  {f['id']:>10}  not located live ({f})")
                continue
            byte = body[start + f["blockOffset"]]
            print(f"  {f['id']:>10}  {(byte >> f['bit']) & 1}   (live {(f['byte'] >> f['bit']) & 1})")


if __name__ == "__main__":
    main()
