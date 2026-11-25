#!/usr/bin/env python3
"""Classifier for the no_unmeasured_estimate Stop guard: a closing message that forecasts how long or
how costly work or a run will be, with no measured number behind the forecast.

The turn it was written from, 2026-11-09 21:16Z, session 5ec7b779. Asked "Is it reasonable to build
similarity matrixies?", the agent answered:

  Yes, and it's nearly free. ... That's a few seconds on top of a kit run. Adding melee-only ashes
  means scoring every skill once, roughly 20 to 30 minutes.

Nothing had been timed. The full similarity runs measured since took 6,863 s (r7), 9,827 s (r8) and
12,716 s (r9) (~/.cache/er-build-planner/attack-similarity-timing.jsonl), and r10 ran past three
hours. The same session also announced r10 as "about 7 hours" and later "roughly 2 to 2.5 hours"
left. The user, 2026-11-11: "You also estimated yesterday over 24 hours ago after I started this
project that generating similiarity scores woudln't be a big lift", and then: "should I ever listen
to you for timelines? Are timeline predictions useful? Should an LLM be prohibited from making
non-cited claims about timelines?". The reasoning is in docs/agents/runtime-evidence.md, "Forecasts
of time and effort are claims, cited or not made".

Two kinds of forecast, judged sentence by sentence over the closing message with fenced code, inline
code and double-quoted spans removed:

- `EFFORT`, halted outright. A forecast of development effort, or a size word standing in for a
  number: "nearly free", "not a big lift", "won't take long", "should be quick", "easy to add",
  "a small change", "a couple of hours of work", "an hour to implement". Before the work exists
  there is no measurement it could cite, and a size word cites none, so no number elsewhere in the
  message clears it.
- `RUNTIME`, halted unless the message states a measured timing. A numeric forecast of how long a run
  takes or has left: "will take about 40 minutes", "about 30 minutes left", "3.5 more hours",
  "should finish by 21:30", "that's a few seconds on top of a kit run", or an approximate duration
  in a forward-looking sentence ("r10 is running (about 7 hours)"). It passes when the same closing
  message states a measured timing with its number: a past duration ("took 6,863 s", "has run
  8,242 s", "over the last 28 minutes"), a rate ("99 s per set", "about 11 a minute", "22.3
  s/item"), progress with elapsed time ("150 of 1,277 attacks after about half an hour"), or a
  timing-log, wall-clock or CPU-seconds figure.

Not forecasts: a sentence that reports the past ("took", "ran", "told you", "estimated") with no
forward marker; a configured length (a cap, timeout, expiry, monitor or cache window); a duration
that times an event ("about 6.6 s after the press", "30 s before the crash"); vague largeness with
no number ("takes hours", "takes a while"), since saying it is slow and giving no number is what
the halt asks for when nothing has been timed.

`--selftest` runs the cases below. `--replay [--limit N] [--dir D]` runs the rule over the closing
message of every turn in the N newest transcripts (default 40) and prints each one it would halt.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# --- vocabulary -----------------------------------------------------------------------------------

_NUMWORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty"
    r"|forty(?:-five)?|fifty|sixty|ninety)"
)
_DIGITS = r"\d+(?:,\d{3})*(?:\.\d+)?"
_NUM = rf"(?:{_DIGITS}|{_NUMWORD}|half an?|an?|a few|a couple(?: of)?|several|a handful of)"
_RANGE = rf"{_NUM}(?:\s*(?:to|-|–|—|or)\s*{_NUM})?"
_UNIT_LONG = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b"
# "a second" is an ordinal far more often than a duration here ("needs a second reply", "a second
# player"), so it is not read as one.
_QTY = (
    rf"(?:\d+\s?h\s?\d+\s?m(?:\s?\d+\s?s)?\b|\d+\s?m\s?\d+\s?s\b"
    rf"|(?!an?\s+seconds?\b){_RANGE}\s*{_UNIT_LONG}"
    rf"|{_DIGITS}(?:\s*(?:to|-|–)\s*{_DIGITS})?\s?(?:s|h)\b)"
)
_APPROX_WORDS = (
    r"(?:about|around|roughly|approximately|approx\.?|~|≈|some|maybe|perhaps|probably|only|just"
    r"|under|over|less than|more than|at most|at least|up to|nearly|almost|another|a further"
    r"|an extra|an additional|a good|well under|well over|likely)"
)
_APPROX = rf"(?:{_APPROX_WORDS}\s*)"
# The approximators that make a bare duration in a forward-looking sentence a forecast.
_APPROX_REQUIRED = (
    r"(?:about|around|roughly|approximately|approx\.?|~|≈|maybe|perhaps|probably|under|less than"
    r"|at most|up to|nearly|almost|another|a further|an extra|an additional|well under|likely)"
)

FORWARD_RE = re.compile(
    r"\b(?:will|would|should|shall|going to|gonna|expect\w*|estimat\w*|projected|ETA|means|mean"
    r"|then|next|once|is running|are running|still running|now running|queued|starting|starts"
    r"|kicked off|launch(?:ing|es)?|scheduled|plan(?:ned)? to|if|until)\b|['’](?:ll|d)\b",
    re.IGNORECASE,
)
# A bare approximate duration ("that's a few seconds", "about 7 hours") is a forecast only beside
# the work it sizes; without one it is far more often a game duration or a conversion.
WORK_NOUN_RE = re.compile(
    r"\b(?:runs?|reruns?|re-runs?|builds?|rebuilds?|pass|passes|jobs?|tasks?|suites?|regenerat\w*"
    r"|sweeps?|batch\w*|pipelines?|stages?|selftests?|tests?|checks?|kit|matrix|matrices|uploads?"
    r"|workflows?|shards?|boards?|scor\w+|comput\w+|generat\w+|render\w*|replay\w*|compil\w+|push"
    r"|gates?|bisect\w*|profil\w+|analys\w+|scans?|samples?|rescor\w+|indexing|install\w*"
    # a run label (r10) or a background task id (bdj5lkbhk)
    r"|r\d+|b[a-z0-9]{8})\b",
    re.IGNORECASE,
)
PAST_REPORT_RE = re.compile(
    r"\b(?:took|spent|lasted|ran|was|were|had|did|finished|completed|ended|told you|said|estimated"
    r"|predicted|guessed|claimed|forecast|projected|quoted)\b",
    re.IGNORECASE,
)
# Reporting an earlier forecast is not making one.
REPORTED_RE = re.compile(
    r"\b(?:told you|I said|you said|called it|I called|claimed|estimated|predicted|guessed|promised"
    r"|I wrote|my estimate|was quoted)\b",
    re.IGNORECASE,
)
# A configured length is a setting, not a forecast of work.
SETTING_RE = re.compile(
    r"\b(?:cap|caps|capped|timeout|time-out|timed out|expire[sd]?|expiry|deadline|budget|limit"
    r"|TTL|cache|cached|cooldown|heartbeat|poll\w*|sleep\w*|watch(?:es|ing)?|monitor\w*|interval"
    r"|retention|waits?|waiting|logger)\b",
    re.IGNORECASE,
)
# A duration that times an event in the game or a log, not the length of a piece of work.
_EVENT_AFTER = (
    r"\s*(?:after|before|ago|into|earlier|later|faster|slower|late|early|apart|behind|ahead|past"
    r"|per\b|a\s+(?:second|frame|minute|hour)|each|every|window|cooldown|delay|old)\b"
)
_EVENT_BEFORE = (
    r"(?:after|at|since|from|until|till|every|each|than|took|spent|ran|lasted|elapsed|was|were"
    r"|the last|the first|for the last|over the last|in the last|within the last|for up to)\s+"
)

_WORK_VERB = (
    r"(?:add|implement|build|rebuild|fix|wire|write|change|do|port|extend|support|set up|make|code"
    r"|refactor|integrate|hook|patch|script|automate|land|ship|model|teach|generate|compute|score"
    r"|run|rerun|re-run|test|check|regenerate)"
)
_MODAL_BE = r"(?:\b(?:will|would|should|shall|ought to|must|may|might|could)\s+be|['’](?:ll|d)\s+be)"
_COPULA = rf"(?:{_MODAL_BE}|\b(?:it|that|this)\s+is|\b(?:it|that|this)['’]s)"
_EASY = r"(?:easy|simple|straightforward|trivial|quick|cheap|painless|negligible|instant|instantaneous)"
_SMALL_JOB = (
    r"(?:quick|easy|trivial|small|tiny|simple|minor|one-line|one-liner|five-minute|two-minute|ten-minute)"
    r"\s+(?:change|fix|job|win|patch|edit|addition|task|tweak|lift|follow-up|extension)"
)
EFFORT_RE = re.compile(
    # Size words standing in for a number.
    r"\b(?:nearly|almost|basically|essentially|practically|virtually|effectively|close to|next to)\s+free\b"
    r"|\bcomes?\s+(?:for\s+)?free\b"
    r"|(?:\bnot|n't|\bno|\bnever)\s+(?:be\s+|really\s+)?(?:a\s+|that\s+|much\s+of\s+a\s+)?"
    r"(?:big|huge|large|major|heavy|real)\s+lift\b"
    r"|\b(?:small|light|tiny|easy|minor|little)\s+lift\b"
    r"|(?:\bnot|n't)\s+take\s+(?:very\s+|that\s+|too\s+|so\s+)?long\b"
    r"|\btakes?\s+(?:no time|almost no time|next to no time|a moment)\b"
    r"|\bin no time\b"
    rf"|{_MODAL_BE}\s+(?:very\s+|pretty\s+|fairly\s+|relatively\s+|really\s+|super\s+)?{_EASY}\b"
    rf"|{_COPULA}\s+(?:only\s+|just\s+)?(?:an?\s+)?{_SMALL_JOB}\b"
    rf"|\b{_EASY}\s+to\s+{_WORK_VERB}\b"
    # A number put on development work, which has no measurement before the work exists.
    rf"|\b{_RANGE}\s*(?:{_UNIT_LONG}|afternoons?|evenings?|weekends?|weeks?)\s+(?:of\s+)?"
    r"(?:work|effort|coding|dev\b|development|implementation|engineering|hacking)\b"
    r"|\b(?:a|one|two|half a)\s+day['’]s\s+work\b"
    rf"|\b(?:take|takes|taking|need|needs|cost|costs)\s+{_APPROX}*{_RANGE}\s*(?:{_UNIT_LONG}|afternoons?|weeks?)"
    rf"\s+to\s+(?:implement|write|add|port|fix|wire|code|refactor|model|support|land|ship)\b",
    re.IGNORECASE,
)

# "lasts" is left out: here it is a buff or hit-record duration far more often than a run's. Bare
# "cost" is left out because it is also the past tense ("the restart cost about 12 minutes").
TAKE_RE = re.compile(
    rf"(?:\b(?:take|takes|taking|need|needs|costs|add|adds|eats?|burns?)"
    rf"|(?:\b(?:will|would|should|could|might|may|to)|['’](?:ll|d))\s+cost)\s+"
    rf"(?:another\s+|an extra\s+|a further\s+)?{_APPROX}*({_QTY})",
    re.IGNORECASE,
)
LEFT_RE = re.compile(
    rf"{_APPROX}*({_QTY})\s+(?:more|left|remaining|to go|longer)\b"
    rf"|{_APPROX}*({_RANGE}\s+more\s+{_UNIT_LONG})"
    rf"|\b(?:left|remaining|leaves|leaving|ETA)\b[:\s]+(?:of\s+)?{_APPROX}*({_QTY})",
    re.IGNORECASE,
)
FINISH_RE = re.compile(
    rf"\b(?:finish|finishes|end|ends|complete|completes|be done|be ready|be back|done|ready|land|lands"
    rf"|wrap up|wraps up)\s+(?:in|within|by|around|at)\s+{_APPROX}*"
    rf"({_QTY}|\d{{1,2}}:\d{{2}}\s*(?:am|pm|z|utc)?\b|\d{{1,2}}\s*(?:am|pm)\b|noon|midnight|tonight"
    rf"|tomorrow|the morning|this evening|this afternoon)",
    re.IGNORECASE,
)
COPULA_RE = re.compile(
    rf"\b(?:that|this|it|which|each|each one|one)(?:'s|’s|\s+is|\s+will be|\s+would be|\s+should be"
    rf"|'ll be|’ll be|'d be|’d be|\s+could be|\s+might be)\s+{_APPROX}*({_QTY})",
    re.IGNORECASE,
)
APPROX_QTY_RE = re.compile(rf"(?:{_APPROX_REQUIRED}\s*)({_QTY})|\b(a few\s+{_UNIT_LONG})", re.IGNORECASE)

# --- measured basis -------------------------------------------------------------------------------

_BASIS_QTY = (
    rf"(?:\d+\s?h\s?\d+\s?m(?:\s?\d+\s?s)?\b|\d+\s?m\s?\d+\s?s\b"
    rf"|(?:{_DIGITS}|{_NUMWORD}|half an?|an)\s*{_UNIT_LONG}|{_DIGITS}\s?(?:s|h|ms)\b)"
)
BASIS_RES = [
    # A past duration: "took 6,863 s", "has run 8,242 s", "over the last 28 minutes".
    re.compile(
        rf"\b(?:took|taken|spent|lasted|elapsed|measured|timed|clocked|ran|(?:has|have|had)\s+(?:been\s+)?run(?:ning)?"
        rf"|finished in|completed in|over the last|in the last|for the last|within the last|so far)\b"
        rf"[^.;\n]{{0,40}}?{_APPROX}*{_BASIS_QTY}",
        re.IGNORECASE,
    ),
    # Elapsed time beside what it got done: "After 1.2 minutes it has seen 12 new hosts".
    re.compile(
        rf"\bafter\s+{_APPROX}*{_BASIS_QTY}[^.;\n]{{0,30}}?\b(?:has|have|had|is at|was at|it['’]s at)\b",
        re.IGNORECASE,
    ),
    # A rate: "99 s per set", "22.3 s/item", "55 seconds each", "50 seconds a weapon",
    # "about 11 a minute", "17 sets per minute".
    re.compile(rf"{_BASIS_QTY}\s*(?:(?:per|/)\s*[a-z]|each\b|an?\s+(?!second\b)[a-z]{{3,}})", re.IGNORECASE),
    re.compile(
        rf"\b{_DIGITS}\s+(?:[a-z-]+\s+){{0,2}}(?:per|a|an|/)\s*(?:second|minute|hour|sec|min)\b",
        re.IGNORECASE,
    ),
    # Progress with the time it took: "150 of 1,277 attacks after about half an hour".
    re.compile(
        rf"\b{_DIGITS}\s+(?:of|/)\s+~?\s*{_DIGITS}\b[^.;\n]{{0,80}}?\b(?:after|in|over)\s+{_APPROX}*{_BASIS_QTY}",
        re.IGNORECASE,
    ),
    # A timed sample: "On samples, multihit runs in 11.6 s", "a 20-entity sample took 412 s".
    re.compile(
        rf"\b(?:samples?|sampled|trial|pilot|dry run|profil\w+|benchmark\w*)\b[^.;\n]{{0,60}}?{_DIGITS}\s?"
        rf"(?:{_UNIT_LONG}|s\b|h\b|ms\b)",
        re.IGNORECASE,
    ),
    # A timing log, wall clock or CPU figure with a number beside it.
    re.compile(
        rf"\b(?:timing log|timing\.jsonl|timing-log|-timing\b|wall[- ]clock|wall time|CPU[- ]s(?:econds)?\b"
        rf"|cpu time|elapsed_s)\b[^.;\n]{{0,60}}?{_DIGITS}",
        re.IGNORECASE,
    ),
]

# --- text handling --------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_QUOTE_RE = re.compile(r"\"[^\"\n]{0,400}\"|“[^”\n]{0,400}”")


def _clean(text: str) -> str:
    text = _FENCE_RE.sub(" ", text or "")
    text = _INLINE_CODE_RE.sub(" ", text)
    return _QUOTE_RE.sub(" ", text)


def sentences(text: str) -> list[str]:
    out = []
    for line in _clean(text).splitlines():
        line = line.strip()
        if not line:
            continue
        out += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*_(\[-])", line) if s.strip()]
    return out


def _event_timed(sentence: str, start: int, end: int) -> bool:
    """The duration at [start, end) times an event or a past span rather than a piece of work."""
    if re.match(_EVENT_AFTER, sentence[end:], re.IGNORECASE):
        return True
    before = sentence[max(0, start - 30):start]
    return bool(re.search(rf"\b{_EVENT_BEFORE}(?:{_APPROX_WORDS}\s*)*$", before, re.IGNORECASE))


def effort_forecast(sentence: str) -> bool:
    """The sentence sizes development work, or puts a size word where a number would go."""
    return bool(EFFORT_RE.search(sentence)) and not REPORTED_RE.search(sentence)


def runtime_forecast(sentence: str) -> bool:
    """The sentence puts a number on how long a run takes or has left."""
    if SETTING_RE.search(sentence):
        return False
    forward = bool(FORWARD_RE.search(sentence))
    if (PAST_REPORT_RE.search(sentence) or REPORTED_RE.search(sentence)) and not forward:
        return False
    work = bool(WORK_NOUN_RE.search(sentence))
    for rx in (TAKE_RE, LEFT_RE, FINISH_RE, COPULA_RE):
        if rx is COPULA_RE and not work:
            continue
        for m in rx.finditer(sentence):
            g = next((i for i in range(1, (m.lastindex or 0) + 1) if m.group(i)), None)
            if g is None:
                continue
            if rx is not FINISH_RE and _event_timed(sentence, m.start(g), m.end(g)):
                continue
            # "one more minute until the heat death of the universe" sizes no work.
            if rx is LEFT_RE and re.search(r"\bmore\b", m.group(0), re.IGNORECASE) and not work:
                continue
            return True
    if forward and work:
        for m in APPROX_QTY_RE.finditer(sentence):
            g = 1 if m.group(1) else 2
            if not _event_timed(sentence, m.start(g), m.end(g)):
                return True
    return False


def measured_basis(text: str) -> str:
    """The first measured timing the message states (code kept, backticks dropped), or ""."""
    flat = " ".join((text or "").replace("`", " ").split())
    for rx in BASIS_RES:
        m = rx.search(flat)
        if m:
            return m.group(0).strip()
    return ""


def unmeasured_estimate(closing: str) -> tuple[str, str] | None:
    """("effort", sentence) for the first effort forecast; else ("runtime", sentence) for the first
    run-time forecast when the message states no measured timing; else None."""
    sents = sentences(closing)
    for s in sents:
        if effort_forecast(s):
            return "effort", s
    runtime = [s for s in sents if runtime_forecast(s)]
    if runtime and not measured_basis(closing):
        return "runtime", runtime[0]
    return None


# --- replay ---------------------------------------------------------------------------------------

def _scan():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import cupcake_turn_scan

    return cupcake_turn_scan


def replay(limit: int, tdir: str, show_all: bool = False) -> int:
    halted = {"effort": 0, "runtime": 0}

    def judge(sid, i, stamp, closing):
        hit = unmeasured_estimate(closing)
        if hit:
            halted[hit[0]] += 1
            print(f"HALT {hit[0]:7} {sid} turn {i} {stamp[:19]}: {' '.join(hit[1].split())[:260]}")
        elif show_all:
            cleared = [s for s in sentences(closing) if runtime_forecast(s)]
            if cleared:
                print(f"allow runtime {sid} turn {i} {stamp[:19]}: {' '.join(cleared[0].split())[:220]}")
                print(f"      basis: {measured_basis(closing)[:160]}")

    files, judged = _scan().replay_closings(limit, tdir, judge)
    print(
        f"replay: {files} transcripts, {judged} closing messages, "
        f"{sum(halted.values())} halted ({halted['effort']} effort, {halted['runtime']} runtime)"
    )
    return 0


# --- selftest -------------------------------------------------------------------------------------

NOV09 = (
    "Yes, and it's nearly free. The kit already computes, for every spell and every rival skill, what "
    "it nets in each of the same 1,208 punish situations. Correlating those per-situation scores, "
    "weighted by how often each situation comes up, gives the matrix: two attacks that land in the "
    "same situations come out as near-duplicates. That's a few seconds on top of a kit run. Adding "
    "melee-only ashes means scoring every skill once, roughly 20 to 30 minutes. It would only measure "
    "how similar two attacks are as punishes, not in neutral or as buffs. Background task bvhsoy04v "
    "is still running the kit with the rivals."
)
MEASURED = (
    "r10 has finished 38 of 944 sets, and its pace over the last 28 minutes was 17 sets, about 99 s "
    "per set. If r10's sets are spread the same way, its 38 sets are about half the stage, which "
    "leaves roughly 2 to 2.5 hours."
)
EFFORT, RUNTIME, CLEAN = "effort", "runtime", None


def selftest() -> int:
    cases = [
        (NOV09, EFFORT),
        ("Yes, and it's nearly free.", EFFORT),
        ("It wouldn't be a big lift.", EFFORT),
        ("Generating similarity scores won't take long.", EFFORT),
        ("Rescoring should be quick.", EFFORT),
        ("The melee pass is easy to add.", EFFORT),
        ("That's a small change in the scorer.", EFFORT),
        ("It's about a couple of hours of work.", EFFORT),
        ("It would take about an hour to implement.", EFFORT),
        (MEASURED + " Wiring the ashes in would be a quick fix.", EFFORT),
        ("That's a few seconds on top of a kit run.", RUNTIME),
        ("Adding melee-only ashes means scoring every skill once, roughly 20 to 30 minutes.", RUNTIME),
        ("r10 is running from sim-release (bdj5lkbhk, about 7 hours, tailed in a kitty tab).", RUNTIME),
        ("It will then run one full build (about 35 minutes, on the same CPUs pinned away from your game).", RUNTIME),
        ("Its full build r3 is at 16 of 1,278 attacks on the CPUs kept off your game, about 30 minutes left.", RUNTIME),
        ("Build r4 (task bftbhtuuf, about 40 minutes): the full run on the code that will be committed.", RUNTIME),
        ("The rebuild should finish by 21:30.", RUNTIME),
        ("The board regeneration will take ~7 h.", RUNTIME),
        (MEASURED, CLEAN),
        ("It's at 150 of 1,277 attacks after about half an hour, which is roughly 3.5 more hours on two cores.", CLEAN),
        ("A 20-entity sample took 412 s, so the 944 sets will take about 5.4 hours.", CLEAN),
        ("The timing log has r9 at 12,716 s; r10 will take about 4 hours.", CLEAN),
        ("The measured full runs took 6,863 s (r7), 9,827 s (r8) and 12,716 s (r9).", CLEAN),
        ("On 2026-11-09 I told you the matrix was \"nearly free… a few seconds on top of a kit run\", "
         "and about 20 to 30 minutes with melee ashes.", CLEAN),
        ("On 2026-11-09 I called it nearly free; r7 took 6,863 s.", CLEAN),
        ("I haven't timed it, so I have no number for how long r10 has left.", CLEAN),
        ("The generator says this takes hours and resumes if interrupted.", CLEAN),
        ("Twin Moons' roll cancel comes at frame 199, about 6.6 s after the press.", CLEAN),
        ("About 30 s before the crash, D3D12Core started throwing errors.", CLEAN),
        ("Monitor b0ng729ic is watching the system log for 10 minutes and will catch any crash.", CLEAN),
        ("It fires 54 minutes after the last request. Firing at 15 or 30 minutes would be worse.", CLEAN),
        ("Every counter line will read like `11/944 attack sets  4m05s, 22.3 s/item, ~5h47m left`.", CLEAN),
        ("- **bow5z34az** (full run r7): at 104 of 1,276 attacks, about 11 a minute.", CLEAN),
        ("Gravitas is about half a second faster and costs less FP.", CLEAN),
        ("```\nETA 5 minutes\n```\nThe build is green.", CLEAN),
        ("A full v9 run also took about 3 hours even before r10 started.", CLEAN),
        ("The dart is quick to cast and easy to dodge.", CLEAN),
        # False positives found by --replay over 85 transcripts, 2026-11-11.
        ("This one is for the bracket branch; the teardown branch needs a second reply.", CLEAN),
        ("Greenburst Crystal Tear lasts 180 seconds (3 minutes).", CLEAN),
        ("The second adds 1.5–3%, and a third adds nothing.", CLEAN),
        ("So the restart cost about 12 minutes: 6.5 of loadout work plus the setup phases.", CLEAN),
        ("Five extra talismans, roughly 40% more HP, and one more minute until the heat death of the universe.", CLEAN),
        ("Next time a game is running anyway, leave it up for about a minute and I'll finish the rebase.", CLEAN),
        ("The check now waits up to 3 s for the push call to reach the transcript.", CLEAN),
        ("You could first move 53 frames after touchdown, which is about 1.2 to 1.3 seconds.", CLEAN),
        ("Its first weapon took about 3 minutes; later ones take seconds to a minute.", CLEAN),
        ("The volume sets to 8× once the counter logger finishes at about 12:06, because a second one can't run.", CLEAN),
        ("After 1.2 minutes it has seen 12 new hosts, so the rates mean little until it has run for a few hours.", CLEAN),
        ("At roughly 50 seconds a weapon, that's about 95 minutes to go.", CLEAN),
        ("It's running on zerk, about 70 of ~183 weapons into each shard after 46 minutes, so roughly two more hours.", CLEAN),
        ("On samples, multihit runs in 11.6 s for 156 rows, and a 24-weapon sweep takes 37 s instead of 91.", CLEAN),
        ("The batch is running: 117 NPC builds left at about 55 seconds each, so roughly 1 hour 50 minutes.", CLEAN),
        # True positives the same replay found.
        ("A test is easy to set up: launch, quit at the title screen, and check for a crash record.", EFFORT),
        ("If a duel page should stop at the sets, that's a one-line change.", EFFORT),
        ("The full batch is running: 119 builds, about 1 hour 50 minutes at the trial pace.", RUNTIME),
        ("The bar is at 78% with about 3h20m left.", RUNTIME),
        ("Note that the pre-push gate runs when you push and may take several minutes.", RUNTIME),
        ("Restarting now with zerk's 2 shards would bring the remaining time to about 1.5 hours.", RUNTIME),
    ]
    bad = 0
    for text, want in cases:
        hit = unmeasured_estimate(text)
        got = hit[0] if hit else None
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {text[:140]!r}")
    print(f"selftest {'ok' if not bad else 'FAILED'} ({len(cases) - bad}/{len(cases)})")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--replay", action="store_true", help="judge the closing messages of recent transcripts")
    ap.add_argument("--limit", type=int, default=40, help="newest transcripts to replay (default 40)")
    ap.add_argument("--all", action="store_true", help="with --replay, also print forecasts a measured timing cleared")
    ap.add_argument(
        "--dir",
        default=_scan().default_transcript_dir(),
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
