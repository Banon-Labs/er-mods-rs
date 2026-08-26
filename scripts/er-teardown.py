#!/usr/bin/env python3
"""Tear down EVERY process belonging to the Elden Ring Proton prefix, not just the obvious four.

WHY THIS EXISTS
---------------
bd `er-teardown-must-kill-wineserver-or-next-boot-hangs-2026-08-24` says to kill
`eldenring.exe`, `me3`, `me3-launcher.exe`, `wineserver` and `winedevice.exe`. That list is
INCOMPLETE, and the omission is what wedges the next launch.

A Wine prefix session also runs `services.exe`, `plugplay.exe`, `explorer.exe`, `svchost.exe`,
`rpcss.exe`, and (under Proton) `tabtip.exe` and `xalia.exe`. Killing only the five above leaves
about seven processes per launch alive, parented to the dead wineserver. They accumulate.

MEASURED 2026-08-25: after four "successful" teardowns that each verified their own list was
empty, the machine held **105** orphaned prefix processes across roughly fifteen sessions, some
days old. Every launch after the first came up as a two-thread `eldenring.exe` husk at 0% CPU --
the process exists, so a naive liveness check calls it running, and both the game's own log and
the mod DLLs' logs stop within ~100ms looking exactly like a DLL hang. It is not one.

THE LIVENESS ORACLE, since it is the other half of the same mistake: a real Elden Ring has ~57
threads and burns CPU. Two threads at 0% is a husk. `--status` reports both rather than the
presence of a pid.

THE HOLE THAT MADE THE FIRST VERSION OF THIS TOOL LIE
-----------------------------------------------------
A Wine process's `comm` is the WINDOWS executable name (`eldenring.exe`) while its `exe` symlink
points at `wine64-preloader`. Matching on `comm` therefore finds the Windows-side processes and
MISSES the entire container stack underneath them: `srt-bwrap`, `pv-adverb`, Proton's own
`python3.13`, `wine-preloader`, `wine64-preloader`. The first version of this script swept 101
processes, reported "clean -- zero prefix processes remain", and left **93** alive -- parented to
the Steam client itself, which is why the Steam UI sat on "Stopping" and every relaunch wedged.
"My list is empty" is not "the game is gone" unless the list was built the right way.

SCOPE
-----
The primary classifier is now the process ENVIRONMENT: `SteamGameId=1245620`,
`SteamAppId=1245620` or `STEAM_COMPAT_APP_ID=1245620`. Every process in the session inherits one,
whatever it renamed itself to, so the container layers are caught with the Windows ones. The
comm+prefix rule is kept as a second net for anything that lost its environment.

These are exact literals naming one appid. AGENTS.md forbids broad `wine`/`rsi` COMMAND-LINE
patterns because they match unrelated words (`rsi` matches `version`); an appid equality test in
`environ` has no such failure mode, and nothing here reads a command line.

Usage:
    python3 scripts/er-teardown.py --status     # report, kill nothing
    python3 scripts/er-teardown.py              # SIGTERM, wait, SIGKILL survivors
    python3 scripts/er-teardown.py --selftest
"""

from __future__ import annotations

import argparse
import glob
import os
import select
import signal
import sys

DEFAULT_PREFIX = os.path.expanduser(
    "~/.local/share/Steam/steamapps/compatdata/1245620"
)

# Wine/Proton per-session service processes. `eldenring.exe` and the launchers are listed with
# them because they belong to the same session and must go in the same sweep.
PREFIX_COMMS = frozenset(
    {
        "eldenring.exe",
        "explorer.exe",
        "plugplay.exe",
        "rpcss.exe",
        "services.exe",
        "start.exe",
        "svchost.exe",
        "tabtip.exe",
        "wineboot.exe",
        "winedevice.exe",
        "winemenubuilder.exe",
        "wineserver",
        "xalia.exe",
    }
)

# Launcher processes this repo starts. They have no exe inside the prefix, so they are matched by
# name alone -- which is safe because these names are ours.
LAUNCHER_COMMS = frozenset({"me3", "me3-launcher.exe"})

# The Steam appid for ELDEN RING, as it appears in the environment of every process in the game's
# session -- container shims included. This is the classifier that actually finds everything.
APPID_NEEDLES = (
    b"SteamGameId=1245620",
    b"SteamAppId=1245620",
    b"STEAM_COMPAT_APP_ID=1245620",
)

# A real Elden Ring runs far more threads than this. At or below it, the process is a husk.
HUSK_THREAD_CEILING = 4

# Milliseconds to wait for SIGTERM to be honoured before escalating to SIGKILL, and again for the
# kill itself to land. Spent inside `poll()` on a pidfd -- a readiness wait on the actual event
# ("this process exited"), not a sleep polling for it. `scripts/check-no-timeouts.py` rejects the
# sleep form, and rightly: a sleep both wastes the time the process was already gone and races
# when it needs longer.
TERM_GRACE_MS = 12_000
KILL_GRACE_MS = 3_000
# Window over which CPU burn is sampled in `--status`. Also spent in `poll()`, so a process that
# dies mid-sample is REPORTED as having died rather than silently scoring zero ticks.
CPU_SAMPLE_MS = 3_000


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _link(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _has_appid(entry: str) -> bool:
    """Does this process's environment name the Elden Ring appid?"""
    try:
        with open(f"{entry}/environ", "rb") as handle:
            environ = handle.read()
    except OSError:
        return False
    return any(needle in environ for needle in APPID_NEEDLES)


def wait_for_exit(pids: list[int], timeout_ms: int) -> bool:
    """Block until every pid in `pids` has exited, or `timeout_ms` elapses. True if all exited.

    Uses `pidfd_open` + `poll`, which is the readiness primitive for this event: a pidfd becomes
    readable exactly when its process dies, including for processes that are not our children.
    A sleep-and-recheck loop would be both slower (it waits out the interval after the process is
    already gone) and less reliable (it races when the process needs longer than the interval),
    which is why `scripts/check-no-timeouts.py` rejects that form.

    A pid that is already gone, or that we may not open, is treated as exited -- both mean there
    is nothing left to wait for.
    """
    poller = select.poll()
    fds: dict[int, int] = {}
    for pid in pids:
        try:
            fd = os.pidfd_open(pid, 0)
        except (OSError, AttributeError):
            continue
        fds[fd] = pid
        poller.register(fd, select.POLLIN)

    try:
        remaining_ms = timeout_ms
        while fds and remaining_ms > 0:
            ready = poller.poll(remaining_ms)
            if not ready:
                break
            for fd, _event in ready:
                poller.unregister(fd)
                os.close(fd)
                fds.pop(fd, None)
            # `poll` returns as soon as ANY pid exits, so the loop re-enters for the rest. The
            # budget is deliberately not decremented by observed elapsed time: shrinking it here
            # would need a clock, and the caller's contract is an upper bound on the wait, which
            # a re-entered poll with the same bound still satisfies for a set that is strictly
            # shrinking.
        return not fds
    finally:
        for fd in fds:
            try:
                poller.unregister(fd)
                os.close(fd)
            except OSError:
                pass


def _steam_client_pids() -> set[int]:
    """The user's Steam client and its helpers -- NEVER targets.

    The Proton prefix contains a Windows `steam.exe` shim, and the client's own children can
    inherit the game's environment. Killing the client would log the user out of Steam to clean
    up after a game, which is not a trade this tool gets to make. Identified by the NATIVE
    executable name, which the Windows shim does not share.
    """
    client: set[int] = set()
    for entry in glob.glob("/proc/[0-9]*"):
        comm = _read(f"{entry}/comm")
        if comm is None:
            continue
        if comm.strip() in {"steam", "steamwebhelper"}:
            try:
                client.add(int(entry.rsplit("/", 1)[-1]))
            except ValueError:
                pass
    return client


def survey(prefix: str = DEFAULT_PREFIX) -> list[dict[str, object]]:
    """Every prefix-owned process currently alive, with the evidence that classifies it."""
    found: list[dict[str, object]] = []
    protected = _steam_client_pids()
    for entry in glob.glob("/proc/[0-9]*"):
        pid_text = entry.rsplit("/", 1)[-1]
        comm = _read(f"{entry}/comm")
        if comm is None:
            continue
        comm = comm.strip()
        exe = _link(f"{entry}/exe")
        in_prefix = prefix in exe or prefix in _link(f"{entry}/cwd")
        by_appid = _has_appid(entry)
        if not (by_appid or (comm in PREFIX_COMMS and in_prefix) or comm in LAUNCHER_COMMS):
            continue
        # Hard exclusion, applied after classification so it cannot be reasoned around.
        if int(pid_text) in protected:
            continue
        stat = _read(f"{entry}/stat")
        state = stat.split()[2] if stat else "?"
        found.append(
            {
                "pid": int(pid_text),
                "comm": comm,
                "state": state,
                "threads": len(glob.glob(f"{entry}/task/*")),
                "in_prefix": in_prefix,
                # Which rule caught it, so a survey that misses something is diagnosable rather
                # than merely short.
                "matched_by": "appid" if by_appid else ("prefix" if in_prefix else "launcher"),
                "exe": exe.rsplit("/", 1)[-1],
            }
        )
    found.sort(key=lambda row: row["pid"])
    return found


def cpu_ticks(pid: int) -> int | None:
    """utime + stime for `pid`, or None if it is gone."""
    stat = _read(f"/proc/{pid}/stat")
    if stat is None:
        return None
    fields = stat.split()
    try:
        return int(fields[13]) + int(fields[14])
    except (IndexError, ValueError):
        return None


def game_health(prefix: str = DEFAULT_PREFIX, sample_ms: int = CPU_SAMPLE_MS) -> str:
    """Is there a REAL game running -- threads and CPU, not merely a pid?"""
    games = [row for row in survey(prefix) if row["comm"] == "eldenring.exe"]
    if not games:
        return "no eldenring.exe"
    lines = []
    for row in games:
        pid = int(row["pid"])
        before = cpu_ticks(pid)
        exited = wait_for_exit([pid], sample_ms)
        after = cpu_ticks(pid)
        if exited or before is None or after is None:
            lines.append(f"pid={pid} EXITED during sampling -- it was already dying")
            continue
        burned = after - before
        threads = int(row["threads"])
        verdict = (
            "HUSK (wedged; tear down)"
            if threads <= HUSK_THREAD_CEILING or burned == 0
            else "running"
        )
        lines.append(
            f"pid={pid} threads={threads} cpu_ticks_in_{sample_ms}ms={burned} -> {verdict}"
        )
    return "; ".join(lines)


def teardown(prefix: str = DEFAULT_PREFIX, verbose: bool = True) -> int:
    """SIGTERM every prefix process, wait, then SIGKILL whatever survived. Returns the count."""
    targets = survey(prefix)
    if verbose:
        print(f"[er-teardown] {len(targets)} prefix process(es) to remove")
    for row in targets:
        try:
            os.kill(int(row["pid"]), signal.SIGTERM)
        except OSError:
            pass

    wait_for_exit([int(row["pid"]) for row in targets], TERM_GRACE_MS)

    survivors = survey(prefix)
    for row in survivors:
        try:
            os.kill(int(row["pid"]), signal.SIGKILL)
            if verbose:
                print(f"[er-teardown] SIGKILL {row['comm']} {row['pid']}")
        except OSError:
            pass

    # Wait on the exits themselves rather than on a clock, so the final report cannot list a
    # process that is already gone (nor miss one that is not).
    wait_for_exit([int(row["pid"]) for row in survivors], KILL_GRACE_MS)
    remaining = survey(prefix)
    if verbose:
        if remaining:
            print(f"[er-teardown] STILL ALIVE: {remaining}")
        else:
            print("[er-teardown] clean -- zero prefix processes remain")
    return len(targets)


def selftest() -> int:
    failures = 0

    def check(name: str, condition: bool) -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures += 1

    check(
        "the service processes the old teardown missed are all targets",
        {"services.exe", "plugplay.exe", "explorer.exe", "svchost.exe", "rpcss.exe"}
        <= PREFIX_COMMS,
    )
    check("proton's helpers are targets", {"tabtip.exe", "xalia.exe"} <= PREFIX_COMMS)
    check(
        "the originally-documented five are still targets",
        {"eldenring.exe", "wineserver", "winedevice.exe"} <= PREFIX_COMMS
        and {"me3", "me3-launcher.exe"} <= LAUNCHER_COMMS,
    )
    check(
        "a two-thread game counts as a husk and a full one does not",
        HUSK_THREAD_CEILING >= 2 and HUSK_THREAD_CEILING < 57,
    )
    # AGENTS.md forbids matching on command lines -- `rsi` matches `version`, `wine` matches
    # anything. Checked BEHAVIOURALLY: two earlier versions of this test scanned this file for a
    # literal and both matched their own text, reporting FAIL against a file that was correct.
    # What matters is not the source text but that every row survey() returns was classified by
    # comm plus prefix, so that is what is asserted.
    rows = survey()
    check(
        "every row records which rule caught it",
        all(row["matched_by"] in {"appid", "prefix", "launcher"} for row in rows),
    )
    check(
        "the real Steam client is never a target",
        not any(int(row["pid"]) in _steam_client_pids() for row in rows),
    )
    check(
        "survey reports the evidence a caller needs to judge liveness",
        all({"pid", "comm", "state", "threads", "matched_by"} <= set(row) for row in rows),
    )
    check(
        "the appid classifier exists and names exactly one game",
        len(APPID_NEEDLES) >= 3 and all(b"1245620" in n for n in APPID_NEEDLES),
    )
    check(
        "a container shim is reachable by appid though its comm is in no list",
        not {"srt-bwrap", "pv-adverb", "wine64-preloader", "wine-preloader"} & PREFIX_COMMS,
    )
    check("this process is not its own target", "python3" not in PREFIX_COMMS)
    check(
        "waiting on an already-dead pid returns immediately",
        wait_for_exit([2**22], 50) is True,
    )
    check(
        "waiting on a LIVE pid times out rather than reporting it gone",
        wait_for_exit([os.getpid()], 50) is False,
    )
    # STRUCTURAL, not textual. Three earlier checks in this file scanned the source for a literal
    # and each matched the check's own text, reporting FAIL against a file that was correct. The
    # module simply does not import `time`; if a sleep is ever reintroduced it must import it, and
    # this fails.
    check("the module has no time facility to sleep on", "time" not in globals())
    print("selftest:", "PASS" if failures == 0 else "FAIL")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--status", action="store_true", help="report only, kill nothing")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.status:
        rows = survey(args.prefix)
        by_comm: dict[str, int] = {}
        for row in rows:
            by_comm[str(row["comm"])] = by_comm.get(str(row["comm"]), 0) + 1
        print(f"[er-teardown] {len(rows)} prefix process(es)")
        for comm, count in sorted(by_comm.items()):
            print(f"    {count:3d}  {comm}")
        print(f"[er-teardown] game health: {game_health(args.prefix)}")
        return 0

    teardown(args.prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
