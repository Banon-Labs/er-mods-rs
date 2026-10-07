# NPC duel signs and a custom Mimic Tear

Draft design, 2026-10-06. Nothing here is built. It turns two Frida prototypes into one DLL:

1. **NPC duel signs.** In an offline game, the player uses the Duelist's Furled Finger. As the sign
   is placed, a menu asks which NPC to fight. Touching the sign on the ground summons that NPC as a
   hostile red phantom.
2. **Custom Mimic Tear.** Using the Mimic Tear Ashes summons up to four characters the player
   built: each one from a build URL, with an AI the player either authors or borrows from an NPC.

Every claim is tagged **MEASURED** (seen in a live game this week), **STATIC** (read out of the
1.17.1 binary, not run), or **OPEN** (not known yet). Addresses are 1.17.1 runtime VAs.

---

## 1. What is already proven

### 1.1 A red sign can summon a character from another map

**MEASURED** on 2026-10-06 at the Great Jar (m60_47_41), under Seamless Co-op. The bd memory is
`red-sign-other-map-npc-combat-live-1171-2026-10-06`. Prototype:
`scripts/frida/signs-to-player.js` (`OTHER_MAP`, `otherMapSign`), with the spawn done by
`scripts/frida/spawn-npc.js` through `scripts/er-ai-lab.py`.

| Step | How | Evidence |
| --- | --- | --- |
| Create the character | Spirit-ash spawn path: `SummonBuddyManager::CreateSummonChr` `0x1404baea0` with `charaInit >= 0` builds a c0000 human with gear and face | MEASURED: Yura (NpcParam 523180079, think 523180000, charaInit 23180), model loaded (`ChrSetEntry` loadStatus 4) |
| Hide it until summoned | Held out of sight (MEASURED, a holding pen). The intended mechanism is `ChangeCharacterDisableState` `0x1403f6500(ChrIns*, bool)`, which is how the Great Jar knights wait | STATIC: bd `chr-enable-setter-and-join-enable-1171-2026-10-06`. The disable path is not yet run live |
| Place a red sign keyed to it | `PlaceNPCSummonSign` body `0x1406fa7f0(SosSignMan*, 2, &entity, &region, summonFlag, &block, dismissFlag, 0)`, then write sign `+0x14` position and `CreateSignSfx` `0x1406febe0` | MEASURED |
| Touch the sign | The game's own join: `0x1406fe520` -> `0x1406ff260` -> `0x14050c6d0` (enables the character: `0x1403f6500(chr, 0)` at `0x14050c74f`) -> warp `0x1403fa5d0` -> `PartyMemberInfo::AddMember` `0x1409faba0` | MEASURED: team 16, party state 4, and Yura killed the player |

The knight control run confirmed the mechanics. A Great Jar knight goes from hidden to drawn in one
frame when its sign is touched: disable bit cleared, team 27 -> 16, chrType 5 -> 2. Its
loadStatus is already 4 before the touch (MEASURED).

### 1.2 Things that broke, and what each one teaches

| Failure | Cause | Rule for the DLL |
| --- | --- | --- |
| A Roundtable NPC joined but was never drawn | Its event script disables backread (`ChrIns+0x20`), so its model never loads (loadStatus 0) | Only summon characters we spawned ourselves, never map residents. STATIC: bd `chrins-10-state8-draw-load-1171-2026-10-06` |
| Game crash | Forcing loadStatus (`ChrSetEntry+0x8`) to 4 | Never write engine state machines; call the setter |
| Joined Yura went friendly and untargetable | The lab's heartbeat restored his spawn team (16 -> 1) | One owner for a summon's team; the DLL never fights itself |
| Hidden Yura walked back to the player | The AI home (`ChrIns+0x90`) was the spawn point | Set the home when hiding; the disable path should make this moot |
| No other sign could be summoned | A joined phantom holds a `PartyMemberInfo` slot, and every sign verdict (`0x1406f3150`) returns 0 | One NPC duel at a time, or understand the verdict first (OPEN) |
| Spawned Yura removed after 14.6 s | Roundtable Hold is a no-summon area; the summon manager removes the spawn | Refuse to place a sign where summoning is barred, and say why |
| Roundtable resident sent home after joining | `LeavePartyMember` `0x1409fb840` in a no-multiplay area | MEASURED: clearing party entry `+0x1d` keeps it; a sign should not be offered there at all |

### 1.3 The Mimic Tear can be redirected

**STATIC**, bd `mimic-tear-summon-hijack-1171-2026-10-06`; prototype
`scripts/frida/mimic-tear-turtles.js`.
- Using Mimic Tear Ashes (goods 207000..207010) goes through `BuddyGenerator` `0x1404bbdd0`.
- Inside that call, the DLL rewrites the BuddyParam rows that `GetBuddyParam` `0x140d28010` returns: npc, think, charaInit and offsets.
- It also grows the summon list to N entries with the game's own list insert, and removes the copy-the-player branch.

Item use, FP cost, the summoning-pool range check, dismissal and the Seamless broadcast (packet 78)
all stay native. A single Mimic Tear has been redirected live. Gear, face, names, heals and turtle
Lua brains were applied live on spawns made through the same `CreateSummonChr` path
(`spawn-npc.js` `applyEquip`, `applyFace`, `nameHook`).

### 1.4 NPC AI is a Lua VM we can load into

**STATIC + MEASURED**, bd `ai-lua-state-and-loader-1171-static-2026-10-05`.
- NPC AI runs in a stock Lua 5.0 VM: `CSWorldAiManager` `0x143d66548` -> `+0x6938` -> `+0xb8` -> `+0x28` `lua_State*`.
- `luaL_loadbuffer` `0x142027f30` compiles plain source at runtime.
- The turtle brains (`scripts/frida/ai-lua/mods/brain_turtles.lua`) ran live through `scripts/frida/ai-lua-hot-reload.js`.
- Think rows choose the battle goal. Yura's three rows all use goal 29999, the game's generic NPC AI.

---

## 2. Feature A: NPC duel signs

### 2.1 Player flow

1. Offline, the player uses the **Duelist's Furled Finger** (goods 101).
2. Instead of the native "sign placed" result, a picker opens: a list of NPCs by name.
3. The player picks one. A red summon sign appears where the player's own duel sign would have
   gone.
4. The player touches the sign. The NPC comes out of it as a hostile red phantom and fights.
5. When the duel ends by a death, dismissal or leaving the area, the NPC is removed and the sign
   can be placed again.

### 2.2 Hard requirement: the NPC is never seen before the sign is touched

Nothing is spawned while the picker is open. The NPC is created only after the player has chosen,
and the player must never see it, its summon effect or its spawn animation before touching the
sign. The prototype broke this: the lab spawner creates 3 m in front of the player and the hide ran
one frame later, so the spawn played in view (MEASURED 2026-10-06, user report). The DLL:

- disables the character inside the creation call itself. That means an `onLeave` on
  `CreateSummonChr` `0x1404baea0` on the game thread, calling `0x1403f6500(chr, true)` before the
  frame that would first draw it. It does not wait for a later frame tick.
- creates it without a summon effect or animation: no `generateAnimId` and no buddy SFX. Which
  arguments of `CreateSummonChr` carry these is OPEN.
- creates it at the sign's position, which is where the join puts it anyway, so it never has to be
  moved while hidden. It stays well inside the activation range, so its model stays loaded
  (loadStatus 4; MEASURED that a character disabled at loadStatus 0 still reaches 4).
- checks the result before placing the sign: disabled, loadStatus 4, not drawn. Otherwise it
  removes the spawn and tells the player why, rather than offering a sign for a broken character.

A live check for this belongs in the first DLL smoke: the hidden character's `IsDrawn`
(`0x1403f3930`) must read 0 on every frame from its creation to the touch.

### 2.3 Components

| Component | What it does | Reuses |
| --- | --- | --- |
| Item gate | Lets goods 101 be used offline and in areas the game would grey it out in; anywhere summoning is barred (the Roundtable case) is still refused, with a reason | `er-invasion-warp/src/can_use_goods_gate.rs` (already opens fingers vanilla refuses) |
| Use interception | Catches the finger's use before the native duel-sign request, and opens the picker instead | OPEN: the native duel-sign request path (Cheat Engine table `requestBlackSOS`) is not read yet |
| Picker | On-screen list of fightable NPCs, keyboard/pad driven | `er-npc-possess/src/picker` (overlay list, 408 rows, initial-jump traversal) |
| Roster | Per NPC: NpcParam, think, charaInit, display name. Sources: the MSB part of each named NPC (as for Yura: `c0000_9012` in `m11_10_00_00`), plus user rows in TOML | Extraction corpus under `~/er-extract`; `scripts/er-param-read.py` for names |
| Spawn | `CreateSummonChr` with charaInit >= 0; wait for loadStatus 4 | `er-npc-possess/src/spawn` (request, readiness) |
| Hide | `0x1403f6500(chr, true)` | New |
| Sign | `0x1406fa7f0` keyed to the spawn's entity id; position at the player's feet | New |
| Join | Native; nothing to do | Game |
| Ownership | Keep team 16 while joined; clean up on death/dismiss/area change | New; replaces the lab heartbeat that broke team |

### 2.4 Open questions

- **Offline sign verdict.** Every run so far was under Seamless. In a true offline session, does
  the touch check (`GetSignByActionButtion` `0x1406fadb0` -> `0x1406f3150`, `0x1406fc850`) accept
  a red NPC sign, and does `PartyMemberInfo` exist? `0x1406f3150` enters Arxan-obfuscated code, so
  read its logic through the 1.16.2 dump first.
- **Which flags.** The prototype borrows the first Great Jar knight's summon/dismiss flags
  (1047412220 / 1047410230). The DLL needs its own flag ids that no event script reads, and
  persistence rules for them (virtual flags, or reset on load).
- **Entity id.** Spawns all carry 35000. Two duels or a duel plus a Mimic must not collide; give
  each spawn a distinct id or key the sign differently.
- **Summon-path removal.** The spirit-ash path removes spawns after about 10 s in some states (the
  lab measured this on 2026-10-05 and drops `RequestWarp` to survive it). Decide whether duel NPCs
  use this path at all or need a non-buddy creation path (`SpawnDynamicChr` crashed for humans: bd
  `human-spawn-via-spawndynamicchr-crashes-render-uaf-1171-measured-2026-10-05`).
- **AI controller.** Spawned characters have one; map residents in Roundtable did not
  (`GetComManipulator` null). Spawning is therefore the only supported source.
- **Ending the duel.** Native red-phantom rules (death sends the phantom home, the host's death
  ends the session) need to be confirmed for an NPC phantom, and the spawn freed afterwards
  through `NotifyBuddyUnsummon`, never `RemoveChrIns` (crash, MEASURED 2026-10-05).

---

## 3. Feature B: custom Mimic Tear

### 3.1 Player flow

1. The player configures up to four **companions**. Each one has:
   - a build URL,
   - a name,
   - an AI choice: a custom Lua brain, or "fight like <NPC>".
2. Using Mimic Tear Ashes summons all configured companions through the native spirit-ash flow, in
   formation around the summoning point.
3. Each companion wears its build's gear, face and Ashes of War, carries the build's name, and
   fights with its chosen AI.

### 3.2 Configuration

```toml
[[mimic.companion]]
name = "Leo"
build_url = "https://er-build-planner.nyasu.business/..."   # read without auth, bd planner memory
ai = { brain = "turtles" }           # a Lua brain shipped with the DLL or dropped in a folder

[[mimic.companion]]
name = "Yura"
build_url = "..."
ai = { like_npc = 523180000 }        # borrow an NPC's think row (battle goal 29999 for Yura)
```

At most four companions. Fewer than four summons fewer.

### 3.3 Components

| Component | What it does | Reuses |
| --- | --- | --- |
| Hijack | `BuddyGenerator` scope, BuddyParam row rewrite per companion, list growth, copy-branch removal | `mimic-tear-turtles.js` -> Rust in `er-hook` |
| Build import | Fetch and parse a build URL into gear, gems, face and stats | `er-build-import-runtime` / `er-build-import-core` (`configured_build_url`, `face.rs`, `gaitem.rs`, `equip_native.rs`) |
| Dressing | Mint gaitems and write ChrAsm on the spawned character; apply the face | `spawn-npc.js` `applyEquip` / `applyFace`, measured live |
| Naming | Overhead and party names per companion | `spawn-npc.js` `nameHook` plus the tag-list probes |
| AI, custom | Load the companion's Lua brain into the AI VM, keyed by think id | `ai-lua-hot-reload.js`, `scripts/frida/ai-lua/mods/*` |
| AI, like an NPC | Set the companion's think to that NPC's NpcThinkParam row (`ChrIns_SetThinkParam` `0x1402ca470`) | bd `npc-think-swap-live-1171-2026-10-06` |
| Peer sync | Native packet 78 carries npc/think/charaInit, so peers build the same body | bd `npc-peer-sync-via-buddy-broadcast-seamless-2026-10-05` |

### 3.4 Open questions

- **Stats.** A build URL gives levels and attributes, but an NPC body takes HP and damage from
  NpcParam plus the summon's doping SpEffect (290000+lv). Decide what a level-150 build means for a
  companion: scale HP/damage with a SpEffect, or accept NpcParam values.
- **Weapons the AI cannot use.** Human NPC AI handles common movesets; spells, incantations and odd
  weapon classes may need a brain that knows them. "Like an NPC" only works as well as that NPC's
  goal handles the companion's gear.
- **Peers.** Packet 78 carries the body, not the gear, face or brain. A Seamless peer sees a default
  c0000 unless the DLL also runs there and applies the same companion data.
- **Where builds come from offline.** Fetching a URL needs the network. Cache each build to disk on
  the first fetch and use the cache when offline.
- **Four at once.** The native list growth reached three turtles. Four, plus a duel NPC, plus
  co-op phantoms, may hit the summon manager's or party table's limits.

---

## 4. Packaging

- **One DLL**, `er-npc-summons`, loaded by ME3 as a `[[natives]]` entry, with both features behind
  config switches. It shares hooks through `er-hook` and must not claim prologues
  `er-invasion-warp`, `er-npc-possess` or `er-quickload` already detour. Check with
  `scripts/check-shared-hook-rvas.py` and `scripts/check-me3-dll-conflicts.py`.
- The Frida prototypes stay the place to answer questions; the DLL gets only mechanisms that are
  already measured.
- No env-var gates; the TOML in the game directory is the only switch.

## 5. Order of work

1. **Hide-before-first-draw**: move the prototype's disable into `CreateSummonChr` and confirm `IsDrawn` never reads 1 before the touch (the disable-based hide itself is MEASURED, bd `hidden-npc-red-sign-disable-trick-live-1171-2026-10-06`). It is
   the one step of Feature A not yet proven.
2. **Answer the offline sign verdict** (2.4, first bullet) by static RE, then one offline run.
3. **Feature A in the DLL**:
   - item gate,
   - use interception,
   - picker,
   - spawn,
   - hide,
   - sign,
   - cleanup.
4. **Feature B, one companion**:
   - the hijack,
   - build-URL dressing,
   - think swap.
5. **Feature B, four companions**, plus Lua brains.
6. Seamless peers.

---

## 6. Implementation (2026-10-06)

Two crates on branch `feat/er-npc-summons`:

| Crate | Kind | What is in it |
| --- | --- | --- |
| `crates/er-npc-summons-core` | library, host-tested (19 tests) | `toml` (hand-rolled reader), `config` (duel roster, flags, up to four companions with `brain` / `like_npc` AI), `duel` (state machine and hidden-NPC verdict), `mimic` (trigger test, formation, per-companion row values) |
| `crates/er-npc-summons` | `cdylib`, `er_npc_summons.dll`, its own `[[natives]]` entry, opt-in | `finger` (detour on `StartMultiplayProcedureWithMountData` for type 2, `CanUseGoods` union answer for goods 101), `game` (spawn hidden, place the red sign, party and liveness reads, unsummon), `mimic_hooks` (the three `BuddyGenerator` detours), `picker` + `overlay` (imgui list through the shared host), `lib` (config hot reload, duel driver on a `FrameBegin` task) |

Config: `er-npc-summons.toml` beside the game executable, schema in `er_npc_summons_core::config`.
Log: `er-npc-summons.log` beside the game executable.

Static RE behind the implementation, each in bd:

- `duelist-finger-use-path-1171-2026-10-06`: the finger reaches `StartMultiplayProcedureWithMountData` with type 2, and is offline-blocked by `CanUseGoods` (`disable_offline`).
- `createsummonchr-args-and-spawn-sfx-1171-2026-10-06`: `args[11] = 0` is what keeps the appear animation, the doping and the later re-enable from running. The DLL passes 0, where the prototype passed 1.
- `sign-touch-verdict-offline-1171-2026-10-06`: one red slot, which is why a joined NPC blocks every other red sign.

Every address is a 1.16.2 RVA with a verified row in `docs/recon/rva-map-1162-to-1170.verified.tsv`. They are listed in `docs/recon/npc-summons-addresses.tsv`, plus `StartMultiplayProcedureWithMountData` 1.16.2 `0x1406587b0` (`IDENTICAL-WHOLE`).

Runtime-proven 2026-10-06, run `br-20261006-231247-f262` (Leo, 1.17.1, Seamless loaded): the
finger opened the picker offline, the chosen NPC (Yura) was created hidden, its red sign was
placed 1.5 m from the player, touching the sign joined it as a red phantom, and it fought until
the player killed it. A Mimic Tear +10 summon was replaced by the configured companion.

Found live, and filed:

- After the quickload autoload, `CS::TestNetStep` stays `NotExecuting` until a map reload. That
  step owns `SosSignMan` and runs its per-frame sign update, which sets `SosSignData+0x30` (the
  byte `CreateSignSfx` reads before it shows a sign) and runs the phantom-join tick. Without it a
  placed sign is invisible and a touched sign never joins. `TriggerAreaReload(false)` starts it.
  bd `testnetstep-not-executing-after-autoload-1171-2026-10-06`; it is an er-quickload bug, not
  one in this DLL.
- The hidden NPC shows a party HP bar on the left of the HUD before the sign is touched, which
  breaks section 2.2.

Companion dressing from build URLs (2026-10-06, static RE only, not run live yet; bd
er-effects-rs-x6nl):

- A companion is a c0000 human because its request carries a `CharaInitParam` id. `CreateSummonChr`
  builds it synchronously through `ChrSet::SpawnChr` -> `ChrInsFactory::CreateCharacter` (1.16.2
  `0x140403a60`, 1.17.1 `0x140403dd0`), which looks the row up (`GetCharaInitParam`, solo param
  23) and applies it with `0x140258c00` (1.17.1 `0x140258bd0`). That applier mints the six
  armaments, four armour pieces, four ammo stacks and four talismans from the row's equip fields
  into the new character's `ChrAsm`. Weapon fields carry affinity and level in the id, as 4100 of
  the regulation's own CharaInitParam weapons do.
- So the DLL adds a fourth detour, on `CreateSummonChr`, active only inside a hijacked
  `BuddyGenerator`. It pairs the call with its companion by the npc/think/charaInit loop one wrote
  (`mimic::claim`), writes that companion's build into the row, calls the original and restores
  the row. Field values come from `er_npc_summons_core::dress`, which reuses the importer's parser,
  catalog interface and equip planner; the game side fetches each build once per URL and resolves
  it with the importer's runtime catalog and `ReinforceParamWeapon` clamp.
- Not carried by the row, and logged as not applied: Ashes of War, spells, the great rune, the
  face, attributes. Peers still build the body's own gear, because packet 78 carries only the
  charaInit id. A URL that is refused, fails to fetch or parse, or resolves to nothing leaves the
  companion in its body's own gear, with the reason in the log.

Not built yet:

- Lua brains;
- companion names;
- taking picker input away from the game;
- refusing the finger where summoning is barred.
