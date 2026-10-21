#!/usr/bin/env python3
"""Gate: write `color`; the British spelling is a ratchet on its way out.

The rule
========
This repo spells it `color`, in prose, identifiers, config keys, messages and file names. The
British spelling (the word `color` with a `u` before the `r`) is counted case-insensitively and
anywhere in a token, so `name_<it>`, `<It>Table` and a shouted `<IT>` all count. The literal is
never written in this file: the pattern is assembled from two halves, so the gate does not have
to exempt itself and the scanner reads its own source like any other.

User request 2026-10-20: enforce `color`, prohibit the British spelling, and do it with a ratchet
so the existing uses can be deprecated over time. On the day it landed the tree carried 538 occurrences in 106 files, so the gate is a
ratchet rather than a wall.

The ratchet
===========
`scripts/color-spelling.baseline.json` records the count per tracked file. The gate fails when

  * a file rises above its number, or a file with no entry has any at all -- new writing is held
    to the real rule from its first line;
  * a file falls below its number without the baseline being lowered in the same diff. Recorded
    debt that is larger than the real debt is slack a later commit can spend without anyone
    noticing, so an improvement has to be written down to count. `--write-baseline` does that.

`_reasons` maps a file to why its occurrences cannot simply be respelled: a key parsed out of
another website's data, a key users already write in their own toml, a word list generated from
an English corpus. A reason is not an exemption -- that file is still held to its number in both
directions -- it is the note that tells the next person which counts are deprecation work and
which are someone else's contract. A reason for a file the baseline does not list is stale and
fails, so the notes cannot outlive what they describe.

Scope: every tracked file (`git ls-files`) except `Cargo.lock`, the baseline itself (its reasons
quote the keys they excuse), and anything with a NUL byte in its first 8 KiB, which is binary. Content is read from the working tree, so the count is of what
the next commit would carry.

Usage:
  python3 scripts/check-color-spelling.py                  # the gate
  python3 scripts/check-color-spelling.py --write-baseline # record the tree's counts
  python3 scripts/check-color-spelling.py --top 20         # worst files, exit 0
  python3 scripts/check-color-spelling.py --tokens <paths> # which spellings a file carries
  python3 scripts/check-color-spelling.py --selftest
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REL = "scripts/color-spelling.baseline.json"
GIT_TIMEOUT_SECONDS = 30

# Assembled, not written, so this file carries none of what it counts.
BANNED = "col" + "our"
PATTERN = re.compile(BANNED.encode("ascii"), re.IGNORECASE)
TOKEN = re.compile(rb"[A-Za-z0-9_]*" + BANNED.encode("ascii") + rb"[A-Za-z0-9_]*", re.IGNORECASE)

# Cargo.lock is written by cargo. The baseline is this gate's own record, and its `_reasons` quote
# the contract keys they excuse, so counting it would make every new reason raise its own number.
SKIPPED = frozenset({"Cargo.lock", BASELINE_REL})
BINARY_PROBE_BYTES = 8192
MAX_LINES_SHOWN = 20


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_PROBE_BYTES]


def count_in(data: bytes) -> int:
    return len(PATTERN.findall(data))


def tracked_paths(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=GIT_TIMEOUT_SECONDS,
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def read_tracked(root: Path, rel: str) -> bytes | None:
    """The working-tree bytes of `rel`, or None when it is absent, a directory, or binary."""
    if rel in SKIPPED or Path(rel).name in SKIPPED:
        return None
    path = root / rel
    if not path.is_file() or path.is_symlink():
        return None
    data = path.read_bytes()
    return None if is_binary(data) else data


def tree_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rel in tracked_paths(root):
        data = read_tracked(root, rel)
        if data is None:
            continue
        n = count_in(data)
        if n:
            counts[rel] = n
    return dict(sorted(counts.items()))


def load_baseline(path: Path) -> tuple[dict[str, int], dict[str, str]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    files = doc.get("files", {})
    reasons = doc.get("_reasons", {})
    if not isinstance(files, dict) or not all(isinstance(v, int) and v > 0 for v in files.values()):
        raise SystemExit(f"{path}: `files` must map each path to a positive count")
    if not isinstance(reasons, dict) or not all(isinstance(v, str) and v.strip() for v in reasons.values()):
        raise SystemExit(f"{path}: `_reasons` must map each path to a non-empty sentence")
    return files, reasons


def render_baseline(counts: dict[str, int], reasons: dict[str, str]) -> str:
    kept = {p: r for p, r in sorted(reasons.items()) if p in counts}
    return json.dumps({"_reasons": kept, "files": dict(sorted(counts.items()))}, indent=2) + "\n"


def judge(counts: dict[str, int], baseline: dict[str, int], reasons: dict[str, str]) -> list[str]:
    """Every way `counts` disagrees with the recorded baseline, one line each. Empty is a pass."""
    problems: list[str] = []
    for path in sorted(set(counts) | set(baseline)):
        now, was = counts.get(path, 0), baseline.get(path, 0)
        if now > was:
            what = "no baseline entry" if was == 0 else f"baseline {was}"
            problems.append(f"rose: {path}: {now} x {BANNED}, {what} (+{now - was})")
        elif now < was:
            problems.append(f"fell: {path}: {now} x {BANNED}, baseline {was} ({now - was})")
    for path in sorted(set(reasons) - set(baseline)):
        problems.append(f"stale: {path}: `_reasons` names a file the baseline does not count")
    return problems


def offending_lines(root: Path, rel: str) -> list[str]:
    data = read_tracked(root, rel) or b""
    out = []
    for number, line in enumerate(data.splitlines(), 1):
        if PATTERN.search(line):
            # UTF-8 Lossy: a display excerpt of a line that may not be UTF-8; nothing parses it.
            out.append(f"{rel}:{number}: {line.decode('utf-8', 'replace').strip()[:120]}")
    return out


def run_gate(root: Path, baseline_path: Path, stream=sys.stderr, out=sys.stdout) -> int:
    counts = tree_counts(root)
    baseline, reasons = load_baseline(baseline_path)
    problems = judge(counts, baseline, reasons)
    rose = [p for p in problems if p.startswith("rose:")]
    for problem in problems:
        if problem.startswith("rose:"):
            rel = problem.split(": ", 2)[1]
            for line in offending_lines(root, rel)[:MAX_LINES_SHOWN]:
                print(line, file=stream)
        print(problem, file=stream)
    if rose:
        print(
            f"\nWrite `color`, never `{BANNED}`: in prose, identifiers, keys and messages alike.\n"
            "If a file genuinely has to carry it -- another program's data key, a key users already\n"
            "write -- raise its number and add a `_reasons` line saying where the contract lives.",
            file=stream,
        )
    if any(p.startswith(("fell:", "stale:")) for p in problems):
        print(
            "\nThe recorded baseline is larger than the tree. Lower it in the same diff:\n"
            "  python3 scripts/check-color-spelling.py --write-baseline",
            file=stream,
        )
    if not problems:
        total = sum(counts.values())
        print(f"check-color-spelling: ok, {total} recorded in {len(counts)} files, none added", file=out)
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write-baseline", action="store_true", help="record the tree's counts")
    ap.add_argument("--top", type=int, default=0, help="print the N worst files and exit 0")
    ap.add_argument("--tokens", nargs="+", metavar="PATH", help="tally the spellings in PATHs")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.tokens:
        for rel in args.tokens:
            data = (REPO_ROOT / rel).read_bytes()
            # UTF-8 Lossy: a display tally of identifier tokens; nothing parses it.
            tally = collections.Counter(t.decode("utf-8", "replace") for t in TOKEN.findall(data))
            print(f"{count_in(data):4d}  {rel}  {dict(tally.most_common())}")
        return 0

    baseline_path = REPO_ROOT / BASELINE_REL
    if args.top:
        counts = tree_counts(REPO_ROOT)
        for path, n in sorted(counts.items(), key=lambda kv: -kv[1])[: args.top]:
            print(f"{n:6d}  {path}")
        print(f"{sum(counts.values()):6d}  total over {len(counts)} files")
        return 0

    if args.write_baseline:
        counts = tree_counts(REPO_ROOT)
        reasons = load_baseline(baseline_path)[1] if baseline_path.exists() else {}
        dropped = sorted(set(reasons) - set(counts))
        baseline_path.write_text(render_baseline(counts, reasons), encoding="utf-8")
        for path in dropped:
            print(f"dropped the `_reasons` entry for {path}: it no longer carries any")
        print(f"{BASELINE_REL}: {sum(counts.values())} in {len(counts)} files")
        return 0

    return run_gate(REPO_ROOT, baseline_path)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    word = BANNED
    mixed = f"{word} {word.title()} {word.upper()} name_{word} {word.title()}Table re{word}ed"
    check("every case and every position is counted", count_in(mixed.encode()), 6)
    check("the American spelling is not counted", count_in(b"color Color COLOR colorTable"), 0)
    check("the pattern is not a literal in this file", word in Path(__file__).read_text("utf-8"), False)
    check("NUL marks a binary", is_binary(b"abc\0" + word.encode()), True)
    check("text is not binary", is_binary(word.encode()), False)
    check("the baseline does not count itself", read_tracked(REPO_ROOT, BASELINE_REL), None)

    check("equal passes", judge({"a.rs": 2}, {"a.rs": 2}, {}), [])
    check("growth fails", [p.split(":")[0] for p in judge({"a.rs": 3}, {"a.rs": 2}, {})], ["rose"])
    check("growth names the delta", "(+1)" in judge({"a.rs": 3}, {"a.rs": 2}, {})[0], True)
    check("a new file fails", judge({"b.py": 1}, {}, {})[0].startswith("rose: b.py: 1 x"), True)
    check("a new file says it has no entry", "no baseline entry" in judge({"b.py": 1}, {}, {})[0], True)
    check(
        "a shrink without lowering the baseline fails",
        [p.split(":")[0] for p in judge({"a.rs": 1}, {"a.rs": 2}, {})],
        ["fell"],
    )
    check("a file swept to zero must leave the baseline", judge({}, {"a.rs": 2}, {})[0].startswith("fell:"), True)
    check("a reason keeps its file to its number", judge({"d.json": 5}, {"d.json": 5}, {"d.json": "x"}), [])
    check(
        "a reason does not let its file grow",
        [p.split(":")[0] for p in judge({"d.json": 6}, {"d.json": 5}, {"d.json": "x"})],
        ["rose"],
    )
    check(
        "a reason for an uncounted file is stale",
        [p.split(":")[0] for p in judge({}, {}, {"gone.json": "x"})],
        ["stale"],
    )
    check(
        "--write-baseline keeps reasons it still needs and drops the rest",
        json.loads(render_baseline({"d.json": 5}, {"d.json": "kept", "gone.json": "x"})),
        {"_reasons": {"d.json": "kept"}, "files": {"d.json": 5}},
    )

    # End to end through git, because the file walk -- ls-files, the binary probe, the skip list,
    # the working tree rather than the index -- is where a ratchet goes quietly blind.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], check=True, timeout=GIT_TIMEOUT_SECONDS)
        (root / "a.rs").write_text(f"// {word}\nlet {word.title()}Table = 1;\n", encoding="utf-8")
        (root / "data.bin").write_bytes(b"\0" + word.encode())
        (root / "Cargo.lock").write_text(f"name = \"{word}\"\n", encoding="utf-8")
        (root / "untracked.rs").write_text(word, encoding="utf-8")
        subprocess.run(git + ["add", "a.rs", "data.bin", "Cargo.lock"], check=True, timeout=GIT_TIMEOUT_SECONDS)
        check("only tracked text files outside the skip list count", tree_counts(root), {"a.rs": 2})

        baseline = root / "baseline.json"
        quiet = open(Path(tmp) / "out.txt", "w", encoding="utf-8")
        baseline.write_text(render_baseline({"a.rs": 2}, {}), encoding="utf-8")
        check("the gate passes at the recorded number", run_gate(root, baseline, quiet, quiet), 0)
        (root / "a.rs").write_text(f"// {word} {word}\nlet {word.title()}Table = 1;\n", encoding="utf-8")
        check("the gate fails when a tracked file gains one", run_gate(root, baseline, quiet, quiet), 1)
        (root / "b.rs").write_text(f"// {word}\n", encoding="utf-8")
        subprocess.run(git + ["add", "b.rs"], check=True, timeout=GIT_TIMEOUT_SECONDS)
        check("the gate fails on a new tracked file that has one", run_gate(root, baseline, quiet, quiet), 1)
        (root / "b.rs").write_text("// color\n", encoding="utf-8")
        (root / "a.rs").write_text("// color\n", encoding="utf-8")
        check("the gate fails when a file improves silently", run_gate(root, baseline, quiet, quiet), 1)
        baseline.write_text(render_baseline(tree_counts(root), {}), encoding="utf-8")
        check("lowering the baseline clears it", run_gate(root, baseline, quiet, quiet), 0)
        quiet.close()
        report = (Path(tmp) / "out.txt").read_text(encoding="utf-8")
        check("a rise is reported with its line", f"a.rs:1: // {word} {word}" in report, True)
        check("a rise tells the author what to write", "Write `color`" in report, True)
        check("a fall says how to lower the baseline", "--write-baseline" in report, True)

    # The real baseline must load, and every reason must name a file it counts.
    files, reasons = load_baseline(REPO_ROOT / BASELINE_REL)
    check("the committed baseline has no stale reasons", sorted(set(reasons) - set(files)), [])

    for f in failures:
        print(f"selftest FAILURE {f}", file=sys.stderr)
    print(f"check-color-spelling selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
