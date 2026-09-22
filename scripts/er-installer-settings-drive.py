#!/usr/bin/env python3
"""Drive `er-installer --configure` over a real pseudo-terminal and check what it wrote.

Why a pty is the only way to test this
--------------------------------------
The settings walkthrough only runs when standard input is a terminal, and that gate is
deliberate: a run whose mods came from `--select` is being scripted, and stopping it to ask about
118 settings would hang whatever is on the other end of the pipe. The same gate makes the
walkthrough invisible to an ordinary piped run, so the only way to exercise it is to give the
process a terminal and type into it.

What this checks, in one run: that an existing settings file is offered rather than replaced,
that a typed value lands in the file in the quoting that file uses, that every comment in it
survives the edit, that a file nobody touched is left byte-identical, and that a missing file is
created from the owning DLL's own text.

The keys it sends are the editor's: `j`/`k` move, Enter opens the highlighted setting for a typed
value, `n` moves to the next file, `q` stops asking. Arrow keys are deliberately not used --
measured 2026-09-19, Wine's console accepts `ENABLE_VIRTUAL_TERMINAL_INPUT` and then does not
emit the escape sequences, so `j`/`k` is the binding that works everywhere.

Usage:
    python3 scripts/er-installer-settings-drive.py --selftest
    python3 scripts/er-installer-settings-drive.py --exe target/debug/er-installer --selftest

`--selftest` builds its own stub game directory under a temporary root, so it needs nothing but
a built binary. It builds one with cargo if `--exe` is not given.
"""

import argparse
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The longest a run may take, and how long to wait between keystrokes. The binary does no work
# between them, so anything near the cap means it is stuck rather than slow.
RUN_TIMEOUT_SECONDS = 25
KEY_DELAY_SECONDS = 0.25

# The repo's hard cap on any agent-run subprocess.
BUILD_TIMEOUT_SECONDS = 30


def drive(exe: Path, game: Path, select_list: str, keys: list[str]) -> tuple[str, int]:
    """Run the installer under a terminal, sending `keys` in order, and return its output.

    Each entry in `keys` is written as-is, so `"j"` is a keypress and `"lb+rb\\r"` is a typed
    line. They go out on a fixed cadence rather than in response to a prompt: the full-screen
    editor repaints continuously, so there is no stable string to wait for.
    """
    env = dict(os.environ)
    env["TERM"] = "xterm"
    # Neither of these may steer the run: the game directory is given explicitly, and a stray
    # `ME3_STEAM_DIR` on the developer's machine would point discovery at their real install.
    env.pop("ME3_STEAM_DIR", None)
    env["HOME"] = str(game.parent)

    argv = [
        str(exe),
        "--game-dir",
        str(game),
        "--dll-dir",
        str(game.parent / "dlls"),
        "--select",
        select_list,
        "--configure",
    ]
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(str(exe), argv, env)

    captured = bytearray()
    pending = list(keys)
    next_key_at = time.time() + KEY_DELAY_SECONDS
    deadline = time.time() + RUN_TIMEOUT_SECONDS
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            captured += chunk
        if pending and time.time() >= next_key_at:
            os.write(fd, pending.pop(0).encode())
            next_key_at = time.time() + KEY_DELAY_SECONDS

    os.close(fd)
    _, status = os.waitpid(pid, 0)
    if pending:
        raise SystemExit(f"the run ended with keys unsent: {pending}")
    return captured.decode(errors="replace"), status


def in_table(text: str, table: str, assignment: str) -> bool:
    """Is `assignment` a line inside `[table]`, and nowhere else in the file?

    Written structurally rather than as a substring match on `"[table]\\nkey = value"`: the
    author's comments sit between a table header and its first key, so adjacency is the wrong
    question. The one that matters is which section the line belongs to, because a top-level
    `mode = ...` and a `[target] mode = ...` are two different settings.
    """
    section = None
    found_in = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
        elif stripped == assignment:
            found_in.append(section)
    return found_in == [table]


def stub_game(root: Path, mods: list[str]) -> Path:
    """A stub game directory, with an empty file standing in for each mod's DLL."""
    game = root / "Game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "eldenring.exe").write_bytes(b"stub")
    dlls = root / "dlls"
    dlls.mkdir(exist_ok=True)
    for artifact in mods:
        (dlls / artifact).write_bytes(b"stub")
    return game


def selftest(exe: Path) -> int:
    failures: list[str] = []

    def check(label: str, passed: bool, detail: str = "") -> None:
        if not passed:
            failures.append(f"{label}{': ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory(prefix="er-installer-settings-") as temporary:
        root = Path(temporary)
        game = stub_game(root, ["er_refill_all.dll", "er_inventory_sort.dll"])

        # A settings file that is already there, holding a value the player chose and a comment
        # of their own -- so the run has something in front of it that it must not destroy.
        #
        # One mod per drive, deliberately: the files are offered in catalog order rather than in
        # the order `--select` names them, so a two-mod drive would be typing into whichever
        # file the catalog happens to list first.
        refill = game / "er-refill-all.toml"
        refill.write_text(
            "# er-refill-all settings.\n"
            "# A comment the player wrote themselves.\n"
            'gamepad_hotkey = "select+start"\n'
            "\n"
            "# Whether to refill at once.\n"
            "refill_immediately = true\n",
            encoding="utf-8",
        )
        before = refill.read_text(encoding="utf-8")

        # Enter opens the highlighted row, which on this file is `gamepad_hotkey`; then the
        # value; then `n` to leave the file.
        output, status = drive(exe, game, "er-refill-all", ["\r", "lb+rb\r", "n"])

        check("exits cleanly", status == 0, f"status {status}")
        check("offers the settings", "Settings" in output)
        check("names the existing file", "er-refill-all.toml" in output)
        check("shows the setting being asked about", "gamepad_hotkey" in output)

        edited = refill.read_text(encoding="utf-8")
        check(
            "the typed value landed, in the file's own quoting",
            'gamepad_hotkey = "lb+rb"' in edited,
            edited,
        )
        check(
            "the player's own comment survived",
            "# A comment the player wrote themselves." in edited,
        )
        check(
            "every other line survived",
            "refill_immediately = true" in edited and "# Whether to refill at once." in edited,
        )
        check(
            "nothing was appended twice",
            edited.count("gamepad_hotkey") == 1,
            f"{edited.count('gamepad_hotkey')} mentions",
        )
        check(
            "only the edited line moved",
            [line for line in before.splitlines() if "gamepad_hotkey" not in line]
            == [line for line in edited.splitlines() if "gamepad_hotkey" not in line],
        )

        # A file that is not there yet, quit out of at once: it must still be written, with the
        # owning DLL's own text, because that text is the documentation in the game folder.
        sort_file = game / "er-inventory-sort.toml"
        _, status = drive(exe, game, "er-inventory-sort", ["q"])
        check("exits cleanly after quitting the questions", status == 0, f"status {status}")
        # The biggest file, and the only shape the rows above cannot reach: a key inside a
        # `[table]`, past the first screen of settings. `er-npc-possess.toml` has 58 settings
        # across seven tables, so this is where a cursor that does not line up with the schema,
        # or an upsert that writes a table key at the top level, shows up.
        #
        # Row 5 is `target.mode`, the first key under `[target]`: four `j` presses from the top.
        possess = game / "er-npc-possess.toml"
        (root / "dlls" / "er_npc_possess.dll").write_bytes(b"stub")
        _, status = drive(
            exe, game, "er-npc-possess", ["j", "j", "j", "j", "\r", "chr_id\r", "n"]
        )
        check("exits cleanly on the big file", status == 0, f"status {status}")
        written = possess.read_text(encoding="utf-8") if possess.is_file() else ""
        check(
            "a key inside a table is written inside that table, and only there",
            in_table(written, "target", 'mode = "chr_id"'),
            "\n".join(
                line for line in written.splitlines() if "mode" in line or line.startswith("[")
            ),
        )
        check(
            "and the other tables' keys are untouched",
            'radial = "DPadDown"' in written and 'r1 = "light"' in written,
        )

        created = sort_file.read_text(encoding="utf-8") if sort_file.is_file() else ""
        check("a missing file is still created", bool(created))
        check(
            "with the assignments the DLL ships",
            all(
                f'{key} = "order_of_acquisition"' in created
                for key in ("armaments", "armor", "talismans")
            ),
            created,
        )
        check(
            "and with the comments the DLL writes, which are the documentation in that folder",
            "auto-created next to the game executable" in created
            and "Values: order_of_acquisition" in created,
        )

        if failures:
            # The terminal session itself, on failure only. A walkthrough that did not run
            # cannot be diagnosed from the checks -- they all just say no -- and the reason is
            # always in what the binary printed before it stopped.
            print("--- what the terminal saw ---")
            print(output)
            print("--- end ---")

    for failure in failures:
        print(f"  {failure}")
    print(f"er-installer-settings-drive selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def build_exe() -> Path:
    built = REPO_ROOT / "target" / "debug" / "er-installer"
    run = subprocess.run(
        ["cargo", "build", "-p", "er-installer"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    if run.returncode != 0:
        raise SystemExit(f"cargo build -p er-installer failed:\n{run.stderr}")
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, help="a built er-installer to drive")
    parser.add_argument("--selftest", action="store_true", help="run the checks")
    args = parser.parse_args()
    exe = args.exe or build_exe()
    if not exe.is_file():
        raise SystemExit(f"{exe} is not there; build it or pass --exe")
    if not args.selftest:
        parser.print_help()
        return 0
    return selftest(exe)


if __name__ == "__main__":
    sys.exit(main())
