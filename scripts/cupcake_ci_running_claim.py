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
Actions, `gh pr checks`) in the clause, or checks, a job named in backticks, the word job, or a check
or workflow run in a clause whose sentence names CI or a PR. A PR number alone is not a CI subject:
"The fix for #<n> is still in progress" is about the work, not its checks. A report that CI
finished, passing or failing, has no such predicate. Quoted text (fenced code, `> ` block quotes, double-quoted spans, backtick spans)
is folded away before matching, so quoting the injection or the user is not a claim. A negated
predicate ("nothing is still running", "nothing in it is still running", "none pending") and "pending
review" are not claims. A clause about a local task (a push, a build, a subagent, a background task
id) is not a CI claim, nor is one that says nothing was pushed or committed ("Nothing is pushed, so
CI has not started").

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
# Workflow tool run named after CI (same replay). So is a clause saying nothing reached the remote:
# "Nothing is pushed, so CI has not started." -- CI that never started is not running. A push that
# did happen ("Pushed at abc1234 and CI on the PR is still running.") stays a claim.
LOCAL_STRICT_RE = re.compile(
    r"\blocal\b|\bpre-push\b|\bcheck\.sh\b|(?<!GitHub\s)\bworkflow\b|\btask\b|\b(?:sub)?agent\b"
    r"|\b(?:nothing|not|never|none)\s+(?:[\w-]+\s+){0,2}(?:pushed|committed)\b|\bun(?:pushed|committed)\b"
    r"|\bno\s+(?:push|commit)(?:es)?\b|\b(?:isn't|aren't|wasn't|weren't|hasn't|haven't)\s+(?:been\s+|yet\s+)?"
    r"(?:pushed|committed)\b|\bbefore\s+(?:the\s+|a\s+|any\s+)?push\b",
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
# A negation in the predicate's own stretch of words: "nothing is still running", "none pending",
# "no job is queued", "nothing in it is still running". Up to five words may sit between, but not
# punctuation or a conjunction, so "CI has not failed, but `x` is still running" and "nothing failed
# and `x` is still running" stay claims. The has-not forms carry their own negation inside the match
# and are not affected.
NEGATED_BEFORE_RE = re.compile(
    r"(?:\b(?:no|none|nothing|nor|neither|zero|not|never)\b|n't)\s+"
    r"(?:(?!(?:but|and|so|or|while|though|although|yet|whereas|because)\b)[\w" + CODE_TOKEN + r"-]+\s+){0,5}$",
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
            # A PR number is not a subject by itself ("Rebasing the PR onto main is still pending."):
            # the clause needs checks, a job or a run, in a sentence that names CI or a PR.
            if sentence_ci and SOFT_SUBJECT_RE.search(clause):
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


# A shell redirection with its target ("> /t/x.log", ">/t/x.log", "2>&1"), dropped before the
# arguments of `gh pr checks` are read so `--watch>/t/x.log` is still the flag.
REDIRECT_RE = re.compile(r"\d*[<>]{1,2}(?:&\d+|\s*[^\s<>()]+)?")
CHECKS_VALUE_FLAGS = ("-i", "--interval", "-q", "--jq", "-t", "--template", "--json", "-R", "--repo")


def checks_invocation(args_text: str) -> tuple[bool, str | None]:
    """(has --watch, the PR it names or None) for the argument text of one `gh pr checks`."""
    args_text = REDIRECT_RE.sub(" ", args_text)
    try:
        args = shlex.split(args_text)
    except ValueError:
        args = args_text.split()
    watch = any(a in ("--watch", "--watch=true") for a in args)
    pr, skip = None, False
    for a in args:
        if skip:
            skip = False
            continue
        if a in CHECKS_VALUE_FLAGS:
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
    return watch, pr


def watched_pr(cmd: str) -> tuple[bool, frozenset[str] | None]:
    """(follows CI, the PR numbers it follows or None) for a background command.

    Follows: `gh pr checks ... --watch` (not `--watch=false`), `gh run watch`, or an until/while loop
    polling `gh pr checks` / `gh run view|list` / the check-runs API. The PRs are the positional
    arguments of every watching (or, for a loop, polling) `gh pr checks` in the command, so one
    command watching two PRs follows both, when each is a number or a `.../pull/<n>` URL. A branch
    name, no argument (the current branch), a shell variable or a `gh run watch` leaves it None,
    which follows any PR."""
    if not cmd or re.search(r"kitty-tail-(?:task|push|agent)\.py\b", cmd):
        return False, None  # a tail split names the watcher in --match/--title; it is not one
    calls = [checks_invocation(m.group(1)) for m in GH_PR_CHECKS_RE.finditer(cmd)]
    watching = [pr for watch, pr in calls if watch]
    run_watch = bool(GH_RUN_WATCH_RE.search(cmd))
    if watching or run_watch:
        if run_watch or None in watching:
            return True, None
        return True, frozenset(watching)
    if LOOP_RE.search(cmd) and LOOP_POLL_RE.search(cmd):
        polled = [pr for _, pr in calls]
        if not polled or None in polled:
            return True, None
        return True, frozenset(polled)
    return False, None


def follows(cmd: str, prs: set[str]) -> bool:
    ok, watched = watched_pr(cmd)
    if not ok:
        return False
    return watched is None or not prs or bool(watched & prs)


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
    # Review of the PR-only-subject, negation-window and push-vocabulary fixes: these stay claims.
    "CI on #632 is still running.",
    "Pushed at abc1234 and CI on #632 is still running.",
    "CI has not failed, but `cargo-test` is still running.",
    "Nothing failed and `cargo-test` on #632 is still running.",
    "The checks on PR #632 are still pending.",
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
    # 2026-11-26 review: a PR number with no CI subject, a negation three words back, and a clause
    # saying nothing was pushed.
    "The fix for #631 is still in progress.",
    "Rebasing #633 onto main is still pending.",
    "Merging #632 is pending your authorize.",
    "PR #630 is open and your merge is pending.",
    "CI on #630 finished with every job green, so nothing in it is still running.",
    "Nothing is pushed, so CI has not started.",
]

P630, P628, P612 = frozenset({"630"}), frozenset({"628"}), frozenset({"612"})

WATCHERS = [
    ("gh pr checks 630 --watch", True, P630),
    ("gh pr checks --watch --interval 30 630", True, P630),
    ("gh pr checks https://github.com/Banon-Labs/er-mods-rs/pull/630 --watch --fail-fast", True, P630),
    ("gh pr checks fix/branch --watch", True, None),
    ("gh pr checks --watch", True, None),
    ("gh pr checks --watch=true 630", True, P630),
    ("gh run watch 123456789 --exit-status", True, None),
    ("until ! gh pr checks 630 | grep -q pending; do sleep 30; done", True, P630),
    # 2026-11-26 review: a redirection with no space before it, and one command watching two PRs.
    ("gh pr checks 630 --watch>/tmp/x.log", True, P630),
    ("gh pr checks 630 --watch 2>&1 >/tmp/x.log", True, P630),
    ("gh pr checks 630 --watch > /tmp/a.log; gh pr checks 631 --watch > /tmp/b.log", True,
     frozenset({"630", "631"})),
    ("gh pr checks 630 --watch; gh pr checks 631", True, P630),
    ("while gh run view 1234 --json status -q .status | grep -qv completed; do sleep 20; done", True, None),
    ("gh pr checks 630", False, None),
    ("gh pr checks 630 --watch=false", False, None),
    ("gh pr view 630 --json statusCheckRollup", False, None),
    ("sleep 600; gh pr checks 630", False, None),
    ("python3 scripts/kitty-tail-task.py --title 'CI' --file /t/b1.output --match 'gh run watch'", False, None),
    # Verbatim, paths shortened, from the 2026-11-25 main session: the watchers it did run.
    ("gh pr checks 628 --watch --interval 30 > /tmp/x/ci-628.log 2>&1; echo \"exit=$?\" >> /tmp/x/ci-628.log",
     True, P628),
    ("(for n in 621 622 623 624 626; do echo \"== $n\"; gh pr checks $n --watch --interval 60 2>&1 | tail -3; "
     "done; echo ALLDONE) > /tmp/x/ci-five.log", True, None),
    ("sleep 20; until [ \"$(gh pr checks 612 --json bucket --jq '[.[]|select(.bucket==\"pending\")]|length' "
     "2>/dev/null)\" = \"0\" ]; do sleep 30; done", True, P612),
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
    assert follows("gh pr checks 631 --watch; gh pr checks 630 --watch", {"630"}), "the first PR counts too"
    assert follows("gh pr checks 630 --watch>/tmp/x.log", {"630"}), "no space before the redirection"
    n += 6
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
