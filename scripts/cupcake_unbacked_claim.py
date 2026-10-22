"""Detect a closing message that claims a repo artifact was built when the turn wrote no file.

Sibling of the unexecuted-promise scan: that one catches "I'll build it" ending in nothing, this
one catches "I built it" when nothing was built. See
.cupcake/signals/last_assistant_unbacked_claim.sh for the incident that forced it.

Kept as an importable module rather than inline heredoc so it is unit-testable without a live
transcript -- the sibling guard's logic lives in cupcake_turn_scan.py for the same reason.
"""

from __future__ import annotations

import re

# First-person completion verbs. Present-perfect and simple past only: a future promise is the
# sibling guard's job, and matching both here would double-halt one turn.
#
# Only verbs that say something now exists. "removed" and "deleted" used to be here, and a deletion
# claims the opposite of what this guard checks: on 2026-10-20 it halted "I deleted the agent's
# temporary 2.6 GB test home." in a turn that ran `rm -rf .../target/fakehome`, demanding proof that
# an artifact exists which the sentence said was gone. Removal verbs (deleted, removed, cleared,
# rm'd, dropped, purged, wiped) are deliberately absent.
_CLAIM = re.compile(
    r"\bI(?:'ve| have)?\s+(?:just\s+|already\s+)?"
    r"(built|added|created|wrote|written|wired|landed|shipped|implemented|patched|updated|"
    r"moved|made|taught|extended|hooked)\b",
    re.IGNORECASE,
)

# The claim only counts when its object is something in this repo. A claim about the game, a run,
# or an external service is not what this guard is for.
_ARTIFACT = re.compile(
    r"(?:\b(?:scripts|crates|tools|docs|\.cupcake)/[\w./-]+"
    r"|\b[\w.-]+\.(?:py|rego|rs|toml|sh|yml|yaml|json|md)\b"
    r"|\b(?:gate|check|hook|policy|guard|selftest|test|script|lint|signal|rulebook)s?\b)",
    re.IGNORECASE,
)

# An honest confession of absence must never be punished -- it is the behaviour being asked for.
_DISCLAIMED = re.compile(
    r"\b(?:no|not|nothing|never|neither)\b[^.]{0,80}?"
    r"\b(?:built|created|exists?|wrote|written|added|shipped|landed|implemented|wired)\b"
    r"|\bI\s+(?:have\s+not|haven't|did\s+not|didn't)\b"
    r"|\bstill\s+(?:need|needs|to\s+be)\b"
    r"|\bunbuilt\b|\bnot\s+(?:yet\s+)?(?:built|written|created|wired)\b",
    re.IGNORECASE,
)

# Bash constructs that actually put bytes on disk. `bd remember` is deliberately absent: recording
# a memory instead of doing the work is the exact substitution this guard exists to catch.
_BASH_WRITE = re.compile(
    r">>?\s*[\w./~$-]"          # redirect into a file
    r"|\btee\b"
    r"|\bsed\b[^|;]*\s-i\b"
    r"|<<\s*'?[A-Z]"            # heredoc (python3 - <<'PY', cat > f <<'EOF')
    r"|\b(?:cp|mv|install|patch|touch|mkdir)\b"
    r"|\bgit\s+(?:apply|checkout|revert|restore|merge|cherry-pick)\b"
    r"|\bchmod\b",
)

_WRITE_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")

# Punctuation that closes whatever came before it, so the next word begins a clause. Markdown
# emphasis markers and the table pipe are in the set because this repo's prose is full of both.
_CLAUSE_BREAK = set(".,;:!?()[]{}*_|\"'—–-\n")

# Words that introduce a clause instead of being modified by one. Deliberately excludes the
# relativizers "that", "which" and "who": those are the explicit spelling of the shape below.
_CLAUSE_OPENER = {
    "and", "but", "so", "or", "yet", "nor", "then", "because", "since", "after", "before",
    "when", "while", "if", "once", "though", "although", "however", "therefore", "thus",
    "meanwhile", "instead", "plus",
}


def _opens_a_clause(before: str) -> bool:
    """True when a claim starting where `before` ends is asserting, not referring back.

    Measured over 2,370 real turns on 2026-09-09, the first time this guard could fire at all: it hit
    ten turns and nine of them read "the gate I wrote", "the staleness test I shipped", "the function
    I hooked", "an importer I built". Those are relative clauses with the relativizer dropped, and
    grammatically they do the opposite of what the guard is looking for -- they presuppose the
    artifact as already given and go on to say something else about it, which is ordinary reporting
    prose. Only "I wrote a cupcake guard" asserts the creation, and assertion is the whole violation.

    The test is positional, not semantic: the pronoun must open its clause. Start of text, any
    closing punctuation, or a conjunction before it means it does. An ordinary word before it means
    the pronoun is inside a noun phrase, so the sentence refers rather than claims.
    """
    tail = before.rstrip()
    if not tail:
        return True
    if tail[-1] in _CLAUSE_BREAK:
        return True
    return tail.split()[-1].lower().strip("".join(_CLAUSE_BREAK)) in _CLAUSE_OPENER


def _asserts_completion(sentence: str) -> bool:
    """True when the sentence carries a completion claim that opens its own clause."""
    return any(_opens_a_clause(sentence[: m.start()]) for m in _CLAIM.finditer(sentence))


def strip_quoted(text: str) -> str:
    """Remove fenced/backticked/quoted spans so quoting a claim cannot trip the guard."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r'"[^"]{0,400}"', " ", text)
    return text


def turn_wrote_a_file(events: list[dict]) -> bool:
    """True when any tool_use in the turn actually mutated the working tree."""
    for ev in events or []:
        for block in (ev.get("message", {}) or {}).get("content", []) or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") in _WRITE_TOOLS:
                return True
            if block.get("name") == "Bash":
                cmd = (block.get("input") or {}).get("command", "")
                if isinstance(cmd, str) and _BASH_WRITE.search(cmd):
                    return True
    return False


def claim_sentences(text: str) -> list[str]:
    """Sentences in `text` that assert a repo artifact was built, minus disclaimed ones."""
    hits = []
    for raw in re.split(r"(?<=[.!?;])\s+|\n", strip_quoted(text or "")):
        sentence = raw.strip()
        if not sentence or _DISCLAIMED.search(sentence):
            continue
        if _asserts_completion(sentence) and _ARTIFACT.search(sentence):
            hits.append(" ".join(sentence.split())[:220])
    return hits


# A path named in prose or in a command: anything with a slash, or a bare name with an extension.
_PATH_TOKEN = re.compile(
    r"~?/?(?:[\w.@+-]+/)+[\w.@+-]+"
    r"|\b[\w@+-][\w.@+-]*\.[A-Za-z]\w{0,7}\b"
)

# Read-only commands whose success says the named path exists. Deliberately narrow: `cat`, `head`
# and friends also fail on a missing file, but nobody runs them to check existence, and a short
# list keeps the rule easy to reason about.
_EXISTENCE_CHECK = re.compile(r"(?:^|[;&|(]\s*|\bthen\s+)(?:ls|stat|test|\[\[?)\s")

# Lines in a tool result that report a path is absent.
_MISSING = re.compile(r"No such file|cannot access|cannot stat|not found", re.IGNORECASE)


def path_tokens(text: str) -> set[str]:
    """Path-like tokens in `text`, with surrounding quotes and sentence punctuation removed."""
    out = set()
    for m in _PATH_TOKEN.finditer(text or ""):
        tok = m.group(0).strip("'\"`.,;:()")
        if not tok or tok.startswith(("http:", "https:")) or not re.search(r"\w", tok):
            continue
        if _looks_like_a_file(tok):
            out.add(tok)
    return out


# Prose is full of tokens the bare pattern above admits and that name no file: `e.g`, `1.17`,
# `and/or`, `read/write`. A token counts only with a known extension or a path-shaped head.
_FILE_EXT = re.compile(
    r"\.(?:py|rego|rs|toml|sh|ya?ml|jsonl?|md|js|ts|txt|log|tsv|csv|dll|exe|html?|java|lock|"
    r"cfg|ini|me3|bin|gzf|sl2|co2)$",
    re.IGNORECASE,
)
_PATH_HEAD = re.compile(
    r"^(?:/|~/|\.\.?/|(?:scripts|crates|tools|docs|data|target|vendor-archive|\.cupcake|\.auto|"
    r"\.claude|\.beads|\.worktrees|\.github)/)"
)


def _looks_like_a_file(tok: str) -> bool:
    return bool(_FILE_EXT.search(tok) or ("/" in tok and _PATH_HEAD.search(tok)))


def _same_file(a: str, b: str) -> bool:
    """True when two spellings name the same file: equal, or one is a `/`-bounded suffix of the
    other -- `scripts/x.py` against `/home/u/repo/scripts/x.py`, or a bare `x.py` against either."""
    a = a.rstrip("/").removeprefix("./")
    b = b.rstrip("/").removeprefix("./")
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_.endswith("/" + short)


def _tool_results(session_events: list[dict]) -> dict[str, tuple[bool, str]]:
    """tool_use_id -> (is_error, result text), read out of the user-side carrier events."""
    out: dict[str, tuple[bool, str]] = {}
    for ev in session_events or []:
        content = (ev.get("message", {}) or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            body = block.get("content")
            if isinstance(body, list):
                body = "\n".join(
                    b.get("text", "") for b in body if isinstance(b, dict) and b.get("type") == "text"
                )
            out[str(block.get("tool_use_id"))] = (bool(block.get("is_error")), str(body or ""))
    return out


def _tool_uses(events: list[dict]) -> list[dict]:
    blocks = []
    for ev in events or []:
        for block in (ev.get("message", {}) or {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                blocks.append(block)
    return blocks


def session_written_paths(session_events: list[dict]) -> set[str]:
    """Every path a Write/Edit/NotebookEdit, or a Bash call carrying a write construct, put on disk
    at any point in the session -- not only in the turn being judged.

    The turn-only view refused a closing message naming a script that a Write had created one turn
    earlier, after a task notification opened a new turn (2026-10-19). A file the agent created in
    this session does not stop existing at a turn boundary. A call whose result came back as an
    error wrote nothing and is not counted. For Bash every path token in a writing command counts,
    which over-credits a `cp a b` source; the closing message still has to name that exact path.
    """
    results = _tool_results(session_events)
    written: set[str] = set()
    for block in _tool_uses(session_events):
        is_error, _ = results.get(str(block.get("id")), (False, ""))
        if is_error:
            continue
        name = block.get("name")
        inp = block.get("input") or {}
        if name in _WRITE_TOOLS:
            for key in ("file_path", "notebook_path"):
                if isinstance(inp.get(key), str):
                    written.add(inp[key])
        elif name == "Bash":
            cmd = inp.get("command", "")
            if isinstance(cmd, str) and _BASH_WRITE.search(cmd):
                written |= path_tokens(cmd)
    return written


def turn_verified_paths(turn_events: list[dict], session_events: list[dict]) -> set[str]:
    """Paths a read-only check in the judged turn showed to exist: a Read that succeeded, or an
    `ls` / `stat` / `test -e` / `[ -e ]` whose result did not report that path missing.

    A tool call with no result in the transcript proves nothing and is treated as a failure.
    """
    results = _tool_results(session_events)
    verified: set[str] = set()
    for block in _tool_uses(turn_events):
        is_error, body = results.get(str(block.get("id")), (True, ""))
        name = block.get("name")
        inp = block.get("input") or {}
        if name == "Read":
            if not is_error and isinstance(inp.get("file_path"), str):
                verified.add(inp["file_path"])
            continue
        if name != "Bash":
            continue
        cmd = inp.get("command", "")
        if not isinstance(cmd, str) or not _EXISTENCE_CHECK.search(cmd):
            continue
        missing_lines = [line for line in body.splitlines() if _MISSING.search(line)]
        for tok in path_tokens(cmd):
            base = tok.rstrip("/").rsplit("/", 1)[-1]
            if any(base in line for line in missing_lines):
                continue
            # A failed command proves nothing about a path its output does not list; a successful
            # one, or one whose output lists the path, does.
            if not is_error or base in body:
                verified.add(tok)
    return verified


def _backed(named: set[str], known: set[str]) -> bool:
    return bool(named) and all(any(_same_file(n, k) for k in known) for n in named)


def offending_claim(
    final_text: str, events: list[dict], session_events: list[dict] | None = None
) -> str:
    """The first unbacked completion claim in the closing prose, or '' when the turn is clean.

    `events` carries the judged turn's tool calls and `session_events` the whole transcript. A
    claim is backed when the turn wrote any file, or when every path it names -- or, for a sentence
    naming none, every path the closing prose names -- was written earlier in the session or shown
    to exist by a read-only check in this turn. A claim with no path anywhere in the closing
    message still needs a write in this turn, as before.
    """
    if turn_wrote_a_file(events):
        return ""
    hits = claim_sentences(final_text)
    if not hits:
        return ""
    if session_events is None:
        session_events = events
    known = session_written_paths(session_events) | turn_verified_paths(events, session_events)
    closing_paths = path_tokens(final_text)
    for hit in hits:
        named = path_tokens(hit) or closing_paths
        if not _backed(named, known):
            return hit
    return ""
