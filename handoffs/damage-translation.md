# Possessed-NPC damage translation feasibility handoff

## Verdict

A possessed NPC swing cannot be translated into a fully vanilla peer-accepted damage event by keeping the NPC as the dealer. The receiver resolves both `Packet15` handles before doing anything; a locally spawned possessed creature has no resolvable peer handle, and a map enemy's locally forced attack animation is not a networked attack event.

The only vanilla path that can subtract a remote human player's HP from this client is the player-vs-player path where the **local `MainPlayer` is the attacker**. That gives one plausible translation strategy: compute/author damage locally from the possessed NPC hit, then send a `Packet15` as if the local player hit the remote player. It may move HP/stamina/death and may trigger some `HitChr`/reaction on the receiver, but it is not proven for guard/stagger/throws/status/super armor and it will not make the peer believe the NPC was the dealer. The smallest next proof is a static packet-builder + receive-field audit followed by one tightly scoped live two-client probe only if the static audit says the forged player-dealer packet can be constructed without corrupting state.

No game launch was performed.

## High-value local code context

### `crates/er-npc-possess/src/possess/netdamage.rs`

Relevant facts are already encoded as pure data + tests:

- Lines 13-52: damage router description. Victim damage module slot 7 routes through `FUN_140445060`; player override `FUN_14044caf0`; categories from `FUN_14044a1b0`; route tables at `DAT_142a36400` / `DAT_142a364d0`.
- Lines 54-68: decisive row: victim `RemotePlayer` + attacker `LocalNpc`/`RemoteNpc` -> mode 0 compute-only. Only `MainPlayer` attacker gets mode 4 `SendPvpDamage`.
- Lines 70-83: `Packet15` receive resolves victim then dealer with `GetChrInsByP2PEntityHandle`; er-npc-possess dynamic spawn uses invalid `blockId = 0xffffffff`, so peers cannot resolve the spawned creature.
- Lines 260-284: transcribed route tables. `ROUTE_TABLE[RemotePlayer][MainPlayer] = 4`; `[RemotePlayer][LocalNpc/RemoteNpc] = 0`; alt table changes only local-player -> remote-player from mode 4 to mode 1 when `AttackDamageInfo+0x11c != 0`.
- Lines 532-621: tests pin exactly this behavior.

Important snippet:

```text
ROUTE_TABLE row victim=RemotePlayer: [5, 4, 0, 0, 0, 0, 0]
ROUTE_TABLE_ALT row victim=RemotePlayer: [5, 1, 0, 0, 0, 0, 0]
```

### `crates/er-npc-possess/src/possess/layout.rs`

Relevant notes:

- Lines 1160-1180: `packet15_receive` static buffer for `WorldChrManImp` packet pump.
- `BUFFER_RVA_1162 = 0x03d65fd0`, `BUFFER_RVA_1170 = 0x03d6a040`; 1.17.1 keeps the 1.17 data RVA because only `.text` moved.
- Offsets from buffer base: dealer handle `+0x120`, victim handle `+0x128`, damage `+0x130` as signed `i16`, stamina `+0x132` as signed `i16`.
- `packet15_receive_rva()` returns `BUFFER_RVA_1170` for both 1.17.0 and mapped 1.17.1.

### `crates/er-npc-possess/src/possess/game.rs`

Relevant runtime oracle already exists:

- Lines 1436-1473: `players_in_world()` samples `WorldChrMan.player_chr_set`, classifying main player vs remote player.
- Lines 1475-1503: `last_received_damage_packet()` reads the `Packet15` receive buffer with the layout constants above.

This is useful for any later live proof: it can distinguish "no damage packet arrived" from "packet arrived but named someone else / was ignored."

## Static RE evidence gathered in this pass

### Ghidra daemon context

Commands confirmed active dumps:

- `python3 scripts/ghidra/mcp_query.py getContext --port 8765` -> `pc_eldenring_runtime.1.16.2.exe`, 367183 functions.
- `python3 scripts/ghidra/mcp_query.py getContext --port 8767` -> `pc_eldenring_runtime.1.17.0.exe`, 366673 functions.

Installed game is 1.17.1. For `.text` functions with RVA `>= 0xafefe9`, add `+0x70` to 1.17.0 function addresses for live runtime. Data RVAs do not shift.

### `CSPlayerDamageModule` slot 21 / player PvP sender

`FUN_14044ce40` (1.16.2) decompile:

```c
Packet15::Packet15(packet);
FUN_140443c40(param_1, packet, attackDamageInfo);
pPVar1 = (PlayerIns *)CS::CSChrModuleBase::GetChrOwner(param_1);
if (pPVar1 is PlayerIns) {
    pPVar4 = CS::PlayerIns::GetPlayerNetworkSession(pPVar1);
    pPVar4->SendHitPacket(pPVar4, packet);
}
```

Interpretation:

- This is the mode-4 path used when local `MainPlayer` attacks a remote `PlayerIns` victim.
- `param_1` is the **victim** damage module. Its owner must be a `PlayerIns`, because the function fetches that victim's `PlayerNetworkSession` and sends directly to it.
- It builds a `Packet15` from the current `AttackDamageInfo` through `FUN_140443c40`.
- It is the most promising supported vanilla sender path for a translation shim, but only if the shim can construct an `AttackDamageInfo` whose attacker is the local player and whose victim module is the target remote player.

Addresses:

| Function | 1.16.2 | 1.17.0 | 1.17.1 live |
|---|---:|---:|---:|
| `CSPlayerDamageModule` slot21 send PvP damage | `0x14044ce40` | `0x14044d3a0` (confirmed function entry on :8767) | `0x14044d3a0` |
| `FUN_140443c40` build `Packet15` from `AttackDamageInfo` | `0x140443c40` | `0x1404441a0` | `0x1404441a0` |
| `CS::PlayerNetworkSession::SendHitPacket` wrapper | `0x140cc5540` | `0x140cc6c10` | `0x140cc6c80` |
| lower `SendHitPacket` -> `P2PSendToSteamId(type 0x14, size 0x120)` | `0x140c9e580` | `0x140c9fc50` | `0x140c9fcc0` |

Caveat: `FUN_140443c40` copies a large subset of `AttackDamageInfo` into `Packet15`. It does not by itself prove which fields must be authored for clean guard/reaction/status behavior. Audit this before calling it with synthetic data.

### `Packet15` receive path

`FUN_14050e4a0` (1.16.2) pump:

```c
TryDequeuePacket15(queue, &DAT_143d65fd0);      // type 0x0f, size 0x140
if (FUN_140ca6280(&DAT_143d65fd0)) {           // validator
    victim = GetChrInsByP2PEntityHandle(packet+0x128);
    if (victim) {
        dealer = GetChrInsByP2PEntityHandle(packet+0x120);
        if (dealer) {
            InitDamageStruct(&info);
            FUN_1404434f0(victim->damage, &info, dealer, packet);
            HitChr(victim->damage, dealer, &info, true);
            SetHP(victim->data, victim->hp - *(i16 *)(packet+0x130));
            SetStamina(victim->data, victim->stamina - *(i16 *)(packet+0x132), 1);
            FUN_1405283b0(&info);
        }
        if (IsHpZero(victim->data)) FuckingDie(victim, 1);
    }
}
```

Validator `FUN_140ca6280` checks:

- `HasChrSelector(packet+0x128)` for victim.
- victim `chrSetIndex != worldBlockInfoCount`.
- `HasChrSelector(packet+0x120)` for dealer.
- packet stamina at `+0x132 >= 0`.
- `FUN_1405285d0(packet)` float/finite sweep.

What it does **not** check in the visible decompile: possession flags, team, `IsImmuneToAttack`, cam override, or "dealer must be the sender" cryptographic/auth identity. However, that does not prove the network layer has no sender/recipient filtering before dequeue.

Addresses:

| Function | 1.16.2 | 1.17.0 | 1.17.1 live |
|---|---:|---:|---:|
| `WorldChrManImp` packet pump | `0x14050e4a0` | `0x14050f2a0` (from existing layout note) | `0x14050f2a0` |
| `Packet15` validator | `0x140ca6280` | `0x140ca7950` | `0x140ca79c0` |
| `TryDequeuePacket15` | `0x140c99c40` | `0x140c9b310` | `0x140c9b380` |
| packet -> `AttackDamageInfo` rebuild | `0x1404434f0` | `0x140443a50` | `0x140443a50` |

Important detail: the lower named `SendHitPacket` sends `0x120` bytes, while receive dequeues `0x140` bytes and the pump reads handles at `+0x120/+0x128`. The extra receive-side fields appear to be transport metadata or appended side data, not the explicit `Packet15` payload constructed by `Packet15::Packet15`. Do not hand-author raw network bytes until this is resolved.

### Generic damage router send modes

`FUN_140445060` mode tail disassembly confirms:

- Mode 1-style directed small packet path builds a 0x10 packet and calls `FUN_140ca1600(..., type 0x15, size 0x10)`.
- Mode 2/3-style `Packet15` path constructs `Packet15`, calls `FUN_140443c40`, then `FUN_140c9f370(... type 0x0f, size 0x140)` broadcast.
- Vitals/super-armor publish calls `FUN_140c9f3d0(... type 0x57, size 0x10)`.

Address/function table:

| Function | 1.16.2 | 1.17.0 | 1.17.1 live | Meaning |
|---|---:|---:|---:|---|
| `FUN_140445060` | `0x140445060` | `0x1404455c0` existing note | `0x1404455c0` | generic damage router |
| `FUN_140445f00` | `0x140445f00` | not mapped here | not mapped here | compute damage only |
| `FUN_140ca1600` | `0x140ca1600` | `0x140ca2cd0` | `0x140ca2d40` | targeted type `0x15`/`Packet21` send via owner lookup |
| `FUN_14050aaf0` | `0x14050aaf0` | `0x14050b8c0` | `0x14050b8c0` | send to owner of a `P2PEntityHandle`: host / creator steam / block owner |
| `FUN_140c9f370` | `0x140c9f370` | `0x140ca0a40` | `0x140ca0ab0` | broadcast type `0x0f`, size `0x140` |
| `FUN_140c9f3d0` | `0x140c9f3d0` | `0x140ca0aa0` | `0x140ca0b10` | broadcast type `0x57`, size `0x10` |

### `Packet21`

Receive section of `FUN_14050e4a0`:

```c
TryDequeuePacket21(queue, &DAT_143d65fb8);      // type 0x15, size 0x10
if (Packet21::Validate(...)) {
    victim = GetChrInsByP2PEntityHandle(packet.handle);
    SetHP(victim->data, hp - packet.damage);
    SetStamina(victim->data, stamina - packet.stamina, 1);
    if (IsHpZero(...)) FuckingDie(victim, 1);
    FUN_140444820(victim->damage, damage, oldHp, 0);
}
```

`TryDequeuePacket21` takes type `0x15`, size `0x10`. No dealer handle is resolved; no `HitChr`; no reconstructed `AttackDamageInfo`; likely no hit reaction/guard/status. `FUN_140ca1600` sends this packet to the owner of a P2P entity handle using `FUN_14050aaf0`.

Feasibility: good candidate for raw HP/stamina/death only if vanilla accepts it from the role in question. Weak candidate for "attack translated" because there is no dealer identity and no damage semantics beyond numeric pool subtraction.

Addresses:

| Function | 1.16.2 | 1.17.0 | 1.17.1 live |
|---|---:|---:|---:|
| `TryDequeuePacket21` | `0x140c99ba0` | `0x140c9b270` | `0x140c9b2e0` |
| targeted sender helper | `0x140ca1600` | `0x140ca2cd0` | `0x140ca2d40` |

### `Packet28`

`FUN_140446bb0` builds `Packet28` after damage and calls both local application and broadcast:

```c
FUN_14043fee0(victimDamageModule, &packet28);
BroadcastPacket28(packet28); // P2P type 0x1c, size 0x50
```

`FUN_14043fee0` receive/apply path is large visual/material/decal/SFX handling. The visible decompile resolves a dealer handle for SFX context, but no HP/stamina subtraction was seen. In `FUN_14050e4a0`, `TryDequeuePacket28` resolves only the victim handle and calls `FUN_14043fee0(victim->damage, &packet28)`.

Feasibility: not a damage carrier. Useful only for cosmetic hit effects if the target peer accepts the victim/dealer context.

Addresses:

| Function | 1.16.2 | 1.17.0 | 1.17.1 live |
|---|---:|---:|---:|
| builder + local apply + broadcast | `0x140446bb0` | `0x140447110` | `0x140447110` |
| apply `Packet28` to damage module | `0x14043fee0` | `0x140440440` | `0x140440440` |
| `BroadcastPacket28` | `0x140c9dab0` | `0x140c9f180` | `0x140c9f1f0` |
| `TryDequeuePacket28` | `0x140c991d0` | `0x140c9a8a0` | `0x140c9a910` |

## Feasibility matrix

Legend: Proven = static proof from this pass or existing repo notes. Hypothesis = plausible but not proven. Rejected = static evidence says no.

| Mechanism | Host -> guest | Invader -> host | Invader -> other phantoms | Damage number / HP | Hit reaction / stagger | Stamina | Death | Guard / super armor / throws / status | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Keep NPC as attacker through normal hit router | Rejected | Rejected | Rejected | No for remote players: route mode 0 compute-only | No packet | No | No | No | Proven rejected by route table. |
| Player-owned bullet visually spawned from NPC | Hypothesis | Hypothesis | Hypothesis | Possible only if bullet owner is local `MainPlayer`, not NPC | Depends on bullet damage path into same player-vs-player mode 4 | Likely via `Packet15` if it reaches mode 4 | Likely | Unknown; bullet flag handling in `FUN_1404434f0` seen (`field_0x6d & 2` maps bullet id to atk id) but not audited | Best "vanilla-looking" lead. Needs static bullet-owner audit. |
| Directly call `CSPlayerDamageModule` slot21 (`FUN_14044ce40`) on remote player victim with local player as attacker in `AttackDamageInfo` | Likely if host has guest `PlayerNetworkSession` | Likely if invader has host session object | Unknown: needs peer session object for each phantom and vanilla may route through victim-specific `PlayerNetworkSession` | Likely: receive subtracts `i16` damage | Partial: receive calls `HitChr(..., true)` before SetHP | Yes: receive subtracts `i16` stamina | Yes: `FuckingDie` after packet | Unknown. Guard/reaction/status depend on authored `AttackDamageInfo` / packet fields; throws require valid handles and likely player-compatible params | Promising, but synthetic `AttackDamageInfo` correctness unproven. |
| Raw lower `SendHitPacket` / raw `Packet15` bytes | Unknown | Unknown | Unknown | Could if extra metadata/handles are supplied correctly | Could | Could | Could | Unknown | Do not do first. Receive dequeues 0x140 but lower sender sends 0x120; extra fields need audit. Prefer vanilla builder/slot21. |
| `Packet21` targeted HP/stamina packet (`type 0x15`, size 0x10) | Hypothesis | Hypothesis | Maybe only to owner of victim handle | Yes, if accepted | No `HitChr`; likely no hit reaction | Yes | Yes | No guard/status/throw; maybe damage-number-only side effect via `FUN_140444820` | Possible minimal HP/stamina/death hack, not an attack translation. |
| `Packet28` | Rejected for HP | Rejected for HP | Rejected for HP | No HP path seen | Cosmetic SFX/decal only | No | No | Cosmetic/material effects only | Not a damage carrier. |
| Vitals publish `type 0x57`, size 0x10 | Hypothesis only for owner-owned chr sync | Likely rejected/not authoritative for remote player victims | Unknown | Might publish victim HP/super armor for locally owned chrs, but not appropriate to damage remote player | No attack semantics | Unknown | Unknown | Super-armor/vitals sync only | Not a safe client-to-peer damage path. |
| Enemy ownership / ChrSync packets 22/23 + enemy net stream | Could let peer see driven creature, not player damage | Could let peer see driven creature | Could let peer see driven creature | Not direct player damage | Creature animation sync possible | No direct player HP | No | Separate feature; Seamless preservation unknown | Relevant to visual sync, not damage acceptance. |

## Role-specific conclusions

### Host damaging guests

Most plausible supported path: host client sends `Packet15` to the target guest using that guest's `PlayerNetworkSession` by invoking the existing mode-4 player PvP sender with local host player as dealer. Receiver can resolve dealer/victim because both are player handles. This should be vanilla-acceptable for HP/stamina/death if the packet fields pass validation.

Unproven: whether a host can send a PvP-looking `Packet15` to guest in Seamless host role when the visual collision came from an NPC; whether guard/reaction/status mirror the NPC attack rather than the local player's current state.

### Invader damaging host

Same conceptual path: invader is local `MainPlayer` on invader client, host is remote `PlayerIns` with a `PlayerNetworkSession`; mode-4 sender is designed for attacker-side PvP. This is the most likely path to work for invader -> host.

Unproven: Seamless may wrap/replace transport semantics; static Elden Ring path itself does not reject this shape.

### Invader damaging other phantoms

Unclear. The vanilla sender is victim-session-specific (`GetPlayerNetworkSession` on the remote victim `PlayerIns`). If the invader has `PlayerNetworkSession` objects for every phantom, the same path may work. If Seamless only exposes host-directed traffic or filters non-host invader peer traffic, it may fail. This needs either static ERSC proof or a live three-peer probe; Elden Ring static RE alone does not settle it.

## Outcome dimensions separated

| Outcome | Best candidate | Proven now? | Notes |
|---|---|---:|---|
| Damage number / HP subtraction | `Packet15` via slot21; fallback `Packet21` | `Packet15` receive subtracts HP proven; forged send not proven | `Packet21` subtracts HP with less semantic baggage. |
| Stamina subtraction | `Packet15` / `Packet21` | Receive subtraction proven | Signed `i16` fields. |
| Death | `Packet15` / `Packet21` | Receiver calls `FuckingDie` when HP zero | Death should follow if HP reaches zero. |
| Hit reaction / stagger | `Packet15` | Partially proven receive calls `HitChr` | Quality depends on packet/`AttackDamageInfo` fields. |
| Guard | `Packet15` | Not proven for forged attack | `FUN_1404434f0` rebuilds attack info; exact guard inputs need audit. |
| Super armor | `Packet15` / vitals publish | Not proven | Vitals publish is separate `type 0x57`; how peer treats super armor in PvP needs audit. |
| Throws | Likely no for NPC-as-dealer; maybe no for player-forged unless params compatible | Not proven | Throw networking has `IsValidForThrowNetworking` gates and handle identity requirements. Treat as high risk. |
| Status effects | Unknown | Not proven | Packet fields include many `AttackDamageInfo` values, but status application via forged packet was not traced. |
| Cosmetic hit decals/SFX | `Packet28` | Static path proven | Not HP damage. Could supplement a player-forged HP packet. |

## Proven vs hypothesized

### Proven

- Remote-player victim + NPC attacker routes to mode 0 compute-only in both route tables.
- Only local `MainPlayer` attacker reaches mode 4 `SendPvpDamage` for a remote-player victim in the default table.
- `Packet15` receive resolves victim handle and dealer handle before `HitChr` and before HP/stamina subtraction.
- `Packet15` receive subtracts signed `i16` damage/stamina and calls death when HP is zero.
- `Packet15` validator checks handles and finite packet shape, not possession-specific state in the visible function.
- Dynamic spawned er-npc-possess creatures have invalid/unresolvable P2P handles for peers.
- `Packet21` receive subtracts HP/stamina without a dealer and without visible `HitChr`.
- `Packet28` is cosmetic/material/SFX, not HP damage.

### Hypothesized

- Calling `FUN_14044ce40` or the vtable slot21 equivalent with a carefully authored `AttackDamageInfo` can send player-attributed damage while the visible local trigger is NPC collision.
- Player-owned bullets may be a cleaner vanilla source of player-attributed `AttackDamageInfo` than hand-authoring a packet.
- Invader -> non-host phantom depends on whether the invader has usable victim `PlayerNetworkSession` objects and whether Seamless permits that peer path.
- Guard/stagger/status/throw fidelity depends on packet fields not yet fully audited.

### Rejected

- Sending damage as the NPC dealer to an unmodded peer: receiver cannot resolve local spawned NPC handles, and normal router drops NPC->remote-player hits.
- `Packet28` as damage carrier.
- Vitals publish as a general client-authoritative damage sender for remote players.

## Smallest next proofs

### Static proof 1: slot21 call contract and packet metadata

Goal: determine exactly what fields a DLL must provide to use the vanilla player PvP sender safely.

Read:

1. `CSPlayerDamageModule` vtable around slot21 to confirm call ABI and owner assumptions.
2. `FUN_140443c40` packet builder field map, especially how dealer/victim handles are supplied or appended.
3. `FUN_1405285d0` validator and any transport append path explaining why `SendHitPacket` sends `0x120` but receive dequeues `0x140` and reads handles at `+0x120/+0x128`.
4. `FUN_1404434f0` packet-to-`AttackDamageInfo` rebuild for guard/status/reaction fields.

Stop condition: either a concrete call recipe exists, or a specific missing field/metadata source blocks it.

### Static proof 2: player-owned bullet path

Goal: determine whether a spawned/player-owned bullet can naturally produce mode-4 player damage with NPC visuals, avoiding synthetic `AttackDamageInfo`.

Read:

1. `FUN_14038b2f0` bullet-vs-chr resolver.
2. Bullet owner derivation into `AttackDamageInfo.attacker` and `field_0x6d & 2` behavior in `FUN_1404434f0`.
3. Bullet creation APIs already used by player attacks, and whether owner can be local `MainPlayer` while origin/visual follows possessed NPC.

Stop condition: yes/no on "player-owned bullet can send vanilla PvP damage to remote player victim."

### Runtime proof, only after static recipe

One bounded two-client Seamless probe:

- Instrument sender around slot21/lower send and receiver `packet15_receive` buffer.
- Use one target remote player, one small known damage value, no death first.
- Required oracle: receiver buffer dealer handle = sender's player handle, victim handle = receiver player handle, damage/stamina match, receiver HP changes by exactly expected amount, and reaction/stagger observed via non-visual telemetry if available.
- Then test death separately. Guard/status/throw are separate probes, not part of the first proof.

## Follow-up subagent prompts for parent orchestration

### Prompt A: slot21 / `Packet15` call-contract audit

Goal: produce a field-level call recipe or a rejection for using the vanilla `CSPlayerDamageModule` slot21 sender to send player-attributed damage from an NPC possession trigger.

Context/evidence:

- Start from `crates/er-npc-possess/src/possess/netdamage.rs` and this handoff.
- Relevant 1.16.2 functions: `FUN_14044ce40`, `FUN_140443c40`, `FUN_1404434f0`, `FUN_140ca6280`, `FUN_14050e4a0`, `SendHitPacket 0x140cc5540/0x140c9e580`.
- Ghidra :8765 is named 1.16.2; :8767 is 1.17.0 structure. Installed runtime 1.17.1 adds `+0x70` only for `.text` RVAs at/above `0xafefe9`.

Success criteria:

- Identify which object `slot21` must be called on, with which `ChrIns`/damage module identities.
- Explain how dealer/victim handles enter the received `0x140` bytes despite lower sender size `0x120`.
- List required `AttackDamageInfo` fields for HP, stamina, reaction, guard/status if statically visible.
- State role limits: host->guest, invader->host, invader->phantom.

Validation:

- Ghidra decompile/disassembly excerpts with addresses.
- No game launch.

### Prompt B: player-owned bullet feasibility audit

Goal: decide whether a player-owned bullet can be used as the vanilla sender path while the possessed NPC supplies only visuals/collision timing.

Context/evidence:

- Existing evidence says bullet-vs-chr resolver ends in the same damage module slot7 with bullet owner as attacker.
- Need verify whether owner can be local `MainPlayer` and whether that reaches mode 4 for remote player victim.
- Relevant functions: `FUN_14038b2f0`, `FUN_1404434f0`, `FUN_140443c40`, bullet param lookup in `FUN_1404434f0` when `packet.field_0x6d & 2`.

Success criteria:

- Yes/no on player-owned bullet sending accepted PvP damage.
- Required bullet owner identity and victim identity.
- Differences from synthetic slot21 call.
- Risks for status/guard/throws.

Validation:

- Static RE only; no game launch.

### Prompt C: ERSC/role transport limits

Goal: determine whether Seamless preserves direct victim `PlayerNetworkSession::SendHitPacket` semantics for invader -> host and invader -> other phantoms.

Context/evidence:

- Vanilla `SendHitPacket` uses target `PlayerNetworkSession` steam id and `P2PSendToSteamId(type 0x14, size 0x120)`.
- Previous memory says ERSC wraps native Steam session but packet-kind handling is inside Themida/VM and not settled statically.

Success criteria:

- Static ERSC evidence if possible; otherwise define smallest live 2-peer and 3-peer proof.
- Explicitly separate host->guest, invader->host, invader->phantom.

Validation:

- Static first. If static cannot settle, produce runtime oracle plan only.

## Commands run

```bash
python3 scripts/ghidra/mcp_query.py getContext --port 8765
python3 scripts/ghidra/mcp_query.py getContext --port 8767
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"14044ce40"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"14050e4a0"}' --port 8765
python3 scripts/ghidra/mcp_query.py searchFunctionsByName --params '{"query":"SendHit"}' --port 8765
python3 scripts/ghidra/mcp_query.py searchFunctionsByName --params '{"query":"Packet"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140443c40"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"1404434f0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140c9e580"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140cc5540"}' --port 8765
python3 scripts/ghidra/mcp_query.py searchFunctionsByName --params '{"query":"Packet21"}' --port 8765
python3 scripts/ghidra/mcp_query.py searchFunctionsByName --params '{"query":"Packet28"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140c99ba0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140c9dab0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"14043fee0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140446bb0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140445060"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"14044caf0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140445f00"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140c99c40"}' --port 8765
python3 scripts/ghidra/mcp_query.py disassembleFunction --params '{"address":"140445060"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140c9f370"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140c9f3d0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140ca1600"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"14050aaf0"}' --port 8765
python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"140ca6280"}' --port 8765
uv run --with capstone python3 scripts/map-rvas-1162-to-1170.py 0x14044ce40 0x140443c40 0x1404434f0 0x140c9e580 0x140cc5540 0x140c9f370 0x140ca1600 0x14050aaf0 0x140c9f3d0 0x140446bb0 0x14043fee0 0x140c9dab0 0x140ca6280 0x140c99ba0 0x140c99c40 0x140c991d0
python3 scripts/ghidra/mcp_query.py getFunctionByAddress --params '{"address":"14044d3a0"}' --port 8767
python3 scripts/ghidra/mcp_query.py getFunctionByAddress --params '{"address":"140c9fcc0"}' --port 8767
$HOME/.local/bin/bd recall possession-pvp-damage-blocked-by-net-routing-matrix-2026-09-02
$HOME/.local/bin/bd recall possession-incoming-pvp-damage-unexplained-receive-oracle-2026-09-02
$HOME/.local/bin/bd recall enemy-chrsync-ownership-is-arbitrated-packet22-2026-09-01
$HOME/.local/bin/bd recall possess-net-sync-verdict-buddy-summon-is-the-seam-2026-09-02
```

## Residual risks

- `FUN_14044ce40` direct invocation from a mod may require exact stack/ABI/object lifetime not yet proven.
- The receive-side `0x140` vs lower-send `0x120` gap is not resolved; do not forge raw packet bytes until that metadata path is known.
- Seamless transport role behavior for invader -> non-host phantom is not settled by vanilla static RE.
- `AttackDamageInfo` fields for guard, status, throws, and super armor are not fully mapped here.
- No live proof has been run; all feasibility claims are static.
