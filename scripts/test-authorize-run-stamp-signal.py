#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_authorize_without_run_stamp`.

The signal emits `AUTHNORUNSTAMP:<sha>\\t<checkout>\\t<note>` when the closing prose asks the user
for `authorize <sha>` and `scripts/check-runtime-evidence.sh <full sha>` exits 1 for it, and nothing
otherwise. User, 2026-10-16: "I knew it needed a run stamp to push. I was hoping you would figure it
out (again) prior to asking me to authorize a push".

Both directions run against the real evidence script in a throwaway repository: a commit that
changes a crate with no DLL log naming it halts; the same commit with a log naming it, and a
docs-only commit, pass. The run root and game directory are fixture directories, so nothing this
machine has built or launched can decide a case. The cases about which checkout a sha is resolved
in, and about the evidence script never being called, swap the script for a stub through
`CUPCAKE_AUTHORIZE_EVIDENCE_CHECK` that records where it ran.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_authorize_without_run_stamp.sh"
SIGNAL_TIMEOUT_SECONDS = 25
TAG = "AUTHNORUNSTAMP:"

# Variables that would let the caller's environment decide a verdict: a waived push, a stub left
# over from a parent run, or git's repository pins when this runs under a git hook.
SCRUBBED = (
    "ER_ALLOW_UNPROVEN_PUSH",
    "CUPCAKE_AUTHORIZE_EVIDENCE_CHECK",
    "ER_ME3_RUN_ROOT",
    "ER_GAME_DIR",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def use(uid: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]},
    }


def result(uid: str, text: str) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": text}]}}


class Fixture:
    """A throwaway repository, a run root and a game directory, all under one temp dir."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.runs = root / "runs"
        self.game = root / "game"
        self.repo.mkdir(parents=True)
        self.game.mkdir()
        # A run that is not this commit, so the run root is readable and answers "not found"
        # rather than "unmeasurable".
        (self.runs / "br-other").mkdir(parents=True)
        (self.runs / "br-other" / "er-thing.log").write_text("some other log\n", encoding="utf-8")

        self.git("init", "-q", "-b", "base", ".")
        self.git("config", "user.email", "selftest@example.invalid")
        self.git("config", "user.name", "selftest")
        self.git("config", "commit.gpgsign", "false")
        self.write("crates/er-thing/lib.rs", "fn main() {}\n")
        self.commit("the base")
        self.write("crates/er-thing/lib.rs", "fn main() { todo!() }\n")
        self.crate_sha = self.commit("game code")
        self.write("docs/notes.md", "a note\n")
        self.docs_sha = self.commit("a note only")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=SIGNAL_TIMEOUT_SECONDS,
            env=clean_env(),
        ).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def stamp(self, sha: str) -> None:
        """A run of `sha`: the first line every shell writes to its own log."""
        (self.runs / "br-good").mkdir(exist_ok=True)
        (self.runs / "br-good" / "er-thing.log").write_text(
            f"build git={sha[:12]} module=er_thing.dll base=0x1 pe=0x2 (t)\nmore\n", encoding="utf-8"
        )

    def env(self) -> dict[str, str]:
        env = clean_env()
        env["ER_ME3_RUN_ROOT"] = str(self.runs)
        env["ER_GAME_DIR"] = str(self.game)
        return env


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in SCRUBBED:
        env.pop(name, None)
    return env


def stub(marker: Path, code: int) -> str:
    """A stand-in evidence command that records the directory it ran in and exits `code`."""
    return f"sh -c 'pwd > {marker}; exit {code}' stub"


def run_signal(events: list[dict], cwd: Path, env: dict[str, str], tmp: Path) -> str:
    path = tmp / "session.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SIGNAL)],
        input=json.dumps({"transcript_path": str(path), "cwd": str(cwd), "hook_event_name": "Stop"}),
        cwd=tmp,
        text=True,
        capture_output=True,
        timeout=SIGNAL_TIMEOUT_SECONDS,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"the signal exited {proc.returncode}: {proc.stderr[-400:]}")
    return proc.stdout.strip()


def worktree_paths() -> set[str]:
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, timeout=SIGNAL_TIMEOUT_SECONDS, env=clean_env(),
    ).stdout
    return {
        os.path.realpath(line[len("worktree "):])
        for line in listing.splitlines()
        if line.startswith("worktree ")
    }


def repo_head() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=10", "HEAD"],
        capture_output=True, text=True, timeout=SIGNAL_TIMEOUT_SECONDS, check=True, env=clean_env(),
    ).stdout.strip()


def main() -> int:
    if shutil.which("git") is None:
        print("test-authorize-run-stamp-signal: SKIP (no git)")
        return 0
    failures = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        if ok:
            print(f"ok   {name}")
        else:
            failures += 1
            print(f"FAIL {name}: {detail}", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="authorize-run-stamp-") as tmpdir:
        tmp = Path(tmpdir)
        fx = Fixture(tmp / "fx")
        repo_real = os.path.realpath(fx.repo)
        crate7, crate12, docs7 = fx.crate_sha[:7], fx.crate_sha[:12], fx.docs_sha[:7]
        marker = tmp / "stub-ran-in"
        ask_crate = f"Committed {crate7} on base, unpushed. To publish it, reply `authorize {crate7}`."
        ask_docs = f"Committed {docs7} on base, unpushed. To publish it, reply `authorize {docs7}`."

        # 1. The halting direction, through the real check-runtime-evidence.sh.
        out = run_signal([user("commit it"), say(ask_crate)], fx.repo, fx.env(), tmp)
        fields = out[len(TAG):].split("\t") if out.startswith(TAG) else []
        report(
            "a crate change with no DLL log naming it halts",
            len(fields) == 3 and fields[0] == crate12 and os.path.realpath(fields[1]) == repo_real,
            f"got {out[:300]!r}",
        )
        # The fallback text is what the signal writes when it found no `evidence` line to quote.
        report(
            f"the halt quotes the evidence script's own note ({fields[2] if len(fields) == 3 else '-'})",
            len(fields) == 3 and fields[2] not in ("", "no DLL log names this commit"),
            f"got {out[:300]!r}",
        )

        # 2. The same commit and message once a run has stamped it.
        fx.stamp(fx.crate_sha)
        try:
            out = run_signal([user("commit it"), say(ask_crate)], fx.repo, fx.env(), tmp)
            report("the same ask passes once a DLL log names the commit", out == "", f"got {out[:300]!r}")
        finally:
            shutil.rmtree(fx.runs / "br-good")

        # 3. A docs-only commit has nothing for a run to prove.
        out = run_signal([user("commit it"), say(ask_docs)], fx.repo, fx.env(), tmp)
        report("a docs-only commit passes", out == "", f"got {out[:300]!r}")

        # 4. Two asks: the docs-only one passes, the crate one still halts.
        both = f"Two branches are committed: reply `authorize {docs7}` for the notes and `authorize {crate7}` for the code."
        out = run_signal([user("commit both"), say(both)], fx.repo, fx.env(), tmp)
        report("of two asks, the unstamped one halts", out.startswith(TAG + crate12), f"got {out[:300]!r}")

        # 5. A message with no `authorize` token never reaches the evidence command.
        env = fx.env()
        env["CUPCAKE_AUTHORIZE_EVIDENCE_CHECK"] = stub(marker, 1)
        out = run_signal([user("commit it"), say(f"Committed {crate7} on base; it is unpushed.")], fx.repo, env, tmp)
        report(
            "no authorize token: no output and the evidence command never runs",
            out == "" and not marker.exists(),
            f"got {out[:200]!r}, stub ran: {marker.exists()}",
        )

        # 6. A sha no checkout knows is allowed without running anything.
        out = run_signal([user("go"), say("Reply `authorize 0123456789abcdef0123` to publish.")], fx.repo, env, tmp)
        report(
            "a sha no checkout knows passes and runs nothing",
            out == "" and not marker.exists(),
            f"got {out[:200]!r}, stub ran: {marker.exists()}",
        )

        # 7. The evidence command's other statuses are not a refusal.
        env2 = fx.env()
        env2["CUPCAKE_AUTHORIZE_EVIDENCE_CHECK"] = stub(marker, 2)
        out = run_signal([user("commit it"), say(ask_crate)], fx.repo, env2, tmp)
        report("an unmeasurable answer (exit 2) passes", out == "" and marker.exists(), f"got {out[:200]!r}")
        marker.unlink(missing_ok=True)

        # 8. Only the closing prose run is judged.
        mid = [
            user("commit it"),
            say(ask_crate),
            use("e1", "Edit", {"file_path": "/x"}),
            result("e1", "ok"),
            say("Edited the note instead."),
        ]
        out = run_signal(mid, fx.repo, env, tmp)
        report("an ask written mid-turn and followed by work is not judged", out == "", f"got {out[:200]!r}")

        # 9. The session sits outside every repository: the sha is found through this
        #    repository's `git worktree list`, and the evidence runs in that checkout.
        outside = tmp / "not-a-repo"
        outside.mkdir()
        head = repo_head()
        out = run_signal(
            [user("commit it"), say(f"Reply `authorize {head}` to publish it.")], outside, env, tmp
        )
        ran_in = os.path.realpath(marker.read_text(encoding="utf-8").strip()) if marker.exists() else ""
        report(
            "a sha outside the session's directory is resolved through the worktree list",
            out.startswith(TAG + head[:10]) and ran_in in worktree_paths(),
            f"got {out[:200]!r}, stub ran in {ran_in!r}",
        )
        marker.unlink(missing_ok=True)

    if failures:
        print(f"test-authorize-run-stamp-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print("test-authorize-run-stamp-signal: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
