#!/usr/bin/env python3
"""Open a kitty tab that follows one background subagent's transcript until the agent ends, then
closes the tab 5 seconds later.

    python3 scripts/kitty-tail-agent.py <agent id> [<agent id> ...]

User, 2026-11-11: "I don't understand how you could have a round running without me being able to
see the output". A background Agent writes its whole conversation -- every tool call, result and
message -- to `~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl` as it works, and
the main session's Agent result names the id (`agentId: <id>`). Each id gets one tab, opened with
`--keep-focus`, running `scripts/watch-workflow.py --agent <that transcript> --full`: the agent's
messages, tool calls and results in color, followed until the main session's transcript carries
the agent's "finished" or "was stopped" notification (or a TaskStop naming it). The view then
prints how the agent ended and closes 5 s later; "is waiting on its own background work" is not an
end. The Stop guard `.cupcake/policies/claude/no_untailed_agent.rego` asks for this command.

    python3 scripts/kitty-tail-agent.py --selftest        # offline: lookup, launch, the view's end
    python3 scripts/kitty-tail-agent.py --live-selftest   # a real kitty tab on a made-up agent

The watcher counterpart of `kitty-tail-task.py` for agents; it reuses `kitty-user-command.py`'s
listener lookup (`$KITTY_LISTEN_ON`) and `kitty @` wrapper.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import select
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
#: Seconds the tab stays open after the agent ends.
LINGER_S = 5
#: How long to wait for an agent's transcript to appear: it starts with the agent's prompt, which
#: the harness writes when it launches the agent.
WAIT_S = 10.0


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ww():
    return _load("watch_workflow", "watch-workflow.py")


def _kuc():
    return _load("kitty_user_command", "kitty-user-command.py")


def resolve(agent_id: str, root: str | None = None, wait: float = WAIT_S) -> str:
    """The agent's transcript path, waiting up to `wait` seconds for it to appear."""
    ww = _ww()
    if not ww.AGENT_ID_RE.fullmatch(agent_id):
        raise SystemExit(f"{agent_id!r} is not an agent id (letters, digits, '_' and '-')")
    deadline = time.monotonic() + wait
    while True:
        path = ww.agent_transcript(agent_id, root)
        if path:
            return path
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"no transcript agent-{agent_id}.jsonl under {root or ww.PROJECTS_ROOT}/*/*/subagents/ "
                f"after {wait:.0f} s; is it the agentId the Agent result named?")
        ww.bounded_poll_wait(0.25)


def view_argv(path: str, linger: int = LINGER_S) -> list[str]:
    """The program the tab runs."""
    return [sys.executable, str(HERE / "watch-workflow.py"), "--agent", path, "--full",
            "--linger", str(linger)]


def title_for(path: str, agent_id: str) -> str:
    desc = " ".join(_ww().agent_description(path).split())
    return f"agent: {desc[:40]}" if desc else f"agent {agent_id[:8]}"


def launch_argv(title: str, program: list[str]) -> list[str]:
    """The `kitty @ launch` arguments: a tab of its own that does not take the keyboard."""
    return ["launch", "--type=tab", "--keep-focus", f"--tab-title={title}", f"--cwd={REPO}", *program]


def followers(oswindows: list[dict], path: str) -> list[int]:
    """The ids of kitty windows (from `kitty @ ls`) already running this tool's view of the
    transcript at `path`, oldest first. A view ends only when its agent does, so a resume of an
    agent that was waiting on its own work still has one (user, 2026-11-16: "I have 3 duplicate
    kitty tabs?", three views of one agent, one per launch and resume)."""
    view = str(HERE / "watch-workflow.py")
    return sorted(w["id"] for osw in oswindows for t in osw.get("tabs", []) for w in t.get("windows", [])
                  if view in w.get("cmdline", []) and "--agent" in w["cmdline"]
                  and path in w["cmdline"][w["cmdline"].index("--agent") + 1:][:1])


def open_view(agent_id: str, root: str | None = None, wait: float = WAIT_S, title: str | None = None,
              dry_run: bool = False, linger: int = LINGER_S) -> int | None:
    """Open the tab for one agent; its kitty window id (None on a dry run). A view already
    following it is kept and any later duplicate of it closed, instead of opening another."""
    path = resolve(agent_id, root, wait)
    argv = launch_argv(title or title_for(path, agent_id), view_argv(path, linger))
    if dry_run:
        print("kitty @ " + shlex.join(argv))
        return None
    k = _kuc()
    listener = k.listener_or_exit()
    have = followers(json.loads(k.kitty(listener, "ls")), path)
    if have:
        for extra in have[1:]:
            k.kitty(listener, "close-window", f"--match=id:{extra}")
        print(f"window {have[0]} already follows {path}" +
              (f"; closed duplicate window(s) {', '.join(map(str, have[1:]))}" if have[1:] else ""))
        return have[0]
    window_id = k.kitty(listener, *argv).strip()
    if not window_id.isdigit():
        raise SystemExit(f"kitty @ launch did not return a window id: {window_id!r}")
    print(f"tab {argv[3].removeprefix('--tab-title=')!r} (window {window_id}) follows {path} until "
          f"agent {agent_id} ends, then closes after {linger} s")
    return int(window_id)


# --- tests ---------------------------------------------------------------------------------------

def fake_session(root: Path, agent_id: str, description: str = "Fake agent for the selftest") -> tuple[Path, Path]:
    """A made-up project under `root`: the main transcript naming the agent's launch, and the
    agent's own transcript with its prompt and one tool call. Returns (main, agent)."""
    session = root / "-fake-project" / "11111111-2222-3333-4444-555555555555"
    sub = session / "subagents"
    sub.mkdir(parents=True)
    main = session.with_suffix(".jsonl")
    launch = {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_fake",
        "content": [{"type": "text", "text": f"Async agent launched successfully.\nagentId: {agent_id} "
                     "(internal ID)\nThe agent is working in the background."}]}]}}
    main.write_text(json.dumps(launch) + "\n")
    agent = sub / f"agent-{agent_id}.jsonl"
    agent.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Do the fake work."}})
                     + "\n" + tool_call("fake-marker-one"))
    (sub / f"agent-{agent_id}.meta.json").write_text(json.dumps({"description": description}))
    return main, agent


def tool_call(marker: str) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{
        "type": "tool_use", "id": f"toolu_{marker}", "name": "Bash",
        "input": {"command": f"echo {marker}"}}]}}) + "\n"


def notification(agent_id: str, verb: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": (
        f"<task-notification>\n<task-id>{agent_id}</task-id>\n<status>completed</status>\n"
        f"<summary>Agent \"Fake agent for the selftest\" {verb}</summary>\n</task-notification>")}}) + "\n"


def resumed(agent_id: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_resume", "content": [{"type": "text", "text": json.dumps(
            {"success": True, "message": f"Resuming agent {agent_id[:7]}", "resumedAgentId": agent_id})}]}]}}) + "\n"


def task_stop(agent_id: str) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{
        "type": "tool_use", "id": "toolu_stop", "name": "TaskStop", "input": {"task_id": agent_id}}]}}) + "\n"


def append(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def read_until(proc: subprocess.Popen, seen: list[str], needle: str, deadline_s: float) -> bool:
    """Collect the view's output into `seen` until `needle` shows up or the deadline passes."""
    deadline = time.monotonic() + deadline_s
    while needle not in "".join(seen):
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        if select.select([proc.stdout], [], [], left)[0]:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                return needle in "".join(seen)
            seen.append(chunk.decode("utf-8", "replace"))  # UTF-8 Lossy: the view's output is only searched
    return True


def selftest() -> int:
    bad = 0

    def check(what, got, want):
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"{'ok' if ok else 'FAIL'}  {what}: {got!r}" + ("" if ok else f" (want {want!r})"))

    ww = _ww()
    aid = "aselftest0000001"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        main, agent = fake_session(root, aid)
        check("an id resolves to its transcript under the projects root", resolve(aid, str(root), 0), str(agent))
        check("a path resolves to itself", ww.agent_transcript(str(agent)), str(agent))
        try:
            resolve("anotherid0000001", str(root), 0)
            check("an unknown id is refused", False, True)
        except SystemExit:
            check("an unknown id is refused", True, True)
        try:
            resolve("../etc", str(root), 0)
            check("a non-id is refused", False, True)
        except SystemExit:
            check("a non-id is refused", True, True)
        check("the main transcript sits beside the session's subagents directory",
              ww.parent_transcript(str(agent)), str(main))
        check("the title is the agent's description", title_for(str(agent), aid), "agent: Fake agent for the selftest")
        check("the tab opens without focus and runs the agent view",
              launch_argv("t", view_argv(str(agent))),
              ["launch", "--type=tab", "--keep-focus", "--tab-title=t", f"--cwd={REPO}", sys.executable,
               str(HERE / "watch-workflow.py"), "--agent", str(agent), "--full", "--linger", "5"])
        other = str(agent).replace(aid, "aotheragent00001")
        ls = [{"tabs": [{"windows": [
            {"id": 9, "cmdline": view_argv(str(agent))},
            {"id": 4, "cmdline": view_argv(str(agent))},
            {"id": 5, "cmdline": view_argv(other)},
            {"id": 6, "cmdline": ["tail", "-F", str(agent)]},
        ]}]}]
        check("the views already following a transcript, oldest first; another agent's and a tail "
              "are not", followers(ls, str(agent)), [4, 9])
        check("no view follows an agent with none", followers(ls, str(agent) + ".x"), [])

        # The status reader, line by line.
        st = ww.AgentStatus(str(main), aid)
        st.refresh()
        check("a launched agent is running", st.state, "running")
        append(main, notification("aotheragent00001", "finished"))
        st.refresh()
        check("another agent's notification changes nothing", st.state, "running")
        append(main, notification(aid, "is waiting on its own background work"))
        st.refresh()
        check("waiting on its own background work is not an end", st.state, "waiting")
        append(main, notification(aid, "finished"))
        st.refresh()
        check("a finished notification ends it", st.state, "finished")
        append(main, resumed(aid))
        st.refresh()
        check("a SendMessage that resumed it starts it again", st.state, "running")
        append(main, task_stop(aid))
        st.refresh()
        check("a TaskStop naming it stops it", st.state, "stopped")
        partial = notification(aid, "finished")
        st2 = ww.AgentStatus(str(main), aid)
        st2.refresh()
        append(main, resumed(aid) + partial[:40])
        st2.refresh()
        check("half a line is not read yet", st2.state, "running")
        append(main, partial[40:])
        st2.refresh()
        check("and is once it is complete", st2.state, "finished")

    # The view end to end: follows new lines, keeps going while the agent waits, ends on finished.
    with tempfile.TemporaryDirectory() as d:
        main, agent = fake_session(Path(d), aid)
        view = subprocess.Popen(view_argv(str(agent), linger=1), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        seen: list[str] = []
        try:
            check("the view renders the agent's tool call", read_until(view, seen, "fake-marker-one", 10), True)
            append(agent, tool_call("fake-marker-two"))
            check("and follows a line appended later", read_until(view, seen, "fake-marker-two", 10), True)
            append(main, notification(aid, "is waiting on its own background work"))
            check("a waiting agent is noted", read_until(view, seen, "is waiting on its own background work", 10), True)
            check("and the view keeps going", read_until(view, seen, "═══", 1.5) or view.poll() is not None, False)
            t0 = time.monotonic()
            append(main, notification(aid, "finished"))
            check("the end is printed", read_until(view, seen, "finished: Agent", 10), True)
            view.wait(timeout=10)
            dt = time.monotonic() - t0
            check("the view exits after the 1 s linger (1 to 8 s)", 1.0 <= dt <= 8.0, True)
            check("with status 0", view.returncode, 0)
        finally:
            if view.poll() is None:
                view.kill()
                view.wait()
        # An agent that already ended: the view shows it all and exits.
        append(main, task_stop(aid))
        out = subprocess.run(view_argv(str(agent), linger=0), capture_output=True, text=True, timeout=20).stdout
        check("an agent that already ended is shown and closes",
              ("fake-marker-two" in out, "stopped: TaskStop named it" in out), (True, True))
    print("selftest ok" if not bad else "selftest FAILED")
    return 1 if bad else 0


def live_selftest() -> int:
    """The production launch against a real kitty tab: it opens without focus, shows the agent's
    lines as they are appended, says when the agent finished, and closes itself. Only the scratch
    window's own id is ever queried, never the window list."""
    k = _kuc()
    listener = k.listener_or_exit()
    aid = "alivetest0000001"
    ok = True
    with tempfile.TemporaryDirectory() as d:
        main, agent = fake_session(Path(d), aid)
        window_id = open_view(aid, root=d, wait=0, linger=2)
        match = f"id:{window_id}"

        def alive() -> bool:
            try:
                return bool(json.loads(k.kitty(listener, "ls", f"--match={match}")))
            except SystemExit:
                return False

        def shows(text: str) -> bool:
            try:
                return text in k.screen_text(listener, match)
            except SystemExit:
                return False

        try:
            for what, step in (
                ("the tab shows the agent's first tool call", lambda: None),
                ("and a tool call appended after it opened", lambda: append(agent, tool_call("fake-marker-two"))),
            ):
                step()
                marker = "fake-marker-one" if "first" in what else "fake-marker-two"
                try:
                    k.poll(what, 10, lambda: shows(marker))
                    print(f"ok    {what}")
                except SystemExit as e:
                    ok = False
                    print(f"FAIL  {what}: {e}")
            # A resume while it still runs: a duplicate view (as the old tool opened) is closed and
            # the first one kept, rather than a third opened.
            dup = int(k.kitty(listener, *launch_argv("dup", view_argv(str(agent), 2))).strip())
            kept = open_view(aid, root=d, wait=0, linger=2)

            def dup_alive() -> bool:
                try:     # `kitty @ ls` fails when no window matches
                    return bool(json.loads(k.kitty(listener, "ls", f"--match=id:{dup}")))
                except SystemExit:
                    return False
            try:
                k.poll("the duplicate to close", 10, lambda: not dup_alive())
                gone = True
            except SystemExit:
                gone = False
            ok &= kept == window_id and gone
            print(f"{'ok  ' if kept == window_id and gone else 'FAIL'}  a second call keeps window "
                  f"{kept} (want {window_id}) and closes the duplicate {dup}: {gone}")
            append(main, notification(aid, "finished"))
            try:
                k.poll("the finished line", 10, lambda: shows("finished: Agent") or not alive())
                print("ok    the tab says the agent finished")
            except SystemExit as e:
                ok = False
                print(f"FAIL  the tab says the agent finished: {e}")
            t0 = time.monotonic()
            try:
                k.poll("the tab to close", 15, lambda: not alive())
                print(f"ok    the tab closed itself {time.monotonic() - t0:.1f} s after the finished line")
            except SystemExit as e:
                ok = False
                print(f"FAIL  the tab closed itself: {e}")
        finally:
            if alive():
                k.kitty(listener, "close-window", f"--match={match}")
    print("live selftest:", "PASS" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("agent_ids", nargs="*", metavar="AGENT_ID",
                    help="the id the Agent result names (`agentId: <id>`); one tab each")
    ap.add_argument("--title", help="the tab title (default: 'agent: <its description>')")
    ap.add_argument("--wait", type=float, default=WAIT_S,
                    help="seconds to wait for the agent's transcript to appear")
    ap.add_argument("--projects-root", help="where the transcripts are (default ~/.claude/projects)")
    ap.add_argument("--linger", type=int, default=LINGER_S, help="seconds the tab stays after the end")
    ap.add_argument("--dry-run", action="store_true", help="print the `kitty @ launch` instead")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--live-selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.live_selftest:
        return live_selftest()
    if not a.agent_ids:
        ap.error("name at least one agent id")
    failed = 0
    for agent_id in a.agent_ids:
        try:
            open_view(agent_id, a.projects_root, a.wait, a.title, a.dry_run, a.linger)
        except SystemExit as e:
            failed += 1
            print(f"{agent_id}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
