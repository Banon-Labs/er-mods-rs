#!/usr/bin/env python3
"""Record one adjudicated row in the 1.17 gate-bypass baseline, its reason alongside it.

# Why this is a committed tool and not an inline edit

`scripts/audit-1170-gate-bypass.baseline.json` is a ratchet, and its `_reasons` map is the half
that makes the numbers readable: a count raised without a justification is a bypass banked
silently, which is the failure the ratchet exists to prevent rather than a use of it. Writing the
pair together is the whole point, so the pair is what this takes -- there is no way to call it
that raises a count and leaves the reason blank.

`--write-baseline` on the audit itself rewrites every count from the current tree and writes no
reason for any of them, so it cannot be used to accept one new row.

    python3 scripts/record-gate-bypass-row.py <baseline.json> <key> <reason-file>

The reason is read from a file rather than the command line because these are paragraphs, not
labels: the rows already in the baseline run to several hundred words each, and they earn it.
"""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    baseline_path = pathlib.Path(sys.argv[1])
    key = sys.argv[2]
    reason = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8").strip()
    if not reason:
        print("refusing to record an empty reason", file=sys.stderr)
        return 2

    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    reasons = data.setdefault("_reasons", {})
    data[key] = data.get(key, 0) + 1
    reasons[key] = reason

    # `_reasons` first and everything else sorted, matching the file's existing shape so the diff
    # is the row and nothing else.
    ordered: dict = {"_reasons": dict(sorted(reasons.items()))}
    ordered.update({k: v for k, v in sorted(data.items()) if k != "_reasons"})
    baseline_path.write_text(
        json.dumps(ordered, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    print(f"{key} -> {data[key]}, reason {len(reason)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
