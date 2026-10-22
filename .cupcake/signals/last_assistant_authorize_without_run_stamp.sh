#!/usr/bin/env bash
# Cupcake signal: last_assistant_authorize_without_run_stamp
#
# Emits `AUTHNORUNSTAMP:<sha>\t<checkout>\t<evidence note>` when the closing prose of the last
# assistant turn asks the user for `authorize <sha>` (the push escape hatch read by
# scripts/cupcake_push_authorization.py) and `bash scripts/check-runtime-evidence.sh <full sha>`
# exits 1 for that sha: the commits change code under crates/ and no DLL log carries
# `build git=<sha>`. Empty otherwise, and empty on anything it cannot measure (fail-open).
#
# User, 2026-10-16, after an agent asked for `authorize <sha>` on feat/er-duel-rules and the push
# was then refused by the pre-push hook and by `ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE` for want of
# a run stamp: "I knew it needed a run stamp to push. I was hoping you would figure it out (again)
# prior to asking me to authorize a push". Building the DLL at the tip and launching it needed
# nothing from the user, so the authorization was asked for one step too early, and not for the
# first time.
#
# What it reads:
#   * the closing prose run of the last turn (`Turn.text_runs[-1]`), through the Stop event's
#     `transcript_path`. Backticks are not stripped: the ask is normally written as
#     `authorize 1a2b3c4d`, in code spans.
#   * every `authorize <7..40 hex digits>` in it. A message with no `authorize` token returns
#     before git or the evidence script is touched, so an ordinary turn pays only the transcript
#     read every Stop signal pays.
#
# Where a sha is resolved: the event's `cwd` first, then every checkout `git worktree list` names
# from that directory and from this repository. The agent often works in `.worktrees/<name>`
# while the session sits elsewhere, and the evidence script has to run in the checkout that knows
# the commit, as the pre-push hook would. A sha no checkout knows is allowed.
#
# The verdict is the exit status of that checkout's own scripts/check-runtime-evidence.sh (this
# repository's copy when the checkout has none), run there with the full sha, exactly as
# scripts/hooks/pre-push runs it: 1 halts; 0 (evidence present, carried forward, or no crate
# changed) and anything else (2, a crash, a timeout) allow.
#
# Timing. With a log naming the tip the script answers in about 0.15 s. Without one it tries to
# carry forward every older run, and that took 37 to 47 s for 23 candidates on 2026-10-16 until
# scripts/er-change-scope.py learned to stop at the first code path; it now takes about 4 s. That
# is still past the 5 s cupcake gives a discovered signal, so this signal is registered in
# .cupcake/rulebook.yml with its own timeout, and the evidence run is capped below that.
#
# Test seam: `CUPCAKE_AUTHORIZE_EVIDENCE_CHECK` replaces the evidence command with a command line
# that gets the full sha appended and runs in the resolved checkout. Only the regression tests set
# it; the Stop hook's environment is Claude Code's, which the agent cannot write.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
CUPCAKE_SIGNAL_EVENT=""
if [ ! -t 0 ]; then
	CUPCAKE_SIGNAL_EVENT="$(timeout 2 cat 2>/dev/null || true)"
fi
export CUPCAKE_SIGNAL_EVENT
python3 - <<'PY' 2>/dev/null || true
import json, os, re, shlex, subprocess, sys

repo_root = os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", ".")
sys.path.insert(0, os.path.join(repo_root, "scripts"))
try:
    import cupcake_turn_scan as scan
except Exception:
    sys.exit(0)

event = {}
try:
    parsed = json.loads(os.environ.get("CUPCAKE_SIGNAL_EVENT") or "{}")
    if isinstance(parsed, dict):
        event = parsed
except ValueError:
    pass

path = event.get("transcript_path")
if not (isinstance(path, str) and os.path.isfile(path)):
    path = scan.latest_transcript()
if not path:
    sys.exit(0)

turn = scan.last_text_turn(scan.split_turns(scan.load_events(path)))
if turn is None or not turn.text_runs:
    sys.exit(0)
closing = turn.text_runs[-1]

# Cheap gate first: no token, no git, no evidence run.
if "authorize" not in closing.lower():
    sys.exit(0)

# `authorize`, then spaces or the punctuation a code span or emphasis puts between the word and
# the sha, then 7..40 hex digits ending on a word boundary.
AUTH_RE = re.compile(r"\bauthorize[\s`'\"*:]+([0-9a-fA-F]{7,40})(?![0-9A-Za-z])", re.IGNORECASE)
shas = []
for m in AUTH_RE.finditer(closing):
    sha = m.group(1).lower()
    if sha not in shas:
        shas.append(sha)
if not shas:
    sys.exit(0)

# The Stop hook may run inside a git hook's environment (a pre-push test run, for one), where
# GIT_DIR wins over `-C`. Drop git's repository-pinning variables so every `git -C` below reads
# the directory it names.
env = dict(os.environ)
for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX",
             "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES"):
    env.pop(name, None)


def git(directory, *args):
    try:
        proc = subprocess.run(["git", "-C", directory, *args], capture_output=True, text=True,
                              timeout=3, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def worktrees(directory):
    listing = git(directory, "worktree", "list", "--porcelain")
    if not listing:
        return []
    return [line[len("worktree "):] for line in listing.splitlines() if line.startswith("worktree ")]


def checkouts():
    seen = []

    def add(directory):
        if directory and os.path.isdir(directory) and directory not in seen:
            seen.append(directory)

    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    if cwd and os.path.isdir(cwd):
        add(git(cwd, "rev-parse", "--show-toplevel"))
    for root in (cwd, repo_root):
        if root and os.path.isdir(root):
            for directory in worktrees(root):
                add(directory)
    return seen


def resolve(sha, dirs):
    for directory in dirs:
        full = git(directory, "rev-parse", "--verify", "--quiet", sha + "^{commit}")
        if full:
            return directory, full
    return None, None


# Kept under the rulebook timeout for this signal, so a slow answer is dropped here (an allow)
# rather than by cupcake.
EVIDENCE_TIMEOUT_SECONDS = 25

override = os.environ.get("CUPCAKE_AUTHORIZE_EVIDENCE_CHECK", "").strip()

dirs = None
for sha in shas[:4]:
    if dirs is None:
        dirs = checkouts()
    checkout, full = resolve(sha, dirs)
    if not full:
        continue
    if override:
        argv = shlex.split(override) + [full]
    else:
        script = os.path.join(checkout, "scripts", "check-runtime-evidence.sh")
        if not os.path.isfile(script):
            script = os.path.join(repo_root, "scripts", "check-runtime-evidence.sh")
        argv = ["bash", script, full]
    # Its own process group, so a timeout ends the python and git children the script spawned
    # rather than only the bash that started them.
    try:
        proc = subprocess.Popen(argv, cwd=checkout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    except OSError:
        continue
    try:
        _out, err = proc.communicate(timeout=EVIDENCE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.communicate()
        continue
    if proc.returncode != 1:
        continue
    note = ""
    for line in (err or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("evidence "):
            note = stripped[len("evidence "):].strip()
            break
    fields = [full[:12], checkout, note or "no DLL log names this commit"]
    sys.stdout.write("AUTHNORUNSTAMP:" + "\t".join(f.replace("\t", " ").replace("\n", " ") for f in fields))
    break
PY
