#!/usr/bin/env bash
# Push one branch through the pre-push hook without a foreground shell sitting on it.
#
# Detached on purpose too. That suite takes ~25 minutes, far past an agent shell's 30s budget, and
# killing a push mid-suite orphans check.sh's flock holders
# (bd killing-a-push-orphans-check-sh-flock-holders-2026-09-11). Launch it with
# `setsid nohup ... &` and read the log.
#
# `main` is never named here. The two refs are literal.
set -euo pipefail
cd "$(dirname "$0")/.."
# The branch to push, defaulting to whatever is checked out. The stack this started as is merged
# (#433 -> main on 2026-09-12), so there is no base ref to fast-forward any more; what is left is
# the one thing this script was always for -- getting a push through a pre-push hook that runs the
# whole gate suite, without a foreground shell sitting on it.
branch="${1:-$(git rev-parse --abbrev-ref HEAD)}"
git push -u origin "$branch"
echo "PUSH-SEQUENCE-DONE"
