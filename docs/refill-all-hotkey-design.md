# Refill-all hotkey: how it can be built

A new DLL that, on one configurable combination, marks **every** refillable item as auto-refill,
and on the next press marks them all no-refill, cycling forever. Config lives in a TOML in the game
directory and is hot-reloaded.

Everything in "What the game gives us" is static RE against 1.16.2 (`dump == deobf == runtime`,
shift 0) plus an offline read of the installed `regulation.bin`. **None of it is runtime-validated
yet.** The two ambushes are in §1 and §2; both are the kind that ship as "the feature half-works".

---

## 1. The obvious function is a toggle, not a setter

`SetItemReplenishState` (`0x140786430`) is what the storage-box UI calls. It does this:

```c
bVar2 = ShouldReplenishItem(tracker, itemId);
SetState(tracker, itemId, !bVar2);          // <-- flips, does not set
```

Looping it over every item **scatters** states — each item lands on the opposite of whatever it
was. It never produces "all on". It also `DLPanic`s when `GLOBAL_CSMenuMan` is null, which is a
crash waiting for anyone calling it outside a menu.

The real primitive is one level down:

```c
CS::ItemReplenishStateTracker::SetState(tracker*, int* itemId, bool state)   // 0x14023dd80
```

It is an absolute setter, it is idempotent, and it **self-filters**: its first act is
`GetEquipParamReplenishType(itemId)` with an early return on `None`. Passing an ineligible id is a
safe no-op, so the caller needs no eligibility table of its own. It also skips the `CSMenuMan`
check — so the caller must do its own `GameDataMan` / tracker null checks instead.

## 2. The tracker is a fixed 2048-entry vector that crashes the game on overflow

`CS::EquipGameData::ItemReplenishStateTracker.entries` is `ItemReplenishStateEntry[2048]`
(8 bytes each: `ItemId` i32, `autoReplenish` bool). **Both** insertion paths —
`InsertSorted` (`0x14023df20`) and the append path (`0x14023e270`) — carry the same guard:

```c
if (0x800 < param_1->count + 1U) {
    DLPanic(".../DLFixedVector.inl", ..., "out of memory.", ...);   // does not return
}
```

That is a hard ceiling on any "mark everything" feature. It is the single reason this design needs
a census rather than a loop over all items.

### It fits — 449 eligible rows against 2048

`scripts/regulation-autoreplenish-census.py` (added on this branch) decrypts the installed
`regulation.bin` with nothing but `python3` + `openssl` and counts the rows the game deems eligible:

| Param | Rows | Eligible | Breakdown |
|---|---|---|---|
| `EquipParamWeapon` | 3554 | **71** | type 2 ×71 (the ammunition rows) |
| `EquipParamGoods` | 2326 | **378** | type 1 ×72, type 2 ×306 |
| **Total** | | **449** | 1599 entries of headroom |

Re-run it against a modded `regulation.bin` before assuming the number holds.

## 3. Eligibility is two bytes

`GetEquipParamReplenishType` (`0x14023de20`) branches on the item id's **high nibble only**:

| High nibble | Table | `autoReplenishType` offset |
|---|---|---|
| `0x0` | `EquipParamWeapon` | `+0x197` |
| `0x4` | `EquipParamGoods` | `+0x6e` |
| anything else | — | `None` |

Goods ids are `0x40000000 | rowId`.

## 4. Defaults are not uniform, which decides what each half of the cycle writes

`ShouldReplenishItem` (`0x14023d990`), when **no entry exists** for an item, returns
`type == Consumable(2)`. So:

- type 2 (306 goods + 71 weapons) defaults **ON**
- type 1 (72 goods) defaults **OFF**

The consequence that matters: **"all off" cannot be implemented by clearing the tracker.** Emptying
it reverts every type-2 item to ON — the opposite of what the player pressed for. All-off needs
explicit `false` entries written.

## 5. Where the tracker lives, and what is already typed

`PlayerGameData +0x2b0` (`equipGameData`, by value) `+0x338` (`itemReplenishStateTracker*`) =
**`+0x5e8`**. That matches the constant already in `crates/er-better-refills-dll` on
`better-refills-decoupled`.

`fromsoftware-rs` already types the whole structure —
`crates/eldenring/src/cs/player_game_data.rs`:

```rust
pub struct ItemReplenishStateTracker {
    entries: [ItemReplenishStateEntry; 2048],   // independent confirmation of the 2048 cap
    pub count: u64,
    ...
}
impl ItemReplenishStateTracker {
    pub fn entries_mut(&mut self) -> &mut [ItemReplenishStateEntry] { ... }
}
```

reached through `EquipGameData.item_replenish_state_tracker: Option<OwnedPtr<...>>`.

And `SoloParamRepository::rows()` — already used by `er-build-import-runtime/src/catalog.rs` —
enumerates `EquipParamGoods` / `EquipParamWeapon` live with `.auto_replenish_type()`. **So the DLL
needs no embedded id table and stays correct under a modded regulation.**

### The write strategy this implies

Two passes, because they have different risk:

1. **Flip what is already there** via `entries_mut()` — set `auto_replenish` on existing entries.
   No insert, no capacity risk, and it does not disturb the sort order that `FindItem`
   binary-searches.
2. **Insert only what is missing** via native `SetState`, which maintains the sorted invariant.
   Bounded by the 449 census.

Marking state alone moves no items. The actual transfer is still `ReplanishItemsFromChest`
(`0x14024dff0`), which `er-better-refills-dll` already calls directly — so an "apply now" option is
a call away rather than a wait for the next grace.

## 6. Which direction a press goes

Do **not** keep the cycle state in a DLL-local `bool`. It desyncs the moment the player toggles an
item in the storage UI, reloads a save, or alt-tabs into a different character. Derive it from the
tracker each press:

> If **every** eligible item is already ON → this press turns everything OFF. Otherwise → this
> press turns everything ON.

From any mixed state the first press is always "turn everything on", which is the predictable
behaviour, and the cycle is self-correcting rather than drifting.

---

## 7. The input half is the actual gap

`er-hotkey-config` — the shared owner of key names and hot reload — is **keyboard-only**:

```rust
pub struct Chord {
    pub modifiers: u8,          // ctrl / alt / shift ONLY
    pub vk: VirtualKey,
    pub dik: Option<Scancode>,
}
```

No mouse button in its key table, no gamepad concept anywhere in the crate. Binding `"mouse4"` or
`"LB+RB"` today produces a parse rejection, not a working chord. Both halves need adding.

### Mouse — a small extension of the same poll

`GetAsyncKeyState` already reports `VK_LBUTTON 0x01`, `VK_RBUTTON 0x02`, `VK_MBUTTON 0x04`,
`VK_XBUTTON1 0x05`, `VK_XBUTTON2 0x06`. Add them to the key table with `dik: None`. Two caveats:

- A key with no scancode has no byte in the game's DirectInput buffer, so a mouse binding can never
  be **suppressed** from the game. Bind the side buttons — LMB/RMB would also swing your weapon.
- Whether Wine/Proton reports the X buttons through `GetAsyncKeyState` at all is **unproven**.
  Verify before shipping it as a default.

### Controller — a different source, template already in-repo

`crates/er-save-picker-core/src/overlay.rs` already does it: `resolve_xinput_get_state()` walks
`xinput1_4` / `xinput1_3` / `xinput9_1_0` via `GetModuleHandleA` (the game already loaded one, so no
`LoadLibrary`) and reads controller 0. Button bits live in `er-title-flow/src/constants_moved.rs`.

Two differences from the keyboard path:

- **XInput has no "pressed since last call" bit.** A pad chord must edge-detect against the previous
  poll's `wButtons`.
- A pad chord is a `wButtons` **mask** (`LB+RB+dpad_up`), not one trigger plus three modifiers — it
  needs its own type alongside `Chord`, not a field bolted onto it.

### The thread rule that breaks background pollers

**`GetAsyncKeyState` does not report the user's keys from a background thread under Wine/Proton.**
Measured in `overlay.rs`: a dedicated poll thread ran 1089 polls, saw 5 key-downs while the user
mashed, and completed 0 picks. Poll from the **game task thread** (what
`er-invasion-warp/src/drive.rs` does) or the Present hook — never a private `std::thread`.

### Rebinding must reset edge state — twice

`er-invasion-warp/src/drive.rs` `KeyEdge::rebind()` is the template, and its second reset is the
load-bearing one:

```rust
self.was_down = false;                     // the local latch, about the OLD key
let _ = unsafe { GetAsyncKeyState(vkey) }; // DISCARDED read: the OS low bit has been
                                           // accumulating on the NEW key since process start
```

Skip the discarded read and every rebind fires once, instantly, on a key nobody pressed.

---

## 8. Proposed config

`er-refill-all.toml`, game directory, hot-reloaded by `er_hotkey_config::reload::HotFile` — which
compares **file text, not mtime** (mtime has one-second resolution on the filesystems a Wine prefix
sits on, and a re-save moves it without changing anything).

```toml
# Any of these fires the cycle. Leave a value empty to disable that input.
hotkey         = "ctrl+shift+r"   # keyboard chord
mouse_hotkey   = "mouse4"         # XBUTTON1 -- side button; LMB/RMB would also attack
gamepad_hotkey = "lb+rb+dpad_up"  # wButtons mask, edge-detected

# Call ReplanishItemsFromChest right after marking, instead of waiting for the next grace/load.
refill_immediately = true
```

A malformed value must **keep the last working binding** — not the shipped default (which would
drag the player back onto the collision they escaped) and not nothing. And a rejection is not a
change: counting it as one makes a config with a permanent typo manufacture a phantom press on
every reload, forever. `er_hotkey_config::binding::Binding` already encodes this.

**Check for a hotkey collision at load.** `GetAsyncKeyState`'s low bit is consumed by reading it, so
two pollers on one key eat each other's edge intermittently. `er-invasion-warp` logs a warning when
two of its five keys collide; this DLL shares a process with that one.

---

## 9. What is still unproven

- No runtime validation of any of it — no live `SetState` call, no observed refill.
- The 449 census is the **stock** regulation; a modded one changes it, and 2048 is a crash.
- Mouse X-buttons through `GetAsyncKeyState` under Proton: untested.
- Whether the tracker's 449 entries serialise cleanly into the save file at that size.
- Whether writing all 449 in one frame is cheap enough not to hitch.
