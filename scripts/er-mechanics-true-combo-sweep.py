#!/usr/bin/env python3
"""List every measured true combo over a set of weapons, one JSON line per combo.

Reads `er-mechanics-frame-advantage.py`'s `--json` record (`record`) for each weapon in both grips
and keeps the slot pairs whose `on_break.verdict` is "true" (the next hit lands before the victim
can roll out). The output is the `--combos` input of `er-mechanics-class-roles.py`.

    python3 scripts/er-mechanics-true-combo-sweep.py WEAPONS.txt > combos.jsonl
    python3 scripts/er-mechanics-true-combo-sweep.py --selftest

WEAPONS.txt holds weapon names separated by commas or newlines.

The records are made in this process from one load of the regulation and tables. Until
2026-10-20 each weapon grip was a `frame-advantage.py --json` child, which loads both again: about
1.9 s a grip against 0.01 s of measuring, so 316 grips took 1m58s on 8 threads and now take
seconds. `--selftest` checks a record against the child's own output.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: The child `--selftest` compares against; the cap only stops a hung one.
FRAME_ADVANTAGE_TIMEOUT_S = 30


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Advantage:
    """`er-mechanics-frame-advantage.py`'s module with its regulation and tables, loaded once."""

    def __init__(self):
        self.fa = _load("er_mechanics_frame_advantage", "er-mechanics-frame-advantage.py")
        self.reg = self.fa.ATTACKS.Regulation(None)
        self.tables = self.fa.Tables(self.reg, None)

    def record(self, weapon, grip):
        """What `frame-advantage.py <weapon> --grip <grip> --json` prints, parsed: the same
        `slot_profile` call and the same `json.dumps(..., default=str)` round trip."""
        fa, reg = self.fa, self.reg
        wid = reg.find_weapon(weapon)
        rows = fa.slot_profile(reg, self.tables, wid, grip, None, 0, 1)
        label = f'{reg.weapon_names.get(wid)} ({wid}) {"2H" if grip == "both" else "1H"}'
        return json.loads(json.dumps([{"weapon": wid, "name": label, "slots": rows}], indent=1, default=str))


def true_combos(weapon, grip, records):
    out = []
    for rec in records:
        for slot in rec.get("slots", []):
            for c in slot.get("combos") or []:
                ob = c.get("on_break") or {}
                if ob.get("verdict") == "true":
                    out.append({"weapon": weapon, "grip": grip, "from": slot.get("slot"),
                                "to": c.get("next"), "gap": ob.get("gap"), "escape": ob.get("escape")})
    return out


def combos(adv, job):
    weapon, grip = job
    try:
        records = adv.record(weapon, grip)
    except Exception as e:  # the child printed nothing for a weapon it could not read
        print(f"{weapon} ({grip}): no frame-advantage output ({e!r})", file=sys.stderr)
        return []
    return true_combos(weapon, grip, records)


def child_record(weapon, grip):
    proc = subprocess.run([sys.executable, str(HERE / "er-mechanics-frame-advantage.py"), weapon,
                           "--grip", grip, "--json"], capture_output=True, text=True, cwd=HERE.parent,
                          timeout=FRAME_ADVANTAGE_TIMEOUT_S)
    return json.loads(proc.stdout)


def selftest():
    adv = Advantage()
    ok = True
    # Raptor Talons 2H holds the one true combo of the heavy kit (2h_r2_1 -> 2h_r2_2, 2026-10-19).
    for weapon, grip in (("Raptor Talons", "both"), ("Uchigatana", "one")):
        same = adv.record(weapon, grip) == child_record(weapon, grip)
        print(f"{'ok' if same else 'FAIL'}: {weapon} {grip}: the in-process record is the child's --json")
        ok &= same
    got = true_combos("Raptor Talons", "both", adv.record("Raptor Talons", "both"))
    want = [{"weapon": "Raptor Talons", "grip": "both", "from": "2h_r2_1", "to": "2h_r2_2", "gap": 24.9,
             "escape": 25}]
    print(f"{'ok' if got == want else 'FAIL'}: Raptor Talons 2H true combos {got}")
    ok &= got == want
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("weapons", type=Path, nargs="?")
    ap.add_argument("--jobs", type=int, default=8, help="ignored: the sweep runs in this process")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.weapons is None:
        ap.error("WEAPONS.txt is required")
    names = [w.strip() for w in a.weapons.read_text().replace(",", "\n").splitlines() if w.strip()]
    jobs = [(w, g) for w in names for g in ("one", "both")]
    # The weapon-grips done, `  i/N weapon grips` on stderr (`er-mechanics-progress.Count`): the
    # combos on stdout are a few lines for the whole sweep, so they say nothing of how far it is.
    progress = _load("er_mechanics_progress", "er-mechanics-progress.py")
    tick = progress.Count(len(jobs), "weapon grips")
    adv = Advantage()
    for job in jobs:
        for r in combos(adv, job):
            print(json.dumps(r), flush=True)
        tick()
    return 0


if __name__ == "__main__":
    sys.exit(main())
