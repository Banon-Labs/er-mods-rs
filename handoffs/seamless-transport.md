# Seamless transport feasibility for `er_npc_possess`

## Bottom line

Seamless Co-op does **not** appear to give `er_npc_possess.dll` a usable no-peer-install transport/authority path for custom possession visuals, animation, or NPC-caused damage.

What exists locally is useful for **matchmaking/discovery** and **self-routing**:

- Steam lobby metadata through Seamless's advertisement lobby.
- Steam lobby queries and filters.
- A Seamless/Steam networking receive poll during host sessions.
- Vanilla Elden Ring player/NPC/network damage machinery.

None of those is a magic remote renderer. Anything that is not already a vanilla networked state transition still needs code on the peer to consume it and apply it. For peers without the DLL, the only viable routes are existing game/Seamless semantics they already understand: normal player state, normal player-owned PvP damage packets, normal world/NPC sync fields, normal SpEffect replication when applied through the game's own synced path.

For `er_npc_possess`, the current possession mechanism is mostly local memory surgery: manipulator override, camera override, body alpha/team/invincibility writes, movement intent writes into the creature, and animation requests through `CSChrEventModule+0x18`. That does not become peer-visible just because Seamless is present.

## Evidence

### 1. Possession is local-field control, not a network protocol

`crates/er-npc-possess/src/possess/mod.rs:1-24` describes the feature's runtime mechanism:

- builds a local thunk/vtable override;
- writes it to `ChrCtrl+0x3b0`;
- puts the creature into `WorldChrManDbg+0xb8 camOverrideChrIns`;
- makes the real player body invisible/invincible/silent and co-locates it;
- writes movement intent into the creature's `AiIns`;
- fires attacks by writing `CSChrEventModule+0x18 requestAnimationId`.

The same file explicitly says identity, damage, and save still point at the real `PlayerIns` (`possess/mod.rs:12-14`). That is good for local play, but it also means remote peers still know the player as the normal player unless some existing networked path carries the new state.

### 2. Possessed NPC damage to remote players is blocked by the game's routing table

`crates/er-npc-possess/src/possess/netdamage.rs` is the strongest local answer for damage:

- `netdamage.rs:37-50`: remote-player victim + NPC attacker routes to mode 0, `Route::ComputeOnly`; numbers are computed and then dropped. No `HitChr`, no `Packet15`, no vitals packet.
- `netdamage.rs:46-50`: the only attacker category that can put damage on a remote player is category 1, the local player's own `PlayerIns`, via `SendPvpDamage` / `Packet15`.
- `netdamage.rs:64-78`: forging a packet for a spawned creature does not solve it because the remote receiver resolves attacker/victim handles first; dynamically spawned creatures use invalid/local handles and the peer cannot resolve them.
- `netdamage.rs:260-302`: the transcribed route tables have remote-player victim row `[5, 4, 0, 0, 0, 0, 0]`, so both NPC categories are mode 0.
- `netdamage.rs:656-677`: tests assert both local and remote NPC attackers against a remote player are `Route::ComputeOnly`, do not reach victim HP, and send no damage message; only `Category::MainPlayer` sends PvP damage.

Implication: Seamless does not alter this for unmodded peers unless it adds its own damage acceptance path. The local evidence says the vanilla-compatible path is still player-owned PvP damage, not NPC swing relabeling.

### 3. Possession animation is local, not a known replicated event

`possess/mod.rs:20-24` says attack firing is a local write to `CSChrEventModule+0x18 requestAnimationId`. `netdamage.rs:79-85` states the same in networking terms: possessing a map-placed creature does not change the verdict because the attack exists on one machine; the other client's copy animates whatever its own owner drives it to.

Follow-up could still look for a native ER path that *does* replicate NPC animation commands, but the current product path is not proven to use one.

### 4. Seamless lobby metadata is discovery/filter data, not remote render state

`crates/er-invasion-warp/src/lobby_publish.rs` is the repo's local Seamless/Steam boundary documentation.

Key facts:

- `lobby_publish.rs:1-35`: Seamless already filters lobby lists on metadata keys; the mod adds missing host location/effects metadata.
- `lobby_publish.rs:35`: the host must run this DLL for custom lobby metadata to be present because only the lobby owner may call `SetLobbyData`; an invader cannot annotate someone else's lobby.
- `lobby_publish.rs:121-156`: `SetLobbyData` is slot 20, `GetLobbyData` slot 19, `GetLobbyOwner` slot 35; ownership is checked because non-owner writes evaporate.
- `lobby_publish.rs:154-166`: measured non-owner writes return success locally but do not persist server-side.
- `lobby_publish.rs:73-88`: the hooks are Steam vtable hooks called by Seamless: `SetLobbyData`, `AddRequestLobbyListStringFilter`, and `RequestLobbyList`.
- `lobby_preflight.rs:48`: the preflight path explicitly says it performs requests and reads only: no join, no create, no lobby write, no game state touched.
- `lobby_preflight.rs:353-370`: joining a host's advertisement lobby by hand was measured to enter as a co-op guest, not as an invader; bare `JoinLobby` is not a hostile handoff.
- `lobby_preflight.rs:376-397`: reading `lobby_key` is enough to decide whether this player's Seamless search can return a host; it does not make unreachable hosts reachable.

Steam's own docs back the type-level point: `ISteamMatchmaking::SetLobbyData(CSteamID, key, value)` sets lobby metadata; `ISteamNetworkingMessages::SendMessageToUser` sends bytes to a host. Neither API causes Elden Ring to render or damage anything unless the receiving game/mod code consumes the data.

### 5. Seamless host/invader lobby ownership split is already measured

Relevant Beads memories:

- `ersc-advertisement-lobby-needs-open-world-for-multiplayer-2026-08-25`: Seamless calls `SetLobbyData` only when the host opens the world for multiplayer. Invading someone else does not create the local advertisement lobby.
- `invasion-publish-ownership-and-loop-policy-2026-08-06`: host-owned lobby metadata persists; writes to someone else's lobby evaporate. The session lobby field may point at someone else's advertisement during search.
- `invasion-reject-loop-proven-two-player-host-needs-no-dll-2026-08-06`: invader-only DLL can rejection-filter Seamless destinations after the server pushes join data; host DLL is not required for that specific routing behavior.
- `a-bare-lobby-join-enters-as-a-coop-guest-2026-09-17`: joining the advertisement lobby is co-op membership, not invasion/hostility.

This makes the host/invader split clear:

- Host can publish metadata only on its own advertisement lobby, after opening the world.
- Invader can read/filter metadata and can reject/cancel destinations locally.
- Invader cannot write state onto the host's lobby.
- Lobby metadata can help choose a host; it cannot make a host or peer render possession state.

### 6. Seamless's observed peer transport is not a free custom channel to unmodded peers

Relevant script and memory evidence:

- `scripts/frida/invasion-p2p-probe.js:1-24`: the probe hooks every relevant Steam networking interface slot in `lsteamclient.dll` instead of guessing headers, and is read-only.
- Beads memory `seamless-transport-is-networkingmessages-receive-poll-2026-09-17`: on an open host during a failed invasion attempt, the only observed call was `SteamNetworkingMessages002_ReceiveMessagesOnChannel` at about 21/s; return value was zero on 1052 sampled calls. `SendMessageToUser`, `AcceptSessionWithUser`, `ConnectP2P`, `AcceptConnection`, and legacy P2P sends did not fire.
- `scripts/frida/steam-lobby-calls-only.js:1-15`: separate probe focused on Seamless lobby calls through `lsteamclient.dll`, not `ersc.dll`, because inline `ersc.dll` hooks can break the product prologue checks.

That evidence says Seamless uses Steam networking/lobby APIs, but the measured host-side traffic did not reveal an inbound custom data stream during the attempted invasion. Even if a Seamless message channel is found later, unmodded peers will only run Seamless's parser. A possess-specific payload would be ignored unless Seamless already defines a compatible payload type that maps to a vanilla-visible state.

### 7. `er-net-effects` is different: it relies on native SpEffect sync, not Seamless custom RPC

`crates/er-net-effects/src/effects.rs:121-125` calls `player.apply_speffect(id, dont_sync)`, with `dont_sync = !network_sync`.

`crates/er-net-effects/src/config.rs:21` ships `network_sync = true`; `config.rs:48-50` warns that permanent effects never expire on other players because the game broadcasts apply but has no remove message.

This is a known vanilla/game sync path. It does not imply arbitrary possession animation/camera/body state can be tunneled through Seamless.

### 8. Supported Seamless version and binary constraints

`build-support/prologue_build.rs:135` pins the supported Seamless version to `2.0.1`. `AGENTS.md` says not to bundle/copy `ersc.dll`; this investigation used installed/archive references only:

- installed: `/home/banon/.local/share/Steam/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll`
- archive: `vendor-archive/seamless/ersc-2.0.1.dll` and `vendor-archive/seamless/ersc-2.0.1.runtime.bin`

A quick static byte search of `vendor-archive/seamless/ersc-2.0.1.runtime.bin` found no plaintext occurrences of `SendMessageToUser`, `ReceiveMessagesOnChannel`, `ISteamNetworkingMessages`, `JoinLobby`, `SetLobbyData`, `GetLobbyData`, `RequestLobbyList`, `lobby_key`, `ykssr_dlc`, `yknx3_seamless_master_lobby`, or `Packet15`. That is consistent with the repo notes that Seamless hashes/obfuscates key names and calls Steam via resolved interfaces, so absence of strings is not proof of absence of functionality.

## Host vs invader feasibility

### Invader has the possess DLL, host/peers do not

Likely peer-visible without peer DLL:

- Normal player position/co-location if the real `PlayerIns` is moved through vanilla-replicated movement.
- Normal player-owned PvP damage, if the action is made to belong to the local `PlayerIns` and uses the game's accepted PvP damage path.
- Normal synced SpEffects when applied with `dont_sync = false`.
- Lobby-query/routing decisions local to the invader.

Not peer-visible as-is:

- Creature model substitution/visual possession. Peers still render the player and whatever NPCs their game/session owns.
- Creature attack animation fired by local `requestAnimationId` write.
- NPC-caused damage to remote players; route table drops it.
- Spawned creature identity; peer cannot resolve its local handle.
- Custom lobby data unless the host also publishes it.

### Host has the possess DLL, invader/guest does not

Likely peer-visible without peer DLL:

- Host can publish lobby metadata about itself; unmodded players do not use custom keys, but modded readers can.
- Any vanilla/Seamless host-owned NPC state that is already part of the ordinary synchronization model.

Not proven peer-visible as-is:

- Host's local possession camera/body/thunk/overlay state.
- Host's custom NPC attack input if it bypasses the native network owner/update path and only writes local animation request fields.
- Host-caused possessed-NPC damage to remote players, unless rewritten as a vanilla-accepted player-owned PvP damage path or a native host-authoritative NPC damage path the peer already accepts.

### Both peers have the possess DLL

Then Steam lobby metadata or a Seamless/Steam message channel could coordinate state, but the receiver still needs code to apply it. This is outside the no-peer-install goal.

## Feasibility answer by unknown

### Does Seamless expose a custom RPC/message/lobby metadata channel usable without peer DLL support?

It exposes or uses Steam lobby metadata and Steam networking APIs, but not a possess-specific no-install RPC. Lobby metadata is readable key/value discovery data. Networking messages are just bytes. Without receiver code already present in Seamless/game that interprets a payload as possession state, no unmodded peer will render or apply it.

### Can Steam lobby data or Seamless packet forwarding make unmodded peers render custom state?

No evidence supports that. Steam lobby data does not enter the renderer/gameplay state; it only affects matchmaking/filtering. Packet forwarding would need an existing Seamless payload type mapped to vanilla state. No such payload has been identified locally.

### Are there host-owned NPC sync paths distinct from vanilla?

Open. The possess code's damage proof is vanilla ER and strongly says NPC-to-remote-player damage from this client is dropped. Seamless may manage co-op world/NPC authority, but the evidence here does not show a Seamless path that accepts arbitrary local `requestAnimationId` or possession-state writes and republishes them to unmodded clients. This is the best static follow-up target.

### What differs between hosting and invading?

- Hosting owns the advertisement lobby and can persist `SetLobbyData` custom keys.
- Invading reads lobby data and can rejection-filter destinations after Seamless/server selection.
- Invader-only routing can work without host DLL; host-side custom metadata cannot exist without host DLL.
- A bare lobby join is co-op, not invasion.
- Neither role bypasses the need for a peer-side consumer for custom visual/animation state.

## Likely implementation direction if the product goal remains no peer DLL

Do not design a Seamless custom channel first. Find a vanilla/Seamless state transition unmodded peers already consume:

1. For damage: make damage originate from the local player's own `PlayerIns` or another accepted vanilla PvP mechanism. The repo's own note says a player-owned bullet is the game's mechanism for player-owned damage; relabelling an NPC swing is not enough.
2. For visuals: either accept that peers see the ordinary player body, or find an existing replicated transform/animation/equipment/SpEffect path that can approximate the visual.
3. For NPC animation: prove whether a host-authoritative map NPC's native animation/task path replicates when driven through the same owner/update mechanism the game uses, not by writing `requestAnimationId` directly.
4. For matchmaking/discovery: continue using lobby metadata only for routing/gating, not as a state transport.

## Follow-up prompts for parent orchestration

### Prompt A: Seamless message/channel static RE

Goal: Determine whether Seamless v2.0.1 defines any existing network message type that unmodified Seamless peers consume into visual, animation, damage, or NPC-authority state.

Context/evidence:
- Use `vendor-archive/seamless/ersc-2.0.1.runtime.bin` and installed `SeamlessCoop/ersc.dll` read-only. Do not copy/bundle.
- `scripts/frida/invasion-p2p-probe.js` measured `SteamNetworkingMessages002_ReceiveMessagesOnChannel` polling, no sends/nonzero receives on the host attempt.
- Plain string search is weak: key/API strings are absent/obfuscated.

Suggested approach:
- Static-only first. Use `scripts/ersc_static.py`, `.pdata` functions, vtable/API call sites, and xrefs around the resolved Steam networking interface calls.
- Identify payload builders/parsers and any dispatch IDs.
- Classify whether any parsed message maps to animation/damage/NPC authority rather than session/matchmaking bookkeeping.

Success criteria:
- Function/RVA-backed list of Seamless message send/receive handlers, or a bounded negative with exact searched interface/call sites.
- Explicit answer whether an arbitrary possess payload would be ignored without peer code.

### Prompt B: Native ER NPC animation replication path

Goal: Find whether a map NPC's animation can be driven through a vanilla/network-owned path that unmodded peers render.

Context/evidence:
- Current possession fires attacks with local `CSChrEventModule+0x18 requestAnimationId` (`possess/mod.rs:20-24`), which `netdamage.rs:79-85` says is not published.
- Ghidra named dump is available; use 1.16.2 names and map addresses for 1.17.1 as required.

Suggested approach:
- Static RE of normal NPC owner update / animation event propagation in ER, not runtime first.
- Compare AI-driven NPC attack animation path vs `requestAnimationId` local write path.
- Identify whether host-authoritative NPC owner sends an event peers consume, and what function builds it.

Success criteria:
- A specific native call/field path that is networked, or a bounded negative saying animation is local/predicted only for NPCs in this context.

### Prompt C: Player-owned damage workaround

Goal: Determine whether possessed attacks can cause peer damage by using an existing player-owned PvP damage mechanism while preserving local creature controls.

Context/evidence:
- `netdamage.rs:37-50` and tests at `netdamage.rs:656-677` prove NPC->remote player is dropped; only local `PlayerIns` sends PvP damage.
- `netdamage.rs:86-88` suggests the real game mechanism is player-owned bullet, not relabelled NPC swing.

Suggested approach:
- Static RE `CSPlayerDamageModule` slot 21 / `Packet15` send path and player-owned bullet construction.
- Prove receiver handle requirements and anti-forgery constraints.
- Avoid designing a fake packet until the accepted native builder path is known.

Success criteria:
- Feasible native player-owned path with function evidence, or clear reasons it cannot be used.

### Prompt D: Steam lobby/member data boundaries

Goal: Finish the metadata boundary: what can a non-member, member, host, and invader read/write, and whether member data marks hostility.

Context/evidence:
- `a-bare-lobby-join-enters-as-a-coop-guest-2026-09-17` says bare `JoinLobby` is co-op.
- `lobby_preflight.rs:353-397` says lobby data and lobby_key are readable enough for pool decisions.

Suggested approach:
- Static/read-only review of existing scripts `frida-lobby-read-keys.py`, `frida-lobby-watch-members.py`, `er-prove-lobby-handoff.py`.
- If later runtime is allowed, read member data only; do not join unless explicitly scoped.

Success criteria:
- Table of read/write permissions and hostility markers, with no claim that metadata changes remote gameplay.

### Prompt E: Host-owned NPC authority in Seamless sessions

Goal: Determine who owns map NPCs during Seamless co-op/invasion and which owner-driven NPC state is replicated.

Context/evidence:
- Possession may target map-placed NPCs as well as spawned ones.
- Dynamic spawned handles are local/invalid for peers (`netdamage.rs:64-78`).
- Map-placed NPCs have real handles, but the current attack write is still local (`netdamage.rs:79-85`).

Suggested approach:
- Static RE of `FUN_1403f3e30`, `WorldChrMan` ownership checks, `GetChrInsByP2PEntityHandle`, and NPC network owner update send/receive paths.
- Use Ghidra first; runtime only after a candidate oracle exists.

Success criteria:
- Role-specific answer for host-owned map NPC, guest-owned NPC, and invader view.

## Residual risks

- Seamless internals are protected/obfuscated; static string absence is weak evidence.
- Existing local runtime memories are strong but scenario-specific; they do not exhaust every Seamless message type.
- Host-owned NPC sync remains the main unknown. It could provide a narrow vanilla-compatible route if the mod drives the same native owner path the game uses.
- Dirty working tree existed before this handoff; this task did not modify source.

## Commands and validation performed

- Read project instructions and `.auto/prompt.md`.
- Ran `$HOME/.local/bin/bd prime`.
- Recalled Seamless/lobby/invasion memories with `$HOME/.local/bin/bd recall ...`.
- Read relevant source files under `crates/er-invasion-warp`, `crates/er-net-effects`, `crates/er-npc-possess`, `scripts/frida`, and `build-support`.
- Ran Steamworks web search for `ISteamMatchmaking::SetLobbyData` and `ISteamNetworkingMessages::SendMessageToUser` docs.
- Ran static byte search over `vendor-archive/seamless/ersc-2.0.1.runtime.bin` for obvious Steam/Seamless strings; all searched plaintext markers were absent.
- Ran `git status --short && git diff --cached --name-only`; there are many pre-existing unstaged changes and no staged files printed.

Tooling note: one attempted Python line-number helper through `bash` tripped an OPA policy evaluation abort (`opa_abort` in `collect_verbs`). I did not work around the guard; I switched back to read/grep tools and recorded the abort here.
