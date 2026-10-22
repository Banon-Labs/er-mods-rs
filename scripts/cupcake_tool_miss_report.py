#!/usr/bin/env python3
"""Reader for the `tool_miss_report` cupcake signal.

User directive 2026-10-18, verbatim: "I didn't say 'You don't have the correct course, here it is,
correct it.' I said: 'You don't have the correct course, its because your tools aren't working, I
don't want you to fix problems before you fix tools'".

What happened: the user reported "Multiple rego violations uncaught in this response". The main
session dispatched one subagent to widen the guards, which was the request, and a second one to fix
the build-kit defects the uncaught message had named, which was not. The second agent was stopped.

This reader answers one question for a pending Agent/Task call in the main session: does the user's
own last typed message report that a guard or tool missed something? It prints

    TOOLMISS|reported=<0|1>|prompt=<first 160 chars of that message>

and `.cupcake/policies/claude/no_product_work_before_tool_fix.rego` refuses the dispatch when
`reported=1` and the agent's prompt is not about the guards. The prompt is read the way the push and
commit guards read it (`cupcake_push_authorization.last_prompt` and its `prompt_text`): only a
message the user typed counts, never assistant text, a system reminder or a task notification. When
the dispatch's own line is not in the transcript yet, the chain is read from its newest entry
instead (`prompt_at_head`).

Fails open (prints nothing) on any error, a subagent's call, or a transcript it cannot read: this
guard refuses work, and a broken reader must not wedge every dispatch.

`--selftest` runs the report classifier over its own corpus.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# A report that a guard or tool missed something. Each alternative needs both a tool noun and a
# miss, or a phrase that only a miss report uses, so an ordinary request that mentions a hook or a
# policy does not match.
TOOL_NOUN = (
    r"(?:rego|polic(?:y|ies)|guards?|hooks?|stop[- ]hooks?|cupcake|gates?|checks?|tools?|tooling"
    r"|detectors?|signals?)"
)
MISS = (
    r"(?:uncaught|missed|miss(?:es|ing)?\s+(?:it|this|that)|didn'?t\s+(?:fire|catch|trip|work|stop)"
    r"|did\s+not\s+(?:fire|catch|trip|work|stop)|failed\s+to\s+(?:fire|catch|trip|stop)"
    r"|(?:aren'?t|are\s+not|isn'?t|is\s+not|weren'?t|wasn'?t)\s+(?:working|firing|catching)"
    r"|let\s+(?:it|this|that)\s+through|never\s+fired)"
)
REPORT_RE = re.compile(
    r"\b" + TOOL_NOUN + r"\b[^.\n?!]{0,60}?\b" + MISS
    + r"|\bviolations?\b[^.\n]{0,40}?\b(?:uncaught|not\s+caught|missed|slipped|got\s+through"
      r"|went\s+through|passed)\b"
    + r"|\buncaught\b[^.\n]{0,40}?\bviolations?\b"
    + r"|\bfix\s+(?:the\s+|your\s+)?tools?\s+(?:first|before)\b"
    + r"|\bbefore\s+you\s+fix\s+(?:the\s+|your\s+)?tools?\b"
    + r"|\bshould\s+have\s+(?:tripped|fired|been\s+caught)\b"
    + r"|\bpassed\s+every\s+(?:stop\s+)?(?:hook|guard|gate|check)s?\b",
    re.IGNORECASE,
)


def reports_tool_miss(prompt: str) -> bool:
    return bool(REPORT_RE.search(prompt or ""))


SELFTEST = [
    ("Multiple rego violations uncaught in this response", True),
    ("You don't have the correct course, its because your tools aren't working, I don't want you "
     "to fix problems before you fix tools", True),
    ("the stop hook didn't fire on that message", True),
    ("why did the guard not catch this? it should have tripped", True),
    ("that closer passed every Stop hook", True),
    ("the policy failed to catch the push", True),
    # Ordinary requests that mention the same nouns.
    ("hook the function at 0x14074a970 and log its first argument", False),
    ("add a policy row for the new weapon", False),
    ("fix the crit score in the build optimizer", False),
    ("check the tools directory for the param inspector", False),
    ("the parry riposte is missed by the score", False),
    ("authorize commit", False),
]


def selftest() -> int:
    bad = [(text, want) for text, want in SELFTEST if reports_tool_miss(text) != want]
    for text, want in bad:
        print(f"FAIL expected {want}: {text}", file=sys.stderr)
    print("cupcake_tool_miss_report selftest:", "OK" if not bad else f"{len(bad)} failure(s)")
    return 1 if bad else 0


# How long to wait for the dispatch's own tool_use line before reading the chain from its head. In
# the main session Claude Code writes that line only after every PreToolUse hook has returned
# (measured 2026-10-18 on Claude Code 2.1.281), so no wait finds it there; 1 s is ten times the
# 0.1 s lag measured in subagent transcripts, and a subagent's dispatch is never judged here.
LOCATE_DEADLINE_SECONDS = 1.0


def prompt_at_head(shared, path: str) -> tuple[str, str]:
    """(uuid, text) of the last genuine prompt on the chain ending at the newest main-chain entry.

    What is on disk while the dispatch's hooks run is the conversation up to this turn, and the
    pending call descends from its newest main-chain entry. The walk keeps the push reader's rules:
    only an entry its `prompt_text` accepts counts, a compaction summary ends the walk, and a
    repeated uuid refuses.
    """
    transcript = shared.Transcript(path)
    transcript.refresh()
    entries = transcript.entries()
    by_uuid: dict[str, dict] = {}
    for e in entries:
        uid = e.get("uuid")
        if isinstance(uid, str):
            if uid in by_uuid:
                raise shared.Refused("the transcript repeats an entry uuid, so it cannot be trusted")
            by_uuid[uid] = e
    node = next((e for e in reversed(entries)
                 if isinstance(e.get("uuid"), str) and not e.get("isSidechain")), None)
    seen: set[str] = set()
    while node is not None:
        uid = node["uuid"]
        if uid in seen:
            break
        seen.add(uid)
        text = shared.prompt_text(node)
        if text is not None:
            return uid, text
        if node.get("type") == "user" and node.get("isCompactSummary"):
            break
        parent = node.get("parentUuid")
        node = by_uuid.get(parent) if isinstance(parent, str) else None
    raise shared.Refused("no message typed by the user precedes this tool call")


def prompt_for_call(shared, path: str, tool_use_id: str, deadline: float) -> tuple[str, str]:
    """The prompt this dispatch answers: from its own line when that lands by `deadline`, else from
    the chain's head. Every refusal other than the line being missing stands."""
    try:
        return shared.last_prompt(path, tool_use_id, deadline)
    except shared.Refused as r:
        if "not in the transcript" not in r.reason:
            raise
    return prompt_at_head(shared, path)


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    started = time.monotonic()
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict) or event.get("agent_id"):
        return 0
    if event.get("tool_name") not in ("Agent", "Task"):
        return 0
    path, tool_use_id = event.get("transcript_path"), event.get("tool_use_id")
    if not isinstance(path, str) or not Path(path).is_file():
        return 0
    try:
        import cupcake_push_authorization as shared

        if isinstance(tool_use_id, str) and tool_use_id:
            _uuid, text = prompt_for_call(shared, path, tool_use_id,
                                          started + LOCATE_DEADLINE_SECONDS)
        else:
            _uuid, text = prompt_at_head(shared, path)
    except Exception:  # noqa: BLE001 -- fail open, see the module docstring
        return 0
    snippet = " ".join(text.split())[:160].replace("|", "/")
    print(f"TOOLMISS|reported={int(reports_tool_miss(text))}|prompt={snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
