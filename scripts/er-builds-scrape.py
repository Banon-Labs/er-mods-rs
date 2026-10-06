#!/usr/bin/env python3
"""Mirror er-build-planner's public Elden Ring builds to ~/.cache/er-build-planner.

    python3 scripts/er-builds-scrape.py              # fetch every page of the public list
    python3 scripts/er-builds-scrape.py --rl 150     # only builds at exactly that RL

One endpoint, read out of the planner's own client (`listPublic` in its `notifications-*.js`
bundle), and it needs no session despite the client always creating one first:

    get https://er-inventory-api.nyasu.business/inventories/browse?page=N&pageSize=100[&rl=R]
        -> {"data": [{id, version, user, createdAt, updatedAt, data: <full build doc>}],
            "pagination": {"pages", "total"}}

The build document inlined as `data` is the same one `GET /inventories/<id>` serves and
`er-build-import-core` parses. `rl` filters on exactly that level, not a range, so the RL window
is applied by the embedder, not here: the whole public list (5699 builds on 2026-09-29) is 57
pages, and mirroring all of it lets the window change without refetching.

Output: `builds.jsonl`, one `{"id", "updatedAt", "user", "build"}` per line. A rerun rewrites it
from the fresh listing, so builds made private or deleted since the last run drop out.
Requests are sequential; a small hobby site, and one mirror does not need to be fast.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://er-inventory-api.nyasu.business"
HEADERS = {"User-Agent": "er-mods-rs build research (single mirror, sequential)", "User": "-"}
PAGE_SIZE = 100
OUT = Path.home() / ".cache/er-build-planner"


def get_json(url: str, tries: int = 3):
    err = None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            err = e
    raise RuntimeError(f"{url}: {err}")


def page(n: int, rl: int | None) -> dict:
    q = f"page={n}&pageSize={PAGE_SIZE}" + (f"&rl={rl}" if rl is not None else "")
    return get_json(f"{API}/inventories/browse?{q}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rl", type=int, help="only builds at exactly this RL (the API's own filter)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    first = page(1, a.rl)
    pages, total = first["pagination"]["pages"], first["pagination"]["total"]
    print(f"browse: {total} public builds, {pages} pages", flush=True)
    rows: dict[str, dict] = {}
    for n in range(1, pages + 1):
        d = first if n == 1 else page(n, a.rl)
        for x in d.get("data", []):
            rows[x["id"]] = {"id": x["id"], "updatedAt": x.get("updatedAt") or x.get("createdAt"),
                             "user": (x.get("user") or {}).get("id"), "build": x.get("data") or {}}
        if n % 10 == 0 or n == pages:
            print(f"  page {n}/{pages}  {len(rows)} builds", flush=True)

    # Pages shift while a new build is published mid-run, so a count short of `total` is a
    # listing that moved under us, not a parse failure; say so rather than hide it.
    if len(rows) != total:
        print(f"note: listed {len(rows)} distinct builds against a reported total of {total}", flush=True)
    dst = a.out / ("builds.jsonl" if a.rl is None else f"builds-rl{a.rl}.jsonl")
    tmp = dst.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for r in rows.values():
            f.write(json.dumps(r) + "\n")
    tmp.replace(dst)
    print(f"wrote {len(rows)} builds to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
