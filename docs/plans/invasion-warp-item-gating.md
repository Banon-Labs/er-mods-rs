# Gating the Challenger's Lynchpin while an invasion is live

Investigation only. No product source was edited, nothing was built, no game was launched, no
runtime probe was run. Everything below is static reading of this repo plus one bounded query
against the named 1.16.2 Ghidra dump on `localhost:8765`, plus `bd` memories.

Two user reports, treated as one problem:

* **Item 7** -- "don't allow them to use the fast invasion item in an invasion."
* **Item 9** -- "when using the item to relocate while in an invasion, the thing bugged out."

Every claim is tagged **VERIFIED** (read out of code or the dump in this session) or **HYPOTHESIS**
(needs a live run to settle).

---

## 1. What the item is, and what using it does today

The item is Seamless Co-op's **Challenger's Lynchpin**, goods id `8380003` / `0x7fde63`, spelled
`0x407fde63` with the goods-category nibble when the inventory keys on it.

| constant | file:line |
| --- | --- |
| `LYNCHPIN_ITEM_ID = 0x407f_de63` | `crates/er-invasion-warp/src/lynchpin_use.rs:91` |
| `LYNCHPIN_GOODS_ID = 0x7f_de63` | `crates/er-invasion-warp/src/lynchpin_use.rs:94` |

**VERIFIED** (bd `goods-use-anim-is-equipparamgoods-0x42-lynchpin-is-runtime-synthesised-2026-09-09`,
bd `ersc-goods-handler-0xa96e0-and-its-registry-2026-09-09`): the row is not in `regulation.bin` at
all. `ersc.dll` allocates a 0xb0-byte `EquipParamGoods` row at init, stamps `goodsUseAnim = 0x42`
into it, and registers a handler at `ersc+0xa96e0` against the goods id. That handler is
`jmp 0x180500d6f`, straight into the Themida-packed section, so what the item *does* is not
statically readable. What is readable is that using it opens an ERSC option menu whose rows are
`OPTIONSELECT_BREAKINWORLD` ("Invade world as a wanderer"),
`OPTIONSELECT_RAPIDREENTRYRBREAKINWORLD` ("Seek opponent" -- the **relocate** row item 9 is about)
and, in non-idle states, `OPTIONSELECT_LEAVEWORLD`.

### 1.1 This DLL's three interventions on the item

`crates/er-invasion-warp/src/lynchpin_use.rs:691-714` (`tick`, driven every frame from
`crates/er-invasion-warp/src/lib.rs:255`) does three idempotent things:

1. `shorten_use_animation()` (`lynchpin_use.rs:203-231`) -- one byte written once per session into
   the live row at `row+0x42`, `66 -> 17`, shortening the use animation from 5.000s to 1.433s.
2. `install_popup_skip()` (`lynchpin_use.rs:451-526`) -- a **bare `MhHook`** (deliberately not the
   `er-hook` union) on `CS::CSMenuMan::OpenConversationChoicesMenu`, rva `0x00e9_e4f0`
   (`lynchpin_use.rs:111-118`). The detour entry is a naked shim
   (`open_choices_entry`, `lynchpin_use.rs:285-292`) whose first instruction stores `r14` into
   `SHOW_R14`, because ersc's `show` keeps `lea r14,[rcx+0x120]` pointing into its own option-menu
   object.
3. `drive_pinned_use()` (`lynchpin_use.rs:621-680`) -- only runs when something called
   `request_use()` (`lynchpin_use.rs:536-548`); not on the player's own use.

### 1.2 The decision path on a player's own use -- quoted, because it is the whole of item 7

`crates/er-invasion-warp/src/lynchpin_use.rs:343-443`, in order:

```rust
let menu_object = SHOW_R14.load(Ordering::SeqCst).wrapping_sub(SHOW_R14_INTERIOR_OFFSET);
let adopted = crate::local_invasion_filter::menu_object::adopt_menu_object(menu_object);
if crate::local_invasion_filter::auto_search_armed() {          // :361
    crate::local_invasion_filter::stand_down_auto_search();
    ... return original(dialog, ..);                            // pass through
}
let using = unsafe { item_in_use() };                           // :372
if using != Some(LYNCHPIN_ITEM_ID) {                            // :373
    ... return original(dialog, ..);                            // pass through
}
let (idle, source) = crate::local_invasion_filter::popup_skip_gate_is_idle();   // :390
if idle {                                                       // :391
    POPUPS_SKIPPED.fetch_add(1, Ordering::SeqCst);
    let started = if adopted {
        crate::local_invasion_filter::drive_invade_with_owner(menu_object, "the Lynchpin's own use")
    } else {
        crate::local_invasion_filter::drive_invade_inline("the Lynchpin's own use")
    };
    ...
    return 0;                                                   // :425  dialog never built
}
... return original(dialog, ..);                                // :437-442 pass through
```

So the state the code consults before acting is exactly three things:

* whether this DLL's own auto re-search loop is armed (`AUTO_SEARCH_ARMED`);
* which item raised the menu, read from `CSMenuGaitemUseState+0xc` (`item_in_use`,
  `lynchpin_use.rs:309-335`);
* whether a Seamless session reads **idle**, via `popup_skip_gate_is_idle()`.

**There is no check anywhere on this path for "the player is already in an invasion."** The idle
test is a proxy for it, and section 3 shows where the proxy fails.

---

## 2. The state vocabulary -- what "in an invasion" reads as

**VERIFIED**, `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs:199-202`, Seamless v2.0.1:

| name | value | meaning |
| --- | --- | --- |
| `state_idle` | `0x01` | nothing in flight; the only state `invade` will start from |
| `state_searching` | `0x0e` | the invade action wrote it; one write site in the whole plaintext `.text` |
| `state_offer_received` | `0x13` | first step past the fast-fail path |
| `state_cancelling` | `0x23` | the cancel action wrote it |

Two more states matter and are **not** in that table:

* `0x0f`, `0x10`, `0x12` -- the connecting steps. `CANCEL_ROW_VISIBLE_STATES = [0x0e, 0x0f, 0x10,
  0x12]` (`crates/er-invasion-warp/src/local_invasion_filter/lock_report.rs:231`), taken from ERSC's
  own hide predicate at `ersc+0x26b40`.
* **`0x16` -- being in an invasion.** This is the decisive fact for both items, and the repo states
  it outright:
  * `crates/er-invasion-warp/src/local_invasion_filter.rs:3036` --
    "`0x16` is being in an invasion, and its clock is how long the player has been fighting."
  * `crates/er-invasion-warp/src/local_invasion_filter.rs:2631-2633` --
    "`0x16` is a state the real session genuinely holds for the length of an invasion -- Frida saw
    the same session enter the `0x24` writer with `state_on_entry=0x16`."
  * `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs:221` -- the leave-world row's hide
    predicate `ersc+0x26ac0` is `hide = (state == 1)`, so the row is drawn in every state but idle,
    "including `0x16`, where the Cancel row's predicate at `ersc+0x26b40` hides it and a player is
    otherwise stranded."

### 2.1 Does an in-invasion oracle already exist? Partly. Three candidates, none wired as a gate.

**(a) The ERSC session state itself.** `read_session_state(abi, session)`
(`local_invasion_filter.rs:1128-1132`). A live invasion reads `0x16`. Cost: one dword read. This is
the cheapest oracle and it already exists -- **VERIFIED** -- but nothing asks it the question "am I
in an invasion", only "is it idle".

**(b) `session+0x1d8`, the host's Steam id.** `SESSION_HOST_STEAM_ID_OFFSET`
(`local_invasion_filter.rs:1888-1906`), with `host_steam_id()` at `:1925-1949`. The doc records the
measurement from run `br-20260910-042516-3b5d`: "entering state `0x16` writes `+0x1d0` and `+0x1d8`
in one step, and the return to idle zeroes both." A non-zero `+0x1d8` is therefore an independent
"in an invasion, and here is whose world" signal. **VERIFIED as recorded**; it is a corroborator for
(a), not a replacement, because it shares the same session pointer.

**(c) The engine side, `CSSessionManagerImp`.** `lobbyState` at `+0x0c`, `protocolState` at `+0x10`
(`crates/er-invasion-warp-core/src/join_progress.rs:62-94`; the offsets are re-declared in
`crates/er-invasion-warp-core/src/warp.rs:100-115`). `lobby_state::CLIENT = 6` means "the join
succeeded -- the P2P session exists"; `protocol_state::IN_GAME = 6` means the world load finished.
**VERIFIED** as read. But in Seamless the same pair describes an ordinary co-op guest, so it says "I
am a guest in someone else's world", not "I am invading" -- it is the right *conservative* reading
for a refusal and the wrong one for a message that names invasion.

**What does NOT exist anywhere in this repo:** any oracle for the **host being invaded**. No
team-type, chr-type, invader-count, or break-in-manager read exists in `crates/`; a sweep for
`team_type|invader|chr_type|CSNetMan|Multiplay` finds only prose and one unrelated
`PlayerGameData.chr_type` offset assertion. The repo also records that the game-side route was
abandoned: `local_invasion_filter.rs:1910-1917` -- an RTTI sweep of the live process "found no
`CSBreakInManager` class in this build at all"; the break-in state lives in
`FNBreakInImpl@FromNet`, reached through `FNClientImpl` at `CSNetMan+0x00`.

### 2.2 If a host-side oracle is wanted, this is the cheapest static source

Do **not** start at `CSNetMan` -- the repo already burned a run there. Start at `ChrType`, which is
already in the dependency graph and needs no new RE to *read*:

* `../fromsoftware-rs/crates/eldenring/src/cs/chr_ins.rs` defines
  `#[repr(i32)] enum ChrType { None = -1, Local = 0, WhitePhantom = 1, Duelist = 2, ...,
  BloodyFinger = 15, Recusant = 16, BluePhantom = 17, FesteringBloodyFinger = 18, ... }`.
* `crates/er-game-base/src/pgd.rs:140` already pins its position:
  `const _: () = assert!(core::mem::offset_of!(PlayerGameData, chr_type) == 0x98);`
* `WorldChrMan` exposes `main_player: Option<OwnedPtr<PlayerIns>>` and
  `player_chr_set: ChrSet<PlayerIns>` ("ChrSet holding the players"), so both "what am I" and "who
  else is in my world" are one walk apart.

So the **invader** side is `main_player.player_game_data.chr_type != Local` and the **host** side is
"some entry in `player_chr_set` other than me has an invader `chr_type`".

**HYPOTHESIS, needs one live read:** that Seamless populates `chr_type` with the invader values at
all. Seamless runs its own invasion system and hooks the spawn resolver
(`crates/er-invasion-warp/src/seamless_probe.rs:1-12`), so it may or may not go through the vanilla
role assignment. Until that is measured, treat `ChrType` as unproven and gate on the ERSC session
state, which is measured.

The Ghidra route if it has to be reverse-engineered: the 1.16.2 dump on `:8765` is the only **named**
image (`getDecompiledCode` / `searchFunctionsByName`); the 1.17 dump on `:8767` has structure and no
names; the installed game is **1.17.1**, so an address from either dump must be carried with
`scripts/map-rvas-1162-to-1170.py` then `scripts/map-rvas-1170-to-1171.py` and byte-checked against
`eldenring-deobf-1.17.1.bin` before anything calls it.

---

## 3. Item 7 verdict -- the gate is missing, and the proxy that stands in for it can be wrong

**VERIFIED.** Today the item-use path refuses to skip the popup only when
`popup_skip_gate_is_idle()` answers false. That function
(`crates/er-invasion-warp/src/local_invasion_filter.rs:2167-2198`) has two sources:

1. **Strong:** `menu_object_session_is_idle()` (`:2113-2128`) -- reads `OSM+0x58 -> session+0x150`
   with no scan, no cache. In a live invasion that reads `0x16`, so `idle == false`, the dialog is
   let through, and the player gets ERSC's real menu including the leave-world row. **Correct.**
2. **Weak fallback, used whenever `OSM == 0`:** `session_is_idle()` (`:2216-2221`), which goes
   through `resolve_session()` and the writable-data scan.

`OSM` is set by `capture_osm` (`menu_object.rs:29-55`), which the item path reaches through
`adopt_menu_object(SHOW_R14 - 0x120)` at `lynchpin_use.rs:350` -- so the strong path arms itself on
the **first** dialog of the process and not before. Before that first dialog, and whenever the
register capture is refused (measured returning `0x1` on run `br-20260910-000334-453e`,
`lynchpin_use.rs:473-477`), the gate is the scan.

The scan has false-positived at least six times, all recorded in-repo
(`local_invasion_filter.rs:1171-1177`, `:2600-2605`): `0x3dfadb`, `0x860f90f8`, `0x451200`,
`0x45e00cb0`, `0xa2760038`, `0xd2c0038`, each sitting at `0x01` -- which **is** `state_idle` -- for
an entire run.

### 3.1 The failure that produces item 9's symptom, mechanically

**VERIFIED as a code path; HYPOTHESIS that it is the run the user hit.** With a look-alike latched,
a player who uses the Lynchpin *while in an invasion* gets:

* `popup_skip_gate_is_idle()` answers `true` (the look-alike reads `0x01`);
* `lynchpin_use.rs:391` takes the skip branch -- the dialog is **never built**;
* `drive_invade_with_owner(menu_object, ..)` re-reads the **real** session through the menu object
  and finds `0x16`, so `read_session_state(abi, session) != Some(abi.state_idle)` at
  `actions.rs:502` returns `false`;
* `lynchpin_use.rs:425` returns `0` regardless.

Net result: the item animation plays, the item is consumed, **no menu appears**, no invasion starts,
and the one row that lets the player out of the invasion (`OPTIONSELECT_LEAVEWORLD`) is swallowed
with it. That is the same class of harm the module's own doc says it was rebuilt to avoid --
`lynchpin_use.rs:41-52`: the earlier `CloseMenu(-1)` version "trapped the player in an invasion with
no way out" (bd `auto-dismiss-must-be-scoped-not-every-dialog-2026-09-09`). The current design
reintroduces it through a different door: not by dismissing the dialog, but by declining to open one
on a gate that can be wrong.

### 3.2 What the gate should be, and where it belongs

The rule the user asked for is **"not while I am in an invasion"**, and the honest reading of that is
not "the session is not idle" -- it is "the session is in an in-world state". Concretely:

```
in_invasion := session state is 0x16
            OR session+0x1d8 (host steam id) is non-zero
```

with a **fail-closed** default: if no session can be read at all, treat the question as unanswered
and **pass the dialog through** rather than skipping it. Passing through is the harmless direction;
skipping is the direction that strands a player.

**Where it belongs:** one function in `local_invasion_filter`, beside
`popup_skip_gate_is_idle`, called from `open_choices_hook` **before** the idle gate at
`lynchpin_use.rs:390`. It must read through the menu object first (the pointer Seamless itself just
handed over, via `SHOW_R14 - 0x120`), and fall back to `resolve_session()` only for the answer
"unknown", never for the answer "idle".

It must **not** be placed inside `drive_invade_*`: those already refuse on non-idle, and by the time
they refuse the dialog has already been swallowed.

---

## 4. Item 9 verdict -- ranked failure modes for "relocate while in an invasion bugged out"

"Relocate" has two distinct readings and the plan has to cover both, because the user's phrase does
not distinguish them.

### Reading A -- "relocate" = the item's own "Seek opponent" row

`OPTIONSELECT_RAPIDREENTRYRBREAKINWORLD`, Seamless's own repeat-invasion row
(bd `seamless-invasion-trigger-is-its-own-menu-item-not-the-finger-2026-08-04`). Its outcome is
readable at `CSGameMan+0xafc`, `shouldUseRapidReentry`
(`crates/er-invasion-warp-core/src/seamless_invade_probe.rs:47-48`, getter `ShouldUseRapidReentry`
`0x14067a150` on 1.16.2).

### Reading B -- "relocate" = this mod's own map-pin / hotkey warp

`request_invasion_warp` (`crates/er-invasion-warp-core/src/warp.rs:455-610`), reached from the
world-map confirm hook (`crates/er-invasion-warp/src/map_confirm.rs:93`) and from F7/F8/F9
(`crates/er-invasion-warp/src/drive.rs:519-525`).

### The ranked list

**#1 (highest confidence) -- the warp gate is OPEN during a live invasion, because the session
cannot be re-identified at `0x16`. VERIFIED from code.**

The chain, each link read this session:

* `identifies_a_session` accepts a candidate only if its state is one of four codes
  (`local_invasion_filter.rs:1203-1206`):
  ```rust
  state == abi.state_idle
      || state == abi.state_searching
      || state == abi.state_cancelling
      || state == abi.state_offer_received
  ```
  `0x16` is **not** in that set.
* The **retention** check uses the same function:
  `crates/er-invasion-warp/src/local_invasion_filter/session_scan.rs:252` --
  `let cached = (session != 0 && identifies_a_session(abi, session, false))`. So a correctly-found
  session is **discarded the moment the player enters an invasion**.
* `resolve_session()` then returns `Err(SessionNotIdentified)` (`local_invasion_filter.rs:818`)
  whenever `OSM == 0`.
* The filter tick's early return publishes the gate as **false**
  (`local_invasion_filter.rs:2560-2566`):
  ```rust
  let Ok(session) = resolve_session() else {
      er_invasion_warp_core::warp::set_invasion_attempt_in_flight(false);
      return;
  };
  publish_invasion_attempt_state(session);
  ```
* `invasion_warp_policy()` therefore returns `Warpable`
  (`crates/er-invasion-warp-core/src/warp.rs:303-309`), and `request_invasion_warp`'s only refusal
  (`warp.rs:462-464`) does not fire.

So the mod will happily perform a full map warp -- session re-entry, block change, explicit spawn,
stage kick -- while the player is inside someone else's world. The `OSM != 0` branch of
`resolve_session` (`:845-848`) only range-checks the state, so this window closes once the player has
opened an ERSC dialog at least once in the process; it is wide open before that, and **always** open
for a host who is being invaded and never touched the item.

**#2 -- `SetupMapReentry` kicks session players, and can call `LeaveSession`, when the local player
is the host. VERIFIED against the named 1.16.2 dump.**

`warp.rs:487` calls `setup_map_reentry_if_in_game` (`warp.rs:718-753`), which calls
`CS::CSSessionManagerImp::SetupMapReentry` whenever `protocolState == InGame`. Decompiled from
`localhost:8765`, `getDecompiledCode 140cafc30`:

```c
void CS::CSSessionManagerImp::SetupMapReentry(CSSessionManagerImp *param_1, bool inCeremony)
{
  param_1->protocolState = WaitReentryToMap;
  param_1->allowMapReentry = inCeremony;
  if (param_1->lobbyState == Host) {
    iVar5 = 0;
    for (pSVar4 = sessionPlayers.begin; pSVar4 != sessionPlayers.end; pSVar4++) {
      if (pSVar4->joinWait != false) {
        GetSteamID(&pSVar4->base, &local_res18);
        pSVar3 = GetSessionManager(...);
        (*(code *)pSVar3->vfptr[4])(pSVar3, param_1->field1_0x8, local_res8, 0, uVar6);
        iVar5++;
      }
    }
    if (iVar5 == (sessionPlayers.size - 1)) {
      CSSessionManager::LeaveSession(param_1);
    }
  }
}
```

Two consequences. As a **guest/invader** it sets `protocolState = WaitReentryToMap` and nothing else
-- survivable, but self-latching, which `warp.rs:104-110` already documents: a second warp then sees
`7`, skips the re-entry entirely, and issues a map load with no session re-entry behind it. As a
**host** it walks the session player list, calls a session-manager vtable slot per waiting player,
and can end in `LeaveSession` -- i.e. the warp tears the multiplayer session down. Either way this is
vanilla behaviour for a vanilla fast travel; what is not vanilla is doing it from a map pin the mod
drew, at a moment the mod believes no attempt is in flight.

The address is the **1.16.2** RVA (`SETUP_MAP_REENTRY_RVA = 0xca_fc30`, `warp.rs:81`); the installed
game is **1.17.1**, so the call itself goes through `game_call_or_err` and can legitimately come back
`AddressUnavailable`. That is a separate question from the semantics above, which do not change.

**#3 -- a warp mid-invasion blinds the filter's own recovery machinery. VERIFIED from code.**

`WarpNextStageKick_` sets `GameMan::callForWarp` (`join_progress.rs:40-45`). `JoinProgress::verdict()`
returns `Verdict::Committed` on `call_for_warp || protocol_state == WAIT_REENTRY_TO_MAP`
(`join_progress.rs:140-142`), and `Committed` is the "hands off" verdict -- so
`drop_a_match_the_engine_has_already_failed` (`local_invasion_filter.rs:2764-2767`) returns early and
`resume_the_hunt_if_an_accepted_join_died` never fires for the duration. The module's own comment
already warns about this coupling in the other direction
(`local_invasion_filter.rs:2818-2820`): "`call_for_warp` is tempting and WRONG: `WarpNextStageKick_`
runs for every warp including a plain fast travel."

**#4 -- the dead-match dropper can cancel a relocate in progress. HYPOTHESIS.**

`drop_a_match_the_engine_has_already_failed` (`local_invasion_filter.rs:2750-2804`) drives a cancel
when ERSC claims a non-idle state while the engine reads `Verdict::Idle` -- `lobbyState == NONE`, no
RPC, both timers clear -- for longer than `TORN_DOWN_GRACE_MS = 8_000` (`:1477`). A "Seek opponent"
relocate necessarily tears the old P2P session down before the new one exists, which is exactly that
reading. If the gap exceeds 8s, the mod cancels the relocate the player asked for. Discriminating
evidence: a `join-progress` trace across a relocate showing how long `lobby=0` persists.

**#5 -- the popup is swallowed so the relocate row is never offered. VERIFIED as a path** (section
3.1), **HYPOTHESIS as the user's run.** The distinguishing log line is
`lynchpin_use.rs:419-424`: `"skipped Seamless's start-a-search popup and started the search inline
(started=false, ...) -- gate answered from the filter's own session (no menu object seen ...)"`.
`started=false` with a skip is the signature: the dialog was suppressed and nothing replaced it.

### Evidence that discriminates between them

All of it is already written by the DLL; the run just has to be read.

| reading | tells you |
| --- | --- |
| `local-invasion: session fields changed at state 0x16` / the `0x16` cooldown line | the session really is in-invasion, and the mod can see it |
| `map-confirm: invasion pin entity_id=... -> LOCAL warp ... session gate: ...` | a warp fired mid-invasion, and which `SessionGate` arm it took (#1, #2) |
| `map-confirm: ... REFUSED: an invasion attempt is in flight` | the gate worked -- so #1 is not this run |
| `join-progress: ersc=0x16 lobby=.. proto=.. warp=1 -> Committed` | #3, and the window for #4 |
| `lynchpin: skipped ... (started=false ...)` | #5 |
| `local-invasion: dropping a match the engine has no session for (#N)` | #4 fired |

---

## 5. One gate or two?

**One gate, in front of the item-use path, plus one correction to the warp latch.** They are not the
same edit and neither subsumes the other:

* Item 7 is a **policy** on the item: refuse the fast-invasion action while in an invasion.
* Item 9 is a **correctness bug** in the warp latch: `invasion_attempt_in_flight` currently publishes
  `false` in exactly the situation it exists to detect. Fixing the item-use gate does nothing for a
  map-pin warp, and fixing the latch does nothing for a swallowed dialog.

They share one new primitive -- `in_invasion()` -- and that is the thing to build once.

---

## 6. Silent refusal or a message?

**A message, and only on the item path.**

* The item path already costs the player an item use and an animation. A silent refusal there is
  indistinguishable from the mod being broken, which is precisely how the current swallow reads.
* The warp path already has the refusal wording and already shows nothing: `map_confirm.rs:115-121`
  logs `REFUSED` and leaves the map open. Adding a banner there is a separate, smaller question.

The surface is `crate::announce::show(&str) -> bool`
(`crates/er-invasion-warp/src/announce.rs:487`), the game's own auto-closing "Grace discovered"
notice -- **not** `showPopupMenu`, which `banner.rs:151-155` records as having given the user a modal
to dismiss per rejection and held the session open long enough to trip the stall watchdog.

Hard constraints, both measured and both in-repo:

* `MAX_CHARS = 96` (`announce.rs:176`) -- longer text is truncated.
* The field is `NOTICE_FIELD_WIDTH_PX = 1728` (`announce.rs:161`) and a message measuring 1729px was
  "placed successfully and never rendered" (`banner.rs:199-201`). Keep the string short; "Already
  invading" is about the right length.

Do **not** route it through `REJECT_NOTICE` / `observe*`: those three announcements share one latch
so the surface cannot contradict itself about a *match verdict* (`banner.rs:8-11`), and this is not a
verdict about a match. Add a fourth, separately deduplicated announcement in `banner.rs` beside
`announce_verdict` (`banner.rs:190-212`), which is the existing example of a banner that is
deduplicated locally rather than through the shared notice.

---

## 7. Implementation plan

Each step is independently testable; steps 1-3 are the fix, 4-6 are the proof, 7 is separable.

### Step 1 -- add the primitive: `in_invasion_through(session) -> InInvasion`

New code in `crates/er-invasion-warp/src/local_invasion_filter.rs`, beside
`publish_invasion_attempt_state` (`:2083`).

```rust
/// Three answers, never two: "unknown" must not collapse into "no".
pub enum InInvasion { Yes, No, Unknown }
```

Decide from, in order: the state field (`0x16` -> `Yes`; `state_idle` -> `No`), then
`session + SESSION_HOST_STEAM_ID_OFFSET` non-zero as a corroborator (`Yes`), then every other known
state (`No`), then unreadable (`Unknown`).

Name `0x16` as a field on `ersc::Abi` -- `state_in_world` -- beside `state_idle` and the rest
(`ersc.rs:137-156`), with the same warning those carry: the enum renumbered wholesale once already,
so a value carried across a Seamless update unchanged is the dangerous option, not the safe one.

Host-side (`cfg(not(windows))`) stub answers `Unknown`, matching every other seam in that file.

Unit-testable on the host: the classification is pure once the two reads are parameters.

### Step 2 -- item 7: gate the popup skip

In `crates/er-invasion-warp/src/lynchpin_use.rs`, insert between the item-identity check (`:389`)
and the idle gate (`:390`):

* resolve the session through the **menu object already in hand** (`menu_object + 0x58`), not through
  `resolve_session()` -- that is the pointer Seamless itself passed, and it is the one path with no
  scan, no cache and no discard in it;
* `InInvasion::Yes` -> pass the dialog through to the original, increment `POPUPS_PASSED`, show the
  banner from step 3, log once;
* `InInvasion::Unknown` -> **also pass through**. Fail closed toward showing the dialog.
* `InInvasion::No` -> fall through to the existing idle gate unchanged.

Note this is deliberately stricter than "not idle": a session mid-search is `No` here, and the
existing idle gate still declines to skip it. The new check adds a reason to pass through; it never
adds a reason to skip.

### Step 3 -- the banner

Add `announce_already_invading()` to
`crates/er-invasion-warp/src/local_invasion_filter/banner.rs`, modelled on `announce_verdict`
(`:190-212`): its own `AtomicU32`/`AtomicBool` dedup, `crate::announce::show`, one log line on
failure behind the existing `NOTICE_FAILED` latch. Text under 96 chars and comfortably under 1728px;
it should say the mod declined and that Seamless's own menu is the way out, in that order.

Gate it on `config.reject_notice` like its neighbours, so a player who turned notices off still gets
the dialog and no banner.

### Step 4 -- item 9, the load-bearing correction: stop discarding the session at `0x16`

In `crates/er-invasion-warp/src/local_invasion_filter.rs:1203-1206`, accept `state_in_world` **only
when `discovering == false`**. The `discovering` parameter already exists for exactly this
distinction (`:1158`, `:1185-1187`), and the asymmetry is the point: widening the accepted set during
a **scan** enlarges a haystack that has already produced six false positives, while widening it for
**retention** keeps a pointer that was identified properly and has merely moved into a state the
table forgot to list.

This single change makes `resolve_session()` keep working through an invasion, which restores:
`publish_invasion_attempt_state` -> `invasion_attempt_in_flight = true` -> `invasion_warp_policy() ==
MarkersOnly` -> `request_invasion_warp` refuses (`warp.rs:462-464`) -> the map pin and F7/F8/F9 both
decline, with the pins dimmed (`map_hooks.rs:1109`).

Add a host-side regression test asserting that a session at `state_in_world` is **retained** and
**not discovered**. `crates/er-invasion-warp/src/local_invasion_filter/tests.rs` already tests this
module's state machine off-game.

### Step 5 -- make the refusal legible on the warp path

`map_confirm.rs:115-121` currently logs the refusal and nothing else. `WarpError::NotAWarpDestination`
already has good wording (`warp.rs:340-345`). Decide whether it also earns a banner; recommendation:
yes, reusing step 3's surface, because a map marker that silently does nothing when clicked is the
same "is the mod broken?" failure as the swallowed dialog.

### Step 6 -- gates

Scoped, per AGENTS.md (never `check.sh` from a worktree agent):

```
cargo test -p er-invasion-warp -p er-invasion-warp-core
cargo fmt -p er-invasion-warp -p er-invasion-warp-core -- --check
python3 scripts/check-comment-caps.py <files touched>
python3 scripts/check-no-lossy-utf8.py
python3 scripts/check-shared-hook-rvas.py     # only if a detour address moves; none should here
cargo xwin build --release --target x86_64-pc-windows-msvc -p er-invasion-warp
```

Note the workspace `default-members` is `crates/er-quickload`, so a bare `cargo xwin build` compiles
nothing for this crate and exits 0. Name the package, and prefer
`scripts/er-build-dlls.sh er-invasion-warp` so the artifact carries a provenance record the launch
scripts will accept.

### Step 7 -- runtime proof, in one session

Nothing below can be settled statically.

1. Start an invasion, then use the Lynchpin. Expect: dialog **opens**, banner shows, `POPUPS_PASSED`
   increments, `POPUPS_SKIPPED` does not.
2. While in that invasion, confirm an invasion pin on the world map. Expect:
   `map-confirm: ... REFUSED: an invasion attempt is in flight` and the player does not move.
3. Read `local-invasion: session fields changed at state 0x16` to confirm the session was retained
   rather than discarded -- the absence of `DISCARDING the cached session` is the oracle for step 4.
4. Pick "Seek opponent" from the dialog and trace `join-progress` across it, specifically how long
   `lobby=0` persists, to settle failure mode #4.
5. Read `main_player.player_game_data.chr_type` (offset `0x98`) while in a Seamless invasion, to
   settle whether the `ChrType` oracle of section 2.2 is usable at all.

---

## 8. Pre-existing defect found on the way, worth its own issue

**VERIFIED, and it is not part of either item.** `crates/er-invasion-warp/src/stall_watchdog.rs` was
never updated for the Seamless enum-wide `+1` renumber. `git log` shows the re-pin commit `fd554f9d`
("Re-pin er-invasion-warp against Seamless Co-op v2.0.0") touched `ersc.rs`,
`local_invasion_filter.rs`, `build.rs` and the tests, and **not** `stall_watchdog.rs`.

The consequences are exactly the two regressions that file documents at length and pins with tests:

* `TIMED_STATES = [0x0e, 0x12, 0x13]` (`stall_watchdog.rs:76-80`), labelled "search -> connect
  handoff / connect / connect" in the **old** numbering. Under v2.0.1, `0x0e` **is**
  `state_searching` (`ersc.rs:200`, and the value is read straight out of
  `mov dword [rdi+0x150], 0xe` at `ersc+0x25886`). The module's own doc calls timing `SEARCHING`
  "the single most obvious way to get this wrong" (`stall_watchdog.rs:23-26`), and its test
  `a_long_search_is_never_a_stall` asserts against `state::SEARCHING = 0x0d` -- the value that is no
  longer searching -- so the test passes while the bug ships.
* `0x12` under v2.0.1 is the old `0x11 + 1`, i.e. Seamless's own "found nobody, go round again" retry
  step -- the state whose timing cancelled 31 searches in one session and made the detector "strictly
  worse than not existing" (`stall_watchdog.rs:56-72`).

`watch_for_stall` (`actions.rs:890-930`) feeds `read_session_state` straight into `observe`, with no
translation, gated only on `AUTO_SEARCH_ARMED`. So whenever the auto re-search loop is armed, an
ordinary search is cancelled after `STALL_THRESHOLD_MS = 5_000`, and the loop restarts it, and it is
cancelled again. That is a strong candidate for "I'm just getting failed invasions" independent of
anything in items 7 and 9.

The fix is to derive the timed set from `ersc::Abi` rather than duplicating literals -- the file's
own justification for duplicating them is host-testability, which a `&'static Abi` parameter
satisfies just as well -- and to re-derive the two connect states for v2.0.1 with
`scripts/ersc-disas.py` before trusting either. File it in `bd`; do not fold it into the item 7/9
change.

---

## Adversarial validation (2026-09-13)

Second agent, adversarial brief: try to refute, default to "not proven". Static reading only --
no product source edited, nothing built, no game launched, no runtime probe. Primary sources
opened independently; every line number the plan cites above was checked against the file rather
than trusted. **All of them are accurate** (spot-checked ~45 citations; the only drift is
`actions.rs:890-930`, whose function actually starts at `:886`, and `announce::show` is `pub
unsafe fn`, which the plan writes without the `unsafe`). Nothing above is deleted; corrections are
added here so the disagreement stays visible.

### Claim 2 -- `0x16` is post-renumber. **CONFIRMED, and the plan understates its own evidence.**

This was the highest-stakes question in the brief and it resolves cleanly in the plan's favour.

* The renumber commit is `fd554f9d` "Re-pin er-invasion-warp against Seamless Co-op v2.0.0",
  **2026-09-02**. `git show --stat` lists eleven files; `stall_watchdog.rs` is not among them
  (this also confirms §8 independently).
* `git log -S'0x16' -- crates/er-invasion-warp/src/local_invasion_filter.rs` returns its earliest
  commit as `392b4b3c`, **2026-09-08** -- six days *after* the renumber. `0x16` has never existed
  in this file under the old numbering.
* The comments the plan leans on are not assertions, they are transcribed live runs, and both
  postdate the re-pin: `:2627-2633` cites run `br-20260910-003946-b9b0` (`OSM=0x466ad518
  session=0x466ac930`, state `0x16` held while lobby/proto advanced, `0x16 -> 0x23 CANCELLING --
  held 5995 ticks / 181264ms`), and `:1888-1906` cites `br-20260910-042516-3b5d` (entering `0x16`
  writes `+0x1d0`/`+0x1d8`, return to idle zeroes both, two different SteamID64s across two
  invasions). `ersc.rs` pins `SUPPORTED_VERSION` at **2.0.1** via `ERSC_SUPPORTED_VERSION` in
  `build-support/prologue_build.rs`, and the ERSC RVAs quoted alongside `0x16`
  (`ersc+0x26ac0`, `ersc+0x26b40`) are `V201_*`-era addresses. So `0x16` was measured on the
  supported build, against the live session ERSC itself handed over.
* Corroborating from the other side: bd `invasion-pin-dim-and-warp-block-are-search-scoped-2026-08-12`
  records the **pre**-renumber vocabulary -- `session + 0x110`, `IDLE=0x00`, `SEARCHING=0x0d`,
  `CANCELLING=0x22`, offer `0x12`. Both the offset (`0x110` -> `0x150`) and every code moved. `0x16`
  appears nowhere in that older set.

**One semantic downgrade the plan should absorb.** What is measured is "the ERSC session holds
`0x16` for the duration of an invasion the local player started, and entering it writes a host
SteamID". Nothing measured shows what `0x16` reads for an ordinary Seamless **co-op guest**, and
the same memory notes the session layer is shared. So the primitive is honestly
`in_someone_elses_world_via_ersc()`, not `in_invasion()`. That is fine for gating the Lynchpin
(harmless either way) and fine for the warp latch (the pre-existing rule is already "anything not
idle counts"), but a **banner that says "Already invading" may lie to a co-op guest**. Word the
banner for the action refused, not for a role that has not been measured.

### Claim 1 -- the warp gate opens at `0x16`. **PLAUSIBLE-BUT-NARROWED. The weakest link is `OSM == 0`, and for the user's reported invader-side run it probably does not hold.**

Each link re-read independently:

| link | verdict |
| --- | --- |
| `identifies_a_session` excludes `0x16` (`:1203-1206`) | **CONFIRMED** -- four codes, `0x16` absent |
| `session_scan.rs:252` is RETENTION, not discovery | **CONFIRMED** -- `identifies_a_session(abi, session, false)`; `:158`/`:173` are the `true` discovery calls, and `tests.rs:1267-1276` pins both strings |
| a failed `resolve_session()` forces `false`, not a latch | **CONFIRMED** -- `:2564` stores `false` explicitly; only two production writers of that latch exist in the whole workspace (`:2091`, `:2564`) |
| `in_flight(false)` opens the warp | **CONFIRMED** -- `invasion_warp_policy()` -> `Warpable` (`warp.rs:303-309`), and `request_invasion_warp`'s only refusal is `MarkersOnly` (`warp.rs:462-464`); `drive.rs:519` reads the same policy |
| the chain fires during the user's invasion | **NOT PROVEN, and evidence points the other way** |

The last row is the refutation. `resolve_session`'s `OSM != 0` branch (`:845-848`) never calls
`identifies_a_session` at all -- it is a bare `read_session_state(...).is_some()` range check,
which `0x16` passes. So at `OSM != 0` the session resolves, `publish_invasion_attempt_state`
(`:2089-2091`) computes `state != 0 && state != state_idle` = **true**, and the warp gate is
**closed**. The plan says this in one sentence and then still ranks the failure "#1 (highest
confidence)".

And `OSM` is set on the invader's own path. `menu_object.rs:131-145` `invade_observer` calls
`capture_osm(a, "the invade action (an invasion item, or Seamless's own menu row)")`, and
`capture_osm` (`:29-55`) does `OSM.swap(osm)`. It is installed every tick by default
(`local_invasion_filter.rs:2509`, defaulting on when the config snapshot is absent). A player who
reached `0x16` by using the Lynchpin necessarily drove ERSC's invade action, so `OSM != 0` before
`0x16` is ever reached. `adopt_menu_object` (`:69-95`) is a third route to the same store.

So the genuinely exposed populations are: a **host being invaded who never touched the item**
(and whether that host's ERSC session even reads `0x16` is unmeasured -- every `0x16` reading in
this repo is invader-side); a player whose ERSC observers are configured off; and a build where
`prologue_matches` refuses the invade action. **The user reported the failure as an invader.**
Downgrade #1 from "highest confidence" to "a real hole, in a population that is probably not the
reporting user", and promote the host case to the reason to fix it.

### Claim 3 -- nothing in the item path asks "am I invading". **CONFIRMED.**

`open_choices_hook` read in full (`lynchpin_use.rs:343-443`) plus its callees. Exactly three
consultations: `auto_search_armed()` (`:361`), `item_in_use()` reading
`CSMenuGaitemUseState+0xc` (`:372`, implementation `:309-335`), `popup_skip_gate_is_idle()`
(`:390`). `adopt_menu_object` at `:350` validates a pointer and stores `OSM`; it decides nothing.
No fourth read exists.

### Claim 4 -- `SetupMapReentry`. **CONFIRMED verbatim, and the plan is *more* right than it claims about the build.**

`getFunctionByAddress 140cafc30` on `:8765` returns
`void SetupMapReentry(CSSessionManagerImp *, bool inCeremony)`, 196 bytes, callees
`GetSteamID@14025f8d0` / `LeaveSession@140cae730` / `GetSessionManager@1423f1930`, callers
including `TriggerAreaReload@1405f2890` and `AttemptReinvasion@1405f2650`. `getDecompiledCode`
matches the plan's quoted C line for line (the plan renders `>> 8` as `sessionPlayers.size`, a
cosmetic simplification -- the entry stride is `0x100`).

Two corrections, in opposite directions:

* **The plan does state the build** ("The address is the 1.16.2 RVA ...; the installed game is
  1.17.1"). It is not overreaching there. But its hedge that the call "can legitimately come back
  `AddressUnavailable`" is **too pessimistic**:
  `docs/recon/rva-map-1162-to-1170.needed-verified.tsv` carries the row
  `0x140cafc30 -> 0x140cb1300 IDENTICAL-WHOLE 1.000 55 SETUP_MAP_REENTRY_RVA BOTH-ENTRIES
  PDATA:0xc4/0xc4`, and `0xcb1300` is above the `0xafefe9` boundary so the 1.17.1 carry adds
  `0x70`. The address resolves on the installed build; the semantics apply live. **#2 is stronger
  than the plan says, not weaker.**
* **The plan does not overreach on the host branch either** -- it explicitly separates
  "as a guest/invader it sets `protocolState = WaitReentryToMap` and nothing else" from the
  `lobbyState == Host` kick/`LeaveSession` path, and `warp.rs:115` independently pins
  `SESSION_LOBBY_STATE_HOST = 3` from `140cafc54: cmpl $0x3,0xc(%rcx)`. For the invader-side
  symptom the only effect is the self-latch, which is what the plan says. No defect here.

The call path is real: `request_invasion_warp` -> `setup_map_reentry_if_in_game(base)`
unconditionally at `warp.rs:487`, gated inside on `protocolState == InGame` (`:742`), reached from
`map_confirm.rs:93` and `drive.rs:519-529`. **CONFIRMED as a reachable path.**

### Claim 5 -- Challenger's Lynchpin `0x407fde63`. **CONFIRMED.** `lynchpin_use.rs:91`, with `LYNCHPIN_GOODS_ID = 0x7f_de63` at `:94`. Compared at `:373`.

### Claim 7 -- `PlayerGameData.chr_type == 0x98`. **Offset CONFIRMED; "unproven for Seamless" is UNDERSTATED to the point of being misleading.**

`crates/er-game-base/src/pgd.rs:140` is exactly
`const _: () = assert!(core::mem::offset_of!(PlayerGameData, chr_type) == 0x98);`, and
`ChrType` in `../fromsoftware-rs/crates/eldenring/src/cs/chr_ins.rs` carries the enum the plan
quotes (`BloodyFinger = 15, Recusant = 16, BluePhantom = 17, FesteringBloodyFinger = 18`).

But the plan calls this "a HYPOTHESIS, needs one live read", as if the question were open. It is
not fully open -- there is a **negative live measurement the plan missed**, bd
`seamless-types-the-local-player-duelist-2-summonparam-minus12-2026-09-07` (run
`br-20260908-012336-d9f9`):

* under Seamless, with **no** invasion in progress, the local player's `ChrIns::chr_type` (`+0x68`)
  and `GameMan::summonParamType` (`+0xd84`) **flip in lockstep** -- `(0, -12)` and `(2, 0)`, eight
  alternations in one run, no third combination. "A snapshot of either field is a coin flip, and a
  gate that samples once can read either pairing."
* Seamless produces **none** of the vanilla invader values (15/16/18) in ordinary play, and the
  memory states outright: "a feature that gates on 'am I invading' by reading either field may
  never arm in a Seamless session." `er-lockon-filter` already gates on that pair and did not fire.

Note also that the measured field is `ChrIns::chr_type +0x68`, a *different* field from the
`PlayerGameData::chr_type 0x98` the plan proposes. The plan's route is not the one that was
measured, and its sibling was measured to be unstable. So §2.2's framing -- "already in the
dependency graph and needs no new RE to *read*" -- is true about the mechanics and wrong about the
confidence. Rewrite it as: **there is positive evidence against a naive `chr_type` read under
Seamless; the ERSC session state is the only measured oracle and the only one to build on.** Step
7.5's live read is still worth doing, but as a *falsification* of a discouraging prior, not as a
first look.

### Claim 6 -- the fix shape.

**The banner half: CONFIRMED in every detail.** `announce::show` is `pub unsafe fn show(text:
&str) -> bool` at `announce.rs:487`; `MAX_CHARS = 96` at `:176`; `NOTICE_FIELD_WIDTH_PX` at `:161`
resolves through `er_gfx::announce_notice::NOTICE_FIELD_WIDTH_PX = 1_728`; the 1729px
"placed successfully and never rendered" note is verbatim at `banner.rs:199-201`; the
`showPopupMenu` warning is at `banner.rs:151-155`; the shared-latch rationale is at
`banner.rs:8-11`; and `announce_verdict` (`:190-212`) does dedup locally, via `LAST_VERDICT_BLOCK`
(`:194`, declared `:214-215`) rather than through `REJECT_NOTICE`. The plan picked the right
exemplar. (Minor internal inconsistency in the *source*, not the plan: `banner.rs`'s own module doc
says "all three announcements share `REJECT_NOTICE`", which `announce_verdict` does not.)

**The `identifies_a_session` widening: the blast radius is larger than the plan says, and step 4
as written does not survive contact with a second mechanism in the same file.**

Enumerating callers first, since the brief asked. `identifies_a_session` has exactly three call
sites: `session_scan.rs:158` and `:173` (`discovering = true`, the sweep) and `session_scan.rs:252`
(`discovering = false`, the cached-answer revalidation). `differential_scan.rs:92` uses
`mutex_shape_identifies_a_session` directly and is untouched. No test pins the four-code list
(`tests.rs:1254-1277` pins the `discovering` gating, `:1764-1778` the busy-candidate refusal,
`:1162-1205` the pointer and mutex requirements) -- so the edit breaks no gate. **The predicate
edit itself is correctly scoped.** What is not scoped is everything downstream of
`resolve_session()` suddenly succeeding at `0x16`:

1. **`note_session_liveness` will discard the session anyway, undoing the fix.** `:2634` exempts
   only `OSM != 0` from the liveness heuristic. In the exact scenario step 4 targets -- `OSM == 0`,
   session at `0x16` -- `trace_join_progress` (`:2807-2817`) now feeds `Some(0x16)` in, and after
   `LIVENESS_STALE_LIMIT = 4` (`:2692`) samples **in which lobby/proto changed**, `:2656-2674`
   logs `DISCARDING the cached session`, calls `invalidate_cached_session()` and latches
   `SESSION_EVER_DISCARDED`. Its own refusal text ("A real session moves through
   0x0e/0x0f/0x12/0x13 during a join, so this pointer is not one") is the same stale assumption
   `identifies_a_session` is being fixed for. The engine is usually quiet once the fight starts, so
   this bites during the transition into `0x16` rather than during the fight -- but it is a coin
   flip, not a safe margin. **Step 4 must exempt `state_in_world` from the liveness discard in the
   same edit, or it is half a fix.** The plan's own step-7.3 oracle ("the absence of `DISCARDING
   the cached session`") is watching for exactly this and does not say what to do when it appears.
2. **A resolvable `0x16` session becomes drivable, and the cancel path deliberately reaches for
   `OPTIONSELECT_LEAVEWORLD` there.** `actions.rs:825-851`: when `cancel_row_refusal` fires --
   which it does at `0x16`, since `CANCEL_ROW_VISIBLE_STATES = [0x0e, 0x0f, 0x10, 0x12]`
   (`lock_report.rs:231`, refusal at `:421-438`) -- the code substitutes the leave-world action and
   drives it. Today `drop_a_match_the_engine_has_already_failed` cannot reach that at `0x16` with
   `OSM == 0`, because its `resolve_session()` at `:2783` fails. After step 4 it resolves. Its
   trigger is `Verdict::Idle` (`lobby_state == NONE`) held past `TORN_DOWN_GRACE_MS = 8_000`
   (`:1477`) -- and whether the *vanilla* `CSSessionManagerImp` reads `NONE` during a *Seamless*
   invasion is precisely the plan's own open failure mode #4. If it does, **step 4 hands the mod
   the ability to boot the player out of a live invasion after 8 seconds** -- the same class of
   harm the whole document is written to prevent. `cancel_match` (`actions.rs:239`) and
   `drive_pending_cancel` (`:2041`, whose `Err(SessionNotIdentified)` branch stops firing) change
   behaviour for the same reason. `request_invade`/`drive_invade_inline`/`drive_invade_with_owner`
   do not: all still require `state_idle` (`actions.rs:502`).
3. Harmless or improving: `host_steam_id` (`:1926`) starts naming hosts instead of logging
   "no Seamless session"; `advertisement_lobby_id` (`:1616`); `session_is_idle` (`:2217`) answers
   `false` before and after; `watch_for_stall` is gated on `AUTO_SEARCH_ARMED` (`actions.rs:901`)
   and `0x16` is not in `TIMED_STATES`.

**Recommendation: keep step 4, but ship it with (a) the `note_session_liveness` exemption and
(b) an explicit refusal to drive any ERSC action from a `state_in_world` session unless the player
asked for it.** Resolving a session and driving it are two permissions, and step 4 currently grants
both.

### Defects found in the plan

1. **§3.1's mechanism is self-contradictory in its `adopted` branch.** It has
   `drive_invade_with_owner(menu_object, ..)` re-reading the real session "through the menu object"
   and finding `0x16`. That cannot happen: `adopt_menu_object` -> `capture_osm` -> `OSM.swap`
   (`menu_object.rs:33`, `:90-93`) runs at `lynchpin_use.rs:350`, *before* the gate at `:390`, so
   if `adopted` is true then `OSM != 0` and `popup_skip_gate_is_idle` takes the **strong** branch
   (`:2168-2173`), reads the real session's `0x16`, answers not-idle, and **the dialog passes
   through**. The swallow can only occur on the `drive_invade_inline` path with `OSM == 0` and a
   scanned look-alike reading `0x01`. Fix the narrative; the failure mode survives, in a narrower
   form.
2. **§3.1 omits a second brake that makes the swallow even rarer.** The moment a scan-latched
   session is discarded, `SESSION_EVER_DISCARDED` latches (`:2674`) and
   `popup_skip_gate_is_idle`'s fallback returns `(false, "nothing trustworthy ...")` (`:2187-2193`)
   for the rest of the run -- dialog passes through. And with the *real* session at `0x16`,
   `session_is_idle()` -> `resolve_session()` -> `Err` -> `false` -> dialog passes through. So the
   honest-session case already fails safe today. Item 7's exposure is narrower than §3 implies,
   which makes the **argument for the item gate a defence-in-depth one, not a live-bug one** --
   still worth building, but it should not be sold as the cause of the user's report.
3. **Two directly-relevant `bd` memories are missing**, against AGENTS.md's "read relevant bd
   memories before broad source inspection":
   `seamless-types-the-local-player-duelist-2-summonparam-minus12-2026-09-07` (contradicts §2.2 and
   re-scopes step 7.5, above) and
   `invasion-pin-dim-and-warp-block-are-search-scoped-2026-08-12`. The second is *supporting*
   evidence the plan should want: the user explicitly confirmed this behaviour once already --
   "unable to warp while invading, which is great" -- which reframes step 4 as **restoring a
   regression the user already signed off on**, not proposing a new policy. That memory also
   carries a constraint step 4 inherits: `pin_choice_signature` must mix the search state or the
   live map will not repaint when the flag flips, so the plan's "the pins dimmed
   (`map_hooks.rs:1109`)" is only half the story.
4. **The plan never asks what published `in_flight` during the user's run.** Every discriminating
   line it lists in §4 is a log line, and the single cheapest one is missing from the table:
   whether `local-invasion: Seamless session resolved -- OSM=0x... session=0x...` (`:850-852`)
   appears at all. That one line decides between the `OSM != 0` world (gate closed, #1 is not the
   bug) and the `OSM == 0` world (gate open). It should be the **first** thing read from the run,
   before any of the others.
5. **§8 is fully confirmed** and if anything is the strongest finding in the document.
   `stall_watchdog.rs` still declares `state::IDLE = 0x00`, `SEARCHING = 0x0d`, `RETRYING = 0x11`,
   `CANCELLING = 0x22` (`:38-51`) -- pre-renumber, against `ersc.rs`'s `0x01/0x0e/0x23`.
   `TIMED_STATES = [0x0e, 0x12, 0x13]` (`:76-80`), and under v2.0.1 `0x0e` **is** `state_searching`
   and `0x13` **is** `state_offer_received`. `a_long_search_is_never_a_stall` (`:252-261`) asserts
   against `state::SEARCHING`, i.e. `0x0d`, so it passes while the shipped detector times the
   state it was written to protect. `watch_for_stall` (`actions.rs:886-930`) feeds
   `read_session_state` straight into `observe` with no translation, gated only on
   `AUTO_SEARCH_ARMED`, and cancels at `STALL_THRESHOLD_MS = 5_000` (`stall_watchdog.rs:125`).
   `git log` confirms the file was edited twice after the renumber (`0e084240`, `4b92d185`,
   both 2026-09-08) without its constants being re-pinned. **This is the defect most likely to
   match "I'm just getting failed invasions", and it should be filed and fixed ahead of both
   items 7 and 9.**
