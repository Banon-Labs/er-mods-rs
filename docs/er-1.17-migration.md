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

## What is still stale

| # | Item | State | Blocked on |
| --- | --- | --- | --- |
| 1 | Ghidra dump / MCP (`ermaporch1162`, :8765) | 1.16.2 only -- every name, signature and struct it returns is the previous build | a 1.17 runtime dump, imported as `ermaporch1170` |
| 2 | 19 unresolved addresses in the RVA map | shape-matched but ambiguous, and deliberately left blank | #1, or hand RE per address |
| 3 | 32 resolved addresses | candidates, not verified hook sites | reading each 1.17 function; only then write it into code |
| 4 | Struct layouts | two confirmed drifts: `PlayerGameData` +8 (`+0xab5` -> `+0xabd`), the Wwise settings object +0x38. The rest is unaudited | #1 |
| 5 | `fromsoftware-rs` bindings (path dependency) | field offsets are 1.16.2-shaped | #4 |
| 6 | Generated prologue windows (`build.rs` + `check-prologue-bytes`) | ground-truthed against the 1.16.2 image, which is why `eldenring-deobf.bin` still points there | #3 -- flip the canonical image in the same commit that re-points the addresses |
| 7 | `dump-exec.bin` + `scripts/dump-deobf-shift.py` | dump side is **1.16.1**: cross-version by two patches, and its matcher cannot see struct-offset drift | regenerate, or retire it in favour of `map-rvas-1162-to-1170.py` |
| 8 | `regulation.bin`, `data/effects.json`, `effect-master-catalog.json` | 1.17 shipped new params; row ids unverified | re-validate with `tools/er-param-inspect` |
| 9 | Save containers / `ProfileSummary` reader | RVA-stale; whether the format itself changed is unknown | #1 plus a save-format diff |
| 10 | ~2.5k `bd` memories carrying 1.16.2 RVAs | correct for the build they were written against, silently wrong now | nothing -- treat every RVA memory as 1.16.2-scoped and re-verify before use |

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

## The order to do the rest in

1. Capture a 1.17 runtime dump and stand up `ermaporch1170` (#1). Items 2, 4, 5 and 9 all reduce to
   lookups once it exists, and 19 blanks become answerable.
2. Verify and re-point addresses feature by feature (#3), cheapest first: each one that lands turns
   a `HOOK REFUSED` line back into a working feature, and the gate keeps the rest safe meanwhile.
3. Flip `eldenring-deobf.bin` to the 1.17 image and regenerate the prologue windows (#6) in one
   commit, once enough addresses are re-pointed that the gates are meaningful again.
4. Re-validate the param/save data (#8, #9), which is the only part that can change what a player
   sees without any address being involved.
