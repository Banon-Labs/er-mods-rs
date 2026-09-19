# er-npc-possess visual / animation / VFX replication investigation

## Short answer

Static evidence says **not as a small update to the current `er_npc_possess.dll` mechanism**.

The current possession path makes the possessing client's local NPC copy animate by writing `CSChrEventModule+0x18 requestAnimationId` or, for non-`W_Event` moves, by calling `PlayAnimationByBehaviorName`. Both paths enter Havok/behavior locally and carry local TimeAct, VFX/SFX, hitboxes, and root motion. The static call paths inspected here do **not** show a vanilla packet that publishes that request/behavior event from an arbitrary possessed NPC to unmodded peers.

There is a vanilla behavior-state/position sync system, but the inspected senders are tied to the main player / mount replay-packing path and normal network ownership, not to arbitrary local writes into a remote/map NPC's `CSChrEventModule`. Damage is already proven separately: NPC-owned hits do not put HP on remote players from the possessing client; only the local player's own `PlayerIns` can use the PvP damage packet route.

Practical split:

| case | static verdict |
|---|---|
| Possessing a dynamically spawned NPC | **No** for unmodded peers. The crate's dynamic spawn is local; peers cannot resolve its `P2PEntityHandle` and do not have the same character identity. |
| Invader possessing a host/map NPC | **No** from current evidence. The invader is not the vanilla network owner for that NPC; local `requestAnimationId` is consumed locally and is not published. |
| Host possessing a map-placed NPC | **Maybe only if a deeper native owner/sync path is driven**, not by the current field-write alone. Host likely has the best chance because host/owner-originated NPC state is the direction vanilla clients already consume, but this needs RE of the specific NPC behavior-state/ChrSync publish path and then proof. Current evidence does not show an animation-request packet that unmodded peers will honor. |
| Damage to unmodded peers | **No** for NPC-owned melee/bullets from possessing client. Static damage matrix routes remote-player-victim / NPC-attacker to compute-only mode, no HP change and no packet. |

Confidence: **moderate-high for "current `requestAnimationId` / `PlayAnimationByBehaviorName` is local-only"; high for damage; moderate for host-owned map NPCs because vanilla has adjacent sync machinery that was only partially inspected here.**

## Evidence from repo source

### Current possess/moveset mechanism is local field write + local behavior event

- `crates/er-npc-possess/src/possess/mod.rs:6-22` documents the architecture: the mod installs a manipulator thunk, writes movement intent into the creature's `AiIns`, and fires attacks by writing `CSChrEventModule+0x18 requestAnimationId`.
- `crates/er-npc-possess/src/moveset/mod.rs:7-20` states the moveset layer's core: `CSChrEventModule::RequestAnimation` is effectively one `int` store into `requestAnimationId`; `CSChrEventModule::Update` formats `W_Event%04d`, resolves the behavior graph, and local clip TimeAct/VFX/sound/root motion follow from the local graph.
- `crates/er-npc-possess/src/possess/game.rs:368-385` implements `Chr::request_animation` as a write to `event + chr_event_module::REQUEST_ANIMATION_ID`.
- `crates/er-npc-possess/src/possess/game.rs:442-455` reads only a local dispatch flag bit (`CSChrEventModule+0x24 bit 0`) to learn whether the local request reached the behavior world.
- `crates/er-npc-possess/src/possess/game.rs:499-529` implements the fallback by resolving `PLAY_ANIMATION_BY_BEHAVIOR_NAME_RVA` and calling `PlayAnimationByBehaviorName` on the local `hkbCharacter` slot.
- `crates/er-npc-possess/src/possess/layout.rs:589-622` records the same RE: request field at `+0x18`, local update at `0x14043a580`, dispatch flag at `+0x24`, no mention of a publish/send path.

### Static Ghidra check of the animation path

Queried 1.16.2 Ghidra MCP on `localhost:8765`.

- `getDecompiledCode 0x14043a530` (`FUN_14043a530`, likely `CSChrEventModule::RequestAnimation`): gets `ChrOwner`, calls `SetRendererVisibility`, stores `param_1->requestAnimationId = iVar1` and mirrors it through owner `field3_0x18->requestAnimationId`. No network/session calls.
- `getDecompiledCode 0x14043a580` (`CSChrEventModule::Update`): clears `field5_0x24 bit 0`, checks `requestAnimationId != -1`, gates on dead/action flag/throw, then calls `FUN_14041aef0` and `FUN_140c14400`; sets local action flags and local `idleAnimId`, sets dispatch bit, resets request field. Callees are `GetChrOwner`, `FUN_140c14400`, `FUN_140264a40`, `FUN_14041aef0`, `IsInTrow`, `IsDead`, `FUN_14043acc0`. No `CSSessionManager`, `P2PBroadcast`, packet sender, or `PlayerNetworkSession` call.
- `getDecompiledCode 0x140c14400`: formats/resolves the event and calls `fireHkbEvent_C(*(hkbCharacter+0xd0), eventId)`. No network calls.
- `getDecompiledCode 0x140c14370` (`PlayAnimationByBehaviorName`): resolves a behavior event name and calls `fireHkbEvent_C`; no network calls. Callers include throw/ride/mount paths, but the direct call itself is local behavior dispatch.

Interpretation: the current two firing paths are local behavior-dispatch mechanisms. If a publish path exists, it is not "nearby" in either function's call tree.

### Damage is already statically settled: NPC attacks cannot damage remote players from the possessor machine

- `crates/er-npc-possess/src/possess/netdamage.rs:36-57`: remote-player victim + NPC attacker selects mode 0 (`ComputeOnly`), computes damage and drops it; no `HitChr`, no `Packet15`, no vitals packet. Only local `PlayerIns::IsMainPlayerIns` attacker category enters mode 4 (`SendPvpDamage`) and sends `Packet15`.
- `crates/er-npc-possess/src/possess/netdamage.rs:61-74`: even a forged `Packet15` requires both victim and dealer handles to resolve via `GetChrInsByP2PEntityHandle`; crate-spawned dynamic NPCs use invalid `blockId = 0xffffffff` and peers cannot resolve them.
- `crates/er-npc-possess/src/possess/netdamage.rs:82-85`: map-placed NPCs have real handles, but the swing itself does not: attacks are fired by local `requestAnimationId`, so peers animate their own owner-driven copy.
- `crates/er-npc-possess/src/possess/netdamage.rs:264-274`: transcribed route table `DAT_142a36400`; row remote-player victim (`Category::RemotePlayer = 2`) has mode 0 for NPC attacker columns 3 and 4.
- `crates/er-npc-possess/src/possess/game.rs:1476-1502` and `layout.rs:1134-1175` read the incoming `Packet15` buffer only as an oracle, not as a route to publish NPC damage.

### Dynamic spawn is local for this crate

- `crates/er-npc-possess/src/spawn/game.rs:180-218` creates a creature with `SPAWN_DYNAMIC_CHR_RVA` (`WorldChrManImp::SpawnDynamicChr`, 1.16.2 RVA `0x506f30`) and returns a local `ChrIns`/slot.
- `netdamage.rs:70-74` records the decisive network identity issue: dynamic spawn's `P2PEntityHandle` uses `blockId = -1 / 0xffffffff`, which the receiver-side damage forwarding tests as invalid. That same identity problem applies to animation/visual replication: unmodded peers need a resolvable entity to apply any state to.
- Ghidra `CreateBuddyFromPacket` (`0x1404b7780`) exists for vanilla summon/buddy packet creation, but the crate does not use that packet path; it calls `SpawnDynamicChr` directly. `CreateBuddyFromPacket` is reached from `UpdateSpiritSummons` and calls `CreateSummonChr` from a `BuddyPacketEntry` linked to a creator `PlayerIns`/Steam ID, not from this crate's local dynamic spawn helper.

## Ghidra network-sync findings

### Behavior-state / position sync exists, but inspected senders are not arbitrary NPC animation publish

Relevant static queries:

- `SendPacket25 @ 0x1404e9610`: signature decompiled as `SendPacket25(..., NetPackingVector<CS::BehaviorStateSyncInfo,unsigned_char>*, u16)`. It packs a `NetPackingVector<CS::BehaviorStateSyncInfo>` and sends packet `0x19` via `CSSessionManagerImp::P2PBroadcast` or `P2PSendToSteamId`. This is the nearest obvious "behavior state" packet.
- `FUN_1403d9bb0 @ 0x1403d9bb0`: caller of `SendPacket25`. It takes a `PadManipulator*`, gets `owningChr->componentContainer->behaviorSync`, calls `FUN_1404237c0`/`FUN_140423840`, and sends packet 25. Callers `FUN_140c9f430` and `FUN_140c9f770` first fetch `GLOBAL_WorldChrMan->mainPlayerIns`, then `ChrIns::GetManipulator`, then require `GetManipulatorType == PAD`. That is main-player synchronization, not a generic NPC publish hook.
- `FUN_1404e5860 @ 0x1404e5860`: packs a `ChrIns` into a `ChrPackingStructure`: location, rotation, optional equipment/spell for `IsPlayerIns`, behavior sync via `FUN_140423840(param_3->componentContainer->behaviorSync, 1, param_2)`, HP values, look direction, locked-on flag. This proves behavior state can be embedded in a character sync packing structure.
- `FUN_1404e4d20 @ 0x1404e4d20`: replay/character sync recorder for `param_1->owner`; it calls `FUN_1404e5860` for owner and only additionally for a mount/ride forwarding target. The decompiled labels/fields call it `ReplayRecorder`, but it uses network-facing packing helpers. The inspected path does not enumerate arbitrary map NPCs; it packs owner + mount.
- `BroadCastPacket41 @ 0x1404e6310`: broadcasts packet `0x29` with subtype `4`, a `P2PEntityHandle`, and copied payload. Receiver `FUN_1404e71d0` resolves the handle via `GetChrInsByP2PEntityHandle`, checks `IsNpc`, then calls a populate routine. This is an existence proof of P2PEntityHandle-addressed NPC state, but not yet proof that an arbitrary request-animation can be published through it.

Interpretation: There are native packets for player/mount behavior state and some NPC-handle-addressed state. The current possession implementation does not call them, and the obvious behavior send path is anchored on `mainPlayerIns`/`PadManipulator`, not the possessed creature.

### Ride / throw network path is a special case, not general NPC attack replication

- `PlayAnimationByBehaviorName` callers include `MountNetwork? @ 0x140477f40` and throw functions. Decompiling `0x140477f40` shows a `Packet18` ride-state handler that resolves two `ChrIns`, sets throw/ride state, uses `CSChrBehaviorSyncModule::SetIgnorNetStateSyncTime_ForThrow`, and plays specific ride animations (`W_RideOn`, `W_Ride_Enemy_On`, `W_Ridden_Enemy_On`) on receiver.
- That demonstrates vanilla can receive a high-level packet and then locally play specific animations on involved entities. It does **not** provide a generic "play NPC animation id X" packet for arbitrary attacks; it is a ride/throw-specific state machine with packet payload fields and validation.

## Host vs invader split

### Invader possessing a map NPC

Static verdict: **No, not by current mechanism.**

Reasoning:

- The invader's process can write its local copy's `requestAnimationId`, and that local copy will run the clip/VFX/SFX/root motion locally.
- The host/unmodded peers own or simulate their own copy from vanilla network authority. The inspected `requestAnimationId`/`PlayAnimationByBehaviorName` paths have no packet send.
- Damage routing independently refuses remote-player-victim / NPC-attacker damage from the invader machine.

Likely solution space, if pursuing: stop treating the NPC swing as NPC-owned. Either make the player's `PlayerIns` own the externally visible action (for damage this means player-owned hit/bullet/packet, not relabelling an NPC swing), or reverse a native host-accepted packet for a specific event class. That would be a new network feature, not a small patch to `requestAnimationId`.

### Host possessing a map NPC

Static verdict: **Possibly feasible only through native owner/sync machinery; not proven and not current behavior.**

Reasoning:

- Host is the side unmodded clients are most likely to accept for map NPC state, and Ghidra shows vanilla packing of behavior state into character sync structures (`FUN_1404e5860`) plus packet41/P2PEntityHandle-addressed NPC state.
- But current field write is only local; there is no proof that writing `requestAnimationId` marks the NPC's `CSChrBehaviorSyncModule` dirty or feeds a packet clients consume for arbitrary NPC attack animation/VFX/root motion.
- The obvious behavior packet sender (`SendPacket25` path) is player/PadManipulator-oriented. The owner/mount `FUN_1404e4d20` packing path may be reusable or may be replay/ghost-only; this leaf remains unresolved.

If next work focuses on host/map NPC, the decisive question is whether host-owned map NPCs have a periodic `ChrSyncPacker`/`CSChrBehaviorSyncModule` publish path for behavior state, and whether `requestAnimationId` changes the packed behavior state that clients apply. Static RE should start from `FUN_1404e4d20`, packet41 receiver subtype handlers, `CSChrBehaviorSyncModule` dirty flags at `+0x1d..+0x1f`, and xrefs to `BroadCastPacket41`/`FUN_1404e5860`/`FUN_1404e5800`.

### Dynamically spawned NPC

Static verdict: **No for unmodded peers.**

Reasoning:

- The crate calls `SpawnDynamicChr`, giving itself a local `ChrIns` in the buddy roster. It does not broadcast a vanilla buddy/summon creation packet.
- Existing netdamage RE records the dynamic handle as `blockId = 0xffffffff`; receivers reject/unresolve it.
- Without a peer-resolvable entity identity, there is nowhere for a peer to apply animation/VFX/root/damage state.

## Exact unresolved leaves

1. **Packet41 subtype 4 / NPC populate semantics.** `BroadCastPacket41` and receiver `FUN_1404e71d0` show a P2PEntityHandle-addressed NPC state payload. Need determine what payload fields are copied, whether it carries behavior state/event ids, and who sends it in normal gameplay (`CopyAnother`, `FUN_1406535a0`, `FUN_140653960`).
2. **BehaviorSync dirty flags.** Need xrefs/writers to `CSChrBehaviorSyncModule+0x1d/+0x1e/+0x1f` and the backing `field1_0x10`. Current queries show packers read them, but not what sets them after a local `fireHkbEvent_C`.
3. **Receiver for packet25 / behavior-state application.** `SendPacket25` is clear, but receiver/application path for packet `0x19` was not fully identified in this pass. Need map where packet25 is dequeued and how it resolves target identity. The send path inspected is main-player/PadManipulator.
4. **Host-owned map NPC periodic sync.** Need decide whether map NPCs (not player/mount/replay ghost) are packed by `FUN_1404e4d20` or another parallel `ChrSyncPacker` path. If they are not, host possession cannot replicate arbitrary attacks without inventing a packet path.
5. **Seamless overlay behavior.** This pass inspected vanilla 1.16.2 Ghidra and local crate evidence, not Seamless `ersc.dll` packet forwarding/transforms for NPC sync. Seamless may transport vanilla packets, but no Seamless-specific NPC animation extension was found or proven here.

## Follow-up subagent prompts for parent orchestration

### Prompt A: packet41 NPC-state payload

Goal: Determine whether vanilla packet41 (`0x29`) subtype 4 can carry NPC animation/behavior/VFX/root-motion state that unmodded peers apply.

Context/evidence:
- `BroadCastPacket41 @ 0x1404e6310` writes subtype `4`, `P2PEntityHandle`, payload, then `P2PBroadcast(0x29, buffer, size+9)`.
- Receiver `FUN_1404e59e0` dequeues `0x29`, switches subtype, subtype 4 calls `FUN_1404e71d0`.
- `FUN_1404e71d0` reads handle, `GetChrInsByP2PEntityHandle`, checks `IsNpc`, calls `FUN_1406573c0` then `CS::PlayerIns::PopulateFromPcInfo`-typed routine.

Tasks:
- Decompile `CopyAnother @ 0x140653920`, `FUN_1406535a0`, `FUN_140653960`, `FUN_1406573c0`, and the populate routine called by `FUN_1404e71d0`.
- Identify payload struct fields and whether behavior state, animation ids, VFX/SFX/TAE/root motion are present.
- State whether a host-side possess DLL could call this with a map NPC handle and unmodded clients would apply the visual result.

Validation: static Ghidra evidence only; no runtime launch.

### Prompt B: BehaviorSync dirty flags and packet25 receiver

Goal: Determine whether a local NPC `fireHkbEvent_C`/`requestAnimationId` mutates `CSChrBehaviorSyncModule` so vanilla behavior sync packets can publish it.

Context/evidence:
- `CSChrBehaviorSyncModule` structure has pointer at `+0x10`, float `ignorNetStateSyncTime_ForThrow` at `+0x18`, bools/flags at `+0x1d..+0x1f`.
- `FUN_1404237c0` reads flags `+0x1d/+0x1e/+0x1f` and appends behavior state sync info.
- `FUN_140423840` reads behavior state into a `ChrPackingStructure` if `+0x1f` and current state are valid.
- `SendPacket25 @ 0x1404e9610` sends behavior state packet `0x19`; current caller path `FUN_1403d9bb0` is main-player PadManipulator.

Tasks:
- Find all writers to `CSChrBehaviorSyncModule+0x1d..+0x1f` and `+0x10`.
- Find packet25 receiver/dequeue/application path and target identity resolution.
- Decide whether an arbitrary map NPC can be packed/sent/applied without the peer DLL.

Validation: static Ghidra evidence only; no runtime launch.

### Prompt C: host vs invader native ownership for map NPCs

Goal: Decide whether network ownership of map-placed NPCs can be transferred or used so a possessing client becomes authoritative for visual state.

Context/evidence:
- `netdamage::Category::LocalNpc/RemoteNpc` depends on `FUN_1403f3e30` / `FUN_140508b70(WorldChrMan, p2pHandle)` ownership.
- Damage table says ownership does not help NPC->remote-player damage, but visual/position sync may still be owner-directional.

Tasks:
- Decompile `FUN_1403f3e30`, `FUN_140508b70`, and relevant P2P ownership setters/packet handlers.
- Determine whether map NPC ownership can change in vanilla/Seamless and whether unmodded peers consume animation/behavior from non-host owners.
- Produce separate verdicts for host possession and invader possession.

Validation: static Ghidra evidence only; no runtime launch.

## Suggested implementation direction if someone proceeds

Do not try to "make `requestAnimationId` networked" by broadcasting that field blindly. Unmodded peers need a packet they already parse, a peer-resolvable entity, and state that lands in the right native module. The most plausible host-only path, if any, is to drive/extend an existing native character sync/behavior sync publisher for host-owned map NPCs. The invader path probably requires re-expressing the attack as player-owned state or a vanilla accepted special-case packet, not NPC-owned animation.

## Commands run

- `$HOME/.local/bin/bd prime` -- loaded repo workflow/memory context.
- `read`/`grep` tool inspections of `crates/er-npc-possess/src/possess/mod.rs`, `moveset/mod.rs`, `possess/game.rs`, `possess/netdamage.rs`, `possess/layout.rs`, `spawn/game.rs`, `spawn/request.rs`, and adjacent source/docs.
- `python3 scripts/ghidra/mcp_query.py getFunctionByAddress/getDecompiledCode/getXrefsTo/searchFunctionsByName ...` for:
  - `0x14043a530` (`FUN_14043a530`, RequestAnimation-like)
  - `0x14043a580` (`CSChrEventModule::Update`)
  - `0x140c14400` (`W_Event` behavior event dispatch)
  - `0x140c14370` (`PlayAnimationByBehaviorName`)
  - `0x140477f40` (`MountNetwork?`, packet18 ride state)
  - `0x1404e9610` (`SendPacket25`)
  - `0x1403d9bb0`, `0x140c9f430`, `0x140c9f770` (main-player behavior sync send callers)
  - `0x1404231a0`, `0x1404237c0`, `0x140423840` (`CSChrBehaviorSyncModule` helpers)
  - `0x1404e5860`, `0x1404e5800`, `0x1404e4d20` (`ChrPackingStructure`/behavior packing)
  - `0x1404e6310`, `0x1404e59e0`, `0x1404e71d0` (packet41 NPC-handle state path)
  - `0x1404b7780` (`CreateBuddyFromPacket`)
  - `0x1409fba60` (`BroadCastNpcLeavePacket`)
  - `0x140c9b020` / `0x140c9eef0` (packet32 quickmatch chr-event id; inspected and ruled unrelated)
- `git status --short` -- showed substantial pre-existing unstaged/untracked work plus this handoff path.
- `git diff --cached --quiet; echo staged=$?` -- returned `staged=0`.

## Validation and limitations

- No Elden Ring launch, runtime probe, Frida attach, or source edit was performed.
- The handoff file is the only intended artifact from this subagent.
- Static evidence is enough to reject "current field-write magically replicates to unmodded peers."
- Static evidence is not enough to completely reject a host-only implementation through native character sync; that is why the packet41/BehaviorSync/ownership follow-ups are listed.
