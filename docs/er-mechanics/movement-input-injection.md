# Movement input injection (left stick without a controller)

Where the player's left-stick movement goes from the pad to the behavior graph on the installed
1.17.1, and the one field a Frida agent should overwrite to make the player walk forward for N
frames. Static research only: nothing was launched and no runtime result is claimed.

Labels: **VERIFIED** read out of the 1.16.2 named Ghidra dump (:8765) and re-read byte-for-byte in
`eldenring-deobf-1.17.1.bin`; **DATA** read out of a file (HKS, another doc); **INFERRED** follows
from the verified code but was not measured. Addresses are 1.17.1 with the 1.16.2 source in
brackets, carried by `scripts/map-rvas-1162-to-1170.py` (everything here is below rva `0xafefe9`,
so 1.17.0 and 1.17.1 agree) and then read in the 1.17.1 image.

## The recommendation

Hook `CSChrActionRequestModule::UpdateFromManipulator` `0x140408190` [`0x140407c60`] on entry,
the hook `scripts/frida/action-script.js` and `chainsaw-driver.js` already own, and on each frame of
the hold write:

| what | where | value |
|---|---|---|
| move vector, character-local | `manip+0x10` (`F32Vector4`) | `(0, 0, -m, 0)` |
| its copy | `manip+0x70` | the same 16 bytes |
| movement request | `args[0]+0xfc` bit 0 | set (bit 2 is dash: leave clear unless sprinting) |

with `manip = ChrCtrl+0x3b0 ?: ChrCtrl+0x18`, `ChrCtrl = (args[0]+0x8 -> ChrIns)+0x58`, `m` the
stick magnitude in `[0, 1]`. On the first frame after the hold, write the zero vector and clear
bit 0 once; the pad overwrites `+0x10` itself every frame, so nothing persists past the frame it
was written in.

```js
// inside the existing onEnter, after the main-player owner check
const chr = args[0].add(0x8).readPointer();
const ctrl = chr.add(0x58).readPointer();
let manip = ctrl.add(0x3b0).readPointer();
if (manip.isNull()) manip = ctrl.add(0x18).readPointer();
const v = [0, 0, -m, 0];
for (const o of [0x10, 0x70]) v.forEach((f, i) => manip.add(o + 4 * i).writeFloat(f));
const fl = args[0].add(0xfc);
fl.writeU32(m > 0 ? (fl.readU32() | 1) >>> 0 : (fl.readU32() & ~1) >>> 0);
```

Why this point and not an earlier one: it is the last thing the pad update calls, so it runs after
every producer of `+0x10` and before the one consumer, and it is device-independent in the same way
the request bits are: the stick, the keyboard and a zero pad all land in `+0x10` first.

## The chain (VERIFIED unless labelled)

ChrCtrl tick dispatcher `0x1403c8db0` [`0x1403c8da0`], once per frame per character:

1. `manip = ChrCtrl+0x3b0`, else `ChrCtrl+0x18` (`0x1403c8dd9`..`0x1403c8de5`). `ChrCtrl+0x10` is the
   owner, `ChrIns+0x190` the module container, `+0x80` the action request module.
2. `call [vt+0x50]` at `0x1403c8e1d` (the `[vt+0x40]` path is taken instead when
   `ChrCtrl+0xe8 & 0x60 == 0x60`; then no pad update and no `UpdateFromManipulator` runs).
   For the player that is `PadManipulator` `+0x50` = `0x1403d9f30` [`0x1403d9f20`] (vtable
   `0x142a2e788`, slot `+0x50` read from the 1.17.1 image), which calls in order:
   - `0x1403dbd20` [`0x1403dbd10`] -> `0x1403dcbe0` [`0x1403dcbd0`]: reads the left stick through
     `CSInGamePad` (`GetLeftStick` `0x140e2c210`, axis getters `0x140e2c330` = y at `+4`,
     `0x140e2c350` = x at `+0`; both return zero while `CSMenuMan` reports a menu), builds
     `(-x, 0, -y, 0)`, stores it raw at `pad+0xc0`, rotates it by the yaw at `manip+0x64`
     (the `+0x60` orientation vector), and hands it to `0x1403cdc40` [`0x1403cdc30`], which
     multiplies it by the transpose of `ChrCtrl+0x230` (the model matrix; `manip+0xa8` is the
     owner, `ChrIns+0x58` the ChrCtrl) and stores the result at `manip+0x10` and `manip+0x70`.
   - `0x1403daa90` [`0x1403daa80`], the action handler: builds the request bits, then at
     `0x1403db22a` tests `pad+0x1e5`. Zero: dodge/backstep/dash logic `0x1403dd4e0`, then if
     `|manip+0x10| > 0` calls `0x140408170` (`and [rcx+0xfc], ~1; or [rcx+0xfc], dl&1`) to set
     movement-request bit 0. Nonzero: `0x1403cdc30` copies a zero vector into `+0x10`/`+0x70`.
     Last, `call 0x140408190` at `0x1403db499`: `UpdateFromManipulator`, which reads `+0xfc` bit 0
     to advance `movement_request_duration` at `+0xf0` (`0x14040820c`).
3. `call 0x1403cc000` [`0x1403cbff0`] at `0x1403c8f70`, the ChrCtrl move update. It reads the
   vector with `0x1403cd780` (`movups xmm0, [rcx+0x10]`), takes `|v|` as the speed level (2.0 when
   `+0xfc` bit 2 is set and stamina is above zero; then capped by `ChrCtrlModifierData.movementLimit`),
   takes `atan2(v.x, v.z) + pi` wrapped to `(-pi, pi]`, in degrees, as the move angle, and stores
   speed at `ChrCtrl+0x3a0`, angle at `+0x3a4`, the normalised direction at `+0x350`
   (`0x1403cc55c`..`0x1403cc57b`). It zeroes everything when `ChrCtrl.disableMove`, a ladder,
   a throw, or the `0x60` event flags hold.
4. CSChrBehaviorModule `0x140420460` [`0x14041ff10`] copies them into the Havok behavior
   variables through the module's index table (names at `0x143b173e0`, 41 entries):
   `0x1403c6f90` (`+0x3a0`) -> `MoveSpeedLevel` (entry 2), `0x1403c6f70` (`+0x3a4`) ->
   `MoveAngle` (entry 1, negated when `0x1403c7a10` says so), `0x1403c6f80` -> `MoveDirection`
   (entry 3). `HksEnv` also calls the angle getter.
5. HKS (DATA, `scripts/er-hks-disasm.py`): `MoveStart`, which `IdleCommonFunction` and
   `StopCommonFunction` call, returns false while `GetVariable("MoveSpeedLevel") <= 0`; otherwise
   it fires `Event_Move` and `GetLocomotionState` reports `PLAYER_STATE_MOVE`. `SpeedUpdate`
   blends `MoveSpeedLevelReal` toward `MoveSpeedLevel` with `ConvergeValue`.

## The encoding

- `manip+0x10` is character-local (INFERRED from step 2: a world vector multiplied by the transpose
  of the model matrix). Forward is local `-Z`: `atan2(0, -1) + pi` wraps to a move angle of 0, and
  a stick pushed up at camera yaw 0 produces `z = -y`. A sideways or backward walk is the same
  vector rotated (`+X` right is INFERRED, not checked).
- Magnitude is the speed level directly. Which clip it picks (DATA, `neutral.md` section 1, labelled
  COMMUNITY there): up to about 0.6 walks (`a000_020000`, 1.50 m/s), above runs (`a000_020100`,
  4.01 m/s), 2.0 with bit 2 sprints. Use `m = 0.5` for a walk and `1.0` for a run.
- When not locked on, the move update also turns the character toward the vector (the
  `[vt+0x18]` call at `0x1403cc52a` takes the normalised direction), so a local `(0, 0, -m)` walks
  straight along the current facing without turning.

## Confirming it worked

In this order, each one a level further from the write:

1. Same frame, no clip or physics involved: hook `0x1403cc000` on leave (`args[0]` is the ChrCtrl;
   prologue is `mov r11, rsp` in the image, not yet hooked live) and read `ChrCtrl+0x3a0` == `m`
   and `ChrCtrl+0x3a4` near 0. Zero here with the write in place means a gate in step 3 held
   (`disableMove`, ladder, throw, `0x60` flags, `movementLimit` 3), not that the write missed.
2. The clip: the `CustomManualSelectorGenerator` writer `0x1419bb530` both agents already log
   should report `a000_020000` (walk) or `a000_020100` (run) within a few frames.
3. The body: `ChrIns+0x190 -> +0x68 -> +0x70` (the `pos()` in `action-script.js`) should advance
   about 0.025 m per frame at 60 fps for a walk, along the facing, and stop within a few frames of
   the release.

## Rejected alternatives

- `GetLeftStick` `0x140e2c210` on leave (overwrite the returned `{x, y}`): also device-independent
  and the most native (dodge direction and `pad+0xc0` follow too), but it is camera-relative, it is
  called by several pad functions per frame, and it is bypassed while a menu is open. A good second
  choice when the dodge direction must follow the stick.
- `pad+0x1f9` / `+0x1fc` / `+0x200` (VERIFIED reader, INFERRED meaning): `0x1403dcbe0` replaces the
  stick with magnitude `+0x200` at angle `+0x1fc` degrees whenever byte `+0x1f9` is set, which looks
  like a built-in forced walk and needs no hook. A byte scan of the 1.17.1 image found no writer
  besides the constructor's zeroing, and `pad+0x1e5` still zeroes the result, so it is untested.
- Writing `ChrCtrl+0x3a0`/`+0x3a4` directly: the move update overwrites them every frame from
  `+0x10`.

Note: bd `movement-proof-must-use-real-input-not-ram-write-2026-07-18` requires real input for the
load-validation movement proof. This injection is for Frida experiments in the action-script
family, not for that proof.
