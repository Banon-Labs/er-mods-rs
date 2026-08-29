# ELDEN RING 1.17 migration status

ELDEN RING updated to **1.17** on 2026-08-27 (PE `FileVersion` 2.7.0.0, Steam buildid 23850278).
Every game address in this workspace was reverse-engineered against **1.16.2** (2.6.2.0). This file
is the state of that gap: what has been repaired, what is stale, and what each remaining item is
blocked on. Update it as items close rather than starting a second list somewhere else.

## What already works on 1.17

| Component | Evidence |
| --- | --- |
| me3 0.11.0 + dearxan | A zero-native profile boots 1.17 normally; the loader was never the problem. |
| Seamless Co-op v1.9.9, via `er-ersc-sigshim` | Two AOB landmarks rebuilt; game reaches the title screen and ersc detours the relocated function. |
| The build gate (`er-hook` + `er-game-base::game_build`) | 51 game-image detours refuse with a logged reason instead of corrupting the image; boot survives. |
| Win32 / DirectInput detours | Resolved through `GetProcAddress`, never version-sensitive. 15 install normally. |
| `eldenring-deobf-1.17.bin` | Generated offline by dearxan (1597 stubs, 1371 decrypted regions); byte-identical to live memory at three independently known sites. |
| `docs/recon/rva-map-1162-to-1170.tsv` | 32 of the 51 refused addresses carried forward with evidence; the other 19 are named, not guessed. |
| 27 translated detour targets | Three independent passes agree: signature re-occurrence, normalised instruction comparison, and `scripts/audit-1170-hook-targets.py`, which finds every one is a real function entry carrying the SAME reference profile it had in 1.16.2 (same call count, same kind). The control is decisive -- 24 of the 27 STALE addresses have zero references in the 1.17 image. |

## What is still stale

| # | Item | State | Blocked on |
| --- | --- | --- | --- |
| 1 | Ghidra dump / MCP (`ermaporch1162`, :8765) | 1.16.2 only -- every name, signature and struct it returns is the previous build | a 1.17 runtime dump, imported as `ermaporch1170` |
| 2 | 19 unresolved addresses in the RVA map | shape-matched but ambiguous, and deliberately left blank | #1, or hand RE per address |
| 3 | 5 non-IDENTICAL mappings (2 NEAR, 1 DIVERGES, 2 too short to judge) | refused; the other 27 are translated and audited | reading each 1.17 function by hand |
| 4 | Struct layouts | two confirmed drifts: `PlayerGameData` +8 (`+0xab5` -> `+0xabd`), the Wwise settings object +0x38. The rest is unaudited | #1 |
| 5 | `fromsoftware-rs` bindings (path dependency) | field offsets are 1.16.2-shaped | #4 |
| 6 | Generated prologue windows (`build.rs` + `check-prologue-bytes`) | ground-truthed against the 1.16.2 image, which is why `eldenring-deobf.bin` still points there | #3 -- flip the canonical image in the same commit that re-points the addresses |
| 7 | `dump-exec.bin` + `scripts/dump-deobf-shift.py` | dump side is **1.16.1**: cross-version by two patches, and its matcher cannot see struct-offset drift | regenerate, or retire it in favour of `map-rvas-1162-to-1170.py` |
| 8 | `regulation.bin`, `data/effects.json`, `effect-master-catalog.json` | 1.17 shipped new params; row ids unverified | re-validate with `tools/er-param-inspect` |
| 9 | Save containers / `ProfileSummary` reader | RVA-stale; whether the format itself changed is unknown | #1 plus a save-format diff |
| 10 | 160 game addresses CALLED without resolving (`transmute(base + SOME_RVA)`) | ungated, ratcheted by `scripts/check-stale-rva-calls.py` so the set cannot grow | converting each to `er_game_base::mem::game_rva`, crate by crate (er-effects-rs-4wjr) |
| 11 | 4 byte-patch stub sites (online-disable, menu-online-mode, signin-force, userindex-force) | REFUSE on 1.17 -- each validates one expected opcode byte and all four differ, so nothing is written. Those features are silently inert, not dangerous | re-RE the four functions on 1.17 |
| 12 | ~2.5k `bd` memories carrying 1.16.2 RVAs | correct for the build they were written against, silently wrong now | nothing -- treat every RVA memory as 1.16.2-scoped and re-verify before use |

## How to carry an address forward

```bash
# One address, or the whole work list straight out of a runtime log.
uv run --with capstone python3 scripts/map-rvas-1162-to-1170.py 0x1407ada40
uv run --with capstone python3 scripts/map-rvas-1162-to-1170.py \
    --from-refusal-log "<game dir>/er-quickload-autoload-debug.log" \
    --tsv docs/recon/rva-map-1162-to-1170.tsv
uv run --with capstone python3 scripts/map-rvas-1162-to-1170.py --selftest
```

The mapper reports how each answer was reached, and the distinction matters:

* `unique, NNB signature` -- one masked match in the whole image. Strong.
* `nearest-anchor delta` -- the shape occurs more than once and the winner was chosen because its
  delta matches the closest uniquely-mapped address. Weaker; the shift changes over spans as short
  as 0xb00 bytes, so an anchor further away than that is not evidence.
* `UNRESOLVED` -- shape matches exist but none agrees with the local delta. Left blank on purpose:
  a blank costs a Ghidra lookup, a wrong address costs a mid-function detour and a dead boot.

`--selftest` asserts the mapper re-derives the two mappings this repo established by hand from the
live 1.17 process (`GetScadutreeBlessing` and the `GetWwiseSettings` allocator landmark). A matcher
that cannot reproduce a known answer has no business proposing an unknown one.

## Three private address helpers that looked gated and were not

Each of these resolved a game address for callers that transmute and CALL it, and none checked
anything. They are fixed; they are recorded because the shape recurs -- a helper whose NAME or doc
comment implies a check, wrapping a bare addition.

| Where | What it claimed | What it did |
| --- | --- | --- |
| `er-save-loader` `load_methods::game_rva` | nothing, but it is the crate's address resolver and its results are transmuted | rejected a null base, then added |
| `er-invasion-path` `sfx::function` | "refusing anything that is not inside the game image" in its own doc comment | added |
| `er-build-import-runtime` `catalog` getters | `name_of`'s safety comment: "the caller guarantees the address is one of the verified getters" | added |

All three now go through `er_game_base::game_build::resolve_game_address`, so the doc comments are
true and an unresolvable address costs a feature rather than the process.

## What a "verified" address does and does not buy

Three questions, and it is worth keeping them apart, because each was answered by a different pass
and conflating them is how a wrong address gets installed with confidence:

| Question | Answered by | What it cannot see |
| --- | --- | --- |
| Where did this function move to? | `scripts/map-rvas-1162-to-1170.py` -- masked signature re-occurrence | whether the recurrence is the function or an inlined copy of its prologue |
| Is the code there the same function? | `scripts/verify-rva-map-1170.py` -- normalised instruction sequences | what the functions it CALLS now do |
| Is it a function ENTRY, and is it safe to patch? | `scripts/audit-1170-hook-targets.py` -- references the image makes to it, plus MinHook's patch window | whether OUR HANDLER is still right |

The last column of the last row is the standing gap: a handler reads struct fields and calls
virtuals by slot, and 1.17 moved plenty of both. No offline pass can close it. Only a fatal record
naming the handler can, which is what `docs/recon/rva-1170-quarantine.tsv` is for and why it is
still empty -- across every run of 2026-08-28 the unhandled-exception filter fired zero times.

A note on how the audit's entry check was built, because it is the reusable part: the first version
decoded BACKWARDS from the address looking for int3 padding or a terminator. Run against the 1.16.2
image at the 27 addresses this project has hooked successfully for months, it called 20 of them
mid-function. Backward decoding desynchronises, and a de-Arxan'd image does not carry the padding
it assumed. The replacement uses forward-derived evidence -- calls, jumps and pointers the image
makes TO the address -- and passes 27/27 on that same known-good input while still rejecting a
deliberate mid-function control. Calibrate a new check on data whose answer you already know
before you point it at data you do not.

## The sibling checkout is part of this migration, and git does not know it

`eldenring::rva::get()` in `../fromsoftware-rs` resolves a `LazyLock<RvaBundle>`
through `ERGameVersion::detect()`, and until 2026-08-29 that function accepted
exactly two strings: `"2.6.2.0"` and `"2.6.2.1"`. The installed game is
`2.7.0.0`, so it took the `.unwrap_or_else(|e| panic!("{e}"))` arm.

That one line of data was the whole 1.17 boot death. **Eight** product DLLs --
er-quickload, er-invasion-warp, er-inventory-sort, er-refill-all,
er-charm-enemies, er-telemetry, er-invasion-path, er-net-effects -- each threw
a `rust_panic` within five milliseconds of each other at
`ms_since_install=869..898`, each on its own thread, each with an identical
frame shape. The panic happens inside a `LazyLock`, on whichever thread first
touches any singleton, so nothing logged the message and nothing named the
DLL: the crash record says `cpp_throw_type=rust_panic` and stops. It cost
eight game launches to find, and the shape of it -- "a companion DLL is
involved, because er-quickload alone gets further" -- was a red herring
produced by nothing more than which thread happened to touch a singleton
first.

The fix is three edits in the sibling checkout:

| file | change |
|---|---|
| `crates/eldenring/mapper-profile.toml` | `title_step_state_table`'s pattern, see below |
| `crates/eldenring/src/rva/rva_ww_270.rs` | new, generated, 96 RVAs |
| `crates/eldenring/src/rva.rs` | `Ww270` variant accepting `(LANG_ID_EN, "2.7.0.0")` |

Regenerate the bundle with the project's own generator rather than by diffing
addresses. 62 of the 96 fields are vtables resolved by RTTI class name, which
is version-independent and needs no work at all; the rest come from AOB
patterns. For comparison, `scripts/map-rvas-1162-to-1170.py` resolved 2 of the
first 5 code addresses -- byte-diffing is the wrong tool for a whole bundle.

```bash
cd ../fromsoftware-rs
# `windows-future` cannot build for a Linux target at all, so the generator is
# cross-compiled and run under wine rather than built natively.
cargo xwin build --release -p binary-mapper --target x86_64-pc-windows-msvc
WINEDEBUG=-all wine target/x86_64-pc-windows-msvc/release/binary-mapper.exe map \
  --profile 'Z:\home\banon\projects\fromsoftware-rs\crates\eldenring\mapper-profile.toml' \
  --exe 'Z:\home\banon\.local\share\Steam\steamapps\common\ELDEN RING\Game\eldenring.exe' \
  --output rust
```

**Check every field for zero before believing the output.** `MapperProfilePattern::find`
records a miss as RVA `0`, prints no warning, and `0` resolves at runtime to the
PE header -- a silent wrong answer that looks like a successful generation.
On the first 1.17 run exactly one field came back zero, `title_step_state_table`,
because 1.17 emits a `nop` between the `call` and the vftable store that the
pattern's fixed `[4]` skip could not cross. Two things about the replacement
are worth keeping: the `mov byte [rdi+0xb8], 0` suffix is load-bearing, since
without it fifteen sibling state-machine constructors match on both versions
and `find` silently takes the first; and pelite 0.10's range skip `[4-5]`
parses fine but does not match here, so the pattern uses an alternation
`( 90 48 8d 05 | 48 8d 05 )` instead. The result is calibrated rather than
fitted: on 2.6.2.0 the new pattern still yields `0x3d71580`, the value already
checked in.

`scripts/check-game-version-supported.py` is the gate that makes this
discoverable without a game. It reads the PE product version off
`eldenring.exe`, reads the accepted versions out of the sibling's `rva.rs`, and
fails when they disagree -- printing the exact panic text the mismatch will
produce. It skips, rather than fails, when either side is absent.

None of the sibling's edits are tracked by this repository. A fresh clone of
`fromsoftware-rs` reintroduces the boot death, and the gate above is what will
say so.

## The order to do the rest in

1. Capture a 1.17 runtime dump and stand up `ermaporch1170` (#1). Items 2, 4, 5 and 9 all reduce to
   lookups once it exists, and 19 blanks become answerable.
2. Verify and re-point addresses feature by feature (#3), cheapest first: each one that lands turns
   a `HOOK REFUSED` line back into a working feature, and the gate keeps the rest safe meanwhile.
3. Flip `eldenring-deobf.bin` to the 1.17 image and regenerate the prologue windows (#6) in one
   commit, once enough addresses are re-pointed that the gates are meaningful again.
4. Re-validate the param/save data (#8, #9), which is the only part that can change what a player
   sees without any address being involved.
