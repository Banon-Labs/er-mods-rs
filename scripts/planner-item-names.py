#!/usr/bin/env python3
"""Snapshot the build planner's own item names into `data/planner-item-names.json`.

    python3 scripts/planner-item-names.py            # refresh the snapshot from the live site
    python3 scripts/planner-item-names.py --selftest

The planner stores each weapon, ammunition and ash under an ASCII key but displays it, and builds
its icon URL, from the entry's `name`: the entry keyed `Misericorde` is named `Miséricorde`, and a
build slot saying `Misericorde` gets `/images/weapons-hi/Misericorde.png`, which is a 404 (the
accented file is the one that exists). So an uploader has to write the `name`, and this file is
the list it checks against. Read out of the planner's JS bundle: every object literal carrying a
`full_hex_id` is one item, `<key>:{full_hex_id:..,id:..,name:<name>}` with the name in backticks (2061 of them on 2026-10-12;
3 differ from their key, all by accents).

The snapshot maps the folded form (accents dropped, lowercased) of both key and name to the name.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unicodedata
import urllib.request

SITE = "https://er-build-planner.nyasu.business"
USER_AGENT = "er-mods-rs build-import (+github.com/Banon-Labs)"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "planner-item-names.json"

ENTRY = re.compile(
    r'(?:"([^"]{1,100})"|`([^`]{1,100})`|([A-Za-z_$][\w$]{0,60})):\{full_hex_id:`[0-9A-F]+`,id:[\de.]+,name:`([^`]*)`')
# An entry hoisted into a variable (`Quickstep:Wn` elsewhere): only the name is known.
HOISTED = re.compile(r'[\s,;{]([A-Za-z_$][\w$]{0,10})=\{full_hex_id:`[0-9A-F]+`,id:[\de.]+,name:`([^`]*)`')
ASSET = re.compile(r'(?:/assets/|\./)([\w.-]+\.js)')


def fold(name: str) -> str:
    """Accents dropped, case and whitespace normalised: the lookup key on both sides."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.replace("’", "'").lower().split())


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def names_from(js: str) -> dict[str, str]:
    """`{fold(key or name): name}` for every item literal in `js`."""
    out: dict[str, str] = {}
    for m in ENTRY.finditer(js):
        key = m.group(1) or m.group(2) or m.group(3)
        name = m.group(4)
        out[fold(key)] = name
        out[fold(name)] = name
    for m in HOISTED.finditer(js):
        out.setdefault(fold(m.group(2)), m.group(2))
    return out


def crawl() -> dict[str, str]:
    """Every chunk reachable from the index page, statically or by `import()`."""
    seen: set[str] = set()
    queue = ASSET.findall(get(f"{SITE}/"))
    names: dict[str, str] = {}
    while queue:
        chunk = queue.pop()
        if chunk in seen:
            continue
        seen.add(chunk)
        js = get(f"{SITE}/assets/{chunk}")
        names.update(names_from(js))
        queue.extend(c for c in ASSET.findall(js) if c not in seen)
    return names


def selftest() -> int:
    js = ('Dagger:{full_hex_id:`000F4240`,id:1e6,name:`Dagger`,x:1},'
          'Misericorde:{full_hex_id:`000FB770`,id:103e4,name:`Miséricorde`},'
          '"Varre\'s Bouquet":{full_hex_id:`00A8C320`,id:1106e4,name:`Varré\'s Bouquet`};'
          'let Wn={full_hex_id:`200138E4`,id:80100,name:`Quickstep`}')
    got = names_from(js)
    assert got["misericorde"] == "Miséricorde", got
    assert got[fold("Miséricorde")] == "Miséricorde"
    assert got["varre's bouquet"] == "Varré's Bouquet"
    assert got["dagger"] == "Dagger"
    assert got["quickstep"] == "Quickstep"
    assert "great epee" not in got
    print("selftest ok: keys and names both resolve to the planner's displayed name")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    names = crawl()
    if len(names) < 2000:
        sys.exit(f"only {len(names)} names read from {SITE}; the bundle layout changed, snapshot not written")
    OUT.write_text(json.dumps(dict(sorted(names.items())), ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    accented = sorted({n for n in names.values() if not n.isascii()})
    print(f"{len(names)} folded names -> {OUT}; accented: {accented}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
