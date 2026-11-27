#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_idle_hold`.

The signal script scans the last-completed assistant turn of the session transcript and returns a
tagged marker:
  * IDLEHOLD:<phrase>  -- an unjustified idle/hold announcement while a background task runs
  * ""                 -- clean (no hold language, or the hold is justified / accompanied by
                          substantive non-overlapping work / blocked on the user)

We drive it against crafted transcript JSONL under a temporary home so the script's
`~/.claude/projects/<cwd-key>/*.jsonl` discovery resolves to our fixture, then assert the tag.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_idle_hold.sh"

PROJECT_DIR = "/fake/project/er-quickload"


def user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def tool_result() -> dict:
    """A tool-result carrier user event -- must not split the assistant turn."""
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}


def assistant_text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def assistant_bash(command: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]},
    }


def assistant_edit() -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/y.rs"}}]
        },
    }


def assistant_agent() -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Agent", "input": {"prompt": "go"}}]},
    }


def run_signal(events: list[dict], *, event_name: str | None = None, live: tuple[str, ...] = (),
               subagents: dict[str, list[dict]] | None = None) -> str:
    """Write events to a fixture transcript under a temp home and return the signal's stdout.

    `event_name` goes into the hook event as `hook_event_name`; the check that a pause names what
    it is blocked on runs only on "Stop". `live` names task ids to run as real processes for the
    duration, each with stdout on `<home>/tasks/<id>.output` the way the harness starts a Bash call,
    under this test's own pid, which the signal is pinned to as `CLAUDE_PID`; so the processes of a
    Claude Code session this test may run under are never read. `subagents` writes
    `<session>/subagents/agent-<id>.jsonl` beside the transcript."""
    with tempfile.TemporaryDirectory() as home:
        key = PROJECT_DIR.replace("/", "-")
        tdir = Path(home) / ".claude" / "projects" / key
        tdir.mkdir(parents=True, exist_ok=True)
        with (tdir / "session.jsonl").open("w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
        for aid, sub_events in (subagents or {}).items():
            sub = tdir / "session" / "subagents"
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"agent-{aid}.jsonl").write_text(
                "".join(json.dumps({**ev, "isSidechain": True}) + "\n" for ev in sub_events), encoding="utf-8")
        hook_event = {"transcript_path": str(tdir / "session.jsonl"), "session_id": "session"}
        if event_name:
            hook_event["hook_event_name"] = event_name
        procs = []
        tasks = Path(home) / "tasks"
        tasks.mkdir()
        try:
            for tid in live:
                with (tasks / f"{tid}.output").open("w") as out:
                    procs.append(subprocess.Popen(["sleep", "30"], stdout=out, stderr=out))
            proc = subprocess.run(
                ["bash", str(SIGNAL)],
                input=json.dumps(hook_event),
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                env={**os.environ, "HOME": home, "CLAUDE_PROJECT_DIR": PROJECT_DIR,
                     "CLAUDE_PID": str(os.getpid())},
            )
        finally:
            for p in procs:
                p.kill()
                p.wait()
        return proc.stdout.strip()


CASES_RUN = 0


def expect(name: str, events: list[dict], predicate, describe: str, **run) -> None:
    global CASES_RUN
    CASES_RUN += 1
    out = run_signal(events, **run)
    if not predicate(out):
        raise AssertionError(f"{name}: {describe} (got {out!r})")


def main() -> int:
    # (1) A bare holding announcement with no work -> IDLEHOLD.
    expect(
        "hold-alone",
        [user("Kick off the RE."), assistant_text("I'm holding for the RE subagent to finish.")],
        lambda o: o.startswith("IDLEHOLD:"),
        "expected IDLEHOLD for a bare hold announcement with no work",
    )

    # (2a) Same hold + a substantive Bash tool_use in the turn -> not flagged.
    expect(
        "hold-plus-substantive-bash",
        [
            user("Kick off the RE."),
            assistant_text("I'm holding for the RE subagent. Meanwhile, checking the disassembly."),
            tool_result(),
            assistant_bash("cargo xwin build --release --target x86_64-pc-windows-msvc"),
        ],
        lambda o: o == "",
        "expected empty when the hold turn also runs substantive work",
    )

    # (2b) Same hold + an Edit tool_use in the turn -> not flagged.
    expect(
        "hold-plus-edit",
        [
            user("Kick off the RE."),
            assistant_text("Standing by for the subagent; prepping the next fix now."),
            tool_result(),
            assistant_edit(),
        ],
        lambda o: o == "",
        "expected empty when the hold turn also edits a file",
    )

    # (2c) Same hold + an Agent tool_use in the turn -> not flagged.
    expect(
        "hold-plus-agent",
        [
            user("Kick off the RE."),
            assistant_text("I'll wait for the build. Launching a parallel investigation."),
            tool_result(),
            assistant_agent(),
        ],
        lambda o: o == "",
        "expected empty when the hold turn also launches a subagent",
    )

    # (3) Same hold + justification prose -> not flagged.
    expect(
        "hold-plus-justification",
        [
            user("Kick off the RE."),
            assistant_text(
                "I'm holding for the RE subagent. I would normally have prepped the next fix "
                "but the fix depends entirely on the subagent's finding, so there is nothing to pull forward."
            ),
        ],
        lambda o: o == "",
        "expected empty when the hold is accompanied by justification prose",
    )

    # (4) A status-peek-only Bash (tail/wc of a log) does not count as substantive -> still flagged.
    expect(
        "hold-plus-status-peek-only",
        [
            user("Kick off the RE."),
            assistant_text("I'm holding for the subagent; just peeking at the log."),
            tool_result(),
            assistant_bash("tail -n 40 target/run.log | wc -l"),
        ],
        lambda o: o.startswith("IDLEHOLD:"),
        "expected IDLEHOLD when the only Bash call is a status/log peek",
    )

    # (5) No holding language at all -> clean.
    expect(
        "no-hold-language",
        [
            user("What is the offset?"),
            assistant_text("The offset is 0x40; I verified it against the disassembly."),
        ],
        lambda o: o == "",
        "expected empty for ordinary technical prose with no hold language",
    )

    # (6) A wait genuinely blocked on the user is legitimate -> not flagged.
    expect(
        "wait-blocked-on-user",
        [
            user("I'll drive the menu."),
            assistant_text("I'll wait for the user to confirm the load finished before I continue."),
        ],
        lambda o: o == "",
        "expected empty when the wait is blocked on the user",
    )

    # (7) A hold phrase only inside a double-quoted span -> stripped -> clean.
    expect(
        "quoted-only-hold",
        [
            user("Explain the ban."),
            assistant_text('The phrase "I\'m holding" is banned unless the turn also does real work.'),
        ],
        lambda o: o == "",
        "expected empty when the hold phrase appears only inside double quotes",
    )

    # (8) Hold in a non-final block of the turn (a later clean block must not mask it) -> detected.
    expect(
        "hold-in-nonfinal-block",
        [
            user("Kick off the RE."),
            assistant_text("I'm holding for the subagent."),
            assistant_text("The subagent will report the function signature soon."),
        ],
        lambda o: o.startswith("IDLEHOLD:"),
        "expected IDLEHOLD from a whole-turn scan when the hold is not the last block",
    )

    # (9) Interrupted turn: a new user prompt after the hold -> the prior turn is still detected.
    expect(
        "interrupted-turn",
        [
            user("Kick off the RE."),
            assistant_text("Standing by for the results."),
            user("Actually, also do Y."),
        ],
        lambda o: o.startswith("IDLEHOLD:"),
        "expected IDLEHOLD on the prior (interrupted) turn",
    )

    # ---- VERBOSEPAUSE (tightened rule 2026-07-17) -------------------------------------------------

    # A long, multi-topic status dump used to end a paused turn (no substantive work, headings +
    # bullets + numbered list + >450 chars, not blocked on the user).
    long_pause_message = (
        "The build subagent is still compiling; here is where things stand while it runs.\n\n"
        "Progress so far:\n"
        "- Resolved the SpEffect call list against SpEffectParam and confirmed all 42 rows.\n"
        "- Rewrote the title-cover oracle to read loadstate the way the game does.\n"
        "- Verified the +0x35 field stalls below 0x0a in the stale dump.\n\n"
        "Next steps once the build lands:\n"
        "1. Re-run the direct offline probe with the freshly built DLL.\n"
        "2. Capture the loading-screen-portrait moment and pixel-diff the input extremes.\n"
        "3. Update the semaphore-progress teardown if the switch is still non-deterministic.\n\n"
        "I expect the compile to finish shortly and will pick this back up then."
    )

    # (10) Pure pause + long message, not blocked on the user -> VERBOSEPAUSE.
    expect(
        "verbose-pure-pause-long",
        [user("Kick off the build."), assistant_text(long_pause_message)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "expected VERBOSEPAUSE for a pure pause whose message is long/multi-topic",
    )

    # (11) Pure pause + short, precise blocked note (no idle phrase) -> clean.
    expect(
        "verbose-pure-pause-short",
        [
            user("Kick off the build."),
            assistant_text(
                "Blocked on the release build finishing; nothing non-overlapping remains, "
                "so I'll resume the moment it returns."
            ),
        ],
        lambda o: o == "",
        "expected empty for a short, precise blocked-pause note",
    )

    # (12) long message but the turn also does substantive Edit work -> clean (may report results).
    expect(
        "verbose-long-with-edit",
        [
            user("Kick off the build."),
            assistant_text(long_pause_message),
            tool_result(),
            assistant_edit(),
        ],
        lambda o: o == "",
        "expected empty when a long message accompanies substantive Edit work",
    )

    # (12b) long message but the turn also launches a subagent -> clean.
    expect(
        "verbose-long-with-agent",
        [
            user("Kick off the build."),
            assistant_text(long_pause_message),
            tool_result(),
            assistant_agent(),
        ],
        lambda o: o == "",
        "expected empty when a long message accompanies an Agent launch",
    )

    # (13) long message that is genuinely blocked on the user -> exempt -> clean.
    expect(
        "verbose-long-blocked-on-user",
        [
            user("Let's validate the autoload."),
            assistant_text(
                "I've reached the point where only a live run can settle this, and that run needs "
                "your hands on the launcher.\n\n"
                "Here is exactly what I need you to do:\n"
                "- Start Steam and log in.\n"
                "- Run ~/Elden/launch.sh with the quicksave profile and the default APPDATA save.\n"
                "- Tell me what the loading-screen portrait shows when PRESS ANY BUTTON appears.\n\n"
                "I cannot proceed without that observation because the semaphore only asserts on a "
                "real character load, so I am blocked on you until you can run it."
            ),
        ],
        lambda o: o == "",
        "expected empty for a long message that is genuinely blocked on the user",
    )

    # (14) long message but only a status-peek Bash (tail) -> still a pure pause -> VERBOSEPAUSE.
    expect(
        "verbose-long-with-status-peek",
        [
            user("Kick off the build."),
            assistant_text(long_pause_message),
            tool_result(),
            assistant_bash("tail -n 40 target/build.log"),
        ],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "expected VERBOSEPAUSE when a long pause turn's only Bash is a status peek",
    )

    # (15) regression -- a long prose answer is not a pause. Nothing is running, the message announces
    # no hold, the user simply asked a question that prose answers. Before 2026-08-22 this fired
    # VERBOSEPAUSE, which halted three consecutive real answers -- twice on the corrective rewrite the
    # wall_of_text guard demanded back when it still halted at Stop, a turn that has no tool_use by
    # construction. Length alone is wall_of_text's jurisdiction, not this rule's.
    # >450 chars on purpose: at 411 the length heuristic would not fire and case (15) would pass
    # trivially, testing nothing. This is a plain answer -- no idle phrasing, no pending-work
    # phrasing, nothing running in the transcript.
    answer_no_pending = (
        "Yes -- intended. The build I launched before you asked for the relaunch already contained "
        "the fade fix, I said so in the handoff and gave you the two Escape presses precisely so you "
        "would try to reproduce it; the relaunch you asked for was the same DLL, byte-identical, so "
        "what you just failed to reproduce is the fixed build behaving as designed rather than a "
        "lucky run that happened to miss the timing window. The oracle table in the commit records "
        "the press landing inside the fade and the hold being refused eight times, which is why the "
        "absence of the symptom counts as proof here rather than as a run that simply missed it."
    )
    expect(
        "verbose-long-prose-answer-nothing-pending",
        [
            user("Tell me before you investigate if you intended for me to notice it was fixed."),
            assistant_text(answer_no_pending),
        ],
        lambda o: o == "",
        "expected empty for a long prose ANSWER with nothing pending -- it is not a pause",
    )

    # (16) The same answer with a background job live. Until 2026-11-11 this was the control that a
    # live job turns any long message into a pause, and only a typed question was excused (2026-10-04).
    # A typed request is excused too now: replayed over the 40 newest transcripts, every message
    # halted this way while answering a typed request ("steam did not start", "I need to know what you
    # were witing for first", "That sounds like the crash we are looking at is a red herring") was an
    # answer, not a turn stopping to wait. The job is launched in an earlier turn -- launching it in
    # this one would itself be substantive work and exempt the turn for a different reason.
    bx1_live = [
        user("Kick off the build."),
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_bg",
                        "name": "Bash",
                        "input": {"command": "cargo build", "run_in_background": True},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bg",
                        "content": "Command running in background with ID: bx1",
                    }
                ]
            },
        },
    ]
    expect(
        "verbose-long-prose-answer-to-request-with-live-background-job-is-clean",
        bx1_live
        + [
            user("Tell me before you investigate if you intended for me to notice it was fixed."),
            assistant_text(answer_no_pending),
        ],
        lambda o: o == "",
        "a long answer to a typed request is not a pause because a background job is live",
    )
    # (16b) Control: a prompt whose subject is the running job is answered from the job, so the same
    # message is a verbose pause.
    expect(
        "verbose-long-answer-to-prompt-naming-running-task-flags",
        bx1_live
        + [user("Tell me what bx1 is doing and whether you intended me to notice the fix."),
           assistant_text(answer_no_pending)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "a prompt that names the running task's id is about that task, not an unrelated question",
    )

    # ---- 2026-10-04 regression: a delivered final report is not a pause ---------------------------
    #
    # The incident: a selftest was run in the background, its completion arrived, the agent peeked the
    # finished output with one `tail` and wrote its final answer. VERBOSEPAUSE halted it, and then
    # halted each shorter rewrite, four times in a row, with nothing running. The notification had
    # been delivered mid-turn as a `queued_command` attachment, which the scanner did not read, so the
    # finished task stayed "live" for the rest of the window.
    report = answer_no_pending  # >450 chars, no hold or pending-work phrasing

    def bg_launch(tool_id: str, task_id: str, *, foreground_moved: bool = False) -> list[dict]:
        ack = (
            "Command did not complete within its 120s timeout and was moved to the background "
            f"(ID: {task_id}). Output is being written to: /tmp/x/{task_id}.output"
            if foreground_moved
            else f"Command running in background with ID: {task_id}. Output is being written to: "
            f"/tmp/x/{task_id}.output"
        )
        inp = {"command": "python3 scripts/x.py --selftest > /tmp/x/st.txt"}
        if not foreground_moved:
            inp["run_in_background"] = True
        return [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": inp}]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": ack}]
                },
            },
        ]

    def notification(tool_id: str, task_id: str, status: str = "completed") -> str:
        return (
            f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>{tool_id}</tool-use-id>\n"
            f"<output-file>/tmp/x/{task_id}.output</output-file>\n<status>{status}</status>\n"
            f"<summary>Background command completed (exit code 0)</summary>\n</task-notification>"
        )

    def queued_notification(tool_id: str, task_id: str) -> list[dict]:
        """The two carriers a mid-turn completion arrives in -- never a user event."""
        text = notification(tool_id, task_id)
        return [
            {"type": "queue-operation", "operation": "enqueue", "content": text},
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "prompt": text,
                    "commandMode": "task-notification",
                },
            },
        ]

    def peek_turn() -> list[dict]:
        return [assistant_bash("tail -3 /tmp/x/st.txt"), tool_result()]

    stop_feedback = user(
        "Stop hook feedback:\nYou paused while blocked on a background task but wrote a long "
        "message (1534 chars)."
    )

    # (18) The incident shape: the earlier task's completion came only as a queued_command
    # attachment, the current one as a user-event notification, the turn peeked and reported.
    incident = (
        [user("Use the metric for gear with synergy.")]
        + bg_launch("toolu_a", "ba1")
        + queued_notification("toolu_a", "ba1")
        + bg_launch("toolu_b", "bb2", foreground_moved=True)
        + [assistant_text("Waiting on the selftest re-run (task bb2).")]
        + [user(notification("toolu_b", "bb2"))]
        + peek_turn()
        + [assistant_text(report)]
    )
    expect(
        "report-after-notification-and-peek",
        incident,
        lambda o: o == "",
        "a final report after every task finished (one via a queued_command attachment) and a "
        "status peek must not be a VERBOSEPAUSE",
    )

    # (19) The turn after Stop-hook feedback has no tool use by construction; with nothing running
    # its rewrite must go through.
    expect(
        "report-after-stop-feedback",
        incident + [stop_feedback, assistant_text(report)],
        lambda o: o == "",
        "the rewrite after Stop-hook feedback, with nothing running, must not be a VERBOSEPAUSE",
    )

    # (20) A task with no completion notice, ended by a successful TaskStop of its task id.
    stopped = (
        [user("Run the selftest.")]
        + bg_launch("toolu_c", "bc3")
        + [user("Stop that and tell me what you found.")]
        + [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_stop", "name": "TaskStop",
                         "input": {"task_id": "bc3"}}
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_stop",
                         "content": '{"message":"Successfully stopped task: bc3"}'}
                    ]
                },
            },
            assistant_text(report),
        ]
    )
    expect(
        "report-after-taskstop",
        stopped,
        lambda o: o == "",
        "a task ended by a successful TaskStop is not live work",
    )

    # (21) Control: the ban stays. A notification for a different task does not close the one still
    # running, and a long text-only turn over it is a VERBOSEPAUSE.
    expect(
        "verbose-long-other-task-still-live",
        [user("Run both.")]
        + bg_launch("toolu_d", "bd4")
        + bg_launch("toolu_e", "be5")
        + [user(notification("toolu_d", "bd4"))]
        + peek_turn()
        + [assistant_text(report)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "a long message while another background task is still running must flag",
    )

    # (22) Control: a foreground Bash the harness moved to the background is live work until its
    # notification lands.
    expect(
        "verbose-long-moved-to-background-still-live",
        [user("Run the selftest.")]
        + bg_launch("toolu_f", "bf6", foreground_moved=True)
        + [user("Anything yet?"), assistant_text(report)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "a long message while a moved-to-background Bash is still running must flag",
    )

    # (22b) 2026-10-19: a foreground Bash whose output quotes the launch phrases -- here, `sed` printing
    # BACKGROUND_LAUNCH_RE out of cupcake_turn_scan.py -- is a finished call, not a launch. It was booked
    # as "Bash moved to the background", and every later long message halted as a verbose pause.
    expect(
        "verbose-long-after-output-quoting-launch-phrases-is-clean",
        [
            user("Read the scanner."),
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "toolu_sed", "name": "Bash",
                                         "input": {"command": "sed -n 350,370p scripts/cupcake_turn_scan.py"}}]},
            },
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_sed", "content":
                    "BACKGROUND_LAUNCH_RE = re.compile(\n    r\"async agent launched|agentId:\"\n"
                    "    r\"|command running in background|running in background with id\"\n"
                    "    r\"|moved to the background\"\n    r\"|you will be notified when it completes\","}]},
            },
            assistant_text(report),
        ],
        lambda o: not o.startswith("VERBOSEPAUSE:"),
        "output that merely quotes the harness's launch phrases is not a running job",
    )

    # (22c) 2026-10-19: a closing report that describes a fixed bug in words -- "booked as a job still
    # running" -- with nothing live and a notification-opened turn. It describes; it does not wait.
    expect(
        "verbose-long-report-describing-still-running-is-clean",
        [user(notification("toolu_h", "bh8")),
         assistant_text(report + " The scanner booked that call as a job still running, so every "
                        "later turn looked like a job still running.")],
        lambda o: not o.startswith("VERBOSEPAUSE:"),
        "'a job still running' in a description is not a claim that something is running now",
    )
    expect(
        "verbose-long-present-tense-still-compiling-flags",
        [user(notification("toolu_h", "bh8")),
         assistant_text(report + " The build is still compiling.")],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "'<it> is still compiling' remains a pause",
    )

    # (23) Control: a refused TaskStop (wrong id) leaves the task live. The prompt asks after the job
    # ("done yet?"), so the reply is judged as a pause rather than as an answer to the user (see 16).
    expect(
        "verbose-long-failed-taskstop-still-live",
        [user("Run the selftest.")]
        + bg_launch("toolu_g", "bg7")
        + [
            user("Is it done yet? Stop it if not."),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_stop2", "name": "TaskStop",
                         "input": {"task_id": "bg7"}}
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_stop2", "is_error": True,
                         "content": "No task found with ID: bg7"}
                    ]
                },
            },
            assistant_text(report),
        ],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "a TaskStop that errored must not count as ending the task",
    )

    # ---- 2026-11-11: a reply phrase handed on a line of its own ------------------------------------
    #
    # With r9 (a backgrounded Bash, task b2dpglvu7) and a release-model subagent still live, the turn
    # closed on the 113-char note below -- one sentence, a blank line, then the phrase alone on its
    # line, the way this repo hands reply phrases -- and VERBOSEPAUSE halted it as a long message. The
    # blank line made it two paragraphs, and the phrase line did not read as waiting on the user. The
    # same turn's earlier 966-char closing message, a finished survey ending on the same phrase line,
    # halted too.
    r9_live = bg_launch("toolu_r9", "b2dpglvu7")
    handed_note = (
        "Blocked on r9 (task b2dpglvu7) and the release-model agent; the guards commit needs your "
        "reply:\n\nauthorize commit"
    )
    assert len(handed_note) == 113, len(handed_note)
    expect(
        "handed-reply-line-blocked-note-is-clean",
        [user("Run r9.")] + r9_live + [user("Fix the guards."), assistant_text(handed_note)],
        lambda o: o == "",
        "the 113-char note handing `authorize commit` on its own line is blocked on the user",
    )
    expect(
        "handed-sha-reply-line-is-clean",
        [user("Run r9.")] + r9_live
        + [user("Fix the guards."),
           assistant_text("Blocked on r9 (task b2dpglvu7); the push needs your reply:\n\n"
                          "`authorize 1f3a9c2e7b4d`")],
        lambda o: o == "",
        "a backticked `authorize <sha>` on its own line is blocked on the user",
    )

    survey_then_phrase = (
        "The survey of the Stop fixtures is finished. Of the forty-one fixtures, thirty-eight halt or "
        "allow exactly as their cases say. Two idle-hold fixtures allowed a long blocked note that "
        "their cases expected to halt, because the live job in each was launched in the same turn and "
        "that launch counted as substantive work; both cases now launch the job a turn earlier, which "
        "is the shape the rule is about. One ask-without-receiver fixture halted on a reply phrase it "
        "should have read as typed, and the replay audit found the same shape in four real transcripts "
        "from the last week, all of them a closing line handing the commit phrase. The guards commit "
        "holds the rego change, the three new fixtures, the replay audit script and the stop-guard "
        "cases that pin them; the full stop-guard suite and opa test both pass on it. r9 and the "
        "release-model agent are unrelated to this commit and keep running.\n\nauthorize commit"
    )
    assert len(survey_then_phrase) > 900, len(survey_then_phrase)
    expect(
        "handed-reply-line-after-long-report-is-clean",
        [user("Run r9 and the full run.")] + r9_live + bg_launch("toolu_full", "bfull1")
        + [user("Survey the Stop fixtures."), assistant_text(survey_then_phrase)],
        lambda o: o == "",
        "a finished report that hands `authorize commit` on its own line is blocked on the user "
        "whatever its length, with two background jobs live",
    )

    recap_tail = (
        "The survey of the Stop fixtures is finished: thirty-eight of forty-one halt or allow as "
        "their cases say, two idle-hold fixtures allowed a long blocked note that should have halted, "
        "and one ask-without-receiver fixture halted on a reply phrase it should have read as typed. "
        "The guards commit holds the rego change, its three new fixtures and the replay audit script, "
        "and the release-model agent is rewriting the planner's affinity table, which r9 needs before "
        "it can rank the forty armaments again."
    )
    blocked_recap = "Blocked on r9 (task b2dpglvu7) and the release-model agent. " + recap_tail
    assert 550 <= len(blocked_recap) <= 650, len(blocked_recap)
    expect(
        "blocked-recap-without-phrase-line-flags",
        [user("Run r9.")] + r9_live + [user("Fix the guards."), assistant_text(blocked_recap)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "the same blocked opener with a 600-char recap and no phrase line is still a verbose pause",
    )
    mentioned_recap = (
        "Blocked on r9 (task b2dpglvu7) and the release-model agent, and the guards commit needs the "
        "authorize commit reply. " + recap_tail
    )
    expect(
        "phrase-mentioned-mid-sentence-recap-flags",
        [user("Run r9.")] + r9_live + [user("Fix the guards."), assistant_text(mentioned_recap)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "naming the phrase inside a sentence hands nothing, so the long recap is still a verbose pause",
    )

    expect(
        "idle-hold-with-handed-reply-line-is-clean",
        [user("Fix the guards."),
         assistant_text("Holding for r9 before rerunning the gate.\n\nauthorize commit")],
        lambda o: o == "",
        "an idle phrase in a message that hands `authorize commit` on its own line is user-blocked",
    )
    expect(
        "idle-hold-with-phrase-mid-sentence-flags",
        [user("Fix the guards."),
         assistant_text("Holding for r9 before rerunning the gate; the commit waits on the "
                        "authorize commit reply.")],
        lambda o: o.startswith("IDLEHOLD:"),
        "the phrase named inside a sentence does not excuse an idle hold",
    )

    # ---- 2026-11-11: an answer that accounts for a running task is not a pause ---------------------
    #
    # Session 5ec7b779, verbatim. The user typed a question; the turn edited a doc, messaged an agent
    # and answered. no_unexplained_running_tasks halted the answer because r10 (bdj5lkbhk) was left
    # running unnamed, and the rewrite -- in a turn opened by that Stop-hook feedback, so it has no tool
    # call -- added the sentence the guard asks for. VERBOSEPAUSE then halted it at 735 chars.
    def typed(text: str) -> dict:
        return {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
                "message": {"role": "user", "content": text}}

    def stop_feedback_meta(text: str) -> dict:
        return {"type": "user", "isMeta": True,
                "message": {"role": "user", "content": "Stop hook feedback:\n" + text}}

    timelines_question = typed(
        "Almost all of your revisions were unaided or guided by me, so I ask again, should I ever "
        "listen to you for timelines? Are timeline predictions useful? Should an LLM be prohibited "
        "from making non-cited claims about timelines?"
    )
    answer_core = (
        "No, not when the forecast cites nothing measured. My uncited estimates here came in 10× to "
        "3,500× too low, and most of the cost came from model changes your corrections drove (homing, "
        "delayed follow-ups, floor aim, objective lines, release frames), which no guess made up front "
        "could have included. A projection from a measured pace or a timed sample is useful, because it "
        "decides whether to wait for a run or stop it. A guess about development effort isn't. So yes "
        "to the prohibition. "
    )
    first_answer = (
        answer_core + "The rule is now in `docs/agents/runtime-evidence.md`. The background agent is "
        "building the guard that will block an uncited run-time forecast and any effort forecast."
    )
    accounted_answer = (
        answer_core + "The rule is now in `docs/agents/runtime-evidence.md`, and the background agent "
        "is building the guard that will block an uncited forecast. r10 (bdj5lkbhk) is still running "
        "because it gives the v9 timing baseline the speed-up will be measured against."
    )
    assert len(accounted_answer) == 735, len(accounted_answer)
    unexplained_feedback = stop_feedback_meta(
        "Background tasks you started are still running and the closing message does not account for "
        "them: bdj5lkbhk (bash: S=/tmp/claude-1000/x...). Any monitor or loop you no longer need, stop "
        "it first (TaskStop with its task id) -- that is the first thing to do, before the report. For "
        "one that is still needed, name its task id in the closing message in a sentence that says "
        "what it is still waiting for or doing."
    )
    timelines_turn = (
        [typed("Start r10 from sim-release.")]
        + bg_launch("toolu_r10", "bdj5lkbhk", foreground_moved=True)
        + [assistant_text("r10 is running from sim-release (bdj5lkbhk), tailed in a kitty tab."),
           timelines_question,
           assistant_edit(), tool_result(),
           {"type": "assistant", "message": {"content": [
               {"type": "tool_use", "id": "toolu_msg", "name": "SendMessage",
                "input": {"to": "a640aedb6bb824f10", "message": "Addition from the user."}}]}},
           tool_result(),
           assistant_text(first_answer),
           unexplained_feedback]
    )
    expect(
        "answer-accounting-for-running-task-after-stop-feedback-is-clean",
        timelines_turn + [assistant_text(accounted_answer)],
        lambda o: o == "",
        "the 735-char answer whose only task reference is the sentence no_unexplained_running_tasks "
        "demanded is an answer to the typed question, not a blocked pause",
    )
    expect(
        "answer-saying-it-waits-on-running-task-flags",
        timelines_turn + [assistant_text(
            accounted_answer.replace("is still running because it gives",
                                     "is still running, and I'm waiting on it for"))],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "the same answer whose task sentence says the turn is waiting on r10 is a verbose pause",
    )

    # A real verbose hold from the same session, verbatim: the user typed a request, the turn launched
    # two runs, the Stop hook asked for them to be accounted for, and the rewrite said it was waiting on
    # both and tabled them. It must still halt.
    waiting_on_both = (
        "Two background runs are still going, and I'm waiting on both:\n\n"
        "| task | what it's still doing |\n|---|---|\n"
        "| `bmb1rcjop` | the pouch selftest, then an INT 50 Uchigatana build holding a Carian Regal "
        "Scepter: the first real test of attack spells competing with buffs for memory slots |\n"
        "| `b0r0j85m6` | the FTH 80 Erdtree Seal kit on the new back-off situations (the INT 80 half "
        "already finished: Twin Moons alone) |"
    )
    assert len(waiting_on_both) == 412, len(waiting_on_both)
    expect(
        "waiting-on-both-runs-after-stop-feedback-flags",
        [typed("make PRs for each branch and then do that next")]
        + bg_launch("toolu_kit", "b0r0j85m6") + bg_launch("toolu_pouch", "bmb1rcjop")
        + [assistant_text("Both runs are going."),
           stop_feedback_meta(
               "Background tasks you started are still running and the closing message does not "
               "account for them: b0r0j85m6 (bash: python3 scripts/er-mechanics-spells.py --stats "
               "int=80,dex=20 --catalyst 'Cari...)."),
           assistant_text(waiting_on_both)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "'I'm waiting on both' plus a table, after Stop-hook feedback, is a verbose blocked pause",
    )

    # ---- 2026-11-11: a pause names what it is blocked on ----------------------------------------
    #
    # User words, verbatim: "So next time, how are you going to monitor your own shells and understand
    # when they are running so you can communicate clearly to the user why you're paused (blocked
    # because X shell is running) or whatever you're doing". On Stop, a pause whose closing message
    # names nothing scripts/cupcake_session_tasks.py measures running gets that report: alone as
    # UNNAMEDPAUSE, or on a second line after VERBOSEPAUSE or IDLEHOLD. The shells here are real
    # processes (run_signal's `live`), so the process-table half is exercised, not stubbed.
    def agent_launch(tool_id: str, agent_id: str, description: str) -> list[dict]:
        return [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent",
                 "input": {"description": description, "prompt": "go", "run_in_background": True}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id,
                 "content": f"Async agent launched successfully. agentId: {agent_id} (internal ID)"}]}},
        ]

    board_run = (
        [typed("Regenerate the boards.")] + bg_launch("toolu_board", "bboard1")
        + [assistant_text("The regeneration is running."), typed("How is it going?")]
    )
    stop = {"event_name": "Stop"}
    expect(
        "pause-naming-no-live-shell-gets-the-report",
        board_run + [assistant_text("Blocked on the board run.")],
        lambda o: o.startswith("UNNAMEDPAUSE:") and "bboard1 (main session:" in o and "\n" not in o,
        "a short pause that names no live shell is UNNAMEDPAUSE with the report",
        live=("bboard1",), **stop,
    )
    expect(
        "pause-naming-the-live-shell-is-clean",
        board_run + [assistant_text("Blocked on bboard1, the board run.")],
        lambda o: o == "",
        "naming the shell's task id is what the pause owes",
        live=("bboard1",), **stop,
    )
    expect(
        "pause-check-is-stop-only",
        board_run + [assistant_text("Blocked on the board run.")],
        lambda o: o == "",
        "on UserPromptSubmit what runs now is not what ran at turn end; the check stays out",
        live=("bboard1",), event_name="UserPromptSubmit",
    )
    expect(
        "pause-with-nothing-measured-running-is-judged-as-before",
        board_run + [assistant_text("Blocked on the board run.")],
        lambda o: o == "",
        "the transcript says bboard1 runs, the process table lists nothing: no report, no halt",
        **stop,
    )
    recap = (
        "Blocked on the board run. It is regenerating the weapon boards with the new damage model, "
        "which changes the ranking of every greatsword against the poise threshold, so the per-weapon "
        "pages will move. The earlier run finished the katana and straight sword families already, "
        "and the colossal weapons are the slowest family by far because of their attack-set counts. "
        "After the boards land I will diff them against yesterday's and look for any weapon whose "
        "rank moved by more than three places."
    )
    assert len(recap) > 450, len(recap)
    expect(
        "verbose-pause-naming-nothing-carries-the-report-on-a-second-line",
        board_run + [assistant_text(recap)],
        lambda o: o.startswith("VERBOSEPAUSE:") and "\nUNNAMED:Shells running: bboard1" in o,
        "VERBOSEPAUSE first line unchanged; the report rides after it",
        live=("bboard1",), **stop,
    )
    expect(
        "verbose-pause-naming-the-shell-is-only-verbose",
        board_run + [assistant_text(recap.replace("the board run.", "bboard1, the board run."))],
        lambda o: o.startswith("VERBOSEPAUSE:") and "UNNAMED" not in o,
        "a long pause that names the shell is only the verbose halt",
        live=("bboard1",), **stop,
    )
    expect(
        "idle-hold-naming-nothing-carries-the-report",
        board_run + [assistant_text("I'm holding for the board run.")],
        lambda o: o.startswith("IDLEHOLD:") and "\nUNNAMED:Shells running: bboard1" in o,
        "IDLEHOLD first line unchanged; the report rides after it",
        live=("bboard1",), **stop,
    )
    expect(
        "pause-after-substantive-work-is-not-asked-to-name",
        board_run + [assistant_edit(), tool_result(), assistant_text("Blocked on the board run.")],
        lambda o: o == "",
        "a turn that did real work is not a pause",
        live=("bboard1",), **stop,
    )
    expect(
        "pause-on-the-user-is-not-asked-to-name",
        board_run + [assistant_text("Blocked on the board run, and I need you to pick the build.")],
        lambda o: o == "",
        "a turn that waits on the user is exempt, as from VERBOSEPAUSE",
        live=("bboard1",), **stop,
    )
    expect(
        "pause-waiting-on-you-is-not-asked-to-name",
        board_run + [assistant_text("Waiting on you to say whether to relaunch Elden Ring.")],
        lambda o: o == "",
        "verbatim from the replay, session 965886fc: a wait on the user, in words USER_WAIT_RE misses",
        live=("bboard1",), **stop,
    )

    speed_agent = (
        [typed("Speed up the attack-set stage.")]
        + agent_launch("toolu_agent", "a5eed0011223344aa", "Speed up attack-set stage")
        + [assistant_text("The agent is on it."), typed("How is it going?")]
    )
    expect(
        "pause-naming-no-working-agent-gets-the-report",
        speed_agent + [assistant_text("Blocked until the agent reports back.")],
        lambda o: o.startswith("UNNAMEDPAUSE:") and "a5eed0011223344aa 'Speed up attack-set stage' (running)" in o,
        "a working agent, no shells: the report lists the agent",
        **stop,
    )
    for name, text in (("description", "Blocked on the speed up attack-set stage agent."),
                       ("short-id", "Blocked on agent a5eed00."),
                       ("full-id", "Blocked on a5eed0011223344aa.")):
        expect(
            f"pause-naming-the-agent-by-{name}-is-clean",
            speed_agent + [assistant_text(text)],
            lambda o: o == "",
            f"an agent named by its {name} is named",
            **stop,
        )
    expect(
        "pause-naming-nothing-while-a-subagent-shell-runs-lists-it-under-the-agent",
        speed_agent + [assistant_text("Blocked on the agent's selftest.")],
        lambda o: o.startswith("UNNAMEDPAUSE:") and "bsub7 (agent a5eed0011223344aa" in o,
        "a subagent's shell, which the main transcript never shows, is in the report with its owner",
        live=("bsub7",), subagents={"a5eed0011223344aa": bg_launch("toolu_sub", "bsub7")}, **stop,
    )
    expect(
        "pause-naming-a-subagent-shell-is-clean",
        speed_agent + [assistant_text("Blocked on bsub7, the agent's selftest.")],
        lambda o: o == "",
        "naming the subagent's shell id is naming what the pause waits on",
        live=("bsub7",), subagents={"a5eed0011223344aa": bg_launch("toolu_sub", "bsub7")}, **stop,
    )
    monitor_launch = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_mon", "name": "Monitor", "input": {"command": "tail -f x.log"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_mon",
             "content": "Monitor started (task bmon7, timeout 1800000ms)."}]}},
    ]
    expect(
        "pause-naming-a-running-monitor-is-clean",
        board_run[:-1] + monitor_launch + [assistant_text("Watching."), typed("How is it going?"),
                                           assistant_text("Blocked on bmon7, the log watcher.")],
        lambda o: o == "",
        "a Monitor writes no task output file; the transcript's running Monitor counts as named",
        live=("bboard1",), **stop,
    )

    # (24) 2026-10-30: the harness logs an `api-request-blob` copy of each message it sends, written
    # after that message's tool_result. A `run_in_background` Bash that finished ("exit 0") was booked
    # again from its blob copy, never got a second result, and stayed "live"; the closing report after
    # the later task's notification halted as an 826-char verbose pause with nothing running.
    def blob_copy(ev: dict) -> dict:
        return {"type": "api-request-blob", "hash": "h", "message": {"role": "assistant",
                                                                       **ev["message"]}}

    finished_bg = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "toolu_x", "name": "Bash",
                                 "input": {"command": "python3 scripts/x.py > /tmp/x/o.txt",
                                           "run_in_background": True}}]},
    }
    blob_incident = (
        [user("Sort the talismans."), finished_bg,
         {"type": "user", "message": {"content": [
             {"type": "tool_result", "tool_use_id": "toolu_x", "content": "exit 0"}]}},
         blob_copy(finished_bg)]
        + bg_launch("toolu_y", "b972d96y5", foreground_moved=True)
        + [{"type": "queue-operation", "operation": "enqueue",
            "content": notification("toolu_y", "b972d96y5")},
           user(notification("toolu_y", "b972d96y5"))]
        + peek_turn()
        + [assistant_text(report)]
    )
    expect(
        "report-after-api-request-blob-replay-is-clean",
        blob_incident,
        lambda o: o == "",
        "an api-request-blob copy of a finished background Bash is not a new launch",
    )
    # Control: without the real result the same launch is still live, blob or no blob.
    expect(
        "verbose-long-blob-copy-of-unfinished-launch-flags",
        [user("Sort the talismans."), finished_bg, blob_copy(finished_bg),
         user("Anything yet?"), assistant_text(report)],
        lambda o: o.startswith("VERBOSEPAUSE:"),
        "dropping blob copies must not hide a launch that never returned",
    )

    print(f"idle-hold signal tests passed ({CASES_RUN} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
