# Chainsaw glitch: the agent-driven driver

`scripts/frida/chainsaw-driver.js` performs the chainsaw glitch by itself on ELDEN RING 1.17.1. It
blocks the player's input, equips the test loadout natively, then presses every button the sequence
needs. Each step waits for a game-state semaphore and fails with a named state if that semaphore
does not arrive within a set number of frames. No step waits on a timer alone.

None of this has run against the game yet. Everything below comes from static reading, bd memories
of earlier live runs, and the offline selftest. The last section lists what only a live run can
confirm.

Files:

| file | role |
|---|---|
| `scripts/frida/chainsaw-driver.js` | The core (a pure state machine; `node ... --selftest`, 49 checks) and the Frida binding. |
| `scripts/er-chainsaw-drive.py` | Prints the commands that start a drive (`command`), reads the result back (`report`), `--selftest`. |
| `scripts/frida/chainsaw-probe.js` | A passive probe for a run the player performs by hand. The driver reuses its hit, equip, switch, gate and FP hooks. |
| `scripts/interactions/chainsaw_report.py` | Reads the passive probe's log. |

## 1. Injection stage

The driver presses buttons by replacing the `XINPUT_STATE` that `XInputGetState(0)` returns,
inside an `Interceptor` `onLeave` on the export's real body (Wine's export is a 5-byte `e9` jump,
which is followed first). L2 is the analog `bLeftTrigger` byte, set to 255. A stick value at
`sThumbLY` is used only for optional list navigation. The same replacement is also the block: the
game never sees the real pad state.

Why this stage, and the runs it rests on:

| run / date | what was injected | result |
|---|---|---|
| br-20260916-074718-82a9 | XInput counter | 590 `XInputGetState` polls in 6 s, all slot 0, all `ERROR_SUCCESS`. The game polls a connected pad about 98 times a second. |
| br-20260916-074718-82a9 | DirectInput `DIK_W` stamp | Delivered (every link was confirmed) and the character moved 0.000. The keyboard is not the stage the character acts on while a pad is connected. |
| 2026-07-18 (bd `synthetic-xinput-injection-does-not-move-character-2026`) | XInput stick | No movement. Later explained: the hook was laid on the Wine thunk and never ran (0 hook fires). |
| br-20260916-082140-882f (bd `xinput-thunk-must-be-followed-first-agent-driven-movement-2026-09-16`) | XInput stick, after following the thunk | The character moved about 20 units. 331 hook fires, 170 stamps. |
| br-20260916-083935-5990, br-20260916-160436-f3e8 | XInput A (0x1000), X (0x4000) | X used the quick item. A answered the invasion-bounds popup and accepted Seamless's dialog. This is the only live evidence that an injected button confirms a menu. |
| br-20260917-025609-d24a (`scripts/er-pad-reaches.py`) | XInput D-pad Down | The game polled 31 times and received the mask on 20 of them. |
| br-20260916-083008-92e8, -083455-0b71 | XInput D-pad Down and stick Down in the bounds popup | The cursor did not move. This is still unexplained (bd `bounds-popup-always-answers-nearbyonly-cursor-wont-move-2026-09-16`). |
| 2026-07-22/23 (bd `CONVERGENCE-both-memory-injection-paths-fail-...`) | `CSInGamePad+0x88` and `inputmgr+0x90` memory writes | These did not drive the pause menu. The driver writes neither. |
| 2026-09-19 (bd `dpad-right-weapon-switch-what-is-actually-established-2026-09-19`) | none (the player's real press) | `DLUID::PadDevice::Poll` 0x141f6d940 copies XInput `wButtons` to `device+0x890`. This is the readback the driver uses to verify the block. |

So XInput is the stage that moves the character and confirms a dialog on this machine. These have
never been shown with an injected press: Start opening the pause menu, A confirming inside
`EquipDialog` or `GaitemSelectDialog`, d-pad or stick navigation in a list, and an injected L2
starting a skill. The driver gates each of them on a semaphore, so a run that fails on one names
the step.

The driver uses Frida, not a DLL. Every hook is an `Interceptor`; there are no watchpoints and
no `MemoryAccessMonitor`. Native calls go through `NativeFunction`. Nothing here needs in-process
Rust.

### The block and how it is verified

| device | how it is blocked | how it is counted |
|---|---|---|
| Pad slot 0 | `XInputGetState` and ordinal-100 `XInputGetStateEx` (`GetProcAddress(xinput, 100)`) return the driver's state. A disconnected slot 0 is forced to connected. | `polls`, `stamped`, and `foreign.pad` (real input that was replaced) |
| Pad slots 1-3 | Reported as `ERROR_DEVICE_NOT_CONNECTED` (0x48f). | `foreign.padOther` |
| Keyboard, mouse | DirectInput `GetDeviceState` (vtable slot 9 of a throwaway device, same as `er-quickload input_blocker.rs`) is zeroed for sizes 256, 16 and 20. | `foreign.kb`, `foreign.mouse`, `polls.kb/mouse` |
| Pointer, keys | `GetCursorPos` is pinned where it was when the block began. `GetKeyState`, `GetAsyncKeyState` and `GetKeyboardState` return nothing held. | `foreign.cursor`, `foreign.keys` |

The final `result` reports `block.held`. It is true only when every slot-0 poll during the drive
was stamped and no pad device the game polled disagreed with the stamp. That second test reads
`device+0x890` after each `PadDevice::Poll` and compares it with the last stamped `wButtons`.
`padDeviceTracksStamp` says a device carried a nonzero stamp with no mismatch, which shows the
press reached the game's own pad object. The game-event check runs in the core: any weapon switch,
equip or menu window that the current step did not ask for fails the run (`foreign_switch`,
`foreign_equip`, `foreign_menu`). `foreign.*` counts the real input the player made and the block
replaced. Nonzero counts there do not fail the run, because that input never reached the game.

Input goes back to the player when the drive reaches a verdict, and also in `dispose` (detach,
reload in place, or the watcher's `SIGTERM`).

## 2. Hooks and addresses (1.17.1)

All of these are below rva `0xafefe9`, so 1.17.0 and 1.17.1 agree, except the last three, which
were measured on 1.17.1 directly. "Mapped" means `scripts/map-rvas-1162-to-1170.py` returned a
unique signature, which was then read in `eldenring-deobf-1.17.1.bin`.

| what | 1.17.1 | 1.16.2 | evidence |
|---|---|---|---|
| Frame clock: main player's `PreBehaviorSafe` | 0x140401f30 | 0x140401bd0 | mapped; equip-gate.md (clears `actionAnimationFlags` each frame) |
| Native-call site: `CSFeManImp::Update` | 0x140772a50 | 0x140771bd0 | mapped; hooked live 2026-09-19 (bd `frame-hook-restore-lets-the-dpad-switch-again-proven-live-2026-09-19`) |
| Open windows: `MenuWindowJob::Run`, `job+0x130` to RTTI | 0x1407ae040 | 0x1407ad1c0 | used live 2026-10-02 to name `GaitemSelectDialog` (bd `r3-item-list-view-mode-sceneobjproxy-7f0-2026-10-02`) |
| Gate wrapper `CanChangeEquipmentInSlot` | 0x140789910 | 0x140788a90 | equip-gate.md |
| Gate call at the slot confirm (return address) | 0x1408de3aa | | 1.17.1 disassembly of `EquipDialog` vtable+0x90 0x1408de350 |
| Gate call at the commit (return address) | 0x140788b00 | | equip-gate.md (`0x140788afb call`) |
| `GetGR_Dialogues(out, 103130)` | 0x140760a20 | | equip-gate.md |
| Item list detail naming / `GetWeaponName` | 0x14099a5a0 / 0x140d12ab0 | / 0x140d11370 | er-r3-view `imp.rs`, `r3-selected-weapon-name.js` (measured 2026-10-03) |
| `EquipItemToChrAsmSlot(slot, MenuGaitem*)` | 0x140788ab0 | 0x140787c30 | mapped; decompile: reads only `itemIdx` +0x48 and `itemId` +0x4c |
| `UnequipItem(slot, removeItem)` | 0x14078ace0 | 0x140789e60 | mapped |
| `GetEquipInventoryData(egd)` = `egd+0x158` | 0x140247b30 | 0x140247b30 | 1.17.1 disassembly `lea rax,[rcx+0x158]` |
| `GetItemInventoryIdx(inv, int*)` | 0x14024c560 | 0x14024c560 | mapped, unique |
| `GetSlotIndexByItemIndex(egd, idx)` | 0x140248440 | 0x140248440 | mapped, unique |
| `GetParamIdInSlot(egd, slot)`: the observed `slots` and the setup read-back. The game-data view moves when an equip runs; the `PlayerIns` view moves on the next character update. | 0x1402470e0 | 0x1402470e0 | mapped, unique; 1.17.1 disassembly `add rcx,0x6c` (ChrAsm) |
| D-pad weapon switch | 0x14042d4d0 | 0x14042cf80 | skill-survives-equip.md |
| `CalculateDamage2` | 0x140448910 | 0x1404483b0 | chainsaw-probe.js |
| `ConsumeFp` | 0x14047fba0 | 0x14047f640 | damage-and-fp.md 3a |
| `GetEquipmentEntryParamId(PlayerIns*, slot)` | 0x1406577b0 | 0x140656960 | damage-and-fp.md 1a |
| `DLUID::PadDevice::Poll` (`wButtons` at +0x890) | 0x141f6d940 | 0x141f6bad0 | measured live 2026-09-19 |
| `WorldChrMan` / `GameDataMan` globals | 0x143d69ff8 / 0x143d61f98 | / 0x143d5df38 | equip-gate.md / data map 642/642 |

Offsets: `PlayerIns+0x6a0` `CSPlayerMenuCtrl`, flag word at +0x20. `ChrIns+0x190` modules: data
+0x00 (fp +0x148, max fp +0x14c), action flag +0x08, TimeAct +0x18 (queue +0x20, stride 0x10,
write +0xc0, read +0xc4), sword arts +0x110. `GameDataMan+0x8` PlayerGameData, whose equipment is
inline at +0x2b0. `ChrAsm` is at `egd+0x6c`, with the selected left slot at +0x0c and the right at
+0x10 (fromsoftware-rs `player_game_data.rs`, the bd memory above). Every dialog's GridControl is at
`+0xa38` with its selected cell at `+0xd4`. `EquipDialog`'s cell table is at `+0x2550`, rows 0x58
apart and 8-aligned, with the slot at row+4.

## 3. The run, step by step

One frame is one main-player `PreBehaviorSafe`. A tap is held for 2 frames and then released for
2. "Budget" is the frame count after which the step fails with the named state.

| state | does | semaphore that releases it | budget, failure |
|---|---|---|---|
| `PRECHECK` | Waits 10 frames to collect the baseline windows. | `player`; all three blocks installed; XInput polls rose; no menu window up; `armRight` in {1,3,5} and `armLeft` in {0,2,4} | 120: `precheck_no_player`, `precheck_block_not_ready`, `precheck_no_pad_polls`, `precheck_menu_already_open`, `precheck_no_arm_slots` |
| `SETUP` | Native requests, one per wait, run in `CSFeManImp::Update`. First `find` the target in the inventory. Then re-plan from a fresh read: wheel into the active right slot, Seal into the active left, Staff into the next left, unequip the third left if it is not in `offhandDefers`. | A `native` event with `ok` (the slot reads the item afterwards). An equip into the slot the item already holds is skipped, because the engine would take it off. | 30 per action, 8 actions: `setup_item_not_in_inventory`, `setup_native_failed`, `setup_no_native_result`, `setup_did_not_converge` |
| `SETUP_VERIFY` | | Plan empty, `GetEquipmentEntryParamId(-1)` = wheel and `(-2)` = Seal | 30: `setup_held_mismatch` |
| `FP_CHECK` | Optional FP refill (`refillFp`) | `fp >= ceil(maxFp * 0.9)`. Type 239 exits its loop when FP runs out, and FP does not regenerate on its own. | 60: `fp_low`, `fp_refill_failed` |
| dry run (`OPEN_MENU` through `C3`, then `DRY_CLOSE`) | Runs once, before the first attempt, with no skill. Walks Start and confirms 1 and 2, reads the item list, navigates to the target if needed, then backs out with B. No third confirm. | Learns `mainCell` (the pause-menu cell whose A opened `EquipDialog`), `listOpenHighlight` (the record the list opens on), and `nav` | per step, see below; `dry_run_refused` if the gate refuses an idle player |
| `HOLD_L2` | L2 = 255 | Chainsaw: 3 consecutive frames with a loop clip of category 839 (`839040051`/`839040056`) in the TimeAct queue. Control: any clip of the skill's category. | 180: `skill_never_started` |
| `LOCK_ON` (`lockOn`) | Taps R3 | `PlayerIns+0x6b0` is not -1 | 30: `lock_on_failed` |
| `SWAP` | Taps d-pad left with L2 still held | Weapon-switch hook 0x14042d4d0 returned **and** `GetEquipmentEntryParamId(-2)` changed | 90: `swap_never_completed` (check the binding: `scripts/er-keybind-repair.py --diff --pad-only`) |
| `MENU_DELAY` | | 6 frames (0.1 s at 60 fps) counted from the switch-complete frame | none |
| `OPEN_MENU` | Taps Start | `.?AVMainTopDialog@CS@@` is pumped | 60: `menu_never_opened` |
| `C1` | Taps A (Equipment) | Before the press: `MainTopDialog+0xa38+0xd4` equals the learned `mainCell`. After: `.?AVEquipDialog@CS@@` is pumped. | 60: `main_menu_cursor_unknown`, `main_menu_cursor_moved`, `wrong_main_menu_entry`, `equip_dialog_never_opened` |
| `C2` | Taps A (slot) | Before the press: the cell under `EquipDialog`'s cursor edits the active right ChrAsm slot. If the cursor is on another right-hand cell, d-pad left/right steps are taken, each released by `+0xd4` changing. After: a gate event from return address 0x1408de3aa, then `.?AVGaitemSelectDialog@CS@@`. | 60 (20 per step): `wrong_slot_focused`, `slot_nav_no_progress`, `item_list_never_opened` |
| refusal | The gate returns 0 at 0x1408de3aa, or `GetGR_Dialogues(103130)` is called. Goes to `REFUSED`. | | see below |
| `C3` | Taps A (target) | Before the press: the detail panel's naming (0x14099a5a0 calling 0x140d12ab0) names the target's base id. Otherwise d-pad (or stick) steps in `navFirst` direction, then once the other way, each released by a new naming. The naming fires on a cursor move only while one of the panel's status holders is shown, which view 0 never has after the open, so the driver hooks the panel update (0x140999930) and holds holder A's shown byte (`*(parts+0x188)`) at 1 for each call; `panelSeq` counts those updates. After: the `EquipItemToChrAsmSlot` event for the right slot, with the target held. | 60 (20 per step, 40 steps): `highlight_unknown`, `target_not_highlighted` (nav off), `nav_no_effect` (the panel never updated: the cursor did not move), `nav_unnamed` (the panel updated and nothing was named), `target_not_in_list`, `nav_limit`, `commit_refused` (gate at 0x140788b00), `equip_did_not_apply`, `no_equip_event` |
| `RELEASE_L2` | L2 = 0 while the menu is still open (`UpdateFromManipulator` 0x140408190 keeps a held button masked after the menu closes) | 3 frames from the equip event | none |
| `CLOSE_MENU` | Taps B until no menu window is pumped | Each tap: the window count drops | 45 per tap: `menu_would_not_close` |
| `REHOLD_L2` | L2 = 255 | Success: 20 frames of the source loop clip with the target held **and** at least `requireHits` (1) `CalculateDamage2` hits computed with the target held. Records the `ConsumeFp` charges and FP. | 300: `loop_without_hits` (the loop ran but nothing was hit), `no_source_skill_after_equip`; `target_skill_played` if a category-832 clip appears |
| `REFUSED` | Waits for the refusal window (any window that is not the three menu classes), then taps A | That window is no longer pumped. If no window shows within budget, it backs out anyway and records `dialogClass: null`. | 60: `refusal_dialog_would_not_close` |
| `REFUSED_CLOSE`, `REFUSED_IDLE` | B until closed, L2 released | 10 consecutive frames with no stance clip, then back to `FP_CHECK` for the next attempt | 240: `never_idle_after_refusal`; `refused_out_of_attempts` after `attempts` (3) |
| `CONTROL_HOLD` | L2 held for `controlHoldFrames` (300) | Counts loop frames, clip frames, hits and FP charges | none |
| any failure | Releases L2 and closes every menu it opened (`CLEANUP`) | | `no_frame_tick` if slot-0 polls run 300 past the last frame tick |

The refusal is recorded on its attempt with the gate verdict, the predicate read at that moment
(`flags`, `aaf`), and the frame distance from the swap. That distance is the value to tune
`menuDelayFrames` against.

### Setup and the build under test

Build `98f687a96d43b1` revision 3 equips Bloodfiend's Arm in planner `equipIndex` 0, Misericorde in
2 and Spiralhorn Shield in 3. Through `ARMAMENT_CHR_ASM_SLOTS` those are ChrAsm 1 (R1), 5 (R3) and
0 (L1). Setup changes this:

| ChrAsm slot | after setup | why |
|---|---|---|
| active right (R1) | Ghiza's Wheel +10, 23100010 | Spinning Wheel, SwordArtsParam 1039, type 239 |
| active left (L1) | Frenzied Flame Seal +10, 34090010 | SwordArtsParam 10, `isRefRightArts` 1 |
| next left (L2) | Watchdog's Staff +10, 23010010 | The d-pad left swap lands here. Its skill, Sorcery of the Crozier (1192), has `isRefRightArts` 1, so L2 still fires the right-hand skill. |
| third left (L3) | left as is if unarmed, Seal or Staff; otherwise unequipped | The shield must not stay in the cycle: Parry (302) has `isRefRightArts` 0 and would take L2. |

Starscourge Greatsword +10 (4050010) only needs to be in the inventory. Setup checks this first
(`find`). In planner revision 3 the target is inventory position 3 and the wheel is position 8.
The in-game list order is not taken from the planner: the dry run reads which record the list
opens on (`learned.listOpenHighlight`) and how many navigation steps reach the target
(`learned.nav`), and attempts gate confirm 3 on the naming. Ids that hold a different upgrade
level can be passed with `--set sourceWeapon=...` and the matching keys.

## 4. Running it

Preconditions, set up by whoever launches:

- The game is up through the approved direct/offline path, in the world, with build `98f687a96d43b1`
  loaded, and something to hit in front of the player. Without a target, `REHOLD_L2` can only
  report `loop_without_hits`.
- No other input driver in the profile: no `er-input-harness` drive, and nothing calling
  `er_quickload_hold_xinput_pad`. Its detour would OR its own mask onto the stamp.
- `er-r3-view` should not be in the profile. It detours the same naming functions, and its view
  modes can hide the detail panel. A hidden panel means no naming, which fails the run with
  `highlight_unknown`.
- The item list in its default view (R3 view 0), so the detail panel names records.

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-chainsaw-drive.py command --preset chainsaw
# prints, and does not run:
python3 /home/banon/projects/er-mods-rs/scripts/er-frida-up.py
uv run --with frida python3 /home/banon/projects/er-mods-rs/scripts/er-frida-watch.py --agent /home/banon/projects/er-mods-rs/scripts/frida/chainsaw-driver.js --role chainsaw-drive --config-json '{"mode": "chainsaw"}'
```

Run the watcher as its own background task with no `timeout` around it. The drive starts when the
agent loads and ends on its own verdict. The watcher stays attached with the hooks passive until
it is detached. Watch progress with `Monitor` on `~/.cache/er-frida/hits.jsonl`, or read it back:

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-chainsaw-drive.py report --since <epoch seconds of the start>
```

Controls: `--preset control-wheel` (Spinning Wheel on Ghiza's Wheel) and
`--preset control-starcaller` (setup puts Starscourge in the right hand and holds Starcaller Cry).
Useful overrides: `--set refillFp=true`, `--set listNav='"stick"'`, `--set lockOn=true`,
`--set menuDelayFrames=4`, `--set trace=true` (one `obs` record per frame).

Offline checks: `node /home/banon/projects/er-mods-rs/scripts/frida/chainsaw-driver.js --selftest`
(49 checks against a fake game that reacts to the pad) and
`python3 /home/banon/projects/er-mods-rs/scripts/er-chainsaw-drive.py --selftest`.

## 5. What only a live run can confirm

1. The injected Start opens `MainTopDialog`. No injected button has opened the pause menu before.
2. `.?AVMainTopDialog@CS@@` is the in-world pause menu. The name and its `+0xa38` grid come from
   static reading only. If it is wrong, `OPEN_MENU` fails with `menu_never_opened` and lists the
   windows that did open.
3. Injected A confirms inside `EquipDialog` and `GaitemSelectDialog`. A is proven only on popups so
   far, and the menu confirm's pad code 2004 has not been mapped to a physical button.
4. Injected B backs out of each menu (pad code 2005 is not mapped either).
5. Whether the item list opens on the equipped item, the top, or the last position, and whether
   d-pad or stick steps move it. Injected list navigation has never worked here (the bounds popup).
   The run answers this as `learned.listOpenHighlight` and `learned.nav`, or fails with
   `nav_no_effect`.
6. The detail panel names the highlighted record when the list opens and, with holder A held shown,
   on every cursor move. Static reading (1.17.1): the update 0x140999930 names through holder A
   (`parts+0x180`) or holder B (`parts+0x198`, shown only in view 2). The open runs it once while
   holder A's new composite still has its constructor's shown byte (0x140999ec0), then view 0's
   pane callback 0x140999130 hides holder A through sub-list `+0xb50` (callback 0x140998f60). The
   run of 2026-10-05 saw exactly that one naming and nothing after two taps, so its `nav_no_effect`
   did not show that the cursor stood still. `panel.updates` in the result and `panelMoved` on each
   silent step now say which, and `listView` names the view.
7. An injected L2 (analog trigger 255) starts Spinning Wheel, and the TimeAct queue shows
   `839040050`/`839040051` in the form `category * 1e6 + clip`.
8. The d-pad left swap completes mid-loop with L2 held, and the gate is open about 6 frames later
   (`gate.open` at `MENU_DELAY`). The static window is inferred (equip-gate.md section 5).
9. The skill keeps looping on Starscourge after the re-hold, and hits are computed with it held.
   That is the glitch itself.
10. Native `EquipItemToChrAsmSlot` / `UnequipItem` called from `CSFeManImp::Update` are safe there,
    and the slot reads back on the same call.
11. `CSChrDataModule+0x148/+0x14c` are FP and max FP on 1.17.1. They are taken from fromsoftware-rs,
    not from this build.
12. `PlayerIns+0x6b0` reads -1 with no lock-on (only matters with `lockOn`).
13. `PadDevice::Poll` is reached on this run and its `+0x890` tracks the stamp
    (`padDeviceTracksStamp`), and the ordinal-100 export exists or is never polled.
14. Frame-count timing: 6 frames as 0.1 s assumes 60 fps. The refusal records carry the measured
    frame distances.

## Reproduced: the pivot sequence, committed from the frontend update (2026-10-05)

Config `{"mode":"chainsaw","input":"action","refillFp":true,"sequence":"pivot","pivotCommit":"fe",
"attempts":6,"pivotL2After":[10,12,14,16,18,20],"pivotWindowFrames":120}`, build 98f687a96d43b1,
two runs, the same outcome both times: attempt 1 (L2 10 frames after the swap tap) never selects the
skill, attempt 2 (12 frames) reproduces the glitch.

| frame | event | gate flags |
|---|---|---|
| 189 | selector writer `0x1419bb530` stores `argTaeId` 839040050 (Spinning Wheel start) | 11, open |
| 189 | `CSFeManImp::Update` commits Starscourge into the right slot; the commit's own gate passes | 11, open |
| 190 on | main-player tick | 25, shut (`0x10`) |
| 287 | loop selector stores 839040051 with Starscourge held | 25 |

Then 192 frames of the Spinning Wheel loop with Starscourge in hand, FP 75 to 0 charged on it.
Hits were not measured: nothing was in range (`loop_without_hits`).

What the runs settle:

- The window is one frame: from the selector choosing the source clip to the next main-player tick,
  where the skill's HKS state sets `0x10`. The menu's commit runs in the frontend update, the same
  place. A driver that reads the gate on the main-player tick never sees it open with the source
  clip (attempts 5 to 10 of the tick-mode sweep, every L2 delay from 10 to 20 frames).
- The swap clip's TAE type-11 window (flags bit 2) is what holds the gate open at that moment.
- Only a fresh L2 press starts a skill. A press 2 to 8 frames after the swap tap dies in the swap
  clip, and holding L2 into idle never starts one.
- A commit on the frame of L2 down, before the selection, plays the target's own skill (Starcaller
  Cry 832040000): the equip only changed which skill L2 starts.
- The loop node re-selected after the equip falls back to child 0, `a839_040051`, because `a832`
  has no 40051. skill-survives-equip.md section 3 had this as inferred; it is now measured.
