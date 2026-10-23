#!/usr/bin/env bash
# Cupcake signal: commit_authorization
#
# Consumed by `.cupcake/policies/claude/git_commit_authorization.rego` and
# `.cupcake/policies/claude/no_lint_before_commit.rego`. Prints one tab-separated line:
#
#   `<AUTHORIZED|DENIED|-> <LIVE|DENIED> <detail>`
#
# The first field answers "may this command commit" (`-` when it holds no commit), the second
# "may this command run a lint or formatter". Both open only on the user's own last typed message
# being exactly `authorize commit`, in the main session, while no commit it paid for has landed.
# The judgement and its reasoning live in `scripts/cupcake_commit_authorization.py`.
#
# Fails closed: each policy allows only on its own allowing word, so a missing reader, a crash or a
# timeout leaves commits and lints denied. There is no override that can print an allowing word.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_commit_authorization.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

# Most Bash calls name neither a commit nor a lint; skip the python start for those. The words
# here are a superset of what the two policies detect, so a command they judge always has a line.
case "$event" in
*commit* | *fmt* | *clippy* | *shellcheck* | *ruff* | *black* | *prettier* | *eslint* | *isort* | \
    *flake8* | *pylint* | *mypy* | *taplo* | *dprint* | *clang-format* | *yamllint* | \
    *markdownlint* | *stylua* | *biome* | *comment-caps* | *lossy-utf8* | *check.sh* | *--fix*) ;;
*) exit 0 ;;
esac

# Cupcake drops a discovered signal after 5 s; 4.5 s ends the reader before cupcake gives up, so a
# reader still running then never records a spend for a commit cupcake has already denied. The
# reader does not wait for the tool call's own transcript line (it lands only after PreToolUse
# returns): one read of the transcript and two `git rev-parse` calls, well under a second.
printf '%s' "$event" | timeout 4.5 python3 "$reader" 2>/dev/null || true
exit 0
