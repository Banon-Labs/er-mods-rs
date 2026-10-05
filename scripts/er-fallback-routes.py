#!/usr/bin/env python3
"""Regulation lookups behind docs/er-mechanics/chainsaw/fallback-test-plan.md.

For a skill category (swordArtsTypeNew, the `c_SwordArtsID` the behavior script compares) print
the SwordArtsParam rows, their FP costs, and the base weapons that carry the skill built in or
can take it as an ash. For a weapon motion category print the base weapons in it.

    python3 scripts/er-fallback-routes.py --skill 25 --skill 318
    python3 scripts/er-fallback-routes.py --skill-name "Spinning Slash"
    python3 scripts/er-fallback-routes.py --motion 57 --motion 50
    python3 scripts/er-fallback-routes.py --weapon 30510000
    python3 scripts/er-fallback-routes.py --windows 623:40000     # follow-up / release windows
    python3 scripts/er-fallback-routes.py --sweep                 # windows into a missing child

Reads the installed 1.17.1 regulation through `er-mechanics-ashes.AshTables` and the 1.17.1
player TimeActs (the same `ER_PLAYER_TAE_DIR` default `er-behbnd-cmsg-fallbacks.py` sets).

`--sweep` answers "which skill opens a follow-up whose node has no child for it, with no weapon
swap at all": every skill clip (a600-a999, 040000-049999) whose TimeAct applies one of the window
SpEffects below, paired with the follow-up node that window leads to, where that skill has no
child of its own. The follow-up animId keeps the clip's hundreds (the weapon-type variant chosen
by `GetSwordArtsDiffCategory` when the skill started): 040000 -> 040010, 042400 -> 042410.
"""
import argparse
import struct
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: Loads first: it points `ER_PLAYER_TAE_DIR` at the 1.17.1 extract before the TimeAct reader
#: reads its default, and registers the module the ashes tables share.
FB = _load('er_behbnd_cmsg_fallbacks', 'er-behbnd-cmsg-fallbacks.py')
ATK = FB.ATK
ASH = _load('er_mechanics_ashes', 'er-mechanics-ashes.py')
#: Window SpEffect -> (follow-up state, offset added to the clip's hundreds).
WINDOW_DEST = {100050: ('SwordArtsOneShotComboEnd', 10), 100052: ('SwordArtsOneShotComboEnd', 10),
               100054: ('SwordArtsOneShotComboEnd', 10), 100051: ('SwordArtsOneShotComboEnd_2', 20),
               100053: ('SwordArtsOneShotComboEnd_2', 20), 100055: ('SwordArtsOneShotComboEnd_2', 20),
               100285: ('SwordArtsChargeCancelEarly', 1)}

#: SpEffects the behavior script tests with env(1116, id) to open a skill follow-up
#: (SwordArtsOneShot_onUpdate lines 12129-12141, GetSwordArtsRequestNew lines 713-718) or a
#: charge release (SwordArtsOneShot_onUpdate lines 12185-12205).
WINDOW_SPEFFECTS = {100050: 'R2 -> ComboEnd', 100051: 'R2 -> ComboEnd_2',
                    100052: 'L2 -> ComboEnd', 100053: 'L2 -> ComboEnd_2',
                    100054: 'R1 -> ComboEnd', 100055: 'R1 -> ComboEnd_2',
                    100285: 'L2 released -> ChargeCancelEarly',
                    100286: 'L2 released -> ChargeCancelLate'}
#: TimeAct event types whose first parameter is a SpEffectParam id (66/67 apply, 331 FP).
SPEFFECT_EVENT_TYPES = (66, 67)


def windows(category, anim):
    """[(spEffect, meaning, start s, end s, event type)] in one clip, imports followed."""
    cat, real, events = ATK.resolve_events(category, anim)
    out = []
    for e in events or []:
        if len(e.params) < 4:
            continue
        sp = struct.unpack_from('<i', e.params, 0)[0]
        if sp in WINDOW_SPEFFECTS:
            out.append((sp, WINDOW_SPEFFECTS[sp], e.start, e.end, e.type, cat, real))
    return out


def sweep():
    """Rows (source clip, window, follow-up node, child 0, child-0 hits) where the source's own
    category has no child in the follow-up node. No swap involved."""
    cm = FB.Cmsgs()
    by_state_anim = {}
    for n in cm.nodes:
        if n['offsetType'] != 0x12:
            continue
        for st in n['states']:
            by_state_anim.setdefault((st, n['animId']), []).append(n)
    names = FB.skill_names()
    rows = []
    for cat in sorted(c for c in FB.tae_categories() if 600 <= c < 1000):
        for anim in sorted(ATK.tae_animations(cat) or {}):
            if not 40000 <= anim < 50000:
                continue
            for sp, meaning, s, e, ty, rc, ra in windows(cat, anim):
                state, add = WINDOW_DEST[sp] if sp in WINDOW_DEST else (None, 0)
                if state is None:
                    continue
                base = anim - anim % 100
                for no_fp in (0, 5):
                    dest = base + add + no_fp
                    for n in by_state_anim.get((state, dest), []):
                        idx, how = FB.select(n, cat)
                        if how == 'own' or not n['children']:
                            continue
                        c0 = FB.child_clip(n['children'][idx])
                        hits = FB.clip_hits(*c0) if c0 else None
                        rows.append({'source': f'a{cat:03d}_{anim:06d}',
                                     'skill': '/'.join(names.get(cat, [])) or '?',
                                     'window': f'{sp} {meaning} {s:.2f}-{e:.2f}s', 'node': n['name'],
                                     'plays': f'a{c0[0]:03d}_{c0[1]:06d}' if c0 else '?',
                                     'how': how, 'hits': hits})
    return rows


def base_weapons(t):
    for wid, w in sorted(t.reg.weapon.items()):
        name = t.reg.weapon_names.get(wid) or ''
        if wid % 10000 or not name or name.startswith('[') or wid >= 100000000:
            continue
        yield wid, w, name


def show_weapon(t, wid, w, name):
    sid = w.get('swordArtsParamId')
    return (f'    {name} {wid}  skill={t.arts_name(sid)} ({sid})  gemMountType={w.get("gemMountType")}'
            f'  wepmotion={w.get("wepmotionCategory")} spAtk={w.get("spAtkcategory")}'
            f'  wepType={w.get("wepType")}')


def skill(t, type_new):
    rows = [(i, r) for i, r in sorted(t.arts.items()) if r['swordArtsTypeNew'] == type_new]
    print(f'== swordArtsTypeNew {type_new} (TimeAct a{600 + type_new})')
    for sid, r in rows:
        print(f'  SwordArtsParam {sid} {t.arts_name(sid)!r}  FP L2/R1/R2 = '
              f'{r["useMagicPoint_L2"]}/{r["useMagicPoint_R1"]}/{r["useMagicPoint_R2"]}')
        for wid, w, name in base_weapons(t):
            if w.get('swordArtsParamId') == sid:
                print(show_weapon(t, wid, w, name))
        for gid, g in sorted(t.gem.items()):
            if g['swordArtsParamId'] == sid:
                print(f'    ash {t.gem_names.get(gid)!r} {gid}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--skill', type=int, action='append', default=[])
    ap.add_argument('--skill-name', action='append', default=[])
    ap.add_argument('--motion', type=int, action='append', default=[])
    ap.add_argument('--weapon', type=int, action='append', default=[])
    ap.add_argument('--windows', action='append', default=[], help='CAT:ANIM, e.g. 603:40000')
    ap.add_argument('--sweep', action='store_true')
    a = ap.parse_args()
    if a.sweep:
        for r in sweep():
            print(f"{r['source']} ({r['skill']})  {r['window']}  -> {r['node']}  plays {r['plays']}"
                  f" [{r['how']}] hits={r['hits']}")
    t = ASH.AshTables()
    for n in a.skill_name:
        sid = t.find_arts(n)
        a.skill.append(t.arts[sid]['swordArtsTypeNew'])
    for s in a.skill:
        skill(t, s)
    for m in a.motion:
        print(f'== wepmotionCategory {m}')
        for wid, w, name in base_weapons(t):
            if w.get('wepmotionCategory') == m:
                print(show_weapon(t, wid, w, name))
    for wid in a.weapon:
        w = t.reg.weapon[wid]
        print(show_weapon(t, wid, w, t.reg.weapon_names.get(wid) or '?'))
    for spec in a.windows:
        cat, anim = (int(x) for x in spec.split(':'))
        rows = windows(cat, anim)
        print(f'== a{cat:03d}_{anim:06d} window SpEffects ({len(rows)})')
        for sp, meaning, s, e, ty, rc, ra in rows:
            src = '' if (rc, ra) == (cat, anim) else f'  (events from a{rc:03d}_{ra:06d})'
            print(f'    {sp} {meaning:34s} {s:.3f}-{e:.3f}s  frames {s * 30:.0f}-{e * 30:.0f}'
                  f'  type {ty}{src}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
