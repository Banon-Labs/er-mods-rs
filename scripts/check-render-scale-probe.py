#!/usr/bin/env python3
"""Check what er-render-scale says it did, from the telemetry file the DLL writes.

Once a second the DLL's worker replaces `er-render-scale-telemetry.json` (crates/er-render-scale/
src/report.rs): the divisor asked for and the one in force, the output size, the size the game
drew read back out of its own viewport ratio (`GxDrawContext+0xed0` times the output), `exact`
(drawn == the output divided by the divisor in force), the game's GPU time, the dynamic resolution
controller's fields and the values it had before, and the menu's state and input capture. This
reads one copy and holds it to one expectation:

    --expect exact       a divisor above 1 is in force, the game drew exactly the output divided
                         by it, and the controller holds min = max = 1/divisor with the load
                         balancer uncoupled and the LOD-bias gate off. `--divisor N` also requires
                         N to be the one chosen and in force.
    --expect menu-open   the menu is open and taking the game's input: the cursor predicate is
                         hooked and answering, and the DirectInput keyboard and mouse blanking are
                         armed. `--require-pad` also requires the XInput hook.
    --expect native      divisor 1 chosen and in force, nothing of ours on the controller, the game
                         drew the whole output, and -- when the DLL ever wrote -- the controller
                         holds the values it had before.

Every mode also requires the schema this script was written for, a pid, and a file written in the
last `--max-age-seconds` (default 10; 0 turns the check off), so a finished run's last word is not
read as the live one.

The file is found the way `check-shader-editor-probe.py` finds its own: `--telemetry`, else
`--artifact-dir`, else `ER_QUICKLOAD_RENDER_SCALE_TELEMETRY_PATH`, else the newest copy among the
run directories and the game directory.

    python3 scripts/check-render-scale-probe.py --expect exact --divisor 3
    python3 scripts/check-render-scale-probe.py --expect menu-open
    python3 scripts/check-render-scale-probe.py --expect native
    python3 scripts/check-render-scale-probe.py --selftest

Exit status: 0 when every check holds, 1 when one does not, 2 when there is no file to read.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import er_run_lib  # noqa: E402
from er_artifact_env import ARTIFACT_ENV, RUN_ARTIFACT_DIR_ENV, resolve_artifact  # noqa: E402

REPORT_RS = REPO / "crates" / "er-render-scale" / "src" / "report.rs"
SCALE_RS = REPO / "crates" / "er-render-scale" / "src" / "scale.rs"

# Mirrored from report.rs; the selftest reads the Rust and fails when they part.
TELEMETRY_NAME = "er-render-scale-telemetry.json"
TELEMETRY_ENV = "ER_QUICKLOAD_RENDER_SCALE_TELEMETRY_PATH"
SCHEMA = 1
# Mirrored from scale.rs: the view setup rounds the scene viewport to this many pixels.
VIEWPORT_SNAP_PX = 4

EXPECTATIONS = ("exact", "menu-open", "native")
DEFAULT_MAX_AGE_SECONDS = 10.0
# How far the controller's min and max may sit from 1/divisor: they are written as `f32`.
SCALE_TOLERANCE = 1e-5


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def clean_divisors(output: list[int]) -> list[int]:
    """The divisors the DLL offers for `output`, as `scale::clean_divisors` computes them."""
    width, height = output
    found = [1]
    for divisor in range(2, min(width, height) // VIEWPORT_SNAP_PX + 1):
        if all(
            side % divisor == 0 and (side // divisor) % VIEWPORT_SNAP_PX == 0 for side in output
        ):
            found.append(divisor)
    return found


def _get(document: dict, *path, default=None):
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def common_checks(document: dict, now_ms: float, max_age_seconds: float) -> list[Check]:
    checks = [
        Check(
            "schema",
            document.get("schema") == SCHEMA,
            f"file says {document.get('schema')!r}, this script reads {SCHEMA}",
        ),
        Check("pid", isinstance(document.get("pid"), int) and document["pid"] > 0),
        Check(
            "game build",
            document.get("game_build_ok") is True,
            "the DLL refuses every build but 1.17.1",
        ),
    ]
    written = document.get("written_unix_ms")
    if max_age_seconds > 0:
        age = (now_ms - written) / 1000.0 if isinstance(written, (int, float)) else math.inf
        checks.append(
            Check(
                "fresh",
                age <= max_age_seconds,
                f"written {age:.1f} s ago, allowed {max_age_seconds:.0f} s",
            )
        )
    return checks


def _close(a, b) -> bool:
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= SCALE_TOLERANCE


def exact_checks(document: dict, divisor: int | None) -> list[Check]:
    applied = document.get("applied_divisor")
    selected = document.get("selected_divisor")
    output = document.get("output")
    expected = document.get("expected")
    drawn = document.get("drawn")
    drs = document.get("drs") or {}
    checks = [
        Check("hook installed", _get(document, "hook", "installed") is True,
              str(_get(document, "hook", "refusal"))),
        Check("sizes agree", document.get("sizes_agree") is True,
              str(document.get("offer_refusal"))),
        Check("a divisor above 1 is in force", isinstance(applied, int) and applied > 1,
              f"applied_divisor {applied!r}"),
        Check("the chosen divisor is the one in force", applied == selected,
              f"selected {selected!r}, applied {applied!r}"),
    ]
    if divisor is not None:
        checks.append(Check(f"divisor {divisor} chosen", selected == divisor and applied == divisor,
                            f"selected {selected!r}, applied {applied!r}"))
    if isinstance(output, list) and len(output) == 2 and isinstance(applied, int) and applied > 0:
        size = [output[0] // applied, output[1] // applied]
        checks.append(Check("divisor is clean for the output", applied in clean_divisors(output),
                            f"{applied} on {output[0]}x{output[1]}"))
        checks.append(Check("expected is the output divided by the divisor", expected == size,
                            f"expected {expected!r}, computed {size}"))
    else:
        checks.append(Check("output size reported", False, f"output {output!r}"))
    checks.append(Check("drawn equals expected", drawn is not None and drawn == expected,
                        f"drawn {drawn!r}, expected {expected!r}"))
    checks.append(Check("exact", document.get("exact") is True))
    if isinstance(applied, int) and applied > 0:
        scale = 1.0 / applied
        checks.extend(
            [
                Check("controller enabled", drs.get("enable") == 1, f"enable {drs.get('enable')!r}"),
                Check("load balancer uncoupled", drs.get("couple") == 0,
                      f"couple {drs.get('couple')!r}"),
                Check("min is 1/divisor", _close(drs.get("min"), scale), f"min {drs.get('min')!r}"),
                Check("max is 1/divisor", _close(drs.get("max"), scale), f"max {drs.get('max')!r}"),
                Check("LOD-bias gate off", drs.get("lod_bias_gate") == 0,
                      f"lod_bias_gate {drs.get('lod_bias_gate')!r}"),
                Check("hook writing", _get(document, "hook", "active") is True),
            ]
        )
    return checks


def menu_open_checks(document: dict, require_pad: bool) -> list[Check]:
    menu = document.get("menu") or {}
    capture = menu.get("input") or {}
    checks = [
        Check("menu open", menu.get("open") is True),
        Check("input taken", capture.get("taking") is True),
        Check("cursor predicate hooked", capture.get("cursor_hooked") is True,
              "the game frees the cursor and holds the camera only through it"),
        Check("cursor predicate answering", isinstance(capture.get("cursor_forced"), int)
              and capture["cursor_forced"] > 0, f"cursor_forced {capture.get('cursor_forced')!r}"),
        Check("keyboard blanking armed", capture.get("keyboard_armed") is True),
        Check("mouse blanking armed", capture.get("mouse_armed") is True),
        Check("panel drawn", isinstance(menu.get("draws"), int) and menu["draws"] > 0,
              f"draws {menu.get('draws')!r}"),
    ]
    if require_pad:
        checks.append(Check("pad hook installed", capture.get("pad_hooked") is True))
    return checks


def native_checks(document: dict) -> list[Check]:
    drs = document.get("drs") or {}
    original = document.get("original") or {}
    output = document.get("output")
    checks = [
        Check("divisor 1 chosen", document.get("selected_divisor") == 1,
              f"selected {document.get('selected_divisor')!r}"),
        Check("divisor 1 in force", document.get("applied_divisor") == 1,
              f"applied {document.get('applied_divisor')!r}"),
        Check("nothing of ours on the controller", _get(document, "hook", "active") is False),
        Check("drawn is the whole output", output is not None and document.get("drawn") == output,
              f"drawn {document.get('drawn')!r}, output {output!r}"),
        Check("exact", document.get("exact") is True),
    ]
    if original.get("captured") is True:
        for field in ("enable", "couple", "lod_bias_gate"):
            checks.append(Check(f"{field} restored", drs.get(field) == original.get(field),
                                f"{drs.get(field)!r} now, {original.get(field)!r} before"))
        for field in ("min", "max"):
            checks.append(Check(f"{field} restored", _close(drs.get(field), original.get(field)),
                                f"{drs.get(field)!r} now, {original.get(field)!r} before"))
    return checks


def evaluate(
    document: dict,
    expect: str,
    *,
    divisor: int | None = None,
    require_pad: bool = False,
    now_ms: float | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> list[Check]:
    now = time.time() * 1000.0 if now_ms is None else now_ms
    checks = common_checks(document, now, max_age_seconds)
    if expect == "exact":
        checks += exact_checks(document, divisor)
    elif expect == "menu-open":
        checks += menu_open_checks(document, require_pad)
    elif expect == "native":
        checks += native_checks(document)
    else:
        raise ValueError(f"unknown expectation {expect!r}")
    return checks


def resolve_telemetry(
    explicit: pathlib.Path | None = None, artifact_dir: pathlib.Path | None = None
) -> pathlib.Path:
    """Where this run's telemetry is; the same order `check-shader-editor-probe.py` uses."""
    if explicit is not None:
        return explicit
    game = er_run_lib.game_dir()
    if artifact_dir is not None:
        return resolve_artifact(TELEMETRY_NAME, game, prefer=artifact_dir)
    if os.environ.get(TELEMETRY_ENV):
        return pathlib.Path(os.environ[TELEMETRY_ENV])
    candidates = []
    if os.environ.get(RUN_ARTIFACT_DIR_ENV):
        candidates.append(pathlib.Path(os.environ[RUN_ARTIFACT_DIR_ENV]) / TELEMETRY_NAME)
    if er_run_lib.RUN_STATE_ROOT.is_dir():
        candidates.extend(er_run_lib.RUN_STATE_ROOT.glob(f"*/{TELEMETRY_NAME}"))
    candidates.append(game / TELEMETRY_NAME)
    newest: tuple[float, pathlib.Path] | None = None
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest[0]:
            newest = (mtime, candidate)
    return newest[1] if newest is not None else game / TELEMETRY_NAME


def report(checks: list[Check], path: pathlib.Path, expect: str) -> int:
    print(f"check-render-scale-probe: {path} --expect {expect}")
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        detail = f"  ({check.detail})" if check.detail and not check.ok else ""
        print(f"  {mark} {check.name}{detail}")
    failed = [check for check in checks if not check.ok]
    if failed:
        print(f"check-render-scale-probe: {len(failed)} of {len(checks)} check(s) failed")
        return 1
    print(f"check-render-scale-probe: all {len(checks)} check(s) hold")
    return 0


# --- selftest --------------------------------------------------------------------------------


def _document(**overrides) -> dict:
    """A telemetry document shaped as report.rs renders one: a 4K run at 1/3."""
    document = {
        "schema": SCHEMA,
        "pid": 372,
        "written_unix_ms": 1_000_000,
        "serial": 7,
        "game_build_ok": True,
        "selected_divisor": 3,
        "applied_divisor": 3,
        "output": [3840, 2160],
        "scene_target": [3840, 2160],
        "sizes_agree": True,
        "offer_refusal": None,
        "divisors": clean_divisors([3840, 2160]),
        "expected": [1280, 720],
        "drawn": [1280, 720],
        "viewport_ratio": [1 / 3, 1 / 3],
        "exact": True,
        "gpu_ms": 5.4,
        "hook": {"installed": True, "refusal": None, "calls": 900, "writes": 880, "restores": 0,
                 "foreign_config": 0, "active": True},
        "drs": {"enable": 1, "couple": 0, "min": 0.33333334, "max": 0.33333334,
                "current": 0.33333334, "lod_bias_gate": 0},
        "original": {"captured": True, "enable": 1, "couple": 1, "min": 0.7, "max": 1.0,
                     "lod_bias_gate": 1},
        "menu": {"open": False, "menu_key": "F5", "draws": 0,
                 "input": {"taking": False, "cursor_hooked": True, "cursor_forced": 0,
                           "keyboard_armed": True, "mouse_armed": True, "keys_blanked": 0,
                           "mouse_reads_blanked": 0, "pad_hooked": True, "pad_zeroed": 0}},
        "settings": {"path": "er-render-scale.toml", "divisor": 3},
    }
    for key, value in overrides.items():
        document[key] = value
    return document


def _native_document() -> dict:
    return _document(
        selected_divisor=1,
        applied_divisor=1,
        expected=[3840, 2160],
        drawn=[3840, 2160],
        viewport_ratio=[1.0, 1.0],
        hook={"installed": True, "refusal": None, "calls": 900, "writes": 880, "restores": 1,
              "foreign_config": 0, "active": False},
        drs={"enable": 1, "couple": 1, "min": 0.7, "max": 1.0, "current": 1.0, "lod_bias_gate": 1},
    )


def _menu_document() -> dict:
    document = _document()
    document["menu"] = {
        "open": True, "menu_key": "F5", "draws": 40,
        "input": {"taking": True, "cursor_hooked": True, "cursor_forced": 120,
                  "keyboard_armed": True, "mouse_armed": True, "keys_blanked": 3,
                  "mouse_reads_blanked": 90, "pad_hooked": False, "pad_zeroed": 0},
    }
    return document


def _passes(document: dict, expect: str, **kwargs) -> bool:
    kwargs.setdefault("now_ms", 1_000_500)
    return all(check.ok for check in evaluate(document, expect, **kwargs))


def _rust_const(path: pathlib.Path, name: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"pub const {name}: [^=]+= \"?([^\";]+)\"?;", text)
    return match.group(1) if match else None


def selftest() -> int:
    failures: list[str] = []

    def case(label: str, condition: bool) -> None:
        if not condition:
            failures.append(label)
            print(f"  FAIL {label}")
        else:
            print(f"  ok   {label}")

    # The copies of the DLL's names held to the Rust.
    case("telemetry file name matches report.rs",
         _rust_const(REPORT_RS, "TELEMETRY_FILE_NAME") == TELEMETRY_NAME)
    case("telemetry env var matches report.rs",
         _rust_const(REPORT_RS, "TELEMETRY_PATH_ENV") == TELEMETRY_ENV)
    case("schema matches report.rs", _rust_const(REPORT_RS, "SCHEMA") == str(SCHEMA))
    case("viewport snap matches scale.rs",
         _rust_const(SCALE_RS, "VIEWPORT_SNAP_PX") == str(VIEWPORT_SNAP_PX))
    case("the knob is in the launchers' shared table", ARTIFACT_ENV.get(TELEMETRY_ENV) == TELEMETRY_NAME)
    case("the 4K divisors are the twelve the menu offers",
         clean_divisors([3840, 2160]) == [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60])

    # exact.
    case("a 1/3 run that drew 1280x720 is exact", _passes(_document(), "exact"))
    case("...and names its divisor", _passes(_document(), "exact", divisor=3))
    case("...but not another one", not _passes(_document(), "exact", divisor=4))
    case("a drawn size the snap moved is not exact",
         not _passes(_document(drawn=[1280, 724], exact=False), "exact"))
    case("an exact flag alone does not pass when the sizes differ",
         not _passes(_document(drawn=[1280, 724]), "exact"))
    case("native is not exact-at-a-divisor", not _passes(_native_document(), "exact"))
    case("a chosen divisor not yet in force fails",
         not _passes(_document(selected_divisor=4), "exact"))
    case("an uncleared coupling fails",
         not _passes(_document(drs={**_document()["drs"], "couple": 1}), "exact"))
    case("a min that is not 1/divisor fails",
         not _passes(_document(drs={**_document()["drs"], "min": 0.5}), "exact"))
    case("sizes that disagree fail",
         not _passes(_document(sizes_agree=False, offer_refusal="differ"), "exact"))
    case("a refused hook fails",
         not _passes(_document(hook={**_document()["hook"], "installed": False,
                                     "refusal": "bytes"}), "exact"))

    # menu-open.
    case("an open menu taking input passes", _passes(_menu_document(), "menu-open"))
    case("...unless the pad is required and not hooked",
         not _passes(_menu_document(), "menu-open", require_pad=True))
    closed = _document()
    case("a closed menu fails", not _passes(closed, "menu-open"))
    unanswered = _menu_document()
    unanswered["menu"]["input"]["cursor_forced"] = 0
    case("an open menu the predicate never answered for fails",
         not _passes(unanswered, "menu-open"))
    released = _menu_document()
    released["menu"]["input"]["taking"] = False
    case("an open menu that gave input back fails", not _passes(released, "menu-open"))

    # native.
    case("native with the game's values back passes", _passes(_native_document(), "native"))
    still_ours = _native_document()
    still_ours["drs"]["min"] = 0.33333334
    case("native with our min still on fails", not _passes(still_ours, "native"))
    case("a scaled run is not native", not _passes(_document(), "native"))
    never_wrote = _native_document()
    never_wrote["original"] = {"captured": False}
    never_wrote["drs"] = {"enable": 1, "couple": 1, "min": 0.7, "max": 1.0, "current": 1.0,
                          "lod_bias_gate": 1}
    case("native before any write passes without originals", _passes(never_wrote, "native"))

    # Every mode.
    case("a stale file fails", not _passes(_document(), "exact", now_ms=1_000_000 + 60_000))
    case("the age check can be turned off",
         _passes(_document(), "exact", now_ms=1_000_000 + 60_000, max_age_seconds=0))
    case("another schema fails", not _passes(_document(schema=SCHEMA + 1), "exact"))
    case("another build fails", not _passes(_document(game_build_ok=False), "exact"))

    # The document the Rust renders parses as JSON and carries every key read here.
    sample = REPO / "target" / "check-render-scale-probe-selftest.json"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(json.dumps(_document()), encoding="utf-8")
    try:
        read = json.loads(sample.read_text(encoding="utf-8"))
        case("a written document reads back", read == _document())
        case("resolve_telemetry honours an explicit path", resolve_telemetry(sample) == sample)
    finally:
        sample.unlink(missing_ok=True)
    report_rs = REPORT_RS.read_text(encoding="utf-8")
    for key in ("selected_divisor", "applied_divisor", "sizes_agree", "expected", "drawn", "exact",
                "gpu_ms", "lod_bias_gate", "cursor_forced", "keyboard_armed", "written_unix_ms",
                "game_build_ok", "original"):
        case(f"report.rs renders `{key}`", f'\\"{key}\\"' in report_rs)

    print(f"selftest: {'FAILED' if failures else 'ok'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--expect", choices=EXPECTATIONS)
    parser.add_argument("--divisor", type=int, help="with --expect exact: the divisor required")
    parser.add_argument("--require-pad", action="store_true",
                        help="with --expect menu-open: also require the XInput hook")
    parser.add_argument("--telemetry", type=pathlib.Path, help="the telemetry file to read")
    parser.add_argument("--artifact-dir", type=pathlib.Path, help="a run directory to read from")
    parser.add_argument("--max-age-seconds", type=float, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.expect is None:
        parser.error("--expect is required (or --selftest)")
    path = resolve_telemetry(args.telemetry, args.artifact_dir)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        print(f"check-render-scale-probe: no telemetry at {path}: {error}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(f"check-render-scale-probe: {path} is not JSON: {error}", file=sys.stderr)
        return 1
    checks = evaluate(
        document,
        args.expect,
        divisor=args.divisor,
        require_pad=args.require_pad,
        max_age_seconds=args.max_age_seconds,
    )
    return report(checks, path, args.expect)


if __name__ == "__main__":
    sys.exit(main())
