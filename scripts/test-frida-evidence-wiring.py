#!/usr/bin/env python3
"""Prove `er-frida-watch.py` records what it observed, on every way a watch can end.

# Why this is not covered by the two selftests

`er-frida-watch.py --selftest` asserts that the strings `record_evidence(` and `seen["messages"]`
are present in its own source, and `er-frida-evidence.py --selftest` asserts that a hand-written
record reads back as `PROVEN`. Neither one runs the watch. Between them sits the join that actually
matters -- whether a real session's message count reaches the log without anybody typing it -- and
a string match cannot see it: moving the recorder into a branch that never runs would pass both.

So this drives `run()` end to end against a stubbed device, session and script. No Frida, no game,
no network. It ends the watch four ways -- a detach, a silent detach, a `SIGTERM`, an interrupt --
and reads each result back through the same `check()` verdict
`.cupcake/policies/claude/no_rust_edit_without_frida_proof.rego` gates on.

The last case is the one worth keeping: a recorder that cannot be loaded must not change the
watcher's exit code. A watch that worked has to report that it worked, whatever the gate then says.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import signal
import sys
import tempfile
import threading

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# How many yields to spend waiting for the watcher thread to reach a given point. A bound, not a
# wait: every loop below exits on an observed state change and this only stops a broken build from
# spinning forever.
SPIN_LIMIT = 200_000


def load(name: str, filename: str):
    """A hyphenated script as a module. No `import` statement can spell these names."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = load("watch_under_test", "er-frida-watch.py")
evidence = load("evidence_under_test", "er-frida-evidence.py")


class FakeProcess:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self.name = name


class FakeScript:
    def __init__(self) -> None:
        self.handlers: dict = {}
        self.loaded = False

    def on(self, event, callback) -> None:
        self.handlers[event] = callback

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False


class FakeSession:
    def __init__(self) -> None:
        self.scripts: list = []
        self.handlers: dict = {}

    def create_script(self, _source: str) -> FakeScript:
        script = FakeScript()
        self.scripts.append(script)
        return script

    def on(self, event, callback) -> None:
        self.handlers[event] = callback


class FakeDevice:
    """Enough of a Frida device for `run()`: one game process, no other watcher attached."""

    def __init__(self, session: FakeSession, pid: int) -> None:
        self.session = session
        self.pid = pid

    def enumerate_processes(self) -> list:
        return [FakeProcess(self.pid, "eldenring.exe")]

    def enumerate_sessions(self) -> list:
        return []

    def attach(self, _pid: int) -> FakeSession:
        return self.session


def spin_until(predicate) -> bool:
    """Yield the processor until `predicate` holds. Returns False if it never did."""
    for _ in range(SPIN_LIMIT):
        if predicate():
            return True
        os.sched_yield()
    return False


# How long a single fake watch may take before the harness calls it hung.
#
# Every ending here is driven by this process, so a watch that has not finished is not slow, it is
# stuck -- and `watch.run` parks on a `select` with nothing to wake it. Left unbounded that is not a
# failing test, it is a test that never returns: measured 2026-09-16, one of these held
# `/run/user/1000/er-mods-rs-check-sh.lock` for 4 hours 21 minutes at zero CPU, inside the
# `runtime-tools` stage of a `git push`, and every later push was refused for concurrency by a run
# that could never finish. A safety cap, never the synchronisation -- the endings below are.
WATCH_CAP_SECONDS = 20.0


def end_watch(session: "FakeSession") -> None:
    """Make `watch.run` return, whatever went wrong. Safe to call more than once."""
    handler = session.handlers.get("detached")
    if handler is not None:
        handler("process-terminated")


def drive(session: FakeSession, messages: int, ending: str, failures: list) -> None:
    """Send `messages` through the agent's own callback, then end the watch `ending`'s way."""
    # `session.handlers["detached"]` is registered after the script's message handler, so waiting
    # for it means both exist. Racing the watcher instead of waiting on it is how this kind of
    # harness ends up flaky and then ignored.
    if not spin_until(lambda: "detached" in session.handlers):
        failures.append("the watcher never registered a detach handler")
        end_watch(session)
        return
    on_message = session.scripts[-1].handlers["message"]
    for index in range(messages):
        on_message({"type": "send", "payload": {"hit": index}}, None)

    if ending == "detach":
        session.handlers["detached"]("process-terminated")
        return

    # Both signal endings need the watcher's own handler installed first, or the default action
    # kills this process and the test reports nothing at all.
    if not spin_until(lambda: signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None)):
        failures.append("the watcher never installed its terminate handler")
        end_watch(session)
        return
    if ending == "sigterm":
        os.kill(os.getpid(), signal.SIGTERM)
    elif ending == "interrupt":
        # Delivered to the main thread, where the watcher's select is parked.
        signal.raise_signal(signal.SIGINT)


def one_watch(tmp: pathlib.Path, agent: pathlib.Path, messages: int, ending: str) -> tuple:
    """Run one whole watch. Returns its exit code, the rows it wrote, and the gate's verdict."""
    log = tmp / f"evidence-{ending}-{messages}.jsonl"
    os.environ["ER_FRIDA_EVIDENCE_LOG"] = str(log)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    session = FakeSession()
    watch.device = lambda: FakeDevice(session, 388)
    failures: list = []
    driver = threading.Thread(target=drive, args=(session, messages, ending, failures), daemon=True)
    driver.start()

    # The watchdog. `watch.run` has to stay on the main thread -- two of the three endings are
    # signals, and the `select` they interrupt is parked there -- so the cap cannot be a join on it.
    finished = threading.Event()

    def watchdog() -> None:
        if finished.wait(WATCH_CAP_SECONDS):
            return
        failures.append(
            f"the {ending} watch did not finish inside {WATCH_CAP_SECONDS:.0f}s -- forcing a "
            "detach so this run ends instead of holding the check lock"
        )
        end_watch(session)

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        code = watch.run(agent, tmp / "hits.jsonl")
    finally:
        finished.set()
    driver.join(10)

    rows = []
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    # `tmp` is not a git repository, so `head_commit_time` returns None and the staleness rule is
    # out of the way. What is under test here is the message count reaching the verdict, not the
    # spent-by-commit clause, which `er-frida-evidence.py --selftest` covers.
    verdict = evidence.check(tmp)
    return code, rows, failures


def main() -> int:
    failed = 0

    def ok(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failed
        suffix = f" -- {detail}" if detail and not condition else ""
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}{suffix}")
        failed += 0 if condition else 1

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        agent = tmp / "probe.js"
        agent.write_text("// a stub agent; nothing loads it\n", encoding="utf-8")

        code, rows, problems = one_watch(tmp, agent, 12, "detach")
        ok("the driver reached the watcher", not problems, str(problems))
        ok("a detach exits zero", code == 0, f"exit={code}")
        ok("a detach writes exactly one record", len(rows) == 1, str(rows))
        ok("the record carries the count the session actually saw", rows[:1] and rows[0]["messages"] == 12, str(rows))
        ok("the record carries the agent that ran", rows[:1] and rows[0]["agent"] == str(agent), str(rows))
        ok("the record carries the pid it attached to", rows[:1] and rows[0]["pid"] == 388, str(rows))
        ok("the record carries how long it was attached", rows[:1] and rows[0]["seconds"] >= 0, str(rows))
        ok("the gate reads that session as proof", evidence.check(tmp) == 0)

        code, rows, problems = one_watch(tmp, agent, 0, "detach")
        ok("a session that received nothing still records", len(rows) == 1, str(rows))
        ok("and the gate refuses it", evidence.check(tmp) == 1)

        code, rows, problems = one_watch(tmp, agent, 7, "sigterm")
        ok("a terminate exits zero", code == 0, f"exit={code}")
        ok("a terminate records what it saw", rows[:1] and rows[0]["messages"] == 7, str(rows))
        ok("and the gate reads it as proof", evidence.check(tmp) == 0)

        code, rows, problems = one_watch(tmp, agent, 3, "interrupt")
        ok("an interrupt exits zero", code == 0, f"exit={code}")
        ok("an interrupt records what it saw", rows[:1] and rows[0]["messages"] == 3, str(rows))
        ok("and the gate reads it as proof", evidence.check(tmp) == 0)

        # A recorder that cannot be loaded is a broken gate, not a broken watch.
        real = watch.EVIDENCE_SCRIPT
        watch.EVIDENCE_SCRIPT = tmp / "not-here.py"
        try:
            code, rows, problems = one_watch(tmp, agent, 5, "detach")
        finally:
            watch.EVIDENCE_SCRIPT = real
        ok("a broken recorder leaves the watcher's exit code alone", code == 0, f"exit={code}")
        ok("and writes nothing, so the gate stays shut", not rows, str(rows))

        os.environ.pop("ER_FRIDA_EVIDENCE_LOG", None)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    print("PASS" if not failed else f"{failed} check(s) failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
