#!/usr/bin/env python3
"""scripts/cupcake-hook.sh when a policy file does not compile: the reply carries the unlock command.

Builds a throwaway git checkout holding a copy of this repo's `.cupcake` and the hook, breaks one
tracked policy and adds one untracked broken policy, and feeds the hook copy real events. The live
`.cupcake` is never touched. Asserts:

  * PreToolUse is refused and the reason carries the exact unlock command;
  * Stop is blocked when the closing message wraps the command in prose -- with and without
    `stop_hook_active`, since the agent can always satisfy this block;
  * Stop is allowed when the closing message is the bare command, a single code span or a lone
    fenced block, read from the transcript or from `last_assistant_message`;
  * running the unlock command restores a compiling engine (PreToolUse is no longer refused);
  * an engine failure that is not a policy file keeps the old reply (no unlock command, and the
    Stop re-entry is let through).

HOOK_UNDER_TEST overrides the hook file copied in, so a candidate can be tested before it replaces
the live one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.environ.get("HOOK_UNDER_TEST") or os.path.join(REPO, "scripts", "cupcake-hook.sh")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  -- " + detail[:600]))
    if not ok:
        FAILURES.append(name)


def git(root: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        timeout=30,
    )


def build(base: str) -> tuple[str, str]:
    root = os.path.realpath(os.path.join(base, "repo"))
    os.makedirs(os.path.join(root, "scripts", "lib"))
    shutil.copytree(os.path.join(REPO, ".cupcake"), os.path.join(root, ".cupcake"), ignore=shutil.ignore_patterns("state"))
    shutil.copy(HOOK, os.path.join(root, "scripts", "cupcake-hook.sh"))
    for rel in ("scripts/lib/cupcake-non-verdict.sh", "scripts/cupcake_engine_unlock.py", "scripts/cupcake_turn_scan.py"):
        shutil.copy(os.path.join(REPO, rel), os.path.join(root, rel))
    git(root, "init", "-q")
    git(root, "add", ".cupcake")
    git(root, "commit", "-q", "--no-verify", "-m", "fixture")
    glob_root = os.path.join(base, "global")
    os.makedirs(os.path.join(glob_root, "policies", "claude"))
    return root, glob_root


def run_hook(root: str, glob_root: str, event: dict, env_extra: dict | None = None) -> dict:
    env = dict(os.environ, CUPCAKE_GLOBAL_CONFIG=glob_root, **(env_extra or {}))
    proc = subprocess.run(
        ["bash", os.path.join(root, "scripts", "cupcake-hook.sh")],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=25,
    )
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError:
        return {"_raw": proc.stdout, "_rc": proc.returncode}


def transcript(base: str, name: str, closing: str) -> str:
    path = os.path.join(base, name + ".jsonl")
    events = [
        {"type": "user", "message": {"content": "go"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reverting."}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "true"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "refused"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": closing}]}},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def stop_event(base: str, name: str, closing: str, active: bool = False) -> dict:
    return {
        "hook_event_name": "Stop",
        "session_id": "s",
        "cwd": base,
        "permission_mode": "default",
        "stop_hook_active": active,
        "transcript_path": transcript(base, name, closing),
    }


def pre_tool(base: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "cwd": base,
        "permission_mode": "default",
        "transcript_path": "/dev/null",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }


def blocked(reply: dict) -> bool:
    return reply.get("decision") == "block"


def main() -> int:
    if shutil.which("cupcake") is None or shutil.which("git") is None:
        print("SKIP cupcake or git not on PATH")
        return 0
    base = tempfile.mkdtemp(prefix="cupcake-hook-engine-failure-")
    try:
        root, glob_root = build(base)
        tracked = os.path.join(root, ".cupcake", "policies", "claude", "no_whole_check_sh.rego")
        untracked = os.path.join(root, ".cupcake", "policies", "claude", "zz_untracked_broken.rego")
        with open(tracked, "a", encoding="utf-8") as fh:
            fh.write("\nbroken := {\n")
        with open(untracked, "w", encoding="utf-8") as fh:
            fh.write("package cupcake.policies.zz_untracked_broken\n\nbad := {\n")
        unlock = f"git -C {root} checkout HEAD -- {tracked} && rm -f {untracked}"

        reply = run_hook(root, glob_root, pre_tool(base))
        hso = reply.get("hookSpecificOutput", {})
        reason = hso.get("permissionDecisionReason", "")
        check("PreToolUse refused", hso.get("permissionDecision") == "deny", json.dumps(reply))
        check(
            "PreToolUse reason carries the exact unlock command",
            unlock in reason,
            "want " + unlock + " got " + reason.partition("unlock command is: ")[2],
        )
        check("PreToolUse reason says the closing message is only the command", "nothing else" in reason, reason)

        wrapped = "The engine is broken. Run this to unlock it:\n\n" + unlock + "\n\nThen I can continue."
        check("Stop blocked: prose around the command", blocked(run_hook(root, glob_root, stop_event(base, "p", wrapped))))
        check(
            "Stop blocked: prose, stop_hook_active",
            blocked(run_hook(root, glob_root, stop_event(base, "pa", wrapped, active=True))),
        )
        check(
            "Stop blocked: command plus one trailing word",
            blocked(run_hook(root, glob_root, stop_event(base, "w", unlock + " please"))),
        )
        for label, closing in (
            ("bare", unlock),
            ("bare with surrounding whitespace", "\n  " + unlock + "\n"),
            ("code span", "`" + unlock + "`"),
            ("fenced block", "```bash\n" + unlock + "\n```"),
        ):
            reply = run_hook(root, glob_root, stop_event(base, "ok", closing))
            check("Stop allowed: " + label, not blocked(reply) and "_raw" not in reply, json.dumps(reply))
        event = stop_event(base, "field", "stale prose")
        event["last_assistant_message"] = unlock
        check("Stop allowed: last_assistant_message field", not blocked(run_hook(root, glob_root, event)))

        # Not a policy file: the old reply, which lets the Stop re-entry through.
        fake = os.path.join(base, "fake-cupcake")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\necho 'ERROR Fatal: No space left on device' >&2\nexit 1\n")
        os.chmod(fake, 0o755)
        env = {"CUPCAKE_BIN": fake}
        reason = run_hook(root, glob_root, pre_tool(base), env).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        check("non-policy failure: refused without an unlock command", "No space left" in reason and "unlock" not in reason, reason)
        check(
            "non-policy failure: Stop re-entry let through",
            not blocked(run_hook(root, glob_root, stop_event(base, "np", wrapped, active=True), env)),
        )

        # The command really unlocks: run it, and the engine compiles again.
        proc = subprocess.run(["bash", "-c", unlock], capture_output=True, text=True, timeout=10)
        check("unlock command exits 0", proc.returncode == 0, proc.stderr)
        reply = run_hook(root, glob_root, pre_tool(base))
        reason = json.dumps(reply)
        check("after unlock: engine runs (no engine-failure notice)", "could not evaluate" not in reason, reason)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
