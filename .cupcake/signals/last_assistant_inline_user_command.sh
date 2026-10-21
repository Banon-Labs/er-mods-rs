#!/usr/bin/env bash
# Cupcake signal: last_assistant_inline_user_command
#
# Emits `INLINEUSERCOMMAND:<detail>` when the closing prose of the last assistant turn carries a
# fenced code block whose info string names a shell language. Empty otherwise (fail-open).
#
# User directive 2026-10-13, in their words: "Update your standing order so that instead of
# providing me inline bash, you open a kitty tab for each inline bash block you want me to run,
# pre-pasted, with a comment block at the top saying why it should be ran".
#
# The replacement is scripts/kitty-user-command.py, which types the command into a focused kitty
# tab without submitting it, under a `#` comment block. A shell-fenced block in the closing
# message is the thing that helper exists to replace, so its presence is the whole signal: a
# command the user is meant to run belongs in a tab, and a record of a command the agent already
# ran is already in the tool output.
#
# What counts, line by line, the way a CommonMark renderer reads it:
#   * an opening fence -- three or more backticks or tildes, after any indentation or blockquote
#     markers (a fence inside a list item is indented) -- whose info string's first word is a
#     shell language: bash, sh, shell, zsh, fish, ksh, dash, ash, csh, tcsh, console,
#     shell-session, shellsession, sh-session, bash-session. Case-insensitive, and `{.bash}` /
#     `{bash}` spellings are read as bash;
#   * an opener that never closes (an interrupted turn) still counts -- the opener is on screen.
# What does not:
#   * fences in any other language (rust, text, json, toml) and fences with no info string;
#   * inline code, single or triple backticks, because a backtick fence's info string may not
#     contain a backtick, so ```git push``` on one line is inline code, not a fence;
#   * a fence-looking line inside another open fence (a ````markdown block quoting a ```bash
#     example), since only a bare fence of the same character and at least the same length
#     closes the outer one.
#
# Only the closing prose run is scanned (`Turn.text_runs[-1]`, contiguous text with no tool call
# after it), so a block written mid-turn and followed by work is not judged here.
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

path = scan.hook_transcript()
if not path:
    sys.exit(0)

turn = scan.last_text_turn(scan.split_turns(scan.load_events(path)))
if turn is None or not turn.text_runs:
    sys.exit(0)
closing = turn.text_runs[-1]

SHELL_LANGS = frozenset({
    "bash", "sh", "shell", "zsh", "fish", "ksh", "dash", "ash", "csh", "tcsh",
    "console", "shell-session", "shellsession", "sh-session", "bash-session",
})

# Indentation and blockquote markers, then the fence run, then the info string.
FENCE_RE = re.compile(r"^[ \t]*(?:>[ \t]*)*(`{3,}|~{3,})(.*)$")
LANG_RE = re.compile(r"\{?\s*\.?([A-Za-z0-9_+-]+)")


def lang_of(info):
    m = LANG_RE.match(info.strip())
    return m.group(1).lower() if m else ""


found = []          # [lang, first non-empty content line or ""]
open_fence = None   # (char, length) of the fence currently open
capture = None      # the entry in `found` still waiting for its first content line
for line in closing.splitlines():
    m = FENCE_RE.match(line)
    if open_fence is None:
        if not m:
            continue
        fence, info = m.group(1), m.group(2)
        if fence[0] == "`" and "`" in info:
            continue  # inline code that happens to start a line
        open_fence = (fence[0], len(fence))
        lang = lang_of(info)
        if lang in SHELL_LANGS:
            found.append([lang, ""])
            capture = found[-1]
        continue
    if m and m.group(1)[0] == open_fence[0] and len(m.group(1)) >= open_fence[1] \
            and not m.group(2).strip():
        open_fence = None
        capture = None
        continue
    if capture is not None and line.strip():
        capture[1] = re.sub(r"^[ \t]*(?:>[ \t]*)*", "", line).strip()
        capture = None

if not found:
    sys.exit(0)

lang, first = found[0]
count = len(found)
noun = "a shell-fenced block" if count == 1 else f"{count} shell-fenced blocks"
detail = f"{noun}, the first fenced as {lang}"
if first:
    detail += f", opening with: {first[:200]}"
sys.stdout.write("INLINEUSERCOMMAND:" + detail)
PY
