#!/usr/bin/env python3
"""Profile the Autoload Identity Launch Gate's slot decode.

`er-run-branch.py` bounds every step at 28s and calls `er-pick-save.py` to learn which character a
launch will autoload. On 2026-09-17 that call measured 38s against the live APPDATA container, so
three launches in a row were refused by the gate after it had correctly decoded the character it
was refusing to launch. Two guesses at the cause -- decoding both containers, and the wide
`candidate_player_game_data_offsets` fallback -- were both wrong, and each cost a 40s round trip to
find out. This exists so the next one is a measurement.

    `python3 scripts/profile-launch-gate.py <save-root> [--slot N] [--out FILE]`

Prints the twelve functions with the most self time. The gate's own cache is bypassed: a profile of
a cache hit measures nothing.
"""

from __future__ import annotations

import argparse
import cProfile
import importlib.util
import io
import pstats
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOP_FUNCTIONS = 12


def picker():
    """The launch gate's own module, loaded the way its callers load it."""
    spec = importlib.util.spec_from_file_location("picker", REPO / "scripts" / "er-pick-save.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load scripts/er-pick-save.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="save directory to decode from")
    parser.add_argument("--slot", type=int, default=0, help="which slot (default 0)")
    parser.add_argument("--container", default="both", choices=("sl2", "co2", "both"))
    parser.add_argument("--out", type=Path, help="write the profile here instead of stdout")
    args = parser.parse_args()

    module = picker()
    # A cache hit would profile a file read. Drop the record for this exact container state so the
    # decode actually runs, rather than deleting the whole cache and costing other callers.
    expected = module.expected_save_bytes()
    active = module.active_container(args.root, args.container, expected)
    if active is None:
        print(f"no eligible {args.container} save under {args.root}", file=sys.stderr)
        return 1
    try:
        store_path = module.identity_cache_path()
        store_path.unlink(missing_ok=True)
    except OSError:
        pass

    profiler = cProfile.Profile()
    profiler.enable()
    result = module.targeted(args.root, args.container, args.slot)
    profiler.disable()

    report = io.StringIO()
    pstats.Stats(profiler, stream=report).sort_stats("tottime").print_stats(TOP_FUNCTIONS)
    text = f"decoded {result['count']} target(s) from {active}\n\n{report.getvalue()}"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
