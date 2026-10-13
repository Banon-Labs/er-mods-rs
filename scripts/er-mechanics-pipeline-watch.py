#!/usr/bin/env python3
"""Live view of an `er-mechanics-build-pipeline.sh` run: one colored line per step with a progress
bar, elapsed time and an estimate, the running step's latest line, and any warning or error.

It only reads the pipeline's own log and each step's log, so it can be started, stopped and
restarted at any time without touching the run.

    python3 scripts/er-mechanics-pipeline-watch.py --dir <dir> --prefix bleedhk
    python3 scripts/er-mechanics-pipeline-watch.py --dir <dir> --prefix bleedhk --once   # one frame
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
import time
from pathlib import Path

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, YELLOW, RED, CYAN, GREY = "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[90m"
BAR_WIDTH = 28
HEADER = re.compile(r"^== (\d\d:\d\d:\d\d) (\S+)(?: (.*))?$")
BRACKET = re.compile(r"^\[(\d+)/(\d+)\]")
FRACTION = re.compile(r"^(\d+)/(\d+)$")
#: A sub-phase's own counter inside a step's log, e.g. the loadout's "  40/166 weapons", and the
#: "name: ..." line that names the phase it counts.
COUNTER = re.compile(r"^\s*(\d+)/(\d+)\s+\w+")
PHASE = re.compile(r"^(?!warning)([a-z][\w -]{1,30}): ")
ALARM = re.compile(r"Traceback|Error|error:|FAIL", re.I)
WARN = re.compile(r"\bwarning\b", re.I)


def steps(p: Path) -> list[dict]:
    """The pipeline's steps in order: id, label, the logs that show its progress, and how to read
    them (`kind`)."""
    return [
        {"id": "1", "label": "build file", "logs": [], "kind": None},
        {"id": "2", "label": "embedding (no ash rows)", "logs": [f"{p}-embed0.log"], "kind": "bracket"},
        {"id": "3", "label": "wieldable weapons", "logs": [], "kind": None},
        {"id": "4", "label": "ash sweeps", "logs": [f"{p}-main.log", f"{p}-fixed.log"], "kind": "swept"},
        {"id": "5", "label": "uncovered weapons", "logs": [f"{p}-inbuild.log"], "kind": "swept"},
        {"id": "6", "label": "embedding (ash rows)", "logs": [f"{p}-embed.log"], "kind": "bracket"},
        {"id": "7a", "label": "true combos", "logs": [f"{p}-true-combos.jsonl"], "kind": "combos"},
        {"id": "7b", "label": "perfect block", "logs": [f"{p}-pb.log"], "kind": None},
        {"id": "7c", "label": "class roles", "logs": [f"{p}-roles.out"], "kind": None},
        {"id": "8", "label": "status-affinity sweep", "logs": [f"{p}-status.log"], "kind": "swept"},
        {"id": "8b", "label": "landed share (Stormcaller)", "logs": [f"{p}-landing.log"], "kind": "fraction"},
        {"id": "9", "label": "loadout", "logs": [f"{p}-loadout.log"], "kind": None},
        {"id": "10", "label": "upload", "logs": [], "kind": None},
    ]


def read_lines(path) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def wieldable_count(p: Path) -> int | None:
    text = " ".join(read_lines(f"{p}-wieldable.txt")).strip()
    return len(text.split(",")) if text else None


def progress(step: dict, p: Path) -> tuple[int, int | None, str]:
    """(done, total or None, latest meaningful line) from the step's logs."""
    done, total, last = 0, None, ""
    for log in step["logs"]:
        lines = read_lines(log)
        if step["kind"] == "bracket":
            for line in lines:
                m = BRACKET.match(line)
                if m:
                    done, total, last = int(m[1]), int(m[2]), line
        elif step["kind"] == "swept":
            # `[i/N] swept <weapon>` per log (`er-mechanics-ash-table.py`), summed over the step's
            # logs; a log from before the counter has bare `swept` lines and no total.
            numbered = [BRACKET.match(line) for line in lines]
            numbered = [m for m in numbered if m]
            if numbered:
                done += int(numbered[-1][1])
                total = (total or 0) + int(numbered[-1][2])
                last = numbered[-1].string
            else:
                swept = [line for line in lines if line.startswith("swept ")]
                done += len(swept)
                last = swept[-1] if swept else last
        elif step["kind"] == "fraction":
            for line in lines:
                m = FRACTION.match(line.strip())
                if m:
                    done, total, last = int(m[1]), int(m[2]), line
                elif "to land" in line:
                    last = line
                    n = re.match(r"(\d+) carriers", line)
                    total = int(n[1]) if n else total
        elif step["kind"] == "combos":
            done = len(lines)
        elif lines:
            # A step with no counter of its own: the latest sub-phase counter after the latest
            # phase line, else the latest line.
            last, phase = lines[-1], None
            for line in lines:
                m = COUNTER.match(line)
                if m:
                    done, total = int(m[1]), int(m[2])
                    last = f"{phase or ''} {line.strip()}".strip()
                elif PHASE.match(line):
                    phase, done, total = PHASE.match(line)[1], 0, None
                    last = line
    if step["id"] == "7a" or (step["id"] == "4" and total is None):
        total = wieldable_count(p)
    return done, total, last


def alarms(step: dict) -> list[tuple[str, str]]:
    out = []
    for log in step["logs"]:
        for line in read_lines(log):
            colour = RED if ALARM.search(line) else YELLOW if WARN.search(line) else None
            if colour and (colour, line) not in out:
                out.append((colour, line))
    return out[-3:]


def clock(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def bar(frac: float) -> str:
    full = int(frac * BAR_WIDTH)
    part = " ▏▎▍▌▋▊▉"[int((frac * BAR_WIDTH - full) * 8)] if full < BAR_WIDTH else ""
    return f"{GREEN}{'█' * full}{part}{GREY}{'·' * (BAR_WIDTH - full - len(part))}{RESET}"


def frame(d: Path, prefix: str, now: dt.datetime) -> str:
    p = d / prefix
    log = read_lines(f"{p}-pipeline.log")
    started = {}
    for line in log:
        m = HEADER.match(line)
        if m:
            started[m[2]] = dt.datetime.combine(now.date(), dt.time.fromisoformat(m[1]))
    plan = steps(p)
    order = [s["id"] for s in plan] + ["done"]
    reached = [i for i in order if i in started]
    current = reached[-1] if reached else None
    width = shutil.get_terminal_size((100, 30)).columns
    first = min(started.values()) if started else None
    finished = "done" in started
    failed = any(ALARM.search(line) for line in log)
    head = f"{BOLD}{prefix}{RESET} pipeline"
    if first:
        end = started.get("done", now)
        head += f"  {GREY}started {first:%H:%M:%S}, {clock((end - first).total_seconds())}{RESET}"
    head += f"  {GREEN}finished{RESET}" if finished else f"  {RED}failed{RESET}" if failed else ""
    out = [head, ""]
    running_detail = []
    for k, s in enumerate(plan):
        sid = s["id"]
        nxt = next((started[i] for i in order[k + 1:] if i in started), None)
        if sid in started:
            t0 = started[sid]
            t1 = nxt or (None if not finished else started["done"])
            is_running = t1 is None and sid == current and not failed
            el = ((t1 or now) - t0).total_seconds()
            done, total, last = progress(s, p)
            if is_running:
                mark, colour = "▶", YELLOW
            elif sid == current and failed:
                mark, colour = "✗", RED
            else:
                mark, colour = "✓", GREEN
            line = f" {colour}{mark}{RESET} {sid:>3}  {s['label']:<28}"
            if is_running and total:
                frac = min(1.0, done / total) if total else 0.0
                # A sub-phase counter (`COUNTER`) started part-way through the step, so the step's
                # elapsed time says nothing about what is left of it.
                eta = el / frac * (1 - frac) if frac > 0 and s["kind"] else None
                line += f" {bar(frac)} {done}/{total}  {clock(el)}" + (f"  {CYAN}~{clock(eta)} left{RESET}" if eta else "")
            elif is_running and done:
                line += f" {done} so far  {clock(el)}"
            else:
                line += f" {GREY}{clock(el)}{RESET}" if not is_running else f" {clock(el)}"
            out.append(line)
            if is_running or (sid == current and failed):
                warn = alarms(s)
                if last and last not in {a for _, a in warn}:
                    running_detail.append(f"      {GREY}{last[:width - 8]}{RESET}")
                running_detail += [f"      {c}{a[:width - 8]}{RESET}" for c, a in warn]
                out += running_detail
                running_detail = []
        elif current and order.index(current) > k:
            out.append(f" {GREY}–  {sid:>3}  {s['label']:<28} up to date, skipped{RESET}")
        else:
            out.append(f" {DIM}·  {sid:>3}  {s['label']}{RESET}")
    tail = [line for line in log if not HEADER.match(line)]
    if finished or failed:
        out += ["", *(f" {RED if ALARM.search(line) else ''}{line[:width - 2]}{RESET}" for line in tail[-8:])]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.once:
        print(frame(a.dir, a.prefix, dt.datetime.now()))
        return 0
    try:
        while True:
            text = frame(a.dir, a.prefix, dt.datetime.now())
            sys.stdout.write("\033[H\033[2J" + text + "\n")
            sys.stdout.flush()
            if "finished" in text.splitlines()[0] or "failed" in text.splitlines()[0]:
                # Hold the last frame: a kitty tab running this closes when it exits.
                input(f"\n{GREY}press Enter to close{RESET}")
                return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
