#!/usr/bin/env python3
"""How many more clients can connect to the X server, and who is holding the rest.

Wine opens one X11 connection per thread in `x11drv_init_thread_data`. When the server is at
its client ceiling, the early threads of a game connect and a later one gets `NULL` back from
`XOpenDisplay`; winex11 dereferences it and the process dies inside `vkCreateSwapchainKHR`.
What the user sees is a black window that disappears, with no crash dump, no panic and no
message -- a failure that looks exactly like a bad mod build and is not one.

That cost eight launches and several hours on 2026-09-14 before a Proton log named it, so the
measurement lives here as a launch preflight rather than as a paragraph somebody has to
remember. See bd `xwayland-maxclients-starvation-kills-er-boot-2026-09-14`.

The count is taken by actually connecting and holding every socket open, because a sequence of
connect/disconnect pairs never overlaps and would report headroom that does not exist. The
handshake is the real X11 connection setup, so a refusal here is the refusal the game gets.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import struct
import subprocess
import sys

# X11 connection setup: byte order 'l', protocol 11.0, no authorisation data. Hyprland's
# Xwayland runs with no auth cookie, and an auth failure answers 2 rather than 1 so it is
# reported rather than silently counted as a success.
SETUP_REQUEST = struct.pack("<BBHHHHH", 0x6C, 0, 11, 0, 0, 0, 0)

# The X protocol's traditional ceiling. Xwayland accepts `-maxclients` to raise it, but Hyprland
# spawns it with `-listenfd` and no such flag, so this is what is actually in force here.
PROTOCOL_CEILING = 256

# A booting Elden Ring took 17 slots before the server cut it off mid-boot, so it wants more
# than that. This is the refusal threshold, not a measurement of the game's true appetite --
# no run has yet been observed reaching the title screen to measure that.
DEFAULT_REQUIRED = 40

X11_SOCKET = os.environ.get("ER_X11_SOCKET", "/tmp/.X11-unix/X0")


def probe(want: int, sock_path: str = X11_SOCKET) -> tuple[int, str]:
    """Open up to `want` simultaneous connections. Returns (opened, refusal reason or "")."""
    held: list[socket.socket] = []
    reason = ""
    try:
        for _ in range(want):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(5)
                s.connect(sock_path)
                s.sendall(SETUP_REQUEST)
                head = s.recv(8)
                if not head:
                    reason = "server closed the connection without a reply"
                    s.close()
                    break
                if head[0] != 1:
                    length = head[1] if head[0] == 0 else 0
                    body = s.recv(length) if length else b""
                    reason = body.decode("latin-1", "replace").strip("\x00") or (
                        f"setup reply code {head[0]}"
                    )
                    s.close()
                    break
                held.append(s)
            except OSError as exc:
                reason = str(exc)
                s.close()
                break
    finally:
        for s in held:
            s.close()
    return len(held), reason


def holders() -> list[tuple[int, str]]:
    """Who holds the existing connections, biggest first. Empty when lsof cannot answer."""
    try:
        out = subprocess.run(
            ["lsof", "+E", "-U"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    counts: dict[str, int] = {}
    peer = re.compile(r"->INO=\d+\s+\d+,([^,]+),\d+[a-z]*u?\s+\(CONNECTED\)")
    for line in out.splitlines():
        if "X11-unix" not in line:
            continue
        found = peer.search(line)
        if found:
            name = found.group(1).strip()
            counts[name] = counts.get(name, 0) + 1
    return sorted(((n, c) for c, n in counts.items()), reverse=True)


def selftest() -> int:
    """Prove the probe measures rather than assumes, without needing a starved server."""
    opened, reason = probe(1)
    if opened != 1:
        print(f"selftest FAILED: could not open even one connection to {X11_SOCKET}: {reason}")
        return 1
    # Asking for zero must open zero and refuse nothing -- the loop body never runs.
    zero, zero_reason = probe(0)
    if zero or zero_reason:
        print(f"selftest FAILED: a zero-connection probe reported {zero}/{zero_reason!r}")
        return 1
    # A bad socket path must be reported as a refusal, not counted as headroom.
    bad, bad_reason = probe(1, sock_path="/tmp/.X11-unix/X-nonexistent-selftest")
    if bad or not bad_reason:
        print("selftest FAILED: a missing socket was not reported as a refusal")
        return 1
    print(f"selftest OK: one connection opened and released; refusals are reported ({bad_reason})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--required",
        type=int,
        default=DEFAULT_REQUIRED,
        help=f"exit non-zero below this much headroom (default {DEFAULT_REQUIRED})",
    )
    parser.add_argument(
        "--probe",
        type=int,
        default=64,
        help="how many connections to try; the ceiling is found below this (default 64)",
    )
    parser.add_argument("--quiet", action="store_true", help="one line, no holder table")
    parser.add_argument("--selftest", action="store_true", help="check the probe itself and exit")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    opened, reason = probe(args.probe)
    hit_ceiling = bool(reason)
    verdict = "OK" if opened >= args.required else "STARVED"
    print(
        f"x11-headroom: {verdict} -- {opened} more clients can connect"
        + (f" (refused at #{opened}: {reason})" if hit_ceiling else " (no ceiling found)")
    )

    if not args.quiet and opened < args.required:
        table = holders()
        if table:
            print(f"  ceiling {PROTOCOL_CEILING}; the slots are held by:")
            for count, name in table[:6]:
                print(f"    {count:5d}  {name}")
        print(
            "  Elden Ring opens one X11 connection per thread. Below this much headroom it dies\n"
            "  inside vkCreateSwapchainKHR with a black window and no crash dump -- which reads\n"
            "  as a broken mod build and is not one. Free slots before launching."
        )
    return 0 if opened >= args.required else 1


if __name__ == "__main__":
    sys.exit(main())
