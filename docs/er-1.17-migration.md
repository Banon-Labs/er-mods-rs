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
`0x120b -> 0x1213`. Its first differing byte is a rip-relative displacement at +0x42, which the
verifier normalises away, so `IDENTICAL` is the right verdict and the 8 bytes are elsewhere in the
tail. Worth re-running after any map regeneration -- a size change is the cheapest signal that a
"verified same function" pair deserves a second look.

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
