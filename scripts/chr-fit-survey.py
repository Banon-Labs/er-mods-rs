#!/usr/bin/env python3
"""Rank many Dark Souls characters by how the builder's fit moves on Elden Ring's player skeleton.

Usage: chr-fit-survey.py --builder <mushroom-man-builder> --source <dir> --donor <FC_M_0000.flver>
                         --out <dir> [--game dsr|ds2] [--jobs N] (<id>... | --from-search <file>)
       chr-fit-survey.py --selftest

For each model id it runs `mushroom-man-builder export-obj` (which writes the fitted OBJ, its
weights, `fit.json`, `source-map.tsv` and the raw `<id>.flver` into `<out>/<id>`) and then
`dsr-er-fit-probe.py --json` on that export, and tabulates the probe's gates, one row per id,
fewest failed gates first. `--from-search` takes the ids from the last line of a `--search` run
("N of these can be built, each with ...: c1350 c1000 ..."). Game files are read from <source>
and the donor; everything is written under <out>, never into the repo. The probe needs numpy, so
run this under `uv run --with numpy`.

`--selftest` needs no game: it checks the search-line reader and the table on fabricated probe
output.
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parent / "dsr-er-fit-probe.py"
# Each export and each probe in a run of its own; the whole 84-model DS2 survey took under two
# minutes at four jobs, so one call reaching this is stuck, not slow.
STEP_TIMEOUT_S = 30

# The gates the table carries a column for, by the probe's gate names; the rest are counted.
COLUMNS = [
    ("bind_io", "bind inside-out"),
    ("idle_io", "idle inside-out outside hand and finger creases"),
    ("buried", "idle arm vertices buried"),
    ("leg_gap", "idle leg-trunk gap"),
    ("idle_edges", "idle loop worst-frame bad edges"),
    ("r1_edges", "R1 a020_030000 worst-frame bad edges"),
    ("run_edges", "running R1 a020_030200 worst-frame bad edges"),
]


def ids_from_search(text):
    """The ids on a `--search` run's closing line, in its order."""
    for line in reversed(text.splitlines()):
        if "can be built" in line and ":" in line:
            return re.findall(r"\bc\d{4}\b", line.split(":", 1)[1])
    return []


def survey_one(args, chr_id):
    """(id, probe json or None, why it has none)."""
    try:
        return _survey_one(args, chr_id)
    except subprocess.TimeoutExpired as e:
        return chr_id, None, [f"{Path(e.cmd[1]).name} took longer than {STEP_TIMEOUT_S} s"]


def _survey_one(args, chr_id):
    out = Path(args.out) / chr_id
    export = subprocess.run(
        [args.builder, "export-obj", "--character", chr_id, "--game", args.game, "--unvalidated",
         args.source, args.donor, str(out)],
        capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
    if export.returncode != 0:
        return chr_id, None, (export.stderr.strip() or export.stdout.strip()).splitlines()[-1:]
    json_path = out / "probe.json"
    probe = subprocess.run(
        [sys.executable, str(PROBE), str(out / f"{chr_id}.flver"), args.donor, str(out),
         "--json", str(json_path)],
        capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
    (out / "probe.txt").write_text(probe.stdout + probe.stderr, encoding="utf-8")
    if probe.returncode != 0 or not json_path.exists():
        return chr_id, None, (probe.stderr.strip() or "probe wrote no json").splitlines()[-1:]
    return chr_id, json.loads(json_path.read_text(encoding="utf-8")), None


def row(chr_id, probe):
    gates = {g["name"]: g for g in probe["gates"]}
    failed = [g["name"] for g in probe["gates"] if g["passed"] is False]
    cells = [chr_id, str(len(failed)), str(probe.get("vertices", "-"))]
    for _, name in COLUMNS:
        g = gates.get(name)
        if g is None:
            cells.append("-")
        else:
            cells.append(("" if g["passed"] is not False else "!") + str(g["measured"]))
    return len(failed), cells


def table(results, names=None):
    names = names or {}
    header = ["id", "failed", "verts"] + [key for key, _ in COLUMNS] + ["name"]
    rows, missing = [], []
    for chr_id, probe, why in results:
        if probe is None:
            missing.append(f"{chr_id}\t{names.get(chr_id, '')}\t{' '.join(why or [])}")
            continue
        failed, cells = row(chr_id, probe)
        rows.append((failed, chr_id, cells + [names.get(chr_id, "")]))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines = ["\t".join(header)] + ["\t".join(cells) for _, _, cells in rows]
    if missing:
        lines.append("")
        lines.append("# not probed")
        lines.extend(missing)
    return "\n".join(lines) + "\n"


def names_from_search(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(c\d{4})\s{2,}(.+?)\s{2,}", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def selftest():
    ok = True

    def check(label, cond, got=""):
        nonlocal ok
        print(f"{'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f": {got}"))
        ok &= bool(cond)

    search = ("c1350  Lonesome Gavlan                    fits Elden Ring's body: 4618 vertices\n"
              "c1000  Goblin                             fits Elden Ring's body: 1811 vertices\n"
              "\n2 of these can be built, each with --character <id> --game ds2 --unvalidated: "
              "c1350 c1000\n")
    check("search ids", ids_from_search(search) == ["c1350", "c1000"], ids_from_search(search))
    check("search names", names_from_search(search) == {"c1350": "Lonesome Gavlan", "c1000": "Goblin"},
          names_from_search(search))
    gate = lambda name, measured, passed: {"name": name, "measured": measured, "limit": "", "passed": passed}
    good = {"vertices": 10, "gates": [gate(name, 0, True) for _, name in COLUMNS]}
    bad = {"vertices": 20, "gates": [gate(name, 5, name != "bind inside-out") for _, name in COLUMNS]
           + [gate("grip anchor to R_Weapon at bind", "-", None)]}
    out = table([("c2000", bad, None), ("c1000", good, None), ("c3000", None, ["no fit"])],
                {"c1000": "Goblin"})
    lines = out.splitlines()
    check("header", lines[0].startswith("id\tfailed\tverts\tbind_io"), lines[0])
    check("fewest failures first", lines[1].startswith("c1000\t0\t10\t0") and lines[1].endswith("Goblin"), lines[1])
    check("a failed gate is marked", lines[2].startswith("c2000\t1\t20\t!5\t5"), lines[2])
    check("an unprobed id is listed with why", lines[-1] == "c3000\t\tno fit", lines[-1])
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--builder")
    ap.add_argument("--source", help="the DSR folder, or Dark Souls II's Game folder")
    ap.add_argument("--donor", help="the vanilla FC_M_0000.flver")
    ap.add_argument("--out")
    ap.add_argument("--game", default="dsr", choices=["dsr", "ds2"])
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--from-search", metavar="FILE")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not (args.builder and args.source and args.donor and args.out):
        ap.error("--builder, --source, --donor and --out are needed")
    names = {}
    ids = list(args.ids)
    if args.from_search:
        text = Path(args.from_search).read_text(encoding="utf-8")
        names = names_from_search(text)
        ids += ids_from_search(text)
    if not ids:
        ap.error("no ids: name some, or pass --from-search")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(lambda chr_id: survey_one(args, chr_id), ids):
            results.append(result)
            print(f"{result[0]}: {'probed' if result[1] else 'not probed: ' + ' '.join(result[2] or [])}",
                  file=sys.stderr, flush=True)
    out = table(results, names)
    (Path(args.out) / "survey.tsv").write_text(out, encoding="utf-8")
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
