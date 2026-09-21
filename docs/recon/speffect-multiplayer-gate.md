# Does Elden Ring itself gate SpEffects by multiplayer context?

Yes. There is exactly one per-effect network rule in the engine, it is a two-clause predicate over
`SpEffectParam`, and it is enforced on both the send and the receive side of the same packet pair
that `er-net-effects` uses.

It is also not the rule we are looking for. It catches zero of the 41 hand-marked ids, and it
already runs inside the path our own `apply_speffect(id, dont_sync)` call takes, so it cannot be
the reason any of those 41 reached another player -- they pass it.

Both halves of that are evidenced below.

## The rule

```
network-syncable(row)  ==  row != null
                       &&  row->stateInfo != 297        // u16 at row offset 0x156
                       &&  row->isDisableNetSync == 0   // bit 4 of the u8 at row offset 0x259
```

`isDisableNetSync` is the only field in the whole `SpEffectParam` paramdef whose name refers to
network synchronisation. Enumerated exhaustively against
`../fromsoftware-rs/tools/param-generator/params/eldenring/SpEffect.xml` via
`scripts/paramdef-field-offset.py SpEffect`: the network/multiplayer vocabulary in that paramdef is
`effectTargetPlayer`, `effectTargetGhost`, `requestKickSession`, `requestLeaveSession`,
`requestLeaveColiseumSession`, `vowType0..15`, `isDisableNetSync`, and the
`atk/defPlayerDmgCorrectRate_*` scalars. Only `isDisableNetSync` is a sync gate; the rest are
targeting, session-control actions, covenant tags and damage scalars.

### Ghidra mislabels the flag; the machine code does not

The decompiler on the 1.16.2 dump prints the second clause as `disableFreeze`. That is off by one
bit. The instruction is a mask test, and the paramdef says which bit is which:

```
0x259  u8  isUseStatusAilmentAtkPowerCorrect bit=0
0x259  u8  isUseAtkParamAtkPowerCorrect      bit=1
0x259  u8  dontDeleteOnDead                  bit=2
0x259  u8  disableFreeze                     bit=3     <- mask 0x08
0x259  u8  isDisableNetSync                  bit=4     <- mask 0x10, this is the one tested
0x259  u8  shamanParamChange                 bit=5
0x259  u8  isStopSearchedNotify              bit=6
0x259  u8  isCheckAboveShadowTest            bit=7
```

`scripts/paramdef-field-offset.py SpEffect --offset 0x259`. Offset `0x156` resolves to `u16
stateInfo` the same way. Read the decompiled name as `isDisableNetSync` everywhere below.

## Where the decision is made

Every address is labelled with the image it came from. The 1.16.2 dump (MCP daemon on `:8765`,
program `ermaporch1162`) is the only one with symbols; `eldenring-deobf.bin` is the same build at
the same addresses (shift 0). The installed game is 1.17.1 (`eldenring-deobf-1.17.1.bin`); those
addresses are carried with `docs/recon/rva-map-1162-to-1170.functions.tsv` then
`scripts/map-rvas-1170-to-1171.py`, and the two that matter are byte-proven and `.pdata`-proven
rather than merely mapped.

| role | 1.16.2 | 1.17.1 |
|---|---|---|
| `ValidateNetworkedSpEffect` (receive gate, packet 38) | `0x140ca6470` | `0x140ca7bb0` |
| `FUN_140ca63a0` (receive gate, entity-targeted variant) | `0x140ca63a0` | `0x140ca7ae0` |
| `CS::PlayerIns::SendSpEffectIdSync` (send gate) | `0x1403f5dd0` | `0x1403f6000` |
| `GetSpEffectParam` | `0x140d505f0` | `0x140d523a0` |
| `TryDequeuePacket38` (receive pump) | `0x140c9b760` | `0x140c9cea0` |
| `FUN_140c9c7a0` (packet `0x78` pump) | `0x140c9c7a0` | `0x140c9dee0` |
| `CS::ChrIns::ApplySpEffect` | `0x1403e8be0` | `0x1403e8dc0` |
| `FUN_1403e8c90` (apply core, decides whether to broadcast) | `0x1403e8c90` | `0x1403e8e70` |
| `FUN_1403ee460` (receive-side applier) | `0x1403ee460` | `0x1403ee690` |

### Receive side

`ValidateNetworkedSpEffect`, 1.16.2 `0x140ca6470`:

```c
bool ValidateNetworkedSpEffect(NetworkedSpEffect *param_1)
{
  SpEffectParamLookupResult local_18;
  local_18.paramRow = (SP_EFFECT_PARAM_ST *)0x0;
  local_18.paramId = 0xffffffff;
  local_18._12_1_ = 0;
  GetSpEffectParam(&local_18,param_1->spEffectId);
  if (((local_18.paramRow != (SP_EFFECT_PARAM_ST *)0x0) && ((local_18.paramRow)->stateInfo != 0x129)
      ) && ((local_18.paramRow)->disableFreeze == '\0')) {
    return true;
  }
  return false;
}
```

Its machine code, which is what pins the field offsets and the bit:

```
140ca6492  CALL 0x140d505f0                      ; GetSpEffectParam
140ca649c  TEST RAX,RAX
140ca649f  JZ   0x140ca64bf                      ; no row -> reject
140ca64a1  MOV  ECX,0x129
140ca64a6  CMP  CX,word ptr [RAX + 0x156]        ; stateInfo
140ca64ad  JZ   0x140ca64bf                      ; stateInfo == 297 -> reject
140ca64af  TEST byte ptr [RAX + 0x259],0x10      ; isDisableNetSync
140ca64b6  JNZ  0x140ca64bf                      ; set -> reject
140ca64b8  MOV  AL,0x1                           ; accept
```

The caller is `TryDequeuePacket38` (1.16.2 `0x140c9b760`), pumped from
`WorldChrMan_PostPhysics` (`0x140510e90`). It dequeues packet type `0x26` (38), runs the gate, and
on success calls `FUN_1403ee460(chrIns, spEffectId, ...)`. That applier holds no further
multiplayer condition -- only a timestamp-ordering check against
`chrIns->lastReceivedPacket60`. So on the receive side the predicate above is the whole filter.

`FUN_140ca63a0` (1.16.2 `0x140ca63a0`, 1.17.1 `0x140ca7ae0`, called from `FUN_140c9a250`) repeats
the identical predicate behind a `P2PEntityHandle::HasChrSelector` / `GetChrSetIndex` check -- the
entity-targeted variant, matching `BroadCastPacket36`. Its bit test sits `0xae` bytes into the
function in both builds (1.16.2 `0x140ca644e`, 1.17.1 `0x140ca7b8e`).

The third receive pump, `FUN_140c9c7a0`, gates on the same `ValidateNetworkedSpEffect` and then
accepts exactly one id (`0x7ad00`) in one protocol state (`WaitReentryToMap`). Narrow special case,
not a general rule.

### Send side

`CS::PlayerIns::SendSpEffectIdSync`, 1.16.2 `0x1403f5dd0` -- the function that ends up broadcasting
whatever `er-net-effects` applies -- carries the same predicate inline before it sends anything:

```c
void CS::PlayerIns::SendSpEffectIdSync(PlayerIns *param_1,uint spEffectId)
{
  ...
  GetSpEffectParam(&local_18,spEffectId);
  if ((local_18.paramRow == (SP_EFFECT_PARAM_ST *)0x0) ||
     (((local_18.paramRow)->stateInfo != 0x129 && ((local_18.paramRow)->disableFreeze == 0)))) {
    if (GLOBAL_WorldChrMan != 0 && GLOBAL_WorldChrMan->mainPlayerIns == param_1) {
      BroadCastPacket38(local_res18,spEffectId, <packed timestamp>);
    } else if (!isChrEventIdlessThan9998(&param_1->chrIns)) {
      BroadCastPacket36(local_res18, ChrIns::GetP2PEntityHandle(...), spEffectId, MakePackedTimestamp());
    }
  }
  return;
}
```

Sender and receiver differ only in the null case: the sender broadcasts an id that has no param row,
and the receiver drops it.

The send side has no bypass. `SendSpEffectIdSync` is the only caller of either broadcast primitive
-- `getXrefsTo` on `BroadCastPacket38` (1.16.2 `0x140c9f970`) and `BroadCastPacket36`
(`0x140c9e620`) returns one call each, both from `0x1403f5ea5` and `0x1403f5ee9` inside
`SendSpEffectIdSync`, the rest being vtable/data references. So no SpEffect id reaches packet 36 or
38 without passing the predicate.

The send-side bit test is encoded differently from the receive side, which is why it is worth
recording separately -- it is the same bit:

```
1403f5e14  MOV   ECX,0x129
1403f5e19  CMP   CX,word ptr [RAX + 0x156]
1403f5e20  JZ    0x1403f5eee                     ; suppressed
1403f5e26  MOVZX EAX,byte ptr [RAX + 0x259]
1403f5e2d  SHR   EAX,0x4                         ; bit 4 = isDisableNetSync
1403f5e30  AND   EAX,0x1
1403f5e33  TEST  AL,AL
1403f5e35  JNZ   0x1403f5eee                     ; suppressed
```

### Three sites, and that is all of them

A byte scan of the whole 1.16.2 image for every addressing form that reads the byte at row offset
`0x259` finds seven sites; only three extract bit 4, and all three are the ones above.

```
$ python3 scripts/find-deobf-bytes.py 'F6 80 59 02 00 00 10'       # TEST [reg+0x259],0x10
  hits=2  0x140ca644e  0x140ca64af
$ python3 scripts/find-deobf-bytes.py '0F B6 80 59 02 00 00 C1 E8 04 83 E0 01 84 C0'
  hits=1  0x1403f5e26
```

The other four reads of that byte extract bits 0, 1, 2, 3 and 7 (checked with capstone at
`0x140448e12`, `0x140448d0c`, `0x1405008d8`, `0x1404f490b`, `0x140d5081d`, `0x1404fc5c0`,
`0x140448f16`) -- different flags packed in the same byte, nothing to do with sync.

The `stateInfo` clause closes the same way. Scanning for the comparison itself finds three sites per
image, and they are the same three functions:

```
$ python3 scripts/find-deobf-bytes.py 'B9 29 01 00 00 66 3B 88 56 01 00 00'
  1.16.2  hits=3  0x1403f5e14  0x140ca6440  0x140ca64a1
  1.17.1  hits=3  0x1403f6044  0x140ca7b80  0x140ca7be1
```

### It is unchanged in the installed 1.17.1 build

Byte-identical predicate, one hit per image, exactly where the map predicts:

```
$ python3 scripts/find-deobf-bytes.py 'B9 29 01 00 00 66 3B 88 56 01 00 00 74 10 F6 80 59 02 00 00 10'
  1.16.2 (eldenring-deobf.bin)         hits=1  0x140ca64a1
  1.17.1 (eldenring-deobf-1.17.1.bin)  hits=1  0x140ca7be1
```

`0x140ca7be1` minus the same `0x31` prologue offset gives `0x140ca7bb0`, and 1.17.1 `.pdata`
declares a function starting at exactly `0x140ca7bb0`. Same for the sender: the full send-side gate
byte pattern hits once at 1.17.1 `0x1403f6044`, and `.pdata` declares a function start at exactly
`0x1403f6000`, the mapped address. Field offsets `0x156` and `0x259` are unchanged between the
builds.

One further byte match in 1.17.1 at `0x141a15109` is a coincidence, not a fourth gate: 1.17.1
`.pdata` shows the preceding function ending at `0x141a15108` and no declared function covering
`0x141a15109`, so those bytes sit in inter-function padding and are never executed as that
instruction. 1.16.2 and 1.17.0 have no such match.

## Why this rule is not our rule

### It fires on none of the 41 marks

Measured against `data/effect-master-catalog.json` (11,325 rows) and the mark file
`<game>/er-net-effects-marked.jsonc` (41 ids):

| | count |
|---|---|
| rows with `isDisableNetSync != 0` | 106 of 11,325 |
| rows with `stateInfo == 297` | 0 of 11,325 |
| marked ids with `isDisableNetSync != 0` | **0 of 41** |
| marked ids with `stateInfo == 297` | **0 of 41** |

Within the 843-row `visuals-only` catalog the marking pass actually scrolled, the game's rule flags
5 rows (`11700`, `18521`, `20004220`, `20004221`, `20004222`) and the human flagged 41. The
intersection is empty.

The `stateInfo != 297` clause is dead code against the shipped regulation: 4,439 rows carry a
non-default `stateInfo` across 321 distinct values, and 297 is not one of them. The live rule is
`isDisableNetSync == 0` alone.

### What the flag actually means

Reading the 106 rows makes the intent obvious, and it is not fairness. They are overwhelmingly the
internal re-trigger rows of buffs -- the ones whose curated names end in `(Cycled)`:

```
310401  [Talisman] Erdtree's Favor (Cycled)        1605001  [Incantation] Flame Grant Me Strength
330801  [Talisman] Primal Glintstone Blade (Cycled) 1660001  [Incantation] Golden Vow (Cycled)
501692  [Item] Shield Grease (Cycled)              1674001  [Incantation] Immutable Shield (Cycled)
511061  [Tear] Cerulean Hidden Tear (Cycled)       1733005  [Incantation] Howl of Shabriri
1447001 [Sorcery] Scholar's Shield (Cycled)        6083201  [Armor] Lazuli Glintstone Crown
```

Every one of them carries the `lifetime` tag. These are the ticking second-stage rows a parent
buff re-applies to itself, and the remote peer derives them locally from the parent that was
synced. `isDisableNetSync` means "this row is an internal tick, keep it off the wire" -- a
bandwidth and derivation rule. It says nothing about whether an effect is acceptable to push at
another player, which is the judgement the 41 marks encode.

That also explains the asymmetry cleanly: the marked effects are ordinary top-level effects that
the game considers perfectly syncable, and objectionable only for gameplay reasons the paramdef has
no field for.

### A consequence worth knowing regardless

`er-net-effects` calls `player.apply_speffect(id, dont_sync)` (`crates/er-net-effects/src/effects.rs`),
which is `CS::ChrIns::ApplySpEffect` -> `FUN_1403e8c90` -> `SendSpEffectIdSync`. The gate lives in
that last frame, not in some outer layer we could sit above, so any id with `isDisableNetSync` set
is already silently dropped on the way out. Nothing needs to be added for those 106; they cannot be
broadcast. If a synced effect appears not to arrive at a peer and the id is one of the 106, this is
why, and it is not a bug in the mod.

## The other candidates, and why each is out

### `vowType0..15` (line of attack 1)

Not a separator, measured, before this investigation started: all 16 `vowType` flags are non-zero
on all 41 marked rows and on all 802 unmarked rows of the visuals-only catalog, and no row in that
population clears any of them.

The mechanism, for the record, since it was the most promising-sounding lead: `vowType` is a
covenant tag, not a sync gate. The consumer side is `RoleParamLookupResult::GetSpEffectByVowRank`
(1.16.2 `0x140d48a60`), which reads `RoleParam.spEffectID_vowRank0..3` -- the game grants an
SpEffect for your rank in a vow. `SpEffectParam.vowType*` is the matching "which vows does this
effect belong to" bitmask. It never decides whether an effect crosses the network.

### The 13 `effectTarget*` booleans

Already ruled out before this work: `effectTargetPlayer != 0` holds for 38 of 41 marks and for 559
of 802 unmarked rows.

### `NetworkParam` / `MultiPlayCorrectionParam` (line of attack 3)

Neither carries per-SpEffect suppression.

`NetworkParam` (156 fields) has no SpEffect reference at all. Its only name matching
`effect|disable|sync` is `f32 multiplayDisableLifeTime`, a timer.

`MultiPlayCorrectionParam` (8 fields) does name SpEffects -- `client1SpEffectId`,
`client2SpEffectId`, `client3SpEffectId`, `bOverrideSpEffect` -- but in the opposite direction, and
not keyed by effect. It is looked up per character from `NpcParam`:

```c
// Get_MultiPlayerCorrectionParam_For_Chr, 1.16.2 0x1403f0460
pNVar1 = (*param_1->_vfptr->GetNpcParam)(param_1);
if (pNVar1 != 0 && pNVar1->paramRow != 0) {
  multiPlayerCorrectionParamId = pNVar1->paramRow->multiPlayCorrectionParamId;
  Get_MultiPlayerCorrectionParam_From_ParamId(param_2, multiPlayerCorrectionParamId);
```

So a row is selected by which NPC this is, and it grants that NPC an SpEffect according to how many
clients are in the session -- enemy scaling. It cannot express "suppress effect X between players".
`NetworkAreaParam` and `NetworkMsgParam` name no SpEffects either.

### The apply path (line of attack 2)

`CS::ChrIns::ApplySpEffect` (1.16.2 `0x1403e8be0`) is a thin wrapper over `FUN_1403e8c90`, whose
signature is `(ChrIns *target, uint spEffectId, ChrIns *source, char shouldNotSync, ...)` -- the
`shouldNotSync` flag `er-net-effects` already passes inverted. That function does contain a stack
of multiplayer conditions, and they were read; none of them is per-effect:

```c
bVar9 = shouldNotSync == '\0';
if (GLOBAL_CSSessionManager->protocolState == InGame) { ... }
  // branches on: chrFlags1c8 & 1, IsMainPlayerIns(source), IsMainPlayerIns(target),
  //              isChrEventIdlessThan9998, IsChrInDebugChrSet,
  //              FUN_140508900 / FUN_140508cc0 / FUN_140508c00 (WorldChrMan, by P2PEntityHandle)
...
bVar3 = FUN_1403fade0(param_1,spEffectId,...);          // apply
if ((bVar3) && (bVar9)) {
  CS::PlayerIns::SendSpEffectIdSync(param_1,spEffectId); // broadcast
}
```

Every one of those branches asks who the character is -- is this my main player, is it a networked
ghost, is the session in-game, does the P2P handle resolve. The effect id enters only as
`spEffectId < 0` and in the param lookup inside `SendSpEffectIdSync`. There is no branch here on
player-vs-enemy or host-vs-guest that varies by which effect is being applied.

### Seamless Co-op (line of attack 4)

`ersc.dll` adds no filter of its own, and it is not in the path anyway.

Searched across `vendor-archive/seamless/ersc-1.9.9.dll`, `ersc-2.0.0.dll`, `ersc-2.0.1.dll` and
`ersc-2.0.1.runtime.bin` (a live-process dump, base `0x180000000`). ersc addresses below are
`ersc-2.0.0.dll` at base `0x180000000`.

- **The code searched is the real code.** Both builds carry a Themida section (`.themida` in 1.9.9,
  renamed `ERSC` in 2.0.x, rva `0x240000`, RWX). But ersc's `.text` on disk is byte-identical to
  `.text` in the runtime dump -- 1,643,670 bytes, zero differing -- so the string pool and code are
  not a stub.
- **No SpEffect vocabulary at all.** `effect`, `Effect`, `EFFECT`, `SpEffect`, `sp_effect`: zero
  occurrences, ASCII or UTF-16LE, in all three images, by raw byte search rather than a
  length-filtered `strings` pass. ersc's 33 assert-path module names (`.rdata`
  `0x1df189`-`0x1dfb59`) include `networking`, `param`, `signatures`, `hooks`,
  `seamless_session_manager` -- and no effect module. The only param names anywhere in the binary
  are `SoloParamRepository` (`0x1801e01bf`) and `WorldMapLegacyConvParam` (`0x1801e0e21`), both on
  abort paths.
- **No id table.** Every 4-byte-aligned dword of each whole file, packed section included, scanned
  for runs of 8 or more consecutive values in the plausible SpEffect ranges. Eleven runs per image,
  all identified: ersc's own module rvas (jump/CFG tables) and two Unicode codepoint tables at
  `0x1801db7d4` and `0x1801dc788` -- the latter confirmed as Unicode by the plane boundaries
  (`0x1FAE0`, `0x20000`, `0x2FFFE`, `0x3FFFE`) immediately preceding it, and both confirmed dead by
  a brute-force displacement xref scan over the full 1.6 MB `.text` that found zero references to
  either while correctly finding four to `SoloParamRepository`. Nothing at all in 13000-14000,
  290000-300000, 500000-520000, 1440000-1680000 or 20500000-20520000.
- **No byte signature for any function on this path.** The first 192 bytes of all nine functions in
  the table above, in every 10-, 12-, 14- and 16-byte window, searched across all three images:
  zero hits outside ersc's own `.text`, and inside it only generic MSVC prologue boilerplate. The
  gate's own literals -- `F6 80 59 02 00 00 10`, the full receive gate, the send gate, and
  `66 3B 88 56 01 00 00` -- are absent from all three. De-interleaved value/mask storage was tested
  too, zero hits.
- **We do not call ersc.** `effects.rs` calls `player.apply_speffect(id, dont_sync)`, which is
  upstream's `chr_ins_apply_speffect` rva `0x3e8dc0` resolved against the **game** module base --
  `CS::ChrIns::ApplySpEffect`. No ersc import, no `GetProcAddress`, no ersc export in the chain.
  ersc's export table is a single symbol, `modengine_ext_init` at `0x3d00`.

Two limits on that negative, both honest and neither closable offline. First, ersc's settings-key
strings (`ersc_settings`, `[GAMEPLAY]`, `allow_*`, `death_debuff`) are also absent from all three
images including the runtime dump, and ersc demonstrably reads settings -- so some of its strings
are not resident as plaintext, which bounds how much the missing `effect` string can carry. Second,
the hook search has no positive control: cross-matching ersc's `.rdata`/`.data` against the game's
`.text` turns up no non-trivial shared runs except the AES S-boxes, meaning ersc stores no game
signature as contiguous plaintext bytes at all. So the search proves ersc holds no plaintext pattern
for these functions, not that it never detours them. Closing it takes one `/proc/<pid>/mem` read of
the first bytes of 1.17.1 `0x1403f6000` and `0x140ca7bb0` with the game running, looking for a
detour -- `scripts/er-live-fields.py`, no Frida server needed.

## Verdict

A game-side rule exists, it is `SpEffectParam.isDisableNetSync == 0 && stateInfo != 297`, and it is
enforced at 1.16.2 `0x1403f5dd0` (send), `0x140ca6470` and `0x140ca63a0` (receive) -- 1.17.1
`0x1403f6000`, `0x140ca7bb0` and `0x140ca7ae0`.

We cannot adopt it as the rule for the 41 marks. It selects 106 rows, none of them marked, on a
criterion (this row is an internal buff tick, derive it locally instead of sending it) unrelated to
the criterion the marks encode (this effect is unfair or destructive to put on another player).
Adopting it would change nothing about what `er-net-effects` broadcasts, because the engine already
applies it two frames below our `apply_speffect` call.

What was checked to reach that conclusion, so the negative can be trusted: the complete
network/multiplayer field vocabulary of the `SpEffectParam` paramdef; every code site in the image
that reads the flag byte at row offset `0x259`; every caller of both broadcast primitives; all
three receive pumps and the send path for packet types 36, 38 and `0x78`; the receive-side applier;
the apply core's full multiplayer branch set; all four `Network*` / `MultiPlayCorrection*`
paramdefs; and three `ersc.dll` builds plus a live dump of one, for strings, id tables and hook
signatures. The search for a game-side fairness rule over SpEffects is exhausted in the engine, in
the params, and in Seamless.

There is no game-side notion of "this SpEffect is unfair against another player". The engine's only
per-effect network concept is transport-level. A rule over the 41 marks has to be derived from what
the marked rows do -- the `duration_filter` / `effectEndurance == -1` shape -- not adopted from
FromSoftware.

## Method notes

- Ghidra named dump 1.16.2, MCP daemon `:8765`, project `ermaporch1162`, brought up with
  `bash scripts/ghidra/mcp-up-1162.sh`, queried with `python3 scripts/ghidra/mcp_query.py`.
  The 1.17 dump on `:8767` was not needed: everything here is named 1.16.2 code that survives to
  1.17.1 byte-identically, which the byte scans prove more cheaply than a second dump would.
- Address carrying: `docs/recon/rva-map-1162-to-1170.functions.tsv` for 1.16.2 to 1.17.0, then
  `scripts/map-rvas-1170-to-1171.py`. Both key results were then independently confirmed by byte
  pattern and by 1.17.1 `.pdata` function starts, so they do not rest on the map.
- No game launch. Nothing here needed one.
