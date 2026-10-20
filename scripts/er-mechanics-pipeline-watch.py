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
import json
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
#: The loadout's stages (`er-mechanics-combo-loadout.Stages`): the plan once, each stage with the
#: seconds it took the last time it ran (null when it never has), then a line as each one starts.
STAGE_PLAN = re.compile(r"^stages: (\[.*\])$")
STAGE = re.compile(r"^stage (\d+)/(\d+): (.+) \((\d+)s in\)$")
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
        {"id": "9", "label": "loadout", "logs": [f"{p}-loadout.log"], "kind": "stages"},
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
        elif lines and not (step["kind"] == "stages" and any(STAGE.match(x) for x in lines)):
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


def stage_progress(lines: list[str], el: float) -> dict | None:
    """Where a staged log is, `el` seconds after its step started, or None before its first
    stage line: `frac` of the step done, `left` seconds (None with no timings to go on), the
    stage's `k`, `n` and `name`, and a `detail` line. Stages are weighed by how long each took the
    last time; one never timed counts as the mean of the timed ones."""
    plan, cur, sub, latest = [], None, None, None
    for line in lines:
        m = STAGE_PLAN.match(line)
        if m:
            try:
                plan = [(str(n), s) for n, s in json.loads(m[1])]
            except (ValueError, TypeError):
                plan = []
        elif m := STAGE.match(line):
            cur, sub, latest = (int(m[1]), int(m[2]), m[3], int(m[4])), None, None
        elif cur and (c := COUNTER.match(line)):
            sub = (int(c[1]), int(c[2]), line.strip())
        elif cur and line.strip() and not WARN.search(line):
            latest = line.strip()
    if not cur:
        return None
    k, n, name, at = cur
    timed = [s for _, s in plan if s is not None]
    mean = sum(timed) / len(timed) if timed else 1.0
    w = [mean if s is None else s for _, s in plan] if len(plan) == n else [1.0] * n
    if sum(w) <= 0:
        w = [1.0] * n
    last = dict(plan).get(name)
    in_stage = max(0.0, el - at)
    sub_frac = sub[0] / sub[1] if sub and sub[1] else None
    if sub_frac:
        frac_in, left_in = sub_frac, in_stage / sub_frac * (1 - sub_frac)
    elif last is not None:
        frac_in, left_in = (min(0.95, in_stage / last) if last > 0 else 0.95), max(0.0, last - in_stage)
    else:
        frac_in, left_in = 0.0, None
    frac = (sum(w[:k - 1]) + frac_in * w[k - 1]) / sum(w)
    later = [s for _, s in plan[k:]] if len(plan) == n else []
    left = left_in + sum(mean if s is None else s for s in later) if left_in is not None and timed else None
    detail = f"{name}: " + (sub[2] if sub else latest or "") + f"  {clock(in_stage)} in"
    if left_in is not None:
        detail += f", ~{clock(left_in)} left" if sub_frac or in_stage <= (last or 0) else ""
    if last and not sub_frac and in_stage > last:
        detail += f", over the {clock(last)} it took last time"
    elif last and not sub_frac:
        detail += f" (took {clock(last)} last time)"
    return {"frac": min(1.0, frac), "left": left, "k": k, "n": n, "name": name, "detail": detail}


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
            sp = stage_progress(read_lines(s["logs"][0]), el) if is_running and s["kind"] == "stages" else None
            if sp:
                line += (f" {bar(sp['frac'])} stage {sp['k']}/{sp['n']} {sp['name']}  {clock(el)}"
                         + (f"  {CYAN}~{clock(sp['left'])} left{RESET}" if sp["left"] is not None else ""))
                last = sp["detail"]
            elif is_running and total:
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


def selftest() -> int:
    fails = []

    def check(cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    plan = 'stages: [["rows", 10], ["whiff", 100], ["write", 10]]'
    check(stage_progress(["lead-in: 0 rows", plan], 5.0) is None, "no stage line yet: no stage progress")
    sp = stage_progress([plan, "stage 1/3: rows (0s in)", "stage 2/3: whiff (10s in)", "whiff: 400 rows",
                         "  100/400 rows"], 30.0)
    # rows 10 done, whiff a quarter of 100: 35 of 120. Its counter says 20 s in for a quarter.
    check(abs(sp["frac"] - 35 / 120) < 1e-9 and sp["k"] == 2 and sp["name"] == "whiff",
          "the bar weighs each stage by its last time and fills the current one by its counter")
    check(abs(sp["left"] - (60 + 10)) < 1e-9 and "100/400 rows" in sp["detail"],
          "time left is the counter's rate for this stage plus the later stages' last times")
    sp = stage_progress([plan, "stage 2/3: whiff (10s in)", "opponent pool: rebuilding"], 140.0)
    check(sp["frac"] < 110 / 120 and "over the 1m40s it took last time" in sp["detail"]
          and "opponent pool: rebuilding" in sp["detail"], "a stage past its last time says so and shows its latest line")
    sp = stage_progress(['stages: [["rows", null], ["whiff", null]]', "stage 2/2: whiff (3s in)"], 5.0)
    check(abs(sp["frac"] - 0.5) < 1e-9 and sp["left"] is None, "with no timings each stage is one of N, nothing left estimated")
    print(f"{len(fails)} failed")
    return 1 if fails else 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
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
