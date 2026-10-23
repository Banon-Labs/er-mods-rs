#!/usr/bin/env python3
"""Fail when the product installs a constant detour on a game address the allowlist does not admit.

The rule this reproduces
========================
`er_game_base::game_build::resolve_detour_address` admits a game-image address for a detour only
when its rva is in `DETOUR_ALLOWLIST` (`crates/er-game-base/src/detour_allowlist.rs`), a sorted
`[u32; N]` it binary-searches. Every translating hook entry point reaches that function --
`MhHook::new`, `register_union_hook*`, `register_shared_hook*` through `er_hook::resolve_target`,
and the three crates that call `resolve_detour_address` themselves. An address that is not on the
list is refused at runtime, once per retry, with an `ADDRESS REFUSED FOR DETOUR` line, and the
feature behind it is inert.

So two facts in the tree have to agree: "this source line hands address X to a detour installer"
and "X is on the allowlist". This gate puts them next to each other and fails on every X that is
not, naming the install site. It also checks the list itself, because the resolver's answer is
only as good as the list:

  * it parses, has exactly the length its type declares, and is strictly ascending -- a row out
    of order is a row `binary_search` cannot find, so an unsorted list refuses addresses it
    appears to admit;
  * with the 2.7.2.0 image present (`eldenring-deobf-1.17.2.bin`, or `ER_DEOBF_BIN_1172`), every
    row is executable, and is either a `.pdata` function start or an unwindless leaf of at least
    five bytes that no declared function contains. A chained-unwind continuation, an address in
    the middle of a declared function, a `.data` global, or a leaf shorter than MinHook's patch is
    a row that licenses five bytes of jmp somewhere they do not belong.

Why it exists
=============
On 2026-08-30 a user played for seven minutes with the mod's loading-screen cover pasted over live
gameplay. The cause was `LOADING_SCREEN_GFX_FADEOUT_RVA`: callable, but refused for detour, 8,430
times in one session, and the only evidence was a line in a 412 MB runtime log. Both facts were in
the tree the whole time and no gate compared them. The address was a 1.16.2 constant translated
through a ledger then; it is a native 2.7.2.0 rva now and the comparison is the same one.

The installer list is derived
=============================
Which `er-hook` functions consult the allowlist is read out of `crates/er-hook/src/lib.rs` by
call-graph closure over `resolve_target` / `resolve_detour_address`, so a new entry point or a
rename is picked up without an edit here. The `*_runtime_derived` variants do not consult the list
(they audit the running image's `.pdata`) and are listed as exempt rather than dropped.

Addresses are resolved by value, not by spelling
================================================
Almost no call site passes a constant. It passes a local:

    let Ok(addr) = game_rva_for_hook(PLAYER_GAME_DATA_NAME_GETTER_RVA as u32) else { ... };
    MhHook::new(addr as *mut c_void, hook as *mut c_void)

so this walks the binding backwards inside the enclosing function -- `let`, `let ... else`,
`if let`, `match` arms, `for` tuple destructuring, closure parameters over an iterator chain
(`REGISTRY.iter().map(|guard| ..)`), plain assignment, and function parameters followed to their
callers in the same crate -- and resolves the names it lands on through `scripts/rva_symbols.py`,
which evaluates declarations to numbers and so sees enum discriminants and derived constants. A
table of records is read through its `rva:` fields, one level into named record constants, and a
name is looked up where the code that wrote it can see it: the same file, then the same crate,
narrowed by the module a `use` imports it from.

Usage
    python3 scripts/check-detour-rva-coverage.py            # the gate
    python3 scripts/check-detour-rva-coverage.py --list     # every site it found
    python3 scripts/check-detour-rva-coverage.py --selftest
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
CRATES = os.path.join(ROOT, "crates")
ALLOWLIST_RS = os.path.join(CRATES, "er-game-base", "src", "detour_allowlist.rs")
HOOK_RS = os.path.join(CRATES, "er-hook", "src", "lib.rs")
IMAGE_BASE = 0x140000000
# The image the allowlist was measured on, and the override `build-support/prologue_build.rs`
# reads for the same image.
IMAGE_ENV = "ER_DEOBF_BIN_1172"
IMAGE_NAME = "eldenring-deobf-1.17.2.bin"
# MinHook writes a five-byte relative jmp at the target.
DETOUR_PATCH_BYTES = 5

sys.path.insert(0, SCRIPTS)
import er_game_image  # noqa: E402  (path set above)
import rva_symbols  # noqa: E402  (path set above)

# ---------------------------------------------------------------------------------------------
# Floors. A matcher that goes blind reports zero findings, which is indistinguishable from a
# clean tree. These are lower bounds on what the scan must still see, not targets: deleting hooks
# legitimately lowers them, and lowering one is a reviewable one-line diff. A refactor that makes
# the scan blind cannot do it silently.
MIN_DETOUR_SITES = 100
MIN_RESOLVED_SITES = 82
MIN_DISTINCT_RVAS = 135
# 535 rows when the list was generated on 2026-10-23. An allowlist that shrank to nothing would
# refuse every detour while this gate reported each site as broken -- loud, but for the wrong
# reason -- so a collapse of the list is named as such.
MIN_ALLOWLIST_ROWS = 500

# Below the first page of the image nothing is a game address.
MIN_PLAUSIBLE_RVA = 0x1000
# `.text` ends far below this. A larger number in an address expression is a plausibility bound or
# a size, not a function.
MAX_PLAUSIBLE_RVA = 0x8000000
# How many binding hops the backward slice will take before giving up. Deep enough for the
# `for (name, target, ..) in [..]` tables and one interprocedural hop; shallow enough that a
# mutually-recursive helper cannot spin.
MAX_SLICE_DEPTH = 6
# How many callers of a wrapper function to follow. A helper called from fifty places is not a
# hook site, it is a utility, and following all of them multiplies noise rather than evidence.
MAX_CALLERS_FOLLOWED = 12
# `resolve_detour_address` is a translating entry point in its own right: three crates hand it an
# address and keep the answer for a later install. Its definition lives in `er-game-base`, whose
# bodies, like `er-hook`'s, are not install sites.
DIRECT_RESOLVER = "resolve_detour_address"
DEFINING_CRATES = ("er-hook", "er-game-base")


# ---------------------------------------------------------------------------------------------
# Source text
# ---------------------------------------------------------------------------------------------


def rust_sources(root=CRATES):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "target"]
        out.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".rs"))
    return sorted(out)


def read(path):
    return open(path, encoding="utf-8", errors="replace").read()


# ---------------------------------------------------------------------------------------------
# The allowlist, read out of er-game-base
# ---------------------------------------------------------------------------------------------

ALLOWLIST_DECL = re.compile(
    r"\bconst\s+DETOUR_ALLOWLIST\s*:\s*\[\s*u32\s*;\s*(\d+)\s*\]\s*=\s*\[(.*?)\]\s*;", re.S
)
HEX_ROW = re.compile(r"0[xX][0-9a-fA-F_]+")


class AllowlistError(RuntimeError):
    """`detour_allowlist.rs` did not yield a list the resolver could search correctly."""


def read_allowlist(source=None, path=ALLOWLIST_RS):
    """The rows of `DETOUR_ALLOWLIST`, in file order, after proving they are searchable.

    Raises rather than returning a partial list. An empty or unparsable list is a parse failure,
    not a permissive default; a declared length that disagrees with the rows is a file rustc would
    reject or, worse, one this reader misread; and an out-of-order or repeated row is one that
    `binary_search` may not find, which is a refusal the file appears not to make.
    """
    text = read(path) if source is None else source
    found = ALLOWLIST_DECL.search(rva_symbols.code_only(text))
    if found is None:
        raise AllowlistError(
            f"{os.path.relpath(path, ROOT)} declares no `const DETOUR_ALLOWLIST: [u32; N] = [..];`. "
            "This gate reproduces the resolver's lookup in that list; it cannot guess at it."
        )
    declared, body = int(found.group(1)), found.group(2)
    rows = [int(token.replace("_", ""), 16) for token in HEX_ROW.findall(body)]
    leftover = re.sub(r"\s|,", "", HEX_ROW.sub("", body))
    if leftover:
        raise AllowlistError(
            f"DETOUR_ALLOWLIST holds something other than hex literals ({leftover[:60]!r}); the "
            "rows this gate reads would not be the rows the resolver searches"
        )
    if not rows:
        raise AllowlistError("DETOUR_ALLOWLIST is empty: every constant detour would be refused")
    if len(rows) != declared:
        raise AllowlistError(
            f"DETOUR_ALLOWLIST declares {declared} rows and lists {len(rows)}; the reader and the "
            "compiler disagree about what the list is"
        )
    disorder = [(prev, cur) for prev, cur in zip(rows, rows[1:]) if cur <= prev]
    if disorder:
        shown = ", ".join(f"0x{a:x} then 0x{b:x}" for a, b in disorder[:4])
        raise AllowlistError(
            f"DETOUR_ALLOWLIST is not strictly ascending ({shown}). The resolver binary-searches "
            "it, so a row out of order is a row it may not find: the address is refused while the "
            "list appears to admit it"
        )
    return rows


# ---------------------------------------------------------------------------------------------
# The allowlist against the 2.7.2.0 image
# ---------------------------------------------------------------------------------------------


def image_path():
    """The 2.7.2.0 flat image as `scripts/er_game_image.py` locates it, or a path that is absent.

    `ER_DEOBF_BIN_1172`, else the repo copy, else the copy beside an ancestor directory, which is
    where a linked worktree under the main checkout finds the gitignored image. The absent path is
    returned rather than `None` so the skip line can name what was looked for.
    """
    found = er_game_image.image_path()
    if found is not None:
        return str(found)
    return os.environ.get(IMAGE_ENV) or os.path.join(ROOT, IMAGE_NAME)


class ImageFacts:
    """What the 2.7.2.0 image declares about where functions are, read once."""

    def __init__(self, blob):
        import function_extent  # noqa: PLC0415 - imported late so the source half needs no image

        self.extent = function_extent
        self.blob = blob
        self.sections = function_extent.sections(blob)
        _extents, self.starts, self.spans = function_extent.declared_functions(blob)
        self.chained = function_extent.chained_begins(blob)

    def leaf_end(self, rva):
        return self.extent.leaf_extent(self.blob, IMAGE_BASE + rva, self.starts)


def row_problem(rva, facts, leaf_end=None):
    """Why `rva` may not carry a detour on 2.7.2.0, or `None` when it may.

    `leaf_end` defaults to the image's leaf sweep and is injectable so the selftest can plant a
    leaf too short to patch without needing one to exist in the image.
    """
    name, executable = facts.extent.classify(facts.sections, IMAGE_BASE + rva)
    if not executable:
        return f"lies in {name}, which is not executable -- a global or a table, never a function"
    if rva in facts.starts:
        return None
    if rva in facts.chained:
        return (
            "is a chained-unwind continuation record: the middle of a function whose prologue "
            "MinHook would never see"
        )
    enclosing = facts.extent.inside_declared_function(rva, facts.spans)
    if enclosing is not None:
        return (
            f"is 0x{rva - enclosing[0]:x} bytes into the declared function "
            f"0x{enclosing[0]:x}..0x{enclosing[1]:x}, not at its entry"
        )
    end = (leaf_end or facts.leaf_end)(rva)
    if end is None:
        return "is in no declared function and decodes to no leaf terminator"
    if end - rva < DETOUR_PATCH_BYTES:
        return (
            f"is a {end - rva}-byte leaf; MinHook's patch is {DETOUR_PATCH_BYTES} bytes and would "
            "run into whatever follows it"
        )
    return None


def image_findings(rows, path=None):
    """`(findings, note)` for the allowlist against the image; findings are `(rva, why)`.

    An absent image skips, saying so, in a line that does not claim the rows were checked.
    """
    path = path or image_path()
    if not os.path.isfile(path):
        return [], (
            f"skipped the image half: {path} is absent (gitignored, game-derived); "
            f"{len(rows)} allowlist row(s) went unchecked against the 2.7.2.0 .pdata"
        )
    facts = ImageFacts(open(path, "rb").read())
    out = [(rva, why) for rva in rows if (why := row_problem(rva, facts)) is not None]
    return out, f"{len(rows)} allowlist row(s) checked against {os.path.basename(path)}"


# ---------------------------------------------------------------------------------------------
# The installers, read out of er-hook
# ---------------------------------------------------------------------------------------------

FN_HEAD = re.compile(r"\bfn\s+([A-Za-z_]\w*)\s*(?:<[^<>{}();]*>)?\s*\(")
IMPL_HEAD = re.compile(r"\bimpl\s+(?:<[^<>]*>\s*)?([A-Za-z_]\w*)")


def _matching(text, index, opener="{", closer="}"):
    depth = 0
    while index < len(text):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def function_bodies(code):
    """`[(name, head_start, body_start, body_end)]` for every `fn` with a block body."""
    out = []
    for match in FN_HEAD.finditer(code):
        args_end = _matching(code, match.end() - 1, "(", ")")
        # Which comes first at depth zero, `{` or `;`. A plain `code.find(";")` reads the `;`
        # inside an array return type -- `fn observer_hooks() -> [ObserverHook; 5] {` -- decides
        # the function is a bodyless declaration, and drops it. That one miss cost the whole
        # loading-screen observer table: five detours, including the address that put an opaque
        # cover over seven minutes of live gameplay.
        # Only `(` and `[` are counted, not angle brackets: `->` is a `>` with no `<`, so counting
        # them drives the depth negative on every function that returns anything, and the array
        # `;` then reads at depth 0 again -- the same miss wearing a different mask.
        depth, i, brace = 0, args_end + 1, -1  # Past the closing `)`, or it counts as a `)`
        while i < len(code):
            c = code[i]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif depth <= 0 and c == "{":
                brace = i
                break
            elif depth <= 0 and c == ";":
                break
            i += 1
        if brace < 0:
            continue  # a trait/extern declaration, no body
        out.append((match.group(1), match.start(), brace, _matching(code, brace)))
    return out


def installer_spellings(source=None, path=HOOK_RS):
    """`(translating, runtime_derived)` -- how a caller spells each er-hook detour entry point.

    Derived by call-graph closure over the functions that consult the allowlist, so a renamed or
    newly added entry point is picked up without editing this file. `MhHook::new` comes back with
    its type prefix because that is how its call sites write it.

    The closure propagates through free functions only. A method name is not a call graph edge:
    propagating through one made `new` a token, a word-boundary call pattern on it matches
    `Vec::new(`, and the closure swallowed nineteen functions -- including both
    `*_runtime_derived` entry points, whose whole purpose is to not consult the list.
    """
    code = rva_symbols.code_only(read(path) if source is None else source)
    bodies = function_bodies(code)
    owner = {}
    for match in IMPL_HEAD.finditer(code):
        brace = code.find("{", match.end())
        if brace < 0:
            continue
        end = _matching(code, brace)
        for name, _head, body_start, _body_end in bodies:
            if brace < body_start < end:
                owner[name] = match.group(1)

    text = {name: code[start:end] for name, _h, start, end in bodies}
    seeds = {n for n, b in text.items() if re.search(r"\bresolve_(?:target|detour_address)\s*\(", b)}
    reaching = set(seeds)
    changed = True
    while changed:
        changed = False
        edges = {t for t in reaching if t not in owner}
        for name, body in text.items():
            if name in reaching:
                continue
            if any(re.search(r"(?<![\w:.])" + re.escape(t) + r"\s*\(", body) for t in edges):
                reaching.add(name)
                changed = True

    public = {
        name
        for name, head, start, _end in bodies
        if re.search(r"\bpub\b", code[head - 40 if head > 40 else 0 : start].split("fn ")[0])
    }
    derived = {n for n in public - reaching if re.search(r"\bwrite_site_is_sound\s*\(", text[n])}

    def spell(name):
        return f"{owner[name]}::{name}" if name in owner else name

    return sorted(spell(n) for n in public & reaching), sorted(spell(n) for n in derived)


# ---------------------------------------------------------------------------------------------
# Call sites, and the backward slice that turns an argument into an RVA
# ---------------------------------------------------------------------------------------------

HEX = re.compile(r"\b0[xX][0-9a-fA-F_]+\b")


def split_args(text, open_paren):
    """`([arg, ...], close_index)` for the call whose `(` is at `open_paren`."""
    depth, i, args, start = 0, open_paren, [], open_paren + 1
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i])
                return args, i
        elif c == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
        i += 1
    return None, len(text)


class Site:
    __slots__ = ("path", "line", "installer", "expr", "rvas", "note")

    def __init__(self, path, line, installer, expr):
        self.path, self.line, self.installer, self.expr = path, line, installer, expr
        self.rvas = set()
        self.note = ""

    @property
    def where(self):
        return f"{os.path.relpath(self.path, ROOT)}:{self.line}"


class Scanner:
    """Every detour install site in a tree, with the RVA each one targets where that is knowable."""

    def __init__(self, installers, derived, root=CRATES, index=None):
        self.installers = list(installers)
        self.derived = list(derived)
        self.root = root
        self.index = index if index is not None else rva_symbols.index(root)
        self.code = {}
        self.bodies = {}
        self._sources = None
        self._mentions = {}
        # `(?![\w])` after each name is what keeps `MhHook::new` from also matching
        # `MhHook::new_runtime_derived` -- the exempt entry point whose entire reason to exist is
        # that it does not go through the list. Longest-first, because alternation is
        # leftmost-first and `register_shared_hook` would otherwise shadow
        # `register_shared_hook_with_budget`.
        # `(?<![\w.])`, not `(?<![\w:.])`: half this tree calls through the module path --
        # `er_hook::register_shared_hook(base + FILE_OPEN_RVA, ..)` is er-armament-icons' only
        # shared hook -- and refusing a `::` prefix silently dropped it.
        names = sorted(self.installers, key=len, reverse=True)
        self.call = re.compile(
            r"(?<![\w.])("
            + "|".join(re.escape(n).replace(r"\:\:", r"\s*::\s*") for n in names)
            + r")(?![\w])\s*(?:::\s*<[^<>]*>\s*)?\("
        )

    # -- text ---------------------------------------------------------------------------------

    def sources(self):
        if self._sources is None:
            self._sources = rust_sources(self.root)
        return self._sources

    def mentioning(self, name):
        """Files whose code text contains `name` at all -- a cheap gate before any regex.

        Following a wrapper's callers used to run a compiled pattern over every file in the tree
        once per call, 81,000 whole-file regex scans in a run. A substring test done once per
        callee name replaces all of it.
        """
        if name not in self._mentions:
            self._mentions[name] = [p for p in self.sources() if name in self.text_of(p)]
        return self._mentions[name]

    def text_of(self, path):
        if path not in self.code:
            self.code[path] = rva_symbols.code_only(read(path))
            self.bodies[path] = function_bodies(self.code[path])
        return self.code[path]

    def enclosing(self, path, offset):
        best = None
        for name, head, start, end in self.bodies[path]:
            if start < offset < end and (best is None or start > best[2]):
                best = (name, head, start, end)
        return best

    # -- scanning -----------------------------------------------------------------------------

    def sites(self, paths=None):
        out = []
        for path in self.sources() if paths is None else paths:
            # er-hook and er-game-base define these functions; their own bodies are not sites.
            if os.path.relpath(path, self.root).split(os.sep)[0] in DEFINING_CRATES:
                continue
            code = self.text_of(path)
            for match in self.call.finditer(code):
                args, _close = split_args(code, match.end() - 1)
                if not args:
                    continue
                site = Site(
                    path,
                    code.count("\n", 0, match.start()) + 1,
                    " ".join(match.group(1).split()),
                    " ".join(args[0].split()),
                )
                self.resolve(site, code, match.start())
                out.append(site)
        return out

    # -- the backward slice -------------------------------------------------------------------

    def resolve(self, site, code, offset):
        enclosing = self.enclosing(site.path, offset)
        if enclosing is None:
            site.note = "no enclosing function"
            return
        found, notes = self._expr_rvas(
            site.expr, site.path, enclosing, 0, set(), offset - enclosing[2]
        )
        site.rvas = found
        if not found:
            site.note = notes or "unresolved"

    def _expr_rvas(self, expr, path, enclosing, depth, seen, limit):
        """RVAs an address expression can carry. Returns `(set, note)`."""
        if depth > MAX_SLICE_DEPTH:
            return set(), "slice depth exhausted"
        out, notes = set(), []
        for match in HEX.finditer(expr):
            value = int(match.group(0).replace("_", ""), 16)
            # A bare literal is only an address when it is not page-aligned: `base + 0x0800_0000`
            # is a range bound on a runtime pointer, and it entered an earlier inventory as a game
            # address. A named constant gets no such test -- the name is evidence a literal lacks.
            if MIN_PLAUSIBLE_RVA <= value <= MAX_PLAUSIBLE_RVA and value & 0xFFF:
                out.add(value)
        for match in rva_symbols.IDENTIFIER.finditer(expr):
            name = match.group(0).replace(" ", "")
            # Keyed by (name, limit), not by name. `if let Some(addr) = addr` rebinds a name to
            # itself, which is idiomatic and common in this tree; a name-only guard sees the
            # second `addr`, calls it already-visited, and abandons a chain that had one hop left.
            # The pair is still a sound cycle guard -- `limit` strictly decreases on the way back
            # through the bindings.
            if (name, limit) in seen:
                continue
            seen.add((name, limit))
            values = self.index._lookup(name, set(), path)
            plausible = {
                v for v in (values or set()) if MIN_PLAUSIBLE_RVA <= v <= MAX_PLAUSIBLE_RVA
            }
            if plausible:
                out |= plausible
                continue
            if values:
                continue  # resolves, but not to anything that could be a game address
            record = self._record_rvas(name, path) or self._table_fn_rvas(name, path)
            if record:
                out |= record
                continue
            sliced, note = self._binding_rvas(name, path, enclosing, depth, seen, limit)
            out |= sliced
            if note:
                notes.append(note)
        return out, "; ".join(notes)

    # `(?!:)` so a path through the `rva` module -- `er_game_base::rva::EZ_CHILDSTEP_RESET_RVA` --
    # is not read as a field named `rva`.
    RVA_FIELD = re.compile(r"\b\w*rva\s*:(?!:)\s*([^,\n}]+)", re.I)
    # A record constant is named like one; a function or a local in a table row is not followed.
    RECORD_NAME = re.compile(r"[A-Z][A-Z0-9_]*")

    def _rva_fields(self, text, path):
        """Every address a `rva: <expr>` field carries, literal or named.

        Both spellings are live: er-invasion-warp writes `MapSeam { rva: 0x088_55b0, .. }`;
        er-loading-portrait-core writes `ObserverHook { rva: LOADING_SCREEN_GFX_FADEOUT_RVA as u32,
        .. }` -- the address that covered a user's screen for seven minutes, so a hex-only field
        matcher would miss the case this gate was written for.
        """
        out = set()
        for match in self.RVA_FIELD.finditer(text):
            expr = match.group(1)
            for hexes in HEX.finditer(expr):
                value = int(hexes.group(0).replace("_", ""), 16)
                if MIN_PLAUSIBLE_RVA <= value <= MAX_PLAUSIBLE_RVA:
                    out.add(value)
            for ident in rva_symbols.IDENTIFIER.finditer(expr):
                values = self.index._lookup(ident.group(0).replace(" ", ""), set(), path)
                out |= {
                    v for v in (values or ()) if MIN_PLAUSIBLE_RVA <= v <= MAX_PLAUSIBLE_RVA
                }
        return out

    # -- crate and module scope ----------------------------------------------------------------

    def crate_of(self, path):
        """The crate directory name a source file belongs to, `er-reload-trace` and the like."""
        return os.path.relpath(path, self.root).split(os.sep)[0]

    @staticmethod
    def _module_of(path):
        """The innermost module a file defines: `guards` for `src/guards/mod.rs` or `guards.rs`."""
        stem = os.path.basename(path)[: -len(".rs")]
        if stem in ("mod", "lib", "main"):
            parent = os.path.basename(os.path.dirname(path))
            return None if parent == "src" else parent
        return stem

    USE_ITEM = re.compile(r"(?:^|[\s;{}])(?:pub(?:\s*\([^)]*\))?\s+)?use\s+([^;]+);", re.S)

    def _imported_module(self, name, path):
        """The module segment a `use` in `path` imports `name` from, or `None`.

        `use guards::{Guard, REGISTRY};` answers `guards`. er-seamless-bugfixes declares three
        statics named `REGISTRY` (guards, patches, mid_hooks), so the crate alone does not say
        which table `REGISTRY.iter()` walks; the import does.
        """

        def expand(prefix, body):
            head, brace, rest = body.partition("{")
            if not brace:
                yield " ".join((prefix + body).split())
                return
            for item in rva_symbols._split_top(rest.rsplit("}", 1)[0], [","]):
                if item.strip():
                    yield from expand(prefix + "".join(head.split()), item.strip())

        for match in self.USE_ITEM.finditer(self.text_of(path)):
            for full in expand("", match.group(1)):
                # `as` only as a word: `use crate::classify::X;` is not an alias of `sify::X`.
                target, *alias = re.split(r"\s+as\s+", full)
                segments = [s for s in "".join(target.split()).split("::") if s]
                if len(segments) >= 2 and (alias[0].strip() if alias else segments[-1]) == name:
                    return segments[-2]
        return None

    def _scoped(self, pool, name, path):
        """The declarations of `name` that the code in `path` can actually mean.

        The same file first, then the same crate, narrowed by the module a qualified spelling or a
        `use` names. Another crate's declaration is taken only when it is the one declaration of
        that name in the tree. The unscoped version of this attributed er-quit-menu-core's `HOOKS`
        and er-input-harness's `install_one` callers to er-reload-trace's trace table, and never
        read the table itself.
        """
        local = [d for d in pool if d.path == path]
        if local:
            return local
        crate = self.crate_of(path)
        same = [d for d in pool if self.crate_of(d.path) == crate]
        if same:
            hint = name.split("::")[-2] if "::" in name else self._imported_module(name, path)
            if hint and hint not in ("crate", "self", "super"):
                narrowed = [d for d in same if self._module_of(d.path) == hint]
                if narrowed:
                    return narrowed
            return same
        return pool if len(pool) == 1 else []

    def _table_fn_rvas(self, name, path):
        """`fn observer_hooks() -> [ObserverHook; 5] { [ .. ] }` -- a table built, not declared.

        This shape defeats every declaration-based resolver at once: the records are not a
        `const`, so `rva_symbols` has no declaration to evaluate; the addresses are named, so a
        literal scan finds nothing; and the install site says `MhHook::new(address as *mut c_void,
        hook.detour)` forty lines below a `for hook in &hooks`.
        """
        for fn_name, _head, start, end in self.bodies.get(path, []):
            if fn_name == name:
                found = self._rva_fields(self.text_of(path)[start:end], path)
                if found:
                    return found
        return set()

    def _record_rvas(self, name, path):
        """Addresses carried by a struct constant -- `MapSeam { rva: 0x08855b0, .. }`.

        `rva_symbols` evaluates integer declarations; a struct literal is outside that universe by
        design, so `verify_seam(&WORLDMAP_VIEWMODEL_CTOR)` resolved to nothing and seven of
        er-invasion-warp's union hooks went unnamed.

        A field named as an address counts, and a blind hex sweep of the record is deliberately
        not the fallback: the first cut had one, and it invented a detour on `0x1a58` -- a struct
        offset sitting in an unrelated record. An address mined out of a literal has no name
        vouching for it, so the field name has to do that job or nothing does.

        A table of named records is followed one level into each name: er-seamless-bugfixes'
        `REGISTRY` is `&[null_special_effect::APPLY_GUARD, ..]`, and the addresses live in those
        constants' own `rva:` fields, three files away from the `REGISTRY.iter().map(|guard| ..)`
        that hands them to `resolve_detour_address`.
        """
        return self._record_rvas_scoped(name, path, depth=0)

    def _record_rvas_scoped(self, name, path, depth):
        last = name.split("::")[-1]
        out = set()
        for decl in self._scoped(self.index.by_simple.get(last, []), name, path):
            out |= self._rva_fields(decl.expr, decl.path)
            if depth == 0:
                for element in rva_symbols.IDENTIFIER.finditer(decl.expr):
                    spelled = element.group(0).replace(" ", "")
                    tail = spelled.split("::")[-1]
                    if tail != last and self.RECORD_NAME.fullmatch(tail):
                        out |= self._record_rvas_scoped(spelled, decl.path, depth + 1)
        return out

    def _binding_rvas(self, name, path, enclosing, depth, seen, limit):
        code = self.text_of(path)
        _fn, head, start, end = enclosing
        body = code[start:end]
        out, notes = set(), []
        # Nearest binding first, and stop at the first one that yields an address. That is what a
        # Rust reader does -- the last binding before the use is the one in scope -- and it is why
        # the earlier bindings are a fallback rather than a union: see `_visible`.
        for at, expr in self._visible(self._bindings(name, body), limit):
            found, note = self._expr_rvas(expr, path, enclosing, depth + 1, seen, at)
            out |= found
            if note:
                notes.append(note)
            if out:
                break
        if out:
            return out, ""
        # A parameter, not a local: follow the callers. `create_absolute_hook(target: *mut
        # c_void)` and `er_effects_union_register_2720(target: usize)` are both real wrappers in
        # this tree, and stopping at the parameter would silently drop every site behind them.
        params = code[head : start + 1]
        if re.search(r"[(,]\s*(?:mut\s+)?" + re.escape(name) + r"\s*:", params):
            found, note = self._caller_rvas(_fn, name, params, path, depth)
            out |= found
            if note:
                notes.append(note)
        return out, "; ".join(notes)

    BINDER = re.compile(r"\b(?:if\s+let|while\s+let|let|for)\b")

    @staticmethod
    def _visible(bindings, limit):
        """Bindings that can reach `limit`, nearest first -- the order a Rust reader resolves in.

        Shadowing is why this matters. `install_now_loading_helper_observer_hooks` binds `addr`
        five times, once per `if let Some(addr) = <a different address>`, and taking every binding
        of the name as a union attributed all five addresses to all five hooks. Bindings after the
        use are dropped outright; a later `let` cannot flow backwards.
        """
        if limit is None:
            return list(reversed(bindings))
        return [b for b in reversed(bindings) if b[0] < limit]

    def _bindings(self, name, body):
        """`[(offset, rhs)]` -- right-hand sides that can flow into `name`, in source order."""
        token = re.compile(r"(?<![\w.])" + re.escape(name) + r"(?![\w])")
        out = []
        for match in self.BINDER.finditer(body):
            keyword = match.group(0)
            if keyword.endswith("for"):
                head = body[match.end() : match.end() + 300]
                split = re.search(r"\bin\b", head)
                if not split:
                    continue
                pattern = head[: split.start()]
                if not token.search(pattern):
                    continue
                # `_rhs`, not "up to the matching brace": the iterable ends at the `{` that opens
                # the loop body. Taking the body too left the trailing `}` on the expression, and
                # the tuple slice then read one column of a malformed table -- the second row of a
                # two-row hook table went missing, silently.
                rhs_start = match.end() + split.end()
                out.append((match.start(), self._tuple_slice(pattern, name, self._rhs(body, rhs_start))))
                continue
            # `let PAT = RHS;` / `let PAT = RHS else {` / `if let PAT = RHS {`
            equals = self._top_level_equals(body, match.end())
            if equals is None:
                continue
            pattern = body[match.end() : equals]
            if not token.search(pattern):
                continue
            out.append((match.start(), self._rhs(body, equals + 1)))
        # `Ok(name) => ...` inside a `match SCRUTINEE { .. }`, and plain reassignment.
        for arm in re.finditer(
            r"(?:Ok|Some|Err)\s*\(\s*(?:mut\s+)?" + re.escape(name) + r"\s*\)\s*=>", body
        ):
            scrutinee = self._match_scrutinee(body, arm.start())
            if scrutinee:
                out.append((arm.start(), scrutinee))
        for assign in re.finditer(r"(?<![\w.=!<>])" + re.escape(name) + r"\s*=(?!=)", body):
            out.append((assign.start(), self._rhs(body, assign.end())))
        # A closure parameter over an iterator chain: `REGISTRY.iter().map(|guard| ..)` binds
        # `guard` to an element of `REGISTRY`, and the chain is what flows into it, the same way
        # a `for` loop's iterable is.
        for closure in self.CLOSURE_HEAD.finditer(body):
            if token.search(closure.group(1)):
                out.append((closure.start(), self._receiver(body, closure.start())))
        return sorted((at, expr) for at, expr in out if expr)

    CLOSURE_HEAD = re.compile(
        r"\.\s*(?:map|for_each|try_for_each|filter_map|filter|find_map|find|flat_map|any|all"
        r"|position|inspect)\s*\(\s*(?:move\s+)?\|([^|]*)\|"
    )

    @staticmethod
    def _receiver(body, end):
        """The iterator chain a `.map(|x| ..)` at `end` is called on, back to its start."""
        depth, i = 0, end - 1
        while i >= 0:
            c = body[i]
            if c in ")]":
                depth += 1
            elif c in "([":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and c in ";{},=|":
                break
            i -= 1
        return body[i + 1 : end]

    @staticmethod
    def _top_level_equals(body, start):
        depth, i = 0, start
        while i < len(body):
            c = body[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    return None
                depth -= 1
            elif depth == 0 and c == ";":
                return None
            elif depth == 0 and c == "=" and body[i - 1] not in "=!<>" and body[i + 1 : i + 2] != "=":
                return i
            i += 1
        return None

    @staticmethod
    def _rhs(body, start):
        """From just after `=`, the expression up to its `;`, ` else` or opening `{` at depth 0."""
        depth, i = 0, start
        while i < len(body):
            c = body[i]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c == "{":
                if depth == 0 and not re.search(r"(?:match|if|unsafe|loop)\s*$", body[start:i]):
                    return body[start:i]
                depth += 1
            elif c == "}":
                depth -= 1
            elif depth == 0 and c == ";":
                return body[start:i]
            elif depth == 0 and body.startswith(" else", i):
                return body[start:i]
            i += 1
        return body[start:]

    @staticmethod
    def _match_scrutinee(body, arm_start):
        head = None
        for match in re.finditer(r"\bmatch\b", body[:arm_start]):
            brace = body.find("{", match.end())
            if brace < 0:
                continue
            if brace < arm_start < _matching(body, brace):
                head = body[match.end() : brace]
        return head

    @staticmethod
    def _tuple_slice(pattern, name, rhs):
        """For `for (a, TARGET, c) in [ (..), (..) ]`, only the target column of each row.

        Taking the whole table would pull a row's handler pointers and slot references in beside
        its address -- harmless for a name that resolves to nothing, and a false finding the day
        one of those columns holds an unrelated constant.
        """
        inner = pattern.strip()
        if inner.startswith("(") and inner.endswith(")"):
            columns = rva_symbols._split_top(inner[1:-1], [","])
            index = next(
                (i for i, c in enumerate(columns) if re.fullmatch(r"\s*(?:mut\s+)?" + re.escape(name) + r"\s*", c)),
                None,
            )
            if index is not None:
                out = []
                for row in rva_symbols._split_top(rhs.strip().lstrip("[").rstrip("]"), [","]):
                    row = row.strip()
                    if row.startswith("(") and row.endswith(")"):
                        cells = rva_symbols._split_top(row[1:-1], [","])
                        if index < len(cells):
                            out.append(cells[index])
                if out:
                    return " , ".join(out)
        return rhs

    def _caller_rvas(self, function, name, params, path, depth):
        """Argument values callers pass for parameter `name` of `function`."""
        columns = rva_symbols._split_top(params[params.find("(") + 1 : params.rfind(")")], [","])
        index = next(
            (i for i, c in enumerate(columns) if re.match(r"\s*(?:mut\s+)?" + re.escape(name) + r"\s*:", c)),
            None,
        )
        if index is None:
            return set(), "parameter position unknown"
        out, followed = set(), 0
        call = re.compile(r"(?<![\w.])" + re.escape(function) + r"\s*\(")
        # Callers in the defining crate only. Seven crates each define their own `install_one`;
        # following every file that mentions the name filled er-reload-trace's site with
        # er-input-harness's pad addresses, then hit the caller cap before reaching the one caller
        # that walks the trace table. A wrapper only another crate calls comes out unresolved, and
        # the unresolved list names it. Measured 2026-10-23 when this was scoped: every address a
        # site lost was another crate's, and each either still resolves at its own crate's site or
        # is not detoured anywhere (er-convenient-deaths' byte-patched functions, a data global).
        crate = self.crate_of(path)
        for other in (p for p in self.mentioning(function) if self.crate_of(p) == crate):
            code = self.text_of(other)
            for match in call.finditer(code):
                args, _close = split_args(code, match.end() - 1)
                if not args or index >= len(args):
                    continue
                enclosing = self.enclosing(other, match.start())
                if enclosing is None or enclosing[0] == function:
                    continue  # the definition itself, or a recursive call
                followed += 1
                if followed > MAX_CALLERS_FOLLOWED:
                    return out, "too many callers to follow"
                found, _note = self._expr_rvas(
                    args[index], other, enclosing, depth + 1, set(), match.start() - enclosing[2]
                )
                out |= found
        return out, "" if out else "no caller supplies a constant"


# ---------------------------------------------------------------------------------------------
# Raw MinHook: the class that bypasses the gate entirely
# ---------------------------------------------------------------------------------------------

# `(?<![\w.])` and not `(?<![\w:.])`: the bypass this exists for was spelled
# `er_hook::MH_CreateHook(..)` -- an import of the raw extern, reached through its module.
RAW_MINHOOK = re.compile(r"(?<![\w.])MH_CreateHook\s*\(")


def raw_minhook_sites(root=CRATES, scanner=None):
    """`MH_CreateHook` called outside `er-hook`, where nothing consults the allowlist.

    Measured 2026-08-30: `er-reload-trace` imported the raw externs and put 19 five-byte JMPs into
    the middle of live instructions while its log said `installed` 34 times. That crate has since
    been converted; this is what stops it, or anything else, coming back.
    """
    out = []
    for path in rust_sources(root):
        if os.path.relpath(path, root).split(os.sep)[0] == "er-hook":
            continue
        code = scanner.text_of(path) if scanner else rva_symbols.code_only(read(path))
        for match in RAW_MINHOOK.finditer(code):
            out.append((path, code.count("\n", 0, match.start()) + 1))
    return out


# ---------------------------------------------------------------------------------------------
# Foreign modules
# ---------------------------------------------------------------------------------------------

# `GetModuleHandleA("ersc.dll")` -- a file that resolves another module by name.
FOREIGN_HANDLE = re.compile(r'GetModuleHandle[AW]\s*\(\s*c?"([A-Za-z0-9_+.\-]+)\.dll"')
MODBLOCK = re.compile(r"\bmod\s+(\w+)\s*\{")
# The same module declared in its own file: `mod ersc;` beside `ersc.rs`. Measured 2026-09-02,
# when `local_invasion_filter.rs`'s `mod ersc { .. }` block moved to `local_invasion_filter/ersc.rs`
# and eight ersc.dll RVAs became game addresses to every tool that knew only the block form.
MODFILE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)


def foreign_module_files(paths):
    """`{path: "ersc.dll"}` for every file that is a foreign module, declared `mod <stem>;`.

    The declaring file must resolve `<stem>.dll` by name itself, so the game's own
    `GetModuleHandleA(NULL)` can never promote a sibling file to foreign. Only the paths rustc
    itself would look at are accepted, so this cannot reach a same-named file elsewhere.
    """
    known = set(paths)
    out = {}
    for path in paths:
        text = read(path)
        stems = {m.group(1).lower() for m in FOREIGN_HANDLE.finditer(text)}
        if not stems:
            continue
        directory = os.path.join(os.path.dirname(path), os.path.basename(path)[: -len(".rs")])
        for m in MODFILE.finditer(text):
            stem = m.group(1).lower()
            if stem not in stems:
                continue
            for candidate in (
                os.path.join(directory, f"{stem}.rs"),
                os.path.join(directory, stem, "mod.rs"),
                os.path.join(os.path.dirname(path), f"{stem}.rs"),
            ):
                if candidate in known and candidate != path:
                    out[candidate] = f"{stem}.dll"
    return out


def foreign_module_spans(text, whole_file_module=None):
    """`[(first_line, last_line, "ersc.dll")]` -- the `mod <stem> { ... }` blocks holding a
    non-game module's addresses. `whole_file_module` makes the span the whole file."""
    if whole_file_module:
        return [(1, text.count("\n") + 2, whole_file_module)]
    stems = {m.group(1).lower() for m in FOREIGN_HANDLE.finditer(text)}
    spans = []
    if not stems:
        return spans
    for m in MODBLOCK.finditer(text):
        stem = m.group(1).lower()
        if stem not in stems:
            continue
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        spans.append((text[: m.start()].count("\n") + 1, text[:i].count("\n") + 1, f"{stem}.dll"))
    return spans


class ForeignFilter:
    """Is this RVA declared inside another module's block -- i.e. not an eldenring.exe address?

    An address is a game address because of the base it is added to. `er-invasion-warp` resolves
    Seamless Co-op with `GetModuleHandleA("ersc.dll")` and detours RVAs on that base; those are
    ersc.dll offsets, and the game's allowlist has nothing to say about them.
    """

    def __init__(self, index, sources):
        self.index = index
        self.whole_files = foreign_module_files(sources)
        self.spans = {}
        # One pass over the resolved declarations, not `Index.claims()` per address: `claims()`
        # additionally runs a whole-tree scan per claimed symbol, and nothing here needs the use
        # sites -- it needs where the address is declared.
        self.holders = {}
        for decl in index.decls:
            for value in decl.value or ():
                self.holders.setdefault(value, []).append((decl.path, decl.line))
        for literal in index.literals:
            self.holders.setdefault(literal.value, []).append((literal.path, literal.line))

    def _spans_for(self, path):
        if path not in self.spans:
            self.spans[path] = foreign_module_spans(read(path), self.whole_files.get(path))
        return self.spans[path]

    def is_foreign(self, rva):
        holders = self.holders.get(rva, []) + self.holders.get(rva + rva_symbols.IMAGE_BASE, [])
        if not holders:
            return False
        return all(
            any(lo <= line <= hi for lo, hi, _m in self._spans_for(path)) for path, line in holders
        )


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------

# A `.data` singleton pointer is not a detour target, and this gate's own resolver cannot tell one
# from a function. Its backward walk is deliberately generous: at a wrapper whose parameter is a
# bare `rva: usize` it follows callers, and where the callers take the address from a table field
# it collects every RVA the crate declares. Measured 2026-09-06 on
# `crates/er-input-harness/src/pad_inject.rs` (`install_one`): 48 addresses attributed to a site
# whose six real callers pass six constants, one of the spurious ones a `GameDataMan` global that
# only ever reaches a `deref_singleton` read.
#
# The rule is the sibling gate's: `scripts/check-shared-hook-rvas.py` carries
# `READ_ONLY = GLOBAL|SINGLETON|VTABLE|_DATA_|REPOSITORY` for this. It is all-or-nothing across
# declarations: an address is dropped when every name declaring it says data, so a real detour
# target that merely shares a value with a data alias still fails. `STATE_ROOT` is this gate's one
# addition: `MOUNT_GUARD_STATE_ROOT_RVA` is a role alias for the `GameDataMan` singleton, and its
# own declaration says so.
DATA_ONLY_NAME = re.compile(r"GLOBAL|SINGLETON|VTABLE|_DATA_|REPOSITORY|STATE_ROOT")


def is_data_only_address(rva, index):
    """True when every symbol declaring `rva` names a data object rather than a function."""
    # `decl.value` is a set (a name can resolve to more than one number through a band or an
    # enum), so membership is the test.
    names = {
        decl.symbol
        for decl in getattr(index, "decls", ())
        if decl.symbol and rva in (getattr(decl, "value", None) or ())
    }
    if not names:
        return False
    return all(DATA_ONLY_NAME.search(name) for name in names)


class Finding:
    __slots__ = ("rva", "sites")

    def __init__(self, rva):
        self.rva, self.sites = rva, []


def findings_for(sites, allowed, foreign, index):
    """`[Finding]`, one per detoured address that is not on `allowed`, sorted by address."""
    out = {}
    for site in sites:
        for rva in sorted(site.rvas):
            if rva in allowed or foreign.is_foreign(rva) or is_data_only_address(rva, index):
                continue
            out.setdefault(rva, Finding(rva)).sites.append(site)
    return [out[r] for r in sorted(out)]


def audit(root=CRATES, rows=None, index=None, with_image=True):
    rows = read_allowlist() if rows is None else rows
    translating, derived = installer_spellings()
    installers = translating + ([DIRECT_RESOLVER] if DIRECT_RESOLVER not in translating else [])
    scanner = Scanner(installers, derived, root=root, index=index)
    sites = scanner.sites()
    foreign = ForeignFilter(scanner.index, scanner.sources())
    image, image_note = image_findings(rows) if with_image else ([], "image half not run")
    return {
        "rows": rows,
        "translating": installers,
        "derived": derived,
        "sites": sites,
        "scanner": scanner,
        "foreign": foreign,
        "findings": findings_for(sites, set(rows), foreign, scanner.index),
        "image": image,
        "image_note": image_note,
        "raw": raw_minhook_sites(root, scanner),
        "resolved": [s for s in sites if s.rvas],
        "unresolved": [s for s in sites if not s.rvas],
        "rvas": {r for s in sites for r in s.rvas},
    }


def report(result, out=sys.stdout, show_sites=False):
    print(
        f"allowlist: {len(result['rows'])} row(s) in {os.path.relpath(ALLOWLIST_RS, ROOT)}, "
        "sorted and unique",
        file=out,
    )
    print(f"  {result['image_note']}", file=out)
    print(f"detour installers (consult the allowlist): {', '.join(result['translating'])}", file=out)
    print(f"exempt (audit the running image instead): {', '.join(result['derived']) or 'none'}", file=out)
    print(
        f"{len(result['sites'])} detour install site(s); {len(result['resolved'])} resolve to "
        f"{len(result['rvas'])} distinct RVA(s); {len(result['unresolved'])} unresolved",
        file=out,
    )
    if show_sites:
        for site in result["sites"]:
            addrs = ", ".join(f"0x{r:x}" for r in sorted(site.rvas)) or f"-- {site.note}"
            print(f"  {site.where}  {site.installer}({site.expr})  {addrs}", file=out)
    if result["unresolved"]:
        print(
            "\nUnresolved -- the scan saw the install and could not name the address. These are "
            "not cleared;\nthey are the scan's blind spots, and a Win32 / vtable / exported-entry "
            "target is the ordinary reason:",
            file=out,
        )
        for site in result["unresolved"]:
            print(f"  {site.where}  {site.installer}({site.expr})  [{site.note}]", file=out)
    return result


def verdict(result, out=sys.stdout):
    failures = []
    for finding in result["findings"]:
        failures.append(
            f"0x{finding.rva:x} is not on DETOUR_ALLOWLIST and is detoured at "
            + ", ".join(sorted({s.where for s in finding.sites}))
        )
    for rva, why in result["image"]:
        failures.append(f"allowlist row 0x{rva:08x} {why}")
    for path, line in result["raw"]:
        failures.append(
            f"{os.path.relpath(path, ROOT)}:{line} calls MH_CreateHook directly -- er-hook's "
            f"allowlist check is not on that path at all"
        )
    counts = (
        ("detour install sites", len(result["sites"]), MIN_DETOUR_SITES),
        ("sites resolved to an RVA", len(result["resolved"]), MIN_RESOLVED_SITES),
        ("distinct detoured RVAs", len(result["rvas"]), MIN_DISTINCT_RVAS),
        ("allowlist rows", len(result["rows"]), MIN_ALLOWLIST_ROWS),
    )
    for label, got, floor in counts:
        if got < floor:
            failures.append(
                f"only {got} {label}, floor is {floor}. The scan has gone blind or the tree "
                f"shrank; a zero-finding pass on this is worthless either way"
            )
    if not failures:
        print(
            f"\ncheck-detour-rva-coverage: OK -- {len(result['resolved'])} resolved detour site(s) "
            f"covering {len(result['rvas'])} address(es), every game address on the 2.7.2.0 "
            f"detour allowlist.",
            file=out,
        )
        return 0
    print(f"\ncheck-detour-rva-coverage: {len(failures)} FAILURE(S)", file=out)
    for line in failures:
        print(f"  FAIL {line}", file=out)
    print(
        "\nA constant detour on an address that is not on DETOUR_ALLOWLIST is refused by\n"
        "er_game_base::game_build::resolve_detour_address at runtime, once per retry, forever --\n"
        "the feature is inert and the only evidence is a log line. Measure the address on\n"
        f"{IMAGE_NAME} (a .pdata entry that starts at it, or a leaf of at least\n"
        f"{DETOUR_PATCH_BYTES} bytes no declared function contains) and add its row, in order, to\n"
        f"  {os.path.relpath(ALLOWLIST_RS, ROOT)}\n"
        "An address found at runtime belongs on er_hook's *_runtime_derived entry points instead.",
        file=out,
    )
    return 1


# ---------------------------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------------------------

# The positive control, frozen as a literal and not composed from the matcher.
#
# `er-armament-icons` installs a shared-union detour on the Scaleform file-open wrapper, spelled
# `base + FILE_OPEN_RVA`, and that address is on the allowlist. The scan must find it, name the
# address, and clear it. Every value below is written out by hand: a control assembled from
# `Scanner`'s own regexes widens when they widen, and then "the scan sees this" becomes "the scan
# sees whatever it sees", which is not a claim at all.
CONTROL_SITE = "crates/er-armament-icons/src/gfx_equip_hook.rs"
CONTROL_INSTALLER = "register_shared_hook"
# `TITLE_SCALEFORM_FILE_OPEN_RVA` on 2.7.2.0 (1.16.2 0x11ced80 before the port).
CONTROL_RVA = 0x11D22E0
# The indirect shape, which a naive matcher fails: `addr` is bound by a
# `let Ok(addr) = game_rva_for_hook(..) else` six lines above the `MhHook::new(addr, ..)` that uses
# it. `PLAYER_GAME_DATA_NAME_GETTER_RVA` on 2.7.2.0 (1.16.2 0x25f8e0).
CONTROL_INDIRECT_SITE = "crates/er-quickload/src/startup_hooks/loading_cover/title_visual_hooks.rs"
CONTROL_INDIRECT_RVA = 0x25F8F0
# The built-table shape: `LOADING_SCREEN_GFX_FADEOUT_RVA` reaches its `MhHook::new` through a
# `fn observer_hooks() -> [ObserverHook; 5]` whose array return type breaks a naive body scan, a
# `for hook in &hooks` binding, an `rva: <NAME> as u32` record field, and `rva_symbols`' value
# resolution of that name. On 2.7.2.0 it is 0x90b6f0 (1.16.2 0x90a0a0, the seven-minute cover).
CONTROL_TABLE_SITE = "crates/er-loading-portrait-core/src/dlstring_lookat_math.rs"
CONTROL_TABLE_RVA = 0x90B6F0
# The registry shape: er-seamless-bugfixes hands `base + guard.rva` to `resolve_detour_address`
# inside `REGISTRY.iter().map(|guard| ..)`, `REGISTRY` is imported from `guards` while `patches`
# and `mid_hooks` declare tables of the same name, and the addresses sit in `Guard` constants in
# two further files. `HAS_SPECIAL_EFFECT_ID_RVA`, `APPLY_RVA`, `LOAD_BALANCER_PARAM_RVA`. Until
# 2026-10-23 the scan saw none of them: taking all three off the allowlist gave zero findings.
CONTROL_REGISTRY_SITE = "crates/er-seamless-bugfixes/src/lib.rs"
CONTROL_REGISTRY_RVAS = (0x4FA7F0, 0x4FB790, 0xD404A0)
# er-reload-trace's `HOOKS` table, reached through `install_one(base, spec, ..)`. Seven crates
# define an `install_one`; the unscoped walk followed their callers instead, filled this site with
# er-input-harness's pad address `0x240e70`, and hit the caller cap before reading `HOOKS`.
CONTROL_TRACE_SITE = "crates/er-reload-trace/src/lib.rs"
CONTROL_TRACE_RVA = 0x82CF60  # the first `HookSpec` row
CONTROL_FOREIGN_RVA = 0x240E70
# The matcher this gate replaced, frozen verbatim: a literal address written beside the installer.
# Kept so the controls above can prove each widening is load-bearing.
NAIVE = re.compile(r"MhHook::new\s*\(\s*(?:base\s*\+\s*)?0x[0-9a-fA-F]+")

# Image controls, frozen from `eldenring-deobf-1.17.2.bin` on 2026-10-23. Each is a row that must
# be refused, and each for a different reason; the selftest plants them one at a time.
IMAGE_CONTROLS = (
    # A `UNW_FLAG_CHAININFO` continuation whose merged run is 0x1a8a00..0x1a8ad5.
    (0x1A8AC6, "chained-unwind"),
    # 0x10 bytes into the file-open wrapper, whose declared run is 0x11d22e0..0x11d2376.
    (CONTROL_RVA + 0x10, "bytes into the declared function"),
    # `WorldChrMan`'s global, in `.data`.
    (0x3D6D018, "not executable"),
)

FIXTURE = {
    "crates/fixture/src/lib.rs": (
        "pub const DIRECT_RVA: usize = 0x111000;\n"
        "pub const LOCAL_RVA: usize = 0x222000;\n"
        "pub const ARM_RVA: usize = 0x333000;\n"
        "pub const TABLE_A_RVA: usize = 0x444000;\n"
        "pub const TABLE_B_RVA: usize = 0x555000;\n"
        "pub const PARAM_RVA: usize = 0x666000;\n"
        "#[repr(usize)] pub enum Rvas { Discriminant = 0x777000 }\n"
        "pub const ENUM_RVA: usize = Rvas::Discriminant as usize;\n"
        "unsafe fn direct() { MhHook::new((base + DIRECT_RVA) as *mut c_void, h); }\n"
        "unsafe fn literal() { MhHook::new(base + 0x888ab0 as *mut c_void, h); }\n"
        "unsafe fn qualified() { er_hook::register_union_hook(base + DIRECT_RVA, h, &S); }\n"
        "unsafe fn local() {\n"
        "    let Ok(addr) = game_rva(LOCAL_RVA as u32) else { return; };\n"
        "    MhHook::new(addr as *mut c_void, h);\n"
        "}\n"
        "unsafe fn arm() {\n"
        "    match game_rva(ARM_RVA as u32) { Ok(a) => { MhHook::new(a, h); }, Err(_) => {} }\n"
        "}\n"
        "unsafe fn table() {\n"
        "    for (name, target, slot) in [(\"a\", base + TABLE_A_RVA, &A), (\"b\", base + TABLE_B_RVA, &B)] {\n"
        "        register_shared_hook(target, handler, slot);\n"
        "    }\n"
        "}\n"
        "unsafe fn wrapper(target: usize) { register_union_hook(target, h, &S); }\n"
        "unsafe fn caller() { wrapper(base + PARAM_RVA); }\n"
        "pub const FN_TABLE_RVA: usize = 0x999ab0;\n"
        "fn table_fn() -> [Spec; 1] { [Spec { rva: FN_TABLE_RVA as u32 }] }\n"
        "unsafe fn from_table() {\n"
        "    let t = table_fn();\n"
        "    for s in &t { let a = game_rva(s.rva).unwrap(); MhHook::new(a as *mut c_void, h); }\n"
        "}\n"
        "unsafe fn variant() {\n"
        "    let a = game_rva(ENUM_RVA as u32).unwrap();\n"
        "    MhHook::new(a as *mut c_void, h);\n"
        "}\n"
        "pub const KEPT_RVA: usize = 0xaaa000;\n"
        "unsafe fn kept() {\n"
        "    let Some(a) = er_game_base::game_build::resolve_detour_address(base + KEPT_RVA, \"k\") else { return };\n"
        "    install_later(a);\n"
        "}\n"
        "unsafe fn exempt() { MhHook::new_runtime_derived(scanned, h); }\n"
        "unsafe fn raw() { MH_CreateHook(x, y, &mut t); }\n"
        # er-seamless-bugfixes' shape: a closure over a registry of named records declared in
        # another module, with a same-named table in a third module that is not the one imported.
        "use guards::{Guard, REGISTRY};\n"
        "unsafe fn registry() {\n"
        "    let resolved: Vec<Option<usize>> = REGISTRY\n"
        "        .iter()\n"
        "        .map(|guard| resolve_detour_address(base + guard.rva, guard.name))\n"
        "        .collect();\n"
        "}\n"
        # er-reload-trace's shape: a parameter of a function another crate also defines by name.
        "static TRACE_HOOKS: &[TraceSpec] = &[TraceSpec { name: \"t\", rva: 0x1234560 }];\n"
        "unsafe fn install_one(spec: &TraceSpec) { register_union_hook(base + spec.rva, h, &S); }\n"
        "unsafe fn install_all() { for spec in TRACE_HOOKS { install_one(spec); } }\n"
    ),
    "crates/fixture/src/guards.rs": (
        "pub const GUARD_A_RVA: usize = 0xbbb000;\n"
        "pub const GUARD_A: Guard = Guard { name: \"a\", rva: GUARD_A_RVA };\n"
        "pub const GUARD_B: Guard = Guard { name: \"b\", rva: 0xccc000 };\n"
        "pub static REGISTRY: &[Guard] = &[GUARD_A, GUARD_B];\n"
    ),
    "crates/fixture/src/patches.rs": (
        "pub static REGISTRY: &[Patch] = &[Patch { name: \"p\", function_rva: 0xddd000 }];\n"
    ),
    "crates/other/src/lib.rs": (
        "pub const OTHER_RVA: usize = 0xeee000;\n"
        "unsafe fn install_one(target: usize) { MhHook::new(target as *mut c_void, h); }\n"
        "unsafe fn other_caller() { install_one(base + OTHER_RVA); }\n"
    ),
}
# Every address the fixture detours, in the shapes listed in `selftest`.
FIXTURE_RVAS = {
    0x111000, 0x222000, 0x333000, 0x444000, 0x555000, 0x666000, 0x777000, 0x888AB0, 0x999AB0,
    0xAAA000, 0xBBB000, 0xCCC000, 0x1234560, 0xEEE000,
}
# The site each of the scoped shapes must resolve to, and nothing else.
FIXTURE_SCOPED = {
    ("crates/fixture/src/lib.rs", "resolve_detour_address", "base + guard.rva"): {0xBBB000, 0xCCC000},
    ("crates/fixture/src/lib.rs", "register_union_hook", "base + spec.rva"): {0x1234560},
    ("crates/other/src/lib.rs", "MhHook::new", "target as *mut c_void"): {0xEEE000},
}


def _allowlist_source(rows, declared=None):
    body = "".join(f"    0x{r:08x}, // `ROW_RVA`, `IDENTICAL-WHOLE`\n" for r in rows)
    count = len(rows) if declared is None else declared
    return f"//! fixture\npub(crate) const DETOUR_ALLOWLIST: [u32; {count}] = [\n{body}];\n"


def selftest():
    import tempfile

    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # -- the allowlist reader: it reads the real list, and refuses one the resolver could not search
    rows = read_allowlist()
    check("the real allowlist parses to at least the floor", len(rows) >= MIN_ALLOWLIST_ROWS, True)
    check("...and the controls are on it", {CONTROL_RVA, CONTROL_INDIRECT_RVA, CONTROL_TABLE_RVA} <= set(rows), True)
    check("a well-formed fixture list round-trips", read_allowlist(_allowlist_source([0x1000, 0x2000])), [0x1000, 0x2000])
    for label, source in (
        ("an empty list", _allowlist_source([])),
        ("a list out of order", _allowlist_source([0x2000, 0x1000])),
        ("a repeated row", _allowlist_source([0x1000, 0x1000])),
        ("a declared length that disagrees with the rows", _allowlist_source([0x1000], declared=2)),
        ("a row that is not a hex literal", _allowlist_source([0x1000]).replace("0x00001000", "SOME_RVA")),
        ("a file with no list at all", "pub const OTHER: [u32; 1] = [0x1000];\n"),
    ):
        try:
            read_allowlist(source)
            failures.append(f"{label} was accepted; the reader must raise AllowlistError")
        except AllowlistError:
            pass
    # A row in a comment is not a row: the reader blanks comments before it parses.
    commented = _allowlist_source([0x1000]).replace("];", "    // 0x00009000,\n];")
    check("a hex value inside a comment is not read as a row", read_allowlist(commented), [0x1000])

    # -- the installer list is derived from er-hook --------------------------------------------
    translating, derived = installer_spellings()
    check("MhHook::new is recognised as a translating detour installer", "MhHook::new" in translating, True)
    check("register_union_hook is too", "register_union_hook" in translating, True)
    check(
        "register_shared_hook reaches the resolver through its budget form",
        "register_shared_hook" in translating,
        True,
    )
    check(
        "the runtime-derived variants are not treated as translating",
        [n for n in translating if n.endswith("_runtime_derived")],
        [],
    )
    check("...and they are listed as exempt rather than dropped", bool(derived), True)
    # The closure on a synthetic er-hook. A propagation rule that walked method names dragged both
    # `*_runtime_derived` entry points into the translating set via `Vec::new(`.
    synthetic = """
        fn resolve_target(a: usize) -> Option<usize> { resolve_detour_address(a) }
        fn helper() -> usize { let v = Vec::new(); v.len() }
        impl Thing {
            pub unsafe fn new(a: usize) { resolve_target(a); Self::create(a) }
            pub unsafe fn new_runtime_derived(a: usize) { write_site_is_sound(a); Self::create(a) }
            unsafe fn create(a: usize) { let v = Vec::new(); MH_CreateHook(a); }
        }
        pub unsafe fn gated(t: usize) { resolve_target(t); }
        pub unsafe fn wraps(t: usize) { gated(t); }
        pub unsafe fn scanned(t: usize) { write_site_is_sound(t); }
        pub fn unrelated() -> usize { helper() }
    """
    synthetic_translating, synthetic_derived = installer_spellings(source=synthetic)
    check(
        "the closure admits exactly the functions that reach the resolver",
        synthetic_translating,
        ["Thing::new", "gated", "wraps"],
    )
    check(
        "...and the runtime-derived pair is exempt, not translating",
        synthetic_derived,
        ["Thing::new_runtime_derived", "scanned"],
    )

    # -- the fixture: every declaration shape, and the gate failing on it ----------------------
    scratch = tempfile.mkdtemp()
    for name, body in FIXTURE.items():
        path = os.path.join(scratch, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(body)
    fixture_root = os.path.join(scratch, "crates")
    fixture_index = rva_symbols.Index.build(root=fixture_root)
    fixture = Scanner(translating + [DIRECT_RESOLVER], derived, root=fixture_root, index=fixture_index)
    found = fixture.sites()
    seen = {r for s in found for r in s.rvas}
    for label, rva in (
        ("a literal `base + CONST` argument", 0x111000),
        ("a local bound by `let ... else` from game_rva", 0x222000),
        ("a match-arm binding", 0x333000),
        ("the first row of a `for (name, target, ..) in [..]` table", 0x444000),
        ("the second row of that table", 0x555000),
        ("an address passed through a wrapper's parameter", 0x666000),
        ("an enum discriminant reached through a derived const", 0x777000),
        ("a bare literal written at the call", 0x888AB0),
        ("an `rva:` field of a table built by a fn with an array return type", 0x999AB0),
        ("an address handed to resolve_detour_address directly", 0xAAA000),
        ("the first named record of a registry walked by `.iter().map(|guard| ..)`", 0xBBB000),
        ("the second, whose `rva:` is a literal", 0xCCC000),
        ("a table row reached through a parameter of a function two crates define", 0x1234560),
    ):
        check(f"the slice resolves {label}", rva in seen, True)
    check("the fixture detours exactly the planted addresses", seen, FIXTURE_RVAS)
    # Scope: each of these sites resolves to its own crate's and its own module's addresses only.
    # Unscoped, the registry site also read `patches::REGISTRY`'s 0xddd000, and each
    # `install_one` site took the other crate's caller as its own.
    scoped = {
        (os.path.relpath(s.path, scratch), s.installer, s.expr): s.rvas
        for s in found
        if (os.path.relpath(s.path, scratch), s.installer, s.expr) in FIXTURE_SCOPED
    }
    check("each scoped shape resolves to exactly its own addresses", scoped, FIXTURE_SCOPED)
    check(
        "the runtime-derived installer is not counted as a site",
        [s for s in found if "runtime_derived" in s.expr or "scanned" in s.expr],
        [],
    )
    check("a raw MH_CreateHook is caught", len(raw_minhook_sites(fixture_root, fixture)), 1)
    check(
        "a module-qualified `er_hook::register_union_hook(..)` call is a site",
        any(s.installer == "register_union_hook" and 0x111000 in s.rvas for s in found),
        True,
    )
    check(
        "the naive matcher finds the one literal site",
        len(NAIVE.findall(FIXTURE["crates/fixture/src/lib.rs"])),
        1,
    )
    check("...and the scan finds all thirteen, so the twelve extra shapes prove something", len(found), 13)
    # The gate can fail: with half the planted addresses on a fixture allowlist, the other half are
    # exactly the findings, each naming its site. With all of them on it, there are none.
    fixture_foreign = ForeignFilter(fixture_index, fixture.sources())
    admitted = {0x111000, 0x333000, 0x555000, 0x777000, 0x999AB0, 0xBBB000, 0xEEE000}
    partial = findings_for(found, admitted, fixture_foreign, fixture_index)
    check(
        "a fixture allowlist missing seven planted addresses yields exactly those seven findings",
        {f.rva for f in partial},
        FIXTURE_RVAS - admitted,
    )
    check("...each naming at least one install site", all(f.sites for f in partial), True)
    check(
        "...and the full list clears the fixture",
        findings_for(found, FIXTURE_RVAS, fixture_foreign, fixture_index),
        [],
    )

    # -- the real tree. A scanner that only ever runs on its own fixture is a fixture. ----------
    result = audit()
    check("the real scan finds detour sites", len(result["sites"]) >= MIN_DETOUR_SITES, True)
    check("...and resolves most of them", len(result["resolved"]) >= MIN_RESOLVED_SITES, True)
    check(
        "...to a plausible number of distinct addresses",
        len(result["rvas"]) >= MIN_DISTINCT_RVAS,
        True,
    )
    control = [
        s
        for s in result["sites"]
        if os.path.relpath(s.path, ROOT) == CONTROL_SITE
        and s.installer == CONTROL_INSTALLER
        and CONTROL_RVA in s.rvas
    ]
    check("the control: the frozen allowlisted site is found and named", len(control), 1)
    indirect = [
        s
        for s in result["sites"]
        if os.path.relpath(s.path, ROOT) == CONTROL_INDIRECT_SITE and CONTROL_INDIRECT_RVA in s.rvas
    ]
    check("the indirect control: an address bound lines above its installer", len(indirect) >= 1, True)
    table = [
        s
        for s in result["sites"]
        if os.path.relpath(s.path, ROOT) == CONTROL_TABLE_SITE and CONTROL_TABLE_RVA in s.rvas
    ]
    check("the built-table control: named through observer_hooks()'s array return type", len(table), 1)
    naive_hits = NAIVE.findall(read(os.path.join(ROOT, CONTROL_INDIRECT_SITE)))
    check("...which the naive matcher cannot see (so the control is non-vacuous)", naive_hits, [])
    check("no raw MH_CreateHook survives in the real tree", result["raw"], [])
    trace = [s for s in result["sites"] if os.path.relpath(s.path, ROOT) == CONTROL_TRACE_SITE]
    check(
        "the trace-table control: er-reload-trace's HOOKS rows reach its install site",
        any(CONTROL_TRACE_RVA in s.rvas for s in trace),
        True,
    )
    check(
        "...and another crate's install_one callers do not",
        [s.where for s in trace if CONTROL_FOREIGN_RVA in s.rvas],
        [],
    )
    # The real-tree half can fail too: take the controls off the list and each must come back as
    # a finding naming its own site.
    planted = (
        (CONTROL_RVA, CONTROL_SITE),
        (CONTROL_INDIRECT_RVA, CONTROL_INDIRECT_SITE),
        (CONTROL_TABLE_RVA, CONTROL_TABLE_SITE),
        (CONTROL_TRACE_RVA, CONTROL_TRACE_SITE),
        *((rva, CONTROL_REGISTRY_SITE) for rva in CONTROL_REGISTRY_RVAS),
    )
    allowed = set(result["rows"]) - {rva for rva, _site in planted}
    mutated = {
        f.rva: {os.path.relpath(s.path, ROOT) for s in f.sites}
        for f in findings_for(result["sites"], allowed, result["foreign"], result["scanner"].index)
    }
    for rva, site in planted:
        check(f"0x{rva:x} off the allowlist is a finding at {site}", site in mutated.get(rva, set()), True)

    # -- the image half: real rows pass, and each planted defect is refused for its own reason ----
    path = image_path()
    image_checked = os.path.isfile(path)
    if image_checked:
        facts = ImageFacts(open(path, "rb").read())
        for rva in (CONTROL_RVA, CONTROL_INDIRECT_RVA, CONTROL_TABLE_RVA):
            check(f"allowlist row 0x{rva:x} is a sound detour target in the image", row_problem(rva, facts), None)
        check("CONTROL_RVA is a declared function start", CONTROL_RVA in facts.starts, True)
        check("CONTROL_INDIRECT_RVA is an unwindless leaf", CONTROL_INDIRECT_RVA in facts.starts, False)
        for rva, reason in IMAGE_CONTROLS:
            why = row_problem(rva, facts) or ""
            check(f"a planted row 0x{rva:x} is refused as {reason!r}", reason in why, True)
        short = row_problem(CONTROL_INDIRECT_RVA, facts, leaf_end=lambda rva: rva + 3)
        check("a planted 3-byte leaf is refused as too short to patch", "3-byte leaf" in (short or ""), True)
        check("the real allowlist has no image findings", result["image"], [])
    else:
        print(f"check-detour-rva-coverage selftest: {result['image_note']}")

    for failure in failures:
        print(f"check-detour-rva-coverage selftest FAILED -- {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"check-detour-rva-coverage selftest: OK ({len(result['sites'])} real detour sites, "
        f"{len(result['rvas'])} addresses, allowlist {len(result['rows'])} rows, "
        f"image half {'checked' if image_checked else 'skipped'})"
    )
    return 0


def _ensure_capstone():
    """Re-exec under uv when the image half needs capstone and the interpreter lacks it.

    The leaf sweep decodes instructions. There is no system pip here; uv provisions capstone from
    its cache in milliseconds, and re-executing keeps the check.sh step spelled `python3`. Without
    the image there is nothing to decode, so nothing is re-executed.
    """
    if not os.path.isfile(image_path()):
        return
    try:
        import capstone  # noqa: F401
    except ImportError:
        try:
            os.execvp("uv", ["uv", "run", "--with", "capstone", "python3", *sys.argv])
        except OSError:
            print("check-detour-rva-coverage: capstone unavailable and `uv` is not on PATH", file=sys.stderr)
            raise SystemExit(2) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print every detour site found")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    _ensure_capstone()
    if args.selftest:
        return selftest()
    try:
        result = audit()
    except AllowlistError as error:
        print(f"check-detour-rva-coverage: {error}", file=sys.stderr)
        return 2
    report(result, show_sites=args.list)
    return verdict(result)


if __name__ == "__main__":
    sys.exit(main())
