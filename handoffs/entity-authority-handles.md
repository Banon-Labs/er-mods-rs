# Entity/handle authority handoff for `er-npc-possess`

Task: determine what entity identity and network authority constraints block or allow unmodded peers to resolve the possessed body. Static-only; no Elden Ring launch.

## Bottom line

1. **Current dynamic spawn path is local-only for unmodded peers.** `er-npc-possess` calls `WorldChrManImp::SpawnDynamicChr` (`0x140506f30`), which forwards to `ChrSet` dynamic spawn `FUN_140492a90`. That constructor builds the spawned creature's `P2PEntityHandle` with `blockId = -1 / 0xffffffff` and the local buddy `ChrSet` slot. Peers cannot resolve that identity unless they have independently instantiated a matching entity/buffer. Forging only the handle is not enough.
2. **Map-placed NPC possession is the cleanest unmodded-peer route for visuals/state.** Those entities already exist on peers with valid map/block-backed handles. If the owning machine can get local-control ownership, stock `ComManipulator` publishing can send placement/behavior over NetChrSync. This does not solve PvP damage from the creature, because the damage router still classifies the attacker as an NPC and drops NPC->remote-player damage.
3. **A dynamic creature can plausibly be made unmodded-peer-visible only by using an existing spawn protocol, not by handle forgery.** The strongest static route is the spirit/summon buddy path: packet 78 carries `BuddyPacketEntry` with `npcParam`, `npcThinkParam`, `charaInitParam`, `FieldInsHandle`, map/position/yaw; `CreateBuddyFromPacket` on peers calls `CreateSummonChr(..., fromNetwork=true)`, then `NetChrSync::SetupForEntityHandle`. This is a stock channel that can instantiate an arbitrary creature model from `npcParamId`. It needs a separate proof that Seamless preserves packet 78 and that the chosen slots/handles align with NetChrSync.
4. **The player's own handle cannot make unmodded peers render the player as a creature.** Player sync constructs/updates a `PlayerIns`; the model/appearance payload has player equipment/face/chrType, not NPC model id. A player handle can publish the Tarnished's transform and can carry PvP damage only when the actual attacker is the local `PlayerIns`, not a relabelled NPC swing.
5. **Ownership is not intrinsically host-only.** `FUN_140508b70` is a local-control query over NetChrSync. Ownership packet logic (`0x16`/`0x17`, funcs below) arbitrates claims by priority/tie-break. Prior memory and current decompilation show guests can participate in ownership arbitration, but what sets priority and how Seamless changes this remain unproven.

## Code evidence

### Spawn path and current invalid-handle blocker

- `crates/er-npc-possess/src/spawn/game.rs:97` defines `SPAWN_DYNAMIC_CHR_RVA = 0x0050_6f30`, documented as `WorldChrManImp::SpawnDynamicChr`.
- `crates/er-npc-possess/src/spawn/game.rs:151-236` calls that function after capacity checks, then locates the creature in the buddy `ChrSet`.
- `crates/er-npc-possess/src/possess/layout.rs:197-245` documents the dynamic spawn band: `FUN_140492a90` scans buddy `ChrSet` entries `6..0x14` without capacity checks; constants are `BAND_FIRST = 6`, `BAND_END = 0x14`.
- `crates/er-npc-possess/src/possess/netdamage.rs:62-76` states the decisive blocker: dynamic spawn builds `P2PEntityHandle(&handle, blockId = -1, chrSetIndex, index)`, so `GetChrInsByP2PEntityHandle` on another machine has nothing to return.

Ghidra 1.16.2 decompilation confirms the source of that invalid handle:

```text
FUN_140506f30(WorldChrManImp*, ChrSpawnRequest*)
  pCVar1 = FUN_140492a90(&param_1->buddyChrSet,param_2);
  if (pCVar1 != null) pCVar1->vft[0x610](..., param_1->field205_0x1e610);

FUN_140492a90(ChrSet*, ChrSpawnRequest*)
  index = 6; chrSetEntry = entries + 6; loop while index < 0x14
  FieldInsHandle::SetSelector(&local_98,param_1->chrSetIndex,index);
  local_res18.entityHandle = -1;
  P2PEntityHandle(&local_res20,(BlockId *)&local_res18,param_1->chrSetIndex,index);
  CreateCharacter(..., local_98, local_80 /* p2p handle */, ...)
```

### Handle resolver shape

Ghidra 1.16.2, `WorldChrManImp::GetChrInsByP2PEntityHandle @ 0x140507db0`:

```text
if (!P2PEntityHandle::HasChrSelector(handle)) return null;
chrSetIndex = P2PEntityHandle::GetChrSetIndex(handle); // high selector bits, not slot bits
worldBlockCount = worldInfo.worldBlockInfoCount;
if (chrSetIndex == worldBlockCount) {
  return GetPlayerInsByCharacterEventId(...);
}
if (chrSetIndex == worldBlockCount + 4) {
  chrSet = world_chr_man->chrSets[chrSetIndex];
  index = chrSet->GetIndexByFieldInsHandle(MakeFieldInsHandle(handle));
} else {
  chrSet = world_chr_man->chrSets[chrSetIndex];
  index = P2PEntityHandle::GetChrSetIndex_low11(handle); // low selector bits
}
return chrSet->SafeGetChrInsByIndex(index);
```

`P2PEntityHandle::MakeFieldInsHandle @ 0x1404db780` preserves real `blockId` when it is not `0xffffffff`, but for invalid block id it falls back to `FieldInsHandle::SetSelector(container, index)`. That makes local slots addressable only in a local `ChrSet`; it does not create a peer-side entity.

`P2PEntityHandle::IsSummonBuddy @ 0x1404db800` requires:

```text
chrSetIndex == worldBlockInfoCount + 2
selector low11 > 0x13 && low11 < 0x50
```

Current dynamic spawns land in slots `6..0x13`, explicitly outside that summon-buddy range.

### Current possession keeps player identity separate

- `crates/er-npc-possess/src/possess/game.rs:1216-1223`: `main_player()` returns the actual `PlayerIns`/`ChrIns` from `WorldChrMan.main_player`.
- `crates/er-npc-possess/src/possess/game.rs:1225-1246`: `set_camera_override` writes only `WorldChrManDbg+0xb8`; docs say `GetMainPlayerIns` camera/lock-on consumers follow it, while `PlayerIns::IsMainPlayerIns` and damage/save identity consumers keep using the raw main player.
- `crates/er-npc-possess/src/possess/game.rs:710-748`: `request_move` co-locates the player's real body by writing the same `ChrCtrl` proxy fields drained by `ChrCtrl::UpdatePositions`; docs note unmodded clients see the possessing player standing at the creature. This is player-body transform visibility, not creature model visibility.
- `crates/er-npc-possess/src/possess/game.rs:1318-1418`: target search excludes `player`, `ghost`, `summon_buddy`, and `debug` ChrSets and walks open-field/map NPC ChrSets. This already supports a map-placed-NPC-only mode if product wants to avoid dynamic spawns.

### Damage routing evidence

- `crates/er-npc-possess/src/possess/netdamage.rs:1-127` has the static damage-model write-up. Important cells:
  - victim = remote player, attacker = NPC local/remote => mode `0`, compute-only, no `HitChr`, no packet.
  - only attacker category `MainPlayer` can send PvP damage to a remote player.
- `crates/er-npc-possess/src/possess/netdamage.rs:62-76` explains why a forged `Packet15` still fails for current dynamic spawn: receive resolves both victim and dealer with `GetChrInsByP2PEntityHandle` before applying HP changes.
- `crates/er-npc-possess/src/possess/layout.rs:1141-1172` records the Packet15 receive buffer fields and the exact `GetChrInsByP2PEntityHandle` reads for victim/dealer handles.

## Static RE evidence gathered in this pass

All Ghidra queries were against the named 1.16.2 daemon on `localhost:8765` unless noted. 1.17 mappings below are candidates from `scripts/map-rvas-1162-to-1170.py` unless the existing verified table is cited.

### Local-control query and ownership

`FUN_140508b70 @ 0x140508b70` (1.17 candidate `0x140509940`):

```text
bool FUN_140508b70(WorldChrManImp *wcm, P2PEntityHandle *h) {
  b = FUN_1404ddfc0(wcm->netChrSync, h);
  P2PEntityHandle::~P2PEntityHandle(h);
  return b;
}
```

Callers: `FUN_1403f3e30` (the local/remote NPC classifier used by damage) and `IsValidForThrowNetworking @ 0x1403f4120`.

`FUN_1404ddfc0 @ 0x1404ddfc0` (1.17 candidate `0x1404ded90`) checks the NetChrSync local-control flag array:

```text
chrSetIndex = P2PEntityHandle::GetChrSetIndex(h);
if (chrSetIndex == worldBlockInfoCount + 3) return true;
if (chrSetIndex < 0 || chrSlotCount <= chrSetIndex || chrSetSync[chrSetIndex] == null) return false;
return FUN_1404da910(chrSetSync[chrSetIndex], clonedHandle); // local-control/readback flag lookup
```

`docs/recon/npc-possess-1170-address-table.md:464-468` already has verified 1.17 entries for related ownership functions:

- `NetChrSync::ProcessOwnershipRequests22`: `0x1404e2140 -> 0x1404e2f10`
- `NetChrSync::ProcessOwnershipRequests23`: `0x1404e0ab0 -> 0x1404e1880`
- `GetOwnershipDataByP2PEntityHandle`: `0x1404d4a00 -> 0x1404d57d0`
- `NetChrSync::SetChrSyncLocalControlFlagOn0x18`: `0x1404de7f0 -> 0x1404df5c0`

`GetOwnershipDataByP2PEntityHandle @ 0x1404d4a00` linearly searches ownership entries by exact `P2PEntityHandle::Eq`.

`FUN_1404d4ff0 @ 0x1404d4ff0` compares two `NetChrSyncOwnership` records: if both claim a handle, higher `priority` keeps local control; equal priority tie breaks by relative count, clearing `locallyControlled` on one side. This is arbitration, not a host-only constant.

`FUN_1404e2270 @ 0x1404e2270` sends ownership packet `0x16` to other Steam IDs; `FUN_1404e0cf0 @ 0x1404e0cf0` sends packet `0x17` and applies `SetChrSyncLocalControlFlagOn0x18` for handles whose owner is self.

### Enemy transform/behavior publishing

`FUN_1404e1e40 @ 0x1404e1e40` (1.17 candidate `0x1404e2c10`) sends placement packet type `4`:

```text
for active NetChrSetSyncs / chr slots:
  chr = chrSet->SafeGetChrInsByIndex(slot)
  handle = ChrIns::GetP2PEntityHandle(chr)
  placement = NetChrSetSync::GetPositionUpdateBuffer(..., handle)
  flags = NetChrSetSync::GetReadbackFlagsForChrIns(..., handle)
  if placement && flags && (*flags & 2) && ChrIns::IsValidForThrowNetworking(chr):
    pack(handle); pack(placement); clear bit 2
CSSessionManagerImp::P2PBroadcast(..., 4, buffer, len)
```

`FUN_1404e1bc0 @ 0x1404e1bc0` receives packet type `4`, requires `HasChrSelector`, valid packed position, `chrSetIndex` in range, and existing `chrSetSync[chrSetIndex]`, then writes `NetChrSetSync::GetPositionUpdateBuffer` and marks readback flag bit 1.

`FUN_1404dfc60 @ 0x1404dfc60` sends behavior packet type `0x46` from `NetChrSetSync0x28Entry` when readback flag bit `8` is dirty. This is the behavior/animation side of enemy sync.

These paths require a resolvable handle and a `NetChrSetSync` entry. A forged handle that points at no peer-side `ChrIns` does not help.

### Stock buddy/summon dynamic-spawn route

`BroadcastBuddySummon @ 0x140c9e9b0` wraps one `BuddyPacketEntry` and calls `BroadcastPacket78`.

`CreateBuddyFromPacket @ 0x1404b7780` receives that packet, looks up the creator by `steamId`, then calls:

```text
CreateSummonChr(manager,
  creatorEventId, creatorSteamId, &packet.mapId, packet.field_0x18,
  &packet.fieldInsHandle, packet.npcParam, packet.npcThinkParam,
  packet.charaInitParam, pos, packet.yaw, ..., fromNetwork=true, ...)
```

`CreateSummonChr @ 0x1404ba980`:

- Derives model name from `npcParamId` on the creature path (`FUN_140d409c0` / `FUN_1404b6eb0`).
- Uses passed `FieldInsHandle` if it is not `-1`; otherwise selects a local player summon buddy slot via `CSSessionManager::GetLocalPlayerSummonBuddyChrSetIndex` and `ChrSet::SpawnChr`.
- After spawn, calls `ChrIns::GetP2PEntityHandle` and `NetChrSync::SetupForEntityHandle`, stores `chrCreaterSteamId`, applies creator team when spirit-summon networking is enabled, then runs vft `+0x610` setup.

This is the only statically observed stock path that both creates a dynamic creature on peers and integrates it into NetChrSync. It is plausible, not yet product-proof, because Seamless packet preservation and slot/authority details are unproven.

## Entity/authority model

### Identities

- **Player identity:** `PlayerIns` in `WorldChrMan.player_chr_set`, found by Steam ID / character event ID. Player P2P path sends transforms and PvP damage for the actual local player. It cannot express an NPC model.
- **Map NPC identity:** `EnemyIns` in open-field/map `ChrSet`s. Peers already have corresponding `ChrIns` and real map/block-backed handles. NetChrSync can publish placement/behavior if local-control ownership allows it.
- **Current dynamic possession spawn identity:** `EnemyIns` in buddy `ChrSet` slots `6..0x13`, invalid block id, no peer-side entity. Local-only unless a separate stock spawn packet creates the same entity on peers.
- **Stock summon buddy identity:** dynamic `EnemyIns` created from `BuddyPacketEntry`, with `FieldInsHandle`/buddy slot and `NetChrSync::SetupForEntityHandle`. Peer-visible in stock code path if packet reaches peers.

### Authority constraints

- `FUN_140508b70` asks NetChrSync whether this client locally controls a handle; damage classification and throw/network validity depend on it.
- Local-control is stored in `NetChrSetSync +0x18`-style flag arrays indexed by `P2PEntityHandle::GetChrSetIndex`.
- Ownership broadcast packets `0x16`/`0x17` advertise/resolve ownership by Steam ID and handle. Static RE shows priority/tie-break arbitration; it does not show a host-only guard.
- Publishing packets require dirty readback flags and `IsValidForThrowNetworking`, so making a body visible is not just moving memory; it must be owned or marked locally controlled enough for stock senders to accept it.

## Host vs invader feasibility

### Host possessing a map-placed NPC

Feasibility: **best static route for unmodded-peer visuals**.

Why:

- Map enemies are already in the host world and have valid handles on peers.
- Host likely starts as or can become owner for many map NPCs; NetChrSync already sends enemy placement/behavior.
- Existing possession driver can drive a map-placed `EnemyIns` without spawning anything.

Blockers/limits:

- PvP damage from the creature still does not land on remote players: attacker is NPC category, remote-player victim row chooses mode 0.
- Peer sees the actual player body too unless peer-side mod hides it; player body co-location is the only thing stock player sync publishes.
- Need proof that manual possession writes cause the correct NetChrSync dirty flags/behavior entries when owning a map NPC. The ComManipulator slot `+0x68` should publish, but exact dirty-bit generation for this possession path needs a static or runtime oracle.

### Invader/guest possessing a map-placed NPC

Feasibility: **plausible for visuals, not settled**.

Why:

- Ownership machinery is not host-privileged in the decompiled shape; guests can have `NetChrSyncOwnership` records and local-control flags.
- Prior memory says a guest can legitimately own a map enemy; current decompilation supports arbitration by priority/tie-break rather than role.

Blockers/unknowns:

- What sets ownership `priority` is not traced.
- Seamless may preserve, alter, or suppress packets `0x16`/`0x17`, packet `4`, packet `0x46`, or packet `78`. Static plaintext evidence only shows Seamless wraps native Steam session classes; it does not settle behavior.
- In an invasion, the invader's process may have a remote copy of host-world map enemies but not authority. Need static trace of role/world owner gates (`EcTestIsMyWorld`, `EcTestIsMyMapOwner`, multiplayer state) or a two-client proof.

### Dynamic spawn with current `SpawnDynamicChr`

Feasibility: **not peer-visible to unmodded clients as currently implemented**.

Reason:

- Invalid block id, local buddy slot `6..0x13`, outside `IsSummonBuddy` range, and no peer-side instantiation. Incoming placement/damage/event packets with that handle cannot resolve a `ChrIns` on peers.

### Dynamic spawn through stock buddy/summon path

Feasibility: **plausible route; strongest unproven dynamic route**.

Reason:

- Stock packet 78 can instantiate a creature by `npcParam` on peers and then call `NetChrSync::SetupForEntityHandle`.
- It can carry arbitrary creature identity via `npcParamId` model derivation.

Risks:

- May be constrained by spirit-summon state/session rules, buddy slot availability, host/guest role, or Seamless filtering.
- Needs exact `BuddyPacketEntry` layout and send/receive conditions, not just address calls.
- Product may need to use game-owned summon slots (`>0x13 && <0x50`) instead of current dynamic band.

### Player handle as proxy

Feasibility: **only for transform/damage as the player, not for creature visuals**.

- Transform: yes, current `request_move` makes unmodded peers see the player's normal Tarnished at the creature root.
- Creature visuals: no static field in player appearance sync carries NPC model/chara id.
- Damage: only if attack is actually player-owned. Relabelling an NPC swing or Packet15 dealer handle does not make the attack route mode 4; the damage path classifies live attacker type before packet send.

## Known blockers

- Current dynamic spawned bodies have invalid `blockId = 0xffffffff`; peer-side `GetChrInsByP2PEntityHandle` cannot resolve them.
- Current spawn slots `6..0x13` are outside `P2PEntityHandle::IsSummonBuddy`'s `>0x13 && <0x50` range.
- NPC attacker to remote player damage is compute-only mode 0; no HP changes and no damage packet leaves.
- Player sync cannot encode NPC model identity.
- Seamless behavior for native enemy/ownership/buddy packets is not statically settled from plaintext imports/RTTI alone.
- Ownership priority source remains untraced.
- A peer-side visual solution without peer DLL still leaves double image: stock peer sees the normal player body co-located unless something vanilla hides it, and no such stock hide channel has been found.

## Plausible but unproven routes

1. **Map-only possession mode:** restrict possession target search to map/open-field NPCs with valid P2P handles; acquire/force ownership; rely on stock NetChrSync. Best short path for unmodded peer visuals, not damage.
2. **Buddy packet dynamic route:** replace current `SpawnDynamicChr` path with a stock buddy summon/packet-78 path so peers instantiate the creature, then drive it through NetChrSync ownership.
3. **Hybrid player-damage/creature-visual route:** keep creature visual sync via map/buddy entity but route attacks through actual `PlayerIns` mechanics (for example player-owned bullets/attack objects). Needs fresh RE; current NPC swing field write cannot do this.
4. **Peer-side mod cleanup:** if peers run the mod, hide the co-located Tarnished and/or coordinate explicit creature ownership using stock P2P send/dequeue. This is outside the unmodded-peer goal.

## Suggested follow-up subagent prompts

### Subagent A: map-placed NPC ownership feasibility

Goal: statically prove how a client takes or loses local-control ownership for an existing map `EnemyIns`, including host vs invader role gates.

Start files/functions:

- `FUN_140508b70`, `FUN_1404ddfc0`, `FUN_1404de7f0`, `FUN_1404e2270`, `FUN_1404e0cf0`, `FUN_1404d4ff0`
- callers of `NetChrSync::SetChrSyncLocalControlFlagOn0x18`
- `docs/recon/npc-possess-1170-address-table.md` ownership rows
- Seamless-facing memories: `enemy-chrsync-ownership-is-arbitrated-packet22-2026-09-01`, `possess-net-sync-verdict-buddy-summon-is-the-seam-2026-09-02`

Questions:

- What writes ownership `priority`?
- Is there a role/world-owner check before a guest can claim map enemy ownership?
- Which update path sets dirty flags after `ComManipulator` movement/behavior when local-control is true?
- Can map-only possession be implemented by filtering existing target search, or does it need explicit ownership packet/send calls?

Validation: Ghidra decompilation snippets plus 1.17 mappings; no runtime.

### Subagent B: buddy packet dynamic spawn route

Goal: trace packet 78 end-to-end and determine whether `er-npc-possess` can create a peer-visible dynamic creature using stock summon-buddy code.

Start functions:

- `BroadcastBuddySummon @ 0x140c9e9b0`
- `BroadcastPacket78` and receive dispatcher for packet 78
- `CreateBuddyFromPacket @ 0x1404b7780`
- `CreateSummonChr @ 0x1404ba980`
- `ChrSet::SpawnChr @ 0x140492e20`, `ChrSet::SpawnSummonBuddy @ 0x140492cb0`
- `P2PEntityHandle::IsSummonBuddy @ 0x1404db800`

Questions:

- Exact `BuddyPacketEntry` layout and required fields.
- Which caller conditions allow `BroadcastBuddySummon` outside normal spirit ash use?
- What slot/handle is assigned when `fromNetwork=true` and when `fieldInsHandle != -1`?
- Does sender need to be host, creator, or local player owner?
- What are the despawn/cleanup packets for buddy entities?

Validation: static trace; optional later two-client runtime only after the packet model is exact.

### Subagent C: player-handle/proxy damage alternatives

Goal: determine whether any stock player-owned attack/bullet route can use player authority while visually driven by a possessed creature.

Start functions/files:

- `crates/er-npc-possess/src/possess/netdamage.rs`
- `CSPlayerDamageModule` slot 21 `0x14044ce40`, Packet15 send/receive `FUN_14050e4a0`
- bullet resolver `FUN_14038b2f0`
- player outbound sync memory `player-net-position-publish-path-2026-09-01`

Questions:

- Can a creature animation spawn a player-owned hit/bullet without peer DLL?
- Is there a field in `AttackDamageInfo` or bullet owner chain that must be actual `PlayerIns` at classification time?
- Is there any native transform/attachment precedent like ride/mount forwarding that can visually connect player-owned damage to creature body?

Validation: static RE; no runtime until a concrete route exists.

## Meta-prompt for the next planning/implementation agent

Goal: choose the narrow next proof target for unmodded-peer possession visibility and damage based on the static entity/authority model.

Context/evidence:

- Current dynamic spawn uses `SpawnDynamicChr` -> `FUN_140492a90` -> invalid-block `P2PEntityHandle`; peers cannot resolve it.
- Map-placed NPCs likely solve identity because peers already have the entity; ownership/local-control is the remaining question.
- Stock buddy packet 78 is a plausible dynamic route because it creates a peer-side creature and calls `NetChrSync::SetupForEntityHandle`.
- Player handle can publish only normal player body/PlayerIns damage; it cannot encode NPC model visuals.
- PvP creature damage remains blocked by damage routing table even if visuals sync.

Success criteria:

- Produce a decision: map-only first, buddy-packet dynamic first, or player-owned damage route first.
- Name the exact functions/fields to edit or instrument, or explicitly say the next step is more static RE.
- Preserve static-first rule; no Elden Ring runtime launch until the chosen mechanism has a falsifiable oracle.

Hard constraints:

- Do not edit source for this handoff task.
- Do not launch the game for this investigation.
- Do not claim Seamless preserves packets 4/0x16/0x17/0x46/0x4e without a two-client proof or direct `ersc.dll` evidence.
- Do not claim dynamic handle forging works unless the peer-side entity creation path is also proven.

Suggested approach:

1. Prioritize map-only possession if the immediate goal is unmodded-peer visuals with least protocol risk.
2. Prioritize buddy packet 78 if arbitrary dynamic creature identity is required.
3. Treat PvP damage as a separate problem; visual sync success does not imply damage success.

Validation:

- Static Ghidra snippets for chosen route.
- 1.17 mapping through existing verified table or `scripts/map-rvas-1162-to-1170.py`, then read the 1.17 function before any hook/call.
- Host-side unit/static checks only if source changes later.

Stop/escalation rules:

- Stop for parent orchestration if a two-client Seamless runtime is needed.
- Stop if the route requires deciding to sacrifice dynamic-spawn support or peer-unmodded support; that is product scope.
- Enough evidence is reached when each claimed packet/handle route has both sender and receiver traced to a concrete `ChrIns` identity.

Resolved assumptions:

- Current dynamic spawn is not peer-resolvable as-is.
- Map-placed entities are the only route that starts with peer-side identity already present.
- Player proxy is useful for co-located Tarnished transform but not creature rendering.

## Commands run / validation notes

- Read relevant repo instructions and `.auto/prompt.md`.
- `$HOME/.local/bin/bd memories P2PEntityHandle`, `bd recall ...` for ownership/spawn/player-net/damage memories.
- Read `crates/er-npc-possess/src/spawn/{game.rs,mod.rs,placement.rs,request.rs}`.
- Read `crates/er-npc-possess/src/possess/{netdamage.rs,game.rs,layout.rs}` relevant sections.
- Read `docs/recon/npc-possess-1170-address-table.md` ownership rows.
- Queried Ghidra MCP for functions listed above via `python3 scripts/ghidra/mcp_query.py`.
- Ran `python3 scripts/map-rvas-1162-to-1170.py ...` for candidate 1.17 mappings.
- Ran `git status --short` and `git diff --cached --name-only`; many pre-existing unstaged files were present, no staged files were reported before this handoff write.

## Residual risks

- Existing workspace had many unrelated unstaged changes before this handoff; this file is the only intended artifact from this task.
- Seamless packet preservation cannot be settled by this static pass.
- 1.17 addresses from the mapper are candidates unless backed by verified-table rows; read 1.17 before using any address in code.
- No source changes or tests were made because the task requested investigation/handoff only.
