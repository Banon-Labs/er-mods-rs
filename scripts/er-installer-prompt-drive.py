#!/usr/bin/env python3
"""Drive `er-installer`'s game-directory prompt over a real pseudo-terminal.

The prompt only appears when standard input is a terminal, which is deliberate: asking a
question of a pipe either hangs the script on the other end or gets answered by whatever the
pipe held next. That same gate makes the prompt invisible to an ordinary piped run, so the only
way to exercise it is to give the process a pty and type into it.

What this checks, in one run: that discovery failing prints where it looked, that the prompt
appears, that a wrong answer is refused with a reason and asked again, that a path pasted with
the quotation marks Explorer's "Copy as path" adds is accepted, and that the word `quit` leaves.

Usage:

    python3 scripts/er-installer-prompt-drive.py --selftest
    python3 scripts/er-installer-prompt-drive.py --exe target/debug/er-installer \\
        --home /tmp/empty-home 'C:\\somewhere\\wrong' '"/real/path/Game"'

`--selftest` builds its own Steam-shaped directories under a temporary root and runs the three
cases, so it needs nothing but a built binary. It builds one with cargo if `--exe` is not given.
"""

import argparse
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The prompt this drives, and the longest a run may take. Both are generous: the binary does no
# work between prompts, so anything near these means it is stuck rather than slow.
PROMPT = b"Elden Ring folder: "
RUN_TIMEOUT_SECONDS = 20
SETTLE_SECONDS = 0.2

# The repo's hard cap on any agent-run subprocess. An incremental build of one small crate is
# well under a second; a cold one is not, and is told to build separately rather than wait.
BUILD_TIMEOUT_SECONDS = 30


def run_with_answers(exe: Path, home: Path, answers: list[str]) -> tuple[str, int]:
    """Run the installer with `home` as the player's home, typing `answers` at each prompt.

    Returns everything the process wrote to its terminal, and its wait status.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("ME3_STEAM_DIR", None)
    # The full-screen picker is never reached here (`--none` chooses the whole set), but a dumb
    # terminal keeps the output plain if that ever changes.
    env["TERM"] = "dumb"

    pid, fd = pty.fork()
    if pid == 0:
        os.execve(str(exe), [str(exe), "--dry-run", "--none"], env)

    captured = bytearray()
    pending = list(answers)
    deadline = time.time() + RUN_TIMEOUT_SECONDS
    answered_at = 0.0
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.4)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            captured += chunk
        if (
            pending
            and captured.endswith(PROMPT)
            and time.time() - answered_at > SETTLE_SECONDS
        ):
            os.write(fd, pending.pop(0).encode() + b"\r")
            answered_at = time.time()

    os.close(fd)
    _, status = os.waitpid(pid, 0)
    if pending:
        raise SystemExit(f"the prompt never came back for: {pending}")
    return captured.decode(errors="replace"), status


def steam_tree(root: Path, install_dir: str) -> Path:
    """A Steam-shaped directory holding a stub game, and the `Game` directory inside it."""
    game = root / "steamapps" / "common" / install_dir / "Game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "eldenring.exe").write_bytes(b"stub")
    return game


def selftest(exe: Path) -> int:
    failures = []
    with tempfile.TemporaryDirectory(prefix="er-installer-prompt-") as temporary:
        root = Path(temporary)
        empty_home = root / "home"
        empty_home.mkdir()
        game = steam_tree(root / "library", "ELDEN RING")

        # A wrong answer, then the right one with the quotes Explorer adds.
        output, status = run_with_answers(
            exe, empty_home, [str(root / "nowhere"), f'"{game}"']
        )
        checks = {
            "says where it looked": "Could not find Elden Ring. Looked in:" in output,
            "asks for the folder": "Elden Ring folder:" in output,
            "names Copy as path": "Copy as path" in output,
            "refuses the wrong folder": "does not hold eldenring.exe" in output,
            "accepts the quoted path": f"Elden Ring: {game}" in output,
            "exits cleanly": status == 0,
        }
        for name, passed in checks.items():
            if not passed:
                failures.append(f"{name}: no")

        # Typing the word leaves, and leaving is not a failure.
        output, status = run_with_answers(exe, empty_home, ["quit"])
        if "Nothing installed." not in output:
            failures.append("quit says nothing installed: no")
        if status != 0:
            failures.append(f"quit exits cleanly: no ({status})")

    if failures:
        print("er-installer-prompt-drive selftest: " + str(len(failures)) + " failure(s)")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("er-installer-prompt-drive selftest: 0 failure(s)")
    return 0


def build_exe() -> Path:
    """Build the installer, within the repo's cap on how long any subprocess may run.

    A cold tree can take longer than the cap allows, and the cap is not negotiable, so a
    timeout here says to build first and pass `--exe` rather than raising the number.
    """
    built = REPO_ROOT / "target/debug/er-installer"
    try:
        subprocess.run(
            ["cargo", "build", "-p", "er-installer", "--bin", "er-installer"],
            cwd=REPO_ROOT,
            check=True,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"cargo build did not finish in {BUILD_TIMEOUT_SECONDS}s. Build it yourself and "
            "pass --exe target/debug/er-installer."
        ) from None
    return built


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--exe", type=Path, help="the installer to drive")
    parser.add_argument("--home", type=Path, help="what the run should see as HOME")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("answers", nargs="*", help="what to type at each prompt, in order")
    arguments = parser.parse_args()

    exe = arguments.exe
    if exe is None:
        if shutil.which("cargo") is None:
            parser.error("no --exe given and cargo is not on PATH")
        exe = build_exe()
    exe = exe.resolve()
    if not exe.is_file():
        parser.error(f"{exe} is not there -- build it with cargo build -p er-installer")

    if arguments.selftest:
        return selftest(exe)
    if arguments.home is None:
        parser.error("--home is required unless --selftest is given")
    output, status = run_with_answers(exe, arguments.home, arguments.answers)
    sys.stdout.write(output)
    print(f"\n[exit status {status}]")
    return 0 if status == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
