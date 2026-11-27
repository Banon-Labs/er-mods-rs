#!/usr/bin/env python3
"""Replay every past Stop boundary through the idle-hold signal, with what ran stood in from the
transcript, and diff two replays.

`last_assistant_idle_hold` halts a paused closing message that names nothing the session has running
(UNNAMEDPAUSE, or an `UNNAMED:` line after VERBOSEPAUSE / IDLEHOLD). What ran is measured from the
process table at Stop (scripts/cupcake_session_tasks.py), and a past process table cannot be replayed.
So this stands it in from the transcript prefix: every Bash task the main transcript shows still
running (`cupcake_turn_scan.running_background_tasks`, no recency cutoff) and every agent it shows
working (`subagent_states`, running or waiting). That over-reports -- a task whose notification never
arrived reads as running forever -- and it misses a subagent's own shells, which the main transcript
never shows; both are said in the output. `--live off` stands in "nothing measurable" instead, which
must reproduce the signal as it was before the naming check.

A boundary is every real user prompt in a transcript (the Stop hook fired on the turn before it) plus
the end of the transcript, as in scripts/audit-ask-without-receiver-replay.py, whose loader stub this
copies: the signal's python body is executed in this process with `cupcake_turn_scan`'s transcript
loader handing back the prefix.

Usage:
    python3 scripts/audit-idle-hold-replay.py --out after.json [--newest 40] [--live transcript|off]
    python3 scripts/audit-idle-hold-replay.py --signal <older copy of the signal> --live off --out before.json
    python3 scripts/audit-idle-hold-replay.py --diff before.json after.json
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
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_idle_hold.sh"


def default_transcripts(newest: int) -> list[str]:
    tdir = Path(os.path.expanduser("~/.claude/projects")) / str(REPO_ROOT).replace("/", "-")
    return sorted(glob.glob(str(tdir / "*.jsonl")), key=os.path.getmtime)[-newest:]


def signal_body(path: Path) -> object:
    """The program between `python3 - <<'PY'` and the closing `PY`, compiled."""
    text = path.read_text(encoding="utf-8")
    start = text.index("\n", text.index("python3 - <<'PY'")) + 1
    return compile(text[start:text.rindex("\nPY")], str(path), "exec")


def stand_in(scan, tasks, events: list[dict]) -> dict:
    """A session report built from the transcript prefix (see the module docstring)."""
    shells = [tasks.Shell(t.task_id, 0, 0.0, 0.0, [], "main", "", t.command)
              for t in scan.running_background_tasks(events) if t.kind == "bash"]
    agents = [(a, "main") for a in scan.subagent_states(events) if a.state in ("running", "waiting")]
    return {"session": "replay", "claude_pid": 0, "shells": shells, "agents": agents, "descriptions": {}}


def run_once(code: object, scan, tasks, path: str, events: list[dict], live: str) -> str:
    scan.hook_transcript = lambda *a, **k: path
    scan.load_events = lambda *a, **k: events
    tasks.hook_report = (lambda *a, **k: stand_in(scan, tasks, events)) if live == "transcript" else (
        lambda *a, **k: None)
    os.environ["CUPCAKE_SIGNAL_EVENT"] = json.dumps({"hook_event_name": "Stop", "transcript_path": path})
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


def replay(signal: Path, paths: list[str], live: str) -> dict:
    os.environ["CUPCAKE_SIGNAL_REPO_ROOT"] = str(REPO_ROOT)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cupcake_session_tasks as tasks
    import cupcake_turn_scan as scan

    load_events, is_prompt = scan.load_events, scan.is_real_user_prompt
    code = signal_body(signal)
    started = time.monotonic()
    boundaries, tags, errors = 0, {}, []
    for path in paths:
        events = load_events(path)
        cuts = [i for i, ev in enumerate(events) if i > 0 and is_prompt(ev)] + [len(events)]
        for cut in cuts:
            boundaries += 1
            try:
                out = run_once(code, scan, tasks, path, events[:cut], live)
            except Exception as exc:  # a crash in the signal is fail-open in production
                errors.append(f"{Path(path).name}:{cut}: {exc!r}")
                continue
            if out:
                turn = scan.last_text_turn(scan.split_turns(events[:cut]))
                runs = turn.text_runs if turn else []
                tags[f"{Path(path).name}:{cut}"] = {"out": out, "closing": (runs[-1] if runs else "")[:300]}
    return {"live": live, "boundaries": boundaries, "seconds": round(time.monotonic() - started, 1),
            "errors": errors, "tags": tags}


def kind(out: str) -> str:
    head = out.split("\n", 1)[0].split(":", 1)[0]
    return head + ("+UNNAMED" if "\nUNNAMED:" in out else "")


def diff(old_path: str, new_path: str) -> int:
    old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    new = json.loads(Path(new_path).read_text(encoding="utf-8"))
    ot, nt = old["tags"], new["tags"]
    print(f"boundaries: {old['boundaries']} -> {new['boundaries']}; tagged: {len(ot)} -> {len(nt)}")
    for key in sorted(set(ot) | set(nt)):
        a, b = ot.get(key, {}).get("out", ""), nt.get(key, {}).get("out", "")
        if a != b:
            print(f"  {key}: {kind(a) if a else '-'} -> {kind(b) if b else '-'}")
            print(f"      closing: {(nt.get(key) or ot.get(key))['closing'][:200]!r}")
            if "UNNAMED" in b:
                print(f"      report:  {b.split('UNNAMED', 1)[1][:200]!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcripts", nargs="*")
    ap.add_argument("--newest", type=int, default=40)
    ap.add_argument("--signal", type=Path, default=SIGNAL)
    ap.add_argument("--live", choices=("transcript", "off"), default="transcript")
    ap.add_argument("--out", help="write {live, boundaries, seconds, errors, tags} here as JSON")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="compare two --out files")
    args = ap.parse_args()
    if args.diff:
        return diff(*args.diff)
    paths = args.transcripts or default_transcripts(args.newest)
    if not paths:
        print("no transcripts to replay", file=sys.stderr)
        return 1
    result = replay(args.signal, paths, args.live)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for t in result["tags"].values():
        counts[kind(t["out"])] = counts.get(kind(t["out"]), 0) + 1
    print(f"{len(paths)} transcripts, {result['boundaries']} boundaries, live={args.live}, "
          f"{len(result['errors'])} errors, {result['seconds']} s; tags: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
