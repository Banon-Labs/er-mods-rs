#!/usr/bin/env python3
"""Audit the audits: is a gate's PASS provable, or merely asserted?

A gate that scans the tree with a regex and reports "0 findings" is only
trustworthy if something would go red when that regex stops matching. Three
audits in this repo reported zero while real findings existed (2026-08-30), each
because its core matcher knew exactly one spelling of a thing that has several.

This tool answers the question empirically instead of by reading code: it
re-runs each gate's own ``--selftest`` (or its companion ``test-*.py``) with the
``re`` module lobotomised so that EVERY pattern matches nothing, and records
whether the selftest noticed.

    exit 0 under a blind matcher  ->  ASSERTED (the selftest cannot see the
                                      matcher; a silent zero is indistinguishable
                                      from a clean tree)
    non-zero under a blind matcher -> PROVABLE (something is watching)

Usage:
    python3 scripts/audit-selftest-vacuity.py            # sweep check.sh's gates
    python3 scripts/audit-selftest-vacuity.py --only rva
    python3 scripts/audit-selftest-vacuity.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PER_SCRIPT_TIMEOUT = 25


# --------------------------------------------------------------------------
# mutation runner (invoked as a subprocess: --run-blind <script> [args...])
# --------------------------------------------------------------------------
def run_blind_reads(script: str, argv: list[str]) -> None:
    """Exec `script` with every FILE READ it performs returning empty content.

    The regex blinding below is blind in turn to a gate that matches with ``in``,
    ``str.startswith``, ``ast`` or ``tomllib`` -- 27 of the gates in check.sh ran ZERO
    regex calls under it and were reported NO-MATCHER-RUN, which is not a verdict. This
    is the second question, and it is matcher-agnostic: if a selftest still passes when
    every file the gate opens is EMPTY, the selftest is not reading the tree at all, so
    its green says nothing about the tree.

    Writes are left alone, so a selftest that builds a temp fixture still builds it --
    it simply reads nothing back, which is the point.
    """
    import atexit
    import builtins
    import io
    import runpy

    scripts_dir = str(SCRIPTS)
    blinded = [0]
    real_open = builtins.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def from_target(depth: int = 2) -> bool:
        try:
            fn = sys._getframe(depth).f_code.co_filename
        except ValueError:
            return False
        return os.path.abspath(fn).startswith(scripts_dir)

    def blind_open(file, mode="r", *a, **kw):
        if "r" in mode and "+" not in mode and from_target():
            blinded[0] += 1
            return io.BytesIO(b"") if "b" in mode else io.StringIO("")
        return real_open(file, mode, *a, **kw)

    def blind_read_text(self, *a, **kw):
        if from_target():
            blinded[0] += 1
            return ""
        return real_read_text(self, *a, **kw)

    def blind_read_bytes(self, *a, **kw):
        if from_target():
            blinded[0] += 1
            return b""
        return real_read_bytes(self, *a, **kw)

    builtins.open = blind_open
    Path.read_text = blind_read_text
    Path.read_bytes = blind_read_bytes

    count_file = os.environ.get("VACUITY_COUNT_FILE")
    if count_file:
        atexit.register(
            lambda: real_open(count_file, "w", encoding="utf-8").write(str(blinded[0]))
        )

    sys.argv = [script] + argv
    runpy.run_path(script, run_name="__main__")


def run_blind(script: str, argv: list[str]) -> None:
    """Exec `script` with every regex IT compiles neutered so it can never match.

    Only calls whose immediate caller lives under ``scripts/`` are blinded --
    stdlib internals (argparse's nargs matcher, in particular) keep working, so
    a non-zero exit means the GATE noticed, not that the runner broke it.

    The number of blinded calls is written to $VACUITY_COUNT_FILE: a selftest
    that passes having blinded ZERO calls never touched a matcher at all, which
    is a stronger form of the same vacuity.
    """
    import atexit
    import runpy

    scripts_dir = str(SCRIPTS)
    never = re.compile(r"(?!x)x")
    blinded = [0]

    real_compile = re.compile
    real_search, real_match, real_fullmatch = re.search, re.match, re.fullmatch
    real_findall, real_finditer = re.findall, re.finditer
    real_sub, real_subn, real_split = re.sub, re.subn, re.split

    def from_target() -> bool:
        try:
            fn = sys._getframe(2).f_code.co_filename
        except ValueError:
            return False
        # abspath, because a relative target path makes co_filename relative too and a naive
        # prefix test then blinds NOTHING while reporting a confident zero.
        return os.path.abspath(fn).startswith(scripts_dir)

    def wrap(real, blind):
        def inner(*a, **kw):
            if from_target():
                blinded[0] += 1
                return blind(*a, **kw)
            return real(*a, **kw)
        return inner

    re.compile = wrap(real_compile, lambda p, flags=0: never)
    re.search = wrap(real_search, lambda p, s, flags=0: None)
    re.match = wrap(real_match, lambda p, s, flags=0: None)
    re.fullmatch = wrap(real_fullmatch, lambda p, s, flags=0: None)
    re.findall = wrap(real_findall, lambda p, s, flags=0: [])
    re.finditer = wrap(real_finditer, lambda p, s, flags=0: iter(()))
    re.sub = wrap(real_sub, lambda p, r, s, count=0, flags=0: s)
    re.subn = wrap(real_subn, lambda p, r, s, count=0, flags=0: (s, 0))
    re.split = wrap(real_split, lambda p, s, maxsplit=0, flags=0: [s])

    count_file = os.environ.get("VACUITY_COUNT_FILE")
    if count_file:
        atexit.register(
            lambda: Path(count_file).write_text(str(blinded[0]), encoding="utf-8")
        )

    sys.argv = [script] + argv
    runpy.run_path(script, run_name="__main__")


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------
def check_sh_invocations() -> "dict[str, list[str]]":
    txt = (SCRIPTS / "check.sh").read_text(encoding="utf-8", errors="replace")
    out: dict[str, list[str]] = {}
    pat = re.compile(
        r'(?:python3|bash)\s+"?\$\{?repo_root\}?/scripts/([A-Za-z0-9_.\-]+)"?([^\n]*)'
    )
    for name, rest in pat.findall(txt):
        out.setdefault(name, []).append(rest.strip())
    return out


def run(cmd: list[str], env=None) -> "tuple[int, str]":
    # The cap is passed as the module constant rather than through a parameter: no caller ever
    # overrode it, and a variable is opaque to `check-no-timeouts.py`, which can only verify a
    # literal or a module constant. A knob nobody turns is not worth a gate it cannot read.
    try:
        p = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=PER_SCRIPT_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    return p.returncode, (p.stdout + p.stderr)


def run_mutated(path: Path, args: list[str], mode: str = "regex") -> "tuple[int, str, int]":
    """Return (exit code, output, number of calls blinded) for the chosen blinding mode."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".count", delete=False) as fh:
        fh.write("unwritten")
        count_path = Path(fh.name)
    env = dict(os.environ, VACUITY_COUNT_FILE=str(count_path))
    # Several scripts here `os.execvp` into `uv run --with capstone python3` when the import
    # fails. That replaces the process and throws away the blinding, so provision capstone up
    # front and the re-exec never fires.
    # ANY mention of capstone, not `import capstone`. The narrow needle missed three real cases:
    # `from capstone import ...` (check-singleton-field-offsets.py), and gates that never import
    # it themselves but load a matcher module that does and re-execs from in there
    # (check-object-field-offsets-1170.py, attribute-field-offset-owners.py). Each came back
    # UNMEASURED -- the verdict that means this tool could not judge the gate at all. The two
    # errors are not symmetric: a false positive costs one `uv run` startup, a false negative
    # costs the whole judgement, so the needle is deliberately broad.
    needs_capstone = "capstone" in path.read_text(encoding="utf-8", errors="replace")
    runner = str(Path(__file__).resolve())
    if needs_capstone:
        prefix = ["uv", "run", "--with", "capstone", "python3"]
    else:
        prefix = [sys.executable]
    flag = "--run-blind" if mode == "regex" else "--run-blind-reads"
    rc, out = run(prefix + [runner, flag, str(path)] + args, env=env)
    # -1 means the runner never got to write a count. That is NOT zero: a script that
    # re-execs itself (several here do, to provision capstone under `uv run`) replaces the
    # process, discarding both the blinding and the atexit hook. Reporting that as "0 regex
    # calls" would be this tool committing the exact sin it exists to find.
    try:
        n = int(count_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        n = -1
    count_path.unlink(missing_ok=True)
    return rc, out, n


def sweep(only: str | None, out_json: Path | None, mode: str = "regex") -> int:
    invocations = check_sh_invocations()
    rows = []
    for name, arglists in sorted(invocations.items()):
        if not name.endswith(".py"):
            continue
        if only and only not in name:
            continue
        path = SCRIPTS / name
        if not path.exists():
            continue
        args = ["--selftest"] if any("--selftest" in a for a in arglists) else []
        if not args:
            companion = (
                SCRIPTS / ("test-" + name[len("check-"):]) if name.startswith("check-") else None
            )
            if companion is not None and companion.exists() and companion.name in invocations:
                path, args = companion, []
            elif name.startswith("test-"):
                pass  # a test-*.py IS the test; run it bare
            else:
                rows.append(
                    {
                        "script": name,
                        "verdict": "NO-SELFTEST",
                        "detail": "no --selftest and no companion test in check.sh",
                    }
                )
                continue
        base_rc, _ = run([sys.executable, str(path)] + args)
        blind_rc, blind_out, blinded = run_mutated(path, args, mode)
        if base_rc != 0:
            verdict = "BASELINE-RED"
            detail = f"baseline exit {base_rc} (pre-existing, not judged)"
        elif blinded < 0:
            verdict = "UNMEASURED"
            detail = "the script re-execs itself; blinding was discarded with the process"
        elif blinded == 0:
            verdict = "NO-MATCHER-RUN"
            detail = ("selftest ran zero regex calls -- it never touches a matcher"
                      if mode == "regex" else
                      "selftest read zero files -- it never touches the tree")
        elif blind_rc == 0:
            verdict = "ASSERTED"
            detail = f"passes with all {blinded} {'regex call' if mode == 'regex' else 'file read'}(s) blinded"
        else:
            tail = [ln for ln in blind_out.strip().splitlines() if ln.strip()][-1:]
            verdict = "PROVABLE"
            detail = f"blinded {blinded}, exit {blind_rc}: {tail[0][:100] if tail else ''}"
        rows.append({"script": name, "ran": path.name, "verdict": verdict, "detail": detail})
        print(f"{name:<44}  {verdict:<15}  {detail}", flush=True)

    for r in rows:
        if "ran" not in r:
            print(f"{r['script']:<44}  {r['verdict']:<15}  {r['detail']}", flush=True)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if out_json:
        out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"wrote {out_json}")
    return 0


def judge_one(path: Path, mode: str = "regex") -> int:
    """Judge a single script, including one check.sh never runs."""
    args = ["--selftest"] if "--selftest" in path.read_text(encoding="utf-8", errors="replace") else []
    base_rc, base_out = run([sys.executable, str(path)] + args)
    blind_rc, blind_out, blinded = run_mutated(path, args, mode)
    print(f"baseline : exit {base_rc}")
    unit = "regex call" if mode == "regex" else "file read"
    print(f"blinded  : {blinded} {unit}(s) neutered, exit {blind_rc}")
    if base_rc != 0:
        print(f"{path.name}: BASELINE-RED -- not judged")
    elif blinded < 0:
        print(f"{path.name}: UNMEASURED -- it re-execs itself, discarding the blinding")
    elif blinded == 0:
        print(f"{path.name}: NO-MATCHER-RUN -- the selftest never ran a {unit}")
    elif blind_rc == 0:
        print(f"{path.name}: ASSERTED -- passes with every {unit} blinded")
    else:
        print(f"{path.name}: PROVABLE -- blinding the matcher turns it red")
    if blind_out.strip():
        print("--- blinded output tail ---")
        print("\n".join(blind_out.strip().splitlines()[-6:]))
    return 0


# --------------------------------------------------------------------------
def selftest() -> int:
    """The blind runner must blind the GATE, count what it blinded, and leave
    the stdlib (argparse in particular) alone."""
    failures = []
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        # NOTE: written into scripts/ because run_blind only blinds callers that
        # live there -- a fixture in /tmp would be (correctly) left alone.
        holder = SCRIPTS / f"_vacuity_fixture_{os.getpid()}"
        holder.mkdir(exist_ok=True)
        try:
            probe = holder / "toy_gate.py"
            probe.write_text(
                "import argparse, re, sys\n"
                "ap = argparse.ArgumentParser()\n"
                "ap.add_argument('--selftest', action='store_true')\n"
                "ap.parse_args()\n"
                "hits = re.findall(r'needle', 'haystack needle haystack')\n"
                "sys.exit(0 if hits else 3)\n",
                encoding="utf-8",
            )
            rc, out = run([sys.executable, str(probe), "--selftest"])
            if rc != 0:
                failures.append(f"toy gate should pass unmutated, got exit {rc}: {out.strip()[:120]}")
            rc, out, n = run_mutated(probe, ["--selftest"])
            if rc == 0:
                failures.append("blind runner failed to blind: toy gate still found its needle")
            if rc == 2:
                failures.append(f"blind runner broke argparse instead of the gate: {out.strip()[:160]}")
            if n != 1:
                failures.append(f"blind runner should have counted 1 blinded call, counted {n}")

            # a gate that ignores regex must be untouched, and counted as zero
            inert = holder / "toy_inert.py"
            inert.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
            rc, out, n = run_mutated(inert, [])
            if rc != 0:
                failures.append(f"blind runner broke a regex-free script, exit {rc}")
            if n != 0:
                failures.append(f"regex-free script should blind 0 calls, counted {n}")

            # A RELATIVE path to a gate must blind exactly as an absolute one does. This is
            # the tool's own version of the bug it hunts: co_filename follows the spelling of
            # the path it was given, and a prefix test against an absolute scripts/ dir then
            # matched nothing and reported a confident "0 regex calls".
            rc, out, n = run_mutated(Path(os.path.relpath(probe, ROOT)), ["--selftest"])
            if n != 1:
                failures.append(
                    f"a relative path to a gate blinded {n} calls, absolute blinded 1 -- "
                    "the prefix test is spelling-sensitive"
                )

            # A gate that re-execs itself discards the blinding; that must read UNMEASURED
            # (-1), never as a clean zero.
            reexec = holder / "toy_reexec.py"
            reexec.write_text(
                "import os, sys\n"
                "os.execvp(sys.executable, [sys.executable, '-c', 'raise SystemExit(0)'])\n",
                encoding="utf-8",
            )
            rc, out, n = run_mutated(reexec, [])
            if n != -1:
                failures.append(
                    f"a self-re-execing gate must read UNMEASURED (-1), got {n} -- "
                    "a false zero here is this tool committing the sin it hunts"
                )

            # NEGATIVE CONTROL: a fixture OUTSIDE scripts/ must NOT be blinded,
            # so the runner cannot pass this suite by neutering everything.
            outsider = Path(td) / "toy_outsider.py"
            outsider.write_text(
                "import re, sys\n"
                "sys.exit(0 if re.findall(r'needle', 'a needle b') else 3)\n",
                encoding="utf-8",
            )
            rc, out, n = run_mutated(outsider, [])
            if rc != 0:
                failures.append(
                    f"runner blinded a file outside scripts/ (exit {rc}) -- blinding is not caller-aware"
                )
        finally:
            for f in holder.glob("*"):
                f.unlink()
            holder.rmdir()

    for f in failures:
        print(f"selftest FAIL {f}")
    print(f"[audit-selftest-vacuity] selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--run-blind":
        run_blind(sys.argv[2], sys.argv[3:])
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--run-blind-reads":
        run_blind_reads(sys.argv[2], sys.argv[3:])
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument(
        "--script",
        help="judge one script by path, even if check.sh never runs it",
    )
    ap.add_argument("--json", type=Path)
    ap.add_argument(
        "--mode",
        choices=("regex", "reads"),
        default="regex",
        help="regex: neuter every pattern the gate compiles. "
             "reads: make every file the gate opens come back EMPTY -- the question for a "
             "gate that matches with `in`/ast/tomllib rather than a regex.",
    )
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.script:
        return judge_one(Path(a.script).resolve(), a.mode)
    return sweep(a.only, a.json, a.mode)


if __name__ == "__main__":
    raise SystemExit(main())
