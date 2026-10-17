# Cheat Engine tables and diagnostics

This directory contains Cheat Engine tables and helpers for Elden Ring Seamless/offline workflows.

## Bundled CJK font override

Files:

- `bundled_cjk_font_override.CT` -- redirects Elden Ring's Scaleform menu font registration to bundled Simplified or Traditional Chinese `font.gfx` assets. It does not require loose-file unpacking, external fonts, or bundled game assets in this repo.

Use exactly one override entry at a time. Disable the current override before switching between Simplified and Traditional Chinese. This is intended for Seamless/offline setups where Cheat Engine use is allowed; do not use it with official EAC matchmaking.

## Duel rules referee

Files:

- `er-duel-rules.CT` -- the competitive duel ruleset referee, the same checks as `er_duel_rules.dll` and `scripts/frida/er-duel-rules.js`. Every rule break, by anyone in the session, is appended to `er-duel-rules-breaks.txt` beside `eldenring.exe` and listed, newest first, in a "Duel rule breaks" window that pops up over Cheat Engine. The local player is listed under their Steam name. `er-duel-rules-ce.log` in the same folder records players as they are judged and a status line every ten seconds; it is the file to send back. Generated -- edit the Lua, not the table.
- `er-duel-rules.lua` -- the referee's source. `python3 scripts/er-duel-rules-gen.py --write` fills its data block from the installed game and rebuilds the table around it; `lua scripts/test-er-duel-rules-ce.lua` runs its rules with no game.
- `er-duel-rules-ce.py`, `er-duel-rules-autotest.lua`, `er-duel-rules-ui-autotest.lua` -- start Windows Cheat Engine 7.5 beside a running game on Linux/Proton (inside the game's pressure-vessel namespaces, as `scripts/er-frida-up.py` does for frida-server); `--autotest` also ticks the entry with no hands, and `--ui-test` drives the ban editor and Rules window, logs the result to `~/.cache/er-ce/ui-test/ui-autotest.log` and closes.

On Windows: start Elden Ring with EAC off, open the table, tick `Duel rules referee (Elden Ring 1.17.1)`. The entry attaches to `eldenring.exe` itself if Cheat Engine has not. It refuses any other game build, and it places one hook (after `SpecialEffect::Apply`'s inner apply, `eldenring.exe+4FB79E`) so an effect that lasts a single frame is still seen; unticking restores the original bytes. Closing Cheat Engine without unticking leaves the hook running harmlessly, and the next tick adopts it.

### Your own rules

The breaks window's **Rules...** button opens the Rules window:

- **The ruleset** tab lists all 49 built-in rules with a box each; untick one to stop enforcing it. The rune level, blue flasks per round and the shield guard cap are numbers you can change.
- **Your bans** tab holds bans of your own. **New ban...** opens the editor: type part of any name -- a weapon, a weapon class, an affinity, armour, a talisman, a crystal tear, a skill, a spell or an item -- and add it. Each line on the right is one condition, and a ban breaks when every line holds for one player at once; **Add to the selected condition (or)** puts a second name on a line as an alternative. A weapon line also says where the weapon must be (any slot, either hand, one hand, two-handed, powerstanced, or the weapon a skill came from) and whether it must carry a weapon buff, and a class or affinity added to a weapon line narrows that same weapon ("a Blood katana held two-handed"). **Add other condition...** takes raw effect, projectile or animation ids, a rune level comparison (`!= 125`) or an attribute one (`Arcane >= 45`).
- **Save and apply** writes `er-duel-rules.json` beside `eldenring.exe` and the referee uses it at once. A ban on what someone wears waits for an opponent to share the world, a ban on something done (a spell, an item, an effect, a buffed weapon) fires whenever it is seen, and a ban on a skill or an animation fires on every use.

The Frida agent reads the same file, and `scripts/er-duel-rules-bans.py` edits it from a terminal (`--help` has the examples), so one file sets the rules for every referee. Each re-reads it within two seconds of a change; with no file, the ruleset applies as written.

Proven live on 2026-10-16 under Proton with Cheat Engine 7.5: the entry attached and ticked, read the Steam name through `steam_api64`, judged the character, caught a buff transfer and a crystal tear heal, and a second Cheat Engine adopted the hook the first left behind. Later the same day the ban editor and Rules window passed `--ui-test` (21 checks, every button pressed), and a rules file written by `er-duel-rules-bans.py` -- one rule off, one ban on effect 600, Godrick's Great Rune -- was picked up within two seconds by both the Frida agent and the ticked table in the running game, each firing the ban once; editing and then deleting the file was followed live.

## Voice chat with every player

Files:

- `er-voice-chat.CT` -- one entry, `Voice chat with every player (Elden Ring 1.17.1)`, with six readouts under it. Every player who should hear and be heard ticks it, in the same session (Seamless Co-op or vanilla).
- `er-voice-chat-ce.py`, `er-voice-chat-autotest.lua` -- start Cheat Engine 7.5 in the game's container on Linux, reusing `er-duel-rules-ce.py`; `--autotest` ticks, unticks and reticks the entry and logs the patch bytes and readouts each time to `er-voice-chat-autotest.log` beside `eldenring.exe`.

Seamless Co-op does not use the game's voice path. Seamless 2.0.1 reads the microphone through `ISteamUser` itself (`ersc+0x92d40`: `GetAvailableVoice`, `GetVoice`, `DecompressVoice`), sends it over its own Steam connection, and plays what arrives through Wwise audio inputs it creates (`ersc+0xa58e0`, 48 kHz). Both halves first test bit 8 of a flags byte (`+0x281` of a Seamless heap object) that ships as `0xd3`, bit 8 clear, so Seamless's voice is off. The entry turns the two jumps that skip voice into nops (`ersc.dll+92D81` capture, `ersc.dll+A5921` playback setup); those are the only two places ersc tests that bit.

For vanilla sessions the entry also turns on the game's own voice chat: it sets two global bytes `CSVoiceChatManager` requires (`eldenring.exe+3D7E89C`, `+3D7E89D`, both 0 in offline launches) and the Voice Chat option (`GameSettings+0x14`), and patches the `RoleParam.voiceChatGroup` test that mutes players on another team (`eldenring.exe+CBBF25` `xor bl,1` -> `xor bl,bl`, `+9F9441` `jz` -> nops). Asserts refuse any game build but 1.17.1 and any Seamless build but 2.0.1; unticking restores every byte and value.

Measured live on 2026-10-16, Seamless 2.0.1 with the world open (Frida, `scripts/frida/seamless-voice-gate.js`): with the original byte Seamless's capture routine ran every frame and read the mic 0 times; with the bit set it read 6810 bytes; and in a fresh game with only the table ticked it read 5249 bytes over 21 `GetVoice` calls and kept running. A crash at `ersc+0xa5f32` happened once, in an earlier session where the bit had been switched on and off under Frida before ticking; it did not recur in the clean run. Whether a second player hears it has not been tested.

## Locked-target weapon-level diagnostic

Files:

- `locked_target_weapon_level_match_diagnostic.CT` -- readonly Cheat Engine table that resolves the local player, locked-on target, active `ChrAsm`, and prints the target's highest equipped weapon level plus planned local weapon-slot changes. It deliberately vetoes mutation.
- `launch-ce-locked-target-diagnostic.sh` -- launches Cheat Engine in the same Proton/Wine prefix as an already-running `eldenring.exe` and opens the CT.

Usage:

```bash
scripts/cheat-engine/launch-ce-locked-target-diagnostic.sh
```

Manual final steps after Cheat Engine opens:

1. Attach Cheat Engine to `eldenring.exe`.
2. Enable `ER locked-target weapon level match - DIAGNOSTIC ONLY`.

The CT shows the computed values in a popup and in Cheat Engine's Lua output log.
