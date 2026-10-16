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
- `er-duel-rules-ce.py`, `er-duel-rules-autotest.lua` -- start Windows Cheat Engine 7.5 beside a running game on Linux/Proton (inside the game's pressure-vessel namespaces, as `scripts/er-frida-up.py` does for frida-server); `--autotest` also ticks the entry with no hands.

On Windows: start Elden Ring with EAC off, open the table, tick `Duel rules referee (Elden Ring 1.17.1)`. The entry attaches to `eldenring.exe` itself if Cheat Engine has not. It refuses any other game build, and it places one hook (after `SpecialEffect::Apply`'s inner apply, `eldenring.exe+4FB79E`) so an effect that lasts a single frame is still seen; unticking restores the original bytes. Closing Cheat Engine without unticking leaves the hook running harmlessly, and the next tick adopts it.

Proven live on 2026-10-16 under Proton with Cheat Engine 7.5: the entry attached and ticked, read the Steam name through `steam_api64`, judged the character, caught a buff transfer and a crystal tear heal, and a second Cheat Engine adopted the hook the first left behind.

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
