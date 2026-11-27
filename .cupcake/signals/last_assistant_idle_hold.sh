#!/usr/bin/env bash
# Cupcake signal: last_assistant_idle_hold
#
# Scans the most recently completed assistant turn of the current session transcript and returns a
# tagged idle-hold marker, or empty if the turn is clean. Consumed by two policies:
#   * idle_hold (Stop): halts turn-end so the agent does non-overlapping work or justifies the wait.
#   * idle_hold_reminder (UserPromptSubmit): standing reminder + interlock backstop (catches an
#     interrupted turn the Stop halt could not see).
#
# Banned class (persistent user directive 2026-07-17, recurring anti-pattern):
#   The agent announces it is IDLING / holding / standing by while a background task runs, without
#   justification. Phrases like "I'm holding", "holding for", "holding off", "standing by",
#   "I'll wait for", "waiting for X before", "waiting on X rather than", "nothing to do but wait",
#   "I'll pause here", "let it run and wait". Emitted as  IDLEHOLD:<phrase>.
#
# Two exemptions suppress the flag (the turn is not idle -- it is either productive or justified):
#   (a) justification prose -- the same turn contains "I would normally have <...> but <...>" /
#       "normally I'd <...> however <...>": the agent acknowledges non-overlapping work exists and
#       states a reason it could not be done. The user can validate that, so it is allowed.
#   (b) substantive work -- the same turn contains a substantive tool_use: an Edit/Write/Agent
#       tool_use, or a Bash command that is not a pure status/log peek. A turn whose only Bash calls
#       are tail/cat/head/wc/grep/echo/ls of a log/output file is a "status peek" and does not count
#       as substantive (so "holding" + only peeking still flags).
#
# One further carve-out inside the phrase match: a wait that is legitimately blocked on the user
# ("waiting for user confirmation", "holding for the user to drive", "I'll wait for you") is not
# idling -- the agent genuinely cannot proceed -- so those are excluded from the phrase hit. A message
# that hands a reply phrase on a line of its own (`authorize commit`, `authorize <sha>`) is blocked on
# the user as a whole and emits neither tag (2026-11-11; the argument is at the VERBOSEPAUSE gate).
#
# Tightened rule (persistent user directive 2026-07-17): a blocked-pause message must be TERSE. When a
# turn ends as a pure pause (no substantive tool_use) that is not blocked on the user, and its message
# is long / multi-topic (>450 chars, or >3 sentences, or any heading/bullet/numbered line, or more than
# one paragraph), the signal emits  VERBOSEPAUSE:<n-chars>  instead of IDLEHOLD. This closes the gap
# where the idle-hold rule accepted a long "justified" hold: a genuinely blocked pause must be only a
# short, precise statement of what it is blocked on -- no status summaries, findings recaps, plans, or
# next-step narration. VERBOSEPAUSE takes precedence over IDLEHOLD and fires even when justification
# prose is present (that verbose justified hold is exactly what the new rule bans). A turn that did
# substantive work, or one genuinely blocked on the user, is exempt.
#
# On Stop, a pause must also name what it is blocked on (user directive 2026-11-11): when
# scripts/cupcake_session_tasks.py measures shells or agents of this session running and the closing
# message names none of them, the signal adds a second line `UNNAMED:<report>` after VERBOSEPAUSE or
# IDLEHOLD, or emits `UNNAMEDPAUSE:<report>` alone. idle_hold.rego halts on it under
# `ER-EFFECTS-PAUSE-NAMES-BLOCKER` and quotes the report. The argument is at the end of the program.
#
# Why a whole-turn scan + both EVENTS: mirrors last_assistant_authority_agreement -- an early-message
# slip must not be masked by a later clean block (whole-turn scan), and an interrupted turn fires no
# Stop event, so the same signal is routed into the UserPromptSubmit interlock which always runs.
# "Last completed turn" = the last non-empty run of assistant text bounded by real user prompts;
# tool-result carrier "user" events do not split a turn.
#
# The shared half lives in scripts/cupcake_turn_scan.py (2026-08-22): transcript discovery, turn
# bucketing, the status-peek command list, and the blocked-on-user phrasing are imported, not copied,
# so this guard and last_assistant_unexecuted_promise.sh can never drift into disagreeing about
# whether the same turn did real work. Only the idle-specific prose classification is local.
#
# Double-quoted spans are stripped before prose matching so quoting the ban (this file, the reminder
# text, or a meta-discussion like the phrase "I'm holding") does not false-trip; a real unquoted
# announcement still matches. Fail-open (empty output) on any error so a transcript hiccup cannot
# wedge the session.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
# The hook event on stdin names this session's transcript; see cupcake_turn_scan.hook_transcript.
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
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)

events = scan.load_events(path)
turns = scan.split_turns(events)
turn = scan.last_text_turn(turns)
if turn is None:
    sys.exit(0)


prompts = [ev for ev in events if scan.is_real_user_prompt(ev)]


def prompt_text(ev):
    content = ev.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


# turns[0] precedes any prompt, so turns[k] was opened by prompts[k - 1].
turn_index = next((i for i, t in enumerate(turns) if t is turn), -1)
last_turn = turn.text
turn_has_work = turn.work

# Strip double-quoted spans so quoting the ban does not count as using it (single quotes are left
# alone because the phrases themselves contain apostrophes, e.g. I'm / I'll).
scrubbed = re.sub(r'"[^"]*"', " ", last_turn)

# Idle-announcement phrases. Word-boundaried / anchored to avoid incidental prose.
IDLE_RE = re.compile(
    r"\b(i'?m\s+holding|i\s+am\s+holding|holding\s+for|holding\s+off"
    r"|standing\s+by"
    r"|i'?ll\s+wait\s+for|i\s+will\s+wait\s+for"
    r"|waiting\s+(?:for|on)\b[^.\n]*?\b(?:before|rather\s+than)\b"
    r"|nothing\s+to\s+do\s+(?:but|while)\s+wait"
    r"|i'?ll\s+pause\s+here"
    r"|let\s+it\s+(?:run|complete)\s+and\s+wait)\b",
    re.IGNORECASE,
)

# A wait blocked on the user is legitimate, not idling. If the phrase's immediate context names the
# user as the blocker, do not count it as an idle hit.
USER_BLOCK_RE = re.compile(r"\b(?:the\s+)?(?:user|users|you|your)\b", re.IGNORECASE)

# Exemption (a): explicit justification prose acknowledging non-overlapping work + a reason.
JUSTIFY_RE = re.compile(
    r"i\s+would\s+normally\b[^.\n]*\b(?:but|however)\b"
    r"|normally\s+i'?d\b[^.\n]*\b(?:but|however)\b"
    r"|normally\s+i\s+would\b[^.\n]*\b(?:but|however)\b",
    re.IGNORECASE,
)


def find_idle_phrase(text):
    for m in IDLE_RE.finditer(text):
        window = text[m.start():m.end() + 48]
        if USER_BLOCK_RE.search(window):
            continue  # blocked on the user -> legitimate, not idling
        return m.group(1).strip()
    return None


# --- VERBOSEPAUSE (tightened rule, user directive 2026-07-17) -------------------------------------
# When a turn ends as a pure pause (no substantive tool_use) whose final message is long / multi-topic,
# the blocked-pause message is too verbose: a genuinely blocked pause must be only a short, precise
# statement of what it is blocked on -- no status summaries, findings recaps, plans, or next-step
# narration. This TIGHTENS the idle-hold rule, which previously accepted a long "justified" hold as
# fine; a verbose justified hold is now a violation. A wait genuinely blocked on the user (awaiting
# their answer/drive) is exempt, as is any turn that did substantive work. Emitted as VERBOSEPAUSE:<n>
# (n = char count of the message). VERBOSEPAUSE takes precedence over IDLEHOLD (its "be terse"
# guidance is the more specific correction, and it must fire even when a justification paragraph is
# present -- that is exactly the long "justified" hold the new rule bans).
#
# The blocked-on-user test is shared: scan.blocked_on_user reads USER_WAIT_RE's phrasings. A reply
# phrase handed on a line of its own (scan.hands_reply_phrase) is read here only, not in the shared
# test, which also excuses a diagnosis with no fix and an unexecuted promise.

# Long / multi-topic heuristic: a blocked-pause note should be one or two short sentences. Flag when the
# message is >450 chars, or has >3 sentences, or contains any heading/bullet/numbered line, or spans
# more than one paragraph (blank-line separated). Returns the char count when long, else None.
HEADING_BULLET_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)")
PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")


def verbose_char_count(text):
    t = text.strip()
    n = len(t)
    if n == 0:
        return None
    if n > 450:
        return n
    if PARAGRAPH_BREAK_RE.search(t):
        return n
    if HEADING_BULLET_RE.search(t):
        return n
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", t) if s.strip()]
    if len(sentences) > 3:
        return n
    return None


phrase = find_idle_phrase(scrubbed)

# A pure-pause turn (no substantive tool_use) that is not blocked on the user and whose message is long
# -> VERBOSEPAUSE. Measured on the raw last-turn text (not the quote-scrubbed copy) so length is not
# undercounted; user-block is checked on the scrubbed copy so a merely-quoted "wait for you" does not
# exempt.
# A prose-only answer is not a pause. VERBOSEPAUSE is about a turn that stops with something
# PENDING: either live background work still running at turn-end, or a message that announces an
# idle/hold itself. A turn that simply answers the user's question in prose waits on nothing, and
# neither did the corrective rewrite the wall_of_text guard used to force -- that one has no tool_use
# by construction, since its whole job is to restate an answer. Without this gate the rule degenerates
# into "no prose answer may exceed 450 chars", which is the wall_of_text guard's job and not this
# one's. Measured 2026-08-22: it halted three consecutive prose answers with no background task
# running at all, twice on rewrites the wall_of_text halt had just demanded. (That halt was removed
# later the same day -- it fired after the text was already on screen and its verdict was printed to
# the user, so it cost a third reading; the rule now injects an invisible one-paragraph correction on
# UserPromptSubmit instead. The gate below still matters: prose answers remain out of scope here.)
# Three ways a turn can be a pause. Events alone are not enough: a turn can narrate work in flight
# that the transcript cannot see (a subagent compiling, a gate running elsewhere), and that turn does
# owe a terse blocked-note. What none of these match is a plain answer.
#
# "still running" counts only as a present-tense claim: "<it> is still compiling", or a sentence that
# opens "Still running". Measured 2026-10-19: a closing report describing a fixed bug -- "booked as
# a job still running", "look like a job still running" -- halted as a verbose pause with nothing
# live, because the bare phrase matched inside a description of what the scanner used to get wrong.
#
# The pattern is two halves. The first states that something is still running; the second defers
# work behind it ("once it lands", "in the meantime"). Only the first can be a sentence that accounts
# for a running task, which matters below.
STILL_RUNNING_PATTERN = (
    r"(?:(?:\b(?:is|are)|['’](?:s|re))\s+still|(?:^|[.!?]\s+)still)\s+"
    r"(?:running|compiling|building|going|underway|in flight)\b"
)
DEFERRAL_PATTERN = (
    r"\b(?:while (?:it|that|they|those) (?:run|runs|compile|compiles|build|builds)"
    r"|once (?:it|that|they|the \w+) (?:lands|land|finishes|finish|completes|complete|returns|return|is done|are done)"
    r"|when (?:it|that|they) (?:lands|land|finishes|finish|completes|complete|returns|return)"
    r"|pick (?:this|it) back up"
    r"|in the meantime)\b"
)
PENDING_WORK_RE = re.compile(STILL_RUNNING_PATTERN + "|" + DEFERRAL_PATTERN, re.IGNORECASE)
STILL_RUNNING_RE = re.compile(STILL_RUNNING_PATTERN, re.IGNORECASE)
DEFERRAL_RE = re.compile(DEFERRAL_PATTERN, re.IGNORECASE)

# Live background work alone does not make a reply to the user a pause. Measured 2026-10-04: the
# user asked whether Eruption plus a greased weapon swap applies the buff through Eruption's damage,
# the closing message was the one-paragraph answer (735 chars), and a board-generation run unrelated
# to the question was still live -- so this rule halted the answer as a verbose blocked pause. When
# the closing message answers a prompt the user typed, the live job makes it a pause only if the
# message itself says it is blocked on or waiting for something; "Blocked on X ... <long recap>"
# still trips.
#
# Widened 2026-11-11 from "a typed question" to "the prompt the user typed". Measured in session
# 5ec7b779: the user asked "should I ever listen to you for timelines? ...", the answer ended
# without naming r10, and no_unexplained_running_tasks halted it until the message
# named the task "in a sentence that says what it is still waiting for or doing". The rewrite added
# "r10 (bdj5lkbhk) is still running because it gives the v9 timing baseline the speed-up will be
# measured against." and this rule halted it as a 735-char blocked pause. Three things made it a
# pause: the turn being judged was opened by the Stop-hook feedback, which has no question mark and
# says "still running", so the answer no longer counted as answering the user; the very sentence the
# other guard demands matched PENDING_WORK_RE; and the answer's own "it decides whether to wait for a
# run" read as announcing a wait (ANNOUNCES_BLOCK_RE, below). Between the two guards an answer of
# normal length had no way through while anything ran. So the prompt a closing message answers is
# found by walking back over Stop-hook feedback to the one before it, a sentence that only says some
# work is still running is not counted as pending work in a reply to the user, a typed request counts
# as well as a question, and a wait is read only where the message says the turn is waiting. Replayed
# over every Stop boundary of the 40 newest transcripts in this project (960 boundaries, worktree
# replay): 15 of 28 halts were answers like this one (to "Is it reasonable to build similarity
# matrixies?", "When did we crash?", "how is the item 0? lol. That bad?", "steam did not start",
# "startup the game with ...", "She's a dummy. You're going to have to give me something she can put
# in her File Explorer bar") and now pass. The other 13 still halt: every one that said it was
# waiting ("Two background runs are still going, and I'm waiting on both:", "I'm holding them so
# ...", "Blocked until the two background agents report back:") and every one opened by a task
# notification or a /loop wake-up rather than by the user. No boundary halts that did not before.
TASK_NOTIFICATION_PROMPT_RE = re.compile(r"<task-notification>", re.IGNORECASE)
# "Blocked on" or "waiting for" said of the turn: at the head of a sentence or clause ("Blocked until
# the two background agents report back:", "the fix is in, waiting on the gate"), or after a subject
# and copula ("I'm waiting on both", "the merge is blocked on r9"). A bare "wait for" is left out:
# in a reply it is the topic far more often than the turn's state. Measured 2026-11-11, the two
# replies it halted on the replay: "it decides whether to wait for a run or stop it" (the timelines
# answer above) and "Steam is fine now, so nothing was left for it to wait on" (session 965886fc).
# ^ is per line because `pause_text` puts each sentence on its own line.
ANNOUNCES_BLOCK_RE = re.compile(
    r"(?:^\s*|[;:,—–]\s*|--\s*"
    r"|\b(?:and|but|so|still|now|currently|i'?m|i\s+am|we'?re|we\s+are|is|are)\s+|['’](?:s|re)\s+)"
    r"(?:blocked\s+(?:on|by|until)|waiting\s+(?:for|on|until))\b"
    r"|^\s*wait\s+(?:for|on|until)\b"
    r"|\buntil\s+(?:it|that|they|the\s+\w+)\s+(?:finishes|finish|lands|land|completes|complete)\b",
    re.IGNORECASE | re.MULTILINE,
)
# A question about the background work itself ("Anything yet?", "How is it going?") is answered
# from the job's state, so it does not exempt the reply. Nor does a prompt that names a running
# task's id ("Check the board regeneration (task babz84j4f); when it finishes, ...").
# The forms ending in "?" sit outside the closing \b: a \b after "?" needs a word character next, so
# until 2026-11-11 "Is it done yet?" and "finished?" at the end of a prompt never matched.
STATUS_PROMPT_RE = re.compile(
    r"\b(?:anything\s+yet|how(?:'s|\s+is|\s+are)\s+(?:it|that|they|things)\s+going"
    r"|status|progress|still\s+running|eta)\b"
    r"|\b(?:\w+\s+yet|done|finished)\s*\?",
    re.IGNORECASE,
)
# The harness writes a Stop hook's verdict back as a user event (`isMeta`, no `origin`) that opens
# the turn holding the rewrite; the rewrite still answers the prompt before it.
STOP_FEEDBACK_RE = re.compile(r"\A\s*Stop hook feedback:")
# User events no one typed: command and caveat markup (`<command-name>`, `<local-command-stdout>`,
# `<bash-input>`), a task notification, and the interruption marker.
NOT_TYPED_RE = re.compile(r"\A\s*(?:<|\[Request interrupted)")


def answered_prompt(index):
    j = index - 1
    while 0 <= j < len(prompts) and STOP_FEEDBACK_RE.match(prompt_text(prompts[j])):
        j -= 1
    return prompts[j] if 0 <= j < len(prompts) else None


def typed_by_user(ev):
    """A prompt the user wrote. In a live transcript that is `origin.kind == "human"`; an SDK prompt
    and a fixture carry no `origin`, so a missing one is accepted. A /loop wake-up, a skill's text, a
    local-command caveat and a compaction summary are `isMeta` or `isCompactSummary`."""
    if ev is None or ev.get("isMeta") or ev.get("isCompactSummary"):
        return False
    origin = ev.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    text = prompt_text(ev)
    return not NOT_TYPED_RE.match(text) and not TASK_NOTIFICATION_PROMPT_RE.search(text)


def names_running_task(text):
    return any(
        re.search(r"(?<![A-Za-z0-9_-])" + re.escape(t.task_id) + r"(?![A-Za-z0-9_-])", text)
        for t in scan.running_background_tasks(events)
    )


answered = answered_prompt(turn_index)
answered_text = prompt_text(answered) if answered is not None else ""
answers_user_prompt = (
    typed_by_user(answered)
    and not STATUS_PROMPT_RE.search(answered_text)
    and not names_running_task(answered_text)
)

# A sentence that says some work is still running, and neither defers anything behind it nor says the
# turn is waiting or holding, accounts for that work; no_unexplained_running_tasks requires one for
# every task left running. In a reply to the user it is not the reply pausing, so it is left out of
# the pending-work test. Split the way no_unexplained_running_tasks splits sentences.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def accounts_for_running_work(sentence):
    return (
        bool(STILL_RUNNING_RE.search(sentence))
        and not DEFERRAL_RE.search(sentence)
        and not ANNOUNCES_BLOCK_RE.search(sentence)
        and not IDLE_RE.search(sentence)
    )


pause_text = scrubbed
if answers_user_prompt:
    pause_text = "\n".join(
        s for s in SENTENCE_SPLIT_RE.split(scrubbed) if not accounts_for_running_work(s)
    )

live_bg_is_pause = bool(scan.live_background_work(events)) and (
    not answers_user_prompt or bool(ANNOUNCES_BLOCK_RE.search(pause_text))
)

# A message that hands a reply phrase on a line of its own -- `authorize commit` or `authorize <sha>`,
# scan.HANDED_REPLY_LINE_RE -- is blocked on the user, and the exemption is total: the phrase line is
# not merely left out of the length and paragraph count. Measured 2026-11-11, in one turn with r9
# (task b2dpglvu7) and a release-model subagent live: a 113-char note -- "Blocked on r9 (task
# b2dpglvu7) and the release-model agent; the guards commit needs your reply:", a blank line, and
# `authorize commit` alone on its line -- halted because PARAGRAPH_BREAK_RE read the blank line as a
# second paragraph, so any blocked note handing a phrase was verbose at any length; and the turn's
# earlier 966-char closing message, a finished survey ending on the same phrase line, halted on its
# length. Leaving the phrase line out of the count clears the first and still halts the second, and
# the second is the report the user reads to decide whether to send the phrase, since it says what
# the commit holds. A turn that ends on the user's typed reply is not paused on a background task,
# the same as one that says "I need you to"; how long its report runs is the wall_of_text
# correction's business, as for any answer. What still halts: a blocked note with a recap and no
# phrase line, and a phrase named inside a sentence ("the commit needs the authorize commit reply")
# or a table row, neither of which is a line of its own.
verbose_n = None
turn_is_pause = (
    bool(phrase)
    or live_bg_is_pause
    or bool(PENDING_WORK_RE.search(pause_text))
)
if (
    turn_is_pause
    and not turn_has_work
    and not scan.blocked_on_user(scrubbed)
    and not scan.hands_reply_phrase(scrubbed)
):
    verbose_n = verbose_char_count(last_turn)

# --- a pause names what it is blocked on (user directive 2026-11-11) -----------------------------
# User words, verbatim: "So next time, how are you going to monitor your own shells and understand
# when they are running so you can communicate clearly to the user why you're paused (blocked because
# X shell is running) or whatever you're doing". The user's view showed shells and agents the main
# session could not account for: they were a subagent's, and a subagent's shells never reach the main
# transcript. So at Stop, a turn that pauses (the same test as VERBOSEPAUSE: a pause, no substantive
# work, not waiting on the user) is checked against what scripts/cupcake_session_tasks.py measures
# running now from the process table: every shell of the session or of its subagents, every agent
# still working. When anything runs and the closing message names none of it -- a shell's task id, an
# agent's id or description, or a Monitor the transcript shows running (a Monitor writes no task
# output file, so the process table cannot list it) -- the report rides along on a second line,
# `UNNAMED:<report>`, after VERBOSEPAUSE or IDLEHOLD, or alone as `UNNAMEDPAUSE:<report>`. idle_hold.rego
# halts on it and quotes the report, so the rewrite can name what it is blocked on.
#
# Only on Stop: the UserPromptSubmit interlock reads this signal too, and at that point what runs is
# no longer what ran when the turn ended; session_tasks_context.rego shows the live report there
# instead. Nothing running, or a report that cannot be measured (no Claude Code process above this
# one, no transcript), asks nothing: a pause with nothing measured running is judged as before.
#
# A pause that says it waits on the user is not asked either, in USER_WAIT_RE's phrasings or as
# "waiting on you", "blocked until you", "waiting for the user". Measured on the replay
# (scripts/audit-idle-hold-replay.py, 40 newest transcripts, 973 Stop boundaries): "Waiting on you to
# say whether to relaunch Elden Ring." was the one closing message it asked to name an agent that was
# not waiting on that agent.
WAITS_ON_USER_RE = re.compile(
    r"\b(?:blocked|waiting|wait)\s+(?:on|for|until)\s+(?:you|your|the\s+user)\b", re.IGNORECASE)
try:
    hook_event = json.loads(os.environ.get("CUPCAKE_SIGNAL_EVENT") or "{}")
except ValueError:
    hook_event = {}
unnamed = ""
if (
    isinstance(hook_event, dict)
    and hook_event.get("hook_event_name") == "Stop"
    and turn_is_pause
    and not turn_has_work
    and not scan.blocked_on_user(scrubbed)
    and not scan.hands_reply_phrase(scrubbed)
    and not WAITS_ON_USER_RE.search(scrubbed)
):
    try:
        import cupcake_session_tasks as tasks

        report = tasks.hook_report(hook_event, main_events=events)
        if tasks.live(report):
            runs = turn.text_runs
            closing = runs[-1] if runs else last_turn
            monitors = [t.task_id for t in scan.running_background_tasks(events) if t.kind == "monitor"]
            if not tasks.named_blocker(closing, report, monitors):
                unnamed = tasks.brief(report)
    except Exception:
        unnamed = ""  # fail open: an unmeasurable report never halts a turn
suffix = "\nUNNAMED:" + unnamed if unnamed else ""

# IDLEHOLD takes only the handed line, not USER_WAIT_RE: a "need you to" in some
# other sentence never excused an idle phrase, which find_idle_phrase ties to the user only within 48
# characters of the phrase, and that stays. A line that is nothing but the reply phrase cannot be
# incidental the way a "you" in another sentence can; it is the ask the turn ends on.
if verbose_n is not None:
    sys.stdout.write("VERBOSEPAUSE:" + str(verbose_n) + suffix)
elif (
    phrase
    and not turn_has_work
    and not JUSTIFY_RE.search(scrubbed)
    and not scan.hands_reply_phrase(scrubbed)
):
    sys.stdout.write("IDLEHOLD:" + phrase + suffix)
elif unnamed:
    sys.stdout.write("UNNAMEDPAUSE:" + unnamed)
PY
