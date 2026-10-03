#!/usr/bin/env python3
"""The PreToolUse live-run guard must refuse exactly what the stale-run sentinel tears down for.

`.cupcake/signals/live_er_run.sh` asks `scripts/er-stale-run-sentinel.sh verdict <path>` about the
pending edit, and `.cupcake/policies/claude/no_source_edit_during_live_run.rego` denies on its
`VERDICT TEARDOWN`. This test runs the real signal on a real event, feeds its output to the real
policy through `opa eval`, and checks the decision -- so a break in either half, or in the line
format between them, fails here.

No game is needed and none is touched: `LIVE_ER_RUN_PROFILES_OVERRIDE` stands in for the live run
with a throwaway profile, and the `verdict` mode never kills anything. The profile loads
`er_r3_view.dll`, whose dependency closure includes `er-game-base`, which is the crate whose
`build.rs` compiles `docs/recon/rva-map-1162-to-1170.data.tsv` in.

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
    ("docs/recon/rva-map-1162-to-1170.data.tsv", True, True, "doc er-game-base/build.rs reads"),
    ("docs/recon/rva-map-1162-to-1170.data.tsv", False, False, "same doc, nothing live"),
    ("docs/recon/rva-map-1162-to-1170.functions.tsv", True, False, "named only in comments, gitignored"),
    ("scripts/er-stale-run-sentinel.sh", True, False, "host-side script"),
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
        profile.write_text('profileVersion = "v1"\n\n[[natives]]\npath = "C:/x/er_r3_view.dll"\n')
        for rel, live, want_deny, why in CASES:
            event = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(REPO / rel)},
            }
            signal = run_signal(event, str(profile) if live else "")
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
