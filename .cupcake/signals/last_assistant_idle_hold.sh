#!/usr/bin/env bash
# Cupcake signal: last_assistant_idle_hold
#
# Scans the most recently COMPLETED assistant turn of the current session transcript and returns a
# TAGGED idle-hold marker, or empty if the turn is clean. Consumed by TWO policies:
#   * idle_hold (Stop): halts turn-end so the agent does non-overlapping work or justifies the wait.
#   * idle_hold_reminder (UserPromptSubmit): standing reminder + interlock backstop (catches an
#     interrupted turn the Stop halt could not see).
#
# BANNED CLASS (persistent user directive 2026-07-17, recurring anti-pattern):
#   The agent announces it is IDLING / HOLDING / STANDING BY while a background task runs, WITHOUT
#   justification. Phrases like "I'm holding", "holding for", "holding off", "standing by",
#   "I'll wait for", "waiting for X before", "waiting on X rather than", "nothing to do but wait",
#   "I'll pause here", "let it run and wait". Emitted as  IDLEHOLD:<phrase>.
#
# TWO EXEMPTIONS suppress the flag (the turn is NOT idle -- it is either productive or justified):
#   (a) JUSTIFICATION PROSE -- the same turn contains "I would normally have <...> but <...>" /
#       "normally I'd <...> however <...>": the agent acknowledges non-overlapping work exists and
#       states a reason it could not be done. The user can validate that, so it is allowed.
#   (b) SUBSTANTIVE WORK -- the same turn contains a substantive tool_use: an Edit/Write/Agent
#       tool_use, or a Bash command that is NOT a pure status/log peek. A turn whose only Bash calls
#       are tail/cat/head/wc/grep/echo/ls of a log/output file is a "status peek" and does NOT count
#       as substantive (so "holding" + only peeking still flags).
#
# One further carve-out inside the phrase match: a wait that is legitimately BLOCKED ON THE USER
# ("waiting for user confirmation", "holding for the user to drive", "I'll wait for you") is NOT
# idling -- the agent genuinely cannot proceed -- so those are excluded from the phrase hit.
#
# TIGHTENED RULE (persistent user directive 2026-07-17): a blocked-pause message must be TERSE. When a
# turn ends as a PURE PAUSE (no substantive tool_use) that is NOT blocked on the user, and its message
# is LONG / multi-topic (>450 chars, OR >3 sentences, OR any heading/bullet/numbered line, OR more than
# one paragraph), the signal emits  VERBOSEPAUSE:<n-chars>  instead of IDLEHOLD. This closes the gap
# where the idle-hold rule accepted a long "justified" hold: a genuinely blocked pause must be ONLY a
# short, precise statement of what it is blocked on -- no status summaries, findings recaps, plans, or
# next-step narration. VERBOSEPAUSE takes precedence over IDLEHOLD and fires even when justification
# prose is present (that verbose justified hold is exactly what the new rule bans). A turn that did
# substantive work, or one genuinely blocked on the USER, is exempt.
#
# WHY A WHOLE-TURN SCAN + BOTH EVENTS: mirrors last_assistant_authority_agreement -- an early-message
# slip must not be masked by a later clean block (whole-turn scan), and an INTERRUPTED turn fires no
# Stop event, so the same signal is routed into the UserPromptSubmit interlock which always runs.
# "Last completed turn" = the last non-empty run of assistant text bounded by real user prompts;
# tool-result carrier "user" events do NOT split a turn.
#
# THE SHARED HALF LIVES IN scripts/cupcake_turn_scan.py (2026-08-22): transcript discovery, turn
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
python3 - <<'PY' 2>/dev/null || true
import os, re, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_turn_scan as scan
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.latest_transcript()
if not path:
    sys.exit(0)

events = scan.load_events(path)
turn = scan.last_text_turn(scan.split_turns(events))
if turn is None:
    sys.exit(0)
last_turn = turn.text
turn_has_work = turn.work

# Strip DOUBLE-quoted spans so quoting the ban does not count as using it (single quotes are left
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

# A wait BLOCKED ON THE USER is legitimate, not idling. If the phrase's immediate context names the
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
# When a turn ends as a PURE PAUSE (no substantive tool_use) whose final message is LONG / multi-topic,
# the blocked-pause message is too verbose: a genuinely blocked pause must be ONLY a short, precise
# statement of what it is blocked on -- no status summaries, findings recaps, plans, or next-step
# narration. This TIGHTENS the idle-hold rule, which previously accepted a long "justified" hold as
# fine; a verbose justified hold is now a violation. A wait genuinely blocked on the USER (awaiting
# their answer/drive) is exempt, as is any turn that did substantive work. Emitted as VERBOSEPAUSE:<n>
# (n = char count of the message). VERBOSEPAUSE takes precedence over IDLEHOLD (its "be terse"
# guidance is the more specific correction, and it must fire even when a justification paragraph is
# present -- that is exactly the long "justified" hold the new rule bans).
#
# The blocked-on-user phrasing (USER_WAIT_RE) is shared: scan.blocked_on_user.

# Long / multi-topic heuristic: a blocked-pause note should be one or two short sentences. Flag when the
# message is >450 chars, OR has >3 sentences, OR contains any heading/bullet/numbered line, OR spans
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

# A pure-pause turn (no substantive tool_use) that is NOT blocked on the user and whose message is long
# -> VERBOSEPAUSE. Measured on the raw last-turn text (not the quote-scrubbed copy) so length is not
# undercounted; user-block is checked on the scrubbed copy so a merely-quoted "wait for you" does not
# exempt.
# A PROSE-ONLY ANSWER IS NOT A PAUSE. VERBOSEPAUSE is about a turn that stops WITH SOMETHING
# PENDING: either live background work still running at turn-end, or a message that announces an
# idle/hold itself. A turn that simply answers the user's question in prose waits on nothing, and
# neither does the corrective rewrite the wall_of_text guard forces -- that one has no tool_use BY
# CONSTRUCTION, since its whole job is to restate an answer. Without this gate the rule degenerates
# into "no prose answer may exceed 450 chars", which is the wall_of_text guard's job and not this
# one's. Measured 2026-08-22: it halted three consecutive prose answers with no background task
# running at all, twice on rewrites the wall_of_text halt had just demanded.
# Three ways a turn can BE a pause. Events alone are not enough: a turn can narrate work in flight
# that the transcript cannot see (a subagent compiling, a gate running elsewhere), and that turn does
# owe a terse blocked-note. What none of these match is a plain ANSWER.
PENDING_WORK_RE = re.compile(
    r"\b(?:still (?:running|compiling|building|going|underway|in flight)"
    r"|while (?:it|that|they|those) (?:run|runs|compile|compiles|build|builds)"
    r"|once (?:it|that|they|the \w+) (?:lands|land|finishes|finish|completes|complete|returns|return|is done|are done)"
    r"|when (?:it|that|they) (?:lands|land|finishes|finish|completes|complete|returns|return)"
    r"|pick (?:this|it) back up"
    r"|in the meantime)\b",
    re.IGNORECASE,
)

verbose_n = None
turn_is_pause = (
    bool(phrase)
    or bool(scan.live_background_work(events))
    or bool(PENDING_WORK_RE.search(scrubbed))
)
if turn_is_pause and not turn_has_work and not scan.blocked_on_user(scrubbed):
    verbose_n = verbose_char_count(last_turn)

if verbose_n is not None:
    sys.stdout.write("VERBOSEPAUSE:" + str(verbose_n))
elif phrase and not turn_has_work and not JUSTIFY_RE.search(scrubbed):
    sys.stdout.write("IDLEHOLD:" + phrase)
PY
