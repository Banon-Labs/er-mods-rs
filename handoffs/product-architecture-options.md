# er-npc-possess unmodded-peer product architecture options

Scope: design only. No source implementation and no runtime launch were performed.

## Bottom line

Full unmodded-peer support is not a truthful product claim for the current possession model. The current DLL makes the local player *control* an NPC by local field writes and a local manipulator vtable swizzle; it does not make remote peers render the player as that NPC, and NPC attacks do not naturally become PvP damage packets.

Smallest plausible products, if any are worth pursuing:

1. **Damage-only translator**: a separate network-damage layer that consumes verified local hit/damage intents and, only after proof, emits a vanilla player-owned PvP damage path. It must be isolated from possession core and default-refuse.
2. **Host-only map-placed NPC visibility**: restrict to a real map NPC with a valid network handle and proven local authority, then drive only that NPC through the game's existing enemy sync channel. It must be marketed as "peers may see this NPC move," not "you appear as the NPC."
3. **Vanilla buddy/replica channel**: possible in the old static RE notes, but too many unresolved gaps to call the smallest product. Treat as a follow-up research lane, not the first product cut.

Every option must keep the user-visible Tarnished/body distinction explicit. A peer seeing the user's ordinary player body co-located inside/near a creature is not success unless the UI says exactly that.

## Evidence and high-value context

### Current possession architecture is local field control, not network identity replacement

Relevant files:

- `crates/er-npc-possess/src/possess/mod.rs:8-23`
  - Possession builds a thunk/vtable patch, installs it on the creature, sets camera override, neuters/co-locates the real player body, writes movement intent into the creature, and fires attacks by writing `CSChrEventModule+0x18 requestAnimationId`.
- `crates/er-npc-possess/src/possess/thunk.rs:1-57`
  - Current settled design swizzles the creature's real `ComManipulator` vtable and only replaces slot `+0x48` (`UpdateAi`). Slot `+0x50` still executes, so local locomotion/execute paths remain native. This is a local object/vtable change.
- `crates/er-npc-possess/src/possess/driver.rs:1091-1238`
  - Per-frame active possession re-neuters the player, reasserts camera/team/camera settings, retargets HUD, co-locates the player with `player.request_move(last_position, creature.yaw())`, writes creature movement intent, and samples net-damage telemetry.
- `crates/er-npc-possess/src/possess/game.rs:720-739`
  - `Chr::request_move` writes `ChrCtrl` proxy position/rotation flags. Comment says the same drain pushes to `WorldChrManImp::GetNetChrSyncPositionUpdateBuffer`, so an unmodded client sees the *player* standing at the creature.
- `crates/er-npc-possess/src/possess/game.rs:1438-1499`
  - Existing telemetry walks `WorldChrMan.player_chr_set` and reads the last received PvP `Packet15` buffer. This is the right evidence seam for any networked behavior gate.
- `crates/er-npc-possess/src/engine.rs:72-136`
  - `PossessionOutcome` and `PossessionEngine` already separate accepted/refused/no-engine, per-frame tick, active state, reload gating, and shutdown. A network layer should not bypass this state machine.

### Damage routing currently proves NPC attacks cannot damage a remote player from this machine

Relevant files and facts:

- `crates/er-npc-possess/src/possess/netdamage.rs:1-89`
  - Remote-player victim + NPC attacker routes to mode 0 (`ComputeOnly`): damage numbers are computed, `HitChr` is skipped, no `Packet15` or vitals packet is built.
  - Only attacker category 1 (`CS::PlayerIns::IsMainPlayerIns`, the local player's own `PlayerIns`) reaches `SendPvpDamage` for a remote-player victim.
  - Spawned creatures have an invalid/unresolvable P2P handle; map-placed enemies have a handle, but this crate's attack is a local `requestAnimationId` write and is not what the peer's copy is animating.
- `crates/er-npc-possess/src/possess/netdamage.rs:480-600`
  - `Ledger::observe` already prints a verdict, remote HP deltas, and the PvP receive buffer state. It distinguishes "packet absent," "packet arrived," and "HP moved."
- Beads memory `possession-pvp-damage-blocked-by-net-routing-matrix-2026-09-02`
  - Static RE settled outgoing damage: NPC->remote-player is computed and dropped; Packet15 receive resolves both handles; current spawned creatures cannot be named on the peer.
- Beads memory `possession-incoming-pvp-damage-unexplained-receive-oracle-2026-09-02`
  - Live A/B later showed possession ON blocks both directions while possession OFF allows damage. Outgoing remains explained by the routing matrix; incoming cause remains not fully explained. Existing Packet15 receive oracle is the next discriminator.

### Spawn path creates local creatures, not peer-visible network creatures

Relevant files:

- `crates/er-npc-possess/src/spawn/mod.rs:1-56`
  - Spawn creates a creature locally, waits for readiness, and removes it later. It deliberately has no residency restriction; missing assets are bounded by readiness deadline.
- `crates/er-npc-possess/src/spawn/game.rs:81-124`
  - Calls `WorldChrManImp::SpawnDynamicChr` and `RemoveChrIns` by verified RVAs.
- `crates/er-npc-possess/src/spawn/game.rs:145-174`
  - Uses `WorldChrMan.summon_buddy_chr_set`, checks capacity, and refuses if the roster is too short because Seamless also lives in this `ChrSet`.
- `crates/er-npc-possess/src/spawn/request.rs:33-46`
  - Spawn request deliberately uses creature branch (`charaInitParam = -1`) and `eventEntityId = 0`; it does not create a map entity identity.
- `crates/er-npc-possess/src/spawn/readiness.rs:1-29`
  - Readiness has no game error edge; it distinguishes ready, vanished, and expired. This pattern should be copied for any network gate: positive proof or explicit refusal, not silent waiting.

### Config and UI already follow useful honesty patterns

Relevant files:

- `crates/er-npc-possess/src/config.rs:73-111`
  - Default config says what works today, what is not wired yet, and that unknown values are reported and ignored.
- `crates/er-npc-possess/src/config.rs:145-180`
  - `[target]` is staged, not live, and `mode = "spawn"` is explicitly described as creating a local creature.
- `crates/er-npc-possess/src/settings.rs:109-151`
  - `TargetMode` already distinguishes lock-on/nearest/crosshair/chr_id/spawn, and `creates()` isolates the mode that creates a character.
- `crates/er-npc-possess/src/settings.rs:197-215` and `settings.rs:300-319`
  - Spawn settings include `readiness_ms`, `despawn_on_release`, and a `summary()` that appears in request logs. Network options should follow this pattern and be summarized in every possession request.

### Static RE memory for peer-visible NPC channel

Beads memory `possess-net-sync-verdict-buddy-summon-is-the-seam-2026-09-02` is the key existing static RE source:

- Player path cannot make a peer render the player as an arbitrary creature. Player network data has no `chrId`/`npcParam` model field; the remote `PlayerIns` already exists before appearance data arrives.
- Native enemy channel exists: ComManipulator `+0x68` publishes enemy placement/behavior/throw/health through NetChrSync packets; NetAIManipulator's publish slot is `ret`, proving publish is ownership-scoped.
- Vanilla buddy summon packet can instantiate an arbitrary creature from `npcParamId`, but unresolved gaps remain: Seamless preservation of those packets, ownership priority, current spawned slot band vs network buddy slot expectations, peer slot derivation, and visual behavior fidelity.
- It explicitly says unmodded peers will still see the ordinary Tarnished co-located/teleported unless they also run a mod that hides it.

## Option A: damage-only translator layer

### Product shape

A network damage mode that does **not** claim visual or animation replication. It only tries to make verified possessed-creature hits produce remote-player HP changes through a vanilla-compatible damage path.

Suggested user-facing copy:

- "Network damage translation: experimental. Peers do not see you as the creature. It only attempts to translate confirmed local creature hits into normal PvP damage."
- Status line must say one of: `disabled`, `armed-unproven`, `refused(reason)`, `sent(candidate)`, `confirmed(remote HP delta)`.

### Architecture

Add a separate module, not code in `driver.rs` or `netdamage.rs` directly:

- likely new file: `crates/er-npc-possess/src/network_damage.rs` or `possess/netdamage_translate.rs`
- keep `possess/netdamage.rs` as current read-only routing/ledger proof source
- add config type, likely `NetworkSettings`, with default `enabled = false`, `damage_translation = "off"` or `"refuse"`
- add a narrow event type from possession core to translator:
  - `DamageIntent { attacker_creature, local_player, victim_player_handle/address, animation, atk_param_id, computed_damage, frame, proof_flags }`
  - no raw "do damage now" calls from `tick_active`
- translator is an independent state machine:
  1. refuses unless build/session/gates are known;
  2. observes a candidate hit and the route verdict;
  3. if a proven player-owned packet/call path exists, emits it;
  4. waits for `Packet15`/remote HP delta proof;
  5. disables itself on mismatch.

### Non-goals

- Do not change current local possession mechanics.
- Do not claim peers see the NPC attack animation.
- Do not forge arbitrary packets until the exact vanilla player-owned damage path is statically mapped and live-proven.
- Do not use a spawned creature's invalid P2P handle as a dealer.
- Do not suppress or bypass current net-damage ledger; the translator depends on it.

### Product restrictions/config gates

Default disabled. Enabling should require all of:

- network session detected and a remote player observed in `WorldChrMan.player_chr_set`;
- `last_received_damage_packet()` gate readable on the running build;
- remote victim has a resolvable player handle/address and is in a damage-enabled relation/team state;
- the hit source is mapped to a verified AtkParam/damage value, not just an animation request;
- translated route is proven to use the local player's own `PlayerIns`/vanilla PvP send path, or it refuses;
- no Seamless/native packet ambiguity for the selected mode.

Explicit refusals:

- unmeasured game build or unresolved packet buffer RVA;
- no remote player observed;
- victim not a remote player;
- creature-only route remains mode 0 and no proven player-owned translation path exists;
- missing hit/damage oracle;
- spawned creature handle is invalid/unresolvable;
- packet sent but no confirmation window can observe a packet/HP delta;
- local player HP/position state is already invalid from possession.

### Risks

- The core missing piece is not packet syntax; it is proving a real hit and attributing it to a remote player without lying.
- Damage could become unfair/desynced if local predicted hits are translated while the remote peer did not see that attack.
- Incoming damage bug remains unresolved; enabling outgoing damage while incoming remains blocked would create one-way PvP unless gated separately.
- Seamless may wrap/alter native damage paths.
- Any packet/call into PvP damage code is high-risk and must be build-gated like other RVAs.

### Validation gates before product enablement

Static gates:

- Unit tests keep route matrix invariants: NPC->remote player is mode 0; local player->remote player is the only send path.
- Static RE identifies the exact vanilla `CSPlayerDamageModule` slot 21 / `SendHitPacket` call contract and all required inputs.
- Static RE identifies a reliable local hit oracle with attacker/victim/AtkParam/damage values.

Runtime gates, later only:

- Two-client proof: possession off PvP baseline works.
- Possession on, translator off reproduces route refusal and no outgoing remote HP movement.
- Translator on: one candidate hit produces exactly one send event and one remote HP delta/packet confirmation.
- Incoming damage remains possible or product refuses "two-way PvP."
- Negative controls: no damage when swing misses; no damage to wrong victim; no damage in friendly/team-disabled session.

## Option B: host-only map-placed NPC visibility

### Product shape

A highly restricted mode for a host/map owner to take over an NPC that already exists in the map and may already have a valid network identity. The claim is not "you become the NPC to peers." The claim is only "the peer-visible NPC may be controlled through the native enemy sync channel."

Suggested user-facing copy:

- "Network visibility: host map-placed NPC only. Peers may see the NPC move if the vanilla enemy-sync ownership gates prove it. They may still see your ordinary character separately."

### Architecture

Do not extend `target.mode = spawn` first. Add a new explicit network visibility mode/gate around already selected targets:

- likely new settings:
  - `[network] enabled = false`
  - `visibility = "off" | "host_map_placed_only"`
  - `damage = "off" | "proof_required"`
- likely new files:
  - `crates/er-npc-possess/src/network_visibility.rs`
  - possible `possess/net_sync.rs` for read-only handle/ownership checks
- possession core keeps selecting and wearing a `Chr`; the network module only decides whether the target is eligible for network visibility.
- eligibility should be snapshotted at `enter()` and rechecked per-frame, similar to current team/camera reassertion.

Eligibility gates:

- target is not created by this mod (`spawned.is_none()` in `Possessing`);
- target has a valid `P2PEntityHandle` with non-`0xffffffff` block id / selector;
- target belongs to a map/ChrSet path native peers can resolve;
- this machine is host/map owner or has proven local-control flag for that entity;
- native enemy publish slot remains intact (`ComManipulator +0x68` path not disabled by possession swizzle);
- ownership arbitration packets/flags are observed as stable;
- Seamless/native session is known to preserve the relevant enemy sync packets.

### Non-goals

- No support for current `mode = "spawn"` creatures.
- No claim that the player renderer changes on peers.
- No remote hiding of the co-located Tarnished on unmodded peers.
- No guest-hostile takeover unless ownership RE proves it and product copy says so.
- No damage claims; pair with Option A only after both gates pass.

### Product restrictions/config gates

Refuse at runtime unless all are true:

- current target came from lock-on/nearest/chr_id and is proven map-placed, not `SpawnedBody`;
- current session role is host/map owner, unless a later RE task proves guest ownership is safe;
- valid, peer-resolvable P2P handle;
- local-control/ownership flag is true and stable for N samples;
- no conflicting mod already owns the creature manipulator/vtable;
- packet/telemetry confirms native enemy sync publishing is happening.

UI/log invariants:

- Do not print "peer-visible" until the handle and local-control flags are true.
- If eligibility drops mid-possession, log `network-visibility: disabled(reason)` and continue local possession only.
- Logs must name the visible entity as "map NPC," not "you."

### Risks

- Current vtable swizzle replaces only `UpdateAi`, but ownership/publish semantics still need proof for the exact target class.
- Host-only claim may still be too broad under Seamless if ERSC changes ownership/map-owner gates.
- Peer might animate its own copy from its own AI/owner if ownership is not actually transferred.
- If peers see both the ordinary Tarnished and the NPC, users may read that as broken unless config/UI says so before enabling.

### Validation gates before product enablement

Static gates:

- Resolve/read `P2PEntityHandle` for a map-placed NPC and prove how to classify invalid vs peer-resolvable handles.
- Resolve/read local-control flag and ownership owner for that handle.
- Prove possession's vtable swizzle leaves enemy publish slot `+0x68` intact on the real `ComManipulator`.

Runtime gates, later only:

- Two-client host test with a map-placed NPC: ownership true, native enemy sync packet counters increment, peer observes NPC position/behavior change.
- Negative controls: spawned creature refuses; invalid/no-handle target refuses; non-host refuses; ownership lost disables network visibility.
- Artifact must include structured packet/ownership telemetry. Screenshots can be user review artifacts, not the run-stopping oracle.

## Option C: vanilla buddy/replica channel

### Product shape

Create a peer-visible vanilla buddy/summon/replica creature using the game's own buddy packet, then drive that peer-visible NPC while local possession controls a creature. This is the most promising route from old static RE for unmodded peers seeing a creature, but it is not the smallest safe product because several hard questions remain open.

### Architecture

Separate "network replica" from possession core:

- possession core owns local gameplay and teardown;
- replica layer owns vanilla summon/buddy broadcast and NetChrSync ownership;
- mapping from local possessed creature to replica is explicit and logged;
- if replica creation/ownership fails, possession continues local-only.

### Non-goals

- Do not reuse current `SpawnDynamicChr` buddy roster creature as if it were peer-visible.
- Do not claim current `mode = "spawn"` works online.
- Do not create a side protocol before exhausting vanilla packets.

### Required restrictions

- default off;
- only creature ids/npcParam rows whose peer instantiation was proven;
- only sessions where native/Seamless packets needed for buddy creation and NetChrSync are preserved;
- refuse if local-control flag cannot be acquired or verified;
- refuse if peer slot derivation is unknown for this target.

### Risks/open gaps

From `possess-net-sync-verdict-buddy-summon-is-the-seam-2026-09-02`:

- unknown whether current Seamless preserves native packets 4 / 0x46 / 22 / 23 / buddy summon;
- unknown what sets ownership `priority`;
- current spawn path's buddy roster slots may sit outside the network buddy band expected by `P2PEntityHandle::IsSummonBuddy`;
- unknown how peers derive the ChrSet slot from `BuddyPacketEntry`;
- unknown whether the behavior stream is rich enough for peer-visible motion fidelity.

This should be a research program, not the first product switch.

## Explicit runtime refusals for any networked behavior

A networked mode must refuse, with one-line reason in log/UI, when any applies:

- no network session or no peer observed;
- unmeasured game build / unresolved RVA / byte-gate failure;
- Seamless build unsupported or packet preservation unknown for the selected mode;
- target was spawned by current local `SpawnDynamicChr` path and has no peer-resolvable handle;
- target lacks valid `P2PEntityHandle` or local-control flag;
- user selected `mode = "spawn"` with `network.visibility = host_map_placed_only`;
- not host/map owner for host-only mode;
- damage translation selected but hit/damage oracle is absent;
- any confirmation telemetry disagrees with the claim being made;
- the layer cannot observe its own proof window, because then it cannot tell success from silence.

## Invariants to avoid lying to the user

- Separate four states in logs/UI:
  1. local possession active;
  2. peer sees the player body position;
  3. peer sees a controlled NPC/replica;
  4. remote HP changed from translated damage.
- Never call state 2 "peer-visible NPC."
- Never call a sent packet "damage landed"; only a remote HP delta/receive proof can say that.
- Never call local animation request "remote animation"; current `requestAnimationId` is local.
- Every network feature line must include `enabled/refused/armed/proven` state and reason.
- Config defaults must be off and described as experimental/proof-gated.
- If an oracle is unavailable on a build, refuse rather than falling back to optimistic wording.
- If peer rendering still includes the ordinary Tarnished, user-facing docs must say so before the feature can be enabled.

## Follow-up subagent prompts for parent orchestration

### Prompt 1: static map-placed NPC eligibility proof

Goal: Find the smallest read-only eligibility oracle for a map-placed NPC to be peer-visible under vanilla enemy sync.

Context: Current possession `Chr` lives in `crates/er-npc-possess/src/possess/game.rs`; old memory says enemy sync uses P2PEntityHandle, local-control flags, and ComManipulator publish slot `+0x68`. Read `possess-net-sync-verdict-buddy-summon-is-the-seam-2026-09-02` and `enemy-chrsync-ownership-is-arbitrated-packet22-2026-09-01`.

Deliverable: list exact fields/functions/addresses needed to answer: valid peer-resolvable handle, map-placed vs local-spawned, local-control owner, and packet publish eligibility. No implementation, no runtime launch.

### Prompt 2: damage translator feasibility proof

Goal: Determine whether a possessed-creature hit can be safely translated through the local player's vanilla PvP damage path.

Context: `possess/netdamage.rs` proves NPC->remote player is mode 0 and only local player category sends damage. Need the exact `CSPlayerDamageModule` slot 21 / Packet15 builder contract and a reliable hit oracle that supplies victim, AtkParam, and damage.

Deliverable: architecture verdict: either a minimal `DamageIntent -> PvP send` path with all required proof gates, or a static refusal explaining why it cannot be done without lying/desyncing. No runtime launch.

### Prompt 3: Seamless/native packet preservation research plan

Goal: Produce a bounded proof plan for whether current ERSC preserves vanilla enemy sync / buddy / ownership packets needed by Options B/C.

Context: Old static memory says ERSC wraps native P2P but packet preservation is unknown. Current repo strongly prefers Frida for runtime investigation, but this task is plan-only unless parent authorizes runtime.

Deliverable: exact packets/functions to count, expected counters, refusal criteria, and two-client validation matrix. No implementation unless delegated separately.

### Prompt 4: user-facing config/log contract

Goal: Design the `[network]` config and log wording that cannot overclaim.

Context: Use existing honesty patterns in `config.rs`, `settings.rs`, `engine.rs`, and `netdamage.rs`. Defaults off. Need states for local-only, refused, armed, sent, proven.

Deliverable: proposed config schema, summary strings, refusal messages, and test cases for parser/summary behavior. No source edit unless parent assigns implementation.

## Suggested planner handoff contract

Goal: Choose at most one smallest product slice to implement first. Recommendation: Option B eligibility/refusal-only instrumentation first, not damage translation. It can prove or refuse peer-visible NPC support without touching PvP damage.

Evidence:

- current code already has local possession state and a net-damage ledger;
- current spawn path is local-only and should be explicitly refused for network visibility;
- outgoing NPC damage to remote players is statically blocked by route matrix;
- map-placed or vanilla buddy channels are the only plausible unmodded visual path, but both require handle/ownership proof before product claims.

Success criteria for next agent:

- no implementation unless explicitly assigned;
- if implementing instrumentation later, default-off config and refusal logs land before any behavior-changing packet/send path;
- tests prove config parsing, state naming, and refusal branches;
- no networked behavior enabled until a two-client proof gate exists.

Stop/escalation rules:

- Stop and ask parent if a design choice would enable live network behavior, PvP damage, packet emission, or runtime launch.
- Stop if static RE cannot distinguish map-placed valid handles from local spawned invalid handles.
- Do not recommend upstream issues/PRs.
- Do not ask the user to drive in-game steps; future runtime proof should be agent-driven or request only the needed evidence outcome.

## Validation performed

No source tests were run because this was a design/handoff task with no source changes. Evidence gathering used read-only file reads, searches, and Beads memory recalls. One attempted Python line-number extraction command was blocked by the OPA/Cupcake policy evaluator aborting; no workaround was needed because the native read/grep tools supplied the context.
