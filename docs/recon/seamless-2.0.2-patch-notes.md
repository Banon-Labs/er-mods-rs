# Seamless Co-op v2.0.1 -> v2.0.2: what changed in `ersc.dll`

Measured 2026-10-27, game build 2.7.2.0, from the two binaries and one live game. All addresses are VAs at the preferred base `0x180000000`.

## The files

| | v2.0.1 | v2.0.2 |
|---|---|---|
| Banner | `Seamless Co-op v2.0.1 by Yui` | `Seamless Co-op v2.0.2 by Yui` |
| Size | 13,753,872 bytes | 13,971,472 bytes |
| PE timestamp | 1788389651 | 1793109274 |
| sha256 | `fcd11a1849fd37851bb449be30f1525cf498f63fd1fa568c4e15030ebe7cb251` | `0a093d8490967bf19c62f895fdf1fda21840282d2b2b371b9b4fc24a0cfe146e` |
| Archive | `vendor-archive/seamless/ersc-2.0.1.dll` | `vendor-archive/seamless/ersc-2.0.2.dll` |

The download zip (`Seamless Co-op v2.0.2 510 2.0.2 2026-10-27T14-30Z`) holds `ersc_launcher.exe`
and `SeamlessCoop/{ersc.dll, ersc_settings.ini, locale/english.json, crashpad/crashpad_handler.exe}`.
The shipped `ersc_settings.ini` has the same set of keys as the installed v2.0.1 one.

## Functions this repo pins

How a function was identified:

- **pdata**: a unique match on the sizes of the nine neighbouring `.pdata` entries
  (`scripts/ersc-disas.py pdatamatch --build v201 --to v202`), then the body compared in both builds.
- **bytes**: a unique match on a byte window with displacements and branch targets masked.
- **fn+off**: the enclosing function carried, plus the same offset, with the instruction matching.

| Function | v2.0.1 | v2.0.2 | `.pdata` index | Identified by |
|---|---|---|---|---|
| `show` | `0x1800241a0` | `0x1800241c0` | 301 -> 301, size `0xa0a` | pdata |
| invade action | `0x180025850` | `0x180025870` | 319 -> 319, size `0x75` | pdata; body identical except rel32 targets |
| cancel action | `0x1800258d0` | `0x1800258f0` | 320 -> 320, size `0x64` | pdata |
| leave-world action | `0x1800259d0` | `0x1800259f0` | 322 -> 322, size `0x74` | pdata |
| `BuildLobbyKey` | `0x1800ad6e0` | `0x1800aec50` | 1782 -> 1801, size `0x2d1` | pdata |
| item handler | `0x180092820` | `0x180093960` | 1444 -> 1459, size `0x182` | pdata; one `lea` registers it, moved `0x18007c423` -> `0x18007d445` |
| session lock (`_Mtx_lock`) | `0x1800f9828` | `0x1800fc038` | | the `call` target inside the invade body |
| `_Mtx_unlock` | `0x1800f9830` | `0x1800fc040` | | bytes |
| `_Mtx_do_lock` | `0x1800f9850` | `0x1800fc060` | | pdata |
| lock body | `0x1800f98c0` | `0x1800fc0d0` | | fn+off |

The invade, cancel and leave-world option actions and `show` move by `+0x20`. Code further into
the image moves by more: `+0x1140` for the item handler, `+0x1570` for `BuildLobbyKey`, `+0x2810`
for the mutex helpers. The shift is not uniform across the image.

`scripts/repin-ersc-prologues.py` had to rewrite only two prologue pins, invade and cancel. Both
changed only in the rel32 of the lock call.

### Other functions carried for the research scripts

These are from the address-sweep pass over `scripts/frida/*.js` and `scripts/*.py`.

| What | v2.0.1 | v2.0.2 | How |
|---|---|---|---|
| two other option actions (`ersc-action-trace.js`) | `0x24ef0`, `0x257d0` | `0x24f10`, `0x257f0` | pdata |
| option-action table, 19 entries (`ersc-option-actions.js`) | `0x25850` ... `0x26dc0` | each `+0x20` | pdata, all unique |
| invade request site (`ersc-cancel-latency.js`) | `0x38ab5` | `0x39255` | the call `0x74` bytes after the completion callback's `lea` |
| request call slot | `0x1f65b8` | `0x1f8a68` | that call's rip target |
| request completion | `0x95fe0` | `0x97590` | bytes, plus the `lea` in the request function |
| request emitter | `0x93be0` | `0x952a0` | the `lea rcx` at the request site, plus matching neighbours |
| menu accept (`ersc-menu-accept.js`) | `0x82600` | `0x83710` | pdata |
| grace grant (`undo-seamless-grace-grant.js`) | `0x86060` / `0x860f6` / `0x94210` | `0x87170` / `0x87206` / `0x95820` | pdata / fn+off (`cmp rsi,7`) / bytes |
| band tables (`seamless-band-tables.js`) | `0xa97b0` | `0xaad20` | pdata; same reads of `+0x70` and `+0x88` |
| `sha256_update` | `0xe29a0` | `0xe51b0` | pdata |
| `BuildLobbyKey` return site | `0xad911` | `0xaee81` | fn+off |
| session-state readers | `0x41edd`, `0x42974`, `0x429ef` | `0x4288d`, `0x43324`, `0x4339f` | fn+off |
| session-state writers | `0x25886`, `0x25806`, `0x24f26` | `0x258a6`, `0x25826`, `0x24f46` | fn+off |

## What did not change

- Session field group: `OSM+0x58` is the session, mutex at `session+0x100`, guard at
  `session+0x14c` with poison `0x7fffffff`, state at `session+0x150`.
- Session state codes: idle `0x01`, searching `0x0e`, cancelling `0x23`. A scan of every
  `mov dword [reg+0x150], imm32` (`ersc-disas.py states 0x150`) finds the same 18 sites with the
  same values in both builds.
- `BuildLobbyKey` reads its context at `+0xc8` in both builds (two uses each).
- The `BuildLobbyKey` inputs other than the salt (`settings+0xc8`, bit 2 of `+0x281`) read the same.
- The item handler's closure is still `0x40` bytes, `{vtable, captured object, handler, ...}`, with a
  pointer to itself at `+0x38`.

## What changed in layout

### The item handler reads its owner one qword further on

The handler's opening instructions in each build:

```
v2.0.1  0x18009283b  mov rsi, [rcx+0x58]     ; owner
        0x18009283f  mov rax, [rcx+0xc0]
v2.0.2  0x18009397b  mov rsi, [rcx+0x60]     ; owner
        0x18009397f  mov rax, [rcx+0xd0]
```

`rcx` is the captured object. Everything after that is the same, including `[rsi+0x58]` for the
session. So the captured-object-to-owner hop is `+0x60` in v2.0.2, while owner-to-session stays
`+0x58`. `er-invasion-warp` used `+0x58` for both hops, which is why its item-handler needle matched
0 closures for the whole run.

### The `seamless` tag is not at `OSM+0x68`

Read from the live menu object `0x4717d3c8` on run `br-20261027-160220-2e56`:

| Offset | Value |
|---|---|
| `+0x58` | `0x4717c790` (the session; confirmed) |
| `+0x60` | `0x4717fcf0` |
| `+0x68` | inline string bytes `alone` |
| `+0x78` | `5` (string size) |
| `+0x80` | `0xf` (string capacity) |

The string `seamless` is not in the first `0x200` bytes of the object. `OSM+0x68` is the session
password from `ersc_settings.ini` (`cooppassword`): with it changed from `alone` to `detoksi`, run
`br-20261027-163119-9946` read `detoksi`, size `7`, at `owner 0x469ad3c8 + 0x68`. `alone` is also the plaintext
of one of Seamless's AES-encrypted string objects (`0x469adeb0` in run `br-20261027-162035-2087`);
the others decrypt to its lobby-data key names (`ynx3_lobby_type`, `ynx3_lobby_preferences`,
`ersc_lobby_password_ynxssr_202...`, `ersc_lobby_breakin_lobby_ynxssr_202_6`). The mod reads this tag as a
diagnostic only, and its log line reports the mismatch as harmless.

### `.data` moved up by `0x2000`

The nine Steam interface slots moved from `0x21b570..0x21b670` to `0x21d570..0x21d670`, with the
same layout. `.data` itself starts at `0x21c000` (was `0x21a000`), and the rule byte moved from
`0x21ad70` to `0x21cd70`.

### The lobby-key salt changed by one byte

| | Address | Salt |
|---|---|---|
| v2.0.1 | `0x1801e20d8` | `2XfW3z/+eN0vdnEFP8pGxtoHpJ/4bxdC` |
| v2.0.2 | `0x1801e43b8` | `2XfW3z/+eN0vdnEFP8pGGtoHpJ/4bxdC` |

It was found by following `BuildLobbyKey`'s `movups` load (v2.0.1 `0x1800ad8b5`, v2.0.2
`0x1800aee25`). `BuildLobbyKey` (`0x1800aec50`) hashes B + A + this salt through its one
`sha256_update` call at `0x1800aee7c`, where A is bit 2 of `[settings+0x281]` written as `"0"`/`"1"`
and B is the output of `0x1800a6990` on the object at `[settings+0xc8]`. `settings` is the session
object itself: in run `br-20261027-174224-22a9` the call's first argument was `0x469ac790`, the
session the item-handler needle resolved in the same run.

The search filter is an exact match on that key: at `0x1800ada3e` Seamless calls
`ISteamMatchmaking` slot `+0x28` (`AddRequestLobbyListStringFilter`) with `"lobby_key"`, the
`BuildLobbyKey` output and comparison `0` (`k_ELobbyComparisonEqual`). The publish path at
`0x1800ada6e` writes the same key with slot `+0xa0` (`SetLobbyData`). The slot names and the
enum value are from the Steamworks header. A v2.0.1 and a v2.0.2 client with the same A and B
therefore hash different strings and filter for different keys.

Recomputed from live memory (read-only `/proc` reads of run `br-20261027-162035-2087`, same
character as run `br-20261027-160220-2e56`):

| | Value |
|---|---|
| B | `76DFB8C5A838F5A6`, the AES-128-ECB plaintext of the object at `0x469aea38` |
| A | `"0"`: `settings+0x281` read `0xd3`, bit 2 clear, by a Frida `Interceptor` on `BuildLobbyKey`'s entry (`scripts/frida/ersc-lobby-key-settings.js`, run `br-20261027-174224-22a9`) |
| sha256(B + A + v2.0.2 salt) | `add953e086980dc8badb4fb372a36f80ff230eb6dfad2759dccdf102cf4441da`, byte for byte the `lobby_key` run `br-20261027-160220-2e56` published on lobby `0x1860000603e87fc` |
| sha256(B + A + v2.0.1 salt) | `724e9aa8f1cdbc5b4f57d08ab6bda5a455d6e22fdae6b3b0185f0899e5054615` |

So for this world a v2.0.1 client filters for `724e9aa8...` and a v2.0.2 host advertises
`add953e0...`, and the equality filter cannot match one to the other.

## Live run (run `br-20261027-160220-2e56`)

Profile: `er_quickload`, `er_crash_logging`, `er_invasion_warp`, `er_seamless_bugfixes`, plus the
installed v2.0.2 `ersc.dll`. Character `Dex = sex`, in the world.

- At +3.5 s: `ersc.dll @0x180000000 recognised as Seamless Co-op v2.0.2 -- filter armed (show=+0x241c0
  invade=+0x25870 cancel=+0x258f0 lobby_key=+0xaec50, session state at S+0x150, idle=0x1
  searching=0xe cancelling=0x23)`.
- At +372 s, when the player opened their world: the mod captured Seamless's menu object
  `0x4717d3c8` with `build=2.0.2`, session `0x4717c790`, in state `1`.
- The session then went `0x01 -> 0x02 -> 0x03 -> 0x04 -> 0x06` within 0.15 s.
- Seamless created advertisement lobby `0x1860000603e87fc` and wrote its keys, including
  `lobby_key`. The mod observed each write and published its own `er_invasion_warp_map =
  m11_10_00_00` to the same lobby, reading it back.
- The item-handler needle matched 0 closures throughout.
- A read-only scan of the game's memory found one item-handler closure, `0x4706d470`, capturing
  `0x4717f410`. `+0x60` from it is the menu object `0x4717d3c8`, and `+0x58` from that is the
  session `0x4717c790`: the same two objects the mod logged.

Static checks run against the v2.0.2 image: the `er-invasion-warp` build script assembles every pin
and compares it to the DLL's bytes, and `cargo test -p er-invasion-warp -p er-invasion-warp-core`
passed 354 tests. `scripts/check-ersc-version-supported.py` reports the installed build supported.

## Where the code shifts come from

`.pdata` entries 0-248 have the same address and size in both builds. The first `+0x20` comes from
three functions that grew because a structure field moved from `+0x78` to `+0x80`, which no longer
fits an 8-bit displacement (each such instruction grows from 4 to 7 bytes). Every other change in
them is a rip-relative displacement.

| Function | Size v2.0.1 -> v2.0.2 | Shift after it | Change |
|---|---|---|---|
| `0x1e270` | `0xda` -> `0xe0` | 0 (absorbed by padding) | `[rbx+0x78]` -> `+0x80`, twice |
| `0x1e680` | `0x101b` -> `0x102b` | `+0x10` from `0x1f6a0` -> `0x1f6b0` | `[rax+0x78]` -> `+0x80`, a 13-byte alignment nop; indirect calls `[r8+0xa8]` -> `+0xb0`, `[r14+0x120]` -> `+0x128`, `[r14+0x128]` -> `+0x130` |
| `0x1f9c0` -> `0x1f9d0` | `0x14c` -> `0x152` | `+0x20` from `0x1fb10` -> `0x1fb30`, 95 entries through `show` | `[r14+0x78]` -> `+0x80`, twice |

Where the later shift values begin:

| Shift | First function | Cause |
|---|---|---|
| `+0x1140` | `0x918c0` -> `0x92a00` | `0x91580` -> `0x926b0` grew `0x33b` -> `0x348` |
| `+0x1570` | `0xa6e60` -> `0xa83d0` | `0xa6c50` -> `0xa81b0` grew `0x203` -> `0x213` |
| `+0x2810` | `0xd4c30` -> `0xd7440`, 2,916 matching entries from there | new function at v2.0.2 `0xd7400`, size `0x3a` |

New functions were inserted at v2.0.2 `0x28170` (5 functions), `0x6eeb0` (`0x33a`), `0x94cc0`
(6 functions) and `0xb96a0` (several `0x3a`/`0x37` stubs). `0x2a1e0`, which registers the option
actions, grew `0x10939` -> `0x10cb9`. v2.0.1 `0x940f0` and `0x95f80` have no v2.0.2 counterpart.

## The item handler's captured object grew by two qwords

Both registration `lea`s (`0x18007c423`, `0x18007d445`) sit in the same function: v2.0.1
`0x1800750a0` (size `0x902e`, `.pdata` 1174), v2.0.2 `0x180075ff0` (size `0x91e9`, 1189). It does
`mov rsi,rcx` on entry and stores `rsi` into the closure, so the captured object is its `this`.
Every `[rsi+X]` in that function:

| v2.0.1 offsets | v2.0.2 | Uses |
|---|---|---|
| `+0x10` ... `+0x88` | `+8` | `0x40`x10 -> `0x48`x10, `0x58`x5 -> `0x60`x5, `0x78`x7 -> `0x80`x7 |
| (none) | new `+0xa0` | 6 uses |
| `+0x98` ... `+0x1e0` | `+0x10` | `0xb0` -> `0xc0`, `0xe0`x103 -> `0xf0`x103, `0xe8`x151 -> `0xf8`x151, `0xf0`x103 -> `0x100`x103, `0x1d0` -> `0x1e0` |

So the owner `+0x58` is now `+0x60`, and the handler's `+0xd0` is v2.0.1's `+0xc0`: both `0xb0` and
`0xe0` move by exactly `0x10`, so nothing was inserted between them. The registering code shows the
owner move directly: `add r12,[rsi+0x58]` at `0x18007c3f9` -> `add rbx,[rsi+0x60]` at `0x18007d41b`.

## `frida-trace-ersc.py`'s hook target is v2.0.2 `0x926b0`

v2.0.1 `0x91580` and v2.0.2 `0x926b0` each have exactly one reference: `lea r8` at `0x18007b70f` /
`0x18007c73f`, in the same registering function. The masked body diff is displacements only. `call
[rsi+0x1d0]` -> `+0x1e0` and `[rsi+0x60]` -> `+0x68` are the captured-object growth above, not a
vtable change. The size growth (827 -> 840 bytes) is three `[rbx+0x78]` -> `+0x80` encodings (+3
each) and one `je` from short to near (+4).

## The needle after the fix

Run `br-20261027-162035-2087`, with `item_handler_owner_offset = 0x60`: `item-handler needle
0x180093960 matched 1 closure(s) naming 1 distinct session(s), first session 0x469ac790 owner
0x469ad3c8` at +10.48 s, then `session resolved from Seamless's item-handler closure` at +11.46 s,
with no dialog opened. v2.0.1 runs matched at 10 to 13 s, so Seamless builds the object at the same
point in boot on both builds.

## The packed (Themida) `ERSC` section

Compared between the decrypted runtime images: v2.0.1's archived
`vendor-archive/seamless/ersc-2.0.1.runtime.bin`, and v2.0.2's read out of run
`br-20261027-160220-2e56` through `/proc`, with no unreadable pages. Both are flat images with
file offset equal to rva.

| Name (script) | v2.0.1 | v2.0.2 | Evidence |
|---|---|---|---|
| `WRITER` (`ersc-option-actions.js`) | `0x2646e6` | `0x27172d` | One masked-shape match in each image: the VM handler that stores one VM register through another. Its context offsets (`+0xb2`, `+0xd7`, `+0x123`/`+0x125`, `+0x134`) are the same in both builds; the host registers changed from `rcx,rbx` to `rsi,rdi`. |
| `LEAD_RVA` | `0x24de00` | `0x292f4c` | One match in each image on a `0x48`-byte record (`0x24decc` -> `0x293018`, `+0x4514c`). Its code pointer moves `0x180093b70` -> `0x180095230`, the request emitter's `+0x16c0`. |
| Session slot (`session-slot-read.js`, `who-asks-steam-for-lobbies.js`) | `0xc64c88` | no equivalent | In v2.0.1 `0xc64c88` is the immediate of `movabs r12, 0x72cffd20`, which is added and then subtracted again (`sub r8, 0x72cffd20` at `0xc64ca2`): protector filler, not a slot. In the v2.0.2 image the live session pointer `0x4717c790` occurs exactly once, at `0x293000`, inside the `LEAD_RVA` block. |
| `PUBLISH_SITES` | `0x612d8a`, `0x623448`, `0x624289` | no static equivalent | Each is a VM re-entry stub starting with `pushfq`. The protector re-randomises them every build (2661 stubs in v2.0.1, 2718 in v2.0.2), and the masked shapes match 0-2 times in v2.0.2, every candidate differing at byte `0x14`. They came from a `SetLobbyData` return-address hook and are found the same way. |
| `ersc-unwind.py` frame pc | `0x28a85b` | `0x2ece55` | One masked-shape match in each: three `push [rsp]; push [rsp]; mov r,[rsp]; add rsp,8` groups, `call [rax+0x10]`, `push imm32`. Inside the blanket entry `0x242000..0x325f12` (unwind `0xd23974`). |
| `ersc-unwind.py` frame pc | `0x636e75` | no static equivalent | Its unwind record (v2.0.1 `0x214460`, v2.0.2 `0x216a88`, identical bytes) is shared by 369 protector fragments in each build, and 104 of the 369 end the same way. |
| `ersc-unwind.py` ThrowInfo | `0x201658` | `0x203b08` | `.?AVsystem_error@std@@` (type descriptor `0x21b710` -> `0x21d710`) -> its one catchable type (`0x201730` -> `0x203be0`) -> its one array (`0x201640` -> `0x203af0`) -> its one ThrowInfo. `--throwinfo 0x203b08` parses the same 4 types as v2.0.1. |

The unreadable unwind record is `0x2522c` in v2.0.2.
