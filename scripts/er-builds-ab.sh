#!/bin/bash
# A/B of the ranking: a reference copy of scripts/ (e.g. `git archive <ref> scripts | tar -x -C DIR`)
# against this checkout, with the same arguments plus extra ones for the new side.
#
#   bash scripts/er-builds-ab.sh <ref-dir containing scripts/> <out-dir> <tag> [extra new-side args...]
#   bash scripts/er-builds-ab.sh --ref-only <ref-dir> <out-dir>      # only the reference side
#
# Prints "<tag> IDENTICAL" when the two JSON rankings are byte-identical, else "<tag> DIFFERS".
# The reference output is computed once per out-dir (`ab-ref.json`). ER_AB_WEAPONS overrides the
# weapon list (empty: the whole ranking) and ER_AB_ARGS the arguments both sides get. Both sides
# rebuild the exchange-pool cache when their `er-mechanics-exchange.py` differ, so expect a few
# minutes on top of the scoring.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
REF_ONLY=0
if [ "${1:-}" = "--ref-only" ]; then REF_ONLY=1; shift; fi
REF="$1"; OUT="$2"; shift 2
TAG=""
if [ "$REF_ONLY" = 0 ]; then TAG="$1"; shift; fi
W="${ER_AB_WEAPONS-Giant-Crusher,Uchigatana,Dagger,Lance,Great Stars}"
# Every run names its --jobs (policy er-effects-require-pvp-jobs): 2 for a few weapons, 4 for
# the whole window, unless ER_AB_ARGS already gives one.
read -r -a SHARED <<< "${ER_AB_ARGS:---rl 150 --sort score --json}"
case " ${SHARED[*]} " in *" --jobs "*) ;; *) if [ -n "$W" ]; then SHARED+=(--jobs 2); else SHARED+=(--jobs 4); fi ;; esac
if [ -n "$W" ]; then SHARED+=(--weapon "$W"); fi
mkdir -p "$OUT"
if [ ! -s "$OUT/ab-ref.json" ]; then
  python3 "$REF/scripts/er-builds-pvp.py" "${SHARED[@]}" > "$OUT/ab-ref.json" 2> "$OUT/ab-ref.err"
fi
if [ "$REF_ONLY" = 1 ]; then exit 0; fi
python3 "$REPO/scripts/er-builds-pvp.py" "${SHARED[@]}" "$@" > "$OUT/ab-$TAG.json" 2> "$OUT/ab-$TAG.err"
if cmp -s "$OUT/ab-ref.json" "$OUT/ab-$TAG.json"; then echo "$TAG IDENTICAL"; else echo "$TAG DIFFERS"; fi
