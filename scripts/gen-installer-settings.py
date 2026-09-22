#!/usr/bin/env python3
"""Turn every mod's own settings file into a schema the installer can walk a player through.

What this is for
----------------
`er-installer` copies DLLs and writes an ME3 profile. Nine of those DLLs also read a settings
file of their own in the game directory, and until now the installer's whole contribution was to
print the names of those files and leave the player to find them. This generator is what lets it
offer the settings instead: for each file, the keys, their shipped defaults, the prose the author
wrote above each one, and the choices where the author listed them.

The one rule: the defaults belong to the DLL, never to this script
------------------------------------------------------------------
Each of these files is written by its own DLL, with comments, the first time the DLL finds it
absent. That text is the documentation a player reads in the game folder, and it is the only
statement of what the shipped defaults are. So the installer must offer those bytes -- not a
second copy of them maintained here, which would drift and would then tell a player their game
is configured one way while it is configured another.

Seven of the nine texts are a string literal in the crate that writes them, and are read out of
the source here. The other two are computed in Rust -- `er-quit-menu.toml` interpolates its own
accepted row names, `er-quickload.toml` interpolates a block that `er-save-picker-core` builds --
and a text scraper cannot evaluate those. Those come from `tools/er-config-defaults`, which links
the same functions the DLLs call, via the tracked file it writes. Refresh that file with
`--collect`; it is checked in so this generator does not have to compile three crates to answer.

Nothing here is allowed to guess. An unresolved `{placeholder}`, a file whose text cannot be
found, or a settings file in the catalog with no source listed is an error, not a gap.

Usage:
    python3 scripts/gen-installer-settings.py            # write the Rust
    python3 scripts/gen-installer-settings.py --check    # fail if it is out of date
    python3 scripts/gen-installer-settings.py --collect  # refresh the computed-text file
    python3 scripts/gen-installer-settings.py --report   # what was found, per file
    python3 scripts/gen-installer-settings.py --selftest

Exit status is 1 on any failure, so `--check` can gate.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_TOML = REPO_ROOT / "scripts" / "me3-dll-catalog.toml"
COMPUTED_TEXT = REPO_ROOT / "tools" / "er-installer" / "config-defaults.txt"
OUT_RS = REPO_ROOT / "tools" / "er-installer" / "src" / "settings_schema.rs"
COLLECTOR = "er-config-defaults"

# The name the collector prints the `er-quickload.toml` save-picker block under. It is a hole in
# a template, not a settings file of its own.
PICKER_BLOCK = "er-quickload.toml#picker_block"

# How to obtain each settings file's shipped text.
#
# `literal` names a Rust source file and the item in it whose string is the text. `computed` means
# the text comes from the tracked collector output under that same file name. A `substitute`
# entry fills a hole in a template literal from the collector output.
#
# Every `config = "..."` value in `me3-dll-catalog.toml` must appear here; the generator refuses
# on one that does not, so a new settings file cannot be silently absent from the walkthrough.
SOURCES: dict[str, dict] = {
    "er-refill-all.toml": {
        "literal": ("crates/er-refill-all/src/config.rs", "DEFAULT_CONFIG_TOML"),
    },
    "er-enemynpc-effects.toml": {
        "literal": ("crates/er-enemynpc-effects/src/config.rs", "DEFAULT_CONFIG_TOML"),
    },
    "er-npc-possess.toml": {
        "literal": ("crates/er-npc-possess/src/config.rs", "DEFAULT_CONFIG_TOML"),
    },
    "er-invasion-path.toml": {
        "literal": ("crates/er-invasion-path/src/config.rs", "DEFAULT_CONFIG_TOML"),
    },
    "er-inventory-sort.toml": {
        "literal": ("crates/er-inventory-sort/src/lib.rs", "boilerplate_config"),
    },
    "er-player-name-filter.toml": {
        "literal": ("crates/er-player-name-filter/src/lib.rs", "boilerplate_config"),
    },
    "er-quickload.toml": {
        "literal": ("crates/er-quickload/src/config.rs", "boilerplate_config"),
        "substitute": {"picker_block": PICKER_BLOCK},
    },
    "er-quit-menu.toml": {"computed": True},
    "er-invasion-warp.toml": {"computed": True},
}


class Failure(Exception):
    """A source that could not be resolved. Always fatal: see the module docstring."""


# --------------------------------------------------------------------------------------
# Reading a Rust string literal out of a source file
# --------------------------------------------------------------------------------------


def raw_literal(source: str, item: str) -> str | None:
    """`const ITEM: &str = r#"..."#;` -- the easy and most common shape."""
    pattern = re.compile(
        r"(?:pub(?:\(crate\))?\s+)?const\s+" + re.escape(item) + r"\s*:\s*&str\s*=\s*r(#+)\"",
        re.M,
    )
    match = pattern.search(source)
    if not match:
        return None
    hashes = match.group(1)
    start = match.end()
    end = source.find('"' + hashes, start)
    if end < 0:
        raise Failure(f"{item}: raw string opened at offset {start} and never closed")
    return source[start:end]


def escaped_literal(source: str, item: str) -> str | None:
    """A function whose body is one ordinary `"..."` literal, escapes and all.

    Covers both remaining shapes: a string laid out over real newlines with `\\"` inside it, and
    one written as `"line\\n\\` continuations. A `format!(...)` wrapper is accepted, because a
    template is the same literal with holes in it.
    """
    pattern = re.compile(r"\bfn\s+" + re.escape(item) + r"\s*\([^)]*\)[^{]*\{", re.M)
    match = pattern.search(source)
    if not match:
        return None
    body_start = match.end()
    quote = source.find('"', body_start)
    if quote < 0:
        raise Failure(f"{item}: no string literal in the function body")
    # Refuse a literal that is not the first thing the body produces -- a `let` above it means
    # the text is assembled, and taking the first string would silently take a fragment.
    prelude = source[body_start:quote]
    if re.search(r"\blet\b", prelude) and "format!" not in prelude:
        raise Failure(
            f"{item}: the body builds its text before the first string literal; "
            "read it through tools/er-config-defaults instead of scraping it"
        )
    return unescape_rust(source, quote + 1, item)


def unescape_rust(source: str, index: int, item: str) -> str:
    """Decode an ordinary Rust string literal starting just past its opening quote."""
    out: list[str] = []
    while index < len(source):
        char = source[index]
        if char == '"':
            return "".join(out)
        if char != "\\":
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(source):
            break
        escape = source[index]
        if escape == "\n":
            # A line continuation: the newline and the indentation after it are not content.
            index += 1
            while index < len(source) and source[index] in " \t":
                index += 1
            continue
        out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(escape, escape))
        index += 1
    raise Failure(f"{item}: string literal never closed")


def literal_text(relative: str, item: str) -> str:
    path = REPO_ROOT / relative
    if not path.is_file():
        raise Failure(f"{relative}: no such file")
    source = path.read_text(encoding="utf-8")
    text = raw_literal(source, item)
    if text is None:
        text = escaped_literal(source, item)
    if text is None:
        raise Failure(f"{relative}: no `{item}` string literal found")
    return text


# --------------------------------------------------------------------------------------
# The computed texts, and putting the two halves together
# --------------------------------------------------------------------------------------


def read_computed() -> dict[str, str]:
    """Parse the tracked collector output into `{name: text}`."""
    if not COMPUTED_TEXT.is_file():
        raise Failure(
            f"{COMPUTED_TEXT.relative_to(REPO_ROOT)} is missing -- run this with --collect"
        )
    blocks: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []
    for line in COMPUTED_TEXT.read_text(encoding="utf-8").splitlines():
        if line == "<<<end>>>":
            if name is None:
                raise Failure("computed text: an end frame with no block open")
            blocks[name] = "\n".join(lines) + "\n"
            name, lines = None, []
        elif line.startswith("<<<") and line.endswith(">>>"):
            if name is not None:
                raise Failure(f"computed text: {name} was never closed")
            name = line[3:-3]
        elif name is not None:
            lines.append(line)
        elif line.strip():
            raise Failure(f"computed text: stray line outside any block: {line!r}")
    if name is not None:
        raise Failure(f"computed text: {name} was never closed")
    return blocks


def collect() -> None:
    """Re-run the collector and write its output to the tracked file."""
    build_run = subprocess.run(
        ["cargo", "build", "-p", COLLECTOR],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if build_run.returncode != 0:
        raise Failure(f"cargo build -p {COLLECTOR} failed:\n{build_run.stderr}")
    run = subprocess.run(
        [str(REPO_ROOT / "target" / "debug" / COLLECTOR)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if run.returncode != 0:
        raise Failure(f"{COLLECTOR} failed:\n{run.stderr}")
    COMPUTED_TEXT.write_text(run.stdout, encoding="utf-8")
    print(f"wrote {COMPUTED_TEXT.relative_to(REPO_ROOT)} ({len(run.stdout)} bytes)")


def default_text(name: str, source: dict, computed: dict[str, str]) -> str:
    if source.get("computed"):
        if name not in computed:
            raise Failure(f"{name}: not in the collector output -- run this with --collect")
        return computed[name]
    text = literal_text(*source["literal"])
    for hole, block in source.get("substitute", {}).items():
        if block not in computed:
            raise Failure(f"{name}: {block} is not in the collector output; run with --collect")
        text = text.replace("{" + hole + "}", computed[block].rstrip("\n"))
    leftover = re.findall(r"\{([a-z_][a-z0-9_]*)\}", text)
    if leftover:
        raise Failure(
            f"{name}: unresolved placeholder(s) {sorted(set(leftover))}. "
            "Add them to `substitute` and print them from tools/er-config-defaults -- "
            "a placeholder shown to a player is a default this repo invented."
        )
    return text


# --------------------------------------------------------------------------------------
# Text to settings
# --------------------------------------------------------------------------------------

ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.+?)\s*$")
TABLE = re.compile(r"^\[([^\]]+)\]\s*$")
# A prose line naming the allowed values, as an author writes it.
VALUES_LINE = re.compile(r"Values:\s*(.+?)\.?\s*$")
# An indented, quoted token at the start of a prose line: how a per-value explanation is written.
QUOTED_LEAD = re.compile(r"^\s+\"([^\"]+)\"")
# A prose line that is nothing but quoted tokens and commas: how a set of names is listed.
QUOTED_SET = re.compile(r"^\s*\"[^\"]+\"(?:\s*,\s*\"[^\"]+\")+\s*,?\s*$")


@dataclass
class Setting:
    key: str
    table: str
    default: str
    kind: str
    choices: list[str] = field(default_factory=list)
    optional: bool = False
    prose: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return f"{self.table}.{self.key}" if self.table else self.key


def classify(value: str) -> str:
    if value in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+", value):
        return "int"
    # Distinguished from `int` because the DLL parsers do: `er-npc-possess` reads
    # `spawn.distance_m` and `mapping.watchdog_seconds` as `f32`, and offering a whole number
    # where the file shows `3.0` invites a value its own parser will reject.
    if re.fullmatch(r"-?\d+\.\d+", value):
        return "float"
    if value.startswith("["):
        return "list"
    return "text"


def strip_inline_comment(value: str) -> str:
    """Drop a trailing `# ...` that is outside quotes. The DLL parsers all do the same."""
    in_quote: str | None = None
    for index, char in enumerate(value):
        if in_quote:
            if char == in_quote:
                in_quote = None
        elif char in "\"'":
            in_quote = char
        elif char == "#":
            return value[:index].strip()
    return value.strip()


def unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def choices_from(prose: list[str]) -> list[str]:
    """The allowed values, only where the author actually listed them."""
    for line in prose:
        match = VALUES_LINE.search(line)
        if match:
            found = [unquote(part) for part in match.group(1).split(",")]
            found = [value for value in found if value]
            if len(found) >= 2:
                return found
    # A single line of nothing but quoted names, e.g. the accepted row names.
    for line in prose:
        body = line.lstrip("#")
        if QUOTED_SET.fullmatch(body):
            return [unquote(token) for token in re.findall(r"\"[^\"]+\"", body)]
    # One quoted name per line, each with its own explanation beside it.
    leads = [match.group(1) for line in prose if (match := QUOTED_LEAD.match(line.lstrip("#")))]
    return leads if len(leads) >= 2 else []


def parse_settings(text: str) -> list[Setting]:
    """Every key a player can set in one of these files, in the order the file lists them.

    A key written as an ordinary assignment ships with that value in force. A key written as a
    comment ships unset, and its commented line is the author's statement of what setting it
    would be -- both kinds are offered, and the difference is carried as `optional` so the
    walkthrough can say "unset" rather than inventing a value for it.

    One comment block can belong to several keys, and does in six of the nine files: an author
    who writes the three inventory-sort categories, or the eight `er-npc-possess` button
    bindings, explains them once and then lists them. So a block carries forward across a run of
    consecutive assignments and is only dropped by a blank line, a table header, or the next
    block of comments. Attributing it to the first key alone left 22 settings with no explanation
    at all and stripped the `Values:` list off two of the three inventory-sort keys -- which
    would have offered a player free text where their own file names three choices.
    """
    settings: list[Setting] = []
    prose: list[str] = []
    # True once an assignment has been seen under the current block: the next comment line
    # starts a new block rather than extending the one that key belongs to.
    spent = False
    table = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            prose, spent = [], False
            continue
        header = TABLE.match(line)
        if header:
            table = header.group(1).strip()
            # The block above a table header explains that table, so its keys inherit it -- the
            # `[buttons]` and `[picker]` tables in `er-npc-possess.toml` are both written that
            # way, and clearing here left five settings with nothing to show a player. Marked
            # spent so the next comment block replaces it rather than extending it.
            spent = True
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            commented = ASSIGNMENT.match(body)
            if commented:
                value = strip_inline_comment(commented.group(2))
                settings.append(
                    Setting(
                        key=commented.group(1),
                        table=table,
                        default=value,
                        kind=classify(value),
                        choices=choices_from(prose),
                        optional=True,
                        prose=list(prose),
                    )
                )
                spent = True
                continue
            if spent:
                prose, spent = [], False
            prose.append(line)
            continue
        assignment = ASSIGNMENT.match(line)
        if assignment:
            value = strip_inline_comment(assignment.group(2))
            settings.append(
                Setting(
                    key=assignment.group(1),
                    table=table,
                    default=value,
                    kind=classify(value),
                    choices=choices_from(prose),
                    optional=False,
                    prose=list(prose),
                )
            )
            spent = True
            continue
        # Not a comment, not an assignment, not blank: a continuation line inside a multi-line
        # array. It belongs to the key above it, so the block is left as it is.
        spent = True
    return dedupe(settings)


def dedupe(settings: list[Setting]) -> list[Setting]:
    """Keep the first mention of each key.

    An author who documents a key in prose with a commented example and then assigns it is
    describing one setting, not two, and offering it twice in a walkthrough reads as a bug.
    A live assignment always wins over a commented one for the same key, because that is the
    value the DLL will actually be running with.
    """
    by_path: dict[str, Setting] = {}
    order: list[str] = []
    for setting in settings:
        existing = by_path.get(setting.path)
        if existing is None:
            by_path[setting.path] = setting
            order.append(setting.path)
        elif existing.optional and not setting.optional:
            by_path[setting.path] = setting
    return [by_path[path] for path in order]


# --------------------------------------------------------------------------------------
# Emitting the Rust
# --------------------------------------------------------------------------------------

HEADER = '''// GENERATED by scripts/gen-installer-settings.py -- do not edit.
//
// Source of truth: each mod DLL's own settings file, as that DLL writes it. Seven are string
// literals in the crates that own them; `er-quit-menu.toml` and `er-invasion-warp.toml` are
// computed in Rust and come through `tools/er-config-defaults`. Edit the owning crate and
// regenerate; `scripts/gen-installer-settings.py --check` is what keeps the two in step.
//
// Compiled in rather than read at runtime, for the same reason `catalog.rs` is: the installer
// ships as one exe with no data files beside it to lose and no TOML parser inside it.
//
// The default text is carried verbatim, comments and all, because it is what the DLL writes when
// it finds no file -- so a fresh install can lay down the real documented file rather than a
// stripped list of assignments.

/// What kind of value a setting takes, as far as its shipped default reveals.
///
/// Inferred from the default literal rather than declared, so a setting cannot be described here
/// as something its own file disagrees with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Bool,
    Int,
    /// Held apart from `Int` because the owning parsers are: `er-npc-possess` reads its
    /// distances and windows as `f32`, and a whole number offered where the file shows `3.0`
    /// invites a value that parser rejects.
    Float,
    /// A TOML array, e.g. the quit-menu row list.
    List,
    Text,
}

/// One key a player can set.
#[derive(Debug)]
pub struct Setting {
    pub key: &'static str,
    /// The `[table]` this key sits under, empty at the top level.
    pub table: &'static str,
    /// The shipped value, exactly as the file spells it.
    pub default: &'static str,
    pub kind: Kind,
    /// The allowed values, and only where the file's own prose lists them. Empty means the
    /// setting takes free text, not that it takes anything.
    pub choices: &'static [&'static str],
    /// True when the key ships commented out: the DLL runs without it, and `default` is the
    /// author's example of what it would be rather than what is in force.
    pub optional: bool,
    /// The comment lines the author wrote directly above this key, `#` and all.
    pub prose: &'static [&'static str],
}

impl Setting {
    /// How the key is spelled in the file: `table.key`, or just the key at the top level.
    pub fn path(&self) -> String {
        if self.table.is_empty() {
            self.key.to_owned()
        } else {
            format!("{}.{}", self.table, self.key)
        }
    }
}

/// One settings file in the game directory.
#[derive(Debug)]
pub struct ConfigFile {
    /// The file name, which is also how `catalog::Mod::config` names it.
    pub file: &'static str,
    /// Exactly what the owning DLL writes when it finds no file.
    pub default_text: &'static str,
    pub settings: &'static [Setting],
}

impl ConfigFile {
    pub fn find(file: &str) -> Option<&'static Self> {
        CONFIGS.iter().find(|entry| entry.file == file)
    }
}
'''


def rust_string(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def rust_slice(values: list[str]) -> str:
    if not values:
        return "&[]"
    return "&[" + ", ".join(rust_string(value) for value in values) + "]"


def rustfmt(text: str) -> str:
    """Put the rendered module through rustfmt, so two gates cannot disagree about it.

    Without this, `cargo fmt --all -- --check` fails on the generated file and the only way to
    satisfy it is to format the file by hand, after which `--check` here fails instead. The prose
    arrays are what provoke it: rustfmt leaves a long string literal alone because it cannot break
    one, but it will wrap a long `&["...", "..."]` across lines.
    """
    run = subprocess.run(
        ["rustfmt", "--edition", "2024", "--emit", "stdout"],
        input=text,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if run.returncode != 0:
        raise Failure(f"rustfmt refused the generated module:\n{run.stderr}")
    # Returned as-is. An earlier version stripped a leading `//` line, believing `--emit stdout`
    # prefixed a banner; it does not, and the line that got eaten was the generated file's own
    # "do not edit" header -- measured by piping `// hello\nfn main(){}` through it, which comes
    # back unchanged.
    if not run.stdout.strip():
        raise Failure("rustfmt returned nothing for the generated module")
    return run.stdout


def render(configs: list[tuple[str, str, list[Setting]]]) -> str:
    out = [HEADER, "\npub const CONFIGS: &[ConfigFile] = &["]
    for file, text, settings in configs:
        out.append("    ConfigFile {")
        out.append(f"        file: {rust_string(file)},")
        out.append(f"        default_text: {rust_string(text)},")
        out.append("        settings: &[")
        for setting in settings:
            out.append("            Setting {")
            out.append(f"                key: {rust_string(setting.key)},")
            out.append(f"                table: {rust_string(setting.table)},")
            out.append(f"                default: {rust_string(setting.default)},")
            out.append(f"                kind: Kind::{setting.kind.capitalize()},")
            out.append(f"                choices: {rust_slice(setting.choices)},")
            out.append(f"                optional: {'true' if setting.optional else 'false'},")
            out.append(f"                prose: {rust_slice(setting.prose)},")
            out.append("            },")
        out.append("        ],")
        out.append("    },")
    out.append("];\n")
    return rustfmt("\n".join(out))


# --------------------------------------------------------------------------------------
# Driving it
# --------------------------------------------------------------------------------------


def catalog_config_files() -> list[str]:
    """Every settings file named by the mod catalog, deduplicated, in catalog order."""
    table = tomllib.loads(CATALOG_TOML.read_text(encoding="utf-8"))
    files: list[str] = []
    for entry in table.values():
        if not isinstance(entry, dict):
            continue
        name = entry.get("config")
        if name and name not in files:
            files.append(name)
    return files


def build() -> tuple[str, list[tuple[str, str, list[Setting]]]]:
    files = catalog_config_files()
    unknown = [name for name in files if name not in SOURCES]
    if unknown:
        raise Failure(
            f"the catalog names settings file(s) with no source here: {unknown}. "
            "Add them to SOURCES -- a settings file the installer cannot read is one a player "
            "is told about and then left to find alone."
        )
    stale = [name for name in SOURCES if name not in files]
    if stale:
        raise Failure(f"SOURCES names settings file(s) no mod claims any more: {stale}")
    computed = read_computed()
    configs = []
    for name in files:
        text = default_text(name, SOURCES[name], computed)
        settings = parse_settings(text)
        if not settings:
            raise Failure(f"{name}: no settings found in {len(text)} bytes of default text")
        configs.append((name, text, settings))
    return render(configs), configs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the Rust is out of date")
    parser.add_argument("--collect", action="store_true", help="refresh the computed-text file")
    parser.add_argument("--report", action="store_true", help="print what was found, per file")
    parser.add_argument("--selftest", action="store_true", help="test the parser")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    try:
        if args.collect:
            collect()
        rendered, configs = build()
    except Failure as failure:
        print(f"gen-installer-settings: {failure}", file=sys.stderr)
        return 1

    total = sum(len(settings) for _, _, settings in configs)

    if args.report:
        for file, text, settings in configs:
            live = sum(1 for setting in settings if not setting.optional)
            print(
                f"{file}: {len(settings)} settings ({live} set, "
                f"{len(settings) - live} optional), {len(text)} bytes of text"
            )
            for setting in settings:
                choices = f" one of {setting.choices}" if setting.choices else ""
                print(f"    {setting.path} = {setting.default} [{setting.kind}]{choices}")
        return 0

    if args.check:
        if not OUT_RS.is_file():
            print(
                f"gen-installer-settings: {OUT_RS.relative_to(REPO_ROOT)} is missing",
                file=sys.stderr,
            )
            return 1
        if OUT_RS.read_text(encoding="utf-8") != rendered:
            print(
                f"gen-installer-settings: {OUT_RS.relative_to(REPO_ROOT)} is out of date.\n"
                "Run: python3 scripts/gen-installer-settings.py",
                file=sys.stderr,
            )
            return 1
        print(
            f"gen-installer-settings: {OUT_RS.relative_to(REPO_ROOT)} is current "
            f"({total} settings across {len(configs)} files)"
        )
        return 0

    OUT_RS.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {OUT_RS.relative_to(REPO_ROOT)}: "
        f"{total} settings across {len(configs)} files"
    )
    return 0


def selftest() -> int:
    """Cover the two things that decide whether a player is shown the truth.

    The parser must find a commented key without inventing one out of prose, and the unescaper
    must reproduce a Rust literal exactly -- a dropped backslash in a Windows path example is a
    default that will not work when a player accepts it.
    """
    failures: list[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, wanted {want!r}")

    sample = (
        "# A header about the whole file.\n"
        "\n"
        "# Controller combination. Empty disables it.\n"
        "#   select   start\n"
        'gamepad_hotkey = "select+start"\n'
        "\n"
        "# Values: order_of_acquisition, item_type, preserve.\n"
        'armaments = "order_of_acquisition"\n'
        "\n"
        "# An optional one, with an example the DLL does not run with.\n"
        "# slot = 0                    # character slot\n"
        "refill_immediately = true\n"
        "\n"
        "[target]\n"
        "# Inside a table.\n"
        "radius = 12\n"
        "# Prose that merely contains an equals sign: - = [ ] ; not an assignment.\n"
        "trailing = false\n"
    )
    settings = parse_settings(sample)
    check(
        "paths",
        [setting.path for setting in settings],
        [
            "gamepad_hotkey",
            "armaments",
            "slot",
            "refill_immediately",
            "target.radius",
            "target.trailing",
        ],
    )
    by_path = {setting.path: setting for setting in settings}
    check("hotkey default", by_path["gamepad_hotkey"].default, '"select+start"')
    check("hotkey kind", by_path["gamepad_hotkey"].kind, "text")
    check(
        "enum choices",
        by_path["armaments"].choices,
        ["order_of_acquisition", "item_type", "preserve"],
    )
    check("slot is optional", by_path["slot"].optional, True)
    check("slot default drops its inline comment", by_path["slot"].default, "0")
    check("slot kind", by_path["slot"].kind, "int")
    check("bool kind", by_path["refill_immediately"].kind, "bool")
    check("table key", by_path["target.radius"].table, "target")
    check(
        "a blank line ends a prose run",
        by_path["armaments"].prose,
        ["# Values: order_of_acquisition, item_type, preserve."],
    )

    # A key documented as a comment and then assigned is one setting, and the live value wins.
    both = parse_settings('# hotkey = "ctrl+a"\nhotkey = "ctrl+b"\n')
    check("dedupe count", len(both), 1)
    check("dedupe keeps the live value", both[0].default, '"ctrl+b"')
    check("dedupe is not optional", both[0].optional, False)

    # The unescaper, against the two shapes in the tree.
    continued = 'fn boilerplate_config() -> &\'static str {\n    "# a\\n\\\n# b\\n"\n}\n'
    check("continuation", escaped_literal(continued, "boilerplate_config"), "# a\n# b\n")
    windows = 'fn boilerplate_config() -> String {\n    "\\\n# %APPDATA%\\\\EldenRing\\\\x\n"\n}\n'
    check(
        "backslashes",
        escaped_literal(windows, "boilerplate_config"),
        "# %APPDATA%\\EldenRing\\x\n",
    )
    raw = 'const DEFAULT_CONFIG_TOML: &str = r#"# hi\nkey = 1\n"#;\n'
    check("raw literal", raw_literal(raw, "DEFAULT_CONFIG_TOML"), "# hi\nkey = 1\n")

    # A source that cannot be read must be fatal rather than shown to a player as empty.
    try:
        default_text("x.toml", {"literal": ("does/not/exist.rs", "x")}, {})
        failures.append("a missing literal source did not raise")
    except Failure:
        pass

    # And the real thing must parse, which is the test that actually covers the nine files.
    configs: list[tuple[str, str, list[Setting]]] = []
    try:
        _, configs = build()
    except Failure as failure:
        failures.append(f"build() failed: {failure}")
    for file, _text, settings in configs:
        if any(not setting.key for setting in settings):
            failures.append(f"{file}: a setting with an empty key")

    for line in failures:
        print(f"selftest: {line}", file=sys.stderr)
    if failures:
        return 1
    print(
        "gen-installer-settings --selftest: ok "
        f"({sum(len(settings) for _, _, settings in configs)} settings across "
        f"{len(configs)} files)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
