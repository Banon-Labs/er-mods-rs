"""Structural hash of the expression that computes `SV_Position` in a DXIL vertex shader.

Research prototype for the shader-editor vertex question. Reads `dxc -dumpbin` text and builds, for
each of the four position components, the expression DAG behind the last `dx.op.storeOutput` to
the `SV_Position` element. Leaves are normalised so that two shaders compiled separately compare
equal when they compute the same arithmetic on the same data:

- a vertex input is its semantic name, index and column;
- a resource is its class, space and register, whatever range id the compiler chose;
- fadd, fmul and the two factors of `FMad` are sorted, because IEEE addition and multiplication
  are commutative bit for bit.

Two hashes are printed per component: with the fast-math flags and `dx.precise` marks, and
without them. Equal flagless hashes with different flagged ones mean the same operations where one
side lets the driver reassociate and the other does not.

Usage: pos_slice.py <shader.ll> [--tree]
"""
from __future__ import annotations

import hashlib
import re
import sys

ASSIGN = re.compile(r"^\s*(%[\w.]+) = (.*)$")
STORE = re.compile(
    r"^\s*call void @dx\.op\.storeOutput\.f32\(i32 5, i32 (\d+), i32 [^,]+, i8 (\d), "
    r"float ([^)]+)\)")
COMMUTATIVE = {"fadd", "fmul", "add", "mul", "and", "or", "xor"}
CASTS = ("sitofp", "uitofp", "fptoui", "fptosi", "bitcast", "zext", "sext", "trunc", "fpext",
         "fptrunc")
TYPE_PREFIX = re.compile(r"^(?:float|half|double|i1|i8|i16|i32|i64|%dx\.types\.\w+(?:\.\w+)?)\s+")
CLASS_NAMES = {0: "srv", 1: "uav", 2: "cb", 3: "smp"}


def metadata(text: str) -> dict[int, str]:
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^!(\d+) = (.*)$", text, re.M)}


def split_top(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "({[<":
            depth += 1
        elif ch in ")}]>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def tuple_items(node: str) -> list[str]:
    return split_top(node.strip()[2:-1])


def ref(item: str) -> int:
    return int(item.lstrip("!"))


def signatures(text: str, nodes: dict[int, str]):
    entry = re.search(r"^!dx\.entryPoints = !\{!(\d+)\}", text, re.M)
    ep = tuple_items(nodes[int(entry.group(1))])
    sig = tuple_items(nodes[ref(ep[2])])
    out = []
    for which in (0, 1):
        elements = {}
        if sig[which] != "null":
            for item in tuple_items(nodes[ref(sig[which])]):
                e = tuple_items(nodes[ref(item)])
                eid = int(e[0].split()[1])
                name = e[1][2:-1]
                kind = int(e[3].split()[1])
                index = tuple_items(nodes[ref(e[4])])[0].split()[1]
                elements[eid] = (name, index, kind)
        out.append(elements)
    return out


def resources(text: str, nodes: dict[int, str]) -> dict[tuple[int, int], tuple[int, int]]:
    """(class, range id) -> (space, lower bound)."""
    res = re.search(r"^!dx\.resources = !\{!(\d+)\}", text, re.M)
    out = {}
    if not res:
        return out
    for cls, lst in enumerate(tuple_items(nodes[int(res.group(1))])):
        if lst == "null":
            continue
        for item in tuple_items(nodes[ref(lst)]):
            e = tuple_items(nodes[ref(item)])
            out[(cls, int(e[0].split()[1]))] = (int(e[3].split()[1]), int(e[4].split()[1]))
    return out


def strip_type(arg: str) -> str:
    return TYPE_PREFIX.sub("", arg.strip())


def build(text: str):
    nodes = metadata(text)
    inputs, outputs = signatures(text, nodes)
    res = resources(text, nodes)
    body = text[text.index("\ndefine "):]
    body = body[:body.index("\n}\n")]
    defs: dict[str, tuple[str, bool]] = {}
    pos_ids = {eid for eid, (_, _, kind) in outputs.items() if kind == 3}
    last_store: dict[int, str] = {}
    for line in body.split("\n"):
        code = line.split(";", 1)[0].rstrip()
        m = ASSIGN.match(code)
        if m:
            rhs = m.group(2)
            precise = "!dx.precise" in rhs
            rhs = re.sub(r",\s*!dx\.\w+ !\d+", "", rhs).strip()
            defs[m.group(1)] = (rhs, precise)
        s = STORE.match(code)
        if s and int(s.group(1)) in pos_ids:
            last_store[int(s.group(2))] = s.group(3).strip()
    # Values are hashed in program order, each from its operands' digests, so a deep chain costs
    # no recursion and a shared subexpression is hashed once. SSA defines an operand before its
    # use except a loop phi's back edge, which is hashed as `?back`.
    memo: dict[str, tuple[str, str]] = {}

    def digest(text: str) -> str:
        return hashlib.sha1(text.encode()).hexdigest()[:16]

    def node(value: str) -> tuple[str, str]:
        """(with flags, without flags) digests of a value's canonical expression."""
        value = strip_type(value)
        if not value.startswith("%"):
            return value, value
        if value in memo:
            return memo[value]
        return ("?back", "?back") if value in defs else (f"?{value}", f"?{value}")

    def join(op: str, parts: list[tuple[str, str]], flags: str = "") -> tuple[str, str]:
        f = f"[{flags}]" if flags else ""
        return (f"{op}{f}({','.join(p[0] for p in parts)})",
                f"{op}({','.join(p[1] for p in parts)})")

    def expression(rhs: str) -> tuple[str, str]:
        m = re.match(r"call [^@]*@dx\.op\.(\w+)(?:\.\w+)?\((.*)\)$", rhs)
        if m:
            args = split_top(m.group(2))
            opcode = int(strip_type(args[0]))
            rest = args[1:]
            if opcode == 4:
                name, index, _ = inputs[int(strip_type(rest[0]))]
                s = f"in:{name}{index}.{strip_type(rest[2])}"
                return s, s
            if opcode == 57:
                cls, rid = int(strip_type(rest[0])), int(strip_type(rest[1]))
                space, reg = res.get((cls, rid), (-1, -1))
                idx = node(rest[2])
                s0 = f"{CLASS_NAMES.get(cls, cls)}:{space}:{reg}"
                return f"{s0}[{idx[0]}]", f"{s0}[{idx[1]}]"
            parts = [node(a) for a in rest]
            if opcode == 46:
                parts = sorted(parts[:2]) + parts[2:]
            return join(f"dx{opcode}", parts)
        m = re.match(r"extractvalue \S+ (%[\w.]+), (\d+)$", rhs)
        if m:
            inner = node(m.group(1))
            return f"{inner[0]}.{m.group(2)}", f"{inner[1]}.{m.group(2)}"
        m = re.match(rf"({'|'.join(CASTS)}) \S+ (\S+) to \S+$", rhs)
        if m:
            if m.group(1) == "bitcast":
                # Same bits: a float load reinterpreted as an int is the int load.
                return node(m.group(2))
            return join(m.group(1), [node(m.group(2))])
        m = re.match(r"(\w+)((?: (?:fast|nnan|ninf|nsz|arcp|nuw|nsw|exact))*) \S+ (.*)$", rhs)
        if m and m.group(1) in {"fadd", "fsub", "fmul", "fdiv", "frem", "add", "sub", "mul",
                                "udiv", "sdiv", "urem", "srem", "shl", "lshr", "ashr", "and",
                                "or", "xor"}:
            op = m.group(1)
            parts = [node(a) for a in split_top(m.group(3))]
            if op in COMMUTATIVE:
                parts = sorted(parts)
            return join(op, parts, m.group(2).strip())
        m = re.match(r"(icmp|fcmp)((?: fast)*) (\w+) \S+ (.*)$", rhs)
        if m:
            return join(f"{m.group(1)}.{m.group(3)}", [node(a) for a in split_top(m.group(4))])
        m = re.match(r"select (.*)$", rhs)
        if m:
            return join("select", [node(a) for a in split_top(m.group(1))])
        m = re.match(r"phi \S+ (.*)$", rhs)
        if m:
            # Incoming values only, sorted: block labels are numbering and their order is block
            # layout, which a structurizer reorders without changing the arithmetic. Which
            # condition selects which value is not compared.
            incoming = [split_top(a.strip()[1:-1])[0] for a in split_top(m.group(1))]
            return join("phi", sorted(node(a) for a in incoming))
        return f"raw<{rhs}>", f"raw<{rhs}>"

    for value, (rhs, precise) in defs.items():
        same_bits = re.match(r"bitcast \S+ (\S+) to \S+$", rhs)
        if same_bits and not precise:
            memo[value] = node(same_bits.group(1))
            continue
        flagged, flagless = expression(rhs)
        flagged = ("precise:" if precise else "") + flagged
        memo[value] = (digest(flagged), digest(flagless))
        EXPRESSIONS[memo[value][0]] = flagged
    return {col: node(val) for col, val in sorted(last_store.items())}


# Flagged digest -> the expression it was taken over, for `--tree`.
EXPRESSIONS: dict[str, str] = {}


def print_tree(digest: str, depth: int, seen: set[str]) -> None:
    """The expression behind a digest, with every operand digest expanded once."""
    text = EXPRESSIONS.get(digest)
    if text is None:
        return
    mark = " (above)" if digest in seen else ""
    print(f"{'  ' * depth}{digest} = {text}{mark}")
    if mark:
        return
    seen.add(digest)
    for child in re.findall(r"\b[0-9a-f]{16}\b", text):
        print_tree(child, depth + 1, seen)


def main() -> int:
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(files) > 1:
        # Batch form: one line per file, "<path>\t<flagged hash>\t<flagless hash>", each over
        # all four components.
        for path in files:
            try:
                trees = build(open(path, encoding="utf-8").read())
                digests = []
                for which in (0, 1):
                    joined = "|".join(f"{c}:{t[which]}" for c, t in trees.items())
                    digests.append(hashlib.sha1(joined.encode()).hexdigest()[:16]
                                   if trees else "none")
            except (OSError, ValueError, KeyError, IndexError, AttributeError,
                    RecursionError) as error:
                digests = [f"error:{type(error).__name__}"] * 2
            print(f"{path}\t{digests[0]}\t{digests[1]}")
        return 0
    text = open(files[0], encoding="utf-8").read()
    trees = build(text)
    seen: set[str] = set()
    for col, (flagged, flagless) in trees.items():
        print(f"{col} flags={flagged} noflags={flagless}")
        if "--tree" in sys.argv[2:]:
            print_tree(flagged, 1, seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
