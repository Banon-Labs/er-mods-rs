#!/usr/bin/env python3
"""Open a kitty split with a command pre-pasted for the user to run, and a comment saying why.

User directive 2026-10-13: a command the agent wants the user to run is never handed over as an
inline bash block. It goes into its own kitty window, focused, typed in but not submitted, under a
`#` comment block that says why it should be run. One window per command block.

    python3 scripts/kitty-user-command.py --title "push loading PR" \
        --why "The agent may not push; this publishes fix/load-character-from-file." \
        --why "Review the branch first: git log origin/main..fix/load-character-from-file" \
        -- 'git -C /abs/worktree push -u origin fix/load-character-from-file'

The window is a split: a new kitty window in the tab the agent session runs in (user request
2026-11-07: "Can we do kitty split instead of tab by default?"). `--tab` opens a new tab instead,
as every handed-over command did before. `placement` below says how the split finds that tab.

What lands in the window, in order:

  * each `--why` line, prefixed `# ` -- the interactive shells run fish, where a line starting with
    `#` is a comment, so it runs nothing and leaves the reason on screen;
  * the command, wrapped as `bash -c '<command>'`.

Both go in as one paste and nothing is submitted: the whole block waits at the prompt until the
user presses enter.

The wrap is there because the shells run fish and the commands are written in bash: `$(...)`,
`&&` chains and `VAR=value cmd` all mean something different, or nothing, to fish. The quoting is
`shlex.quote`, whose `'"'"'` spelling for an embedded single quote parses identically in bash and
fish, so a title like "account's" survives both shells. `--raw` skips the wrap for a command that
is already fish.

The listener is `$KITTY_LISTEN_ON`, never a fixed port: `kitty.conf` says
`listen_on tcp:127.0.0.1:0`, so the port is chosen when kitty starts and changes every restart.
A hard-coded 45678 in the global instructions was refused with "connection refused" on 2026-10-13.

`--dry-run` prints exactly what would be sent. `--selftest` proves the wrap round-trips the
awkward characters through both bash and fish without opening anything. `--live-selftest` (with
`--tab` for a tab) opens a real window the same way and also checks which tab it landed in.
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
# the condition. A new window's fish runs its config (fastfetch, direnv) before the first prompt, and
# text sent during that time is consumed by startup and never reaches the line editor -- measured
# 2026-10-13, when two handed-over tabs showed bare prompts with neither comment nor command.
SHELL_READY_DEADLINE_S = 20.0
SCREEN_DEADLINE_S = 5.0

#: What a helper opens: a split (a new window in the agent's own tab) by default, or a tab.
SPLIT = "split"
TAB = "tab"


def agent_window_id() -> str | None:
    """The kitty window this agent session runs in: `$KITTY_WINDOW_ID`, which kitty exports to the
    shells it starts. None when it is unset or not a number."""
    value = os.environ.get("KITTY_WINDOW_ID", "").strip()
    return value if value.isdigit() else None


def placement(kind: str, title: str) -> tuple[list[str], str]:
    """The `kitty @ launch` options that open the new window as `kind`, and where it lands, in words.

    A split is `--type=window`, a new window in an existing tab. Which tab is `--match`, a tab
    match for a launch: `window_id:<n>` is the tab holding window n, where `id:<n>` would look for a
    tab numbered n first. `--next-to` puts the new window beside the agent's own, and kitty ignores
    it unless that window is in the target tab (`kitty @ launch --help`, kitty 0.49.2), so both are
    given. `--location=split` splits the agent's window along its longer side in the `splits`
    layout; every other layout, including `fat`, the one the user's tabs start in, places the new
    window after it (kitty/layout/base.py). Without `$KITTY_WINDOW_ID` the split goes to the
    focused tab, and the words returned say so.
    """
    if kind == TAB:
        return ["--type=tab", f"--tab-title={title}"], "a new tab"
    argv = ["--type=window", "--location=split", f"--window-title={title}"]
    agent = agent_window_id()
    if agent is None:
        return argv, "the focused tab: KITTY_WINDOW_ID is not set, so the agent's own tab is unknown"
    return [*argv, f"--match=window_id:{agent}", f"--next-to=id:{agent}"], (
        f"the agent's tab, next to window {agent}"
    )


def tab_ids(listener: str, window_ids: list[int]) -> dict[int, int]:
    """Window id to tab id, for these windows only. `kitty @ ls` is matched to them, and only ids
    are read from it, so no other window's title or command line is listed."""
    query = " or ".join(f"id:{w}" for w in window_ids)
    listing = json.loads(kitty(listener, "ls", f"--match={query}"))
    wanted = set(window_ids)
    return {
        w["id"]: tab["id"]
        for os_window in listing
        for tab in os_window["tabs"]
        for w in tab["windows"]
        if w["id"] in wanted
    }

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


def launch_ready_window(listener: str, title: str, cwd: str, keep_focus: bool, kind: str) -> tuple[str, str]:
    """Open a split or a tab, and return its match spec and where it landed once its shell has
    drawn a prompt."""
    where_argv, where = placement(kind, title)
    argv = ["launch", *where_argv, f"--cwd={cwd}"]
    if keep_focus:
        argv.append("--keep-focus")
    window_id = kitty(listener, *argv).strip()
    if not window_id.isdigit():
        raise SystemExit(f"kitty @ launch did not return a window id: {window_id!r}")
    match = f"id:{window_id}"
    poll(f"the first prompt in window {window_id}", SHELL_READY_DEADLINE_S, lambda: at_prompt(listener, match))
    return match, where


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


def open_window(title: str, cwd: str, comments: list[str], prompt_text: str, kind: str) -> int:
    listener = listener_or_exit()
    match, where = launch_ready_window(listener, title, cwd, keep_focus=False, kind=kind)
    paste_and_confirm(listener, match, comments, prompt_text)
    kitty(listener, "focus-window", f"--match={match}")
    window_id = int(match.removeprefix("id:"))
    print(json.dumps({
        "window_id": window_id, "window_type": kind, "placement": where, "title": title, "cwd": cwd,
        "pre_pasted": prompt_text, "on_screen": True,
    }))
    return 0


def placement_check(listener: str, new_id: int, kind: str) -> tuple[bool | None, str]:
    """Whether the new window is where `kind` puts it: a split in the agent window's tab, a tab
    anywhere else. None when there is no agent window to compare with."""
    agent = agent_window_id()
    if agent is None:
        return None, "placement not checked: KITTY_WINDOW_ID is not set"
    tabs = tab_ids(listener, [int(agent), new_id])
    same = tabs.get(int(agent)) is not None and tabs.get(int(agent)) == tabs.get(new_id)
    ok = same if kind == SPLIT else not same
    return ok, (
        f"new window {new_id} is in tab {tabs.get(new_id)}, agent window {agent} in tab "
        f"{tabs.get(int(agent))}"
    )


def live_selftest(kind: str) -> int:
    """Run the production paste path against a real fish window and read back what fish executed.

    The offline selftest proves the wrap parses; it cannot prove the text arrives in the line
    editor intact, and on 2026-10-13 it did not, twice: first through keystroke typing, then
    because text sent before fish's first prompt was eaten by its startup. So this goes through
    the same launch, readiness wait, paste and on-screen check `open_window` uses -- comments with
    an apostrophe, then a command whose arguments carry one -- and only then submits the command
    and compares the printed arguments. It also reads which tab the window opened in: a split must
    share the agent window's tab, a tab must not. The scratch window opens without taking focus and
    is closed afterwards, and the probe command only prints its own arguments.
    """
    listener = listener_or_exit()
    # The padding makes the command longer than any window is wide, so fish breaks it across
    # lines the way it broke the 359-character PR command. Its length is printed rather than the
    # padding itself, because a wrapped output line cannot be told from two lines on read-back.
    padding = "w" * 400
    probe = f"p=\"{padding}\"; printf '[%s]\\n' \"account's\" \"tail.md\" \"${{#p}}\" && echo '[done]'"
    expected = f"[account's]\n[tail.md]\n[{len(padding)}]\n[done]\n"
    comments = comment_lines(["why it's run", "second line"])
    match, _ = launch_ready_window(listener, "quote-probe", "/tmp", keep_focus=True, kind=kind)
    try:
        placed, placed_words = placement_check(listener, int(match.removeprefix("id:")), kind)
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
    expect = "the agent window's tab" if kind == SPLIT else "a tab of its own"
    print(f"{'FAIL' if placed is False else 'ok  '} {kind} in {expect}: {placed_words}")
    ok = ok and placed is not False
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
    failures += placement_selftest()
    print("selftest:", "PASS" if failures == 0 else f"{failures} FAILED")
    return 0 if failures == 0 else 1


def placement_selftest() -> int:
    """The launch options for a split with and without `$KITTY_WINDOW_ID`, and for a tab."""
    saved = os.environ.get("KITTY_WINDOW_ID")
    cases = [
        ("7", SPLIT, ["--type=window", "--location=split", "--window-title=t",
                      "--match=window_id:7", "--next-to=id:7"], "the agent's tab, next to window 7"),
        (None, SPLIT, ["--type=window", "--location=split", "--window-title=t"], "the focused tab"),
        ("7", TAB, ["--type=tab", "--tab-title=t"], "a new tab"),
    ]
    failures = 0
    try:
        for env, kind, want_argv, want_words in cases:
            if env is None:
                os.environ.pop("KITTY_WINDOW_ID", None)
            else:
                os.environ["KITTY_WINDOW_ID"] = env
            got_argv, words = placement(kind, "t")
            ok = got_argv == want_argv and words.startswith(want_words)
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {kind} with KITTY_WINDOW_ID={env} -> {got_argv} ({words})")
    finally:
        if saved is None:
            os.environ.pop("KITTY_WINDOW_ID", None)
        else:
            os.environ["KITTY_WINDOW_ID"] = saved
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Open a kitty split with a command pre-pasted (not run) under a '# why' comment."
    )
    parser.add_argument("--title", help="window title (the tab title with --tab)")
    parser.add_argument(
        "--tab",
        action="store_true",
        help="open a new tab instead of a split in the agent's own tab",
    )
    parser.add_argument(
        "--why",
        action="append",
        default=[],
        help="a reason line for the comment block; repeat for more lines (required)",
    )
    parser.add_argument("--cwd", default=os.path.expanduser("~"), help="the shell's working directory")
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
        help="open a real fish window the way a handed-over one is opened, and check what fish ran "
        "and which tab it landed in",
    )
    parser.add_argument("command", nargs="*", help="the command, after --")
    args = parser.parse_args(argv)
    kind = TAB if args.tab else SPLIT

    if args.selftest:
        return selftest()
    if args.live_selftest:
        return live_selftest(kind)
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
        parser.error("--why is required: the window must say why the command should be run")
    if not args.title:
        parser.error("--title is required")

    comments = comment_lines(args.why)
    prompt_text = wrap(command, args.raw)
    if args.dry_run:
        where_argv, where = placement(kind, args.title)
        print(f"# kitty @ launch {shlex.join([*where_argv, f'--cwd={args.cwd}'])}  ({kind}: {where})")
        print("\n".join([*comments, prompt_text]))
        return 0
    return open_window(args.title, args.cwd, comments, prompt_text, kind)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
