#!/usr/bin/env python3
"""Start role-bound Frida watchers for the npc-netsync two-client proof."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
AGENT = REPO / "scripts/frida/npc-netsync-proof.js"

# A safety cap on bringing one role's server up, not a wait for it. `er-frida-up.py` returns as soon
# as the port answers and fetches the server in resumable chunks, so a start still running after this
# has hit something worth failing on rather than sitting through.
SERVER_START_TIMEOUT_SECONDS = 30


def role_plan(run_root: pathlib.Path, role: str, linux_pid: int, windows_pid: int, port: int, config_json: str | None) -> dict:
    role_root = run_root / role
    watch_command = [
        "uv",
        "run",
        "--with",
        "frida",
        "python3",
        "-u",
        "scripts/er-frida-watch.py",
        "--role",
        role,
        "--endpoint",
        f"127.0.0.1:{port}",
        "--pid",
        str(windows_pid),
        "--agent",
        str(AGENT),
        "--log",
        str(role_root / "frida-events.jsonl"),
    ]
    if config_json is not None:
        watch_command.extend(["--config-json", config_json])
    return {
        "role": role,
        "linux_pid": linux_pid,
        "windows_pid": windows_pid,
        "endpoint": f"127.0.0.1:{port}",
        "server_log": str(role_root / "frida-server.log"),
        "server_pidfile": str(role_root / "frida-server.pid.json"),
        "watch_log": str(role_root / "frida-events.jsonl"),
        "watch_stdout": str(role_root / "frida-watch.stdout.log"),
        "evidence_log": str(role_root / "frida-evidence.jsonl"),
        "agent": str(AGENT),
        "up_command": [
            "python3",
            "scripts/er-frida-up.py",
            "--force",
            "--role",
            role,
            "--linux-pid",
            str(linux_pid),
            "--port",
            str(port),
            "--log",
            str(role_root / "frida-server.log"),
            "--pidfile",
            str(role_root / "frida-server.pid.json"),
        ],
        "watch_command": watch_command,
    }


def write_plan(run_root: pathlib.Path, plan: dict) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    for role in ["host", "peer"]:
        (run_root / role).mkdir(parents=True, exist_ok=True)
    (run_root / "attach-plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def start_role(plan: dict) -> subprocess.Popen:
    subprocess.run(plan["up_command"], cwd=REPO, check=True, timeout=SERVER_START_TIMEOUT_SECONDS)
    stdout_path = pathlib.Path(plan["watch_stdout"])
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ER_FRIDA_EVIDENCE_LOG"] = plan["evidence_log"]
    handle = stdout_path.open("ab")
    return subprocess.Popen(plan["watch_command"], cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=pathlib.Path, default=pathlib.Path("/tmp/npc-netsync-proof") / str(int(time.time())))
    parser.add_argument("--host-linux-pid", type=int, required=True)
    parser.add_argument("--host-windows-pid", type=int, required=True)
    parser.add_argument("--peer-linux-pid", type=int, required=True)
    parser.add_argument("--peer-windows-pid", type=int, required=True)
    parser.add_argument("--host-port", type=int, default=27042)
    parser.add_argument("--peer-port", type=int, default=27043)
    parser.add_argument("--target-json", help="JSON target handle emitted as npc_netsync_target in both agents")
    parser.add_argument("--start", action="store_true", help="Actually start servers and watchers. Omit for dry-run planning.")
    args = parser.parse_args()

    config_json = None
    if args.target_json is not None:
        try:
            target = json.loads(args.target_json)
        except json.JSONDecodeError as exc:
            print(f"--target-json is not valid JSON: {exc}", file=sys.stderr)
            return 1
        config_json = json.dumps({"npc_netsync_target": target}, sort_keys=True)
    host = role_plan(args.run_root, "host", args.host_linux_pid, args.host_windows_pid, args.host_port, config_json)
    peer = role_plan(args.run_root, "peer", args.peer_linux_pid, args.peer_windows_pid, args.peer_port, config_json)
    plan = {
        "schema": "npc_netsync_attach_plan.v1",
        "run_root": str(args.run_root),
        "host": host,
        "peer": peer,
        "join_command": [
            "python3",
            "scripts/join-npc-netsync-proof.py",
            "--host",
            host["watch_log"],
            "--peer",
            peer["watch_log"],
            "--out",
            str(args.run_root / "verdict.json"),
        ],
    }
    write_plan(args.run_root, plan)
    if not args.start:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    procs = [start_role(host), start_role(peer)]
    (args.run_root / "watcher-pids.json").write_text(
        json.dumps({"host": procs[0].pid, "peer": procs[1].pid}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"run_root": str(args.run_root), "host_watcher": procs[0].pid, "peer_watcher": procs[1].pid}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
