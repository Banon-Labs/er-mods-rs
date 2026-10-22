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
so this mode finds the wrapper process, reads that line, and follows the wrapper output plus every
shard log. The guard `.cupcake/policies/claude/no_untailed_long_run.rego` asks for this form.

This is the watcher counterpart of `kitty-user-command.py`, which hands the user a command to
run; this one runs nothing for the user to approve. It reuses that helper's listener lookup
(`$KITTY_LISTEN_ON`) and `kitty @` wrapper.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shlex
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


def boards_files(wrapper_out: str, jobs: int, wait: float = BOARDS_CACHE_WAIT_S) -> list[str]:
    """The wrapper's own output, then every shard log it can write. A shard log that does not
    exist yet is followed anyway: `tail -F` picks it up when the shard opens it."""
    cache = boards_cache(wrapper_out, wait)
    return [wrapper_out] + [str(cache / f"shard-{i}.log") for i in range(jobs)]


def boards_target(pid: int | None, files: list[str], root: Path | None) -> tuple[int, list[str]]:
    """(wrapper pid, files to follow) for a board regeneration. The wrapper's output defaults to
    whatever its stdout is open on, which for a background task is the harness's output file."""
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
    return pid, boards_files(wrapper_out, boards_jobs(cmdline))


def open_tab(title: str, path: str | list[str], pid: int, dry_run: bool) -> int:
    argv = ["launch", "--type=tab", "--keep-focus", f"--tab-title={title}", *tail_argv(path, pid)]
    if dry_run:
        print("kitty @ " + shlex.join(argv))
        return 0
    k = _kuc()
    window_id = k.kitty(k.listener_or_exit(), *argv).strip()
    if not window_id.isdigit():
        raise SystemExit(f"kitty @ launch did not return a window id: {window_id!r}")
    print(f"tab {title!r} (window {window_id}) tails {path} until pid {pid} exits, then closes after {LINGER_S} s")
    return 0


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
        check("boards files", boards_files(out, boards_jobs(["python3", BOARDS_SCRIPT, "--jobs", "2"]), wait=0),
              [out, f"{d}/key/shard-0.log", f"{d}/key/shard-1.log"])
        check("boards jobs default", boards_jobs(["python3", BOARDS_SCRIPT]), 3)
        with open(out, "w") as f:
            f.write("nothing here\n")
        try:
            boards_files(out, 3, wait=0)
            check("boards refuses an output with no cache line", False, True)
        except SystemExit:
            check("boards refuses an output with no cache line", True, True)
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
    ap.add_argument("--cwd", type=Path,
                    help="when several processes match, keep the one working in this checkout "
                         "(default: the checkout this command runs in)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    root = repo_root(a.cwd if a.cwd else os.getcwd())
    if a.boards:
        pid, files = boards_target(a.pid, a.file, root)
        return open_tab(a.title, files, pid, a.dry_run)
    if not a.file or not (a.pid or a.match):
        ap.error("--file and one of --pid / --match are required")
    pid = a.pid or find_pid(a.match, root=root, stdout=a.file[0])
    return open_tab(a.title, a.file[0] if len(a.file) == 1 else a.file, pid, a.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
