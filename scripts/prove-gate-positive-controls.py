#!/usr/bin/env python3
"""POSITIVE CONTROLS FOR THE GATES: does each check actually go RED on its own defect?

`scripts/audit-selftest-vacuity.py` answers a NEARBY question -- would the gate's own
selftest notice if its matcher went blind -- and that is a proxy. This answers the direct
one: plant the exact defect the gate claims to detect INTO THE REAL TREE, run the real
gate, and require a non-zero exit that NAMES the defect. Then plant a legitimate lookalike
and require the gate to stay green, because a gate that is red on everything is a gate
people learn to route around.

WHY IT IS NOT IN check.sh
-------------------------
Every control here MUTATES TRACKED FILES for the duration of one subprocess. That is safe
for one operator running it deliberately and NOT safe inside a suite other agents run
concurrently: a `git status` taken during a mutation window shows a defect nobody wrote,
and a concurrent writer to the same file would lose its edit to the restore. So this is a
manual instrument. `scripts/audit-selftest-vacuity.py --selftest` is the part that IS
cheap and side-effect-free enough to gate, and it is wired in check.sh.

RESTORATION IS NOT OPTIONAL, AND `finally` IS NOT ENOUGH
-------------------------------------------------------
SIGTERM -- how an agent harness reclaims a long-running process -- kills the interpreter
without unwinding. Measured on this tool's own first run: a `subprocess.run(..., timeout=600)`
mutant survived in `scripts/detect-proc.py` after a 45s harness timeout. Restoration is
therefore driven by a registry that atexit AND the fatal signal handlers both drain.

FOUR WAYS TO WRITE AN INVALID MUTANT (all four were hit while building this)
---------------------------------------------------------------------------
A mutant that does not actually contain the defect proves nothing about the gate:
  * `fn _pc_probe_dead_helper()` -- rustc's `dead_code` lint EXEMPTS `_`-prefixed names,
    so the "dead function" was never dead and check-save-disable-warnings was right to
    stay green.
  * renaming a required token `X` to `X + "X"` leaves `X` present as a PREFIX, so a
    substring gate correctly finds it.
  * `fn pcProbeUsedHelper` fails to COMPILE under `-D non-snake-case`, so the gate never
    ran; a mutant that breaks the build is not a positive control.
  * a forbidden launch URL written as a `#` COMMENT is deliberately skipped by
    check-launch-guardrails, which only scans executable lines.

    python3 scripts/prove-gate-positive-controls.py --list
    python3 scripts/prove-gate-positive-controls.py --only lossy
    python3 scripts/prove-gate-positive-controls.py --fast   # skip the cargo-driven ones
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Both bounds obey the repo's 30s cap on every non-game operation
# (scripts/check-no-timeouts.py, MAX_TIMEOUT_SECONDS). Nothing here needs more: the SUBJECT
# gates cap their own subprocesses below that -- check-save-disable-warnings caps cargo at
# 25s itself -- so a longer outer bound would only delay a verdict the gate has already made.
GATE_TIMEOUT = 30.0

RESULTS: list[tuple[str, str, str]] = []  # (gate, direction, PASS/FAIL)

# A planted defect left in the tree is the worst outcome this tool can produce, and a bare
# `finally` does NOT cover it (see the module docstring). The context managers below register
# here; atexit and the fatal signals both drain it.
_PENDING: "dict[Path, bytes | None]" = {}  # path -> original bytes, or None if it did not exist


def _restore_all() -> None:
    for path, original in list(_PENDING.items()):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        except OSError:
            pass
        _PENDING.pop(path, None)


atexit.register(_restore_all)


def _on_signal(signum, _frame):
    _restore_all()
    signal.signal(signum, signal.SIG_DFL)
    import os

    os.kill(os.getpid(), signum)


for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    with contextlib.suppress(ValueError, OSError):
        signal.signal(_sig, _on_signal)


def run(cmd, env=None, cwd=None):
    # The bound is the module constant rather than a parameter: no caller ever wanted a
    # different one, and check-no-timeouts.py can only verify a literal or a module
    # constant -- a variable reads to it as unbounded, which is how this tool first failed
    # the very gate it exists to prove.
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
            timeout=GATE_TIMEOUT, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT\n{exc.stdout or ''}{exc.stderr or ''}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


@contextlib.contextmanager
def edit_file(rel: str, transform):
    """Rewrite a tracked file with transform(text); restore byte-exactly afterwards."""
    path = ROOT / rel
    original = path.read_bytes()
    _PENDING[path] = original
    try:
        path.write_text(transform(original.decode("utf-8")), encoding="utf-8")
        yield path
    finally:
        path.write_bytes(original)
        _PENDING.pop(path, None)


@contextlib.contextmanager
def new_file(rel: str, content: str, track: bool = False):
    """Create a file that did not exist; delete it afterwards.

    ``track`` stages it with ``git add -N`` for gates that enumerate the git INDEX rather
    than the filesystem (check-no-committed-build-artifacts, check-no-timeouts), and
    unstages it in the same ``finally``.
    """
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    _PENDING[path] = path.read_bytes() if existed else None
    try:
        path.write_text(content, encoding="utf-8")
        if track:
            subprocess.run(["git", "-C", str(ROOT), "add", "-N", "--", rel],
                           capture_output=True, timeout=20)
        yield path
    finally:
        if track:
            subprocess.run(["git", "-C", str(ROOT), "rm", "--cached", "-q", "--force", "--", rel],
                           capture_output=True, timeout=20)
        if not existed:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(_PENDING[path])
        _PENDING.pop(path, None)


def inconclusive(gate: str, direction: str, why: str) -> None:
    RESULTS.append((gate, direction, "INCONCLUSIVE"))
    print(f"  {direction:<30} INCONCLUSIVE  {why}")


def expect(gate: str, direction: str, rc: int, out: str, want_red: bool, mentions=()):
    """Record one control. ``mentions`` are substrings the failure message must carry --
    a gate that goes red without naming the defect is red for an unknown reason.

    A 124 is NOT a red verdict: it means the gate did not finish inside the repo's 30s cap on
    non-game operations, which several whole-tree scans here genuinely straddle
    (check-no-timeouts ~29s, check-oracle-writers ~22s, both longer under load). Counting that
    as RED would let a gate that never ran look sensitive, which is the exact error this tool
    exists to catch."""
    if rc == 124:
        inconclusive(gate, direction, "gate did not finish inside the repo's own 30s cap")
        return False
    ok = (rc != 0) if want_red else (rc == 0)
    missing = [m for m in mentions if m not in out] if (ok and want_red) else []
    passed = ok and not missing
    RESULTS.append((gate, direction, "PASS" if passed else "FAIL"))
    print(f"  {direction:<30} exit={rc:<4} "
          f"{'RED' if rc else 'GREEN'} (want {'RED' if want_red else 'GREEN'})  "
          f"{'PASS' if passed else 'FAIL'}"
          + (f"  message omits {missing}" if missing else ""))
    if not passed:
        for line in [ln for ln in out.strip().splitlines() if ln.strip()][-5:]:
            print("      |", line[:180])
    return passed


# ==========================================================================
# controls
# ==========================================================================
CONTROLS: "dict[str, tuple[bool, object]]" = {}  # name -> (is_fast, fn)


def control(name: str, fast: bool = True, baseline: "list[str] | None" = None):
    """Register a control.

    ``baseline`` is the unmutated gate command. A control can only be read off a GREEN
    baseline: if the gate is already red -- which happens routinely on a shared branch,
    where another agent's in-flight edit can remove a line a gate asserts -- then both the
    mutated and unmutated runs are red and the comparison says nothing. That is reported as
    INCONCLUSIVE with the baseline's own message, never as a failing control.
    """
    def deco(fn):
        def wrapped():
            if baseline is not None:
                rc, out = run(baseline)
                if rc != 0:
                    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:]
                    inconclusive(name, "baseline",
                                 f"the gate is ALREADY RED unmutated (exit {rc}): "
                                 f"{tail[0][:120] if tail else ''}")
                    return
            fn()
        CONTROLS[name] = (fast, wrapped)
        return fn
    return deco


@control("no-lossy-utf8")
def _lossy():
    g = ["python3", "scripts/check-no-lossy-utf8.py"]
    bad = "fn f(b: &[u8]) -> String { String::from_utf8_lossy(b).to_string() }\n"
    with new_file("_pc_probe_lossy.rs", bad):
        rc, out = run(g)
        expect("no-lossy-utf8", "sens/unjustified", rc, out, True,
               ["_pc_probe_lossy.rs", "from_utf8_lossy"])
    ok = "// UTF-8 Lossy: display only\n" + bad
    with new_file("_pc_probe_lossy.rs", ok):
        rc, out = run(g)
        expect("no-lossy-utf8", "spec/justified", rc, out, False)


@control("no-timeouts", fast=False)
def _timeouts():
    g = ["python3", "scripts/check-no-timeouts.py"]
    # assembled, never written literally: check-no-timeouts.py scans .py source text, so a
    # literal over-cap timeout in THIS file would make the control tool fail the gate it proves.
    over_cap = "timeout=" + str(20 * 30)
    py600 = ("\n\ndef _pc_probe():\n    import subprocess\n"
             "    subprocess.run(['true'], " + over_cap + ")\n")
    # This gate's own whole-tree scan measures 28-31s, straddling the 30s cap it enforces, so a
    # harness that obeys the cap cannot reliably complete it. A 124 here is that collision, not a
    # blind gate: run it standalone to see the control.
    with edit_file("scripts/detect-proc.py", lambda t: t + py600):
        rc, out = run(g)
        if rc == 124:
            inconclusive("no-timeouts", "sens/py-timeout-600",
                         "the gate's own scan does not finish inside the repo's 30s cap")
        else:
            expect("no-timeouts", "sens/py-timeout-600", rc, out, True, ["detect-proc.py"])
    long_sleep = "sleep " + str(20 * 30)
    with edit_file("scripts/steam-running.sh",
                   lambda t, s=long_sleep: t + "\n_pc_probe() { " + s + "; }\n"):
        rc, out = run(g)
        if rc == 124:
            inconclusive("no-timeouts", "sens/shell-sleep", "gate exceeded the repo's own 30s cap")
        else:
            expect("no-timeouts", "sens/shell-sleep", rc, out, True, ["steam-running.sh"])
    py30 = "\n\ndef _pc_probe():\n    import subprocess\n    subprocess.run(['true'], timeout=30)\n"
    with edit_file("scripts/detect-proc.py", lambda t: t + py30):
        rc, out = run(g)
        if rc == 124:
            inconclusive("no-timeouts", "spec/py-timeout-at-cap", "gate exceeded the repo's own 30s cap")
        else:
            expect("no-timeouts", "spec/py-timeout-at-cap", rc, out, False)


@control("retired-button-labels", baseline=["python3", "scripts/check-retired-button-labels.py"])
def _retired():
    g = ["python3", "scripts/check-retired-button-labels.py"]
    with new_file("docs/_pc_probe.md", "Open the menu and press Load Profile to reload.\n"):
        rc, out = run(g)
        expect("retired-button-labels", "sens/present-tense-prose", rc, out, True,
               ["_pc_probe.md", "Load Profile"])
    with new_file("docs/_pc_probe.md", "The row was renamed: Load Profile is now Load Character.\n"):
        rc, out = run(g)
        expect("retired-button-labels", "spec/marked-as-history", rc, out, False)


@control("rust-file-sizes")
def _sizes():
    # Run against an ISOLATED root: the live tree is periodically over the limit from
    # in-flight work, and a control cannot be read off an already-red baseline.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.rs").write_text("// x\n" * 3400)
        rc, out = run(["python3", "scripts/check-rust-file-sizes.py", "--root", str(root)])
        expect("rust-file-sizes", "sens/over-fail-lines", rc, out, True, ["a.rs"])
        (root / "a.rs").write_text("// x\n" * 800)
        rc, out = run(["python3", "scripts/check-rust-file-sizes.py", "--root", str(root)])
        expect("rust-file-sizes", "spec/under-fail-lines", rc, out, False)


@control("markdown-code-blocks")
def _md():
    g = ["python3", "scripts/check-markdown-code-blocks.py", "README.md"]
    block = "\n\n```bash\nthis is ( not valid shell\n```\n"
    with edit_file("README.md", lambda t: t + block):
        rc, out = run(g)
        expect("markdown-code-blocks", "sens/undirected-block", rc, out, True)
    with edit_file("README.md", lambda t: t + "\n\n<!-- md-test: skip illustrative -->" + block):
        rc, out = run(g)
        expect("markdown-code-blocks", "spec/skip-directive", rc, out, False)


@control("experiments-rustfmt", baseline=["python3", "scripts/check-experiments-rustfmt.py"])
def _expfmt():
    g = ["python3", "scripts/check-experiments-rustfmt.py"]
    p = "crates/er-quickload/src/experiments/_pc_probe_fmt.rs"
    with new_file(p, "fn  _pc_probe( )   ->u8{  1  }\n"):
        rc, out = run(g)
        expect("experiments-rustfmt", "sens/misformatted", rc, out, True, ["_pc_probe_fmt.rs"])
    with new_file(p, "fn _pc_probe() -> u8 {\n    1\n}\n"):
        rc, out = run(g)
        expect("experiments-rustfmt", "spec/well-formatted", rc, out, False)


@control("launch-guardrails",
         baseline=["python3", "scripts/check-launch-guardrails.py", "--audit"])
def _launch():
    g = ["python3", "scripts/check-launch-guardrails.py", "--audit"]
    # assembled from pieces so the repo's own Cupcake launch guard does not (correctly)
    # refuse the shell command that carries this control.
    forbidden = "steam:" + "//" + "rungameid/" + "12456" + "20"
    with edit_file("AGENTS.md", lambda t: t.replace("Do not bundle `ersc.dll`",
                                                    "Do not bundle the co-op DLL", 1)):
        rc, out = run(g)
        expect("launch-guardrails", "sens/contract-snippet-gone", rc, out, True, ["ersc.dll"])
    # an EXECUTABLE line: the gate deliberately skips `#` comments.
    with edit_file("scripts/er-smoke-driver.sh",
                   lambda t: t + "\n_pc_probe() { xdg-open " + forbidden + "; }\n"):
        rc, out = run(g)
        expect("launch-guardrails", "sens/forbidden-launch-form", rc, out, True,
               ["er-smoke-driver.sh", "smoke-driver-forbidden-launch-mode"])
    with edit_file("scripts/er-smoke-driver.sh",
                   lambda t: t + '\n_pc_probe() { "$PROTON" run "$GAME_DIR/eldenring.exe"; }\n'):
        rc, out = run(g)
        expect("launch-guardrails", "spec/direct-offline-launch", rc, out, False)


@control("native-continue-static")
def _native_continue():
    # The image is ground truth and must not be touched; the mutable input is the gate's
    # own CLAIM about where a byte window sits. A false claim must be rejected.
    s = "scripts/check-native-continue-static.py"
    with edit_file(s, lambda t: t.replace("CONTINUE_LOAD = 0x14067B750",
                                          "CONTINUE_LOAD = 0x14067B760", 1)):
        rc, out = run(["python3", s])
        expect("native-continue-static", "sens/wrong-rva", rc, out, True)


@control("menu-constructor-static")
def _menu_ctor():
    s = "scripts/check-menu-constructor-static.py"
    with edit_file(s, lambda t: t.replace("IDLE_CTOR = 0x007ACF80",
                                          "IDLE_CTOR = 0x007ACF90", 1)):
        rc, out = run(["python3", s])
        expect("menu-constructor-static", "sens/wrong-rva", rc, out, True)


@control("starting-classes", fast=False,
         baseline=["python3", "scripts/check-starting-classes.py"])
def _classes():
    g = ["python3", "scripts/check-starting-classes.py"]
    f = "crates/er-build-import-core/src/class.rs"
    with edit_file(f, lambda t: t.replace('    "Heavy Knight",\n', "", 1)):
        rc, out = run(g)
        expect("starting-classes", "sens/class-dropped", rc, out, True)
    with edit_file(f, lambda t: t.replace('    "Confessor",\n', '    "Confesser",\n', 1)):
        rc, out = run(g)
        expect("starting-classes", "sens/name-misspelled", rc, out, True)
    with edit_file(f, lambda t: t.replace('    "Confessor",\n    "Samurai",\n',
                                          '    "Samurai",\n    "Confessor",\n', 1)):
        rc, out = run(g)
        expect("starting-classes", "sens/order-swapped", rc, out, True)


@control("regulation-effects-json", fast=False)
def _effects():
    g = ["python3", "scripts/diff-regulation-params.py", "--effects-json"]
    with edit_file("data/effects.json",
                   lambda t: re.sub(r'("id"\s*:\s*)(\d+)', lambda m: m.group(1) + "999999991",
                                    t, count=1)):
        rc, out = run(g)
        expect("regulation-effects-json", "sens/nonexistent-speffect", rc, out, True)


@control("reload-trace-policy", fast=False,
         baseline=["python3", "scripts/check-reload-trace-policy.py", "--audit"])
def _reload_trace():
    g = ["python3", "scripts/check-reload-trace-policy.py", "--audit"]
    p = "crates/er-reload-trace/src/_pc_probe.rs"
    bad = ('pub fn _pc_probe() {\n'
           '    let _ = std::env::var("ER_QUICKLOAD_PROBE");\n'
           '    unsafe { core::ptr::write(core::ptr::null_mut::<u8>(), 1) };\n}\n')
    with new_file(p, bad):
        rc, out = run(g)
        expect("reload-trace-policy", "sens/env-gate+game-write", rc, out, True)
    # the 2026-08-30 false positive: the same words as PROSE must stay green.
    prose = ('// History: this crate used to call std::env::var("ER_QUICKLOAD_X") and\n'
             '// product_autoload_enabled(); both were removed.\npub fn _pc_probe() {}\n')
    with new_file(p, prose):
        rc, out = run(g)
        expect("reload-trace-policy", "spec/same-words-in-prose", rc, out, False)


@control("user-release-package", fast=False)
def _release_pkg():
    g = ["python3", "scripts/check-user-release-package.py"]
    with edit_file("scripts/build-user-release-package.py",
                   lambda t: t.replace("SHA256SUMS.txt", "SHA256SUMS-renamed.txt")):
        rc, out = run(g)
        expect("user-release-package", "sens/required-file-missing", rc, out, True,
               ["SHA256SUMS.txt"])


@control("save-disable-warnings", fast=False,
         baseline=["python3", "scripts/check-save-disable-warnings.py"])
def _save_disable():
    g = ["python3", "scripts/check-save-disable-warnings.py"]
    s = "crates/er-save-disable/src/lib.rs"
    # NOT `_`-prefixed: rustc exempts those from dead_code, which makes the mutant invalid.
    dead = "\n#[cfg(windows)]\nfn pc_probe_dead_helper(x: u32) -> u32 {\n    x + 1\n}\n"
    with edit_file(s, lambda t: t + dead):
        rc, out = run(g)
        expect("save-disable-warnings", "sens/dead-fn", rc, out, True, ["pc_probe_dead_helper"])
    used = ("\n#[cfg(windows)]\nfn pc_probe_used_helper(x: u32) -> u32 {\n    x + 1\n}\n"
            "#[cfg(windows)]\n#[unsafe(no_mangle)]\n"
            "pub extern \"C\" fn pc_probe_export() -> u32 {\n    pc_probe_used_helper(1)\n}\n")
    with edit_file(s, lambda t: t + used):
        rc, out = run(g)
        expect("save-disable-warnings", "spec/used-fn", rc, out, False)


@control("er_run_lib")
def _run_lib():
    # A bare `python3 scripts/er_run_lib.py` IS its selftest (no --selftest flag).
    s = "scripts/er_run_lib.py"
    with edit_file(s, lambda t: t.replace(
            "def collect_dead_runs(root: Path = RUN_STATE_ROOT) -> list[tuple[str, list[str]]]:",
            "def collect_dead_runs(root: Path = RUN_STATE_ROOT) -> list[tuple[str, list[str]]]:\n"
            "    return []  # pc_probe: GC disabled", 1)):
        rc, out = run(["python3", s])
        expect("er_run_lib", "sens/gc-disabled", rc, out, True)


@control("no-unguarded-cstr-from-ptr", fast=False,
         baseline=["python3", "scripts/check-no-unguarded-cstr-from-ptr.py"])
def _cstr():
    g = ["python3", "scripts/check-no-unguarded-cstr-from-ptr.py"]
    bad = ('pub unsafe fn pc_probe(key: *const i8) -> String {\n'
           '    unsafe { std::ffi::CStr::from_ptr(key) }.to_string_lossy().into_owned()\n}\n')
    with new_file("crates/er-game-base/src/_pc_probe_cstr.rs", bad):
        rc, out = run(g)
        expect("no-unguarded-cstr-from-ptr", "sens/unguarded", rc, out, True,
               ["_pc_probe_cstr.rs"])


@control("no-committed-build-artifacts", fast=False)
def _artifacts():
    g = ["python3", "scripts/check-no-committed-build-artifacts.py"]
    with new_file("_pc_probe_artifact.dll", "MZ\x00\x00", track=True):
        rc, out = run(g)
        expect("no-committed-build-artifacts", "sens/tracked-dll", rc, out, True,
               ["_pc_probe_artifact.dll"])


@control("beads-prime-size", fast=False)
def _prime():
    g = ["python3", "scripts/test-beads-prime-size.py"]
    f = "scripts/gen-beads-prime.py"
    with edit_file(f, lambda t: t.replace(
            '        if len(text.encode("utf-8")) <= MAX_BYTES:\n            return text\n',
            "        return text  # pc_probe: budget enforcement disabled\n", 1)):
        rc, out = run(g)
        expect("beads-prime-size", "sens/budget-not-enforced", rc, out, True)
    with edit_file(f, lambda t: t.replace("bd memories", "bd MEMORIESX")):
        rc, out = run(g)
        expect("beads-prime-size", "sens/no-discovery-guidance", rc, out, True)


@control("input-harness-static", baseline=["python3", "scripts/test-input-harness-static.py"])
def _input_harness():
    g = ["python3", "scripts/test-input-harness-static.py"]
    f = "crates/er-input-harness/src/pad_inject.rs"
    with edit_file(f, lambda t: t.replace("const VK_ID_MAX: u32 = 1080;",
                                          "const VK_ID_MAX: u32 = 1090;", 1)):
        rc, out = run(g)
        expect("input-harness-static", "sens/const-changed", rc, out, True)
    with edit_file(f, lambda t: t.replace(
            "const CS_INGAME_PAD_TYPEID_RVAS: [usize; 2] = [0x3d5df27, 0x3d5df28];",
            "const CS_INGAME_PAD_TYPEID_RVAS: [usize; 2] = [0x3d5df27, 0x3d5df29];", 1)):
        rc, out = run(g)
        expect("input-harness-static", "sens/rva-changed", rc, out, True)
    # Its own docstring promises re-wrapped prose must NOT break it. Re-wrap a `///` LINE:
    # replacing the first textual occurrence of the range instead put a line break inside a
    # code statement, which failed an unrelated assertion -- a broken mutant, not a gate defect.
    def rewrap(text: str) -> str:
        out_lines = []
        done = False
        for line in text.splitlines(keepends=True):
            if not done and line.lstrip().startswith("///") and "1000..1080" in line:
                head, _, tail = line.partition("1000..1080")
                indent = head[: len(head) - len(head.lstrip())]
                out_lines.append(head + "1000..1080\n" + indent + "///" + tail)
                done = True
            else:
                out_lines.append(line)
        return "".join(out_lines)

    with edit_file(f, rewrap):
        rc, out = run(g)
        expect("input-harness-static", "spec/reflowed-prose", rc, out, False)


SIGNAL_TESTS = {
    "test-authority-agreement-signal.py": ".cupcake/signals/last_assistant_authority_agreement.sh",
    "test-idle-hold-signal.py": ".cupcake/signals/last_assistant_idle_hold.sh",
    "test-native-ownership-vocab-signal.py": ".cupcake/signals/last_assistant_native_ownership_vocab.sh",
    "test-stall-on-friction-signal.py": ".cupcake/signals/last_assistant_stall_on_friction.sh",
    "test-wall-of-text-signal.py": ".cupcake/signals/last_assistant_wall_of_text.sh",
    "test-unexecuted-promise-signal.py": ".cupcake/signals/last_assistant_unexecuted_promise.sh",
}


@control("cupcake-signal-tests", fast=False)
def _signals():
    # These tests drive the REAL shell signal as a subprocess, so blinding the TEST's own
    # regexes proves nothing -- the subject is the shell script. Stub it in both directions.
    silent = "#!/usr/bin/env bash\nexit 0\n"
    always = "#!/usr/bin/env bash\necho 'PROBE:always-fires'\n"
    for test, sig in SIGNAL_TESTS.items():
        for label, stub in (("sens/never-fires", silent), ("spec/cries-wolf", always)):
            with edit_file(sig, lambda t, s=stub: s):
                rc, out = run(["python3", "scripts/" + test])
                expect(test[:-3], label, rc, out, True)


@control("cupcake-hook-shim", fast=False)
def _hook_shim():
    with edit_file("scripts/cupcake-hook.sh", lambda t: "#!/usr/bin/env bash\nexit 0\n"):
        rc, out = run(["python3", "scripts/test-cupcake-hook-shim.py"])
        expect("cupcake-hook-shim", "sens/shim-always-allows", rc, out, True)


@control("cupcake-policies", fast=False)
def _policies():
    p = ".cupcake/policies/claude/block_manual_pgrep.rego"
    with edit_file(p, lambda t: t.replace("deny contains", "deny_disabled_pc_probe contains")):
        rc, out = run(["python3", "scripts/test-cupcake-policies.py"])
        expect("cupcake-policies", "sens/deny-rule-renamed-away", rc, out, True)


@control("semaphore-watchdog")
def _watchdog():
    g = ["python3", "scripts/test-semaphore-watchdog.py"]
    s = "scripts/semaphore_watchdog.py"
    # NOTE: emptying TEARDOWN_STALL's VALUE is NOT caught -- the test imports the constant
    # and compares against it, so both sides move together. Mutate the LOGIC instead.
    with edit_file(s, lambda t: t.replace("            if prev is not None and cur > prev:",
                                          "            if prev is not None and cur >= prev:", 1)):
        rc, out = run(g)
        expect("semaphore-watchdog", "sens/flat-counts-as-progress", rc, out, True)
    with edit_file(s, lambda t: t.replace("        if now - self._t0 >= self.hard_cap_seconds:",
                                          "        if False and now - self._t0 >= self.hard_cap_seconds:", 1)):
        rc, out = run(g)
        expect("semaphore-watchdog", "sens/hard-cap-disabled", rc, out, True)


@control("me3-dll-conflicts", baseline=["python3", "scripts/check-me3-dll-conflicts.py"])
def _conflicts():
    g = ["python3", "scripts/check-me3-dll-conflicts.py"]
    t = "scripts/me3-dll-conflicts.toml"
    names = re.findall(r'"(er-[a-z0-9\-]+)"', (ROOT / t).read_text(encoding="utf-8"))
    if not names:
        print("  me3-dll-conflicts: table has no crate rows to mutate")
        return
    target = names[0]
    with edit_file(t, lambda x: x.replace('"%s"' % target, '"%s-pc-probe"' % target, 1)):
        rc, out = run(g)
        expect("me3-dll-conflicts", "sens/shell-dropped-from-table", rc, out, True)


@control("single-dll-product-contract", fast=False,
         baseline=["python3", "scripts/check-single-dll-product-contract.py"])
def _single_dll():
    g = ["python3", "scripts/check-single-dll-product-contract.py"]
    with edit_file("scripts/stage-autoload-release.sh",
                   lambda t: t + '\ncp -f "$TARGET_DIR/er_quit_menu.dll" "$STAGE_DIR/er_quit_menu.dll"\n'):
        rc, out = run(g)
        expect("single-dll-product-contract", "sens/harness-dll-staged", rc, out, True)
    with edit_file("crates/er-quickload/Cargo.toml",
                   lambda t: t.replace("[dependencies]",
                                       '[dependencies]\ner-quit-menu = { path = "../er-quit-menu" }', 1)):
        rc, out = run(g)
        expect("single-dll-product-contract", "sens/harness-as-product-dep", rc, out, True)


@control("own-load-save-rejection-guard", fast=False,
         baseline=["python3", "scripts/check-own-load-save-rejection-guard.py"])
def _own_load():
    g = ["python3", "scripts/check-own-load-save-rejection-guard.py"]
    # rename by SHORTENING: appending a suffix leaves the required token present as a prefix.
    with edit_file("crates/er-quickload/src/experiments/save_redirect/path_hooks.rs",
                   lambda t: t.replace("oracle_own_load_save_repeated_identical_rejections",
                                       "oracle_own_load_save_repeated_ident_rejections")):
        rc, out = run(g)
        expect("own-load-save-rejection-guard", "sens/telemetry-field-renamed", rc, out, True,
               ["oracle_own_load_save_repeated_identical_rejections"])
    with edit_file("crates/er-save-redirect/src/lib.rs",
                   lambda t: t.replace("repeated_identical_rejection_sets_a_nonzero_recurrence_semaphore",
                                       "repeated_ident_rejection_sets_a_nonzero_recurrence_semaphore")):
        rc, out = run(g)
        expect("own-load-save-rejection-guard", "sens/host-test-renamed", rc, out, True)


@control("derive-callsite-1170")
def _derive_callsite():
    s = "scripts/derive-callsite-1170.py"
    with edit_file(s, lambda t: t.replace("def call_at(", "def call_at_real(", 1).replace(
            "def call_at_real(", "def call_at(image, addr):\n    return None\n\n\ndef call_at_real(", 1)):
        rc, out = run(["python3", s, "--selftest"])
        expect("derive-callsite-1170", "sens/decoder-stubbed", rc, out, True)


@control("oracle-writers", fast=False,
         baseline=["python3", "scripts/check-oracle-writers.py"])
def _oracle_writers():
    g = ["python3", "scripts/check-oracle-writers.py"]
    w = "crates/er-telemetry-core/src/counters.rs"
    dead = ('\npub static PC_PROBE_DEAD_COUNTER: AtomicU64 = AtomicU64::new(0);\n'
            'pub fn pc_probe_read() -> u64 {\n'
            '    PC_PROBE_DEAD_COUNTER.load(Ordering::Relaxed)\n}\n')
    with edit_file(w, lambda t: t + dead):
        rc, out = run(g)
        expect("oracle-writers", "sens/read-never-written", rc, out, True,
               ["PC_PROBE_DEAD_COUNTER"])
    with edit_file(w, lambda t: t + dead + 'pub fn pc_probe_write() {\n'
                                          '    PC_PROBE_DEAD_COUNTER.fetch_add(1, Ordering::Relaxed);\n}\n'):
        rc, out = run(g)
        expect("oracle-writers", "spec/read-and-written", rc, out, False)
    orig = ('\npub static PC_PROBE_ORIG: AtomicUsize = AtomicUsize::new(0);\n'
            'pub fn pc_probe_install() {\n    let _ = &PC_PROBE_ORIG;\n}\n'
            'pub fn pc_probe_call() -> usize {\n    PC_PROBE_ORIG.load(Ordering::Relaxed)\n}\n')
    with edit_file(w, lambda t: t + orig):
        rc, out = run(g)
        expect("oracle-writers", "spec/by-reference-trampoline", rc, out, False)


@control("stale-rva-calls", fast=False,
         baseline=["python3", "scripts/check-stale-rva-calls.py"])
def _stale_rva():
    g = ["python3", "scripts/check-stale-rva-calls.py"]
    p = "crates/er-charm-enemies/src/_pc_probe_stale.rs"
    bad = ('pub const PC_PROBE_TARGET_RVA: usize = 0x00b0d400;\n'
           'pub unsafe fn pc_probe(base: usize) -> u64 {\n'
           '    let f: extern "C" fn() -> u64 = unsafe { core::mem::transmute(base + PC_PROBE_TARGET_RVA) };\n'
           '    f()\n}\n')
    with new_file(p, bad):
        rc, out = run(g)
        expect("stale-rva-calls", "sens/raw-transmute-call", rc, out, True, ["_pc_probe_stale.rs"])
    good = ('pub const PC_PROBE_TARGET_RVA: usize = 0x00b0d400;\n'
            'pub unsafe fn pc_probe() -> u64 {\n'
            '    let addr = er_game_base::mem::game_rva(PC_PROBE_TARGET_RVA);\n'
            '    let f: extern "C" fn() -> u64 = unsafe { core::mem::transmute(addr) };\n'
            '    f()\n}\n')
    with new_file(p, good):
        rc, out = run(g)
        expect("stale-rva-calls", "spec/gated-via-game_rva", rc, out, False)


@control("dll-freshness")
def _freshness():
    f = "scripts/er-dll-freshness.sh"
    g = ["bash", f, "--selftest"]
    with edit_file(f, lambda t: t.replace(
            "\t[[ ${#stale[@]} -eq 0 ]] && return 0",
            "\treturn 0  # pc_probe: refusal disabled\n\t[[ ${#stale[@]} -eq 0 ]] && return 0", 1)):
        rc, out = run(g)
        expect("dll-freshness", "sens/refusal-disabled", rc, out, True)

    def skip_unknown(t):
        # accept-and-skip an artifact name the workspace does not build, instead of refusing.
        i = t.find('\t\tif [[ -z "$pkg" ]]; then')
        tail = "\t\t\tcontinue\n\t\tfi"
        j = t.find(tail, i)
        return t[:i] + '\t\tif [[ -z "$pkg" ]]; then\n' + tail + t[j + len(tail):]

    with edit_file(f, skip_unknown):
        rc, out = run(g)
        expect("dll-freshness", "sens/unknown-name-skipped", rc, out, True)


@control("prologue-masks-1170")
def _prologue_masks():
    f = "scripts/verify-prologue-masks-1170.py"
    g = ["python3", f, "--selftest"]
    with edit_file(f, lambda t: t.replace("def masked_equal(", "def masked_equal_real(", 1).replace(
            "def masked_equal_real(",
            "def masked_equal(*a, **k):\n    return True\n\n\ndef masked_equal_real(", 1)):
        rc, out = run(g)
        expect("prologue-masks-1170", "sens/mask-always-matches", rc, out, True)
    with edit_file(f, lambda t: t.replace("def derive_rip_mask(", "def derive_rip_mask_real(", 1).replace(
            "def derive_rip_mask_real(",
            "def derive_rip_mask(b, *a, **k):\n    return bytes(len(b))\n\n\ndef derive_rip_mask_real(", 1)):
        rc, out = run(g)
        expect("prologue-masks-1170", "sens/mask-all-ignored", rc, out, True)


@control("dll-provenance")
def _provenance():
    p = "scripts/er-dll-provenance.py"
    g = ["python3", p, "--selftest"]
    with edit_file(p, lambda t: t.replace("def verify(", "def verify_real(", 1).replace(
            "def verify_real(", "def verify(*a, **k):\n    return 0\n\n\ndef verify_real(", 1)):
        rc, out = run(g)
        expect("dll-provenance", "sens/verify-always-agrees", rc, out, True)
    with edit_file(p, lambda t: t.replace("def source_sha(", "def source_sha_real(", 1).replace(
            "def source_sha_real(",
            "def source_sha(*a, **k):\n    return 'constant'\n\n\ndef source_sha_real(", 1)):
        rc, out = run(g)
        expect("dll-provenance", "sens/source-hash-constant", rc, out, True)


# ==========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="substring filter over control names")
    ap.add_argument("--fast", action="store_true",
                    help="skip controls that drive cargo/regulation/opa (slower than ~25s)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, (fast, _) in sorted(CONTROLS.items()):
            print(f"  {name:<34} {'fast' if fast else 'slow'}")
        return 0

    for name, (fast, fn) in sorted(CONTROLS.items()):
        if args.only and args.only not in name:
            continue
        if args.fast and not fast:
            continue
        print(f"\n[{name}]")
        try:
            fn()
        except Exception as exc:  # a broken control must not leave a mutant behind
            RESULTS.append((name, "harness", "ERROR"))
            print(f"  HARNESS ERROR: {type(exc).__name__}: {exc}")

    _restore_all()
    failed = [r for r in RESULTS if r[2] == "FAIL"]
    unknown = [r for r in RESULTS if r[2] == "INCONCLUSIVE"]
    print(f"\n{len(RESULTS)} control(s), {len(failed)} FAIL, {len(unknown)} INCONCLUSIVE")
    for gate, direction, _ in unknown:
        print(f"  INCONCLUSIVE  {gate} {direction}")
    for gate, direction, verdict in failed:
        print(f"  {verdict}  {gate} {direction}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
