#!/usr/bin/env python3
"""Detect a closing message that says GitHub CI is still running while nothing in the session follows it.

The instance, 2026-11-25, verbatim from the closing message after a branch was pushed:

    CI on GitHub is still running; `compiles` and `cargo-test` hadn't finished at last check.

That state came only from the UserPromptSubmit `CI STATE (measured, not remembered)` line, read once.
The session had started no background task watching that pull request's checks and had opened no kitty split
tailing one, so it reported a running job it was not following. The user's words: "\" CI on GitHub
is still running\" I don't believe you because your rego policy that told you to open a kitty
tab/split to tail the output didn't run". The background-task guards (`no_untailed_long_run`,
`no_unexplained_running_tasks`, the global `background_task_needs_tail`) had nothing to fire on,
because remote CI is not a local task.

So a claim that CI is running is backed only by a live watcher of that pull request's checks: a
background Bash or Monitor task, still running at turn end as `cupcake_turn_scan.running_background_tasks`
reads it, whose command is `gh pr checks [<pr>] --watch`, `gh run watch [<run-id>]`, or an
`until`/`while` loop that polls `gh pr checks` / `gh run view`. The flags were checked against
`gh pr checks --help` (`--watch[=true|false]`, `--interval`, `--fail-fast`) and `gh run watch --help`
(`<run-id>`, `--exit-status`, `--compact`, `--interval`) on gh 2.x, 2026-11-25.

What counts as the claim (`ci_running_claims`): a clause of the closing message that says CI is
running, pending, queued, in progress, waited on, or not finished -- with a CI subject (CI, GitHub
Actions, checks, a workflow or check run) in the clause, or a job named in backticks / the word job /
a PR number in a clause whose sentence names CI. A report that CI finished, passing or failing, has
no such predicate. Quoted text (fenced code, `> ` block quotes, double-quoted spans, backtick spans)
is folded away before matching, so quoting the injection or the user is not a claim. A negated
predicate ("nothing is still running", "none pending") and "pending review" are not claims. A clause
about a local task (a push, a build, a subagent, a background task id) is not a CI claim.

Kept as an importable module so the classifier has a `--selftest` without a live transcript; the
signal `.cupcake/signals/last_assistant_ci_running_claim.sh` does the transcript walk through
`cupcake_turn_scan.py`, shared with every neighbouring Stop signal.
"""
from __future__ import annotations

import re
import shlex
import sys

# --- quoting -------------------------------------------------------------------------------------

CODE_TOKEN = ""   # a backtick span: a job name, a command, a quoted line
QUOTE_TOKEN = ""  # a double-quoted span: someone else's words


def fold_quotes(text: str) -> str:
    """The closing text with every quoted span folded to a placeholder, so only the agent's own
    words are matched. A fence, closed or left open, and a `> ` block-quote line vanish entirely;
    a backtick span keeps its place as CODE_TOKEN because "`cargo-test` hasn't finished" names a
    job; a double-quoted span (straight or curly) becomes QUOTE_TOKEN."""
    text = re.sub(r"```.*?(?:```|\Z)", " ", text or "", flags=re.DOTALL)
    text = "\n".join(line for line in text.splitlines() if not re.match(r"\s*>", line))
    text = re.sub(r"`[^`\n]*`", CODE_TOKEN, text)
    text = re.sub(r"\"[^\"\n]{0,400}\"|“[^”\n]{0,400}”", QUOTE_TOKEN, text)
    return text


def sentences(text: str) -> list[str]:
    """Sentences and lines (a table row or list item is its own unit)."""
    out = []
    for line in text.splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line):
            s = s.strip(" \t|*-")
            if s:
                out.append(s)
    return out


def clauses(sentence: str) -> list[str]:
    return [c for c in re.split(r";|\s+--\s+|\s+—\s+", sentence) if c.strip()]


# --- the claim -----------------------------------------------------------------------------------

# A subject that is CI by itself. "workflow" alone is not one: in this repo it is as often the local
# Workflow tool ("The similarity workflow wm52msbvd is still running."), and "checks" alone is as
# often a local suite ("Two checks are still running:"); both measured on a replay of the last 1600
# closing messages in this repo's transcripts, 2026-11-25.
CI_SUBJECT_RE = re.compile(
    r"\bCI\b|\bGitHub\s+(?:Actions|CI|checks?|workflows?|runners?)\b|\bActions\s+runs?\b"
    r"|\bgh\s+pr\s+checks\b",
    re.IGNORECASE,
)
# A subject that is CI only when its sentence names CI or a PR: checks, a check or workflow run, a
# job or stage, a job named in backticks.
SOFT_SUBJECT_RE = re.compile(
    r"(?<!local\s)(?<!pre-push\s)\bchecks\b|\bcheck\s+runs?\b|\bworkflow\s+runs?\b|\bjobs?\b"
    r"|\bstages?\b|" + CODE_TOKEN,
    re.IGNORECASE,
)
PR_REF_RE = re.compile(r"(?:\bPR\s*#?|\bpull\s+request\s+#?|(?<![\w/])#)(\d{1,6})\b", re.IGNORECASE)
# Words that make a clause about a local task, not about CI, unless the clause says CI outright.
LOCAL_RE = re.compile(
    r"\b(?:push|pushes|pushing|build|builds|building|subagent|agent|task|monitor|local|pre-push"
    r"|check\.sh|selftest|game|launch|workflow|shards?|regeneration)\b|\bb[a-z0-9]{7,9}\b",
    re.IGNORECASE,
)
# Local even beside the word CI: "The CI speed-up workflow `wf1` is still running." is a local
# Workflow tool run named after CI (same replay).
LOCAL_STRICT_RE = re.compile(
    r"\blocal\b|\bpre-push\b|\bcheck\.sh\b|(?<!GitHub\s)\bworkflow\b|\btask\b|\b(?:sub)?agent\b",
    re.IGNORECASE,
)

RUNNING_RE = re.compile(
    r"\bstill\s+(?:running|going|pending|queued|in[\s_-]progress|in\s+flight|executing|building)\b"
    r"|\b(?:is|are|was|were|remains?|stays?|sits?)\s+(?:currently\s+|now\s+)?"
    r"(?:running|pending|queued|in[\s_-]progress|in\s+flight|executing)\b"
    r"|\bin[_-]progress\b|\bpending\b|\bqueued\b|\bunfinished\b|\bincomplete\b"
    r"|\b(?:has|have|had|did|does|do)(?:n't|\s+not)\s+(?:yet\s+)?(?:finished|completed|reported|"
    r"started|ended|concluded)\b"
    r"|\bnot\s+(?:yet\s+)?(?:finished|done|complete|completed)\b"
    # Waiting counts only on CI's own things: "two dangling comments wait on the waivers" is not CI.
    r"|\bwait(?:ing|s)?\s+(?:on|for)\s+(?:the\s+|its\s+|a\s+)?(?:CI\b|GitHub\b|checks?\b|runners?\b|"
    r"jobs?\b|PR\b|#\d|results\b|" + CODE_TOKEN + r")",
    re.IGNORECASE,
)
# "pending review", "pending your merge": a PR's state, not CI's.
NOT_CI_PENDING_RE = re.compile(
    r"\bpending\s+(?:a\s+|the\s+|your\s+|user\s+|human\s+)?(?:review|approval|merge|undraft|decision)",
    re.IGNORECASE,
)
# "waiting on your `authorize commit`": the user, not CI.
NOT_CI_WAIT_RE = re.compile(r"wait(?:ing)?\s+(?:on|for)\s+(?:you\b|your\b|the\s+user|a\s+reply)", re.IGNORECASE)
# A negation just before the predicate: "nothing is still running", "none pending", "no job is
# queued". The has-not forms carry their own negation inside the match and are not affected.
NEGATED_BEFORE_RE = re.compile(
    r"(?:\b(?:no|none|nothing|nor|neither|zero|not|never)\b|n't)\s+(?:[\w-]+\s+){0,2}$",
    re.IGNORECASE,
)


def running_predicate(clause: str) -> bool:
    for m in RUNNING_RE.finditer(clause):
        at = m.group(0).lower().rfind("pending")
        if at >= 0 and NOT_CI_PENDING_RE.match(clause, m.start() + at):
            continue
        if m.group(0).lower().startswith("wait") and NOT_CI_WAIT_RE.match(clause, m.start()):
            continue
        if NEGATED_BEFORE_RE.search(clause[: m.start()]):
            continue
        return True
    return False


def ci_running_claims(closing: str) -> list[str]:
    """Every sentence of the closing message (with quotes folded) that claims CI is still running."""
    found = []
    for sentence in sentences(fold_quotes(closing)):
        sentence_ci = bool(CI_SUBJECT_RE.search(sentence) or PR_REF_RE.search(sentence))
        for clause in clauses(sentence):
            if not running_predicate(clause):
                continue
            if CI_SUBJECT_RE.search(clause) and not LOCAL_STRICT_RE.search(clause):
                found.append(sentence)
                break
            if LOCAL_RE.search(clause):
                continue
            if PR_REF_RE.search(clause) or (sentence_ci and SOFT_SUBJECT_RE.search(clause)):
                found.append(sentence)
                break
    return found


def claimed_prs(text: str) -> set[str]:
    return set(PR_REF_RE.findall(fold_quotes(text)))


def readable(sentence: str, limit: int = 200) -> str:
    s = sentence.replace(CODE_TOKEN, "`...`").replace(QUOTE_TOKEN, '"..."')
    return s if len(s) <= limit else s[: limit - 3] + "..."


# --- the watcher ---------------------------------------------------------------------------------

GH_PR_CHECKS_RE = re.compile(r"\bgh\s+pr\s+checks\b([^;&|\n]*)")
GH_RUN_WATCH_RE = re.compile(r"\bgh\s+run\s+watch\b")
LOOP_RE = re.compile(r"\b(?:until|while)\b")
LOOP_POLL_RE = re.compile(r"\bgh\s+(?:pr\s+checks|run\s+(?:view|list))\b|\bgh\s+api\b[^;&|\n]*check-runs")


def watched_pr(cmd: str) -> tuple[bool, str | None]:
    """(follows CI, the PR number it names or None) for a background command.

    Follows: `gh pr checks ... --watch` (not `--watch=false`), `gh run watch`, or an until/while loop
    polling `gh pr checks` / `gh run view|list` / the check-runs API. The PR is the positional
    argument of `gh pr checks` when it is a number or a `.../pull/<n>` URL; a branch name or no
    argument (the current branch) leaves it None, which follows any PR."""
    if not cmd or re.search(r"kitty-tail-(?:task|push|agent)\.py\b", cmd):
        return False, None  # a tail split names the watcher in --match/--title; it is not one
    pr = None
    for m in GH_PR_CHECKS_RE.finditer(cmd):
        try:
            args = shlex.split(m.group(1))
        except ValueError:
            args = m.group(1).split()
        skip = False
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("-i", "--interval", "-q", "--jq", "-t", "--template", "--json", "-R", "--repo"):
                skip = True
                continue
            if a.startswith("-"):
                continue
            u = re.search(r"/pull/(\d+)", a)
            if u:
                pr = u.group(1)
            elif re.fullmatch(r"#?\d+", a):
                pr = a.lstrip("#")
            break
    watch_flag = any(
        re.search(r"(?:^|\s)--watch(?:=true)?(?=\s|$)", m.group(1)) for m in GH_PR_CHECKS_RE.finditer(cmd)
    )
    if watch_flag or GH_RUN_WATCH_RE.search(cmd):
        return True, pr
    if LOOP_RE.search(cmd) and LOOP_POLL_RE.search(cmd):
        return True, pr
    return False, None


def follows(cmd: str, prs: set[str]) -> bool:
    ok, pr = watched_pr(cmd)
    if not ok:
        return False
    return pr is None or not prs or pr in prs


# --- the halt text -------------------------------------------------------------------------------

def suggestion(pr: str | None) -> str:
    n = pr or "<pr>"
    return (
        f"start `gh pr checks {n} --watch` with run_in_background: true (or `gh run watch <run-id>` "
        f"for one workflow run), then open its tail split in the same turn: "
        f"`python3 scripts/kitty-tail-task.py --title 'CI: PR #{n}' --file <its output file> "
        f"--match 'gh pr checks'`, and name its task id in the closing message"
    )


# --- selftest ------------------------------------------------------------------------------------

INSTANCE = (
    "#630 is pushed at e0421c88, its pre-push suite passed, and its body now covers the review fixes "
    "and the stamp-only run. CI on GitHub is still running; `compiles` and `cargo-test` hadn't "
    "finished at last check."
)

CLAIMS = [
    INSTANCE,
    "CI is pending on PR #630.",
    "CI failed on `clippy`; `cargo-test` is still running.",
    "The checks on #630 are queued.",
    "Waiting on CI for #630.",
    "`cargo-test` on PR #630 hasn't finished yet.",
    "GitHub Actions: compiles is in_progress.",
    "CI is still in progress -- `stage / policy` has not finished.",
    "| #630 | CI pending | waiting on `compiles` |",
]

NOT_CLAIMS = [
    "CI passed on PR #630: compiles, cargo-test and lint all succeeded.",
    "CI failed on #630: `cargo-test` failed in `stage / policy`.",
    "CI finished; nothing is still running.",
    "CI on #630 is done: none pending, two failed.",
    # 2026-11-25, same session, two sentences of one closing message: a local push, and a PR that
    # already passed.
    "PR #625 already passes CI. The `fix/ersc-203` push is still running (befvj57u5).",
    "Release PR #615 has never had its required checks run, so it is blocked until a push triggers them.",
    'You quoted "CI on GitHub is still running" from my earlier message.',
    "The injection line `CI STATE (measured, not remembered): PR #630 is PENDING` was read once.",
    "> CI STATE (measured, not remembered): PR #630 on `x` is PENDING",
    "PR #630 is pending review.",
    "The local checks are still running in the background.",
    "The pre-push suite is still running (b55sogynq).",
    "Other merge work is still running:",
    "The agent \"Fix stale-run sentinel selftest on CI\" is still running.",
    "Committed as 91b39cdf; CI has not been triggered because nothing is pushed.",
    # From the 2026-11-25 replay of 1600 closing messages: local workflows, local checks, the user.
    "The similarity-matrix workflow (wm52msbvd) is still running and builds on the same per-situation scores.",
    "Two checks are still running:",
    "The v27 agent is still running its Glintstone Pebble checks and a timing run.",
    "The #629 fixes are still waiting on your `authorize commit`.",
    "There's no pull request for the branch yet, so CI hasn't run on it.",
    "The Seamless-only game is still running past the 34 s mark, and task `b1` is timing it until "
    "it exits or its 90 s check runs out.",
    "The ledger fix and the picker fix are ready to commit as one batch, and the push (`bx1`) is "
    "still running the other checks.",
    "The CI speed-up workflow `wf1` is still running.",
    "#630, crate comments | two dangling comments wait on the waivers; 253 rewritten comment lines "
    "are left as they are",
]

WATCHERS = [
    ("gh pr checks 630 --watch", True, "630"),
    ("gh pr checks --watch --interval 30 630", True, "630"),
    ("gh pr checks https://github.com/Banon-Labs/er-mods-rs/pull/630 --watch --fail-fast", True, "630"),
    ("gh pr checks fix/branch --watch", True, None),
    ("gh pr checks --watch", True, None),
    ("gh pr checks --watch=true 630", True, "630"),
    ("gh run watch 123456789 --exit-status", True, None),
    ("until ! gh pr checks 630 | grep -q pending; do sleep 30; done", True, "630"),
    ("while gh run view 1234 --json status -q .status | grep -qv completed; do sleep 20; done", True, None),
    ("gh pr checks 630", False, None),
    ("gh pr checks 630 --watch=false", False, None),
    ("gh pr view 630 --json statusCheckRollup", False, None),
    ("sleep 600; gh pr checks 630", False, None),
    ("python3 scripts/kitty-tail-task.py --title 'CI' --file /t/b1.output --match 'gh run watch'", False, None),
    # Verbatim, paths shortened, from the 2026-11-25 main session: the watchers it did run.
    ("gh pr checks 628 --watch --interval 30 > /tmp/x/ci-628.log 2>&1; echo \"exit=$?\" >> /tmp/x/ci-628.log",
     True, "628"),
    ("(for n in 621 622 623 624 626; do echo \"== $n\"; gh pr checks $n --watch --interval 60 2>&1 | tail -3; "
     "done; echo ALLDONE) > /tmp/x/ci-five.log", True, None),
    ("sleep 20; until [ \"$(gh pr checks 612 --json bucket --jq '[.[]|select(.bucket==\"pending\")]|length' "
     "2>/dev/null)\" = \"0\" ]; do sleep 30; done", True, "612"),
]


def selftest() -> int:
    n = 0
    for text in CLAIMS:
        assert ci_running_claims(text), f"missed claim: {text!r}"
        n += 1
    for text in NOT_CLAIMS:
        got = ci_running_claims(text)
        assert not got, f"false claim {got!r} in: {text!r}"
        n += 1
    for cmd, want_ok, want_pr in WATCHERS:
        got = watched_pr(cmd)
        assert got == (want_ok, want_pr), f"{cmd!r}: got {got}, want {(want_ok, want_pr)}"
        n += 1
    assert claimed_prs(INSTANCE) == {"630"}, claimed_prs(INSTANCE)
    assert follows("gh pr checks 630 --watch", {"630"})
    assert not follows("gh pr checks 631 --watch", {"630"}), "a watcher of another PR follows nothing claimed"
    assert follows("gh pr checks --watch", {"630"}), "the current branch's PR is not knowable offline"
    assert follows("gh pr checks 631 --watch", set()), "a claim naming no PR takes any watcher"
    n += 4
    s = suggestion("630")
    assert "gh pr checks 630 --watch" in s and "kitty-tail-task.py" in s and "run_in_background" in s, s
    n += 1
    print(f"cupcake_ci_running_claim selftest: OK ({n} checks)")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        raise SystemExit(selftest())
    print(__doc__.strip().splitlines()[0])
    print("usage: cupcake_ci_running_claim.py --selftest")
    raise SystemExit(2)
