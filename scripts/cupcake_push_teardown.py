#!/usr/bin/env python3
"""Has the user just authorized a push, which needs Elden Ring closed first?

Read by `.cupcake/signals/push_teardown.sh`, consumed by
`.cupcake/policies/claude/teardown_must_relaunch.rego`, which admits a standalone
`er-teardown.py --reason=push-<token>` only when this prints an `AUTHORIZED` line.

User directive 2026-10-22, verbatim: "to authorize a push sha is to authorize a teardown". A push
runs the pre-push hook, and `scripts/check.sh` exits 2 while `er-teardown.py --status` reports a
game, so a push the user authorized cannot happen while the game is up. That day the user replied
`authorize 58a59772...` with a game running that stamped nothing (it tested an uncommitted
build), `--reason=cursor-test-done-for-push` was refused, and the authorization went stale while
the agent asked the user to close the game themselves. The `stamped-` form does not cover that
run: it admits only a `br-*` run whose own log stamps the branch being pushed.

Output, one tab-separated line, or nothing when the command does not name the push form:

    `AUTHORIZED <branch> <tip sha> <message uuid>`
    `DENIED <reason>`

`AUTHORIZED` requires every one of:

  1. The main session asks, never a subagent: an `authorize <sha>` reply is the user's to the
     session they typed it into.
  2. The last genuine prompt on the chain that ends at this tool call is exactly `authorize <sha>`.
     That is read by `scripts/cupcake_push_authorization.py`'s own `prompt_and_chain` and
     `AUTH_RE`, so the two guards cannot disagree about what the user said or which entries count
     as the user saying it; that module's docstring says what it refuses and why.
  3. The sha is a prefix of the tip of a local branch other than main or master, read from the
     refs of the repository the command runs in (every worktree shares them). The command text
     is never trusted for it.
  4. That reply has not already paid for a push: no record for its message uuid in the push
     guard's spent-authorization file. The teardown comes before the push it clears the way for.

Fails closed: the policy lifts its deny only on `AUTHORIZED`, so a crash, a timeout or empty
output keeps the teardown refused. There is no override variable.

Usage:
    python3 scripts/cupcake_push_teardown.py < event.json
    python3 scripts/cupcake_push_teardown.py --selftest
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import cupcake_push_authorization as push_auth  # noqa: E402

PUSH_REASON = re.compile(r"(?:^|\s)--reason=push-[A-Za-z0-9._-]+(?:\s|$)")


class Denied(Exception):
    pass


def names_push_form(command: str) -> bool:
    return "er-teardown.py" in command and PUSH_REASON.search(command) is not None


def branch_tips(cwd: str) -> list[tuple[str, str]]:
    out = subprocess.run(["git", "-C", cwd, "for-each-ref", "--format=%(objectname) %(refname:short)",
                          "refs/heads"], capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise Denied(f"cannot read the branches in {cwd}")
    pairs = []
    for line in out.stdout.splitlines():
        sha, _, branch = line.partition(" ")
        if re.fullmatch(r"[0-9a-f]{40}", sha) and branch:
            pairs.append((sha, branch))
    return pairs


def already_spent(message_uuid: str) -> bool:
    try:
        lines = push_auth.STATE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("message_uuid") == message_uuid:
            return True
    return False


def decide(event: dict, started: float) -> str | None:
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    if not names_push_form(command):
        return None
    if event.get("agent_id"):
        raise Denied("only the main session tears down on the user's push authorization")
    path, tool_use_id = event.get("transcript_path"), event.get("tool_use_id")
    if not isinstance(path, str) or not Path(path).is_file():
        raise Denied("the session transcript is not readable")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        raise Denied("the event names no tool call")
    try:
        message_uuid, text, _chain = push_auth.prompt_and_chain(
            path, tool_use_id, started + push_auth.LOCATE_DEADLINE_SECONDS)
    except push_auth.Refused as r:
        raise Denied(r.reason) from None
    m = push_auth.AUTH_RE.fullmatch(text.strip())
    if m is None:
        raise Denied("the user's last message is not `authorize <sha>`")
    prefix = m.group(1).lower()
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else str(SCRIPTS.parent)
    hits = [(sha, b) for sha, b in branch_tips(cwd)
            if sha.startswith(prefix) and b not in push_auth.PROTECTED_BRANCHES]
    if not hits:
        raise Denied(f"the user authorized {prefix}, which is the tip of no pushable local branch")
    if already_spent(message_uuid):
        raise Denied("that authorization already paid for a push, so no push is waiting on it")
    sha, branch = hits[0]
    return "\t".join(["AUTHORIZED", branch, sha, message_uuid])


def main() -> int:
    started = time.monotonic()
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        line = decide(event, started)
    except Denied as d:
        line = f"DENIED\t{d}"
    except Exception as exc:  # noqa: BLE001 -- any failure must deny, and say so
        line = f"DENIED\tthe reader failed: {type(exc).__name__}"
    if line:
        print(line)
    return 0


def selftest() -> int:
    import tempfile
    import uuid as uuid_mod

    failures = 0
    with tempfile.TemporaryDirectory(prefix="push-teardown-test-") as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                                  text=True, timeout=10).stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        git("commit", "-q", "--allow-empty", "-m", "base")
        main_tip = git("rev-parse", "HEAD")
        git("checkout", "-q", "-b", "fix/x")
        git("commit", "-q", "--allow-empty", "-m", "x")
        tip = git("rev-parse", "HEAD")
        push_auth.STATE_FILE = root / "push-authorizations.jsonl"

        teardown = "python3 /r/scripts/er-teardown.py --reason=push-needs-game-closed"

        def run(reply: str, command: str = teardown, reply_uuid: str | None = None,
                **extra) -> str:
            entries, head = [], None
            for kind, body in [("human", "push it"), ("say", f"authorize {tip}"),
                               ("reply", reply), ("use", command)]:
                e = {"uuid": (reply_uuid if kind == "reply" and reply_uuid
                              else str(uuid_mod.uuid4())),
                     "parentUuid": head, "isSidechain": False, "sessionId": "fixture"}
                kind = "human" if kind == "reply" else kind
                if kind == "human":
                    e.update({"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
                              "message": {"role": "user", "content": body}})
                elif kind == "say":
                    e.update({"type": "assistant", "message": {"role": "assistant",
                              "content": [{"type": "text", "text": body}]}})
                else:
                    e.update({"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "toolu_td", "name": "Bash",
                         "input": {"command": body}}]}})
                entries.append(e)
                head = e["uuid"]
            path = root / f"t{uuid_mod.uuid4().hex}.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
            event = {"transcript_path": str(path), "tool_use_id": "toolu_td", "cwd": str(repo),
                     "tool_input": {"command": command}}
            event.update(extra)
            try:
                return decide(event, time.monotonic()) or ""
            except Denied as d:
                return f"DENIED\t{d}"

        cases = [
            ("authorized tip admits", run(f"authorize {tip}"), "AUTHORIZED\tfix/x"),
            ("short sha admits", run(f"authorize {tip[:8]}"), "AUTHORIZED\tfix/x"),
            ("other words deny", run(f"authorize {tip} and close it"), "DENIED"),
            ("a question denies", run("can you close the game?"), "DENIED"),
            ("main's tip denies", run(f"authorize {main_tip}"), "DENIED"),
            ("unknown sha denies", run("authorize deadbeefdeadbeef"), "DENIED"),
            ("subagent denies", run(f"authorize {tip}", agent_id="sub"), "DENIED"),
            ("other reason prints nothing",
             run(f"authorize {tip}", command="python3 /r/scripts/er-teardown.py --reason=x"), ""),
        ]
        for name, got, want in cases:
            ok = got.startswith(want) if want else got == ""
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {name}: {got or '(nothing)'}")

        push_auth.STATE_FILE.write_text(json.dumps({"message_uuid": "spent-reply"}) + "\n",
                                        encoding="utf-8")
        got = run(f"authorize {tip}", reply_uuid="spent-reply")
        ok = got.startswith("DENIED") and "already paid" in got
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} a reply that paid for a push denies: {got}")
    print("selftest:", "FAILED" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["--selftest"] else main())
