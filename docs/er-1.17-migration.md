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
| 3 | 1 mapping refused on evidence: `MOVEMAPSTEP_STEP_MOVEMAP_RVA` (`NEAR`, body grew by two instructions) | callable, detour refused; the 2026-08-30 verdict rework cleared the other four -- see "Function lengths" below | an explicit human promotion, if that detour is wanted |
| 4 | Struct layouts | two confirmed drifts: `PlayerGameData` +8 (`+0xab5` -> `+0xabd`), the Wwise settings object +0x38. The rest is unaudited | #1 |
| 5 | `fromsoftware-rs` bindings (path dependency) | field offsets are 1.16.2-shaped | #4 |
| 6 | Generated prologue windows (`build.rs` + `check-prologue-bytes`) | mostly fine: a swept comparison of all 36 specs against the 1.17 image found exactly ONE that breaks, `er-save-suppress::QUIT_PHASE_SETTLE_SIG`, now respelled. `Image::EldenRing1170` exists for a spec that must be 1.17 (3 use it); the rest are register-only prologues whose encoding is version-invariant | 5 specs whose 1.16.2 RVA is in no map, so they could not be checked at all |
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

## The wedge: er-quickload kills the game's main thread (open, 2026-08-29)

The crash classes are closed -- the full eighteen-DLL profile loads with zero panics and three
detour refusals -- and what is left is not a crash. With `er_quickload.dll` loaded, ELDEN RING
1.17's **initial thread exits** a few seconds into boot. `/proc/<pid>/stat` shows the thread-group
leader in state `Z` while ~81 workers stay alive and asleep, so the process lingers as a husk with
a mapped window and ~0 CPU until it finally goes away. That husk is the "black screen".

### What is measured, and what it rules out

| profile | result |
|---|---|
| no natives at all | 112 threads, 784 CPU ticks/3s -- healthy |
| `ersc.dll` + `er_ersc_sigshim` | 106 threads, ~350 ticks/3s -- healthy, so **Seamless is not it** |
| the same plus `er_quickload.dll` | leader `Z`, workers idle -- **wedged** |

Both healthy profiles and the wedged one were launched the same way, through `~/Elden/launch.sh`
with hand-written profiles in `~/Elden/` (`control-no-natives.me3`, `control-ersc-only.me3`,
`control-quickload-only.me3`). That matters: for eight runs every death was staged by
`scripts/er-run-branch.py` and every survival came from `launch.sh`, so the launcher and the DLL
set were changing together and neither could be blamed. The third profile broke the tie and
exonerated the runner.

### Six hypotheses, each falsified by its own launch -- do not re-test these

1. the boot pump's foreign-thread self-present (`self_present_safe` forced false)
2. the early foreign-thread window move (`apply_startup_window_final_geometry`)
3. the swapchain VMT swap (`try_install_game_present_hook` made a no-op)
4. our ~200 game-image detours (`DETOUR_SAFE_1162_TO_1170` emptied in `er-game-base/build.rs`)
5. `er-crash-logging`'s vectored handler (excluded from the profile)
6. `scripts/er-stale-run-sentinel.sh` -- its log records `killed=-` on every line; it never fired

### The trap that cost four stack captures

`/proc/<pid>/mem` **is** `/proc/<pid>/task/<leader>/mem`, so a zombie leader makes it unopenable:
the open fails with `ESRCH` "No such process" while the process is alive and burning 195 ticks per
three seconds. Read `/proc/<pid>/task/<live-tid>/mem` instead. Both
`scripts/er-wedge-stacks.py` and `scripts/wine-thread-death-watch.py` now do, and the former
prints the leader state as its headline so the state is never inferred from a failed read again.

### Where it stands, and the next step

`scripts/wine-thread-death-watch.py` caught the death: over its last 400 samples the initial
thread was in state **R -- running in userspace -- for 368 of them**, CPU still climbing, and then
it was gone. Not blocked, and no fatal record from the vectored handler in the runs that carried
one. A thread that runs hot and then exits cleanly points at a deliberate thread termination, not
a fault.

### The death site, captured

It is a **SIGSEGV inside `er_quickload.dll`**, and Wine aborts the thread because it cannot even
build the exception frame -- which is why no vectored handler ever recorded it. `abort_thread`
bypasses the SEH/VEH path entirely.

```
#0 abort_thread            (ntdll.so)
#1 virtual_setup_exception (ntdll.so)   <- could not build the exception frame
#2 setup_raise_exception   (ntdll.so)
#3 segv_handler            (ntdll.so)
#4 <signal handler called>
#5 0x6ffff9cd2326 in er_quickload.dll
#6 0x0
```

The PE stack scan at the abort (module base `0x6ffff9cd0000`) names the shape:

| rva | times on the stack | symbol |
|---|---|---|
| `0x2326` | 3 | none -- below where Rust code starts, so a linker import/jump thunk |
| `0x1cf0c0` | 2 | `core::fmt::num::impl$8::fmt` |
| `0x392fd8` | 1 | none |

Alternating repeated frames, plus `virtual_setup_exception` failing, is **recursion into stack
overflow** -- not a wild pointer. An import thunk in the loop with `core::fmt` beside it points at
a log write re-entering a hooked Win32 file call: the DLL logs every `CreateFileW` through
save-override, and writing that log opens a file. Confirm by disassembling `er_quickload.dll` at
rva `0x2326` to see which import it is, then read that hook's re-entrancy guard.

### How to reproduce the capture

This is the part that cost the most, so it is written down rather than rediscovered:

* me3 has `--suspend` ("Suspend the game until a debugger is attached"). Pass it through the user
  launcher as `ME3_PROFILE=... bash ~/Elden/launch.sh -s -- --suspend`. **The bare `--` matters**;
  without it `getopts` eats `--suspend` and the script just prints its usage.
* Then attach `python3 scripts/wine-thread-death-watch.py --gdb --max-seconds 240 --out <file>`.
  Attaching after the fact cannot work -- gdb refuses a zombie leader -- and without `--suspend`
  the thread dies within about six seconds, far too early to attach by hand.
* Run the watcher under `setsid`/`nohup`: an agent tool call is killed at two minutes and the
  capture needs longer than that.
* Under gdb the death is DELAYED, because every breakpoint trap slows the process down. An 80
  second window was not enough and 240 was.

## Which DLL actually breaks: a five-profile split (2026-08-29)

The boot death was being blamed on "the mod set". It is one DLL, and the crash log named the
fault all along -- 216 `access-violation` lines of which exactly ONE matters:

| | fault |
|---|---|
| 1 x | EXECUTE at `0x3cb67a0` -- a heap object whose vtable is game rva `0x2b6d728`, RTTI `CS::CSFadeImp` |
| 215 x | identical NULL read at `ntdll+0x3969c`, `rsp` marching DOWN `0x1260` a line, `0x2fc50` -> `0x13a50` |

4704 bytes a level against a 1 MiB stack is dead after ~220 levels, and `MAX_AV_LOG_LINES` is 256,
so the line budget could never stop it. The thread then takes a SIGSEGV Wine cannot report --
`virtual_setup_exception` needs stack to build the exception frame, has none, and calls
`abort_thread` -- which is why the process died with no fatal record and no minidump.

**The amplifier is NOT our handler re-entering itself.** That hypothesis was implemented
(`er_game_base::reentry::ReentryLatch` on both `crash_vectored_handler`s) and falsified by its own
oracle in one run: the descent reproduced exactly with `oracle_veh_reentrant_refusals = 0`. A gdb
catch on `KiUserExceptionDispatcher` then counted **218 raises on the initial thread with `rsp`
descending a constant `0x12c0` each** -- every one a fresh top-level raise, our previous invocation
already returned. The recursion is in Wine's exception dispatch. The latch stays as correct
defensive code for a real hazard; it is not this bug.

### The split -- nine launches

Each row is one launch of one `.me3` profile through `~/Elden/launch.sh -s`, watching
`/proc/<pid>/stat` field 3 for the thread-group leader.

| profile | crates | outcome | fault signature |
|---|---|---|---|
| sigshim + ersc + quickload | -- | leader `Z` | `0xc0000005` EXECUTE at `0x3cb67a0` (`CS::CSFadeImp`), +1.7s |
| **er_quickload ALONE** | 23 | leader `Z` | **identical** -- same address, same chain |
| er_telemetry | 6 | boots | -- |
| er_quit_menu | 12 | boots | -- |
| er_loading_bar | 5 | boots | -- |
| er_save_picker | 9 | boots | -- |
| er_save_disable | 4 | boots | -- |
| er_loading_portrait | 11 | vanishes ~4s after its first Present | NO crash record at all |
| er_armament_icons | 4 | vanishes ~+38s | `0xc000001d` ILLEGAL_INSTRUCTION at `game+0x32ee2b5`; 3 hooks correctly REFUSED by the gate first |

**"Died" is not a usable signal on 1.17 -- the fault SIGNATURE is.** Three shells die and they die
three different ways; only er_quickload produces the CSFadeImp execute fault. A bisect that scores
rows as live/dead instead of comparing signatures would have convicted the wrong crate twice.

Exonerated outright: Seamless Co-op 1.9.9; the shared base (`er-game-base`, `er-hook`,
`er-crash-logging-core`, `er-telemetry-core` -- er_telemetry links all of it and boots); the shared
title/quit feature crates (er_quit_menu); the Present hook (er_loading_bar hooks Present and boots,
which kills the tempting 2x2 where both Present-hooking shells died); `er-save-redirect`
(er_save_picker); `er-save-suppress` (er_save_disable). Also cleared by reading rather than
running: quickload's own VMT-swap path, whose `swapchain_vtable_matches` demands an exact
slot-8-and-22 match against addresses resolved from a dummy swapchain, and whose run log records
`exact vtable match` -- it cannot have patched a CSFadeImp.

### What is left, and why the shell axis is now exhausted

Crate-set arithmetic (`cargo metadata` closures, dying minus living) leaves these carried by NO
other cdylib, so no further profile can test them without editing quickload itself:

    er-quickload (379 files of experiments)   er-title-flow      er-scaleform-hooks
    er-boot-profiler                          er-tpf             er-profile-summary-core

and three shared only with the OTHER dying shell, so unproven either way: `er-gfx`,
`er-loading-portrait-core`, `erpx-rs`. (`er_armament_icons` carries er-gfx and does die -- but with
a different signature, so it neither convicts nor clears it.) `CS::CSFadeImp` is title/fade
machinery, which points at er-title-flow and er-scaleform-hooks first.

### Reading the evidence without being misled

* `modbt=[...]` in an `access-violation` line is a **stack SCAN**, not an unwind. It mixes dead
  frames with live ones. Chasing its frame 0 here led to `eldenring.exe+0xb3d2e8`, whose preceding
  call is a three-instruction atomic increment that cannot fault -- a stale frame. Do not chain it.
* What IS trustworthy in that line: `raw=[...]` (the actual qwords at `rsp`), the `vt=` probe (feed
  the rva to the RTTI reader below), and the register values.
* Resolving a vtable rva to a class name, offline, in both images -- `vtable[-1]` is the
  RTTICompleteObjectLocator, whose `pTypeDescriptor` rva + 16 is the mangled name:

  ```python
  col = struct.unpack_from('<Q', image, vt_rva - 8)[0] - 0x140000000
  ptd = struct.unpack_from('<IIIII', image, col)[3]
  name = image[ptd+16:ptd+16+120].split(b'\0')[0]
  ```

  This is how `0x2b6d728` was named `CS::CSFadeImp`, and it also proves the region MOVED: in 1.16.2
  that same rva is not a vtable at all, and `0x2b63bb0` -- which the tree hard-codes as
  `TITLE_OWNER_VTABLE_RVA` in three crates -- is `CS::TitleStep` in 1.16.2 and nothing in 1.17.
  Those scans now silently find nothing. Read-only and fault-safe, so not a crash, but the features
  behind them are dead until the constants are re-pointed.

### Function lengths as a cheap map audit

Comparing `.pdata` extents for every pair in `rva-map-1162-to-1170.needed-verified.tsv`: exactly
one of 218 changed size, `0x140af7cf0 -> 0x140af9000` (`MOVEMAPSTEP_STEP_MOVEMAP_RVA`),
`0x120b -> 0x1213`.

**The conclusion originally drawn here was wrong, and the way it was wrong is the point.** It said
the 8 bytes were "elsewhere in the tail" and `IDENTICAL` was the right verdict. They are two
INSERTED INSTRUCTIONS at index 873 of 975 -- a Torrent destroy-and-recreate -- and `IDENTICAL` was
what the verifier said after comparing 120 instructions and stopping. A cheap signal was noticed,
explained away, and the explanation was never checked against the thing it explained.

Since 2026-08-30 the extent length is a first-class signal rather than an audit someone remembers
to run: a differing length blocks an `IDENTICAL` verdict outright, the verifier decodes the WHOLE
declared extent instead of the first 120 instructions, and every row carries an `extent` column
(`PDATA:0x120b/0x1213+8`). This pair now verdicts `NEAR` -- callable, detour refused -- which is
what it should have said all along.

## How much of the migration is actually left (2026-08-29)

`367` `const *_RVA` declarations exist under `crates/`. `182` are mapped for 1.17. The other `185`
break down like this -- and the shape of the split is the point, because only one bucket is the
"the mapper could not find it" problem everyone assumes:

| count | why it is unmapped |
|---|---|
| 141 | **not in `.text` at all** -- vtable, global or other `.data`/`.rdata` address. The gate is keyed on `.pdata` function starts, so these were never candidates. This is the silent-wrong-answer class. |
| 29 | a real function start, but the masked-signature mapper found no unique pair |
| 11 | **mid-function, and the containing function IS already mapped** -- mechanically fixable |
| 4 | mid-function, containing function also unmapped |

`er-title-flow` (38) and `er-loading-portrait-core` (30) hold the most unmapped constants after
er-quickload (58) -- the same two crates that sit in the shells that die.

### The eleven mechanical ones

A mid-function address cannot be mapped, but the function that contains it can, and the offset
within it survives the move. So the fix is to declare the FUNCTION as the `*_RVA` constant (which
puts it in front of `scripts/select-needed-1170-rows.py`) and add the offset at the use site:

| constant | 1.16.2 | containing fn -> 1.17 | offset |
|---|---|---|---|
| `TITLE_GFX_VISIBLE_TITLE_FADEIN_CALLER_RVA` | `0x744e02` | `0x744dd0` -> `0x745c20` | `+0x32` |
| `TITLE_NATIVE_MENU_VISUAL_FACTORY_RVA` | `0x7acbf0` | `0x7acb00` -> `0x7ad980` | `+0xf0` |
| `TITLE_NATIVE_MENU_VISUAL_WINDOW_FADEIN_RUN_CALLER_RVA` | `0x7ad530` | `0x7ad1c0` -> `0x7ae040` | `+0x370` |
| `GX_COMMAND_QUEUE_RVA` | `0x8012a8` | `0x8012a0` -> `0x802120` | `+0x8` |
| `SYSTEM_QUIT_DUPLICATE_TARGET_RETURN_RVA` | `0x958a20` | `0x958910` -> `0x959ab0` | `+0x110` |
| `SYSTEM_QUIT_SECOND_ROW_TARGET_RETURN_RVA` | `0x958b37` | `0x958910` -> `0x959ab0` | `+0x227` |
| `FREELIST_SHUTDOWN_ASSERT_RVA` | `0xc57670` | `0xc57666` -> `0xc58d36` | `+0xa` |
| `GX_CMD_QUEUE_WRAPPER_RVA_MIN` | `0x1aea900` | `0x1aea880` -> `0x1aec680` | `+0x80` |

(`SHOW_RVA`, `INVADE_ACTION_RVA` and `CANCEL_ACTION_RVA` also land in this bucket but their
"containing function" maps to itself, which means they are not `eldenring.exe` RVAs at all -- check
what module they are relative to before touching them.)

### Worked example: `SPLASH_SKIP_RVA`, done

It was `0xb0c35d`, a byte in the middle of `STEP_BeginLogo`, patched `je(0x74)` -> `jg(0x7f)` with a
raw `VirtualProtect` + store that never went near the address gate. On 1.17 that byte is `0x4a` --
the first displacement byte of a `lea` -- so the write would have corrupted an unrelated
instruction in the title path. **It corrupted nothing only because `apply_splash_skip` compares the
opcode before it writes**, and on 2026-08-29 it logged:

    splash-skip: ABORT -- byte at 0x140b0c35d is 0x4a, expected 0x74

That is the pattern to copy for every raw byte patch: a signature check makes a stale address a
refusal instead of corruption. The address is now `SPLASH_SKIP_FN_RVA = 0xb0c2a0` plus
`SPLASH_SKIP_JE_OFFSET = 0xbd`, resolved through `resolve_game_address`, with the resolver living in
`er-title-flow` beside the constants so the patcher and the telemetry oracle that reads the byte
back cannot disagree about which byte it is. The pair is corroborated four ways: the whole-image
map already carried `0xb0c2a0 -> 0xb0d940`; both functions are `0x294` bytes in `.pdata`; the byte
at `+0xbd` is `0x74` in both images behind the same `bf b8 00 00 00 00` prefix; and the verifier
scores it `IDENTICAL 1.000 over 120 insns, BOTH-ENTRIES`. The `+0x16a0` delta is the same one the
`CS::TitleStep` vtable slots move by, so the region shifted as a block.

## Per-DLL 1.17 readiness, and the one property worth guarding hardest (2026-08-29)

`scripts/audit-1170-readiness.py` scores every cdylib on a single question: **can a stale 1.16.2
address still reach ELDEN RING 1.17 from this DLL without the gate having a say?** Three ways it
can, and they are not equally dangerous, which is the whole reason the tool separates them:

| bucket | what happens on 1.17 |
|---|---|
| **EXEC** | `transmute(base + SOME_RVA)` then call it. Control transfers into whatever now occupies the address. Nothing refuses, nothing logs. WORST. |
| **WRITE** | a raw store at `base + SOME_RVA`. Corrupts the image for every later reader, not just this caller. |
| read/compare | `safe_read_usize(base + rva)`, or `vt != base + SOME_VTABLE_RVA`. Fault-safe: a wrong answer, never a fault. The SILENT class -- the feature quietly stops working. |

**A MAPPED constant is not safe when used this way.** The map knows exactly where the function
went; `base + rva` simply never asks it. Counting only the unmapped ones undercounted the hazard
by half, which is how the first pass of this audit got it wrong.

### Where it stands

    ungated EXEC   210  ->  0    ALL 27 cdylibs
    ungated WRITE    0  ->  0    ALL 27 cdylibs, before and after

**Every game call in this tree now goes through the 1.17 gate.** A function the patch moved and
nothing verified produces a refusal and a named log line, not a jump into whatever now occupies
the address. That is a property of the whole DLL set, not of the DLLs someone remembered to fix.

**0 ungated WRITEs is the property worth guarding hardest**: it means no DLL in this tree can
corrupt the running 1.17 image with a stale address. `--check` is wired into `scripts/check.sh`
and fails when any per-cdylib count RISES, so that stays true by construction rather than by
memory. It divides with `scripts/check-stale-rva-calls.py`, which is the authority on the repo-wide
CALL set; this one adds per-cdylib attribution (a flat repo total hides one DLL regressing while
another improves) and the WRITE/read buckets that nothing else measures. The two independently
written detectors agree to within one site, which is the closest thing to a cross-check available.

### What the conversion looks like

`scripts/gate-stale-rva-calls.py` did 197 of them mechanically, and its value is mostly in what it
REFUSES to touch:

* never inside an `extern "system"` function -- those are detours, and a detour that returns early
  never calls its original, which deletes the game's own behaviour instead of adding ours.
  `hud_weapon_update_hook` needed `return ret`, not `return`;
* only where the return type has an obvious did-nothing value (`()`, `bool`, an integer, `f32`);
  `Result<String, String>` and `Option<(f32, f32)>` are printed for a human rather than guessed at.

The 13 it refused were then done by hand, and the refusals were right every time: three
`Result<_, String>` functions wanted an `Err` carrying a reason, and two were detours where the
correct degraded behaviour is to skip only the ADDED work and still let the game's own body run.
A blind transform would have deleted vanilla behaviour in both detours.

### Runtime evidence lives in a file, never in the audit's head

`docs/recon/dll-1170-runtime-results.json` records the launch outcome per cdylib, and the audit
prints it as a column. It is populated only by an actual launch: a DLL with zero ungated sites is
NOT thereby "working on 1.17", and the tool never infers one from the other. Rows tagged
`(pre-gating)` were measured before their crate was converted and are due a re-run.

## The silent class: data addresses, and how a vtable proves its own identity (2026-08-29)

141 of the unmapped constants are not in `.text` at all -- vtables, globals, tables. The function
gate cannot see them, and a stale one does not crash: the reads are fault-safe, so it yields a
wrong answer and the feature behind it quietly stops working. `TITLE_OWNER_VTABLE_RVA` is
`CS::TitleStep` in 1.16.2 and not a vtable at all in 1.17, and its three scans had been finding no
owner, forever, without a log line.

`scripts/map-data-rvas-1162-to-1170.py` already carries data addresses by VOTING: every
rip-relative reference in 1.16.2 `.text` is mapped onto its 1.17 function and the same instruction
re-read there. It withholds any row under two agreeing references, because a confident wrong
address once cost a boot.

### RTTI rescues what voting withholds

A vtable carries its own name. MSVC puts a CompleteObjectLocator at `vtable[-1]` whose type
descriptor holds the mangled class, and **a name that occurs once per image is stronger evidence
than any number of agreeing displacements**. `rtti_confirms()` accepts a withheld row when the same
mangled name sits at the source in 1.16.2 and the destination in 1.17 **and at neither crossed
position** -- that last condition is what stops a region which happens not to have moved from
passing by accident. Rescued rows carry an `rtti` suffix in the votes column.

    76 -> 81 usable rows, 5 carried by RTTI:
      FUNCTOR_VTABLE_RVA                          0x2ac3ea8 -> 0x2ac6f28
      DEPOSITORY_DIALOG_VFTABLE_RVA               0x2aebba0 -> 0x2aeec20   CS::DepositoryDialog
      SYSTEM_QUIT_RETURN_TITLE_ACTION_VTABLE_RVA  0x2b12b48 -> 0x2b15bc8
      MEMBERFUNCJOB_VTABLE_RVA                    0x2b265d0 -> 0x2b29650   CS::MenuMemberFuncJob<TitleTopDialog>

### Two methods, no shared assumption, same answer

Worth doing for any address you are going to trust. The title vtables were derived twice over:

| | `TITLE_OWNER_VTABLE_RVA` | `TITLE_TOP_DIALOG_VTABLE_RVA` |
|---|---|---|
| RTTI ordinal + slot delta (`find-vtable-rva.py`) | `0x2b63bb0 -> 0x2b66c60` (#16 of 21 TitleStep vtables in both images) | `0x2b26468 -> 0x2b294e8` (all three shift the same `0x3080`) |
| reference voting (`map-data-rvas`) | `0x2b63bb0 -> 0x2b66c60`, 2/2 | `0x2b26468 -> 0x2b294e8`, 2/2 |

All 32 use sites now go through `er_game_base::mem::game_data_addr(base, RVA, "RVA")`, which
resolves for the running build and returns `0` on a refusal -- so an unmapped address takes the
caller's existing "not the object I wanted" branch and says so, instead of silently comparing
against a 1.16.2 value that can never match.

**When refreshing the data map, diff by (constant -> destination), never by line.** A refresh
rewrote 37 of 77 rows and changed ZERO destinations -- only the vote counts moved. Read by line,
that looks like a mass remap.

### Score against all three maps, not one

The resolver's table is fed by three files -- `data.tsv`, `needed-verified.tsv` and `verified.tsv`
(see `crates/er-game-base/build.rs`) -- so "is this constant mapped?" has to be asked of the union.
Asking only the data map said 167 sites needed a new row. Against the union it is 110, and the
57-site difference were free wins: the address was already known and the code was still building it
by hand. `scripts/gate-stale-rva-data.py` now reads all three.

    151 + 57 = 208 data/compare sites routed through `game_data_addr`
    110 sites across 61 constants still need a map row earned first

Note that several of those constants are `.text` addresses used as DATA -- `MENU_ITEM_ACCEPT_NATIVE_RVA`
(`0x7ad810`) is compared against, not called. `map-data-rvas-1162-to-1170.py` skips `.text` by
design, so those rows come from the FUNCTION map instead; a constant missing from one map is not
missing from the gate.

## The gate turned the boot crash into 792 refusals (2026-08-29)

`er_quickload` boots on ELDEN RING 1.17. It had been dying at +1.7s with an execute access
violation on a `CS::CSFadeImp` object; on the fully-gated tree its crash log carries **zero fault
lines** and its debug log carries **792 `ADDRESS REFUSED` entries**. That is the whole mechanism in
one number: 792 stale 1.16.2 addresses that used to be called blind are now refused by name, and
one of them was the fault. A refused address costs a feature; calling it cost the process.

### Per-DLL runtime verdicts, one launch each, on one settled build

    26 boot   1 dies   (27 cdylibs, docs/recon/dll-1170-runtime-results.json)

The single holdout is `er-reload-trace`, which vanishes with no crash record. It installs ~40
diagnostic hooks and its log shows two `create failed status=8` (`MH_ERROR_UNSUPPORTED_FUNCTION`)
before the end -- a tracing shell, not a product one, and its own bug rather than a shared defect.

Two shells that DIED before this work now boot: `er_armament_icons` (was `0xc000001d`
ILLEGAL_INSTRUCTION at `game+0x32ee2b5`) and `er_loading_portrait` (was vanishing ~4s after its
first Present). Both had ungated `transmute(base + rva)` sites; both are clean now.

### Two ways this measurement can lie, and what stops each

* **A mixed build.** The first attempt at this sweep was thrown away because DLLs were rebuilt ten
  minutes into it, so the early entries tested one binary and the rest another. The sweep now
  fingerprints every DLL at the start and ABORTS the instant one changes underneath it.
* **Scoring liveness instead of signature.** Three shells once died three different ways, and
  ranking rows live/dead convicted the wrong crate twice. The results file records the crash
  signature, and `not-built` is never read as a pass -- `er-ags-stub` reported it for a whole run
  because its DLL is `amd_ags_x64.dll`, not `er_ags_stub.dll`; the sweep now reads the real `[lib]
  name` from cargo metadata.

### What this does and does not prove

It proves each DLL can be loaded into 1.17 and the game survives boot, and that no stale address
can execute or corrupt. It does NOT prove any FEATURE works: 792 refusals is 792 things not
happening. Feature-level 1.17 correctness is per-DLL work behind this, and the refusal log is
exactly the to-do list for it.

## Twenty-eight `functions.tsv` pairs two methods refute, and why none was written (2026-08-30)

Two independent passes each found `docs/recon/rva-map-1162-to-1170.functions.tsv` rows they
could refute: the call-graph topology pairing adjudicated by whole-body byte equality
(13 rows), and the leaf pass's caller vote adjudicated by masked extent bytes plus region
delta (19 rows, 4 shared). The negative controls are strong -- the whole-body test accepted
0 of 400 random wrong destinations while accepting 399 of 400 correct ones -- and 16 of the
leaf pass's 19 were independently reproduced by a topology run that did not seed them, with
0 contradicted.

**None of them was written to any ledger, and that is the finding, not an omission.** All 28
are absent from `verified.tsv`, `needed.tsv`, `needed-verified.tsv` and `data.tsv`, and no
literal in `crates/` names any of them. They exist only in `functions.tsv`, which is
gitignored AND is not what `build.rs` reads -- its `FUNCTION_MAP` is `needed.tsv`. So no
wrong address reaches either map from these rows, `refuted_sources()` is not in play, and a
correction written into a tracked ledger would be a dead row.

They are recorded here because `functions.tsv` is regenerated and untracked, so this table is
the only place the derivation survives. **If one of these addresses is ever named by a new
`const *_RVA`, put the corrected pair into `needed.tsv` before running
`select-needed-1170-rows.py --refresh`**: the refresh then REFUSES rather than importing the
wrong value. That guard is real -- planting `0x116c70 -> 0xdeadbe0` on a scratch copy exits 1
with `CONFLICT: ... but functions.tsv now pairs it with 0x116c70`, and writes nothing.

| 1.16.2 | `functions.tsv` says | refuted to | evidence | 1.16.2 name |
|---|---|---|---|---|
| `0x140536630` | `0x140a7e5c0` | `0x140537480` | topology + whole-body bytes | thunk_FUN_145ac79fd |
| `0x1406ab480` | `0x140e56250` | `0x1406ac2d0` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x1409f5e70` | `0x140a96e80` | `0x1409f7150` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140b0f3f0` | `0x140bbab60` | `0x140b10a90` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140b2d250` | `0x141d2b520` | `0x140b2e8f0` | topology + whole-body bytes + caller vote + region delta | thunk_FUN_1459837f8 |
| `0x140d86bc0` | `0x141c99210` | `0x140d88900` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140db28d0` | `0x140e4dd80` | `0x140db4630` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140e44850` | `0x1401b9430` | `0x140e46650` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140e46020` | `0x140e54040` | `0x140e47e20` | caller vote + masked bytes + region delta (topology had no opinion) | — |
| `0x140e4ac30` | `0x1426d51a0` | `0x140e4ca30` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140e4c550` | `0x1406c8190` | `0x140e4e350` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140e4c6d0` | `0x140ae1a80` | `0x140e4e4d0` | topology + whole-body bytes + caller vote + region delta | Game.Network.GetOpenFieldMaxDistFromHostPlayer |
| `0x140e4e3e0` | `0x140654b70` | `0x140e501e0` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x140e51e50` | `0x140540150` | `0x140e53c50` | caller vote + masked bytes + region delta (topology had no opinion) | — |
| `0x140e530b0` | `0x140bf7f30` | `0x140e54eb0` | caller vote + masked bytes + region delta (topology had no opinion) | — |
| `0x140e5dc80` | `0x1406830c0` | `0x140e5fa80` | topology + whole-body bytes + caller vote + region delta | thunk_FUN_1402b9ccd |
| `0x140e5df10` | `0x140e4a8e0` | `0x140e5fd10` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x141125370` | `0x140dfde10` | `0x141127170` | topology + whole-body bytes | SysAllocStatic |
| `0x14181e140` | `0x1404d3780` | `0x14181ff40` | topology + whole-body bytes + caller vote + region delta | thunk_FUN_145333383 |
| `0x141a597c0` | `0x140b125b0` | `0x141a5b5c0` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x141ec5fe0` | `0x141ec7de0` | `0x141ec7e30` | topology + whole-body bytes | FUN_141ec5fe0 |
| `0x141ecf9a0` | `0x140e45e80` | `0x141ed17a0` | caller vote + masked bytes + region delta + unseeded topology | — |
| `0x142028860` | `0x1414c146f` | `0x14202a660` | topology + whole-body bytes | thunk_FUN_144c45a14 |
| `0x142217ae0` | `0x1422198e0` | `0x142219830` | topology + whole-body bytes | FUN_142217ae0 |
| `0x14223f8f0` | `0x14296e345` | `0x142242080` | topology + whole-body bytes | SetSwitch |
| `0x142417b80` | `0x14241a390` | `0x14241a3c0` | topology + whole-body bytes | FUN_142417b80 |
| `0x14296b5a9` | `0x14296dd69` | `0x14296e7f5` | topology + whole-body bytes | Unwind@14296b5a9 |
| `0x14299c180` | `0x14299edb0` | `0x14299ed20` | topology + whole-body bytes | FUN_14299c180 |

Three of the leaf pass's nineteen are NOT in the reproduced set: `0x140e46020`,
`0x140e51e50` and `0x140e530b0`. In the split that withheld each one topology never reached
it, and in the other split it is a seed that merely echoes `functions.tsv`, so topology has
no independent opinion. What would decide them is a third complementary seed split that
both withholds them and anchors their neighbourhood -- NOT a wider ORDER residue, which is
the setting whose cross-run error is 1.881% rather than 0.181%.

Several are adjacent-sibling SWAPS, where `functions.tsv` crossed two same-shape neighbours
(`0x14299c0f0`/`0x14299c180` onto `0x14299ed20`/`0x14299edb0`, and `0x141ec5fe0`/`0x141ec6030`
onto `0x141ec7de0`/`0x141ec7e30`). Masked signature identity cannot separate those; a caller
set can. Apply such a pair as a pair or not at all -- half a swap leaves one address
pointing at the other's function.
