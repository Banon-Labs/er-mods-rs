#!/usr/bin/env bash
# Cupcake signal: last_assistant_untailed_long_run
#
# Emits `UNTAILEDRUN:<command> => <tail command>; ...` when the turn being stopped started a long
# run in the background and opened no kitty tab tailing it. Empty otherwise (fail-open).
#
# User request 2026-10-19: "When we regenerate the board, is there a skill we can make and an agent
# instruction or rego guard to remind you to open a kitty tab tailing the real output from the run?"
# The instance: `python3 scripts/gen-r3-weapon-boards.py --jobs 3`, a run of half an hour or more,
# was backgrounded with no tab, although AGENTS.md ("A long background build is tailed in a kitty
# tab") and the tail-build-task skill both already said to open one.
#
# A long run is a Bash call of this turn whose result is a background acknowledgement
# (`cupcake_turn_scan.RUNNING_TASK_LAUNCH_RE`: run_in_background, a foreground call the harness
# moved to the background, or one the user backgrounded) and whose command matches LONG_RUNS below,
# the one list of what counts. It is tailed when a later Bash call in the same turn runs
# scripts/kitty-tail-task.py or scripts/kitty-tail-push.py -- and, for a tool in REAL_OUTPUT whose
# progress goes somewhere other than its own output (the board generator's shard logs), only when
# that call follows those files. A tail call that failed still counts
# when the closing message mentions the tab or kitty, so a refusal the agent reports is not
# punished twice. A run that already finished inside the turn needs no tab.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
CUPCAKE_SIGNAL_EVENT=""
if [ ! -t 0 ]; then
	CUPCAKE_SIGNAL_EVENT="$(timeout 2 cat 2>/dev/null || true)"
fi
export CUPCAKE_SIGNAL_EVENT
python3 - <<'PY' 2>/dev/null || true
import json, os, re, shlex, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_turn_scan as scan
except Exception:
    sys.exit(0)

# The long runs, as (name, pattern). The name is what `--match` gets in the suggested tail command.
# A script counts only where it is run (after python3/bash/sh, or as the command itself), so a
# backgrounded `grep gen-r3-weapon-boards.py` is not a board regeneration.
_RUN = r"(?:\b(?:python3?|bash|sh)\s+(?:-[A-Za-z]\S*\s+)*|(?:^|[;&|]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)(?:[^\s'\"(]*/)?"
QUICK_FLAGS = r"--(?:selftest|help|list-stages|dry-run)\b"
LONG_RUNS = [
    ("gen-r3-weapon-boards.py", re.compile(_RUN + r"gen-r3-weapon-boards\.py\b(?![^;&|]*--check\b)")),
    ("er-weapon-build.py", re.compile(_RUN + r"er-weapon-build\.py\b")),
    ("er-build-dlls.sh", re.compile(_RUN + r"er-build-dlls\.sh\b")),
    ("er-builds-pvp.py", re.compile(_RUN + r"er-builds-pvp\.py\b")),
    ("er-mechanics-build-pipeline.sh", re.compile(_RUN + r"er-mechanics-build-pipeline\.sh\b")),
    ("check.sh", re.compile(_RUN + r"(?<=/)check\.sh\b")),
    ("test-cupcake-stop-guards.py", re.compile(_RUN + r"test-cupcake-stop-guards\.py\b")),
    ("cargo xwin build", re.compile(r"\bcargo\s+xwin\s+build\b")),
    ("git push", re.compile(r"\bgit\s+(?:-C\s+\S+\s+)*push\b")),
]
TAIL_RE = re.compile(r"kitty-tail-(?:task|push)\.py\b")
# Where a tool's real progress goes, when it is not the task's own output. A tail call for that
# tool counts only if it follows these files; every other tool's tail call counts as it is.
# gen-r3-weapon-boards.py prints four setup lines and sends every progress line to one
# <cache>/shard-<n>.log per worker (user correction 2026-10-19: a tab on the wrapper's output
# alone shows nothing of the run).
REAL_OUTPUT = {
    "gen-r3-weapon-boards.py": re.compile(r"--boards\b|shard-[0-9]+\.log"),
}
OUTPUT_FILE_RE = re.compile(r"Output is being written to:\s*(\S+)")
# The file a command's own stdout redirect names, which is where its output is when it has one.
REDIRECT_RE = re.compile(r"(?:^|\s)(?:1|&)?>>?\s*(?!&)([^\s;&|]+)")
TAB_WORD_RE = re.compile(r"\b(?:tab|kitty)\b", re.IGNORECASE)


def long_run(cmd: str):
    """(LONG_RUNS name, where it starts in the command) for the long run the command runs, or
    None."""
    if not cmd or TAIL_RE.search(cmd):
        return None
    for name, pat in LONG_RUNS:
        m = pat.search(cmd)
        if m:
            if name != "git push" and re.search(QUICK_FLAGS, cmd):
                return None
            return name, m.start()
    return None


ASSIGN_RE = re.compile(r"(?:^|[\s;&|])(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|'\"$`]+)")
VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def redirected_output(cmd: str, start: int):
    """The file the long run at `start` sends both its streams to (`> f 2>&1`, `&> f`), read from
    its own pipeline up to the next `;`, `&&`, `||` or newline, with any `$VAR` the command assigns
    a literal to expanded; None when there is none or it cannot be spelled out. Only stdout
    redirected leaves the progress on stderr, in the task's own output file."""
    segment = re.split(r";|&&|\|\||\n", cmd[start:], maxsplit=1)[0]
    if not re.search(r"2>&1|&>", segment):
        return None
    found = [r.strip("'\"") for r in REDIRECT_RE.findall(segment)]
    found = [r for r in found if r and r != "/dev/null"]
    if not found:
        return None
    target = found[0]
    values = dict(ASSIGN_RE.findall(cmd))
    target = VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), target)
    if re.search(r"[$`'\"]", target):
        return None
    return target


path = scan.hook_transcript()
if not path:
    sys.exit(0)

events = scan.load_events(path)
start = 0
for i, ev in enumerate(events):
    if scan.is_real_user_prompt(ev):
        start = i + 1
turn_events = events[start:]

pending = {}   # tool_use_id -> (name, command)
launches = []  # dicts: name, command, task_id, output, tool_id, order
tails = []     # (order, ok)
finished = set()
order = 0
for ev in turn_events:
    for tool_id, task_id in scan.finished_notifications(ev):
        finished.add(tool_id)
        finished.add(task_id)
    msg = ev.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        continue
    for block in content:
        if not isinstance(block, dict):
            continue
        order += 1
        if block.get("type") == "tool_use" and block.get("name") == "Bash":
            inp = block.get("input") or {}
            cmd = inp.get("command") if isinstance(inp, dict) and isinstance(inp.get("command"), str) else ""
            pending[block.get("id") or ""] = cmd
        elif block.get("type") == "tool_result":
            uid = block.get("tool_use_id") or ""
            if uid not in pending:
                continue
            cmd = pending.pop(uid)
            text = scan._result_text(block)
            if TAIL_RE.search(cmd):
                tails.append((order, not block.get("is_error"), cmd))
                continue
            if block.get("is_error"):
                continue
            m = scan.RUNNING_TASK_LAUNCH_RE.search(text)
            if not m:
                continue
            run = long_run(cmd)
            if not run:
                continue
            name, at = run
            out = OUTPUT_FILE_RE.search(text)
            output = out.group(1).rstrip(".") if out else "<the task's output file>"
            redirect = redirected_output(cmd, at)
            if redirect:
                output = redirect
            launches.append({
                "name": name, "command": cmd, "task_id": m.group(1) or m.group(2) or "",
                "output": output, "tool_id": uid, "order": order,
            })

if not launches:
    sys.exit(0)

turns = scan.split_turns(events)
turn = turns[-1] if turns else None
closing = turn.text_runs[-1] if turn is not None and turn.text_runs else ""


def tailed(launch) -> bool:
    real = REAL_OUTPUT.get(launch["name"])
    for at, ok, cmd in tails:
        if at <= launch["order"]:
            continue
        if real is not None and not real.search(cmd):
            continue  # a tab on the wrapper's output alone, for a tool whose progress is elsewhere
        if ok or TAB_WORD_RE.search(closing):
            return True
    return False


def suggestion(launch) -> str:
    out = shlex.quote(launch["output"]) if not launch["output"].startswith("<") else launch["output"]
    if launch["name"] == "git push":
        return "python3 scripts/kitty-tail-push.py --log %s --title 'push'" % out
    if launch["name"] == "gen-r3-weapon-boards.py":
        return ("python3 scripts/kitty-tail-task.py --boards --title boards --file %s (it follows that "
                "file plus every <cache>/shard-<n>.log, where the progress goes, until the wrapper "
                "exits; by hand that is one --file per shard log and --pid of the wrapper)" % out)
    return ("python3 scripts/kitty-tail-task.py --title %s --file %s --match %s (--pid <pid> when "
            "several processes match)"
            % (shlex.quote(launch["name"]), out,
               shlex.quote("xwin" if launch["name"] == "cargo xwin build" else launch["name"])))


missing = [l for l in launches
           if not tailed(l) and l["tool_id"] not in finished and l["task_id"] not in finished]
if not missing:
    sys.exit(0)


def describe(l) -> str:
    cmd = " ".join(l["command"].split())
    if len(cmd) > 100:
        cmd = cmd[:97] + "..."
    return "%s => %s" % (cmd, suggestion(l))


sys.stdout.write("UNTAILEDRUN:" + "; ".join(describe(l) for l in missing)[:1500])
PY
