#!/usr/bin/env python3
"""The PreToolUse live-run guard must refuse exactly what the stale-run sentinel tears down for.

`.cupcake/signals/live_er_run.sh` asks `scripts/er-stale-run-sentinel.sh verdict <path>` about the
pending edit, and `.cupcake/policies/claude/no_source_edit_during_live_run.rego` denies on its
`VERDICT TEARDOWN`. This test runs the real signal on a real event, feeds its output to the real
policy through `opa eval`, and checks the decision -- so a break in either half, or in the line
format between them, fails here.

No game is needed and none is touched: `LIVE_ER_RUN_PROFILES_OVERRIDE` stands in for the live run
with a throwaway profile, and the `verdict` mode never kills anything. The profile loads
`er_invasion_warp.dll`, whose dependency closure includes `er-game-base` and
`er-invasion-warp-core`, and the second embeds `docs/er-invasion-warp.dll-pool-test.toml` with
`include_str!`: a file under docs/ that a build reads. Until the 2.7.2.0 port that role was played
by `docs/recon/rva-map-1162-to-1170.data.tsv`, which `er-game-base/build.rs` compiled in; nothing
under `docs/recon/` is read by a build any more.

Added 2026-10-02, after the guard allowed any edit outside `crates/` while the sentinel tore a run
down for a build-read doc.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIGNAL = REPO / ".cupcake/signals/live_er_run.sh"
POLICY = REPO / ".cupcake/policies/claude/no_source_edit_during_live_run.rego"
QUERY = "data.cupcake.policies.claude.no_source_edit_during_live_run.deny"
TIMEOUT = 25

# (path relative to the repo, live?, expect deny?, why)
CASES = [
    ("crates/er-game-base/src/mem.rs", True, True, "crate source in the loaded closure"),
    ("AGENTS.md", True, False, "prose no build reads"),
    ("docs/er-invasion-warp.dll-pool-test.toml", True, True, "doc er-invasion-warp-core embeds"),
    ("docs/er-invasion-warp.dll-pool-test.toml", False, False, "same doc, nothing live"),
    ("docs/recon/boot-timing.tsv", True, False, "new recon data no build reads"),
    ("docs/recon/speffect-vfx-rows.json", True, False, "gitignored, not repo source"),
    ("scripts/er-stale-run-sentinel.sh", True, False, "host-side script"),
]

# The same edits against a run whose DLLs another checkout built. A worktree's copy of the
# sentinel used to tear down the main tree's run, matching loaded DLLs by basename alone.
FOREIGN_CASES = [
    ("crates/er-game-base/src/mem.rs", True, False, "crate source, DLL built by another tree"),
    ("docs/er-invasion-warp.dll-pool-test.toml", True, False, "build-read doc, DLL built by another tree"),
]

# A game running without me3 leaves no profile to read. Measured 2026-10-17: the sentinel answered
# `fallback-profile-undiscoverable` for every path before asking whether any build reads it, so
# this policy refused edits to scripts/ and docs/ that its own refusal text promises stay open.
UNDISCOVERABLE = "undiscoverable=game-live-without-me3"
UNDISCOVERABLE_CASES = [
    ("scripts/er-stale-run-sentinel.sh", True, False, "host-side script, game live without me3"),
    ("docs/foo.md", True, False, "docs prose, game live without me3"),
    ("crates/er-game-base/src/mem.rs", True, True, "crate source, game live without me3"),
    ("docs/er-invasion-warp.dll-pool-test.toml", True, True, "build-read doc, game live without me3"),
]


def run_signal(event: dict, profiles: str) -> str:
    env = dict(os.environ, LIVE_ER_RUN_PROFILES_OVERRIDE=profiles)
    out = subprocess.run(
        ["bash", str(SIGNAL)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=TIMEOUT,
    )
    return out.stdout


def denied(event: dict) -> bool:
    out = subprocess.run(
        ["opa", "eval", "-f", "json", "-d", str(POLICY), "-I", QUERY],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if out.returncode != 0:
        raise RuntimeError(f"opa eval failed: {out.stderr.strip()}")
    value = json.loads(out.stdout)["result"][0]["expressions"][0]["value"]
    return any(d.get("rule_id") == "ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN" for d in value)


def main() -> int:
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "signal-test.me3"
        profile.write_text('profileVersion = "v1"\n\n[[natives]]\npath = "C:/x/er_invasion_warp.dll"\n')
        tree_b = Path(tmp) / "tree-b"
        (tree_b / "target/x86_64-pc-windows-msvc/release").mkdir(parents=True)
        (tree_b / "Cargo.toml").write_text("")
        foreign = Path(tmp) / "foreign-tree.me3"
        foreign.write_text(
            'profileVersion = "v1"\n\n[[natives]]\n'
            f'path = "{tree_b}/target/x86_64-pc-windows-msvc/release/er_invasion_warp.dll"\n'
        )
        runs = (
            [(c, profile) for c in CASES]
            + [(c, foreign) for c in FOREIGN_CASES]
            + [(c, UNDISCOVERABLE) for c in UNDISCOVERABLE_CASES]
        )
        for (rel, live, want_deny, why), prof in runs:
            event = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(REPO / rel)},
            }
            signal = run_signal(event, str(prof) if live else "")
            event["signals"] = {"live_er_run": signal}
            got = denied(event)
            verdict = next((ln for ln in signal.splitlines() if ln.startswith("VERDICT ")), "-")
            status = "ok  " if got == want_deny else "FAIL"
            if got != want_deny:
                fails += 1
            print(f"{status} {'deny ' if got else 'allow'} {rel} ({why}; live={live}) {verdict.split(chr(9))[:2]}")
            # A live case must carry a verdict: without one the policy fell back to the closure
            # rule, and a pass would prove the fallback rather than the sentinel's decision.
            if live and verdict == "-":
                fails += 1
                print(f"FAIL no VERDICT line from the signal for {rel}:\n{signal}")
    if fails:
        print(f"test-live-er-run-signal FAILED ({fails})")
        return 1
    print("test-live-er-run-signal ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
