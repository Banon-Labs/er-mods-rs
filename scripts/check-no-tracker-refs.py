#!/usr/bin/env python3
"""Gate: comments carry no beads ids, pull request numbers or branch names.

User request 2026-11-24: "I would like to remove mention of beads issues and PRs and branches from
comments throughout the codebase". A comment says what the code does and why. Which issue asked for
it, which pull request landed it and which branch it was written on is history, and the history
lives in git (`git log`, `git blame`) and in bd, where it stays true. In a comment it goes stale
the day the branch is deleted or the issue closes, and it sends the reader to look something up
instead of telling them.

What counts
===========
Only comments: `//`, `///`, `//!` and `/* */` in Rust, JavaScript, C and the shader languages; `#`
in Python, shell, rego, YAML and TOML; `--` in Lua; and Python docstrings, which are documentation
to everyone but the interpreter. String literals are code, and so is a branch name a test fixture
pushes; neither is read. Markdown is out of scope.

In a comment, three shapes are a mention:

  * a beads id: this repo's prefix (or its old one) plus a 3-5 character hash, with an optional
    `.N` child suffix, and the 3-character ids of backseat-gamer's tracker;
  * a pull request: `PR` and a number, `pull request` and a number, the `(#<n>)` suffix GitHub puts
    on a squash merge, a `/pull/<n>` link, and a bare three-digit `#<n>`, which is how this tree
    wrote pull request numbers when it left the `PR` off;
  * a branch: a conventional prefix this repo's branches use, a slash, and a hyphenated slug, plus
    release-please's own branch.

What does not, measured over the tree on 2026-11-24 (the false positives the first draft found):

  * `docs/...`, `research/...`, `triage/...`, `test/...` and `build/...` are directories here, so
    those prefixes are never read as branches, and a slug that ends in a file extension or carries
    on into another path segment is a path, not a branch;
  * a prefix and a one-word slug is prose, not a branch: "a fix/instrument run", "the refactor/move
    gate", "cleanup/destructor". Every branch this repo has pushed has a hyphenated slug;
  * a bare `#<n>` of one, two, four or more digits is an ordinal or a counter -- "R1 #1", "run #14",
    "CALL #13800", a hex color -- and so is a three-digit one inside a list (`#64,#128`) or after
    a word that counts (run, call, hit, frame, write, point, line, kick, slot, step), and a
    one-digit `(#1)` quoted from a log line;
  * a lowercase `pr` glued to a number is a run id or a profile file name (`pr<n>-boot-...`,
    `pr<n>-invasion-warp-....me3`), so only a capital `PR` is read;
  * other project names followed by a short word (`frida-up`, `ai-lab`, `data-write`) look like a
    bd id under that project's default prefix and are not one, so only the prefixes this repo's
    comments actually cite are read.

Usage:
  python3 scripts/check-no-tracker-refs.py              # the gate: file:line for each, exit 1 if any
  python3 scripts/check-no-tracker-refs.py <paths>      # only those files
  python3 scripts/check-no-tracker-refs.py --count      # totals per kind and per file, exit 0
  python3 scripts/check-no-tracker-refs.py --selftest

The write-time half is `.cupcake/policies/claude/edit_no_tracker_refs_guard.rego`, which refuses
an edit that adds one.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT_SECONDS = 30
BINARY_PROBE_BYTES = 8192


def _load_comment_caps():
    """`check-comment-caps.py` already tokenises Rust, Python and shell comments without reading
    inside string literals, and its selftest pins the hard cases (raw strings, char literals,
    shell `$#` and `${x#y}`). Reusing it keeps one tokeniser for those three languages."""
    path = Path(__file__).resolve().with_name("check-comment-caps.py")
    spec = importlib.util.spec_from_file_location("check_comment_caps", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


CC = _load_comment_caps()

# --- what a mention looks like ---------------------------------------------------------------

# The prefixes are assembled so this file's own comments and docstring carry none of what it reads.
BD_PREFIXES = ("er-effects" + "-rs", "er-mods" + "-rs")
BEADS = re.compile(r"\b(?:" + "|".join(map(re.escape, BD_PREFIXES)) + r")-[a-z0-9]{3,5}(?:\.\d+)*(?![\w-])")
# backseat-gamer's ids are three characters; `bsg-check` and `bsg-work` are words, not ids.
BEADS_BSG = re.compile(r"\bbsg-[a-z0-9]{3}(?![\w-])")

# `PR` in capitals only: a lowercase `pr109-...` is a run id or a profile file name, not prose.
PR_WORD = re.compile(r"\bPRs?[\s-]*#?\d+|(?i:\bpull[\s-]+requests?)[\s-]*#?\d+")
# Two digits or more: a `(#1)` is a counter quoted from a log line.
PR_SUFFIX = re.compile(r"\(#\d{2,}\)")
PR_URL = re.compile(r"/pull/\d+")
# A bare three-digit number: how this tree wrote a pull request when it dropped the `PR`.
PR_BARE = re.compile(r"(?<![\w#,~&/.-])#(\d{3})(?![\w#])")
COUNTING_WORD = re.compile(
    r"\b(?:runs?|calls?|hits?|frames?|writes?|points?|lines?|kicks?|slots?|steps?|rows?|entry|"
    r"entries|items?|attempts?|iterations?|samples?|R1|R2|L1|L2)\s*$",
    re.IGNORECASE,
)

# The prefixes of every branch this repo has pushed, less the ones that are also directories here.
BRANCH_PREFIXES = (
    "feat", "fix", "chore", "refactor", "perf", "revert", "style", "cleanup", "merge", "diag",
    "roadmap", "wip", "spike", "investigation", "guard", "ci", "backup", "autoresearch",
)
BRANCH = re.compile(r"(?<![\w/.-])(?:" + "|".join(BRANCH_PREFIXES) + r")/([A-Za-z0-9][\w.-]*)")
FILE_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")
RELEASE_PLEASE = re.compile(r"\brelease-please--branches--[\w./-]+")


def mentions(comment: str) -> list[tuple[str, str]]:
    """Every (kind, text) mention in one line of comment text."""
    found: list[tuple[str, str]] = []
    for pattern in (BEADS, BEADS_BSG):
        found += [("beads", m.group(0)) for m in pattern.finditer(comment)]
    taken: list[tuple[int, int]] = []
    for pattern in (PR_WORD, PR_SUFFIX, PR_URL):
        for m in pattern.finditer(comment):
            found.append(("pr", m.group(0)))
            taken.append(m.span())
    for m in PR_BARE.finditer(comment):
        if any(a <= m.start() < b for a, b in taken):
            continue
        if COUNTING_WORD.search(comment[: m.start()]):
            continue
        found.append(("pr", m.group(0)))
    for m in BRANCH.finditer(comment):
        slug = m.group(1).rstrip(".")
        if "-" not in slug or FILE_EXTENSION.search(slug) or comment.startswith("/", m.end()):
            continue
        found.append(("branch", m.group(0).rstrip(".")))
    found += [("branch", m.group(0)) for m in RELEASE_PLEASE.finditer(comment)]
    return found


# Cheap first pass over a whole file, so only the few files that could hold a mention are
# tokenised: the tokeniser walks characters in Python, and the tree is ~45 MB of source.
PREFILTER = re.compile(
    "|".join(
        p.pattern
        for p in (BEADS, BEADS_BSG, PR_WORD, PR_SUFFIX, PR_URL, PR_BARE, BRANCH, RELEASE_PLEASE)
    ),
    re.IGNORECASE,
)

# --- where the comments are ------------------------------------------------------------------

C_LIKE = {".js", ".mjs", ".cjs", ".ts", ".c", ".h", ".cc", ".cpp", ".hpp", ".hlsl", ".hlsli",
          ".wgsl", ".java", ".jsonc", ".frag", ".vert", ".glsl"}
DIALECTS = {
    ".rs": "rust", ".py": "python", ".sh": "hash", ".bash": "hash",
    ".yml": "hash", ".yaml": "hash", ".toml": "python",
    ".rego": "rego", ".lua": "lua", ".typ": "typ",
    **{ext: "c" for ext in C_LIKE},
}
# Generic tokeniser settings: line-comment markers, block-comment pairs, and string delimiters as
# (open, close, has escapes, may span lines).
GENERIC = {
    "rego": (("#",), (), (('"', '"', True, False), ("`", "`", False, True))),
    "lua": (("--",), (("--[[", "]]"),), (('"', '"', True, False), ("'", "'", True, False), ("[[", "]]", False, True))),
    "c": (("//",), (("/*", "*/"),), (('"', '"', True, False), ("'", "'", True, False), ("`", "`", True, True))),
    # Typst has no single-quoted strings: an apostrophe there is prose.
    "typ": (("//",), (("/*", "*/"),), (('"', '"', True, False),)),
}
VENDORED = ("third_party/", "vendor/")
SHEBANG = re.compile(r"^#!.*\b(?:ba)?sh\b|^#!.*\bpython")


def dialect_of(rel: str, text: str) -> str | None:
    path = Path(rel)
    if path.suffix in DIALECTS:
        return DIALECTS[path.suffix]
    if path.name in {".gitignore", ".gitattributes"}:
        return "hash"
    if not path.suffix:
        first = text.split("\n", 1)[0]
        if m := SHEBANG.match(first):
            return "python" if "python" in m.group(0) else "hash"
    return None


def generic_comment_spans(text: str, dialect: str) -> list[tuple[int, str]]:
    line_markers, blocks, strings = GENERIC[dialect]
    spans: list[tuple[int, str]] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        block = next((b for b in blocks if text.startswith(b[0], i)), None)
        if block:
            end = text.find(block[1], i + len(block[0]))
            end = n if end < 0 else end + len(block[1])
            for offset, body in enumerate(text[i:end].split("\n")):
                spans.append((line + offset, body))
            line += text.count("\n", i, end)
            i = end
            continue
        if any(text.startswith(m, i) for m in line_markers):
            end = text.find("\n", i)
            end = n if end < 0 else end
            spans.append((line, text[i:end]))
            i = end
            continue
        string = next((s for s in strings if text.startswith(s[0], i)), None)
        if string:
            opener, closer, escapes, multiline = string
            j = i + len(opener)
            while j < n:
                if escapes and text[j] == "\\":
                    j += 2
                    continue
                if text.startswith(closer, j):
                    j += len(closer)
                    break
                if not multiline and text[j] == "\n":
                    break  # an unterminated quote is an apostrophe, not a string
                j += 1
            line += text.count("\n", i, j)
            i = j
            continue
        i += 1
    return spans


def comment_spans(rel: str, text: str) -> list[tuple[int, str]]:
    dialect = dialect_of(rel, text)
    if dialect is None:
        return []
    if dialect in GENERIC:
        return generic_comment_spans(text, dialect)
    spans = CC.comment_spans(text, dialect)
    # Docstrings are Python's alone: a TOML file shares the `#` tokeniser but has none.
    if dialect == "python" and Path(rel).suffix in {".py", ""}:
        spans += CC.docstring_spans(text)
    return sorted(set(spans))


def scan_text(rel: str, text: str) -> list[tuple[int, str, str, str]]:
    """(line, kind, mention, comment) for each mention in the comments of `text`."""
    if not PREFILTER.search(text):
        return []
    out = []
    for line, comment in comment_spans(rel, text):
        for kind, mention in mentions(comment):
            out.append((line, kind, mention, comment.strip()))
    return out


def tracked_paths(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=GIT_TIMEOUT_SECONDS,
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def read_text(root: Path, rel: str) -> str | None:
    if rel.startswith(VENDORED) or rel.endswith(".md"):
        return None
    path = root / rel
    if not path.is_file() or path.is_symlink():
        return None
    data = path.read_bytes()
    if b"\0" in data[:BINARY_PROBE_BYTES]:
        return None
    # UTF-8 Lossy: the scan reads comments for a pattern; a stray byte cannot form one.
    return data.decode("utf-8", "replace")


def scan_tree(root: Path, only: list[str] | None = None) -> list[tuple[str, int, str, str, str]]:
    findings = []
    for rel in only if only else tracked_paths(root):
        text = read_text(root, rel)
        if text is None or dialect_of(rel, text) is None:
            continue
        findings += [(rel, *hit) for hit in scan_text(rel, text)]
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="scan only these repo-relative files")
    ap.add_argument("--count", action="store_true", help="totals per kind and per file, exit 0")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    findings = scan_tree(REPO_ROOT, args.paths or None)
    if args.count:
        kinds = collections.Counter(f[2] for f in findings)
        files = collections.Counter(f[0] for f in findings)
        for rel, n in files.most_common():
            print(f"{n:5d}  {rel}")
        print(f"{len(findings):5d}  total in {len(files)} files: {dict(kinds)}")
        return 0
    for rel, line, kind, mention, comment in findings:
        print(f"{rel}:{line}: {kind} `{mention}`: {comment[:120]}")
    if findings:
        print(
            f"\n{len(findings)} mention(s) of a beads id, pull request or branch in comments.\n"
            "Comments carry no beads ids, PR numbers or branch names (user request 2026-11-24):\n"
            "say what the code does and why; the history lives in git and bd.",
            file=sys.stderr,
        )
        return 1
    print("check-no-tracker-refs: ok, no beads ids, pull requests or branches in comments")
    return 0


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def kinds(comment: str) -> list[str]:
        return [k for k, _ in mentions(comment)]

    bd = BD_PREFIXES[0]
    br = "fe" + "at"  # assembled, like the patterns, so no comment here carries one

    # Positives.
    check("a beads id", kinds(f"// see {bd}-a1b2 for the run"), ["beads"])
    check("a 3-character beads id", kinds(f"# ({bd}-wmw)"), ["beads"])
    check("a 5-character beads id", kinds(f"// {bd}-abc12"), ["beads"])
    check("a child beads id", mentions(f"// {bd}-a1b2.3 defect")[0][1], f"{bd}-a1b2.3")
    check("the old prefix", kinds(f"// {BD_PREFIXES[1]}-x9z"), ["beads"])
    check("a backseat-gamer id", kinds("# filed as bsg-" + "chy"), ["beads"])
    check("PR and a number", kinds("// landed in PR #" + "398"), ["pr"])
    check("PR without the hash", kinds("// PR " + "12 fixed it"), ["pr"])
    check("PR-# glued", kinds("//! the PR-#" + "103 skip"), ["pr"])
    check("PRs and two numbers", kinds("# PRs #" + "322 and #" + "323"), ["pr", "pr"])
    check("pull request and a number", kinds("# opened draft pull request #" + "481"), ["pr"])
    check("the squash-merge suffix", kinds("// feat(x): thing (#" + "62)"), ["pr"])
    check("a pull link", kinds("// https://github.com/o/r/pull/" + "5"), ["pr"])
    check("a bare three-digit number", kinds("// the #" + "362 merge"), ["pr"])
    check("a bare number at a line end", kinds("// since #" + "317"), ["pr"])
    check("a branch", kinds(f"// on `{br}/ai-lab-turtles`"), ["branch"])
    check("a fix branch", kinds("# pushed fi" + "x/harness-repl-reach today"), ["branch"])
    check("a branch then a full stop", mentions(f"# publishes {br}/load-x.")[0][1], f"{br}/load-x")
    check("release-please's branch", kinds("# release-please--branches--" + "main"), ["branch"])

    # False positives the corpus showed, which must stay quiet.
    check("a docs path", kinds("// see docs/agents/runtime-evidence.md"), [])
    check("a docs file with a hyphen", kinds("# docs/ci-gate-portability.tsv row"), [])
    check("a test fixture path", kinds("// test/fixtures/x.json"), [])
    check("a ci path", kinds("# ci/run-all.sh"), [])
    check("a build dir", kinds("# build/out-dir"), [])
    check("a prefix inside a path", kinds("// crates/fix/load-thing.rs"), [])
    check("a branch-shaped file path", kinds(f"// {br}/load-thing.rs"), [])
    check("a branch-shaped dir", kinds(f"// {br}/load-thing/mod.rs"), [])
    check("prose with a slash", kinds("// a fix/instrument run, the refactor/move gate"), [])
    check("cleanup/destructor", kinds("/// cleanup/destructor body"), [])
    check("R1 #1", kinds("# the R1 #1 first hit"), [])
    check("a two-digit counter", kinds("// run #14 stalled"), [])
    check("a four-digit counter", kinds("// CALL #13800 epoch=1"), [])
    check("a three-digit counter after a counting word", kinds("// hit #370 and kick #200"), [])
    check("a list of counters", kinds("// lines at #1,#2,#64,#128,#256,#512"), [])
    check("a hex color", kinds("# `#111216` at 0.94, #808080808080 gray"), [])
    check("a material tag", kinds("//! `#27#_Cloth`"), [])
    check("bsg words", kinds("# bsg-check and bsg-work"), [])
    check("another project's name", kinds("# frida-up and ai-lab and data-write"), [])
    check("a fixture id that is too long", kinds(f"// {bd}-toolong"), [])
    check("an upstream issue link", kinds("// https://github.com/rust-lang/rust/issues/" + "123"), [])
    check("a placeholder", kinds("// PR #<n> and (#<n>) and <prefix>/<slug>"), [])
    check("a counter quoted from a log", kinds("/// `cancelled rejected match (#1) -- ...`"), [])
    check("a run id", kinds("// run pr" + "109-boot-oscancel-20260730"), [])
    check("a profile file name", kinds("# /home/u/Elden/pr" + "190-invasion-warp.me3"), [])
    check("PR glued with a hyphen is still one", kinds("// the PR-" + "117 black screen"), ["pr"])

    # Only comments: the tokenisers must not read inside strings.
    rs = f'let s = "PR #{3}98 {bd}-a1b2";\n// see PR #' + "398\n"
    check("rust: the string is code, the comment is read", [h[0] for h in scan_text("a.rs", rs)], [2])
    py = (
        f'"""Module docstring citing {bd}-a1b2."""\n'
        f'BRANCH = "{br}/fixture-branch"  # a comment citing {br}/real-branch\n'
        "def f():\n"
        f'    """Nested docstring: PR #' + "398.\"\"\"\n"
    )
    check("python: docstrings and comments, not strings", [(h[0], h[1]) for h in scan_text("a.py", py)],
          [(1, "beads"), (2, "branch"), (4, "pr")])
    sh = f'git push origin {br}/test-fixture  # pushes the fixture\n# from {br}/real-one\necho "$#"\n'
    check("shell: a functional branch is code", [h[0] for h in scan_text("a.sh", sh)], [2])
    rego = f'x := `# {br}/not-a-comment`\n# see PR #' + '398\ny := "# PR #' + '398"\n'
    check("rego: raw and quoted strings are code", [h[0] for h in scan_text("a.rego", rego)], [2])
    js = f'const s = `// {br}/in-template`;\n/* block\n   PR #' + "398 */\n// ok\n"
    check("js: template strings are code, block comments are read", [h[0] for h in scan_text("a.js", js)], [3])
    lua = f'local s = "-- PR #' + '398"\n-- see PR #' + "398\n"
    check("lua", [h[0] for h in scan_text("a.lua", lua)], [2])
    yml = f'run: git push origin {br}/ci-fixture\n# from PR #' + "398\n"
    check("yaml", [h[0] for h in scan_text("a.yml", yml)], [2])
    hook = "#!/usr/bin/env bash\n# landed in PR #" + "398\n"
    check("an extensionless shell hook", [h[0] for h in scan_text("pre-push", hook)], [2])
    check("markdown is out of scope", dialect_of("README.md", "# PR"), None)
    check("a data file has no comments", dialect_of("data/x.json", "{}"), None)

    # End to end through git: tracked files only, markdown and vendored skipped.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], check=True, timeout=GIT_TIMEOUT_SECONDS)
        (root / "a.rs").write_text("// see PR #" + "398\nfn f() {}\n", encoding="utf-8")
        (root / "b.md").write_text("PR #" + "398\n", encoding="utf-8")
        (root / "third_party").mkdir()
        (root / "third_party" / "c.rs").write_text("// PR #" + "398\n", encoding="utf-8")
        (root / "untracked.rs").write_text("// PR #" + "398\n", encoding="utf-8")
        subprocess.run(git + ["add", "a.rs", "b.md", "third_party/c.rs"], check=True, timeout=GIT_TIMEOUT_SECONDS)
        check("only tracked, unvendored source counts", [(f[0], f[1]) for f in scan_tree(root)], [("a.rs", 1)])
        (root / "a.rs").write_text("// see the history in git\nfn f() {}\n", encoding="utf-8")
        check("a clean tree has none", scan_tree(root), [])

    # This file names what it reads without carrying any of it.
    check("this file's comments are clean", scan_text("scripts/check-no-tracker-refs.py",
                                                       Path(__file__).read_text("utf-8")), [])

    for f in failures:
        print(f"selftest FAILURE {f}", file=sys.stderr)
    print(f"check-no-tracker-refs selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
