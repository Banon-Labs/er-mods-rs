#!/usr/bin/env python3
"""Prove the cupcake hook shim evaluates -- and still DENIES -- under every permission mode.

WHY THIS GATE EXISTS
--------------------
cupcake 0.5.2 deserializes `permission_mode` into a closed enum and exits 1 on anything outside
{default, plan, acceptEdits, bypassPermissions}. Claude Code shipped an `auto` mode, so on
2026-08-24 EVERY hook in this repo -- PreToolUse and PostToolUse included -- failed with

    Error: unknown variant `auto`, expected one of `default`, `plan`, ...

for a whole session, with every policy in .cupcake/policies silently not running. The suite was
green throughout, exactly like the 2026-08-22 episode recorded in check.sh.

An unrecognised permission mode must degrade to "evaluate anyway", never to "evaluate nothing".
The failure mode is invisible by construction -- a guard that never runs looks identical to a
guard that allowed you -- so it is asserted here rather than trusted.

WHAT IT ASSERTS, for a KNOWN mode, the `auto` mode that broke it, and an invented future one:
  * the shim exits 0 and emits parseable JSON,
  * a command the policies deny is STILL denied (the shim must not neuter a guard),
  * a benign command is still allowed (it must not deny everything either),
  * stderr stays quiet, because the default log level is `info` and floods ~4KB per event.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHIM = REPO / "scripts" / "cupcake-hook.sh"
# `default` is the control, `auto` is the mode that actually broke, and the third is a mode that
# does not exist -- the point is that the NEXT unknown mode must not repeat this.
MODES = ["default", "auto", "some-future-mode"]
DENIED_COMMAND = "git push origin main"
ALLOWED_COMMAND = "echo hello"
# stderr is not required to be empty (a real warning should still surface), but the `info` flood
# is ~4KB per event and must not come back.
MAX_STDERR_BYTES = 512


def run(mode: str, command: str) -> tuple[int, str, bytes]:
    payload = {
        "session_id": "shim-test",
        "transcript_path": "/tmp/does-not-exist.jsonl",
        "cwd": str(REPO),
        "hook_event_name": "PreToolUse",
        "permission_mode": mode,
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    proc = subprocess.run(
        ["bash", str(SHIM)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        text=False,
        timeout=25,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr


def verdict(stdout: str) -> str:
    try:
        decision = json.loads(stdout)
    except ValueError:
        return "unparseable"
    specific = decision.get("hookSpecificOutput") or {}
    return specific.get("permissionDecision") or decision.get("decision") or "allow"


def main() -> int:
    if not SHIM.exists():
        print(f"[test-cupcake-hook-shim] FAIL: missing {SHIM}")
        return 1
    failures: list[str] = []
    for mode in MODES:
        code, stdout, stderr = run(mode, DENIED_COMMAND)
        if code != 0:
            failures.append(f"{mode}: denied-command run exited {code}: {stderr[:200]!r}")
            continue
        got = verdict(stdout)
        if got != "deny":
            failures.append(
                f"{mode}: {DENIED_COMMAND!r} came back {got!r}, not 'deny' -- the guard did not run"
            )
        if len(stderr) > MAX_STDERR_BYTES:
            failures.append(
                f"{mode}: {len(stderr)}B of stderr (max {MAX_STDERR_BYTES}); is --log-level set?"
            )

        code, stdout, stderr = run(mode, ALLOWED_COMMAND)
        if code != 0:
            failures.append(f"{mode}: benign run exited {code}: {stderr[:200]!r}")
            continue
        got = verdict(stdout)
        if got == "deny":
            failures.append(f"{mode}: {ALLOWED_COMMAND!r} was denied; the shim denies everything")

    if failures:
        for failure in failures:
            print(f"[test-cupcake-hook-shim] FAIL: {failure}")
        return 1
    print(
        f"[test-cupcake-hook-shim] ok ({len(MODES)} permission modes: "
        f"{', '.join(MODES)}; deny still denies, allow still allows, stderr quiet)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
