#!/usr/bin/env python3
"""Record and judge Frida evidence, so a Rust edit has to be preceded by a measurement.

# Why this exists

User directive 2026-09-16, after the agent tore the game down twice and ran two build/relaunch
cycles to land a change whose mechanism was never measured: "why would you build, if you haven't
proven you can do it with Frida yet?" AGENTS.md already said it -- "The order is Frida, then Frida,
then Frida, and only then a DLL ... Build a DLL when the mechanism is already known and the code is
the product, never to find something out" -- and the agent did it anyway. A note that is ignored is
not a rule, so this is the executable half.

`.cupcake/policies/claude/no_rust_edit_without_frida_proof.rego` refuses a Write/Edit under
`crates/**/*.rs` unless this says `PROVEN`, and the `--check` verdict is the only thing that opens
that door.

# What counts as evidence

A record is appended by the Frida entry points themselves when a session ends, carrying what that
session actually did: the agent file, the pid it attached to, how many `send()` messages came back,
and how long it ran. A session that attached and observed nothing reports `messages=0` and does not
count -- the point is going and looking, and a watcher that saw nothing did not look at anything.

The log lives under `XDG_STATE_HOME` (default `~/.local/state/er-mods-rs/`), not in the repo, so the
gate's evidence is not a file the gated Write tool can create.

# How far that goes, honestly

This heading used to read "and why it cannot be written by hand", which was false, and a false claim
here is worse than the hole it papers over -- the next agent believes it. Measured against the live
engine 2026-09-16, with no evidence present, every one of these was allowed:

    python3 scripts/er-frida-evidence.py --record --agent x --pid 1 --messages 12 --seconds 9
    echo '{"at": 9999999999, "messages": 12}' >> ~/.local/state/er-mods-rs/frida-evidence.jsonl
    an Edit to this file, to `.cupcake/signals/frida_evidence.sh`, or to the policy itself
    a Bash `cat > crates/<crate>/src/lib.rs <<EOF`, which is not an Edit and is not routed

The `XDG_STATE_HOME` placement closes exactly one route, the repo-relative Write, and nothing else.
Everything above is one command. So this is a gate against an agent that reaches for a build when it
should reach for a hook -- the failure it was written for -- and not against one that has decided to
get around it. Treat the two differently: the first is what the refusal text talks to, and the
second is a choice no amount of rego reaches.

# What makes a record stale

Evidence is spent by a commit. A record counts only if it is newer than `HEAD`'s commit time, so one
measurement licenses the edits of one change and the next change needs its own. Without that the
first Frida run of a session would license every edit after it forever, which is the same "I looked
once" excuse in a machine-readable costume.

    python3 scripts/er-frida-evidence.py --record --agent scripts/frida/x.js --pid 388 --messages 12
    python3 scripts/er-frida-evidence.py --check
    python3 scripts/er-frida-evidence.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def state_dir() -> pathlib.Path:
    """Where the evidence log lives: user state, deliberately outside the repo."""
    root = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return pathlib.Path(root) / "er-mods-rs"


def log_path() -> pathlib.Path:
    override = os.environ.get("ER_FRIDA_EVIDENCE_LOG")
    if override:
        return pathlib.Path(override)
    return state_dir() / "frida-evidence.jsonl"


def head_commit_time(repo: pathlib.Path) -> int | None:
    """When the newest committed Rust change landed, or `None` outside a repo.

    A record older than this is spent: the change it measured has been committed, and the next
    change needs its own measurement.

    "Rust change" and not "commit". The gate this feeds exists to stop a `.rs` file under
    `crates/` being written without somebody going and looking first, so the thing that consumes
    a measurement is a committed Rust change -- and only that. Keying on plain `HEAD` made every
    commit spend it, including ones that cannot possibly have used it: on 2026-09-17 a
    `scripts/`-only commit (`a8c11bb1`, the launch-gate cache) spent a live measurement taken
    minutes earlier, and the next Rust edit was refused with `spent-by-commit` for a reason that
    had nothing to do with Rust. That is a false refusal, not a strict one, and a gate that
    refuses for the wrong reason teaches the next agent to look for a way around it.

    The pathspec is the same shape the policy uses to decide what it guards: `*.rs` under
    `crates/`. A commit that touches both is still a Rust commit and still spends.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "-1",
                "--format=%ct",
                "--",
                "crates/**/*.rs",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


def record(agent: str, pid: int, messages: int, seconds: float) -> int:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": int(time.time()),
        "agent": agent,
        "pid": pid,
        "messages": messages,
        "seconds": round(seconds, 2),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    print(f"frida-evidence: recorded {row}")
    return 0


def newest_record(path: pathlib.Path) -> dict | None:
    """The last well-formed record, or `None`.

    Read from the end rather than parsed whole: this runs in front of every edit and the log grows
    without bound. A malformed trailing line is skipped rather than fatal -- a broken log must not
    take the policy down with it.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "at" in row:
            return row
    return None


def check(repo: pathlib.Path) -> int:
    """Print one verdict line. `PROVEN` is the only one the policy opens on."""
    row = newest_record(log_path())
    if row is None:
        print("UNPROVEN no-frida-evidence nothing has attached to the game and reported back")
        return 1

    messages = int(row.get("messages", 0) or 0)
    if messages <= 0:
        print(
            f"UNPROVEN silent-session the last watch on {row.get('agent', '?')} "
            f"reported {messages} messages, so it observed nothing"
        )
        return 1

    head = head_commit_time(repo)
    if head is not None and int(row.get("at", 0)) <= head:
        print(
            f"UNPROVEN spent-by-commit the last measurement predates HEAD, so it belongs to "
            f"a change that is already committed"
        )
        return 1

    print(
        f"PROVEN agent={row.get('agent', '?')} pid={row.get('pid', '?')} "
        f"messages={messages} seconds={row.get('seconds', '?')}"
    )
    return 0


def selftest() -> int:
    import tempfile

    failures = 0

    def ok(label: str, condition: bool) -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}")
        failures += 0 if condition else 1

    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "frida-evidence.jsonl"
        os.environ["ER_FRIDA_EVIDENCE_LOG"] = str(log)
        empty = pathlib.Path(tmp) / "norepo"
        empty.mkdir()

        ok("an absent log is unproven", check(empty) == 1)

        record("scripts/frida/x.js", 388, 0, 3.0)
        ok("a silent session is unproven", check(empty) == 1)

        record("scripts/frida/x.js", 388, 12, 4.0)
        ok("a session that observed something is proven", check(empty) == 0)

        ok(
            "a malformed trailing line does not take the log down",
            (log.open("a", encoding="utf-8").write("{not json\n") or True)
            and check(empty) == 0,
        )

        ok("the log lives outside the repo by default", REPO_ROOT not in state_dir().parents)
        os.environ.pop("ER_FRIDA_EVIDENCE_LOG", None)

    print("selftest: PASS" if not failures else f"selftest: {failures} check(s) failed")
    return 0 if not failures else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="append a record and exit")
    parser.add_argument("--agent", default="", help="the agent file that ran")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--messages", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--check", action="store_true", help="print PROVEN or UNPROVEN")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.record:
        return record(args.agent, args.pid, args.messages, args.seconds)
    if args.check:
        return check(REPO_ROOT)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
