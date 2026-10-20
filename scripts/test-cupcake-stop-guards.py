#!/usr/bin/env python3
"""End-to-end proof that the Stop-event guards actually halt a turn.

Why this test exists (2026-08-22). Every Stop guard in this repo was inert for 36 days -- from the
day the first one landed (2026-07-17) until the day this was written -- and the suite stayed green
the entire time, because the coverage was split in a way that left the real path untested:

  * scripts/test-*-signal.py           tested the signal shell scripts alone (no policy, no cupcake);
  * .cupcake/tests/*_test.rego         tested the policies in the OPA interpreter (`opa test`);
  * scripts/test-cupcake-policies.py   ran the real `cupcake eval` binary -- PreToolUse events only.

`cupcake eval` does not use the OPA interpreter. It compiles the policies to WASM and runs them in
its own embedded runtime, where an unimplemented host builtin (`sprintf`) silently yields undefined
and the rule never fires. So the signal passed, the policy passed, the interpreter passed, and the
thing that actually runs at turn-end returned `{}` -- a clean allow -- every single time.

This test closes that hole by driving the whole path exactly as Claude Code does: the real
transcript on disk, the real signal scripts, the real `cupcake eval` commands read out of
.claude/settings.json, and an assertion on the verdict that comes back. Both directions are asserted
-- a guard that cannot halt is useless, and a guard that halts on a clean turn wedges every session.

It also drives UserPromptSubmit (2026-08-22), because one guard deliberately does not halt. Claude
Code renders every Stop verdict into the user's transcript ("Stop hook error: <reason>") and Stop
fires only after the assistant's text has already been streamed, so a Stop halt on answer length
makes the user read the long version, the scolding and the rewrite -- three times the reading, from
a rule whose whole purpose is less of it. wall_of_text therefore lives on UserPromptSubmit, whose
`additionalContext` is a hidden attachment and lands before the next answer is written. Two things
have to hold and both are asserted here: it must not halt at Stop, and its correction must actually
come back on the invisible channel.

Fixtures live in .cupcake/tests/fixtures/*.jsonl and are ordinary Claude Code transcripts.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / ".cupcake" / "tests" / "fixtures"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


@dataclass(frozen=True)
class Case:
    fixture: str
    # Distinctive substring of the expected halt reason, or None when the turn must be allowed.
    # Matched on the reason rather than the rule_id because cupcake renders a lone decision as a
    # bare reason string with no [rule_id] prefix.
    expect_halt_text: str | None
    why: str
    # Extra environment for the hook, as (name, value) pairs. For a signal whose verdict comes from
    # outside the transcript, so the case pins that verdict instead of reading this machine's.
    env: tuple[tuple[str, str], ...] = ()


CASES = [
    Case(
        "unexecuted_promise.jsonl",
        "promise nothing is going to keep",
        "turn ends on 'I'll re-run the gate...' with no tool call, no background work, no handoff",
    ),
    Case(
        "described_next_step.jsonl",
        "describing a next step instead of starting it",
        "turn names the mechanism, the target and the tool for the next step and begins none of it "
        "-- no tool call, no blocker, no question (the 2026-09-08 directive)",
    ),
    Case(
        "described_next_step_nominal.jsonl",
        "describing a next step instead of starting it",
        "the same rule reached by the nominal spelling, verbatim from 2026-09-16: 'The pre-flight "
        "itself is not built yet -- ... which is the next edit.' The turn made a real edit first, "
        "so the fixture also pins that a delivered turn does not excuse the closer that defers the "
        "rest. It escaped because the guard's noun list wanted a copula after the noun ('the next "
        "step is'), and this puts the copula in front of it",
    ),
    Case(
        "described_next_step_no_oracle.jsonl",
        None,
        "the other half of that widening: 'launched as br-20260913-142426-cc94 -- but not yet "
        "confirmed by you or by any oracle' is the in-game observation AGENTS.md names as a "
        "legitimate ending, and it must NOT halt. It did before 'confirmed by you' joined the "
        "handoff family, which is the false positive the nominal spellings nearly bought",
    ),
    Case(
        "idle_hold.jsonl",
        "announced holding/idling",
        "turn is a pure pause announcing an idle hold while a background task runs",
    ),
    Case(
        "idle_hold_report_after_notification.jsonl",
        None,
        "a final report after both background tasks finished -- one completion delivered mid-turn "
        "as a queued_command attachment, the other as a notification that started the turn -- and "
        "one status peek of the output. Must NOT halt: VERBOSEPAUSE halted this shape four times "
        "in a row on 2026-10-04 because it read only the user-event notification carrier",
    ),
    Case(
        "idle_hold_answer_while_background_live.jsonl",
        None,
        "a one-paragraph answer to the user's direct question while an unrelated background board "
        "run is still live. Must NOT halt: VERBOSEPAUSE halted this exact shape on 2026-10-04 because "
        "any live background job made every long closing message a blocked pause",
    ),
    Case(
        "idle_hold_blocked_recap_after_question.jsonl",
        "paused while blocked on a background task",
        "the other half: a user question ('How is it going?') answered with 'Blocked on the board "
        "generation run.' plus a long recap is still a verbose blocked pause and must halt",
    ),
    Case(
        "authority_agreement.jsonl",
        "authority-coded agreement",
        "turn opens with \"You're right\" -- banned agreement phrasing (2026-07-17 directive)",
    ),
    Case(
        "wall_of_text.jsonl",
        None,
        "four paragraphs -- must NOT halt: a Stop verdict is printed to the user and the text is "
        "already on screen, so halting costs a third reading instead of saving one (see UPS_CASES)",
    ),
    Case(
        "narration_between_tools.jsonl",
        None,
        "six one-line preambles between six tool calls -- ordinary work, not a wall of text",
    ),
    Case(
        "stall_on_friction.jsonl",
        "handing the decision back",
        "user pushback met with an admission and a menu instead of the corrective action",
    ),
    Case(
        "stall_after_instruction_violation_question.jsonl",
        "ended the turn with an admission",
        "the user asked a question-shaped correction after an explicit instruction was violated; "
        "answering the question and asking for access is still pausing instead of corrective action",
    ),
    Case(
        "proof_without_observation.jsonl",
        "cited nothing that observed it in the game",
        "the turn calls a runtime feature proven off a load and an address translation -- neither is "
        "an outcome anyone could have watched happen (the 2026-09-09 directive)",
    ),
    Case(
        "proof_with_observation.jsonl",
        None,
        "the same claim beside the run id that observed it -- must NOT halt, or citing a run is "
        "punished the same as citing nothing",
    ),
    Case(
        "unbacked_claim.jsonl",
        "claiming an artifact exists that you did not create",
        "the turn claims a gate was built and wired in, and ships one `bd remember` -- the exact "
        "2026-08-23 incident. Its signal read a Turn attribute that does not exist, so the guard was "
        "silently inert from the day it landed until 2026-09-09 while its six opa tests stayed green",
    ),
    Case(
        "backed_claim.jsonl",
        None,
        "the same sentence beside the Edit that backs it -- must NOT halt, or delivering the thing "
        "and saying so is punished identically to claiming it and not delivering",
    ),
    Case(
        "promissory_closer.jsonl",
        "announcing a fix as though it were already underway",
        "the turn closes on 'Fixing both: bypass the union ...' -- a present participle with no "
        "subject, announcing work as if in flight, and the turn wrote nothing (the 2026-09-09 "
        "directive). Neither sibling could see it: no first-person opener for "
        "no_unexecuted_promise, no forward-looking prescription for no_described_next_step",
    ),
    Case(
        "promissory_closer_with_edit.jsonl",
        None,
        "the same closing sentence beside the Edit that makes it true -- must NOT halt, or "
        "reporting work just done is punished identically to announcing work never done",
    ),
    Case(
        "promissory_gerund_mid_turn.jsonl",
        None,
        "'Wiring the detour entry now.' as a mid-turn preamble before the Edit, with an ordinary "
        "closing report -- the correct shape, and the one a gerund rule must never touch",
    ),
    Case(
        "unread_evidence_closer.jsonl",
        "naming the evidence that answers your own open question",
        "the turn closes on 'which its own log answers, so I am reading that next' -- the answer "
        "was in a file on disk and the read was left for after the turn. The same defect as the "
        "promissory closer in the grammar of an observation, which is how it slipped past a "
        "pattern keyed on a work gerund or a first-person opener",
    ),
    Case(
        "zero_information_stop.jsonl",
        "nothing for the user to reply",
        "asked what their ideal response was, the turn answered 'Nothing -- the ball is in my "
        "court. Rebuilding and relaunching now; the one thing I'll need from you afterwards is a "
        "single use of the item.' Both actions were its own and unblocked, and the in-game input "
        "it asked for is one it drives itself (the 2026-09-09 directive). The trailing clause is "
        "what disarmed no_unexecuted_promise and no_described_next_step: both exempt a turn that "
        "hands the obligation to the user",
    ),
    Case(
        "zero_information_stop_observation.jsonl",
        None,
        "the same 'Rechecking the log now' shape beside an observation only the user can make -- "
        "must NOT halt, or the launch-handoff protocol becomes unspeakable",
    ),
    Case(
        "zero_information_stop_delivered.jsonl",
        None,
        "'nothing on your side is needed' closing a turn that edited the file and reports the "
        "green gate -- the honest end of a finished task, not a handback",
    ),
    Case(
        "deferred_evidence_read.jsonl",
        "pointing at evidence that already exists and did not open it",
        "the turn closes on 'er-quickload-autoload-debug.log will say which' and never opens the "
        "file (the 2026-09-09 directive). The sibling arm's own pattern cannot span the modal "
        "between the artifact and the verb of telling, so this is the shape that reaches "
        "ER-EFFECTS-NO-DEFERRED-EVIDENCE-READ rather than ER-EFFECTS-NO-PROMISSORY-CLOSER",
    ),
    Case(
        "deferred_evidence_future.jsonl",
        None,
        "the same deferral pointed at a log the next run has still to write -- must NOT halt, or "
        "deferring to evidence that does not exist yet becomes unspeakable, and this repo does it "
        "in most turns that end on a launch",
    ),
    Case(
        "deferred_evidence_consulted.jsonl",
        None,
        "the same sentence beside the tail that opened the log and the finding it produced -- must "
        "NOT halt, or reading the evidence is punished identically to skipping it",
    ),
    Case(
        "future_tense_commitment.jsonl",
        "promising work you could have done in it",
        "the turn closes on 'Next run I'll build this in and re-attach Frida to confirm zero "
        "DISCARDING lines ...' -- the build command and the launch script were both available, so "
        "the deadline bought a round trip worth nothing (the 2026-09-09 directive, and a repeat "
        "offence). On the REAL turn all three neighbours stayed silent: a live game session made "
        "`carried` true for two of them, and the third reads a deadline as a handoff",
    ),
    Case(
        "future_tense_commitment_acted.jsonl",
        None,
        "the same grammar over work the turn actually did -- an edit-class promise beside the Edit "
        "-- must NOT halt, or reporting what you just did is punished identically to deferring it",
    ),
    Case(
        "delegation_as_completion.jsonl",
        "describing what a subagent you just dispatched contains",
        "the turn dispatched an agent and then listed its deliverables -- 'It has the verbatim "
        "sentence, the exemptions ..., and the same evidence bar the others got: ...' -- before any "
        "completion notification existed. The Agent tool's own result says the caller knows nothing "
        "about those results until the notification arrives",
    ),
    Case(
        "delegation_reported.jsonl",
        None,
        "the same enumeration after the completion notification landed -- must NOT halt, or "
        "relaying what an agent actually reported is punished identically to predicting it",
    ),
    Case(
        "challenged_convention.jsonl",
        "challenged a choice you made",
        "asked 'Do you crutch on 1.16.2 addreses for a specific reason?' the turn answered with a "
        "table of why the convention exists and changed nothing (the 2026-09-10 directive). Every "
        "neighbour was disarmed: no_stall_on_friction exempts a prompt that asked a question, "
        "no_diagnosis_without_fix needs a defect named, and no_future_tense_commitment cleared the "
        "closing promise because the class it named was a read the turn had already done",
    ),
    Case(
        "challenged_convention_pivot.jsonl",
        "challenged a choice you made",
        "'Why on earth would I want that convention?' answered with 'You wouldn't -- and there's "
        "no need to invent a new field, because ...' plus 160 words of rationale. One turn, both "
        "arms: the challenge was justified rather than acted on, and the concession was argued "
        "past",
    ),
    Case(
        "challenged_convention_insist.jsonl",
        "challenged a choice you made",
        "the third consecutive turn, in which the user names the shape itself -- 'Why do you insist "
        "on going My real point <em-dash> massive amount of prose that is never worth reading' -- "
        "and the turn does it again",
    ),
    Case(
        "challenged_convention_pivot_only.jsonl",
        "concession and then argued past it",
        "a concession opening a closer that changed nothing, with no detectable challenge in the "
        "prompt -- the arm that reaches the shape the challenge detector cannot see",
    ),
    Case(
        "challenged_convention_changed.jsonl",
        None,
        "the same challenge answered with the Edit that settles it -- must NOT halt, or making the "
        "change and saying why is punished identically to only saying why",
    ),
    Case(
        "challenged_convention_asked.jsonl",
        None,
        "'Explain why you start from 1.16.2 before we change it' -- an explicit ask, so explaining "
        "is the deliverable and gagging it would be the worse failure",
    ),
    Case(
        "challenged_convention_codebase_question.jsonl",
        None,
        "'Why does the engine park the disconnect until the next frame?' -- a third-person "
        "question about the binary, which this repo must always be able to answer at length",
    ),
    Case(
        "challenged_convention_short_reply.jsonl",
        None,
        "the same challenge answered in two words -- 'You wouldn't.' -- must NOT halt: the third "
        "conjunct is a wall of justification, and a plain concession is not one",
    ),
    Case(
        "challenged_convention_blocked.jsonl",
        None,
        "the same challenge beside a read that needs the game running and a daemon that needs "
        "sudo -- dependencies the agent cannot dissolve by working harder. Its first wording said "
        "'the guard refused the write' and ER-EFFECTS-NO-BLAME-DEFLECTION halted it correctly, "
        "which is worth knowing: a blocker has to be stated with the agent's own hand in it",
    ),
    Case(
        "narrated_action_rerun.jsonl",
        "narrating the action instead of having taken it",
        "the turn closes on 'Re-running it now without the cap.' -- the command was its own and "
        "unblocked, and it stopped to say so (the 2026-09-10 directive). The present participle is "
        "the gap: no_future_tense_commitment needs a first-person future opener, and a bare "
        "participial clause commits nobody",
    ),
    Case(
        "narrated_action_rebuild.jsonl",
        "narrating the action instead of having taken it",
        "'Rebuilding and relaunching now.' -- the one spelling ER-EFFECTS-NO-ZERO-INFORMATION-STOP "
        "also reaches, so cupcake returns both halts here. Kept as a fixture because it is one of "
        "the five verbatim closers, and the overlap is worth pinning rather than discovering",
    ),
    Case(
        "narrated_action_bringing_up.jsonl",
        "narrating the action instead of having taken it",
        "'Bringing it up to read the pointer chain live rather than guessing another offset:' -- a "
        "colon closing the clause, which is the announce-then-do shape with the tool call missing",
    ),
    Case(
        "narrated_action_diagnosable.jsonl",
        "narrating the action instead of having taken it",
        "'Making the empty read diagnosable ... :' -- a code-shaped verb the promissory closer's "
        "gerund list does not carry, in a turn that wrote nothing after it",
    ),
    Case(
        "narrated_action_dispatch.jsonl",
        "narrating the action instead of having taken it",
        "'Dispatching a subagent to enumerate the menu builder's rows properly, and unblocking you "
        "now ...:' -- the dispatch is one tool call away and the turn ended instead",
    ),
    Case(
        "narrated_action_trailing.jsonl",
        "narrating the action instead of having taken it",
        "the 2026-09-11 instance, verbatim: 'No - feature-gate er-quickload instead of forking it, "
        "and I'm starting on that now.' -- the announcement rides in after a comma instead of "
        "heading its own sentence, which is why the anchored first-person arm could not see it. "
        "Measured before it was widened: a fixture of that turn replayed through all 17 "
        "last_assistant_*.sh signals left every one of them silent",
    ),
    Case(
        "narrated_action_reported.jsonl",
        None,
        "the same participle carrying what came back -- 'Re-running it now - exit 0, 26 rows.' -- "
        "must NOT halt, or reporting an outcome in the present participle is punished identically "
        "to announcing one that does not exist",
    ),
    Case(
        "narrated_action_banner.jsonl",
        None,
        "the loud teardown/launch banner AGENTS.md mandates, closing on 'Bringing the new build up "
        "on the same character now.' -- must NOT halt. The banner is a required form: the user "
        "stops what they are doing and turns to a screen because of it, and a rule that made it "
        "unspeakable would take a safety announcement away to save a round trip",
    ),
    Case(
        "narrated_action_blocked.jsonl",
        None,
        "the same narration ending on a dependency the agent cannot dissolve -- no run is up and "
        "reading the chain needs the game running -- must NOT halt",
    ),
    Case(
        "narrated_action_mid_turn.jsonl",
        None,
        "'Making the empty read diagnosable ...:' as a mid-turn preamble before the Edit, with an "
        "ordinary closing report -- the correct shape, and the one this rule must never touch. "
        "wall_of_text.rego draws the same line in its own correction text",
    ),
    Case(
        "admission_with_defence.jsonl",
        "took the admission back in the same message",
        "the verbatim 2026-09-10 turn: 'I never drove an invasion myself all session' followed by "
        "'it is not firing right now because ...' and a table closing 'So that run did announce'. "
        "Measured on the real transcript slice, every one of the fifteen neighbouring signals was "
        "silent on it -- stall_on_friction saw the accusation but matched no admission of its own "
        "and its `acted` fact was true anyway, because the turn had run six Bash calls",
    ),
    Case(
        "admission_with_defence_rebuttal.jsonl",
        "did not ask you to check",
        "the rebuttal arm on its own: an accusation of not measuring, answered with an admission "
        "and then the ledger's counters. The user asserted a premise rather than asking, so the "
        "correction is uninvited",
    ),
    Case(
        "admission_only.jsonl",
        None,
        "the same admission with nothing after it -- must NOT halt. The admission is the part the "
        "directive says to reward, and a rule that charged it would teach agents to admit less",
    ),
    Case(
        "admission_with_substitute_work.jsonl",
        None,
        "the same admission followed by what the turn did instead -- must NOT halt, or reporting "
        "the substitute work is punished identically to excusing the omission",
    ),
    Case(
        "admission_correction_solicited.jsonl",
        None,
        "the same correction, asked for: 'Did that run announce at all? check the ledger'. Must "
        "NOT halt -- a factual answer someone requested is the deliverable, and gagging it would "
        "be the worse failure",
    ),
    Case(
        "admission_with_defence_blocked.jsonl",
        None,
        "the same shape where the reason is a dependency the agent cannot dissolve -- starting the "
        "daemon needs sudo -- must NOT halt, or AGENTS.md's own instruction to name the external "
        "blocker in one line becomes unspeakable",
    ),
    Case(
        "admission_with_defence_mid_turn.jsonl",
        None,
        "the admission and its excuse as a mid-turn preamble before the Edit, with an ordinary "
        "closing report -- the correct shape, and the one this rule must never touch",
    ),
    Case(
        "fix_claim_unproven.jsonl",
        "calling a change a fix without a run behind it",
        "the turn edits crates/er-quit-menu-core, builds the shell, and closes on 'That is the "
        "real fix.' -- the 2026-09-11 directive, after a turn of exactly that shape failed live "
        "on the next launch. The user: \"We are supposed to have a rego policy that stops you "
        "from saying 'fix' without runtime evidence\". Every neighbour was disarmed: "
        "no_unbacked_claim needs nothing to have been written and a file was, "
        "no_proof_without_observation reads only the word 'proven', and "
        "no_diagnosis_without_fix turns on whether a file changed",
    ),
    Case(
        "fix_claim_watcher_armed.jsonl",
        "calling a change a fix without a run behind it",
        "the 2026-09-13 escape, verbatim: three feature-gate predicates edited in the "
        "er-quickload crate, cross-compiled, launched, and closed on 'Fixed and relaunched as "
        "07f2729b' in the same message as the launch -- before the process had written a line. "
        "Nothing halted. The signal had emitted evidence=1 because the turn's last call was a "
        "Monitor armed on tail -F er-quickload-autoload-debug.log: a filename in a tool input, on "
        "a log nobody had read. Arming a watch is not reading a measurement, and this fixture is "
        "the whole reason that sentence is now in the classifier",
    ),
    Case(
        "fix_claim_with_evidence.jsonl",
        None,
        "the same sentence beside the log the run wrote -- must NOT halt, or reading the "
        "evidence is punished identically to skipping it",
    ),
    Case(
        "fix_claim_hedged.jsonl",
        None,
        "the same change closed with 'it is unverified ... nothing has run since the edit' -- "
        "must NOT halt. The honest hedge is the behaviour the directive asks for, and a rule "
        "that charged it would teach agents to claim harder rather than to hedge",
    ),
    Case(
        "fix_claim_host_only.jsonl",
        None,
        "the same word over a change to scripts/ with its selftest green -- must NOT halt: no "
        "crate that reaches a DLL was touched, so there is nothing a run could show either way",
    ),
    Case(
        "deferred_investigation.jsonl",
        "naming your own next investigative move instead of making it",
        "the turn reads one file and closes on 'the next place to look is profile_table_guard's "
        "rebuild of saveSlotsStates' -- one of six closers from a single 2026-09-12 session, none "
        "of them caught. The promissory arm needs a work gerund, the unread arm needs a file "
        "claimed to hold the answer, and no_described_next_step needs one of its own nouns followed "
        "by a copula, which 'the next place to look is' does not give it",
    ),
    Case(
        "deferred_investigation_edited.jsonl",
        None,
        "the same shape beside the Edit that changed the arming order -- must NOT halt, or saying "
        "where the work goes next is punished identically to stopping in front of it",
    ),
    Case(
        "deferred_investigation_blocked.jsonl",
        None,
        "the bisect that cannot take its next half until a live run reports, said in one line -- "
        "must NOT halt, or the blocker sentence AGENTS.md asks for becomes unspeakable",
    ),
    Case(
        "diagnosis_fixed_then_reported.jsonl",
        None,
        "the write, its green selftest, and only then a closing line naming what had been broken -- "
        "must NOT halt. `fixed` is positional and a closer has nothing after it, so this turn "
        "scored fixed=0 however much it wrote, and the guard charged it twice on 2026-09-19 for "
        "work it had already done. The unordered `edited` is what tells it apart from the turn "
        "that named a defect and changed nothing",
    ),
    Case(
        "diagnosis_committed_by_subagent.jsonl",
        None,
        "the 2026-10-02 false positive: a subagent committed the fix, and the closing message "
        "named the cause and the commit. This turn wrote nothing, so `edited` is 0, but the commit "
        "was made after the session began -- must NOT halt, or relaying a subagent's fix is "
        "punished identically to never making it",
    ),
    Case(
        "diagnosis_cites_pre_session_commit.jsonl",
        "the diagnosis is not the deliverable",
        "the same cause named, citing a commit that predates the session and changing nothing -- "
        "must still halt. An old hash in the prose is history, not a fix, so the time bound on "
        "`committed` is what keeps the true positive blocking",
    ),
    Case(
        "ask_without_receiver.jsonl",
        "You asked the user for something you have no receiver for",
        "the 2026-10-02 closer, verbatim: a Frida watcher backgrounded, then 'I need you to close "
        "the item list with B twice' with no Monitor armed. The watcher's exit is not the press, "
        "so nothing would have woken the agent when the user acted",
    ),
    Case(
        "ask_with_monitor.jsonl",
        None,
        "the same ask after a Monitor on the trace log started and has not ended -- must NOT "
        "halt, because the agent is now prepared to receive what it asked for",
    ),
    Case(
        "ask_subjective.jsonl",
        None,
        "'Does the font look right to you?' -- the answer is the user's typed reply, so no "
        "instrument is needed and the ask must NOT halt",
    ),
    Case(
        "ask_game_mechanic_you.jsonl",
        None,
        "the 2026-10-04 false positive, verbatim: a finished report saying the axes catch a roll "
        "'after you run in'. That 'you' is the player in a game mechanic, mid-sentence, not a "
        "request to the user -- must NOT halt",
    ),
    Case(
        "running_task_unexplained.jsonl",
        "Background tasks you started are still running",
        "the 2026-10-07 shape: a stage-log copier loop backgrounded for a push and still running "
        "when the turn closes on a report that never mentions it",
    ),
    Case(
        "running_task_stopped.jsonl",
        None,
        "the same copier stopped with TaskStop before the report -- must NOT halt",
    ),
    Case(
        "running_task_justified.jsonl",
        None,
        "the same copier still running, and the closing message names b31ydejza with the reason "
        "it is still needed -- must NOT halt",
    ),
    Case(
        "untailed_long_run.jsonl",
        "opened no kitty tab tailing its real output",
        "the 2026-10-19 shape: `gen-r3-weapon-boards.py --jobs 3` backgrounded for half an hour "
        "and no kitty-tail-task.py call after it, although AGENTS.md and the tail-build-task skill "
        "both said to open one",
    ),
    Case(
        "untailed_long_run_tailed.jsonl",
        None,
        "the same regeneration followed by `kitty-tail-task.py --boards`, which tails the shard "
        "logs where its progress goes -- must NOT halt",
    ),
    Case(
        "inline_user_command.jsonl",
        "hands the user a command as an inline shell block",
        "the 2026-10-13 shape: a commit, then the push the agent may not run handed over as a "
        "bash block. The user's directive is that such a command goes into its own kitty tab "
        "through scripts/kitty-user-command.py, pre-pasted under a comment saying why",
    ),
    Case(
        "inline_user_command_other_fences.jsonl",
        None,
        "a rust block, a text block and an inline `git push` in the same closer -- must NOT halt. "
        "Only a fence whose info string names a shell is a command block; code and log excerpts "
        "are not, and inline code is not a block",
    ),
    Case(
        "blame_deflection_bare.jsonl",
        "attributed an outcome to a mechanism",
        "'The hook refused the first write.' with no hand of the agent's anywhere -- the mechanism "
        "stands alone as the actor, which ER-EFFECTS-NO-BLAME-DEFLECTION exists to halt",
    ),
    Case(
        "blame_deflection_cause_first.jsonl",
        None,
        "'My first attempt used a python heredoc, ... The hook refused that write' -- the agent's "
        "own action is the sentence before the mechanism, so it is an account, not a deflection "
        "(bd er-effects-rs-454a). None of the signal's turn-wide ownership shapes match it, so "
        "only the local first-person-cause check in the policy lets it through. It must NOT halt",
    ),
    Case(
        "authorize_without_run_stamp.jsonl",
        "no run has stamped that commit",
        "the 2026-10-16 shape: a commit to a crate, then 'reply `authorize <sha>`' while the "
        "runtime-evidence gate refuses that sha -- the push would be denied for want of a run "
        "stamp the agent could have produced itself. The evidence verdict is pinned to exit 1",
        (("CUPCAKE_AUTHORIZE_EVIDENCE_CHECK", "false"),),
    ),
    Case(
        "authorize_without_run_stamp.jsonl",
        None,
        "the same message with the evidence verdict pinned to exit 0 (a DLL log names the sha, or "
        "the commits change no crate) -- must NOT halt, or asking for a push that will go through "
        "is punished identically to asking for one that will not",
        (("CUPCAKE_AUTHORIZE_EVIDENCE_CHECK", "true"),),
    ),
    Case(
        "clean.jsonl",
        None,
        "substantive work, no banned prose -- must NOT halt, or every turn wedges",
    ),
]


@dataclass(frozen=True)
class ContextCase:
    """A UserPromptSubmit case: the correction must arrive on the invisible additionalContext
    channel, or not arrive at all."""

    fixture: str
    # Distinctive substring the injected context must contain, or None when it must be absent.
    expect_context: str | None
    why: str


UPS_CASES = [
    ContextCase(
        "wall_of_text.jsonl",
        "MEASURED: your PREVIOUS answer ran to 4 paragraphs",
        "the previous answer was four paragraphs -- the next turn is told so, before it writes",
    ),
    ContextCase(
        "narration_between_tools.jsonl",
        None,
        "one-line preambles between tool calls are not a wall of text and must not be corrected",
    ),
    ContextCase(
        "clean.jsonl",
        None,
        "a clean previous turn gets the standing rule only, never a correction",
    ),
]

# The standing one-paragraph rule is unconditional, so its absence means the policy is not routed at
# all -- exactly the silent-inert failure this whole file exists to catch.
STANDING_RULE = "ONE PARAGRAPH."


def hook_command(event: str) -> list[str]:
    """The hook command Claude Code actually runs for `event`, read from settings.json so this test
    follows the real configuration instead of a copy that can drift away from it."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    for group in settings.get("hooks", {}).get(event, []):
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if "cupcake" in cmd:
                return cmd.replace("$CLAUDE_PROJECT_DIR", str(REPO_ROOT)).split()
    raise SystemExit(
        f"test-cupcake-stop-guards: no cupcake {event} hook found in .claude/settings.json"
    )


@functools.lru_cache(maxsize=1)
def head_commit() -> tuple[str, int]:
    """`HEAD`'s short hash and committer time, read once per run rather than once per attempt."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%h %ct", "HEAD"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.split()
    return out[0], int(out[1])


def substitute_head(text: str) -> str:
    """Fill the commit placeholders a fixture may carry, from the checkout's own `HEAD`.

    The committed-in-session fixtures need a real commit and a session timestamp on either side of
    it. A hash written into the fixture would stop resolving once its branch is squash-merged and
    pruned, so the fixture names `HEAD` instead:
      `@@HEAD@@`         the short hash of `HEAD`
      `@@BEFORE_HEAD@@`  an ISO timestamp one minute before the committer time of `HEAD`
      `@@AFTER_HEAD@@`   an ISO timestamp one minute after it
    """
    if "@@" not in text:
        return text
    short, epoch = head_commit()

    def iso(seconds: int) -> str:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return (
        text.replace("@@HEAD@@", short)
        .replace("@@BEFORE_HEAD@@", iso(epoch - 60))
        .replace("@@AFTER_HEAD@@", iso(epoch + 60))
    )


# Cupcake runs every signal under a deadline -- 5 s for the auto-discovered .cupcake/signals/*.sh,
# a constant in cupcake-core's rulebook.rs that nothing here can raise -- and a signal that misses
# it is dropped with this line on stderr. The policy that reads it then sees no signal and stays
# silent, so the hook prints `{}`: byte-for-byte the same output as an inert guard. Under a
# parallel check.sh at nice 10 / SCHED_IDLE beside cargo, a python signal that takes 0.3 s idle can
# take longer than that, which is how this test failed on two pushed tips in one pre-push run while
# passing alone on the same commit (2026-10-08). An evaluation that dropped a signal has not
# answered the question, so it is retried rather than read as a verdict.
SIGNAL_TIMEOUT_RE = re.compile(r"Signal '([^']+)' failed: Signal execution timed out")
# Attempts per case before a dropped-signal evaluation is reported as a failure of its own.
ATTEMPTS = 4
# The whole hook, per attempt. A hook that overruns it is the same non-answer as a dropped signal.
HOOK_TIMEOUT_SECONDS = 25


def leave_sched_idle() -> None:
    """Move this test, and so every hook and git call it spawns, to SCHED_OTHER at the inherited nice.

    check.sh drops every gate to SCHED_IDLE (scripts/lib/cpu-courtesy.sh), which runs a task only
    when no ordinary task wants the core. That is right for a gate that merely has to finish, and
    wrong for this one: what it measures is a hook that in production runs at ordinary priority
    under cupcake's 5 s signal deadline. Measured 2026-10-08 with 15 ordinary busy loops on this
    16-core machine: at SCHED_IDLE every single hook evaluation overran 25 s, so the test could
    produce no verdict at all, while at the same nice under SCHED_OTHER it passed. With 96 such
    loops the test's own `git log` for the fixture placeholders overran its 10 s too, which is why
    this applies to the whole process and not only to the hook subprocess. The nice value
    is kept, so the hook still loses every contested slice to the user's own work -- it is only no
    longer starved outright. RLIMIT_NICE decides whether an unprivileged process may leave
    SCHED_IDLE; where it may not, the hook stays idle and the retry in run_hook is what is left.
    """
    if not hasattr(os, "sched_getscheduler"):
        return
    try:
        if os.sched_getscheduler(0) == getattr(os, "SCHED_IDLE", -1):
            os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    except OSError:
        pass


@dataclass(frozen=True)
class HookResult:
    decision: dict
    raw: str
    stderr: str
    # Signals cupcake dropped on its deadline, or ["<whole hook>"] when the hook itself overran.
    dropped: list[str]


def stderr_tail(stderr: str, limit: int = 1200) -> str:
    tail = stderr.strip()[-limit:]
    return f"\n      cupcake stderr: {tail!r}" if tail else ""


def run_hook(
    fixture_name: str, event_name: str, argv: list[str], extra_env: tuple[tuple[str, str], ...] = ()
) -> HookResult | str:
    """Drive one fixture through the real hook until an evaluation runs every signal it asked for.

    Returns the first complete evaluation, or a failure message string. A failure message names
    the dropped signals when every attempt dropped one, so a signal that has become too slow for
    cupcake's deadline still fails this test -- as itself, not disguised as an inert guard."""
    last: HookResult | str = "no attempt ran"
    for attempt in range(1, ATTEMPTS + 1):
        last = run_hook_once(fixture_name, event_name, argv, extra_env)
        if isinstance(last, str) or not last.dropped:
            return last
        print(
            f"     retry {fixture_name} ({event_name}): attempt {attempt} dropped "
            f"{', '.join(last.dropped)} on cupcake's deadline -- no verdict",
            file=sys.stderr,
        )
    assert isinstance(last, HookResult)
    return (
        f"every one of {ATTEMPTS} evaluations dropped a signal on cupcake's deadline "
        f"({', '.join(last.dropped)}), so none of them is a verdict. A signal that cannot finish "
        f"inside 5 s on this machine is itself the defect to fix.{stderr_tail(last.stderr)}"
    )


def run_hook_once(
    fixture_name: str, event_name: str, argv: list[str], extra_env: tuple[tuple[str, str], ...] = ()
) -> HookResult | str:
    """One real cupcake hook invocation. Returns a HookResult, or a failure message string."""
    fixture = FIXTURES / fixture_name
    if not fixture.is_file():
        return f"missing fixture {fixture}"

    with tempfile.TemporaryDirectory(prefix="cupcake-stop-guard-") as tmp:
        # Signals discover the transcript via ~/.claude/projects/<cwd-with-slashes-as-dashes>/*.jsonl
        # (scripts/cupcake_turn_scan.latest_transcript). Point home at a throwaway tree holding only
        # this fixture, so the test never reads the live session transcript.
        slug = str(REPO_ROOT).replace("/", "-")
        tdir = Path(tmp) / ".claude" / "projects" / slug
        tdir.mkdir(parents=True)
        (tdir / "session.jsonl").write_text(
            substitute_head(fixture.read_text(encoding="utf-8")), encoding="utf-8"
        )

        env = {**os.environ, "HOME": tmp, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
        # A stub left in the caller's environment would decide the authorize-run-stamp verdict
        # for every case; only a case that names one gets one.
        env.pop("CUPCAKE_AUTHORIZE_EVIDENCE_CHECK", None)
        env.update(dict(extra_env))
        payload = {
            "session_id": f"stop-guard-{fixture_name}",
            "transcript_path": str(tdir / "session.jsonl"),
            "cwd": str(REPO_ROOT),
            "hook_event_name": event_name,
        }
        if event_name == "Stop":
            payload["stop_hook_active"] = False
        else:
            payload["prompt"] = "next question"
        try:
            proc = subprocess.run(
                argv, input=json.dumps(payload), capture_output=True, text=True, env=env,
                timeout=HOOK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return HookResult({}, "", err, [f"<whole hook, {HOOK_TIMEOUT_SECONDS}s>"])

    raw = proc.stdout.strip()
    try:
        decision = json.loads(raw) if raw else {}
    except ValueError:
        return f"unparseable cupcake output: {raw[:200]!r}{stderr_tail(proc.stderr)}"
    dropped = sorted(set(SIGNAL_TIMEOUT_RE.findall(proc.stderr)))
    return HookResult(decision, raw, proc.stderr, dropped)


def run_case(case: Case, argv: list[str]) -> str | None:
    """Returns None on pass, or a failure message."""
    outcome = run_hook(case.fixture, "Stop", argv, case.env)
    if isinstance(outcome, str):
        return outcome
    decision, raw, err = outcome.decision, outcome.raw, stderr_tail(outcome.stderr)

    reason = decision.get("reason", "")
    blocked = decision.get("decision") == "block"

    if case.expect_halt_text is None:
        if blocked:
            return f"expected NO halt (clean turn) but cupcake blocked: {reason[:160]!r}{err}"
        return None

    if not blocked:
        return (
            f"expected a HALT but cupcake returned {raw or '{}'!r}.\n"
            f"      The guard is INERT -- this is the exact 2026-07-17..2026-08-22 defect. Check that\n"
            f"      no rule in its path uses a builtin Cupcake's WASM runtime cannot execute\n"
            f"      (run: python3 scripts/check-cupcake-wasm-builtins.py).{err}"
        )
    if case.expect_halt_text not in reason:
        return f"halted, but reason lacked {case.expect_halt_text!r}: {reason[:200]!r}{err}"
    return None


def run_context_case(case: ContextCase, argv: list[str]) -> str | None:
    """Returns None on pass, or a failure message.

    Asserts on `hookSpecificOutput.additionalContext` -- the channel Claude Code turns into a
    `hook_additional_context` attachment, which the REPL filters out of the rendered message list.
    That invisibility is the point: the correction has to reach the model without the user reading
    it. A correction that came back as a `reason` instead would be printed to the user, so the shape
    of the output is as load-bearing as its content.
    """
    outcome = run_hook(case.fixture, "UserPromptSubmit", argv)
    if isinstance(outcome, str):
        return outcome
    decision, raw = outcome.decision, outcome.raw

    if decision.get("decision") == "block":
        return f"UserPromptSubmit must never block on answer length, but cupcake blocked: {raw[:200]!r}"

    context = decision.get("hookSpecificOutput", {}).get("additionalContext", "")
    if STANDING_RULE not in context:
        return (
            f"the standing one-paragraph rule is missing from additionalContext -- the policy is not\n"
            f"      routed or is inert. Got: {context[:200]!r}{stderr_tail(outcome.stderr)}"
        )

    if case.expect_context is None:
        if "MEASURED:" in context:
            return f"expected NO correction but one was injected: {context[context.index('MEASURED:'):][:200]!r}"
        return None

    if case.expect_context not in context:
        return f"correction missing {case.expect_context!r} from additionalContext: {context[:400]!r}"
    return None


def main() -> int:
    global ATTEMPTS
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", default=[], metavar="FIXTURE",
                        help="run only the cases for this fixture (repeatable)")
    parser.add_argument("--attempts", type=int, default=ATTEMPTS,
                        help="evaluations per case before a dropped signal fails it "
                             f"(default {ATTEMPTS}; 1 reproduces the pre-2026-10-08 behaviour)")
    args = parser.parse_args()
    ATTEMPTS = max(1, args.attempts)
    leave_sched_idle()

    if shutil.which("cupcake") is None:
        print("test-cupcake-stop-guards: SKIP (cupcake not installed)")
        return 0

    stop_cases = [c for c in CASES if not args.only or c.fixture in args.only]
    ups_cases = [c for c in UPS_CASES if not args.only or c.fixture in args.only]
    if args.only and not stop_cases and not ups_cases:
        print(f"test-cupcake-stop-guards: no case uses {args.only}", file=sys.stderr)
        return 2

    stop_argv = hook_command("Stop")
    ups_argv = hook_command("UserPromptSubmit")
    failures = 0
    for case in stop_cases:
        err = run_case(case, stop_argv)
        verdict = "halt" if case.expect_halt_text else "allow"
        if err:
            failures += 1
            print(f"FAIL [{verdict}] {case.fixture}: {case.why}\n      {err}", file=sys.stderr)
        else:
            print(f"ok   [{verdict}] {case.fixture}: {case.why}")

    for ctx_case in ups_cases:
        err = run_context_case(ctx_case, ups_argv)
        verdict = "correct" if ctx_case.expect_context else "quiet"
        if err:
            failures += 1
            print(f"FAIL [{verdict}] {ctx_case.fixture}: {ctx_case.why}\n      {err}", file=sys.stderr)
        else:
            print(f"ok   [{verdict}] {ctx_case.fixture}: {ctx_case.why}")

    if failures:
        print(f"\ntest-cupcake-stop-guards: {failures} failure(s)", file=sys.stderr)
        return 1
    print(
        f"test-cupcake-stop-guards: OK ({len(stop_cases)} Stop cases, {len(ups_cases)} UserPromptSubmit "
        f"cases, through the real hook commands)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
