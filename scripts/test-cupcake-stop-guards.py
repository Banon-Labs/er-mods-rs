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
    # A second session's transcript, written beside the hook's own and made newer than it. The
    # hook event names only the fixture, so nothing in the decoy may reach a verdict.
    decoy: str | None = None
    # Task ids to run as real processes while the hook runs, each started the way the harness starts
    # a Bash call (stdout on `<tasks dir>/<id>.output`) under this test's pid, which every hook is
    # pinned to as CLAUDE_PID. For the guards that read scripts/cupcake_session_tasks.py.
    live: tuple[str, ...] = ()


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
        "idle_hold_handed_reply_line.jsonl",
        None,
        "the 2026-11-11 false positive, verbatim: a 113-char note blocked on r9 (task b2dpglvu7) and "
        "a release-model agent, a blank line, then `authorize commit` alone on its line. Handing the "
        "reply phrase on its own line is blocked on the user, so it must NOT halt as a verbose pause",
    ),
    Case(
        "idle_hold_blocked_recap_no_phrase_line.jsonl",
        "paused while blocked on a background task",
        "the same blocked opener with a 600-char recap and no phrase line is still a verbose pause "
        "and must halt",
    ),
    Case(
        "idle_hold_phrase_mid_sentence_recap.jsonl",
        "paused while blocked on a background task",
        "'the guards commit needs the authorize commit reply' names the phrase inside a sentence and "
        "hands nothing, so the long recap after it must still halt",
    ),
    Case(
        "idle_hold_answer_accounting_running_task.jsonl",
        None,
        "the 2026-11-11 false positive, verbatim but for one sentence: the 735-char answer to the "
        "user's typed timelines question, rewritten after no_unexplained_running_tasks asked for 'r10 "
        "(bdj5lkbhk) is still running because ...'. Its only task reference is that sentence and the "
        "turn holding it was opened by the Stop-hook feedback, so it must NOT halt as a verbose "
        "blocked pause. 'The rule is now in `docs/agents/runtime-evidence.md`' reads "
        "'`docs/agents/runtime-evidence.md` describes it now', because no_markdown_rule rightly halts "
        "the original",
    ),
    Case(
        "idle_hold_waiting_on_both_after_feedback.jsonl",
        "paused while blocked on a background task",
        "the real hold from the same session, verbatim: after the same Stop-hook feedback, 'Two "
        "background runs are still going, and I'm waiting on both:' plus a table answers no one and "
        "must still halt",
    ),
    Case(
        "authority_agreement.jsonl",
        "authority-coded agreement",
        "turn opens with \"You're right\" -- banned agreement phrasing (2026-07-17 directive)",
    ),
    Case(
        "filed_not_started.jsonl",
        "You filed work this turn",
        "a mechanic filed as bd er-effects-rs-me5p and the turn closed on 'It waits for the homing and "
        "requirement-line changes' with nothing started and no blocker (2026-11-10 directive)",
    ),
    Case(
        "filed_and_started.jsonl",
        None,
        "the same filing followed by an Agent launch that starts it must NOT halt",
    ),
    Case(
        "filed_blocked.jsonl",
        None,
        "the same filing closed on 'blocked on task bpll5d7de' -- a running task it depends on -- must "
        "NOT halt",
    ),
    # 2026-11-20: five closing messages of 2026-11-19/20, verbatim, that parked a written fix behind
    # a running job. None ran `bd create`, so the filing shape let all of them through. User: "yes,
    # your quoted message and my last message are consistent, but inconsistent with yoru actions".
    Case(
        "filed_deferred_written_not_applied.jsonl",
        "parks a fix behind a running job",
        "'I've written the change but not applied it. ... I'll apply it when the kit run (b3fgttzcf) "
        "finishes', with saved sets as the excuse",
    ),
    Case(
        "filed_deferred_written_and_tested.jsonl",
        "parks a fix behind a running job",
        "'The fix for the time-left estimate is written and tested, and I'll apply it when the kit "
        "run (b3fgttzcf) ends'",
    ),
    Case(
        "filed_deferred_holding_patches.jsonl",
        "parks a fix behind a running job",
        "'I'm holding the timestamp, time-estimate and Golden Land patches until the read-back "
        "finishes'",
    ),
    Case(
        "filed_deferred_holding_saved_sets.jsonl",
        "parks a fix behind a running job",
        "'I'm holding it until the all-spells run (b4pvxbpqy) finishes, because changing the scripts "
        "mid-run would throw away that run's saved sets'",
    ),
    Case(
        "filed_deferred_table_cell.jsonl",
        "parks a fix behind a running job",
        "a table cell: 'the patch I'm applying once this run (b4pvxbpqy) ends'",
    ),
    Case(
        "filed_deferred_applied.jsonl",
        None,
        "'Applied the patch; the running job keeps its loaded code' reports the fix applied and must "
        "NOT halt",
    ),
    Case(
        "filed_deferred_authorize_commit.jsonl",
        None,
        "'This waits on your `authorize commit`' waits on the user's reply, not a job, and must NOT "
        "halt",
    ),
    Case(
        "filed_deferred_still_loading.jsonl",
        None,
        "'The read-back (b849hq3rz) is still loading' reports a job and parks no fix, and must NOT halt",
    ),
    Case(
        "negated_opener.jsonl",
        "something that did not happen",
        "'What does this mean to me?' answered by 'Nothing in the game changed.' -- a non-event "
        "nobody asked about ahead of the answer (2026-11-10 correction)",
    ),
    Case(
        "negated_opener_absence_asked.jsonl",
        None,
        "'What changed?' answered by 'Nothing changed in your spell kit' -- the question is about "
        "change, so the negation is the answer and must NOT halt",
    ),
    Case(
        "negated_opener_after_halt.jsonl",
        None,
        "the rewrite after a no_negated_opener halt: the newest prompt is the Stop hook's own "
        "feedback, no wh-question, so the guard cannot halt the same turn twice",
    ),
    Case(
        "unmeasured_estimate.jsonl",
        "sizes development work",
        "the 2026-11-09 21:16Z closer, verbatim: 'it's nearly free ... a few seconds on top of a kit "
        "run ... roughly 20 to 30 minutes', with nothing timed; the full runs then took 6,863 to "
        "12,716 s (2026-11-11 correction)",
    ),
    Case(
        "unmeasured_estimate_runtime.jsonl",
        "states no measured number",
        "the 2026-11-11 17:57Z closer: 'r10 is running ... (bdj5lkbhk, about 7 hours, ...)' -- a "
        "run-time forecast with no measured timing in the message",
    ),
    Case(
        "unmeasured_estimate_measured.jsonl",
        None,
        "'which leaves roughly 2 to 2.5 hours' beside the pace it projects from ('over the last 28 "
        "minutes ... about 99 s per set') -- a cited forecast must NOT halt",
    ),
    Case(
        "unmeasured_estimate_reported.jsonl",
        None,
        "the old forecast reported in quotes beside the measured run times -- reporting a forecast "
        "is not making one, and must NOT halt",
    ),
    Case(
        "markdown_rule.jsonl",
        "calls text in a markdown file",
        "the 2026-11-11 21:15Z closer: 'The rule is now in `docs/agents/runtime-evidence.md`.' -- a "
        "markdown entry called a rule (2026-11-11 correction: 'its logically inconsistent to use the "
        "word rule in reference to markdown files')",
    ),
    Case(
        "markdown_rule_carries.jsonl",
        "calls text in a markdown file (AGENTS.md)",
        "the 2026-11-10 22:11Z closer, verbatim: 'AGENTS.md now carries the same rule.'",
    ),
    Case(
        "markdown_rule_executable.jsonl",
        None,
        "a rego rule named by its file and a game's stacking rule, with AGENTS.md and a mechanics doc "
        "describing them -- must NOT halt",
    ),
    Case(
        "markdown_rule_quoted.jsonl",
        None,
        "the word itself in quotes ('calls markdown text a \"rule\"') beside 'AGENTS.md describes it' "
        "-- must NOT halt",
    ),
    Case(
        "correction_instance_first.jsonl",
        "Correction not encoded first",
        "the 2026-11-11 turn in session 5ec7b779: 'If I ever see a subagent that is finished but "
        "hasn't been closed ...' answered with ListAgents, a read-only check, a TaskStop and a "
        "SendMessage, and nothing that encodes the correction for future agents",
    ),
    Case(
        "correction_guard_agent_first.jsonl",
        None,
        "the next turn: 'I was not concerned about you closing that specific agent whatsoever' "
        "answered by launching the Stop-guard agent before any other action -- must NOT halt",
    ),
    Case(
        "correction_guard_edit_first.jsonl",
        None,
        "'Why did you stash them?' answered by a .cupcake edit first and the instance fix after it "
        "-- must NOT halt",
    ),
    Case(
        "correction_impossible.jsonl",
        None,
        "a correction whose closing message says why no guard can carry it -- must NOT halt",
    ),
    Case(
        "correction_nested_read_first.jsonl",
        None,
        "2026-11-16: a `sed -n \"$(python3 -c ...)\" | python3 -c ...` read, then the AGENTS.md entry, "
        "then the TaskStop -- the nested read only reads, so must NOT halt",
    ),
    Case(
        "correction_sed_in_place_first.jsonl",
        "Correction not encoded first",
        "`sed -i` on a repo script before the AGENTS.md entry acts on the instance first",
    ),
    Case(
        "correction_task_prompt.jsonl",
        None,
        "'run r10' is a task, not a correction, so an instance edit with no guard change must NOT halt",
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
        "backed_claim_earlier_turn.jsonl",
        None,
        "the 2026-10-19 false positive: a Write created the script one turn earlier, a task "
        "notification opened a new turn, an ls -l listed it, and the closer named its path -- must "
        "NOT halt. The signal used to look only at the judged turn's writes and refused it twice",
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
        "unfixed_unchecked_answer.jsonl",
        "a defect was left unfixed",
        "the 2026-10-18 closer, verbatim: a question answered with 'As far as I know ... I haven't "
        "checked that against the skill data yet' and 'I haven't fixed either', no file changed. "
        "It passed every Stop guard: the diagnosis arm needs a determiner beside a singular defect "
        "noun ('the crit and pairing problems' is neither) and exempts any prompt with a question "
        "mark. The admission of an unfixed defect is not an explanation, so `asked` cannot clear it",
    ),
    Case(
        "unfixed_answer_fixed_and_checked.jsonl",
        None,
        "the same question answered after the lookup and the edit, closing on the result as fact "
        "-- must NOT halt",
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
        "ask_authorize_commit_reply.jsonl",
        None,
        "the 2026-10-26 false positive, verbatim: 'I need your `authorize commit` reply before I "
        "can fix either PR'. The only answer is the user's typed reply phrase, so no instrument is "
        "needed and the ask must NOT halt",
    ),
    Case(
        "ask_past_tense_report.jsonl",
        None,
        "the 2026-10-28 false positive, verbatim: 'As soon as you opened your world, Seamless "
        "created your advertisement lobby, ...'. A bare simple-past verb after the temporal 'you' "
        "narrates what the user already did -- a report, not an ask, so it must NOT halt",
    ),
    Case(
        "ask_as_soon_as_you_open.jsonl",
        "You asked the user for something you have no receiver for",
        "the same clause in the present tense, 'As soon as you open your world, the probe records "
        "the lobby key', with nothing armed -- must still halt",
    ),
    Case(
        "ask_game_mechanic_you.jsonl",
        None,
        "the 2026-10-04 false positive, verbatim: a finished report saying the axes catch a roll "
        "'after you run in'. That 'you' is the player in a game mechanic, mid-sentence, not a "
        "request to the user -- must NOT halt",
    ),
    Case(
        "ask_authorize_commit_reply.jsonl",
        None,
        "the 2026-10-26 false positive, verbatim: 'I need your `authorize commit` reply before I "
        "can fix either PR'. The only answer is the user's typed reply phrase, so no instrument is "
        "needed and the ask must NOT halt",
    ),
    Case(
        "ask_need_it_authorize_reply.jsonl",
        None,
        "the 2026-11-10 replay false positive (transcript 9538a842, boundary 102), with the "
        "sentences other guards judge cut: 'each reply pays for one commit, so I need it twice, "
        "once per branch:' over a line holding only 'authorize commit'. 'It' is that typed reply, "
        "so no instrument is needed and the ask must NOT halt",
    ),
    Case(
        "ask_past_tense_report.jsonl",
        None,
        "the 2026-10-28 false positive, verbatim: 'As soon as you opened your world, Seamless "
        "created your advertisement lobby, ...'. A bare simple-past verb after the temporal 'you' "
        "narrates what the user already did -- a report, not an ask, so it must NOT halt",
    ),
    Case(
        "ask_as_soon_as_you_open.jsonl",
        "You asked the user for something you have no receiver for",
        "the same clause in the present tense, 'As soon as you open your world, the probe records "
        "the lobby key', with nothing armed -- must still halt",
    ),
    Case(
        "ask_game_mechanic_when_you.jsonl",
        None,
        "the 2026-11-10 false positive, verbatim: 'When you summon, the game sends peers just the "
        "three Moongrum IDs ...'. The main clause is the game doing something for any player, with "
        "no first person and no instrument -- a mechanic, not an ask, so it must NOT halt",
    ),
    Case(
        "ask_generic_you_conditional.jsonl",
        None,
        "the 2026-11-10 false positive, verbatim: a report opening 'Once you count what each one "
        "costs a real melee build, three spells are strictly worse ...'. That 'you' is anyone "
        "following the argument, and the main clause is a statement, not a request -- must NOT halt",
    ),
    Case(
        "ask_knowledge_question.jsonl",
        None,
        "the 2026-11-10 false positive, verbatim: a closer asking 'can you tell it apart from a "
        "Comet, Shard Spiral or Night Comet, and a tap from a charge?'. It asks what the user can "
        "perceive, and their typed reply is the answer, so no receiver is needed -- must NOT halt",
    ),
    Case(
        "ask_task_output_need.jsonl",
        None,
        "the 2026-11-10 false positive, verbatim: a status report whose r8 bullet names task "
        "b2mrgwhrl, still running, and says 'I need its numbers to recheck the four control pairs'. "
        "The agent is naming what it awaits from its own task, whose finished notification wakes "
        "it, and asks the user for nothing -- must NOT halt",
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
        "unclosed_finished_agent.jsonl",
        "Subagents you launched have finished and you have not closed them: a4d14f2d4d71a5ba0",
        "the 2026-11-11 shape: a background agent notified 'finished', the turn read its diff and "
        "closed on a report without a TaskStop naming a4d14f2d4d71a5ba0. The user: 'If I ever see a "
        "subagent that is finished but hasn't been closed, I always have VERY HIGH concerns that you "
        "consumed its results'",
    ),
    Case(
        "closed_finished_agent.jsonl",
        None,
        "the same agent closed with TaskStop after its diff was read; the stop is refused as 'not "
        "running (status: completed)', which is still the close -- must NOT halt",
    ),
    Case(
        "waiting_agent_not_closed.jsonl",
        None,
        "the agent's notification says it 'is waiting on its own background work': parked with jobs "
        "of its own, not finished -- must NOT halt",
    ),
    Case(
        "resumed_agent_running.jsonl",
        None,
        "the agent finished, its diff was read, and a SendMessage answered 'Resuming agent': it is "
        "running again until it next notifies -- must NOT halt",
    ),
    Case(
        "untailed_long_run.jsonl",
        "opened no kitty split tailing its real output",
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
        "unwatched_red_stage.jsonl",
        "nothing will wake you when one of its stages goes red",
        "the 2026-11-25 shape: `git push` backgrounded with a kitty split tailing it and no "
        "Monitor, so the policy stage's `RED (exit 1)` waited for the push to finish (user: 'not "
        "wait until all checks pass before addressing any one of the failing reds')",
    ),
    Case(
        "unwatched_red_stage_monitored.jsonl",
        None,
        "the same push with a Monitor following its output file through a filter that passes RED "
        "-- must NOT halt",
    ),
    Case(
        "unwatched_red_stage_finished.jsonl",
        None,
        "the same push after its completion notification, with no Monitor -- nothing left to "
        "watch, must NOT halt",
    ),
    Case(
        "untailed_agent.jsonl",
        "opened no kitty tab following its transcript: a505613c401ed9d43",
        "the 2026-11-11 shape: 'Speed up attack sets, round 2' launched in the background and no "
        "kitty-tail-agent.py call after it. The user: 'I don't understand how you could have a round "
        "running without me being able to see the output'",
    ),
    Case(
        "untailed_agent_tailed.jsonl",
        None,
        "the same launch followed by `kitty-tail-agent.py a505613c401ed9d43`, which opens a tab on "
        "the agent's transcript -- must NOT halt",
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
    Case(
        "clean.jsonl",
        None,
        "a concurrent session in the same project ended on an unkept promise and its transcript is "
        "the newest file there; this session's own turn is clean and must NOT halt. Measured "
        "2026-10-20: session f159d78f was halted quoting a sentence from session bd9e64a1, because "
        "the signals read the newest transcript in the directory instead of the hook event's",
        decoy="unexecuted_promise.jsonl",
    ),
    Case(
        "pause_names_blocker_unnamed.jsonl",
        "names nothing this session has running",
        "the 2026-11-11 directive ('communicate clearly to the user why you're paused (blocked because "
        "X shell is running)'): 'Blocked until the agent reports back.' while agent a5eed0011223344aa "
        "works names neither its id nor its description, so it halts and the reason lists the agent",
    ),
    Case(
        "pause_names_blocker_unnamed.jsonl",
        "bsub7 (owner unknown",
        "the same pause while a real process writes tasks/bsub7.output under the Claude Code pid: the "
        "process table half of the report reaches the halt reason through the real hook",
        live=("bsub7",),
    ),
    Case(
        "pause_names_blocker_named.jsonl",
        None,
        "the rewrite that names the agent ('the Speed up attack-set stage agent (a5eed00)') is a "
        "pause that says what it is blocked on, and must NOT halt",
        live=("bsub7",),
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
    # The prompt being submitted. A signal that classifies the prompt itself reads it from here.
    prompt: str = "next question"
    # A substring the injected context must not contain, for a case whose point is that one policy
    # stays quiet while the standing rules still arrive.
    forbid_context: str | None = None
    # Task ids to run as real processes while the hook runs (see Case.live).
    live: tuple[str, ...] = ()


UPS_CASES = [
    ContextCase(
        "pause_names_blocker_unnamed.jsonl",
        "bsub7 (owner unknown",
        "the 2026-11-11 directive ('find a way to self discover what shells you kick off'): with a real "
        "process writing tasks/bsub7.output under the Claude Code pid and agent a5eed0011223344aa "
        "working, the next turn is told both, measured, before it writes",
        live=("bsub7",),
    ),
    ContextCase(
        "pause_names_blocker_unnamed.jsonl",
        "a5eed0011223344aa 'Speed up attack-set stage' (running)",
        "the working agent alone, with no live shell, is still reported",
    ),
    ContextCase(
        "clean.jsonl",
        None,
        "nothing running: no report is injected",
        forbid_context="RUNNING NOW",
    ),
    ContextCase(
        "clean.jsonl",
        "CORRECTION FIRST: this message reads as a correction",
        "the 2026-11-11 directive: a prompt that reads as a correction ('every time I correct you, you "
        "fix the issue instead of fix it for future agents first') is told, before the turn starts, "
        "that its first change encodes the correction for future agents",
        prompt="My largest concern is that every time I correct you, you fix the issue instead of fix "
               "it for future agents first",
    ),
    ContextCase(
        "clean.jsonl",
        None,
        "a task prompt ('run r10') is not a correction and gets no correction-first line",
        prompt="run r10",
        forbid_context="CORRECTION FIRST:",
    ),
    ContextCase(
        "wall_of_text.jsonl",
        "MEASURED: your PREVIOUS answer ran to 4 paragraphs",
        "the previous answer was four paragraphs -- the next turn is told so, before it writes",
    ),
    # The paragraph rule has no Stop halt on purpose (wall_of_text.rego: a Stop verdict is printed to
    # the user and cannot unsend the answer), so the 2026-10-18 message is measured on the next prompt.
    # This pins that it is measured at all: two prose paragraphs around a table.
    ContextCase(
        "unfixed_unchecked_answer.jsonl",
        "MEASURED: your PREVIOUS answer ran to 2 paragraphs",
        "the 2026-10-18 answer: a paragraph, a table and a closing paragraph -- the table is "
        "structure, the two paragraphs are prose, and the next turn is told it ran to two",
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
    fixture_name: str,
    event_name: str,
    argv: list[str],
    extra_env: tuple[tuple[str, str], ...] = (),
    decoy: str | None = None,
    prompt: str = "next question",
    live: tuple[str, ...] = (),
) -> HookResult | str:
    """Drive one fixture through the real hook until an evaluation runs every signal it asked for.

    Returns the first complete evaluation, or a failure message string. A failure message names
    the dropped signals when every attempt dropped one, so a signal that has become too slow for
    cupcake's deadline still fails this test -- as itself, not disguised as an inert guard."""
    last: HookResult | str = "no attempt ran"
    for attempt in range(1, ATTEMPTS + 1):
        last = run_hook_once(fixture_name, event_name, argv, extra_env, decoy, prompt, live)
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
    fixture_name: str,
    event_name: str,
    argv: list[str],
    extra_env: tuple[tuple[str, str], ...] = (),
    decoy: str | None = None,
    prompt: str = "next question",
    live: tuple[str, ...] = (),
) -> HookResult | str:
    """One real cupcake hook invocation. Returns a HookResult, or a failure message string."""
    fixture = FIXTURES / fixture_name
    if not fixture.is_file():
        return f"missing fixture {fixture}"
    decoy_fixture = FIXTURES / decoy if decoy else None
    if decoy_fixture is not None and not decoy_fixture.is_file():
        return f"missing decoy fixture {decoy_fixture}"

    with tempfile.TemporaryDirectory(prefix="cupcake-stop-guard-") as tmp:
        # Signals read the transcript the hook event names (scripts/cupcake_turn_scan.hook_transcript).
        # It still lives where Claude Code puts it, ~/.claude/projects/<cwd-with-slashes-as-dashes>/,
        # under a throwaway home so the test never reads a live session transcript.
        slug = str(REPO_ROOT).replace("/", "-")
        tdir = Path(tmp) / ".claude" / "projects" / slug
        tdir.mkdir(parents=True)
        (tdir / "session.jsonl").write_text(
            substitute_head(fixture.read_text(encoding="utf-8")), encoding="utf-8"
        )
        if decoy_fixture is not None:
            other = tdir / "other-session.jsonl"
            other.write_text(
                substitute_head(decoy_fixture.read_text(encoding="utf-8")), encoding="utf-8"
            )
            later = (tdir / "session.jsonl").stat().st_mtime + 60
            os.utime(other, (later, later))

        # CLAUDE_PID is this test's own pid: scripts/cupcake_session_tasks.py reports the processes
        # under it, so a Claude Code session this test runs under never reaches a verdict, and a
        # case's `live` processes, started here, are the only ones it sees.
        env = {**os.environ, "HOME": tmp, "CLAUDE_PROJECT_DIR": str(REPO_ROOT), "CLAUDE_PID": str(os.getpid())}
        # The repo's guards are under test, not the user's global ones. The throwaway home was
        # meant to keep ~/.config/cupcake out too, but the hook resolves the global root as
        # ${CUPCAKE_GLOBAL_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/cupcake}, and a desktop session
        # exports XDG_CONFIG_HOME, so the user's global policies loaded anyway. A global Stop guard
        # added there on 2026-11-16 (background_task_needs_tail) then halted first on every fixture
        # with a backgrounded task, and five cases failed on a guard this repo does not ship. An
        # empty global root with a `policies/claude` directory is what the hook needs to evaluate
        # quietly: a missing one makes it print a "global policies are NOT loaded" warning instead.
        global_root = Path(tmp) / "global-cupcake"
        (global_root / "policies" / "claude").mkdir(parents=True)
        env["CUPCAKE_GLOBAL_CONFIG"] = str(global_root)
        # A stub left in the caller's environment would decide the authorize-run-stamp verdict
        # for every case; only a case that names one gets one.
        env.pop("CUPCAKE_AUTHORIZE_EVIDENCE_CHECK", None)
        # Before the empty global root above, the hook also evaluated the global tree
        # (~/.config/cupcake), and its background_task_needs_tail halts only when KITTY_WINDOW_ID
        # is set, so a suite started
        # from a kitty shell got a different verdict from the same suite run anywhere else: seven
        # cases failed on 2026-11-16 in kitty and passed outside it. Every fixture here is judged
        # as a turn outside kitty, and no hook reaches the user's terminal. A case that needs
        # kitty names KITTY_WINDOW_ID in its `env`.
        for name in [n for n in env if n.startswith("KITTY_")]:
            del env[name]
        env.update(dict(extra_env))
        tasks = Path(tmp) / "tasks"
        tasks.mkdir()
        live_procs = []
        for tid in live:
            with (tasks / f"{tid}.output").open("w") as out:
                live_procs.append(subprocess.Popen(["sleep", "60"], stdout=out, stderr=out))
        payload = {
            "session_id": f"stop-guard-{fixture_name}",
            "transcript_path": str(tdir / "session.jsonl"),
            "cwd": str(REPO_ROOT),
            "hook_event_name": event_name,
        }
        if event_name == "Stop":
            payload["stop_hook_active"] = False
        else:
            payload["prompt"] = prompt
        try:
            proc = subprocess.run(
                argv, input=json.dumps(payload), capture_output=True, text=True, env=env,
                timeout=HOOK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return HookResult({}, "", err, [f"<whole hook, {HOOK_TIMEOUT_SECONDS}s>"])
        finally:
            for p in live_procs:
                p.kill()
                p.wait()

    raw = proc.stdout.strip()
    try:
        decision = json.loads(raw) if raw else {}
    except ValueError:
        return f"unparseable cupcake output: {raw[:200]!r}{stderr_tail(proc.stderr)}"
    dropped = sorted(set(SIGNAL_TIMEOUT_RE.findall(proc.stderr)))
    return HookResult(decision, raw, proc.stderr, dropped)


def run_case(case: Case, argv: list[str]) -> str | None:
    """Returns None on pass, or a failure message."""
    outcome = run_hook(case.fixture, "Stop", argv, case.env, case.decoy, live=case.live)
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
    outcome = run_hook(case.fixture, "UserPromptSubmit", argv, prompt=case.prompt, live=case.live)
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

    if case.forbid_context is not None and case.forbid_context in context:
        return f"context must not carry {case.forbid_context!r}: {context[:400]!r}"

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
