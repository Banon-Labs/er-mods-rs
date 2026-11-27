#!/usr/bin/env python3
"""Find background check-suite runs (a `git push`, whose pre-push hook runs the suite) that nothing
wakes the agent on when one of their stages goes red.

The user's words (2026-11-25), verbatim: "It would be wise, and prudent, to not wait until all
checks pass before addressing any one of the failing reds in each future case
`>>> stage policy RED (exit 1)   1702s   /run/user/1000/er-mods-rs-pre-push/6abca54e80d19a080c8579b188175fa73d210e01/policy.log`"

What happened: the main session ran `git -C /home/banon/projects/er-mods-rs push -u origin
chore/no-tracker-refs` in the background and opened a kitty split on its output with
`scripts/kitty-tail-push.py`. That split is the user's view; it wakes nobody. The suite prints one
`>>> stage <name> RED (exit <n>) <secs>s <log>` line per stage the moment the stage ends
(`_check_run_stage` in scripts/check.sh) while the other stages keep running for up to an hour
(moveset took 4052 s on the push before). So the policy stage's red sat in the output file until
the push's own completion notification, which came after every other stage had finished.

The only thing that wakes the agent while a background task still runs is a Monitor, whose every
stdout line is a notification. So this module reports each still-running background suite task
that has no still-running Monitor following its output file through a filter that passes a red
stage line as it arrives (each grep stage is evaluated on one, `--line-buffered` included). A
suite whose stage lines are piped into a stage that holds them to the end (`2>&1 | tail -20`)
writes nothing a Monitor could wake on, so it is reported with that stage named and no Monitor
clears it.
A Monitor expires after 30 minutes at most, and an expired one is no longer running
(`cupcake_turn_scan.running_background_tasks` closes it on its expiry event), so a push that
outlives its Monitor is reported again and the Monitor is re-armed.

The shape the report names (`monitor_command`) is the one the main session armed by hand after the
correction, with the end of the stream tied to the push when its pid is known:

    tail -n +1 [--pid=<git push pid>] -F <output> \\
        | grep --line-buffered -E <MONITOR_FILTER> \\
        | python3 <repo>/scripts/monitor-throttle.py 10

`tail -n +1` replays a red that landed before the Monitor was armed. `--pid` ends `tail`, and so
the Monitor, when the push ends; `scripts/kitty-tail-push.py` knows that pid and prints the command
with it after opening its split. Without a pid the Monitor runs until it is stopped or expires, and
`no_unexplained_running_tasks` asks for its TaskStop once the push is over. The final stage is the
throttle because `monitor_rate_limit.rego` refuses any Monitor that does not end in it.

Usage:
    python3 scripts/cupcake_red_stage_watch.py --selftest
    python3 scripts/cupcake_red_stage_watch.py --transcript <session.jsonl>   # what the signal says
The Stop signal `.cupcake/signals/last_assistant_unwatched_red_stage.sh` calls `signal_line`.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cupcake_turn_scan as scan  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# A command counts where it is run, not where it is named (a backgrounded `grep check.sh` is not a
# suite run). Same prefix as no_untailed_long_run's LONG_RUNS.
_RUN = (
    r"(?:\b(?:python3?|bash|sh)\s+(?:-[A-Za-z]\S*\s+)*"
    r"|(?:^|[;&|]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)(?:[^\s'\"(]*/)?"
)
# The suites that print a `>>> stage ... RED` line per stage while the rest keep running: the
# pre-push hook (through `git push`, or run by hand) and a full check.sh. `check.sh --stage <name>`
# runs one stage and ends on its verdict, so its own completion is the event.
#
# A `git push` counts only when it runs this repo's pre-push hook: in this repo or one of its
# worktrees (a `git -C <dir>` or an earlier `cd <dir>` naming a dir whose git common dir is this
# repo's), with no `--no-verify`, `--dry-run`/`-n` or `-c core.hooksPath=` override. A background
# `git -C ~/.config/cupcake push` runs that repo's hook, not this suite.
_WRAPPERS = r"(?:(?:timeout|nice|nohup|command|env|time|stdbuf)(?:\s+(?:-\S+|\d\S*))*\s+)*"
_GIT_PUSH_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*" + _WRAPPERS
    + r"git((?:\s+(?:-[Cc]\s+(?:'[^']*'|\"[^\"]*\"|\S+)|--?[A-Za-z][\w-]*(?:=\S+)?))*)\s+push\b(.*)$",
    re.S)
SUITE_RUNS = [
    ("pre-push", re.compile(_RUN + r"hooks/pre-push\b")),
    ("check.sh", re.compile(_RUN + r"(?<=/)check\.sh\b(?![^;&|]*--stage\b)")),
]
QUICK_FLAGS = re.compile(r"--(?:selftest|help|list-stages|dry-run)\b")

# A suite stage line as `_check_run_stage` prints it: what a watching Monitor's filter must pass.
RED_SAMPLE = ">>> stage policy         RED (exit 1)     1702s   /run/x/policy.log"

# Commands that write their stdout through stdio or hold it to the end, so a stage line they are
# piped reaches the next stage only when a buffer fills or they exit. grep and sed are live with
# `--line-buffered` / `-u`, and any of these under `stdbuf -oL` / `-o0` except the ones that hold
# lines by design.
_HOLD_BY_DESIGN = {"tail", "head", "sort", "wc", "uniq", "tac", "less", "more"}
_STDIO_BUFFERED = {"grep", "egrep", "fgrep", "sed", "awk", "gawk", "mawk", "cut", "tr", "column"}
_LIVE = {"cat", "tee"}

# The filter `monitor_command` writes. Every terminal state a push can reach, not only the red
# stage: a refusal before the suite, a compile error in check-committed-compiles, a rejected ref,
# and the harness's own `[exited with code <n>]` line at the end of the output file.
MONITOR_FILTER = "RED|FAIL|error|REFUSED|rejected|exited with code"

# A command's own redirect of both streams to a file, which is where its output then goes.
_BOTH_STREAMS_RE = re.compile(r"2>&1|&>")
_REDIRECT_RE = re.compile(r"(?:^|\s)(?:1|&)?>>?\s*(?!&)([^\s;&|]+)")


@dataclass
class Unwatched:
    task_id: str
    name: str
    command: str
    output: str  # the file a Monitor should follow, or "" when the transcript does not name one
    held: str = ""  # the pipe stage that holds the stage lines until the task ends, if any


@dataclass
class SuiteRun:
    name: str
    redirect: str = ""  # the file the suite's own redirect sends both streams to
    held: str = ""  # the first downstream stage that holds the stage lines until it ends
    tees: tuple[str, ...] = ()  # files a `tee` ahead of `held` writes the stage lines to live


def _pipelines(cmd: str) -> list[list[tuple[str, str]]]:
    """Split a shell command at its unquoted control operators (`;`, `&&`, `||`, `&`, newline)
    into pipelines, and each pipeline at its unquoted `|` / `|&` into stages. A stage is
    `(text, op)`, where `op` is the pipe that feeds the next stage, or ""."""
    pipelines: list[list[tuple[str, str]]] = []
    stages: list[tuple[str, str]] = []
    cur: list[str] = []
    quote = ""
    i, n = 0, len(cmd)

    def end_stage(op: str) -> None:
        stages.append(("".join(cur).strip(), op))
        cur.clear()

    def end_pipeline() -> None:
        end_stage("")
        if any(text for text, _ in stages):
            pipelines.append(list(stages))
        stages.clear()

    while i < n:
        c = cmd[i]
        nxt = cmd[i + 1] if i + 1 < n else ""
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and nxt:
                cur.append(nxt)
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\" and nxt:
            cur.append(c + nxt)
            i += 1
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "|":
            if nxt == "|":
                end_pipeline()
                i += 1
            elif nxt == "&":
                end_stage("|&")
                i += 1
            else:
                end_stage("|")
        elif c == "&":
            if nxt == "&":
                end_pipeline()
                i += 1
            elif nxt == ">" or (cur and cur[-1].endswith(">")):
                cur.append(c)  # `&>file`, `2>&1`, `>&2`: a redirect, not a separator
            else:
                end_pipeline()
        elif c in ";\n":
            end_pipeline()
        else:
            cur.append(c)
        i += 1
    end_pipeline()
    return pipelines


def _argv(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _unwrap_stdbuf(argv: list[str]) -> tuple[list[str], bool]:
    """Drop a leading `stdbuf ...`; the flag says whether it made stdout line- or unbuffered."""
    if not argv or os.path.basename(argv[0]) != "stdbuf":
        return argv, False
    live = False
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        opt = argv[i]
        val = ""
        if opt in ("-o", "--output"):
            val = argv[i + 1] if i + 1 < len(argv) else ""
            i += 1
        elif opt.startswith("-o"):
            val = opt[2:]
        elif opt.startswith("--output="):
            val = opt.split("=", 1)[1]
        live = live or val in ("L", "0")
        i += 1
    return argv[i:], live


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def _git_common_dir(path: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute",
                            "--git-common-dir"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else ""


@functools.lru_cache(maxsize=64)
def same_repo(path: str, repo_root: str = str(REPO_ROOT)) -> bool:
    """True when `path` is this repo or one of its worktrees (the same git common dir)."""
    if "$" in path:
        return True  # an unexpanded variable names no dir we can read; count it rather than miss
    p = Path(path)
    if not p.is_dir():
        return False
    ours = _git_common_dir(Path(repo_root))
    if not ours:
        return os.path.realpath(p) == os.path.realpath(repo_root)
    return _git_common_dir(p) == ours


def _push_runs_suite(stage: str, earlier: list[list[tuple[str, str]]],
                     repo_root: Path) -> bool | None:
    """None when the stage is not a `git push`; otherwise whether it runs this repo's pre-push."""
    m = _GIT_PUSH_RE.match(stage)
    if not m:
        return None
    globals_, args = _argv(m.group(1)), _argv(m.group(2))
    for a in args:
        if a in ("--no-verify", "--dry-run", "--help", "-h") or re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", a):
            return False
    where: Path | None = None
    for pipeline in earlier:  # `cd <dir> && git push`: the push runs in that dir
        argv = _argv(pipeline[0][0]) if len(pipeline) == 1 else []
        if len(argv) == 2 and argv[0] == "cd":
            where = (where or repo_root) / _expand(argv[1])
    i = 0
    while i < len(globals_):
        opt = globals_[i]
        val = globals_[i + 1] if i + 1 < len(globals_) else ""
        if opt == "-C":
            where = (where or repo_root) / _expand(val)
            i += 1
        elif opt == "-c":
            if val.lower().startswith("core.hookspath="):
                return False
            i += 1
        i += 1
    return where is None or same_repo(os.path.normpath(str(where)), str(repo_root))


def _holder(downstream: list[str]) -> tuple[str, tuple[str, ...]]:
    """The first stage in `downstream` that holds the lines it is piped until it ends (or "", when
    every stage passes them on as they come), and the files a `tee` ahead of it writes live."""
    tees: list[str] = []
    for stage in downstream:
        argv, line_buffered = _unwrap_stdbuf(_argv(stage))
        prog = os.path.basename(argv[0]) if argv else ""
        if prog in _LIVE:
            if prog == "tee":
                tees += [a for a in argv[1:] if not a.startswith("-")]
            continue
        if prog in _HOLD_BY_DESIGN and not (prog in ("tail", "less", "more") and _follows(argv)):
            return stage, tuple(tees)
        if line_buffered:
            continue
        if prog in ("grep", "egrep", "fgrep") and "--line-buffered" in argv:
            continue
        if prog == "sed" and any(a in ("-u", "--unbuffered") for a in argv):
            continue
        if prog in ("bash", "sh") or (prog.startswith("python") and "-u" in argv):
            continue
        return stage, tuple(tees)
    return "", tuple(tees)


def _follows(argv: list[str]) -> bool:
    return any(a in ("-f", "-F", "--follow") or a.startswith("--follow=")
               or re.fullmatch(r"-[A-Za-z0-9+]*[fF][A-Za-z0-9]*", a) for a in argv[1:])


def suite_run(cmd: str, repo_root: Path = REPO_ROOT) -> SuiteRun | None:
    """The suite from SUITE_RUNS (or a `git push` that runs this repo's pre-push) the command runs,
    with where its stage lines go, or None."""
    if not cmd:
        return None
    pipelines = _pipelines(cmd)
    for pi, pipeline in enumerate(pipelines):
        for si, (stage, op) in enumerate(pipeline):
            push = _push_runs_suite(stage, pipelines[:pi], repo_root)
            if push is False:
                return None
            if push:
                name = "git push"
            else:
                name = next((n for n, pat in SUITE_RUNS if pat.search(stage)), "")
                if not name:
                    continue
                if QUICK_FLAGS.search(stage):
                    return None
            redirect = redirect_target(stage)
            if redirect:
                return SuiteRun(name, redirect=redirect)
            # The stage lines go to stderr (`>&2` in `_check_run_stage`), so a plain `|` leaves
            # them in the task's output; only `|&` or a `2>&1` hands them to the next stage.
            if op == "|&" or (op == "|" and re.search(r"2>&1", stage)):
                held, tees = _holder([s for s, _ in pipeline[si + 1:]])
                return SuiteRun(name, held=held, tees=tees)
            return SuiteRun(name)
    return None


def suite_name(cmd: str) -> str | None:
    """Which suite the command runs, or None."""
    run = suite_run(cmd)
    return run.name if run else None


def redirect_target(cmd: str) -> str:
    """The file the command sends both its streams to, or "" when it sends them nowhere special."""
    if not _BOTH_STREAMS_RE.search(cmd):
        return ""
    for target in _REDIRECT_RE.findall(cmd):
        target = target.strip("'\"")
        if target and target != "/dev/null" and not re.search(r"[$`]", target):
            return target
    return ""


def watch_targets(task: scan.RunningTask, run: SuiteRun | None = None) -> list[str]:
    """Spellings of the file the suite's stage lines reach while it runs, which a Monitor command
    may name: the command's own redirect target; else the files a `tee` writes, and, unless a pipe
    stage holds the lines to the end, the harness's output file and its `<task id>.output`."""
    run = run or suite_run(task.command) or SuiteRun("")
    if run.redirect:
        return [run.redirect]
    targets = list(run.tees)
    if not run.held:
        if task.output:
            targets.append(task.output)
        targets.append(task.task_id + ".output")
    return targets


def _grep_passes(argv: list[str], line: str) -> bool:
    """Whether `grep <argv[1:]>` reading a pipe passes `line` on at once: it matches (or, with
    `-v`, does not), and grep is line-buffered and prints lines (no `-c`, `-q`, `-l`, `-L`)."""
    mode = {"egrep": "E", "fgrep": "F"}.get(os.path.basename(argv[0]), "G")
    flags: set[str] = set()
    patterns: list[str] = []
    positional: list[str] = []
    takes_arg = set("efmABCdD")
    long_map = {"--extended-regexp": "E", "--fixed-strings": "F", "--basic-regexp": "G",
                "--perl-regexp": "P", "--ignore-case": "i", "--invert-match": "v",
                "--word-regexp": "w", "--line-regexp": "x", "--count": "c", "--quiet": "q",
                "--silent": "q", "--files-with-matches": "l", "--files-without-match": "L"}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--":
            positional += argv[i + 1:]
            break
        if a.startswith("--"):
            key, _, val = a.partition("=")
            if key == "--regexp":
                patterns.append(val if _ else (argv[i + 1] if i + 1 < len(argv) else ""))
                i += 0 if _ else 1
            elif key == "--file":
                return True  # patterns from a file this check cannot read; do not judge them
            elif key == "--line-buffered":
                flags.add("line-buffered")
            elif key in long_map:
                flags.add(long_map[key])
            elif not _ and key in ("--max-count", "--after-context", "--before-context",
                                   "--context", "--label", "--devices", "--directories",
                                   "--binary-files", "--include", "--exclude", "--exclude-dir"):
                i += 1
        elif a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in takes_arg:
                    val = a[j + 1:] or (argv[i + 1] if i + 1 < len(argv) else "")
                    if not a[j + 1:]:
                        i += 1
                    if ch == "e":
                        patterns.append(val)
                    elif ch == "f":
                        return True
                    break
                flags.add(ch)
                j += 1
        else:
            positional.append(a)
        i += 1
    if not patterns:
        if not positional:
            return False
        patterns.append(positional.pop(0))
    if positional and positional != ["-"]:
        return False  # it reads those files, not the pipe
    if "line-buffered" not in flags or flags & set("cqlL"):
        return False
    mode = next((m for m in "PEFG" if m in flags), mode)
    matched = any(_pattern_matches(p, line, mode, flags)
                  for pat in patterns for p in pat.split("\n"))
    return matched != ("v" in flags)


def _bre_to_python(pat: str) -> str:
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "\\" and i + 1 < len(pat):
            nxt = pat[i + 1]
            out.append({"<": r"\b", ">": r"\b"}.get(nxt, nxt if nxt in "|(){}+?" else c + nxt))
            i += 2
            continue
        out.append("\\" + c if c in "|(){}+?" else c)
        i += 1
    return "".join(out)


def _pattern_matches(pat: str, line: str, mode: str, flags: set[str]) -> bool:
    if mode == "F":
        body = re.escape(pat)
    elif mode == "G":
        body = _bre_to_python(pat)
    else:
        body = pat
    if "x" in flags:
        body = "^(?:%s)$" % body
    elif "w" in flags:
        body = r"(?<!\w)(?:%s)(?!\w)" % body
    try:
        return re.search(body, line, re.I if "i" in flags else 0) is not None
    except re.error:
        return True  # a POSIX class or other spelling Python does not read; do not judge it


def _stage_passes_red(stage: str, monitor_cmd: str) -> bool:
    """Whether one filter stage of a Monitor pipeline passes a red stage line on as it arrives."""
    argv, line_buffered = _unwrap_stdbuf(_argv(stage))
    prog = os.path.basename(argv[0]) if argv else ""
    if prog in ("grep", "egrep", "fgrep"):
        if line_buffered and "--line-buffered" not in argv:
            argv = argv + ["--line-buffered"]
        return _grep_passes(argv, RED_SAMPLE)
    if "monitor-throttle.py" in stage or prog == "cat":
        return True
    if prog in _HOLD_BY_DESIGN or (prog in _STDIO_BUFFERED and not line_buffered
                                   and not (prog == "sed" and ("-u" in argv or "--unbuffered" in argv))):
        return False
    # awk, sed -u, a python filter, a `while read` loop: their logic is not read here, so the old
    # test stands for them: the Monitor names the verdict word.
    return "RED" in monitor_cmd


def watches(monitor_cmd: str, task: scan.RunningTask, run: SuiteRun | None = None) -> bool:
    """True when a pipeline of the Monitor command starts by following a file the suite's stage
    lines reach while it runs, and every later stage of it passes a red stage line on at once."""
    targets = [t for t in watch_targets(task, run) if t]
    for pipeline in _pipelines(monitor_cmd or ""):
        if not any(t in pipeline[0][0] for t in targets):
            continue
        if all(_stage_passes_red(stage, monitor_cmd) for stage, _ in pipeline[1:]):
            return True
    return False


def unwatched_suites(events: list[dict]) -> list[Unwatched]:
    running = scan.running_background_tasks(events)
    monitors = [t for t in running if t.kind == "monitor"]
    out = []
    for task in running:
        if task.kind != "bash":
            continue
        run = suite_run(task.command)
        if not run:
            continue
        if any(watches(m.command, task, run) for m in monitors):
            continue
        live = run.redirect or (run.tees[0] if run.tees else "") or ("" if run.held else task.output)
        out.append(Unwatched(task.task_id, run.name, task.command, live,
                             run.held if not (run.redirect or run.tees) else ""))
    return out


def monitor_command(output: str, pid: int | None = None, repo_root: Path = REPO_ROOT) -> str:
    """The Monitor command that wakes the agent on a red stage of the suite writing `output`."""
    follow = "tail -n +1 %s-F %s" % ("--pid=%d " % pid if pid else "", shlex.quote(output))
    throttle = shlex.quote(str(repo_root / "scripts" / "monitor-throttle.py"))
    return "%s | grep --line-buffered -E %s | python3 %s 10" % (
        follow, shlex.quote(MONITOR_FILTER), throttle)


def describe(u: Unwatched, repo_root: Path = REPO_ROOT) -> str:
    cmd = " ".join(u.command.split())
    if len(cmd) > 90:
        cmd = cmd[:87] + "..."
    if u.held:
        return ("%s (%s: %s) => its stage lines are piped through `%s`, which holds them until the "
                "task ends, so no Monitor can wake on a red stage: TaskStop it and run it again "
                "without that pipe" % (u.task_id, u.name, cmd, u.held))
    output = u.output or "<the task's output file>"
    return "%s (%s: %s) => Monitor with timeout_ms 1800000: %s" % (
        u.task_id, u.name, cmd, monitor_command(output, repo_root=repo_root))


def signal_line(events: list[dict], repo_root: Path = REPO_ROOT) -> str:
    found = unwatched_suites(events)
    if not found:
        return ""
    return ("UNWATCHEDRED:" + "; ".join(describe(u, repo_root) for u in found))[:1800]


# --- selftest ---------------------------------------------------------------------------------

def _ev(role: str, *blocks) -> dict:
    return {"type": role, "message": {"role": role, "content": list(blocks)}}


def _use(tid: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _res(tid: str, text: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tid, "content": text}
    if is_error:
        block["is_error"] = True
    return block


OUT = "/tmp/claude-1000/p/s/tasks/bpush1.output"
PUSH = "git -C %s push -u origin chore/no-tracker-refs" % shlex.quote(str(REPO_ROOT))


def _push_events(extra: list[dict] | None = None, push: str = PUSH) -> list[dict]:
    evs = [
        _ev("user", {"type": "text", "text": "authorize 6abca54e"}),
        _ev("assistant", _use("tu_push", "Bash", command=push, run_in_background=True)),
        _ev("user", _res("tu_push", "Command running in background with ID: bpush1. Output is "
                         "being written to: %s. You will be notified when it completes." % OUT)),
    ]
    return evs + (extra or [])


def _monitor(tid: str, task: str, command: str) -> list[dict]:
    return [
        _ev("assistant", _use(tid, "Monitor", command=command, description="push reds",
                              timeout_ms=1800000)),
        _ev("user", _res(tid, "Monitor started (task %s, expires in 30m unless the source ends "
                         "first)." % task)),
    ]


def _notify(task: str, status: str = "completed", body: str = "") -> dict:
    return _ev("user", {"type": "text", "text": (
        "<task-notification><task-id>%s</task-id><status>%s</status>%s</task-notification>"
        % (task, status, body))})


def selftest() -> int:
    failures = []
    count = [0]

    def check(name, got, want):
        count[0] += 1
        if got != want:
            failures.append("%s: got %r want %r" % (name, got, want))

    good = monitor_command(OUT)
    ids = lambda evs: [u.task_id for u in unwatched_suites(evs)]  # noqa: E731

    check("a background push with no Monitor is reported", ids(_push_events()), ["bpush1"])
    check("the report names the output file the Monitor must follow",
          OUT in signal_line(_push_events()), True)
    check("a Monitor on the output that passes RED clears it",
          ids(_push_events(_monitor("tu_m", "bm1", good))), [])
    check("a Monitor naming only <task id>.output also counts",
          ids(_push_events(_monitor("tu_m", "bm1", "tail -n +1 -F x/bpush1.output | grep "
                                    "--line-buffered RED | python3 scripts/monitor-throttle.py 10"))),
          [])
    check("a Monitor on the output whose filter drops RED does not count",
          ids(_push_events(_monitor("tu_m", "bm1", "tail -F %s | grep --line-buffered done | "
                                    "python3 scripts/monitor-throttle.py 10" % OUT))), ["bpush1"])
    check("a Monitor on some other file does not count",
          ids(_push_events(_monitor("tu_m", "bm1", "tail -F /tmp/other.log | grep RED | "
                                    "python3 scripts/monitor-throttle.py 10"))), ["bpush1"])
    check("a Monitor that expired no longer watches",
          ids(_push_events(_monitor("tu_m", "bm1", good) + [_notify(
              "bm1", "running", "<event>[Monitor expired after 30m with 0 events delivered.]</event>")])),
          ["bpush1"])
    check("a Monitor that was stopped no longer watches",
          ids(_push_events(_monitor("tu_m", "bm1", good) + [
              _ev("assistant", _use("tu_s", "TaskStop", task_id="bm1")),
              _ev("user", _res("tu_s", "stopped"))])), ["bpush1"])
    check("a push that finished needs no Monitor", ids(_push_events([_notify("bpush1")])), [])
    check("a push that was stopped needs no Monitor",
          ids(_push_events([_ev("assistant", _use("tu_s", "TaskStop", task_id="bpush1")),
                            _ev("user", _res("tu_s", "stopped"))])), [])
    check("a foreground push that returned is not a task",
          ids([_ev("assistant", _use("tu_p", "Bash", command=PUSH)),
               _ev("user", _res("tu_p", "Everything up-to-date"))]), [])
    check("a push the hook refused is not a task",
          ids([_ev("assistant", _use("tu_p", "Bash", command=PUSH, run_in_background=True)),
               _ev("user", _res("tu_p", "PreToolUse:Bash hook error: refused", True))]), [])

    check("suite: git push", suite_name("git push origin feat/x"), "git push")
    check("suite: git -C push", suite_name(PUSH), "git push")
    check("suite: dry-run push is not one", suite_name("git push --dry-run origin x"), None)
    check("suite: pre-push by hand", suite_name("bash scripts/hooks/pre-push origin url"), "pre-push")
    check("suite: full check.sh", suite_name("bash scripts/check.sh"), "check.sh")
    check("suite: one stage of check.sh is not one", suite_name("bash scripts/check.sh --stage lint"),
          None)
    check("suite: a grep naming check.sh is not one", suite_name("grep -n stage scripts/check.sh"),
          None)
    check("suite: kitty-tail-push is not one",
          suite_name("python3 scripts/kitty-tail-push.py --log %s" % OUT), None)

    # Which pushes run this repo's pre-push.
    main_top = Path(_git_common_dir(REPO_ROOT)).parent
    with tempfile.TemporaryDirectory() as other:
        subprocess.run(["git", "init", "-q", other], check=True, timeout=10)
        q = shlex.quote(other)
        check("push: -C another repo runs its hook, not this suite",
              suite_name("git -C %s push origin main" % q), None)
        check("push: cd into another repo first, the same",
              suite_name("cd %s && git push origin main" % q), None)
        check("push: a background push in another repo is not reported",
              ids(_push_events(push="git -C %s push origin main" % q)), [])
    check("push: -C this repo's main checkout counts", suite_name(
        "git -C %s push origin x" % shlex.quote(str(main_top))), "git push")
    check("push: cd into this repo counts", suite_name(
        "cd %s && git push origin x" % shlex.quote(str(REPO_ROOT))), "git push")
    check("push: -C a dir that does not exist is not one",
          suite_name("git -C /nonexistent/er-red-stage push origin x"), None)
    check("push: no -C counts", suite_name("git push -u origin x"), "git push")
    check("push: --no-verify runs no hook", suite_name("git push --no-verify origin x"), None)
    check("push: --no-verify after -C this repo, the same",
          suite_name(PUSH + " --no-verify"), None)
    check("push: -n is --dry-run", suite_name("git push -n origin x"), None)
    check("push: -n bundled with -u", suite_name("git push -un origin x"), None)
    check("push: a hooksPath override runs no hook",
          suite_name("git -c core.hooksPath=/dev/null push origin x"), None)
    check("push: a `head -n` after the pipe is not push's -n",
          suite_name("git push origin x 2>&1 | head -n 50"), "git push")
    check("push: under timeout counts", suite_name("timeout 3600 git push origin x"), "git push")
    check("push: echoed is not one", suite_name("echo git push origin x"), None)

    # Where the stage lines go once the push is piped.
    held = _push_events(_monitor("tu_m", "bm1", good), push=PUSH + " 2>&1 | tail -20")
    check("2>&1 | tail -20 holds the stage lines: a Monitor on the output does not clear it",
          ids(held), ["bpush1"])
    check("the held report names the holding stage and asks for a TaskStop",
          all(s in signal_line(held) for s in ("`tail -20`", "TaskStop")), True)
    check("|& tail -20 holds them too",
          ids(_push_events(_monitor("tu_m", "bm1", good), push=PUSH + " |& tail -20")), ["bpush1"])
    check("| tail -20 without 2>&1 leaves stderr's stage lines in the output",
          ids(_push_events(_monitor("tu_m", "bm1", good), push=PUSH + " | tail -20")), [])
    check("2>&1 | grep --line-buffered -v passes them on",
          ids(_push_events(_monitor("tu_m", "bm1", good),
                           push=PUSH + " 2>&1 | grep --line-buffered -v Compiling")), [])
    check("2>&1 | grep -v without --line-buffered holds them",
          ids(_push_events(_monitor("tu_m", "bm1", good),
                           push=PUSH + " 2>&1 | grep -v Compiling")), ["bpush1"])
    teed = PUSH + " 2>&1 | tee /tmp/p.log | tail -20"
    check("a tee ahead of tail is where the lines go live",
          [u.output for u in unwatched_suites(_push_events(push=teed))], ["/tmp/p.log"])
    check("a Monitor on the tee file clears it",
          ids(_push_events(_monitor("tu_m", "bm1", monitor_command("/tmp/p.log")), push=teed)), [])
    check("a Monitor on the held output does not",
          ids(_push_events(_monitor("tu_m", "bm1", good), push=teed)), ["bpush1"])

    # Which Monitor filters pass a red stage line as it arrives.
    def mon(cmd):
        return ids(_push_events(_monitor("tu_m", "bm1", cmd)))
    thr = " | python3 scripts/monitor-throttle.py 10"
    check("filter: grep -v RED drops it",
          mon("tail -F %s | grep --line-buffered -v RED%s" % (OUT, thr)), ["bpush1"])
    check("filter: grep RED without --line-buffered holds it",
          mon("tail -F %s | grep RED%s" % (OUT, thr)), ["bpush1"])
    check("filter: grep -c RED prints only a count",
          mon("tail -F %s | grep --line-buffered -c RED%s" % (OUT, thr)), ["bpush1"])
    check("filter: -E without RED in the alternatives drops it",
          mon("tail -F %s | grep --line-buffered -E 'FAIL|error'%s" % (OUT, thr)), ["bpush1"])
    check("filter: BRE 'RED|FAIL' is a literal bar and drops it",
          mon("tail -F %s | grep --line-buffered 'RED|FAIL'%s" % (OUT, thr)), ["bpush1"])
    check("filter: BRE 'FAIL\\|RED' passes it",
          mon("tail -F %s | grep --line-buffered 'FAIL\\|RED'%s" % (OUT, thr)), [])
    check("filter: -i red passes it", mon("tail -F %s | grep --line-buffered -i red%s" % (OUT, thr)), [])
    check("filter: -v green passes it",
          mon("tail -F %s | grep --line-buffered -v green%s" % (OUT, thr)), [])
    check("filter: stdbuf -oL grep RED passes it",
          mon("tail -F %s | stdbuf -oL grep RED%s" % (OUT, thr)), [])
    check("filter: monitor-throttle.py's documented order (-E --line-buffered) passes it",
          mon('tail -f %s | grep -E --line-buffered "RED"%s' % (OUT, thr)), [])
    check("filter: a tail -5 stage holds it",
          mon("tail -F %s | grep --line-buffered RED | tail -5%s" % (OUT, thr)), ["bpush1"])
    check("filter: RED named only in another pipeline does not count",
          mon("echo RED; tail -F %s | grep --line-buffered done%s" % (OUT, thr)), ["bpush1"])
    spaced = "/tmp/a log.txt"
    check("filter: kitty-tail-push's --pid command on a quoted path passes it",
          watches(monitor_command(spaced, 7), scan.RunningTask("bsp1", "bash", PUSH, 0, spaced)),
          True)
    check("filter: the halt's own command passes it", watches(good, scan.RunningTask(
        "bpush1", "bash", PUSH, 0, OUT)), True)

    check("redirect: both streams to a file",
          redirect_target("bash scripts/check.sh > /tmp/c.log 2>&1"), "/tmp/c.log")
    check("redirect: stdout only is not where the stage lines go",
          redirect_target("bash scripts/check.sh > /tmp/c.log"), "")
    redirected = [
        _ev("assistant", _use("tu_c", "Bash", command="bash scripts/check.sh &> /tmp/c.log",
                              run_in_background=True)),
        _ev("user", _res("tu_c", "Command running in background with ID: bchk1. Output is being "
                         "written to: /tmp/t/bchk1.output.")),
    ]
    check("a redirected suite is reported against its redirect target",
          [u.output for u in unwatched_suites(redirected)], ["/tmp/c.log"])
    check("a Monitor on the redirect target clears it",
          ids(redirected + _monitor("tu_m", "bm2", monitor_command("/tmp/c.log"))), [])
    check("a Monitor on the harness output of a redirected suite does not (it stays empty)",
          ids(redirected + _monitor("tu_m", "bm2", monitor_command("/tmp/t/bchk1.output"))),
          ["bchk1"])

    check("monitor command, no pid", good,
          "tail -n +1 -F %s | grep --line-buffered -E '%s' | python3 %s 10"
          % (OUT, MONITOR_FILTER, REPO_ROOT / "scripts" / "monitor-throttle.py"))
    check("monitor command, with pid", monitor_command(OUT, 4242).startswith(
        "tail -n +1 --pid=4242 -F %s | " % OUT), True)
    # The command the report names must pass the Monitor guard's final-stage check; this is the
    # regex `monitor_rate_limit.rego` applies (throttled_tail_pattern), in Python spelling.
    throttled = re.compile(r"\|[ \t]*(uv run [^|]*)?python3?[ \t]+[^| \t]*scripts/monitor-throttle"
                           r"\.py([ \t]+[1-9][0-9]+(\.[0-9]+)?)?[ \t]*$")
    check("monitor command ends in the throttle the Monitor guard requires",
          bool(throttled.search(good)), True)
    check("the filter passes a red stage line", bool(re.search(
        MONITOR_FILTER, ">>> stage policy         RED (exit 1)   1702s   /run/x/policy.log")), True)
    check("the filter passes the harness's exit line", bool(re.search(
        MONITOR_FILTER, "[exited with code 1]")), True)
    check("the filter drops a green stage line", bool(re.search(
        MONITOR_FILTER, ">>> stage docs           green             6s   /run/x/docs.log")), False)

    for f in failures:
        print("FAIL " + f, file=sys.stderr)
    if failures:
        print("cupcake_red_stage_watch selftest: %d failure(s)" % len(failures), file=sys.stderr)
        return 1
    print("cupcake_red_stage_watch selftest: OK (%d checks)" % count[0])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--transcript", help="a session transcript to judge instead of the hook event's")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    path = args.transcript or scan.hook_transcript()
    if not path or not os.path.exists(path):
        return 0
    line = signal_line(scan.load_events(path))
    if line:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
