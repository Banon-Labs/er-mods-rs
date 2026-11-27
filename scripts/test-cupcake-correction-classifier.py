#!/usr/bin/env python3
"""Precision of the correction classifier (`scripts/cupcake_correction_first.correction`) on real
prompts the user typed in this repo's sessions.

The corpus is `.cupcake/tests/corpus/correction_prompts.jsonl`, one prompt per line, copied verbatim
from the transcripts under ~/.claude/projects/-home-banon-projects-er-mods-rs/ on 2026-11-11 (the
`source` field is `<transcript id prefix>:<message uuid>`; pasted logs are trimmed to an empty
`<pasted_content>`). Labels:

  correction  -- the prompt corrects how the agent works: a complaint about its behavior, a standing
                 rule about how to work, or a challenge to a choice it made.
  other       -- a task request, a question about the game or the code, a fact the user supplies,
                 a status report.
  borderline  -- a correction of the product the agent made rather than of how it works (the
                 spawned duelists were given too much AI), an insult carrying a fact, or a request
                 to build a rule.
                 Reported, never scored: whether these should open a correction turn is a judgement
                 the corpus cannot settle.

`set` says how each line was chosen. `tuned` lines (46 corrections, 50 others, 10 borderline) were
picked from 865 unique typed prompts in the 70 newest transcripts and the classifier's patterns were
written while reading them, so their score is in-sample. `heldout` lines are every hit the finished
classifier made on the other 759 prompts, labelled after the fact: 4 corrections and 3 borderline,
with no task request or plain question among them.

Four `tuned` lines were added on 2026-11-19 from session 5ec7b779, after the guard halted a turn on
"Do you have to re-run to get the same information if we consider Faith instead of Int?" (other: it
asks whether a computation must be repeated). Beside it: "This doesn't sound right, if it says Int
..." (other: it doubts a run's output and asks for no change in how the agent works; the right answer
is to check the run), and two corrections, "Can't you run golden land by itself with better profiling
..." and "Can't we scan the steam API instead?", each a better way of working put to the agent as a
question. The corpus now holds 48 + 4 corrections, 50 + 2 others and 13 borderline lines.

Three `tuned` lines were added on 2026-11-22 after the UserPromptSubmit reminder quoted "do not repeat
work that is already complete." from the harness's usage-limit reset text ("Your claude.ai usage limit
has reset. Continue the task ..."), which the user never typed. Two are that text (other), alone and
with the local `/login` record and a task-notification reminder the hook received beside it; the third
is the user saying the same thing in their own words, "stop redoing finished work, you repeated the
run" (correction; its `source` is `synthetic`, not a transcript message). The corpus now holds 53
corrections, 54 others and 13 borderline lines.

The gate: no `other` prompt may be flagged, and at least 90% of `correction` prompts must be.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / ".cupcake" / "tests" / "corpus" / "correction_prompts.jsonl"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

MIN_RECALL = 0.90


def main() -> int:
    import cupcake_correction_first as cf

    rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
    tp = fp = fn = tn = 0
    misses: list[str] = []
    false_alarms: list[str] = []
    borderline_hits = 0
    borderline = 0
    for row in rows:
        hit = cf.correction(row["prompt"])
        label = row["label"]
        if label == "correction":
            if hit:
                tp += 1
            else:
                fn += 1
                misses.append(row["prompt"][:100])
        elif label == "other":
            if hit:
                fp += 1
                false_alarms.append(f"{row['prompt'][:80]!r} -> {hit[:80]!r}")
            else:
                tn += 1
        else:
            borderline += 1
            borderline_hits += bool(hit)

    scored = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(f"correction classifier: {scored} scored prompts ({tp + fn} corrections, {fp + tn} others)")
    print(f"  confusion: TP={tp} FP={fp} FN={fn} TN={tn}  precision={precision:.3f} recall={recall:.3f}")
    print(f"  borderline (not scored): {borderline_hits} of {borderline} flagged")
    for miss in misses:
        print(f"  missed correction: {miss!r}")
    failures = 0
    for alarm in false_alarms:
        failures += 1
        print(f"FAIL flagged a non-correction: {alarm}", file=sys.stderr)
    if recall < MIN_RECALL:
        failures += 1
        print(f"FAIL recall {recall:.3f} is under {MIN_RECALL}", file=sys.stderr)
    if failures:
        print(f"test-cupcake-correction-classifier: {failures} failure(s)", file=sys.stderr)
        return 1
    print("test-cupcake-correction-classifier: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
