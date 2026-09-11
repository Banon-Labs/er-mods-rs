#!/usr/bin/env python3
"""Count what the quit-menu files reach for through `use crate::*`.

The System>Quit modules under `crates/er-quickload/src/experiments/startup_hooks/quit_menu/`
open with `use crate::*`, so every root-crate `fn`, `static`, `const` and type they name is a
cross-call that has to become a `QuitMenuHost` field, move with the code, or be deleted before
the file can live in `er-quit-menu-core`. This script produces that list.

Method: collect every definition in `crates/er-quickload/src/**/*.rs` outside the quit_menu
tree, then intersect it with the identifiers each moving file mentions, comments and string
literals stripped. The intersection is an upper bound -- a name can collide with a local
binding -- so the output is a worklist to classify by hand, not a verdict.

Usage:
    python3 scripts/audit-quit-menu-seam.py build   # the build-rows subset
    python3 scripts/audit-quit-menu-seam.py all9    # every file the plan lists
    python3 scripts/audit-quit-menu-seam.py build --markdown
"""

import collections
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "crates", "er-quickload", "src")
QUIT_MENU = os.path.join(ROOT, "experiments", "startup_hooks", "quit_menu")

# the build-rows subset: what "Load Build from URL" and "Generate Build Link" stand on.
MOVING_BUILD_ROWS = [
    "build_url_editor.rs",
    "build_url_row.rs",
    "generate_build_link_row.rs",
    "build_url_clipboard.rs",
    "system_quit_row_identity.rs",
    "system_quit_dialog_handlers.rs",
    "system_quit_hooks.rs",
]
# plus the two files the whole quit menu needs.
MOVING_ALL_NINE = MOVING_BUILD_ROWS + [
    "profile_rows_system_quit_menu.rs",
    "save_picker_path_editor.rs",
]

DEFINITION_PATTERNS = [
    (
        re.compile(
            r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?'
            r'(?:extern\s+"[^"]*"\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)',
            re.M,
        ),
        "fn",
    ),
    (
        re.compile(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?static\s+(?:mut\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:",
            re.M,
        ),
        "static",
    ),
    (
        re.compile(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", re.M
        ),
        "const",
    ),
    (
        re.compile(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|union|trait|type)\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)",
            re.M,
        ),
        "type",
    ),
]

IDENTIFIER = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


def strip_comments_and_strings(src):
    """Drop `//`, `/* */` and double-quoted spans so a name in prose is not counted."""
    out = []
    index = 0
    end = len(src)
    while index < end:
        char = src[index]
        if char == "/" and index + 1 < end and src[index + 1] == "/":
            while index < end and src[index] != "\n":
                index += 1
        elif char == "/" and index + 1 < end and src[index + 1] == "*":
            index += 2
            while index + 1 < end and not (src[index] == "*" and src[index + 1] == "/"):
                index += 1
            index += 2
        elif char == '"':
            index += 1
            while index < end:
                if src[index] == "\\":
                    index += 2
                    continue
                if src[index] == '"':
                    index += 1
                    break
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def collect_definitions():
    """name -> (kind, path relative to the root crate's src), outside the quit_menu tree."""
    found = {}
    for directory, _subdirs, files in os.walk(ROOT):
        if directory.startswith(QUIT_MENU):
            continue
        for name in files:
            if not name.endswith(".rs"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = strip_comments_and_strings(handle.read())
            for pattern, kind in DEFINITION_PATTERNS:
                for match in pattern.finditer(src):
                    found.setdefault(
                        match.group(1), (kind, os.path.relpath(path, ROOT))
                    )
    return found


def reached_by(files, definitions):
    """name -> the moving files that mention it."""
    used = {}
    for name in files:
        path = os.path.join(QUIT_MENU, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            src = strip_comments_and_strings(handle.read())
        for match in IDENTIFIER.finditer(src):
            symbol = match.group(1)
            if symbol in definitions:
                used.setdefault(symbol, set()).add(name)
    return used


def main(argv):
    scope = "build"
    as_markdown = False
    for arg in argv[1:]:
        if arg == "--markdown":
            as_markdown = True
        elif arg in ("build", "all9"):
            scope = arg
        else:
            print(f"unknown argument {arg!r}", file=sys.stderr)
            return 2

    definitions = collect_definitions()
    files = MOVING_BUILD_ROWS if scope == "build" else MOVING_ALL_NINE
    used = reached_by(files, definitions)

    rows = []
    for symbol in sorted(used):
        kind, source = definitions[symbol]
        rows.append(
            {
                "name": symbol,
                "kind": kind,
                "defined_in": source,
                "used_by": sorted(used[symbol]),
            }
        )

    if as_markdown:
        print("| symbol | kind | defined in | reached by |")
        print("|---|---|---|---|")
        for row in rows:
            callers = ", ".join(f"`{name}`" for name in row["used_by"])
            print(
                f"| `{row['name']}` | {row['kind']} | `{row['defined_in']}` | {callers} |"
            )
    else:
        print(json.dumps(rows, indent=1))

    counts = collections.Counter(row["kind"] for row in rows)
    print(f"scope={scope} total={len(rows)} {dict(counts)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
