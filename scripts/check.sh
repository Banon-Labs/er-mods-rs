#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

bash "$repo_root/scripts/check-no-local-main-commits.sh"
python3 "$repo_root/scripts/check-no-timeouts.py"
python3 "$repo_root/scripts/check-no-committed-build-artifacts.py" --selftest
python3 "$repo_root/scripts/check-no-committed-build-artifacts.py"
python3 "$repo_root/scripts/test-no-timeouts.py"
bash "$repo_root/scripts/test-git-pre-push-block-main.sh"
# Telemetry honesty: no counter may be READ to emit an oracle while written nowhere. Selftest first,
# so the gate is never trusted on its own say-so (er-effects-rs-56fx).
python3 "$repo_root/scripts/check-oracle-writers.py" --selftest
python3 "$repo_root/scripts/check-oracle-writers.py"
# The workspace uses `../fromsoftware-rs` PATH dependencies, and CI clones that sibling at ONE
# pinned revision while a developer's is whatever they have checked out -- often a fork carrying
# types upstream does not have. Everything below compiles against the developer's copy, so it
# cannot see the divergence: PRs #322/#323 were green here and failed CI outright on
# `unresolved import eldenring::cs::MsgRepositoryImp`. This is the cheap text check for that.
python3 "$repo_root/scripts/check-fromsoftware-symbols.py" --selftest
python3 "$repo_root/scripts/check-fromsoftware-symbols.py"
python3 "$repo_root/scripts/check-launch-guardrails.py" --audit
python3 "$repo_root/scripts/check-runtime-probe-contract.py" --audit
python3 "$repo_root/scripts/test-runtime-probe-contract.py"
python3 "$repo_root/scripts/test-er-readiness-watch.py"
python3 "$repo_root/scripts/test-save-slot-oracle.py"
python3 "$repo_root/scripts/test-detect-proc.py"
python3 "$repo_root/scripts/test-semaphore-watchdog.py"
python3 "$repo_root/scripts/test-input-harness-static.py"
python3 "$repo_root/scripts/test-wall-of-text-classifier.py"
# The SessionStart/PreCompact prime hook must stay small enough that the harness INLINES it.
# At 2452 memories it emitted 157.4 KB, which Claude Code persisted to a file and replaced
# with a 2 KB preview -- so the priming content never reached the agent while still costing
# a large slice of every session, PreCompact included. Size is the whole feature, so it is a
# gate: this drives the real generator against a synthetic 6000-memory store.
python3 "$repo_root/scripts/test-beads-prime-size.py"
python3 "$repo_root/scripts/check-retired-button-labels.py"
python3 "$repo_root/scripts/check-autoload-happy-path.py"
python3 "$repo_root/scripts/test-autoload-happy-path.py"
# An unresolvable staged save is terminal for the process. Pin the caller-side state transitions and
# the nonzero recurrence semaphore so the 120,959-call identical-rejection loop cannot return.
python3 "$repo_root/scripts/check-own-load-save-rejection-guard.py" --selftest
python3 "$repo_root/scripts/check-own-load-save-rejection-guard.py"
python3 "$repo_root/scripts/check-yk0j-runtime-proof.py" --selftest
python3 "$repo_root/scripts/check-user-release-package.py"
python3 "$repo_root/scripts/check-native-continue-static.py"
python3 "$repo_root/scripts/check-menu-constructor-static.py"
# RVA 0 is the PE header: the 1.16.2 -> 1.17 resolver refuses it every time, forever, at the call
# site's own rate. One `game_rva(0)` used only to fetch the module base sat on the 4 Hz telemetry
# write and logged 339,764 anonymous refusals in a single session. Selftest first.
python3 "$repo_root/scripts/check-no-rva-zero.py" --selftest
python3 "$repo_root/scripts/check-no-rva-zero.py"
python3 "$repo_root/scripts/check-env-gate-comments.py"
python3 "$repo_root/scripts/test-env-gate-comments.py"
python3 "$repo_root/scripts/check-marker-file-gates.py"
python3 "$repo_root/scripts/test-marker-file-gates.py"
python3 "$repo_root/scripts/check-reload-trace-policy.py" --audit
python3 "$repo_root/scripts/check-windows-proof-render.py"
python3 "$repo_root/scripts/test-windows-proof-render.py"
python3 "$repo_root/scripts/test-windows-proof-render-smoke-verdict.py"
command -v cupcake >/dev/null 2>&1 || {
	echo "missing required command: cupcake" >&2
	exit 127
}
cupcake validate --log-level error
python3 "$repo_root/scripts/test-cupcake-policies.py"
# EVERY CUPCAKE GUARD IN THIS REPO WAS PARTLY OR WHOLLY INERT UNTIL 2026-08-22, and the suite was
# green the whole time. `cupcake eval` does not run policies in the OPA interpreter -- it compiles
# them to WASM and executes them in its own runtime, where a builtin the runtime has no host
# implementation for (`sprintf`, `regex.find_n`) returns UNDEFINED instead of raising. The rule body
# fails, the decision set comes back empty, and cupcake reports a clean ALLOW with exit code 0. The
# old coverage could not see it: the signal tests ran the shell scripts alone, the .rego tests ran
# the INTERPRETER, and the only real-binary test used PreToolUse events -- so all five Stop guards
# (36 days), the launch guard's non-`command` payload scan (63 days) and the tmp-script guard's Bash
# branch were dead in production while passing every check.
#
# First gate: every builtin the policies call must be PROVEN to execute in the live WASM runtime, and
# a builtin with no probe recipe is a hard failure -- so a new policy reaching for an unverified
# builtin breaks the build instead of quietly not firing. Selftest first, so the gate is never
# trusted on its own say-so.
python3 "$repo_root/scripts/check-cupcake-wasm-builtins.py" --selftest
python3 "$repo_root/scripts/check-cupcake-wasm-builtins.py"
# Second gate: drive real transcripts through the real hook commands out of .claude/settings.json
# and assert the halt actually comes back -- plus a clean turn that must still be allowed, because a
# guard that halts everything wedges every session just as badly as one that halts nothing. It also
# drives UserPromptSubmit, where wall_of_text now lives: that rule must NOT halt (a Stop verdict is
# printed to the user, and it fires after the answer is already on screen, so halting buys a third
# reading instead of saving one) and its correction must come back on the invisible
# additionalContext channel.
# Third gate: a permission mode cupcake does not recognise must not silently disable every guard.
# cupcake 0.5.2 exits 1 on `permission_mode: "auto"`, which Claude Code now sends -- so on
# 2026-08-24 every hook in this repo failed and every policy went inert for a whole session, with
# this suite green throughout. scripts/cupcake-hook.sh normalises the mode and pins the log level;
# this proves a denial still denies through it.
python3 "$repo_root/scripts/test-cupcake-hook-shim.py"
python3 "$repo_root/scripts/test-cupcake-stop-guards.py"
python3 "$repo_root/scripts/test-authority-agreement-signal.py"
python3 "$repo_root/scripts/test-idle-hold-signal.py"
python3 "$repo_root/scripts/test-unexecuted-promise-signal.py"
python3 "$repo_root/scripts/test-native-ownership-vocab-signal.py"
python3 "$repo_root/scripts/test-stall-on-friction-signal.py"
python3 "$repo_root/scripts/test-wall-of-text-signal.py"
command -v opa >/dev/null 2>&1 && opa test "$repo_root/.cupcake/system/commands.rego" "$repo_root/.cupcake/policies/claude/no_authority_agreement.rego" "$repo_root/.cupcake/policies/claude/no_authority_agreement_reminder.rego" "$repo_root/.cupcake/tests/no_authority_agreement_test.rego" "$repo_root/.cupcake/tests/no_authority_agreement_reminder_test.rego" "$repo_root/.cupcake/policies/claude/idle_hold.rego" "$repo_root/.cupcake/policies/claude/idle_hold_reminder.rego" "$repo_root/.cupcake/tests/idle_hold_test.rego" "$repo_root/.cupcake/tests/idle_hold_reminder_test.rego" "$repo_root/.cupcake/policies/claude/native_ownership_vocab_reminder.rego" "$repo_root/.cupcake/tests/native_ownership_vocab_reminder_test.rego" "$repo_root/.cupcake/policies/claude/block_manual_pgrep.rego" "$repo_root/.cupcake/tests/block_manual_pgrep_test.rego" "$repo_root/.cupcake/policies/claude/bash_elden_ring_launch_guard.rego" "$repo_root/.cupcake/tests/bash_elden_ring_launch_guard_test.rego" "$repo_root/.cupcake/policies/claude/block_askuserquestion.rego" "$repo_root/.cupcake/tests/block_askuserquestion_test.rego" "$repo_root/.cupcake/policies/claude/block_askuserquestion_reminder.rego" "$repo_root/.cupcake/tests/block_askuserquestion_reminder_test.rego" "$repo_root/.cupcake/policies/claude/no_stall_on_friction.rego" "$repo_root/.cupcake/tests/no_stall_on_friction_test.rego" "$repo_root/.cupcake/policies/claude/no_unexecuted_promise.rego" "$repo_root/.cupcake/tests/no_unexecuted_promise_test.rego" "$repo_root/.cupcake/policies/claude/wall_of_text.rego" "$repo_root/.cupcake/tests/wall_of_text_test.rego"
command -v opa >/dev/null 2>&1 && opa test "$repo_root/.cupcake/system/commands.rego" "$repo_root/.cupcake/policies/claude/git_block_main_push.rego" "$repo_root/.cupcake/tests/git_block_main_push_test.rego"
command -v opa >/dev/null 2>&1 && opa test "$repo_root/.cupcake/system/commands.rego" "$repo_root/.cupcake/policies/claude/git_block_main_commit.rego" "$repo_root/.cupcake/tests/git_block_main_commit_test.rego"
# The shared executed-text decomposition every git guard now reads (bd
# er-effects-rs-dt2e). It is the one place that decides what counts as EXECUTED
# rather than quoted, so a regression here silently re-opens four guards at once.
command -v opa >/dev/null 2>&1 && opa test "$repo_root/.cupcake/system/commands.rego" "$repo_root/.cupcake/tests/commands_test.rego"
# These two had test files but no runner: written, committed, and never executed
# once. Both carried the same wrapper hole as the guards above, and neither test
# suite could have caught it because neither ever ran.
command -v opa >/dev/null 2>&1 && opa test "$repo_root/.cupcake/system/commands.rego" "$repo_root/.cupcake/policies/claude/git_require_fresh_origin_main.rego" "$repo_root/.cupcake/tests/git_require_fresh_origin_main_test.rego"
command -v opa >/dev/null 2>&1 && opa test "$repo_root/.cupcake/system/commands.rego" "$repo_root/.cupcake/policies/claude/builtins/git_block_no_verify.rego" "$repo_root/.cupcake/tests/git_block_no_verify_test.rego"
python3 "$repo_root/scripts/check-no-lossy-utf8.py"
# A NUL-terminator walk over a pointer we did not create is how both testers' games died on
# 2026-08-23 (bd er-effects-rs-uuly): `CStr::from_ptr` -> `strlen` -> AV on a garbage NON-null
# `key` from Steam/Seamless, past a guard that only checked for null. Four more sites of the same
# shape were still live when that crash's own fix was reviewed, so the invariant is a gate rather
# than a habit. Selftest first, so the gate is never trusted on its own say-so.
python3 "$repo_root/scripts/check-no-unguarded-cstr-from-ptr.py" --selftest
python3 "$repo_root/scripts/check-no-unguarded-cstr-from-ptr.py"
# A detour's expected prologue must be GENERATED from named iced-x86 instructions in a build.rs,
# never hand-typed: `mov rax, rsp` has two legal encodings, the game ships 48 8b c4, an assembler
# left to choose emits 48 89 e0, and a prologue that is one byte off byte-checks its own hook off
# on every launch while looking perfectly built. Selftest first, so the gate is never trusted on
# its own say-so. The shared generator + what verifies it live in build-support/prologue_build.rs;
# rustfmt cannot see that file through `include!`, so it is checked explicitly here.
python3 "$repo_root/scripts/check-prologue-bytes.py" --selftest
python3 "$repo_root/scripts/check-prologue-bytes.py"
rustfmt --edition 2024 --check "$repo_root/build-support/prologue_build.rs"
# LINT PARITY WITH ../fromsoftware-rs. Standing user requirement (2026-08-21): this code must
# be AT LEAST as strict as the parent project. Cargo cannot inherit that -- `[lints] workspace =
# true` resolves only against THIS workspace root and lint levels never propagate from a path
# dependency -- so parity is asserted rather than inherited. The gate READS upstream's CI and
# manifests, so it goes red when upstream gets stricter instead of us finding out months later.
# It also fails if a blanket `-Awarnings` returns to .cargo/config.toml, which silently defeats
# `[workspace.lints.rust] warnings = "deny"` (measured, not theorised). Selftest first, so the
# gate is never trusted on its own say-so.
python3 "$repo_root/scripts/check-lint-parity.py" --selftest
python3 "$repo_root/scripts/check-lint-parity.py"
# FNV-1a has one zero-dependency owner below every caller. Prove the scanner catches copied
# implementations before trusting the live ownership check.
python3 "$repo_root/scripts/check-fnv1a-owner.py" --selftest
python3 "$repo_root/scripts/check-fnv1a-owner.py"
# One game address must have exactly ONE literal declaration. Divergent names for one address are
# divergent CLAIMS about what it is; three turned out to be wrong RE facts shipping in the DLL
# (bd rva-67b750-is-save-write-not-continue-load-2026-08-01,
# rva-4852f88-is-saveload2-slsystemimpl-not-fd4-io-worker-2026-08-01). Selftest first, so the gate
# is never trusted on its own say-so.
python3 "$repo_root/scripts/check-rva-alias-drift.py" --selftest
python3 "$repo_root/scripts/check-rva-alias-drift.py"
# The in-memory CS::ProfileSummary record layout is cross-cutting RAM ABI, not feature-owned data.
# Keep its typed definition in er-game-base and reject copied numeric offsets/formulas elsewhere.
python3 "$repo_root/scripts/check-profile-summary-layout.py" --selftest
python3 "$repo_root/scripts/check-profile-summary-layout.py"
# A log describes exactly ONE process run. er-invasion-warp appended to a fixed filename, so
# twelve launches became one 565KB file and a count over it read as one run's behaviour. Every
# appending opener must route through er-game-base's one-shot truncation. Selftest first, so the
# gate is never trusted on its own say-so.
python3 "$repo_root/scripts/check-fresh-run-logs.py" --selftest
python3 "$repo_root/scripts/check-fresh-run-logs.py"
# The refactor/move DLL byte-identity gate (.github/workflows/refactor-byte-identical.yml) has two
# halves that can each rot silently: the trigger (which PRs it applies to) and the comparator (what
# counts as a difference). Both are tested here -- a gate whose scope and whose normalizer are
# untested is decorative.
bash "$repo_root/scripts/test-pr-refactor-scope.sh"
python3 "$repo_root/scripts/test-dll-byte-identical.py"
python3 "$repo_root/scripts/test-release-workflow.py"
python3 "$repo_root/scripts/check-rust-file-sizes.py"
python3 "$repo_root/scripts/check-experiments-rustfmt.py"
# THE EXPERIMENTS RATCHET. er-quickload is being extracted INTO crates until it is a thin
# shim that bundles them, so the line total under crates/er-quickload/src/experiments/** may
# shrink but never grow; the roadmap's ledger row is the high-water mark. It is a ratchet, not
# a freeze: edits are free, only NET GROWTH is refused, and `--refresh` accepts growth in one
# command -- the value is that accepting it becomes a reviewable diff to the ledger instead of
# the invisible default. Measured on PR #367, 62% of 1,553 added lines already landed in
# extracted crates with no enforcement, pulled there by the host-seam pattern; what that
# pattern does NOT catch is a new module born inside the shim, which is what this refuses.
# Selftest first, so the gate is never trusted on its own say-so.
python3 "$repo_root/scripts/check-crate-extraction-roadmap.py" --selftest
python3 "$repo_root/scripts/check-crate-extraction-roadmap.py"
# THE STALE-CALL RATCHET (2026-08-28). The 1.17 build gate resolves DETOUR addresses and refuses
# the ones it cannot place. Nothing looked at a game address reached as a direct CALL --
# `transmute(base + SOME_RVA)` -- and that is the worse of the two: a refused detour makes one
# feature inert and logs why, while a stale call transfers control into whatever now occupies
# those bytes and faults with no unwind and no record naming anything of ours. ONE such site is
# left; this refuses a second while it is converted to er_game_base::mem::game_rva.
#
# Two defects in the gate itself were fixed on 2026-08-30, and both had made it report a number
# that was not the truth. It required the constant to be spelled `*RVA*`, so forty converted-since
# sites in er-build-import-runtime named `GET_MAIN_PLAYER_STATS` were never visible to it; and it
# matched raw file text, so two of the three rows in its baseline were DOC COMMENTS describing the
# hazard. The baseline was re-derived from scratch rather than edited, because a ratchet is only
# meaningful when every row in it is a finding somebody consciously accepted.
# THE SHARED SYMBOL RESOLVER, gated first because two gates now ask it their central question.
# It answers "which symbol declares this address" by evaluating VALUES -- literal consts and
# statics, enum discriminants, `use X as Y` aliases, `const A = path::B` indirection,
# module-qualified names, arrays, Range bands, bare hex literals in table fields -- rather than by
# searching for one spelling, which is how both callers were wrong on 2026-08-30. Its controls
# include an address declared ONLY as an enum discriminant, checked against the frozen pre-fix
# matcher so the proof that the widening is load-bearing cannot quietly widen with it.
python3 "$repo_root/scripts/rva_symbols.py" --selftest
python3 "$repo_root/scripts/check-stale-rva-calls.py" --selftest
python3 "$repo_root/scripts/check-stale-rva-calls.py"
# THE HAND-ROW GUARD (2026-08-30). `select-needed-1170-rows.py --refresh` rewrites
# docs/recon/rva-map-1162-to-1170.needed.tsv WHOLESALE, so until today a pair somebody derived by
# hand and typed in -- exactly the short .pdata records and body-changed functions the machine map
# cannot carry -- was deleted by the next refresh at exit 0, with nothing printed and nothing in
# the diff anyone reads. The loss does not read as a loss afterwards: the address reads as one that
# was never mapped. The selftest asserts on a fabricated table that such a row is carried forward
# and that a pair contradicting the function map stops the write instead of being merged. Only the
# selftest runs here; the `--refresh`/current check is not wired in, because the tracked file
# tracks constants that land continuously and would make this gate red on somebody else's commit.
python3 "$repo_root/scripts/select-needed-1170-rows.py" --selftest
# THE SILENT-COMPARISON GATE (2026-08-30). The two gates above catch stale addresses that are
# DETOURED or CALLED; both of those announce themselves when they fail. A stale address that is
# only COMPARED does not. `trace_first_game_caller_rva()` / `callstack_contains_game_rva()` take a
# return address off the live stack, subtract the module base, and test it against a 1.16.2
# constant -- nothing is resolved, so nothing is refused, and on a moved build the comparison
# simply stops matching with no log line anywhere. Nine such sites existed; two of them were
# user-visible features that were dead on 1.17 in total silence (the three cloned System>Quit rows,
# and the title FadeIn suppression). The constants are mid-function return addresses, which the
# address map structurally cannot carry, so the fix is to name the containing function and add the
# offset at the use site -- see the script's docstring and `scripts/derive-callsite-1170.py`.
python3 "$repo_root/scripts/check-no-stale-callsite-rva.py" --selftest
python3 "$repo_root/scripts/check-no-stale-callsite-rva.py"
python3 "$repo_root/scripts/derive-callsite-1170.py" --selftest
# THE SILENT-REFUSAL GATE (2026-08-28). Every cdylib statically links its own er-hook/er-game-base,
# so the log sink is a PER-DLL static and a DLL that never installs one says nothing when the build
# gate refuses an address. Measured cost: er-armament-icons reported four
# MH_ERROR_UNSUPPORTED_FUNCTION failures -- a code that means BOTH "MinHook cannot hook this" and
# "the gate refused the address" -- for addresses including one that IS in the verified translation
# table, and a whole game run could not tell the two apart.
python3 "$repo_root/scripts/check-hook-log-sink.py" --selftest
python3 "$repo_root/scripts/check-hook-log-sink.py"
# THE TRANSLATED-TARGET AUDIT. `verify-rva-map-1170.py` proves the mapped 1.17 code is the same
# function; this proves the destination is a real function ENTRY, by the calls and pointers the
# 1.17 image itself makes to it, and that MinHook's five-byte patch is safe there. Its selftest
# calibrates on the 27 addresses this project hooks successfully on 1.16.2 today -- the previous
# implementation of the entry check called 20 of those mid-function, and calibration is what
# caught it.
python3 "$repo_root/scripts/audit-1170-hook-targets.py" --selftest
# THE MID-FUNCTION GATE. `NEITHER-ENTRY` in a verdict table is TWO verdicts wearing one name: a
# leaf function the x64 ABI let omit unwind data (safe to hook), and an address INSIDE another
# function (MinHook writes five bytes into a live body). build.rs accepts the word for detours
# because refusing it would throw away every legitimate leaf, and nothing downstream re-checked
# which sense it was. MEASURED 2026-08-30, in one merge wave: six mid-function addresses reached
# or nearly reached the verified table, every one of them carrying `IDENTICAL` over 20-94
# instructions -- because a mid-function address verifies BETTER than a real entry, sitting in a
# neighbourhood that did not change. One (0x140aec480, +0x360 inside 0x140aec120) was merged with
# its own note saying `containing-fn-offset-0x360`, while the real entry (0x140aec570) was already
# written down in crates/er-title-flow, and crates/er-reload-trace carried a raw `rva: 0xaec480`
# HookSpec that would have consumed the licence (since removed, on the same day, by a different
# agent who reached the same address from the other direction).
#
# The inversion, stated once: that impostor row is IDENTICAL over 56 instructions and would carry a
# detour; the CORRECT pair 0xaec570 -> 0xaed880 is IDENTICAL over 9 and is refused one by
# MIN_VERIFIED_INSNS. The wrong address had the better-looking evidence.
#
# So a clean verdict is not evidence of a valid hook target, and no reviewer can be the check.
# Selftest first, so the gate is never trusted on its own say-so: it builds a .pdata table in
# memory and proves BOTH senses of NEITHER-ENTRY are separated, then drives the whole failure path
# on a synthetic mid-function row. The live half skips, saying so, on a checkout without the
# de-Arxan'd images (they are untracked by policy).
python3 "$repo_root/scripts/classify-1170-entry-kind.py" --selftest
python3 "$repo_root/scripts/classify-1170-entry-kind.py" --fail-on-mid
# THE DUPLICATE-ROW GATE (2026-08-30). er-game-base/build.rs concatenates four address ledgers and
# finishes with `rows.sort_unstable(); rows.dedup_by_key(|(old, _)| *old)`. `sort_unstable` orders
# by the WHOLE tuple, so among rows sharing a source the survivor is the one with the numerically
# SMALLEST destination -- a choice nobody made, applied silently, with the losing row leaving no
# trace anywhere. Nothing gated ledger duplicates: check-rva-alias-drift.py gates Rust
# DECLARATIONS, the double-resolve gate below gates a destination that is also somebody's source,
# and a verdict table verifies a pair without asking whether it was written down twice. MEASURED
# 2026-08-30: the curated ledger declared 0x1408c47c0 and 0x1409b72b0 twice each. Both pairs
# agreed, so the maps were right by luck -- and the two 0x1408c47c0 rows disagreed in prose about
# whether its .pdata record is a chained continuation (it is a ROOT), which is exactly the drift a
# second row hides.
#
# A GENERATED ledger legitimately repeats a source: select-needed-1170-rows.py emits one row per
# DECLARING NAME, 85 of them today. So the repeat rule applies only to the CURATED ledger, and the
# selftest carries a false-positive control that fails if anyone widens it. The selftest plants
# each defect into a COPY of the real tracked ledgers and requires the verdict to flip; 7 of 7
# mutations of the gate's own rules are caught by it.
python3 "$repo_root/scripts/check-no-duplicate-ledger-rows.py" --selftest
python3 "$repo_root/scripts/check-no-duplicate-ledger-rows.py"
# THE DOUBLE-RESOLVE GATE. A row's 1.17 DESTINATION can also be some other row's 1.16.2 SOURCE,
# and then translating an address twice does not fail -- it SUCCEEDS, returning a third, unrelated
# function. The table is keyed by the 1.16.2 side and an address carries no label saying which side
# it came from, so the second lookup cannot tell an already-translated address from an untranslated
# one: nothing errors, nothing logs, and a hook lands somewhere it was never meant to. MEASURED
# 2026-08-30: er-reload-trace's `native_submit` resolved 0x7ac890 -> 0x7ad710 in er-hook, which
# handed the RESOLVED address to the product's union register, which resolved again -- and
# 0x7ad710 is itself a tracked source, -> 0x7ae590. Both rows are BYTE-IDENTICAL/BOTH-ENTRIES, so
# no verdict, audit or entry check had anything to object to.
#
# That call path was restructured to resolve exactly once per branch and
# `register_shared_hook_resolved` was deleted, but single-resolve is a CONVENTION across six crates
# and the ledgers went from 80 to 470+ rows in a day. This is the machine check the convention did
# not have: er-game-base's `verified_map_is_idempotent` reads like it covers this and cannot -- it
# filters to rows where `from != moved`, then asks a predicate requiring `from == moved`, so it is
# a tautology. Selftest first, so the gate is never trusted on its own say-so; it drives the whole
# path over synthetic ledgers and re-reads every admission rule out of build.rs rather than
# copying it, because a copied `EXHAUSTIVE_VERDICTS` already reported one of these tables as 42
# rows instead of 374.
#
# Its claimed-by-no-feature test was fixed on 2026-08-30, and that one had passed WHILE
# recommending a destructive action: it searched for `const NAME: usize = 0x<addr>;`, and printed
# "claimed by no feature: deleting it removes this collision at zero cost" whenever it found none.
# 0xb0d400 is declared `MenuJobWait = 0x00b0d400` inside an enum, reached as
# TITLE_MENU_JOB_WAIT_RVA, with live uses on the autoload path -- the shape it demanded never
# occurs, so its advice would have deleted a working feature's address. The answer now has three
# values (CLAIMED / PROVEN UNCLAIMED / NOT PROVEN) and only the middle one licenses a deletion,
# and the baselined NOTES are held to the same rule since a note is what a reader actually sees.
python3 "$repo_root/scripts/check-1170-translation-collisions.py" --selftest
python3 "$repo_root/scripts/check-1170-translation-collisions.py"
# THE SECOND OPINION ON THE DATA MAP'S VTABLE ROWS. `map-data-rvas-1162-to-1170.py` carries every
# datum by the CODE that references it, so each row depends on the function map being right about
# one function. RTTI depends on none of that: a vtable's [base-8] points at its
# CompleteObjectLocator, whose TypeDescriptor holds the class's mangled name, and a name occurring
# once per image identifies its vtable outright. Two methods with disjoint failure modes.
#
# This is the gate the failure it guards did not have. `TITLE_OWNER_VTABLE_RVA` is `CS::TitleStep`
# in 1.16.2 and not a vtable at all at the same address in 1.17, and its three scans had been
# finding no title owner, forever, with no refusal line and no fault -- a wrong data address does
# not crash, the comparison simply never matches. 31 of the map's rows are vtables and all 31 are
# checked here. The selftest runs first and carries its own negative control (every destination
# shifted onto the next vtable must be rejected); `--prove-selftest-catches-regression` blinds the
# matcher and requires the selftest to go red, so a green here cannot be vacuous. SKIPs at exit 0
# without the two gitignored images.
python3 "$repo_root/scripts/verify-data-rvas-by-rtti.py" --selftest
python3 "$repo_root/scripts/verify-data-rvas-by-rtti.py" > /dev/null
# THE WORK-LIST AUDIT. The inventory that says how much of the 1.17 migration is left classified
# every `*_RVA` constant in a cdylib as an eldenring.exe address, by NAME. Four of them are
# Seamless Co-op's, added to `GetModuleHandleA("ersc.dll")`, and an ELDEN RING patch does not move
# them: translating one through the game map and detouring the result would have put five bytes of
# jmp into an unrelated game function. Two agents caught it by reading the code and no checker did.
# The selftest pins the four foreign addresses, the plausibility bounds that are not addresses at
# all, and two REAL game addresses as the control, so an exclusion that ate real work fails too.
python3 "$repo_root/scripts/audit-1170-coverage-inventory.py" --selftest
# THE VERSION GATE. On 2026-08-29 every product DLL died within a second of loading, and it took
# eight game launches to find out why: `ERGameVersion::from_lang_version` in the sibling
# fromsoftware-rs checkout accepted only "2.6.2.0" and "2.6.2.1", the game had become 2.7.0.0, and
# `eldenring::rva::get()` therefore panicked inside a LazyLock on whichever thread first touched a
# singleton -- surfacing as eight unattributed rust_panics with the message nowhere a human looks.
# Both halves of that comparison are readable off the disk, so it never needed a game to catch.
python3 "$repo_root/scripts/check-game-version-supported.py" --selftest
python3 "$repo_root/scripts/check-game-version-supported.py"
python3 "$repo_root/scripts/check-markdown-code-blocks.py" "$repo_root/README.md"
cargo fmt --all --manifest-path "$repo_root/Cargo.toml" -- --check
shellcheck "$repo_root/.githooks/pre-push"
shellcheck "$repo_root/scripts/check-no-local-main-commits.sh"
shellcheck "$repo_root/scripts/git-pre-push-block-main.sh"
shellcheck "$repo_root/scripts/test-git-pre-push-block-main.sh"
shellcheck "$repo_root/scripts/pr-refactor-scope.sh"
shellcheck "$repo_root/scripts/test-pr-refactor-scope.sh"
shellcheck "$repo_root/scripts/probe-dll-build-determinism.sh"
shellcheck "$repo_root/scripts/hooks/pre-push"
shellcheck "$repo_root/scripts/stage-autoload-release.sh"
shellcheck "$repo_root/scripts/run-product-continue-direct-probe.sh"
shellcheck "$repo_root/scripts/run-me3-product-smoke.sh"
shellcheck "$repo_root/scripts/run-windows-proof-render-smoke.sh"
shellcheck "$repo_root/scripts/run-portrait-dll-standalone-smoke.sh"
shellcheck "$repo_root/scripts/build-invasion-warp-profile.sh"
shellcheck "$repo_root/scripts/check-rust-build.sh"
shellcheck "$repo_root/scripts/er-stale-run-sentinel.sh"
shellcheck "$repo_root/scripts/er-tree-bisect-run.sh"
shellcheck "$repo_root/scripts/beads-prime.sh"
shellcheck "$repo_root/scripts/test-er-stale-run-sentinel-e2e.sh"

# The stale-run sentinel kills a live game when an edit feeds a DLL that run loaded, so BOTH
# directions are load-bearing: a name it cannot match is a run it cannot stop, and a path it
# misclassifies is either contaminated evidence or a run killed mid-measurement. The selftest proves
# the classifier in both directions (a crate feeding a loaded DLL and its transitive dependencies
# tear down; host-side scripts, policy, docs and crates building UNLOADED DLLs do not), plus the
# `/proc/<pid>/comm` 15-character truncation handling end to end against a real process.
#
# It deliberately never calls `teardown` -- a real game may be live while this gate runs. The other
# half (/proc profile discovery + the kill itself) is proven by
# scripts/test-er-stale-run-sentinel-e2e.sh, which is NOT run here because it is destructive by
# design; run it by hand, and it refuses if a real run is live.
bash "$repo_root/scripts/er-stale-run-sentinel.sh" --selftest

# LAUNCH REACHABILITY GATE (2026-08-04). A launch takes the user's screen and yields one recording;
# spending it on a predicate that CANNOT fire returns a clean-looking run that proves nothing. The
# selftest runs first and includes the concrete regression -- the `requestCode latches 2` terminator
# that shipped and could never execute -- so the gate is never trusted on its own say-so.
python3 "$repo_root/scripts/er-launch-gate.py" --selftest

# Host-buildable GFx codec + derived-movie proof gates. These are the only place the runtime GFx
# transforms are checked (the Windows-target `cargo xwin test --lib` below cannot reach an integration
# test), and they carry the System->Quit grid-geometry gate: the two added rows are navigable only
# because the derived movie names them `Item_1_0`/`Item_1_1`. Movie-reading tests SKIP when the local
# extraction corpus is absent, so this is safe on a machine without it.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-gfx

# Scaleform's native hook owner stays host-testable at its dependency-injection seam even
# before R24 moves the first hook family. The er-gfx architecture test above enforces the
# one-way codec dependency; this test proves the narrow callback remains inert-by-default
# and install-once.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-scaleform-hooks --lib

# er-save-loader's host-portable save decoding: BND4 slot bodies + the PlayerGameData
# stats/vitals reads the loading-screen stats panel sources pre-mount. Save-byte tests are
# corpus-gated (skip when local save-files/ fixtures are absent; game-derived bytes are
# never versioned).
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-save-loader

# Host simulation for the own-load terminal-rejection state machine. It drives the preserved
# 120,959-tick churn shape and requires exactly one resolver call plus zero repeated rejections.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-save-redirect --lib

# er-loading-portrait-core's host-portable stats-line layer: proves the UNIFIED loading-screen
# stats layout (one five-line panel whether the values came from the save slot or live
# PlayerGameData, bd er-effects-rs-qic7). The bitmap-geometry test is corpus-gated on the
# extracted menu font (ER_FONT_GFX_PATH overridable) and skips when absent.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-loading-portrait-core

# The save-picker crate split (docs/plans/save-picker-crate-extraction.md). The row model
# and the quit-row resolver are pure logic, so the HOST run is their real coverage -- the
# whole point of the extraction is that state machines which today need a game launch
# become `cargo test`-able. The DLL shells' tests prove the host seam installs exactly
# once. `check-rust-build.sh` keeps all four building for the shipping target.
cargo test --manifest-path "$repo_root/Cargo.toml" \
	-p er-save-picker-core -p er-save-picker -p er-quit-menu-core -p er-quit-menu

# The ProfileSummary crate split. Its two host-portable decisions are the ones that were
# untestable while they lived in the shim: whether a record describes a real character (the
# predicate the whole autoload chain turns on) and the throttle standing between a ~26 MB file
# read and a per-frame ~26 MB file read. `check-rust-build.sh` keeps the windows-only half --
# the serialized-save reader and the record writer -- building and RUNNING on the shipping target.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-profile-summary-core

# The world-map invasion-spawn warp crates (docs/plans/world-map-invasion-warp.md). The
# catalog, the block grouping, the BlockId disk/memory byte-order conversion and the on-disk
# `.aip` decoder are all pure logic, so the HOST run is their real coverage -- that
# testability is the point of the crate split. The corpus test that decodes the 365 real
# `.aip` files skips when the local extraction is absent (game-derived bytes are never
# versioned). `check-rust-build.sh` keeps both crates building for the shipping target.
cargo test --manifest-path "$repo_root/Cargo.toml" \
	-p er-invasion-warp-core -p er-invasion-warp

# er-net-effects's host-portable modules. Six of them are ungated with a comment saying
# "so its tests run on the host" -- and until this line existed NOTHING ran them: the workspace
# pins `default-members` to er-quickload, so a bare `cargo test` never selects this crate and the
# windows-target `cargo xwin test --lib` in check-rust-build.sh selects er-quickload only. 42
# tests sat inert. The load-bearing one now is `selector_gate`: it decides whether this DLL may
# take the player's arrow keys away from the game, which is not a claim to leave to review.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-net-effects --lib

# er-invasion-path's host-portable half: the world->screen projection, the distance ramp, the
# per-player colour assignment and the config parser. Every one of those can be wrong without
# crashing anything -- a projection off by the aspect ratio just looks like "the overlay is
# broken" -- and none of it is reachable from any other gate: the crate is windows-only to ship,
# and the workspace pins `default-members` to er-quickload, so a bare `cargo test` never selects
# it. The near-plane trim regression this caught on the way in is exactly the class of bug that
# otherwise costs a game launch to find.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-invasion-path

# The build importer's HOST half: planner-JSON parsing, the name -> item-id catalogue lookup, the
# grant/equip plan, and the `er-quickload.toml` `build_url` scan. It was absent from this gate while
# it had 23 tests, so the whole mapping could regress silently -- the game-side crates
# (er-build-import-runtime, er-build-import) are windows-only and prove none of it. There is
# nothing to run here for those two: `check-rust-build.sh` keeps them building for the shipping
# target, and the DLL half is proven in game.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-build-import-core

# Two gates the repo had no equivalent of, both reading the INSTALLED regulation.bin. 1.17 added
# CharaInitParam rows 3010/3011 -- two new starting classes -- and nothing in Rust could notice:
# STARTING_CLASSES was a [&str; 10], so build export answered None for an Idus Knight and import
# never set the class (er-effects-rs-d3jz). The effects.json check replaces `er-param-inspect
# validate`, which needs a Smithbox checkout and a dotnet bridge to reach a verdict that needs
# neither (er-effects-rs-7ics). Both are dependency-free and sub-second.
#
# They run HERE and nowhere else on purpose. Their authority is the regulation.bin of the game
# installed on this machine, so a missing regulation is exit 2 rather than a pass -- "could not
# look" must never read as "agreed". The single escape hatch is an explicit
# ER_ALLOW_MISSING_REGULATION=1, which downgrades that to a printed `SKIPPED: ... was NOT checked`
# line on stderr. Do NOT set it on a developer machine that has the game: it converts the only
# gate that reads real param data into a line of log noise. .github/workflows/check.yml does not
# invoke this script -- it re-implements a chosen subset of these steps as its own job steps -- and
# a GitHub runner has no game install, so these two are deliberately absent from CI. Adding them
# there would print SKIPPED on every run forever, which is a green that means nothing.
python3 "$repo_root/scripts/diff-regulation-params.py" --effects-json
python3 "$repo_root/scripts/check-starting-classes.py"

# er-telemetry-core's host-portable logic. The workspace pins `default-members` to the DLL crate, so the
# windows-target `cargo xwin test --lib` below selects er-quickload ONLY and never ran these -- a
# telemetry-crate test module could be added and silently never execute in any gate. The load-count
# consistency logic is pure integer arithmetic with no platform semantics, so the host run is the
# real coverage; the cross-compile check in check-rust-build.sh keeps it building for the shipping
# target too.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-telemetry-core --lib

# er-seamless-bugfixes' registries. The crate's own docs already said the `cfg(not(windows))` allow
# exists so `cargo test -p er-seamless-bugfixes` can build -- but no gate ever RAN it, so all 23
# tests were inert: `default-members` pins the workspace to er-quickload, and check-rust-build.sh
# only LINKS this shell. What that left unchecked is the whole safety argument for the code patch.
# The freelist patch rewrites one byte of live game code, and its licence to do so is that the `JZ`
# two bytes earlier already lands past the `INT3`; these tests recompute that landing address the
# way the CPU does, and require the write to be one NOP at the `INT3`'s own offset. The window
# BYTES are ground-truthed separately, against eldenring-deobf.bin, by the crate's build.rs.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-seamless-bugfixes --lib

# er-hook's raw code-patch primitives. This crate is linked into 15 of the 23 cdylibs, the shipped
# er_quickload.dll among them, so a defect in a byte-patch primitive here is a defect in all of
# them at once -- and it is the crate LEAST able to report one: it carries a crate-level
# `#![allow(dead_code, ...)]` for MinHook binding parity, so an unused or wrong primitive draws no
# warning, and `default-members` pins a bare `cargo test` to er-quickload so nothing ever selected
# it. The tests cover what a compile check cannot see about `write_code_byte`: that the page is
# relocked to the protection it actually had rather than left `PAGE_EXECUTE_READWRITE`, and that a
# refused `VirtualProtect` returns before the store instead of writing anyway. Each assertion was
# confirmed to go red against a deliberately broken implementation.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-hook --lib

# THE 1.17 UNGATED-ADDRESS RATCHET. `er_game_base::game_build` translates or REFUSES a known 1.16.2
# address on the running build, and `er-hook`'s `MhHook::new` routes detours through it -- so a hook
# on a function the patch moved fails loudly instead of corrupting the image. That protection only
# covers addresses that go through it, and a hand-built `transmute(base + SOME_RVA)` does not: it
# calls the 1.16.2 address on 1.17 with nothing to refuse it, EVEN WHEN the map already knows where
# that function went. This counts those per cdylib and fails when a count RISES.
#
# The property worth protecting most: 0 ungated WRITEs across all 27 cdylibs, so no DLL can corrupt
# the 1.17 image with a stale address. Measured 2026-08-29; this is what keeps it true.
python3 "$repo_root/scripts/audit-1170-readiness.py" --selftest
python3 "$repo_root/scripts/audit-1170-readiness.py" --check

# er-game-base: the shared re-entrancy latch and the bounded wait helpers. Both are load-bearing
# for whether the game SURVIVES, not for what it computes -- `wait::poll_until` is what stops an
# unbounded `yield_now` spin from starving the serializing wineserver, and `reentry::ReentryLatch`
# is what stops a crash handler that faults while describing a fault from eating 4704 bytes of
# stack per level until the thread dies unreportably (measured on ELDEN RING 1.17, 2026-08-28).
# Neither failure mode produces a compile error and neither is selectable by a bare `cargo test`,
# because `default-members` pins that to er-quickload.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-game-base --lib

# er-refill-all: the pad-chord parser, the config reload decision, and the cycle-direction rule are
# all host-buildable on purpose, so the parts that decide whether a press does the right thing are
# testable without the game. The tracker-capacity assertion lives here too -- it is the guard on a
# DLPanic that would crash the game outright.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-refill-all

# HOST-TARGET COMPILE OF THE PRODUCT CRATE AND ITS WHOLE HOST DEPENDENCY GRAPH. Everything else
# in this file compiles the DLL crates for x86_64-pc-windows-msvc, where the windows-only game
# bindings always resolve -- so a `use windows::...` / `use eldenring::...` written WITHOUT a
# `#[cfg(windows)]` gate is invisible to every gate here while breaking a plain host
# `cargo test`. er-title-flow shipped exactly that: 31 unresolved-import errors on the host
# (measured 2026-08-23), and the cost was misdirection -- an agent or human reaching for a host
# `cargo test` saw a wall of errors that looked like their own change.
#
# `-p er-quickload --lib` is the reproducer itself: the crate's host build is a single stub fn,
# so this compiles nothing but the dependency graph, which is the surface that rots.
# `-p er-title-flow --lib` additionally RUNS boot_hold's predicates -- the crate's only
# host-portable logic, and untestable at all until the gates landed.
cargo test --manifest-path "$repo_root/Cargo.toml" -p er-quickload -p er-title-flow --lib

# Rust format + Windows-target BUILD of the injectable DLL (cross-compiled from Linux via
# cargo-xwin). A real build (not just `cargo check`) so codegen/link regressions -- including
# any pre-existing rust breakage -- are caught here, producing the linked er_quickload.dll.
# The linking gate above is only as good as its list. `check-rust-build.sh` carries an
# `me3_shells` array of every ME3-loadable cdylib and links each one, but that array was kept
# correct by a COMMENT saying "keep this list in sync" -- so adding a new DLL crate would leave
# the suite green while nothing ever linked it, which is the same hole the array closed, one
# level up. This makes the list's completeness executable.
python3 "$repo_root/scripts/check-me3-shell-coverage.py" --selftest
python3 "$repo_root/scripts/check-me3-shell-coverage.py"

# Knowing every shell exists is not knowing which of them can share a process. Several pairs
# corrupt each other -- two MinHook instances on one prologue, two D3D12 Present compositors,
# a harness that drives input every frame -- and that knowledge used to live only as prose in
# a hand-written ~/Elden/*.me3. scripts/er-dll-closure.py now reads it as data to decide what a
# generated profile may load, so the table must stay complete: a new cdylib that nobody has
# classified is exactly the one a dependency-closure walk auto-includes.
python3 "$repo_root/scripts/check-me3-dll-conflicts.py" --selftest
python3 "$repo_root/scripts/check-me3-dll-conflicts.py"

# ...and the table only helps if it still matches the CODE. This scans every cdylib for the hook
# targets it claims and fails on any address two of them claim without a [[conflict]] or [[shared]]
# row -- then proves each [[shared]] row's mechanism, so neither side can quietly revert to a
# private MinHook instance. That reversion is the failure this pair of gates exists for: two
# instances on one prologue overwrite each other's trampolines, the loser reports installed and
# never runs, nothing crashes, and the feature merely looks unimplemented. It cost a full day on
# 2026-08-23 before an A/B against a one-DLL profile named it.
python3 "$repo_root/scripts/check-shared-hook-rvas.py" --selftest
python3 "$repo_root/scripts/check-shared-hook-rvas.py"

# The branch-launch pipeline. Each stage refuses rather than guessing, and each carries its own
# selftest for the refusal it exists to make -- a stale DLL, an unrankable conflict, a save with
# no decoded identity, a block printed without the DLL's testimony.
python3 "$repo_root/scripts/er_run_lib.py"
python3 "$repo_root/scripts/er-dll-closure.py" --selftest
python3 "$repo_root/scripts/er-dll-provenance.py" --selftest
python3 "$repo_root/scripts/er-pick-save.py" --selftest
python3 "$repo_root/scripts/er-gen-me3-profile.py" --selftest
python3 "$repo_root/scripts/er-run-reaper.py" --selftest
python3 "$repo_root/scripts/er-run-branch.py" --selftest

# Scoring a DLL by launching it alone. Its verdict is the husk oracle -- thread count and CPU
# burn, not a pid existing -- and its selftest drives every branch of that classification,
# including the two-thread husk that a naive check calls a pass. It launches nothing.
python3 "$repo_root/scripts/er-release-bisect.py" --selftest

# Product D3 contract: the customized quit menu is an rlib dependency inside the one shipped
# er_quickload.dll. Its standalone DLL remains an explicitly-built harness and must never leak into
# the default build, staged product payload, or required ME3 native list.
python3 "$repo_root/scripts/check-single-dll-product-contract.py" --selftest
python3 "$repo_root/scripts/check-single-dll-product-contract.py"

bash "$repo_root/scripts/check-rust-build.sh"

# Dead/unused code in the save-disable DLL, on its shipping target. Scoped to that one
# crate on purpose: the repo builds with a global `-Awarnings`, so this is the narrow
# place where warning-freedom is both achievable today and load-bearing -- the crate's
# whole job is to stop saves, and two dead helpers already survived a refactor unseen.
python3 "$repo_root/scripts/check-save-disable-warnings.py"
