# ELDEN RING 1.17 address table for the possession / spawn / net / i-frames work

**Status: uncommitted. Every row below is byte-evidence, not permission — read the 1.17 function
before you hook it.**

The installed game has been **1.17** since 2026-08-27, but essentially all of this project's
reverse engineering was done against the **1.16.2** named Ghidra dump. Individual agents verified
their own addresses ad hoc and with inconsistent rigour. This file is the single authoritative,
byte-verified 1.17 table for the addresses those agents left behind.

Machine-readable companion: `npc-possess-rva-map-1170.verified.tsv` (152 rows, written by
`scripts/verify-rva-map-1170.py --tsv`). Input list with provenance:
`npc-possess-1162-addresses.tsv` — every address, and which `bd` memory it came from.

Both images are FLAT: file offset == RVA, `VA = 0x140000000 + offset`, `.rdata` included, and the
**shift is ZERO** in both directions (dump VA == deobf VA == runtime VA).

## THERE IS NO SINGLE 1.16.2 -> 1.17 SHIFT. DO NOT EXTRAPOLATE ONE.

The 152 verified pairs move by **more than a dozen different deltas**:

| delta | pairs | | delta | pairs |
|---|---|---|---|---|
| `+0x10` | 34 | | `+0x550` | 9 |
| `+0x560` | 25 | | `+0x530` | 5 |
| `+0xdd0` | 22 | | `+0x370` | 4 |
| `+0xe50` | 13 | | `+0xe00` | 3 |
| `+0x230` | 11 | | `+0x360` | 2 |
| `+0x16d0` | 9 | | others | 15 |

A neighbouring function's delta is a HINT for the search, never an answer. An estimated shift that
lands mid-instruction is exactly the failure that got `scripts/dump-deobf-shift.py` deleted from
this repo, and it recurred during this sweep in a new disguise — see the RideManipulator entry
under *Corrections*.

## How each row was established

Four independent techniques, in descending order of how often they were needed. A row's `how
resolved` column names the one that produced it.

1. **Relocation-masked byte signature** (`scripts/map-rvas-1162-to-1170.py`) — 120/153. Masks
   displacements, immediates and branch targets, so it survives struct drift. **Every `e8`/`e9`
   rel32 MUST be wildcarded**: a raw prologue copy returns 0 hits on any function containing a
   call, which reads as "the function is gone" rather than "your pattern is wrong". Signatures are
   28–40 bytes; 12 bytes of common frame setup returns 64 hits (the cap) and identifies nothing.
2. **Caller rel32 decoding** (`scripts/resolve-1170-by-caller-rel32.py`, written for this sweep) —
   +18. If a caller C is already mapped to C', then F' is whatever C' calls in the same position.
   Alignment is by **call index**, not byte offset, and both bodies must contain the same number of
   calls, or the tool declines. Several targets got 2–10 independently agreeing callers.
3. **RTTI vtable slot** (`scripts/find-1170-vtable-by-rtti.py`, written for this sweep) — +11.
   Follows the compiler's own record: decorated class name -> TypeDescriptor -> Complete Object
   Locator -> the qword at `vtable-8`. Exact, with no similarity metric anywhere in the chain. It
   re-derives all ten known 1.16.2 vtables correctly, which is its self-test.
4. **Call-graph topology** (`scripts/resolve-1170-by-topology.py`, written for this sweep) — +4.
   For a function with a generic body, no mapped caller and no vtable slot: require an EXACT
   `.pdata` size match plus equal caller and callee counts among the shape-alike candidates.

Every resulting pair was then judged instruction-by-instruction by
`scripts/verify-rva-map-1170.py`, which decodes deep into both bodies and compares them normalised.

## Result

**152 of 153 addresses resolved. 1 was never an address at all. 0 of the 31 pre-existing 1.17
claims in the `bd` memories were wrong.**

| verdict | rows | meaning |
|---|---|---|
| `IDENTICAL-WHOLE` | 100 | whole declared body, normalised, agrees |
| `IDENTICAL-LEAF` | 29 | whole decoded body of a function with no `.pdata` entry |
| `BYTE-IDENTICAL` | 9 | whole body, byte for byte |
| `IDENTICAL-LEAF-NOPATCH` | 4 | body proved; too short to hold a 5-byte jump — **CALL/READ only** |
| `PATCH-SITE-IDENTICAL` | 1 | body differs past the patch site; detour still safe |
| `NEAR` | 4 | **1.17 genuinely changed this code** — see below |
| `IDENTICAL` | 1 | prefix match |

112 rows are `BOTH-ENTRIES` (both images' `.pdata` declares a function there, so a detour may be
installed); 34 are leaves that may be **called or read but never hooked**.

## The four functions 1.17 actually changed

These four are correctly mapped — two of them by the exact RTTI/vtable route, which cannot be
wrong about identity — and the `NEAR` verdict means the **body** was edited in 1.17. Anything
depending on their internals must be re-read, not carried across.

| 1.16.2 | 1.17 | ratio / insns | why it matters |
|---|---|---|---|
| `0x1404275e0` | `0x140427b30` | 0.96 / 911 | **`0ChrActionFlag`, the TAE i-frame jumptable dispatcher.** 4% of it changed. |
| `0x140425ba0` | `0x1404260f0` | 0.99 / 452 | `ActivateChrActionFlagEarly`, its sibling. |
| `0x140660120` | `0x140660f70` | 0.99 / 489 | `PlayerIns` vtable `+0x88` Update. |
| `0x1403b11c0` | `0x1403b11d0` | 1.00 / 688, first diff at insn 684 | `ChrCam::Update` — diverges only at the very end. |

**The i-frame path still works on 1.17.** The dispatcher moved and changed, but the mechanism did
not: all six jumptable case bodies re-derive uniquely in the 1.17 image at a uniform `+0x550`, and
each still writes the same bit into `CSChrActionFlagModule+0x40 actionModifiersFlags`.

| jumptable id | bit | 1.16.2 | 1.17 |
|---|---|---|---|
| jt8 (the roll) | `\|= 0x2` | `0x140427860` | `0x140427db0` |
| jt94 | `\|= 0x1` | `0x1404284d9` | `0x140428a29` |
| jt132 | `\|= 0x10` | `0x1404285c6` | `0x140428b16` |
| jt143 | `\|= 0x20` | `0x140428631` | `0x140428b81` |
| jt24 | `\|= 0x100` | `0x140427c41` | `0x140428191` |
| jt5 | `\|= 0x400` | `0x140427828` | `0x140427d78` |

Structural confirmation that the dispatcher pair is right: 1.16.2 `0x1404275e0` is reached from
`ExecuteThreadOne` (`0x14042e1a0`) at `0x14042e1fe`; 1.17 `0x140427b30` is reached from
`FUN_14042e6f0` at `0x14042e74e` — the same slot of the same dispatch table.

## Class vtables, located by RTTI (exact)

Note the deltas differ per table. Extrapolating any one of them onto another would have been wrong.

| class | 1.16.2 vtable | 1.17 vtable | delta |
|---|---|---|---|
| `ChrIns` | `0x142a2e0b8` | `0x142a310c8` | `+0x3010` |
| `EnemyIns` | `0x142a44010` | `0x142a47090` | `+0x3080` |
| `PlayerIns` | `0x142a7cb40` | `0x142a7fbb0` | `+0x3070` |
| `ChrManipulator` | `0x142a298d8` | `0x142a2c8e8` | `+0x3010` |
| `ComManipulator` | `0x142a29d60` | `0x142a2cd70` | `+0x3010` |
| `PadManipulator` | `0x142a2b778` | `0x142a2e788` | `+0x3010` |
| `NetAIManipulator` | `0x142a2a848` | `0x142a2d858` | `+0x3010` |
| `RideManipulator` | `0x142a2c108` | `0x142a2f118` | `+0x3010` |
| `CSChrDataModule` | `0x142a35088` | `0x142a380b8` | `+0x3030` |
| `CSChrPhysicsModule` | `0x142a39860` | `0x142a3c890` | `+0x3030` |
| `CSFeManImp` | `0x142a9d988` | `0x142aa0a08` | `+0x3080` |
| `WorldChrManDbgImp` | `0x142a4bbc8` | `0x142a4ec48` | `+0x3080` |

## Corrections

**1. `thunk_FUN_140433fe0` IS NOT AN ADDRESS.** `bd er-spawn-chrins-native-path-1162-2026-09-01`
says `WorldChrManImp::RemoveChrIns` "calls chrIns vtable thunk_FUN_140433fe0". There is no such
function: `searchFunctionsByName` returns 0 hits, `0x140433fe0` is mid-body inside `FUN_140433ed0`
(entry `0x140433ed0`), the bytes there decode as `add byte ptr [rax-0x75], cl`, and the value
appears nowhere in the image as a pointer. It is a **Ghidra-generated vtable FIELD NAME** that the
decompiler prints as `(*param_2->_vfptr->thunk_FUN_140433fe0)(param_2)`. The real call is
`ff 50 58` = **`call [rax+0x58]`**, byte-identical in both images:

| ChrIns vtable `+0x58` | 1.16.2 | 1.17 |
|---|---|---|
| `ChrIns` | `0x1403ed530` | `0x1403ed710` |
| `EnemyIns` | `0x1404cf4c0` | `0x1404d0290` |
| `PlayerIns` | `0x140654540` | `0x140655390` |

**2. `scratchpad/dump_funcs.tsv` is the 1.16.1 image — not 1.16.2, and not 1.17.** `bd
per-frame-chrins-move-without-fall-damage-2026-09-01` warns it is "NOT 1.17", which understates
the problem by a whole patch. Measured: 972 sampled functions match `dump-exec.bin` **972/972 =
100%**, against **5/972 (0.5%)** for `eldenring-deobf.bin` (1.16.2) and **2/972 (0.2%)** for
`eldenring-deobf-1.17.bin`. Its addresses are wrong for **both** images anyone still uses. Two
further stale artifacts sit beside it and are the residue of the deleted `dump-deobf-shift.py`:
`dump-exec.bin` (the 1.16.1 flat image) and `scratchpad/shift_per_func.tsv` (its `dump_va -> shift`
staircase). Nothing should read any of the three.

**3. `FUN_140716260` is NOT unmapped.** `bd possession-camoverridechrins-is-the-safe-seam-2026-09-01`
records the lock-on per-frame update as "UNMAPPED (9 shape matches, no anchor)". It is
**`0x1407170b0`**, established three independent ways: two agreeing mapped callers
(`0x140510e90`, `0x1405116c0`) decoding the same rel32; identical `.pdata` size (4708) with
identical caller (2) and callee (56) counts; and its callee list contains the mapped 1.17
`GetMainPlayerIns` `0x140508dc0`.

**4. `ChrRes` ctor is NOT unmapped.** `bd npc-possess-1170-verified-call-addresses-2026-09-01`
records `0x14048c310` as "still returns 0 hits ... remains UNMAPPED for 1.17". It is
**`0x14048c870`**, resolved through its mapped caller `ChrIns::ChrIns`
(`0x1403e6e00` -> `0x1403e6e30`, call #19), verified `IDENTICAL-WHOLE`.

**5. A slot-agreement VOTE cannot separate a class from its parent.** While locating vtables,
`scripts/locate-1170-vtable.py` TIED at 42 agreeing slots between `0x142a2c8e8` and `0x142a2f118`
for `RideManipulator` and returned the first. That was **wrong**: RTTI shows `0x142a2f118` is
`RideManipulator` and `0x142a2c8e8` is **`ChrManipulator`, its base class** — which of course
shares nearly every slot. Inheritance produces slot agreement, so slot agreement cannot measure
identity. The tool now carries that warning and defers to the RTTI chain. This is the same failure
mode as an extrapolated shift, wearing different clothes.

## Struct offsets, re-read on the 1.17 image

### The one that moved: `ChrIns` gained 8 bytes at `+0x3b8`

**1.17 inserts a new 8-byte pointer at `ChrIns+0x3b8`. Every field at `>= 0x3b8` shifts `+8`;
every field at `<= 0x3b0` is unchanged.** Total size stays `0x580` because tail padding absorbs
it — so a stale offset does **not** fault. It silently returns the neighbouring field forever.

| field | 1.16.2 | 1.17 | evidence |
|---|---|---|---|
| `debugFlags` (bit `0x10` no-attack, `0x20` no-move) | `+0x530` | **`+0x538`** | 1.17 `0x1403d0008 test byte [rax+0x538],0x10`; `0x1403cc235 test byte [rax+0x538],0x20` |
| `staminaRecovery` | `+0x534` | **`+0x53c`** | `0x140401b0d mov [rdi+0x53c],eax` — the sole diff in 296 insns of `ChrIns::Update` |

Confirmed independently by an encoding census over the whole image, and the flip is **exclusive,
not additive**: `f6 80 <disp32> 10/20` (`test byte [rax+disp],bit`) occurs in 1.16.2 **twice at
`+0x530` for each bit and zero times at `+0x538`**, and in 1.17 **zero times at `+0x530` and twice
at `+0x538`**.

Full moved set (all `+8`): `0x3b8, 0x3e8, 0x3ec, 0x3f0, 0x3f4, 0x3fc, 0x404, 0x40c, 0x410, 0x440,
0x470, 0x4a0, 0x4d0, 0x500, 0x530, 0x534, 0x538, 0x540, 0x548, 0x54c, 0x550, 0x558, 0x560, 0x568,
0x570`.

The insertion point was pinned by the **destructor**, not by the drift tool. `~ChrIns`
(`0x1403e7940` -> `0x1403e7970`) releases in reverse order starting at `0x398` in *both* builds and
is extended at the **top**: 1.16.2 releases `0x3b0, 0x3a8, 0x3a0, 0x398`; 1.17 releases
`0x3b8, 0x3b0, 0x3a8, 0x3a0, 0x398`; both then `lea rcx,[rdi+0x308]`. The constructor agrees.

### Everything else held

| struct | fields checked | verdict |
|---|---|---|
| `ChrCtrl` | `+0x10 +0x18 +0x20 +0xc0 +0xE8 +0xFC +0x100 +0x110 +0x120 +0x140 +0x2d4 +0x3b0`, size `0x3D0` | **all unchanged** — ctor `0x1403c4d50`->`0x1403c4d60` 0 diffs / 157 insns; `UpdatePositions` `0x1403cd180`->`0x1403cd190` 0 diffs / 44 insns |
| `ChrIns` | `+0x28 +0x58 +0x178 +0x190 +0x1c5 +0x1ca`, size `0x580` | all unchanged (see above for `+0x530`/`+0x534`) |
| `ChrInsModuleContainer` | `+0x0 data, +0x8 actionFlag, +0x28 behavior, +0x38 ai, +0x58 event, +0x68 physics, +0x80 actionRequest, +0x98 damage` | all unchanged; `+0x68 physics` confirmed four independent ways |
| `CSChrDataModule` | `+0x138 +0x13c +0x140 +0x148 +0x14c +0x154 +0x158` | all unchanged |
| `CSChrPhysicsModule` | `+0x70 position, +0x80 prevUpdatePosition, +0x340 hitHeight, +0x344 hitRadius` | all unchanged |
| `CSFeManImp+0x80` | all eight vitals fields `+0x84 +0x88 +0x8c +0x90 +0x98 +0xa4 +0xac +0xb8` | all unchanged |
| `WorldChrManImp` | `+0x1e508 mainPlayerIns`, `+0x1e648 debugChrCreator` | unchanged |
| `WorldChrManDbgImp` | `+0xa8 padManip`, `+0xb8 camOverrideChrIns` | unchanged |
| `EnemyIns` | `+0x580 +0x588 +0x590 +0x598 +0x5a0`, size `0x5e0` | unchanged; `CreateCharacter` `0x140403a60`->`0x140403dd0` has **0 diffs over 174 insns** and keeps `mov ecx,0x5e0` (enemy) / `mov ecx,0x740` (player) |
| `PlayerGameData` | `+0xa58` | **moved to `+0xa5c`** (`+4`), inside the already-documented `[0x960,0xa78)` drift band |

Globals: `GLOBAL_WorldChrMan` `0x143d65f88` -> **`0x143d69ff8`**; `GLOBAL_WorldChrManDbg`
`0x143d66198` -> **`0x143d6a208`**. (Note the second happens to equal the *1.16.2* `LockTgtMan`
address — do not let that coincidence mislead a reader.)

### Corrections to the field names the brief carried

* `CSChrPhysicsModule` `needsChrMatrixUpdate` is **`+0x90`** and `chrProxyPosUpdateNeeded` is
  **`+0x91`** — not `+0x150`/`+0x1b0` as the brief listed.
* `CSChrDataModule hpBase` is **`+0x144`**, which was missing from the list entirely.
* `EnemyIns` vtable `+0x88` does **not** tail-jump *unguarded*. It runs guarded pre-work (reads
  `ChrIns+0x190`, tests `ChrIns+0x1c4 & 0x20`, calls the manipulators at `+0x5a8`/`+0x5b0`) and
  *then* tail-jumps to `ChrIns::Update`. The address is right; "unguarded" is wrong.

## Item 2: the two explicitly-unverified items

**(a) `WorldChrManDbgImp+0xb8 camOverrideChrIns` — CONFIRMED, still `+0xb8`.** The 1.17 accessor is
`0x142066514` (reached via thunk `0x140514010`), and it is **byte-identical** to 1.16.2
`0x140df35f7`: `48 8b 81 b8 00 00 00` = `mov rax,[rcx+0xb8]`. The 16-byte thunk pattern is unique
in both images. The **reader set is still exactly 5**, at identical intra-function offsets:

| role | 1.16.2 | 1.17 |
|---|---|---|
| `GetMainPlayerIns` | `0x140507ff0` (+0x2a) | `0x140508dc0` (+0x2a) |
| `GetMainPlayerInsOrMount` | `0x140508050` (+0x2f) | `0x140508e20` (+0x2f) |
| `ChrCtrl::Unref` | `0x1403c52a0` (+0xb6) | `0x1403c52b0` (+0xb6) |
| `WorldChrManImp::RemoveChrIns` | `0x14050a570` (+0x133) | `0x14050b340` (+0x133) |
| `WorldChrMan_Update_RideCheck` | `0x14050eb31` | `0x14050f931` |

**(b) `EnemyIns` layout / vtable — CONFIRMED.** 1.17 vtable `0x142a47090` is **RTTI-proven** (COL
`0x1432d7d90` -> TypeDescriptor `0x143c84e60` -> `.?AVEnemyIns@CS@@`), **203 slots on both**.

| slot | 1.16.2 | 1.17 | 1.17 shape read |
|---|---|---|---|
| `+0x088` Update | `0x1404d0c30` | `0x1404d1a00` | guarded pre-work, then tail-`jmp 0x140401a30` |
| `+0x118` IsPlayerIns | `0x1403f4530` | `0x1403f4760` | `xor al,al; ret` = **ret 0** |
| `+0x168` GetPlayerGameData | `0x1403f0b20` | `0x1403f0d50` | `jmp 0x140260440` |
| `+0x218` GetChrAsm | `0x1403eebe0` | `0x1403eee10` | `xor eax,eax; ret` = **ret 0** |
| `+0x220` GetChrAsm2 | `0x1403eebd0` | `0x1403eee00` | `xor eax,eax; ret` = **ret 0** |
| `+0x228` GetEquipmentEntry | `0x1403f1f60` | `0x1403f2190` | `or eax,-1; ret` = **ret -1** |
| `+0x230` GetWeaponGaitemHandleBySlot | `0x1403f1f50` | `0x1403f2180` | `mov [rdx],0; mov rax,rdx; ret` |
| `+0x1d8` IsImmuneToAttack | `0x1403f3b90` | `0x1403f3dc0` | identical body |
| `+0x320` ResolveBehaviorId | `0x1404cf040` | `0x1404cfe10` | identical body |

## Item 4: anchors added, and the four dialog functions mapped

Seven new anchors in the previously empty regions, each a **unique** relocation-masked match, each
whole-body verified, all now in `rva-map-1162-to-1170.verified.tsv` (103 -> 114 data rows, none lost):

| region | 1.16.2 | 1.17 | delta | verdict |
|---|---|---|---|---|
| `0x87xxxx` | `0x140875750` | `0x140876740` | `+0xff0` | IDENTICAL-WHOLE |
| `0x87xxxx` | `0x140875980` | `0x140876970` | `+0xff0` | IDENTICAL-WHOLE |
| `0x92xxxx` | `0x1409202d0` | `0x140921470` | `+0x11a0` | IDENTICAL-WHOLE |
| `0x92xxxx` | `0x1409231d0` | `0x140924370` | `+0x11a0` | IDENTICAL-WHOLE |
| `0x92xxxx` | `0x140923600` | `0x1409247a0` | `+0x11a0` | IDENTICAL-WHOLE |
| `0x9axxxx` | `0x1409a4e10` | `0x1409a5fb0` | `+0x11a0` | IDENTICAL-WHOLE |
| `0x9axxxx` | `0x1409a5160` | `0x1409a6300` | `+0x11a0` | **BYTE-IDENTICAL** |

All four targets resolve, all `IDENTICAL-WHOLE` ratio 1.000, `BOTH-ENTRIES`, `.pdata` extents equal:

| function | 1.16.2 | 1.17 | delta | what the 1.17 read confirmed |
|---|---|---|---|---|
| list builder | `0x140875590` | `0x140876580` | `+0xff0` | same 10-slot `GetProfileSummary` loop, same three vftable stores (`MenuViewItemListBase`, `MenuViewItemList<MenuSaveDataSummary>`, `BasicViewItemList<MenuSaveDataSummary,10>`), same `0xbd0` frame |
| activate | `0x1409a4670` | `0x1409a5810` | `+0x11a0` | same MSVC **lambda identity hash** `lambda_4c99f2aa9be82b9e77290caf9df1593f`, same FMG ids `0x61edb`/`0x631fb`, same 決定 caption, same `+0x147`/`[0x161]`/`[0x399]`/`+0x295` offsets |
| list rebuild | `0x1409a4ed0` | `0x1409a6070` | `+0x11a0` | same vftable stores, same `0x21`-stride dtor loop; calls the mapped list builder |
| `AddCancelButton` | `0x140920c90` | `0x140921e30` | `+0x11a0` | same lambda hash `lambda_633960ee0b3fb409d37bb910424db95f`, byte-identical frame |

### The `+0xe50` warning was load-bearing — extrapolating it fails on all four

| target | real delta | `+0xe50` would have landed on |
|---|---|---|
| `0x140875590` | `+0xff0` | `0x1408763e0` — **a valid, declared 1.17 function entry, but the counterpart of a DIFFERENT 1.16.2 function.** A detour installs cleanly and silently hooks the wrong function: no crash, no log line, wrong behaviour. |
| `0x1409a4670` | `+0x11a0` | `0x1409a54c0` — `0x1b0` bytes *inside* `FUN_1409a5310` |
| `0x1409a4ed0` | `+0x11a0` | `0x1409a5d20` — `0x510` bytes *inside* the activate target |
| `0x140920c90` | `+0x11a0` | `0x140921ae0` — `0x10` bytes *inside* `0x140921ad0` |

## Two tool defects found and fixed during this sweep

**1. `scripts/map-rvas-1162-to-1170.py` truncated its candidate list at 9.** `bytes.find` walks the
image low-to-high, so the list handed to the anchor pass was "the first nine matches", not "the
matches". `0x1409a4670`'s true counterpart is **hit 39 of 51** and was discarded before any anchor
could be consulted — so the familiar note `9 shape matches, none at the nearest anchor's delta` was
a **truncation, not a disagreement**. Cap raised to 512. Re-running this sweep's 153 addresses
against the fixed tool maps 132 (up from 120) and **agrees with every pair in the table below,
zero disagreements** — the table is robust to the fix.

**2. `scripts/verify-rva-map-1170.py --tsv` destroyed comment lines.** Its carry-forward guard
counted **rows only**. One write preserved all 103 data rows and deleted **181 of the ledger's 209
comment lines**, at exit 0, with nothing on stderr naming them. That prose is where the ledger
records why addresses were removed or deliberately refused. Fixed: comments and blanks now carry in
original order, and `--tsv` twice is byte-idempotent.

**3. `scripts/pair-object-field-drift.py` reports the wrong insertion point.** For `ChrIns` it says
`0x390` and claims `0x390/0x398/0x3a0/0x3a8/0x3b0` all moved `+8`. They did not — following it
would mis-shift five live pointer fields. It masks numeric literals before `difflib`, so a run of
identical `mov qword [rdi+X], r15` null stores is unalignable and it charges the extra store to the
*first* position rather than the last. Prefer ctor+dtor pairing until it is fixed.

**Coverage gap**: `scripts/check-object-field-offsets-1170.py` covers 12 classes and **none** of
`ChrIns`/`ChrCtrl`/`EnemyIns`/`PlayerIns`/`WorldChrManDbgImp`. `ChrIns +0x530 -> +0x538` is exactly
the silent failure that gate exists to catch, and it is currently uncaught.

## Other stale sources found

* `crates/er-quickload/src/constants/profile_render.rs` lines 66–96 cite **`dump-deobf-shift`** as
  provenance — the tool AGENTS.md bans as actively wrong — and name `FUN_140875680` and
  `FUN_1409a2e40`, both **mid-function** in 1.16.2. Doc comments only; the live constants are correct.
* `WORLD_CHR_MAN_PLAYER_INS_OFFSET = 0x1e508` is centralised in `crates/er-game-base/src/rva.rs:381`
  but re-declared privately in `crates/er-build-import-runtime/src/equip_native.rs:135` and
  `.../grant.rs:337`. The value is correct on 1.17, so this is hygiene, not a bug — but it is a
  struct offset, so nothing translates it, and the day it moves those copies will not follow.

## Verified table

| 1.16.2 VA | 1.17 VA | delta | name | verdict (insns) | how resolved | detourable |
|---|---|---|---|---|---|---|
| `0x1403c8c40` | `0x1403c8c50` | `+0x10` | ChrCtrl::UpdateAi | IDENTICAL-LEAF (17) | unique, 40B signature, 26B fixed | CALL/READ only |
| `0x140492cb0` | `0x140493210` | `+0x560` | ChrSet::SpawnSummonBuddy | IDENTICAL-WHOLE (99) | unique, 41B signature, 28B fixed | yes |
| `0x140403a60` | `0x140403dd0` | `+0x370` | ChrInsFactory::CreateCharacter | IDENTICAL-WHOLE (174) | unique, 42B signature, 27B fixed | yes |
| `0x14050a570` | `0x14050b340` | `+0xdd0` | WorldChrManImp::RemoveChrIns | IDENTICAL-WHOLE (126) | unique, 42B signature, 30B fixed | yes |
| `0x1404cd710` | `0x1404ce4e0` | `+0xdd0` | EneDatMan acquire FUN_1404cd710 | IDENTICAL-WHOLE (83) | unique, 40B signature, 32B fixed | yes |
| `0x1404cd870` | `0x1404ce640` | `+0xdd0` | EneDatMan release FUN_1404cd870 | IDENTICAL-WHOLE (37) | unique, 42B signature, 33B fixed | yes |
| `0x1403ef830` | `0x1403efa60` | `+0x230` | ChrIns::GetEneDat | BYTE-IDENTICAL (47) | unique, 39B signature, 27B fixed | yes |
| `0x14048c310` | `0x14048c870` | `+0x560` | ChrRes ctor | IDENTICAL-WHOLE (60) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140303ec0` | `0x140303ed0` | `+0x10` | CSAiFunc::TurnTo | IDENTICAL-LEAF (3) | nearest-anchor delta +0x10, 9 shape candidates | CALL/READ only |
| `0x14043aa30` | `0x14043af90` | `+0x560` | CSChrEventModule::RequestAnimation | IDENTICAL-WHOLE (20) | unique, 42B signature, 27B fixed | yes |
| `0x1403cf340` | `0x1403cf350` | `+0x10` | ComManipulator::UpdateAI (vft+0x48) | IDENTICAL-LEAF (1) | RTTI vtable slot ComManipulator+0x48 | CALL/READ only |
| `0x1403cf2a0` | `0x1403cf2b0` | `+0x10` | ComManipulator tick (vft+0x50) | IDENTICAL-WHOLE (35) | unique, 42B signature, 26B fixed | yes |
| `0x1403d0250` | `0x1403d0260` | `+0x10` | FUN_1403d0250 UpdateMovement | IDENTICAL-WHOLE (265) | nearest-anchor delta +0x10, 9 shape candidates | yes |
| `0x1403c7800` | `0x1403c7810` | `+0x10` | ChrCtrl::ShouldUpdateAi | IDENTICAL-WHOLE (57) | unique, 40B signature, 27B fixed | yes |
| `0x1403c8da0` | `0x1403c8db0` | `+0x10` | ChrCtrl tick dispatcher | IDENTICAL-WHOLE (134) | unique, 41B signature, 31B fixed | yes |
| `0x1404016d0` | `0x140401a30` | `+0x360` | ChrIns::Update | IDENTICAL-WHOLE (296) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140400960` | `0x140400c50` | `+0x2f0` | ChrIns::UpdateAiLogic | IDENTICAL-LEAF (3) | unique, 14B signature, 8B fixed | CALL/READ only |
| `0x1403c52a0` | `0x1403c52b0` | `+0x10` | ChrCtrl::Unref | IDENTICAL-WHOLE (204) | nearest-anchor delta +0x10, 9 shape candidates | yes |
| `0x1403c4d50` | `0x1403c4d60` | `+0x10` | ChrCtrl ctor | IDENTICAL-WHOLE (245) | unique, 44B signature, 29B fixed | yes |
| `0x1403d8660` | `0x1403d8670` | `+0x10` | PadManipulator ctor | IDENTICAL-WHOLE (111) | unique, 40B signature, 27B fixed | yes |
| `0x1403d88a0` | `0x1403d88b0` | `+0x10` | PadManipulator dtor FUN_1403d88a0 | IDENTICAL-WHOLE (62) | nearest-anchor delta +0x10, 2 shape candidates | yes |
| `0x1403c6f40` | `0x1403c6f50` | `+0x10` | ChrCtrl::GetManipulator | IDENTICAL-LEAF (5) | unique, 17B signature, 11B fixed | CALL/READ only |
| `0x1404d0c30` | `0x1404d1a00` | `+0xdd0` | EnemyIns vft+0x88 Update | IDENTICAL-WHOLE (33) | unique, 40B signature, 27B fixed | yes |
| `0x140660120` | `0x140660f70` | `+0xe50` | PlayerIns vft+0x88 Update | NEAR (489) | RTTI vtable slot PlayerIns+0x88 | yes |
| `0x1403cda90` | `0x1403cdaa0` | `+0x10` | PadManipulator vft+0x48 base-noop | IDENTICAL-LEAF-NOPATCH (1) | RTTI vtable slot ChrManipulator+0x48 | CALL/READ only |
| `0x1403d9f20` | `0x1403d9f30` | `+0x10` | PadManipulator vft+0x50 | IDENTICAL-WHOLE (60) | unique, 42B signature, 29B fixed | yes |
| `0x1403daa80` | `0x1403daa90` | `+0x10` | PadManipulator tick FUN_1403daa80 | IDENTICAL-WHOLE (600) | nearest-anchor delta +0x10, 3 shape candidates | yes |
| `0x1403d4280` | `0x1403d4290` | `+0x10` | NetAIManipulator vft+0x48 | BYTE-IDENTICAL (55) | unique, 42B signature, 27B fixed | yes |
| `0x1403d3b00` | `0x1403d3b10` | `+0x10` | NetAIManipulator vft+0x50 | IDENTICAL-WHOLE (399) | nearest-anchor delta +0x10, 3 shape candidates | yes |
| `0x1403e08f0` | `0x1403e0900` | `+0x10` | RideManipulator vft+0x50 | DIVERGES (4) | RTTI vtable slot RideManipulator+0x50 | CALL/READ only |
| `0x140406d40` | `0x140407270` | `+0x530` | CSChrActionRequestModule | IDENTICAL-WHOLE (20) | nearest-anchor delta +0x530, 9 shape candidates | yes |
| `0x140407c60` | `0x140408190` | `+0x530` | CSChrActionRequestModule::UpdateFromManipulator | IDENTICAL-WHOLE (159) | unique, 42B signature, 36B fixed | yes |
| `0x140507ff0` | `0x140508dc0` | `+0xdd0` | WorldChrManImp::GetMainPlayerIns | IDENTICAL-WHOLE (26) | unique, 42B signature, 29B fixed | yes |
| `0x1403f4040` | `0x1403f4270` | `+0x230` | PlayerIns::IsMainPlayerIns | IDENTICAL-WHOLE (20) | unique, 41B signature, 26B fixed | yes |
| `0x1403b11c0` | `0x1403b11d0` | `+0x10` | ChrCam::Update | NEAR (688) | unique, 40B signature, 29B fixed | yes |
| `0x14067a7a0` | `0x14067b5f0` | `+0xe50` | GameMan::SetCamZoomSimpleLerp | IDENTICAL-LEAF (5) | unique, 22B signature, 15B fixed | CALL/READ only |
| `0x140716260` | `0x1407170b0` | `+0xe50` | LockTgtMan per-frame update FUN_140716260 | IDENTICAL-WHOLE (1069) | caller rel32, 2 agreeing caller(s) | yes |
| `0x140508050` | `0x140508e20` | `+0xdd0` | GetMainPlayerInsOrMount FUN_140508050 | IDENTICAL-WHOLE (40) | unique, 41B signature, 28B fixed | yes |
| `0x1403b5ac0` | `0x1403b5ad0` | `+0x10` | ChrExFollowCam::Update | IDENTICAL-WHOLE (511) | unique, 40B signature, 24B fixed | yes |
| `0x140affe40` | `0x140b012d0` | `+0x1490` | camera update FUN_140affe40 | IDENTICAL-WHOLE (184) | unique, 41B signature, 32B fixed | yes |
| `0x14065a330` | `0x14065b180` | `+0xe50` | PlayerIns::UpdateSafePosition | IDENTICAL-WHOLE (48) | topology size+callees+callers | yes |
| `0x140659eb0` | `0x14065ad00` | `+0xe50` | PlayerIns::UpdateBlockPosition | IDENTICAL-WHOLE (45) | nearest-anchor delta +0xe50, 2 shape candidates | yes |
| `0x1405110e0` | `0x140511ee0` | `+0xe00` | WorldChrMan_PrePhysics | IDENTICAL-WHOLE (128) | unique, 46B signature, 30B fixed | yes |
| `0x140df35f7` | `0x142066514` | `+0x1272f1d` | WorldChrManDbg+0xb8 accessor FUN_140df35f7 | DIVERGES (6) | thunk target, byte-identical mov rax,[rcx+0xb8] | CALL/READ only |
| `0x140513210` | `0x140514010` | `+0xe00` | thunk to +0xb8 accessor | IDENTICAL-LEAF (1) | caller rel32, 4 agreeing caller(s) | CALL/READ only |
| `0x140492e20` | `0x140493380` | `+0x560` | ChrSet::SpawnChr | IDENTICAL-WHOLE (166) | unique, 41B signature, 29B fixed | yes |
| `0x140492a90` | `0x140492ff0` | `+0x560` | ChrSet ghost-band spawn FUN_140492a90 | IDENTICAL-WHOLE (144) | nearest-anchor delta +0x560, 2 shape candidates | yes |
| `0x140493140` | `0x1404936a0` | `+0x560` | ChrSet entries[0..6] spawn FUN_140493140 | IDENTICAL-WHOLE (139) | unique, 42B signature, 29B fixed | yes |
| `0x1404942a0` | `0x140494800` | `+0x560` | ChrSet::AddChrInsToGroupIdMap | IDENTICAL-WHOLE (104) | nearest-anchor delta +0x560, 9 shape candidates | yes |
| `0x1404ba980` | `0x1404baea0` | `+0x520` | SummonBuddyManager::CreateSummonChr | IDENTICAL-WHOLE (639) | caller rel32, 2 agreeing caller(s) | yes |
| `0x140494440` | `0x1404949a0` | `+0x560` | OpenFieldChrSet spawner FUN_140494440 | IDENTICAL-WHOLE (351) | nearest-anchor delta +0x560, 3 shape candidates | yes |
| `0x1403e6e00` | `0x1403e6e30` | `+0x30` | ChrIns::ChrIns | PATCH-SITE-IDENTICAL (467) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140402800` | `0x140402b70` | `+0x370` | ChrNameToInt | IDENTICAL-LEAF (16) | unique, 43B signature, 38B fixed | CALL/READ only |
| `0x14048dc10` | `0x14048e170` | `+0x560` | ChrRes resource-request step FUN_14048dc10 | IDENTICAL-WHOLE (196) | unique, 45B signature, 31B fixed | yes |
| `0x1404caaf0` | `0x1404cb680` | `+0xb90` | EneDat asset builder FUN_1404caaf0 | DIVERGES (516) | caller rel32, 1 agreeing caller(s) | yes |
| `0x1404cd460` | `0x1404ce230` | `+0xdd0` | EneDat all-loaded poll FUN_1404cd460 | IDENTICAL-LEAF (9) | unique, 33B signature, 19B fixed | CALL/READ only |
| `0x1403e5bc0` | `0x1403e5bd0` | `+0x10` | chrId to L'cNNNN' FUN_1403e5bc0 | IDENTICAL-LEAF (56) | topology size+callees+callers | CALL/READ only |
| `0x1403e62f0` | `0x1403e6320` | `+0x30` | chrbnd path builder FUN_1403e62f0 | IDENTICAL-WHOLE (73) | caller rel32, 1 agreeing caller(s) | yes |
| `0x1404cdcc0` | `0x1404cea90` | `+0xdd0` | EneDat acquire debug precedent FUN_1404cdcc0 | IDENTICAL-WHOLE (46) | unique, 41B signature, 19B fixed | yes |
| `0x140494a50` | `0x140494fb0` | `+0x560` | ChrSet::RemoveChrIns | IDENTICAL-WHOLE (69) | unique, 40B signature, 28B fixed | yes |
| `0x140e76ea0` | `0x140e78ca0` | `+0x1e00` | CSDelayDeleteMan enqueue FUN_140e76ea0 | IDENTICAL-WHOLE (73) | caller rel32, 1 agreeing caller(s) | yes |
| `0x1403f2a90` | `0x1403f2cc0` | `+0x230` | IsChrInDebugChrSet | IDENTICAL-WHOLE (25) | nearest-anchor delta +0x230, 2 shape candidates | yes |
| `0x1404032d0` | `0x140403640` | `+0x370` | ChrInsFactory ctor | IDENTICAL-LEAF (4) | nearest-anchor delta +0x370, 9 shape candidates | CALL/READ only |
| `0x1404032e0` | `0x140403650` | `+0x370` | ChrInsFactory dtor | IDENTICAL-LEAF (3) | caller rel32, 10 agreeing caller(s) | CALL/READ only |
| `0x1404ce0a0` | `0x1404cee70` | `+0xdd0` | EnemyIns::EnemyIns | IDENTICAL-WHOLE (365) | nearest-anchor delta +0xdd0, 2 shape candidates | yes |
| `0x1403f2470` | `0x1403f26a0` | `+0x230` | ChrIns::InitializeCharacter | IDENTICAL-WHOLE (274) | nearest-anchor delta +0x230, 2 shape candidates | yes |
| `0x14048fbf0` | `0x140490150` | `+0x560` | FD4FileCap status check FUN_14048fbf0 | IDENTICAL-WHOLE (420) | topology size+callees+callers | yes |
| `0x140506f30` | `0x140507d00` | `+0xdd0` | WorldChrManImp buddy spawn FUN_140506f30 | IDENTICAL-WHOLE (18) | nearest-anchor delta +0xdd0, 2 shape candidates | yes |
| `0x140494f50` | `0x1404954b0` | `+0x560` | buddy ChrSet init FUN_140494f50 | IDENTICAL-WHOLE (58) | nearest-anchor delta +0x560, 3 shape candidates | yes |
| `0x1405ee180` | `0x1405eefd0` | `+0xe50` | GetChrInsByEntityId | IDENTICAL-WHOLE (116) | unique, 45B signature, 33B fixed | yes |
| `0x1403f73e0` | `0x1403f7610` | `+0x230` | SetMountHandles | IDENTICAL-LEAF (27) | unique, 17B signature, 13B fixed | CALL/READ only |
| `0x14050e1a0` | `0x14050efa0` | `+0xe00` | WorldChrMan_Respawn | IDENTICAL-WHOLE (171) | unique, 46B signature, 30B fixed | yes |
| `0x140e9ce30` | `0x140e9ec30` | `+0x1e00` | CSTalkDynamicChrCtrl FUN_140e9ce30 | IDENTICAL-WHOLE (197) | topology size+callees+callers | yes |
| `0x1403ef730` | `0x1403ef960` | `+0x230` | GetChrCreator | IDENTICAL-WHOLE (52) | unique, 42B signature, 23B fixed | yes |
| `0x1403c8610` | `0x1403c8620` | `+0x10` | per-frame updatePos | IDENTICAL-WHOLE (212) | unique, 41B signature, 28B fixed | yes |
| `0x1403cd180` | `0x1403cd190` | `+0x10` | ChrCtrl::UpdatePositions | IDENTICAL-WHOLE (80) | unique, 46B signature, 28B fixed | yes |
| `0x14045f910` | `0x14045fe70` | `+0x560` | CSChrPhysicsModule::ForceSetPosition | IDENTICAL-LEAF (7) | unique, 34B signature, 20B fixed | CALL/READ only |
| `0x14045f7a0` | `0x14045fd00` | `+0x560` | CSChrPhysicsModule::SetOrientation | BYTE-IDENTICAL (57) | unique, 43B signature, 31B fixed | yes |
| `0x140461a00` | `0x140461f60` | `+0x560` | EulerToQuat | IDENTICAL-WHOLE (179) | unique, 47B signature, 34B fixed | yes |
| `0x140454c00` | `0x140455160` | `+0x560` | CSChrMaterialModule::SetDisableFallDamage | IDENTICAL-LEAF-NOPATCH (2) | caller rel32, 1 agreeing caller(s) | CALL/READ only |
| `0x140c456b0` | `0x140c46d80` | `+0x16d0` | caller of updatePos FUN_140c456b0 | IDENTICAL-WHOLE (54) | unique, 42B signature, 31B fixed | yes |
| `0x1403da750` | `0x1403da760` | `+0x10` | net position publisher FUN_1403da750 | IDENTICAL-WHOLE (189) | unique, 43B signature, 28B fixed | yes |
| `0x1403f0bf0` | `0x1403f0e20` | `+0x230` | ChrIns::GetPhysicsPosition | IDENTICAL-WHOLE (9) | nearest-anchor delta +0x230, 9 shape candidates | yes |
| `0x140679630` | `0x14067a480` | `+0xe50` | GetTargetMapId | IDENTICAL-LEAF (5) | nearest-anchor delta +0xe50, 9 shape candidates | CALL/READ only |
| `0x14061e270` | `0x14061f0c0` | `+0xe50` | ConvertPhysicsCoordsToBlockCoords | IDENTICAL-WHOLE (69) | unique, 40B signature, 29B fixed | yes |
| `0x1404e9ad0` | `0x1404ea8a0` | `+0xdd0` | ChrPackingStructure::SetLocation | IDENTICAL-LEAF (8) | unique, 30B signature, 21B fixed | CALL/READ only |
| `0x1403d99e0` | `0x1403d99f0` | `+0x10` | PadManipulator publish slot FUN_1403d99e0 | IDENTICAL-WHOLE (100) | unique, 41B signature, 26B fixed | yes |
| `0x1403d9bb0` | `0x1403d9bc0` | `+0x10` | publish caller FUN_1403d9bb0 | IDENTICAL-WHOLE (97) | nearest-anchor delta +0x10, 9 shape candidates | yes |
| `0x140c9f430` | `0x140ca0b00` | `+0x16d0` | FUN_140c9f430 | IDENTICAL-WHOLE (160) | unique, 44B signature, 30B fixed | yes |
| `0x140c9f770` | `0x140ca0e40` | `+0x16d0` | FUN_140c9f770 | IDENTICAL-WHOLE (57) | unique, 44B signature, 28B fixed | yes |
| `0x1403cd370` | `0x1403cd380` | `+0x10` | ChrManipulator::ChrManipulator | IDENTICAL-LEAF (24) | unique, 45B signature, 25B fixed | CALL/READ only |
| `0x1403c76d0` | `0x1403c76e0` | `+0x10` | ChrCtrl::SetManipulator | IDENTICAL-WHOLE (61) | unique, 41B signature, 30B fixed | yes |
| `0x1404e8060` | `0x1404e8e30` | `+0xdd0` | MakeChrSyncPacker | IDENTICAL-WHOLE (144) | unique, 46B signature, 33B fixed | yes |
| `0x1404e5860` | `0x1404e6630` | `+0xdd0` | ReplayRecorder packer FUN_1404e5860 | IDENTICAL-WHOLE (100) | unique, 41B signature, 31B fixed | yes |
| `0x1404e4d20` | `0x1404e5af0` | `+0xdd0` | ReplayRecorder FUN_1404e4d20 | IDENTICAL-WHOLE (459) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140474550` | `0x140474ab0` | `+0x560` | CSChrRideModule::GetForwardingTarget | IDENTICAL-LEAF (2) | caller rel32, 3 agreeing caller(s) | CALL/READ only |
| `0x140509f90` | `0x14050ad60` | `+0xdd0` | GetNetChrSyncPositionUpdateBuffer | BYTE-IDENTICAL (64) | nearest-anchor delta +0xdd0, 4 shape candidates | yes |
| `0x1404275e0` | `0x140427b30` | `+0x550` | TAE 0ChrActionFlag handler | NEAR (911) | unique, 40B signature, 35B fixed | yes |
| `0x140425ba0` | `0x1404260f0` | `+0x550` | ActivateChrActionFlagEarly | NEAR (452) | unique, 43B signature, 33B fixed | yes |
| `0x1403f3b90` | `0x1403f3dc0` | `+0x230` | ChrIns::IsImmuneToAttack | IDENTICAL (75) | unique, 40B signature, 26B fixed | CALL/READ only |
| `0x1404483b0` | `0x140448910` | `+0x560` | CSChrDamageModule::CalculateDamage2 | IDENTICAL-WHOLE (703) | unique, 40B signature, 29B fixed | yes |
| `0x140656e60` | `0x140657cb0` | `+0xe50` | PlayerIns::IsInvincible | IDENTICAL-WHOLE (17) | nearest-anchor delta +0xe50, 3 shape candidates | yes |
| `0x140401bd0` | `0x140401f30` | `+0x360` | ChrIns::PreBehaviorSafe | IDENTICAL-WHOLE (30) | unique, 45B signature, 25B fixed | yes |
| `0x140405b80` | `0x1404060b0` | `+0x530` | actionModifiersFlags zeroer FUN_140405b80 | IDENTICAL-WHOLE (178) | unique, 41B signature, 30B fixed | yes |
| `0x140430960` | `0x140430eb0` | `+0x550` | CSChrTimeActModule::RunTaeAndUpdateAnimQueue | IDENTICAL-WHOLE (90) | unique, 40B signature, 30B fixed | yes |
| `0x14041b740` | `0x14041bc70` | `+0x530` | TAE_Callback | IDENTICAL-WHOLE (45) | unique, 43B signature, 34B fixed | yes |
| `0x140c14370` | `0x140c15a40` | `+0x16d0` | PlayAnimationByBehaviorName | BYTE-IDENTICAL (80) | unique, 40B signature, 30B fixed | yes |
| `0x1404266d0` | `0x140426c20` | `+0x550` | CSChrTaeAnimEvent::AttackBehavior | IDENTICAL-WHOLE (193) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140426e60` | `0x1404273b0` | `+0x550` | CSChrTaeAnimEvent::BulletBehavior | IDENTICAL-WHOLE (199) | unique, 41B signature, 24B fixed | yes |
| `0x1404269e0` | `0x140426f30` | `+0x550` | CSChrTaeAnimEvent::CommonBehavior | IDENTICAL-WHOLE (52) | unique, 40B signature, 29B fixed | yes |
| `0x140429db0` | `0x14042a300` | `+0x550` | CastHighlightedMagic | IDENTICAL-WHOLE (141) | nearest-anchor delta +0x550, 9 shape candidates | yes |
| `0x140426ca0` | `0x1404271f0` | `+0x550` | SpawnFFXBySpEffect2 | IDENTICAL-WHOLE (61) | unique, 41B signature, 27B fixed | yes |
| `0x14042f150` | `0x14042f6a0` | `+0x550` | CSChrTaeAnimEvent::ExecuteThreadTwo | DIVERGES (193) | unique, 44B signature, 25B fixed | yes |
| `0x1404cf040` | `0x1404cfe10` | `+0xdd0` | EnemyIns::ResolveBehaviorId | IDENTICAL-WHOLE (128) | unique, 42B signature, 29B fixed | yes |
| `0x1403e9380` | `0x1403e9560` | `+0x1e0` | ChrIns::ResolveBehaviorId (base) | IDENTICAL-LEAF-NOPATCH (2) | RTTI vtable slot ChrIns+0x320 | CALL/READ only |
| `0x1403f4b90` | `0x1403f4dc0` | `+0x230` | IsValidBehaviorJudgeID | IDENTICAL-LEAF (3) | nearest-anchor delta +0x230, 3 shape candidates | CALL/READ only |
| `0x140652280` | `0x1406530d0` | `+0xe50` | PlayerIns::ResolveBehaviorId | IDENTICAL-WHOLE (125) | unique, 40B signature, 30B fixed | yes |
| `0x140d24130` | `0x140d25840` | `+0x1710` | LookupBehaviorParam | IDENTICAL-WHOLE (52) | unique, 41B signature, 31B fixed | yes |
| `0x140d240e0` | `0x140d257f0` | `+0x1710` | BehaviorParam::GetAtkParam | IDENTICAL-WHOLE (22) | unique, 34B signature, 26B fixed | yes |
| `0x140d23090` | `0x140d24760` | `+0x16d0` | GetAtkParam | IDENTICAL-WHOLE (56) | unique, 41B signature, 31B fixed | yes |
| `0x1404fa350` | `0x1404fb120` | `+0xdd0` | SpEffect behavior offset FUN_1404fa350 | IDENTICAL-WHOLE (104) | unique, 40B signature, 31B fixed | yes |
| `0x1405ed760` | `0x1405ee5b0` | `+0xe50` | PlayAnimationOnChr | IDENTICAL-WHOLE (37) | nearest-anchor delta +0xe50, 2 shape candidates | yes |
| `0x1405e2060` | `0x1405e2eb0` | `+0xe50` | ForceAnimationPlayback | BYTE-IDENTICAL (134) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140c144d0` | `0x140c15ba0` | `+0x16d0` | fireHkbEvent_C | BYTE-IDENTICAL (134) | unique, 43B signature, 28B fixed | yes |
| `0x140406a60` | `0x140406f90` | `+0x530` | SetTurnSpeed | IDENTICAL-LEAF (13) | nearest-anchor delta +0x530, 3 shape candidates | CALL/READ only |
| `0x1403c3f30` | `0x1403c3f40` | `+0x10` | SetTurnVelocity | IDENTICAL-LEAF (2) | caller rel32, 1 agreeing caller(s) | CALL/READ only |
| `0x140303a70` | `0x140303a80` | `+0x10` | SetTurnReferenceDirection | IDENTICAL-WHOLE (53) | unique, 40B signature, 25B fixed | yes |
| `0x14041bce0` | `0x14041c220` | `+0x540` | CSChrBehaviorModule::Play_W_Init | IDENTICAL-WHOLE (15) | unique, 24B signature, 15B fixed | yes |
| `0x140c07a40` | `0x140c09110` | `+0x16d0` | hkbCharacter slot getter FUN_140c07a40 | IDENTICAL-LEAF (9) | unique, 16B signature, 14B fixed | CALL/READ only |
| `0x140c13f40` | `0x140c15610` | `+0x16d0` | GLOBAL_CSHkBehManager name lookup FUN_140c13f40 | IDENTICAL-WHOLE (129) | nearest-anchor delta +0x16d0, 9 shape candidates | yes |
| `0x1403c00f0` | `0x1403c0100` | `+0x10` | requestAnimationId reset FUN_1403c00f0 | IDENTICAL-WHOLE (38) | unique, 43B signature, 28B fixed | yes |
| `0x1403c7ed0` | `0x1403c7ee0` | `+0x10` | ChrCtrl::SetOrientation | IDENTICAL-LEAF (4) | nearest-anchor delta +0x10, 9 shape candidates | CALL/READ only |
| `0x1403b1e90` | `0x1403b1ea0` | `+0x10` | ChrCam::UpdateManipulatorLookDirection | IDENTICAL-WHOLE (45) | unique, 41B signature, 29B fixed | yes |
| `0x1403cda40` | `0x1403cda50` | `+0x10` | manipulator look-dir writer FUN_1403cda40 | IDENTICAL-LEAF (3) | nearest-anchor delta +0x10, 9 shape candidates | CALL/READ only |
| `0x1404e2140` | `0x1404e2f10` | `+0xdd0` | NetChrSync::ProcessOwnershipRequests22 | IDENTICAL-WHOLE (65) | nearest-anchor delta +0xdd0, 2 shape candidates | yes |
| `0x1404e0ab0` | `0x1404e1880` | `+0xdd0` | NetChrSync::ProcessOwnershipRequests23 | IDENTICAL-WHOLE (134) | nearest-anchor delta +0xdd0, 2 shape candidates | yes |
| `0x1404d4a00` | `0x1404d57d0` | `+0xdd0` | GetOwnershipDataByP2PEntityHandle | BYTE-IDENTICAL (144) | nearest-anchor delta +0xdd0, 2 shape candidates | yes |
| `0x1404d4be0` | `0x1404d59b0` | `+0xdd0` | NetChrSyncOwnershipHolder ctor | IDENTICAL-WHOLE (36) | nearest-anchor delta +0xdd0, 2 shape candidates | yes |
| `0x1404de7f0` | `0x1404df5c0` | `+0xdd0` | NetChrSync::SetChrSyncLocalControlFlagOn0x18 | BYTE-IDENTICAL (117) | unique, 43B signature, 31B fixed | yes |
| `0x140772a80` | `0x140773900` | `+0xe80` | CSFeManImp::UpdatePlayerComponents | IDENTICAL-WHOLE (1366) | caller rel32, 1 agreeing caller(s) | yes |
| `0x140771bd0` | `0x140772a50` | `+0xe80` | CSFeManImp::Update | IDENTICAL-WHOLE (98) | unique, 40B signature, 32B fixed | yes |
| `0x140437280` | `0x1404377e0` | `+0x560` | CSChrDataModule::GetMaxRecoverableHp | IDENTICAL-LEAF (3) | unique, 15B signature, 7B fixed | CALL/READ only |
| `0x1404372c0` | `0x140437820` | `+0x560` | CSChrDataModule::GethpMaxUncapped | IDENTICAL-LEAF (2) | caller rel32, 1 agreeing caller(s) | CALL/READ only |
| `0x140438a50` | `0x140438fb0` | `+0x560` | EnemyIns vitals seeder FUN_140438a50 | IDENTICAL-WHOLE (117) | unique, 40B signature, 32B fixed | yes |
| `0x140437140` | `0x1404376a0` | `+0x560` | CSChrDataModule vft slot3 fpBase getter | IDENTICAL-LEAF (2) | RTTI vtable slot CSChrDataModule+0x18 | CALL/READ only |
| `0x140437150` | `0x1404376b0` | `+0x560` | CSChrDataModule vft slot4 staminaBase getter | IDENTICAL-LEAF (2) | RTTI vtable slot CSChrDataModule+0x20 | CALL/READ only |
| `0x140437ac0` | `0x140438020` | `+0x560` | fpMax recompute FUN_140437ac0 | IDENTICAL-WHOLE (67) | nearest-anchor delta +0x560, 2 shape candidates | yes |
| `0x140437bc0` | `0x140438120` | `+0x560` | staminaMax recompute FUN_140437bc0 | IDENTICAL-WHOLE (68) | nearest-anchor delta +0x560, 2 shape candidates | yes |
| `0x1403f0b20` | `0x1403f0d50` | `+0x230` | EnemyIns vft+0x168 GetPlayerGameData | IDENTICAL-LEAF (1) | RTTI vtable slot ChrIns/EnemyIns+0x168 | CALL/READ only |
| `0x1403f4530` | `0x1403f4760` | `+0x230` | EnemyIns vft+0x118 IsPlayerIns (ret 0) | IDENTICAL-LEAF-NOPATCH (2) | RTTI vtable slot ChrIns/EnemyIns+0x118 | CALL/READ only |
| `0x140cba510` | `0x140cbbbe0` | `+0x16d0` | CSSessionManager voicechat FUN_140cba510 | IDENTICAL-WHOLE (93) | unique, 44B signature, 27B fixed | yes |
| `0x14073cf20` | `0x14073dd70` | `+0xe50` | QuickmatchManager FUN_14073cf20 | IDENTICAL-WHOLE (180) | unique, 42B signature, 33B fixed | yes |
