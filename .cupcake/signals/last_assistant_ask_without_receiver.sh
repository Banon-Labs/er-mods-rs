#!/usr/bin/env bash
# Cupcake signal: last_assistant_ask_without_receiver
#
# Emits `ASKNORECEIVER:<sentence>` when the closing prose of the last assistant turn asks the user
# for an action or an event the agent means to observe, and nothing is armed that would wake the
# agent when it happens. Empty otherwise (fail-open).
#
# User directive 2026-10-02, in their words: "When telling me you 'need' something, that you're
# already prepared to receive the information. In this case, you set no monitors, so you needed
# nothing from me. You only needed yourself to set a monitor."
#
# The instance: a Frida watcher was attached and writing events to a log, and the turn closed on
# "I need you to close the item list with B twice: once from our view 3, and once from a native
# view". No Monitor was armed. The watcher itself was a backgrounded Bash task, but its exit is not
# the event -- it runs until detach -- so the user's presses would have landed in a log nobody was
# going to read, and the next turn would have started from the user saying "done".
#
# What counts as a receiver, read from the transcript (the Stop hook's `transcript_path`, which
# cupcake hands to every signal on stdin, and nothing else -- see cupcake_turn_scan.hook_transcript):
#   * a Monitor tool_use whose result said "Monitor started", with no finished
#     `<task-notification>` for it and no TaskStop naming its task since;
#   * a `run_in_background` Bash whose command waits for something and then exits -- an
#     `until`/`while` loop, `inotifywait`, `grep -m`, a `tail -f | grep -m` -- still running (its
#     result was the "running in background" acknowledgement and no finished notification came).
#     A background job that merely runs (a watcher, a build, a server) is not one: its exit is not
#     the awaited event, so it would never re-invoke the agent when the user acts.
#
# What is exempt, sentence by sentence: an ask whose answer is the user's own typed reply --
# subjective judgement ("does it look right"), a preference or decision, an approval, a credential
# or sudo, something to paste, what the user sees when no oracle covers it, when they want
# something, or what they know or can perceive ("can you tell X apart from Y", "can you tell
# whether ...", "can you distinguish ..."). Those need no instrument. A question with none of the ask phrases
# ("Does the font look right to you?") never matches in the first place. Also exempt: "I need its
# <output>" on a line that names a background task still running, with no "you" in the sentence --
# the agent naming what it awaits from its own task, whose finished notification is the receiver
# (see TASK_OUTPUT_NEED_RE).
#
# Only the closing prose run is scanned; quoted, backticked and fenced spans are stripped so quoting
# the rule cannot trip it.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
CUPCAKE_SIGNAL_EVENT=""
if [ ! -t 0 ]; then
	CUPCAKE_SIGNAL_EVENT="$(timeout 2 cat 2>/dev/null || true)"
fi
export CUPCAKE_SIGNAL_EVENT
python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_turn_scan as scan
except Exception:
    sys.exit(0)

path = scan.hook_transcript()
if not path:
    sys.exit(0)

events = scan.load_events(path)
turn = scan.last_text_turn(scan.split_turns(events))
if turn is None or not turn.text_runs:
    sys.exit(0)
closing = turn.text_runs[-1]

# Background tasks still running as the turn ends, matched as whole tokens. See TASK_OUTPUT_NEED_RE.
_running_ids = sorted({t.task_id for t in scan.running_background_tasks(events) if t.task_id},
                      key=len, reverse=True)
RUNNING_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, _running_ids)) + r")(?![A-Za-z0-9_-])"
) if _running_ids else None

# --- the ask -----------------------------------------------------------------------------------

def scrub(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    if RUNNING_ID_RE is not None:
        # "(task `b2mrgwhrl`)" names the task as surely as the bare id does; keep the id itself.
        text = re.sub(r"`(" + RUNNING_ID_RE.pattern + r")`", r"\1", text)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r'"[^"\n]*"', " ", text)
    text = re.sub(r"[“][^”\n]*[”]", " ", text)
    return text.replace("’", "'")

_NOT_PAST = r"(?!said\b|asked\b|wrote\b|mentioned\b|told\b|were\b|was\b|did\b|meant\b|described\b)"

# A bare simple-past verb after a temporal "you" narrates what the user already did, so it is a
# report, not an ask: on 2026-10-28 "As soon as you opened your world, Seamless created your
# advertisement lobby, ..." halted a finished report. Only the bare form is excused: "once you've
# opened" and "once you have opened" are present perfect and still ask. Present-tense verbs that end
# in -ed ("need", "proceed", "embed") stay asks.
_SIMPLE_PAST = (
    r"(?!(?!(?:need|feed|proceed|succeed|exceed|bleed|breed|heed|seed|speed|weed|shed|shred|embed"
    r"|wed|bed)\b)\w+ed\b)"
    r"(?!(?:went|ran|came|got|made|took|saw|left|began|brought|bought|caught|chose|drew|drove|fell"
    r"|fought|found|gave|held|kept|knew|lost|met|paid|rode|rose|sent|sat|spoke|stood|struck|swung"
    r"|threw|thought|understood|woke|won|had)\b)"
)

ASK_RE = re.compile(
    r"\bneed\s+you\s+to\b"
    # "First I need the game's point-light creation API." (2026-11-10 replay) still matches here, on
    # purpose. Its words are those of "I need the game's title screen" and "I need a fresh session":
    # only what the noun names says whether the agent fetches it or the user causes it, and no rule
    # here reads that. That halt was also never real: the sentence was the preamble to a tool call
    # the user rejected, and transcript 26d7bb3b holds no stop_hook_summary for that turn.
    r"|\bi(?:'d|\s+would|\s+still)?\s+need\s+(?!to\b|nothing\b|no\b|not\b)\w+"
    r"|\bwhat\s+i\s+need\b"
    # Not inside a hyphenated word: on 2026-11-10 "Main is pulled (..., a release-please changelog
    # bump)." halted a report on the release-please tool's name.
    r"|(?<![\w-])please\s+\w+"
    r"|\b(?:let|tell|ping|show)\s+me\s+(?:know\s+)?(?:when|once|after|as\s+soon\s+as|whether\s+it|if\s+it)\b"
    # Not straight after a negation: on 2026-11-10 "I wasn't waiting for you to run it." halted a
    # turn that said the opposite of an ask.
    r"|(?<!\bnot\s)(?<!n't\s)\bwaiting\s+(?:on|for)\s+you\b"
    # A temporal "you" clause is an ask only where it leads a sentence or clause ("Once you open
    # it, the probe records ...") or trails a first-person main clause ("I'll read the log when
    # you finish"). Mid-sentence it is usually the generic player of a game mechanic: on
    # 2026-10-04 "... catch a roll taken at the first chance in 5 of 6 cases after you run in"
    # halted a finished report.
    #
    # A clause-leading state form ("once you're broken", "when you are in the lobby") describes a
    # condition, not something the user is asked to do: on 2026-10-19 "Now, once you're broken, the
    # roll-out escapes his later hits." halted a finished report on a game mechanic. It counts only
    # when the sentence also speaks in the first person ("once you're in, I'll read it", "tell me").
    r"|(?:^[\s*•>-]*|[,;:–—(]\s*|\b(?:and|then|so)\s+)"
    r"(?:once|when|after|as\s+soon\s+as)\s+you(?:(?:'ve|\s+have)\s+|\s+(?!are\b)" + _SIMPLE_PAST + r")"
    + _NOT_PAST + r"\w+"
    r"|(?:^[\s*•>-]*|[,;:–—(]\s*|\b(?:and|then|so)\s+)"
    r"(?:once|when|after|as\s+soon\s+as)\s+you(?:'re|\s+are)\s+\w+"
    r"(?=[^.!?]*\b(?:i|i'll|i'm|i'd|me|my|we|we'll|us|our)\b)"
    r"|\b(?:i'll|i\s+will|i\s+can|we'll|we\s+will)\b[^.!?]*?\b(?:once|when|after|as\s+soon\s+as)\s+"
    r"you(?:(?:'re|'ve|\s+have|\s+are)\s+|\s+" + _SIMPLE_PAST + r")" + _NOT_PAST + r"\w+"
    r"|\b(?:can|could|would)\s+you\s+(?:please\s+)?\w+"
    r"|\bover\s+to\s+you\b|\byour\s+turn\b|\bin\s+your\s+court\b",
    re.IGNORECASE,
)

# Asks the user answers by typing a reply: no instrument could, or needs to, receive them.
EXEMPT_RE = re.compile(
    r"\blooks?\s+(?:right|good|ok|okay|correct|wrong|off|better|worse|fine|like)\b"
    r"|\bfeels?\b|\bprefer\w*\b|\bpreference\b|\bopinion\b|\bjudge?ment\b|\bthink\b"
    r"|\bwhich\s+(?:one|option|of|do\s+you|would\s+you)\b|\byou\s+want\b|\bwant\s+me\s+to\b"
    r"|\b(?:what|whether|if)\s+you\s+see\b|\bno\s+(?:\w+\s+){0,3}(?:oracle|semaphore)\b"
    r"|\bshould\s+i\b|\bshall\s+i\b|\bapprov\w*\b|\bsign[- ]?off\b|\bdecid\w*\b|\bdecision\b"
    r"|\byour\s+(?:call|choice)\b|\bconfirm\w*\b"
    r"|\bcredential\w*\b|\bpassword\b|\bpassphrase\b|\btoken\b|\b2fa\b|\botp\b|\blog\s*in\b|\bsign\s+in\b"
    r"|\bsudo\b|\bpurchase\b|\bpaste\b|\breply\s+with\b|\btype\s+(?:it|the|your)\b",
    re.IGNORECASE,
)

# A markdown table row is a description, never an ask: on 2026-10-18 the row "| Fixing the product
# before the tools | No guard | New rule `X`: once you report a tool miss, the turn may only start
# agents that work on guards ... |" halted a finished report, because the temporal clause after the
# colon looked like a clause-leading "once you".
TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*$", re.MULTILINE)

# A temporal "you" clause whose consequent states what a rule permits or forbids ("once you report
# a tool miss, the turn may only start agents ...") describes a trigger, not an ask. The clause is
# blanked so any other ask in the same sentence is still seen. "the probe records it" stays an ask:
# only deontic consequents qualify.
RULE_CONDITIONAL_RE = re.compile(
    r"\b(?:once|when|after|as\s+soon\s+as)\s+you\b[^,;.!?]*,\s*"
    r"(?:the|a|an|this|that|its?|each|every|any|no|such)\b[^,;.!?]*?"
    r"\b(?:may|must|cannot|can't|can\s+only|(?:is|are|gets?)\s+(?:not\s+)?"
    r"(?:allowed|refused|blocked|denied|permitted|rejected|halted))\b",
    re.IGNORECASE,
)

# An ask whose object is the user's typed reply ("I need your `authorize commit` reply", "I need the
# `authorize <sha>` phrase", "please reply") is answered by typing, so it needs no instrument: on
# 2026-10-26 "I need your `authorize commit` reply before I can fix either PR" halted a turn whose
# only possible next step was that reply. The phrase is blanked rather than exempting the sentence,
# so an action ask riding along ("I need your reply once you've used the item") is still seen.
TYPED_REPLY_ASK_RE = re.compile(
    r"\bi(?:'d|\s+would|\s+still)?\s+need\s+(?:your|the|an?|one|that|this)\s+"
    r"(?:[^\s.!?]+\s+){0,4}?"
    r"(?:reply|replies|answer|phrase|go-ahead|authori[sz]ation)\b"
    r"|\bplease\s+(?:reply|respond|answer)\b",
    re.IGNORECASE,
)

# The same ask with a pronoun for the reply: on 2026-11-10 the replay of transcript 9538a842
# (boundary 102) halted on "Formatting and committing both wait for your reply, and each reply pays
# for one commit, so I need it twice, once per branch:", whose next line was `authorize commit`.
# "It" stands for the reply only when the closing message hands `authorize commit` or `authorize
# <hex sha>` on a line of its own (HANDED_REPLY_LINE_RE), this sentence or the one before names the
# reply in TYPED_REPLY_ASK_RE's nouns (REPLY_NOUN_RE), and nothing but a clause boundary or a count
# follows the pronoun. Blanked like TYPED_REPLY_ASK_RE, so an action ask riding along is still seen.
# "I need it" with no such line, "I need it open", "I need the game open" and "I need you to run X"
# stay asks. HANDED_REPLY_LINE_RE lives in cupcake_turn_scan, where the idle-hold signal reads the same
# line as waiting on the user.
HANDED_REPLY_LINE_RE = scan.HANDED_REPLY_LINE_RE
REPLY_NOUN_RE = re.compile(
    r"\b(?:reply|replies|answer|phrase|go-ahead|authori[sz]ation)\b", re.IGNORECASE
)
REPLY_PRONOUN_NEED_RE = re.compile(
    r"\bi(?:'d|\s+would|\s+still)?\s+need\s+(?:it|them|both)\b"
    r"(?=\s*(?:[,.;:!?)–—]|$|(?:twice|once|again|first|both|each|per|too|now)\b"
    r"|(?:\d+|one|two|three|four)\s+(?:more\s+)?times?\b))",
    re.IGNORECASE,
)
HANDS_REPLY_PHRASE = HANDED_REPLY_LINE_RE.search(closing) is not None

# A clause-leading temporal "you" clause whose verb is a step of reasoning, not an act ("once you
# count", "once you factor in", "when you compare"), followed by a main clause that states a fact
# about its own subject ("three spells are strictly worse ..."), is the generic "you" of an
# argument: nobody is asked to count anything. On 2026-11-10 "Once you count what each one costs a
# real melee build, three spells are strictly worse than an ash that does the same job, and a
# fourth is on a Faith build." halted a finished report. The clause is blanked, so any other ask in
# the sentence ("..., please reload") is still seen, and only when the sentence names no first
# person and no instrument: "once you count the hits, tell me" and "once you compare them, the log
# shows it" stay asks. An acting verb ("once you open the inventory, the cadence shows up") is
# never blanked.
_REASONING_VERB = (
    r"(?:count(?:s|ed|ing)?|consider(?:s|ed|ing)?|factor(?:s|ed|ing)?|account(?:s|ed|ing)?"
    r"|compar(?:e|es|ed|ing)|weigh(?:s|ed|ing)?|includ(?:e|es|ed|ing)|add(?:s|ed|ing)?"
    r"|subtract(?:s|ed|ing)?|discount(?:s|ed|ing)?|normali[sz](?:e|es|ed|ing)|total(?:s|ed|ing)?"
    r"|look(?:s|ed|ing)?\s+at|t(?:ake|akes|aken|ook)\s+[^,;.!?]*?\binto\s+account)"
)
GENERIC_YOU_RE = re.compile(
    r"(?:^[\s*•>-]*|[,;:–—(]\s*|\b(?:and|then|so)\s+)"
    r"(?:once|when|after|as\s+soon\s+as)\s+you(?:'ve|\s+have)?\s+" + _REASONING_VERB
    + r"\b[^,;.!?]*,\s*"
    r"(?=(?:the|a|an|this|that|these|those|its?|their|each|every|any|all|both|no|none|only|most"
    r"|some|such|there|they|one|two|three|four|five|six|seven|eight|nine|ten|\d+|(?-i:[A-Z])\w*)\b)",
    re.IGNORECASE,
)
# A clause-leading "when you <present verb>," whose main clause opens on the game or a game system and
# a third-person present verb ("the game sends", "Seamless puts") states how the game behaves for any
# player: on 2026-11-10 "When you summon, the game sends peers just the three Moongrum IDs from ..."
# halted a finished report. Blanked, like GENERIC_YOU_RE, only when the sentence names no first
# person and no instrument, so "when you summon, tell me", "when you summon, I'll read the log" and
# "when you summon, the probe records it" stay asks, and any other ask in the sentence (", so please
# reload") is still seen. Only "when": "once you summon" and "as soon as you summon" wait for the
# user and stay asks, and so do "when you've summoned" and any subject that is not the game's.
GAME_MECHANIC_YOU_RE = re.compile(
    r"(?:^[\s*•>-]*|[,;:–—(]\s*|\b(?:and|then|so)\s+)"
    r"when\s+you\s+(?!are\b|have\b)\w+\b[^,;.!?]*,\s*"
    r"(?=(?:the\s+(?:game|engine|server)|seamless|steam|elden\s+ring)\s+"
    r"(?:(?:only|still|also|just|then|always|never)\s+)?\w+s\b)",
    re.IGNORECASE,
)
# "Can you tell X apart from Y", "can you tell whether ...", "can you distinguish ..." ask what the
# user knows or can perceive, and their typed reply is the answer: no event is awaited. On 2026-11-10
# "Before a Glintstone Cometshard's bolt leaves the staff, can you tell it apart from a Comet, Shard
# Spiral or Night Comet, and a tap from a charge?" halted on the bare "can you <verb>" ask. Only the
# ask is blanked, so another ask in the same sentence (", and please reload") is still seen. "Can you
# tell me when it appears" asks for a report of a future event and stays an ask, and so does "can
# you tell the host to rehost": "tell" followed by me/us, or by "to" before the "apart"/"from", is
# not this form.
KNOWLEDGE_QUESTION_RE = re.compile(
    r"\b(?:can|could)\s+you\s+(?:(?:\w+ly|still|even|ever|always|already)\s+)?"
    r"(?:tell\s+(?!me\b|us\b)(?=(?:(?!\bto\b)[^.!?])*?\b(?:apart|from)\b)"
    r"|tell\s+(?:whether|if|which|the\s+difference)\b"
    r"|(?:distinguish|differentiate|discern)\b(?!\s+(?:me|us)\b))",
    re.IGNORECASE,
)
FIRST_PERSON_OR_INSTRUMENT_RE = re.compile(
    r"\b(?:i|i'll|i'm|i'd|i've|me|my|we|we'll|we're|us|our|probe|log|logs|scan|trace|tracer"
    r"|monitor|watcher|hook|oracle|frida|telemetry)\b",
    re.IGNORECASE,
)

# "I need its numbers" on a line that names a background task still running states what the agent
# awaits from that task, not something it asks of the user: the task's own finished notification
# wakes the agent. On 2026-11-10 "- **r8 full run** (task b2mrgwhrl): 850 of 1,276 attacks done. I
# need its numbers to recheck the four control pairs ..." halted a status report, with b2mrgwhrl a
# backgrounded Bash still running. The need is blanked only when its object opens with its/their,
# the line names a task that is still running, and the sentence has no second person anywhere, so
# any other ask in it is still seen. "I need you to", "I need your log", "I need its numbers from
# you", "I need the game open" and "I need the item list closed" stay asks, on a task line or off
# one: the game and the item list are not the task's, and a running watcher's exit is not the
# user's press.
TASK_OUTPUT_NEED_RE = re.compile(
    r"\b(?:what\s+)?i(?:'d|\s+would|\s+still)?\s+need(?:\s+(?:is|are))?\s+(?:its|their)\b",
    re.IGNORECASE,
)
SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours|yourself|yourselves)\b", re.IGNORECASE)


def sentences(text):
    """(sentence, whether its line names a running background task) for each sentence."""
    for line in text.split("\n"):
        on_task_line = RUNNING_ID_RE is not None and RUNNING_ID_RE.search(line) is not None
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            yield sentence, on_task_line


offending = None
previous = ""
for sentence, on_task_line in sentences(TABLE_ROW_RE.sub(" ", scrub(closing))):
    s = KNOWLEDGE_QUESTION_RE.sub(" ", RULE_CONDITIONAL_RE.sub(" ", sentence))
    s = TYPED_REPLY_ASK_RE.sub(" ", s).strip()
    if HANDS_REPLY_PHRASE and REPLY_NOUN_RE.search(previous + " " + sentence):
        s = REPLY_PRONOUN_NEED_RE.sub(" ", s).strip()
    if sentence.strip():
        previous = sentence
    if not FIRST_PERSON_OR_INSTRUMENT_RE.search(s):
        s = GENERIC_YOU_RE.sub(" ", s).strip()
        s = GAME_MECHANIC_YOU_RE.sub(" ", s).strip()
    if on_task_line and not SECOND_PERSON_RE.search(sentence):
        s = TASK_OUTPUT_NEED_RE.sub(" ", s).strip()
    if not s or not ASK_RE.search(s):
        continue
    if EXEMPT_RE.search(s):
        continue
    offending = s
    break
if offending is None:
    sys.exit(0)

# --- the receiver ------------------------------------------------------------------------------

WAIT_CMD_RE = re.compile(
    r"\buntil\b|\bwhile\b[^\n]*\bdo\b|\binotifywait\b"
    r"|\bgrep\s+(?:-[A-Za-z]*m\s*\d|--max-count)"
    r"|\btail\b[^|\n]*\s-[A-Za-z]*[fF]\b[^\n]*\|\s*(?:head\b|sed\s+[^|\n]*\bq\b|awk\b[^\n]*\bexit\b)",
)
MONITOR_STARTED_RE = re.compile(r"\bmonitor\s+started\b", re.IGNORECASE)
TASK_ID_RE = re.compile(r"\(task\s+([A-Za-z0-9_-]+)|\bID:\s*([A-Za-z0-9_-]+)", re.IGNORECASE)

live = {}       # tool_use_id -> (kind, command)
task_of = {}    # task id -> tool_use_id
pending = {}    # tool_use_id -> (kind, command), awaiting its first result
for ev in events:
    raw = scan._user_content_string(ev)
    if raw and scan.TASK_NOTIFICATION_RE.search(raw):
        m = scan.TASK_TOOL_USE_ID_RE.search(raw)
        st = scan.TASK_STATUS_RE.search(raw)
        if m and st and st.group(1).lower() in scan.FINISHED_STATUSES:
            live.pop(m.group(1), None)
        continue
    content = ev.get("message", {}).get("content")
    if not isinstance(content, list):
        continue
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name") or ""
            inp = block.get("input") or {}
            if not isinstance(inp, dict):
                continue
            cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""
            if name == "Monitor":
                pending[block.get("id") or ""] = ("monitor", cmd)
            elif name == "Bash" and inp.get("run_in_background"):
                pending[block.get("id") or ""] = ("bash", cmd)
            elif name in ("TaskStop", "KillShell", "KillBash"):
                tid = inp.get("task_id") or inp.get("shell_id") or ""
                live.pop(task_of.get(tid, tid), None)
        elif block.get("type") == "tool_result":
            uid = block.get("tool_use_id") or ""
            if uid not in pending:
                continue
            kind, cmd = pending.pop(uid)
            text = scan._result_text(block)
            if block.get("is_error"):
                continue
            started = (
                MONITOR_STARTED_RE.search(text) if kind == "monitor"
                else scan.BACKGROUND_LAUNCH_RE.search(text)
            )
            if not started:
                continue  # refused by a hook, failed, or already finished
            live[uid] = (kind, cmd)
            m = TASK_ID_RE.search(text)
            if m:
                task_of[m.group(1) or m.group(2)] = uid

for kind, cmd in live.values():
    if kind == "monitor" or WAIT_CMD_RE.search(cmd or ""):
        sys.exit(0)

sys.stdout.write("ASKNORECEIVER:" + offending[:300])
PY
