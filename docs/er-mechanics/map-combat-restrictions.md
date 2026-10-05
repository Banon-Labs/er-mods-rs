# Map combat restrictions: why Roundtable Hold forbids attacks and lock-on

Static research, 2026-10-04. Installed game 1.17.1. Labels:

- **VERIFIED**: read from the 1.17.1 de-Arxan'd image (`eldenring-deobf-1.17.1.bin`) or from
  the named 1.16.2 Ghidra dump and carried to 1.17.1 by byte match or by the function map.
- **DATA**: read from game data (installed `regulation.bin`, EMEVD, MSB).
- **INFERRED**: follows from the above but was not read directly.

## Short answer

There is no map flag or map param that says "no attacks here". Each map's event script
(EMEVD) watches where the player is and applies one SpEffect, **9621 "Disallow Hostile
Actions"**, while the player is in the protected space, and clears it when they leave. That
SpEffect carries nothing except `stateInfo = 457` (0x1c9). Native code looks for an active
SpEffect with state 457 and, when it finds one:

1. disables the R1, R2, L1 and L2 action inputs in the player's pad manipulator, so attacks,
   weapon skills, spell casts and guards never become action requests;
2. refuses spells whose `MagicParam.isUseNoAttackRegion` is 0, and goods whose
   `EquipParamGoods.isUseNoAttackRegion` is 0;
3. greys the weapon and spell slots on the HUD and drops the spirit-ash summon prompt.

**Lock-on is not part of this mechanism.** No code reads state 457 on the lock-on path. In
Roundtable Hold you cannot lock on because none of the characters there is a valid target:
their team type is `None` (0), and the lock-on candidate walk only admits characters the
player's team relation says it can attack (INFERRED for the relation matrix; see below).

## 1. The SpEffect (DATA)

`SpEffectParam` row **9621**, name "Disallow Hostile Actions":
`stateInfo = 457`, `effectEndurance = -1` (lasts until cleared), `spCategory = 20`,
`effectTargetSelf = 0`, `effectTargetSelfTarget = 1`, `vfxId = -1`. Every other field is at its
default. **It is the only row in the regulation with `stateInfo = 457`.** No code references
row 9621 by number: none of seven immediate encodings of 0x2595 occurs in the 1.17.1 image.

```
python3 /home/banon/projects/er-mods-rs/scripts/er-param-read.py SpEffectParam --where stateInfo=457 --fields stateInfo,effectEndurance --names
python3 /home/banon/projects/er-mods-rs/scripts/er-param-read.py SpEffectParam --row 9621 --names
```

## 2. The native checks for state 457

Every site that loads `0x1c9` as an argument to `HasSpecialEffectWithStateInfo`, or compares
`SP_EFFECT_PARAM_ST.stateInfo` (+0x156) with it. Found by scanning the 1.17.1 image for
`mov r32, 0x1c9` in every register encoding; hits outside these functions are Havok line
numbers or mid-instruction noise.

| 1.17.1 address | 1.16.2 name / address | effect when state 457 is active | label |
|---|---|---|---|
| `0x1403daa90` (gate at `0x1403dac29`) | `FUN_1403daa80(PadManipulator*, FD4Time*)`, the player pad manipulator update | `CSChrActionRequestModule::SetDisabledInputState` (`0x1404080e0`) is called with action types 0, 1, 2, 3 (R1, R2, L1, L2) and `true` | VERIFIED |
| `0x1404fac00` | `CS::SpecialEffect::CanUseMagic` `0x1404f9e30` | returns false for a spell with `MagicParam` byte +0x32 bit 6 (`isUseNoAttackRegion`) clear | VERIFIED (1.16.2 decompile, 1.17 paired by function map) |
| `0x1404faae0` | `CanUseGoods(SpecialEffect*, int)` `0x1404f9d10` | returns false for goods with `EquipParamGoods` byte +0x74 bit 0 (`isUseNoAttackRegion`, default 1) clear | VERIFIED (same) |
| `0x140773900` | `CSFeManImp::UpdatePlayerComponents` `0x140772a80` | left/right weapon slots and the spell slot get their disabled HUD state | VERIFIED call, INFERRED meaning of the HUD flags |
| `0x140771a20` | `FUN_140770ba0(CSFeManImp*)` | the spirit-ash summon prompt is not raised | VERIFIED call, INFERRED meaning |

The pad manipulator gate in 1.17.1 (`HasSpecialEffectWithStateInfo` is `0x1404fa370`, the
character's `SpecialEffect` is `ChrIns+0x178`):

```
1403dac29  ba c9 01 00 00     mov  edx, 0x1c9
1403dac2e  48 8b 88 78 01 00  mov  rcx, [rax+0x178]
1403dac35  e8 36 f7 11 00     call 0x1404fa370          ; HasSpecialEffectWithStateInfo
1403dac3c  74 3e              je   0x1403dac7c
1403dac42  33 d2              xor  edx, edx             ; R1
1403dac47  e8 94 d4 02 00     call 0x1404080e0          ; SetDisabledInputState(req, 0, 1)
1403dac50  8b d3              mov  edx, ebx             ; R2
1403dac5e  ba 02 00 00 00     mov  edx, 2               ; L1
1403dac6f  ba 03 00 00 00     mov  edx, 3               ; L2
```

Because the gate sits on the input-to-action-request step, it is not an HKS rule:
`c0000.hks` (1.17.1 extract) contains neither 457 nor 9621 as a constant. HKS simply never
sees the R1/R2/L1/L2 request (VERIFIED absence of the constants; INFERRED that HKS reads the
filtered request through `env(1106, ACTION_ARM_*)` in `GetAttackRequest`).

Movement, rolling, jumping, item use and gestures are not touched by this gate.

## 3. How a map turns it on (DATA)

### Roundtable Hold, `m11_10_00_00`

`m11_10_00_00.emevd` event 0 starts event **11102600** unconditionally. Decoded with the
DarkScript3 ER EMEDF:

```
11102600 (restart on end):
  Set Network Sync State(Disabled)
  Clear SpEffect(10000, 9621)
  AND_01: Player In/Out Map(inside, m11_10_00_00)
  AND_01: In/Outside Area(OUTSIDE, entity 10000, area 11102600, 1)
  AND_01: Ceremony Active(false, ceremony 20)
  IF MAIN pass AND_01
  Set SpEffect(10000, 9621)
  Wait 1.0 s
  AND_02: same three conditions
  IF MAIN fail AND_02            ; any of them stops holding
  End(restart)                   ; restart clears 9621 again
```

So the player gets 9621 whenever they are anywhere in Roundtable Hold **except**:

- **inside MSB region 11102600**, `Other` region `Ling Yu  Fei Zhan Dou eria(Pai Ta )` ("non-combat
  area, exclusion"). It is a composite of four boxes centred near
  (-286, -34.6, -286), (-306, -33.6, -269), (-258, -33.2, -285), (-279, -33.6, -297). They sit
  about 12 m below the main floor (y about -22) and overlap the region
  `An Ling Qin Ru  Ju Dian 1FnoDi ` ("dark-spirit invasion, enemy on base 1F", trigger at
  (-285.6, -33.6, -286.2)). This is the lower level where the hold's hostile encounter is staged.
- **while ceremony 20 is active**. `Ceremony` param row **11100020** (map 11_10, ceremony 20)
  selects `eventLayerId = 2`, `mapStudioLayerId = 2`. In the MSB, `c0000_9025` (NpcParam
  **543390079**, Ensha of the Royal Remains, `teamType = 27` Hostile NPC) exists only on layer
  2 (`MapStudioLayer = 4`), while the ordinary Ensha `c0000_9024` (523390079) has
  `teamType = 0`. The MSB also carries `Ling Yu  NPCYi Si maruchi An Hei Yuan Zhuo Kong Jian ` ("NPC pseudo-multiplayer,
  dark Roundtable space") trigger/invade/activate regions. Event 11103700 runs the fight under
  `Ceremony Active(true, 20)`.

These are the two "variations" of Roundtable Hold where attacks work.

How ceremony 20 is entered is INFERRED: in 1.16.2 the ceremony id is set by
`SetNPCInvadeTargetCeremony` (called from `ReqInvadeNPCWorld` and `SetMultiplayJoinData`) and
read back by `GetNPCInvadeCeremonyId` in `STEP_MoveMap_Update`, which points at an NPC-invasion
style entry into the hold rather than an ordinary load.

### Other maps using the same SpEffect

Same shape, but the condition is **inside** a named area **and** multiplayer state
`Invasion`, and the target is entity 20000. All DATA from the EMEVD and MSB; the meaning of
entity 20000 (which players it covers) is INFERRED.

| map | event (area) | area name in MSB |
|---|---|---|
| m10_00 Stormveil | 10003500 (10002740), (10002741) | `Gong Ji Jin Zhi  Jiao Hui ` "no attack: church", `Gong Ji Jin Zhi  Yu Zuo noJian ` "no attack: throne room" |
| m14_00 Raya Lucaria | 14003500 (14002700) | `Gong Ji Jin Zhi  bosuBu Wu ` "no attack: boss room" |
| m16_00 Volcano Manor | 16002670 (16002670) | `Ling Yu Pan Ding  He Cheng Ling Yu  Gong Ji Jin Zhi Qu Yu ` "composite no-attack zone" |
| m12_05 Mohgwyn Palace | `common_func` 90005615 (12052699) | `Zhan Dou Jin Zhi Ling Yu  mo-gunobosuBu Wu ` "no-combat region: Mohg's boss room" |
| m21_01 (DLC) | 21012848 | not resolved |

## 4. Lock-on

The lock-on toggle is 1.16.2 `FUN_140affe40(MoveMapStep*, ...)`, 1.17.1 `0x140b01340`. On the
lock-on button it flips `LockTgtMan+0x2831` (`lockOnEnabled`; `GLOBAL_LockTgtMan` at
`0x143d6e278`), then forces it off when `CS::ChrIns::IsLockOnDisabled(mainPlayer)` is true
(VERIFIED in 1.17.1: `call 0x1403f2ea0` at `0x140b01602`, `mov byte [rax+0x2831], 0` at
`0x140b01645`).

`IsLockOnDisabled` (1.17.1 `0x1403f2ea0`, unique byte match) is
`(ChrIns+0x58 -> +0xc8 -> +0x18) >> 3 & 1`: bit 3 of `ChrCtrlModifierData.actionFlags`. The
only writer found sets it from **TAE event 0 (ChrActionFlag) type 0x37** (1.16.2
`0ChrActionFlag` `0x1404275e0` case 0x37; 1.17.1 `or dword [rsi+0x18], 8` at `0x1404286fd`). That
is a per-animation flag, not a map flag.

The candidate walk in `LockTgtMan` update (1.17.1 `0x1407170b0`) admits a lock point only if
it is enabled, has a character owner, `CanTargetTeamType(player, owner)` is true (1.17.1
`0x14051b610`, a lookup in the `CSTeamTypeRelation` matrix with `opposeTarget = true`), and
the owner is not the player (see `docs/recon/lockon-filter-findings.md`). Nothing on that path
reads state 457 (VERIFIED: no 0x1c9 immediate in either function).

Roundtable NPC team types (DATA, NpcParam): Roderika 523200079, Diallos 523140079, Corhyn
523510079, Ensha 523390079, the Two Fingers 20600079, Enia 21700079, Hewg 34510179 all have
`teamType = 0` (None). Ensha's ceremony-20 copy 543390079 and Alberich 543850079 have
`teamType = 27` (Hostile NPC).

INFERRED: the relation cell for the player's team against `None` does not grant
`opposeTarget`, so there is simply nothing to lock on to in the normal hold, and in ceremony 20
the Hostile NPC is lockable. Not read: the `CSTeamTypeRelation` matrix contents, and whether
an EMEVD `Set Character Team Type` changes any of these NPCs at runtime.

## Still inferred

- The `CSTeamTypeRelation` cell (player team vs `None`) that makes Roundtable NPCs untargetable.
- How ceremony 20 is entered (NPC-invade ceremony path, from function names only).
- The HUD meaning of the 457-gated flags in `UpdatePlayerComponents`.
- Which players entity 20000 covers in the invasion-only maps.
- EMEVD and MSB were read from the 2026-07-13 extraction
  (`~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/`), which predates 1.17.1; the regulation
  rows come from the installed 1.17.1 `regulation.bin`. The 1.17.1 extract
  (`~/er-extract/1171-20261004`) holds only `action/` and `chr/`.

## Reproduce

```
# SpEffect row and the only stateInfo 457 row
python3 /home/banon/projects/er-mods-rs/scripts/er-param-read.py SpEffectParam --where stateInfo=457 --names
# field offsets of the two isUseNoAttackRegion bits
python3 /home/banon/projects/er-mods-rs/scripts/paramdef-field-offset.py MagicParam isUseNoAttackRegion
python3 /home/banon/projects/er-mods-rs/scripts/paramdef-field-offset.py EquipParamGoods isUseNoAttackRegion
# every 0x1c9 immediate in 1.17.1
ER_DEOBF_BIN=eldenring-deobf-1.17.1.bin python3 /home/banon/projects/er-mods-rs/scripts/find-deobf-bytes.py 'ba c9 01 00 00'
# the pad manipulator gate
ER_DEOBF_IMAGE=eldenring-deobf-1.17.1.bin bash /home/banon/projects/er-mods-rs/scripts/disas-deobf.sh --color=never 0x1403dac20 0x60
# lock-on toggle and IsLockOnDisabled
ER_DEOBF_IMAGE=eldenring-deobf-1.17.1.bin bash /home/banon/projects/er-mods-rs/scripts/disas-deobf.sh --color=never 0x140b01340 0x380
ER_DEOBF_BIN=eldenring-deobf-1.17.1.bin python3 /home/banon/projects/er-mods-rs/scripts/find-deobf-bytes.py '48 8b 41 58 48 8b 88 c8 00 00 00 8b 41 18 c1 e8 03 83 e0 01 c3'
# 1.16.2 named decompiles (Ghidra :8765)
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"1403daa80"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"1404f9e30"}'
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140affe40"}'
# EMEVD events that apply 9621, and the Roundtable event
cd ~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/event
python3 /home/banon/projects/er-mods-rs/scripts/er-emevd-find.py 9621 *.emevd
python3 /home/banon/projects/er-mods-rs/scripts/er-emevd-find.py 11102600 m11_10_00_00.emevd
# ceremony row and NPC team types
python3 /home/banon/projects/er-mods-rs/scripts/er-param-read.py Ceremony --names
python3 /home/banon/projects/er-mods-rs/scripts/er-param-read.py NpcParam --row 543390079 --fields teamType --names
```

EMEDF instruction names came from DarkScript3's `er-common.emedf.json`
(github.com/AinTunez/DarkScript3, `DarkScript3/Resources/`); a scratch copy is not kept in the
repo.
