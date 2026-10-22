"""The er-build-planner build corpus, read through one floor on the last-save date.

Every script that reads the mirror (`~/.cache/er-build-planner/builds.jsonl`, or a
`builds-rl<N>.jsonl` written by `er-builds-scrape.py --rl`) reads it through `iter_rows` or
`load_rows`, so no consumer can see a row saved before `CORPUS_SINCE`. A row is
`{"id", "updatedAt", "user", "build"}`; `updatedAt` is the build's last save, so this is a floor on
the last save: a pre-DLC build re-saved after the floor passes, which was accepted.
`build.version` is the planner app's version, not the game's, and is not read here.

Importable from a sibling script as `sys.path.insert(0, str(HERE)); import er_corpus`, or by
file path with `importlib`.

    python3 scripts/er_corpus.py --selftest
    python3 scripts/er_corpus.py [--mirror <path>] [--since <yyyy-mm-dd>]   # kept/dropped counts
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Iterator

CACHE = Path.home() / ".cache" / "er-build-planner"
MIRROR = CACHE / "builds.jsonl"

#: Shadow of the Erdtree shipped with patch 1.12 on this date. User ruling 2026-10-20: a build last
#: saved before the DLC is outdated and must not inform the build planner.
CORPUS_SINCE = "2024-06-20"


def floor(since: str | None) -> str:
    """A caller's own `--since` raised to `CORPUS_SINCE`: a stricter floor stays, a looser one or
    none becomes the corpus floor."""
    return CORPUS_SINCE if not since or since < CORPUS_SINCE else since


def kept(row: dict, since: str = CORPUS_SINCE) -> bool:
    """Whether a mirror row is on or after `since`. `updatedAt` is ISO 8601 (`2026-09-29T05:21:45.000Z`),
    so a string comparison against a `YYYY-MM-DD` date is a date comparison. A row with no
    `updatedAt` has no known save date and is dropped."""
    u = row.get("updatedAt")
    return isinstance(u, str) and u >= since


def iter_rows(path: Path | str = MIRROR, since: str = CORPUS_SINCE, counts: dict | None = None) -> Iterator[dict]:
    """The mirror's rows saved on or after `since`, in file order, blank lines skipped. `counts`,
    when given, gets `kept` and `dropped` added to as rows are read. `since` below `CORPUS_SINCE`
    is a research override; every default path uses `CORPUS_SINCE`."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            ok = kept(row, since)
            if counts is not None:
                k = "kept" if ok else "dropped"
                counts[k] = counts.get(k, 0) + 1
            if ok:
                yield row


def load_rows(path: Path | str = MIRROR, since: str = CORPUS_SINCE, counts: dict | None = None) -> list[dict]:
    """`iter_rows` as a list."""
    return list(iter_rows(path, since, counts))


def floor_counts(path: Path | str = MIRROR, since: str = CORPUS_SINCE) -> dict:
    """`{"kept": n, "dropped": n, "since": since}` for a mirror."""
    c = {"kept": 0, "dropped": 0}
    for _ in iter_rows(path, since, c):
        pass
    return {**c, "since": since}


def selftest() -> int:
    rows = [{"id": "a", "updatedAt": "2024-06-19T23:59:59.000Z", "build": {}},
            {"id": "b", "updatedAt": "2024-06-20T00:00:00.000Z", "build": {}},
            {"id": "c", "updatedAt": "2026-09-29T05:21:45.000Z", "build": {}},
            {"id": "d", "build": {}},
            {"id": "e", "updatedAt": None, "build": {}}]
    fails = 0

    def check(name, ok, got=None):
        nonlocal fails
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": {got!r}"))
        fails += not ok

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "builds.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")
        got = [r["id"] for r in iter_rows(p)]
        check("a row before the floor is dropped", "a" not in got, got)
        check("rows on and after the floor are kept", got == ["b", "c"], got)
        check("a missing or null updatedAt is dropped", "d" not in got and "e" not in got, got)
        c = floor_counts(p)
        check("floor_counts counts kept and dropped", (c["kept"], c["dropped"]) == (2, 3), c)
        check("an explicit research floor is honoured",
              [r["id"] for r in load_rows(p, since="2000-01-01")] == ["a", "b", "c"])
    check("floor keeps a stricter --since", floor("2026-06-01") == "2026-06-01")
    check("floor raises a looser --since", floor("2023-01-01") == CORPUS_SINCE)
    check("floor fills an absent --since", floor(None) == CORPUS_SINCE)
    print("selftest:", "FAIL" if fails else "ok")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--mirror", type=Path, default=MIRROR)
    ap.add_argument("--since", default=CORPUS_SINCE)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    print(json.dumps(floor_counts(a.mirror, a.since)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
