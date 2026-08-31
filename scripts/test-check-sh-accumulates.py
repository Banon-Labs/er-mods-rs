#!/usr/bin/env python3
"""Gate: `scripts/check.sh` must COLLECT failures, not stop at the first one.

WHY THIS IS A GATE AND NOT A COMMENT
------------------------------------
check.sh ran under `set -e` until 2026-08-31, and the cost was measured twice in one day: it went
red at line 46 on a gate whose subject was mid-edit by another agent, and the ~130 gate invocations
after it produced NO VERDICT AT ALL -- not pass, not fail, nothing. That is precisely the failure
mode the suite exists to refuse, one level up: A GATE THAT NEVER EXECUTED IS INDISTINGUISHABLE FROM
A GATE THAT PASSED. Agents read "red at X" and reported their own work as green on the strength of
running a handful of checks by hand.

`set -e` also made POSITION into authority -- the same check is load-bearing at line 46 and
decorative at line 900 -- so nothing about a gate's classification is stable while it holds.

A note asking the next person not to re-add `set -e` is exactly the advisory that gets missed. So
the property is tested: the real preamble is lifted out of the real check.sh and driven over
synthetic suites, because testing a copy of it would prove nothing about the file that runs.

WHAT IT CHECKS
--------------
1. a failing step does not stop the suite -- later steps still run, and the LAST one runs;
2. every failure is reported at the end, with its line, and the exit code is non-zero;
3. a clean suite exits 0 and says so;
4. an explicit fail-fast guard (the justified exception, e.g. `command -v cupcake || exit 127`)
   reports the remaining steps as NOT RUN, loudly, and NAMES them one table row each -- silence
   there is the whole defect, and a bare count is not an answer anyone can act on;
5. `command -v <missing> && cmd` is NOT recorded as a failure, which is what `set -e` did too;
6. a KILLED step (`timeout`'s 124, or death by signal) is INCONCLUSIVE -- a third state, neither
   pass nor fail, because the step reached no verdict. Scoring it FAILED would let a check that
   never completed look sensitive to the tree;
7. NOT RUN alone, with nothing failing, still exits non-zero;
8. NON-VACUITY: with the ERR trap deleted from the lifted preamble, case 2 must FAIL. Without
   this, a preamble that had quietly stopped recording would still pass every case above.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_SH = ROOT / "scripts" / "check.sh"
END_OF_PREAMBLE = "# -------------------------------------------------------------------------------------------"
TIMEOUT_SECONDS = 30


def preamble() -> str:
    """The real check.sh preamble, verbatim. Testing a copy would prove nothing."""
    text = CHECK_SH.read_text(encoding="utf-8")
    if "set -euo pipefail" in text.split(END_OF_PREAMBLE)[0]:
        raise SystemExit(
            "check.sh has `set -euo pipefail` again: one red step would abort the suite and every "
            "check after it would produce no verdict at all. See this file's docstring."
        )
    if END_OF_PREAMBLE not in text:
        raise SystemExit(
            "check.sh no longer carries the accumulation preamble's end marker; this gate cannot "
            "lift it, and a gate that cannot read its subject must fail rather than pass."
        )
    return text.split(END_OF_PREAMBLE)[0] + END_OF_PREAMBLE + "\n"


def run_fixture(body: str, head: str | None = None) -> "tuple[int, str]":
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "fixture.sh"
        path.write_text((head if head is not None else preamble()) + body, encoding="utf-8")
        path.chmod(0o755)
        proc = subprocess.run(
            ["bash", str(path)], capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=td
        )
        return proc.returncode, proc.stdout + proc.stderr


MARKER = 'python3 -c "print(\'LAST_STEP_RAN\')"'
FAILING = 'python3 -c "raise SystemExit(1)"'
PASSING = 'python3 -c "pass"'
# Exit 143 = SIGTERM, which is how a harness/`timeout` reclaims a long-running step. The preamble
# must call that INCONCLUSIVE rather than FAILED: the step reached no verdict.
KILLED = 'python3 -c "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"'
END = "\n_check_reached_end=1\n"


def marker_printed(out: str) -> bool:
    """Did the marker step actually EXECUTE, as opposed to merely being quoted back?

    A plain `"LAST_STEP_RAN" in out` was the oracle until the summary grew a per-step table, which
    prints each step's SOURCE TEXT beside its state -- so a step reported `NOT RUN` puts the marker
    string on screen without ever having run, and the substring test called that a pass. The
    execution evidence is the marker on a line of its OWN, which is what `print()` produces and what
    a table row (indented, prefixed by its state) never does.
    """
    return re.search(r"^LAST_STEP_RAN$", out, re.M) is not None


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, why: str) -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {why}")
        if not cond:
            failures.append(why)

    # 1 + 2: two failures among five steps -- all five run, both are named, exit is non-zero.
    rc, out = run_fixture(
        f"{PASSING}\n{FAILING}\n{PASSING}\n{FAILING}\n{MARKER}\n{END}"
    )
    check(marker_printed(out), "a failing step does not stop the suite -- the LAST step still runs")
    check(rc != 0, "a suite with failures exits non-zero")
    check("FAILED        : 2" in out, "both failures are counted, not just the first")
    check(len(re.findall(r"^  line \d+", out, re.M)) == 2, "each failure is reported with its line")
    check("NOT RUN       : 0" in out, "a completed suite reports nothing as NOT RUN")

    # 3: clean suite.
    rc, out = run_fixture(f"{PASSING}\n{PASSING}\n{MARKER}\n{END}")
    check(rc == 0, "a clean suite exits 0")
    check("none failed" in out, "a clean suite says so explicitly")

    # 4: the justified fail-fast exception must announce what it skipped.
    rc, out = run_fixture(
        "command -v definitely-not-a-real-binary >/dev/null 2>&1 || { echo 'missing'; exit 127; }\n"
        f"{PASSING}\n{PASSING}\n{MARKER}\n{END}"
    )
    check(rc != 0, "an early fail-fast exit is non-zero")
    check(not marker_printed(out), "steps after a fail-fast exit really do not run")
    check("DID NOT REACH THE END" in out, "the suite says loudly that it stopped early")
    check(
        re.search(r"NOT RUN\s+: [1-9]", out) is not None,
        "the steps that did not run are COUNTED -- silence there is the whole defect",
    )
    # ...and NAMED. A count says how much has no verdict; only the per-step table says WHICH,
    # and "which" is the only form of that answer anyone can act on.
    check(
        len(re.findall(r"^  NOT RUN\s+line \d+", out, re.M)) == 3,
        "the steps that did not run are NAMED, one table row each, with their source line",
    )

    # 5: `missing-cmd && cmd` is not a failure, matching the old `set -e` behaviour.
    rc, out = run_fixture(
        "command -v definitely-not-a-real-binary >/dev/null 2>&1 && python3 -c \"raise SystemExit(1)\"\n"
        f"{MARKER}\n{END}"
    )
    check(rc == 0, "`missing-cmd && cmd` is not recorded as a failure (matches the old set -e)")

    # 6: A KILLED STEP IS A THIRD STATE. `timeout`'s 124 and death-by-signal (143 here) mean the
    # step never reached a verdict. Scoring that FAILED makes a check that never completed look
    # sensitive to the tree; scoring it passed is the defect this file exists to refuse. Two real
    # steps sit near this environment's 30s per-command cap, so the state is reachable in practice.
    rc, out = run_fixture(f"{PASSING}\n{KILLED}\n{MARKER}\n{END}")
    check(marker_printed(out), "a killed step does not stop the suite either")
    check("INCONCLUSIVE  : 1" in out, "a killed step is counted INCONCLUSIVE")
    check("FAILED        : 0" in out, "a killed step is NOT counted as a failure")
    check(rc != 0, "INCONCLUSIVE is not a pass: the suite still exits non-zero")

    # 7: NOT RUN alone, with nothing failing, must still be non-zero -- otherwise a suite that
    # skipped work reports success, which is the exact confusion this file was rewritten to end.
    rc, out = run_fixture(
        "command -v definitely-not-a-real-binary >/dev/null 2>&1 || { echo 'missing'; exit 127; }\n"
        f"{PASSING}\n{END}"
    )
    check(
        rc != 0 and "FAILED        : 0" in out,
        "a suite whose only defect is NOT RUN still exits non-zero",
    )

    # 8: NON-VACUITY. Delete the ERR trap from the lifted preamble; case 2 must now break.
    blinded = re.sub(r"^trap '_check_note_failure.*$", "", preamble(), flags=re.M)
    rc, out = run_fixture(f"{PASSING}\n{FAILING}\n{MARKER}\n{END}", head=blinded)
    check(
        rc == 0 and "FAILED        : 0" in out,
        "non-vacuity: with the ERR trap removed the failure IS missed, so these cases are "
        "watching the trap rather than passing on their own",
    )

    for f in failures:
        print(f"check-sh-accumulates FAILED: {f}", file=sys.stderr)
    print(f"[test-check-sh-accumulates] {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
