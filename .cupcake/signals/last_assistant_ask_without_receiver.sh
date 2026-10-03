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
# cupcake hands to every signal on stdin; latest_transcript is the fallback):
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
# or sudo, something to paste, what the user sees when no oracle covers it, or when they want
# something. Those need no instrument. A question with none of the ask phrases
# ("Does the font look right to you?") never matches in the first place.
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

path = None
try:
    event = json.loads(os.environ.get("CUPCAKE_SIGNAL_EVENT") or "{}")
    cand = event.get("transcript_path") if isinstance(event, dict) else None
    if isinstance(cand, str) and os.path.isfile(cand):
        path = cand
except ValueError:
    pass
path = path or scan.latest_transcript()
if not path:
    sys.exit(0)

events = scan.load_events(path)
turn = scan.last_text_turn(scan.split_turns(events))
if turn is None or not turn.text_runs:
    sys.exit(0)
closing = turn.text_runs[-1]

# --- the ask -----------------------------------------------------------------------------------

def scrub(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r'"[^"\n]*"', " ", text)
    text = re.sub(r"[“][^”\n]*[”]", " ", text)
    return text.replace("’", "'")

ASK_RE = re.compile(
    r"\bneed\s+you\s+to\b"
    r"|\bi(?:'d|\s+would|\s+still)?\s+need\s+(?!to\b|nothing\b|no\b|not\b)\w+"
    r"|\bwhat\s+i\s+need\b"
    r"|\bplease\s+\w+"
    r"|\b(?:let|tell|ping|show)\s+me\s+(?:know\s+)?(?:when|once|after|as\s+soon\s+as|whether\s+it|if\s+it)\b"
    r"|\bwaiting\s+(?:on|for)\s+you\b"
    r"|\b(?:once|when|after|as\s+soon\s+as)\s+you(?:'re|'ve|\s+have|\s+are)?\s+"
    r"(?!said\b|asked\b|wrote\b|mentioned\b|told\b|were\b|was\b|did\b|meant\b|described\b)\w+"
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

offending = None
for sentence in re.split(r"(?<=[.!?])\s+|\n+", scrub(closing)):
    s = sentence.strip()
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
