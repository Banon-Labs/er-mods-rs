#!/usr/bin/env bash
# Run a cupcake hook evaluation, surviving harness permission modes cupcake does not know yet.
#
# THREE BUGS THIS EXISTS TO FIX. The first two were observed live on 2026-08-24 with
# cupcake 0.5.2, the third was measured against the same binary on 2026-08-26:
#
# 1. NEW PERMISSION MODES ARE FATAL. Claude Code grew an `auto` permission mode; cupcake
#    deserializes `permission_mode` into a closed enum and exits 1 on anything outside
#    {default, plan, acceptEdits, bypassPermissions}:
#
#        Error: unknown variant `auto`, expected one of `default`, `plan`, ...
#
#    That is not a Stop-hook problem, it is EVERY hook -- PreToolUse and PostToolUse included --
#    so every guard in .cupcake/policies went inert for a whole session while the suite stayed
#    green. This repo has been here before (see the check.sh comment about every guard being
#    partly or wholly inert until 2026-08-22), which is exactly why the mode is normalised here
#    rather than waited on: an unrecognised mode must degrade to "evaluate anyway", never to
#    "evaluate nothing".
#
# 2. THE DEFAULT LOG LEVEL IS `info`. Unset, cupcake writes ~60 INFO lines to stderr on every
#    single hook -- policy-by-policy parse chatter, WASM compilation, signal gathering. The
#    user's own global config already passes `--log-level error`; the repo's hooks did not.
#
# 3. THE ENGINE ERASES UNQUOTED NEWLINES, so line 2 of a Bash command has no separator in
#    front of it (bd er-effects-rs-5eah). Measured, not inferred: `cupcake eval` replaces
#    UNQUOTED newlines with spaces before any policy runs, while newlines inside quotes
#    survive. Every git guard anchors command position on `(^|[;&|(\n])`, so
#
#        echo hi
#        git push origin main
#
#    arrived as `echo hi git push origin main` and was ALLOWED in production, while the very
#    same text denies under `opa test`. No pattern in any .rego file can reach this: the
#    separator is gone before evaluation. This script is the last place that sees the raw
#    text, so it restores the boundary here, rewriting each unquoted newline to `; `.
#
#    `;` is the right character on three independent counts: it is in the anchor class of
#    every git guard (`[;&|(\n]`, and `[;&|]\s*|\n` in the fresh-origin-main guard); the
#    Elden Ring launch guard already normalises `\n` to `;` itself before scanning; and the
#    engine measurement above shows `echo hi;<newline>git push origin main` DENYING, so the
#    character demonstrably survives the collapse the newline does not. The trailing space
#    matters too -- commands.has_verb anchors on `(^|\s)verb(\s|$)`, which `;git` would miss.
#
#    WHAT COUNTS AS QUOTED IS NOT THIS SCRIPT'S OWN OPINION. It is the same rule
#    .cupcake/system/commands.rego uses (escaped quotes stripped, split on the quote char,
#    odd indices are quoted, double-quoted-outer tried first and single-quoted-outer as the
#    fallback), transcribed below and mirrored deliberately. A newline inside a quoted span
#    is left alone: a `bd remember` body, a commit message or a doc that merely QUOTES a
#    guarded command must stay allowed, and turning those into `;` would manufacture exactly
#    the false positive bd er-effects-rs-dt2e removed. Where this shim and the policies
#    disagree about what is quoted, one of them is wrong; keeping them the same rule is the
#    point.
#
# stdout, stdin and the exit code are passed through untouched: the exit code is how cupcake
# denies an action, so swallowing it would disable the guards just as thoroughly as bug 1.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CUPCAKE_BIN="${CUPCAKE_BIN:-cupcake}"

# The whole normalisation, as one program so the hook still costs a single python start.
# `--normalize-only` runs it and stops, which is how scripts/test-cupcake-hook-shim.py gets
# at the REAL code for its span cases instead of a second transcription that could drift.
NORMALIZER=$(cat <<'PYSRC'
import json
import re
import sys

# Modes cupcake 0.5.2 accepts. Anything else is rewritten to `default`, which is the least
# privileged of them -- a mode we cannot interpret must never be treated as more permissive
# than the one the user is actually in.
KNOWN_MODES = {"default", "plan", "acceptEdits", "bypassPermissions"}

# The characters commands.rego blanks inside a quoted span (its blanked_anchors). Kept
# identical so this shim and the policies read the same text the same way.
ANCHORS = set("\n\r;&|()'\"")

# commands.rego's shell_heredoc_reader_pattern, verbatim. Rego's regex.match is an
# unanchored search, so re.search is the faithful translation.
SHELL_HEREDOC_READER = re.compile(
    r"""(?i)(^|[ \t;&|(])(command[ \t]+)?(([^ \t;&|()'"]*/)?(ba|z|k|da|a)?sh|eval|source|\.)([ \t]+-{1,2}[a-z][a-z0-9-]*)*[ \t]*$"""
)
# commands.rego's heredoc_tag pattern, verbatim (anchored there, so re.match here).
HEREDOC_TAG = re.compile(r"""^-?[ \t]*["']?([A-Za-z_][A-Za-z0-9_]*)["']?""")

UNRESOLVABLE = "unresolvable"
SHELL_READ = "shell"
NO_HEREDOC = None


def heredoc_region(text):
    """Mirror commands.rego's heredoc_body_blanked: which slice of TEXT is a heredoc body.

    HEREDOC DECISION (deliberate, and the direction matters):

      * a body a NON-SHELL command reads -- `python3 - <<'EOF'`, `cat > doc.md <<EOF`,
        `git commit -F - <<'EOF'` -- is DATA, and is treated exactly like a quoted span:
        its newlines are left as newlines. This repo's agents write those constantly, and
        one containing a line-anchored `git push origin main` is documentation, not a push.
        It is also what keeps this shim from breaking the policies' own heredoc handling:
        commands.rego finds the body by looking for "\n" + tag, so rewriting the
        terminator's newline to `;` would make the heredoc unrecognisable there, drop the
        text back to its raw form, and deny every doc heredoc. The terminator newline is
        therefore inside the preserved region.

      * a body a SHELL reads -- `bash <<EOF` -- is a PROGRAM, and its newlines become
        separators like any other command text. commands.rego already refuses to blank
        those bodies for the same reason; this is that stance carried to the shim, and it
        closes the shell-read-heredoc case pinned as known-open in
        scripts/test-cupcake-policies.py.

    Returns (start, end) for a data body, SHELL_READ, UNRESOLVABLE, or NO_HEREDOC.
    """
    parts = text.split("<<")
    if len(parts) == 1:
        return NO_HEREDOC
    if len(parts) != 2:
        # More than one heredoc. commands.rego understands exactly one and keeps scanning
        # the RAW text; this stops instead (see the UNRESOLVABLE note in rewrite_command),
        # because the bodies it can no longer locate are usually documentation.
        return UNRESOLVABLE
    before, after = parts
    if SHELL_HEREDOC_READER.search(before):
        return SHELL_READ
    matched = HEREDOC_TAG.match(after)
    if not matched:
        # `<<<` here-strings, `$((x << 2))`, `<<$VAR`: there is no multi-line body here at
        # all, so there is nothing to protect and the quote scan can carry on. This is what
        # commands.rego does with the same input, and it is the one UNRESOLVABLE-shaped case
        # where carrying on cannot put a heredoc body at risk.
        return NO_HEREDOC
    tag = matched.group(1)
    terminator = "\n" + tag
    if after.count(terminator) != 1:
        # A body whose terminator never appears, or appears more than once (a line of the
        # body starting with the tag). The body IS multi-line and cannot be located, so this
        # stops rather than guess where it ends.
        return UNRESOLVABLE
    start = len(before) + 2
    end = start + after.index(terminator) + 1  # through the newline that closes the body
    return (start, end)


def mask_escaped_quotes(work):
    """commands.rego DELETES `\\"` then `\\'` before reading quote parity.

    Deleting and masking are the same thing for a parity read, and masking keeps every
    index aligned with the original command, which is what lets the mask be applied back.
    """
    for quote in ('"', "'"):
        i = 0
        while i < len(work) - 1:
            if work[i] == "\\" and work[i + 1] == quote:
                work[i] = "\x00"
                work[i + 1] = "\x00"
                i += 2
            else:
                i += 1


def blank_spans(work, outer, inner):
    """commands.rego's blank_spans: outer quote style first, then inner in what survives.

    Returns a per-index "this is inside a quoted span" mask, or None when either parity
    read is meaningless -- an unmatched quote means the split-index trick says nothing.
    """
    mask = [False] * len(work)
    for quote in (outer, inner):
        positions = [i for i, char in enumerate(work) if char == quote]
        if len(positions) % 2:
            return None
        for pair in range(0, len(positions), 2):
            for i in range(positions[pair] + 1, positions[pair + 1]):
                mask[i] = True
                if work[i] in ANCHORS:
                    work[i] = " "
    return mask


def continues_line(text, index):
    """A newline preceded by an ODD number of backslashes is a line continuation.

    It JOINS two lines; it is not a command boundary, so it must not become `;`. Left as a
    newline, the engine collapses it to the space the join already meant.
    """
    backslashes = 0
    i = index - 1
    while i >= 0 and text[i] == "\\":
        backslashes += 1
        i -= 1
    return backslashes % 2 == 1


def rewrite_command(command):
    """UNQUOTED newlines to `; `. None means "cannot decompose this" -- see the caller.

    Note what this can and cannot do: it only ever ADDS a separator, never removes text, so
    it can only make a guard fire on MORE commands. The failure it must avoid is the other
    direction -- inventing a command boundary inside quoted prose.
    """
    if "\n" not in command:
        return command

    work = list(command)
    quoted = [False] * len(work)

    region = heredoc_region(command)
    if region == UNRESOLVABLE:
        # KNOWN-OPEN RESIDUE, and deliberately the safe direction. A multi-line heredoc body
        # this cannot locate (two heredocs, a terminator that never appears) leaves the whole
        # command untouched, which is exactly today's behaviour: the engine collapses those
        # newlines and the lines after the first stay invisible. That is a divergence from
        # commands.rego, which keeps scanning the raw text -- but it diverges in the one
        # direction that is safe, since passing a text through can only under-deny, never
        # invent a boundary inside a documentation heredoc.
        return None
    if isinstance(region, tuple):
        start, end = region
        for i in range(start, end):
            quoted[i] = True
            if work[i] in ANCHORS:
                work[i] = " "
    # NO_HEREDOC and SHELL_READ both fall through with nothing marked: there is either no
    # body, or the body is a program whose line breaks are boundaries like any other.

    # commands.rego refuses to read quote spans once command substitution is present,
    # because substitution inside double quotes really does execute. Same refusal here, and
    # for the same reason -- disagreeing with the policies about what is quoted is worse
    # than leaving this shape alone. It is checked AFTER the heredoc blanking, so a `$(` in
    # a documentation heredoc does not disqualify the whole command (its `(` is gone).
    resolved = "".join(work)
    if "$(" in resolved or "`" in resolved:
        return None

    mask_escaped_quotes(work)

    # Double-quoted-outer first, single-quoted-outer as the fallback; commands.rego's order,
    # because the fallback is what reads `echo 'don"t' ; ...` correctly.
    spans = blank_spans(list(work), '"', "'")
    if spans is None:
        spans = blank_spans(list(work), "'", '"')
    if spans is None:
        # Unbalanced quotes: the parity read is meaningless, so nothing here knows what is
        # quoted. Pass it through rather than guess. Same residue note as the heredoc case.
        return None
    for i, is_quoted in enumerate(spans):
        if is_quoted:
            quoted[i] = True

    out = []
    for i, char in enumerate(command):
        if char == "\n" and not quoted[i] and not continues_line(command, i):
            out.append("; ")
        else:
            out.append(char)
    return "".join(out)


def normalize(payload):
    if not isinstance(payload, dict):
        return payload
    if payload.get("permission_mode") not in KNOWN_MODES and "permission_mode" in payload:
        payload["permission_mode"] = "default"
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        rewritten = rewrite_command(tool_input["command"])
        if rewritten is not None:
            tool_input["command"] = rewritten
    return payload


raw = sys.stdin.read()
try:
    parsed = json.loads(raw)
except (ValueError, TypeError):
    # Not JSON we understand: hand it over untouched and let cupcake be the one to complain.
    sys.stdout.write(raw)
    sys.exit(0)
try:
    sys.stdout.write(json.dumps(normalize(parsed)))
except Exception:  # a normaliser bug must not take every guard down with it
    sys.stdout.write(raw)
PYSRC
)

if [ "${1:-}" = "--normalize-only" ]; then
	python3 -c "$NORMALIZER"
	exit $?
fi

# The raw event is held so a normaliser failure can still be evaluated. Feeding cupcake an
# empty payload would fail the hook open, which is the one outcome this file exists to prevent.
raw_event=$(cat)
normalized=$(printf '%s' "$raw_event" | python3 -c "$NORMALIZER")
normalize_rc=$?
if [ "$normalize_rc" -ne 0 ] || [ -z "$normalized" ]; then
	normalized=$raw_event
fi

printf '%s' "$normalized" | "$CUPCAKE_BIN" eval \
	--harness claude \
	--log-level error \
	--policy-dir "$repo_root/.cupcake" \
	--global-config "$repo_root/.cupcake/rulebook.yml"
exit "${PIPESTATUS[1]}"
