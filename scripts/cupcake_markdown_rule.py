#!/usr/bin/env python3
"""Classifier for the no_markdown_rule Stop guard: a closing message that calls text in a markdown
file a rule.

User correction 2026-11-11, session 5ec7b779: "This is probably the 100th time I've told you that its
logically inconsistent to use the word rule in reference to markdown files". An instruction file, a
memory or a note is advisory: an agent can miss it or ignore it, and nothing stops the turn that
does. Only executable enforcement (a policy, a test, a hook) binds. A markdown entry is guidance, a
note, a convention, or a description of what a guard enforces, so calling it a rule claims a force
it does not have. The closers it was written from, all in that session:

  ... and the rule is in `docs/agents/conventions.md` so later runs keep it.    (2026-11-11 20:21Z)
  The rule is now in `docs/agents/runtime-evidence.md`.                         (2026-11-11 21:15Z)
  AGENTS.md now carries the same rule.                                          (2026-11-10 22:11Z)

and, in narration and commit text, "I'm writing the rule into AGENTS.md", "docs/agents/runtime-
evidence.md carries the written rule" and "The rule and the record behind it are in
`docs/agents/runtime-evidence.md`".

The closing message is judged sentence by sentence, and a markdown table row cell by cell and as a
whole. Fenced code, blockquotes, and quoted spans ("...", '...', curly quotes) are set aside first,
so the word itself can be named in quotes. A markdown referent is a `.md` path (inline code or bare),
AGENTS.md / CLAUDE.md, a `docs/...` path, a heading in backticks, or a phrase such as "the doc", "the
notes", "the instructions", "a doc entry", "markdown text". A path whose own file name contains
"rule" is not one: it is a saved copy of someone's rule set (a PvP ladder's), and its text is rules.

What halts:

- the markdown referent names the rule: "the AGENTS.md rule", "AGENTS.md's rule", "the doc rule",
  or the referent and the word are at most five words apart with nothing between them that
  describes ("AGENTS.md now carries the same rule", "What stayed in AGENTS.md: the rules");
- the rule is put in the referent: "the rule is in docs/...", "the rule into AGENTS.md", "two rules
  to AGENTS.md", "a rule in the notes", "rules go in AGENTS.md", "Rule: docs/...", up to eight words
  between the word and the preposition and four between the preposition and the referent;
- "written rule", anywhere outside quotes;
- a table row whose first cell is a markdown referent and whose other cell says "rule", or the
  reverse ("| `frida.md` | 10 KB | Frida setup and watchpoint rules |").

What passes:

- a rule that belongs to something executable or to the game: the words of its own noun phrase name
  it ("a rego rule", "the policy rule", "a lint rule", "the guard's rule", "the `.gitignore` rule",
  "the game's stacking rule", "the record's resolve rule", "the hit-priority rule"), or a rule id or
  inline-code name stands right after it ("rule `ER-EFFECTS-X`", "the rule in `x.rego`");
- a markdown file that describes, documents, explains, mentions or points at a rule ("ashes-of-
  war.md describes the punish rule");
- "rule out", "rules of thumb", "rule_id", and "rule" naming a guard or test ("the markdown-rule
  guard");
- "rule" with no markdown referent in its sentence: a sentence that leans on the previous one ("I
  moved sections word for word and didn't reword any rules") is not caught, by choice, because
  reading across sentences finds game rules beside unrelated doc paths far more often.

`--selftest` runs the cases below. `--replay [--limit N] [--dir D] [--all]` runs the classifier over
the closing message of every turn in the N newest transcripts (default 40) and prints each one it
would halt; `--all` also prints every allowed sentence that says "rule".
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

# --- referents ------------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_DQUOTE_RE = re.compile(r"\"[^\"\n]{0,400}\"|“[^”\n]{0,400}”")
# A straight single quote opens a span only where an apostrophe cannot stand: not after a letter,
# digit or another quote, so "AGENTS.md's rule" and "agents' rule" keep their possessives.
_SQUOTE_RE = re.compile(r"(?<![\w'’])'[^'\n]{1,200}'(?!\w)|‘[^’\n]{0,200}’")

# The inside of an inline-code span that names a markdown file, a docs/ path or a heading.
# A docs/ path counts as a directory of markdown only when no component has an extension, so
# `docs/ci-gate-portability.tsv` is a data file, not a referent.
_CODE_MD_RE = re.compile(
    r"^(?:#{1,6}\s.*|(?:~?/)?(?:[\w.@ -]+/)*[\w.@-]+\.md(?:#[\w-]+)?|(?:~?/)?(?:[\w.@-]+/)*docs(?:/[\w@-]+)*/?"
    r"|AGENTS|CLAUDE|MEMORY)$",
    re.IGNORECASE,
)
_BARE_MD_RE = re.compile(
    r"(?<![\w./@-])(?:~?/)?(?:[\w.@-]+/)*[\w.@-]+\.md\b(?:#[\w-]+)?"
    r"|(?<![\w./@-])(?:~?/)?(?:[\w.@-]+/)*docs/(?:[\w@-]+/)*(?:[\w@-]+/?)?(?![\w@/-]|\.\w)"
)
# "a note" and "two notes:" announce a remark far more often than they name a file, so a lone note,
# doc or instruction needs a definite determiner; the file-shaped nouns take any.
_DEF = r"(?:the|this|that|these|those|our|its|your|my|their|[\w-]+['’]s)"
_DET = rf"(?:{_DEF}|a|an|each|every)"
_MOD = (
    r"(?:(?:written|markdown|instruction|instructions|agent|agents|project|repo|repository|global|local"
    r"|guidance|conventions?|runtime|evidence|session|handoff|design|frida|ghidra|seamless|kitty"
    r"|on-demand|read-on-demand|same|new|old|relevant|existing)[\s-]+){0,2}"
)
_NOUN_DEF = r"(?:docs?|notes?|instructions?|readme|write-ups?)"
_NOUN_DET = r"(?:instruction files?|markdown files?|markdown entr(?:y|ies)|doc entr(?:y|ies))"
_NOUN = (
    r"(?:docs|instructions|instruction files|doc entries"
    r"|markdown\s+(?:files?|text|entr(?:y|ies)|notes?|docs?|prose|sections?|paragraphs?))"
)
_DOC_RE = re.compile(
    rf"(?<![/.-])\b(?:{_DEF}\s+{_MOD}{_NOUN_DEF}|{_DET}\s+{_MOD}(?:{_NOUN}|{_NOUN_DET})|{_MOD}{_NOUN})\b"
    r"(?![/.]\w)"  # part of a path or file name
    r"(?![-\s]*(?:site|string|strings|comment|comments|page|pages|test|tests|build|generator|url|link"
    r"|links|server|tool|tooling)\b)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"\b(MDREF|CODEREF|QUOTE)(\d+)\b")

# --- the word -------------------------------------------------------------------------------------

RULE_RE = re.compile(
    r"(?<![-/.])\brules?\b"  # not a crate or file name ("er-duel-rules") or a compound ("node-rule")
    r"(?!\s+(?:\w+\s+)?out\b)"  # rule out, rule those out
    r"(?!\s+of\s+thumb\b)"
    r"(?![-_](?:guard|policy|policies|signal|check|test|tests|classifier|fixture|selftest|ids?)\b)",
    re.IGNORECASE,
)

# Words of the rule's own noun phrase that make it an executable or a game rule.
_QUALIFIERS = {
    # executable
    "rego", "opa", "policy", "policies", "guard", "guards", "cupcake", "lint", "linter", "linting",
    "clippy", "ruff", "shellcheck", "eslint", "prettier", "rustfmt", "gitignore", ".gitignore", "ignore",
    "regex", "regexp", "grammar", "parser", "parsing", "css", "hook", "hooks", "signal", "signals",
    "stop-guard", "gate", "gates", "ci", "firewall", "udev", "systemd", "nftables", "iptables",
    "routing", "matcher", "matching", "classifier", "validator", "schema", "halt", "deny", "allow",
    "cfg", "cargo", "compiler", "type", "borrow", "lifetime", "orphan", "coherence", "precedence",
    "tokenizer", "filter", "cron", "check", "checker", "test", "selftest", "script", "scripts",
    # game and model
    "game", "games", "engine", "param", "params", "pvp", "duel", "duels", "ladder", "tournament",
    "league", "stacking", "damage", "hit", "hit-record", "hit-priority", "scoring", "score", "selection",
    "aim", "punish", "resolve", "mount", "ash-mount", "stance", "poise", "buff", "buffs", "scaling",
    "frame", "roll", "dodge", "netcode", "matchmaking", "summon", "invasion", "targeting", "stamina",
    "parry", "riposte", "stagger", "combo", "cancel", "tracking", "homing", "release", "follow-up",
    "displacement", "refinement", "strata", "node", "kit", "model", "record", "spell", "skill",
    "weapon", "talisman", "family", "stop", "archetype", "re-roll", "infusion", "affinity",
}
# Possessives that do not make the rule executable: a person's or a file's rule is prose.
_PROSE_OWNERS = {"agent", "agents", "user", "users", "session", "doc", "docs", "note", "notes", "file", "repo", "my", "your"}
# Words that end the rule's noun phrase when walking back from it.
_NP_STOP = {
    "the", "a", "an", "this", "that", "these", "those", "its", "our", "their", "your", "my", "his",
    "her", "and", "or", "but", "of", "in", "into", "to", "from", "with", "by", "for", "on", "at", "is",
    "are", "was", "were", "be", "been", "as", "under", "inside", "within", "no", "any", "each", "every",
    "which", "what", "one", "two", "three", "carries", "carry", "has", "have", "holds", "hold",
}
_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+$")
_WORD_RE = re.compile(r"(?:MDREF|CODEREF|QUOTE)\d+|[\w'’.-]+")

DESCRIBE_RE = re.compile(
    r"\b(?:describ\w*|document\w*|explain\w*|summari[sz]\w*|mention\w*|referenc\w*|cites?|cited|citing"
    r"|links?|linked|linking|points?|pointed|pointing|names?|named|naming|discuss\w*|outlin\w*"
    r"|covers?|covered|walks?\s+through|quot\w+)\b",
    re.IGNORECASE,
)
# A word that starts a new clause, so a referent on its other side is not the rule's.
CLAUSE_BREAK_RE = re.compile(
    r"\b(?:but|while|whereas|because|although|though|then|which|when|whenever|if|unless|until|so|since"
    r"|after|before|whose)\b|,\s*(?:and|or|nor|yet)\b|[;—–]|\s--\s",
    re.IGNORECASE,
)
# The referent leads a clause of its own ("..., and AGENTS.md describes it").
_SUBJECT_RE = re.compile(rf"^\s*(?:now\s+|also\s+|only\s+|still\s+)?{DESCRIBE_RE.pattern}", re.IGNORECASE)
ENFORCE_RE = re.compile(r"\b(?:enforc\w*|guard\w*|polic\w+|hooks?|rego|halts?|halted|denies|denied|blocks?|blocked)\b", re.IGNORECASE)
LINK_RE = re.compile(r"\b(?:in|into|to|from|inside|within|under|of|on|at|is|are)\b|[:(=]", re.IGNORECASE)

MAX_B_WORDS = 5
MAX_A_PRE_WORDS = 8
MAX_A_POST_WORDS = 4


def _is_md_code(inner: str) -> bool:
    s = inner.strip().rstrip(".,:;")
    if not s or not _CODE_MD_RE.match(s):
        return False
    return "rule" not in os.path.basename(s.rstrip("/")).lower()


class Unit:
    """One sentence, or one table cell, with its referents and quotes replaced by tokens."""

    def __init__(self, norm: str, originals: dict[str, str]):
        self.norm = norm
        self.originals = originals

    def show(self, text: str | None = None) -> str:
        t = self.norm if text is None else text
        for _ in range(3):  # a quote can hold a token
            t = _TOKEN_RE.sub(lambda m: self.originals.get(m.group(0), m.group(0)), t)
        t = " ".join(t.split())
        return re.sub(r"\s+([.,:;!?)])", r"\1", t).replace("( ", "(")


def _normalize(line: str, originals: dict[str, str]) -> str:
    counter = [len(originals)]

    def keep(kind: str, text: str) -> str:
        key = f"{kind}{counter[0]}"
        counter[0] += 1
        originals[key] = text
        return f" {key} "

    def code(m: re.Match) -> str:
        return keep("MDREF" if _is_md_code(m.group(1)) else "CODEREF", m.group(0))

    line = _INLINE_CODE_RE.sub(code, line)
    line = _DQUOTE_RE.sub(lambda m: keep("QUOTE", m.group(0)), line)
    line = _SQUOTE_RE.sub(lambda m: keep("QUOTE", m.group(0)), line)

    def bare(m: re.Match) -> str:
        text = m.group(0)
        if "rule" in os.path.basename(text.rstrip("/")).lower():
            return keep("CODEREF", text)
        return keep("MDREF", text)

    line = _BARE_MD_RE.sub(bare, line)
    line = re.sub(r"\b(?:AGENTS|CLAUDE)\b(?!\.\w)", lambda m: keep("MDREF", m.group(0)), line)
    line = _DOC_RE.sub(lambda m: keep("MDREF", m.group(0)), line)
    return line


def units(text: str) -> list[tuple[str, list[Unit]]]:
    """[(kind, units)]: kind "sentence" holds one unit, kind "row" one unit per table cell."""
    out: list[tuple[str, list[Unit]]] = []
    for raw in _FENCE_RE.sub("\n", text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(">"):
            continue
        originals: dict[str, str] = {}
        norm = _normalize(line, originals)
        if norm.lstrip().startswith("|"):
            cells = [c.strip() for c in norm.strip().strip("|").split("|")]
            if all(not c or re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            out.append(("row", [Unit(c, originals) for c in cells]))
            continue
        for sent in re.split(r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\betc\.)(?<!\bvs\.)(?<=[.!?])\s+", norm.strip()):
            if sent.strip():
                out.append(("sentence", [Unit(sent, originals)]))
    return out


# --- judging --------------------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def qualified(norm: str, m: re.Match) -> bool:
    """The rule at `m` belongs to something executable or to the game, by its own noun phrase."""
    post = norm[m.end():]
    if re.match(r"^\W{0,3}(?:CODEREF\d+|[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+\b)", post):
        return True
    if re.match(r"^\s*(?:[\w'’-]+\s+){0,2}?(?:in|from|of|at)\s+(?:the\s+)?CODEREF\d+", post, re.IGNORECASE):
        return True
    possessive = False
    for word in reversed(_words(norm[:m.start()])[-5:]):
        w = word.strip(".,:;*_()[]").lower()
        if word.startswith("CODEREF") or _RULE_ID_RE.match(word.strip(".,:;*_()[]")):
            return True
        if word.startswith(("MDREF", "QUOTE")):
            return False
        owner = re.sub(r"['’]s?$", "", w)
        if not owner:  # a bare 's or ' after a token: the token before it owns the rule
            possessive = True
            continue
        if possessive or owner != w:  # a possessive ends the phrase: the rule is its owner's
            return owner in _QUALIFIERS or owner not in _PROSE_OWNERS
        if w in _QUALIFIERS or any(p in _QUALIFIERS for p in w.split("-") if p):
            return True
        if w in _NP_STOP:
            return False
    return False


def _md_before(norm: str, m: re.Match) -> str | None:
    """The markdown referent that names the rule from in front of it, or None."""
    before = norm[:m.start()]
    refs = list(re.finditer(r"MDREF\d+", before))
    if not refs:
        return None
    ref = refs[-1]
    gap = before[ref.end():]
    if "CODEREF" in gap or len(_words(gap)) > MAX_B_WORDS:
        return None
    # "AGENTS.md is unchanged and the rule passes" joins two clauses; every shape this is for
    # ("AGENTS.md now carries the same rule", "the new AGENTS.md rule") has no and/or in between.
    if DESCRIBE_RE.search(gap) or CLAUSE_BREAK_RE.search(gap) or re.search(r"\b(?:and|or)\b", gap, re.IGNORECASE):
        return None
    return ref.group(0)


def _is_file_ref(original: str) -> bool:
    """A referent that names a file or a heading, not a phrase such as "the docs"."""
    return bool(re.search(r"\.md\b|AGENTS|CLAUDE|MEMORY|docs/|^`#", original))


def _md_after(unit: Unit, m: re.Match) -> str | None:
    """The markdown referent the rule is put in, after it, or None."""
    norm = unit.norm
    after = norm[m.end():]
    ref = re.search(r"MDREF\d+", after)
    if not ref:
        return None
    gap = after[:ref.start()]
    if "CODEREF" in gap or DESCRIBE_RE.search(gap) or _SUBJECT_RE.match(after[ref.end():]):
        return None
    # "the rule AGENTS.md sets", "the rule that AGENTS.md gives": a named file straight after the
    # word is the rule's source. A phrase there is not ("the rule docs").
    if re.fullmatch(r"\s*(?:that|which)?\s*", gap, re.IGNORECASE):
        return ref.group(0) if _is_file_ref(unit.originals.get(ref.group(0), "")) else None
    for link in reversed(list(LINK_RE.finditer(gap))):
        pre, post = gap[:link.start()], gap[link.end():]
        # "of" binds only straight after the word ("the rules of AGENTS.md"); further on it belongs
        # to another noun ("its format rules and the UI sections of the design doc").
        if link.group(0).lower() == "of" and _words(pre):
            continue
        if len(_words(pre)) > MAX_A_PRE_WORDS or len(_words(post)) > MAX_A_POST_WORDS:
            continue
        if CLAUSE_BREAK_RE.search(pre) or ENFORCE_RE.search(pre):
            continue
        # "fires on Stop, and AGENTS.md ..." starts a new clause after the link's object.
        if "," in post or CLAUSE_BREAK_RE.search(post):
            continue
        return ref.group(0)
    return None


def _unit_hit(unit: Unit) -> tuple[str, str] | None:
    for m in RULE_RE.finditer(unit.norm):
        if qualified(unit.norm, m):
            continue
        if re.search(r"\bwritten\s+$", unit.norm[:m.start()], re.IGNORECASE):
            return unit.show(), "written rule"
        ref = _md_before(unit.norm, m) or _md_after(unit, m)
        if ref:
            return unit.show(), unit.originals.get(ref, ref)
    return None


def _bare_rule(unit: Unit) -> bool:
    return any(not qualified(unit.norm, m) for m in RULE_RE.finditer(unit.norm))


def _verdict(kind: str, us: list[Unit]) -> str | None:
    """The markdown referent this sentence or row calls a rule, or None."""
    for u in us:
        hit = _unit_hit(u)
        if hit:
            return hit[1]
    if kind == "row" and len(us) > 1:
        first, rest = us[0], us[1:]
        ref = re.search(r"MDREF\d+", first.norm)
        if ref and any(_bare_rule(u) for u in rest):
            return first.originals.get(ref.group(0), ref.group(0))
        refs = [r for r in (re.search(r"MDREF\d+", u.norm) for u in rest) if r]
        if refs and _bare_rule(first):
            return first.originals.get(refs[0].group(0), refs[0].group(0))
    return None


def verdicts(closing: str) -> list[tuple[str, str | None]]:
    """(sentence or row as written, referent or None) for every unit that says rule."""
    out = []
    for kind, us in units(closing):
        if any(RULE_RE.search(u.norm) for u in us):
            out.append((" | ".join(u.show() for u in us), _verdict(kind, us)))
    return out


def markdown_rule(closing: str) -> tuple[str, str] | None:
    """(sentence, referent) for the first place the message calls markdown text a rule, else None."""
    for text, ref in verdicts(closing):
        if ref:
            return text, ref
    return None


# --- replay ---------------------------------------------------------------------------------------

def closing_messages(path: str):
    """(turn index, timestamp, closing message) for every turn of `path` that ended on prose, chosen
    the way the signal chooses the turn it judges."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import cupcake_turn_scan as scan

    turns, stamps = [scan.Turn()], [""]
    for ev in scan.load_events(path):
        if scan.is_real_user_prompt(ev):
            turns.append(scan.Turn())
            stamps.append("")
            continue
        if ev.get("type") != "assistant":
            continue
        for block in ev.get("message", {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                turns[-1].blocks.append(("text", block["text"]))
                stamps[-1] = ev.get("timestamp", stamps[-1])
            elif block.get("type") == "tool_use":
                turns[-1].blocks.append(("tool", block))
    for i, turn in enumerate(turns):
        if not turn.texts or turn.last_text_index < 0 or turn.tool_after(turn.last_text_index):
            continue
        yield i, stamps[i], turn.text_runs[-1]


def replay(limit: int, tdir: str, show_all: bool = False) -> int:
    files = sorted(glob.glob(os.path.join(tdir, "*.jsonl")), key=os.path.getmtime, reverse=True)[:limit]
    judged = halted = 0
    for path in files:
        sid = os.path.basename(path)[:8]
        for i, stamp, closing in closing_messages(path):
            judged += 1
            found = verdicts(closing)
            if any(ref for _, ref in found):
                halted += 1
            for text, ref in found:
                if ref:
                    print(f"HALT  {sid} turn {i} {stamp[:16]} [{ref[:60]}]: {text[:280]}")
                elif show_all:
                    print(f"allow {sid} turn {i} {stamp[:16]}: {text[:260]}")
    print(f"replay: {len(files)} transcripts, {judged} closing messages, {halted} halted")
    return 0


# --- selftest -------------------------------------------------------------------------------------

HALT, CLEAN = "halt", None


def selftest() -> int:
    cases = [
        # The closers and narration it was written from, verbatim (session 5ec7b779).
        ("The rule is now in `docs/agents/runtime-evidence.md`.", HALT),
        ("The progress selftest and the similarity checks pass, and the rule is in "
         "`docs/agents/conventions.md` so later runs keep it.", HALT),
        ("I tested it end to end: filed-and-dropped halts, filed-then-started passes. AGENTS.md now "
         "carries the same rule.", HALT),
        ("Next I'm writing the rule into AGENTS.md, then wiring the report into the hooks so that "
         "what's running reaches me without my having to ask:", HALT),
        ("docs/agents/runtime-evidence.md carries the written rule.", HALT),
        ("The rule and the record behind it are in `docs/agents/runtime-evidence.md`, \"Forecasts of "
         "time and effort are claims, cited or not made\".", HALT),
        ("Adding the rule to AGENTS.md next to the other stop-too-early shapes.", HALT),
        ("Encoding the rule first: a progress-output convention in `docs/agents/conventions.md`, then "
         "the code change with selftest checks.", HALT),
        # Older closers found by --replay.
        ("The new Steam rule is in the scripts and `AGENTS.md`:", HALT),
        ("- **What stayed in AGENTS.md:** the rules that apply every turn.", HALT),
        ("| `frida.md` | 10 KB | Frida setup and watchpoint rules |", HALT),
        ("| AGENTS.md rule | \"A long background build is tailed\" in `/home/banon/projects/er-mods-rs/AGENTS.md` |", HALT),
        ("The AGENTS.md rule is advisory: nothing enforces it if an agent skips it.", HALT),
        ("I added two rules to `/home/banon/AGENTS.md`:", HALT),
        ("- Behaviour rules go in AGENTS.md or in executable enforcement.", HALT),
        ("2 of those are the new AGENTS.md rule.", HALT),
        ("| **AGENTS.md** | A short rule telling future agents to use the theme for any new overlay. |", HALT),
        # The other spellings the guard is for.
        ("Rule: docs/agents/runtime-evidence.md, section 'Forecasts of time and effort'.", HALT),
        ("The written rule is the runtime-evidence section.", HALT),
        ("There is a rule in the notes about this.", HALT),
        ("That is the rule in the doc.", HALT),
        ("The instructions' rule says to tail it.", HALT),
        ("or an AGENTS.md / docs/agents rule where a guard is impossible", HALT),
        ("AGENTS.md's push rule needs an exact reply.", HALT),
        ("The rule AGENTS.md sets is one push per reply.", HALT),
        ("The `## Session Completion` rule asks for the phrase.", HALT),
        ("The doc entry rule is in `docs/agents/frida.md`.", HALT),
        ("| Push rule | `AGENTS.md`, Session Completion |", HALT),
        ("The rule on line 40 of AGENTS.md asks for it.", HALT),
        ("The rule in the global instructions says not to back anything up.", HALT),
        # Executable rules.
        ("The rego rule in `no_whole_check_sh.rego` refuses it, and AGENTS.md describes it.", CLEAN),
        ("A policy rule denies the push; AGENTS.md describes what it enforces.", CLEAN),
        ("The lint rule fires on that line, see docs/agents/build-and-test.md.", CLEAN),
        ("The `no_filed_not_started` guard's rule is described in AGENTS.md.", CLEAN),
        ("I sent the close as a standalone command, and the repo's existing rule in "
         "`.cupcake/policies/claude/teardown_must_relaunch.rego` refuses that.", CLEAN),
        ("Git ignores the copy under the `/save-files` rule in `.gitignore`, so it can't be committed.", CLEAN),
        ("New rule `ER-EFFECTS-TOOLS-BEFORE-PROBLEMS` blocks it; AGENTS.md names it.", CLEAN),
        ("The guard rule fires before AGENTS.md is read.", CLEAN),
        # Game and model rules.
        ("The record's resolve rule is described in docs/mechanics/hit-records.md.", CLEAN),
        ("The game's stacking rule is in docs/mechanics/buffs.md.", CLEAN),
        ("I'm checking whether ashes-of-war.md describes the punish rule that `run` extends.", CLEAN),
        ("The hit-priority rule in dnok is fixed.", CLEAN),
        ("Overlapping buffs are replayed through the game's stacking rules, so only one stays live.", CLEAN),
        ("Weapon skills follow the same rule: the tap is the first frame inside the release window.", CLEAN),
        ("I saved the rules text verbatim to `/home/banon/projects/er-mods-rs/docs/er-pvp-ladder-rules-2026-10.md`.", CLEAN),
        # The word itself, phrasal verbs, and rules with no markdown referent.
        ("The other adds a guard that blocks calling markdown text a \"rule\" and rewrites the sentences.", CLEAN),
        ("| agent: markdown \"rule\" guard | main session | the guard and the AGENTS.md / docs sweep |", CLEAN),
        ("The guard halts a message that calls a doc entry a 'rule'.", CLEAN),
        ("> The rule is now in docs/agents/runtime-evidence.md.", CLEAN),
        ("```\nthe rule is in AGENTS.md\n```\nThe build is green.", CLEAN),
        ("| safetyhook `MidHook` | Proven, but C++23 + CMake + Zydis, which AGENTS.md rules out for in-process code |", CLEAN),
        ("Several read a computed key, so this can't rule those out, per docs/agents/frida.md.", CLEAN),
        ("The rule counts only your most recent message, though.", CLEAN),
        ("The new rule passes its selftest: 154 checks, including both new ones.", CLEAN),
        ("As a rule of thumb, docs/agents/ghidra.md comes first.", CLEAN),
        ("The markdown-rule guard is in `.cupcake/policies/claude/no_markdown_rule.rego`.", CLEAN),
        ("Documenting the kit rule in the module doc.", CLEAN),
        ("I moved sections word for word and didn't reword any rules.", CLEAN),
        ("AGENTS.md describes it, and the guard enforces it.", CLEAN),
        ("The copied AGENTS.md push section in the kitty-tail bundle points at a policy and a teardown "
         "rule that aren't on that branch.", CLEAN),
        ("I'm reading its format rules and the editor UI sections of the design doc.", CLEAN),
        ("Seamless's docs site has no license, terms of use, or rule about other programs calling it.", CLEAN),
        ("The rule fires when the closing message names AGENTS.md as the place it lives.", CLEAN),
        ("The rule fires on Stop, and AGENTS.md describes it.", CLEAN),
        ("The rule fires on Stop and AGENTS.md describes it.", CLEAN),
        ("AGENTS.md is unchanged, and the rule passes its selftest.", CLEAN),
        ("AGENTS.md is unchanged and the new rule passes its selftest.", CLEAN),
        ("One note: the rule is assumed, not measured.", CLEAN),
        ("Two notes: the rule counts only the last message.", CLEAN),
        ("A note on the rule: it is filed as er-effects-rs-g6o7.", CLEAN),
        ("The docs/agents/frida.md table lists the rules.", HALT),
        ("There are rules in docs/ci-gate-portability.tsv for every gate.", CLEAN),
        ("The stage rules in `docs/ci-gate-portability.tsv` come from measurement.", CLEAN),
        ("The rule is in docs/agents/.", HALT),
    ]
    bad = 0
    for text, want in cases:
        hit = markdown_rule(text)
        got = HALT if hit else None
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got} {hit!r}: {text[:150]!r}")
    print(f"selftest {'ok' if not bad else 'FAILED'} ({len(cases) - bad}/{len(cases)})")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--replay", action="store_true", help="judge the closing messages of recent transcripts")
    ap.add_argument("--limit", type=int, default=40, help="newest transcripts to replay (default 40)")
    ap.add_argument("--all", action="store_true", help="with --replay, also print allowed sentences that say rule")
    # Claude Code names a project's transcript directory after its path with every character that is
    # not a letter or digit turned into "-"; a worktree's own directory holds only its own sessions.
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_repo = repo.split("/.worktrees/")[0]
    ap.add_argument(
        "--dir",
        default=os.path.expanduser("~/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", main_repo)),
        help="transcript directory (default: the main checkout's Claude Code project directory)",
    )
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.replay:
        return replay(args.limit, args.dir, args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
