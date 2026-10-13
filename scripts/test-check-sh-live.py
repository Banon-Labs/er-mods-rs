#!/usr/bin/env python3
"""Gate: check.sh's live fan-out reports progress, always ends, and keeps every verdict.

On a terminal, `scripts/check.sh` follows its stages with one redrawn line instead of printing
nothing between stage verdicts (the pushes of 2026-10-13 sat silent for the nine minutes the
`addresses` stage took, and the user could not tell slow from hung). The machinery is a pipe: each
stage child writes `step <stage> <reached> <total> <text>` as each of its steps starts and its
wrapper writes `done` when it ends, and the parent redraws on every line. A pipe-driven loop has
two ways to go wrong that a reviewer will not see, so they are tested here against the real
functions, lifted out of the real check.sh rather than copied:

1. the parent blocks forever -- a stage whose `done` never arrives, or a loop that miscounts;
2. the numbers lie -- a step counted twice, or another stage's row counted as this one's, which
   runs the numerator past the denominator and makes the line meaningless.

What it checks
--------------
1. a stage child reports exactly its own rows, once each, in order, with its own total, and takes
   ER_CHECK_PROGRESS_FD off its environment before any step can inherit it;
2. a stage child whose reader has gone dies of SIGPIPE at its next report, so a closed terminal
   does not leave stages running orphaned;
3. the live fan-out over three fake stages (one red) ends, returns 0, prints each stage's verdict
   exactly once with the red one marked, and draws a progress line naming the running step in its
   short form;
4. non-vacuity: with the row filter taken out of the lifted reporter, case 1's count changes, so
   case 1 is watching the filter rather than passing on its own.

No game data, no network, no real suite: every stage is a fixture of this test's own making.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_SH = ROOT / "scripts" / "check.sh"
TIMEOUT_SECONDS = 30
LIFTED_FUNCTIONS = ("_check_report_progress", "_check_run_stage", "_check_live_short",
                    "_check_live_draw", "_check_fanout_live")
ROW_FILTER = "[[ -n ${_check_row_at[$1]:-} && -z ${_check_reached_at[$1]:-} ]] || return 0"
ROW_FILTER_REMOVED = "[[ -z ${_check_reached_at[$1]:-} ]] || return 0"

# The stage half of the fixture: what a real stage child's preamble and DEBUG trap amount to for
# progress. Rows 10-12 are this stage's; 99 is another stage's row, reported and to be ignored.
STAGE_MODE = r'''
if [[ ${1:-} == --stage ]]; then
	_check_stage=$2
	_check_progress_fd=${ER_CHECK_PROGRESS_FD:-}
	unset ER_CHECK_PROGRESS_FD
	declare -A _check_reached_at=()
	declare -A _check_row_at=([10]=1 [11]=1 [12]=1)
	_check_progress_total=${#_check_row_at[@]}
	_check_report_progress 10 'python3 "$repo_root/scripts/first.py" --selftest'
	_check_report_progress 10 'python3 "$repo_root/scripts/first.py" --selftest'
	_check_report_progress 99 'python3 "$repo_root/scripts/other-stage.py"'
	_check_report_progress 11 'bash "$repo_root/scripts/second.sh"'
	[[ -n ${ER_CHECK_PROGRESS_FD:-} ]] && exit 9
	[[ $_check_stage == red ]] && exit 3
	_check_report_progress 12 'cargo test -p fixture'
	exit 0
fi
'''

FAKE_STAGES = '''#!/usr/bin/env python3
import sys
if "--counts" in sys.argv:
    print("   3  alpha\\n   3  red\\n   3  gamma\\n   9  total")
'''


def lift(source: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source, re.S | re.M)
    if not match:
        sys.exit(f"[test-check-sh-live] FAIL: no function {name}() in {CHECK_SH}")
    return match.group(0)


def lift_live_state(source: str) -> str:
    match = re.search(r"^declare -A _check_live_total=.*?^_check_live_cols=\d+\n", source, re.S | re.M)
    if not match:
        sys.exit(f"[test-check-sh-live] FAIL: no live-state declarations in {CHECK_SH}")
    return match.group(0)


def write_fixture(tmp: Path, reporter_patch: tuple[str, str] | None = None) -> Path:
    source = CHECK_SH.read_text(encoding="utf-8")
    functions = "".join(lift(source, name) for name in LIFTED_FUNCTIONS)
    if reporter_patch:
        old, new = reporter_patch
        if old not in functions:
            sys.exit("[test-check-sh-live] FAIL: the row filter this test mutates is no longer in "
                     "_check_report_progress; update ROW_FILTER")
        functions = functions.replace(old, new)
    (tmp / "scripts").mkdir(exist_ok=True)
    (tmp / "scripts" / "check-stages.py").write_text(FAKE_STAGES, encoding="utf-8")
    fixture = tmp / "fixture.sh"
    fixture.write_text("\n".join([
        "#!/usr/bin/env bash",
        "set -uo pipefail",
        f"repo_root={tmp}",
        "_check_jobs=2",
        lift_live_state(source),
        functions,
        STAGE_MODE,
        'mkdir -p "$repo_root/out"',
        '_check_fanout_live "$repo_root/out" "$repo_root/cache" alpha red gamma',
        'echo "fanout rc=$?"',
    ]) + "\n", encoding="utf-8")
    return fixture


def stage_events(fixture: Path, stage: str) -> tuple[int, list[list[str]]]:
    read_end, write_end = os.pipe()
    env = {**os.environ, "ER_CHECK_PROGRESS_FD": str(write_end)}
    proc = subprocess.run(["bash", str(fixture), "--stage", stage], env=env, pass_fds=(write_end,),
                          capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    os.close(write_end)
    with os.fdopen(read_end, encoding="utf-8") as events:
        rows = [line.rstrip("\n").split("\t") for line in events]
    return proc.returncode, rows


def main() -> int:
    failures = 0

    def check(what: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {what}" + (f"\n        {detail}" if detail and not ok else ""))

    with tempfile.TemporaryDirectory(prefix="check-sh-live-") as tmp_name:
        tmp = Path(tmp_name)
        fixture = write_fixture(tmp)

        rc, rows = stage_events(fixture, "alpha")
        want = [["step", "alpha", "1", "3", 'python3 "$repo_root/scripts/first.py" --selftest'],
                ["step", "alpha", "2", "3", 'bash "$repo_root/scripts/second.sh"'],
                ["step", "alpha", "3", "3", "cargo test -p fixture"]]
        check("a stage reports its own rows once each, in order, with its own total", rows == want,
              f"got {rows}")
        check("the stage took ER_CHECK_PROGRESS_FD off its environment and exited 0", rc == 0, f"exit {rc}")

        read_end, write_end = os.pipe()
        os.close(read_end)
        env = {**os.environ, "ER_CHECK_PROGRESS_FD": str(write_end)}
        proc = subprocess.run(["bash", str(fixture), "--stage", "alpha"], env=env, pass_fds=(write_end,),
                              capture_output=True, timeout=TIMEOUT_SECONDS)
        os.close(write_end)
        check("a stage whose reader has gone dies of SIGPIPE at its next report",
              proc.returncode == -signal.SIGPIPE, f"exit {proc.returncode}")

        try:
            # Bytes, not text: text mode turns every `\r` into `\n`, which is the one character
            # the redrawn line is made of.
            proc = subprocess.run(["bash", str(fixture)], capture_output=True,
                                  timeout=TIMEOUT_SECONDS, start_new_session=True)
        except subprocess.TimeoutExpired:
            check(f"the live fan-out ends (it was still running after {TIMEOUT_SECONDS}s)", False)
            print(f"[test-check-sh-live] {failures + 1} failure(s)")
            return 1
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        check("the live fan-out ends and returns 0", "fanout rc=0" in out, f"stdout {out!r}")
        for stage, verdict in (("alpha", "green"), ("red", "RED (exit 3)"), ("gamma", "green")):
            hits = re.findall(rf">>> stage {stage}\s+{re.escape(verdict)}\s", err)
            check(f"stage {stage} has exactly one verdict line, {verdict}", len(hits) == 1,
                  f"{len(hits)} in {err!r}")
        frames = [frame.replace("\x1b[K", "") for frame in err.split("\r")]
        check("the progress line starts at 0 of 3 stages and 0 of 9 steps",
              any(f.startswith("[0/3 stages  0/9 steps") for f in frames), f"frames {frames[:4]!r}")
        check("the progress line names a running step in its short form",
              any(re.search(r"\b(alpha|red|gamma) [12]/3 (first\.py --selftest|second\.sh)", f) for f in frames),
              f"frames {frames!r}")
        check("the line is cleared at the end, so the summary starts on a clean line",
              err.endswith("\r\x1b[K"), f"tail {err[-40:]!r}")

        mutated = write_fixture(Path(tempfile.mkdtemp(dir=tmp)), (ROW_FILTER, ROW_FILTER_REMOVED))
        _, rows = stage_events(mutated, "alpha")
        check("non-vacuity: without the row filter the count changes, so case 1 watches the filter",
              len(rows) != len(want), f"got {len(rows)} events")

    print(f"[test-check-sh-live] {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
