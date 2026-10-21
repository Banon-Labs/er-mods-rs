#!/usr/bin/env python3
"""~/.claude/hooks/cupcake-hook.sh when a policy file does not compile: the reply carries the unlock command.

The global wrapper runs on every tool call of every session, against whatever project `.cupcake` the
session has and the global tree at ${XDG_CONFIG_HOME:-$HOME/.config}/cupcake. This runs the installed
wrapper in place against throwaway trees -- a git checkout holding a copy of this repo's `.cupcake`
as "some other project", and a git copy of the real global config as XDG_CONFIG_HOME -- so neither
the live project tree nor the live global tree is touched. Asserts, with the project tree found
through CLAUDE_PROJECT_DIR and again through the cwd lookup:

  * PreToolUse is refused and the reason carries the exact unlock command;
  * Stop is blocked when the closing message wraps the command in prose -- with and without
    `stop_hook_active`;
  * Stop is allowed when the closing message is the bare command, a single code span or a lone
    fenced block, read from the transcript or from `last_assistant_message`;
  * running the unlock command restores a compiling engine;
  * a broken global policy is named by its own `git -C <global root> checkout HEAD -- <file>`;
  * an engine failure that is not a policy file keeps the old reply;
  * the wrapper's lib/ copies of the helper, the transcript reader and the non-verdict library are
    byte-identical to this repo's, so the two wrappers cannot drift apart silently.

GLOBAL_HOOK_UNDER_TEST overrides the wrapper path, so a candidate can be tested before it is installed.
"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.environ.get("GLOBAL_HOOK_UNDER_TEST") or os.path.expanduser("~/.claude/hooks/cupcake-hook.sh")
LIB = os.path.join(os.path.dirname(HOOK), "lib")
GLOBAL_SRC = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "cupcake")
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
    root = os.path.realpath(os.path.join(base, "project"))
    os.makedirs(os.path.join(root, "sub"))
    shutil.copytree(os.path.join(REPO, ".cupcake"), os.path.join(root, ".cupcake"), ignore=shutil.ignore_patterns("state"))
    git(root, "init", "-q")
    git(root, "add", ".cupcake")
    git(root, "commit", "-q", "--no-verify", "-m", "fixture")
    config = os.path.realpath(os.path.join(base, "config"))
    glob_root = os.path.join(config, "cupcake")
    shutil.copytree(GLOBAL_SRC, glob_root, ignore=shutil.ignore_patterns(".git", "state", "debug"))
    git(glob_root, "init", "-q")
    git(glob_root, "add", ".")
    git(glob_root, "commit", "-q", "--no-verify", "-m", "fixture")
    return root, config


def run_hook(root: str, config: str, event: dict, mode: str, env_extra: dict | None = None) -> dict:
    env = dict(os.environ, XDG_CONFIG_HOME=config, **(env_extra or {}))
    env.pop("CUPCAKE_GLOBAL_CONFIG", None)
    if mode == "project-dir":
        env["CLAUDE_PROJECT_DIR"] = root
        cwd = os.path.dirname(root)
    else:
        env.pop("CLAUDE_PROJECT_DIR", None)
        cwd = root
    proc = subprocess.run(
        ["bash", HOOK], input=json.dumps(event), capture_output=True, text=True, env=env, cwd=cwd, timeout=25
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


def reason_of(reply: dict) -> str:
    return reply.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def unlock_cases(base: str, root: str, config: str, mode: str, unlock: str) -> None:
    tag = "[" + mode + "] "
    reply = run_hook(root, config, pre_tool(base), mode)
    check(tag + "PreToolUse refused", reply.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", json.dumps(reply))
    reason = reason_of(reply)
    check(
        tag + "PreToolUse reason carries the exact unlock command",
        unlock in reason,
        "want " + unlock + " got " + reason.partition("unlock command is: ")[2],
    )
    check(tag + "PreToolUse reason says the closing message is only the command", "nothing else" in reason, reason)
    wrapped = "The engine is broken. Run this to unlock it:\n\n" + unlock + "\n\nThen I can continue."
    check(tag + "Stop blocked: prose around the command", blocked(run_hook(root, config, stop_event(base, "p", wrapped), mode)))
    check(tag + "Stop blocked: prose, stop_hook_active", blocked(run_hook(root, config, stop_event(base, "pa", wrapped, True), mode)))
    check(tag + "Stop blocked: command plus one trailing word", blocked(run_hook(root, config, stop_event(base, "w", unlock + " please"), mode)))
    for label, closing in (
        ("bare", unlock),
        ("bare with surrounding whitespace", "\n  " + unlock + "\n"),
        ("code span", "`" + unlock + "`"),
        ("fenced block", "```bash\n" + unlock + "\n```"),
    ):
        reply = run_hook(root, config, stop_event(base, "ok", closing), mode)
        check(tag + "Stop allowed: " + label, not blocked(reply) and "_raw" not in reply, json.dumps(reply))
    event = stop_event(base, "field", "stale prose")
    event["last_assistant_message"] = unlock
    check(tag + "Stop allowed: last_assistant_message field", not blocked(run_hook(root, config, event, mode)))


def main() -> int:
    if not os.path.isfile(HOOK):
        print("FAIL no wrapper at " + HOOK)
        return 1
    for name, repo_rel in (
        ("cupcake_engine_unlock.py", "scripts/cupcake_engine_unlock.py"),
        ("cupcake_turn_scan.py", "scripts/cupcake_turn_scan.py"),
        ("cupcake-non-verdict.sh", "scripts/lib/cupcake-non-verdict.sh"),
    ):
        copy = os.path.join(LIB, name)
        check(
            "lib/" + name + " is a verbatim copy of " + repo_rel,
            os.path.isfile(copy) and filecmp.cmp(copy, os.path.join(REPO, repo_rel), shallow=False),
            copy,
        )
    if shutil.which("cupcake") is None or shutil.which("git") is None:
        print("SKIP cupcake or git not on PATH")
        return 1 if FAILURES else 0
    if not os.path.isdir(os.path.join(GLOBAL_SRC, "policies", "claude")):
        print("SKIP no global cupcake config at " + GLOBAL_SRC)
        return 1 if FAILURES else 0
    base = tempfile.mkdtemp(prefix="global-cupcake-hook-engine-failure-")
    try:
        root, config = build(base)
        reply = run_hook(root, config, pre_tool(base), "project-dir")
        check("clean trees: engine runs", "could not evaluate" not in json.dumps(reply), json.dumps(reply))

        tracked = os.path.join(root, ".cupcake", "policies", "claude", "no_whole_check_sh.rego")
        untracked = os.path.join(root, ".cupcake", "policies", "claude", "zz_untracked_broken.rego")
        with open(tracked, "a", encoding="utf-8") as fh:
            fh.write("\nbroken := {\n")
        with open(untracked, "w", encoding="utf-8") as fh:
            fh.write("package cupcake.policies.zz_untracked_broken\n\nbad := {\n")
        unlock = f"git -C {root} checkout HEAD -- {tracked} && rm -f {untracked}"
        unlock_cases(base, root, config, "project-dir", unlock)
        unlock_cases(base, root, config, "cwd", unlock)

        proc = subprocess.run(["bash", "-c", unlock], capture_output=True, text=True, timeout=10)
        check("unlock command exits 0", proc.returncode == 0, proc.stderr)
        reply = run_hook(root, config, pre_tool(base), "project-dir")
        check("after unlock: engine runs", "could not evaluate" not in json.dumps(reply), json.dumps(reply))

        # A broken policy in the global tree, with the project tree clean.
        glob_root = os.path.join(config, "cupcake")
        glob_claude = os.path.join(glob_root, "policies", "claude")
        target = next(
            (os.path.join(glob_claude, f) for f in sorted(os.listdir(glob_claude)) if f.endswith(".rego")),
            None,
        )
        if target is None:
            print("SKIP global tree has no top-level claude policy to break")
        else:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write("\nbroken := {\n")
            g_unlock = f"git -C {glob_root} checkout HEAD -- {target}"
            reason = reason_of(run_hook(root, config, pre_tool(base), "project-dir"))
            check("global policy: reason carries its unlock command", g_unlock in reason, "want " + g_unlock + " got " + reason)
            check(
                "global policy: Stop allowed on the bare command",
                not blocked(run_hook(root, config, stop_event(base, "g", g_unlock), "project-dir")),
            )
            subprocess.run(["bash", "-c", g_unlock], capture_output=True, timeout=10)
            reply = run_hook(root, config, pre_tool(base), "project-dir")
            check("global policy: after unlock engine runs", "could not evaluate" not in json.dumps(reply), json.dumps(reply))

        # Not a policy file: the old reply, which lets the Stop re-entry through.
        fake = os.path.join(base, "fake-cupcake")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\necho 'ERROR Fatal: No space left on device' >&2\nexit 1\n")
        os.chmod(fake, 0o755)
        env = {"CUPCAKE_BIN": fake}
        reason = reason_of(run_hook(root, config, pre_tool(base), "project-dir", env))
        check("non-policy failure: refused without an unlock command", "No space left" in reason and "unlock" not in reason, reason)
        wrapped = "The engine is broken.\n\nsomething"
        check(
            "non-policy failure: Stop re-entry let through",
            not blocked(run_hook(root, config, stop_event(base, "np", wrapped, active=True), "project-dir", env)),
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
