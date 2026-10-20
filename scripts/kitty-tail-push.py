#!/usr/bin/env python3
"""Show a running `git push` in a new kitty tab, and close the tab 5 seconds after it ends.

User request 2026-10-19: "tail pushes in a new kitty tab until they complete, where terminal will
exit after a 5 second delay". A push here runs the pre-push hook's whole check suite, ten minutes
or more, and the agent runs it as a background task whose output only the agent could see.

    python3 scripts/kitty-tail-push.py --log <the background task's output file>
    python3 scripts/kitty-tail-push.py --log <file> --pid <git push pid>
    python3 scripts/kitty-tail-push.py --verdict <file>     # what the tab prints at the end
    python3 scripts/kitty-tail-push.py --selftest

Without `--pid`, the newest `git ... push` process this user owns is the one followed, found by
reading `/proc` (raw `pgrep` is guard-denied here). The tab runs `tail --pid`, so it follows the
log exactly as long as that process lives -- the end of the push is the event, not a timer. It
then prints `PUSH SUCCEEDED` or `PUSH FAILED` from git's own output, waits the 5 seconds the user
asked for so the verdict can be read, and exits, which closes the tab.

kitty is reached through `$KITTY_LISTEN_ON`, as in scripts/kitty-user-command.py: kitty.conf
listens on a random port.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

CLOSE_DELAY_SECONDS = 5

# git's summary line for a ref it updated: ` * [new branch]  a -> a`, or `   1a2b3c4..5d6e7f8  a -> a`.
# A fast-forward or a new branch is the only shape a push here can take: force is guard-denied.
UPDATED_REF = re.compile(r"^ (\* \[new branch\]|  [0-9a-f]{7,}\.\.[0-9a-f]{7,}) ", re.MULTILINE)
REJECTED = re.compile(r"^error: failed to push|^ ! \[", re.MULTILINE)


def verdict(text: str) -> str:
    if UPDATED_REF.search(text) and not REJECTED.search(text):
        return "PUSH SUCCEEDED"
    return "PUSH FAILED"


def newest_push_pid() -> int | None:
    """The most recently started `git ... push` process this user owns, or None."""
    uid = os.getuid()
    best: tuple[int, int] | None = None
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            started = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            continue
        words = [Path(a.decode(errors="replace")).name for a in argv if a]
        if not words or words[0] != "git" or "push" not in words[1:]:
            continue
        if best is None or started > best[0]:
            best = (started, int(entry.name))
    return best[1] if best else None


# The tab paints the log as it reads it (user, 2026-10-19: "Our skill doesn't provide color output
# for the push in the kitty tab?"). A push's output goes to a file, so neither git nor check.sh
# colours it, and check.sh writes its stage verdicts as plain words even on a terminal. Painting in
# the viewer covers every producer at once. Order matters: a line that is red is red even if it also
# says "ok" somewhere.
RED_RE = re.compile(
    r"\bRED\b|\bFAIL(?:ED|URE)?\b|\bREFUS(?:ED|ING)\b|\berror\b|\[rejected\]|PUSH FAILED|Traceback"
)
YELLOW_RE = re.compile(r"\bSKIPPED\b|\bINCONCLUSIVE\b|\bNOT RUN\b|\bwarning\b")
GREEN_RE = re.compile(r"\bgreen\b|\bPASS(?:ED)?\b|\bpassed\b|^\s*ok\b|\bOK\b|PUSH SUCCEEDED|\[new branch\]|\.\.[0-9a-f]{7,}\s")
PAINT = {"red": "\033[31m", "yellow": "\033[33m", "green": "\033[32m"}
BOLD, RESET = "\033[1m", "\033[0m"


def paint(line: str) -> str:
    """One log line with the colour its verdict words call for; stage lines are also bold."""
    colour = next(
        (PAINT[name] for name, rx in (("red", RED_RE), ("yellow", YELLOW_RE), ("green", GREEN_RE)) if rx.search(line)),
        "",
    )
    bold = BOLD if line.startswith(">>> ") else ""
    return f"{bold}{colour}{line}{RESET}" if colour or bold else line


def tab_script(log: Path, pid: int | None) -> str:
    me = shlex.quote(str(Path(__file__).resolve()))
    quoted = shlex.quote(str(log))
    follow = f"tail -n +1 --pid={pid} -f {quoted}" if pid else f"cat {quoted}"
    return "\n".join(
        [
            f"{follow} | python3 -u {me} --paint",
            "echo",
            f"python3 {me} --verdict {quoted} | python3 -u {me} --paint",
            f"echo 'closing in {CLOSE_DELAY_SECONDS}s'",
            # The delay the user asked for, so the verdict stays on screen long enough to read.
            f"sleep {CLOSE_DELAY_SECONDS}",
        ]
    )


def open_tab(log: Path, pid: int | None, title: str, focus: bool) -> int:
    listener = os.environ.get("KITTY_LISTEN_ON", "").strip()
    if not listener:
        print(
            "KITTY_LISTEN_ON is not set, so there is no kitty to talk to; run this from a shell kitty started.",
            file=sys.stderr,
        )
        return 2
    argv = ["kitty", "@", "--to", listener, "launch", "--type=tab", f"--tab-title={title}"]
    if not focus:
        argv.append("--keep-focus")
    argv += ["bash", "-c", tab_script(log, pid)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0:
        print(f"kitty @ launch failed ({result.returncode}): {result.stderr.strip()}", file=sys.stderr)
        return 1
    following = f"pid {pid}" if pid else "no live push process; showing the finished log"
    print(f"opened kitty tab {title!r} (window {result.stdout.strip()}), following {log} ({following})")
    return 0


def selftest() -> int:
    new_branch = "To github.com:Banon-Labs/er-mods-rs.git\n * [new branch]      fix/x -> fix/x\n"
    fast_forward = "To github.com:Banon-Labs/er-mods-rs.git\n   1a2b3c4..5d6e7f8  fix/x -> fix/x\n"
    hook_failed = ">>> stage lint  red\nerror: failed to push some refs to 'github.com:x/y.git'\n"
    rejected = " ! [rejected]        fix/x -> fix/x (fetch first)\nerror: failed to push some refs\n"
    cases = [
        (new_branch, "PUSH SUCCEEDED"),
        (fast_forward, "PUSH SUCCEEDED"),
        (hook_failed, "PUSH FAILED"),
        (rejected, "PUSH FAILED"),
        ("", "PUSH FAILED"),
    ]
    failures = [(text, want, verdict(text)) for text, want in cases if verdict(text) != want]
    for text, want, got in failures:
        print(f"FAIL verdict: wanted {want}, got {got} for {text!r}")
    with tempfile.NamedTemporaryFile("w", suffix=".log") as log:
        for pid in (None, 4242):
            check = subprocess.run(["bash", "-n", "-c", tab_script(Path(log.name), pid)],
                                   capture_output=True, text=True, timeout=10)
            if check.returncode != 0:
                failures.append(("tab script", "parses", check.stderr))
                print(f"FAIL tab script for pid={pid} does not parse: {check.stderr.strip()}")
    painted = [
        (">>> stage product        RED (exit 1)    107s   /run/x/product.log", PAINT["red"]),
        (">>> stage docs           green             2s   /run/x/docs.log", PAINT["green"]),
        ("  FAILED        line 2496  python3 x.py", PAINT["red"]),
        ("  ok    the refusal names the file", PAINT["green"]),
        ("  SKIPPED       line 2206  needs image-1162", PAINT["yellow"]),
        ("PUSH SUCCEEDED", PAINT["green"]),
        ("PUSH FAILED", PAINT["red"]),
    ]
    for line, colour in painted:
        if not paint(line).startswith((colour, BOLD + colour)):
            failures.append(("paint", colour, line))
            print(f"FAIL paint: {line!r} did not get {colour!r}")
    if paint("cache /home/x/r3-boards/abc") != "cache /home/x/r3-boards/abc":
        failures.append(("paint", "plain", "neutral line"))
        print("FAIL paint: a line with no verdict word was coloured")
    script = tab_script(Path("/tmp/a log with spaces.txt"), 7)
    if "'/tmp/a log with spaces.txt'" not in script or "--pid=7" not in script:
        failures.append(("quoting", "quoted", script))
        print(f"FAIL the log path is not quoted or the pid is missing:\n{script}")
    print("selftest:", "FAIL" if failures else "ok")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", type=Path, help="the push's output file (a background task's output)")
    parser.add_argument("--pid", type=int, help="the git push process; default: the newest one this user owns")
    parser.add_argument("--title", default="git push", help="the tab's title")
    parser.add_argument("--focus", action="store_true", help="switch to the new tab instead of staying put")
    parser.add_argument("--verdict", type=Path, metavar="LOG", help="print PUSH SUCCEEDED/FAILED for a log and exit")
    parser.add_argument("--paint", action="store_true", help="colour stdin's verdict words onto stdout (the tab's filter)")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.paint:
        for line in sys.stdin:
            sys.stdout.write(paint(line.rstrip("\n")) + "\n")
            sys.stdout.flush()
        return 0
    if args.verdict:
        text = args.verdict.read_text(errors="replace") if args.verdict.exists() else ""
        print(verdict(text))
        return 0
    if not args.log:
        parser.error("--log is required")
    return open_tab(args.log, args.pid or newest_push_pid(), args.title, args.focus)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
