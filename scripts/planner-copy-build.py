#!/usr/bin/env python3
"""Copy a build planner build into a new short link, optionally changing its accessory colour.

    python3 scripts/planner-copy-build.py <build id> [--accessories-colour R,G,B]

Reads the build anonymously (`GET /inventories/<id>`) and stores the copy under the mod's own
anonymous planner session -- the file er-build-import-runtime's upload path keeps in the game
directory (er-build-planner-session.json) -- so it gets a fresh `?b=` link that whoever opens it
can save into their own account. Prints the link and what the planner reads back.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request

API = "https://er-inventory-api.nyasu.business/inventories"
PLANNER = "https://er-build-planner.nyasu.business/?b="
USER_AGENT = "er-mods-rs build-import (+github.com/Banon-Labs)"
SESSION = (pathlib.Path(os.environ.get("ER_GAME_DIR", pathlib.Path.home()
    / ".local/share/Steam/steamapps/common/ELDEN RING/Game")) / "er-build-planner-session.json")
# Fields the API adds to a stored build; a new build is posted without them.
SERVER_FIELDS = ("id", "author", "views", "revision", "computed")


def request(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT,
        "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", help="the build id, the part after ?b=")
    ap.add_argument("--accessories-colour", help="R,G,B for sliders.sliders.accessoriesColour")
    args = ap.parse_args()

    doc = request(f"{API}/{args.build}")
    for key in SERVER_FIELDS:
        doc.pop(key, None)
    if args.accessories_colour:
        colour = [int(v) for v in args.accessories_colour.split(",")]
        if len(colour) != 3 or not all(0 <= v <= 255 for v in colour):
            ap.error("--accessories-colour takes three values 0..255")
        doc["sliders"]["sliders"]["accessoriesColour"] = colour

    session = json.loads(SESSION.read_text(encoding="utf-8"))
    stored = request(API, {"id": "", "data": doc, "gameId": 1, "version": doc.get("version")},
        {"Authorization": f"Basic {session['session']}", "User": session["user"]})
    new_id = stored.get("id")
    if not new_id:
        print(f"upload refused: {stored}", file=sys.stderr)
        return 1
    back = request(f"{API}/{new_id}")
    print(f"{back['name']}  {PLANNER}{new_id}  accessoriesColour={back['sliders']['sliders'].get('accessoriesColour')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
