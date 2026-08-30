#!/usr/bin/env python3
"""The complete STATIC inventory of 1.17 exposure, per cdylib.

WHY A STATIC INVENTORY AND NOT THE LOGS
---------------------------------------
`er-game-base` refuses any 1.16.2 address with no verified 1.17 mapping and logs the refusal.
That log is the obvious work list and it is the wrong one: a refusal is only written when the
code path actually RUNS. One 25-hour session logged 679,812 refusals across eight DLL logs, and
that is a FLOOR -- every feature nobody triggered is silent, and some cannot easily be triggered
at all. So the work list has to come from the SOURCE.

WHAT IT COUNTS
--------------
Every game address a cdylib can resolve, found in four declaration forms. Missing any one of
them under-reports, and the third form has already cost a black screen:

  1. `const FOO_RVA: usize = 0x749b20;`
  2. `const FOO_RVA: usize = BAR_RVA;`                     -- derived from another constant
  3. `const FOO_RVA: usize = SomeEnum::Variant as usize;`  -- the value lives on the enum; a
     literal-hex scan misses all 37 of these. `TITLE_TOP_DIALOG_IS_IN_STATE_RVA` is one.
  4. a bare literal in a hook/seam table (`HookSpec { rva: 0x836f30, detour: ... }`), which
     carries no `_RVA` name at all. er-reload-trace declares 39 addresses this way and
     er-invasion-warp's `MapSeam` table another 13.

HOW IT CLASSIFIES
-----------------
The authority is not a TSV; it is the two tables `er-game-base/build.rs` generates, which this
script reproduces exactly (asserted by --selftest against the built `address_map_1170.rs`):

  call_ok    the address is in VERIFIED_1162_TO_1170, so `game_rva` / `resolve_game_address`
             will translate it. Assembled from the DETOURABLE rows of
             rva-map-1162-to-1170.verified.tsv, ALL rows of .needed.tsv, ALL rows of .data.tsv,
             minus the quarantine and minus every DIVERGES verdict.
  detour_ok  the address is in DETOUR_SAFE_1162_TO_1170 -- the stricter licence, which needs
             both a body comparison and BOTH-ENTRIES/NEITHER-ENTRY evidence from .pdata.
  prov       where the row came from: VERIFIED / DATA / FUNCTION / HELD-BACK / NONE.

An address with `call_ok = false` WILL be refused the moment its path runs.

    python3 scripts/audit-1170-coverage-inventory.py --report      # the markdown inventory
    python3 scripts/audit-1170-coverage-inventory.py --json OUT
    python3 scripts/audit-1170-coverage-inventory.py --selftest
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECON = os.path.join(REPO, "docs", "recon")
BASE = 0x140000000

# ---------------------------------------------------------------- declaration forms
CONST = re.compile(r"const\s+([A-Za-z0-9_]*RVA[A-Za-z0-9_]*)\s*:\s*(?:usize|u32|u64)\s*=\s*(0x[0-9a-fA-F_]+)")
ALIAS = re.compile(r"const\s+([A-Za-z0-9_]*RVA[A-Za-z0-9_]*)\s*:\s*(?:usize|u32|u64)\s*=\s*(\w+)::(\w+)\s+as\s+(?:usize|u32|u64)")
DERIVE = re.compile(r"const\s+([A-Za-z0-9_]*RVA[A-Za-z0-9_]*)\s*:\s*(?:usize|u32|u64)\s*=\s*([A-Za-z0-9_:]*[A-Z0-9_]*RVA[A-Z0-9_]*)\s*(?:as\s+(?:usize|u32|u64)\s*)?;")
VARIANT = re.compile(r"^\s*(\w+)\s*=\s*(0x[0-9a-fA-F_]+)\s*,", re.M)
ENUMHEAD = re.compile(r"pub\s+enum\s+(\w+)\s*\{")
RVA_FIELD = re.compile(r"\brva\s*:\s*(0x[0-9a-fA-F_]+)")
BASE_PLUS_LIT = re.compile(r"\b(?:base|image_base|module_base|game_base)\s*\+\s*(0x[0-9a-fA-F_]{5,})")
ADDR_EXPR = re.compile(r"\b(?:base|image_base|module_base|game_base)\s*\+\s*")

# ---------------------------------------------------------------- use-site shapes
GATED = re.compile(r"\b(?:game_rva|game_rva_named|game_data_addr|read_game_global|resolve_game_address|resolve_detour_address|game_ptr)\s*\(")
HOOKPRIM = re.compile(r"\bMhHook::new\b|\bregister_union_hook\b|\bresolve_detour_address\b|\bMH_CreateHook\b")
EXEC_USE = re.compile(r"(?:transmute|as\s+\*const\s+fn|as\s+extern)\s*[(<]?\s*$")
RAW_WRITE = re.compile(r"write_code_byte|write_code_bytes|\*\s*target\s*=|\*\s*(?:addr|address|slot)\s*=|as\s*\*mut\s+[\w:]+\s*\)\s*=")
DETOUR_FIELD = re.compile(r"\bdetour\s*:")
SIGCHECK = re.compile(r"prologue|signature|EXPECTED|expected_", re.I)

MIN_VERIFIED_INSNS = 12
# Below the first page of the image nothing is a game address; a discriminant that small is a
# mis-parse, not an RVA.
MIN_PLAUSIBLE_RVA = 0x1000
# Names that describe a RANGE, not an address -- `AV_GAME_TEXT_RVA_MAX`, `GX_CMD_QUEUE_WRAPPER_RVA_MIN`.
# Translating a bound is a category error, and left in they outrank real work by sitting in hot
# comparison code. Same filter `select-needed-1170-rows.py` applies, for the same reason.
BOUND = re.compile(r"_(MIN|MAX|BOUND|BASE|SIZE|LEN|LENGTH|COUNT|END|START|STRIDE|ALIGN)$")
DETOURABLE_ENTRY = ("BOTH-ENTRIES", "NEITHER-ENTRY")


def rs_files(root):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "target"]
        out += [os.path.join(base, f) for f in files if f.endswith(".rs")]
    return sorted(out)


# ---------------------------------------------------------------- symbol table
def build_symbol_table(paths):
    """`name -> rva`, over all four declaration forms. Enum variants are keyed `Enum::Variant`."""
    consts, aliases, by_enum, any_variant, decl = {}, {}, {}, {}, {}
    for path in paths:
        text = open(path, encoding="utf-8", errors="replace").read()
        for m in ENUMHEAD.finditer(text):
            name, depth, i = m.group(1), 0, m.end() - 1
            # ONLY `*Rva` enums. Every `pub enum` with hex discriminants was taken at first, and
            # `MenuEventId::MoveA = 0x0` then entered the inventory as game address 0x0 -- an
            # address that is not one, ranked above real work by the sheer count of its references.
            if not name.endswith("Rva"):
                continue
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            for variant, value in VARIANT.findall(text[m.end():i]):
                rva = int(value.replace("_", ""), 16)
                by_enum[(name, variant)] = rva
                any_variant.setdefault(variant, rva)
                decl.setdefault(f"{name}::{variant}", (path, text[: m.end()].count("\n") + 1))
        for m in CONST.finditer(text):
            consts.setdefault(m.group(1), int(m.group(2).replace("_", ""), 16))
            decl.setdefault(m.group(1), (path, text[: m.start()].count("\n") + 1))
        for m in ALIAS.finditer(text):
            aliases.setdefault(m.group(1), (m.group(2), m.group(3)))
            decl.setdefault(m.group(1), (path, text[: m.start()].count("\n") + 1))
    for name, (enum, variant) in aliases.items():
        rva = by_enum.get((enum, variant), any_variant.get(variant))
        if rva is not None:
            consts.setdefault(name, rva)
    for path in paths:
        text = open(path, encoding="utf-8", errors="replace").read()
        for m in DERIVE.finditer(text):
            name, source = m.group(1), m.group(2).rsplit("::", 1)[-1]
            if name not in consts and source in consts:
                consts[name] = consts[source]
                decl.setdefault(name, (path, text[: m.start()].count("\n") + 1))
    syms = dict(consts)
    for (enum, variant), rva in by_enum.items():
        syms[f"{enum}::{variant}"] = rva
    return syms, decl


# ---------------------------------------------------------------- the maps, as build.rs reads them
def _rows(path):
    if not os.path.isfile(path):
        return []
    return [l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8", errors="replace")
            if l.strip() and not l.startswith("#")]


def _rva(s):
    try:
        v = int(s.strip(), 16)
    except ValueError:
        return None
    return v - BASE if v >= BASE else v


def verdict_table(path):
    """(every source RVA, the DETOURABLE ones, the DIVERGES ones) -- build.rs's own filter."""
    allr, det, div = set(), set(), set()
    for f in _rows(path):
        if len(f) < 3:
            continue
        src = _rva(f[0])
        if src is None:
            continue
        allr.add(src)
        if f[2] == "DIVERGES":
            div.add(src)
            continue
        if len(f) < 7:
            continue
        if f[2] == "IDENTICAL":
            try:
                if int(f[4].strip()) < MIN_VERIFIED_INSNS:
                    continue
            except ValueError:
                continue
        elif f[2] != "BYTE-IDENTICAL":
            continue
        if f[6].strip() in DETOURABLE_ENTRY:
            det.add(src)
    return allr, det, div


def plain_map(path):
    return {_rva(f[0]) for f in _rows(path) if len(f) >= 2 and _rva(f[0]) is not None and _rva(f[1]) is not None}


def load_maps(extra_observed=None):
    ver_all, ver_det, ver_div = verdict_table(os.path.join(RECON, "rva-map-1162-to-1170.verified.tsv"))
    nv_all, nv_det, nv_div = verdict_table(os.path.join(RECON, "rva-map-1162-to-1170.needed-verified.tsv"))
    func = plain_map(os.path.join(RECON, "rva-map-1162-to-1170.needed.tsv"))
    data = plain_map(os.path.join(RECON, "rva-map-1162-to-1170.data.tsv"))
    quar = {_rva(f[0]) for f in _rows(os.path.join(RECON, "rva-1170-quarantine.tsv")) if _rva(f[0]) is not None}
    held = quar | ver_div | nv_div
    observed = set()
    p = os.path.join(RECON, "rva-1170-observed-refusals.txt")
    if os.path.isfile(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    observed.add(int(line, 16))
                except ValueError:
                    pass
    # A SECOND source of "this path demonstrably runs". The tracked refusal file is a snapshot of
    # one boot; a fresh scan of the current DLL logs sees whatever the last runs reached, and the
    # 14.9 GB autoload log has to be scanned out-of-band anyway. Either VA or RVA form is accepted.
    if extra_observed and os.path.isfile(extra_observed):
        for line in open(extra_observed, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                v = int(line, 16)
            except ValueError:
                continue
            observed.add(v - BASE if v >= BASE else v)
    return {
        "ver_all": ver_all, "ver_det": ver_det, "nv_all": nv_all, "nv_det": nv_det,
        "func": func, "data": data, "held": held, "observed": observed,
        "call": (ver_det | func | data) - held,
        "detour": (ver_det | nv_det) - held,
    }


def provenance(rva, maps):
    if rva in maps["held"]:
        return "HELD-BACK"
    if rva in maps["ver_det"] or rva in maps["nv_det"]:
        return "VERIFIED"
    if rva in maps["data"]:
        return "DATA"
    if rva in maps["func"]:
        return "FUNCTION"
    return "NONE"


# ---------------------------------------------------------------- the scan
def collect(maps):
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        capture_output=True, text=True, cwd=REPO, check=True, timeout=60).stdout)
    pkgs = {p["name"]: p for p in meta["packages"]}
    cdylibs = sorted(p["name"] for p in meta["packages"]
                     if any("cdylib" in t["kind"] for t in p["targets"]))

    def closure(name, seen=None):
        seen = set() if seen is None else seen
        if name in seen or name not in pkgs:
            return seen
        seen.add(name)
        for dep in pkgs[name]["dependencies"]:
            if dep["kind"] is None and dep["name"] in pkgs:
                closure(dep["name"], seen)
        return seen

    srcs = {c: rs_files(os.path.join(os.path.dirname(pkgs[c]["manifest_path"]), "src")) for c in pkgs}
    all_paths = sorted({p for c in pkgs for p in srcs[c]})
    syms, _decl = build_symbol_table(all_paths)
    ident = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in syms), key=len, reverse=True)) + r")\b")

    per_file = {}
    for path in all_paths:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        file_hooks = any(HOOKPRIM.search(l) for l in lines)
        hits = []

        def add(i, name, rva, forced_detour=False):
            line = lines[i]
            window = "\n".join(lines[max(0, i - 30): i + 31])
            near = "\n".join(lines[max(0, i - 8): i + 9])
            pos = line.find(name) if name in line else 0
            expr = ADDR_EXPR.search(line)
            if expr and expr.end() <= pos:
                pos = expr.start()
            before = line[:pos].rstrip()
            if RAW_WRITE.search(near):
                kind = "write"
            elif EXEC_USE.search(before) or EXEC_USE.search("\n".join(lines[max(0, i - 1): i + 1]).rstrip()):
                kind = "exec"
            else:
                kind = "read"
            hits.append({
                "line": i + 1, "name": name, "rva": rva, "kind": kind,
                "detour": forced_detour or bool(file_hooks and HOOKPRIM.search(window)),
                "gated": bool(GATED.search(near)), "sig": bool(SIGCHECK.search(near)),
                "text": line.strip()[:140],
            })

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for m in ident.finditer(line):
                if re.search(r"\bconst\s+" + re.escape(m.group(1)) + r"\b", line):
                    continue
                if syms[m.group(1)] < MIN_PLAUSIBLE_RVA or BOUND.search(m.group(1)):
                    continue
                add(i, m.group(1), syms[m.group(1)])
            for m in RVA_FIELD.finditer(line):
                rva = int(m.group(1).replace("_", ""), 16)
                block = "\n".join(lines[max(0, i - 12): i + 12])
                cn = re.search(r"const\s+([A-Z0-9_]+)\s*:", block)
                nm = re.search(r'name\s*:\s*"([^"]+)"', block)
                label = (f"(literal {cn.group(1)})" if cn else
                         f'(literal "{nm.group(1)}")' if nm else f"(literal 0x{rva:x})")
                add(i, label, rva, forced_detour=bool(DETOUR_FIELD.search(block)))
            for m in BASE_PLUS_LIT.finditer(line):
                rva = int(m.group(1).replace("_", ""), 16)
                # Page-aligned means a RANGE bound, not a function: `openfile >= base + 0x0800_0000`
                # is a sanity check on a runtime-resolved pointer, and it entered the inventory as
                # game address 0x8000000. No real RVA in this tree is page-aligned.
                if MIN_PLAUSIBLE_RVA <= rva <= 0x8000000 and rva & 0xFFF:
                    add(i, f"(literal base+0x{rva:x})", rva)
        per_file[path] = hits

    owner = {p: c for c in pkgs for p in srcs[c]}
    out = {"cdylibs": cdylibs, "crates": {}, "classes": {}}
    for dll in cdylibs:
        addrs = {}
        for crate in closure(dll):
            for path in srcs[crate]:
                for h in per_file.get(path, []):
                    e = addrs.setdefault(h["rva"], {
                        "names": set(), "detour": False, "kinds": set(), "crates": set(),
                        "gated": 0, "ungated": 0, "sig": False, "sites": []})
                    e["names"].add(h["name"])
                    e["detour"] |= h["detour"]
                    e["kinds"].add(h["kind"])
                    e["crates"].add(owner[path])
                    e["sig"] |= h["sig"]
                    e["gated" if h["gated"] else "ungated"] += 1
                    e["sites"].append([os.path.relpath(path, REPO), h["line"], h["kind"],
                                       h["detour"], h["gated"], h["text"]])
        out["crates"][dll] = {
            hex(r): {"names": sorted(v["names"]), "detour": v["detour"], "kinds": sorted(v["kinds"]),
                     "crates": sorted(v["crates"]), "gated": v["gated"], "ungated": v["ungated"],
                     "sig": v["sig"], "sites": v["sites"][:40]}
            for r, v in addrs.items()}
    for rva in sorted({int(r, 16) for a in out["crates"].values() for r in a}):
        out["classes"][hex(rva)] = {
            "prov": provenance(rva, maps),
            "call_ok": rva in maps["call"],
            "detour_ok": rva in maps["detour"],
            "observed_refusal": rva in maps["observed"],
        }
    return out


def selftest(maps):
    """The tables this reproduces must equal the ones the release build actually generated."""
    failures = []
    import glob
    generated = sorted(glob.glob(os.path.join(REPO, "target", "**", "address_map_1170.rs"), recursive=True),
                       key=os.path.getmtime)
    # Only a build NEWER than every map file proves anything. The maps are edited constantly while
    # the migration is in progress, and comparing against a table generated before the last TSV
    # edit reports a difference that is real and means nothing about this script.
    newest_map = max((os.path.getmtime(os.path.join(RECON, f)) for f in os.listdir(RECON)
                      if f.startswith("rva-")), default=0)
    if not generated:
        failures.append("no generated address_map_1170.rs to compare against; build er-game-base first")
    elif os.path.getmtime(generated[-1]) < newest_map:
        print("selftest SKIP: the newest generated address_map_1170.rs predates the map files; "
              "rebuild er-game-base to compare")
        generated = []
    else:
        pass

    if generated:
        text = open(generated[-1], encoding="utf-8").read()

        def grab(name):
            m = re.search(name + r": \[\(u32, u32\); \d+\] = \[(.*?)\n\];", text, re.S)
            return {int(a, 16) for a, _ in re.findall(r"\((0x[0-9a-f]+), (0x[0-9a-f]+)\)", m.group(1))}

        if grab("VERIFIED_1162_TO_1170") != maps["call"]:
            failures.append("the reproduced CALL table differs from the generated one")
        if grab("DETOUR_SAFE_1162_TO_1170") != maps["detour"]:
            failures.append("the reproduced DETOUR table differs from the generated one")
    paths = []
    for base, dirs, files in os.walk(os.path.join(REPO, "crates")):
        dirs[:] = [d for d in dirs if d != "target"]
        paths += [os.path.join(base, f) for f in files if f.endswith(".rs")]
    syms, _ = build_symbol_table(sorted(paths))
    # The enum-alias form, whose absence black-screened the game on 2026-08-29.
    if syms.get("TITLE_TOP_DIALOG_IS_IN_STATE_RVA") != 0x749B20:
        failures.append("enum-alias constants are invisible: TITLE_TOP_DIALOG_IS_IN_STATE_RVA")
    if syms.get("GET_CURRENT_MAP_ID_RVA") != 0x5EEFB0:
        failures.append("plain literal constants are invisible: GET_CURRENT_MAP_ID_RVA")
    for line in failures:
        print(f"selftest FAIL: {line}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0



# ---------------------------------------------------------------- ranking and the markdown report
# How many `~/Elden/*.me3` profiles load each DLL. A proxy for "is anyone running this", which is
# not the same question as "does it work" and is the only cheap answer to it available offline.
PROFILE_LOADS = {
    "er-quickload": 36, "er-invasion-warp": 18, "er-crash-logging": 11, "er-telemetry": 8,
    "er-quit-menu": 7, "er-save-picker": 7, "er-net-effects": 7, "er-better-refills": 7,
    "er-ersc-sigshim": 6, "er-armament-icons": 6, "er-player-name-filter": 6,
    "er-seamless-bugfixes": 6, "er-inventory-sort": 5, "er-reload-trace": 4,
    "er-charm-enemies": 3, "er-loading-bar": 3, "er-save-disable": 3, "er-invasion-path": 3,
    "er-refill-all": 3, "er-input-harness": 3, "er-build-import": 2, "er-loading-portrait": 2,
    "er-build-watermark": 2, "er-death-persist": 1, "er-diag-harness": 1,
    "mushroom-man-runtime": 1, "er-ags-stub": 0,
}

FEATURE_OF_PATH = [
    ("startup_hooks/quit_menu/save_picker", "save picker (Load Character from File)"),
    ("startup_hooks/quit_menu/profile_rows", "System>Quit cloned rows"),
    ("startup_hooks/quit_menu/system_quit_ownership_repro", "System>Quit ownership guards"),
    ("startup_hooks/quit_menu/system_quit_dialog", "System>Quit dialog handlers"),
    ("startup_hooks/quit_menu/save_flow_boxes", "save-flow message boxes"),
    ("startup_hooks/quit_menu", "System>Quit menu"),
    ("startup_hooks/loading_cover", "boot / loading cover"),
    ("startup_hooks/diagnostics", "startup diagnostics hooks"),
    ("own_stepper", "own-stepper (title step table)"),
    ("own_load", "own-load drive"),
    ("continue_load", "product Continue"),
    ("experiments/trace", "menu / file trace"),
    ("crashlog", "crash logging"),
    ("er-invasion-path/src/navpath", "invasion path: navmesh"),
    ("er-invasion-path/src/sfx", "invasion path: SFX"),
    ("er-invasion-path", "invasion path"),
    ("er-invasion-warp/src/map_seams", "warp: map seam table"),
    ("er-invasion-warp-core/src/warp", "warp: core warp"),
    ("er-invasion-warp/src/local_invasion_filter", "warp: local invasion filter"),
    ("er-invasion-warp", "invasion warp"),
    ("er-loading-portrait-core", "loading portrait"),
    ("er-reload-trace", "reload trace"),
    ("er-better-refills", "better refills"),
    ("er-armament-icons", "armament icons HUD/GFx"),
    ("er-build-import-runtime", "build import/export"),
    ("er-title-flow/src/title_tick_cover", "title tick cover"),
    ("er-title-flow/src/profile_select_flow", "profile-select flow"),
    ("er-title-flow", "title flow"),
    ("er-save-suppress", "save suppress"),
    ("er-save-loader", "save loader"),
    ("er-quit-menu-core", "quit menu (standalone)"),
    ("er-player-name-filter", "player name filter"),
    ("er-input-harness", "input harness"),
    ("er-seamless-bugfixes", "seamless bugfixes"),
    ("er-diag-harness", "diag harness"),
    ("er-charm-enemies", "charm enemies"),
]


def feature_of(path):
    for key, label in FEATURE_OF_PATH:
        if key in path:
            return label
    return os.path.dirname(path).replace("crates/", "")


def per_address(inv):
    """Collapse the per-cdylib view into one row per address."""
    out = {}
    for dll, addrs in inv["crates"].items():
        for rva, v in addrs.items():
            e = out.setdefault(rva, {"dlls": set(), "names": set(), "detour": False, "kinds": set(),
                                     "crates": set(), "gated": 0, "ungated": 0, "sites": None})
            e["dlls"].add(dll)
            e["names"] |= set(v["names"])
            e["detour"] |= v["detour"]
            e["kinds"] |= set(v["kinds"])
            e["crates"] |= set(v["crates"])
            if e["sites"] is None or len(v["sites"]) > len(e["sites"]):
                e["sites"], e["gated"], e["ungated"] = v["sites"], v["gated"], v["ungated"]
    return out


def rank(inv, maps):
    """The UNMAPPED addresses, worst blast radius first."""
    everything, rows = per_address(inv), []
    for rva, v in everything.items():
        if inv["classes"][rva]["call_ok"]:
            continue
        row = {"rva": rva, "names": sorted(v["names"]), "dlls": sorted(v["dlls"]),
               "crates": sorted(v["crates"]), "detour": v["detour"], "kinds": sorted(v["kinds"]),
               "gated": v["gated"], "ungated": v["ungated"], "sites": v["sites"][:4],
               "ndlls": len(v["dlls"]), "observed": int(rva, 16) in maps["observed"]}
        score = 8 * row["ndlls"]
        if row["observed"]:
            score += 100          # its path demonstrably RUNS; everything else is a guess
        if "exec" in row["kinds"]:
            score += 80           # a stale transmute target is a dead process, not a dead feature
        if "write" in row["kinds"]:
            score += 60           # corrupts the image for every later reader
        if row["detour"]:
            score += 40           # needs the stricter licence, so mapping it is not enough
        if "er-quickload" in row["dlls"]:
            score += 25           # the product DLL, in 36 of the profiles
        if row["ungated"] and not row["gated"]:
            score += 30           # nothing will refuse it; it just goes silently wrong
        row["score"] = score
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["rva"]))
    return rows, everything


def markdown(inv, maps, path):
    import datetime
    cls, rows, _every = inv["classes"], *rank(inv, maps)
    out = []
    w = out.append
    w("# 1.17 exposure: complete STATIC inventory across all 27 cdylibs")
    w("")
    w(f"Generated `{datetime.datetime.now().isoformat(timespec='seconds')}` by "
      "`scripts/audit-1170-coverage-inventory.py --markdown`.")
    w("")
    w("## Why the source and not the logs")
    w("")
    w("A refusal is only logged when the path RUNS, so the log is a FLOOR. Every feature nobody "
      "triggered is silent. Addresses here are collected in four declaration forms; a literal-hex "
      "scan sees only the first:")
    w("")
    w("1. `const FOO_RVA: usize = 0x749b20;`")
    w("2. `const FOO_RVA: usize = BAR_RVA;`")
    w("3. `const FOO_RVA: usize = SomeEnum::Variant as usize;` -- the form that black-screened the "
      "game on 2026-08-29")
    w("4. a bare literal in a hook/seam table with no `_RVA` name at all: "
      "`HookSpec { rva: 0x836f30, detour: ... }`")
    w("")
    w("Classification reproduces the two tables `er-game-base/build.rs` generates and asserts "
      "equality against the built `address_map_1170.rs` (`--selftest`).")
    w("")
    w("## Per-cdylib coverage")
    w("")
    w("| crate | profiles | total addresses | verified | data-mapped | function-mapped | "
      "held-back | unmapped | unmapped-detour-targets |")
    w("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for dll in inv["cdylibs"]:
        a = inv["crates"][dll]
        c = collections.Counter(cls[r]["prov"] for r in a)
        un = [r for r in a if not cls[r]["call_ok"]]
        w(f"| `{dll}` | {PROFILE_LOADS.get(dll, 0)} | {len(a)} | {c['VERIFIED']} | {c['DATA']} | "
          f"{c['FUNCTION']} | {c['HELD-BACK']} | **{len(un)}** | "
          f"**{sum(1 for r in un if a[r]['detour'])}** |")
    every = {r for a in inv["crates"].values() for r in a}
    cc = collections.Counter(cls[r]["prov"] for r in every)
    w(f"| **DISTINCT** | | {len(every)} | {cc['VERIFIED']} | {cc['DATA']} | {cc['FUNCTION']} | "
      f"{cc['HELD-BACK']} | **{len(rows)}** | **{sum(1 for r in rows if r['detour'])}** |")
    w("")
    w("## Ranked UNMAPPED list")
    w("")
    w("Score = observed at runtime (100) + exec use (80) + raw write (60) + detour target (40) + "
      "ungated-only (30) + in `er-quickload` (25) + 8 per affected DLL. `g/u` = gated / ungated "
      "reference sites, by a window heuristic -- a hint about what to read, never a safety claim.")
    w("")
    w("| # | RVA (1.16.2) | score | seen at runtime | detour | use | DLLs | g/u | constant | "
      "feature | first site |")
    w("|--:|---|--:|:-:|:-:|---|--:|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        site = r["sites"][0]
        name = r["names"][0] + (f" (+{len(r['names']) - 1})" if len(r["names"]) > 1 else "")
        w(f"| {i} | `{r['rva']}` | {r['score']} | {'YES' if r['observed'] else '-'} | "
          f"{'YES' if r['detour'] else '-'} | {','.join(r['kinds'])} | {r['ndlls']} | "
          f"{r['gated']}/{r['ungated']} | `{name}` | {feature_of(site[0])} | "
          f"`{site[0]}:{site[1]}` |")
    w("")
    w("## Unmapped clustered by owning crate")
    w("")
    by_crate, detours = collections.Counter(), collections.Counter()
    for r in rows:
        crate = r["sites"][0][0].split("/")[1]
        by_crate[crate] += 1
        if r["detour"]:
            detours[crate] += 1
    w("| owning crate | unmapped | of which detour targets |")
    w("|---|--:|--:|")
    for crate, n in by_crate.most_common():
        w(f"| `{crate}` | {n} | {detours[crate]} |")
    w("")
    w("Densest files -- a whole feature's address set going dark together:")
    w("")
    for f, n in collections.Counter(r["sites"][0][0] for r in rows).most_common(14):
        w(f"* **{n}** -- `{f}` ({feature_of(f)})")
    w("")
    w("## What this inventory cannot see")
    w("")
    w("* **Runtime-resolved addresses.** `er-armament-icons` reads `FileOpener::OpenFile` out of a "
      "vtable at runtime and hooks whatever comes back; the refusals `0x1411d0fa0` and "
      "`0x141158940` have no constant anywhere in the tree. Same shape for `0x1426634a0` in "
      "`er-input-harness`. Only the logs find those.")
    w("* **Gated/ungated and detour attribution are window heuristics**, not dataflow.")
    w("* **Addresses named without an `_RVA` suffix and not in an `rva:` field.** "
      "`record-1170-refusals.py` found 42 such addresses from a single boot.")
    open(path, "w").write("\n".join(out) + "\n")
    print(f"wrote {path} ({len(rows)} unmapped)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="write the full inventory to this path")
    ap.add_argument("--report", action="store_true", help="print the per-cdylib table")
    ap.add_argument("--markdown", help="write the full inventory + ranked unmapped list as markdown")
    ap.add_argument("--observed-extra", help="file of extra refused addresses (hex per line, VA or RVA)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    maps = load_maps(args.observed_extra)
    if args.selftest:
        return selftest(maps)
    inv = collect(maps)
    if args.markdown:
        markdown(inv, maps, args.markdown)
    if args.json:
        json.dump(inv, open(args.json, "w"), indent=0)
        print(f"wrote {args.json}")
    if args.report or not (args.json or args.markdown):
        cls = inv["classes"]
        print(f"{'cdylib':26s} {'addrs':>5} {'VER':>4} {'DATA':>4} {'FUNC':>4} {'HELD':>4} "
              f"{'UNMAP':>5} {'u-det':>5}")
        for dll in inv["cdylibs"]:
            a = inv["crates"][dll]
            c = collections.Counter(cls[r]["prov"] for r in a)
            unmapped = [r for r in a if not cls[r]["call_ok"]]
            print(f"{dll:26s} {len(a):5d} {c['VERIFIED']:4d} {c['DATA']:4d} {c['FUNCTION']:4d} "
                  f"{c['HELD-BACK']:4d} {len(unmapped):5d} "
                  f"{sum(1 for r in unmapped if a[r]['detour']):5d}")
        every = {r for a in inv["crates"].values() for r in a}
        print(f"\n{len(every)} unique addresses; "
              f"{sum(1 for r in every if cls[r]['call_ok'])} resolvable, "
              f"{sum(1 for r in every if not cls[r]['call_ok'])} UNMAPPED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
