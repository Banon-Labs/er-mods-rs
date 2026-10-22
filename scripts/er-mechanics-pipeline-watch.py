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
ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")
HEADER = re.compile(r"^== (\d\d:\d\d:\d\d) (\S+)(?: (.*))?$")
BRACKET = re.compile(r"^\[(\d+)/(\d+)\]")
FRACTION = re.compile(r"^(\d+)/(\d+)$")
#: A sub-phase's own counter inside a step's log, e.g. the loadout's "  40/166 weapons", and the
#: "name: ..." line that names the phase it counts.
COUNTER = re.compile(r"^\s*(\d+)/(\d+)\s+(\w+)")
#: The rows a pool counter line carries beside its chunks (`  12/143 weapons, 900/8640 rows`). Its
#: chunks run largest first, so the rows, not the chunks, are the share of the work done.
ROWS = re.compile(r"\b(\d+)/(\d+) rows\b")
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
        {"id": "7a", "label": "true combos", "logs": [f"{p}-true-combos.err"], "kind": None},
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
            # `[i/N] weapon` per weapon, then a later phase's own counter (`COUNTER`, the
            # embedding's powerstance pairs) after it: the latest of the two is where the step is.
            for line in lines:
                m = BRACKET.match(line) or COUNTER.match(line)
                if m:
                    done, total, last = int(m[1]), int(m[2]), line.strip()
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
    if step["id"] == "4" and total is None:
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
            r = ROWS.search(line)
            sub = (int(r[1]), int(r[2]), line.strip(), "rows") if r else (int(c[1]), int(c[2]), line.strip(), c[3])
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
    return {"frac": min(1.0, frac), "left": left, "k": k, "n": n, "name": name, "detail": detail,
            "count": (sub[0], sub[1], sub[3]) if sub else None, "latest": latest, "in": in_stage,
            # Seconds the later stages took last time, the ones never timed left out, and whether
            # this stage's own share is known (a counter or a last time to go on).
            "later_known": sum(s for s in later if s is not None), "known": left_in is not None,
            "stage_left": left_in}


#: A counter's latest gap may run this many times the median of its earlier gaps before the line
#: says it has stopped, and it must have shown this many gaps before that is judged at all. The
#: loadout's pools run their largest chunks first, so its gaps shrink and the median over-states
#: the next one: the mark comes late rather than wrong.
STALL_FACTOR = 5
STALL_MIN_GAPS = 3


class Pulse:
    """When the running step's counter last advanced, as seen by polling it, and the gaps between
    its advances. `see` once a poll with the counter's `key` (which stage it counts) and `value`;
    a new key starts over. `resolution` is the poll period: a gap is never read as shorter than
    one, since two advances within one poll look like one."""

    def __init__(self, resolution: float = 0.0):
        self.resolution = resolution
        self.key = self.value = self.at = None
        #: False while `at` is only when this watcher first looked, not when the counter moved.
        self.exact = False
        self.gaps: list[float] = []

    def see(self, key, value, now: float, written: float | None = None) -> None:
        """`written`: when the log last changed, given only when its last line is the counter's."""
        if key != self.key:
            # A key seen change under a running watcher started within one poll; the first one
            # seen started whenever it did, unless the log's last write is the counter itself.
            exact = written is not None or self.key is not None
            self.key, self.value, self.gaps = key, value, []
            self.at, self.exact = (min(now, written) if written is not None else now), exact
            return
        if value != self.value:
            if self.exact:
                self.gaps.append(max(now - self.at, self.resolution))
            self.value, self.at, self.exact = value, now, True

    def threshold(self) -> float | None:
        """Seconds without an advance that mark the counter as stopped, None with too few gaps."""
        if len(self.gaps) < STALL_MIN_GAPS:
            return None
        return STALL_FACTOR * max(sorted(self.gaps)[len(self.gaps) // 2], self.resolution)

    def age_text(self, now: float) -> str:
        """`last step 31s ago`, or `no progress for 14m` in red past `threshold`. Empty before the
        first `see`."""
        if self.at is None:
            return ""
        age = max(0.0, now - self.at)
        # Whole minutes past ten: the seconds say nothing then, and the line has 80 columns.
        ago = ("" if self.exact else "≥") + (clock(age) if age < 600 or age >= 3600 else f"{int(age) // 60}m")
        lim = self.threshold()
        if lim is not None and age > lim:
            return f"{RED}no progress for {ago}{RESET}"
        return f"last step {ago} ago"

    def text(self, label: str, now: float) -> str:
        """`label, last step 31s ago`, or `label, no progress for 14m` in red past `threshold`."""
        age = self.age_text(now)
        return f"{label}, {age}" if age else label


def written_at(paths, needle: str) -> float | None:
    """The newest of `paths`' modification times when its last non-blank line holds `needle`
    (the counter's `done/total`), so that write is the counter's own; None otherwise."""
    for path in paths:
        lines = [x for x in read_lines(path) if x.strip()]
        if lines and needle in lines[-1]:
            try:
                return os.path.getmtime(path)
            except OSError:
                return None
    return None


def pulse_text(pulse: Pulse, step: dict, p: Path, sp: dict | None, now: float, el: float | None = None,
               unit: bool = True) -> str:
    """Feed `pulse` the running `step`'s counter and return a short line of where the step is and
    whether it is moving: the stage (or step) and how long it has been in it, its counter and the
    counter's age: `interrupt 30s 2492/8215 rows, last step 31s ago`. A staged step counts by its
    stage's counter (`sp`, from `stage_progress`), timed from the stage's own line; any other step
    by its `progress`, timed by `el`. A stage before its first counter line shows its name and time
    only. Every loadout stage writes one (`stage_counters`). Without `unit` the counter's word is
    left out (`interrupt 30s 2492/8215, ...`), for a line cut to 80 columns. Empty for a step with
    no log to read."""
    if not step["logs"]:
        return ""
    if sp:
        c = sp["count"]
        key, value = (step["id"], sp["k"], sp["name"]), (c[0] if c else sp["latest"])
        head = f"{sp['name']} {clock(sp['in'])}" if sp.get("in") is not None else sp["name"]
        label = (f"{head} {c[0]}/{c[1]}" + (f" {c[2]}" if unit else "")) if c else head
        needle = f"{c[0]}/{c[1]}" if c else None
        counted = c is not None
    else:
        done, total, last = progress(step, p)
        key, value = (step["id"],), (done, last)
        head = f"{step['label']} {clock(el)}" if el is not None else step["label"]
        label = f"{head} {done}/{total}" if total else f"{head} {done}" if done else head
        needle = f"{done}/{total}" if total else None
        counted = True
    pulse.see(key, value, now, written_at(step["logs"], needle) if needle else None)
    return pulse.text(label, now) if counted else label


#: A counter line as `COUNTER` reads it, written as an f-string with its fields blanked (`counter_fstring`):
#: `  {}/{} rows`, `{}/{} weapon affinities, ...`. A `stage {}/{}: ` line is no counter.
COUNTER_SHAPE = re.compile(r"^\s*\{\}/\{\}\s+(?:[A-Za-z_]|\{\})")
#: Method names of the builtin containers, strings and paths: an attribute call by one of these
#: names (`rows.append`, `path.read_text`) is never resolved to a script's own function of that name.
_BUILTIN_ATTRS = frozenset(n for t in (dict, list, set, str, bytes, Path) for n in dir(t))
#: Seconds a combo-loadout stage may take without a counter (the request of 2026-10-20: a stage over
#: about 30 s must show a counter that moves).
UNCOUNTED_STAGE_SECONDS = 30.0


def counter_fstring(node) -> bool:
    """Whether the f-string `node` writes a `COUNTER` line."""
    import ast
    text = "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                   for v in node.values)
    return bool(COUNTER_SHAPE.match(text))


def stage_counters(loadout: Path) -> dict[str, list[str]]:
    """{stage: where its counters are} for every `stage("...")` in `loadout`
    (`er-mechanics-combo-loadout.py`), read from its source, never run.

    A stage's code is the lines from its `stage(...)` call to the next one in the same function
    (or that function's end), and everything those lines call, followed through the script's own
    functions and the `er-mechanics-*.py` modules it loads. A call into a function that starts
    stages of its own (`weigh`) is not followed: its stages are their own, and the code after it
    runs in its last one. A counter is an f-string in `COUNTER`'s shape, or making an object of a
    class that writes one (`Count`). An attribute call is resolved by name alone, never by a name
    the builtin types use, so a stage can be credited a counter one of its calls never reaches, never
    denied one it does."""
    import ast
    here = loadout.parent
    # The script and every `er-mechanics-*.py` it names, and those name, and so on.
    mods, todo = {}, [loadout.name]
    while todo:
        name = todo.pop()
        if name in mods or not (here / name).exists():
            continue
        src = (here / name).read_text(encoding="utf-8")
        mods[name] = ast.parse(src)
        todo += re.findall(r"""['"](er-mechanics-[\w-]+\.py)['"]""", src)
    funcs, classes, alias = {}, {}, {}
    for m, tree in mods.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                classes.setdefault((m, n.name), []).append(n)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault((m, n.name), []).append(n)
        # `Count, counted = _progress().Count, _progress().counted`: a name bound at the top of a
        # module to another module's attribute is called as that attribute.
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1:
                t, v = n.targets[0], n.value
                pairs = (zip(t.elts, v.elts) if isinstance(t, ast.Tuple) and isinstance(v, ast.Tuple)
                         and len(t.elts) == len(v.elts) else [(t, v)])
                for tt, vv in pairs:
                    if isinstance(tt, ast.Name) and isinstance(vv, ast.Attribute):
                        alias[(m, tt.id)] = vv.attr

    def stage_calls(fn):
        return sorted((c.lineno, c.args[0].value) for c in ast.walk(fn) if isinstance(c, ast.Call)
                      and isinstance(c.func, ast.Name) and c.func.id == "stage" and c.args
                      and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str))

    staged = {id(f): stage_calls(f) for fs in funcs.values() for f in fs}
    staged = {k: v for k, v in staged.items() if v}

    def targets(m, call):
        """(module, function) pairs a call may run; a class's every method for a class."""
        def methods(cs, mm):
            return [(mm, x) for c in cs for x in c.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]
        f = call.func
        if isinstance(f, ast.Name) and (m, f.id) not in alias:
            return [(m, x) for x in funcs.get((m, f.id), [])] + methods(classes.get((m, f.id), []), m)
        attr = alias[(m, f.id)] if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if attr is None or attr in _BUILTIN_ATTRS:
            return []
        # `self.f()` is a method of this module's classes; `X.f()` a module's own function or class
        # (a class's every method: what it makes is used after). A method called on anything else
        # is reached through its class instead, so a common method name credits nothing.
        if isinstance(f.value if isinstance(f, ast.Attribute) else None, ast.Name) and f.value.id == "self":
            return [t for (mm, name), cs in classes.items() if mm == m for t in methods(cs, mm)
                    if t[1].name == attr]
        return ([(mm, x) for mm, tree in mods.items() for x in tree.body
                 if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == attr]
                + [t for (mm, name), cs in classes.items() if name == attr for t in methods(cs, mm)])

    memo = {}

    def emits(m, nodes, seen) -> list[str]:
        """Where the counters are among `nodes` and what they call."""
        out = []
        for node in nodes:
            for n in ast.walk(node):
                if isinstance(n, ast.JoinedStr) and counter_fstring(n):
                    out.append(f"{m}:{n.lineno}")
                elif isinstance(n, ast.Call):
                    for mm, fn in targets(m, n):
                        k = (mm, id(fn))
                        # A function already read is reused; one still being read (`seen`, not in
                        # `memo`) is a call back into itself and adds nothing new.
                        if id(fn) in staged or (k in seen and k not in memo):
                            continue
                        seen.add(k)
                        if k not in memo:
                            memo[k] = emits(mm, fn.body, seen)
                        out += memo[k]
        return sorted(set(out))

    got: dict[str, list[str]] = {}
    for fs in funcs.values():
        for fn in fs:
            if id(fn) not in staged:
                continue
            # Where each stage starts, and where a call into a staged function hands over to its last stage.
            marks = list(staged[id(fn)])
            for c in ast.walk(fn):
                if isinstance(c, ast.Call):
                    for _, t in targets(loadout.name, c):
                        if id(t) in staged:
                            marks.append((c.end_lineno, staged[id(t)][-1][1]))
            marks.sort()
            stmts = [s for s in ast.walk(fn) if isinstance(s, ast.stmt) and s is not fn]
            for k, (line, name) in enumerate(marks):
                end = marks[k + 1][0] if k + 1 < len(marks) else fn.end_lineno + 1
                # The simple statements wholly inside the stage's lines (a compound one's own test
                # or iterator is read through its children).
                body = [s for s in stmts if line < s.lineno and s.end_lineno < end
                        and not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                got.setdefault(name, [])
                got[name] = sorted(set(got[name]) | set(emits(loadout.name, body, set())))
    return got


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


def bar(frac: float, width: int = BAR_WIDTH) -> str:
    full = int(frac * width)
    part = " ▏▎▍▌▋▊▉"[int((frac * width - full) * 8)] if full < width else ""
    return f"{GREEN}{'█' * full}{part}{GREY}{'·' * (width - full - len(part))}{RESET}"


def frame(d: Path, prefix: str, now: dt.datetime, pulse: Pulse | None = None) -> str:
    """The whole view; with `pulse`, the running step's line also carries its counter and how long
    ago it last advanced (`pulse_text`)."""
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
            if is_running and pulse is not None and (beat := pulse_text(pulse, s, p, sp, now.timestamp(), el)):
                line += f"  {GREY}|{RESET} {beat}"
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
    # Largest chunk first: 2 of 4 weapons done is 600 of 800 rows, three quarters of the work.
    sp = stage_progress([plan, "stage 2/3: whiff (10s in)", "  2/4 weapons, 600/800 rows"], 40.0)
    check(abs(sp["frac"] - (10 + 75) / 120) < 1e-9 and abs(sp["left"] - (10 + 10)) < 1e-9
          and "2/4 weapons" in sp["detail"],
          "a counter line's rows, not its chunks, set the stage's share and its rate")
    sp = stage_progress(['stages: [["rows", null], ["whiff", null]]', "stage 2/2: whiff (3s in)"], 5.0)
    check(abs(sp["frac"] - 0.5) < 1e-9 and sp["left"] is None, "with no timings each stage is one of N, nothing left estimated")
    sp = stage_progress([plan, "stage 2/3: whiff (10s in)", "  20/143 weapons, 2492/8215 rows"], 40.0)
    check(sp["count"] == (2492, 8215, "rows"), "stage_progress hands over the rows counter and its unit")
    sp = stage_progress([plan, "stage 2/3: whiff (10s in)", "  7/40 pairs"], 40.0)
    check(sp["count"] == (7, 40, "pairs"), "a counter with no rows keeps its own word as the unit")

    # The counter advances every 10 s; the age runs from the advance, not from the poll.
    pu = Pulse(2.0)
    key = ("9", 2, "interrupt")
    pu.see(key, 100, 1000.0, written=990.0)
    check(pu.exact and pu.at == 990.0 and pu.text("x", 1000.0) == "x, last step 10s ago",
          "first sight with the counter as the log's last write takes the log's time")
    for t, v in ((1000.0, 100), (1000.0 + 0, 100), (1000.0, 200), (1010.0, 300), (1020.0, 400), (1030.0, 500)):
        pu.see(key, v, t)
    check(pu.gaps == [10.0, 10.0, 10.0, 10.0] and pu.threshold() == 50.0,
          "each advance records its gap; the stop mark is five times the median gap")
    check(pu.text("x", 1061.0) == "x, last step 31s ago" and "no progress" not in pu.text("x", 1079.0),
          "an advance within five median gaps reads as a live step with its age")
    check(pu.text("x", 1870.0) == f"x, {RED}no progress for 14m{RESET}",
          "past five median gaps the line says no progress, in red")
    pu.see(key, 500, 1900.0)
    check(pu.at == 1030.0, "an unchanged counter does not move the last step")
    pu.see(("9", 3, "whiff"), None, 1902.0)
    check(pu.gaps == [] and pu.exact and pu.text("x", 1912.0) == "x, last step 10s ago",
          "a new stage starts over, timed from when it was seen")
    cold = Pulse(2.0)
    cold.see(key, 100, 500.0)
    cold.see(key, 200, 507.0)
    check(cold.text("x", 500.0) == "x, last step 0s ago" and cold.gaps == [],
          "a watcher started mid-gap neither counts that cut gap nor claims to know its first age")
    cold2 = Pulse(2.0)
    cold2.see(key, 100, 500.0)
    check(cold2.text("x", 531.0) == "x, last step ≥31s ago", "an age it cannot pin down is marked as at least")
    fast = Pulse(2.0)
    for i in range(5):
        fast.see(key, i, 100.0 + 0.5 * i)
    check(fast.threshold() == 10.0, "gaps are never shorter than one poll, so a fast counter is not called stopped at once")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pfx = Path(tmp) / "t"
        loadout = Path(f"{pfx}-loadout.log")
        lines = [plan, "stage 2/3: interrupt (10s in)", "  20/143 weapons, 2492/8215 rows"]
        loadout.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(loadout, (5000.0, 5000.0))
        step9 = next(s for s in steps(pfx) if s["id"] == "9")
        pu = Pulse(2.0)
        text = pulse_text(pu, step9, pfx, stage_progress(lines, 40.0), 5031.0)
        check(text == "interrupt 30s 2492/8215 rows, last step 31s ago",
              f"pulse_text: the stage, its time in, its counter, aged from the log's write ({text})")
        loadout.write_text("\n".join(lines + ["warning: slow"]) + "\n", encoding="utf-8")
        pu = Pulse(2.0)
        text = pulse_text(pu, step9, pfx, stage_progress(lines, 40.0), 5031.0)
        check(text == "interrupt 30s 2492/8215 rows, last step ≥0s ago",
              "a later line than the counter leaves the counter's age unknown, not the log's")

        # A stage before its first counter line: its name and time in it, no age to show.
        quiet = ['stages: [["partners", 3], ["set coverage", null], ["write", 0]]',
                 "stage 1/3: partners (0s in)", "stage 2/3: set coverage (10s in)"]
        loadout.write_text("\n".join(quiet) + "\n", encoding="utf-8")
        text = pulse_text(Pulse(2.0), step9, pfx, stage_progress(quiet, 340.0), 6000.0)
        check(text == "set coverage 5m30s", f"a stage with no counter yet shows its name and time in it ({text})")
        sp_q = stage_progress(quiet, 40.0)
        check(not sp_q["known"] and sp_q["later_known"] == 0 and sp_q["left"] is None,
              "a stage never timed and with no counter is an unknown share, not seconds")

        # The embedding's powerstance pairs come after its last `[i/N]` weapon line: the step
        # reads the later counter.
        Path(f"{pfx}-embed0.log").write_text("tables 10.9s\n[329/330] A 338s\n[330/330] B 339s\n"
                                              "  2000/5040 pairs\n", encoding="utf-8")
        step2 = next(s for s in steps(pfx) if s["id"] == "2")
        check(progress(step2, pfx)[:2] == (2000, 5040), f"embedding: the pairs counter after the weapons ({progress(step2, pfx)})")
        Path(f"{pfx}-true-combos.err").write_text("  40/316 weapon grips\n", encoding="utf-8")
        step7a = next(s for s in steps(pfx) if s["id"] == "7a")
        check(progress(step7a, pfx)[:2] == (40, 316), "true combos: the weapon grips done, not the combos found")

    with tempfile.TemporaryDirectory() as tmp:
        pfx = Path(tmp) / "t"
        Path(f"{pfx}-status.log").write_text("[3/40] swept A\n[4/40] swept B\n", encoding="utf-8")
        step8 = next(s for s in steps(pfx) if s["id"] == "8")
        text = pulse_text(Pulse(2.0), step8, pfx, None, time.time())
        check(text.startswith("status-affinity sweep 4/40, last step "), f"a step with its own counter ({text})")
        text = pulse_text(Pulse(2.0), step8, pfx, None, time.time(), el=95.0)
        check(text.startswith("status-affinity sweep 1m35s 4/40, last step "),
              f"a step outside the loadout shows its time in it from the pipeline's step line ({text})")

    # `stage_counters` on a made-up loadout: a counter through an alias of another module's
    # function, one written in place, one in a helper module's function; a stage with a loop and no
    # counter, and one whose only counter is a method called on an object, which credits nothing.
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "er-mechanics-progress.py").write_text(
            "class Count:\n    def __call__(self):\n        print(f'  {self.i}/{self.total} {self.unit}')\n"
            "def counted(items, unit):\n    tick = Count()\n    for x in items:\n        yield x\n        tick()\n",
            encoding="utf-8")
        (d / "er-mechanics-helper.py").write_text(
            "def slow(rows):\n    for i, x in enumerate(rows):\n        print(f'  {i}/{len(rows)} things')\n"
            "class Thing:\n    def run(self, rows):\n        print(f'  {1}/{2} things')\n", encoding="utf-8")
        (d / "loadout.py").write_text(
            "Count, counted = _progress().Count, _progress().counted\n"
            "def _progress():\n    return load('er-mechanics-progress.py')\n"
            "H = load('er-mechanics-helper.py')\n"
            "def helper(rows):\n    for x in counted(rows, 'rows'):\n        pass\n"
            "def quiet(rows):\n    for x in rows:\n        print(f'stage {1}/{2}: x')\n"
            "def weigh(rows, stage):\n    stage('a')\n    helper(rows)\n    stage('b')\n    quiet(rows)\n"
            "    thing.run(rows)\n"
            "def main(rows):\n    stage = Stages()\n    stage('c')\n    print(f'  {1}/{2} rows')\n"
            "    weigh(rows, stage)\n    quiet(rows)\n    stage('d')\n    rows.append(1)\n    H.slow(rows)\n",
            encoding="utf-8")
        got = {k: bool(v) for k, v in stage_counters(d / "loadout.py").items()}
        check(got == {"a": True, "b": False, "c": True, "d": True},
              f"stage_counters: through an alias, none, in place and through a helper module ({got})")

    # The real loadout: every stage it can start writes a counter (user, 2026-10-20: the bar must
    # always have a counter that moves). Read from the source, so a stage added without one fails
    # here before any run is spent finding out.
    import ast
    loadout = Path(__file__).resolve().parent / "er-mechanics-combo-loadout.py"
    names = next(ast.literal_eval(n.value) for n in ast.parse(loadout.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "STAGES" for t in n.targets))
    got = stage_counters(loadout)
    unseen = [n for n in names if n not in got]
    bare = [n for n in names if n in got and not got[n]]
    check(not unseen, f"stage_counters finds every stage of `STAGES` started in the source (missing {unseen})")
    check(not bare, f"every combo-loadout stage writes a counter (none in {bare})")
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
    pulse = Pulse(a.interval)
    if a.once:
        print(frame(a.dir, a.prefix, dt.datetime.now(), pulse))
        return 0
    try:
        while True:
            text = frame(a.dir, a.prefix, dt.datetime.now(), pulse)
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
