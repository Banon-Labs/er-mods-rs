# Chainsaw glitch: the equipment-change gate

Static RE only (no game launch, no Frida). Every address is ELDEN RING 1.17.1 (PE 2.7.1.0) with
the 1.16.2 address in brackets. 1.17.1 bytes come from `eldenring-deobf-1.17.1.bin`. Names and
decompiles come from the 1.16.2 Ghidra dump on :8765. Each 1.17.1 address below was either read
out of a 1.17.1 vtable or found by a byte signature and then disassembled in the 1.17.1 image.
Names in `CamelCase` that Ghidra does not carry are mine and are marked "(name ours)".

## Summary

- The gate is `CSChrMenuFlags` at `CSPlayerMenuCtrl+0x18`. Its flag word is at
  `CSPlayerMenuCtrl+0x20`, and `PlayerIns+0x6a0` points at the `CSPlayerMenuCtrl`. The predicate
  is `0x1407c1fb0` [`0x1407c1130`]:

  ```
  allowed = (f & 0x1) && !(f & 0x10) &&
            ( (ChrActionFlagModule.actionAnimationFlags & 1) || (f & 0x6) )
  ```

- The flag word is rebuilt every frame. A per-frame chr update stores `f = 9`. After that, HKS
  `act(147)` ORs in `0x4`, HKS `act(163)` ORs in `0x10`, and TAE `ChrActionFlag` type 11 ORs in
  `0x2`. `actionAnimationFlags` is zeroed every frame in `PreBehaviorSafe`, and HKS `act(9100)`
  (`Wait`) sets bit 0 again.
- Nothing sets a persistent "skill busy" lock. The skill blocks equipment changes only because
  `ArtsCommonFunction`, run by every SwordArts state's `onUpdate`, calls `act(163)` every frame.
- The weapon switch `0x14042d4d0` [`0x14042cf80`] writes nothing the gate reads. The window
  comes from the behaviour graph. `ArtsCommonFunction` calls `ExecWeaponChange`, which takes the
  SwordArts state off the active layer, so the next frame has no `act(163)`. At the same time the
  half-blend lower body runs a Move or Stop state that calls `act(9100)`, which satisfies the
  positive term. Nothing has to race anything: once the frame stops containing an Arts state,
  the gate is open.

## 1. Where the message comes from

`msg/engus/menu.msgbnd.dcx`, `GR_Dialogues.fmg`, id **103130 (`0x192da`)** = "Cannot change
equipment at this time". This was read from the existing extraction
`/home/banon/projects/er-msg/engus/menu-msgbnd-dcx/GR_Dialogues.fmg.xml` with
`scripts/fmg-id-lookup.py --near 103130`. Ids 103131 to 103139 are `%null%`, so no other
equip-refusal text sits next to it.

The immediate `da 92 01 00` occurs exactly once in each image
(`scripts/find-deobf-bytes.py 'da 92 01 00'`): 1.17.1 `0x1408de3bd`, 1.16.2 `0x1408dd21d`.

### The refusing function: `EquipDialog` vtable slot `+0x90`

- `EquipDialog` vtable `0x142af0a28` [`0x142aed9a8`] (RTTI `.?AVEquipDialog@CS@@`, found with
  `scripts/find-vtable-rva.py EquipDialog`). Slot `+0x90` holds `0x1408de350` [`0x1408dd1b0`],
  read straight out of both images.
- 1.17.1 disassembly:

  ```
  1408de37c  add rcx,0xa38
  1408de383  call 0x14073ac70          ; returns *(rcx+0xd4): the highlighted slot row
  1408de388  lea r8,[rbx+0x2550]       ; slot table, 0x58-byte rows
  1408de3a1  mov ecx,[rdx+rax+4]       ; row.chrAsmSlot
  1408de3a5  call 0x140789910          ; gate wrapper
  1408de3aa  test al,al
  1408de3ac  jne 0x1408de477           ; allowed -> open the item list
  1408de3bc  mov edx,0x192da           ; refused -> GR_Dialogues 103130
  1408de3c6  call 0x140760a20          ; GetGR_Dialogues
  ```

- 1.16.2 decompile of `FUN_1408dd1b0`:

  ```c
  iVar2 = FUN_140739e20(param_1 + 0xa38);
  cVar1 = FUN_140788a90(*(undefined4 *)(... + (longlong)iVar2 * 0x58 + param_1 + 0x2550));
  if (cVar1 == '\0') { pMVar3 = GetGR_Dialogues(&local_48,0x192da); ... }
  else { FUN_14078e030(param_1); }
  ```

The same gate runs again at commit time, so the check is not only made when the menu opens.
`EquipItemToChrAsmSlot` `0x140788ab0` [`0x140787c30`] calls it before writing the slot, at
1.17.1 `0x140788afb call 0x140789910` [`0x140787c7b`], and returns without a message when it
fails (1.16.2: `cVar1 = FUN_140788a90(); if (cVar1 == '\0') return;`).
`getXrefsTo 140788a90` lists four other callers: `FUN_140801490`, `FUN_1408e0360` (twice) and
`FUN_1408dfe00`.

## 2. The gate

### Wrapper `CanChangeEquipmentInSlot` (name ours) `0x140789910` [`0x140788a90`]

1.17.1:

```
140789924  mov rax,[0x143d69ff8]       ; WorldChrMan
140789962  mov rdi,[rax+0x1e508]       ; main PlayerIns
140789970  mov rcx,rdi
140789973  call 0x140657c70
14078997a  movzx ebx,bl
14078997d  cmove ebx,ebp               ; result = thunk()
```

1.16.2 decompile:

```c
cVar3 = FUN_140656e20(pPVar1); bVar9 = cVar3 != '\0';
if (param_1 == 0x15) { ... GetDefaultPlayerMenuCtrl ... vfunc+0x68 ... }
if ((param_1 - 0xcU < 5) && ... menuRefSpecialEffect1 ...) { ... }
return bVar9;
```

The extra terms apply only to `ChrAsmSlot` 0x15 (`AccessoryCovenant`) and 0xc to 0x10 (the
protector slots). The weapon slots (0 to 5, `WeaponLeft1` to `WeaponRight3`) see only the
`CSPlayerMenuCtrl` term. The enum values come from
`fromsoftware-rs/crates/eldenring/src/cs/player_game_data.rs`.

### Thunk `0x140657c70` [`0x140656e20`]

```
140657c70  mov rcx,[rcx+0x6a0]         ; PlayerIns.player_menu_ctrl (CSPlayerMenuCtrl*)
140657c77  mov rax,[rcx]
140657c7a  jmp qword ptr [rax+0x108]
```

`PlayerIns+0x6a0` is `player_menu_ctrl: NonNull<CSPlayerMenuCtrl>` in
`fromsoftware-rs/crates/eldenring/src/cs/chr_ins.rs`.

### `CSPlayerMenuCtrl` vtable slot `+0x108` and the predicate

`CSPlayerMenuCtrl` vtable `0x142ab0708` [`0x142aad688`]. Slot `+0x108` = `0x1407c2610`
[`0x1407c1790`]:

```
1407c2610  add rcx,0x18                ; &this->menuFlags (CSChrMenuFlags)
1407c2614  jmp 0x1407c1fb0
```

The predicate is `0x1407c1fb0` [`0x1407c1130`]. 1.17.1 disassembly, with `rcx` = `CSChrMenuFlags*`
and the flag word at `+0x8` (that is, `CSPlayerMenuCtrl+0x20`):

```
1407c1fb6  mov eax,[rcx+0x8]
1407c1fbc  test al,0x1
1407c1fbe  je  0x1407c2030             ; bit0 clear  -> false
1407c1fc0  test al,0x10
1407c1fc2  jne 0x1407c2030             ; bit 0x10 set -> false
1407c1fc4  mov rax,[0x143d69ff8]       ; WorldChrMan
1407c1ffe  mov rax,[rax+0x1e508]       ; main PlayerIns
1407c2005  test rax,rax
1407c2008  je  0x1407c2030             ; no player   -> false
1407c200a  mov rax,[rax+0x190]         ; ChrIns.modules
1407c2011  mov rcx,[rax+0x8]           ; CSChrActionFlagModule
1407c2015  test byte [rcx+0x10],0x1    ; actionAnimationFlags bit0
1407c2019  je  0x1407c2023
1407c201b  mov al,0x1                  ; -> true
1407c2023  test byte [rbx+0x8],0x6
1407c2027  setne al                    ; -> (f & 6) != 0
1407c2030  xor al,al                   ; -> false
```

1.16.2 decompile, identical in logic:

```c
if (((param_1->flags & 1) != 0) && ((param_1->flags & 0x10) == 0)) {
  ...
  if (... mainPlayerIns->chrIns.componentContainer->actionFlag->actionAnimationFlags & 1) != 0)
    return true;
  return (param_1->flags & 6) != 0;
}
return false;
```

## 3. Who writes the gate's inputs

### `CSChrMenuFlags.flags` (`CSPlayerMenuCtrl+0x20`)

| bit | meaning (from the writers) | vtable slot | 1.17.1 body [1.16.2] |
|---|---|---|---|
| `=9` | per-frame reset: bit0 and bit3 set, every other bit cleared | `+0xe0` | `0x1407c2390` -> `0x1407c1fa0 mov dword [rcx+8],9` [`0x1407c1510 mov dword [rcx+0x20],9`] |
| `0x1` | master enable; the reset sets it, slot `+0xe8` can clear it | `+0xe8` | `0x1407c2ad0 and [rcx+0x20],~1; or [rcx+0x20],dl&1` [`0x1407c1c50`] |
| `0x2` | allowed, from TAE | `+0xf0` | `0x1407c2aa0 or dword [rcx+0x20],2` [`0x1407c1c20`] |
| `0x4` | allowed, from HKS | `+0xf8` | `0x1407c2a90 or dword [rcx+0x20],4` [`0x1407c1c10`] |
| `0x10` | forbidden, from HKS; overrides everything | `+0x100` | `0x1407c2a80 or dword [rcx+0x20],0x10` [`0x1407c1c00`] |

The slot bodies were read from the 1.17.1 vtable (`+0xe0` `0x1407c2390` ... `+0x110`
`0x1407c2ab0`) and disassembled in place.

**Per-frame reset (`f = 9`).** It happens in the per-frame chr update `0x140401a30`
[`0x1404016d0`], called from `FUN_140660120` and `FUN_1404d0c30`:

```
140401e3c  call [rax+0x600]  ; ChrIns::GetDefaultPlayerMenuCtrl
          mov rdx,[rax]; mov rcx,rax
          call [rdx+0xe0]    ; -> flags = 9
```

The signature `ff 90 00 06 00 00 48 8b 10 48 8b c8 ff 92 e0 00 00 00` occurs once in each image:
1.17.1 `0x140401e3c`, 1.16.2 `0x140401adc`. The 1.16.2 decompile shows the same thing:
`ppCVar9 = GetDefaultPlayerMenuCtrl(param_1); (*(*ppCVar9)->FUN_1407b0350)(ppCVar9);`. The struct
member name there is stale, but its offset is `+0xe0`.

**HKS `act(147)` = `SetCanChangeEquipmentOn` -> `|= 0x4`.** `HksAct` is `0x14040d100`
[`0x14040cbd0`]. Its act id comes in `edx`. Ids at or below `0x3e8` dispatch through the byte
table `0x40e77c` and the dword table `0x40e6c4` (1.16.2: `0x40e24c` and `0x40e194`). Resolving
id 147 gives `0x14040d946` [`0x14040d416`]:

```
14040d949  call 0x1403f4270            ; PlayerIns::IsMainPlayerIns
14040d95c  call [rax+0x600]            ; GetDefaultPlayerMenuCtrl
14040d968  call [rdx+0xf8]             ; |= 4
```

The 1.16.2 Ghidra decompile labels this case `SetCanChangeEquipmentOn` and the call
`thunk_FUN_1407af900`, whose vtable-struct offset is `0xf8`.

**HKS `act(163)` = `SetCanChangeEquipmentOff` -> `|= 0x10`.** Id 163 resolves to `0x14040d973`
[`0x14040d443`] and calls `[rdx+0x100]` at `0x14040d995` [`0x14040d465`]. Ghidra labels this case
`SetCanChangeEquipmentOff`.

**TAE event 0 `ChrActionFlag`, type 11 -> `|= 0x2`.** Handler `CS::CSChrTaeAnimEvent::_ChrActionFlag`
`0x140427b30` [`0x1404275e0`]. The call site is 1.17.1 `0x140427e58 mov rax,[r12]; mov rcx,r12;
call [rax+0xf0]` [`0x140427908`]. The 1.16.2 decompile:

```c
case 0xb:
  (*(*ppCVar16)->FUN_1407b0330)(ppCVar16);              // vtable +0xf0 -> flags |= 2
  pCVar23 = param_1->chrIns->componentContainer->actionRequest;
  CSChrActionRequestModule::CancelMovement(pCVar23,true);
  pCVar23->taeCancels = pCVar23->taeCancels | 0x40;
```

Type 11 is therefore the movement-cancel window. The name is inferred from what the handler
does. Any animation that carries this window also opens the gate for those frames.

The other two hits in the vcall scan (`0x140efa298` slot `+0xe8`, `0x140f09419` slot `+0xf0` in
1.16.2) are Scaleform code that happens to have `0x600` in a nearby operand. They are not
`CSPlayerMenuCtrl` calls. The doc does not name a caller of slot `+0xe8`, the only thing that
can clear bit0 after the reset; none was found.

### `CSChrActionFlagModule.actionAnimationFlags` bit 0 (`+0x10`)

- **Set** by HKS `act(9100)` (`Wait`). This is the third act-id table (ids at or above 9001:
  `add edx,-9001; cmp edx,0x68`, byte table `0x40e900`, dword table `0x40e8dc`; 1.16.2 `0x40e3d0`
  and `0x40e3ac`). Id 9100 resolves to `0x14040e5e2` [`0x14040e0b2`]:

  ```
  14040e5e2  mov rax,[rsi]
             mov rcx,[rax+0x190]       ; modules
             mov rax,[rcx+0x8]         ; CSChrActionFlagModule
             or  dword [rax+0x10],1
  ```

  The Ghidra case label is `Wait`. A byte scan of `0x1403c0000` to `0x140500000` for `or [r+0x10],1`
  finds this one site and no other.
- **Cleared** every frame by `CS::ChrIns::PreBehaviorSafe` `0x140401f30` [`0x140401bd0`], which
  calls `0x1404060b0` [`0x140405b80`]:

  ```
  1404060e6  mov [rcx+0x10],r12        ; r12 = 0 -> actionAnimationFlags = 0, +0x14 = 0
  1404060f3  mov [rcx+0x40],r12        ; actionModifiersFlags = 0
  ```

  The 1.16.2 decompile: `param_1->actionAnimationFlags = 0; param_1->field2_0x14 = 0;
  param_1->unkFlags = 0; param_1->actionModifiersFlags = 0;`.
- HKS `SetWeaponCancelType` masks `& 0xfffff807`, which keeps bit 0.

## 4. Which HKS states drive those acts

Source: `/home/banon/er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script/c0000.hks`, read
with `scripts/er-hks-disasm.py <file> --calls-to act`. **This extraction predates 1.17.**
1.17.1 was not re-extracted. The engine act ids are confirmed unchanged by the 1.17.1 jump-table
resolution above. That the script still makes the same calls in 1.17.1 is inferred.

- `act(163)` Off: `ArtsCommonFunction` (pc 13, unconditional, before any `Exec*`),
  `ArtsStanceCommonFunction`, `ItemCommonFunction`, `QuickItemCommonFunction`,
  `StealthItemCommonFunction`, `GestureCommonFunction`, `QuickTurnCommonFunction`,
  `DefaultBackStep_onUpdate`, `BackStepGuardOn/End_UpperLayer_onUpdate`, `LadderEndCommonFunction`.
- `act(147)` On: `FallCommonFunction`, `LandCommonFunction`, `EventCommonFunction`, all the
  `Ladder*CommonFunction`s, `Ladder_Activate`, `LadderDrop_onUpdate`.
- `act(9100)` Wait: `IdleCommonFunction`, `StopCommonFunction`, `MoveCommonFunction`,
  `GuardCommonFunction`, `Idle_onActivate`, the `*Stop*_onActivate` states, `Move_Upper` and
  `Guard*_Upper_onActivate`, the `Ride*` states, and `Stealth_Idle` / `StealthStop`.

`ArtsCommonFunction` is reached from 44 SwordArts `onUpdate`s (`--calls-to ArtsCommonFunction`).
These include every loop state: `SwordArtsLoopLoop_onUpdate`, `SwordArtsHalfLoopLoop_Upper_onUpdate`,
`SwordArtsLoopEnd_onUpdate`, and the `Both`/`Left` variants. `SwordArtsLoopLoop_onUpdate` reaches
it at pc 153. It returns early only when it fires `W_SwordArtsLoopEnd` or `W_FallDeath`.

So while a held skill loops, every frame has `f & 0x10` and the gate is shut. That is the
"Cannot change equipment" the user saw.

## 5. Mechanism of the window

1. **The weapon switch writes nothing the gate reads.** `0x14042d4d0` [`0x14042cf80`] ends with
   `SetChrAsmEquipSlotsIndex`, `armStyle = OneHanded`, `SetPrecisionShootingMode(false)`, the
   `RemoveWepParam*SpEffects` call, `FUN_140658cc0(player)` and `PlayerIns+0x5d0 = 1`. Its only
   `call [rax+0xf8]` (`0x14042d552`) is on the `ChrManipulator` that `GetManipulator` returns, not
   on `CSPlayerMenuCtrl`. There is no `+0x600` or `+0x6a0` access in its body
   (`0x14042d4d0`..`0x14042d8ed`).
2. **It does not clear a lock the skill set, because no such lock exists.** Both inputs are rebuilt
   every frame (sections 3 and 4). The skill's "lock" is only `ArtsCommonFunction` calling
   `act(163)` from the active SwordArts state's `onUpdate` on each frame.
3. **What opens the window is the behaviour-state change.** `ArtsCommonFunction` calls
   `act(163)` at pc 13 and then `ExecWeaponChange` at pc 33, returning `TRUE` when it fires. On
   the frame of the swap, `0x10` is still set. From the next frame the upper body is in
   `WeaponChangeStart_Upper` and then `WeaponChangeEnd_Upper`. Their common function
   `WeaponChangeCommonFunction` never calls `act(163)` (its dump has no `act` call). So `0x10` is
   absent.
4. **The positive term comes from the lower body (inferred).** `WeaponChangeStart_Upper_onUpdate`
   calls `HalfBlendLowerCommonFunction(Event_WeaponChangeStartMirror, ...)`. That function either
   fires `ExecStopHalfBlend` or `Event_Move` / `MoveStart` on the lower layer. `StopCommonFunction`
   and `MoveCommonFunction` both call `act(9100)` at pc 3, which sets `actionAnimationFlags` bit 0.
   The static read does not prove which lower state is active on a given frame. A TAE type-11
   window in the weapon-change animation would open the gate the same way, through `f |= 2`; the
   TAE was not read.
5. The user's timing fits this. 1.2 s before the skill the menu was refused. Mid-loop the gate is
   shut by `act(163)`. 0.10 s after the off-hand swap the menu opened, because the frame no longer
   has an Arts state and the lower body is in Stop or Move.
6. **Lock-on, backstep and fall recovery (inferred from the act list).** Each of these replaces
   the SwordArts state with one that does not call `act(163)` for at least one frame:
   - `LandCommonFunction` and `FallCommonFunction` call `act(147)` outright.
   - A backstep's own `onUpdate` calls `act(163)`. The window opens when it hands over to a
     Stop/Idle state (`act(9100)`).
   - Lock-on was not traced to a specific state.

The "keeps repeating with the new weapon's model" half of the glitch is a separate question (why
the SwordArts loop on the re-held L2 runs against the new weapon). This document does not answer
it. The gate only explains how the equip goes through mid-skill.

## Reproduce

```bash
python3 /home/banon/projects/er-mods-rs/scripts/fmg-id-lookup.py --root /home/banon/projects/er-msg/engus/menu-msgbnd-dcx --near 103130 --win 2
ER_DEOBF_BIN=eldenring-deobf-1.17.1.bin python3 /home/banon/projects/er-mods-rs/scripts/find-deobf-bytes.py 'da 92 01 00'
python3 /home/banon/projects/er-mods-rs/scripts/find-vtable-rva.py CSPlayerMenuCtrl EquipDialog CSChrMenuFlags
ER_DEOBF_BIN=eldenring-deobf-1.17.1.bin bash /home/banon/projects/er-mods-rs/scripts/disas-deobf.sh 0x1407c1fb0 0x88
python3 /home/banon/projects/er-mods-rs/scripts/find-vcall-slot-sites.py --image eldenring-deobf-1.17.1.bin --slot 0xe0 --slot 0xf8 --slot 0x100 --field 0x600 --window 40
python3 /home/banon/projects/er-mods-rs/scripts/er-hks-disasm.py /home/banon/er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script/c0000.hks --calls-to act
```
