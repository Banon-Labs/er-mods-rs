#!/usr/bin/env python3
"""Hand-built game addresses that reach a call, a read or a write without the build check.

What this holds
---------------
Every game address in this workspace is a 2.7.2.0 rva, and `er_game_base::game_build` refuses
each one on any other build -- but only on the paths that ask it: `resolve_game_address`,
`resolve_detour_address`, `resolve_call_site_rva`, `mem::game_rva*`, `game_data_addr*`,
`read_global_*`, `write_global_u8`, and the er-hook entry points that resolve their own target
(`MhHook::new`, `register_union_hook*`, `register_shared_hook*`). An address added up by hand
(`base + FOO_RVA`, `base + spec.rva`, `module_base + rva`) asks nothing. On 2.7.2.0 it is the
number the gate would have handed back; on the next patch a call through it jumps into whatever
code moved there and a read returns whatever data moved there, with no refusal line to say so.

This is the successor of `audit-1170-gate-bypass.py` and `audit-1170-readiness.py`, deleted with
the cross-build address map on 2026-10-23 (their last committed copies are at `41d6a363`). Those
audits were about translation; what survives of them is this one invariant.

What counts as a site
---------------------
An rva-shaped operand added to something. A named rva -- an `*_RVA` constant or path, a
`FooRva::Bar as usize` variant, a `.rva` / `*_rva` field or binding -- is a site whatever it is
added to (`base`, `self.base`, `module_base`, `start`, ...), because a base bound under a new
name (`let Some((start, end)) = game_image_range()`) is still the game's. A hex literal of five
digits or more is a site when it is added to a module base (`base`, `module_base`, `game_base`,
`image_base`, `self.base`, `game_module_base()?`, ...). A path whose last segment is an offset,
size or length (`rva::CS_MENU_MAN_MENU_DATA_OFFSET`) is not an rva. A `game_rva_for_hook(..)`
call is a site too: it adds the base and checks nothing, on the promise that its result goes to a
hook API that does.

A named rva added to something the scan cannot read as a term -- a call's result, a parenthesised
sum, a `+=` -- is reported as `UNSEEN`, so a new spelling of a base cannot walk past the scan.

Not sites: another module's base (a function that takes its base from `ersc_module_base`), an
operand read out of the image's own PE or RTTI structures at runtime, and test code and build
scripts, which never run against a game. The PE and RTTI exemption is by name: the operand is bound
from one of the PE walkers (`pe_iat_directory`, `pe_import_descriptors`, `resource_directory`,
`version_resource_data`) or from a u32 read at one of the named RTTI and exception-info field
offsets (`COL_SELF_RVA_OFFSET`, `CATCHABLE_TYPE_DESCRIPTOR_OFFSET`, ...). An rva got any other way
(`u32::from_le_bytes`, a helper with `section` or `header` in its name) is classified like any
other operand.

How a site passes
-----------------
  inline    the expression is an argument of a gate or of a resolving hook API, or of a local
            helper that hands that parameter to one (followed to a fixpoint);
  flow      it is bound by `let` (or lands in a `for` pattern, for a tuple in an array literal)
            and every use of the binding is a gate, a resolving hook API, a log argument, a
            comparison or an error value;
  derived   the rva operand came out of a gate that refuses: `resolve_call_site_rva(..).map(|rva|
            base + rva)`, `let rva = resolve_call_site_rva(..)?`, `let rva =
            is_supported_build().then_some(FOO_RVA).ok_or(..)?`, `let Some(rva) =
            is_supported_build().then_some(FOO_RVA) else { .. }`. A gate's result unwrapped any
            other way (`.unwrap_or(FOO_RVA)`) refuses nothing;
  guarded   a refusal earlier in the function that leaves before the site can run: an
            `if !is_supported_build()` (or `if addr == 0` on a `game_data_addr` result) whose
            block ends in `return`, `continue`, `break`, `Err(..)?` or a panic; a `?` on
            `resolve_*(..)`, on `is_supported_build().then_some(..)`, or on a local function that
            asks the gate (`verify(..)?`), through `ok_or`/`map_err`-style adapters; a
            `let Some(..) = resolve_*(..) else { .. }`. A site inside an `if is_supported_build()`
            or `if let Some(..) = resolve_*(..)` block passes too. A `return` or `?` covers the
            rest of its function, or of the closure it is written in; a `continue` or `break`
            covers the rest of its loop body. A check whose value is kept in a local, discarded,
            or followed by a block that falls through guards nothing, and neither does a refusal
            whose own branch holds the site.

A local function is followed into: what its body does with the parameter a site lands in is
what the site does (`create_and_apply_single_hook` reaches `MhHook::new`; a helper that only
prints the address is a log).

Everything else is a finding, named by where the address goes:

  `CALL`    transmuted to a function pointer
  `READ`    read through: `read_bytes`, `safe_read_*`, `*(x as *const T)`, a prologue check
  `WRITE`   written through: `*(x as *mut T) =`, `write_code_byte`, `copy_nonoverlapping`
  `HOOK`    handed to a hook API that checks `.pdata` and not the build
  `STORE`   stored into a static, an atomic or a struct, where its later reader cannot see the
            missing check
  `RETURN`  returned to the caller
  `ARG`     passed to a function this audit cannot see into
  `OTHER`   any other use
  `UNSEEN`  a named rva added to something the scan cannot read as a base (see above)

Why the gate lists are re-proven
--------------------------------
A name on `GATE_FNS` or `GUARD_FNS` licenses every site handed to it, so each one is re-proven
from `crates/er-game-base` on every run (`gate_proof`): some definition of it must reach
`is_supported_build` through the functions its body calls, and `is_supported_build` must still
compare `game_file_version()` against `SUPPORTED_FILE_VERSION`. `detour_allowed` is not a root:
it looks the rva up in a list measured on 2.7.2.0 and says nothing about which build is running.
A definition of one of these names in another crate (er-save-loader's own `game_rva`) must reach a
proven name as well, because the scan matches calls by name. The er-hook entry points on
`HOOK_APIS` are re-proven the same way from er-hook (`api_gating`).

Why `*_runtime_derived` is not a gate
-------------------------------------
`register_union_hook_runtime_derived*`, `MhHook::new_runtime_derived`,
`MhHook::new_following_arxan` and `ilhook`'s `Hooker::new` ask the running image's `.pdata`
whether the address is a function entry with room for the patch. On another build a 2.7.2.0 rva
can still land on some function's entry, and the hook then goes in on the wrong function. They
are the right API for an address found at runtime; a constant handed to one needs an
`is_supported_build()` refusal in front of it, and the selftest proves that a constant without
one is reported.

The ratchet
-----------
`scripts/audit-ungated-game-addresses.baseline.json` records each finding that stands today,
keyed by file, function and expression, with the sinks it reaches and a one-line reason: why it
is safe (the caller refused already, the value is the gate's own version-resource walk) or what
would fix it. The gate fails on a finding with no row, on a finding whose sinks changed, on a row
whose site is gone (stale: delete the row in the same change), and on a row without a reason. A
finding that a one-line guard would clear gets the guard, not a row.

Usage
    python3 scripts/audit-ungated-game-addresses.py              # the gate
    python3 scripts/audit-ungated-game-addresses.py --report     # every site and its verdict
    python3 scripts/audit-ungated-game-addresses.py --selftest
"""

from __future__ import annotations

import bisect
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rva_symbols import code_only  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(REPO, "scripts", "audit-ungated-game-addresses.baseline.json")

# ---------------------------------------------------------------------------- the gate's names
# Functions that resolve an address (or an rva) through the build check and hand back a refusal.
GATE_FNS = {
    "resolve_game_address",
    "resolve_game_address_fmt",
    "resolve_detour_address",
    "game_data_addr",
    "game_data_addr_offset",
    "read_global_ptr",
    "read_global_u8",
    "write_global_u8",
}
# er-hook entry points that run their target through `resolve_target` -> `resolve_detour_address`
# before MinHook sees it. `api_gating` reads er-hook to prove each one still does.
HOOK_APIS = {
    "register_union_hook",
    "register_union_hook5",
    "register_shared_hook",
    "register_shared_hook_with_budget",
    "register_shared_hook5",
    "register_shared_hook5_with_budget",
    "er_effects_union_register_2720",
    "er_effects_union_register5_2720",
}
HOOK_METHODS = {"MhHook::new"}
# Functions whose `None`/`Err` is a refusal, so a `?` or a `let .. else` on one leaves the
# function on another build. `game_rva_for_hook` is not here: it checks nothing.
GUARD_FNS = {
    "resolve_game_address",
    "resolve_game_address_fmt",
    "resolve_detour_address",
    "resolve_call_site_rva",
    "resolve_call_site_band",
    "game_rva",
    "game_rva_named",
}
GUARD_CALL = "(?:" + "|".join(sorted(GUARD_FNS, key=len, reverse=True)) + ")"
# The one root `gate_proof` accepts, and what its body must still compare.
BUILD_CHECK = "is_supported_build"
BUILD_CHECK_BODY = (r"\bgame_file_version\s*\(", r"\bSUPPORTED_FILE_VERSION\b")
# Adapters that pass a refusal through: `gate(..).ok_or(..)?` still leaves on `None`.
ADAPTERS = {
    "ok_or_else", "ok_or", "map_err", "ok", "filter", "map", "and_then", "context", "with_context",
    "then_some", "then", "inspect",
}
# Hook entry points that audit `.pdata` and not the build.
PDATA_HOOK = re.compile(
    r"(?:^|::)(?:\w*_runtime_derived\w*|new_following_arxan|new_runtime_derived)$|(?:^|::)Hooker::new$"
)
CALL_HEADS = {"transmute", "transmute_copy"}
READ_HEAD = re.compile(
    r"^(?:read_bytes|safe_read_\w+|read_c_string|read_unaligned|read_volatile|from_raw_parts"
    r"|\w*_matches|check_window|checked_entry|pins_match|read_u8|read_u16|read_u32|read_u64"
    r"|read_usize|read_i32|read_f32)$"
)
WRITE_HEAD = re.compile(
    r"^(?:write_code_bytes?|safe_write_\w+|write_volatile|write_unaligned|copy_nonoverlapping"
    r"|write_bytes|patch_\w+)$"
)
STORE_METHOD = {"store", "swap", "compare_exchange", "compare_exchange_weak", "set", "insert", "push", "get_or_init"}
BENIGN = {"GATED", "LOG", "COMPARE", "REPORT"}

# ---------------------------------------------------------------------------- what a site is
BASE_IDENT = (
    r"(?:\bself\s*\.\s*(?:base|module_base)"
    r"|(?<![.\w])(?:base|image_base|module_base|game_base|mod_base|game_module|exe_base|img_base)\b)"
)
BASE_CALL = (
    r"(?:(?<![\w])(?:[\w]+::)*game_module_base\s*\(\s*\)\s*"
    r"(?:\?|\.\s*unwrap\s*\(\s*\)|\.\s*unwrap_or\s*\([^()]*\)|\.\s*ok\s*\(\s*\)\s*\?|\.\s*expect\s*\([^()]*\)))"
)
ADD = re.compile(
    r"(?P<base>" + BASE_CALL + "|" + BASE_IDENT + r")(?:\s*as\s+usize)?\s*"
    r"(?P<op>\+(?!=)|\.\s*(?:wrapping_add|checked_add|byte_add|add)\s*\()\s*"
)
OPERAND = re.compile(
    r"\(?\s*(?P<operand>0x[0-9a-fA-F_]+|(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)"
)
# Any named term, for a named rva: `start + RUN_RVA` is a site whatever `start` is called.
TERM = r"(?<![.\w])(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*"
ADD_ANY = re.compile(
    r"(?P<base>" + TERM + r")(?:\s*as\s+usize)?\s*"
    r"(?P<op>\+(?!=)|\.\s*(?:wrapping_add|checked_add|byte_add|add)\s*\()\s*"
)
# Every addition of a named rva, whatever stands on its left; one that no site covers is `UNSEEN`.
FLOOR = re.compile(
    r"(?P<op>\+=?|\.\s*(?:wrapping_add|checked_add|byte_add|add)\s*\()\s*\(?\s*"
    r"(?P<operand>(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)"
)
FOR_HOOK = re.compile(r"(?<![\w])(?:[\w]+::)*game_rva_for_hook\s*\(")
# Seamless Co-op's `ersc.dll`: its rvas belong to its own build, pinned by `ersc::SUPPORTED`.
FOREIGN_BASE = re.compile(r"\bersc_module_base\b|\bersc::Abi\b|\bseamless_base\b|\bersc_base\b|\bdll_base\b")
# How an operand read out of the image's own headers is bound, by name: the PE walkers, and a u32
# read at a named field of an RTTI locator, class descriptor or exception ThrowInfo. Any other
# read (`from_le_bytes`, a helper that merely mentions a section or a header) is classified.
PE_WALKERS = ("pe_iat_directory", "pe_import_descriptors", "resource_directory", "version_resource_data")
RTTI_FIELDS = (
    "COL_SELF_RVA_OFFSET",
    "COL_TYPE_DESCRIPTOR_RVA_OFFSET",
    "COL_CLASS_DESCRIPTOR_RVA_OFFSET",
    "CHD_BASE_CLASS_ARRAY_RVA_OFFSET",
    "BASE_CLASS_DESCRIPTOR_TYPE_RVA_OFFSET",
    "THROW_INFO_CATCHABLE_ARRAY_OFFSET",
    "CATCHABLE_ARRAY_FIRST_TYPE_OFFSET",
    "CATCHABLE_TYPE_DESCRIPTOR_OFFSET",
)
SELF_DESCRIBING = re.compile(
    r"(?<![.\w])(?:[\w]+::)*(?:" + "|".join(PE_WALKERS) + r")\s*\("
    r"|\b(?:safe_)?read_u32\s*\(\s*[a-z_]\w*\s*\+\s*(?:" + "|".join(RTTI_FIELDS) + r")\s*\)"
)
SKIP_PATH = re.compile(r"(?:^|/)(?:tests|benches|examples)/|(?:^|/)tests\.rs$|(?:^|/)build\.rs$|^build-support/")


MEASURE_SUFFIXES = ("_OFFSET", "_offset", "_SIZE", "_size", "_LEN", "_len")


def rva_shaped(operand: str) -> bool:
    if operand.startswith("0x"):
        return len(operand[2:].replace("_", "").lstrip("0")) >= 5
    segments = re.split(r"\s*(?:::|\.)\s*", operand)
    if segments[-1].endswith(MEASURE_SUFFIXES):
        return False  # `er_game_base::rva::CS_MENU_MAN_MENU_DATA_OFFSET` is an offset in a module named `rva`
    for segment in segments:
        if segment.endswith(MEASURE_SUFFIXES):
            continue
        if re.search(r"(?:^|_)(?:rva|RVA)s?(?:_|$)", segment) or re.search(r"[a-z]Rva(?:[A-Z]|$)", segment):
            return True
    return False


# ---------------------------------------------------------------------------- source model
class Source:
    def __init__(self, path: str, raw: str):
        self.path = path
        self.raw = raw
        code = code_only(raw)
        # A bracket inside a char literal (`'('`) would unbalance every walk below.
        self.code = re.sub(r"'[()\[\]{}]'", "' '", code)
        self._starts = [0]
        for line in raw.split("\n"):
            self._starts.append(self._starts[-1] + len(line) + 1)
        self.fns = self._fn_spans()
        self.test_spans = self._test_spans()

    def lineno(self, pos: int) -> int:
        return bisect.bisect_right(self._starts, pos)

    def _block_end(self, brace: int) -> int:
        depth, i, code = 0, brace, self.code
        while i < len(code):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return len(code)

    def _fn_spans(self):
        spans = []
        for m in re.finditer(r"\bfn\s+([A-Za-z_]\w*)", self.code):
            # The body opens at the first `{` after the signature; a `;` first is a declaration.
            i, depth = m.end(), 0
            while i < len(self.code):
                c = self.code[i]
                if c in "([<":
                    depth += 1
                elif c in ")]>" and depth:
                    depth -= 1
                elif c == ";" and depth == 0:
                    i = -1
                    break
                elif c == "{" and depth == 0:
                    break
                i += 1
            if i < 0 or i >= len(self.code):
                continue
            spans.append((m.start(), i, self._block_end(i), m.group(1)))
        return spans

    def _test_spans(self):
        """The item each `#[cfg(test)]` / `#[test]` attribute covers.

        The item ends at its own block when a `{` comes first, and at the `;` that ends it when one
        comes first: `#[cfg(test)] mod tests;` and `#[cfg(test)] use super::*;` cover only
        themselves, not the function after them. A `}` first means the attribute was on the last
        field or variant of the enclosing block. A `,` is not an end: it also separates generic
        arguments (`-> Result<(), String> {`).
        """
        spans = []
        code = self.code
        for m in re.finditer(r"#\[cfg\(test\)\]|#\[test\]", code):
            depth, i = 0, m.end()
            while i < len(code):
                c = code[i]
                if c in "([":
                    depth += 1
                elif c in ")]":
                    depth -= 1
                elif depth == 0 and c == "{":
                    spans.append((m.start(), self._block_end(i)))
                    break
                elif depth == 0 and c in ";}":
                    spans.append((m.start(), i))
                    break
                i += 1
        return spans

    def in_test(self, pos: int) -> bool:
        return any(lo <= pos <= hi for lo, hi in self.test_spans)

    def enclosing_fn(self, pos: int):
        best = None
        for start, body, end, name in self.fns:
            if body < pos < end and (best is None or body > best[1]):
                best = (start, body, end, name)
        return best


def _name_before(code: str, i: int) -> str:
    """The call head ending just before index `i` (the open paren), turbofish skipped."""
    j = i - 1
    while j >= 0 and code[j] in " \t\n":
        j -= 1
    if j >= 0 and code[j] == ">":
        depth = 0
        while j >= 0:
            if code[j] == ">":
                depth += 1
            elif code[j] == "<":
                depth -= 1
                if depth == 0:
                    j -= 1
                    break
            j -= 1
    k = j
    while k >= 0 and (code[k].isalnum() or code[k] in "_:!."):
        k -= 1
    return code[k + 1 : j + 1].strip(":")


def is_struct_literal(code: str, brace: int) -> bool:
    """`Name { field: .. }` rather than a block opened after `if X {`, `-> Result<..> {`, `match x {`."""
    j = brace - 1
    while j >= 0 and code[j] in " \t\n":
        j -= 1
    k = j
    while k >= 0 and (code[k].isalnum() or code[k] in "_:"):
        k -= 1
    last = code[k + 1 : j + 1].split("::")[-1]
    if not re.match(r"^[A-Z][A-Za-z0-9]*$", last) or not re.search(r"[a-z]", last):
        return False
    if re.search(r"(?:->|\b(?:impl|for|struct|enum|trait|union|if|while|match|in))\s*$", code[: k + 1]):
        return False
    return bool(re.match(r"\s*(?:[a-z_]\w*\s*(?::(?!:)|,|\})|\.\.)", code[brace + 1 :]))


def _continues_expression(code: str, after: int) -> bool:
    """Whether a `}` ends a block inside an expression (`f(|x| { .. }, y)`) rather than a statement."""
    rest = code[after : after + 40].lstrip()
    return rest[:1] in tuple(",.)?]+-/%|^<>=") or bool(re.match(r"(?:else|as)\b", rest))


def heads(code: str, pos: int):
    """Enclosing groups at `pos`, innermost first, up to the statement: (name, open index)."""
    out = []
    depth, i = 0, pos - 1
    while i >= 0:
        c = code[i]
        if c == "}" and depth == 0 and not _continues_expression(code, i + 1):
            return out, i + 1  # the end of the block statement before this one
        if c in ")]}":
            depth += 1
        elif c in "([":
            if depth:
                depth -= 1
            elif c == "[":
                out.append(("[", i))
            else:
                out.append((_name_before(code, i), i))
        elif c == "{":
            if depth:
                depth -= 1
            elif is_struct_literal(code, i):
                out.append(("struct:" + _name_before(code, i), i))
            else:
                return out, i + 1
        elif c == ";" and depth == 0:
            return out, i + 1
        i -= 1
    return out, 0


def close_of(code: str, open_index: int) -> int:
    depth, i = 0, open_index
    while i < len(code):
        if code[i] in "([{":
            depth += 1
        elif code[i] in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(code)


def expr_end(code: str, pos: int) -> int:
    """End of the additive expression starting at `pos` (exclusive)."""
    depth, i = 0, pos
    while i < len(code):
        c = code[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            if depth == 0:
                return i
            depth -= 1
        elif depth == 0:
            two = code[i : i + 2]
            if c in ",;{}?" or two in ("==", "!=", "<=", ">=", "&&", "||", "=>", ".."):
                return i
            if c in "<>=" and not code[max(0, i - 4) : i].rstrip().endswith(("as *mut", "as *const")):
                return i
        i += 1
    return i


def top_level_commas(code: str, open_index: int, close_index: int) -> list[int]:
    out, depth = [], 0
    for i in range(open_index + 1, close_index):
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(i)
    return out


def arg_index(code: str, open_index: int, pos: int) -> int:
    close = close_of(code, open_index)
    return sum(1 for comma in top_level_commas(code, open_index, close) if comma < pos)


def is_comparison(code: str, start: int, end: int) -> bool:
    before = code[:start].rstrip(" \t\n(&")
    after = code[end:].lstrip(" \t\n)")
    if re.search(r"(?:[<>]=?|[=!]=)$", before) and not before.endswith(("->", "=>")):
        return True
    return bool(re.match(r"(?:[<>]=?|[=!]=)(?!>)", after))


# ---------------------------------------------------------------------------- helpers
def last_segment(name: str) -> str:
    return re.split(r"::|\.", name)[-1] if name else ""


def is_hook_api(name: str) -> bool:
    tail = name.split("::")
    return last_segment(name) in HOOK_APIS or "::".join(tail[-2:]) in HOOK_METHODS


def fn_params(src: Source, fn) -> list[str]:
    start, body, _end, _name = fn
    sig = src.code[start:body]
    open_index = sig.find("(")
    if open_index < 0:
        return []
    close = close_of(sig, open_index)
    names = []
    inner = sig[open_index + 1 : close]
    for part in re.split(r",(?![^<(\[]*[>)\]])", inner):
        m = re.match(r"\s*(?:mut\s+)?([a-z_]\w*)\s*:", part)
        if m:
            names.append(m.group(1))
        elif re.match(r"\s*&?\s*(?:mut\s+)?self\b", part):
            continue
    return names


def crate_of(path: str) -> str:
    parts = path.split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "crates" else parts[0]


class Corpus:
    """Every scanned file, plus what each local function does with each of its parameters.

    A site handed to a local function is followed into it: `create_and_apply_single_hook(..,
    (base + RVA) as *mut c_void, ..)` reaches `MhHook::new` through `create_absolute_hook`, and
    `report_routing_detour(.., addr, ..)` only prints the address. A summary is computed when it is
    first asked for and then kept; a function reached again while its own summary is being
    computed answers as unknown, which keeps recursion finite. Keyed by crate and name, so two
    crates' `install_one` stay apart; within a crate, definitions sharing a name are united.
    """

    def __init__(self, sources):
        self.sources = sources
        self.fns: dict = {}
        for src in sources:
            for fn in src.fns:
                if src.in_test(fn[0]):
                    continue
                self.fns.setdefault((crate_of(src.path), fn[3]), []).append((src, fn))
        self._summaries: dict = {}
        self._refuses: dict = {}

    def summary(self, crate: str, name: str, index: int):
        """The sinks parameter `index` of `crate::name` reaches, or None when there is no body."""
        key = (crate, name, index)
        if key in self._summaries:
            return self._summaries[key]
        definitions = self.fns.get((crate, name))
        if not definitions:
            return None
        self._summaries[key] = {"ARG:" + name}  # while in progress
        kinds: set = set()
        for src, fn in definitions:
            params = fn_params(src, fn)
            if index < len(params):
                kinds |= _follow(src, params[index], fn[1] + 1, self, 0)
        self._summaries[key] = kinds
        return kinds

    def refuses(self, crate: str, name: str) -> bool:
        """Whether a local function asks the build check, so a `?` on its result is a refusal."""
        key = (crate, name)
        if key not in self._refuses:
            self._refuses[key] = any(
                re.search(r"\bis_supported_build\s*\(|\b" + GUARD_CALL + r"\s*\(", src.code[fn[1] : fn[2]])
                for src, fn in self.fns.get(key, [])
            )
        return self._refuses[key]


# ---------------------------------------------------------------------------- one position
def classify_use(src: Source, start: int, end: int, helpers: Corpus, depth: int = 0) -> set:
    """Where the value at `code[start:end]` goes: a set of sink names (see the module docstring)."""
    code = src.code
    if is_comparison(code, start, end):
        return {"COMPARE"}
    groups, stmt_start = heads(code, start)
    for position, (name, open_index) in enumerate(groups):
        last = last_segment(name)
        if name in ("for", "let", "while"):
            return {"SHADOW"}  # a pattern that binds the name afresh, not a use of it
        if name == "":
            close = close_of(code, open_index)
            commas = top_level_commas(code, open_index, close)
            if commas:
                return _tuple_element(src, open_index, close, start, groups[position + 1 :], helpers, depth)
            # A grouping paren: `*(x as *const T)` is a read, `*(x as *mut T) = v` a write.
            before = code[:open_index].rstrip()
            after = code[close + 1 :]
            if before.endswith("*") and not re.search(r"[\w)\]]\s*\*$", before):
                return {"WRITE"} if re.match(r"\s*=(?!=)", after) else {"READ"}
            if re.match(r"\s*\.\s*write\w*\s*\(", after):
                return {"WRITE"}
            if re.match(r"\s*\.\s*read\w*\s*\(", after):
                return {"READ"}
            continue
        if name == "[":
            return {"STORE"}
        if name.startswith("struct:"):
            outer = [g for g, _ in groups[position + 1 :] if g]
            return {"REPORT"} if outer and last_segment(outer[0]) == "Err" else {"STORE"}
        if name.endswith("!"):
            return {"COMPARE"} if "assert" in name else {"LOG"}
        if last in GATE_FNS or is_hook_api(name):
            return {"GATED"}
        if PDATA_HOOK.search(name):
            return {"HOOK"}
        if last in CALL_HEADS:
            return {"CALL"}
        if READ_HEAD.match(last):
            return {"READ"}
        if WRITE_HEAD.match(last) or (last == "write" and "." in name):
            return {"WRITE"}
        if "." in name and last in STORE_METHOD:
            return {"STORE"}
        if last in ("Ok", "Some", "Err"):
            # `let Ok(x) = ..`, `if let Some(x) = ..` and a match arm `Ok(x) =>` bind afresh.
            close = close_of(code, open_index)
            if re.search(r"\blet\s+(?:[\w]+::)*$", code[stmt_start : open_index - len(name)]) or re.match(
                r"\s*(?:=>|=(?!=))", code[close + 1 :]
            ):
                return {"SHADOW"}
            return {"REPORT"} if last == "Err" else {"RETURN"}
        if last in ("map", "and_then", "then", "then_some", "filter", "unwrap_or", "ok_or", "ok_or_else"):
            continue
        kinds = helpers.summary(crate_of(src.path), last, arg_index(code, open_index, start))
        if kinds is not None:
            return set(kinds) or {"LOG"}  # a parameter its body never uses reaches nothing
        return {"ARG:" + last}
    # No enclosing call: the statement itself says where the value goes.
    prefix = code[stmt_start:start]
    after = code[end:]
    if re.match(r"\s*as\s+(?:unsafe\s+)?extern\b", after):
        return {"CALL"}
    if re.match(r"\s*\.\s*write\w*\s*\(", after):
        return {"WRITE"}
    bind = re.match(r"\s*(?:unsafe\s*\{\s*)?let\s+(?:mut\s+)?([a-z_]\w*)\s*(?::[^=]+)?=\s*(?:unsafe\s*\{\s*)?\(?\s*$", prefix)
    if bind:
        return _follow(src, bind.group(1), end, helpers, depth)
    if re.search(r"\breturn\s*$", prefix):
        return {"RETURN"}
    if re.match(r"\s*\}", after):
        fn = src.enclosing_fn(start)
        close = end + len(after) - len(after.lstrip()) if after.lstrip() else end
        if fn and close == fn[2]:
            return {"RETURN"}
        return {"OTHER"}
    assign = re.match(r"\s*([a-z_]\w*)\s*=\s*$", prefix)
    if assign:
        fn = src.enclosing_fn(start)
        declared = fn and re.search(
            r"\blet\s+mut\s+" + re.escape(assign.group(1)) + r"\b", src.code[fn[1] : start]
        )
        if declared:
            return _follow(src, assign.group(1), end, helpers, depth)  # a local, reassigned
    if re.search(r"(?<![=!<>])=\s*$", prefix):
        return {"STORE"}
    return {"OTHER"}


def _tuple_element(
    src: Source, open_index: int, close: int, pos: int, outer, helpers: Corpus, depth: int
) -> set:
    """A value in a tuple -- of an array literal, or pushed onto a local -- followed into the `for`
    pattern element it lands in."""
    code = src.code
    index = sum(1 for comma in top_level_commas(code, open_index, close) if comma < pos)
    if outer and outer[0][0].endswith(".push") and re.match(r"^[a-z_]\w*\.push$", outer[0][0]):
        # `local.push((label, address))` and later `for (label, address) in &local`.
        receiver = outer[0][0].split(".")[0]
        loop = re.compile(
            r"\bfor\s+&?\s*\(([^)]*)\)\s+in\s*&?\s*(?:mut\s+)?" + re.escape(receiver) + r"\b"
        ).search(code, close)
        if loop:
            names = [p.strip() for p in loop.group(1).split(",")]
            if index < len(names):
                name = re.sub(r"^(?:&|ref\s+|mut\s+)+", "", names[index]).strip()
                if re.match(r"^[a-z_]\w*$", name) and name != "_":
                    return _follow(src, name, loop.end(), helpers, depth)
                return set()
        return {"STORE"}
    if not outer or outer[0][0] != "[":
        return {"STORE"}
    array_open = outer[0][1]
    array_close = close_of(code, array_open)
    before = code[: array_open].rstrip()
    pattern = None
    scope_from = array_close
    direct = re.search(r"\bfor\s+&?\s*\(([^)]*)\)\s+in\s*&?\s*$", before)
    if direct:
        pattern = direct.group(1)
    else:
        named = re.search(r"\blet\s+(?:mut\s+)?([a-z_]\w*)\s*(?::[^=]+)?=\s*&?\s*$", before)
        if named:
            loop = re.compile(
                r"\bfor\s+&?\s*\(([^)]*)\)\s+in\s*&?\s*" + re.escape(named.group(1)) + r"\b"
            ).search(code, array_close)
            if loop:
                pattern, scope_from = loop.group(1), loop.end()
    if pattern is None:
        return {"STORE"}
    names = [p.strip() for p in pattern.split(",")]
    if index >= len(names):
        return {"STORE"}
    name = re.sub(r"^(?:&|ref\s+|mut\s+)+", "", names[index]).strip()
    if not re.match(r"^[a-z_]\w*$", name) or name == "_":
        return set()
    return _follow(src, name, scope_from, helpers, depth)


def _follow(src: Source, name: str, after: int, helpers: Corpus, depth: int) -> set:
    """Every use of binding `name` after `after`, to the end of its function."""
    if depth > 3:
        return {"OTHER"}
    fn = src.enclosing_fn(after)
    end = fn[2] if fn else len(src.code)
    kinds: set = set()
    rx = re.compile(r"(?<![.\w])" + re.escape(name) + r"\b(?!\s*:(?!:))")
    for m in rx.finditer(src.code, after, end):
        if re.search(r"\b(?:let|for)\s+(?:mut\s+)?$", src.code[: m.start()]):
            break  # shadowed
        use = classify_use(src, m.start(), m.end(), helpers, depth + 1)
        if "SHADOW" in use:
            break
        kinds |= use
    return kinds


# ---------------------------------------------------------------------------- refusals
# A refusal counts only where it keeps the site from running on another build. The helpers below
# answer the three questions that takes: does this call hand back a refusal, is the refusal
# consumed by something that leaves (`?`, a `let .. else`, an `if` block that returns), and how
# far does leaving reach (the rest of the function, the closure, or the loop body).
CALL_AT = re.compile(r"(?:[\w]+::)*([A-Za-z_]\w*)\s*\(")
ANY_CALL = re.compile(r"(?<![.\w])(?:[\w]+::)*([A-Za-z_]\w*)\s*\(")
NOT_A_CALL = {"if", "while", "match", "return", "for", "in", "Some", "Ok", "Err"}


def _open_of(code: str, close: int) -> int:
    """The bracket that opens the group closed at `close`."""
    depth, i = 0, close
    while i >= 0:
        if code[i] in ")]}":
            depth += 1
        elif code[i] in "([{":
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return 0


def _chain_end(code: str, close: int) -> int:
    """Index just past the adapters (`.ok_or(..)`, `.map_err(..)`, ..) chained onto the call whose
    argument list closes at `close`."""
    adapter = re.compile(r"\s*\.\s*([a-z_]\w*)\s*\(")
    i = close + 1
    while True:
        m = adapter.match(code, i)
        if not m or m.group(1) not in ADAPTERS:
            return i
        i = close_of(code, m.end() - 1) + 1


def _refusing_head(src: Source, name: str, close: int, corpus) -> bool:
    """Whether a call to `name` (argument list closing at `close`) hands back a refusal: a name on
    `GUARD_FNS`, a local function that asks the gate, or `is_supported_build()` turned into an
    `Option` by `.then_some(..)` / `.then(..)`. The bare check is a `bool` and refuses nothing."""
    if name == BUILD_CHECK:
        return bool(re.compile(r"\s*\.\s*then(?:_some)?\s*\(").match(src.code, close + 1))
    return name in GUARD_FNS or bool(corpus is not None and corpus.refuses(crate_of(src.path), name))


def _gate_chain(src: Source, at: int, corpus) -> int | None:
    """When a refusing call starts at `at`, the index just past its adapter chain."""
    m = CALL_AT.match(src.code, at)
    if not m:
        return None
    close = close_of(src.code, m.end() - 1)
    if not _refusing_head(src, m.group(1), close, corpus):
        return None
    return _chain_end(src.code, close)


def _wrappers_before(code: str, at: int) -> int:
    """How many `(` and `unsafe {` open directly before `at`: their closers may stand between a
    chain and its `?`, as in `(unsafe { verify(..) })?`."""
    m = re.search(r"(?:(?:\(|unsafe\s*\{)\s*)*$", code[max(0, at - 80) : at])
    return len(re.findall(r"[({]", m.group(0))) if m else 0


def _terminator(code: str, end: int, wrappers: int = 0):
    """What consumes a refusing chain that ends at `end`: `("?", index)`, `("else", brace)` for a
    `let .. else`, `("block", brace)` for an `if let`, or None (kept, discarded, unwrapped)."""
    i, closed = end, 0
    closer = re.compile(r"\s*[)}]")
    while closed < wrappers:
        m = closer.match(code, i)
        if not m:
            break
        i, closed = m.end(), closed + 1
    m = re.compile(r"\s*(\?|else\s*\{|\{)").match(code, i)
    if not m:
        return None
    token = m.group(1)
    if token == "?":
        return ("?", m.end() - 1)
    return ("else" if token.startswith("else") else "block", m.end() - 1)


def _enclosing_braces(code: str, at: int, floor: int) -> list:
    """The unmatched `{` around `at`, innermost first, down to the one at `floor`."""
    out, depth, i = [], 0, at - 1
    while i >= floor:
        c = code[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth:
                depth -= 1
            else:
                out.append(i)
        i -= 1
    return out


def _is_closure_body(code: str, brace: int) -> bool:
    return bool(re.search(r"\|\s*(?:->\s*[^{};|]+?)?\s*$", code[max(0, brace - 200) : brace]))


LOOP_HEAD = re.compile(r"(?:\bfor\b[^{};]*\bin\b[^{};]*|\bwhile\b[^{};]*|\bloop\s*)$")


def _scope_end(code: str, at: int, fn, kind: str) -> int:
    """Where the code a refusal at `at` keeps from running ends. `return` (and `?`) leaves the
    closure or function it is written in; `loop` (`continue`, `break`) leaves the loop body and
    cannot cross a closure; `block` is the block the statement sits in. A refusal that reaches
    nothing answers `at`, so no site after it counts as guarded."""
    braces = _enclosing_braces(code, at, fn[1])
    if kind == "block":
        return close_of(code, braces[0]) if braces else fn[2]
    for brace in braces:
        if _is_closure_body(code, brace):
            return close_of(code, brace) if kind == "return" else at
        if kind == "loop" and LOOP_HEAD.search(code[max(fn[1], brace - 300) : brace]):
            return close_of(code, brace)
    return fn[2] if kind == "return" else at


def _last_statement(code: str, brace: int) -> str:
    """The last statement of the block opened at `brace`, whitespace folded."""
    close = close_of(code, brace)
    depth, start, last = 0, brace + 1, ""
    for i in range(brace + 1, close):
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if c == "}" and depth == 0 and not _continues_expression(code, i + 1):
                last, start = code[start : i + 1], i + 1  # a block statement with no `;`
        elif c == ";" and depth == 0:
            if code[start:i].strip():
                last = code[start:i]
            start = i + 1
    tail = code[start:close]
    return " ".join((tail if tail.strip() else last).split())


def _diverges(code: str, brace: int) -> str | None:
    """`return` or `loop` when the block opened at `brace` always leaves, else None."""
    last = _last_statement(code, brace)
    if re.match(r"return\b", last) or re.match(r"(?:[\w]+::)*(?:panic|unreachable|todo|unimplemented)\s*!", last):
        return "return"
    if re.fullmatch(r"(?:Err\s*\(.*\)|None)\s*\?", last, re.S):
        return "return"
    if re.match(r"(?:continue|break)\b", last):
        return "loop"
    return None


# ---------------------------------------------------------------------------- origin and guards
BINDINGS = (
    (r"\blet\s+(?:mut\s+)?NAME\b[^=;]*=(?P<rhs>[^;]*)", "let"),
    (r"\blet\s+(?:Some|Ok)\s*\(\s*(?:\(\s*)?[^=]*\bNAME\b[^=]*\)\s*=(?P<rhs>[^;]*)", "pattern"),
    (r"\blet\s+\(\s*[^=]*\bNAME\b[^=]*\)\s*=(?P<rhs>[^;]*)", "let"),
    (r"\bfor\s+&?\s*\(?[^{]*?\bNAME\b[^{]*?\bin\b(?P<rhs>[^{]*)\{", "for"),
    (r"(?P<rhs>[^;{}]*)\|\s*[^|]*\bNAME\b[^|]*\|", "closure"),
)


def _adapter_on_gate(src: Source, open_index: int, corpus, depth: int = 0) -> bool:
    """Whether the `(` at `open_index` opens an adapter (`.map(`, `.and_then(`, ..) chained onto a
    refusing call, so a closure passed there runs only when the gate let the value through."""
    code = src.code
    m = re.compile(r"\.\s*([a-z_]\w*)\s*$").search(code, max(0, open_index - 80), open_index)
    if not m or m.group(1) not in ADAPTERS or depth > 8:
        return False
    j = m.start() - 1
    while j >= 0 and code[j] in " \t\n":
        j -= 1
    if j < 0 or code[j] != ")":
        return False
    inner_open = _open_of(code, j)
    name = _name_before(code, inner_open)
    if "." not in name:
        return _refusing_head(src, last_segment(name), j, corpus)
    return _adapter_on_gate(src, inner_open, corpus, depth + 1)


def _derived_from_gate(src: Source, rhs_start: int, rhs_end: int, kind: str, corpus) -> bool:
    """Whether a binding's value exists only when a gate allowed it: `let x = gate(..)?`,
    `let Some(x) = gate(..) else`, `if let Some(x) = gate(..)`, `gate(..).map(|x| ..)`."""
    code = src.code
    if kind in ("let", "pattern"):
        lead = re.compile(r"\s*(?:(?:unsafe\s*\{|\()\s*)*").match(code, rhs_start)
        end = _gate_chain(src, lead.end(), corpus)
        if end is None:
            return False
        term = _terminator(code, end, len(re.findall(r"[({]", lead.group(0))))
        if term is None:
            return False
        return term[0] == "?" if kind == "let" else term[0] in ("?", "else", "block")
    if kind == "closure":
        before = re.sub(r"\bmove\s*$", "", code[:rhs_end].rstrip()).rstrip()
        return before.endswith("(") and _adapter_on_gate(src, len(before) - 1, corpus)
    return False


def operand_origin(src: Source, fn, operand: str, pos: int, corpus=None, depth: int = 0) -> str:
    """`gate`, `self` or `constant`: where a bare local operand's value came from."""
    if not re.match(r"^[a-z_]\w*$", operand):
        return "constant"
    code = src.code
    lo = fn[1] if fn else 0
    found = None
    for template, kind in BINDINGS:
        for m in re.compile(template.replace("NAME", re.escape(operand))).finditer(code, lo, pos):
            if found is None or m.start() > found[0]:
                found = (m.start(), m.start("rhs"), m.end("rhs"), kind)
    if found is None:
        return "constant"  # a parameter or a constant spelled in lower case: the caller's value
    _, rhs_start, rhs_end, kind = found
    rhs = code[rhs_start:rhs_end]
    if _derived_from_gate(src, rhs_start, rhs_end, kind, corpus):
        return "gate"
    if kind != "closure" and SELF_DESCRIBING.search(rhs):
        return "self"
    # `for (name_rva, ..) in imports` takes its origin from where `imports` came from.
    bare = re.fullmatch(r"\s*&?\s*(?:mut\s+)?([a-z_]\w*)\s*(?:\.\s*(?:iter|into_iter)\s*\(\s*\)\s*)?", rhs)
    if bare and depth < 2 and kind in ("let", "for"):
        return operand_origin(src, fn, bare.group(1), rhs_start, corpus, depth + 1)
    return "constant"


NOT_BUILD = re.compile(r"!\s*(?:[\w]+::)*is_supported_build\s*\(\s*\)")
IS_BUILD = re.compile(r"\s*(?:[\w]+::)*is_supported_build\s*\(\s*\)\s*(?:&&[^|]*)?")


def _cond_brace(code: str, start: int, limit: int) -> int | None:
    """The `{` that opens an `if` block whose condition starts at `start`."""
    depth, i = 0, start
    while i < limit:
        c = code[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and c == "{":
            return i
        elif depth == 0 and c == ";":
            return None
        i += 1
    return None


def _if_guard(src: Source, fn, pos: int) -> str | None:
    """An `if` that keeps the site from running on another build: the site is inside
    `if is_supported_build() { .. }`, or after an `if !is_supported_build() { .. }` (or an
    `if addr == 0` on a `game_data_addr` result) whose block always leaves, within the reach of
    that leaving."""
    code = src.code
    for m in re.compile(r"\bif\s+(?!let\b)").finditer(code, fn[1], pos):
        brace = _cond_brace(code, m.end(), pos)
        if brace is None:
            continue
        cond = code[m.end() : brace]
        close = close_of(code, brace)
        if IS_BUILD.fullmatch(cond):
            if brace < pos < close:
                return "if is_supported_build()"
            continue
        label = None
        if NOT_BUILD.search(cond) and "&&" not in cond:
            label = "if !is_supported_build()"
        else:
            zero = re.fullmatch(r"\s*([a-z_]\w*)\s*==\s*(?:0|ZERO|NULL\w*)\s*", cond)
            bound = zero and re.search(
                r"\blet\s+(?:mut\s+)?" + re.escape(zero.group(1))
                + r"\s*(?::[^=]+)?=\s*(?:[\w]+::)*game_data_addr(?:_offset)?\s*\(",
                code[fn[1] : m.start()],
            )
            if bound:
                label = f"if {zero.group(1)} == 0 on a game_data_addr result"
        if label is None or close >= pos:
            continue  # not a refusal, or the site is in the refusal's own branch
        kind = _diverges(code, brace)
        if kind and pos < _scope_end(code, m.start(), fn, kind):
            return label
    return None


def _call_guard(src: Source, fn, pos: int, corpus) -> str | None:
    """A refusing call earlier in the function whose refusal leaves before the site: `gate(..)?`,
    `let Some(..) = gate(..) else { .. }`, or the site inside `if let Some(..) = gate(..) { .. }`."""
    code = src.code
    for m in ANY_CALL.finditer(code, fn[1], pos):
        name = m.group(1)
        if name in NOT_A_CALL:
            continue
        close = close_of(code, m.end() - 1)
        if close >= pos or not _refusing_head(src, name, close, corpus):
            continue  # the site is inside the call's own arguments, or the call refuses nothing
        term = _terminator(code, _chain_end(code, close), _wrappers_before(code, m.start()))
        if term is None:
            continue
        kind, at = term
        label = "is_supported_build().then_some(..)" if name == BUILD_CHECK else f"{name}(..)"
        _groups, stmt_start = heads(code, m.start())
        prefix = code[stmt_start : m.start()]
        if kind == "?":
            if at < pos < _scope_end(code, m.start(), fn, "return"):
                return f"{label}?"
        elif kind == "else" and re.match(r"\s*let\s+(?:Some|Ok)\s*\(", prefix):
            leave = _diverges(code, at) or "block"
            if close_of(code, at) < pos < _scope_end(code, stmt_start, fn, leave):
                return f"let .. = {label} else"
        elif kind == "block" and re.match(r"\s*(?:if|while)\s+let\s+(?:Some|Ok)\s*\(", prefix):
            if at < pos < close_of(code, at):
                return f"if let .. = {label}"
    return None


def guarded(src: Source, fn, pos: int, corpus: Corpus | None = None) -> str | None:
    """The refusal earlier in this function that the site sits behind, or None."""
    if fn is None:
        return None
    return _if_guard(src, fn, pos) or _call_guard(src, fn, pos, corpus)


# ---------------------------------------------------------------------------- the scan
def norm(text: str) -> str:
    return " ".join(text.split())


def sites_in(src: Source, helpers: Corpus):
    """Yield every site in one file: dict(file, line, fn, expr, verdict, sinks, why)."""
    if SKIP_PATH.search(src.path):
        return
    code = src.code
    found = []
    taken = set()

    def add_site(m, named_only: bool) -> None:
        rest = OPERAND.match(code, m.end())
        if not rest or not rva_shaped(rest.group("operand")):
            return
        if named_only and rest.group("operand").startswith("0x"):
            return  # a hex literal is an rva only next to a known base; elsewhere it is an offset
        start = m.start("base")
        if m.group("op").startswith("."):
            end = close_of(code, m.end("op") - 1) + 1
        else:
            end = expr_end(code, m.end())
        taken.add(m.start("op"))
        found.append(("add", start, end, norm(rest.group("operand")), None))

    for m in ADD.finditer(code):
        add_site(m, named_only=False)
    for m in ADD_ANY.finditer(code):
        if m.start("op") not in taken:
            add_site(m, named_only=True)
    for m in FOR_HOOK.finditer(code):
        if re.search(r"\bfn\s+$", code[: m.start()]):
            continue
        end = close_of(code, m.end() - 1) + 1
        if code[end : end + 1] == "?":
            end += 1
        found.append(("for_hook", m.start(), end, "", None))
    # The floor: a named rva added where no site above starts, because its left side is a call, a
    # parenthesised sum or a `+=` that the patterns cannot read as a base.
    spans = [(start, end) for _kind, start, end, _operand, _expr in found]
    for m in FLOOR.finditer(code):
        op = m.start("op")
        if not rva_shaped(m.group("operand")) or any(start <= op < end for start, end in spans):
            continue
        line_start = src.raw.rfind("\n", 0, op) + 1
        found.append(("unseen", op, m.end("operand"), norm(m.group("operand")), norm(src.raw[line_start : m.end("operand")])))
    for kind, start, end, operand, expr in found:
        if src.in_test(start):
            continue
        fn = src.enclosing_fn(start)
        fn_name = fn[3] if fn else "<item>"
        expr = expr or norm(src.raw[start:end])
        row = {"file": src.path, "line": src.lineno(start), "fn": fn_name, "expr": expr}
        fn_code = code[fn[0] : fn[2]] if fn else code
        if FOREIGN_BASE.search(fn_code):
            yield {**row, "verdict": "skip", "why": "another module's base"}
            continue
        if kind == "unseen":
            yield {**row, "verdict": "finding", "sinks": ["UNSEEN"], "why": "a named rva added to something the scan cannot read as a base"}
            continue
        origin = operand_origin(src, fn, operand, start, helpers) if kind == "add" else "constant"
        if origin == "self":
            yield {**row, "verdict": "skip", "why": "operand read from the image's own headers"}
            continue
        if kind == "for_hook":
            sinks = _for_hook_sinks(src, start, end, helpers)
        else:
            sinks = classify_use(src, start, end, helpers)
        bad = sorted(sinks - BENIGN)
        if not bad:
            yield {**row, "verdict": "pass", "why": "gated: " + ",".join(sorted(sinks)) if sinks else "unused"}
            continue
        if origin == "gate":
            yield {**row, "verdict": "pass", "why": "derived from a gate", "sinks": bad}
            continue
        guard = guarded(src, fn, start, helpers)
        if guard:
            yield {**row, "verdict": "pass", "why": f"guarded by {guard}", "sinks": bad}
            continue
        yield {**row, "verdict": "finding", "sinks": bad}


def _for_hook_sinks(src: Source, start: int, end: int, helpers: Corpus) -> set:
    """Where a `game_rva_for_hook(..)` result goes. Its binding forms: `let Ok(a) = ..`,
    `let a = ..?`, `let a = match .. { Ok(x) => x, .. }`, `match .. { Ok(a) => .. }`."""
    code = src.code
    groups, stmt_start = heads(code, start)
    prefix = code[stmt_start:start]
    let_ok = re.search(r"\b(?:let|if\s+let)\s+Ok\s*\(\s*([a-z_]\w*)\s*\)\s*=\s*(?:unsafe\s*\{\s*)?(?:[\w]+::)*$", prefix)
    if let_ok:
        return _follow(src, let_ok.group(1), end, helpers, 0)
    let_match = re.search(r"\blet\s+(?:mut\s+)?([a-z_]\w*)\s*(?::[^=]+)?=\s*match\s+$", prefix)
    if let_match:
        brace = code.find("{", end)
        # The arms (`Ok(a) => a`) hand the value over; the binding's uses start after them.
        return _follow(src, let_match.group(1), close_of(code, brace) if brace >= 0 else end, helpers, 0)
    if re.search(r"\bmatch\s+$", prefix):
        arm = re.match(r"\s*\{\s*Ok\s*\(\s*([a-z_]\w*)\s*\)\s*=>", code[end:])
        if arm:
            return _follow(src, arm.group(1), end + arm.end(), helpers, 0)
    return classify_use(src, start, end, helpers)


def load_sources(paths):
    out = []
    for path in paths:
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        out.append(Source(os.path.relpath(path, REPO) if os.path.isabs(path) else path, raw))
    return out


def scan_sources(sources):
    helpers = Corpus(sources)
    rows = []
    for src in sources:
        rows.extend(sites_in(src, helpers))
    return rows


def corpus_paths():
    return sorted(glob.glob(os.path.join(REPO, "crates", "**", "*.rs"), recursive=True))


# ---------------------------------------------------------------------------- er-hook still gates
def api_gating(sources) -> dict:
    """`{hook API: does its body still reach resolve_detour_address}`, read from er-hook itself.

    A hook API on `HOOK_APIS` licenses every site handed to it, so the licence is re-earned from
    the definitions on every run: delete `resolve_target` out of `MhHook::new` and every site that
    relied on it becomes a finding.
    """
    bodies: dict = {}
    for src in sources:
        if not src.path.replace("\\", "/").startswith("crates/er-hook/"):
            continue
        for start, body, end, name in src.fns:
            if src.in_test(start):
                continue
            text = src.code[body:end]
            if name == "new":
                impl = src.code.rfind("impl MhHook", 0, start)
                if impl < 0 or src._block_end(src.code.find("{", impl)) < start:
                    continue
                name = "MhHook::new"
            bodies[name] = text
    reach = {"resolve_target": "resolve_detour_address(" in bodies.get("resolve_target", "")}
    names = sorted(HOOK_APIS | HOOK_METHODS)
    changed = True
    while changed:
        changed = False
        for name in names:
            if reach.get(name):
                continue
            text = bodies.get(name, "")
            calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", text))
            if any(reach.get(c) for c in calls):
                reach[name] = True
                changed = True
    return {name: bool(reach.get(name)) for name in names}


# ---------------------------------------------------------------------------- the gate names still gate
def gate_proof(sources, names) -> tuple:
    """`({name: does it reach is_supported_build}, [(path, name) of shadowing definitions])`.

    Read from `crates/er-game-base`, which defines what `GATE_FNS` and `GUARD_FNS` name. A name is
    proven when some non-test definition of it calls, directly or through other er-game-base
    functions, `is_supported_build`, and that root still compares `game_file_version()` against
    `SUPPORTED_FILE_VERSION`. A name with no definition there (`copy_nonoverlapping`) or whose body
    never asks (`game_rva_for_hook`, `safe_read_u8`) is not proven, so putting it on a list makes
    the gate red rather than licensing every site handed to it.

    A definition of one of `names` in another crate is a shadow: the scan matches calls by name,
    so it would take a call to the local function for the real gate. A shadow must call a proven
    name itself (er-save-loader's `game_rva` calls `resolve_game_address`).
    """
    call_rx = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
    calls: dict = {}
    outside = []
    root = False
    for src in sources:
        in_base = src.path.replace("\\", "/").startswith("crates/er-game-base/")
        for start, body, end, name in src.fns:
            if src.in_test(start):
                continue
            text = src.code[body:end]
            if in_base:
                calls.setdefault(name, set()).update(call_rx.findall(text))
                if name == BUILD_CHECK and all(re.search(rx, text) for rx in BUILD_CHECK_BODY):
                    root = True
            elif name in names:
                outside.append((src.path, name, set(call_rx.findall(text))))
    reach = {BUILD_CHECK: root}
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if not reach.get(name) and any(reach.get(c) for c in called):
                reach[name] = True
                changed = True
    proof = {name: bool(reach.get(name)) for name in sorted(names)}
    shadows = [(path, name) for path, name, called in outside if not any(reach.get(c) for c in called)]
    return proof, shadows


# ---------------------------------------------------------------------------- the ratchet
def key_of(row) -> tuple:
    return (row["file"], row["fn"], row["expr"])


def compare(findings, baseline_rows):
    """(new, stale, changed, unreasoned) against the baseline rows."""
    current: dict = {}
    for row in findings:
        entry = current.setdefault(key_of(row), {"count": 0, "sinks": set(), "lines": []})
        entry["count"] += 1
        entry["sinks"] |= set(row["sinks"])
        entry["lines"].append(row["line"])
    recorded = {}
    unreasoned = []
    for row in baseline_rows:
        recorded[key_of(row)] = row
        if not str(row.get("reason", "")).strip() or str(row.get("reason")).strip().upper().startswith("TODO"):
            unreasoned.append(row)
    new, stale, changed = [], [], []
    for key, entry in sorted(current.items()):
        row = recorded.get(key)
        if row is None or entry["count"] > int(row.get("count", 1)):
            new.append((key, entry))
        elif sorted(entry["sinks"]) != sorted(row.get("sinks", [])):
            changed.append((key, entry, row))
    for key, row in sorted(recorded.items()):
        entry = current.get(key)
        if entry is None or entry["count"] < int(row.get("count", 1)):
            stale.append((key, row))
    return new, stale, changed, unreasoned


def load_baseline(path=BASELINE):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["sites"]


# ---------------------------------------------------------------------------- selftest
POSITIVES = {
    "CALL": '''
fn call(base: usize) {
    let entry = base + FOO_RVA;
    let f: unsafe extern "system" fn() = unsafe { std::mem::transmute(entry) };
    unsafe { f() };
}
''',
    "READ": '''
fn read(base: usize, abi: &Abi) -> Option<u8> {
    unsafe { er_game_base::mem::safe_read_u8(base + abi.flag_rva) }
}
''',
    "WRITE": '''
fn write(base: usize) {
    unsafe { *((base + BAR_RVA) as *mut u8) = 1 };
}
''',
    "HOOK": '''
fn pdata(base: usize) {
    let _ = unsafe { er_hook::register_union_hook_runtime_derived(base + C_RVA, handler, &ORIG) };
}
''',
    "READ via tuple": '''
fn tuple(base: usize) {
    for (name, target) in [("a", base + A_RVA), ("b", base + B_RVA)] {
        let mut bytes = [0u8; 4];
        unsafe { read_bytes(target, &mut bytes) };
    }
}
''',
    "CALL via game_rva_for_hook": '''
fn for_hook() {
    let Ok(addr) = game_rva_for_hook(X_RVA as u32) else {
        return;
    };
    let f: unsafe extern "system" fn() = unsafe { std::mem::transmute(addr) };
}
''',
    "READ in the refusal branch": '''
fn refusal(base: usize) {
    let Some(address) = resolve_detour_address(base + S_RVA, "s") else {
        let mut b = [0u8; 1];
        unsafe { read_bytes(base + S_RVA, &mut b) };
        return;
    };
}
''',
    "STORE": '''
fn store(base: usize) {
    SLOT.store(base + D_RVA, Ordering::Release);
}
''',
    "RETURN": '''
fn ret(base: usize) -> usize {
    base + FOO_RVA
}
''',
    "ARG": '''
fn arg(base: usize) {
    unknown_sink(base + FOO_RVA);
}
''',
    "OTHER": '''
fn other(base: usize, ready: bool) -> usize {
    let target = if ready { base + FOO_RVA } else { 0 };
    target
}
''',
    "UNSEEN rva added to a call's result": '''
fn unseen() {
    let entry = image_start() + FOO_RVA;
    unsafe { read_bytes(entry, &mut [0u8; 4]) };
}
''',
    "CALL from an rva added to a base of any name": '''
fn any_name() {
    let Some((start, _end)) = game_image_range() else { return };
    let f: fn() = unsafe { std::mem::transmute(start + RUN_RVA) };
}
''',
    "CALL after a check whose block does not leave": '''
fn no_exit(base: usize) {
    if !is_supported_build() {
        log(format_args!("unsupported"));
    }
    let f: fn() = unsafe { std::mem::transmute(base + FOO_RVA) };
}
''',
    "CALL after a check kept in a local": '''
fn kept(base: usize) {
    let unsupported = !is_supported_build();
    let f: fn() = unsafe { std::mem::transmute(base + FOO_RVA) };
}
''',
    "WRITE after a discarded then": '''
fn discarded(base: usize) {
    is_supported_build().then(|| ());
    unsafe { *((base + BAR_RVA) as *mut u8) = 1 };
}
''',
    "CALL in the refusal branch of an if": '''
fn inside(base: usize) {
    if !is_supported_build() {
        let f: fn() = unsafe { std::mem::transmute(base + FOO_RVA) };
        return;
    }
}
''',
    "READ after a refusal that only leaves a closure": '''
fn closure_only(base: usize) {
    let check = || {
        if !is_supported_build() {
            return;
        }
    };
    check();
    unsafe { read_bytes(base + FOO_RVA, &mut [0u8; 4]) };
}
''',
    "READ after a continue, outside its loop": '''
fn after_loop(base: usize) {
    for _ in 0..1 {
        if !is_supported_build() {
            continue;
        }
    }
    unsafe { read_bytes(base + FOO_RVA, &mut [0u8; 4]) };
}
''',
    "CALL on a gate result unwrapped with a default": '''
fn unwrapped(base: usize) {
    let rva = is_supported_build().then_some(G_RVA).unwrap_or(G_RVA);
    let f: fn() = unsafe { std::mem::transmute(base + rva) };
}
''',
    "CALL after a `mod tests;` declaration": '''
#[cfg(test)]
mod tests;
fn install(base: usize) {
    let f: fn() = unsafe { std::mem::transmute(base + FOO_RVA) };
}
''',
    "WRITE of an rva parsed from bytes": '''
fn parsed(base: usize, buf: [u8; 4]) {
    let rva = u32::from_le_bytes(buf) as usize;
    unsafe { write_code_byte(base + rva, 0xcc) };
}
''',
    "READ of an rva from a helper named like a section walk": '''
fn sectioned(base: usize) {
    let rva = section_relative(FOO_RVA);
    unsafe { read_bytes(base + rva, &mut [0u8; 4]) };
}
''',
}
NEGATIVES = {
    "inline gate": '''
fn inline(base: usize) -> Option<usize> {
    er_game_base::game_build::resolve_game_address(base + FOO_RVA, "foo")
}
''',
    "hook flow": '''
fn hook(base: usize) {
    let target = base + FOO_RVA;
    let _ = unsafe { MhHook::new(target as *mut c_void, detour as *mut c_void) };
    log(format_args!("hooked 0x{target:x}"));
}
''',
    "tuple into a resolving hook": '''
fn tuple_hook(base: usize) {
    let targets = [("a", base + A_RVA, h as UnionFn), ("b", base + B_RVA, h as UnionFn)];
    for (name, target, handler) in targets {
        let _ = unsafe { register_shared_hook(target, handler, &ORIG) };
    }
}
''',
    "guarded": '''
fn guarded(base: usize) {
    if !is_supported_build() {
        return;
    }
    let entry = base + FOO_RVA;
    unsafe { read_bytes(entry, &mut [0u8; 4]) };
}
''',
    "derived": '''
fn derived(base: usize) -> Option<usize> {
    resolve_call_site_rva(F_RVA, 0x10, "f").map(|rva| base + rva)
}
''',
    "then_some": '''
fn then_some() -> Option<usize> {
    let rva = is_supported_build().then_some(G_RVA)?;
    let base = game_module_base().ok()?;
    let entry = base + rva;
    unsafe { er_hook::register_union_hook_runtime_derived(entry, h, &O) };
    None
}
''',
    "foreign module": '''
fn foreign() -> bool {
    let Some(base) = ersc_module_base() else { return false };
    prologue_matches(base + abi.show_rva, abi.show_prologue)
}
''',
    "self-describing": '''
unsafe fn walk(image_base: usize, info: usize) -> Option<u32> {
    let array_rva = unsafe { safe_read_u32(info + THROW_INFO_CATCHABLE_ARRAY_OFFSET) }?;
    let array = image_base + array_rva as usize;
    unsafe { safe_read_u32(array) }
}
''',
    "comparison": '''
fn plausible(base: usize, a: usize) -> bool {
    a >= base && a < base + 0x0800_0000
}
''',
    "log only": '''
fn log_only(base: usize) {
    log(format_args!("at 0x{:x}", base + FOO_RVA));
}
''',
    "test code": '''
#[cfg(test)]
mod tests {
    fn t(base: usize) {
        let f: fn() = unsafe { std::mem::transmute(base + FOO_RVA) };
    }
}
''',
    "game_rva_for_hook into a hook": '''
fn for_hook_ok() {
    let Ok(addr) = game_rva_for_hook(X_RVA as u32) else {
        return;
    };
    let _ = unsafe { MhHook::new(addr as *mut c_void, d as *mut c_void) };
}
''',
    "local hook helper": '''
unsafe fn helper_hook(name: &str, target: *mut c_void, d: *mut c_void) {
    let _ = unsafe { MhHook::new(target, d) };
}
fn uses_helper(base: usize) {
    unsafe { helper_hook("x", (base + Z_RVA) as *mut c_void, d as *mut c_void) };
}
''',
    "guarded by return Err": '''
fn err_guard(base: usize) -> Result<(), String> {
    if !is_supported_build() {
        return Err(describe_build());
    }
    unsafe { read_bytes(base + FOO_RVA, &mut [0u8; 4]) };
    Ok(())
}
''',
    "guarded by continue in the loop": '''
fn loop_guard(base: usize) {
    for spec in SPECS {
        if !is_supported_build() {
            continue;
        }
        unsafe { read_bytes(base + spec.rva, &mut [0u8; 4]) };
    }
}
''',
    "guarded by a then_some chain": '''
fn chain_guard(base: usize) -> Option<()> {
    is_supported_build().then_some(()).ok_or("unsupported").ok()?;
    unsafe { read_bytes(base + FOO_RVA, &mut [0u8; 4]) };
    Some(())
}
''',
    "inside a positive check": '''
fn positive(base: usize) {
    if is_supported_build() {
        unsafe { read_bytes(base + FOO_RVA, &mut [0u8; 4]) };
    }
}
''',
    "derived through let else": '''
fn let_else() {
    let Some(rva) = is_supported_build().then_some(G_RVA) else {
        return;
    };
    let base = game_module_base().unwrap_or(0);
    let f: fn() = unsafe { std::mem::transmute(base + rva) };
}
''',
    "PE walker": '''
unsafe fn iat(base: usize) -> usize {
    let Some((rva, _size)) = (unsafe { pe_iat_directory(base) }) else { return 0 };
    unsafe { safe_read_u64(base + rva) }.unwrap_or(0) as usize
}
''',
    "PE walker through a binding": '''
unsafe fn imports(base: usize) {
    let Some(imports) = (unsafe { pe_import_descriptors(base) }) else { return };
    for (name_rva, _ilt, _iat) in imports {
        let _ = unsafe { read_c_string(base + name_rva, 64) };
    }
}
''',
    "any-name base into a resolving hook": '''
fn any_base() {
    let Some((start, _end)) = game_image_range() else { return };
    let run = start + RUN_RVA;
    let _ = unsafe { register_shared_hook(run, h, &O) };
}
''',
    "an offset in a module named rva": '''
fn offset(menu_man: usize) -> usize {
    unsafe { safe_read_usize(menu_man + er_game_base::rva::CS_MENU_MAN_MENU_DATA_OFFSET) }.unwrap_or(0)
}
''',
    "test code after a test declaration": '''
#[cfg(test)]
use super::*;
#[cfg(test)]
mod tests {
    fn t(base: usize) {
        let f: fn() = unsafe { std::mem::transmute(base + FOO_RVA) };
    }
}
''',
}
# Every sink class has a planted positive, and the counts are pinned from below so that deleting a
# positive or a negative fails the selftest rather than quietly narrowing it.
SINK_CLASSES = {"CALL", "READ", "WRITE", "HOOK", "STORE", "RETURN", "ARG", "OTHER", "UNSEEN"}
MIN_POSITIVES = 23
MIN_NEGATIVES = 23
# Names that are not gates. `gate_proof` must refuse each one, or the proof licenses anything.
NOT_GATES = {"write_code_byte", "copy_nonoverlapping", "transmute", "game_rva_for_hook", "safe_read_u8", "read_bytes"}
# The ratchet's own red/green: a baseline that lists the planted finding passes it; one that
# misses it, carries a stale row or a row without a reason fails.
RATCHET = '''
fn ratchet(base: usize) {
    let f: fn() = unsafe { std::mem::transmute(base + R_RVA) };
}
'''


def selftest() -> int:
    failed = []

    def scan_text(text, name="crates/selftest/src/lib.rs"):
        return scan_sources([Source(name, text)])

    for label, text in POSITIVES.items():
        rows = [r for r in scan_text(text) if r["verdict"] == "finding"]
        want = label.split()[0]
        hit = any(s == want or s.startswith(want + ":") for r in rows for s in r["sinks"])
        print(f"  positive {label:48} -> {'FIRES' if hit else 'DID NOT FIRE'} {[r['sinks'] for r in rows]}")
        if not hit:
            failed.append(label)
    for label, text in NEGATIVES.items():
        rows = [r for r in scan_text(text) if r["verdict"] == "finding"]
        print(f"  negative {label:48} -> {'clean' if not rows else f'FALSE POSITIVE {rows}'}")
        if rows:
            failed.append("negative:" + label)
    covered = {label.split()[0] for label in POSITIVES}
    uncovered = sorted(SINK_CLASSES - covered)
    print(f"  coverage sink classes planted  -> {len(SINK_CLASSES) - len(uncovered)}/{len(SINK_CLASSES)}" + (f", none for {uncovered}" if uncovered else ""))
    if uncovered:
        failed.append(f"sink classes with no planted positive: {uncovered}")
    print(f"  coverage planted cases         -> {len(POSITIVES)} positives (at least {MIN_POSITIVES}), {len(NEGATIVES)} negatives (at least {MIN_NEGATIVES})")
    if len(POSITIVES) < MIN_POSITIVES or len(NEGATIVES) < MIN_NEGATIVES:
        failed.append("a planted positive or negative was deleted")

    findings = [r for r in scan_text(RATCHET) if r["verdict"] == "finding"]
    row = {"file": "crates/selftest/src/lib.rs", "fn": "ratchet", "expr": "base + R_RVA", "sinks": ["CALL"], "count": 1, "reason": "planted"}
    checks = {
        "listed finding passes": compare(findings, [row]) == ([], [], [], []),
        "unlisted finding fails": bool(compare(findings, [])[0]),
        "stale row fails": bool(compare([], [row])[1]),
        "row without a reason fails": bool(compare(findings, [{**row, "reason": ""}])[3]),
        "changed sinks fail": bool(compare(findings, [{**row, "sinks": ["READ"]}])[2]),
    }
    for label, ok in checks.items():
        print(f"  ratchet  {label:30} -> {'ok' if ok else 'BROKEN'}")
        if not ok:
            failed.append("ratchet:" + label)

    # er-hook still gates, and taking the gate out of `MhHook::new` is seen.
    corpus = load_sources(corpus_paths())
    gating = api_gating(corpus)
    lost = [name for name, ok in gating.items() if not ok]
    print(f"  er-hook  resolving hook APIs   -> {len(gating) - len(lost)}/{len(gating)} reach resolve_detour_address" + (f", NOT {lost}" if lost else ""))
    if lost:
        failed.append(f"hook-apis-lost-their-gate:{lost}")
    hook = next((s for s in corpus if s.path == "crates/er-hook/src/lib.rs"), None)
    if hook is None:
        failed.append("er-hook source not found")
    else:
        broken = Source(hook.path, re.sub(r"resolve_detour_address\s*\(", "resolve_nothing(", hook.raw))
        others = [s for s in corpus if s.path != hook.path]
        degraded = [n for n, ok in api_gating(others + [broken]).items() if not ok]
        print(f"  er-hook  without resolve_detour_address -> {len(degraded)} API(s) lose the licence")
        if len(degraded) != len(gating):
            failed.append("api-gating-vacuous")

    # The gate names still gate, a name that does not is refused, and so is a local shadow.
    names = GATE_FNS | GUARD_FNS
    proof, shadows = gate_proof(corpus, names)
    unproven = [name for name, ok in proof.items() if not ok]
    print(f"  er-game-base gate names        -> {len(proof) - len(unproven)}/{len(proof)} reach is_supported_build" + (f", NOT {unproven}" if unproven else ""))
    if unproven:
        failed.append(f"gate-names-not-proven:{unproven}")
    if shadows:
        failed.append(f"gate-names-shadowed:{shadows}")
    planted, _ = gate_proof(corpus, names | NOT_GATES)
    accepted = sorted(name for name in NOT_GATES if planted[name])
    print(f"  er-game-base planted non-gates -> {len(NOT_GATES) - len(accepted)}/{len(NOT_GATES)} refused" + (f", ACCEPTED {accepted}" if accepted else ""))
    if accepted:
        failed.append(f"gate-proof-accepts-non-gates:{accepted}")
    build = next((s for s in corpus if s.path == "crates/er-game-base/src/game_build.rs"), None)
    if build is None:
        failed.append("er-game-base game_build.rs not found")
    else:
        rest = [s for s in corpus if s.path != build.path]
        unasked = Source(build.path, re.sub(r"(?<!fn )\bis_supported_build\s*\(\s*\)", "true", build.raw))
        lost = [n for n, ok in gate_proof(rest + [unasked], names)[0].items() if not ok]
        gutted = Source(build.path, re.sub(r"\bgame_file_version\s*\(\s*\)\s*==\s*Some\s*\(\s*SUPPORTED_FILE_VERSION\s*\)", "true", build.raw))
        lost_root = [n for n, ok in gate_proof(rest + [gutted], names)[0].items() if not ok]
        print(f"  er-game-base nobody asks the check -> {len(lost)}/{len(names)} lose the licence; a gutted check -> {len(lost_root)}/{len(names)}")
        if len(lost) != len(names) or len(lost_root) != len(names):
            failed.append("gate-proof-vacuous")
    shadow = Source("crates/selftest/src/lib.rs", "pub fn game_rva(base: usize, rva: u32) -> usize {\n    base + rva as usize\n}\n")
    _, planted_shadows = gate_proof(corpus + [shadow], names)
    print(f"  er-game-base planted shadow    -> {'refused' if (shadow.path, 'game_rva') in planted_shadows else 'ACCEPTED'}")
    if (shadow.path, "game_rva") not in planted_shadows:
        failed.append("gate-proof-misses-a-shadow")

    # The same on real code: er-cel-shade's sky install without its build refusal is red.
    sky = next((s for s in corpus if s.path == "crates/er-cel-shade/src/sky/game.rs"), None)
    if sky is None or "if !is_supported_build() {" not in sky.raw:
        failed.append("real-tree control missing: crates/er-cel-shade/src/sky/game.rs moved or changed shape")
    else:
        stripped = Source(sky.path, sky.raw.replace("if !is_supported_build() {", "if false {"))
        red = [r for r in scan_sources([stripped]) if r["verdict"] == "finding"]
        print(f"  real     sky install without is_supported_build -> {len(red)} finding(s)")
        if len(red) < 6:
            failed.append("real-tree control did not fire")

    # Non-vacuity on the real tree: the scan must see the sites it is about.
    rows = scan_sources(corpus)
    sites = [r for r in rows if r["verdict"] != "skip"]
    adds = [r for r in sites if "game_rva_for_hook" not in r["expr"]]
    print(f"  corpus   sites seen            -> {len(adds)} hand-built, {len(sites) - len(adds)} game_rva_for_hook")
    if len(adds) < 40 or len(sites) - len(adds) < 20:
        failed.append("corpus-too-small: the matcher has stopped seeing the tree")

    if failed:
        print(f"SELFTEST FAILED: {failed}", file=sys.stderr)
        return 1
    print("audit-ungated-game-addresses selftest OK: every class fires, every pass shape is clean")
    return 0


# ---------------------------------------------------------------------------- main
def main(argv) -> int:
    if "--selftest" in argv:
        return selftest()
    corpus = load_sources(corpus_paths())
    gating = api_gating(corpus)
    lost = [name for name, ok in gating.items() if not ok]
    proof, shadows = gate_proof(corpus, GATE_FNS | GUARD_FNS)
    unproven = [name for name, ok in proof.items() if not ok]
    rows = scan_sources(corpus)
    if "--report" in argv:
        for row in sorted(rows, key=lambda r: (r["file"], r["line"])):
            extra = f" [{','.join(row.get('sinks', []))}]" if row.get("sinks") else ""
            print(f"{row['verdict']:8} {row['file']}:{row['line']} {row['fn']}: {row['expr']}{extra} -- {row.get('why', '')}")
        counts: dict = {}
        for row in rows:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        print(f"\n{len(rows)} sites: {counts}")
        return 0
    findings = [r for r in rows if r["verdict"] == "finding"]
    new, stale, changed, unreasoned = compare(findings, load_baseline())
    for key, entry in new:
        print(f"NEW      {key[0]}:{entry['lines'][0]} {key[1]}: {key[2]} -> {sorted(entry['sinks'])}")
        print(
            "         "
            + json.dumps({"file": key[0], "fn": key[1], "expr": key[2], "sinks": sorted(entry["sinks"]), "count": entry["count"], "reason": ""})
        )
    for key, entry, row in changed:
        print(f"CHANGED  {key[0]}:{entry['lines'][0]} {key[1]}: {key[2]} -> {sorted(entry['sinks'])}, baseline says {row.get('sinks')}")
    for key, row in stale:
        print(f"STALE    {key[0]} {key[1]}: {key[2]} -- the baseline row no longer matches a site; delete it")
    for row in unreasoned:
        print(f"REASON?  {row['file']} {row['fn']}: {row['expr']} -- the baseline row has no reason")
    for name in lost:
        print(f"GATE?    er-hook's {name} no longer reaches resolve_detour_address, so it licenses nothing")
    for name in unproven:
        print(f"GATE?    {name} does not reach is_supported_build in crates/er-game-base, so it licenses nothing")
    for path, name in shadows:
        print(f"GATE?    {path} defines its own {name}, which reaches no proven gate; the scan would take its calls for the real one")
    print(f"{len(rows)} sites, {len(findings)} findings, {len(load_baseline())} baseline rows")
    if new or stale or changed or unreasoned or lost or unproven or shadows:
        print(
            "\naudit-ungated-game-addresses: FAILED -- a game address built by hand reaches a call, a read "
            "or a write without the build check. On any build but 2.7.2.0 it lands on whatever moved "
            "there. Route it through er_game_base::game_build::resolve_game_address (or game_data_addr, "
            "game_rva, a resolving hook API), put an is_supported_build() refusal in front of it, or, "
            "when it is safe for a reason this audit cannot see, record that reason in "
            "scripts/audit-ungated-game-addresses.baseline.json."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
