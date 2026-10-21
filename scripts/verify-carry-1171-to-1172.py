#!/usr/bin/env python3
"""Byte-verify addresses carried from 1.17.1 (2.7.1.0) to 2.7.2.0 before an agent calls or hooks them.

`scripts/map-rvas-1171-to-1172.py` carries an address through a segment table. This reads the
code or data at both ends to show the carry landed on the same thing:

* code (`--code OLD:NEW`): both images are decoded from the containing `.pdata` function start, so
  a mid-function address is checked to be an instruction boundary at the same offset, and every
  instruction of the function is compared. Bytes must match except rel32 branch targets and
  rip-relative displacements, and those pass only when the 1.17.1 target, carried through the
  table, is the 2.7.2.0 target.
* data (`--data OLD:NEW`): every rip-relative reference to the 1.17.1 global in `.text` is found,
  its instruction carried, and checked to reference the 2.7.2.0 global.
* `--disp HEX`: every instruction in a carried text segment whose memory displacement is HEX is
  carried and checked to keep that displacement, which is how a struct offset is confirmed.
* `--text-diff`: every byte difference inside the carried text segments is explained as a relocated
  rel32 or rip-relative disp32, or counted as unexplained. Expect noise: functions whose Arxan
  stubs de-Arxan'd differently still pair by length. A struct field that moved shows up in
  `displacement_shifts` as one from -> to step repeated across many functions (2.7.2.0:
  WorldChrMan +0x1e5f0 and up, +0x10).

Reads `map-rvas-1171-to-1172.py`, its tsv and both de-Arxan'd images from this checkout, or from
the main checkout when this is a linked worktree (they are gitignored inputs there).

    uv run --with capstone --with numpy python3 scripts/verify-carry-1171-to-1172.py \\
        --code 0x1404baea0:0x1404baf80 --data 0x143d69ff8:0x143d6d018 --disp 0x1e508
"""
import argparse
import bisect
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from capstone import CS_ARCH_X86, CS_GRP_CALL, CS_GRP_JUMP, CS_MODE_64, Cs
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP

BASE = 0x140000000


def _carry_script() -> Path:
    here = Path(__file__).resolve().parent.parent
    cand = [here]
    try:
        common = subprocess.run(["git", "-C", str(here), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        cand.append((here / common).resolve().parent)
    except (OSError, subprocess.CalledProcessError):
        pass
    for root in cand:
        p = root / "scripts" / "map-rvas-1171-to-1172.py"
        if p.is_file() and (root / "docs" / "recon" / "carry-1171-to-1172.tsv").is_file():
            return p
    raise SystemExit("map-rvas-1171-to-1172.py and its tsv not found in this checkout or the main one")


_spec = importlib.util.spec_from_file_location("carry1172", _carry_script())
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)
ROWS = M.read_tsv()
STARTS = [r[1] for r in ROWS]
OLD = M.Image(M.OLD_IMAGE)
NEW = M.Image(M.NEW_IMAGE)
PO, PN = OLD.pdata(), NEW.pdata()
POB, PNB = [b for b, _ in PO], [b for b, _ in PN]
CS = Cs(CS_ARCH_X86, CS_MODE_64)
CS.detail = True


def carry(rva):
    at = bisect.bisect_right(STARTS, rva) - 1
    if at >= 0 and ROWS[at][1] <= rva < ROWS[at][2]:
        return rva + ROWS[at][3]
    return None


def text_carry(rva):
    at = bisect.bisect_right(STARTS, rva) - 1
    if at >= 0 and ROWS[at][0] == "text" and ROWS[at][1] <= rva < ROWS[at][2]:
        return rva + ROWS[at][3]
    return None


def func_of(pd, pdb, rva):
    i = bisect.bisect_right(pdb, rva) - 1
    if i >= 0 and pd[i][0] <= rva < pd[i][1]:
        return pd[i]
    return None


def decode(img, lo, hi):
    return list(CS.disasm(img.data[lo:hi], BASE + lo))


def rip_target(insn):
    for op in insn.operands:
        if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
            return insn.address + insn.size + op.mem.disp
    return None


def branch_target(insn):
    if insn.group(CS_GRP_JUMP) or insn.group(CS_GRP_CALL):
        for op in insn.operands:
            if op.type == X86_OP_IMM:
                return op.imm
    return None


def _target_ok(t_old, t_new):
    if t_old is None:
        return None
    c = carry(t_old - BASE)
    if c is None:
        return f"target {t_old:#x} uncarried"
    if c + BASE != t_new:
        return f"target {t_old:#x} carries to {c + BASE:#x}, 2.7.2.0 has {t_new:#x}"
    return None


def compare_insns(a, b):
    """None when the two instructions are the same instruction after relocation.

    Otherwise a reason. A reason starting with "uncarried" means every other byte matches and only
    the target of a call, jump or rip-relative operand could not be checked, because the table
    refuses that target (a function or global that itself changed)."""
    if a.size != b.size or a.mnemonic != b.mnemonic:
        return f"{a.mnemonic} {a.op_str} | {b.mnemonic} {b.op_str}"
    rt, bt = rip_target(a), branch_target(a)
    mask = bytearray(a.size)
    if rt is not None:
        mask[a.disp_offset:a.disp_offset + a.disp_size] = b"\1" * a.disp_size
    if bt is not None:
        mask[a.imm_offset:a.imm_offset + a.imm_size] = b"\1" * a.imm_size
    for i in range(a.size):
        if not mask[i] and a.bytes[i] != b.bytes[i]:
            return f"{a.mnemonic} {a.op_str} | {b.mnemonic} {b.op_str}"
    for r in (_target_ok(rt, rip_target(b)), _target_ok(bt, branch_target(b))):
        if r:
            return ("uncarried " + r) if "uncarried" in r else r
    return None


def verify_code(old_va, new_va, window=32):
    ro, rn = old_va - BASE, new_va - BASE
    fo, fn = func_of(PO, POB, ro), func_of(PN, PNB, rn)
    out = {"old": hex(old_va), "new": hex(new_va)}
    if fo is None or fn is None:
        out["pdata"] = "none (leaf): compared 0x80 bytes from the address"
        lo_o, hi_o, lo_n, hi_n = ro, ro + 0x80, rn, rn + 0x80
    else:
        out["func"] = [hex(fo[0] + BASE), hex(fn[0] + BASE)]
        out["len"] = [hex(fo[1] - fo[0]), hex(fn[1] - fn[0])]
        if ro - fo[0] != rn - fn[0]:
            out["verdict"] = "offset in function differs"
            return out
        lo_o, hi_o, lo_n, hi_n = fo[0], fo[1], fn[0], fn[1]
    a, b = decode(OLD, lo_o, hi_o), decode(NEW, lo_n, hi_n)
    if fo is None or fn is None:
        # A leaf has no .pdata extent: stop at its first ret or tail jmp.
        end = next((i for i, x in enumerate(a) if x.mnemonic in ("ret", "jmp", "int3")), len(a) - 1)
        a, b = a[:end + 1], b[:end + 1]
        hi_o, hi_n = a[-1].address + a[-1].size - BASE, b[-1].address + b[-1].size - BASE
    boundary = [any(i.address == old_va for i in a), any(i.address == new_va for i in b)]
    if not all(boundary):
        out["verdict"] = f"not an instruction boundary {boundary}"
        return out
    diffs = []
    for x, y in zip(a, b):
        off = x.address - BASE - lo_o
        if off != y.address - BASE - lo_n:
            diffs.append((off, "desync"))
            break
        r = compare_insns(x, y)
        if r:
            diffs.append((off, r))
    if len(a) != len(b):
        diffs.append((hi_o - lo_o, f"instruction count {len(a)} vs {len(b)}"))
    at = ro - lo_o
    win = [x for x in a if old_va <= x.address < old_va + window]
    wdiff = [d for d in diffs if at <= d[0] < at + window]
    out["insns"] = len(a)
    out["window"] = " ; ".join(f"{x.mnemonic} {x.op_str}" for x in win[:6])
    out["diffs"] = [f"+{o:#x}: {r}" for o, r in diffs[:6]]
    real = [d for d in diffs if not d[1].startswith("uncarried")]
    wreal = [d for d in wdiff if not d[1].startswith("uncarried")]
    if not diffs:
        out["verdict"] = "bytes match"
    elif not real:
        out["verdict"] = f"bytes match; {len(diffs)} target(s) the table refuses, unchecked"
    elif not wreal:
        out["verdict"] = "window matches, rest of function differs"
    else:
        out["verdict"] = "window differs"
    return out


def _disp32_scan(img):
    lo, hi = img.text_lo, img.text_hi
    buf = np.frombuffer(img.data, dtype=np.uint8, count=hi + 8).astype(np.int64)
    d = buf[lo:hi] | (buf[lo + 1:hi + 1] << 8) | (buf[lo + 2:hi + 2] << 16) | (buf[lo + 3:hi + 3] << 24)
    return np.where(d >= 0x80000000, d - 0x100000000, d), np.arange(lo, hi, dtype=np.int64)


_SCAN = {}


def readers(img, target_rva):
    """Instruction VAs in .text with a rip-relative operand naming target_rva."""
    if id(img) not in _SCAN:
        _SCAN[id(img)] = _disp32_scan(img)
    d, pos = _SCAN[id(img)]
    found = set()
    for ia in (0, 1, 2, 4):
        for h in np.nonzero(pos + 4 + ia + d == target_rva)[0]:
            p = int(pos[h])
            for back in range(2, 8):
                st = p - back
                for insn in CS.disasm(img.data[st:st + 16], BASE + st, count=1):
                    if insn.disp_offset == back and rip_target(insn) == BASE + target_rva:
                        found.add(BASE + st)
    return sorted(found)


def verify_data(old_va, new_va):
    r_old = readers(OLD, old_va - BASE)
    r_new = set(readers(NEW, new_va - BASE))
    ok, bad, unc = [], [], 0
    for r in r_old:
        c = carry(r - BASE)
        if c is None:
            unc += 1
        elif c + BASE in r_new:
            ok.append(r)
        else:
            bad.append(hex(r))
    return {"old": hex(old_va), "new": hex(new_va), "refs_1171": len(r_old), "refs_2720": len(r_new),
            "carried_to_a_ref": len(ok), "carried_elsewhere": bad[:5], "ref_site_uncarried": unc,
            "first_refs": [f"{r:#x}->{carry(r - BASE) + BASE:#x}" for r in ok[:3]],
            "verdict": "references agree" if ok and not bad else "check"}


def verify_disp(disp):
    """Instructions in carried text with memory displacement `disp`: does the carried copy keep it."""
    le = disp.to_bytes(4, "little", signed=True) if not -128 <= disp < 128 else None
    if le is None:
        return {"disp": hex(disp), "verdict": "disp8 offsets are not scanned; see --text-diff"}
    data = OLD.data
    keep = bad = unc = 0
    shifted = {}
    at = data.find(le, OLD.text_lo, OLD.text_hi)
    while at != -1:
        for back in range(2, 8):
            st = at - back
            ok_insn = None
            for insn in CS.disasm(data[st:st + 16], BASE + st, count=1):
                if insn.disp_offset == back and insn.disp_size == 4:
                    if any(op.type == X86_OP_MEM and op.mem.disp == disp and op.mem.base != X86_REG_RIP
                           for op in insn.operands):
                        ok_insn = insn
            if ok_insn is None:
                continue
            c = text_carry(st)
            if c is None:
                unc += 1
                break
            got = None
            for y in CS.disasm(NEW.data[c:c + 16], BASE + c, count=1):
                got = y
            if got is not None and got.bytes == ok_insn.bytes:
                keep += 1
            elif got is not None and got.size == ok_insn.size and got.mnemonic == ok_insn.mnemonic:
                # The same instruction with another displacement: the field moved.
                nd = [op.mem.disp for op in got.operands if op.type == X86_OP_MEM and op.mem.base != X86_REG_RIP]
                shifted.setdefault(nd[0] if nd else None, []).append(f"{BASE + st:#x}")
            else:
                # Anything else is a byte pattern that was not this instruction in a function that
                # carried: a mid-instruction match, or a function that changed.
                bad += 1
            break
        at = data.find(le, at + 1, OLD.text_hi)
    out = {"disp": hex(disp), "kept": keep, "shifted": {hex(k) if k is not None else "?": (len(v), v[:3])
                                                         for k, v in shifted.items()},
           "other": bad, "uncarried": unc}
    out["verdict"] = ("unchanged" if keep and not shifted else "moved" if shifted and not keep else
                      "kept at some sites, moved at others" if shifted else "no sites")
    return out


def text_diff():
    """Every differing byte in the carried text segments, explained as a relocated rel32/disp32."""
    o = np.frombuffer(OLD.data, dtype=np.uint8)
    n = np.frombuffer(NEW.data, dtype=np.uint8)
    starts = np.array(STARTS, dtype=np.int64)
    ends = np.array([r[2] for r in ROWS], dtype=np.int64)
    deltas = np.array([r[3] for r in ROWS], dtype=np.int64)

    def vcarry(rva):
        i = np.searchsorted(starts, rva, side="right") - 1
        i = np.clip(i, 0, len(starts) - 1)
        inside = (starts[i] <= rva) & (rva < ends[i])
        return np.where(inside, rva + deltas[i], -1)

    def i32(arr, p):
        v = (arr[p].astype(np.int64) | (arr[p + 1].astype(np.int64) << 8)
             | (arr[p + 2].astype(np.int64) << 16) | (arr[p + 3].astype(np.int64) << 24))
        return np.where(v >= 0x80000000, v - 0x100000000, v)

    text_lo, text_hi = OLD.text_lo, OLD.text_hi
    n_text_lo, n_text_hi = NEW.text_lo, NEW.text_hi
    total = segs = bytes_cmp = 0
    unexplained = []
    loose = 0
    for kind, lo, hi, d in ROWS:
        if kind != "text":
            continue
        segs += 1
        bytes_cmp += hi - lo
        a = o[lo:hi]
        b = n[lo + d:hi + d]
        diff = a != b
        if not diff.any():
            continue
        idx = np.nonzero(diff)[0]
        prev = np.concatenate(([False], diff[:-1]))
        run_start = np.nonzero(diff & ~prev)[0]
        nxt = np.concatenate((diff[1:], [False]))
        run_end = np.nonzero(diff & ~nxt)[0] + 1
        total += len(idx)
        explained = np.zeros(len(run_start), dtype=bool)
        weak = np.zeros(len(run_start), dtype=bool)
        for k in range(4):
            p = run_start - k
            ok_span = (p >= 0) & (run_end <= p + 4)
            pp = np.clip(p, 0, len(a) - 4) + lo
            od = i32(o, pp)
            nd = i32(n, pp + d)
            for ia in (0, 1, 2, 4):
                ot = pp + 4 + ia + od
                nt = pp + d + 4 + ia + nd
                c = vcarry(ot)
                explained |= ok_span & (c >= 0) & (c == nt)
                # A target the table does not carry (the Arxan section, the IAT, a changed
                # function): both ends outside .text, or both inside it with no carry. Counted
                # apart, since it is not checked against the table.
                outside = (ot >= text_hi) & (nt >= n_text_hi) & (ot < len(OLD.data)) & (nt < len(NEW.data))
                inside_unc = (ot >= text_lo) & (ot < text_hi) & (nt >= n_text_lo) & (nt < n_text_hi) & (c < 0)
                weak |= ok_span & (outside | inside_unc)
        weak &= ~explained
        loose += int(weak.sum())
        bad = ~(explained | weak)
        for s, e in zip(run_start[bad], run_end[bad]):
            unexplained.append((lo + int(s), lo + int(e)))
    out = {"text_segments": segs, "bytes_compared": bytes_cmp, "differing_bytes": total,
           "runs_with_uncarried_target": loose, "unexplained_runs": len(unexplained)}
    # Decode each unexplained run's instruction from its function start, and group the ones that
    # are the same instruction with another displacement: that is a struct offset that moved.
    # Only runs inside a .pdata function that carries onto a .pdata function of the same length:
    # the gaps between functions hold Arxan stubs and padding, which de-Arxan'ing leaves different.
    shifts = {}
    rows = []
    cache = {}
    pn = set(PN)
    outside_paired = 0
    for s, e in unexplained:
        f = func_of(PO, POB, s)
        cf = text_carry(f[0]) if f is not None else None
        if f is None or cf is None or (cf, cf + f[1] - f[0]) not in pn:
            outside_paired += 1
            continue
        if f not in cache:
            cache[f] = decode(OLD, f[0], f[1])
        x = next((i for i in cache[f] if i.address - BASE <= s < i.address - BASE + i.size), None)
        c = text_carry(x.address - BASE) if x is not None else None
        y = next(iter(CS.disasm(NEW.data[c:c + 16], BASE + c, count=1)), None) if c is not None else None
        if x is not None and y is not None and x.size == y.size and x.mnemonic == y.mnemonic:
            ox = [op.mem.disp for op in x.operands if op.type == X86_OP_MEM and op.mem.base != X86_REG_RIP]
            oy = [op.mem.disp for op in y.operands if op.type == X86_OP_MEM and op.mem.base != X86_REG_RIP]
            if ox and oy and ox != oy:
                key = (ox[0], oy[0])
                shifts.setdefault(key, []).append(hex(x.address))
                continue
        rows.append({"at": hex(BASE + s), "len": e - s,
                     "insn": (f"{x.address:#x} {x.mnemonic} {x.op_str} -> "
                              f"{y.mnemonic + ' ' + y.op_str if y else '?'}") if x is not None else None})
    out["displacement_shifts"] = [
        {"from": hex(k[0]), "to": hex(k[1]), "n": len(v), "sites": v[:4]}
        for k, v in sorted(shifts.items(), key=lambda kv: (kv[0][0], kv[0][1]))]
    out["unexplained_outside_paired_functions"] = outside_paired
    out["other_unexplained_in_paired_functions"] = len(rows)
    out["other_samples"] = rows[:60]
    return out


def pair(s):
    a, b = s.split(":")
    return int(a, 16), int(b, 16)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--code", action="extend", nargs="+", default=[], type=pair, metavar="OLD:NEW")
    ap.add_argument("--data", action="extend", nargs="+", default=[], type=pair, metavar="OLD:NEW")
    ap.add_argument("--disp", action="extend", nargs="+", default=[], type=lambda s: int(s, 16), metavar="HEX")
    ap.add_argument("--brief", action="store_true", help="one line per result")
    ap.add_argument("--text-diff", action="store_true")
    a = ap.parse_args()
    res = {}
    if a.code:
        res["code"] = [verify_code(o, n) for o, n in a.code]
    if a.data:
        res["data"] = [verify_data(o, n) for o, n in a.data]
    if a.disp:
        res["disp"] = [verify_disp(x) for x in a.disp]
    if a.text_diff:
        res["text_diff"] = text_diff()
    if not a.brief:
        print(json.dumps(res, indent=1))
        return 0
    for c in res.get("code", []):
        print(f"{c['old']} -> {c['new']}  {c['verdict']}  insns={c.get('insns')} len={c.get('len')} "
              f"{'leaf ' if 'pdata' in c else ''}{c.get('diffs', [])[:3]}")
    for d in res.get("data", []):
        print(f"{d['old']} -> {d['new']}  {d['verdict']}  refs {d['refs_1171']}/{d['refs_2720']} "
              f"agree={d['carried_to_a_ref']} elsewhere={d['carried_elsewhere']} "
              f"uncarried={d['ref_site_uncarried']} e.g. {d['first_refs'][:1]}")
    for d in res.get("disp", []):
        print(f"disp {d['disp']}  {d['verdict']}  kept={d.get('kept')} shifted={d.get('shifted')} "
              f"other={d.get('other')} uncarried={d.get('uncarried')}")
    if "text_diff" in res:
        print(json.dumps(res["text_diff"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
