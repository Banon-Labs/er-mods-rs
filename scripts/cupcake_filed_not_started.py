#!/usr/bin/env python3
"""Classifier for the no_filed_not_started Stop guard: a turn that filed a fix (`bd create`) and
ended without starting it.

The turn it was written from, 2026-11-10. The user described a mechanic the model lacked (delaying
an ash's follow-up press to catch a roll); the agent filed it as a beads issue and closed
with "It waits for the homing and requirement-line changes, which edit the same files." Nothing
blocked it: a separate worktree takes it, which is what the next turn did. The user's words: "The
only time I would want you to mention a fix and not work on it is if you're blocked when it relates
to what we are working on already, and this needs to be internalized for all future sessions".

The rule:

- the turn ran `bd create` (the agent filed work);
- no Agent, Workflow, Edit or Write call came after the last `bd create` (nothing started it);
- the closing message names no blocker: an external one (`cupcake_admission_with_defence.
  externally_blocked`: a credential, sudo, a live game, a guard that refused, a tool that is not
  installed) or a running task the work depends on ("blocked on task bxxxxxx", "failed to
  connect").

A filing at the end of finished work that is blocked says so in the closing message and passes.

The second shape, added 2026-11-20: a fix that is written and parked behind a running job. Across
2026-11-19/20 the closing messages said "The fix for the time-left estimate is written and tested,
and I'll apply it when the kit run (b3fgttzcf) ends", "I'm holding it until the all-spells run
(b4pvxbpqy) finishes, because changing the scripts mid-run would throw away that run's saved sets",
and three more like them (the `filed_deferred_*.jsonl` fixtures). None filed anything, so the first
shape let all five through. The stated reason was no blocker: a running process has already loaded
its code, so editing the files matters only if it restarts, and the next run recomputes anyway. The
user, 2026-11-20: "yes, your quoted message and my last message are consistent, but inconsistent
with yoru actions".

`deferred_apply` flags a closing message that puts applying (or holding) a fix after a job ends:
"I'll apply ... when/once ... finishes/ends", "holding ... until ... finishes", "applying once this
run ends". It stays quiet when:

- nothing is deferred: the fix is reported applied ("Applied the patch; the running job keeps its
  loaded code"), or what waits is a report rather than a fix;
- what is held is not a fix ("holding the full run until the subset finishes" sequences runs);
- the wait is on the user or their game ("once you close the game"), or on an external blocker
  (`cupcake_admission_with_defence.externally_blocked`);
- the fix is made from the running job's output ("they come from its output").

A running task is deliberately not an exemption for this shape: "blocked on task b3fgttzcf" is the
very excuse it exists to refuse when the task is only running the code the fix edits.
"""
from __future__ import annotations

import re
import sys

BD_CREATE_RE = re.compile(r"(?:^|[\s;&|/])bd\s+create\b|\$\w+\s+create\b.*--title")
STARTERS = ("Agent", "Workflow", "Edit", "Write")
RUNNING_BLOCKER_RE = re.compile(
    r"\bblocked\s+on\s+(?:task\s+)?`?[a-z0-9]{7,}`?|\bdepends\s+on\s+(?:task\s+)?`?[a-z0-9]{7,}`?"
    r"|\bfailed\s+to\s+connect\b|\bcannot\s+start\s+until\b",
    re.IGNORECASE,
)


# A closing message that puts applying a fix after a job ends. Each family stays inside one clause:
# no sentence end, newline or table-cell border between the verb, the "until/when/once" and the
# job's end, so a report in one cell and a wait in the next do not join. Past tense ("applied",
# "ended") is left out on purpose: "I applied it once the run ended" reports the fix applied.
_CLAUSE = r"[^.!?\n|]"
_UNTIL = r"\b(?:until|till|when|once|after)\b"
_JOB_ENDS = (
    r"\b(?:finish(?:es)?|ends?|completes?|exits?|stops?|is\s+(?:done|over|finished)"
    r"|has\s+(?:finished|ended|completed))\b"
)
_FIX_NOUN = r"\b(?:fix(?:es)?|patch(?:es)?|change[sd]?|changing|edit(?:s|ed|ing)?|diff|rewrite)\b"
DEFER_RE = re.compile(
    # "I'll apply it when the kit run (b3fgttzcf) finishes", "the patch I'm applying once this run
    # (b4pvxbpqy) ends".
    rf"\b(?:apply|applying)\b{_CLAUSE}{{0,120}}?{_UNTIL}{_CLAUSE}{{0,100}}?{_JOB_ENDS}"
    # "I'm holding the timestamp, time-estimate and Golden Land patches until the read-back
    # finishes": the held thing is a fix noun, or a pronoun the rest of the message makes one.
    rf"|\b(?:hold(?:ing)?|keep(?:ing)?)\s+(?:back\s+)?"
    rf"(?P<held>it|them|this|that|these|those|the\s{_CLAUSE}{{0,80}}?{_FIX_NOUN})"
    rf"{_CLAUSE}{{0,80}}?{_UNTIL}{_CLAUSE}{{0,100}}?{_JOB_ENDS}",
    re.IGNORECASE,
)
FIX_NOUN_RE = re.compile(_FIX_NOUN, re.IGNORECASE)
# The wait is on the user or their game, not on a job: "once you close the game", "when your
# session ends", "until the game exits".
DEFER_ON_USER_RE = re.compile(
    rf"{_UNTIL}\s+(?:you\b|your\b|the\s+game\b|elden\s+ring\b)", re.IGNORECASE
)
# The fix is made from the running job's output, so it cannot be written before the job ends.
NEEDS_OUTPUT_RE = re.compile(
    r"\b(?:needs?|reads?|uses?|depends\s+on|comes?\s+from|is\s+computed\s+from)\b"
    r"[^.!?\n|]{0,60}?\b(?:output|results?|numbers|measurements?)\b",
    re.IGNORECASE,
)


def _clause_around(text: str, start: int, end: int) -> str:
    """The clause holding text[start:end], bounded by a sentence end, a newline or a cell border."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n|") + 1
    rights = [i for i in (text.find(c, end) for c in ".!?\n|") if i >= 0]
    right = min(rights) + 1 if rights else len(text)
    return " ".join(text[left:right].split())


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence holding text[start:end] plus the next one, which is where a reason
    ("they come from its output") sits when it is not in the same clause."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    m = re.search(r"[.!?\n]\s*[^.!?\n]*[.!?\n]?", text[end:])
    right = end + m.end() if m else len(text)
    return text[left:right]


def deferred_apply(closing: str) -> str:
    """The clause that parks a written fix behind a job's end, or "" when the closing message does
    not (the module docstring lists what stays quiet)."""
    text = closing or ""
    try:
        import cupcake_admission_with_defence as ad
        if ad.externally_blocked(text):
            return ""
    except Exception:
        pass
    for m in DEFER_RE.finditer(text):
        if DEFER_ON_USER_RE.search(m.group(0)):
            continue
        if NEEDS_OUTPUT_RE.search(_sentence_around(text, m.start(), m.end())):
            continue
        held = m.group("held")
        if held and not FIX_NOUN_RE.search(held) and not FIX_NOUN_RE.search(text):
            continue
        return _clause_around(text, m.start(), m.end())
    return ""


def _blocked(closing: str) -> bool:
    try:
        import cupcake_admission_with_defence as ad
        if ad.externally_blocked(closing):
            return True
    except Exception:
        pass
    return bool(RUNNING_BLOCKER_RE.search(closing or ""))


def is_bd_create(block: dict) -> bool:
    if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Bash":
        return False
    cmd = (block.get("input") or {}).get("command", "")
    return isinstance(cmd, str) and bool(BD_CREATE_RE.search(cmd))


def filed_not_started(tool_blocks: list[dict], closing: str) -> int:
    """How many `bd create` calls the turn made after the last call that started work, when the
    closing message names no blocker; 0 when the turn is clean."""
    filed, last_create = 0, -1
    for i, b in enumerate(tool_blocks):
        if is_bd_create(b):
            filed, last_create = filed + 1, i
    if not filed:
        return 0
    if any(b.get("name") in STARTERS for b in tool_blocks[last_create + 1:]):
        return 0
    return 0 if _blocked(closing) else filed


def selftest() -> int:
    def bash(cmd):
        return {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}
    agent = {"type": "tool_use", "name": "Agent", "input": {"prompt": "x"}}
    create = bash('$HOME/.local/bin/bd create --title "Follow-ups: delay the press" --type feature')
    cases = [
        ([create], "The fix is filed as bd er-effects-rs-me5p. It waits for the homing change.", 1),
        ([create, agent], "Filed and started in its own worktree.", 0),
        ([agent, create], "Filed it; the agent above was for something else.", 1),
        ([create], "Filed; it is blocked on task bpll5d7de, whose numbers it reads.", 0),
        ([create], "Filed; the Ghidra MCP server failed to connect, and the read needs it.", 0),
        ([bash("B=$HOME/.local/bin/bd; $B create --title 'x' --type bug")], "Filed.", 1),
        ([bash("$HOME/.local/bin/bd show er-effects-rs-me5p")], "Shown.", 0),
        ([], "Nothing filed.", 0),
    ]
    # deferred_apply: (closing message, must it flag). The first five are verbatim closing messages
    # of 2026-11-19/20 that parked a written fix behind a running job; the rest must stay quiet.
    deferred = [
        ("I've written the change but not applied it. ... I'll apply it when the kit run (b3fgttzcf) "
         "finishes. Changing the scripts now would invalidate its 207 saved sets if it had to resume.",
         True),
        ("The fix for the time-left estimate is written and tested, and I'll apply it when the kit run "
         "(b3fgttzcf) ends.", True),
        ("I'm holding the timestamp, time-estimate and Golden Land patches until the read-back "
         "finishes.", True),
        ("I'm holding it until the all-spells run (b4pvxbpqy) finishes, because changing the scripts "
         "mid-run would throw away that run's saved sets.", True),
        ("| Built-in clock times and finish times on every line | the patch I'm applying once this run "
         "(b4pvxbpqy) ends |", True),
        # A running task named as the "blocker" is the excuse itself, not an exemption.
        ("The fix is ready; I'll apply it once the kit run finishes, blocked on task b3fgttzcf.", True),
        ("Applied the patch; the running job keeps its loaded code.", False),
        ("This waits on your `authorize commit`.", False),
        ("The read-back (b849hq3rz) is still loading.", False),
        ("Applied the time-left fix to the scripts. The kit run (b3fgttzcf) keeps the code it loaded, "
         "and the next run picks it up when it starts.", False),
        ("I applied the patch once the run ended.", False),
        ("I'll report the per-set numbers when the kit run (b3fgttzcf) finishes.", False),
        ("I'm holding the full run until the --only subset finishes, because the subset decides "
         "whether the model changes.", False),
        ("I'll apply the new thresholds when the calibration run (b7aa1c2d3) finishes. They come from "
         "its output.", False),
        ("The DLL patch is built; I'll apply it once you close the game.", False),
        ("The edit is written, but applying it requires sudo, which I do not have; I'll apply it when "
         "that command finishes.", False),
    ]
    bad = 0
    for blocks, closing, want in cases:
        got = filed_not_started(blocks, closing)
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {closing!r}")
    for closing, want in deferred:
        got = deferred_apply(closing)
        if bool(got) != want:
            bad += 1
            print(f"FAIL deferred want={want} got={got!r}: {closing!r}")
    total = len(cases) + len(deferred)
    print(f"selftest {'ok' if not bad else 'FAILED'} ({total - bad}/{total})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.dirname(__file__))
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else 0)
