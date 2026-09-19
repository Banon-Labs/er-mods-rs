#!/usr/bin/env python3
"""Move a window to a workspace as soon as the compositor says it opened.

Used to put the gamescope window holding Elden Ring on the monitor the user was working on.
gamescope's window lands on whatever output the compositor gives it, and AGENTS.md wants the game
where the launcher is rather than pinned to a hard-coded monitor.

The wait is an actual readiness signal, not a poll: Hyprland publishes `openwindow` on its event
socket, and reading that socket blocks until an event arrives. `scripts/check-no-timeouts.py`
bans sleep loops for exactly this reason -- a poll interval is a guess about how long something
takes, and it is wrong in both directions.

Only the one requested class is ever read or printed. AGENTS.md forbids dumping the window list,
because it exposes every unrelated application the user happens to be running.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys

DEFAULT_TIMEOUT_SECONDS = 30


def event_socket_path() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not signature:
        return None
    path = os.path.join(runtime, "hypr", signature, ".socket2.sock")
    return path if os.path.exists(path) else None


def existing_window(window_class: str) -> str | None:
    """The address of an already-open window of this class, if the race was lost."""
    try:
        out = subprocess.run(
            ["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
        clients = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for client in clients:
        if (client.get("class") or "").lower() == window_class.lower():
            return client.get("address")
    return None


def wait_for_open(window_class: str, timeout: int) -> str | None:
    """Block on the compositor's event stream until this class opens. `None` on timeout."""
    path = event_socket_path()
    if path is None:
        return None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(timeout)
        try:
            stream.connect(path)
        except OSError:
            return None
        # Checked after connecting, never before: a window that opens between a pre-check and the
        # subscription would be missed by both, which is the classic shape of this race.
        already = existing_window(window_class)
        if already:
            return already
        buffered = ""
        try:
            while True:
                chunk = stream.recv(4096)
                if not chunk:
                    return None
                buffered += chunk.decode("utf-8", "replace")
                while "\n" in buffered:
                    line, buffered = buffered.split("\n", 1)
                    # openwindow>>address,workspace,class,title
                    if not line.startswith("openwindow>>"):
                        continue
                    fields = line[len("openwindow>>") :].split(",", 3)
                    if len(fields) >= 3 and fields[2].lower() == window_class.lower():
                        return "0x" + fields[0]
        except (OSError, socket.timeout):
            return None


def move(address: str, workspace: int) -> bool:
    dispatch = f'hl.dsp.window.move({{ workspace = {workspace}, window = "address:{address}" }})'
    try:
        proc = subprocess.run(
            ["hyprctl", "dispatch", dispatch],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "error" not in proc.stdout.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--class", dest="window_class", default="gamescope")
    parser.add_argument("--workspace", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        path = event_socket_path()
        print("selftest: hyprland event socket " + (f"found: {path}" if path else "not available"))
        sample = 'openwindow>>55dcff23dd30,2,gamescope,Elden Ring'
        fields = sample[len("openwindow>>"):].split(",", 3)
        parsed = len(fields) >= 3 and fields[2] == "gamescope"
        print(f"selftest: openwindow line parses to a class: {'ok' if parsed else 'broken'}")
        return 0 if (path and parsed) else 1

    if args.workspace is None:
        parser.error("--workspace is required unless --selftest is given")
    address = wait_for_open(args.window_class, args.timeout)
    if address is None:
        print(f"hypr-place-window: no {args.window_class} window within {args.timeout}s", file=sys.stderr)
        return 1
    if not move(address, args.workspace):
        print(f"hypr-place-window: could not move {address}", file=sys.stderr)
        return 1
    print(f"hypr-place-window: {args.window_class} placed on workspace {args.workspace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
