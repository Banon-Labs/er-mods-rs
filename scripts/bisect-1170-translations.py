#!/usr/bin/env python3
"""Find which translated 1.17 hook address takes the game down, by halving the enabled set.

`er-hook` translates a 1.16.2 detour address to its verified 1.17 equivalent
(`docs/recon/rva-map-1162-to-1170.verified.tsv`). Verification proves the FUNCTION is the same;
it cannot prove the HANDLER still is, because a handler also reads struct fields and calls
virtuals by slot, and 1.17 moved plenty of both. Measured: with 0 translations the game runs past
50s, with 26 it faults at ~11s. This narrows that down to the address responsible.

The oracle is `er-run-outcome.txt`, written beside the game executable by the product's exit
hooks, NOT the process disappearing -- a player quitting to desktop is indistinguishable from a
crash by process state alone, and reading it that way once cost 26 wrongly-quarantined addresses.

    outcome=fault       the run died by exception   -> the culprit is in the enabled set
    outcome=clean-quit  somebody quit the game      -> the round proved nothing, run it again
    outcome=running     process gone, no exit path  -> killed from outside; nothing proven
    (file absent)       the logger never installed  -> the build or profile is wrong

USAGE
    python3 scripts/bisect-1170-translations.py --enable 0x744dd0,0x7451c0,...
    python3 scripts/bisect-1170-translations.py --enable-first-half-of 0x744dd0,0x7451c0,...
    python3 scripts/bisect-1170-translations.py --report      # what the last run concluded

Each invocation rewrites the temporary section of the quarantine file, rebuilds every shell,
relaunches, and watches until the outcome file resolves or the watch window expires. The
permanent quarantine rows (the ones carrying real evidence) are preserved.
"""

import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFIED = os.path.join(ROOT, "docs", "recon", "rva-map-1162-to-1170.verified.tsv")
QUARANTINE = os.path.join(ROOT, "docs", "recon", "rva-1170-quarantine.tsv")
GAME_DIR = os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/ELDEN RING/Game"
)
OUTCOME = os.path.join(GAME_DIR, "er-run-outcome.txt")
PROFILE = os.path.expanduser("~/Elden/group-1170.me3")
LAUNCHER = os.path.expanduser("~/Elden/launch.sh")
BASE = 0x140000000
# Marker that owns the temporary rows, so a bisect round never eats an evidence-backed row.
TEMP_MARKER = "BISECT-TEMPORARY"
# The observed fault lands at ~11s. This watch window is several times that, so "survived" means
# survived well past where every failing run has died -- not merely "was still up briefly".
WATCH_SECONDS = 60
# Seconds between polls of the outcome file.
POLL_SECONDS = 3


def accepted_rvas():
    """1.16.2 RVAs the verifier judged the same function, which is what er-hook may translate."""
    out = []
    for line in open(VERIFIED, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 5 or fields[2] != "IDENTICAL" or int(fields[4]) < 12:
            continue
        out.append(int(fields[0], 16) - BASE)
    return sorted(set(out))


def rewrite_quarantine(enabled):
    """Keep every evidence-backed row; exclude everything accepted that is not in `enabled`."""
    kept = [
        line
        for line in open(QUARANTINE, encoding="utf-8").read().splitlines()
        if TEMP_MARKER not in line
    ]
    excluded = [rva for rva in accepted_rvas() if rva not in enabled]
    already = {line.split("\t")[0] for line in kept if line.startswith("0x")}
    with open(QUARANTINE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept).rstrip("\n") + "\n")
        for rva in excluded:
            if f"0x{rva:x}" in already:
                continue
            handle.write(
                f"0x{rva:x}\t{TEMP_MARKER}: excluded for this bisect round only. Not evidence; "
                "rewritten on the next round and removed when the bisect finishes.\n"
            )
    return excluded


def run(command, timeout):
    return subprocess.run(
        command, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )


def build():
    packages = run("python3 scripts/me3-dll-list.py --cargo-args", 30).stdout.strip()
    result = run(
        f"cargo xwin build --release --target x86_64-pc-windows-msvc {packages}", 280
    )
    if result.returncode != 0:
        sys.exit(f"build failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")


def translating_count():
    """How many pairs the freshly built table actually carries -- the round's real independent
    variable, read back rather than assumed."""
    import glob

    files = glob.glob(
        os.path.join(
            ROOT, "target/x86_64-pc-windows-msvc/release/build/er-hook-*/out/address_map_1170.rs"
        )
    )
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    return len(re.findall(r"^    \(0x", open(newest, encoding="utf-8").read(), re.M))


def launch_and_watch():
    run("timeout 25 python3 scripts/er-teardown.py", 30)
    if os.path.exists(OUTCOME):
        os.remove(OUTCOME)
    subprocess.Popen(
        f"cd {os.path.dirname(LAUNCHER)} && ME3_PROFILE={PROFILE} nohup {LAUNCHER} -s "
        "> /dev/null 2>&1 &",
        shell=True,
    )
    deadline = time.time() + WATCH_SECONDS
    outcome = "(file absent)"
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        if os.path.exists(OUTCOME):
            outcome = open(OUTCOME, encoding="utf-8").read().strip()
            if "outcome=running" not in outcome:
                return outcome, "resolved"
    return outcome, "watch window expired"


def main():
    parser = argparse.ArgumentParser(description="Bisect the translated 1.17 hook addresses.")
    parser.add_argument("--enable", help="comma-separated 1.16.2 RVAs to translate this round")
    parser.add_argument(
        "--enable-first-half-of",
        metavar="RVAS",
        help="comma-separated candidates; enables the first half (the usual bisect step)",
    )
    parser.add_argument("--report", action="store_true", help="print the current outcome file")
    args = parser.parse_args()

    if args.report:
        print(open(OUTCOME, encoding="utf-8").read().strip() if os.path.exists(OUTCOME) else "(file absent)")
        return 0

    if args.enable_first_half_of:
        candidates = [int(v, 0) for v in args.enable_first_half_of.split(",") if v.strip()]
        enabled = candidates[: len(candidates) // 2]
    elif args.enable:
        enabled = [int(v, 0) for v in args.enable.split(",") if v.strip()]
    else:
        enabled = []

    excluded = rewrite_quarantine(enabled)
    build()
    count = translating_count()
    print(f"enabled {len(enabled)} ({', '.join(hex(v) for v in enabled) or 'none'})")
    print(f"excluded {len(excluded)}; table carries {count} pairs")
    outcome, how = launch_and_watch()
    print(f"outcome: {outcome}   [{how}]")
    if "outcome=fault" in outcome:
        print("VERDICT: the culprit is IN the enabled set -- halve it and run again")
    elif "outcome=clean-quit" in outcome:
        print("VERDICT: somebody quit the game; this round proved nothing -- run it again")
    elif "outcome=running" in outcome:
        print("VERDICT: survived the watch window -- the culprit is in the EXCLUDED set")
    else:
        print("VERDICT: inconclusive -- no outcome file, so the product DLL never installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
