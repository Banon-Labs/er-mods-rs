#!/usr/bin/env python3
"""SWEEP 2 detector: writes derived from a game-image RVA that no HOOK audit can see.

MinHook-shaped audits look at detour installs. These four classes never touch MinHook,
so nothing had ever checked them:

  SLOT     a store into a function-pointer table / vtable slot (`*(slot as *mut usize) = ...`)
  RVA_ARG  a byte-patch primitive that takes `(base, rva)` as SEPARATE ARGUMENTS -- invisible
           to any regex looking for the text `base + SOMETHING_RVA`
  SENTINEL `game_data_addr(..) + offset`, which destroys the `0`-means-refused sentinel:
           a refusal on row 3 yields the address 24, and `if addr != 0` waves it through
  RAW      a raw store whose address expression contains `base + *_rva` in any casing

Every class has a POSITIVE CONTROL in `--selftest`, because a detector that reports zero
is indistinguishable from a detector that matches nothing: `audit-1170-readiness.py`
reported ZERO ungated writes while six existed, and its `HAND_BUILT` pattern required an
uppercase `RVA` so `base + spec.rva` matched 0 of 40 real sites.

Reports; it does not gate. The gating ratchet is `scripts/audit-1170-readiness.py`.
"""
from __future__ import annotations

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rustfn import mask_source  # noqa: E402

GATED = re.compile(
    r"game_data_addr(?:_offset)?\s*\(|resolve_game_address(?:_fmt)?\s*\(|resolve_detour_address\s*\("
    r"|game_rva(?:_named)?\s*\(|write_global_u8\s*\("
)
# A store, in the spellings this tree actually uses.
STORE = re.compile(
    r"\*\s*\(?[^;=]{0,200}?as\s+\*mut\s+[A-Za-z0-9_:<> ]+\s*\)?\s*=(?!=)"
    r"|\)\s*\.\s*write(?:_volatile|_unaligned)?\s*\("
    r"|\bcopy_nonoverlapping\s*\("
)
# `base + FOO_RVA` / `base + spec.rva` / `module_base + rva` -- lowercase included ON PURPOSE.
IMAGE_ADDR = re.compile(
    r"\b(?:base|image_base|module_base|game_base|module)\s*\+\s*"
    r"((?:[A-Za-z_][A-Za-z0-9_]*(?:::|\.))*"
    r"(?:[A-Za-z0-9_]*RVA[A-Za-z0-9_]*|(?:[a-z0-9_]*_)?rva(?:_[a-z0-9_]+)?))"
)
# The byte-patch primitives, which take `(base, rva)` as arguments and so carry no `base + rva`
# text for IMAGE_ADDR to find.
RVA_ARG_CALL = re.compile(r"\b(patch_3byte_stub|apply_xor_ret_stub)\s*\(")
SENTINEL = re.compile(r"game_data_addr\s*\(")
SLOT_LHS = re.compile(
    r"\*\s*\(?\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*\+[^)]*)?\)?\s*as\s+\*mut\s+[A-Za-z0-9_:<> ]+\s*\)?\s*=(?!=)"
    r"|\(\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*\+[^)]*)?\)?\s*as\s+\*mut\s+[A-Za-z0-9_:<> ]+\s*\)\s*\.\s*write"
)
SLOT_NAME = re.compile(r"vtable|vtbl|vft|vfptr|slot|table|entry|thunk|dispatch", re.I)
SKIP_DIR = re.compile(r"/(tests|examples|benches)/|/tests\.rs$|^third_party/")

WINDOW = 4  # lines: a store's address can be bound a few lines above it


def close_paren(mask: str, open_index: int) -> int:
    depth = 1
    i = open_index + 1
    while i < len(mask) and depth:
        if mask[i] == "(":
            depth += 1
        elif mask[i] == ")":
            depth -= 1
        i += 1
    return i


def scan_text(path: str, src: str) -> list[tuple[str, int, str, str]]:
    """Return (class, line, symbol, source_line) findings for one file's text."""
    out = []
    mask = mask_source(src)
    lines = src.splitlines()
    mlines = mask.splitlines()

    # RVA_ARG: every call to a primitive that takes (base, rva) separately.
    for m in RVA_ARG_CALL.finditer(mask):
        end = close_paren(mask, mask.index("(", m.end() - 1))
        args = mask[m.end() - 1 : end]
        line = src[: m.start()].count("\n") + 1
        out.append(("RVA_ARG", line, m.group(1), " ".join(args.split())[:120]))

    # SENTINEL: `game_data_addr(...)` immediately followed by `+`/`-`.
    for m in SENTINEL.finditer(mask):
        end = close_paren(mask, m.end() - 1)
        tail = mask[end : end + 40]
        # `-` only when it is not the `->` of a return type: `game_data_addr(..) -> usize {` is the
        # DEFINITION, not a call, and it matched here until 2026-08-30.
        if re.match(r"\s*(?:\+|-(?!>))\s*[^=]", tail):
            line = src[: m.start()].count("\n") + 1
            out.append(("SENTINEL", line, "game_data_addr(..)+offset", lines[line - 1].strip()[:120]))

    # SLOT and RAW: per store, look back WINDOW lines for how the address was built.
    for i, mline in enumerate(mlines, 1):
        if not STORE.search(mline):
            continue
        lo = max(0, i - 1 - WINDOW)
        window_mask = "\n".join(mlines[lo:i])
        window_src = "\n".join(lines[lo:i])
        addr = IMAGE_ADDR.search(window_mask)
        gated = GATED.search(window_mask)
        sm = SLOT_LHS.search(mline)
        name = (sm.group(1) or sm.group(2)) if sm else None
        # SLOT is tested FIRST: it is the more specific class, and every SLOT store is also a
        # RAW store, so testing RAW first would swallow it and the SLOT control would never fire.
        if name and SLOT_NAME.search(name) and not gated:
            if IMAGE_ADDR.search(window_mask) or re.search(
                rf"\b{re.escape(name)}\s*=\s*[^;]*(?:RVA|rva)", window_src
            ):
                out.append(("SLOT", i, name, lines[i - 1].strip()[:120]))
                continue
        if addr and not gated:
            out.append(("RAW", i, addr.group(1), lines[i - 1].strip()[:120]))
    return out


def scan(paths) -> list:
    out = []
    for p in paths:
        if SKIP_DIR.search(p):
            continue
        src = open(p, encoding="utf-8", errors="replace").read()
        for cls, line, sym, text in scan_text(p, src):
            out.append((cls, p, line, sym, text))
    return out


CONTROLS = {
    "SLOT": '''
fn control_slot(module_base: usize) {
    let slot = module_base + TITLE_STEP_IDX10_SLOT_RVA;
    unsafe { *(slot as *mut usize) = handler as *const () as usize };
}
''',
    "RVA_ARG": '''
fn control_rva_arg(base: usize) {
    let _ = er_hook::patch_3byte_stub(base, SIGNIN_FORCE_RVA, 0x40, STUB, "signin-force");
    er_hook::apply_xor_ret_stub(base, ONLINE_DISABLE_RVA, 0x48, STUB, "IsOnlineMode getter");
}
''',
    "SENTINEL": '''
fn control_sentinel(base: usize, idx: usize) -> usize {
    er_game_base::mem::game_data_addr(base, TABLE_RVA, "TABLE_RVA") + idx * 8
}
''',
    "RAW": '''
fn control_raw(base: usize, spec: &Spec) {
    unsafe { *((base + spec.rva) as *mut u8) = 1 };
}
''',
}
NEGATIVE = '''
fn control_gated(base: usize, idx: usize) {
    let row = er_game_base::mem::game_data_addr_offset(base, TABLE_RVA, "TABLE_RVA", idx * 8);
    if row != 0 {
        unsafe { *(row as *mut usize) = 0 };
    }
}
'''


def selftest() -> int:
    failed = []
    for cls, text in CONTROLS.items():
        found = {c for c, _, _, _ in scan_text("control.rs", text)}
        status = "FIRES" if cls in found else "DID NOT FIRE"
        print(f"  positive control {cls:9} -> {status} (matched {sorted(found) or 'nothing'})")
        if cls not in found:
            failed.append(cls)
    neg = scan_text("control.rs", NEGATIVE)
    print(f"  negative control  gated    -> {'clean' if not neg else f'FALSE POSITIVE {neg}'}")
    if neg:
        failed.append("negative")
    if failed:
        print(f"SELFTEST FAILED: {failed}", file=sys.stderr)
        return 1
    print("audit-ungated-image-writes selftest OK: all four classes have a proven positive control")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    paths = [a for a in sys.argv[1:] if not a.startswith("-")] or sorted(
        glob.glob("crates/**/*.rs", recursive=True)
    )
    rows = scan(paths)
    for cls, p, line, sym, text in sorted(rows):
        print(f"{cls:9} {p}:{line}  [{sym}]\n          {text}")
    counts = {}
    for cls, *_ in rows:
        counts[cls] = counts.get(cls, 0) + 1
    print(f"\n{len(rows)} findings: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
