# Spawning a human (c0000) NPC with its own AI

Static only (1.16.2 named dump on :8765, byte-checked against `eldenring-deobf-1.17.1.bin`). Nothing
here has been run.

## Verdict

er-npc-possess can spawn an AI-driven human through the entry it already calls. The only required
change is to the request: `charaInitParam >= 0` and model `c0000`. The game's own human spirit-ash
summon builds exactly that request.

## Mechanism

`ChrInsFactory::CreateCharacter` (1.16.2 `0x140403a60`, 1.17.1 `0x140403dd0`) branches on
`ChrSpawnRequest+0x48` (`cmp dword [r8+0x48], ebx` at 1.17.1 `0x140403e1b`):

| `charaInitParam` | Branch |
|---|---|
| `< 0` | `HeapAlloc(0x5e0)` + `EnemyIns` (what the spawner does today) |
| `>= 0` | `FUN_1402563d0` (1.17.1 `0x1402563a0`) takes a free "NPC Player" `PlayerGameData` from `GameDataMan`'s 40-entry session list, applies the `CharaInitParam` row (stats, equipment, spells), takes the `CharacterType` from that row, then `HeapAlloc(0x740)` + `PlayerIns` |

The player branch also copies `npcParamId` (`+0x40`) and `npcThinkId` (`+0x44`) into `ChrInitData`, so
the human runs whatever `NpcThinkParam` row we name, including a private `battleGoalID`. Unlike the
creature branch, it reads `position` and `orientation` (`PlayerChrBaseData::Init`) and the
`MsbResCap*` at `+0xc0`.

Retail precedent: `SummonBuddyManager::CreateSummonChr` (1.16.2 `0x1404ba980`, 1.17.1 `0x1404baea0`)
writes `charaInitParam` and `npcThinkId` from its arguments, `+0xc0 = 0`, and for
`charaInitParam >= 0` takes the model name from chr id 0, which is `c0000`. A null `MsbResCap` is
therefore a path the game itself takes.

Map-placed humans such as Moongrum do not go through `ChrSpawnRequest`. `FUN_140494440`
(`OpenFieldChrSet`, map load) creates them through Arxan-thunked MSB factories. It reapplies
`componentContainer->data->charaInitParamId` to `PlayerGameData` on respawn when `IsPlayerIns`, which
shows that they are `PlayerIns` too.

## Code change

`crates/er-npc-possess/src/spawn/request.rs`:

- Add `chara_init_param: i32` to `SpawnSpec` and write it at `req::CHARA_INIT_PARAM` in place of
  `CREATURE_CHARA_INIT_PARAM`; `-1` keeps today's behaviour.
- When it is `>= 0`, force `chr_id = 0`, so the model is `c0000`.
- Position and yaw now take effect: set them to where the human should appear.

Entry, slot band, despawn (`RemoveChrIns` 1.17.1 `0x14050b340`) and the `ChrCtrl+0x3b0` override
are unchanged; the override applies to `PlayerIns` and `EnemyIns` alike.

## Unverified, check at runtime

- **Readiness:** the `AssetsResident` gate reads an `EneDat` FLVER cap. A `PlayerIns` draws through
  equipment parts and FaceGen, so that gate may never pass for `c0000`. Skip it for the player
  branch.
- **`PlayerGameData` slots:** whether despawn returns the slot to the 40-entry pool is not traced.
  Repeated spawns could exhaust it; when they do, `CreateCharacter` returns null.
- **Seamless Co-op:** the "NPC Player" `PlayerGameData` list is session-shared state that Seamless
  also uses.
