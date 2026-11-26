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
that has no still-running Monitor following its output file through a filter that passes `RED`.
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
import os
import re
import shlex
import sys
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
SUITE_RUNS = [
    ("git push", re.compile(r"\bgit\s+(?:-C\s+\S+\s+)*push\b")),
    ("pre-push", re.compile(_RUN + r"hooks/pre-push\b")),
    ("check.sh", re.compile(_RUN + r"(?<=/)check\.sh\b(?![^;&|]*--stage\b)")),
]
QUICK_FLAGS = re.compile(r"--(?:selftest|help|list-stages|dry-run)\b")

# What a watching Monitor's filter must pass: the stage verdict word itself.
WATCH_WORD_RE = re.compile(r"RED")

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


def suite_name(cmd: str) -> str | None:
    """Which suite from SUITE_RUNS the command runs, or None."""
    if not cmd:
        return None
    for name, pat in SUITE_RUNS:
        if pat.search(cmd):
            if name != "git push" and QUICK_FLAGS.search(cmd):
                return None
            if name == "git push" and re.search(r"--dry-run\b|(?:^|\s)-n\b", cmd):
                return None
            return name
    return None


def redirect_target(cmd: str) -> str:
    """The file the command sends both its streams to, or "" when it sends them nowhere special."""
    if not _BOTH_STREAMS_RE.search(cmd):
        return ""
    for target in _REDIRECT_RE.findall(cmd):
        target = target.strip("'\"")
        if target and target != "/dev/null" and not re.search(r"[$`]", target):
            return target
    return ""


def watch_targets(task: scan.RunningTask) -> list[str]:
    """Spellings of the suite's output a Monitor command may name: the harness's output file, its
    `<task id>.output` basename, and the command's own redirect target."""
    targets = []
    redirect = redirect_target(task.command)
    if redirect:
        targets.append(redirect)
    if task.output:
        targets.append(task.output)
    targets.append(task.task_id + ".output")
    return targets


def watches(monitor_cmd: str, task: scan.RunningTask) -> bool:
    """True when the Monitor command follows the suite's output and its filter passes `RED`."""
    if not WATCH_WORD_RE.search(monitor_cmd or ""):
        return False
    return any(t and t in monitor_cmd for t in watch_targets(task))


def unwatched_suites(events: list[dict]) -> list[Unwatched]:
    running = scan.running_background_tasks(events)
    monitors = [t for t in running if t.kind == "monitor"]
    out = []
    for task in running:
        if task.kind != "bash":
            continue
        name = suite_name(task.command)
        if not name:
            continue
        if any(watches(m.command, task) for m in monitors):
            continue
        out.append(Unwatched(task.task_id, name, task.command,
                             redirect_target(task.command) or task.output))
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
PUSH = "git -C /home/banon/projects/er-mods-rs push -u origin chore/no-tracker-refs"


def _push_events(extra: list[dict] | None = None) -> list[dict]:
    evs = [
        _ev("user", {"type": "text", "text": "authorize 6abca54e"}),
        _ev("assistant", _use("tu_push", "Bash", command=PUSH, run_in_background=True)),
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
