#!/usr/bin/env python3
"""Compare the two name sources for a param: Smithbox's row names and the game's own FMG text.

    python3 scripts/er-name-sources.py EquipParamGem GemName
    python3 scripts/er-name-sources.py Magic GoodsName --show 40

For every row id in the installed regulation it reports which source names it, where the two
agree, where they disagree, and where neither does. Nothing is decided here: the output is what
a naming rule has to be argued from.
"""
import argparse
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('param')
    ap.add_argument('fmg')
    ap.add_argument('--offset', type=int, default=0, help='game id = row id + offset')
    ap.add_argument('--show', type=int, default=15)
    a = ap.parse_args()
    pr = _mod('er_param_read', 'er-param-read.py')
    names = _mod('er_item_name', 'er-item-name.py')
    ids = [r['id'] for r in pr.rows(pr.param_bytes(pr.load(), a.param), fields=['-'], strict=False)[0]]
    sb = {i: v for i, v in pr._smithbox_names(a.param).items() if v}
    game = names.game_names(a.fmg)
    g = {i: game[i + a.offset] for i in ids if i + a.offset in game}
    both = [i for i in ids if i in sb and i in g]
    same = [i for i in both if sb[i].strip().casefold() == g[i].strip().casefold()]
    diff = [i for i in both if i not in same]
    game_only = [i for i in ids if i in g and i not in sb]
    sb_only = [i for i in ids if i in sb and i not in g]
    neither = [i for i in ids if i not in sb and i not in g]
    print(f'{a.param} vs {a.fmg} (offset {a.offset}): {len(ids)} rows, {len(game)} game ids')
    print(f'  both {len(both)} (agree {len(same)}, differ {len(diff)}), game only {len(game_only)}, '
          f'smithbox only {len(sb_only)}, neither {len(neither)}')
    for label, rows in (('differ', diff), ('game only', game_only), ('smithbox only', sb_only)):
        for i in rows[:a.show]:
            print(f'  {label:13} {i:>10}  smithbox={sb.get(i)!r}  game={g.get(i)!r}')
    print(f'  neither: {neither[:a.show]}')


if __name__ == '__main__':
    main()
