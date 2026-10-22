#!/usr/bin/env python3
"""Apply a Smithbox param CSV to the running game's params in memory, through param-lab.js.

    er-param-csv-apply.py <param.csv> <paramdef.xml> <param index> [--apply] [--revert]
                          [--columns name,name,...]

The CSV's columns after `Row ID,Row Name` are matched to the paramdef's fields in order, leaving
out padding (`dummy8`) and bitfields, which old CSV exports also leave out. When the counts
differ the script stops; `--columns` then names the field for each CSV column (`-` skips one).
It dumps every row live, decodes it with the paramdef, and prints the fields whose CSV value
differs from the game's; `--apply` writes exactly those. `--revert` puts back everything
param-lab wrote to this param. regulation.bin is never touched.

Needs a running `er-frida-watch.py --agent scripts/frida/param-lab.js
--command-file target/param-lab/cmd.json --log ~/.cache/er-frida/param-lab.jsonl`.
"""

import argparse
import csv
import json
import os
import struct
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import importlib.util  # noqa: E402

import er_run_lib  # noqa: E402

spec = importlib.util.spec_from_file_location("offsets", os.path.join(REPO, "scripts", "er-paramdef-offsets.py"))
offsets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offsets)

CMD = os.path.join(REPO, "target", "param-lab", "cmd.json")
LOG = os.path.expanduser("~/.cache/er-frida/param-lab.jsonl")
# Backstop for a reply that never comes; a loaded agent answers within a frame or two.
REPLY_TIMEOUT_SECONDS = 20
# First seq used when the command file is absent, clear of hand-written ones.
SEQ_START = 30000
FMT = {"u8": "<B", "s8": "<b", "u16": "<H", "s16": "<h", "u32": "<I", "s32": "<i", "f32": "<f"}


def reply_after(start, kinds):
    """The first agent message in the log past byte `start` whose kind is in `kinds`."""
    try:
        handle = open(LOG, encoding="utf-8")
    except OSError:
        return None
    with handle:
        handle.seek(start)
        for line in handle:
            try:
                payload = json.loads(line).get("message", {}).get("payload")
            except ValueError:
                continue  # the watcher is mid-line; the next write wakes us again
            if isinstance(payload, dict) and payload.get("kind") in kinds:
                return payload
    return None


def send(body, timeout=REPLY_TIMEOUT_SECONDS):
    """Write one command with the next seq and wait for param-lab's answer in the watcher's log.

    The watcher posts the command to the agent when the file changes, and appends the agent's
    reply to the log; the wait here is an inotify wait on the log's directory, bounded by
    `timeout` as a backstop for an agent that is not loaded.
    """
    try:
        with open(CMD, encoding="utf-8") as handle:
            seq = int(json.load(handle).get("seq", SEQ_START - 1)) + 1
    except (OSError, ValueError, AttributeError):
        seq = SEQ_START
    os.makedirs(os.path.dirname(CMD), exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    with open(CMD, "w", encoding="utf-8") as handle:
        json.dump(dict(body, seq=seq), handle)
    kinds = {body["op"], "error"}
    deadline = time.monotonic() + timeout
    with er_run_lib.DirectoryWatch(os.path.dirname(LOG)) as watch:
        if not watch.available:
            sys.exit("inotify is unavailable, so there is no way to wait for param-lab's reply")
        while True:
            reply = reply_after(start, kinds)
            if reply is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                sys.exit(
                    f"param-lab did not answer seq {seq} in {timeout}s: is er-frida-watch.py running with "
                    f"--agent scripts/frida/param-lab.js --command-file {CMD} --log {LOG}?"
                )
            watch.wait(remaining)
    if reply.get("kind") == "error":
        sys.exit(f"param-lab error: {reply['message']}")
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv")
    parser.add_argument("paramdef")
    parser.add_argument("index", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revert", action="store_true")
    parser.add_argument("--columns")
    args = parser.parse_args()

    if args.revert:
        print(send({"op": "revert", "index": args.index}))
        return

    text = open(args.paramdef, encoding="utf-8").read()
    allf = list(offsets.fields(text))
    size = allf[-1][0]
    plain = [(off, ty, name) for off, bit, bits, ty, name in allf[:-1] if bit is None and ty in FMT]
    by_name = {name: (off, ty, name) for off, ty, name in plain}

    with open(args.csv, newline="", encoding="utf-8-sig") as handle:
        table = list(csv.reader(handle))
    header, body = table[0], table[1:]
    ncol = len(header) - 2
    if args.columns:
        names = args.columns.split(",")
        if len(names) != ncol:
            sys.exit(f"--columns names {len(names)} fields for {ncol} CSV columns")
        cols = [None if n == "-" else by_name[n] for n in names]
    elif ncol == len(plain):
        cols = plain
    else:
        sys.exit(f"{ncol} CSV columns but {len(plain)} plain fields: {[n for _, _, n in plain]}; pass --columns")

    live = {r["id"]: bytes.fromhex(r["hex"]) for r in send({"op": "dump", "index": args.index, "size": size})["rows"]}
    edits = []
    missing = []
    for row in body:
        rid = int(row[0])
        if rid not in live:
            missing.append(rid)
            continue
        for col, raw in zip(cols, row[2:]):
            if col is None or raw == "":
                continue
            off, ty, name = col
            have = struct.unpack_from(FMT[ty], live[rid], off)[0]
            want = float(raw) if ty == "f32" else int(float(raw))
            same = abs(have - want) < 1e-4 if ty == "f32" else have == want
            if not same:
                edits.append({"id": rid, "off": off, "type": ty, "value": want, "name": name, "was": have})
    for e in edits:
        print(f"row {e['id']:>8}  {e['name']:<36} {e['was']:>12.6g} -> {e['value']:<12.6g}")
    print(f"{len(edits)} fields differ across {len({e['id'] for e in edits})} rows; rows not in game: {missing or 'none'}")
    if args.apply and edits:
        reply = send({"op": "write", "index": args.index,
                      "edits": [{k: e[k] for k in ("id", "off", "type", "value")} for e in edits]})
        print(f"written {reply['written']}, missing {reply['missing']}")


if __name__ == "__main__":
    main()
