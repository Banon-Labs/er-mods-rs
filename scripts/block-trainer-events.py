#!/usr/bin/env python3
"""One line per block-trainer result from the AI lab's JSON-lines log on stdin, for a Monitor.

    tail -n 0 -F ~/.cache/er-frida/ai-lab.jsonl | python3 -u scripts/block-trainer-events.py \
        | python3 scripts/monitor-throttle.py 10

A pb-recipe line (scripts/frida/block-trainer.js, cfg.mode 'recipe') becomes its condition, outcome,
guard bit, damage, stamina lost, when the drop was made, the attack, the stun clips and the
partner's animation and move speed from 2 frames before the hit to 4 after. A pb-attempt line (the
timing search) becomes its outcome and timing. Trainer and lab errors pass through.
"""

import json
import sys


def recipe(d: dict) -> str:
    # g<guard up><guard held>: the guard state the game reports, then the input the trainer holds.
    frames = " ".join(f"{s['f']}:{s['anim']}/{s['speed']}/dl{s.get('dl')}/g{int(bool(s.get('guardFlag')))}"
                      f"{int(bool(s.get('guard')))}" for s in d.get("trace") or [] if -2 <= s["f"] <= 5)
    stun = [s["anim"] for s in d.get("stun") or []]
    return (f"{d.get('name')} {d.get('outcome')} guard={d.get('guard')} dmg={d.get('damage')} "
            f"spLost={d.get('staminaLost')} poise={d.get('poiseFound')}/{d.get('poise')}->{d.get('poiseAfter')}"
            f"->{d.get('poiseLater')} asked={d.get('asked')} "
            f"drop={d.get('drop')}@{d.get('dropFramesBeforeHit')} "
            f"atk={d.get('attack')} stun={stun} | {frames}")


def attempt(d: dict) -> str:
    return (f"{d.get('outcome')} atk={d.get('anim')} lead={d.get('lead')} release={d.get('release')} "
            f"guard={d.get('guard')} dmg={d.get('damage')}")


def main() -> int:
    for line in sys.stdin:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        kind = d.get("kind", "")
        if kind == "pb-recipe":
            # An attempt nobody swung at says only that the player is away; one a quarter minute
            # would wake a watcher for nothing.
            if d.get("outcome") == "never hit":
                continue
            # A hit landing while the defender backs off between attempts belongs to no condition;
            # against an attacker that chases, there is one most rounds.
            if d.get("name") == "stray":
                continue
            print(recipe(d), flush=True)
        elif kind == "pb-attempt":
            print(attempt(d), flush=True)
        elif kind in ("pb-error", "load-error", "unload-error"):
            print("ERROR", json.dumps(d)[:300], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
