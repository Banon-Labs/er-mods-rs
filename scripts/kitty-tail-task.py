#!/usr/bin/env python3
"""Open a kitty tab that tails a running task's output until the task's process exits, then
closes the tab 5 seconds later.

    python3 scripts/kitty-tail-task.py --title 'duel build' \\
        --file <task output file> --match 'er-weapon-build.py' --match '--fight duel'

The tab runs `tail -n +1 -F --pid=<pid> <file>; sleep 5` directly (no shell prompt, nothing for
the user to submit): `tail --pid` ends once that process is gone, and kitty closes a tab whose
program exits. `--pid` names the process; otherwise every `--match` substring must appear in its
command line, and exactly one live process may match. The tab opens with `--keep-focus`, so it
does not take the keyboard from whatever the user is doing. When several processes match, the one
working in this checkout (`--cwd` names another) is kept.

    python3 scripts/kitty-tail-task.py --boards --title boards [--file <task output file>]

follows a board regeneration (`gen-r3-weapon-boards.py`): its wrapper prints four setup lines and
the progress goes to one `shard-<n>.log` per worker in the cache directory its first line names,
so this mode finds the wrapper process and the tab runs `--boards-view` instead of `tail`: a bar
for the whole pass and one per shard, with %, time left at the rate the shards are showing, the
last step's age and a red stall mark, as the duel build's view draws them
(`er-mechanics-pipeline-watch.py`). It reads only the logs and the cache, never the generator.
The guard `.cupcake/policies/claude/no_untailed_long_run.rego` asks for this form.

    python3 scripts/kitty-tail-task.py --boards-view --once [--file <task output file>]   # one frame

This is the watcher counterpart of `kitty-user-command.py`, which hands the user a command to
run; this one runs nothing for the user to approve. It reuses that helper's listener lookup
(`$KITTY_LISTEN_ON`) and `kitty @` wrapper.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: Seconds the tab stays open after the task's process exits (user, 2026-10-19).
LINGER_S = 5


def _kuc():
    spec = importlib.util.spec_from_file_location("kitty_user_command", HERE / "kitty-user-command.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tail_argv(path: str | list[str], pid: int, linger: int = LINGER_S) -> list[str]:
    """The program the tab runs.

    Several paths are for a run whose real progress is spread over worker logs: the board
    generator's wrapper prints four setup lines, and every line after that goes to one
    `shard-<n>.log` per worker in its cache directory (2026-10-19). `tail` heads each file's
    lines with its name, so the shards stay apart in one tab.
    """
    paths = [path] if isinstance(path, str) else list(path)
    script = f"tail -n +1 -F --pid={pid} {' '.join(shlex.quote(p) for p in paths)}; sleep {linger}"
    return ["bash", "-c", script]


def repo_root(path: str | Path) -> Path | None:
    """The checkout `path` lies in: the nearest directory holding `.git` -- a directory in the main
    tree, a file in a worktree -- so a worktree is its own root and not part of the main tree."""
    p = Path(path)
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def find_pid(needles: list[str], proc: Path = Path("/proc"), me: int | None = None,
             exclude: tuple[str, ...] = (), root: Path | None = None, stdout: str | None = None) -> int:
    """The one live process whose command line holds every needle and none of `exclude` (this
    process excluded).

    When several match, `stdout` breaks the tie first -- the process whose standard output is that
    file, which for a background task is the harness's output file -- and then `root`: only the
    processes whose working directory lies in that checkout are kept. Two sessions regenerating boards at once from different checkouts
    matched together on 2026-10-19, and the tab was refused.
    """
    me = os.getpid() if me is None else me
    hits = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        line = " ".join(a.decode("utf-8", "replace") for a in argv if a)  # UTF-8 Lossy: a command line is only matched, never written back
        if (line and all(n in line for n in needles) and not any(x in line for x in exclude)
                and "kitty-tail-task.py" not in line):
            hits.append((int(entry.name), line))
    # A shell that launched the task (`zsh -c '... er-weapon-build.py ...'`) matches as well:
    # keep only matches that are not an ancestor of another match.
    pids = {p for p, _ in hits}

    def ancestors(pid):
        seen = set()
        while pid > 1 and pid not in seen:
            seen.add(pid)
            try:
                pid = int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
            yield pid

    outer = {a for p in pids for a in ancestors(p)}
    hits = [h for h in hits if h[0] not in outer]
    if len(hits) > 1 and stdout:
        def writes_to(pid):
            try:
                return os.path.realpath(os.readlink(proc / str(pid) / "fd" / "1")) == os.path.realpath(stdout)
            except OSError:
                return False
        mine = [h for h in hits if writes_to(h[0])]
        if len(mine) == 1:
            hits = mine
    if len(hits) > 1 and root is not None:
        def in_root(pid):
            try:
                return repo_root(os.readlink(proc / str(pid) / "cwd")) == Path(root)
            except OSError:
                return False
        mine = [h for h in hits if in_root(h[0])]
        if len(mine) == 1:
            hits = mine
    if len(hits) != 1:
        found = "; ".join(f"{p} {l[:100]}" for p, l in hits) or "none"
        raise SystemExit(f"--match must name exactly one live process, found {len(hits)}: {found}")
    return hits[0][0]


#: The board generator. Its wrapper prints a few setup lines, and every progress line after them
#: goes to one `<cache>/shard-<n>.log` per worker (2026-10-19); `cache <dir>` is its first line.
BOARDS_SCRIPT = "gen-r3-weapon-boards.py"
#: Invocations of it that are not a board regeneration, and its shard processes, which are the
#: wrapper's children and would otherwise match in its place.
BOARDS_EXCLUDE = ("--shard", "--check", "--selftest", "--only")
#: How long to wait for the wrapper's `cache` line before giving up.
BOARDS_CACHE_WAIT_S = 15


def boards_cache(wrapper_out: str, wait: float = BOARDS_CACHE_WAIT_S) -> Path:
    """The cache directory the wrapper's output names on its `cache <dir>` line."""
    try:
        for line in Path(wrapper_out).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("cache "):
                return Path(line[len("cache "):].strip())
    except OSError:
        pass
    # Not written yet: `tail -F` follows the file through inotify, so each new line arrives as an event.
    deadline = time.monotonic() + wait
    follow = subprocess.Popen(["tail", "-n", "+1", "-F", wrapper_out], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, errors="replace")
    try:
        while (left := deadline - time.monotonic()) > 0:
            if not select.select([follow.stdout], [], [], left)[0]:
                break
            line = follow.stdout.readline()
            if not line:
                break
            if line.startswith("cache "):
                return Path(line[len("cache "):].strip())
    finally:
        follow.kill()
        follow.wait(timeout=5)
    raise SystemExit(f"{wrapper_out} has no 'cache <dir>' line after {wait:.0f} s; is it the "
                     f"output of {BOARDS_SCRIPT}?")


def boards_jobs(cmdline: list[str], default: int = 3) -> int:
    """The `--jobs` the wrapper was asked for: no host runs more shards than that."""
    for i, a in enumerate(cmdline):
        if a == "--jobs" and i + 1 < len(cmdline) and cmdline[i + 1].isdigit():
            return int(cmdline[i + 1])
        if a.startswith("--jobs=") and a[7:].isdigit():
            return int(a[7:])
    return default


def boards_target(pid: int | None, files: list[str], root: Path | None) -> tuple[int, str, int]:
    """(wrapper pid, wrapper output file, requested `--jobs`) for a board regeneration. The
    wrapper's output defaults to whatever its stdout is open on, which for a background task is
    the harness's output file."""
    if pid is None:
        pid = find_pid([BOARDS_SCRIPT], exclude=BOARDS_EXCLUDE, root=root,
                       stdout=files[0] if files else None)
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        raise SystemExit(f"process {pid} is not running")
    cmdline = [a.decode("utf-8", "replace") for a in raw if a]  # UTF-8 Lossy: argv is only parsed for --jobs
    wrapper_out = files[0] if files else None
    if wrapper_out is None:
        try:
            wrapper_out = os.readlink(f"/proc/{pid}/fd/1")
        except OSError:
            raise SystemExit(f"cannot read the stdout of process {pid}; pass the task output file as --file")
    return pid, wrapper_out, boards_jobs(cmdline)


# The board view: a progress bar over the shard logs, drawn by this script in the tab instead of a
# raw `tail`. Everything is read from what the generator already writes, so the generator and its
# board sources stay untouched (changing one makes `weapon_boards.rs` stale).

#: A shard's plan line, printed once per run (`run_shard`); its logs are appended to across runs,
#: so only the last one counts.
SHARD_PLAN = re.compile(r"^shard (\d+)/(\d+): (\d+) weapons, (\d+) to compute$")
#: A weapon finished: `shard 0: 16/183 Greataxe ok 36s`, or the failure's reason in place of `ok`.
SHARD_STEP = re.compile(r"^shard (\d+): (\d+)/(\d+) (.+) (\d+)s$")
#: How many shards the run started: the last `local:`/`remote <host>:` plan line in the wrapper's
#: output (`plan_shards`), or one when it had nothing left to compute.
SHARDS_GRANTED = re.compile(r"^(?:local|remote \S+): (?:--jobs \d+ requested, (\d+) granted"
                            r"|warning: .*running the (\d+) requested shard)")
#: A weapon's cached result; `opened-*.json` and `subset.json` are not results.
RESULT_NAME = re.compile(r"^\d+\.json$")
#: Wrapper lines worth showing in red.
BOARDS_ALARM = re.compile(r"Traceback|Error|error:|failed|stale:", re.I)
#: Seconds between redraws.
BOARDS_INTERVAL_S = 2.0


def _watch():
    """`er-mechanics-pipeline-watch.py`, for the duel build's bar, clock, colours and `Pulse`."""
    spec = importlib.util.spec_from_file_location("er_mechanics_pipeline_watch",
                                                  HERE / "er-mechanics-pipeline-watch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lines(path) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def shard_state(lines: list[str]) -> dict | None:
    """What one shard log says about the shard's latest run, or None before its plan line: `total`
    weapons to compute, `done`, the seconds each finished weapon took (`took`), the failed ones'
    lines (`failed`) and the latest finished line (`last`)."""
    start = None
    for i, line in enumerate(lines):
        if SHARD_PLAN.match(line):
            start = i
    if start is None:
        return None
    total = int(SHARD_PLAN.match(lines[start])[4])
    done, took, failed, last = 0, [], [], ""
    for line in lines[start + 1:]:
        m = SHARD_STEP.match(line)
        if m:
            done, last = int(m[2]), line
            took.append(int(m[5]))
            if not m[4].endswith(" ok"):
                failed.append(line)
    return {"total": total, "done": done, "took": took, "failed": failed, "last": last}


def boards_shard_count(wrapper_lines: list[str], requested: int) -> int:
    """How many shards the current pass runs: the wrapper's last grant, else what was requested."""
    n = None
    for line in wrapper_lines:
        m = SHARDS_GRANTED.match(line)
        if m:
            n = int(m[1] or m[2])
        elif line.startswith("local: nothing left to compute"):
            n = 1
    return requested if n is None else max(1, n)


def process_started(pid: int) -> float | None:
    """When `pid` started, as a Unix time, from `/proc/<pid>/stat` and the boot time."""
    try:
        ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        boot = next(int(x.split()[1]) for x in Path("/proc/stat").read_text().splitlines()
                    if x.startswith("btime "))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def _mtime(path) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def shard_left(st: dict | None, mean: float | None, in_weapon: float) -> float | None:
    """Seconds a shard still needs: its remaining weapons at `mean` seconds each, less what the
    current one has already run (`in_weapon`), never below zero for that one."""
    if st is None or mean is None:
        return None
    rest = st["total"] - st["done"]
    if rest <= 0:
        return 0.0
    return (rest - 1) * mean + max(0.0, mean - in_weapon)


def boards_frame(cache: Path, wrapper_out: str, shards: int, started: float, now: float,
                 alive: bool = True) -> str:
    """The board view: one bar for the whole pass, one per shard, and the wrapper's tail.

    The whole pass is the sum of every shard's `done/total`. Time left is the slowest shard's: its
    remaining weapons at the mean seconds a weapon has taken in that shard (the `Ns` its lines end
    with), so it follows the rate this run is showing. The last step is the newest weapon result in
    the cache, which a shard writes the moment a weapon finishes; it turns red once no weapon has
    finished for `STALL_FACTOR` times the median a weapon takes, three shards or not."""
    w = _watch()
    wrapper = _lines(wrapper_out)
    states, logs = [], []
    for i in range(shards):
        log = cache / f"shard-{i}.log"
        logs.append(log)
        written = _mtime(log)
        # A log no newer than the run holds only an earlier run's lines.
        states.append(shard_state(_lines(log)) if written is not None and written >= started else None)
    took_all = [t for st in states if st for t in st["took"]]
    mean_all = sum(took_all) / len(took_all) if took_all else None
    done = sum(st["done"] for st in states if st)
    total = sum(st["total"] for st in states if st)
    known = all(st is not None for st in states)
    lefts = []
    rows = []
    for i, (st, log) in enumerate(zip(states, logs)):
        mean = sum(st["took"]) / len(st["took"]) if st and st["took"] else mean_all
        # The shard's last write is the line that ends one weapon or the warning that starts the
        # next, so the current weapon has been running since then.
        left = shard_left(st, mean, max(0.0, now - (_mtime(log) or now)))
        lefts.append(left)
        if st is None:
            rows.append(f"   shard {i}  {w.GREY}starting{w.RESET}")
            continue
        frac = st["done"] / st["total"] if st["total"] else 1.0
        line = f"   shard {i}  {w.bar(frac, 20)} {st['done']}/{st['total']}"
        if left is not None and st["done"] < st["total"]:
            line += f"  {w.CYAN}~{w.clock(left)}{w.RESET}"
        if st["last"]:
            m = SHARD_STEP.match(st["last"])
            line += f"  {w.GREY}last {m[4].removesuffix(' ok')[:40]}, {m[5]}s{w.RESET}"
        if st["failed"]:
            line += f"  {w.RED}{len(st['failed'])} failed{w.RESET}"
        rows.append(line)
    left = max(lefts) if known and all(x is not None for x in lefts) else None
    results = [t for f in (os.scandir(cache) if cache.is_dir() else ()) if RESULT_NAME.match(f.name)
               and (t := _mtime(f.path)) is not None and t >= started]
    pulse = w.Pulse()
    pulse.key = ("boards",)
    pulse.at, pulse.exact = (max(results), True) if results else (started, False)
    # The stall mark's yardstick: what a weapon takes, not the gap between any two shards' steps.
    pulse.gaps = took_all
    el = max(0.0, now - started)
    finished = not alive
    head = (f"{w.BOLD}boards{w.RESET}  {w.GREY}cache {cache.name}, started "
            f"{time.strftime('%H:%M:%S', time.localtime(started))}, {w.clock(el)}{w.RESET}")
    if finished:
        head += f"  {w.GREEN}finished{w.RESET}" if not any(BOARDS_ALARM.search(x) for x in wrapper) \
            else f"  {w.RED}failed{w.RESET}"
    frac = done / total if total else 0.0
    mark = (f"{w.GREEN}✓" if finished else f"{w.YELLOW}▶") + w.RESET
    line = f" {mark} {w.bar(frac)} {frac * 100:3.0f}%  {done}/{total}" + ("" if known else "+")
    line += f"  {w.clock(el)}"
    if left is not None and not finished and done < total:
        line += f"  {w.CYAN}~{w.clock(left)} left{w.RESET}"
    if not finished:
        line += f"  {w.GREY}|{w.RESET} " + pulse.text(f"weapon {done}/{total}", now)
    out = [head, "", line, *rows, ""]
    width = shutil.get_terminal_size((100, 30)).columns
    out += [f" {w.RED if BOARDS_ALARM.search(x) else w.GREY}{x[:width - 2]}{w.RESET}" for x in wrapper[-6:]]
    fails = [f for st in states if st for f in st["failed"]]
    out += [f" {w.RED}{x[:width - 2]}{w.RESET}" for x in fails[-3:]]
    return "\n".join(out)


def boards_view(wrapper_out: str, pid: int | None, jobs: int, once: bool = False,
                linger: int = LINGER_S) -> int:
    """Redraw `boards_frame` until the wrapper `pid` exits, then hold the last frame `linger`
    seconds; the tab running this closes when it returns."""
    cache = boards_cache(wrapper_out)
    started = (process_started(pid) if pid else None) or _mtime(wrapper_out) or time.time()

    def alive():
        return pid is not None and Path(f"/proc/{pid}").exists()

    def draw(clear: bool) -> None:
        text = boards_frame(cache, wrapper_out, boards_shard_count(_lines(wrapper_out), jobs), started,
                            time.time(), alive())
        sys.stdout.write(("\033[H\033[2J" if clear else "") + text + "\n")
        sys.stdout.flush()

    if once:
        draw(False)
        return 0
    # The wrapper's pidfd turns readable when it exits, so exit wakes the wait at once and the
    # select timeout only paces the redraw.
    pidfd = None
    if pid is not None:
        try:
            pidfd = os.pidfd_open(pid)
        except ProcessLookupError:
            pass
    try:
        while pidfd is not None:
            draw(True)
            if select.select([pidfd], [], [], BOARDS_INTERVAL_S)[0]:
                break
        draw(True)
        # The hold: a bounded select with nothing to wake it but the timeout.
        select.select([], [], [], linger)
    except KeyboardInterrupt:
        pass
    finally:
        if pidfd is not None:
            os.close(pidfd)
    return 0


def boards_argv(wrapper_out: str, pid: int, jobs: int) -> list[str]:
    """The program a board tab runs: this script's own view of the run."""
    return [sys.executable, str(Path(__file__).resolve()), "--boards-view", "--file", wrapper_out,
            "--pid", str(pid), "--jobs", str(jobs)]


def open_tab(title: str, path: str | list[str], pid: int, dry_run: bool,
             program: list[str] | None = None) -> int:
    """`program` replaces the `tail` the tab runs otherwise (the board view)."""
    argv = ["launch", "--type=tab", "--keep-focus", f"--tab-title={title}",
            *(program or tail_argv(path, pid))]
    if dry_run:
        print("kitty @ " + shlex.join(argv))
        return 0
    k = _kuc()
    window_id = k.kitty(k.listener_or_exit(), *argv).strip()
    if not window_id.isdigit():
        raise SystemExit(f"kitty @ launch did not return a window id: {window_id!r}")
    what = f"shows the board progress of {path}" if program else f"tails {path}"
    print(f"tab {title!r} (window {window_id}) {what} until pid {pid} exits, then closes after {LINGER_S} s")
    return 0


def boards_view_selftest(check) -> None:
    """The board view against a made-up cache: two shards granted of three asked, one an earlier
    run's log until it is touched, results from this run and an older one."""
    import tempfile
    plain = re.compile(r"\033\[[0-9;]*m")
    check("shard_state before a plan line", shard_state(["er-param-read: stale"]), None)
    check("granted shards", boards_shard_count(["cache x", "local: --jobs 3 requested, 2 granted: ..."], 3), 2)
    check("nothing left runs one shard", boards_shard_count(["local: nothing left to compute, so one shard"], 3), 1)
    check("an uncapped grant", boards_shard_count(
        ["local: warning: cannot read /proc/meminfo (x); running the 4 requested shard(s) uncapped"], 3), 4)
    check("no grant yet: the request", boards_shard_count(["cache x"], 3), 3)
    check("time left counts the current weapon's run", shard_left({"total": 4, "done": 2}, 15.0, 10.0), 20.0)
    check("a weapon past the mean adds nothing for itself", shard_left({"total": 4, "done": 2}, 15.0, 99.0), 15.0)
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "key"
        cache.mkdir()
        out = os.path.join(d, "task.output")
        Path(out).write_text(f"cache {cache}\nlocal: --jobs 3 requested, 2 granted: ...\n")
        s0 = cache / "shard-0.log"
        s0.write_text("shard 0/3: 12 weapons, 9 to compute\nshard 0: 1/9 Old ok 99s\n"
                      "er-param-read: stale\nshard 0/2: 10 weapons, 4 to compute\n"
                      "shard 0: 1/4 Dagger ok 10s\nshard 0: 2/4 Big Name Sword ok 20s\ner-param-read: stale\n")
        s1 = cache / "shard-1.log"
        s1.write_text("shard 1/2: 9 weapons, 4 to compute\nshard 1: 1/4 Club ValueError: no row 30s\n")
        os.utime(s0, (1100, 1100))
        os.utime(s1, (1090, 1090))
        for name, t in (("1.json", 1050), ("2.json", 1080), ("3.json", 1095), ("4.json", 900),
                        ("opened-0.json", 1099)):
            (cache / name).write_text("{}")
            os.utime(cache / name, (t, t))
        st = shard_state(_lines(s0))
        check("the last plan line starts the shard's run", (st["total"], st["done"], st["took"]), (4, 2, [10, 20]))
        check("a reason in place of ok is a failure", len(shard_state(_lines(s1))["failed"]), 1)
        text = plain.sub("", boards_frame(cache, out, 2, 1000.0, 1110.0))
        bar_line = text.splitlines()[2]
        check("whole-pass bar: %, done/total, time left at the slowest shard's rate",
              "38%  3/8  1m50s  ~1m10s left" in bar_line, True)
        check("last step is the newest result of this run", bar_line.endswith("weapon 3/8, last step 15s ago"), True)
        check("a shard row with its own time left and last weapon",
              "2/4  ~20s  last Big Name Sword, 20s" in text, True)
        check("a failed weapon is counted", "1 failed" in text and "ValueError: no row" in text, True)
        late = plain.sub("", boards_frame(cache, out, 2, 1000.0, 1300.0)).splitlines()[2]
        check("no step for five median weapon times reads as stopped",
              late.endswith("weapon 3/8, no progress for 3m25s"), True)
        os.utime(s1, (900, 900))
        early = plain.sub("", boards_frame(cache, out, 2, 1000.0, 1110.0))
        check("a log untouched since the run started is an earlier run's: starting, total unknown",
              "2/4+" in early and "shard 1  starting" in early and "left" not in early.splitlines()[2], True)
        Path(out).write_text(f"cache {cache}\na shard failed; see {cache}/shard-*.log\n")
        done = plain.sub("", boards_frame(cache, out, 2, 1000.0, 1110.0, alive=False))
        check("an exited wrapper that reported a failure is failed", done.splitlines()[0].endswith("failed"), True)
        Path(out).write_text(f"cache {cache}\nwrote crates/er-r3-view/src/weapon_boards.rs: 547 boards\n")
        done = plain.sub("", boards_frame(cache, out, 2, 1000.0, 1110.0, alive=False))
        check("an exited wrapper with no failure is finished", done.splitlines()[0].endswith("finished"), True)
        check("the tab runs the view with the wrapper's output, pid and jobs",
              boards_argv(out, 42, 3)[2:], ["--boards-view", "--file", out, "--pid", "42", "--jobs", "3"])


def selftest() -> int:
    bad = 0

    def check(what, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"{'ok' if ok else 'FAIL'}  {what}: {got!r}" + ("" if ok else f" (want {want!r})"))

    argv = tail_argv("/tmp/a b.out", 42)
    check("tail argv", argv, ["bash", "-c", "tail -n +1 -F --pid=42 '/tmp/a b.out'; sleep 5"])
    # The program really ends once the watched process does, then lingers: a 1 s process and a
    # 1 s linger finish in about 2 s with the file's line printed.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".out", delete=False) as f:
        f.write("line one\n")
    import threading
    child = subprocess.Popen(["sleep", "1"])
    # Reaped at once: an unreaped child stays a zombie that `tail --pid` still counts as alive.
    threading.Thread(target=child.wait, daemon=True).start()
    t0 = time.monotonic()
    out = subprocess.run(tail_argv(f.name, child.pid, linger=1), capture_output=True, text=True, timeout=20)
    dt = time.monotonic() - t0
    os.unlink(f.name)
    check("tail printed the file", "line one" in out.stdout, True)
    check("tail ended after the process and the linger (1.5 to 6 s)", 1.5 <= dt <= 6.0, True)
    # find_pid: exactly one match or a refusal.
    # Two commands, so bash stays the process instead of exec'ing sleep and dropping the marker.
    sleeper = subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-selftest-marker"])
    try:
        check("find_pid finds the one process", find_pid(["kitty-tail-selftest-marker"]), sleeper.pid)
        # A wrapping shell whose command line also holds the marker is its ancestor, so dropped.
        wrap = subprocess.Popen(["bash", "-c", "bash -c 'sleep 30; true' kitty-tail-nested-marker; true",
                                 "kitty-tail-nested-marker"])
        try:
            deadline = time.monotonic() + 5
            got = None
            # Until the inner shell has started, the outer one is the only match.
            while (got is None or got == wrap.pid) and time.monotonic() < deadline:
                try:
                    got = find_pid(["kitty-tail-nested-marker"])
                except SystemExit:
                    pass
            check("find_pid keeps the inner process, not its wrapping shell",
                  got is not None and got != wrap.pid, True)
        finally:
            subprocess.run(["pkill", "-P", str(wrap.pid)], check=False, timeout=10)
            wrap.kill()
            wrap.wait()
        try:
            find_pid(["no-such-process-anywhere-xyz"])
            check("find_pid refuses no match", False, True)
        except SystemExit:
            check("find_pid refuses no match", True, True)
    finally:
        sleeper.kill()
        sleeper.wait()
    # root: of two matching processes, the one working in the named checkout.
    with tempfile.TemporaryDirectory() as a_dir, tempfile.TemporaryDirectory() as b_dir:
        for d in (a_dir, b_dir):
            os.mkdir(os.path.join(d, ".git"))
        os.mkdir(os.path.join(b_dir, "sub"))
        twins = [subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-twin-marker"], cwd=cwd)
                 for cwd in (a_dir, os.path.join(b_dir, "sub"))]
        try:
            try:
                find_pid(["kitty-tail-twin-marker"])
                check("two matches without a root are refused", False, True)
            except SystemExit:
                check("two matches without a root are refused", True, True)
            check("root picks the process working in that checkout",
                  find_pid(["kitty-tail-twin-marker"], root=Path(b_dir)), twins[1].pid)
            check("and the other checkout picks the other one",
                  find_pid(["kitty-tail-twin-marker"], root=Path(a_dir)), twins[0].pid)
        finally:
            for t in twins:
                t.kill()
                t.wait()
        # stdout: of two matching processes, the one writing to the named file.
        outs = [os.path.join(a_dir, "one.out"), os.path.join(b_dir, "two.out")]
        handles = [open(o, "w") for o in outs]
        twins = [subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-out-marker"], stdout=h)
                 for h in handles]
        try:
            check("stdout picks the process writing to that file",
                  find_pid(["kitty-tail-out-marker"], stdout=outs[1]), twins[1].pid)
        finally:
            for t in twins:
                t.kill()
                t.wait()
            for h in handles:
                h.close()
    excluded =subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-excl-marker", "--shard"])
    kept = subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-excl-marker"])
    try:
        check("exclude drops a match", find_pid(["kitty-tail-excl-marker"], exclude=("--shard",)), kept.pid)
    finally:
        for t in (excluded, kept):
            t.kill()
            t.wait()
    # boards: the wrapper's `cache` line names the directory, `--jobs` the shard count.
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "task.output")
        with open(out, "w") as f:
            f.write(f"cache {d}/key\nremote x: copying\n")
        check("boards cache", boards_cache(out, wait=0), Path(f"{d}/key"))
        check("boards jobs", boards_jobs(["python3", BOARDS_SCRIPT, "--jobs", "2"]), 2)
        check("boards jobs default", boards_jobs(["python3", BOARDS_SCRIPT]), 3)
        with open(out, "w") as f:
            f.write("nothing here\n")
        try:
            boards_cache(out, wait=0)
            check("boards refuses an output with no cache line", False, True)
        except SystemExit:
            check("boards refuses an output with no cache line", True, True)
    boards_view_selftest(check)
    print("selftest ok" if not bad else "selftest FAILED")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--title", default="task")
    ap.add_argument("--file", action="append", default=[],
                    help="the task's output file; repeat to follow worker logs as well")
    ap.add_argument("--pid", type=int, help="the task's process")
    ap.add_argument("--match", action="append", default=[],
                    help="a substring of the task process's command line; repeat to narrow")
    ap.add_argument("--boards", action="store_true",
                    help=f"a {BOARDS_SCRIPT} run: find its wrapper process and follow its output plus "
                         "every <cache>/shard-<n>.log, where the progress actually goes; --file and "
                         "--pid are optional here")
    ap.add_argument("--boards-view", action="store_true",
                    help="draw the board run's progress bar here (what a --boards tab runs); "
                         "--file is the wrapper output, --pid the wrapper")
    ap.add_argument("--jobs", type=int, default=3,
                    help="--boards-view: the --jobs the run was asked for, used until its output "
                         "says how many shards it was granted")
    ap.add_argument("--once", action="store_true", help="--boards-view: print one frame and exit")
    ap.add_argument("--cwd", type=Path,
                    help="when several processes match, keep the one working in this checkout "
                         "(default: the checkout this command runs in)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    root = repo_root(a.cwd if a.cwd else os.getcwd())
    if a.boards_view:
        try:
            pid, out, jobs = boards_target(a.pid, a.file, root)
        except SystemExit:
            if not a.file:
                raise
            # The wrapper has exited: draw what the finished run left.
            pid, out, jobs = None, a.file[0], a.jobs
        return boards_view(out, pid, jobs, once=a.once)
    if a.boards:
        pid, out, jobs = boards_target(a.pid, a.file, root)
        boards_cache(out)  # refuse now, not inside the tab, an output that is not a board run's
        return open_tab(a.title, out, pid, a.dry_run, program=boards_argv(out, pid, jobs))
    if not a.file or not (a.pid or a.match):
        ap.error("--file and one of --pid / --match are required")
    pid = a.pid or find_pid(a.match, root=root, stdout=a.file[0])
    return open_tab(a.title, a.file[0] if len(a.file) == 1 else a.file, pid, a.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
