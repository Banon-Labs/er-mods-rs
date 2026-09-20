#!/usr/bin/env python3
"""A shell that serves the derived `05_010_ProfileSelect` movie must say whether it earns it.

# The regression this exists for

`er_quit_menu_core::gfx_swap` will not serve the derived stats-panel movie unless
`set_profile_05_010_edit_armed` has been told it may. The latch defaults to refusing, and that
default is right: the edit hides the face box, shifts the name and level left and recompacts five
156px rows into ten at 52px, so a host that serves it and then writes nothing into the space it
made puts a worse window on screen than the one the game ships.

On 2026-09-19 that latch gained exactly one caller, inside `arm::arm_standalone`. Every shell that
arms through it kept working. `er-save-game-row` hand-rolls its arm -- it installs the swap hook,
the row-populate detours and the picker cache key one by one -- so it asked for the movie and never
answered for it. Its destination browser opened in the game's own character presentation for the
rest of the day: no drive strip, no current-path bar, no last-saved time, and 8192 lines of
`stats-text: ... has no ErCharStats child -- not our ProfileSelect movie; left native` while the
player browsed.

Both halves built clean, both halves logged the refusal in plain words, and nothing failed. That is
the shape this gate closes: asking for the movie and answering for it are two calls, and a crate
may not make the first without making the second.

# The rule

A crate that calls `install_gfx_swap_hook_for` or `install_quit_menu_gfx_swap_hook` must also call
`set_profile_05_010_edit_armed` or `arm_standalone` somewhere in its own sources.

Deliberately coarse. Whether a particular `GfxServeSet` carries a profile-select bit is not
decidable from the call site -- every named constant in that module does carry one, and a
hand-built set would be a literal this would have to evaluate. Answering `false` is always
available and costs one line, so an over-strict rule here asks a host to state something it should
be stating anyway rather than to invent an exemption.

Definitions do not count as calls: `er-quit-menu-core` owns all four names, and a `fn` line
declaring one is not a host asking for anything.

    python3 scripts/check-profile-select-chrome-answered.py
    python3 scripts/check-profile-select-chrome-answered.py --selftest
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Asking for the movie.
ASKS = ("install_gfx_swap_hook_for", "install_quit_menu_gfx_swap_hook")

# Answering for it. `arm_standalone` counts because answering is the first thing it does, which is
# what makes every shell that arms through it safe without a line of its own.
ANSWERS = ("set_profile_05_010_edit_armed", "arm_standalone")

# A `fn` line declares a name; it does not ask for anything. `pub unsafe fn install_...` and
# `pub fn set_profile_05_010_edit_armed` are both caught by this.
DECLARATION = re.compile(r"\bfn\s+(\w+)")


def mentions(body: str, names: tuple[str, ...]) -> set[str]:
    """Which of `names` this source calls, ignoring the lines that declare them."""
    found: set[str] = set()
    for line in body.splitlines():
        declared = {match.group(1) for match in DECLARATION.finditer(line)}
        for name in names:
            if name in line and name not in declared:
                found.add(name)
    return found


def audit(crates_dir: pathlib.Path) -> list[str]:
    """One message per crate that asks without answering."""
    failures: list[str] = []
    for crate in sorted(p for p in crates_dir.iterdir() if p.is_dir()):
        asked: set[str] = set()
        answered: set[str] = set()
        for source in sorted(crate.rglob("*.rs")):
            try:
                body = source.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            asked |= mentions(body, ASKS)
            answered |= mentions(body, ANSWERS)
        if asked and not answered:
            failures.append(
                f"{crate.name}: calls {', '.join(sorted(asked))} but never calls "
                f"{' or '.join(ANSWERS)} -- it asks for the derived 05_010_ProfileSelect movie "
                f"without saying whether it fills the fields that edit makes room for, so the "
                f"latch refuses and the picker renders in the game's own character presentation"
            )
    return failures


def selftest() -> int:
    import tempfile

    failures = 0

    def ok(label: str, condition: bool) -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}")
        failures += 0 if condition else 1

    with tempfile.TemporaryDirectory() as tmp:
        crates = pathlib.Path(tmp) / "crates"

        def crate(name: str, body: str) -> None:
            src = crates / name / "src"
            src.mkdir(parents=True)
            (src / "lib.rs").write_text(body, encoding="utf-8")

        crate(
            "asks-without-answering",
            "fn arm() { gfx_swap::install_gfx_swap_hook_for(MOVIES); }\n",
        )
        crate(
            "asks-and-answers",
            "fn arm() {\n"
            "    gfx_swap::set_profile_05_010_edit_armed(true);\n"
            "    gfx_swap::install_gfx_swap_hook_for(MOVIES);\n"
            "}\n",
        )
        crate(
            "answers-through-the-shared-arm",
            "fn arm() { er_quit_menu_core::arm::arm_standalone(ROWS, ACTIONS); }\n",
        )
        crate("asks-nothing", "fn arm() { install_something_else(); }\n")
        # The owning crate declares all four names. A declaration is not a call, so a crate that
        # only declares the installers is not asking and is not judged for an answer.
        crate(
            "declares-them",
            "pub unsafe fn install_gfx_swap_hook_for(serve: GfxServeSet) -> bool { true }\n"
            "pub fn set_profile_05_010_edit_armed(armed: bool) {}\n",
        )

        named = {message.split(":", 1)[0] for message in audit(crates)}
        ok("a crate that asks without answering fails", "asks-without-answering" in named)
        ok("answering beside the ask passes", "asks-and-answers" not in named)
        ok("answering through arm_standalone passes", "answers-through-the-shared-arm" not in named)
        ok("a crate that asks for nothing passes", "asks-nothing" not in named)
        ok("declaring the installers is not asking", "declares-them" not in named)
        ok("nothing else was flagged", len(named) == 1)

    live = audit(REPO_ROOT / "crates")
    ok("this workspace passes its own gate", not live)
    for message in live:
        print(f"      {message}")

    print("selftest: PASS" if not failures else f"selftest: {failures} check(s) failed")
    return 0 if not failures else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument(
        "--crates-dir",
        default="",
        help="audit a tree other than this one -- how a checkout of the broken revision was "
        "confirmed to fail this gate rather than only a synthetic case in the selftest",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    failures = audit(pathlib.Path(args.crates_dir) if args.crates_dir else REPO_ROOT / "crates")
    for message in failures:
        print(f"profile-select-chrome: {message}")
    if failures:
        print(
            "\nAnswer the gate beside the install, with the same call arm_standalone makes:\n"
            "    gfx_swap::set_profile_05_010_edit_armed(\n"
            "        profile_select_chrome_gate::profile_select_chrome_required(\n"
            "            browse_rows_armed, host_dresses_character_rows,\n"
            "        ),\n"
            "    );\n"
            "`false` is a legitimate answer and means the window is left as the game built it."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
