#!/usr/bin/env python3
"""Item and build embeddings from er-build-planner's public Elden Ring builds, RL 125-169.

    python3 scripts/er-builds-scrape.py                       # mirror first
    python3 scripts/er-builds-embed.py fit                    # corpus -> embeddings.npz
    python3 scripts/er-builds-embed.py eval                   # held-out recall, EASE vs baselines
    python3 scripts/er-builds-embed.py similar "Rivers of Blood"
    python3 scripts/er-builds-embed.py build 82086df03c4b8e   # a build id or ?b= share link

The first stage of an Elden Ring build optimizer, ported in shape from ds2-mods-rs's
`scripts/ds2-builds-recommend.py` (see its `docs/DS2-BUILD-EMBEDDINGS.md`). Stated deviations:

* Items are identified by the planner's own names, not param row ids. Nothing here joins the
  regulation yet, so no build is checked for stat requirements it cannot meet.
* Only the active loadout set counts. A build with several sets is the set its author left
  selected; the others are alternatives, and merging them would pair items never worn together.

Corpus filter, each rule counted in the `fit` report:

| rule | why |
| --- | --- |
| `stats.rl` inside `--rl-min..--rl-max` | the bracket asked for |
| RL equals the eight attributes' sum minus 79 | the game's own level identity; a mismatch is a planner doc whose stats were never filled in |
| at least one armament equipped | a build with no weapon says nothing about weapons |
| duplicate (same author, same tokens) | authors save many copies of one build |

Tokens, one per equipped thing: `w:` armament, `wa:` armament|affinity (an affinity changes what
the weapon is for, so the pair is its own token beside the plain weapon, which a rare pair falls
back on), `aow:` ash of war, `t:` talisman, `s:` spell, `a:` armor piece, `gr:` great rune, `ct:`
crystal tear. Stats are not tokens: eight always-present stat tokens would swamp a build of fifteen
and pull every query toward "same stats". They are kept beside the embeddings for neighbour
queries instead.

Two models over the same build x token matrix:

* EASE (Steck 2019): a closed-form item-item regression, the recommender. `eval` measures it.
* PPMI co-occurrence -> truncated SVD: dense item vectors for similarity, and a build's vector is
  the normalized mean of its items'. These are the embeddings; `similar` and `build` use them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

CACHE = Path.home() / ".cache/er-build-planner"
API = "https://er-inventory-api.nyasu.business"
ATTRS = ["vig", "mnd", "vit", "str", "dex", "int", "fth", "arc"]  # planner keys; `vit` is Endurance
LEVEL_OFFSET = 79  # RL = sum of the eight attributes - 79
KINDS = {"w": "armament", "wa": "armament|affinity", "aow": "ash of war", "t": "talisman",
         "s": "spell", "a": "armor", "gr": "great rune", "ct": "crystal tear"}


# --------------------------------------------------------------------------------------------
# corpus

def active_set(build: dict, kind: str) -> int:
    sets = (build.get("sets") or {}).get(kind) or []
    return next((i for i, s in enumerate(sets) if s.get("active")), 0)


def equipped(slots: list, active: int) -> list[dict]:
    """Slots worn in the active set. `equipSet[i]` is the slot's position in set `i` (holes are
    sets it is not in); `equipIndex` caches the active one and is all a pre-sets build carries."""
    out = []
    for s in slots or []:
        es = s.get("equipSet")
        pos = (es[active] if active < len(es) else None) if isinstance(es, list) else s.get("equipIndex")
        if pos is not None:
            out.append(s)
    return out


def tokens(build: dict) -> list[str]:
    toks = []
    for s in equipped((build.get("inventory") or {}).get("slots"), active_set(build, "weapons")):
        name = s.get("name")
        if not name:
            continue
        toks += [f"w:{name}", f"wa:{name}|{s.get('infusion') or 'Standard'}"]
        # "No Skill" is the planner's placeholder for an armament with no ash of war, not a skill.
        if s.get("weaponArt") and s["weaponArt"] != "No Skill":
            toks.append(f"aow:{s['weaponArt']}")
    for s in equipped((build.get("talismans") or {}).get("slots"), active_set(build, "talismans")):
        toks.append(f"t:{s['name']}")
    pa = active_set(build, "protectors")
    for part in ("head", "body", "arms", "legs"):
        for s in equipped(((build.get("protectors") or {}).get(part) or {}).get("slots"), pa):
            toks.append(f"a:{s['name']}")
    for s in (build.get("spells") or {}).get("slots") or []:
        if s.get("name") and s.get("equipIndex", 0) is not None:
            toks.append(f"s:{s['name']}")
    if build.get("greatRune"):
        toks.append(f"gr:{build['greatRune']}")
    for t in (build.get("items") or {}).get("crystalTears") or []:
        if t:
            toks.append(f"ct:{t}")
    return sorted(set(toks))


def stats_of(build: dict) -> dict | None:
    st = build.get("stats") or {}
    try:
        return {"rl": int(st["rl"]), **{k: int(st[k]) for k in ATTRS}}
    except (KeyError, TypeError, ValueError):
        return None


def load_corpus(path: Path, rl_min: int, rl_max: int) -> tuple[list[dict], Counter]:
    why: Counter = Counter()
    seen: set = set()
    out = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        b = row["build"]
        st = stats_of(b)
        if st is None:
            why["no stats"] += 1
            continue
        if not rl_min <= st["rl"] <= rl_max:
            why["outside RL window"] += 1
            continue
        if sum(st[k] for k in ATTRS) - LEVEL_OFFSET != st["rl"]:
            why["RL disagrees with attributes"] += 1
            continue
        toks = tokens(b)
        if not any(t.startswith("w:") for t in toks):
            why["no armament equipped"] += 1
            continue
        key = (row.get("user"), tuple(toks))
        if key in seen:
            why["duplicate"] += 1
            continue
        seen.add(key)
        why["kept"] += 1
        out.append({"id": row["id"], "name": b.get("name", ""), "pve": bool(b.get("isPvE")),
                    "stats": st, "tokens": toks})
    return out, why


# --------------------------------------------------------------------------------------------
# models

def matrix(corpus: list[dict], vocab: dict[str, int]) -> np.ndarray:
    X = np.zeros((len(corpus), len(vocab)), dtype=np.float32)
    for i, b in enumerate(corpus):
        for t in b["tokens"]:
            j = vocab.get(t)
            if j is not None:
                X[i, j] = 1.0
    return X


def ease(X: np.ndarray, lam: float) -> np.ndarray:
    G = (X.T @ X).astype(np.float64)
    G[np.diag_indices_from(G)] += lam
    P = np.linalg.inv(G)
    B = -P / np.diag(P)
    B[np.diag_indices_from(B)] = 0.0
    return B.astype(np.float32)


def ppmi_svd(X: np.ndarray, dim: int, alpha: float = 1.0) -> np.ndarray:
    """`alpha` < 1 smooths context counts (Levy, Goldberg & Dagan 2015) against raw PPMI's bias
    toward rare tokens. Measured on this corpus 2026-09-29, `eval` held-out recall@10: 39.0% at
    1.0, 33.3% at 0.75, and the neighbour lists barely moved, so it is off."""
    C = (X.T @ X).astype(np.float64)
    np.fill_diagonal(C, 0.0)
    total = C.sum()
    row = C.sum(1, keepdims=True)
    ctx = row.T ** alpha
    ctx = ctx / ctx.sum() * total
    with np.errstate(divide="ignore", invalid="ignore"):
        pmi = np.log((C * total) / (row @ ctx))
    ppmi = np.nan_to_num(np.maximum(pmi, 0.0), nan=0.0, posinf=0.0, neginf=0.0)
    U, S, _ = np.linalg.svd(ppmi, full_matrices=False)
    k = min(dim, len(S))
    E = U[:, :k] * np.sqrt(S[:k])
    n = np.linalg.norm(E, axis=1, keepdims=True)
    return (E / np.where(n == 0, 1, n)).astype(np.float32)


def build_vec(E: np.ndarray, idx: list[int]) -> np.ndarray:
    if not idx:
        return np.zeros(E.shape[1], dtype=np.float32)
    v = E[idx].mean(0)
    n = np.linalg.norm(v)
    return v / n if n else v


def fit(corpus: list[dict], min_df: int, lam: float, dim: int) -> dict:
    df = Counter(t for b in corpus for t in b["tokens"])
    vocab_list = sorted(t for t, c in df.items() if c >= min_df)
    vocab = {t: i for i, t in enumerate(vocab_list)}
    X = matrix(corpus, vocab)
    E = ppmi_svd(X, dim)
    BE = np.stack([build_vec(E, [vocab[t] for t in b["tokens"] if t in vocab]) for b in corpus])
    return {"vocab": vocab_list, "df": np.array([df[t] for t in vocab_list]), "X": X,
            "B": ease(X, lam), "E": E, "BE": BE}


# --------------------------------------------------------------------------------------------
# eval

def evaluate(corpus: list[dict], min_df: int, lam: float, dim: int, folds: int, k: int, seed: int) -> None:
    """Hide one item from every test build and ask each model to put it back in its top k.
    Candidates exclude what the build already holds; the hidden item must be in the fold's vocab."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(corpus))
    hits: Counter = Counter()
    by_kind: dict = {}
    n = 0
    for f in range(folds):
        test_idx = set(order[f::folds].tolist())
        train = [b for i, b in enumerate(corpus) if i not in test_idx]
        m = fit(train, min_df, lam, dim)
        vocab = {t: i for i, t in enumerate(m["vocab"])}
        pop = m["df"].astype(np.float32)
        for i in test_idx:
            held = [t for t in corpus[i]["tokens"] if t in vocab and not t.startswith("wa:")]
            if len(held) < 3:
                continue
            h = held[rng.integers(len(held))]
            # The wa: pair of a hidden armament leaks it, so it is hidden alongside.
            rest = [vocab[t] for t in corpus[i]["tokens"] if t in vocab and t != h
                    and not (h.startswith("w:") and t.startswith("wa:" + h[2:] + "|"))]
            x = np.zeros(len(vocab), dtype=np.float32)
            x[rest] = 1
            scores = {"ease": x @ m["B"], "embedding": m["E"] @ build_vec(m["E"], rest), "popularity": pop.copy()}
            kind = h.split(":", 1)[0]
            by_kind.setdefault(kind, Counter())["n"] += 1
            for name, s in scores.items():
                s = s.copy()
                s[rest] = -np.inf
                top = np.argpartition(-s, k)[:k]
                if vocab[h] in top:
                    hits[name] += 1
                    by_kind[kind][name] += 1
            n += 1
    print(f"held-out recall@{k}, {folds}-fold, {n} builds")
    for name in ("ease", "embedding", "popularity"):
        print(f"  {name:<11} {hits[name] / n:6.1%}")
    print(f"  per hidden-item kind (ease / embedding / popularity):")
    for kind, c in sorted(by_kind.items(), key=lambda kv: -kv[1]["n"]):
        print(f"    {KINDS.get(kind, kind):<18} n={c['n']:<5} " +
              " / ".join(f"{c[x] / c['n']:5.1%}" for x in ("ease", "embedding", "popularity")))


# --------------------------------------------------------------------------------------------
# queries

def load_model(path: Path) -> dict:
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["meta"]))
    return {"vocab": list(z["vocab"]), "df": z["df"], "B": z["B"], "E": z["E"], "BE": z["BE"],
            "S": z["S"], "meta": meta}


def fetch_build(ref: str) -> dict:
    bid = re.search(r"[?&]b=([0-9a-f]+)", ref)
    bid = bid.group(1) if bid else ref
    mirror = CACHE / "builds.jsonl"
    if mirror.exists():
        for line in mirror.read_text().splitlines():
            row = json.loads(line)
            if row["id"] == bid:
                return row["build"]
    req = urllib.request.Request(f"{API}/inventories/{bid}", headers={"User-Agent": "er-mods-rs build research"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"build {bid}: not in the mirror, and the planner answered {e.code} (deleted or private)")
    return d.get("data", d)


def show_similar(m: dict, query: str, n: int) -> int:
    q = query.lower()
    hits = [i for i, t in enumerate(m["vocab"]) if q in t.lower()]
    if not hits:
        print(f"no token contains {query!r}")
        return 1
    i = max(hits, key=lambda j: m["df"][j])
    sims = m["E"] @ m["E"][i]
    # An armament and its own affinity pairs are one item spelled twice; they are always nearest.
    base = m["vocab"][i].split(":", 1)[1].split("|")[0]
    for j, t in enumerate(m["vocab"]):
        if j == i or t in (f"w:{base}",) or t.startswith(f"wa:{base}|"):
            sims[j] = -np.inf
    print(f"{m['vocab'][i]}  (in {m['df'][i]} builds)")
    for j in np.argsort(-sims)[:n]:
        print(f"  {sims[j]:.3f}  {m['vocab'][j]:<60} {m['df'][j]:>5}")
    return 0


def show_pairs(m: dict, corpus: list[dict], query: str, kind: str, n: int) -> int:
    """Items of `kind` worn with an armament, three ways, each with its build count.

    `lift` = P(item | armament) / P(item) from the corpus itself; `ease` = the armament's EASE
    weight toward the item (what the recommender would add); `cos` = embedding cosine. A pair
    worn together in fewer than three builds is not shown: its lift is noise.
    """
    want = f"w:{query}"
    vocab = {t: i for i, t in enumerate(m["vocab"])}
    if want not in vocab:
        near = [t for t in m["vocab"] if t.startswith("w:") and query.lower() in t.lower()]
        print(f"no armament {query!r}" + (f"; did you mean {near[:5]}" if near else ""))
        return 1
    with_w = [b for b in corpus if want in b["tokens"]]
    if not with_w:
        print(f"{want}: in the model but in no build of this corpus window")
        return 1
    df = Counter(t for b in corpus for t in set(b["tokens"]) if t.startswith(kind + ":"))
    co = Counter(t for b in with_w for t in set(b["tokens"]) if t.startswith(kind + ":"))
    i = vocab[want]
    rows = []
    for t, c in co.items():
        if c < 3:
            continue
        lift = (c / len(with_w)) / (df[t] / len(corpus))
        j = vocab.get(t)
        rows.append((lift, c, t, m["B"][i, j] if j is not None else float("nan"),
                     float(m["E"][j] @ m["E"][i]) if j is not None else float("nan")))
    print(f"{want}: {len(with_w)} of {len(corpus)} builds; {KINDS[kind]} worn with it in 3+ of them")
    print(f"  {'lift':>5} {'with':>5} {'all':>5} {'ease':>7} {'cos':>6}  item")
    for lift, c, t, e, cos in sorted(rows, reverse=True)[:n]:
        print(f"  {lift:5.2f} {c:5d} {df[t]:5d} {e:+7.3f} {cos:6.3f}  {t.split(':', 1)[1]}")
    return 0


def show_build(m: dict, ref: str, n: int) -> int:
    b = fetch_build(ref)
    vocab = {t: i for i, t in enumerate(m["vocab"])}
    toks = tokens(b)
    idx = [vocab[t] for t in toks if t in vocab]
    print(f"{b.get('name', '')!r}: {len(toks)} tokens, {len(idx)} in vocab, stats {stats_of(b)}")
    x = np.zeros(len(vocab), dtype=np.float32)
    x[idx] = 1
    s = x @ m["B"]
    s[idx] = -np.inf
    print("recommended (EASE), best per kind:")
    for kind in ("w", "wa", "aow", "t", "a", "s", "ct", "gr"):
        # A score at or below zero is EASE saying the build's items argue against it, not a pick.
        cand = [j for j in np.argsort(-s) if m["vocab"][j].startswith(kind + ":") and s[j] > 0][:n]
        if cand:
            print(f"  {KINDS[kind]}:")
            for j in cand:
                print(f"    {s[j]:+.3f}  {m['vocab'][j].split(':', 1)[1]}")
    v = build_vec(m["E"], idx)
    sims = m["BE"] @ v
    print("nearest builds (embedding cosine):")
    for j in np.argsort(-sims)[:n]:
        b2 = m["meta"]["builds"][j]
        print(f"  {sims[j]:.3f}  {b2['id']}  RL{b2['rl']:<4} {b2['name'][:50]}")
    return 0


# --------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fit", "eval", "similar", "build", "pairs"])
    ap.add_argument("--kind", default="t", choices=sorted(KINDS), help="pairs: item kind, default talisman")
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--mirror", type=Path, default=CACHE / "builds.jsonl")
    ap.add_argument("--model", type=Path, default=CACHE / "embeddings.npz")
    ap.add_argument("--rl-min", type=int, default=125)
    ap.add_argument("--rl-max", type=int, default=169)
    ap.add_argument("--min-df", type=int, default=3, help="drop tokens seen in fewer builds")
    ap.add_argument("--lam", type=float, default=50.0, help="EASE L2")
    ap.add_argument("--dim", type=int, default=64, help="embedding dimensions")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("-n", type=int, default=8, help="rows shown per list")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    if a.cmd in ("similar", "build"):
        if not a.arg:
            ap.error(f"{a.cmd} needs an argument")
        m = load_model(a.model)
        return show_similar(m, a.arg, a.n) if a.cmd == "similar" else show_build(m, a.arg, a.n)

    corpus, why = load_corpus(a.mirror, a.rl_min, a.rl_max)
    if a.cmd == "pairs":
        if not a.arg:
            ap.error("pairs needs an armament name")
        m = load_model(a.model)
        if m["meta"]["rl"] != [a.rl_min, a.rl_max]:
            ap.error(f"the model was fit on RL {m['meta']['rl']}; pass the same --rl-min/--rl-max")
        return show_pairs(m, corpus, a.arg, a.kind, a.n)
    print(f"corpus RL {a.rl_min}-{a.rl_max}: " + ", ".join(f"{k} {v}" for k, v in why.most_common()))
    if a.cmd == "eval":
        evaluate(corpus, a.min_df, a.lam, a.dim, a.folds, a.k, a.seed)
        return 0

    m = fit(corpus, a.min_df, a.lam, a.dim)
    kinds = Counter(t.split(":", 1)[0] for t in m["vocab"])
    print(f"vocab {len(m['vocab'])} tokens (df >= {a.min_df}): " +
          ", ".join(f"{KINDS[k]} {kinds[k]}" for k in KINDS if kinds[k]))
    meta = {"rl": [a.rl_min, a.rl_max], "min_df": a.min_df, "lam": a.lam, "dim": a.dim,
            "builds": [{"id": b["id"], "name": b["name"], "rl": b["stats"]["rl"], "pve": b["pve"]} for b in corpus]}
    S = np.array([[b["stats"][k] for k in ATTRS] for b in corpus], dtype=np.int16)
    np.savez_compressed(a.model, vocab=np.array(m["vocab"]), df=m["df"], B=m["B"], E=m["E"], BE=m["BE"],
                        S=S, meta=np.array(json.dumps(meta)))
    print(f"wrote {a.model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
