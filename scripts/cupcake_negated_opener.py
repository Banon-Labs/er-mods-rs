#!/usr/bin/env python3
"""Classifier for the no_negated_opener Stop guard: a reply that meets the user's what/how/why
question by opening with something that did not happen.

The turn it was written from, 2026-11-10:

  user: What does this mean to me?
  assistant: Nothing in the game changed. The model was crediting hits the game never lets land, ...

The user had asked what a change to the offline damage model meant for their play. The first
sentence ruled out a reading nobody had (that analysis on disk changed the game) and the answer
started in the second. The user's words: "Do you want me to continue to enumerate on all of the
things it doesn't do which are also completely unrelated like you did, obfuscating the entire point
for me to respond to you in the first place?" -- and then "If I shouldn't do it, why do you do it,
and I have to remind you not to?". The every-turn reminder to lead with the answer was already in
context on that turn and did not stop it.

The rule judges no intent and is narrow on purpose:

- the prompt holds a question sentence that starts with what, how, why or which, after filler such
  as "OK, so"; who and when questions are left out, since "Nobody" and "Not before 3" answer them;
- the closing message's first sentence starts with Nothing or None;
- a question that itself asks about change or absence ("what changed?", "what's left?", "which
  failed?") is exempt, because there "Nothing" can be the whole answer.

A turn that the guard halted is judged again against the Stop hook's own feedback, which is the
newest prompt `cupcake_turn_scan.is_real_user_prompt` sees and is no wh-question, so the rewrite is
never halted twice.
"""
from __future__ import annotations

import re
import sys

FILLER = r"(?:(?:ok(?:ay)?|so|and|but|well|then|alright|hm+|um+)[\s,]+)*"
QUESTION_RE = re.compile(rf"^\s*{FILLER}((?:what|how|why|which)\b[^?]*\?)", re.IGNORECASE)
ABSENCE_RE = re.compile(
    r"\b(?:chang\w*|differ\w*|left|remain\w*|broke\w*|break\w*|wrong|miss\w*|else|happen\w*"
    r"|fail\w*|error\w*|block\w*|stop\w*|new|need\w*|lost|pending|outstanding|risk\w*|problem\w*"
    r"|issue\w*)\b",
    re.IGNORECASE,
)
OPENER_RE = re.compile(r"^(?:nothing|none)\b", re.IGNORECASE)
INTERRUPTED = "[Request interrupted by user]"


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.replace(INTERRUPTED, "\n").splitlines():
        out += [s for s in re.split(r"(?<=[.!?])\s+", line.strip()) if s]
    return out


def question(prompt: str) -> str:
    """The first what/how/why/which question sentence of `prompt`, or ""."""
    for s in _sentences(prompt or ""):
        m = QUESTION_RE.match(s)
        if m:
            return m.group(1)
    return ""


def asks_about_absence(q: str) -> bool:
    """The question asks whether something changed, is left, broke or is missing."""
    return bool(ABSENCE_RE.search(q))


def first_sentence(closing: str) -> str:
    """The closing message's first prose sentence: fenced code, headings and table rows skipped,
    leading list, quote and emphasis markers stripped."""
    text = re.sub(r"```.*?```", " ", closing or "", flags=re.DOTALL)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "|")):
            continue
        line = re.sub(r"^(?:[>*_\-]+|\d+[.)])\s*", "", line).lstrip("*_ ")
        return re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0]
    return ""


def negated_opener(prompt: str, closing: str) -> tuple[str, str] | None:
    """(question, opener) when the closing message answers a what/how/why/which question that is not
    about change or absence by opening with Nothing or None; else None."""
    q = question(prompt)
    if not q or asks_about_absence(q):
        return None
    s = first_sentence(closing)
    if not OPENER_RE.match(s):
        return None
    return q, s


def selftest() -> int:
    cases = [
        ("What does this mean to me?",
         "Nothing in the game changed. The model was crediting hits the game never lets land.", True),
        ("[Request interrupted by user]\nOK, so what does this mean for my build?",
         "**Nothing** in your kit moves.", True),
        ("What does this mean to me?", "Your INT 80 kit stays the same; Dryleaf Arts drops.", False),
        ("What changed?", "Nothing changed in the kit.", False),
        ("What's left to do?", "None; the commit is in.", False),
        ("Which builds failed?", "None of them.", False),
        ("Who touched the file?", "Nobody.", False),
        ("OK, so just to be clear, this work never solves world hunger?", "None of the three.", False),
        ("Stop hook feedback:\nThe user challenged a choice you made", "Nothing in the game changed.", False),
        ("Why is it slow?", "```\nnothing\n```\nThe cache was cold.", False),
        ("How does this affect me?", "- Nothing for your spells: they keep their picks.", True),
    ]
    bad = 0
    for prompt, closing, want in cases:
        got = negated_opener(prompt, closing) is not None
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {prompt!r} -> {closing!r}")
    print(f"selftest {'ok' if not bad else 'FAILED'} ({len(cases) - bad}/{len(cases)})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else 0)
