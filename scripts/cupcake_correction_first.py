#!/usr/bin/env python3
"""Classifier for the correction-first guard pair: a user's correction is encoded for future agents
before the agent acts on the instance it was about.

User directive 2026-11-11, verbatim: "My largest concern is that every time I correct you, you fix the
issue instead of fix it for future agents first".

The turn it was written from (session 5ec7b779, read from its transcript):
  1. The user typed a correction that opens "If I ever see a subagent that is finished but hasn't
     been closed" and says they worry the agent missed that subagent's changes until it is closed.
  2. The assistant ran ListAgents, two read-only Bash checks, a TaskStop on one agent and a
     SendMessage telling a running subagent to wrap up, then closed on prose. No guard, test or
     instruction file changed and no agent was launched to change one.
  3. Only on the next message ("I was not concerned about you closing that specific agent
     whatsoever") did it launch an agent to build the Stop guard.
The instance fix came first; the fix for future agents came a turn later, after a second correction.

Two halves live here so the UserPromptSubmit reminder and the Stop guard cannot disagree:

  `correction(prompt)`     the sentence of the user's prompt that reads as a correction of how the
                           agent works, or ''. Three families: a complaint about the agent's behavior
                           ("every time I correct you", "I'm worried that you", "You forgot to record
                           it?"), a standing rule about how to work ("Don't overfit or assume",
                           "Never ..."), and a challenge to a choice the agent made (reused from
                           `cupcake_challenged_convention.challenged_choice`). A task request ("run
                           r10"), a question about the game or the code, and a fact the user supplies
                           are not corrections. Measured on 100 labelled real prompts (and 13
                           borderline ones, reported unscored) by
                           scripts/test-cupcake-correction-classifier.py.

  `judge(prompt, span)`    for the transcript tail after the user's latest typed prompt: the facts the
                           Stop guard halts on, or None. The turn's tool calls are sorted into
                             enforcement -- an Edit/Write to .cupcake/**, AGENTS.md, docs/agents/**,
                                            scripts/cupcake_*.py, scripts/test-cupcake-*.py or
                                            scripts/test-*-signal.py, or an Agent launch whose prompt
                                            names one of those paths or says guard/policy/Stop hook;
                             instance    -- any other Edit/Write, a Bash that is not read-only,
                                            SendMessage, TaskStop, or any other Agent launch;
                             neutral     -- reads (Read, Grep, ListAgents, ToolSearch, read-only Bash).
                           It halts when an instance action came before the first enforcement action,
                           or when the turn ended with no enforcement action at all -- unless the
                           closing message says why enforcement is impossible.

A halt of this guard is not repeated once the turn has encoded the correction: when the span already
holds this guard's own Stop feedback and an enforcement action, the turn may end. The order it broke
cannot be undone, and a halt that only a second, made-up rule could clear would wedge the session.

The user's latest typed prompt is found with `cupcake_push_authorization.prompt_text`, the one
definition this repo has of "a message the user typed": task notifications, system reminders, hook
feedback and compaction summaries are not prompts, and a turn does not restart at them.

`--selftest` runs the unit cases below.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cupcake_challenged_convention as cc  # noqa: E402
import cupcake_turn_scan as scan  # noqa: E402

try:  # the one definition of a typed prompt; without it the Stop half says nothing
    from cupcake_push_authorization import prompt_text as typed_prompt_text  # noqa: E402
except Exception:  # pragma: no cover - fail open
    typed_prompt_text = None

DIRECTIVE = (
    "My largest concern is that every time I correct you, you fix the issue instead of fix it for "
    "future agents first"
)

# The halt reason opens with this, and the Stop feedback the harness records carries it back, which is
# how a later Stop in the same span knows it already halted (see `judge`).
HALT_MARKER = "Correction not encoded first"

# --- the correction classifier ---------------------------------------------------------------------

# Carriers the harness or a paste puts inside a prompt that the user did not type as prose.
_NOISE_RE = re.compile(
    r"<pasted_content[^>]*>.*?(?:</pasted_content>|\Z)"
    r"|<system-reminder>.*?(?:</system-reminder>|\Z)"
    r"|<task-notification>.*?(?:</task-notification>|\Z)"
    r"|\[Image #\d+\]|\[image attachment stripped[^\]]*\]",
    re.IGNORECASE | re.DOTALL,
)

# Text the Claude Code harness writes into a prompt. The UserPromptSubmit hook's `prompt` field carries
# it as if the user typed it; the Stop half never sees it, because `prompt_text` skips events the
# transcript records as `promptSource: system` / `origin.kind: auto-continuation`.
#   - Local slash-command records: the caveat, the command's name and its output (`/login` ->
#     "Login successful"). `<command-args>` is what the user typed after the command, so only its tags
#     go and its text is still classified.
#   - `!` shell-mode records: the command and its output are shell, not prose.
#   - Fixed harness sentences, each matched as the whole template, so a user who types one of their
#     clauses in their own message is still read. 2026-11-22, session 5ec7b779: the UserPromptSubmit
#     reminder quoted "do not repeat work that is already complete." from the usage-limit reset text
#     below (transcript event: promptSource system, isMeta, origin.kind auto-continuation).
_HARNESS_RE = re.compile(
    r"<(local-command-(?:caveat|stdout|stderr)|command-name|command-message|bash-(?:input|stdout|stderr))>"
    r".*?(?:</\1>|\Z)"
    r"|</?command-args>"
    r"|\bYour\s+(?:[\w.-]+\s+)?usage\s+limit\s+has\s+reset\.\s*Continue\s+the\s+task\s+you\s+were\s+working"
    r"\s+on\s+when\s+the\s+limit\s+was\s+reached;\s*do\s+not\s+repeat\s+work\s+that\s+is\s+already\s+complete\.?",
    re.IGNORECASE | re.DOTALL,
)

# A sentence that asks the agent to do something is a task, whatever else it contains: "Also can you
# make sure all Cheat engine instances automatically get muted on start in the future" is a request
# about the product, not a rule about how the agent works.
_REQUEST_RE = re.compile(
    r"^(?:(?:also|and|so|ok|okay|now|then|please)[,\s]+)*"
    r"(?:(?:can|could|would|will)\s+(?:you|we)\b|please\b|let'?s\b|lets\b)",
    re.IGNORECASE,
)

# A complaint about the agent's behavior: the subject is the agent, or the user's dealings with it.
# Each arm is a shape measured in this repo's transcripts; the comment names one real prompt per arm.
_COMPLAINT_RES = [
    # "every time I correct you, you fix the issue instead of ..."
    r"\bevery\s+time\s+i\s+(?:correct|tell|ask|remind|catch)\s+you\b",
    # "I don't know how many times I have to tell you"
    r"\bhow\s+many\s+times\s+(?:do\s+)?i\s+(?:have|need|got)\s+to\b",
    # "why do you do it, and I have to remind you not to?"
    r"\bi\s+(?:have|had|keep\s+having)\s+to\s+(?:remind|tell|ask)\s+you\b",
    # "I don't want to remind you again that ..."
    r"\bi\s+(?:don'?t|do\s+not)\s+want\s+to\s+(?:have\s+to\s+)?(?:remind|tell)\s+you\b",
    # "I just told you you're not blocked"
    r"\bi\s+(?:just\s+|already\s+)?told\s+you\b",
    # "I didn't ask to close", "I didn't say to stop", "I never asked you to"
    r"\bi\s+(?:didn'?t|did\s+not|never)\s+(?:ask(?:ed)?|sa(?:y|id)|t[eo]ld?)\s+(?:you\s+)?(?:to|for|that)\b",
    # "I don't think I ever asked you to"
    r"\bi\s+(?:don'?t\s+think\s+i\s+)?ever\s+asked\s+you\s+to\b",
    # "Didn't ask"
    r"^(?:i\s+)?didn'?t\s+ask\b|^(?:nobody|no\s+one)\s+asked\b",
    # "If I ever see a subagent that is finished but hasn't been closed"
    r"\bif\s+i\s+ever\s+see\b",
    # "VERY HIGH concerns that you consumed", "not concerned about you closing"
    r"\b(?:worried|concerned|concerns?|worry)\s+(?:about\s+|that\s+)?you\b",
    # "You cut out facts instead of identifying them", "instead of you asking the user". The "instead
    # of" must sit in the clause whose subject is "you": not inside an "if/whether/unless" clause (a
    # hypothetical, "if you consider X instead of Y"), and not across a "when/once" clause that brings
    # in another subject. 2026-11-19, session 5ec7b779, this arm halted a turn on "Do you have to
    # re-run to get the same information if we consider Faith instead of Int?", where the "instead
    # of" belongs to "if we consider". Replayed over the 933 distinct typed prompts of this repo's
    # transcripts, that is the only sentence the narrowing changes.
    r"(?<!\bif\s)(?<!\bwhether\s)(?<!\bunless\s)\byou\b(?!\s+(?:can|could|may|might)\b)"
    r"(?:(?!\b(?:if|whether|unless)\s|\b(?:when|whenever|once)\s+(?:we|i|it|they|he|she|this|that|the)\b)"
    r"[^.?!]){0,80}?\binstead\s+of\b|\binstead\s+of\s+you\b",
    # "You're not allowed to stop", "You're supposed to have objective metrics", "obligated to"
    r"\byou(?:'re|\s+are|\s+were)\s+(?:not\s+(?:allowed|supposed|permitted|blocked)|supposed\s+to|obligated)\b",
    # "You forgot to record it?"
    r"(?<!\bdo\s)(?<!\bdid\s)(?<!\bif\s)\byou\s+(?:forgot|ignored|skipped|keep|kept)\b",
    # "You haven't validated my claim"
    r"\byou\s+(?:still\s+)?(?:haven'?t|have\s+not)\s+(?:validated|verified|checked|proven|proved|read|tested|confirmed|measured)\b",
    # "even though you can't seem to remember"
    r"\byou\s+can'?t\s+seem\s+to\b|\byou\s+never\s+(?:remember|listen|read|check)\b",
    # "That's not the order", "This is not what I meant", "This was not a directive to self correct"
    r"\bthat(?:'s|\s+is)\s+not\s+(?:the\s+order|what\s+i\s+(?:meant|asked|said|wanted))\b"
    r"|\b(?:this|that|it)\s+(?:is|was)\s+not\s+what\s+i\s+(?:meant|asked|said|wanted)\b"
    r"|\bthis\s+was\s+not\s+a\s+directive\b",
    # "Most of my pre-releases are fine, quit inventing", "stop explaining". Only as an imperative: at
    # the head of the sentence or a clause. "when I stop walking" and "Does a tap stop happening" are
    # held-out prompts this arm once misread.
    r"(?:^|[,;:]\s*|\b(?:please|just|and|so)\s+)(?:quit|stop)\s+"
    r"(?!(?:the|it|this|that|them|him|her|my|our|working)\b)\w+ing\b",
    # "the magnitude of your incompetence"
    r"\byour\s+(?:\w+\s+)?(?:incompetence|stupidity|laziness)\b"
    r"|\b(?:dipshit|troglodyte|trogladyte|idiot|moron)\b|\byou\s+(?:stupid|dumb|fucking)\b",
    # "See a pattern here? You think I won't call you out for this each time?"
    r"\bsee\s+a\s+pattern\b|\bcall\s+you\s+out\b",
    # "I'm aware of all of this. It doesn't bear repeating"
    r"\b(?:does(?:n'?t|\s+not)\s+bear\s+repeating|stop\s+repeating|no\s+need\s+to\s+repeat)\b",
    # "Figure out why you thought I was blocked", "I just want to know why you wait for user input"
    r"\b(?:know|understand|wonder|figure\s+out|tell\s+me)\s+why\s+you\s+(?!think\b|believe\b|suppose\b)",
    # "I thought you knew, and I didn't need to tell you"
    r"\bi\s+thought\s+you\s+(?:knew|would|were\s+going\s+to|had)\b",
    # "as your instructions provide", "Whatever instruction told you to record in memories"
    r"\bas\s+your\s+(?:own\s+)?(?:instructions|rules|skills?)\b"
    r"|\byour\s+(?:own\s+)?(?:instructions|agents\.md|rules)\s+(?:say|said|provide|require|tell|should)\b"
    r"|\binstructions?\s+(?:that\s+)?(?:told|tells|tell)\s+you\b",
    # "You should try to omit table borders"
    r"\byou\s+should(?:n'?t|\s+not|\s+never|\s+always)\b|\byou\s+should\s+(?:try\s+to\s+)?(?:omit|avoid|stop)\b",
    # "Would a developer put out ... unmeasured items? Neither should we"
    r"\bneither\s+should\s+(?:we|you)\b",
    # "enumerate ... like you did"
    r"\b(?:like|as)\s+you\s+(?:did|always\s+do|keep\s+doing|usually\s+do)\b",
    # "You're telling me this because you expect me to be here"
    r"\byou(?:'re|\s+are)\s+(?:telling|asking)\s+me\b",
    # "Why can't we just check what happens with our current DLL"
    r"\bwhy\s+can'?t\s+(?:we|you)\s+just\b",
    # "Can't you run golden land by itself with better profiling to get a much quicker answer?", "Can't
    # we scan the steam API instead?": a better way of working, put as a question. At the head of the
    # sentence only; across the 933 distinct typed prompts of this repo's transcripts, those two and
    # the "Why can't we just" above are the only sentences that open this way.
    r"^(?:(?:also|and|so|but|ok|okay|well)[,\s]+)*can'?t\s+(?:you|we)\b",
    # "That's the only time you should mention anything about it"
    r"\b(?:the\s+)?only\s+time\s+(?:i\s+(?:would\s+)?want\s+)?you\s+(?:should|to|may|can)\b",
    # "if you don't emit any text except for the command"
    r"\bif\s+you\s+(?:don'?t|do\s+not|didn'?t)\s+(?:emit|write|add|print|narrate|explain|repeat|recap|pad)\b",
    # "You wrote a memory, which means I'm quite confident it won't stick"
    r"\byou\s+(?:wrote|recorded|saved|filed|stashed|created)\s+(?:a|another|the)\s+(?:memory|memories|stash|backup)\b",
    # "Are you even looking at all of the attack animations?"
    r"\b(?:are|did|do|have)\s+you\s+even\b",
    # "it should be: Launch steam if needed, but ..."
    r"^(?:it|that|this)\s+should\s+(?:be|have\s+been)\s*:",
]
_COMPLAINT_RE = re.compile("|".join(f"(?:{p})" for p in _COMPLAINT_RES), re.IGNORECASE)

# A standing marker turns "you" into a rule about how the agent works from now on: "this needs to be
# internalized for all future sessions", "something I want you to remember for another session",
# "whenever you need to push". It needs a second-person subject in the same sentence, and "whenever
# you're ready" is an invitation, not a rule.
_STANDING_RE = re.compile(
    r"\b(?:all\s+)?future\s+(?:sessions?|agents?)\b|\bfrom\s+now\s+on\b|\bgoing\s+forward\b"
    r"|\banother\s+session\b|\bnext\s+session\b"
    r"|\b(?:every\s+(?:single\s+)?time|each\s+time|any\s*time)\s+you\b"
    r"|\bwhenever\s+you\b(?!(?:'re|\s+are)\s+ready|\s+(?:can|want|like|get)\b)",
    re.IGNORECASE,
)
_YOU_RE = re.compile(r"\byou\b|\byour\b|\byou'?re\b", re.IGNORECASE)

# A standing rule stated as an imperative at the head of a sentence. "Never"/"always" are standing by
# themselves. "Don't" is not -- "OK, then don't restart" is an instruction for this run -- so it
# counts only with a verb that is always about how the agent thinks or talks ("Don't overfit or
# assume", "don't inferr shit"), or with a standing marker ("don't ... again").
_LEAD_IN = r"^(?:(?:also|and|so|but|ok|okay|please|then|just|uh|yeah|no|again|seriously|lol)[,.!\s]+)*"
_RULE_RE = re.compile(
    _LEAD_IN + r"(?:never(?!\s+(?:mind|heard|seen|knew|had|got|thought|been)\b)|always(?!\s+been\b))\s+\w+"
    r"|" + _LEAD_IN + r"(?:do\s+not|don'?t|dont)\s+(?:ever\s+)?"
    r"(?:infer\w*|inferr\w*|assume|guess|speculate|overfit|invent|fabricate|hallucinate|extrapolate"
    r"|make\s+(?:things|stuff|shit)\s+up|apologi[sz]e|hedge|narrate|recap|repeat|editoriali[sz]e"
    r"|pad|lecture|stall|idle|give\s+up|bail)\b"
    r"|" + _LEAD_IN + r"(?:do\s+not|don'?t|dont|never)\s+[^.?!]*\b(?:again|ever\s+again|next\s+time"
    r"|in\s+the\s+future|from\s+now\s+on)\b",
    re.IGNORECASE,
)


def clean_prompt(prompt: str) -> str:
    """The prose the user typed: pasted logs, images, reminders, harness-written text and quoted spans
    removed."""
    return cc.strip_quoted(_HARNESS_RE.sub(" ", _NOISE_RE.sub(" ", prompt or "")))


def correction(prompt: str) -> str:
    """The sentence of `prompt` that reads as a correction of how the agent works, or ''.

    The first match wins: a correction usually leads ("That's not the order. giving you the
    authorization message is ...") and a prompt that corrects and then asks for work should be quoted
    by its correction.
    """
    text = clean_prompt(prompt)
    for sentence in cc.sentences(text):
        if _REQUEST_RE.match(sentence):
            continue
        if _COMPLAINT_RE.search(sentence) or _RULE_RE.search(sentence):
            return cc.quote(sentence)
        if _STANDING_RE.search(sentence) and _YOU_RE.search(sentence):
            return cc.quote(sentence)
    challenge = cc.challenged_choice(text)
    return challenge


# --- the turn: enforcement before instance ---------------------------------------------------------

ENFORCEMENT_PATH_RE = re.compile(
    r"(?:^|/)\.cupcake/"
    r"|(?:^|/)AGENTS\.md$"
    r"|(?:^|/)docs/agents/"
    r"|(?:^|/)scripts/cupcake_[^/]*\.py$"
    r"|(?:^|/)scripts/test-cupcake-[^/]*\.py$"
    r"|(?:^|/)scripts/test-[^/]*-signal\.py$"
)

# An agent launch counts as enforcement when its brief names an enforcement path or the machinery. A
# guard counter, a guard boost and guard damage are Elden Ring mechanics, not this repo's guards.
ENFORCEMENT_BRIEF_RE = re.compile(
    r"\.cupcake\b|\bAGENTS\.md\b|\bdocs/agents/|\bscripts/cupcake_|\bscripts/test-cupcake-"
    r"|\btest-[\w-]+-signal\.py\b|\bstop[\s-]hook\b|\bcupcake\b|\brego\b|\bpolic(?:y|ies)\b"
    r"|\bguards?\b(?!\s*-?\s*(?:counter|boost|break|damage|negation|point|stat|stance|up\b|down\b))",
    re.IGNORECASE,
)

WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
AGENT_TOOLS = ("Agent", "Task", "Workflow")
STOP_TOOLS = ("TaskStop", "KillShell", "KillBash")

# --- read-only Bash --------------------------------------------------------------------------------
# The measured turn's two checks were a `for` loop over two checkouts running a `python3 -c` read,
# and `timeout 25 python3 scripts/test-...py | tail -1`; neither changed anything, so both are reads.
# Anything this reader cannot judge is an instance action: that is the side that halts, so the list
# only ever widens on a measured read.
#
# 2026-11-16, session 5ec7b779: the guard halted on
#   sed -n "$(python3 -c "...open('scripts/x.py').read()...")"),+40p" scripts/x.py | python3 -c "..."
# which only reads. The old reader blanked quoted spans with a regex, which paired the `"` that opens
# `"$(` with the `"` that opens the inner `-c "`, left the Python code outside any quote and split it
# on `(` and newlines into "commands" named `.read` and `print[...`. The reader below lexes the
# command the way bash does: quotes, `$(...)`, backticks, subshells and redirects nest, so the code
# of a `python3 -c` is one word wherever it sits, and each command inside `$(...)` is judged too.
# The old reader also counted `do`, `then` and `{` as read commands, so `for f in x; do cp -f ...`
# passed as a read; keywords now open the position of the command that follows them. Replayed over
# the 5,293 distinct Bash commands of the 25 newest transcripts, the two readers differ on 178:
# 113 reads the old one called acts (nested quotes, `<(...)`, `python3 -I -c`, `sleep`), and 65
# acts it called reads (`do cp -f`, `do python3 scripts/<not a test>.py`, kitty-tail-task.py with a
# quoted `--match '--selftest'`).

_READ_CMDS = set(scan.PEEK_CMDS) | {
    "cd", "pwd", "awk", "gawk", "sort", "uniq", "cut", "tr", "jq", "rg", "stat", "file", "du", "df",
    "diff", "cmp", "sha256sum", "sha1sum", "md5sum", "readlink", "realpath", "basename", "dirname",
    "which", "type", "test", "[", "[[", "true", "false", "printf", "date", "nl", "xxd", "od",
    "strings", "column", "fold", "rev", "tac", "comm", "join", "paste", "expand", "fd", "tree", "id",
    "whoami", "uname", "sed", "find", "seq", "read", "sleep",
    # Builtins that change only the shell's own variables.
    ":", "set", "export", "unset", "shift", "local",
}
# Shell words that open or close a compound command: the command proper follows them.
_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}", "done", "fi", "esac"}
_WRAPPERS = {"timeout", "nice", "env", "time", "command", "rtk", "stdbuf", "ionice"}
_GIT_READ = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "blame", "grep", "merge-base",
    "cat-file", "describe", "shortlog", "show-ref", "for-each-ref", "rev-list", "ls-tree", "name-rev",
    "check-ignore", "whatchanged",
}
_BD_READ = {"show", "list", "ready", "memories", "recall", "prime", "search", "stats", "blocked",
            "count", "query", "help", "--help"}
_CARGO_READ = {"test", "check", "metadata", "tree", "--version"}
_READ_FLAG_RE = re.compile(r"^--(?:selftest|status|dry-run|help|list|check-only)(?:=.*)?$")
_TEST_SCRIPT_RE = re.compile(r"^(?:.*/)?test-[\w-]+\.py$")


def _read_flagged(words: list[str]) -> bool:
    """A script run that only tests or reports: the script is a `test-*.py`, or the word right after
    it is a read flag. A flag further on may be the value of another option: `kitty-tail-task.py
    --title x --match '--selftest'` opens a tab."""
    for k, w in enumerate(words):
        if w.startswith("-"):
            continue
        return bool(_TEST_SCRIPT_RE.match(w) or (k + 1 < len(words) and _READ_FLAG_RE.match(words[k + 1])))
    return False

# Python that writes, deletes, runs a program or reaches past the process. Checked against the code
# of every `python3 -c` and, because a heredoc's body is Python the reader cannot place, against the
# whole command. A match is an act even when it sits in a search pattern: that is the safe side.
_PY_WRITE_RE = re.compile(
    r"\.write_(?:text|bytes)\(|(?<!stdout)(?<!stderr)\.write(?:lines)?\("
    r"|\bos\.(?:remove|unlink|rename|renames|replace|makedirs|mkdir|rmdir|removedirs|system|popen|kill"
    r"|killpg|open|truncate|chmod|chown|link|symlink|utime|exec\w*|spawn\w*|posix_spawn\w*|fork|mkfifo"
    r"|mknod)\b"
    r"|\b(?:shutil|subprocess|pty|sqlite3|ctypes)\."
    r"|\.(?:unlink|mkdir|touch|rmdir|rename|chmod|lchmod|symlink_to|hardlink_to|link_to|truncate|save"
    r"|savez\w*|savefig|tofile|to_csv|to_json|to_parquet|to_pickle|dump)\("
    # Path.replace(target) takes one argument; str.replace takes two.
    r"|\.replace\(\s*[^,()]*\)"
    r"|(?<![.\w])(?:exec|eval|__import__)\s*\("
    # A loaded script's entry point runs that script, writes and all.
    r"|\bmain\s*\("
    r"|\bimport\s+(?:shutil|subprocess|pty|sqlite3|ctypes)\b|\bfrom\s+(?:shutil|subprocess|pty|sqlite3|ctypes)\s+import\b"
    r"|\bfrom\s+os\s+import\b[^\n;]*\b(?:remove|unlink|rename|replace|makedirs|mkdir|rmdir|system|popen|kill"
    r"|truncate|chmod|symlink|link)\b"
)
_OPEN_RE = re.compile(r"\bopen\(")
# A string literal that is exactly a file mode which writes: 'w', 'a', 'x', 'r+', 'wb', 'at', ...
_WRITE_MODE_RE = re.compile(r"(?<![\w'\"])(['\"])[rbtU]*[wax+][rbtU+]*\1")


def _py_writes(code: str) -> bool:
    """True when Python source may write, delete or run something. `open(...)` counts when its
    argument list holds a write-mode literal anywhere, or cannot be matched to a closing paren."""
    if _PY_WRITE_RE.search(code):
        return True
    for m in _OPEN_RE.finditer(code):
        depth, j = 1, m.end()
        while j < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[j], 0)
            j += 1
        if depth or _WRITE_MODE_RE.search(code[m.end():j]):
            return True
    return False


_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\s*(?=\n|$)", re.DOTALL)
_SUBST = "\x00"  # stands in a word for the output of a `$(...)`, which is judged as its own command


class _Unjudged(Exception):
    """Shell syntax this reader does not follow; the command counts as an act."""


def _scratch(path: str) -> bool:
    return path.startswith(("/dev/", "/tmp/", "$TMPDIR", "${TMPDIR")) or "/scratchpad/" in path


def _dquote(s: str, i: int, cmds: list, redirs: list, depth: int) -> tuple[str, int]:
    """The text of a double-quoted span opening before `i`, and the index after its closing quote.
    Command substitutions inside it are lexed into `cmds`."""
    buf = []
    while i < len(s):
        c = s[i]
        if c == '"':
            return "".join(buf), i + 1
        if c == "\\" and i + 1 < len(s) and s[i + 1] in '$`"\\\n':
            if s[i + 1] != "\n":
                buf.append(s[i + 1])
            i += 2
            continue
        if c == "$" and s[i + 1:i + 3] == "((":
            i = _arith(s, i + 3, cmds, redirs, depth + 1)
            buf.append(_SUBST)
            continue
        if c == "$" and s[i + 1:i + 2] == "(":
            i = _lex(s, i + 2, ")", cmds, redirs, depth + 1)
            buf.append(_SUBST)
            continue
        if c == "`":
            i = _lex(s, i + 1, "`", cmds, redirs, depth + 1)
            buf.append(_SUBST)
            continue
        buf.append(c)
        i += 1
    raise _Unjudged("unterminated double quote")


def _arith(s: str, i: int, cmds: list, redirs: list, depth: int) -> int:
    """Skip a `$(( ... ))` opened before `i`, lexing the command substitutions inside it; return the
    index after the closing `))`."""
    level = 0
    while i < len(s):
        c = s[i]
        if c == "$" and s[i + 1:i + 2] == "(" and s[i + 2:i + 3] != "(":
            i = _lex(s, i + 2, ")", cmds, redirs, depth + 1)
            continue
        if c == "`":
            i = _lex(s, i + 1, "`", cmds, redirs, depth + 1)
            continue
        if c == "(":
            level += 1
        elif c == ")":
            if level == 0:
                if s[i + 1:i + 2] != ")":
                    raise _Unjudged("odd $(( ))")
                return i + 2
            level -= 1
        i += 1
    raise _Unjudged("unterminated $((")


def _lex(s: str, i: int, stop, cmds: list, redirs: list, depth: int = 0) -> int:
    """Lex `s` from `i` up to `stop` (None, ')' or '`'), appending each simple command's words to
    `cmds` and each file written by a redirect to `redirs`; return the index after `stop`."""
    if depth > 16:
        raise _Unjudged("nested too deep")
    words: list[str] = []
    word = None
    target = None  # what the next finished word is: None an argument, "out" a file written, "in" not judged

    def finish_word():
        nonlocal word, target
        if word is None:
            return
        if target == "out":
            redirs.append(word)
        elif target is None:
            words.append(word)
        word = None
        target = None

    def finish_cmd():
        finish_word()
        if target is not None:
            raise _Unjudged("redirect with no target")
        if words:
            cmds.append(list(words))
            words.clear()

    n = len(s)
    while i < n:
        c = s[i]
        if stop == "`" and c == "`":
            finish_cmd()
            return i + 1
        if c == ")":
            if stop != ")":
                raise _Unjudged("unbalanced )")
            finish_cmd()
            return i + 1
        if c == "\\":
            if s[i + 1:i + 2] == "\n":
                i += 2
                continue
            word = (word or "") + s[i + 1:i + 2]
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _Unjudged("unterminated single quote")
            word = (word or "") + s[i + 1:j]
            i = j + 1
            continue
        if c == '"':
            text, i = _dquote(s, i + 1, cmds, redirs, depth)
            word = (word or "") + text
            continue
        if c == "`":
            i = _lex(s, i + 1, "`", cmds, redirs, depth + 1)
            word = (word or "") + _SUBST
            continue
        if c == "$":
            nxt = s[i + 1:i + 2]
            if s[i + 1:i + 3] == "((":
                i = _arith(s, i + 3, cmds, redirs, depth + 1)
                word = (word or "") + _SUBST
                continue
            if nxt == "(":
                i = _lex(s, i + 2, ")", cmds, redirs, depth + 1)
                word = (word or "") + _SUBST
                continue
            if nxt == "{":
                j = s.find("}", i + 2)
                if j < 0 or re.search(r"[`\"'(]", s[i + 2:j]):
                    raise _Unjudged("parameter expansion holding a quote or a substitution")
                word = (word or "") + s[i:j + 1]
                i = j + 1
                continue
            if nxt == "'":
                m = re.compile(r"\$'((?:[^'\\]|\\.)*)'").match(s, i)
                if not m:
                    raise _Unjudged("unterminated $'")
                word = (word or "") + m.group(1)
                i = m.end()
                continue
            word = (word or "") + c
            i += 1
            continue
        if c == "#" and word is None:
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "<>" or (c == "&" and s[i + 1:i + 2] == ">"):
            if c == "&":
                i += 1
            elif word is not None and word.isdigit():
                word = None  # `2>`: the digits name a descriptor, not an argument
            finish_word()
            if s[i] == ">":
                i += 1
                if s[i:i + 1] in (">", "|"):
                    i += 1
                if s[i:i + 1] == "(":
                    i = _lex(s, i + 1, ")", cmds, redirs, depth + 1)
                    continue
                if s[i:i + 1] == "&":
                    m = re.compile(r"&(?:\d+-?|-)").match(s, i)
                    if m:
                        i = m.end()
                        continue
                    i += 1
                target = "out"
                continue
            i += 1  # past `<`
            if s[i:i + 1] == "(":
                i = _lex(s, i + 1, ")", cmds, redirs, depth + 1)
                continue
            if s[i:i + 1] == ">":
                i += 1
                target = "out"  # `<>` opens the file read-write
                continue
            if s[i:i + 1] == "&":
                m = re.compile(r"&(?:\d+-?|-)").match(s, i)
                if not m:
                    raise _Unjudged("odd <& redirect")
                i = m.end()
                continue
            if s[i:i + 1] == "<":
                i += 1
                if s[i:i + 1] in ("<", "-"):
                    i += 1
            target = "in"
            continue
        if c in " \t":
            finish_word()
            i += 1
            continue
        if c in "\n;|&":
            finish_cmd()
            i += 1
            while i < n and s[i] in "&|;":
                i += 1
            continue
        if c == "(":
            finish_word()
            if all(w in _KEYWORDS for w in words):
                words.clear()  # `do (cd x && ...)`: the keyword opens the subshell's position
            if words:
                raise _Unjudged("( after words")
            i = _lex(s, i + 1, ")", cmds, redirs, depth + 1)
            continue
        word = (word or "") + c
        i += 1
    if stop is not None:
        raise _Unjudged(f"unterminated {stop}")
    finish_cmd()
    return n


def _positionals(rest: list[str]) -> list[str]:
    return [t for t in rest if not t.startswith("-")]


# --- help reads ------------------------------------------------------------------------------------
# 2026-11-17, session 5ec7b779: the guard halted on
#   kitty @ get-text --help 2>&1 | grep -A4 -- '--extent' | head -8
# which reads a CLI's help text before using a flag, as the global instructions require. So a command
# that only asks a program for its help is a read: `man <page>`, `help <builtin>`, `<prog> [sub ...]
# --help`, `<prog> help [topic ...]`, `<prog> -h`. The commands piped after it are judged on their own.
# Kept narrow on the side that halts:
#   - `--help` must be the last word, and every word between the program and it a bare subcommand
#     name (`@`, `get-text`): `rm -rf x --help` and `deploy --env=prod --help` stay acts.
#   - `-h` counts only as the sole argument, and never for the power programs, where `shutdown -h`
#     schedules a halt.
#   - `help` counts only as the first argument: `git checkout help` checks out a branch named help.
#   - A program that runs the command named in its arguments (`xargs rm --help`, `ssh host x --help`,
#     `docker run img --help`) or runs a target (`make help`) is never a help read.
_HELP_SUBCMD_RE = re.compile(r"^[@A-Za-z][\w.:@-]*$")
_HELP_MAX_SUBCMDS = 3
_HELP_RUNNERS = {
    "xargs", "parallel", "ssh", "sudo", "doas", "su", "pkexec", "watch", "nohup", "setsid", "exec",
    "eval", "source", ".", "flock", "chroot", "unshare", "nsenter", "systemd-run", "docker", "podman",
    "kubectl", "make", "just", "ninja", "rake", "task", "npx", "uvx", "pipx",
}
_POWER_CMDS = {"shutdown", "halt", "poweroff", "reboot", "telinit", "init", "systemctl"}
_MAN_WRITE_OPTS = ("-P", "--pager", "-H", "--html", "-B", "--browser")


def _help_read(name: str, rest: list[str]) -> bool:
    """True when one simple command only asks a program for its help text."""
    if name == "man":
        return not any(t.startswith(_MAN_WRITE_OPTS) for t in rest)
    if name == "help":
        return True  # the bash builtin
    if name in _HELP_RUNNERS or not rest:
        return False
    if rest == ["-h"]:
        return name not in _POWER_CMDS
    if rest[0] == "help":
        return all(_HELP_SUBCMD_RE.match(t) for t in rest[1:])
    if rest[-1] == "--help":
        middle = rest[:-1]
        return len(middle) <= _HELP_MAX_SUBCMDS and all(_HELP_SUBCMD_RE.match(t) for t in middle)
    return False


_SED_WRITE_RE = re.compile(r"(?<![A-Za-z])[wWe](?:\s|$|;|\})")


def _sed_reads(rest: list[str]) -> bool:
    """`sed` with no in-place flag and no script that writes (`w`, `W`) or runs (`e`) anything."""
    scripts = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t.startswith("--in-place") or (re.match(r"^-[A-Za-z]+$", t) and "i" in t):
            return False
        if t in ("-f", "--file") or t.startswith("--file="):
            return False  # a script in a file is not seen here
        if t in ("-e", "--expression"):
            scripts.append(rest[i + 1] if i + 1 < len(rest) else "")
            i += 2
            continue
        if t.startswith("--expression="):
            scripts.append(t.split("=", 1)[1])
        i += 1
    if not scripts:
        pos = _positionals(rest)
        scripts = pos[:1]
    return not any(_SED_WRITE_RE.search(sc) for sc in scripts)


def _awk_reads(rest: list[str]) -> bool:
    """`awk` whose program prints into no file or pipe and calls no `system()`."""
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in ("-f", "--file") or t.startswith("-f"):
            return False
        if t in ("-v", "-F"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        # `print x > "f"`, `print | "cmd"`, `"cmd" | getline` and system() write or run; a `>` in
        # a condition (`$3 > 5`) does neither.
        return not re.search(r"system\s*\(|\bprintf?\b[^;{}\n]*[>|]|\|\s*getline|\bclose\s*\(|\bfflush\s*\(", t)
    return True


def _python_reads(rest: list[str], heredoc: bool) -> bool:
    i = 0
    while i < len(rest):
        if rest[i] in ("-X", "-W"):
            i += 2
        elif re.match(r"^-[IBuEsSOqb]+$", rest[i]):
            i += 1
        else:
            break
    rest = rest[i:]
    if rest[:1] == ["-c"]:
        return len(rest) > 1 and not _py_writes(rest[1])
    if rest[:1] == ["-"] or (not rest and heredoc):
        return True  # the heredoc's code was checked with the whole command
    if rest[:2] in (["-m", "py_compile"], ["-m", "json.tool"]):
        return True
    return _read_flagged(rest)


def _command_reads(words: list[str], heredoc: bool, depth: int) -> bool:
    """True when one simple command, its words already unquoted, only reads."""
    i = 0
    while True:
        while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
            i += 1
        if i < len(words) and words[i] in _KEYWORDS:
            i += 1
            continue
        # Wrappers run the command after them: `timeout 25 python3 ...`, `rtk grep ...`.
        if i < len(words) and words[i].split("/")[-1] in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.match(r"^\d+(?:\.\d+)?[smhd]?$", words[i])):
                i += 1
            continue
        break
    if i >= len(words):
        return True
    name = words[i].split("/")[-1]
    rest = words[i + 1:]
    if name in ("for", "select", "case", "in"):
        return True  # a loop header; its words' substitutions were lexed as their own commands
    if _help_read(name, rest):
        return True
    if name in ("sed",):
        return _sed_reads(rest)
    if name in ("awk", "gawk"):
        return _awk_reads(rest)
    if name == "find":
        return not any(t in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
                             "-fprintf", "-fls") for t in rest)
    if name == "sort":
        return not any(t == "-o" or t.startswith(("-o", "--output")) for t in rest)
    if name in ("uniq", "xxd"):
        return len(_positionals(rest)) < 2  # a second operand is the file written
    if name == "tree":
        return "-o" not in rest
    if name == "rg":
        return not any(t.startswith("--pre") for t in rest)
    if name == "fd":
        return not any(t in ("-x", "-X") or t.startswith("--exec") for t in rest)
    if name == "file":
        return "-C" not in rest
    if name == "date":
        return not any(t == "-s" or t.startswith("--set") for t in rest)
    if name == "kill":
        return rest[:1] == ["-0"]  # a liveness probe sends no signal
    if name in _READ_CMDS:
        return True
    if name == "git":
        if any(t.startswith("--output") for t in rest):
            return False
        j = 0
        while j < len(rest) and rest[j] in ("-C", "-c", "--git-dir", "--work-tree"):
            j += 2
        sub = rest[j] if j < len(rest) else ""
        if sub in _GIT_READ:
            return True
        if sub == "hash-object":
            return not any(t == "-w" or t.startswith("--stdin-paths") for t in rest[j + 1:])
        if sub == "worktree" and rest[j + 1:j + 2] == ["list"]:
            return True
        if sub == "branch":
            opts = rest[j + 1:]
            if any(t in ("-d", "-D", "-m", "-M", "-c", "-C", "-f", "-u", "--delete", "--move", "--copy",
                         "--force", "--set-upstream-to", "--unset-upstream", "--edit-description")
                   or t.startswith(("--set-upstream-to=", "-u=")) for t in opts):
                return False
            # With --list (or a filter that implies it) the operands are patterns, not new branches.
            listing = any(t in ("--list", "-l", "--show-current", "--contains", "--no-contains", "--merged",
                                "--no-merged", "--points-at") or t.startswith(("--contains=", "--merged=",
                                                                               "--points-at=", "--format="))
                          for t in opts)
            return listing or all(t.startswith("-") for t in opts)
        return sub in ("remote", "config", "stash") and rest[j + 1:j + 2] in (["-v"], ["--get"], ["list"])
    if name in ("python3", "python"):
        return _python_reads(rest, heredoc)
    if name == "bd":
        return bool(rest[:1]) and rest[0] in _BD_READ
    if name == "cargo":
        return bool(rest[:1]) and rest[0] in _CARGO_READ
    if name == "opa":
        return bool(rest[:1]) and rest[0] in ("test", "eval", "check", "inspect", "version")
    if name == "cupcake":
        return bool(rest[:1]) and rest[0] in ("eval", "verify", "inspect", "--version")
    if name in ("bash", "sh"):
        if rest[:1] == ["-c"] and len(rest) > 1:
            return _bash_reads(rest[1], depth + 1)
        return _read_flagged(rest)
    if name in ("mkdir", "touch"):
        return bool(rest) and all(t.startswith("-") or _scratch(t) for t in rest)
    return False


def _bash_reads(cmd: str, depth: int) -> bool:
    if depth > 4:
        return False
    if _py_writes(cmd):
        return False
    heredoc = "<<" in cmd
    body = _HEREDOC_RE.sub(lambda m: "<<" + m.group(1) + m.group(2), cmd)
    cmds: list[list[str]] = []
    redirs: list[str] = []
    try:
        _lex(body, 0, None, cmds, redirs)
    except (_Unjudged, IndexError):
        return False
    # A read that redirects into a file wrote that file: `echo x > scripts/y.py`.
    if any(not _scratch(r) for r in redirs):
        return False
    return all(_command_reads(words, heredoc, depth) for words in cmds)


def bash_is_read_only(cmd) -> bool:
    """True when the Bash command only reads: every command in it, including those inside `$(...)`,
    backticks and subshells, is a known read, and nothing is redirected into a file outside the
    scratch area. Syntax the reader cannot follow makes it an act."""
    if not isinstance(cmd, str) or not cmd.strip():
        return True
    return _bash_reads(cmd, 0)


def _tool_input(block: dict) -> dict:
    inp = block.get("input") if isinstance(block, dict) else None
    return inp if isinstance(inp, dict) else {}


def classify(block: dict) -> str:
    """'enforcement', 'instance' or 'neutral' for one tool_use block."""
    name = str(block.get("name") or "")
    inp = _tool_input(block)
    if name in WRITE_TOOLS:
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        return "enforcement" if ENFORCEMENT_PATH_RE.search(path) else "instance"
    if name in AGENT_TOOLS:
        brief = " ".join(str(inp.get(k) or "") for k in ("description", "prompt", "script", "name"))
        return "enforcement" if ENFORCEMENT_BRIEF_RE.search(brief) else "instance"
    if name == "Bash":
        return "neutral" if bash_is_read_only(inp.get("command")) else "instance"
    if name == "SendMessage" or name in STOP_TOOLS:
        return "instance"
    return "neutral"


def describe(block: dict) -> str:
    """A short label for a tool call, safe in a pipe-delimited facts line."""
    name = str(block.get("name") or "?")
    inp = _tool_input(block)
    detail = (inp.get("file_path") or inp.get("command") or inp.get("description") or inp.get("task_id")
              or inp.get("to") or inp.get("summary") or "")
    detail = " ".join(str(detail).split())[:90].replace("|", "/")
    return f"{name} {detail}".strip()


# A concrete reason enforcement is impossible: an impossibility phrase and a reason in one sentence.
IMPOSSIBLE_RE = re.compile(
    r"\b(?:cannot|can'?t|could\s+not|couldn'?t)\s+be\s+(?:enforced|encoded|guarded|detected|checked)\b"
    r"|\b(?:impossible|no\s+way)\s+to\s+(?:enforce|encode|guard|detect|check)\b"
    r"|\benforcement\s+is\s+(?:impossible|not\s+possible)\b|\bnot\s+enforceable\b"
    r"|\b(?:no|a)\s+(?:guard|hook|policy|signal|test)\s+(?:can|could|cannot|can'?t)\s+(?:not\s+)?"
    r"(?:see|detect|observe|tell|read|check|know)\b"
    r"|\bnothing\s+(?:for\s+a\s+guard\s+)?to\s+(?:encode|enforce)\b",
    re.IGNORECASE,
)
REASON_RE = re.compile(r"\bbecause\b|\bsince\b|:|--|—|\bas\s+(?:it|the|there|that|this)\b", re.IGNORECASE)


def impossibility(closing: str) -> str:
    for sentence in cc.sentences(cc.strip_quoted(closing)):
        if IMPOSSIBLE_RE.search(sentence) and REASON_RE.search(sentence):
            return cc.quote(sentence)
    return ""


def _halt_feedback(ev: dict) -> bool:
    """This guard's own Stop feedback, in either shape the transcript records it."""
    att = ev.get("attachment")
    if isinstance(att, dict) and att.get("type") == "hook_blocking_error":
        return HALT_MARKER in json.dumps(att.get("blockingError") or "")
    if ev.get("type") == "user":
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, list):
            content = "\n".join(b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")
        text = content if isinstance(content, str) else ""
        return text.lstrip().startswith("Stop hook feedback") and HALT_MARKER in text
    return False


def latest_typed_prompt(lines: list[str]) -> tuple[int, str] | None:
    """(line index, text) of the last prompt the user typed, scanning back from the end. Only lines
    that can hold one are parsed, so a 40-megabyte transcript costs one pass of substring checks."""
    if typed_prompt_text is None:
        return None
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if '"promptSource"' not in line and '"queued_command"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        text = typed_prompt_text(ev)
        if text is not None:
            return i, text
    return None


def judge(prompt: str, span: list[dict]) -> dict | None:
    """The halt facts for the turn that `prompt` opened, whose events are `span`, or None."""
    said = correction(prompt)
    if not said:
        return None
    first_enforcement = first_instance = None
    halted_at = None
    closing = ""
    order = 0
    for ev in span:
        if _halt_feedback(ev):
            halted_at = order
            order += 1
            continue
        if ev.get("type") != "assistant" or ev.get("isSidechain"):
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                closing = block["text"]
                continue
            if block.get("type") != "tool_use":
                continue
            closing = ""  # prose before a tool call is not the closing message
            kind = classify(block)
            if kind == "enforcement" and first_enforcement is None:
                first_enforcement = order
            elif kind == "instance" and first_instance is None:
                first_instance = (order, describe(block))
            order += 1
    if first_enforcement is not None:
        if first_instance is None or first_enforcement < first_instance[0]:
            return None
        if halted_at is not None:
            return None  # already told; the encoding exists, and the order cannot be undone
    elif impossibility(closing):
        return None
    if first_instance is not None:
        verdict = "instance_first"
        first = first_instance[1]
    else:
        verdict = "unencoded"
        first = ""
    return {
        "correction": said,
        "verdict": verdict,
        "first_instance": first,
        "encoded_later": 1 if first_enforcement is not None else 0,
    }


def facts_line(facts: dict) -> str:
    return "CORRECTIONFACTS|correction={}|verdict={}|first_instance={}|encoded_later={}".format(
        facts["correction"].replace("|", "/"),
        facts["verdict"],
        facts["first_instance"].replace("|", "/"),
        facts["encoded_later"],
    )


def judge_transcript(path: str) -> str:
    """The Stop signal's whole job: the facts line for the transcript at `path`, or ''."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return ""
    found = latest_typed_prompt(lines)
    if found is None:
        return ""
    index, prompt = found
    if not correction(prompt):
        return ""
    span: list[dict] = []
    for line in lines[index + 1:]:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            span.append(ev)
    facts = judge(prompt, span)
    return facts_line(facts) if facts else ""


# --- selftest --------------------------------------------------------------------------------------

# The read this guard halted on, 2026-11-16 in session 5ec7b779, verbatim from the transcript.
SAVE_CACHE_READ = (
    "cd /home/banon/projects/er-mods-rs/.worktrees/r21 && sed -n \"$(python3 -c \"\n"
    "t=open('scripts/er-mechanics-attack-similarity.py').read().splitlines()\n"
    "print([i+1 for i,l in enumerate(t) if l.startswith('def save_cache(')][0])\"),+40p\" "
    "scripts/er-mechanics-attack-similarity.py | python3 -c \"import sys;[print(l.rstrip()[:150]) "
    "for l in sys.stdin if 'b{' in l or 'builds' in l or 'def ' in l]\""
)

# 2026-11-17, session 5ec7b779: the help read this guard halted on, verbatim from its halt text.
HELP_READ = "kitty @ get-text --help 2>&1 | grep -A4 -- '--extent' | head -8"

# 2026-11-22, session 5ec7b779: the harness's usage-limit reset text, verbatim from the transcript, and
# the shape the UserPromptSubmit hook received it in, with a local `/login` record and a reminder.
USAGE_RESET = ("Your claude.ai usage limit has reset. Continue the task you were working on when the limit "
               "was reached; do not repeat work that is already complete.")
USAGE_RESET_PROMPT = (
    "<local-command-caveat>Caveat: The messages below were generated by the user while running local "
    "commands. DO NOT respond to these messages or otherwise consider them in your response unless the "
    "user explicitly asks you to.</local-command-caveat>\n"
    "<command-name>/login</command-name>\n            <command-message>login</command-message>\n"
    "            <command-args></command-args>\n"
    "<local-command-stdout>Login successful</local-command-stdout>\n"
    "<system-reminder>\n<task-notification>\n<task-id>bx1</task-id>\n<status>completed</status>\n"
    "</task-notification>\n</system-reminder>\n" + USAGE_RESET
)


def selftest() -> int:
    def tool(name, **inp):
        return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": name, "input": inp}]}}

    def say(text):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

    corr = ("If I ever see a subagent that is finished but hasn't been closed, I always have VERY HIGH "
            "concerns that you consumed its results.")
    bad = 0
    prompts = [
        (corr, True),
        ("My largest concern is that every time I correct you, you fix the issue instead of fix it for "
         "future agents first", True),
        ("I was not concerned about you closing that specific agent whatsoever", True),
        ("Why did you stash them?", True),
        ("What networked events could the cheater have used? Don't overfit or assume", True),
        ("run r10", False),
        ("what are the strictly worse ashes?", False),
        ("OK, then don't restart", False),
        ("Lets not do heavy, but instead Magic infusion", False),
        ("Also can you make sure all Cheat engine instances get muted on start in the future", False),
        ("I wouldn't build a guard against the light overflow given I don't think that has value yet", False),
        ('<pasted_content id="1">you keep failing</pasted_content> what does this log mean?', False),
        # 2026-11-19, session 5ec7b779: halted as a correction by the "instead of" arm.
        ("Do you have to re-run to get the same information if we consider Faith instead of Int?", False),
        ("Do you have to re-run if you consider Faith instead of Int?", False),
        ("Can't you run golden land by itself with better profiling to get a much quicker answer?", True),
        ("Why did you pause to half answer my question instead of solving it.", True),
        # 2026-11-22, session 5ec7b779: harness text the UserPromptSubmit reminder read as a correction.
        (USAGE_RESET, False),
        (USAGE_RESET_PROMPT, False),
        # The same words typed by the user are still read, and so are a slash command's own args.
        ("Do not repeat work that is already complete, you redid the r10 run.", True),
        ("stop redoing finished work, you repeated the run", True),
        (USAGE_RESET_PROMPT + "\nstop redoing finished work, you repeated the run", True),
        ("<command-name>/loop</command-name>\n<command-message>loop</command-message>\n"
         "<command-args>stop narrating your next step</command-args>", True),
    ]
    for text, want in prompts:
        got = bool(correction(text))
        if got != want:
            bad += 1
            print(f"FAIL correction want={want} got={got}: {text[:80]!r}")
    reads = [
        "cd /x && for d in . y; do echo \"== $d\"; python3 -c \"print(open(d+'/a').read())\" \"$d\"; done",
        "cd /x && timeout 25 python3 scripts/test-wall-of-text-classifier.py 2>&1 | tail -1",
        "sed -n 1,60p .cupcake/policies/claude/x.rego",
        "git -C /x status --short && git diff --stat | tail -1",
        "python3 - <<'PY'\nif a > 3:\n    print(a)\nPY",
        "opa test .cupcake/ 2>&1 | tail -3",
        "mkdir -p /tmp/claude-1000/x/scratchpad/c && python3 -c 'print(1)' > /tmp/claude-1000/x/scratchpad/c/o.txt",
        "$HOME/.local/bin/bd show er-effects-rs-me5p",
        # 2026-11-16, session 5ec7b779: the read this guard halted on, with the newlines the
        # transcript recorded inside the inner `python3 -c "..."`.
        SAVE_CACHE_READ,
        SAVE_CACHE_READ.replace("\n", " "),
        "cat scripts/x.py | head -40",
        "tail -n 20 /home/banon/x.log; ls -la scripts/",
        "rg -n 'def save_cache' scripts/ | head",
        "sed -n '/def save_cache(/,/^def /p' scripts/x.py",
        "python3 - <<'PY'\nimport json\nprint(json.load(open('a.json'))['k'])\nPY",
        "python3 -c \"print(open('a.bin', 'rb').read()[:4])\"",
        "x=$(git rev-parse HEAD) && git show --stat \"$x\" | head -5",
        "for f in a b; do wc -l \"$f\"; done 2>/dev/null",
        "bash -c 'cat a | head -3'",
        "(time python3 scripts/x.py --selftest --keep-going) > /tmp/claude-1000/x/scratchpad/s.log 2>&1",
        "echo \"elapsed $(( $(date +%s) - $(cat /tmp/x/start) ))s\"",
        "for d in a b; do (cd $d && timeout 25 python3 scripts/test-x-signal.py | tail -1); done",
        "git branch -a --list '*voice*'",
        "while kill -0 123 2>/dev/null; do sleep 20; done; tail -3 /tmp/x.log",
        # 2026-11-17, session 5ec7b779: a CLI's help read before using one of its flags.
        HELP_READ,
        "man kitty | sed -n 1,40p",
        "man 1 git-commit | grep -n -- '--fixup' | head",
        "git help commit | head -20",
        "cargo build --help | grep -- --release",
        "rg -h | less",
        "help set | head",
    ]
    for cmd in reads:
        if not bash_is_read_only(cmd):
            bad += 1
            print(f"FAIL read-only: {cmd[:80]!r}")
    writes = [
        "python3 scripts/er-run-branch.py --with er-net-effects",
        "git commit -m 'x'",
        "python3 -c \"open('a','w').write('x')\"",
        "python3 -c \"p='a'; f=open(p,'w'); print('x', file=f)\"",
        "python3 -c \"open(os.path.join(d, 'b'), mode='a')\"",
        "python3 -c \"from pathlib import Path; Path('a').write_text('x')\"",
        "python3 -c \"import shutil; shutil.rmtree('a')\"",
        "python3 -c \"from pathlib import Path; Path('a').replace('b')\"",
        "python3 - <<'PY'\nimport os\nos.remove('a')\nPY",
        "echo hi > scripts/x.py",
        "echo hi >> AGENTS.md",
        "cat a 2>/dev/null >scripts/x.py",
        "sed -i 's/a/b/' x",
        "sed --in-place 's/a/b/' x",
        "sed -ni 's/a/b/p' x",
        "sed -n 's/a/b/w out.txt' x",
        "sed -n \"$(python3 -c \"open('a','w')\"),+40p\" x",
        "cat \"$(rm -rf x)\"",
        "cat `rm -rf x`",
        "for f in a; do rm -f \"$f\"; done",
        "(cd x && rm -f y)",
        "bash -c 'rm -f a'",
        "sort -o out.txt in.txt",
        "awk '{print > \"o\"}' a",
        "find . -name x -delete",
        "pkill -f eldenring",
        "cargo build -p er-net-effects",
        "cat \"unterminated",
        "python3 scripts/kitty-tail-task.py --title t --file /tmp/x.log --match x.py --match '--selftest'",
        "python3 -c \"import importlib.util as u; s=u.spec_from_file_location('m','scripts/m.py'); "
        "m=u.module_from_spec(s); s.loader.exec_module(m); m.main(['--json','out.json'])\"",
        "for f in a b; do cp -f \"$f\" dst/; done",
        "git branch -D old-branch",
        "kill 1234",
        # A help read keeps its edges: what follows it, where it writes, and what is not help.
        "kitty @ get-text --help > scripts/x.txt",
        "kitty @ get-text --help | xargs rm -f",
        "xargs rm --help",
        "ssh host deploy --help",
        "docker run img --help",
        "make help",
        "shutdown -h",
        "rm -rf x --help",
        "git checkout help",
        "deploy --env=prod --help",
        "man -P 'rm -rf x' kitty",
    ]
    for cmd in writes:
        if bash_is_read_only(cmd):
            bad += 1
            print(f"FAIL state-changing: {cmd[:80]!r}")
    turns = [
        ("instance first", [tool("TaskStop", task_id="a1"), say("Closed.")], "instance_first"),
        ("enforcement first", [tool("Edit", file_path="/r/.cupcake/policies/claude/x.rego"),
                               tool("TaskStop", task_id="a1"), say("Done.")], None),
        ("guard agent first", [tool("ListAgents"), tool("Agent", prompt="Add a Cupcake Stop guard that ..."),
                               say("Launched.")], None),
        ("guard-counter agent is not enforcement",
         [tool("Agent", prompt="Rank guard counter damage for every weapon"), say("Launched.")], "instance_first"),
        ("nested read, then AGENTS.md, then the instance",
         [tool("Bash", command=SAVE_CACHE_READ), tool("Edit", file_path="/r/AGENTS.md"),
          tool("TaskStop", task_id="a1"), say("Encoded.")], None),
        ("help read, then AGENTS.md, then the instance",
         [tool("Bash", command=HELP_READ), tool("Edit", file_path="/r/AGENTS.md"),
          tool("TaskStop", task_id="a1"), say("Encoded.")], None),
        ("a real mutation after a help read and before AGENTS.md is still an act",
         [tool("Bash", command=HELP_READ), tool("Bash", command="kitty @ close-window --match id:3"),
          tool("Edit", file_path="/r/AGENTS.md"), say("Encoded.")], "instance_first"),
        ("sed -i before AGENTS.md is still an act",
         [tool("Bash", command="sed -i 's/a/b/' scripts/x.py"), tool("Edit", file_path="/r/AGENTS.md"),
          say("Encoded.")], "instance_first"),
        ("prose only", [say("They are closed now.")], "unencoded"),
        ("impossibility argued",
         [say("No guard can see this because it happens in the game's own menu, outside any transcript.")], None),
        ("after own halt with encoding",
         [tool("TaskStop", task_id="a1"), say("Closed."),
          {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookEvent": "Stop",
                                                 "blockingError": {"blockingError": HALT_MARKER + ": ..."}}},
          tool("Write", file_path="/r/AGENTS.md"), say("Encoded.")], None),
        ("after own halt without encoding",
         [tool("TaskStop", task_id="a1"), say("Closed."),
          {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookEvent": "Stop",
                                                 "blockingError": {"blockingError": HALT_MARKER + ": ..."}}},
          say("Still closed.")], "instance_first"),
    ]
    for name, span, want in turns:
        facts = judge(corr, span)
        got = facts["verdict"] if facts else None
        if got != want:
            bad += 1
            print(f"FAIL turn {name}: want {want} got {got}")
    if judge("run r10", [tool("Edit", file_path="/r/src/x.rs"), say("Done.")]) is not None:
        bad += 1
        print("FAIL a task prompt must never be judged")
    total = len(prompts) + len(reads) + len(writes) + len(turns) + 1
    print(f"cupcake_correction_first selftest {'ok' if not bad else 'FAILED'} ({total - bad}/{total})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else 0)
