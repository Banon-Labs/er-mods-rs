#!/usr/bin/env python3
"""Open a kitty tab with a command pre-pasted for the user to run, and a comment saying why.

User directive 2026-10-13: a command the agent wants the user to run is never handed over as an
inline bash block. It goes into its own kitty tab, focused, typed in but not submitted, under a
`#` comment block that says why it should be run. One tab per command block.

    python3 scripts/kitty-user-command.py --title "push loading PR" \
        --why "The agent may not push; this publishes fix/load-character-from-file." \
        --why "Review the branch first: git log origin/main..fix/load-character-from-file" \
        -- 'git -C /abs/worktree push -u origin fix/load-character-from-file'

What lands in the tab, in order:

  * each `--why` line, prefixed `# ` -- the interactive tabs run fish, where a line starting with
    `#` is a comment, so it runs nothing and leaves the reason on screen;
  * the command, wrapped as `bash -c '<command>'`.

Both go in as one paste and nothing is submitted: the whole block waits at the prompt until the
user presses enter.

The wrap is there because the tabs run fish and the commands are written in bash: `$(...)`,
`&&` chains and `VAR=value cmd` all mean something different, or nothing, to fish. The quoting is
`shlex.quote`, whose `'"'"'` spelling for an embedded single quote parses identically in bash and
fish, so a title like "account's" survives both shells. `--raw` skips the wrap for a command that
is already fish.

The listener is `$KITTY_LISTEN_ON`, never a fixed port: `kitty.conf` says
`listen_on tcp:127.0.0.1:0`, so the port is chosen when kitty starts and changes every restart.
A hard-coded 45678 in the global instructions was refused with "connection refused" on 2026-10-13.

`--dry-run` prints exactly what would be sent. `--selftest` proves the wrap round-trips the
awkward characters through both bash and fish without opening anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

# Upper bounds on polls of kitty's own state, not waits: each loop returns the moment kitty reports
# the condition. A new tab's fish runs its config (fastfetch, direnv) before the first prompt, and
# text sent during that window is consumed by startup and never reaches the line editor -- measured
# 2026-10-13, when two handed-over tabs showed bare prompts with neither comment nor command.
SHELL_READY_DEADLINE_S = 20.0
SCREEN_DEADLINE_S = 5.0

# Characters a handed-over command actually carried on 2026-10-13: an apostrophe in a PR title,
# a `$(...)` substitution, double quotes, `&&`, a backslash and a dollar sign.
SELFTEST_COMMANDS = (
    "echo \"fix(er-quickload): a second Steam account's folder\"",
    "echo $(printf '%s' nested) && echo 'single \"double\" inside'",
    "printf '%s\\n' 'back\\slash' '$HOME stays literal'",
)


def wrap(command: str, raw: bool) -> str:
    """The text left at the prompt."""
    return command if raw else f"bash -c {shlex.quote(command)}"


def comment_lines(why: list[str]) -> list[str]:
    """The comment lines, one per reason line, each submitted on its own."""
    lines: list[str] = []
    for entry in why:
        for line in entry.splitlines() or [""]:
            lines.append(f"# {line}".rstrip())
    return lines


def pasted_block(comments: list[str], prompt_text: str) -> str:
    """The comments and the command as one block, comments first, nothing submitted."""
    return "\n".join([*comments, prompt_text])


def paste_into(listener: str, match: str, comments: list[str], prompt_text: str) -> None:
    """Paste the comments and the command as a single block and leave it at the prompt.

    One bracketed paste, so nothing can arrive out of order. fish keeps a pasted newline in the
    buffer instead of running it, and when the user presses enter the `#` lines are comments and
    the last line runs. Measured 2026-10-15: each comment used to be pasted and submitted on its
    own, with the next paste sent before fish had redrawn its prompt, and a tab came up as
    `bash -c '...'# <second comment>` -- the second comment landed after the command's closing
    quote. Keystrokes are no better: on 2026-10-13 the typed form lost the `'"'"'` quoting.
    """
    block = pasted_block(comments, prompt_text)
    kitty(listener, "send-text", f"--match={match}", "--bracketed-paste=enable", "--stdin", stdin=block)


def kitty(listener: str, *argv: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["kitty", "@", "--to", listener, *argv],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"kitty @ {argv[0]} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def listener_or_exit() -> str:
    listener = os.environ.get("KITTY_LISTEN_ON", "").strip()
    if not listener:
        raise SystemExit(
            "KITTY_LISTEN_ON is not set, so there is no kitty to talk to. kitty.conf uses a random "
            "port (listen_on tcp:127.0.0.1:0); run this from a shell kitty started."
        )
    return listener


def at_prompt(listener: str, match: str) -> bool:
    """kitty's shell integration marks each prompt, and `kitty @ ls` reports it per window."""
    listing = json.loads(kitty(listener, "ls", f"--match={match}"))
    return any(w.get("at_prompt") for os_window in listing for tab in os_window["tabs"] for w in tab["windows"])


def screen_text(listener: str, match: str) -> str:
    return kitty(listener, "get-text", f"--match={match}")


def poll(what: str, deadline_s: float, probe) -> None:
    """Ask kitty until it reports the condition. Each probe is a blocking `kitty @` round trip,
    which paces the loop; there is no sleep between them."""
    deadline = time.monotonic() + deadline_s
    while not probe():
        if time.monotonic() > deadline:
            raise SystemExit(f"gave up after {deadline_s:.0f}s waiting for {what}")


def launch_ready_tab(listener: str, title: str, cwd: str, keep_focus: bool) -> str:
    """Open a tab and return its match spec once its shell has drawn a prompt."""
    argv = ["launch", "--type=tab", f"--tab-title={title}", f"--cwd={cwd}"]
    if keep_focus:
        argv.append("--keep-focus")
    window_id = kitty(listener, *argv).strip()
    if not window_id.isdigit():
        raise SystemExit(f"kitty @ launch did not return a window id: {window_id!r}")
    match = f"id:{window_id}"
    poll(f"the first prompt in window {window_id}", SHELL_READY_DEADLINE_S, lambda: at_prompt(listener, match))
    return match


def without_whitespace(text: str) -> str:
    return "".join(text.split())


def pasted_landed(listener: str, match: str, comments: list[str], prompt_text: str) -> bool:
    """The whole block, in order, on screen.

    Compared without whitespace: a command longer than the window reads back split across lines,
    so a 359-character command came back as `--body` and `-file` on two lines and failed a plain
    substring test. Compared as one string rather than line by line, because the line-by-line
    check passed on 2026-10-15 for a tab whose second comment sat after the command.
    """
    screen = without_whitespace(screen_text(listener, match))
    return without_whitespace(pasted_block(comments, prompt_text)) in screen


def paste_and_confirm(listener: str, match: str, comments: list[str], prompt_text: str) -> None:
    paste_into(listener, match, comments, prompt_text)
    poll(
        "the comments and the command to appear on screen",
        SCREEN_DEADLINE_S,
        lambda: pasted_landed(listener, match, comments, prompt_text),
    )


def open_tab(title: str, cwd: str, comments: list[str], prompt_text: str) -> int:
    listener = listener_or_exit()
    match = launch_ready_tab(listener, title, cwd, keep_focus=False)
    paste_and_confirm(listener, match, comments, prompt_text)
    kitty(listener, "focus-window", f"--match={match}")
    window_id = int(match.removeprefix("id:"))
    print(json.dumps({"window_id": window_id, "title": title, "cwd": cwd, "pre_pasted": prompt_text, "on_screen": True}))
    return 0


def live_selftest() -> int:
    """Run the production paste path against a real fish tab and read back what fish executed.

    The offline selftest proves the wrap parses; it cannot prove the text arrives in the line
    editor intact, and on 2026-10-13 it did not, twice: first through keystroke typing, then
    because text sent before fish's first prompt was eaten by its startup. So this goes through
    the same launch, readiness wait, paste and on-screen check `open_tab` uses -- comments with an
    apostrophe, then a command whose arguments carry one -- and only then submits the command and
    compares the printed arguments. The scratch tab opens without taking focus and is closed
    afterwards, and the probe command only prints its own arguments.
    """
    listener = listener_or_exit()
    # The padding makes the command longer than any window is wide, so fish breaks it across
    # lines the way it broke the 359-character PR command. Its length is printed rather than the
    # padding itself, because a wrapped output line cannot be told from two lines on read-back.
    padding = "w" * 400
    probe = f"p=\"{padding}\"; printf '[%s]\\n' \"account's\" \"tail.md\" \"${{#p}}\" && echo '[done]'"
    expected = f"[account's]\n[tail.md]\n[{len(padding)}]\n[done]\n"
    comments = comment_lines(["why it's run", "second line"])
    match = launch_ready_tab(listener, "quote-probe", "/tmp", keep_focus=True)
    try:
        paste_and_confirm(listener, match, comments, wrap(probe, raw=False))
        # The block must still be waiting at the prompt: a pasted newline that ran the buffer
        # early would hand the user a command that had already gone.
        ran_early = "[done]" in screen_text(listener, match).splitlines()
        kitty(listener, "send-text", f"--match={match}", "--stdin", stdin="\r")
        poll(
            "the probe to finish",
            SCREEN_DEADLINE_S,
            lambda: "[done]" in screen_text(listener, match).splitlines(),
        )
        screen = screen_text(listener, match)
    finally:
        kitty(listener, "close-window", f"--match={match}")
    got = "".join(f"{line}\n" for line in screen.splitlines() if re.fullmatch(r"\[[^\]]*\]", line))
    comments_shown = "# why it's run" in screen and "# second line" in screen
    ok = got == expected and comments_shown and not ran_early
    print(
        f"{'ok  ' if ok else 'FAIL'} fish executed -> {got!r}; comments shown={comments_shown}; "
        f"ran before enter={ran_early}"
    )
    print("live selftest:", "PASS" if ok else "FAILED")
    return 0 if ok else 1


def selftest() -> int:
    failures = 0
    for command in SELFTEST_COMMANDS:
        expected = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, timeout=10, check=False
        ).stdout
        wrapped = wrap(command, raw=False)
        for shell in ("bash", "fish"):
            if shutil.which(shell) is None:
                print(f"SKIP {shell}: not on PATH")
                continue
            got = subprocess.run(
                [shell, "-c", wrapped], capture_output=True, text=True, timeout=10, check=False
            ).stdout
            ok = got == expected
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {shell:4} {command!r} -> {got!r}")
    lines = comment_lines(["first reason", "two\nlines"])
    ok = lines == ["# first reason", "# two", "# lines"]
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} comment lines {lines!r}")
    print("selftest:", "PASS" if failures == 0 else f"{failures} FAILED")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Open a kitty tab with a command pre-pasted (not run) under a '# why' comment."
    )
    parser.add_argument("--title", help="tab title")
    parser.add_argument(
        "--why",
        action="append",
        default=[],
        help="a reason line for the comment block; repeat for more lines (required)",
    )
    parser.add_argument("--cwd", default=os.path.expanduser("~"), help="tab working directory")
    parser.add_argument(
        "--command-file",
        help="read the command from this file instead of after --; for commands long enough that "
        "the policy engine runs out of memory evaluating the call that carries them",
    )
    parser.add_argument("--raw", action="store_true", help="paste the command as is, without bash -c")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent and exit")
    parser.add_argument("--selftest", action="store_true", help="check the quoting in bash and fish")
    parser.add_argument(
        "--live-selftest",
        action="store_true",
        help="open a real fish tab the way a handed-over tab is opened, and check what fish ran",
    )
    parser.add_argument("command", nargs="*", help="the command, after --")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.live_selftest:
        return live_selftest()
    if args.command_file and args.command:
        parser.error("give the command after -- or in --command-file, not both")
    if args.command_file:
        with open(args.command_file, encoding="utf-8") as handle:
            command = " ".join(handle.read().split("\n")).strip()
    else:
        command = " ".join(args.command).strip()
    if not command:
        parser.error("a command is required, after -- or in --command-file")
    if not args.why:
        parser.error("--why is required: the tab must say why the command should be run")
    if not args.title:
        parser.error("--title is required")

    comments = comment_lines(args.why)
    prompt_text = wrap(command, args.raw)
    if args.dry_run:
        print("\n".join([*comments, prompt_text]))
        return 0
    return open_tab(args.title, args.cwd, comments, prompt_text)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
