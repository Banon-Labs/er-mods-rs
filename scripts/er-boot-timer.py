#!/usr/bin/env python3
"""Time a launch from the bash epoch to a player standing in a world, without touching the game.

Why this exists rather than `scripts/er-readiness-watch.py`
-----------------------------------------------------------
That watcher is the right tool for an agent-owned probe: it reads the product's own telemetry
json, it owns teardown, and it fails a run that stalls. Both properties are wrong here.

The comparison this tool serves puts an autoloading `er_quickload.dll` run beside a run with no
natives at all, and a profile with no natives writes no telemetry json, so the richer oracle has
nothing to read and the two halves of the comparison would be measured by different instruments.
The second half is also driven by the user pressing Continue themselves, and a watcher that owns
shutdown would close the game under them.

So the oracle here is the one witness every profile can produce: the game's own
`WorldChrMan -> mainPlayerIns` walk, read out of `/proc/<pid>/mem`. Nothing is injected, no thread
is suspended, and the read is identical whether the process has fifteen of our dlls in it or none.
The addresses and the three-valued verdict come from `scripts/er_run_lib.py`, which is also what
`scripts/er-frida-up.py` gates on.

Telemetry is still read when a path is given, and its composite world-loaded verdict is recorded
as a second, richer milestone. It is never the milestone the runs are compared on, because only
some of them can produce it.

Milestones, each a delta in seconds from the launch epoch:

    t_process          `eldenring.exe` first appears in /proc
    t_image_base       the game image is mapped at its preferred base, so a read can be made
    t_world_chr_man    the `WorldChrMan` singleton is non-null, so a world is being built
    t_player_present   `mainPlayerIns` is non-null, so the character exists in that world
    t_player_settled   `mainPlayerIns` stayed non-null for the dwell, so it was not a blink
    t_telemetry_world  the product's own composite world-loaded verdict, when telemetry exists

Usage:
    python3 scripts/er-boot-timer.py --epoch-file <f> --label autoload-1 --out <f>.json
    python3 scripts/er-boot-timer.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import er_run_lib  # noqa: E402
from runtime_timeout_cap import runtime_timeout_cap_seconds  # noqa: E402

# How often the walk is retried. A read of two qwords costs microseconds, so the interval is set
# by how precisely a milestone wants to be placed rather than by what the read costs.
POLL_INTERVAL_SECONDS = 0.2

# How long `mainPlayerIns` has to stay non-null before the load counts as settled. The pointer is
# published while the world is still streaming, so the bare transition is the earliest honest
# answer and not the moment a player could act on it. Recording both keeps that difference visible
# instead of picking one and calling it readiness.
DEFAULT_DWELL_SECONDS = 3.0

MILESTONES = (
    "t_process",
    "t_image_base",
    "t_world_chr_man",
    "t_player_present",
    "t_player_settled",
    "t_telemetry_world",
)

DEBUG_EVENT_PREFIXES = {
    "runtime_config": "runtime-config: loaded",
    "default_save": "save-override: DEFAULT-USER-SAVE",
    "missing_save_picker": "save-override: no usable autoload save",
    "loadgame_scan_census": "loadgame-scan: census",
    "loadgame_scan_done": "loadgame-scan: done",
    "native_fullread_submit": "native-fullread: SUBMIT",
    "native_fullread_commit": "native-fullread: *** COMMIT",
}
DEBUG_LOG_LINE_RE = re.compile(r"^\[\+(\d+)ms\].*? dll:[0-9a-fA-F]+ (.*)$")


def telemetry_world_loaded(telemetry: dict | None) -> bool:
    """The product's own composite verdict, as far as a reader outside the process can check it.

    Deliberately a subset of `er-readiness-watch.py`'s predicate: the fields that depend on an
    expected save or an expected animation belong to a probe that staged those expectations, and
    this tool stages nothing.
    """
    if not isinstance(telemetry, dict):
        return False
    if telemetry.get("game_man_instance_resolved") is not True:
        return False
    grounded = telemetry.get("oracle_grounded") is True
    now_loading_clear = telemetry.get("oracle_now_loading") in (0, False)
    return bool(
        telemetry.get("oracle_player_present") is True
        and telemetry.get("oracle_block_id_valid") is True
        and telemetry.get("oracle_load_in_progress_b80") in (0, False)
        and (grounded or now_loading_clear)
    )


def resident_kb(pid: int) -> int | None:
    """The game's resident set size, as a profile-independent measure of boot progress.

    Both halves of the autoload-versus-vanilla comparison load the same game, the same archives and
    the same menu resources, so the curve of what the process has paged in is the same work in both
    -- unlike any oracle inside our dll, which only one half has. If the two curves are offset in
    time, the boot itself is slower with the dll loaded, and that offset is the part of the gap that
    has nothing to do with the autoload logic.
    """
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def io_counters(pid: int) -> tuple[int, int, int, int] | None:
    """`(syscr, syscw, read_bytes, write_bytes)` from `/proc/<pid>/io`.

    The two syscall counts and the two byte counts answer different questions and both are needed.
    A process can issue a million writes that never reach a platter -- the page cache absorbs them,
    `write_bytes` barely moves, and the cost was entirely in kernel transitions. It can equally
    issue one read that costs a hundred milliseconds of actual disk. Recording both is what lets a
    redundant-io claim say which kind it was instead of picking whichever number is larger.

    `read_bytes`/`write_bytes` are what the block layer actually moved, so cached reads do not
    appear in them and a file rewritten every frame shows up in `syscw` long before `write_bytes`.
    """
    try:
        values: dict[str, int] = {}
        for line in Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition(":")
            values[name.strip()] = int(value)
        return (
            values.get("syscr", 0),
            values.get("syscw", 0),
            values.get("read_bytes", 0),
            values.get("write_bytes", 0),
        )
    except (OSError, ValueError):
        return None


def cpu_ticks(pid: int) -> int | None:
    """Total user + system jiffies the process has burned, across all its threads.

    Separates "our code is doing work" from "our code is waiting". A stretch where the game is
    pinned near a full core is spending the time computing; a stretch where it burns almost nothing
    is blocked on something, and a wait is a different fix from a slow routine. `utime` and `stime`
    are fields 14 and 15 of `/proc/<pid>/stat`, counted after the comm field, which is parenthesised
    and may itself contain spaces -- hence the split on the last `)` rather than on whitespace.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2 :].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, ValueError, IndexError):
        return None


def read_debug_events(path: Path | None, epoch: float) -> dict[str, dict[str, object]]:
    """Compact event-boundary summary from the product debug log for this launch.

    Final world timings alone say the load was slower; they do not say whether the delay moved
    before the native submit, during the drain, or after the commit. The debug log is the only
    cheap source for those internal boundaries, so each timing artifact records counts plus
    first/last log milliseconds for the small set of events that explain boot-time regressions.
    """
    if path is None:
        return {}
    try:
        if path.stat().st_mtime < epoch:
            return {}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    events: dict[str, dict[str, object]] = {}
    for line in lines:
        match = DEBUG_LOG_LINE_RE.match(line)
        if not match:
            continue
        ms = int(match.group(1))
        message = match.group(2)
        if message.startswith("repeat: "):
            message = message.removeprefix("repeat: ")
        for name, prefix in DEBUG_EVENT_PREFIXES.items():
            if not message.startswith(prefix):
                continue
            event = events.setdefault(
                name,
                {"count": 0, "first_ms": ms, "last_ms": ms, "first": message, "last": message},
            )
            event["count"] = int(event["count"]) + 1
            event["last_ms"] = ms
            event["last"] = message
            break
    return events


def read_telemetry(path: Path | None, epoch: float) -> dict | None:
    """The run's telemetry, or `None` when the file on disk belongs to an earlier run.

    The game-directory artifact is a single slot: the dll renames it to `.prev` and truncates on
    its first write, so between a launch and that write the path still holds the previous run's
    json.
    A previous run that reached a world leaves a world-loaded verdict sitting there, and a reader
    that does not check the mtime stamps its world milestone at roughly zero seconds -- a boot
    time of nothing at all, sourced from a run that finished hours ago.
    """
    if path is None:
        return None
    try:
        if path.stat().st_mtime < epoch:
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def watch(
    epoch: float,
    label: str,
    out_path: Path | None,
    telemetry_path: Path | None,
    debug_log_path: Path | None,
    dwell: float,
    max_seconds: float,
) -> dict:
    timing: dict[str, float] = {}
    record: dict[str, object] = {
        "label": label,
        "epoch": epoch,
        "dwell_seconds": dwell,
        "max_seconds": max_seconds,
        "timing": timing,
        "oracle": "WorldChrMan -> mainPlayerIns via /proc/<pid>/mem",
        "outcome": "watching",
    }

    def flush() -> None:
        if debug_log_path is not None:
            events = read_debug_events(debug_log_path, epoch)
            if events:
                record["debug_events"] = events
        if out_path is not None:
            out_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")

    def stamp(name: str) -> None:
        if name not in timing:
            timing[name] = round(time.time() - epoch, 3)
            flush()

    pid: int | None = None
    base: int | None = None
    player_since: float | None = None
    deadline = epoch + max_seconds
    # Each entry is `[seconds since launch, resident kb, cumulative user+system jiffies]`.
    rss_trace: list[list[float]] = []
    record["rss_trace"] = rss_trace
    flush()

    while time.time() < deadline:
        if pid is None or not Path(f"/proc/{pid}").exists():
            found = er_run_lib.game_pid()
            if found is not None and found != pid:
                pid, base, player_since = found, None, None
                record["pid"] = pid
                stamp("t_process")
        if pid is not None and base is None:
            base = er_run_lib.game_image_base(pid)
            if base is not None:
                record["image_base"] = hex(base)
                stamp("t_image_base")
        if pid is not None and base is not None:
            verdict, detail = er_run_lib.player_in_a_world_at(pid, base)
            record["last_detail"] = detail
            if verdict is True or "mainPlayerIns" in detail:
                stamp("t_world_chr_man")
            if verdict is True:
                stamp("t_player_present")
                if player_since is None:
                    player_since = time.time()
                elif time.time() - player_since >= dwell:
                    stamp("t_player_settled")
            else:
                player_since = None
        if pid is not None:
            kb = resident_kb(pid)
            if kb is not None:
                io = io_counters(pid) or (0, 0, 0, 0)
                rss_trace.append(
                    [round(time.time() - epoch, 2), kb, cpu_ticks(pid) or 0, *io]
                )
        telemetry = read_telemetry(telemetry_path, epoch)
        if telemetry is not None:
            record["telemetry_present"] = True
            if telemetry_world_loaded(telemetry):
                stamp("t_telemetry_world")
        if "t_player_settled" in timing and (
            telemetry_path is None or "t_telemetry_world" in timing
        ):
            record["outcome"] = "settled"
            flush()
            return record
        time.sleep(POLL_INTERVAL_SECONDS)

    record["outcome"] = "settled" if "t_player_settled" in timing else "deadline"
    flush()
    return record


def selftest() -> int:
    failures = 0
    checks: list[tuple[str, bool]] = []

    already_over = watch(
        epoch=time.time(),
        label="selftest-no-window",
        out_path=None,
        telemetry_path=None,
        debug_log_path=None,
        dwell=0.1,
        max_seconds=0.0,
    )
    checks.append(
        (
            "a window that is already over records no milestone and says deadline",
            already_over["outcome"] == "deadline" and already_over["timing"] == {},
        )
    )
    checks.append(
        ("the composite telemetry verdict refuses a non-dict", telemetry_world_loaded(None) is False)
    )
    checks.append(
        (
            "the composite telemetry verdict refuses an unresolved game-man",
            telemetry_world_loaded(
                {"oracle_player_present": True, "oracle_block_id_valid": True}
            )
            is False,
        )
    )
    checks.append(
        (
            "the composite telemetry verdict accepts a loaded world",
            telemetry_world_loaded(
                {
                    "game_man_instance_resolved": True,
                    "oracle_player_present": True,
                    "oracle_block_id_valid": True,
                    "oracle_load_in_progress_b80": 0,
                    "oracle_grounded": True,
                }
            )
            is True,
        )
    )
    checks.append(
        (
            "the composite telemetry verdict refuses a world still loading",
            telemetry_world_loaded(
                {
                    "game_man_instance_resolved": True,
                    "oracle_player_present": True,
                    "oracle_block_id_valid": True,
                    "oracle_load_in_progress_b80": 1,
                    "oracle_grounded": True,
                }
            )
            is False,
        )
    )
    stale = Path(__file__).resolve().parent.parent / "target" / "er-boot-timer-selftest-telemetry.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    loaded_world = {
        "game_man_instance_resolved": True,
        "oracle_player_present": True,
        "oracle_block_id_valid": True,
        "oracle_load_in_progress_b80": 0,
        "oracle_grounded": True,
    }
    stale.write_text(json.dumps(loaded_world), encoding="utf-8")
    checks.append(
        (
            "telemetry older than the launch epoch is refused, not read as this run's world",
            read_telemetry(stale, stale.stat().st_mtime + 1.0) is None,
        )
    )
    checks.append(
        (
            "telemetry written after the launch epoch is read",
            read_telemetry(stale, stale.stat().st_mtime - 1.0) == loaded_world,
        )
    )
    stale.unlink(missing_ok=True)

    sample_events = read_debug_events(
        None,
        0.0,
    )
    checks.append(("debug event reader accepts a missing path", sample_events == {}))
    sample_log = Path(__file__).resolve().parent.parent / "target" / "er-boot-timer-selftest-debug.log"
    sample_log.write_text(
        "\n".join(
            [
                "[+11964ms] 2026-09-21 dll:d05eb5fb loadgame-scan: done hits=0 rows_walked=2 found_member_node=0x0 found_item=0x0",
                "[+11965ms] 2026-09-21 dll:d05eb5fb native-fullread: SUBMIT slot=0 b78=0 -> DRAIN",
                "[+12982ms] 2026-09-21 dll:d05eb5fb native-fullread: *** COMMIT continue_confirm 0x140b0f890",
            ]
        ),
        encoding="utf-8",
    )
    events = read_debug_events(sample_log, sample_log.stat().st_mtime - 1.0)
    checks.append(
        (
            "debug event reader records native full-read boundaries",
            events.get("native_fullread_submit", {}).get("first_ms") == 11965
            and events.get("native_fullread_commit", {}).get("first_ms") == 12982,
        )
    )
    sample_log.unlink(missing_ok=True)

    for label, ok in er_run_lib.world_read_selftest():
        checks.append((f"er_run_lib: {label}", ok))

    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        failures += 0 if ok else 1

    pid = er_run_lib.game_pid()
    if pid is None:
        print("  ....  no eldenring.exe is running, so the live walk went unexercised")
    else:
        base = er_run_lib.game_image_base(pid)
        if base is None:
            print(f"  ....  live pid {pid}: the game image is not mapped at its preferred base")
        else:
            verdict, detail = er_run_lib.player_in_a_world_at(pid, base)
            print(f"  ....  live pid {pid}: {verdict} -- {detail}")

    print(f"\n{'selftest failed' if failures else 'selftest passed'}: {failures} failing check(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--epoch", type=float, help="launch epoch, unix seconds")
    parser.add_argument("--epoch-file", help="file holding the launch epoch")
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", help="json file for the milestones, rewritten as each one lands")
    parser.add_argument(
        "--telemetry", help="er-quickload-telemetry.json, when the profile writes one"
    )
    parser.add_argument(
        "--debug-log",
        help="er-quickload-autoload-debug.log. Defaults to a sibling of --telemetry when present.",
    )
    parser.add_argument("--dwell", type=float, default=DEFAULT_DWELL_SECONDS)
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=float(runtime_timeout_cap_seconds()),
        help="observation window. This tool never tears anything down, so the window only bounds "
        "how long it watches, and a user-driven run may want a longer one.",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.epoch is None and args.epoch_file is None:
        parser.error("one of --epoch or --epoch-file is required")
    epoch = args.epoch
    if epoch is None:
        epoch = float(Path(args.epoch_file).read_text(encoding="utf-8").strip())

    telemetry_path = Path(args.telemetry) if args.telemetry else None
    debug_log_path = Path(args.debug_log) if args.debug_log else None
    if debug_log_path is None and telemetry_path is not None:
        debug_log_path = telemetry_path.with_name("er-quickload-autoload-debug.log")

    record = watch(
        epoch=epoch,
        label=args.label,
        out_path=Path(args.out) if args.out else None,
        telemetry_path=telemetry_path,
        debug_log_path=debug_log_path,
        dwell=args.dwell,
        max_seconds=args.max_seconds,
    )
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record["outcome"] == "settled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
