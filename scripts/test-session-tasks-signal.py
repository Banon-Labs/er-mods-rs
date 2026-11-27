#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `session_tasks` (.cupcake/signals/session_tasks.sh).

The signal reads a UserPromptSubmit hook event on stdin and prints, in one paragraph, the shells and
agents of that event's session running now, as scripts/cupcake_session_tasks.py measures them: each
shell's task id, owner, what it is doing and how long it has run, then each agent still working.
session_tasks_context.rego injects it. Nothing is printed when nothing runs or nothing can be
measured.

The shells here are real processes, each started the way the harness starts a Bash call (stdout on
`<tasks dir>/<task id>.output`) under this test's own pid, which the signal is pinned to as
`CLAUDE_PID`; so a Claude Code session this test runs under is never read. The transcripts are
fixtures under a temporary directory. User words 2026-11-11, verbatim: "I refuse to tell you, and
encourge you to find a way to self discover what shells you kick off. I didn't run any shells".
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "session_tasks.sh"

CASES_RUN = 0


def has_child(pid: int) -> bool:
    try:
        return bool(Path(f"/proc/{pid}/task/{pid}/children").read_text().split())
    except OSError:
        return False


def line(**ev) -> str:
    return json.dumps(ev) + "\n"


def bash_launch(tool_id: str, task_id: str, description: str, command: str = "sleep 30") -> str:
    return (line(type="assistant", message={"content": [
                {"type": "tool_use", "id": tool_id, "name": "Bash",
                 "input": {"command": command, "description": description, "run_in_background": True}}]})
            + line(type="user", message={"content": [
                {"type": "tool_result", "tool_use_id": tool_id,
                 "content": f"Command running in background with ID: {task_id}. Output is being written to: x"}]}))


def agent_launch(tool_id: str, agent_id: str, description: str) -> str:
    return (line(type="assistant", message={"content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent", "input": {"description": description}}]})
            + line(type="user", message={"content": [
                {"type": "tool_result", "tool_use_id": tool_id,
                 "content": f"Async agent launched successfully. agentId: {agent_id} (internal ID)"}]}))


def tasks_dir(tmp: Path) -> Path:
    return tmp / "claude-1000" / "-proj" / "sess" / "tasks"


def run(tmp: Path, transcript: str, stdin: str | None = None, live: tuple[str, ...] = (),
        scripts: dict[str, str] | None = None) -> tuple[str, float]:
    """The signal's stdout for a UserPromptSubmit event naming `transcript`, and its wall time, with
    `live` task ids running as real processes meanwhile, each a `bash -c` of its entry in `scripts`
    (default `sleep 30 & wait`) with stdout and stderr on its output file."""
    tasks = tasks_dir(tmp)
    tasks.mkdir(parents=True, exist_ok=True)
    procs = []
    try:
        for tid in live:
            with (tasks / f"{tid}.output").open("w") as out:
                script = (scripts or {}).get(tid, "sleep 30 & wait")
                procs.append(subprocess.Popen(["bash", "-c", script], stdout=out, stderr=out))
        # Every script forks a `sleep`; once each shell has its child, its descriptors are in place.
        # Each look reads /proc, which paces the loop.
        deadline = time.monotonic() + 5
        while not all(has_child(p.pid) for p in procs) and time.monotonic() < deadline:
            pass
        event = stdin if stdin is not None else json.dumps({
            "session_id": Path(transcript).stem, "transcript_path": transcript,
            "hook_event_name": "UserPromptSubmit", "prompt": "next"})
        started = time.monotonic()
        proc = subprocess.run(["bash", str(SIGNAL)], input=event, capture_output=True, text=True, timeout=25,
                              env={**os.environ, "CLAUDE_PID": str(os.getpid())})
        return proc.stdout.strip(), time.monotonic() - started
    finally:
        for p in procs:
            p.kill()
            p.wait()


def expect(name: str, got: str, predicate, describe: str) -> None:
    global CASES_RUN
    CASES_RUN += 1
    if not predicate(got):
        raise AssertionError(f"{name}: {describe} (got {got!r})")
    print(f"ok  {name}: {got[:90]!r}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        proj = tmp / "projects" / "-proj"
        sub = proj / "sess" / "subagents"
        sub.mkdir(parents=True)
        main_t = proj / "sess.jsonl"
        main_t.write_text(
            bash_launch("tb", "bboard1", "Regenerate the weapon boards")
            + bash_launch("td", "bdone2", "A run that finished")
            + agent_launch("ta", "a5eed0011223344aa", "Speed up attack-set stage")
            + bash_launch("tg", "bgrp3", "Redirected selftests", "{ python3 x.py --selftest; } > log 2>&1")
            + bash_launch("tt", "btail4", "Tail the finished run", "tail -f bdone2.output"))
        (sub / "agent-a5eed0011223344aa.jsonl").write_text(
            bash_launch("ts", "bsub7", "", "python3 bench.py --jobs 2").replace(
                '"type": "assistant"', '"isSidechain": true, "type": "assistant"'))
        (sub / "agent-a5eed0011223344aa.meta.json").write_text(json.dumps({"description": "Speed up attack-set stage"}))
        quiet_t = proj / "quiet.jsonl"
        quiet_t.write_text(bash_launch("td", "bdone2", "A run that finished"))

        out, _ = run(tmp, str(main_t), live=("bboard1", "bsub7"))
        expect("live-report", out,
               lambda o: all(k in o for k in (
                   "Shells running:", "bboard1 (main session: Regenerate the weapon boards; up ",
                   "bsub7 (agent a5eed0011223344aa (Speed up attack-set stage): python3 bench.py --jobs 2; up ",
                   "Agents working: a5eed0011223344aa 'Speed up attack-set stage' (running)."))
               and "bdone2" not in o and "\n" not in o,
               "both live shells with owners and what they do, the working agent, one paragraph; the "
               "finished task is absent")
        out, _ = run(tmp, str(main_t), live=("bother9",))
        expect("unclaimed-shell-is-listed", out, lambda o: "bother9 (owner unknown" in o,
               "a live shell no transcript launched is still this process's, listed as owner unknown")
        # Task by9azikd7, 2026-11-11: a group redirect moves the shell's stdout and stderr to saved
        # descriptors (bash: 10 and 11) and points fd 1 at the log, so fd 1 alone missed it. A shell
        # that only reads another task's output (fd 3, O_RDONLY) does not make that task live.
        done_out = tasks_dir(tmp) / "bdone2.output"
        out, _ = run(tmp, str(main_t), live=("bgrp3", "btail4"), scripts={
            "bgrp3": f"{{ sleep 30; }} > {tmp}/redirect.log 2>&1; true",
            "btail4": f": >> {done_out}; exec 3< {done_out}; sleep 30 & wait"})
        expect("redirected-group-and-reader", out,
               lambda o: "bgrp3 (main session: Redirected selftests; up " in o
               and "btail4 (main session: Tail the finished run; up " in o and "bdone2" not in o,
               "the redirected group's shell is listed by its saved descriptors; the reader is listed as "
               "its own task, and the finished task it reads is not")
        out, _ = run(tmp, str(main_t))
        expect("agent-only", out,
               lambda o: o.startswith("Agents working: a5eed0011223344aa") and "Shells running" not in o,
               "no live shells: only the working agent")
        out, _ = run(tmp, str(quiet_t))
        expect("nothing-running-prints-nothing", out, lambda o: o == "",
               "a finished launch and no live process: nothing to inject")
        out, _ = run(tmp, str(quiet_t), stdin="not json")
        expect("garbage-event-prints-nothing", out, lambda o: o == "", "fail open on an unparseable event")
        out, _ = run(tmp, str(quiet_t), stdin="")
        expect("no-event-prints-nothing", out, lambda o: o == "", "fail open with no event")
        out, _ = run(tmp, str(main_t), live=("bboard1",),
                     stdin=json.dumps({"transcript_path": str(proj / "missing.jsonl"), "session_id": "missing"}))
        expect("unknown-session-prints-nothing", out, lambda o: o == "",
               "an event naming no transcript that exists is no session to report")

        # Cost. A session measured 2026-11-11 had 52 MB of transcript; cupcake drops a discovered
        # signal at 5 s and this one caps itself at 4 s. A 48 MB transcript of tool output with the
        # launch of a live shell at its middle has to be read well inside that.
        big = proj / "big.jsonl"
        filler = line(type="user", message={"content": [
            {"type": "tool_result", "tool_use_id": "tf", "content": "x" * 4000}]})
        with big.open("w") as fh:
            for _ in range(6000):
                fh.write(filler)
            fh.write(bash_launch("tb", "bbig1", "The run in the middle"))
            for _ in range(6000):
                fh.write(filler)
        out, took = run(tmp, str(big), live=("bbig1",))
        expect("large-transcript-in-time", f"{took:.2f}s {out}",
               lambda o: "bbig1 (main session: The run in the middle" in o and took < 2.5,
               f"a {big.stat().st_size >> 20} MB transcript must be reported in under 2.5 s")
    print(f"session_tasks signal tests passed ({CASES_RUN} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
