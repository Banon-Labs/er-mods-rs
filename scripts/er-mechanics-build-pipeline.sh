#!/usr/bin/env bash
# Generate a full planner build (every weapon page, sets, ranged, shields) for one stat block,
# the same chain of scripts the STR 80 / DEX 20 build (planner 188e74da444029 -> c8144277634d65)
# was made with, then upload it.
#
#   bash scripts/er-mechanics-build-pipeline.sh --prefix bleed --class Hero \
#       --stats vig=56,mnd=9,end=41,str=54,dex=9,int=7,fth=8,arc=45 --affinity Blood \
#       --name "Bleed Warped Axe RL150" [--focus "Warped Axe:Blood"] [--gear gear.json] [--update <planner id>] [--dir <dir>]
#       [--no-upload]    # stop once <prefix>-plan.json is written
#
# Steps, each skipped when its output exists and is newer than its inputs (delete a file to redo
# it):
#   1. <prefix>-planner-build.json the base planner build with --class/--stats/--name (jq)
#   2. <prefix>-embed0.json     er-mechanics-weapon-embed.py, no ash rows yet
#   3. <prefix>-wieldable.txt   er-mechanics-class-roles.py --list-wieldable
#   4. <prefix>-main/fixed.json er-mechanics-ash-table.py over the wieldable weapons at --affinity
#                               (with --variants) and over the base build's fixed-skill weapons
#   5. <prefix>-inbuild.json    the wieldable weapons neither sweep covered
#   6. <prefix>-embed.json      the embedding again, with every ash row
#   7. <prefix>-true-combos.jsonl, <prefix>-pb.json, <prefix>-roles.json
#   8. <prefix>-status.json     status-affinity sweep of the role picks no sweep has
#   9. <prefix>-plan.json       er-mechanics-combo-loadout.py, every cache under --dir
#  10. upload                   planner-ash-loadout-upload.py (new build, or --update <id>)
#
# The interrupt cache is per stat block and is the slow step (about 17,000 profiles from empty);
# it and the pierce pass run under the throttle (`--interrupt-jobs`), and so do the step 4 and 5
# sweeps (`--jobs`, 11.5 minutes in one process on 2026-10-14). The simulations behind the
# interrupt profiles and the embeddings' world reaches do not depend on stats, so they are kept
# across stat blocks in the planner cache (`interrupt-sims/`, `embed-reach-<hash>.json`), and so
# does the multihit counter rate, one `counter.json` per --dir (two stat blocks' files agreed on all
# 621 rows they shared, 2026-10-14).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
BASE_ID=188e74da444029
DIR=""
PREFIX="" CLASS="" STATS="" AFFINITY="Heavy" NAME="" GEAR="" UPDATE="" JOBS=6 BASE_JSON="" FOCUS="" NO_UPLOAD=""
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX=$2; shift 2 ;;
        --class) CLASS=$2; shift 2 ;;
        --stats) STATS=$2; shift 2 ;;
        --affinity) AFFINITY=$2; shift 2 ;;
        --name) NAME=$2; shift 2 ;;
        --gear) GEAR=$2; shift 2 ;;
        --update) UPDATE=$2; shift 2 ;;
        --focus) FOCUS=$2; shift 2 ;;
        --dir) DIR=$2; shift 2 ;;
        --base) BASE_ID=$2; shift 2 ;;
        --base-json) BASE_JSON=$2; shift 2 ;;
        --jobs) JOBS=$2; shift 2 ;;
        --no-upload) NO_UPLOAD=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
for v in PREFIX CLASS STATS NAME DIR BASE_JSON; do
    [ -n "${!v}" ] || { echo "--$(echo "$v" | tr 'A-Z_' 'a-z-') is required" >&2; exit 2; }
done
cd "$REPO"
P="$DIR/$PREFIX"
DMG=$(echo "$STATS" | tr ',' '\n' | grep -E '^(str|dex|int|fth|arc)=' | paste -sd,)
step() { echo "== $(date +%H:%M:%S) $*"; }
fresh() {  # `fresh out in...`: the output exists and is newer than every input
    local out=$1; shift
    [ -s "$out" ] || return 1
    for i in "$@"; do [ "$out" -nt "$i" ] || return 1; done
}

B="$P-planner-build.json"
# The gear file is an input too: its armor and talismans set the equip load `computed` holds.
if ! fresh "$B" "$BASE_JSON" ${GEAR:+"$GEAR"}; then
    step "1 build file"
    # `computed` is the planner's cache of what the stats give; the loadout and the shield pass read
    # max HP, stamina and equip load from it, so it is recomputed for the new stats with
    # `er-mechanics-resources.Model` (which reproduces the planner's numbers) rather than kept stale.
    # The planner document spells Endurance `vit`, so `end=` is written under that key.
    jq --arg c "$CLASS" --arg n "$NAME" --arg s "$STATS" '
        .characterClass = $c | .name = $n
        | .stats += ($s | split(",") | map(split("=") | {(if .[0] == "end" then "vit" else .[0] end): (.[1] | tonumber)}) | add)' \
        "$BASE_JSON" | python3 -c "
import importlib.util, json, sys
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
R = load('r', 'scripts/er-mechanics-resources.py')
b = json.load(sys.stdin)
if sys.argv[1]:   # the armor and talismans the upload will equip, so equip load sees them
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):   # apply_gear reports each set it equips
        load('u', 'scripts/planner-ash-loadout-upload.py').apply_gear(b, json.load(open(sys.argv[1])))
worn = lambda slots: [s['name'] for s in slots or [] if s.get('equipSet') or s.get('equipIndex') is not None]
tal = worn((b.get('talismans') or {}).get('slots'))
arm = [n for p in (b.get('protectors') or {}).values() for n in worn(p.get('slots'))]
r = R.Model().resources(R.from_planner(b['stats']), tal, arm, b.get('greatRune'))
b['computed'] = dict(b.get('computed') or {}, maxHealth=r['max_hp'], maxFP=int(r['max_fp']),
                     maxStamina=r['max_stamina'], maxEquipLoad=r['max_equip_load'])
json.dump(b, sys.stdout)" "$GEAR" > "$B"
fi

if ! fresh "$P-embed0.json" "$B"; then
    step "2 embedding without ash rows"
    python3 scripts/er-mechanics-weapon-embed.py --build "$B" --out "$P-embed0.json" > "$P-embed0.log" 2>&1
fi
if ! fresh "$P-wieldable.txt" "$P-embed0.json"; then
    step "3 wieldable weapons"
    python3 scripts/er-mechanics-class-roles.py "$B" --embed "$P-embed0.json" --list-wieldable > "$P-wieldable.txt"
fi
echo "wieldable: $(tr ',' '\n' < "$P-wieldable.txt" | wc -l)"

T=scripts/er-mechanics-ash-table.py
# The sweeps read the build file of step 1, not the base's planner id: the weapons a build already
# holds are this build's, which for `er-weapon-build.py` is an empty inventory, not the base's.
if ! fresh "$P-main.json" "$P-wieldable.txt" || ! fresh "$P-fixed.json" "$B"; then
    step "4 ash sweeps: $AFFINITY over the wieldable weapons, and the fixed-skill weapons"
    python3 $T --build-file "$B" --affinity "$AFFINITY" --grip both --variants --stats "$DMG" --only-extra \
        --jobs "$JOBS" --extra-weapons "$(cat "$P-wieldable.txt")" --json "$P-main.json" --out "$P-main.html" \
        > "$P-main.log" 2>&1 &
    python3 $T --build-file "$B" --affinity "$AFFINITY" --grip both --stats "$DMG" --only-fixed \
        --json "$P-fixed.json" --out "$P-fixed.html" > "$P-fixed.log" 2>&1 &
    wait
fi
if ! fresh "$P-inbuild.json" "$P-main.json" "$P-fixed.json"; then
    step "5 wieldable weapons neither sweep covered"
    MISS=$(python3 -c "
import json, sys
w = open(sys.argv[1]).read().strip().split(',')
swept = {x['weapon'] for f in sys.argv[2:] for x in json.load(open(f))}
print(','.join(x for x in w if x not in swept))" "$P-wieldable.txt" "$P-main.json" "$P-fixed.json")
    python3 $T --build-file "$B" --affinity "$AFFINITY" --grip both --variants --stats "$DMG" --jobs "$JOBS" \
        --extra-weapons "$MISS" --json "$P-inbuild.json" --out "$P-inbuild.html" > "$P-inbuild.log" 2>&1
fi

if ! fresh "$P-embed.json" "$P-main.json" "$P-inbuild.json" "$P-fixed.json"; then
    step "6 embedding with ash rows"
    python3 scripts/er-mechanics-weapon-embed.py --build "$B" --ash-rows "$P-main.json" "$P-inbuild.json" \
        "$P-fixed.json" --out "$P-embed.json" > "$P-embed.log" 2>&1
fi
if ! fresh "$P-true-combos.jsonl" "$P-wieldable.txt"; then
    step "7a true combos"
    python3 scripts/er-mechanics-true-combo-sweep.py "$P-wieldable.txt" > "$P-true-combos.jsonl" 2> "$P-true-combos.err"
fi
if ! fresh "$P-pb.json" "$P-embed.json"; then
    step "7b perfect block"
    python3 scripts/er-mechanics-perfect-block.py --build "$B" --weapons-from "$P-embed.json" --json "$P-pb.json" \
        > "$P-pb.log" 2>&1
fi
if ! fresh "$P-roles.json" "$P-embed.json" "$P-true-combos.jsonl" "$P-pb.json"; then
    step "7c class roles"
    python3 scripts/er-mechanics-class-roles.py "$B" --embed "$P-embed.json" --combos "$P-true-combos.jsonl" \
        --perfect-block "$P-pb.json" --out "$P-roles.json" > "$P-roles.out" 2>&1
fi

if ! fresh "$P-status.json" "$P-roles.json"; then
    step "8 status-affinity sweep of the role picks no sweep has"
    W=$(python3 -c "
import json, sys
r = json.load(open(sys.argv[1]))
have = {(x['weapon'], x['affinity']) for f in sys.argv[2:] for x in json.load(open(f))}
miss = set()
for v in r['classes'].values():
    for role in ('main_hand', 'offhand'):
        p = v.get(role)
        if p and (p['weapon'], p['affinity']) not in have and p['affinity'] != 'Standard':
            miss.add(p['weapon'])
print(','.join(sorted(miss)))" "$P-roles.json" "$P-main.json" "$P-inbuild.json" "$P-fixed.json")
    echo "status weapons: ${W:-none}"
    if [ -n "$W" ]; then
        python3 $T --build-file "$B" --affinity "$AFFINITY" --grip both --stats "$DMG" --only-extra \
            --extra-weapons "$W" --status-weapons "$W" --json "$P-status.json" --out "$P-status.html" > "$P-status.log" 2>&1
    else
        echo "[]" > "$P-status.json"
    fi
fi

step "8b landed share of the weapon-swing ashes (Stormcaller)"
python3 scripts/er-mechanics-ash-landing.py "$P-main.json" "$P-inbuild.json" "$P-fixed.json" "$P-status.json" \
    --stats "$DMG" --cache "$P-landing.json" --jobs "$JOBS" > "$P-landing.log" 2>&1

step "9 loadout"
python3 scripts/er-mechanics-combo-loadout.py "$P-main.json" "$P-inbuild.json" "$P-fixed.json" "$P-status.json" \
    --roles "$P-roles.json" --build "$B" --embed "$P-embed.json" --perfect-block "$P-pb.json" \
    --ranged-cache "$P-ash-ranged.json" --interrupt-cache "$P-ash-interrupt.json" \
    --counter-cache "$DIR/counter.json" --pierce-cache "$P-pierce.json" --buff-cache "$P-buff.json" \
    --landing-cache "$P-landing.json" \
    --interrupt-jobs "$JOBS" --out "$P-plan.json" --html "$P-pages.html" --collapse-twins --landmark 15 \
    --fun-skip-held --rank-terms "$P-rank-terms.json" ${FOCUS:+--focus "$FOCUS"} > "$P-loadout.log" 2>&1

if [ -n "$NO_UPLOAD" ]; then
    # `er-weapon-build.py` prices the plan's equip sets before it lets anything upload.
    # The id is `done` so er-mechanics-pipeline-watch.py marks the run finished.
    step "done (plan written, upload skipped) $P-plan.json"
    exit 0
fi
step "10 upload"
STATS_UP=$(echo "$STATS" | tr -d ' ')
ARGS=(--base "$BASE_ID" --plan "$P-plan.json" --class "$CLASS" --stats "$STATS_UP" --name "$NAME")
[ -n "$GEAR" ] && ARGS+=(--gear "$GEAR")
[ -n "$UPDATE" ] && ARGS+=(--update "$UPDATE")
python3 scripts/planner-ash-loadout-upload.py "${ARGS[@]}"
step "done"
