#!/usr/bin/env python3
"""Keep an authorized push from dying on a running Elden Ring, and from wasting the authorization.

Called by `scripts/hooks/pre-push`. Two subcommands:

    clear-game --pair <local ref> <sha> [--pair ...]
    refund --reason <token> --pair <local ref> <sha> [--pair ...]

Why it exists. 2026-10-27: the user replied `authorize 6e313ae7...`, the main agent ran
`git push origin fix/crash-logging-freelist-quit-patch` while the run it had launched to stamp that
tip (br-20261027-210646-679a) was still up, and the hook's `scripts/check.sh` refused with
"check.sh: REFUSED -- Elden Ring is running." The push sent nothing, but
`scripts/cupcake_push_authorization.py` had already recorded the reply as spent at PreToolUse, so
the retry was refused, and so was the `er-teardown.py --reason=push-<token>` teardown the
`push_teardown` signal admits only for an unspent reply. The user had already said, 2026-10-22,
"to authorize a push sha is to authorize a teardown"; the one reply should have covered both.

`clear-game`: when a game is up and some pushed tip still has to run the suite, the hook calls this
just before `scripts/check.sh`. It tears the game down itself, with
`er-teardown.py --reason=push-<tip12>`, only when both hold:

  1. The game is an agent launch: the newest `br-*` run under `er_run_lib.RUN_STATE_ROOT` still has
     its `run.json` and its launcher pid is alive. A game the user started through
     `~/Elden/launch.sh` writes no `br-*` directory, so it is never torn down here.
  2. This push is the one the user authorized: a pushed `refs/heads/<branch>` and sha match a
     record in `.cupcake/state/push-authorizations.jsonl` written in the last `MAX_AGE_MINUTES`,
     and that record is still the live holder of its reply (not refunded). The record is written by
     the push guard's signal from the user's own `authorize <sha>` reply; a push typed by the user
     in their own terminal has no such record, and the hook refuses as before.

Otherwise it refuses (exit 1) before the suite starts, and refunds the authorization (below), so
the agent can close the game with `er-teardown.py --reason=push-<token>` and push again on the same
reply.

`refund`: the hook calls this when it refuses a push for a precondition that says nothing about the
pushed content and that the agent can clear -- a dirty working tree, another push holding the gate
tree, missing runtime evidence, a game still up. It appends a record marking the reply's current
holder refunded, which `cupcake_push_authorization.spend` reads as "this holder did not spend it",
exactly like a holder Claude Code refused before it ran. The authorization stays bound to the same
branch tip, so a refund can never buy a push of anything the user did not authorize; at worst it
buys a second push of the same tip, which sends nothing new.

Exit status: 0 = clear to run the suite (or refund done), 1 = refused, 2 = usage.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import er_run_lib  # noqa: E402 -- RUN_STATE_ROOT and `process_alive`, the launcher's own

STATE_REL = Path(".cupcake") / "state" / "push-authorizations.jsonl"
# A spend is written at PreToolUse and the hook starts at once, but the compile and runtime-evidence
# gates run in between, and the compile gate can take minutes. An hour covers it without letting a
# reply from an earlier session reach a push today.
MAX_AGE_MINUTES = 60.0


def say(msg: str) -> None:
    print(f"pre-push: {msg}", file=sys.stderr)


# --- the authorization records -----------------------------------------------------------------

def state_files(cwd: str) -> list[Path]:
    """The spent-authorization files this push could have been recorded in.

    The signal writes beside the `.cupcake/` of the checkout cupcake runs in, which is the session's
    project directory -- usually the main checkout even when the push names a linked worktree. So
    both the pushed checkout's and the main checkout's are read.
    """
    out: list[Path] = []
    for args in (["rev-parse", "--show-toplevel"],
                 ["rev-parse", "--path-format=absolute", "--git-common-dir"]):
        try:
            proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                                  timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        root = Path(proc.stdout.strip())
        if args[-1] == "--git-common-dir":
            root = root.parent
        path = root / STATE_REL
        if path not in out:
            out.append(path)
    return out


def read_records(fh) -> list[dict]:
    fh.seek(0)
    records = []
    for line in fh:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def live_holder(records: list[dict], pairs: list[tuple[str, str]], now: float,
                max_age_minutes: float) -> dict | None:
    """The newest record that still holds its reply for one of the pushed (branch, sha) pairs."""
    last_for_message: dict[str, dict] = {}
    for rec in records:
        if isinstance(rec.get("message_uuid"), str):
            last_for_message[rec["message_uuid"]] = rec
    wanted = set(pairs)
    best = None
    for rec in last_for_message.values():
        if rec.get("refunded"):
            continue
        if (rec.get("branch"), rec.get("tip")) not in wanted:
            continue
        at = rec.get("at")
        if not isinstance(at, (int, float)) or now - at > max_age_minutes * 60 or at > now + 60:
            continue
        if best is None or at > best["at"]:
            best = rec
    return best


def find_live_holder(paths: list[Path], pairs: list[tuple[str, str]], now: float,
                     max_age_minutes: float) -> dict | None:
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                rec = live_holder(read_records(fh), pairs, now, max_age_minutes)
        except OSError:
            continue
        if rec is not None:
            return rec
    return None


def refund(paths: list[Path], pairs: list[tuple[str, str]], reason: str, now: float,
           max_age_minutes: float) -> dict | None:
    """Mark the live holder for this push refunded; the refund record, or None when none held."""
    for path in paths:
        if not path.is_file():
            continue
        try:
            with open(path, "a+", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                holder = live_holder(read_records(fh), pairs, now, max_age_minutes)
                if holder is None:
                    continue
                record = {"message_uuid": holder["message_uuid"],
                          "tool_use_id": holder.get("tool_use_id"),
                          "branch": holder["branch"], "tip": holder["tip"],
                          "refunded": reason, "at": now}
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(record) + "\n")
                return record
        except OSError:
            continue
    return None


# --- the game ----------------------------------------------------------------------------------

def game_up_real() -> bool:
    """`er-teardown.py --status` says a game is up -- the detector check.sh refuses on."""
    try:
        proc = subprocess.run([sys.executable, str(SCRIPTS / "er-teardown.py"), "--status"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return "game health: pid=" in proc.stdout


def agent_run_up_real(root: Path | None = None) -> str | None:
    """The run id of a live `er-run-branch.py` launch, or None."""
    root = root or er_run_lib.RUN_STATE_ROOT
    try:
        runs = [d for d in root.iterdir() if d.is_dir() and d.name.startswith("br-")]
    except OSError:
        return None
    if not runs:
        return None
    run = max(runs, key=lambda d: d.name)  # `br-<UTC yyyymmdd-hhmmss>-<hex>` sorts by launch time
    try:
        state = json.loads((run / "run.json").read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return run.name if er_run_lib.process_alive(pid) else None


def teardown_real(reason: str) -> int:
    try:
        return subprocess.run([sys.executable, str(SCRIPTS / "er-teardown.py"),
                               f"--reason={reason}"], timeout=30).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def clear_game(pairs: list[tuple[str, str]], paths: list[Path], now: float,
               max_age_minutes: float = MAX_AGE_MINUTES,
               game_up: Callable[[], bool] = game_up_real,
               agent_run: Callable[[], str | None] = agent_run_up_real,
               teardown: Callable[[str], int] = teardown_real) -> int:
    if not game_up():
        return 0
    holder = find_live_holder(paths, pairs, now, max_age_minutes)
    run = agent_run()
    if holder is not None and run is not None:
        tip12 = str(holder["tip"])[:12]
        say(f"Elden Ring is up as agent run {run}, and the user's `authorize {tip12}` reply "
            f"covers its teardown -- closing it before the suite.")
        teardown(f"push-{tip12}")
        if not game_up():
            return 0
        say("REFUSED -- er-teardown.py did not leave the game closed (a wedged game refuses to "
            "be torn down until it is walked; see its output above).")
    elif holder is None:
        say("REFUSED -- Elden Ring is running, and this push carries no live `authorize <sha>` "
            "record, so the hook leaves the game alone. Close the game, then push again.")
        return 1
    else:
        say("REFUSED -- Elden Ring is running and no live er-run-branch.py run launched it, so "
            "the hook does not close it on its own.")
    rec = refund(paths, pairs, "game-running", now, max_age_minutes)
    if rec is not None:
        say(f"the user's authorization for {str(rec['tip'])[:12]} is NOT spent by this push. "
            "Close the game with `python3 scripts/er-teardown.py --reason=push-<one-token-why>` "
            "as the whole command, then run the same `git push` again.")
    return 1


# --- the command line --------------------------------------------------------------------------

def parse_pairs(raw: list[list[str]]) -> list[tuple[str, str]]:
    pairs = []
    for ref, sha in raw or []:
        if ref.startswith("refs/heads/") and sha.strip("0"):
            pairs.append((ref[len("refs/heads/"):], sha))
    return pairs


def main(argv: list[str]) -> int:
    if argv == ["--selftest"]:
        return selftest()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("clear-game", "refund"):
        p = sub.add_parser(name)
        p.add_argument("--pair", nargs=2, action="append", metavar=("REF", "SHA"), default=[])
        p.add_argument("--max-age-minutes", type=float, default=MAX_AGE_MINUTES)
        if name == "refund":
            p.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    pairs = parse_pairs(args.pair)
    paths = state_files(os.getcwd())
    now = time.time()
    if args.cmd == "clear-game":
        return clear_game(pairs, paths, now, args.max_age_minutes)
    rec = refund(paths, pairs, args.reason, now, args.max_age_minutes)
    if rec is not None:
        say(f"refused before any gate judged the content ({args.reason}), so the user's "
            f"authorization for {str(rec['tip'])[:12]} is NOT spent: clear that, then run the same "
            "`git push` again.")
    return 0


def selftest() -> int:
    import tempfile

    failures = 0

    def check(label: str, ok: bool, got: object = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {label}{'' if ok else f': {got}'}")

    tip = "6e313ae726b67a6e94ad2fb4e80c49218a355766"
    other = "a44bc06b081ef074dda4453594e5315ac3150842"
    branch = "fix/x"
    pairs = parse_pairs([[f"refs/heads/{branch}", tip]])
    check("pairs keep branch refs", pairs == [(branch, tip)], pairs)
    check("pairs drop deletions and tags",
          parse_pairs([["refs/heads/a", "0" * 40], ["refs/tags/v1", tip]]) == [])

    with tempfile.TemporaryDirectory(prefix="pre-push-clear-test-") as tmp:
        state = Path(tmp) / "push-authorizations.jsonl"
        now = time.time()

        def write(*recs: dict) -> None:
            state.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")

        def spend(msg: str = "m1", t: str = tip, at: float = now - 30, tool: str = "toolu_a") -> dict:
            return {"message_uuid": msg, "tool_use_id": tool, "branch": branch, "tip": t, "at": at}

        def run(up: list[bool], agent: str | None) -> tuple[int, list[str]]:
            torn: list[str] = []
            states = iter(up)
            rc = clear_game(pairs, [state], now, game_up=lambda: next(states),
                            agent_run=lambda: agent, teardown=lambda r: torn.append(r) or 0)
            return rc, torn

        write(spend())
        rc, torn = run([False], "br-1")
        check("no game: clear, nothing torn down", rc == 0 and torn == [], (rc, torn))

        rc, torn = run([True, False], "br-1")
        check("agent run + live authorization: torn down with push-<tip12>, clear",
              rc == 0 and torn == [f"push-{tip[:12]}"], (rc, torn))

        write(spend())
        rc, torn = run([True], None)
        recs = [json.loads(line) for line in state.read_text().splitlines()]
        check("user-launched game: refused, not torn down", rc == 1 and torn == [], (rc, torn))
        check("user-launched game: authorization refunded",
              recs[-1].get("refunded") == "game-running" and recs[-1]["message_uuid"] == "m1", recs)
        rc, torn = run([True], "br-1")
        check("after a refund nothing live holds the reply: refused, not torn down",
              rc == 1 and torn == [], (rc, torn))

        write(spend())
        rc, torn = run([True, True], "br-1")
        recs = [json.loads(line) for line in state.read_text().splitlines()]
        check("teardown that leaves the game up: refused and refunded",
              rc == 1 and torn and recs[-1].get("refunded") == "game-running", (rc, recs))

        write(spend(t=other))
        rc, torn = run([True], "br-1")
        check("authorization for another tip: refused, not torn down", rc == 1 and torn == [],
              (rc, torn))

        write(spend(at=now - 2 * 3600))
        rc, torn = run([True], "br-1")
        check("stale authorization: refused, not torn down", rc == 1 and torn == [], (rc, torn))

        write()
        rc, torn = run([True], "br-1")
        check("no authorization at all (a push the user typed): refused, not torn down",
              rc == 1 and torn == [], (rc, torn))

        # A reply whose holder moved on to a later call: only the last record per reply counts.
        write(spend(tool="toolu_a"), {**spend(tool="toolu_b"), "replaces": "toolu_a"})
        rec = refund([state], pairs, "dirty-tree", now, MAX_AGE_MINUTES)
        check("refund names the current holder", rec is not None and rec["tool_use_id"] == "toolu_b",
              rec)
        check("a second refund finds nothing to refund",
              refund([state], pairs, "dirty-tree", now, MAX_AGE_MINUTES) is None)

        # And the reader the push guard uses must treat the refunded holder as not having spent it.
        import cupcake_push_authorization as push_auth
        push_auth.STATE_FILE = state
        got = push_auth.spend("m1", "toolu_c", branch, tip, ran=lambda _h: True)
        check("the push guard lets a new call take over a refunded reply", got is None, got)
        got = push_auth.spend("m1", "toolu_d", branch, tip, ran=lambda _h: True)
        check("...and that new call then holds it as before",
              got == "that authorization was already spent on another push", got)

    print("selftest:", "FAILED" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
