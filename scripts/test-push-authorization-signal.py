#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `push_authorization` and the push guard it feeds.

The signal prints `AUTHORIZED <branch> <tip> <uuid>` only when the user's last typed message is
exactly `authorize <sha>`, the sha is the tip of the one branch the command pushes, and that message
has not already paid for another push. Each case writes a transcript fixture shaped like the ones
Claude Code writes (entry fields measured on this project's transcripts, 2026-10-07), builds a
throwaway git repository with a bare remote, and hands the signal a PreToolUse event on stdin the way
cupcake does. The late cases hold the tool call's own line back and append it only after the reader
has read the transcript once, because Claude Code starts the hook before that line is on disk
(measured 2026-10-14).

Nothing is pushed: the signal only reads refs, and the last case runs `cupcake eval`, which decides
and never executes. The reader and the signal are copied into a temporary root so the spent-
authorization record it writes lands there, not in this checkout's `.cupcake/state`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid as uuid_mod
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
READER = REPO_ROOT / "scripts" / "cupcake_push_authorization.py"
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "push_authorization.sh"

sys.path.insert(0, str(READER.parent))
sys.dont_write_bytecode = True
import cupcake_push_authorization as reader  # noqa: E402

# What cupcake 0.5.2 gives a signal it discovered in `.cupcake/signals/` (`default_timeout()` in
# cupcake-core's `engine/rulebook.rs`; measured 2026-10-14: a signal answering after 4.6 s was
# read, one answering after 5.4 s was dropped).
CUPCAKE_SIGNAL_SECONDS = 5.0

FAILURES: list[str] = []


def signal_cap_seconds() -> float | None:
    """The `timeout` the signal script puts on the reader, read out of the script itself."""
    m = re.search(r"timeout ([0-9.]+) python3", SIGNAL.read_text(encoding="utf-8"))
    return float(m.group(1)) if m else None


# Upper bound on waiting for the reader to open the transcript at all; it does so well inside a
# second, and a reader that never does fails the case through its verdict.
READ_SEEN_SECONDS = 10.0


def run_late(argv: list[str], cwd: Path, event: dict, path: Path, pieces: list[bytes],
             during=None) -> tuple[str, int, float]:
    """Run `argv` on `event` while `path` is still being written, as Claude Code does.

    Each time the reader finishes reading the transcript (an inotify close-without-write on it),
    the next of `pieces` is appended, so the first piece always lands after the reader's first look
    -- the order measured on 2026-10-14. `during` runs before the first piece. With no pieces the
    line never lands. Returns stdout, the exit code and the seconds until the process ended.
    """
    event_file = path.with_suffix(".event.json")
    event_file.write_text(json.dumps(event), encoding="utf-8")
    with reader.FileEvents(str(path), reader.IN_CLOSE_NOWRITE) as reads, \
            open(event_file, encoding="utf-8") as stdin:
        started = time.monotonic()
        proc = subprocess.Popen(argv, cwd=cwd, stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for i, piece in enumerate(pieces):
            if not reads.wait(READ_SEEN_SECONDS):
                break
            if i == 0 and during is not None:
                during()
            with open(path, "ab") as fh:
                fh.write(piece)
        out, _ = proc.communicate(timeout=25)
        return out, proc.returncode, time.monotonic() - started


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


def sh(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, timeout=10).stdout.strip()


# --- fixtures ----------------------------------------------------------------------------------

class Chain:
    """Builds transcript entries linked through parentUuid, the way Claude Code writes them."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.n = 0
        self.head: str | None = None

    def _add(self, entry: dict, on_chain: bool = True, uuid: str | None = None) -> dict:
        self.n += 1
        entry["uuid"] = uuid or str(uuid_mod.uuid4())
        entry["parentUuid"] = self.head
        entry.setdefault("isSidechain", False)
        entry["sessionId"] = "fixture"
        self.entries.append(entry)
        if on_chain:
            self.head = entry["uuid"]
        return entry

    def human(self, text: str, source: str = "typed", **extra) -> dict:
        e = {"type": "user", "origin": {"kind": "human"}, "promptSource": source,
             "message": {"role": "user", "content": text}}
        e.update(extra)
        return self._add(e)

    def say(self, text: str) -> dict:
        return self._add({"type": "assistant", "message": {"role": "assistant",
                          "content": [{"type": "text", "text": text}]}})

    def use(self, tool_use_id: str, command: str) -> dict:
        return self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": command}}]}})

    def result(self, tool_use_id: str, text: str) -> dict:
        return self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}})

    def refused(self, tool_use_id: str, kind: str = "permission-rule", on_chain: bool = True) -> dict:
        """The result Claude Code writes for a call it refused before running it (measured 2026-10-20)."""
        text = ("PreToolUse:Bash hook error: This push carries changes under crates/ that have "
                "never run.")
        return self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": text, "is_error": True, "tool_use_id": tool_use_id}]},
            "toolUseResult": "Error: " + text, "toolDenialKind": kind}, on_chain=on_chain)

    def failed(self, tool_use_id: str) -> dict:
        """A call that ran and exited non-zero: an error result with no denial kind."""
        return self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "Exit code 1\nerror: failed to push some refs",
             "is_error": True, "tool_use_id": tool_use_id}]}})

    def notification(self, text: str) -> dict:
        return self._add({"type": "user", "origin": {"kind": "task-notification"},
                          "promptSource": "system", "message": {"role": "user", "content": text}})

    def meta(self, text: str) -> dict:
        return self._add({"type": "user", "isMeta": True, "message": {"role": "user", "content": text}})

    def attachment(self) -> dict:
        return self._add({"type": "attachment", "attachment": {"type": "hook_success",
                          "hookEvent": "PreToolUse", "content": ""}})

    def forged(self, text: str, uuid: str | None = None) -> dict:
        """An entry appended by someone other than Claude Code: never becomes the chain's head."""
        e = {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
             "message": {"role": "user", "content": text}}
        return self._add(e, on_chain=False, uuid=uuid)

    def forged_use(self, tool_use_id: str, command: str, parent: dict) -> dict:
        """A copy of a tool call appended by someone else, hung off a forged `parent`."""
        e = self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": command}}]}},
            on_chain=False)
        e["parentUuid"] = parent["uuid"]
        return e

    def write(self, path: Path) -> Path:
        path.write_text("".join(json.dumps(e) + "\n" for e in self.entries), encoding="utf-8")
        return path


class Sandbox:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="push-auth-test-"))
        self.root = self.dir / "tree"            # stands in for this checkout
        (self.root / "scripts").mkdir(parents=True)
        (self.root / ".cupcake" / "signals").mkdir(parents=True)
        shutil.copy2(READER, self.root / "scripts" / READER.name)
        shutil.copy2(SIGNAL, self.root / ".cupcake" / "signals" / SIGNAL.name)
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        bare = self.dir / "remote.git"
        sh(["git", "init", "-q", "--bare", str(bare)], self.dir)
        sh(["git", "init", "-q", "-b", "main"], self.repo)
        sh(["git", "config", "user.email", "t@example.invalid"], self.repo)
        sh(["git", "config", "user.name", "t"], self.repo)
        sh(["git", "commit", "-q", "--allow-empty", "-m", "base"], self.repo)
        sh(["git", "checkout", "-q", "-b", "feat/x"], self.repo)
        sh(["git", "commit", "-q", "--allow-empty", "-m", "x"], self.repo)
        sh(["git", "branch", "moving"], self.repo)
        sh(["git", "checkout", "-q", "-b", "other", "main"], self.repo)
        sh(["git", "commit", "-q", "--allow-empty", "-m", "other"], self.repo)
        sh(["git", "remote", "add", "origin", str(bare)], self.repo)
        self.tip = sh(["git", "rev-parse", "refs/heads/feat/x"], self.repo)
        self.other_tip = sh(["git", "rev-parse", "refs/heads/other"], self.repo)
        self.main_tip = sh(["git", "rev-parse", "refs/heads/main"], self.repo)
        self.n = 0

    def run(self, chain: Chain, tool_use_id: str, command: str, **extra) -> list[str]:
        self.n += 1
        path = chain.write(self.dir / f"t{self.n}.jsonl")
        event = {"session_id": "fixture", "transcript_path": str(path), "cwd": str(self.repo),
                 "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": tool_use_id,
                 "tool_input": {"command": command}}
        event.update(extra)
        proc = subprocess.run(["bash", str(self.root / ".cupcake" / "signals" / SIGNAL.name)],
                              input=json.dumps(event), text=True, capture_output=True, timeout=20)
        return proc.stdout.strip().split("\t") if proc.stdout.strip() else []

    def run_late(self, chain: Chain, tool_use_id: str, command: str, pieces: list[bytes],
                 during=None) -> tuple[list[str], float]:
        """The signal, with the tool call's own line written only after the reader has looked.

        The tool_use line must be the chain's last entry; it is held back (see `run_late`).
        Returns the signal's fields and how long it took to answer.
        """
        self.n += 1
        path = self.dir / f"t{self.n}.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in chain.entries[:-1]), encoding="utf-8")
        event = {"session_id": "fixture", "transcript_path": str(path), "cwd": str(self.repo),
                 "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": tool_use_id,
                 "tool_input": {"command": command}}
        out, _, took = run_late(["bash", str(self.root / ".cupcake" / "signals" / SIGNAL.name)],
                                self.dir, event, path, pieces, during)
        return (out.strip().split("\t") if out.strip() else []), took

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


PUSH = "git push origin feat/x"


def ask_then_push(sb: Sandbox, reply: str, tool_use_id: str = "toolu_push", command: str = PUSH,
                  **human_extra) -> tuple[Chain, list[str]]:
    c = Chain()
    c.human("push feat/x please")
    c.say("Reply with exactly: authorize " + sb.tip)
    c.human(reply, **human_extra)
    c.say("Pushing.")
    c.use(tool_use_id, command)
    return c, sb.run(c, tool_use_id, command)


def expect(name: str, out: list[str], verdict: str, contains: str = "") -> None:
    ok = bool(out) and out[0] == verdict and (not contains or contains in "\t".join(out))
    check(name, ok, f"got {out!r}")


def main() -> int:
    sb = Sandbox()
    try:
        # --- the one allowed shape -----------------------------------------------------------
        _, out = ask_then_push(sb, f"authorize {sb.tip}", "toolu_a1")
        expect("exact full-sha authorization is accepted", out, "AUTHORIZED")
        check("accepted line names branch and tip", out[1:3] == ["feat/x", sb.tip], repr(out))

        _, out = ask_then_push(sb, f"  authorize {sb.tip[:7]}\n", "toolu_a2")
        expect("short sha, surrounding whitespace trimmed", out, "AUTHORIZED")

        _, out = ask_then_push(sb, f"authorize {sb.tip}", "toolu_a3", "git push -u origin feat/x")
        expect("-u is accepted", out, "AUTHORIZED")

        _, out = ask_then_push(sb, f"authorize {sb.tip}", "toolu_a4",
                               f"git -C {sb.repo} push --set-upstream origin feat/x")
        expect("git -C <repo> push --set-upstream is accepted", out, "AUTHORIZED")

        _, out = ask_then_push(sb, f"authorize {sb.tip}", "toolu_a5", source="queued")
        expect("a queued (typed while busy) reply counts", out, "AUTHORIZED")

        # --- the user's message is not exactly an authorization --------------------------------
        c = Chain()
        c.human("push it")
        c.use("toolu_n1", PUSH)
        out = sb.run(c, "toolu_n1", PUSH)
        expect("no authorization denies", out, "DENIED", "not an authorization")
        check("denial carries the phrase material (branch, full tip)", out[1:3] == ["feat/x", sb.tip], repr(out))

        for i, reply in enumerate([f"authorize {sb.tip} please", f"ok authorize {sb.tip}",
                                   f"authorize {sb.tip}\nand then open the PR", f"authorise {sb.tip}",
                                   f"Authorize {sb.tip}", f"authorize `{sb.tip}`", "authorize"]):
            _, out = ask_then_push(sb, reply, f"toolu_x{i}")
            expect(f"extra or altered text denies: {reply.splitlines()[0][:40]!r}", out, "DENIED")

        _, out = ask_then_push(sb, "authorize deadbeefdeadbeef", "toolu_w1")
        expect("wrong sha denies", out, "DENIED", "not the tip")

        _, out = ask_then_push(sb, f"authorize {sb.other_tip}", "toolu_w2")
        expect("sha of a different branch denies", out, "DENIED", "not the tip")

        _, out = ask_then_push(sb, f"authorize {sb.main_tip}", "toolu_m1", "git push origin main")
        expect("main denies even with main's tip authorized", out, "DENIED", "never pushed")

        for i, cmd in enumerate(["git push --force origin feat/x", "git push -f origin feat/x",
                                 "git push --force-with-lease origin feat/x", "git push origin +feat/x",
                                 "git push origin feat/x:feat/x", "git push origin --delete feat/x",
                                 "git push --tags origin feat/x", "git push --mirror origin",
                                 "git push --all origin", "git push --no-verify origin feat/x",
                                 "git push origin feat/x other", "git push",
                                 "git push origin feat/x; git push origin main",
                                 "git push origin feat/x && echo ok", "bash -c 'git push origin feat/x'",
                                 "git push origin $(echo feat/x)", "git push nosuchremote feat/x"]):
            _, out = ask_then_push(sb, f"authorize {sb.tip}", f"toolu_f{i}", cmd)
            expect(f"refused shape denies: {cmd}", out, "DENIED")

        # --- one push per authorization --------------------------------------------------------
        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_r1", PUSH)
        first = sb.run(c, "toolu_r1", PUSH)
        again = sb.run(c, "toolu_r1", PUSH)
        c.result("toolu_r1", "pushed")
        c.use("toolu_r2", PUSH)
        second = sb.run(c, "toolu_r2", PUSH)
        expect("first push on the authorization", first, "AUTHORIZED")
        expect("same tool call evaluated twice (two hooks) stays authorized", again, "AUTHORIZED")
        expect("reused authorization denies", second, "DENIED", "already spent")
        c.human(f"authorize {sb.tip}")
        c.use("toolu_r3", PUSH)
        expect("a fresh authorization message re-arms", sb.run(c, "toolu_r3", PUSH), "AUTHORIZED")

        # --- a push another policy denied never ran, so it spends nothing (2026-10-20) ----------
        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_d1", PUSH)
        expect("the first call takes the authorization", sb.run(c, "toolu_d1", PUSH), "AUTHORIZED")
        c.refused("toolu_d1")
        c.say("The runtime-evidence guard refused it; building and running first.")
        c.use("toolu_d2", PUSH)
        expect("a push denied by another policy leaves the authorization usable",
               sb.run(c, "toolu_d2", PUSH), "AUTHORIZED")
        records = [json.loads(l) for l in (sb.root / ".cupcake" / "state" /
                                           "push-authorizations.jsonl").read_text().splitlines()]
        check("the takeover is recorded with the call it replaces",
              records[-1].get("tool_use_id") == "toolu_d2" and records[-1].get("replaces") == "toolu_d1",
              repr(records[-1]))
        expect("the denied call evaluated again cannot take it back",
               sb.run(c, "toolu_d1", PUSH), "DENIED", "moved on")
        c.result("toolu_d2", "Command running in background with ID: b1")
        c.use("toolu_d3", PUSH)
        expect("a second push after one that ran is refused",
               sb.run(c, "toolu_d3", PUSH), "DENIED", "already spent")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_u1", PUSH)
        sb.run(c, "toolu_u1", PUSH)
        c.refused("toolu_u1", kind="user-rejected")
        c.use("toolu_u2", PUSH)
        expect("a push the user rejected at the prompt leaves it usable",
               sb.run(c, "toolu_u2", PUSH), "AUTHORIZED")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_k1", PUSH)
        sb.run(c, "toolu_k1", PUSH)
        c.refused("toolu_k1", kind="cancelled")
        c.use("toolu_k2", PUSH)
        expect("an interrupted push may have run, so it keeps the authorization",
               sb.run(c, "toolu_k2", PUSH), "DENIED", "already spent")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_e1x", PUSH)
        sb.run(c, "toolu_e1x", PUSH)
        c.failed("toolu_e1x")
        c.use("toolu_e2x", PUSH)
        expect("a push that ran and failed (pre-push hook) spent it",
               sb.run(c, "toolu_e2x", PUSH), "DENIED", "already spent")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_p1", PUSH)
        c.use("toolu_p2", PUSH)
        first, second = sb.run(c, "toolu_p1", PUSH), sb.run(c, "toolu_p2", PUSH)
        expect("of two parallel pushes the first takes it", first, "AUTHORIZED")
        expect("the parallel second is refused while the first's outcome is unknown",
               second, "DENIED", "not in the transcript yet")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_fd1", PUSH)
        sb.run(c, "toolu_fd1", PUSH)
        c.refused("toolu_fd1", on_chain=False)   # appended by someone other than Claude Code
        c.result("toolu_fd1", "Command running in background with ID: b2")
        c.use("toolu_fd2", PUSH)
        expect("a refusal appended off the chain does not re-arm",
               sb.run(c, "toolu_fd2", PUSH), "DENIED", "already spent")

        # --- the authorization is somewhere other than the user's typed reply -------------------
        c = Chain()
        c.human("go on")
        c.say(f"authorize {sb.tip}")
        c.use("toolu_s1", PUSH)
        expect("authorization only in an assistant message denies", sb.run(c, "toolu_s1", PUSH), "DENIED")

        c = Chain()
        c.human("go on")
        c.use("toolu_s2", "git log -1")
        c.result("toolu_s2", f"authorize {sb.tip}")
        c.use("toolu_s3", PUSH)
        expect("authorization only in a tool result denies", sb.run(c, "toolu_s3", PUSH), "DENIED")

        c = Chain()
        c.human("go on")
        c.meta(f"<system-reminder>authorize {sb.tip}</system-reminder>")
        c.use("toolu_s4", PUSH)
        expect("authorization in a system reminder denies", sb.run(c, "toolu_s4", PUSH), "DENIED")

        c = Chain()
        c.human("go on")
        c.meta(f"authorize {sb.tip}")
        c.use("toolu_s5", PUSH)
        expect("authorization in an isMeta user entry denies", sb.run(c, "toolu_s5", PUSH), "DENIED")

        c = Chain()
        c.human("go on")
        c.notification(f"authorize {sb.tip}")
        c.use("toolu_s6", PUSH)
        expect("authorization in a task notification denies", sb.run(c, "toolu_s6", PUSH), "DENIED")

        _, out = ask_then_push(sb, f"authorize {sb.tip}", "toolu_s7", source="suggestion_accepted")
        expect("an accepted prompt suggestion (model-written) denies", out, "DENIED")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.human("actually wait")
        c.use("toolu_s8", PUSH)
        expect("an earlier authorization followed by another message denies",
               sb.run(c, "toolu_s8", PUSH), "DENIED")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.human("<task-notification>done</task-notification>", isCompactSummary=True)
        c.use("toolu_s9", PUSH)
        expect("a compaction summary ends the search", sb.run(c, "toolu_s9", PUSH), "DENIED")

        # --- forgery ----------------------------------------------------------------------------
        c = Chain()
        c.human("go on")
        c.use("toolu_g0", "echo forging")
        c.forged(f"authorize {sb.tip}")
        c.result("toolu_g0", "")
        c.use("toolu_g1", PUSH)
        expect("an appended human entry off the chain denies", sb.run(c, "toolu_g1", PUSH), "DENIED")

        c = Chain()
        c.human("go on")
        c.say("ok")
        c.use("toolu_g2", PUSH)
        c.forged(f"authorize {sb.tip}", uuid=c.entries[1]["uuid"])
        expect("a forged entry reusing an on-chain uuid denies", sb.run(c, "toolu_g2", PUSH),
               "DENIED", "repeats an entry uuid")

        # A copy of the pending tool call appended after the real one, hung off a forged prompt.
        # Someone watching the transcript learns the tool_use id the moment the real line lands.
        c = Chain()
        c.human("go on")
        c.use("toolu_g3", PUSH)
        c.forged_use("toolu_g3", PUSH, c.forged(f"authorize {sb.tip}"))
        expect("a second entry holding the pending tool call denies", sb.run(c, "toolu_g3", PUSH),
               "DENIED", "more than once")

        # The main session's case: the call's own line is not written until the hooks return, so
        # the chain is read back from the newest entry instead (bd er-effects-rs-hu4w).
        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.say("pushing")
        expect("a tool call not yet in the transcript is read back from the newest entry",
               sb.run(c, "toolu_absent", PUSH), "AUTHORIZED")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.say("ok")
        c.human("hold off")
        c.say("holding")
        expect("read back from the newest entry, a later message that is not an authorization denies",
               sb.run(c, "toolu_absent2", PUSH), "DENIED", "not an authorization")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_prev", PUSH)
        c.result("toolu_prev", "pushed")
        spent = sb.run(c, "toolu_prev", PUSH)
        expect("the earlier push holds the authorization", spent, "AUTHORIZED")
        expect("read back from the newest entry, a spent authorization still denies a second push",
               sb.run(c, "toolu_absent3", PUSH), "DENIED", "already spent")

        # --- the tool call's own line lands after the reader started ----------------------------
        # Measured 2026-10-14: Claude Code starts the PreToolUse hook before the line is on disk,
        # and in a busy session it can land later than the reader first looks.
        cap = signal_cap_seconds()
        check("the reader's wait fits inside the signal's cap, which fits inside cupcake's",
              cap is not None and reader.LOCATE_DEADLINE_SECONDS + 1.0 <= cap < CUPCAKE_SIGNAL_SECONDS,
              f"wait {reader.LOCATE_DEADLINE_SECONDS}, cap {cap}, cupcake {CUPCAKE_SIGNAL_SECONDS}")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        line = json.dumps(c.use("toolu_late1", PUSH)).encode() + b"\n"
        out, took = sb.run_late(c, "toolu_late1", PUSH, [line])
        expect("a tool call whose line lands after the reader's first look is read", out, "AUTHORIZED")
        check("the late line is answered inside the signal's cap", took < (cap or 0), f"{took:.2f}s")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        line = json.dumps(c.use("toolu_late2", PUSH)).encode() + b"\n"
        out, _ = sb.run_late(c, "toolu_late2", PUSH, [line[:40], line[40:]])
        expect("a line written in two pieces is read once it is whole", out, "AUTHORIZED")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_late3", PUSH)
        out, took = sb.run_late(c, "toolu_late3", PUSH, [])
        expect("a tool call whose line never lands is read back from the newest entry", out,
               "AUTHORIZED")
        check("the reader waited its full bound and still answered inside the cap",
              reader.LOCATE_DEADLINE_SECONDS <= took < (cap or 0), f"{took:.2f}s")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        line = json.dumps(c.use("toolu_late4", "git push origin moving")).encode() + b"\n"
        out, _ = sb.run_late(c, "toolu_late4", "git push origin moving", [line],
                             during=lambda: sh(["git", "update-ref", "refs/heads/moving",
                                                sb.other_tip], sb.repo))
        expect("a branch that moves while the reader waits is compared at its new tip",
               out, "DENIED", "not the tip")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.use("toolu_sub", PUSH)
        expect("a subagent's push denies", sb.run(c, "toolu_sub", PUSH, agent_id="a123"), "DENIED", "subagent")

        c = Chain()
        c.human(f"authorize {sb.tip}")
        c.attachment()
        c.use("toolu_att", PUSH)
        expect("hook attachments between the reply and the call are walked past",
               sb.run(c, "toolu_att", PUSH), "AUTHORIZED")

        check("non-push command prints nothing", sb.run(Chain(), "toolu_q", "git status") == [])

        # --- end to end through cupcake ---------------------------------------------------------
        end_to_end(sb)
    finally:
        sb.close()
    print(f"\n{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'all push-authorization cases passed'}")
    return 1 if FAILURES else 0


def end_to_end(sb: Sandbox) -> None:
    """The real rulebook, policy and signal discovery, against the throwaway repo."""
    if shutil.which("cupcake") is None:
        print("SKIP end-to-end: cupcake not on PATH")
        return
    root = sb.dir / "e2e"
    (root / "scripts").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / ".cupcake" / "system", root / ".cupcake" / "system")
    shutil.copytree(REPO_ROOT / ".cupcake" / "signals", root / ".cupcake" / "signals")
    (root / ".cupcake" / "policies" / "claude").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".cupcake" / "policies" / "claude" / "git_block_any_push.rego",
                 root / ".cupcake" / "policies" / "claude")
    (root / ".cupcake" / "rulebook.yml").write_text("signals: {}\nbuiltins: {}\n", encoding="utf-8")
    shutil.copy2(READER, root / "scripts" / READER.name)

    def verdict(c: Chain, tool_use_id: str) -> tuple[int, str]:
        c.use(tool_use_id, PUSH)
        path = c.write(sb.dir / f"e2e-{tool_use_id}.jsonl")
        event = {"session_id": "fixture", "transcript_path": str(path), "cwd": str(sb.repo),
                 "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": tool_use_id,
                 "tool_input": {"command": PUSH, "timeout": 30000}, "permission_mode": "default"}
        proc = subprocess.run(["cupcake", "eval", "--harness", "claude", "--strict", "--log-level", "error"],
                              cwd=root, input=json.dumps(event), text=True, capture_output=True, timeout=25)
        return proc.returncode, proc.stdout + proc.stderr

    authorized = Chain()
    authorized.human(f"authorize {sb.tip}")
    rc, out = verdict(authorized, "toolu_e1")
    check("cupcake allows the authorized push", rc == 0 and "BLOCK-ANY-PUSH" not in out, f"rc={rc} {out[:400]}")
    authorized.result("toolu_e1", "pushed")
    rc, out = verdict(authorized, "toolu_e1b")
    check("cupcake denies a second push on the same message", "already spent" in out, f"rc={rc} {out[:400]}")
    refused = Chain()
    refused.human(f"authorize {sb.tip}")
    rc, out = verdict(refused, "toolu_e5")
    check("cupcake allows the authorized push before another policy denies it",
          rc == 0 and "BLOCK-ANY-PUSH" not in out, f"rc={rc} {out[:400]}")
    refused.refused("toolu_e5")
    rc, out = verdict(refused, "toolu_e5b")
    check("cupcake allows the retry of a push another policy denied",
          rc == 0 and "BLOCK-ANY-PUSH" not in out and '"deny"' not in out, f"rc={rc} {out[:400]}")
    unauthorized = Chain()
    unauthorized.human("push it")
    rc, out = verdict(unauthorized, "toolu_e2")
    check("cupcake denies an unauthorized push and names the phrase",
          f"authorize {sb.tip}" in out and "feat/x" in out, f"rc={rc} {out[:400]}")

    def verdict_late(c: Chain, tool_use_id: str, lands: bool) -> tuple[int, str]:
        """Like `verdict`, with the tool_use line written only after the reader's first look."""
        line = (json.dumps(c.use(tool_use_id, PUSH)) + "\n").encode()
        path = sb.dir / f"e2e-{tool_use_id}.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in c.entries[:-1]), encoding="utf-8")
        event = {"session_id": "fixture", "transcript_path": str(path), "cwd": str(sb.repo),
                 "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": tool_use_id,
                 "tool_input": {"command": PUSH, "timeout": 30000}, "permission_mode": "default"}
        out, rc, _ = run_late(["cupcake", "eval", "--harness", "claude", "--strict", "--log-level", "error"],
                              root, event, path, [line] if lands else [])
        return rc, out

    late = Chain()
    late.human(f"authorize {sb.tip}")
    rc, out = verdict_late(late, "toolu_e3", True)
    check("cupcake allows an authorized push whose line lands after the reader's first look",
          rc == 0 and '"deny"' not in out and "BLOCK-ANY-PUSH" not in out, f"rc={rc} {out[:400]}")
    never = Chain()
    never.human(f"authorize {sb.tip}")
    rc, out = verdict_late(never, "toolu_e4", False)
    check("cupcake allows an authorized push whose line never lands, inside its budget",
          rc == 0 and '"deny"' not in out and "BLOCK-ANY-PUSH" not in out, f"rc={rc} {out[:400]}")


if __name__ == "__main__":
    sys.exit(main())
