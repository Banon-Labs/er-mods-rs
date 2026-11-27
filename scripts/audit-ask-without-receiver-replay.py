#!/usr/bin/env python3
"""Replay every past Stop boundary through the ask-without-receiver signal, and diff two replays.

`last_assistant_ask_without_receiver` halts a closing message that asks the user for an action or an
event while nothing armed would wake the agent. Its unit tests pin the shapes someone thought of; this
runs the shipped rule over the transcripts the agent actually wrote, so a rule change can be judged by
exactly which real verdicts it moves.

A boundary is every real user prompt in a transcript (the Stop hook fired on the turn before it) plus
the end of the transcript. At each one the signal sees the whole transcript up to that point, as it
does in production: its receiver scan reads every earlier Monitor and background launch, so a window
would invent halts. The signal's python body is lifted out of its shell wrapper and executed in this
process with `cupcake_turn_scan`'s transcript loader stubbed to hand back that prefix -- the same code
the hook runs, without a `bash` + `python3` spawn per boundary.

Usage:
    python3 scripts/audit-ask-without-receiver-replay.py --out before.json [transcript.jsonl ...]
    # edit the signal
    python3 scripts/audit-ask-without-receiver-replay.py --out after.json
    python3 scripts/audit-ask-without-receiver-replay.py --diff before.json after.json

With no transcripts it reads every `*.jsonl` of this repo under ~/.claude/projects/. Read-only on the
transcripts. A boundary present in only one replay (a transcript that grew in between) is counted, not
diffed.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_ask_without_receiver.sh"
TAG = "ASKNORECEIVER:"


def default_transcripts() -> list[str]:
    tdir = Path(os.path.expanduser("~/.claude/projects")) / str(REPO_ROOT).replace("/", "-")
    return sorted(glob.glob(str(tdir / "*.jsonl")))


def signal_body(path: Path) -> object:
    """The program between `python3 - <<'PY'` and the closing `PY`, compiled."""
    text = path.read_text(encoding="utf-8")
    start = text.index("\n", text.index("python3 - <<'PY'")) + 1
    return compile(text[start:text.rindex("\nPY")], str(path), "exec")


def run_once(code: object, scan, path: str, events: list[dict]) -> str:
    scan.hook_transcript = lambda *a, **k: path
    scan.load_events = lambda *a, **k: events
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        exec(code, {"__name__": "__main__"})
    except SystemExit:
        pass
    finally:
        sys.stdout = real_stdout
    return captured.getvalue().strip()


def replay(signal: Path, paths: list[str]) -> dict:
    os.environ["CUPCAKE_SIGNAL_REPO_ROOT"] = str(REPO_ROOT)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cupcake_turn_scan as scan

    load_events, is_prompt = scan.load_events, scan.is_real_user_prompt
    code = signal_body(signal)
    started = time.monotonic()
    boundaries, halts, errors = 0, {}, []
    for path in paths:
        events = load_events(path)
        cuts = [i for i, ev in enumerate(events) if i > 0 and is_prompt(ev)] + [len(events)]
        for cut in cuts:
            if cut == 0:
                continue
            boundaries += 1
            try:
                out = run_once(code, scan, path, events[:cut])
            except Exception as exc:  # a crash in the signal is fail-open in production
                errors.append(f"{Path(path).name}:{cut}: {exc!r}")
                continue
            if out.startswith(TAG):
                halts[f"{Path(path).name}:{cut}"] = out
    return {"boundaries": boundaries, "seconds": round(time.monotonic() - started, 1),
            "errors": errors, "halts": halts}


def diff(old_path: str, new_path: str) -> int:
    old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    oh, nh = old["halts"], new["halts"]
    print(f"boundaries: {old['boundaries']} -> {new['boundaries']}; halts: {len(oh)} -> {len(nh)}")
    for key in sorted(set(oh) - set(nh)):
        print(f"  no longer halts  {key}: {oh[key][len(TAG):][:200]}")
    for key in sorted(set(nh) - set(oh)):
        print(f"  now halts        {key}: {nh[key][len(TAG):][:200]}")
    for key in sorted(set(oh) & set(nh)):
        if oh[key] != nh[key]:
            print(f"  quotes another   {key}: {oh[key][len(TAG):][:120]!r} -> {nh[key][len(TAG):][:120]!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcripts", nargs="*")
    ap.add_argument("--signal", type=Path, default=SIGNAL)
    ap.add_argument("--out", help="write {boundaries, seconds, errors, halts} here as JSON")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="compare two --out files")
    args = ap.parse_args()
    if args.diff:
        return diff(*args.diff)
    paths = args.transcripts or default_transcripts()
    if not paths:
        print("no transcripts to replay", file=sys.stderr)
        return 1
    result = replay(args.signal, paths)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"{len(paths)} transcripts, {result['boundaries']} boundaries, {len(result['halts'])} halts, "
          f"{len(result['errors'])} errors, {result['seconds']} s")
    for key, out in result["halts"].items():
        print(f"  {key}: {out[len(TAG):][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
