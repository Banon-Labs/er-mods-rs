#!/usr/bin/env python3
"""Open a kitty split that tails a running task's output until the task's process exits, then
closes the split 5 seconds later.

    python3 scripts/kitty-tail-task.py --title 'duel build' \\
        --file <task output file> --match 'er-weapon-build.py' --match '--fight duel'

The split is a new kitty window in the agent session's own tab (user request 2026-11-07); `--tab`
opens a new tab instead, as it did before. It runs `tail -n +1 -F --pid=<pid> <file>; sleep 5`
directly (no shell prompt, nothing for the user to submit): `tail --pid` ends once that process is
gone, and kitty closes a window whose program exits, and a tab with it when it was the tab's only
window. `--pid` names the process; otherwise every `--match` substring must appear in its command
line, and exactly one live process may match. The window opens with `--keep-focus`, so it does not
take the keyboard from whatever the user is doing. When several processes match, the one working
in this checkout (`--cwd` names another) is kept. A task and the pool workers it forks are one
match, the task.

    python3 scripts/kitty-tail-task.py --boards --title boards [--file <task output file>]

follows a board regeneration (`gen-r3-weapon-boards.py`): its wrapper prints four setup lines and
the progress goes to one `shard-<n>.log` per worker in the cache directory its first line names,
so this mode finds the wrapper process and the split runs `--boards-view` instead of `tail`: a bar
for the whole pass and one per shard, with %, time left at the rate the shards are showing, the
last step's age and a red stall mark, as the duel build's view draws them
(`er-mechanics-pipeline-watch.py`). It reads only the logs and the cache, never the generator.
The guard `.cupcake/policies/claude/no_untailed_long_run.rego` asks for this form.

    python3 scripts/kitty-tail-task.py --boards-view --once [--file <task output file>]   # one frame

    python3 /home/banon/projects/er-mods-rs/scripts/kitty-tail-task.py --split \\
        --title 'tail: <what> (<task id>)' --file <task .output file> [--file <other file> ...]

is the form the global Stop guard `~/.config/cupcake/policies/claude/background_task_needs_tail.rego`
suggests for a background task started in this repo (2026-11-16). `--split` names the default, a
split next to `$KITTY_WINDOW_ID`, as the user's "open a kitty split tailing it, always" asks; it is
kept so that suggestion, and the guards that quote it, stay valid, and `--tab` still wins over it.
With `--file` and no `--pid` or `--match`, the task is every live process holding the first file
open for writing (`/proc/<pid>/fd` plus the `fdinfo` flags, so a reader such as another tail does
not count), the topmost of each tree, which is the shell a background task runs in. That is the
lookup `~/.local/bin/kitty-tail-bg` does; the global guard suggests that helper elsewhere, but this
repo's `bash_no_python_file_write.rego` refuses it because it is a python program committed nowhere
here. When no writer turns up within two seconds the task has ended: the window prints the files
once and closes.

This is the watcher counterpart of `kitty-user-command.py`, which hands the user a command to
run; this one runs nothing for the user to approve. It reuses that helper's listener lookup
(`$KITTY_LISTEN_ON`), `kitty @` wrapper and split-or-tab placement.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: Seconds the window stays open after the task's process exits (user, 2026-10-19).
LINGER_S = 5
#: The programs that launch a task by running its command line as a string. One of these that
#: matches and is an ancestor of a match with another command line is the launcher, not the task.
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"})


def _kuc():
    spec = importlib.util.spec_from_file_location("kitty_user_command", HERE / "kitty-user-command.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tail_argv(path: str | list[str], pid: int | list[int], linger: int = LINGER_S) -> list[str]:
    """The program the window runs.

    Several paths are for a run whose real progress is spread over worker logs: the board
    generator's wrapper prints four setup lines, and every line after that goes to one
    `shard-<n>.log` per worker in its cache directory (2026-10-19). `tail` heads each file's
    lines with its name, so the shards stay apart in one window.

    Several pids follow until every one is gone. No pid is a task that already ended: the files
    are printed once and the window closes after the linger.
    """
    paths = [path] if isinstance(path, str) else list(path)
    pids = [pid] if isinstance(pid, int) else list(pid)
    quoted = " ".join(shlex.quote(p) for p in paths)
    if pids:
        follow = " ".join(f"--pid={p}" for p in pids)
        script = f"tail -n +1 -F {follow} {quoted}; sleep {linger}"
    else:
        script = (f"tail -n +1 {quoted}; echo '[task already ended; closing in {linger} s]'; "
                  f"sleep {linger}")
    return ["bash", "-c", script]


def _parent(pid: int, proc: Path = Path("/proc")) -> int | None:
    try:
        return int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _ancestors(pid: int, proc: Path = Path("/proc")) -> set[int]:
    seen: set[int] = set()
    p = _parent(pid, proc)
    while p and p > 1 and p not in seen:
        seen.add(p)
        p = _parent(p, proc)
    return seen


def opened_for_write(pid: str, fd: str, proc: Path = Path("/proc")) -> bool:
    """Whether fd `fd` of `pid` is open O_WRONLY or O_RDWR (fdinfo `flags:` is octal)."""
    try:
        for line in (proc / pid / "fdinfo" / fd).read_text(errors="replace").splitlines():
            if line.startswith("flags:"):
                return int(line.split()[1], 8) & 0o3 in (0o1, 0o2)
    except (OSError, ValueError, IndexError):
        pass
    return False


def writers(path: str, proc: Path = Path("/proc"), me: int | None = None) -> list[int]:
    """The topmost live processes holding `path` open for writing, this process and its ancestors
    left out (Claude Code itself may hold a task's output file, and it is this process's ancestor).
    Ported from `~/.local/bin/kitty-tail-bg` on 2026-11-16."""
    target = os.path.realpath(path)
    me = os.getpid() if me is None else me
    skip = {me} | _ancestors(me, proc)
    found: set[int] = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        try:
            fds = os.listdir(entry / "fd")
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(entry / "fd" / fd)
            except OSError:
                continue
            if link == target and opened_for_write(entry.name, fd, proc):
                found.add(int(entry.name))
                break
    return sorted(p for p in found if _ancestors(p, proc).isdisjoint(found))


#: inotify(7)'s "file was opened" event, from <sys/inotify.h>.
IN_OPEN = 0x00000020
#: Seconds to look for a writer of `--file` before treating the task as ended. A tail sent in the
#: same message as the task can run before the task's shell has the file open.
WRITER_WAIT_S = 2.0


def wait_for_writers(path: str, wait: float = WRITER_WAIT_S) -> list[int]:
    """The writers of `path`, looked for again each time inotify reports the file opened or written,
    until `wait` runs out. A file that does not exist yet, or no inotify, gets one look."""
    sys.path.insert(0, str(HERE))
    from cupcake_push_authorization import IN_MODIFY, FileEvents

    deadline = time.monotonic() + wait
    try:
        events = FileEvents(path, IN_MODIFY | IN_OPEN)
    except OSError:
        return writers(path)
    with events:
        while True:
            pids = writers(path)
            remaining = deadline - time.monotonic()
            if pids or remaining <= 0:
                return pids
            events.wait(remaining)


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
    process excluded). A match that descends from another match is part of that one, so a task
    and the pool workers it forks are the task.

    When several match, `stdout` breaks the tie first -- the process whose standard output is that
    file, which for a background task is the harness's output file -- and then `root`: only the
    processes whose working directory lies in that checkout are kept. Two sessions regenerating boards at once from different checkouts
    matched together on 2026-10-19, and the tab was refused.
    """
    me = os.getpid() if me is None else me
    hits: dict[int, tuple[str, str]] = {}  # pid -> (program name, command line)
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
            program = os.path.basename(argv[0].decode("utf-8", "replace")).lstrip("-")  # UTF-8 Lossy: compared with shell names, never written back
            hits[int(entry.name)] = (program, line)

    def ancestors(pid):
        seen = set()
        while pid > 1 and pid not in seen:
            seen.add(pid)
            try:
                pid = int((proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
            yield pid

    lineage = {p: set(ancestors(p)) for p in hits}
    # A shell that launched the task (`zsh -c '... er-weapon-build.py ...'`) matches as well, and
    # its command line is not the task's: drop it. A shell whose matching descendant carries its
    # own command line is a script and its forked subshell, and stays.
    launchers = {a for p in hits for a in lineage[p]
                 if a in hits and hits[a][0] in SHELLS and hits[a][1] != hits[p][1]}
    for a in launchers:
        del hits[a]
    # A match below another match is part of it: a pool's workers carry their task's command line
    # and standard output, so a task that forks them matches once per process (2118456/7/8 on
    # 2026-11-11, where the refusal named three). Each tree of matches is its topmost process,
    # which outlives the rest. On 2026-11-12 the committed form, which dropped every match that
    # had a matching descendant, threw away a `--jobs 6` cache build and kept its six workers.
    pids = sorted(p for p in hits if lineage[p].isdisjoint(hits))
    if len(pids) > 1 and stdout:
        def writes_to(pid):
            try:
                return os.path.realpath(os.readlink(proc / str(pid) / "fd" / "1")) == os.path.realpath(stdout)
            except OSError:
                return False
        mine = [p for p in pids if writes_to(p)]
        if len(mine) == 1:
            pids = mine
    if len(pids) > 1 and root is not None:
        def in_root(pid):
            try:
                return repo_root(os.readlink(proc / str(pid) / "cwd")) == Path(root)
            except OSError:
                return False
        mine = [p for p in pids if in_root(p)]
        if len(mine) == 1:
            pids = mine
    if len(pids) != 1:
        found = "; ".join(f"{p} {hits[p][1][:100]}" for p in pids) or "none"
        raise SystemExit(f"--match must name exactly one live process, found {len(pids)}: {found}")
    return pids[0]


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
    # Woken by the wrapper's own writes (inotify), not a poll; the watch is added before each read
    # so a line written between the two still wakes the next wait.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cupcake_push_authorization import IN_MODIFY, FileEvents

    deadline = time.monotonic() + wait
    with FileEvents(wrapper_out, IN_MODIFY) as writes:
        while True:
            try:
                for line in Path(wrapper_out).read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("cache "):
                        return Path(line[len("cache "):].strip())
            except OSError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SystemExit(f"{wrapper_out} has no 'cache <dir>' line after {wait:.0f} s; is it "
                                 f"the output of {BOARDS_SCRIPT}?")
            writes.wait(remaining)


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
    """`er-mechanics-pipeline-watch.py`, for the duel build's bar, clock, colors and `Pulse`."""
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
    seconds; the window running this closes when it returns."""
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
    """The program a board window runs: this script's own view of the run."""
    return [sys.executable, str(Path(__file__).resolve()), "--boards-view", "--file", wrapper_out,
            "--pid", str(pid), "--jobs", str(jobs)]


def launch_argv(title: str, path: str | list[str], pid: int | list[int], program: list[str] | None,
                kind: str) -> tuple[list[str], str]:
    """The `kitty @ launch` arguments, and where the window lands in words. `program` replaces the
    `tail` the window runs otherwise (the board view). Never `--hold`: the window closes when its
    program ends."""
    where_argv, where = _kuc().placement(kind, title)
    return ["launch", *where_argv, "--keep-focus", *(program or tail_argv(path, pid))], where


def open_window(title: str, path: str | list[str], pid: int | list[int], dry_run: bool,
                program: list[str] | None = None, kind: str = "split") -> int:
    argv, where = launch_argv(title, path, pid, program, kind)
    if dry_run:
        print("kitty @ " + shlex.join(argv))
        return 0
    k = _kuc()
    window_id = k.kitty(k.listener_or_exit(), *argv).strip()
    if not window_id.isdigit():
        raise SystemExit(f"kitty @ launch did not return a window id: {window_id!r}")
    what = f"shows the board progress of {path}" if program else f"tails {path}"
    pids = [pid] if isinstance(pid, int) else list(pid)
    until = (f"until pid {', '.join(map(str, pids))} exits" if pids
             else "once (no live writer: the task already ended)")
    print(f"{kind} {title!r} (window {window_id}, in {where}) {what} {until}, "
          f"then closes after {LINGER_S} s")
    for old in superseded(kitty_windows(k), title, int(window_id)):
        k.kitty(k.listener_or_exit(), "close-window", f"--match=id:{old}")
        print(f"closed window {old}, an earlier view of {title!r}")
    return 0


def superseded(windows: list[dict], title: str, new_id: int) -> list[int]:
    """The windows a view just opened as `new_id` replaces: every other window titled exactly
    `title`. Reopening a task's view with other files is how its view changes, and the earlier one
    would otherwise stay open until the task ends (2026-11-17: a run's split carried the agent's
    memory sampler beside its progress for hours). So is every window whose title names the same
    background task id: a task has one view, and an agent's view of a queue that followed only its
    first log stayed open beside the one reopened to add the second (2026-11-17)."""
    task = TASK_ID_RE.search(title)
    return [w["id"] for w in windows if w["id"] != new_id
            and (w.get("title") == title or (task and task.group(0) in w.get("title", "")))]


#: Seconds a held tail's files must have gone unwritten before `--reap` closes its split.
REAP_QUIET_S = 5.0
#: A background task id in a split's title, as the Stop guards ask titles to carry one.
TASK_ID_RE = re.compile(r"\((b[a-z0-9]{8})\)")


def held_tail_files(win: dict) -> list[str] | None:
    """The files a split of the old form (`kitty @ launch --hold tail -F <files>`, no `--pid`)
    follows, or None for any other window: that tail never exits, so the split never closes on its
    own (2026-11-16). A split this helper opened runs `bash -c 'tail ... --pid=...'` and is None."""
    cmd = win.get("cmdline") or []
    at = next((i for i, a in enumerate(cmd) if os.path.basename(a) == "tail"), None)
    if at is None:
        return None
    args = cmd[at + 1:]
    if any(a.startswith("--pid") for a in args):
        return None
    return [a for a in args if a.startswith("/")] or None


def claude_tmp_root(env=None, uid: int | None = None) -> Path:
    """Claude Code's per-user temp root, where its task outputs live: `claude-<uid>` under
    `$CLAUDE_CODE_TMPDIR`, else under the OS temp dir node's `os.tmpdir()` reads (`$TMPDIR`,
    `$TMP`, `$TEMP`, then `/tmp`). Read from the Claude Code binary, which builds it as
    `join(env.CLAUDE_CODE_TMPDIR || os.tmpdir(), "claude-" + process.getuid())`."""
    env = os.environ if env is None else env
    base = next((env[k] for k in ("CLAUDE_CODE_TMPDIR", "TMPDIR", "TMP", "TEMP") if env.get(k)), "/tmp")
    return Path(base.rstrip("/") or "/") / f"claude-{os.getuid() if uid is None else uid}"


def task_outputs(title: str, roots=None) -> list[str]:
    """The `<project>/<session>/tasks/<id>.output` files of the task id a split's title names."""
    m = TASK_ID_RE.search(title or "")
    if not m:
        return []
    roots = (claude_tmp_root(),) if roots is None else roots
    return [str(p) for root in roots if root.is_dir() for p in root.glob(f"*/*/tasks/{m.group(1)}.output")]


def running_task_ids() -> set[str]:
    """The background shells of this Claude Code session and its subagents that are still alive
    (`cupcake_session_tasks.py --json`, which reads the process table). A task whose output the
    harness writes itself has no writer of its own the fd lookup can see (2026-11-16, bydijwr4b)."""
    import json
    out = subprocess.run([sys.executable, str(HERE / "cupcake_session_tasks.py"), "--json"],
                         capture_output=True, text=True, timeout=30).stdout
    try:
        return {s["task_id"] for s in json.loads(out).get("shells", []) if s.get("task_id")}
    except (ValueError, AttributeError):
        raise SystemExit("cupcake_session_tasks.py --json gave no report: not reaping blind")


def reapable(win: dict, writers_fn=writers, mtime_fn=_mtime, outputs_fn=task_outputs,
             now: float | None = None, quiet: float = REAP_QUIET_S, running: set[str] = frozenset()) -> bool:
    """Whether `--reap` closes `win`: a held tail whose title's task is not in `running`, none of
    whose files (nor that task's output) a live process holds open for writing, and none written
    in the last `quiet` seconds."""
    files = held_tail_files(win)
    if not files:
        return False
    m = TASK_ID_RE.search(win.get("title") or "")
    if m and m.group(1) in running:
        return False
    now = time.time() if now is None else now
    for f in files + outputs_fn(win.get("title", "")):
        if writers_fn(f):
            return False
        m = mtime_fn(f)
        if m is not None and now - m < quiet:
            return False
    return True


def kitty_windows(k) -> list[dict]:
    import json
    return [w for o in json.loads(k.kitty(k.listener_or_exit(), "ls")) for t in o["tabs"] for w in t["windows"]]


def reap(dry_run: bool, title_prefix: str | None = None, quiet: float = REAP_QUIET_S) -> list[int]:
    """Close every finished held tail split (`reapable`); `title_prefix` limits it to splits whose
    title starts so. Only those splits are printed, never the user's other windows."""
    k = _kuc()
    running = running_task_ids()
    closed = []
    for w in kitty_windows(k):
        if title_prefix and not (w.get("title") or "").startswith(title_prefix):
            continue
        if not reapable(w, quiet=quiet, running=running):
            continue
        print(f"{'would close' if dry_run else 'closing'} window {w['id']}: {w.get('title', '')}")
        if not dry_run:
            k.kitty(k.listener_or_exit(), "close-window", f"--match=id:{w['id']}")
        closed.append(w["id"])
    print(f"{len(closed)} finished tail split(s) {'to close' if dry_run else 'closed'}")
    return closed


def reap_selftest() -> int:
    """Live: two held tail splits like the old form opens, one on a writer that ends and one on a
    writer that stays; `reap` must close the first and keep the second, then close the second once
    its writer is gone. Only splits titled `REAP_TEST` are ever touched."""
    import tempfile
    k = _kuc()
    listener = k.listener_or_exit()
    window_id = os.environ.get("KITTY_WINDOW_ID", "").strip() or None
    prefix = f"tail: reap-selftest {os.getpid()}"
    bad = []

    def check(name, ok):
        print(f"{'ok' if ok else 'FAIL'}: {name}")
        if not ok:
            bad.append(name)

    with tempfile.TemporaryDirectory() as d:
        done, live = Path(d, "done.log"), Path(d, "live.log")
        quick = subprocess.Popen(["bash", "-c", f"echo started > {shlex.quote(str(done))}; sleep 1"])
        stay = subprocess.Popen(["bash", "-c", f"echo started > {shlex.quote(str(live))}; exec sleep 120"],
                                stdout=open(live, "a"))
        ids = {}
        for name, path in (("done", done), ("live", live)):
            argv = ["launch", "--type=window"]
            if window_id:
                argv += [f"--match=window_id:{window_id}", f"--next-to=id:{window_id}"]
            argv += ["--location=split", "--keep-focus", "--hold", f"--title={prefix} {name}",
                     "tail", "-n", "+1", "-F", str(path)]
            ids[name] = int(k.kitty(listener, *argv).strip())
        try:
            # `quiet=0`: the writers' exits are what these checks are about, not the files' age.
            quick.wait()
            closed = reap(False, prefix, quiet=0.0)
            present = {w["id"] for w in kitty_windows(k)}
            check("the split of a finished writer is closed", ids["done"] in closed and ids["done"] not in present)
            check("the split of a live writer stays open", ids["live"] not in closed and ids["live"] in present)
            stay.terminate()
            stay.wait()
            closed = reap(False, prefix, quiet=0.0)
            present = {w["id"] for w in kitty_windows(k)}
            check("it closes once its writer is gone", ids["live"] in closed and ids["live"] not in present)
        finally:
            for p in (quick, stay):
                if p.poll() is None:
                    p.kill()
            present = {w["id"] for w in kitty_windows(k)}
            for wid in ids.values():
                if wid in present:
                    k.kitty(listener, "close-window", f"--match=id:{wid}")
    print("reap selftest ok" if not bad else "reap selftest FAILED")
    return 1 if bad else 0


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
        check("the window runs the view with the wrapper's output, pid and jobs",
              boards_argv(out, 42, 3)[2:], ["--boards-view", "--file", out, "--pid", "42", "--jobs", "3"])


def stop_session(*procs: subprocess.Popen) -> None:
    """Kill each selftest process started with `start_new_session=True`, with everything it forked.
    Killing the shell alone left its `sleep 30` holding the selftest's stdout for half a minute."""
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


def pool_selftest(check) -> None:
    """A task and the workers it forks are one match; two unrelated tasks are still refused."""
    def matching(marker: str) -> int:
        n = 0
        for entry in Path("/proc").iterdir():
            try:
                n += entry.name.isdigit() and marker.encode() in (entry / "cmdline").read_bytes()
            except OSError:
                pass
        return n

    def started(argv: list[str], marker: str, count: int, stdout=subprocess.DEVNULL,
                cwd: str | None = None) -> subprocess.Popen:
        """`argv` in a session of its own, once `count` processes carry `marker`. Each look is a
        walk of /proc, which paces the loop; stdin is a pipe a Python task can block on."""
        p = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.PIPE, stdout=stdout,
                             stderr=subprocess.DEVNULL, cwd=cwd)
        deadline = time.monotonic() + 10
        while matching(marker) < count and time.monotonic() < deadline:
            pass
        return p

    # The workers carry the task's command line: `fork` makes them the task's children, and
    # `forkserver` (Python 3.14's default) the children of a server the task starts, whose own
    # command line ends in the task's arguments. argv: method, worker count, marker.
    pool = ("import multiprocessing as m, sys, time\n"
            "p = m.get_context(sys.argv[1]).Pool(int(sys.argv[2]))\n"
            "p.map(time.sleep, [0] * int(sys.argv[2]))\n"
            "sys.stdin.read()\n")
    # (method, workers, processes carrying the marker); fork with six is the 2026-11-12 shape, a
    # `--jobs 6` cache build that was refused as its six workers.
    for method, workers, count in (("fork", 2, 3), ("forkserver", 2, 4), ("fork", 6, 7)):
        marker = f"kitty-tail-pool-{method}-{workers}-marker"
        task = started([sys.executable, "-c", pool, method, str(workers), marker], marker, count)
        try:
            check(f"a {method} pool of {workers}: its workers match too", matching(marker) >= count, True)
            check(f"a {method} pool of {workers} is its task, not a worker", find_pid([marker]), task.pid)
        finally:
            stop_session(task)
    # Two pools in different checkouts, each writing its own output: a tie between the two tasks,
    # never their workers, which the stdout and checkout tie-breaks then settle.
    marker = "kitty-tail-pool-twin-marker"
    import tempfile
    with tempfile.TemporaryDirectory() as a_dir, tempfile.TemporaryDirectory() as b_dir:
        dirs = (a_dir, b_dir)
        outs = [os.path.join(d, "task.out") for d in dirs]
        for d in dirs:
            os.mkdir(os.path.join(d, ".git"))
        handles = [open(o, "w") for o in outs]
        tasks = [started([sys.executable, "-c", pool, "fork", "3", marker], marker, 4 * (n + 1),
                         stdout=handles[n], cwd=dirs[n]) for n in range(2)]
        try:
            try:
                find_pid([marker])
                named = "found nothing"
            except SystemExit as e:
                msg = str(e)
                head = "--match must name exactly one live process, found 2: "
                named = ({int(s.split(" ", 1)[0]) for s in msg[len(head):].split("; ")}
                         if msg.startswith(head) else msg)
            check("two unrelated pools are refused, naming the two tasks only", named,
                  {t.pid for t in tasks})
            check("stdout picks the pool writing to that file", find_pid([marker], stdout=outs[1]),
                  tasks[1].pid)
            check("root picks the pool working in that checkout", find_pid([marker], root=Path(a_dir)),
                  tasks[0].pid)
        finally:
            stop_session(*tasks)
            for h in handles:
                h.close()
    # A script's forked subshell carries the script's command line: the script is the task.
    marker = "kitty-tail-subshell-marker"
    script = started(["bash", "-c", "( sleep 30; true ); true", marker], marker, 2)
    try:
        check("a script's forked subshell is the script", find_pid([marker]), script.pid)
    finally:
        stop_session(script)


def writers_selftest(check) -> None:
    """`--file` alone: the task is the writer of the file; the split never runs `--hold`."""
    import tempfile
    import threading

    def until(cond, limit=5.0):
        """Ask `cond` until it holds; each condition here walks /proc, which paces the loop."""
        deadline = time.monotonic() + limit
        while not cond() and time.monotonic() < deadline:
            pass

    def holds_open(pid: int, path: str) -> bool:
        target = os.path.realpath(path)
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            return False
        for fd in fds:
            try:
                if os.path.realpath(f"/proc/{pid}/fd/{fd}") == target:
                    return True
            except OSError:
                pass
        return False

    check("tail argv, several pids", tail_argv(["/tmp/a", "/tmp/b c"], [41, 42]),
          ["bash", "-c", "tail -n +1 -F --pid=41 --pid=42 /tmp/a '/tmp/b c'; sleep 5"])
    check("tail argv, no pid prints once", tail_argv("/tmp/x", [])[2].startswith("tail -n +1 /tmp/x; echo "), True)
    saved = os.environ.get("KITTY_WINDOW_ID")
    os.environ["KITTY_WINDOW_ID"] = "7"
    try:
        argv, _ = launch_argv("tail: x (b1)", "/tmp/x", 1, ["bash", "-c", "true"], "split")
        check("split: next to this window, keep focus, no --hold",
              (argv[:7], any(a.startswith("--hold") for a in argv)),
              (["launch", "--type=window", "--location=split", "--window-title=tail: x (b1)",
                "--match=window_id:7", "--next-to=id:7", "--keep-focus"], False))
    finally:
        if saved is None:
            os.environ.pop("KITTY_WINDOW_ID", None)
        else:
            os.environ["KITTY_WINDOW_ID"] = saved
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "task one.output")
        # A background task as Claude Code runs it: a shell whose stdout and stderr are the output
        # file, with a child that inherits them. The shell is the one to watch.
        with open(out, "w") as fh:
            task = subprocess.Popen(["bash", "-c", "echo started; sleep 30; true"], stdout=fh,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        try:
            until(lambda: len(writers(out)) > 0 and os.path.getsize(out) > 0)
            check("the task's shell is the writer, not its child", writers(out), [task.pid])
            reader = subprocess.Popen(["tail", "-F", out], stdout=subprocess.DEVNULL, start_new_session=True)
            try:
                until(lambda: holds_open(reader.pid, out))
                check("a reader is not a writer", (holds_open(reader.pid, out), writers(out)),
                      (True, [task.pid]))
            finally:
                stop_session(reader)
            env = dict(os.environ, KITTY_WINDOW_ID="7")
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--dry-run", "--split",
                                "--title", "tail: t (b1)", "--file", out], capture_output=True, text=True,
                               timeout=20, env=env)
            check("--split --file alone: a split following the writer's pid",
                  (r.returncode, f"--pid={task.pid}" in r.stdout, "--location=split" in r.stdout,
                   "--hold" in r.stdout), (0, True, True, False))
            # The split's shell starts in kitty's directory, not this one (2026-11-19: a relative
            # target/v29-kit/kit.out tailed "No such file or directory" for three hours).
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--dry-run", "--split",
                                "--title", "tail: t (b1)", "--file", os.path.basename(out)],
                               capture_output=True, text=True, timeout=20, env=env,
                               cwd=os.path.dirname(out))
            check("a relative --file reaches the split as an absolute path",
                  (r.returncode, shlex.quote(os.path.realpath(out)) in r.stdout), (0, True))
        finally:
            stop_session(task)
        check("no writer once the task ended", writers(out), [])
        # The program ends after the task and the linger: a 1 s writer, 1 s linger.
        with open(out, "w") as fh:
            short = subprocess.Popen(["bash", "-c", "echo line one; sleep 1; true"], stdout=fh)
        threading.Thread(target=short.wait, daemon=True).start()  # reaped: a zombie still counts
        until(lambda: os.path.getsize(out) > 0)
        t0 = time.monotonic()
        r = subprocess.run(tail_argv(out, writers(out) or [short.pid], linger=1), capture_output=True,
                           text=True, timeout=20)
        check("the split's tail ends after the writer and the linger (0.8 to 6 s)",
              ("line one" in r.stdout, 0.8 <= time.monotonic() - t0 <= 6.0), (True, True))


def launch_selftest(check) -> None:
    """A split goes in the tab of the window named by `$KITTY_WINDOW_ID`; `--tab` opens a tab."""
    saved = os.environ.get("KITTY_WINDOW_ID")
    os.environ["KITTY_WINDOW_ID"] = "9"
    try:
        argv, where = launch_argv("t", "/tmp/a b.out", 42, None, "split")
        check("a split launches a window in the agent's tab, beside the agent's window",
              (argv[:7], where), (["launch", "--type=window", "--location=split", "--window-title=t",
                                   "--match=window_id:9", "--next-to=id:9", "--keep-focus"],
                                  "the agent's tab, next to window 9"))
        check("and runs the tail", argv[7:], tail_argv("/tmp/a b.out", 42))
        argv, where = launch_argv("t", "/tmp/a b.out", 42, ["view"], "tab")
        check("--tab launches a tab with the old title option",
              (argv, where), (["launch", "--type=tab", "--tab-title=t", "--keep-focus", "view"], "a new tab"))
        os.environ.pop("KITTY_WINDOW_ID")
        argv, where = launch_argv("t", "/tmp/a b.out", 42, None, "split")
        check("without KITTY_WINDOW_ID the split goes to the focused tab and says so",
              ("--match=window_id:9" in argv, where.startswith("the focused tab")), (False, True))
    finally:
        if saved is None:
            os.environ.pop("KITTY_WINDOW_ID", None)
        else:
            os.environ["KITTY_WINDOW_ID"] = saved


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
    sleeper = subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-selftest-marker"],
                               start_new_session=True)
    try:
        check("find_pid finds the one process", find_pid(["kitty-tail-selftest-marker"]), sleeper.pid)
        # A wrapping shell whose command line also holds the marker is its ancestor, so dropped.
        wrap = subprocess.Popen(["bash", "-c", "bash -c 'sleep 30; true' kitty-tail-nested-marker; true",
                                 "kitty-tail-nested-marker"], start_new_session=True)
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
            stop_session(wrap)
        try:
            find_pid(["no-such-process-anywhere-xyz"])
            check("find_pid refuses no match", False, True)
        except SystemExit:
            check("find_pid refuses no match", True, True)
    finally:
        stop_session(sleeper)
    # root: of two matching processes, the one working in the named checkout.
    with tempfile.TemporaryDirectory() as a_dir, tempfile.TemporaryDirectory() as b_dir:
        for d in (a_dir, b_dir):
            os.mkdir(os.path.join(d, ".git"))
        os.mkdir(os.path.join(b_dir, "sub"))
        twins = [subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-twin-marker"], cwd=cwd,
                                  start_new_session=True)
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
            stop_session(*twins)
        # stdout: of two matching processes, the one writing to the named file.
        outs = [os.path.join(a_dir, "one.out"), os.path.join(b_dir, "two.out")]
        handles = [open(o, "w") for o in outs]
        twins = [subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-out-marker"], stdout=h,
                                  start_new_session=True)
                 for h in handles]
        try:
            check("stdout picks the process writing to that file",
                  find_pid(["kitty-tail-out-marker"], stdout=outs[1]), twins[1].pid)
        finally:
            stop_session(*twins)
            for h in handles:
                h.close()
    excluded =subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-excl-marker", "--shard"],
                               start_new_session=True)
    kept = subprocess.Popen(["bash", "-c", "sleep 30; true", "kitty-tail-excl-marker"], start_new_session=True)
    try:
        check("exclude drops a match", find_pid(["kitty-tail-excl-marker"], exclude=("--shard",)), kept.pid)
    finally:
        stop_session(excluded, kept)
    pool_selftest(check)
    writers_selftest(check)
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
    wins = [{"id": 5, "title": "tail: run (b1234abcd)"}, {"id": 6, "title": "tail: run (b1234abcd)"},
            {"id": 7, "title": "tail: run (b1234abcd) 2"}, {"id": 8, "title": "editor"}]
    check("a reopened view replaces every view of its task", superseded(wins, "tail: run (b1234abcd)", 6), [5, 7])
    check("a first view replaces nothing", superseded(wins[3:], "tail: run (b1234abcd)", 9), [])
    check("a view under another title of the same task is replaced",
          superseded(wins, "tail: queue again (b1234abcd)", 9), [5, 6, 7])
    check("a title with no task id replaces only its exact title",
          superseded(wins + [{"id": 10, "title": "editor"}], "editor", 10), [8])
    check("tmp root: CLAUDE_CODE_TMPDIR wins",
          claude_tmp_root({"CLAUDE_CODE_TMPDIR": "/x/", "TMPDIR": "/y"}, uid=7), Path("/x/claude-7"))
    check("tmp root: TMPDIR next", claude_tmp_root({"TMPDIR": "/y"}, uid=7), Path("/y/claude-7"))
    check("tmp root: /tmp by default", claude_tmp_root({}, uid=7), Path("/tmp/claude-7"))
    check("tmp root: the running user's uid", claude_tmp_root({}).name, f"claude-{os.getuid()}")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d, "-proj", "sess", "tasks", "b1234abcd.output")
        out.parent.mkdir(parents=True)
        out.write_text("x\n")
        check("task_outputs finds the titled task's output under a root",
              task_outputs("tail: run (b1234abcd)", roots=(Path(d),)), [str(out)])
    launch_selftest(check)
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
                    help="draw the board run's progress bar here (what a --boards window runs); "
                         "--file is the wrapper output, --pid the wrapper")
    ap.add_argument("--tab", action="store_true",
                    help="open a new tab instead of a split in the agent's own tab")
    ap.add_argument("--jobs", type=int, default=3,
                    help="--boards-view: the --jobs the run was asked for, used until its output "
                         "says how many shards it was granted")
    ap.add_argument("--once", action="store_true", help="--boards-view: print one frame and exit")
    ap.add_argument("--cwd", type=Path,
                    help="when several processes match, keep the one working in this checkout "
                         "(default: the checkout this command runs in)")
    ap.add_argument("--split", action="store_true",
                    help="open a split next to $KITTY_WINDOW_ID, the default; kept because the "
                         "global background-task Stop guard suggests it (--tab wins over it)")
    ap.add_argument("--reap", action="store_true",
                    help="close every split of the old --hold tail -F form whose task has finished "
                         "(no live writer, files quiet); with --dry-run, list them")
    ap.add_argument("--reap-selftest", action="store_true",
                    help="prove --reap on two test splits of its own before it is run on real ones")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    # The split's shell starts in kitty's directory, so a relative path names nothing there.
    a.file = [os.path.realpath(f) for f in a.file]
    if a.selftest:
        return selftest()
    if a.reap_selftest:
        return reap_selftest()
    if a.reap:
        reap(a.dry_run)
        return 0
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
    kind = "tab" if a.tab else "split"
    if a.boards:
        pid, out, jobs = boards_target(a.pid, a.file, root)
        boards_cache(out)  # refuse now, not inside the window, an output that is not a board run's
        return open_window(a.title, out, pid, a.dry_run, program=boards_argv(out, pid, jobs), kind=kind)
    if not a.file:
        ap.error("--file is required")
    if a.pid:
        pid: int | list[int] = a.pid
    elif a.match:
        pid = find_pid(a.match, root=root, stdout=a.file[0])
    else:
        # No --pid or --match: the task is whatever holds the first file open for writing.
        pid = wait_for_writers(a.file[0])
    return open_window(a.title, a.file[0] if len(a.file) == 1 else a.file, pid, a.dry_run, kind=kind)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
