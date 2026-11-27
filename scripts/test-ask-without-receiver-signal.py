#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_ask_without_receiver`.

The signal emits `ASKNORECEIVER:<sentence>` when the closing prose asks the user for an action or
event and nothing armed would wake the agent when it happens; empty otherwise. Each case writes a
crafted transcript, hands its path to the signal on stdin the way cupcake does, and asserts the tag.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_ask_without_receiver.sh"

ASK = (
    "The close trace is attached and waiting. I need you to close the item list with B twice: "
    "once from our view 3, and once from a native view."
)


def user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def use(uid: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]},
    }


def result(uid: str, text: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": uid, "content": text}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


def notify(uid: str, task: str, status: str = "completed") -> dict:
    return user(
        f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{uid}</tool-use-id>\n"
        f"<status>{status}</status>\n<summary>done</summary>\n</task-notification>"
    )


WATCHER = [
    use("w1", "Bash", {"command": "uv run --with frida python3 scripts/er-frida-watch.py "
                                  "--agent scripts/frida/x.js > /tmp/t.log", "run_in_background": True}),
    result("w1", "Command running in background with ID: bw1. Output is being written to: /tmp/t.log"),
]
MONITOR = [
    use("m1", "Monitor", {"command": "tail -n0 -F /tmp/t.log | grep --line-buffered close",
                          "description": "close events"}),
    result("m1", "Monitor started (task bm1, timeout 1800000ms). You will be notified on each event."),
]
UNTIL = [
    use("u1", "Bash", {"command": "until grep -q close /tmp/t.log; do sleep 1; done; tail -3 /tmp/t.log",
                       "run_in_background": True}),
    result("u1", "Command running in background with ID: bu1."),
]
# The 2026-11-10 r8 run: a foreground Bash the harness moved to the background after its timeout.
# Its exit is not a user's action, so it is no receiver for an ask of the user.
R8 = [
    use("r8", "Bash", {"command": "python3 scripts/er-mechanics-attack-similarity.py --out /tmp/r8.html "
                                  "--json /tmp/r8.json > /tmp/r8.log 2>&1"}),
    result("r8", "Command did not complete within its 120s timeout and was moved to the background "
                 "(ID: b2mrgwhrl). Output is being written to: /tmp/b2mrgwhrl.output. You will be "
                 "notified when it completes."),
]
R8_CLOSER = (
    "I'm blocked on three running jobs:\n\n"
    "- **r8 full run** (task b2mrgwhrl): 850 of 1,276 attacks done. I need its numbers to recheck the "
    "four control pairs the similarity selftest uses, and to merge the on-screen defender changes into "
    "dnok without disturbing the run.\n"
    "- **Similarity wiring agent**: making the similarity run use the on-screen defender. Until it does, "
    "Glintstone Cometshard and the Glintstone Dart still net about 0 there.\n"
    "- **Hit-record agent** (xb3i): a static read of whether a hitbox that deals no damage, like "
    "Night-and-Flame Stance R2's judge 3920, still uses up a victim's hit record."
)
R8_LINE = "- **r8 full run** (task b2mrgwhrl): 850 of 1,276 attacks done. "


def run_signal(events: list[dict]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(SIGNAL)],
            input=json.dumps({"transcript_path": str(path), "hook_event_name": "Stop"}),
            cwd=REPO_ROOT, text=True, capture_output=True, timeout=25,
        )
        return proc.stdout.strip()


HALT = "halt"
ALLOW = "allow"

CASES = [
    ("the 2026-10-02 closer, watcher only", [user("go"), *WATCHER, say(ASK)], HALT),
    ("same ask, live Monitor", [user("go"), *WATCHER, *MONITOR, say(ASK)], ALLOW),
    ("Monitor armed in an earlier turn, still live",
     [user("go"), *WATCHER, *MONITOR, say("Armed."), user("ok"), say(ASK)], ALLOW),
    ("Monitor whose stream ended", [user("go"), *MONITOR, notify("m1", "bm1"), say(ASK)], HALT),
    ("Monitor stopped by TaskStop",
     [user("go"), *MONITOR, use("s1", "TaskStop", {"task_id": "bm1"}), result("s1", "stopped"), say(ASK)],
     HALT),
    ("Monitor refused by a hook",
     [user("go"), use("m1", "Monitor", {"command": "tail -f /tmp/t.log"}),
      result("m1", "PreToolUse:Monitor hook error: blocked an unthrottled Monitor", True), say(ASK)],
     HALT),
    ("background until-loop still waiting", [user("go"), *WATCHER, *UNTIL, say(ASK)], ALLOW),
    ("background until-loop already exited",
     [user("go"), *UNTIL, notify("u1", "bu1"), say(ASK)], HALT),
    ("let me know when", [user("go"), say("Press R3 in the item list and let me know when it is open.")], HALT),
    ("once you", [user("go"), say("Once you open the inventory, the probe records the cadence.")], HALT),
    ("once you, after a comma", [user("go"), say("Open the item list, and once you do, the probe records it.")],
     HALT),
    ("first-person consequent, trailing when you",
     [user("go"), say("I'll read the close trace when you open the inventory.")], HALT),
    ("bulleted once you", [user("go"), say("- Once you invade, the log names the host.")], HALT),
    ("2026-10-04 false positive: game-mechanic after you, mid-sentence",
     [user("go"), say("The axe row is the best roll cover: the Hand Axe, followed by the Stone Club and "
                      "the Battle Axe group, which catch a roll taken at the first chance in 5 of 6 cases "
                      "after you run in.")], ALLOW),
    ("game-mechanic when you, mid-sentence",
     [user("go"), say("The Greatsword staggers a shield poke when you two-hand it.")], ALLOW),
    ("2026-10-18 false positive: rule description in a table row",
     [user("go"), say("| Gap | Before | After |\n|---|---|---|\n"
                      "| Fixing the product before the tools | No guard | New rule "
                      "`ER-EFFECTS-TOOLS-BEFORE-PROBLEMS`: once you report a tool miss, the turn may only "
                      "start agents that work on guards. It uses a word list, so a product prompt that "
                      "happens to mention \"guard\" gets through, and it doesn't block direct edits to "
                      "product code |")], ALLOW),
    ("rule conditional in prose, deontic consequent",
     [user("go"), say("New rule `X`: once you report a tool miss, the turn may only start guard agents.")],
     ALLOW),
    ("rule conditional plus a real ask in the same sentence",
     [user("go"), say("Once you report a miss, the turn may only start guard agents, so please reload now.")],
     HALT),
    ("2026-10-19 false positive: once you're <state>, game mechanic",
     [user("go"), say("Now, once you're broken, the roll-out escapes his later hits.")], ALLOW),
    ("once you are <state>, game mechanic, leading",
     [user("go"), say("Once you are guard broken, the riposte window opens for him.")], ALLOW),
    ("when you're <state>, mid-sentence game mechanic",
     [user("go"), say("The ash still connects when you're staggered.")], ALLOW),
    ("2026-11-10 false positive: once you count, generic you leading a statement",
     [user("What ashes of war are strictly worse versions of spells?"),
      say("Once you count what each one costs a real melee build, three spells are strictly worse than "
          "an ash that does the same job, and a fourth is on a Faith build.\n\n"
          "| Spell | Ash |\n|---|---|\n| Golden Vow | Golden Vow |")], ALLOW),
    ("once you factor in, capitalized subject",
     [user("go"), say("Once you factor in stamina, Golden Vow is worse than the ash.")], ALLOW),
    ("once you count, tell me", [user("go"), say("Once you count the hits, tell me.")], HALT),
    ("once you count, imperative consequent", [user("go"), say("Once you count them, reload.")], HALT),
    ("once you count, please in the consequent",
     [user("go"), say("Once you count them, three are left, so please reload.")], HALT),
    ("once you compare, instrument consequent",
     [user("go"), say("Once you compare them, the log shows the gap.")], HALT),
    ("once you count, first-person consequent",
     [user("go"), say("Once you count them, the total goes in my table.")], HALT),
    ("acting verb, plain statement consequent",
     [user("go"), say("Once you open the inventory, the cadence shows up.")], HALT),
    ("once you're in, first-person consequent",
     [user("go"), say("Once you're in the lobby, I'll read the session scan.")], HALT),
    ("once you're in, tell me", [user("go"), say("Once you're at the grace, tell me.")], HALT),
    ("once you open the menu, tell me", [user("go"), say("Once you open the menu, tell me.")], HALT),
    ("let me know once you've rehosted",
     [user("go"), say("Let me know once you've rehosted.")], HALT),
    ("once you've rehosted, leading", [user("go"), say("Once you've rehosted, the scan records it.")], HALT),
    ("once you reload, tell me", [user("go"), say("Once you reload, tell me what the log prints.")], HALT),
    ("let me know once you see it", [user("go"), say("Let me know once you see it in the tooltip.")], HALT),
    ("2026-10-28 false positive: as soon as you opened, past-tense report",
     [user("go"), say("As soon as you opened your world, Seamless created your advertisement lobby, and "
                      "your DLL wrote `er_invasion_warp_map = m13_00_00_00` to it, then read the value "
                      "back from Steam.")], ALLOW),
    ("once you opened, past-tense report",
     [user("go"), say("Once you opened the menu, the probe recorded the cadence.")], ALLOW),
    ("when you went, irregular past-tense report",
     [user("go"), say("When you went through the fog wall, the scan logged the new map.")], ALLOW),
    ("I'll ... when you opened, past-tense report",
     [user("go"), say("I can see the warp key landed when you opened your world.")], ALLOW),
    ("as soon as you open your world, present-tense ask",
     [user("go"), say("As soon as you open your world, the probe records the lobby key.")], HALT),
    ("once you've opened, present perfect ask",
     [user("go"), say("Once you've opened your world, the probe records the lobby key.")], HALT),
    ("once you have opened, present perfect ask",
     [user("go"), say("Once you have opened your world, the probe records the lobby key.")], HALT),
    ("once you proceed, present-tense -eed verb",
     [user("go"), say("Once you proceed to the boss, the probe records the fog wall.")], HALT),
    ("I'll read it when you open, present-tense ask",
     [user("go"), say("I'll read the warp key when you open your world.")], HALT),
    ("past-tense report plus a real ask in the same sentence",
     [user("go"), say("When you opened your world, Seamless created the lobby, so please rehost.")], HALT),
    ("2026-11-10 false positive: when you summon, the game sends, a game mechanic",
     [user("why were they Moongrum?"),
      say("**Summons:** your names, gear and face changes only happen on your machine. When you summon, "
          "the game sends peers just the three Moongrum IDs from `er-npc-summons.toml` (body 523590024, "
          "AI 523590100, gear 23590). The host's game builds each companion from its own unmodified "
          "Moongrum rows.")], ALLOW),
    ("when you invade, Seamless puts, a game mechanic",
     [user("go"), say("When you invade, Seamless only puts you in a host's world of the same band.")], ALLOW),
    ("when you summon, tell me", [user("go"), say("When you summon, tell me.")], HALT),
    ("when you summon, first-person consequent",
     [user("go"), say("When you summon, I'll read the log.")], HALT),
    ("when you summon, instrument consequent",
     [user("go"), say("When you summon, the probe records the IDs.")], HALT),
    ("when you summon, the game sends, plus a real ask",
     [user("go"), say("When you summon, the game sends the IDs, so please reload.")], HALT),
    ("once you summon, the game sends, waits for the user",
     [user("go"), say("Once you summon, the game sends the IDs.")], HALT),
    ("when you've summoned, the game sends, present perfect",
     [user("go"), say("When you've summoned, the game sends the IDs.")], HALT),
    ("when you summon, a subject that is not the game's",
     [user("go"), say("When you summon, the cadence shows up.")], HALT),
    ("I need you to", [user("go"), say("I need you to open the inventory twice.")], HALT),
    ("please press", [user("go"), say("Please press R3 in the item list.")], HALT),
    ("please verb",[user("go"),say("Please invade a host from the Bloodhound's Fang tile.")], HALT),
    ("2026-11-10 false positive: release-please, a hyphenated tool name",
     [user("pull main and check the PRs"),
      say("Main is pulled (`6ebcd76b`, a release-please changelog bump). On both PRs, every red job is a "
          "`cargo fmt --check` diff. Nothing else fails.")], ALLOW),
    ("please after a hyphenated word, still an ask",
     [user("go"), say("The release-please bump landed, so please rebase the branch.")], HALT),
    ("2026-11-10 false positive: I wasn't waiting for you, negated",
     [user("were you waiting on me?"),
      say("I wasn't waiting for you to run it. My wait loop was watching the run's artifact folder for "
          "the DLL's log, so the loop could never finish; I've stopped it.")], ALLOW),
    ("I'm not waiting on you, negated", [user("go"), say("I'm not waiting on you; the run ends by itself.")],
     ALLOW),
    ("waiting for you, not negated", [user("go"), say("I'm waiting for you to rehost.")], HALT),
    ("negated wait plus a real ask in the same sentence",
     [user("go"), say("I'm not waiting on you to rehost, but please reload the area.")], HALT),
    ("I need the game's point-light creation API, left halting: same words as an ask",
     [user("go"), say("Building the bare-light stimulus instead. First I need the game's point-light "
                      "creation API.")], HALT),
    ("I need the game's title screen, the ask it cannot be told from",
     [user("go"), say("First I need the game's title screen.")], HALT),
    ("2026-11-10 false positive: can you tell X apart from Y, a knowledge question",
     [user("which attack spells can a duelist read?"),
      say("Before a Glintstone Cometshard's bolt leaves the staff, can you tell it apart from a Comet, "
          "Shard Spiral or Night Comet, and a tap from a charge?")], ALLOW),
    ("can you tell whether, a knowledge question",
     [user("go"), say("Can you tell whether the charged cast has a longer windup than the tap?")], ALLOW),
    ("could you reliably tell if, a knowledge question",
     [user("go"), say("Could you reliably tell if a Night Comet is coming from the staff glow alone?")],
     ALLOW),
    ("can you distinguish, a knowledge question",
     [user("go"), say("Can you distinguish a Comet from a Cometshard by the sound?")], ALLOW),
    ("can you tell me when, a report of a future event",
     [user("go"), say("Can you tell me when the bolt leaves the staff?")], HALT),
    ("can you tell me whether it, a report of a future event",
     [user("go"), say("Can you tell me whether it fires after the reload?")], HALT),
    ("can you tell someone to act, not a knowledge question",
     [user("go"), say("Can you tell the host to rehost from the lobby?")], HALT),
    ("knowledge question plus a real ask in the same sentence",
     [user("go"), say("Can you tell whether it fired, and please reload the area.")], HALT),
    ("2026-11-10 false positive: I need its numbers, on a line naming a running task",
     [user("go"), *R8, say(R8_CLOSER)], ALLOW),
    ("I need its numbers, task id in backticks",
     [user("go"), *R8, say("- r8 (task `b2mrgwhrl`): 850 done. I need its numbers to recheck the pairs.")],
     ALLOW),
    ("what I need is their totals, on a running task's line",
     [user("go"), *R8, say(R8_LINE + "What I need is their totals for the control pairs.")], ALLOW),
    ("I need its numbers, the task already finished",
     [user("go"), *R8, notify("r8", "b2mrgwhrl"), say(R8_CLOSER)], HALT),
    ("I need its numbers, line names no task",
     [user("go"), *R8, say("850 of 1,276 attacks done.\nI need its numbers to recheck the pairs.")], HALT),
    ("I need you to, on a running task's line",
     [user("go"), *R8, say(R8_LINE + "I need you to close the item list twice.")], HALT),
    ("I need your log, on a running task's line",
     [user("go"), *R8, say(R8_LINE + "I need your log from the last session.")], HALT),
    ("I need its numbers from you, on a running task's line",
     [user("go"), *R8, say(R8_LINE + "I need its numbers from you before the merge.")], HALT),
    ("I need the game open, on a running task's line",
     [user("go"), *R8, say(R8_LINE + "I need the game open on the title screen.")], HALT),
    ("I need the game open, no task", [user("go"), say("I need the game open on the title screen.")], HALT),
    ("I need a fresh session from you", [user("go"), say("I need a fresh session from you.")], HALT),
    ("I need the item list closed, on a running watcher's line",
     [user("go"), *WATCHER, say("- Frida watcher (task bw1) is attached. I need the item list closed "
                                "twice with B.")], HALT),
    ("I need its numbers plus a real ask in the same sentence",
     [user("go"), *R8, say(R8_LINE + "I need its numbers, so please reload the area.")], HALT),
    ("subjective question", [user("go"), say("Does the font look right to you?")], ALLOW),
    ("subjective ask phrase", [user("go"), say("Please tell me if the board looks right.")], ALLOW),
    ("decision", [user("go"), say("I need you to decide whether the board replaces view 2 or adds a view 4.")],
     ALLOW),
    ("sudo", [user("go"), say("I need you to run the sudo command in the new Kitty tab.")], ALLOW),
    ("observation with no oracle",
     [user("go"), say("Tell me what you see on the loading screen; there is no pixel oracle for it.")], ALLOW),
    ("2026-10-26 false positive: authorize commit reply ask",
     [user("go"), say("I need your `authorize commit` reply before I can fix either PR, because both fixes "
                      "are `cargo fmt` runs and formatting and committing wait for it. You'll need to send "
                      "it twice, once per branch:\n\nauthorize commit\n\nThen I run the formatter.")], ALLOW),
    ("authorize <sha> phrase ask",
     [user("go"), say("I need the `authorize 6ebcd76b0000` phrase for fix/x before I push it.")], ALLOW),
    ("I need your reply, bare", [user("go"), say("I need your reply on the PR split.")], ALLOW),
    ("please reply", [user("go"), say("Please reply with which branch goes first.")], ALLOW),
    ("reply ask with an action ask riding along",
     [user("go"), say("I need your reply once you've used the item.")], HALT),
    ("reply ask then I need you to",
     [user("go"), say("I need your `authorize commit` reply, and I need you to use the item twice.")], HALT),
    ("need your presses is not a reply", [user("go"), say("I need your presses on B twice.")], HALT),
    ("please respond after the action",
     [user("go"), say("Please respond when you've rehosted.")], HALT),
    ("2026-11-10 replay false positive: I need it, the reply phrase handed on its own line",
     [user("fix the two red PRs"),
      say("The fix is to run `cargo fmt` on each branch and commit the result. Formatting and committing "
          "both wait for your reply, and each reply pays for one commit, so I need it twice, once per "
          "branch:\n\nauthorize commit\n\nAfter each commit I'll check that branch's runtime evidence, "
          "then give you the `authorize <sha>` phrase for its push.")], ALLOW),
    ("I'd need it, authorize <sha> on its own line in backticks",
     [user("go"), say("The push waits for your reply, so I'd need it once:\n\n`authorize 6ebcd76b0000`")],
     ALLOW),
    ("I need it, the reply named in the sentence before",
     [user("go"), say("Each commit waits for your `authorize commit` reply. I need it twice, once per "
                      "branch:\n\nauthorize commit")], ALLOW),
    ("I need it, no authorize phrase on a line of its own",
     [user("go"), say("Formatting waits for your reply, so I need it twice, once per branch.")], HALT),
    ("I need it, phrase line present but no reply named",
     [user("go"), say("The game has to sit on the title screen, so I need it twice.\n\nauthorize commit")],
     HALT),
    ("I need it open, phrase line present",
     [user("go"), say("Each reply pays for one commit, and I need it open on the title screen:\n\n"
                      "authorize commit")], HALT),
    ("I need it, phrase line present, action ask riding along",
     [user("go"), say("Each reply pays for one commit, so I need it twice, once you've used the item:\n\n"
                      "authorize commit")], HALT),
    ("I need it, phrase line present, I need you to in the next sentence",
     [user("go"), say("Committing waits for your reply, so I need it twice:\n\nauthorize commit\n\n"
                      "I need you to close the item list with B.")], HALT),
    ("I need the game open, phrase line present",
     [user("go"), say("Your reply pays for the commit:\n\nauthorize commit\n\n"
                      "After that I need the game open on the title screen.")], HALT),
    ("self need", [user("go"), say("I need to rebuild the shell before the next run.")], ALLOW),
    ("quoted ask", [user("go"), say('The bad closer was "I need you to close the item list".')], ALLOW),
    ("ask mid-turn, report at the end",
     [user("go"), say("I need you to close it."), use("e1", "Edit", {"file_path": "/x"}), result("e1", "ok"),
      say("Edited the trace agent.")], ALLOW),
]


def main() -> int:
    failures = 0
    for name, events, want in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("ASKNORECEIVER:") else ALLOW if out == "" else f"odd:{out!r}"
        if got != want:
            failures += 1
            print(f"FAIL {name}: want {want}, got {got} ({out[:160]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-ask-without-receiver-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-ask-without-receiver-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
