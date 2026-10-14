#!/usr/bin/env python3
"""Every policy that exports a decision must be named in `.cupcake/system/evaluate.rego`.

Since 2026-09-18 the dispatcher lists each decision-exporting package by hand instead of walking
`data.cupcake.policies` (the walk crashed the wasm runtime; see the header of evaluate.rego). That
trade made a new failure silent: a policy file that is parsed, routed and compiled, and whose
`deny` is never read. `opa test` passes, because it queries the package directly, and the live hook
answers allow. Measured 2026-10-07: `transcript_write_guard.rego` passed 14 of 14 unit tests and
denied nothing under `cupcake eval`, even with a rule body reduced to "deny every Bash call", until
its line was added here.

This gate reads every `.rego` under `.cupcake/policies` for a top-level `deny`, `halt` or
`add_context` rule and requires the matching
`all_<verb>s contains decision if some decision in data.<package>.<verb>` line, and the reverse:
a dispatcher line whose package or verb no longer exists is reported too.

    python3 scripts/check-cupcake-dispatcher.py
    python3 scripts/check-cupcake-dispatcher.py --selftest
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = REPO_ROOT / ".cupcake" / "policies"
EVALUATE = REPO_ROOT / ".cupcake" / "system" / "evaluate.rego"

VERB_COLLECTION = {"deny": "all_denials", "halt": "all_halts", "add_context": "all_add_context"}
PACKAGE_RE = re.compile(r"^package\s+([\w.]+)", re.MULTILINE)
EXPORT_RE = re.compile(r"^(deny|halt|add_context)\s+(?:contains\b|:=)", re.MULTILINE)
DISPATCH_RE = re.compile(
    r"^(all_denials|all_halts|all_add_context)\s+contains\s+decision\s+if\s+some\s+decision\s+in\s+"
    r"data\.([\w.]+)\.(deny|halt|add_context)\s*$",
    re.MULTILINE,
)


def exports(policy_texts: dict[str, str]) -> set[tuple[str, str]]:
    found = set()
    for text in policy_texts.values():
        m = PACKAGE_RE.search(text)
        if not m:
            continue
        for verb in set(EXPORT_RE.findall(text)):
            found.add((m.group(1), verb))
    return found


def dispatched(evaluate_text: str) -> tuple[set[tuple[str, str]], list[str]]:
    lines, wrong = set(), []
    for collection, package, verb in DISPATCH_RE.findall(evaluate_text):
        if VERB_COLLECTION[verb] != collection:
            wrong.append(f"{package}.{verb} is collected into {collection}")
        lines.add((package, verb))
    return lines, wrong


def problems(policy_texts: dict[str, str], evaluate_text: str) -> list[str]:
    want = exports(policy_texts)
    have, wrong = dispatched(evaluate_text)
    out = list(wrong)
    for package, verb in sorted(want - have):
        out.append(f"not dispatched: data.{package}.{verb} -- add "
                   f"`{VERB_COLLECTION[verb]} contains decision if some decision in data.{package}.{verb}`")
    # Builtin policies are compiled only when enabled, so a line for one that is absent here is fine.
    for package, verb in sorted(have - want):
        if ".builtins." not in package:
            out.append(f"dispatcher names data.{package}.{verb}, which no policy exports")
    return out


def selftest() -> int:
    policy = {"a.rego": "package cupcake.policies.claude.a\n\ndeny contains decision if {\n}\n",
              "b.rego": "package cupcake.policies.claude.b\n\nhalt contains decision if {\n}\n"}
    good = ("all_denials contains decision if some decision in data.cupcake.policies.claude.a.deny\n"
            "all_halts contains decision if some decision in data.cupcake.policies.claude.b.halt\n")
    assert problems(policy, good) == [], problems(policy, good)
    missing = good.splitlines()[0] + "\n"
    assert any("not dispatched: data.cupcake.policies.claude.b.halt" in p for p in problems(policy, missing))
    stale = good + "all_denials contains decision if some decision in data.cupcake.policies.claude.gone.deny\n"
    assert any("no policy exports" in p for p in problems(policy, stale))
    crossed = good.replace("all_halts", "all_denials")
    assert any("collected into all_denials" in p for p in problems(policy, crossed))
    print("check-cupcake-dispatcher selftest: ok")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    texts = {str(p): p.read_text(encoding="utf-8") for p in sorted(POLICY_DIR.rglob("*.rego"))}
    found = problems(texts, EVALUATE.read_text(encoding="utf-8"))
    if found:
        print("check-cupcake-dispatcher: FAIL -- a policy decision the live hook never reads")
        for line in found:
            print("  " + line)
        return 1
    print(f"check-cupcake-dispatcher: ok -- {len(exports(texts))} decision exports dispatched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
